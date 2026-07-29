import importlib.util
import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"
APPLIER = REPO / "python/pdf_oxide/presets/applier.py"


_SPEC = importlib.util.spec_from_file_location("nist_preset_applier", APPLIER)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"could not load applier from {APPLIER}")
applier = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = applier
_SPEC.loader.exec_module(applier)
ApplierConfig = applier.ApplierConfig
apply_ledger = applier.apply_ledger


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


def test_nist_page27_printed_page_number_is_running_footer():
    ledger = json.loads(LEDGER.read_text())
    elements = [
        {
            "id": "actual:p27:block:2",
            "page": 27,
            "source_type": "PageNumber",
            "type": "unknown_region",
            "bbox": [
                0.49029411365783293,
                0.939856018682923,
                0.5096764658011642,
                0.9550605542732008,
            ],
            "text": "xxv",
        }
    ]

    result = apply_ledger(elements, ledger, ApplierConfig(mode="release"))

    assert result[0]["type"] == "running_footer"
    assert result[0]["semantic_role"] == "page_chrome"
