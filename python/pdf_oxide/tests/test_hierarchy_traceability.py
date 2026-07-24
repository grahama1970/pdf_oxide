"""Hierarchy and traceability contract tests."""
from __future__ import annotations

from pdf_oxide.pipeline_arango import _build_graph_records
from pdf_oxide.pipeline_extract import _build_blocks, _build_figures, _build_sections
from pdf_oxide.pipeline_flatten import flatten
from pdf_oxide.pipeline_types import PipelineConfig, PipelineResult


PDF_SHA256 = "a" * 64


def _raw_document():
    return {
        "sections": [
            {"title": "1. Introduction", "level": 2, "page": 0},
            {"title": "Abstract", "level": 1, "page": 0},
            {"title": "Paper Title", "level": 1, "page": 0},
            {"title": "3. Parent", "level": 2, "page": 1},
            {"title": "3.2. Skipped Child", "level": 4, "page": 1},
            {"title": "Unnumbered Parent", "level": 1, "page": 2},
            {"title": "Unnumbered Deep Child", "level": 4, "page": 2},
        ],
        "pages": [
            {
                "page": 0,
                "blocks": [
                    {
                        "text": "Paper Title",
                        "block_type": "Title",
                        "header_level": 1,
                        "bbox": [100, 700, 200, 20],
                    },
                    {
                        "text": "Abstract",
                        "block_type": "Title",
                        "header_level": 1,
                        "bbox": [100, 600, 80, 20],
                    },
                    {
                        "text": "abstract body content",
                        "block_type": "Body",
                        "bbox": [100, 550, 300, 20],
                    },
                    {
                        "text": "1. Introduction",
                        "block_type": "Title",
                        "header_level": 2,
                        "bbox": [100, 400, 120, 20],
                    },
                ],
            },
            {
                "page": 1,
                "blocks": [
                    {
                        "text": "3. Parent",
                        "block_type": "Title",
                        "header_level": 2,
                        "bbox": [100, 700, 100, 20],
                    },
                    {
                        "text": "3.2. Skipped Child",
                        "block_type": "Title",
                        "header_level": 4,
                        "bbox": [100, 600, 130, 20],
                    },
                    {
                        "text": "child body content",
                        "block_type": "Body",
                        "bbox": [100, 500, 200, 20],
                    },
                ],
            },
            {
                "page": 2,
                "blocks": [
                    {
                        "text": "Unnumbered Parent",
                        "block_type": "Title",
                        "header_level": 1,
                        "bbox": [100, 700, 150, 20],
                    },
                    {
                        "text": "Unnumbered Deep Child",
                        "block_type": "Title",
                        "header_level": 4,
                        "bbox": [100, 600, 180, 20],
                    },
                ],
            },
        ],
    }


def test_tree_uses_geometric_order_and_nearest_lower_level_parent():
    sections = _build_sections(_raw_document(), PDF_SHA256)

    assert [section["title"] for section in sections] == [
        "Paper Title",
        "Abstract",
        "1. Introduction",
        "3. Parent",
        "3.2. Skipped Child",
        "Unnumbered Parent",
        "Unnumbered Deep Child",
    ]
    assert [section["doc_order"] for section in sections] == list(range(7))
    parent = sections[3]
    skipped_child = sections[4]
    deep_parent = sections[5]
    deep_child = sections[6]
    assert skipped_child["parent_id"] == parent["id"]
    assert skipped_child["depth"] == parent["depth"] + 1
    assert parent["children_ids"] == [skipped_child["id"]]
    assert skipped_child["section_path"] == "3. Parent > 3.2. Skipped Child"
    assert parent["level"] == 2
    assert parent["hierarchy_level"] == 1
    assert skipped_child["level"] == 4
    assert skipped_child["hierarchy_level"] == 2
    assert deep_child["parent_id"] == deep_parent["id"]
    assert deep_parent["level"] == 1
    assert deep_child["level"] == 4
    assert sections[0]["provenance"] == {
        "pdf_sha256": PDF_SHA256,
        "page": 0,
        "bbox": [100.0, 700.0, 200.0, 20.0],
    }


