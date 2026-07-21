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


def test_block_elements_choose_duplicate_text_line_by_block_geometry() -> None:
    mod = _load_module()
    block = {
        "block_type": "Title",
        "text": "CHAPTER ONE",
        "bbox": [90.0, 704.76, 80.24, 16.02],
        "font_size": 14.52,
        "font_name": "TT0",
        "is_bold": True,
    }
    text_lines = [
        {
            "text": "CHAPTER ONE",
            "bbox": [0.147, 0.941, 0.223, 0.955],
            "raw_bbox": [90.0, 38.16, 136.0, 47.16],
        },
        {
            "text": "CHAPTER ONE",
            "bbox": [0.147, 0.089, 0.223, 0.110],
            "raw_bbox": [90.0, 704.76, 170.24, 720.78],
        },
    ]

    elements = mod._block_elements(
        block=block,
        block_index=6,
        page_index=27,
        page_w=612.0,
        page_h=792.0,
        text_lines=text_lines,
    )

    assert len(elements) == 1
    assert elements[0]["id"] == "actual:p28:block:6"
    assert elements[0]["bbox"] == [0.147, 0.089, 0.223, 0.110]


def test_block_elements_preserve_rust_footnote_type_for_candidate_inference() -> None:
    mod = _load_module()
    block = {
        "block_type": "Footnote",
        "text": "maintenance, use, sharing, dissemination, or disposition of information [OMB A-130].",
        "bbox": [90.0, 196.26, 314.57, 9.0],
        "font_size": 9.0,
        "font_name": "TT0",
        "is_bold": False,
    }

    elements = mod._block_elements(
        block=block,
        block_index=14,
        page_index=27,
        page_w=612.0,
        page_h=792.0,
        text_lines=[],
    )

    assert len(elements) == 1
    assert elements[0]["source_type"] == "Footnote"
    assert elements[0]["type"] == "footnote"


def test_block_elements_use_overlapping_footnote_lines_when_rust_text_corrupts_citation() -> None:
    mod = _load_module()
    block = {
        "block_type": "Footnote",
        "text": (
            "operating as intended, and producing the desired outcome with respect to meeting the designated "
            "security and privacy requirements [-SP 80053A]."
        ),
        "bbox": [90.0, 74.4, 410.306, 19.98],
        "font_size": 9.0,
        "font_name": "TT0",
        "is_bold": False,
    }
    text_lines = [
        {
            "text": "operating as intended, and producing the desired outcome with respect to meeting the designated security and",
            "bbox": [0.147, 0.881, 0.814, 0.896],
            "raw_bbox": [90.0, 697.39, 498.25, 709.43],
        },
        {
            "text": "privacy requirements [SP 800-53A].",
            "bbox": [0.147, 0.894, 0.358, 0.910],
            "raw_bbox": [90.0, 708.37, 219.29, 720.41],
        },
    ]

    elements = mod._block_elements(
        block=block,
        block_index=23,
        page_index=27,
        page_w=612.0,
        page_h=792.0,
        text_lines=text_lines,
    )

    assert len(elements) == 1
    assert "[SP 800-53A]" in elements[0]["text"]
    assert "[-SP 80053A]" not in elements[0]["text"]
    assert elements[0]["type"] == "footnote"


def test_block_elements_remove_space_inside_wrapped_bracketed_citation() -> None:
    mod = _load_module()
    block = {
        "block_type": "Body",
        "text": "For example, [OMB A- 130] imposes information security and privacy requirements.",
        "bbox": [90.0, 341.03, 430.99, 172.08],
        "font_size": 10.98,
        "font_name": "TT0",
        "is_bold": False,
    }

    elements = mod._block_elements(
        block=block,
        block_index=11,
        page_index=33,
        page_w=612.0,
        page_h=792.0,
        text_lines=[],
    )

    assert len(elements) == 1
    assert "[OMB A-130]" in elements[0]["text"]
    assert "[OMB A- 130]" not in elements[0]["text"]


def test_merge_footnote_continuation_lines_into_numbered_note() -> None:
    mod = _load_module()
    elements = [
        {
            "id": "actual:p31:block:15",
            "page": 31,
            "type": "footnote",
            "bbox": [0.147, 0.873, 0.835, 0.888],
            "text": "20 [SP 800-53B] provides guidance for tailoring baselines and overlays to",
            "raw": {},
        },
        {
            "id": "actual:p31:block:16",
            "page": 31,
            "type": "footnote",
            "bbox": [0.147, 0.887, 0.721, 0.902],
            "text": "support the specific protection needs and requirements of stakeholders and their organizations.",
            "raw": {},
        },
    ]

    merged = mod._merge_footnote_continuations(elements)

    assert len(merged) == 1
    assert "support the specific protection needs" in merged[0]["text"]
    assert merged[0]["bbox"] == [0.147, 0.873, 0.835, 0.902]
    assert merged[0]["raw"]["continuation_ids"] == ["actual:p31:block:16"]


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


