"""Opus-driven manifest generator for PDF cloning.

Uses Opus via scillm to analyze source PDF structure and emit enhanced manifest
with element sequences per section.

Input: TOC extraction, table shapes, page signatures from clone_profiler
Output: Enhanced manifest with element_sequence per section
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import httpx

from .clone_types import (
    BlockType,
    CrossRef,
    ElementSpec,
    FigureSpec,
    SectionBudget,
)


# Default presets by category
DEFAULT_PRESETS = {
    "tables": [
        "data_grid", "control_matrix", "summary_table", "mapping_table",
        "comparison_table", "definition_table", "timeline_table",
    ],
    "callouts": [
        "note_box", "warning_box", "tip_box", "important_box", "info_box",
    ],
    "lists": [
        "bullet_list", "numbered_list", "checklist", "definition_list",
    ],
    "figures": [
        "flowchart", "diagram", "architecture", "chart",
    ],
}

# Preset aliases - LLM outputs may use variations
PRESET_ALIASES = {
    "bullet": "bullet_list",
    "bullets": "bullet_list",
    "numbered": "numbered_list",
    "numbers": "numbered_list",
    "ordered_list": "numbered_list",
    "check": "checklist",
    "definitions": "definition_list",
    "note": "note_box",
    "warning": "warning_box",
    "tip": "tip_box",
    "important": "important_box",
    "info": "info_box",
}


def _normalize_preset(preset: Optional[str]) -> Optional[str]:
    """Normalize preset name using aliases."""
    if preset is None:
        return None
    return PRESET_ALIASES.get(preset, preset)

# Section type inference patterns
SECTION_PATTERNS = {
    "control_family": [
        "access control", "awareness", "audit", "assessment", "configuration",
        "contingency", "identification", "incident", "maintenance", "media",
        "physical", "planning", "program", "personnel", "risk", "supply chain",
        "system and service", "system and comm",
    ],
    "glossary": ["glossary", "acronym", "definition", "terminology"],
    "reference": ["reference", "bibliography", "citation"],
    "appendix": ["appendix", "annex"],
    "toc": ["table of content", "contents"],
    "front_matter": ["abstract", "introduction", "scope", "purpose", "overview"],
    "summary": ["summary", "executive summary", "control summary"],
}


def _infer_section_type(title: str) -> str:
    """Infer section type from title."""
    title_lower = title.lower()
    for section_type, patterns in SECTION_PATTERNS.items():
        if any(p in title_lower for p in patterns):
            return section_type
    return "body"


def _build_fallback_sequence(
    section_type: str,
    page_span: int,
    table_count: int,
    available_presets: Dict[str, List[str]],
    default_table_preset: str = "data_grid",
    default_callout_preset: str = "note_box",
) -> List[ElementSpec]:
    """Build element sequence using rule-based fallback.

    Args:
        section_type: Inferred section type (control_family, glossary, etc.)
        page_span: Number of pages this section spans
        table_count: Number of tables detected in this section
        available_presets: Available presets by category
        default_table_preset: Default table preset from style profile
        default_callout_preset: Default callout preset from style profile
    """
    elements = []

    if section_type == "control_family":
        # Control sections: intro paragraph, control table, notes
        elements.append(ElementSpec(element_type=BlockType.PARAGRAPH, count=2))
        preset = "control_matrix" if "control_matrix" in available_presets.get("tables", []) else default_table_preset
        elements.append(ElementSpec(
            element_type=BlockType.TABLE,
            preset=preset,
            config={"rows": max(5, page_span * 3)},
        ))
        if page_span > 2:
            elements.append(ElementSpec(element_type=BlockType.LIST, preset="bullet_list"))
        elements.append(ElementSpec(element_type=BlockType.PARAGRAPH, count=1))

    elif section_type == "glossary":
        # Glossary: intro, definition table
        elements.append(ElementSpec(element_type=BlockType.PARAGRAPH, count=1))
        preset = "glossary" if "glossary" in available_presets.get("tables", []) else default_table_preset
        elements.append(ElementSpec(
            element_type=BlockType.TABLE,
            preset=preset,
            config={"rows": max(20, page_span * 8)},
        ))

    elif section_type == "summary":
        # Summary: intro, summary table, conclusion
        elements.append(ElementSpec(element_type=BlockType.PARAGRAPH, count=2))
        elements.append(ElementSpec(
            element_type=BlockType.TABLE,
            preset=default_table_preset,
            config={"rows": max(10, page_span * 4)},
        ))
        elements.append(ElementSpec(element_type=BlockType.PARAGRAPH, count=1))

    elif section_type == "front_matter":
        # Front matter: prose with optional callout
        elements.append(ElementSpec(element_type=BlockType.PARAGRAPH, count=max(3, page_span * 2)))
        if page_span > 1:
            elements.append(ElementSpec(
                element_type=BlockType.CALLOUT,
                preset=default_callout_preset,
            ))
        elements.append(ElementSpec(element_type=BlockType.PARAGRAPH, count=1))

    else:
        # Default body section
        elements.append(ElementSpec(element_type=BlockType.PARAGRAPH, count=max(2, page_span)))
        if table_count > 0:
            elements.append(ElementSpec(
                element_type=BlockType.TABLE,
                preset=default_table_preset,
                config={"rows": 8},
            ))
            elements.append(ElementSpec(element_type=BlockType.PARAGRAPH, count=1))

    return elements


async def generate_manifest(
    toc: List[Dict[str, Any]],
    table_shapes: List[Dict[str, Any]],
    page_signatures: List[Dict[str, Any]],
    presets: Optional[Dict[str, List[str]]] = None,
    model: str = "opus",
    scillm_url: str = "http://localhost:4001",
    style_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate enhanced manifest with element sequences using Opus analysis.

    Args:
        toc: TOC entries with title, page, depth
        table_shapes: Table shape info from clone_profiler
        page_signatures: Per-page content signatures
        presets: Available presets by category (defaults to DEFAULT_PRESETS)
        model: LLM model for manifest generation (default: opus)
        scillm_url: scillm proxy URL
        style_profile: Optional StyleProfile dict from style_extractor (guides preset selection)

    Returns:
        Manifest dict with sections array, each having element_sequence
    """
    presets = presets or DEFAULT_PRESETS

    # Extract style-guided defaults from profile
    default_table_preset = "data_grid"
    default_callout_preset = "note_box"
    if style_profile:
        default_table_preset = style_profile.get("table_preset", "data_grid")
        default_callout_preset = style_profile.get("callout_preset", "note_box")

    # Build page→table lookup
    tables_by_page: Dict[int, List[Dict[str, Any]]] = {}
    for table in table_shapes:
        page = table.get("page", -1)
        if page >= 0:
            tables_by_page.setdefault(page, []).append(table)

    # Build sections with page spans
    sections = []
    sorted_toc = sorted(
        [t for t in toc if t.get("page") is not None],
        key=lambda t: t.get("page", 0)
    )

    total_pages = max((s.get("page_num", 0) for s in page_signatures), default=0) + 1

    for i, entry in enumerate(sorted_toc):
        start_page = entry.get("page", 0)
        if i + 1 < len(sorted_toc):
            end_page = sorted_toc[i + 1].get("page", start_page + 1)
        else:
            end_page = total_pages

        page_span = max(1, end_page - start_page)

        # Count tables in this section
        tables_in_section = []
        for p in range(start_page, end_page):
            tables_in_section.extend(tables_by_page.get(p, []))

        sections.append({
            "id": i,
            "title": entry.get("title", f"Section {i}"),
            "depth": entry.get("depth", 0),
            "start_page": start_page,
            "end_page": end_page,
            "page_span": page_span,
            "table_count": len(tables_in_section),
            "section_type": _infer_section_type(entry.get("title", "")),
        })

    # Try Opus for intelligent analysis, fall back to rule-based
    try:
        manifest = await _opus_analyze(sections, presets, model, scillm_url)
        if manifest and manifest.get("sections"):
            return manifest
    except Exception as e:
        print(f"  Opus analysis failed, using fallback: {e}")

    # Fallback: rule-based manifest generation
    return _build_fallback_manifest(
        sections, presets,
        default_table_preset=default_table_preset,
        default_callout_preset=default_callout_preset,
    )


