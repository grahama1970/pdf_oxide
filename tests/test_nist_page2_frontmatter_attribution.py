import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_2_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 1, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_2_department_of_commerce_is_frontmatter_attribution_not_list():
    page = _extract_page_2_with_ledger()
    matches = [
        block
        for block in page.get("blocks") or []
        if str(block.get("text") or "") == "U.S. Department of Commerce"
    ]

    assert len(matches) == 1
    attribution = matches[0]
    assert attribution.get("type") == "paragraph_block"
    assert attribution.get("semantic_role") == "frontmatter_attribution"
    assert attribution.get("source_type") == "Body"
