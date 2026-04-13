"""Clone builder — generate PDF from profiler manifest + generated content.

Complete workflow:
1. Profile source PDF → manifest (TOC, table_shapes, page structure)
2. Extract table content (Camelot)
3. Generate similar content (/scillm)
4. Build PDF with QIDs (ReportLab)
5. Output: cloned PDF + QID manifest for validation

The QID manifest is the ground truth — we know exactly what was written.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

from pdf_oxide.clone_v3.content_generator import GeneratedTable
from pdf_oxide.clone_v3.table_extractor import ExtractedTable


# =============================================================================
# QID Allocation
# =============================================================================

class QidCollisionError(ValueError):
    """Raised when a QID collision is detected during build."""
    pass


class QidAllocator:
    """Deterministic QID generator for clone elements.

    Per Codex review:
    - Uses 16 hex chars (64-bit) for visible QID suffix (was 12/48-bit)
    - Detects collisions during build and fails fast
    - Token remains 20-bit for compact representation
    """

    VERSION = "clone_v4"  # Bumped for hash length change

    def __init__(self, doc_id: str, seed: int = 42):
        self.doc_id = doc_id
        self.seed = seed
        self._counter = 0
        self._manifest: Dict[str, str] = {}  # qid -> rendered_text
        self._allocated_qids: set[str] = set()  # Collision detection

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
        # Canonicalize parts to ensure stable serialization
        canonical_parts = "|".join(str(p) for p in parts)
        semantic_key = f"{self.VERSION}|{self.doc_id}|{self.seed}|{element_type}|{canonical_parts}"
        h = hashlib.sha256(semantic_key.encode()).hexdigest()[:16]  # 64-bit (was 48-bit)
        qid = f"QID_{h.upper()}"

        # Collision detection
        if qid in self._allocated_qids:
            raise QidCollisionError(
                f"QID collision detected: {qid} already allocated. "
                f"Semantic key: {semantic_key}"
            )
        self._allocated_qids.add(qid)

        token = int(h[:8], 16) % (2**20)  # 20-bit token (unchanged)
        self._counter += 1
        return qid, token

    def register(self, qid: str, text: str) -> None:
        """Register QID with its rendered text for manifest."""
        self._manifest[qid] = text

    def get_manifest(self) -> Dict[str, str]:
        """Return QID manifest (qid -> text)."""
        return dict(self._manifest)

    @property
    def allocated_count(self) -> int:
        """Number of QIDs allocated."""
        return len(self._allocated_qids)


# =============================================================================
# Style Definitions
# =============================================================================

def get_styles() -> Dict[str, ParagraphStyle]:
    """Standard document styles."""
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


# =============================================================================
# Table Builder
# =============================================================================

def build_table_with_qids(
    table: GeneratedTable,
    qid_alloc: QidAllocator,
    table_id: str,
    style_preset: str = "professional",
) -> Tuple[Table, List[Dict[str, Any]]]:
    """Build ReportLab Table with QIDs embedded in cells.

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


# =============================================================================
# Clone Builder
# =============================================================================

@dataclass
class CloneManifest:
    """Ground truth manifest for cloned PDF."""
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
    """Build cloned PDF from profiler manifest and generated table content.

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
# Full Pipeline
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
    from pdf_oxide.clone_v3.table_extractor import extract_all_tables
    from pdf_oxide.clone_v3.content_generator import generate_all_tables

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