async def _opus_analyze(
    sections: List[Dict[str, Any]],
    presets: Dict[str, List[str]],
    model: str,
    scillm_url: str,
) -> Optional[Dict[str, Any]]:
    """Use Opus to analyze sections and generate element sequences."""

    system_prompt = f"""You analyze PDF document structure to plan element sequences for each section.

Available presets:
- Tables: {', '.join(presets.get('tables', [])[:10])}
- Callouts: {', '.join(presets.get('callouts', [])[:5])}
- Lists: {', '.join(presets.get('lists', [])[:4])}

For each section, determine the optimal sequence of elements based on:
1. Section type (control family → table-heavy, glossary → definition table)
2. Page span (longer sections need more elements)
3. Typical document patterns (intro prose → main content → summary)

Output JSON with sections array, each having element_sequence."""

    user_prompt = f"""Analyze these sections and output element_sequence for each:

{json.dumps(sections, indent=2)}

For each section output:
{{
  "sections": [
    {{
      "id": 0,
      "title": "...",
      "element_sequence": [
        {{"type": "paragraph", "count": 2}},
        {{"type": "table", "preset": "control_matrix", "rows": 12}},
        {{"type": "callout", "preset": "note_box"}},
        {{"type": "paragraph", "count": 1}}
      ],
      "figure_specs": [
        {{"figure_type": "flowchart", "description": "...", "caption": "..."}}
      ],
      "cross_refs": [
        {{"target_id": "AC-2(1)", "ref_text": "See AC-2(1)"}}
      ]
    }}
  ]
}}

Match presets to section content. Control families need control_matrix tables.
Glossary needs definition_table. Include figure_specs only for sections with diagrams.
Return ONLY valid JSON."""

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{scillm_url}/v1/chat/completions",
            headers={"Authorization": "Bearer sk-dev-proxy-123"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)


