import json
import sys
from pathlib import Path

import pytest

from pdf_oxide.presets.applier import ApplierConfig, apply_ledger


REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"
SOURCE_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")

sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
import snapshot_current_extraction as snapshot  # noqa: E402


def test_nist_same_band_running_headers_merge_to_one_header_footer_noise_element():
    ledger = json.loads(LEDGER.read_text())
    elements = [
        {
            "id": "actual:p27:line:0",
            "page": 27,
            "source_type": "running_header",
            "type": "running_header",
            "bbox": [0.145, 0.043, 0.264, 0.057],
            "text": "NIST SP 800-53, R EV. 5",
        },
        {
            "id": "actual:p27:line:68+actual:p27:line:111",
            "page": 27,
            "source_type": "running_header",
            "type": "running_header",
            "bbox": [0.464, 0.043, 0.855, 0.057],
            "text": "SECURITY AND PRIVACY CONTROLS FOR INFORMATION SYSTEMS AND ORGANIZATIONS",
        },
        {
            "id": "actual:p27:table:0",
            "page": 27,
            "source_type": "table",
            "type": "table",
            "bbox": [0.146, 0.092, 0.853, 0.725],
            "text": "DATE | TYPE | REVISION | PAGE",
        },
    ]

    result = apply_ledger(elements, ledger, ApplierConfig(mode="release"))

    assert [element for element in result if element["type"] == "running_header"] == []
    page_chrome = [
        element
        for element in result
        if element["type"] == "header_footer_noise"
        and element.get("semantic_role") == "page_chrome"
    ]
    assert len(page_chrome) == 1
    assert page_chrome[0]["child_ids"] == [
        "actual:p27:line:0",
        "actual:p27:line:68+actual:p27:line:111",
    ]
    assert "NIST SP 800-53" in page_chrome[0]["text"]
    assert "SECURITY AND PRIVACY CONTROLS" in page_chrome[0]["text"]
    assert [element["id"] for element in result if element["type"] == "table"] == [
        "actual:p27:table:0"
    ]


def test_nist_page_27_snapshot_routes_top_header_to_header_footer_noise():
    if not SOURCE_PDF.exists():
        pytest.skip(f"missing corpus fixture: {SOURCE_PDF}")

    payload = snapshot._extract_page(SOURCE_PDF, 26, LEDGER, "release")
    blocks = payload["blocks"]

    top_header_blocks = [
        block
        for block in blocks
        if block.get("type") == "header_footer_noise"
        and "NIST SP 800-53" in block.get("text", "")
        and "SECURITY AND PRIVACY CONTROLS" in block.get("text", "")
    ]
    assert len(top_header_blocks) == 1
    assert top_header_blocks[0].get("semantic_role") == "page_chrome"
    assert not [
        block
        for block in blocks
        if block.get("type") == "running_header"
        and (
            "NIST SP 800-53" in block.get("text", "")
            or "SECURITY AND PRIVACY CONTROLS" in block.get("text", "")
        )
    ]
    assert any(block.get("id") == "actual:p27:table:0" and block.get("type") == "table" for block in blocks)
