"""Embeddings plugin.

Generates embeddings for chunks stored in ArangoDB by querying
the embedding service in batches and updating chunk documents.
"""
from __future__ import annotations

from typing import Any, Dict, List, TYPE_CHECKING

from .base import Plugin, registry
from ..pipeline_util import log

if TYPE_CHECKING:
    from ..pipeline_types import PipelineConfig, PipelineResult

BATCH_SIZE = 64


class EmbeddingsPlugin(Plugin):
    name = "embeddings"
    depends_on = ("arango",)
    asset_types = ("Text", "Table", "Figure", "Requirement")
    description = "Generate embeddings for chunks"

    async def run_batch(self, result: "PipelineResult", config: "PipelineConfig") -> Dict[str, Any]:
        """Query chunks with zero/null embeddings and generate vectors."""
        try:
            from arango import ArangoClient
        except ImportError:
            log("python-arango not installed -- skipping embeddings")
            return {"embedded": 0, "failed": 0, "skipped": 0}

        try:
            import httpx
        except ImportError:
            log("httpx not installed -- skipping embeddings")
            return {"embedded": 0, "failed": 0, "skipped": 0}

        client = ArangoClient(hosts=config.arango_url)
        db = client.db(
            config.arango_db,
            username=config.arango_user,
            password=config.arango_pass,
        )

        if not db.has_collection("datalake_chunks"):
            log("datalake_chunks collection not found -- nothing to embed")
            return {"embedded": 0, "failed": 0, "skipped": 0}

        # Find chunks needing embeddings: null, missing, or all-zero vectors
        cursor = db.aql.execute(
            """
            FOR c IN datalake_chunks
                FILTER c.asset_type IN @types
                FILTER c.embedding == null
                    OR LENGTH(c.embedding) == 0
                    OR SUM(FOR v IN (c.embedding || []) RETURN ABS(v)) == 0
                RETURN { _key: c._key, text: c.text }
            """,
            bind_vars={"types": self.asset_types},
        )
        pending: List[Dict[str, str]] = [doc for doc in cursor]

        if not pending:
            log("No chunks need embeddings")
            return {"embedded": 0, "failed": 0, "skipped": len(pending)}

        log(f"Generating embeddings for {len(pending)} chunks")

        stats: Dict[str, int] = {"embedded": 0, "failed": 0, "skipped": 0}
        chunk_coll = db.collection("datalake_chunks")

        for offset in range(0, len(pending), BATCH_SIZE):
            batch = pending[offset : offset + BATCH_SIZE]
            texts = [doc["text"] or "" for doc in batch]

            vectors = await _embed_batch(texts, config)

            for i, doc in enumerate(batch):
                if i < len(vectors) and vectors[i] is not None:
                    chunk_coll.update(
                        {"_key": doc["_key"], "embedding": vectors[i]}
                    )
                    stats["embedded"] += 1
                else:
                    stats["failed"] += 1

        log(
            f"Embeddings complete: {stats['embedded']} embedded, "
            f"{stats['failed']} failed"
        )
        return stats


async def _embed_batch(
    texts: List[str], config: "PipelineConfig"
) -> List[Any]:
    """Call embedding service. Returns zero vectors on failure."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{config.embedding_url}/embed/batch",
                json={"texts": texts},
            )
            resp.raise_for_status()
            return resp.json().get("vectors", [])
    except Exception as e:
        log(f"Embedding service unavailable ({e}) -- using zero vectors")
        return [[0.0] * config.embedding_dim for _ in texts]


registry.register(EmbeddingsPlugin())
