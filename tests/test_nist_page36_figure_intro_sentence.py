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


def test_nist_page_36_figure_intro_sentence_is_body_prose():
    page = _extract_page_36_with_ledger()
    blocks = page.get("blocks") or []

    intro_matches = [
        block
        for block in blocks
        if _norm(block.get("text")) == "Figure 1 illustrates the structure of a typical control."
    ]
    assert len(intro_matches) == 1
    intro = intro_matches[0]
    assert intro.get("id") == "actual:p36:block:7"
    assert intro.get("source_type") == "Caption"
    assert intro.get("type") == "paragraph_block"
    assert intro.get("semantic_role") == "figure_intro"

    title_matches = [
        block
        for block in blocks
        if _norm(block.get("text")) == "FIGURE 1: CONTROL STRUCTURE"
    ]
    assert len(title_matches) == 1
    title = title_matches[0]
    assert title.get("id") == "actual:p36:block:8"
    assert title.get("type") == "caption"
    assert title.get("semantic_role") == "figure_caption"
