import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"

EXPECTED_TOC_PATH = ["toc:0035"]
EXPECTED_BREADCRUMB = ["REFERENCES"]


def _extract_page_401_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 400, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def _normalized(text):
    return " ".join(str(text or "").split())


def _block_by_text(blocks, expected_text):
    expected = _normalized(expected_text)
    matches = [block for block in blocks if _normalized(block.get("text")) == expected]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(
    ("expected_text", "expected_type"),
    [
        ("REFERENCES", "section_heading"),
        ("[ATOM54] Atomic Energy Act (P.L. 83-703), August 1954.", "reference"),
    ],
)
def test_nist_page_401_references_blocks_keep_toc_lineage(expected_text, expected_type):
    page = _extract_page_401_with_ledger()
    block = _block_by_text(page.get("blocks") or [], expected_text)

    assert block.get("type") == expected_type
    assert block.get("toc_path") == EXPECTED_TOC_PATH
    assert block.get("breadcrumb") == EXPECTED_BREADCRUMB

    lineage = block.get("toc_lineage") or []
    assert [node.get("id") for node in lineage] == EXPECTED_TOC_PATH
    assert [node.get("label") for node in lineage] == EXPECTED_BREADCRUMB


def test_nist_page_401_reference_footnote_keeps_toc_lineage():
    page = _extract_page_401_with_ledger()
    matches = [
        block
        for block in page.get("blocks") or []
        if _normalized(block.get("text")).startswith(
            "34 The references cited in this appendix are those external publications"
        )
    ]

    assert len(matches) == 1
    footnote = matches[0]
    assert footnote.get("type") == "footnote"
    assert footnote.get("semantic_role") == "footnote_group"
    assert footnote.get("toc_path") == EXPECTED_TOC_PATH
    assert footnote.get("breadcrumb") == EXPECTED_BREADCRUMB
