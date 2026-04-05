"""DEPRECATED — Use plugins instead.

Table/figure description: features=["describe"]
Requirement extraction: features=["requirements"]

This file is kept only as a tombstone. Import from plugins directly.
"""
raise ImportError(
    "pipeline_enrich.py is deprecated. "
    "Use PipelineConfig(features=['describe', 'requirements']) instead."
)
