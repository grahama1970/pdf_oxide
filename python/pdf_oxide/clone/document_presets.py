"""Complete document presets — full multi-page PDF generation with ReportLab.

Unlike fixture_generator.py (individual table presets), this module creates
complete document structures matching real-world PDFs:

- Engineering specs (TOC, requirements tables, compliance matrices)
- Academic papers (title, abstract, 2-column layout, figures, references)
- Technical manuals (chapters, numbered sections, diagrams)
- Reports (executive summary, findings, appendices)

Each preset generates a complete PDF with QID-embedded elements for
extraction validation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    FrameBreak,
    Image,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


# =============================================================================
# Manifest Dataclasses
# =============================================================================

ElementType = Literal[
    "header", "footer", "heading", "paragraph", "table", "figure",
    "caption", "list", "requirement", "toc_entry", "page_number"
]


@dataclass
class ElementManifest:
    """Ground truth for a single element."""
    eid: str
    type: ElementType
    qid: str
    text: str
    rendered_text: str
    page: int
    reading_order: int
    level: int = 0  # For headings (1-4)
    is_toc: bool = False
    table_data: Optional[List[List[str]]] = None  # For tables
    rows: int = 0
    cols: int = 0


@dataclass
class PageManifest:
    """Ground truth for a page."""
    page_num: int
    width: float
    height: float
    elements: List[ElementManifest]
    has_header: bool = False
    has_footer: bool = False


@dataclass
class DocumentManifest:
    """Ground truth for entire document."""
    doc_id: str
    preset: str
    generated_at: str
    seed: int
    page_count: int
    pages: List[PageManifest]
    toc_entries: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "doc_id": self.doc_id,
            "preset": self.preset,
            "generated_at": self.generated_at,
            "seed": self.seed,
            "page_count": self.page_count,
            "toc_entries": self.toc_entries,
            "pages": [
                {
                    "page_num": p.page_num,
                    "width": p.width,
                    "height": p.height,
                    "has_header": p.has_header,
                    "has_footer": p.has_footer,
                    "elements": [
                        {
                            "eid": e.eid,
                            "type": e.type,
                            "qid": e.qid,
                            "text": e.text,
                            "rendered_text": e.rendered_text,
                            "page": e.page,
                            "reading_order": e.reading_order,
                            "level": e.level,
                            "is_toc": e.is_toc,
                            "rows": e.rows,
                            "cols": e.cols,
                        }
                        for e in p.elements
                    ],
                }
                for p in self.pages
            ],
        }

    def total_elements(self) -> int:
        return sum(len(p.elements) for p in self.pages)


class QidAllocator:
    """Deterministic QID generator."""

    VERSION = "doc_v1"

    def __init__(self, doc_id: str, seed: int):
        self.doc_id = doc_id
        self.seed = seed
        self._counter = 0
        self._assigned: Dict[str, str] = {}

    def allocate(self, element_type: str, *parts) -> str:
        """Generate deterministic QID."""
        semantic_key = f"{self.VERSION}|{self.doc_id}|{self.seed}|{element_type}|{'|'.join(str(p) for p in parts)}"

        if semantic_key in self._assigned:
            return self._assigned[semantic_key]

        h = hashlib.sha256(semantic_key.encode()).hexdigest()[:12]
        qid = f"QID_{h.upper()}"

        self._assigned[semantic_key] = qid
        self._counter += 1
        return qid


# =============================================================================
# Style Definitions
# =============================================================================

def get_document_styles() -> Dict[str, ParagraphStyle]:
    """Standard document styles matching engineering/academic PDFs."""
    base = getSampleStyleSheet()

    return {
        # Title page
        "doc_title": ParagraphStyle(
            "doc_title",
            parent=base["Title"],
            fontSize=24,
            alignment=TA_CENTER,
            spaceAfter=24,
        ),
        "doc_subtitle": ParagraphStyle(
            "doc_subtitle",
            parent=base["Normal"],
            fontSize=14,
            alignment=TA_CENTER,
            spaceAfter=12,
        ),
        "doc_author": ParagraphStyle(
            "doc_author",
            parent=base["Normal"],
            fontSize=12,
            alignment=TA_CENTER,
            spaceAfter=6,
        ),
        "doc_date": ParagraphStyle(
            "doc_date",
            parent=base["Normal"],
            fontSize=10,
            alignment=TA_CENTER,
            spaceAfter=24,
        ),

        # Headings
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontSize=16,
            spaceBefore=18,
            spaceAfter=10,
            fontName="Helvetica-Bold",
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontSize=14,
            spaceBefore=14,
            spaceAfter=8,
            fontName="Helvetica-Bold",
        ),
        "h3": ParagraphStyle(
            "h3",
            parent=base["Heading3"],
            fontSize=12,
            spaceBefore=10,
            spaceAfter=6,
            fontName="Helvetica-Bold",
        ),
        "h4": ParagraphStyle(
            "h4",
            parent=base["Heading4"],
            fontSize=11,
            spaceBefore=8,
            spaceAfter=4,
            fontName="Helvetica-Bold",
        ),

        # Body text
        "body": ParagraphStyle(
            "body",
            parent=base["Normal"],
            fontSize=10,
            alignment=TA_JUSTIFY,
            spaceAfter=8,
            firstLineIndent=0,
        ),
        "body_indent": ParagraphStyle(
            "body_indent",
            parent=base["Normal"],
            fontSize=10,
            alignment=TA_JUSTIFY,
            spaceAfter=8,
            firstLineIndent=18,
        ),

        # TOC
        "toc_h1": ParagraphStyle(
            "toc_h1",
            parent=base["Normal"],
            fontSize=11,
            fontName="Helvetica-Bold",
            spaceBefore=8,
        ),
        "toc_h2": ParagraphStyle(
            "toc_h2",
            parent=base["Normal"],
            fontSize=10,
            leftIndent=18,
            spaceBefore=2,
        ),
        "toc_h3": ParagraphStyle(
            "toc_h3",
            parent=base["Normal"],
            fontSize=9,
            leftIndent=36,
            spaceBefore=1,
        ),

        # Lists
        "bullet": ParagraphStyle(
            "bullet",
            parent=base["Normal"],
            fontSize=10,
            leftIndent=24,
            firstLineIndent=-12,
            spaceAfter=4,
        ),
        "numbered": ParagraphStyle(
            "numbered",
            parent=base["Normal"],
            fontSize=10,
            leftIndent=24,
            firstLineIndent=-12,
            spaceAfter=4,
        ),

        # Requirements
        "requirement_id": ParagraphStyle(
            "requirement_id",
            parent=base["Normal"],
            fontSize=10,
            fontName="Helvetica-Bold",
            spaceBefore=8,
        ),
        "requirement_text": ParagraphStyle(
            "requirement_text",
            parent=base["Normal"],
            fontSize=10,
            leftIndent=36,
            spaceAfter=6,
        ),

        # Captions
        "figure_caption": ParagraphStyle(
            "figure_caption",
            parent=base["Normal"],
            fontSize=9,
            alignment=TA_CENTER,
            spaceBefore=6,
            spaceAfter=12,
        ),
        "table_caption": ParagraphStyle(
            "table_caption",
            parent=base["Normal"],
            fontSize=9,
            alignment=TA_CENTER,
            spaceBefore=6,
            spaceAfter=12,
        ),

        # Header/footer
        "header": ParagraphStyle(
            "header",
            parent=base["Normal"],
            fontSize=9,
            alignment=TA_CENTER,
        ),
        "footer": ParagraphStyle(
            "footer",
            parent=base["Normal"],
            fontSize=9,
            alignment=TA_CENTER,
        ),
        "page_num": ParagraphStyle(
            "page_num",
            parent=base["Normal"],
            fontSize=9,
            alignment=TA_CENTER,
        ),
    }


# =============================================================================
# Table Builders
# =============================================================================

def build_requirements_table(
    qid_alloc: QidAllocator,
    requirements: List[Dict[str, str]],
    table_id: str,
) -> tuple[Table, List[ElementManifest]]:
    """Build a requirements compliance table.

    Requirements format: [{"id": "3.1.1", "text": "...", "status": "Compliant"}, ...]
    """
    elements = []

    # Header
    header = ["Req ID", "Requirement", "Status", "Evidence"]
    data = [header]

    for i, req in enumerate(requirements):
        qid = qid_alloc.allocate("table_cell", table_id, i, 0)
        row = [
            f"[{qid}]{req.get('id', '')}",
            req.get("text", ""),
            req.get("status", ""),
            req.get("evidence", ""),
        ]
        data.append(row)

        elements.append(ElementManifest(
            eid=f"{table_id}_r{i+1}",
            type="requirement",
            qid=qid,
            text=req.get("id", ""),
            rendered_text=f"[{qid}]{req.get('id', '')}",
            page=0,  # Will be set later
            reading_order=i,
        ))

    style = TableStyle([
        # Header row
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.4)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),

        # Grid
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),

        # Alignment
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (2, 1), (2, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),

        # Padding
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),

        # Zebra stripes
        *[
            ('BACKGROUND', (0, i), (-1, i), colors.Color(0.95, 0.95, 0.95))
            for i in range(2, len(data), 2)
        ],
    ])

    col_widths = [0.8 * inch, 3.5 * inch, 0.9 * inch, 1.5 * inch]
    table = Table(data, colWidths=col_widths)
    table.setStyle(style)

    return table, elements


def build_data_table(
    qid_alloc: QidAllocator,
    headers: List[str],
    rows: List[List[str]],
    table_id: str,
    style_preset: str = "professional",
) -> tuple[Table, List[ElementManifest]]:
    """Build a general data table with QID embedding."""
    elements = []
    data = [headers]

    for r_idx, row in enumerate(rows):
        qid_row = []
        for c_idx, cell in enumerate(row):
            qid = qid_alloc.allocate("table_cell", table_id, r_idx + 1, c_idx)
            qid_row.append(f"[{qid}]{cell}")
            elements.append(ElementManifest(
                eid=f"{table_id}_r{r_idx+1}_c{c_idx}",
                type="table",
                qid=qid,
                text=cell,
                rendered_text=f"[{qid}]{cell}",
                page=0,
                reading_order=r_idx * len(row) + c_idx,
            ))
        data.append(qid_row)

    style_cmds = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]

    if style_preset == "professional":
        style_cmds.extend([
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.4)),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('LINEBELOW', (0, 0), (-1, 0), 2, colors.Color(0.1, 0.2, 0.3)),
            ('LINEBELOW', (0, -1), (-1, -1), 1, colors.black),
            *[
                ('LINEBELOW', (0, i), (-1, i), 0.25, colors.Color(0.85, 0.85, 0.85))
                for i in range(1, len(data) - 1)
            ],
        ])
    elif style_preset == "grid":
        style_cmds.extend([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ])
    elif style_preset == "minimal":
        style_cmds.extend([
            ('LINEABOVE', (0, 0), (-1, 0), 1, colors.black),
            ('LINEBELOW', (0, 0), (-1, 0), 0.5, colors.black),
            ('LINEBELOW', (0, -1), (-1, -1), 1, colors.black),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ])
    elif style_preset == "zebra":
        style_cmds.extend([
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.3, 0.3, 0.5)),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            *[
                ('BACKGROUND', (0, i), (-1, i), colors.Color(0.92, 0.92, 0.96))
                for i in range(2, len(data), 2)
            ],
        ])

    table = Table(data)
    table.setStyle(TableStyle(style_cmds))

    return table, elements


def build_figure_placeholder(
    width: float,
    height: float,
    label: str,
) -> Table:
    """Build a figure placeholder box."""
    data = [[f"[{label}]"]]
    style = TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.Color(0.92, 0.92, 0.92)),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.Color(0.4, 0.4, 0.4)),
    ])
    table = Table(data, colWidths=[width], rowHeights=[height])
    table.setStyle(style)
    return table


# =============================================================================
# Document Presets
# =============================================================================

def preset_engineering_spec(
    output_path: str,
    qid_alloc: QidAllocator,
    seed: int = 42,
) -> DocumentManifest:
    """Engineering specification document.

    Structure:
    - Title page
    - Table of Contents
    - 1. Introduction
    - 2. Scope
    - 3. Requirements (with compliance tables)
    - 4. Verification Matrix
    - Appendix A: Definitions
    """
    styles = get_document_styles()
    story = []
    elements_by_page: Dict[int, List[ElementManifest]] = {}
    toc_entries = []
    page_num = 1
    reading_order = 0

    def add_element(elem: ElementManifest, page: int):
        elem.page = page
        elem.reading_order = reading_order
        elements_by_page.setdefault(page, []).append(elem)

    # ─────────────────────────────────────────────────────────────────────────
    # Title Page
    # ─────────────────────────────────────────────────────────────────────────
    qid = qid_alloc.allocate("title", 0)
    title_text = "System Requirements Specification"
    story.append(Spacer(1, 2 * inch))
    story.append(Paragraph(f"[{qid}]{title_text}", styles["doc_title"]))
    add_element(ElementManifest(
        eid="title", type="heading", qid=qid, text=title_text,
        rendered_text=f"[{qid}]{title_text}", page=page_num, reading_order=reading_order, level=0
    ), page_num)
    reading_order += 1

    qid = qid_alloc.allocate("subtitle", 0)
    subtitle = "Document No. SRS-2024-001"
    story.append(Paragraph(f"[{qid}]{subtitle}", styles["doc_subtitle"]))
    add_element(ElementManifest(
        eid="subtitle", type="paragraph", qid=qid, text=subtitle,
        rendered_text=f"[{qid}]{subtitle}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    story.append(Spacer(1, 1 * inch))

    qid = qid_alloc.allocate("author", 0)
    author = "Engineering Division"
    story.append(Paragraph(f"[{qid}]{author}", styles["doc_author"]))
    add_element(ElementManifest(
        eid="author", type="paragraph", qid=qid, text=author,
        rendered_text=f"[{qid}]{author}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    qid = qid_alloc.allocate("date", 0)
    date_str = "April 2024"
    story.append(Paragraph(f"[{qid}]{date_str}", styles["doc_date"]))
    add_element(ElementManifest(
        eid="date", type="paragraph", qid=qid, text=date_str,
        rendered_text=f"[{qid}]{date_str}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    story.append(PageBreak())
    page_num += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Table of Contents
    # ─────────────────────────────────────────────────────────────────────────
    qid = qid_alloc.allocate("toc_title", 0)
    toc_title = "Table of Contents"
    story.append(Paragraph(f"[{qid}]{toc_title}", styles["h1"]))
    add_element(ElementManifest(
        eid="toc_title", type="heading", qid=qid, text=toc_title,
        rendered_text=f"[{qid}]{toc_title}", page=page_num, reading_order=reading_order, level=1
    ), page_num)
    reading_order += 1

    toc_items = [
        ("1. Introduction", 3, 1),
        ("1.1 Purpose", 3, 2),
        ("1.2 Scope", 3, 2),
        ("2. System Overview", 4, 1),
        ("3. Requirements", 5, 1),
        ("3.1 Functional Requirements", 5, 2),
        ("3.2 Performance Requirements", 6, 2),
        ("3.3 Interface Requirements", 6, 2),
        ("4. Verification Matrix", 7, 1),
        ("Appendix A: Definitions", 8, 1),
    ]

    for title, target_page, level in toc_items:
        qid = qid_alloc.allocate("toc_entry", title)
        style_name = f"toc_h{min(level, 3)}"
        story.append(Paragraph(
            f"[{qid}]{title} {'.' * (50 - len(title))} {target_page}",
            styles[style_name]
        ))
        add_element(ElementManifest(
            eid=f"toc_{title}", type="toc_entry", qid=qid, text=title,
            rendered_text=f"[{qid}]{title}", page=page_num, reading_order=reading_order,
            level=level, is_toc=True
        ), page_num)
        reading_order += 1
        toc_entries.append({"title": title, "page": target_page, "level": level})

    story.append(PageBreak())
    page_num += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Section 1: Introduction
    # ─────────────────────────────────────────────────────────────────────────
    qid = qid_alloc.allocate("h1", "introduction")
    section_title = "1. Introduction"
    story.append(Paragraph(f"[{qid}]{section_title}", styles["h1"]))
    add_element(ElementManifest(
        eid="s1_title", type="heading", qid=qid, text=section_title,
        rendered_text=f"[{qid}]{section_title}", page=page_num, reading_order=reading_order, level=1
    ), page_num)
    reading_order += 1

    qid = qid_alloc.allocate("para", "intro_1")
    para_text = ("This document specifies the system requirements for the XYZ Project. "
                 "It provides a comprehensive description of functional, performance, "
                 "and interface requirements that must be satisfied by the system.")
    story.append(Paragraph(f"[{qid}]{para_text}", styles["body"]))
    add_element(ElementManifest(
        eid="s1_p1", type="paragraph", qid=qid, text=para_text,
        rendered_text=f"[{qid}]{para_text}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    # Subsection 1.1
    qid = qid_alloc.allocate("h2", "purpose")
    subsec_title = "1.1 Purpose"
    story.append(Paragraph(f"[{qid}]{subsec_title}", styles["h2"]))
    add_element(ElementManifest(
        eid="s1_1_title", type="heading", qid=qid, text=subsec_title,
        rendered_text=f"[{qid}]{subsec_title}", page=page_num, reading_order=reading_order, level=2
    ), page_num)
    reading_order += 1

    qid = qid_alloc.allocate("para", "purpose_1")
    para_text = ("The purpose of this SRS is to define the requirements for the system "
                 "in sufficient detail to enable system design, development, and testing.")
    story.append(Paragraph(f"[{qid}]{para_text}", styles["body"]))
    add_element(ElementManifest(
        eid="s1_1_p1", type="paragraph", qid=qid, text=para_text,
        rendered_text=f"[{qid}]{para_text}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    story.append(PageBreak())
    page_num += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Section 3: Requirements (with table)
    # ─────────────────────────────────────────────────────────────────────────
    qid = qid_alloc.allocate("h1", "requirements")
    section_title = "3. Requirements"
    story.append(Paragraph(f"[{qid}]{section_title}", styles["h1"]))
    add_element(ElementManifest(
        eid="s3_title", type="heading", qid=qid, text=section_title,
        rendered_text=f"[{qid}]{section_title}", page=page_num, reading_order=reading_order, level=1
    ), page_num)
    reading_order += 1

    qid = qid_alloc.allocate("h2", "func_req")
    subsec_title = "3.1 Functional Requirements"
    story.append(Paragraph(f"[{qid}]{subsec_title}", styles["h2"]))
    add_element(ElementManifest(
        eid="s3_1_title", type="heading", qid=qid, text=subsec_title,
        rendered_text=f"[{qid}]{subsec_title}", page=page_num, reading_order=reading_order, level=2
    ), page_num)
    reading_order += 1

    # Requirements table
    requirements = [
        {"id": "FR-001", "text": "The system shall process user authentication requests within 2 seconds.", "status": "Compliant", "evidence": "Test Report TR-001"},
        {"id": "FR-002", "text": "The system shall support concurrent sessions for up to 1000 users.", "status": "Compliant", "evidence": "Test Report TR-002"},
        {"id": "FR-003", "text": "The system shall provide real-time data synchronization.", "status": "Partial", "evidence": "Test Report TR-003"},
        {"id": "FR-004", "text": "The system shall log all user activities for audit purposes.", "status": "Compliant", "evidence": "Test Report TR-004"},
        {"id": "FR-005", "text": "The system shall encrypt all data in transit using TLS 1.3.", "status": "Compliant", "evidence": "Test Report TR-005"},
    ]

    table, table_elements = build_requirements_table(qid_alloc, requirements, "t_func_req")
    for elem in table_elements:
        elem.page = page_num
        elements_by_page.setdefault(page_num, []).append(elem)

    story.append(Spacer(1, 0.2 * inch))
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    # Caption
    qid = qid_alloc.allocate("caption", "func_req_table")
    caption_text = "Table 1: Functional Requirements Compliance Matrix"
    story.append(Paragraph(f"[{qid}]{caption_text}", styles["table_caption"]))
    add_element(ElementManifest(
        eid="t1_caption", type="caption", qid=qid, text=caption_text,
        rendered_text=f"[{qid}]{caption_text}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    story.append(PageBreak())
    page_num += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Section 4: Verification Matrix
    # ─────────────────────────────────────────────────────────────────────────
    qid = qid_alloc.allocate("h1", "verification")
    section_title = "4. Verification Matrix"
    story.append(Paragraph(f"[{qid}]{section_title}", styles["h1"]))
    add_element(ElementManifest(
        eid="s4_title", type="heading", qid=qid, text=section_title,
        rendered_text=f"[{qid}]{section_title}", page=page_num, reading_order=reading_order, level=1
    ), page_num)
    reading_order += 1

    # Verification table
    headers = ["Requirement", "Test Method", "Test ID", "Result"]
    rows = [
        ["FR-001", "Demonstration", "DT-001", "Pass"],
        ["FR-002", "Analysis", "AT-001", "Pass"],
        ["FR-003", "Inspection", "IT-001", "Partial"],
        ["FR-004", "Test", "TT-001", "Pass"],
        ["FR-005", "Analysis", "AT-002", "Pass"],
    ]

    table, table_elements = build_data_table(qid_alloc, headers, rows, "t_verification", "professional")
    for elem in table_elements:
        elem.page = page_num
        elements_by_page.setdefault(page_num, []).append(elem)

    story.append(Spacer(1, 0.2 * inch))
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    qid = qid_alloc.allocate("caption", "verif_table")
    caption_text = "Table 2: Requirements Verification Matrix"
    story.append(Paragraph(f"[{qid}]{caption_text}", styles["table_caption"]))
    add_element(ElementManifest(
        eid="t2_caption", type="caption", qid=qid, text=caption_text,
        rendered_text=f"[{qid}]{caption_text}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Build PDF
    # ─────────────────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=1 * inch,
        rightMargin=1 * inch,
        topMargin=1 * inch,
        bottomMargin=1 * inch,
    )
    doc.build(story)

    # Build manifest
    pages = []
    for pn in range(1, page_num + 1):
        pages.append(PageManifest(
            page_num=pn,
            width=letter[0],
            height=letter[1],
            elements=elements_by_page.get(pn, []),
            has_header=(pn > 2),
            has_footer=True,
        ))

    return DocumentManifest(
        doc_id=qid_alloc.doc_id,
        preset="engineering_spec",
        generated_at=datetime.now(timezone.utc).isoformat(),
        seed=seed,
        page_count=page_num,
        pages=pages,
        toc_entries=toc_entries,
    )


def preset_academic_paper(
    output_path: str,
    qid_alloc: QidAllocator,
    seed: int = 42,
) -> DocumentManifest:
    """Academic paper format (single-column, arXiv style).

    Structure:
    - Title + Authors + Abstract
    - 1. Introduction
    - 2. Related Work
    - 3. Methodology
    - 4. Results (with tables and figures)
    - 5. Conclusion
    - References
    """
    styles = get_document_styles()
    story = []
    elements_by_page: Dict[int, List[ElementManifest]] = {}
    page_num = 1
    reading_order = 0

    def add_element(elem: ElementManifest, page: int):
        elem.page = page
        elem.reading_order = reading_order
        elements_by_page.setdefault(page, []).append(elem)

    # ─────────────────────────────────────────────────────────────────────────
    # Title and Abstract
    # ─────────────────────────────────────────────────────────────────────────
    qid = qid_alloc.allocate("title", 0)
    title_text = "Deep Learning for Table Extraction: A Comprehensive Study"
    story.append(Paragraph(f"[{qid}]{title_text}", styles["doc_title"]))
    add_element(ElementManifest(
        eid="title", type="heading", qid=qid, text=title_text,
        rendered_text=f"[{qid}]{title_text}", page=page_num, reading_order=reading_order, level=0
    ), page_num)
    reading_order += 1

    qid = qid_alloc.allocate("author", 0)
    author_text = "John Smith, Jane Doe, Bob Wilson"
    story.append(Paragraph(f"[{qid}]{author_text}", styles["doc_author"]))
    add_element(ElementManifest(
        eid="authors", type="paragraph", qid=qid, text=author_text,
        rendered_text=f"[{qid}]{author_text}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    story.append(Spacer(1, 0.3 * inch))

    qid = qid_alloc.allocate("h2", "abstract")
    abstract_title = "Abstract"
    story.append(Paragraph(f"[{qid}]{abstract_title}", styles["h2"]))
    add_element(ElementManifest(
        eid="abstract_title", type="heading", qid=qid, text=abstract_title,
        rendered_text=f"[{qid}]{abstract_title}", page=page_num, reading_order=reading_order, level=2
    ), page_num)
    reading_order += 1

    qid = qid_alloc.allocate("para", "abstract_text")
    abstract_text = (
        "We present a novel approach to table extraction from PDF documents using "
        "deep learning. Our method achieves state-of-the-art results on benchmark "
        "datasets, with F1 scores of 0.95 on PubTables-1M and 0.92 on FinTabNet. "
        "We introduce a new architecture combining vision transformers with "
        "sequence-to-sequence models for structure recognition."
    )
    story.append(Paragraph(f"[{qid}]{abstract_text}", styles["body"]))
    add_element(ElementManifest(
        eid="abstract_text", type="paragraph", qid=qid, text=abstract_text,
        rendered_text=f"[{qid}]{abstract_text}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    story.append(Spacer(1, 0.3 * inch))

    # ─────────────────────────────────────────────────────────────────────────
    # Section 1: Introduction
    # ─────────────────────────────────────────────────────────────────────────
    qid = qid_alloc.allocate("h1", "introduction")
    section_title = "1. Introduction"
    story.append(Paragraph(f"[{qid}]{section_title}", styles["h1"]))
    add_element(ElementManifest(
        eid="s1_title", type="heading", qid=qid, text=section_title,
        rendered_text=f"[{qid}]{section_title}", page=page_num, reading_order=reading_order, level=1
    ), page_num)
    reading_order += 1

    qid = qid_alloc.allocate("para", "intro_1")
    para_text = (
        "Table extraction from documents is a fundamental task in document understanding. "
        "Tables contain structured information that is critical for many downstream applications, "
        "including knowledge base construction, question answering, and data analysis."
    )
    story.append(Paragraph(f"[{qid}]{para_text}", styles["body"]))
    add_element(ElementManifest(
        eid="s1_p1", type="paragraph", qid=qid, text=para_text,
        rendered_text=f"[{qid}]{para_text}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    story.append(PageBreak())
    page_num += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Section 4: Results (with tables)
    # ─────────────────────────────────────────────────────────────────────────
    qid = qid_alloc.allocate("h1", "results")
    section_title = "4. Results"
    story.append(Paragraph(f"[{qid}]{section_title}", styles["h1"]))
    add_element(ElementManifest(
        eid="s4_title", type="heading", qid=qid, text=section_title,
        rendered_text=f"[{qid}]{section_title}", page=page_num, reading_order=reading_order, level=1
    ), page_num)
    reading_order += 1

    qid = qid_alloc.allocate("para", "results_1")
    para_text = "Table 1 shows the comparison of our method with baseline approaches."
    story.append(Paragraph(f"[{qid}]{para_text}", styles["body"]))
    add_element(ElementManifest(
        eid="s4_p1", type="paragraph", qid=qid, text=para_text,
        rendered_text=f"[{qid}]{para_text}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    # Results table
    headers = ["Method", "Precision", "Recall", "F1"]
    rows = [
        ["Baseline CNN", "0.82", "0.79", "0.80"],
        ["TableNet", "0.88", "0.85", "0.86"],
        ["DETR-Table", "0.91", "0.89", "0.90"],
        ["Ours (ViT-Seq)", "0.96", "0.94", "0.95"],
    ]

    table, table_elements = build_data_table(qid_alloc, headers, rows, "t_results", "minimal")
    for elem in table_elements:
        elem.page = page_num
        elements_by_page.setdefault(page_num, []).append(elem)

    story.append(Spacer(1, 0.2 * inch))
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    qid = qid_alloc.allocate("caption", "results_table")
    caption_text = "Table 1: Comparison with baseline methods on PubTables-1M"
    story.append(Paragraph(f"[{qid}]{caption_text}", styles["table_caption"]))
    add_element(ElementManifest(
        eid="t1_caption", type="caption", qid=qid, text=caption_text,
        rendered_text=f"[{qid}]{caption_text}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    story.append(Spacer(1, 0.3 * inch))

    # Ablation study table
    qid = qid_alloc.allocate("h2", "ablation")
    subsec_title = "4.1 Ablation Study"
    story.append(Paragraph(f"[{qid}]{subsec_title}", styles["h2"]))
    add_element(ElementManifest(
        eid="s4_1_title", type="heading", qid=qid, text=subsec_title,
        rendered_text=f"[{qid}]{subsec_title}", page=page_num, reading_order=reading_order, level=2
    ), page_num)
    reading_order += 1

    headers = ["Component", "Removed", "F1 Score", "Delta"]
    rows = [
        ["Full model", "-", "0.95", "-"],
        ["w/o ViT backbone", "Vision Transformer", "0.88", "-0.07"],
        ["w/o seq2seq decoder", "Sequence decoder", "0.91", "-0.04"],
        ["w/o data augmentation", "Augmentation", "0.93", "-0.02"],
    ]

    table, table_elements = build_data_table(qid_alloc, headers, rows, "t_ablation", "zebra")
    for elem in table_elements:
        elem.page = page_num
        elements_by_page.setdefault(page_num, []).append(elem)

    story.append(Spacer(1, 0.2 * inch))
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    qid = qid_alloc.allocate("caption", "ablation_table")
    caption_text = "Table 2: Ablation study results"
    story.append(Paragraph(f"[{qid}]{caption_text}", styles["table_caption"]))
    add_element(ElementManifest(
        eid="t2_caption", type="caption", qid=qid, text=caption_text,
        rendered_text=f"[{qid}]{caption_text}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    # ─────────────────────────────────────────────────────────────────────────
    # Build PDF
    # ─────────────────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=1 * inch,
        rightMargin=1 * inch,
        topMargin=1 * inch,
        bottomMargin=1 * inch,
    )
    doc.build(story)

    # Build manifest
    pages = []
    for pn in range(1, page_num + 1):
        pages.append(PageManifest(
            page_num=pn,
            width=letter[0],
            height=letter[1],
            elements=elements_by_page.get(pn, []),
        ))

    return DocumentManifest(
        doc_id=qid_alloc.doc_id,
        preset="academic_paper",
        generated_at=datetime.now(timezone.utc).isoformat(),
        seed=seed,
        page_count=page_num,
        pages=pages,
    )


def preset_technical_report(
    output_path: str,
    qid_alloc: QidAllocator,
    seed: int = 42,
) -> DocumentManifest:
    """Technical report with executive summary and data tables.

    Structure:
    - Title page
    - Executive Summary
    - 1. Background
    - 2. Analysis
    - 3. Findings (with data tables)
    - 4. Recommendations
    - Appendices
    """
    styles = get_document_styles()
    story = []
    elements_by_page: Dict[int, List[ElementManifest]] = {}
    page_num = 1
    reading_order = 0

    def add_element(elem: ElementManifest, page: int):
        elem.page = page
        elem.reading_order = reading_order
        elements_by_page.setdefault(page, []).append(elem)

    # Title page
    qid = qid_alloc.allocate("title", 0)
    title_text = "Technical Assessment Report"
    story.append(Spacer(1, 2 * inch))
    story.append(Paragraph(f"[{qid}]{title_text}", styles["doc_title"]))
    add_element(ElementManifest(
        eid="title", type="heading", qid=qid, text=title_text,
        rendered_text=f"[{qid}]{title_text}", page=page_num, reading_order=reading_order, level=0
    ), page_num)
    reading_order += 1

    qid = qid_alloc.allocate("subtitle", 0)
    subtitle = "Performance Analysis Q1 2024"
    story.append(Paragraph(f"[{qid}]{subtitle}", styles["doc_subtitle"]))
    add_element(ElementManifest(
        eid="subtitle", type="paragraph", qid=qid, text=subtitle,
        rendered_text=f"[{qid}]{subtitle}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    story.append(PageBreak())
    page_num += 1

    # Executive Summary
    qid = qid_alloc.allocate("h1", "exec_summary")
    section_title = "Executive Summary"
    story.append(Paragraph(f"[{qid}]{section_title}", styles["h1"]))
    add_element(ElementManifest(
        eid="exec_summary_title", type="heading", qid=qid, text=section_title,
        rendered_text=f"[{qid}]{section_title}", page=page_num, reading_order=reading_order, level=1
    ), page_num)
    reading_order += 1

    qid = qid_alloc.allocate("para", "exec_summary_1")
    para_text = (
        "This report presents the findings of our Q1 2024 performance assessment. "
        "Key metrics show a 15% improvement in processing efficiency compared to "
        "the previous quarter, with system availability exceeding 99.9%."
    )
    story.append(Paragraph(f"[{qid}]{para_text}", styles["body"]))
    add_element(ElementManifest(
        eid="exec_summary_p1", type="paragraph", qid=qid, text=para_text,
        rendered_text=f"[{qid}]{para_text}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    story.append(PageBreak())
    page_num += 1

    # Section: Findings with data table
    qid = qid_alloc.allocate("h1", "findings")
    section_title = "3. Findings"
    story.append(Paragraph(f"[{qid}]{section_title}", styles["h1"]))
    add_element(ElementManifest(
        eid="findings_title", type="heading", qid=qid, text=section_title,
        rendered_text=f"[{qid}]{section_title}", page=page_num, reading_order=reading_order, level=1
    ), page_num)
    reading_order += 1

    # Performance data table
    headers = ["Metric", "Q4 2023", "Q1 2024", "Change"]
    rows = [
        ["Response Time (ms)", "245", "208", "-15%"],
        ["Throughput (req/s)", "1,250", "1,438", "+15%"],
        ["Error Rate (%)", "0.12", "0.08", "-33%"],
        ["Availability (%)", "99.85", "99.92", "+0.07%"],
        ["CPU Utilization (%)", "72", "65", "-10%"],
    ]

    table, table_elements = build_data_table(qid_alloc, headers, rows, "t_perf", "professional")
    for elem in table_elements:
        elem.page = page_num
        elements_by_page.setdefault(page_num, []).append(elem)

    story.append(Spacer(1, 0.2 * inch))
    story.append(table)
    story.append(Spacer(1, 0.2 * inch))

    qid = qid_alloc.allocate("caption", "perf_table")
    caption_text = "Table 1: Performance Metrics Comparison"
    story.append(Paragraph(f"[{qid}]{caption_text}", styles["table_caption"]))
    add_element(ElementManifest(
        eid="t1_caption", type="caption", qid=qid, text=caption_text,
        rendered_text=f"[{qid}]{caption_text}", page=page_num, reading_order=reading_order
    ), page_num)
    reading_order += 1

    # Build PDF
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=1 * inch,
        rightMargin=1 * inch,
        topMargin=1 * inch,
        bottomMargin=1 * inch,
    )
    doc.build(story)

    # Build manifest
    pages = []
    for pn in range(1, page_num + 1):
        pages.append(PageManifest(
            page_num=pn,
            width=letter[0],
            height=letter[1],
            elements=elements_by_page.get(pn, []),
        ))

    return DocumentManifest(
        doc_id=qid_alloc.doc_id,
        preset="technical_report",
        generated_at=datetime.now(timezone.utc).isoformat(),
        seed=seed,
        page_count=page_num,
        pages=pages,
    )


# =============================================================================
# Preset Registry
# =============================================================================

DOCUMENT_PRESETS: Dict[str, Callable] = {
    "engineering_spec": preset_engineering_spec,
    "academic_paper": preset_academic_paper,
    "technical_report": preset_technical_report,
}


# =============================================================================
# Main API
# =============================================================================

def generate_document(
    preset: str,
    output_path: str,
    seed: int = 42,
) -> DocumentManifest:
    """Generate a complete document using a preset.

    Args:
        preset: Name of the document preset
        output_path: Where to write the PDF
        seed: Random seed for determinism

    Returns:
        DocumentManifest with ground truth for all elements
    """
    if preset not in DOCUMENT_PRESETS:
        raise ValueError(f"Unknown preset: {preset}. Available: {list(DOCUMENT_PRESETS.keys())}")

    doc_id = hashlib.md5(f"{preset}:{output_path}:{seed}".encode()).hexdigest()[:8]
    qid_alloc = QidAllocator(doc_id, seed)

    preset_fn = DOCUMENT_PRESETS[preset]
    return preset_fn(output_path, qid_alloc, seed)


def generate_and_save(
    preset: str,
    output_dir: str,
    name: Optional[str] = None,
    seed: int = 42,
) -> tuple[Path, Path]:
    """Generate document PDF and save manifest JSON alongside it.

    Returns:
        (pdf_path, manifest_path)
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if name is None:
        name = f"{preset}_{seed}"

    pdf_path = out_dir / f"{name}.pdf"
    manifest_path = out_dir / f"{name}.manifest.json"

    manifest = generate_document(preset, str(pdf_path), seed)

    # Save manifest
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))

    return pdf_path, manifest_path


def list_presets() -> List[str]:
    """Return list of available document presets."""
    return list(DOCUMENT_PRESETS.keys())


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate complete document presets")
    parser.add_argument("--preset", "-p", default="engineering_spec",
                        choices=list(DOCUMENT_PRESETS.keys()),
                        help="Document preset to generate")
    parser.add_argument("--output", "-o", default="fixtures/document_preset.pdf",
                        help="Output PDF path")
    parser.add_argument("--seed", "-s", type=int, default=42,
                        help="Random seed for determinism")
    parser.add_argument("--list", "-l", action="store_true",
                        help="List available presets")

    args = parser.parse_args()

    if args.list:
        print("Available document presets:")
        for name in DOCUMENT_PRESETS:
            print(f"  - {name}")
        return

    manifest = generate_document(args.preset, args.output, args.seed)

    # Save manifest alongside PDF
    manifest_path = Path(args.output).with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))

    print(f"Generated: {args.output}")
    print(f"Manifest: {manifest_path}")
    print(f"Pages: {manifest.page_count}")
    print(f"Total elements: {manifest.total_elements()}")


if __name__ == "__main__":
    main()
