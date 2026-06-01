import sys
from pathlib import Path

import pytest

from pdf_oxide.presets.applier import ApplierConfig, apply_ledger


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts/pdf_lab"
SOURCE_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"

sys.path.insert(0, str(SCRIPTS))
import snapshot_current_extraction as snapshot  # noqa: E402


def _page_blocks(page: int) -> list[dict]:
    if not SOURCE_PDF.exists():
        pytest.skip(f"missing corpus fixture: {SOURCE_PDF}")
    payload = snapshot._extract_page(
        SOURCE_PDF,
        page - 1,
        LEDGER,
        "release",
    )
    return payload["blocks"]


@pytest.mark.parametrize(
    ("page", "header", "min_rows"),
    [
        (19, "DATE | TYPE | REVISION | PAGE", 18),
        (20, "DATE | TYPE | REVISION | PAGE", 25),
        (457, "CONTROL NUMBER", 40),
        (485, "CONTROL NUMBER", 40),
    ],
)
def test_nist_ruled_tables_are_materialized_from_shared_table_extractor(page, header, min_rows):
    blocks = _page_blocks(page)
    tables = [block for block in blocks if block.get("type") == "table"]

    assert len(tables) == 1
    table = tables[0]
    assert header in table["text"].splitlines()[0]
    assert table["raw"]["row_count"] >= min_rows
    assert table["raw"]["column_count"] == 4
    assert table["raw"]["rows"][0]["cells"]


@pytest.mark.parametrize("page", [457, 485])
def test_nist_ruled_table_cell_text_suppresses_standalone_heading_blocks(page):
    blocks = _page_blocks(page)
    non_table_text = "\n".join(
        block.get("text", "")
        for block in blocks
        if block.get("type") != "table"
    )

    assert "CONTROL NAME" not in non_table_text
    assert "IMPLEMENTED" not in non_table_text


def test_table_contained_suppression_covers_section_subtitle_cells():
    ledger = {
        "entries": [
                {
                    "entry_id": "suppress-table-contained",
                    "applier_rule_kind": "table_contained_suppression_rule",
                    "category": "structural_grouping",
                    "status": "closed",
                    "rule": {
                    "table_when": {"type": "table"},
                    "suppress_when": {"type": ["section_subtitle"]},
                    "min_coverage": 0.9,
                },
            }
        ]
    }
    elements = [
        {
            "id": "table",
            "page": 1,
            "type": "table",
            "source_type": "table",
            "bbox": [0.1, 0.1, 0.9, 0.9],
            "text": "CONTROL NUMBER | CONTROL NAME",
        },
        {
            "id": "cell-heading",
            "page": 1,
            "type": "section_subtitle",
            "source_type": "Body",
            "bbox": [0.2, 0.2, 0.4, 0.25],
            "text": "AC-4(11) CONFIGURATION OF SECURITY OR PRIVACY POLICY FILTERS S",
        },
    ]

    result = apply_ledger(elements, ledger, ApplierConfig(mode="release"))

    assert [element["id"] for element in result] == ["table"]


def test_snapshot_table_serialization_prefers_complete_data_matrix():
    table = {
        "text": "CONTROL NUMBER | CONTROL ENHANCEMENT NAME | IMPLEMENTED BY | ASSURANCE",
        "data": [
            [
                "CONTROL NUMBER",
                "CONTROL NAME CONTROL ENHANCEMENT NAME",
                "IMPLEMENTED BY",
                "ASSURANCE",
            ],
            ["AC-1", "Policy and Procedures", "O", "√"],
        ],
        "rows": [
            {
                "cells": [
                    {"text": "CONTROL NUMBER"},
                    {"text": "CONTROL ENHANCEMENT NAME"},
                    {"text": "IMPLEMENTED BY"},
                    {"text": "ASSURANCE"},
                ]
            }
        ],
    }

    raw = snapshot._raw_table_payload(table, snapshot._table_metrics(table))

    assert "CONTROL NAME CONTROL ENHANCEMENT NAME" in snapshot._table_text(table)
    assert raw["rows"][0]["cells"][1]["text"] == "CONTROL NAME CONTROL ENHANCEMENT NAME"


def test_snapshot_table_text_preserves_trailing_empty_cells():
    table = {
        "data": [
            ["CONTROL NUMBER", "CONTROL NAME", "IMPLEMENTED BY", "ASSURANCE"],
            ["SI-6(2)", "AUTOMATED TRACKING, REPORTING, AND CORRECTIVE ACTION", "S", ""],
        ],
    }

    lines = snapshot._table_text(table).splitlines()

    assert lines[1] == "SI-6(2) | AUTOMATED TRACKING, REPORTING, AND CORRECTIVE ACTION | S |"
