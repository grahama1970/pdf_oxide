"""ReportLab Canvas renderer. Output IS oracle truth."""
from __future__ import annotations
from io import BytesIO
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors
from .schemas import (
    BBox,
    RenderedWord,
    RenderedLine,
    RenderedBlock,
    PageManifest,
    LayoutProposal,
)
from .synthesizer import SynthesizedBlock
from .line_breaker import break_text_into_lines
from .fonts import register_fonts, resolve_font, classify_font_family
from .schemas import BlockProposal


def _safe_get_cell(cells: list[list[str]] | None, r: int, c: int) -> str:
    """Safely get cell content with full bounds checks."""
    if cells is None:
        return ""
    if r < 0 or r >= len(cells):
        return ""
    row = cells[r]
    if c < 0 or c >= len(row):
        return ""
    return row[c]


def _truncate_cell_text(text: str, max_chars: int, ellipsis: str = "...") -> str:
    """Truncate text to fit cell width, preserving QID marker."""
    if len(text) <= max_chars:
        return text
    # Preserve QID marker if present
    if text.startswith("[QID_"):
        end_bracket = text.find("]")
        if end_bracket > 0 and end_bracket < max_chars - len(ellipsis):
            qid_part = text[:end_bracket + 1]
            remaining = max_chars - len(qid_part) - len(ellipsis)
            if remaining > 0:
                return qid_part + text[end_bracket + 1:end_bracket + 1 + remaining] + ellipsis
    # No QID or no room - simple truncate
    return text[:max_chars - len(ellipsis)] + ellipsis


def _render_table(
    c: canvas.Canvas,
    proposal: LayoutProposal,
    block: BlockProposal,
    synth: SynthesizedBlock,
) -> tuple[RenderedBlock, dict[str, str]]:
    """Render a table block and return RenderedBlock + QID map.

    Uses ReportLab Table with cell-level QID markers.
    Includes bounds checks and text overflow handling per Codex review.
    """
    table_info = block.table
    if not table_info or not synth.table_cells:
        # Fallback: empty table
        return RenderedBlock(
            id=block.id,
            block_type="table",
            lines=[],
            bbox=block.bbox,
            font_family="sans",
            font_size=10.0,
            is_bold=False,
            is_italic=False,
            qid=synth.qid,
            logical_text="[TABLE]",
            rendered_text=f"[{synth.qid}][TABLE]",
        ), {}

    rows = table_info.rows
    cols = table_info.cols

    # Compute column widths based on table bbox
    table_width = block.bbox.width
    col_width = table_width / cols if cols > 0 else 50

    # Estimate max chars per cell (font size 9, ~5pt per char avg)
    max_chars_per_cell = int(col_width / 5) if col_width > 0 else 20

    # Build table data with QID markers in each cell (with bounds checks)
    table_data = []
    for r in range(rows):
        row_data = []
        for col in range(cols):
            cell_text = _safe_get_cell(synth.table_cells, r, col)
            cell_qid = _safe_get_cell(synth.cell_qids, r, col)
            # Prepend QID marker to cell text
            if cell_qid:
                cell_text = f"[{cell_qid}]{cell_text}"
            # Truncate to prevent overflow
            cell_text = _truncate_cell_text(cell_text, max_chars_per_cell)
            row_data.append(cell_text)
        table_data.append(row_data)

    # Create ReportLab Table
    t = Table(table_data, colWidths=[col_width] * cols)

    # Style: grid lines if ruled, header row bold
    style_commands = [
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]

    if table_info.ruled:
        style_commands.extend([
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ])

    # Bold header row
    if rows > 0:
        style_commands.append(('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'))

    t.setStyle(TableStyle(style_commands))

    # Compute table dimensions
    t_width, t_height = t.wrap(table_width, block.bbox.height)

    # Position: convert from PyMuPDF coords (top-left origin) to PDF coords (bottom-left)
    # block.bbox.y0 is top of table in PyMuPDF coords
    # In PDF coords, we need bottom of table
    table_bottom_y = proposal.height - block.bbox.y0 - t_height
    table_x = block.bbox.x0

    # Draw the table
    t.drawOn(c, table_x, table_bottom_y)

    # Build RenderedBlock for manifest
    # Table lines are represented as rows
    rendered_lines = []
    row_height = t_height / rows if rows > 0 else 0
    for r in range(rows):
        row_text = " | ".join(table_data[r])
        rendered_lines.append(RenderedLine(
            id=f"{block.id}_r{r}",
            words=[],  # Tables don't have word-level bbox
            bbox=BBox(
                x0=block.bbox.x0,
                y0=table_bottom_y + (rows - r - 1) * row_height,
                x1=block.bbox.x1,
                y1=table_bottom_y + (rows - r) * row_height,
            ),
            baseline_y=table_bottom_y + (rows - r - 1) * row_height,
        ))

    # Compute rendered bbox
    rendered_bbox = BBox(
        x0=table_x,
        y0=table_bottom_y,
        x1=table_x + t_width,
        y1=table_bottom_y + t_height,
    )

    # Build logical text (all cell content without QIDs)
    logical_parts = []
    for r in range(rows):
        for col in range(cols):
            cell_text = _safe_get_cell(synth.table_cells, r, col)
            logical_parts.append(cell_text)
    logical_text = " ".join(logical_parts)

    return RenderedBlock(
        id=block.id,
        block_type="table",
        lines=rendered_lines,
        bbox=rendered_bbox,
        font_family="sans",
        font_size=9.0,
        is_bold=False,
        is_italic=False,
        qid=synth.qid,
        logical_text=logical_text,
        rendered_text=f"[{synth.qid}]{logical_text}",
    ), {}


