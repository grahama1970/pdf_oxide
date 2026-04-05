"""PDF Cloner: Preset discovery and validation via synthetic fixtures."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import itertools
import json
import os
import random
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from difflib import SequenceMatcher

import httpx
import typer
from loguru import logger
from pydantic import BaseModel, Field, ValidationError
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Table, TableStyle
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

from pypdf import PdfReader, PdfWriter

import pdf_oxide
from pdf_oxide.survey import survey_document

app = typer.Typer(name="clone_pdf", help="PDF Cloner — profile, sample, clone, score")


class ElementType(str, Enum):
    header = "header"
    body = "body"
    table = "table"
    figure = "figure"
    caption = "caption"
    list_item = "list"
    footnote = "footnote"
    equation = "equation"
    page_number = "page_number"
    running_header = "running_header"
    running_footer = "running_footer"


class IRElement(BaseModel):
    id: str
    type: ElementType
    bbox: list[float] = Field(default_factory=list)
    text: str = ""
    header_level: Optional[int] = 0
    page: int = 0
    reading_order: int = 0
    font_size: float = 12.0
    is_bold: bool = False
    numbering: Optional[str] = None


class TableCell(BaseModel):
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    text: str
    role: str = "data"


class TableContinuation(BaseModel):
    is_continued: bool = False
    continued_from: Optional[str] = None


class IRTable(BaseModel):
    table_id: str
    page_start: int
    page_end: int
    bbox_per_page: dict[int, list[float]] = Field(default_factory=dict)
    caption: Optional[str] = None
    n_header_rows: int = 1
    n_rows: int
    n_cols: int
    cells: list[TableCell]
    continuation: TableContinuation = Field(default_factory=TableContinuation)
    style: str = "ruled"


class IRRelationship(BaseModel):
    type: str
    source: str
    target: str


class WindowIR(BaseModel):
    window_id: str
    source_pages: list[int]
    source_pdf: str
    family_id: str
    elements: list[IRElement] = Field(default_factory=list)
    tables: list[IRTable] = Field(default_factory=list)
    relationships: list[IRRelationship] = Field(default_factory=list)
    reading_order: list[str] = Field(default_factory=list)


def validate_ir(ir_dict: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []

    try:
        ir = WindowIR(**ir_dict)
    except ValidationError as exc:
        return False, [str(err) for err in exc.errors()]

    element_ids = [el.id for el in ir.elements]
    table_ids = [tbl.table_id for tbl in ir.tables]

    if len(set(element_ids)) != len(element_ids):
        errors.append("duplicate element IDs found")

    valid_ids = set(element_ids) | set(table_ids)

    for ref_id in ir.reading_order:
        if ref_id not in set(element_ids):
            errors.append(f"reading_order references unknown element id: {ref_id}")

    for rel in ir.relationships:
        if rel.source not in valid_ids:
            errors.append(f"relationship source references unknown id: {rel.source}")
        if rel.target not in valid_ids:
            errors.append(f"relationship target references unknown id: {rel.target}")

    source_page_set = set(ir.source_pages)
    default_page = ir.source_pages[0] if ir.source_pages else 0
    for el in ir.elements:
        if el.page not in source_page_set:
            el.page = default_page  # auto-fix missing/wrong page

    for tbl in ir.tables:
        if tbl.page_start not in source_page_set:
            tbl.page_start = default_page
        if tbl.page_end not in source_page_set:
            tbl.page_end = default_page

    return len(errors) == 0, errors


def _build_page_signatures(doc, survey: Dict[str, Any]) -> List[Dict[str, Any]]:
    page_details = survey.get("page_details", []) or []
    table_pages = set(survey.get("table_pages", []) or [])
    figure_pages = set(survey.get("figure_pages", []) or [])
    equation_pages = set(survey.get("equation_pages", []) or [])

    signatures: List[Dict[str, Any]] = []
    for idx, detail in enumerate(page_details):
        page_num = int(detail.get("page", idx))
        signatures.append(
            {
                "page_num": page_num,
                "char_count": int(detail.get("char_count", 0) or 0),
                "has_images": bool(detail.get("has_images", False)),
                "is_blank": bool(detail.get("is_blank", False)),
                "table_candidate": page_num in table_pages,
                "figure_candidate": page_num in figure_pages,
                "equation_candidate": page_num in equation_pages,
            }
        )
    return signatures


def profile_for_cloning(pdf_path: str) -> Dict[str, Any]:
    doc = pdf_oxide.PdfDocument(pdf_path)
    survey = survey_document(doc, enrich_profile=True)
    toc = doc.get_toc() or []
    _ = doc.get_section_map()

    page_count = int(survey.get("page_count", 0) or 0)

    return {
        "doc_id": hashlib.md5(pdf_path.encode("utf-8")).hexdigest(),
        "path": pdf_path,
        "page_count": page_count,
        "domain": survey.get("domain", "general"),
        "complexity_score": survey.get("complexity_score", 1),
        "layout_mode": "multi_column" if int(survey.get("columns", 1) or 1) > 1 else "single_column",
        "has_toc": bool(survey.get("has_toc", False)),
        "toc_entry_count": int(survey.get("toc_entry_count", 0) or 0),
        "toc_pages": [e.get("page") for e in toc if isinstance(e, dict)] or [],
        "lof_entries": [e for e in toc if isinstance(e, dict) and e.get("entry_type") == "Figure"],
        "lot_entries": [e for e in toc if isinstance(e, dict) and e.get("entry_type") == "Table"],
        "has_tables": bool(survey.get("has_tables", False)),
        "table_density": len(survey.get("table_pages", []) or []) / max(page_count, 1),
        "has_figures": bool(survey.get("has_figures", False)),
        "figure_density": len(survey.get("figure_pages", []) or []) / max(page_count, 1),
        "has_equations": bool(survey.get("has_equations", False)),
        "has_engineering": survey.get("domain") in ("engineering", "defense"),
        "section_count": int(survey.get("section_count", 0) or 0),
        "section_style": survey.get("section_style"),
        "is_scanned": bool(survey.get("is_scanned", False)),
        "page_signatures": _build_page_signatures(doc, survey),
    }


def assign_family(signature: dict) -> dict:
    domain = signature.get("domain")
    table_density = float(signature.get("table_density", 0.0) or 0.0)
    section_style = signature.get("section_style")
    has_engineering = bool(signature.get("has_engineering", False))
    layout_mode = signature.get("layout_mode")
    has_equations = bool(signature.get("has_equations", False))
    has_toc = bool(signature.get("has_toc", False))
    section_count = int(signature.get("section_count", 0) or 0)
    is_scanned = bool(signature.get("is_scanned", False))
    figure_density = float(signature.get("figure_density", 0.0) or 0.0)

    rules_matched: list[str] = []

    if domain == "defense" and table_density > 0.3 and section_style == "decimal":
        family_id = "defense_spec_requirements_tables"
        confidence = 0.9
        rules_matched.append("rule_1_defense_table_decimal")
    elif domain == "defense" and has_engineering:
        family_id = "defense_engineering_drawings"
        confidence = 0.9
        rules_matched.append("rule_2_defense_engineering")
    elif domain == "engineering" and table_density > 0.2:
        family_id = "engineering_spec_tables"
        confidence = 0.9
        rules_matched.append("rule_3_engineering_tables")
    elif domain == "academic" and layout_mode == "multi_column" and has_equations:
        family_id = "academic_twocol_math"
        confidence = 0.9
        rules_matched.append("rule_4_academic_twocol_math")
    elif domain == "academic" and layout_mode == "multi_column":
        family_id = "academic_twocol_prose"
        confidence = 0.9
        rules_matched.append("rule_5_academic_twocol_prose")
    elif domain == "academic":
        family_id = "academic_singlecol"
        confidence = 0.9
        rules_matched.append("rule_6_academic_singlecol")
    elif has_toc and section_count > 20 and table_density > 0.1:
        family_id = "technical_manual_mixed"
        confidence = 0.7
        rules_matched.append("rule_7_toc_sections_tables")
    elif is_scanned:
        family_id = "scanned_mixed"
        confidence = 0.7
        rules_matched.append("rule_8_scanned")
    else:
        family_id = "general_prose"
        confidence = 0.5
        rules_matched.append("rule_9_default")

    subfamily_id: Optional[str] = None
    if table_density > 0.35 and has_toc:
        subfamily_id = "appendix_matrix_tables"
        rules_matched.append("subfamily_table_heavy_appendices")
    elif figure_density > 0.3:
        subfamily_id = "figure_heavy_sections"
        rules_matched.append("subfamily_figure_heavy_sections")

    return {
        "family_id": family_id,
        "subfamily_id": subfamily_id,
        "confidence": confidence,
        "rules_matched": rules_matched,
    }

def profile_and_assign(pdf_path: str) -> dict:
    signature = profile_for_cloning(pdf_path)
    assigned = assign_family(signature)
    return {**signature, **assigned}


def build_sampling_plan(pdf_path: str, max_windows: int = 20, seed: int = 42) -> dict:
    random.seed(seed)
    profile = profile_for_cloning(pdf_path)
    section_map = pdf_oxide.PdfDocument(pdf_path).get_section_map() or []

    total_pages = int(profile.get("page_count", 0) or 0)
    if total_pages <= 0:
        return {
            "strategy": "toc_guided_structural_stratified",
            "seed": seed,
            "total_pages": 0,
            "windows": [],
            "category_counts": {"anchor": 0, "boundary": 0, "pathology": 0, "span": 0},
        }

    target_counts = {
        "anchor": max(1, int(round(max_windows * 0.4))),
        "boundary": max(1, int(round(max_windows * 0.2))),
        "pathology": max(1, int(round(max_windows * 0.2))),
        "span": max(1, int(round(max_windows * 0.2))),
    }

    total_target = sum(target_counts.values())
    if total_target != max_windows:
        target_counts["anchor"] += max_windows - total_target

    windows: list[dict] = []
    category_counts = {"anchor": 0, "boundary": 0, "pathology": 0, "span": 0}
    seen_pages: set[tuple[int, ...]] = set()

    def add_window(source_pages: list[int], category: str, reason: str) -> bool:
        norm_pages = sorted(set(int(p) for p in source_pages if 1 <= int(p) <= total_pages))
        if not norm_pages or len(windows) >= max_windows:
            return False
        key = tuple(norm_pages)
        if key in seen_pages:
            return False
        if category_counts[category] >= target_counts[category]:
            return False
        seen_pages.add(key)
        category_counts[category] += 1
        windows.append(
            {
                "window_id": f"WIN_{len(windows) + 1:04d}",
                "source_pages": norm_pages,
                "category": category,
                "selection_reason": [reason],
            }
        )
        return True

    add_window([1], "anchor", "first_content_page")
    add_window([total_pages], "anchor", "last_page")

    for entry in profile.get("lof_entries", []) or []:
        p = entry.get("page") if isinstance(entry, dict) else None
        if p is not None:
            add_window([int(p)], "boundary", "lof_reference")

    for entry in profile.get("lot_entries", []) or []:
        p = entry.get("page") if isinstance(entry, dict) else None
        if p is not None:
            add_window([int(p)], "pathology", "lot_reference")

    section_starts: list[int] = []
    for sec in section_map:
        if isinstance(sec, dict):
            page = sec.get("page") or sec.get("start_page")
            if page is not None:
                section_starts.append(int(page))
        elif isinstance(sec, (list, tuple)) and sec:
            try:
                section_starts.append(int(sec[0]))
            except (TypeError, ValueError, IndexError):
                pass
    for p in sorted(set(section_starts)):
        add_window([p], "boundary", "section_start")

    signatures = profile.get("page_signatures", []) or []
    pathology_ranked = sorted(
        signatures,
        key=lambda s: int(bool(s.get("table_candidate"))) + int(bool(s.get("equation_candidate"))),
        reverse=True,
    )
    for sig in pathology_ranked:
        page = int(sig.get("page_num", 0) or 0)
        score = int(bool(sig.get("table_candidate"))) + int(bool(sig.get("equation_candidate")))
        if score > 0:
            add_window([page], "pathology", "high_table_equation_score")

    table_pages = {int(s.get("page_num", 0) or 0) for s in signatures if s.get("table_candidate")}
    for p in sorted(table_pages):
        if p + 1 in table_pages:
            add_window([p, p + 1], "span", "table_continuation")

    anchor_candidates = list(range(1, total_pages + 1))
    random.shuffle(anchor_candidates)
    for p in anchor_candidates:
        add_window([p], "anchor", "stratified_anchor_fill")

    while len(windows) < max_windows:
        p = random.randint(1, total_pages)
        if add_window([p], "anchor", "anchor_backfill"):
            continue
        for cat in ("boundary", "pathology", "span"):
            if category_counts[cat] < target_counts[cat]:
                old = target_counts[cat]
                target_counts[cat] = category_counts[cat]
                target_counts["anchor"] += old - target_counts[cat]

    return {
        "strategy": "toc_guided_structural_stratified",
        "seed": seed,
        "total_pages": total_pages,
        "windows": windows,
        "category_counts": category_counts,
    }



def build_test_manifest(profile: dict, sampling_plan: dict, fixture_dir: str, preset_candidates: Optional[list[dict]] = None) -> dict:
    windows_manifest: list[dict] = []
    for window in sampling_plan.get("windows", []) or []:
        wid = str(window.get("window_id", ""))
        window_dir = os.path.join(fixture_dir, wid)
        ir_path = os.path.join(window_dir, "ir.json")
        extraction_targets: list[str] = []

        if os.path.exists(ir_path):
            try:
                with open(ir_path, "r", encoding="utf-8") as f:
                    ir_data = json.load(f)
                element_types = {
                    str(el.get("type"))
                    for el in (ir_data.get("elements", []) or [])
                    if isinstance(el, dict) and el.get("type")
                }
                if ir_data.get("tables"):
                    element_types.add("table")
                extraction_targets = sorted(element_types)
            except (json.JSONDecodeError, KeyError, OSError):
                extraction_targets = []

        windows_manifest.append(
            {
                "window_id": wid,
                "fixture": {
                    "synthetic_pdf": os.path.join(window_dir, "synthetic.pdf"),
                    "ir_path": ir_path,
                    "truth_document": os.path.join(window_dir, "truth_document.json"),
                    "truth_tables": os.path.join(window_dir, "truth_tables.json"),
                    "renderer_backend": "reportlab",
                },
                "extraction_targets": extraction_targets,
                "source_pages": window.get("source_pages", []),
            }
        )

    manifest = {
        "manifest_version": "1.0",
        "created": datetime.now(timezone.utc).isoformat(),
        "source_document": {
            "doc_id": profile["doc_id"],
            "path": profile["path"],
            "page_count": profile["page_count"],
            "family_id": profile["family_id"],
            "subfamily_id": profile.get("subfamily_id"),
        },
        "sampling": sampling_plan,
        "windows": windows_manifest,
        "preset_candidates": preset_candidates or [{"name": "default", "overrides": {}}],
        "scoring": {
            "metrics": [
                "block_presence_f1",
                "block_type_accuracy",
                "section_recall",
                "table_presence_f1",
                "table_cell_f1",
                "reading_order_score",
            ],
            "pass_thresholds": {
                "block_presence_f1": 0.85,
                "table_cell_f1": 0.80,
                "reading_order_score": 0.80,
            },
        },
        "test_usage": {
            "valid_for_preset_discovery": True,
            "valid_for_synthetic_regression": True,
            "valid_for_real_pdf_comparison": True,
        },
    }

    os.makedirs(fixture_dir, exist_ok=True)
    with open(os.path.join(fixture_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return manifest
def compile_document_truth(ir: dict) -> dict:
    parsed = WindowIR(**ir)

    type_map = {
        ElementType.header: "Header",
        ElementType.body: "Body",
        ElementType.caption: "Caption",
        ElementType.list_item: "List",
        ElementType.footnote: "Footnote",
        ElementType.equation: "Equation",
        ElementType.page_number: "PageNumber",
        ElementType.figure: "Body",
        ElementType.table: "Body",
        ElementType.running_header: "Header",
        ElementType.running_footer: "Footer",
    }

    elements_by_id = {el.id: el for el in parsed.elements}

    ordered_elements = sorted(
        parsed.elements,
        key=lambda el: (el.page, el.reading_order),
    )

    pages = []
    for page_num in sorted(parsed.source_pages):
        page_elements = [el for el in ordered_elements if el.page == page_num]
        blocks = [
            {
                "block_type": type_map.get(el.type, "Body"),
                "confidence": 1.0,
                "bbox": el.bbox,
                "text": el.text,
                "header_level": el.header_level,
                "font_size": el.font_size,
            }
            for el in page_elements
        ]
        page_text_parts = [el.text for el in page_elements if el.text]
        pages.append(
            {
                "page_num": page_num,
                "blocks": blocks,
                "text": "\n".join(page_text_parts),
            }
        )

    sections = []
    for el in ordered_elements:
        if el.type == ElementType.header and el.header_level > 0:
            sections.append(
                {
                    "title": el.text,
                    "header_level": el.header_level,
                    "page": el.page,
                    "bbox": el.bbox,
                    "element_id": el.id,
                }
            )

    caption_lookup: Dict[str, dict] = {}
    for rel in parsed.relationships:
        if rel.type == "caption_of":
            src = elements_by_id.get(rel.source)
            tgt = elements_by_id.get(rel.target)
            if src and src.type == ElementType.caption and tgt:
                caption_lookup[tgt.id] = {
                    "caption_id": src.id,
                    "caption_text": src.text,
                    "caption_bbox": src.bbox,
                    "caption_page": src.page,
                }
            elif tgt and tgt.type == ElementType.caption and src:
                caption_lookup[src.id] = {
                    "caption_id": tgt.id,
                    "caption_text": tgt.text,
                    "caption_bbox": tgt.bbox,
                    "caption_page": tgt.page,
                }

    figures = []
    for el in ordered_elements:
        if el.type == ElementType.figure:
            fig = {
                "figure_id": el.id,
                "page": el.page,
                "bbox": el.bbox,
                "text": el.text,
            }
            if el.id in caption_lookup:
                fig["caption"] = caption_lookup[el.id]
            figures.append(fig)

    running_headers = [
        {
            "element_id": el.id,
            "page": el.page,
            "bbox": el.bbox,
            "text": el.text,
            "font_size": el.font_size,
        }
        for el in ordered_elements
        if el.type == ElementType.running_header
    ]

    running_footers = [
        {
            "element_id": el.id,
            "page": el.page,
            "bbox": el.bbox,
            "text": el.text,
            "font_size": el.font_size,
        }
        for el in ordered_elements
        if el.type == ElementType.running_footer
    ]

    return {
        "pages": pages,
        "sections": sections,
        "figures": figures,
        "running_headers": running_headers,
        "running_footers": running_footers,
    }


def compile_table_truth(ir: dict) -> dict:
    parsed = WindowIR(**ir)

    tables = []
    for table in parsed.tables:
        tables.append(
            {
                "table_id": table.table_id,
                "page": table.page_start,
                "page_end": table.page_end,
                "bbox": table.bbox_per_page.get(table.page_start, [0, 0, 0, 0]),
                "caption": table.caption,
                "n_rows": table.n_rows,
                "n_cols": table.n_cols,
                "n_header_rows": table.n_header_rows,
                "cells": [cell.model_dump() for cell in table.cells],
                "is_continuation": table.continuation.is_continued,
                "style": table.style,
            }
        )

    return {"tables": tables}


def render_ir_to_pdf(ir: dict, output_path: str) -> str:
    if not REPORTLAB_AVAILABLE:
        raise ImportError("reportlab is required for PDF rendering but not installed")
    
    valid, errors = validate_ir(ir)
    if not valid:
        raise ValueError(f"Invalid IR: {errors}")

    parsed = WindowIR(**ir)
    c = canvas.Canvas(output_path, pagesize=letter)
    page_width, page_height = letter

    for page_num in parsed.source_pages:
        page_elements = sorted([el for el in parsed.elements if el.page == page_num], key=lambda el: el.reading_order)
        page_tables = [tbl for tbl in parsed.tables if tbl.page_start <= page_num <= tbl.page_end]

        for el in page_elements:
            x0, y0, x1, y1 = el.bbox
            x = float(x0)
            y_top = float(y1)

            if el.type == ElementType.header:
                c.setFont("Helvetica-Bold", max(8, float(el.font_size)))
                c.drawString(x, y_top, el.text)
            elif el.type in (ElementType.body, ElementType.list_item, ElementType.footnote):
                c.setFont("Helvetica", max(8, float(el.font_size)))
                c.drawString(x, y_top, el.text)
            elif el.type == ElementType.caption:
                c.setFont("Helvetica-Oblique", max(6, float(el.font_size) - 1))
                c.drawString(x, y_top, el.text)
            elif el.type == ElementType.running_header:
                c.setFont("Helvetica", max(8, float(el.font_size)))
                c.drawString(x, page_height - 24, el.text)
            elif el.type == ElementType.running_footer:
                c.setFont("Helvetica", max(8, float(el.font_size)))
                c.drawString(x, 18, el.text)
            elif el.type == ElementType.equation:
                c.setFont("Courier", max(8, float(el.font_size)))
                c.drawString(x, y_top, el.text or "[equation]")
            elif el.type == ElementType.page_number:
                c.setFont("Helvetica", max(8, float(el.font_size)))
                text = el.text if el.text else str(page_num)
                text_w = c.stringWidth(text, "Helvetica", max(8, float(el.font_size)))
                c.drawString((page_width - text_w) / 2.0, 18, text)
            elif el.type == ElementType.figure:
                width = max(1.0, float(x1 - x0))
                height = max(1.0, float(y1 - y0))
                c.rect(x0, y0, width, height)
                c.setFont("Helvetica-Oblique", 9)
                c.drawString(x0 + 4, y1 - 12, el.text or "Figure")

        for tbl in page_tables:
            bbox = tbl.bbox_per_page.get(page_num)
            if not bbox:
                continue

            max_row = max((cell.row for cell in tbl.cells), default=-1)
            max_col = max((cell.col for cell in tbl.cells), default=-1)
            n_rows = max(tbl.n_rows, max_row + 1)
            n_cols = max(tbl.n_cols, max_col + 1)
            data = [["" for _ in range(n_cols)] for _ in range(n_rows)]

            for cell in tbl.cells:
                if 0 <= cell.row < n_rows and 0 <= cell.col < n_cols:
                    data[cell.row][cell.col] = cell.text

            table_obj = Table(data)
            styles = []
            if tbl.style == "ruled":
                styles.append(("GRID", (0, 0), (-1, -1), 0.5, colors.black))
            elif tbl.style == "light_ruled":
                styles.append(("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.grey))

            if tbl.n_header_rows > 0:
                header_end = min(tbl.n_header_rows - 1, n_rows - 1)
                styles.extend(
                    [
                        ("FONTNAME", (0, 0), (-1, header_end), "Helvetica-Bold"),
                        ("BACKGROUND", (0, 0), (-1, header_end), colors.lightgrey),
                    ]
                )

            if styles:
                table_obj.setStyle(TableStyle(styles))

            x0, y0, x1, y1 = bbox
            avail_w = max(1.0, float(x1 - x0))
            avail_h = max(1.0, float(y1 - y0))
            _, th = table_obj.wrapOn(c, avail_w, avail_h)
            table_obj.drawOn(c, float(x0), float(y1) - th)

        c.showPage()

    c.save()
    return output_path

def _bbox_iou(a, b) -> float:
    if not a or not b or len(a) < 4 or len(b) < 4:
        return 0.0
    ax0, ay0, ax1, ay1 = [float(x) for x in a[:4]]
    bx0, by0, bx1, by1 = [float(x) for x in b[:4]]
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _text_similarity(a, b) -> float:
    return SequenceMatcher(None, str(a or ""), str(b or "")).ratio()


def _match_blocks(pred, truth) -> list[tuple]:
    candidates = []
    for pi, p in enumerate(pred or []):
        ptxt = str(p.get("text", "") if isinstance(p, dict) else "")
        for ti, t in enumerate(truth or []):
            ttxt = str(t.get("text", "") if isinstance(t, dict) else "")
            sim = _text_similarity(ptxt, ttxt)
            if sim > 0.5:
                candidates.append((sim, pi, ti))
    candidates.sort(reverse=True)
    used_p, used_t, matches = set(), set(), []
    for _, pi, ti in candidates:
        if pi in used_p or ti in used_t:
            continue
        used_p.add(pi)
        used_t.add(ti)
        matches.append((pi, ti))
    return matches


def _match_tables(pred, truth) -> list[tuple]:
    candidates = []
    for pi, p in enumerate(pred or []):
        if not isinstance(p, dict):
            continue
        p_page = p.get("page") or p.get("page_start")
        p_bbox = p.get("bbox")
        if p_bbox is None:
            bpp = p.get("bbox_per_page") or {}
            if isinstance(bpp, dict) and bpp:
                p_bbox = next(iter(bpp.values()))
        for ti, t in enumerate(truth or []):
            if not isinstance(t, dict):
                continue
            t_page = t.get("page") or t.get("page_start")
            t_bbox = t.get("bbox")
            if t_bbox is None:
                bpp = t.get("bbox_per_page") or {}
                if isinstance(bpp, dict) and bpp:
                    t_bbox = next(iter(bpp.values()))
            if p_page == t_page and _bbox_iou(p_bbox, t_bbox) > 0.5:
                candidates.append((_bbox_iou(p_bbox, t_bbox), pi, ti))
    candidates.sort(reverse=True)
    used_p, used_t, matches = set(), set(), []
    for _, pi, ti in candidates:
        if pi in used_p or ti in used_t:
            continue
        used_p.add(pi)
        used_t.add(ti)
        matches.append((pi, ti))
    return matches


def score_extraction(extraction_result, truth_document, truth_tables) -> dict:
    pred_pages = (extraction_result or {}).get("pages", []) or []
    truth_pages = (truth_document or {}).get("pages", []) or []

    pred_blocks = [b for p in pred_pages if isinstance(p, dict) for b in (p.get("blocks", []) or []) if isinstance(b, dict)]
    truth_blocks = [b for p in truth_pages if isinstance(p, dict) for b in (p.get("blocks", []) or []) if isinstance(b, dict)]

    block_matches = _match_blocks(pred_blocks, truth_blocks)
    tp = len(block_matches)
    p_n = len(pred_blocks)
    t_n = len(truth_blocks)
    precision = tp / p_n if p_n else 0.0
    recall = tp / t_n if t_n else 0.0
    block_presence_f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    type_ok = 0
    for pi, ti in block_matches:
        if str(pred_blocks[pi].get("block_type", "")).lower() == str(truth_blocks[ti].get("block_type", "")).lower():
            type_ok += 1
    block_type_accuracy = (type_ok / tp) if tp else 0.0

    pred_sections = {str(s.get("title", "")).strip().lower() for s in (extraction_result or {}).get("sections", []) or [] if isinstance(s, dict)}
    truth_sections = {str(s.get("title", "")).strip().lower() for s in (truth_document or {}).get("sections", []) or [] if isinstance(s, dict)}
    section_recall = (len(pred_sections & truth_sections) / len(truth_sections)) if truth_sections else 0.0

    # Reading order: compare block text sequences across matched pages
    # If no explicit reading_order field, derive from block array order
    truth_order = (truth_document or {}).get("reading_order", []) or []
    pred_order = (extraction_result or {}).get("reading_order", []) or []
    if not truth_order:
        # Derive from block text in page order
        truth_order = [b.get("text", "")[:50] for p in truth_pages for b in (p.get("blocks") or []) if b.get("text")]
    if not pred_order:
        pred_order = [b.get("text", "")[:50] for p in pred_pages for b in (p.get("blocks") or []) if b.get("text")]
    common = [x for x in truth_order if x in set(pred_order)]
    if len(common) < 2:
        reading_order_score = 1.0 if len(truth_order) <= 1 else 0.0
    else:
        tpos = {k: i for i, k in enumerate(truth_order)}
        ppos = {k: i for i, k in enumerate(pred_order)}
        concordant = 0
        total = 0
        for i in range(len(common)):
            for j in range(i + 1, len(common)):
                total += 1
                a, b = common[i], common[j]
                if (tpos[a] - tpos[b]) * (ppos[a] - ppos[b]) > 0:
                    concordant += 1
        reading_order_score = (concordant / total) if total else 0.0

    pred_tables = (extraction_result or {}).get("tables", []) or []
    truth_table_list = (truth_tables or {}).get("tables", truth_tables if isinstance(truth_tables, list) else []) or []
    table_matches = _match_tables(pred_tables, truth_table_list)
    ttp = len(table_matches)
    tp_n = len(pred_tables)
    tt_n = len(truth_table_list)
    tprec = ttp / tp_n if tp_n else 0.0
    trec = ttp / tt_n if tt_n else 0.0
    table_presence_f1 = (2 * tprec * trec / (tprec + trec)) if (tprec + trec) else 0.0

    cell_f1s = []
    for pi, ti in table_matches:
        pcells = {(c.get("row"), c.get("col")): c for c in (pred_tables[pi].get("cells", []) or []) if isinstance(c, dict)}
        tcells = {(c.get("row"), c.get("col")): c for c in (truth_table_list[ti].get("cells", []) or []) if isinstance(c, dict)}
        keys = set(pcells) | set(tcells)
        if not keys:
            cell_f1s.append(1.0)
            continue
        hit = 0
        for k in keys:
            if k in pcells and k in tcells:
                if _text_similarity(pcells[k].get("text", ""), tcells[k].get("text", "")) > 0.8:
                    hit += 1
        p = hit / len(pcells) if pcells else 0.0
        r = hit / len(tcells) if tcells else 0.0
        cell_f1s.append((2 * p * r / (p + r)) if (p + r) else 0.0)
    table_cell_f1 = (sum(cell_f1s) / len(cell_f1s)) if cell_f1s else 0.0

    continuity_scores = []
    for t in truth_table_list:
        if not isinstance(t, dict):
            continue
        if int(t.get("page_end", t.get("page_start", 0)) or 0) > int(t.get("page_start", 0) or 0):
            tid = t.get("table_id")
            matched_pred = None
            for pi, ti in table_matches:
                if ti < len(truth_table_list) and truth_table_list[ti].get("table_id") == tid:
                    matched_pred = pred_tables[pi]
                    break
            if matched_pred is None:
                continuity_scores.append(0.0)
            else:
                p_multi = int(matched_pred.get("page_end", matched_pred.get("page_start", 0)) or 0) > int(matched_pred.get("page_start", 0) or 0)
                continuity_scores.append(1.0 if p_multi else 0.0)
    cross_page_table_continuity = (sum(continuity_scores) / len(continuity_scores)) if continuity_scores else 1.0

    # Weighted average — skip table metrics if no tables in truth
    doc_score = (block_presence_f1 + block_type_accuracy + section_recall) / 3.0
    has_tables = len(truth_table_list) > 0
    if has_tables:
        table_score = (table_presence_f1 + table_cell_f1 + cross_page_table_continuity) / 3.0
        overall_score = 0.4 * doc_score + 0.4 * table_score + 0.2 * reading_order_score
    else:
        overall_score = 0.6 * doc_score + 0.4 * reading_order_score

    pass_thresholds = {
        "block_presence_f1": 0.85,
        "block_type_accuracy": 0.80,
        "section_recall": 0.80,
        "reading_order_score": 0.80,
        "table_presence_f1": 0.80,
        "table_cell_f1": 0.80,
        "cross_page_table_continuity": 0.80,
    }

    per_page = {}
    truth_by_page = {p.get("page") or p.get("page_num", 0): p for p in truth_pages if isinstance(p, dict)}
    pred_by_page = {p.get("page") or p.get("page_num", 0): p for p in pred_pages if isinstance(p, dict)}
    all_pages = {k for k in (set(truth_by_page) | set(pred_by_page)) if k is not None}
    for pg in sorted(all_pages):
        pb = len((pred_by_page.get(pg) or {}).get("blocks", []) or [])
        tb = len((truth_by_page.get(pg) or {}).get("blocks", []) or [])
        per_page[pg] = {"pred_blocks": pb, "truth_blocks": tb}

    result = {
        "block_presence_f1": float(block_presence_f1),
        "block_type_accuracy": float(block_type_accuracy),
        "section_recall": float(section_recall),
        "reading_order_score": float(reading_order_score),
        "table_presence_f1": float(table_presence_f1),
        "table_cell_f1": float(table_cell_f1),
        "cross_page_table_continuity": float(cross_page_table_continuity),
        "overall_score": float(overall_score),
        "pass": all(
            float({"block_presence_f1": block_presence_f1, "block_type_accuracy": block_type_accuracy,
                   "section_recall": section_recall, "reading_order_score": reading_order_score,
                   "table_presence_f1": table_presence_f1, "table_cell_f1": table_cell_f1,
                   "cross_page_table_continuity": cross_page_table_continuity}.get(k, 0)) >= v
            for k, v in pass_thresholds.items()
            if not (k.startswith("table") and not has_tables)  # skip table thresholds if no tables
        ),
        "details": {"per_page": per_page, "matches": {"blocks": tp, "tables": ttp}},
    }
    return result


# ---------------------------------------------------------------------------
# Task 4 — render_windows: extract PNGs + spans per sampled window
# ---------------------------------------------------------------------------

def render_windows(
    pdf_path: str,
    sampling_plan: dict,
    output_dir: str,
) -> list[dict]:
    """Render each window in sampling_plan to PNGs and span JSON files."""
    doc = pdf_oxide.PdfDocument(pdf_path)
    rendered: list[dict] = []

    for window in sampling_plan.get("windows", []):
        wid = str(window.get("window_id", ""))
        source_pages: list[int] = [int(p) for p in window.get("source_pages", [])]
        win_dir = os.path.join(output_dir, wid)
        os.makedirs(win_dir, exist_ok=True)

        png_paths: list[str] = []
        span_paths: list[str] = []

        for page_num in source_pages:
            page_idx = page_num - 1  # pdf_oxide uses 0-based indexing
            if page_idx < 0:
                continue

            # Render PNG
            png_path = os.path.join(win_dir, f"page_{page_num}.png")
            img_bytes = doc.render_page(page_idx, dpi=150)
            with open(png_path, "wb") as f:
                f.write(img_bytes)
            png_paths.append(png_path)

            # Extract spans
            spans = doc.extract_spans(page_idx)
            span_data = [
                {
                    "text": s.text,
                    "bbox": list(s.bbox),
                    "font_name": s.font_name,
                    "font_size": s.font_size,
                }
                for s in spans
            ]
            span_path = os.path.join(win_dir, f"spans_{page_num}.json")
            with open(span_path, "w", encoding="utf-8") as f:
                json.dump(span_data, f, indent=2)
            span_paths.append(span_path)

        # Extract window pages into a mini-PDF via pypdf
        window_pdf_path = os.path.join(win_dir, "window.pdf")
        reader = PdfReader(pdf_path)
        writer = PdfWriter()
        for page_num in source_pages:
            page_idx = page_num - 1
            if 0 <= page_idx < len(reader.pages):
                writer.add_page(reader.pages[page_idx])
        with open(window_pdf_path, "wb") as f:
            writer.write(f)

        rendered.append({
            "window_id": wid,
            "pdf_path": pdf_path,
            "window_pdf_path": window_pdf_path,
            "png_paths": png_paths,
            "span_paths": span_paths,
            "source_pages": source_pages,
        })
        logger.debug(f"Rendered {wid}: {len(png_paths)} pages")

    return rendered


# ---------------------------------------------------------------------------
# Task 6 — generate_window_ir: Gemini via scillm produces structured IR
# ---------------------------------------------------------------------------

SCILLM_URL = os.environ.get("SCILLM_URL", "http://localhost:4001")
SCILLM_KEY = os.environ.get("SCILLM_PROXY_KEY", "sk-dev-proxy-123")
_SCILLM_HEADERS = {"Authorization": f"Bearer {SCILLM_KEY}", "Content-Type": "application/json"}

_IR_PROMPT = """You are a ReportLab expert. This PDF has pages to recreate.
Write a complete Python script using ReportLab that recreates the structural layout of this page.