def _build_fallback_manifest(
    sections: List[Dict[str, Any]],
    presets: Dict[str, List[str]],
    default_table_preset: str = "data_grid",
    default_callout_preset: str = "note_box",
) -> Dict[str, Any]:
    """Build manifest using rule-based element sequence generation.

    Args:
        sections: Section definitions with section_type, page_span, table_count
        presets: Available presets by category
        default_table_preset: Default table preset from style profile
        default_callout_preset: Default callout preset from style profile
    """
    result_sections = []

    for section in sections:
        section_type = section.get("section_type", "body")
        page_span = section.get("page_span", 1)
        table_count = section.get("table_count", 0)

        elements = _build_fallback_sequence(
            section_type, page_span, table_count, presets,
            default_table_preset=default_table_preset,
            default_callout_preset=default_callout_preset,
        )

        result_sections.append({
            "id": section["id"],
            "title": section["title"],
            "depth": section.get("depth", 0),
            "start_page": section["start_page"],
            "end_page": section["end_page"],
            "section_type": section_type,
            "element_sequence": [
                {
                    "type": e.element_type.value,
                    "preset": e.preset,
                    "count": e.count,
                    **({"config": e.config} if e.config else {}),
                }
                for e in elements
            ],
            "figure_specs": [],
            "cross_refs": [],
        })

    return {
        "sections": result_sections,
        "missing_presets": [],
        "preset_warnings": [],
    }


