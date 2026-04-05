"""ArangoDB export — writes document, chunks, and edges.

Embeddings are NOT generated here. Use the "embeddings" plugin instead.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .pipeline_types import PipelineConfig, PipelineResult
from .pipeline_util import log, md5


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

    doc_key = md5(result.source_pdf)

    # Upsert document
    doc_node = {
        "_key": doc_key,
        "source": result.source_pdf,
        "filename": Path(result.source_pdf).name,
        "page_count": result.page_count,
        "profile": result.metadata.get("profile", {}),
        "table_count": len(result.tables),
        "figure_count": len(result.figures),
        "requirement_count": len(result.requirements),
        "extraction_engine": "pdf_oxide",
    }
    db.collection("datalake_docs").insert(doc_node, overwrite=True)

    # Upsert chunks + edges (no embeddings — use embeddings plugin)
    chunk_coll = db.collection("datalake_chunks")
    edge_coll = db.collection("datalake_edges")

    for chunk in chunks:
        chunk["source"] = result.source_pdf
        chunk["content_type"] = "canon"
        chunk_coll.insert(chunk, overwrite=True)

        edge_coll.insert(
            {
                "_from": f"datalake_docs/{doc_key}",
                "_to": f"datalake_chunks/{chunk['_key']}",
                "type": "has_asset",
                "asset_type": chunk["asset_type"],
            },
            overwrite=True,
        )

    return {"synced": True, "doc_key": doc_key, "chunks": len(chunks)}


def _ensure_collections(db: Any) -> None:
    """Create collections if they don't exist."""
    for name in ["datalake_docs", "datalake_chunks"]:
        if not db.has_collection(name):
            db.create_collection(name)
    if not db.has_collection("datalake_edges"):
        db.create_collection("datalake_edges", edge=True)
