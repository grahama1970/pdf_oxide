from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts/pdf_lab/run_page_second_pass_dag.py"
    spec = importlib.util.spec_from_file_location("run_page_second_pass_dag_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _selected_candidates_payload(
    candidate_ids: list[str],
    *,
    case_id: str = "page_case_0001_p0001",
    page_number: int = 1,
    candidate_count: int | None = None,
) -> dict:
    candidates = [{"candidate_id": candidate_id} for candidate_id in candidate_ids]
    return {
        "schema": "pdf_lab.second_pass.selected_candidates.v1",
        "page_case": {"case_id": case_id, "page_number": page_number},
        "candidate_count": len(candidates) if candidate_count is None else candidate_count,
        "candidates": candidates,
    }


def _candidate_presets_payload(
    candidate_ids: list[str],
    *,
    case_id: str = "page_case_0001_p0001",
    page_number: int = 1,
    candidate_count: int | None = None,
) -> dict:
    candidates = [
        {
            "candidate_id": candidate_id,
            "preset_type": "table",
            "bbox": [0.1, 0.2, 0.8, 0.4],
            "features": {},
            "question": "Does the rendered page evidence agree with this extracted candidate?",
            "allowed_review_statuses": ["clean", "defect", "unsure", "substrate_blocked"],
        }
        for candidate_id in candidate_ids
    ]
    return {
        "schema": "pdf_lab.second_pass.candidate_presets.v1",
        "page_case": {"case_id": case_id, "page_number": page_number},
        "candidate_count": len(candidates) if candidate_count is None else candidate_count,
        "candidates": candidates,
    }


def _review_validation_payload(
    candidate_ids: list[str],
    *,
    case_id: str = "page_case_0001_p0001",
    page_number: int = 1,
    ok: bool = False,
    errors: list[str] | None = None,
    seen_candidate_ids: list[str] | None = None,
    candidate_count: int | None = None,
) -> dict:
    return {
        "schema": "pdf_lab.second_pass.review_validation.v1",
        "ok": ok,
        "errors": ["dry_run_review_not_executed"] if errors is None else errors,
        "page_case": {"case_id": case_id, "page_number": page_number},
        "candidate_count": len(candidate_ids) if candidate_count is None else candidate_count,
        "expected_candidate_ids": candidate_ids,
        "seen_candidate_ids": [] if seen_candidate_ids is None else seen_candidate_ids,
    }


def _write_full_patched_confirmed_artifacts(case_dir: Path, commit_sha: str = "abc123") -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    for image_name in ["page_after.png", "page_after_candidates.png"]:
        (case_dir / image_name).write_bytes(b"png")
    (case_dir / "page_after.json").write_text(json.dumps({"page": 1, "blocks": []}), encoding="utf-8")
    (case_dir / "patch_delta.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.patch_delta.v1",
                "ok": True,
                "patch_changed_files": ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "patch_scope_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.patch_scope_validation.v1",
                "ok": True,
                "changed_files": ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
                "test_files": ["tests/test_fix.py"],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "test_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.test_validation.v1",
                "ok": True,
                "errors": [],
                "results": [],
                "required_test_files": ["tests/test_fix.py"],
                "covered_test_files": ["tests/test_fix.py"],
                "missing_test_file_coverage": [],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "selected_candidates.json").write_text(
        json.dumps(_selected_candidates_payload(["cand:p0001:0000:table"])),
        encoding="utf-8",
    )
    (case_dir / "review_after_request.json").write_text(json.dumps({"messages": []}), encoding="utf-8")
    (case_dir / "review_after_request_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_request_validation.v1",
                "ok": True,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "review_after_response.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0001:0000:table",
                        "status": "clean",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "review_after_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": True,
                "errors": [],
                "expected_candidate_ids": ["cand:p0001:0000:table"],
                "seen_candidate_ids": ["cand:p0001:0000:table"],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "commit_acceptance_gate.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.commit_acceptance_gate.v1",
                "ok": True,
                "commit_sha": commit_sha,
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "commit_gate.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.commit_gate.v1",
                "ok": True,
                "commit_sha": commit_sha,
                "changed_files": ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
                "committed_files": ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
                "exact_file_match": True,
                "revertability_check": {
                    "schema": "pdf_lab.second_pass.revertability_check.v1",
                    "ok": True,
                    "commit_sha": commit_sha,
                },
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "revertability_check.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.revertability_check.v1",
                "ok": True,
                "commit_sha": commit_sha,
            }
        ),
        encoding="utf-8",
    )


