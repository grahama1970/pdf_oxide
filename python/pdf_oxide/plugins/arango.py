"""ArangoDB export plugin.

Flattens extracted content into datalake chunks and writes
document, chunks, and edges to ArangoDB. Does NOT generate
embeddings — use the "embeddings" plugin for that.
"""
from __future__ import annotations

from typing import Any, Dict, TYPE_CHECKING

from .base import Plugin, registry
from ..pipeline_flatten import flatten
from ..pipeline_arango import export_arango
from ..pipeline_util import log

if TYPE_CHECKING:
    from ..pipeline_types import PipelineConfig, PipelineResult


class ArangoPlugin(Plugin):
    name = "arango"
    depends_on = ()
    description = "Export chunks to ArangoDB"

    def run(self, result: "PipelineResult", config: "PipelineConfig") -> None:
        """Flatten result into chunks (sync, cheap)."""
        if not result.flattened:
            result.flattened = flatten(result)
        log(f"Arango: flattened {len(result.flattened)} chunks")

    async def run_batch(
        self, result: "PipelineResult", config: "PipelineConfig"
    ) -> Dict[str, Any]:
        """Export flattened chunks to ArangoDB (async)."""
        return await export_arango(result, result.flattened, config)


registry.register(ArangoPlugin())
