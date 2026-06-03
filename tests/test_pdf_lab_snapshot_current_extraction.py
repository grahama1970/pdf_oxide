from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts/pdf_lab/snapshot_current_extraction.py"
    spec = importlib.util.spec_from_file_location("snapshot_current_extraction_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_block_elements_use_trimmed_line_bbox_for_oversized_title() -> None:
    mod = _load_module()
    block = {
        "block_type": "Title",
        "text": "Information Systems and Organizations",
        "bbox": [96.0, 584.0, 990.0, 26.0],
        "font_size": 26.0,
        "font_name": "TT0",
        "is_bold": True,
    }
    text_lines = [
        {
            "text": "Information Systems and Organizations",
            "bbox": [0.157, 0.228, 0.853, 0.274],
            "raw_bbox": [96.0, 180.0, 522.0, 217.0],
        }
    ]

    elements = mod._block_elements(
        block=block,
        block_index=3,
        page_index=1,
        page_w=612.0,
        page_h=792.0,
        text_lines=text_lines,
    )

    assert len(elements) == 1
    assert elements[0]["id"] == "actual:p2:block:3"
    assert elements[0]["bbox"] == [0.157, 0.228, 0.853, 0.274]
    assert elements[0]["bbox"][2] < 1.0


def test_sparse_multiline_cover_block_splits_to_line_elements() -> None:
    mod = _load_module()
    block = {
        "block_type": "Body",
        "text": "This publication is available free of charge from: https://doi.org/10.6028/NIST.SP.800-53r5 September 2020",
        "bbox": [90.0, 321.0, 436.0, 183.0],
        "font_size": 12.0,
    }
    text_lines = [
        {"text": "This publication is available free of charge from:", "bbox": [0.535, 0.476, 0.853, 0.494]},
        {"text": "https://doi.org/10.6028/NIST.SP.800-53r5", "bbox": [0.573, 0.492, 0.853, 0.509]},
        {"text": "September 2020", "bbox": [0.719, 0.578, 0.857, 0.599]},
    ]

    elements = mod._block_elements(
        block=block,
        block_index=5,
        page_index=1,
        page_w=612.0,
        page_h=792.0,
        text_lines=text_lines,
    )

    assert [element["id"] for element in elements] == [
        "actual:p2:block:5:line:0",
        "actual:p2:block:5:line:1",
        "actual:p2:block:5:line:2",
    ]
    assert [element["text"] for element in elements] == [line["text"] for line in text_lines]
    assert all((element["bbox"][2] - element["bbox"][0]) < 0.4 for element in elements)


def test_tiny_empty_lattice_table_false_positive_is_suppressed() -> None:
    mod = _load_module()
    table = {
        "rows": 2,
        "cols": 2,
        "whitespace": 100.0,
        "data": [["", ""], ["", ""]],
    }
    metrics = {"row_count": 2, "column_count": 2}

    assert mod._is_tiny_empty_table_false_positive(table, metrics, [0.75, 0.72, 0.81, 0.73]) is True

    table_with_text = {
        "rows": 2,
        "cols": 2,
        "whitespace": 10.0,
        "data": [["A", "B"], ["C", "D"]],
    }
    assert mod._is_tiny_empty_table_false_positive(table_with_text, metrics, [0.1, 0.1, 0.4, 0.2]) is False