def test_run_page_case_dry_run_writes_self_contained_artifacts(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "table",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "A | B",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:table",
                "page_number": 3,
                "preset_type": "table",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "table"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:table"],
                "strata": ["preset:table"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-a",
    )

    case_dir = Path(result["case_dir"])
    assert result["terminal_status"] == "still_open"
    assert (case_dir / "state.json").is_file()
    assert (case_dir / "page_before.json").is_file()
    assert (case_dir / "page_before.png").is_file()
    assert (case_dir / "page_candidates.png").is_file()
    assert (case_dir / "candidate_presets.json").is_file()
    assert (case_dir / "review_request.json").is_file()
    assert (case_dir / "review_request_validation.json").is_file()
    assert (case_dir / "scillm_orchestrator_page_dag_spec.json").is_file()
    assert (case_dir / "scillm_orchestrator_page_dag_spec_validation.json").is_file()
    assert (case_dir / "scillm_orchestrator_page_submission.json").is_file()
    assert (case_dir / "scillm_orchestrator_page_submission_validation.json").is_file()
    assert (case_dir / "review.html").is_file()
    assert (case_dir / "terminal_ledger.json").is_file()
    assert (case_dir / "review_bundle_validation.json").is_file()
    assert (case_dir / "review_bundle.zip").is_file()

    sampled_manifest = json.loads((case_dir / "sampled_candidate_manifest.json").read_text(encoding="utf-8"))
    assert sampled_manifest["schema"] == "pdf_lab.second_pass.sampled_candidate_manifest.v1"
    assert sampled_manifest["candidate_count"] == 1
    assert sampled_manifest["page_case"]["candidate_ids"] == ["cand:p0003:0000:table"]
    assert [candidate["candidate_id"] for candidate in sampled_manifest["candidates"]] == ["cand:p0003:0000:table"]
    selected_candidates = json.loads((case_dir / "selected_candidates.json").read_text(encoding="utf-8"))
    assert selected_candidates["schema"] == "pdf_lab.second_pass.selected_candidates.v1"
    assert selected_candidates["page_case"] == {"case_id": "page_case_0001_p0003", "page_number": 3}
    assert selected_candidates["candidate_count"] == 1
    assert [candidate["candidate_id"] for candidate in selected_candidates["candidates"]] == ["cand:p0003:0000:table"]
    candidate_presets = json.loads((case_dir / "candidate_presets.json").read_text(encoding="utf-8"))
    assert candidate_presets["schema"] == "pdf_lab.second_pass.candidate_presets.v1"
    assert candidate_presets["page_case"] == {"case_id": "page_case_0001_p0003", "page_number": 3}
    assert candidate_presets["candidate_count"] == 1
    assert [candidate["candidate_id"] for candidate in candidate_presets["candidates"]] == ["cand:p0003:0000:table"]
    request = json.loads((case_dir / "review_request.json").read_text(encoding="utf-8"))
    assert request["endpoint"] == "POST /v1/chat/completions"
    assert request["scillm_metadata"] == {"batch_id": "batch-a", "item_id": "page_case_0001_p0003"}
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    assert ledger["terminal_status"] == "still_open"
    assert ledger["reason"] == "dry_run_review_not_executed"
    assert "scillm_orchestrator_page_dag_spec.json" in ledger["evidence_artifacts"]
    assert "scillm_orchestrator_page_dag_spec_validation.json" in ledger["evidence_artifacts"]
    assert "scillm_orchestrator_page_submission.json" in ledger["evidence_artifacts"]
    assert "scillm_orchestrator_page_submission_validation.json" in ledger["evidence_artifacts"]
    assert "review.html" in ledger["evidence_artifacts"]
    assert "review_request_validation.json" in ledger["evidence_artifacts"]
    request_validation = json.loads((case_dir / "review_request_validation.json").read_text(encoding="utf-8"))
    assert request_validation["schema"] == "pdf_lab.second_pass.review_request_validation.v1"
    assert request_validation["ok"] is True
    assert request_validation["image_part_count"] == 2
    assert request_validation["text_part_count"] == 1
    dag_spec = json.loads((case_dir / "scillm_orchestrator_page_dag_spec.json").read_text(encoding="utf-8"))
    dag_spec_validation = json.loads((case_dir / "scillm_orchestrator_page_dag_spec_validation.json").read_text(encoding="utf-8"))
    submission = json.loads((case_dir / "scillm_orchestrator_page_submission.json").read_text(encoding="utf-8"))
    submission_validation = json.loads((case_dir / "scillm_orchestrator_page_submission_validation.json").read_text(encoding="utf-8"))
    assert dag_spec["target_dag_state_owner"] == "scillm_orchestrator"
    assert all(node["state_owner"] == "scillm_orchestrator" for node in dag_spec["nodes"])
    assert dag_spec["current_planner_role"] == "pdf_lab_project_agent_final_reviewer_only"
    assert dag_spec_validation["ok"] is True
    node_ids = {node["node_id"] for node in dag_spec["nodes"]}
    assert "reextract_page_after_patch" in node_ids
    assert "deterministic_page_closure_gate" in node_ids
    terminal_requires = set(dag_spec["terminal_requirements"]["patched_confirmed_requires"])
    assert "patch_scope_validation.json" in terminal_requires
    assert "test_validation.json" in terminal_requires
    assert "review_after_response.json" in terminal_requires
    assert "review_after_request_validation.json" in terminal_requires
    assert "commit_acceptance_gate.json" in terminal_requires
    assert "commit_sha" in terminal_requires
    assert submission["target_dag_state_owner"] == "scillm_orchestrator"
    assert submission["transport_create_body"]["dag_node_id"] == "pdf_lab_second_pass_page:page_case_0001_p0003"
    context = submission["transport_create_body"]["orchestrator_context"]
    assert context["schema"] == "pdf_lab.second_pass.scillm_orchestrator_context.v1"
    assert context["dag_spec_sha256"] == submission["dag_spec_sha256"]
    assert context["target_dag_state_owner"] == "scillm_orchestrator"
    assert context["required_terminal_gate"] == "deterministic_pdf_lab_acceptance_before_commit"
    assert submission_validation["ok"] is True
    assert submission_validation["dag_spec_sha256"] == submission["dag_spec_sha256"]
    assert submission_validation["case_id"] == submission["case_id"]
    assert submission_validation["page_number"] == submission["page_number"]
    assert any(node["node_id"] == "scillm_one_shot_page_review" and node["endpoint"] == "POST /v1/chat/completions" for node in dag_spec["nodes"])
    html = (case_dir / "review.html").read_text(encoding="utf-8")
    assert "PDF Lab Second-Pass Page Review" in html
    assert "cand:p0003:0000:table" in html
    with zipfile.ZipFile(case_dir / "review_bundle.zip") as bundle:
        names = set(bundle.namelist())
        assert "review.html" in names
        assert "review_request_validation.json" in names
        assert "scillm_orchestrator_page_dag_spec.json" in names
        assert "scillm_orchestrator_page_submission.json" in names
    assert (case_dir / "review_validation.json").is_file()
    review_validation = json.loads((case_dir / "review_validation.json").read_text(encoding="utf-8"))
    assert review_validation["page_case"] == {"case_id": "page_case_0001_p0003", "page_number": 3}
    assert review_validation["candidate_count"] == 1
    assert review_validation["expected_candidate_ids"] == ["cand:p0003:0000:table"]
    bundle_validation = json.loads((case_dir / "review_bundle_validation.json").read_text(encoding="utf-8"))
    assert bundle_validation["schema"] == "pdf_lab.second_pass.page_review_bundle_validation.v1"
    assert bundle_validation["ok"] is True
    assert bundle_validation["zip_content_ok"] is True
    assert bundle_validation["missing_expected_zip_entries"] == []
    assert "terminal_ledger.json" in bundle_validation["required_zip_entries"]
    assert "terminal_ledger_validation.json" in bundle_validation["required_zip_entries"]
    assert "review.html" in bundle_validation["required_zip_entries"]
    assert "review_request_validation.json" in bundle_validation["required_zip_entries"]
    assert (case_dir / "receipts/initialize_page_case.json").is_file()
    assert (case_dir / "receipts/render_page_review_artifact.json").is_file()
    package_receipt = json.loads((case_dir / "receipts/package_page_review_bundle.json").read_text(encoding="utf-8"))
    assert package_receipt["validator_result"]["ok"] is True
    assert "review_bundle_validation.json" in package_receipt["output_artifacts"]


def test_validate_page_orchestrator_dag_spec_rejects_stale_patched_confirmed_contract(tmp_path: Path) -> None:
    dag = _load_module()
    spec = dag.build_page_orchestrator_dag_spec(
        page_case={
            "case_id": "page_case_0001_p0001",
            "page_number": 1,
            "page_index": 0,
            "candidate_ids": ["cand:p0001:0000:table"],
        },
        candidates=[
            {
                "candidate_id": "cand:p0001:0000:table",
                "page_number": 1,
                "preset_type": "table",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {},
            }
        ],
        review_request_artifact="review_request.json",
        patch_backend="scillm_orchestrator",
        patch_mode="dry_run",
        review_mode="dry_run",
        repair_strategy="single",
        opencode_agent="build",
        opencode_agent_sequence=None,
        opencode_model=None,
        code_root=tmp_path,
        caller_skill="pdf-lab",
        page_extract_timeout_s=30.0,
        status="ready",
    )
    spec["terminal_requirements"]["patched_confirmed_requires"] = [
        "commit_gate.json",
        "revertability_check.json",
        "commit_sha",
    ]

    validation = dag.validate_page_orchestrator_dag_spec(spec)

    assert validation["ok"] is False
    assert (
        "terminal_requirements.patched_confirmed_requires does not match patched-confirmed evidence contract"
        in validation["errors"]
    )


def test_validate_page_orchestrator_submission_rejects_stale_identity_metadata(tmp_path: Path) -> None:
    dag = _load_module()
    page_case = {
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "page_index": 0,
        "candidate_ids": ["cand:p0001:0000:table"],
    }
    spec = dag.build_page_orchestrator_dag_spec(
        page_case=page_case,
        candidates=[
            {
                "candidate_id": "cand:p0001:0000:table",
                "page_number": 1,
                "preset_type": "table",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {},
            }
        ],
        review_request_artifact="review_request.json",
        patch_backend="scillm_orchestrator",
        patch_mode="dry_run",
        review_mode="dry_run",
        repair_strategy="single",
        opencode_agent="build",
        opencode_agent_sequence=None,
        opencode_model=None,
        code_root=tmp_path,
        caller_skill="pdf-lab",
        page_extract_timeout_s=30.0,
        status="ready",
    )
    submission = dag.build_page_orchestrator_submission(
        case_dir=tmp_path / "page_case_0001_p0001",
        page_case=page_case,
        dag_spec=spec,
        dag_spec_artifact="scillm_orchestrator_page_dag_spec.json",
        code_root=tmp_path,
        timeout_s=60.0,
    )
    stale = json.loads(json.dumps(submission))
    stale["scillm_metadata"]["case_id"] = "page_case_9999_p9999"
    stale["transport_create_body"]["orchestrator_context"]["page_number"] = 9999

    validation = dag.validate_page_orchestrator_submission(stale, dag_spec=spec)

    assert validation["ok"] is False
    assert "submission scillm_metadata case_id does not match submission" in validation["errors"]
    assert "orchestrator_context page_number does not match submission" in validation["errors"]


def test_validate_page_orchestrator_run_receipt_rejects_stale_request_identity(tmp_path: Path) -> None:
    dag = _load_module()
    page_case = {
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "page_index": 0,
        "candidate_ids": ["cand:p0001:0000:table"],
    }
    spec = dag.build_page_orchestrator_dag_spec(
        page_case=page_case,
        candidates=[
            {
                "candidate_id": "cand:p0001:0000:table",
                "page_number": 1,
                "preset_type": "table",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {},
            }
        ],
        review_request_artifact="review_request.json",
        patch_backend="scillm_orchestrator",
        patch_mode="dry_run",
        review_mode="dry_run",
        repair_strategy="single",
        opencode_agent="build",
        opencode_agent_sequence=None,
        opencode_model=None,
        code_root=tmp_path,
        caller_skill="pdf-lab",
        page_extract_timeout_s=30.0,
        status="ready",
    )
    submission = dag.build_page_orchestrator_submission(
        case_dir=tmp_path / "page_case_0001_p0001",
        page_case=page_case,
        dag_spec=spec,
        dag_spec_artifact="scillm_orchestrator_page_dag_spec.json",
        code_root=tmp_path,
        timeout_s=60.0,
    )
    request = dag.build_page_orchestrator_run_request(
        case_dir=tmp_path / "page_case_0001_p0001",
        page_case=page_case,
        submission=submission,
        dag_spec_artifact="scillm_orchestrator_page_dag_spec.json",
        code_root=tmp_path,
        timeout_s=60.0,
    )
    receipt = {
        "schema": "pdf_lab.second_pass.page_orchestrator_run_receipt.v1",
        "endpoint": "POST /v1/scillm/opencode/transport/runs",
        "http_status": 200,
        "request_metadata": {
            "graph_node": "scillm_orchestrator_page_dag",
            "case_id": "page_case_9999_p9999",
            "page_number": 9999,
            "dag_spec_sha256": "stale-dag",
        },
        "transport_run_id": "tr-page-0001",
        "create_response": {"transport_run_id": "tr-stale-page"},
        "observation": {"schema": "scillm.opencode_transport.observation.v1"},
    }

    validation = dag.validate_page_orchestrator_run_receipt(receipt, mode="live", request=request)

    assert validation["ok"] is False
    assert "page orchestrator run receipt request_metadata case_id does not match request" in validation["errors"]
    assert "page orchestrator run receipt request_metadata page_number does not match request" in validation["errors"]
    assert "page orchestrator run receipt request_metadata dag_spec_sha256 does not match request" in validation["errors"]
    assert "page orchestrator create_response transport_run_id does not match receipt" in validation["errors"]


def test_validate_page_orchestrator_run_receipt_rejects_wrong_surface_and_status(tmp_path: Path) -> None:
    dag = _load_module()
    page_case = {
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "page_index": 0,
        "candidate_ids": ["cand:p0001:0000:table"],
    }
    spec = dag.build_page_orchestrator_dag_spec(
        page_case=page_case,
        candidates=[
            {
                "candidate_id": "cand:p0001:0000:table",
                "page_number": 1,
                "preset_type": "table",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {},
            }
        ],
        review_request_artifact="review_request.json",
        patch_backend="scillm_orchestrator",
        patch_mode="dry_run",
        review_mode="dry_run",
        repair_strategy="single",
        opencode_agent="build",
        opencode_agent_sequence=None,
        opencode_model=None,
        code_root=tmp_path,
        caller_skill="pdf-lab",
        page_extract_timeout_s=30.0,
        status="ready",
    )
    submission = dag.build_page_orchestrator_submission(
        case_dir=tmp_path / "page_case_0001_p0001",
        page_case=page_case,
        dag_spec=spec,
        dag_spec_artifact="scillm_orchestrator_page_dag_spec.json",
        code_root=tmp_path,
        timeout_s=60.0,
    )
    request = dag.build_page_orchestrator_run_request(
        case_dir=tmp_path / "page_case_0001_p0001",
        page_case=page_case,
        submission=submission,
        dag_spec_artifact="scillm_orchestrator_page_dag_spec.json",
        code_root=tmp_path,
        timeout_s=60.0,
    )
    receipt = {
        "schema": "pdf_lab.second_pass.page_orchestrator_run_receipt.v1",
        "endpoint": "POST /v1/chat/completions",
        "http_status": 202,
        "request_metadata": request["scillm_metadata"],
        "transport_run_id": "tr-page-0001",
        "create_response": {"transport_run_id": "tr-page-0001"},
        "observation": {"schema": "scillm.opencode_transport.observation.v1"},
    }

    validation = dag.validate_page_orchestrator_run_receipt(receipt, mode="live", request=request)

    assert validation["ok"] is False
    assert "page orchestrator run receipt endpoint mismatch" in validation["errors"]
    assert "page orchestrator run receipt http_status must be 200" in validation["errors"]


def test_extract_page_for_code_root_subprocess_timeout_is_page_timeout(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=kwargs.get("args") or "extract", timeout=kwargs["timeout"])

    monkeypatch.setattr(dag.subprocess, "run", fake_run)

    try:
        dag.extract_page_for_code_root(
            tmp_path / "fake.pdf",
            7,
            None,
            "release",
            dag.REPO,
            page_extract_timeout_s=0.25,
        )
    except dag.PageExtractionTimeout as exc:
        assert "page 7 extraction exceeded page_extract_timeout_s=0.25" in str(exc)
    else:
        raise AssertionError("expected PageExtractionTimeout")


def test_extract_page_for_code_root_subprocess_disables_bytecode_writes(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()
    captured_env = {}

    def fake_run(args, **kwargs):
        captured_env.update(kwargs["env"])
        out_path = Path(args[-1])
        out_path.write_text(json.dumps({"page_number": 2}), encoding="utf-8")
        return subprocess.CompletedProcess(args=args, returncode=0)

    monkeypatch.setattr(dag.subprocess, "run", fake_run)

    page = dag.extract_page_for_code_root(
        tmp_path / "fake.pdf",
        2,
        None,
        "release",
        dag.REPO,
        page_extract_timeout_s=0.25,
    )

    assert page == {"page_number": 2}
    assert captured_env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_run_page_case_page_extraction_failure_fails_closed_with_bundle(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page_for_code_root(*args, **kwargs):
        raise dag.PageExtractionTimeout("page 3 extraction exceeded page_extract_timeout_s=0.1")

    def fail_render(*args, **kwargs):
        raise AssertionError("rendering should not run after extraction failure")

    monkeypatch.setattr(dag, "extract_page_for_code_root", fake_extract_page_for_code_root)
    monkeypatch.setattr(dag, "render_original_page", fail_render)
    monkeypatch.setattr(dag, "render_candidate_overlay", fail_render)

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:table",
                "page_number": 3,
                "preset_type": "table",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "table"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:table"],
                "strata": ["preset:table"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-a",
        page_extract_timeout_s=0.1,
    )

    case_dir = Path(result["case_dir"])
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    assert result["terminal_status"] == "blocked_substrate"
    assert ledger["reason"] == "page_extraction_failed"
    assert "page_extraction_error.json" in ledger["evidence_artifacts"]
    assert "scillm_orchestrator_page_dag_spec.json" in ledger["evidence_artifacts"]
    assert "scillm_orchestrator_page_dag_spec_validation.json" in ledger["evidence_artifacts"]
    assert "review.html" in ledger["evidence_artifacts"]
    assert (case_dir / "page_extraction_error.json").is_file()
    dag_spec = json.loads((case_dir / "scillm_orchestrator_page_dag_spec.json").read_text(encoding="utf-8"))
    dag_spec_validation = json.loads((case_dir / "scillm_orchestrator_page_dag_spec_validation.json").read_text(encoding="utf-8"))
    assert dag_spec["status"] == "blocked_before_model_nodes"
    assert dag_spec_validation["ok"] is True
    assert not (case_dir / "page_before.json").exists()
    html = (case_dir / "review.html").read_text(encoding="utf-8")
    assert "No rendered page images were produced before this case failed closed." in html
    with zipfile.ZipFile(case_dir / "review_bundle.zip") as bundle:
        names = set(bundle.namelist())
    assert {
        "terminal_ledger.json",
        "page_extraction_error.json",
        "scillm_orchestrator_page_dag_spec.json",
        "review.html",
    }.issubset(names)


def test_validate_review_response_rejects_terminal_claims() -> None:
    dag = _load_module()
    validation = dag.validate_review_response(
        {
            "schema": "pdf_lab.second_pass.review_response.v1",
            "page_status": "clean",
            "terminal_status": "patched_confirmed",
            "candidate_findings": [
                {
                    "candidate_id": "cand:p0003:0000:table",
                    "status": "clean",
                    "evidence": "bbox matches rendered table",
                    "rationale": "visual and JSON agree",
                }
            ],
        },
        ["cand:p0003:0000:table"],
    )

    assert validation["ok"] is False
    assert any("terminal/closure" in error for error in validation["errors"])


def test_validate_review_response_requires_page_rationale() -> None:
    dag = _load_module()
    validation = dag.validate_review_response(
        {
            "schema": "pdf_lab.second_pass.review_response.v1",
            "page_status": "clean",
            "candidate_findings": [
                {
                    "candidate_id": "cand:p0003:0000:table",
                    "status": "clean",
                    "evidence": "bbox matches rendered table",
                    "rationale": "visual and JSON agree",
                    "suggested_fix_surface": "none",
                }
            ],
        },
        ["cand:p0003:0000:table"],
    )

    assert validation["ok"] is False
    assert "page_rationale must be non-empty" in validation["errors"]


def test_validate_review_response_rejects_stale_receipt_metadata() -> None:
    dag = _load_module()
    review = {
        "schema": "pdf_lab.second_pass.review_response.v1",
        "page_status": "defect",
        "page_rationale": "visual and JSON disagree",
        "candidate_findings": [
            {
                "candidate_id": "cand:p0003:0000:table",
                "status": "defect",
                "evidence": "bbox misses the rendered table",
                "rationale": "table-like evidence is not represented",
                "suggested_fix_surface": "python/pdf_oxide classifier",
            }
        ],
    }
    request = {
        "scillm_metadata": {
            "batch_id": "batch-review",
            "item_id": "page_case_0001_p0003",
        }
    }
    receipt = {
        "schema": "pdf_lab.second_pass.scillm_review_receipt.v1",
        "scillm_metadata": {
            "batch_id": "batch-stale",
            "item_id": "page_case_9999_p9999",
        },
        "review_response": review,
    }

    validation = dag.validate_review_response(
        review,
        ["cand:p0003:0000:table"],
        receipt=receipt,
        request=request,
    )

    assert validation["ok"] is False
    assert "review receipt scillm_metadata batch_id does not match request" in validation["errors"]
    assert "review receipt scillm_metadata item_id does not match request" in validation["errors"]


def test_validate_review_response_rejects_wrong_receipt_surface_and_status() -> None:
    dag = _load_module()
    review = {
        "schema": "pdf_lab.second_pass.review_response.v1",
        "page_status": "defect",
        "page_rationale": "visual and JSON disagree",
        "candidate_findings": [
            {
                "candidate_id": "cand:p0003:0000:table",
                "status": "defect",
                "evidence": "bbox misses the rendered table",
                "rationale": "table-like evidence is not represented",
                "suggested_fix_surface": "python/pdf_oxide classifier",
            }
        ],
    }
    request = {
        "scillm_metadata": {
            "batch_id": "batch-review",
            "item_id": "page_case_0001_p0003",
        }
    }
    receipt = {
        "schema": "pdf_lab.second_pass.scillm_review_receipt.v1",
        "endpoint": "POST /v1/scillm/opencode/runs",
        "http_status": 202,
        "scillm_metadata": request["scillm_metadata"],
        "review_response": review,
    }

    validation = dag.validate_review_response(
        review,
        ["cand:p0003:0000:table"],
        receipt=receipt,
        request=request,
    )

    assert validation["ok"] is False
    assert "review receipt endpoint mismatch" in validation["errors"]
    assert "review receipt http_status must be 200" in validation["errors"]


def test_validate_review_response_rejects_inconsistent_page_and_candidate_statuses() -> None:
    dag = _load_module()
    clean_with_defect = dag.validate_review_response(
        {
            "schema": "pdf_lab.second_pass.review_response.v1",
            "page_status": "clean",
            "candidate_findings": [
                {
                    "candidate_id": "cand:p0003:0000:table",
                    "status": "defect",
                    "evidence": "bbox misses the rendered table",
                    "rationale": "visual and JSON disagree",
                    "suggested_fix_surface": "python/pdf_oxide classifier",
                }
            ],
        },
        ["cand:p0003:0000:table"],
    )
    defect_without_defect_finding = dag.validate_review_response(
        {
            "schema": "pdf_lab.second_pass.review_response.v1",
            "page_status": "defect",
            "candidate_findings": [
                {
                    "candidate_id": "cand:p0003:0000:table",
                    "status": "clean",
                    "evidence": "bbox matches the rendered table",
                    "rationale": "visual and JSON agree",
                    "suggested_fix_surface": "none",
                }
            ],
        },
        ["cand:p0003:0000:table"],
    )

    assert clean_with_defect["ok"] is False
    assert "page_status clean requires every candidate finding status to be clean" in clean_with_defect["errors"]
    assert defect_without_defect_finding["ok"] is False
    assert "page_status defect requires at least one defect candidate finding" in defect_without_defect_finding["errors"]


def test_validate_review_response_rejects_duplicate_findings_and_bad_fix_surface() -> None:
    dag = _load_module()
    validation = dag.validate_review_response(
        {
            "schema": "pdf_lab.second_pass.review_response.v1",
            "page_status": "defect",
            "candidate_findings": [
                {
                    "candidate_id": "cand:p0003:0000:table",
                    "status": "defect",
                    "evidence": "bbox misses the rendered table",
                    "rationale": "visual and JSON disagree",
                    "suggested_fix_surface": "none",
                },
                {
                    "candidate_id": "cand:p0003:0000:table",
                    "status": "clean",
                    "evidence": "duplicate clean claim",
                    "rationale": "duplicate finding should fail",
                    "suggested_fix_surface": "python/pdf_oxide",
                },
            ],
        },
        ["cand:p0003:0000:table"],
    )

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "duplicate candidate findings" in errors
    assert "suggested_fix_surface must identify a fix surface for defect findings" in errors
    assert "suggested_fix_surface must be none for clean findings" in errors


def test_run_page_case_live_review_routes_clean_without_patch(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "table",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "A | B",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_call_scillm_review(review_request, **kwargs):
        assert review_request["scillm_payload"]["scillm_metadata"] == {
            "batch_id": "batch-live",
            "item_id": "page_case_0001_p0003",
        }
        return {
            "schema": "pdf_lab.second_pass.scillm_review_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 200,
            "scillm_metadata": review_request["scillm_metadata"],
            "raw_response": {"choices": [{"message": {"content": "{}"}}]},
            "review_response": {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean",
                "page_rationale": "visual evidence and extraction agree",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:table",
                        "status": "clean",
                        "evidence": "annotated bbox covers rendered table",
                        "rationale": "candidate preset and JSON agree",
                        "suggested_fix_surface": "none",
                    }
                ],
            },
        }

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_scillm_review", fake_call_scillm_review)

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:table",
                "page_number": 3,
                "preset_type": "table",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "table"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:table"],
                "strata": ["preset:table"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-live",
        review_mode="live",
    )

    case_dir = Path(result["case_dir"])
    assert result["terminal_status"] == "reviewed_clean"
    assert json.loads((case_dir / "review_validation.json").read_text(encoding="utf-8"))["ok"] is True
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    assert ledger["terminal_status"] == "reviewed_clean"
    assert ledger["commit_sha"] is None
    assert (case_dir / "scillm_review_receipt.json").is_file()
    assert (case_dir / "receipts/scillm_one_shot_page_review.json").is_file()


def test_live_review_failure_writes_blocked_substrate_bundle(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [{"id": "actual:p3:block:0", "type": "table", "bbox": [0.1, 0.2, 0.8, 0.4], "text": "A | B"}],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_scillm_review", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("proxy unavailable")))

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:table",
                "page_number": 3,
                "preset_type": "table",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "table"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:table"],
                "strata": ["preset:table"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-live-fail",
        review_mode="live",
    )

    case_dir = Path(result["case_dir"])
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    validation = json.loads((case_dir / "review_validation.json").read_text(encoding="utf-8"))
    error = json.loads((case_dir / "scillm_review_error.json").read_text(encoding="utf-8"))
    assert result["terminal_status"] == "blocked_substrate"
    assert ledger["terminal_status"] == "blocked_substrate"
    assert ledger["reason"] == "scillm_review_call_failed"
    assert "scillm_review_error.json" in ledger["evidence_artifacts"]
    assert validation["errors"] == ["scillm_review_call_failed"]
    assert error["error_type"] == "RuntimeError"
    assert (case_dir / "review.html").is_file()
    with zipfile.ZipFile(case_dir / "review_bundle.zip") as bundle:
        assert "scillm_review_error.json" in bundle.namelist()
        assert "review.html" in bundle.namelist()


def test_live_review_preflight_failure_blocks_before_model_call(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [{"id": "actual:p3:block:0", "type": "table", "bbox": [0.1, 0.2, 0.8, 0.4], "text": "A | B"}],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_preflight(**kwargs):
        assert kwargs["surface"] == "chat"
        return {
            "schema": "pdf_lab.second_pass.scillm_preflight.v1",
            "surface": "chat",
            "base_url": kwargs["base_url"],
            "caller_skill": kwargs["caller_skill"],
            "checks": [],
            "ok": False,
            "errors": ["scillm health status is not ok"],
        }

    def fail_call_scillm_review(*args, **kwargs):
        raise AssertionError("review call should not run after failed preflight")

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "preflight_scillm_surface", fake_preflight)
    monkeypatch.setattr(dag, "call_scillm_review", fail_call_scillm_review)

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:table",
                "page_number": 3,
                "preset_type": "table",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "table"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:table"],
                "strata": ["preset:table"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-live",
        review_mode="live",
        scillm_preflight_mode="live",
    )

    case_dir = Path(result["case_dir"])
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    assert result["terminal_status"] == "blocked_substrate"
    assert ledger["reason"] == "scillm_review_call_failed"
    assert "scillm_review_preflight.json" in ledger["evidence_artifacts"]
    assert "scillm_review_error.json" in ledger["evidence_artifacts"]
    with zipfile.ZipFile(case_dir / "review_bundle.zip") as bundle:
        assert "scillm_review_preflight.json" in bundle.namelist()
        assert "scillm_review_error.json" in bundle.namelist()


def test_preflight_scillm_surface_checks_caller_contract_and_opencode_health(monkeypatch) -> None:
    dag = _load_module()
    calls: list[tuple[str, str, bool]] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    class FakeHttpx:
        @staticmethod
        def get(url, headers, timeout):
            path = "/" + url.split("/", 3)[3]
            calls.append(("GET", path, "X-Caller-Skill" in headers))
            if path == "/health/liveliness":
                return FakeResponse(200, {"status": "ok"})
            if path == "/v1/scillm/health":
                return FakeResponse(200, {"status": "ok"})
            if path == "/v1/scillm/opencode/health":
                return FakeResponse(200, {"status": "ok", "opencode_serve": True})
            return FakeResponse(404, {"error": "not_found"})

        @staticmethod
        def post(url, headers, json, timeout):
            path = "/" + url.split("/", 3)[3]
            calls.append(("POST", path, "X-Caller-Skill" in headers))
            assert json["model"] == "local-text"
            return FakeResponse(400, {"error": {"code": "caller_skill_required"}})

    monkeypatch.setitem(sys.modules, "httpx", FakeHttpx)

    preflight = dag.preflight_scillm_surface(
        base_url="http://localhost:4001",
        auth_token="token",
        caller_skill="pdf-lab",
        surface="opencode_serve",
        timeout_s=1.0,
    )

    assert preflight["ok"] is True
    assert ("GET", "/v1/scillm/opencode/health", True) in calls
    assert ("POST", "/v1/chat/completions", False) in calls
    assert any(check.get("include_caller_skill") is False for check in preflight["checks"])


def test_preflight_scillm_surface_rejects_missing_caller_contract_regression(monkeypatch) -> None:
    dag = _load_module()

    class FakeResponse:
        status_code = 404
        text = '{"error":"model_not_found"}'

        def json(self):
            return {"error": "model_not_found"}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    class FakeHealthResponse:
        status_code = 200

        def json(self):
            return {"status": "ok"}

        def raise_for_status(self):
            return None

    class FakeHttpx:
        @staticmethod
        def get(url, headers, timeout):
            return FakeHealthResponse()

        @staticmethod
        def post(url, headers, json, timeout):
            assert "X-Caller-Skill" not in headers
            return FakeResponse()

    monkeypatch.setitem(sys.modules, "httpx", FakeHttpx)

    preflight = dag.preflight_scillm_surface(
        base_url="http://localhost:4001",
        auth_token="token",
        caller_skill="pdf-lab",
        surface="chat",
        timeout_s=1.0,
    )

    assert preflight["ok"] is False
    assert "missing-caller chat contract did not return caller_skill_required" in preflight["errors"]


def test_opencode_agent_profile_rejects_chat_model_id() -> None:
    dag = _load_module()

    try:
        dag.validate_opencode_agent_profile("opencode-go/kimi-k2.6")
    except ValueError as exc:
        assert "agent profile" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_effective_opencode_model_defaults_only_for_live_orchestrator() -> None:
    dag = _load_module()

    assert dag.resolve_effective_opencode_model(
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        opencode_model=None,
    ) == "opencode-go/kimi-k2.6"
    assert dag.resolve_effective_opencode_model(
        patch_mode="dry_run",
        patch_backend="scillm_orchestrator",
        opencode_model=None,
    ) is None
    assert dag.resolve_effective_opencode_model(
        patch_mode="live",
        patch_backend="opencode_serve",
        opencode_model=None,
    ) is None
    assert dag.resolve_effective_opencode_model(
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        opencode_model="custom-model",
    ) == "custom-model"


def test_validate_review_request_contract_rejects_stale_page_case_item_id(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "page_before.json").write_text(json.dumps({"page": 3, "blocks": []}), encoding="utf-8")
    (case_dir / "candidate_presets.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
    (case_dir / "page_before.png").write_bytes(b"png")
    (case_dir / "page_candidates.png").write_bytes(b"png")
    request = dag.build_review_request(
        case_dir=case_dir,
        page_case={"case_id": "page_case_0001_p0003", "page_number": 3},
        page_json_path="page_before.json",
        original_image_path="page_before.png",
        annotated_image_path="page_candidates.png",
        candidate_presets_path="candidate_presets.json",
        model="gpt-5.5",
        batch_id="batch-review",
    )
    request["scillm_metadata"]["item_id"] = "page_case_9999_p9999"
    request["scillm_payload"]["scillm_metadata"] = dict(request["scillm_metadata"])

    validation = dag.validate_review_request_contract(case_dir, request)

    assert validation["ok"] is False
    assert "scillm_payload scillm_metadata.item_id must match review_request page_case.case_id" in validation["errors"]


def test_validate_review_request_contract_rejects_stale_payload_model_identity(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "page_before.json").write_text(json.dumps({"page": 1, "blocks": []}), encoding="utf-8")
    (case_dir / "candidate_presets.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
    (case_dir / "page_before.png").write_bytes(b"png")
    (case_dir / "page_candidates.png").write_bytes(b"png")
    request = dag.build_review_request(
        case_dir=case_dir,
        page_case={"case_id": "page_case_0001_p0001", "page_number": 1},
        page_json_path="page_before.json",
        original_image_path="page_before.png",
        annotated_image_path="page_candidates.png",
        candidate_presets_path="candidate_presets.json",
        model="gpt-5.5",
        batch_id="batch-review",
    )
    request["scillm_payload"]["model"] = "gpt-4.1"
    request["scillm_payload"]["reasoning_effort"] = "low"

    validation = dag.validate_review_request_contract(case_dir, request)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "scillm_payload model must match review_request model" in errors
    assert "scillm_payload reasoning_effort must match review_request reasoning_effort" in errors


def test_validate_review_request_contract_rejects_stale_prompt_evidence(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "page_before.json").write_text(json.dumps({"page": 1, "blocks": []}), encoding="utf-8")
    (case_dir / "candidate_presets.json").write_text(
        json.dumps({"schema": "pdf_lab.second_pass.candidate_presets.v1", "candidates": []}),
        encoding="utf-8",
    )
    (case_dir / "page_before.png").write_bytes(b"png")
    (case_dir / "page_candidates.png").write_bytes(b"png")
    request = dag.build_review_request(
        case_dir=case_dir,
        page_case={"case_id": "page_case_0001_p0001", "page_number": 1},
        page_json_path="page_before.json",
        original_image_path="page_before.png",
        annotated_image_path="page_candidates.png",
        candidate_presets_path="candidate_presets.json",
        model="gpt-5.5",
        batch_id="batch-review",
    )
    request["page_case"] = {"case_id": "page_case_0001_p0001", "page_number": 99}
    (case_dir / "candidate_presets.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.candidate_presets.v1",
                "candidates": [{"candidate_id": "cand:p0099:0000:table"}],
            }
        ),
        encoding="utf-8",
    )

    validation = dag.validate_review_request_contract(case_dir, request)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "scillm_payload text prompt does not include current review_request page_case" in errors
    assert "scillm_payload text prompt does not include current artifacts.candidate_presets" in errors


def test_validate_review_request_contract_rejects_stale_page_json_identity(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "page_before.json").write_text(
        json.dumps({"page": 99, "page_number": 99, "pdf_page_index": 98, "blocks": []}),
        encoding="utf-8",
    )
    (case_dir / "candidate_presets.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
    (case_dir / "page_before.png").write_bytes(b"png")
    (case_dir / "page_candidates.png").write_bytes(b"png")
    request = dag.build_review_request(
        case_dir=case_dir,
        page_case={"case_id": "page_case_0001_p0001", "page_number": 1},
        page_json_path="page_before.json",
        original_image_path="page_before.png",
        annotated_image_path="page_candidates.png",
        candidate_presets_path="candidate_presets.json",
        model="gpt-5.5",
        batch_id="batch-review",
    )

    validation = dag.validate_review_request_contract(case_dir, request)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "review_request artifacts.page_json page does not match page_case.page_number" in errors
    assert "review_request artifacts.page_json page_number does not match page_case.page_number" in errors
    assert "review_request artifacts.page_json pdf_page_index does not match page_case.page_number" in errors


def test_validate_review_request_contract_rejects_stale_candidate_presets_ids(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "page_before.json").write_text(json.dumps({"page": 1, "blocks": []}), encoding="utf-8")
    (case_dir / "candidate_presets.json").write_text(
        json.dumps(_candidate_presets_payload(["cand:p0002:0000:table"])),
        encoding="utf-8",
    )
    (case_dir / "page_before.png").write_bytes(b"png")
    (case_dir / "page_candidates.png").write_bytes(b"png")
    request = dag.build_review_request(
        case_dir=case_dir,
        page_case={
            "case_id": "page_case_0001_p0001",
            "page_number": 1,
            "candidate_ids": ["cand:p0001:0000:table"],
        },
        page_json_path="page_before.json",
        original_image_path="page_before.png",
        annotated_image_path="page_candidates.png",
        candidate_presets_path="candidate_presets.json",
        model="gpt-5.5",
        batch_id="batch-review",
    )

    validation = dag.validate_review_request_contract(case_dir, request)

    assert validation["ok"] is False
    assert "review_request artifacts.candidate_presets candidate_ids do not match page_case.candidate_ids" in validation["errors"]


def test_validate_review_request_contract_rejects_stale_candidate_presets_contract(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "page_before.json").write_text(json.dumps({"page": 1, "blocks": []}), encoding="utf-8")
    preset_payload = _candidate_presets_payload(
        ["cand:p0001:0000:table"],
        case_id="page_case_9999_p9999",
        page_number=9999,
        candidate_count=2,
    )
    preset_payload["schema"] = "pdf_lab.second_pass.candidate_presets.v0"
    (case_dir / "candidate_presets.json").write_text(json.dumps(preset_payload), encoding="utf-8")
    (case_dir / "page_before.png").write_bytes(b"png")
    (case_dir / "page_candidates.png").write_bytes(b"png")
    request = dag.build_review_request(
        case_dir=case_dir,
        page_case={
            "case_id": "page_case_0001_p0001",
            "page_number": 1,
            "candidate_ids": ["cand:p0001:0000:table"],
        },
        page_json_path="page_before.json",
        original_image_path="page_before.png",
        annotated_image_path="page_candidates.png",
        candidate_presets_path="candidate_presets.json",
        model="gpt-5.5",
        batch_id="batch-review",
    )

    validation = dag.validate_review_request_contract(case_dir, request)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "review_request artifacts.candidate_presets schema mismatch" in errors
    assert "review_request artifacts.candidate_presets page_case.case_id does not match page_case.case_id" in errors
    assert "review_request artifacts.candidate_presets page_case.page_number does not match page_case.page_number" in errors
    assert "review_request artifacts.candidate_presets candidate_count does not match candidates" in errors


def test_patch_worker_prompt_uses_absolute_workspace_and_evidence_paths(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    workspace = tmp_path / "workspace"
    case_dir.mkdir()
    workspace.mkdir()

    prompt = dag.build_patch_worker_prompt(
        executor_label="test",
        case_dir=case_dir,
        workspace_root=workspace,
        page_case={"case_id": "case-1", "page_number": 1},
        candidates=[{"candidate_id": "cand:p0001:0000:unknown_layout"}],
        review_response={
            "schema": "pdf_lab.second_pass.review_response.v1",
            "candidate_findings": [
                {
                    "candidate_id": "cand:p0001:0000:unknown_layout",
                    "status": "defect",
                }
            ],
        },
    )

    assert f"Workspace root: {workspace.resolve()}" in prompt
    assert f"Evidence case directory: {case_dir.resolve()}" in prompt
    assert str(case_dir.resolve() / "page_before.json") in prompt
    assert "## Output Format" in prompt
    assert "PATCH_APPLIED" in prompt
    assert "PATCH_DELEGATE_BLOCKED" in prompt


def test_compact_patch_worker_prompt_keeps_contract_but_omits_full_candidate_payload(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    workspace = tmp_path / "workspace"
    case_dir.mkdir()
    workspace.mkdir()

    prompt = dag.build_patch_worker_prompt(
        executor_label="test",
        case_dir=case_dir,
        workspace_root=workspace,
        page_case={"case_id": "case-1", "page_number": 1, "candidate_ids": ["cand:p0001:0000:unknown_layout"]},
        candidates=[
            {
                "candidate_id": "cand:p0001:0000:unknown_layout",
                "preset_type": "unknown_layout",
                "text_excerpt": "broken text",
                "features": {"large_nested_payload": "must_not_be_in_compact_prompt"},
            }
        ],
        review_response={
            "schema": "pdf_lab.second_pass.review_response.v1",
            "page_rationale": "full rationale should not be copied wholesale",
            "candidate_findings": [
                {
                    "candidate_id": "cand:p0001:0000:unknown_layout",
                    "status": "defect",
                    "rationale": "compact defect reason",
                }
            ],
        },
        prompt_profile="compact",
    )

    assert f"Workspace root: {workspace.resolve()}" in prompt
    assert "## Output Format" in prompt
    assert "PATCH_APPLIED" in prompt
    assert "## Compact Evidence Payload" in prompt
    assert "broken text" in prompt
    assert "compact defect reason" in prompt
    assert "must_not_be_in_compact_prompt" not in prompt
    assert "full rationale should not be copied wholesale" not in prompt


def test_plan_only_patch_worker_prompt_uses_repair_plan_without_bulk_evidence(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    workspace = tmp_path / "workspace"
    case_dir.mkdir()
    workspace.mkdir()

    prompt = dag.build_patch_worker_prompt(
        executor_label="test",
        case_dir=case_dir,
        workspace_root=workspace,
        page_case={"case_id": "case-1", "page_number": 1, "candidate_ids": ["cand:p0001:0000:unknown_layout"]},
        candidates=[
            {
                "candidate_id": "cand:p0001:0000:unknown_layout",
                "preset_type": "unknown_layout",
                "text_excerpt": "broken text",
                "features": {"large_nested_payload": "must_not_be_in_plan_only_prompt"},
            }
        ],
        review_response={
            "schema": "pdf_lab.second_pass.review_response.v1",
            "page_rationale": "full rationale should not be copied into plan-only prompt",
            "candidate_findings": [
                {
                    "candidate_id": "cand:p0001:0000:unknown_layout",
                    "status": "defect",
                    "rationale": "fallback defect reason",
                }
            ],
        },
        repair_diagnosis={
            "schema": "pdf_lab.second_pass.scillm_repair_plan_receipt.v1",
            "repair_plan": {
                "schema": "pdf_lab.second_pass.repair_plan.v1",
                "summary": "classify table-like unknown layout",
                "suspected_fault": "classifier misses table-like content",
                "patch_targets": ["python/pdf_oxide/classifier.py", "tests/test_classifier.py"],
                "test_plan": ["add focused regression"],
                "patch_constraints": ["smallest safe change"],
                "confidence": "medium",
            },
        },
        prompt_profile="plan_only",
    )

    assert f"Workspace root: {workspace.resolve()}" in prompt
    assert "## Plan Payload" in prompt
    assert "## Output Format" in prompt
    assert "PATCH_APPLIED" in prompt
    assert "classify table-like unknown layout" in prompt
    assert "classifier misses table-like content" in prompt
    assert "python/pdf_oxide/classifier.py" in prompt
    assert "must_not_be_in_plan_only_prompt" not in prompt
    assert "full rationale should not be copied into plan-only prompt" not in prompt
    assert "fallback defect reason" not in prompt


def test_patch_prompt_contract_accepts_plan_only_prompt_and_writes_review_payload(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    workspace = tmp_path / "workspace"
    case_dir.mkdir()
    workspace.mkdir()
    patch_request = dag.build_opencode_patch_request(
        case_dir=case_dir,
        page_case={"case_id": "case-1", "page_number": 1, "candidate_ids": ["cand:p0001:0000:unknown_layout"]},
        candidates=[{"candidate_id": "cand:p0001:0000:unknown_layout", "preset_type": "unknown_layout"}],
        review_response={
            "schema": "pdf_lab.second_pass.review_response.v1",
            "candidate_findings": [{"candidate_id": "cand:p0001:0000:unknown_layout", "status": "defect"}],
        },
        agent="build",
        opencode_model=None,
        skills=["scillm"],
        timeout_s=30,
        cleanup_session=True,
        cwd=workspace,
        prompt_profile="plan_only",
        repair_diagnosis={
            "schema": "pdf_lab.second_pass.scillm_repair_plan_receipt.v1",
            "repair_plan": {
                "schema": "pdf_lab.second_pass.repair_plan.v1",
                "summary": "classify table-like unknown layout",
                "suspected_fault": "classifier misses table-like content",
                "patch_targets": ["python/pdf_oxide/classifier.py", "tests/test_classifier.py"],
                "test_plan": ["add focused regression"],
                "patch_constraints": ["smallest safe change"],
                "confidence": "medium",
            },
        },
    )

    contract, artifacts = dag.write_patch_prompt_contract_artifacts(
        case_dir,
        patch_request,
        artifact_prefix="patch_attempt_01_",
        live_patch_required=True,
    )

    assert contract["ok"] is True
    assert contract["metrics"]["char_count"] <= dag.PATCH_PROMPT_MAX_CHARS
    assert "patch_attempt_01_prompt_contract.json" in artifacts
    assert "patch_attempt_01_prompt_review_payload.txt" in artifacts
    review_payload = (case_dir / "patch_attempt_01_prompt_review_payload.txt").read_text(encoding="utf-8")
    assert "Purpose: Validate the PDF Oxide pdf-lab OpenCode patch delegate prompt" in review_payload
    assert "VALID OUTPUT EXAMPLE" in review_payload
    assert "PATCH_APPLIED" in review_payload


def test_patch_prompt_contract_rejects_stale_request_identity(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    workspace = tmp_path / "workspace"
    page_case = {
        "case_id": "case-1",
        "page_number": 1,
        "candidate_ids": ["cand:p0001:0000:unknown_layout"],
    }
    case_dir.mkdir()
    workspace.mkdir()
    patch_request = dag.build_scillm_orchestrator_patch_request(
        case_dir=case_dir,
        page_case=page_case,
        candidates=[{"candidate_id": "cand:p0001:0000:unknown_layout", "preset_type": "unknown_layout"}],
        review_response={
            "schema": "pdf_lab.second_pass.review_response.v1",
            "candidate_findings": [{"candidate_id": "cand:p0001:0000:unknown_layout", "status": "defect"}],
        },
        agent="build",
        opencode_model=None,
        skills=["scillm"],
        timeout_s=30,
        cwd=workspace,
        prompt_profile="plan_only",
    )
    patch_request["attempt_index"] = 1
    patch_request["attempt_count"] = 2
    patch_request["transport_retry_fresh_parent"] = False
    patch_request["scillm_metadata"]["attempt_index"] = 1
    patch_request["scillm_metadata"]["attempt_count"] = 2
    patch_request["scillm_metadata"]["agent"] = "build"
    patch_request["scillm_metadata"]["transport_retry_fresh_parent"] = False

    contract = dag.validate_patch_prompt_contract(
        patch_request,
        live_patch_required=True,
        expected_page_case=page_case,
    )
    assert contract["ok"] is True

    stale_case = json.loads(json.dumps(patch_request))
    stale_case["scillm_metadata"]["case_id"] = "case-2"
    stale_case_contract = dag.validate_patch_prompt_contract(
        stale_case,
        live_patch_required=True,
        expected_page_case=page_case,
    )
    assert stale_case_contract["ok"] is False
    assert "patch request scillm_metadata.case_id must match page_case.case_id" in stale_case_contract["errors"]
    assert "scillm orchestrator patch request dag_node_id must match scillm_metadata.case_id" in stale_case_contract["errors"]
    assert (
        "scillm orchestrator patch request create_run_body.dag_node_id must match scillm_metadata.case_id"
        in stale_case_contract["errors"]
    )

    stale_page = json.loads(json.dumps(patch_request))
    stale_page["scillm_metadata"]["page_number"] = 99
    stale_page_contract = dag.validate_patch_prompt_contract(
        stale_page,
        live_patch_required=True,
        expected_page_case=page_case,
    )
    assert stale_page_contract["ok"] is False
    assert "patch request scillm_metadata.page_number must match page_case.page_number" in stale_page_contract["errors"]

    stale_agent = json.loads(json.dumps(patch_request))
    stale_agent["scillm_metadata"]["agent"] = "implement"
    stale_agent_contract = dag.validate_patch_prompt_contract(
        stale_agent,
        live_patch_required=True,
        expected_page_case=page_case,
    )
    assert stale_agent_contract["ok"] is False
    assert "patch request scillm_metadata.agent must match patch_request.agent" in stale_agent_contract["errors"]

    stale_attempt = json.loads(json.dumps(patch_request))
    stale_attempt["scillm_metadata"]["attempt_index"] = 2
    stale_attempt_contract = dag.validate_patch_prompt_contract(
        stale_attempt,
        live_patch_required=True,
        expected_page_case=page_case,
    )
    assert stale_attempt_contract["ok"] is False
    assert (
        "patch request scillm_metadata.attempt_index must match patch_request.attempt_index"
        in stale_attempt_contract["errors"]
    )

    missing_retry_metadata = json.loads(json.dumps(patch_request))
    del missing_retry_metadata["scillm_metadata"]["transport_retry_fresh_parent"]
    missing_retry_contract = dag.validate_patch_prompt_contract(
        missing_retry_metadata,
        live_patch_required=True,
        expected_page_case=page_case,
    )
    assert missing_retry_contract["ok"] is False
    assert (
        "patch request transport_retry_fresh_parent must be present in both request and scillm_metadata"
        in missing_retry_contract["errors"]
    )


def test_plan_only_patch_prompt_sanitizes_dynamic_review_weasel_words(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    workspace = tmp_path / "workspace"
    case_dir.mkdir()
    workspace.mkdir()
    review_response = {
        "schema": "pdf_lab.second_pass.review_response.v1",
        "candidate_findings": [
            {
                "candidate_id": "cand:p0001:0000:unknown_layout",
                "status": "defect",
                "evidence": "The large region includes whitespace.",
                "rationale": "Split separated lines where appropriate.",
                "suggested_fix_surface": "Tighten segmentation where appropriate.",
            }
        ],
    }

    patch_request = dag.build_opencode_patch_request(
        case_dir=case_dir,
        page_case={"case_id": "case-1", "page_number": 1, "candidate_ids": ["cand:p0001:0000:unknown_layout"]},
        candidates=[{"candidate_id": "cand:p0001:0000:unknown_layout", "preset_type": "unknown_layout"}],
        review_response=review_response,
        agent="build",
        opencode_model=None,
        skills=["scillm"],
        timeout_s=30,
        cleanup_session=True,
        cwd=workspace,
        prompt_profile="plan_only",
    )

    contract = dag.validate_patch_prompt_contract(patch_request, live_patch_required=True)

    assert contract["ok"] is True
    assert "appropriate" not in patch_request["prompt"].lower()
    assert "where specific" in patch_request["prompt"]
    assert review_response["candidate_findings"][0]["suggested_fix_surface"] == "Tighten segmentation where appropriate."


def test_default_patch_prompt_profile_stays_live_bounded_for_many_candidates(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    workspace = tmp_path / "workspace"
    case_dir.mkdir()
    workspace.mkdir()
    candidate_ids = [f"cand:p0001:{index:04d}:reference" for index in range(30)]
    patch_request = dag.build_opencode_patch_request(
        case_dir=case_dir,
        page_case={"case_id": "case-1", "page_number": 1, "candidate_ids": candidate_ids},
        candidates=[
            {
                "candidate_id": candidate_id,
                "preset_type": "reference",
                "text_excerpt": "large candidate payload should not be copied into the default live patch prompt",
                "features": {"large_nested_payload": "x" * 200},
            }
            for candidate_id in candidate_ids
        ],
        review_response={
            "schema": "pdf_lab.second_pass.review_response.v1",
            "candidate_findings": [
                {
                    "candidate_id": candidate_ids[0],
                    "status": "defect",
                    "rationale": "first row is misclassified",
                    "suggested_fix_surface": "tests/test_pdf_lab_live_patch_canary.py",
                }
            ],
        },
        agent="build",
        opencode_model=None,
        skills=["scillm"],
        timeout_s=30,
        cleanup_session=True,
        cwd=workspace,
    )

    contract = dag.validate_patch_prompt_contract(patch_request, live_patch_required=True)

    assert patch_request["prompt_profile"] == "plan_only"
    assert contract["ok"] is True
    assert contract["metrics"]["char_count"] <= dag.PATCH_PROMPT_MAX_CHARS
    assert "large candidate payload should not be copied" not in patch_request["prompt"]
    assert "large_nested_payload" not in patch_request["prompt"]


def test_patch_evidence_workspace_is_ignored_and_used_by_prompt(tmp_path: Path) -> None:
    dag = _load_module()
    code_root = tmp_path / "workspace"
    case_dir = tmp_path / "case"
    code_root.mkdir()
    case_dir.mkdir()
    subprocess.run(["git", "init"], cwd=code_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for name in dag.PATCH_EVIDENCE_WORKSPACE_FILES:
        path = case_dir / name
        path.write_bytes(b"png") if name.endswith(".png") else path.write_text("{}", encoding="utf-8")

    workspace = dag.materialize_patch_evidence_workspace(case_dir, code_root, "case-1")
    patch_request = dag.build_opencode_patch_request(
        case_dir=case_dir,
        evidence_case_dir=Path(workspace["workspace_case_dir"]),
        page_case={"case_id": "case-1", "page_number": 1, "candidate_ids": ["cand:p0001:0000:unknown_layout"]},
        candidates=[{"candidate_id": "cand:p0001:0000:unknown_layout", "preset_type": "unknown_layout"}],
        review_response={
            "schema": "pdf_lab.second_pass.review_response.v1",
            "candidate_findings": [{"candidate_id": "cand:p0001:0000:unknown_layout", "status": "defect"}],
        },
        agent="build",
        opencode_model=None,
        skills=["scillm"],
        timeout_s=30,
        cleanup_session=True,
        cwd=code_root,
    )
    status = subprocess.run(
        ["git", "-C", str(code_root), "status", "--short"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout

    assert workspace["ok"] is True
    assert ".pdf_lab_runtime/" in (code_root / ".git/info/exclude").read_text(encoding="utf-8")
    assert status.strip() == ""
    assert str(Path(workspace["workspace_case_dir"]).resolve() / "review_response.json") in patch_request["prompt"]
    assert str(case_dir.resolve() / "review_response.json") not in patch_request["prompt"]


def test_patch_prompt_contract_blocks_oversized_live_prompt(tmp_path: Path) -> None:
    dag = _load_module()
    patch_request = {
        "schema": "pdf_lab.second_pass.opencode_patch_request.v1",
        "prompt_profile": "full",
        "prompt": "## Role\n## Task\n## Context\n## Constraints\n"
        + ("x" * (dag.PATCH_PROMPT_MAX_CHARS + 1))
        + "\n## Output Format\nPATCH_APPLIED\nPATCH_DELEGATE_BLOCKED\nDo not commit\nWorkspace root:\nreview_response\nreview_validation",
    }

    contract = dag.validate_patch_prompt_contract(patch_request, live_patch_required=True)

    assert contract["ok"] is False
    assert any("exceeds live max chars" in error for error in contract["errors"])


def test_repair_diagnosis_prompt_is_read_only_and_evidence_bounded(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    workspace = tmp_path / "workspace"
    case_dir.mkdir()
    workspace.mkdir()

    prompt = dag.build_repair_diagnosis_prompt(
        executor_label="test",
        case_dir=case_dir,
        workspace_root=workspace,
        page_case={"case_id": "case-1", "page_number": 1, "candidate_ids": ["cand:p0001:0000:unknown_layout"]},
        candidates=[
            {
                "candidate_id": "cand:p0001:0000:unknown_layout",
                "preset_type": "unknown_layout",
                "text_excerpt": "broken table-like content",
                "features": {"large_nested_payload": "must_not_be_in_diagnosis_prompt"},
            }
        ],
        review_response={
            "schema": "pdf_lab.second_pass.review_response.v1",
            "candidate_findings": [
                {
                    "candidate_id": "cand:p0001:0000:unknown_layout",
                    "status": "defect",
                    "rationale": "diagnose this defect",
                }
            ],
        },
    )

    assert f"workspace_root\": \"{workspace.resolve()}" in prompt
    assert "Do not edit files" in prompt
    assert "Minimal files to inspect or patch" in prompt
    assert "broken table-like content" in prompt
    assert "diagnose this defect" in prompt
    assert "must_not_be_in_diagnosis_prompt" not in prompt


def test_scillm_repair_plan_request_and_validation_are_bounded(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()

    request = dag.build_scillm_repair_plan_request(
        case_dir=case_dir,
        page_case={"case_id": "case-1", "page_number": 1, "candidate_ids": ["cand:p0001:0000:unknown_layout"]},
        candidates=[
            {
                "candidate_id": "cand:p0001:0000:unknown_layout",
                "preset_type": "unknown_layout",
                "text_excerpt": "broken table-like content",
                "features": {"large_nested_payload": "must_not_be_in_repair_plan_prompt"},
            }
        ],
        review_response={
            "schema": "pdf_lab.second_pass.review_response.v1",
            "page_status": "defect",
            "candidate_findings": [
                {
                    "candidate_id": "cand:p0001:0000:unknown_layout",
                    "status": "defect",
                    "rationale": "plan this defect",
                }
            ],
        },
        model="gpt-5.5",
        batch_id="batch-plan",
    )

    prompt = request["scillm_payload"]["messages"][0]["content"]
    assert request["schema"] == "pdf_lab.second_pass.scillm_repair_plan_request.v1"
    assert request["scillm_metadata"]["item_id"] == "case-1:repair_plan"
    assert "Return JSON only" in prompt
    assert "broken table-like content" in prompt
    assert "plan this defect" in prompt
    assert "must_not_be_in_repair_plan_prompt" not in prompt
    request_validation = dag.validate_repair_plan_request_contract(request)
    assert request_validation["ok"] is True

    validation = dag.validate_repair_plan(
        {
            "schema": "pdf_lab.second_pass.repair_plan.v1",
            "summary": "unknown layout should be classified more precisely",
            "suspected_fault": "classifier misses table-like block",
            "patch_targets": ["python/pdf_oxide/classifier.py", "tests/test_classifier.py"],
            "test_plan": ["add focused unknown-layout regression"],
            "patch_constraints": ["smallest safe change", "no generated artifacts"],
            "confidence": "medium",
        }
    )
    assert validation["ok"] is True

    bad = dag.validate_repair_plan({"schema": "pdf_lab.second_pass.repair_plan.v1", "confidence": "certain"})
    assert bad["ok"] is False
    assert any("confidence" in error for error in bad["errors"])


def test_validate_repair_plan_request_rejects_stale_page_case_item_id(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    request = dag.build_scillm_repair_plan_request(
        case_dir=case_dir,
        page_case={"case_id": "page_case_0001_p0003", "page_number": 3},
        candidates=[{"candidate_id": "cand:p0003:0000:unknown_layout", "preset_type": "unknown_layout"}],
        review_response={
            "schema": "pdf_lab.second_pass.review_response.v1",
            "page_status": "defect",
            "candidate_findings": [
                {
                    "candidate_id": "cand:p0003:0000:unknown_layout",
                    "status": "defect",
                    "rationale": "plan this defect",
                }
            ],
        },
        model="gpt-5.5",
        batch_id="batch-plan",
    )
    request["scillm_metadata"]["item_id"] = "page_case_9999_p9999:repair_plan"
    request["scillm_payload"]["scillm_metadata"] = dict(request["scillm_metadata"])

    validation = dag.validate_repair_plan_request_contract(request)

    assert validation["ok"] is False
    assert "repair_plan_request scillm_metadata.item_id must match page_case.case_id repair-plan suffix" in validation["errors"]


def test_validate_repair_plan_rejects_stale_receipt_metadata() -> None:
    dag = _load_module()
    plan = {
        "schema": "pdf_lab.second_pass.repair_plan.v1",
        "summary": "unknown layout should be classified more precisely",
        "suspected_fault": "classifier misses table-like block",
        "patch_targets": ["python/pdf_oxide/classifier.py", "tests/test_classifier.py"],
        "test_plan": ["add focused unknown-layout regression"],
        "patch_constraints": ["smallest safe change", "no generated artifacts"],
        "confidence": "medium",
    }
    request = {
        "scillm_metadata": {
            "batch_id": "batch-repair",
            "item_id": "page_case_0001_p0003:repair_plan",
        }
    }
    receipt = {
        "schema": "pdf_lab.second_pass.scillm_repair_plan_receipt.v1",
        "scillm_metadata": {
            "batch_id": "batch-stale",
            "item_id": "page_case_9999_p9999:repair_plan",
        },
        "repair_plan": plan,
    }

    validation = dag.validate_repair_plan(plan, receipt=receipt, request=request)

    assert validation["ok"] is False
    assert "repair plan receipt scillm_metadata batch_id does not match request" in validation["errors"]
    assert "repair plan receipt scillm_metadata item_id does not match request" in validation["errors"]


def test_validate_repair_plan_rejects_wrong_receipt_surface_and_status() -> None:
    dag = _load_module()
    plan = {
        "schema": "pdf_lab.second_pass.repair_plan.v1",
        "summary": "unknown layout should be classified more precisely",
        "suspected_fault": "classifier misses table-like block",
        "patch_targets": ["python/pdf_oxide/classifier.py", "tests/test_classifier.py"],
        "test_plan": ["add focused unknown-layout regression"],
        "patch_constraints": ["smallest safe change", "no generated artifacts"],
        "confidence": "medium",
    }
    request = {
        "scillm_metadata": {
            "batch_id": "batch-repair",
            "item_id": "page_case_0001_p0003:repair_plan",
        }
    }
    receipt = {
        "schema": "pdf_lab.second_pass.scillm_repair_plan_receipt.v1",
        "endpoint": "POST /v1/scillm/opencode/runs",
        "http_status": 202,
        "scillm_metadata": request["scillm_metadata"],
        "repair_plan": plan,
    }

    validation = dag.validate_repair_plan(plan, receipt=receipt, request=request)

    assert validation["ok"] is False
    assert "repair plan receipt endpoint mismatch" in validation["errors"]
    assert "repair plan receipt http_status must be 200" in validation["errors"]


def test_materialize_opencode_host_artifacts_copies_and_summarizes_timeout(tmp_path: Path) -> None:
    dag = _load_module()
    host_dir = tmp_path / "host"
    case_dir = tmp_path / "case"
    host_dir.mkdir()
    case_dir.mkdir()
    status_path = host_dir / "status.json"
    result_path = host_dir / "opencode_result.json"
    events_path = host_dir / "events.jsonl"
    status_path.write_text(json.dumps({"state": "timeout", "phase": "timed_out"}), encoding="utf-8")
    result_path.write_text(json.dumps({"status": "timeout", "assistant_text": "", "diff": []}), encoding="utf-8")
    events_path.write_text(
        "\n".join(
            [
                json.dumps({"event": "run_started", "run_id": "oc-test"}),
                json.dumps({"event": "messages_snapshot", "assistant_chars": 0}),
                json.dumps({"event": "run_timeout", "timeout_s": 120}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    artifacts = dag.materialize_opencode_host_artifacts(
        case_dir,
        {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "raw_response": {
                "run_id": "oc-test",
                "session_id": "ses-test",
                "status": "timeout",
                "assistant_text": "",
                "diff": [],
                "skills": {"skills_missing": ["debugger"]},
                "artifacts": {
                    "host_status_json": str(status_path),
                    "host_opencode_result_json": str(result_path),
                    "host_events_jsonl": str(events_path),
                },
            },
        },
        prefix="patch_attempt_01_diagnosis_",
    )

    assert "patch_attempt_01_diagnosis_opencode_host_status.json" in artifacts
    assert "patch_attempt_01_diagnosis_opencode_host_result.json" in artifacts
    assert "patch_attempt_01_diagnosis_opencode_host_events.jsonl" in artifacts
    assert "patch_attempt_01_diagnosis_opencode_host_artifacts_summary.json" in artifacts
    summary = json.loads((case_dir / "patch_attempt_01_diagnosis_opencode_host_artifacts_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "timeout"
    assert summary["assistant_text_present"] is False
    assert summary["diff_present"] is False
    assert summary["event_counts"]["run_timeout"] == 1
    assert summary["skills"]["skills_missing"] == ["debugger"]


def test_run_page_case_defect_builds_opencode_patch_request_fail_closed(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "unknown_region",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "broken table-like content",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_call_scillm_review(review_request, **kwargs):
        return {
            "schema": "pdf_lab.second_pass.scillm_review_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 200,
            "scillm_metadata": review_request["scillm_metadata"],
            "raw_response": {"choices": [{"message": {"content": "{}"}}]},
            "review_response": {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "page_rationale": "visual table is not represented by extracted structure",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "defect",
                        "evidence": "annotated region contains table-like content",
                        "rationale": "extraction emits unknown layout instead of table/list structure",
                        "suggested_fix_surface": "python/pdf_oxide presets or extractor classifier",
                    }
                ],
            },
        }

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_scillm_review", fake_call_scillm_review)

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-defect",
        review_mode="live",
        patch_mode="dry_run",
        opencode_model="gpt-5.5",
    )

    case_dir = Path(result["case_dir"])
    assert result["terminal_status"] == "still_open"
    assert (case_dir / "patch_request.json").is_file()
    assert (case_dir / "patch_validation.json").is_file()
    patch_request = json.loads((case_dir / "patch_request.json").read_text(encoding="utf-8"))
    assert patch_request["endpoint"] == "POST /v1/scillm/opencode/runs"
    assert patch_request["agent"] == "build"
    assert patch_request["opencode_model"] == "gpt-5.5"
    assert patch_request["model"] == "gpt-5.5"
    assert str(case_dir.resolve()) in patch_request["prompt"]
    assert "PATCH_DELEGATE_BLOCKED" in patch_request["prompt"]
    assert "opencode-go" not in patch_request["agent"]
    patch_validation = json.loads((case_dir / "patch_validation.json").read_text(encoding="utf-8"))
    assert patch_validation["ok"] is False
    assert patch_validation["errors"] == ["patch_delegate_dry_run"]
    attempts = json.loads((case_dir / "patch_attempts_ledger.json").read_text(encoding="utf-8"))
    assert attempts["attempts"][0]["prompt_contract_artifacts"] == [
        "patch_attempt_01_prompt_contract.json",
        "patch_attempt_01_prompt_review_payload.txt",
    ]
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    assert ledger["terminal_status"] == "still_open"
    assert ledger["reason"] == "patch_delegate_dry_run"
    assert ledger["commit_sha"] is None
    assert "patch_attempt_01_prompt_contract.json" in ledger["evidence_artifacts"]
    assert "patch_attempt_01_prompt_review_payload.txt" in ledger["evidence_artifacts"]


def test_split_repair_strategy_writes_diagnosis_node_before_patch_dry_run(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "unknown_region",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "broken table-like content",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)

    fixture_path = tmp_path / "review_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "page_rationale": "visual table is not represented by extracted structure",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "defect",
                        "evidence": "annotated region contains table-like content",
                        "rationale": "extraction emits unknown layout instead of table/list structure",
                        "suggested_fix_surface": "python/pdf_oxide presets or extractor classifier",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-split",
        review_mode="fixture",
        review_fixture_path=fixture_path,
        patch_mode="dry_run",
        repair_strategy="split",
        patch_prompt_profile="compact",
    )

    case_dir = Path(result["case_dir"])
    assert result["terminal_status"] == "still_open"
    assert (case_dir / "repair_diagnosis_request.json").is_file()
    assert (case_dir / "repair_diagnosis_validation.json").is_file()
    assert not (case_dir / "patch_request.json").exists()
    diagnosis_request = json.loads((case_dir / "repair_diagnosis_request.json").read_text(encoding="utf-8"))
    diagnosis_validation = json.loads((case_dir / "repair_diagnosis_validation.json").read_text(encoding="utf-8"))
    attempts = json.loads((case_dir / "patch_attempts_ledger.json").read_text(encoding="utf-8"))
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    assert diagnosis_request["schema"] == "pdf_lab.second_pass.opencode_repair_diagnosis_request.v1"
    assert "Do not edit files" in diagnosis_request["prompt"]
    assert diagnosis_validation["errors"] == ["repair_diagnosis_dry_run"]
    assert attempts["repair_strategy"] == "split"
    assert attempts["attempts"][0]["diagnosis_request_artifact"] == "patch_attempt_01_diagnosis_request.json"
    assert ledger["reason"] == "repair_diagnosis_dry_run"
    assert "repair_diagnosis_request.json" in ledger["evidence_artifacts"]
    assert "repair_diagnosis_validation.json" in ledger["evidence_artifacts"]
    assert "patch_request.json" not in ledger["evidence_artifacts"]


def test_chat_plan_split_feeds_repair_plan_into_patch_prompt(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()
    captured_patch_request: dict = {}

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "unknown_region",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "broken table-like content",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_preflight(**kwargs):
        return {
            "schema": "pdf_lab.second_pass.scillm_preflight.v1",
            "surface": kwargs["surface"],
            "base_url": kwargs["base_url"],
            "caller_skill": kwargs["caller_skill"],
            "checks": [],
            "ok": True,
            "errors": [],
        }

    def fake_repair_plan(repair_plan_request, **kwargs):
        return {
            "schema": "pdf_lab.second_pass.scillm_repair_plan_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 200,
            "scillm_metadata": repair_plan_request["scillm_metadata"],
            "raw_response": {"choices": [{"message": {"content": "{}"}}]},
            "repair_plan": {
                "schema": "pdf_lab.second_pass.repair_plan.v1",
                "summary": "classify table-like unknown layout",
                "suspected_fault": "unknown layout classifier misses table-like content",
                "patch_targets": ["python/pdf_oxide/classifier.py", "tests/test_classifier.py"],
                "test_plan": ["add focused regression"],
                "patch_constraints": ["smallest safe change", "no generated artifacts"],
                "confidence": "medium",
            },
        }

    def fake_call_opencode_patch(patch_request, **kwargs):
        captured_patch_request.update(patch_request)
        return {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "endpoint": "POST /v1/scillm/opencode/runs",
            "http_status": 200,
            "request_metadata": patch_request["scillm_metadata"],
            "raw_response": {
                "status": "timeout",
                "assistant_text": "",
                "diff": [],
                "artifacts": {},
            },
        }

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "preflight_scillm_surface", fake_preflight)
    monkeypatch.setattr(dag, "call_scillm_repair_plan", fake_repair_plan)
    monkeypatch.setattr(dag, "call_opencode_patch", fake_call_opencode_patch)
    monkeypatch.setattr(dag, "git_changed_files", lambda repo=dag.REPO: [])

    fixture_path = tmp_path / "review_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "page_rationale": "visual table is not represented by extracted structure",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "defect",
                        "evidence": "annotated region contains table-like content",
                        "rationale": "extraction emits unknown layout instead of table/list structure",
                        "suggested_fix_surface": "python/pdf_oxide presets or extractor classifier",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-chat-plan",
        review_mode="fixture",
        review_fixture_path=fixture_path,
        patch_mode="live",
        scillm_preflight_mode="live",
        repair_strategy="chat_plan_split",
        patch_prompt_profile="compact",
    )

    case_dir = Path(result["case_dir"])
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    attempts = json.loads((case_dir / "patch_attempts_ledger.json").read_text(encoding="utf-8"))
    assert result["terminal_status"] == "blocked_substrate"
    assert ledger["reason"] == "patch_delegate_timeout"
    bug_report = json.loads((case_dir / "scillm_patch_delegate_bug_report.json").read_text(encoding="utf-8"))
    assert "scillm_patch_delegate_bug_report.json" in ledger["evidence_artifacts"]
    assert bug_report["schema"] == "pdf_lab.second_pass.scillm_patch_delegate_bug_report.v1"
    assert bug_report["terminal_reason"] == "patch_delegate_timeout"
    assert bug_report["observed"]["validation_errors"]
    assert bug_report["artifacts"]["request"] == "patch_request.json"
    assert (case_dir / "repair_plan_request.json").is_file()
    assert (case_dir / "repair_plan_request_validation.json").is_file()
    assert (case_dir / "repair_plan_receipt.json").is_file()
    assert (case_dir / "repair_plan_validation.json").is_file()
    assert "repair_plan_request_validation.json" in ledger["evidence_artifacts"]
    assert "repair_plan_receipt.json" in ledger["evidence_artifacts"]
    assert attempts["repair_strategy"] == "chat_plan_split"
    assert attempts["attempts"][0]["repair_plan_request_validation_artifact"] == "patch_attempt_01_repair_plan_request_validation.json"
    assert attempts["attempts"][0]["repair_plan_receipt_artifact"] == "patch_attempt_01_repair_plan_receipt.json"
    assert "classify table-like unknown layout" in captured_patch_request["prompt"]
    assert "unknown layout classifier misses table-like content" in captured_patch_request["prompt"]


def test_fixture_review_can_drive_defect_patch_branch_without_live_review(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "unknown_region",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "broken table-like content",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fail_live_review(*args, **kwargs):
        raise AssertionError("fixture review must not call scillm chat")

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_scillm_review", fail_live_review)

    fixture_path = tmp_path / "review_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_fixture.v1",
                "review_response": {
                    "schema": "pdf_lab.second_pass.review_response.v1",
                    "page_status": "defect",
                    "page_rationale": "visual table is not represented by extracted structure",
                    "candidate_findings": [
                        {
                            "candidate_id": "cand:p0003:0000:unknown_layout",
                            "status": "defect",
                            "evidence": "annotated region contains table-like content",
                            "rationale": "extraction emits unknown layout instead of table/list structure",
                            "suggested_fix_surface": "python/pdf_oxide presets or extractor classifier",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-fixture",
        review_mode="fixture",
        review_fixture_path=fixture_path,
        patch_mode="dry_run",
    )

    case_dir = Path(result["case_dir"])
    assert result["terminal_status"] == "still_open"
    assert (case_dir / "review_fixture.json").is_file()
    assert not (case_dir / "scillm_review_receipt.json").exists()
    assert (case_dir / "patch_request.json").is_file()
    validation = json.loads((case_dir / "review_validation.json").read_text(encoding="utf-8"))
    assert validation["ok"] is True
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    assert ledger["reason"] == "patch_delegate_dry_run"
    assert "review_fixture.json" in ledger["evidence_artifacts"]
    with zipfile.ZipFile(case_dir / "review_bundle.zip") as bundle:
        assert "review_fixture.json" in bundle.namelist()


def test_fixture_review_clean_cannot_prove_page_clean(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [{"id": "actual:p3:block:0", "type": "table", "bbox": [0.1, 0.2, 0.8, 0.4], "text": "A | B"}],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)

    fixture_path = tmp_path / "clean_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean",
                "page_rationale": "fixture says clean",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:table",
                        "status": "clean",
                        "evidence": "fixture evidence",
                        "rationale": "fixture rationale",
                        "suggested_fix_surface": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:table",
                "page_number": 3,
                "preset_type": "table",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "table"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:table"],
                "strata": ["preset:table"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-fixture-clean",
        review_mode="fixture",
        review_fixture_path=fixture_path,
    )

    case_dir = Path(result["case_dir"])
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    assert result["terminal_status"] == "human_needed"
    assert ledger["reason"] == "fixture_review_cannot_prove_clean"


def test_live_patch_delegate_failure_writes_blocked_substrate_bundle(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "unknown_region",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "broken table-like content",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_call_scillm_review(review_request, **kwargs):
        return {
            "schema": "pdf_lab.second_pass.scillm_review_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 200,
            "scillm_metadata": review_request["scillm_metadata"],
            "raw_response": {"choices": [{"message": {"content": "{}"}}]},
            "review_response": {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "page_rationale": "visual table is not represented by extracted structure",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "defect",
                        "evidence": "annotated region contains table-like content",
                        "rationale": "extraction emits unknown layout instead of table/list structure",
                        "suggested_fix_surface": "python/pdf_oxide presets or extractor classifier",
                    }
                ],
            },
        }

    def fake_preflight(**kwargs):
        assert kwargs["surface"] in {"chat", "opencode_serve"}
        return {
            "schema": "pdf_lab.second_pass.scillm_preflight.v1",
            "surface": kwargs["surface"],
            "base_url": kwargs["base_url"],
            "caller_skill": kwargs["caller_skill"],
            "checks": [{"path": "/health/liveliness", "http_status": 200, "payload": {"status": "ok"}}],
            "ok": True,
            "errors": [],
        }

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_scillm_review", fake_call_scillm_review)
    monkeypatch.setattr(dag, "preflight_scillm_surface", fake_preflight)
    monkeypatch.setattr(dag, "git_changed_files", lambda repo=dag.REPO: [])
    monkeypatch.setattr(dag, "call_opencode_patch", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("opencode down")))

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-defect",
        review_mode="live",
        patch_mode="live",
        scillm_preflight_mode="live",
    )

    case_dir = Path(result["case_dir"])
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    patch_validation = json.loads((case_dir / "patch_validation.json").read_text(encoding="utf-8"))
    patch_error = json.loads((case_dir / "patch_error.json").read_text(encoding="utf-8"))
    assert result["terminal_status"] == "blocked_substrate"
    assert ledger["terminal_status"] == "blocked_substrate"
    assert ledger["reason"] == "patch_delegate_call_failed"
    assert "scillm_patch_preflight.json" in ledger["evidence_artifacts"]
    assert "patch_error.json" in ledger["evidence_artifacts"]
    assert patch_validation["errors"] == ["patch_delegate_call_failed"]
    assert patch_error["error_type"] == "RuntimeError"
    assert patch_error["preflight_artifact"] == "scillm_patch_preflight.json"
    with zipfile.ZipFile(case_dir / "review_bundle.zip") as bundle:
        assert "scillm_patch_preflight.json" in bundle.namelist()
        assert "patch_error.json" in bundle.namelist()
        assert "review.html" in bundle.namelist()


def test_live_patch_prompt_contract_failure_blocks_before_opencode(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "unknown_region",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "broken table-like content",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def invalid_patch_prompt(**kwargs):
        return "Analyze relevant information and fix it."

    def fail_preflight(*args, **kwargs):
        raise AssertionError("prompt contract gate must run before scillm patch preflight")

    def fail_opencode(*args, **kwargs):
        raise AssertionError("prompt contract gate must run before OpenCode")

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "build_patch_worker_prompt", invalid_patch_prompt)
    monkeypatch.setattr(dag, "preflight_scillm_surface", fail_preflight)
    monkeypatch.setattr(dag, "call_opencode_patch", fail_opencode)
    monkeypatch.setattr(dag, "git_changed_files", lambda repo=dag.REPO: [])

    fixture_path = tmp_path / "review_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "page_rationale": "fixture opens the patch branch",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "defect",
                        "evidence": "fixture evidence",
                        "rationale": "fixture rationale",
                        "suggested_fix_surface": "python/pdf_oxide classifier",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-prompt-contract-fail",
        review_mode="fixture",
        review_fixture_path=fixture_path,
        patch_mode="live",
        scillm_preflight_mode="live",
    )

    case_dir = Path(result["case_dir"])
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    patch_validation = json.loads((case_dir / "patch_validation.json").read_text(encoding="utf-8"))
    prompt_contract = json.loads((case_dir / "patch_attempt_01_prompt_contract.json").read_text(encoding="utf-8"))
    attempts = json.loads((case_dir / "patch_attempts_ledger.json").read_text(encoding="utf-8"))

    assert result["terminal_status"] == "still_open"
    assert ledger["reason"] == "patch_prompt_contract_failed"
    assert patch_validation["patch_status"] == "prompt_contract_failed"
    assert patch_validation["errors"][0] == "patch_prompt_contract_failed"
    assert prompt_contract["ok"] is False
    assert "relevant" in prompt_contract["banned_weasel_words"]
    assert "patch_attempt_01_prompt_contract.json" in ledger["evidence_artifacts"]
    assert "patch_attempt_01_prompt_review_payload.txt" in ledger["evidence_artifacts"]
    assert attempts["attempts"][0]["prompt_contract_artifacts"] == [
        "patch_attempt_01_prompt_contract.json",
        "patch_attempt_01_prompt_review_payload.txt",
    ]
    assert not (case_dir / "scillm_patch_preflight.json").exists()
    assert not (case_dir / "patch_receipt.json").exists()
    with zipfile.ZipFile(case_dir / "review_bundle.zip") as bundle:
        assert "patch_attempt_01_prompt_contract.json" in bundle.namelist()
        assert "patch_attempt_01_prompt_review_payload.txt" in bundle.namelist()


def test_run_page_case_defect_can_use_scillm_orchestrator_backend(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "unknown_region",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "broken table-like content",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_call_scillm_review(review_request, **kwargs):
        return {
            "schema": "pdf_lab.second_pass.scillm_review_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 200,
            "scillm_metadata": review_request["scillm_metadata"],
            "raw_response": {"choices": [{"message": {"content": "{}"}}]},
            "review_response": {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "page_rationale": "visual table is not represented by extracted structure",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "defect",
                        "evidence": "annotated region contains table-like content",
                        "rationale": "extraction emits unknown layout instead of table/list structure",
                        "suggested_fix_surface": "python/pdf_oxide presets or extractor classifier",
                    }
                ],
            },
        }

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_scillm_review", fake_call_scillm_review)

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-defect",
        review_mode="live",
        patch_mode="dry_run",
        patch_backend="scillm_orchestrator",
        opencode_model="gpt-5.5",
    )

    case_dir = Path(result["case_dir"])
    patch_request = json.loads((case_dir / "patch_request.json").read_text(encoding="utf-8"))
    assert patch_request["endpoint"] == "POST /v1/scillm/opencode/transport/runs + children + message"
    assert patch_request["create_run_body"]["dag_node_id"] == "pdf_lab_second_pass_patch:page_case_0001_p0003"
    assert patch_request["create_child_body"]["mode"] == "apply_patches"
    assert patch_request["opencode_model"] == "gpt-5.5"
    assert patch_request["message_body"]["model"] == "gpt-5.5"
    assert str(case_dir.resolve()) in patch_request["message_body"]["prompt"]
    assert "PATCH_APPLIED" in patch_request["message_body"]["prompt"]
    assert "PATCH_DELEGATE_BLOCKED" in patch_request["message_body"]["prompt"]
    assert (case_dir / "patch_attempt_01_prompt_contract.json").is_file()
    assert (case_dir / "patch_attempt_01_prompt_review_payload.txt").is_file()
    assert patch_request["message_body"]["stream"] is True
    assert patch_request["message_body"]["heartbeat_s"] == 15
    dag_spec = json.loads((case_dir / "scillm_orchestrator_page_dag_spec.json").read_text(encoding="utf-8"))
    dag_spec_validation = json.loads((case_dir / "scillm_orchestrator_page_dag_spec_validation.json").read_text(encoding="utf-8"))
    submission = json.loads((case_dir / "scillm_orchestrator_page_submission.json").read_text(encoding="utf-8"))
    submission_validation = json.loads((case_dir / "scillm_orchestrator_page_submission_validation.json").read_text(encoding="utf-8"))
    assert dag_spec_validation["ok"] is True
    patch_node = next(node for node in dag_spec["nodes"] if node["node_id"] == "patch_delegate_attempts")
    assert all(node["state_owner"] == "scillm_orchestrator" for node in dag_spec["nodes"])
    assert patch_node["runtime_owner"] == "scillm_orchestrator"
    assert patch_node["surface"] == "scillm_opencode_transport"
    assert patch_node["endpoint"] == "POST /v1/scillm/opencode/transport/runs + children + message"
    assert "X-Caller-Skill" in patch_node["required_headers"]
    assert submission["dag_spec_sha256"] == submission_validation["dag_spec_sha256"]
    assert submission_validation["ok"] is True
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    assert ledger["terminal_status"] == "still_open"
    assert ledger["reason"] == "patch_delegate_dry_run"
    assert ledger["commit_sha"] is None
    assert "scillm_orchestrator_page_dag_spec.json" in ledger["evidence_artifacts"]


def test_page_orchestrator_live_registration_reuses_parent_transport_for_patch(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "unknown_region",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "broken table-like content",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_call_page_orchestrator_run(request, **kwargs):
        assert request["endpoint"] == "POST /v1/scillm/opencode/transport/runs"
        assert request["create_run_body"]["dag_node_id"] == "pdf_lab_second_pass_page:page_case_0001_p0003"
        assert request["target_dag_state_owner"] == "scillm_orchestrator"
        assert request["dag_spec_sha256"]
        return {
            "schema": "pdf_lab.second_pass.page_orchestrator_run_receipt.v1",
            "endpoint": "POST /v1/scillm/opencode/transport/runs",
            "http_status": 200,
            "request_metadata": request["scillm_metadata"],
            "transport_run_id": "tr-page-0003",
            "create_response": {
                "transport_run_id": "tr-page-0003",
                "observation": {
                    "schema": "scillm.opencode_transport.observation.v1",
                    "browser_dialog_url": "http://127.0.0.1:4098/session/parent",
                },
            },
            "observation": {
                "schema": "scillm.opencode_transport.observation.v1",
                "browser_dialog_url": "http://127.0.0.1:4098/session/parent",
            },
        }

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_page_orchestrator_run", fake_call_page_orchestrator_run)
    monkeypatch.setattr(dag, "git_changed_files", lambda repo=dag.REPO: [])

    fixture_path = tmp_path / "review_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "page_rationale": "visual table is not represented by extracted structure",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "defect",
                        "evidence": "annotated region contains table-like content",
                        "rationale": "extraction emits unknown layout instead of table/list structure",
                        "suggested_fix_surface": "python/pdf_oxide presets or extractor classifier",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-defect",
        review_mode="fixture",
        review_fixture_path=fixture_path,
        patch_mode="dry_run",
        patch_backend="scillm_orchestrator",
        opencode_model="gpt-5.5",
        page_orchestrator_mode="live",
    )

    case_dir = Path(result["case_dir"])
    state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
    run_validation = json.loads((case_dir / "scillm_page_orchestrator_run_validation.json").read_text(encoding="utf-8"))
    patch_request = json.loads((case_dir / "patch_request.json").read_text(encoding="utf-8"))
    submission = json.loads((case_dir / "scillm_orchestrator_page_submission.json").read_text(encoding="utf-8"))
    submission_validation = json.loads((case_dir / "scillm_orchestrator_page_submission_validation.json").read_text(encoding="utf-8"))
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))

    assert submission["schema"] == "pdf_lab.second_pass.scillm_orchestrator_page_submission.v1"
    assert submission_validation["ok"] is True
    assert submission["dag_spec_sha256"] == submission_validation["dag_spec_sha256"]
    assert run_validation["ok"] is True
    assert run_validation["registered"] is True
    assert run_validation["transport_run_id"] == "tr-page-0003"
    assert state["page_orchestrator_transport_run_id"] == "tr-page-0003"
    assert patch_request["transport_run_id"] == "tr-page-0003"
    assert patch_request["create_run_body"]["transport_run_id"] == "tr-page-0003"
    assert "scillm_page_orchestrator_run_receipt.json" in ledger["evidence_artifacts"]
    assert "scillm_page_orchestrator_run_validation.json" in ledger["evidence_artifacts"]


def test_transport_patch_remote_protocol_failure_retries_with_fresh_parent(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()
    patch_transport_run_ids: list[str | None] = []
    retry_flags: list[bool] = []
    git_status_calls = {"count": 0}

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "unknown_region",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "broken table-like content",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_call_page_orchestrator_run(request, **kwargs):
        return {
            "schema": "pdf_lab.second_pass.page_orchestrator_run_receipt.v1",
            "endpoint": "POST /v1/scillm/opencode/transport/runs",
            "http_status": 200,
            "request_metadata": request["scillm_metadata"],
            "transport_run_id": "tr-page-0003",
            "create_response": {"transport_run_id": "tr-page-0003"},
            "observation": {"schema": "scillm.opencode_transport.observation.v1"},
        }

    def fake_call_scillm_orchestrator_patch(patch_request, **kwargs):
        patch_transport_run_ids.append(patch_request.get("transport_run_id"))
        retry_flags.append(bool(patch_request.get("transport_retry_fresh_parent")))
        if len(patch_transport_run_ids) == 1:
            event_stream = dag.build_transport_session_error_stream(
                "RemoteProtocolError",
                "peer closed connection without sending complete message body (incomplete chunked read)",
            )
            return {
                "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
                "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
                "http_status": 200,
                "request_metadata": patch_request["scillm_metadata"],
                "transport_run_id": "tr-page-0003",
                "event_stream": event_stream,
                "message_response": event_stream["final_result"],
            }
        return {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
            "http_status": 200,
            "request_metadata": patch_request["scillm_metadata"],
            "transport_run_id": "tr-fresh-retry",
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 1,
                "event_type_counts": {"message.completed": 1},
                "events": [
                    {
                        "event_type": "message.completed",
                        "result": {
                            "delivery_state": "completed",
                            "assistant_text": "PATCH_APPLIED changed_files=tests/test_fix.py tests=tests/test_fix.py commands=not-run",
                            "diff": "diff --git a/tests/test_fix.py b/tests/test_fix.py",
                        },
                    }
                ],
                "raw_line_count": 1,
                "final_result": {
                    "delivery_state": "completed",
                    "assistant_text": "PATCH_APPLIED changed_files=tests/test_fix.py tests=tests/test_fix.py commands=not-run",
                    "diff": "diff --git a/tests/test_fix.py b/tests/test_fix.py",
                },
                "delivery_state": "completed",
                "saw_message_completed": True,
                "tool_errors": [],
                "session_errors": [],
                "permission_requests": [],
                "parse_errors": [],
            },
            "message_response": {
                "delivery_state": "completed",
                "assistant_text": "PATCH_APPLIED changed_files=tests/test_fix.py tests=tests/test_fix.py commands=not-run",
                "diff": "diff --git a/tests/test_fix.py b/tests/test_fix.py",
            },
        }

    def fake_git_changed_files(repo=dag.REPO):
        git_status_calls["count"] += 1
        return [] if git_status_calls["count"] == 1 else ["tests/test_fix.py"]

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_page_orchestrator_run", fake_call_page_orchestrator_run)
    monkeypatch.setattr(dag, "call_scillm_orchestrator_patch", fake_call_scillm_orchestrator_patch)
    monkeypatch.setattr(dag, "git_changed_files", fake_git_changed_files)

    fixture_path = tmp_path / "review_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "page_rationale": "visual table is not represented by extracted structure",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "defect",
                        "evidence": "annotated region contains table-like content",
                        "rationale": "extraction emits unknown layout instead of table/list structure",
                        "suggested_fix_surface": "tests/test_fix.py",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-defect",
        review_mode="fixture",
        review_fixture_path=fixture_path,
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        opencode_model="gpt-5.5",
        scillm_preflight_mode="dry_run",
        page_orchestrator_mode="live",
    )

    case_dir = Path(result["case_dir"])
    attempts = json.loads((case_dir / "patch_attempts_ledger.json").read_text(encoding="utf-8"))
    first_request = json.loads((case_dir / "patch_attempt_01_request.json").read_text(encoding="utf-8"))
    second_request = json.loads((case_dir / "patch_attempt_02_request.json").read_text(encoding="utf-8"))

    assert patch_transport_run_ids == ["tr-page-0003", None]
    assert retry_flags == [False, True]
    assert attempts["agent_sequence"] == ["build", "build"]
    assert attempts["attempt_count"] == 2
    assert attempts["selected_attempt_index"] == 2
    assert attempts["attempts"][0]["ok"] is False
    assert attempts["attempts"][1]["ok"] is True
    assert first_request["transport_retry_fresh_parent"] is False
    assert second_request["transport_retry_fresh_parent"] is True
    assert first_request["transport_run_id"] == "tr-page-0003"
    assert second_request["transport_run_id"] is None


def test_parse_transport_sse_response_records_message_completed() -> None:
    dag = _load_module()

    class FakeResponse:
        text = ""

        def iter_lines(self):
            yield "data: {\"event_type\":\"heartbeat\",\"delivery_state\":\"running\"}"
            yield "data: {\"event_type\":\"message.completed\",\"result\":{\"delivery_state\":\"completed\",\"diff\":\"diff --git\"}}"
            yield "data: [DONE]"

    parsed = dag.parse_transport_sse_response(FakeResponse())

    assert parsed["schema"] == "pdf_lab.second_pass.scillm_transport_event_stream.v1"
    assert parsed["event_count"] == 2
    assert parsed["delivery_state"] == "completed"
    assert parsed["saw_message_completed"] is True
    assert parsed["final_result"]["diff"] == "diff --git"


def test_validate_transport_patch_receipt_requires_stream_completion() -> None:
    dag = _load_module()

    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "message_response": {"delivery_state": "completed"},
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 1,
                "saw_message_completed": False,
            },
        },
        patch_mode="live",
    )

    assert validation["ok"] is False
    assert any("message.completed" in error for error in validation["errors"])


def test_validate_patch_delegate_receipt_rejects_stale_opencode_request_metadata() -> None:
    dag = _load_module()
    request = {
        "scillm_metadata": {
            "graph_node": "opencode_patch_attempt",
            "case_id": "page_case_0001_p0003",
            "page_number": 3,
            "attempt_index": 2,
            "attempt_count": 3,
            "agent": "build",
            "transport_retry_fresh_parent": False,
        }
    }
    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "request_metadata": {
                "graph_node": "opencode_patch_attempt",
                "case_id": "page_case_9999_p9999",
                "page_number": 9999,
                "attempt_index": 1,
                "attempt_count": 3,
                "agent": "review",
                "transport_retry_fresh_parent": True,
            },
            "raw_response": {
                "status": "completed",
                "assistant_text": "PATCH_APPLIED changed_files=tests/test_fix.py tests=tests/test_fix.py commands=pytest",
                "diff": "diff --git a/tests/test_fix.py b/tests/test_fix.py",
                "artifacts": {},
            },
        },
        patch_mode="live",
        request=request,
    )

    assert validation["ok"] is False
    assert "patch receipt request_metadata case_id does not match request" in validation["errors"]
    assert "patch receipt request_metadata page_number does not match request" in validation["errors"]
    assert "patch receipt request_metadata attempt_index does not match request" in validation["errors"]
    assert "patch receipt request_metadata agent does not match request" in validation["errors"]
    assert "patch receipt request_metadata transport_retry_fresh_parent does not match request" in validation["errors"]


def test_validate_patch_delegate_receipt_rejects_wrong_opencode_surface_and_status() -> None:
    dag = _load_module()
    request = {
        "scillm_metadata": {
            "graph_node": "opencode_patch_attempt",
            "case_id": "page_case_0001_p0003",
            "page_number": 3,
            "attempt_index": 1,
            "attempt_count": 1,
            "agent": "build",
            "transport_retry_fresh_parent": False,
        }
    }
    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 202,
            "request_metadata": request["scillm_metadata"],
            "raw_response": {
                "status": "completed",
                "assistant_text": "PATCH_APPLIED changed_files=tests/test_fix.py tests=tests/test_fix.py commands=pytest",
                "diff": "diff --git a/tests/test_fix.py b/tests/test_fix.py",
                "artifacts": {},
            },
        },
        patch_mode="live",
        request=request,
    )

    assert validation["ok"] is False
    assert "OpenCode patch receipt endpoint mismatch" in validation["errors"]
    assert "OpenCode patch receipt http_status must be 200" in validation["errors"]


def test_validate_patch_delegate_receipt_rejects_stale_transport_request_metadata() -> None:
    dag = _load_module()
    request = {
        "scillm_metadata": {
            "graph_node": "scillm_orchestrator_patch_attempt",
            "case_id": "page_case_0001_p0003",
            "page_number": 3,
            "attempt_index": 1,
            "attempt_count": 2,
            "agent": "build",
            "transport_retry_fresh_parent": False,
        }
    }
    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "request_metadata": {
                "graph_node": "scillm_orchestrator_patch_attempt",
                "case_id": "page_case_0001_p0004",
                "page_number": 4,
                "attempt_index": 2,
                "attempt_count": 2,
                "agent": "review",
                "transport_retry_fresh_parent": True,
            },
            "message_response": {
                "delivery_state": "completed",
                "assistant_text": "PATCH_APPLIED changed_files=tests/test_fix.py tests=tests/test_fix.py commands=pytest",
                "diff": "diff --git a/tests/test_fix.py b/tests/test_fix.py",
            },
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 3,
                "saw_message_completed": True,
                "parse_errors": [],
                "session_errors": [],
                "tool_errors": [],
                "permission_requests": [],
            },
        },
        patch_mode="live",
        request=request,
    )

    assert validation["ok"] is False
    assert "patch receipt request_metadata case_id does not match request" in validation["errors"]
    assert "patch receipt request_metadata page_number does not match request" in validation["errors"]
    assert "patch receipt request_metadata attempt_index does not match request" in validation["errors"]
    assert "patch receipt request_metadata agent does not match request" in validation["errors"]
    assert "patch receipt request_metadata transport_retry_fresh_parent does not match request" in validation["errors"]


def test_validate_patch_delegate_receipt_rejects_wrong_transport_surface_and_status() -> None:
    dag = _load_module()
    request = {
        "scillm_metadata": {
            "graph_node": "scillm_orchestrator_patch_attempt",
            "case_id": "page_case_0001_p0003",
            "page_number": 3,
            "attempt_index": 1,
            "attempt_count": 1,
            "agent": "build",
            "transport_retry_fresh_parent": False,
        }
    }
    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 202,
            "request_metadata": request["scillm_metadata"],
            "message_response": {
                "delivery_state": "completed",
                "assistant_text": "PATCH_APPLIED changed_files=tests/test_fix.py tests=tests/test_fix.py commands=pytest",
                "diff": "diff --git a/tests/test_fix.py b/tests/test_fix.py",
            },
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 3,
                "saw_message_completed": True,
                "parse_errors": [],
                "session_errors": [],
                "tool_errors": [],
                "permission_requests": [],
            },
        },
        patch_mode="live",
        request=request,
    )

    assert validation["ok"] is False
    assert "transport patch receipt endpoint mismatch" in validation["errors"]
    assert "transport patch receipt http_status must be 200" in validation["errors"]


