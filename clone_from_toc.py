#!/usr/bin/env python3
"""Clone PDF from TOC using presets + LLM content.

Flow:
1. Load TOC manifest (sections with page ranges)
2. LLM Call 1: For batch of sections, pick presets and generate content
3. Render each element using its preset
4. Output PDF with known ground truth

Usage:
    python clone_from_toc.py --manifest .archive/clone_v4_deprecated/clone_v4_manifest.json --output clone.pdf
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "python")

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, ListFlowable, ListItem,
)
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER

from pdf_oxide.presets import (
    TABLE_PRESETS, CALLOUT_PRESETS, LIST_PRESETS,
    TableSpec, build_table, build_callout, build_list,
)

# Preset names for the LLM prompt
TABLE_PRESET_NAMES = list(TABLE_PRESETS.keys())
CALLOUT_PRESET_NAMES = list(CALLOUT_PRESETS.keys())

# Text bank path for corpus-based content
TEXT_BANK_PATH = Path("/mnt/storage12tb/text_banks")


def load_glossary_from_corpus(domain: str = "nist", count: int = 50, seed: int = 42) -> list:
    """Load glossary term/definition pairs from text corpus.

    Returns list of {"term": "...", "definition": "..."} dicts.
    """
    import random

    bank_file = TEXT_BANK_PATH / f"{domain}.json"
    if not bank_file.exists():
        bank_file = TEXT_BANK_PATH / "government.json"

    if not bank_file.exists():
        return []

    with open(bank_file) as f:
        data = json.load(f)

    # Filter to glossary entries
    glossary_entries = [d for d in data if d.get("content_type") == "glossary"]

    # Parse term/definition pairs
    terms = []
    skip_patterns = ["http", "www", "doi.org", "publication", "available", "volume"]
    for entry in glossary_entries:
        text = entry.get("text", "")
        if ":" in text[:60]:  # Looks like "Term: definition"
            parts = text.split(":", 1)
            term = parts[0].strip()
            defn = parts[1].strip() if len(parts) > 1 else ""
            # Filter out noise
            term_lower = term.lower()
            if any(p in term_lower for p in skip_patterns):
                continue
            if not term or not defn or len(term) < 3 or len(term) > 50:
                continue
            if len(defn) < 20:  # Too short to be a real definition
                continue
            terms.append({"term": term, "definition": defn[:300]})

    # Deterministic shuffle and selection
    rng = random.Random(seed)
    rng.shuffle(terms)
    return terms[:count]


def load_toc(manifest_path: str) -> list:
    """Load TOC entries from manifest."""
    with open(manifest_path) as f:
        manifest = json.load(f)
    return manifest.get("toc", [])


def infer_element_type(title: str, page_span: int) -> str:
    """Infer what element type a section should have."""
    title_lower = title.lower()

    # Control families get tables
    if any(kw in title_lower for kw in ["access control", "awareness", "audit", "assessment",
                                         "configuration", "contingency", "identification",
                                         "incident", "maintenance", "media", "physical",
                                         "planning", "program", "personnel", "risk",
                                         "system and service", "system and comm", "supply"]):
        return "control_table"

    # Specific sections
    if "glossary" in title_lower or "acronym" in title_lower:
        return "definition_table"
    if "control summar" in title_lower:
        return "summary_table"
    if "reference" in title_lower:
        return "reference_list"
    if "errata" in title_lower:
        return "errata_table"

    # Front matter
    if any(kw in title_lower for kw in ["abstract", "introduction", "scope", "purpose",
                                         "applicability", "audience", "fundamental"]):
        return "prose_with_callout"

    # Default for longer sections
    if page_span > 3:
        return "prose_with_table"

    return "prose"


async def llm_generate_batch(sections: list, domain: str = "NIST security", chunk_size: int = 5, model: str = "text") -> dict:
    """LLM generates content for sections in chunks.

    Returns dict mapping section_id to content dict.
    """
    import httpx

    system_prompt = f"""You generate content for a PDF document.

Available table presets: {', '.join(TABLE_PRESET_NAMES[:20])}
Available callout presets: {', '.join(CALLOUT_PRESET_NAMES)}

For each section, output JSON with:
- paragraphs: list of paragraph texts
- table: optional {{preset, headers, rows}} for tables
- callout: optional {{preset, title, body}} for callout boxes

