"""VLM description plugin — batch-enriches Table/Figure chunks in ArangoDB.

Queries unenriched chunks (missing ai_description) and sends content to a
VLM endpoint to generate titles and descriptions.  Tables use their HTML
representation; Figures use surrounding text context.

Usage as plugin:
    PipelineConfig(features=["arango", "describe"])

Usage as CLI:
    python -m pdf_oxide.plugins.describe --asset-type Table --limit 10
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .base import Plugin, registry
from ..pipeline_util import log, safe_json

if TYPE_CHECKING:
    from ..pipeline_types import PipelineConfig, PipelineResult


class DescribePlugin(Plugin):
    """Batch VLM description of Table and Figure chunks."""

    name = "describe"
    depends_on = ("arango",)
    asset_types = ("Table", "Figure")
    description = "Generate AI titles and descriptions for tables and figures"

    async def run_batch(self, result: "PipelineResult", config: "PipelineConfig") -> Dict[str, Any]:
        """Query ArangoDB for unenriched Table/Figure chunks, describe via VLM.

        Returns:
            Dict with keys: enriched, skipped, failed, elapsed_sec
        """
        try:
            from arango import ArangoClient
        except ImportError:
            return {"error": "python-arango not installed"}

        try:
            from openai import AsyncOpenAI
        except ImportError:
            return {"error": "openai package not installed"}

        t0 = time.monotonic()

        db = ArangoClient(hosts=config.arango_url).db(
            config.arango_db,
            username=config.arango_user,
            password=config.arango_pass,
        )
        client = AsyncOpenAI(
            base_url=f"{config.scillm_api_base}/v1", api_key="not-needed"
        )

        # Build AQL query for unenriched Table/Figure chunks
        filters = ["c.source_meta.ai_description == null"]
        bind: Dict[str, Any] = {}

        # Only process asset types this plugin handles
        filters.append("c.asset_type IN @asset_types")
        bind["asset_types"] = self.asset_types

        query = (
            "FOR c IN datalake_chunks "
            f"FILTER {' AND '.join(filters)} "
            "RETURN c"
        )

        chunks = list(db.aql.execute(query, bind_vars=bind))
        log(f"describe: {len(chunks)} unenriched Table/Figure chunks found")

        if not chunks:
            return {"enriched": 0, "skipped": 0, "failed": 0, "elapsed_sec": 0.0}

        sem = asyncio.Semaphore(config.vlm_concurrency)
        stats = {"enriched": 0, "skipped": 0, "failed": 0}
        coll = db.collection("datalake_chunks")

        async def _process_one(chunk: Dict[str, Any]) -> None:
            atype = chunk.get("asset_type", "")
            if atype == "Table":
                result = await _describe_table(
                    chunk, client, config.vlm_model, sem, config.vlm_timeout
                )
            elif atype == "Figure":
                result = await _describe_figure(
                    chunk, client, config.vlm_model, sem, config.vlm_timeout
                )
            else:
                stats["skipped"] += 1
                return

            if result:
                merged_meta = {**chunk.get("source_meta", {}), **result}
                coll.update({"_key": chunk["_key"], "source_meta": merged_meta})
                stats["enriched"] += 1
            else:
                stats["failed"] += 1

        await asyncio.gather(*[_process_one(c) for c in chunks])

        elapsed = round(time.monotonic() - t0, 2)
        stats["elapsed_sec"] = elapsed
        log(f"describe: done — {stats}")
        return stats


# ---------------------------------------------------------------------------
# Per-type VLM calls
# ---------------------------------------------------------------------------

async def _describe_table(
    chunk: Dict[str, Any],
    client: Any,
    model: str,
    sem: asyncio.Semaphore,
    timeout: int = 45,
) -> Optional[Dict[str, Any]]:
    """Send table HTML to VLM and get structured description."""
    html = chunk.get("source_meta", {}).get("html", "")
    if not html:
        return None

    async with sem:
        try:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Analyze this HTML table. Return strict JSON: "
                                "{title: short title, description: 2-3 sentence "
                                "summary, headers: [column headers]}"
                            ),
                        },
                        {"role": "user", "content": html[:4000]},
                    ],
                    temperature=0.2,
                    response_format={"type": "json_object"},
                ),
                timeout=timeout,
            )
            parsed = safe_json(resp.choices[0].message.content)
            return {
                "ai_title": parsed.get("title", ""),
                "ai_description": parsed.get("description", ""),
                "ai_headers": parsed.get("headers", []),
            }
        except Exception as e:
            log(f"describe: table enrichment failed: {e}")
            return None


async def _describe_figure(
    chunk: Dict[str, Any],
    client: Any,
    model: str,
    sem: asyncio.Semaphore,
    timeout: int = 45,
) -> Optional[Dict[str, Any]]:
    """Send figure text context to VLM and get structured description."""
    text = chunk.get("text", "")
    if not text:
        return None

    async with sem:
        try:
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Based on this figure caption and context, "
                                "return strict JSON: {title: <=10 words, "
                                "description: 2-3 sentences}"
                            ),
                        },
                        {"role": "user", "content": text[:2000]},
                    ],
                    temperature=0.2,
                    response_format={"type": "json_object"},
                ),
                timeout=timeout,
            )
            parsed = safe_json(resp.choices[0].message.content)
            return {
                "ai_title": parsed.get("title", ""),
                "ai_description": parsed.get("description", ""),
            }
        except Exception as e:
            log(f"describe: figure enrichment failed: {e}")
            return None


# ---------------------------------------------------------------------------
# Register plugin
# ---------------------------------------------------------------------------

registry.register(DescribePlugin())


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import os
    import sys

    from ..pipeline_types import PipelineConfig

    parser = argparse.ArgumentParser(
        description="VLM description batch for Table/Figure chunks"
    )
    parser.add_argument(
        "--asset-type",
        type=str,
        choices=["Table", "Figure"],
        help="Filter by asset type",
    )
    parser.add_argument("--doc-id", type=str, help="Filter by document ID")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.getenv("VLM_CONCURRENCY", "6")),
        help="Parallel VLM calls (default: 6)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max chunks to process (0 = all)",
    )
    args = parser.parse_args()

    config = PipelineConfig(vlm_concurrency=args.concurrency)

    # If a specific asset type is requested, narrow the plugin's scope
    plugin = registry.get("describe")
    if plugin is None:
        print("ERROR: describe plugin not registered", file=sys.stderr)
        sys.exit(1)

    if args.asset_type:
        plugin.asset_types = (args.asset_type,)

    # CLI is standalone — no PipelineResult needed for batch mode
    from ..pipeline_types import PipelineResult
    dummy_result = PipelineResult(source_pdf="", page_count=0)
    result = asyncio.run(plugin.run_batch(dummy_result, config))
    print(result)