def test_validate_repair_diagnosis_receipt_rejects_stale_opencode_request_metadata() -> None:
    dag = _load_module()
    request = {
        "scillm_metadata": {
            "graph_node": "opencode_repair_diagnosis_attempt",
            "case_id": "page_case_0001_p0003",
            "page_number": 3,
            "attempt_index": 2,
            "attempt_count": 3,
            "agent": "build",
        }
    }
    validation = dag.validate_repair_diagnosis_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "request_metadata": {
                "graph_node": "opencode_repair_diagnosis_attempt",
                "case_id": "page_case_9999_p9999",
                "page_number": 9999,
                "attempt_index": 1,
                "attempt_count": 3,
                "agent": "review",
            },
            "raw_response": {
                "status": "completed",
                "assistant_text": "Diagnosis: classifier needs table-like block regression.",
            },
        },
        patch_mode="live",
        request=request,
    )

    assert validation["ok"] is False
    assert "repair diagnosis receipt request_metadata case_id does not match request" in validation["errors"]
    assert "repair diagnosis receipt request_metadata page_number does not match request" in validation["errors"]
    assert "repair diagnosis receipt request_metadata attempt_index does not match request" in validation["errors"]
    assert "repair diagnosis receipt request_metadata agent does not match request" in validation["errors"]