Use formal NIST/FIPS technical language. Requirements use SHALL/MUST."""

    content_map = {}

    async def generate_chunk(client: httpx.AsyncClient, chunk: list) -> dict:
        """Generate content for a chunk of sections."""
        sections_spec = [{
            "id": s["id"],
            "title": s["title"],
            "element_type": s["element_type"],
            "page_span": s["page_end"] - s["page"],
        } for s in chunk]

        # Calculate expected row counts based on page span
        row_guidance = []
        for s in chunk:
            span = s["page_end"] - s["page"]
            if "glossary" in s["title"].lower():
                rows = max(20, span * 8)  # ~8 terms per page
                row_guidance.append(f"Section {s['id']} (GLOSSARY): generate {rows}+ term/definition rows")
            elif "acronym" in s["title"].lower():
                rows = max(15, span * 12)  # ~12 acronyms per page
                row_guidance.append(f"Section {s['id']} (ACRONYMS): generate {rows}+ acronym/definition rows")
            elif "control summar" in s["title"].lower():
                rows = max(20, span * 3)  # ~3 controls per page
                row_guidance.append(f"Section {s['id']} (CONTROL SUMMARIES): generate {rows}+ control rows")
            elif "reference" in s["title"].lower() and span > 5:
                rows = max(15, span * 4)  # ~4 references per page
                row_guidance.append(f"Section {s['id']} (REFERENCES): generate {rows}+ reference entries")
            elif s["element_type"] == "control_table":
                rows = max(5, span * 2)
                row_guidance.append(f"Section {s['id']}: generate {rows}+ control rows")

        guidance_str = "\n".join(row_guidance) if row_guidance else ""

        user_prompt = f"""Generate content for these {domain} document sections:

{json.dumps(sections_spec, indent=2)}

IMPORTANT - Content volume based on page_span:
{guidance_str if guidance_str else "Generate 3-6 rows for tables based on section complexity."}

Return JSON object with section IDs as keys:
{{
  "{chunk[0]['id']}": {{
    "paragraphs": ["First paragraph text.", "Second paragraph."],
    "table": {{"preset": "control_matrix", "headers": ["ID", "Name"], "rows": [["AC-1", "Policy"]]}},
    "callout": null
  }}
}}

