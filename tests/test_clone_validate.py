"""Tests for clone validator adapters and pipeline integration."""
from __future__ import annotations

import sys
import types

from pdf_oxide.clone.clone_types import BlockType, TruthManifest, TruthObject
from pdf_oxide.clone.clone_validate import (
    pipeline_result_to_extraction_result,
    validate_pdf,
    validate_pipeline_result,
)


def _build_sample_manifest() -> TruthManifest:
    manifest = TruthManifest(
        doc_id="doc123",
        source_path="source.pdf",
        output_path="clone.pdf",
        seed=42,
    )

    paragraph_qid = "QID_1111111111111111"
    manifest.register(
        TruthObject(
            qid=paragraph_qid,
            block_type=BlockType.PARAGRAPH,
            logical_text="Paragraph 1",
            rendered_text=f"[{paragraph_qid}]Paragraph 1",
            page_num=0,
            sequence_num=0,
        )
    )

    table_qid = "QID_2222222222222222"
    manifest.register(
        TruthObject(
            qid=table_qid,
            block_type=BlockType.TABLE_CELL,
            logical_text="Cell 1",
            rendered_text=f"[{table_qid}]Cell 1",
            page_num=0,
            sequence_num=1,
            table_id="table_1",
            row=0,
            col=0,
        )
    )

    manifest.register_table_structure(
        table_id="table_1",
        rows=1,
        cols=1,
        cell_qids=[[table_qid]],
    )
    manifest.rebuild_page_qid_order()
    return manifest


def _sample_pipeline_dict() -> dict:
    return {
        "blocks": [
            {
                "text": "[QID_1111111111111111]Paragraph 1",
                "type": "paragraph",
                "id": "b0",
            }
        ],
        "tables": [
            {
                "table_id": "table_1",
                "rows": 1,
                "cols": 1,
                "cells": [["[QID_2222222222222222]Cell 1"]],
            }
        ],
    }


def test_pipeline_result_to_extraction_result_normalizes_objects_and_dicts() -> None:
    class BlockObj:
        def __init__(self) -> None:
            self.text = "[QID_3333333333333333]Object Block"
            self.type = "paragraph"
            self.id = "block_obj"

    class TableObj:
        def __init__(self) -> None:
            self.id = "table_obj"
            self.rows = 0
            self.cols = 0
            self.cells = [["Object Cell"]]

    pipeline_result = {
        "blocks": [
            {
                "text": "[QID_1111111111111111]Paragraph 1",
                "type": "paragraph",
                "id": "b0",
            },
            BlockObj(),
        ],
        "tables": [
            {
                "id": "table_dict",
                "data": [["A", "B"], ["C", "D"]],
            },
            TableObj(),
        ],
    }

    extraction_result = pipeline_result_to_extraction_result(pipeline_result)

    assert extraction_result["blocks"][0]["text"].startswith("[QID_1111")
    assert extraction_result["blocks"][1]["id"] == "block_obj"
    assert extraction_result["tables"][0]["rows"] == 2
    assert extraction_result["tables"][0]["cols"] == 2
    flat_cells = [
        cell
        for table in extraction_result["tables"]
        for row in table["cells"]
        for cell in row
    ]
    assert any("Object Cell" in cell for cell in flat_cells)
    assert "Object Block" in extraction_result["text"]


def test_validate_pipeline_result_recovers_truth_manifest() -> None:
    manifest = _build_sample_manifest()
    pipeline_result = _sample_pipeline_dict()

    result = validate_pipeline_result(manifest, pipeline_result)

    assert result.qid_recovery.is_perfect
    assert result.ordering.ordering_score == 1.0
    assert result.grid_recoveries
    assert result.grid_recoveries[0].cell_recovery == 1.0
    assert result.grid_recoveries[0].structure_match
    assert result.contamination.is_clean


def test_validate_pdf_invokes_pipeline_and_adapter(monkeypatch) -> None:
    manifest = _build_sample_manifest()

    block_obj = types.SimpleNamespace(
        text="[QID_1111111111111111]Paragraph 1",
        type="paragraph",
        id="b0",
    )
    table_obj = types.SimpleNamespace(
        id="table_1",
        rows=1,
        cols=1,
        cells=[["[QID_2222222222222222]Cell 1"]],
    )
    pipeline_output = types.SimpleNamespace(blocks=[block_obj], tables=[table_obj])

    calls: dict[str, object] = {}

    def fake_extract(pdf_path: str, config=None):
        calls["pdf_path"] = pdf_path
        calls["config"] = config
        return pipeline_output

    fake_module = types.SimpleNamespace(extract_pdf=fake_extract)
    monkeypatch.setitem(sys.modules, "pdf_oxide.pipeline", fake_module)

    result = validate_pdf("dummy.pdf", manifest=manifest)

    assert calls["pdf_path"] == "dummy.pdf"
    assert result.qid_recovery.is_perfect
    assert result.ordering.ordering_score == 1.0
    assert result.contamination.is_clean