def test_validate_repair_diagnosis_receipt_rejects_wrong_opencode_surface_and_status() -> None:
    dag = _load_module()
    request = {
        "scillm_metadata": {
            "graph_node": "opencode_repair_diagnosis_attempt",
            "case_id": "page_case_0001_p0003",
            "page_number": 3,
            "attempt_index": 1,
            "attempt_count": 1,
            "agent": "build",
        }
    }
    validation = dag.validate_repair_diagnosis_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 202,
            "request_metadata": request["scillm_metadata"],
            "raw_response": {
                "status": "completed",
                "assistant_text": "Diagnosis: classifier needs table-like block regression.",
            },
        },
        patch_mode="live",
        request=request,
    )

    assert validation["ok"] is False
    assert "OpenCode diagnosis receipt endpoint mismatch" in validation["errors"]
    assert "OpenCode diagnosis receipt http_status must be 200" in validation["errors"]


def test_validate_repair_diagnosis_receipt_rejects_stale_transport_request_metadata() -> None:
    dag = _load_module()
    request = {
        "scillm_metadata": {
            "graph_node": "scillm_orchestrator_repair_diagnosis_attempt",
            "case_id": "page_case_0001_p0003",
            "page_number": 3,
            "attempt_index": 1,
            "attempt_count": 2,
            "agent": "build",
        }
    }
    validation = dag.validate_repair_diagnosis_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "request_metadata": {
                "graph_node": "scillm_orchestrator_repair_diagnosis_attempt",
                "case_id": "page_case_0001_p0004",
                "page_number": 4,
                "attempt_index": 2,
                "attempt_count": 2,
                "agent": "review",
            },
            "message_response": {
                "delivery_state": "completed",
                "assistant_text": "Diagnosis: read-only analysis completed.",
            },
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 3,
                "saw_message_completed": True,
                "parse_errors": [],
                "session_errors": [],
                "tool_errors": [],
            },
        },
        patch_mode="live",
        request=request,
    )

    assert validation["ok"] is False
    assert "repair diagnosis receipt request_metadata case_id does not match request" in validation["errors"]
    assert "repair diagnosis receipt request_metadata page_number does not match request" in validation["errors"]
    assert "repair diagnosis receipt request_metadata attempt_index does not match request" in validation["errors"]
    assert "repair diagnosis receipt request_metadata agent does not match request" in validation["errors"]


