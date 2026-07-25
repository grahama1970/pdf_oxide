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


def _normalized(text):
    return " ".join(str(text or "").split())


def test_nist_page_456_running_header_and_footer_are_page_chrome_noise():
    page = _extract_page_456_with_ledger()
    blocks = page.get("blocks") or []

    top_headers = [
        block
        for block in blocks
        if "NIST SP 800-53" in _normalized(block.get("text"))
        and "SECURITY AND PRIVACY CONTROLS" in _normalized(block.get("text"))
    ]
    assert len(top_headers) == 1
    assert top_headers[0].get("type") == "header_footer_noise"
    assert top_headers[0].get("source_type") == "Header"
    assert top_headers[0].get("semantic_role") == "page_chrome"

    footer_blocks = [
        block
        for block in blocks
        if "APPENDIX C" in _normalized(block.get("text")) and "PAGE 429" in _normalized(block.get("text"))
    ]
    assert len(footer_blocks) == 1
    assert footer_blocks[0].get("type") == "header_footer_noise"
    assert footer_blocks[0].get("source_type") == "Footer"

    leaked_chrome = [
        {
            "id": block.get("id"),
            "type": block.get("type"),
            "source_type": block.get("source_type"),
            "text": _normalized(block.get("text")),
            "bbox": block.get("bbox"),
        }
        for block in blocks
        if block.get("type") in {"section_heading", "paragraph_block", "list"}
        and (
            "NIST SP 800-53" in _normalized(block.get("text"))
            or "APPENDIX C" in _normalized(block.get("text"))
            or "PAGE 429" in _normalized(block.get("text"))
        )
    ]
    assert leaked_chrome == []
