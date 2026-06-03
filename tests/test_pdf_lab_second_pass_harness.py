from __future__ import annotations

import importlib.util
import json
import math
import sys
import zipfile
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts/pdf_lab/run_second_pass_harness.py"
    spec = importlib.util.spec_from_file_location("run_second_pass_harness_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _passing_sampling_audit(*, candidate_count: int, selected_count: int, seed: int = 1234) -> dict:
    return {
        "schema": "pdf_lab.second_pass.sampling_audit.v1",
        "candidate_count": candidate_count,
        "requested_sample_size": selected_count,
        "seed": seed,
        "selected_count": selected_count,
        "recommended_min_sample_size": selected_count,
        "statistical_significance_basis": {
            "method": "stratified_priority_coverage_plus_weighted_random_reserve",
            "seed": seed,
            "candidate_page_population": selected_count,
            "selected_page_count": selected_count,
            "recommended_min_sample_size": selected_count,
            "adequate": True,
        },
        "adequate_sample_size": True,
        "adequate_for_priority_strata": True,
        "covered_priority_strata": ["preset:table"],
        "missed_priority_strata": [],
        "warnings": [],
    }


def _manifest_candidate(candidate_id: str, page_number: int, preset_type: str, block_index: int = 0) -> dict:
    return {
        "candidate_id": candidate_id,
        "page_number": page_number,
        "page_index": page_number - 1,
        "block_id": f"b{page_number}",
        "block_index": block_index,
        "preset_type": preset_type,
        "bbox": [0.1, 0.2, 0.8, 0.4],
        "json_pointer": f"/pages/{page_number - 1}/blocks/{block_index}",
        "detection_reason": ["hardening_interest"],
    }


def _sampled_page_case(
    *,
    candidate_id: str,
    page_number: int,
    preset_type: str = "table",
    case_index: int = 1,
    forced: bool = False,
) -> dict:
    case_id = f"page_case_{case_index:04d}_p{page_number:04d}"
    return {
        "case_id": case_id,
        "page_number": page_number,
        "candidate_ids": [candidate_id],
        "preset_counts": {preset_type: 1},
        "strata": [f"preset:{preset_type}", "risk:high"],
        "forced_by_human_annotation": forced,
        "selection_probability_estimate": 1.0 if forced else 0.5,
        "selection_probability_basis": {
            "method": "forced_human_annotation" if forced else "max(weighted_page_score_inclusion_estimate,candidate_share_estimate)",
            "forced_page": forced,
        },
        "selection_reason": ["human_annotated_page"] if forced else ["high_risk_preset"],
    }


def _candidate_manifest(candidates: list[dict], **extra: object) -> dict:
    preset_counts: dict[str, int] = {}
    page_counts: dict[int, int] = {}
    page_preset_counts: dict[int, dict[str, int]] = {}
    for candidate in candidates:
        preset_type = candidate["preset_type"]
        page_number = candidate["page_number"]
        preset_counts[preset_type] = preset_counts.get(preset_type, 0) + 1
        page_counts[page_number] = page_counts.get(page_number, 0) + 1
        page_preset_counts.setdefault(page_number, {})
        page_preset_counts[page_number][preset_type] = page_preset_counts[page_number].get(preset_type, 0) + 1
    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "preset_types": sorted(set(preset_counts) | {"text"}),
        "candidate_count": len(candidates),
        "preset_counts": dict(sorted(preset_counts.items())),
        "pages": [
            {
                "page_number": page_number,
                "candidate_count": page_counts[page_number],
                "risk_candidate_count": page_counts[page_number],
                "preset_counts": dict(sorted(page_preset_counts[page_number].items())),
            }
            for page_number in sorted(page_counts)
        ],
        "candidates": candidates,
    }
    manifest.update(extra)
    return manifest


def _mark_isolated_code_root(code_root: Path) -> None:
    code_root.mkdir(parents=True, exist_ok=True)
    (code_root / ".pdf_lab_isolated_code_root.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.isolated_code_root.v1",
                "dest_root": str(code_root.resolve()),
                "clean": True,
            }
        ),
        encoding="utf-8",
    )


def test_live_code_root_visibility_requires_mounted_isolated_root(tmp_path: Path) -> None:
    harness = _load_module()
    mounted = tmp_path / "mounted"
    code_root = mounted / "code-root"
    code_root.mkdir(parents=True)

    validation = harness.validate_scillm_live_code_root(
        code_root=code_root,
        patch_mode="live",
        patch_backend="opencode_serve",
        mounted_prefixes=[mounted],
        isolated_code_root_manifest=None,
    )

    assert validation["ok"] is False
    assert validation["under_mounted_prefix"] is True
    assert validation["isolated_code_root_required"] is True
    assert validation["isolated_marker_present"] is False
    assert "must be an isolated pdf-lab code root" in "\n".join(validation["errors"])

    (code_root / ".pdf_lab_isolated_code_root.json").write_text("{}", encoding="utf-8")
    malformed = harness.validate_scillm_live_code_root(
        code_root=code_root,
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        mounted_prefixes=[mounted],
        isolated_code_root_manifest=None,
    )

    assert malformed["ok"] is False
    assert malformed["isolated_marker_present"] is True
    assert "isolated code root marker schema mismatch" in malformed["errors"]

    _mark_isolated_code_root(code_root)
    accepted = harness.validate_scillm_live_code_root(
        code_root=code_root,
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        mounted_prefixes=[mounted],
        isolated_code_root_manifest=None,
    )

    assert accepted["ok"] is True
    assert accepted["isolated_marker_present"] is True
    assert accepted["isolated_marker_schema"] == "pdf_lab.second_pass.isolated_code_root.v1"
    assert accepted["isolated_marker_dest_root"] == str(code_root.resolve())


def test_live_code_root_visibility_rejects_marker_dest_root_mismatch(tmp_path: Path) -> None:
    harness = _load_module()
    mounted = tmp_path / "mounted"
    code_root = mounted / "code-root"
    code_root.mkdir(parents=True)
    (code_root / ".pdf_lab_isolated_code_root.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.isolated_code_root.v1",
                "dest_root": str((mounted / "other-root").resolve()),
            }
        ),
        encoding="utf-8",
    )

    validation = harness.validate_scillm_live_code_root(
        code_root=code_root,
        patch_mode="live",
        patch_backend="opencode_serve",
        mounted_prefixes=[mounted],
        isolated_code_root_manifest={
            "schema": "pdf_lab.second_pass.isolated_code_root.v1",
            "dest_root": str((mounted / "third-root").resolve()),
        },
    )

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "marker dest_root does not match code_root" in errors
    assert "manifest dest_root does not match code_root" in errors


def test_dry_code_root_visibility_does_not_require_isolated_marker(tmp_path: Path) -> None:
    harness = _load_module()
    code_root = tmp_path / "plain-code-root"
    code_root.mkdir()

    validation = harness.validate_scillm_live_code_root(
        code_root=code_root,
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        mounted_prefixes=[tmp_path / "other-mounted-prefix"],
        isolated_code_root_manifest=None,
    )

    assert validation["ok"] is True
    assert validation["live_patch_required"] is False
    assert validation["isolated_code_root_required"] is False
    assert validation["under_mounted_prefix"] is False


PAGE_DAG_ARTIFACTS = [
    "state.json",
    "sampled_candidate_manifest.json",
    "page_before.json",
    "page_before.png",
    "page_candidates.png",
    "candidate_presets.json",
    "review_request.json",
    "scillm_orchestrator_page_dag_spec.json",
    "scillm_orchestrator_page_dag_spec_validation.json",
    "scillm_orchestrator_page_submission.json",
    "scillm_orchestrator_page_submission_validation.json",
    "review_validation.json",
    "scillm_page_orchestrator_run_request.json",
    "scillm_page_orchestrator_run_validation.json",
    "review.html",
    "terminal_ledger_validation.json",
]
PATCHED_CONFIRMED_ARTIFACTS = [
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
]


def _page_number_from_case_id(case_id: str) -> int:
    marker = "_p"
    if marker not in case_id:
        return 1
    suffix = case_id.rsplit(marker, 1)[1]
    return int(suffix) if suffix.isdigit() else 1


def _write_page_dag_case(
    case_dir: Path,
    *,
    case_id: str,
    terminal_status: str = "reviewed_clean",
    reason: str = "test",
    commit_sha: str | None = None,
    extra_evidence: list[str] | None = None,
) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    page_number = _page_number_from_case_id(case_id)
    artifacts = [*PAGE_DAG_ARTIFACTS, *(extra_evidence or [])]
    for name in artifacts:
        path = case_dir / name
        if path.suffix == ".png" or path.suffix == ".zip":
            path.write_bytes(b"artifact")
        elif name == "commit_acceptance_gate.json":
            path.write_text(
                json.dumps(
                    {
                        "schema": "pdf_lab.second_pass.commit_acceptance_gate.v1",
                        "ok": True,
                        "commit_sha": commit_sha,
                    }
                ),
                encoding="utf-8",
            )
        elif name == "commit_gate.json":
            path.write_text(
                json.dumps(
                    {
                        "schema": "pdf_lab.second_pass.commit_gate.v1",
                        "ok": True,
                        "commit_sha": commit_sha,
                        "exact_file_match": True,
                        "committed_files": ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
                        "revertability_check": {
                            "schema": "pdf_lab.second_pass.revertability_check.v1",
                            "ok": True,
                            "commit_sha": commit_sha,
                        },
                    }
                ),
                encoding="utf-8",
            )
        elif name == "patch_scope_validation.json":
            path.write_text(
                json.dumps(
                    {
                        "schema": "pdf_lab.second_pass.patch_scope_validation.v1",
                        "ok": True,
                        "errors": [],
                        "changed_files": ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
                        "test_files": ["tests/test_fix.py"],
                    }
                ),
                encoding="utf-8",
            )
        elif name == "test_validation.json":
            path.write_text(
                json.dumps(
                    {
                        "schema": "pdf_lab.second_pass.test_validation.v1",
                        "ok": True,
                        "errors": [],
                        "results": [{"command": "pytest tests/test_fix.py -q", "exit_code": 0}],
                        "required_test_files": ["tests/test_fix.py"],
                        "covered_test_files": ["tests/test_fix.py"],
                        "missing_test_file_coverage": [],
                    }
                ),
                encoding="utf-8",
            )
        elif name == "review_after_request_validation.json":
            path.write_text(
                json.dumps(
                    {
                        "schema": "pdf_lab.second_pass.review_request_validation.v1",
                        "ok": True,
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )
        elif name == "review_after_validation.json":
            path.write_text(
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
        elif name == "review_after_response.json":
            path.write_text(
                json.dumps(
                    {
                        "schema": "pdf_lab.second_pass.review_response.v1",
                        "page_status": "clean",
                        "page_rationale": "after-patch extraction is clean",
                        "candidate_findings": [
                            {
                                "candidate_id": "cand:p0001:0000:table",
                                "status": "clean",
                                "evidence": "candidate renders correctly after patch",
                                "rationale": "validated after patch",
                                "suggested_fix_surface": "none",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
        elif name == "revertability_check.json":
            path.write_text(
                json.dumps(
                    {
                        "schema": "pdf_lab.second_pass.revertability_check.v1",
                        "ok": True,
                        "commit_sha": commit_sha,
                    }
                ),
                encoding="utf-8",
            )
        else:
            path.write_text(json.dumps({"artifact": name}), encoding="utf-8")
    evidence = ["terminal_ledger.json", *artifacts]
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": case_id,
        "page_number": page_number,
        "terminal_status": terminal_status,
        "reason": reason,
        "commit_sha": commit_sha,
        "evidence_artifacts": evidence,
    }
    if terminal_status == "patched_confirmed":
        terminal.update(
            {
                "commit_gate_ok": True,
                "commit_acceptance_ok": True,
                "commit_revertability_ok": True,
                "commit_exact_file_match": True,
            }
        )
    (case_dir / "terminal_ledger.json").write_text(json.dumps(terminal), encoding="utf-8")
    (case_dir / "terminal_ledger_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_terminal_ledger_validation.v1",
                "ok": True,
                "errors": [],
                "case_id": case_id,
                "page_number": page_number,
                "terminal_status": terminal_status,
                "declared_evidence_count": len(evidence),
                "missing_artifacts": [],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "review_bundle.zip").write_bytes(b"zip")
    required_zip_entries = sorted(set(evidence) | {"terminal_ledger.json", "terminal_ledger_validation.json", "review.html"})
    (case_dir / "review_bundle_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_review_bundle_validation.v1",
                "ok": True,
                "errors": [],
                "case_id": case_id,
                "page_number": page_number,
                "zip_path": str(case_dir / "review_bundle.zip"),
                "required_zip_entries": required_zip_entries,
                "zip_content_ok": True,
                "missing_artifacts": [],
                "missing_expected_zip_entries": [],
                "duplicate_zip_entries": [],
            }
        ),
        encoding="utf-8",
    )


def _write_sampled_page_cases(path: Path, cases: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                "selected_count": len(cases),
                "selected_pages": [case["page_number"] for case in cases],
                "page_cases": cases,
            }
        ),
        encoding="utf-8",
    )


def test_aggregate_page_results_fails_closed_on_nonterminal() -> None:
    harness = _load_module()
    aggregate = harness.aggregate_page_results(
        [
            {"case_id": "a", "page_number": 1, "terminal_status": "reviewed_clean"},
            {"case_id": "b", "page_number": 2, "terminal_status": "still_open"},
        ]
    )

    assert aggregate["ok"] is False
    assert aggregate["unresolved_count"] == 1
    assert "b" in aggregate["errors"][0]


def test_aggregate_page_results_fails_closed_on_blocked_substrate() -> None:
    harness = _load_module()
    aggregate = harness.aggregate_page_results(
        [
            {"case_id": "a", "page_number": 1, "terminal_status": "reviewed_clean"},
            {"case_id": "b", "page_number": 2, "terminal_status": "blocked_substrate"},
        ]
    )

    assert aggregate["ok"] is False
    assert aggregate["nonterminal_count"] == 0
    assert aggregate["unresolved_count"] == 1
    assert "unresolved" in aggregate["errors"][0]


def test_aggregate_requires_commit_sha_for_patched_confirmed() -> None:
    harness = _load_module()
    aggregate = harness.aggregate_page_results(
        [
            {"case_id": "a", "page_number": 1, "terminal_status": "patched_confirmed", "commit_sha": None},
        ]
    )

    assert aggregate["ok"] is False
    assert aggregate["patched_without_commit_count"] == 1


def test_aggregate_requires_commit_gate_and_revertability_evidence_for_patched_confirmed() -> None:
    harness = _load_module()
    aggregate = harness.aggregate_page_results(
        [
            {
                "case_id": "a",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "commit_sha": "abc123",
                "evidence_artifacts": ["commit_gate.json"],
            },
        ]
    )

    assert aggregate["ok"] is False
    assert aggregate["patched_missing_commit_gate_artifacts_count"] == 1
    assert "missing commit_acceptance_gate.json" in "\n".join(aggregate["errors"])


def test_aggregate_requires_unique_commit_sha_per_patched_page() -> None:
    harness = _load_module()
    aggregate = harness.aggregate_page_results(
        [
            {
                "case_id": "a",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "commit_sha": "abc123",
                "evidence_artifacts": ["commit_acceptance_gate.json", "commit_gate.json", "revertability_check.json"],
            },
            {
                "case_id": "b",
                "page_number": 2,
                "terminal_status": "patched_confirmed",
                "commit_sha": "abc123",
                "evidence_artifacts": ["commit_acceptance_gate.json", "commit_gate.json", "revertability_check.json"],
            },
        ]
    )

    assert aggregate["ok"] is False
    assert aggregate["duplicate_commit_shas"] == ["abc123"]
    assert "not one-commit-per-page unique" in "\n".join(aggregate["errors"])


def test_aggregate_requires_unique_case_id_and_page_number() -> None:
    harness = _load_module()
    aggregate = harness.aggregate_page_results(
        [
            {"case_id": "dup", "page_number": 7, "terminal_status": "reviewed_clean"},
            {"case_id": "dup", "page_number": 7, "terminal_status": "reviewed_clean"},
            {"terminal_status": "reviewed_clean"},
        ]
    )

    errors = "\n".join(aggregate["errors"])
    assert aggregate["ok"] is False
    assert aggregate["duplicate_case_ids"] == ["dup"]
    assert aggregate["duplicate_page_numbers"] == [7]
    assert aggregate["missing_case_id_count"] == 1
    assert aggregate["missing_page_number_count"] == 1
    assert "duplicate page result case_ids" in errors
    assert "duplicate page result page_numbers" in errors


def test_aggregate_rejects_malformed_page_result_case_ids() -> None:
    harness = _load_module()
    aggregate = harness.aggregate_page_results(
        [
            {"case_id": "../escape", "page_number": 1, "terminal_status": "reviewed_clean"},
            {"case_id": "page_case_0002_p0001", "page_number": 2, "terminal_status": "reviewed_clean"},
        ]
    )

    errors = "\n".join(aggregate["errors"])
    assert aggregate["ok"] is False
    assert aggregate["malformed_case_ids"] == ["../escape"]
    assert aggregate["case_id_page_suffix_mismatches"] == ["page_case_0002_p0001"]
    assert "malformed page result case_ids: ['../escape']" in errors
    assert "page result case_id page suffixes do not match page_number: ['page_case_0002_p0001']" in errors


def test_aggregate_rejects_raw_page_result_identity_mismatch() -> None:
    harness = _load_module()
    aggregate = harness.aggregate_page_results(
        [
            {
                "case_id": "page_case_0001_p0001",
                "page_number": 1,
                "terminal_status": "reviewed_clean",
                "raw_result_case_id": "page_case_9999_p9999",
                "raw_result_page_number": 9999,
                "raw_result_identity_mismatch_errors": [
                    "raw page result case_id 'page_case_9999_p9999' does not match sampled case_id 'page_case_0001_p0001'",
                    "raw page result page_number 9999 does not match sampled page_number 1",
                ],
            },
        ]
    )

    assert aggregate["ok"] is False
    assert aggregate["raw_identity_mismatch_count"] == 1
    assert aggregate["raw_identity_mismatch_cases"][0]["case_id"] == "page_case_0001_p0001"
    assert "raw page result identity mismatches" in "\n".join(aggregate["errors"])


def test_aggregate_indexes_scillm_patch_delegate_bug_reports() -> None:
    harness = _load_module()
    aggregate = harness.aggregate_page_results(
        [
            {"case_id": "a", "page_number": 1, "terminal_status": "reviewed_clean"},
            {
                "case_id": "b",
                "page_number": 2,
                "terminal_status": "blocked_substrate",
                "scillm_patch_delegate_bug_report": "/tmp/b/scillm_patch_delegate_bug_report.json",
            },
        ]
    )

    assert aggregate["scillm_patch_delegate_bug_report_count"] == 1
    assert aggregate["scillm_patch_delegate_bug_report_cases"][0]["case_id"] == "b"


def test_validate_sampling_gate_fails_closed_on_inadequate_priority_sampling() -> None:
    harness = _load_module()
    gate = harness.validate_sampling_gate(
        manifest={"candidate_count": 8},
        sampled_cases={
            "selected_count": 3,
            "seed": 1234,
            "sampling_audit": {
                "schema": "pdf_lab.second_pass.sampling_audit.v1",
                "seed": 1234,
                "selected_count": 3,
                "adequate_sample_size": False,
                "adequate_for_priority_strata": False,
                "recommended_min_sample_size": 8,
                "statistical_significance_basis": {
                    "seed": 1234,
                    "adequate": False,
                    "recommended_min_sample_size": 8,
                },
                "covered_priority_strata": ["preset:table"],
                "missed_priority_strata": ["preset:equation", "preset:footnote"],
                "warnings": ["priority strata not represented in selected pages"],
            },
        },
    )

    assert gate["schema"] == "pdf_lab.second_pass.sampling_gate.v1"
    assert gate["ok"] is False
    assert gate["recommended_min_sample_size"] == 8
    assert "sampling_audit adequate_sample_size is not true" in gate["errors"]
    assert "sampling_audit statistical_significance_basis.adequate is not true" in gate["errors"]
    assert "preset:equation" in "\n".join(gate["errors"])


def test_validate_sampling_gate_rejects_malformed_statistical_audit() -> None:
    harness = _load_module()
    gate = harness.validate_sampling_gate(
        manifest={"candidate_count": 8},
        sampled_cases={
            "selected_count": 4,
            "seed": 777,
            "sampling_audit": {
                "schema": "wrong.schema",
                "seed": 778,
                "selected_count": 3,
                "adequate_sample_size": True,
                "adequate_for_priority_strata": True,
                "recommended_min_sample_size": 4,
                "statistical_significance_basis": {
                    "seed": 779,
                    "adequate": True,
                    "recommended_min_sample_size": 7,
                },
                "covered_priority_strata": ["preset:table"],
                "missed_priority_strata": [],
                "warnings": [],
            },
        },
    )

    assert gate["ok"] is False
    assert gate["sampling_audit_schema"] == "wrong.schema"
    assert gate["statistical_significance_adequate"] is True
    errors = "\n".join(gate["errors"])
    assert "sampling_audit schema mismatch" in errors
    assert "sampling_audit seed does not match sampled_page_cases seed" in errors
    assert "sampling_audit statistical_significance_basis seed does not match sampled_page_cases seed" in errors
    assert "sampling_audit statistical_significance_basis recommended_min_sample_size mismatch" in errors
    assert "sampling_audit selected_count does not match sampled_page_cases selected_count" in errors


def test_validate_sampling_gate_requires_additive_forced_page_accounting() -> None:
    harness = _load_module()
    gate = harness.validate_sampling_gate(
        manifest={"candidate_count": 8},
        sampled_cases={
            "selected_count": 4,
            "seed": 1234,
            "forced_pages": {"requested": [1], "accepted": [1], "rejected": []},
            "probabilistic_selected_pages": [1, 2],
            "sampling_audit": {
                "schema": "pdf_lab.second_pass.sampling_audit.v1",
                "seed": 1234,
                "selected_count": 4,
                "probabilistic_selected_count": 3,
                "forced_pages_are_additive": False,
                "adequate_sample_size": True,
                "adequate_for_priority_strata": True,
                "recommended_min_sample_size": 3,
                "statistical_significance_basis": {
                    "seed": 1234,
                    "adequate": True,
                    "recommended_min_sample_size": 3,
                    "probabilistic_selected_page_count": 3,
                    "accepted_forced_page_count": 0,
                    "forced_pages_are_additive": False,
                },
                "covered_priority_strata": ["preset:table"],
                "missed_priority_strata": [],
                "warnings": [],
            },
        },
    )

    errors = "\n".join(gate["errors"])
    assert gate["ok"] is False
    assert gate["accepted_forced_page_count"] == 1
    assert "sampling_audit forced_pages_are_additive is not true" in errors
    assert "sampling_audit statistical_significance_basis forced_pages_are_additive is not true" in errors
    assert "probabilistic_selected_pages overlaps accepted forced pages" in errors
    assert "selected_count does not equal probabilistic_selected_pages plus accepted forced pages" in errors
    assert "sampling_audit probabilistic_selected_count mismatch" in errors
    assert "sampling_audit statistical_significance_basis accepted_forced_page_count mismatch" in errors


def test_validate_sampling_gate_accepts_additive_forced_page_accounting() -> None:
    harness = _load_module()
    gate = harness.validate_sampling_gate(
        manifest={"candidate_count": 8},
        sampled_cases={
            "selected_count": 4,
            "seed": 1234,
            "forced_pages": {"requested": [1], "accepted": [1], "rejected": []},
            "probabilistic_selected_pages": [2, 3, 4],
            "sampling_audit": {
                "schema": "pdf_lab.second_pass.sampling_audit.v1",
                "seed": 1234,
                "selected_count": 4,
                "probabilistic_selected_count": 3,
                "forced_pages_are_additive": True,
                "adequate_sample_size": True,
                "adequate_for_priority_strata": True,
                "recommended_min_sample_size": 3,
                "statistical_significance_basis": {
                    "seed": 1234,
                    "adequate": True,
                    "recommended_min_sample_size": 3,
                    "probabilistic_selected_page_count": 3,
                    "accepted_forced_page_count": 1,
                    "forced_pages_are_additive": True,
                },
                "covered_priority_strata": ["preset:table"],
                "missed_priority_strata": [],
                "warnings": [],
            },
        },
    )

    assert gate["ok"] is True
    assert gate["accepted_forced_page_count"] == 1
    assert gate["probabilistic_selected_count"] == 3
    assert gate["forced_pages_are_additive"] is True


def test_validate_candidate_sample_linkage_fails_on_unknown_candidate_id() -> None:
    harness = _load_module()
    validation = harness.validate_candidate_sample_linkage(
        manifest={
            "schema": "pdf_lab.second_pass.candidate_manifest.v1",
            "candidate_count": 1,
            "candidates": [_manifest_candidate("cand:p0001:0000:table", 1, "table")],
        },
        sampled_cases={
            "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
            "selected_count": 1,
            "selected_pages": [1],
            "page_cases": [
                {
                    "case_id": "page_case_0001_p0001",
                    "page_number": 1,
                    "candidate_ids": ["cand:p0001:9999:table"],
                }
            ],
        },
    )

    assert validation["schema"] == "pdf_lab.second_pass.candidate_sample_linkage_validation.v1"
    assert validation["ok"] is False
    assert "sampled candidate_ids missing from manifest" in "\n".join(validation["errors"])


def test_validate_candidate_sample_linkage_requires_all_candidates_for_selected_page() -> None:
    harness = _load_module()
    validation = harness.validate_candidate_sample_linkage(
        manifest={
            "schema": "pdf_lab.second_pass.candidate_manifest.v1",
            "candidate_count": 2,
            "candidates": [
                _manifest_candidate("cand:p0001:0000:table", 1, "table", block_index=0),
                _manifest_candidate("cand:p0001:0001:equation", 1, "equation", block_index=1),
            ],
        },
        sampled_cases={
            "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
            "selected_count": 1,
            "selected_pages": [1],
            "page_cases": [
                {
                    "case_id": "page_case_0001_p0001",
                    "page_number": 1,
                    "candidate_ids": ["cand:p0001:0000:table"],
                    "preset_counts": {"table": 1},
                }
            ],
        },
    )

    errors = "\n".join(validation["errors"])
    assert validation["ok"] is False
    assert "missing manifest candidate_ids for selected page" in errors
    assert "preset_counts do not match manifest candidates" in errors


def test_validate_candidate_sample_linkage_rejects_stale_preset_counts() -> None:
    harness = _load_module()
    validation = harness.validate_candidate_sample_linkage(
        manifest={
            "schema": "pdf_lab.second_pass.candidate_manifest.v1",
            "candidate_count": 2,
            "candidates": [
                _manifest_candidate("cand:p0001:0000:table", 1, "table", block_index=0),
                _manifest_candidate("cand:p0001:0001:equation", 1, "equation", block_index=1),
            ],
        },
        sampled_cases={
            "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
            "selected_count": 1,
            "selected_pages": [1],
            "page_cases": [
                {
                    "case_id": "page_case_0001_p0001",
                    "page_number": 1,
                    "candidate_ids": ["cand:p0001:0000:table", "cand:p0001:0001:equation"],
                    "preset_counts": {"table": 2},
                }
            ],
        },
    )

    assert validation["ok"] is False
    assert "preset_counts do not match manifest candidates" in "\n".join(validation["errors"])


def test_validate_candidate_sample_linkage_requires_forced_page_partition() -> None:
    harness = _load_module()
    validation = harness.validate_candidate_sample_linkage(
        manifest={
            "schema": "pdf_lab.second_pass.candidate_manifest.v1",
            "candidate_count": 3,
            "candidates": [
                _manifest_candidate("cand:p0001:0000:table", 1, "table"),
                _manifest_candidate("cand:p0002:0000:equation", 2, "equation"),
                _manifest_candidate("cand:p0003:0000:figure", 3, "figure"),
            ],
        },
        sampled_cases={
            "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
            "selected_count": 3,
            "selected_pages": [1, 2, 3],
            "forced_pages": {"requested": [1], "accepted": [1], "rejected": []},
            "probabilistic_selected_pages": [1, 2],
            "page_cases": [
                {
                    "case_id": "page_case_0001_p0001",
                    "page_number": 1,
                    "candidate_ids": ["cand:p0001:0000:table"],
                    "forced_by_human_annotation": False,
                },
                {
                    "case_id": "page_case_0002_p0002",
                    "page_number": 2,
                    "candidate_ids": ["cand:p0002:0000:equation"],
                    "forced_by_human_annotation": False,
                },
                {
                    "case_id": "page_case_0003_p0003",
                    "page_number": 3,
                    "candidate_ids": ["cand:p0003:0000:figure"],
                    "forced_by_human_annotation": False,
                },
            ],
        },
    )

    errors = "\n".join(validation["errors"])
    assert validation["ok"] is False
    assert "forced page case flags do not match accepted forced pages" in errors
    assert "probabilistic_selected_pages overlap accepted forced pages" in errors
    assert "selected_pages do not equal probabilistic_selected_pages plus accepted forced pages" in errors


def test_validate_candidate_sample_linkage_accepts_forced_page_partition() -> None:
    harness = _load_module()
    validation = harness.validate_candidate_sample_linkage(
        manifest={
            "schema": "pdf_lab.second_pass.candidate_manifest.v1",
            "candidate_count": 3,
            "candidates": [
                _manifest_candidate("cand:p0001:0000:table", 1, "table"),
                _manifest_candidate("cand:p0002:0000:equation", 2, "equation"),
                _manifest_candidate("cand:p0003:0000:figure", 3, "figure"),
            ],
        },
        sampled_cases={
            "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
            "selected_count": 3,
            "selected_pages": [1, 2, 3],
            "forced_pages": {"requested": [1], "accepted": [1], "rejected": []},
            "probabilistic_selected_pages": [2, 3],
            "page_cases": [
                _sampled_page_case(candidate_id="cand:p0001:0000:table", page_number=1, preset_type="table", case_index=1, forced=True),
                _sampled_page_case(candidate_id="cand:p0002:0000:equation", page_number=2, preset_type="equation", case_index=2),
                _sampled_page_case(candidate_id="cand:p0003:0000:figure", page_number=3, preset_type="figure", case_index=3),
            ],
        },
    )

    assert validation["ok"] is True
    assert validation["accepted_forced_pages"] == [1]
    assert validation["probabilistic_selected_pages"] == [2, 3]


def test_validate_candidate_sample_linkage_rejects_duplicate_sampled_pages() -> None:
    harness = _load_module()
    validation = harness.validate_candidate_sample_linkage(
        manifest={
            "schema": "pdf_lab.second_pass.candidate_manifest.v1",
            "candidate_count": 1,
            "candidates": [_manifest_candidate("cand:p0001:0000:table", 1, "table")],
        },
        sampled_cases={
            "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
            "selected_count": 2,
            "selected_pages": [1, 1],
            "page_cases": [
                {
                    "case_id": "page_case_0001_p0001",
                    "page_number": 1,
                    "candidate_ids": ["cand:p0001:0000:table"],
                },
                {
                    "case_id": "page_case_0001_p0001",
                    "page_number": 1,
                    "candidate_ids": ["cand:p0001:0000:table"],
                },
            ],
        },
    )

    errors = "\n".join(validation["errors"])
    assert validation["ok"] is False
    assert "selected_pages contains duplicates: [1]" in errors
    assert "page_cases contains duplicate case_ids: ['page_case_0001_p0001']" in errors
    assert "page_cases contains duplicate page_numbers: [1]" in errors


def test_validate_candidate_sample_linkage_rejects_malformed_candidate_ids() -> None:
    harness = _load_module()
    validation = harness.validate_candidate_sample_linkage(
        manifest={
            "schema": "pdf_lab.second_pass.candidate_manifest.v1",
            "candidate_count": 1,
            "candidates": [_manifest_candidate("cand:p0001:0000:table", 1, "table")],
        },
        sampled_cases={
            "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
            "selected_count": 1,
            "selected_pages": [1],
            "page_cases": [
                {
                    "case_id": "page_case_0001_p0001",
                    "page_number": 1,
                    "candidate_ids": ["cand:p0001:0000:table", "cand:p0001:0000:table", None],
                },
            ],
        },
    )

    errors = "\n".join(validation["errors"])
    assert validation["ok"] is False
    assert "page_case_0001_p0001 candidate_ids must be a list of non-empty strings" in errors
    assert "page_case_0001_p0001 candidate_ids contains duplicates: ['cand:p0001:0000:table']" in errors


def test_validate_candidate_sample_linkage_requires_sampling_metadata() -> None:
    harness = _load_module()
    validation = harness.validate_candidate_sample_linkage(
        manifest={
            "schema": "pdf_lab.second_pass.candidate_manifest.v1",
            "candidate_count": 1,
            "candidates": [_manifest_candidate("cand:p0001:0000:table", 1, "table")],
        },
        sampled_cases={
            "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
            "selected_count": 1,
            "selected_pages": [1],
            "forced_pages": {"requested": [1], "accepted": [1], "rejected": []},
            "probabilistic_selected_pages": [],
            "page_cases": [
                {
                    "case_id": "page_case_0001_p0001",
                    "page_number": 1,
                    "candidate_ids": ["cand:p0001:0000:table"],
                    "preset_counts": "stale",
                    "strata": [],
                    "selection_reason": [],
                    "forced_by_human_annotation": True,
                    "selection_probability_estimate": 0.5,
                    "selection_probability_basis": {"method": "weighted", "forced_page": False},
                },
            ],
        },
    )

    errors = "\n".join(validation["errors"])
    assert validation["ok"] is False
    assert validation["malformed_sampling_metadata_case_ids"] == ["page_case_0001_p0001"]
    assert validation["malformed_forced_probability_case_ids"] == ["page_case_0001_p0001"]
    assert "sampled page cases have malformed sampling metadata: ['page_case_0001_p0001']" in errors
    assert "forced sampled page cases missing forced_human_annotation probability basis: ['page_case_0001_p0001']" in errors


def test_validate_candidate_sample_linkage_rejects_malformed_page_case_ids() -> None:
    harness = _load_module()
    validation = harness.validate_candidate_sample_linkage(
        manifest={
            "schema": "pdf_lab.second_pass.candidate_manifest.v1",
            "candidate_count": 2,
            "candidates": [
                _manifest_candidate("cand:p0001:0000:table", 1, "table"),
                _manifest_candidate("cand:p0002:0000:table", 2, "table"),
            ],
        },
        sampled_cases={
            "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
            "selected_count": 2,
            "selected_pages": [1, 2],
            "page_cases": [
                {
                    "case_id": "../escape",
                    "page_number": 1,
                    "candidate_ids": ["cand:p0001:0000:table"],
                },
                {
                    "case_id": "page_case_0002_p0001",
                    "page_number": 2,
                    "candidate_ids": ["cand:p0002:0000:table"],
                },
            ],
        },
    )

    errors = "\n".join(validation["errors"])
    assert validation["ok"] is False
    assert "../escape case_id must match page_case_####_p####" in errors
    assert "page_case_0002_p0001 case_id page suffix does not match page_number 2" in errors


def test_validate_candidate_manifest_integrity_rejects_stale_counts() -> None:
    harness = _load_module()
    valid_manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "preset_types": ["table", "text"],
        "candidate_count": 1,
        "preset_counts": {"table": 1},
        "pages": [
            {
                "page_number": 1,
                "candidate_count": 1,
                "risk_candidate_count": 1,
                "preset_counts": {"table": 1},
            }
        ],
        "candidates": [_manifest_candidate("cand:p0001:0000:table", 1, "table")],
    }
    stale_manifest = {
        **valid_manifest,
        "candidate_count": 2,
        "preset_counts": {"table": 2},
        "pages": [
            {
                "page_number": 1,
                "candidate_count": 2,
                "risk_candidate_count": 2,
                "preset_counts": {"table": 2},
            }
        ],
    }

    valid = harness.validate_candidate_manifest_integrity(valid_manifest)
    stale = harness.validate_candidate_manifest_integrity(stale_manifest)

    assert valid["ok"] is True
    assert stale["ok"] is False
    errors = "\n".join(stale["errors"])
    assert "candidate_count 2 does not equal candidates length 1" in errors
    assert "manifest preset_counts do not match candidates" in errors
    assert "page 1 candidate_count does not match candidates" in errors


def test_validate_candidate_manifest_integrity_requires_preset_counts() -> None:
    harness = _load_module()
    manifest = _candidate_manifest([_manifest_candidate("cand:p0001:0000:table", 1, "table")])
    del manifest["preset_counts"]

    validation = harness.validate_candidate_manifest_integrity(manifest)

    assert validation["ok"] is False
    assert "candidate manifest preset_counts is not an object" in "\n".join(validation["errors"])


def test_validate_candidate_manifest_integrity_rejects_coerced_candidate_count() -> None:
    harness = _load_module()
    string_count = _candidate_manifest([_manifest_candidate("cand:p0001:0000:table", 1, "table")])
    string_count["candidate_count"] = "1"
    float_count = _candidate_manifest([_manifest_candidate("cand:p0001:0000:table", 1, "table")])
    float_count["candidate_count"] = 1.2

    string_validation = harness.validate_candidate_manifest_integrity(string_count)
    float_validation = harness.validate_candidate_manifest_integrity(float_count)

    assert string_validation["ok"] is False
    assert float_validation["ok"] is False
    assert "candidate manifest candidate_count must be a non-negative integer: '1'" in string_validation["errors"]
    assert "candidate manifest candidate_count must be a non-negative integer: 1.2" in float_validation["errors"]


def test_validate_candidate_manifest_integrity_rejects_malformed_geometry_and_page_refs() -> None:
    harness = _load_module()
    bad_candidate = {
        **_manifest_candidate("cand:p0003:0000:table", 3, "table"),
        "page_index": 99,
        "block_index": -1,
        "bbox": [math.nan, 1.2, 0.1, -0.1],
    }
    manifest = _candidate_manifest([bad_candidate], page_count=2)

    validation = harness.validate_candidate_manifest_integrity(manifest)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "page_number 3 exceeds manifest page_count 2" in errors
    assert "page_index does not match page_number - 1" in errors
    assert "bbox contains non-finite values" in errors
    assert "missing valid block_index" in errors


def test_validate_candidate_manifest_integrity_rejects_unnormalized_or_unordered_bbox() -> None:
    harness = _load_module()
    unnormalized = {
        **_manifest_candidate("cand:p0001:0000:table", 1, "table"),
        "bbox": [-0.1, 0.2, 0.8, 0.4],
    }
    unordered = {
        **_manifest_candidate("cand:p0002:0000:table", 2, "table"),
        "bbox": [0.8, 0.2, 0.1, 0.4],
    }
    manifest = _candidate_manifest([unnormalized, unordered], page_count=2)

    validation = harness.validate_candidate_manifest_integrity(manifest)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "bbox values must be normalized to [0, 1]" in errors
    assert "bbox coordinates are not ordered [x0, y0, x1, y1]" in errors


def test_page_result_surfaces_scillm_patch_delegate_bug_report(tmp_path: Path) -> None:
    harness = _load_module()
    case = {"case_id": "page_case_0001_p0002", "page_number": 2}
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "terminal_ledger.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
                "terminal_status": "blocked_substrate",
                "reason": "patch_delegate_substrate_error",
                "commit_sha": None,
                "evidence_artifacts": [
                    "terminal_ledger.json",
                    "scillm_patch_delegate_bug_report.json",
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "scillm_patch_delegate_bug_report.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.scillm_patch_delegate_bug_report.v1",
                "terminal_reason": "patch_delegate_substrate_error",
                "observed": {"transport_run_id": "otr-bug"},
            }
        ),
        encoding="utf-8",
    )

    page_result = harness._page_result_from_case(case, {"case_dir": str(case_dir), "terminal_status": "blocked_substrate"})

    assert page_result["scillm_patch_delegate_bug_report"] == str(case_dir / "scillm_patch_delegate_bug_report.json")
    assert page_result["scillm_patch_delegate_bug_report_schema"] == "pdf_lab.second_pass.scillm_patch_delegate_bug_report.v1"
    assert page_result["scillm_patch_delegate_bug_report_terminal_reason"] == "patch_delegate_substrate_error"
    assert page_result["scillm_patch_delegate_bug_report_transport_run_id"] == "otr-bug"


