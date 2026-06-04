import json
from pathlib import Path

from pdf_oxide.presets.applier import ApplierConfig, apply_ledger


REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def test_nist_top_running_header_and_rule_are_page_chrome_after_header_remap():
    ledger = json.loads(LEDGER.read_text())
    elements = [
        {
            "id": "actual:p27:block:0",
            "page": 27,
            "source_type": "Header",
            "type": "unknown_region",
            "bbox": [
                0.14705882352941177,
                0.04473800287064564,
                0.8507449929314311,
                0.05821755146980286,
            ],
            "text": (
                "NIST SP 800-53, REV. 5 "
                "SECURITY AND PRIVACY CONTROLS FOR INFORMATION SYSTEMS AND ORGANIZATIONS"
            ),
        },
        {
            "id": "actual:p27:block:1",
            "page": 27,
            "source_type": "Header",
            "type": "unknown_region",
            "bbox": [
                0.14705882352941177,
                0.05679133214473659,
                0.8517156862745098,
                0.07054428688936337,
            ],
            "text": "_________________________________________________________________________________________________",
        },
        {
            "id": "actual:p27:block:2",
            "page": 27,
            "source_type": "Header",
            "type": "unknown_region",
            "bbox": [0.147, 0.20, 0.80, 0.22],
            "text": "CA-5 PLAN OF ACTION AND MILESTONES",
        },
    ]

    result = apply_ledger(elements, ledger, ApplierConfig(mode="release"))

    assert [element["type"] for element in result[:2]] == [
        "header_footer_noise",
        "header_footer_noise",
    ]
    assert [element["semantic_role"] for element in result[:2]] == [
        "page_chrome",
        "page_chrome",
    ]
    assert result[2]["type"] == "section_heading"
    assert "semantic_role" not in result[2]
