from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
import types
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts/pdf_lab/build_pdf_element_candidate_manifest.py"
    spec = importlib.util.spec_from_file_location("build_pdf_element_candidate_manifest_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_infer_preset_type_covers_existing_extraction_shapes() -> None:
    manifest = _load_module()

    assert manifest.infer_preset_type({"type": "table", "text": "A | B"}, 25, 400) == "table"
    assert manifest.infer_preset_type({"type": "list", "text": "• item"}, 25, 400) == "list"
    assert manifest.infer_preset_type({"type": "reference", "text": "[NIST] reference"}, 390, 400) == "reference"
    assert manifest.infer_preset_type({"type": "footnote", "bbox": [0.1, 0.8, 0.9, 0.85], "text": "1 Note"}, 25, 400) == "footnote"
    assert manifest.infer_preset_type({"type": "toc_entry", "text": "Controls ..... 42"}, 8, 400) == "toc"
    assert manifest.infer_preset_type({"type": "boilerplate", "bbox": [0.02, 0.2, 0.08, 0.9], "text": "side"}, 25, 400) == "side_chrome"
    assert manifest.infer_preset_type({"type": "section_heading", "text": "AC-1 POLICY"}, 25, 400) == "section_heading"
    assert manifest.infer_preset_type({"type": "caption", "text": "Figure 1 control flow"}, 25, 400) == "figure"
    assert manifest.infer_preset_type({"type": "section_heading", "text": "APPENDIX A"}, 390, 400) == "appendix"
    assert manifest.infer_preset_type({"type": "unknown_region", "text": "unknown"}, 25, 400) == "unknown_layout"


def test_header_footer_noise_takes_precedence_over_dash_list_syntax() -> None:
    manifest = _load_module()

    assert (
        manifest.infer_preset_type(
            {
                "type": "header_footer_noise",
                "source_type": "RotatedSideChrome",
                "bbox": [0.034, 0.703, 0.064, 0.718],
                "text": "-53r5",
            },
            15,
            492,
        )
        == "side_chrome"
    )


def test_header_footer_noise_boilerplate_uses_chrome_preset_away_from_boundary() -> None:
    manifest = _load_module()

    assert (
        manifest.infer_preset_type(
            {
                "type": "header_footer_noise",
                "source_type": "Boilerplate",
                "bbox": [0.755, 0.110, 0.853, 0.136],
                "text": "Revision 5",
            },
            2,
            492,
        )
        == "side_chrome"
    )


def test_build_manifest_from_pages_records_candidate_schema(tmp_path: Path) -> None:
    manifest = _load_module()
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    payload = manifest.build_manifest_from_pages(
        pdf_path=pdf_path,
        pages=[
            {
                "page": 1,
                "blocks": [
                    {"id": "b1", "type": "table", "bbox": [0.1, 0.2, 0.8, 0.4], "text": "A | B", "confidence": 0.9},
                    {"id": "b2", "type": "unknown_region", "bbox": [0.2, 0.5, 0.9, 0.8], "text": "mystery"},
                ],
            }
        ],
        page_count=10,
        ledger_path=None,
        apply_mode="release",
        command=["test"],
    )

    assert payload["schema"] == "pdf_lab.second_pass.candidate_manifest.v1"
    assert payload["candidate_count"] == 2
    assert payload["preset_counts"] == {"table": 1, "unknown_layout": 1}
    first = payload["candidates"][0]
    assert first["candidate_id"].startswith("cand:p0001:")
    assert first["json_pointer"] == "/pages/0/blocks/0"
    assert first["preset_type"] == "table"
    assert first["bbox"] == [0.1, 0.2, 0.8, 0.4]
    assert "hardening_interest" in first["detection_reason"]


def test_build_manifest_tolerates_malformed_bbox_values(tmp_path: Path) -> None:
    manifest = _load_module()
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    payload = manifest.build_manifest_from_pages(
        pdf_path=pdf_path,
        pages=[
            {
                "page": 1,
                "blocks": [
                    {"id": "b1", "type": "table", "bbox": ["bad", None, {}, []], "text": "A | B"},
                ],
            }
        ],
        page_count=10,
        ledger_path=None,
        apply_mode="release",
        command=["test"],
    )

    candidate = payload["candidates"][0]
    assert payload["candidate_count"] == 1
    assert candidate["bbox"] == [0.0, 0.0, 0.0, 0.0]
    assert candidate["features"]["bbox_area"] == 0.0
    assert "hardening_interest" in candidate["detection_reason"]


def test_build_manifest_tolerates_non_finite_bbox_values(tmp_path: Path) -> None:
    manifest = _load_module()
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    payload = manifest.build_manifest_from_pages(
        pdf_path=pdf_path,
        pages=[
            {
                "page": 1,
                "blocks": [
                    {"id": "b1", "type": "table", "bbox": [math.nan, 0.2, math.inf, 0.4], "text": "A | B"},
                ],
            }
        ],
        page_count=10,
        ledger_path=None,
        apply_mode="release",
        command=["test"],
    )

    candidate = payload["candidates"][0]
    assert candidate["bbox"] == [0.0, 0.0, 0.0, 0.0]
    assert candidate["features"]["bbox_area"] == 0.0


def test_non_text_presets_are_hardening_interest() -> None:
    manifest = _load_module()

    for preset_type in sorted(manifest.PRESET_TYPES - {"text"}):
        reasons = manifest.detection_reasons(
            {"type": preset_type, "bbox": [0.2, 0.2, 0.8, 0.3], "text": preset_type},
            preset_type,
            25,
            400,
        )
        assert "hardening_interest" in reasons, preset_type

    text_reasons = manifest.detection_reasons(
        {"type": "text", "bbox": [0.2, 0.2, 0.8, 0.3], "text": "plain paragraph"},
        "text",
        25,
        400,
    )
    assert "hardening_interest" not in text_reasons


def test_build_manifest_records_page_census_failures(tmp_path: Path) -> None:
    manifest = _load_module()
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    payload = manifest.build_manifest_from_pages(
        pdf_path=pdf_path,
        pages=[
            {
                "page": 2,
                "blocks": [
                    {"id": "b1", "type": "equation", "bbox": [0.2, 0.2, 0.8, 0.3], "text": "x = y + z"},
                ],
            }
        ],
        page_count=10,
        ledger_path=None,
        apply_mode="release",
        command=["test"],
        census_failures=[
            {
                "schema": "pdf_lab.second_pass.page_census_failure.v1",
                "page_number": 1,
                "page_index": 0,
                "status": "timeout",
                "error_type": "PageCensusTimeout",
                "error": "page 1 exceeded page_timeout_s=0.5",
                "page_timeout_s": 0.5,
            }
        ],
    )

    assert payload["extracted_page_count"] == 1
    assert payload["census_failure_count"] == 1
    assert payload["census_failures"][0]["status"] == "timeout"
    assert payload["candidate_count"] == 1
    assert payload["preset_counts"] == {"equation": 1}


def test_candidate_manifest_cli_writes_failure_artifact_for_census_setup_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = _load_module()
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")
    out_path = tmp_path / "candidate_manifest.json"

    def fail_extract_pages_with_failures(*args, **kwargs):
        raise RuntimeError("fitz open failed")

    monkeypatch.setattr(manifest, "extract_pages_with_failures", fail_extract_pages_with_failures)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_pdf_element_candidate_manifest.py",
            "--pdf",
            str(pdf_path),
            "--out",
            str(out_path),
            "--max-pages",
            "1",
        ],
    )

    exit_code = manifest.main()

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert payload["schema"] == "pdf_lab.second_pass.candidate_manifest.v1"
    assert payload["ok"] is False
    assert payload["candidate_count"] == 0
    assert payload["extracted_page_count"] == 0
    assert payload["census_failure_count"] == 1
    assert payload["census_failures"][0]["status"] == "substrate_error"
    assert payload["census_failures"][0]["error_type"] == "RuntimeError"
    assert "fitz open failed" in payload["errors"][0]


