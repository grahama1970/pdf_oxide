#!/usr/bin/env python3
"""Deterministic repair loop for pdf_oxide shaping/classification fixes.

This harness assumes:

1. pdf_oxide performs deterministic pass-1 extraction.
2. A shaped extraction function (currently `extract_for_pdflab.extract_pdf`)
   emits PDF Lab-style blocks from that pass-1 evidence.
3. `/code-runner` is an exception handler that edits a small allowlist when the
   shaped output regresses on a benchmark PDF.

What this script does:
- Builds and caches a frozen deterministic reference from pdf_oxide primitives.
- Runs the shaped extractor and scans for conservative structural defects.
- Builds a per-page review queue and a page-local focus packet for each round.
- Generates focus-page screenshots and bbox-overlay images for `/code-runner`.
- Optionally builds a frozen VLM visual reference (`--enable-visual`) and adds
  visual findings as secondary evidence.
- Gates acceptance against the last accepted blocking baseline plus any
  configured benchmark PDFs.

What this script does not do by default:
- Use VLM findings as the primary accept/reject gate.
- Perform repo/viewer-specific UI integration beyond page PNG generation.
- Auto-commit accepted rounds to git.

The intent is to keep the loop deterministic by default while still giving the
repair agent page-local visual evidence and exact extracted geometry.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

# ----------------------------------------------------------------------------
# Repo wiring
# ----------------------------------------------------------------------------

DEFAULT_REPO = Path(__file__).resolve().parents[2]
DEFAULT_WORKDIR = Path("/tmp/pdf_oxide_repair_loop")
DEFAULT_CODE_RUNNER = Path("/home/graham/.claude/skills/code-runner/run.sh")
REFERENCE_SNAPSHOT_VERSION = 2
METRICS_VERSION = 2
FOCUS_RENDER_DPI = 150


# ----------------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------------

def configure_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> <level>{level: <8}</level> {message}",
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

    @property
    def visual_reference_json(self) -> Path:
        return self.workdir / f"{self.key}.visual_reference.json"

    @property
    def visual_reference_meta_json(self) -> Path:
        return self.workdir / f"{self.key}.visual_reference.meta.json"

    @property
    def visual_pages_dir(self) -> Path:
        return self.workdir / f"{self.key}.visual_pages"

    def ensure(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.review_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.visual_pages_dir.mkdir(parents=True, exist_ok=True)


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

# Visual categories are surfaced from the frozen VLM reference. They ride in the
# review queue as evidence but do NOT gate acceptance until explicitly promoted.
# Promotion is manual only — edit BLOCKING_VISUAL_CATEGORIES here or pass
# --promote-visual-category on the CLI. Do not auto-promote.
VISUAL_CATEGORIES = {
    "MISSED_VISIBLE_REGION",
    "BAD_BOUNDARY",
    "OVER_MERGE",
    "OVER_SPLIT",
}

BLOCKING_VISUAL_CATEGORIES: set[str] = set()

CATEGORY_WEIGHTS = {
    # Primitive (commit-blocking)
    "EMPTY_BLOCK": 4,
    "DUPLICATE_BLOCK": 5,
    "PHANTOM_BLOCK": 6,
    "MISSING_TEXT_REGION": 9,
    "MISSING_TABLE_PAGE": 10,
    "MISSING_HEADER_CANDIDATE": 5,
    "WRONG_TYPE_TABLE_AS_TEXT": 6,
    # Visual (evidence-only unless promoted)
    "MISSED_VISIBLE_REGION": 7,
    "BAD_BOUNDARY": 3,
    "OVER_MERGE": 4,
    "OVER_SPLIT": 4,
}


BLOCK_TYPE_COLORS = {
    "table": "#3B82F6",
    "header": "#F97316",
    "text": "#A3A3A3",
    "figure": "#22C55E",
    "equation": "#1D4ED8",
    "caption": "#F59E0B",
    "boilerplate": "#64748B",
    "page_number": "#EC4899",
}


def weighted_total(summary: dict[str, int]) -> int:
    total = 0
    for category, count in summary.items():
        weight = CATEGORY_WEIGHTS.get(category, 1)
        total += weight * count
    return total


def primitive_summary_only(summary: dict[str, int], promoted: set[str]) -> dict[str, int]:
    """Return only categories that the gate considers blocking.

    Visual categories are excluded unless they appear in `promoted`.
    """
    return {
        cat: count
        for cat, count in summary.items()
        if cat not in VISUAL_CATEGORIES or cat in promoted
    }


def visual_summary_only(summary: dict[str, int]) -> dict[str, int]:
    """Return only the visual-category counts from a summary."""
    return {cat: count for cat, count in summary.items() if cat in VISUAL_CATEGORIES}


def build_metrics_views(
    summary_full: dict[str, int],
    *,
    blocking_summary: dict[str, int] | None = None,
    promoted_visual: set[str] | None = None,
) -> dict[str, Any]:
    """Build explicit audit-friendly metric views from a full summary.

    Separates:
      - everything the scanner saw (summary_full)
      - what the gate acts on (summary_blocking — primitives + promoted visuals)
      - what was visual evidence only (summary_visual)
      - weighted totals for each view
    """
    promoted = promoted_visual or BLOCKING_VISUAL_CATEGORIES
    if blocking_summary is None:
        summary_blocking = primitive_summary_only(summary_full, promoted)
    else:
        summary_blocking = {
            cat: int(count)
            for cat, count in blocking_summary.items()
            if cat not in VISUAL_CATEGORIES or cat in promoted
        }
        for cat in promoted:
            if cat in summary_full:
                summary_blocking[cat] = int(summary_full[cat])
    summary_visual = visual_summary_only(summary_full)
    return {
        "summary_full": dict(summary_full),
        "summary_blocking": dict(summary_blocking),
        "summary_visual": dict(summary_visual),
        "totals": {
            "weighted_full": weighted_total(summary_full),
            "weighted_blocking": weighted_total(summary_blocking),
            "weighted_visual": weighted_total(summary_visual),
        },
    }


def effective_hard_categories(promoted: set[str]) -> set[str]:
    return HARD_CATEGORIES | (VISUAL_CATEGORIES & promoted)


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


def quantize_bbox(bbox: list[float], digits: int = 1) -> tuple[float, float, float, float]:
    """Round bbox coordinates for stable duplicate keys.

    Sub-pixel coordinate drift between rounds can falsely differentiate
    identical blocks. Quantize to 1 decimal by default; bump to 0 if
    noise persists.
    """
    return (
        round(float(bbox[0]), digits),
        round(float(bbox[1]), digits),
        round(float(bbox[2]), digits),
        round(float(bbox[3]), digits),
    )


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


def _bbox_from_item(item: dict[str, Any], page_height: float) -> list[float] | None:
    bbox = item.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        x0 = float(bbox[0])
        y0_pdf = float(bbox[1])
        width = float(bbox[2])
        height = float(bbox[3])
        # pdf_oxide primitive extractors expose Rect as (x, y, width, height),
        # in PDF bottom-left coordinates. The defect scanner expects
        # [x0, y0, x1, y1] in top-left page coordinates.
        x1 = x0 + width
        y1_pdf = y0_pdf + height
        return [x0, page_height - y1_pdf, x1, page_height - y0_pdf]
    return None


def _normalize_reference_snapshot(reference: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Normalize cached reference primitive boxes to [x0, y0, x1, y1].

    Older snapshots persisted pdf_oxide's native (x, y, width, height) tuples
    directly. Detect that legacy shape cheaply and convert in-memory so cached
    references do not poison later rounds.
    """
    sample_count = 0
    saw_xywh = False
    for page in reference.get("pages", []):
        for key in ("words", "spans", "tables", "images", "paths"):
            for item in page.get(key, []):
                bbox = item.get("bbox")
                if not isinstance(bbox, list | tuple) or len(bbox) < 4:
                    continue
                sample_count += 1
                if float(bbox[2]) < float(bbox[0]) or float(bbox[3]) < float(bbox[1]):
                    saw_xywh = True
                    break
                if sample_count >= 128:
                    break
            if saw_xywh or sample_count >= 128:
                break
        if saw_xywh or sample_count >= 128:
            break

    if not saw_xywh:
        return reference, False

    for page in reference.get("pages", []):
        page_height = float(page.get("height") or 1.0)
        for key in ("words", "spans", "tables", "images", "paths"):
            for item in page.get(key, []):
                bbox = item.get("bbox")
                if not isinstance(bbox, list | tuple) or len(bbox) < 4:
                    continue
                x0 = float(bbox[0])
                y0_pdf = float(bbox[1])
                width = float(bbox[2])
                height = float(bbox[3])
                x1 = x0 + width
                y1_pdf = y0_pdf + height
                item["bbox"] = [x0, page_height - y1_pdf, x1, page_height - y0_pdf]

    return reference, True


def _reference_bbox_health(reference: dict[str, Any], sample_limit: int = 256) -> tuple[int, int]:
    """Sample reference boxes and count obviously invalid coordinates."""
    total = 0
    invalid = 0
    for page in reference.get("pages", []):
        page_width = float(page.get("width") or 1.0)
        page_height = float(page.get("height") or 1.0)
        for key in ("words", "spans", "tables", "images", "paths"):
            for item in page.get(key, []):
                bbox = item.get("bbox")
                if not isinstance(bbox, list | tuple) or len(bbox) < 4:
                    continue
                total += 1
                x0, y0, x1, y1 = [float(v) for v in bbox[:4]]
                if (
                    x1 < x0
                    or y1 < y0
                    or x0 < -1.0
                    or y0 < -1.0
                    or x1 > page_width + 1.0
                    or y1 > page_height + 1.0
                ):
                    invalid += 1
                if total >= sample_limit:
                    return invalid, total
    return invalid, total


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
            logger.warning("survey_document failed: {}", exc)

    toc_entries: list[dict[str, Any]] = []
    get_toc = _safe_getattr(oxide_doc, "get_toc")
    if callable(get_toc):
        try:
            toc = get_toc() or {}
            toc_entries = [e for e in toc.get("entries", []) if isinstance(e, dict)]
        except Exception as exc:  # pragma: no cover
            logger.warning("get_toc failed: {}", exc)

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
            bbox = _bbox_from_item(word, page_height)
            text = normalize_text(_text_from_wordish(word))
            if not bbox or not text:
                continue
            normalized_words.append({"text": text, "bbox": bbox})

        normalized_spans = []
        for span in spans:
            bbox = _bbox_from_item(span, page_height)
            text = normalize_text(_text_from_wordish(span))
            size = _font_size_from_span(span)
            if not bbox or not text:
                continue
            normalized_spans.append({"text": text, "bbox": bbox, "font_size": size, "bold": bool(span.get("bold"))})

        normalized_tables = []
        for table in tables:
            bbox = _bbox_from_item(table, page_height)
            if bbox:
                normalized_tables.append({"bbox": bbox, "rows": len(table.get("data") or [])})

        normalized_images = []
        for image in images:
            bbox = _bbox_from_item(image, page_height)
            if bbox:
                normalized_images.append({"bbox": bbox})

        normalized_paths = []
        for path in paths:
            bbox = _bbox_from_item(path, page_height)
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
        "snapshot_version": REFERENCE_SNAPSHOT_VERSION,
        "page_count": page_count,
        "toc_entries": toc_entries,
        "survey_table_pages": sorted(survey_table_pages),
        "pages": pages,
    }
    output_path.write_text(json.dumps(snapshot, indent=2))
    return snapshot


