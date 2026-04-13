"""Clone: Text-first PDF cloning with render-time oracle truth.

This is the canonical clone module for pdf_oxide. It provides:
- Table-centric PDF assembly with QID tracking
- Deterministic content generation from text banks
- Ground-truth manifest for extraction validation
- Structural validation beyond QID presence

Architecture:
- clone_types.py: Canonical types (RenderPlan, TruthManifest, etc.)
- clone_builder.py: Main PDF assembly with QidAllocator
- clone_validate.py: Structural validation against TruthManifest
- content_generator.py: Text/table content from text banks
- table_extractor.py: Extract tables via oxide backend
- schemas.py: Data classes for layout proposals
- synthesizer.py: Text synthesis with QID injection

Usage:
    from pdf_oxide.clone import derive_render_plan, TruthManifest, ValidationResult
    from pdf_oxide.clone_profiler import profile_for_cloning

    profile = profile_for_cloning("source.pdf")
    plan = derive_render_plan(SourceProfileRef(profile), seed=42)
    # ... build PDF with plan ...
    # ... validate extraction against truth manifest ...
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

from .clone_types import (
    # Enums
    PageType,
    BlockType,
    # Core types
    SourceProfileRef,
    SectionBudget,
    PageRegime,
    RenderPlan,
    TruthObject,
    TruthManifest,
    # Builder function
    derive_render_plan,
)

from .clone_builder import (
    QidAllocator,
    QidCollisionError,
    CloneManifest,
    build_clone_from_generated,
    clone_pdf,
    clone_pdf_sync,
    # Unified builder (TruthManifest output)
    CloneBuilder,
    build_clone,
)

from .clone_validate import (
    QidRecovery,
    OrderingResult,
    GridRecovery,
    ContaminationResult,
    ValidationResult,
    extract_qids_from_text,
    validate_extraction,
    validate_from_text,
)

__all__ = [
    # Schemas (layout proposals)
    "BBox",
    "RenderedWord",
    "RenderedLine",
    "RenderedBlock",
    "PageManifest",
    "DocumentManifest",
    "LayoutProposal",
    "BlockProposal",
    # Types (render plan + truth)
    "PageType",
    "BlockType",
    "SourceProfileRef",
    "SectionBudget",
    "PageRegime",
    "RenderPlan",
    "TruthObject",
    "TruthManifest",
    "derive_render_plan",
    # Builder
    "QidAllocator",
    "QidCollisionError",
    "CloneManifest",
    "build_clone_from_generated",
    "clone_pdf",
    "clone_pdf_sync",
    # Unified builder (TruthManifest output)
    "CloneBuilder",
    "build_clone",
    # Validation
    "QidRecovery",
    "OrderingResult",
    "GridRecovery",
    "ContaminationResult",
    "ValidationResult",
    "extract_qids_from_text",
    "validate_extraction",
    "validate_from_text",
]