def validate_preset_coverage(
    manifest: Dict[str, Any],
    available_presets: Dict[str, List[str]],
) -> List[str]:
    """Validate that all presets referenced in manifest exist.

    Args:
        manifest: Manifest dict with sections
        available_presets: Available presets by category

    Returns:
        List of missing preset names
    """
    # Flatten available presets
    all_available = set()
    for category_presets in available_presets.values():
        all_available.update(category_presets)

    missing = []
    warnings = []

    for section in manifest.get("sections", []):
        for element in section.get("element_sequence", []):
            preset = element.get("preset")
            if preset and preset not in all_available:
                if preset not in missing:
                    missing.append(preset)
                warnings.append(
                    f"Section '{section.get('title', section.get('id'))}' "
                    f"references unknown preset '{preset}'"
                )

    # Update manifest with warnings
    manifest["missing_presets"] = missing
    manifest["preset_warnings"] = warnings

    return missing


def manifest_to_section_budgets(
    manifest: Dict[str, Any],
    domain: str = "general",
) -> List[SectionBudget]:
    """Convert manifest sections to SectionBudget objects.

    Args:
        manifest: Manifest from generate_manifest()
        domain: Content domain for text generation

    Returns:
        List of SectionBudget objects with element_sequence populated
    """
    budgets = []

    for i, section in enumerate(manifest.get("sections", [])):
        # Convert element_sequence to ElementSpec objects
        element_sequence = []
        for elem in section.get("element_sequence", []):
            elem_type = elem.get("type", "paragraph")
            try:
                block_type = BlockType(elem_type)
            except ValueError:
                block_type = BlockType.PARAGRAPH

            figure_spec = None
            if block_type == BlockType.FIGURE and section.get("figure_specs"):
                # Use first figure spec for this element
                for fspec in section.get("figure_specs", []):
                    figure_spec = FigureSpec(
                        figure_type=fspec.get("figure_type", "diagram"),
                        description=fspec.get("description", ""),
                        caption=fspec.get("caption", ""),
                    )
                    break

            element_sequence.append(ElementSpec(
                element_type=block_type,
                preset=_normalize_preset(elem.get("preset")),
                count=elem.get("count", 1),
                config=elem.get("config", {}),
                figure_spec=figure_spec,
            ))

        # Count elements by type for backward compat
        paragraph_count = sum(
            e.count for e in element_sequence
            if e.element_type == BlockType.PARAGRAPH
        )
        table_count = sum(
            1 for e in element_sequence
            if e.element_type == BlockType.TABLE
        )
        list_count = sum(
            1 for e in element_sequence
            if e.element_type in (BlockType.LIST, BlockType.LIST_ITEM)
        )
        figure_count = sum(
            1 for e in element_sequence
            if e.element_type == BlockType.FIGURE
        )

        # Determine content type
        section_type = section.get("section_type", "body")
        if section_type == "control_family":
            content_type = "requirement"
        elif section_type in ("glossary", "reference"):
            content_type = "glossary"
        else:
            content_type = "prose"

        # Use defaults if page info missing (e.g., from Opus-generated manifest)
        section_id = section.get("id", i) if isinstance(section.get("id"), int) else i
        start_page = section.get("start_page", section_id)
        end_page = section.get("end_page", start_page + 5)

        budget = SectionBudget(
            section_id=section_id,
            title=section.get("title", f"Section {section_id}"),
            depth=section.get("depth", 0),
            start_page=start_page,
            end_page=end_page,
            paragraph_count=paragraph_count,
            list_count=list_count,
            table_count=table_count,
            figure_count=figure_count,
            has_requirements=section_type == "control_family",
            has_callouts=any(
                e.element_type == BlockType.CALLOUT for e in element_sequence
            ),
            has_footnotes=False,
            content_type=content_type,
            domain=domain,
            element_sequence=element_sequence,
        )
        budgets.append(budget)

    return budgets
