import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_27_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 26, LEDGER, "release"), snapshot
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_27_revision_table_cells_do_not_leak_as_standalone_blocks():
    page, snapshot = _extract_page_27_with_ledger()
    blocks = page.get("blocks") or []

    tables = [block for block in blocks if block.get("type") == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert table.get("bbox") == pytest.approx(
        [0.1466667, 0.0915151, 0.8525490, 0.7434091],
        abs=0.004,
    )

    table_text = " ".join(str(table.get("text") or "").split())
    for expected in [
        "DATE",
        "TYPE",
        "REVISION",
        "PAGE",
        "12-10-2020",
        "Appendix B Acronyms",
    ]:
        assert expected in table_text

    leaks = []
    for block in blocks:
        if block.get("type") == "table":
            continue
        bbox = block.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        if snapshot._bbox_coverage(bbox, table["bbox"]) >= 0.90:
            leaks.append(
                {
                    "id": block.get("id"),
                    "type": block.get("type"),
                    "source_type": block.get("source_type"),
                    "text": " ".join(str(block.get("text") or "").split()),
                    "bbox": bbox,
                }
            )

    assert leaks == []


def test_nist_page_27_revision_table_preserves_rows_and_fragment_lineage():
    page, _snapshot = _extract_page_27_with_ledger()
    table = next(block for block in page.get("blocks") or [] if block.get("type") == "table")
    raw = table.get("raw") or {}
    rows = raw.get("rows") or []

    assert raw.get("row_count") == 33
    assert raw.get("column_count") == 4
    assert rows[0] == {
        "cells": [
            {"text": "DATE"},
            {"text": "TYPE"},
            {"text": "REVISION"},
            {"text": "PAGE"},
        ]
    }
    assert len(raw.get("table_contained_fragment_ids") or []) == 40
