"""Generate PDF test fixtures with ReportLab. Manifest IS ground truth.

Creates diverse table types for benchmarking pdf_oxide extraction.
ReportLab output is oracle - we know exactly what was written.
"""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    Image,
    PageBreak,
)


@dataclass
class CellManifest:
    """Ground truth for a single cell."""
    row: int
    col: int
    qid: str
    text: str  # logical text without QID marker
    rendered_text: str  # text as written (with QID marker)
    is_merged: bool = False
    merge_span: tuple[int, int] | None = None  # (row_span, col_span)
    has_image: bool = False


@dataclass
class TableManifest:
    """Ground truth for a table."""
    table_id: str
    rows: int
    cols: int
    cells: list[CellManifest]
    preset: str
    bbox: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1
    ruled: bool = True


@dataclass
class PageManifest:
    """Ground truth for a page."""
    page_num: int
    width: float
    height: float
    tables: list[TableManifest]


@dataclass
class FixtureManifest:
    """Ground truth for the entire fixture PDF."""
    fixture_id: str
    generated_at: str
    seed: int
    pages: list[PageManifest]
    presets_used: list[str]

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "fixture_id": self.fixture_id,
            "generated_at": self.generated_at,
            "seed": self.seed,
            "presets_used": self.presets_used,
            "pages": [
                {
                    "page_num": p.page_num,
                    "width": p.width,
                    "height": p.height,
                    "tables": [
                        {
                            "table_id": t.table_id,
                            "rows": t.rows,
                            "cols": t.cols,
                            "preset": t.preset,
                            "ruled": t.ruled,
                            "cells": [
                                {
                                    "row": c.row,
                                    "col": c.col,
                                    "qid": c.qid,
                                    "text": c.text,
                                    "rendered_text": c.rendered_text,
                                    "is_merged": c.is_merged,
                                    "merge_span": c.merge_span,
                                    "has_image": c.has_image,
                                }
                                for c in t.cells
                            ],
                        }
                        for t in p.tables
                    ],
                }
                for p in self.pages
            ],
        }


class QidAllocator:
    """Deterministic QID generator for fixtures."""

    VERSION = "fixture_v1"

    def __init__(self, fixture_id: str, seed: int):
        self.fixture_id = fixture_id
        self.seed = seed
        self._assigned: dict[str, str] = {}

    def allocate(self, table_id: str, row: int, col: int) -> str:
        """Generate deterministic QID for a cell."""
        semantic_key = f"{self.VERSION}|{self.fixture_id}|{self.seed}|{table_id}|r{row}c{col}"

        if semantic_key in self._assigned:
            return self._assigned[semantic_key]

        h = hashlib.sha256(semantic_key.encode()).hexdigest()[:12]
        qid = f"QID_{h.upper()}"

        self._assigned[semantic_key] = qid
        return qid


# =============================================================================
# Table Presets - Each returns (data, style_commands, manifest_cells)
# =============================================================================


def preset_simple_grid(
    qid_alloc: QidAllocator,
    table_id: str,
    rows: int = 4,
    cols: int = 3,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Simple grid table with borders."""
    cells = []
    data = []

    for r in range(rows):
        row_data = []
        for c in range(cols):
            qid = qid_alloc.allocate(table_id, r, c)
            if r == 0:
                text = f"Header {c+1}"
            else:
                text = f"Cell R{r}C{c}"
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(
                row=r, col=c, qid=qid, text=text, rendered_text=rendered
            ))
        data.append(row_data)

    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]

    return data, style, cells


def preset_merged_cells(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Table with merged/spanning cells.

    Layout:
    +----------+----------+----------+
    | Spanning Header (3 cols)       |
    +----------+----------+----------+
    | Row span | Col 1    | Col 2    |
    | (2 rows) +----------+----------+
    |          | Col 1    | Col 2    |
    +----------+----------+----------+
    | Footer spanning 2 cols | Col 3 |
    +----------+----------+----------+
    """
    cells = []

    # Row 0: spanning header
    qid0 = qid_alloc.allocate(table_id, 0, 0)
    text0 = "Spanning Header"
    cells.append(CellManifest(
        row=0, col=0, qid=qid0, text=text0,
        rendered_text=f"[{qid0}]{text0}",
        is_merged=True, merge_span=(1, 3)
    ))

    # Row 1: row-spanning cell + normal cells
    qid10 = qid_alloc.allocate(table_id, 1, 0)
    text10 = "Row Span"
    cells.append(CellManifest(
        row=1, col=0, qid=qid10, text=text10,
        rendered_text=f"[{qid10}]{text10}",
        is_merged=True, merge_span=(2, 1)
    ))

    qid11 = qid_alloc.allocate(table_id, 1, 1)
    text11 = "R1C1"
    cells.append(CellManifest(
        row=1, col=1, qid=qid11, text=text11,
        rendered_text=f"[{qid11}]{text11}"
    ))

    qid12 = qid_alloc.allocate(table_id, 1, 2)
    text12 = "R1C2"
    cells.append(CellManifest(
        row=1, col=2, qid=qid12, text=text12,
        rendered_text=f"[{qid12}]{text12}"
    ))

    # Row 2: merged cell continues + normal cells
    qid21 = qid_alloc.allocate(table_id, 2, 1)
    text21 = "R2C1"
    cells.append(CellManifest(
        row=2, col=1, qid=qid21, text=text21,
        rendered_text=f"[{qid21}]{text21}"
    ))

    qid22 = qid_alloc.allocate(table_id, 2, 2)
    text22 = "R2C2"
    cells.append(CellManifest(
        row=2, col=2, qid=qid22, text=text22,
        rendered_text=f"[{qid22}]{text22}"
    ))

    # Row 3: footer spanning 2 cols
    qid30 = qid_alloc.allocate(table_id, 3, 0)
    text30 = "Footer Span"
    cells.append(CellManifest(
        row=3, col=0, qid=qid30, text=text30,
        rendered_text=f"[{qid30}]{text30}",
        is_merged=True, merge_span=(1, 2)
    ))

    qid32 = qid_alloc.allocate(table_id, 3, 2)
    text32 = "R3C2"
    cells.append(CellManifest(
        row=3, col=2, qid=qid32, text=text32,
        rendered_text=f"[{qid32}]{text32}"
    ))

    # Build data array (None for cells covered by merge)
    data = [
        [f"[{qid0}]{text0}", None, None],
        [f"[{qid10}]{text10}", f"[{qid11}]{text11}", f"[{qid12}]{text12}"],
        [None, f"[{qid21}]{text21}", f"[{qid22}]{text22}"],
        [f"[{qid30}]{text30}", None, f"[{qid32}]{text32}"],
    ]

    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('SPAN', (0, 0), (2, 0)),  # Header spans 3 cols
        ('SPAN', (0, 1), (0, 2)),  # Row span 2 rows
        ('SPAN', (0, 3), (1, 3)),  # Footer spans 2 cols
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
    ]

    return data, style, cells


