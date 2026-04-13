"""Canonical types for clone pipeline.

These types bridge clone_profiler's rich output to the render plan, enabling
structurally-faithful PDF cloning with render-time truth tracking.

Hierarchy:
    SourceProfileRef → RenderPlan → PageRegime → SectionBudget
                                                       ↓
                                              TruthManifest → TruthObject
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import hashlib
import json


# =============================================================================
# Enums
# =============================================================================

class PageType(str, Enum):
    """Page classification for regime selection."""
    FRONT_MATTER = "front_matter"  # Title, TOC, LOF, LOT
    BODY_TEXT = "body_text"        # Regular prose sections
    TABLE_HEAVY = "table_heavy"    # Pages dominated by tables
    FIGURE_HEAVY = "figure_heavy"  # Pages dominated by figures
    MIXED = "mixed"                # Both tables and prose
    APPENDIX = "appendix"          # Back matter, references
    BLANK = "blank"


class BlockType(str, Enum):
    """Content block types for QID allocation."""
    TITLE = "title"
    TOC_HEADER = "toc_header"
    TOC_ENTRY = "toc_entry"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    TABLE_HEADER = "table_header"
    TABLE_CELL = "table_cell"
    CAPTION = "caption"
    FIGURE = "figure"
    LIST = "list"
    LIST_ITEM = "list_item"
    FOOTNOTE = "footnote"
    CALLOUT = "callout"
    RUNNING_HEADER = "running_header"
    RUNNING_FOOTER = "running_footer"


# =============================================================================
# Source Profile Reference
# =============================================================================

@dataclass
class SourceProfileRef:
    """Reference to clone_profiler output.

    This wraps the rich profile dict from clone_profiler.profile_for_cloning()
    and provides typed accessors for render plan derivation.
    """
    profile: Dict[str, Any]

    @property
    def doc_id(self) -> str:
        return self.profile.get("doc_id", "unknown")

    @property
    def path(self) -> str:
        return self.profile.get("path", "")

    @property
    def page_count(self) -> int:
        return int(self.profile.get("page_count", 0))

    @property
    def domain(self) -> str:
        return self.profile.get("domain", "general")

    @property
    def layout_mode(self) -> str:
        return self.profile.get("layout_mode", "single_column")

    @property
    def has_toc(self) -> bool:
        return bool(self.profile.get("has_toc", False))

    @property
    def toc_sections(self) -> List[Dict[str, Any]]:
        return self.profile.get("toc_sections", [])

    @property
    def table_shapes(self) -> List[Dict[str, Any]]:
        return self.profile.get("table_shapes", [])

    @property
    def page_signatures(self) -> List[Dict[str, Any]]:
        return self.profile.get("page_signatures", [])

    @property
    def running_headers(self) -> List[Dict[str, Any]]:
        return self.profile.get("running_headers", [])

    @property
    def running_footers(self) -> List[Dict[str, Any]]:
        return self.profile.get("running_footers", [])

    @property
    def font_families(self) -> List[str]:
        return self.profile.get("font_families", [])

    @property
    def font_map(self) -> Dict[str, Any]:
        return self.profile.get("font_map", {})

    @property
    def requirements_pages(self) -> List[int]:
        return self.profile.get("requirements_pages", [])

    @property
    def list_pages(self) -> List[int]:
        return self.profile.get("list_pages", [])

    @property
    def footnote_pages(self) -> List[int]:
        return self.profile.get("footnote_pages", [])

    @property
    def callout_pages(self) -> List[int]:
        return self.profile.get("callout_pages", [])

    @property
    def metrics(self) -> Dict[str, Any]:
        return self.profile.get("metrics", {})

    def get_table_shapes_for_page(self, page_num: int) -> List[Dict[str, Any]]:
        """Get all table shapes on a specific page."""
        return [t for t in self.table_shapes if t.get("page") == page_num]

    def get_sections_for_page(self, page_num: int) -> List[Dict[str, Any]]:
        """Get TOC sections that start on a specific page."""
        return [s for s in self.toc_sections if s.get("page") == page_num]

    def classify_page(self, page_num: int) -> PageType:
        """Classify a page by its dominant content type."""
        sigs = self.page_signatures
        if page_num >= len(sigs):
            return PageType.BLANK

        sig = sigs[page_num]

        if sig.get("is_blank"):
            return PageType.BLANK

        # Check for TOC pages
        toc_pages = self.profile.get("toc_pages", [])
        if page_num in toc_pages or page_num + 1 in toc_pages:
            return PageType.FRONT_MATTER

        # Check for table-heavy
        tables_on_page = self.get_table_shapes_for_page(page_num)
        if len(tables_on_page) >= 1:
            # If table covers significant portion of page, it's table_heavy
            total_rows = sum(t.get("rows", 0) for t in tables_on_page)
            if total_rows >= 10:
                return PageType.TABLE_HEAVY
            return PageType.MIXED

        # Check for figure pages
        if sig.get("figure_candidate"):
            return PageType.FIGURE_HEAVY

        return PageType.BODY_TEXT


# =============================================================================
# Section Budget
# =============================================================================

@dataclass
class SectionBudget:
    """Content budget for a single section.

    Derived from TOC section plus page analysis. Controls how much
    content to generate for this section to match source structure.
    """
    section_id: int
    title: str
    depth: int
    start_page: int
    end_page: int  # Exclusive

    # Content budget (derived from source analysis)
    paragraph_count: int = 0
    list_count: int = 0
    table_count: int = 0
    figure_count: int = 0

    # Hints from source
    has_requirements: bool = False
    has_callouts: bool = False
    has_footnotes: bool = False

    # Text bank parameters
    content_type: str = "prose"  # prose, requirement, bullet_list, etc.
    domain: str = "general"

    @property
    def page_span(self) -> int:
        return max(1, self.end_page - self.start_page)


# =============================================================================
# Page Regime
# =============================================================================

@dataclass
class PageRegime:
    """Rendering regime for a page or page range.

    Specifies how to assemble content on pages of this type:
    margins, columns, header/footer callbacks, table styles, etc.
    """
    page_type: PageType
    start_page: int
    end_page: int  # Exclusive

    # Layout
    columns: int = 1
    left_margin: float = 0.75  # inches
    right_margin: float = 0.75
    top_margin: float = 0.75
    bottom_margin: float = 0.75

    # Running content
    header_preset: Optional[str] = None  # e.g., "doc_title_header"
    footer_preset: Optional[str] = None  # e.g., "page_number_footer"

    # Table styling
    table_preset: str = "data_grid"

    # Font selection
    body_font: str = "Helvetica"
    heading_font: str = "Helvetica-Bold"
    base_size: float = 10.0


# =============================================================================
# Render Plan
# =============================================================================

@dataclass
class RenderPlan:
    """Complete render plan derived from source profile.

    This is the bridge between clone_profiler's analysis and clone_builder's
    PDF assembly. It specifies:
    - What content to generate (section budgets)
    - How to render it (page regimes)
    - What to track for validation (truth manifest schema)
    """
    doc_id: str
    source_path: str
    seed: int
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Source metadata
    page_count: int = 0
    domain: str = "general"
    layout_mode: str = "single_column"

    # Content plan
    section_budgets: List[SectionBudget] = field(default_factory=list)
    page_regimes: List[PageRegime] = field(default_factory=list)

    # Table extraction targets (page → table shapes)
    table_targets: Dict[int, List[Dict[str, Any]]] = field(default_factory=dict)

    # Font mapping (source font → ReportLab font)
    font_mapping: Dict[str, str] = field(default_factory=dict)

    # Running content patterns
    header_pattern: Optional[str] = None
    footer_pattern: Optional[str] = None

    def get_regime_for_page(self, page_num: int) -> Optional[PageRegime]:
        """Get the rendering regime for a specific page."""
        for regime in self.page_regimes:
            if regime.start_page <= page_num < regime.end_page:
                return regime
        return None

    def get_budget_for_section(self, section_id: int) -> Optional[SectionBudget]:
        """Get the content budget for a specific section."""
        for budget in self.section_budgets:
            if budget.section_id == section_id:
                return budget
        return None

    def total_tables(self) -> int:
        """Total number of tables to generate."""
        return sum(len(tables) for tables in self.table_targets.values())

    def total_sections(self) -> int:
        """Total number of sections in the plan."""
        return len(self.section_budgets)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "doc_id": self.doc_id,
            "source_path": self.source_path,
            "seed": self.seed,
            "created_at": self.created_at,
            "page_count": self.page_count,
            "domain": self.domain,
            "layout_mode": self.layout_mode,
            "section_budgets": [
                {
                    "section_id": b.section_id,
                    "title": b.title,
                    "depth": b.depth,
                    "start_page": b.start_page,
                    "end_page": b.end_page,
                    "paragraph_count": b.paragraph_count,
                    "table_count": b.table_count,
                    "content_type": b.content_type,
                    "domain": b.domain,
                }
                for b in self.section_budgets
            ],
            "page_regimes": [
                {
                    "page_type": r.page_type.value,
                    "start_page": r.start_page,
                    "end_page": r.end_page,
                    "columns": r.columns,
                    "table_preset": r.table_preset,
                }
                for r in self.page_regimes
            ],
            "table_count": self.total_tables(),
            "section_count": self.total_sections(),
        }

    def save(self, path: str) -> None:
        """Save render plan to JSON file."""
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))


# =============================================================================
# Truth Manifest (Render-Time Oracle)
# =============================================================================

@dataclass
class TruthObject:
    """A single truth entry in the manifest.

    Tracks one rendered element (heading, cell, paragraph, etc.) with
    enough information for structural validation beyond QID presence.
    """
    qid: str
    block_type: BlockType
    logical_text: str  # Content without QID markers
    rendered_text: str  # Content with [QID_xxx] prefix

    # Position in document
    page_num: int
    sequence_num: int  # Order within page

    # For table cells
    table_id: Optional[str] = None
    row: Optional[int] = None
    col: Optional[int] = None

    # For sections
    section_id: Optional[int] = None
    depth: Optional[int] = None

    # Validation helpers
    expected_neighbors: List[str] = field(default_factory=list)  # [prev_qid, next_qid]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "qid": self.qid,
            "block_type": self.block_type.value,
            "logical_text": self.logical_text,
            "rendered_text": self.rendered_text,
            "page_num": self.page_num,
            "sequence_num": self.sequence_num,
            "table_id": self.table_id,
            "row": self.row,
            "col": self.col,
            "section_id": self.section_id,
            "depth": self.depth,
            "expected_neighbors": self.expected_neighbors,
        }


@dataclass
class TruthManifest:
    """Render-time truth for the entire document.

    This is the oracle for extraction validation. It records:
    - Every QID allocated and where it was placed
    - Table structure (rows × cols × cell QIDs)
    - Section hierarchy
    - Expected ordering relationships

    Validation compares extraction results against this manifest for:
    - QID recovery rate (presence)
    - Ordering correctness (sequence)
    - Grid recovery (table structure)
    - Contamination detection (QID appearing in wrong block)
    """
    doc_id: str
    source_path: str
    output_path: str
    seed: int
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # Content
    objects: List[TruthObject] = field(default_factory=list)

    # Quick lookups
    qid_to_object: Dict[str, TruthObject] = field(default_factory=dict)

    # Table structure: table_id → {rows, cols, cells: [[qid, ...], ...]}
    table_structures: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Section hierarchy: section_id → {title, depth, qid, children: [section_id, ...]}
    section_hierarchy: Dict[int, Dict[str, Any]] = field(default_factory=dict)

    # Page → [qid, ...] in render order
    page_qid_order: Dict[int, List[str]] = field(default_factory=dict)

    def register(self, obj: TruthObject) -> None:
        """Register a truth object and update indices."""
        self.objects.append(obj)
        self.qid_to_object[obj.qid] = obj

        # Update page ordering
        if obj.page_num not in self.page_qid_order:
            self.page_qid_order[obj.page_num] = []
        self.page_qid_order[obj.page_num].append(obj.qid)

    def register_table_structure(
        self,
        table_id: str,
        rows: int,
        cols: int,
        cell_qids: List[List[str]],
    ) -> None:
        """Register a table's grid structure."""
        self.table_structures[table_id] = {
            "rows": rows,
            "cols": cols,
            "cells": cell_qids,
        }

    def register_section(
        self,
        section_id: int,
        title: str,
        depth: int,
        qid: str,
        parent_id: Optional[int] = None,
    ) -> None:
        """Register a section in the hierarchy."""
        self.section_hierarchy[section_id] = {
            "title": title,
            "depth": depth,
            "qid": qid,
            "parent_id": parent_id,
            "children": [],
        }
        if parent_id is not None and parent_id in self.section_hierarchy:
            self.section_hierarchy[parent_id]["children"].append(section_id)

    def update_object_page(self, qid: str, page_num: int) -> None:
        """Update a truth object's page after ReportLab pagination."""
        obj = self.qid_to_object.get(qid)
        if obj is None:
            return
        obj.page_num = page_num

    def rebuild_page_qid_order(self) -> None:
        """Rebuild page-level QID ordering from the current object list."""
        self.page_qid_order = {}
        for obj in sorted(self.objects, key=lambda o: (o.page_num, o.sequence_num)):
            if obj.page_num < 0:
                continue
            self.page_qid_order.setdefault(obj.page_num, []).append(obj.qid)

    @property
    def total_qids(self) -> int:
        return len(self.objects)

    @property
    def total_tables(self) -> int:
        return len(self.table_structures)

    @property
    def total_sections(self) -> int:
        return len(self.section_hierarchy)

    @property
    def total_pages(self) -> int:
        return len(self.page_qid_order)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "source_path": self.source_path,
            "output_path": self.output_path,
            "seed": self.seed,
            "created_at": self.created_at,
            "total_qids": self.total_qids,
            "total_tables": self.total_tables,
            "total_sections": self.total_sections,
            "total_pages": self.total_pages,
            "objects": [o.to_dict() for o in self.objects],
            "table_structures": self.table_structures,
            "section_hierarchy": self.section_hierarchy,
            "page_qid_order": {str(k): v for k, v in self.page_qid_order.items()},
        }

    def save(self, path: str) -> None:
        """Save truth manifest to JSON file."""
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: str) -> "TruthManifest":
        """Load truth manifest from JSON file."""
        data = json.loads(Path(path).read_text())
        manifest = cls(
            doc_id=data["doc_id"],
            source_path=data["source_path"],
            output_path=data["output_path"],
            seed=data["seed"],
            created_at=data.get("created_at", ""),
        )

        # Reconstruct objects
        for obj_data in data.get("objects", []):
            obj = TruthObject(
                qid=obj_data["qid"],
                block_type=BlockType(obj_data["block_type"]),
                logical_text=obj_data["logical_text"],
                rendered_text=obj_data["rendered_text"],
                page_num=obj_data["page_num"],
                sequence_num=obj_data["sequence_num"],
                table_id=obj_data.get("table_id"),
                row=obj_data.get("row"),
                col=obj_data.get("col"),
                section_id=obj_data.get("section_id"),
                depth=obj_data.get("depth"),
                expected_neighbors=obj_data.get("expected_neighbors", []),
            )
            manifest.objects.append(obj)
            manifest.qid_to_object[obj.qid] = obj

        manifest.table_structures = data.get("table_structures", {})
        manifest.section_hierarchy = data.get("section_hierarchy", {})
        manifest.page_qid_order = {int(k): v for k, v in data.get("page_qid_order", {}).items()}

        return manifest


