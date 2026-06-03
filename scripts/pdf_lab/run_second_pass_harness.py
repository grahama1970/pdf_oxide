#!/usr/bin/env python3
"""Document-level deterministic second-pass harness runner."""

from __future__ import annotations

import argparse
import contextlib
import inspect
import json
import math
import os
import re
import signal
import subprocess
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
PAGE_CASE_ID_RE = re.compile(r"^page_case_\d{4}_p(?P<page_number>\d{4})$")
TERMINAL_PAGE_STATUSES = {
    "reviewed_clean",
    "patched_confirmed",
    "rejected_with_proof",
    "blocked_substrate",
    "human_needed",
    "still_open",
}
RESOLVED_PASS_STATUSES = {"reviewed_clean", "patched_confirmed", "rejected_with_proof"}
DEFAULT_SCILLM_MOUNTED_WORKSPACE_PREFIXES = ["/home/graham/workspace"]
BASE_PAGE_REVIEW_BUNDLE_ARTIFACTS = {
    "terminal_ledger.json",
    "review_bundle.zip",
    "review_bundle_validation.json",
}
REQUIRED_PAGE_DAG_ARTIFACTS = {
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
}
REQUIRED_PATCHED_CONFIRMED_ARTIFACTS = {
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
}
OPTIONAL_PAGE_REVIEW_BUNDLE_ARTIFACTS = {
    "scillm_patch_delegate_bug_report.json",
    "patch_request.json",
    "patch_receipt.json",
    "patch_validation.json",
    "patch_attempts_ledger.json",
    "transport_event_stream.json",
    "transport_events.jsonl",
    "scillm_proof_floor.json",
    "scillm_proof_floor_validation.json",
    "liveliness_response.json",
    "opencode_health_response.json",
    "positive_chat_request.json",
    "positive_chat_response.json",
    "missing_caller_chat_request.json",
    "missing_caller_chat_response.json",
    "opencode_completion_canary.json",
    "opencode_completion_canary_request.json",
    "opencode_completion_canary_validation.json",
    "opencode_completion_canary_cleanup.json",
    "opencode_completion_canary_receipt.json",
    "opencode_completion_canary_error.json",
    "canary_opencode_host_status.json",
    "canary_opencode_host_result.json",
    "canary_opencode_host_events.jsonl",
    "canary_opencode_host_artifacts_summary.json",
    "scillm_transport_readonly_canary.json",
    "scillm_transport_readonly_canary_request.json",
    "scillm_transport_readonly_canary_validation.json",
    "scillm_transport_readonly_canary_receipt.json",
    "scillm_transport_readonly_canary_error.json",
    "scillm_transport_readonly_canary_event_stream.json",
    "scillm_transport_write_canary.json",
    "scillm_transport_write_canary_request.json",
    "scillm_transport_write_canary_validation.json",
    "scillm_transport_write_canary_cleanup.json",
    "scillm_transport_write_canary_receipt.json",
    "scillm_transport_write_canary_error.json",
    "scillm_transport_write_canary_event_stream.json",
}


