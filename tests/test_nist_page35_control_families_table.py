import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_35_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 34, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_35_control_families_table_caption_and_footnote_are_separate():
    page = _extract_page_35_with_ledger()
    blocks = page.get("blocks") or []

    tables = [block for block in blocks if block.get("type") == "table"]
    assert len(tables) == 1

    table_text = " ".join(str(tables[0].get("text") or "").split())
    for expected in [
        "ID | FAMILY | ID | FAMILY",
        "AC | Access Control | PE | Physical and Environmental Protection",
        "AU | Audit and Accountability | PM | Program Management",
        "IA | Identification and Authentication | SA | System and Services Acquisition",
        "MP | Media Protection | SR | Supply Chain Risk Management",
    ]:
        assert expected in table_text

    captions = [
        block
        for block in blocks
        if block.get("type") == "caption"
        and "TABLE 1: SECURITY AND PRIVACY CONTROL FAMILIES" in " ".join(str(block.get("text") or "").split())
    ]
    assert len(captions) == 1
    assert captions[0].get("semantic_role") == "table_caption"

    footnotes = [
        block
        for block in blocks
        if block.get("type") == "footnote"
        and "Of the 20 control families in NIST SP 800-53" in " ".join(str(block.get("text") or "").split())
    ]
    assert len(footnotes) == 1
    assert footnotes[0].get("semantic_role") == "footnote_group"

    leaked_table_cells = []
    table_cell_texts = {
        "AC",
        "Access Control",
        "PE",
        "Physical and Environmental Protection",
        "AU",
        "Audit and Accountability",
        "PM",
        "Program Management",
        "SR",
        "Supply Chain Risk Management",
    }
    for block in blocks:
        if block.get("type") in {"table", "caption"}:
            continue
        text = " ".join(str(block.get("text") or "").split())
        if text in table_cell_texts:
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