def preset_borderless(
    qid_alloc: QidAllocator,
    table_id: str,
    rows: int = 3,
    cols: int = 4,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Borderless table (no grid lines)."""
    cells = []
    data = []

    for r in range(rows):
        row_data = []
        for c in range(cols):
            qid = qid_alloc.allocate(table_id, r, c)
            text = f"B{r}{c}"
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(
                row=r, col=c, qid=qid, text=text, rendered_text=rendered
            ))
        data.append(row_data)

    # No grid, just padding
    style = [
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]

    return data, style, cells


def preset_alternating_rows(
    qid_alloc: QidAllocator,
    table_id: str,
    rows: int = 6,
    cols: int = 3,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Table with alternating row backgrounds."""
    cells = []
    data = []

    for r in range(rows):
        row_data = []
        for c in range(cols):
            qid = qid_alloc.allocate(table_id, r, c)
            if r == 0:
                text = f"Col {c+1}"
            else:
                text = f"Value {r}-{c}"
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(
                row=r, col=c, qid=qid, text=text, rendered_text=rendered
            ))
        data.append(row_data)

    style = [
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.Color(0.95, 0.95, 0.95)]),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]

    return data, style, cells


def preset_nested_table(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Table with a nested table inside a cell."""
    cells = []

    # Inner table
    inner_data = [["A", "B"], ["1", "2"]]
    inner_style = TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.red),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ])
    inner_table = Table(inner_data, colWidths=[30, 30])
    inner_table.setStyle(inner_style)

    # Outer table cells
    qid00 = qid_alloc.allocate(table_id, 0, 0)
    text00 = "Header 1"
    cells.append(CellManifest(
        row=0, col=0, qid=qid00, text=text00,
        rendered_text=f"[{qid00}]{text00}"
    ))

    qid01 = qid_alloc.allocate(table_id, 0, 1)
    text01 = "Header 2"
    cells.append(CellManifest(
        row=0, col=1, qid=qid01, text=text01,
        rendered_text=f"[{qid01}]{text01}"
    ))

    qid10 = qid_alloc.allocate(table_id, 1, 0)
    text10 = "Contains nested"
    cells.append(CellManifest(
        row=1, col=0, qid=qid10, text=text10,
        rendered_text=f"[{qid10}]{text10}"
    ))

    # Cell with nested table - QID in text before table
    qid11 = qid_alloc.allocate(table_id, 1, 1)
    text11 = "[Nested Table]"
    cells.append(CellManifest(
        row=1, col=1, qid=qid11, text=text11,
        rendered_text=f"[{qid11}]{text11}"
    ))

    data = [
        [f"[{qid00}]{text00}", f"[{qid01}]{text01}"],
        [f"[{qid10}]{text10}", inner_table],  # nested table as cell content
    ]

    style = [
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
    ]

    return data, style, cells


def preset_with_paragraphs(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Table with Paragraph flowables (rich text) in cells."""
    styles = getSampleStyleSheet()
    cells = []

    # Row 0: Headers
    qid00 = qid_alloc.allocate(table_id, 0, 0)
    text00 = "Description"
    cells.append(CellManifest(
        row=0, col=0, qid=qid00, text=text00,
        rendered_text=f"[{qid00}]{text00}"
    ))

    qid01 = qid_alloc.allocate(table_id, 0, 1)
    text01 = "Details"
    cells.append(CellManifest(
        row=0, col=1, qid=qid01, text=text01,
        rendered_text=f"[{qid01}]{text01}"
    ))

    # Row 1: Paragraph with formatting
    qid10 = qid_alloc.allocate(table_id, 1, 0)
    text10 = "Item One"
    para10 = Paragraph(f"[{qid10}]<b>{text10}</b>", styles['Normal'])
    cells.append(CellManifest(
        row=1, col=0, qid=qid10, text=text10,
        rendered_text=f"[{qid10}]{text10}"
    ))

    qid11 = qid_alloc.allocate(table_id, 1, 1)
    text11 = "This is a longer description that demonstrates text wrapping within a table cell."
    para11 = Paragraph(f"[{qid11}]{text11}", styles['Normal'])
    cells.append(CellManifest(
        row=1, col=1, qid=qid11, text=text11,
        rendered_text=f"[{qid11}]{text11}"
    ))

    # Row 2: Another paragraph row
    qid20 = qid_alloc.allocate(table_id, 2, 0)
    text20 = "Item Two"
    para20 = Paragraph(f"[{qid20}]<i>{text20}</i>", styles['Normal'])
    cells.append(CellManifest(
        row=2, col=0, qid=qid20, text=text20,
        rendered_text=f"[{qid20}]{text20}"
    ))

    qid21 = qid_alloc.allocate(table_id, 2, 1)
    text21 = "Short note."
    para21 = Paragraph(f"[{qid21}]{text21}", styles['Normal'])
    cells.append(CellManifest(
        row=2, col=1, qid=qid21, text=text21,
        rendered_text=f"[{qid21}]{text21}"
    ))

    data = [
        [f"[{qid00}]{text00}", f"[{qid01}]{text01}"],
        [para10, para11],
        [para20, para21],
    ]

    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lavender),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]

    return data, style, cells