# ----------------------------------------------------------------------------
# Visual reference (frozen VLM oracle — evidence, not gate)
# ----------------------------------------------------------------------------
#
# The visual reference is a second frozen oracle, computed ONCE per PDF via
# /pdf-screenshot (page → PNG) + /scillm VLM (Sonnet OAuth). It surfaces
# visible elements that the primitive APIs cannot see (rotated text, elements
# below pdf_oxide's confidence threshold, etc.).
#
# Contract:
#   - Built once; reused across all rounds. Rebuilt only with --refresh-visual-reference.
#   - Findings ride in the review queue alongside primitive defects.
#   - Findings do NOT gate acceptance unless the category is explicitly listed in
#     BLOCKING_VISUAL_CATEGORIES (empty by default; promotion is manual).
#   - Metadata sidecar tracks model, prompt version, render DPI, per-page image SHA.
#     Drift is warned, never auto-resolved.


VISUAL_PROMPT_VERSION = "v3"

# Two prompts, two roles:
#   VISUAL_REFERENCE_PROMPT — "what is on this page" element discovery used to
#     build the frozen VLM reference snapshot (build_visual_reference()).
#     Evidence-only. Never drives corrections directly.
#
#   VISUAL_REVIEW_PROMPT    — overlay-aware adjudication against EXISTING
#     extracted blocks. Used by the per-page correction pass to decide whether
#     each candidate block is correct / bad_bbox / wrong_type / over_merge /
#     over_split, and to surface missed visible regions.
#
# Both prompts use the real shaped-extractor taxonomy — the 5 labels that
# classify_block() in python/pdf_oxide/extract_for_pdflab.py actually emits,
# confirmed against a full-document extraction:
#
#     text, header, table, boilerplate, page_number
#
# Nothing else. No figure/equation/caption — pdf_oxide does not emit those,
# and telling the VLM to label them would create a false taxonomy the gate
# cannot verify. If the extractor gains new types, add them here and bump
# VISUAL_PROMPT_VERSION so the cached visual reference is rebuilt.
#
# Coord convention: pixel / top-left origin on the rendered PNG (NOT PDF
# points, NOT PDF bottom-left). Image dimensions are substituted at format()
# time so the VLM knows the valid range.


VISUAL_REFERENCE_PROMPT = """You are auditing a single PDF page rendered as an image.

Task: list every visibly distinct content element on the page.

For each element, return:
  type   one of (shaped-extractor taxonomy):
           text         body paragraphs, sentences, list items
           header       section/subsection titles (any visible heading level)
           table        tabular data arranged in rows and columns
           boilerplate  running header/footer, legal notice, watermark
           page_number  a standalone page number at the top or bottom
  bbox   [x0, y0, x1, y1] in pixels in THIS image's coordinate system,
         top-left origin. The image is {width}x{height} pixels.
         0 <= x0 < x1 <= {width} and 0 <= y0 < y1 <= {height}.
  text   short excerpt (<= 200 chars) of the visible text; "" for non-text.
  level  integer 1-6 for header elements; null otherwise.

Rules:
  - Only report what you can SEE. Do not infer content from context.
  - Use ONLY the five types above. If a visible element does not fit
    (e.g. a figure, equation, or caption), skip it — the extractor does
    not produce those types, so reporting them would pollute the oracle.
  - Prefer splitting into separate elements over merging.
  - Skip purely decorative rules, borders, or bullets unless they define
    structure.
  - If the page is blank or only contains watermark/boilerplate, return
    {{"elements": []}}.

Return valid JSON only. No prose, no commentary, no markdown fences.
Exact shape:
{{"elements": [ {{"type": "...", "bbox": [x0,y0,x1,y1], "text": "...", "level": null}} ]}}
"""


VISUAL_REVIEW_PROMPT = """You are judging how a PDF extractor represented a single rendered page.

You are given:
  - PAGE_IMAGE: one page image, size {width}x{height} pixels.
  - <BLOCKS>: a JSON array appended below.

Do NOT re-extract the page.
Do NOT invent new taxonomy labels.
Your job is to judge the extractor's existing BLOCKS and identify any
in-scope visible region that has no corresponding candidate block.

Each block in <BLOCKS> has:
  - id: string
  - type: one of: text, header, table, boilerplate, page_number
  - bbox: [x0, y0, x1, y1]
  - text: short excerpt

Coordinate system:
  - Pixel coordinates on PAGE_IMAGE.
  - Origin is top-left.
  - bbox values are integers.
  - x0, y0 are inclusive; x1, y1 are exclusive.
  - Must satisfy:
      0 <= x0 < x1 <= {width}
      0 <= y0 < y1 <= {height}

Taxonomy (use ONLY these exact strings):
  text, header, table, boilerplate, page_number

Type guidance:
  - text         body text, paragraphs, list items, ordinary prose
  - header       titles, section headings, subsection headings
  - table        tabular content with structured rows/columns
  - boilerplate  running headers/footers, publication strings
  - page_number  page number only

Out of scope (extractor does not emit these — NEVER label them):
  - figures, photos, diagrams
  - equations, formulas
  - captions, decorative lines, shapes
  - Never use "figure" / "equation" / "caption" / any out-of-scope string
    in correct_type, split_bboxes[*].type, or missed.type.
  - Never emit a "missed" entry for an out-of-scope visible region.

Return JSON only. No prose. No markdown fences.

Return exactly:
  1. One judgement for EVERY input block id, in the SAME ORDER as <BLOCKS>.
  2. Then zero or more extra judgements for missed regions.

Allowed verdicts for input blocks:
  correct, bad_bbox, wrong_type, over_merge, over_split
Allowed verdict for extra regions:
  missed

Precedence (when more than one verdict seems possible, pick the FIRST that applies):
  1. over_merge
  2. over_split
  3. wrong_type
  4. bad_bbox
  5. correct

Verdict definitions (and required fields):

correct
  - Candidate refers to the right visible element.
  - Type is right.
  - bbox is acceptably tight.
  - Include: id, verdict.

bad_bbox
  - Candidate refers to the right visible element.
  - Type is basically right.
  - bbox is wrong (shifted, too large, too small, cuts off text).
  - Include: id, verdict, correct_bbox, reason.
  - If the candidate cannot be matched to any visible in-scope region,
    use:
      verdict: "bad_bbox"
      correct_bbox: null
      reason: "not locatable on image"

wrong_type
  - Candidate refers to one visible element.
  - bbox is basically right.
  - type is wrong.
  - Include: id, verdict, correct_type, reason.

over_merge
  - One candidate block combines 2 or more distinct in-scope logical
    elements that should be separate blocks.
  - Include: id, verdict,
    split_bboxes: [{{"type": "...", "bbox": [x0,y0,x1,y1]}}, ...],
    reason.

over_split
  - One logical in-scope element was emitted as multiple candidate
    blocks that should have been a single block.
  - Every fragment in the split group gets verdict "over_split".
  - For each such fragment, include:
      id, verdict, merge_with, reason
    where merge_with is the list of ALL OTHER ids in the same split
    group, sorted lexicographically.

missed
  - Use ONLY when a visible in-scope region has NO corresponding candidate
    block at all. Do NOT use "missed" for a region that already has some
    candidate, even if that candidate is badly boxed or mistyped — judge
    the candidate instead.
  - Do NOT use "missed" for out-of-scope regions (figures, equations,
    captions, decorative marks).
  - Include: id (null), verdict, type, bbox, reason.

General rules:
  - Use only the 5 taxonomy strings for correct_type, split_bboxes[*].type,
    and missed.type.
  - reason must be short, concrete, and <= 200 characters.
  - Do not duplicate ids.
  - Do not omit any input id.
  - Do not add fields not described above.

Output shape (strict):
{{"judgements": [
  {{"id": "b_001", "verdict": "correct"}},
  {{"id": "b_002", "verdict": "bad_bbox", "correct_bbox": [72, 612, 540, 780], "reason": "cuts off final line"}},
  {{"id": "b_003", "verdict": "wrong_type", "correct_type": "table", "reason": "visible rows and columns"}},
  {{"id": "b_004", "verdict": "over_merge",
   "split_bboxes": [
     {{"type": "header", "bbox": [72, 120, 540, 150]}},
     {{"type": "text",   "bbox": [72, 160, 540, 360]}}
   ],
   "reason": "header merged into paragraph"}},
  {{"id": "b_005", "verdict": "over_split", "merge_with": ["b_006"], "reason": "same paragraph split across adjacent blocks"}},
  {{"id": "b_006", "verdict": "over_split", "merge_with": ["b_005"], "reason": "same paragraph split across adjacent blocks"}},
  {{"id": null, "verdict": "missed", "type": "table", "bbox": [330, 640, 590, 780], "reason": "visible table has no candidate"}}
]}}
"""


# Backwards-compatible alias — existing build_visual_reference() call site
# formats this with width/height only.
VISUAL_PROMPT_TEMPLATE = VISUAL_REFERENCE_PROMPT


