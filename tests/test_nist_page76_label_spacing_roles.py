import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_76_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 75, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_76_discussion_and_related_controls_have_roles_and_single_label_gap():
    page = _extract_page_76_with_ledger()
    blocks = page.get("blocks") or []

    discussion_blocks = [
        block
        for block in blocks
        if str(block.get("text") or "").startswith("Discussion:")
    ]
    related_controls_blocks = [
        block
        for block in blocks
        if str(block.get("text") or "").startswith("Related Controls:")
    ]

    assert len(discussion_blocks) >= 3
    assert len(related_controls_blocks) >= 3
    for block in [*discussion_blocks, *related_controls_blocks]:
        text = str(block.get("text") or "")
        assert ":  " not in text
        assert block.get("type") == "paragraph_block"
        assert block.get("source_type") == "Body"

    assert {block.get("semantic_role") for block in discussion_blocks} == {"discussion"}
    assert {block.get("semantic_role") for block in related_controls_blocks} == {"related_controls"}


def test_nist_page_76_bottom_footer_has_page_chrome_role_after_page_number_normalization():
    page = _extract_page_76_with_ledger()
    blocks = page.get("blocks") or []

    footers = [
        block
        for block in blocks
        if str(block.get("text") or "") == "CHAPTER THREE PAGE 49"
    ]

    assert len(footers) == 1
    footer = footers[0]
    assert footer.get("type") == "header_footer_noise"
    assert footer.get("semantic_role") == "page_chrome"
    assert footer.get("source_type") == "Body"
