import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_28_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 27, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def _normalized(text):
    return " ".join(str(text or "").split())


def test_nist_page_28_numbered_bottom_notes_are_footnotes_not_section_headers():
    page = _extract_page_28_with_ledger()
    blocks = page.get("blocks") or []

    expected_footnotes = [
        "4 [OMB A-130] defines security and privacy controls.",
        "6 Organizational operations include mission, functions, image, and reputation.",
    ]
    for expected in expected_footnotes:
        matches = [
            block
            for block in blocks
            if _normalized(block.get("text")).startswith(expected)
        ]
        assert len(matches) == 1
        assert matches[0].get("type") == "footnote"
        assert matches[0].get("source_type") == "Footnote"

    leaked_notes = [
        {
            "id": block.get("id"),
            "type": block.get("type"),
            "source_type": block.get("source_type"),
            "text": _normalized(block.get("text")),
            "bbox": block.get("bbox"),
        }
        for block in blocks
        if block.get("type") in {"section_heading", "paragraph_block"}
        and any(_normalized(block.get("text")).startswith(expected) for expected in expected_footnotes)
    ]
    assert leaked_notes == []