Requirements:
- Use reportlab.pdfgen.canvas for text positioning with drawString()
- Use reportlab.platypus.Table for any tables, with TableStyle for grid lines
- Use reportlab.lib.pagesizes.letter for page size
- Match font sizes approximately (headers larger, body ~10-12pt)
- Place headers, body paragraphs, tables, captions, footnotes, and page numbers
- For tables: include ALL cell text, mark header rows with bold/background
- Include running headers and footers if present
- Output ONLY the Python code, no explanation. The script should save to 'output.pdf'."""


import re as _re


def _extract_json(raw: str) -> dict:
    """Extract JSON object from LLM response, handling markdown fences and trailing commas."""
    text = raw.strip()
    # Strip markdown fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fix trailing commas before } or ] (common Gemini quirk)
    fixed = _re.sub(r",\s*([}\]])", r"\1", text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # Try extracting first { ... } block
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    fixed_candidate = _re.sub(r",\s*([}\]])", r"\1", candidate)
                    return json.loads(fixed_candidate)
    raise json.JSONDecodeError("No valid JSON object found in response", text, 0)


async def _call_gemini_ir(
    client: httpx.AsyncClient,
    window_info: dict,
    output_dir: str,
    model: str,
    family_id: str,
) -> dict:
    """Send PNGs + spans to Gemini and parse the IR response."""
    wid = window_info["window_id"]
    source_pages = window_info["source_pages"]
    source_pdf = window_info.get("pdf_path", "")

    prompt_text = _IR_PROMPT.format(
        window_id=wid,
        source_pages=json.dumps(source_pages),
        source_pdf=source_pdf,
        family_id=family_id,
    )

    content_parts: list[dict] = [{"type": "text", "text": prompt_text}]

    # Send source PDF binary via Gemini inlineData — Gemini processes PDFs natively
    pdf_path = window_info.get("pdf_path", "")
    if pdf_path and os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        content_parts.append({
            "inlineData": {"mimeType": "application/pdf", "data": b64},
        })

    # Add span context
    all_spans: list[dict] = []
    for span_path in window_info.get("span_paths", []):
        with open(span_path, "r", encoding="utf-8") as f:
            all_spans.extend(json.load(f))
    content_parts.append({
        "type": "text",
        "text": f"Extracted text spans:\n{json.dumps(all_spans[:500])}",
    })

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content_parts}],
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
    }

    resp = await client.post(
        f"{SCILLM_URL}/v1/chat/completions",
        headers=_SCILLM_HEADERS,
        json=payload,
        timeout=120.0,
    )
    resp.raise_for_status()
    body = resp.json()

    raw_text = body["choices"][0]["message"]["content"]
    ir_dict = _extract_json(raw_text)

    valid, errors = validate_ir(ir_dict)
    if not valid:
        # Retry once with error feedback
        retry_parts = content_parts + [
            {"type": "text", "text": f"Your previous response had validation errors: {errors}. Fix them and return valid JSON only."}
        ]
        retry_payload = {**payload, "messages": [{"role": "user", "content": retry_parts}]}
        resp2 = await client.post(
            f"{SCILLM_URL}/v1/chat/completions",
            headers=_SCILLM_HEADERS,
            json=retry_payload,
            timeout=120.0,
        )
        resp2.raise_for_status()
        raw2 = resp2.json()["choices"][0]["message"]["content"]
        ir_dict = _extract_json(raw2)
        valid, errors = validate_ir(ir_dict)
        if not valid:
            raise ValueError(f"IR validation failed after retry: {errors}")

    # Save IR
    win_dir = os.path.join(output_dir, wid)
    os.makedirs(win_dir, exist_ok=True)
    ir_path = os.path.join(win_dir, "ir.json")
    with open(ir_path, "w", encoding="utf-8") as f:
        json.dump(ir_dict, f, indent=2)

    return ir_dict


def generate_window_ir(
    window_info: dict,
    output_dir: str,
    model: str = "text-gemini",
    family_id: str = "unknown",
) -> dict:
    """Generate structured IR for a single window via Gemini."""
    return asyncio.run(_call_gemini_ir(
        httpx.AsyncClient(),
        window_info,
        output_dir,
        model,
        family_id,
    ))


# ---------------------------------------------------------------------------
# Task 7 — generate_ir_batch: parallel Gemini IR generation
# ---------------------------------------------------------------------------

async def _generate_ir_batch_async(
    rendered_windows: list[dict],
    output_dir: str,
    model: str = "text-gemini",
    family_id: str = "unknown",
    concurrency: int = 4,
) -> dict:
    """Async parallel IR generation for all windows."""
    sem = asyncio.Semaphore(concurrency)
    succeeded = 0
    failed = 0
    retried = 0
    ir_paths: dict[str, str] = {}

    async with httpx.AsyncClient() as client:
        async def _process(window: dict) -> None:
            nonlocal succeeded, failed, retried
            wid = window["window_id"]
            async with sem:
                try:
                    ir = await _call_gemini_ir(client, window, output_dir, model, family_id)
                    ir_paths[wid] = os.path.join(output_dir, wid, "ir.json")
                    succeeded += 1
                except (httpx.HTTPStatusError, httpx.RequestError, json.JSONDecodeError, ValueError, KeyError) as exc:
                    logger.warning(f"IR generation failed for {wid}: {exc}")
                    failed += 1

        await asyncio.gather(*[_process(w) for w in rendered_windows])

    return {
        "total": len(rendered_windows),
        "succeeded": succeeded,
        "failed": failed,
        "retried": retried,
        "ir_paths": ir_paths,
    }


def generate_ir_batch(
    rendered_windows: list[dict],
    output_dir: str,
    model: str = "text-gemini",
    family_id: str = "unknown",
    concurrency: int = 4,
) -> dict:
    """Generate IR for all rendered windows in parallel."""
    return asyncio.run(_generate_ir_batch_async(
        rendered_windows, output_dir, model, family_id, concurrency,
    ))


# ---------------------------------------------------------------------------
# Task 12 — evaluate_fixture: run extraction preset against synthetic fixture
# ---------------------------------------------------------------------------

def evaluate_fixture(
    manifest_path: str,
    preset_overrides: Optional[dict] = None,
) -> dict:
    """Run extraction on synthetic PDFs from manifest and score against truth."""
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    window_scores: dict[str, dict] = {}
    metric_sums: dict[str, float] = {}
    metric_counts: dict[str, int] = {}
    all_pass = True

    for win in manifest.get("windows", []):
        wid = win.get("window_id", "")
        fixture = win.get("fixture", {})
        synthetic_pdf = fixture.get("synthetic_pdf", "")
        truth_doc_path = fixture.get("truth_document", "")
        truth_tbl_path = fixture.get("truth_tables", "")

        if not os.path.exists(synthetic_pdf):
            logger.warning(f"Skipping {wid}: synthetic PDF not found at {synthetic_pdf}")
            continue
        if not os.path.exists(truth_doc_path) or not os.path.exists(truth_tbl_path):
            logger.warning(f"Skipping {wid}: truth files not found")
            continue

        # Run extraction
        doc = pdf_oxide.PdfDocument(synthetic_pdf)
        extract_kwargs = dict(
            detect_figures=True,
            detect_engineering=True,
            normalize_text=True,
            build_sections=True,
            max_pages=0,
        )
        if preset_overrides:
            for k in ("body_font_size_override", "header_ratio_override"):
                if k in preset_overrides:
                    extract_kwargs[k] = preset_overrides[k]
            for k in ("detect_figures", "detect_engineering", "normalize_text", "build_sections"):
                if k in preset_overrides:
                    extract_kwargs[k] = preset_overrides[k]

        extraction_result = doc.extract_document(**extract_kwargs)

        # Extract tables per page
        page_count = doc.page_count()
        tables: list[dict] = []
        for p in range(page_count):
            tables.extend(doc.extract_tables(p))
        extraction_result["tables"] = tables

        # Load truth
        with open(truth_doc_path, "r", encoding="utf-8") as f:
            truth_document = json.load(f)
        with open(truth_tbl_path, "r", encoding="utf-8") as f:
            truth_tables = json.load(f)

        scores = score_extraction(extraction_result, truth_document, truth_tables)
        window_scores[wid] = scores
        if not scores.get("pass", False):
            all_pass = False

        for k, v in scores.items():
            if isinstance(v, (int, float)) and k != "pass":
                metric_sums[k] = metric_sums.get(k, 0.0) + float(v)
                metric_counts[k] = metric_counts.get(k, 0) + 1

    aggregate = {
        k: metric_sums[k] / metric_counts[k]
        for k in metric_sums
        if metric_counts.get(k, 0) > 0
    }
    aggregate["pass"] = all_pass

    return {
        "manifest_path": manifest_path,
        "preset": preset_overrides or "default",
        "window_scores": window_scores,
        "aggregate": aggregate,
    }


# ---------------------------------------------------------------------------
# Task 13 — search_presets + promote_family_preset
# ---------------------------------------------------------------------------

_DEFAULT_SEARCH_SPACE = {
    "header_ratio_override": [None, 1.1, 1.15, 1.2, 1.3],
    "body_font_size_override": [None],
    "detect_figures": [True],
    "detect_engineering": [True, False],
    "normalize_text": [True],
    "build_sections": [True],
}


def search_presets(
    manifest_path: str,
    search_space: Optional[dict] = None,
) -> dict:
    """Grid search over ExtractionConfig knobs to find the best preset for a family."""
    space = search_space or _DEFAULT_SEARCH_SPACE

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    family_id = manifest.get("source_document", {}).get("family_id", "unknown")

    keys = sorted(space.keys())
    combos = list(itertools.product(*(space[k] for k in keys)))
    logger.info(f"Searching {len(combos)} preset combinations for family={family_id}")

    results: list[dict] = []
    for combo in combos:
        overrides = {}
        for k, v in zip(keys, combo):
            if v is not None:
                overrides[k] = v

        evaluation = evaluate_fixture(manifest_path, preset_overrides=overrides or None)
        agg = evaluation.get("aggregate", {})
        results.append({
            "preset": overrides or {"name": "default"},
            "scores": agg,
            "overall_score": agg.get("overall_score", 0.0),
            "pass": agg.get("pass", False),
        })

    results.sort(key=lambda r: r["overall_score"], reverse=True)

    winner = results[0] if results else None
    runner_up = results[1] if len(results) > 1 else None

    return {
        "family_id": family_id,
        "search_space_size": len(combos),
        "results": results,
        "winner": {**winner, "rank": 1} if winner else None,
        "runner_up": {**runner_up, "rank": 2} if runner_up else None,
    }


def promote_family_preset(
    search_result: dict,
    registry_path: str = "local/family_registry.json",
) -> dict:
    """Append the winning preset to the family registry."""
    winner = search_result.get("winner")
    if not winner:
        raise ValueError("No winner to promote")

    entry = {
        "family_id": search_result["family_id"],
        "preset": winner["preset"],
        "validation_scores": winner["scores"],
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "source_manifest": search_result.get("manifest_path", ""),
    }

    os.makedirs(os.path.dirname(registry_path) or ".", exist_ok=True)
    registry: list[dict] = []
    if os.path.exists(registry_path):
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)

    # Replace existing entry for same family_id
    registry = [r for r in registry if r.get("family_id") != entry["family_id"]]
    registry.append(entry)

    with open(registry_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)

    logger.info(f"Promoted preset for {entry['family_id']} to {registry_path}")
    return entry


# ---------------------------------------------------------------------------
# Task 14 — clone_pdf: full end-to-end orchestrator
# ---------------------------------------------------------------------------

def clone_pdf(
    pdf_path: str,
    output_dir: str,
    max_windows: int = 20,
    seed: int = 42,
    search_presets_flag: bool = False,
    model: str = "text-gemini",
) -> dict:
    """Full clone pipeline: profile → sample → render → IR → render_ir → truth → manifest → score."""
    os.makedirs(output_dir, exist_ok=True)
    logger.info(f"Cloning {pdf_path} → {output_dir}")

    # 1. Profile + family assignment
    profile = profile_and_assign(pdf_path)
    family_id = profile.get("family_id", "unknown")
    logger.info(f"Family: {family_id} (confidence={profile.get('confidence', 0)})")

    # Save profile
    with open(os.path.join(output_dir, "profile.json"), "w") as f:
        json.dump(profile, f, indent=2)

    # 2. Sampling plan
    sampling_plan = build_sampling_plan(pdf_path, max_windows=max_windows, seed=seed)
    with open(os.path.join(output_dir, "sampling_plan.json"), "w") as f:
        json.dump(sampling_plan, f, indent=2)
    logger.info(f"Sampled {len(sampling_plan.get('windows', []))} windows")

    # 3. Render windows (PNGs + spans)
    rendered = render_windows(pdf_path, sampling_plan, output_dir)
    logger.info(f"Rendered {len(rendered)} windows")

    # 4. Generate IR via Gemini
    ir_results = generate_ir_batch(
        rendered, output_dir, model=model, family_id=family_id,
    )
    logger.info(
        f"IR generation: {ir_results['succeeded']}/{ir_results['total']} succeeded, "
        f"{ir_results['failed']} failed"
    )

    # 5. For each successful IR: render synthetic PDF + compile truth
    windows_succeeded = 0
    for wid, ir_path in ir_results.get("ir_paths", {}).items():
        win_dir = os.path.join(output_dir, wid)
        with open(ir_path, "r", encoding="utf-8") as f:
            ir = json.load(f)

        # Render synthetic PDF
        synthetic_path = os.path.join(win_dir, "synthetic.pdf")
        try:
            render_ir_to_pdf(ir, synthetic_path)
        except (ValueError, ImportError, OSError) as exc:
            logger.warning(f"Failed to render synthetic PDF for {wid}: {exc}")
            continue

        # Compile truth
        truth_doc = compile_document_truth(ir)
        with open(os.path.join(win_dir, "truth_document.json"), "w") as f:
            json.dump(truth_doc, f, indent=2)

        truth_tbl = compile_table_truth(ir)
        with open(os.path.join(win_dir, "truth_tables.json"), "w") as f:
            json.dump(truth_tbl, f, indent=2)

        windows_succeeded += 1

    # 6. Build manifest
    manifest = build_test_manifest(profile, sampling_plan, output_dir)
    manifest_path = os.path.join(output_dir, "manifest.json")

    # 7. Evaluate
    evaluation = evaluate_fixture(manifest_path)

    # 8. Optional preset search
    search_result = None
    if search_presets_flag:
        search_result = search_presets(manifest_path)

    return {
        "pdf_path": pdf_path,
        "family_id": family_id,
        "family_confidence": profile.get("confidence", 0),
        "windows_total": len(sampling_plan.get("windows", [])),
        "windows_succeeded": windows_succeeded,
        "evaluation": evaluation.get("aggregate", {}),
        "search_result": search_result,
        "output_dir": output_dir,
        "manifest_path": manifest_path,
    }


# ---------------------------------------------------------------------------
# Task 16 — get_family_preset + confidence_tag for /learn-datalake integration
# ---------------------------------------------------------------------------

def get_family_preset(
    pdf_path: str,
    registry_path: str = "local/family_registry.json",
) -> dict:
    """Look up validated preset from registry for a PDF's family."""
    profile = profile_and_assign(pdf_path)
    family_id = profile.get("family_id", "unknown")

    if not os.path.exists(registry_path):
        return {
            "confidence": "none",
            "preset": None,
            "family_id": family_id,
            "action": "clone_required",
        }

    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    entry = next(
        (r for r in registry if r.get("family_id") == family_id),
        None,
    )

    if entry is None:
        return {
            "confidence": "none",
            "preset": None,
            "family_id": family_id,
            "action": "clone_required",
        }

    scores = entry.get("validation_scores", {})
    overall = scores.get("overall_score", 0.0)
    passes = scores.get("pass", False)

    if passes and overall >= 0.8:
        return {
            "confidence": "high",
            "preset": entry["preset"],
            "family_id": family_id,
            "validation_scores": scores,
            "action": "extract_with_preset",
        }

    return {
        "confidence": "low",
        "preset": entry["preset"],
        "family_id": family_id,
        "validation_scores": scores,
        "action": "clone_and_revalidate",
    }


