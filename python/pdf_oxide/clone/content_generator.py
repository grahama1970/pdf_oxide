"""Generate plausibly similar content via scillm.

Given extracted table structure and content, generate new data that:
1. Maintains the same schema (headers, column types)
2. Has plausibly similar content (not copied)
3. Can be used as benchmark ground truth

Uses /scillm endpoint for LLM generation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from pdf_oxide.clone.table_extractor import ExtractedTable


SCILLM_URL = "http://localhost:4001/v1/chat/completions"
SCILLM_KEY = "sk-dev-proxy-123"


@dataclass
class GeneratedTable:
    """Table with generated content."""
    page: int
    rows: int
    cols: int
    bbox: tuple
    ruled: bool
    headers: List[str]
    data: List[List[str]]
    source_summary: str  # Description of what the original looked like

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page": self.page,
            "rows": self.rows,
            "cols": self.cols,
            "bbox": list(self.bbox),
            "ruled": self.ruled,
            "headers": self.headers,
            "data": self.data,
            "source_summary": self.source_summary,
        }


def _infer_column_types(headers: List[str], data: List[List[str]]) -> List[str]:
    """Infer column types from headers and sample data."""
    types = []
    for col_idx, header in enumerate(headers):
        header_lower = header.lower()

        # Check header keywords
        if any(kw in header_lower for kw in ["id", "number", "no.", "num", "#"]):
            types.append("identifier")
        elif any(kw in header_lower for kw in ["date", "time", "year"]):
            types.append("date")
        elif any(kw in header_lower for kw in ["status", "state", "result"]):
            types.append("status")
        elif any(kw in header_lower for kw in ["description", "text", "notes", "comment"]):
            types.append("text")
        elif any(kw in header_lower for kw in ["%", "percent", "rate", "score"]):
            types.append("percentage")
        elif any(kw in header_lower for kw in ["amount", "cost", "price", "value"]):
            types.append("numeric")
        elif any(kw in header_lower for kw in ["name", "title", "label"]):
            types.append("name")
        else:
            # Infer from data
            if data and col_idx < len(data[0]):
                sample = data[0][col_idx]
                if sample.replace(".", "").replace("-", "").isdigit():
                    types.append("numeric")
                elif len(sample) > 50:
                    types.append("text")
                else:
                    types.append("string")
            else:
                types.append("string")

    return types


def _build_generation_prompt(
    extracted: ExtractedTable,
    toc_context: Optional[str] = None,
    num_rows: Optional[int] = None,
) -> str:
    """Build prompt for generating similar table content."""
    col_types = _infer_column_types(extracted.headers, extracted.data)

    # Sample data for context (first 3 rows)
    sample_rows = extracted.data[:3] if extracted.data else []

    prompt = f"""Generate plausibly similar table data for a technical document.

ORIGINAL TABLE STRUCTURE:
- Headers: {extracted.headers}
- Column types: {col_types}
- Rows in original: {len(extracted.data)}
- Sample data (first 3 rows):
{json.dumps(sample_rows, indent=2)}

REQUIREMENTS:
- Generate {num_rows or len(extracted.data)} rows of new data
- Match the schema exactly (same number of columns)
- Content should be plausibly similar but NOT copied
- Maintain realistic patterns (IDs should be sequential, dates reasonable, etc.)
- For requirement IDs: use format like REQ-001, REQ-002 or 3.1.1, 3.1.2
- For status columns: use values like "Compliant", "Partial", "Non-Compliant", "N/A"
- For text descriptions: write technical but concise descriptions

"""

    if toc_context:
        prompt += f"""
DOCUMENT CONTEXT (from TOC):
{toc_context}

The table content should be relevant to this section.
"""

    prompt += """
