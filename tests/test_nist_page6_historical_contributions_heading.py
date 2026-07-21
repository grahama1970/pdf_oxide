import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"
HEADING_TEXT = "HISTORICAL CONTRIBUTIONS TO NIST SPECIAL PUBLICATION 800-53"


def _extract_page_6_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 5, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_6_historical_contributions_is_frontmatter_heading_not_reference():
    page = _extract_page_6_with_ledger()
    matches = [block for block in page.get("blocks") or [] if str(block.get("text") or "") == HEADING_TEXT]

    assert len(matches) == 1
    heading = matches[0]
    assert heading.get("type") == "section_heading"
    assert heading.get("semantic_role") == "frontmatter_section_heading"
    assert heading.get("source_type") == "Body"