def test_page_result_records_malformed_scillm_patch_delegate_bug_report(tmp_path: Path) -> None:
    harness = _load_module()
    case = {"case_id": "page_case_0001_p0002", "page_number": 2}
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "terminal_ledger.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
                "terminal_status": "blocked_substrate",
                "reason": "patch_delegate_substrate_error",
                "commit_sha": None,
                "evidence_artifacts": [
                    "terminal_ledger.json",
                    "scillm_patch_delegate_bug_report.json",
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "scillm_patch_delegate_bug_report.json").write_text("{not json", encoding="utf-8")

    page_result = harness._page_result_from_case(case, {"case_dir": str(case_dir), "terminal_status": "blocked_substrate"})

    assert page_result["scillm_patch_delegate_bug_report"] == str(case_dir / "scillm_patch_delegate_bug_report.json")
    assert page_result["scillm_patch_delegate_bug_report_schema"] is None
    assert page_result["scillm_patch_delegate_bug_report_read_errors"]
    assert "scillm_patch_delegate_bug_report.json unreadable" in page_result["scillm_patch_delegate_bug_report_read_errors"][0]


def test_page_result_records_malformed_terminal_ledger_without_crashing(tmp_path: Path) -> None:
    harness = _load_module()
    case = {"case_id": "page_case_0001_p0002", "page_number": 2}
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "terminal_ledger.json").write_text("{not json", encoding="utf-8")

    page_result = harness._page_result_from_case(case, {"case_dir": str(case_dir), "terminal_status": "blocked_substrate"})

    assert page_result["case_id"] == "page_case_0001_p0002"
    assert page_result["terminal_status"] == "blocked_substrate"
    assert page_result["terminal_ledger_read_errors"]
    assert "terminal_ledger.json unreadable" in page_result["terminal_ledger_read_errors"][0]
    assert page_result["evidence_artifacts"] == []


def test_page_result_records_raw_identity_mismatch(tmp_path: Path) -> None:
    harness = _load_module()
    case = {"case_id": "page_case_0001_p0002", "page_number": 2}
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "terminal_ledger.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
                "terminal_status": "blocked_substrate",
                "reason": "test",
                "commit_sha": None,
                "evidence_artifacts": ["terminal_ledger.json"],
            }
        ),
        encoding="utf-8",
    )

    page_result = harness._page_result_from_case(
        case,
        {
            "case_id": "page_case_9999_p9999",
            "page_number": 9999,
            "case_dir": str(case_dir),
            "terminal_status": "blocked_substrate",
        },
    )

    assert page_result["case_id"] == "page_case_0001_p0002"
    assert page_result["page_number"] == 2
    assert page_result["raw_result_case_id"] == "page_case_9999_p9999"
    assert page_result["raw_result_page_number"] == 9999
    errors = "\n".join(page_result["raw_result_identity_mismatch_errors"])
    assert "raw page result case_id" in errors
    assert "raw page result page_number" in errors


def test_build_scillm_patch_delegate_bug_report_bundle(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    report_path = case_dir / "scillm_patch_delegate_bug_report.json"
    report_path.write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.scillm_patch_delegate_bug_report.v1",
                "terminal_reason": "patch_delegate_substrate_error",
                "observed": {
                    "transport_run_id": "otr-bug",
                    "validation_errors": ["transport stream did not include message.completed"],
                },
                "scillm_project_agent_bug_report": "Fix scillm/OpenCode terminal patch evidence.",
            }
        ),
        encoding="utf-8",
    )

    bundle = harness.build_scillm_patch_delegate_bug_report_bundle(
        out_dir=tmp_path / "out",
        page_results=[
            {
                "case_id": "page_case_0001_p0002",
                "page_number": 2,
                "terminal_status": "blocked_substrate",
                "reason": "patch_delegate_substrate_error",
                "case_dir": str(case_dir),
                "scillm_patch_delegate_bug_report": str(report_path),
            }
        ],
    )

    assert bundle["schema"] == "pdf_lab.second_pass.scillm_patch_delegate_bug_report_bundle.v1"
    assert bundle["bug_report_count"] == 1
    assert bundle["reports"][0]["transport_run_id"] == "otr-bug"
    assert bundle["reports"][0]["validation_errors"] == ["transport stream did not include message.completed"]
    assert "PATCH_APPLIED" in bundle["scillm_project_agent_summary"]


def test_build_scillm_patch_delegate_bug_report_bundle_records_malformed_report(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    report_path = case_dir / "scillm_patch_delegate_bug_report.json"
    report_path.write_text(json.dumps(["not-object"]), encoding="utf-8")

    bundle = harness.build_scillm_patch_delegate_bug_report_bundle(
        out_dir=tmp_path / "out",
        page_results=[
            {
                "case_id": "page_case_0001_p0002",
                "page_number": 2,
                "terminal_status": "blocked_substrate",
                "reason": "patch_delegate_substrate_error",
                "case_dir": str(case_dir),
                "scillm_patch_delegate_bug_report": str(report_path),
            }
        ],
    )

    assert bundle["bug_report_count"] == 1
    assert bundle["malformed_bug_report_count"] == 1
    assert bundle["reports"][0]["bug_report_schema"] is None
    assert bundle["reports"][0]["read_errors"] == ["scillm_patch_delegate_bug_report.json is not a JSON object"]


def test_package_scillm_patch_delegate_bug_report_bundle(tmp_path: Path) -> None:
    harness = _load_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    for name in [
        "scillm_patch_delegate_bug_report.json",
        "patch_request.json",
        "patch_receipt.json",
        "patch_validation.json",
        "patch_attempts_ledger.json",
        "transport_event_stream.json",
        "transport_events.jsonl",
        "terminal_ledger.json",
        "terminal_ledger_validation.json",
    ]:
        (case_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
    bundle_path = out_dir / "scillm_patch_delegate_bug_reports.json"
    bundle_path.write_text(json.dumps({"schema": "bundle"}), encoding="utf-8")
    zip_path = out_dir / "scillm_patch_delegate_bug_reports.zip"

    validation = harness.package_scillm_patch_delegate_bug_report_bundle(
        out_dir=out_dir,
        bundle_path=bundle_path,
        zip_path=zip_path,
        page_results=[
            {
                "case_id": "page_case_0001_p0002",
                "case_dir": str(case_dir),
                "scillm_patch_delegate_bug_report": str(case_dir / "scillm_patch_delegate_bug_report.json"),
            }
        ],
    )

    assert validation["ok"] is True
    assert validation["included_count"] == 10
    assert validation["zip_content_ok"] is True
    assert validation["missing_expected_zip_entries"] == []
    assert validation["duplicate_zip_entries"] == []
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert set(validation["required_zip_entries"]).issubset(names)
    assert "scillm_patch_delegate_bug_reports.json" in names
    assert "page_cases/page_case_0001_p0002/scillm_patch_delegate_bug_report.json" in names
    assert "page_cases/page_case_0001_p0002/patch_request.json" in names
    assert "page_cases/page_case_0001_p0002/transport_events.jsonl" in names
    assert "page_cases/page_case_0001_p0002/terminal_ledger_validation.json" in names


def test_package_scillm_patch_delegate_bug_report_bundle_rejects_directory_artifact(
    tmp_path: Path,
) -> None:
    harness = _load_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    for name in [
        "scillm_patch_delegate_bug_report.json",
        "patch_request.json",
        "patch_receipt.json",
        "patch_validation.json",
        "patch_attempts_ledger.json",
        "transport_event_stream.json",
        "transport_events.jsonl",
        "terminal_ledger.json",
        "terminal_ledger_validation.json",
    ]:
        (case_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
    directory_artifact = case_dir / "patch_receipt.json"
    directory_artifact.unlink()
    directory_artifact.mkdir()
    bundle_path = out_dir / "scillm_patch_delegate_bug_reports.json"
    bundle_path.write_text(json.dumps({"schema": "bundle"}), encoding="utf-8")
    zip_path = out_dir / "scillm_patch_delegate_bug_reports.zip"

    validation = harness.package_scillm_patch_delegate_bug_report_bundle(
        out_dir=out_dir,
        bundle_path=bundle_path,
        zip_path=zip_path,
        page_results=[
            {
                "case_id": "page_case_0001_p0002",
                "case_dir": str(case_dir),
                "scillm_patch_delegate_bug_report": str(case_dir / "scillm_patch_delegate_bug_report.json"),
            }
        ],
    )

    missing_arcname = "page_cases/page_case_0001_p0002/patch_receipt.json"
    assert validation["ok"] is False
    assert validation["zip_content_ok"] is False
    assert str(directory_artifact) in validation["missing_artifacts"]
    assert missing_arcname in validation["missing_expected_zip_entries"]
    assert missing_arcname not in validation["included_artifacts"]


def test_build_patch_commit_ledger_requires_artifacts_and_unique_commits(tmp_path: Path) -> None:
    harness = _load_module()
    case_a = tmp_path / "case-a"
    case_a.mkdir()
    (case_a / "terminal_ledger.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
                "case_id": "a",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "commit_sha": "abc123",
                "commit_acceptance_ok": True,
                "commit_revertability_ok": True,
                "commit_exact_file_match": True,
                "evidence_artifacts": [
                    "terminal_ledger_validation.json",
                    "patch_scope_validation.json",
                    "test_validation.json",
                    "review_after_request_validation.json",
                    "review_after_validation.json",
                    "review_after_response.json",
                    "commit_acceptance_gate.json",
                    "commit_gate.json",
                    "revertability_check.json",
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_a / "terminal_ledger_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_terminal_ledger_validation.v1",
                "ok": True,
                "case_id": "a",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
            }
        ),
        encoding="utf-8",
    )
    (case_a / "commit_acceptance_gate.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.commit_acceptance_gate.v1",
                "ok": True,
                "commit_sha": "abc123",
            }
        ),
        encoding="utf-8",
    )
    (case_a / "commit_gate.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.commit_gate.v1",
                "ok": True,
                "commit_sha": "abc123",
                "exact_file_match": True,
                "committed_files": ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
                "revertability_check": {
                    "schema": "pdf_lab.second_pass.revertability_check.v1",
                    "ok": True,
                    "commit_sha": "abc123",
                },
            }
        ),
        encoding="utf-8",
    )
    (case_a / "patch_scope_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.patch_scope_validation.v1",
                "ok": True,
                "errors": [],
                "changed_files": ["python/pdf_oxide/extract_for_pdflab.py", "tests/test_fix.py"],
                "test_files": ["tests/test_fix.py"],
            }
        ),
        encoding="utf-8",
    )
    (case_a / "test_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.test_validation.v1",
                "ok": True,
                "errors": [],
                "results": [{"command": "pytest tests/test_fix.py -q", "exit_code": 0}],
                "required_test_files": ["tests/test_fix.py"],
                "covered_test_files": ["tests/test_fix.py"],
                "missing_test_file_coverage": [],
            }
        ),
        encoding="utf-8",
    )
    (case_a / "review_after_request_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_request_validation.v1",
                "ok": True,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    (case_a / "review_after_validation.json").write_text(
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
    (case_a / "review_after_response.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.review_response.v1",
                "page_status": "clean",
                "page_rationale": "after-patch extraction is clean",
                "candidate_findings": [
                    {
                        "candidate_id": "cand:p0001:0000:table",
                        "status": "clean",
                        "evidence": "candidate renders correctly after patch",
                        "rationale": "validated after patch",
                        "suggested_fix_surface": "none",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_a / "revertability_check.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.revertability_check.v1",
                "ok": True,
                "commit_sha": "abc123",
            }
        ),
        encoding="utf-8",
    )
    (case_a / "review_bundle.zip").write_bytes(b"zip")
    (case_a / "review_bundle_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_review_bundle_validation.v1",
                "ok": True,
                "zip_content_ok": True,
                "case_id": "a",
                "page_number": 1,
                "missing_expected_zip_entries": [],
                "duplicate_zip_entries": [],
            }
        ),
        encoding="utf-8",
    )
    case_b = tmp_path / "case-b"
    case_b.mkdir()
    (case_b / "terminal_ledger.json").write_bytes(b"{}")

    ledger = harness.build_patch_commit_ledger(
        out_dir=tmp_path / "out",
        page_results=[
            {
                "case_id": "a",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "reason": "verified",
                "case_dir": str(case_a),
                "commit_sha": "abc123",
                "evidence_artifacts": [
                    "terminal_ledger_validation.json",
                    "patch_scope_validation.json",
                    "test_validation.json",
                    "review_after_request_validation.json",
                    "review_after_validation.json",
                    "review_after_response.json",
                    "commit_acceptance_gate.json",
                    "commit_gate.json",
                    "revertability_check.json",
                ],
            },
            {
                "case_id": "b",
                "page_number": 2,
                "terminal_status": "patched_confirmed",
                "reason": "verified",
                "case_dir": str(case_b),
                "commit_sha": "abc123",
                "evidence_artifacts": ["commit_gate.json"],
            },
        ],
    )

    assert ledger["schema"] == "pdf_lab.second_pass.patch_commit_ledger.v1"
    assert ledger["ok"] is False
    assert ledger["commit_count"] == 2
    assert ledger["duplicate_commit_shas"] == ["abc123"]
    assert ledger["entries"][0]["ok"] is True
    assert ledger["entries"][0]["terminal_ledger_commit_sha"] == "abc123"
    assert ledger["entries"][0]["terminal_ledger_commit_acceptance_ok"] is True
    assert ledger["entries"][0]["terminal_ledger_commit_revertability_ok"] is True
    assert ledger["entries"][0]["terminal_ledger_validation_ok"] is True
    assert ledger["entries"][0]["review_bundle_validation_ok"] is True
    assert ledger["entries"][0]["review_bundle_zip_content_ok"] is True
    assert ledger["entries"][0]["commit_acceptance_ok"] is True
    assert ledger["entries"][0]["commit_exact_file_match"] is True
    assert ledger["entries"][0]["commit_gate_revertability_ok"] is True
    assert ledger["entries"][0]["commit_gate_revertability_commit_sha"] == "abc123"
    assert ledger["entries"][0]["revertability_ok"] is True
    assert ledger["entries"][0]["revertability_commit_sha"] == "abc123"
    assert ledger["entries"][1]["ok"] is False
    assert "missing revertability_check.json artifact" in "\n".join(ledger["errors"])


