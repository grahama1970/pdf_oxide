"""Sampler-aware content generator using /create-text corpus.

Wires SectionBudget.sampler_hints into actual text generation,
producing deterministic content that matches source structure.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

from pdf_oxide.clone.clone_types import SectionBudget


# Try to import create_text from skill location
_CREATE_TEXT_PATH = Path.home() / ".claude/skills/create-text"
if _CREATE_TEXT_PATH.exists():
    sys.path.insert(0, str(_CREATE_TEXT_PATH))
    try:
        from create_text import create_text as _corpus_create_text
    except ImportError:
        _corpus_create_text = None
else:
    _corpus_create_text = None


def _map_content_type(budget: SectionBudget) -> str:
    """Map SectionBudget.content_type to create_text content_type."""
    ct = budget.content_type.lower()
    if ct in ("requirement", "requirements"):
        return "requirement"
    if ct in ("bullet_list", "list"):
        return "bullet_list"
    if ct in ("glossary", "reference"):
        return "glossary"
    if ct in ("heading", "header"):
        return "heading"
    return "prose"


def _map_domain(budget: SectionBudget) -> str:
    """Map SectionBudget.domain to create_text domain.

    Available corpus domains: aerospace, arxiv, engineering, government,
    healthcare_it, medical_device, nasa, nist, other, transport_regulation
    """
    d = budget.domain.lower()
    # Map defense/military to government (corpus doesn't have "defense")
    if d in ("defense", "mil", "military", "cui"):
        return "government"
    if d in ("nist", "gov", "standards", "fips"):
        return "nist"
    if d in ("government",):
        return "government"
    if d in ("arxiv", "academic", "research"):
        return "arxiv"
    if d in ("nasa", "aerospace"):
        return "nasa"
    if d in ("engineering", "technical"):
        return "engineering"
    return None  # All domains


def _fallback_paragraphs(budget: SectionBudget, count: int, seed: int) -> List[str]:
    """Generate fallback paragraphs when corpus isn't available."""
    import hashlib

    paragraphs = []
    for i in range(count):
        h = hashlib.md5(f"{seed}:{budget.section_id}:para:{i}".encode()).hexdigest()[:8]

        if budget.has_requirements:
            text = (
                f"[{h}] The system shall implement appropriate security controls "
                f"in accordance with section {budget.section_id} requirements. "
                f"Organizations must ensure compliance with applicable standards."
            )
        elif budget.content_type == "glossary":
            text = f"[{h}] Term {i+1}: Definition for item in section {budget.title}."
        else:
            text = (
                f"[{h}] This section describes the implementation details for {budget.title}. "
                f"Additional context is provided for section {budget.section_id} content area {i+1}."
            )
        paragraphs.append(text)
    return paragraphs


def _fallback_table(budget: SectionBudget, table_idx: int, seed: int) -> Dict[str, Any]:
    """Generate fallback table when corpus isn't available."""
    import hashlib

    hints = budget.sampler_hints
    rows_hint = hints.get("table_windows", 0)
    row_count = max(3, min(rows_hint * 2, 10)) if rows_hint else 5

    if budget.has_requirements:
        headers = ["Control ID", "Requirement", "Status"]
        rows = []
        for j in range(row_count):
            h = hashlib.md5(f"{seed}:{budget.section_id}:tbl{table_idx}:row{j}".encode()).hexdigest()[:6]
            rows.append([
                f"CTL-{h.upper()}",
                f"Implement control for section {budget.section_id}",
                ["Implemented", "Planned", "Not Applicable"][j % 3],
            ])
    else:
        headers = ["ID", "Description", "Value"]
        rows = []
        for j in range(row_count):
            h = hashlib.md5(f"{seed}:{budget.section_id}:tbl{table_idx}:row{j}".encode()).hexdigest()[:6]
            rows.append([
                f"ITEM-{h.upper()}",
                f"Item {j+1} for {budget.title}",
                f"{(j+1) * 10}",
            ])

    return {"headers": headers, "rows": rows}


def _fallback_list_items(budget: SectionBudget, count: int, seed: int) -> List[str]:
    """Generate fallback list items."""
    import hashlib

    items = []
    list_templates = [
        "Ensure compliance with {title} requirements",
        "Document all {title} procedures",
        "Maintain records for audit purposes",
        "Review and update annually",
        "Report deviations within 24 hours",
        "Train personnel on {title} protocols",
        "Implement monitoring controls",
        "Verify effectiveness of measures",
    ]

    for i in range(count):
        template = list_templates[i % len(list_templates)]
        h = hashlib.md5(f"{seed}:{budget.section_id}:list:{i}".encode()).hexdigest()[:6]
        text = template.format(title=budget.title[:20])
        items.append(f"[{h}] {text}")

    return items


def _fallback_figure(budget: SectionBudget, fig_idx: int, seed: int) -> Dict[str, Any]:
    """Generate fallback figure placeholder."""
    import hashlib

    h = hashlib.md5(f"{seed}:{budget.section_id}:fig:{fig_idx}".encode()).hexdigest()[:6]
    figure_types = ["Process Flow", "Architecture Diagram", "Data Flow", "System Overview"]
    fig_type = figure_types[fig_idx % len(figure_types)]

    return {
        "id": f"fig-{h}",
        "caption": f"Figure {fig_idx + 1}: {fig_type} for {budget.title[:30]}",
        "placeholder_text": f"[{fig_type} Placeholder - {h.upper()}]",
    }


