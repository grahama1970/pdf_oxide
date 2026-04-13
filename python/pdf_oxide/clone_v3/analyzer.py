"""PyMuPDF-based page analyzer. Output is PROPOSAL, not truth."""
from __future__ import annotations
import fitz
from collections import Counter
from typing import Any
from .schemas import BBox, SpanProposal, LineProposal, BlockProposal, LayoutProposal, TableProposal


def _bbox_from_rect(rect: fitz.Rect) -> BBox:
    """Convert PyMuPDF Rect to BBox."""
    return BBox(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1)


def _classify_block(
    block: BlockProposal,
    page_width: float,
    page_height: float,
    dominant_body_size: float,
) -> str:
    """Classify block type based on heuristics.

    Note: PyMuPDF uses top-left origin (y=0 at top, increases downward).
    """
    bbox = block.bbox
    size = block.dominant_size

    # Header/footer detection by y position
    # PyMuPDF: small y = near top = header, large y = near bottom = footer
    if bbox.y0 < page_height * 0.08:
        return "header"
    if bbox.y0 > page_height * 0.88:
        return "footer"

    # Heading detection
    if size > dominant_body_size * 1.15 and block.is_bold:
        return "heading"
    if size > dominant_body_size * 1.3:
        return "heading"

    # List detection (starts with bullet, number, or letter)
    first_text = ""
    if block.lines and block.lines[0].spans:
        first_text = block.lines[0].spans[0].text.strip()
    if first_text and (
        first_text[0] in "•◦▪-" or
        (len(first_text) > 1 and first_text[0].isdigit() and first_text[1] in ".)")
    ):
        return "list_item"

    return "paragraph"


def analyze_page(
    doc: fitz.Document,
    page_num: int,
    page_tables: list[dict] | None = None,
) -> LayoutProposal:
    """Analyze a single page with PyMuPDF. Returns layout proposal.

    page_tables: list of table_shape dicts from profiler for this page.
    """
    page = doc[page_num]
    width = page.rect.width
    height = page.rect.height

    # Get text as dict: blocks -> lines -> spans
    text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)

    # Collect all font sizes to find dominant body size
    all_sizes: list[float] = []
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # skip image blocks
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                all_sizes.append(span.get("size", 11))

    size_counts = Counter(round(s, 1) for s in all_sizes)
    dominant_body_size = size_counts.most_common(1)[0][0] if size_counts else 11.0

    blocks: list[BlockProposal] = []
    block_idx = 0

    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # skip image blocks
            continue

        block_bbox = BBox(
            x0=block["bbox"][0],
            y0=block["bbox"][1],
            x1=block["bbox"][2],
            y1=block["bbox"][3],
        )

        lines: list[LineProposal] = []
        font_sizes: list[float] = []
        font_names: list[str] = []
        bold_count = 0
        italic_count = 0
        span_count = 0

        for line in block.get("lines", []):
            line_bbox = BBox(
                x0=line["bbox"][0],
                y0=line["bbox"][1],
                x1=line["bbox"][2],
                y1=line["bbox"][3],
            )

            spans: list[SpanProposal] = []
            for span in line.get("spans", []):
                span_bbox = BBox(
                    x0=span["bbox"][0],
                    y0=span["bbox"][1],
                    x1=span["bbox"][2],
                    y1=span["bbox"][3],
                )
                flags = span.get("flags", 0)
                sp = SpanProposal(
                    text=span.get("text", ""),
                    bbox=span_bbox,
                    font_name=span.get("font", ""),
                    font_size=span.get("size", 11),
                    flags=flags,
                )
                spans.append(sp)

                font_sizes.append(sp.font_size)
                font_names.append(sp.font_name)
                if sp.is_bold:
                    bold_count += 1
                if sp.is_italic:
                    italic_count += 1
                span_count += 1

            if spans:
                lines.append(LineProposal(spans=spans, bbox=line_bbox))

        if not lines:
            continue

        # Compute dominant font for block
        dominant_font = Counter(font_names).most_common(1)[0][0] if font_names else ""
        dominant_size = Counter(round(s, 1) for s in font_sizes).most_common(1)[0][0] if font_sizes else 11.0
        is_bold = bold_count > span_count / 2
        is_italic = italic_count > span_count / 2

        bp = BlockProposal(
            id=f"p{page_num + 1}_b{block_idx}",
            block_type="unknown",
            lines=lines,
            bbox=block_bbox,
            dominant_font=dominant_font,
            dominant_size=dominant_size,
            is_bold=is_bold,
            is_italic=is_italic,
        )

        # Classify
        bp.block_type = _classify_block(bp, width, height, dominant_body_size)
        blocks.append(bp)
        block_idx += 1

    # Inject table blocks from profiler data
    if page_tables:
        for t_idx, ts in enumerate(page_tables):
            table_block = _create_table_block(page_num + 1, t_idx, ts, height)
            blocks.append(table_block)

    return LayoutProposal(
        page_num=page_num + 1,
        width=width,
        height=height,
        blocks=blocks,
    )


def analyze_document(
    pdf_path: str,
    pages: list[int] | None = None,
    profile: dict[str, Any] | None = None,
) -> list[LayoutProposal]:
    """Analyze multiple pages. pages is 1-indexed; None means all.

    If profile is provided (from clone_profiler), table blocks are injected.
    """
    doc = fitz.open(pdf_path)
    if pages is None:
        pages = list(range(1, doc.page_count + 1))

    # Build table lookup by page (0-indexed)
    table_by_page: dict[int, list[dict]] = {}
    if profile:
        for ts in profile.get("table_shapes", []):
            pg = ts["page"]  # 0-indexed from profiler
            table_by_page.setdefault(pg, []).append(ts)

    proposals = []
    for page_num in pages:
        page_idx = page_num - 1  # 0-indexed
        page_tables = table_by_page.get(page_idx, [])
        proposal = analyze_page(doc, page_idx, page_tables)
        proposals.append(proposal)

    return proposals


def _create_table_block(
    page_num: int,
    table_idx: int,
    table_shape: dict,
    page_height: float,
) -> BlockProposal:
    """Create a table BlockProposal from profiler table_shape."""
    bbox_raw = table_shape["bbox"]  # [x0, y0, x1, y1] in PDF coords

    table_proposal = TableProposal(
        rows=table_shape["rows"],
        cols=table_shape["cols"],
        bbox=BBox(x0=bbox_raw[0], y0=bbox_raw[1], x1=bbox_raw[2], y1=bbox_raw[3]),
        ruled=table_shape.get("ruled", True),
    )

    return BlockProposal(
        id=f"p{page_num}_t{table_idx}",
        block_type="table",
        lines=[],  # tables don't have lines in the normal sense
        bbox=BBox(x0=bbox_raw[0], y0=bbox_raw[1], x1=bbox_raw[2], y1=bbox_raw[3]),
        dominant_font="",
        dominant_size=10.0,
        is_bold=False,
        is_italic=False,
        table=table_proposal,
    )