def test_build_patch_commit_ledger_rejects_false_acceptance_content(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "terminal_ledger.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
                "case_id": "page_case_0001_p0001",
                "terminal_status": "reviewed_clean",
                "commit_sha": "other-sha",
                "commit_acceptance_ok": False,
                "commit_revertability_ok": False,
                "commit_exact_file_match": False,
                "evidence_artifacts": ["commit_gate.json"],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "review_bundle.zip").write_bytes(b"zip")
    (case_dir / "review_bundle_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_review_bundle_validation.v1",
                "ok": False,
                "zip_content_ok": False,
                "case_id": "page_case_0001_p0001",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "terminal_ledger_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_terminal_ledger_validation.v1",
                "ok": False,
                "case_id": "page_case_0001_p0001",
                "terminal_status": "patched_confirmed",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "commit_acceptance_gate.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.commit_acceptance_gate.v1",
                "ok": False,
                "commit_sha": "abc123",
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
                "exact_file_match": False,
                "revertability_check": {
                    "schema": "pdf_lab.second_pass.revertability_check.v1",
                    "ok": False,
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
                "ok": False,
                "commit_sha": "abc123",
            }
        ),
        encoding="utf-8",
    )

    ledger = harness.build_patch_commit_ledger(
        out_dir=tmp_path / "out",
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "reason": "verified",
                "case_dir": str(case_dir),
                "commit_sha": "abc123",
                "evidence_artifacts": [
                    "terminal_ledger_validation.json",
                    "commit_acceptance_gate.json",
                    "commit_gate.json",
                    "revertability_check.json",
                ],
            }
        ],
    )

    assert ledger["ok"] is False
    assert ledger["entries"][0]["ok"] is False
    assert ledger["entries"][0]["terminal_ledger_commit_sha"] == "other-sha"
    assert ledger["entries"][0]["terminal_ledger_commit_acceptance_ok"] is False
    assert ledger["entries"][0]["terminal_ledger_commit_revertability_ok"] is False
    assert ledger["entries"][0]["terminal_ledger_validation_ok"] is False
    assert ledger["entries"][0]["review_bundle_validation_ok"] is False
    assert ledger["entries"][0]["review_bundle_zip_content_ok"] is False
    assert ledger["entries"][0]["commit_acceptance_ok"] is False
    assert ledger["entries"][0]["commit_exact_file_match"] is False
    assert ledger["entries"][0]["commit_gate_revertability_ok"] is False
    assert ledger["entries"][0]["commit_gate_revertability_commit_sha"] == "other-sha"
    assert ledger["entries"][0]["revertability_ok"] is False
    assert ledger["entries"][0]["revertability_commit_sha"] == "abc123"
    errors = "\n".join(ledger["errors"])
    assert "terminal_ledger terminal_status is not patched_confirmed" in errors
    assert "terminal_ledger commit_sha does not match page result" in errors
    assert "terminal_ledger commit_acceptance_ok is not true" in errors
    assert "terminal_ledger commit_revertability_ok is not true" in errors
    assert "terminal_ledger commit_exact_file_match is not true" in errors
    assert "terminal_ledger evidence missing terminal_ledger_validation.json" in errors
    assert "terminal_ledger_validation.ok is not true" in errors
    assert "review_bundle_validation.ok is not true" in errors
    assert "review_bundle_validation.zip_content_ok is not true" in errors
    assert "commit_acceptance_gate.ok is not true" in errors
    assert "commit_gate.exact_file_match is not true" in errors
    assert "commit_gate.revertability_check.ok is not true" in errors
    assert "commit_gate.revertability_check commit_sha does not match page result" in errors
    assert "revertability_check.ok is not true" in errors


def test_build_patch_commit_ledger_rejects_terminal_page_identity_mismatch(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="abc123",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )
    terminal = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    terminal["page_number"] = 999
    (case_dir / "terminal_ledger.json").write_text(json.dumps(terminal), encoding="utf-8")
    terminal_validation = json.loads((case_dir / "terminal_ledger_validation.json").read_text(encoding="utf-8"))
    terminal_validation["page_number"] = 999
    (case_dir / "terminal_ledger_validation.json").write_text(json.dumps(terminal_validation), encoding="utf-8")

    ledger = harness.build_patch_commit_ledger(
        out_dir=tmp_path / "out",
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "reason": "verified",
                "case_dir": str(case_dir),
                "commit_sha": "abc123",
                "evidence_artifacts": [
                    "terminal_ledger_validation.json",
                    *PATCHED_CONFIRMED_ARTIFACTS,
                ],
            }
        ],
    )

    assert ledger["ok"] is False
    assert ledger["entries"][0]["ok"] is False
    assert ledger["entries"][0]["terminal_ledger_page_number"] == 999
    assert ledger["entries"][0]["terminal_ledger_validation_page_number"] == 999
    errors = "\n".join(ledger["errors"])
    assert "terminal_ledger page_number does not match page result" in errors
    assert "terminal_ledger_validation page_number does not match page result" in errors


def test_build_patch_commit_ledger_rejects_review_bundle_page_identity_mismatch(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="abc123",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )
    bundle_validation = json.loads((case_dir / "review_bundle_validation.json").read_text(encoding="utf-8"))
    bundle_validation["page_number"] = 999
    (case_dir / "review_bundle_validation.json").write_text(json.dumps(bundle_validation), encoding="utf-8")

    ledger = harness.build_patch_commit_ledger(
        out_dir=tmp_path / "out",
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "reason": "verified",
                "case_dir": str(case_dir),
                "commit_sha": "abc123",
                "evidence_artifacts": [
                    "terminal_ledger_validation.json",
                    *PATCHED_CONFIRMED_ARTIFACTS,
                ],
            }
        ],
    )

    assert ledger["ok"] is False
    assert ledger["entries"][0]["ok"] is False
    assert ledger["entries"][0]["review_bundle_validation_page_number"] == 999
    assert "review_bundle_validation page_number does not match page result" in "\n".join(ledger["errors"])


def test_build_patch_commit_ledger_rejects_review_bundle_missing_case_identity(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="abc123",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )
    bundle_validation = json.loads((case_dir / "review_bundle_validation.json").read_text(encoding="utf-8"))
    bundle_validation.pop("case_id")
    (case_dir / "review_bundle_validation.json").write_text(json.dumps(bundle_validation), encoding="utf-8")

    ledger = harness.build_patch_commit_ledger(
        out_dir=tmp_path / "out",
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "reason": "verified",
                "case_dir": str(case_dir),
                "commit_sha": "abc123",
                "evidence_artifacts": [
                    "terminal_ledger_validation.json",
                    *PATCHED_CONFIRMED_ARTIFACTS,
                ],
            }
        ],
    )

    assert ledger["ok"] is False
    assert ledger["entries"][0]["ok"] is False
    assert "review_bundle_validation case_id does not match page result" in "\n".join(ledger["errors"])


def test_build_patch_commit_ledger_rejects_commit_files_not_matching_patch_scope(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="abc123",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )
    commit_gate = json.loads((case_dir / "commit_gate.json").read_text(encoding="utf-8"))
    commit_gate["committed_files"] = ["tests/test_fix.py"]
    (case_dir / "commit_gate.json").write_text(json.dumps(commit_gate), encoding="utf-8")

    ledger = harness.build_patch_commit_ledger(
        out_dir=tmp_path / "out",
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "reason": "verified",
                "case_dir": str(case_dir),
                "commit_sha": "abc123",
                "evidence_artifacts": [
                    "terminal_ledger_validation.json",
                    *PATCHED_CONFIRMED_ARTIFACTS,
                ],
            }
        ],
    )

    assert ledger["ok"] is False
    assert ledger["entries"][0]["ok"] is False
    assert ledger["entries"][0]["patch_scope_changed_files"] == [
        "python/pdf_oxide/extract_for_pdflab.py",
        "tests/test_fix.py",
    ]
    assert ledger["entries"][0]["commit_gate_committed_files"] == ["tests/test_fix.py"]
    assert "commit_gate committed_files do not match patch_scope_validation changed_files" in "\n".join(ledger["errors"])


def test_build_patch_commit_ledger_rejects_after_review_not_clean(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="abc123",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )
    after_response = json.loads((case_dir / "review_after_response.json").read_text(encoding="utf-8"))
    after_response["page_status"] = "defect"
    after_response["candidate_findings"][0]["status"] = "defect"
    after_response["candidate_findings"][0]["suggested_fix_surface"] = "python/pdf_oxide"
    (case_dir / "review_after_response.json").write_text(json.dumps(after_response), encoding="utf-8")

    ledger = harness.build_patch_commit_ledger(
        out_dir=tmp_path / "out",
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "reason": "verified",
                "case_dir": str(case_dir),
                "commit_sha": "abc123",
                "evidence_artifacts": [
                    "terminal_ledger_validation.json",
                    *PATCHED_CONFIRMED_ARTIFACTS,
                ],
            }
        ],
    )

    assert ledger["ok"] is False
    assert ledger["entries"][0]["ok"] is False
    assert ledger["entries"][0]["review_after_page_status"] == "defect"
    errors = "\n".join(ledger["errors"])
    assert "review_after_response page_status is not clean" in errors
    assert "review_after_response candidate_findings are not all clean" in errors


def test_build_patch_commit_ledger_rejects_after_review_request_validation_failure(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="abc123",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )
    after_request_validation = json.loads((case_dir / "review_after_request_validation.json").read_text(encoding="utf-8"))
    after_request_validation["ok"] = False
    after_request_validation["errors"] = ["review_request missing annotated_image"]
    (case_dir / "review_after_request_validation.json").write_text(json.dumps(after_request_validation), encoding="utf-8")

    ledger = harness.build_patch_commit_ledger(
        out_dir=tmp_path / "out",
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "reason": "verified",
                "case_dir": str(case_dir),
                "commit_sha": "abc123",
                "evidence_artifacts": [
                    "terminal_ledger_validation.json",
                    *PATCHED_CONFIRMED_ARTIFACTS,
                ],
            }
        ],
    )

    assert ledger["ok"] is False
    assert ledger["entries"][0]["ok"] is False
    assert ledger["entries"][0]["review_after_request_validation_ok"] is False
    errors = "\n".join(ledger["errors"])
    assert "review_after_request_validation.ok is not true" in errors


def test_build_patch_commit_ledger_rejects_missing_targeted_test_coverage(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="abc123",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )
    test_validation = json.loads((case_dir / "test_validation.json").read_text(encoding="utf-8"))
    test_validation["ok"] = True
    test_validation["required_test_files"] = ["tests/test_fix.py"]
    test_validation["covered_test_files"] = []
    test_validation["missing_test_file_coverage"] = ["tests/test_fix.py"]
    (case_dir / "test_validation.json").write_text(json.dumps(test_validation), encoding="utf-8")

    ledger = harness.build_patch_commit_ledger(
        out_dir=tmp_path / "out",
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "reason": "verified",
                "case_dir": str(case_dir),
                "commit_sha": "abc123",
                "evidence_artifacts": [
                    "terminal_ledger_validation.json",
                    *PATCHED_CONFIRMED_ARTIFACTS,
                ],
            }
        ],
    )

    assert ledger["ok"] is False
    assert ledger["entries"][0]["ok"] is False
    errors = "\n".join(ledger["errors"])
    assert "test_validation covered_test_files do not match patch_scope_validation test_files" in errors
    assert "test_validation missing_test_file_coverage is not empty" in errors


def test_build_patch_commit_ledger_rejects_revertability_schema_and_sha_mismatch(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "terminal_ledger.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
                "case_id": "page_case_0001_p0001",
                "terminal_status": "patched_confirmed",
                "commit_sha": "abc123",
                "commit_acceptance_ok": True,
                "commit_revertability_ok": True,
                "commit_exact_file_match": True,
                "evidence_artifacts": [
                    "terminal_ledger_validation.json",
                    "commit_acceptance_gate.json",
                    "commit_gate.json",
                    "revertability_check.json",
                ],
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "terminal_ledger_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_terminal_ledger_validation.v1",
                "ok": True,
                "case_id": "page_case_0001_p0001",
                "terminal_status": "patched_confirmed",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "review_bundle.zip").write_bytes(b"zip")
    (case_dir / "review_bundle_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_review_bundle_validation.v1",
                "ok": True,
                "zip_content_ok": True,
                "case_id": "page_case_0001_p0001",
            }
        ),
        encoding="utf-8",
    )
    (case_dir / "commit_acceptance_gate.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.commit_acceptance_gate.v1",
                "ok": True,
                "commit_sha": "abc123",
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
                "schema": "wrong.schema",
                "ok": True,
                "commit_sha": "other-sha",
            }
        ),
        encoding="utf-8",
    )

    ledger = harness.build_patch_commit_ledger(
        out_dir=tmp_path / "out",
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "page_number": 1,
                "terminal_status": "patched_confirmed",
                "reason": "verified",
                "case_dir": str(case_dir),
                "commit_sha": "abc123",
                "evidence_artifacts": [
                    "terminal_ledger_validation.json",
                    "commit_acceptance_gate.json",
                    "commit_gate.json",
                    "revertability_check.json",
                ],
            }
        ],
    )

    assert ledger["ok"] is False
    assert ledger["entries"][0]["ok"] is False
    errors = "\n".join(ledger["errors"])
    assert "commit_gate.revertability_check schema mismatch" in errors
    assert "commit_gate.revertability_check commit_sha does not match page result" in errors
    assert "revertability_check schema mismatch" in errors
    assert "revertability_check commit_sha does not match page result" in errors


def test_package_patch_commit_ledger(tmp_path: Path) -> None:
    harness = _load_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    for name in [
        "terminal_ledger.json",
        "terminal_ledger_validation.json",
        "commit_acceptance_gate.json",
        "commit_gate.json",
        "revertability_check.json",
        "review_bundle.zip",
        "review_bundle_validation.json",
    ]:
        (case_dir / name).write_bytes(b"{}")
    ledger_path = out_dir / "patch_commit_ledger.json"
    ledger_path.write_text(json.dumps({"schema": "ledger"}), encoding="utf-8")
    zip_path = out_dir / "patch_commit_ledger.zip"

    validation = harness.package_patch_commit_ledger(
        ledger_path=ledger_path,
        zip_path=zip_path,
        page_results=[
            {
                "case_id": "page_case_0001_p0002",
                "terminal_status": "patched_confirmed",
                "case_dir": str(case_dir),
            }
        ],
    )

    assert validation["ok"] is True
    assert validation["included_count"] == 8
    assert validation["zip_content_ok"] is True
    assert validation["missing_expected_zip_entries"] == []
    assert validation["duplicate_zip_entries"] == []
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert set(validation["required_zip_entries"]).issubset(names)
    assert "patch_commit_ledger.json" in names
    assert "page_cases/page_case_0001_p0002/commit_acceptance_gate.json" in names
    assert "page_cases/page_case_0001_p0002/terminal_ledger_validation.json" in names
    assert "page_cases/page_case_0001_p0002/commit_gate.json" in names
    assert "page_cases/page_case_0001_p0002/revertability_check.json" in names
    assert "page_cases/page_case_0001_p0002/review_bundle.zip" in names
    assert "page_cases/page_case_0001_p0002/review_bundle_validation.json" in names


def test_validate_patch_commit_ledger_zip_rejects_stale_entry(tmp_path: Path) -> None:
    harness = _load_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    for name in [
        "terminal_ledger.json",
        "terminal_ledger_validation.json",
        "commit_acceptance_gate.json",
        "commit_gate.json",
        "revertability_check.json",
        "review_bundle.zip",
        "review_bundle_validation.json",
    ]:
        (case_dir / name).write_bytes(b"{}")
    ledger_path = out_dir / "patch_commit_ledger.json"
    ledger_path.write_text(json.dumps({"schema": "ledger"}), encoding="utf-8")
    zip_path = out_dir / "patch_commit_ledger.zip"

    validation = harness.package_patch_commit_ledger(
        ledger_path=ledger_path,
        zip_path=zip_path,
        page_results=[
            {
                "case_id": "page_case_0001_p0002",
                "terminal_status": "patched_confirmed",
                "case_dir": str(case_dir),
            }
        ],
    )
    (case_dir / "commit_gate.json").write_bytes(b'{"stale":false}')
    stale_validation = harness.validate_patch_commit_ledger_zip(
        zip_path=zip_path,
        included_artifacts=validation["included_artifacts"],
        missing_artifacts=validation["missing_artifacts"],
        required_zip_entries=validation["required_zip_entries"],
        expected_sources={
            "patch_commit_ledger.json": ledger_path,
            "page_cases/page_case_0001_p0002/terminal_ledger.json": case_dir / "terminal_ledger.json",
            "page_cases/page_case_0001_p0002/terminal_ledger_validation.json": case_dir / "terminal_ledger_validation.json",
            "page_cases/page_case_0001_p0002/commit_acceptance_gate.json": case_dir / "commit_acceptance_gate.json",
            "page_cases/page_case_0001_p0002/commit_gate.json": case_dir / "commit_gate.json",
            "page_cases/page_case_0001_p0002/revertability_check.json": case_dir / "revertability_check.json",
            "page_cases/page_case_0001_p0002/review_bundle.zip": case_dir / "review_bundle.zip",
            "page_cases/page_case_0001_p0002/review_bundle_validation.json": case_dir / "review_bundle_validation.json",
        },
    )

    assert stale_validation["ok"] is False
    assert stale_validation["zip_content_ok"] is False
    assert stale_validation["mismatched_zip_entries"] == ["page_cases/page_case_0001_p0002/commit_gate.json"]
    assert (
        "mismatched_zip_entries: page_cases/page_case_0001_p0002/commit_gate.json"
        in harness.package_validation_errors(stale_validation)
    )


def test_package_patch_commit_ledger_rejects_directory_artifact(tmp_path: Path) -> None:
    harness = _load_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    for name in [
        "terminal_ledger.json",
        "terminal_ledger_validation.json",
        "commit_acceptance_gate.json",
        "commit_gate.json",
        "revertability_check.json",
        "review_bundle.zip",
        "review_bundle_validation.json",
    ]:
        (case_dir / name).write_bytes(b"{}")
    directory_artifact = case_dir / "commit_gate.json"
    directory_artifact.unlink()
    directory_artifact.mkdir()
    ledger_path = out_dir / "patch_commit_ledger.json"
    ledger_path.write_text(json.dumps({"schema": "ledger"}), encoding="utf-8")
    zip_path = out_dir / "patch_commit_ledger.zip"

    validation = harness.package_patch_commit_ledger(
        ledger_path=ledger_path,
        zip_path=zip_path,
        page_results=[
            {
                "case_id": "page_case_0001_p0002",
                "terminal_status": "patched_confirmed",
                "case_dir": str(case_dir),
            }
        ],
    )

    missing_arcname = "page_cases/page_case_0001_p0002/commit_gate.json"
    assert validation["ok"] is False
    assert validation["zip_content_ok"] is False
    assert str(directory_artifact) in validation["missing_artifacts"]
    assert missing_arcname in validation["missing_expected_zip_entries"]
    assert missing_arcname not in validation["included_artifacts"]


def test_package_harness_review_bundle_includes_run_and_page_artifacts(tmp_path: Path) -> None:
    harness = _load_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    top_level_artifacts = []
    for name in [
        "candidate_manifest.json",
        "sampled_page_cases.json",
        "sampling_gate.json",
        "candidate_sample_linkage_validation.json",
        "deterministic_execution_plan.json",
        "scillm_code_root_visibility.json",
        "scillm_patch_delegate_bug_reports.json",
        "scillm_patch_delegate_bug_reports_zip.json",
        "scillm_patch_delegate_bug_reports.zip",
        "patch_commit_ledger.json",
        "patch_commit_ledger_zip.json",
        "patch_commit_ledger.zip",
        "harness_readiness_audit.json",
        "harness_report.json",
    ]:
        path = out_dir / name
        if path.suffix == ".zip":
            path.write_bytes(b"zip")
        else:
            path.write_text(json.dumps({"artifact": name}), encoding="utf-8")
        top_level_artifacts.append(path)
    case_dir = tmp_path / "case"
    _write_page_dag_case(case_dir, case_id="page_case_0001_p0001", terminal_status="reviewed_clean")
    zip_path = out_dir / "harness_review_bundle.zip"

    validation = harness.package_harness_review_bundle(
        out_dir=out_dir,
        zip_path=zip_path,
        top_level_artifacts=top_level_artifacts,
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "case_dir": str(case_dir),
                "terminal_status": "reviewed_clean",
            }
        ],
    )

    assert validation["schema"] == "pdf_lab.second_pass.harness_review_bundle_zip.v1"
    assert validation["ok"] is True
    assert validation["missing_required_artifacts"] == []
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "candidate_manifest.json" in names
    assert "harness_report.json" in names
    assert "page_cases/page_case_0001_p0001/terminal_ledger.json" in names
    assert "page_cases/page_case_0001_p0001/review_bundle.zip" in names
    assert "page_cases/page_case_0001_p0001/scillm_orchestrator_page_dag_spec.json" in names


def test_package_harness_review_bundle_requires_resolved_page_dag_artifacts(tmp_path: Path) -> None:
    harness = _load_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    top_artifact = out_dir / "candidate_manifest.json"
    top_artifact.write_text(json.dumps({"artifact": "candidate_manifest.json"}), encoding="utf-8")
    case_dir = tmp_path / "case"
    _write_page_dag_case(case_dir, case_id="page_case_0001_p0001", terminal_status="reviewed_clean")
    missing_artifact = case_dir / "review_request.json"
    missing_artifact.unlink()

    page_results = [
        {
            "case_id": "page_case_0001_p0001",
            "case_dir": str(case_dir),
            "terminal_status": "reviewed_clean",
        }
    ]
    validation = harness.package_harness_review_bundle(
        out_dir=out_dir,
        zip_path=out_dir / "harness_review_bundle.zip",
        top_level_artifacts=[top_artifact],
        page_results=page_results,
    )
    input_validation = harness.validate_harness_review_bundle_inputs(
        zip_path=out_dir / "harness_review_bundle.zip",
        top_level_artifacts=[top_artifact],
        page_results=page_results,
    )

    missing_arcname = "page_cases/page_case_0001_p0001/review_request.json"
    assert validation["ok"] is False
    assert validation["zip_content_ok"] is False
    assert str(missing_artifact) in validation["missing_required_artifacts"]
    assert missing_arcname in validation["missing_expected_zip_entries"]
    assert input_validation["ok"] is False
    assert str(missing_artifact) in input_validation["missing_required_artifacts"]


def test_package_harness_review_bundle_requires_patched_confirmed_artifacts(tmp_path: Path) -> None:
    harness = _load_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    top_artifact = out_dir / "candidate_manifest.json"
    top_artifact.write_text(json.dumps({"artifact": "candidate_manifest.json"}), encoding="utf-8")
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="abc123",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )
    missing_artifact = case_dir / "review_after_response.json"
    missing_artifact.unlink()

    page_results = [
        {
            "case_id": "page_case_0001_p0001",
            "case_dir": str(case_dir),
            "terminal_status": "patched_confirmed",
        }
    ]
    validation = harness.package_harness_review_bundle(
        out_dir=out_dir,
        zip_path=out_dir / "harness_review_bundle.zip",
        top_level_artifacts=[top_artifact],
        page_results=page_results,
    )
    input_validation = harness.validate_harness_review_bundle_inputs(
        zip_path=out_dir / "harness_review_bundle.zip",
        top_level_artifacts=[top_artifact],
        page_results=page_results,
    )

    missing_arcname = "page_cases/page_case_0001_p0001/review_after_response.json"
    assert validation["ok"] is False
    assert validation["zip_content_ok"] is False
    assert str(missing_artifact) in validation["missing_required_artifacts"]
    assert missing_arcname in validation["missing_expected_zip_entries"]
    assert input_validation["ok"] is False
    assert str(missing_artifact) in input_validation["missing_required_artifacts"]