def test_nist_page45_snapshot_adds_toc_lineage() -> None:
    mod = _load_module()
    blocks = [
        {
            "id": "actual:p45:block:7",
            "page": 45,
            "type": "section_link",
            "text": "Quick link to Access Control Summary Table",
        }
    ]

    enriched = mod._add_toc_lineage(blocks, Path("NIST_SP_800-53r5.pdf"), 45)

    assert enriched[0]["breadcrumb"] == [
        "CHAPTER THREE THE CONTROLS",
        "3.1 ACCESS CONTROL",
    ]
    assert enriched[0]["toc_path"] == ["toc:0014", "toc:0015"]
    assert [node["id"] for node in enriched[0]["toc_lineage"]] == ["toc:0014", "toc:0015"]
    assert enriched[0]["breadcrumb_nodes"][1]["label"] == "3.1 ACCESS CONTROL"


def test_toc_lineage_helper_ignores_unmapped_pdf_page() -> None:
    mod = _load_module()
    blocks = [{"id": "actual:p1:block:1", "page": 1, "type": "paragraph_block"}]

    assert mod._add_toc_lineage(blocks, Path("other.pdf"), 45) == blocks
    assert mod._add_toc_lineage(blocks, Path("NIST_SP_800-53r5.pdf"), 44) == blocks


def test_rotated_side_chrome_suppresses_duplicate_margin_fragments() -> None:
    mod = _load_module()
    elements = [
        {
            "id": "actual:p45:rotated_side_chrome:1",
            "page": 45,
            "source_type": "RotatedSideChrome",
            "type": "header_footer_noise",
            "text": "This publication is available free of charge from: https://doi.org/10.6028/NIST.SP.800-53r5",
            "bbox": [0.030, 0.288, 0.050, 0.741],
        },
        {
            "id": "actual:p45:block:3",
            "page": 45,
            "source_type": "Body",
            "type": "unknown_region",
            "text": "This publication is available free of charge from:",
            "bbox": [0.034, 0.276, 0.350, 0.288],
        },
        {
            "id": "actual:p45:block:4",
            "page": 45,
            "source_type": "Body",
            "type": "unknown_region",
            "text": "https://doi.org/10.6028/NIST.SP.800",
            "bbox": [0.034, 0.520, 0.271, 0.532],
        },
        {
            "id": "actual:p45:block:body",
            "page": 45,
            "source_type": "Body",
            "type": "paragraph_block",
            "text": "Access control policy body text.",
            "bbox": [0.206, 0.300, 0.700, 0.320],
        },
    ]

    result = mod._suppress_rotated_side_chrome_duplicates(elements)

    assert [element["id"] for element in result] == [
        "actual:p45:rotated_side_chrome:1",
        "actual:p45:block:body",
    ]


def test_table_geometry_metadata_preserves_off_page_extent() -> None:
    mod = _load_module()

    metadata = mod._table_geometry_metadata(
        [-92.808, 73.2, 704.808, 235.2],
        [0.0, 0.092424, 1.0, 0.29697],
        612.0,
        792.0,
    )

    assert metadata["visible_bbox"] == [0.0, 0.092424, 1.0, 0.29697]
    assert metadata["full_normalized_bbox"][0] < 0.0
    assert metadata["full_normalized_bbox"][2] > 1.0
    assert metadata["bbox_clipped_to_page"] is True
    assert metadata["off_page_extent"]["left"] > 0.0
    assert metadata["off_page_extent"]["right"] > 0.0


def test_rotated_margin_line_fragments_consolidate_to_side_chrome() -> None:
    mod = _load_module()
    text_lines = [
        {
            "text": "This publication is available free of charge from: https://doi.org/10.6028/NIST.SP.800-53r5",
            "bbox": [0.029, 0.288, 0.050, 0.742],
            "raw_bbox": [18.1, 227.9, 30.4, 589.0],
            "dir": [0.0, 1.0],
            "font_name": "ArialMT",
            "font_size": 9.0,
            "is_bold": False,
        }
    ]
    raw_elements = [
        {
            "id": "actual:p15:block:3",
            "page": 15,
            "pdf_page_index": 14,
            "type": "unknown_region",
            "source_type": "Body",
            "bbox": [0.034, 0.276, 0.350, 0.288],
            "text": "This publication is available free of charge from:",
        },
        {
            "id": "actual:p15:block:4",
            "page": 15,
            "pdf_page_index": 14,
            "type": "unknown_region",
            "source_type": "Body",
            "bbox": [0.034, 0.520, 0.271, 0.532],
            "text": "https://doi.org/10.6028/NIST.SP.800",
        },
        {
            "id": "actual:p15:block:5",
            "page": 15,
            "pdf_page_index": 14,
            "type": "unknown_region",
            "source_type": "List",
            "bbox": [0.034, 0.703, 0.064, 0.718],
            "text": "-53r5",
        },
    ]

    elements = mod._consolidate_rotated_side_chrome_fragments(raw_elements, text_lines, page_index=14)

    assert len(elements) == 1
    assert elements[0]["id"] == "actual:p15:rotated_side_chrome:1"
    assert elements[0]["type"] == "header_footer_noise"
    assert elements[0]["source_type"] == "RotatedSideChrome"
    assert elements[0]["bbox"] == [0.029, 0.288, 0.050, 0.742]
    assert elements[0]["text"].endswith("NIST.SP.800-53r5")
    assert elements[0]["raw"]["fragment_ids"] == [
        "actual:p15:block:3",
        "actual:p15:block:4",
        "actual:p15:block:5",
    ]
