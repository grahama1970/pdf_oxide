import json
from pathlib import Path

import pytest

from pdf_oxide import pipeline
from pdf_oxide.annotation_call import (
    ANNOTATION_CALL_SCHEMA,
    build_annotation_call,
    validate_annotation_call,
    write_annotation_call,
)
from pdf_oxide.pipeline_extract import _build_blocks
from pdf_oxide.pipeline_types import PipelineConfig, PipelineResult


SIMPLE_PDF = Path(__file__).parent / "fixtures" / "simple.pdf"


def _result(pdf_path, blocks):
    return PipelineResult(
        source_pdf=str(pdf_path),
        page_count=1,
        blocks=blocks,
    )


def test_build_blocks_propagates_engine_confidence_and_font_without_default():
    blocks = _build_blocks(
        {
            "pages": [
                {
                    "page": 0,
                    "blocks": [
                        {
                            "text": "ambiguous",
                            "block_type": "Body",
                            "bbox": [1.0, 2.0, 3.0, 4.0],
                            "font_name": "EngineFont",
                            "confidence": 0.42,
                        },
                        {
                            "text": "missing confidence",
                            "block_type": "Body",
                            "bbox": [5.0, 6.0, 7.0, 8.0],
                        },
                    ],
                }
            ]
        },
        [],
    )

    assert blocks[0]["confidence"] == 0.42
    assert blocks[0]["font_name"] == "EngineFont"
    assert blocks[1]["confidence"] is None
    assert blocks[1]["font_name"] is None


def test_threshold_boundary_and_accuracy_estimate(tmp_path):
    pdf_path = tmp_path / "fixture.pdf"
    pdf_path.write_bytes(b"%PDF fixture")
    result = _result(
        pdf_path,
        [
            {
                "id": "below",
                "page": 0,
                "bbox": [1.0, 2.0, 3.0, 4.0],
                "type": "Body",
                "text": "below threshold",
                "confidence": 0.599,
            },
            {
                "id": "boundary",
                "page": 0,
                "bbox": [5.0, 6.0, 7.0, 8.0],
                "type": "Header",
                "text": "at threshold",
                "confidence": 0.6,
            },
        ],
    )

    payload = build_annotation_call(
        result,
        engine_commit="a" * 40,
    )

    assert payload["schema"] == ANNOTATION_CALL_SCHEMA
    assert payload["engine_name"] == "pdf-oxide"
    assert payload["engine_version"]
    assert payload["accuracy_estimate"] == {
        "basis": "confidence_threshold",
        "value": 0.5,
    }
    assert len(payload["items"]) == 1
    assert payload["items"][0] == {
        "page": 0,
        "kind": "block",
        "bbox": [1.0, 2.0, 3.0, 4.0],
        "reason": "low_confidence",
        "confidence": 0.599,
        "current_type": "Body",
        "text_excerpt": "below threshold",
    }


