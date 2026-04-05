"""Lean4 prove plugin.

Proves requirements via Lean4 theorem prover. Operates in batch mode
against ArangoDB — queries Requirement chunks that lack lean4_status,
sends each to the lean4-prove service, and updates chunks with results.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, TYPE_CHECKING

from .base import Plugin, registry
from ..pipeline_util import log

if TYPE_CHECKING:
    from ..pipeline_types import PipelineConfig, PipelineResult

LEAN4_PROVE_URL = os.getenv("LEAN4_PROVE_URL", "http://localhost:8604/prove")


class Lean4Plugin(Plugin):
    name = "lean4"
    depends_on = ("requirements",)
    asset_types = ("Requirement",)
    description = "Prove requirements via Lean4 theorem prover"

    async def run_batch(self, result: "PipelineResult", config: "PipelineConfig") -> Dict[str, Any]:
        """Prove unprocesed Requirement chunks via the lean4-prove service."""
        try:
            from arango import ArangoClient
        except ImportError:
            log("python-arango not installed -- skipping lean4 prove")
            return {"skipped": True, "reason": "python-arango not installed"}

        try:
            import httpx
        except ImportError:
            log("httpx not installed -- skipping lean4 prove")
            return {"skipped": True, "reason": "httpx not installed"}

        client = ArangoClient(hosts=config.arango_url)
        db = client.db(
            config.arango_db,
            username=config.arango_user,
            password=config.arango_pass,
        )

        if not db.has_collection("datalake_chunks"):
            log("datalake_chunks collection not found -- nothing to prove")
            return {"skipped": True, "reason": "no chunks collection"}

        chunks = _query_unproved(db)
        if not chunks:
            log("No Requirement chunks need proving")
            return {"proved": 0, "failed": 0, "skipped": 0}

        log(f"Lean4: {len(chunks)} requirements to prove")

        semaphore = asyncio.Semaphore(2)
        stats: Dict[str, int] = {"proved": 0, "failed": 0, "skipped": 0}

        async with httpx.AsyncClient(timeout=120) as http:
            tasks = [
                _prove_one(http, semaphore, db, chunk, stats)
                for chunk in chunks
            ]
            await asyncio.gather(*tasks)

        log(
            f"Lean4 done: {stats['proved']} proved, "
            f"{stats['failed']} failed, {stats['skipped']} skipped"
        )
        return stats


def _query_unproved(db: Any, doc_id: str | None = None, limit: int = 0) -> List[Dict[str, Any]]:
    """Query Requirement chunks without lean4_status."""
    bind: Dict[str, Any] = {}
    filters = [
        "c.asset_type == 'Requirement'",
        "c.source_meta.lean4_status == null",
    ]
    if doc_id:
        filters.append("c.source_meta.doc_key == @doc_id")
        bind["doc_id"] = doc_id

    aql = (
        "FOR c IN datalake_chunks\n"
        f"  FILTER {' AND '.join(filters)}\n"
    )
    if limit > 0:
        aql += f"  LIMIT {limit}\n"
    aql += "  RETURN c"

    cursor = db.aql.execute(aql, bind_vars=bind)
    return list(cursor)


async def _prove_one(
    http: Any,
    semaphore: asyncio.Semaphore,
    db: Any,
    chunk: Dict[str, Any],
    stats: Dict[str, int],
) -> None:
    """Send a single requirement to the lean4-prove service and update."""
    req_text = chunk.get("text", "")
    if not req_text.strip():
        stats["skipped"] += 1
        return

    async with semaphore:
        try:
            resp = await http.post(
                LEAN4_PROVE_URL,
                json={
                    "requirement": req_text,
                    "max_retries": 3,
                    "candidates": 5,
                },
            )
            resp.raise_for_status()
            result = resp.json()
        except Exception as e:
            log(f"Lean4 prove error for {chunk['_key']}: {e}")
            stats["failed"] += 1
            _update_chunk(db, chunk["_key"], {
                "lean4_status": "error",
                "lean4_code": "",
                "lean4_attempts": 0,
            })
            return

    success = result.get("success", False)
    update = {
        "lean4_status": "proved" if success else "failed",
        "lean4_code": result.get("code", ""),
        "lean4_attempts": result.get("attempts", 0),
    }

    if success:
        stats["proved"] += 1
    else:
        stats["failed"] += 1

    _update_chunk(db, chunk["_key"], update)


def _update_chunk(db: Any, key: str, lean4_fields: Dict[str, Any]) -> None:
    """Merge lean4 results into chunk source_meta."""
    db.collection("datalake_chunks").update(
        {
            "_key": key,
            "source_meta": lean4_fields,
        },
        merge=True,
    )


registry.register(Lean4Plugin())


# ── CLI for standalone batch runs ──────────────────────────────────

if __name__ == "__main__":
    import argparse

    from ..pipeline_types import PipelineConfig

    parser = argparse.ArgumentParser(
        description="Lean4 prove batch: prove Requirement chunks"
    )
    parser.add_argument("--doc-id", required=True, help="Document hash key")
    parser.add_argument(
        "--limit", type=int, default=0, help="Max requirements to prove (0 = all)"
    )
    args = parser.parse_args()

    async def _main() -> None:
        try:
            from arango import ArangoClient
            import httpx  # noqa: F401
        except ImportError as e:
            log(f"Missing dependency: {e}")
            return

        config = PipelineConfig()
        client = ArangoClient(hosts=config.arango_url)
        db = client.db(
            config.arango_db,
            username=config.arango_user,
            password=config.arango_pass,
        )

        chunks = _query_unproved(db, doc_id=args.doc_id, limit=args.limit)
        if not chunks:
            log("No Requirement chunks to prove")
            return

        log(f"Lean4 CLI: {len(chunks)} requirements to prove")

        semaphore = asyncio.Semaphore(2)
        stats: Dict[str, int] = {"proved": 0, "failed": 0, "skipped": 0}

        async with httpx.AsyncClient(timeout=120) as http:
            tasks = [
                _prove_one(http, semaphore, db, chunk, stats)
                for chunk in chunks
            ]
            await asyncio.gather(*tasks)

        log(
            f"Lean4 CLI done: {stats['proved']} proved, "
            f"{stats['failed']} failed, {stats['skipped']} skipped"
        )

    asyncio.run(_main())
