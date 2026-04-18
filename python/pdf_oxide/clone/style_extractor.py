"""Extract visual style profile from source PDF using Opus VLM.

Uses scillm's native PDF reading capability to analyze representative pages
and return deterministic style parameters that map to existing presets.

Pipeline position:
    clone_profiler → style_extractor → manifest_generator → clone_builder
"""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # pymupdf
import httpx

# Available presets the model can select from
AVAILABLE_TABLE_PRESETS = [
    "data_grid", "requirements_matrix", "comparison_matrix", "ledger",
    "sectioned_grid", "gridless_report", "control_matrix", "risk_register",
    "traceability_matrix", "gap_analysis", "poam", "assessment_results",
    "audit_findings", "evidence_summary", "compliance_status", "test_results",
    "parameter_table", "specification_table", "interface_table", "hazard_analysis",
    "action_items", "decision_log", "change_requests", "schedule_table",
    "budget_table", "raci_matrix", "meeting_minutes", "revision_history",
    "glossary", "acronyms", "references", "personnel_table", "distribution_list",
    "signature_block", "approval_block", "checklist",
]

AVAILABLE_HEADER_PRESETS = [
    "doc_title_header", "section_header", "classified_header", "versioned_header",
    "chapter_header", "minimal_header", "dual_logo_header", "nist_header",
]

AVAILABLE_FOOTER_PRESETS = [
    "page_number_footer", "classified_footer", "revision_footer", "copyright_footer",
    "doc_control_footer", "draft_footer", "distribution_footer", "minimal_footer",
]

AVAILABLE_CALLOUT_PRESETS = [
    "note_box", "warning_box", "danger_box", "tip_box", "example_box",
    "definition_box", "quote_block", "code_block", "requirement_box",
    "finding_box", "recommendation_box", "reference_box",
]


@dataclass
class FontProfile:
    """Font configuration extracted from source PDF."""
    body: str = "Helvetica"
    heading: str = "Helvetica-Bold"
    code: str = "Courier"
    body_size: float = 10.0
    heading_sizes: Dict[int, float] = field(default_factory=lambda: {1: 16, 2: 14, 3: 12, 4: 11})


@dataclass
class ColorProfile:
    """Color scheme extracted from source PDF."""
    heading: str = "#000000"
    body: str = "#333333"
    table_header_bg: str = "#4472C4"
    table_header_text: str = "#FFFFFF"
    table_alt_row: str = "#D9E2F3"
    callout_bg: str = "#EFF6FF"
    callout_border: str = "#60A5FA"
    link: str = "#0563C1"


@dataclass
class SpacingProfile:
    """Spacing/layout configuration."""
    paragraph_after: float = 12.0
    heading_before: float = 18.0
    heading_after: float = 10.0
    list_indent: float = 24.0
    table_padding: float = 6.0


@dataclass
class PageProfile:
    """Page layout configuration."""
    margins: Tuple[float, float, float, float] = (72, 72, 72, 72)  # L, R, T, B
    header_height: float = 36.0
    footer_height: float = 24.0
    column_count: int = 1
    page_width: float = 612.0  # letter
    page_height: float = 792.0


