"""pdf_oxide plugin system.

Plugins extend extraction with optional capabilities:
  - arango: Write chunks to ArangoDB
  - describe: VLM table/figure descriptions
  - requirements: Extract requirements from text
  - lean4: Prove requirements via Lean4
  - controls: Map framework controls
  - taxonomy: Extract bridge tags
  - embeddings: Generate embeddings

Usage:
    from pdf_oxide import extract_pdf, PipelineConfig
    result = extract_pdf("doc.pdf", config=PipelineConfig(
        features=["arango", "describe"]
    ))
"""
from .base import Plugin, PluginRegistry, registry, list_plugins

# Auto-register all built-in plugins on import
from . import arango, describe, requirements, lean4, controls, taxonomy, embeddings  # noqa: F401

__all__ = ["Plugin", "PluginRegistry", "registry", "list_plugins"]