Match presets to section types. Control sections need control_matrix tables.
Glossary needs definition tables with Term/Definition columns.
Acronyms need Acronym/Definition columns.
Keep content realistic but synthetic."""

        try:
            resp = await client.post(
                "http://localhost:4001/v1/chat/completions",
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
        except Exception as e:
            print(f"  Chunk failed: {e}")
            return {}

    # Process in chunks
    async with httpx.AsyncClient() as client:
        for i in range(0, len(sections), chunk_size):
            chunk = sections[i:i + chunk_size]
            chunk_ids = [s["id"] for s in chunk]
            print(f"  Processing sections {chunk_ids}...")
            result = await generate_chunk(client, chunk)
            content_map.update(result)

    return content_map


def render_section(section: dict, content: dict, styles: dict, story: list, seed: int = 42):
    """Render a section using its content and appropriate presets."""

    title_lower = section.get("title", "").lower()

    # Section heading
    depth = section.get("level", 0)
    heading_style = styles[f"Heading{min(depth + 1, 4)}"]
    story.append(Paragraph(section["title"], heading_style))
    story.append(Spacer(1, 0.15 * inch))

    # Special handling: GLOSSARY section uses corpus data
    if "glossary" in title_lower:
        page_span = section.get("page_end", 0) - section.get("page", 0)
        count = max(50, page_span * 6)  # ~6 terms per page
        glossary_terms = load_glossary_from_corpus("nist", count=count, seed=seed)

        if glossary_terms:
            story.append(Paragraph(
                "This glossary provides definitions for key terms used throughout this publication.",
                styles["BodyText"]
            ))
            story.append(Spacer(1, 0.1 * inch))

            headers = ["Term", "Definition"]
            rows = [[t["term"], t["definition"]] for t in glossary_terms]
            spec = TableSpec(headers=headers, rows=rows)
            table = build_table(spec, preset="data_grid")
            story.append(table)
            story.append(Spacer(1, 0.2 * inch))
            return  # Skip normal rendering

    # Paragraphs
    for para_text in content.get("paragraphs", []):
        if para_text:
            story.append(Paragraph(para_text, styles["BodyText"]))
            story.append(Spacer(1, 0.08 * inch))

    # Table (if present)
    table_data = content.get("table")
    if table_data and isinstance(table_data, dict):
        preset_name = table_data.get("preset", "data_grid")
        headers = table_data.get("headers", [])
        rows = table_data.get("rows", [])

        if headers and rows:
            # Validate preset exists
            if preset_name not in TABLE_PRESETS:
                preset_name = "data_grid"

            spec = TableSpec(headers=headers, rows=rows)
            table = build_table(spec, preset=preset_name)
            story.append(table)
            story.append(Spacer(1, 0.1 * inch))

    # Callout box (if present)
    callout_data = content.get("callout")
    if callout_data and isinstance(callout_data, dict):
        preset_name = callout_data.get("preset", "note_box")
        title = callout_data.get("title", "")
        body = callout_data.get("body", "")

        if body:
            if preset_name not in CALLOUT_PRESETS:
                preset_name = "note_box"

            callout = build_callout(title, body, preset=preset_name)
            story.append(callout)
            story.append(Spacer(1, 0.1 * inch))

    story.append(Spacer(1, 0.2 * inch))


async def clone_document(manifest_path: str, output_path: str, limit: int = None, model: str = "text"):
    """Clone document from TOC manifest."""

    print(f"Loading TOC from {manifest_path}...")
    print(f"Using model: {model}")
    toc = load_toc(manifest_path)
    print(f"Loaded {len(toc)} TOC entries")

    # Filter to level 0-1 (major sections only)
    top_level = [s for s in toc if s.get("level", 0) <= 1]
    if limit:
        top_level = top_level[:limit]
    print(f"Processing {len(top_level)} top-level sections")

    # Enrich TOC entries with inferred element types
    sections = []
    for i, entry in enumerate(top_level):
        page_span = entry.get("page_end", entry["page"]) - entry["page"]
        sections.append({
            "id": str(i),
            "title": entry["title"],
            "page": entry["page"],
            "page_end": entry.get("page_end", entry["page"] + 1),
            "level": entry.get("level", 0),
            "element_type": infer_element_type(entry["title"], page_span),
        })

    print("\nSection analysis:")
    for s in sections[:10]:
        print(f"  [{s['id']:2s}] {s['title'][:45]:<47} → {s['element_type']}")
    if len(sections) > 10:
        print(f"  ... and {len(sections) - 10} more")

    # LLM call: generate content in batches
    print(f"\nGenerating content via LLM (batch of {len(sections)}) using {model}...")
    content_map = await llm_generate_batch(sections, model=model)

    success = sum(1 for k in content_map if content_map[k].get("paragraphs"))
    print(f"LLM returned content for {success}/{len(sections)} sections")

    # Build PDF
    print(f"\nBuilding PDF...")
    doc = SimpleDocTemplate(output_path, pagesize=letter,
                            leftMargin=1*inch, rightMargin=1*inch,
                            topMargin=1*inch, bottomMargin=1*inch)
    styles = getSampleStyleSheet()
    story = []

    for section in sections:
        content = content_map.get(section["id"], {})
        if not content:
            content = {"paragraphs": [f"[Placeholder content for {section['title']}]"]}
        render_section(section, content, styles, story)

    doc.build(story)
    print(f"\nWrote: {output_path}")
    print(f"Sections rendered: {len(sections)}")


def main():
    parser = argparse.ArgumentParser(description="Clone PDF from TOC manifest")
    parser.add_argument("--manifest", default=".archive/clone_v4_deprecated/clone_v4_manifest.json",
                        help="Path to TOC manifest JSON")
    parser.add_argument("--output", "-o", default="/tmp/clone_from_toc.pdf",
                        help="Output PDF path")
    parser.add_argument("--limit", "-n", type=int, default=None,
                        help="Limit number of sections to process")
    parser.add_argument("--model", "-m", default="text",
                        help="LLM model for content generation (default: text, uses dynamic Chutes routing)")
    args = parser.parse_args()

    asyncio.run(clone_document(args.manifest, args.output, args.limit, args.model))


if __name__ == "__main__":
    main()
