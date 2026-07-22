import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_with_ledger(page_index: int):
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, page_index, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_104_au12_page_break_control_label_stays_field_label():
    page104 = _extract_page_with_ledger(103)
    blocks104 = page104.get("blocks") or []

    control_labels = [
        block
        for block in blocks104
        if " ".join(str(block.get("text") or "").split()) == "Control:"
    ]
    assert len(control_labels) == 1

    label = control_labels[0]
    assert label.get("type") == "paragraph_block"
    assert label.get("semantic_role") == "nist_field_label"
    assert label.get("bbox") == [
        0.20588235294117646,
        0.8697468150745739,
        0.25978078405841504,
        0.886674514924637,
    ]

    section_headings = {
        " ".join(str(block.get("text") or "").split())
        for block in blocks104
        if block.get("type") == "section_heading"
    }
    assert "AU-12 AUDIT RECORD GENERATION" in section_headings

    page105 = _extract_page_with_ledger(104)
    blocks105 = page105.get("blocks") or []
    au12_control_items = [
        block
        for block in blocks105
        if " ".join(str(block.get("text") or "").split()).startswith(
            (
                "a. Provide audit record generation capability",
                "b. Allow [Assignment: organization-defined personnel",
                "c. Generate audit records for the event types defined",
            )
        )
    ]

    assert len(au12_control_items) == 3
    assert all(block.get("type") == "list" for block in au12_control_items)
