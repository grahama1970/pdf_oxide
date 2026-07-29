import contextlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"
NIST_STYLE_FIXTURE = REPO / "tests/fixtures/generated/nist_style_fixtures.pdf"
APPLIER_PATH = REPO / "python/pdf_oxide/presets/applier.py"

_APPLIER_SPEC = importlib.util.spec_from_file_location(
    "pdf_oxide_presets_applier_test", APPLIER_PATH
)
if _APPLIER_SPEC is None or _APPLIER_SPEC.loader is None:
    raise RuntimeError(f"could not load applier module from {APPLIER_PATH}")
_APPLIER = importlib.util.module_from_spec(_APPLIER_SPEC)
sys.modules[_APPLIER_SPEC.name] = _APPLIER
_APPLIER_SPEC.loader.exec_module(_APPLIER)
ApplierConfig = _APPLIER.ApplierConfig
apply_ledger = _APPLIER.apply_ledger


def _apply(elements):
    ledger = json.loads(LEDGER.read_text())
    return apply_ledger(elements, ledger, ApplierConfig(mode="release"))


def _extract_fixture_page_1_without_ledger():
    if not NIST_STYLE_FIXTURE.exists():
        pytest.fail(f"fixture PDF not present: {NIST_STYLE_FIXTURE}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_STYLE_FIXTURE, 0, None, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_off_page_table_bbox_preserves_text_and_marks_clipped_geometry(monkeypatch):
    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        class FakeDoc:
            def page_dimensions(self, page_index):
                assert page_index == 0
                return 612.0, 792.0

            def classify_blocks(self, page_index):
                assert page_index == 0
                return []

            def read_pdf(self, pages, flavor):
                assert pages == "1"
                assert flavor == "auto"
                return [
                    {
                        "bbox": (-92.8, 73.2, 704.8, 235.2),
                        "data": [
                            ["[QID_LEFT]Req ID", "[QID_RIGHT]Notes"],
                            ["[QID_AC1]AC-1", "[QID_NOTE]Annual review"],
                        ],
                        "flavor": "Lattice",
                    }
                ]

        fake_pdf_oxide = types.SimpleNamespace(open=lambda path: FakeDoc())
        monkeypatch.setitem(sys.modules, "pdf_oxide", fake_pdf_oxide)
        monkeypatch.setattr(snapshot, "_extract_fitz_text_lines", lambda *args: [])
        monkeypatch.setattr(snapshot, "_top_row_cell_bboxes_from_drawing_grid", lambda *args: [])

        page = snapshot._extract_page(Path("/tmp/off-page-table.pdf"), 0, None, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)

    tables = [block for block in page["blocks"] if block.get("type") == "table"]
    assert len(tables) == 1
    table = tables[0]
    geometry = table["table_geometry"]

    assert geometry["bbox_clipped_to_page"] is True
    assert geometry["raw_bbox"] == pytest.approx([-92.8, 73.2, 704.8, 235.2])
    assert geometry["full_normalized_bbox"][0] < 0.0
    assert geometry["full_normalized_bbox"][2] > 1.0
    assert geometry["off_page_extent"]["left"] > 0.0
    assert geometry["off_page_extent"]["right"] > 0.0
    assert table["bbox"] == pytest.approx([0.0, 0.09242424242424242, 1.0, 0.296969696969697])
    assert "[QID_LEFT]Req ID" in table["text"]
    assert "[QID_NOTE]Annual review" in table["text"]


def test_table_text_suppresses_trailing_all_empty_decorative_row():
    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        table = {
            "data": [
                ["DATE", "TYPE", "REVISION", "PAGE"],
                ["12-10-2020", "Editorial", "Table C-17 update", "454"],
                ["", "", "", ""],
            ]
        }

        assert snapshot._table_text(table) == (
            "DATE | TYPE | REVISION | PAGE\n12-10-2020 | Editorial | Table C-17 update | 454"
        )
        assert snapshot._raw_table_payload(table, snapshot._table_metrics(table))["rows"] == [
            {
                "cells": [
                    {"text": "DATE"},
                    {"text": "TYPE"},
                    {"text": "REVISION"},
                    {"text": "PAGE"},
                ]
            },
            {
                "cells": [
                    {"text": "12-10-2020"},
                    {"text": "Editorial"},
                    {"text": "Table C-17 update"},
                    {"text": "454"},
                ]
            },
        ]
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_style_page_1_real_extraction_suppresses_qid_table_row_duplicates():
    page = _extract_fixture_page_1_without_ledger()
    blocks = page.get("blocks") or []

    tables = [block for block in blocks if block.get("type") == "table"]
    assert tables, "control table block was not emitted"
    control_table = next(
        (
            table
            for table in tables
            if "Requirement Description" in (table.get("text") or "")
            and "AC-1" in (table.get("text") or "")
        ),
        None,
    )
    assert control_table is not None, "control table block was not found"

    duplicate_qid_rows = [
        {
            "id": block.get("id"),
            "type": block.get("type"),
            "source_type": block.get("source_type"),
            "text": block.get("text"),
        }
        for block in blocks
        if block.get("type") != "table" and str(block.get("text") or "").count("[QID_") >= 2
    ]
    assert not duplicate_qid_rows, (
        "standalone non-table QID row blocks were emitted inside the control table: "
        f"{duplicate_qid_rows}"
    )

    table_text = control_table.get("text") or ""
    assert "Access Control Policy and Procedures" in table_text
    assert "Implemented" in table_text
    assert "res[" not in table_text

    title_blocks = [
        block for block in blocks if "Preset: requirements_matrix" in (block.get("text") or "")
    ]
    assert title_blocks, "title/control heading block was not emitted"
    assert all(block.get("type") != "header_footer_noise" for block in title_blocks)


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


def test_nist_table_suppression_removes_full_width_qid_rows_inside_table():
    elements = [
        {
            "id": "actual:p1:title:0",
            "page": 1,
            "source_type": "Body",
            "type": "unknown_region",
            "bbox": [0.0, 0.0800, 1.0, 0.0900],
            "text": "Preset: requirements_matrix (Table ID: t0)",
        },
        {
            "id": "actual:p1:block:header-row",
            "page": 1,
            "source_type": "Body",
            "type": "unknown_region",
            "bbox": [-0.10, 0.0962, 1.10, 0.1063],
            "text": "[QID_001] Control [QID_002] Requirement [QID_003] Status",
        },
        {
            "id": "actual:p1:block:ac-1-row",
            "page": 1,
            "source_type": "Body",
            "type": "unknown_region",
            "bbox": [-0.10, 0.1189, 1.10, 0.1290],
            "text": "[QID_004] AC-1 [QID_005] Access Control Policy [QID_006] Implemented",
        },
        {
            "id": "actual:p1:table:0",
            "page": 1,
            "source_type": "table",
            "type": "table",
            "bbox": [0.0, 0.0924, 1.0, 0.2969],
            "text": "[QID_001] Control | [QID_002] Requirement | [QID_003] Status",
        },
    ]

    result = _apply(elements)

    assert [element["id"] for element in result] == [
        "actual:p1:title:0",
        "actual:p1:table:0",
    ]
    assert result[0]["text"] == "Preset: requirements_matrix (Table ID: t0)"
    assert result[1]["source_type"] == "table"
    assert result[1]["type"] == "table"


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
                    {
                        "cells": [
                            {"text": "CHAPTER"},
                            {"text": "THREE"},
                            {"text": "PAGE"},
                            {"text": "130"},
                        ]
                    },
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
        "header_footer_noise",
        "paragraph_block",
        "paragraph_block",
    ]


