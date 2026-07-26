import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_468_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 467, LEDGER, "release"), snapshot
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_468_table_cells_do_not_leak_as_standalone_blocks():
    page, snapshot = _extract_page_468_with_ledger()
    blocks = page.get("blocks") or []

    tables = [block for block in blocks if block.get("type") == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert table.get("bbox") == pytest.approx(
        [0.1466667, 0.1137121, 0.8525490, 0.9028788],
        abs=0.004,
    )

    table_text = " ".join(str(table.get("text") or "").split())
    for expected in [
        "CONTROL NUMBER",
        "CONTROL NAME CONTROL ENHANCEMENT NAME",
        "IMPLEMENTED BY",
        "ASSURANCE",
        "IA-1",
        "Policy and Procedures",
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


def test_nist_page_468_table_header_cells_preserve_geometry_and_source_ids():
    page, _snapshot = _extract_page_468_with_ledger()
    table = next(block for block in page.get("blocks") or [] if block.get("type") == "table")
    rows = ((table.get("raw") or {}).get("rows") or [])
    assert rows
    assert (table.get("raw") or {}).get("table_contained_fragment_ids")

    header = rows[0]
    assert header.get("role") == "header_row"

    expected = [
        (
            "CONTROL NUMBER",
            [0.1466667, 0.1137121, 0.2338235, 0.1871212],
            ["actual:p468:block:5", "actual:p468:block:6"],
        ),
        (
            "CONTROL NAME CONTROL ENHANCEMENT NAME",
            [0.2338235, 0.1137121, 0.6053922, 0.1871212],
            ["actual:p468:block:7", "actual:p468:block:8"],
        ),
        (
            "IMPLEMENTED BY",
            [0.6053922, 0.1137121, 0.7299020, 0.1871212],
            ["actual:p468:block:9", "actual:p468:block:10"],
        ),
        (
            "ASSURANCE",
            [0.7299020, 0.1137121, 0.8525490, 0.1871212],
            ["actual:p468:block:11"],
        ),
    ]

    expected_row_source_ids = []
    for cell, (text, bbox, source_ids) in zip(header["cells"], expected):
        assert cell.get("role") == "column_header"
        assert cell.get("text") == text
        assert cell.get("bbox_source") == "pdf_drawing_grid"
        assert cell.get("bbox") == pytest.approx(bbox, abs=0.004)
        assert cell.get("source_ids") == source_ids
        expected_row_source_ids.extend(source_ids)
    assert header.get("source_ids") == expected_row_source_ids
