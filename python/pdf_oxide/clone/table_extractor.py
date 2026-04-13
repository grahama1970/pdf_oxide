"""Extract table content from PDFs using pure pdf_oxide.

This module now uses table_extractor_oxide.py which is 60x faster than Camelot.
The old Camelot-based implementation has been replaced.

Workflow:
1. pdf_oxide survey identifies table locations (bbox, rows, cols, col_positions)
2. pdf_oxide extract_words() gets positioned text
3. Words are clustered into rows and assigned to cells
4. Returns DataFrames alongside table_shapes for the profiler
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# Re-export everything from the oxide-based implementation
from pdf_oxide.clone.table_extractor_oxide import (
    ExtractedTable,
    extract_table_from_shape,
    extract_tables_from_page,
    extract_all_tables,
)

# Re-export for backwards compatibility
__all__ = [
    "ExtractedTable",
    "extract_table_from_shape",
    "extract_tables_from_page",
    "extract_all_tables",
    "enrich_profile_with_table_content",
]


def enrich_profile_with_table_content(
    profile: Dict[str, Any],
    pdf_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Add extracted table content to profiler output.

    Args:
        profile: Output from clone_profiler.profile_for_cloning()
        pdf_path: PDF path (uses profile["path"] if not provided)

    Returns:
        Profile with added "table_content" field
    """
    pdf_path = pdf_path or profile.get("path")
    if not pdf_path:
        return profile

    table_shapes = profile.get("table_shapes", [])
    if not table_shapes:
        profile["table_content"] = []
        return profile

    extracted = extract_all_tables(pdf_path, table_shapes)
    profile["table_content"] = [t.to_dict() for t in extracted]

    return profile


# CLI for testing
if __name__ == "__main__":
    import argparse

    from pdf_oxide.clone_profiler import profile_for_cloning

    parser = argparse.ArgumentParser(description="Extract tables from PDF")
    parser.add_argument("pdf", help="PDF file path")
    parser.add_argument("--page", "-p", type=int, help="Specific page (0-indexed)")
    parser.add_argument("--json", "-j", action="store_true", help="Output JSON")

    args = parser.parse_args()

    # Get table shapes from profiler
    profile = profile_for_cloning(args.pdf)
    table_shapes = profile.get("table_shapes", [])

    if args.page is not None:
        import pdf_oxide
        doc = pdf_oxide.PdfDocument(args.pdf)
        tables = extract_tables_from_page(doc, args.page, table_shapes)
    else:
        tables = extract_all_tables(args.pdf, table_shapes)

    if args.json:
        print(json.dumps([t.to_dict() for t in tables], indent=2))
    else:
        for t in tables:
            print(f"\n=== Page {t.page}, {t.rows}x{t.cols} ===")
            print(f"Headers: {t.headers}")
            print(f"Data rows: {len(t.data)}")
            if t.data:
                df = t.to_dataframe()
                print(df.to_string())