def test_nist_short_footnote_table_false_positives_are_removed():
    elements = [
        {
            "id": "actual:p31:table:0",
            "page": 31,
            "source_type": "table",
            "type": "table",
            "bbox": [0.0, 0.7912878749346492, 1.0, 0.9543434345360958],
            "text": (
                "15 [- SP | 800-30] provides guidance on the risk assessment process.\n"
                "20 [- SP | 800-53B] provides guidance for tailoring baselines.\n"
                "CHAPT | ER NE | PAGE 4"
            ),
            "raw": {
                "row_count": 3,
                "column_count": 3,
                "rows": [
                    {"cells": [{"text": "15 [- SP"}, {"text": "800-30] provides guidance"}]},
                    {"cells": [{"text": "20 [- SP"}, {"text": "800-53B] provides guidance"}]},
                    {"cells": [{"text": "CHAPT"}, {"text": "ER NE"}, {"text": "PAGE 4"}]},
                ],
            },
        },
        {
            "id": "actual:p33:table:0",
            "page": 33,
            "source_type": "table",
            "type": "table",
            "bbox": [0.0, 0.8871212101945973, 1.0, 0.9543434345360958],
            "text": (
                "23 Unless otherwise stated, all references to NIST publications refer to "
                "the most recent version of those publications.\nPAGE 6 CHAPTER ONE"
            ),
            "raw": {
                "row_count": 2,
                "column_count": 2,
                "rows": [
                    {"cells": [{"text": "23 Unless otherwise stated, all references"}]},
                    {"cells": [{"text": "PAGE 6 CHAPTER ONE"}]},
                ],
            },
        },
    ]

    result = _apply(elements)

    assert result == []