def preset_numeric_data(
    qid_alloc: QidAllocator,
    table_id: str,
    rows: int = 5,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Table with numeric data (financial report style)."""
    cells = []
    headers = ["Item", "Q1", "Q2", "Q3", "Total"]

    data = []
    # Header row
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(
            row=0, col=c, qid=qid, text=h, rendered_text=rendered
        ))
    data.append(header_row)

    # Data rows
    import random
    rng = random.Random(42)
    items = ["Revenue", "Expenses", "Profit", "Tax"]
    for r, item in enumerate(items, start=1):
        row_data = []
        qid0 = qid_alloc.allocate(table_id, r, 0)
        rendered0 = f"[{qid0}]{item}"
        row_data.append(rendered0)
        cells.append(CellManifest(
            row=r, col=0, qid=qid0, text=item, rendered_text=rendered0
        ))

        total = 0
        for c in range(1, 4):  # Q1, Q2, Q3
            val = rng.randint(1000, 9999)
            total += val
            qid = qid_alloc.allocate(table_id, r, c)
            text = f"${val:,}"
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(
                row=r, col=c, qid=qid, text=text, rendered_text=rendered
            ))

        # Total column
        qid_total = qid_alloc.allocate(table_id, r, 4)
        text_total = f"${total:,}"
        rendered_total = f"[{qid_total}]{text_total}"
        row_data.append(rendered_total)
        cells.append(CellManifest(
            row=r, col=4, qid=qid_total, text=text_total, rendered_text=rendered_total
        ))
        data.append(row_data)

    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),  # First col bold
        ('FONTNAME', (-1, 0), (-1, -1), 'Helvetica-Bold'),  # Last col bold
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.2, 0.2, 0.4)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BACKGROUND', (-1, 1), (-1, -1), colors.Color(0.9, 0.95, 0.9)),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),  # Numbers right-aligned
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),  # Labels left-aligned
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]

    return data, style, cells


# =============================================================================
# Advanced Presets
# =============================================================================


def preset_complex_merge(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Complex multi-level header with row and column spans.

    Layout (6 cols x 5 rows):
    +------------------+------------------+------------------+
    |    Main Title    |    Category A    |    Category B    |
    |    (span 2x1)    +--------+---------+--------+---------+
    |                  | Sub A1 | Sub A2  | Sub B1 | Sub B2  |
    +------------------+--------+---------+--------+---------+
    | Row Group 1      |   v1   |   v2    |   v3   |   v4    |
    | (span 1x2)       +--------+---------+--------+---------+
    |                  |   v5   |   v6    |   v7   |   v8    |
    +------------------+--------+---------+--------+---------+
    | Single Row       |   v9   |  v10    |  v11   |  v12    |
    +------------------+--------+---------+--------+---------+
    """
    cells = []

    # Row 0: Main Title spans 2 rows, Category A and B span 2 cols each
    qid00 = qid_alloc.allocate(table_id, 0, 0)
    text00 = "Main Title"
    cells.append(CellManifest(
        row=0, col=0, qid=qid00, text=text00,
        rendered_text=f"[{qid00}]{text00}",
        is_merged=True, merge_span=(2, 1)
    ))

    qid01 = qid_alloc.allocate(table_id, 0, 1)
    text01 = "Category A"
    cells.append(CellManifest(
        row=0, col=1, qid=qid01, text=text01,
        rendered_text=f"[{qid01}]{text01}",
        is_merged=True, merge_span=(1, 2)
    ))

    qid03 = qid_alloc.allocate(table_id, 0, 3)
    text03 = "Category B"
    cells.append(CellManifest(
        row=0, col=3, qid=qid03, text=text03,
        rendered_text=f"[{qid03}]{text03}",
        is_merged=True, merge_span=(1, 2)
    ))

    # Row 1: Sub-headers
    sub_headers = ["Sub A1", "Sub A2", "Sub B1", "Sub B2"]
    for c, sh in enumerate(sub_headers, start=1):
        qid = qid_alloc.allocate(table_id, 1, c)
        cells.append(CellManifest(
            row=1, col=c, qid=qid, text=sh,
            rendered_text=f"[{qid}]{sh}"
        ))

    # Row 2-3: Row Group spans 2 rows
    qid20 = qid_alloc.allocate(table_id, 2, 0)
    text20 = "Row Group 1"
    cells.append(CellManifest(
        row=2, col=0, qid=qid20, text=text20,
        rendered_text=f"[{qid20}]{text20}",
        is_merged=True, merge_span=(2, 1)
    ))

    # Values for rows 2-3
    for r in range(2, 4):
        for c in range(1, 5):
            qid = qid_alloc.allocate(table_id, r, c)
            text = f"V{(r-2)*4 + c}"
            cells.append(CellManifest(
                row=r, col=c, qid=qid, text=text,
                rendered_text=f"[{qid}]{text}"
            ))

    # Row 4: Single row
    qid40 = qid_alloc.allocate(table_id, 4, 0)
    text40 = "Single Row"
    cells.append(CellManifest(
        row=4, col=0, qid=qid40, text=text40,
        rendered_text=f"[{qid40}]{text40}"
    ))
    for c in range(1, 5):
        qid = qid_alloc.allocate(table_id, 4, c)
        text = f"V{8 + c}"
        cells.append(CellManifest(
            row=4, col=c, qid=qid, text=text,
            rendered_text=f"[{qid}]{text}"
        ))

    # Build data array
    data = [
        [f"[{qid00}]{text00}", f"[{qid01}]{text01}", None, f"[{qid03}]{text03}", None],
        [None] + [f"[{c.qid}]{c.text}" for c in cells if c.row == 1],
        [f"[{qid20}]{text20}"] + [f"[{c.qid}]{c.text}" for c in cells if c.row == 2 and c.col > 0],
        [None] + [f"[{c.qid}]{c.text}" for c in cells if c.row == 3 and c.col > 0],
        [f"[{qid40}]{text40}"] + [f"[{c.qid}]{c.text}" for c in cells if c.row == 4 and c.col > 0],
    ]

    style = [
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ('BOX', (0, 0), (-1, -1), 2, colors.black),
        ('SPAN', (0, 0), (0, 1)),   # Main Title spans 2 rows
        ('SPAN', (1, 0), (2, 0)),   # Category A spans 2 cols
        ('SPAN', (3, 0), (4, 0)),   # Category B spans 2 cols
        ('SPAN', (0, 2), (0, 3)),   # Row Group spans 2 rows
        ('BACKGROUND', (0, 0), (-1, 1), colors.Color(0.2, 0.3, 0.5)),
        ('TEXTCOLOR', (0, 0), (-1, 1), colors.white),
        ('BACKGROUND', (0, 2), (0, 3), colors.Color(0.3, 0.4, 0.6)),
        ('TEXTCOLOR', (0, 2), (0, 3), colors.white),
        ('FONTNAME', (0, 0), (-1, 1), 'Helvetica-Bold'),
        ('FONTNAME', (0, 2), (0, -1), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]

    return data, style, cells


def preset_long_table(
    qid_alloc: QidAllocator,
    table_id: str,
    rows: int = 25,
    cols: int = 4,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Long table that may require scrolling or page breaks."""
    cells = []
    data = []
    headers = ["ID", "Name", "Status", "Value"]

    # Header row
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(
            row=0, col=c, qid=qid, text=h, rendered_text=rendered
        ))
    data.append(header_row)

    # Data rows
    statuses = ["Active", "Pending", "Complete", "Failed", "Review"]
    import random
    rng = random.Random(42)

    for r in range(1, rows):
        row_data = []
        # ID
        qid0 = qid_alloc.allocate(table_id, r, 0)
        text0 = f"ID-{r:03d}"
        row_data.append(f"[{qid0}]{text0}")
        cells.append(CellManifest(row=r, col=0, qid=qid0, text=text0, rendered_text=f"[{qid0}]{text0}"))

        # Name
        qid1 = qid_alloc.allocate(table_id, r, 1)
        text1 = f"Item {r}"
        row_data.append(f"[{qid1}]{text1}")
        cells.append(CellManifest(row=r, col=1, qid=qid1, text=text1, rendered_text=f"[{qid1}]{text1}"))

        # Status
        qid2 = qid_alloc.allocate(table_id, r, 2)
        text2 = statuses[r % len(statuses)]
        row_data.append(f"[{qid2}]{text2}")
        cells.append(CellManifest(row=r, col=2, qid=qid2, text=text2, rendered_text=f"[{qid2}]{text2}"))

        # Value
        qid3 = qid_alloc.allocate(table_id, r, 3)
        text3 = f"${rng.randint(100, 9999):,}"
        row_data.append(f"[{qid3}]{text3}")
        cells.append(CellManifest(row=r, col=3, qid=qid3, text=text3, rendered_text=f"[{qid3}]{text3}"))

        data.append(row_data)

    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.15, 0.15, 0.3)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.Color(0.95, 0.95, 0.98)]),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('ALIGN', (2, 0), (2, -1), 'CENTER'),
        ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]

    return data, style, cells


def preset_with_image(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Table with placeholder image cells (SVG rectangles)."""
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.graphics import renderPDF

    cells = []

    def make_placeholder(width: int, height: int, label: str) -> Drawing:
        """Create a simple placeholder drawing."""
        d = Drawing(width, height)
        d.add(Rect(0, 0, width, height, fillColor=colors.Color(0.9, 0.9, 0.95), strokeColor=colors.grey))
        d.add(String(width/2, height/2, label, textAnchor='middle', fontSize=8, fillColor=colors.grey))
        return d

    # Row 0: Headers
    headers = ["Item", "Image", "Description"]
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(
            row=0, col=c, qid=qid, text=h, rendered_text=rendered
        ))

    # Rows 1-3: Data with placeholder images
    data_rows = []
    items = [("Product A", "Widget for task X"), ("Product B", "Gadget for task Y"), ("Product C", "Tool for task Z")]
    for r, (name, desc) in enumerate(items, start=1):
        row_data = []

        # Name
        qid0 = qid_alloc.allocate(table_id, r, 0)
        row_data.append(f"[{qid0}]{name}")
        cells.append(CellManifest(row=r, col=0, qid=qid0, text=name, rendered_text=f"[{qid0}]{name}"))

        # Image placeholder
        qid1 = qid_alloc.allocate(table_id, r, 1)
        placeholder = make_placeholder(60, 40, f"IMG-{r}")
        row_data.append(placeholder)
        cells.append(CellManifest(
            row=r, col=1, qid=qid1, text=f"[IMG-{r}]",
            rendered_text=f"[{qid1}][IMG-{r}]",
            has_image=True
        ))

        # Description
        qid2 = qid_alloc.allocate(table_id, r, 2)
        row_data.append(f"[{qid2}]{desc}")
        cells.append(CellManifest(row=r, col=2, qid=qid2, text=desc, rendered_text=f"[{qid2}]{desc}"))

        data_rows.append(row_data)

    data = [header_row] + data_rows

    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]

    return data, style, cells


def preset_mixed_alignment(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Table with mixed alignments per column."""
    cells = []
    data = []

    headers = ["Left Align", "Center", "Right Align", "Justify"]
    contents = [
        ["Short", "Med text", "Long content here", "This text should be justified across the cell width"],
        ["A", "BB", "CCC", "DDDD is longer"],
        ["Item 1", "Item 2", "Item 3", "Item 4 with more text"],
    ]

    # Header row
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(row=0, col=c, qid=qid, text=h, rendered_text=rendered))
    data.append(header_row)

    # Data rows
    for r, row_content in enumerate(contents, start=1):
        row_data = []
        for c, text in enumerate(row_content):
            qid = qid_alloc.allocate(table_id, r, c)
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.8, 0.8, 0.9)),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'CENTER'),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('ALIGN', (3, 0), (3, -1), 'LEFT'),  # JUSTIFY not always supported
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]

    return data, style, cells


def preset_colored_cells(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Table with individual cell backgrounds (heatmap style)."""
    cells = []
    data = []

    # Header
    headers = ["Metric", "Jan", "Feb", "Mar", "Apr"]
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(row=0, col=c, qid=qid, text=h, rendered_text=rendered))
    data.append(header_row)

    # Data with color-coded values
    metrics = ["Sales", "Returns", "Profit"]
    import random
    rng = random.Random(42)

    style_cmds = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.3, 0.3, 0.4)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]

    for r, metric in enumerate(metrics, start=1):
        row_data = []
        qid0 = qid_alloc.allocate(table_id, r, 0)
        row_data.append(f"[{qid0}]{metric}")
        cells.append(CellManifest(row=r, col=0, qid=qid0, text=metric, rendered_text=f"[{qid0}]{metric}"))

        for c in range(1, 5):
            val = rng.randint(10, 100)
            qid = qid_alloc.allocate(table_id, r, c)
            text = str(val)
            row_data.append(f"[{qid}]{text}")
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=f"[{qid}]{text}"))

            # Color based on value (green high, red low)
            intensity = val / 100.0
            if metric == "Returns":
                # Returns: red is bad (high)
                bg_color = colors.Color(1.0, 1.0 - intensity * 0.5, 1.0 - intensity * 0.5)
            else:
                # Sales/Profit: green is good (high)
                bg_color = colors.Color(1.0 - intensity * 0.5, 1.0, 1.0 - intensity * 0.5)
            style_cmds.append(('BACKGROUND', (c, r), (c, r), bg_color))

        data.append(row_data)

    return data, style_cmds, cells