def test_validate_repair_diagnosis_receipt_rejects_wrong_transport_surface_and_status() -> None:
    dag = _load_module()
    request = {
        "scillm_metadata": {
            "graph_node": "scillm_orchestrator_repair_diagnosis_attempt",
            "case_id": "page_case_0001_p0003",
            "page_number": 3,
            "attempt_index": 1,
            "attempt_count": 1,
            "agent": "build",
        }
    }
    validation = dag.validate_repair_diagnosis_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 202,
            "request_metadata": request["scillm_metadata"],
            "message_response": {
                "delivery_state": "completed",
                "assistant_text": "Diagnosis: read-only analysis completed.",
            },
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 3,
                "saw_message_completed": True,
                "parse_errors": [],
                "session_errors": [],
                "tool_errors": [],
            },
        },
        patch_mode="live",
        request=request,
    )

    assert validation["ok"] is False
    assert "transport diagnosis receipt endpoint mismatch" in validation["errors"]
    assert "transport diagnosis receipt http_status must be 200" in validation["errors"]


def test_parse_transport_sse_response_records_message_failed_as_session_error() -> None:
    dag = _load_module()

    class FakeResponse:
        text = ""

        def iter_lines(self):
            yield "data: {\"event_type\":\"message.failed\",\"delivery_state\":\"failed\",\"error\":\"opencode serve timed out\"}"
            yield "data: [DONE]"

    parsed = dag.parse_transport_sse_response(FakeResponse())

    assert parsed["event_type_counts"]["message.failed"] == 1
    assert parsed["session_errors"][0]["error"] == "opencode serve timed out"


def test_parse_transport_sse_response_primary_deadline_is_session_error() -> None:
    dag = _load_module()

    class FakeResponse:
        def iter_lines(self):
            yield 'data: {"event_type":"heartbeat"}'

    parsed = dag.parse_transport_sse_response(FakeResponse(), max_elapsed_s=0)

    assert parsed["event_type_counts"]["session_error"] == 1
    assert parsed["session_errors"][0]["error_type"] == "stream_deadline_exceeded"


def test_parse_transport_sse_response_replay_deadline_is_not_session_error() -> None:
    dag = _load_module()

    class FakeResponse:
        def iter_lines(self):
            yield 'data: {"event_type":"heartbeat"}'

    parsed = dag.parse_transport_sse_response(
        FakeResponse(),
        max_elapsed_s=0,
        deadline_event_is_error=False,
    )

    assert parsed["event_type_counts"]["stream_deadline"] == 1
    assert parsed["session_errors"] == []


def test_merge_transport_event_streams_preserves_replayed_failure() -> None:
    dag = _load_module()

    merged = dag.merge_transport_event_streams(
        {
            "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
            "event_count": 1,
            "events": [
                {"event_type": "message.completed", "event_id": "complete-1", "result": {"delivery_state": "completed", "diff": []}},
            ],
            "final_result": {"delivery_state": "completed", "diff": []},
            "saw_message_completed": True,
        },
        {
            "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
            "event_count": 1,
            "events": [
                {"event_type": "message.failed", "event_id": "fail-1", "delivery_state": "failed", "error": "timeout"},
            ],
            "final_result": {},
            "saw_message_completed": False,
        },
    )

    assert merged["event_type_counts"]["message.completed"] == 1
    assert merged["event_type_counts"]["message.failed"] == 1
    assert merged["session_errors"][0]["error"] == "timeout"


def test_broken_transport_stream_becomes_session_error() -> None:
    dag = _load_module()

    event_stream = dag.build_transport_session_error_stream("RemoteProtocolError", "incomplete chunked read")
    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "message_response": event_stream["final_result"],
            "event_stream": event_stream,
        },
        patch_mode="live",
    )

    assert event_stream["delivery_state"] == "failed"
    assert event_stream["event_type_counts"]["session_error"] == 1
    assert validation["ok"] is False
    assert any("session_error" in error for error in validation["errors"])


def test_transport_timeout_validation_preserves_deadline_detail() -> None:
    dag = _load_module()

    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "message_response": {},
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 1,
                "saw_message_completed": False,
                "parse_errors": [],
                "session_errors": [
                    {
                        "event_type": "session_error",
                        "error_type": "stream_deadline_exceeded",
                        "error": "transport stream exceeded 270.0s parse deadline",
                    }
                ],
                "tool_errors": [],
                "permission_requests": [],
            },
        },
        patch_mode="live",
    )

    assert validation["ok"] is False
    assert any("stream_deadline_exceeded" in error for error in validation["errors"])
    assert dag.patch_validation_has_delegate_timeout(validation) is True


def test_opencode_serve_timeout_validation_is_timeout_classified() -> None:
    dag = _load_module()

    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "raw_response": {
                "status": "timeout",
                "assistant_text": "",
                "diff": [],
                "artifacts": {"status_json": "/tmp/status.json"},
            },
        },
        patch_mode="live",
    )

    assert validation["ok"] is False
    assert "OpenCode run timed out before producing a patch diff" in validation["errors"]
    assert dag.patch_validation_has_delegate_timeout(validation) is True


def test_validate_transport_patch_receipt_rejects_permission_requests() -> None:
    dag = _load_module()

    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "message_response": {"delivery_state": "completed"},
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 2,
                "saw_message_completed": True,
                "permission_requests": [{"event_type": "permission_requested", "tool": "bash"}],
            },
        },
        patch_mode="live",
    )

    assert validation["ok"] is False
    assert any("permission" in error for error in validation["errors"])


def test_validate_transport_patch_receipt_rejects_message_failed_event() -> None:
    dag = _load_module()

    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "message_response": {"delivery_state": "completed", "assistant_text": "I will patch next.", "diff": []},
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 3,
                "saw_message_completed": True,
                "session_errors": [
                    {"event_type": "message.failed", "delivery_state": "failed", "error": "opencode serve timed out"}
                ],
                "tool_errors": [],
                "parse_errors": [],
                "permission_requests": [],
            },
        },
        patch_mode="live",
    )

    assert validation["ok"] is False
    assert any("session_error" in error for error in validation["errors"])


def test_validate_transport_patch_receipt_rejects_nested_provider_error() -> None:
    dag = _load_module()

    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "message_response": {
                "delivery_state": "idle_seen",
                "assistant_text": "",
                "diff": [],
                "message": {
                    "info": {
                        "error": {
                            "name": "APIError",
                            "data": {
                                "message": "Bad Request: model is not supported",
                            },
                        }
                    }
                },
            },
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 2,
                "saw_message_completed": True,
                "parse_errors": [],
                "session_errors": [],
                "tool_errors": [],
                "permission_requests": [],
            },
        },
        patch_mode="live",
    )

    assert validation["ok"] is False
    assert any("worker/provider error" in error for error in validation["errors"])
    assert any("without assistant text or diff" in error for error in validation["errors"])


def test_validate_transport_patch_receipt_rejects_text_only_completion() -> None:
    dag = _load_module()

    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "message_response": {
                "delivery_state": "completed",
                "assistant_text": "I will inspect the workspace before editing.",
                "diff": [],
            },
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 3,
                "saw_message_completed": True,
                "parse_errors": [],
                "session_errors": [],
                "tool_errors": [],
                "permission_requests": [],
            },
        },
        patch_mode="live",
    )

    assert validation["ok"] is False
    assert any("missing PATCH_APPLIED/PATCH_DELEGATE_BLOCKED sentinel" in error for error in validation["errors"])
    assert any("produced no diff" in error for error in validation["errors"])


def test_transport_patch_failure_predicate_accepts_remote_protocol_drop() -> None:
    dag = _load_module()

    assert dag.patch_validation_has_recoverable_transport_failure(
        {
            "schema": "pdf_lab.second_pass.patch_delegate_validation.v1",
            "ok": False,
            "errors": [
                "transport stream did not include message.completed",
                "transport session_error RemoteProtocolError: peer closed connection without sending complete message body (incomplete chunked read)",
            ],
        }
    ) is True
    assert dag.patch_validation_has_recoverable_transport_failure(
        {
            "schema": "pdf_lab.second_pass.patch_delegate_validation.v1",
            "ok": False,
            "errors": ["transport patch delegate response missing PATCH_APPLIED/PATCH_DELEGATE_BLOCKED sentinel"],
        }
    ) is False


def test_validate_patch_receipts_reject_diff_without_terminal_sentinel() -> None:
    dag = _load_module()
    opencode_validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "raw_response": {
                "status": "completed",
                "assistant_text": "",
                "diff": "diff --git a/tests/test_fix.py b/tests/test_fix.py",
                "artifacts": {},
            },
        },
        patch_mode="live",
    )
    transport_validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "message_response": {
                "delivery_state": "completed",
                "assistant_text": "",
                "diff": "diff --git a/tests/test_fix.py b/tests/test_fix.py",
            },
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 3,
                "saw_message_completed": True,
                "parse_errors": [],
                "session_errors": [],
                "tool_errors": [],
                "permission_requests": [],
            },
        },
        patch_mode="live",
    )

    assert opencode_validation["ok"] is False
    assert "OpenCode patch delegate produced no assistant_text terminal sentinel" in opencode_validation["errors"]
    assert transport_validation["ok"] is False
    assert "transport patch delegate produced no assistant_text terminal sentinel" in transport_validation["errors"]


def test_parse_patch_applied_claim_extracts_files_tests_and_commands() -> None:
    dag = _load_module()

    claim = dag.parse_patch_applied_claim(
        "PATCH_APPLIED changed_files=python/pdf_oxide/extract_for_pdflab.py,tests/test_fix.py "
        "tests=tests/test_fix.py commands=pytest tests/test_fix.py -q"
    )

    assert claim["errors"] == []
    assert claim["changed_files"] == ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"]
    assert claim["tests"] == ["tests/test_fix.py"]
    assert claim["commands"] == "pytest tests/test_fix.py -q"


def test_validate_patch_receipt_rejects_malformed_patch_applied_claim() -> None:
    dag = _load_module()

    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "raw_response": {
                "status": "completed",
                "assistant_text": "PATCH_APPLIED",
                "diff": "diff --git a/tests/test_fix.py b/tests/test_fix.py",
                "artifacts": {},
            },
        },
        patch_mode="live",
    )

    assert validation["ok"] is False
    assert "PATCH_APPLIED missing changed_files= field" in validation["errors"]
    assert validation["applied_claim"]["errors"]


def test_validate_opencode_patch_receipt_rejects_progress_text_without_sentinel() -> None:
    dag = _load_module()

    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "raw_response": {
                "status": "timeout",
                "assistant_text": "Checking the workspace first, then I will apply the patch.",
                "diff": [],
                "artifacts": {},
            },
        },
        patch_mode="live",
    )

    assert validation["ok"] is False
    assert any("missing PATCH_APPLIED/PATCH_DELEGATE_BLOCKED sentinel" in error for error in validation["errors"])
    assert any("timed out before producing a patch diff" in error for error in validation["errors"])


def test_validate_opencode_patch_receipt_rejects_incomplete_tool_loop() -> None:
    dag = _load_module()

    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "raw_response": {
                "status": "timeout",
                "assistant_text": "Checking the workspace and required evidence first.",
                "diff": [],
                "artifacts": {},
                "message": {
                    "info": {"finish": "tool-calls"},
                    "parts": [
                        {"type": "step-start"},
                        {"type": "text", "text": "Checking the workspace and required evidence first."},
                        {"type": "tool", "tool": "bash", "state": {"status": "completed"}},
                        {"type": "step-finish", "reason": "tool-calls"},
                    ],
                },
            },
        },
        patch_mode="live",
    )

    assert validation["ok"] is False
    assert any("stopped after tool call without terminal sentinel or diff" in error for error in validation["errors"])


def test_validate_transport_patch_receipt_rejects_incomplete_tool_loop() -> None:
    dag = _load_module()

    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "message_response": {
                "delivery_state": "completed",
                "assistant_text": "Checking the workspace and required evidence first.",
                "diff": [],
                "message": {
                    "info": {"finish": "tool-calls"},
                    "parts": [
                        {"type": "step-start"},
                        {"type": "text", "text": "Checking the workspace and required evidence first."},
                        {"type": "tool", "tool": "bash", "state": {"status": "completed"}},
                        {"type": "step-finish", "reason": "tool-calls"},
                    ],
                },
            },
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 4,
                "saw_message_completed": True,
                "parse_errors": [],
                "session_errors": [],
                "tool_errors": [],
                "permission_requests": [],
            },
        },
        patch_mode="live",
    )

    assert validation["ok"] is False
    assert any("stopped after tool call without terminal sentinel or diff" in error for error in validation["errors"])


