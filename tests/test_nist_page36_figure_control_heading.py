import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_36_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 35, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def _norm(text: object) -> str:
    return " ".join(str(text or "").split())


def test_nist_page_36_figure_control_heading_is_figure_content():
    page = _extract_page_36_with_ledger()
    blocks = page.get("blocks") or []

    heading_matches = [
        block for block in blocks if _norm(block.get("text")) == "AU-4 AUDIT STORAGE CAPACITY"
    ]
    assert len(heading_matches) == 1
    heading = heading_matches[0]
    assert heading.get("id") == "actual:p36:block:10"
    assert heading.get("source_type") == "Body"
    assert heading.get("type") == "figure"
    assert heading.get("semantic_role") == "figure_content"
    assert heading.get("bbox") == [
        0.15960784363590813,
        0.37824719361584597,
        0.41836433161317915,
        0.39748995231859613,
    ]