def test_nist_short_reference_header_table_false_positive_is_removed():
    elements = [
        {
            "id": "actual:p415:table:0",
            "page": 415,
            "source_type": "table",
            "type": "table",
            "bbox": [0.0, 0.0571970066638908, 1.0, 0.11047979797979798],
            "text": (
                "__________ | __________________________________________________\n"
                "[SP 800-166] Cooper DA, Ferraiolo H, Chandramouli R"
            ),
            "raw": {
                "row_count": 2,
                "column_count": 2,
                "rows": [
                    {
                        "cells": [
                            {"text": "__________"},
                            {"text": "__________________________________________________"},
                        ]
                    },
                    {"cells": [{"text": "[SP 800-166]"}, {"text": "Cooper DA, Ferraiolo H"}]},
                ],
            },
        }
    ]

    result = _apply(elements)

    assert result == []


def test_nist_short_reference_header_table_false_positive_with_split_cfr_label_is_removed():
    elements = [
        {
            "id": "actual:p403:table:0",
            "page": 403,
            "source_type": "table",
            "type": "table",
            "bbox": [0.0, 0.0571970066638908, 1.0, 0.11047979797979798],
            "text": (
                "__________ | __________________________________________________\n"
                "[5 CFR 73 | 1] Code of Federal Regulations, Title 5"
            ),
            "raw": {
                "row_count": 2,
                "column_count": 2,
                "rows": [
                    {
                        "cells": [
                            {"text": "__________"},
                            {"text": "__________________________________________________"},
                        ]
                    },
                    {
                        "cells": [
                            {"text": "[5 CFR 73"},
                            {"text": "1] Code of Federal Regulations, Title 5"},
                        ]
                    },
                ],
            },
        }
    ]

    result = _apply(elements)

    assert result == []


def test_nist_short_table_false_positive_rule_preserves_real_qid_table():
    elements = [
        {
            "id": "actual:p1:table:0",
            "page": 1,
            "source_type": "table",
            "type": "table",
            "bbox": [0.0, 0.0924, 1.0, 0.2969],
            "text": "[QID_001] Control | [QID_002] Requirement | [QID_003] Status",
            "raw": {
                "row_count": 2,
                "column_count": 3,
                "rows": [
                    {
                        "cells": [
                            {"text": "[QID_001] Control"},
                            {"text": "[QID_002] Requirement"},
                            {"text": "[QID_003] Status"},
                        ]
                    },
                    {
                        "cells": [
                            {"text": "[QID_004] AC-1"},
                            {"text": "[QID_005] Policy"},
                            {"text": "[QID_006] Implemented"},
                        ]
                    },
                ],
            },
        }
    ]

    result = _apply(elements)

    assert [element["id"] for element in result] == ["actual:p1:table:0"]