def test_page_census_subprocess_timeout_is_page_failure(tmp_path: Path, monkeypatch) -> None:
    manifest = _load_module()

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=kwargs.get("args", "python"), timeout=0.25)

    monkeypatch.setattr(manifest.subprocess, "run", fake_run)

    try:
        manifest._extract_one_page_with_timeout(
            snapshot=object(),
            pdf_path=tmp_path / "sample.pdf",
            page_index=0,
            ledger_path=None,
            apply_mode="release",
            page_timeout_s=0.25,
        )
    except manifest.PageCensusTimeout as exc:
        assert "page 1 exceeded page_timeout_s=0.25" in str(exc)
    else:
        raise AssertionError("expected PageCensusTimeout")


def test_extract_pages_with_failures_writes_page_progress(tmp_path: Path, monkeypatch) -> None:
    manifest = _load_module()

    class FakeDoc:
        page_count = 2

        def close(self) -> None:
            pass

    fake_fitz = types.SimpleNamespace(open=lambda _path: FakeDoc())

    def fake_extract_page(_pdf_path, page_index, _ledger_path, _apply_mode):
        if page_index == 1:
            raise RuntimeError("synthetic page failure")
        return {
            "page": page_index + 1,
            "blocks": [
                {"id": "b1", "type": "table", "bbox": [0.1, 0.2, 0.8, 0.4], "text": "A | B"},
            ],
        }

    fake_snapshot = types.SimpleNamespace(_extract_page=fake_extract_page)
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    monkeypatch.setitem(sys.modules, "snapshot_current_extraction", fake_snapshot)

    progress_path = tmp_path / "candidate_census_progress.json"
    pages, page_count, failures = manifest.extract_pages_with_failures(
        tmp_path / "sample.pdf",
        None,
        "release",
        2,
        page_timeout_s=None,
        progress_path=progress_path,
    )

    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in progress_path.with_name("candidate_census_events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert page_count == 2
    assert len(pages) == 1
    assert failures[0]["page_number"] == 2
    assert progress["schema"] == "pdf_lab.second_pass.candidate_census_progress.v1"
    assert progress["status"] == "completed"
    assert progress["completed_pages"] == 1
    assert progress["failed_pages"] == 1
    assert progress["remaining_pages"] == 0
    assert [event["event"] for event in events] == [
        "page_started",
        "page_completed",
        "page_started",
        "page_failed",
        "completed",
    ]


def test_extract_pages_with_failures_accepts_explicit_page_numbers(tmp_path: Path, monkeypatch) -> None:
    manifest = _load_module()
    seen_page_indices: list[int] = []

    class FakeDoc:
        page_count = 5

        def close(self) -> None:
            pass

    fake_fitz = types.SimpleNamespace(open=lambda _path: FakeDoc())

    def fake_extract_page(_pdf_path, page_index, _ledger_path, _apply_mode):
        seen_page_indices.append(page_index)
        return {"page": page_index + 1, "blocks": []}

    fake_snapshot = types.SimpleNamespace(_extract_page=fake_extract_page)
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)
    monkeypatch.setitem(sys.modules, "snapshot_current_extraction", fake_snapshot)

    pages, page_count, failures = manifest.extract_pages_with_failures(
        tmp_path / "sample.pdf",
        None,
        "release",
        None,
        page_timeout_s=None,
        progress_path=tmp_path / "progress.json",
        page_numbers=[5, 2, 2, 99, 0],
    )

    assert page_count == 5
    assert failures == []
    assert [page["page"] for page in pages] == [2, 5]
    assert seen_page_indices == [1, 4]
