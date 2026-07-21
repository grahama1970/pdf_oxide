import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


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


def test_nist_page_45_discussion_text_uses_clean_line_order():
    page = _extract_page_45_with_ledger()
    blocks = page.get("blocks") or []
    matches = [
        block
        for block in blocks
        if " ".join(str(block.get("text") or "").split()).startswith(
            "Discussion: Access control policy and procedures address the controls"
        )
    ]

    assert len(matches) == 1
    discussion_text = " ".join(str(matches[0].get("text") or "").split())
    assert "policies and procedures. Policies and procedures contribute" in discussion_text
    assert "aPnd procedures. olicies" not in discussion_text