def confidence_tag(
    pdf_path: str,
    registry_path: str = "local/family_registry.json",
) -> str:
    """Quick lookup returning 'high'|'low'|'none'."""
    return get_family_preset(pdf_path, registry_path)["confidence"]


@app.command("profile")
def profile(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    output_json: bool = typer.Option(False, "--json", is_flag=True, help="Output as JSON"),
) -> None:
    """Profile a PDF for cloning — wraps survey_document + profile into DocumentSignature."""
    result = profile_for_cloning(pdf_path)
    if output_json:
        print(json.dumps(result))
    else:
        typer.echo(f"doc_id:     {result['doc_id']}")
        typer.echo(f"domain:     {result['domain']}")
        typer.echo(f"pages:      {result['page_count']}")
        typer.echo(f"layout:     {result['layout_mode']}")
        typer.echo(f"has_toc:    {result['has_toc']}")
        typer.echo(f"tables:     {result['has_tables']} (density={result['table_density']:.2f})")
        typer.echo(f"figures:    {result['has_figures']} (density={result['figure_density']:.2f})")
        typer.echo(f"sections:   {result['section_count']} ({result['section_style']})")
        typer.echo(f"complexity: {result['complexity_score']}")


@app.command("family")
def family(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    output_json: bool = typer.Option(False, "--json", is_flag=True, help="Output as JSON"),
) -> None:
    """Profile and assign family based on rule-based signature matching."""
    result = profile_and_assign(pdf_path)
    if output_json:
        print(json.dumps(result))
    else:
        typer.echo(f"family: {result['family_id']} (confidence={result['confidence']})")