def test_blocks_and_figures_get_paths_provenance_and_ordered_block_ids():
    raw = _raw_document()
    sections = _build_sections(raw, PDF_SHA256)
    blocks = _build_blocks(raw, sections, PDF_SHA256)
    figures = _build_figures(
        {
            "figures": [
                {
                    "page": 1,
                    "bbox": [100, 450, 200, 100],
                    "caption": "Figure 1. Traceable",
                }
            ]
        },
        doc=None,
        config=PipelineConfig(sync_to_arango=False),
        sections=sections,
        pdf_sha256=PDF_SHA256,
    )

    by_title = {section["title"]: section for section in sections}
    child = by_title["3.2. Skipped Child"]
    child_block_ids = [
        block["id"] for block in blocks if block["section_id"] == child["id"]
    ]
    # Production extraction calls attach_block_ids after building blocks.
    from pdf_oxide.pipeline_hierarchy import attach_block_ids

    attach_block_ids(sections, blocks)
    assert child["block_ids"] == child_block_ids
    for section in sections:
        matching_heading = next(
            (
                block
                for block in blocks
                if block["text"] == section["title"]
                and block["page"] == section["page_start"]
            ),
            None,
        )
        assert matching_heading is not None
        assert matching_heading["section_id"] == section["id"]
        assert matching_heading["id"] in section["block_ids"]
    assert figures[0]["section_id"] == child["id"]
    assert figures[0]["section_path"].endswith("3.2. Skipped Child")
    assert figures[0]["provenance"]["pdf_sha256"] == PDF_SHA256
    assert figures[0]["render_ref"] == {
        "page": 1,
        "bbox": [100, 450, 200, 100],
    }


def test_flatten_and_arango_preserve_unbroken_trace_chain():
    sections = _build_sections(_raw_document(), PDF_SHA256)
    blocks = _build_blocks(_raw_document(), sections, PDF_SHA256)
    from pdf_oxide.pipeline_hierarchy import attach_block_ids

    attach_block_ids(sections, blocks)
    child = next(
        section
        for section in sections
        if section["title"] == "3.2. Skipped Child"
    )
    result = PipelineResult(
        source_pdf="/corpus/paper.pdf",
        page_count=3,
        sections=sections,
        blocks=blocks,
        tables=[
            {
                "id": "table-id",
                "page": 1,
                "bbox": [10, 100, 200, 300],
                "text": "a,b\n1,2",
                "html_data": "<table><tr><td>a</td></tr></table>",
                "section_id": child["id"],
                "section_path": child["section_path"],
                "provenance": {
                    "pdf_sha256": PDF_SHA256,
                    "page": 1,
                    "bbox": [10, 100, 200, 300],
                },
                "render_ref": {
                    "page": 1,
                    "bbox": [10, 100, 200, 300],
                },
            }
        ],
        metadata={"pdf_sha256": PDF_SHA256},
    )
    chunks = flatten(result)
    table_chunk = next(
        chunk for chunk in chunks if chunk["asset_type"] == "Table"
    )
    assert table_chunk["element_refs"] == ["table-id"]
    assert table_chunk["section_path"].endswith("3.2. Skipped Child")
    assert table_chunk["provenance"]["pdf_sha256"] == PDF_SHA256
    assert table_chunk["render_ref"]["bbox"] == [10, 100, 200, 300]

    graph = _build_graph_records(result, chunks)
    edge_types = {edge["type"] for edge in graph["edges"]}
    assert {"has_child", "in_section", "represents_element"} <= edge_types
    table_node = next(
        element for element in graph["elements"] if element["id"] == "table-id"
    )
    assert table_node["text"] == "a,b\n1,2"
    assert table_node["section_path"] == table_chunk["section_path"]
    assert table_node["provenance"] == table_chunk["provenance"]
