"""Requirements extraction plugin.

Scans Text chunks in ArangoDB for modal verbs (shall, must, will,
should, required) and uses an LLM to extract formal requirements.
Each extracted requirement is written back as a Requirement-type chunk.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, TYPE_CHECKING

from .base import Plugin, registry
from ..pipeline_util import safe_json, log, md5

if TYPE_CHECKING:
    from ..pipeline_types import PipelineConfig, PipelineResult

# Modal verbs that signal a formal requirement
_MODAL_RE = re.compile(
    r"\b(shall|must|will|should|required)\b", re.IGNORECASE
)

_SYSTEM_PROMPT = (
    "You are a requirements engineer. Extract formal requirements "
    "(sentences containing shall/must/will/should) from the given text. "
    'Return JSON: {"requirements": [{"id": null, "text": "...", '
    '"type": "Function", "confidence": 1.0}]}'
)


def _has_requirements(chunk: Dict[str, Any]) -> bool:
    """Return True if the chunk text contains modal requirement verbs."""
    text = chunk.get("text", "")
    return bool(_MODAL_RE.search(text))


class RequirementsPlugin(Plugin):
    name = "requirements"
    depends_on = ("arango",)
    asset_types = ("Text",)
    description = "Extract formal requirements from text chunks"

    async def run_batch(self, result: "PipelineResult", config: "PipelineConfig") -> Dict[str, Any]:
        """Scan Text chunks in ArangoDB, extract requirements via LLM."""
        try:
            from arango import ArangoClient
        except ImportError:
            return {"error": "python-arango not installed"}

        try:
            from openai import AsyncOpenAI
        except ImportError:
            return {"error": "openai package not installed"}

        # Connect to ArangoDB
        client = ArangoClient(hosts=config.arango_url)
        db = client.db(
            config.arango_db,
            username=config.arango_user,
            password=config.arango_pass,
        )

        # Query Text chunks not yet processed for requirements
        if not db.has_collection("datalake_chunks"):
            return {"error": "datalake_chunks collection does not exist"}

        cursor = db.aql.execute(
            """
            FOR c IN datalake_chunks
                FILTER c.asset_type == "Text"
                FILTER c.requirements_processed != true
                RETURN c
            """
        )
        candidates: List[Dict[str, Any]] = [doc for doc in cursor]
        log(f"Requirements: {len(candidates)} unprocessed Text chunks found")

        # Filter for modal keywords
        chunks_with_modals = [c for c in candidates if _has_requirements(c)]
        skipped = len(candidates) - len(chunks_with_modals)
        log(
            f"Requirements: {len(chunks_with_modals)} chunks contain modal "
            f"verbs, {skipped} skipped"
        )

        if not chunks_with_modals:
            return {"extracted": 0, "skipped": skipped, "failed": 0}

        # LLM client
        llm = AsyncOpenAI(
            base_url=f"{config.scillm_api_base}/v1",
            api_key="not-needed",
        )
        semaphore = asyncio.Semaphore(config.text_concurrency)

        chunk_coll = db.collection("datalake_chunks")
        edge_coll = db.collection("datalake_edges")

        extracted = 0
        failed = 0

        async def _process_chunk(chunk: Dict[str, Any]) -> None:
            nonlocal extracted, failed
            async with semaphore:
                try:
                    resp = await llm.chat.completions.create(
                        model=config.text_model,
                        messages=[
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": chunk["text"]},
                        ],
                        temperature=0.0,
                    )
                    raw = resp.choices[0].message.content or ""
                    parsed = safe_json(raw)
                    reqs = parsed.get("requirements", [])

                    source_key = chunk["_key"]
                    doc_key = chunk.get("doc_key", "")

                    for i, req in enumerate(reqs):
                        req_text = req.get("text", "").strip()
                        if not req_text:
                            continue

                        req_key = md5(f"{source_key}:req:{i}")
                        req_chunk = {
                            "_key": req_key,
                            "asset_type": "Requirement",
                            "text": req_text,
                            "requirement_type": req.get("type", "Function"),
                            "confidence": req.get("confidence", 1.0),
                            "source_chunk": source_key,
                            "doc_key": doc_key,
                            "source": chunk.get("source", ""),
                            "page": chunk.get("page"),
                            "content_type": "canon",
                        }
                        chunk_coll.insert(req_chunk, overwrite=True)

                        # Edge from source text chunk to requirement
                        edge_coll.insert(
                            {
                                "_from": f"datalake_chunks/{source_key}",
                                "_to": f"datalake_chunks/{req_key}",
                                "type": "has_requirement",
                            },
                            overwrite=True,
                        )
                        extracted += 1

                    # Mark chunk as processed
                    chunk_coll.update(
                        {"_key": source_key, "requirements_processed": True}
                    )

                except Exception as e:
                    log(f"Requirements: failed on chunk {chunk.get('_key')}: {e}")
                    failed += 1

        tasks = [_process_chunk(c) for c in chunks_with_modals]
        await asyncio.gather(*tasks)

        stats = {"extracted": extracted, "skipped": skipped, "failed": failed}
        log(f"Requirements: {stats}")
        return stats


registry.register(RequirementsPlugin())


# ---------------------------------------------------------------------------
# CLI entry point: python -m pdf_oxide.plugins.requirements
# ---------------------------------------------------------------------------
def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract requirements from Text chunks in ArangoDB"
    )
    parser.add_argument(
        "--doc-id",
        required=True,
        help="Document hash to scope the extraction",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max chunks to process (0 = all)",
    )
    parser.add_argument("--arango-url", default=None)
    parser.add_argument("--arango-db", default=None)
    args = parser.parse_args()

    from ..pipeline_types import PipelineConfig

    config = PipelineConfig()
    if args.arango_url:
        config.arango_url = args.arango_url
    if args.arango_db:
        config.arango_db = args.arango_db

    # Patch AQL query to scope by doc_key and limit
    plugin = registry.get("requirements")
    assert plugin is not None

    # For CLI, override run_batch to add doc_id filter
    original_run_batch = plugin.run_batch

    async def _scoped_batch(cfg: "PipelineConfig") -> Dict[str, Any]:
        try:
            from arango import ArangoClient
        except ImportError:
            return {"error": "python-arango not installed"}

        try:
            from openai import AsyncOpenAI
        except ImportError:
            return {"error": "openai package not installed"}

        client = ArangoClient(hosts=cfg.arango_url)
        db = client.db(
            cfg.arango_db,
            username=cfg.arango_user,
            password=cfg.arango_pass,
        )

        bind = {"doc_key": args.doc_id}
        query = """
            FOR c IN datalake_chunks
                FILTER c.asset_type == "Text"
                FILTER c.requirements_processed != true
                FILTER c.doc_key == @doc_key
        """
        if args.limit > 0:
            query += f"    LIMIT {args.limit}\n"
        query += "    RETURN c"

        cursor = db.aql.execute(query, bind_vars=bind)
        candidates = list(cursor)
        log(f"CLI: {len(candidates)} chunks for doc {args.doc_id}")

        if not candidates:
            return {"extracted": 0, "skipped": 0, "failed": 0}

        chunks_with_modals = [c for c in candidates if _has_requirements(c)]
        skipped = len(candidates) - len(chunks_with_modals)

        if not chunks_with_modals:
            return {"extracted": 0, "skipped": skipped, "failed": 0}

        llm = AsyncOpenAI(
            base_url=f"{cfg.scillm_api_base}/v1",
            api_key="not-needed",
        )
        semaphore = asyncio.Semaphore(cfg.text_concurrency)
        chunk_coll = db.collection("datalake_chunks")
        edge_coll = db.collection("datalake_edges")

        extracted = 0
        failed = 0

        async def _process(chunk: Dict[str, Any]) -> None:
            nonlocal extracted, failed
            async with semaphore:
                try:
                    resp = await llm.chat.completions.create(
                        model=cfg.text_model,
                        messages=[
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": chunk["text"]},
                        ],
                        temperature=0.0,
                    )
                    raw = resp.choices[0].message.content or ""
                    parsed = safe_json(raw)
                    reqs = parsed.get("requirements", [])
                    source_key = chunk["_key"]
                    doc_key = chunk.get("doc_key", "")

                    for i, req in enumerate(reqs):
                        req_text = req.get("text", "").strip()
                        if not req_text:
                            continue
                        req_key = md5(f"{source_key}:req:{i}")
                        req_chunk = {
                            "_key": req_key,
                            "asset_type": "Requirement",
                            "text": req_text,
                            "requirement_type": req.get("type", "Function"),
                            "confidence": req.get("confidence", 1.0),
                            "source_chunk": source_key,
                            "doc_key": doc_key,
                            "source": chunk.get("source", ""),
                            "page": chunk.get("page"),
                            "content_type": "canon",
                        }
                        chunk_coll.insert(req_chunk, overwrite=True)
                        edge_coll.insert(
                            {
                                "_from": f"datalake_chunks/{source_key}",
                                "_to": f"datalake_chunks/{req_key}",
                                "type": "has_requirement",
                            },
                            overwrite=True,
                        )
                        extracted += 1

                    chunk_coll.update(
                        {"_key": source_key, "requirements_processed": True}
                    )
                except Exception as e:
                    log(f"Requirements CLI: failed on {chunk.get('_key')}: {e}")
                    failed += 1

        await asyncio.gather(*[_process(c) for c in chunks_with_modals])
        return {"extracted": extracted, "skipped": skipped, "failed": failed}

    t0 = time.time()
    result = asyncio.run(_scoped_batch(config))
    elapsed = time.time() - t0
    print(f"Requirements extraction complete in {elapsed:.1f}s: {result}")


if __name__ == "__main__":
    _cli()
