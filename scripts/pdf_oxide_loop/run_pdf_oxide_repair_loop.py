#!/usr/bin/env python3
"""Conservative starter loop for pdf_oxide extraction repair.

This script is intentionally narrower than the earlier aspirational design.
It assumes:

1. pdf_oxide performs deterministic pass-1 extraction.
2. A shaped extraction function (currently extract_for_pdflab.extract_pdf) emits
   PDF Lab-style blocks.
3. The loop is trying to reduce obvious misses and bad shaping decisions.
4. /code-runner is an exception handler that edits a small allowlist.

What this version does:
- Builds a frozen deterministic reference from pdf_oxide primitives.
- Runs the shaped extractor.
- Scans for a conservative set of obvious defects.
- Optionally renders suspicious pages for visual review.
- Invokes /code-runner with a bounded prompt.
- Gates acceptance against the last ACCEPTED baseline only.

What this version does NOT do yet:
- VLM adjudication in the hot path.
- Repo-specific viewer integration beyond an optional render command template.
- Aggressive semantic reasoning about sections/requirements.
- Automatic git commit / revert orchestration.

The intent is to give a project agent a clean, grounded starting point.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import logging
import os
import shlex
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

# ----------------------------------------------------------------------------
# Repo wiring
# ----------------------------------------------------------------------------

DEFAULT_REPO = Path(__file__).resolve().parents[0]
DEFAULT_WORKDIR = Path("/tmp/pdf_oxide_repair_loop")
DEFAULT_CODE_RUNNER = Path("/home/graham/.claude/skills/code-runner/run.sh")


# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

logger = logging.getLogger("pdf_oxide_repair_loop")


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


# ----------------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class LoopPaths:
    pdf: Path
    workdir: Path

    @property
    def key(self) -> str:
        digest = hashlib.sha1(str(self.pdf.resolve()).encode("utf-8")).hexdigest()[:10]
        return f"{self.pdf.stem}_{digest}"

    @property
    def extraction_json(self) -> Path:
        return self.workdir / f"{self.key}.extraction.json"

    @property
    def reference_json(self) -> Path:
        return self.workdir / f"{self.key}.reference.json"

    @property
    def defects_json(self) -> Path:
        return self.workdir / f"{self.key}.defects.json"

    @property
    def rounds_json(self) -> Path:
        return self.workdir / f"{self.key}.rounds.json"

    @property
    def review_json(self) -> Path:
        return self.workdir / f"{self.key}.review_queue.json"

    @property
    def review_dir(self) -> Path:
        return self.workdir / f"{self.key}.review"

    @property
    def logs_dir(self) -> Path:
        return self.workdir / f"{self.key}.logs"

    def ensure(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.review_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass
class Defect:
    category: str
    page: int
    severity: str
    detail: str
    source: str
    bbox: list[float] | None = None
    blocking: bool = True
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "page": self.page,
            "severity": self.severity,
            "detail": self.detail,
            "source": self.source,
            "bbox": self.bbox,
            "blocking": self.blocking,
            "extra": self.extra or {},
        }


@dataclasses.dataclass
class GateResult:
    accepted: bool
    current_total: int
    prior_total: int
    regressions: list[str]
    hard_regressions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ----------------------------------------------------------------------------
# Utility helpers
# ----------------------------------------------------------------------------


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


HARD_CATEGORIES = {
    "EMPTY_BLOCK",
    "DUPLICATE_BLOCK",
    "PHANTOM_BLOCK",
    "MISSING_TEXT_REGION",
    "MISSING_TABLE_PAGE",
}

CATEGORY_WEIGHTS = {
    "EMPTY_BLOCK": 4,
    "DUPLICATE_BLOCK": 5,
    "PHANTOM_BLOCK": 6,
    "MISSING_TEXT_REGION": 9,
    "MISSING_TABLE_PAGE": 10,
    "MISSING_HEADER_CANDIDATE": 5,
    "WRONG_TYPE_TABLE_AS_TEXT": 6,
}


def weighted_total(summary: dict[str, int]) -> int:
    total = 0
    for category, count in summary.items():
        weight = CATEGORY_WEIGHTS.get(category, 1)
        total += weight * count
    return total


def normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def block_bbox_pixels(block: dict[str, Any], page_width: float, page_height: float) -> list[float]:
    bbox = block.get("bbox") or [0.0, 0.0, 0.0, 0.0]
    if max(bbox) <= 1.5:
        return [
            bbox[0] * page_width,
            bbox[1] * page_height,
            bbox[2] * page_width,
            bbox[3] * page_height,
        ]
    return [float(x) for x in bbox]


def bbox_intersection_ratio(a: list[float], b: list[float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    if ix <= 0.0 or iy <= 0.0:
        return 0.0
    inter = ix * iy
    a_area = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    b_area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    smaller = min(a_area, b_area)
    if smaller <= 0.0:
        return 0.0
    return inter / smaller


def bbox_union(boxes: Iterable[list[float]]) -> list[float] | None:
    boxes = list(boxes)
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


# ----------------------------------------------------------------------------
# pdf_oxide reference snapshot
# ----------------------------------------------------------------------------


def _safe_getattr(obj: Any, name: str) -> Any:
    return getattr(obj, name, None)


def _call_or_attr(obj: Any, name: str, *args: Any, default: Any = None) -> Any:
    """Resolve `obj.name` or `obj.name(*args)` — pyo3 wrappers expose methods, not attrs."""
    value = getattr(obj, name, default)
    if callable(value):
        try:
            return value(*args)
        except Exception:
            return default
    return value


# pyo3 wrappers (TextSpan, Word, Table, Image, Path) expose attributes, not dict keys.
# Normalize each item to a dict so downstream code can use `.get(...)` uniformly.
_ATTR_ALIASES: dict[str, tuple[str, ...]] = {
    "bbox": ("bbox",),
    "text": ("text", "word", "token", "value"),
    "font_size": ("font_size", "size"),
    "bold": ("bold", "is_bold"),
    "italic": ("italic", "is_italic"),
    "font_name": ("font_name",),
    "origin": ("origin",),
    "data": ("data", "rows", "cells"),
}


def _as_mapping(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    out: dict[str, Any] = {}
    for canonical, aliases in _ATTR_ALIASES.items():
        for alias in aliases:
            val = getattr(obj, alias, None)
            if val is not None:
                out[canonical] = val
                break
    return out


def _call_list(oxide_doc: Any, name: str, page_num: int) -> list[dict[str, Any]]:
    items = _call_or_attr(oxide_doc, name, page_num) or []
    try:
        iterator = list(items)
    except TypeError:
        return []
    return [m for m in (_as_mapping(item) for item in iterator) if m]


def _extract_page_dimensions(oxide_doc: Any, page_num: int) -> tuple[float, float]:
    dims = _call_or_attr(oxide_doc, "page_dimensions", page_num)
    if isinstance(dims, dict):
        return float(dims.get("width", 1.0)), float(dims.get("height", 1.0))
    if isinstance(dims, (list, tuple)) and len(dims) >= 2:
        return float(dims[0]), float(dims[1])
    return 1.0, 1.0


def _extract_page_count(oxide_doc: Any) -> int:
    value = _call_or_attr(oxide_doc, "page_count")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _extract_words(oxide_doc: Any, page_num: int) -> list[dict[str, Any]]:
    return _call_list(oxide_doc, "extract_words", page_num)


def _extract_spans(oxide_doc: Any, page_num: int) -> list[dict[str, Any]]:
    return _call_list(oxide_doc, "extract_spans", page_num)


def _extract_tables(oxide_doc: Any, page_num: int) -> list[dict[str, Any]]:
    return _call_list(oxide_doc, "extract_tables", page_num)


def _extract_images(oxide_doc: Any, page_num: int) -> list[dict[str, Any]]:
    return _call_list(oxide_doc, "extract_images", page_num)


def _extract_paths(oxide_doc: Any, page_num: int) -> list[dict[str, Any]]:
    return _call_list(oxide_doc, "extract_paths", page_num)


def _bbox_from_item(item: dict[str, Any]) -> list[float] | None:
    bbox = item.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        return [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
    return None


def _text_from_wordish(item: dict[str, Any]) -> str:
    for key in ("text", "word", "token", "value"):
        value = item.get(key)
        if isinstance(value, str):
            return value
    return ""


def _font_size_from_span(item: dict[str, Any]) -> float | None:
    value = item.get("font_size") or item.get("size")
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def build_reference_snapshot(pdf_path: Path, output_path: Path, max_pages: int | None = None) -> dict[str, Any]:
    """Freeze pass-1 deterministic evidence for the current PDF.

    This snapshot should be treated as stable reference data for a given loop run.
    """
    import pdf_oxide  # imported lazily so the script can still be inspected without the repo env

    oxide_doc = pdf_oxide.open(str(pdf_path))

    survey = None
    survey_fn = _safe_getattr(pdf_oxide, "survey_document")
    if callable(survey_fn):
        try:
            survey = survey_fn(str(pdf_path))
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.warning("survey_document failed: %s", exc)

    toc_entries: list[dict[str, Any]] = []
    get_toc = _safe_getattr(oxide_doc, "get_toc")
    if callable(get_toc):
        try:
            toc = get_toc() or {}
            toc_entries = [e for e in toc.get("entries", []) if isinstance(e, dict)]
        except Exception as exc:  # pragma: no cover
            logger.warning("get_toc failed: %s", exc)

    page_count = _extract_page_count(oxide_doc)
    if max_pages is not None:
        page_count = min(page_count, max_pages)

    survey_table_pages: set[int] = set()
    if isinstance(survey, dict):
        raw_pages = survey.get("table_pages") or []
        survey_table_pages = {int(p) for p in raw_pages if isinstance(p, int)}

    pages: list[dict[str, Any]] = []
    for page_num in range(page_count):
        page_width, page_height = _extract_page_dimensions(oxide_doc, page_num)
        words = _extract_words(oxide_doc, page_num)
        spans = _extract_spans(oxide_doc, page_num)
        tables = _extract_tables(oxide_doc, page_num)
        images = _extract_images(oxide_doc, page_num)
        paths = _extract_paths(oxide_doc, page_num)

        normalized_words = []
        for word in words:
            bbox = _bbox_from_item(word)
            text = normalize_text(_text_from_wordish(word))
            if not bbox or not text:
                continue
            normalized_words.append({"text": text, "bbox": bbox})

        normalized_spans = []
        for span in spans:
            bbox = _bbox_from_item(span)
            text = normalize_text(_text_from_wordish(span))
            size = _font_size_from_span(span)
            if not bbox or not text:
                continue
            normalized_spans.append({"text": text, "bbox": bbox, "font_size": size, "bold": bool(span.get("bold"))})

        normalized_tables = []
        for table in tables:
            bbox = _bbox_from_item(table)
            if bbox:
                normalized_tables.append({"bbox": bbox, "rows": len(table.get("data") or [])})

        normalized_images = []
        for image in images:
            bbox = _bbox_from_item(image)
            if bbox:
                normalized_images.append({"bbox": bbox})

        normalized_paths = []
        for path in paths:
            bbox = _bbox_from_item(path)
            if bbox:
                normalized_paths.append({"bbox": bbox})

        page_text = " ".join(word["text"] for word in normalized_words)
        pages.append(
            {
                "page": page_num,
                "width": page_width,
                "height": page_height,
                "word_count": len(normalized_words),
                "span_count": len(normalized_spans),
                "words": normalized_words,
                "spans": normalized_spans,
                "tables": normalized_tables,
                "images": normalized_images,
                "paths": normalized_paths,
                "page_text": page_text,
                "table_expected": bool(normalized_tables) or page_num in survey_table_pages,
            }
        )

    snapshot = {
        "pdf": str(pdf_path),
        "created_at": now_iso(),
        "page_count": page_count,
        "toc_entries": toc_entries,
        "survey_table_pages": sorted(survey_table_pages),
        "pages": pages,
    }
    output_path.write_text(json.dumps(snapshot, indent=2))
    return snapshot


# ----------------------------------------------------------------------------
# Conservative defect scanner
# ----------------------------------------------------------------------------


def _covered_by_any(bbox: list[float], candidates: Iterable[list[float]], threshold: float = 0.65) -> bool:
    for candidate in candidates:
        if bbox_intersection_ratio(bbox, candidate) >= threshold:
            return True
    return False


def _derive_header_candidates(page_ref: dict[str, Any]) -> list[dict[str, Any]]:
    """Find visually obvious header-like spans.

    This is intentionally conservative: top-of-page or clearly larger-than-median spans.
    """
    spans = page_ref.get("spans") or []
    if not spans:
        return []

    sizes = [s["font_size"] for s in spans if s.get("font_size") is not None]
    if not sizes:
        return []

    sorted_sizes = sorted(sizes)
    median_size = sorted_sizes[len(sorted_sizes) // 2]
    page_height = float(page_ref.get("height") or 1.0)

    candidates = []
    for span in spans:
        font_size = span.get("font_size")
        bbox = span.get("bbox")
        if font_size is None or not bbox:
            continue
        text = normalize_text(span.get("text", ""))
        if len(text) < 4:
            continue
        near_top = bbox[1] <= page_height * 0.20
        large = font_size >= median_size * 1.30
        if near_top and large:
            candidates.append(span)
    return candidates


def scan_defects(reference: dict[str, Any], extraction: dict[str, Any]) -> dict[str, Any]:
    blocks_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for block in extraction.get("blocks", []):
        page = int(block.get("page", 0))
        blocks_by_page[page].append(block)

    defects: list[Defect] = []
    page_scores: Counter[int] = Counter()

    for page_ref in reference.get("pages", []):
        page_num = int(page_ref["page"])
        page_width = float(page_ref["width"])
        page_height = float(page_ref["height"])
        extracted = blocks_by_page.get(page_num, [])

        all_block_boxes = [block_bbox_pixels(block, page_width, page_height) for block in extracted]
        content_blocks = [
            block for block in extracted
            if block.get("blockType") not in {"boilerplate", "page_number"}
        ]
        content_boxes = [block_bbox_pixels(block, page_width, page_height) for block in content_blocks]
        table_boxes = [
            block_bbox_pixels(block, page_width, page_height)
            for block in extracted
            if block.get("blockType") == "table"
        ]
        header_boxes = [
            block_bbox_pixels(block, page_width, page_height)
            for block in extracted
            if block.get("blockType") == "header"
        ]

        # 1) Empty / trivial blocks.
        for block in extracted:
            text = normalize_text(block.get("text") or "")
            if len(text) < 2:
                defects.append(
                    Defect(
                        category="EMPTY_BLOCK",
                        page=page_num,
                        severity="medium",
                        detail=f"{block.get('blockType', '?')} block has trivial text",
                        source="deterministic",
                        bbox=block_bbox_pixels(block, page_width, page_height),
                    )
                )

        # 2) Duplicate blocks.
        seen: set[tuple[str, str]] = set()
        for block in extracted:
            text = normalize_text(block.get("text") or "")
            if len(text) < 20:
                continue
            key = (str(block.get("blockType", "?")), text[:100])
            if key in seen:
                defects.append(
                    Defect(
                        category="DUPLICATE_BLOCK",
                        page=page_num,
                        severity="high",
                        detail=f"Duplicate {key[0]} block: {key[1][:80]}",
                        source="deterministic",
                        bbox=block_bbox_pixels(block, page_width, page_height),
                    )
                )
            else:
                seen.add(key)

        # 3) Phantom blocks: no overlap with any pass-1 evidence.
        primitive_boxes = []
        primitive_boxes.extend([item["bbox"] for item in page_ref.get("words", [])])
        primitive_boxes.extend([item["bbox"] for item in page_ref.get("tables", [])])
        primitive_boxes.extend([item["bbox"] for item in page_ref.get("images", [])])
        primitive_boxes.extend([item["bbox"] for item in page_ref.get("paths", [])])
        for block in content_blocks:
            bbox = block_bbox_pixels(block, page_width, page_height)
            if not primitive_boxes:
                continue
            if not _covered_by_any(bbox, primitive_boxes, threshold=0.10):
                defects.append(
                    Defect(
                        category="PHANTOM_BLOCK",
                        page=page_num,
                        severity="high",
                        detail=f"{block.get('blockType', '?')} block has no meaningful overlap with pass-1 evidence",
                        source="deterministic",
                        bbox=bbox,
                    )
                )

        # 4) Missing text regions: group uncovered words into coarse visual regions.
        uncovered_word_boxes: list[list[float]] = []
        uncovered_texts: list[str] = []
        for word in page_ref.get("words", []):
            if len(word["text"]) < 3:
                continue
            if not _covered_by_any(word["bbox"], content_boxes, threshold=0.70):
                uncovered_word_boxes.append(word["bbox"])
                uncovered_texts.append(word["text"])

        if len(uncovered_word_boxes) >= 8:
            defect_bbox = bbox_union(uncovered_word_boxes[:60])
            preview = " ".join(uncovered_texts[:20])[:180]
            defects.append(
                Defect(
                    category="MISSING_TEXT_REGION",
                    page=page_num,
                    severity="high",
                    detail=f"Obvious uncovered text region: {preview}",
                    source="deterministic",
                    bbox=defect_bbox,
                )
            )

        # 5) Missing table page.
        table_expected = bool(page_ref.get("table_expected"))
        if table_expected and not table_boxes:
            defect_bbox = bbox_union([item["bbox"] for item in page_ref.get("tables", [])])
            defects.append(
                Defect(
                    category="MISSING_TABLE_PAGE",
                    page=page_num,
                    severity="high",
                    detail="Pass-1 evidence says the page contains a table, but no table block was emitted",
                    source="deterministic",
                    bbox=defect_bbox,
                )
            )

        # 6) Missing obvious header candidate.
        header_candidates = _derive_header_candidates(page_ref)
        if header_candidates:
            missing_candidates = [c for c in header_candidates if not _covered_by_any(c["bbox"], header_boxes, threshold=0.70)]
            if missing_candidates:
                defect_bbox = bbox_union([c["bbox"] for c in missing_candidates])
                preview = " | ".join(c["text"] for c in missing_candidates[:3])[:180]
                defects.append(
                    Defect(
                        category="MISSING_HEADER_CANDIDATE",
                        page=page_num,
                        severity="medium",
                        detail=f"Likely header-like span not covered by a header block: {preview}",
                        source="deterministic",
                        bbox=defect_bbox,
                        blocking=False,
                    )
                )

        # 7) Obvious type error: a text block heavily overlaps a pass-1 table.
        table_primitive_boxes = [table["bbox"] for table in page_ref.get("tables", [])]
        for block in content_blocks:
            if block.get("blockType") == "table":
                continue
            bbox = block_bbox_pixels(block, page_width, page_height)
            if _covered_by_any(bbox, table_primitive_boxes, threshold=0.75):
                defects.append(
                    Defect(
                        category="WRONG_TYPE_TABLE_AS_TEXT",
                        page=page_num,
                        severity="medium",
                        detail=f"{block.get('blockType', '?')} block substantially overlaps a pass-1 table region",
                        source="deterministic",
                        bbox=bbox,
                        blocking=False,
                    )
                )
                break

        page_scores[page_num] = sum(1 for defect in defects if defect.page == page_num)

    summary_counter = Counter(defect.category for defect in defects)
    blocking_summary_counter = Counter(defect.category for defect in defects if defect.blocking)

    return {
        "summary": dict(summary_counter),
        "blocking_summary": dict(blocking_summary_counter),
        "weighted_total": weighted_total(dict(summary_counter)),
        "blocking_weighted_total": weighted_total(dict(blocking_summary_counter)),
        "defects": [defect.to_dict() for defect in defects],
        "page_scores": dict(page_scores),
        "total_pages": reference.get("page_count", 0),
    }


# ----------------------------------------------------------------------------
# Review queue / page rendering
# ----------------------------------------------------------------------------


def build_review_queue(defects_report: dict[str, Any], render_template: str | None, paths: LoopPaths) -> dict[str, Any]:
    """Create a page-level queue for human or VLM second-pass review.

    render_template is optional and should be a format string containing:
      {pdf}  absolute pdf path
      {page} zero-based page number
      {out}  output image path

    Example:
      python scripts/render_pdf_page.py --pdf {pdf} --page {page} --out {out}
    """
    defect_pages = sorted(
        int(page)
        for page, score in defects_report.get("page_scores", {}).items()
        if int(score) > 0
    )
    review_items = []
    for page_num in defect_pages:
        image_path = paths.review_dir / f"page_{page_num:04d}.png"
        render_error = None
        if render_template:
            cmd = render_template.format(pdf=str(paths.pdf), page=page_num, out=str(image_path))
            try:
                subprocess.run(shlex.split(cmd), check=True, capture_output=True, text=True)
            except Exception as exc:  # pragma: no cover - environment dependent
                render_error = str(exc)
        review_items.append(
            {
                "page": page_num,
                "image": str(image_path) if image_path.exists() else None,
                "render_error": render_error,
                "defects": [
                    d for d in defects_report.get("defects", [])
                    if int(d.get("page", -1)) == page_num
                ],
            }
        )

    review_queue = {
        "created_at": now_iso(),
        "pdf": str(paths.pdf),
        "items": review_items,
    }
    paths.review_json.write_text(json.dumps(review_queue, indent=2))
    return review_queue


# ----------------------------------------------------------------------------
# State management
# ----------------------------------------------------------------------------


def load_round_state(rounds_json: Path, pdf: Path) -> dict[str, Any]:
    if rounds_json.exists():
        return json.loads(rounds_json.read_text())
    return {
        "pdf": str(pdf),
        "created_at": now_iso(),
        "baseline": None,
        "rounds": [],
    }


def save_round_state(rounds_json: Path, state: dict[str, Any]) -> None:
    rounds_json.write_text(json.dumps(state, indent=2))


def last_accepted_summary(state: dict[str, Any]) -> dict[str, int] | None:
    for entry in reversed(state.get("rounds", [])):
        if entry.get("status") == "accepted":
            return {k: int(v) for k, v in (entry.get("summary") or {}).items()}
    baseline = state.get("baseline")
    if isinstance(baseline, dict):
        return {k: int(v) for k, v in baseline.items()}
    return None


# ----------------------------------------------------------------------------
# Extraction / code-runner steps
# ----------------------------------------------------------------------------


def run_shaped_extraction(pdf_path: Path, output_path: Path, max_pages: int | None = None) -> dict[str, Any]:
    from pdf_oxide.extract_for_pdflab import extract_pdf  # imported lazily

    result = extract_pdf(str(pdf_path), output_path=str(output_path), max_pages=max_pages)
    if not isinstance(result, dict):
        raise RuntimeError("extract_pdf did not return a dict")
    return result


FIX_PROMPT = """You are patching pdf_oxide extraction calibration code.

