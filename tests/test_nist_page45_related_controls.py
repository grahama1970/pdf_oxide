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


def test_nist_page_45_related_controls_has_semantic_role():
    page = _extract_page_45_with_ledger()
    blocks = page.get("blocks") or []
    matches = [
        block
        for block in blocks
        if " ".join(str(block.get("text") or "").split())
        == "Related Controls: IA-1, PM-9, PM-24, PS-8, SI-12."
    ]

    assert len(matches) == 1
    related_controls = matches[0]
    assert related_controls.get("type") == "paragraph_block"
    assert related_controls.get("semantic_role") == "related_controls"
    assert related_controls.get("source_type") == "Body"
    assert related_controls.get("toc_path") == ["toc:0014", "toc:0015"]
