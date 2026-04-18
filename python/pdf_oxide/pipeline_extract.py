"""Step 1: Rust-powered PDF extraction.

Calls pdf_oxide's Rust core for text, tables, figures, sections.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from .pdf_oxide import PdfDocument
from .pipeline_types import PipelineConfig, PipelineResult
from .pipeline_util import assign_section, data_to_csv, data_to_html, md5


def extract_content(pdf_path: str, config: PipelineConfig) -> PipelineResult:
    """Run Rust extraction: text, tables, figures, sections."""
    doc = PdfDocument(pdf_path)
    t0 = time.monotonic()

    raw = doc.extract_document(
        detect_figures=True,
        detect_engineering=True,
        normalize_text=True,
        build_sections=True,
    )

    sections = _build_sections(raw)
    blocks = _build_blocks(raw, sections)
    tables = _extract_tables(doc, config, sections)
    figures = _build_figures(raw, doc, config, sections)

    elapsed = time.monotonic() - t0

    return PipelineResult(
        source_pdf=pdf_path,
        page_count=doc.page_count(),
        sections=sections,
        blocks=blocks,
        tables=tables,
        figures=figures,
        metadata={
            "profile": raw.get("profile", {}),
            "engineering": raw.get("engineering", {}),
            "page_count": doc.page_count(),
        },
        timings={"extraction": elapsed},
    )


def _build_sections(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build sections from Rust-detected sections only.

    Sections come from:
    - PDF outline/bookmarks
    - Font-based heading detection (Rust build_sections)

    Control IDs (AC-1, SI-7, etc.) are NOT sections - they are entities.
    Use /extract-entities for control ID extraction.
    """
    sections = []

    for s in raw.get("sections", []):
        title = s.get("title", "")
        sections.append(
            {
                "id": md5(f"sec_{title}_{s.get('page', 0)}"),
                "title": title,
                "level": s.get("level", 1),
                "page_start": s.get("page", 0),
                "page_end": s.get("page", 0),
                "numbering": s.get("numbering"),
            }
        )

    # Sort by page then title
    sections.sort(key=lambda s: (s["page_start"], s["title"]))
    return sections


def _build_blocks(
    raw: Dict[str, Any], sections: List[Dict]
) -> List[Dict[str, Any]]:
    blocks = []
    for page_data in raw.get("pages", []):
        page_num = page_data.get("page", 0)
        for blk in page_data.get("blocks", []):
            blocks.append(
                {
                    "id": md5(f"blk_{page_num}_{blk.get('bbox', ())}"),
                    "page": page_num,
                    "text": blk.get("text", ""),
                    "type": blk.get("block_type", "text"),
                    "bbox": blk.get("bbox"),
                    "font_size": blk.get("font_size"),
                    "is_bold": blk.get("is_bold", False),
                    "header_level": blk.get("header_level"),
                    "section_id": assign_section(blk, sections, page_num),
                }
            )
    return blocks


def _extract_tables(
    doc: PdfDocument, config: PipelineConfig, sections: List[Dict]
) -> List[Dict[str, Any]]:
    tables = []
    for page_idx in range(doc.page_count()):
        page_tables = doc.read_pdf(
            pages=str(page_idx + 1),
            flavor=config.table_flavor,
            line_scale=config.line_scale,
        )
        for i, t in enumerate(page_tables):
            page_num = t.get("page", page_idx)
            tbl = {
                "id": md5(f"tbl_{page_num}_{i}_{t.get('bbox', ())}"),
                "page": page_num,
                "order": t.get("order", i),
                "bbox": t.get("bbox"),
                "rows": t.get("rows", 0),
                "cols": t.get("cols", 0),
                "accuracy": t.get("accuracy", 0.0),
                "whitespace": t.get("whitespace", 0.0),
                "flavor": t.get("flavor", config.table_flavor),
                "data": t.get("data", []),
                "df_data": t.get("df_data", []),
                "csv_data": data_to_csv(t.get("data", [])),
                "html_data": data_to_html(t.get("data", [])),
                "section_id": assign_section(t, sections, page_num),
                "extraction_method": "pdf_oxide",
            }
            # Render table image for VLM description (if describe plugin enabled)
            if t.get("bbox") and "describe" in config.features:
                try:
                    img_bytes = doc.render_page_clipped(
                        page_num, list(t["bbox"]), dpi=150, format="png"
                    )
                    tbl["_image_bytes"] = img_bytes
                except Exception:
                    pass
            tables.append(tbl)
    return tables


def _build_figures(
    raw: Dict[str, Any],
    doc: PdfDocument,
    config: PipelineConfig,
    sections: List[Dict],
) -> List[Dict[str, Any]]:
    figures = []
    for fig in raw.get("figures", []):
        page_num = fig.get("page", 0)
        fig_dict = {
            "id": md5(f"fig_{page_num}_{fig.get('bbox', ())}"),
            "page": page_num,
            "bbox": fig.get("bbox"),
            "caption": fig.get("caption"),
            "context_above": fig.get("context_above", ""),
            "context_below": fig.get("context_below", ""),
            "section_id": assign_section(fig, sections, page_num),
        }
        # Render figure image for VLM (if describe plugin enabled)
        if fig.get("bbox") and "describe" in config.features:
            try:
                img_bytes = doc.render_page_clipped(
                    page_num, list(fig["bbox"]), dpi=150, format="png"
                )
                fig_dict["_image_bytes"] = img_bytes
            except Exception:
                pass
        figures.append(fig_dict)
    return figures
