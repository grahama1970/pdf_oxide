import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"
EXPECTED_TOC_PATH = ["toc:0038"]
EXPECTED_BREADCRUMB = ["APPENDIX C CONTROL SUMMARIES"]


def _extract_page_455_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 454, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def _normalized(text):
    return " ".join(str(text or "").split())


def test_nist_page_455_control_summaries_blocks_keep_toc_lineage():
    page = _extract_page_455_with_ledger()
    blocks = page.get("blocks") or []

    expected_blocks = [
        ("APPENDIX C", "unknown_region"),
        ("CONTROL SUMMARIES", "section_heading"),
        ("IMPLEMENTATION, WITHDRAWAL, AND ASSURANCE DESIGNATIONS", "section_heading"),
    ]
    for expected_text, expected_type in expected_blocks:
        matches = [block for block in blocks if _normalized(block.get("text")) == expected_text]
        assert len(matches) == 1
        block = matches[0]
        assert block.get("type") == expected_type
        assert block.get("toc_path") == EXPECTED_TOC_PATH
        assert block.get("breadcrumb") == EXPECTED_BREADCRUMB

    body_blocks = [
        block
        for block in blocks
        if block.get("type") in {"paragraph_block", "list", "footnote"}
    ]
    assert body_blocks
    missing_lineage = [
        {
            "id": block.get("id"),
            "type": block.get("type"),
            "text": _normalized(block.get("text")),
            "toc_path": block.get("toc_path"),
            "breadcrumb": block.get("breadcrumb"),
        }
        for block in body_blocks
        if block.get("toc_path") != EXPECTED_TOC_PATH
        or block.get("breadcrumb") != EXPECTED_BREADCRUMB
    ]
    assert missing_lineage == []
