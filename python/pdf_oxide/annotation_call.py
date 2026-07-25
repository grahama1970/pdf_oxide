"""Contract-v1 annotation-call reporting for ambiguous extraction output."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping, Optional

from .pdf_oxide import VERSION


if TYPE_CHECKING:
    from .pipeline_types import PipelineResult


ANNOTATION_CALL_SCHEMA = "pdf_oxide.annotation_call.v1"
DEFAULT_CONFIDENCE_THRESHOLD = 0.6
CLOSED_REASONS = frozenset(
    {
        "low_confidence",
        "char_parity_deficit",
        "unadjudicated_residual",
        "reviewer_flagged",
    }
)
_REQUIRED_TOP_LEVEL_FIELDS = {
    "schema",
    "pdf_sha256",
    "engine_commit",
    "accuracy_estimate",
    "items",
}
_OPTIONAL_TOP_LEVEL_FIELDS = {"engine_name", "engine_version"}
_TEXT_EXCERPT_LIMIT = 20_000


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_pdf_oxide_repository(path: Path) -> bool:
    return (
        (path / ".git").exists()
        and (path / "Cargo.toml").is_file()
        and (path / "python" / "pdf_oxide").is_dir()
    )


def _engine_commit() -> str:
    override = os.getenv("PDF_OXIDE_ENGINE_COMMIT")
    if override:
        return override

    candidates = []
    seen = set()
    for start in (Path(__file__).resolve().parent, Path.cwd()):
        for candidate in (start, *start.parents):
            if candidate not in seen and _is_pdf_oxide_repository(candidate):
                candidates.append(candidate)
                seen.add(candidate)

    for candidate in candidates:
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=candidate,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            continue
    return "unknown"


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _normalized_accounting_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _page_engine_text(result: PipelineResult, page: int) -> str:
    parts = [str(block.get("text") or "") for block in result.blocks if block.get("page") == page]
    parts.extend(
        " ".join(str(cell) for row in table.get("data", []) for cell in row)
        for table in result.tables
        if table.get("page") == page
    )
    for figure in result.figures:
        if figure.get("page") != page:
            continue
        parts.extend(str(block.get("text") or "") for block in (figure.get("content_blocks") or []))
    return "\n".join(part for part in parts if part)


def _pdftotext_page(pdf_path: Path, page: int) -> tuple[Optional[str], Optional[str]]:
    try:
        completed = subprocess.run(
            [
                "pdftotext",
                "-f",
                str(page + 1),
                "-l",
                str(page + 1),
                str(pdf_path),
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        return None, f"pdftotext_unavailable:{error}"
    if completed.returncode != 0:
        detail = completed.stderr.strip().replace("\n", " ")[:240]
        return None, f"pdftotext_failed:{completed.returncode}:{detail}"
    return completed.stdout, None


def _missing_text_in_oracle_order(oracle_text: str, engine_text: str) -> str:
    missing = Counter(_normalized_accounting_text(oracle_text)) - Counter(
        _normalized_accounting_text(engine_text)
    )
    ordered = []
    for character in _normalized_accounting_text(oracle_text):
        if missing[character] > 0:
            ordered.append(character)
            missing[character] -= 1
    return "".join(ordered)


def _oracle_excerpt(oracle_text: str, missing_text: str) -> str:
    if not oracle_text:
        return ""
    positions = [
        index for index, character in enumerate(oracle_text) if character in set(missing_text)
    ]
    if not positions:
        return oracle_text[:_TEXT_EXCERPT_LIMIT]
    start = max(0, positions[0] - 500)
    end = min(len(oracle_text), positions[-1] + 501)
    if end - start > _TEXT_EXCERPT_LIMIT:
        end = start + _TEXT_EXCERPT_LIMIT
    return oracle_text[start:end]


def _char_parity_bbox_union(
    bboxes: list[list[float]],
) -> list[float] | None:
    """Return the smallest PDF-space xywh bbox containing all valid input boxes."""
    valid = [
        bbox
        for bbox in bboxes
        if len(bbox) == 4 and all(_is_finite_number(value) for value in bbox)
    ]
    if not valid:
        return None
    x0 = min(bbox[0] for bbox in valid)
    y0 = min(bbox[1] for bbox in valid)
    x1 = max(bbox[0] + bbox[2] for bbox in valid)
    y1 = max(bbox[1] + bbox[3] for bbox in valid)
    return [x0, y0, x1 - x0, y1 - y0]


def _compact_match_text(value: Any) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", str(value or "")).casefold()
        if not character.isspace()
    )


def _table_text(table: Mapping[str, Any]) -> str:
    return "\n".join(
        "\t".join(str(cell or "") for cell in row) for row in table.get("data", [])
    )


def _top_left_xyxy_to_pdf_xywh(
    bbox: Any,
    page_height: float,
) -> list[float] | None:
    if (
        not isinstance(bbox, (list, tuple))
        or len(bbox) != 4
        or not all(_is_finite_number(value) for value in bbox)
    ):
        return None
    x0, y0, x1, y1 = bbox
    if x1 <= x0 or y1 <= y0 or y1 > page_height:
        return None
    return [x0, page_height - y1, x1 - x0, y1 - y0]


def _xywh_bbox(bbox: Any) -> list[float] | None:
    if (
        not isinstance(bbox, (list, tuple))
        or len(bbox) != 4
        or not all(_is_finite_number(value) for value in bbox)
        or bbox[2] <= 0
        or bbox[3] <= 0
    ):
        return None
    return list(bbox)


def _page_localization_candidates(
    result: PipelineResult,
    page: int,
) -> list[Dict[str, Any]]:
    candidates: list[Dict[str, Any]] = []
    for block in result.blocks:
        if block.get("page") != page:
            continue
        bbox = _xywh_bbox(block.get("bbox"))
        text = str(block.get("text") or "")
        if bbox is not None and text.strip():
            candidates.append({"bbox": bbox, "text": text})

    page_tables = [table for table in result.tables if table.get("page") == page]
    if page_tables:
        _, page_height = _page_dimensions(result.source_pdf, page)
        # Table extraction uses top-left xyxy coordinates. annotation_call.v1
        # uses bottom-left PDF-space xywh rectangles, matching text blocks.
        for table in page_tables:
            bbox = _top_left_xyxy_to_pdf_xywh(table.get("bbox"), page_height)
            text = _table_text(table)
            if bbox is not None and text.strip():
                candidates.append({"bbox": bbox, "text": text})

    for figure in result.figures:
        if figure.get("page") != page:
            continue
        for content_block in figure.get("content_blocks") or []:
            bbox = _xywh_bbox(content_block.get("bbox"))
            text = str(content_block.get("text") or "")
            if bbox is not None and text.strip():
                candidates.append({"bbox": bbox, "text": text})
    return candidates


def _candidate_match(
    oracle_text: str,
    candidate: Mapping[str, Any],
    missing_positions: set[int],
) -> Dict[str, Any] | None:
    oracle = _compact_match_text(oracle_text)
    candidate_text = _compact_match_text(candidate.get("text"))
    if not oracle or not candidate_text:
        return None
    matches = [
        match
        for match in SequenceMatcher(
            None,
            oracle,
            candidate_text,
            autojunk=False,
        ).get_matching_blocks()
        if match.size >= 4
    ]
    matched_characters = sum(match.size for match in matches)
    if matched_characters < 12:
        return None
    start = min(match.a for match in matches)
    end = max(match.a + match.size for match in matches)
    covered_missing_positions = {
        position for position in missing_positions if start <= position < end
    }
    return {
        **candidate,
        "covered_missing_positions": covered_missing_positions,
        "matched_characters": matched_characters,
        "coverage": matched_characters / len(candidate_text),
    }


def _page_dimensions(source_pdf: str, page: int) -> tuple[float, float]:
    from .pdf_oxide import PdfDocument

    return PdfDocument(source_pdf).page_dimensions(page)


def _page_bbox(source_pdf: str, page: int) -> list[float]:
    width, height = _page_dimensions(source_pdf, page)
    return [0.0, 0.0, width, height]


def _localize_char_parity_item(
    item: Dict[str, Any],
    result: PipelineResult,
    oracle_text: str,
    missing_text: str,
) -> None:
    oracle = _compact_match_text(oracle_text)
    outstanding = Counter(_compact_match_text(missing_text))
    missing_positions = set()
    for index, character in enumerate(oracle):
        if outstanding[character] > 0:
            missing_positions.add(index)
            outstanding[character] -= 1

    matches = [
        match
        for candidate in _page_localization_candidates(result, item["page"])
        if (match := _candidate_match(oracle_text, candidate, missing_positions)) is not None
    ]
    matches.sort(
        key=lambda match: (
            len(match["covered_missing_positions"]),
            match["matched_characters"],
            match["coverage"],
        ),
        reverse=True,
    )

    selected = []
    covered: set[int] = set()
    for match in matches:
        new_positions = match["covered_missing_positions"] - covered
        if missing_positions and not new_positions:
            continue
        selected.append(match)
        covered.update(match["covered_missing_positions"])
        if len(selected) == 3 or covered == missing_positions:
            break
    if not selected and matches:
        selected = [matches[0]]

    bbox = _char_parity_bbox_union([match["bbox"] for match in selected])
    if bbox is not None:
        item["bbox"] = bbox
        item["localization"] = "block" if len(selected) == 1 else "blocks"
        return

    item["bbox"] = _page_bbox(result.source_pdf, item["page"])
    item["localization"] = "page"


def _enrich_char_parity_item(
    item: Dict[str, Any],
    result: PipelineResult,
    oracle_cache: Dict[int, tuple[Optional[str], Optional[str]]],
) -> None:
    page = item["page"]
    engine_text = _page_engine_text(result, page)
    item.setdefault("text_excerpt", engine_text[:_TEXT_EXCERPT_LIMIT])
    if page not in oracle_cache:
        oracle_cache[page] = _pdftotext_page(Path(result.source_pdf), page)
    oracle_text, oracle_error = oracle_cache[page]
    if oracle_text is None:
        item.setdefault("oracle_excerpt", "")
        item.setdefault(
            "missing_text_derivation_error",
            oracle_error or "pdftotext_returned_no_text",
        )
        _localize_char_parity_item(item, result, "", "")
        return

    missing_text = _missing_text_in_oracle_order(oracle_text, engine_text)
    item.setdefault("oracle_excerpt", _oracle_excerpt(oracle_text, missing_text))
    expected_count = item["missing_chars"]
    if len(missing_text) == expected_count:
        item.setdefault("missing_text", missing_text)
    else:
        item.setdefault(
            "missing_text_derivation_error",
            (
                "char_accounting_count_mismatch:"
                f"declared={expected_count}:derived={len(missing_text)}"
            ),
        )
    _localize_char_parity_item(item, result, oracle_text, missing_text)


def _validate_item(
    item: Mapping[str, Any],
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> None:
    if item.get("reason") not in CLOSED_REASONS:
        raise ValueError(f"annotation-call reason must be in closed set {sorted(CLOSED_REASONS)}")
    if not isinstance(item.get("page"), int) or isinstance(item.get("page"), bool):
        raise ValueError("annotation-call item page must be an integer")
    if item.get("kind") not in {"block", "region", "page"}:
        raise ValueError("annotation-call item kind must be block, region, or page")
    if "page_image_refs" in item:
        refs = item["page_image_refs"]
        if (
            not isinstance(refs, list)
            or not refs
            or not all(isinstance(ref, str) and ref for ref in refs)
            or len(refs) != len(set(refs))
        ):
            raise ValueError(
                "annotation-call item page_image_refs must be a non-empty list of unique filenames"
            )
        hashes = item.get("page_image_sha256")
        if (
            not isinstance(hashes, Mapping)
            or set(hashes) != set(refs)
            or not all(
                isinstance(value, str)
                and len(value) == 64
                and all(character in "0123456789abcdef" for character in value)
                for value in hashes.values()
            )
        ):
            raise ValueError(
                "annotation-call item page_image_sha256 must map every ref to a lowercase SHA-256"
            )

    reason = item["reason"]
    if reason == "low_confidence":
        required = {"bbox", "confidence", "current_type", "text_excerpt"}
        missing = required - item.keys()
        if missing:
            raise ValueError(f"low_confidence annotation-call item missing {sorted(missing)}")
        confidence = item["confidence"]
        if not _is_finite_number(confidence) or not 0.0 <= confidence < threshold:
            raise ValueError("low_confidence item confidence must be finite, in [0, threshold)")
        bbox = item["bbox"]
        if (
            not isinstance(bbox, (list, tuple))
            or len(bbox) != 4
            or not all(_is_finite_number(coordinate) for coordinate in bbox)
        ):
            raise ValueError("low_confidence item bbox must be four finite numeric coordinates")
        if not isinstance(item["current_type"], str):
            raise ValueError("low_confidence item current_type must be a string")
        if not isinstance(item["text_excerpt"], str):
            raise ValueError("low_confidence item text_excerpt must be a string")
    elif reason == "char_parity_deficit":
        if item.get("localization") not in {"block", "blocks", "page"}:
            raise ValueError(
                "char_parity_deficit item localization must be block, blocks, or page"
            )
        missing_chars = item.get("missing_chars")
        if (
            not isinstance(missing_chars, int)
            or isinstance(missing_chars, bool)
            or missing_chars < 0
        ):
            raise ValueError(
                "char_parity_deficit item missing_chars must be a non-negative integer"
            )
        for field in ("text_excerpt", "oracle_excerpt", "missing_text"):
            if field in item and not isinstance(item[field], str):
                raise ValueError(f"char_parity_deficit item {field} must be a string")
        if "missing_text_derivation_error" in item and (
            not isinstance(item["missing_text_derivation_error"], str)
            or not item["missing_text_derivation_error"]
        ):
            raise ValueError(
                "char_parity_deficit item missing_text_derivation_error must be a non-empty string"
            )
        if "missing_text" in item and len(item["missing_text"]) != missing_chars:
            raise ValueError(
                "char_parity_deficit item missing_text length must match missing_chars"
            )
        bbox = item.get("bbox")
        if (
            not isinstance(bbox, (list, tuple))
            or len(bbox) != 4
            or not all(_is_finite_number(coordinate) for coordinate in bbox)
            or bbox[2] <= 0
            or bbox[3] <= 0
        ):
            raise ValueError(
                "char_parity_deficit item bbox must be a positive four-coordinate rectangle"
            )


def validate_annotation_call(
    payload: Mapping[str, Any],
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> None:
    """Raise ``ValueError`` unless *payload* matches contract v1."""
    fields = set(payload)
    missing_fields = _REQUIRED_TOP_LEVEL_FIELDS - fields
    unexpected_fields = fields - _REQUIRED_TOP_LEVEL_FIELDS - _OPTIONAL_TOP_LEVEL_FIELDS
    if missing_fields or unexpected_fields:
        raise ValueError(
            "annotation-call fields must contain the required fields and only "
            f"known optional fields; missing={sorted(missing_fields)}; "
            f"unexpected={sorted(unexpected_fields)}"
        )
    if payload.get("schema") != ANNOTATION_CALL_SCHEMA:
        raise ValueError(f"annotation-call schema must be {ANNOTATION_CALL_SCHEMA!r}")
    pdf_sha256 = payload.get("pdf_sha256")
    if (
        not isinstance(pdf_sha256, str)
        or len(pdf_sha256) != 64
        or any(character not in "0123456789abcdef" for character in pdf_sha256)
    ):
        raise ValueError("annotation-call pdf_sha256 must be a lowercase SHA-256")
    if not isinstance(payload.get("engine_commit"), str) or not payload["engine_commit"]:
        raise ValueError("annotation-call engine_commit must be a non-empty string")
    if "engine_name" in payload and payload["engine_name"] != "pdf-oxide":
        raise ValueError("annotation-call engine_name must be 'pdf-oxide'")
    if "engine_version" in payload and (
        not isinstance(payload["engine_version"], str) or not payload["engine_version"]
    ):
        raise ValueError("annotation-call engine_version must be a non-empty string")

    accuracy = payload.get("accuracy_estimate")
    if not isinstance(accuracy, Mapping):
        raise ValueError("annotation-call accuracy_estimate must be an object")
    if set(accuracy) != {"basis", "value"}:
        raise ValueError("annotation-call accuracy_estimate fields must be basis and value")
    if accuracy.get("basis") != "confidence_threshold":
        raise ValueError("annotation-call accuracy_estimate basis must be confidence_threshold")
    value = accuracy.get("value")
    if not _is_finite_number(value) or not 0.0 <= value <= 1.0:
        raise ValueError("annotation-call accuracy_estimate value must be in [0, 1]")

    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("annotation-call items must be an array")
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("annotation-call items must be objects")
        _validate_item(item, threshold=threshold)


def build_annotation_call(
    result: PipelineResult,
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    extra_items: Iterable[Mapping[str, Any]] = (),
    engine_commit: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a v1 report without modifying the extraction result."""
    if not _is_finite_number(threshold):
        raise ValueError("confidence threshold must be numeric")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("confidence threshold must be in [0, 1]")

    from .pipeline_page_images import page_image_refs_for_page

    items = []
    at_or_above = 0
    for block in result.blocks:
        confidence = block.get("confidence")
        if not _is_finite_number(confidence):
            raise ValueError(
                f"pipeline block {block.get('id', '<unknown>')} has no numeric confidence"
            )
        if confidence >= threshold:
            at_or_above += 1
            continue
        item = {
            "page": block.get("page"),
            "kind": "block",
            "bbox": block.get("bbox"),
            "reason": "low_confidence",
            "confidence": confidence,
            "current_type": block.get("type"),
            "text_excerpt": (block.get("text") or "")[:200],
        }
        page_image_refs = block.get("page_image_refs") or (
            page_image_refs_for_page(result, block.get("page", 0))
        )
        if page_image_refs:
            item["page_image_refs"] = list(page_image_refs)
            item["page_image_sha256"] = {
                ref: block["page_image_sha256"][ref] for ref in page_image_refs
            }
        _validate_item(item, threshold=threshold)
        items.append(item)

    oracle_cache: Dict[int, tuple[Optional[str], Optional[str]]] = {}
    for extra_item in extra_items:
        item = dict(extra_item)
        if item.get("reason") == "char_parity_deficit":
            _enrich_char_parity_item(item, result, oracle_cache)
        page_image_refs = item.get("page_image_refs") or (
            page_image_refs_for_page(result, item.get("page", 0))
        )
        if page_image_refs:
            item["page_image_refs"] = list(page_image_refs)
            manifest = result.metadata.get("page_images", {})
            hashes = {
                image["filename"]: image["byte_sha256"] for image in manifest.get("images", [])
            }
            item["page_image_sha256"] = {ref: hashes[ref] for ref in page_image_refs}
        _validate_item(item, threshold=threshold)
        items.append(item)

    block_count = len(result.blocks)
    payload = {
        "schema": ANNOTATION_CALL_SCHEMA,
        "pdf_sha256": _sha256_file(Path(result.source_pdf)),
        "engine_name": "pdf-oxide",
        "engine_version": VERSION,
        "engine_commit": engine_commit or _engine_commit(),
        "accuracy_estimate": {
            "basis": "confidence_threshold",
            "value": at_or_above / block_count if block_count else 0.0,
        },
        "items": items,
    }
    validate_annotation_call(payload, threshold=threshold)
    return payload


def write_annotation_call(
    result: PipelineResult,
    output_path: Path,
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    extra_items: Iterable[Mapping[str, Any]] = (),
    engine_commit: Optional[str] = None,
    page_images_dir: Optional[Path] = None,
) -> Path:
    """Write a deterministic v1 report and return its path."""
    payload = build_annotation_call(
        result,
        threshold=threshold,
        extra_items=extra_items,
        engine_commit=engine_commit,
    )
    if page_images_dir is not None:
        from .pipeline_page_images import validate_page_image_ref_list

        for index, item in enumerate(payload["items"]):
            validate_page_image_ref_list(
                item.get("page_image_refs"),
                page_images_dir,
                owner=f"annotation-call item {index}",
                sha256_by_ref=item.get("page_image_sha256"),
            )
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return output_path