@app.command("render-ir")
def render_ir(
    ir_json: str = typer.Argument(..., help="Path to IR JSON file"),
    output_path: str = typer.Option("synthetic.pdf", "-o", help="Output PDF path"),
) -> None:
    with open(ir_json, "r", encoding="utf-8") as f:
        ir = json.load(f)
    typer.echo(render_ir_to_pdf(ir, output_path))


@app.command("sample")
def sample(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    max_windows: int = typer.Option(20, "--max-windows", help="Maximum windows to sample"),
    seed: int = typer.Option(42, "--seed", help="Random seed"),
    output_json: bool = typer.Option(False, "--json", is_flag=True, help="Output as JSON"),
) -> None:
    """Generate a stratified window sampling plan for a PDF."""
    result = build_sampling_plan(pdf_path, max_windows=max_windows, seed=seed)
    if output_json:
        print(json.dumps(result))
    else:
        typer.echo(f"Strategy: {result.get('strategy')}")
        typer.echo(f"Windows: {len(result.get('windows', []))}")
        for w in result.get("windows", []):
            typer.echo(f"  {w['window_id']}: pages={w['source_pages']} cat={w['category']} reason={w['selection_reason']}")


@app.command("manifest")
def manifest_cmd(
    fixture_dir: str = typer.Argument(..., help="Path to fixture directory"),
    output_json: bool = typer.Option(False, "--json", is_flag=True, help="Output as JSON"),
) -> None:
    """Build a test manifest from fixtures in a directory."""
    import os
    profile_path = os.path.join(fixture_dir, "profile.json")
    plan_path = os.path.join(fixture_dir, "sampling_plan.json")
    if os.path.exists(profile_path) and os.path.exists(plan_path):
        with open(profile_path) as f:
            prof = json.load(f)
        with open(plan_path) as f:
            plan = json.load(f)
        result = build_test_manifest(prof, plan, fixture_dir)
        if output_json:
            print(json.dumps(result))
        else:
            typer.echo(f"Manifest: {len(result.get('windows', []))} windows")
    else:
        typer.echo("Missing profile.json or sampling_plan.json in fixture dir")