def preset_dense_data(
    qid_alloc: QidAllocator,
    table_id: str,
    rows: int = 8,
    cols: int = 10,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Dense table with many small cells (matrix style)."""
    cells = []
    data = []

    # Generate dense numeric matrix
    import random
    rng = random.Random(42)

    for r in range(rows):
        row_data = []
        for c in range(cols):
            qid = qid_alloc.allocate(table_id, r, c)
            if r == 0:
                text = f"C{c}"  # Column headers
            elif c == 0:
                text = f"R{r}"  # Row headers
            else:
                text = str(rng.randint(0, 99))
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

    style = [
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.85, 0.85, 0.9)),
        ('BACKGROUND', (0, 0), (0, -1), colors.Color(0.85, 0.85, 0.9)),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 7),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]

    return data, style, cells


# =============================================================================
# Engineering / Defense Document Presets
# =============================================================================


def preset_requirements_matrix(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Requirements traceability matrix (NIST/CMMC style).

    Columns: Req ID | Description | Status | Evidence | Notes
    """
    cells = []
    data = []

    headers = ["Req ID", "Requirement Description", "Status", "Evidence Ref", "Notes"]
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(row=0, col=c, qid=qid, text=h, rendered_text=rendered))
    data.append(header_row)

    # Realistic requirements
    requirements = [
        ("AC-1", "Access Control Policy and Procedures", "Implemented", "SSP-3.1", "Annual review"),
        ("AC-2", "Account Management", "Partial", "SSP-3.2.1", "Pending automation"),
        ("AC-3", "Access Enforcement", "Implemented", "SSP-3.3", "Role-based"),
        ("AC-4", "Information Flow Enforcement", "Not Implemented", "-", "Phase 2"),
        ("AC-5", "Separation of Duties", "Implemented", "SSP-3.5", "Verified Q1"),
        ("AC-6", "Least Privilege", "Partial", "SSP-3.6", "In progress"),
        ("AC-7", "Unsuccessful Logon Attempts", "Implemented", "SSP-3.7", "Threshold: 3"),
        ("AC-8", "System Use Notification", "Implemented", "SSP-3.8", "Banner active"),
    ]

    for r, (req_id, desc, status, evidence, notes) in enumerate(requirements, start=1):
        row_data = []
        texts = [req_id, desc, status, evidence, notes]
        for c, text in enumerate(texts):
            qid = qid_alloc.allocate(table_id, r, c)
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('BOX', (0, 0), (-1, -1), 1.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (0, -1), 'Courier'),  # Req IDs in mono
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.2, 0.25, 0.35)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('ALIGN', (2, 0), (2, -1), 'CENTER'),
        ('ALIGN', (3, 0), (3, -1), 'CENTER'),
        ('ALIGN', (4, 0), (4, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]

    return data, style, cells


def preset_spec_table(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Technical specification table (engineering datasheet style).

    Multi-row headers with parameter/min/typ/max/unit columns.
    """
    cells = []

    # Build complex header
    # Row 0: "Electrical Characteristics" spanning all cols
    qid00 = qid_alloc.allocate(table_id, 0, 0)
    text00 = "Electrical Characteristics (TA = 25°C)"
    cells.append(CellManifest(
        row=0, col=0, qid=qid00, text=text00,
        rendered_text=f"[{qid00}]{text00}",
        is_merged=True, merge_span=(1, 5)
    ))

    # Row 1: Sub-headers
    sub_headers = ["Parameter", "Min", "Typ", "Max", "Unit"]
    for c, h in enumerate(sub_headers):
        qid = qid_alloc.allocate(table_id, 1, c)
        cells.append(CellManifest(row=1, col=c, qid=qid, text=h, rendered_text=f"[{qid}]{h}"))

    # Data rows
    specs = [
        ("Supply Voltage (VDD)", "3.0", "3.3", "3.6", "V"),
        ("Supply Current (IDD)", "-", "15", "25", "mA"),
        ("Input Voltage High (VIH)", "2.0", "-", "VDD", "V"),
        ("Input Voltage Low (VIL)", "0", "-", "0.8", "V"),
        ("Output Voltage High (VOH)", "2.4", "-", "-", "V"),
        ("Output Voltage Low (VOL)", "-", "-", "0.4", "V"),
        ("Clock Frequency (fCLK)", "1", "-", "20", "MHz"),
        ("Rise Time (tR)", "-", "10", "20", "ns"),
    ]

    for r, (param, vmin, typ, vmax, unit) in enumerate(specs, start=2):
        vals = [param, vmin, typ, vmax, unit]
        for c, text in enumerate(vals):
            qid = qid_alloc.allocate(table_id, r, c)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=f"[{qid}]{text}"))

    # Build data array
    data = [
        [f"[{qid00}]{text00}", None, None, None, None],
        [f"[{c.qid}]{c.text}" for c in cells if c.row == 1],
    ]
    for r in range(2, 2 + len(specs)):
        data.append([f"[{c.qid}]{c.text}" for c in cells if c.row == r])

    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('SPAN', (0, 0), (4, 0)),  # Title spans all
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.15, 0.2, 0.35)),
        ('BACKGROUND', (0, 1), (-1, 1), colors.Color(0.8, 0.82, 0.88)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 1), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 2), (-1, -1), [colors.white, colors.Color(0.95, 0.95, 0.97)]),
    ]

    return data, style, cells


def preset_compliance_matrix(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Compliance cross-reference matrix (standard vs standard mapping)."""
    cells = []

    # Headers: blank corner + standards
    standards = ["ISO 27001", "NIST 800-53", "CMMC L2", "SOC 2"]
    header_row = ["Control Family"]
    for s in standards:
        header_row.append(s)

    data = []
    row_data = []
    for c, h in enumerate(header_row):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        row_data.append(rendered)
        cells.append(CellManifest(row=0, col=c, qid=qid, text=h, rendered_text=rendered))
    data.append(row_data)

    # Control families with checkmarks/refs
    families = [
        ("Access Control", "A.9", "AC", "AC.L2-3.1.1", "CC6.1"),
        ("Audit & Accountability", "A.12.4", "AU", "AU.L2-3.3.1", "CC7.2"),
        ("Configuration Management", "A.12.5", "CM", "CM.L2-3.4.1", "CC6.6"),
        ("Identification & Auth", "A.9.4", "IA", "IA.L2-3.5.1", "CC6.1"),
        ("Incident Response", "A.16", "IR", "IR.L2-3.6.1", "CC7.3"),
        ("Maintenance", "A.11.2", "MA", "MA.L2-3.7.1", "-"),
        ("Physical Protection", "A.11.1", "PE", "PE.L2-3.10.1", "CC6.4"),
        ("Risk Assessment", "A.8.2", "RA", "RA.L2-3.11.1", "CC3.2"),
    ]

    for r, (family, iso, nist, cmmc, soc) in enumerate(families, start=1):
        row_data = []
        vals = [family, iso, nist, cmmc, soc]
        for c, text in enumerate(vals):
            qid = qid_alloc.allocate(table_id, r, c)
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('BOX', (0, 0), (-1, -1), 1, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.45)),
        ('BACKGROUND', (0, 1), (0, -1), colors.Color(0.9, 0.9, 0.92)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
    ]

    return data, style, cells


# =============================================================================
# ArXiv / Academic Paper Presets
# =============================================================================


def preset_results_table(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """ML results table with metrics ± std (arXiv style).

    Method | Accuracy | Precision | Recall | F1 Score
    """
    cells = []
    data = []

    headers = ["Method", "Accuracy", "Precision", "Recall", "F1 Score"]
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(row=0, col=c, qid=qid, text=h, rendered_text=rendered))
    data.append(header_row)

    # Results with ± notation
    results = [
        ("Baseline (CNN)", "78.2 ± 1.3", "76.8 ± 1.5", "79.1 ± 1.2", "77.9 ± 1.1"),
        ("ResNet-50", "84.5 ± 0.8", "83.2 ± 0.9", "85.1 ± 0.7", "84.1 ± 0.6"),
        ("ViT-B/16", "87.3 ± 0.6", "86.4 ± 0.7", "87.8 ± 0.5", "87.1 ± 0.5"),
        ("Ours (w/o aug)", "89.1 ± 0.5", "88.2 ± 0.6", "89.5 ± 0.4", "88.8 ± 0.4"),
        ("Ours (full)", "91.2 ± 0.4", "90.5 ± 0.5", "91.8 ± 0.3", "91.1 ± 0.3"),
    ]

    for r, row_vals in enumerate(results, start=1):
        row_data = []
        for c, text in enumerate(row_vals):
            qid = qid_alloc.allocate(table_id, r, c)
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

    style = [
        ('LINEABOVE', (0, 0), (-1, 0), 1.5, colors.black),
        ('LINEBELOW', (0, 0), (-1, 0), 0.75, colors.black),
        ('LINEBELOW', (0, -1), (-1, -1), 1.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Oblique'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        # Bold the best result
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
    ]

    return data, style, cells


def preset_comparison_matrix(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Model comparison matrix (checkmarks and crosses style)."""
    cells = []
    data = []

    # Header with features
    features = ["Feature", "GPT-4", "Claude 3", "Gemini", "Llama 3", "Ours"]
    header_row = []
    for c, h in enumerate(features):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(row=0, col=c, qid=qid, text=h, rendered_text=rendered))
    data.append(header_row)

    # Feature comparisons (using text symbols)
    comparisons = [
        ("Multimodal Input", "Yes", "Yes", "Yes", "No", "Yes"),
        ("Tool Use", "Yes", "Yes", "Yes", "No", "Yes"),
        ("Code Generation", "Yes", "Yes", "Yes", "Yes", "Yes"),
        ("Long Context (>100K)", "Yes", "Yes", "Yes", "No", "Yes"),
        ("Fine-tuning API", "Yes", "No", "No", "Yes", "Yes"),
        ("On-premise Deploy", "No", "No", "No", "Yes", "Yes"),
        ("Open Weights", "No", "No", "No", "Yes", "Yes"),
    ]

    for r, row_vals in enumerate(comparisons, start=1):
        row_data = []
        for c, text in enumerate(row_vals):
            qid = qid_alloc.allocate(table_id, r, c)
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

    style = [
        ('LINEABOVE', (0, 0), (-1, 0), 1, colors.black),
        ('LINEBELOW', (0, 0), (-1, 0), 0.5, colors.black),
        ('LINEBELOW', (0, -1), (-1, -1), 1, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        # Highlight "Ours" column
        ('BACKGROUND', (-1, 0), (-1, -1), colors.Color(0.9, 0.95, 0.9)),
    ]

    return data, style, cells


def preset_ablation_study(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Ablation study table (arXiv ML paper style)."""
    cells = []
    data = []

    headers = ["Configuration", "BLEU", "ROUGE-L", "BERTScore", "Params (M)"]
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(row=0, col=c, qid=qid, text=h, rendered_text=rendered))
    data.append(header_row)

    ablations = [
        ("Full Model", "42.8", "45.2", "0.892", "350"),
        ("  - w/o Attention", "38.4", "41.1", "0.856", "320"),
        ("  - w/o Pretrain", "35.2", "38.4", "0.834", "350"),
        ("  - w/o Dropout", "41.2", "43.8", "0.878", "350"),
        ("  - Small (1/2)", "39.6", "42.3", "0.868", "175"),
        ("  - Tiny (1/4)", "34.8", "37.9", "0.823", "88"),
    ]

    for r, row_vals in enumerate(ablations, start=1):
        row_data = []
        for c, text in enumerate(row_vals):
            qid = qid_alloc.allocate(table_id, r, c)
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

    style = [
        ('LINEABOVE', (0, 0), (-1, 0), 1.5, colors.black),
        ('LINEBELOW', (0, 0), (-1, 0), 0.75, colors.black),
        ('LINEBELOW', (0, 1), (-1, 1), 0.5, colors.grey),  # Below full model
        ('LINEBELOW', (0, -1), (-1, -1), 1.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (0, 1), 'Helvetica-Bold'),  # Full model bold
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
    ]

    return data, style, cells


def preset_dataset_stats(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Dataset statistics table (ML paper style)."""
    cells = []
    data = []

    headers = ["Dataset", "Train", "Val", "Test", "Classes", "Avg Length"]
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(row=0, col=c, qid=qid, text=h, rendered_text=rendered))
    data.append(header_row)

    datasets = [
        ("IMDB", "25,000", "5,000", "25,000", "2", "231.4"),
        ("SST-2", "67,349", "872", "1,821", "2", "19.3"),
        ("AG News", "120,000", "-", "7,600", "4", "43.2"),
        ("Yelp Full", "650,000", "-", "50,000", "5", "155.8"),
        ("DBpedia", "560,000", "-", "70,000", "14", "54.6"),
        ("Yahoo Answers", "1,400,000", "-", "60,000", "10", "108.4"),
    ]

    for r, row_vals in enumerate(datasets, start=1):
        row_data = []
        for c, text in enumerate(row_vals):
            qid = qid_alloc.allocate(table_id, r, c)
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

    style = [
        ('LINEABOVE', (0, 0), (-1, 0), 1.5, colors.black),
        ('LINEBELOW', (0, 0), (-1, 0), 0.75, colors.black),
        ('LINEBELOW', (0, -1), (-1, -1), 1.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (0, -1), 'Courier'),  # Dataset names in mono
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
    ]

    return data, style, cells


# =============================================================================
# Professional Document Presets (from reportlab examples)
# =============================================================================


def preset_timesheet(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Timesheet table with total row spanning (from jurasec example).

    Uses LINEABOVE pattern instead of GRID for professional look.
    """
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    cells = []
    data = []

    # Custom colors (similar to original example)
    header_bg = colors.Color(50/255, 140/255, 140/255)
    total_bg = colors.Color(122/255, 180/255, 225/255)

    headers = ["No.", "Date", "Start Time", "End Time", "Duration"]
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(row=0, col=c, qid=qid, text=h, rendered_text=rendered))
    data.append(header_row)

    # Timesheet entries
    entries = [
        ("1", "Monday, January 15, 2024", "09:00", "17:30", "8:30"),
        ("2", "Tuesday, January 16, 2024", "08:45", "18:00", "9:15"),
        ("3", "Wednesday, January 17, 2024", "09:15", "17:45", "8:30"),
        ("4", "Thursday, January 18, 2024", "09:00", "18:30", "9:30"),
        ("5", "Friday, January 19, 2024", "08:30", "16:00", "7:30"),
    ]

    for r, row_vals in enumerate(entries, start=1):
        row_data = []
        for c, text in enumerate(row_vals):
            qid = qid_alloc.allocate(table_id, r, c)
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

    # Total row with spanning
    total_row = len(entries) + 1
    qid_total_label = qid_alloc.allocate(table_id, total_row, 0)
    text_total_label = "Total Hours"
    cells.append(CellManifest(
        row=total_row, col=0, qid=qid_total_label, text=text_total_label,
        rendered_text=f"[{qid_total_label}]{text_total_label}",
        is_merged=True, merge_span=(1, 4)
    ))

    qid_total_val = qid_alloc.allocate(table_id, total_row, 4)
    text_total_val = "43:15"
    cells.append(CellManifest(
        row=total_row, col=4, qid=qid_total_val, text=text_total_val,
        rendered_text=f"[{qid_total_val}]{text_total_val}"
    ))

    data.append([f"[{qid_total_label}]{text_total_label}", None, None, None, f"[{qid_total_val}]{text_total_val}"])

    style = [
        ('LINEABOVE', (0, 0), (-1, -1), 1, colors.Color(122/255, 180/255, 225/255)),
        ('LINEBELOW', (0, -1), (-1, -1), 1.5, colors.black),
        ('BACKGROUND', (0, 0), (-1, 0), header_bg),
        ('BACKGROUND', (0, -1), (-1, -1), total_bg),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('SPAN', (0, -1), (3, -1)),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('ALIGN', (2, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
    ]

    return data, style, cells


def preset_invoice_items(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Invoice line items table with subtotal/tax/total rows."""
    cells = []
    data = []

    headers = ["Item #", "Description", "Qty", "Unit Price", "Amount"]
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(row=0, col=c, qid=qid, text=h, rendered_text=rendered))
    data.append(header_row)

    # Line items
    items = [
        ("001", "Professional Services - Consulting", "40", "$150.00", "$6,000.00"),
        ("002", "Software License - Annual", "1", "$2,500.00", "$2,500.00"),
        ("003", "Training - On-site (per day)", "3", "$1,200.00", "$3,600.00"),
        ("004", "Technical Support - Monthly", "12", "$500.00", "$6,000.00"),
        ("005", "Documentation Package", "1", "$750.00", "$750.00"),
    ]

    for r, row_vals in enumerate(items, start=1):
        row_data = []
        for c, text in enumerate(row_vals):
            qid = qid_alloc.allocate(table_id, r, c)
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

    # Summary rows (Subtotal, Tax, Total)
    summary_rows = [
        ("", "", "", "Subtotal:", "$18,850.00"),
        ("", "", "", "Tax (8%):", "$1,508.00"),
        ("", "", "", "TOTAL:", "$20,358.00"),
    ]

    for r, row_vals in enumerate(summary_rows, start=len(items) + 1):
        row_data = []
        for c, text in enumerate(row_vals):
            qid = qid_alloc.allocate(table_id, r, c)
            rendered = f"[{qid}]{text}" if text else ""
            row_data.append(rendered)
            if text:
                cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

    style = [
        ('GRID', (0, 0), (-1, len(items)), 0.5, colors.grey),
        ('BOX', (0, 0), (-1, len(items)), 1, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.15, 0.2, 0.35)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (3, -1), (-1, -1), 'Helvetica-Bold'),
        ('LINEABOVE', (3, -1), (-1, -1), 1, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
    ]

    return data, style, cells


def preset_test_report(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Engineering test report table with pass/fail indicators."""
    cells = []
    data = []

    headers = ["Test ID", "Test Description", "Expected", "Actual", "Result"]
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(row=0, col=c, qid=qid, text=h, rendered_text=rendered))
    data.append(header_row)

    tests = [
        ("TC-001", "Voltage output under load", "5.0V ± 0.1V", "5.02V", "PASS"),
        ("TC-002", "Current draw at idle", "< 10mA", "8.5mA", "PASS"),
        ("TC-003", "Temperature rise after 1hr", "< 20°C", "18.3°C", "PASS"),
        ("TC-004", "Response time to input", "< 50ms", "62ms", "FAIL"),
        ("TC-005", "Power consumption at max", "< 5W", "4.8W", "PASS"),
        ("TC-006", "EMI emissions", "FCC Class B", "Class B", "PASS"),
        ("TC-007", "Vibration test (MIL-STD)", "No failure", "No failure", "PASS"),
        ("TC-008", "Humidity exposure 95% RH", "Functional", "Functional", "PASS"),
    ]

    style_cmds = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (0, -1), 'Courier'),  # Test IDs in mono
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.2, 0.25, 0.4)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
    ]

    for r, row_vals in enumerate(tests, start=1):
        row_data = []
        for c, text in enumerate(row_vals):
            qid = qid_alloc.allocate(table_id, r, c)
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

        # Color the result cell
        if row_vals[-1] == "PASS":
            style_cmds.append(('BACKGROUND', (-1, r), (-1, r), colors.Color(0.8, 1.0, 0.8)))
        else:
            style_cmds.append(('BACKGROUND', (-1, r), (-1, r), colors.Color(1.0, 0.8, 0.8)))
            style_cmds.append(('FONTNAME', (-1, r), (-1, r), 'Helvetica-Bold'))

    return data, style_cmds, cells


def preset_gradient_header(
    qid_alloc: QidAllocator,
    table_id: str,
) -> tuple[list[list[Any]], list[tuple], list[CellManifest]]:
    """Table with horizontal gradient header (from reportlab example)."""
    cells = []
    data = []

    headers = ["Region", "Q1 Sales", "Q2 Sales", "Q3 Sales", "Q4 Sales", "Total"]
    header_row = []
    for c, h in enumerate(headers):
        qid = qid_alloc.allocate(table_id, 0, c)
        rendered = f"[{qid}]{h}"
        header_row.append(rendered)
        cells.append(CellManifest(row=0, col=c, qid=qid, text=h, rendered_text=rendered))
    data.append(header_row)

    # Regional sales data
    regions = [
        ("North America", "$1.2M", "$1.4M", "$1.3M", "$1.8M", "$5.7M"),
        ("Europe", "$0.8M", "$0.9M", "$1.1M", "$1.2M", "$4.0M"),
        ("Asia Pacific", "$0.6M", "$0.7M", "$0.8M", "$1.0M", "$3.1M"),
        ("Latin America", "$0.3M", "$0.4M", "$0.4M", "$0.5M", "$1.6M"),
        ("Middle East", "$0.2M", "$0.2M", "$0.3M", "$0.3M", "$1.0M"),
    ]

    for r, row_vals in enumerate(regions, start=1):
        row_data = []
        for c, text in enumerate(row_vals):
            qid = qid_alloc.allocate(table_id, r, c)
            rendered = f"[{qid}]{text}"
            row_data.append(rendered)
            cells.append(CellManifest(row=r, col=c, qid=qid, text=text, rendered_text=rendered))
        data.append(row_data)

    # Total row
    total_row = len(regions) + 1
    totals = ["TOTAL", "$3.1M", "$3.6M", "$3.9M", "$4.8M", "$15.4M"]
    row_data = []
    for c, text in enumerate(totals):
        qid = qid_alloc.allocate(table_id, total_row, c)
        rendered = f"[{qid}]{text}"
        row_data.append(rendered)
        cells.append(CellManifest(row=total_row, col=c, qid=qid, text=text, rendered_text=rendered))
    data.append(row_data)

    style = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        # Horizontal gradient for header
        ('BACKGROUND', (0, 0), (-1, 0), ["HORIZONTAL", colors.Color(0.2, 0.3, 0.6), colors.Color(0.5, 0.2, 0.5)]),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.Color(0.9, 0.9, 0.95)),
        ('BACKGROUND', (-1, 1), (-1, -2), colors.Color(0.95, 1.0, 0.95)),  # Total column light green
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]

    return data, style, cells