class CandidateCensusTimeout(TimeoutError):
    """Raised when pdf_oxide candidate census exceeds the harness timeout."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json_object_if_exists(path: Path) -> tuple[dict[str, Any], list[str]]:
    if not path.exists():
        return {}, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - malformed artifact is recorded as validation evidence.
        return {}, [f"{path.name} unreadable: {type(exc).__name__}: {exc}"]
    if not isinstance(payload, dict):
        return {}, [f"{path.name} is not a JSON object"]
    return payload, []


def is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_plain_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def read_non_negative_int_field(
    payload: dict[str, Any],
    *,
    field_name: str,
    artifact_label: str,
    errors: list[str],
) -> int:
    value = payload.get(field_name)
    if not is_plain_int(value) or value < 0:
        errors.append(f"{artifact_label} {field_name} must be a non-negative integer: {value!r}")
        return 0
    return value


def read_string_set_field(
    payload: dict[str, Any],
    *,
    field_name: str,
    artifact_label: str,
    errors: list[str],
    required: bool,
) -> set[str]:
    value = payload.get(field_name)
    if value is None:
        if required:
            errors.append(f"{artifact_label} {field_name} must be a list of non-empty strings")
        return set()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        errors.append(f"{artifact_label} {field_name} must be a list of non-empty strings: {value!r}")
        return set()
    return set(value)


def validate_page_results_match_sampled_cases(
    *,
    sampled_cases_path: Path | None,
    page_results: list[dict[str, Any]],
    aggregate: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    sampled_cases: dict[str, Any] = {}
    read_errors: list[str] = []
    if sampled_cases_path is None:
        errors.append("sampled_page_cases path missing")
    elif not sampled_cases_path.is_file():
        errors.append("sampled_page_cases artifact missing")
    else:
        sampled_cases, read_errors = read_json_object_if_exists(sampled_cases_path)
        errors.extend(read_errors)

    page_cases = sampled_cases.get("page_cases") if sampled_cases else []
    if sampled_cases and not isinstance(page_cases, list):
        errors.append("sampled_page_cases page_cases is not a list")
        page_cases = []

    expected_sequence: list[dict[str, Any]] = []
    malformed_sampled_cases: list[str] = []
    for index, case in enumerate(page_cases or []):
        if not isinstance(case, dict):
            malformed_sampled_cases.append(f"page_cases[{index}] is not an object")
            continue
        case_id = case.get("case_id")
        page_number = case.get("page_number")
        if not isinstance(case_id, str) or not case_id or not is_plain_int(page_number) or page_number < 1:
            malformed_sampled_cases.append(f"page_cases[{index}] missing case_id or integer page_number")
            continue
        expected_sequence.append({"case_id": case_id, "page_number": page_number})
    if malformed_sampled_cases:
        errors.extend(malformed_sampled_cases)

    observed_sequence: list[dict[str, Any]] = []
    malformed_observed_results: list[str] = []
    for index, result in enumerate(page_results):
        case_id = result.get("case_id")
        page_number = result.get("page_number")
        if not isinstance(case_id, str) or not case_id or not is_plain_int(page_number) or page_number < 1:
            malformed_observed_results.append(f"page_results[{index}] missing case_id or integer page_number")
        observed_sequence.append(
            {
                "case_id": case_id if isinstance(case_id, str) else "",
                "page_number": page_number,
            }
        )
    if malformed_observed_results:
        errors.extend(malformed_observed_results)
    observed_case_ids = [item["case_id"] for item in observed_sequence if item["case_id"]]
    duplicate_observed_case_ids = sorted(
        case_id
        for case_id, count in Counter(observed_case_ids).items()
        if count > 1
    )
    if duplicate_observed_case_ids:
        errors.append(f"duplicate observed page result case_ids: {duplicate_observed_case_ids}")

    expected_by_case_id = {item["case_id"]: item["page_number"] for item in expected_sequence}
    observed_by_case_id = {
        item["case_id"]: item["page_number"]
        for item in observed_sequence
        if item["case_id"]
    }
    extra_observed_case_ids = sorted(set(observed_by_case_id) - set(expected_by_case_id))
    if extra_observed_case_ids:
        errors.append(f"page results include cases not present in sampled_page_cases: {extra_observed_case_ids}")
    page_number_mismatches = sorted(
        {
            case_id: {
                "expected": expected_by_case_id[case_id],
                "observed": observed_by_case_id[case_id],
            }
            for case_id in set(observed_by_case_id) & set(expected_by_case_id)
            if observed_by_case_id[case_id] != expected_by_case_id[case_id]
        }.items()
    )
    if page_number_mismatches:
        errors.append(f"page result page_numbers do not match sampled_page_cases: {page_number_mismatches}")

    observed_prefix = observed_sequence[: len(expected_sequence)]
    expected_prefix = expected_sequence[: len(observed_prefix)]
    if observed_prefix != expected_prefix:
        errors.append(
            "page result sequence does not match sampled_page_cases prefix: "
            f"observed={observed_prefix}, expected_prefix={expected_prefix}"
        )

    missing_sampled_case_ids = [
        item["case_id"]
        for item in expected_sequence[len(observed_sequence):]
    ]
    if missing_sampled_case_ids and aggregate.get("ok") is True:
        errors.append(
            "green page aggregate cannot omit sampled page cases: "
            f"{missing_sampled_case_ids}"
        )

    return {
        "schema": "pdf_lab.second_pass.page_result_sample_match_validation.v1",
        "ok": not errors,
        "errors": errors,
        "sampled_cases_path": str(sampled_cases_path) if sampled_cases_path else None,
        "expected_case_count": len(expected_sequence),
        "observed_case_count": len(page_results),
        "expected_sequence": expected_sequence,
        "observed_sequence": observed_sequence,
        "missing_sampled_case_ids": missing_sampled_case_ids,
        "extra_observed_case_ids": extra_observed_case_ids,
        "duplicate_observed_case_ids": duplicate_observed_case_ids,
        "malformed_sampled_cases": malformed_sampled_cases,
        "malformed_observed_results": malformed_observed_results,
        "aggregate_ok": aggregate.get("ok") is True,
    }


def required_harness_review_bundle_page_artifacts(terminal_status: str | None) -> set[str]:
    required = set(BASE_PAGE_REVIEW_BUNDLE_ARTIFACTS)
    if terminal_status in RESOLVED_PASS_STATUSES:
        required.update(REQUIRED_PAGE_DAG_ARTIFACTS)
    if terminal_status == "patched_confirmed":
        required.update(REQUIRED_PATCHED_CONFIRMED_ARTIFACTS)
    return required


def optional_harness_review_bundle_page_artifacts(terminal_status: str | None) -> set[str]:
    optional = set(REQUIRED_PAGE_DAG_ARTIFACTS)
    optional.update(REQUIRED_PATCHED_CONFIRMED_ARTIFACTS)
    optional.update(OPTIONAL_PAGE_REVIEW_BUNDLE_ARTIFACTS)
    return optional - required_harness_review_bundle_page_artifacts(terminal_status)


def parse_mounted_workspace_prefixes(raw: str | None = None) -> list[Path]:
    source = raw if raw is not None else os.environ.get("SCILLM_MOUNTED_WORKSPACE_PREFIXES")
    values = source.split(":") if source else DEFAULT_SCILLM_MOUNTED_WORKSPACE_PREFIXES
    prefixes = [Path(value).expanduser().resolve() for value in values if value.strip()]
    if not prefixes:
        raise ValueError("at least one scillm mounted workspace prefix is required")
    return prefixes


def validate_scillm_live_code_root(
    *,
    code_root: Path,
    patch_mode: str,
    patch_backend: str,
    mounted_prefixes: list[Path],
    isolated_code_root_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    code_root = code_root.resolve()
    live_patch_required = patch_mode == "live" and patch_backend in {"opencode_serve", "scillm_orchestrator"}
    mounted_prefixes = [prefix.resolve() for prefix in mounted_prefixes]
    under_mounted_prefix = any(code_root == prefix or code_root.is_relative_to(prefix) for prefix in mounted_prefixes)
    isolated_marker = code_root / ".pdf_lab_isolated_code_root.json"
    isolated_marker_present = isolated_marker.is_file()
    isolated_marker_schema: str | None = None
    isolated_marker_dest_root: str | None = None
    isolated_marker_clean: bool | None = None
    errors: list[str] = []
    if isolated_marker_present:
        try:
            loaded_marker = json.loads(isolated_marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"isolated code root marker is unreadable JSON: {type(exc).__name__}: {exc}")
        else:
            if not isinstance(loaded_marker, dict):
                errors.append("isolated code root marker must be a JSON object")
            else:
                isolated_marker_schema = loaded_marker.get("schema") if isinstance(loaded_marker.get("schema"), str) else None
                dest_root_value = loaded_marker.get("dest_root")
                isolated_marker_dest_root = dest_root_value if isinstance(dest_root_value, str) else None
                clean_value = loaded_marker.get("clean")
                isolated_marker_clean = clean_value if isinstance(clean_value, bool) else None
    if live_patch_required and not under_mounted_prefix:
        errors.append(
            f"live scillm/OpenCode patch code_root must be under a mounted workspace prefix: {code_root}"
        )
    if live_patch_required and not isolated_marker_present:
        errors.append(
            f"live scillm/OpenCode patch code_root must be an isolated pdf-lab code root with marker: {isolated_marker}"
        )
    if live_patch_required and isolated_marker_present:
        if isolated_marker_schema != "pdf_lab.second_pass.isolated_code_root.v1":
            errors.append("isolated code root marker schema mismatch")
        if isolated_marker_dest_root is not None and Path(isolated_marker_dest_root).expanduser().resolve() != code_root:
            errors.append(f"isolated code root marker dest_root does not match code_root: {isolated_marker_dest_root}")
        if isolated_code_root_manifest is not None:
            manifest_dest_root = isolated_code_root_manifest.get("dest_root")
            if isinstance(manifest_dest_root, str) and Path(manifest_dest_root).expanduser().resolve() != code_root:
                errors.append(f"isolated code root manifest dest_root does not match code_root: {manifest_dest_root}")
            manifest_schema = isolated_code_root_manifest.get("schema")
            if manifest_schema != "pdf_lab.second_pass.isolated_code_root.v1":
                errors.append("isolated code root manifest schema mismatch")
    return {
        "schema": "pdf_lab.second_pass.scillm_code_root_visibility.v1",
        "code_root": str(code_root),
        "patch_mode": patch_mode,
        "patch_backend": patch_backend,
        "live_patch_required": live_patch_required,
        "mounted_workspace_prefixes": [str(prefix) for prefix in mounted_prefixes],
        "under_mounted_prefix": under_mounted_prefix,
        "isolated_code_root_required": live_patch_required,
        "isolated_marker_present": isolated_marker_present,
        "isolated_marker_schema": isolated_marker_schema,
        "isolated_marker_dest_root": isolated_marker_dest_root,
        "isolated_marker_clean": isolated_marker_clean,
        "isolated_code_root_manifest_present": isolated_code_root_manifest is not None,
        "ok": not errors,
        "errors": errors,
    }


def _import_pdf_lab_modules() -> tuple[Any, Any, Any]:
    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    import build_pdf_element_candidate_manifest as manifest_mod  # noqa: PLC0415
    import run_page_second_pass_dag as page_dag  # noqa: PLC0415
    import select_stratified_page_cases as sampler_mod  # noqa: PLC0415

    return manifest_mod, sampler_mod, page_dag


def prepare_code_root_if_requested(
    *,
    source_root: Path,
    dest_root: Path | None,
    include_paths: list[str] | None,
    force: bool,
) -> dict[str, Any] | None:
    if dest_root is None:
        return None
    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    import prepare_isolated_code_root as prepare_mod  # noqa: PLC0415

    return prepare_mod.prepare_isolated_code_root(
        source_root=source_root,
        dest_root=dest_root,
        include_paths=include_paths or prepare_mod.DEFAULT_INCLUDE_PATHS,
        force=force,
    )


def run_candidate_census(
    *,
    manifest_mod: Any,
    pdf_path: Path,
    ledger_path: Path | None,
    apply_mode: str,
    max_pages: int | None,
    debug_log: Path,
    timeout_s: float | None,
    page_timeout_s: float | None,
    progress_path: Path | None = None,
    page_numbers: list[int] | None = None,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    def _ensure_progress_artifacts(
        *,
        pages: list[dict[str, Any]],
        page_count: int,
        failures: list[dict[str, Any]],
    ) -> None:
        if progress_path is None or progress_path.exists():
            return
        if page_numbers:
            valid_page_numbers = sorted({page for page in page_numbers if 1 <= page <= page_count})
            limit = len(valid_page_numbers[:max_pages]) if max_pages else len(valid_page_numbers)
        else:
            limit = min(page_count, max_pages) if max_pages else page_count
        event = {
            "schema": "pdf_lab.second_pass.candidate_census_event.v1",
            "event": "completed",
            "created_at": utc_now(),
            "completed_pages": len(pages),
            "failed_pages": len(failures),
            "limit": limit,
            "legacy_progress_synthesized": True,
        }
        write_json(
            progress_path,
            {
                "schema": "pdf_lab.second_pass.candidate_census_progress.v1",
                "updated_at": utc_now(),
                "pdf_path": str(pdf_path),
                "page_count": page_count,
                "limit": limit,
                "completed_pages": len(pages),
                "failed_pages": len(failures),
                "remaining_pages": max(0, limit - len(pages) - len(failures)),
                "current_page_number": None,
                "status": "completed",
                "last_event": event,
                "legacy_progress_synthesized": True,
            },
        )
        events_path = progress_path.with_name("candidate_census_events.jsonl")
        events_path.write_text(json.dumps(event, sort_keys=True) + "\n", encoding="utf-8")

    def _handle_timeout(signum, frame):  # noqa: ARG001 - signal handler contract.
        raise CandidateCensusTimeout(f"candidate census exceeded timeout_s={timeout_s}")

    previous_handler = None
    timeout_enabled = timeout_s is not None and timeout_s > 0 and not page_timeout_s
    if timeout_enabled:
        previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, _handle_timeout)
        signal.setitimer(signal.ITIMER_REAL, float(timeout_s))
    try:
        with debug_log.open("w", encoding="utf-8") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            if hasattr(manifest_mod, "extract_pages_with_failures"):
                kwargs = {"page_timeout_s": page_timeout_s}
                try:
                    signature = inspect.signature(manifest_mod.extract_pages_with_failures)
                except (TypeError, ValueError):
                    signature = None
                if signature is None or "progress_path" in signature.parameters:
                    kwargs["progress_path"] = progress_path
                if signature is None or "page_numbers" in signature.parameters:
                    kwargs["page_numbers"] = page_numbers
                pages, page_count, failures = manifest_mod.extract_pages_with_failures(
                    pdf_path,
                    ledger_path,
                    apply_mode,
                    max_pages,
                    **kwargs,
                )
                _ensure_progress_artifacts(pages=pages, page_count=page_count, failures=failures)
                return pages, page_count, failures
            pages, page_count = manifest_mod.extract_pages(pdf_path, ledger_path, apply_mode, max_pages)
            failures: list[dict[str, Any]] = []
            _ensure_progress_artifacts(pages=pages, page_count=page_count, failures=failures)
            return pages, page_count, failures
    finally:
        if timeout_enabled:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)


def candidate_census_failure_aggregate(reason: str) -> dict[str, Any]:
    return {
        "status_counts": {},
        "nonterminal_count": 0,
        "nonterminal_cases": [],
        "unresolved_count": 0,
        "unresolved_cases": [],
        "commit_shas": [],
        "patched_confirmed_count": 0,
        "patched_without_commit_count": 0,
        "patched_missing_commit_gate_artifacts_count": 0,
        "patched_missing_commit_gate_artifacts_cases": [],
        "duplicate_commit_shas": [],
        "ok": False,
        "errors": [reason],
    }


def aggregate_page_results(page_results: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(result.get("terminal_status") or "missing" for result in page_results)
    case_ids = [
        result.get("case_id")
        for result in page_results
        if isinstance(result.get("case_id"), str) and result.get("case_id")
    ]
    page_numbers = [
        result.get("page_number")
        for result in page_results
        if is_plain_int(result.get("page_number")) and result.get("page_number") >= 1
    ]
    missing_case_id_cases = [
        result
        for result in page_results
        if not isinstance(result.get("case_id"), str) or not result.get("case_id")
    ]
    missing_page_number_cases = [
        result
        for result in page_results
        if not is_plain_int(result.get("page_number")) or result.get("page_number") < 1
    ]
    duplicate_case_ids = sorted(
        case_id
        for case_id, count in Counter(case_ids).items()
        if case_id and count > 1
    )
    duplicate_page_numbers = sorted(
        page_number
        for page_number, count in Counter(page_numbers).items()
        if count > 1
    )
    malformed_case_ids: list[str] = []
    case_id_page_suffix_mismatches: list[str] = []
    for result in page_results:
        case_id = result.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            continue
        case_id_match = PAGE_CASE_ID_RE.fullmatch(case_id)
        if case_id_match is None:
            malformed_case_ids.append(case_id)
            continue
        page_number = result.get("page_number")
        if is_plain_int(page_number) and int(case_id_match.group("page_number")) != page_number:
            case_id_page_suffix_mismatches.append(case_id)
    raw_identity_mismatch_cases = [
        result
        for result in page_results
        if result.get("raw_result_identity_mismatch_errors")
    ]
    nonterminal = [
        result
        for result in page_results
        if result.get("terminal_status") not in TERMINAL_PAGE_STATUSES
    ]
    unresolved = [
        result
        for result in page_results
        if result.get("terminal_status") not in RESOLVED_PASS_STATUSES
    ]
    commit_shas = [
        result.get("commit_sha")
        for result in page_results
        if result.get("terminal_status") == "patched_confirmed"
        and isinstance(result.get("commit_sha"), str)
        and result.get("commit_sha")
    ]
    patched_without_commit = [
        result
        for result in page_results
        if result.get("terminal_status") == "patched_confirmed"
        and (not isinstance(result.get("commit_sha"), str) or not result.get("commit_sha"))
    ]
    patched_missing_commit_gate_artifacts = [
        result
        for result in page_results
        if result.get("terminal_status") == "patched_confirmed"
        and (
            "commit_acceptance_gate.json" not in (result.get("evidence_artifacts") or [])
            or "commit_gate.json" not in (result.get("evidence_artifacts") or [])
            or "revertability_check.json" not in (result.get("evidence_artifacts") or [])
        )
    ]
    duplicate_commit_shas = sorted(
        sha
        for sha, count in Counter(commit_shas).items()
        if sha and count > 1
    )
    scillm_patch_delegate_bug_reports = [
        result
        for result in page_results
        if result.get("scillm_patch_delegate_bug_report")
    ]
    errors = []
    if nonterminal:
        errors.append(f"invalid or missing terminal page statuses: {[item.get('case_id') for item in nonterminal]}")
    if unresolved:
        errors.append(f"unresolved page cases remain: {[item.get('case_id') for item in unresolved]}")
    if patched_without_commit:
        errors.append(f"patched_confirmed missing commit SHA: {[item.get('case_id') for item in patched_without_commit]}")
    if patched_missing_commit_gate_artifacts:
        errors.append(
            "patched_confirmed missing commit_acceptance_gate.json, commit_gate.json, or revertability_check.json evidence: "
            f"{[item.get('case_id') for item in patched_missing_commit_gate_artifacts]}"
        )
    if duplicate_commit_shas:
        errors.append(f"patched_confirmed commit SHAs are not one-commit-per-page unique: {duplicate_commit_shas}")
    if missing_case_id_cases:
        errors.append(f"page results missing case_id: {[item.get('page_number') for item in missing_case_id_cases]}")
    if missing_page_number_cases:
        errors.append(f"page results missing integer page_number: {[item.get('case_id') for item in missing_page_number_cases]}")
    if duplicate_case_ids:
        errors.append(f"duplicate page result case_ids: {duplicate_case_ids}")
    if duplicate_page_numbers:
        errors.append(f"duplicate page result page_numbers: {duplicate_page_numbers}")
    if malformed_case_ids:
        errors.append(f"malformed page result case_ids: {sorted(malformed_case_ids)}")
    if case_id_page_suffix_mismatches:
        errors.append(
            "page result case_id page suffixes do not match page_number: "
            f"{sorted(case_id_page_suffix_mismatches)}"
        )
    if raw_identity_mismatch_cases:
        errors.append(
            "raw page result identity mismatches: "
            f"{[(item.get('case_id'), item.get('raw_result_identity_mismatch_errors')) for item in raw_identity_mismatch_cases]}"
        )
    return {
        "status_counts": dict(sorted(status_counts.items())),
        "case_ids": case_ids,
        "page_numbers": page_numbers,
        "missing_case_id_count": len(missing_case_id_cases),
        "missing_case_id_cases": missing_case_id_cases,
        "missing_page_number_count": len(missing_page_number_cases),
        "missing_page_number_cases": missing_page_number_cases,
        "duplicate_case_ids": duplicate_case_ids,
        "duplicate_page_numbers": duplicate_page_numbers,
        "malformed_case_ids": sorted(malformed_case_ids),
        "case_id_page_suffix_mismatches": sorted(case_id_page_suffix_mismatches),
        "raw_identity_mismatch_count": len(raw_identity_mismatch_cases),
        "raw_identity_mismatch_cases": raw_identity_mismatch_cases,
        "nonterminal_count": len(nonterminal),
        "nonterminal_cases": nonterminal,
        "unresolved_count": len(unresolved),
        "unresolved_cases": unresolved,
        "commit_shas": commit_shas,
        "patched_confirmed_count": status_counts.get("patched_confirmed", 0),
        "patched_without_commit_count": len(patched_without_commit),
        "patched_missing_commit_gate_artifacts_count": len(patched_missing_commit_gate_artifacts),
        "patched_missing_commit_gate_artifacts_cases": patched_missing_commit_gate_artifacts,
        "duplicate_commit_shas": duplicate_commit_shas,
        "scillm_patch_delegate_bug_report_count": len(scillm_patch_delegate_bug_reports),
        "scillm_patch_delegate_bug_report_cases": scillm_patch_delegate_bug_reports,
        "ok": not errors,
        "errors": errors,
    }


def validate_sampling_gate(
    *,
    manifest: dict[str, Any],
    sampled_cases: dict[str, Any],
) -> dict[str, Any]:
    audit = sampled_cases.get("sampling_audit")
    forced_pages = sampled_cases.get("forced_pages")
    sampled_seed = sampled_cases.get("seed")
    accepted_forced_pages = []
    errors: list[str] = []
    candidate_count = read_non_negative_int_field(
        manifest,
        field_name="candidate_count",
        artifact_label="candidate manifest",
        errors=errors,
    )
    selected_count = read_non_negative_int_field(
        sampled_cases,
        field_name="selected_count",
        artifact_label="sampled_page_cases",
        errors=errors,
    )
    if isinstance(forced_pages, dict):
        accepted_forced_pages = forced_pages.get("accepted") or []
    if not isinstance(accepted_forced_pages, list) or not all(is_plain_int(page) and page >= 1 for page in accepted_forced_pages):
        errors.append("sampled_page_cases forced_pages.accepted must be a list of page numbers")
        accepted_forced_pages = []
    probabilistic_selected_pages = sampled_cases.get("probabilistic_selected_pages")
    if not isinstance(audit, dict):
        errors.append("sampled_page_cases missing sampling_audit")
        audit = {}
    statistical_basis = audit.get("statistical_significance_basis") if isinstance(audit, dict) else None
    if candidate_count > 0 and audit.get("schema") != "pdf_lab.second_pass.sampling_audit.v1":
        errors.append(f"sampling_audit schema mismatch: {audit.get('schema')}")
    if candidate_count > 0 and not isinstance(statistical_basis, dict):
        errors.append("sampling_audit missing statistical_significance_basis")
        statistical_basis = {}
    if candidate_count > 0 and not is_plain_int(sampled_seed):
        errors.append("sampled_page_cases seed must be an integer")
    if candidate_count > 0 and is_plain_int(sampled_seed):
        audit_seed = audit.get("seed")
        statistical_basis_seed = statistical_basis.get("seed")
        if not is_plain_int(audit_seed):
            errors.append(f"sampling_audit seed must be an integer: {audit_seed!r}")
        elif audit_seed != sampled_seed:
            errors.append("sampling_audit seed does not match sampled_page_cases seed")
        if not is_plain_int(statistical_basis_seed):
            errors.append(f"sampling_audit statistical_significance_basis seed must be an integer: {statistical_basis_seed!r}")
        elif statistical_basis_seed != sampled_seed:
            errors.append("sampling_audit statistical_significance_basis seed does not match sampled_page_cases seed")
    if candidate_count > 0 and selected_count <= 0:
        errors.append("candidate manifest has candidates but selected_count is zero")
    if candidate_count > 0 and audit.get("adequate_sample_size") is not True:
        errors.append("sampling_audit adequate_sample_size is not true")
    if candidate_count > 0 and audit.get("adequate_for_priority_strata") is not True:
        errors.append("sampling_audit adequate_for_priority_strata is not true")
    if candidate_count > 0 and statistical_basis.get("adequate") is not True:
        errors.append("sampling_audit statistical_significance_basis.adequate is not true")
    if (
        candidate_count > 0
        and statistical_basis
        and statistical_basis.get("recommended_min_sample_size") != audit.get("recommended_min_sample_size")
    ):
        errors.append("sampling_audit statistical_significance_basis recommended_min_sample_size mismatch")
    audit_selected_count = audit.get("selected_count")
    if candidate_count > 0 and audit_selected_count is not None:
        if not is_plain_int(audit_selected_count) or audit_selected_count < 0:
            errors.append(f"sampling_audit selected_count must be a non-negative integer: {audit_selected_count!r}")
        elif audit_selected_count != selected_count:
            errors.append("sampling_audit selected_count does not match sampled_page_cases selected_count")
    if candidate_count > 0 and accepted_forced_pages:
        if audit.get("forced_pages_are_additive") is not True:
            errors.append("sampling_audit forced_pages_are_additive is not true")
        if statistical_basis.get("forced_pages_are_additive") is not True:
            errors.append("sampling_audit statistical_significance_basis forced_pages_are_additive is not true")
        if not isinstance(probabilistic_selected_pages, list) or not all(
            is_plain_int(page) and page >= 1 for page in probabilistic_selected_pages
        ):
            errors.append("sampled_page_cases probabilistic_selected_pages must be a list of page numbers when forced pages are accepted")
            probabilistic_selected_pages = []
        if set(probabilistic_selected_pages or []) & set(accepted_forced_pages):
            errors.append("probabilistic_selected_pages overlaps accepted forced pages")
        probabilistic_count = len(probabilistic_selected_pages or [])
        if selected_count != probabilistic_count + len(accepted_forced_pages):
            errors.append("selected_count does not equal probabilistic_selected_pages plus accepted forced pages")
        if audit.get("probabilistic_selected_count") != probabilistic_count:
            errors.append("sampling_audit probabilistic_selected_count mismatch")
        if statistical_basis.get("probabilistic_selected_page_count") != probabilistic_count:
            errors.append("sampling_audit statistical_significance_basis probabilistic_selected_page_count mismatch")
        if statistical_basis.get("accepted_forced_page_count") != len(accepted_forced_pages):
            errors.append("sampling_audit statistical_significance_basis accepted_forced_page_count mismatch")
    missed_priority = audit.get("missed_priority_strata") if isinstance(audit, dict) else None
    if candidate_count > 0 and missed_priority:
        errors.append(f"sampling_audit missed priority strata: {missed_priority}")
    return {
        "schema": "pdf_lab.second_pass.sampling_gate.v1",
        "candidate_count": candidate_count,
        "selected_count": selected_count,
        "accepted_forced_page_count": len(accepted_forced_pages),
        "seed": sampled_seed,
        "sampling_audit_seed": audit.get("seed") if isinstance(audit, dict) else None,
        "statistical_significance_seed": statistical_basis.get("seed") if isinstance(statistical_basis, dict) else None,
        "probabilistic_selected_count": (
            len(probabilistic_selected_pages)
            if isinstance(probabilistic_selected_pages, list)
            else audit.get("probabilistic_selected_count")
            if isinstance(audit, dict)
            else None
        ),
        "forced_pages_are_additive": audit.get("forced_pages_are_additive") if isinstance(audit, dict) else None,
        "sampling_audit_schema": audit.get("schema") if isinstance(audit, dict) else None,
        "adequate_sample_size": audit.get("adequate_sample_size") if isinstance(audit, dict) else None,
        "adequate_for_priority_strata": audit.get("adequate_for_priority_strata") if isinstance(audit, dict) else None,
        "statistical_significance_adequate": statistical_basis.get("adequate") if isinstance(statistical_basis, dict) else None,
        "recommended_min_sample_size": audit.get("recommended_min_sample_size") if isinstance(audit, dict) else None,
        "statistical_significance_basis": statistical_basis if isinstance(statistical_basis, dict) else None,
        "covered_priority_strata": audit.get("covered_priority_strata", []) if isinstance(audit, dict) else [],
        "missed_priority_strata": missed_priority or [],
        "warnings": audit.get("warnings", []) if isinstance(audit, dict) else [],
        "ok": not errors,
        "errors": errors,
    }


def validate_candidate_manifest_integrity(manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if manifest.get("schema") != "pdf_lab.second_pass.candidate_manifest.v1":
        errors.append(f"candidate manifest schema is not pdf_lab.second_pass.candidate_manifest.v1: {manifest.get('schema')}")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        errors.append("candidate manifest candidates is not a list")
        candidates = []
    pages = manifest.get("pages")
    if not isinstance(pages, list):
        errors.append("candidate manifest pages is not a list")
        pages = []
    declared_candidate_count = read_non_negative_int_field(
        manifest,
        field_name="candidate_count",
        artifact_label="candidate manifest",
        errors=errors,
    )
    declared_page_count = manifest.get("page_count")
    if declared_page_count is not None and (not is_plain_int(declared_page_count) or declared_page_count < 1):
        errors.append(f"candidate manifest page_count must be a positive integer when present: {declared_page_count!r}")
    if declared_candidate_count != len(candidates):
        errors.append(f"candidate_count {declared_candidate_count} does not equal candidates length {len(candidates)}")
    preset_types = read_string_set_field(
        manifest,
        field_name="preset_types",
        artifact_label="candidate manifest",
        errors=errors,
        required=True,
    )
    if not preset_types:
        errors.append("candidate manifest preset_types is empty")
    candidate_ids: set[str] = set()
    duplicate_candidate_ids: list[str] = []
    preset_counts: Counter[str] = Counter()
    page_candidate_counts: Counter[int] = Counter()
    page_preset_counts: dict[int, Counter[str]] = {}
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            errors.append(f"candidate at index {index} is not an object")
            continue
        candidate_label = str(candidate.get("candidate_id") or f"candidate[{index}]")
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id:
            errors.append(f"candidate at index {index} missing candidate_id")
        elif candidate_id in candidate_ids:
            duplicate_candidate_ids.append(candidate_id)
        else:
            candidate_ids.add(candidate_id)
        page_number = candidate.get("page_number")
        if not is_plain_int(page_number) or page_number < 1:
            errors.append(f"{candidate_label} missing valid page_number")
        elif is_plain_int(declared_page_count) and page_number > declared_page_count:
            errors.append(f"{candidate_label} page_number {page_number} exceeds manifest page_count {declared_page_count}")
        page_index = candidate.get("page_index")
        if not is_plain_int(page_index) or (is_plain_int(page_number) and page_number >= 1 and page_index != page_number - 1):
            errors.append(f"{candidate_label} page_index does not match page_number - 1")
        preset_type = str(candidate.get("preset_type") or "")
        if not preset_type:
            errors.append(f"{candidate_label} missing preset_type")
        elif preset_types and preset_type not in preset_types:
            errors.append(f"{candidate_label}: preset_type {preset_type!r} is not in manifest preset_types")
        bbox = candidate.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4 or not all(is_plain_number(value) for value in bbox):
            errors.append(f"{candidate_label} missing numeric bbox[4]")
        elif not all(math.isfinite(float(value)) for value in bbox):
            errors.append(f"{candidate_label} bbox contains non-finite values")
        elif any(float(value) < 0.0 or float(value) > 1.0 for value in bbox):
            errors.append(f"{candidate_label} bbox values must be normalized to [0, 1]")
        elif float(bbox[0]) > float(bbox[2]) or float(bbox[1]) > float(bbox[3]):
            errors.append(f"{candidate_label} bbox coordinates are not ordered [x0, y0, x1, y1]")
        if not str(candidate.get("json_pointer") or ""):
            errors.append(f"{candidate_label} missing json_pointer")
        block_index = candidate.get("block_index")
        if not is_plain_int(block_index) or block_index < 0:
            errors.append(f"{candidate_label} missing valid block_index")
        if is_plain_int(page_number) and page_number >= 1 and preset_type:
            page_candidate_counts[page_number] += 1
            page_preset_counts.setdefault(page_number, Counter())[preset_type] += 1
        if preset_type:
            preset_counts[preset_type] += 1
    if duplicate_candidate_ids:
        errors.append(f"duplicate candidate_ids: {sorted(duplicate_candidate_ids)}")
    declared_preset_counts = manifest.get("preset_counts")
    if not isinstance(declared_preset_counts, dict):
        errors.append("candidate manifest preset_counts is not an object")
    elif declared_preset_counts != dict(sorted(preset_counts.items())):
        errors.append(
            f"manifest preset_counts do not match candidates: "
            f"declared={declared_preset_counts}, expected={dict(sorted(preset_counts.items()))}"
        )
    page_summary_numbers: set[int] = set()
    for index, page_summary in enumerate(pages):
        if not isinstance(page_summary, dict):
            errors.append(f"page summary at index {index} is not an object")
            continue
        page_number = page_summary.get("page_number")
        if not is_plain_int(page_number) or page_number < 1:
            errors.append(f"page summary at index {index} missing valid page_number")
            continue
        page_summary_numbers.add(page_number)
        expected_count = page_candidate_counts.get(page_number, 0)
        declared_page_candidate_count = page_summary.get("candidate_count")
        if not is_plain_int(declared_page_candidate_count) or declared_page_candidate_count < 0:
            errors.append(
                f"page {page_number} candidate_count must be a non-negative integer: {declared_page_candidate_count!r}"
            )
        elif declared_page_candidate_count != expected_count:
            errors.append(
                f"page {page_number} candidate_count does not match candidates: "
                f"declared={declared_page_candidate_count}, expected={expected_count}"
            )
        expected_preset_counts = dict(sorted(page_preset_counts.get(page_number, Counter()).items()))
        if page_summary.get("preset_counts") != expected_preset_counts:
            errors.append(
                f"page {page_number} preset_counts do not match candidates: "
                f"declared={page_summary.get('preset_counts')}, expected={expected_preset_counts}"
            )
    missing_page_summaries = sorted(set(page_candidate_counts) - page_summary_numbers)
    if missing_page_summaries:
        errors.append(f"candidate pages missing page summaries: {missing_page_summaries}")
    return {
        "schema": "pdf_lab.second_pass.candidate_manifest_integrity_validation.v1",
        "ok": not errors,
        "errors": errors,
        "candidate_count": declared_candidate_count,
        "actual_candidate_count": len(candidates),
        "unique_candidate_id_count": len(candidate_ids),
        "preset_counts": dict(sorted(preset_counts.items())),
        "page_count_with_candidates": len(page_candidate_counts),
    }


def validate_candidate_sample_linkage(
    *,
    manifest: dict[str, Any],
    sampled_cases: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    if manifest.get("schema") != "pdf_lab.second_pass.candidate_manifest.v1":
        errors.append(f"candidate manifest schema is not pdf_lab.second_pass.candidate_manifest.v1: {manifest.get('schema')}")
    if sampled_cases.get("schema") != "pdf_lab.second_pass.sampled_page_cases.v1":
        errors.append(
            f"sampled page cases schema is not pdf_lab.second_pass.sampled_page_cases.v1: {sampled_cases.get('schema')}"
        )

    candidates = manifest.get("candidates")
    if not isinstance(candidates, list):
        errors.append("candidate manifest candidates is not a list")
        candidates = []
    page_cases = sampled_cases.get("page_cases")
    if not isinstance(page_cases, list):
        errors.append("sampled page cases page_cases is not a list")
        page_cases = []

    declared_candidate_count = read_non_negative_int_field(
        manifest,
        field_name="candidate_count",
        artifact_label="candidate manifest",
        errors=errors,
    )
    if declared_candidate_count != len(candidates):
        errors.append(f"candidate_count {declared_candidate_count} does not equal candidates length {len(candidates)}")

    declared_selected_count = read_non_negative_int_field(
        sampled_cases,
        field_name="selected_count",
        artifact_label="sampled_page_cases",
        errors=errors,
    )
    if declared_selected_count != len(page_cases):
        errors.append(f"selected_count {declared_selected_count} does not equal page_cases length {len(page_cases)}")

    preset_types = read_string_set_field(
        manifest,
        field_name="preset_types",
        artifact_label="candidate manifest",
        errors=errors,
        required=False,
    )
    if not preset_types:
        preset_types = {
            "appendix",
            "equation",
            "figure",
            "footnote",
            "list",
            "reference",
            "section_heading",
            "side_chrome",
            "table",
            "text",
            "toc",
            "unknown_layout",
        }

    candidate_by_id: dict[str, dict[str, Any]] = {}
    candidate_ids_by_page: dict[int, set[str]] = {}
    preset_counts_by_page: dict[int, Counter[str]] = {}
    duplicate_candidate_ids: list[str] = []
    manifest_pages: set[int] = set()
    manifest_preset_counts: Counter[str] = Counter()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            errors.append(f"candidate at index {index} is not an object")
            continue
        candidate_id = str(candidate.get("candidate_id") or "")
        if not candidate_id:
            errors.append(f"candidate at index {index} missing candidate_id")
        elif candidate_id in candidate_by_id:
            duplicate_candidate_ids.append(candidate_id)
        else:
            candidate_by_id[candidate_id] = candidate

        page_number = candidate.get("page_number")
        if not is_plain_int(page_number) or page_number < 1:
            errors.append(f"{candidate_id or f'candidate[{index}]'} missing valid page_number")
        else:
            manifest_pages.add(page_number)
            if candidate_id:
                candidate_ids_by_page.setdefault(page_number, set()).add(candidate_id)

        preset_type = str(candidate.get("preset_type") or "")
        if not preset_type:
            errors.append(f"{candidate_id or f'candidate[{index}]'} missing preset_type")
        elif preset_type not in preset_types:
            errors.append(f"{candidate_id}: preset_type {preset_type!r} is not in manifest preset_types")
        else:
            manifest_preset_counts[preset_type] += 1
            if is_plain_int(page_number) and page_number >= 1:
                preset_counts_by_page.setdefault(page_number, Counter())[preset_type] += 1

        bbox = candidate.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4 or not all(is_plain_number(value) for value in bbox):
            errors.append(f"{candidate_id or f'candidate[{index}]'} missing numeric bbox[4]")
        if not str(candidate.get("json_pointer") or ""):
            errors.append(f"{candidate_id or f'candidate[{index}]'} missing json_pointer")

    if duplicate_candidate_ids:
        errors.append(f"duplicate candidate_ids: {sorted(duplicate_candidate_ids)}")

    selected_pages = sampled_cases.get("selected_pages")
    if not isinstance(selected_pages, list):
        errors.append("sampled page cases selected_pages is not a list")
        selected_pages = []
    elif not all(is_plain_int(page) and page >= 1 for page in selected_pages):
        errors.append("sampled page cases selected_pages must be a list of page numbers")
    duplicate_selected_pages = sorted(
        page
        for page, count in Counter(page for page in selected_pages if is_plain_int(page) and page >= 1).items()
        if count > 1
    )
    if duplicate_selected_pages:
        errors.append(f"sampled page cases selected_pages contains duplicates: {duplicate_selected_pages}")
    selected_page_set = {page for page in selected_pages if is_plain_int(page) and page >= 1}
    forced_pages = sampled_cases.get("forced_pages")
    accepted_forced_pages: list[int] = []
    if isinstance(forced_pages, dict):
        raw_accepted_forced_pages = forced_pages.get("accepted") or []
        if not isinstance(raw_accepted_forced_pages, list) or not all(
            is_plain_int(page) and page >= 1 for page in raw_accepted_forced_pages
        ):
            errors.append("sampled page cases forced_pages.accepted must be a list of page numbers")
        else:
            accepted_forced_pages = sorted(set(raw_accepted_forced_pages))
    elif forced_pages is not None:
        errors.append("sampled page cases forced_pages is not an object")
    probabilistic_selected_pages: list[int] | None = None
    raw_probabilistic_selected_pages = sampled_cases.get("probabilistic_selected_pages")
    if raw_probabilistic_selected_pages is not None:
        if not isinstance(raw_probabilistic_selected_pages, list) or not all(
            is_plain_int(page) and page >= 1 for page in raw_probabilistic_selected_pages
        ):
            errors.append("sampled page cases probabilistic_selected_pages must be a list of page numbers")
        else:
            probabilistic_selected_pages = sorted(set(raw_probabilistic_selected_pages))
    page_case_pages: set[int] = set()
    page_case_page_counts: Counter[int] = Counter()
    page_case_ids: set[str] = set()
    duplicate_page_case_ids: list[str] = []
    forced_page_case_pages: set[int] = set()
    malformed_sampling_metadata_case_ids: list[str] = []
    malformed_forced_probability_case_ids: list[str] = []
    sampled_candidate_ids: set[str] = set()
    unknown_sampled_candidate_ids: list[str] = []
    page_mismatch_candidate_ids: list[str] = []
    empty_page_cases: list[str] = []
    for index, case in enumerate(page_cases):
        if not isinstance(case, dict):
            errors.append(f"page_case at index {index} is not an object")
            continue
        case_id = str(case.get("case_id") or f"page_case[{index}]")
        case_id_match = PAGE_CASE_ID_RE.fullmatch(case_id)
        if case_id_match is None:
            errors.append(f"{case_id} case_id must match page_case_####_p####")
        if case_id in page_case_ids:
            duplicate_page_case_ids.append(case_id)
        else:
            page_case_ids.add(case_id)
        page_number = case.get("page_number")
        if not is_plain_int(page_number) or page_number < 1:
            errors.append(f"{case_id} missing valid page_number")
        else:
            page_case_pages.add(page_number)
            page_case_page_counts[page_number] += 1
            if page_number not in selected_page_set:
                errors.append(f"{case_id} page_number {page_number} is not in selected_pages")
            if case_id_match is not None and int(case_id_match.group("page_number")) != page_number:
                errors.append(f"{case_id} case_id page suffix does not match page_number {page_number}")
            if case.get("forced_by_human_annotation") is True:
                forced_page_case_pages.add(page_number)
        case_candidate_ids = case.get("candidate_ids")
        if not isinstance(case_candidate_ids, list):
            errors.append(f"{case_id} candidate_ids is not a list")
            case_candidate_ids = []
        elif not all(isinstance(candidate_id, str) and candidate_id for candidate_id in case_candidate_ids):
            errors.append(f"{case_id} candidate_ids must be a list of non-empty strings")
        if not case_candidate_ids:
            empty_page_cases.append(case_id)
        duplicate_case_candidate_ids = sorted(
            candidate_id
            for candidate_id, count in Counter(candidate_id for candidate_id in case_candidate_ids if isinstance(candidate_id, str)).items()
            if count > 1
        )
        if duplicate_case_candidate_ids:
            errors.append(f"{case_id} candidate_ids contains duplicates: {duplicate_case_candidate_ids}")
        case_candidate_id_set = {str(candidate_id) for candidate_id in case_candidate_ids}
        preset_counts = case.get("preset_counts")
        strata = case.get("strata")
        selection_reason = case.get("selection_reason")
        selection_probability_estimate = case.get("selection_probability_estimate")
        selection_probability_basis = case.get("selection_probability_basis")
        forced_by_human_annotation = case.get("forced_by_human_annotation")
        if not isinstance(preset_counts, dict):
            malformed_sampling_metadata_case_ids.append(case_id)
        if not isinstance(strata, list) or not strata or not all(isinstance(stratum, str) and stratum for stratum in strata):
            malformed_sampling_metadata_case_ids.append(case_id)
        if not isinstance(selection_reason, list) or not selection_reason or not all(
            isinstance(reason, str) and reason for reason in selection_reason
        ):
            malformed_sampling_metadata_case_ids.append(case_id)
        if forced_by_human_annotation not in {True, False}:
            malformed_sampling_metadata_case_ids.append(case_id)
        if not is_plain_number(selection_probability_estimate) or not 0 <= float(selection_probability_estimate) <= 1:
            malformed_sampling_metadata_case_ids.append(case_id)
        if not isinstance(selection_probability_basis, dict):
            malformed_sampling_metadata_case_ids.append(case_id)
        if forced_by_human_annotation is True and (
            selection_probability_estimate != 1.0
            or not isinstance(selection_probability_basis, dict)
            or selection_probability_basis.get("method") != "forced_human_annotation"
            or selection_probability_basis.get("forced_page") is not True
        ):
            malformed_forced_probability_case_ids.append(case_id)
        for candidate_id in case_candidate_ids:
            candidate_id = str(candidate_id)
            sampled_candidate_ids.add(candidate_id)
            candidate = candidate_by_id.get(candidate_id)
            if candidate is None:
                unknown_sampled_candidate_ids.append(candidate_id)
                continue
            candidate_page_number = candidate.get("page_number")
            if is_plain_int(page_number) and (not is_plain_int(candidate_page_number) or candidate_page_number != page_number):
                page_mismatch_candidate_ids.append(candidate_id)
        if is_plain_int(page_number) and page_number >= 1:
            expected_candidate_ids = candidate_ids_by_page.get(page_number, set())
            missing_page_candidate_ids = sorted(expected_candidate_ids - case_candidate_id_set)
            extra_page_candidate_ids = sorted(case_candidate_id_set - expected_candidate_ids)
            if missing_page_candidate_ids:
                errors.append(f"{case_id} missing manifest candidate_ids for selected page: {missing_page_candidate_ids}")
            if extra_page_candidate_ids:
                errors.append(f"{case_id} includes candidate_ids not assigned to selected page: {extra_page_candidate_ids}")
            expected_preset_counts = dict(sorted(preset_counts_by_page.get(page_number, Counter()).items()))
            declared_preset_counts = case.get("preset_counts")
            if declared_preset_counts is not None and declared_preset_counts != expected_preset_counts:
                errors.append(
                    f"{case_id} preset_counts do not match manifest candidates for selected page: "
                    f"declared={declared_preset_counts}, expected={expected_preset_counts}"
                )

    if selected_page_set != page_case_pages:
        errors.append(
            f"selected_pages do not match page_case page_numbers: selected={sorted(selected_page_set)}, cases={sorted(page_case_pages)}"
        )
    duplicate_page_case_pages = sorted(page for page, count in page_case_page_counts.items() if count > 1)
    if duplicate_page_case_ids:
        errors.append(f"sampled page cases page_cases contains duplicate case_ids: {sorted(duplicate_page_case_ids)}")
    if duplicate_page_case_pages:
        errors.append(f"sampled page cases page_cases contains duplicate page_numbers: {duplicate_page_case_pages}")
    if accepted_forced_pages:
        accepted_forced_page_set = set(accepted_forced_pages)
        missing_forced_selected = sorted(accepted_forced_page_set - selected_page_set)
        if missing_forced_selected:
            errors.append(f"accepted forced pages are not selected: {missing_forced_selected}")
        if forced_page_case_pages != accepted_forced_page_set:
            errors.append(
                f"forced page case flags do not match accepted forced pages: "
                f"flags={sorted(forced_page_case_pages)}, accepted={accepted_forced_pages}"
            )
        if probabilistic_selected_pages is None:
            errors.append("probabilistic_selected_pages required when forced pages are accepted")
        else:
            probabilistic_selected_page_set = set(probabilistic_selected_pages)
            if probabilistic_selected_page_set & accepted_forced_page_set:
                errors.append("probabilistic_selected_pages overlap accepted forced pages")
            if not probabilistic_selected_page_set <= selected_page_set:
                errors.append(
                    f"probabilistic_selected_pages include pages outside selected_pages: "
                    f"{sorted(probabilistic_selected_page_set - selected_page_set)}"
                )
            expected_selected_page_set = probabilistic_selected_page_set | accepted_forced_page_set
            if expected_selected_page_set != selected_page_set:
                errors.append(
                    f"selected_pages do not equal probabilistic_selected_pages plus accepted forced pages: "
                    f"selected={sorted(selected_page_set)}, expected={sorted(expected_selected_page_set)}"
                )
    if empty_page_cases:
        errors.append(f"page cases without candidate_ids: {sorted(empty_page_cases)}")
    if unknown_sampled_candidate_ids:
        errors.append(f"sampled candidate_ids missing from manifest: {sorted(set(unknown_sampled_candidate_ids))}")
    if page_mismatch_candidate_ids:
        errors.append(f"sampled candidate_ids do not belong to their page_case page_number: {sorted(set(page_mismatch_candidate_ids))}")
    if malformed_sampling_metadata_case_ids:
        errors.append(f"sampled page cases have malformed sampling metadata: {sorted(set(malformed_sampling_metadata_case_ids))}")
    if malformed_forced_probability_case_ids:
        errors.append(
            "forced sampled page cases missing forced_human_annotation probability basis: "
            f"{sorted(set(malformed_forced_probability_case_ids))}"
        )

    unsampled_manifest_pages = sorted(manifest_pages - selected_page_set)
    if declared_candidate_count > 0 and not sampled_candidate_ids:
        errors.append("candidate manifest has candidates but no sampled candidate_ids")
    if unsampled_manifest_pages:
        warnings.append(f"manifest candidate pages not selected by sample: {unsampled_manifest_pages}")

    return {
        "schema": "pdf_lab.second_pass.candidate_sample_linkage_validation.v1",
        "manifest_schema": manifest.get("schema"),
        "sampled_cases_schema": sampled_cases.get("schema"),
        "candidate_count": declared_candidate_count,
        "actual_candidate_count": len(candidates),
        "selected_count": declared_selected_count,
        "actual_page_case_count": len(page_cases),
        "manifest_candidate_id_count": len(candidate_by_id),
        "sampled_candidate_id_count": len(sampled_candidate_ids),
        "manifest_pages": sorted(manifest_pages),
        "selected_pages": sorted(selected_page_set),
        "accepted_forced_pages": accepted_forced_pages,
        "probabilistic_selected_pages": probabilistic_selected_pages,
        "malformed_sampling_metadata_case_ids": sorted(set(malformed_sampling_metadata_case_ids)),
        "malformed_forced_probability_case_ids": sorted(set(malformed_forced_probability_case_ids)),
        "manifest_preset_counts": dict(sorted(manifest_preset_counts.items())),
        "warnings": warnings,
        "ok": not errors,
        "errors": errors,
    }


def build_deterministic_execution_plan(
    *,
    sampled_cases: dict[str, Any],
    patch_mode: str,
    patch_backend: str,
    review_mode: str,
    commit_mode: str,
    page_orchestrator_mode: str,
    stop_on_nonterminal: bool,
) -> dict[str, Any]:
    page_cases = sampled_cases.get("page_cases") or []
    return {
        "schema": "pdf_lab.second_pass.deterministic_execution_plan.v1",
        "created_at": utc_now(),
        "owner": "pdf_lab_harness_code",
        "agent_decision_allowed": False,
        "execution_mode": "sequential",
        "page_case_order": [
            {
                "index": index,
                "case_id": case.get("case_id"),
                "page_number": case.get("page_number"),
                "candidate_ids": case.get("candidate_ids") or [],
                "preset_counts": case.get("preset_counts") or {},
                "strata": case.get("strata") or [],
                "forced_by_human_annotation": case.get("forced_by_human_annotation") is True,
                "selection_probability_estimate": case.get("selection_probability_estimate"),
                "selection_probability_basis": case.get("selection_probability_basis"),
            }
            for index, case in enumerate(page_cases, start=1)
        ],
        "stop_on_nonterminal": stop_on_nonterminal,
        "pre_page_gates": [
            "candidate_sample_linkage_validation.ok",
            "sampling_gate.ok",
            "scillm_code_root_visibility.ok",
            "scillm_proof_floor.ok when live patch mode requires scillm",
            "opencode_completion_canary.ok when patch_backend == opencode_serve",
            "scillm_transport_readonly_canary.ok when patch_backend == scillm_orchestrator",
            "scillm_transport_write_canary.ok when patch_backend == scillm_orchestrator",
        ],
        "per_page_dag_policy": {
            "review_mode": review_mode,
            "patch_mode": patch_mode,
            "patch_backend": patch_backend,
            "page_orchestrator_mode": page_orchestrator_mode,
            "patch_delegate_is_executor_only": True,
            "planner_merge_decision_owner": "pdf_lab_harness_code",
        },
        "commit_policy": {
            "commit_mode": commit_mode,
            "one_git_commit_per_verified_bug_fix": True,
            "patched_confirmed_requires": [
                "after_review_validation reviewed_clean",
                "patch_scope_validation.ok",
                "test_validation.ok",
                "commit_acceptance_gate.ok",
                "commit_gate.exact_file_match",
                "revertability_check.ok",
            ],
            "failed_commit_attempt_policy": "unstage_attempted_patch_files_only_and_record_index_cleanup",
        },
    }


def validate_deterministic_execution_plan(
    plan: dict[str, Any] | None,
    *,
    page_results: list[dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        errors.append("missing deterministic execution plan")
        plan = {}
    if plan.get("schema") != "pdf_lab.second_pass.deterministic_execution_plan.v1":
        errors.append(f"deterministic execution plan schema mismatch: {plan.get('schema')}")
    if plan.get("owner") != "pdf_lab_harness_code":
        errors.append("deterministic execution plan owner is not pdf_lab_harness_code")
    if plan.get("agent_decision_allowed") is not False:
        errors.append("deterministic execution plan must set agent_decision_allowed false")
    if plan.get("execution_mode") != "sequential":
        errors.append("deterministic execution plan execution_mode is not sequential")
    commit_policy = plan.get("commit_policy")
    if not isinstance(commit_policy, dict) or commit_policy.get("one_git_commit_per_verified_bug_fix") is not True:
        errors.append("deterministic execution plan missing one_git_commit_per_verified_bug_fix policy")
    page_case_order = plan.get("page_case_order")
    if not isinstance(page_case_order, list):
        errors.append("deterministic execution plan page_case_order is not a list")
        page_case_order = []
    planned_case_ids = [
        case.get("case_id")
        for case in page_case_order
        if isinstance(case, dict) and isinstance(case.get("case_id"), str) and case.get("case_id")
    ]
    planned_page_numbers = [
        case.get("page_number")
        for case in page_case_order
        if isinstance(case, dict) and is_plain_int(case.get("page_number")) and case.get("page_number") >= 1
    ]
    if any(
        not isinstance(case.get("case_id"), str) or not case.get("case_id")
        for case in page_case_order
        if isinstance(case, dict)
    ):
        errors.append("deterministic execution plan page_case_order contains missing case_id")
    if any(
        not is_plain_int(case.get("page_number")) or case.get("page_number") < 1
        for case in page_case_order
        if isinstance(case, dict)
    ):
        errors.append("deterministic execution plan page_case_order contains missing integer page_number")
    duplicate_planned_case_ids = sorted(
        case_id
        for case_id, count in Counter(planned_case_ids).items()
        if count > 1
    )
    duplicate_planned_page_numbers = sorted(
        page_number
        for page_number, count in Counter(planned_page_numbers).items()
        if count > 1
    )
    if duplicate_planned_case_ids:
        errors.append(f"deterministic execution plan duplicate case_ids: {duplicate_planned_case_ids}")
    if duplicate_planned_page_numbers:
        errors.append(f"deterministic execution plan duplicate page_numbers: {duplicate_planned_page_numbers}")
    malformed_planned_case_ids: list[str] = []
    planned_case_id_page_suffix_mismatches: list[str] = []
    malformed_probability_case_ids: list[str] = []
    malformed_forced_probability_case_ids: list[str] = []
    for index, case in enumerate(page_case_order):
        if not isinstance(case, dict):
            errors.append(f"deterministic execution plan page_case_order[{index}] is not an object")
            continue
        raw_case_id = case.get("case_id")
        case_id = raw_case_id if isinstance(raw_case_id, str) and raw_case_id else f"page_case_order[{index}]"
        case_id_match = PAGE_CASE_ID_RE.fullmatch(case_id)
        if case_id_match is None:
            malformed_planned_case_ids.append(case_id)
        page_number = case.get("page_number")
        if case_id_match is not None and is_plain_int(page_number) and int(case_id_match.group("page_number")) != page_number:
            planned_case_id_page_suffix_mismatches.append(case_id)
        estimate = case.get("selection_probability_estimate")
        basis = case.get("selection_probability_basis")
        if estimate is not None:
            if not is_plain_number(estimate) or not 0 <= float(estimate) <= 1:
                malformed_probability_case_ids.append(case_id)
        if basis is not None and not isinstance(basis, dict):
            malformed_probability_case_ids.append(case_id)
        if case.get("forced_by_human_annotation") is True:
            if estimate != 1.0 or not isinstance(basis, dict) or basis.get("method") != "forced_human_annotation":
                malformed_forced_probability_case_ids.append(case_id)
    if malformed_planned_case_ids:
        errors.append(f"deterministic execution plan has malformed case_ids: {sorted(set(malformed_planned_case_ids))}")
    if planned_case_id_page_suffix_mismatches:
        errors.append(
            "deterministic execution plan case_id page suffixes do not match page_number: "
            f"{sorted(set(planned_case_id_page_suffix_mismatches))}"
        )
    if malformed_probability_case_ids:
        errors.append(f"deterministic execution plan has malformed selection probability metadata: {sorted(set(malformed_probability_case_ids))}")
    if malformed_forced_probability_case_ids:
        errors.append(f"deterministic execution plan forced pages missing forced_human_annotation probability basis: {sorted(set(malformed_forced_probability_case_ids))}")
    actual_case_ids = [
        result.get("case_id")
        for result in page_results
        if isinstance(result.get("case_id"), str) and result.get("case_id")
    ]
    malformed_observed_case_results = [
        f"page_results[{index}] missing case_id"
        for index, result in enumerate(page_results)
        if not isinstance(result.get("case_id"), str) or not result.get("case_id")
    ]
    if malformed_observed_case_results:
        errors.extend(malformed_observed_case_results)
    if actual_case_ids and actual_case_ids != planned_case_ids[: len(actual_case_ids)]:
        errors.append(
            f"page result order does not match deterministic execution plan prefix: "
            f"actual={actual_case_ids}, planned_prefix={planned_case_ids[: len(actual_case_ids)]}"
        )
    return {
        "schema": "pdf_lab.second_pass.deterministic_execution_plan_validation.v1",
        "ok": not errors,
        "errors": errors,
        "planned_case_count": len(planned_case_ids),
        "observed_page_result_count": len(actual_case_ids),
        "planned_case_ids": planned_case_ids,
        "planned_page_numbers": planned_page_numbers,
        "duplicate_planned_case_ids": duplicate_planned_case_ids,
        "duplicate_planned_page_numbers": duplicate_planned_page_numbers,
        "malformed_planned_case_ids": sorted(set(malformed_planned_case_ids)),
        "planned_case_id_page_suffix_mismatches": sorted(set(planned_case_id_page_suffix_mismatches)),
        "malformed_probability_case_ids": sorted(set(malformed_probability_case_ids)),
        "malformed_forced_probability_case_ids": sorted(set(malformed_forced_probability_case_ids)),
        "observed_case_ids": actual_case_ids,
        "malformed_observed_case_results": malformed_observed_case_results,
    }


def _page_result_from_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    case_dir = Path(result["case_dir"])
    raw_result_case_id = result.get("case_id")
    raw_result_page_number = result.get("page_number")
    identity_mismatch_errors: list[str] = []
    if raw_result_case_id is not None and raw_result_case_id != case.get("case_id"):
        identity_mismatch_errors.append(
            f"raw page result case_id {raw_result_case_id!r} does not match sampled case_id {case.get('case_id')!r}"
        )
    if raw_result_page_number is not None and raw_result_page_number != case.get("page_number"):
        identity_mismatch_errors.append(
            f"raw page result page_number {raw_result_page_number!r} "
            f"does not match sampled page_number {case.get('page_number')!r}"
        )
    ledger_path = case_dir / "terminal_ledger.json"
    ledger, ledger_read_errors = read_json_object_if_exists(ledger_path)
    orchestrator_validation_path = case_dir / "scillm_orchestrator_page_dag_spec_validation.json"
    orchestrator_validation, orchestrator_validation_read_errors = read_json_object_if_exists(orchestrator_validation_path)
    page_orchestrator_validation_path = case_dir / "scillm_page_orchestrator_run_validation.json"
    page_orchestrator_validation, page_orchestrator_validation_read_errors = read_json_object_if_exists(
        page_orchestrator_validation_path
    )
    orchestrator_submission_validation_path = case_dir / "scillm_orchestrator_page_submission_validation.json"
    orchestrator_submission_validation, orchestrator_submission_validation_read_errors = read_json_object_if_exists(
        orchestrator_submission_validation_path
    )
    state_path = case_dir / "state.json"
    state, state_read_errors = read_json_object_if_exists(state_path)
    bug_report_path = case_dir / "scillm_patch_delegate_bug_report.json"
    bug_report, bug_report_read_errors = read_json_object_if_exists(bug_report_path)
    terminal_validation_path = case_dir / "terminal_ledger_validation.json"
    terminal_validation, terminal_validation_read_errors = read_json_object_if_exists(terminal_validation_path)
    review_bundle_validation_path = case_dir / "review_bundle_validation.json"
    review_bundle_validation, review_bundle_validation_read_errors = read_json_object_if_exists(review_bundle_validation_path)
    return {
        "case_id": case["case_id"],
        "page_number": case["page_number"],
        "raw_result_case_id": raw_result_case_id,
        "raw_result_page_number": raw_result_page_number,
        "raw_result_identity_mismatch_errors": identity_mismatch_errors,
        "terminal_status": ledger.get("terminal_status") or result.get("terminal_status"),
        "reason": ledger.get("reason"),
        "case_dir": str(case_dir),
        "terminal_ledger": str(ledger_path),
        "terminal_ledger_read_errors": ledger_read_errors,
        "terminal_ledger_validation": str(terminal_validation_path),
        "terminal_ledger_validation_ok": terminal_validation.get("ok"),
        "terminal_ledger_validation_read_errors": terminal_validation_read_errors,
        "review_bundle": str(case_dir / "review_bundle.zip"),
        "review_bundle_validation": str(review_bundle_validation_path),
        "review_bundle_validation_ok": review_bundle_validation.get("ok"),
        "review_bundle_zip_content_ok": review_bundle_validation.get("zip_content_ok"),
        "review_bundle_validation_read_errors": review_bundle_validation_read_errors,
        "commit_sha": ledger.get("commit_sha"),
        "evidence_artifacts": ledger.get("evidence_artifacts") or [],
        "orchestrator_dag_spec": str(case_dir / "scillm_orchestrator_page_dag_spec.json"),
        "orchestrator_dag_spec_validation": str(orchestrator_validation_path),
        "orchestrator_dag_spec_ok": orchestrator_validation.get("ok"),
        "orchestrator_dag_spec_validation_read_errors": orchestrator_validation_read_errors,
        "orchestrator_target_dag_state_owner": orchestrator_validation.get("target_dag_state_owner"),
        "orchestrator_page_submission": str(case_dir / "scillm_orchestrator_page_submission.json"),
        "orchestrator_page_submission_validation": str(orchestrator_submission_validation_path),
        "orchestrator_page_submission_ok": orchestrator_submission_validation.get("ok"),
        "orchestrator_page_submission_validation_read_errors": orchestrator_submission_validation_read_errors,
        "orchestrator_page_submission_dag_sha256": orchestrator_submission_validation.get("dag_spec_sha256"),
        "page_orchestrator_run_validation": str(page_orchestrator_validation_path),
        "page_orchestrator_run_ok": page_orchestrator_validation.get("ok"),
        "page_orchestrator_run_validation_read_errors": page_orchestrator_validation_read_errors,
        "page_orchestrator_registered": page_orchestrator_validation.get("registered"),
        "page_orchestrator_transport_run_id": page_orchestrator_validation.get("transport_run_id")
        or state.get("page_orchestrator_transport_run_id"),
        "state_read_errors": state_read_errors,
        "scillm_patch_delegate_bug_report": str(bug_report_path) if bug_report_path.exists() else None,
        "scillm_patch_delegate_bug_report_schema": bug_report.get("schema"),
        "scillm_patch_delegate_bug_report_terminal_reason": bug_report.get("terminal_reason"),
        "scillm_patch_delegate_bug_report_read_errors": bug_report_read_errors,
        "scillm_patch_delegate_bug_report_transport_run_id": (bug_report.get("observed") or {}).get("transport_run_id")
        if isinstance(bug_report.get("observed"), dict)
        else None,
    }


def build_scillm_patch_delegate_bug_report_bundle(
    *,
    out_dir: Path,
    page_results: list[dict[str, Any]],
) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for result in page_results:
        bug_report_path = result.get("scillm_patch_delegate_bug_report")
        if not bug_report_path:
            continue
        path = Path(str(bug_report_path))
        report, read_errors = read_json_object_if_exists(path)
        reports.append(
            {
                "case_id": result.get("case_id"),
                "page_number": result.get("page_number"),
                "terminal_status": result.get("terminal_status"),
                "terminal_reason": result.get("reason"),
                "case_dir": result.get("case_dir"),
                "bug_report_artifact": str(path),
                "bug_report_schema": report.get("schema"),
                "bug_report_terminal_reason": report.get("terminal_reason"),
                "transport_run_id": (report.get("observed") or {}).get("transport_run_id")
                if isinstance(report.get("observed"), dict)
                else result.get("scillm_patch_delegate_bug_report_transport_run_id"),
                "validation_errors": (report.get("observed") or {}).get("validation_errors", [])
                if isinstance(report.get("observed"), dict)
                else [],
                "read_errors": read_errors or list(result.get("scillm_patch_delegate_bug_report_read_errors") or []),
                "scillm_project_agent_bug_report": report.get("scillm_project_agent_bug_report"),
            }
        )
    return {
        "schema": "pdf_lab.second_pass.scillm_patch_delegate_bug_report_bundle.v1",
        "created_at": utc_now(),
        "out_dir": str(out_dir),
        "bug_report_count": len(reports),
        "malformed_bug_report_count": sum(1 for report in reports if report.get("read_errors")),
        "reports": reports,
        "scillm_project_agent_summary": (
            "Each report is a fail-closed pdf-lab patch delegate failure. "
            "The scillm/OpenCode side should make the bounded request return either "
            "PATCH_APPLIED with a non-empty diff, or PATCH_DELEGATE_BLOCKED with a concrete substrate reason."
            if reports
            else ""
        ),
    }


def build_live_scillm_canary_bug_report(
    *,
    out_dir: Path,
    code_root: Path,
    patch_mode: str,
    patch_backend: str,
    code_root_visibility: dict[str, Any] | None,
    scillm_proof_floor: dict[str, Any] | None,
    opencode_completion_canary: dict[str, Any] | None,
    scillm_transport_readonly_canary: dict[str, Any] | None,
    scillm_transport_write_canary: dict[str, Any] | None,
) -> dict[str, Any]:
    lane_required = patch_mode == "live" and patch_backend in {"opencode_serve", "scillm_orchestrator"}
    checks = [
        ("code_root_visibility", code_root_visibility),
        ("scillm_proof_floor", scillm_proof_floor),
        ("opencode_completion_canary", opencode_completion_canary),
        ("scillm_transport_readonly_canary", scillm_transport_readonly_canary),
        ("scillm_transport_write_canary", scillm_transport_write_canary),
    ]
    observed_checks = [
        {
            "check_id": check_id,
            "present": isinstance(payload, dict),
            "schema": payload.get("schema") if isinstance(payload, dict) else None,
            "ok": payload.get("ok") if isinstance(payload, dict) else None,
            "status": payload.get("status") if isinstance(payload, dict) else None,
            "errors": payload.get("errors") if isinstance(payload, dict) else None,
        }
        for check_id, payload in checks
    ]
    failed_checks = [
        {
            "check_id": check_id,
            "schema": payload.get("schema"),
            "status": payload.get("status"),
            "errors": payload.get("errors") or [],
            "request_artifact": payload.get("request_artifact"),
            "receipt_artifact": payload.get("receipt_artifact"),
            "error_artifact": payload.get("error_artifact"),
            "validation_artifact": payload.get("validation_artifact"),
            "cleanup_artifact": payload.get("cleanup_artifact"),
            "event_stream_artifact": payload.get("event_stream_artifact"),
        }
        for check_id, payload in checks
        if isinstance(payload, dict) and payload.get("ok") is not True
    ]
    missing_required_checks = [
        check_id
        for check_id, payload in checks
        if lane_required and payload is None and check_id in {"code_root_visibility", "scillm_proof_floor"}
    ]
    errors: list[str] = []
    if failed_checks:
        errors.append(f"live scillm canary checks failed: {[check['check_id'] for check in failed_checks]}")
    if missing_required_checks:
        errors.append(f"live scillm canary checks missing: {missing_required_checks}")
    return {
        "schema": "pdf_lab.second_pass.live_scillm_canary_bug_report.v1",
        "created_at": utc_now(),
        "out_dir": str(out_dir),
        "code_root": str(code_root.resolve()),
        "patch_mode": patch_mode,
        "patch_backend": patch_backend,
        "lane_required": lane_required,
        "ok": not errors,
        "errors": errors,
        "observed_checks": observed_checks,
        "failed_checks": failed_checks,
        "missing_required_checks": missing_required_checks,
        "scillm_project_agent_bug_report": (
            "Fix the live scillm/OpenCode substrate so all pdf-lab proof-floor and canary gates pass. "
            "The deterministic harness requires green liveliness, OpenCode health, caller-skill chat contract, "
            "OpenCode serve write canary, transport read-only canary, and transport write canary before delegated "
            "patching can be claimed operational. Inspect failed_checks artifact paths for the exact request, "
            "response, validation, and cleanup evidence."
            if errors
            else None
        ),
    }


def package_scillm_patch_delegate_bug_report_bundle(
    *,
    out_dir: Path,
    bundle_path: Path,
    zip_path: Path,
    page_results: list[dict[str, Any]],
) -> dict[str, Any]:
    included: list[str] = []
    missing: list[str] = []
    required_zip_entries: list[str] = []

    def add_file(bundle: zipfile.ZipFile, source: Path, arcname: str) -> None:
        required_zip_entries.append(arcname)
        if source.is_file():
            bundle.write(source, arcname=arcname)
            included.append(arcname)
        else:
            missing.append(str(source))

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        add_file(bundle, bundle_path, bundle_path.name)
        for result in page_results:
            bug_report_artifact = result.get("scillm_patch_delegate_bug_report")
            if not bug_report_artifact:
                continue
            case_dir = Path(str(result.get("case_dir") or "")).resolve()
            case_prefix = f"page_cases/{result.get('case_id') or case_dir.name}"
            for artifact in [
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
                add_file(bundle, case_dir / artifact, f"{case_prefix}/{artifact}")
    zip_entries: list[str] = []
    duplicate_zip_entries: list[str] = []
    if zip_path.is_file():
        with zipfile.ZipFile(zip_path) as bundle:
            zip_entries = bundle.namelist()
        entry_counts = Counter(zip_entries)
        duplicate_zip_entries = sorted(entry for entry, count in entry_counts.items() if count > 1)
    missing_expected_zip_entries = sorted(entry for entry in required_zip_entries if entry not in set(zip_entries))
    zip_content_ok = zip_path.is_file() and not missing_expected_zip_entries and not duplicate_zip_entries
    return {
        "schema": "pdf_lab.second_pass.scillm_patch_delegate_bug_report_zip.v1",
        "zip_path": str(zip_path),
        "included_count": len(included),
        "included_artifacts": included,
        "missing_artifacts": missing,
        "required_zip_entries": required_zip_entries,
        "zip_entry_count": len(zip_entries),
        "zip_content_ok": zip_content_ok,
        "missing_expected_zip_entries": missing_expected_zip_entries,
        "duplicate_zip_entries": duplicate_zip_entries,
        "ok": not missing and zip_content_ok,
    }


def build_patch_commit_ledger(
    *,
    out_dir: Path,
    page_results: list[dict[str, Any]],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    commit_shas: list[str] = []
    for result in page_results:
        if result.get("terminal_status") != "patched_confirmed":
            continue
        case_dir = Path(str(result.get("case_dir") or ""))
        evidence_artifacts = result.get("evidence_artifacts") or []
        commit_sha = result.get("commit_sha")
        commit_acceptance_path = case_dir / "commit_acceptance_gate.json"
        commit_gate_path = case_dir / "commit_gate.json"
        revertability_path = case_dir / "revertability_check.json"
        terminal_ledger_path = case_dir / "terminal_ledger.json"
        terminal_ledger_validation_path = case_dir / "terminal_ledger_validation.json"
        review_bundle_path = case_dir / "review_bundle.zip"
        review_bundle_validation_path = case_dir / "review_bundle_validation.json"
        patch_scope_validation_path = case_dir / "patch_scope_validation.json"
        test_validation_path = case_dir / "test_validation.json"
        review_after_request_validation_path = case_dir / "review_after_request_validation.json"
        review_after_validation_path = case_dir / "review_after_validation.json"
        review_after_response_path = case_dir / "review_after_response.json"
        entry_errors: list[str] = []
        terminal_ledger: dict[str, Any] = {}
        commit_acceptance: dict[str, Any] = {}
        commit_gate: dict[str, Any] = {}
        revertability: dict[str, Any] = {}
        terminal_ledger_validation: dict[str, Any] = {}
        review_bundle_validation: dict[str, Any] = {}
        patch_scope_validation: dict[str, Any] = {}
        test_validation: dict[str, Any] = {}
        review_after_request_validation: dict[str, Any] = {}
        review_after_validation: dict[str, Any] = {}
        review_after_response: dict[str, Any] = {}
        if not isinstance(commit_sha, str) or not commit_sha:
            entry_errors.append("missing commit_sha")
        if "commit_gate.json" not in evidence_artifacts:
            entry_errors.append("terminal evidence missing commit_gate.json")
        if "revertability_check.json" not in evidence_artifacts:
            entry_errors.append("terminal evidence missing revertability_check.json")
        if "commit_acceptance_gate.json" not in evidence_artifacts:
            entry_errors.append("terminal evidence missing commit_acceptance_gate.json")
        if "terminal_ledger_validation.json" not in evidence_artifacts:
            entry_errors.append("terminal evidence missing terminal_ledger_validation.json")
        if "patch_scope_validation.json" not in evidence_artifacts:
            entry_errors.append("terminal evidence missing patch_scope_validation.json")
        if "test_validation.json" not in evidence_artifacts:
            entry_errors.append("terminal evidence missing test_validation.json")
        if "review_after_request_validation.json" not in evidence_artifacts:
            entry_errors.append("terminal evidence missing review_after_request_validation.json")
        if "review_after_validation.json" not in evidence_artifacts:
            entry_errors.append("terminal evidence missing review_after_validation.json")
        if "review_after_response.json" not in evidence_artifacts:
            entry_errors.append("terminal evidence missing review_after_response.json")
        if not commit_acceptance_path.is_file():
            entry_errors.append("missing commit_acceptance_gate.json artifact")
        else:
            try:
                commit_acceptance = json.loads(commit_acceptance_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed proof is a ledger failure.
                entry_errors.append(f"commit_acceptance_gate.json unreadable: {type(exc).__name__}: {exc}")
        if not commit_gate_path.is_file():
            entry_errors.append("missing commit_gate.json artifact")
        else:
            try:
                commit_gate = json.loads(commit_gate_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed proof is a ledger failure.
                entry_errors.append(f"commit_gate.json unreadable: {type(exc).__name__}: {exc}")
        if not revertability_path.is_file():
            entry_errors.append("missing revertability_check.json artifact")
        else:
            try:
                revertability = json.loads(revertability_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed proof is a ledger failure.
                entry_errors.append(f"revertability_check.json unreadable: {type(exc).__name__}: {exc}")
        if not terminal_ledger_path.is_file():
            entry_errors.append("missing terminal_ledger.json artifact")
        else:
            try:
                terminal_ledger = json.loads(terminal_ledger_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed proof is a ledger failure.
                entry_errors.append(f"terminal_ledger.json unreadable: {type(exc).__name__}: {exc}")
        if not terminal_ledger_validation_path.is_file():
            entry_errors.append("missing terminal_ledger_validation.json artifact")
        else:
            try:
                terminal_ledger_validation = json.loads(terminal_ledger_validation_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed proof is a ledger failure.
                entry_errors.append(f"terminal_ledger_validation.json unreadable: {type(exc).__name__}: {exc}")
        if not review_bundle_path.is_file():
            entry_errors.append("missing review_bundle.zip artifact")
        if not review_bundle_validation_path.is_file():
            entry_errors.append("missing review_bundle_validation.json artifact")
        else:
            try:
                review_bundle_validation = json.loads(review_bundle_validation_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed proof is a ledger failure.
                entry_errors.append(f"review_bundle_validation.json unreadable: {type(exc).__name__}: {exc}")
        if not patch_scope_validation_path.is_file():
            entry_errors.append("missing patch_scope_validation.json artifact")
        else:
            try:
                patch_scope_validation = json.loads(patch_scope_validation_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed proof is a ledger failure.
                entry_errors.append(f"patch_scope_validation.json unreadable: {type(exc).__name__}: {exc}")
        if not test_validation_path.is_file():
            entry_errors.append("missing test_validation.json artifact")
        else:
            try:
                test_validation = json.loads(test_validation_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed proof is a ledger failure.
                entry_errors.append(f"test_validation.json unreadable: {type(exc).__name__}: {exc}")
        if not review_after_request_validation_path.is_file():
            entry_errors.append("missing review_after_request_validation.json artifact")
        else:
            try:
                review_after_request_validation = json.loads(review_after_request_validation_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed proof is a ledger failure.
                entry_errors.append(f"review_after_request_validation.json unreadable: {type(exc).__name__}: {exc}")
        if not review_after_validation_path.is_file():
            entry_errors.append("missing review_after_validation.json artifact")
        else:
            try:
                review_after_validation = json.loads(review_after_validation_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed proof is a ledger failure.
                entry_errors.append(f"review_after_validation.json unreadable: {type(exc).__name__}: {exc}")
        if not review_after_response_path.is_file():
            entry_errors.append("missing review_after_response.json artifact")
        else:
            try:
                review_after_response = json.loads(review_after_response_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed proof is a ledger failure.
                entry_errors.append(f"review_after_response.json unreadable: {type(exc).__name__}: {exc}")
        if terminal_ledger:
            if terminal_ledger.get("schema") != "pdf_lab.second_pass.page_terminal_ledger.v1":
                entry_errors.append("terminal_ledger schema mismatch")
            if terminal_ledger.get("case_id") != result.get("case_id"):
                entry_errors.append("terminal_ledger case_id does not match page result")
            if terminal_ledger.get("page_number") != result.get("page_number"):
                entry_errors.append("terminal_ledger page_number does not match page result")
            if terminal_ledger.get("terminal_status") != "patched_confirmed":
                entry_errors.append("terminal_ledger terminal_status is not patched_confirmed")
            if terminal_ledger.get("commit_sha") != commit_sha:
                entry_errors.append("terminal_ledger commit_sha does not match page result")
            if terminal_ledger.get("commit_acceptance_ok") is not True:
                entry_errors.append("terminal_ledger commit_acceptance_ok is not true")
            if terminal_ledger.get("commit_revertability_ok") is not True:
                entry_errors.append("terminal_ledger commit_revertability_ok is not true")
            if terminal_ledger.get("commit_exact_file_match") is not True:
                entry_errors.append("terminal_ledger commit_exact_file_match is not true")
            terminal_evidence = terminal_ledger.get("evidence_artifacts")
            if not isinstance(terminal_evidence, list):
                entry_errors.append("terminal_ledger evidence_artifacts is not a list")
            else:
                for artifact in [
                    "terminal_ledger_validation.json",
                    "patch_scope_validation.json",
                    "test_validation.json",
                    "review_after_request_validation.json",
                    "review_after_validation.json",
                    "review_after_response.json",
                    "commit_acceptance_gate.json",
                    "commit_gate.json",
                    "revertability_check.json",
                ]:
                    if artifact not in terminal_evidence:
                        entry_errors.append(f"terminal_ledger evidence missing {artifact}")
        if terminal_ledger_validation:
            if terminal_ledger_validation.get("schema") != "pdf_lab.second_pass.page_terminal_ledger_validation.v1":
                entry_errors.append("terminal_ledger_validation schema mismatch")
            if terminal_ledger_validation.get("ok") is not True:
                entry_errors.append("terminal_ledger_validation.ok is not true")
            if terminal_ledger_validation.get("case_id") != result.get("case_id"):
                entry_errors.append("terminal_ledger_validation case_id does not match page result")
            if terminal_ledger_validation.get("page_number") != result.get("page_number"):
                entry_errors.append("terminal_ledger_validation page_number does not match page result")
            if terminal_ledger_validation.get("terminal_status") != result.get("terminal_status"):
                entry_errors.append("terminal_ledger_validation terminal_status does not match page result")
        if review_bundle_validation:
            if review_bundle_validation.get("schema") != "pdf_lab.second_pass.page_review_bundle_validation.v1":
                entry_errors.append("review_bundle_validation schema mismatch")
            if review_bundle_validation.get("ok") is not True:
                entry_errors.append("review_bundle_validation.ok is not true")
            if review_bundle_validation.get("zip_content_ok") is not True:
                entry_errors.append("review_bundle_validation.zip_content_ok is not true")
            if review_bundle_validation.get("case_id") != result.get("case_id"):
                entry_errors.append("review_bundle_validation case_id does not match page result")
            if review_bundle_validation.get("page_number") != result.get("page_number"):
                entry_errors.append("review_bundle_validation page_number does not match page result")
        if patch_scope_validation:
            if patch_scope_validation.get("schema") != "pdf_lab.second_pass.patch_scope_validation.v1":
                entry_errors.append("patch_scope_validation schema mismatch")
            if patch_scope_validation.get("ok") is not True:
                entry_errors.append("patch_scope_validation.ok is not true")
            if not isinstance(patch_scope_validation.get("changed_files"), list):
                entry_errors.append("patch_scope_validation changed_files is not a list")
            if not isinstance(patch_scope_validation.get("test_files"), list):
                entry_errors.append("patch_scope_validation test_files is not a list")
        if test_validation:
            if test_validation.get("schema") != "pdf_lab.second_pass.test_validation.v1":
                entry_errors.append("test_validation schema mismatch")
            if test_validation.get("ok") is not True:
                entry_errors.append("test_validation.ok is not true")
            if patch_scope_validation and isinstance(patch_scope_validation.get("test_files"), list):
                required_tests = sorted(str(path) for path in patch_scope_validation.get("test_files") or [])
                validation_required_tests = sorted(str(path) for path in test_validation.get("required_test_files") or [])
                validation_covered_tests = sorted(str(path) for path in test_validation.get("covered_test_files") or [])
                validation_missing_tests = sorted(str(path) for path in test_validation.get("missing_test_file_coverage") or [])
                if validation_required_tests and validation_required_tests != required_tests:
                    entry_errors.append("test_validation required_test_files do not match patch_scope_validation test_files")
                if validation_covered_tests != required_tests:
                    entry_errors.append("test_validation covered_test_files do not match patch_scope_validation test_files")
                if validation_missing_tests:
                    entry_errors.append("test_validation missing_test_file_coverage is not empty")
        if review_after_request_validation:
            if review_after_request_validation.get("schema") != "pdf_lab.second_pass.review_request_validation.v1":
                entry_errors.append("review_after_request_validation schema mismatch")
            if review_after_request_validation.get("ok") is not True:
                entry_errors.append("review_after_request_validation.ok is not true")
        if review_after_validation:
            if review_after_validation.get("schema") != "pdf_lab.second_pass.review_validation.v1":
                entry_errors.append("review_after_validation schema mismatch")
            if review_after_validation.get("ok") is not True:
                entry_errors.append("review_after_validation.ok is not true")
        if review_after_response:
            if review_after_response.get("schema") != "pdf_lab.second_pass.review_response.v1":
                entry_errors.append("review_after_response schema mismatch")
            if review_after_response.get("page_status") != "clean":
                entry_errors.append("review_after_response page_status is not clean")
            findings = review_after_response.get("candidate_findings")
            if not isinstance(findings, list):
                entry_errors.append("review_after_response candidate_findings is not a list")
            elif any(not isinstance(finding, dict) or finding.get("status") != "clean" for finding in findings):
                entry_errors.append("review_after_response candidate_findings are not all clean")
        if commit_acceptance:
            if commit_acceptance.get("schema") != "pdf_lab.second_pass.commit_acceptance_gate.v1":
                entry_errors.append("commit_acceptance_gate schema mismatch")
            if commit_acceptance.get("ok") is not True:
                entry_errors.append("commit_acceptance_gate.ok is not true")
            if commit_acceptance.get("commit_sha") != commit_sha:
                entry_errors.append("commit_acceptance_gate commit_sha does not match page result")
        if commit_gate:
            if commit_gate.get("schema") != "pdf_lab.second_pass.commit_gate.v1":
                entry_errors.append("commit_gate schema mismatch")
            if commit_gate.get("ok") is not True:
                entry_errors.append("commit_gate.ok is not true")
            if commit_gate.get("commit_sha") != commit_sha:
                entry_errors.append("commit_gate commit_sha does not match page result")
            if commit_gate.get("exact_file_match") is not True:
                entry_errors.append("commit_gate.exact_file_match is not true")
            if patch_scope_validation and isinstance(patch_scope_validation.get("changed_files"), list):
                verified_changed_files = sorted(str(path) for path in patch_scope_validation.get("changed_files") or [])
                committed_files = commit_gate.get("committed_files")
                if not isinstance(committed_files, list):
                    entry_errors.append("commit_gate committed_files missing or not a list")
                    committed_files = []
                else:
                    committed_files = sorted(str(path) for path in committed_files)
                    if committed_files != verified_changed_files:
                        entry_errors.append(
                            "commit_gate committed_files do not match patch_scope_validation changed_files"
                        )
            commit_gate_revertability = commit_gate.get("revertability_check")
            if not isinstance(commit_gate_revertability, dict):
                entry_errors.append("commit_gate.revertability_check missing or not an object")
            else:
                if commit_gate_revertability.get("schema") != "pdf_lab.second_pass.revertability_check.v1":
                    entry_errors.append("commit_gate.revertability_check schema mismatch")
                if commit_gate_revertability.get("ok") is not True:
                    entry_errors.append("commit_gate.revertability_check.ok is not true")
                if commit_gate_revertability.get("commit_sha") != commit_sha:
                    entry_errors.append("commit_gate.revertability_check commit_sha does not match page result")
        if revertability:
            if revertability.get("schema") != "pdf_lab.second_pass.revertability_check.v1":
                entry_errors.append("revertability_check schema mismatch")
            if revertability.get("ok") is not True:
                entry_errors.append("revertability_check.ok is not true")
            if revertability.get("commit_sha") != commit_sha:
                entry_errors.append("revertability_check commit_sha does not match page result")
        if isinstance(commit_sha, str) and commit_sha:
            commit_shas.append(commit_sha)
        entries.append(
            {
                "case_id": result.get("case_id"),
                "page_number": result.get("page_number"),
                "terminal_status": result.get("terminal_status"),
                "terminal_reason": result.get("reason"),
                "case_dir": str(case_dir),
                "commit_sha": commit_sha,
                "terminal_ledger": str(terminal_ledger_path),
                "terminal_ledger_validation": str(terminal_ledger_validation_path),
                "review_bundle_validation": str(review_bundle_validation_path),
                "patch_scope_validation": str(patch_scope_validation_path),
                "test_validation": str(test_validation_path),
                "review_after_request_validation": str(review_after_request_validation_path),
                "review_after_validation": str(review_after_validation_path),
                "review_after_response": str(review_after_response_path),
                "terminal_ledger_commit_sha": terminal_ledger.get("commit_sha") if terminal_ledger else None,
                "terminal_ledger_page_number": terminal_ledger.get("page_number") if terminal_ledger else None,
                "terminal_ledger_validation_page_number": terminal_ledger_validation.get("page_number")
                if terminal_ledger_validation
                else None,
                "terminal_ledger_commit_acceptance_ok": terminal_ledger.get("commit_acceptance_ok")
                if terminal_ledger
                else None,
                "terminal_ledger_commit_revertability_ok": terminal_ledger.get("commit_revertability_ok")
                if terminal_ledger
                else None,
                "review_bundle": str(review_bundle_path),
                "commit_acceptance_gate": str(commit_acceptance_path),
                "commit_gate": str(commit_gate_path),
                "revertability_check": str(revertability_path),
                "terminal_ledger_validation_ok": terminal_ledger_validation.get("ok")
                if terminal_ledger_validation
                else None,
                "review_bundle_validation_ok": review_bundle_validation.get("ok")
                if review_bundle_validation
                else None,
                "review_bundle_validation_page_number": review_bundle_validation.get("page_number")
                if review_bundle_validation
                else None,
                "review_bundle_zip_content_ok": review_bundle_validation.get("zip_content_ok")
                if review_bundle_validation
                else None,
                "patch_scope_validation_ok": patch_scope_validation.get("ok")
                if patch_scope_validation
                else None,
                "patch_scope_changed_files": patch_scope_validation.get("changed_files")
                if patch_scope_validation
                else None,
                "test_validation_ok": test_validation.get("ok") if test_validation else None,
                "review_after_request_validation_ok": review_after_request_validation.get("ok")
                if review_after_request_validation
                else None,
                "review_after_validation_ok": review_after_validation.get("ok")
                if review_after_validation
                else None,
                "review_after_page_status": review_after_response.get("page_status")
                if review_after_response
                else None,
                "commit_acceptance_ok": commit_acceptance.get("ok") if commit_acceptance else None,
                "commit_gate_ok": commit_gate.get("ok") if commit_gate else None,
                "commit_exact_file_match": commit_gate.get("exact_file_match") if commit_gate else None,
                "commit_gate_committed_files": commit_gate.get("committed_files") if commit_gate else None,
                "commit_gate_revertability_ok": commit_gate.get("revertability_check", {}).get("ok")
                if isinstance(commit_gate.get("revertability_check"), dict)
                else None,
                "commit_gate_revertability_commit_sha": commit_gate.get("revertability_check", {}).get("commit_sha")
                if isinstance(commit_gate.get("revertability_check"), dict)
                else None,
                "revertability_ok": revertability.get("ok") if revertability else None,
                "revertability_commit_sha": revertability.get("commit_sha") if revertability else None,
                "evidence_artifacts": evidence_artifacts,
                "ok": not entry_errors,
                "errors": entry_errors,
            }
        )
        errors.extend(f"{result.get('case_id')}: {error}" for error in entry_errors)
    duplicate_commit_shas = sorted(
        sha
        for sha, count in Counter(commit_shas).items()
        if sha and count > 1
    )
    if duplicate_commit_shas:
        errors.append(f"duplicate commit SHAs: {duplicate_commit_shas}")
    return {
        "schema": "pdf_lab.second_pass.patch_commit_ledger.v1",
        "created_at": utc_now(),
        "out_dir": str(out_dir),
        "commit_count": len(entries),
        "commit_shas": commit_shas,
        "duplicate_commit_shas": duplicate_commit_shas,
        "entries": entries,
        "ok": not errors,
        "errors": errors,
    }


def package_patch_commit_ledger(
    *,
    ledger_path: Path,
    zip_path: Path,
    page_results: list[dict[str, Any]],
) -> dict[str, Any]:
    included: list[str] = []
    missing: list[str] = []
    required_zip_entries: list[str] = []
    expected_sources: dict[str, Path] = {}

    def add_file(bundle: zipfile.ZipFile, source: Path, arcname: str) -> None:
        required_zip_entries.append(arcname)
        expected_sources[arcname] = source
        if source.is_file():
            bundle.write(source, arcname=arcname)
            included.append(arcname)
        else:
            missing.append(str(source))

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        add_file(bundle, ledger_path, ledger_path.name)
        for result in page_results:
            if result.get("terminal_status") != "patched_confirmed":
                continue
            case_dir = Path(str(result.get("case_dir") or "")).resolve()
            case_prefix = f"page_cases/{result.get('case_id') or case_dir.name}"
            for artifact in [
                "terminal_ledger.json",
                "terminal_ledger_validation.json",
                "commit_acceptance_gate.json",
                "commit_gate.json",
                "revertability_check.json",
                "review_bundle.zip",
                "review_bundle_validation.json",
            ]:
                add_file(bundle, case_dir / artifact, f"{case_prefix}/{artifact}")
    validation = validate_patch_commit_ledger_zip(
        zip_path=zip_path,
        included_artifacts=included,
        missing_artifacts=missing,
        required_zip_entries=required_zip_entries,
        expected_sources=expected_sources,
    )
    return validation


def validate_patch_commit_ledger_zip(
    *,
    zip_path: Path,
    included_artifacts: list[str],
    missing_artifacts: list[str],
    required_zip_entries: list[str],
    expected_sources: dict[str, Path],
) -> dict[str, Any]:
    zip_entries: list[str] = []
    duplicate_zip_entries: list[str] = []
    mismatched_zip_entries: list[str] = []
    if zip_path.is_file():
        with zipfile.ZipFile(zip_path) as bundle:
            zip_entries = bundle.namelist()
            for arcname in included_artifacts:
                source = expected_sources.get(arcname)
                if source is not None and arcname in zip_entries and source.is_file():
                    if bundle.read(arcname) != source.read_bytes():
                        mismatched_zip_entries.append(arcname)
        entry_counts = Counter(zip_entries)
        duplicate_zip_entries = sorted(entry for entry, count in entry_counts.items() if count > 1)
    missing_expected_zip_entries = sorted(entry for entry in required_zip_entries if entry not in set(zip_entries))
    zip_content_ok = zip_path.is_file() and not missing_expected_zip_entries and not duplicate_zip_entries and not mismatched_zip_entries
    return {
        "schema": "pdf_lab.second_pass.patch_commit_ledger_zip.v1",
        "zip_path": str(zip_path),
        "included_count": len(included_artifacts),
        "included_artifacts": included_artifacts,
        "missing_artifacts": missing_artifacts,
        "required_zip_entries": required_zip_entries,
        "zip_entry_count": len(zip_entries),
        "zip_content_ok": zip_content_ok,
        "missing_expected_zip_entries": missing_expected_zip_entries,
        "duplicate_zip_entries": duplicate_zip_entries,
        "mismatched_zip_entries": sorted(mismatched_zip_entries),
        "ok": not missing_artifacts and zip_content_ok,
    }


def package_harness_review_bundle(
    *,
    out_dir: Path,
    zip_path: Path,
    top_level_artifacts: list[Path],
    page_results: list[dict[str, Any]],
    validation_artifact_path: Path | None = None,
) -> dict[str, Any]:
    included: list[str] = []
    missing_required: list[str] = []
    required_zip_entries: list[str] = []
    expected_sources: dict[str, Path] = {}

    def add_required(bundle: zipfile.ZipFile, source: Path, arcname: str) -> None:
        required_zip_entries.append(arcname)
        expected_sources[arcname] = source
        if source.is_file():
            bundle.write(source, arcname=arcname)
            included.append(arcname)
        else:
            missing_required.append(str(source))

    def add_optional(bundle: zipfile.ZipFile, source: Path, arcname: str) -> None:
        if source.is_file():
            expected_sources[arcname] = source
            bundle.write(source, arcname=arcname)
            included.append(arcname)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for artifact in top_level_artifacts:
            add_required(bundle, artifact, artifact.name)
        for result in page_results:
            case_dir = Path(str(result.get("case_dir") or "")).resolve()
            case_prefix = f"page_cases/{result.get('case_id') or case_dir.name}"
            terminal_status = str(result.get("terminal_status") or "")
            for artifact in sorted(required_harness_review_bundle_page_artifacts(terminal_status)):
                add_required(bundle, case_dir / artifact, f"{case_prefix}/{artifact}")
            for artifact in sorted(optional_harness_review_bundle_page_artifacts(terminal_status)):
                add_optional(bundle, case_dir / artifact, f"{case_prefix}/{artifact}")
    if validation_artifact_path is not None:
        validation_arcname = validation_artifact_path.name
        required_zip_entries.append(validation_arcname)
        included.append(validation_arcname)
    result = validate_harness_review_bundle_zip(
        zip_path=zip_path,
        included_artifacts=included,
        missing_required_artifacts=missing_required,
        required_zip_entries=required_zip_entries,
        expected_sources=expected_sources,
        page_case_count=len(page_results),
        virtual_zip_entries=[validation_artifact_path.name] if validation_artifact_path is not None else [],
    )
    if validation_artifact_path is not None:
        write_json(validation_artifact_path, result)
        with zipfile.ZipFile(zip_path, "a", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(validation_artifact_path, arcname=validation_artifact_path.name)
    return result


def validate_harness_review_bundle_zip(
    *,
    zip_path: Path,
    included_artifacts: list[str],
    missing_required_artifacts: list[str],
    required_zip_entries: list[str],
    expected_sources: dict[str, Path],
    page_case_count: int,
    virtual_zip_entries: list[str] | None = None,
) -> dict[str, Any]:
    zip_entries: list[str] = []
    duplicate_zip_entries: list[str] = []
    mismatched_zip_entries: list[str] = []
    if zip_path.is_file():
        with zipfile.ZipFile(zip_path) as bundle:
            zip_entries = bundle.namelist()
            for arcname in included_artifacts:
                source = expected_sources.get(arcname)
                if source is not None and arcname in zip_entries and source.is_file():
                    if bundle.read(arcname) != source.read_bytes():
                        mismatched_zip_entries.append(arcname)
        zip_entries = [*zip_entries, *(virtual_zip_entries or [])]
        entry_counts = Counter(zip_entries)
        duplicate_zip_entries = sorted(entry for entry, count in entry_counts.items() if count > 1)
    missing_expected_zip_entries = sorted(entry for entry in required_zip_entries if entry not in set(zip_entries))
    zip_content_ok = zip_path.is_file() and not missing_expected_zip_entries and not duplicate_zip_entries and not mismatched_zip_entries
    return {
        "schema": "pdf_lab.second_pass.harness_review_bundle_zip.v1",
        "zip_path": str(zip_path),
        "included_count": len(included_artifacts),
        "included_artifacts": included_artifacts,
        "missing_required_artifacts": missing_required_artifacts,
        "required_zip_entries": required_zip_entries,
        "zip_entry_count": len(zip_entries),
        "zip_content_ok": zip_content_ok,
        "missing_expected_zip_entries": missing_expected_zip_entries,
        "duplicate_zip_entries": duplicate_zip_entries,
        "mismatched_zip_entries": sorted(mismatched_zip_entries),
        "page_case_count": page_case_count,
        "ok": not missing_required_artifacts and zip_content_ok,
    }


def package_validation_errors(validation: dict[str, Any] | None) -> list[str]:
    if not validation:
        return []
    errors: list[str] = []
    for key in [
        "missing_artifacts",
        "missing_required_artifacts",
        "missing_expected_zip_entries",
        "duplicate_zip_entries",
        "mismatched_zip_entries",
    ]:
        values = validation.get(key)
        if values:
            errors.extend(f"{key}: {value}" for value in values)
    if validation.get("zip_content_ok") is False and not errors:
        errors.append("zip_content_ok is false")
    return errors


def _without_bundle_consistency_field(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            key: _without_bundle_consistency_field(value)
            for key, value in payload.items()
            if key
            not in {
                "harness_review_bundle_consistency_validation",
                "harness_review_bundle_consistency_validation_result",
            }
        }
    if isinstance(payload, list):
        return [_without_bundle_consistency_field(value) for value in payload]
    return payload


def validate_harness_review_bundle_consistency(
    *,
    zip_path: Path,
    report_path: Path,
    readiness_audit_path: Path,
    bundle_validation_path: Path,
    final_gate_path: Path,
) -> dict[str, Any]:
    errors: list[str] = []
    comparisons: dict[str, bool] = {}
    if not zip_path.is_file():
        errors.append("harness review bundle zip is missing")
        zip_entries: list[str] = []
    else:
        with zipfile.ZipFile(zip_path) as bundle:
            zip_entries = bundle.namelist()
            for path in [report_path, readiness_audit_path, bundle_validation_path, final_gate_path]:
                arcname = path.name
                if arcname not in zip_entries:
                    errors.append(f"{arcname} missing from harness review bundle")
                    comparisons[arcname] = False
                    continue
                if not path.is_file():
                    errors.append(f"{arcname} persisted artifact is missing")
                    comparisons[arcname] = False
                    continue
                try:
                    zipped_payload = json.loads(bundle.read(arcname).decode("utf-8"))
                    persisted_payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001 - consistency proof must expose malformed artifacts.
                    errors.append(f"{arcname} consistency read failed: {type(exc).__name__}: {exc}")
                    comparisons[arcname] = False
                    continue
                if arcname == report_path.name:
                    zipped_payload = _without_bundle_consistency_field(zipped_payload)
                    persisted_payload = _without_bundle_consistency_field(persisted_payload)
                matches = zipped_payload == persisted_payload
                comparisons[arcname] = matches
                if not matches:
                    errors.append(f"{arcname} in harness review bundle does not match persisted artifact")
    return {
        "schema": "pdf_lab.second_pass.harness_review_bundle_consistency_validation.v1",
        "ok": not errors,
        "errors": errors,
        "zip_path": str(zip_path),
        "report_path": str(report_path),
        "readiness_audit_path": str(readiness_audit_path),
        "bundle_validation_path": str(bundle_validation_path),
        "final_gate_path": str(final_gate_path),
        "zip_entry_count": len(zip_entries),
        "comparisons": comparisons,
    }


def build_harness_final_gate(
    *,
    harness_readiness_audit: dict[str, Any],
    harness_review_bundle_consistency_validation: dict[str, Any],
    report_terminal_status: str | None = None,
) -> dict[str, Any]:
    final_gate_errors: list[str] = []
    readiness_ok = harness_readiness_audit.get("ok") is True
    bundle_consistency_ok = harness_review_bundle_consistency_validation.get("ok") is True
    if not readiness_ok:
        final_gate_errors.extend(
            f"readiness failed: {requirement}"
            for requirement in harness_readiness_audit.get("failed_requirements", [])
        )
        if not harness_readiness_audit.get("failed_requirements"):
            final_gate_errors.append("readiness failed: harness readiness audit ok is not true")
    if not bundle_consistency_ok:
        final_gate_errors.extend(
            f"bundle consistency failed: {error}"
            for error in harness_review_bundle_consistency_validation.get("errors", [])
        )
        if not harness_review_bundle_consistency_validation.get("errors"):
            final_gate_errors.append("bundle consistency failed: validation ok is not true")
    terminal_status = "passed" if not final_gate_errors else "failed_closed"
    if report_terminal_status is not None and report_terminal_status != terminal_status:
        final_gate_errors.append(
            f"report terminal_status {report_terminal_status!r} does not match final gate terminal_status {terminal_status!r}"
        )
        terminal_status = "failed_closed"
    return {
        "schema": "pdf_lab.second_pass.harness_final_gate.v1",
        "ok": not final_gate_errors,
        "readiness_ok": readiness_ok,
        "bundle_consistency_ok": bundle_consistency_ok,
        "terminal_status": terminal_status,
        "errors": final_gate_errors,
    }


def validate_harness_review_bundle_inputs(
    *,
    zip_path: Path,
    top_level_artifacts: list[Path],
    page_results: list[dict[str, Any]],
) -> dict[str, Any]:
    missing_required: list[str] = []
    included: list[str] = []
    for artifact in top_level_artifacts:
        if artifact.is_file():
            included.append(artifact.name)
        else:
            missing_required.append(str(artifact))
    for result in page_results:
        case_dir = Path(str(result.get("case_dir") or "")).resolve()
        case_prefix = f"page_cases/{result.get('case_id') or case_dir.name}"
        terminal_status = str(result.get("terminal_status") or "")
        for artifact in sorted(required_harness_review_bundle_page_artifacts(terminal_status)):
            source = case_dir / artifact
            if source.is_file():
                included.append(f"{case_prefix}/{artifact}")
            else:
                missing_required.append(str(source))
        for artifact in sorted(optional_harness_review_bundle_page_artifacts(terminal_status)):
            source = case_dir / artifact
            if source.is_file():
                included.append(f"{case_prefix}/{artifact}")
    return {
        "schema": "pdf_lab.second_pass.harness_review_bundle_zip.v1",
        "zip_path": str(zip_path),
        "included_count": len(included),
        "included_artifacts": included,
        "missing_required_artifacts": missing_required,
        "page_case_count": len(page_results),
        "ok": not missing_required,
    }


def build_harness_readiness_audit(
    *,
    out_dir: Path,
    candidate_manifest_path: Path | None,
    sampled_cases_path: Path | None,
    sampling_gate: dict[str, Any] | None,
    page_results: list[dict[str, Any]],
    aggregate: dict[str, Any],
    patch_mode: str,
    patch_backend: str,
    code_root_visibility: dict[str, Any] | None,
    scillm_proof_floor: dict[str, Any] | None,
    opencode_completion_canary: dict[str, Any] | None,
    scillm_transport_readonly_canary: dict[str, Any] | None,
    scillm_bug_report_zip_validation: dict[str, Any] | None,
    patch_commit_ledger: dict[str, Any] | None,
    patch_commit_ledger_zip_validation: dict[str, Any] | None,
    harness_review_bundle_validation: dict[str, Any] | None = None,
    candidate_sample_linkage_validation: dict[str, Any] | None = None,
    candidate_manifest_integrity_validation: dict[str, Any] | None = None,
    scillm_transport_write_canary: dict[str, Any] | None = None,
    deterministic_execution_plan_validation: dict[str, Any] | None = None,
    live_scillm_canary_bug_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add_check(requirement: str, ok: bool, evidence: Any = None, errors: list[str] | None = None) -> None:
        checks.append(
            {
                "requirement": requirement,
                "ok": bool(ok),
                "evidence": evidence,
                "errors": errors or ([] if ok else ["requirement not satisfied"]),
            }
        )

    add_check(
        "candidate manifest exists",
        bool(candidate_manifest_path and candidate_manifest_path.is_file()),
        str(candidate_manifest_path) if candidate_manifest_path else None,
    )
    add_check(
        "candidate manifest integrity passed",
        bool(candidate_manifest_integrity_validation and candidate_manifest_integrity_validation.get("ok") is True),
        candidate_manifest_integrity_validation,
        list((candidate_manifest_integrity_validation or {}).get("errors") or []),
    )
    add_check(
        "sampled page cases exist",
        bool(sampled_cases_path and sampled_cases_path.is_file()),
        str(sampled_cases_path) if sampled_cases_path else None,
    )
    add_check(
        "candidate manifest and sampled cases are linked",
        bool(candidate_sample_linkage_validation and candidate_sample_linkage_validation.get("ok") is True),
        candidate_sample_linkage_validation,
        list((candidate_sample_linkage_validation or {}).get("errors") or []),
    )
    add_check(
        "sampling gate passed",
        bool(sampling_gate and sampling_gate.get("ok") is True),
        sampling_gate,
        list((sampling_gate or {}).get("errors") or []),
    )
    add_check(
        "deterministic execution plan is code-owned and sequential",
        bool(deterministic_execution_plan_validation and deterministic_execution_plan_validation.get("ok") is True),
        deterministic_execution_plan_validation,
        list((deterministic_execution_plan_validation or {}).get("errors") or []),
    )
    add_check(
        "page aggregate resolved",
        bool(aggregate.get("ok") is True),
        {
            "status_counts": aggregate.get("status_counts"),
            "unresolved_count": aggregate.get("unresolved_count"),
        },
        list(aggregate.get("errors") or []),
    )
    page_result_sample_match_validation = validate_page_results_match_sampled_cases(
        sampled_cases_path=sampled_cases_path,
        page_results=page_results,
        aggregate=aggregate,
    )
    add_check(
        "page results match sampled page cases",
        bool(page_result_sample_match_validation.get("ok") is True),
        page_result_sample_match_validation,
        list(page_result_sample_match_validation.get("errors") or []),
    )
    live_patch_required = patch_mode == "live" and patch_backend in {"opencode_serve", "scillm_orchestrator"}
    live_opencode_serve_required = patch_mode == "live" and patch_backend == "opencode_serve"
    live_scillm_orchestrator_required = patch_mode == "live" and patch_backend == "scillm_orchestrator"
    scillm_proof_floor_artifact_validation = validate_scillm_proof_floor_artifacts(out_dir, scillm_proof_floor)
    opencode_completion_canary_artifact_validation = validate_live_canary_artifacts(
        out_dir=out_dir,
        canary=opencode_completion_canary,
        artifact_builder=opencode_completion_canary_artifacts,
        canary_schema="pdf_lab.second_pass.opencode_completion_canary.v1",
        validation_schema="pdf_lab.second_pass.opencode_completion_canary_validation.v1",
        validation_artifact_name="opencode_completion_canary_validation.json",
        cleanup_schema="pdf_lab.second_pass.opencode_completion_canary_cleanup.v1",
        cleanup_artifact_name="opencode_completion_canary_cleanup.json",
    )
    scillm_transport_readonly_canary_artifact_validation = validate_live_canary_artifacts(
        out_dir=out_dir,
        canary=scillm_transport_readonly_canary,
        artifact_builder=scillm_transport_readonly_canary_artifacts,
        canary_schema="pdf_lab.second_pass.scillm_transport_readonly_canary.v1",
        validation_schema="pdf_lab.second_pass.scillm_transport_readonly_canary_validation.v1",
        validation_artifact_name="scillm_transport_readonly_canary_validation.json",
    )
    scillm_transport_write_canary_artifact_validation = validate_live_canary_artifacts(
        out_dir=out_dir,
        canary=scillm_transport_write_canary,
        artifact_builder=scillm_transport_write_canary_artifacts,
        canary_schema="pdf_lab.second_pass.scillm_transport_write_canary.v1",
        validation_schema="pdf_lab.second_pass.scillm_transport_write_canary_validation.v1",
        validation_artifact_name="scillm_transport_write_canary_validation.json",
        cleanup_schema="pdf_lab.second_pass.opencode_completion_canary_cleanup.v1",
        cleanup_artifact_name="scillm_transport_write_canary_cleanup.json",
    )
    add_check(
        "live scillm code root visibility passed",
        (not live_patch_required) or bool(code_root_visibility and code_root_visibility.get("ok") is True),
        {
            "required": live_patch_required,
            "patch_mode": patch_mode,
            "patch_backend": patch_backend,
            "visibility": code_root_visibility,
        },
        list((code_root_visibility or {}).get("errors") or []),
    )
    add_check(
        "live scillm proof floor passed",
        (not live_patch_required) or bool(scillm_proof_floor_artifact_validation.get("ok") is True),
        {
            "required": live_patch_required,
            "patch_mode": patch_mode,
            "patch_backend": patch_backend,
            "proof_floor": scillm_proof_floor,
            "artifact_validation": scillm_proof_floor_artifact_validation,
        },
        list(scillm_proof_floor_artifact_validation.get("errors") or []),
    )
    add_check(
        "live opencode serve write-capability canary passed",
        (not live_opencode_serve_required)
        or bool(opencode_completion_canary_artifact_validation.get("ok") is True),
        {
            "required": live_opencode_serve_required,
            "patch_mode": patch_mode,
            "patch_backend": patch_backend,
            "canary": opencode_completion_canary,
            "artifact_validation": opencode_completion_canary_artifact_validation,
            "validation_artifact": (opencode_completion_canary or {}).get("validation_artifact"),
            "cleanup_artifact": (opencode_completion_canary or {}).get("cleanup_artifact"),
        },
        list(opencode_completion_canary_artifact_validation.get("errors") or []),
    )
    add_check(
        "live scillm transport read-only canary passed",
        (not live_scillm_orchestrator_required)
        or bool(scillm_transport_readonly_canary_artifact_validation.get("ok") is True),
        {
            "required": live_scillm_orchestrator_required,
            "patch_mode": patch_mode,
            "patch_backend": patch_backend,
            "canary": scillm_transport_readonly_canary,
            "artifact_validation": scillm_transport_readonly_canary_artifact_validation,
        },
        list(scillm_transport_readonly_canary_artifact_validation.get("errors") or []),
    )
    add_check(
        "live scillm transport write-capability canary passed",
        (not live_scillm_orchestrator_required)
        or bool(scillm_transport_write_canary_artifact_validation.get("ok") is True),
        {
            "required": live_scillm_orchestrator_required,
            "patch_mode": patch_mode,
            "patch_backend": patch_backend,
            "canary": scillm_transport_write_canary,
            "artifact_validation": scillm_transport_write_canary_artifact_validation,
            "validation_artifact": (scillm_transport_write_canary or {}).get("validation_artifact"),
            "cleanup_artifact": (scillm_transport_write_canary or {}).get("cleanup_artifact"),
        },
        list(scillm_transport_write_canary_artifact_validation.get("errors") or []),
    )
    live_canary_bug_report_errors: list[str] = []
    if live_patch_required:
        if not isinstance(live_scillm_canary_bug_report, dict):
            live_canary_bug_report_errors.append("live scillm canary bug report missing")
        else:
            if live_scillm_canary_bug_report.get("schema") != "pdf_lab.second_pass.live_scillm_canary_bug_report.v1":
                live_canary_bug_report_errors.append("live scillm canary bug report schema mismatch")
            if live_scillm_canary_bug_report.get("lane_required") is not True:
                live_canary_bug_report_errors.append("live scillm canary bug report lane_required is not true")
            any_live_canary_failed = any(
                payload is not None and isinstance(payload, dict) and payload.get("ok") is not True
                for payload in [
                    code_root_visibility,
                    scillm_proof_floor,
                    opencode_completion_canary if live_opencode_serve_required else None,
                    scillm_transport_readonly_canary if live_scillm_orchestrator_required else None,
                    scillm_transport_write_canary if live_scillm_orchestrator_required else None,
                ]
            )
            expected_failed_check_ids = sorted(
                check_id
                for check_id, payload in [
                    ("code_root_visibility", code_root_visibility),
                    ("scillm_proof_floor", scillm_proof_floor),
                    ("opencode_completion_canary", opencode_completion_canary if live_opencode_serve_required else None),
                    (
                        "scillm_transport_readonly_canary",
                        scillm_transport_readonly_canary if live_scillm_orchestrator_required else None,
                    ),
                    (
                        "scillm_transport_write_canary",
                        scillm_transport_write_canary if live_scillm_orchestrator_required else None,
                    ),
                ]
                if isinstance(payload, dict) and payload.get("ok") is not True
            )
            expected_observed_checks = [
                (check_id, payload)
                for check_id, payload in [
                    ("code_root_visibility", code_root_visibility),
                    ("scillm_proof_floor", scillm_proof_floor),
                    ("opencode_completion_canary", opencode_completion_canary if live_opencode_serve_required else None),
                    (
                        "scillm_transport_readonly_canary",
                        scillm_transport_readonly_canary if live_scillm_orchestrator_required else None,
                    ),
                    (
                        "scillm_transport_write_canary",
                        scillm_transport_write_canary if live_scillm_orchestrator_required else None,
                    ),
                ]
                if payload is not None
            ]
            reported_failed_check_ids = sorted(
                str(check.get("check_id") or "")
                for check in live_scillm_canary_bug_report.get("failed_checks") or []
                if isinstance(check, dict)
            )
            reported_observed_checks = {
                str(check.get("check_id") or ""): check
                for check in live_scillm_canary_bug_report.get("observed_checks") or []
                if isinstance(check, dict)
            }
            if any_live_canary_failed and live_scillm_canary_bug_report.get("ok") is not False:
                live_canary_bug_report_errors.append("live scillm canary bug report did not fail closed for failed canary checks")
            if any_live_canary_failed and not live_scillm_canary_bug_report.get("failed_checks"):
                live_canary_bug_report_errors.append("live scillm canary bug report missing failed_checks for failed canary checks")
            if expected_failed_check_ids != reported_failed_check_ids:
                live_canary_bug_report_errors.append(
                    f"live scillm canary bug report failed_checks mismatch: "
                    f"expected={expected_failed_check_ids}, reported={reported_failed_check_ids}"
                )
            for check_id, payload in expected_observed_checks:
                reported_check = reported_observed_checks.get(check_id)
                if not isinstance(reported_check, dict):
                    live_canary_bug_report_errors.append(
                        f"live scillm canary bug report observed_checks missing active check: {check_id}"
                    )
                    continue
                if not isinstance(payload, dict):
                    continue
                expected_check = {
                    "present": True,
                    "schema": payload.get("schema"),
                    "ok": payload.get("ok"),
                    "status": payload.get("status"),
                    "errors": payload.get("errors"),
                }
                reported_subset = {
                    "present": reported_check.get("present"),
                    "schema": reported_check.get("schema"),
                    "ok": reported_check.get("ok"),
                    "status": reported_check.get("status"),
                    "errors": reported_check.get("errors"),
                }
                if expected_check != reported_subset:
                    live_canary_bug_report_errors.append(
                        f"live scillm canary bug report observed_checks mismatch for {check_id}: "
                        f"expected={expected_check}, reported={reported_subset}"
                    )
            if not any_live_canary_failed and live_scillm_canary_bug_report.get("ok") is not True:
                live_canary_bug_report_errors.append("live scillm canary bug report is not ok despite green canary checks")
    add_check(
        "live scillm canary bug report is deterministic",
        (not live_patch_required) or not live_canary_bug_report_errors,
        {
            "required": live_patch_required,
            "patch_mode": patch_mode,
            "patch_backend": patch_backend,
            "bug_report": live_scillm_canary_bug_report,
        },
        live_canary_bug_report_errors,
    )
    orchestrator_errors: list[str] = []
    if live_scillm_orchestrator_required:
        if not page_results:
            orchestrator_errors.append("live scillm_orchestrator run has no page results")
        for result in page_results:
            case_id = result.get("case_id")
            if result.get("page_orchestrator_run_ok") is not True:
                orchestrator_errors.append(f"{case_id}: page_orchestrator_run_ok is not true")
            if result.get("page_orchestrator_registered") is not True:
                orchestrator_errors.append(f"{case_id}: page_orchestrator_registered is not true")
            if not result.get("page_orchestrator_transport_run_id"):
                orchestrator_errors.append(f"{case_id}: missing page_orchestrator_transport_run_id")
    add_check(
        "live scillm orchestrator page registration passed",
        (not live_scillm_orchestrator_required) or not orchestrator_errors,
        {
            "required": live_scillm_orchestrator_required,
            "patch_mode": patch_mode,
            "patch_backend": patch_backend,
            "page_count": len(page_results),
            "registered_count": sum(1 for result in page_results if result.get("page_orchestrator_registered") is True),
        },
        orchestrator_errors,
    )

    page_errors: list[str] = []
    for result in page_results:
        case_id = result.get("case_id")
        case_dir = Path(str(result.get("case_dir") or ""))
        terminal_ledger = Path(str(result.get("terminal_ledger") or ""))
        terminal_ledger_validation = Path(str(result.get("terminal_ledger_validation") or case_dir / "terminal_ledger_validation.json"))
        review_bundle = Path(str(result.get("review_bundle") or ""))
        evidence_artifacts = set(result.get("evidence_artifacts") or [])
        identity_mismatch_errors = result.get("raw_result_identity_mismatch_errors") or []
        expected_case_artifacts = {
            "terminal_ledger": case_dir / "terminal_ledger.json",
            "terminal_ledger_validation": case_dir / "terminal_ledger_validation.json",
            "review_bundle": case_dir / "review_bundle.zip",
            "review_bundle_validation": case_dir / "review_bundle_validation.json",
        }
        actual_case_artifacts = {
            "terminal_ledger": terminal_ledger,
            "terminal_ledger_validation": terminal_ledger_validation,
            "review_bundle": review_bundle,
            "review_bundle_validation": Path(
                str(result.get("review_bundle_validation") or expected_case_artifacts["review_bundle_validation"])
            ),
        }
        for artifact_key, expected_artifact_path in expected_case_artifacts.items():
            if actual_case_artifacts[artifact_key] != expected_artifact_path:
                page_errors.append(
                    f"{case_id}: {artifact_key} path is not case-local: {actual_case_artifacts[artifact_key]}"
                )
        if identity_mismatch_errors:
            page_errors.append(f"{case_id}: raw page result identity mismatch: {identity_mismatch_errors}")
        if result.get("terminal_status") not in TERMINAL_PAGE_STATUSES:
            page_errors.append(f"{case_id}: invalid terminal_status {result.get('terminal_status')}")
        for read_error_key in [
            "terminal_ledger_read_errors",
            "terminal_ledger_validation_read_errors",
            "orchestrator_dag_spec_validation_read_errors",
            "orchestrator_page_submission_validation_read_errors",
            "page_orchestrator_run_validation_read_errors",
            "state_read_errors",
            "scillm_patch_delegate_bug_report_read_errors",
            "review_bundle_validation_read_errors",
        ]:
            read_errors = result.get(read_error_key) or []
            if read_errors:
                page_errors.append(f"{case_id}: {read_error_key}: {read_errors}")
        if not terminal_ledger.is_file():
            page_errors.append(f"{case_id}: missing terminal_ledger artifact")
        else:
            try:
                terminal = json.loads(terminal_ledger.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - invalid JSON is explicit evidence failure.
                terminal = {"errors": [f"terminal_ledger unreadable: {type(exc).__name__}: {exc}"]}
                page_errors.append(f"{case_id}: terminal_ledger unreadable: {type(exc).__name__}: {exc}")
            if terminal.get("schema") != "pdf_lab.second_pass.page_terminal_ledger.v1":
                page_errors.append(f"{case_id}: terminal_ledger schema mismatch")
            if terminal.get("case_id") != case_id:
                page_errors.append(f"{case_id}: terminal_ledger case_id does not match page result")
            if terminal.get("page_number") != result.get("page_number"):
                page_errors.append(f"{case_id}: terminal_ledger page_number does not match page result")
            if terminal.get("terminal_status") != result.get("terminal_status"):
                page_errors.append(f"{case_id}: terminal_ledger terminal_status does not match page result")
            if terminal.get("commit_sha") != result.get("commit_sha"):
                page_errors.append(f"{case_id}: terminal_ledger commit_sha does not match page result")
            terminal_evidence = terminal.get("evidence_artifacts")
            if not isinstance(terminal_evidence, list):
                page_errors.append(f"{case_id}: terminal_ledger evidence_artifacts is not a list")
            elif set(terminal_evidence) != evidence_artifacts:
                page_errors.append(f"{case_id}: terminal_ledger evidence_artifacts do not match page result")
        if not terminal_ledger_validation.is_file():
            page_errors.append(f"{case_id}: missing terminal_ledger_validation artifact")
        else:
            try:
                terminal_validation = json.loads(terminal_ledger_validation.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - invalid JSON is explicit evidence failure.
                terminal_validation = {"ok": False, "errors": [f"terminal_ledger_validation unreadable: {type(exc).__name__}: {exc}"]}
            if terminal_validation.get("ok") is not True:
                page_errors.append(f"{case_id}: terminal_ledger_validation failed: {terminal_validation.get('errors')}")
            if terminal_validation.get("schema") != "pdf_lab.second_pass.page_terminal_ledger_validation.v1":
                page_errors.append(f"{case_id}: terminal_ledger_validation schema mismatch")
            if terminal_validation.get("case_id") != case_id:
                page_errors.append(f"{case_id}: terminal_ledger_validation case_id does not match page result")
            if terminal_validation.get("page_number") != result.get("page_number"):
                page_errors.append(f"{case_id}: terminal_ledger_validation page_number does not match page result")
            if terminal_validation.get("terminal_status") != result.get("terminal_status"):
                page_errors.append(f"{case_id}: terminal_ledger_validation terminal_status does not match page result")
        if "terminal_ledger_validation.json" not in evidence_artifacts:
            page_errors.append(f"{case_id}: terminal evidence missing terminal_ledger_validation.json")
        if not review_bundle.is_file():
            page_errors.append(f"{case_id}: missing review_bundle artifact")
        review_bundle_validation_path = actual_case_artifacts["review_bundle_validation"]
        if not review_bundle_validation_path.is_file():
            page_errors.append(f"{case_id}: missing review_bundle_validation artifact")
        else:
            try:
                review_bundle_validation = json.loads(review_bundle_validation_path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - invalid JSON is explicit evidence failure.
                review_bundle_validation = {"ok": False, "errors": [f"review_bundle_validation unreadable: {type(exc).__name__}: {exc}"]}
                page_errors.append(f"{case_id}: review_bundle_validation unreadable: {type(exc).__name__}: {exc}")
            if result.get("review_bundle_validation_ok") is not True:
                page_errors.append(f"{case_id}: review_bundle_validation failed")
            if result.get("review_bundle_zip_content_ok") is not True:
                page_errors.append(f"{case_id}: review_bundle_validation zip_content_ok is not true")
            if review_bundle_validation.get("schema") != "pdf_lab.second_pass.page_review_bundle_validation.v1":
                page_errors.append(f"{case_id}: review_bundle_validation schema mismatch")
            if review_bundle_validation.get("case_id") != case_id:
                page_errors.append(f"{case_id}: review_bundle_validation case_id does not match page result")
            if review_bundle_validation.get("page_number") != result.get("page_number"):
                page_errors.append(f"{case_id}: review_bundle_validation page_number does not match page result")
        if result.get("terminal_status") in RESOLVED_PASS_STATUSES:
            missing_declared = sorted(REQUIRED_PAGE_DAG_ARTIFACTS - evidence_artifacts)
            missing_files = sorted(
                artifact
                for artifact in REQUIRED_PAGE_DAG_ARTIFACTS
                if not (case_dir / artifact).is_file()
            )
            if missing_declared:
                page_errors.append(f"{case_id}: terminal evidence missing page DAG artifacts {missing_declared}")
            if missing_files:
                page_errors.append(f"{case_id}: case directory missing page DAG artifact files {missing_files}")
        if result.get("terminal_status") == "patched_confirmed":
            missing_declared = sorted(REQUIRED_PATCHED_CONFIRMED_ARTIFACTS - evidence_artifacts)
            missing_files = sorted(
                artifact
                for artifact in REQUIRED_PATCHED_CONFIRMED_ARTIFACTS
                if not (case_dir / artifact).is_file()
            )
            if missing_declared:
                page_errors.append(f"{case_id}: terminal evidence missing patched-confirmed artifacts {missing_declared}")
            if missing_files:
                page_errors.append(f"{case_id}: case directory missing patched-confirmed artifact files {missing_files}")
    add_check(
        "each resolved page case has self-contained DAG evidence",
        not page_errors,
        {"page_case_count": len(page_results)},
        page_errors,
    )

    add_check(
        "scillm patch delegate bug report bundle is packageable",
        bool(scillm_bug_report_zip_validation and scillm_bug_report_zip_validation.get("ok") is True),
        scillm_bug_report_zip_validation,
        package_validation_errors(scillm_bug_report_zip_validation),
    )
    add_check(
        "patch commit ledger passed",
        bool(patch_commit_ledger and patch_commit_ledger.get("ok") is True),
        {
            "commit_count": (patch_commit_ledger or {}).get("commit_count"),
            "commit_shas": (patch_commit_ledger or {}).get("commit_shas"),
        },
        list((patch_commit_ledger or {}).get("errors") or []),
    )
    raw_patched_confirmed_count = aggregate.get("patched_confirmed_count")
    if raw_patched_confirmed_count is not None:
        if not is_plain_int(raw_patched_confirmed_count) or raw_patched_confirmed_count < 0:
            expected_patch_commit_count = 0
            patch_commit_errors: list[str] = [
                f"aggregate patched_confirmed_count must be a non-negative integer: {raw_patched_confirmed_count!r}"
            ]
        else:
            expected_patch_commit_count = raw_patched_confirmed_count
            patch_commit_errors = []
    else:
        status_counts = aggregate.get("status_counts")
        if not isinstance(status_counts, dict):
            expected_patch_commit_count = 0
            patch_commit_errors = ["aggregate status_counts missing or not an object"]
        else:
            raw_status_patched_confirmed = status_counts.get("patched_confirmed", 0)
            if not is_plain_int(raw_status_patched_confirmed) or raw_status_patched_confirmed < 0:
                expected_patch_commit_count = 0
                patch_commit_errors = [
                    "aggregate status_counts.patched_confirmed must be a non-negative integer: "
                    f"{raw_status_patched_confirmed!r}"
                ]
            else:
                expected_patch_commit_count = raw_status_patched_confirmed
                patch_commit_errors = []
    patch_commit_count = (patch_commit_ledger or {}).get("commit_count")
    patch_commit_shas = list((patch_commit_ledger or {}).get("commit_shas") or [])
    if not is_plain_int(patch_commit_count) or patch_commit_count < 0:
        patch_commit_errors.append(f"patch commit ledger commit_count must be a non-negative integer: {patch_commit_count!r}")
    elif patch_commit_count != expected_patch_commit_count:
        patch_commit_errors.append(
            f"patch commit ledger count {patch_commit_count} does not match patched_confirmed count {expected_patch_commit_count}"
        )
    if len(set(patch_commit_shas)) != expected_patch_commit_count:
        patch_commit_errors.append(
            f"patch commit ledger unique SHA count {len(set(patch_commit_shas))} does not match patched_confirmed count {expected_patch_commit_count}"
        )
    if (patch_commit_ledger or {}).get("duplicate_commit_shas"):
        patch_commit_errors.append(
            f"patch commit ledger has duplicate commit SHAs: {(patch_commit_ledger or {}).get('duplicate_commit_shas')}"
        )
    patched_page_commit_by_case_id = {
        str(result.get("case_id")): str(result.get("commit_sha"))
        for result in page_results
        if result.get("terminal_status") == "patched_confirmed" and result.get("case_id") and result.get("commit_sha")
    }
    patch_commit_entries = (patch_commit_ledger or {}).get("entries")
    if not isinstance(patch_commit_entries, list):
        if expected_patch_commit_count > 0:
            patch_commit_errors.append("patch commit ledger entries missing or not a list")
        patch_commit_entries = []
    ledger_commit_by_case_id: dict[str, str] = {}
    duplicate_ledger_case_ids = sorted(
        case_id
        for case_id, count in Counter(
            str(entry.get("case_id") or "")
            for entry in patch_commit_entries
            if isinstance(entry, dict) and entry.get("case_id")
        ).items()
        if case_id and count > 1
    )
    for entry in patch_commit_entries:
        if not isinstance(entry, dict):
            patch_commit_errors.append("patch commit ledger entry is not an object")
            continue
        case_id = str(entry.get("case_id") or "")
        commit_sha = str(entry.get("commit_sha") or "")
        if case_id and commit_sha:
            ledger_commit_by_case_id[case_id] = commit_sha
    if duplicate_ledger_case_ids:
        patch_commit_errors.append(f"patch commit ledger has duplicate case_ids: {duplicate_ledger_case_ids}")
    if ledger_commit_by_case_id != patched_page_commit_by_case_id:
        patch_commit_errors.append(
            "patch commit ledger entries do not match patched_confirmed page result commits: "
            f"expected={patched_page_commit_by_case_id}, ledger={ledger_commit_by_case_id}"
        )
    add_check(
        "patch commit ledger matches patched-confirmed page count",
        not patch_commit_errors,
        {
            "patched_confirmed_count": expected_patch_commit_count,
            "commit_count": patch_commit_count,
            "commit_shas": patch_commit_shas,
            "duplicate_commit_shas": (patch_commit_ledger or {}).get("duplicate_commit_shas"),
            "patched_page_commit_by_case_id": patched_page_commit_by_case_id,
            "ledger_commit_by_case_id": ledger_commit_by_case_id,
            "duplicate_ledger_case_ids": duplicate_ledger_case_ids,
        },
        patch_commit_errors,
    )
    add_check(
        "patch commit ledger bundle is packageable",
        bool(patch_commit_ledger_zip_validation and patch_commit_ledger_zip_validation.get("ok") is True),
        patch_commit_ledger_zip_validation,
        package_validation_errors(patch_commit_ledger_zip_validation),
    )
    add_check(
        "harness review bundle is packageable",
        bool(harness_review_bundle_validation and harness_review_bundle_validation.get("ok") is True),
        harness_review_bundle_validation,
        package_validation_errors(harness_review_bundle_validation),
    )

    failed_checks = [check for check in checks if not check["ok"]]
    return {
        "schema": "pdf_lab.second_pass.harness_readiness_audit.v1",
        "created_at": utc_now(),
        "out_dir": str(out_dir),
        "ok": not failed_checks,
        "failed_count": len(failed_checks),
        "failed_requirements": [check["requirement"] for check in failed_checks],
        "checks": checks,
    }


def validate_harness_page_terminal_ledger(case_dir: Path, terminal: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if terminal.get("schema") != "pdf_lab.second_pass.page_terminal_ledger.v1":
        errors.append("terminal ledger schema mismatch")
    terminal_status = terminal.get("terminal_status")
    if terminal_status not in TERMINAL_PAGE_STATUSES:
        errors.append(f"invalid terminal_status: {terminal_status}")
    case_id = terminal.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        errors.append("missing case_id")
    page_number = terminal.get("page_number")
    if not is_plain_int(page_number) or page_number < 1:
        errors.append("missing integer page_number")
    if not isinstance(terminal.get("reason"), str) or not terminal.get("reason", "").strip():
        errors.append("missing terminal reason")
    evidence_artifacts = terminal.get("evidence_artifacts")
    if not isinstance(evidence_artifacts, list) or not all(isinstance(item, str) and item for item in evidence_artifacts):
        errors.append("evidence_artifacts must be a list of artifact names")
        evidence_artifacts = []
    duplicate_evidence_artifacts = sorted(
        artifact for artifact, count in Counter(evidence_artifacts).items() if count > 1
    )
    if duplicate_evidence_artifacts:
        errors.append(f"evidence_artifacts contains duplicate artifact names: {duplicate_evidence_artifacts}")
    unsafe_evidence_artifacts = sorted(
        artifact
        for artifact in evidence_artifacts
        if Path(artifact).is_absolute() or ".." in Path(artifact).parts
    )
    if unsafe_evidence_artifacts:
        errors.append(f"evidence_artifacts contains unsafe artifact paths: {unsafe_evidence_artifacts}")
    missing_artifacts = sorted(
        artifact
        for artifact in evidence_artifacts
        if artifact != "terminal_ledger_validation.json"
        and artifact not in unsafe_evidence_artifacts
        and not (case_dir / artifact).is_file()
    )
    if missing_artifacts:
        errors.append(f"declared evidence artifacts are missing: {missing_artifacts}")
    if terminal_status == "patched_confirmed":
        commit_sha = terminal.get("commit_sha")
        if not isinstance(commit_sha, str) or not commit_sha:
            errors.append("patched_confirmed terminal ledger missing commit_sha")
        if terminal.get("commit_gate_ok") is not True:
            errors.append("patched_confirmed terminal ledger requires commit_gate_ok true")
        if terminal.get("commit_exact_file_match") is not True:
            errors.append("patched_confirmed terminal ledger requires commit_exact_file_match true")
        if terminal.get("commit_revertability_ok") is not True:
            errors.append("patched_confirmed terminal ledger requires commit_revertability_ok true")
        if terminal.get("commit_acceptance_ok") is not True:
            errors.append("patched_confirmed terminal ledger requires commit_acceptance_ok true")
        for artifact in sorted(REQUIRED_PATCHED_CONFIRMED_ARTIFACTS):
            if artifact not in evidence_artifacts:
                errors.append(f"patched_confirmed terminal ledger missing {artifact}")
            elif not (case_dir / artifact).is_file():
                errors.append(f"patched_confirmed terminal ledger artifact missing on disk: {artifact}")
    else:
        if terminal.get("commit_sha") is not None:
            errors.append(f"{terminal_status} terminal ledger must not carry commit_sha")
    return {
        "schema": "pdf_lab.second_pass.page_terminal_ledger_validation.v1",
        "ok": not errors,
        "errors": errors,
        "case_id": terminal.get("case_id"),
        "page_number": terminal.get("page_number"),
        "terminal_status": terminal_status,
        "declared_evidence_count": len(evidence_artifacts),
        "duplicate_evidence_artifacts": duplicate_evidence_artifacts,
        "unsafe_evidence_artifacts": unsafe_evidence_artifacts,
        "missing_artifacts": missing_artifacts,
    }


def validate_harness_page_review_bundle(case_dir: Path, zip_path: Path, terminal: dict[str, Any]) -> dict[str, Any]:
    evidence_artifacts = terminal.get("evidence_artifacts")
    if not isinstance(evidence_artifacts, list):
        evidence_artifacts = []
    evidence_artifacts = [artifact for artifact in evidence_artifacts if isinstance(artifact, str) and artifact]
    duplicate_evidence_artifacts = sorted(
        artifact for artifact, count in Counter(evidence_artifacts).items() if count > 1
    )
    unsafe_evidence_artifacts = sorted(
        artifact
        for artifact in evidence_artifacts
        if Path(artifact).is_absolute() or ".." in Path(artifact).parts
    )
    safe_evidence_artifacts = [
        artifact for artifact in evidence_artifacts if artifact not in unsafe_evidence_artifacts
    ]
    required_zip_entries = sorted(
        {
            "terminal_ledger.json",
            "terminal_ledger_validation.json",
            "review.html",
            *safe_evidence_artifacts,
        }
    )
    missing_artifacts = sorted(
        artifact for artifact in required_zip_entries if not (case_dir / artifact).is_file()
    )
    zip_entries: list[str] = []
    duplicate_zip_entries: list[str] = []
    mismatched_zip_entries: list[str] = []
    errors: list[str] = []
    if duplicate_evidence_artifacts:
        errors.append(f"terminal evidence_artifacts contains duplicate artifact names: {duplicate_evidence_artifacts}")
    if unsafe_evidence_artifacts:
        errors.append(f"terminal evidence_artifacts contains unsafe artifact paths: {unsafe_evidence_artifacts}")
    terminal_ledger_matches_argument = False
    terminal_ledger_validation_matches_recomputed = False
    terminal_ledger_validation_ok = False
    terminal_ledger_path = case_dir / "terminal_ledger.json"
    if terminal_ledger_path.is_file():
        try:
            terminal_ledger_payload = json.loads(terminal_ledger_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - malformed evidence is a validation failure.
            errors.append(f"terminal_ledger.json unreadable: {type(exc).__name__}: {exc}")
        else:
            if terminal_ledger_payload == terminal:
                terminal_ledger_matches_argument = True
            else:
                errors.append("terminal_ledger.json does not match terminal argument")
    terminal_ledger_validation_path = case_dir / "terminal_ledger_validation.json"
    if terminal_ledger_matches_argument and terminal_ledger_validation_path.is_file():
        try:
            terminal_ledger_validation_payload = json.loads(terminal_ledger_validation_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - malformed evidence is a validation failure.
            errors.append(f"terminal_ledger_validation.json unreadable: {type(exc).__name__}: {exc}")
        else:
            recomputed_terminal_validation = validate_harness_page_terminal_ledger(case_dir, terminal)
            if terminal_ledger_validation_payload == recomputed_terminal_validation:
                terminal_ledger_validation_matches_recomputed = True
                terminal_ledger_validation_ok = recomputed_terminal_validation.get("ok") is True
            else:
                errors.append("terminal_ledger_validation.json does not match recomputed terminal validation")
            if recomputed_terminal_validation.get("ok") is not True:
                errors.append("terminal_ledger_validation ok is not true")
    if not zip_path.is_file():
        errors.append("review bundle zip is missing")
    else:
        with zipfile.ZipFile(zip_path) as bundle:
            zip_entries = bundle.namelist()
            for artifact in required_zip_entries:
                source = case_dir / artifact
                if artifact in zip_entries and source.is_file():
                    if bundle.read(artifact) != source.read_bytes():
                        mismatched_zip_entries.append(artifact)
        entry_counts = Counter(zip_entries)
        duplicate_zip_entries = sorted(entry for entry, count in entry_counts.items() if count > 1)
        if duplicate_zip_entries:
            errors.append(f"duplicate zip entries: {duplicate_zip_entries}")
    missing_expected_zip_entries = sorted(entry for entry in required_zip_entries if entry not in set(zip_entries))
    if missing_artifacts:
        errors.append(f"required bundle artifacts are missing from case dir: {missing_artifacts}")
    if missing_expected_zip_entries:
        errors.append(f"required bundle artifacts are missing from zip: {missing_expected_zip_entries}")
    if mismatched_zip_entries:
        errors.append(f"required bundle artifacts differ between case dir and zip: {sorted(mismatched_zip_entries)}")
    zip_content_ok = (
        zip_path.is_file()
        and not missing_expected_zip_entries
        and not duplicate_evidence_artifacts
        and not unsafe_evidence_artifacts
        and not duplicate_zip_entries
        and not mismatched_zip_entries
    )
    return {
        "schema": "pdf_lab.second_pass.page_review_bundle_validation.v1",
        "ok": not errors,
        "errors": errors,
        "case_id": terminal.get("case_id"),
        "page_number": terminal.get("page_number"),
        "zip_path": str(zip_path),
        "required_zip_entries": required_zip_entries,
        "zip_entry_count": len(zip_entries),
        "zip_content_ok": zip_content_ok,
        "terminal_ledger_matches_argument": terminal_ledger_matches_argument,
        "terminal_ledger_validation_matches_recomputed": terminal_ledger_validation_matches_recomputed,
        "terminal_ledger_validation_ok": terminal_ledger_validation_ok,
        "missing_artifacts": missing_artifacts,
        "missing_expected_zip_entries": missing_expected_zip_entries,
        "duplicate_evidence_artifacts": duplicate_evidence_artifacts,
        "unsafe_evidence_artifacts": unsafe_evidence_artifacts,
        "duplicate_zip_entries": duplicate_zip_entries,
        "mismatched_zip_entries": sorted(mismatched_zip_entries),
    }


def _write_blocked_case_result(
    *,
    out_dir: Path,
    case: dict[str, Any],
    reason: str,
    visibility: dict[str, Any],
    extra_artifacts: dict[str, Path] | None = None,
) -> dict[str, Any]:
    case_dir = out_dir / "page_cases" / str(case["case_id"])
    case_dir.mkdir(parents=True, exist_ok=True)
    write_json(case_dir / "scillm_code_root_visibility.json", visibility)
    evidence_artifacts = ["scillm_code_root_visibility.json", "review.html", "terminal_ledger_validation.json"]
    for artifact_name, source_path in (extra_artifacts or {}).items():
        if source_path.is_file():
            target = case_dir / artifact_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source_path.read_bytes())
            evidence_artifacts.insert(-1, artifact_name)
    review_html = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>pdf-lab blocked case</title></head>"
        "<body><main><h1>pdf-lab blocked case</h1>"
        f"<p>Case: {case['case_id']}</p>"
        f"<p>Reason: {reason}</p>"
        "</main></body></html>"
    )
    (case_dir / "review.html").write_text(review_html, encoding="utf-8")
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": case["case_id"],
        "page_number": case["page_number"],
        "terminal_status": "blocked_substrate",
        "reason": reason,
        "allowed_terminal_statuses": sorted(TERMINAL_PAGE_STATUSES),
        "evidence_artifacts": evidence_artifacts,
        "commit_sha": None,
    }
    write_json(case_dir / "terminal_ledger.json", terminal)
    terminal_validation = validate_harness_page_terminal_ledger(case_dir, terminal)
    write_json(case_dir / "terminal_ledger_validation.json", terminal_validation)
    with zipfile.ZipFile(case_dir / "review_bundle.zip", "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in ["terminal_ledger.json", *evidence_artifacts]:
            bundle.write(case_dir / name, arcname=name)
    review_bundle_validation = validate_harness_page_review_bundle(case_dir, case_dir / "review_bundle.zip", terminal)
    write_json(case_dir / "review_bundle_validation.json", review_bundle_validation)
    return {
        "case_id": case["case_id"],
        "page_number": case["page_number"],
        "terminal_status": "blocked_substrate",
        "reason": reason,
        "case_dir": str(case_dir),
        "terminal_ledger": str(case_dir / "terminal_ledger.json"),
        "review_bundle": str(case_dir / "review_bundle.zip"),
        "review_bundle_validation": str(case_dir / "review_bundle_validation.json"),
        "review_bundle_validation_ok": review_bundle_validation["ok"],
        "review_bundle_zip_content_ok": review_bundle_validation["zip_content_ok"],
        "commit_sha": None,
        "evidence_artifacts": terminal["evidence_artifacts"],
        "terminal_ledger_validation": str(case_dir / "terminal_ledger_validation.json"),
        "terminal_ledger_validation_ok": terminal_validation["ok"],
    }


def build_opencode_completion_canary_request(
    *,
    code_root: Path,
    agent: str,
    skills: list[str],
    timeout_s: float,
    cleanup_session: bool,
    model: str | None,
) -> dict[str, Any]:
    canary_relpath = ".pdf_lab_write_canary/opencode_write_canary.txt"
    prompt = (
        "You are a pdf-lab OpenCode write-capability canary. "
        f"Create or overwrite exactly one file at {canary_relpath} with exactly this single line: "
        "PDF_LAB_OPENCODE_WRITE_CANARY_OK\n"
        "Do not edit, create, delete, move, stage, or commit any other file. "
        "Return one concise assistant_text line that starts with PDF_LAB_CANARY_OK and names the file written."
    )
    request: dict[str, Any] = {
        "schema": "pdf_lab.second_pass.opencode_completion_canary_request.v1",
        "endpoint": "POST /v1/scillm/opencode/runs",
        "agent": agent,
        "skills": skills,
        "timeout_s": timeout_s,
        "cleanup_session": cleanup_session,
        "cwd": str(code_root.resolve()),
        "prompt": prompt,
        "scillm_metadata": {
            "graph_node": "opencode_completion_canary",
            "caller": "pdf-lab",
            "canary_relpath": canary_relpath,
        },
    }
    if model:
        request["model"] = model
        request["opencode_model"] = model
    return request


def validate_opencode_completion_canary_receipt(
    receipt: dict[str, Any] | None,
    *,
    code_root: Path | None = None,
    canary_relpath: str = ".pdf_lab_write_canary/opencode_write_canary.txt",
) -> dict[str, Any]:
    raw = receipt.get("raw_response") if isinstance(receipt, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    status = raw.get("status")
    assistant_text = str(raw.get("assistant_text") or raw.get("output") or raw.get("text") or "")
    sentinel_path = code_root / canary_relpath if code_root is not None else None
    sentinel_text = sentinel_path.read_text(encoding="utf-8") if sentinel_path is not None and sentinel_path.is_file() else None
    errors: list[str] = []
    if status not in {"completed", "success", "ok"}:
        errors.append(f"OpenCode completion canary status is not completed/success/ok: {status}")
        if status == "timeout":
            errors.append("OpenCode completion canary timed out before producing assistant_text")
    if not assistant_text.strip():
        errors.append("OpenCode completion canary produced no assistant_text")
    if not assistant_text.strip().startswith("PDF_LAB_CANARY_OK"):
        errors.append("OpenCode completion canary assistant_text did not start with PDF_LAB_CANARY_OK")
    if sentinel_path is None:
        errors.append("OpenCode completion canary did not receive code_root for write validation")
    elif not sentinel_path.is_file():
        errors.append(f"OpenCode completion canary did not create sentinel file: {canary_relpath}")
    expected_sentinel = "PDF_LAB_OPENCODE_WRITE_CANARY_OK"
    sentinel_content_ok = sentinel_text in {expected_sentinel, f"{expected_sentinel}\n"}
    if sentinel_path is None:
        pass
    elif not sentinel_path.is_file():
        pass
    elif not sentinel_content_ok:
        errors.append("OpenCode completion canary sentinel file content mismatch")
    diff_present = bool(raw.get("diff"))
    if not diff_present:
        errors.append("OpenCode completion canary produced no patch diff evidence")
    return {
        "schema": "pdf_lab.second_pass.opencode_completion_canary_validation.v1",
        "ok": not errors,
        "errors": errors,
        "status": status or "unknown",
        "assistant_text_present": bool(assistant_text.strip()),
        "sentinel_present": assistant_text.strip().startswith("PDF_LAB_CANARY_OK"),
        "write_sentinel_path": str(sentinel_path) if sentinel_path is not None else None,
        "write_sentinel_present": bool(sentinel_path is not None and sentinel_path.is_file()),
        "write_sentinel_content_ok": sentinel_content_ok,
        "diff_present": diff_present,
    }


def cleanup_opencode_completion_canary_file(
    *,
    code_root: Path,
    canary_relpath: str = ".pdf_lab_write_canary/opencode_write_canary.txt",
) -> dict[str, Any]:
    sentinel_path = code_root / canary_relpath
    canary_dir = sentinel_path.parent
    errors: list[str] = []
    removed_file = False
    removed_dir = False
    try:
        if sentinel_path.is_file():
            sentinel_path.unlink()
            removed_file = True
        if canary_dir.exists() and canary_dir.is_dir() and not any(canary_dir.iterdir()):
            canary_dir.rmdir()
            removed_dir = True
    except Exception as exc:  # noqa: BLE001 - cleanup evidence must show substrate failures.
        errors.append(f"{type(exc).__name__}: {exc}")
    remaining_status = git_status_short(code_root)
    if any(item.startswith("?? .pdf_lab_write_canary") or ".pdf_lab_write_canary" in item for item in remaining_status):
        errors.append(f"OpenCode completion canary cleanup left git status entries: {remaining_status}")
    return {
        "schema": "pdf_lab.second_pass.opencode_completion_canary_cleanup.v1",
        "ok": not errors,
        "errors": errors,
        "sentinel_path": str(sentinel_path),
        "removed_file": removed_file,
        "removed_dir": removed_dir,
        "git_status_after_cleanup": remaining_status,
    }


def run_opencode_completion_canary(
    *,
    out_dir: Path,
    page_dag: Any,
    code_root: Path,
    patch_mode: str,
    patch_backend: str,
    scillm_base_url: str,
    scillm_auth_token: str,
    caller_skill: str,
    agent: str,
    skills: list[str] | None,
    timeout_s: float,
    cleanup_session: bool,
    model: str | None,
) -> dict[str, Any] | None:
    if patch_mode != "live" or patch_backend != "opencode_serve":
        return None
    canary_dir = out_dir / "opencode_completion_canary"
    canary_dir.mkdir(parents=True, exist_ok=True)
    request = build_opencode_completion_canary_request(
        code_root=code_root,
        agent=agent,
        skills=skills or page_dag.DEFAULT_OPENCODE_SKILLS,
        timeout_s=timeout_s,
        cleanup_session=cleanup_session,
        model=model,
    )
    write_json(canary_dir / "opencode_completion_canary_request.json", request)
    receipt: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    try:
        receipt = page_dag.call_opencode_patch(
            request,
            base_url=scillm_base_url,
            auth_token=scillm_auth_token,
            caller_skill=caller_skill,
            timeout_s=timeout_s + 30,
        )
        write_json(canary_dir / "opencode_completion_canary_receipt.json", receipt)
        page_dag.materialize_opencode_host_artifacts(canary_dir, receipt, prefix="canary_")
        validation = validate_opencode_completion_canary_receipt(receipt, code_root=code_root)
    except Exception as exc:  # noqa: BLE001 - live substrate failures must be ledgered.
        error = {
            "schema": "pdf_lab.second_pass.substrate_error.v1",
            "node_id": "opencode_completion_canary",
            "endpoint": "POST /v1/scillm/opencode/runs",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        write_json(canary_dir / "opencode_completion_canary_error.json", error)
        validation = {
            "schema": "pdf_lab.second_pass.opencode_completion_canary_validation.v1",
            "ok": False,
            "errors": ["opencode_completion_canary_call_failed"],
            "status": "substrate_error",
            "assistant_text_present": False,
            "sentinel_present": False,
            "diff_present": False,
        }
    write_json(canary_dir / "opencode_completion_canary_validation.json", validation)
    cleanup = cleanup_opencode_completion_canary_file(code_root=code_root)
    write_json(canary_dir / "opencode_completion_canary_cleanup.json", cleanup)
    if not cleanup["ok"]:
        validation["ok"] = False
        validation.setdefault("errors", []).extend(cleanup["errors"])
        write_json(canary_dir / "opencode_completion_canary_validation.json", validation)
    canary = {
        "schema": "pdf_lab.second_pass.opencode_completion_canary.v1",
        "ok": bool(validation.get("ok")),
        "code_root": str(code_root.resolve()),
        "patch_mode": patch_mode,
        "patch_backend": patch_backend,
        "request_artifact": str(canary_dir / "opencode_completion_canary_request.json"),
        "receipt_artifact": str(canary_dir / "opencode_completion_canary_receipt.json") if receipt is not None else None,
        "error_artifact": str(canary_dir / "opencode_completion_canary_error.json") if error is not None else None,
        "validation_artifact": str(canary_dir / "opencode_completion_canary_validation.json"),
        "cleanup_artifact": str(canary_dir / "opencode_completion_canary_cleanup.json"),
        "errors": validation.get("errors") or [],
        "status": validation.get("status"),
    }
    write_json(canary_dir / "opencode_completion_canary.json", canary)
    return canary


def opencode_completion_canary_artifacts(out_dir: Path, canary: dict[str, Any] | None = None) -> dict[str, Path]:
    if not canary:
        return {}
    canary_dir = out_dir / "opencode_completion_canary"
    artifacts = {
        "opencode_completion_canary.json": canary_dir / "opencode_completion_canary.json",
        "opencode_completion_canary_request.json": canary_dir / "opencode_completion_canary_request.json",
        "opencode_completion_canary_validation.json": canary_dir / "opencode_completion_canary_validation.json",
        "opencode_completion_canary_cleanup.json": canary_dir / "opencode_completion_canary_cleanup.json",
    }
    optional_artifacts = {
        "receipt_artifact": "opencode_completion_canary_receipt.json",
        "error_artifact": "opencode_completion_canary_error.json",
    }
    for field, name in optional_artifacts.items():
        path = canary_dir / name
        if canary.get(field) or path.is_file():
            artifacts[name] = path
    for name in [
        "canary_opencode_host_status.json",
        "canary_opencode_host_result.json",
        "canary_opencode_host_events.jsonl",
        "canary_opencode_host_artifacts_summary.json",
    ]:
        path = canary_dir / name
        if path.is_file():
            artifacts[name] = path
    return artifacts


def build_scillm_transport_readonly_canary_request(
    *,
    code_root: Path,
    agent: str,
    skills: list[str],
    timeout_s: float,
    model: str | None,
) -> dict[str, Any]:
    prompt = (
        "You are a read-only pdf-lab scillm transport canary. "
        "Do not edit, create, delete, move, stage, or commit any file. "
        "Run at most read-only inspection commands such as pwd and git status --short. "
        "Return assistant_text that starts with PDF_LAB_TRANSPORT_CANARY_OK and names the workspace root. "
        "The diff must remain empty."
    )
    request: dict[str, Any] = {
        "schema": "pdf_lab.second_pass.scillm_transport_readonly_canary_request.v1",
        "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
        "agent": agent,
        "opencode_model": model,
        "role": "diagnose",
        "child_mode": "read_only",
        "skills": skills,
        "timeout_s": timeout_s,
        "cwd": str(code_root.resolve()),
        "create_run_body": {
            "dag_node_id": "pdf_lab_transport_readonly_canary",
            "workspace": str(code_root.resolve()),
            "title": "pdf-lab transport read-only canary",
        },
        "create_child_body": {
            "role": "diagnose",
            "agent": agent,
            "mode": "read_only",
            "title": "pdf-lab transport canary",
            "skills": skills,
        },
        "message_body": {
            "prompt": prompt,
            "agent": agent,
            "role": "diagnose",
            "stream": True,
            "timeout_s": timeout_s,
            "heartbeat_s": 15,
            "wait_idle": True,
            "skills": skills,
        },
        "scillm_metadata": {
            "graph_node": "scillm_transport_readonly_canary",
            "caller": "pdf-lab",
        },
    }
    if model:
        request["message_body"]["model"] = model
    return request


def git_status_short(repo: Path) -> list[str]:
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--short"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if status.returncode != 0:
        return [f"git status failed: {status.stderr.strip() or status.stdout.strip()}"]
    return [line for line in status.stdout.splitlines() if line.strip()]


def validate_scillm_transport_readonly_canary_receipt(
    receipt: dict[str, Any] | None,
    *,
    worktree_status: list[str],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    raw = receipt.get("message_response") if isinstance(receipt, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    event_stream = receipt.get("event_stream") if isinstance(receipt, dict) else {}
    if not isinstance(event_stream, dict):
        event_stream = {}
    delivery_state = raw.get("delivery_state") or raw.get("status") or event_stream.get("delivery_state")
    saw_message_completed = event_stream.get("saw_message_completed") is True
    assistant_text = str(raw.get("assistant_text") or raw.get("output") or raw.get("text") or "")
    diff = raw.get("diff")
    if delivery_state != "completed":
        errors.append(f"transport read-only canary delivery_state is not completed: {delivery_state}")
    if not assistant_text.strip():
        errors.append("transport read-only canary produced no assistant_text")
    if not assistant_text.strip().startswith("PDF_LAB_TRANSPORT_CANARY_OK"):
        errors.append("transport read-only canary assistant_text did not start with PDF_LAB_TRANSPORT_CANARY_OK")
    if diff not in (None, "", [], {}):
        errors.append("transport read-only canary produced a non-empty diff")
    if worktree_status:
        errors.append(f"transport read-only canary left non-clean worktree status: {worktree_status}")
    return {
        "schema": "pdf_lab.second_pass.scillm_transport_readonly_canary_validation.v1",
        "ok": not errors,
        "errors": errors,
        "delivery_state": delivery_state or "unknown",
        "assistant_text_present": bool(assistant_text.strip()),
        "sentinel_present": assistant_text.strip().startswith("PDF_LAB_TRANSPORT_CANARY_OK"),
        "diff_present": diff not in (None, "", [], {}),
        "worktree_status": worktree_status,
    }


def build_scillm_transport_write_canary_request(
    *,
    code_root: Path,
    agent: str,
    skills: list[str],
    timeout_s: float,
    model: str | None,
) -> dict[str, Any]:
    canary_relpath = ".pdf_lab_write_canary/scillm_transport_write_canary.txt"
    prompt = (
        "You are a pdf-lab scillm transport write-capability canary. "
        f"Create or overwrite exactly one file at {canary_relpath} with exactly this single line: "
        "PDF_LAB_SCILLM_TRANSPORT_WRITE_CANARY_OK\n"
        "Do not edit, create, delete, move, stage, or commit any other file. "
        "Return assistant_text that starts with PDF_LAB_TRANSPORT_WRITE_CANARY_OK and names the file written."
    )
    request: dict[str, Any] = {
        "schema": "pdf_lab.second_pass.scillm_transport_write_canary_request.v1",
        "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
        "agent": agent,
        "opencode_model": model,
        "role": "patch",
        "child_mode": "patch",
        "skills": skills,
        "timeout_s": timeout_s,
        "cwd": str(code_root.resolve()),
        "canary_relpath": canary_relpath,
        "create_run_body": {
            "dag_node_id": "pdf_lab_transport_write_canary",
            "workspace": str(code_root.resolve()),
            "title": "pdf-lab transport write canary",
        },
        "create_child_body": {
            "role": "patch",
            "agent": agent,
            "mode": "patch",
            "title": "pdf-lab transport write canary",
            "skills": skills,
        },
        "message_body": {
            "prompt": prompt,
            "agent": agent,
            "role": "patch",
            "stream": True,
            "timeout_s": timeout_s,
            "heartbeat_s": 15,
            "wait_idle": True,
            "skills": skills,
        },
        "scillm_metadata": {
            "graph_node": "scillm_transport_write_canary",
            "caller": "pdf-lab",
            "canary_relpath": canary_relpath,
        },
    }
    if model:
        request["message_body"]["model"] = model
    return request


def validate_scillm_transport_write_canary_receipt(
    receipt: dict[str, Any] | None,
    *,
    code_root: Path,
    canary_relpath: str = ".pdf_lab_write_canary/scillm_transport_write_canary.txt",
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    raw = receipt.get("message_response") if isinstance(receipt, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    event_stream = receipt.get("event_stream") if isinstance(receipt, dict) else {}
    if not isinstance(event_stream, dict):
        event_stream = {}
    delivery_state = raw.get("delivery_state") or raw.get("status") or event_stream.get("delivery_state")
    saw_message_completed = event_stream.get("saw_message_completed") is True
    assistant_text = str(raw.get("assistant_text") or raw.get("output") or raw.get("text") or "")
    diff = raw.get("diff")
    diff_present = diff not in (None, "", [], {})
    sentinel_path = code_root / canary_relpath
    sentinel_text = sentinel_path.read_text(encoding="utf-8") if sentinel_path.is_file() else None
    if delivery_state != "completed":
        errors.append(f"transport write canary delivery_state is not completed: {delivery_state}")
    if not assistant_text.strip():
        errors.append("transport write canary produced no assistant_text")
    if not assistant_text.strip().startswith("PDF_LAB_TRANSPORT_WRITE_CANARY_OK"):
        errors.append("transport write canary assistant_text did not start with PDF_LAB_TRANSPORT_WRITE_CANARY_OK")
    if not sentinel_path.is_file():
        errors.append(f"transport write canary did not create sentinel file: {canary_relpath}")
    elif sentinel_text != "PDF_LAB_SCILLM_TRANSPORT_WRITE_CANARY_OK\n":
        errors.append("transport write canary sentinel file content mismatch")
    if not diff_present:
        errors.append("transport write canary produced no patch diff evidence")
    for session_error in event_stream.get("session_errors") or []:
        if isinstance(session_error, dict):
            error_type = session_error.get("error_type") or "unknown"
            error = session_error.get("error") or session_error
            errors.append(f"transport write canary session_error {error_type}: {error}")
    replay_error = event_stream.get("event_replay_error")
    if isinstance(replay_error, dict):
        error_type = replay_error.get("error_type") or "unknown"
        error = replay_error.get("error") or replay_error
        message = f"transport write canary event replay error {error_type}: {error}"
        if delivery_state == "completed" and saw_message_completed:
            warnings.append(message)
        else:
            errors.append(message)
    return {
        "schema": "pdf_lab.second_pass.scillm_transport_write_canary_validation.v1",
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "delivery_state": delivery_state or "unknown",
        "assistant_text_present": bool(assistant_text.strip()),
        "sentinel_present": assistant_text.strip().startswith("PDF_LAB_TRANSPORT_WRITE_CANARY_OK"),
        "diff_present": diff_present,
        "write_sentinel_path": str(sentinel_path),
        "write_sentinel_present": sentinel_path.is_file(),
        "write_sentinel_content_ok": sentinel_text == "PDF_LAB_SCILLM_TRANSPORT_WRITE_CANARY_OK\n",
    }


def run_scillm_transport_write_canary(
    *,
    out_dir: Path,
    page_dag: Any,
    code_root: Path,
    patch_mode: str,
    patch_backend: str,
    scillm_base_url: str,
    scillm_auth_token: str,
    caller_skill: str,
    agent: str,
    skills: list[str] | None,
    timeout_s: float,
    model: str | None,
) -> dict[str, Any] | None:
    if patch_mode != "live" or patch_backend != "scillm_orchestrator":
        return None
    canary_dir = out_dir / "scillm_transport_write_canary"
    canary_dir.mkdir(parents=True, exist_ok=True)
    request = build_scillm_transport_write_canary_request(
        code_root=code_root,
        agent=agent,
        skills=skills or page_dag.DEFAULT_OPENCODE_SKILLS,
        timeout_s=timeout_s,
        model=model,
    )
    write_json(canary_dir / "scillm_transport_write_canary_request.json", request)
    receipt: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    try:
        receipt = page_dag.call_scillm_orchestrator_patch(
            request,
            base_url=scillm_base_url,
            auth_token=scillm_auth_token,
            caller_skill=caller_skill,
            timeout_s=timeout_s + 30,
        )
        write_json(canary_dir / "scillm_transport_write_canary_receipt.json", receipt)
        if isinstance(receipt.get("event_stream"), dict):
            write_json(canary_dir / "scillm_transport_write_canary_event_stream.json", receipt["event_stream"])
        validation = validate_scillm_transport_write_canary_receipt(
            receipt,
            code_root=code_root,
            canary_relpath=request["canary_relpath"],
        )
    except Exception as exc:  # noqa: BLE001 - live substrate failures must be ledgered.
        error = {
            "schema": "pdf_lab.second_pass.substrate_error.v1",
            "node_id": "scillm_transport_write_canary",
            "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        write_json(canary_dir / "scillm_transport_write_canary_error.json", error)
        validation = {
            "schema": "pdf_lab.second_pass.scillm_transport_write_canary_validation.v1",
            "ok": False,
            "errors": ["scillm_transport_write_canary_call_failed"],
            "delivery_state": "substrate_error",
            "assistant_text_present": False,
            "sentinel_present": False,
            "diff_present": False,
            "write_sentinel_path": str(code_root / request["canary_relpath"]),
            "write_sentinel_present": False,
            "write_sentinel_content_ok": False,
        }
    write_json(canary_dir / "scillm_transport_write_canary_validation.json", validation)
    cleanup = cleanup_opencode_completion_canary_file(
        code_root=code_root,
        canary_relpath=request["canary_relpath"],
    )
    write_json(canary_dir / "scillm_transport_write_canary_cleanup.json", cleanup)
    if not cleanup["ok"]:
        validation["ok"] = False
        validation.setdefault("errors", []).extend(cleanup["errors"])
        write_json(canary_dir / "scillm_transport_write_canary_validation.json", validation)
    canary = {
        "schema": "pdf_lab.second_pass.scillm_transport_write_canary.v1",
        "ok": bool(validation.get("ok")),
        "code_root": str(code_root.resolve()),
        "patch_mode": patch_mode,
        "patch_backend": patch_backend,
        "request_artifact": str(canary_dir / "scillm_transport_write_canary_request.json"),
        "receipt_artifact": str(canary_dir / "scillm_transport_write_canary_receipt.json") if receipt is not None else None,
        "error_artifact": str(canary_dir / "scillm_transport_write_canary_error.json") if error is not None else None,
        "validation_artifact": str(canary_dir / "scillm_transport_write_canary_validation.json"),
        "cleanup_artifact": str(canary_dir / "scillm_transport_write_canary_cleanup.json"),
        "event_stream_artifact": str(canary_dir / "scillm_transport_write_canary_event_stream.json")
        if (canary_dir / "scillm_transport_write_canary_event_stream.json").is_file()
        else None,
        "errors": validation.get("errors") or [],
        "status": validation.get("delivery_state"),
    }
    write_json(canary_dir / "scillm_transport_write_canary.json", canary)
    return canary


def scillm_transport_write_canary_artifacts(out_dir: Path, canary: dict[str, Any] | None = None) -> dict[str, Path]:
    if not canary:
        return {}
    canary_dir = out_dir / "scillm_transport_write_canary"
    artifacts = {
        "scillm_transport_write_canary.json": canary_dir / "scillm_transport_write_canary.json",
        "scillm_transport_write_canary_request.json": canary_dir / "scillm_transport_write_canary_request.json",
        "scillm_transport_write_canary_validation.json": canary_dir / "scillm_transport_write_canary_validation.json",
        "scillm_transport_write_canary_cleanup.json": canary_dir / "scillm_transport_write_canary_cleanup.json",
    }
    optional_artifacts = {
        "receipt_artifact": "scillm_transport_write_canary_receipt.json",
        "error_artifact": "scillm_transport_write_canary_error.json",
        "event_stream_artifact": "scillm_transport_write_canary_event_stream.json",
    }
    for field, name in optional_artifacts.items():
        path = canary_dir / name
        if canary.get(field) or path.is_file():
            artifacts[name] = path
    return artifacts


def run_scillm_transport_readonly_canary(
    *,
    out_dir: Path,
    page_dag: Any,
    code_root: Path,
    patch_mode: str,
    patch_backend: str,
    scillm_base_url: str,
    scillm_auth_token: str,
    caller_skill: str,
    agent: str,
    skills: list[str] | None,
    timeout_s: float,
    model: str | None,
) -> dict[str, Any] | None:
    if patch_mode != "live" or patch_backend != "scillm_orchestrator":
        return None
    canary_dir = out_dir / "scillm_transport_readonly_canary"
    canary_dir.mkdir(parents=True, exist_ok=True)
    request = build_scillm_transport_readonly_canary_request(
        code_root=code_root,
        agent=agent,
        skills=skills or page_dag.DEFAULT_OPENCODE_SKILLS,
        timeout_s=timeout_s,
        model=model,
    )
    write_json(canary_dir / "scillm_transport_readonly_canary_request.json", request)
    receipt: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    try:
        receipt = page_dag.call_scillm_orchestrator_patch(
            request,
            base_url=scillm_base_url,
            auth_token=scillm_auth_token,
            caller_skill=caller_skill,
            timeout_s=timeout_s + 30,
        )
        write_json(canary_dir / "scillm_transport_readonly_canary_receipt.json", receipt)
        if isinstance(receipt.get("event_stream"), dict):
            write_json(canary_dir / "scillm_transport_readonly_canary_event_stream.json", receipt["event_stream"])
        worktree_status = git_status_short(code_root)
        validation = validate_scillm_transport_readonly_canary_receipt(
            receipt,
            worktree_status=worktree_status,
        )
    except Exception as exc:  # noqa: BLE001 - live substrate failures must be ledgered.
        error = {
            "schema": "pdf_lab.second_pass.substrate_error.v1",
            "node_id": "scillm_transport_readonly_canary",
            "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        write_json(canary_dir / "scillm_transport_readonly_canary_error.json", error)
        validation = {
            "schema": "pdf_lab.second_pass.scillm_transport_readonly_canary_validation.v1",
            "ok": False,
            "errors": ["scillm_transport_readonly_canary_call_failed"],
            "delivery_state": "substrate_error",
            "assistant_text_present": False,
            "sentinel_present": False,
            "diff_present": False,
            "worktree_status": [],
        }
    write_json(canary_dir / "scillm_transport_readonly_canary_validation.json", validation)
    canary = {
        "schema": "pdf_lab.second_pass.scillm_transport_readonly_canary.v1",
        "ok": bool(validation.get("ok")),
        "code_root": str(code_root.resolve()),
        "patch_mode": patch_mode,
        "patch_backend": patch_backend,
        "request_artifact": str(canary_dir / "scillm_transport_readonly_canary_request.json"),
        "receipt_artifact": str(canary_dir / "scillm_transport_readonly_canary_receipt.json") if receipt is not None else None,
        "error_artifact": str(canary_dir / "scillm_transport_readonly_canary_error.json") if error is not None else None,
        "validation_artifact": str(canary_dir / "scillm_transport_readonly_canary_validation.json"),
        "event_stream_artifact": str(canary_dir / "scillm_transport_readonly_canary_event_stream.json")
        if (canary_dir / "scillm_transport_readonly_canary_event_stream.json").is_file()
        else None,
        "transport_run_id": receipt.get("transport_run_id") if isinstance(receipt, dict) else None,
        "errors": validation.get("errors") or [],
        "delivery_state": validation.get("delivery_state"),
    }
    write_json(canary_dir / "scillm_transport_readonly_canary.json", canary)
    return canary


def scillm_transport_readonly_canary_artifacts(out_dir: Path, canary: dict[str, Any] | None = None) -> dict[str, Path]:
    if not canary:
        return {}
    canary_dir = out_dir / "scillm_transport_readonly_canary"
    artifacts = {
        "scillm_transport_readonly_canary.json": canary_dir / "scillm_transport_readonly_canary.json",
        "scillm_transport_readonly_canary_request.json": canary_dir / "scillm_transport_readonly_canary_request.json",
        "scillm_transport_readonly_canary_validation.json": canary_dir / "scillm_transport_readonly_canary_validation.json",
    }
    optional_artifacts = {
        "receipt_artifact": "scillm_transport_readonly_canary_receipt.json",
        "error_artifact": "scillm_transport_readonly_canary_error.json",
        "event_stream_artifact": "scillm_transport_readonly_canary_event_stream.json",
    }
    for field, name in optional_artifacts.items():
        path = canary_dir / name
        if canary.get(field) or path.is_file():
            artifacts[name] = path
    return artifacts


def validate_live_canary_artifacts(
    *,
    out_dir: Path,
    canary: dict[str, Any] | None,
    artifact_builder: Any,
    canary_schema: str,
    validation_schema: str,
    validation_artifact_name: str,
    cleanup_schema: str | None = None,
    cleanup_artifact_name: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    artifacts = artifact_builder(out_dir, canary)
    if not isinstance(canary, dict):
        errors.append("live canary missing")
    elif canary.get("schema") != canary_schema:
        errors.append(f"live canary schema mismatch: {canary.get('schema')}")
    elif canary.get("ok") is not True:
        errors.extend(list(canary.get("errors") or ["live canary did not pass"]))
    missing = sorted(name for name, path in artifacts.items() if not path.is_file())
    if missing:
        errors.append(f"live canary missing artifacts: {missing}")

    validation_payload: dict[str, Any] = {}
    validation_path = artifacts.get(validation_artifact_name)
    if validation_path and validation_path.is_file():
        try:
            loaded = json.loads(validation_path.read_text(encoding="utf-8"))
            validation_payload = loaded if isinstance(loaded, dict) else {}
        except Exception as exc:  # noqa: BLE001 - artifact validation must fail closed on unreadable JSON.
            errors.append(f"live canary validation artifact unreadable: {type(exc).__name__}: {exc}")
    elif canary:
        errors.append(f"live canary validation artifact missing: {validation_artifact_name}")
    if validation_payload:
        if validation_payload.get("schema") != validation_schema:
            errors.append(f"live canary validation schema mismatch: {validation_payload.get('schema')}")
        if validation_payload.get("ok") is not True:
            errors.extend(
                f"live canary validation failed: {error}"
                for error in validation_payload.get("errors") or ["validation ok is not true"]
            )

    cleanup_payload: dict[str, Any] = {}
    cleanup_path = artifacts.get(cleanup_artifact_name or "") if cleanup_artifact_name else None
    if cleanup_schema and cleanup_artifact_name:
        if cleanup_path and cleanup_path.is_file():
            try:
                loaded = json.loads(cleanup_path.read_text(encoding="utf-8"))
                cleanup_payload = loaded if isinstance(loaded, dict) else {}
            except Exception as exc:  # noqa: BLE001 - cleanup evidence is part of fail-closed proof.
                errors.append(f"live canary cleanup artifact unreadable: {type(exc).__name__}: {exc}")
        elif canary:
            errors.append(f"live canary cleanup artifact missing: {cleanup_artifact_name}")
        if cleanup_payload:
            if cleanup_payload.get("schema") != cleanup_schema:
                errors.append(f"live canary cleanup schema mismatch: {cleanup_payload.get('schema')}")
            if cleanup_payload.get("ok") is not True:
                errors.extend(
                    f"live canary cleanup failed: {error}"
                    for error in cleanup_payload.get("errors") or ["cleanup ok is not true"]
                )

    return {
        "schema": "pdf_lab.second_pass.live_canary_artifact_validation.v1",
        "ok": not errors,
        "errors": errors,
        "canary_schema": canary_schema,
        "validation_schema": validation_schema,
        "artifact_paths": {name: str(path) for name, path in artifacts.items()},
        "validation_artifact": str(validation_path) if validation_path else None,
        "cleanup_artifact": str(cleanup_path) if cleanup_path else None,
    }


def _http_json_response(response: Any) -> Any:
    try:
        return response.json()
    except Exception:  # noqa: BLE001 - response text is useful substrate evidence.
        return {"text": getattr(response, "text", "")}


def _response_text(response: Any, payload: Any) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text:
        return text
    return json.dumps(payload, sort_keys=True)


def _chat_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    return str(message.get("content") or "")


def build_scillm_proof_floor_chat_payload(*, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": "Return exactly: PDF_LAB_SCILLM_PREFLIGHT_OK",
            }
        ],
        "max_tokens": 16,
    }


def build_scillm_proof_floor_negative_chat_payload() -> dict[str, Any]:
    return {
        "model": "local-text",
        "messages": [{"role": "user", "content": "pdf-lab caller contract negative probe"}],
        "max_tokens": 1,
    }


def run_scillm_proof_floor(
    *,
    out_dir: Path,
    patch_mode: str,
    patch_backend: str,
    scillm_base_url: str,
    scillm_auth_token: str,
    caller_skill: str,
    model: str,
    timeout_s: float,
) -> dict[str, Any] | None:
    live_patch_required = patch_mode == "live" and patch_backend in {"opencode_serve", "scillm_orchestrator"}
    if not live_patch_required:
        return None

    import httpx  # noqa: PLC0415

    proof_dir = out_dir / "scillm_proof_floor"
    proof_dir.mkdir(parents=True, exist_ok=True)
    root = scillm_base_url.rstrip("/")
    headers = {
        "Authorization": f"Bearer {scillm_auth_token}",
        "X-Caller-Skill": caller_skill,
        "Content-Type": "application/json",
    }
    no_caller_headers = dict(headers)
    no_caller_headers.pop("X-Caller-Skill", None)
    positive_chat_payload = build_scillm_proof_floor_chat_payload(model=model)
    negative_chat_payload = build_scillm_proof_floor_negative_chat_payload()
    write_json(proof_dir / "positive_chat_request.json", positive_chat_payload)
    write_json(proof_dir / "missing_caller_chat_request.json", negative_chat_payload)

    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    def record_check(
        *,
        check_id: str,
        method: str,
        path: str,
        include_caller_skill: bool,
        expected_status: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact_base = proof_dir / f"{check_id}_response.json"
        try:
            if method == "GET":
                response = httpx.get(f"{root}{path}", headers=headers, timeout=timeout_s)
            else:
                response = httpx.post(
                    f"{root}{path}",
                    headers=headers if include_caller_skill else no_caller_headers,
                    json=payload or {},
                    timeout=timeout_s,
                )
            response_payload = _http_json_response(response)
            response_text = _response_text(response, response_payload)
            check = {
                "check_id": check_id,
                "method": method,
                "path": path,
                "include_caller_skill": include_caller_skill,
                "http_status": response.status_code,
                "expected_status": expected_status,
                "payload": response_payload,
                "response_text": response_text,
                "response_artifact": str(artifact_base),
            }
            write_json(artifact_base, check)
        except Exception as exc:  # noqa: BLE001 - proof floor must ledger substrate errors.
            check = {
                "check_id": check_id,
                "method": method,
                "path": path,
                "include_caller_skill": include_caller_skill,
                "expected_status": expected_status,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "response_artifact": str(artifact_base),
            }
            write_json(artifact_base, check)
        checks.append(check)
        return check

    liveliness = record_check(
        check_id="liveliness",
        method="GET",
        path="/health/liveliness",
        include_caller_skill=True,
        expected_status=200,
    )
    opencode_health = record_check(
        check_id="opencode_health",
        method="GET",
        path="/v1/scillm/opencode/health",
        include_caller_skill=True,
        expected_status=200,
    )
    positive_chat = record_check(
        check_id="positive_chat",
        method="POST",
        path="/v1/chat/completions",
        include_caller_skill=True,
        expected_status=200,
        payload=positive_chat_payload,
    )
    missing_caller = record_check(
        check_id="missing_caller_chat",
        method="POST",
        path="/v1/chat/completions",
        include_caller_skill=False,
        expected_status=400,
        payload=negative_chat_payload,
    )

    if liveliness.get("http_status") != 200 or (liveliness.get("payload") or {}).get("status") != "ok":
        errors.append("GET /health/liveliness did not return status ok")
    opencode_payload = opencode_health.get("payload") if isinstance(opencode_health.get("payload"), dict) else {}
    opencode_status = opencode_payload.get("status")
    if opencode_health.get("http_status") != 200 or (
        opencode_status not in {"ok", "healthy", "enabled"} and not opencode_payload.get("opencode_serve")
    ):
        errors.append("GET /v1/scillm/opencode/health did not prove OpenCode serve health")
    if positive_chat.get("http_status") != 200:
        errors.append("positive chat preflight did not return HTTP 200")
    elif "PDF_LAB_SCILLM_PREFLIGHT_OK" not in _chat_content(positive_chat.get("payload")):
        errors.append("positive chat preflight missing PDF_LAB_SCILLM_PREFLIGHT_OK sentinel")
    missing_caller_text = str(missing_caller.get("response_text") or json.dumps(missing_caller.get("payload"), sort_keys=True))
    if missing_caller.get("http_status") != 400 or "caller_skill_required" not in missing_caller_text:
        errors.append("missing-caller chat contract did not return caller_skill_required")

    validation = {
        "schema": "pdf_lab.second_pass.scillm_proof_floor_validation.v1",
        "ok": not errors,
        "errors": errors,
        "required": True,
        "required_checks": [
            "GET /health/liveliness",
            "GET /v1/scillm/opencode/health",
            "POST /v1/chat/completions with X-Caller-Skill",
            "POST /v1/chat/completions without X-Caller-Skill returns caller_skill_required",
        ],
    }
    write_json(proof_dir / "scillm_proof_floor_validation.json", validation)
    proof_floor = {
        "schema": "pdf_lab.second_pass.scillm_proof_floor.v1",
        "ok": validation["ok"],
        "required": True,
        "patch_mode": patch_mode,
        "patch_backend": patch_backend,
        "base_url": scillm_base_url,
        "caller_skill": caller_skill,
        "model": model,
        "checks": checks,
        "errors": errors,
        "validation_artifact": str(proof_dir / "scillm_proof_floor_validation.json"),
        "artifact_dir": str(proof_dir),
    }
    write_json(proof_dir / "scillm_proof_floor.json", proof_floor)
    return proof_floor


def scillm_proof_floor_artifacts(out_dir: Path, proof_floor: dict[str, Any] | None = None) -> dict[str, Path]:
    if not proof_floor:
        return {}
    proof_dir = out_dir / "scillm_proof_floor"
    names = [
        "scillm_proof_floor.json",
        "scillm_proof_floor_validation.json",
        "liveliness_response.json",
        "opencode_health_response.json",
        "positive_chat_request.json",
        "positive_chat_response.json",
        "missing_caller_chat_request.json",
        "missing_caller_chat_response.json",
    ]
    return {name: proof_dir / name for name in names}


def validate_scillm_proof_floor_artifacts(out_dir: Path, proof_floor: dict[str, Any] | None) -> dict[str, Any]:
    errors: list[str] = []
    artifacts = scillm_proof_floor_artifacts(out_dir, proof_floor)
    if not isinstance(proof_floor, dict):
        errors.append("scillm proof floor missing")
    elif proof_floor.get("schema") != "pdf_lab.second_pass.scillm_proof_floor.v1":
        errors.append("scillm proof floor schema mismatch")
    elif proof_floor.get("ok") is not True:
        errors.extend(list(proof_floor.get("errors") or ["scillm proof floor did not pass"]))
    missing = sorted(name for name, path in artifacts.items() if not path.is_file())
    if missing:
        errors.append(f"scillm proof floor missing artifacts: {missing}")
    validation_payload: dict[str, Any] = {}
    validation_path = artifacts.get("scillm_proof_floor_validation.json")
    if validation_path and validation_path.is_file():
        try:
            loaded = json.loads(validation_path.read_text(encoding="utf-8"))
            validation_payload = loaded if isinstance(loaded, dict) else {}
        except Exception as exc:  # noqa: BLE001 - artifact validation must fail closed on unreadable JSON.
            errors.append(f"scillm proof floor validation artifact unreadable: {type(exc).__name__}: {exc}")
    elif proof_floor:
        errors.append("scillm proof floor validation artifact missing")
    if validation_payload:
        if validation_payload.get("schema") != "pdf_lab.second_pass.scillm_proof_floor_validation.v1":
            errors.append("scillm proof floor validation schema mismatch")
        if validation_payload.get("ok") is not True:
            errors.extend(
                f"scillm proof floor validation failed: {error}"
                for error in validation_payload.get("errors") or ["validation ok is not true"]
            )
    return {
        "schema": "pdf_lab.second_pass.scillm_proof_floor_artifact_validation.v1",
        "ok": not errors,
        "errors": errors,
        "artifact_paths": {name: str(path) for name, path in artifacts.items()},
        "validation_artifact": str(validation_path) if validation_path else None,
    }


def run_harness(
    *,
    pdf_path: Path,
    out_dir: Path,
    ledger_path: Path | None,
    apply_mode: str,
    max_pages: int | None,
    candidate_census_timeout_s: float | None = None,
    candidate_page_timeout_s: float | None = None,
    candidate_census_pages: list[int] | None = None,
    sample_size: int,
    seed: int,
    review_mode: str,
    patch_mode: str,
    patch_backend: str,
    commit_mode: str,
    model: str,
    batch_id: str,
    review_fixture_path: Path | None,
    review_after_fixture_path: Path | None = None,
    human_annotated_pages_json: Path | None = None,
    scillm_base_url: str,
    scillm_auth_token: str,
    caller_skill: str,
    scillm_timeout_s: float,
    scillm_preflight_mode: str,
    opencode_agent: str,
    opencode_agent_sequence: list[str] | None = None,
    opencode_model: str | None,
    patch_prompt_profile: str = "plan_only",
    repair_strategy: str = "single",
    opencode_timeout_s: float,
    opencode_cleanup_session: bool,
    opencode_skills: list[str] | None,
    allowed_patch_prefixes: list[str] | None,
    validation_commands: list[str] | None,
    code_root: Path,
    prepare_isolated_code_root_dest: Path | None,
    prepare_isolated_code_root_include_paths: list[str] | None,
    prepare_isolated_code_root_force: bool,
    page_extract_timeout_s: float | None = None,
    page_orchestrator_mode: str = "dry_run",
    scillm_mounted_workspace_prefixes: list[Path] | None = None,
    stop_on_nonterminal: bool = False,
) -> dict[str, Any]:
    manifest_mod, sampler_mod, page_dag = _import_pdf_lab_modules()
    effective_opencode_model = page_dag.resolve_effective_opencode_model(
        patch_mode=patch_mode,
        patch_backend=patch_backend,
        opencode_model=opencode_model,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    debug_log = out_dir / "candidate_manifest_debug.log"
    census_progress_path = out_dir / "candidate_census_progress.json"
    census_events_path = out_dir / "candidate_census_events.jsonl"
    isolated_code_root_manifest = prepare_code_root_if_requested(
        source_root=REPO,
        dest_root=prepare_isolated_code_root_dest,
        include_paths=prepare_isolated_code_root_include_paths,
        force=prepare_isolated_code_root_force,
    )
    effective_code_root = Path(isolated_code_root_manifest["dest_root"]) if isolated_code_root_manifest else code_root
    code_root_visibility = validate_scillm_live_code_root(
        code_root=effective_code_root,
        patch_mode=patch_mode,
        patch_backend=patch_backend,
        mounted_prefixes=scillm_mounted_workspace_prefixes or parse_mounted_workspace_prefixes(),
        isolated_code_root_manifest=isolated_code_root_manifest,
    )
    write_json(out_dir / "scillm_code_root_visibility.json", code_root_visibility)

    try:
        pages, page_count, census_failures = run_candidate_census(
            manifest_mod=manifest_mod,
            pdf_path=pdf_path,
            ledger_path=ledger_path,
            apply_mode=apply_mode,
            max_pages=max_pages,
            debug_log=debug_log,
            timeout_s=candidate_census_timeout_s,
            page_timeout_s=candidate_page_timeout_s,
            progress_path=census_progress_path,
            page_numbers=candidate_census_pages,
        )
    except Exception as exc:  # noqa: BLE001 - census substrate failures must be ledgered.
        census_failure = {
            "schema": "pdf_lab.second_pass.candidate_census_failure.v1",
            "created_at": utc_now(),
            "node_id": "candidate_census",
            "pdf_path": str(pdf_path),
            "ledger_path": str(ledger_path) if ledger_path else None,
            "apply_mode": apply_mode,
            "max_pages": max_pages,
            "timeout_s": candidate_census_timeout_s,
            "status": "timeout" if isinstance(exc, CandidateCensusTimeout) else "substrate_error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "debug_log": str(debug_log),
            "progress": str(census_progress_path),
            "events": str(census_events_path),
        }
        census_failure_path = out_dir / "candidate_census_failure.json"
        write_json(census_failure_path, census_failure)
        aggregate = candidate_census_failure_aggregate(f"candidate census failed: {census_failure['status']}")
        report = {
            "schema": "pdf_lab.second_pass.harness_report.v1",
            "created_at": utc_now(),
            "pdf_path": str(pdf_path),
            "out_dir": str(out_dir),
            "candidate_manifest": None,
            "sampled_page_cases": None,
            "candidate_census_status": census_failure["status"],
            "candidate_census_timeout_s": candidate_census_timeout_s,
            "candidate_page_timeout_s": candidate_page_timeout_s,
            "candidate_census_pages": candidate_census_pages,
            "candidate_census_failure": str(census_failure_path),
            "candidate_manifest_debug_log": str(debug_log),
            "candidate_census_progress": str(census_progress_path),
            "candidate_census_events": str(census_events_path),
            "candidate_count": None,
            "requested_sample_size": sample_size,
            "human_annotated_pages_json": str(human_annotated_pages_json) if human_annotated_pages_json else None,
            "forced_pages_input": None,
            "selected_count": 0,
            "selected_pages": [],
            "review_mode": review_mode,
            "review_fixture_path": str(review_fixture_path) if review_fixture_path else None,
            "review_after_fixture_path": str(review_after_fixture_path) if review_after_fixture_path else None,
            "patch_mode": patch_mode,
            "patch_backend": patch_backend,
            "commit_mode": commit_mode,
            "scillm_base_url": scillm_base_url,
            "caller_skill": caller_skill,
            "scillm_timeout_s": scillm_timeout_s,
            "scillm_preflight_mode": scillm_preflight_mode,
            "opencode_agent": opencode_agent,
            "opencode_agent_sequence": opencode_agent_sequence,
            "opencode_model": effective_opencode_model,
            "requested_opencode_model": opencode_model,
            "opencode_model_defaulted": effective_opencode_model is not None and opencode_model is None,
            "patch_prompt_profile": patch_prompt_profile,
            "repair_strategy": repair_strategy,
            "opencode_timeout_s": opencode_timeout_s,
            "opencode_cleanup_session": opencode_cleanup_session,
            "opencode_skills": opencode_skills,
            "allowed_patch_prefixes": allowed_patch_prefixes,
            "code_root": str(effective_code_root),
            "scillm_code_root_visibility": code_root_visibility,
            "scillm_proof_floor": None,
            "opencode_completion_canary": None,
            "scillm_transport_readonly_canary": None,
            "scillm_transport_write_canary": None,
            "isolated_code_root_manifest": isolated_code_root_manifest,
            "page_results": [],
            "aggregate": aggregate,
            "terminal_status": "failed_closed",
        }
        write_json(out_dir / "harness_report.json", report)
        return report
    manifest = manifest_mod.build_manifest_from_pages(
        pdf_path=pdf_path,
        pages=pages,
        page_count=page_count,
        ledger_path=ledger_path,
        apply_mode=apply_mode,
        command=sys.argv,
        census_failures=census_failures,
    )
    manifest_path = out_dir / "candidate_manifest.json"
    write_json(manifest_path, manifest)
    candidate_manifest_integrity_validation = validate_candidate_manifest_integrity(manifest)
    candidate_manifest_integrity_validation_path = out_dir / "candidate_manifest_integrity_validation.json"
    write_json(candidate_manifest_integrity_validation_path, candidate_manifest_integrity_validation)
    forced_pages = sampler_mod.load_forced_pages(human_annotated_pages_json) if human_annotated_pages_json else []
    forced_pages_input = {
        "schema": "pdf_lab.second_pass.forced_pages_input.v1",
        "source": str(human_annotated_pages_json) if human_annotated_pages_json else None,
        "page_count": len(forced_pages),
        "pages": forced_pages,
    }
    forced_pages_input_path = out_dir / "forced_pages_input.json"
    write_json(forced_pages_input_path, forced_pages_input)

    if forced_pages:
        sampled_cases = sampler_mod.select_page_cases(
            manifest,
            sample_size=sample_size,
            seed=seed,
            forced_pages=forced_pages,
        )
    else:
        sampled_cases = sampler_mod.select_page_cases(
            manifest,
            sample_size=sample_size,
            seed=seed,
        )
    sampled_cases_path = out_dir / "sampled_page_cases.json"
    write_json(sampled_cases_path, sampled_cases)
    sampling_gate = validate_sampling_gate(
        manifest=manifest,
        sampled_cases=sampled_cases,
    )
    write_json(out_dir / "sampling_gate.json", sampling_gate)
    candidate_sample_linkage_validation = validate_candidate_sample_linkage(
        manifest=manifest,
        sampled_cases=sampled_cases,
    )
    candidate_sample_linkage_validation_path = out_dir / "candidate_sample_linkage_validation.json"
    write_json(candidate_sample_linkage_validation_path, candidate_sample_linkage_validation)
    deterministic_execution_plan = build_deterministic_execution_plan(
        sampled_cases=sampled_cases,
        patch_mode=patch_mode,
        patch_backend=patch_backend,
        review_mode=review_mode,
        commit_mode=commit_mode,
        page_orchestrator_mode=page_orchestrator_mode,
        stop_on_nonterminal=stop_on_nonterminal,
    )
    deterministic_execution_plan_path = out_dir / "deterministic_execution_plan.json"
    write_json(deterministic_execution_plan_path, deterministic_execution_plan)

    page_results: list[dict[str, Any]] = []
    scillm_proof_floor = None
    opencode_completion_canary = None
    scillm_transport_readonly_canary = None
    scillm_transport_write_canary = None
    substrate_pre_page_gates_ok = (
        code_root_visibility["ok"]
        and candidate_manifest_integrity_validation["ok"]
        and candidate_sample_linkage_validation["ok"]
        and sampling_gate["ok"]
    )
    if substrate_pre_page_gates_ok:
        scillm_proof_floor = run_scillm_proof_floor(
            out_dir=out_dir,
            patch_mode=patch_mode,
            patch_backend=patch_backend,
            scillm_base_url=scillm_base_url,
            scillm_auth_token=scillm_auth_token,
            caller_skill=caller_skill,
            model=model,
            timeout_s=min(scillm_timeout_s, 30.0),
        )
    if substrate_pre_page_gates_ok and (scillm_proof_floor is None or scillm_proof_floor.get("ok") is True):
        opencode_completion_canary = run_opencode_completion_canary(
            out_dir=out_dir,
            page_dag=page_dag,
            code_root=effective_code_root,
            patch_mode=patch_mode,
            patch_backend=patch_backend,
            scillm_base_url=scillm_base_url,
            scillm_auth_token=scillm_auth_token,
            caller_skill=caller_skill,
            agent=opencode_agent,
            skills=opencode_skills,
            timeout_s=min(opencode_timeout_s, 45.0),
            cleanup_session=opencode_cleanup_session,
            model=effective_opencode_model,
        )
        scillm_transport_readonly_canary = run_scillm_transport_readonly_canary(
            out_dir=out_dir,
            page_dag=page_dag,
            code_root=effective_code_root,
            patch_mode=patch_mode,
            patch_backend=patch_backend,
            scillm_base_url=scillm_base_url,
            scillm_auth_token=scillm_auth_token,
            caller_skill=caller_skill,
            agent=opencode_agent,
            skills=opencode_skills,
            timeout_s=min(opencode_timeout_s, 45.0),
            model=effective_opencode_model,
        )
        if scillm_transport_readonly_canary is None or scillm_transport_readonly_canary.get("ok") is True:
            scillm_transport_write_canary = run_scillm_transport_write_canary(
                out_dir=out_dir,
                page_dag=page_dag,
                code_root=effective_code_root,
                patch_mode=patch_mode,
                patch_backend=patch_backend,
                scillm_base_url=scillm_base_url,
                scillm_auth_token=scillm_auth_token,
                caller_skill=caller_skill,
                agent=opencode_agent,
                skills=opencode_skills,
                timeout_s=min(opencode_timeout_s, 45.0),
                model=effective_opencode_model,
            )
    if not candidate_manifest_integrity_validation["ok"] or not candidate_sample_linkage_validation["ok"] or not sampling_gate["ok"]:
        page_results = []
    elif not code_root_visibility["ok"]:
        page_results = [
            _write_blocked_case_result(
                out_dir=out_dir,
                case=case,
                reason="scillm_code_root_visibility_failed",
                visibility=code_root_visibility,
            )
            for case in sampled_cases.get("page_cases") or []
        ]
    elif scillm_proof_floor is not None and not scillm_proof_floor.get("ok"):
        page_results = [
            _write_blocked_case_result(
                out_dir=out_dir,
                case=case,
                reason="scillm_proof_floor_failed",
                visibility=code_root_visibility,
                extra_artifacts=scillm_proof_floor_artifacts(out_dir, scillm_proof_floor),
            )
            for case in sampled_cases.get("page_cases") or []
        ]
    elif opencode_completion_canary is not None and not opencode_completion_canary.get("ok"):
        extra_artifacts = opencode_completion_canary_artifacts(out_dir, opencode_completion_canary)
        page_results = [
            _write_blocked_case_result(
                out_dir=out_dir,
                case=case,
                reason="opencode_completion_canary_failed",
                visibility=code_root_visibility,
                extra_artifacts=extra_artifacts,
            )
            for case in sampled_cases.get("page_cases") or []
        ]
    elif scillm_transport_readonly_canary is not None and not scillm_transport_readonly_canary.get("ok"):
        extra_artifacts = scillm_transport_readonly_canary_artifacts(out_dir, scillm_transport_readonly_canary)
        page_results = [
            _write_blocked_case_result(
                out_dir=out_dir,
                case=case,
                reason="scillm_transport_readonly_canary_failed",
                visibility=code_root_visibility,
                extra_artifacts=extra_artifacts,
            )
            for case in sampled_cases.get("page_cases") or []
        ]
    elif scillm_transport_write_canary is not None and not scillm_transport_write_canary.get("ok"):
        extra_artifacts = scillm_transport_write_canary_artifacts(out_dir, scillm_transport_write_canary)
        page_results = [
            _write_blocked_case_result(
                out_dir=out_dir,
                case=case,
                reason="scillm_transport_write_canary_failed",
                visibility=code_root_visibility,
                extra_artifacts=extra_artifacts,
            )
            for case in sampled_cases.get("page_cases") or []
        ]
    for case in [] if (
        not candidate_sample_linkage_validation["ok"]
        or not candidate_manifest_integrity_validation["ok"]
        or not sampling_gate["ok"]
        or not code_root_visibility["ok"]
        or (scillm_proof_floor is not None and not scillm_proof_floor.get("ok"))
        or (opencode_completion_canary is not None and not opencode_completion_canary.get("ok"))
        or (scillm_transport_readonly_canary is not None and not scillm_transport_readonly_canary.get("ok"))
        or (scillm_transport_write_canary is not None and not scillm_transport_write_canary.get("ok"))
    ) else sampled_cases.get("page_cases") or []:
        result = page_dag.run_page_case(
            pdf_path=pdf_path,
            manifest=manifest,
            sampled_cases=sampled_cases,
            out_dir=out_dir / "page_cases",
            case_id=case["case_id"],
            page_number=None,
            ledger_path=ledger_path,
            apply_mode=apply_mode,
            dpi=150,
            model=model,
            batch_id=batch_id,
            review_mode=review_mode,
            review_fixture_path=review_fixture_path,
            review_after_fixture_path=review_after_fixture_path,
            scillm_base_url=scillm_base_url,
            scillm_auth_token=scillm_auth_token,
            caller_skill=caller_skill,
            scillm_timeout_s=scillm_timeout_s,
            scillm_preflight_mode=scillm_preflight_mode,
            patch_mode=patch_mode,
            patch_backend=patch_backend,
            opencode_agent=opencode_agent,
            opencode_agent_sequence=opencode_agent_sequence,
            opencode_model=effective_opencode_model,
            patch_prompt_profile=patch_prompt_profile,
            repair_strategy=repair_strategy,
            opencode_timeout_s=opencode_timeout_s,
            opencode_cleanup_session=opencode_cleanup_session,
            opencode_skills=opencode_skills,
            allowed_patch_prefixes=allowed_patch_prefixes,
            validation_commands=validation_commands,
            commit_mode=commit_mode,
            code_root=effective_code_root,
            page_extract_timeout_s=page_extract_timeout_s,
            page_orchestrator_mode=page_orchestrator_mode,
        )
        page_result = _page_result_from_case(case, result)
        page_results.append(page_result)
        if stop_on_nonterminal and page_result["terminal_status"] not in RESOLVED_PASS_STATUSES:
            break

    aggregate = aggregate_page_results(page_results)
    deterministic_execution_plan_validation = validate_deterministic_execution_plan(
        deterministic_execution_plan,
        page_results=page_results,
    )
    deterministic_execution_plan_validation_path = out_dir / "deterministic_execution_plan_validation.json"
    write_json(deterministic_execution_plan_validation_path, deterministic_execution_plan_validation)
    orchestrator_dag_spec_count = sum(
        1
        for result in page_results
        if "scillm_orchestrator_page_dag_spec.json" in (result.get("evidence_artifacts") or [])
    )
    orchestrator_dag_spec_ok_count = sum(1 for result in page_results if result.get("orchestrator_dag_spec_ok") is True)
    orchestrator_page_submission_ok_count = sum(
        1 for result in page_results if result.get("orchestrator_page_submission_ok") is True
    )
    page_orchestrator_registered_count = sum(1 for result in page_results if result.get("page_orchestrator_registered") is True)
    scillm_patch_delegate_bug_report_count = sum(
        1 for result in page_results if result.get("scillm_patch_delegate_bug_report")
    )
    scillm_patch_delegate_bug_reports = build_scillm_patch_delegate_bug_report_bundle(
        out_dir=out_dir,
        page_results=page_results,
    )
    scillm_patch_delegate_bug_reports_path = out_dir / "scillm_patch_delegate_bug_reports.json"
    write_json(scillm_patch_delegate_bug_reports_path, scillm_patch_delegate_bug_reports)
    scillm_patch_delegate_bug_reports_zip_path = out_dir / "scillm_patch_delegate_bug_reports.zip"
    scillm_patch_delegate_bug_reports_zip = package_scillm_patch_delegate_bug_report_bundle(
        out_dir=out_dir,
        bundle_path=scillm_patch_delegate_bug_reports_path,
        zip_path=scillm_patch_delegate_bug_reports_zip_path,
        page_results=page_results,
    )
    write_json(out_dir / "scillm_patch_delegate_bug_reports_zip.json", scillm_patch_delegate_bug_reports_zip)
    patch_commit_ledger = build_patch_commit_ledger(
        out_dir=out_dir,
        page_results=page_results,
    )
    patch_commit_ledger_path = out_dir / "patch_commit_ledger.json"
    write_json(patch_commit_ledger_path, patch_commit_ledger)
    patch_commit_ledger_zip_path = out_dir / "patch_commit_ledger.zip"
    patch_commit_ledger_zip = package_patch_commit_ledger(
        ledger_path=patch_commit_ledger_path,
        zip_path=patch_commit_ledger_zip_path,
        page_results=page_results,
    )
    write_json(out_dir / "patch_commit_ledger_zip.json", patch_commit_ledger_zip)
    live_scillm_canary_bug_report = build_live_scillm_canary_bug_report(
        out_dir=out_dir,
        code_root=effective_code_root,
        patch_mode=patch_mode,
        patch_backend=patch_backend,
        code_root_visibility=code_root_visibility,
        scillm_proof_floor=scillm_proof_floor,
        opencode_completion_canary=opencode_completion_canary,
        scillm_transport_readonly_canary=scillm_transport_readonly_canary,
        scillm_transport_write_canary=scillm_transport_write_canary,
    )
    live_scillm_canary_bug_report_path = out_dir / "live_scillm_canary_bug_report.json"
    write_json(live_scillm_canary_bug_report_path, live_scillm_canary_bug_report)
    harness_readiness_audit_path = out_dir / "harness_readiness_audit.json"
    harness_review_bundle_zip_path = out_dir / "harness_review_bundle.zip"
    harness_review_bundle_validation_path = out_dir / "harness_review_bundle_zip.json"
    harness_review_bundle_consistency_validation_path = out_dir / "harness_review_bundle_consistency_validation.json"
    harness_final_gate_path = out_dir / "harness_final_gate.json"
    harness_report_path = out_dir / "harness_report.json"
    harness_review_bundle_top_level_artifacts = [
        out_dir / "candidate_manifest.json",
        census_progress_path,
        census_events_path,
        candidate_manifest_integrity_validation_path,
        out_dir / "sampled_page_cases.json",
        forced_pages_input_path,
        out_dir / "sampling_gate.json",
        out_dir / "candidate_sample_linkage_validation.json",
        deterministic_execution_plan_path,
        deterministic_execution_plan_validation_path,
        out_dir / "scillm_code_root_visibility.json",
        *scillm_proof_floor_artifacts(out_dir, scillm_proof_floor).values(),
        *opencode_completion_canary_artifacts(out_dir, opencode_completion_canary).values(),
        *scillm_transport_readonly_canary_artifacts(out_dir, scillm_transport_readonly_canary).values(),
        *scillm_transport_write_canary_artifacts(out_dir, scillm_transport_write_canary).values(),
        scillm_patch_delegate_bug_reports_path,
        out_dir / "scillm_patch_delegate_bug_reports_zip.json",
        scillm_patch_delegate_bug_reports_zip_path,
        patch_commit_ledger_path,
        out_dir / "patch_commit_ledger_zip.json",
        patch_commit_ledger_zip_path,
        live_scillm_canary_bug_report_path,
        harness_readiness_audit_path,
        harness_final_gate_path,
        harness_report_path,
    ]
    write_json(harness_readiness_audit_path, {"schema": "pdf_lab.second_pass.harness_readiness_audit.pending.v1"})
    write_json(harness_final_gate_path, {"schema": "pdf_lab.second_pass.harness_final_gate.pending.v1"})
    write_json(harness_report_path, {"schema": "pdf_lab.second_pass.harness_report.pending.v1"})
    harness_review_bundle_zip = validate_harness_review_bundle_inputs(
        zip_path=harness_review_bundle_zip_path,
        top_level_artifacts=harness_review_bundle_top_level_artifacts,
        page_results=page_results,
    )
    write_json(harness_review_bundle_validation_path, harness_review_bundle_zip)
    harness_readiness_audit = build_harness_readiness_audit(
        out_dir=out_dir,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_cases_path,
        sampling_gate=sampling_gate,
        candidate_sample_linkage_validation=candidate_sample_linkage_validation,
        candidate_manifest_integrity_validation=candidate_manifest_integrity_validation,
        page_results=page_results,
        aggregate=aggregate,
        patch_mode=patch_mode,
        patch_backend=patch_backend,
        code_root_visibility=code_root_visibility,
        scillm_proof_floor=scillm_proof_floor,
        opencode_completion_canary=opencode_completion_canary,
        scillm_transport_readonly_canary=scillm_transport_readonly_canary,
        scillm_transport_write_canary=scillm_transport_write_canary,
        scillm_bug_report_zip_validation=scillm_patch_delegate_bug_reports_zip,
        patch_commit_ledger=patch_commit_ledger,
        patch_commit_ledger_zip_validation=patch_commit_ledger_zip,
        harness_review_bundle_validation=harness_review_bundle_zip,
        deterministic_execution_plan_validation=deterministic_execution_plan_validation,
        live_scillm_canary_bug_report=live_scillm_canary_bug_report,
    )
    write_json(harness_readiness_audit_path, harness_readiness_audit)
    report = {
        "schema": "pdf_lab.second_pass.harness_report.v1",
        "created_at": utc_now(),
        "pdf_path": str(pdf_path),
        "out_dir": str(out_dir),
        "candidate_manifest": str(manifest_path),
        "candidate_manifest_integrity_validation": str(candidate_manifest_integrity_validation_path),
        "candidate_manifest_integrity_validation_result": candidate_manifest_integrity_validation,
        "sampled_page_cases": str(sampled_cases_path),
        "sampling_gate": str(out_dir / "sampling_gate.json"),
        "sampling_gate_validation": sampling_gate,
        "candidate_sample_linkage_validation": str(candidate_sample_linkage_validation_path),
        "candidate_sample_linkage_validation_result": candidate_sample_linkage_validation,
        "deterministic_execution_plan": str(deterministic_execution_plan_path),
        "deterministic_execution_plan_result": deterministic_execution_plan,
        "deterministic_execution_plan_validation": str(deterministic_execution_plan_validation_path),
        "deterministic_execution_plan_validation_result": deterministic_execution_plan_validation,
        "candidate_census_status": "completed",
        "candidate_census_timeout_s": candidate_census_timeout_s,
        "candidate_page_timeout_s": candidate_page_timeout_s,
        "candidate_census_pages": candidate_census_pages,
        "candidate_census_failure": None,
        "candidate_census_failure_count": len(census_failures),
        "candidate_census_failures": census_failures,
        "candidate_manifest_debug_log": str(debug_log),
        "candidate_census_progress": str(census_progress_path),
        "candidate_census_events": str(census_events_path),
        "candidate_count": manifest.get("candidate_count"),
        "requested_sample_size": sample_size,
        "human_annotated_pages_json": str(human_annotated_pages_json) if human_annotated_pages_json else None,
        "forced_pages_input": str(forced_pages_input_path),
        "forced_pages_input_result": forced_pages_input,
        "selected_count": sampled_cases.get("selected_count"),
        "selected_pages": sampled_cases.get("selected_pages"),
        "review_mode": review_mode,
        "review_fixture_path": str(review_fixture_path) if review_fixture_path else None,
        "review_after_fixture_path": str(review_after_fixture_path) if review_after_fixture_path else None,
        "patch_mode": patch_mode,
        "patch_backend": patch_backend,
        "commit_mode": commit_mode,
        "scillm_base_url": scillm_base_url,
        "caller_skill": caller_skill,
        "scillm_timeout_s": scillm_timeout_s,
        "scillm_preflight_mode": scillm_preflight_mode,
        "opencode_agent": opencode_agent,
        "opencode_agent_sequence": opencode_agent_sequence,
        "opencode_model": effective_opencode_model,
        "requested_opencode_model": opencode_model,
        "opencode_model_defaulted": effective_opencode_model is not None and opencode_model is None,
        "patch_prompt_profile": patch_prompt_profile,
        "repair_strategy": repair_strategy,
        "opencode_timeout_s": opencode_timeout_s,
        "opencode_cleanup_session": opencode_cleanup_session,
        "opencode_skills": opencode_skills,
        "allowed_patch_prefixes": allowed_patch_prefixes,
        "page_extract_timeout_s": page_extract_timeout_s,
        "page_orchestrator_mode": page_orchestrator_mode,
        "code_root": str(effective_code_root),
        "scillm_code_root_visibility": code_root_visibility,
        "scillm_proof_floor": scillm_proof_floor,
        "opencode_completion_canary": opencode_completion_canary,
        "scillm_transport_readonly_canary": scillm_transport_readonly_canary,
        "scillm_transport_write_canary": scillm_transport_write_canary,
        "isolated_code_root_manifest": isolated_code_root_manifest,
        "orchestrator_dag_spec_count": orchestrator_dag_spec_count,
        "orchestrator_dag_spec_ok_count": orchestrator_dag_spec_ok_count,
        "orchestrator_page_submission_ok_count": orchestrator_page_submission_ok_count,
        "page_orchestrator_registered_count": page_orchestrator_registered_count,
        "scillm_patch_delegate_bug_report_count": scillm_patch_delegate_bug_report_count,
        "scillm_patch_delegate_bug_reports": str(scillm_patch_delegate_bug_reports_path),
        "scillm_patch_delegate_bug_reports_zip": str(scillm_patch_delegate_bug_reports_zip_path),
        "scillm_patch_delegate_bug_reports_zip_validation": scillm_patch_delegate_bug_reports_zip,
        "patch_commit_ledger": str(patch_commit_ledger_path),
        "patch_commit_ledger_zip": str(patch_commit_ledger_zip_path),
        "patch_commit_ledger_zip_validation": patch_commit_ledger_zip,
        "live_scillm_canary_bug_report": str(live_scillm_canary_bug_report_path),
        "live_scillm_canary_bug_report_result": live_scillm_canary_bug_report,
        "harness_review_bundle": str(harness_review_bundle_zip_path),
        "harness_review_bundle_zip_validation": harness_review_bundle_zip,
        "harness_readiness_audit": str(harness_readiness_audit_path),
        "harness_readiness_audit_validation": harness_readiness_audit,
        "page_results": page_results,
        "aggregate": aggregate,
        "terminal_status": "passed" if harness_readiness_audit["ok"] else "failed_closed",
    }
    write_json(harness_report_path, report)
    harness_review_bundle_zip = package_harness_review_bundle(
        out_dir=out_dir,
        zip_path=harness_review_bundle_zip_path,
        top_level_artifacts=harness_review_bundle_top_level_artifacts,
        page_results=page_results,
        validation_artifact_path=harness_review_bundle_validation_path,
    )
    report["harness_review_bundle_zip_validation"] = harness_review_bundle_zip
    harness_readiness_audit = build_harness_readiness_audit(
        out_dir=out_dir,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_cases_path,
        sampling_gate=sampling_gate,
        candidate_sample_linkage_validation=candidate_sample_linkage_validation,
        candidate_manifest_integrity_validation=candidate_manifest_integrity_validation,
        page_results=page_results,
        aggregate=aggregate,
        patch_mode=patch_mode,
        patch_backend=patch_backend,
        code_root_visibility=code_root_visibility,
        scillm_proof_floor=scillm_proof_floor,
        opencode_completion_canary=opencode_completion_canary,
        scillm_transport_readonly_canary=scillm_transport_readonly_canary,
        scillm_transport_write_canary=scillm_transport_write_canary,
        scillm_bug_report_zip_validation=scillm_patch_delegate_bug_reports_zip,
        patch_commit_ledger=patch_commit_ledger,
        patch_commit_ledger_zip_validation=patch_commit_ledger_zip,
        harness_review_bundle_validation=harness_review_bundle_zip,
        deterministic_execution_plan_validation=deterministic_execution_plan_validation,
        live_scillm_canary_bug_report=live_scillm_canary_bug_report,
    )
    write_json(harness_readiness_audit_path, harness_readiness_audit)
    report["harness_readiness_audit_validation"] = harness_readiness_audit
    report["terminal_status"] = "passed" if harness_readiness_audit["ok"] else "failed_closed"
    write_json(harness_report_path, report)
    harness_review_bundle_zip = package_harness_review_bundle(
        out_dir=out_dir,
        zip_path=harness_review_bundle_zip_path,
        top_level_artifacts=harness_review_bundle_top_level_artifacts,
        page_results=page_results,
        validation_artifact_path=harness_review_bundle_validation_path,
    )
    report["harness_review_bundle_zip_validation"] = harness_review_bundle_zip
    harness_readiness_audit = build_harness_readiness_audit(
        out_dir=out_dir,
        candidate_manifest_path=manifest_path,
        sampled_cases_path=sampled_cases_path,
        sampling_gate=sampling_gate,
        candidate_sample_linkage_validation=candidate_sample_linkage_validation,
        candidate_manifest_integrity_validation=candidate_manifest_integrity_validation,
        page_results=page_results,
        aggregate=aggregate,
        patch_mode=patch_mode,
        patch_backend=patch_backend,
        code_root_visibility=code_root_visibility,
        scillm_proof_floor=scillm_proof_floor,
        opencode_completion_canary=opencode_completion_canary,
        scillm_transport_readonly_canary=scillm_transport_readonly_canary,
        scillm_transport_write_canary=scillm_transport_write_canary,
        scillm_bug_report_zip_validation=scillm_patch_delegate_bug_reports_zip,
        patch_commit_ledger=patch_commit_ledger,
        patch_commit_ledger_zip_validation=patch_commit_ledger_zip,
        harness_review_bundle_validation=harness_review_bundle_zip,
        deterministic_execution_plan_validation=deterministic_execution_plan_validation,
        live_scillm_canary_bug_report=live_scillm_canary_bug_report,
    )
    write_json(harness_readiness_audit_path, harness_readiness_audit)
    report["harness_readiness_audit_validation"] = harness_readiness_audit
    report["terminal_status"] = "passed" if harness_readiness_audit["ok"] else "failed_closed"
    write_json(harness_report_path, report)
    package_harness_review_bundle(
        out_dir=out_dir,
        zip_path=harness_review_bundle_zip_path,
        top_level_artifacts=harness_review_bundle_top_level_artifacts,
        page_results=page_results,
        validation_artifact_path=harness_review_bundle_validation_path,
    )
    harness_review_bundle_consistency_validation = validate_harness_review_bundle_consistency(
        zip_path=harness_review_bundle_zip_path,
        report_path=harness_report_path,
        readiness_audit_path=harness_readiness_audit_path,
        bundle_validation_path=harness_review_bundle_validation_path,
        final_gate_path=harness_final_gate_path,
    )
    write_json(harness_review_bundle_consistency_validation_path, harness_review_bundle_consistency_validation)
    if harness_review_bundle_zip_path.is_file() and harness_review_bundle_consistency_validation_path.is_file():
        with zipfile.ZipFile(harness_review_bundle_zip_path, "a", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(
                harness_review_bundle_consistency_validation_path,
                arcname=harness_review_bundle_consistency_validation_path.name,
            )
    report["harness_review_bundle_consistency_validation"] = str(harness_review_bundle_consistency_validation_path)
    report["harness_review_bundle_consistency_validation_result"] = harness_review_bundle_consistency_validation
    final_gate = build_harness_final_gate(
        harness_readiness_audit=harness_readiness_audit,
        harness_review_bundle_consistency_validation=harness_review_bundle_consistency_validation,
        report_terminal_status=report.get("terminal_status"),
    )
    report["final_gate"] = final_gate
    report["harness_final_gate"] = str(harness_final_gate_path)
    report["terminal_status"] = final_gate["terminal_status"]
    write_json(harness_final_gate_path, final_gate)
    write_json(harness_report_path, report)
    harness_review_bundle_zip = package_harness_review_bundle(
        out_dir=out_dir,
        zip_path=harness_review_bundle_zip_path,
        top_level_artifacts=harness_review_bundle_top_level_artifacts,
        page_results=page_results,
        validation_artifact_path=harness_review_bundle_validation_path,
    )
    report["harness_review_bundle_zip_validation"] = harness_review_bundle_zip
    harness_review_bundle_consistency_validation = validate_harness_review_bundle_consistency(
        zip_path=harness_review_bundle_zip_path,
        report_path=harness_report_path,
        readiness_audit_path=harness_readiness_audit_path,
        bundle_validation_path=harness_review_bundle_validation_path,
        final_gate_path=harness_final_gate_path,
    )
    write_json(harness_review_bundle_consistency_validation_path, harness_review_bundle_consistency_validation)
    if harness_review_bundle_zip_path.is_file() and harness_review_bundle_consistency_validation_path.is_file():
        with zipfile.ZipFile(harness_review_bundle_zip_path, "a", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(
                harness_review_bundle_consistency_validation_path,
                arcname=harness_review_bundle_consistency_validation_path.name,
            )
    report["harness_review_bundle_consistency_validation_result"] = harness_review_bundle_consistency_validation
    report["final_gate"] = build_harness_final_gate(
        harness_readiness_audit=harness_readiness_audit,
        harness_review_bundle_consistency_validation=harness_review_bundle_consistency_validation,
        report_terminal_status=report.get("terminal_status"),
    )
    report["harness_final_gate"] = str(harness_final_gate_path)
    report["terminal_status"] = report["final_gate"]["terminal_status"]
    write_json(harness_final_gate_path, report["final_gate"])
    write_json(harness_report_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--apply-mode", default="release")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--candidate-census-timeout-s", type=float)
    parser.add_argument("--candidate-page-timeout-s", type=float)
    parser.add_argument("--candidate-census-page", type=int, action="append", dest="candidate_census_pages")
    parser.add_argument("--sample-size", type=int, default=12)
    parser.add_argument("--seed", type=int, default=530800)
    parser.add_argument("--review-mode", choices=["dry_run", "live", "fixture"], default="dry_run")
    parser.add_argument("--review-fixture", type=Path, dest="review_fixture_path")
    parser.add_argument("--review-after-fixture", type=Path, dest="review_after_fixture_path")
    parser.add_argument("--human-annotated-pages-json", type=Path, dest="human_annotated_pages_json")
    parser.add_argument("--patch-mode", choices=["dry_run", "live"], default="dry_run")
    parser.add_argument("--patch-backend", choices=["opencode_serve", "scillm_orchestrator"], default="opencode_serve")
    parser.add_argument("--commit-mode", choices=["dry_run", "live"], default="dry_run")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--batch-id", default="pdf-lab-second-pass")
    parser.add_argument("--scillm-base-url", default=os.environ.get("SCILLM_API_BASE", "http://localhost:4001"))
    parser.add_argument("--scillm-auth-token", default=os.environ.get("SCILLM_PROXY_KEY", "sk-dev-proxy-123"))
    parser.add_argument("--caller-skill", default="pdf-lab")
    parser.add_argument("--scillm-timeout-s", type=float, default=180.0)
    parser.add_argument("--scillm-preflight-mode", choices=["dry_run", "live"], default="live")
    parser.add_argument("--opencode-agent", default="build")
    parser.add_argument("--opencode-agent-sequence", action="append", dest="opencode_agent_sequence")
    parser.add_argument("--opencode-model")
    parser.add_argument("--patch-prompt-profile", choices=["compact", "full", "plan_only"], default="plan_only")
    parser.add_argument("--repair-strategy", choices=["single", "split", "chat_plan_split"], default="single")
    parser.add_argument("--opencode-timeout-s", type=float, default=600.0)
    parser.add_argument("--opencode-keep-session", action="store_true")
    parser.add_argument("--opencode-skill", action="append", dest="opencode_skills")
    parser.add_argument("--allowed-patch-prefix", action="append", dest="allowed_patch_prefixes")
    parser.add_argument("--validation-command", action="append", dest="validation_commands")
    parser.add_argument("--code-root", type=Path, default=REPO)
    parser.add_argument("--page-extract-timeout-s", type=float)
    parser.add_argument("--page-orchestrator-mode", choices=["dry_run", "live"], default="dry_run")
    parser.add_argument("--prepare-isolated-code-root", type=Path, dest="prepare_isolated_code_root_dest")
    parser.add_argument("--prepare-code-root-include", action="append", dest="prepare_isolated_code_root_include_paths")
    parser.add_argument("--prepare-code-root-force", action="store_true", dest="prepare_isolated_code_root_force")
    parser.add_argument("--scillm-mounted-workspace-prefix", action="append", type=Path, dest="scillm_mounted_workspace_prefixes")
    parser.add_argument("--stop-on-nonterminal", action="store_true")
    args = parser.parse_args()

    report = run_harness(
        pdf_path=args.pdf,
        out_dir=args.out,
        ledger_path=args.ledger,
        apply_mode=args.apply_mode,
        max_pages=args.max_pages,
        candidate_census_timeout_s=args.candidate_census_timeout_s,
        candidate_page_timeout_s=args.candidate_page_timeout_s,
        candidate_census_pages=args.candidate_census_pages,
        sample_size=args.sample_size,
        seed=args.seed,
        review_mode=args.review_mode,
        patch_mode=args.patch_mode,
        patch_backend=args.patch_backend,
        commit_mode=args.commit_mode,
        model=args.model,
        batch_id=args.batch_id,
        review_fixture_path=args.review_fixture_path,
        review_after_fixture_path=args.review_after_fixture_path,
        human_annotated_pages_json=args.human_annotated_pages_json,
        scillm_base_url=args.scillm_base_url,
        scillm_auth_token=args.scillm_auth_token,
        caller_skill=args.caller_skill,
        scillm_timeout_s=args.scillm_timeout_s,
        scillm_preflight_mode=args.scillm_preflight_mode,
        opencode_agent=args.opencode_agent,
        opencode_agent_sequence=args.opencode_agent_sequence,
        opencode_model=args.opencode_model,
        patch_prompt_profile=args.patch_prompt_profile,
        repair_strategy=args.repair_strategy,
        opencode_timeout_s=args.opencode_timeout_s,
        opencode_cleanup_session=not args.opencode_keep_session,
        opencode_skills=args.opencode_skills,
        allowed_patch_prefixes=args.allowed_patch_prefixes,
        validation_commands=args.validation_commands,
        code_root=args.code_root,
        page_extract_timeout_s=args.page_extract_timeout_s,
        page_orchestrator_mode=args.page_orchestrator_mode,
        prepare_isolated_code_root_dest=args.prepare_isolated_code_root_dest,
        prepare_isolated_code_root_include_paths=args.prepare_isolated_code_root_include_paths,
        prepare_isolated_code_root_force=args.prepare_isolated_code_root_force,
        scillm_mounted_workspace_prefixes=args.scillm_mounted_workspace_prefixes,
        stop_on_nonterminal=args.stop_on_nonterminal,
    )
    print(json.dumps({"out": str(args.out), "terminal_status": report["terminal_status"], "selected_pages": report["selected_pages"]}, sort_keys=True))
    return 0 if report["terminal_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
