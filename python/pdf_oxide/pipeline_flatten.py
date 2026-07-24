"""Step 3: Flatten extracted content into datalake_chunks format."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from .pipeline_util import md5


if TYPE_CHECKING:
    from .pipeline_types import PipelineResult


def flatten(result: PipelineResult) -> List[Dict[str, Any]]:
    """Flatten all assets into datalake_chunks format."""
    doc_key = md5(result.source_pdf)
    section_paths = {
        section["id"]: section.get("section_path", "")
        for section in result.sections
    }
    chunks: List[Dict[str, Any]] = []

    _flatten_blocks(chunks, result.blocks, doc_key, section_paths)
    _flatten_tables(chunks, result.tables, doc_key, section_paths)
    _flatten_figures(chunks, result.figures, doc_key, section_paths)
    _flatten_requirements(
        chunks, result.requirements, doc_key, section_paths
    )

    return chunks


def _flatten_blocks(
    chunks: List[Dict],
    blocks: List[Dict],
    doc_key: str,
    section_paths: Dict[str, str],
) -> None:
    for blk in blocks:
        text = blk.get("text", "").strip()
        if not text or len(text) < 10:
            continue
        section_id = blk.get("section_id")
        path = blk.get("section_path") or section_paths.get(section_id, "")
        element_id = blk["id"]
        chunks.append(
            {
                "_key": md5(f"{doc_key}_{blk['id']}"),
                "doc_id": doc_key,
                "element_id": element_id,
                "element_refs": [element_id],
                "text": text,
                "asset_type": "Text",
                "section_id": section_id,
                "section_path": path,
                "provenance": blk.get("provenance"),
                "page_image_refs": blk.get("page_image_refs", []),
                "page_image_sha256": blk.get("page_image_sha256", {}),
                "source_meta": {
                    "element_id": element_id,
                    "page": blk.get("page", 0),
                    "bbox": blk.get("bbox"),
                    "section_id": section_id,
                    "section_path": path,
                    "provenance": blk.get("provenance"),
                    "page_image_refs": blk.get("page_image_refs", []),
                    "page_image_sha256": blk.get("page_image_sha256", {}),
                    "block_type": blk.get("type"),
                    "font_size": blk.get("font_size"),
                },
            }
        )


def _flatten_tables(
    chunks: List[Dict],
    tables: List[Dict],
    doc_key: str,
    section_paths: Dict[str, str],
) -> None:
    for tbl in tables:
        title = tbl.get("ai_title", f"Table p{tbl.get('page', 0)}")
        text = f"Table: {title}\n{tbl.get('html_data', '')}"
        section_id = tbl.get("section_id")
        path = tbl.get("section_path") or section_paths.get(section_id, "")
        element_id = tbl["id"]
        render_ref = tbl.get("render_ref") or {
            "page": tbl.get("page", 0),
            "bbox": tbl.get("bbox"),
        }
        chunks.append(
            {
                "_key": md5(f"{doc_key}_{tbl['id']}"),
                "doc_id": doc_key,
                "element_id": element_id,
                "element_refs": [element_id],
                "text": text,
                "asset_type": "Table",
                "section_id": section_id,
                "section_path": path,
                "provenance": tbl.get("provenance"),
                "page_image_refs": tbl.get("page_image_refs", []),
                "page_image_sha256": tbl.get("page_image_sha256", {}),
                "render_ref": render_ref,
                "source_meta": {
                    "element_id": element_id,
                    "table_id": element_id,
                    "caption": title,
                    "html": tbl.get("html_data"),
                    "bbox": tbl.get("bbox"),
                    "extraction_method": "pdf_oxide",
                    "accuracy": tbl.get("accuracy"),
                    "whitespace": tbl.get("whitespace"),
                    "rows": tbl.get("rows"),
                    "cols": tbl.get("cols"),
                    "page": tbl.get("page", 0),
                    "section_id": section_id,
                    "section_path": path,
                    "provenance": tbl.get("provenance"),
                    "page_image_refs": tbl.get("page_image_refs", []),
                    "page_image_sha256": tbl.get("page_image_sha256", {}),
                    "render_ref": render_ref,
                    "ai_description": tbl.get("ai_description"),
                },
            }
        )


def _flatten_figures(
    chunks: List[Dict],
    figures: List[Dict],
    doc_key: str,
    section_paths: Dict[str, str],
) -> None:
    for fig in figures:
        title = fig.get(
            "ai_title",
            fig.get("caption", f"Figure p{fig.get('page', 0)}"),
        )
        desc = fig.get("ai_description", "")
        text = f"Figure: {title}\n{desc}"
        section_id = fig.get("section_id")
        path = fig.get("section_path") or section_paths.get(section_id, "")
        element_id = fig["id"]
        render_ref = fig.get("render_ref") or {
            "page": fig.get("page", 0),
            "bbox": fig.get("bbox"),
        }
        chunks.append(
            {
                "_key": md5(f"{doc_key}_{fig['id']}"),
                "doc_id": doc_key,
                "element_id": element_id,
                "element_refs": [element_id],
                "text": text,
                "asset_type": "Figure",
                "section_id": section_id,
                "section_path": path,
                "provenance": fig.get("provenance"),
                "page_image_refs": fig.get("page_image_refs", []),
                "page_image_sha256": fig.get("page_image_sha256", {}),
                "render_ref": render_ref,
                "source_meta": {
                    "element_id": element_id,
                    "figure_id": element_id,
                    "caption": title,
                    "bbox": fig.get("bbox"),
                    "page": fig.get("page", 0),
                    "section_id": section_id,
                    "section_path": path,
                    "provenance": fig.get("provenance"),
                    "page_image_refs": fig.get("page_image_refs", []),
                    "page_image_sha256": fig.get("page_image_sha256", {}),
                    "render_ref": render_ref,
                    "ai_description": desc,
                },
            }
        )


def _flatten_requirements(
    chunks: List[Dict],
    requirements: List[Dict],
    doc_key: str,
    section_paths: Dict[str, str],
) -> None:
    for req in requirements:
        req_id = req.get("req_id") or req["id"]
        text = f"Requirement {req_id}: {req.get('text', '')}"
        section_id = req.get("section_id")
        path = req.get("section_path") or section_paths.get(section_id, "")
        element_id = req["id"]
        chunks.append(
            {
                "_key": md5(f"{doc_key}_{req['id']}"),
                "doc_id": doc_key,
                "element_id": element_id,
                "element_refs": [element_id],
                "text": text,
                "asset_type": "Requirement",
                "section_id": section_id,
                "section_path": path,
                "provenance": req.get("provenance"),
                "page_image_refs": req.get("page_image_refs", []),
                "page_image_sha256": req.get("page_image_sha256", {}),
                "source_meta": {
                    "element_id": element_id,
                    "req_id": req_id,
                    "type": req.get("type"),
                    "confidence": req.get("confidence"),
                    "source": req.get("source"),
                    "page": req.get("page", 0),
                    "section_id": section_id,
                    "section_path": path,
                    "provenance": req.get("provenance"),
                    "page_image_refs": req.get("page_image_refs", []),
                    "page_image_sha256": req.get("page_image_sha256", {}),
                },
            }
        )
