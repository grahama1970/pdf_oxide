"""Plugin base class and registry."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING

if TYPE_CHECKING:
    from ..pipeline_types import PipelineConfig, PipelineResult


class Plugin(ABC):
    """Base class for pdf_oxide plugins.

    Plugins run after core Rust extraction. They can operate in two modes:
      - Inline: run(result, config) — operates on PipelineResult in memory
      - Batch: run_batch(config) — operates on chunks already in ArangoDB

    Subclasses MUST override name and should override depends_on/asset_types
    as class attributes (not mutate the base defaults).
    """

    name: str = ""
    # Frozen tuples prevent the shared-mutable-default bug
    depends_on: tuple = ()
    asset_types: tuple = ()
    description: str = ""

    def run(
        self,
        result: "PipelineResult",
        config: "PipelineConfig",
    ) -> None:
        """Run inline after extraction. Override for inline plugins."""

    async def run_batch(
        self,
        result: "PipelineResult",
        config: "PipelineConfig",
    ) -> Dict[str, Any]:
        """Run as batch against ArangoDB. Override for batch plugins."""
        return {"skipped": True, "reason": "not implemented"}


class PluginRegistry:
    """Registry that resolves plugin dependencies and executes in order."""

    def __init__(self) -> None:
        self._plugins: Dict[str, Plugin] = {}

    def register(self, plugin: Plugin) -> None:
        self._plugins[plugin.name] = plugin

    def get(self, name: str) -> Optional[Plugin]:
        return self._plugins.get(name)

    @property
    def all_names(self) -> List[str]:
        return list(self._plugins.keys())

    def resolve(self, features: List[str]) -> List[Plugin]:
        """Resolve features list into ordered plugins with dependencies.

        Auto-adds transitive dependencies. Returns plugins in
        topological order (dependencies first).

        Raises:
            ValueError: If a requested feature is not registered.
            RuntimeError: If a dependency cycle is detected.
        """
        # Warn on unknown features
        for name in features:
            if name not in self._plugins:
                import warnings
                warnings.warn(
                    f"Unknown plugin feature '{name}' — "
                    f"available: {self.all_names}",
                    stacklevel=2,
                )

        # Expand transitive dependencies
        needed: set[str] = set()
        queue = list(features)
        while queue:
            name = queue.pop(0)
            if name in needed:
                continue
            plugin = self._plugins.get(name)
            if plugin is None:
                continue
            needed.add(name)
            for dep in plugin.depends_on:
                if dep not in needed:
                    queue.append(dep)

        # Topological sort (Kahn's algorithm)
        in_degree: Dict[str, int] = {n: 0 for n in needed}
        for n in needed:
            p = self._plugins[n]
            for dep in p.depends_on:
                if dep in needed:
                    in_degree[n] = in_degree.get(n, 0) + 1

        queue = [n for n in needed if in_degree[n] == 0]
        ordered: List[Plugin] = []
        while queue:
            queue.sort()  # deterministic ordering
            n = queue.pop(0)
            ordered.append(self._plugins[n])
            for other in needed:
                p = self._plugins[other]
                if n in list(p.depends_on):
                    in_degree[other] -= 1
                    if in_degree[other] == 0:
                        queue.append(other)

        # Detect cycles — if ordered is smaller than needed, there's a cycle
        if len(ordered) < len(needed):
            stuck = needed - {p.name for p in ordered}
            raise RuntimeError(
                f"Dependency cycle detected among plugins: {stuck}"
            )

        return ordered

    async def run_all(
        self,
        features: List[str],
        result: "PipelineResult",
        config: "PipelineConfig",
    ) -> Dict[str, Any]:
        """Resolve and run all enabled plugins in dependency order.

        Calls run() for inline plugins, then await run_batch() for batch
        plugins. This is async — the caller (extract_pdf) holds the single
        asyncio.run() per best-practices-python async-single-asyncio-run.
        """
        plugins = self.resolve(features)
        report: Dict[str, Any] = {}
        for plugin in plugins:
            try:
                # Inline phase (sync)
                plugin.run(result, config)

                # Batch phase — await run_batch if overridden
                if type(plugin).run_batch is not Plugin.run_batch:
                    batch_result = await plugin.run_batch(result, config)
                    report[plugin.name] = batch_result
                else:
                    report[plugin.name] = "ok"
            except Exception as e:
                report[plugin.name] = f"error: {e}"
        return report


# Global registry — plugins register themselves on import
registry = PluginRegistry()


def list_plugins() -> List[str]:
    """Return names of all registered plugins."""
    return registry.all_names
