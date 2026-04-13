"""Clone builder — preset-driven PDF assembly with TruthManifest output.

This is the unified clone builder that provides:
- BaseDocTemplate + PageTemplate from presets
- Header/footer callbacks from presets
- Integration with clone_sampler regions for section budgeting
- TruthManifest output for structural validation

Also maintains backward-compatible exports for the table-centric workflow.

Usage:
    from pdf_oxide.clone import CloneBuilder, derive_render_plan, SourceProfileRef
    from pdf_oxide.clone_profiler import profile_for_cloning

    profile = profile_for_cloning("source.pdf")
    plan = derive_render_plan(SourceProfileRef(profile), seed=42)

    builder = CloneBuilder(plan)
    manifest = builder.build("output.pdf")
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.pdfgen.canvas import Canvas

from pdf_oxide.clone.clone_types import (
    BlockType,
    PageRegime,
    PageType,
    RenderPlan,
    SectionBudget,
    TruthManifest,
    TruthObject,
)
from pdf_oxide.clone.content_generator import GeneratedTable
from pdf_oxide.clone.table_extractor import ExtractedTable
from pdf_oxide.presets import (
    TABLE_PRESETS,
    build_table,
    TableSpec,
)


# =============================================================================
# QID Allocation
# =============================================================================

class QidCollisionError(ValueError):
    """Raised when a QID collision is detected during build."""
    pass


class QidAllocator:
    """Deterministic QID generator for clone elements.

    Uses SHA-256 with 16 hex chars (64-bit) for visible QID suffix.
    Detects collisions during build and fails fast.
    """

    VERSION = "clone"

    def __init__(self, doc_id: str, seed: int = 42):
        self.doc_id = doc_id
        self.seed = seed
        self._counter = 0
        self._manifest: Dict[str, str] = {}  # qid -> rendered_text (for legacy compat)
        self._allocated_qids: set[str] = set()

    def allocate(self, element_type: str, *parts) -> Tuple[str, int]:
        """Generate QID and token with collision detection.

        Args:
            element_type: Type of element (heading, table, cell, etc.)
            *parts: Additional identifying parts for semantic key

        Returns:
            (qid_string, qid_token) tuple

        Raises:
            QidCollisionError: If generated QID collides with existing one
        """
        canonical_parts = "|".join(str(p) for p in parts)
        semantic_key = f"{self.VERSION}|{self.doc_id}|{self.seed}|{element_type}|{canonical_parts}"
        h = hashlib.sha256(semantic_key.encode()).hexdigest()[:16]
        qid = f"QID_{h.upper()}"

        if qid in self._allocated_qids:
            raise QidCollisionError(
                f"QID collision detected: {qid} already allocated. "
                f"Semantic key: {semantic_key}"
            )
        self._allocated_qids.add(qid)

        token = int(h[:8], 16) % (2**20)
        self._counter += 1
        return qid, token

    def register(self, qid: str, text: str) -> None:
        """Register QID with its rendered text for manifest (legacy compat)."""
        self._manifest[qid] = text

    def get_manifest(self) -> Dict[str, str]:
        """Return QID manifest (qid -> text) for legacy compat."""
        return dict(self._manifest)

    @property
    def allocated_count(self) -> int:
        return len(self._allocated_qids)


# =============================================================================
# Page Type to Template Mapping
# =============================================================================

PAGE_TYPE_TO_TEMPLATE: Dict[PageType, str] = {
    PageType.FRONT_MATTER: "toc_page",
    PageType.BODY_TEXT: "standard_page",
    PageType.TABLE_HEAVY: "standard_page",
    PageType.FIGURE_HEAVY: "standard_page",
    PageType.MIXED: "standard_page",
    PageType.APPENDIX: "appendix_page",
    PageType.BLANK: "blank_page",
}


# =============================================================================
# Clone Builder (TruthManifest output)
# =============================================================================

class CloneBuilder:
    """Preset-driven PDF builder with TruthManifest output.

    Uses the presets infrastructure for:
    - Page templates (margins, columns)
    - Header/footer callbacks
    - Table styling

    Produces TruthManifest with:
    - Every QID allocated and its rendered position
    - Table structure (rows × cols × cell QIDs)
    - Section hierarchy
    - Page-level QID ordering
    """

    def __init__(
        self,
        plan: RenderPlan,
        header_preset: str = "doc_title_header",
        footer_preset: str = "page_number_footer",
        table_preset: str = "data_grid",
    ):
        """Initialize builder with render plan.

        Args:
            plan: RenderPlan from derive_render_plan()
            header_preset: Preset name for running headers
            footer_preset: Preset name for running footers
            table_preset: Preset name for tables
        """
        self.plan = plan
        self.header_preset = header_preset
        self.footer_preset = footer_preset
        self.table_preset = table_preset

        self._qid_alloc = QidAllocator(plan.doc_id, plan.seed)
        self._manifest: Optional[TruthManifest] = None
        self._current_page = 0
        self._sequence_num = 0
        self._styles = self._build_styles()

    def _build_styles(self) -> Dict[str, ParagraphStyle]:
        """Build paragraph styles."""
        base = getSampleStyleSheet()
        return {
            "title": ParagraphStyle(
                "title",
                parent=base["Title"],
                fontSize=18,
                spaceAfter=18,
                fontName="Helvetica-Bold",
            ),
            "h1": ParagraphStyle(
                "h1",
                parent=base["Heading1"],
                fontSize=14,
                spaceBefore=14,
                spaceAfter=8,
                fontName="Helvetica-Bold",
            ),
            "h2": ParagraphStyle(
                "h2",
                parent=base["Heading2"],
                fontSize=12,
                spaceBefore=10,
                spaceAfter=6,
                fontName="Helvetica-Bold",
            ),
            "h3": ParagraphStyle(
                "h3",
                parent=base["Heading3"],
                fontSize=11,
                spaceBefore=8,
                spaceAfter=4,
                fontName="Helvetica-Bold",
            ),
            "body": ParagraphStyle(
                "body",
                parent=base["Normal"],
                fontSize=10,
                spaceAfter=6,
            ),
            "toc_entry": ParagraphStyle(
                "toc_entry",
                parent=base["Normal"],
                fontSize=10,
                spaceBefore=2,
            ),
            "toc_indent": ParagraphStyle(
                "toc_indent",
                parent=base["Normal"],
                fontSize=10,
                spaceBefore=1,
                leftIndent=18,
            ),
            "caption": ParagraphStyle(
                "caption",
                parent=base["Normal"],
                fontSize=9,
                spaceBefore=4,
                spaceAfter=12,
            ),
        }

    def _register_truth(
        self,
        qid: str,
        block_type: BlockType,
        logical_text: str,
        table_id: Optional[str] = None,
        row: Optional[int] = None,
        col: Optional[int] = None,
        section_id: Optional[int] = None,
        depth: Optional[int] = None,
    ) -> str:
        """Register a truth object and return rendered text."""
        rendered = f"[{qid}]{logical_text}"

        obj = TruthObject(
            qid=qid,
            block_type=block_type,
            logical_text=logical_text,
            rendered_text=rendered,
            page_num=self._current_page,
            sequence_num=self._sequence_num,
            table_id=table_id,
            row=row,
            col=col,
            section_id=section_id,
            depth=depth,
        )
        self._manifest.register(obj)
        self._sequence_num += 1

        return rendered

    def _build_heading(
        self,
        text: str,
        depth: int,
        section_id: Optional[int] = None,
    ) -> Paragraph:
        """Build a heading paragraph with QID."""
        qid, _ = self._qid_alloc.allocate("heading", section_id or self._sequence_num)
        rendered = self._register_truth(
            qid,
            BlockType.HEADING,
            text,
            section_id=section_id,
            depth=depth,
        )

        style_name = f"h{min(depth + 1, 3)}"
        return Paragraph(rendered, self._styles[style_name])

    def _build_paragraph(self, text: str) -> Paragraph:
        """Build a body paragraph with QID."""
        qid, _ = self._qid_alloc.allocate("paragraph", self._sequence_num)
        rendered = self._register_truth(qid, BlockType.PARAGRAPH, text)
        return Paragraph(rendered, self._styles["body"])

    def _build_toc_entry(
        self,
        title: str,
        page_num: int,
        depth: int,
        section_id: int,
    ) -> Paragraph:
        """Build a TOC entry with QID."""
        qid, _ = self._qid_alloc.allocate("toc_entry", section_id)

        # Format: title dots page_number
        dots = "." * max(1, 50 - len(title))
        display_text = f"{title} {dots} {page_num + 1}"

        rendered = self._register_truth(
            qid,
            BlockType.TOC_ENTRY,
            title,  # logical text is just the title
            section_id=section_id,
            depth=depth,
        )

        # Register section in hierarchy
        self._manifest.register_section(section_id, title, depth, qid)

        style = self._styles["toc_indent"] if depth > 0 else self._styles["toc_entry"]
        return Paragraph(rendered.replace(title, display_text), style)

    def _build_table(
        self,
        headers: List[str],
        data: List[List[str]],
        table_id: str,
    ) -> Tuple[Table, List[List[str]]]:
        """Build a table with QIDs in cells using preset styling.

        Returns:
            (Table flowable, cell_qids grid for manifest)
        """
        cell_qids: List[List[str]] = []

        # Build header row
        header_row = []
        header_qids = []
        for col_idx, header in enumerate(headers):
            qid, _ = self._qid_alloc.allocate("header", table_id, 0, col_idx)
            rendered = self._register_truth(
                qid,
                BlockType.TABLE_HEADER,
                header,
                table_id=table_id,
                row=0,
                col=col_idx,
            )
            header_row.append(rendered)
            header_qids.append(qid)
        cell_qids.append(header_qids)

        # Build data rows
        table_data = [header_row]
        for row_idx, row in enumerate(data):
            data_row = []
            row_qids = []
            for col_idx, cell in enumerate(row):
                qid, _ = self._qid_alloc.allocate("cell", table_id, row_idx + 1, col_idx)
                rendered = self._register_truth(
                    qid,
                    BlockType.TABLE_CELL,
                    cell,
                    table_id=table_id,
                    row=row_idx + 1,
                    col=col_idx,
                )
                data_row.append(rendered)
                row_qids.append(qid)
            table_data.append(data_row)
            cell_qids.append(row_qids)

        # Build table with consistent styling
        tbl = Table(table_data)
        n_rows = len(table_data)

        style_cmds = [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9E2F3")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#6B7280")),
            ("LINEBELOW", (0, -1), (-1, -1), 0.8, colors.HexColor("#6B7280")),
            ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#6B7280")),
        ]

        # Zebra striping
        for i in range(2, n_rows, 2):
            style_cmds.append(
                ("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FAFAFA"))
            )

        tbl.setStyle(TableStyle(style_cmds))

        return tbl, cell_qids

    def _build_caption(self, text: str, table_idx: int) -> Paragraph:
        """Build a table caption with QID."""
        qid, _ = self._qid_alloc.allocate("caption", table_idx)
        rendered = self._register_truth(qid, BlockType.CAPTION, text)
        return Paragraph(rendered, self._styles["caption"])

    def build(
        self,
        output_path: str,
        content_generator: Optional[Callable[[SectionBudget], Dict[str, Any]]] = None,
    ) -> TruthManifest:
        """Build the cloned PDF and return TruthManifest.

        Args:
            output_path: Where to write the PDF
            content_generator: Optional callback to generate content for each section.
                Signature: fn(budget: SectionBudget) -> {"paragraphs": [...], "tables": [...]}
                If None, generates placeholder content.

        Returns:
            TruthManifest with ground truth for validation
        """
        # Initialize manifest
        self._manifest = TruthManifest(
            doc_id=self.plan.doc_id,
            source_path=self.plan.source_path,
            output_path=output_path,
            seed=self.plan.seed,
        )

        # Get document title
        doc_title = Path(self.plan.source_path).stem if self.plan.source_path else "Document"

        # Create document using SimpleDocTemplate
        # TODO: Upgrade to BaseDocTemplate with preset page templates once
        # the onPageEnd issue is resolved in the presets module
        doc = SimpleDocTemplate(
            output_path,
            pagesize=letter,
            leftMargin=0.7 * inch,
            rightMargin=0.7 * inch,
            topMargin=0.8 * inch,
            bottomMargin=0.7 * inch,
        )

        # Build story
        story = []

        # Title
        qid, _ = self._qid_alloc.allocate("title", 0)
        rendered_title = self._register_truth(qid, BlockType.TITLE, doc_title)
        story.append(Paragraph(rendered_title, self._styles["title"]))
        story.append(Spacer(1, 0.3 * inch))

        # TOC if we have sections
        if self.plan.section_budgets:
            qid, _ = self._qid_alloc.allocate("toc_header", 0)
            rendered = self._register_truth(qid, BlockType.TOC_HEADER, "Table of Contents")
            story.append(Paragraph(rendered, self._styles["h1"]))
            story.append(Spacer(1, 0.1 * inch))

            for budget in self.plan.section_budgets[:30]:  # Limit TOC entries
                entry = self._build_toc_entry(
                    budget.title,
                    budget.start_page,
                    budget.depth,
                    budget.section_id,
                )
                story.append(entry)

            story.append(PageBreak())
            self._current_page += 1
            self._sequence_num = 0

        # Build sections
        table_idx = 0
        for budget in self.plan.section_budgets:
            # Section heading
            heading = self._build_heading(budget.title, budget.depth, budget.section_id)
            story.append(heading)
            story.append(Spacer(1, 0.1 * inch))

            # Get content for section
            if content_generator:
                content = content_generator(budget)
            else:
                content = self._generate_placeholder_content(budget)

            # Add paragraphs
            for para_text in content.get("paragraphs", []):
                story.append(self._build_paragraph(para_text))

            # Add tables
            for table_data in content.get("tables", []):
                table_id = f"t{table_idx}"
                headers = table_data.get("headers", ["Column A", "Column B"])
                rows = table_data.get("rows", [["Data 1", "Data 2"]])

                tbl, cell_qids = self._build_table(headers, rows, table_id)
                story.append(tbl)
                story.append(Spacer(1, 0.05 * inch))

                # Register table structure
                self._manifest.register_table_structure(
                    table_id=table_id,
                    rows=len(rows) + 1,  # +1 for header
                    cols=len(headers),
                    cell_qids=cell_qids,
                )

                # Caption
                caption = self._build_caption(
                    f"Table {table_idx + 1}: Data from section {budget.section_id}",
                    table_idx,
                )
                story.append(caption)
                story.append(Spacer(1, 0.15 * inch))

                table_idx += 1

            # Page break between major sections
            if budget.page_span > 1:
                story.append(PageBreak())
                self._current_page += 1
                self._sequence_num = 0

        # Build PDF
        doc.build(story)

        return self._manifest

    def _generate_placeholder_content(
        self,
        budget: SectionBudget,
    ) -> Dict[str, Any]:
        """Generate placeholder content based on section budget."""
        content: Dict[str, Any] = {"paragraphs": [], "tables": []}

        # Placeholder paragraphs
        for i in range(min(budget.paragraph_count, 3)):
            content["paragraphs"].append(
                f"This is placeholder paragraph {i + 1} for section '{budget.title}'. "
                f"Content type: {budget.content_type}, domain: {budget.domain}."
            )

        # Placeholder tables
        for i in range(budget.table_count):
            content["tables"].append({
                "headers": ["ID", "Description", "Status"],
                "rows": [
                    [f"ITEM-{j:03d}", f"Description for item {j}", "Active"]
                    for j in range(1, 6)
                ],
            })

        return content


# =============================================================================
# Convenience Functions
# =============================================================================

def build_clone(
    plan: RenderPlan,
    output_path: str,
    content_generator: Optional[Callable[[SectionBudget], Dict[str, Any]]] = None,
    header_preset: str = "doc_title_header",
    footer_preset: str = "page_number_footer",
) -> TruthManifest:
    """Build a cloned PDF from a render plan.

    Args:
        plan: RenderPlan from derive_render_plan()
        output_path: Where to write the PDF
        content_generator: Optional callback for section content
        header_preset: Running header preset name
        footer_preset: Running footer preset name

    Returns:
        TruthManifest for validation
    """
    builder = CloneBuilder(
        plan,
        header_preset=header_preset,
        footer_preset=footer_preset,
    )
    return builder.build(output_path, content_generator)


# =============================================================================
# Legacy Support (CloneManifest-based workflow)
# =============================================================================

def get_styles() -> Dict[str, ParagraphStyle]:
    """Standard document styles (legacy compat)."""
    base = getSampleStyleSheet()

    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"],
            fontSize=18, alignment=TA_CENTER, spaceAfter=18,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Heading1"],
            fontSize=14, spaceBefore=14, spaceAfter=8, fontName="Helvetica-Bold",
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"],
            fontSize=12, spaceBefore=10, spaceAfter=6, fontName="Helvetica-Bold",
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"],
            fontSize=11, spaceBefore=8, spaceAfter=4, fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"],
            fontSize=10, alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "caption": ParagraphStyle(
            "caption", parent=base["Normal"],
            fontSize=9, alignment=TA_CENTER, spaceBefore=4, spaceAfter=12,
        ),
        "toc": ParagraphStyle(
            "toc", parent=base["Normal"],
            fontSize=10, spaceBefore=2, leftIndent=0,
        ),
        "toc_indent": ParagraphStyle(
            "toc_indent", parent=base["Normal"],
            fontSize=10, spaceBefore=1, leftIndent=18,
        ),
    }


def build_table_with_qids(
    table: GeneratedTable,
    qid_alloc: QidAllocator,
    table_id: str,
    style_preset: str = "professional",
) -> Tuple[Table, List[Dict[str, Any]]]:
    """Build ReportLab Table with QIDs embedded in cells (legacy compat).

    Returns:
        (Table flowable, list of cell manifests)
    """
    cell_manifests = []

    # Build header row with QIDs
    header_row = []
    for col_idx, header in enumerate(table.headers):
        qid, token = qid_alloc.allocate("header", table_id, 0, col_idx)
        rendered = f"[{qid}]{header}"
        header_row.append(rendered)
        qid_alloc.register(qid, header)
        cell_manifests.append({
            "qid": qid, "token": token, "text": header, "rendered": rendered,
            "row": 0, "col": col_idx, "is_header": True,
        })

    data = [header_row]

    # Build data rows with QIDs
    for row_idx, row in enumerate(table.data):
        data_row = []
        for col_idx, cell in enumerate(row):
            qid, token = qid_alloc.allocate("cell", table_id, row_idx + 1, col_idx)
            rendered = f"[{qid}]{cell}"
            data_row.append(rendered)
            qid_alloc.register(qid, cell)
            cell_manifests.append({
                "qid": qid, "token": token, "text": cell, "rendered": rendered,
                "row": row_idx + 1, "col": col_idx, "is_header": False,
            })
        data.append(data_row)

    # Style commands
    n_rows = len(data)
    style_cmds = [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]

    if style_preset == "professional":
        style_cmds.extend([
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.2, 0.3, 0.4)),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('LINEBELOW', (0, 0), (-1, 0), 2, colors.Color(0.1, 0.2, 0.3)),
            ('LINEBELOW', (0, -1), (-1, -1), 1, colors.black),
        ])
        for i in range(1, n_rows - 1):
            style_cmds.append(
                ('LINEBELOW', (0, i), (-1, i), 0.25, colors.Color(0.85, 0.85, 0.85))
            )
    elif style_preset == "grid":
        style_cmds.extend([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ])
    elif style_preset == "zebra":
        style_cmds.extend([
            ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.3, 0.3, 0.5)),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ])
        for i in range(2, n_rows, 2):
            style_cmds.append(
                ('BACKGROUND', (0, i), (-1, i), colors.Color(0.92, 0.92, 0.96))
            )
    else:  # minimal
        style_cmds.extend([
            ('LINEABOVE', (0, 0), (-1, 0), 1, colors.black),
            ('LINEBELOW', (0, 0), (-1, 0), 0.5, colors.black),
            ('LINEBELOW', (0, -1), (-1, -1), 1, colors.black),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ])

    tbl = Table(data)
    tbl.setStyle(TableStyle(style_cmds))

    return tbl, cell_manifests


@dataclass
class CloneManifest:
    """Ground truth manifest for cloned PDF (legacy format)."""
    doc_id: str
    source_path: str
    generated_at: str
    seed: int
    page_count: int
    tables: List[Dict[str, Any]]
    toc_sections: List[Dict[str, Any]]
    qid_manifest: Dict[str, str]  # qid -> text

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "source_path": self.source_path,
            "generated_at": self.generated_at,
            "seed": self.seed,
            "page_count": self.page_count,
            "tables": self.tables,
            "toc_sections": self.toc_sections,
            "qid_manifest": self.qid_manifest,
            "total_qids": len(self.qid_manifest),
        }

    def save(self, path: str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))


def build_clone_from_generated(
    source_profile: Dict[str, Any],
    generated_tables: List[GeneratedTable],
    output_path: str,
    seed: int = 42,
    table_style: str = "professional",
) -> CloneManifest:
    """Build cloned PDF from profiler manifest and generated table content (legacy).

    Args:
        source_profile: Output from clone_profiler.profile_for_cloning()
        generated_tables: Tables with generated content
        output_path: Where to write PDF
        seed: Random seed for QID generation
        table_style: Table style preset

    Returns:
        CloneManifest with ground truth
    """
    doc_id = hashlib.md5(f"{output_path}:{seed}".encode()).hexdigest()[:8]
    qid_alloc = QidAllocator(doc_id, seed)
    styles = get_styles()

    story = []
    table_manifests = []

    # Title
    doc_title = Path(source_profile.get("path", "Document")).stem
    qid, _ = qid_alloc.allocate("title", 0)
    story.append(Paragraph(f"[{qid}]{doc_title}", styles["title"]))
    qid_alloc.register(qid, doc_title)
    story.append(Spacer(1, 0.3 * inch))

    # TOC
    toc_sections = source_profile.get("toc_sections", [])
    if toc_sections:
        qid, _ = qid_alloc.allocate("toc_header", 0)
        story.append(Paragraph(f"[{qid}]Table of Contents", styles["h1"]))
        qid_alloc.register(qid, "Table of Contents")
        story.append(Spacer(1, 0.1 * inch))

        for section in toc_sections[:20]:  # Limit TOC entries
            title = section.get("title", "")
            page = section.get("page", 0)
            depth = section.get("depth", 0)

            qid, _ = qid_alloc.allocate("toc_entry", section.get("id", 0))
            style = styles["toc_indent"] if depth > 0 else styles["toc"]
            text = f"{title} {'.' * max(1, 50 - len(title))} {page + 1}"
            story.append(Paragraph(f"[{qid}]{text}", style))
            qid_alloc.register(qid, title)

        story.append(PageBreak())

    # Group tables by page
    tables_by_page: Dict[int, List[GeneratedTable]] = {}
    for table in generated_tables:
        tables_by_page.setdefault(table.page, []).append(table)

    # Build pages with sections and tables
    current_page = 0
    section_idx = 0
    table_idx = 0

    for page_num in sorted(tables_by_page.keys()):
        # Add section heading if we have one for this page
        while section_idx < len(toc_sections):
            section = toc_sections[section_idx]
            section_page = section.get("page", 0)
            if section_page is None or section_page > page_num:
                break

            if section_page == page_num or section_page == page_num - 1:
                title = section.get("title", "")
                depth = section.get("depth", 0)
                qid, _ = qid_alloc.allocate("heading", section.get("id", section_idx))

                style_name = f"h{min(depth + 1, 3)}"
                story.append(Paragraph(f"[{qid}]{title}", styles[style_name]))
                qid_alloc.register(qid, title)
                story.append(Spacer(1, 0.1 * inch))

            section_idx += 1

        # Add tables for this page
        for table in tables_by_page[page_num]:
            table_id = f"t{table_idx}"

            # Build table with QIDs
            tbl, cell_manifests = build_table_with_qids(
                table, qid_alloc, table_id, table_style
            )

            story.append(tbl)
            story.append(Spacer(1, 0.1 * inch))

            # Caption
            qid, _ = qid_alloc.allocate("caption", table_idx)
            caption_text = f"Table {table_idx + 1}: Generated from page {page_num + 1}"
            story.append(Paragraph(f"[{qid}]{caption_text}", styles["caption"]))
            qid_alloc.register(qid, caption_text)

            story.append(Spacer(1, 0.2 * inch))

            table_manifests.append({
                "table_id": table_id,
                "source_page": page_num,
                "rows": table.rows,
                "cols": table.cols,
                "cells": cell_manifests,
            })

            table_idx += 1

        # Page break between source pages
        if page_num < max(tables_by_page.keys()):
            story.append(PageBreak())

        current_page = page_num

    # Build PDF
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )
    doc.build(story)

    # Count pages (approximate based on content)
    page_count = max(1, len(tables_by_page) + (1 if toc_sections else 0))

    return CloneManifest(
        doc_id=doc_id,
        source_path=source_profile.get("path", ""),
        generated_at=datetime.now(timezone.utc).isoformat(),
        seed=seed,
        page_count=page_count,
        tables=table_manifests,
        toc_sections=[s for s in toc_sections[:20]],
        qid_manifest=qid_alloc.get_manifest(),
    )


# =============================================================================
# Full Pipeline (legacy async interface)
# =============================================================================

async def clone_pdf(
    source_pdf: str,
    output_pdf: str,
    seed: int = 42,
    table_style: str = "professional",
    model: str = "text",
) -> CloneManifest:
    """Complete clone pipeline: profile → extract → generate → build.

    Args:
        source_pdf: Path to source PDF
        output_pdf: Path for cloned PDF
        seed: Random seed for determinism
        table_style: Table style preset
        model: scillm model for content generation

    Returns:
        CloneManifest with ground truth
    """
    from pdf_oxide.clone_profiler import profile_for_cloning
    from pdf_oxide.clone.table_extractor import extract_all_tables
    from pdf_oxide.clone.content_generator import generate_all_tables

    # Step 1: Profile
    print(f"[1/4] Profiling {source_pdf}...")
    profile = profile_for_cloning(source_pdf)
    table_shapes = profile.get("table_shapes", [])

    if not table_shapes:
        print("No tables found in source PDF")
        # Build minimal clone with just TOC
        return build_clone_from_generated(profile, [], output_pdf, seed, table_style)

    # Step 2: Extract
    print(f"[2/4] Extracting {len(table_shapes)} tables...")
    extracted = extract_all_tables(source_pdf, table_shapes)

    # Step 3: Generate
    print(f"[3/4] Generating similar content via scillm...")
    generated = await generate_all_tables(
        extracted,
        profile.get("toc_sections"),
        model=model,
    )

    # Step 4: Build
    print(f"[4/4] Building cloned PDF...")
    manifest = build_clone_from_generated(
        profile, generated, output_pdf, seed, table_style
    )

    print(f"Done! Wrote {output_pdf}")
    print(f"  Tables: {len(manifest.tables)}")
    print(f"  QIDs: {len(manifest.qid_manifest)}")

    return manifest


def clone_pdf_sync(
    source_pdf: str,
    output_pdf: str,
    seed: int = 42,
    table_style: str = "professional",
    model: str = "text",
) -> CloneManifest:
    """Synchronous wrapper for clone_pdf."""
    import asyncio
    return asyncio.run(clone_pdf(source_pdf, output_pdf, seed, table_style, model))


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Clone PDF with generated content")
    parser.add_argument("source", help="Source PDF path")
    parser.add_argument("--output", "-o", help="Output PDF path")
    parser.add_argument("--seed", "-s", type=int, default=42, help="Random seed")
    parser.add_argument("--style", default="professional",
                        choices=["professional", "grid", "zebra", "minimal"],
                        help="Table style")
    parser.add_argument("--model", "-m", default="text", help="scillm model")
    parser.add_argument("--manifest", action="store_true", help="Save manifest JSON")

    args = parser.parse_args()

    # Default output path
    if not args.output:
        source = Path(args.source)
        args.output = str(source.parent / f"{source.stem}_clone.pdf")

    # Run clone
    manifest = clone_pdf_sync(
        args.source,
        args.output,
        seed=args.seed,
        table_style=args.style,
        model=args.model,
    )

    # Save manifest
    if args.manifest:
        manifest_path = Path(args.output).with_suffix(".manifest.json")
        manifest.save(str(manifest_path))
        print(f"Manifest: {manifest_path}")