# =============================================================================
# Fixture Generator
# =============================================================================


PRESETS = {
    # Basic presets
    "simple_grid": preset_simple_grid,
    "merged_cells": preset_merged_cells,
    "borderless": preset_borderless,
    "alternating_rows": preset_alternating_rows,
    "nested_table": preset_nested_table,
    "with_paragraphs": preset_with_paragraphs,
    "numeric_data": preset_numeric_data,
    # Advanced presets
    "complex_merge": preset_complex_merge,
    "long_table": preset_long_table,
    "with_image": preset_with_image,
    "mixed_alignment": preset_mixed_alignment,
    "colored_cells": preset_colored_cells,
    "dense_data": preset_dense_data,
    # Engineering / Defense presets
    "requirements_matrix": preset_requirements_matrix,
    "spec_table": preset_spec_table,
    "compliance_matrix": preset_compliance_matrix,
    # ArXiv / Academic presets
    "results_table": preset_results_table,
    "comparison_matrix": preset_comparison_matrix,
    "ablation_study": preset_ablation_study,
    "dataset_stats": preset_dataset_stats,
    # Professional document presets
    "timesheet": preset_timesheet,
    "invoice_items": preset_invoice_items,
    "test_report": preset_test_report,
    "gradient_header": preset_gradient_header,
}


