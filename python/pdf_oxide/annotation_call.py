"""Contract-v1 annotation-call reporting for ambiguous extraction output."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping, Optional


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
_TOP_LEVEL_FIELDS = {
    "schema",
    "pdf_sha256",
    "engine_commit",
    "accuracy_estimate",
    "items",
}


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
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


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

    reason = item["reason"]
    if reason == "low_confidence":
        required = {"bbox", "confidence", "current_type", "text_excerpt"}
        missing = required - item.keys()
        if missing:
            raise ValueError(f"low_confidence annotation-call item missing {sorted(missing)}")
        confidence = item["confidence"]
        if not _is_finite_number(confidence) or not 0.0 <= confidence < threshold:
            raise ValueError(
                "low_confidence item confidence must be finite, in [0, threshold)"
            )
        bbox = item["bbox"]
        if (
            not isinstance(bbox, (list, tuple))
            or len(bbox) != 4
            or not all(_is_finite_number(coordinate) for coordinate in bbox)
        ):
            raise ValueError(
                "low_confidence item bbox must be four finite numeric coordinates"
            )
        if not isinstance(item["current_type"], str):
            raise ValueError("low_confidence item current_type must be a string")
        if not isinstance(item["text_excerpt"], str):
            raise ValueError("low_confidence item text_excerpt must be a string")
    elif reason == "char_parity_deficit":
        missing_chars = item.get("missing_chars")
        if (
            not isinstance(missing_chars, int)
            or isinstance(missing_chars, bool)
            or missing_chars < 0
        ):
            raise ValueError(
                "char_parity_deficit item missing_chars must be a non-negative integer"
            )


def validate_annotation_call(
    payload: Mapping[str, Any],
    *,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> None:
    """Raise ``ValueError`` unless *payload* matches contract v1."""
    fields = set(payload)
    if fields != _TOP_LEVEL_FIELDS:
        raise ValueError(
            "annotation-call fields must be exactly "
            f"{sorted(_TOP_LEVEL_FIELDS)}; got {sorted(fields)}"
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
        _validate_item(item, threshold=threshold)
        items.append(item)

    for extra_item in extra_items:
        item = dict(extra_item)
        _validate_item(item, threshold=threshold)
        items.append(item)

    block_count = len(result.blocks)
    payload = {
        "schema": ANNOTATION_CALL_SCHEMA,
        "pdf_sha256": _sha256_file(Path(result.source_pdf)),
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
) -> Path:
    """Write a deterministic v1 report and return its path."""
    payload = build_annotation_call(
        result,
        threshold=threshold,
        extra_items=extra_items,
        engine_commit=engine_commit,
    )
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return output_path