OUTPUT FORMAT:
Return ONLY a JSON array of arrays (row-major), no explanation.
Example: [["REQ-001", "Description here", "Compliant"], ["REQ-002", "Another desc", "Partial"]]
"""

    return prompt


async def generate_similar_table(
    extracted: ExtractedTable,
    toc_context: Optional[str] = None,
    num_rows: Optional[int] = None,
    model: str = "text",
    timeout: float = 60.0,
    max_retries: int = 3,
    client: Optional[httpx.AsyncClient] = None,
) -> GeneratedTable:
    """Generate plausibly similar table content via scillm.

    Args:
        extracted: Original extracted table
        toc_context: TOC section title for context
        num_rows: Number of rows to generate (default: same as original)
        model: scillm model alias
        timeout: Request timeout
        max_retries: Max retries on rate limit
        client: Optional shared httpx client for connection pooling

    Returns:
        GeneratedTable with new content
    """
    import asyncio
    import random

    prompt = _build_generation_prompt(extracted, toc_context, num_rows)

    # Use provided client or create one-off
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))

    last_error = None
    resp = None
    try:
        for attempt in range(max_retries):
            try:
                resp = await client.post(
                    SCILLM_URL,
                    headers={"Authorization": f"Bearer {SCILLM_KEY}"},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "response_format": {"type": "json_object"},
                        "max_tokens": 4000,
                    },
                    timeout=timeout,
                )
                if resp.status_code == 429:
                    # Rate limited - exponential backoff with jitter
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(wait)
                    last_error = e
                    continue
                raise
        else:
            # All retries exhausted - return empty table
            return GeneratedTable(
                page=extracted.page,
                rows=0,
                cols=extracted.cols,
                bbox=extracted.bbox,
                ruled=extracted.ruled,
                headers=extracted.headers,
                data=[],
                source_summary=f"Generation failed after {max_retries} retries: {last_error}",
            )

        # Parse response
        content = resp.json()["choices"][0]["message"]["content"]
        data = []

        try:
            # Handle both {"data": [...]} and direct [...] formats
            parsed = json.loads(content)
            if isinstance(parsed, dict) and "data" in parsed:
                data = parsed["data"]
            elif isinstance(parsed, list):
                data = parsed
            else:
                data = []
        except json.JSONDecodeError:
            # Fallback: extract JSON array from text
            import re
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    data = []

        # Build summary of original for reference
        source_summary = (
            f"Generated from {extracted.rows}x{extracted.cols} table on page {extracted.page}. "
            f"Headers: {extracted.headers}"
        )

        return GeneratedTable(
            page=extracted.page,
            rows=len(data),
            cols=extracted.cols,
            bbox=extracted.bbox,
            ruled=extracted.ruled,
            headers=extracted.headers,
            data=data,
            source_summary=source_summary,
        )
    finally:
        if owns_client:
            await client.aclose()


def generate_similar_table_sync(
    extracted: ExtractedTable,
    toc_context: Optional[str] = None,
    num_rows: Optional[int] = None,
    model: str = "text",
    timeout: float = 60.0,
) -> GeneratedTable:
    """Synchronous wrapper for generate_similar_table."""
    import asyncio
    return asyncio.run(generate_similar_table(
        extracted, toc_context, num_rows, model, timeout
    ))


async def generate_all_tables(
    extracted_tables: List[ExtractedTable],
    toc_sections: Optional[List[Dict[str, Any]]] = None,
    model: str = "text",
    chunk_size: int = 4,
    timeout: float = 120.0,
) -> List[GeneratedTable]:
    """Generate similar content for all extracted tables.

    Uses chunked processing per scillm best practices:
    - Process chunk_size tables concurrently
    - Wait for chunk to complete before starting next
    - Reuse httpx client for connection pooling
    - Avoids queue timeout on large batches

    Args:
        extracted_tables: List of extracted tables
        toc_sections: TOC sections from profiler (for context)
        model: scillm model alias
        chunk_size: Tables to process concurrently (default 4)
        timeout: Timeout per request (default 120s)

    Returns:
        List of GeneratedTable with new content
    """
    import asyncio

    # Build page-to-section mapping for context
    page_to_section: Dict[int, str] = {}
    if toc_sections:
        for section in toc_sections:
            page = section.get("page")
            if page is not None:
                page_to_section[page] = section.get("title", "")

    async def generate_one(client: httpx.AsyncClient, table: ExtractedTable) -> GeneratedTable:
        context = page_to_section.get(table.page)
        return await generate_similar_table(
            table, context, model=model, timeout=timeout, client=client
        )

    # Chunked processing with shared client (scillm best practice)
    all_results: List[GeneratedTable] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        for i in range(0, len(extracted_tables), chunk_size):
            chunk = extracted_tables[i:i + chunk_size]
            chunk_results = await asyncio.gather(
                *[generate_one(client, t) for t in chunk],
                return_exceptions=True
            )
            # Handle exceptions - return empty tables for failed ones
            for j, result in enumerate(chunk_results):
                if isinstance(result, Exception):
                    t = chunk[j]
                    all_results.append(GeneratedTable(
                        page=t.page, rows=0, cols=t.cols, bbox=t.bbox,
                        ruled=t.ruled, headers=t.headers, data=[],
                        source_summary=f"Error: {result}"
                    ))
                else:
                    all_results.append(result)

    return all_results


# CLI for testing
if __name__ == "__main__":
    import argparse
    import asyncio

    from pdf_oxide.clone_profiler import profile_for_cloning
    from pdf_oxide.clone.table_extractor import extract_all_tables

    parser = argparse.ArgumentParser(description="Generate similar table content")
    parser.add_argument("pdf", help="PDF file path")
    parser.add_argument("--model", "-m", default="text", help="scillm model")
    parser.add_argument("--json", "-j", action="store_true", help="Output JSON")

    args = parser.parse_args()

    # Profile and extract
    print(f"Profiling {args.pdf}...")
    profile = profile_for_cloning(args.pdf)
    table_shapes = profile.get("table_shapes", [])

    if not table_shapes:
        print("No tables found in PDF")
        exit(0)

    print(f"Extracting {len(table_shapes)} tables...")
    extracted = extract_all_tables(args.pdf, table_shapes)

    # Generate similar content
    print(f"Generating similar content for {len(extracted)} tables...")

    async def run():
        return await generate_all_tables(
            extracted,
            profile.get("toc_sections"),
            model=args.model,
        )

    generated = asyncio.run(run())

    if args.json:
        print(json.dumps([g.to_dict() for g in generated], indent=2))
    else:
        for g in generated:
            print(f"\n=== Page {g.page}, {g.rows}x{g.cols} ===")
            print(f"Headers: {g.headers}")
            print(f"Generated {len(g.data)} rows")
            if g.data:
                for row in g.data[:3]:
                    print(f"  {row}")
                if len(g.data) > 3:
                    print(f"  ... ({len(g.data) - 3} more rows)")
