import json
from pathlib import Path

from pdf_oxide.presets.applier import ApplierConfig, apply_ledger


REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def test_nist_field_split_separates_discussion_and_related_controls():
    ledger = json.loads(LEDGER.read_text())
    elements = [
        {
            "id": "actual:p382:block:6",
            "page": 382,
            "source_type": "Body",
            "type": "paragraph_block",
            "bbox": [0.235, 0.088, 0.851, 0.172],
            "font_size": 11.01,
            "is_bold": False,
            "text": "Obtain software from trusted sources. Discussion:  Trusted sources include offline secure storage facilities. Related Controls:  None.",
            "raw": {
                "matched_lines": [
                    {
                        "text": "Obtain software from trusted sources.",
                        "bbox": [0.235, 0.088, 0.620, 0.105],
                    },
                    {
                        "text": "Discussion:  Trusted sources include offline secure storage facilities.",
                        "bbox": [0.235, 0.106, 0.790, 0.123],
                    },
                    {
                        "text": "Related Controls:  None.",
                        "bbox": [0.235, 0.124, 0.430, 0.141],
                    },
                ]
            },
        },
        {
            "id": "ordinary:discussion",
            "page": 382,
            "source_type": "Body",
            "type": "paragraph_block",
            "bbox": [0.235, 0.30, 0.851, 0.34],
            "font_size": 10.02,
            "is_bold": False,
            "text": "Discussion:  Already separate.",
        },
    ]

    result = apply_ledger(elements, ledger, ApplierConfig(mode="release"))

    assert [element["id"] for element in result] == [
        "actual:p382:block:6",
        "actual:p382:block:6#discussion",
        "actual:p382:block:6#related_controls",
        "ordinary:discussion",
    ]
    assert result[0]["text"] == "Obtain software from trusted sources."
    assert result[0]["semantic_role"] == "control_statement"
    assert result[0]["child_ids"] == [
        "actual:p382:block:6",
        "actual:p382:block:6#discussion",
        "actual:p382:block:6#related_controls",
    ]
    assert result[1]["text"].startswith("Discussion:")
    assert result[1]["semantic_role"] == "discussion"
    assert result[1]["bbox"] == [0.235, 0.106, 0.79, 0.123]
    assert result[2]["text"] == "Related Controls:  None."
    assert result[2]["semantic_role"] == "related_controls"
    assert result[2]["bbox"] == [0.235, 0.124, 0.43, 0.141]
    assert result[3]["id"] == "ordinary:discussion"


def test_nist_standalone_field_label_is_not_heading():
    ledger = json.loads(LEDGER.read_text())
    elements = [
        {
            "id": "actual:p399:block:13",
            "page": 399,
            "source_type": "Body",
            "type": "paragraph_block",
            "bbox": [0.147, 0.377, 0.263, 0.390],
            "font_size": 9.51,
            "is_bold": True,
            "text": "Control:",
        },
        {
            "id": "actual:p399:block:12",
            "page": 399,
            "source_type": "Body",
            "type": "paragraph_block",
            "bbox": [0.147, 0.353, 0.424, 0.367],
            "font_size": 10.98,
            "is_bold": True,
            "text": "SR-11 COMPONENT AUTHENTICITY",
        },
    ]

    result = apply_ledger(elements, ledger, ApplierConfig(mode="release"))

    assert result[0]["type"] == "paragraph_block"
    assert result[0]["semantic_role"] == "nist_field_label"
    assert result[1]["type"] == "section_heading"
    assert result[1]["semantic_role"] == "nist_control_heading"