def render_page(
    proposal: LayoutProposal,
    synth_blocks: list[SynthesizedBlock],
) -> tuple[bytes, PageManifest]:
    """Render a page and emit manifest. Returns (pdf_bytes, manifest).

    The manifest records EXACTLY what was rendered - this is oracle truth.
    """
    register_fonts()

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(proposal.width, proposal.height))

    rendered_blocks: list[RenderedBlock] = []
    qid_map: dict[str, str] = {}

    for synth in synth_blocks:
        block = synth.proposal

        # Handle tables separately
        if block.block_type == "table" and synth.table_cells:
            rendered_block, table_qids = _render_table(
                c, proposal, block, synth
            )
            rendered_blocks.append(rendered_block)
            qid_map[synth.qid] = block.id
            # Also map cell QIDs
            if synth.cell_qids:
                for r, row_qids in enumerate(synth.cell_qids):
                    for col, cell_qid in enumerate(row_qids):
                        qid_map[cell_qid] = f"{block.id}_r{r}c{col}"
            continue

        text = synth.rendered_text  # includes QID marker

        # Resolve font
        font = resolve_font(
            block.dominant_font,
            block.dominant_size,
            block.is_bold,
            block.is_italic,
        )

        # Break text into lines
        max_width = block.bbox.width
        broken = break_text_into_lines(text, max_width, font)

        # Compute leading
        leading = font.size * 1.2

        # Start position (top of block, PDF coords from bottom)
        # PyMuPDF bbox is top-left origin, PDF is bottom-left
        # block.bbox.y0 is top in PyMuPDF coords
        start_y = proposal.height - block.bbox.y0 - font.size
        x0 = block.bbox.x0

        rendered_lines: list[RenderedLine] = []
        actual_y = start_y

        c.setFont(font.name, font.size)

        for line_idx, broken_line in enumerate(broken.lines):
            baseline_y = actual_y

            # Draw the line
            c.drawString(x0, baseline_y, broken_line.text)

            # Record word positions
            rendered_words: list[RenderedWord] = []
            for word_text, word_x_offset, word_width in broken_line.words:
                word_x = x0 + word_x_offset
                rendered_words.append(RenderedWord(
                    text=word_text,
                    bbox=BBox(
                        x0=word_x,
                        y0=baseline_y,
                        x1=word_x + word_width,
                        y1=baseline_y + font.size,
                    ),
                    baseline_y=baseline_y,
                ))

            line_bbox = BBox(
                x0=x0,
                y0=baseline_y,
                x1=x0 + broken_line.width,
                y1=baseline_y + font.size,
            )

            rendered_lines.append(RenderedLine(
                id=f"{block.id}_l{line_idx}",
                words=rendered_words,
                bbox=line_bbox,
                baseline_y=baseline_y,
            ))

            actual_y -= leading

        # Compute actual block bbox from rendered lines
        if rendered_lines:
            block_bbox = BBox(
                x0=min(l.bbox.x0 for l in rendered_lines),
                y0=min(l.bbox.y0 for l in rendered_lines),
                x1=max(l.bbox.x1 for l in rendered_lines),
                y1=max(l.bbox.y1 for l in rendered_lines),
            )
        else:
            block_bbox = block.bbox

        rendered_blocks.append(RenderedBlock(
            id=block.id,
            block_type=block.block_type,
            lines=rendered_lines,
            bbox=block_bbox,
            font_family=font.family,
            font_size=font.size,
            is_bold=font.bold,
            is_italic=font.italic,
            qid=synth.qid,
            logical_text=synth.logical_text,
            rendered_text=synth.rendered_text,
        ))

        qid_map[synth.qid] = block.id

    c.save()
    pdf_bytes = buf.getvalue()

    manifest = PageManifest(
        page_num=proposal.page_num,
        width=proposal.width,
        height=proposal.height,
        blocks=rendered_blocks,
        qid_map=qid_map,
    )

    return pdf_bytes, manifest