def test_build_harness_readiness_audit_requires_page_and_gate_artifacts(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "terminal_ledger.json").write_text(json.dumps({"terminal_status": "reviewed_clean"}), encoding="utf-8")

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": False, "errors": ["sample too small"]},
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "terminal_status": "reviewed_clean",
                "terminal_ledger": str(case_dir / "terminal_ledger.json"),
                "review_bundle": str(case_dir / "review_bundle.zip"),
            }
        ],
        aggregate={"ok": True, "errors": [], "status_counts": {"reviewed_clean": 1}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
    )

    assert audit["schema"] == "pdf_lab.second_pass.harness_readiness_audit.v1"
    assert audit["ok"] is False
    assert "sampling gate passed" in audit["failed_requirements"]
    assert "each resolved page case has self-contained DAG evidence" in audit["failed_requirements"]
    assert "terminal_ledger_validation" in json.dumps(audit)
    assert "sample too small" in json.dumps(audit)


def test_readiness_audit_rejects_terminal_ledger_that_does_not_match_page_result(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="reviewed_clean",
    )
    (case_dir / "review_bundle.zip").write_bytes(b"zip")
    terminal = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    terminal["case_id"] = "page_case_9999_p9999"
    (case_dir / "terminal_ledger.json").write_text(json.dumps(terminal), encoding="utf-8")
    page_result = harness._page_result_from_case(
        {"case_id": "page_case_0001_p0001", "page_number": 1},
        {"case_dir": str(case_dir), "terminal_status": "reviewed_clean"},
    )

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[page_result],
        aggregate={"ok": True, "errors": [], "status_counts": {"reviewed_clean": 1}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
    )

    assert audit["ok"] is False
    assert "each resolved page case has self-contained DAG evidence" in audit["failed_requirements"]
    assert "terminal_ledger case_id does not match page result" in json.dumps(audit)


def test_readiness_audit_rejects_terminal_ledger_page_number_mismatch(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    _write_sampled_page_cases(
        sampled_path,
        [{"case_id": "page_case_0001_p0001", "page_number": 1, "candidate_ids": ["c1"]}],
    )
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="reviewed_clean",
    )
    terminal = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    terminal["page_number"] = 999
    (case_dir / "terminal_ledger.json").write_text(json.dumps(terminal), encoding="utf-8")
    page_result = harness._page_result_from_case(
        {"case_id": "page_case_0001_p0001", "page_number": 1},
        {"case_dir": str(case_dir), "terminal_status": "reviewed_clean"},
    )

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[page_result],
        aggregate={"ok": True, "errors": [], "status_counts": {"reviewed_clean": 1}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
        harness_review_bundle_validation={"ok": True, "missing_artifacts": []},
        candidate_sample_linkage_validation={"ok": True, "errors": []},
        candidate_manifest_integrity_validation={"ok": True, "errors": []},
        deterministic_execution_plan_validation={"ok": True, "errors": []},
    )

    assert audit["ok"] is False
    assert "each resolved page case has self-contained DAG evidence" in audit["failed_requirements"]
    assert "terminal_ledger page_number does not match page result" in json.dumps(audit)


def test_readiness_audit_rejects_terminal_ledger_validation_identity_mismatch(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    _write_sampled_page_cases(
        sampled_path,
        [{"case_id": "page_case_0001_p0001", "page_number": 1, "candidate_ids": ["c1"]}],
    )
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="reviewed_clean",
    )
    terminal_validation = json.loads((case_dir / "terminal_ledger_validation.json").read_text(encoding="utf-8"))
    terminal_validation["page_number"] = 999
    (case_dir / "terminal_ledger_validation.json").write_text(json.dumps(terminal_validation), encoding="utf-8")
    page_result = harness._page_result_from_case(
        {"case_id": "page_case_0001_p0001", "page_number": 1},
        {"case_dir": str(case_dir), "terminal_status": "reviewed_clean"},
    )

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[page_result],
        aggregate={"ok": True, "errors": [], "status_counts": {"reviewed_clean": 1}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
        harness_review_bundle_validation={"ok": True, "missing_artifacts": []},
        candidate_sample_linkage_validation={"ok": True, "errors": []},
        candidate_manifest_integrity_validation={"ok": True, "errors": []},
        deterministic_execution_plan_validation={"ok": True, "errors": []},
    )

    assert audit["ok"] is False
    assert "each resolved page case has self-contained DAG evidence" in audit["failed_requirements"]
    assert "terminal_ledger_validation page_number does not match page result" in json.dumps(audit)


def test_readiness_audit_rejects_raw_page_result_identity_mismatch(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="reviewed_clean",
    )
    page_result = harness._page_result_from_case(
        {"case_id": "page_case_0001_p0001", "page_number": 1},
        {
            "case_id": "page_case_9999_p9999",
            "page_number": 9999,
            "case_dir": str(case_dir),
            "terminal_status": "reviewed_clean",
        },
    )

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[page_result],
        aggregate={"ok": True, "errors": [], "status_counts": {"reviewed_clean": 1}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
    )

    assert audit["ok"] is False
    assert "each resolved page case has self-contained DAG evidence" in audit["failed_requirements"]
    audit_json = json.dumps(audit)
    assert "raw page result identity mismatch" in audit_json
    assert "raw page result case_id" in audit_json
    assert "raw page result page_number" in audit_json


def test_readiness_audit_rejects_page_result_read_errors(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="reviewed_clean",
    )
    (case_dir / "review_bundle.zip").write_bytes(b"zip")
    (case_dir / "state.json").write_text("{not json", encoding="utf-8")
    page_result = harness._page_result_from_case(
        {"case_id": "page_case_0001_p0001", "page_number": 1},
        {"case_dir": str(case_dir), "terminal_status": "reviewed_clean"},
    )

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[page_result],
        aggregate={"ok": True, "errors": [], "status_counts": {"reviewed_clean": 1}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
    )

    assert page_result["state_read_errors"]
    assert audit["ok"] is False
    assert "each resolved page case has self-contained DAG evidence" in audit["failed_requirements"]
    assert "state_read_errors" in json.dumps(audit)


def test_readiness_audit_rejects_failed_page_review_bundle_validation(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="reviewed_clean",
    )
    validation = json.loads((case_dir / "review_bundle_validation.json").read_text(encoding="utf-8"))
    validation["ok"] = False
    validation["zip_content_ok"] = False
    validation["errors"] = ["required bundle artifacts are missing from zip: ['review.html']"]
    validation["missing_expected_zip_entries"] = ["review.html"]
    (case_dir / "review_bundle_validation.json").write_text(json.dumps(validation), encoding="utf-8")
    page_result = harness._page_result_from_case(
        {"case_id": "page_case_0001_p0001", "page_number": 1},
        {"case_dir": str(case_dir), "terminal_status": "reviewed_clean"},
    )

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[page_result],
        aggregate={"ok": True, "errors": [], "status_counts": {"reviewed_clean": 1}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
    )

    assert page_result["review_bundle_validation_ok"] is False
    assert page_result["review_bundle_zip_content_ok"] is False
    assert audit["ok"] is False
    assert "each resolved page case has self-contained DAG evidence" in audit["failed_requirements"]
    assert "review_bundle_validation failed" in json.dumps(audit)


def test_readiness_audit_rejects_external_page_artifact_paths(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="reviewed_clean",
    )
    external_terminal_ledger = tmp_path / "external" / "terminal_ledger.json"
    external_terminal_ledger.parent.mkdir()
    external_terminal_ledger.write_text((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"), encoding="utf-8")
    page_result = harness._page_result_from_case(
        {"case_id": "page_case_0001_p0001", "page_number": 1},
        {"case_dir": str(case_dir), "terminal_status": "reviewed_clean"},
    )
    page_result["terminal_ledger"] = str(external_terminal_ledger)

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[page_result],
        aggregate={"ok": True, "errors": [], "status_counts": {"reviewed_clean": 1}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
    )

    assert audit["ok"] is False
    assert "each resolved page case has self-contained DAG evidence" in audit["failed_requirements"]
    assert "terminal_ledger path is not case-local" in json.dumps(audit)


def test_readiness_audit_rejects_review_bundle_validation_identity_mismatch(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    _write_sampled_page_cases(
        sampled_path,
        [{"case_id": "page_case_0001_p0001", "page_number": 1, "candidate_ids": ["c1"]}],
    )
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="reviewed_clean",
    )
    validation = json.loads((case_dir / "review_bundle_validation.json").read_text(encoding="utf-8"))
    validation["page_number"] = 999
    (case_dir / "review_bundle_validation.json").write_text(json.dumps(validation), encoding="utf-8")
    page_result = harness._page_result_from_case(
        {"case_id": "page_case_0001_p0001", "page_number": 1},
        {"case_dir": str(case_dir), "terminal_status": "reviewed_clean"},
    )

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[page_result],
        aggregate={"ok": True, "errors": [], "status_counts": {"reviewed_clean": 1}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
        harness_review_bundle_validation={"ok": True, "missing_artifacts": []},
        candidate_sample_linkage_validation={"ok": True, "errors": []},
        candidate_manifest_integrity_validation={"ok": True, "errors": []},
        deterministic_execution_plan_validation={"ok": True, "errors": []},
    )

    assert audit["ok"] is False
    assert "each resolved page case has self-contained DAG evidence" in audit["failed_requirements"]
    assert "review_bundle_validation page_number does not match page result" in json.dumps(audit)


def test_readiness_audit_rejects_review_bundle_validation_missing_case_identity(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    _write_sampled_page_cases(
        sampled_path,
        [{"case_id": "page_case_0001_p0001", "page_number": 1, "candidate_ids": ["c1"]}],
    )
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="reviewed_clean",
    )
    validation = json.loads((case_dir / "review_bundle_validation.json").read_text(encoding="utf-8"))
    validation.pop("case_id")
    (case_dir / "review_bundle_validation.json").write_text(json.dumps(validation), encoding="utf-8")
    page_result = harness._page_result_from_case(
        {"case_id": "page_case_0001_p0001", "page_number": 1},
        {"case_dir": str(case_dir), "terminal_status": "reviewed_clean"},
    )

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[page_result],
        aggregate={"ok": True, "errors": [], "status_counts": {"reviewed_clean": 1}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
        harness_review_bundle_validation={"ok": True, "missing_artifacts": []},
        candidate_sample_linkage_validation={"ok": True, "errors": []},
        candidate_manifest_integrity_validation={"ok": True, "errors": []},
        deterministic_execution_plan_validation={"ok": True, "errors": []},
    )

    assert audit["ok"] is False
    assert "each resolved page case has self-contained DAG evidence" in audit["failed_requirements"]
    assert "review_bundle_validation case_id does not match page result" in json.dumps(audit)


def test_write_blocked_case_result_includes_terminal_ledger_validation(tmp_path: Path) -> None:
    harness = _load_module()

    result = harness._write_blocked_case_result(
        out_dir=tmp_path / "out",
        case={"case_id": "page_case_0001_p0001", "page_number": 1},
        reason="scillm_proof_floor_failed",
        visibility={"ok": True, "errors": []},
    )

    case_dir = Path(result["case_dir"])
    validation = json.loads((case_dir / "terminal_ledger_validation.json").read_text(encoding="utf-8"))
    bundle_validation = json.loads((case_dir / "review_bundle_validation.json").read_text(encoding="utf-8"))
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))

    assert result["terminal_ledger_validation_ok"] is True
    assert result["review_bundle_validation_ok"] is True
    assert result["review_bundle_zip_content_ok"] is True
    assert validation["ok"] is True
    assert bundle_validation["schema"] == "pdf_lab.second_pass.page_review_bundle_validation.v1"
    assert bundle_validation["ok"] is True
    assert bundle_validation["zip_content_ok"] is True
    assert bundle_validation["terminal_ledger_matches_argument"] is True
    assert bundle_validation["terminal_ledger_validation_matches_recomputed"] is True
    assert bundle_validation["terminal_ledger_validation_ok"] is True
    assert bundle_validation["missing_expected_zip_entries"] == []
    assert "terminal_ledger_validation.json" in result["evidence_artifacts"]
    assert "terminal_ledger_validation.json" in ledger["evidence_artifacts"]
    with zipfile.ZipFile(result["review_bundle"]) as archive:
        names = set(archive.namelist())
    assert "terminal_ledger_validation.json" in names
    assert set(bundle_validation["required_zip_entries"]).issubset(names)


def test_harness_page_review_bundle_rejects_stale_terminal_validation(tmp_path: Path) -> None:
    harness = _load_module()
    result = harness._write_blocked_case_result(
        out_dir=tmp_path / "out",
        case={"case_id": "page_case_0001_p0001", "page_number": 1},
        reason="scillm_proof_floor_failed",
        visibility={"ok": True, "errors": []},
    )
    case_dir = Path(result["case_dir"])
    terminal = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    stale_validation = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger_validation.v1",
        "ok": True,
        "errors": [],
        "case_id": "page_case_9999_p9999",
        "page_number": 9999,
        "terminal_status": "reviewed_clean",
        "declared_evidence_count": 1,
        "missing_artifacts": [],
    }
    (case_dir / "terminal_ledger_validation.json").write_text(json.dumps(stale_validation), encoding="utf-8")
    with zipfile.ZipFile(result["review_bundle"], "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for artifact in sorted({
            "terminal_ledger.json",
            "terminal_ledger_validation.json",
            "review.html",
            *terminal["evidence_artifacts"],
        }):
            path = case_dir / artifact
            if path.is_file():
                bundle.write(path, artifact)

    validation = harness.validate_harness_page_review_bundle(case_dir, Path(result["review_bundle"]), terminal)

    assert validation["ok"] is False
    assert validation["zip_content_ok"] is True
    assert validation["terminal_ledger_matches_argument"] is True
    assert validation["terminal_ledger_validation_matches_recomputed"] is False
    assert validation["terminal_ledger_validation_ok"] is False
    assert "terminal_ledger_validation.json does not match recomputed terminal validation" in validation["errors"]


def test_harness_page_review_bundle_rejects_duplicate_and_unsafe_terminal_evidence(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "review.html",
            "../outside.json",
            "/tmp/outside.json",
            "terminal_ledger_validation.json",
        ],
    }
    (case_dir / "terminal_ledger.json").write_text(json.dumps(terminal), encoding="utf-8")
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    (case_dir / "terminal_ledger_validation.json").write_text(
        json.dumps(harness.validate_harness_page_terminal_ledger(case_dir, terminal)),
        encoding="utf-8",
    )
    zip_path = case_dir / "review_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(case_dir / "terminal_ledger.json", "terminal_ledger.json")
        bundle.write(case_dir / "terminal_ledger_validation.json", "terminal_ledger_validation.json")
        bundle.write(case_dir / "review.html", "review.html")

    validation = harness.validate_harness_page_review_bundle(case_dir, zip_path, terminal)

    assert validation["ok"] is False
    assert validation["zip_content_ok"] is False
    assert validation["duplicate_evidence_artifacts"] == ["review.html"]
    assert validation["unsafe_evidence_artifacts"] == ["../outside.json", "/tmp/outside.json"]
    assert "../outside.json" not in validation["required_zip_entries"]
    assert "/tmp/outside.json" not in validation["required_zip_entries"]
    assert validation["missing_artifacts"] == []
    assert validation["missing_expected_zip_entries"] == []
    errors = "\n".join(validation["errors"])
    assert "terminal evidence_artifacts contains duplicate artifact names: ['review.html']" in errors
    assert "terminal evidence_artifacts contains unsafe artifact paths: ['../outside.json', '/tmp/outside.json']" in errors
    assert "terminal_ledger_validation ok is not true" in errors


def test_harness_page_review_bundle_rejects_stale_zip_entry(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
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
    (case_dir / "terminal_ledger.json").write_text(json.dumps(terminal), encoding="utf-8")
    (case_dir / "review.html").write_text(json.dumps({"artifact": "review.html", "version": "current"}), encoding="utf-8")
    (case_dir / "review_request.json").write_text(json.dumps({"artifact": "review_request.json", "version": "current"}), encoding="utf-8")
    (case_dir / "terminal_ledger_validation.json").write_text(
        json.dumps(harness.validate_harness_page_terminal_ledger(case_dir, terminal)),
        encoding="utf-8",
    )
    zip_path = case_dir / "review_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(case_dir / "terminal_ledger.json", "terminal_ledger.json")
        bundle.write(case_dir / "terminal_ledger_validation.json", "terminal_ledger_validation.json")
        bundle.write(case_dir / "review.html", "review.html")
        bundle.writestr("review_request.json", json.dumps({"artifact": "review_request.json", "version": "stale"}))
    validation = harness.validate_harness_page_review_bundle(case_dir, zip_path, terminal)

    assert validation["ok"] is False
    assert validation["zip_content_ok"] is False
    assert validation["missing_expected_zip_entries"] == []
    assert validation["mismatched_zip_entries"] == ["review_request.json"]
    assert "required bundle artifacts differ between case dir and zip" in "\n".join(validation["errors"])


def test_validate_deterministic_execution_plan_rejects_agent_or_reordered_pages() -> None:
    harness = _load_module()
    valid = harness.validate_deterministic_execution_plan(
        {
            "schema": "pdf_lab.second_pass.deterministic_execution_plan.v1",
            "owner": "pdf_lab_harness_code",
            "agent_decision_allowed": False,
            "execution_mode": "sequential",
            "page_case_order": [
                {
                    "case_id": "page_case_0001_p0001",
                    "page_number": 1,
                    "selection_probability_estimate": 0.5,
                    "selection_probability_basis": {"method": "weighted"},
                },
                {
                    "case_id": "page_case_0002_p0002",
                    "page_number": 2,
                    "selection_probability_estimate": 1.0,
                    "selection_probability_basis": {"method": "forced_human_annotation"},
                    "forced_by_human_annotation": True,
                },
            ],
            "commit_policy": {"one_git_commit_per_verified_bug_fix": True},
        },
        page_results=[{"case_id": "page_case_0001_p0001", "page_number": 1}],
    )
    invalid = harness.validate_deterministic_execution_plan(
        {
            "schema": "pdf_lab.second_pass.deterministic_execution_plan.v1",
            "owner": "agent",
            "agent_decision_allowed": True,
            "execution_mode": "async",
            "page_case_order": [
                {"case_id": "page_case_0001_p0001", "page_number": 1},
                {"case_id": "page_case_0002_p0002", "page_number": 2},
            ],
            "commit_policy": {"one_git_commit_per_verified_bug_fix": False},
        },
        page_results=[{"case_id": "page_case_0002_p0002", "page_number": 2}],
    )

    assert valid["ok"] is True
    assert invalid["ok"] is False
    assert "agent_decision_allowed false" in "\n".join(invalid["errors"])
    assert "page result order does not match" in "\n".join(invalid["errors"])


def test_validate_deterministic_execution_plan_rejects_malformed_case_ids() -> None:
    harness = _load_module()
    validation = harness.validate_deterministic_execution_plan(
        {
            "schema": "pdf_lab.second_pass.deterministic_execution_plan.v1",
            "owner": "pdf_lab_harness_code",
            "agent_decision_allowed": False,
            "execution_mode": "sequential",
            "page_case_order": [
                {"case_id": "../escape", "page_number": 1},
                {"case_id": "page_case_0002_p0001", "page_number": 2},
            ],
            "commit_policy": {"one_git_commit_per_verified_bug_fix": True},
        },
        page_results=[],
    )

    errors = "\n".join(validation["errors"])
    assert validation["ok"] is False
    assert validation["malformed_planned_case_ids"] == ["../escape"]
    assert validation["planned_case_id_page_suffix_mismatches"] == ["page_case_0002_p0001"]
    assert "deterministic execution plan has malformed case_ids: ['../escape']" in errors
    assert "case_id page suffixes do not match page_number: ['page_case_0002_p0001']" in errors


def test_validate_deterministic_execution_plan_rejects_duplicate_planned_pages() -> None:
    harness = _load_module()
    validation = harness.validate_deterministic_execution_plan(
        {
            "schema": "pdf_lab.second_pass.deterministic_execution_plan.v1",
            "owner": "pdf_lab_harness_code",
            "agent_decision_allowed": False,
            "execution_mode": "sequential",
            "page_case_order": [
                {"case_id": "dup", "page_number": 3},
                {"case_id": "dup", "page_number": 3},
                {"case_id": None, "page_number": None},
            ],
            "commit_policy": {"one_git_commit_per_verified_bug_fix": True},
        },
        page_results=[],
    )

    errors = "\n".join(validation["errors"])
    assert validation["ok"] is False
    assert validation["duplicate_planned_case_ids"] == ["dup"]
    assert validation["duplicate_planned_page_numbers"] == [3]
    assert "page_case_order contains missing case_id" in errors
    assert "page_case_order contains missing integer page_number" in errors


def test_validate_deterministic_execution_plan_rejects_malformed_probability_metadata() -> None:
    harness = _load_module()
    validation = harness.validate_deterministic_execution_plan(
        {
            "schema": "pdf_lab.second_pass.deterministic_execution_plan.v1",
            "owner": "pdf_lab_harness_code",
            "agent_decision_allowed": False,
            "execution_mode": "sequential",
            "page_case_order": [
                {
                    "case_id": "bad-probability",
                    "page_number": 1,
                    "selection_probability_estimate": 1.5,
                    "selection_probability_basis": "not-an-object",
                },
                {
                    "case_id": "bad-forced",
                    "page_number": 2,
                    "forced_by_human_annotation": True,
                    "selection_probability_estimate": 0.75,
                    "selection_probability_basis": {"method": "weighted"},
                },
            ],
            "commit_policy": {"one_git_commit_per_verified_bug_fix": True},
        },
        page_results=[],
    )

    errors = "\n".join(validation["errors"])
    assert validation["ok"] is False
    assert validation["malformed_probability_case_ids"] == ["bad-probability"]
    assert validation["malformed_forced_probability_case_ids"] == ["bad-forced"]
    assert "malformed selection probability metadata" in errors
    assert "forced pages missing forced_human_annotation probability basis" in errors


def test_validate_page_results_match_sampled_cases_rejects_green_missing_pages(tmp_path: Path) -> None:
    harness = _load_module()
    sampled_path = tmp_path / "sampled_page_cases.json"
    _write_sampled_page_cases(
        sampled_path,
        [
            {"case_id": "page_case_0001_p0001", "page_number": 1, "candidate_ids": ["c1"]},
            {"case_id": "page_case_0002_p0002", "page_number": 2, "candidate_ids": ["c2"]},
        ],
    )

    validation = harness.validate_page_results_match_sampled_cases(
        sampled_cases_path=sampled_path,
        page_results=[{"case_id": "page_case_0001_p0001", "page_number": 99}],
        aggregate={"ok": True},
    )

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "green page aggregate cannot omit sampled page cases" in errors
    assert "page result page_numbers do not match sampled_page_cases" in errors


def test_validate_page_results_match_sampled_cases_allows_failed_closed_prefix(tmp_path: Path) -> None:
    harness = _load_module()
    sampled_path = tmp_path / "sampled_page_cases.json"
    _write_sampled_page_cases(
        sampled_path,
        [
            {"case_id": "page_case_0001_p0001", "page_number": 1, "candidate_ids": ["c1"]},
            {"case_id": "page_case_0002_p0002", "page_number": 2, "candidate_ids": ["c2"]},
        ],
    )

    validation = harness.validate_page_results_match_sampled_cases(
        sampled_cases_path=sampled_path,
        page_results=[{"case_id": "page_case_0001_p0001", "page_number": 1}],
        aggregate={"ok": False},
    )

    assert validation["ok"] is True
    assert validation["missing_sampled_case_ids"] == ["page_case_0002_p0002"]


def test_readiness_audit_rejects_green_page_results_missing_sampled_case(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    _write_sampled_page_cases(
        sampled_path,
        [
            {"case_id": "page_case_0001_p0001", "page_number": 1, "candidate_ids": ["c1"]},
            {"case_id": "page_case_0002_p0002", "page_number": 2, "candidate_ids": ["c2"]},
        ],
    )
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="reviewed_clean",
    )
    page_result = harness._page_result_from_case(
        {"case_id": "page_case_0001_p0001", "page_number": 1},
        {"case_dir": str(case_dir), "terminal_status": "reviewed_clean"},
    )

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[page_result],
        aggregate={"ok": True, "errors": [], "status_counts": {"reviewed_clean": 1}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
        harness_review_bundle_validation={"ok": True, "missing_artifacts": []},
        candidate_sample_linkage_validation={"ok": True, "errors": []},
        candidate_manifest_integrity_validation={"ok": True, "errors": []},
        deterministic_execution_plan_validation={"ok": True, "errors": []},
    )

    assert audit["ok"] is False
    assert "page results match sampled page cases" in audit["failed_requirements"]
    assert "green page aggregate cannot omit sampled page cases" in json.dumps(audit)


def test_readiness_audit_requires_after_patch_artifacts_for_patched_confirmed(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="abc123",
        extra_evidence=["commit_acceptance_gate.json", "commit_gate.json", "revertability_check.json"],
    )

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "terminal_status": "patched_confirmed",
                "terminal_ledger": str(case_dir / "terminal_ledger.json"),
                "review_bundle": str(case_dir / "review_bundle.zip"),
                "case_dir": str(case_dir),
                "evidence_artifacts": ["terminal_ledger.json", *PAGE_DAG_ARTIFACTS, "commit_acceptance_gate.json", "commit_gate.json", "revertability_check.json"],
            }
        ],
        aggregate={"ok": True, "errors": [], "status_counts": {"patched_confirmed": 1}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 1, "commit_shas": ["abc123"], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
    )

    assert audit["ok"] is False
    assert "each resolved page case has self-contained DAG evidence" in audit["failed_requirements"]
    assert "patched-confirmed artifacts" in json.dumps(audit)