def test_validate_transport_patch_receipt_rejects_explicit_blocked_marker() -> None:
    dag = _load_module()

    validation = dag.validate_patch_delegate_receipt(
        {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "message_response": {
                "delivery_state": "completed",
                "assistant_text": "PATCH_DELEGATE_BLOCKED: workspace is inaccessible",
                "diff": [],
            },
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 3,
                "saw_message_completed": True,
                "parse_errors": [],
                "session_errors": [],
                "tool_errors": [],
                "permission_requests": [],
            },
        },
        patch_mode="live",
    )

    assert validation["ok"] is False
    assert any("blocked substrate" in error for error in validation["errors"])


def test_orchestrator_patch_writes_transport_event_artifacts(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "unknown_region",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "broken table-like content",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_call_scillm_review(review_request, **kwargs):
        return {
            "schema": "pdf_lab.second_pass.scillm_review_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 200,
            "scillm_metadata": review_request["scillm_metadata"],
            "raw_response": {"choices": [{"message": {"content": "{}"}}]},
            "review_response": {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "page_rationale": "visual table is not represented by extracted structure",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "defect",
                        "evidence": "annotated region contains table-like content",
                        "rationale": "extraction emits unknown layout instead of table/list structure",
                        "suggested_fix_surface": "python/pdf_oxide presets or extractor classifier",
                    }
                ],
            },
        }

    def fake_call_orchestrator_patch(patch_request, **kwargs):
        return {
            "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
            "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
            "http_status": 200,
            "request_metadata": patch_request["scillm_metadata"],
            "transport_run_id": "tr-test",
            "create_response": {"transport_run_id": "tr-test"},
            "child_response": {"child_id": "child-test"},
            "event_stream": {
                "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
                "event_count": 2,
                "event_type_counts": {"heartbeat": 1, "message.completed": 1},
                "events": [
                    {"event_type": "heartbeat", "delivery_state": "running"},
                    {"event_type": "message.completed", "result": {"delivery_state": "completed", "diff": "diff --git"}},
                ],
                "raw_line_count": 2,
                "final_result": {"delivery_state": "completed", "diff": "diff --git"},
                "delivery_state": "completed",
                "saw_message_completed": True,
                "tool_errors": [],
                "session_errors": [],
                "permission_requests": [],
                "parse_errors": [],
            },
            "message_response": {"delivery_state": "completed", "diff": "diff --git"},
            "observation": None,
        }

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_scillm_review", fake_call_scillm_review)
    monkeypatch.setattr(dag, "call_scillm_orchestrator_patch", fake_call_orchestrator_patch)
    monkeypatch.setattr(dag, "git_changed_files", lambda repo=dag.REPO: [])

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-defect",
        review_mode="live",
        patch_mode="live",
        patch_backend="scillm_orchestrator",
    )

    case_dir = Path(result["case_dir"])
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    assert result["terminal_status"] == "still_open"
    assert (case_dir / "transport_event_stream.json").is_file()
    assert (case_dir / "transport_events.jsonl").is_file()
    assert "transport_event_stream.json" in ledger["evidence_artifacts"]
    assert "transport_events.jsonl" in ledger["evidence_artifacts"]
    with zipfile.ZipFile(case_dir / "review_bundle.zip") as bundle:
        assert "transport_event_stream.json" in bundle.namelist()
        assert "transport_events.jsonl" in bundle.namelist()


def test_patch_agent_sequence_retries_until_receipt_validation_passes(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()
    attempted_agents: list[str] = []

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "unknown_region",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "broken table-like content",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_call_scillm_review(review_request, **kwargs):
        return {
            "schema": "pdf_lab.second_pass.scillm_review_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 200,
            "scillm_metadata": review_request["scillm_metadata"],
            "raw_response": {"choices": [{"message": {"content": "{}"}}]},
            "review_response": {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "page_rationale": "visual table is not represented by extracted structure",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "defect",
                        "evidence": "annotated region contains table-like content",
                        "rationale": "extraction emits unknown layout instead of table/list structure",
                        "suggested_fix_surface": "python/pdf_oxide presets or extractor classifier",
                    }
                ],
            },
        }

    def fake_call_opencode_patch(patch_request, **kwargs):
        attempted_agents.append(patch_request["agent"])
        diff = [] if patch_request["agent"] == "build" else "diff --git a/tests/test_fix.py b/tests/test_fix.py"
        assistant_text = (
            "PATCH_APPLIED changed_files=tests/test_fix.py tests=tests/test_fix.py commands=not-run"
            if diff
            else ""
        )
        return {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "endpoint": "POST /v1/scillm/opencode/runs",
            "http_status": 200,
            "request_metadata": patch_request["scillm_metadata"],
            "raw_response": {"status": "completed", "diff": diff, "assistant_text": assistant_text},
        }

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_scillm_review", fake_call_scillm_review)
    monkeypatch.setattr(dag, "call_opencode_patch", fake_call_opencode_patch)
    monkeypatch.setattr(dag, "git_changed_files", lambda repo=dag.REPO: [])

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-defect",
        review_mode="live",
        patch_mode="live",
        patch_backend="opencode_serve",
        opencode_agent="build",
        opencode_agent_sequence=["build", "explore"],
        scillm_preflight_mode="dry_run",
    )

    case_dir = Path(result["case_dir"])
    attempts = json.loads((case_dir / "patch_attempts_ledger.json").read_text(encoding="utf-8"))
    root_request = json.loads((case_dir / "patch_request.json").read_text(encoding="utf-8"))
    root_validation = json.loads((case_dir / "patch_validation.json").read_text(encoding="utf-8"))
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))

    assert attempted_agents == ["build", "explore"]
    assert attempts["agent_sequence"] == ["build", "explore"]
    assert attempts["attempt_count"] == 2
    assert attempts["selected_attempt_index"] == 2
    assert attempts["attempts"][0]["ok"] is False
    assert attempts["attempts"][1]["ok"] is True
    assert root_request["agent"] == "explore"
    assert root_validation["ok"] is True
    assert ledger["terminal_status"] == "still_open"
    assert ledger["reason"] == "patch_scope_validation_failed"
    assert "patch_attempts_ledger.json" in ledger["evidence_artifacts"]


def test_validate_patch_scope_requires_allowed_files_and_regression_test() -> None:
    dag = _load_module()

    no_test = dag.validate_patch_scope(["python/pdf_oxide/extract_for_pdflab.py"], ["python/pdf_oxide/", "tests/"])
    assert no_test["ok"] is False
    assert any("regression test" in error for error in no_test["errors"])

    bad_path = dag.validate_patch_scope(["docs/generated.json", "tests/test_fix.py"], ["python/pdf_oxide/", "tests/"])
    assert bad_path["ok"] is False
    assert any("disallowed" in error for error in bad_path["errors"])

    valid = dag.validate_patch_scope(["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"], ["python/pdf_oxide/", "tests/"])
    assert valid["ok"] is True
    assert valid["test_files"] == ["tests/test_fix.py"]


def test_run_validation_commands_disables_bytecode_writes(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()
    captured_env = {}

    def fake_run(command, **kwargs):
        captured_env.update(kwargs["env"])
        pycache = tmp_path / "tests" / "__pycache__"
        pycache.mkdir(parents=True)
        (pycache / "test_fix.cpython-312.pyc").write_bytes(b"pyc")
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dag.subprocess, "run", fake_run)

    validation = dag.run_validation_commands(["python -m py_compile tests/test_fix.py"], cwd=tmp_path)

    assert validation["ok"] is True
    assert captured_env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert validation["bytecode_cache_cleanup"] == ["tests/__pycache__"]
    assert not (tmp_path / "tests" / "__pycache__").exists()


def test_run_validation_commands_requires_changed_test_coverage(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(dag.subprocess, "run", fake_run)

    missing = dag.run_validation_commands(
        ["python -m py_compile tests/unrelated.py"],
        cwd=tmp_path,
        required_test_files=["tests/test_fix.py"],
    )
    covered = dag.run_validation_commands(
        ["python -m py_compile tests/test_fix.py"],
        cwd=tmp_path,
        required_test_files=["tests/test_fix.py"],
    )

    assert missing["ok"] is False
    assert missing["missing_test_file_coverage"] == ["tests/test_fix.py"]
    assert "validation commands did not cover changed regression tests" in "\n".join(missing["errors"])
    assert covered["ok"] is True
    assert covered["covered_test_files"] == ["tests/test_fix.py"]


def test_patch_delta_ignores_generated_bytecode_for_scope_claim_match() -> None:
    dag = _load_module()

    patch_delta = dag.compute_patch_delta(
        [],
        [
            "python/pdf_oxide/__pycache__/extract_for_pdflab.cpython-311.pyc",
            "python/pdf_oxide/extract_for_pdflab.py",
            "tests/__pycache__/test_fix.cpython-311-pytest-9.0.3.pyc",
            "tests/test_fix.py",
        ],
    )

    assert patch_delta["patch_changed_files"] == [
        "python/pdf_oxide/extract_for_pdflab.py",
        "tests/test_fix.py",
    ]
    assert patch_delta["ignored_generated_files"] == [
        "python/pdf_oxide/__pycache__/extract_for_pdflab.cpython-311.pyc",
        "tests/__pycache__/test_fix.cpython-311-pytest-9.0.3.pyc",
    ]

    validation = dag.validate_patch_scope(
        patch_delta["patch_changed_files"],
        ["python/pdf_oxide/", "tests/"],
        {
            "schema": "pdf_lab.second_pass.patch_applied_claim.v1",
            "status": "applied",
            "raw_line": (
                "PATCH_APPLIED changed_files=python/pdf_oxide/extract_for_pdflab.py,tests/test_fix.py "
                "tests=tests/test_fix.py commands=pytest tests/test_fix.py -q"
            ),
            "changed_files": ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
            "tests": ["tests/test_fix.py"],
            "commands": "pytest tests/test_fix.py -q",
            "errors": [],
        },
    )

    assert validation["ok"] is True


def test_validate_patch_scope_accepts_regression_test_declared_in_tests_field() -> None:
    dag = _load_module()

    validation = dag.validate_patch_scope(
        ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
        ["python/pdf_oxide/", "tests/"],
        {
            "schema": "pdf_lab.second_pass.patch_applied_claim.v1",
            "status": "applied",
            "raw_line": (
                "PATCH_APPLIED changed_files=python/pdf_oxide/extract_for_pdflab.py "
                "tests=tests/test_fix.py commands=pytest tests/test_fix.py -q"
            ),
            "changed_files": ["python/pdf_oxide/extract_for_pdflab.py"],
            "tests": ["tests/test_fix.py"],
            "commands": "pytest tests/test_fix.py -q",
            "errors": [],
        },
    )

    assert validation["ok"] is True
    assert validation["test_files"] == ["tests/test_fix.py"]


def test_git_changed_files_includes_staged_additions(tmp_path: Path) -> None:
    dag = _load_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "PDF Lab Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "pdf-lab-test@example.invalid"], check=True)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, stdout=subprocess.PIPE)

    (repo / "tests").mkdir()
    (repo / "tests/test_page2.py").write_text("def test_page2():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tests/test_page2.py"], check=True)

    assert dag.git_changed_files(repo) == ["tests/test_page2.py"]


def test_validate_patch_scope_rejects_delegate_claim_that_differs_from_git_delta() -> None:
    dag = _load_module()

    validation = dag.validate_patch_scope(
        ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
        ["python/pdf_oxide/", "tests/"],
        {
            "schema": "pdf_lab.second_pass.patch_applied_claim.v1",
            "status": "applied",
            "raw_line": "PATCH_APPLIED changed_files=tests/test_fix.py tests=tests/test_fix.py commands=pytest tests/test_fix.py -q",
            "changed_files": ["tests/test_fix.py"],
            "tests": ["tests/test_fix.py"],
            "commands": "pytest tests/test_fix.py -q",
            "errors": [],
        },
    )

    assert validation["ok"] is False
    assert any("changed_files do not match observed patch delta" in error for error in validation["errors"])


