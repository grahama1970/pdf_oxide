"""Step 3: Flatten extracted content into datalake_chunks format."""
from __future__ import annotations

from typing import Any, Dict, List

from .pipeline_types import PipelineResult
from .pipeline_util import md5


def flatten(result: PipelineResult) -> List[Dict[str, Any]]:
    """Flatten all assets into datalake_chunks format."""
    doc_key = md5(result.source_pdf)
    chunks: List[Dict[str, Any]] = []

    _flatten_blocks(chunks, result.blocks, doc_key)
    _flatten_tables(chunks, result.tables, doc_key)
    _flatten_figures(chunks, result.figures, doc_key)
    _flatten_requirements(chunks, result.requirements, doc_key)

    return chunks


def _flatten_blocks(
    chunks: List[Dict], blocks: List[Dict], doc_key: str
) -> None:
    for blk in blocks:
        text = blk.get("text", "").strip()
        if not text or len(text) < 10:
            continue
        chunks.append(
            {
                "_key": md5(f"{doc_key}_{blk['id']}"),
                "doc_id": doc_key,
                "text": text,
                "asset_type": "Text",
                "source_meta": {
                    "page": blk.get("page", 0),
                    "bbox": blk.get("bbox"),
                    "section_id": blk.get("section_id"),
                    "block_type": blk.get("type"),
                    "font_size": blk.get("font_size"),
                },
            }
        )


def _flatten_tables(
    chunks: List[Dict], tables: List[Dict], doc_key: str
) -> None:
    for tbl in tables:
        title = tbl.get("ai_title", f"Table p{tbl.get('page', 0)}")
        text = f"Table: {title}\n{tbl.get('html_data', '')}"
        chunks.append(
            {
                "_key": md5(f"{doc_key}_{tbl['id']}"),
                "doc_id": doc_key,
                "text": text,
                "asset_type": "Table",
                "source_meta": {
                    "table_id": tbl["id"],
                    "caption": title,
                    "html": tbl.get("html_data"),
                    "bbox": tbl.get("bbox"),
                    "extraction_method": "pdf_oxide",
                    "accuracy": tbl.get("accuracy"),
                    "whitespace": tbl.get("whitespace"),
                    "rows": tbl.get("rows"),
                    "cols": tbl.get("cols"),
                    "page": tbl.get("page", 0),
                    "section_id": tbl.get("section_id"),
                    "ai_description": tbl.get("ai_description"),
                },
            }
        )


def _flatten_figures(
    chunks: List[Dict], figures: List[Dict], doc_key: str
) -> None:
    for fig in figures:
        title = fig.get(
            "ai_title",
            fig.get("caption", f"Figure p{fig.get('page', 0)}"),
        )
        desc = fig.get("ai_description", "")
        text = f"Figure: {title}\n{desc}"
        chunks.append(
            {
                "_key": md5(f"{doc_key}_{fig['id']}"),
                "doc_id": doc_key,
                "text": text,
                "asset_type": "Figure",
                "source_meta": {
                    "figure_id": fig["id"],
                    "caption": title,
                    "bbox": fig.get("bbox"),
                    "page": fig.get("page", 0),
                    "section_id": fig.get("section_id"),
                    "ai_description": desc,
                },
            }
        )


def _flatten_requirements(
    chunks: List[Dict], requirements: List[Dict], doc_key: str
) -> None:
    for req in requirements:
        req_id = req.get("req_id") or req["id"]
        text = f"Requirement {req_id}: {req.get('text', '')}"
        chunks.append(
            {
                "_key": md5(f"{doc_key}_{req['id']}"),
                "doc_id": doc_key,
                "text": text,
                "asset_type": "Requirement",
                "source_meta": {
                    "req_id": req_id,
                    "type": req.get("type"),
                    "confidence": req.get("confidence"),
                    "source": req.get("source"),
                    "page": req.get("page", 0),
                    "section_id": req.get("section_id"),
                },
            }
        )
