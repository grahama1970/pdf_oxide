import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_46_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 45, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def _texts(page):
    return [" ".join(str(block.get("text") or "").split()) for block in page.get("blocks") or []]


def test_nist_page_46_splits_h_nested_account_notification_list():
    page = _extract_page_46_with_ledger()
    texts = _texts(page)

    assert not [
        block.get("id")
        for block in page.get("blocks") or []
        if block.get("type") == "table"
        and "AC-2 AC | COUNT MANAGEMENT" in " ".join(str(block.get("text") or "").split())
    ]

    assert not any(
        text.startswith("h. Notify account managers")
        and " i. Authorize access to the system based on:" in text
        for text in texts
    )

    assert any(text.startswith("h. Notify account managers") and text.endswith("within:") for text in texts)
    assert (
        sum(text.startswith("h. Notify account managers") and text.endswith("within:") for text in texts) == 1
    )
    assert any(
        text.startswith("1. [Assignment: organization-defined time period]")
        and "when accounts are no longer required;" in text
        for text in texts
    )
    assert (
        sum(
            text.startswith("1. [Assignment: organization-defined time period]")
            and "when accounts are no longer required;" in text
            for text in texts
        )
        == 1
    )
    assert any(
        text.startswith("2. [Assignment: organization-defined time period]")
        and text.endswith("when users are terminated or transferred; and")
        for text in texts
    )
    assert (
        sum(
            text.startswith("2. [Assignment: organization-defined time period]")
            and text.endswith("when users are terminated or transferred; and")
            for text in texts
        )
        == 1
    )
    assert any(
        text.startswith("3. [Assignment: organization-defined time period]")
        and text.endswith("changes for an individual;")
        for text in texts
    )
    assert (
        sum(
            text.startswith("3. [Assignment: organization-defined time period]")
            and text.endswith("changes for an individual;")
            for text in texts
        )
        == 1
    )

    assert any(text.startswith("i. Authorize access to the system based on:") for text in texts)
    assert sum(text.startswith("i. Authorize access to the system based on:") for text in texts) == 1
    assert any(text == "1. A valid access authorization;" for text in texts)
    assert any(text == "2. Intended system usage; and" for text in texts)
    assert any(text == "3. [Assignment: organization-defined attributes (as required)];" for text in texts)

    assert not [
        block.get("id")
        for block in page.get("blocks") or []
        if (block.get("raw") or {}).get("repair") == "page46_ac2_control_table_to_structured_text"
    ]