def _corrupt_text(text: str, seed: int, corruption_rate: float = 0.02) -> str:
    """Add realistic OCR-like corruption to text.

    Common PDF/OCR errors:
    - 'l' → '1', 'I' → '1'
    - 'O' → '0'
    - 'rn' → 'm'
    - Missing spaces
    - Extra spaces
    - Character substitutions
    """
    import random

    if corruption_rate <= 0:
        return text

    rng = random.Random(seed)
    if rng.random() > corruption_rate * 10:  # Only corrupt ~20% of texts
        return text

    chars = list(text)
    corruption_map = {
        'l': '1', 'I': '1', 'O': '0', 'o': '0',
        'S': '5', 'B': '8', 'g': '9', 'Z': '2',
    }

    num_corruptions = max(1, int(len(chars) * corruption_rate))
    for _ in range(num_corruptions):
        idx = rng.randint(0, len(chars) - 1)
        char = chars[idx]
        if char in corruption_map and rng.random() < 0.5:
            chars[idx] = corruption_map[char]
        elif char == ' ' and rng.random() < 0.3:
            chars[idx] = ''  # Missing space
        elif char.isalpha() and rng.random() < 0.2:
            chars[idx] = ''  # Missing character

    return ''.join(chars)


def generate_section_content(
    budget: SectionBudget,
    seed: int = 42,
    corruption_rate: float = 0.02,
) -> Dict[str, Any]:
    """Generate content for a section using sampler hints.

    Args:
        budget: SectionBudget with sampler_hints populated
        seed: Random seed for deterministic generation
        corruption_rate: Probability of OCR-like corruption per text

    Returns:
        Dict with paragraphs, tables, lists, figures keys for CloneBuilder
    """
    content: Dict[str, Any] = {
        "paragraphs": [],
        "tables": [],
        "lists": [],
        "figures": [],
    }
    hints = budget.sampler_hints

    # Determine paragraph count from hints or budget
    para_count = budget.paragraph_count
    if hints.get("avg_char_count"):
        para_count = max(1, hints["avg_char_count"] // 500)
    para_count = max(1, min(para_count, 8))

    # Determine content type from hints or budget
    content_type = _map_content_type(budget)
    if hints.get("content_votes"):
        votes = hints["content_votes"]
        if votes:
            top_vote = max(votes, key=votes.get)
            if top_vote in ("requirements", "requirement"):
                content_type = "requirement"
            elif top_vote in ("prose", "body"):
                content_type = "prose"

    domain = _map_domain(budget)

    # Generate paragraphs
    if _corpus_create_text is not None:
        try:
            chunks = _corpus_create_text(
                content_type=content_type,
                domain=domain,
                count=para_count,
                seed=seed + budget.section_id,
            )
            content["paragraphs"] = [c["text"] for c in chunks if c.get("text")]
        except Exception:
            content["paragraphs"] = _fallback_paragraphs(budget, para_count, seed)
    else:
        content["paragraphs"] = _fallback_paragraphs(budget, para_count, seed)

    # Ensure we have at least one paragraph
    if not content["paragraphs"]:
        content["paragraphs"] = _fallback_paragraphs(budget, para_count, seed)

    # Apply occasional corruption to paragraphs
    if corruption_rate > 0:
        content["paragraphs"] = [
            _corrupt_text(p, seed + i, corruption_rate)
            for i, p in enumerate(content["paragraphs"])
        ]

    # Generate tables based on budget.table_count and hints
    table_count = budget.table_count
    if hints.get("table_windows"):
        table_count = max(table_count, hints["table_windows"])

    for i in range(table_count):
        content["tables"].append(_fallback_table(budget, i, seed))

    # Generate lists based on budget.list_count
    list_count = budget.list_count
    for i in range(list_count):
        item_count = 4 + (i % 4)  # 4-7 items per list
        items = _fallback_list_items(budget, item_count, seed + i * 100)
        content["lists"].append({
            "items": items,
            "bullet_type": "bullet" if i % 2 == 0 else "number",
        })

    # Generate figures based on budget.figure_count
    for i in range(budget.figure_count):
        content["figures"].append(_fallback_figure(budget, i, seed))

    return content


def make_content_generator(seed: int = 42):
    """Return a content generator function for CloneBuilder.

    Usage:
        builder = CloneBuilder(plan)
        manifest = builder.build("out.pdf", content_generator=make_content_generator(seed=42))
    """
    def generator(budget: SectionBudget) -> Dict[str, Any]:
        return generate_section_content(budget, seed)
    return generator


# =============================================================================
# LLM-Backed Synthetic Content Generator
# =============================================================================

def _build_system_prompt() -> str:
    """Build system prompt with behavioral rules for PDF clone content generation."""
    return """You generate synthetic, renderable content for structurally similar PDF clones of technical standards documents.

Your task is to produce synthetic section content from a provided manifest.

Your output is used by a document renderer. Therefore, you must generate:
1. realistic synthetic technical content, and
2. lightweight layout and structure hints that are easy to validate.

You must follow these rules exactly:

1. Output exactly one valid JSON object and nothing else.
2. Do not output markdown, code fences, comments, explanations, or notes.
3. Preserve every provided section ID exactly as given.
4. Preserve each provided heading exactly as given, including numbering and punctuation.
5. Include every section exactly once.
6. Do not add extra sections or extra top-level keys.
7. All arrays and objects required by the schema must always be present, even when empty.
8. Content must be fully synthetic and must not copy or closely paraphrase real standards text.
9. Use a formal, precise, NIST/FIPS-style government technical tone.
10. Requirements sections must use normative language such as "shall", "must", and "organizations are required to".
11. Non-requirements sections must use explanatory technical language and avoid excessive normative wording.
12. Child sections should elaborate on parent topics without repeating the same content.
13. Keep content compact, renderable, and structurally regular.

Paragraph rules:
14. Each section must contain 2 to 4 paragraph objects.
15. Each paragraph object must contain:
   - text
   - role
   - emphasis_level
16. Each paragraph text must be 2 to 4 sentences.
17. role must be one of: "overview", "scope", "requirements", "guidance", "rationale".
18. emphasis_level must be one of: "low", "medium", "high".

Table rules:
19. Generate exactly the number of tables specified in the manifest.
20. Each table object must contain:
   - table_title
   - table_type
   - headers
   - rows
   - width_hint
21. table_type must be one of: "control_matrix", "requirement_summary", "role_responsibility", "reference_mapping", "status_matrix".
22. width_hint must be one of: "narrow", "medium", "wide".
23. Tables must have 3 to 5 headers and 3 to 6 rows.
24. Every row must have the same number of cells as headers.
25. Keep cell text short and renderable.

Figure rules:
26. Generate exactly the number of figures specified in the manifest.
27. Each figure object must contain:
   - figure_caption
   - figure_type
   - visual_complexity
   - description
28. figure_caption must begin with "Figure N:".
29. figure_type must be one of: "architecture", "workflow", "control_flow", "hierarchy", "boundary_diagram".
30. visual_complexity must be one of: "simple", "moderate", "dense".
31. description must be 1 to 2 sentences describing what the synthetic figure should depict.

Section-level hint rules:
32. Each section object must contain:
   - section_title
   - section_role
   - requirement_intensity
   - density_hint
   - paragraphs
   - tables
   - figures
33. section_role must be one of: "front_matter", "overview", "requirements", "subrequirements".
34. requirement_intensity must be one of: "none", "light", "moderate", "heavy".
35. density_hint must be one of: "light", "medium", "dense".

Domain guidance:
- Target style: NIST/FIPS-like cybersecurity standards document.
- Use realistic terminology related to security controls, access control, account management, authentication, authorization, auditability, interfaces, boundaries, and compliance.
- Realistic identifiers such as AC-1, AC-2, IA-1, AU-2 may be used as synthetic examples.
- Do not invent stories, case studies, vendor names, or marketing language.

Self-check before output:
- Is the output valid JSON?
- Are all required section IDs present exactly once?
- Do all section titles exactly match the manifest headings?
- Does each section have 2 to 4 paragraph objects?
- Does each section have exactly the required number of tables and figures?
- Are all enum-like fields using only allowed values?
- Is there any prose outside the JSON object? If so, remove it."""


def _infer_section_role(budget: SectionBudget) -> str:
    """Infer section_role from budget hints."""
    title_lower = budget.title.lower()

    # Check if subsection by depth or numbered title pattern
    is_subsection = budget.depth > 1
    if not is_subsection:
        title_parts = budget.title.split()
        if title_parts:
            num_part = title_parts[0].rstrip(".")
            if "." in num_part:  # e.g., "2.1" or "3.1.2"
                is_subsection = True

    # Front matter keywords only apply to top-level sections (depth=1, no dot in number)
    if not is_subsection and any(kw in title_lower for kw in ("introduction", "scope", "applicability", "background")):
        return "front_matter"

    # Overview for purpose/scope subsections
    if any(kw in title_lower for kw in ("purpose", "scope", "overview")):
        return "overview"

    if budget.has_requirements:
        return "subrequirements" if is_subsection else "requirements"

    return "overview"


def _infer_requirement_intensity(budget: SectionBudget) -> str:
    """Infer requirement_intensity from budget hints."""
    if not budget.has_requirements:
        return "none"
    # More tables/figures usually means heavier requirements
    if budget.table_count >= 2 or budget.figure_count >= 2:
        return "heavy"
    if budget.table_count >= 1 or budget.figure_count >= 1:
        return "moderate"
    return "light"


def _infer_density_hint(budget: SectionBudget) -> str:
    """Infer density_hint from budget hints."""
    hints = budget.sampler_hints
    if hints.get("avg_char_count", 0) > 2000:
        return "dense"
    if hints.get("avg_char_count", 0) > 800:
        return "medium"
    if budget.table_count >= 2 or budget.figure_count >= 2:
        return "dense"
    if budget.table_count >= 1 or budget.figure_count >= 1:
        return "medium"
    return "light"


def _build_toc_prompt(budgets: List[SectionBudget], domain: str) -> str:
    """Build user prompt with manifest and exact JSON template with layout hints."""
    # Build manifest lines
    manifest_lines = []
    for b in budgets:
        manifest_lines.append(
            f'- Section ID {b.section_id} -> heading: "{b.title}", '
            f'tables: {b.table_count}, figures: {b.figure_count}, '
            f'requirements: {"true" if b.has_requirements else "false"}'
        )
    manifest_str = "\n".join(manifest_lines)

    # Build exact JSON template for all sections with enriched schema
    template_parts = []
    for b in budgets:
        section_role = _infer_section_role(b)
        req_intensity = _infer_requirement_intensity(b)
        density = _infer_density_hint(b)

        # Determine paragraph role based on section characteristics
        para_role = "requirements" if b.has_requirements else "overview"
        para_emphasis = "high" if b.has_requirements else "medium"

        # Tables template with enriched fields
        if b.table_count > 0:
            table_type = "control_matrix" if b.has_requirements else "reference_mapping"
            tables_list = []
            for ti in range(b.table_count):
                tables_list.append(f'''        {{
          "table_title": "Table {ti + 1}. ...",
          "table_type": "{table_type}",
          "width_hint": "medium",
          "headers": ["...", "...", "..."],
          "rows": [
            ["...", "...", "..."]
          ]
        }}''')
            tables_str = "[\n" + ",\n".join(tables_list) + "\n      ]"
        else:
            tables_str = "[]"

        # Figures template with enriched fields
        if b.figure_count > 0:
            fig_type = "workflow" if b.has_requirements else "architecture"
            figs_list = []
            for fi in range(b.figure_count):
                figs_list.append(f'''        {{
          "figure_caption": "Figure {fi + 1}: ...",
          "figure_type": "{fig_type}",
          "visual_complexity": "moderate",
          "description": "..."
        }}''')
            figs_str = "[\n" + ",\n".join(figs_list) + "\n      ]"
        else:
            figs_str = "[]"

        template_parts.append(f'''    "{b.section_id}": {{
      "section_title": "{b.title}",
      "section_role": "{section_role}",
      "requirement_intensity": "{req_intensity}",
      "density_hint": "{density}",
      "paragraphs": [
        {{
          "text": "...",
          "role": "{para_role}",
          "emphasis_level": "{para_emphasis}"
        }}
      ],
      "tables": {tables_str},
      "figures": {figs_str}
    }}''')

    template_str = ",\n".join(template_parts)

    # Section-specific content guidance
    content_guidance = []
    for b in budgets:
        title_short = b.title[:40]
        if b.has_requirements:
            if b.table_count > 0 and b.figure_count > 0:
                content_guidance.append(
                    f"- Section {b.section_id} ({title_short}) should focus on specific control requirements "
                    f"with supporting matrix and visual workflow."
                )
            elif b.table_count > 0:
                content_guidance.append(
                    f"- Section {b.section_id} ({title_short}) should present requirements with tabular control mappings."
                )
            else:
                content_guidance.append(
                    f"- Section {b.section_id} ({title_short}) should use normative standards language."
                )
        else:
            if "introduction" in b.title.lower():
                content_guidance.append(
                    f"- Section {b.section_id} ({title_short}) should introduce the document context and scope."
                )
            elif "purpose" in b.title.lower():
                content_guidance.append(
                    f"- Section {b.section_id} ({title_short}) should explain the intended use and audience."
                )
            else:
                content_guidance.append(
                    f"- Section {b.section_id} ({title_short}) should use explanatory technical language."
                )

    # Build examples section showing what each content type should look like
    examples_section = '''
## Content Examples

### Requirements paragraph (role="requirements"):
"text": "Organizations SHALL implement access control policies that define the types of access authorizations, process for obtaining access, and procedures for reviewing and updating access control policies. The organization SHALL employ automated mechanisms to support the management of access control policies.",
"role": "requirements",
"emphasis_level": "high"

### Overview paragraph (role="overview"):
"text": "This section establishes the foundational requirements for access control within federal information systems. Access control is a critical security function that supports the principles of least privilege and separation of duties.",
"role": "overview",
"emphasis_level": "medium"

### Table (control_matrix):
{
  "table_title": "Table 3-1. Access Control Family",
  "table_type": "control_matrix",
  "width_hint": "wide",
  "headers": ["Control ID", "Control Name", "Baseline Impact"],
  "rows": [
    ["AC-1", "Policy and Procedures", "Low, Moderate, High"],
    ["AC-2", "Account Management", "Low, Moderate, High"],
    ["AC-3", "Access Enforcement", "Low, Moderate, High"]
  ]
}

### Table (requirement_summary):
{
  "table_title": "Table 2-1. Document Applicability",
  "table_type": "requirement_summary",
  "width_hint": "medium",
  "headers": ["System Category", "Applicability", "Tailoring Allowed"],
  "rows": [
    ["High Value Asset", "Required", "Limited"],
    ["General Support System", "Required", "Yes"]
  ]
}

### Figure (workflow):
{
  "figure_caption": "Figure 2-1: Access Request and Authorization Flow",
  "figure_type": "workflow",
  "visual_complexity": "moderate",
  "description": "A sequential flowchart showing user access request submission, supervisor approval, security review, and final provisioning steps with decision points for denial and escalation."
}

### Figure (architecture):
{
  "figure_caption": "Figure 1-1: Security Control Framework Overview",
  "figure_type": "architecture",
  "visual_complexity": "dense",
  "description": "A layered diagram showing the relationship between organizational controls at the top, system-level controls in the middle, and technical enforcement mechanisms at the bottom, with connecting arrows indicating inheritance and implementation."
}
'''

    return f"""Generate synthetic content for a NIST/FIPS-style cybersecurity technical document using the following manifest.

Manifest:
{manifest_str}

{examples_section}

Return exactly one JSON object with this shape:
{{
  "sections": {{
{template_str}
  }}
}}

Additional guidance:
{chr(10).join(content_guidance)}
- Tables should contain realistic short-form data such as control IDs, requirement summaries, applicability, responsibility, references, or status values.
- Figure descriptions should be plausible for the section and useful for a standards-style technical document.

Return ONLY the JSON object."""


def _build_single_section_system_prompt() -> str:
    """Build system prompt for single-section content generation."""
    return """You generate synthetic content for ONE section of a NIST/FIPS-style technical document.

Output exactly one valid JSON object and nothing else. No markdown, code fences, or explanations.

Rules:
1. Content must be fully synthetic - do not copy real standards text.
2. Use formal, precise government technical tone.
3. Requirements sections use normative language: "shall", "must", "organizations are required to".
4. Non-requirements sections use explanatory technical language.
5. Each paragraph must be 2-4 sentences with role and emphasis_level.
6. Generate exactly the number of tables and figures specified.
7. Tables contain realistic short-form data (control IDs, requirements, applicability).
8. Figure descriptions should be plausible for a standards document."""


def _build_single_section_prompt(budget: SectionBudget, domain: str) -> str:
    """Build user prompt for generating content for ONE section."""
    section_role = _infer_section_role(budget)
    req_intensity = _infer_requirement_intensity(budget)
    density = _infer_density_hint(budget)
    para_role = "requirements" if budget.has_requirements else "overview"
    para_emphasis = "high" if budget.has_requirements else "medium"

    # Tables template
    if budget.table_count > 0:
        table_type = "control_matrix" if budget.has_requirements else "reference_mapping"
        table_items = []
        for i in range(budget.table_count):
            table_items.append(
                '{"table_title": "Table ' + str(i+1) + '. ...", '
                '"table_type": "' + table_type + '", '
                '"width_hint": "medium", '
                '"headers": ["...", "...", "..."], '
                '"rows": [["...", "...", "..."]]}'
            )
        tables_template = "[" + ", ".join(table_items) + "]"
    else:
        tables_template = "[]"

    # Figures template
    if budget.figure_count > 0:
        fig_type = "workflow" if budget.has_requirements else "architecture"
        fig_items = []
        for i in range(budget.figure_count):
            fig_items.append(
                '{"figure_caption": "Figure ' + str(i+1) + ': ...", '
                '"figure_type": "' + fig_type + '", '
                '"visual_complexity": "moderate", '
                '"description": "..."}'
            )
        figs_template = "[" + ", ".join(fig_items) + "]"
    else:
        figs_template = "[]"

    # Content guidance
    if budget.has_requirements:
        guidance = "Use normative SHALL/MUST language for security requirements."
    elif "introduction" in budget.title.lower():
        guidance = "Introduce the document context and scope."
    elif "purpose" in budget.title.lower():
        guidance = "Explain the intended use and audience."
    else:
        guidance = "Use explanatory technical language."

    return f"""Generate synthetic content for this {domain} document section:

Section ID: {budget.section_id}
Title: "{budget.title}"
Tables required: {budget.table_count}
Figures required: {budget.figure_count}
Requirements section: {budget.has_requirements}

Guidance: {guidance}

Return JSON with this exact shape:
{{
  "section_title": "{budget.title}",
  "section_role": "{section_role}",
  "requirement_intensity": "{req_intensity}",
  "density_hint": "{density}",
  "paragraphs": [
    {{"text": "...", "role": "{para_role}", "emphasis_level": "{para_emphasis}"}},
    {{"text": "...", "role": "{para_role}", "emphasis_level": "{para_emphasis}"}}
  ],
  "tables": {tables_template},
  "figures": {figs_template}
}}

Return ONLY the JSON object."""


async def make_llm_content_generator_batch(
    budgets: List[SectionBudget],
    domain: str = "government security",
    model: str = "text-gemini",
    seed: int = 42,
    timeout: float = 60.0,
    batch_id: str = None,
):
    """Generate content via parallel single-section LLM calls using scillm batch pattern.

    Uses scillm's recommended pattern:
    - One HTTP call per section (not batching sections into one prompt)
    - asyncio.Semaphore(chunk_size) for parallelism
    - scillm_metadata with batch_id/item_id for auto-resume on failure

    Args:
        budgets: List of SectionBudget from RenderPlan
        domain: Domain context for content style
        model: scillm model alias (default: "text-gemini")
        seed: For fallback content if LLM fails
        timeout: Per-request timeout in seconds
        batch_id: Optional batch identifier for resume capability

    Returns:
        Generator function that returns content dict for each SectionBudget
    """
    import asyncio
    import httpx
    import json
    import time
    import uuid

    batch_id = batch_id or str(uuid.uuid4())[:8]
    content_cache: Dict[int, Dict[str, Any]] = {}
    system_prompt = _build_single_section_system_prompt()

    # Query scillm for optimal chunk_size
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://localhost:4001/v1/scillm/concurrency?model={model}",
                headers={"Authorization": "Bearer sk-dev-proxy-123"},
                timeout=5.0,
            )
            chunk_size = resp.json().get("chunk_size", 2)
    except Exception:
        chunk_size = 2  # Safe default for Gemini free tier

    print(f"Generating content for {len(budgets)} sections via {model} (chunk_size={chunk_size})...")
    start = time.time()

    # Track errors for self-correction on retry
    error_messages: Dict[int, str] = {}

    async def generate_one(client: httpx.AsyncClient, budget: SectionBudget, error_context: str = None, max_retries: int = 3) -> tuple:
        """Generate content for a single section with exponential backoff for queue busy."""
        import random
        user_prompt = _build_single_section_prompt(budget, domain)

        # Add self-correction context on retry
        if error_context:
            user_prompt += f"""

RETRY ATTEMPT - Previous attempt failed with error:
{error_context}

Please ensure your response is valid JSON with all required fields (paragraphs, tables, lists, figures).
Double-check that arrays are properly formatted and all strings are properly escaped."""

        for attempt in range(max_retries):
            try:
                resp = await client.post(
                    "http://localhost:4001/v1/chat/completions",
                    headers={
                        "Authorization": "Bearer sk-dev-proxy-123",
                        "X-Caller-Skill": "pdf_oxide.clone",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "scillm_metadata": {
                            "batch_id": batch_id,
                            "item_id": str(budget.section_id),
                        },
                    },
                    timeout=timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                content_text = data["choices"][0]["message"]["content"]
                parsed = json.loads(content_text)
                print(f"  [{budget.section_id}] {budget.title[:40]:<42} OK")
                return (budget.section_id, parsed)
            except httpx.HTTPStatusError as e:
                # Check for queue busy (another batch running) - retry with backoff
                if e.response.status_code == 429:
                    response_text = e.response.text if hasattr(e.response, 'text') else str(e)
                    if "BUSY" in response_text or "queue" in response_text.lower():
                        wait = 30 + random.uniform(0, 30) * (attempt + 1)
                        print(f"  [{budget.section_id}] Queue busy, waiting {wait:.0f}s (attempt {attempt + 1}/{max_retries})")
                        await asyncio.sleep(wait)
                        continue
                # Non-busy 429 or other HTTP error - record and fail
                error_messages[budget.section_id] = str(e)
                print(f"  [{budget.section_id}] {budget.title[:40]:<42} FAILED: {e}")
                return (budget.section_id, None)
            except Exception as e:
                error_messages[budget.section_id] = str(e)
                print(f"  [{budget.section_id}] {budget.title[:40]:<42} FAILED: {e}")
                return (budget.section_id, None)

        # Max retries exceeded
        error_messages[budget.section_id] = "Max retries exceeded (queue busy)"
        print(f"  [{budget.section_id}] {budget.title[:40]:<42} FAILED: Max retries exceeded")
        return (budget.section_id, None)

    # Chunked processing: process chunk_size sections at a time
    # This prevents queue buildup in the proxy
    results = []
    async with httpx.AsyncClient() as client:
        for chunk_start in range(0, len(budgets), chunk_size):
            chunk = budgets[chunk_start:chunk_start + chunk_size]
            chunk_tasks = [generate_one(client, b) for b in chunk]
            chunk_results = await asyncio.gather(*chunk_tasks)
            results.extend(chunk_results)

        # Second pass: retry failed sections with self-correction prompt
        failed_ids = {sid for sid, parsed in results if parsed is None}
        if failed_ids:
            failed_budgets = [b for b in budgets if b.section_id in failed_ids]
            print(f"\nRetrying {len(failed_budgets)} failed sections with error context...")
            retry_results = []
            for chunk_start in range(0, len(failed_budgets), chunk_size):
                chunk = failed_budgets[chunk_start:chunk_start + chunk_size]
                # Pass error context for self-correction
                chunk_tasks = [generate_one(client, b, error_messages.get(b.section_id)) for b in chunk]
                chunk_results = await asyncio.gather(*chunk_tasks)
                retry_results.extend(chunk_results)
            # Update results with retry outcomes
            retry_map = {sid: parsed for sid, parsed in retry_results}
            results = [(sid, retry_map.get(sid, parsed) if sid in failed_ids else parsed)
                       for sid, parsed in results]

    # Process results into cache
    success_count = 0
    for section_id, parsed in results:
        if parsed is None:
            # Find the budget for fallback (only after retry failed)
            budget = next((b for b in budgets if b.section_id == section_id), None)
            if budget:
                content_cache[section_id] = generate_section_content(budget, seed)
            continue

        success_count += 1
        # Extract paragraph texts from enriched paragraph objects
        raw_paragraphs = parsed.get("paragraphs", [])
        paragraphs = []
        paragraph_metadata = []
        for p in raw_paragraphs:
            if isinstance(p, dict):
                paragraphs.append(p.get("text", ""))
                paragraph_metadata.append({
                    "role": p.get("role", "overview"),
                    "emphasis_level": p.get("emphasis_level", "medium"),
                })
            else:
                paragraphs.append(str(p))
                paragraph_metadata.append({"role": "overview", "emphasis_level": "medium"})

        # Extract figure data
        raw_figures = parsed.get("figures", [])
        figures = []
        figure_descriptions = []
        for i, f in enumerate(raw_figures):
            if isinstance(f, dict):
                figure_descriptions.append(f.get("figure_caption", f"Figure {i+1}"))
                figures.append({
                    "id": f"fig-{section_id}-{i}",
                    "caption": f.get("figure_caption", f"Figure {i+1}"),
                    "figure_type": f.get("figure_type", "workflow"),
                    "visual_complexity": f.get("visual_complexity", "moderate"),
                    "description": f.get("description", ""),
                    "placeholder_text": f"[{f.get('figure_type', 'Figure')} Placeholder]",
                })
            else:
                figure_descriptions.append(str(f))
                figures.append({
                    "id": f"fig-{section_id}-{i}",
                    "caption": str(f),
                    "placeholder_text": "[Figure Placeholder]",
                })

        # Extract table data
        raw_tables = parsed.get("tables", [])
        tables = []
        for t in raw_tables:
            if isinstance(t, dict):
                tables.append({
                    "table_title": t.get("table_title", ""),
                    "table_type": t.get("table_type", "reference_mapping"),
                    "width_hint": t.get("width_hint", "medium"),
                    "headers": t.get("headers", []),
                    "rows": t.get("rows", []),
                })

        content_cache[section_id] = {
            "section_title": parsed.get("section_title", ""),
            "section_role": parsed.get("section_role", "overview"),
            "requirement_intensity": parsed.get("requirement_intensity", "none"),
            "density_hint": parsed.get("density_hint", "medium"),
            "paragraphs": paragraphs,
            "paragraph_metadata": paragraph_metadata,
            "tables": tables,
            "figures": figures,
            "figure_descriptions": figure_descriptions,
            "lists": [],
        }

    elapsed = time.time() - start
    print(f"LLM batch generation: {success_count}/{len(budgets)} sections in {elapsed:.1f}s")

    def generator(budget: SectionBudget) -> Dict[str, Any]:
        if budget.section_id in content_cache:
            return content_cache[budget.section_id]
        return generate_section_content(budget, seed)

    return generator


def make_llm_content_generator(
    budgets: List[SectionBudget],
    domain: str = "government security",
    model: str = "text-gemini",
    seed: int = 42,
    timeout: float = 300.0,
):
    """Generate all content via single LLM call, serve deterministically.

    Args:
        budgets: List of SectionBudget from RenderPlan
        domain: Domain context for content style (e.g., "NIST CUI security")
        model: scillm model alias (default: "text-gemini" - cascades to paid on 429)
        seed: For fallback content if LLM fails
        timeout: HTTP timeout in seconds (default: 300s for large documents)
        ollama_fallback: Try local Ollama if scillm returns 429 (default: True)

    Returns:
        Generator function that returns content dict for each SectionBudget:
        {
            "section_title": str,
            "section_role": str,  # front_matter, overview, requirements, subrequirements
            "requirement_intensity": str,  # none, light, moderate, heavy
            "density_hint": str,  # light, medium, dense
            "paragraphs": List[str],
            "paragraph_metadata": List[{"role": str, "emphasis_level": str}],
            "tables": List[{
                "table_title": str,
                "table_type": str,  # control_matrix, requirement_summary, etc.
                "width_hint": str,  # narrow, medium, wide
                "headers": List[str],
                "rows": List[List[str]]
            }],
            "figures": List[{
                "id": str,
                "caption": str,
                "figure_type": str,  # architecture, workflow, control_flow, etc.
                "visual_complexity": str,  # simple, moderate, dense
                "description": str,
                "placeholder_text": str
            }],
            "figure_descriptions": List[str],  # Legacy compatibility
            "lists": List[dict],
        }

    Usage:
        plan = derive_render_plan(...)
        gen = make_llm_content_generator(plan.section_budgets, domain="NIST security")
        manifest = build_clone(plan, "out.pdf", content_generator=gen)
    """
    import httpx
    import json
    import time

    # Build two-part prompt (system + user)
    system_prompt = _build_system_prompt()
    user_prompt = _build_toc_prompt(budgets, domain)
    content_cache: Dict[int, Dict[str, Any]] = {}
    content_text = None

    print(f"Generating synthetic content for {len(budgets)} sections via {model}...")
    start = time.time()

    try:
        resp = httpx.post(
            "http://localhost:4001/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-dev-proxy-123",
                "X-Caller-Skill": "pdf_oxide.clone",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},  # Proxy handles JSON validation + retry
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content_text = data["choices"][0]["message"]["content"]
        parsed = json.loads(content_text)  # Proxy with response_format ensures valid JSON

        elapsed = time.time() - start
        print(f"LLM content generation completed in {elapsed:.1f}s")

        # Index by section_id with full enriched structure
        for sid_str, section_content in parsed.get("sections", {}).items():
            try:
                sid = int(sid_str)

                # Extract paragraph texts from enriched paragraph objects
                raw_paragraphs = section_content.get("paragraphs", [])
                paragraphs = []
                paragraph_metadata = []
                for p in raw_paragraphs:
                    if isinstance(p, dict):
                        paragraphs.append(p.get("text", ""))
                        paragraph_metadata.append({
                            "role": p.get("role", "overview"),
                            "emphasis_level": p.get("emphasis_level", "medium"),
                        })
                    else:
                        # Fallback for plain string paragraphs
                        paragraphs.append(str(p))
                        paragraph_metadata.append({"role": "overview", "emphasis_level": "medium"})

                # Extract figure data from enriched figure objects
                raw_figures = section_content.get("figures", [])
                figures = []
                figure_descriptions = []
                for i, f in enumerate(raw_figures):
                    if isinstance(f, dict):
                        figure_descriptions.append(f.get("figure_caption", f"Figure {i+1}"))
                        figures.append({
                            "id": f"fig-{sid}-{i}",
                            "caption": f.get("figure_caption", f"Figure {i+1}"),
                            "figure_type": f.get("figure_type", "workflow"),
                            "visual_complexity": f.get("visual_complexity", "moderate"),
                            "description": f.get("description", ""),
                            "placeholder_text": f"[{f.get('figure_type', 'Figure')} Placeholder]",
                        })
                    else:
                        # Fallback for plain string descriptions
                        figure_descriptions.append(str(f))
                        figures.append({
                            "id": f"fig-{sid}-{i}",
                            "caption": str(f),
                            "placeholder_text": "[Figure Placeholder]",
                        })

                # Extract table data with enriched fields
                raw_tables = section_content.get("tables", [])
                tables = []
                for t in raw_tables:
                    if isinstance(t, dict):
                        tables.append({
                            "table_title": t.get("table_title", ""),
                            "table_type": t.get("table_type", "reference_mapping"),
                            "width_hint": t.get("width_hint", "medium"),
                            "headers": t.get("headers", []),
                            "rows": t.get("rows", []),
                        })

                content_cache[sid] = {
                    "section_title": section_content.get("section_title", ""),
                    "section_role": section_content.get("section_role", "overview"),
                    "requirement_intensity": section_content.get("requirement_intensity", "none"),
                    "density_hint": section_content.get("density_hint", "medium"),
                    "paragraphs": paragraphs,
                    "paragraph_metadata": paragraph_metadata,
                    "tables": tables,
                    "figures": figures,
                    "figure_descriptions": figure_descriptions,
                    "lists": [],  # Populated by fallback if needed
                }
            except (ValueError, TypeError):
                continue

        print(f"Cached content for {len(content_cache)}/{len(budgets)} sections")

    except httpx.HTTPStatusError as e:
        elapsed = time.time() - start
        # Read the proxy's error response body for advice/recommendation
        try:
            error_body = e.response.json()
            error_info = error_body.get("error", {})
            print(f"[llm_content_generator] HTTP {e.response.status_code} after {elapsed:.1f}s")
            print(f"  message: {error_info.get('message', str(e))}")
            if error_info.get("advice"):
                print(f"  advice: {error_info['advice']}")
            if error_info.get("recommendation"):
                print(f"  recommendation: {error_info['recommendation']}")
        except Exception:
            print(f"[llm_content_generator] HTTP error after {elapsed:.1f}s: {e}")
        print("Using fallback content generator")
    except Exception as e:
        elapsed = time.time() - start
        print(f"[llm_content_generator] Failed after {elapsed:.1f}s: {type(e).__name__}: {e}")
        print("Using fallback content generator")

    def generator(budget: SectionBudget) -> Dict[str, Any]:
        if budget.section_id in content_cache:
            return content_cache[budget.section_id]
        # Fallback to deterministic placeholder
        return generate_section_content(budget, seed)

    return generator


async def make_llm_content_generator_async(
    budgets: List[SectionBudget],
    domain: str = "government security",
    model: str = "text-gemini",
    seed: int = 42,
    timeout: float = 300.0,
):
    """Async version of make_llm_content_generator with enriched schema."""
    import httpx
    import json
    import time

    # Build two-part prompt (system + user)
    system_prompt = _build_system_prompt()
    user_prompt = _build_toc_prompt(budgets, domain)
    content_cache: Dict[int, Dict[str, Any]] = {}

    print(f"Generating synthetic content for {len(budgets)} sections via {model} (async)...")
    start = time.time()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://localhost:4001/v1/chat/completions",
                headers={
                    "Authorization": "Bearer sk-dev-proxy-123",
                    "X-Caller-Skill": "pdf_oxide.clone",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},  # Proxy handles JSON validation
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            content_text = data["choices"][0]["message"]["content"]
            parsed = json.loads(content_text)

            elapsed = time.time() - start
            print(f"LLM content generation completed in {elapsed:.1f}s")

            # Index by section_id with full enriched structure
            for sid_str, section_content in parsed.get("sections", {}).items():
                try:
                    sid = int(sid_str)

                    # Extract paragraph texts from enriched paragraph objects
                    raw_paragraphs = section_content.get("paragraphs", [])
                    paragraphs = []
                    paragraph_metadata = []
                    for p in raw_paragraphs:
                        if isinstance(p, dict):
                            paragraphs.append(p.get("text", ""))
                            paragraph_metadata.append({
                                "role": p.get("role", "overview"),
                                "emphasis_level": p.get("emphasis_level", "medium"),
                            })
                        else:
                            paragraphs.append(str(p))
                            paragraph_metadata.append({"role": "overview", "emphasis_level": "medium"})

                    # Extract figure data from enriched figure objects
                    raw_figures = section_content.get("figures", [])
                    figures = []
                    figure_descriptions = []
                    for i, f in enumerate(raw_figures):
                        if isinstance(f, dict):
                            figure_descriptions.append(f.get("figure_caption", f"Figure {i+1}"))
                            figures.append({
                                "id": f"fig-{sid}-{i}",
                                "caption": f.get("figure_caption", f"Figure {i+1}"),
                                "figure_type": f.get("figure_type", "workflow"),
                                "visual_complexity": f.get("visual_complexity", "moderate"),
                                "description": f.get("description", ""),
                                "placeholder_text": f"[{f.get('figure_type', 'Figure')} Placeholder]",
                            })
                        else:
                            figure_descriptions.append(str(f))
                            figures.append({
                                "id": f"fig-{sid}-{i}",
                                "caption": str(f),
                                "placeholder_text": "[Figure Placeholder]",
                            })

                    # Extract table data with enriched fields
                    raw_tables = section_content.get("tables", [])
                    tables = []
                    for t in raw_tables:
                        if isinstance(t, dict):
                            tables.append({
                                "table_title": t.get("table_title", ""),
                                "table_type": t.get("table_type", "reference_mapping"),
                                "width_hint": t.get("width_hint", "medium"),
                                "headers": t.get("headers", []),
                                "rows": t.get("rows", []),
                            })

                    content_cache[sid] = {
                        "section_title": section_content.get("section_title", ""),
                        "section_role": section_content.get("section_role", "overview"),
                        "requirement_intensity": section_content.get("requirement_intensity", "none"),
                        "density_hint": section_content.get("density_hint", "medium"),
                        "paragraphs": paragraphs,
                        "paragraph_metadata": paragraph_metadata,
                        "tables": tables,
                        "figures": figures,
                        "figure_descriptions": figure_descriptions,
                        "lists": [],
                    }
                except (ValueError, TypeError):
                    continue

            print(f"Cached content for {len(content_cache)}/{len(budgets)} sections")

    except Exception as e:
        elapsed = time.time() - start
        print(f"[llm_content_generator_async] LLM call failed after {elapsed:.1f}s: {e}")
        print("Using fallback content generator")

    def generator(budget: SectionBudget) -> Dict[str, Any]:
        if budget.section_id in content_cache:
            return content_cache[budget.section_id]
        return generate_section_content(budget, seed)

    return generator