@dataclass
class StyleProfile:
    """Complete style profile for PDF cloning."""
    # Preset selections (must match existing presets)
    table_preset: str = "data_grid"
    header_preset: str = "doc_title_header"
    footer_preset: str = "page_number_footer"
    callout_preset: str = "note_box"

    # Raw style parameters (fallback if no preset matches)
    fonts: FontProfile = field(default_factory=FontProfile)
    colors: ColorProfile = field(default_factory=ColorProfile)
    spacing: SpacingProfile = field(default_factory=SpacingProfile)
    page: PageProfile = field(default_factory=PageProfile)

    # Detected document characteristics
    has_toc: bool = True
    has_numbered_headings: bool = True
    has_requirements: bool = False
    has_tables: bool = True
    has_figures: bool = False
    has_callouts: bool = False
    estimated_complexity: str = "moderate"  # simple, moderate, complex

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "table_preset": self.table_preset,
            "header_preset": self.header_preset,
            "footer_preset": self.footer_preset,
            "callout_preset": self.callout_preset,
            "fonts": {
                "body": self.fonts.body,
                "heading": self.fonts.heading,
                "code": self.fonts.code,
                "body_size": self.fonts.body_size,
                "heading_sizes": self.fonts.heading_sizes,
            },
            "colors": {
                "heading": self.colors.heading,
                "body": self.colors.body,
                "table_header_bg": self.colors.table_header_bg,
                "table_header_text": self.colors.table_header_text,
                "table_alt_row": self.colors.table_alt_row,
                "callout_bg": self.colors.callout_bg,
                "callout_border": self.colors.callout_border,
                "link": self.colors.link,
            },
            "spacing": {
                "paragraph_after": self.spacing.paragraph_after,
                "heading_before": self.spacing.heading_before,
                "heading_after": self.spacing.heading_after,
                "list_indent": self.spacing.list_indent,
                "table_padding": self.spacing.table_padding,
            },
            "page": {
                "margins": list(self.page.margins),
                "header_height": self.page.header_height,
                "footer_height": self.page.footer_height,
                "column_count": self.page.column_count,
                "page_width": self.page.page_width,
                "page_height": self.page.page_height,
            },
            "characteristics": {
                "has_toc": self.has_toc,
                "has_numbered_headings": self.has_numbered_headings,
                "has_requirements": self.has_requirements,
                "has_tables": self.has_tables,
                "has_figures": self.has_figures,
                "has_callouts": self.has_callouts,
                "estimated_complexity": self.estimated_complexity,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StyleProfile":
        """Create from JSON dict."""
        fonts = FontProfile(**data.get("fonts", {}))
        colors = ColorProfile(**data.get("colors", {}))
        spacing = SpacingProfile(**data.get("spacing", {}))
        page_data = data.get("page", {})
        if "margins" in page_data:
            page_data["margins"] = tuple(page_data["margins"])
        page = PageProfile(**page_data)
        chars = data.get("characteristics", {})

        return cls(
            table_preset=data.get("table_preset", "data_grid"),
            header_preset=data.get("header_preset", "doc_title_header"),
            footer_preset=data.get("footer_preset", "page_number_footer"),
            callout_preset=data.get("callout_preset", "note_box"),
            fonts=fonts,
            colors=colors,
            spacing=spacing,
            page=page,
            has_toc=chars.get("has_toc", True),
            has_numbered_headings=chars.get("has_numbered_headings", True),
            has_requirements=chars.get("has_requirements", False),
            has_tables=chars.get("has_tables", True),
            has_figures=chars.get("has_figures", False),
            has_callouts=chars.get("has_callouts", False),
            estimated_complexity=chars.get("estimated_complexity", "moderate"),
        )


def select_representative_pages(
    pdf_path: str,
    profiler_output: Dict[str, Any],
    max_pages: int = 8,
) -> List[int]:
    """Select representative pages from source PDF based on profiler output.

    Selects pages that show:
    1. TOC page (if detected)
    2. First page with a table
    3. Requirements section first page (if detected)
    4. Header/footer example page
    5. A body content page
    """
    pages = set()

    toc_sections = profiler_output.get("toc_sections", [])
    table_shapes = profiler_output.get("table_shapes", [])
    page_signatures = profiler_output.get("page_signatures", [])

    # 1. TOC page - usually early in document
    for entry in toc_sections:
        if "table of contents" in entry.get("title", "").lower():
            pages.add(entry.get("page", 0))
            break
    else:
        # No explicit TOC, try page 1-3
        pages.add(min(2, len(page_signatures) - 1) if page_signatures else 0)

    # 2. First table page
    if table_shapes:
        first_table_page = table_shapes[0].get("page", 0)
        pages.add(first_table_page)
        # Also add a different table page if available (for variety)
        if len(table_shapes) > 3:
            pages.add(table_shapes[len(table_shapes) // 2].get("page", 0))

    # 3. Requirements section page (look for control/requirement keywords)
    req_keywords = ["control", "requirement", "shall", "security"]
    for entry in toc_sections:
        title_lower = entry.get("title", "").lower()
        if any(kw in title_lower for kw in req_keywords):
            pages.add(entry.get("page", 0))
            break

    # 4. Header/footer example - middle of document usually has stable headers
    if page_signatures:
        mid_page = len(page_signatures) // 2
        pages.add(mid_page)

    # 5. First content page (after cover/TOC)
    for entry in toc_sections:
        page = entry.get("page", 0)
        if page > 5:  # Skip early pages
            pages.add(page)
            break

    # 6. If we have glossary/appendix, include that
    for entry in toc_sections:
        title_lower = entry.get("title", "").lower()
        if "glossary" in title_lower or "appendix" in title_lower:
            pages.add(entry.get("page", 0))
            break

    # Sort and limit
    sorted_pages = sorted(pages)[:max_pages]

    # Ensure we have at least some pages
    if not sorted_pages:
        sorted_pages = list(range(min(3, len(page_signatures) or 3)))

    return sorted_pages


def extract_pages_as_mini_pdf(
    source_pdf: str,
    page_numbers: List[int],
    output_path: Optional[str] = None,
) -> str:
    """Extract specific pages into a mini-PDF for VLM analysis.

    Args:
        source_pdf: Path to source PDF
        page_numbers: 0-indexed page numbers to extract
        output_path: Optional output path (defaults to temp file)

    Returns:
        Path to the mini-PDF
    """
    doc = fitz.open(source_pdf)

    if output_path is None:
        output_path = tempfile.mktemp(suffix="_style_sample.pdf")

    # Create new document with selected pages
    mini_doc = fitz.open()

    for page_num in page_numbers:
        if 0 <= page_num < len(doc):
            mini_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)

    mini_doc.save(output_path)
    mini_doc.close()
    doc.close()

    return output_path


def _build_extraction_prompt() -> Tuple[str, str]:
    """Build system and user prompts for style extraction (v3).

    v3 improvements over v2:
    - Global tie-break rules: frequency → document-level style → structural specificity
    - Explicit fallback defaults for EVERY field
    - Handles incomplete/mixed/ambiguous samples gracefully
    - No "do not guess" contradiction — use defaults when uncertain

    Compliant with /best-practices-prompt rules:
    - Format anchor first (Rule 16)
    - Decision rules for preset selection (Rule 29)
    - Rejection criteria (Rule 7)
    - Negative examples (Rule 27)
    """

    system_prompt = f"""Return exactly one JSON object with keys: table_preset, header_preset, footer_preset, callout_preset, fonts, colors, spacing, page, characteristics.

You are a PDF visual style extractor. Analyze the attached PDF pages and select preset names that match the document's visual style.

## GLOBAL RULES (apply to ALL decisions)

### Tie-Break Resolution
When multiple presets could apply, choose using this priority:
1. FREQUENCY: The style that appears on the most pages wins
2. DOCUMENT-LEVEL: If document title contains "NIST" or "SP 800", prefer NIST-family presets
3. STRUCTURAL SPECIFICITY: More specific presets beat generic ones (control_matrix > data_grid)

### Incomplete Sample Handling
The sample may be incomplete, mixed, or visually ambiguous. For every field:
- If directly observable in the PDF → extract it
- If not visible but inferable from document context → infer it
- If neither observable nor inferable → use the FALLBACK DEFAULT listed for that field

Every field MUST be populated. There is no "unknown" or "null" option.

## TASK

1. Examine tables in the PDF. Select ONE table_preset using the decision rules below.
2. Examine running headers. Select ONE header_preset.
3. Examine running footers. Select ONE footer_preset.
4. Examine callout boxes. Select ONE callout_preset (even if no callouts visible — use default).
5. Extract font names, sizes, colors, and spacing from the PDF.
6. Return the complete StyleProfile JSON.

## PRESET SELECTION RULES

### Table Presets (FALLBACK DEFAULT: "data_grid")

Examine the most frequent table style in the sample. If the table has:
- Control ID + Description + Status columns → "control_matrix"
- Requirement ID + Text + Evidence columns → "requirements_matrix"
- Comparison columns (Feature/Option A/Option B) → "comparison_matrix"
- Date + Amount columns, financial data → "ledger" or "budget_table"
- Term + Definition two-column layout → "glossary"
- Acronym + Expansion two-column layout → "acronyms"
- Bordered grid with header row (generic) → "data_grid"
- No borders, whitespace separation → "gridless_report"
- RACI letters (R/A/C/I) in cells → "raci_matrix"
- Risk severity + Likelihood + Impact → "risk_register"
- Test case + Result + Pass/Fail → "test_results"
- No tables visible in sample → "data_grid" (fallback)

Valid values: {json.dumps(AVAILABLE_TABLE_PRESETS)}

### Header Presets (FALLBACK DEFAULT: "minimal_header")

Examine pages 2+ for running headers. If the running header has:
- "NIST" or "Special Publication" text → "nist_header"
- Document title left, page number right → "doc_title_header"
- "CONTROLLED", "CONFIDENTIAL", or classification markings → "classified_header"
- Version number visible (v1.0, Rev A, Revision B) → "versioned_header"
- Chapter or section name centered → "chapter_header"
- Only page number, no other content → "minimal_header"
- Two logos (left and right) → "dual_logo_header"
- No header visible or inconsistent across pages → "minimal_header" (fallback)

Valid values: {json.dumps(AVAILABLE_HEADER_PRESETS)}

### Footer Presets (FALLBACK DEFAULT: "page_number_footer")

Examine pages 2+ for running footers. If the running footer has:
- "Page X of Y" format → "page_number_footer"
- Classification marking centered → "classified_footer"
- Document number + Rev + Page → "revision_footer"
- Copyright notice (© or "Copyright") → "copyright_footer"
- Document control number → "doc_control_footer"
- "DRAFT" watermark or text → "draft_footer"
- Distribution statement → "distribution_footer"
- Only page number, nothing else → "minimal_footer"
- No footer visible → "page_number_footer" (fallback)

Valid values: {json.dumps(AVAILABLE_FOOTER_PRESETS)}

### Callout Presets (FALLBACK DEFAULT: "note_box")

Scan for boxed or highlighted content blocks. If the callout box has:
- Blue background, informational → "note_box"
- Yellow/orange background, caution → "warning_box"
- Red background, critical warning → "danger_box"
- Green background, helpful tip → "tip_box"
- Gray background, code/monospace → "code_block"
- Purple background, definition → "definition_box"
- Left border only, quoted text → "quote_block"
- No callout boxes visible → "note_box" (fallback — still populate this field)

IMPORTANT: Even if characteristics.has_callouts is false, callout_preset MUST be populated with "note_box".

Valid values: {json.dumps(AVAILABLE_CALLOUT_PRESETS)}

## FONT RULES (FALLBACK DEFAULT: "Helvetica")

Map observed fonts to ReportLab names:
- Arial, Calibri, Segoe UI, any sans-serif → "Helvetica"
- Arial Bold, Calibri Bold → "Helvetica-Bold"
- Times New Roman, Georgia, any serif → "Times-Roman"
- Times New Roman Bold → "Times-Bold"
- Courier New, Consolas, any monospace → "Courier"
- Cannot identify font → "Helvetica" (fallback)

For fonts.code, always use "Courier".

## COLOR RULES (FALLBACK DEFAULTS shown in schema)

Extract hex color codes from the PDF. If a color cannot be determined, use the default.

## REJECTION CRITERIA

Do NOT output:
- Preset names not in the valid lists above
- Font names not in the ReportLab list
- Color values not matching #RRGGBB format
- Negative spacing/margin values
- estimated_complexity values other than "simple", "moderate", "complex"
- Null, empty, or "unknown" values for any field

## OUTPUT SCHEMA (defaults shown for incomplete samples)

{{
  "table_preset": "string from table presets list (default: data_grid)",
  "header_preset": "string from header presets list (default: minimal_header)",
  "footer_preset": "string from footer presets list (default: page_number_footer)",
  "callout_preset": "string from callout presets list (default: note_box)",
  "fonts": {{
    "body": "ReportLab font name (default: Helvetica)",
    "heading": "ReportLab font name (default: Helvetica-Bold)",
    "code": "Courier",
    "body_size": 10.0,
    "heading_sizes": {{"1": 18, "2": 14, "3": 12, "4": 11}}
  }},
  "colors": {{
    "heading": "#000000",
    "body": "#333333",
    "table_header_bg": "#4472C4",
    "table_header_text": "#FFFFFF",
    "table_alt_row": "#D9E2F3",
    "callout_bg": "#EFF6FF",
    "callout_border": "#60A5FA",
    "link": "#0563C1"
  }},
  "spacing": {{
    "paragraph_after": 12.0,
    "heading_before": 18.0,
    "heading_after": 10.0,
    "list_indent": 24.0,
    "table_padding": 6.0
  }},
  "page": {{
    "margins": [72, 72, 72, 72],
    "header_height": 36.0,
    "footer_height": 24.0,
    "column_count": 1
  }},
  "characteristics": {{
    "has_toc": false,
    "has_numbered_headings": false,
    "has_requirements": false,
    "has_tables": true,
    "has_figures": false,
    "has_callouts": false,
    "estimated_complexity": "moderate"
  }}
}}

Output NOTHING but the raw JSON object. No commentary, no markdown fencing."""

    user_prompt = """Analyze the attached PDF pages and extract the visual style profile.

These pages are representative samples showing:
- Table formatting (borders, headers, row striping)
- Running headers and footers
- Heading hierarchy (H1, H2, H3 sizes and fonts)
- Body text font and size
- Any callout boxes or special elements

For each field:
1. If you can see it in the PDF → extract the actual value
2. If you can infer it from context → use your inference
3. If neither → use the fallback default from the schema

Every field must be populated. Return ONLY the JSON object."""

    return system_prompt, user_prompt


async def extract_style_with_opus(
    mini_pdf_path: str,
    scillm_url: str = "http://localhost:4001",
    model: str = "opus",
) -> StyleProfile:
    """Send mini-PDF to Opus via scillm for style extraction.

    Uses scillm's native PDF reading capability (base64-encoded PDF).
    """
    system_prompt, user_prompt = _build_extraction_prompt()

    # Read PDF as base64
    with open(mini_pdf_path, "rb") as f:
        pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")

    # Build message with PDF attachment
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
            ],
        },
    ]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{scillm_url}/v1/chat/completions",
            headers={
                "Authorization": "Bearer sk-dev-proxy-123",
                "X-Caller-Skill": "pdf-oxide-style-extractor",
            },
            json={
                "model": model,
                "messages": messages,
                "response_format": {"type": "json_object"},
                "max_tokens": 2000,
            },
            timeout=180.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

        # Parse and validate
        data = json.loads(content)

        # Validate presets exist
        if data.get("table_preset") not in AVAILABLE_TABLE_PRESETS:
            data["table_preset"] = "data_grid"
        if data.get("header_preset") not in AVAILABLE_HEADER_PRESETS:
            data["header_preset"] = "doc_title_header"
        if data.get("footer_preset") not in AVAILABLE_FOOTER_PRESETS:
            data["footer_preset"] = "page_number_footer"
        if data.get("callout_preset") not in AVAILABLE_CALLOUT_PRESETS:
            data["callout_preset"] = "note_box"

        return StyleProfile.from_dict(data)