@app.command("render")
def render_cmd(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    max_windows: int = typer.Option(5, "--max-windows", help="Maximum windows"),
    seed: int = typer.Option(42, "--seed", help="Random seed"),
    output_dir: str = typer.Option("/tmp/clone_render", "-o", help="Output directory"),
) -> None:
    """Render sampled windows to PNGs and span JSON files."""
    plan = build_sampling_plan(pdf_path, max_windows=max_windows, seed=seed)
    result = render_windows(pdf_path, plan, output_dir)
    for r in result:
        typer.echo(f"{r['window_id']}: {len(r['png_paths'])} PNGs, {len(r['span_paths'])} span files")


@app.command("generate-ir")
def generate_ir_cmd(
    rendered_dir: str = typer.Argument(..., help="Path to a rendered window directory"),
    output_json: bool = typer.Option(False, "--json", is_flag=True),
    model: str = typer.Option("text-gemini", "--model", help="scillm model name"),
) -> None:
    """Generate structured IR for a single rendered window via Gemini."""
    # Reconstruct window_info from directory contents
    win_dir = Path(rendered_dir)
    wid = win_dir.name
    png_paths = sorted(str(p) for p in win_dir.glob("page_*.png"))
    span_paths = sorted(str(p) for p in win_dir.glob("spans_*.json"))
    pages = [int(p.stem.split("_")[1]) for p in win_dir.glob("page_*.png")]
    window_info = {
        "window_id": wid,
        "pdf_path": "",
        "png_paths": png_paths,
        "span_paths": span_paths,
        "source_pages": sorted(pages),
    }
    ir = generate_window_ir(window_info, str(win_dir.parent), model=model)
    if output_json:
        print(json.dumps(ir))
    else:
        typer.echo(f"IR: {len(ir.get('elements', []))} elements, {len(ir.get('tables', []))} tables")