def generate_fixture(
    output_path: str,
    presets: list[str] | None = None,
    seed: int = 42,
    tables_per_page: int = 1,
) -> FixtureManifest:
    """Generate a PDF fixture with specified table presets.

    Args:
        output_path: Where to write the PDF
        presets: List of preset names to include (default: all)
        seed: Random seed for determinism
        tables_per_page: Max tables per page (overflow creates new pages)

    Returns:
        FixtureManifest with ground truth for all cells
    """
    if presets is None:
        presets = list(PRESETS.keys())

    # Validate presets
    for p in presets:
        if p not in PRESETS:
            raise ValueError(f"Unknown preset: {p}. Available: {list(PRESETS.keys())}")

    fixture_id = hashlib.md5(f"{output_path}:{seed}".encode()).hexdigest()[:8]
    qid_alloc = QidAllocator(fixture_id, seed)

    # Build document
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    story = []
    page_manifests = []
    current_page_tables = []
    page_num = 1
    table_idx = 0

    styles = getSampleStyleSheet()

    for preset_name in presets:
        preset_fn = PRESETS[preset_name]
        table_id = f"t{table_idx}"

        # Generate table
        data, style_cmds, manifest_cells = preset_fn(qid_alloc, table_id)

        # Create ReportLab table
        table = Table(data)
        table.setStyle(TableStyle(style_cmds))

        # Add title
        title = Paragraph(f"<b>Preset: {preset_name}</b> (Table ID: {table_id})", styles['Heading2'])
        story.append(title)
        story.append(Spacer(1, 0.1 * inch))
        story.append(table)
        story.append(Spacer(1, 0.3 * inch))

        # Track manifest
        ruled = any(cmd[0] in ('GRID', 'BOX', 'INNERGRID') for cmd in style_cmds)
        table_manifest = TableManifest(
            table_id=table_id,
            rows=len(data),
            cols=len(data[0]) if data else 0,
            cells=manifest_cells,
            preset=preset_name,
            ruled=ruled,
        )
        current_page_tables.append(table_manifest)
        table_idx += 1

        # Page break logic
        if len(current_page_tables) >= tables_per_page and preset_name != presets[-1]:
            page_manifests.append(PageManifest(
                page_num=page_num,
                width=letter[0],
                height=letter[1],
                tables=current_page_tables,
            ))
            current_page_tables = []
            page_num += 1
            story.append(PageBreak())

    # Final page
    if current_page_tables:
        page_manifests.append(PageManifest(
            page_num=page_num,
            width=letter[0],
            height=letter[1],
            tables=current_page_tables,
        ))

    # Build PDF
    doc.build(story)

    # Create manifest
    manifest = FixtureManifest(
        fixture_id=fixture_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        seed=seed,
        pages=page_manifests,
        presets_used=presets,
    )

    return manifest


