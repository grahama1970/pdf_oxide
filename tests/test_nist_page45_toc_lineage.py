import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"

EXPECTED_TOC_PATH = ["toc:0014", "toc:0015"]
EXPECTED_BREADCRUMB = ["CHAPTER THREE THE CONTROLS", "3.1 ACCESS CONTROL"]


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


def _block_by_exact_text(blocks, expected_text):
    normalized_expected = " ".join(expected_text.split())
    matches = [
        block
        for block in blocks
        if " ".join(str(block.get("text") or "").split()) == normalized_expected
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(
    "expected_text",
    [
        "Control:",
        "a. Develop, document, and disseminate to [Assignment: organization-defined personnel or roles]:",
        "(a) Addresses purpose, scope, roles, responsibilities, management commitment, coordination among organizational entities, and compliance; and",
        "b. Designate an [Assignment: organization-defined official] to manage the development, documentation, and dissemination of the access control policy and procedures; and",
    ],
)
def test_nist_page_45_body_blocks_keep_chapter_and_section_toc_lineage(expected_text):
    page = _extract_page_45_with_ledger()
    block = _block_by_exact_text(page.get("blocks") or [], expected_text)

    assert block.get("toc_path") == EXPECTED_TOC_PATH
    assert block.get("breadcrumb") == EXPECTED_BREADCRUMB

    lineage = block.get("toc_lineage") or []
    assert [node.get("id") for node in lineage] == EXPECTED_TOC_PATH
    assert [node.get("label") for node in lineage] == EXPECTED_BREADCRUMB