SCILLM_BASE_URL = os.environ.get("SCILLM_BASE_URL", "http://localhost:4001")
SCILLM_BEARER = os.environ.get("SCILLM_BEARER", "sk-dev-proxy-123")
PDF_SCREENSHOT_RUN = Path(
    os.environ.get(
        "PDF_SCREENSHOT_RUN",
        "/home/graham/.claude/skills/pdf-screenshot/run.sh",
    )
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def render_page_png(pdf_path: Path, page_num: int, out_path: Path, dpi: int) -> Path:
    """Render a single PDF page to PNG via /pdf-screenshot."""
    if not PDF_SCREENSHOT_RUN.exists():
        raise FileNotFoundError(f"pdf-screenshot not found: {PDF_SCREENSHOT_RUN}")
    cmd = [
        str(PDF_SCREENSHOT_RUN),
        str(pdf_path),
        "--page", str(page_num),
        "--out", str(out_path),
        "--dpi", str(dpi),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    if not out_path.exists():
        raise RuntimeError(f"pdf-screenshot did not produce {out_path}")
    return out_path


def _call_vlm_sonnet(image_path: Path, prompt: str, model: str, timeout: float = 120.0) -> dict[str, Any]:
    """Send a page image + prompt to /scillm VLM; return parsed JSON response."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("httpx is required for VLM calls") from exc

    import base64 as _b64
    png_bytes = image_path.read_bytes()
    data_url = f"data:image/png;base64,{_b64.b64encode(png_bytes).decode('ascii')}"

    # NOTE: Claude (vlm-claude) rejects response_format=json_object per
    # /scillm SKILL.md lines 713, 790 — we ask for strict JSON in the prompt
    # and rely on the proxy's JSON Guard middleware to strip ```json fences
    # and auto-repair minor JSON errors.
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {SCILLM_BEARER}",
        "Content-Type": "application/json",
    }
    url = f"{SCILLM_BASE_URL}/v1/chat/completions"
    resp = httpx.post(url, json=body, headers=headers, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()

    text = ""
    try:
        text = payload["choices"][0]["message"]["content"]
    except Exception:
        text = json.dumps(payload)
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"elements": [], "_raw": text}


def _png_dimensions(png_path: Path) -> tuple[int, int]:
    """Return image dimensions with a fast PNG-header path and Pillow fallback."""
    data = png_path.read_bytes()[:24]
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return (width, height)
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return (0, 0)
    try:
        with Image.open(png_path) as image:
            return image.size
    except Exception:  # pragma: no cover
        return (0, 0)


def build_visual_reference(
    paths: LoopPaths,
    *,
    model: str,
    dpi: int,
    max_pages: int | None,
    prompt_version: str = VISUAL_PROMPT_VERSION,
) -> dict[str, Any]:
    """Freeze a per-page VLM audit for the current PDF.

    Rebuilds every cached page PNG and calls /scillm VLM on each. This is
    expensive — call only when --refresh-visual-reference is set or when the
    reference does not exist. Reuse across rounds is the default.
    """
    logger.info("Building frozen VLM visual reference (model={}, dpi={})", model, dpi)

    reference = json.loads(paths.reference_json.read_text())
    page_count = int(reference.get("page_count") or 0)
    if max_pages is not None:
        page_count = min(page_count, max_pages)

    started = time.time()
    pages_out: list[dict[str, Any]] = []
    image_shas: dict[str, str] = {}

    for page_num in range(page_count):
        image_path = paths.visual_pages_dir / f"page_{page_num:04d}.png"
        try:
            render_page_png(paths.pdf, page_num, image_path, dpi=dpi)
        except Exception as exc:
            logger.warning("render failed on page {}: {}", page_num, exc)
            pages_out.append({"page": page_num, "elements": [], "error": f"render: {exc}"})
            continue

        image_shas[str(page_num)] = _sha256_file(image_path)
        width, height = _png_dimensions(image_path)
        prompt = VISUAL_PROMPT_TEMPLATE.format(width=width or 1, height=height or 1)

        try:
            vlm = _call_vlm_sonnet(image_path, prompt, model=model)
            raw_elements = vlm.get("elements") or []
        except Exception as exc:
            logger.warning("vlm failed on page {}: {}", page_num, exc)
            pages_out.append({"page": page_num, "elements": [], "error": f"vlm: {exc}"})
            continue

        elements: list[dict[str, Any]] = []
        for elem in raw_elements:
            if not isinstance(elem, dict):
                continue
            bbox = elem.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                continue
            elements.append(
                {
                    "type": str(elem.get("type") or "unknown"),
                    "bbox": [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])],
                    "text": normalize_text(str(elem.get("text") or ""))[:200],
                    "level": elem.get("level"),
                }
            )

        pages_out.append(
            {
                "page": page_num,
                "image_width": width,
                "image_height": height,
                "elements": elements,
            }
        )

    snapshot = {
        "pdf": str(paths.pdf),
        "created_at": now_iso(),
        "model": model,
        "prompt_version": prompt_version,
        "render_dpi": dpi,
        "page_count": page_count,
        "pages": pages_out,
    }
    meta = {
        "pdf": str(paths.pdf),
        "created_at": snapshot["created_at"],
        "model": model,
        "prompt_version": prompt_version,
        "render_dpi": dpi,
        "page_count": page_count,
        "image_shas": image_shas,
        "build_duration_seconds": round(time.time() - started, 2),
    }
    paths.visual_reference_json.write_text(json.dumps(snapshot, indent=2))
    paths.visual_reference_meta_json.write_text(json.dumps(meta, indent=2))
    return snapshot


def load_visual_reference(
    paths: LoopPaths,
    *,
    expected_model: str,
    expected_dpi: int,
    expected_prompt_version: str = VISUAL_PROMPT_VERSION,
) -> dict[str, Any] | None:
    """Return the cached visual reference, warning on config drift.

    Returns None if no cached reference exists; caller decides whether to build.
    Never auto-refreshes on drift.
    """
    if not paths.visual_reference_json.exists():
        return None
    if paths.visual_reference_meta_json.exists():
        try:
            meta = json.loads(paths.visual_reference_meta_json.read_text())
        except Exception:
            meta = {}
        drift = []
        if meta.get("model") != expected_model:
            drift.append(f"model {meta.get('model')!r} != {expected_model!r}")
        if meta.get("prompt_version") != expected_prompt_version:
            drift.append(f"prompt {meta.get('prompt_version')!r} != {expected_prompt_version!r}")
        if int(meta.get("render_dpi") or 0) != int(expected_dpi):
            drift.append(f"dpi {meta.get('render_dpi')} != {expected_dpi}")
        if drift:
            logger.warning(
                "visual reference metadata drift (reuse allowed, refresh recommended): %s",
                "; ".join(drift),
            )
    return json.loads(paths.visual_reference_json.read_text())


# ----------------------------------------------------------------------------
# Visual defect scanner (evidence — non-blocking unless promoted)
# ----------------------------------------------------------------------------


def _image_to_pdf_bbox(
    bbox_px: list[float],
    image_w: int,
    image_h: int,
    page_w: float,
    page_h: float,
) -> list[float]:
    """Map a VLM bbox (image pixels, top-left origin) into PDF point space.

    pdf_oxide blocks are stored in PDF-point coordinates; we convert so visual
    bboxes can be compared with block bboxes directly.
    """
    if image_w <= 0 or image_h <= 0:
        return [float(x) for x in bbox_px]
    sx = page_w / image_w
    sy = page_h / image_h
    return [bbox_px[0] * sx, bbox_px[1] * sy, bbox_px[2] * sx, bbox_px[3] * sy]


def scan_visual_defects(
    reference: dict[str, Any],
    visual_ref: dict[str, Any],
    extraction: dict[str, Any],
) -> list[Defect]:
    """Diagnose visual defects: MISSED_VISIBLE_REGION / BAD_BOUNDARY / OVER_MERGE / OVER_SPLIT.

    These ride in the review queue as evidence; the gate ignores them unless a
    category is promoted via BLOCKING_VISUAL_CATEGORIES.
    """
    defects: list[Defect] = []

    ref_pages = {int(p.get("page", -1)): p for p in reference.get("pages", [])}
    vis_pages = {int(p.get("page", -1)): p for p in visual_ref.get("pages", [])}

    blocks_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for block in extraction.get("blocks", []):
        blocks_by_page[int(block.get("page", 0))].append(block)

    for page_num, vis_page in vis_pages.items():
        ref_page = ref_pages.get(page_num)
        if not ref_page:
            continue
        page_w = float(ref_page.get("width") or 1.0)
        page_h = float(ref_page.get("height") or 1.0)
        image_w = int(vis_page.get("image_width") or 0)
        image_h = int(vis_page.get("image_height") or 0)

        extracted = blocks_by_page.get(page_num, [])
        extracted_boxes = [block_bbox_pixels(b, page_w, page_h) for b in extracted]

        visual_boxes_in_pdf: list[tuple[dict[str, Any], list[float]]] = []
        for elem in vis_page.get("elements") or []:
            pdf_bbox = _image_to_pdf_bbox(elem["bbox"], image_w, image_h, page_w, page_h)
            visual_boxes_in_pdf.append((elem, pdf_bbox))

        # 1) MISSED_VISIBLE_REGION: VLM saw a content element, no extracted block covers it.
        for elem, pdf_bbox in visual_boxes_in_pdf:
            if elem["type"] in {"page_number", "header", "footer"}:
                continue  # boilerplate — not a content miss
            if not _covered_by_any(pdf_bbox, extracted_boxes, threshold=0.40):
                defects.append(
                    Defect(
                        category="MISSED_VISIBLE_REGION",
                        page=page_num,
                        severity="medium",
                        detail=f"VLM saw {elem['type']}: {elem['text'][:80]!r}",
                        source="visual",
                        bbox=pdf_bbox,
                        blocking=("MISSED_VISIBLE_REGION" in BLOCKING_VISUAL_CATEGORIES),
                        extra={"visual_type": elem["type"], "level": elem.get("level")},
                    )
                )

        # 2) BAD_BOUNDARY: block overlaps a visual element but boundaries disagree sharply.
        for block, block_box in zip(extracted, extracted_boxes):
            if block.get("blockType") in {"boilerplate", "page_number"}:
                continue
            best = None
            best_ratio = 0.0
            for elem, pdf_bbox in visual_boxes_in_pdf:
                ratio = bbox_intersection_ratio(block_box, pdf_bbox)
                if ratio > best_ratio:
                    best_ratio = ratio
                    best = (elem, pdf_bbox)
            if best is None or best_ratio < 0.30:
                continue
            elem, pdf_bbox = best
            # Boundary disagreement: partial overlap, not a tight match.
            if 0.30 <= best_ratio < 0.75:
                defects.append(
                    Defect(
                        category="BAD_BOUNDARY",
                        page=page_num,
                        severity="low",
                        detail=f"{block.get('blockType')} block boundary disagrees with visual {elem['type']}",
                        source="visual",
                        bbox=block_box,
                        blocking=("BAD_BOUNDARY" in BLOCKING_VISUAL_CATEGORIES),
                        extra={"overlap_ratio": round(best_ratio, 3), "visual_type": elem["type"]},
                    )
                )

        # 3) OVER_MERGE: one extracted block covers multiple distinct visual elements.
        for block, block_box in zip(extracted, extracted_boxes):
            if block.get("blockType") in {"boilerplate", "page_number"}:
                continue
            covered = [
                elem for elem, pdf_bbox in visual_boxes_in_pdf
                if bbox_intersection_ratio(pdf_bbox, block_box) >= 0.70
                and elem["type"] not in {"header", "footer", "page_number"}
            ]
            if len(covered) >= 2:
                types = sorted({c["type"] for c in covered})
                defects.append(
                    Defect(
                        category="OVER_MERGE",
                        page=page_num,
                        severity="medium",
                        detail=f"{block.get('blockType')} block merges {len(covered)} visual elements: {types}",
                        source="visual",
                        bbox=block_box,
                        blocking=("OVER_MERGE" in BLOCKING_VISUAL_CATEGORIES),
                        extra={"merged_count": len(covered), "visual_types": types},
                    )
                )

        # 4) OVER_SPLIT: a single visual element is covered by multiple extracted blocks.
        for elem, pdf_bbox in visual_boxes_in_pdf:
            if elem["type"] in {"header", "footer", "page_number"}:
                continue
            covering = [
                (block, block_box) for block, block_box in zip(extracted, extracted_boxes)
                if bbox_intersection_ratio(block_box, pdf_bbox) >= 0.35
                and block.get("blockType") not in {"boilerplate", "page_number"}
            ]
            if len(covering) >= 3:  # >= 3 to avoid false positives on caption+figure
                defects.append(
                    Defect(
                        category="OVER_SPLIT",
                        page=page_num,
                        severity="medium",
                        detail=f"Visual {elem['type']} split across {len(covering)} blocks",
                        source="visual",
                        bbox=pdf_bbox,
                        blocking=("OVER_SPLIT" in BLOCKING_VISUAL_CATEGORIES),
                        extra={"split_count": len(covering), "visual_type": elem["type"]},
                    )
                )

    return defects


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

        # 2) Duplicate blocks — keyed on (blockType, rounded bbox, text prefix).
        # Including the bbox prevents legitimate repeated phrases (e.g. "Low",
        # "Moderate" in a control-summary table) from registering as duplicates
        # when they sit at clearly different positions on the page.
        seen: set[tuple[str, tuple[float, float, float, float], str]] = set()
        for block in extracted:
            text = normalize_text(block.get("text") or "")
            if len(text) < 20:
                continue
            bbox_px = block_bbox_pixels(block, page_width, page_height)
            bbox_key = quantize_bbox(bbox_px, digits=1)
            key = (str(block.get("blockType", "?")), bbox_key, text[:100])
            if key in seen:
                defects.append(
                    Defect(
                        category="DUPLICATE_BLOCK",
                        page=page_num,
                        severity="high",
                        detail=f"Duplicate {key[0]} block at {bbox_key}: {text[:80]}",
                        source="deterministic",
                        bbox=bbox_px,
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


def _defect_sort_key(defect: dict[str, Any]) -> tuple[int, int, str]:
    """Order within a page: blocking primitive → non-blocking primitive → visual attached → visual-only.

    The "visual attached" bucket is visual defects whose bbox overlaps a primitive
    defect on the same page (attached evidence). "Visual-only" are visuals with no
    matching primitive — they land at the end.
    """
    is_visual = defect.get("category") in VISUAL_CATEGORIES
    is_blocking = bool(defect.get("blocking", True))
    is_attached = bool(defect.get("extra", {}).get("attached_to_primitive", False))

    if not is_visual and is_blocking:
        bucket = 0
    elif not is_visual and not is_blocking:
        bucket = 1
    elif is_visual and is_attached:
        bucket = 2
    else:
        bucket = 3
    sev_rank = {"high": 0, "medium": 1, "low": 2}.get(defect.get("severity") or "low", 3)
    return (bucket, sev_rank, defect.get("category", ""))


def _tag_visual_attachments(defects: list[dict[str, Any]]) -> None:
    """Mark visual defects whose bbox overlaps a primitive defect on the same page."""
    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for d in defects:
        by_page[int(d.get("page", -1))].append(d)
    for page_num, page_defects in by_page.items():
        primitive_boxes = [
            d["bbox"] for d in page_defects
            if d.get("category") not in VISUAL_CATEGORIES and d.get("bbox")
        ]
        for d in page_defects:
            if d.get("category") not in VISUAL_CATEGORIES:
                continue
            bbox = d.get("bbox")
            extra = d.setdefault("extra", {}) or {}
            d["extra"] = extra
            if bbox and any(bbox_intersection_ratio(bbox, pb) >= 0.20 for pb in primitive_boxes):
                extra["attached_to_primitive"] = True


def build_review_queue(defects_report: dict[str, Any], render_template: str | None, paths: LoopPaths) -> dict[str, Any]:
    """Create a per-page queue for /code-runner (and humans).

    Within each page, defects are ordered:
      1. blocking primitive defects
      2. non-blocking primitive defects
      3. visual findings attached to the same page region as a primitive defect
      4. visual-only findings
    """
    defects = defects_report.get("defects", [])
    _tag_visual_attachments(defects)

    pages_with_any_defect = sorted({int(d.get("page", -1)) for d in defects if int(d.get("page", -1)) >= 0})

    review_items = []
    for page_num in pages_with_any_defect:
        image_path = paths.review_dir / f"page_{page_num:04d}.png"
        render_error = None
        if render_template:
            cmd = render_template.format(pdf=str(paths.pdf), page=page_num, out=str(image_path))
            try:
                subprocess.run(shlex.split(cmd), check=True, capture_output=True, text=True)
            except Exception as exc:  # pragma: no cover - environment dependent
                render_error = str(exc)

        page_defects = [d for d in defects if int(d.get("page", -1)) == page_num]
        page_defects.sort(key=_defect_sort_key)
        review_items.append(
            {
                "page": page_num,
                "image": str(image_path) if image_path.exists() else None,
                "render_error": render_error,
                "defects": page_defects,
                "defect_counts": {
                    "primitive_blocking": sum(
                        1 for d in page_defects
                        if d.get("category") not in VISUAL_CATEGORIES and d.get("blocking", True)
                    ),
                    "primitive_nonblocking": sum(
                        1 for d in page_defects
                        if d.get("category") not in VISUAL_CATEGORIES and not d.get("blocking", True)
                    ),
                    "visual_attached": sum(
                        1 for d in page_defects
                        if d.get("category") in VISUAL_CATEGORIES
                        and (d.get("extra") or {}).get("attached_to_primitive")
                    ),
                    "visual_only": sum(
                        1 for d in page_defects
                        if d.get("category") in VISUAL_CATEGORIES
                        and not (d.get("extra") or {}).get("attached_to_primitive")
                    ),
                },
            }
        )

    review_queue = {
        "created_at": now_iso(),
        "pdf": str(paths.pdf),
        "items": review_items,
    }
    paths.review_json.write_text(json.dumps(review_queue, indent=2))
    return review_queue


def _load_current_extraction(paths: LoopPaths) -> dict[str, Any]:
    return json.loads(paths.extraction_json.read_text())


def _load_reference(paths: LoopPaths) -> dict[str, Any]:
    return json.loads(paths.reference_json.read_text())


def _page_ref_by_num(reference: dict[str, Any], page_num: int) -> dict[str, Any] | None:
    for page in reference.get("pages", []):
        if int(page.get("page", -1)) == page_num:
            return page
    return None


def _normalized_bbox_from_pdf_bbox(bbox: list[float], page_width: float, page_height: float) -> list[float]:
    if page_width <= 0 or page_height <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        round(float(bbox[0]) / page_width, 6),
        round(float(bbox[1]) / page_height, 6),
        round(float(bbox[2]) / page_width, 6),
        round(float(bbox[3]) / page_height, 6),
    ]


def _block_bbox_image_pixels(
    block: dict[str, Any],
    image_width: int,
    image_height: int,
    page_width: float,
    page_height: float,
) -> list[float]:
    bbox = block.get("bbox") or [0.0, 0.0, 0.0, 0.0]
    if max(bbox) <= 1.5:
        return [
            float(bbox[0]) * image_width,
            float(bbox[1]) * image_height,
            float(bbox[2]) * image_width,
            float(bbox[3]) * image_height,
        ]
    if page_width <= 0 or page_height <= 0:
        return [float(x) for x in bbox]
    return [
        float(bbox[0]) * image_width / page_width,
        float(bbox[1]) * image_height / page_height,
        float(bbox[2]) * image_width / page_width,
        float(bbox[3]) * image_height / page_height,
    ]


def _overlay_style_for_block(block_type: str) -> tuple[str, str]:
    color = BLOCK_TYPE_COLORS.get(block_type, "#EF4444")
    label = block_type.upper()
    return (color, label)


def _ensure_focus_page_image(
    paths: LoopPaths,
    page_num: int,
    review_item: dict[str, Any],
    *,
    dpi: int,
) -> tuple[Path | None, str | None]:
    image_path = None
    render_error = review_item.get("render_error")
    review_image = review_item.get("image")
    if review_image:
        candidate = Path(review_image)
        if candidate.exists():
            image_path = candidate

    if image_path is None:
        image_path = paths.review_dir / f"page_{page_num:04d}.png"
        if not image_path.exists():
            try:
                render_page_png(paths.pdf, page_num, image_path, dpi=dpi)
            except Exception as exc:
                render_error = str(exc)
                image_path = None

    return (image_path, render_error)


def _write_focus_overlay(
    image_path: Path,
    out_path: Path,
    *,
    page_blocks: list[dict[str, Any]],
    page_width: float,
    page_height: float,
) -> tuple[Path | None, dict[str, Any]]:
    try:
        from PIL import Image, ImageColor, ImageDraw
    except ImportError as exc:  # pragma: no cover
        logger.warning("Pillow not available for overlay rendering: {}", exc)
        return (None, {})

    image = Image.open(image_path).convert("RGBA")
    draw = ImageDraw.Draw(image)
    image_width, image_height = image.size
    legend: dict[str, Any] = {}

    for block in page_blocks:
        block_type = str(block.get("blockType") or "unknown")
        color_hex, label = _overlay_style_for_block(block_type)
        rgba = ImageColor.getcolor(color_hex, "RGBA")
        outline = (rgba[0], rgba[1], rgba[2], 255)
        fill = (rgba[0], rgba[1], rgba[2], 38)
        bbox = _block_bbox_image_pixels(
            block,
            image_width,
            image_height,
            page_width,
            page_height,
        )
        x0, y0, x1, y1 = bbox
        if x1 <= x0 or y1 <= y0:
            continue
        draw.rectangle([x0, y0, x1, y1], outline=outline, fill=fill, width=3)

        block_id = str(block.get("id") or f"p{block.get('page', 0)}_{block_type}")
        tag = f"{block_id}:{label}"
        text_y = max(0.0, y0 - 16.0)
        text_box = [x0, text_y, min(image_width, x0 + max(48, len(tag) * 7)), text_y + 14]
        draw.rectangle(text_box, fill=outline)
        draw.text((x0 + 2, text_y), tag, fill=(255, 255, 255, 255))

        legend.setdefault(block_type, {"color": color_hex, "count": 0})
        legend[block_type]["count"] += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(out_path)
    return (out_path, legend)


def _candidate_block_packet(
    block: dict[str, Any],
    *,
    page_width: float,
    page_height: float,
    image_width: int | None,
    image_height: int | None,
) -> dict[str, Any]:
    bbox_pdf = block_bbox_pixels(block, page_width, page_height)
    bbox_normalized = (
        [round(float(x), 6) for x in (block.get("bbox") or bbox_pdf)]
        if max(block.get("bbox") or [0.0, 0.0, 0.0, 0.0]) <= 1.5
        else _normalized_bbox_from_pdf_bbox(bbox_pdf, page_width, page_height)
    )
    payload = {
        "id": block.get("id"),
        "page": int(block.get("page", -1)),
        "blockType": block.get("blockType"),
        "confidence": block.get("confidence"),
        "text": block.get("text") or "",
        "bbox_normalized": bbox_normalized,
        "bbox_pdf_points": [round(float(x), 3) for x in bbox_pdf],
        "qids": block.get("qids") or [],
        "tocEntries": block.get("tocEntries") or [],
    }
    if image_width is not None and image_height is not None:
        payload["bbox_image_pixels"] = [
            round(float(x), 1)
            for x in _block_bbox_image_pixels(
                block,
                image_width,
                image_height,
                page_width,
                page_height,
            )
        ]
    return payload


def _page_reference_summary(page_ref: dict[str, Any]) -> dict[str, Any]:
    table_boxes = [item["bbox"] for item in page_ref.get("tables", [])[:8]]
    header_candidates = _derive_header_candidates(page_ref)
    return {
        "page_width_points": float(page_ref.get("width") or 0.0),
        "page_height_points": float(page_ref.get("height") or 0.0),
        "word_count": int(page_ref.get("word_count") or len(page_ref.get("words", []))),
        "span_count": int(page_ref.get("span_count") or len(page_ref.get("spans", []))),
        "table_expected": bool(page_ref.get("table_expected")),
        "table_regions_pdf_points": table_boxes,
        "header_candidate_preview": [c.get("text") for c in header_candidates[:5]],
        "page_text_preview": normalize_text(page_ref.get("page_text") or "")[:400],
    }


def _normalized_scillm_chat_url(raw_url: str | None) -> str:
    """Normalize SCILLM_API_BASE to the full chat-completions endpoint.

    /code-runner expects the env var to be the full POST target, but this shell
    often exports only the service root (`http://localhost:4001`). Normalize it
    before spawning the subprocess so code-runner doesn't post to `/`.
    """
    if not raw_url:
        return "http://localhost:4001/v1/chat/completions"
    normalized = raw_url.rstrip("/")
    if normalized.endswith("/v1/chat/completions"):
        return normalized
    if normalized.endswith("/v1"):
        return f"{normalized}/chat/completions"
    return f"{normalized}/v1/chat/completions"


def _blocking_defect_count(review_item: dict[str, Any]) -> int:
    return sum(1 for defect in review_item.get("defects", []) if defect.get("blocking", True))


def select_focus_review_item(review_queue: dict[str, Any]) -> dict[str, Any] | None:
    items = review_queue.get("items", [])
    blocking_items = [item for item in items if _blocking_defect_count(item) > 0]
    if not blocking_items:
        return None
    return sorted(
        blocking_items,
        key=lambda item: (
            -_blocking_defect_count(item),
            -len(item.get("defects", [])),
            int(item.get("page", -1)),
        ),
    )[0]


def write_page_focus_packet(
    paths: LoopPaths,
    *,
    round_num: int,
    review_item: dict[str, Any],
    defects_report: dict[str, Any],
    extraction: dict[str, Any],
    render_dpi: int = FOCUS_RENDER_DPI,
) -> Path:
    page_num = int(review_item["page"])
    reference = _load_reference(paths)
    page_ref = _page_ref_by_num(reference, page_num)
    if page_ref is None:
        raise RuntimeError(f"Missing reference page for focus packet: {page_num}")

    page_width = float(page_ref.get("width") or 1.0)
    page_height = float(page_ref.get("height") or 1.0)
    page_blocks = [
        block
        for block in extraction.get("blocks", [])
        if int(block.get("page", -1)) == page_num
    ]
    image_path, render_error = _ensure_focus_page_image(
        paths,
        page_num,
        review_item,
        dpi=render_dpi,
    )
    image_width = None
    image_height = None
    if image_path is not None and image_path.exists():
        image_width, image_height = _png_dimensions(image_path)

    overlay_path = None
    overlay_legend: dict[str, Any] = {}
    if image_path is not None and image_path.exists():
        overlay_path, overlay_legend = _write_focus_overlay(
            image_path,
            paths.review_dir / f"page_{page_num:04d}.overlay.png",
            page_blocks=page_blocks,
            page_width=page_width,
            page_height=page_height,
        )

    candidate_blocks = [
        _candidate_block_packet(
            block,
            page_width=page_width,
            page_height=page_height,
            image_width=image_width,
            image_height=image_height,
        )
        for block in page_blocks
    ]
    packet = {
        "created_at": now_iso(),
        "pdf": str(paths.pdf),
        "round": round_num,
        "page": page_num,
        "image": str(image_path) if image_path is not None and image_path.exists() else None,
        "overlay_image": str(overlay_path) if overlay_path is not None and overlay_path.exists() else None,
        "overlay_legend": overlay_legend,
        "render_error": render_error,
        "page_geometry": {
            "page_width_points": page_width,
            "page_height_points": page_height,
            "image_width": image_width,
            "image_height": image_height,
            "overlay_coordinate_space": "top-left image pixels",
            "pdf_coordinate_space": "top-left page points",
            "normalized_coordinate_space": "[0,1] normalized page fractions",
        },
        "defect_counts": review_item.get("defect_counts", {}),
        "blocking_defect_count": _blocking_defect_count(review_item),
        "defects": review_item.get("defects", []),
        "candidate_blocks": candidate_blocks,
        "candidate_block_counts": dict(Counter(block.get("blockType") for block in page_blocks)),
        "reference_summary": _page_reference_summary(page_ref),
        "document_blocking_summary": defects_report.get("blocking_summary", {}),
        "document_weighted_blocking": defects_report.get("blocking_weighted_total", 0),
    }
    packet_path = paths.workdir / f"{paths.key}.round_{round_num:02d}.page_{page_num:04d}.json"
    packet_path.write_text(json.dumps(packet, indent=2))
    return packet_path


# ----------------------------------------------------------------------------
# State management
# ----------------------------------------------------------------------------


def _default_round_state(pdf: Path) -> dict[str, Any]:
    return {
        "pdf": str(pdf),
        "created_at": now_iso(),
        "baseline": None,
        "rounds": [],
    }


def load_round_state(rounds_json: Path, pdf: Path) -> dict[str, Any]:
    if rounds_json.exists():
        return json.loads(rounds_json.read_text())
    return _default_round_state(pdf)


def save_round_state(rounds_json: Path, state: dict[str, Any]) -> None:
    rounds_json.write_text(json.dumps(state, indent=2))


def snapshot_allowlist_files(
    repo_root: Path,
    allowlist: list[str],
    backup_root: Path,
) -> dict[str, Any]:
    """Snapshot allowlisted files before a /code-runner round.

    Records per-file existence + backup path so rejection can restore the
    working-tree state exactly (not just the last committed version).
    """
    backup_root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "created_at": now_iso(),
        "repo_root": str(repo_root),
        "files": [],
    }
    for relpath in allowlist:
        abs_path = (repo_root / relpath).resolve()
        entry = {
            "relpath": relpath,
            "abs_path": str(abs_path),
            "existed": abs_path.exists(),
            "backup_path": None,
        }
        if abs_path.exists():
            backup_path = backup_root / relpath
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(abs_path, backup_path)
            entry["backup_path"] = str(backup_path)
        manifest["files"].append(entry)

    manifest_path = backup_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


def restore_allowlist_files(manifest: dict[str, Any]) -> list[str]:
    """Restore allowlisted files from a pre-round snapshot.

    Handles three cases:
      - file existed pre-round → copy backup back
      - file did not exist pre-round → delete if created
      - allowlist entry pointed to a directory → remove tree
    Raises on inconsistent manifests (missing backup for existing file).
    """
    restored: list[str] = []
    for entry in manifest.get("files", []):
        relpath = entry["relpath"]
        abs_path = Path(entry["abs_path"])
        existed = bool(entry.get("existed"))
        backup_path = entry.get("backup_path")

        if existed:
            if not backup_path:
                raise RuntimeError(f"Missing backup path for existing file: {relpath}")
            src = Path(backup_path)
            if not src.exists():
                raise RuntimeError(f"Backup missing for {relpath}: {src}")
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, abs_path)
            restored.append(relpath)
        else:
            if abs_path.exists():
                if abs_path.is_file():
                    abs_path.unlink()
                else:
                    shutil.rmtree(abs_path)
                restored.append(relpath)
    return restored


def last_accepted_summary(state: dict[str, Any]) -> dict[str, int] | None:
    """Return the blocking (gate-relevant) category counts of the last accepted round.

    Reads the new `metrics.summary_blocking` structure first, falls back to
    legacy flat fields for state files written before the metrics-views patch.
    """
    for entry in reversed(state.get("rounds", [])):
        if entry.get("status") == "accepted":
            if entry.get("metrics_version") != METRICS_VERSION:
                continue
            metrics = entry.get("metrics") or {}
            source = (
                metrics.get("summary_blocking")
                or entry.get("blocking_summary")
                or entry.get("summary")
                or {}
            )
            return {k: int(v) for k, v in source.items()}
    baseline = state.get("baseline")
    if isinstance(baseline, dict):
        if baseline.get("metrics_version") != METRICS_VERSION:
            return None
        if "metrics" in baseline:
            summary = baseline.get("metrics", {}).get("summary_blocking", {})
            return {k: int(v) for k, v in summary.items()}
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


# RATIONALE (not sent to LLM)
# Purpose: Guide /code-runner to repair one extraction failure mode using a
# single page as the diagnostic lens while full-document DoD remains the gate.
# Consumer: /code-runner task spec for the pdf_oxide repair harness.
# Why this matters: A vague repair prompt invites page-specific hacks, noisy
# edits, or fake coverage wins that do not survive deterministic validation.
# Input: page_focus_json with page-local defects, extracted blocks, page
# geometry, page image, overlay image, project knowledge, and the exact DoD
# command below.
# Output: code edits only within the allowlist; success is determined by the
# external DoD command, not by prose.
FIX_PROMPT = """You are editing pdf_oxide extraction calibration code.

Task:
Use the focus-page evidence to identify one mechanism-level extraction problem,
then patch the allowlisted code so the full-document deterministic gate improves.

Files you may edit:
{allowlist}

Read these inputs first:
1. Project knowledge: {project_knowledge}
   Use this to remember the intended split:
   - pass 1 is deterministic extraction
   - pass 2 is shaping/classification/enrichment
   - /code-runner is the exception handler, not the runtime extractor
2. Focus page packet: {page_focus_json}
   Use these exact fields:
   - `page`
   - `defects[]`: page-local defect records; use `category`, `detail`, `bbox`, `blocking`
   - `page_geometry`: point-space, normalized-space, and image-space coordinates
   - `candidate_blocks[]`: current extracted blocks on this page; use:
     `id`, `blockType`, `confidence`, `text`,
     `bbox_normalized`, `bbox_pdf_points`, `bbox_image_pixels`
   - `candidate_block_counts`
   - `reference_summary`
   - `document_blocking_summary`, `document_weighted_blocking`
   - `image`, `overlay_image`, `overlay_legend`
3. Focus page screenshot path: {page_image}
4. Focus page overlay path: {page_overlay_image}
5. Accepted-baseline state: {rounds_json}

Required reasoning order:
1. Inspect the focus page image and the overlay image together.
2. Use `candidate_blocks[]` by `id` to decide which extracted element is wrong:
   wrong type, bad bbox, missing region, over-merge, or over-split.
3. Explain the failure using the packet fields above, not guesswork.
4. Infer the smallest mechanism-level code change in the allowlist that can
   correct that behavior on this page and similar pages.
5. Reject any page-specific or PDF-specific special case.

Priority categories:
1. `MISSING_TEXT_REGION`
2. `MISSING_TABLE_PAGE`
3. `PHANTOM_BLOCK`
4. `DUPLICATE_BLOCK`
5. `EMPTY_BLOCK`

Validation command:
`{dod_command}`

Success means all of these are true:
- the validation command passes on the full PDF
- weighted blocking defects improve versus the last accepted baseline
- no new hard-regression categories appear
- the change is explainable as a general extraction rule

Treat these as wrong answers:
- any condition on this PDF name, this page number, or quoted page text
- synthetic placeholder blocks added only to satisfy coverage
- inventing content that is not visually present on the page image
- ignoring the existing block ids / bbox evidence and reasoning from prose alone
- edits outside the allowlist
- broad speculative rewrites not justified by the packet fields
- a change that helps page {focus_page} but fails the full-document DoD

Example:
- Acceptable: the overlay shows block `b17` is tagged `text` but visually
  aligns with a running header band; tighten the header classification rule so
  similar top-of-page spans become `header`.
- Acceptable: block `b22` visibly misses the right edge of a title region;
  adjust a bbox-merging or normalization rule so the emitted title bbox expands
  for similar spans.
- Unacceptable: if page == {focus_page}, emit a special-case block or hardcode
  this PDF's page text.

Working style:
- make the smallest viable code edit
- use the page image, overlay, and extracted element geometry together
- trust the external DoD command over your intuition
- if the packet does not justify a safe fix, avoid speculative changes

You are evaluated by the code diff and the validation command result, not by prose.
"""


def run_code_runner(
    *,
    repo_root: Path,
    code_runner_path: Path,
    allowlist: list[str],
    paths: LoopPaths,
    dod_command: str,
    backend: str,
    round_num: int,
    focus_page: int,
    page_focus_json: Path,
    page_image: str | None,
    page_overlay_image: str | None,
    inner_max_rounds: int = 3,
) -> subprocess.CompletedProcess[str]:
    """Invoke /code-runner via its task-spec JSON contract.

    /code-runner's CLI is `run.sh run <spec.json> [--max-rounds N] [--backend M]`
    — it does NOT take per-flag prompt/allowlist/dod overrides. Build the spec
    file in the workdir, then invoke the subcommand form.
    """
    if not code_runner_path.exists():
        raise FileNotFoundError(f"code-runner not found: {code_runner_path}")
    project_knowledge = repo_root / "PROJECT_KNOWLEDGE.md"

    prompt = FIX_PROMPT.format(
        focus_page=focus_page,
        page_focus_json=str(page_focus_json),
        page_image=page_image or "not available",
        page_overlay_image=page_overlay_image or "not available",
        project_knowledge=str(project_knowledge),
        rounds_json=str(paths.rounds_json),
        dod_command=dod_command,
        allowlist=", ".join(allowlist),
    )

    read_context = [
        str(page_focus_json),
        str(paths.rounds_json),
    ]
    if project_knowledge.exists():
        read_context.append(str(project_knowledge))
    if page_image:
        read_context.append(str(page_image))
    if page_overlay_image:
        read_context.append(str(page_overlay_image))

    spec = {
        "task_id": f"{paths.key}_round_{round_num:02d}_page_{focus_page:04d}",
        "title": f"pdf_oxide extraction repair (round {round_num}, page {focus_page})",
        "prompt": prompt,
        "backend": backend,
        "cwd": str(repo_root),
        "output_dir": str(paths.workdir / f"{paths.key}.code_runner" / f"round_{round_num:02d}_page_{focus_page:04d}"),
        "allowlist": list(allowlist),
        "read_context": read_context,
        "definition_of_done": {
            "command": dod_command,
            "assertion": '"accepted": true',
        },
        "max_rounds": inner_max_rounds,
    }
    spec_path = paths.workdir / f"{paths.key}.round_{round_num:02d}.page_{focus_page:04d}.code_runner_spec.json"
    spec_path.write_text(json.dumps(spec, indent=2))

    cmd = [
        str(code_runner_path),
        "run",
        str(spec_path),
        "--max-rounds", str(inner_max_rounds),
        "--backend", backend,
    ]

    # Our DoD re-extracts a 492-page PDF — far longer than /code-runner's
    # 60s default. Bump it via CODE_RUNNER_DOD_TIMEOUT (read by evidence.py).
    # Also normalize SCILLM_API_BASE: code-runner expects the full
    # /v1/chat/completions URL, but many shells export only the service root.
    env = {
        **os.environ,
        "CODE_RUNNER_DOD_TIMEOUT": "900",
        "SCILLM_API_BASE": _normalized_scillm_chat_url(os.environ.get("SCILLM_API_BASE")),
    }

    logger.info("Running code-runner with spec {}", spec_path)
    return subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, env=env)


# ----------------------------------------------------------------------------
# Gating
# ----------------------------------------------------------------------------


def gate_candidate(
    prior_summary: dict[str, int] | None,
    current_summary: dict[str, int],
    *,
    promoted_visual: set[str] | None = None,
) -> GateResult:
    """Commit/revert gate.

    Only primitive categories (and any explicitly promoted visual categories)
    count toward the weighted score and hard-regression check. Visual findings
    ride in the review queue as evidence but do NOT gate acceptance by default.
    """
    promoted = promoted_visual or BLOCKING_VISUAL_CATEGORIES
    prior_summary = prior_summary or {}

    prior_blocking = primitive_summary_only(prior_summary, promoted)
    current_blocking = primitive_summary_only(current_summary, promoted)

    current_total = weighted_total(current_blocking)
    prior_total = weighted_total(prior_blocking)

    hard_set = effective_hard_categories(promoted)
    regressions: list[str] = []
    hard_regressions: list[str] = []
    for category, current_count in current_blocking.items():
        prior_count = int(prior_blocking.get(category, 0))
        if current_count > prior_count:
            regressions.append(category)
            if category in hard_set:
                hard_regressions.append(category)

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
        logger.info("Using existing reference snapshot: {}", paths.reference_json)
        reference = json.loads(paths.reference_json.read_text())
        invalid_boxes, sampled_boxes = _reference_bbox_health(reference)
        snapshot_version = int(reference.get("snapshot_version") or 0)
        if snapshot_version != REFERENCE_SNAPSHOT_VERSION or invalid_boxes:
            logger.warning(
                "Rebuilding cached reference snapshot: version={} expected={} invalid_boxes={}/{}",
                snapshot_version,
                REFERENCE_SNAPSHOT_VERSION,
                invalid_boxes,
                sampled_boxes,
            )
            return build_reference_snapshot(paths.pdf, paths.reference_json, max_pages=max_pages)
        return reference
    logger.info("Building frozen pass-1 reference snapshot")
    reference = build_reference_snapshot(paths.pdf, paths.reference_json, max_pages=max_pages)
    return reference


def baseline_if_missing(state: dict[str, Any], defects_report: dict[str, Any], rounds_json: Path) -> dict[str, Any]:
    baseline = state.get("baseline")
    baseline_version = baseline.get("metrics_version") if isinstance(baseline, dict) else None
    if baseline is None or baseline_version != METRICS_VERSION:
        if baseline is not None and baseline_version != METRICS_VERSION:
            logger.warning(
                "Resetting baseline metrics due to version change: {} -> {}",
                baseline_version,
                METRICS_VERSION,
            )
        metrics = build_metrics_views(
            defects_report.get("summary", {}),
            blocking_summary=defects_report.get("blocking_summary", {}),
        )
        state["baseline"] = {
            "created_at": now_iso(),
            "metrics_version": METRICS_VERSION,
            "metrics": metrics,
        }
        save_round_state(rounds_json, state)
    return state


@dataclasses.dataclass
class VisualConfig:
    enabled: bool = False
    model: str = "vlm-claude"
    dpi: int = 150
    refresh: bool = False


def ensure_visual_reference(paths: LoopPaths, config: VisualConfig, max_pages: int | None) -> dict[str, Any] | None:
    if not config.enabled:
        return None
    if config.refresh or not paths.visual_reference_json.exists():
        return build_visual_reference(
            paths,
            model=config.model,
            dpi=config.dpi,
            max_pages=max_pages,
        )
    cached = load_visual_reference(
        paths,
        expected_model=config.model,
        expected_dpi=config.dpi,
    )
    if cached is None:
        return build_visual_reference(
            paths,
            model=config.model,
            dpi=config.dpi,
            max_pages=max_pages,
        )
    return cached


def run_diagnose_cycle(
    paths: LoopPaths,
    max_pages: int | None,
    render_template: str | None,
    visual: VisualConfig | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    cycle_started = time.perf_counter()
    reference = ensure_reference(paths, max_pages=max_pages)
    logger.info("Diagnose cycle: running shaped extraction -> {}", paths.extraction_json)
    extraction_started = time.perf_counter()
    extraction = run_shaped_extraction(paths.pdf, paths.extraction_json, max_pages=max_pages)
    logger.info(
        "Diagnose cycle: shaped extraction complete in {:.2f}s ({} blocks)",
        time.perf_counter() - extraction_started,
        len(extraction.get("blocks", [])),
    )
    logger.info("Diagnose cycle: scanning defects")
    scan_started = time.perf_counter()
    defects_report = scan_defects(reference, extraction)
    logger.info(
        "Diagnose cycle: defect scan complete in {:.2f}s (blocking_total={})",
        time.perf_counter() - scan_started,
        defects_report.get("blocking_weighted_total", 0),
    )

    visual_ref = ensure_visual_reference(paths, visual or VisualConfig(), max_pages)
    if visual_ref is not None:
        try:
            visual_defects = scan_visual_defects(reference, visual_ref, extraction)
        except Exception as exc:
            logger.warning("visual scan failed: {}", exc)
            visual_defects = []
        if visual_defects:
            combined_defects = list(defects_report.get("defects", [])) + [d.to_dict() for d in visual_defects]
            defects_report["defects"] = combined_defects
            summary = Counter(d["category"] for d in combined_defects)
            blocking_summary = Counter(
                d["category"] for d in combined_defects if d.get("blocking", True)
            )
            defects_report["summary"] = dict(summary)
            defects_report["blocking_summary"] = dict(blocking_summary)
            defects_report["weighted_total"] = weighted_total(dict(summary))
            defects_report["blocking_weighted_total"] = weighted_total(dict(blocking_summary))
            # Update page_scores to include visual contributions.
            page_scores: Counter[int] = Counter()
            for d in combined_defects:
                page_scores[int(d.get("page", -1))] += 1
            defects_report["page_scores"] = dict(page_scores)

    paths.defects_json.write_text(json.dumps(defects_report, indent=2))
    logger.info("Diagnose cycle: wrote defects -> {}", paths.defects_json)
    review_queue = build_review_queue(defects_report, render_template, paths)
    logger.info(
        "Diagnose cycle complete in {:.2f}s (review_pages={})",
        time.perf_counter() - cycle_started,
        len(review_queue.get("items", [])),
    )
    return reference, defects_report, review_queue


def check_benchmark_suite(
    benchmark_pdfs: list[Path],
    *,
    workdir: Path,
    max_pages: int | None,
    render_template: str | None,
    visual: VisualConfig | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    """Run the diagnose cycle on each benchmark PDF and require non-regression.

    Improvement is not required, only non-regression. A benchmark fails if its
    weighted blocking total rises OR any hard category regresses.
    """
    results: list[dict[str, Any]] = []
    overall_ok = True

    for benchmark_pdf in benchmark_pdfs:
        benchmark_pdf = benchmark_pdf.resolve()
        if not benchmark_pdf.exists():
            results.append({"pdf": str(benchmark_pdf), "ok": False, "error": "benchmark PDF not found"})
            overall_ok = False
            continue

        bench_paths = LoopPaths(pdf=benchmark_pdf, workdir=workdir.resolve())
        bench_paths.ensure()
        bench_state = load_round_state(bench_paths.rounds_json, benchmark_pdf)

        _, bench_defects_report, _ = run_diagnose_cycle(
            bench_paths,
            max_pages=max_pages,
            render_template=render_template,
            visual=visual,
        )
        bench_state = baseline_if_missing(bench_state, bench_defects_report, bench_paths.rounds_json)
        prior_summary = last_accepted_summary(bench_state)
        gate = gate_candidate(prior_summary, bench_defects_report.get("blocking_summary", {}))

        ok = (gate.current_total <= gate.prior_total) and not gate.hard_regressions
        results.append(
            {
                "pdf": str(benchmark_pdf),
                "ok": ok,
                "prior_total": gate.prior_total,
                "current_total": gate.current_total,
                "regressions": gate.regressions,
                "hard_regressions": gate.hard_regressions,
            }
        )
        if not ok:
            overall_ok = False

    return overall_ok, results


def do_dod_check(
    paths: LoopPaths,
    max_pages: int | None,
    render_template: str | None,
    visual: VisualConfig | None = None,
    benchmark_pdfs: list[Path] | None = None,
) -> int:
    state = load_round_state(paths.rounds_json, paths.pdf)
    _, defects_report, _ = run_diagnose_cycle(
        paths, max_pages=max_pages, render_template=render_template, visual=visual,
    )
    metrics = build_metrics_views(
        defects_report.get("summary", {}),
        blocking_summary=defects_report.get("blocking_summary", {}),
    )
    prior_summary = last_accepted_summary(state)
    # Gate on blocking view only — non-blocking primitives and visual evidence
    # must not influence accept/revert.
    gate = gate_candidate(prior_summary, metrics["summary_blocking"])

    benchmarks_ok, benchmark_results = check_benchmark_suite(
        benchmark_pdfs or [],
        workdir=paths.workdir,
        max_pages=max_pages,
        render_template=render_template,
        visual=visual,
    )

    accepted = gate.accepted and benchmarks_ok
    print(json.dumps({
        "accepted": accepted,
        "metrics": metrics,
        "gate_metrics": {
            "prior_weighted_blocking": gate.prior_total,
            "current_weighted_blocking": gate.current_total,
            "accepted": gate.accepted,
            "regressions": gate.regressions,
            "hard_regressions": gate.hard_regressions,
        },
        "benchmarks_ok": benchmarks_ok,
        "benchmark_results": benchmark_results,
    }, indent=2))
    return 0 if accepted else 1


def run_focus_packet_smoke_check(
    paths: LoopPaths,
    *,
    sample_size: int,
    sample_seed: int,
    max_pages: int | None,
    render_template: str | None,
    visual: VisualConfig | None = None,
) -> int:
    """Build a deterministic sample of focus packets and assert core invariants."""
    if sample_size <= 0:
        raise ValueError("sample_size must be > 0")

    _, defects_report, review_queue = run_diagnose_cycle(
        paths,
        max_pages=max_pages,
        render_template=render_template,
        visual=visual,
    )
    items = review_queue.get("items", [])
    if not items:
        print(json.dumps({
            "status": "fail",
            "reason": "review queue is empty",
            "sample_size": sample_size,
            "sample_seed": sample_seed,
        }, indent=2))
        return 1

    rng = random.Random(sample_seed)
    chosen = items if len(items) <= sample_size else rng.sample(items, sample_size)
    extraction = _load_current_extraction(paths)

    sampled_results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, item in enumerate(chosen, start=1):
        page_num = int(item["page"])
        packet_path = write_page_focus_packet(
            paths,
            round_num=9000 + index,
            review_item=item,
            defects_report=defects_report,
            extraction=extraction,
        )
        packet = json.loads(packet_path.read_text())
        image_path = Path(packet["image"]) if packet.get("image") else None
        overlay_path = Path(packet["overlay_image"]) if packet.get("overlay_image") else None
        candidate_blocks = packet.get("candidate_blocks", [])
        page_geometry = packet.get("page_geometry") or {}

        checks = {
            "image_exists": bool(image_path and image_path.exists()),
            "overlay_exists": bool(overlay_path and overlay_path.exists()),
            "candidate_blocks_present": bool(candidate_blocks),
            "all_blocks_have_image_bbox": all(
                "bbox_image_pixels" in block for block in candidate_blocks
            ),
            "image_dimensions_nonzero": bool(
                int(page_geometry.get("image_width") or 0) > 0
                and int(page_geometry.get("image_height") or 0) > 0
            ),
        }
        passed = all(checks.values())
        result = {
            "page": page_num,
            "packet": str(packet_path),
            "checks": checks,
            "candidate_block_count": len(candidate_blocks),
            "block_types": dict(Counter(block.get("blockType") for block in candidate_blocks)),
            "overlay_legend": packet.get("overlay_legend", {}),
        }
        sampled_results.append(result)
        if not passed:
            failures.append(result)

    status = "pass" if not failures else "fail"
    print(json.dumps({
        "status": status,
        "sample_size": sample_size,
        "sample_seed": sample_seed,
        "pages_tested": [int(item["page"]) for item in chosen],
        "results": sampled_results,
        "failure_count": len(failures),
    }, indent=2))
    return 0 if not failures else 1


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
        default=["python/pdf_oxide/extract_for_pdflab.py"],
        help="Repeatable allowlist entry for /code-runner. Defaults to the shaped "
             "extractor only; adding the scanner lets the agent move goalposts and "
             "should require an explicit --allowlist override.",
    )
    parser.add_argument(
        "--benchmark-pdf",
        action="append",
        default=[],
        type=Path,
        help="Repeatable benchmark PDF path. Each benchmark must non-regress to accept.",
    )
    parser.add_argument(
        "--render-page-cmd",
        help=(
            "Optional render command template for suspicious pages. Use {pdf}, {page}, and {out}. "
            "Example: 'python scripts/render_pdf_page.py --pdf {pdf} --page {page} --out {out}'"
        ),
    )
    parser.add_argument("--dod-check", action="store_true")
    parser.add_argument(
        "--focus-packet-smoke-check",
        type=int,
        metavar="N",
        help="Build and validate a deterministic random sample of N focus packets.",
    )
    parser.add_argument(
        "--focus-packet-seed",
        type=int,
        default=53,
        help="RNG seed for --focus-packet-smoke-check sampling.",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--enable-visual",
        action="store_true",
        help="Build/use a frozen VLM visual reference as secondary evidence (not a gate).",
    )
    parser.add_argument("--refresh-visual-reference", action="store_true")
    parser.add_argument("--visual-model", default="vlm-claude")
    parser.add_argument("--visual-dpi", type=int, default=150)
    parser.add_argument(
        "--promote-visual-category",
        action="append",
        default=[],
        choices=sorted(VISUAL_CATEGORIES),
        help="Promote a visual category to blocking (adds to BLOCKING_VISUAL_CATEGORIES for this run).",
    )
    args = parser.parse_args()

    configure_logging(args.verbose)

    # Apply visual-category promotions for this run (manual only — no auto).
    for cat in args.promote_visual_category:
        BLOCKING_VISUAL_CATEGORIES.add(cat)
    if BLOCKING_VISUAL_CATEGORIES:
        logger.info("BLOCKING_VISUAL_CATEGORIES promoted for this run: {}", sorted(BLOCKING_VISUAL_CATEGORIES))

    pdf_path = args.pdf.resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    paths = LoopPaths(pdf=pdf_path, workdir=args.workdir.resolve())
    paths.ensure()

    visual_cfg = VisualConfig(
        enabled=args.enable_visual,
        model=args.visual_model,
        dpi=args.visual_dpi,
        refresh=args.refresh_visual_reference,
    )

    if args.dod_check:
        return do_dod_check(
            paths,
            max_pages=args.max_pages,
            render_template=args.render_page_cmd,
            visual=visual_cfg,
            benchmark_pdfs=args.benchmark_pdf,
        )
    if args.focus_packet_smoke_check is not None:
        return run_focus_packet_smoke_check(
            paths,
            sample_size=args.focus_packet_smoke_check,
            sample_seed=args.focus_packet_seed,
            max_pages=args.max_pages,
            render_template=args.render_page_cmd,
            visual=visual_cfg,
        )

    state = load_round_state(paths.rounds_json, pdf_path)

    # Preflight / baseline.
    logger.info("Preflight diagnose cycle")
    _, defects_report, review_queue = run_diagnose_cycle(
        paths, max_pages=args.max_pages, render_template=args.render_page_cmd, visual=visual_cfg,
    )
    state = baseline_if_missing(state, defects_report, paths.rounds_json)

    last_total = weighted_total(last_accepted_summary(state) or {})
    logger.info("Preflight baseline ready: weighted_blocking={}", last_total)
    if last_total == 0:
        logger.info("Halting: zero weighted blocking defects at preflight")
        return 0
    stall_count = 0

    for _ in range(args.max_rounds):
        state = load_round_state(paths.rounds_json, pdf_path)
        round_num = len(state.get("rounds", [])) + 1

        logger.info("Starting round {}", round_num)
        prior_summary = last_accepted_summary(state)
        current_review_queue = review_queue if round_num == 1 else json.loads(paths.review_json.read_text())
        current_defects_report = defects_report if round_num == 1 else json.loads(paths.defects_json.read_text())
        focus_item = select_focus_review_item(current_review_queue)
        if focus_item is None:
            logger.info("Halting: no blocking page-level defects remain")
            return 0
        focus_page = int(focus_item["page"])
        focus_packet = write_page_focus_packet(
            paths,
            round_num=round_num,
            review_item=focus_item,
            defects_report=current_defects_report,
            extraction=_load_current_extraction(paths),
            render_dpi=visual_cfg.dpi if visual_cfg.enabled else FOCUS_RENDER_DPI,
        )
        focus_packet_data = json.loads(focus_packet.read_text())
        logger.info(
            "Round {} focus page={} blocking_defects={} context={}",
            round_num,
            focus_page,
            _blocking_defect_count(focus_item),
            focus_packet,
        )

        # Snapshot allowlisted files BEFORE the round so a rejected round
        # can be restored regardless of whether /code-runner is promotion-safe.
        backup_root = paths.workdir / f"{paths.key}.backup_round_{round_num:02d}"
        snapshot_manifest = snapshot_allowlist_files(
            args.repo_root.resolve(),
            args.allowlist,
            backup_root,
        )

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
        for bench_pdf in args.benchmark_pdf:
            dod_command += f" --benchmark-pdf {shlex.quote(str(bench_pdf.resolve()))}"
        if visual_cfg.enabled:
            dod_command += (
                f" --enable-visual --visual-model {shlex.quote(visual_cfg.model)} "
                f"--visual-dpi {visual_cfg.dpi}"
            )
            for cat in sorted(BLOCKING_VISUAL_CATEGORIES):
                dod_command += f" --promote-visual-category {shlex.quote(cat)}"

        t0 = time.time()
        code_runner_result = run_code_runner(
            repo_root=args.repo_root.resolve(),
            code_runner_path=args.code_runner.resolve(),
            allowlist=args.allowlist,
            paths=paths,
            dod_command=dod_command,
            backend=args.backend,
            round_num=round_num,
            focus_page=focus_page,
            page_focus_json=focus_packet,
            page_image=focus_packet_data.get("image"),
            page_overlay_image=focus_packet_data.get("overlay_image"),
        )
        elapsed = round(time.time() - t0, 2)

        _, defects_report, review_queue = run_diagnose_cycle(
            paths, max_pages=args.max_pages, render_template=args.render_page_cmd, visual=visual_cfg,
        )
        # Split the full summary into explicit views so that rounds.json and
        # the gate never share a single ambiguous "summary" field:
        #   metrics["summary_full"]     — every category seen this round
        #   metrics["summary_blocking"] — primitives + promoted visuals (gate oracle)
        #   metrics["summary_visual"]   — visual-only (evidence, not gate)
        metrics = build_metrics_views(
            defects_report.get("summary", {}),
            blocking_summary=defects_report.get("blocking_summary", {}),
        )
        gate = gate_candidate(prior_summary, metrics["summary_blocking"])

        # Benchmark non-regression check — guards against overfitting the
        # triggering PDF at the expense of the fixture suite.
        benchmarks_ok, benchmark_results = check_benchmark_suite(
            args.benchmark_pdf,
            workdir=paths.workdir,
            max_pages=args.max_pages,
            render_template=args.render_page_cmd,
            visual=visual_cfg,
        )

        status = (
            "accepted"
            if (code_runner_result.returncode == 0 and gate.accepted and benchmarks_ok)
            else "rejected"
        )

        # On rejection, restore the pre-round snapshot of allowlisted files.
        # This makes the loop safe regardless of whether /code-runner is
        # promotion-safe. If restore itself fails, halt — we cannot continue
        # with a possibly-dirty working tree.
        restore_performed = False
        restored_paths: list[str] = []
        restore_error: str | None = None
        if status != "accepted":
            try:
                restored_paths = restore_allowlist_files(snapshot_manifest)
                restore_performed = True
                logger.info("Rejected round restored allowlisted files: {}", restored_paths)
            except Exception as exc:
                restore_error = str(exc)
                logger.exception("Failed to restore allowlisted files after rejected round")
                raise SystemExit(
                    f"Restore failed after rejected round {round_num}: {restore_error}"
                )

        entry = {
            "round": round_num,
            "timestamp": now_iso(),
            "status": status,
            "metrics_version": METRICS_VERSION,
            "elapsed_seconds": elapsed,
            "focus_page": focus_page,
            "focus_packet": str(focus_packet),
            "metrics": metrics,
            "gate": gate.to_dict(),
            "gate_metrics": {
                "prior_weighted_blocking": gate.prior_total,
                "current_weighted_blocking": gate.current_total,
                "accepted": gate.accepted,
                "regressions": gate.regressions,
                "hard_regressions": gate.hard_regressions,
            },
            "benchmarks_ok": benchmarks_ok,
            "benchmark_results": benchmark_results,
            "restore": {
                "performed": restore_performed,
                "paths": restored_paths,
                "error": restore_error,
                "backup_root": str(backup_root),
            },
            "code_runner": {
                "returncode": code_runner_result.returncode,
                "stdout_tail": code_runner_result.stdout[-2000:],
                "stderr_tail": code_runner_result.stderr[-1000:],
            },
        }
        state.setdefault("rounds", []).append(entry)
        save_round_state(paths.rounds_json, state)

        # Stall detection uses the blocking weighted total from the metrics
        # view — visual fluctuations must not influence halt logic.
        current_total = metrics["totals"]["weighted_blocking"]
        logger.info(
            "Round {} status={} current_total={} prior_total={} regressions={} benchmarks_ok={}",
            round_num,
            status,
            current_total,
            gate.prior_total,
            gate.regressions,
            benchmarks_ok,
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
            logger.info("Halting: stall limit reached ({})", args.stall_limit)
            return 0

    logger.info("Halting: max rounds reached ({})", args.max_rounds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