def test_terminal_ledger_validation_requires_full_patched_confirmed_evidence(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="abc123",
        extra_evidence=["commit_acceptance_gate.json", "commit_gate.json", "revertability_check.json"],
    )
    terminal = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))

    validation = harness.validate_harness_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "patched_confirmed terminal ledger missing test_validation.json" in errors
    assert "patched_confirmed terminal ledger missing review_after_request_validation.json" in errors
    assert "patched_confirmed terminal ledger missing review_after_response.json" in errors


def test_terminal_ledger_validation_rejects_unproven_commit_flags(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="abc123",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )
    terminal = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    terminal["commit_gate_ok"] = False
    terminal["commit_exact_file_match"] = False
    terminal["commit_revertability_ok"] = False

    validation = harness.validate_harness_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    errors = "\n".join(validation["errors"])
    assert "patched_confirmed terminal ledger requires commit_gate_ok true" in errors
    assert "patched_confirmed terminal ledger requires commit_exact_file_match true" in errors
    assert "patched_confirmed terminal ledger requires commit_revertability_ok true" in errors


def test_harness_terminal_ledger_rejects_duplicate_and_unsafe_evidence(tmp_path: Path) -> None:
    harness = _load_module()
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "review.html").write_text("review", encoding="utf-8")
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": "page_case_0001_p0001",
        "page_number": 1,
        "terminal_status": "still_open",
        "reason": "dry_run_review_not_executed",
        "evidence_artifacts": [
            "review.html",
            "review.html",
            "../outside.json",
            "/tmp/outside.json",
            "terminal_ledger_validation.json",
        ],
        "commit_sha": None,
    }

    validation = harness.validate_harness_page_terminal_ledger(case_dir, terminal)

    assert validation["ok"] is False
    assert validation["duplicate_evidence_artifacts"] == ["review.html"]
    assert validation["unsafe_evidence_artifacts"] == ["../outside.json", "/tmp/outside.json"]
    errors = "\n".join(validation["errors"])
    assert "evidence_artifacts contains duplicate artifact names: ['review.html']" in errors
    assert "evidence_artifacts contains unsafe artifact paths: ['../outside.json', '/tmp/outside.json']" in errors
    assert "declared evidence artifacts are missing" not in errors


def test_readiness_audit_requires_patch_commit_count_to_match_patched_pages(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    case_a = tmp_path / "case-a"
    case_b = tmp_path / "case-b"
    _write_page_dag_case(
        case_a,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="sha-a",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )
    _write_page_dag_case(
        case_b,
        case_id="page_case_0002_p0002",
        terminal_status="patched_confirmed",
        commit_sha="sha-b",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )

    page_results = [
        {
            "case_id": "page_case_0001_p0001",
            "terminal_status": "patched_confirmed",
            "terminal_ledger": str(case_a / "terminal_ledger.json"),
            "terminal_ledger_validation": str(case_a / "terminal_ledger_validation.json"),
            "review_bundle": str(case_a / "review_bundle.zip"),
            "case_dir": str(case_a),
            "evidence_artifacts": ["terminal_ledger.json", *PAGE_DAG_ARTIFACTS, *PATCHED_CONFIRMED_ARTIFACTS],
        },
        {
            "case_id": "page_case_0002_p0002",
            "terminal_status": "patched_confirmed",
            "terminal_ledger": str(case_b / "terminal_ledger.json"),
            "terminal_ledger_validation": str(case_b / "terminal_ledger_validation.json"),
            "review_bundle": str(case_b / "review_bundle.zip"),
            "case_dir": str(case_b),
            "evidence_artifacts": ["terminal_ledger.json", *PAGE_DAG_ARTIFACTS, *PATCHED_CONFIRMED_ARTIFACTS],
        },
    ]

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=page_results,
        aggregate={
            "ok": True,
            "errors": [],
            "status_counts": {"patched_confirmed": 2},
            "patched_confirmed_count": 2,
            "unresolved_count": 0,
        },
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={
            "ok": True,
            "commit_count": 1,
            "commit_shas": ["sha-a"],
            "duplicate_commit_shas": [],
            "errors": [],
        },
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
    )

    assert audit["ok"] is False
    assert "patch commit ledger matches patched-confirmed page count" in audit["failed_requirements"]
    assert "does not match patched_confirmed count 2" in json.dumps(audit)


def test_readiness_audit_requires_patch_commit_entries_to_match_patched_pages(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    case_a = tmp_path / "case-a"
    case_b = tmp_path / "case-b"
    _write_page_dag_case(
        case_a,
        case_id="page_case_0001_p0001",
        terminal_status="patched_confirmed",
        commit_sha="sha-a",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )
    _write_page_dag_case(
        case_b,
        case_id="page_case_0002_p0002",
        terminal_status="patched_confirmed",
        commit_sha="sha-b",
        extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
    )

    page_results = [
        {
            "case_id": "page_case_0001_p0001",
            "page_number": 1,
            "terminal_status": "patched_confirmed",
            "commit_sha": "sha-a",
            "terminal_ledger": str(case_a / "terminal_ledger.json"),
            "terminal_ledger_validation": str(case_a / "terminal_ledger_validation.json"),
            "review_bundle": str(case_a / "review_bundle.zip"),
            "review_bundle_validation": str(case_a / "review_bundle_validation.json"),
            "review_bundle_validation_ok": True,
            "review_bundle_zip_content_ok": True,
            "case_dir": str(case_a),
            "evidence_artifacts": ["terminal_ledger.json", *PAGE_DAG_ARTIFACTS, *PATCHED_CONFIRMED_ARTIFACTS],
        },
        {
            "case_id": "page_case_0002_p0002",
            "page_number": 2,
            "terminal_status": "patched_confirmed",
            "commit_sha": "sha-b",
            "terminal_ledger": str(case_b / "terminal_ledger.json"),
            "terminal_ledger_validation": str(case_b / "terminal_ledger_validation.json"),
            "review_bundle": str(case_b / "review_bundle.zip"),
            "review_bundle_validation": str(case_b / "review_bundle_validation.json"),
            "review_bundle_validation_ok": True,
            "review_bundle_zip_content_ok": True,
            "case_dir": str(case_b),
            "evidence_artifacts": ["terminal_ledger.json", *PAGE_DAG_ARTIFACTS, *PATCHED_CONFIRMED_ARTIFACTS],
        },
    ]

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=page_results,
        aggregate={
            "ok": True,
            "errors": [],
            "status_counts": {"patched_confirmed": 2},
            "patched_confirmed_count": 2,
            "unresolved_count": 0,
        },
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={
            "ok": True,
            "commit_count": 2,
            "commit_shas": ["sha-a", "sha-b"],
            "duplicate_commit_shas": [],
            "entries": [
                {"case_id": "page_case_0001_p0001", "commit_sha": "sha-a"},
                {"case_id": "page_case_9999_p9999", "commit_sha": "sha-b"},
            ],
            "errors": [],
        },
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
    )

    assert audit["ok"] is False
    assert "patch commit ledger matches patched-confirmed page count" in audit["failed_requirements"]
    assert "entries do not match patched_confirmed page result commits" in json.dumps(audit)


def test_readiness_audit_surfaces_zip_content_validation_errors(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[],
        aggregate={"ok": True, "errors": [], "status_counts": {}, "unresolved_count": 0},
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor=None,
        opencode_completion_canary=None,
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={
            "ok": False,
            "missing_artifacts": [],
            "missing_expected_zip_entries": ["page_cases/a/scillm_patch_delegate_bug_report.json"],
            "duplicate_zip_entries": ["scillm_patch_delegate_bug_reports.json"],
            "zip_content_ok": False,
        },
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={
            "ok": False,
            "missing_artifacts": [],
            "missing_expected_zip_entries": ["page_cases/a/commit_gate.json"],
            "duplicate_zip_entries": ["patch_commit_ledger.json"],
            "zip_content_ok": False,
        },
        harness_review_bundle_validation={
            "ok": False,
            "missing_required_artifacts": [],
            "missing_expected_zip_entries": ["harness_report.json"],
            "duplicate_zip_entries": ["candidate_manifest.json"],
            "zip_content_ok": False,
        },
    )

    assert audit["ok"] is False
    dumped = json.dumps(audit)
    assert "scillm patch delegate bug report bundle is packageable" in audit["failed_requirements"]
    assert "patch commit ledger bundle is packageable" in audit["failed_requirements"]
    assert "harness review bundle is packageable" in audit["failed_requirements"]
    assert "missing_expected_zip_entries: page_cases/a/scillm_patch_delegate_bug_report.json" in dumped
    assert "duplicate_zip_entries: patch_commit_ledger.json" in dumped
    assert "missing_expected_zip_entries: harness_report.json" in dumped


def test_readiness_audit_requires_live_orchestrator_page_registration(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    case_dir = tmp_path / "case"
    _write_page_dag_case(
        case_dir,
        case_id="page_case_0001_p0001",
        terminal_status="blocked_substrate",
    )

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "terminal_status": "blocked_substrate",
                "terminal_ledger": str(case_dir / "terminal_ledger.json"),
                "review_bundle": str(case_dir / "review_bundle.zip"),
                "case_dir": str(case_dir),
                "evidence_artifacts": ["terminal_ledger.json", *PAGE_DAG_ARTIFACTS],
                "page_orchestrator_run_ok": False,
                "page_orchestrator_registered": False,
                "page_orchestrator_transport_run_id": None,
            }
        ],
        aggregate={
            "ok": False,
            "errors": ["unresolved page cases remain: ['page_case_0001_p0001']"],
            "status_counts": {"blocked_substrate": 1},
            "unresolved_count": 1,
        },
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor={"ok": True, "errors": []},
        opencode_completion_canary=None,
        scillm_transport_readonly_canary={"ok": True, "errors": []},
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
    )

    assert audit["ok"] is False
    assert "live scillm orchestrator page registration passed" in audit["failed_requirements"]
    assert "page_orchestrator_transport_run_id" in json.dumps(audit)


def test_readiness_audit_requires_live_scillm_canary_bug_report_for_live_lane(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[],
        aggregate={"ok": True, "errors": [], "status_counts": {}, "unresolved_count": 0},
        patch_mode="live",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor={"schema": "pdf_lab.second_pass.scillm_proof_floor.v1", "ok": True, "errors": []},
        opencode_completion_canary={"schema": "pdf_lab.second_pass.opencode_completion_canary.v1", "ok": True, "errors": []},
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
    )

    assert audit["ok"] is False
    assert "live scillm canary bug report is deterministic" in audit["failed_requirements"]
    assert "live scillm canary bug report missing" in json.dumps(audit)


def test_readiness_audit_accepts_failed_live_canary_bug_report_for_failed_canary(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    failed_write_canary = {
        "schema": "pdf_lab.second_pass.scillm_transport_write_canary.v1",
        "ok": False,
        "errors": ["scillm_transport_write_canary_call_failed"],
    }
    bug_report = harness.build_live_scillm_canary_bug_report(
        out_dir=tmp_path,
        code_root=tmp_path,
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        code_root_visibility={"schema": "visibility", "ok": True, "errors": []},
        scillm_proof_floor={"schema": "proof", "ok": True, "errors": []},
        opencode_completion_canary=None,
        scillm_transport_readonly_canary={"schema": "readonly", "ok": True, "errors": []},
        scillm_transport_write_canary=failed_write_canary,
    )

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[],
        aggregate={"ok": True, "errors": [], "status_counts": {}, "unresolved_count": 0},
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        code_root_visibility={"schema": "visibility", "ok": True, "errors": []},
        scillm_proof_floor={"schema": "proof", "ok": True, "errors": []},
        opencode_completion_canary=None,
        scillm_transport_readonly_canary={"schema": "readonly", "ok": True, "errors": []},
        scillm_transport_write_canary=failed_write_canary,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
        live_scillm_canary_bug_report=bug_report,
    )

    dumped = json.dumps(audit)
    assert "live scillm transport write-capability canary passed" in audit["failed_requirements"]
    assert "live scillm canary bug report is deterministic" not in audit["failed_requirements"]
    assert "scillm_transport_write_canary_call_failed" in dumped


def test_readiness_audit_rejects_underreported_live_canary_bug_report(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    failed_write_canary = {
        "schema": "pdf_lab.second_pass.scillm_transport_write_canary.v1",
        "ok": False,
        "errors": ["scillm_transport_write_canary_call_failed"],
    }
    underreported_bug_report = {
        "schema": "pdf_lab.second_pass.live_scillm_canary_bug_report.v1",
        "lane_required": True,
        "ok": False,
        "errors": ["live scillm canary checks failed: ['scillm_transport_write_canary']"],
        "failed_checks": [],
    }

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[],
        aggregate={"ok": True, "errors": [], "status_counts": {}, "unresolved_count": 0},
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        code_root_visibility={"schema": "visibility", "ok": True, "errors": []},
        scillm_proof_floor={"schema": "proof", "ok": True, "errors": []},
        opencode_completion_canary=None,
        scillm_transport_readonly_canary={"schema": "readonly", "ok": True, "errors": []},
        scillm_transport_write_canary=failed_write_canary,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
        live_scillm_canary_bug_report=underreported_bug_report,
    )

    assert audit["ok"] is False
    assert "live scillm canary bug report is deterministic" in audit["failed_requirements"]
    assert "failed_checks mismatch" in json.dumps(audit)


def test_readiness_audit_rejects_live_canary_bug_report_missing_observed_active_check(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    failed_write_canary = {
        "schema": "pdf_lab.second_pass.scillm_transport_write_canary.v1",
        "ok": False,
        "errors": ["scillm_transport_write_canary_call_failed"],
    }
    bug_report = harness.build_live_scillm_canary_bug_report(
        out_dir=tmp_path,
        code_root=tmp_path,
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        code_root_visibility={"schema": "visibility", "ok": True, "errors": []},
        scillm_proof_floor={"schema": "proof", "ok": True, "errors": []},
        opencode_completion_canary=None,
        scillm_transport_readonly_canary={"schema": "readonly", "ok": True, "errors": []},
        scillm_transport_write_canary=failed_write_canary,
    )
    bug_report["observed_checks"] = [
        check for check in bug_report["observed_checks"] if check["check_id"] != "scillm_transport_write_canary"
    ]

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[],
        aggregate={"ok": True, "errors": [], "status_counts": {}, "unresolved_count": 0},
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        code_root_visibility={"schema": "visibility", "ok": True, "errors": []},
        scillm_proof_floor={"schema": "proof", "ok": True, "errors": []},
        opencode_completion_canary=None,
        scillm_transport_readonly_canary={"schema": "readonly", "ok": True, "errors": []},
        scillm_transport_write_canary=failed_write_canary,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
        live_scillm_canary_bug_report=bug_report,
    )

    assert audit["ok"] is False
    assert "live scillm canary bug report is deterministic" in audit["failed_requirements"]
    assert "observed_checks missing active check: scillm_transport_write_canary" in json.dumps(audit)


def test_readiness_audit_rejects_live_canary_bug_report_stale_observed_ok_value(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")
    failed_write_canary = {
        "schema": "pdf_lab.second_pass.scillm_transport_write_canary.v1",
        "ok": False,
        "errors": ["scillm_transport_write_canary_call_failed"],
    }
    bug_report = harness.build_live_scillm_canary_bug_report(
        out_dir=tmp_path,
        code_root=tmp_path,
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        code_root_visibility={"schema": "visibility", "ok": True, "errors": []},
        scillm_proof_floor={"schema": "proof", "ok": True, "errors": []},
        opencode_completion_canary=None,
        scillm_transport_readonly_canary={"schema": "readonly", "ok": True, "errors": []},
        scillm_transport_write_canary=failed_write_canary,
    )
    for check in bug_report["observed_checks"]:
        if check["check_id"] == "scillm_transport_write_canary":
            check["ok"] = True
            break

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[],
        aggregate={"ok": True, "errors": [], "status_counts": {}, "unresolved_count": 0},
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        code_root_visibility={"schema": "visibility", "ok": True, "errors": []},
        scillm_proof_floor={"schema": "proof", "ok": True, "errors": []},
        opencode_completion_canary=None,
        scillm_transport_readonly_canary={"schema": "readonly", "ok": True, "errors": []},
        scillm_transport_write_canary=failed_write_canary,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
        live_scillm_canary_bug_report=bug_report,
    )

    assert audit["ok"] is False
    assert "live scillm canary bug report is deterministic" in audit["failed_requirements"]
    assert "observed_checks mismatch for scillm_transport_write_canary" in json.dumps(audit)


def test_readiness_audit_names_live_opencode_write_capability_gate(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[],
        aggregate={"ok": True, "errors": [], "status_counts": {}, "unresolved_count": 0},
        patch_mode="live",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor={"ok": True, "errors": []},
        opencode_completion_canary={
            "ok": False,
            "errors": ["OpenCode completion canary did not create sentinel file: .pdf_lab_write_canary/opencode_write_canary.txt"],
            "validation_artifact": "/tmp/opencode_completion_canary_validation.json",
            "cleanup_artifact": "/tmp/opencode_completion_canary_cleanup.json",
        },
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
    )

    assert audit["ok"] is False
    assert "live opencode serve write-capability canary passed" in audit["failed_requirements"]
    assert "opencode_completion_canary_cleanup.json" in json.dumps(audit)
    assert ".pdf_lab_write_canary/opencode_write_canary.txt" in json.dumps(audit)


def test_run_scillm_proof_floor_requires_positive_and_negative_chat_contracts(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    calls: list[tuple[str, str, bool]] = []

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload
            self.text = json.dumps(payload)

        def json(self):
            return self._payload

    class FakeHttpx:
        @staticmethod
        def get(url, headers, timeout):  # noqa: ARG004
            path = "/" + url.split("/", 3)[3]
            calls.append(("GET", path, "X-Caller-Skill" in headers))
            if path == "/health/liveliness":
                return FakeResponse(200, {"status": "ok"})
            if path == "/v1/scillm/opencode/health":
                return FakeResponse(200, {"status": "ok", "opencode_serve": True})
            return FakeResponse(404, {"error": "not_found"})

        @staticmethod
        def post(url, headers, json, timeout):  # noqa: ARG004
            path = "/" + url.split("/", 3)[3]
            has_caller = "X-Caller-Skill" in headers
            calls.append(("POST", path, has_caller))
            if has_caller:
                return FakeResponse(
                    200,
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "PDF_LAB_SCILLM_PREFLIGHT_OK",
                                }
                            }
                        ]
                    },
                )
            return FakeResponse(400, {"error": {"code": "caller_skill_required"}})

    monkeypatch.setitem(sys.modules, "httpx", FakeHttpx)

    proof = harness.run_scillm_proof_floor(
        out_dir=tmp_path / "out",
        patch_mode="live",
        patch_backend="opencode_serve",
        scillm_base_url="http://localhost:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab",
        model="gpt-5.5",
        timeout_s=1.0,
    )

    assert proof["ok"] is True
    assert ("GET", "/health/liveliness", True) in calls
    assert ("GET", "/v1/scillm/opencode/health", True) in calls
    assert ("POST", "/v1/chat/completions", True) in calls
    assert ("POST", "/v1/chat/completions", False) in calls
    assert (tmp_path / "out/scillm_proof_floor/scillm_proof_floor.json").is_file()
    assert (tmp_path / "out/scillm_proof_floor/positive_chat_request.json").is_file()
    assert (tmp_path / "out/scillm_proof_floor/missing_caller_chat_response.json").is_file()


def test_readiness_audit_rejects_proof_floor_ok_without_validation_artifacts(tmp_path: Path) -> None:
    harness = _load_module()
    manifest_path = tmp_path / "candidate_manifest.json"
    manifest_path.write_text(json.dumps({"schema": "manifest"}), encoding="utf-8")
    sampled_path = tmp_path / "sampled_page_cases.json"
    sampled_path.write_text(json.dumps({"schema": "sample"}), encoding="utf-8")

    audit = harness.build_harness_readiness_audit(
        out_dir=tmp_path,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_path,
        sampling_gate={"ok": True, "errors": []},
        page_results=[],
        aggregate={"ok": True, "errors": [], "status_counts": {}, "unresolved_count": 0},
        patch_mode="live",
        patch_backend="opencode_serve",
        code_root_visibility={"ok": True, "errors": []},
        scillm_proof_floor={
            "schema": "pdf_lab.second_pass.scillm_proof_floor.v1",
            "ok": True,
            "errors": [],
        },
        opencode_completion_canary={"ok": True, "errors": []},
        scillm_transport_readonly_canary=None,
        scillm_bug_report_zip_validation={"ok": True, "missing_artifacts": []},
        patch_commit_ledger={"ok": True, "commit_count": 0, "commit_shas": [], "errors": []},
        patch_commit_ledger_zip_validation={"ok": True, "missing_artifacts": []},
        candidate_sample_linkage_validation={"ok": True, "errors": []},
        candidate_manifest_integrity_validation={"ok": True, "errors": []},
        deterministic_execution_plan_validation={"ok": True, "errors": []},
    )

    assert audit["ok"] is False
    assert "live scillm proof floor passed" in audit["failed_requirements"]
    assert "scillm proof floor missing artifacts" in json.dumps(audit)


def test_live_canary_artifact_validation_rejects_opencode_ok_without_artifacts(tmp_path: Path) -> None:
    harness = _load_module()

    validation = harness.validate_live_canary_artifacts(
        out_dir=tmp_path,
        canary={
            "schema": "pdf_lab.second_pass.opencode_completion_canary.v1",
            "ok": True,
            "errors": [],
        },
        artifact_builder=harness.opencode_completion_canary_artifacts,
        canary_schema="pdf_lab.second_pass.opencode_completion_canary.v1",
        validation_schema="pdf_lab.second_pass.opencode_completion_canary_validation.v1",
        validation_artifact_name="opencode_completion_canary_validation.json",
        cleanup_schema="pdf_lab.second_pass.opencode_completion_canary_cleanup.v1",
        cleanup_artifact_name="opencode_completion_canary_cleanup.json",
    )

    assert validation["ok"] is False
    assert "live canary missing artifacts" in "\n".join(validation["errors"])
    assert "opencode_completion_canary_validation.json" in json.dumps(validation)


def test_live_canary_artifact_validation_rejects_transport_ok_without_validation_artifact(tmp_path: Path) -> None:
    harness = _load_module()

    validation = harness.validate_live_canary_artifacts(
        out_dir=tmp_path,
        canary={
            "schema": "pdf_lab.second_pass.scillm_transport_readonly_canary.v1",
            "ok": True,
            "errors": [],
        },
        artifact_builder=harness.scillm_transport_readonly_canary_artifacts,
        canary_schema="pdf_lab.second_pass.scillm_transport_readonly_canary.v1",
        validation_schema="pdf_lab.second_pass.scillm_transport_readonly_canary_validation.v1",
        validation_artifact_name="scillm_transport_readonly_canary_validation.json",
    )

    assert validation["ok"] is False
    assert "live canary validation artifact missing" in "\n".join(validation["errors"])
    assert "scillm_transport_readonly_canary_validation.json" in json.dumps(validation)


