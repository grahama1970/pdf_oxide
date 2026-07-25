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


def test_nist_page_28_rotated_sidebar_chrome_is_not_body_content():
    page = _extract_page_28_with_ledger()
    blocks = page.get("blocks") or []

    doi_blocks = [
        block
        for block in blocks
        if "doi.org/10.6028/NIST.SP.800" in _normalized(block.get("text"))
        or "This publication is available free of charge" in _normalized(block.get("text"))
    ]
    assert len(doi_blocks) == 1
    assert doi_blocks[0].get("type") == "header_footer_noise"
    assert doi_blocks[0].get("source_type") == "RotatedSideChrome"

    body_doi_leaks = [
        {
            "id": block.get("id"),
            "type": block.get("type"),
            "source_type": block.get("source_type"),
            "text": _normalized(block.get("text")),
            "bbox": block.get("bbox"),
        }
        for block in blocks
        if block.get("type") in {"paragraph_block", "list"}
        and (
            "doi.org/10.6028/NIST.SP.800" in _normalized(block.get("text"))
            or "This publication is available free of charge" in _normalized(block.get("text"))
        )
    ]
    assert body_doi_leaks == []

    paragraphs = [
        _normalized(block.get("text"))
        for block in blocks
        if block.get("type") == "paragraph_block" and _normalized(block.get("text"))
    ]
    assert any(text.startswith("Modern information systems") for text in paragraphs)