@app.command("evaluate")
def evaluate_cmd(
    manifest_path: str = typer.Argument(..., help="Path to manifest.json"),
    preset: Optional[str] = typer.Option(None, "--preset", help="JSON preset overrides"),
    output_json: bool = typer.Option(False, "--json", is_flag=True),
) -> None:
    """Evaluate extraction against synthetic fixtures from a manifest."""
    overrides = json.loads(preset) if preset else None
    result = evaluate_fixture(manifest_path, preset_overrides=overrides)
    if output_json:
        print(json.dumps(result))
    else:
        agg = result.get("aggregate", {})
        typer.echo(f"Overall: {agg.get('overall_score', 0):.3f} pass={agg.get('pass', False)}")
        for k, v in agg.items():
            if k not in ("pass", "overall_score"):
                typer.echo(f"  {k}: {v:.3f}")


@app.command("search-presets")
def search_presets_cmd(
    manifest_path: str = typer.Argument(..., help="Path to manifest.json"),
    output_json: bool = typer.Option(False, "--json", is_flag=True),
) -> None:
    """Grid search over extraction config knobs for best preset."""
    result = search_presets(manifest_path)
    if output_json:
        print(json.dumps(result))
    else:
        typer.echo(f"Family: {result['family_id']}, searched {result['search_space_size']} combos")
        w = result.get("winner")
        if w:
            typer.echo(f"Winner: score={w['overall_score']:.3f} preset={w['preset']}")