def test_run_scillm_transport_readonly_canary_validates_completed_empty_diff(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    code_root = tmp_path / "code-root"
    code_root.mkdir()
    captured_request = {}

    class FakePageDag:
        DEFAULT_OPENCODE_SKILLS = ["scillm"]

        @staticmethod
        def call_scillm_orchestrator_patch(request, **kwargs):
            captured_request.update(request)
            assert kwargs["base_url"] == "http://localhost:4001"
            return {
                "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
                "transport_run_id": "tr-canary",
                "event_stream": {
                    "delivery_state": "completed",
                    "final_result": {
                        "delivery_state": "completed",
                        "assistant_text": "PDF_LAB_TRANSPORT_CANARY_OK workspace is visible",
                        "diff": [],
                    },
                },
                "message_response": {
                    "delivery_state": "completed",
                    "assistant_text": "PDF_LAB_TRANSPORT_CANARY_OK workspace is visible",
                    "diff": [],
                },
            }

    monkeypatch.setattr(harness, "git_status_short", lambda repo: [])

    canary = harness.run_scillm_transport_readonly_canary(
        out_dir=tmp_path / "out",
        page_dag=FakePageDag,
        code_root=code_root,
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        scillm_base_url="http://localhost:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab",
        agent="build",
        skills=None,
        timeout_s=1.0,
        model="gpt-5.5",
    )

    assert canary["ok"] is True
    assert canary["transport_run_id"] == "tr-canary"
    assert captured_request["schema"] == "pdf_lab.second_pass.scillm_transport_readonly_canary_request.v1"
    assert captured_request["create_child_body"]["mode"] == "read_only"
    assert captured_request["message_body"]["model"] == "gpt-5.5"
    assert (tmp_path / "out/scillm_transport_readonly_canary/scillm_transport_readonly_canary.json").is_file()
    assert (tmp_path / "out/scillm_transport_readonly_canary/scillm_transport_readonly_canary_event_stream.json").is_file()


def test_run_scillm_transport_write_canary_validates_write_and_cleanup(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    code_root = tmp_path / "code-root"
    code_root.mkdir()
    captured_request = {}

    class FakePageDag:
        DEFAULT_OPENCODE_SKILLS = ["scillm"]

        @staticmethod
        def call_scillm_orchestrator_patch(request, **kwargs):  # noqa: ARG004
            captured_request.update(request)
            relpath = request["canary_relpath"]
            sentinel = Path(request["cwd"]) / relpath
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text("PDF_LAB_SCILLM_TRANSPORT_WRITE_CANARY_OK\n", encoding="utf-8")
            return {
                "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
                "transport_run_id": "tr-write-canary",
                "event_stream": {
                    "delivery_state": "completed",
                    "final_result": {
                        "delivery_state": "completed",
                        "assistant_text": f"PDF_LAB_TRANSPORT_WRITE_CANARY_OK wrote {relpath}",
                        "diff": [{"path": relpath, "status": "added"}],
                    },
                },
                "message_response": {
                    "delivery_state": "completed",
                    "assistant_text": f"PDF_LAB_TRANSPORT_WRITE_CANARY_OK wrote {relpath}",
                    "diff": [{"path": relpath, "status": "added"}],
                },
            }

    monkeypatch.setattr(harness, "git_status_short", lambda repo: [])  # noqa: ARG005

    canary = harness.run_scillm_transport_write_canary(
        out_dir=tmp_path / "out",
        page_dag=FakePageDag,
        code_root=code_root,
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        scillm_base_url="http://localhost:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab",
        agent="build",
        skills=None,
        timeout_s=1.0,
        model="gpt-5.5",
    )

    canary_dir = tmp_path / "out/scillm_transport_write_canary"
    validation = json.loads((canary_dir / "scillm_transport_write_canary_validation.json").read_text(encoding="utf-8"))
    cleanup = json.loads((canary_dir / "scillm_transport_write_canary_cleanup.json").read_text(encoding="utf-8"))

    assert canary["ok"] is True
    assert canary["status"] == "completed"
    assert captured_request["schema"] == "pdf_lab.second_pass.scillm_transport_write_canary_request.v1"
    assert captured_request["create_child_body"]["mode"] == "patch"
    assert validation["write_sentinel_present"] is True
    assert validation["write_sentinel_content_ok"] is True
    assert validation["diff_present"] is True
    assert cleanup["ok"] is True
    assert cleanup["removed_file"] is True
    assert not (code_root / ".pdf_lab_write_canary/scillm_transport_write_canary.txt").exists()


def test_validate_scillm_transport_write_canary_requires_patch_diff(tmp_path: Path) -> None:
    harness = _load_module()
    code_root = tmp_path / "code-root"
    sentinel = code_root / ".pdf_lab_write_canary/scillm_transport_write_canary.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("PDF_LAB_SCILLM_TRANSPORT_WRITE_CANARY_OK\n", encoding="utf-8")

    validation = harness.validate_scillm_transport_write_canary_receipt(
        {
            "message_response": {
                "delivery_state": "completed",
                "assistant_text": "PDF_LAB_TRANSPORT_WRITE_CANARY_OK wrote `.pdf_lab_write_canary/scillm_transport_write_canary.txt`",
                "diff": [],
            },
            "event_stream": {
                "delivery_state": "completed",
                "saw_message_completed": True,
                "session_errors": [],
            },
        },
        code_root=code_root,
    )

    assert validation["ok"] is False
    assert validation["write_sentinel_present"] is True
    assert validation["write_sentinel_content_ok"] is True
    assert validation["diff_present"] is False
    assert "transport write canary produced no patch diff evidence" in validation["errors"]


def test_validate_scillm_transport_write_canary_surfaces_transport_event_errors(tmp_path: Path) -> None:
    harness = _load_module()
    code_root = tmp_path / "code-root"
    sentinel = code_root / ".pdf_lab_write_canary/scillm_transport_write_canary.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("PDF_LAB_SCILLM_TRANSPORT_WRITE_CANARY_OK\n", encoding="utf-8")

    validation = harness.validate_scillm_transport_write_canary_receipt(
        {
            "message_response": {
                "delivery_state": "failed",
                "status": "failed",
            },
            "event_stream": {
                "delivery_state": "failed",
                "session_errors": [
                    {
                        "error_type": "RemoteProtocolError",
                        "error": "peer closed connection without sending complete message body",
                    }
                ],
                "event_replay_error": {
                    "error_type": "ConnectError",
                    "error": "[Errno 111] Connection refused",
                },
            },
        },
        code_root=code_root,
    )

    assert validation["ok"] is False
    assert validation["write_sentinel_present"] is True
    assert validation["write_sentinel_content_ok"] is True
    errors = "\n".join(validation["errors"])
    assert "transport write canary session_error RemoteProtocolError" in errors
    assert "transport write canary event replay error ConnectError" in errors


def test_validate_scillm_transport_write_canary_allows_replay_warning_after_completion(tmp_path: Path) -> None:
    harness = _load_module()
    code_root = tmp_path / "code-root"
    sentinel = code_root / ".pdf_lab_write_canary/scillm_transport_write_canary.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("PDF_LAB_SCILLM_TRANSPORT_WRITE_CANARY_OK\n", encoding="utf-8")

    validation = harness.validate_scillm_transport_write_canary_receipt(
        {
            "message_response": {
                "delivery_state": "completed",
                "assistant_text": "PDF_LAB_TRANSPORT_WRITE_CANARY_OK wrote `.pdf_lab_write_canary/scillm_transport_write_canary.txt`",
                "diff": [{"file": ".pdf_lab_write_canary/scillm_transport_write_canary.txt", "status": "added"}],
            },
            "event_stream": {
                "delivery_state": "completed",
                "saw_message_completed": True,
                "session_errors": [],
                "event_replay_error": {
                    "error_type": "ReadTimeout",
                    "error": "timed out",
                },
            },
        },
        code_root=code_root,
    )

    assert validation["ok"] is True
    assert validation["errors"] == []
    assert validation["warnings"] == ["transport write canary event replay error ReadTimeout: timed out"]
    assert validation["assistant_text_present"] is True
    assert validation["write_sentinel_content_ok"] is True


def test_run_opencode_completion_canary_validates_write_and_cleanup(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    code_root = tmp_path / "code-root"
    code_root.mkdir()
    captured_request = {}

    class FakePageDag:
        DEFAULT_OPENCODE_SKILLS = ["scillm"]

        @staticmethod
        def call_opencode_patch(request, **kwargs):  # noqa: ARG004
            captured_request.update(request)
            relpath = request["scillm_metadata"]["canary_relpath"]
            sentinel = Path(request["cwd"]) / relpath
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text("PDF_LAB_OPENCODE_WRITE_CANARY_OK\n", encoding="utf-8")
            return {
                "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
                "raw_response": {
                    "status": "completed",
                    "assistant_text": f"PDF_LAB_CANARY_OK wrote {relpath}",
                    "diff": [{"path": relpath, "status": "added"}],
                },
            }

        @staticmethod
        def materialize_opencode_host_artifacts(case_dir, receipt, *, prefix=""):  # noqa: ARG004
            return []

    monkeypatch.setattr(harness, "git_status_short", lambda repo: [])  # noqa: ARG005

    canary = harness.run_opencode_completion_canary(
        out_dir=tmp_path / "out",
        page_dag=FakePageDag,
        code_root=code_root,
        patch_mode="live",
        patch_backend="opencode_serve",
        scillm_base_url="http://localhost:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab",
        agent="build",
        skills=None,
        timeout_s=1.0,
        cleanup_session=False,
        model="gpt-5.5",
    )

    canary_dir = tmp_path / "out/opencode_completion_canary"
    validation = json.loads((canary_dir / "opencode_completion_canary_validation.json").read_text(encoding="utf-8"))
    cleanup = json.loads((canary_dir / "opencode_completion_canary_cleanup.json").read_text(encoding="utf-8"))

    assert canary["ok"] is True
    assert captured_request["scillm_metadata"]["canary_relpath"] == ".pdf_lab_write_canary/opencode_write_canary.txt"
    assert validation["write_sentinel_present"] is True
    assert validation["write_sentinel_content_ok"] is True
    assert validation["diff_present"] is True
    assert cleanup["ok"] is True
    assert cleanup["removed_file"] is True
    assert not (code_root / ".pdf_lab_write_canary/opencode_write_canary.txt").exists()


def test_opencode_completion_canary_accepts_sentinel_without_trailing_newline(tmp_path: Path) -> None:
    harness = _load_module()
    code_root = tmp_path / "code-root"
    sentinel = code_root / ".pdf_lab_write_canary/opencode_write_canary.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("PDF_LAB_OPENCODE_WRITE_CANARY_OK", encoding="utf-8")

    validation = harness.validate_opencode_completion_canary_receipt(
        {
            "raw_response": {
                "status": "completed",
                "assistant_text": "PDF_LAB_CANARY_OK wrote .pdf_lab_write_canary/opencode_write_canary.txt",
                "diff": [{"path": ".pdf_lab_write_canary/opencode_write_canary.txt", "status": "added"}],
            }
        },
        code_root=code_root,
    )

    assert validation["ok"] is True
    assert validation["write_sentinel_content_ok"] is True


def test_validate_opencode_completion_canary_requires_patch_diff(tmp_path: Path) -> None:
    harness = _load_module()
    code_root = tmp_path / "code-root"
    sentinel = code_root / ".pdf_lab_write_canary/opencode_write_canary.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("PDF_LAB_OPENCODE_WRITE_CANARY_OK\n", encoding="utf-8")

    validation = harness.validate_opencode_completion_canary_receipt(
        {
            "raw_response": {
                "status": "completed",
                "assistant_text": "PDF_LAB_CANARY_OK wrote .pdf_lab_write_canary/opencode_write_canary.txt",
                "diff": [],
            }
        },
        code_root=code_root,
    )

    assert validation["ok"] is False
    assert validation["write_sentinel_present"] is True
    assert validation["write_sentinel_content_ok"] is True
    assert validation["diff_present"] is False
    assert "OpenCode completion canary produced no patch diff evidence" in validation["errors"]


def test_run_opencode_completion_canary_call_failure_uses_canary_validation_schema(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    code_root = tmp_path / "code-root"
    code_root.mkdir()

    class FakePageDag:
        DEFAULT_OPENCODE_SKILLS = ["scillm"]

        @staticmethod
        def call_opencode_patch(request, **kwargs):  # noqa: ARG004
            raise RuntimeError("serve unavailable")

        @staticmethod
        def materialize_opencode_host_artifacts(case_dir, receipt, *, prefix=""):  # noqa: ARG004
            return []

    monkeypatch.setattr(harness, "git_status_short", lambda repo: [])  # noqa: ARG005

    canary = harness.run_opencode_completion_canary(
        out_dir=tmp_path / "out",
        page_dag=FakePageDag,
        code_root=code_root,
        patch_mode="live",
        patch_backend="opencode_serve",
        scillm_base_url="http://localhost:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab",
        agent="build",
        skills=None,
        timeout_s=1.0,
        cleanup_session=False,
        model="gpt-5.5",
    )

    canary_dir = tmp_path / "out/opencode_completion_canary"
    validation = json.loads((canary_dir / "opencode_completion_canary_validation.json").read_text(encoding="utf-8"))
    error = json.loads((canary_dir / "opencode_completion_canary_error.json").read_text(encoding="utf-8"))

    assert canary["ok"] is False
    assert canary["error_artifact"]
    assert validation["schema"] == "pdf_lab.second_pass.opencode_completion_canary_validation.v1"
    assert validation["errors"] == ["opencode_completion_canary_call_failed"]
    assert error["node_id"] == "opencode_completion_canary"


def test_opencode_completion_canary_artifacts_are_top_level_bundle_inputs(tmp_path: Path) -> None:
    harness = _load_module()
    out_dir = tmp_path / "out"
    canary_dir = out_dir / "opencode_completion_canary"
    canary_dir.mkdir(parents=True)
    for name in [
        "opencode_completion_canary.json",
        "opencode_completion_canary_request.json",
        "opencode_completion_canary_validation.json",
        "opencode_completion_canary_cleanup.json",
        "opencode_completion_canary_receipt.json",
        "canary_opencode_host_artifacts_summary.json",
    ]:
        (canary_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
    canary = {
        "schema": "pdf_lab.second_pass.opencode_completion_canary.v1",
        "ok": True,
        "receipt_artifact": str(canary_dir / "opencode_completion_canary_receipt.json"),
        "error_artifact": None,
    }

    artifacts = harness.opencode_completion_canary_artifacts(out_dir, canary)
    validation = harness.validate_harness_review_bundle_inputs(
        zip_path=out_dir / "harness_review_bundle.zip",
        top_level_artifacts=list(artifacts.values()),
        page_results=[],
    )

    assert "opencode_completion_canary_error.json" not in artifacts
    assert "opencode_completion_canary_receipt.json" in artifacts
    assert "canary_opencode_host_artifacts_summary.json" in artifacts
    assert validation["ok"] is True
    assert validation["missing_required_artifacts"] == []


def test_build_live_scillm_canary_bug_report_surfaces_failed_transport_write(tmp_path: Path) -> None:
    harness = _load_module()
    code_root = tmp_path / "code-root"
    code_root.mkdir()

    report = harness.build_live_scillm_canary_bug_report(
        out_dir=tmp_path / "out",
        code_root=code_root,
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        code_root_visibility={"schema": "visibility", "ok": True, "errors": []},
        scillm_proof_floor={"schema": "proof", "ok": True, "errors": []},
        opencode_completion_canary=None,
        scillm_transport_readonly_canary={"schema": "readonly", "ok": True, "errors": []},
        scillm_transport_write_canary={
            "schema": "pdf_lab.second_pass.scillm_transport_write_canary.v1",
            "ok": False,
            "errors": ["scillm_transport_write_canary_call_failed"],
            "error_artifact": "scillm_transport_write_canary_error.json",
            "validation_artifact": "scillm_transport_write_canary_validation.json",
        },
    )

    assert report["ok"] is False
    assert report["failed_checks"][0]["check_id"] == "scillm_transport_write_canary"
    assert report["failed_checks"][0]["errors"] == ["scillm_transport_write_canary_call_failed"]
    assert "Fix the live scillm/OpenCode substrate" in report["scillm_project_agent_bug_report"]


def test_transport_canary_success_artifacts_do_not_require_error_artifact(tmp_path: Path) -> None:
    harness = _load_module()
    out_dir = tmp_path / "out"
    canary_dir = out_dir / "scillm_transport_readonly_canary"
    canary_dir.mkdir(parents=True)
    for name in [
        "scillm_transport_readonly_canary.json",
        "scillm_transport_readonly_canary_request.json",
        "scillm_transport_readonly_canary_validation.json",
        "scillm_transport_readonly_canary_receipt.json",
        "scillm_transport_readonly_canary_event_stream.json",
    ]:
        (canary_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
    canary = {
        "schema": "pdf_lab.second_pass.scillm_transport_readonly_canary.v1",
        "ok": True,
        "receipt_artifact": str(canary_dir / "scillm_transport_readonly_canary_receipt.json"),
        "error_artifact": None,
        "event_stream_artifact": str(canary_dir / "scillm_transport_readonly_canary_event_stream.json"),
    }

    artifacts = harness.scillm_transport_readonly_canary_artifacts(out_dir, canary)
    validation = harness.validate_harness_review_bundle_inputs(
        zip_path=out_dir / "harness_review_bundle.zip",
        top_level_artifacts=list(artifacts.values()),
        page_results=[],
    )

    assert "scillm_transport_readonly_canary_error.json" not in artifacts
    assert validation["ok"] is True
    assert validation["missing_required_artifacts"] == []


def test_package_harness_review_bundle_validates_written_zip_entries(tmp_path: Path) -> None:
    harness = _load_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    top_artifact = out_dir / "candidate_manifest.json"
    top_artifact.write_text(json.dumps({"artifact": "candidate_manifest.json"}), encoding="utf-8")
    case_dir = out_dir / "page_cases/page_case_0001_p0001"
    case_dir.mkdir(parents=True)
    (case_dir / "terminal_ledger.json").write_text(json.dumps({"terminal_status": "reviewed_clean"}), encoding="utf-8")
    (case_dir / "review_bundle.zip").write_bytes(b"zip")
    (case_dir / "review_bundle_validation.json").write_text(
        json.dumps(
            {
                "schema": "pdf_lab.second_pass.page_review_bundle_validation.v1",
                "ok": True,
                "zip_content_ok": True,
                "missing_expected_zip_entries": [],
                "duplicate_zip_entries": [],
            }
        ),
        encoding="utf-8",
    )

    packaged = harness.package_harness_review_bundle(
        out_dir=out_dir,
        zip_path=out_dir / "harness_review_bundle.zip",
        top_level_artifacts=[top_artifact],
        page_results=[
            {
                "case_id": "page_case_0001_p0001",
                "case_dir": str(case_dir),
            }
        ],
        validation_artifact_path=out_dir / "harness_review_bundle_zip.json",
    )

    assert packaged["ok"] is True
    assert packaged["zip_content_ok"] is True
    assert packaged["missing_expected_zip_entries"] == []
    assert packaged["duplicate_zip_entries"] == []
    assert (out_dir / "harness_review_bundle_zip.json").is_file()
    with zipfile.ZipFile(packaged["zip_path"]) as archive:
        names = set(archive.namelist())
    assert set(packaged["required_zip_entries"]).issubset(names)
    assert "harness_review_bundle_zip.json" in names
    assert "page_cases/page_case_0001_p0001/review_bundle_validation.json" in names


def test_validate_harness_review_bundle_zip_rejects_stale_entry(tmp_path: Path) -> None:
    harness = _load_module()
    source = tmp_path / "candidate_manifest.json"
    source.write_text(json.dumps({"artifact": "fresh"}), encoding="utf-8")
    zip_path = tmp_path / "harness_review_bundle.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("candidate_manifest.json", json.dumps({"artifact": "stale"}))

    validation = harness.validate_harness_review_bundle_zip(
        zip_path=zip_path,
        included_artifacts=["candidate_manifest.json"],
        missing_required_artifacts=[],
        required_zip_entries=["candidate_manifest.json"],
        expected_sources={"candidate_manifest.json": source},
        page_case_count=0,
    )

    assert validation["ok"] is False
    assert validation["zip_content_ok"] is False
    assert validation["mismatched_zip_entries"] == ["candidate_manifest.json"]


def test_validate_harness_review_bundle_consistency_checks_final_gate(tmp_path: Path) -> None:
    harness = _load_module()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    report_path = out_dir / "harness_report.json"
    readiness_audit_path = out_dir / "harness_readiness_audit.json"
    bundle_validation_path = out_dir / "harness_review_bundle_zip.json"
    final_gate_path = out_dir / "harness_final_gate.json"
    zip_path = out_dir / "harness_review_bundle.zip"

    report_path.write_text(json.dumps({"schema": "report", "final_gate": {"ok": True}}), encoding="utf-8")
    readiness_audit_path.write_text(json.dumps({"schema": "audit", "ok": True}), encoding="utf-8")
    bundle_validation_path.write_text(json.dumps({"schema": "bundle", "ok": True}), encoding="utf-8")
    final_gate_path.write_text(json.dumps({"schema": "final_gate", "ok": True}), encoding="utf-8")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(report_path, arcname=report_path.name)
        archive.write(readiness_audit_path, arcname=readiness_audit_path.name)
        archive.write(bundle_validation_path, arcname=bundle_validation_path.name)
        archive.writestr(final_gate_path.name, json.dumps({"schema": "final_gate", "ok": False}))

    validation = harness.validate_harness_review_bundle_consistency(
        zip_path=zip_path,
        report_path=report_path,
        readiness_audit_path=readiness_audit_path,
        bundle_validation_path=bundle_validation_path,
        final_gate_path=final_gate_path,
    )

    assert validation["ok"] is False
    assert validation["comparisons"]["harness_report.json"] is True
    assert validation["comparisons"]["harness_readiness_audit.json"] is True
    assert validation["comparisons"]["harness_review_bundle_zip.json"] is True
    assert validation["comparisons"]["harness_final_gate.json"] is False
    assert "harness_final_gate.json in harness review bundle does not match persisted artifact" in validation["errors"]


def test_build_harness_final_gate_passes_only_when_readiness_and_bundle_consistency_pass() -> None:
    harness = _load_module()

    final_gate = harness.build_harness_final_gate(
        harness_readiness_audit={"ok": True, "failed_requirements": []},
        harness_review_bundle_consistency_validation={"ok": True, "errors": []},
        report_terminal_status="passed",
    )

    assert final_gate == {
        "schema": "pdf_lab.second_pass.harness_final_gate.v1",
        "ok": True,
        "readiness_ok": True,
        "bundle_consistency_ok": True,
        "terminal_status": "passed",
        "errors": [],
    }


def test_build_harness_final_gate_rejects_stale_report_terminal_status() -> None:
    harness = _load_module()

    final_gate = harness.build_harness_final_gate(
        harness_readiness_audit={"ok": False, "failed_requirements": ["aggregate terminal statuses are resolved"]},
        harness_review_bundle_consistency_validation={"ok": True, "errors": []},
        report_terminal_status="passed",
    )

    assert final_gate["ok"] is False
    assert final_gate["readiness_ok"] is False
    assert final_gate["bundle_consistency_ok"] is True
    assert final_gate["terminal_status"] == "failed_closed"
    errors = "\n".join(final_gate["errors"])
    assert "readiness failed: aggregate terminal statuses are resolved" in errors
    assert "report terminal_status 'passed' does not match final gate terminal_status 'failed_closed'" in errors


def test_run_harness_fails_closed_before_page_dag_when_transport_readonly_canary_fails(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    page_dag_called = False
    code_root = tmp_path / "mounted" / "code-root"
    _mark_isolated_code_root(code_root)

    class FakeManifestMod:
        @staticmethod
        def extract_pages(pdf_path, ledger_path, apply_mode, max_pages):  # noqa: ARG004
            return ([{"page": 1, "blocks": [{"id": "b1", "type": "table"}]}], 1)

        @staticmethod
        def build_manifest_from_pages(**kwargs):  # noqa: ARG004
            return _candidate_manifest([_manifest_candidate("cand:p0001:0000:table", 1, "table")])

    class FakeSamplerMod:
        @staticmethod
        def select_page_cases(manifest, sample_size, seed):  # noqa: ARG004
            return {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                "selected_count": 1,
                "selected_pages": [1],
                "seed": seed,
                "sampling_audit": _passing_sampling_audit(candidate_count=1, selected_count=1, seed=seed),
                "page_cases": [_sampled_page_case(candidate_id="cand:p0001:0000:table", page_number=1)],
            }

    class FakePageDag:
        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):  # noqa: ARG004
            return opencode_model or "opencode-go/kimi-k2.6"

        @staticmethod
        def run_page_case(**kwargs):  # noqa: ARG004
            nonlocal page_dag_called
            page_dag_called = True
            raise AssertionError("page DAG should not run when transport canary fails")

    def passing_proof_floor(**kwargs):
        proof_dir = kwargs["out_dir"] / "scillm_proof_floor"
        proof_dir.mkdir(parents=True, exist_ok=True)
        for name in harness.scillm_proof_floor_artifacts(kwargs["out_dir"], {"ok": True}).keys():
            (proof_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
        return {
            "schema": "pdf_lab.second_pass.scillm_proof_floor.v1",
            "ok": True,
            "errors": [],
            "artifact_dir": str(proof_dir),
        }

    def failing_transport_canary(**kwargs):
        canary_dir = kwargs["out_dir"] / "scillm_transport_readonly_canary"
        canary_dir.mkdir(parents=True, exist_ok=True)
        for name in [
            "scillm_transport_readonly_canary.json",
            "scillm_transport_readonly_canary_request.json",
            "scillm_transport_readonly_canary_validation.json",
            "scillm_transport_readonly_canary_receipt.json",
        ]:
            (canary_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
        return {
            "schema": "pdf_lab.second_pass.scillm_transport_readonly_canary.v1",
            "ok": False,
            "errors": ["transport read-only canary produced no assistant_text"],
            "artifact_dir": str(canary_dir),
        }

    def write_canary_should_not_run(**kwargs):  # noqa: ARG001
        raise AssertionError("transport write canary should not run when read-only canary fails")

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))
    monkeypatch.setattr(harness, "run_scillm_proof_floor", passing_proof_floor)
    monkeypatch.setattr(harness, "run_scillm_transport_readonly_canary", failing_transport_canary)
    monkeypatch.setattr(harness, "run_scillm_transport_write_canary", write_canary_should_not_run)

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=1,
        sample_size=1,
        seed=123,
        review_mode="live",
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        commit_mode="real",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=None,
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="live",
        opencode_agent="build",
        opencode_model=None,
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=["scillm"],
        allowed_patch_prefixes=["tests/"],
        validation_commands=None,
        code_root=code_root,
        prepare_isolated_code_root_dest=None,
        prepare_isolated_code_root_include_paths=None,
        prepare_isolated_code_root_force=False,
        scillm_mounted_workspace_prefixes=[tmp_path / "mounted"],
    )

    assert page_dag_called is False
    assert report["terminal_status"] == "failed_closed"
    assert report["scillm_transport_readonly_canary"]["ok"] is False
    assert report["page_results"][0]["reason"] == "scillm_transport_readonly_canary_failed"
    assert "live scillm transport read-only canary passed" in report["harness_readiness_audit_validation"]["failed_requirements"]
    assert "scillm_transport_readonly_canary.json" in report["page_results"][0]["evidence_artifacts"]
    with zipfile.ZipFile(report["harness_review_bundle"]) as archive:
        names = set(archive.namelist())
    assert "scillm_transport_readonly_canary.json" in names
    assert "page_cases/page_case_0001_p0001/scillm_transport_readonly_canary.json" in names


def test_run_harness_fails_closed_before_page_dag_when_transport_write_canary_fails(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    page_dag_called = False
    code_root = tmp_path / "mounted" / "code-root"
    _mark_isolated_code_root(code_root)

    class FakeManifestMod:
        @staticmethod
        def extract_pages(pdf_path, ledger_path, apply_mode, max_pages):  # noqa: ARG004
            return ([{"page": 1, "blocks": [{"id": "b1", "type": "table"}]}], 1)

        @staticmethod
        def build_manifest_from_pages(**kwargs):  # noqa: ARG004
            return _candidate_manifest([_manifest_candidate("cand:p0001:0000:table", 1, "table")])

    class FakeSamplerMod:
        @staticmethod
        def select_page_cases(manifest, sample_size, seed):  # noqa: ARG004
            return {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                "selected_count": 1,
                "selected_pages": [1],
                "seed": seed,
                "sampling_audit": _passing_sampling_audit(candidate_count=1, selected_count=1, seed=seed),
                "page_cases": [_sampled_page_case(candidate_id="cand:p0001:0000:table", page_number=1)],
            }

    class FakePageDag:
        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):  # noqa: ARG004
            return opencode_model or "opencode-go/kimi-k2.6"

        @staticmethod
        def run_page_case(**kwargs):  # noqa: ARG004
            nonlocal page_dag_called
            page_dag_called = True
            raise AssertionError("page DAG should not run when transport write canary fails")

    def passing_proof_floor(**kwargs):
        proof_dir = kwargs["out_dir"] / "scillm_proof_floor"
        proof_dir.mkdir(parents=True, exist_ok=True)
        for name in harness.scillm_proof_floor_artifacts(kwargs["out_dir"], {"ok": True}).keys():
            (proof_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
        return {
            "schema": "pdf_lab.second_pass.scillm_proof_floor.v1",
            "ok": True,
            "errors": [],
            "artifact_dir": str(proof_dir),
        }

    def passing_readonly_canary(**kwargs):
        canary_dir = kwargs["out_dir"] / "scillm_transport_readonly_canary"
        canary_dir.mkdir(parents=True, exist_ok=True)
        for name in [
            "scillm_transport_readonly_canary.json",
            "scillm_transport_readonly_canary_request.json",
            "scillm_transport_readonly_canary_validation.json",
            "scillm_transport_readonly_canary_receipt.json",
        ]:
            (canary_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
        return {
            "schema": "pdf_lab.second_pass.scillm_transport_readonly_canary.v1",
            "ok": True,
            "errors": [],
            "receipt_artifact": str(canary_dir / "scillm_transport_readonly_canary_receipt.json"),
        }

    def failing_write_canary(**kwargs):
        canary_dir = kwargs["out_dir"] / "scillm_transport_write_canary"
        canary_dir.mkdir(parents=True, exist_ok=True)
        for name in [
            "scillm_transport_write_canary.json",
            "scillm_transport_write_canary_request.json",
            "scillm_transport_write_canary_validation.json",
            "scillm_transport_write_canary_cleanup.json",
            "scillm_transport_write_canary_receipt.json",
        ]:
            (canary_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
        return {
            "schema": "pdf_lab.second_pass.scillm_transport_write_canary.v1",
            "ok": False,
            "errors": ["transport write canary did not create sentinel file"],
            "receipt_artifact": str(canary_dir / "scillm_transport_write_canary_receipt.json"),
            "cleanup_artifact": str(canary_dir / "scillm_transport_write_canary_cleanup.json"),
        }

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))
    monkeypatch.setattr(harness, "run_scillm_proof_floor", passing_proof_floor)
    monkeypatch.setattr(harness, "run_scillm_transport_readonly_canary", passing_readonly_canary)
    monkeypatch.setattr(harness, "run_scillm_transport_write_canary", failing_write_canary)

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=1,
        sample_size=1,
        seed=123,
        review_mode="live",
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        commit_mode="real",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=None,
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="live",
        opencode_agent="build",
        opencode_model=None,
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=["scillm"],
        allowed_patch_prefixes=["tests/"],
        validation_commands=None,
        code_root=code_root,
        prepare_isolated_code_root_dest=None,
        prepare_isolated_code_root_include_paths=None,
        prepare_isolated_code_root_force=False,
        scillm_mounted_workspace_prefixes=[tmp_path / "mounted"],
    )

    assert page_dag_called is False
    assert report["terminal_status"] == "failed_closed"
    assert report["scillm_transport_write_canary"]["ok"] is False
    assert report["page_results"][0]["reason"] == "scillm_transport_write_canary_failed"
    assert "live scillm transport write-capability canary passed" in report["harness_readiness_audit_validation"]["failed_requirements"]
    assert "scillm_transport_write_canary_cleanup.json" in report["page_results"][0]["evidence_artifacts"]
    with zipfile.ZipFile(report["harness_review_bundle"]) as archive:
        names = set(archive.namelist())
    assert "scillm_transport_write_canary.json" in names
    assert "page_cases/page_case_0001_p0001/scillm_transport_write_canary_cleanup.json" in names


def test_run_harness_fails_closed_before_page_dag_when_scillm_proof_floor_fails(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    page_dag_called = False

    class FakeManifestMod:
        @staticmethod
        def extract_pages(pdf_path, ledger_path, apply_mode, max_pages):  # noqa: ARG004
            return ([{"page": 1, "blocks": [{"id": "b1", "type": "table"}]}], 1)

        @staticmethod
        def build_manifest_from_pages(**kwargs):  # noqa: ARG004
            return _candidate_manifest([_manifest_candidate("cand:p0001:0000:table", 1, "table")])

    class FakeSamplerMod:
        @staticmethod
        def select_page_cases(manifest, sample_size, seed):  # noqa: ARG004
            return {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                "selected_count": 1,
                "selected_pages": [1],
                "seed": seed,
                "sampling_audit": _passing_sampling_audit(candidate_count=1, selected_count=1, seed=seed),
                "page_cases": [_sampled_page_case(candidate_id="cand:p0001:0000:table", page_number=1)],
            }

    class FakePageDag:
        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):  # noqa: ARG004
            return opencode_model

        @staticmethod
        def run_page_case(**kwargs):  # noqa: ARG004
            nonlocal page_dag_called
            page_dag_called = True
            raise AssertionError("page DAG should not run when scillm proof floor fails")

    def fake_run_scillm_proof_floor(**kwargs):
        proof_dir = kwargs["out_dir"] / "scillm_proof_floor"
        proof_dir.mkdir(parents=True)
        for name in [
            "scillm_proof_floor.json",
            "scillm_proof_floor_validation.json",
            "liveliness_response.json",
            "opencode_health_response.json",
            "positive_chat_request.json",
            "positive_chat_response.json",
            "missing_caller_chat_request.json",
            "missing_caller_chat_response.json",
        ]:
            (proof_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
        return {
            "schema": "pdf_lab.second_pass.scillm_proof_floor.v1",
            "ok": False,
            "errors": ["positive chat preflight did not return HTTP 200"],
            "artifact_dir": str(proof_dir),
        }

    def fail_opencode_canary(**kwargs):  # noqa: ARG001
        raise AssertionError("OpenCode canary should not run when scillm proof floor fails")

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))
    monkeypatch.setattr(harness, "run_scillm_proof_floor", fake_run_scillm_proof_floor)
    monkeypatch.setattr(harness, "run_opencode_completion_canary", fail_opencode_canary)
    code_root = tmp_path / "code-root"
    _mark_isolated_code_root(code_root)

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=1,
        sample_size=1,
        seed=123,
        review_mode="live",
        patch_mode="live",
        patch_backend="opencode_serve",
        commit_mode="real",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=None,
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="live",
        opencode_agent="build",
        opencode_model="gpt-5.5",
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=["scillm"],
        allowed_patch_prefixes=["tests/"],
        validation_commands=None,
        code_root=code_root,
        prepare_isolated_code_root_dest=None,
        prepare_isolated_code_root_include_paths=None,
        prepare_isolated_code_root_force=False,
        scillm_mounted_workspace_prefixes=[tmp_path],
    )

    assert page_dag_called is False
    assert report["terminal_status"] == "failed_closed"
    assert report["scillm_proof_floor"]["ok"] is False
    assert report["page_results"][0]["reason"] == "scillm_proof_floor_failed"
    assert "live scillm proof floor passed" in report["harness_readiness_audit_validation"]["failed_requirements"]
    assert "scillm_proof_floor.json" in report["page_results"][0]["evidence_artifacts"]
    with zipfile.ZipFile(report["harness_review_bundle"]) as archive:
        names = set(archive.namelist())
    assert "scillm_proof_floor.json" in names
    assert "page_cases/page_case_0001_p0001/scillm_proof_floor.json" in names


def test_run_harness_writes_manifest_sample_and_report(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    captured_page_kwargs = {}
    captured_forced_pages = {}
    human_pages_path = tmp_path / "human_pages.json"
    human_pages_path.write_text("[1]\n", encoding="utf-8")

    class FakeManifestMod:
        @staticmethod
        def extract_pages(pdf_path, ledger_path, apply_mode, max_pages):
            return (
                [
                    {
                        "page": 1,
                        "blocks": [
                            {
                                "id": "b1",
                                "type": "table",
                                "bbox": [0.1, 0.2, 0.8, 0.4],
                                "text": "A | B",
                            }
                        ],
                    }
                ],
                1,
            )

        @staticmethod
        def build_manifest_from_pages(**kwargs):
            return {
                "schema": "pdf_lab.second_pass.candidate_manifest.v1",
                "pdf_id": "fake",
                "pdf_path": str(kwargs["pdf_path"]),
                "page_count": 1,
                "preset_types": ["table", "text"],
                "candidate_count": 1,
                "preset_counts": {"table": 1},
                "pages": [
                    {
                        "page_number": 1,
                        "candidate_count": 1,
                        "risk_candidate_count": 1,
                        "preset_counts": {"table": 1},
                    }
                ],
                "candidates": [
                    _manifest_candidate("cand:p0001:0000:table", 1, "table")
                ],
            }

    class FakeSamplerMod:
        @staticmethod
        def load_forced_pages(path):
            assert path == human_pages_path
            return [1]

        @staticmethod
        def select_page_cases(manifest, sample_size, seed, forced_pages=None):
            captured_forced_pages["value"] = forced_pages
            return {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                    "forced_pages": {"requested": forced_pages or [], "accepted": forced_pages or [], "rejected": []},
                    "selected_count": 1,
                    "seed": seed,
                    "selected_pages": [1],
                    "probabilistic_selected_pages": [],
                    "sampling_audit": {
                        **_passing_sampling_audit(candidate_count=1, selected_count=1, seed=seed),
                        "probabilistic_selected_count": 0,
                        "forced_pages_are_additive": True,
                        "statistical_significance_basis": {
                            **_passing_sampling_audit(candidate_count=1, selected_count=1, seed=seed)["statistical_significance_basis"],
                            "probabilistic_selected_page_count": 0,
                        "accepted_forced_page_count": 1,
                        "forced_pages_are_additive": True,
                    },
                },
                "page_cases": [_sampled_page_case(candidate_id="cand:p0001:0000:table", page_number=1, forced=True)],
            }

    class FakePageDag:
        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):
            if opencode_model:
                return opencode_model
            if patch_mode == "live" and patch_backend == "scillm_orchestrator":
                return "opencode-go/kimi-k2.6"
            return None

        @staticmethod
        def run_page_case(**kwargs):
            captured_page_kwargs.update(kwargs)
            case_dir = kwargs["out_dir"] / kwargs["case_id"]
            _write_page_dag_case(
                case_dir,
                case_id=kwargs["case_id"],
                terminal_status="reviewed_clean",
            )
            return {"case_dir": str(case_dir), "terminal_status": "reviewed_clean", "page_number": 1}

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=1,
        sample_size=1,
        seed=123,
        review_mode="dry_run",
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        commit_mode="dry_run",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=None,
        human_annotated_pages_json=human_pages_path,
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="live",
        opencode_agent="build",
        opencode_model="gpt-5.5",
        patch_prompt_profile="plan_only",
        repair_strategy="split",
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=["scillm"],
        allowed_patch_prefixes=["tests/"],
        validation_commands=None,
        code_root=tmp_path / "code-root",
        prepare_isolated_code_root_dest=None,
        prepare_isolated_code_root_include_paths=None,
        prepare_isolated_code_root_force=False,
    )

    assert report["terminal_status"] == "passed"
    assert (tmp_path / "out/candidate_manifest.json").is_file()
    assert (tmp_path / "out/candidate_manifest_integrity_validation.json").is_file()
    assert (tmp_path / "out/sampled_page_cases.json").is_file()
    assert (tmp_path / "out/forced_pages_input.json").is_file()
    assert (tmp_path / "out/deterministic_execution_plan.json").is_file()
    assert (tmp_path / "out/deterministic_execution_plan_validation.json").is_file()
    assert (tmp_path / "out/harness_report.json").is_file()
    assert report["aggregate"]["ok"] is True
    assert report["candidate_manifest_integrity_validation_result"]["ok"] is True
    assert captured_forced_pages["value"] == [1]
    assert report["human_annotated_pages_json"] == str(human_pages_path)
    assert report["forced_pages_input_result"]["pages"] == [1]
    assert report["deterministic_execution_plan_result"]["owner"] == "pdf_lab_harness_code"
    assert report["deterministic_execution_plan_result"]["agent_decision_allowed"] is False
    assert report["deterministic_execution_plan_result"]["execution_mode"] == "sequential"
    assert report["deterministic_execution_plan_result"]["page_case_order"] == [
        {
            "index": 1,
            "case_id": "page_case_0001_p0001",
            "page_number": 1,
            "candidate_ids": ["cand:p0001:0000:table"],
            "preset_counts": {"table": 1},
            "strata": ["preset:table", "risk:high"],
            "forced_by_human_annotation": True,
            "selection_probability_estimate": 1.0,
            "selection_probability_basis": {
                "method": "forced_human_annotation",
                "forced_page": True,
            },
        }
    ]
    assert report["deterministic_execution_plan_result"]["commit_policy"]["one_git_commit_per_verified_bug_fix"] is True
    assert report["deterministic_execution_plan_validation_result"]["ok"] is True
    assert captured_page_kwargs["scillm_base_url"] == "http://example.invalid:4001"
    assert captured_page_kwargs["review_fixture_path"] is None
    assert captured_page_kwargs["scillm_auth_token"] == "token"
    assert captured_page_kwargs["caller_skill"] == "pdf-lab-test"
    assert captured_page_kwargs["scillm_timeout_s"] == 12.5
    assert captured_page_kwargs["scillm_preflight_mode"] == "live"
    assert captured_page_kwargs["opencode_agent"] == "build"
    assert captured_page_kwargs["patch_prompt_profile"] == "plan_only"
    assert captured_page_kwargs["repair_strategy"] == "split"
    assert captured_page_kwargs["opencode_timeout_s"] == 55.0
    assert captured_page_kwargs["opencode_cleanup_session"] is False
    assert captured_page_kwargs["opencode_skills"] == ["scillm"]
    assert captured_page_kwargs["allowed_patch_prefixes"] == ["tests/"]
    assert captured_page_kwargs["code_root"] == tmp_path / "code-root"
    assert report["scillm_base_url"] == "http://example.invalid:4001"
    assert report["caller_skill"] == "pdf-lab-test"
    assert report["scillm_preflight_mode"] == "live"
    assert report["patch_prompt_profile"] == "plan_only"
    assert report["repair_strategy"] == "split"
    assert report["code_root"] == str(tmp_path / "code-root")
    assert report["isolated_code_root_manifest"] is None
    assert Path(report["sampling_gate"]).is_file()
    assert report["sampling_gate_validation"]["ok"] is True
    assert Path(report["candidate_sample_linkage_validation"]).is_file()
    assert report["candidate_sample_linkage_validation_result"]["ok"] is True
    assert Path(report["harness_review_bundle"]).is_file()
    assert report["harness_review_bundle_zip_validation"]["ok"] is True
    assert report["harness_review_bundle_zip_validation"]["zip_content_ok"] is True
    assert report["harness_review_bundle_zip_validation"]["missing_expected_zip_entries"] == []
    assert Path(report["harness_readiness_audit"]).is_file()
    assert report["harness_readiness_audit_validation"]["ok"] is True
    assert Path(report["harness_review_bundle_consistency_validation"]).is_file()
    assert report["harness_review_bundle_consistency_validation_result"]["ok"] is True
    assert report["harness_review_bundle_consistency_validation_result"]["comparisons"]["harness_report.json"] is True
    assert report["harness_review_bundle_consistency_validation_result"]["comparisons"]["harness_readiness_audit.json"] is True
    assert report["harness_review_bundle_consistency_validation_result"]["comparisons"]["harness_review_bundle_zip.json"] is True
    assert report["harness_review_bundle_consistency_validation_result"]["comparisons"]["harness_final_gate.json"] is True
    assert report["final_gate"] == {
        "schema": "pdf_lab.second_pass.harness_final_gate.v1",
        "ok": True,
        "readiness_ok": True,
        "bundle_consistency_ok": True,
        "terminal_status": "passed",
        "errors": [],
    }
    assert Path(report["harness_final_gate"]).is_file()
    assert json.loads(Path(report["harness_final_gate"]).read_text(encoding="utf-8")) == report["final_gate"]
    with zipfile.ZipFile(report["harness_review_bundle"]) as archive:
        names = set(archive.namelist())
    assert "candidate_manifest.json" in names
    assert "candidate_manifest_integrity_validation.json" in names
    assert "candidate_sample_linkage_validation.json" in names
    assert "forced_pages_input.json" in names
    assert "deterministic_execution_plan.json" in names
    assert "deterministic_execution_plan_validation.json" in names
    assert "harness_review_bundle_zip.json" in names
    assert "harness_review_bundle_consistency_validation.json" in names
    assert "harness_final_gate.json" in names
    assert "harness_report.json" in names
    assert "page_cases/page_case_0001_p0001/review_bundle.zip" in names
    with zipfile.ZipFile(report["harness_review_bundle"]) as archive:
        zipped_report = json.loads(archive.read("harness_report.json").decode("utf-8"))
        zipped_final_gate = json.loads(archive.read("harness_final_gate.json").decode("utf-8"))
        zipped_consistency = json.loads(
            archive.read("harness_review_bundle_consistency_validation.json").decode("utf-8")
        )
    persisted_consistency = json.loads(
        Path(report["harness_review_bundle_consistency_validation"]).read_text(encoding="utf-8")
    )
    assert zipped_report["harness_review_bundle_zip_validation"]["ok"] is True
    assert zipped_report["final_gate"] == report["final_gate"]
    assert "preflight_only" not in zipped_report["harness_review_bundle_zip_validation"]
    assert zipped_final_gate == report["final_gate"]
    assert zipped_consistency == persisted_consistency
    assert zipped_consistency["ok"] is True


def test_run_harness_final_status_uses_actual_bundle_package_validation(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()

    class FakeManifestMod:
        @staticmethod
        def extract_pages(pdf_path, ledger_path, apply_mode, max_pages):
            return (
                [
                    {
                        "page": 1,
                        "blocks": [
                            {
                                "id": "b1",
                                "type": "table",
                                "bbox": [0.1, 0.2, 0.8, 0.4],
                                "text": "A | B",
                            }
                        ],
                    }
                ],
                1,
            )

        @staticmethod
        def build_manifest_from_pages(**kwargs):
            return {
                "schema": "pdf_lab.second_pass.candidate_manifest.v1",
                "pdf_id": "fake",
                "pdf_path": str(kwargs["pdf_path"]),
                "page_count": 1,
                "preset_types": ["table"],
                "candidate_count": 1,
                "preset_counts": {"table": 1},
                "pages": [
                    {
                        "page_number": 1,
                        "candidate_count": 1,
                        "risk_candidate_count": 1,
                        "preset_counts": {"table": 1},
                    }
                ],
                "candidates": [_manifest_candidate("cand:p0001:0000:table", 1, "table")],
            }

    class FakeSamplerMod:
        @staticmethod
        def load_forced_pages(path):
            return []

        @staticmethod
        def select_page_cases(manifest, sample_size, seed, forced_pages=None):
            return {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                "selected_count": 1,
                "selected_pages": [1],
                "seed": seed,
                "sampling_audit": _passing_sampling_audit(candidate_count=1, selected_count=1, seed=seed),
                "page_cases": [_sampled_page_case(candidate_id="cand:p0001:0000:table", page_number=1)],
            }

    class FakePageDag:
        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):
            return opencode_model

        @staticmethod
        def run_page_case(**kwargs):
            case_dir = kwargs["out_dir"] / kwargs["case_id"]
            _write_page_dag_case(
                case_dir,
                case_id=kwargs["case_id"],
                terminal_status="reviewed_clean",
            )
            return {"case_dir": str(case_dir), "terminal_status": "reviewed_clean", "page_number": 1}

    def fail_package_harness_review_bundle(**kwargs):
        validation = {
            "schema": "pdf_lab.second_pass.harness_review_bundle_zip.v1",
            "zip_path": str(kwargs["zip_path"]),
            "included_count": 0,
            "included_artifacts": [],
            "missing_required_artifacts": [],
            "required_zip_entries": ["harness_report.json"],
            "zip_entry_count": 0,
            "zip_content_ok": False,
            "missing_expected_zip_entries": ["harness_report.json"],
            "duplicate_zip_entries": [],
            "page_case_count": len(kwargs["page_results"]),
            "ok": False,
        }
        validation_artifact_path = kwargs.get("validation_artifact_path")
        if validation_artifact_path is not None:
            harness.write_json(validation_artifact_path, validation)
        return validation

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))
    monkeypatch.setattr(harness, "package_harness_review_bundle", fail_package_harness_review_bundle)

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=1,
        sample_size=1,
        seed=123,
        review_mode="dry_run",
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        commit_mode="dry_run",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=None,
        human_annotated_pages_json=None,
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="live",
        opencode_agent="build",
        opencode_model="gpt-5.5",
        patch_prompt_profile="full",
        repair_strategy="single",
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=["scillm"],
        allowed_patch_prefixes=["tests/"],
        validation_commands=None,
        code_root=tmp_path / "code-root",
        prepare_isolated_code_root_dest=None,
        prepare_isolated_code_root_include_paths=None,
        prepare_isolated_code_root_force=False,
    )

    persisted_report = json.loads((tmp_path / "out/harness_report.json").read_text(encoding="utf-8"))
    persisted_audit = json.loads((tmp_path / "out/harness_readiness_audit.json").read_text(encoding="utf-8"))
    assert report["terminal_status"] == "failed_closed"
    assert persisted_report["terminal_status"] == "failed_closed"
    assert report["harness_review_bundle_zip_validation"]["ok"] is False
    assert report["harness_review_bundle_consistency_validation_result"]["ok"] is False
    assert "harness review bundle zip is missing" in json.dumps(report["harness_review_bundle_consistency_validation_result"])
    assert report["final_gate"]["ok"] is False
    assert report["final_gate"]["readiness_ok"] is False
    assert report["final_gate"]["bundle_consistency_ok"] is False
    assert report["final_gate"]["terminal_status"] == "failed_closed"
    assert "readiness failed: harness review bundle is packageable" in report["final_gate"]["errors"]
    assert any(error.startswith("bundle consistency failed:") for error in report["final_gate"]["errors"])
    assert persisted_audit["ok"] is False
    assert "harness review bundle is packageable" in persisted_audit["failed_requirements"]
    assert "missing_expected_zip_entries: harness_report.json" in json.dumps(persisted_audit)


