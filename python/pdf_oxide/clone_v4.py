"""TOC-driven PDF clone builder for NIST-style documents.

This module generates structurally similar PDFs using:
- TOC manifest as document structure (sections, page spans)
- Text banks for domain-appropriate content
- ReportLab presets for tables, lists, callouts
- QID injection for extraction validation

The goal is NOT identical cloning, but structural similarity with
real text content for testing pdf_oxide extraction accuracy.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

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


# =============================================================================
# Text Bank Loader
# =============================================================================

class TextBank:
    """Loads and indexes text from text banks for content generation."""

    BANK_DIR = Path("/mnt/storage12tb/text_banks")
    DOMAINS = ["nist", "government", "engineering"]

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = random.Random(seed)
        self._chunks: dict[str, list[str]] = {}  # content_type -> texts
        self._loaded = False

    def _load(self) -> None:
        """Load chunks from text banks on first access."""
        if self._loaded:
            return

        for domain in self.DOMAINS:
            bank_file = self.BANK_DIR / f"{domain}.json"
            if not bank_file.exists():
                continue

            try:
                chunks = json.loads(bank_file.read_text())
                for chunk in chunks:
                    text = chunk.get("text", "").strip()
                    if len(text) < 20:
                        continue
                    ct = chunk.get("content_type", "prose")
                    if ct not in self._chunks:
                        self._chunks[ct] = []
                    self._chunks[ct].append(text)
            except Exception:
                continue

        self._loaded = True

    def get_text(self, content_type: str, target_len: int = 200) -> str:
        """Get text of approximately target length."""
        self._load()

        candidates = self._chunks.get(content_type, [])
        if not candidates:
            # Fallback to any available text
            candidates = self._chunks.get("glossary", []) or \
                         self._chunks.get("heading", []) or \
                         ["The organization implements security controls."]

        # Build text to approximate target length
        result = []
        current_len = 0
        attempts = 0

        while current_len < target_len and attempts < 50:
            chunk = self.rng.choice(candidates)
            result.append(chunk)
            current_len += len(chunk) + 1
            attempts += 1

        text = " ".join(result)

        # Trim to approximate length at word boundary
        if len(text) > target_len * 1.3:
            cut = target_len
            while cut < len(text) and text[cut] not in " .,;:":
                cut += 1
            text = text[:cut].rstrip(" .,;:")

        return text

    def get_heading(self, title: str) -> str:
        """Return the section title as-is (preserves TOC structure)."""
        return title.strip()


# =============================================================================
# QID Allocator
# =============================================================================

class QidAllocator:
    """Deterministic QID generator using SHA-256."""

    VERSION = "v4"

    def __init__(self, doc_id: str, seed: int = 42):
        self.doc_id = doc_id
        self.seed = seed
        self._manifest: dict[str, str] = {}  # qid -> text

    def allocate(self, element_type: str, *parts) -> str:
        """Generate deterministic QID for an element."""
        canonical = "|".join(str(p) for p in parts)
        key = f"{self.VERSION}|{self.doc_id}|{self.seed}|{element_type}|{canonical}"
        h = hashlib.sha256(key.encode()).hexdigest()[:16]
        return f"QID_{h.upper()}"

    def register(self, qid: str, text: str) -> None:
        """Register QID with its text for manifest export."""
        self._manifest[qid] = text

    def get_manifest(self) -> dict[str, str]:
        """Return the QID manifest (qid -> text mapping)."""
        return dict(self._manifest)


# =============================================================================
# Section Content Generator
# =============================================================================

@dataclass
class SectionContent:
    """Generated content for a TOC section."""
    title: str
    level: int
    paragraphs: list[str]
    table_data: list[list[str]] | None = None
    qids: list[str] = field(default_factory=list)


def classify_section(title: str) -> str:
    """Determine content type based on section title."""
    title_upper = title.upper()

    if "GLOSSARY" in title_upper or "DEFINITION" in title_upper:
        return "glossary"
    if "TABLE" in title_upper or "LIST OF" in title_upper:
        return "table_cell"
    if "POLICY" in title_upper or "PROCEDURE" in title_upper:
        return "requirement"
    if "CONTROL" in title_upper or any(f"{x}-" in title for x in
        ["AC", "AT", "AU", "CA", "CM", "CP", "IA", "IR", "MA", "MP",
         "PE", "PL", "PM", "PS", "PT", "RA", "SA", "SC", "SI", "SR"]):
        return "requirement"
    if "APPENDIX" in title_upper or "REFERENCE" in title_upper:
        return "glossary"
    return "prose"


def generate_section_content(
    toc_entry: dict,
    text_bank: TextBank,
    qid_allocator: QidAllocator,
    include_table: bool = False,
) -> SectionContent:
    """Generate content for a single TOC section."""
    title = toc_entry["title"]
    level = toc_entry.get("level", 0)
    page = toc_entry.get("page", 1)

    content_type = classify_section(title)
    qids = []

    # Generate heading with QID
    heading_qid = qid_allocator.allocate("heading", page, title[:30])
    qid_allocator.register(heading_qid, title)
    qids.append(heading_qid)

    # Generate paragraphs based on content type and section span
    page_span = max(1, toc_entry.get("page_end", page) - page + 1)
    para_count = min(page_span * 2, 6)  # 2 paragraphs per page, max 6

    paragraphs = []
    for i in range(para_count):
        para_qid = qid_allocator.allocate("paragraph", page, title[:20], i)
        text = text_bank.get_text(content_type, target_len=250)
        qid_allocator.register(para_qid, text[:50])  # Register summary
        paragraphs.append(f"[{para_qid}] {text}")
        qids.append(para_qid)

    # Generate table if requested
    table_data = None
    if include_table:
        rows = 5
        cols = 4
        table_data = []
        # Header row
        headers = []
        for c in range(cols):
            cell_qid = qid_allocator.allocate("table_header", page, c)
            cell_text = text_bank.get_text("heading", target_len=20)[:15]
            qid_allocator.register(cell_qid, cell_text)
            headers.append(f"[{cell_qid}] {cell_text}")
            qids.append(cell_qid)
        table_data.append(headers)

        # Data rows
        for r in range(1, rows):
            row = []
            for c in range(cols):
                cell_qid = qid_allocator.allocate("table_cell", page, r, c)
                cell_text = text_bank.get_text("table_cell", target_len=30)[:25]
                qid_allocator.register(cell_qid, cell_text)
                row.append(f"[{cell_qid}] {cell_text}")
                qids.append(cell_qid)
            table_data.append(row)

    return SectionContent(
        title=f"[{heading_qid}] {title}",
        level=level,
        paragraphs=paragraphs,
        table_data=table_data,
        qids=qids,
    )


# =============================================================================
# PDF Builder
# =============================================================================

def create_styles() -> dict[str, ParagraphStyle]:
    """Create ReportLab paragraph styles for the clone."""
    base = getSampleStyleSheet()

    return {
        "h1": ParagraphStyle(
            "H1",
            parent=base["Heading1"],
            fontSize=16,
            spaceBefore=24,
            spaceAfter=12,
            textColor=colors.HexColor("#003366"),
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontSize=14,
            spaceBefore=18,
            spaceAfter=10,
            textColor=colors.HexColor("#004488"),
        ),
        "h3": ParagraphStyle(
            "H3",
            parent=base["Heading3"],
            fontSize=12,
            spaceBefore=12,
            spaceAfter=8,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
            alignment=TA_JUSTIFY,
            spaceBefore=6,
            spaceAfter=6,
        ),
    }


def build_section_flowables(section: SectionContent, styles: dict) -> list:
    """Convert section content to ReportLab flowables."""
    flowables = []

    # Heading
    level = section.level
    style_key = "h1" if level == 0 else "h2" if level == 1 else "h3"
    flowables.append(Paragraph(section.title, styles[style_key]))

    # Paragraphs
    for para in section.paragraphs:
        flowables.append(Paragraph(para, styles["body"]))

    # Table if present
    if section.table_data:
        table = Table(section.table_data)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        flowables.append(Spacer(1, 12))
        flowables.append(table)

    flowables.append(Spacer(1, 12))
    return flowables


# =============================================================================
# Main Build Functions
# =============================================================================

def load_manifest(path: str | Path) -> dict:
    """Load TOC manifest from JSON file."""
    return json.loads(Path(path).read_text())


def build_clone(
    manifest_path: str | Path,
    output_path: str | Path,
    max_pages: int | None = None,
    seed: int = 42,
) -> tuple[bytes, dict[str, str]]:
    """Build a structurally similar PDF clone from TOC manifest.

    Args:
        manifest_path: Path to the TOC manifest JSON
        output_path: Path to write the output PDF
        max_pages: Limit clone to first N pages of TOC (None = all)
        seed: Random seed for deterministic content generation

    Returns:
        (pdf_bytes, qid_manifest) tuple
    """
    manifest = load_manifest(manifest_path)
    toc = manifest["toc"]
    table_pages = set(t.get("page", 0) for t in manifest.get("table_shapes", []))

    # Initialize generators
    text_bank = TextBank(seed=seed)
    doc_id = Path(manifest.get("source", "unknown")).stem
    qid_allocator = QidAllocator(doc_id=doc_id, seed=seed)
    styles = create_styles()

    # Filter TOC by max_pages
    if max_pages:
        toc = [e for e in toc if e.get("page", 1) <= max_pages]

    # Generate content for each section
    flowables = []
    current_page = 0

    for entry in toc:
        page = entry.get("page", 1)

        # Insert page break when entering a new page (approximate)
        if page > current_page + 2:
            flowables.append(PageBreak())
            current_page = page

        # Generate section content
        include_table = page in table_pages
        section = generate_section_content(
            entry, text_bank, qid_allocator, include_table
        )

        # Convert to flowables
        flowables.extend(build_section_flowables(section, styles))

    # Build PDF
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )
    doc.build(flowables)

    pdf_bytes = buffer.getvalue()

    # Write output file
    output_path = Path(output_path)
    output_path.write_bytes(pdf_bytes)

    return pdf_bytes, qid_allocator.get_manifest()


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import sys

    manifest_path = sys.argv[1] if len(sys.argv) > 1 else \
        "python/pdf_oxide/clone_v4_manifest.json"
    output_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/nist_clone_v4.pdf"
    max_pages = int(sys.argv[3]) if len(sys.argv) > 3 else 50

    pdf_bytes, qid_manifest = build_clone(
        manifest_path=manifest_path,
        output_path=output_path,
        max_pages=max_pages,
        seed=42,
    )

    # Save QID manifest
    qid_path = output_path.replace(".pdf", "_qids.json")
    Path(qid_path).write_text(json.dumps(qid_manifest, indent=2))

    print(f"Clone: {len(pdf_bytes):,} bytes")
    print(f"QIDs: {len(qid_manifest)}")
    print(f"Output: {output_path}")
    print(f"QID manifest: {qid_path}")