def test_schema_validation_and_extra_item_hook_payload(tmp_path):
    pdf_path = tmp_path / "fixture.pdf"
    pdf_path.write_bytes(b"%PDF fixture")
    output_path = tmp_path / "annotation_call.json"
    result = _result(
        pdf_path,
        [
            {
                "id": "high",
                "page": 0,
                "bbox": [1.0, 2.0, 3.0, 4.0],
                "type": "Body",
                "text": "high confidence",
                "confidence": 0.9,
            }
        ],
    )
    extra_items = [
        {
            "page": 0,
            "kind": "region",
            "reason": "char_parity_deficit",
            "missing_chars": 7,
        },
        {
            "page": 0,
            "kind": "page",
            "reason": "unadjudicated_residual",
        },
    ]

    write_annotation_call(
        result,
        output_path,
        extra_items=extra_items,
        engine_commit="b" * 40,
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    validate_annotation_call(payload)
    assert payload["items"][0]["text_excerpt"] == "high confidence"
    assert payload["items"][0]["oracle_excerpt"] == ""
    assert payload["items"][0]["missing_text_derivation_error"].startswith("pdftotext_failed:")
    assert payload["items"][1] == extra_items[1]

    payload["items"][0]["reason"] = "invented_reason"
    with pytest.raises(ValueError, match="closed set"):
        validate_annotation_call(payload)


def test_char_parity_enrichment_derives_missing_text(tmp_path, monkeypatch):
    pdf_path = tmp_path / "fixture.pdf"
    pdf_path.write_bytes(b"%PDF fixture")
    result = _result(
        pdf_path,
        [
            {
                "id": "block",
                "page": 0,
                "bbox": [1.0, 2.0, 3.0, 4.0],
                "type": "Body",
                "text": "ab",
                "confidence": 0.9,
            }
        ],
    )

    class Completed:
        returncode = 0
        stdout = "a\u0014b\u0015"
        stderr = ""

    monkeypatch.setattr(
        "pdf_oxide.annotation_call.subprocess.run", lambda *args, **kwargs: Completed()
    )
    payload = build_annotation_call(
        result,
        extra_items=[
            {
                "page": 0,
                "kind": "region",
                "reason": "char_parity_deficit",
                "missing_chars": 2,
            }
        ],
        engine_commit="e" * 40,
    )

    item = payload["items"][0]
    assert item["text_excerpt"] == "ab"
    assert item["oracle_excerpt"] == "a\u0014b\u0015"
    assert item["missing_text"] == "\u0014\u0015"
    assert "missing_text_derivation_error" not in item


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("confidence", 0.6, "finite, in"),
        ("confidence", float("nan"), "finite, in"),
        ("bbox", [1.0, 2.0, 3.0], "four finite"),
        ("bbox", [1.0, 2.0, 3.0, float("inf")], "four finite"),
    ],
)
def test_schema_validation_rejects_invalid_low_confidence_items(tmp_path, field, value, message):
    pdf_path = tmp_path / "fixture.pdf"
    pdf_path.write_bytes(b"%PDF fixture")
    payload = build_annotation_call(
        _result(
            pdf_path,
            [
                {
                    "id": "below",
                    "page": 0,
                    "bbox": [1.0, 2.0, 3.0, 4.0],
                    "type": "Body",
                    "text": "below threshold",
                    "confidence": 0.5,
                }
            ],
        ),
        engine_commit="d" * 40,
    )
    payload["items"][0][field] = value

    with pytest.raises(ValueError, match=message):
        validate_annotation_call(payload)


def test_missing_engine_confidence_is_not_laundered(tmp_path):
    pdf_path = tmp_path / "fixture.pdf"
    pdf_path.write_bytes(b"%PDF fixture")
    result = _result(
        pdf_path,
        [
            {
                "id": "missing",
                "page": 0,
                "bbox": [1.0, 2.0, 3.0, 4.0],
                "type": "Body",
                "text": "missing confidence",
                "confidence": None,
            }
        ],
    )

    with pytest.raises(ValueError, match="no numeric confidence"):
        build_annotation_call(result, engine_commit="c" * 40)


def test_pipeline_writes_annotation_call_next_to_extracted_json(tmp_path, monkeypatch):
    pdf_path = SIMPLE_PDF
    result = _result(
        pdf_path,
        [
            {
                "id": "high",
                "page": 0,
                "bbox": [1.0, 2.0, 3.0, 4.0],
                "type": "Body",
                "text": "high confidence",
                "confidence": 0.9,
            }
        ],
    )
    result.timings["extraction"] = 0.0
    hook_calls = []

    monkeypatch.setattr(pipeline, "extract_content", lambda _path, _config: result)
    monkeypatch.setattr(pipeline, "flatten", lambda _result: [])
    config = PipelineConfig(
        features=[],
        sync_to_arango=False,
        output_dir=tmp_path / "output",
        annotation_call_hook=lambda hook_result: (
            hook_calls.append(hook_result)
            or [
                {
                    "page": 0,
                    "kind": "page",
                    "reason": "unadjudicated_residual",
                }
            ]
        ),
    )

    pipeline._extract_and_process(str(pdf_path), config)

    assert (config.output_dir / "extracted.json").is_file()
    annotation_path = config.output_dir / "annotation_call.json"
    payload = json.loads(annotation_path.read_text(encoding="utf-8"))
    assert len(payload["items"]) == 1
    assert payload["items"][0]["page"] == 0
    assert payload["items"][0]["kind"] == "page"
    assert payload["items"][0]["reason"] == "unadjudicated_residual"
    assert len(payload["items"][0]["page_image_refs"]) == 1
    assert set(payload["items"][0]["page_image_sha256"]) == set(
        payload["items"][0]["page_image_refs"]
    )
    assert hook_calls == [result]
