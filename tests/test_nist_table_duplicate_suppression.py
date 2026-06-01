import json
from pathlib import Path

from pdf_oxide.presets.applier import ApplierConfig, apply_ledger


REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _apply(elements):
    ledger = json.loads(LEDGER.read_text())
    return apply_ledger(elements, ledger, ApplierConfig(mode="release"))


def test_nist_table_suppression_removes_contained_standalone_cells():
    elements = [
        {
            "id": "actual:p456:table:0",
            "page": 456,
            "source_type": "table",
            "type": "table",
            "bbox": [0.146, 0.113, 0.853, 0.904],
            "text": "CONTROL NUMBER | CONTROL NAME | IMPLEMENTED BY | ASSURANCE",
        },
        {
            "id": "actual:p456:line:100",
            "page": 456,
            "source_type": "Header",
            "type": "unknown_region",
            "bbox": [0.661, 0.188, 0.673, 0.202],
            "text": "O",
        },
        {
            "id": "actual:p456:line:54",
            "page": 456,
            "source_type": "Body",
            "type": "unknown_region",
            "bbox": [0.241, 0.187, 0.370, 0.201],
            "text": "Policy and Procedures",
        },
    ]

    result = _apply(elements)

    assert [element["id"] for element in result] == ["actual:p456:table:0"]


def test_nist_table_suppression_removes_contained_unknown_equation_fragments():
    elements = [
        {
            "id": "actual:p464:table:0",
            "page": 464,
            "source_type": "table",
            "type": "table",
            "bbox": [0.146, 0.113, 0.853, 0.900],
            "text": "CM-3(1) | AUTOMATED DOCUMENTATION | O | √",
        },
        {
            "id": "actual:p464:block:26",
            "page": 464,
            "source_type": "Equation",
            "type": "unknown_region",
            "bbox": [0.663, 0.359, 0.798, 0.370],
            "text": "O √",
        },
    ]

    result = _apply(elements)

    assert [element["id"] for element in result] == ["actual:p464:table:0"]


def test_nist_table_suppression_preserves_caption_and_outside_content():
    elements = [
        {
            "id": "actual:p456:table:0",
            "page": 456,
            "source_type": "table",
            "type": "table",
            "bbox": [0.146, 0.113, 0.853, 0.904],
            "text": "CONTROL NUMBER | CONTROL NAME | IMPLEMENTED BY | ASSURANCE",
        },
        {
            "id": "actual:p456:caption:0",
            "page": 456,
            "source_type": "Caption",
            "type": "unknown_region",
            "bbox": [0.377, 0.088, 0.619, 0.104],
            "text": "TABLE C-1: ACCESS CONTROL FAMILY",
        },
        {
            "id": "actual:p456:line:outside",
            "page": 456,
            "source_type": "Body",
            "type": "unknown_region",
            "bbox": [0.12, 0.92, 0.50, 0.94],
            "text": "Outside table text",
        },
    ]

    result = _apply(elements)

    assert [element["id"] for element in result] == [
        "actual:p456:table:0",
        "actual:p456:caption:0",
        "actual:p456:line:outside",
    ]


def test_nist_false_positive_table_is_removed_before_body_suppression():
    elements = [
        {
            "id": "actual:p157:block:0",
            "page": 157,
            "source_type": "Header",
            "type": "unknown_region",
            "bbox": [0.147, 0.045, 1.0, 0.055],
            "text": "NIST SP 800-53, REV. 5 SECURITY AND PRIVACY CONTROLS",
        },
        {
            "id": "actual:p157:block:6",
            "page": 157,
            "source_type": "Body",
            "type": "unknown_region",
            "bbox": [0.21, 0.10, 0.41, 0.13],
            "text": "Control Enhancements: None",
        },
        {
            "id": "actual:p157:block:7",
            "page": 157,
            "source_type": "Body",
            "type": "unknown_region",
            "bbox": [0.21, 0.13, 0.34, 0.16],
            "text": "References: None.",
        },
        {
            "id": "actual:p157:table:0",
            "page": 157,
            "source_type": "table",
            "type": "table",
            "bbox": [0.034, 0.045, 0.853, 0.952],
            "text": "",
            "raw": {
                "row_count": 53,
                "column_count": 19,
                "rows": [
                    {"cells": [{"text": "NIST"}, {"text": "SP 800-53"}]},
                    {"cells": [{"text": "Control"}, {"text": "Enhancements:"}, {"text": "None"}]},
                    {"cells": [{"text": "References:"}, {"text": "None."}]},
                    {"cells": [{"text": "This publication is available free of charge from:"}]},
                    {"cells": [{"text": "CHAPTER"}, {"text": "THREE"}, {"text": "PAGE"}, {"text": "130"}]},
                ],
            },
        },
    ]

    result = _apply(elements)

    assert [element["id"] for element in result] == [
        "actual:p157:block:0",
        "actual:p157:block:6",
        "actual:p157:block:7",
    ]
    assert [element["type"] for element in result] == [
        "section_heading",
        "paragraph_block",
        "paragraph_block",
    ]
