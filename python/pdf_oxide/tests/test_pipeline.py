"""Tests for pdf_oxide plugin architecture and pipeline integration."""
from __future__ import annotations

import asyncio
import warnings

import pytest

from pdf_oxide.plugins import list_plugins, registry
from pdf_oxide.plugins.base import Plugin, PluginRegistry
from pdf_oxide.pipeline_types import PipelineConfig, PipelineResult


# ---------------------------------------------------------------------------
# Plugin registry tests
# ---------------------------------------------------------------------------

class TestPluginRegistry:
    """Test the plugin registry and dependency resolution."""

    def test_all_plugins_registered(self):
        """All 7 built-in plugins are auto-registered."""
        names = list_plugins()
        assert "arango" in names
        assert "describe" in names
        assert "requirements" in names
        assert "lean4" in names
        assert "controls" in names
        assert "taxonomy" in names
        assert "embeddings" in names
        assert len(names) == 7

    def test_dependency_chain_lean4(self):
        """lean4 auto-resolves: arango -> requirements -> lean4."""
        resolved = registry.resolve(["lean4"])
        names = [p.name for p in resolved]
        assert names == ["arango", "requirements", "lean4"]

    def test_dependency_chain_describe(self):
        """describe auto-resolves: arango -> describe."""
        resolved = registry.resolve(["describe"])
        names = [p.name for p in resolved]
        assert names == ["arango", "describe"]

    def test_empty_features(self):
        """Empty features list resolves to no plugins."""
        resolved = registry.resolve([])
        assert resolved == []

    def test_full_chain_ordering(self):
        """Full chain respects topological order."""
        resolved = registry.resolve(
            ["lean4", "describe", "embeddings", "controls", "taxonomy"]
        )
        names = [p.name for p in resolved]
        # arango must come before everything else
        assert names.index("arango") < names.index("describe")
        assert names.index("arango") < names.index("embeddings")
        assert names.index("arango") < names.index("controls")
        assert names.index("arango") < names.index("taxonomy")
        # requirements must come before lean4
        assert names.index("requirements") < names.index("lean4")

    def test_transitive_dependencies(self):
        """Requesting lean4 auto-includes requirements and arango."""
        resolved = registry.resolve(["lean4"])
        names = [p.name for p in resolved]
        assert "arango" in names
        assert "requirements" in names
        assert "lean4" in names

    def test_duplicate_features_handled(self):
        """Duplicate feature names don't cause issues."""
        resolved = registry.resolve(["arango", "arango", "describe"])
        names = [p.name for p in resolved]
        assert names.count("arango") == 1

    def test_unknown_feature_warns(self):
        """Unknown feature names emit a warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            resolved = registry.resolve(["nonexistent", "arango"])
            names = [p.name for p in resolved]
            assert "arango" in names
            assert "nonexistent" not in names
            assert len(w) == 1
            assert "nonexistent" in str(w[0].message)

    def test_cycle_detection(self):
        """Dependency cycles raise RuntimeError."""
        r = PluginRegistry()

        class A(Plugin):
            name = "a"
            depends_on = ("b",)

        class B(Plugin):
            name = "b"
            depends_on = ("a",)

        r.register(A())
        r.register(B())

        with pytest.raises(RuntimeError, match="cycle"):
            r.resolve(["a"])


# ---------------------------------------------------------------------------
# Plugin interface tests
# ---------------------------------------------------------------------------

class TestPluginInterface:
    """Test that each plugin has the correct interface."""

    @pytest.mark.parametrize("name", [
        "arango", "describe", "requirements",
        "lean4", "controls", "taxonomy", "embeddings",
    ])
    def test_plugin_has_name(self, name):
        plugin = registry.get(name)
        assert plugin is not None
        assert plugin.name == name

    @pytest.mark.parametrize("name,expected_deps", [
        ("arango", ()),
        ("describe", ("arango",)),
        ("requirements", ("arango",)),
        ("lean4", ("requirements",)),
        ("controls", ("arango",)),
        ("taxonomy", ("arango",)),
        ("embeddings", ("arango",)),
    ])
    def test_plugin_depends_on(self, name, expected_deps):
        plugin = registry.get(name)
        assert plugin.depends_on == expected_deps

    @pytest.mark.parametrize("name", [
        "arango", "describe", "requirements",
        "lean4", "controls", "taxonomy", "embeddings",
    ])
    def test_plugin_has_run_methods(self, name):
        plugin = registry.get(name)
        assert hasattr(plugin, "run")
        assert hasattr(plugin, "run_batch")
        assert callable(plugin.run)
        assert callable(plugin.run_batch)

    def test_depends_on_is_immutable(self):
        """depends_on uses tuples, not lists (shared-mutable-default bug)."""
        for name in list_plugins():
            plugin = registry.get(name)
            assert isinstance(plugin.depends_on, tuple), (
                f"{name}.depends_on should be tuple, got {type(plugin.depends_on)}"
            )

    def test_asset_types_is_immutable(self):
        """asset_types uses tuples, not lists."""
        for name in list_plugins():
            plugin = registry.get(name)
            assert isinstance(plugin.asset_types, tuple), (
                f"{name}.asset_types should be tuple, got {type(plugin.asset_types)}"
            )


# ---------------------------------------------------------------------------
# run_all behavioral tests
# ---------------------------------------------------------------------------

class TestRunAll:
    """Test run_all actually executes both inline and batch plugins."""

    def test_inline_plugin_runs(self):
        """run_all calls run() for inline-only plugins."""
        r = PluginRegistry()
        ran = []

        class InlineOnly(Plugin):
            name = "inline_only"
            depends_on = ()
            def run(self, result, config):
                ran.append("inline")

        r.register(InlineOnly())
        result = PipelineResult(source_pdf="test.pdf", page_count=1)
        report = asyncio.run(
            r.run_all(["inline_only"], result, PipelineConfig())
        )
        assert ran == ["inline"]
        assert report["inline_only"] == "ok"

    def test_batch_plugin_runs(self):
        """run_all calls run_batch() for batch plugins."""
        r = PluginRegistry()

        class BatchOnly(Plugin):
            name = "batch_only"
            depends_on = ()
            async def run_batch(self, result, config):
                return {"processed": 42}

        r.register(BatchOnly())
        result = PipelineResult(source_pdf="test.pdf", page_count=1)
        report = asyncio.run(
            r.run_all(["batch_only"], result, PipelineConfig())
        )
        assert report["batch_only"] == {"processed": 42}

    def test_mixed_plugin_runs_both(self):
        """run_all calls run() then run_batch() for mixed plugins."""
        r = PluginRegistry()
        phases = []

        class MixedPlugin(Plugin):
            name = "mixed"
            depends_on = ()
            def run(self, result, config):
                phases.append("inline")
            async def run_batch(self, result, config):
                phases.append("batch")
                return {"ok": True}

        r.register(MixedPlugin())
        result = PipelineResult(source_pdf="test.pdf", page_count=1)
        report = asyncio.run(
            r.run_all(["mixed"], result, PipelineConfig())
        )
        assert phases == ["inline", "batch"]
        assert report["mixed"] == {"ok": True}

    def test_dependency_order_in_run_all(self):
        """run_all executes dependencies before dependents."""
        r = PluginRegistry()
        order = []

        class First(Plugin):
            name = "first"
            depends_on = ()
            def run(self, result, config):
                order.append("first")

        class Second(Plugin):
            name = "second"
            depends_on = ("first",)
            def run(self, result, config):
                order.append("second")

        r.register(First())
        r.register(Second())
        result = PipelineResult(source_pdf="test.pdf", page_count=1)
        asyncio.run(r.run_all(["second"], result, PipelineConfig()))
        assert order == ["first", "second"]

    def test_error_in_plugin_reported(self):
        """Plugin exceptions are caught and reported, not raised."""
        r = PluginRegistry()

        class Broken(Plugin):
            name = "broken"
            depends_on = ()
            def run(self, result, config):
                raise ValueError("kaboom")

        r.register(Broken())
        result = PipelineResult(source_pdf="test.pdf", page_count=1)
        report = asyncio.run(
            r.run_all(["broken"], result, PipelineConfig())
        )
        assert "error: kaboom" in report["broken"]


# ---------------------------------------------------------------------------
# PipelineConfig tests
# ---------------------------------------------------------------------------

class TestPipelineConfig:
    """Test PipelineConfig with features."""

    def test_default_empty_features(self):
        cfg = PipelineConfig()
        assert cfg.features == []

    def test_features_list(self):
        cfg = PipelineConfig(features=["arango", "describe"])
        assert cfg.features == ["arango", "describe"]

    def test_config_has_model_fields(self):
        cfg = PipelineConfig()
        assert hasattr(cfg, "vlm_model")
        assert hasattr(cfg, "text_model")
        assert hasattr(cfg, "scillm_api_base")

    def test_config_has_arango_fields(self):
        cfg = PipelineConfig()
        assert hasattr(cfg, "arango_url")
        assert hasattr(cfg, "arango_db")
        assert hasattr(cfg, "arango_user")
        assert hasattr(cfg, "arango_pass")

    def test_config_has_embedding_fields(self):
        cfg = PipelineConfig()
        assert hasattr(cfg, "embedding_url")
        assert hasattr(cfg, "embedding_dim")
        assert cfg.embedding_dim == 384


# ---------------------------------------------------------------------------
# Custom plugin registration
# ---------------------------------------------------------------------------

class TestCustomPlugin:
    """Test creating and registering custom plugins."""

    def test_custom_plugin(self):
        """Users can create and register their own plugins."""
        custom_registry = PluginRegistry()

        class MyPlugin(Plugin):
            name = "my_custom"
            depends_on = ()
            description = "A custom plugin"

            def run(self, result, config):
                result.metadata["custom_ran"] = True

        plugin = MyPlugin()
        custom_registry.register(plugin)
        assert custom_registry.get("my_custom") is not None
        assert "my_custom" in custom_registry.all_names
