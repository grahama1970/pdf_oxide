"""Pydantic schemas for clone pipeline."""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Literal


class BBox(BaseModel):
    """Bounding box in PDF coordinates (origin bottom-left)."""
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


# --- Analyzer output (proposal, not truth) ---

class SpanProposal(BaseModel):
    """A text span from PyMuPDF analysis."""
    text: str
    bbox: BBox
    font_name: str
    font_size: float
    flags: int  # PyMuPDF font flags (bold=16, italic=2)

    @property
    def is_bold(self) -> bool:
        return bool(self.flags & 16)

    @property
    def is_italic(self) -> bool:
        return bool(self.flags & 2)


class LineProposal(BaseModel):
    """A text line from PyMuPDF analysis."""
    spans: list[SpanProposal]
    bbox: BBox


class TableCellProposal(BaseModel):
    """A table cell from profiler."""
    row: int
    col: int
    text: str = ""
    bbox: BBox | None = None


class TableProposal(BaseModel):
    """A table from profiler data."""
    rows: int
    cols: int
    bbox: BBox
    ruled: bool = True
    cells: list[TableCellProposal] = Field(default_factory=list)


class BlockProposal(BaseModel):
    """A text block proposal from analyzer."""
    id: str
    block_type: Literal["heading", "paragraph", "header", "footer", "list_item", "table", "unknown"]
    lines: list[LineProposal]
    bbox: BBox
    dominant_font: str
    dominant_size: float
    is_bold: bool
    is_italic: bool
    table: TableProposal | None = None  # populated for table blocks


class LayoutProposal(BaseModel):
    """Analyzer output for a single page."""
    page_num: int
    width: float
    height: float
    blocks: list[BlockProposal]


# --- Renderer output (oracle truth) ---

class RenderedWord(BaseModel):
    """A word as actually rendered."""
    text: str
    bbox: BBox
    baseline_y: float


class RenderedLine(BaseModel):
    """A line as actually rendered."""
    id: str
    words: list[RenderedWord]
    bbox: BBox
    baseline_y: float


class RenderedBlock(BaseModel):
    """A block as actually rendered - THIS IS ORACLE TRUTH."""
    id: str
    block_type: Literal["heading", "paragraph", "header", "footer", "list_item", "table"]
    lines: list[RenderedLine]
    bbox: BBox
    font_family: Literal["serif", "sans", "mono"]
    font_size: float
    is_bold: bool
    is_italic: bool
    qid: str
    logical_text: str   # synthetic content without QID markers
    rendered_text: str  # actual token stream with QID markers


class PageManifest(BaseModel):
    """Render-time truth for a single page."""
    page_num: int
    width: float
    height: float
    blocks: list[RenderedBlock]
    qid_map: dict[str, str] = Field(default_factory=dict)  # qid → block_id


class DocumentManifest(BaseModel):
    """Render-time truth for entire document."""
    source_path: str
    seed: int
    generated_at: str
    corpus_type: Literal["gold", "source_inspired", "round_trip"]
    pages: list[PageManifest]

    @property
    def total_blocks(self) -> int:
        return sum(len(p.blocks) for p in self.pages)

    @property
    def total_qids(self) -> int:
        return sum(len(p.qid_map) for p in self.pages)