def generate_and_save(
    output_dir: str,
    name: str = "table_fixtures",
    presets: list[str] | None = None,
    seed: int = 42,
) -> tuple[Path, Path]:
    """Generate fixture PDF and save manifest JSON alongside it.

    Returns:
        (pdf_path, manifest_path)
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = out_dir / f"{name}.pdf"
    manifest_path = out_dir / f"{name}.manifest.json"

    manifest = generate_fixture(str(pdf_path), presets=presets, seed=seed)

    # Save manifest
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))

    return pdf_path, manifest_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate PDF table fixtures")
    parser.add_argument("--output", "-o", default="fixtures/table_fixtures.pdf",
                        help="Output PDF path")
    parser.add_argument("--presets", "-p", nargs="+", default=None,
                        help="Presets to include (default: all)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--list-presets", action="store_true",
                        help="List available presets and exit")
    args = parser.parse_args()

    if args.list_presets:
        print("Available presets:")
        for name in PRESETS:
            print(f"  - {name}")
        return

    manifest = generate_fixture(args.output, presets=args.presets, seed=args.seed)

    # Save manifest
    manifest_path = args.output.replace(".pdf", ".manifest.json")
    Path(manifest_path).write_text(json.dumps(manifest.to_dict(), indent=2))

    print(f"Generated: {args.output}")
    print(f"Manifest:  {manifest_path}")
    print(f"  Pages:   {len(manifest.pages)}")
    print(f"  Tables:  {sum(len(p.tables) for p in manifest.pages)}")
    print(f"  Cells:   {sum(len(t.cells) for p in manifest.pages for t in p.tables)}")
    print(f"  Presets: {', '.join(manifest.presets_used)}")


if __name__ == "__main__":
    main()