def test_create_patch_commit_records_revertability_check(tmp_path: Path) -> None:
    dag = _load_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "PDF Lab Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "pdf-lab-test@example.invalid"], check=True)
    (repo / "tests").mkdir()
    (repo / "tests/test_fix.py").write_text("def test_existing():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "baseline"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    (repo / "tests/test_fix.py").write_text(
        "def test_existing():\n    assert True\n\n"
        "def test_new_pdf_lab_fix():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )

    commit_gate = dag.create_patch_commit(
        commit_mode="live",
        changed_files=["tests/test_fix.py"],
        message=dag.build_commit_message(page_number=3, case_id="case-1", changed_files=["tests/test_fix.py"]),
        repo=repo,
    )

    assert commit_gate["ok"] is True
    assert commit_gate["commit_sha"]
    assert commit_gate["committed_files"] == ["tests/test_fix.py"]
    assert commit_gate["exact_file_match"] is True
    assert commit_gate["revertability_check"]["ok"] is True
    assert commit_gate["revertability_check"]["revert_exit_code"] == 0
    assert not (repo / ".pdf_lab_revert_checks" / commit_gate["commit_sha"]).exists()


def test_create_patch_commit_rejects_staged_file_mismatch_before_commit(tmp_path: Path) -> None:
    dag = _load_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "PDF Lab Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "pdf-lab-test@example.invalid"], check=True)
    (repo / "tests").mkdir()
    (repo / "tests/test_fix.py").write_text("def test_existing():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "baseline"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    baseline_head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    (repo / "tests/test_fix.py").write_text(
        "def test_existing():\n    assert True\n\n"
        "def test_new_pdf_lab_fix():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )

    commit_gate = dag.create_patch_commit(
        commit_mode="live",
        changed_files=["tests"],
        message=dag.build_commit_message(page_number=3, case_id="case-1", changed_files=["tests"]),
        repo=repo,
    )
    head_after = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    staged_after = dag.git_staged_files(repo)

    assert commit_gate["ok"] is False
    assert commit_gate["commit_sha"] is None
    assert commit_gate["changed_files_under_paths"] == ["tests/test_fix.py"]
    assert commit_gate["staged_files_after_add"] == []
    assert "changed files under requested paths did not match isolated patch delta before staging" in "\n".join(commit_gate["errors"])
    assert head_after == baseline_head
    assert staged_after == []


def test_create_patch_commit_unstages_attempted_files_on_commit_failure(tmp_path: Path) -> None:
    dag = _load_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "PDF Lab Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "pdf-lab-test@example.invalid"], check=True)
    (repo / "tests").mkdir()
    (repo / "tests/test_fix.py").write_text("def test_existing():\n    assert True\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "baseline"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    baseline_head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    (repo / "tests/test_fix.py").write_text(
        "def test_existing():\n    assert True\n\n"
        "def test_new_pdf_lab_fix():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )

    commit_gate = dag.create_patch_commit(
        commit_mode="live",
        changed_files=["tests/test_fix.py"],
        message="",
        repo=repo,
    )
    head_after = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    staged_after = dag.git_staged_files(repo)

    assert commit_gate["ok"] is False
    assert commit_gate["commit_sha"] is None
    assert commit_gate["staged_files_after_add"] == ["tests/test_fix.py"]
    assert commit_gate["index_cleanup"]["ok"] is True
    assert commit_gate["index_cleanup"]["staged_files_after_cleanup"] == []
    assert "git commit failed" in "\n".join(commit_gate["errors"]).lower() or "empty commit message" in "\n".join(commit_gate["errors"]).lower()
    assert head_after == baseline_head
    assert staged_after == []


def test_commit_acceptance_gate_requires_revertability() -> None:
    dag = _load_module()

    accepted = dag.validate_commit_gate_acceptance(
        {
            "schema": "pdf_lab.second_pass.commit_gate.v1",
            "ok": True,
            "commit_sha": "abc123",
            "exact_file_match": True,
            "revertability_check": {
                "schema": "pdf_lab.second_pass.revertability_check.v1",
                "ok": True,
                "commit_sha": "abc123",
            },
        }
    )
    missing_revertability = dag.validate_commit_gate_acceptance(
        {
            "schema": "pdf_lab.second_pass.commit_gate.v1",
            "ok": True,
            "commit_sha": "abc123",
            "exact_file_match": True,
        }
    )
    mismatched_revertability = dag.validate_commit_gate_acceptance(
        {
            "schema": "pdf_lab.second_pass.commit_gate.v1",
            "ok": True,
            "commit_sha": "abc123",
            "exact_file_match": True,
            "revertability_check": {
                "schema": "pdf_lab.second_pass.revertability_check.v1",
                "ok": True,
                "commit_sha": "other",
            },
        }
    )
    mismatched_commit_schema = dag.validate_commit_gate_acceptance(
        {
            "schema": "wrong",
            "ok": True,
            "commit_sha": "abc123",
            "exact_file_match": True,
            "revertability_check": {
                "schema": "pdf_lab.second_pass.revertability_check.v1",
                "ok": True,
                "commit_sha": "abc123",
            },
        }
    )
    mismatched_revertability_schema = dag.validate_commit_gate_acceptance(
        {
            "schema": "pdf_lab.second_pass.commit_gate.v1",
            "ok": True,
            "commit_sha": "abc123",
            "exact_file_match": True,
            "revertability_check": {"schema": "wrong", "ok": True, "commit_sha": "abc123"},
        }
    )

    assert accepted["ok"] is True
    assert missing_revertability["ok"] is False
    assert "commit_gate revertability_check ok is not true" in missing_revertability["errors"]
    assert mismatched_revertability["ok"] is False
    assert "commit_gate revertability_check commit_sha does not match commit_gate commit_sha" in mismatched_revertability["errors"]
    assert mismatched_commit_schema["ok"] is False
    assert "commit_gate schema mismatch" in mismatched_commit_schema["errors"]
    assert mismatched_revertability_schema["ok"] is False
    assert "commit_gate revertability_check schema mismatch" in mismatched_revertability_schema["errors"]


def test_validate_page_terminal_ledger_accepts_committed_verified_patch(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    _write_full_patched_confirmed_artifacts(case_dir)
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "patched_confirmed",
        "reason": "patch_committed_and_after_review_clean",
        "evidence_artifacts": [
            "review.html",
            "selected_candidates.json",
            "patch_delta.json",
            "patch_scope_validation.json",
            "test_validation.json",
            "page_after.json",
            "page_after.png",
            "page_after_candidates.png",
            "review_after_request.json",
            "review_after_request_validation.json",
            "review_after_response.json",
            "review_after_validation.json",
            "commit_acceptance_gate.json",
            "commit_gate.json",
            "revertability_check.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": "abc123",
        "commit_gate_ok": True,
        "commit_exact_file_match": True,
        "commit_revertability_ok": True,
        "commit_acceptance_ok": True,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["schema"] == "pdf_lab.second_pass.page_terminal_ledger_validation.v1"
    assert validation["ok"] is True
    assert validation["missing_artifacts"] == []


def test_validate_page_terminal_ledger_rejects_commit_artifact_mismatch(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    _write_full_patched_confirmed_artifacts(case_dir)
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "commit_acceptance_gate.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.commit_acceptance_gate.v1",
                "ok": True,
                "commit_sha": "other-sha",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "commit_gate.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.commit_gate.v1",
                "ok": True,
                "commit_sha": "abc123",
                "changed_files": ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
                "committed_files": ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
                "exact_file_match": True,
                "revertability_check": {
                    "schema": "wrong.schema",
                    "ok": True,
                    "commit_sha": "other-sha",
                },
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "revertability_check.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.revertability_check.v1",
                "ok": True,
                "commit_sha": "other-sha",
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "patched_confirmed",
        "reason": "patch_committed_and_after_review_clean",
        "evidence_artifacts": [
            "review.html",
            "selected_candidates.json",
            "patch_delta.json",
            "patch_scope_validation.json",
            "test_validation.json",
            "page_after.json",
            "page_after.png",
            "page_after_candidates.png",
            "review_after_request.json",
            "review_after_request_validation.json",
            "review_after_response.json",
            "review_after_validation.json",
            "commit_acceptance_gate.json",
            "commit_gate.json",
            "revertability_check.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": "abc123",
        "commit_gate_ok": True,
        "commit_exact_file_match": True,
        "commit_revertability_ok": True,
        "commit_acceptance_ok": True,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "commit_acceptance_gate commit_sha does not match terminal ledger" in errors
    assert "commit_gate.revertability_check schema mismatch" in errors
    assert "commit_gate.revertability_check commit_sha does not match terminal ledger" in errors
    assert "revertability_check commit_sha does not match terminal ledger" in errors


def test_validate_page_terminal_ledger_rejects_invalid_after_request_validation(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    _write_full_patched_confirmed_artifacts(case_dir)
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "review_after_request_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_request_validation.v1",
                "ok": False,
                "errors": ["missing after-patch image evidence"],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "patched_confirmed",
        "reason": "patch_committed_and_after_review_clean",
        "evidence_artifacts": [
            "review.html",
            "selected_candidates.json",
            "patch_delta.json",
            "patch_scope_validation.json",
            "test_validation.json",
            "page_after.json",
            "page_after.png",
            "page_after_candidates.png",
            "review_after_request.json",
            "review_after_request_validation.json",
            "review_after_response.json",
            "review_after_validation.json",
            "commit_acceptance_gate.json",
            "commit_gate.json",
            "revertability_check.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": "abc123",
        "commit_gate_ok": True,
        "commit_exact_file_match": True,
        "commit_revertability_ok": True,
        "commit_acceptance_ok": True,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    assert "review_after_request_validation.ok is not true" in validation["errors"]


def test_validate_page_terminal_ledger_rejects_stale_after_request_validation(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    _write_full_patched_confirmed_artifacts(case_dir)
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "candidate_presets.json").write_text(
        json.dumps(_candidate_presets_payload(["cand:p0001:0000:table"])),
        encoding="utf-8",
    )
    after_request = dag.build_review_request(
        case_dir=case_dir,
        page_case={
            "case_id": "page_case_0001_p0001:after_patch",
            "page_number": 1,
            "candidate_ids": ["cand:p0001:0000:table"],
        },
        page_json_path="page_after.json",
        original_image_path="page_after.png",
        annotated_image_path="page_after_candidates.png",
        candidate_presets_path="candidate_presets.json",
        model="gpt-5.5",
        batch_id="batch-after",
    )
    (case_dir / "review_after_request.json").write_text(json.dumps(after_request), encoding="utf-8")
    (case_dir / "review_after_request_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_request_validation.v1",
                "ok": True,
                "errors": [],
                "artifact_paths": {"page_json": "stale.json"},
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "patched_confirmed",
        "reason": "patch_committed_and_after_review_clean",
        "evidence_artifacts": [
            "review.html",
            "selected_candidates.json",
            "candidate_presets.json",
            "patch_delta.json",
            "patch_scope_validation.json",
            "test_validation.json",
            "page_after.json",
            "page_after.png",
            "page_after_candidates.png",
            "review_after_request.json",
            "review_after_request_validation.json",
            "review_after_response.json",
            "review_after_validation.json",
            "commit_acceptance_gate.json",
            "commit_gate.json",
            "revertability_check.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": "abc123",
        "commit_gate_ok": True,
        "commit_exact_file_match": True,
        "commit_revertability_ok": True,
        "commit_acceptance_ok": True,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    assert "review_after_request_validation does not match recomputed review_after_request contract" in validation["errors"]


def test_validate_page_terminal_ledger_rejects_stale_after_review_candidate_set(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    _write_full_patched_confirmed_artifacts(case_dir)
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "review_after_response.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean",
                "candidate_findings": [{"candidate_id": "cand:p0002:0000:table", "status": "clean"}],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "review_after_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": True,
                "errors": [],
                "expected_candidate_ids": ["cand:p0002:0000:table"],
                "seen_candidate_ids": ["cand:p0002:0000:table"],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "patched_confirmed",
        "reason": "patch_committed_and_after_review_clean",
        "evidence_artifacts": [
            "review.html",
            "selected_candidates.json",
            "patch_delta.json",
            "patch_scope_validation.json",
            "test_validation.json",
            "page_after.json",
            "page_after.png",
            "page_after_candidates.png",
            "review_after_request.json",
            "review_after_request_validation.json",
            "review_after_response.json",
            "review_after_validation.json",
            "commit_acceptance_gate.json",
            "commit_gate.json",
            "revertability_check.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": "abc123",
        "commit_gate_ok": True,
        "commit_exact_file_match": True,
        "commit_revertability_ok": True,
        "commit_acceptance_ok": True,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "review_after_validation expected_candidate_ids do not match selected_candidates" in errors
    assert "review_after_validation seen_candidate_ids do not match selected_candidates" in errors
    assert "review_after_response candidate_findings do not match selected_candidates" in errors


def test_validate_page_terminal_ledger_rejects_uncovered_changed_tests(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    _write_full_patched_confirmed_artifacts(case_dir)
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "test_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.test_validation.v1",
                "ok": True,
                "errors": [],
                "results": [{"command": "python -m py_compile tests/unrelated.py", "exit_code": 0}],
                "required_test_files": ["tests/test_fix.py"],
                "covered_test_files": [],
                "missing_test_file_coverage": ["tests/test_fix.py"],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "patched_confirmed",
        "reason": "patch_committed_and_after_review_clean",
        "evidence_artifacts": [
            "review.html",
            "selected_candidates.json",
            "patch_delta.json",
            "patch_scope_validation.json",
            "test_validation.json",
            "page_after.json",
            "page_after.png",
            "page_after_candidates.png",
            "review_after_request.json",
            "review_after_request_validation.json",
            "review_after_response.json",
            "review_after_validation.json",
            "commit_acceptance_gate.json",
            "commit_gate.json",
            "revertability_check.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": "abc123",
        "commit_gate_ok": True,
        "commit_exact_file_match": True,
        "commit_revertability_ok": True,
        "commit_acceptance_ok": True,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "test_validation covered_test_files do not match patch_scope_validation test_files" in errors
    assert "test_validation missing_test_file_coverage is not empty" in errors


def test_validate_page_terminal_ledger_rejects_missing_patched_confirmed_evidence(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    _write_full_patched_confirmed_artifacts(case_dir)
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "patched_confirmed",
        "reason": "patch_committed_and_after_review_clean",
        "evidence_artifacts": [
            "review.html",
            "commit_acceptance_gate.json",
            "commit_gate.json",
            "revertability_check.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": "abc123",
        "commit_gate_ok": True,
        "commit_exact_file_match": True,
        "commit_revertability_ok": True,
        "commit_acceptance_ok": True,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "patched_confirmed terminal ledger missing patch_scope_validation.json" in errors
    assert "patched_confirmed terminal ledger missing test_validation.json" in errors
    assert "patched_confirmed terminal ledger missing review_after_request_validation.json" in errors
    assert "patched_confirmed terminal ledger missing review_after_response.json" in errors


def test_validate_page_terminal_ledger_rejects_unproven_patched_confirmed(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "patched_confirmed",
        "reason": "patch_committed_and_after_review_clean",
        "evidence_artifacts": ["review.html", "commit_gate.json"],
        "commit_sha": None,
        "commit_gate_ok": True,
        "commit_exact_file_match": False,
        "commit_revertability_ok": False,
        "commit_acceptance_ok": False,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "patched_confirmed terminal ledger missing commit_sha" in errors
    assert "patched_confirmed terminal ledger requires commit_exact_file_match true" in errors
    assert "patched_confirmed terminal ledger missing commit_acceptance_gate.json" in errors
    assert "declared evidence artifacts are missing" in errors


def test_validate_page_terminal_ledger_rejects_stale_patch_attempts_ledger(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "patch_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.patch_delegate_validation.v1",
                "ok": False,
                "errors": ["patch_delegate_failed"],
                "patch_status": "failed",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "patch_attempt_01_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.patch_delegate_validation.v1",
                "ok": False,
                "errors": ["transport stream contained session_error events"],
                "patch_status": "failed",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "patch_attempts_ledger.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.patch_attempts_ledger.v1",
                "patch_backend": "scillm_orchestrator",
                "patch_mode": "live",
                "patch_prompt_profile": "plan_only",
                "repair_strategy": "direct",
                "agent_sequence": ["build"],
                "attempt_count": 2,
                "selected_attempt_index": 1,
                "attempts": [
                    {
                        "attempt_index": 1,
                        "agent": "build",
                        "validation_artifact": "patch_attempt_01_validation.json",
                        "ok": True,
                        "errors": ["stale copied success"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "patch_delegate_failed",
        "evidence_artifacts": [
            "review.html",
            "patch_validation.json",
            "patch_attempts_ledger.json",
            "patch_attempt_01_validation.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "patch_attempts_ledger attempt_count does not match attempts length" in errors
    assert "patch_attempts_ledger attempts[0].ok does not match validation_artifact" in errors
    assert "patch_attempts_ledger attempts[0].errors do not match validation_artifact" in errors
    assert "patch_validation errors do not match selected/final patch attempt validation" in errors


def test_validate_page_terminal_ledger_rejects_reviewed_clean_with_defect_response(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": True,
                "errors": [],
                "expected_candidate_ids": ["cand:p0001:0000:table"],
                "seen_candidate_ids": ["cand:p0001:0000:table"],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "review_response.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0001:0000:table",
                        "status": "defect",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "reviewed_clean",
        "reason": "scillm_review_validated_clean",
        "evidence_artifacts": [
            "review.html",
            "review_validation.json",
            "review_response.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "reviewed_clean terminal ledger requires review_response page_status clean" in errors
    assert "reviewed_clean terminal ledger requires all review_response candidate_findings clean" in errors


def test_validate_page_terminal_ledger_rejects_stale_review_candidate_set(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "selected_candidates.json").write_text(
        json.dumps(_selected_candidates_payload(["cand:p0001:0000:table"])),
        encoding="utf-8",
    )
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": True,
                "errors": [],
                "expected_candidate_ids": ["cand:p0002:0000:table"],
                "seen_candidate_ids": ["cand:p0002:0000:table"],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "review_response.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean",
                "candidate_findings": [{"candidate_id": "cand:p0002:0000:table", "status": "clean"}],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "reviewed_clean",
        "reason": "scillm_review_validated_clean",
        "evidence_artifacts": [
            "review.html",
            "selected_candidates.json",
            "review_validation.json",
            "review_response.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "review_validation expected_candidate_ids do not match selected_candidates" in errors
    assert "review_validation seen_candidate_ids do not match selected_candidates" in errors
    assert "review_response candidate_findings do not match selected_candidates" in errors


def test_validate_page_terminal_ledger_rejects_stale_candidate_presets(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "selected_candidates.json").write_text(
        json.dumps(_selected_candidates_payload(["cand:p0001:0000:table"])),
        encoding="utf-8",
    )
    (case_dir / "candidate_presets.json").write_text(
        json.dumps(_candidate_presets_payload(["cand:p0002:0000:table"])),
        encoding="utf-8",
    )
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": True,
                "errors": [],
                "expected_candidate_ids": ["cand:p0001:0000:table"],
                "seen_candidate_ids": ["cand:p0001:0000:table"],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "review_response.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean",
                "candidate_findings": [{"candidate_id": "cand:p0001:0000:table", "status": "clean"}],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "reviewed_clean",
        "reason": "scillm_review_validated_clean",
        "evidence_artifacts": [
            "review.html",
            "selected_candidates.json",
            "candidate_presets.json",
            "review_validation.json",
            "review_response.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    assert "candidate_presets candidate_ids do not match selected_candidates" in validation["errors"]


def test_validate_page_terminal_ledger_rejects_stale_candidate_presets_contract(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "selected_candidates.json").write_text(
        json.dumps(_selected_candidates_payload(["cand:p0001:0000:table"])),
        encoding="utf-8",
    )
    preset_payload = _candidate_presets_payload(
        ["cand:p0001:0000:table"],
        case_id="page_case_9999_p9999",
        page_number=9999,
        candidate_count=2,
    )
    preset_payload["schema"] = "pdf_lab.second_pass.candidate_presets.v0"
    (case_dir / "candidate_presets.json").write_text(json.dumps(preset_payload), encoding="utf-8")
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": False,
                "errors": ["dry_run_review_not_executed"],
                "expected_candidate_ids": ["cand:p0001:0000:table"],
                "seen_candidate_ids": [],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "selected_candidates.json",
            "candidate_presets.json",
            "review_validation.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "candidate_presets schema mismatch" in errors
    assert "candidate_presets page_case.case_id does not match terminal ledger" in errors
    assert "candidate_presets page_case.page_number does not match terminal ledger" in errors
    assert "candidate_presets candidate_count does not match candidates" in errors


def test_validate_page_terminal_ledger_rejects_stale_selected_candidates_contract(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    selected_payload = _selected_candidates_payload(
        ["cand:p0001:0000:table"],
        case_id="page_case_9999_p9999",
        page_number=9999,
        candidate_count=2,
    )
    selected_payload["schema"] = "pdf_lab.second_pass.selected_candidates.v0"
    (case_dir / "selected_candidates.json").write_text(json.dumps(selected_payload), encoding="utf-8")
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": False,
                "errors": ["dry_run_review_not_executed"],
                "expected_candidate_ids": ["cand:p0001:0000:table"],
                "seen_candidate_ids": [],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "selected_candidates.json",
            "review_validation.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "selected_candidates schema mismatch" in errors
    assert "selected_candidates page_case.case_id does not match terminal ledger" in errors
    assert "selected_candidates page_case.page_number does not match terminal ledger" in errors
    assert "selected_candidates candidate_count does not match candidates" in errors


def test_validate_page_terminal_ledger_rejects_stale_review_validation_contract(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "selected_candidates.json").write_text(
        json.dumps(_selected_candidates_payload(["cand:p0001:0000:table"])),
        encoding="utf-8",
    )
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            _review_validation_payload(
                ["cand:p0001:0000:table"],
                case_id="page_case_9999_p9999",
                page_number=9999,
                candidate_count=2,
            )
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "selected_candidates.json",
            "review_validation.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "review_validation page_case.case_id does not match terminal ledger" in errors
    assert "review_validation page_case.page_number does not match terminal ledger" in errors
    assert "review_validation candidate_count does not match selected_candidates" in errors


def test_validate_page_terminal_ledger_rejects_stale_review_validation_for_current_response(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "selected_candidates.json").write_text(
        json.dumps(_selected_candidates_payload(["cand:p0001:0000:table"])),
        encoding="utf-8",
    )
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            _review_validation_payload(
                ["cand:p0001:0000:table"],
                ok=True,
                errors=[],
                seen_candidate_ids=["cand:p0001:0000:table"],
            )
        ),
        encoding="utf-8",
    )
    (case_dir / "review_response.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0001:0000:table",
                        "status": "clean",
                        "evidence": "bbox matches rendered table",
                        "rationale": "visual and JSON agree",
                        "suggested_fix_surface": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "reviewed_clean",
        "reason": "scillm_review_validated_clean",
        "evidence_artifacts": [
            "review.html",
            "selected_candidates.json",
            "review_validation.json",
            "review_response.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "review_validation does not match recomputed review_response contract" in errors


def test_validate_page_terminal_ledger_rejects_stale_review_request_validation(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "review_request.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_request.v1",
                "endpoint": "POST /v1/chat/completions",
                "response_format": {"type": "json_object"},
                "required_response_schema": {"schema": "pdf_lab.second_pass.review_response.v1"},
                "model": "gpt-5.5",
                "reasoning_effort": "high",
                "scillm_metadata": {"batch_id": "batch-a", "item_id": "page_case_0001_p0001"},
                "page_case": {"case_id": "page_case_0001_p0001", "page_number": 1},
                "artifacts": {
                    "page_json": "missing_page.json",
                    "original_image": "missing_before.png",
                    "annotated_image": "missing_candidates.png",
                    "candidate_presets": "missing_presets.json",
                },
                "scillm_payload": {
                    "model": "gpt-5.5",
                    "reasoning_effort": "high",
                    "response_format": {"type": "json_object"},
                    "scillm_metadata": {"batch_id": "batch-a", "item_id": "page_case_0001_p0001"},
                    "messages": [{"role": "user", "content": []}],
                },
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "review_request_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_request_validation.v1",
                "ok": True,
                "errors": [],
                "artifact_paths": {},
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "review_validation_failed",
        "evidence_artifacts": [
            "review.html",
            "review_request.json",
            "review_request_validation.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    assert "review_request_validation does not match recomputed review_request contract" in validation["errors"]


def test_validate_page_terminal_ledger_rejects_dry_run_with_review_response(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": True,
                "errors": [],
                "expected_candidate_ids": ["cand:p0001:0000:table"],
                "seen_candidate_ids": ["cand:p0001:0000:table"],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "review_response.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean",
                "candidate_findings": [{"candidate_id": "cand:p0001:0000:table", "status": "clean"}],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "review_validation.json",
            "review_response.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "dry_run_review_not_executed terminal ledger requires review_validation.ok false" in errors
    assert "dry_run_review_not_executed terminal ledger requires matching review_validation error" in errors
    assert "dry_run_review_not_executed terminal ledger must not include review_response.json" in errors


def test_validate_page_terminal_ledger_rejects_stale_review_preflight_surface(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": False,
                "errors": ["scillm_review_call_failed"],
                "expected_candidate_ids": ["cand:p0001:0000:table"],
                "seen_candidate_ids": [],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "scillm_review_preflight.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.scillm_preflight.v1",
                "surface": "opencode_serve",
                "base_url": "http://localhost:4001",
                "caller_skill": "",
                "checks": [],
                "ok": False,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "blocked_substrate",
        "reason": "scillm_review_call_failed",
        "evidence_artifacts": [
            "review.html",
            "review_validation.json",
            "scillm_review_preflight.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "scillm_review_preflight.json surface does not match expected scillm surface" in errors
    assert "scillm_review_preflight.json caller_skill must be non-empty" in errors
    assert "scillm_review_preflight.json ok false requires non-empty errors" in errors


def test_validate_page_terminal_ledger_rejects_patch_preflight_surface_mismatch(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": True,
                "errors": [],
                "expected_candidate_ids": ["cand:p0001:0000:table"],
                "seen_candidate_ids": ["cand:p0001:0000:table"],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "review_response.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "candidate_findings": [{"candidate_id": "cand:p0001:0000:table", "status": "defect"}],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "patch_request.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_request.v1",
                "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "patch_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.patch_delegate_validation.v1",
                "ok": False,
                "errors": ["patch_delegate_call_failed"],
                "patch_status": "substrate_error",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "scillm_patch_preflight.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.scillm_preflight.v1",
                "surface": "opencode_serve",
                "base_url": "http://localhost:4001",
                "caller_skill": "pdf-lab",
                "checks": [],
                "ok": True,
                "errors": ["stale success artifact kept an old error"],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "blocked_substrate",
        "reason": "patch_delegate_call_failed",
        "evidence_artifacts": [
            "review.html",
            "review_validation.json",
            "review_response.json",
            "patch_request.json",
            "patch_validation.json",
            "scillm_patch_preflight.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "scillm_patch_preflight.json surface does not match expected scillm surface" in errors
    assert "scillm_patch_preflight.json ok true requires empty errors" in errors


def test_validate_page_terminal_ledger_rejects_duplicate_and_unsafe_evidence_artifacts(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": False,
                "errors": ["dry_run_review_not_executed"],
                "expected_candidate_ids": ["cand:p0001:0000:table"],
                "seen_candidate_ids": [],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "review.html",
            "review_validation.json",
            "../outside.json",
            "/tmp/outside.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "evidence_artifacts contains duplicate artifact names: ['review.html']" in errors
    assert "evidence_artifacts contains unsafe artifact paths: ['../outside.json', '/tmp/outside.json']" in errors


def test_validate_page_terminal_ledger_rejects_stale_state_and_candidate_manifest(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "state.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_state.v1",
                "case_id": "page_case_9999_p9999",
                "page_number": 9999,
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "sampled_candidate_manifest.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.sampled_candidate_manifest.v1",
                "page_case": {
                    "case_id": "page_case_9999_p9999",
                    "page_number": 9999,
                    "candidate_ids": ["cand:p0001:0000:table"],
                },
                "candidate_count": 1,
                "candidates": [{"candidate_id": "cand:p0001:0000:table"}],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "selected_candidates.json").write_text(
        json.dumps(_selected_candidates_payload(["cand:p0001:0001:table"])),
        encoding="utf-8",
    )
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": False,
                "errors": ["dry_run_review_not_executed"],
                "expected_candidate_ids": ["cand:p0001:0000:table"],
                "seen_candidate_ids": [],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "state.json",
            "sampled_candidate_manifest.json",
            "selected_candidates.json",
            "review.html",
            "review_validation.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "state.json case_id does not match terminal ledger" in errors
    assert "state.json page_number does not match terminal ledger" in errors
    assert "sampled_candidate_manifest page_case.case_id does not match terminal ledger" in errors
    assert "sampled_candidate_manifest page_case.page_number does not match terminal ledger" in errors
    assert "selected_candidates candidate_ids do not match sampled_candidate_manifest" in errors


def test_validate_page_terminal_ledger_rejects_stale_sampled_page_case_candidate_ids(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "sampled_candidate_manifest.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.sampled_candidate_manifest.v1",
                "page_case": {
                    "case_id": "page_case_0001_p0001",
                    "page_number": 1,
                    "candidate_ids": ["cand:p0001:9999:table"],
                },
                "candidate_count": 1,
                "candidates": [{"candidate_id": "cand:p0001:0000:table"}],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "selected_candidates.json").write_text(
        json.dumps(_selected_candidates_payload(["cand:p0001:0000:table"])),
        encoding="utf-8",
    )
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": False,
                "errors": ["dry_run_review_not_executed"],
                "expected_candidate_ids": ["cand:p0001:0000:table"],
                "seen_candidate_ids": [],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "sampled_candidate_manifest.json",
            "selected_candidates.json",
            "review.html",
            "review_validation.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    assert "sampled_candidate_manifest page_case.candidate_ids do not match candidates" in validation["errors"]


def test_validate_page_terminal_ledger_rejects_sampled_manifest_schema_and_count_mismatch(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "sampled_candidate_manifest.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.sampled_candidate_manifest.v0",
                "page_case": {
                    "case_id": "page_case_0001_p0001",
                    "page_number": 1,
                    "candidate_ids": ["cand:p0001:0000:table"],
                },
                "candidate_count": 2,
                "candidates": [{"candidate_id": "cand:p0001:0000:table"}],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "selected_candidates.json").write_text(
        json.dumps(_selected_candidates_payload(["cand:p0001:0000:table"])),
        encoding="utf-8",
    )
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": False,
                "errors": ["dry_run_review_not_executed"],
                "expected_candidate_ids": ["cand:p0001:0000:table"],
                "seen_candidate_ids": [],
            }
        ),
        encoding="utf-8",
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "sampled_candidate_manifest.json",
            "selected_candidates.json",
            "review.html",
            "review_validation.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = dag.validate_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "sampled_candidate_manifest schema mismatch" in errors
    assert "sampled_candidate_manifest candidate_count does not match candidates" in errors


def test_validate_page_review_bundle_rejects_missing_zip_entry(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    for name in sorted(dag.MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS):
        (case_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
    zip_path = case_dir / "review_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(dag.MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS - {"review_request.json"}):
            bundle.write(case_dir / name, name)
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "review_request.json",
            "terminal_ledger_validation.json",
        ],
    }

    validation = dag.validate_page_review_bundle(case_dir, zip_path, terminal)

    assert validation["schema"] == "pdf_lab.second_pass.page_review_bundle_validation.v1"
    assert validation["ok"] is False
    assert validation["zip_content_ok"] is False
    assert validation["missing_artifacts"] == []
    assert validation["missing_expected_zip_entries"] == ["review_request.json"]
    assert "required bundle artifacts are missing from zip" in "\n".join(validation["errors"])


def test_validate_page_review_bundle_rejects_unsafe_artifact_paths(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    for name in sorted(dag.MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS):
        (case_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
    zip_path = case_dir / "review_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(dag.MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS):
            bundle.write(case_dir / name, name)
        bundle.writestr("../escape.json", "{}")
        bundle.writestr("/tmp/escape.json", "{}")
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "../outside.json",
            "/tmp/outside.json",
            "terminal_ledger_validation.json",
        ],
    }

    validation = dag.validate_page_review_bundle(case_dir, zip_path, terminal)

    assert validation["ok"] is False
    assert validation["zip_content_ok"] is False
    assert "../outside.json" not in validation["required_zip_entries"]
    assert "/tmp/outside.json" not in validation["required_zip_entries"]
    assert validation["unsafe_evidence_artifacts"] == ["../outside.json", "/tmp/outside.json"]
    assert validation["unsafe_zip_entries"] == ["../escape.json", "/tmp/escape.json"]
    errors = "\n".join(validation["errors"])
    assert "terminal evidence_artifacts contains unsafe bundle paths" in errors
    assert "unsafe zip entries" in errors


def test_validate_page_review_bundle_rejects_invalid_evidence_artifacts(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    for name in sorted(dag.MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS):
        (case_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
    zip_path = case_dir / "review_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(dag.MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS):
            bundle.write(case_dir / name, name)
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "",
            42,
            None,
            "terminal_ledger_validation.json",
        ],
    }

    validation = dag.validate_page_review_bundle(case_dir, zip_path, terminal)

    assert validation["ok"] is False
    assert validation["zip_content_ok"] is True
    assert validation["invalid_evidence_artifacts"] == ["", 42, None]
    assert "" not in validation["required_zip_entries"]
    assert "terminal evidence_artifacts contains invalid artifact names" in "\n".join(validation["errors"])


def test_validate_page_review_bundle_rejects_stale_terminal_ledger_json(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    for name in sorted(dag.MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS):
        (case_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "review_request.json",
            "terminal_ledger_validation.json",
        ],
    }
    stale_terminal = {
        **terminal,
        "case_id": "page_case_9999_p9999",
        "page_number": 9999,
    }
    (case_dir / "terminal_ledger.json").write_text(json.dumps(stale_terminal), encoding="utf-8")
    zip_path = case_dir / "review_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(dag.MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS):
            bundle.write(case_dir / name, name)

    validation = dag.validate_page_review_bundle(case_dir, zip_path, terminal)

    assert validation["ok"] is False
    assert validation["zip_content_ok"] is True
    assert validation["terminal_ledger_matches_argument"] is False
    assert "terminal_ledger.json does not match terminal argument" in validation["errors"]


def test_validate_page_review_bundle_rejects_stale_terminal_validation_json(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    for name in sorted(dag.MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS):
        (case_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "review_validation.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }
    (case_dir / "terminal_ledger.json").write_text(json.dumps(terminal), encoding="utf-8")
    (case_dir / "review_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_validation.v1",
                "ok": False,
                "errors": ["dry_run_review_not_executed"],
                "expected_candidate_ids": [],
                "seen_candidate_ids": [],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "terminal_ledger_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_terminal_ledger_validation.v1",
                "ok": True,
                "errors": [],
                "case_id": "page_case_9999_p9999",
                "terminal_status": "reviewed_clean",
                "declared_evidence_count": 1,
                "missing_artifacts": [],
            }
        ),
        encoding="utf-8",
    )
    zip_path = case_dir / "review_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(dag.MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS):
            bundle.write(case_dir / name, name)

    validation = dag.validate_page_review_bundle(case_dir, zip_path, terminal)

    assert validation["ok"] is False
    assert validation["zip_content_ok"] is True
    assert validation["terminal_ledger_matches_argument"] is True
    assert validation["terminal_ledger_validation_matches_recomputed"] is False
    assert "terminal_ledger_validation.json does not match recomputed terminal validation" in validation["errors"]


def test_validate_review_request_contract_rejects_missing_multimodal_evidence(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "page_before.json").write_text(json.dumps({"blocks": []}), encoding="utf-8")
    (case_dir / "page_before.png").write_bytes(b"png")
    (case_dir / "candidate_presets.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
    review_request = {
        "schema": "pdf_lab.second_pass.review_request.v1",
        "endpoint": "POST /v1/chat/completions",
        "response_format": {"type": "json_object"},
        "scillm_metadata": {"batch_id": "batch", "item_id": "case"},
        "artifacts": {
            "page_json": "page_before.json",
            "original_image": "page_before.png",
            "annotated_image": "missing_overlay.png",
            "candidate_presets": "candidate_presets.json",
        },
        "required_response_schema": {"schema": "pdf_lab.second_pass.review_response.v1"},
        "scillm_payload": {
            "model": "gpt-5.5",
            "response_format": {"type": "json_object"},
            "scillm_metadata": {"batch_id": "batch", "item_id": "case"},
            "messages": [{"role": "user", "content": [{"type": "text", "text": "review"}]}],
        },
    }

    validation = dag.validate_review_request_contract(case_dir, review_request)

    assert validation["schema"] == "pdf_lab.second_pass.review_request_validation.v1"
    assert validation["ok"] is False
    assert validation["image_part_count"] == 0
    errors = "\n".join(validation["errors"])
    assert "review_request artifact does not exist: missing_overlay.png" in errors
    assert "scillm_payload must include exactly two image_url evidence parts" in errors


def test_validate_review_request_contract_rejects_unsafe_artifact_paths(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    absolute_page_json = outside / "page_before.json"
    traversal_candidate_presets = tmp_path / "candidate_presets.json"
    absolute_page_json.write_text(json.dumps({"blocks": []}), encoding="utf-8")
    traversal_candidate_presets.write_text(json.dumps({"candidates": []}), encoding="utf-8")
    (case_dir / "page_before.png").write_bytes(b"png")
    (case_dir / "page_candidates.png").write_bytes(b"png")
    review_request = {
        "schema": "pdf_lab.second_pass.review_request.v1",
        "endpoint": "POST /v1/chat/completions",
        "response_format": {"type": "json_object"},
        "scillm_metadata": {"batch_id": "batch", "item_id": "page_case_0001_p0001"},
        "page_case": {"case_id": "page_case_0001_p0001", "page_number": 1},
        "artifacts": {
            "page_json": str(absolute_page_json),
            "original_image": "page_before.png",
            "annotated_image": "page_candidates.png",
            "candidate_presets": "../candidate_presets.json",
        },
        "required_response_schema": {"schema": "pdf_lab.second_pass.review_response.v1"},
        "scillm_payload": {
            "model": "gpt-5.5",
            "response_format": {"type": "json_object"},
            "scillm_metadata": {"batch_id": "batch", "item_id": "page_case_0001_p0001"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "review"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n"}},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n"}},
                    ],
                }
            ],
        },
    }

    validation = dag.validate_review_request_contract(case_dir, review_request)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert f"review_request artifacts.page_json unsafe path: {absolute_page_json}" in errors
    assert "review_request artifacts.candidate_presets unsafe path: ../candidate_presets.json" in errors


def test_validate_review_request_contract_rejects_stale_payload_image_bytes(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "page_before.json").write_text(json.dumps({"blocks": []}), encoding="utf-8")
    (case_dir / "candidate_presets.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
    (case_dir / "page_before.png").write_bytes(b"before-current")
    (case_dir / "page_candidates.png").write_bytes(b"annotated-current")
    request = dag.build_review_request(
        case_dir=case_dir,
        page_case={"case_id": "page_case_0001_p0001", "page_number": 1},
        page_json_path="page_before.json",
        original_image_path="page_before.png",
        annotated_image_path="page_candidates.png",
        candidate_presets_path="candidate_presets.json",
        model="gpt-5.5",
        batch_id="batch-review",
    )
    request["scillm_payload"]["messages"][0]["content"][2]["image_url"]["url"] = "data:image/png;base64,c3RhbGU="

    validation = dag.validate_review_request_contract(case_dir, request)

    assert validation["ok"] is False
    assert "scillm_payload image_url part 2 does not match artifacts.annotated_image" in validation["errors"]


def test_validate_page_review_bundle_requires_minimum_one_case_evidence(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    for name in [
        "terminal_ledger.json",
        "terminal_ledger_validation.json",
        "review.html",
        "review_request.json",
    ]:
        (case_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
    zip_path = case_dir / "review_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in [
            "terminal_ledger.json",
            "terminal_ledger_validation.json",
            "review.html",
            "review_request.json",
        ]:
            bundle.write(case_dir / name, name)
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "review_request.json",
            "terminal_ledger_validation.json",
        ],
    }

    validation = dag.validate_page_review_bundle(case_dir, zip_path, terminal)

    assert validation["schema"] == "pdf_lab.second_pass.page_review_bundle_validation.v1"
    assert validation["ok"] is False
    assert "page_before.json" in validation["missing_artifacts"]
    assert "page_before.png" in validation["missing_artifacts"]
    assert "candidate_presets.json" in validation["missing_artifacts"]
    assert "selected_candidates.json" in validation["missing_artifacts"]
    assert "scillm_orchestrator_page_dag_spec.json" in validation["missing_artifacts"]
    assert "required bundle artifacts are missing from case dir" in "\n".join(validation["errors"])


def test_validate_page_review_bundle_rejects_stale_zip_entry(tmp_path: Path) -> None:
    dag = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    for name in sorted(dag.MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS):
        (case_dir / name).write_text(json.dumps({"artifact": name, "version": "current"}), encoding="utf-8")
    zip_path = case_dir / "review_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in sorted(dag.MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS - {"review_request.json"}):
            bundle.write(case_dir / name, name)
        bundle.writestr("review_request.json", json.dumps({"artifact": "review_request.json", "version": "stale"}))
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "review_request.json",
            "terminal_ledger_validation.json",
        ],
    }

    validation = dag.validate_page_review_bundle(case_dir, zip_path, terminal)

    assert validation["ok"] is False
    assert validation["zip_content_ok"] is False
    assert validation["missing_expected_zip_entries"] == []
    assert validation["mismatched_zip_entries"] == ["review_request.json"]
    assert "required bundle artifacts differ between case dir and zip" in "\n".join(validation["errors"])


def test_verified_patch_flow_requires_commit_sha_for_patched_confirmed(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode):
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "table",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "A | B",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_call_scillm_review(review_request, **kwargs):
        after = review_request["artifacts"]["page_json"] == "page_after.json"
        return {
            "schema": "pdf_lab.second_pass.scillm_review_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 200,
            "scillm_metadata": review_request["scillm_metadata"],
            "raw_response": {"choices": [{"message": {"content": "{}"}}]},
            "review_response": {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean" if after else "defect",
                "page_rationale": "clean after patch" if after else "defect before patch",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "clean" if after else "defect",
                        "evidence": "candidate evidence reviewed",
                        "rationale": "after evidence is clean" if after else "before evidence is defective",
                        "suggested_fix_surface": "none" if after else "python/pdf_oxide",
                    }
                ],
            },
        }

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_scillm_review", fake_call_scillm_review)
    def fake_call_opencode_patch(patch_request, **kwargs):
        return {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "endpoint": "POST /v1/scillm/opencode/runs",
            "http_status": 200,
            "request_metadata": patch_request["scillm_metadata"],
            "raw_response": {
                "status": "completed",
                "assistant_text": (
                    "PATCH_APPLIED changed_files=python/pdf_oxide/extract_for_pdflab.py,tests/test_fix.py "
                    "tests=tests/test_fix.py commands=pytest tests/test_fix.py -q"
                ),
                "artifacts": {"diff": "diff.patch"},
            },
        }

    monkeypatch.setattr(dag, "call_opencode_patch", fake_call_opencode_patch)
    changed_snapshots = iter([
        ["PROJECT_KNOWLEDGE.md", "artifacts/pdf_lab/existing.json"],
        [
            "PROJECT_KNOWLEDGE.md",
            "artifacts/pdf_lab/existing.json",
            "python/pdf_oxide/extract_for_pdflab.py",
            "tests/test_fix.py",
        ],
    ])
    monkeypatch.setattr(dag, "git_changed_files", lambda repo=dag.REPO: next(changed_snapshots))
    monkeypatch.setattr(dag, "run_validation_commands", lambda commands, cwd=dag.REPO, required_test_files=None: {
        "schema": "pdf_lab.second_pass.test_validation.v1",
        "ok": True,
        "errors": [],
        "results": [{"command": commands[0], "exit_code": 0, "stdout": "ok", "stderr": ""}],
        "required_test_files": sorted(required_test_files or []),
        "covered_test_files": sorted(required_test_files or []),
        "missing_test_file_coverage": [],
    })
    monkeypatch.setattr(dag, "create_patch_commit", lambda **kwargs: {
        "schema": "pdf_lab.second_pass.commit_gate.v1",
        "ok": True,
        "mode": kwargs["commit_mode"],
        "errors": [],
        "commit_sha": "abc123",
        "changed_files": kwargs["changed_files"],
        "committed_files": kwargs["changed_files"],
        "exact_file_match": True,
        "revertability_check": {
            "schema": "pdf_lab.second_pass.revertability_check.v1",
            "ok": True,
            "commit_sha": "abc123",
            "errors": [],
            "revert_exit_code": 0,
        },
    })

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-verified",
        review_mode="live",
        patch_mode="live",
        validation_commands=["pytest tests/test_fix.py -q"],
        commit_mode="live",
    )

    case_dir = Path(result["case_dir"])
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    assert result["terminal_status"] == "patched_confirmed"
    assert ledger["terminal_status"] == "patched_confirmed"
    assert ledger["commit_sha"] == "abc123"
    assert ledger["commit_acceptance_ok"] is True
    assert ledger["commit_revertability_ok"] is True
    assert "commit_acceptance_gate.json" in ledger["evidence_artifacts"]
    patch_baseline = json.loads((case_dir / "patch_baseline.json").read_text(encoding="utf-8"))
    assert patch_baseline["dirty"] is True
    patch_delta = json.loads((case_dir / "patch_delta.json").read_text(encoding="utf-8"))
    assert patch_delta["patch_changed_files"] == [
        "python/pdf_oxide/extract_for_pdflab.py",
        "tests/test_fix.py",
    ]
    assert (case_dir / "patch_scope_validation.json").is_file()
    assert (case_dir / "test_validation.json").is_file()
    assert (case_dir / "page_after.json").is_file()
    assert (case_dir / "review_after_validation.json").is_file()
    assert (case_dir / "commit_gate.json").is_file()
    assert (case_dir / "commit_acceptance_gate.json").is_file()


def test_fixture_after_review_canary_runs_real_commit_and_revertability(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()
    code_root = tmp_path / "code-root"
    code_root.mkdir()
    subprocess.run(["git", "-C", str(code_root), "init"], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "-C", str(code_root), "config", "user.name", "PDF Lab Test"], check=True)
    subprocess.run(["git", "-C", str(code_root), "config", "user.email", "pdf-lab@example.invalid"], check=True)
    (code_root / "README.md").write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(code_root), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(code_root), "commit", "-m", "initial"], check=True, stdout=subprocess.PIPE)

    def fake_extract_page_for_code_root(pdf_path, page_number, ledger_path, apply_mode, code_root):
        fixed = (Path(code_root) / "python/pdf_oxide/extract_for_pdflab.py").exists()
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [
                {
                    "id": "actual:p3:block:0",
                    "type": "table" if fixed else "unknown_region",
                    "bbox": [0.1, 0.2, 0.8, 0.4],
                    "text": "A | B",
                }
            ],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_call_opencode_patch(patch_request, **kwargs):
        root = Path(patch_request["cwd"])
        (root / "python/pdf_oxide").mkdir(parents=True, exist_ok=True)
        (root / "tests").mkdir(parents=True, exist_ok=True)
        (root / "python/pdf_oxide/extract_for_pdflab.py").write_text("FIXED = True\n", encoding="utf-8")
        (root / "tests/test_pdf_lab_canary_fix.py").write_text(
            "def test_pdf_lab_canary_fix():\n    assert True\n",
            encoding="utf-8",
        )
        return {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "endpoint": "POST /v1/scillm/opencode/runs",
            "http_status": 200,
                "request_metadata": patch_request["scillm_metadata"],
                "raw_response": {
                    "status": "completed",
                    "assistant_text": (
                        "PATCH_APPLIED changed_files=python/pdf_oxide/extract_for_pdflab.py,"
                        "tests/test_pdf_lab_canary_fix.py tests=tests/test_pdf_lab_canary_fix.py "
                        "commands=python -m py_compile tests/test_pdf_lab_canary_fix.py"
                    ),
                    "diff": "diff --git a/python/pdf_oxide/extract_for_pdflab.py b/python/pdf_oxide/extract_for_pdflab.py",
                },
            }

    monkeypatch.setattr(dag, "extract_page_for_code_root", fake_extract_page_for_code_root)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_opencode_patch", fake_call_opencode_patch)

    defect_fixture = tmp_path / "defect_fixture.json"
    defect_fixture.write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "defect",
                "page_rationale": "before patch fixture marks candidate defective",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "defect",
                        "evidence": "fixture before evidence",
                        "rationale": "unknown region should be table",
                        "suggested_fix_surface": "python/pdf_oxide",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    clean_after_fixture = tmp_path / "clean_after_fixture.json"
    clean_after_fixture.write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean",
                "page_rationale": "after patch fixture marks candidate clean",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "clean",
                        "evidence": "fixture after evidence",
                        "rationale": "table structure is now represented",
                        "suggested_fix_surface": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-after-fixture-canary",
        review_mode="fixture",
        review_fixture_path=defect_fixture,
        review_after_fixture_path=clean_after_fixture,
        patch_mode="live",
        scillm_preflight_mode="dry_run",
        validation_commands=["python -m py_compile tests/test_pdf_lab_canary_fix.py"],
        commit_mode="live",
        code_root=code_root,
    )

    case_dir = Path(result["case_dir"])
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    commit_gate = json.loads((case_dir / "commit_gate.json").read_text(encoding="utf-8"))
    patch_delta = json.loads((case_dir / "patch_delta.json").read_text(encoding="utf-8"))
    assert result["terminal_status"] == "patched_confirmed"
    assert ledger["reason"] == "patch_validated_and_committed_with_after_fixture"
    assert ledger["commit_sha"] == commit_gate["commit_sha"]
    assert ledger["commit_acceptance_ok"] is True
    assert ledger["commit_revertability_ok"] is True
    assert "review_after_fixture.json" in ledger["evidence_artifacts"]
    assert "commit_acceptance_gate.json" in ledger["evidence_artifacts"]
    assert "revertability_check.json" in ledger["evidence_artifacts"]
    assert (case_dir / "commit_acceptance_gate.json").is_file()
    assert patch_delta["patch_changed_files"] == [
        "python/pdf_oxide/extract_for_pdflab.py",
        "tests/test_pdf_lab_canary_fix.py",
    ]
    assert commit_gate["ok"] is True
    assert commit_gate["exact_file_match"] is True
    assert commit_gate["committed_files"] == patch_delta["patch_changed_files"]
    assert commit_gate["revertability_check"]["ok"] is True
    with zipfile.ZipFile(case_dir / "review_bundle.zip") as bundle:
        assert "review_after_fixture.json" in bundle.namelist()
        assert "commit_acceptance_gate.json" in bundle.namelist()
        assert "revertability_check.json" in bundle.namelist()


def test_compute_patch_delta_fails_when_delegate_only_touches_preexisting_dirty_files() -> None:
    dag = _load_module()

    delta = dag.compute_patch_delta(
        ["python/pdf_oxide/extract_for_pdflab.py", "tests/existing_dirty.py"],
        ["python/pdf_oxide/extract_for_pdflab.py", "tests/existing_dirty.py"],
    )

    assert delta["ok"] is False
    assert delta["patch_changed_files"] == []
    assert delta["errors"] == ["patch produced no isolatable new changed files"]


def test_verified_patch_flow_uses_configured_code_root(tmp_path: Path, monkeypatch) -> None:
    dag = _load_module()
    code_root = tmp_path / "isolated-code-root"
    code_root.mkdir()
    seen: dict[str, list[str]] = {"extract": [], "git": [], "tests": [], "commit": []}

    def fake_extract_page(pdf_path, page_number, ledger_path, apply_mode, repo=dag.REPO):
        seen["extract"].append(str(repo))
        return {
            "page": page_number,
            "pdf_page_index": page_number - 1,
            "blocks": [{"id": "actual:p3:block:0", "type": "table", "bbox": [0.1, 0.2, 0.8, 0.4], "text": "A | B"}],
        }

    def fake_render_original(pdf_path, page_number, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"png")

    def fake_render_overlay(pdf_path, page_number, candidates, out, dpi):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"overlay")

    def fake_call_scillm_review(review_request, **kwargs):
        after = review_request["artifacts"]["page_json"] == "page_after.json"
        return {
            "schema": "pdf_lab.second_pass.scillm_review_receipt.v1",
            "endpoint": "POST /v1/chat/completions",
            "http_status": 200,
            "scillm_metadata": review_request["scillm_metadata"],
            "raw_response": {"choices": [{"message": {"content": "{}"}}]},
            "review_response": {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean" if after else "defect",
                "page_rationale": "clean after patch" if after else "defect before patch",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0003:0000:unknown_layout",
                        "status": "clean" if after else "defect",
                        "evidence": "candidate evidence reviewed",
                        "rationale": "after evidence is clean" if after else "before evidence is defective",
                        "suggested_fix_surface": "none" if after else "python/pdf_oxide",
                    }
                ],
            },
        }

    changed_snapshots = iter([
        [],
        ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
    ])

    def fake_git_changed_files(repo=dag.REPO):
        seen["git"].append(str(repo))
        return next(changed_snapshots)

    def fake_run_validation_commands(commands, cwd=dag.REPO, required_test_files=None):
        seen["tests"].append(str(cwd))
        return {
            "schema": "pdf_lab.second_pass.test_validation.v1",
            "ok": True,
            "errors": [],
            "results": [{"command": commands[0], "exit_code": 0, "stdout": "ok", "stderr": ""}],
            "required_test_files": sorted(required_test_files or []),
            "covered_test_files": sorted(required_test_files or []),
            "missing_test_file_coverage": [],
        }

    def fake_create_patch_commit(**kwargs):
        seen["commit"].append(str(kwargs["repo"]))
        return {
            "schema": "pdf_lab.second_pass.commit_gate.v1",
            "ok": True,
            "mode": kwargs["commit_mode"],
            "errors": [],
            "commit_sha": "abc123",
            "changed_files": kwargs["changed_files"],
            "preexisting_staged_files": [],
            "committed_files": kwargs["changed_files"],
            "exact_file_match": True,
            "revertability_check": {
                "schema": "pdf_lab.second_pass.revertability_check.v1",
                "ok": True,
                "commit_sha": "abc123",
                "errors": [],
                "revert_exit_code": 0,
            },
        }

    monkeypatch.setattr(dag, "extract_page", fake_extract_page)
    monkeypatch.setattr(dag, "render_original_page", fake_render_original)
    monkeypatch.setattr(dag, "render_candidate_overlay", fake_render_overlay)
    monkeypatch.setattr(dag, "call_scillm_review", fake_call_scillm_review)
    def fake_call_opencode_patch(patch_request, **kwargs):
        return {
            "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
            "endpoint": "POST /v1/scillm/opencode/runs",
            "http_status": 200,
            "request_metadata": patch_request["scillm_metadata"],
            "raw_response": {
                "status": "completed",
                "assistant_text": (
                    "PATCH_APPLIED changed_files=python/pdf_oxide/extract_for_pdflab.py,tests/test_fix.py "
                    "tests=tests/test_fix.py commands=pytest tests/test_fix.py -q"
                ),
                "artifacts": {"diff": "diff.patch"},
            },
        }

    monkeypatch.setattr(dag, "call_opencode_patch", fake_call_opencode_patch)
    monkeypatch.setattr(dag, "git_changed_files", fake_git_changed_files)
    monkeypatch.setattr(dag, "run_validation_commands", fake_run_validation_commands)
    monkeypatch.setattr(dag, "create_patch_commit", fake_create_patch_commit)

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "candidates": [
            {
                "candidate_id": "cand:p0003:0000:unknown_layout",
                "page_number": 3,
                "preset_type": "unknown_layout",
                "bbox": [0.1, 0.2, 0.8, 0.4],
                "features": {"block_type": "unknown_region"},
            }
        ],
    }
    sampled_cases = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "page_cases": [
            {
                "case_id": "page_case_0001_p0003",
                "page_number": 3,
                "page_index": 2,
                "candidate_ids": ["cand:p0003:0000:unknown_layout"],
                "strata": ["preset:unknown_layout"],
                "selection_probability_estimate": 0.5,
                "selection_reason": ["high_risk_preset"],
            }
        ],
    }

    result = dag.run_page_case(
        pdf_path=tmp_path / "fake.pdf",
        manifest=manifest,
        sampled_cases=sampled_cases,
        out_dir=tmp_path / "out",
        case_id="page_case_0001_p0003",
        page_number=None,
        ledger_path=None,
        apply_mode="release",
        dpi=72,
        model="gpt-5.5",
        batch_id="batch-code-root",
        review_mode="live",
        patch_mode="live",
        validation_commands=["pytest tests/test_fix.py -q"],
        commit_mode="live",
        code_root=code_root,
    )

    case_dir = Path(result["case_dir"])
    state = json.loads((case_dir / "state.json").read_text(encoding="utf-8"))
    patch_request = json.loads((case_dir / "patch_request.json").read_text(encoding="utf-8"))
    assert result["terminal_status"] == "patched_confirmed"
    assert state["code_root"] == str(code_root.resolve())
    assert patch_request["cwd"] == str(code_root.resolve())
    assert seen["extract"] == [str(code_root.resolve()), str(code_root.resolve())]
    assert seen["git"] == [str(code_root.resolve()), str(code_root.resolve())]
    assert seen["tests"] == [str(code_root.resolve())]
    assert seen["commit"] == [str(code_root.resolve())]


def test_create_patch_commit_commits_only_isolated_delta_with_trailers(tmp_path: Path) -> None:
    dag = _load_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "PDF Lab Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "pdf-lab@example.invalid"], check=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, stdout=subprocess.PIPE)

    (repo / "python/pdf_oxide").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "python/pdf_oxide/extract_for_pdflab.py").write_text("fix = True\n", encoding="utf-8")
    (repo / "tests/test_fix.py").write_text("def test_fix():\n    assert True\n", encoding="utf-8")
    changed_files = ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"]

    message = dag.build_commit_message(page_number=3, case_id="page_case_0001_p0003", changed_files=changed_files)
    result = dag.create_patch_commit(
        commit_mode="live",
        changed_files=changed_files,
        message=message,
        repo=repo,
    )

    assert result["ok"] is True
    assert result["changed_files"] == changed_files
    assert result["committed_files"] == changed_files
    assert result["exact_file_match"] is True
    commit_message = subprocess.check_output(
        ["git", "-C", str(repo), "log", "-1", "--pretty=%B"],
        text=True,
    )
    assert "PDF-Lab-Case: page_case_0001_p0003" in commit_message
    assert "Reviewed-By: pdf-lab-second-pass-harness" in commit_message
    assert "Persona-Role: deterministic-pdf-extraction-validator" in commit_message
    assert "Issue-Codes: pdf-lab-second-pass,page-3" in commit_message


def test_create_patch_commit_rejects_preexisting_staged_files(tmp_path: Path) -> None:
    dag = _load_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "PDF Lab Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "pdf-lab@example.invalid"], check=True)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, stdout=subprocess.PIPE)

    (repo / "UNRELATED.md").write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "UNRELATED.md"], check=True)
    (repo / "tests").mkdir()
    (repo / "tests/test_fix.py").write_text("def test_fix():\n    assert True\n", encoding="utf-8")

    result = dag.create_patch_commit(
        commit_mode="live",
        changed_files=["tests/test_fix.py"],
        message=dag.build_commit_message(page_number=3, case_id="page_case_0001_p0003", changed_files=["tests/test_fix.py"]),
        repo=repo,
    )

    assert result["ok"] is False
    assert result["commit_sha"] is None
    assert result["preexisting_staged_files"] == ["UNRELATED.md"]
    assert any("preexisting staged files" in error for error in result["errors"])
    assert subprocess.check_output(["git", "-C", str(repo), "rev-list", "--count", "HEAD"], text=True).strip() == "1"
