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
        "CONTROL ENHANCEMENT NAME",
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
