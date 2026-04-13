"""Clone: Text-first PDF cloning with render-time oracle truth.

This is the canonical clone module for pdf_oxide. It provides:
- Table-centric PDF assembly with QID tracking
- Deterministic content generation from text banks
- Ground-truth manifest for extraction validation

Architecture:
- clone_builder.py: Main PDF assembly with QidAllocator
- content_generator.py: Text/table content from text banks
- table_extractor.py: Extract tables via oxide backend
- schemas.py: Data classes for layout proposals
- synthesizer.py: Text synthesis with QID injection
"""
from .schemas import (
    BBox,
    RenderedWord,
    RenderedLine,
    RenderedBlock,
    PageManifest,
    DocumentManifest,
    LayoutProposal,
    BlockProposal,
)

__all__ = [
    "BBox",
    "RenderedWord",
    "RenderedLine",
    "RenderedBlock",
    "PageManifest",
    "DocumentManifest",
    "LayoutProposal",
    "BlockProposal",
]