def test_run_harness_fails_closed_when_sampling_gate_is_inadequate(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    page_dag_called = False
    proof_floor_called = False
    code_root = tmp_path / "mounted" / "code-root"
    _mark_isolated_code_root(code_root)

    class FakeManifestMod:
        @staticmethod
        def extract_pages(pdf_path, ledger_path, apply_mode, max_pages):
            return ([{"page": 1, "blocks": [{"id": "b1", "type": "table"}]}], 12)

        @staticmethod
        def build_manifest_from_pages(**kwargs):
            return _candidate_manifest(
                [
                    _manifest_candidate("cand:p0001:0000:table", 1, "table"),
                    _manifest_candidate("cand:p0002:0000:equation", 2, "equation"),
                    _manifest_candidate("cand:p0003:0000:footnote", 3, "footnote"),
                    _manifest_candidate("cand:p0004:0000:reference", 4, "reference"),
                ],
            )

    class FakeSamplerMod:
        @staticmethod
        def select_page_cases(manifest, sample_size, seed):
            return {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                "selected_count": 1,
                "selected_pages": [1],
                "sampling_audit": {
                    "schema": "pdf_lab.second_pass.sampling_audit.v1",
                    "candidate_count": 4,
                    "requested_sample_size": sample_size,
                    "selected_count": 1,
                    "recommended_min_sample_size": 4,
                    "adequate_sample_size": False,
                    "adequate_for_priority_strata": False,
                    "covered_priority_strata": ["preset:table"],
                    "missed_priority_strata": ["preset:equation", "preset:footnote", "preset:reference"],
                    "warnings": ["priority strata not represented in selected pages"],
                },
                "page_cases": [_sampled_page_case(candidate_id="cand:p0001:0000:table", page_number=1)],
            }

    class FakePageDag:
        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):
            return opencode_model

        @staticmethod
        def run_page_case(**kwargs):
            nonlocal page_dag_called
            page_dag_called = True
            raise AssertionError("page DAG should not run when sampling gate is inadequate")

    def fail_if_proof_floor_runs(**kwargs):
        nonlocal proof_floor_called
        proof_floor_called = True
        raise AssertionError("scillm proof floor should not run when sampling gate is inadequate")

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))
    monkeypatch.setattr(harness, "run_scillm_proof_floor", fail_if_proof_floor_runs)

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=12,
        sample_size=1,
        seed=123,
        review_mode="dry_run",
        patch_mode="live",
        patch_backend="opencode_serve",
        commit_mode="dry_run",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=None,
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="dry_run",
        opencode_agent="build",
        opencode_model="gpt-5.5",
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=["scillm"],
        allowed_patch_prefixes=["tests/"],
        validation_commands=None,
        code_root=code_root,
        prepare_isolated_code_root_dest=None,
        prepare_isolated_code_root_include_paths=None,
        prepare_isolated_code_root_force=False,
        scillm_mounted_workspace_prefixes=[tmp_path / "mounted"],
    )

    persisted_gate = json.loads((tmp_path / "out/sampling_gate.json").read_text(encoding="utf-8"))
    assert report["aggregate"]["ok"] is True
    assert report["sampling_gate_validation"]["ok"] is False
    assert report["page_results"] == []
    assert page_dag_called is False
    assert proof_floor_called is False
    assert "sampling_gate.ok" in report["deterministic_execution_plan_result"]["pre_page_gates"]
    assert report["harness_readiness_audit_validation"]["ok"] is False
    assert "sampling gate passed" in report["harness_readiness_audit_validation"]["failed_requirements"]
    assert persisted_gate["missed_priority_strata"] == ["preset:equation", "preset:footnote", "preset:reference"]
    assert report["terminal_status"] == "failed_closed"