# =============================================================================
# Render Plan Builder
# =============================================================================

def derive_render_plan(
    profile_ref: SourceProfileRef,
    seed: int = 42,
    regions: Optional[List[Dict[str, Any]]] = None,
) -> RenderPlan:
    """Derive a render plan from clone_profiler output.

    This integrates with clone_sampler's region analysis for proper
    section boundary detection instead of thin TOC-only logic.

    Args:
        profile_ref: Wrapped clone_profiler output
        seed: Random seed for deterministic generation
        regions: Optional pre-computed regions from clone_sampler._outline_to_regions()
                 If None, will compute from toc_sections with fallback logic.

    Returns:
        RenderPlan ready for clone_builder
    """
    # Generate doc_id from source path and seed
    hash_input = f"{profile_ref.path}:{seed}"
    doc_id = hashlib.md5(hash_input.encode()).hexdigest()[:8]

    plan = RenderPlan(
        doc_id=doc_id,
        source_path=profile_ref.path,
        seed=seed,
        page_count=profile_ref.page_count,
        domain=profile_ref.domain,
        layout_mode=profile_ref.layout_mode,
    )

    # Build page signature lookup for complexity analysis
    sig_by_page: Dict[int, Dict[str, Any]] = {}
    for sig in profile_ref.page_signatures:
        page_num = sig.get("page_num", -1)
        if page_num >= 0:
            sig_by_page[page_num] = sig

    # Build table lookup by page
    tables_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for table in profile_ref.table_shapes:
        page = table.get("page", -1)
        if page >= 0:
            tables_by_page.setdefault(page, []).append(table)

    # Collect page-level signals
    requirements_pages = set(profile_ref.requirements_pages)
    list_pages = set(profile_ref.list_pages)
    callout_pages = set(profile_ref.callout_pages)
    footnote_pages = set(profile_ref.footnote_pages)

    # Use regions if provided, otherwise build from TOC with proper boundaries
    if regions is None:
        regions = _build_regions_from_toc(
            profile_ref.toc_sections,
            profile_ref.page_count,
        )

    # Build section budgets from regions (not thin TOC boundaries)
    for i, region in enumerate(regions):
        start_page = region.get("start", 0)
        end_page = region.get("end", start_page) + 1  # Convert inclusive to exclusive
        hints = region.get("hints", [])
        title = region.get("title", f"Section {i}")
        level = region.get("level", 1)

        # Count tables in this region
        tables_in_region = []
        for page in range(start_page, end_page):
            tables_in_region.extend(tables_by_page.get(page, []))

        # Estimate paragraph count from page complexity
        paragraph_count = 0
        for page in range(start_page, end_page):
            sig = sig_by_page.get(page, {})
            char_count = sig.get("char_count", 0)
            # Estimate ~500 chars per paragraph
            paragraph_count += max(1, char_count // 500)
        paragraph_count = max(1, paragraph_count)

        # Determine content type from region hints
        if "requirements" in hints:
            content_type = "requirement"
        elif "tables" in hints:
            content_type = "prose"  # Tables have their own content
        elif "figures" in hints:
            content_type = "prose"
        elif "reference_material" in hints or "appendix" in hints:
            content_type = "glossary"
        else:
            content_type = "prose"

        # Check page signals for this region
        has_reqs = any(p in requirements_pages for p in range(start_page, end_page))
        has_callouts = any(p in callout_pages for p in range(start_page, end_page))
        has_footnotes = any(p in footnote_pages for p in range(start_page, end_page))
        has_lists = any(p in list_pages for p in range(start_page, end_page))

        budget = SectionBudget(
            section_id=i,
            title=title,
            depth=level - 1,  # Convert 1-based level to 0-based depth
            start_page=start_page,
            end_page=end_page,
            paragraph_count=paragraph_count,
            list_count=1 if has_lists else 0,
            table_count=len(tables_in_region),
            figure_count=sum(
                1 for p in range(start_page, end_page)
                if sig_by_page.get(p, {}).get("figure_candidate")
            ),
            has_requirements=has_reqs or "requirements" in hints,
            has_callouts=has_callouts,
            has_footnotes=has_footnotes,
            content_type=content_type,
            domain=profile_ref.domain,
        )
        plan.section_budgets.append(budget)

    # Build page regimes using region hints for better type classification
    region_by_page: Dict[int, Dict[str, Any]] = {}
    for region in regions:
        for page in range(region.get("start", 0), region.get("end", 0) + 1):
            region_by_page[page] = region

    current_regime_start = 0
    current_page_type = _classify_page_with_hints(
        0, profile_ref, sig_by_page, tables_by_page, region_by_page
    ) if profile_ref.page_count > 0 else PageType.BLANK

    for page_num in range(1, profile_ref.page_count + 1):
        if page_num < profile_ref.page_count:
            page_type = _classify_page_with_hints(
                page_num, profile_ref, sig_by_page, tables_by_page, region_by_page
            )
        else:
            page_type = None  # End of document

        # Start new regime when page type changes
        if page_type != current_page_type or page_num == profile_ref.page_count:
            # Determine table preset based on content
            if current_page_type == PageType.TABLE_HEAVY:
                table_preset = "data_grid"
            else:
                table_preset = "professional"

            # Determine header preset based on page type
            if current_page_type == PageType.FRONT_MATTER:
                header_preset = None
            elif current_page_type == PageType.APPENDIX:
                header_preset = "section_header"
            else:
                header_preset = "doc_title_header"

            regime = PageRegime(
                page_type=current_page_type,
                start_page=current_regime_start,
                end_page=page_num,
                table_preset=table_preset,
                header_preset=header_preset,
                footer_preset="page_number_footer",
            )
            plan.page_regimes.append(regime)

            if page_type is not None:
                current_regime_start = page_num
                current_page_type = page_type

    # Build table targets
    plan.table_targets = tables_by_page

    # Map fonts
    for font_name, font_info in profile_ref.font_map.items():
        reportlab_name = font_info.get("reportlab_name", font_name)
        plan.font_mapping[font_name] = reportlab_name

    # Detect running header/footer patterns
    if profile_ref.running_headers:
        header = profile_ref.running_headers[0]
        plan.header_pattern = header.get("text", header) if isinstance(header, dict) else str(header)
    if profile_ref.running_footers:
        footer = profile_ref.running_footers[0]
        plan.footer_pattern = footer.get("text", footer) if isinstance(footer, dict) else str(footer)

    return plan


def _build_regions_from_toc(
    toc_sections: List[Dict[str, Any]],
    total_pages: int,
) -> List[Dict[str, Any]]:
    """Build regions from TOC sections with proper boundaries.

    This is a fallback when clone_sampler regions aren't available.
    Uses the same logic as clone_sampler._outline_to_regions for consistency.
    """
    import re

    if not toc_sections:
        # No TOC - treat entire document as one region
        return [{
            "title": "Document",
            "start": 0,
            "end": total_pages - 1,
            "size": total_pages,
            "hints": [],
            "level": 1,
        }]

    # Sort sections by page
    sorted_sections = sorted(
        [s for s in toc_sections if s.get("page") is not None],
        key=lambda s: s.get("page", 0)
    )

    if not sorted_sections:
        return [{
            "title": "Document",
            "start": 0,
            "end": total_pages - 1,
            "size": total_pages,
            "hints": [],
            "level": 1,
        }]

    regions = []
    for i, section in enumerate(sorted_sections):
        start = section.get("page", 0)
        if i + 1 < len(sorted_sections):
            end = sorted_sections[i + 1].get("page", start + 1) - 1
        else:
            end = total_pages - 1

        title = section.get("title", "").lower()
        hints = []

        # Detect hints from title
        if any(kw in title for kw in ("table", "mapping", "matrix")):
            hints.append("tables")
        if any(kw in title for kw in ("figure", "list of figure")):
            hints.append("figures")
        if any(kw in title for kw in ("requirement",)):
            hints.append("requirements")
        if any(kw in title for kw in ("appendix", "annex")):
            hints.append("appendix")
        if any(kw in title for kw in ("glossary", "acronym", "reference", "bibliography")):
            hints.append("reference_material")
        if any(kw in title for kw in ("introduction", "purpose", "scope", "overview")):
            hints.append("intro")
        if any(kw in title for kw in ("content", "toc")):
            hints.append("toc")

        regions.append({
            "title": section.get("title", ""),
            "start": start,
            "end": max(start, end),
            "size": max(1, end - start + 1),
            "hints": hints,
            "level": section.get("depth", 0) + 1,
        })

    return regions


def _classify_page_with_hints(
    page_num: int,
    profile_ref: SourceProfileRef,
    sig_by_page: Dict[int, Dict[str, Any]],
    tables_by_page: Dict[int, List[Dict[str, Any]]],
    region_by_page: Dict[int, Dict[str, Any]],
) -> PageType:
    """Classify a page using both signature data and region hints."""
    sig = sig_by_page.get(page_num, {})
    region = region_by_page.get(page_num, {})
    hints = region.get("hints", [])

    # Check for blank
    if sig.get("is_blank"):
        return PageType.BLANK

    # Check region hints first (more reliable than page-level heuristics)
    if "toc" in hints:
        return PageType.FRONT_MATTER
    if "appendix" in hints or "reference_material" in hints:
        return PageType.APPENDIX

    # Check TOC pages
    toc_pages = profile_ref.profile.get("toc_pages", [])
    if page_num in toc_pages or page_num + 1 in toc_pages:
        return PageType.FRONT_MATTER

    # Check table density
    tables_on_page = tables_by_page.get(page_num, [])
    if tables_on_page:
        total_rows = sum(t.get("rows", 0) for t in tables_on_page)
        if total_rows >= 10 or "tables" in hints:
            return PageType.TABLE_HEAVY
        return PageType.MIXED

    # Check for figures
    if sig.get("figure_candidate") or sig.get("has_images") or "figures" in hints:
        return PageType.FIGURE_HEAVY

    return PageType.BODY_TEXT
