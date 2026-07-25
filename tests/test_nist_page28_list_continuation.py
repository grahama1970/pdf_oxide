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
    return " ".join(str(text or "").split()).lower()


def test_nist_page_28_bullet_continuations_remain_in_single_list_block():
    page = _extract_page_28_with_ledger()
    blocks = page.get("blocks") or []

    list_blocks = [block for block in blocks if block.get("type") == "list"]
    matching_lists = [
        block
        for block in list_blocks
        if "what security and privacy controls are needed" in _normalized(block.get("text"))
    ]
    assert len(matching_lists) == 1

    list_text = _normalized(matching_lists[0].get("text"))
    for expected in [
        "what security and privacy controls are needed",
        "and to adequately manage mission/business risks or risks to individuals",
        "have the selected controls been implemented",
        "what is the required level of assurance",
        "controls, as designed and implemented, are effective",
    ]:
        assert expected in list_text

    leaked_continuations = [
        {
            "id": block.get("id"),
            "type": block.get("type"),
            "source_type": block.get("source_type"),
            "text": block.get("text"),
            "bbox": block.get("bbox"),
        }
        for block in blocks
        if block.get("type") == "paragraph_block"
        and (
            "and to adequately manage mission/business risks" in _normalized(block.get("text"))
            or "controls, as designed and implemented, are effective" in _normalized(block.get("text"))
        )
    ]
    assert leaked_continuations == []
