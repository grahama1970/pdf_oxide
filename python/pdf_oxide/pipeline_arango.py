"""ArangoDB export — writes document, chunks, and edges.

Embeddings are NOT generated here. Use the "embeddings" plugin instead.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from .pipeline_util import log, md5


if TYPE_CHECKING:
    from .pipeline_types import PipelineConfig, PipelineResult


async def export_arango(
    result: PipelineResult,
    chunks: List[Dict[str, Any]],
    config: PipelineConfig,
) -> Dict[str, Any]:
    """Write document + chunks + edges to ArangoDB."""
    try:
        from arango import ArangoClient
    except ImportError:
        log("python-arango not installed -- skipping ArangoDB export")
        return {"synced": False, "reason": "python-arango not installed"}

    client = ArangoClient(hosts=config.arango_url)
    db = client.db(
        config.arango_db,
        username=config.arango_user,
        password=config.arango_pass,
    )

    _ensure_collections(db)
    graph = _build_graph_records(result, chunks)
    doc_node = graph["document"]
    doc_key = doc_node["_key"]
    db.collection("datalake_docs").insert(doc_node, overwrite=True)

    section_coll = db.collection("datalake_sections")
    element_coll = db.collection("datalake_elements")
    chunk_coll = db.collection("datalake_chunks")
    edge_coll = db.collection("datalake_edges")

    for section in graph["sections"]:
        section_coll.insert(section, overwrite=True)
    for element in graph["elements"]:
        element_coll.insert(element, overwrite=True)
    for chunk in chunks:
        chunk["source"] = result.source_pdf
        chunk["content_type"] = "canon"
        chunk_coll.insert(chunk, overwrite=True)
    for edge in graph["edges"]:
        edge_coll.insert(edge, overwrite=True)

    return {
        "synced": True,
        "doc_key": doc_key,
        "sections": len(graph["sections"]),
        "elements": len(graph["elements"]),
        "chunks": len(chunks),
        "edges": len(graph["edges"]),
    }


def _edge(
    from_id: str,
    to_id: str,
    edge_type: str,
    **fields: Any,
) -> Dict[str, Any]:
    return {
        "_key": md5(f"{from_id}|{edge_type}|{to_id}"),
        "_from": from_id,
        "_to": to_id,
        "type": edge_type,
        **fields,
    }


def _build_graph_records(
    result: PipelineResult,
    chunks: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build deterministic document, section, element, and edge records."""
    doc_key = md5(result.source_pdf)
    doc_ref = f"datalake_docs/{doc_key}"
    document = {
        "_key": doc_key,
        "source": result.source_pdf,
        "filename": Path(result.source_pdf).name,
        "pdf_sha256": result.metadata.get("pdf_sha256"),
        "page_count": result.page_count,
        "profile": result.metadata.get("profile", {}),
        "section_count": len(result.sections),
        "table_count": len(result.tables),
        "figure_count": len(result.figures),
        "requirement_count": len(result.requirements),
        "extraction_engine": "pdf_oxide",
        "page_images": result.metadata.get("page_images"),
    }

    section_keys = {
        section["id"]: md5(f"{doc_key}_section_{section['id']}")
        for section in result.sections
    }
    sections = []
    edges: List[Dict[str, Any]] = []
    for section in result.sections:
        section_key = section_keys[section["id"]]
        section_ref = f"datalake_sections/{section_key}"
        sections.append(
            {
                "_key": section_key,
                "doc_id": doc_key,
                **section,
            }
        )
        edges.append(
            _edge(
                doc_ref,
                section_ref,
                "has_section",
                doc_order=section.get("doc_order"),
            )
        )
        parent_id = section.get("parent_id")
        if parent_id in section_keys:
            edges.append(
                _edge(
                    f"datalake_sections/{section_keys[parent_id]}",
                    section_ref,
                    "has_child",
                    doc_order=section.get("doc_order"),
                )
            )

    elements = []
    for asset_type, source_elements in (
        ("Text", result.blocks),
        ("Table", result.tables),
        ("Figure", result.figures),
        ("Requirement", result.requirements),
    ):
        for element in source_elements:
            element_id = element["id"]
            element_key = md5(f"{doc_key}_element_{element_id}")
            element_ref = f"datalake_elements/{element_key}"
            elements.append(
                {
                    "_key": element_key,
                    "doc_id": doc_key,
                    "id": element_id,
                    "element_type": asset_type,
                    "text": element.get("text", ""),
                    "section_id": element.get("section_id"),
                    "section_path": element.get("section_path", ""),
                    "provenance": element.get("provenance"),
                    "page_image_refs": element.get("page_image_refs", []),
                    "page_image_sha256": element.get("page_image_sha256", {}),
                    "render_ref": element.get("render_ref"),
                }
            )
            section_id = element.get("section_id")
            if section_id in section_keys:
                edges.append(
                    _edge(
                        element_ref,
                        f"datalake_sections/{section_keys[section_id]}",
                        "in_section",
                        element_id=element_id,
                        element_type=asset_type,
                    )
                )

    element_keys = {
        element["id"]: element["_key"] for element in elements
    }
    for chunk in chunks:
        chunk_ref = f"datalake_chunks/{chunk['_key']}"
        edges.append(
            _edge(
                doc_ref,
                chunk_ref,
                "has_asset",
                asset_type=chunk["asset_type"],
            )
        )
        element_key = element_keys.get(chunk.get("element_id"))
        if element_key is not None:
            edges.append(
                _edge(
                    chunk_ref,
                    f"datalake_elements/{element_key}",
                    "represents_element",
                )
            )

    return {
        "document": document,
        "sections": sections,
        "elements": elements,
        "edges": edges,
    }


def _ensure_collections(db: Any) -> None:
    """Create collections if they don't exist."""
    for name in [
        "datalake_docs",
        "datalake_sections",
        "datalake_elements",
        "datalake_chunks",
    ]:
        if not db.has_collection(name):
            db.create_collection(name)
    if not db.has_collection("datalake_edges"):
        db.create_collection("datalake_edges", edge=True)
