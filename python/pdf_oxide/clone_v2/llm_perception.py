"""Stage 1: LLM vision perception — PDF page → PageBlueprint JSON.

Sends each page as a base64 PDF attachment to Claude via /scillm.
The LLM describes the structural elements on the page as JSON conforming
to the PageBlueprint schema. No code generation — perception only.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile

import httpx
from loguru import logger
from pypdf import PdfReader, PdfWriter

from pdf_oxide.clone_v2.schemas import PageBlueprint, PageSize


SCILLM_URL = os.environ.get("SCILLM_URL", "http://localhost:4001")
SCILLM_KEY = os.environ.get("SCILLM_PROXY_KEY", "sk-dev-proxy-123")
_HEADERS = {"Authorization": f"Bearer {SCILLM_KEY}", "Content-Type": "application/json"}

_SYSTEM_PROMPT = """\
You are a PDF page structural annotator. Your job is to identify every
structural element on a PDF page and return a JSON object conforming to
the schema below. You must NOT output code, prose, or markdown — JSON only.

## Output schema

```json
{
  "document_id": "string",
  "page_number": int,
  "page_size": {"width_pt": 612, "height_pt": 792},
  "rotation": 0,
  "elements": [
    {
      "eid": "p{page}_e{n}",
      "type": "header|footer|heading|paragraph|table|figure|caption|list|requirement|equation|footnote",
      "bbox": {"x": float_0_to_1, "y": float_0_to_1, "w": float_0_to_1, "h": float_0_to_1},
      "reading_order": int,
      "z_index": 0,
      "style": {
        "font_family": "Times New Roman|Arial|Unknown",
        "font_size_pt": float,
        "bold": bool,
        "italic": bool,
        "align": "left|center|right|justify",
        "line_spacing": 1.2
      },
      "semantic": {
        "heading_level": int_or_null,
        "list_kind": "bullet|numbered|null",
        "requirement_id": "3.1.1|null",
        "caption_for": "eid_of_figure_or_table|null"
      },
      "table": {
        "n_rows": int,
        "n_cols": int,
        "col_widths_pt": [float, ...],
        "row_heights_pt": [float, ...],
        "cells": [
          {
            "r": int, "c": int,
            "rowspan": 1, "colspan": 1,
            "bbox": {"x": float_0_to_1, "y": float_0_to_1, "w": float_0_to_1, "h": float_0_to_1},
            "is_header": bool,
            "role": "header|body|stub|footnote"
          }
        ],
        "borders": {"outer": true, "inner_v": true, "inner_h": true}
      },
      "figure": {
        "kind": "diagram|chart|photo|logo|unknown",
        "has_border": bool
      },
      "confidence": float_0_to_1
    }
  ]
}
```

## Rules

1. All bbox coordinates are NORMALIZED [0, 1] relative to page size. Origin is bottom-left.
   x=0 is left edge, x=1 is right edge, y=0 is bottom, y=1 is top.
2. Every visible structural element must be listed.
3. Tables must include cell-level bboxes with row/col indices and spans.
4. Figures are separate from their captions. Link via semantic.caption_for.
5. Headings must include heading_level (1=chapter, 2=section, 3=subsection).
6. Requirement clauses (e.g., "3.1.1 Limit information system access...") are type "requirement" with requirement_id.
7. Headers and footers are type "header"/"footer" — running text at page margins.
8. Do NOT invent decorative details. Report what you see.
9. Output ONLY the JSON object. No explanation, no markdown fencing."""


def _extract_single_page_pdf(pdf_path: str, page_index: int) -> bytes:
    """Extract a single page from a PDF as bytes."""
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    writer.add_page(reader.pages[page_index])
    with tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024) as buf:
        writer.write(buf)
        buf.seek(0)
        return buf.read()


def _get_page_size(pdf_path: str, page_index: int) -> PageSize:
    """Get page dimensions in points via pypdf."""
    reader = PdfReader(pdf_path)
    page = reader.pages[page_index]
    box = page.mediabox
    return PageSize(
        width_pt=float(box.width),
        height_pt=float(box.height),
    )


def analyze_page(
    pdf_path: str,
    page_index: int,
    document_id: str,
    model: str = "claude-sonnet-4-6",
    timeout: int = 120,
) -> dict:
    """Send a single PDF page to the LLM for structural annotation.

    Returns the raw JSON dict from the LLM response.
    Raises on HTTP errors or invalid JSON.
    """
    page_pdf_bytes = _extract_single_page_pdf(pdf_path, page_index)
    pdf_b64 = base64.b64encode(page_pdf_bytes).decode()
    page_size = _get_page_size(pdf_path, page_index)

    user_text = (
        f"Annotate this PDF page. It is page {page_index + 1} of document '{document_id}'. "
        f"Page size: {page_size.width_pt:.0f} x {page_size.height_pt:.0f} points. "
        f"Return the PageBlueprint JSON with all structural elements. JSON only."
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {
                "url": f"data:application/pdf;base64,{pdf_b64}",
            }},
        ]},
    ]

    logger.info(f"Perception: page {page_index} of {document_id} via {model}")

    resp = httpx.post(
        f"{SCILLM_URL}/v1/chat/completions",
        json={"model": model, "max_tokens": 16384, "messages": messages},
        headers=_HEADERS,
        timeout=timeout,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"scillm returned {resp.status_code}: {resp.text[:500]}")

    content = resp.json()["choices"][0]["message"]["content"]

    # Strip markdown fencing if present
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        content = content.split("```")[1].split("```")[0].strip()

    try:
        raw = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM returned invalid JSON for page {page_index}: {e}\n{content[:500]}") from e

    # Inject document_id and page_number if missing
    raw.setdefault("document_id", document_id)
    raw.setdefault("page_number", page_index + 1)
    raw.setdefault("page_size", {"width_pt": page_size.width_pt, "height_pt": page_size.height_pt})

    return raw


def analyze_page_to_blueprint(
    pdf_path: str,
    page_index: int,
    document_id: str,
    model: str = "claude-sonnet-4-6",
    timeout: int = 120,
) -> PageBlueprint:
    """Full pipeline: PDF page → LLM → normalized PageBlueprint."""
    from pdf_oxide.clone_v2.blueprint_normalizer import normalize_blueprint

    raw = analyze_page(pdf_path, page_index, document_id, model=model, timeout=timeout)
    return normalize_blueprint(raw)
