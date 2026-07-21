import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_421_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 420, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_421_glossary_terms_materialize_as_definition_table():
    page = _extract_page_421_with_ledger()
    blocks = page.get("blocks") or []

    glossary_tables = [
        block
        for block in blocks
        if block.get("type") == "table"
        and block.get("table_kind") == "glossary"
    ]
    assert len(glossary_tables) == 1

    table_text = " ".join(str(glossary_tables[0].get("text") or "").split())
    for expected in [
        "TERM | DEFINITION",
        "access control | [FIPS 201-2] The process of granting or denying",
        "adequate security | [OMB A-130] Security protections commensurate",
        "advanced persistent threat | [SP 800-39] An adversary",
        "agency | [OMB A-130] Any executive agency",
        "all-source intelligence | [DODTERMS] Intelligence products",
    ]:
        assert expected in table_text

    leaked_definition_parts = []
    for block in blocks:
        if block.get("type") == "table":
            continue
        text = " ".join(str(block.get("text") or "").split())
        if text in {
            "access control",
            "[FIPS 201-2]",
            "adequate security",
            "[OMB A-130]",
            "advanced persistent threat",
            "[SP 800-39]",
            "agency",
            "all-source intelligence",
            "[DODTERMS]",
        } or text.startswith(
            (
                "The process of granting or denying specific requests",
                "Security protections commensurate with the risk",
                "An adversary that possesses sophisticated levels",
                "Any executive agency or department",
                "Intelligence products and/or organizations",
            )
        ):
            leaked_definition_parts.append(
                {
                    "id": block.get("id"),
                    "type": block.get("type"),
                    "source_type": block.get("source_type"),
                    "text": text,
                    "bbox": block.get("bbox"),
                }
            )

    assert leaked_definition_parts == []

    standalone_citation_references = [
        {
            "id": block.get("id"),
            "type": block.get("type"),
            "source_type": block.get("source_type"),
            "text": " ".join(str(block.get("text") or "").split()),
        }
        for block in blocks
        if block.get("type") == "reference"
        and " ".join(str(block.get("text") or "").split())
        in {"[FIPS 201-2]", "[OMB A-130]", "[SP 800-39]"}
    ]
    assert standalone_citation_references == []