@app.command("clone")
def clone_cmd(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    output_dir: str = typer.Option("/tmp/clone_output", "-o", help="Output directory"),
    max_windows: int = typer.Option(20, "--max-windows", help="Maximum windows"),
    seed: int = typer.Option(42, "--seed", help="Random seed"),
    do_search_presets: bool = typer.Option(False, "--search-presets", is_flag=True),
    model: str = typer.Option("text-gemini", "--model", help="scillm model name"),
) -> None:
    """Full clone pipeline: profile → sample → render → IR → synthetic → score."""
    result = clone_pdf(
        pdf_path, output_dir,
        max_windows=max_windows, seed=seed,
        search_presets_flag=do_search_presets, model=model,
    )
    typer.echo(f"Family: {result['family_id']} (confidence={result['family_confidence']})")
    typer.echo(f"Windows: {result['windows_succeeded']}/{result['windows_total']}")
    agg = result.get("evaluation", {})
    typer.echo(f"Score: {agg.get('overall_score', 0):.3f} pass={agg.get('pass', False)}")


@app.command("lookup")
def lookup_cmd(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    registry: str = typer.Option("local/family_registry.json", "--registry", help="Registry path"),
    output_json: bool = typer.Option(False, "--json", is_flag=True),
) -> None:
    """Look up validated preset from registry for a PDF's family."""
    result = get_family_preset(pdf_path, registry_path=registry)
    if output_json:
        print(json.dumps(result))
    else:
        typer.echo(f"family={result['family_id']} confidence={result['confidence']} action={result['action']}")


if __name__ == "__main__":
    app()
