"""Step 1: Rust-powered PDF extraction.

Calls pdf_oxide's Rust core for text, tables, figures, sections.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from .pdf_oxide import PdfDocument
from .pipeline_hierarchy import (
    attach_block_ids,
    build_section_tree,
    provenance,
    section_path,
)
from .pipeline_types import PipelineConfig, PipelineResult
from .pipeline_util import (
    assign_section,
    data_to_csv,
    data_to_html,
    md5,
    sha256_file,
)


def extract_content(pdf_path: str, config: PipelineConfig) -> PipelineResult:
    """Run Rust extraction: text, tables, figures, sections."""
    doc = PdfDocument(pdf_path)
    t0 = time.monotonic()

    raw = doc.extract_document(
        detect_figures=True,
        detect_engineering=True,
        normalize_text=True,
        build_sections=True,
        reconcile_tables=config.reconcile_tables,
    )

    pdf_sha256 = sha256_file(pdf_path)
    sections = _build_sections(raw, pdf_sha256)
    blocks = _build_blocks(raw, sections, pdf_sha256)
    attach_block_ids(sections, blocks)
    if config.reconcile_tables and raw.get("tables") is not None:
        # Engine already extracted and reconciled tables against the block
        # stream; consuming them here keeps one source of truth (a separate
        # read_pdf pass would resurface the lossy un-reconciled cell text).
        tables = _tables_from_engine(raw, sections, config, pdf_sha256)
    else:
        tables = _extract_tables(doc, config, sections, pdf_sha256)
    figures = _build_figures(raw, doc, config, sections, pdf_sha256)

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
            "pdf_sha256": pdf_sha256,
        },
        timings={"extraction": elapsed},
    )


def _build_sections(
    raw: Dict[str, Any],
    pdf_sha256: str = "",
) -> List[Dict[str, Any]]:
    """Build sections from Rust-detected sections only.

    Sections come from:
    - PDF outline/bookmarks
    - Font-based heading detection (Rust build_sections)

    Control IDs (AC-1, SI-7, etc.) are NOT sections - they are entities.
    Use /extract-entities for control ID extraction.
    """
    return build_section_tree(raw, pdf_sha256)


def _build_blocks(
    raw: Dict[str, Any],
    sections: List[Dict],
    pdf_sha256: str = "",
) -> List[Dict[str, Any]]:
    blocks = []
    for page_data in raw.get("pages", []):
        page_num = page_data.get("page", 0)
        for blk in page_data.get("blocks", []):
            block_id = md5(f"blk_{page_num}_{blk.get('bbox', ())}")
            assigned_section = assign_section(blk, sections, page_num)
            blocks.append(
                {
                    "id": block_id,
                    "page": page_num,
                    "text": blk.get("text", ""),
                    "type": blk.get("block_type", "text"),
                    "bbox": blk.get("bbox"),
                    "font_size": blk.get("font_size"),
                    "font_name": blk.get("font_name"),
                    "is_bold": blk.get("is_bold", False),
                    # The engine owns this value.  Preserve a missing value as
                    # None rather than laundering it into a high-confidence
                    # default.
                    "confidence": blk.get("confidence"),
                    "header_level": blk.get("header_level"),
                    "section_id": assigned_section,
                    "section_path": section_path(assigned_section, sections),
                    "provenance": provenance(
                        pdf_sha256, page_num, blk.get("bbox")
                    ),
                }
            )
    return blocks


def _tables_from_engine(
    raw: Dict[str, Any],
    sections: List[Dict],
    config: PipelineConfig,
    pdf_sha256: str = "",
) -> List[Dict[str, Any]]:
    """Build table dicts from the engine's reconciled extract_document result."""
    tables = []
    for t in raw.get("tables", []):
        page_num = t.get("page", 0)
        order = t.get("order", 0)
        table_id = md5(f"tbl_{page_num}_{order}_{t.get('bbox', ())}")
        assigned_section = assign_section(t, sections, page_num)
        table_text = data_to_csv(t.get("data", [])) or f"Table page {page_num}"
        tables.append(
            {
                "id": table_id,
                "page": page_num,
                "order": order,
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
                "text": table_text,
                "section_id": assigned_section,
                "section_path": section_path(assigned_section, sections),
                "provenance": provenance(
                    pdf_sha256, page_num, t.get("bbox")
                ),
                "render_ref": {
                    "page": page_num,
                    "bbox": t.get("bbox"),
                },
                "extraction_method": "pdf_oxide",
            }
        )
    return tables


def _extract_tables(
    doc: PdfDocument,
    config: PipelineConfig,
    sections: List[Dict],
    pdf_sha256: str = "",
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
            table_id = md5(f"tbl_{page_num}_{i}_{t.get('bbox', ())}")
            assigned_section = assign_section(t, sections, page_num)
            tbl = {
                "id": table_id,
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
                "text": (
                    data_to_csv(t.get("data", []))
                    or f"Table page {page_num}"
                ),
                "section_id": assigned_section,
                "section_path": section_path(assigned_section, sections),
                "provenance": provenance(
                    pdf_sha256, page_num, t.get("bbox")
                ),
                "render_ref": {
                    "page": page_num,
                    "bbox": t.get("bbox"),
                },
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
    pdf_sha256: str = "",
) -> List[Dict[str, Any]]:
    figures = []
    for fig in raw.get("figures", []):
        page_num = fig.get("page", 0)
        figure_id = md5(f"fig_{page_num}_{fig.get('bbox', ())}")
        assigned_section = assign_section(fig, sections, page_num)
        figure_text_parts = [
            str(value).strip()
            for value in (
                fig.get("caption"),
                fig.get("context_above"),
                fig.get("context_below"),
            )
            if value
        ]
        figure_text_parts.extend(
            str(block.get("text", "")).strip()
            for block in fig.get("content_blocks", [])
            if block.get("text")
        )
        if not figure_text_parts:
            figure_text_parts.append(f"Figure page {page_num}")
        fig_dict = {
            "id": figure_id,
            "page": page_num,
            "bbox": fig.get("bbox"),
            "caption": fig.get("caption"),
            "caption_number": fig.get("caption_number"),
            "context_above": fig.get("context_above", ""),
            "context_below": fig.get("context_below", ""),
            # Figure reconciliation removes these blocks from the page stream.
            # Keep their verbatim text and suppression provenance in the public
            # pipeline result so every absorption remains auditable and
            # character-conserving.
            "content_blocks": fig.get("content_blocks", []),
            "suppressed_table_orders": fig.get("suppressed_table_orders", []),
            "text": "\n".join(figure_text_parts),
            "section_id": assigned_section,
            "section_path": section_path(assigned_section, sections),
            "provenance": provenance(
                pdf_sha256, page_num, fig.get("bbox")
            ),
            "render_ref": {
                "page": page_num,
                "bbox": fig.get("bbox"),
            },
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