async def extract_style_profile(
    source_pdf: str,
    profiler_output: Dict[str, Any],
    scillm_url: str = "http://localhost:4001",
    model: str = "opus",
    keep_mini_pdf: bool = False,
) -> StyleProfile:
    """Full pipeline: select pages → extract mini-PDF → analyze with Opus.

    Args:
        source_pdf: Path to source PDF
        profiler_output: Output from clone_profiler.profile_for_cloning()
        scillm_url: scillm proxy URL
        model: Model to use (opus recommended for PDF analysis)
        keep_mini_pdf: If True, don't delete the mini-PDF after analysis

    Returns:
        StyleProfile with preset selections and style parameters
    """
    # 1. Select representative pages
    pages = select_representative_pages(source_pdf, profiler_output)
    print(f"  Selected {len(pages)} representative pages: {pages}")

    # 2. Extract mini-PDF
    mini_pdf = extract_pages_as_mini_pdf(source_pdf, pages)
    print(f"  Created mini-PDF: {mini_pdf}")

    try:
        # 3. Send to Opus for analysis
        print(f"  Analyzing with {model}...")
        profile = await extract_style_with_opus(mini_pdf, scillm_url, model)
        print(f"  Extracted style profile:")
        print(f"    - table_preset: {profile.table_preset}")
        print(f"    - header_preset: {profile.header_preset}")
        print(f"    - footer_preset: {profile.footer_preset}")
        print(f"    - complexity: {profile.estimated_complexity}")

        return profile

    finally:
        if not keep_mini_pdf:
            Path(mini_pdf).unlink(missing_ok=True)


def get_default_style_profile() -> StyleProfile:
    """Return default style profile for fallback scenarios."""
    return StyleProfile()


# CLI for testing
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python style_extractor.py <source.pdf> [profiler_output.json]")
        sys.exit(1)

    source_pdf = sys.argv[1]

    # Load profiler output or use empty
    if len(sys.argv) > 2:
        with open(sys.argv[2]) as f:
            profiler_output = json.load(f)
    else:
        # Run profiler
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
        from pdf_oxide.clone_profiler import profile_for_cloning
        profiler_output = profile_for_cloning(source_pdf)

    async def main():
        profile = await extract_style_profile(
            source_pdf,
            profiler_output,
            keep_mini_pdf=True,
        )
        print("\n=== Style Profile ===")
        print(json.dumps(profile.to_dict(), indent=2))

    asyncio.run(main())
