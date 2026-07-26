import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_456_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 455, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_456_control_table_headers_do_not_leak_as_standalone_blocks():
    page = _extract_page_456_with_ledger()
    blocks = page.get("blocks") or []

    tables = [block for block in blocks if block.get("type") == "table"]
    assert len(tables) == 1

    table_text = " ".join(str(tables[0].get("text") or "").split())
    for expected in [
        "CONTROL NUMBER",
        "CONTROL NAME",
        "IMPLEMENTED BY",
        "ASSURANCE",
        "AC-1",
        "Policy and Procedures",
    ]:
        assert expected in table_text

    leaked_table_cells = []
    for block in blocks:
        if block.get("type") == "table":
            continue
        text = " ".join(str(block.get("text") or "").split())
        if text in {
            "CONTROL",
            "NUMBER",
            "CONTROL NAME",
            "CONTROL ENHANCEMENT NAME",
            "IMPLEMENTED",
            "BY",
            "ASSURANCE",
            "O",
            "S",
        }:
            leaked_table_cells.append(
                {
                    "id": block.get("id"),
                    "type": block.get("type"),
                    "source_type": block.get("source_type"),
                    "text": text,
                    "bbox": block.get("bbox"),
                }
            )

    assert leaked_table_cells == []


def test_nist_page_456_control_table_headers_have_cell_bboxes_for_overlay():
    page = _extract_page_456_with_ledger()
    table = next(block for block in page.get("blocks") or [] if block.get("type") == "table")
    rows = ((table.get("raw") or {}).get("rows") or [])
    assert rows, "table raw rows are required for PDF Lab table-cell overlay"

    header = rows[0]
    assert header.get("role") == "header_row"
    assert len(header.get("cells") or []) == 4

    expected = [
        (
            "CONTROL NUMBER",
            [0.1466667, 0.1137121, 0.2338235, 0.1871212],
            ["actual:p456:block:5", "actual:p456:block:6"],
        ),
        (
            "CONTROL NAME CONTROL ENHANCEMENT NAME",
            [0.2338235, 0.1137121, 0.6053922, 0.1871212],
            ["actual:p456:block:7", "actual:p456:block:8"],
        ),
        (
            "IMPLEMENTED BY",
            [0.6053922, 0.1137121, 0.7299020, 0.1871212],
            ["actual:p456:block:9", "actual:p456:block:10"],
        ),
        (
            "ASSURANCE",
            [0.7299020, 0.1137121, 0.8525490, 0.1871212],
            ["actual:p456:block:11"],
        ),
    ]

    expected_row_source_ids = []
    for cell, (text, bbox, source_ids) in zip(header["cells"], expected):
        assert cell.get("role") == "column_header"
        assert cell.get("text") == text
        assert cell.get("bbox") is not None
        assert cell.get("bbox_source") == "pdf_drawing_grid"
        assert cell["bbox"] == pytest.approx(bbox, abs=0.004)
        assert cell.get("source_ids") == source_ids
        expected_row_source_ids.extend(source_ids)
    assert header.get("source_ids") == expected_row_source_ids
