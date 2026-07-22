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


def test_nist_page_45_control_enhancements_none_is_labeled_enhancements():
    page = _extract_page_45_with_ledger()
    blocks = page.get("blocks") or []
    matches = [
        block
        for block in blocks
        if " ".join(str(block.get("text") or "").split()).lower()
        == "control enhancements: none."
    ]

    assert len(matches) == 1
    enhancement = matches[0]
    assert enhancement.get("type") == "labeled_enhancements"
    assert enhancement.get("semantic_role") == "nist_control_enhancements_none"
    assert enhancement.get("source_type") == "Body"
    assert enhancement.get("toc_path") == ["toc:0014", "toc:0015"]
