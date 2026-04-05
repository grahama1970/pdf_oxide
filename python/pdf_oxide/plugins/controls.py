"""Framework controls mapping plugin.

Maps framework control references (NIST, SPARTA, CWE, etc.) in
extracted content. Uses pdf_oxide's Rust ``map_framework_controls()``
for the heavy lifting.
"""
from __future__ import annotations

from typing import Any, Dict, List, TYPE_CHECKING

from .base import Plugin, registry
from ..pipeline_util import log

if TYPE_CHECKING:
    from ..pipeline_types import PipelineConfig, PipelineResult


def _get_mapper():
    """Try to import the Rust map_framework_controls function."""
    try:
        from ..pdf_oxide import map_framework_controls
        return map_framework_controls
    except ImportError:
        return None


class ControlsPlugin(Plugin):
    name = "controls"
    depends_on = ("arango",)
    asset_types = ("Text", "Table", "Requirement")
    description = "Map framework controls (NIST, SPARTA, CWE, etc.)"

    def run(self, result: "PipelineResult", config: "PipelineConfig") -> None:
        """Inline mode: map controls on extracted blocks, tables, and requirements."""
        mapper = _get_mapper()
        if mapper is None:
            log("map_framework_controls not available -- skipping controls mapping")
            return

        stats: Dict[str, int] = {"mapped": 0, "skipped": 0}

        # Process text blocks
        for block in result.blocks:
            text = block.get("text", "")
            if not text.strip():
                stats["skipped"] += 1
                continue
            controls = mapper(text)
            if controls:
                block.setdefault("metadata", {})["controls"] = controls
                stats["mapped"] += 1
            else:
                stats["skipped"] += 1

        # Process tables
        for table in result.tables:
            text = table.get("text", table.get("caption", ""))
            if not text.strip():
                stats["skipped"] += 1
                continue
            controls = mapper(text)
            if controls:
                table.setdefault("metadata", {})["controls"] = controls
                stats["mapped"] += 1
            else:
                stats["skipped"] += 1

        # Process requirements
        for req in result.requirements:
            text = req.get("text", "")
            if not text.strip():
                stats["skipped"] += 1
                continue
            controls = mapper(text)
            if controls:
                req.setdefault("metadata", {})["controls"] = controls
                stats["mapped"] += 1
            else:
                stats["skipped"] += 1

        result.metadata["controls_stats"] = stats
        log(f"Controls mapping: {stats['mapped']} mapped, {stats['skipped']} skipped")

    async def run_batch(self, result: "PipelineResult", config: "PipelineConfig") -> Dict[str, Any]:
        """Batch mode: read chunks from ArangoDB, map controls, write edges."""
        mapper = _get_mapper()
        if mapper is None:
            log("map_framework_controls not available -- skipping batch controls")
            return {"mapped": 0, "skipped": 0, "edges_created": 0,
                    "reason": "rust function unavailable"}

        try:
            from arango import ArangoClient
        except ImportError:
            log("python-arango not installed -- skipping batch controls")
            return {"mapped": 0, "skipped": 0, "edges_created": 0,
                    "reason": "python-arango not installed"}

        client = ArangoClient(hosts=config.arango_url)
        db = client.db(
            config.arango_db,
            username=config.arango_user,
            password=config.arango_pass,
        )

        # Ensure edge collection exists
        if not db.has_collection("chunk_control_edges"):
            db.create_collection("chunk_control_edges", edge=True)

        # Query chunks that haven't been control-mapped yet
        cursor = db.aql.execute(
            """
            FOR chunk IN datalake_chunks
                FILTER chunk.controls_mapped != true
                RETURN {_key: chunk._key, _id: chunk._id, text: chunk.text}
            """,
            batch_size=500,
        )

        stats: Dict[str, int] = {"mapped": 0, "skipped": 0, "edges_created": 0}
        edge_coll = db.collection("chunk_control_edges")
        chunk_coll = db.collection("datalake_chunks")
        edges_batch: List[Dict[str, Any]] = []

        for chunk in cursor:
            text = chunk.get("text", "")
            if not text.strip():
                stats["skipped"] += 1
                continue

            controls = mapper(text)
            if not controls:
                # Mark as processed even if no controls found
                chunk_coll.update({"_key": chunk["_key"], "controls_mapped": True})
                stats["skipped"] += 1
                continue

            # Create edges from chunk to each control
            for ctrl in controls:
                edge = {
                    "_from": chunk["_id"],
                    "_to": f"framework_controls/{ctrl['id']}",
                    "framework": ctrl.get("framework", ""),
                    "confidence": ctrl.get("confidence", 1.0),
                }
                edges_batch.append(edge)
                stats["edges_created"] += 1

            # Mark chunk as processed
            chunk_coll.update({
                "_key": chunk["_key"],
                "controls_mapped": True,
                "control_ids": [c["id"] for c in controls],
            })
            stats["mapped"] += 1

            # Flush edges in batches
            if len(edges_batch) >= 500:
                edge_coll.insert_many(edges_batch, overwrite=True)
                edges_batch.clear()

        # Flush remaining edges
        if edges_batch:
            edge_coll.insert_many(edges_batch, overwrite=True)

        log(
            f"Controls batch: {stats['mapped']} mapped, "
            f"{stats['skipped']} skipped, {stats['edges_created']} edges created"
        )
        return stats


registry.register(ControlsPlugin())
