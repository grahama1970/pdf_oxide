import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"
MERGED_TEXT = (
    "Throughout this publication, examples are used to illustrate, clarify, or explain certain items in "
    "chapter sections, controls, and control enhancements. These examples are illustrative in nature "
    "and are not intended to limit or constrain the application of controls or control enhancements by organizations."
)
FRAGMENTS = [
    "Throughout this publication, examples are used to illustrate, clarify, or explain certain items in",
    "chapter sections, controls, and control enhancements. These examples are illustrative in nature",
    "and are not intended to limit or constrain the application of controls or control enhancements by organizations.",
]


def _extract_page_13_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 12, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_13_use_examples_body_paragraph_is_merged_not_heading():
    page = _extract_page_13_with_ledger()
    blocks = page.get("blocks") or []
    matches = [block for block in blocks if str(block.get("text") or "") == MERGED_TEXT]

    assert len(matches) == 1
    merged = matches[0]
    assert merged.get("type") == "paragraph_block"
    assert merged.get("source_type") == "Body"
    assert merged.get("child_ids") == ["actual:p13:block:5", "actual:p13:block:7"]
    for fragment in FRAGMENTS:
        assert not any(block.get("text") == fragment for block in blocks)
