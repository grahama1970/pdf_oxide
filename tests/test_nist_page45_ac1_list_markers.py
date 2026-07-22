import contextlib
import re
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


def test_nist_page_45_ac1_lower_alpha_markers_are_lists_not_headings():
    page = _extract_page_45_with_ledger()
    blocks = page.get("blocks") or []
    texts_by_prefix = {
        "a.": [],
        "b.": [],
        "c.": [],
    }
    marker_mistypes = []

    for block in blocks:
        text = " ".join(str(block.get("text") or "").split())
        for prefix in texts_by_prefix:
            if text.startswith(prefix):
                texts_by_prefix[prefix].append(block)
        if re.match(r"^[cd]\.\s+", text) and block.get("type") not in {"list", "list_item"}:
            marker_mistypes.append(
                {
                    "id": block.get("id"),
                    "type": block.get("type"),
                    "source_type": block.get("source_type"),
                    "text": text,
                    "bbox": block.get("bbox"),
                }
            )

    assert texts_by_prefix["a."], "expected AC-1 a. list marker"
    assert texts_by_prefix["b."], "expected AC-1 b. list marker"
    assert texts_by_prefix["c."], "expected AC-1 c. list marker"
    assert all(block.get("type") == "list" for blocks in texts_by_prefix.values() for block in blocks)
    assert marker_mistypes == []

    related_controls = [
        block
        for block in blocks
        if "Related Controls:" in " ".join(str(block.get("text") or "").split())
    ]
    assert len(related_controls) == 1
    assert related_controls[0].get("type") == "paragraph_block"
