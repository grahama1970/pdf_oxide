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


def test_nist_page_45_quick_link_is_section_link_not_paragraph():
    page = _extract_page_45_with_ledger()
    blocks = page.get("blocks") or []
    matches = [
        block
        for block in blocks
        if " ".join(str(block.get("text") or "").split()).lower()
        == "quick link to access control summary table"
    ]

    assert len(matches) == 1
    quick_link = matches[0]
    assert quick_link.get("type") == "section_link"
    assert quick_link.get("semantic_role") == "nist_quick_link"
    assert quick_link.get("source_type") == "Body"
    assert quick_link.get("toc_path") == ["toc:0014", "toc:0015"]