def test_run_harness_fails_closed_before_page_dag_when_sample_candidate_is_unknown(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    page_dag_called = False

    class FakeManifestMod:
        @staticmethod
        def extract_pages(pdf_path, ledger_path, apply_mode, max_pages):
            return ([{"page": 1, "blocks": [{"id": "b1", "type": "table"}]}], 1)

        @staticmethod
        def build_manifest_from_pages(**kwargs):
            return _candidate_manifest([_manifest_candidate("cand:p0001:0000:table", 1, "table")])

    class FakeSamplerMod:
        @staticmethod
        def select_page_cases(manifest, sample_size, seed):
            return {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                "selected_count": 1,
                "selected_pages": [1],
                "seed": seed,
                "sampling_audit": _passing_sampling_audit(candidate_count=1, selected_count=1, seed=seed),
                "page_cases": [_sampled_page_case(candidate_id="cand:p0001:9999:table", page_number=1)],
            }

    class FakePageDag:
        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):
            return opencode_model

        @staticmethod
        def run_page_case(**kwargs):
            nonlocal page_dag_called
            page_dag_called = True
            raise AssertionError("page DAG should not run when candidate/sample linkage is invalid")

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=1,
        sample_size=1,
        seed=123,
        review_mode="dry_run",
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        commit_mode="dry_run",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=None,
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="dry_run",
        opencode_agent="build",
        opencode_model="gpt-5.5",
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=["scillm"],
        allowed_patch_prefixes=["tests/"],
        validation_commands=None,
        code_root=tmp_path / "code-root",
        prepare_isolated_code_root_dest=None,
        prepare_isolated_code_root_include_paths=None,
        prepare_isolated_code_root_force=False,
    )

    validation = json.loads((tmp_path / "out/candidate_sample_linkage_validation.json").read_text(encoding="utf-8"))
    assert page_dag_called is False
    assert validation["ok"] is False
    assert report["candidate_sample_linkage_validation_result"]["ok"] is False
    assert "candidate manifest and sampled cases are linked" in report["harness_readiness_audit_validation"]["failed_requirements"]
    assert "sampled candidate_ids missing from manifest" in json.dumps(report)
    assert report["terminal_status"] == "failed_closed"


def test_run_harness_passes_two_patched_pages_with_unique_commit_evidence(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    page_case_calls = []

    class FakeManifestMod:
        @staticmethod
        def extract_pages(pdf_path, ledger_path, apply_mode, max_pages):
            return (
                [
                    {"page": 1, "blocks": [{"id": "b1", "type": "table"}]},
                    {"page": 2, "blocks": [{"id": "b2", "type": "equation"}]},
                ],
                2,
            )

        @staticmethod
        def build_manifest_from_pages(**kwargs):
            return _candidate_manifest(
                [
                    _manifest_candidate("cand:p0001:0000:table", 1, "table"),
                    _manifest_candidate("cand:p0002:0000:equation", 2, "equation"),
                ],
                pdf_id="fake",
                pdf_path=str(kwargs["pdf_path"]),
                page_count=2,
            )

    class FakeSamplerMod:
        @staticmethod
        def select_page_cases(manifest, sample_size, seed):
            return {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                "selected_count": 2,
                "selected_pages": [1, 2],
                "seed": seed,
                "sampling_audit": _passing_sampling_audit(candidate_count=2, selected_count=2, seed=seed),
                "page_cases": [
                    _sampled_page_case(candidate_id="cand:p0001:0000:table", page_number=1, preset_type="table", case_index=1),
                    _sampled_page_case(candidate_id="cand:p0002:0000:equation", page_number=2, preset_type="equation", case_index=2),
                ],
            }

    class FakePageDag:
        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):
            return opencode_model

        @staticmethod
        def run_page_case(**kwargs):
            page_case_calls.append(kwargs["case_id"])
            commit_sha = {
                "page_case_0001_p0001": "sha-page-1",
                "page_case_0002_p0002": "sha-page-2",
            }[kwargs["case_id"]]
            case_dir = kwargs["out_dir"] / kwargs["case_id"]
            _write_page_dag_case(
                case_dir,
                case_id=kwargs["case_id"],
                terminal_status="patched_confirmed",
                reason="fixture_verified_patch",
                commit_sha=commit_sha,
                extra_evidence=PATCHED_CONFIRMED_ARTIFACTS,
            )
            return {"case_dir": str(case_dir), "terminal_status": "patched_confirmed"}

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=2,
        sample_size=2,
        seed=123,
        review_mode="fixture",
        patch_mode="dry_run",
        patch_backend="scillm_orchestrator",
        commit_mode="live",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=tmp_path / "review-fixture.json",
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="dry_run",
        opencode_agent="build",
        opencode_model="gpt-5.5",
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=["scillm"],
        allowed_patch_prefixes=["tests/"],
        validation_commands=["uv run pytest tests/test_pdf_lab_page_second_pass_dag.py -q"],
        code_root=tmp_path / "code-root",
        prepare_isolated_code_root_dest=None,
        prepare_isolated_code_root_include_paths=None,
        prepare_isolated_code_root_force=False,
    )

    persisted_report = json.loads((tmp_path / "out/harness_report.json").read_text(encoding="utf-8"))
    assert page_case_calls == ["page_case_0001_p0001", "page_case_0002_p0002"]
    assert report["terminal_status"] == "passed"
    assert persisted_report["terminal_status"] == "passed"
    assert report["aggregate"]["ok"] is True
    assert report["aggregate"]["commit_shas"] == ["sha-page-1", "sha-page-2"]
    assert report["aggregate"]["duplicate_commit_shas"] == []
    assert report["aggregate"]["patched_without_commit_count"] == 0
    assert report["aggregate"]["patched_missing_commit_gate_artifacts_count"] == 0
    assert report["aggregate"]["status_counts"] == {"patched_confirmed": 2}
    assert [result["commit_sha"] for result in report["page_results"]] == ["sha-page-1", "sha-page-2"]
    bundle = json.loads(Path(report["scillm_patch_delegate_bug_reports"]).read_text(encoding="utf-8"))
    assert bundle["bug_report_count"] == 0
    assert report["scillm_patch_delegate_bug_report_count"] == 0
    assert Path(report["scillm_patch_delegate_bug_reports_zip"]).is_file()
    assert report["scillm_patch_delegate_bug_reports_zip_validation"]["ok"] is True
    patch_commit_ledger = json.loads(Path(report["patch_commit_ledger"]).read_text(encoding="utf-8"))
    assert patch_commit_ledger["ok"] is True
    assert patch_commit_ledger["commit_count"] == 2
    assert patch_commit_ledger["commit_shas"] == ["sha-page-1", "sha-page-2"]
    assert Path(report["patch_commit_ledger_zip"]).is_file()
    assert report["patch_commit_ledger_zip_validation"]["ok"] is True
    assert report["patch_commit_ledger_zip_validation"]["zip_content_ok"] is True
    assert report["patch_commit_ledger_zip_validation"]["missing_expected_zip_entries"] == []
    assert report["harness_readiness_audit_validation"]["ok"] is True
    with zipfile.ZipFile(report["patch_commit_ledger_zip"]) as archive:
        names = set(archive.namelist())
    assert "patch_commit_ledger.json" in names
    assert "page_cases/page_case_0001_p0001/commit_gate.json" in names
    assert "page_cases/page_case_0002_p0002/revertability_check.json" in names


def test_run_harness_fails_closed_on_candidate_census_timeout(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    sampler_called = False
    page_dag_called = False

    class FakeManifestMod:
        pass

    class FakeSamplerMod:
        @staticmethod
        def select_page_cases(manifest, sample_size, seed):
            nonlocal sampler_called
            sampler_called = True
            raise AssertionError("sampler should not run after candidate census timeout")

    class FakePageDag:
        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):
            return opencode_model

        @staticmethod
        def run_page_case(**kwargs):
            nonlocal page_dag_called
            page_dag_called = True
            raise AssertionError("page DAG should not run after candidate census timeout")

    def fake_census(**kwargs):
        assert kwargs["timeout_s"] == 0.25
        raise harness.CandidateCensusTimeout("candidate census exceeded timeout_s=0.25")

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))
    monkeypatch.setattr(harness, "run_candidate_census", fake_census)

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=20,
        candidate_census_timeout_s=0.25,
        sample_size=12,
        seed=123,
        review_mode="fixture",
        patch_mode="dry_run",
        patch_backend="scillm_orchestrator",
        commit_mode="dry_run",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=None,
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="dry_run",
        opencode_agent="build",
        opencode_model="gpt-5.5",
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=["scillm"],
        allowed_patch_prefixes=["tests/"],
        validation_commands=None,
        code_root=tmp_path / "code-root",
        prepare_isolated_code_root_dest=None,
        prepare_isolated_code_root_include_paths=None,
        prepare_isolated_code_root_force=False,
    )

    failure = json.loads((tmp_path / "out/candidate_census_failure.json").read_text(encoding="utf-8"))
    persisted_report = json.loads((tmp_path / "out/harness_report.json").read_text(encoding="utf-8"))
    assert sampler_called is False
    assert page_dag_called is False
    assert failure["status"] == "timeout"
    assert failure["timeout_s"] == 0.25
    assert report["terminal_status"] == "failed_closed"
    assert persisted_report["terminal_status"] == "failed_closed"
    assert report["candidate_census_status"] == "timeout"
    assert report["candidate_manifest"] is None
    assert report["sampled_page_cases"] is None
    assert report["page_results"] == []
    assert report["aggregate"]["ok"] is False
    assert "candidate census failed: timeout" in report["aggregate"]["errors"]


def test_run_harness_records_page_census_failures_and_continues_sampling(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    captured_manifest_kwargs = {}
    captured_census_kwargs = {}

    class FakeManifestMod:
        @staticmethod
        def extract_pages_with_failures(
            pdf_path,
            ledger_path,
            apply_mode,
            max_pages,
            *,
            page_timeout_s,
            progress_path,
            page_numbers,
        ):
            captured_census_kwargs.update(
                {
                    "pdf_path": pdf_path,
                    "ledger_path": ledger_path,
                    "apply_mode": apply_mode,
                    "max_pages": max_pages,
                    "page_timeout_s": page_timeout_s,
                    "progress_path": progress_path,
                    "page_numbers": page_numbers,
                }
            )
            progress_path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
            progress_path.with_name("candidate_census_events.jsonl").write_text(
                json.dumps({"event": "completed"}) + "\n",
                encoding="utf-8",
            )
            return (
                [
                    {
                        "page": 2,
                        "blocks": [
                            {"id": "b1", "type": "table", "bbox": [0.1, 0.2, 0.8, 0.4], "text": "A | B"},
                        ],
                    }
                ],
                3,
                [
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

        @staticmethod
        def build_manifest_from_pages(**kwargs):
            captured_manifest_kwargs.update(kwargs)
            return _candidate_manifest(
                [_manifest_candidate("cand:p0002:0000:table", 2, "table")],
                census_failure_count=len(kwargs["census_failures"]),
                census_failures=kwargs["census_failures"],
            )

    class FakeSamplerMod:
        @staticmethod
        def select_page_cases(manifest, sample_size, seed):
            assert manifest["census_failure_count"] == 1
            return {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                "selected_count": 1,
                "selected_pages": [2],
                "seed": seed,
                "sampling_audit": _passing_sampling_audit(candidate_count=1, selected_count=1, seed=seed),
                "page_cases": [_sampled_page_case(candidate_id="cand:p0002:0000:table", page_number=2)],
            }

    class FakePageDag:
        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):
            return opencode_model

        @staticmethod
        def run_page_case(**kwargs):
            case_dir = kwargs["out_dir"] / kwargs["case_id"]
            _write_page_dag_case(
                case_dir,
                case_id=kwargs["case_id"],
                terminal_status="reviewed_clean",
            )
            return {"case_dir": str(case_dir), "terminal_status": "reviewed_clean", "page_number": 2}

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=3,
        candidate_census_timeout_s=None,
        candidate_page_timeout_s=0.5,
        candidate_census_pages=[2, 3],
        sample_size=1,
        seed=123,
        review_mode="fixture",
        patch_mode="dry_run",
        patch_backend="scillm_orchestrator",
        commit_mode="dry_run",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=None,
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="dry_run",
        opencode_agent="build",
        opencode_model="gpt-5.5",
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=["scillm"],
        allowed_patch_prefixes=["tests/"],
        validation_commands=None,
        code_root=tmp_path / "code-root",
        prepare_isolated_code_root_dest=None,
        prepare_isolated_code_root_include_paths=None,
        prepare_isolated_code_root_force=False,
    )

    manifest_json = json.loads((tmp_path / "out/candidate_manifest.json").read_text(encoding="utf-8"))
    assert captured_census_kwargs["page_timeout_s"] == 0.5
    assert captured_census_kwargs["progress_path"] == tmp_path / "out/candidate_census_progress.json"
    assert captured_census_kwargs["page_numbers"] == [2, 3]
    assert captured_manifest_kwargs["census_failures"][0]["status"] == "timeout"
    assert report["terminal_status"] == "passed"
    assert report["candidate_census_status"] == "completed"
    assert report["candidate_page_timeout_s"] == 0.5
    assert report["candidate_census_pages"] == [2, 3]
    assert report["candidate_census_failure_count"] == 1
    assert report["candidate_census_failures"][0]["page_number"] == 1
    assert report["candidate_census_progress"] == str(tmp_path / "out/candidate_census_progress.json")
    assert report["candidate_census_events"] == str(tmp_path / "out/candidate_census_events.jsonl")
    assert manifest_json["census_failure_count"] == 1
    assert report["selected_pages"] == [2]


def test_run_harness_can_prepare_isolated_code_root(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    captured_page_kwargs = {}
    prepared_dest = tmp_path / "prepared-code-root"

    class FakeManifestMod:
        @staticmethod
        def extract_pages(pdf_path, ledger_path, apply_mode, max_pages):
            return ([{"page": 1, "blocks": []}], 1)

        @staticmethod
        def build_manifest_from_pages(**kwargs):
            return _candidate_manifest([_manifest_candidate("cand:p0001:0000:table", 1, "table")])

    class FakeSamplerMod:
        @staticmethod
        def select_page_cases(manifest, sample_size, seed):
            return {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                "selected_count": 1,
                "selected_pages": [1],
                "seed": seed,
                "sampling_audit": _passing_sampling_audit(candidate_count=1, selected_count=1, seed=seed),
                "page_cases": [_sampled_page_case(candidate_id="cand:p0001:0000:table", page_number=1)],
            }

    class FakePageDag:
        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):
            if opencode_model:
                return opencode_model
            if patch_mode == "live" and patch_backend == "scillm_orchestrator":
                return "opencode-go/kimi-k2.6"
            return None

        @staticmethod
        def run_page_case(**kwargs):
            captured_page_kwargs.update(kwargs)
            case_dir = kwargs["out_dir"] / kwargs["case_id"]
            _write_page_dag_case(
                case_dir,
                case_id=kwargs["case_id"],
                terminal_status="reviewed_clean",
            )
            return {"case_dir": str(case_dir), "terminal_status": "reviewed_clean", "page_number": 1}

    def fake_prepare(**kwargs):
        assert kwargs["dest_root"] == prepared_dest
        assert kwargs["include_paths"] == ["python", "tests"]
        assert kwargs["force"] is True
        return {
            "schema": "pdf_lab.second_pass.isolated_code_root.v1",
            "dest_root": str(prepared_dest),
            "clean": True,
            "baseline_commit": "abc123",
        }

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))
    monkeypatch.setattr(harness, "prepare_code_root_if_requested", fake_prepare)

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=1,
        sample_size=1,
        seed=123,
        review_mode="dry_run",
        patch_mode="dry_run",
        patch_backend="opencode_serve",
        commit_mode="dry_run",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=None,
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="live",
        opencode_agent="build",
        opencode_model="gpt-5.5",
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=["scillm"],
        allowed_patch_prefixes=["tests/"],
        validation_commands=None,
        code_root=tmp_path / "ignored-code-root",
        prepare_isolated_code_root_dest=prepared_dest,
        prepare_isolated_code_root_include_paths=["python", "tests"],
        prepare_isolated_code_root_force=True,
    )

    assert captured_page_kwargs["code_root"] == prepared_dest
    assert captured_page_kwargs["opencode_model"] == "gpt-5.5"
    assert report["code_root"] == str(prepared_dest)
    assert report["opencode_model"] == "gpt-5.5"
    assert report["isolated_code_root_manifest"]["clean"] is True


def test_live_patch_fails_closed_when_code_root_is_not_mounted(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    page_dag_called = False

    class FakeManifestMod:
        @staticmethod
        def extract_pages(pdf_path, ledger_path, apply_mode, max_pages):
            return ([{"page": 1, "blocks": []}], 1)

        @staticmethod
        def build_manifest_from_pages(**kwargs):
            return _candidate_manifest([_manifest_candidate("cand:p0001:0000:table", 1, "table")])

    class FakeSamplerMod:
        @staticmethod
        def select_page_cases(manifest, sample_size, seed):
            return {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                "selected_count": 1,
                "selected_pages": [1],
                "seed": seed,
                "sampling_audit": _passing_sampling_audit(candidate_count=1, selected_count=1, seed=seed),
                "page_cases": [_sampled_page_case(candidate_id="cand:p0001:0000:table", page_number=1)],
            }

    class FakePageDag:
        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):
            if opencode_model:
                return opencode_model
            if patch_mode == "live" and patch_backend == "scillm_orchestrator":
                return "opencode-go/kimi-k2.6"
            return None

        @staticmethod
        def run_page_case(**kwargs):
            nonlocal page_dag_called
            page_dag_called = True
            raise AssertionError("page DAG should not run when live patch code root is not mounted")

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=1,
        sample_size=1,
        seed=123,
        review_mode="fixture",
        patch_mode="live",
        patch_backend="scillm_orchestrator",
        commit_mode="dry_run",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=None,
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="live",
        opencode_agent="build",
        opencode_model=None,
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=["scillm"],
        allowed_patch_prefixes=["tests/"],
        validation_commands=None,
        code_root=tmp_path / "unmounted-code-root",
        prepare_isolated_code_root_dest=None,
        prepare_isolated_code_root_include_paths=None,
        prepare_isolated_code_root_force=False,
        scillm_mounted_workspace_prefixes=[tmp_path / "mounted"],
    )

    visibility = json.loads((tmp_path / "out/scillm_code_root_visibility.json").read_text(encoding="utf-8"))
    assert page_dag_called is False
    assert visibility["ok"] is False
    assert visibility["under_mounted_prefix"] is False
    assert report["terminal_status"] == "failed_closed"
    assert report["opencode_model"] == "opencode-go/kimi-k2.6"
    assert report["requested_opencode_model"] is None
    assert report["opencode_model_defaulted"] is True
    assert report["aggregate"]["status_counts"] == {"blocked_substrate": 1}
    assert report["page_results"][0]["reason"] == "scillm_code_root_visibility_failed"
    assert "live scillm code root visibility passed" in report["harness_readiness_audit_validation"]["failed_requirements"]
    assert Path(report["page_results"][0]["terminal_ledger"]).is_file()
    assert Path(report["page_results"][0]["review_bundle"]).is_file()


def test_live_opencode_serve_completion_canary_blocks_page_dag_when_executor_is_silent(tmp_path: Path, monkeypatch) -> None:
    harness = _load_module()
    page_dag_called = False
    code_root = tmp_path / "mounted" / "code-root"
    _mark_isolated_code_root(code_root)

    class FakeManifestMod:
        @staticmethod
        def extract_pages(pdf_path, ledger_path, apply_mode, max_pages):
            return ([{"page": 1, "blocks": []}], 1)

        @staticmethod
        def build_manifest_from_pages(**kwargs):
            return _candidate_manifest([_manifest_candidate("cand:p0001:0000:table", 1, "table")])

    class FakeSamplerMod:
        @staticmethod
        def select_page_cases(manifest, sample_size, seed):
            return {
                "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
                "selected_count": 1,
                "selected_pages": [1],
                "seed": seed,
                "sampling_audit": _passing_sampling_audit(candidate_count=1, selected_count=1, seed=seed),
                "page_cases": [_sampled_page_case(candidate_id="cand:p0001:0000:table", page_number=1)],
            }

    class FakePageDag:
        DEFAULT_OPENCODE_SKILLS = ["memory", "scillm"]

        @staticmethod
        def resolve_effective_opencode_model(*, patch_mode, patch_backend, opencode_model):
            return opencode_model

        @staticmethod
        def call_opencode_patch(patch_request, **kwargs):
            assert patch_request["schema"] == "pdf_lab.second_pass.opencode_completion_canary_request.v1"
            assert patch_request["cwd"] == str(code_root.resolve())
            return {
                "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
                "raw_response": {
                    "run_id": "oc-silent",
                    "session_id": "ses-silent",
                    "status": "timeout",
                    "assistant_text": "",
                    "diff": [],
                    "artifacts": {},
                },
            }

        @staticmethod
        def materialize_opencode_host_artifacts(case_dir, receipt, *, prefix=""):
            summary_name = f"{prefix}opencode_host_artifacts_summary.json"
            (case_dir / summary_name).write_text(
                json.dumps(
                    {
                        "schema": "pdf_lab.second_pass.opencode_host_artifacts_summary.v1",
                        "status": "timeout",
                        "assistant_text_present": False,
                        "diff_present": False,
                    }
                ),
                encoding="utf-8",
            )
            return [summary_name]

        @staticmethod
        def validate_repair_diagnosis_delegate_receipt(receipt, *, patch_mode):
            return {
                "schema": "pdf_lab.second_pass.repair_diagnosis_validation.v1",
                "ok": False,
                "errors": ["OpenCode diagnosis timed out before producing a repair plan"],
                "diagnosis_status": "timeout",
                "assistant_text_present": False,
            }

        @staticmethod
        def run_page_case(**kwargs):
            nonlocal page_dag_called
            page_dag_called = True
            raise AssertionError("page DAG should not run when completion canary fails")

    def passing_proof_floor(**kwargs):
        proof_dir = kwargs["out_dir"] / "scillm_proof_floor"
        proof_dir.mkdir(parents=True, exist_ok=True)
        for name in [
            "scillm_proof_floor.json",
            "scillm_proof_floor_validation.json",
            "liveliness_response.json",
            "opencode_health_response.json",
            "positive_chat_request.json",
            "positive_chat_response.json",
            "missing_caller_chat_request.json",
            "missing_caller_chat_response.json",
        ]:
            (proof_dir / name).write_text(json.dumps({"artifact": name}), encoding="utf-8")
        return {
            "schema": "pdf_lab.second_pass.scillm_proof_floor.v1",
            "ok": True,
            "errors": [],
            "artifact_dir": str(proof_dir),
        }

    monkeypatch.setattr(harness, "_import_pdf_lab_modules", lambda: (FakeManifestMod, FakeSamplerMod, FakePageDag))
    monkeypatch.setattr(harness, "run_scillm_proof_floor", passing_proof_floor)

    report = harness.run_harness(
        pdf_path=tmp_path / "fake.pdf",
        out_dir=tmp_path / "out",
        ledger_path=None,
        apply_mode="release",
        max_pages=1,
        sample_size=1,
        seed=123,
        review_mode="fixture",
        patch_mode="live",
        patch_backend="opencode_serve",
        commit_mode="dry_run",
        model="gpt-5.5",
        batch_id="batch",
        review_fixture_path=None,
        scillm_base_url="http://example.invalid:4001",
        scillm_auth_token="token",
        caller_skill="pdf-lab-test",
        scillm_timeout_s=12.5,
        scillm_preflight_mode="live",
        opencode_agent="build",
        opencode_model=None,
        opencode_timeout_s=55.0,
        opencode_cleanup_session=False,
        opencode_skills=None,
        allowed_patch_prefixes=["tests/"],
        validation_commands=None,
        code_root=code_root,
        prepare_isolated_code_root_dest=None,
        prepare_isolated_code_root_include_paths=None,
        prepare_isolated_code_root_force=False,
        scillm_mounted_workspace_prefixes=[tmp_path / "mounted"],
    )

    case_dir = tmp_path / "out/page_cases/page_case_0001_p0001"
    ledger = json.loads((case_dir / "terminal_ledger.json").read_text(encoding="utf-8"))
    assert page_dag_called is False
    assert report["terminal_status"] == "failed_closed"
    assert report["opencode_completion_canary"]["ok"] is False
    assert "live opencode serve write-capability canary passed" in report["harness_readiness_audit_validation"]["failed_requirements"]
    assert report["page_results"][0]["reason"] == "opencode_completion_canary_failed"
    assert ledger["terminal_status"] == "blocked_substrate"
    assert "opencode_completion_canary_validation.json" in ledger["evidence_artifacts"]
    assert "opencode_completion_canary_cleanup.json" in ledger["evidence_artifacts"]
    assert "canary_opencode_host_artifacts_summary.json" in ledger["evidence_artifacts"]
    assert (case_dir / "opencode_completion_canary_receipt.json").is_file()
    assert (case_dir / "opencode_completion_canary_cleanup.json").is_file()
    assert (case_dir / "canary_opencode_host_artifacts_summary.json").is_file()
    with zipfile.ZipFile(report["harness_review_bundle"]) as archive:
        names = set(archive.namelist())
    assert "opencode_completion_canary.json" in names
    assert "page_cases/page_case_0001_p0001/opencode_completion_canary_cleanup.json" in names
    assert "page_cases/page_case_0001_p0001/canary_opencode_host_artifacts_summary.json" in names
