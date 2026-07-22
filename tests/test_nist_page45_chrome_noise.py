import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"

CHROME_ANCHORS = (
    "NIST SP 800-53, REV. 5",
    "SECURITY AND PRIVACY CONTROLS FOR INFORMATION SYSTEMS AND ORGANIZATIONS",
    "This publication is available free of charge from: https://doi.org/10.6028/NIST.SP.800 -53r5",
    "CHAPTER THREE PAGE 18",
)


def _extract_page_45_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 44, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def _normalized(text):
    return " ".join(str(text or "").split())


def test_nist_page_45_chrome_text_is_typed_as_noise_not_body_content():
    page = _extract_page_45_with_ledger()
    blocks = page.get("blocks") or []
    chrome_blocks = [block for block in blocks if block.get("type") == "header_footer_noise"]

    assert len(chrome_blocks) == 4
    assert {block.get("source_type") for block in chrome_blocks} == {
        "Header",
        "Footer",
        "Boilerplate",
    }

    chrome_text = "\n".join(_normalized(block.get("text")) for block in chrome_blocks)
    for anchor in CHROME_ANCHORS:
        assert _normalized(anchor) in chrome_text

    non_chrome_text = "\n".join(
        _normalized(block.get("text"))
        for block in blocks
        if block.get("type") != "header_footer_noise"
    )
    for anchor in CHROME_ANCHORS:
        assert _normalized(anchor) not in non_chrome_text


def test_nist_page_45_rotated_doi_chrome_bbox_stays_in_left_margin():
    page = _extract_page_45_with_ledger()
    blocks = page.get("blocks") or []
    doi_blocks = [
        block
        for block in blocks
        if block.get("type") == "header_footer_noise"
        and "doi.org/10.6028/NIST.SP.800" in _normalized(block.get("text"))
    ]

    assert len(doi_blocks) == 1
    bbox = doi_blocks[0].get("bbox")
    assert isinstance(bbox, list) and len(bbox) == 4
    assert bbox[0] < 0.06
    assert bbox[2] < 0.08