Goal:
Reduce obvious extraction misses and shaping errors reported in {defects_json}.

Constraints:
- Prefer mechanism fixes over PDF-specific special cases.
- Keep edits as small as possible.
- Do not add document-title hardcoding unless the evidence packet explicitly proves
  the logic is generalizable.
- The last ACCEPTED baseline is the real target, not the last attempted round.
- Improve the triggering PDF without regressing the benchmark/fixture checks.

Editable scope:
{allowlist}

Primary signals to fix first:
- MISSING_TEXT_REGION
- MISSING_TABLE_PAGE
- DUPLICATE_BLOCK
- EMPTY_BLOCK
- PHANTOM_BLOCK

Available context:
- defects report: {defects_json}
- review queue: {review_json}
- rounds state:  {rounds_json}

Definition of done:
- rerun the deterministic extraction loop
- weighted blocking defects improve vs the last accepted baseline
- no new hard-regression categories
"""


def run_code_runner(
    *,
    repo_root: Path,
    code_runner_path: Path,
    allowlist: list[str],
    paths: LoopPaths,
    dod_command: str,
    backend: str,
) -> subprocess.CompletedProcess[str]:
    if not code_runner_path.exists():
        raise FileNotFoundError(f"code-runner not found: {code_runner_path}")

    prompt = FIX_PROMPT.format(
        defects_json=str(paths.defects_json),
        review_json=str(paths.review_json),
        rounds_json=str(paths.rounds_json),
        allowlist=", ".join(allowlist),
    )

    cmd = [
        str(code_runner_path),
        "--prompt", prompt,
        "--backend", backend,
        "--dod-command", dod_command,
    ]
    for item in allowlist:
        cmd.extend(["--allowlist", item])
    for item in (paths.defects_json, paths.review_json, paths.rounds_json, paths.reference_json):
        cmd.extend(["--read-context", str(item)])

    logger.info("Running code-runner")
    return subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)


# ----------------------------------------------------------------------------
# Gating
# ----------------------------------------------------------------------------


def gate_candidate(prior_summary: dict[str, int] | None, current_summary: dict[str, int]) -> GateResult:
    prior_summary = prior_summary or {}
    current_total = weighted_total(current_summary)
    prior_total = weighted_total(prior_summary)

    regressions: list[str] = []
    hard_regressions: list[str] = []
    for category, current_count in current_summary.items():
        prior_count = int(prior_summary.get(category, 0))
        if current_count > prior_count:
            regressions.append(category)
            if category in HARD_CATEGORIES:
                hard_regressions.append(category)

    # Accept if the weighted score improved and there are no hard regressions.
    accepted = (current_total < prior_total) and not hard_regressions
    return GateResult(
        accepted=accepted,
        current_total=current_total,
        prior_total=prior_total,
        regressions=sorted(regressions),
        hard_regressions=sorted(hard_regressions),
    )


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------


def ensure_reference(paths: LoopPaths, max_pages: int | None) -> dict[str, Any]:
    if paths.reference_json.exists():
        logger.info("Using existing reference snapshot: %s", paths.reference_json)
        return json.loads(paths.reference_json.read_text())
    logger.info("Building frozen pass-1 reference snapshot")
    return build_reference_snapshot(paths.pdf, paths.reference_json, max_pages=max_pages)


def baseline_if_missing(state: dict[str, Any], defects_report: dict[str, Any], rounds_json: Path) -> dict[str, Any]:
    if state.get("baseline") is None:
        state["baseline"] = defects_report.get("summary", {})
        save_round_state(rounds_json, state)
    return state


def run_diagnose_cycle(paths: LoopPaths, max_pages: int | None, render_template: str | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    reference = ensure_reference(paths, max_pages=max_pages)
    extraction = run_shaped_extraction(paths.pdf, paths.extraction_json, max_pages=max_pages)
    defects_report = scan_defects(reference, extraction)
    paths.defects_json.write_text(json.dumps(defects_report, indent=2))
    review_queue = build_review_queue(defects_report, render_template, paths)
    return reference, defects_report, review_queue


def do_dod_check(paths: LoopPaths, max_pages: int | None, render_template: str | None) -> int:
    state = load_round_state(paths.rounds_json, paths.pdf)
    _, defects_report, _ = run_diagnose_cycle(paths, max_pages=max_pages, render_template=render_template)
    prior_summary = last_accepted_summary(state)
    gate = gate_candidate(prior_summary, defects_report.get("summary", {}))
    print(json.dumps({
        "accepted": gate.accepted,
        "prior_total": gate.prior_total,
        "current_total": gate.current_total,
        "regressions": gate.regressions,
        "hard_regressions": gate.hard_regressions,
    }, indent=2))
    return 0 if gate.accepted else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--stall-limit", type=int, default=2)
    parser.add_argument("--max-rounds", type=int, default=10)
    parser.add_argument("--code-runner", type=Path, default=DEFAULT_CODE_RUNNER)
    parser.add_argument("--backend", default="codex")
    parser.add_argument(
        "--allowlist",
        action="append",
        default=["python/pdf_oxide/extract_for_pdflab.py", "python/pdf_oxide/extraction_scanner.py"],
        help="Repeatable allowlist entry for /code-runner",
    )
    parser.add_argument(
        "--render-page-cmd",
        help=(
            "Optional render command template for suspicious pages. Use {pdf}, {page}, and {out}. "
            "Example: 'python scripts/render_pdf_page.py --pdf {pdf} --page {page} --out {out}'"
        ),
    )
    parser.add_argument("--dod-check", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(args.verbose)

    pdf_path = args.pdf.resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    paths = LoopPaths(pdf=pdf_path, workdir=args.workdir.resolve())
    paths.ensure()

    if args.dod_check:
        return do_dod_check(paths, max_pages=args.max_pages, render_template=args.render_page_cmd)

    state = load_round_state(paths.rounds_json, pdf_path)

    # Preflight / baseline.
    logger.info("Preflight diagnose cycle")
    _, defects_report, _ = run_diagnose_cycle(paths, max_pages=args.max_pages, render_template=args.render_page_cmd)
    state = baseline_if_missing(state, defects_report, paths.rounds_json)

    last_total = weighted_total(last_accepted_summary(state) or {})
    stall_count = 0

    for _ in range(args.max_rounds):
        state = load_round_state(paths.rounds_json, pdf_path)
        round_num = len(state.get("rounds", [])) + 1

        logger.info("Starting round %s", round_num)
        prior_summary = last_accepted_summary(state)

        dod_command = (
            f"python {shlex.quote(str(Path(__file__).resolve()))} "
            f"--pdf {shlex.quote(str(pdf_path))} "
            f"--repo-root {shlex.quote(str(args.repo_root.resolve()))} "
            f"--workdir {shlex.quote(str(paths.workdir))} "
            f"--backend {shlex.quote(args.backend)} "
            f"--code-runner {shlex.quote(str(args.code_runner.resolve()))} "
            f"--dod-check"
        )
        if args.max_pages is not None:
            dod_command += f" --max-pages {args.max_pages}"
        if args.render_page_cmd:
            dod_command += f" --render-page-cmd {shlex.quote(args.render_page_cmd)}"

        t0 = time.time()
        code_runner_result = run_code_runner(
            repo_root=args.repo_root.resolve(),
            code_runner_path=args.code_runner.resolve(),
            allowlist=args.allowlist,
            paths=paths,
            dod_command=dod_command,
            backend=args.backend,
        )
        elapsed = round(time.time() - t0, 2)

        _, defects_report, _ = run_diagnose_cycle(paths, max_pages=args.max_pages, render_template=args.render_page_cmd)
        gate = gate_candidate(prior_summary, defects_report.get("summary", {}))

        status = "accepted" if (code_runner_result.returncode == 0 and gate.accepted) else "rejected"
        entry = {
            "round": round_num,
            "timestamp": now_iso(),
            "status": status,
            "elapsed_seconds": elapsed,
            "summary": defects_report.get("summary", {}),
            "weighted_total": defects_report.get("weighted_total", 0),
            "gate": gate.to_dict(),
            "code_runner": {
                "returncode": code_runner_result.returncode,
                "stdout_tail": code_runner_result.stdout[-2000:],
                "stderr_tail": code_runner_result.stderr[-1000:],
            },
        }
        state.setdefault("rounds", []).append(entry)
        save_round_state(paths.rounds_json, state)

        current_total = weighted_total(defects_report.get("summary", {}))
        logger.info(
            "Round %s status=%s current_total=%s prior_total=%s regressions=%s",
            round_num,
            status,
            current_total,
            gate.prior_total,
            gate.regressions,
        )

        if status == "accepted":
            if current_total == 0:
                logger.info("Halting: zero weighted defects")
                return 0
            if current_total >= last_total:
                stall_count += 1
            else:
                stall_count = 0
            last_total = current_total
        else:
            stall_count += 1

        if stall_count >= args.stall_limit:
            logger.info("Halting: stall limit reached (%s)", args.stall_limit)
            return 0

    logger.info("Halting: max rounds reached (%s)", args.max_rounds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
