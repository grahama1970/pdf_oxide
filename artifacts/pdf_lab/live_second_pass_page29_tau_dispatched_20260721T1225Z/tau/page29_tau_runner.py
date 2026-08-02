#!/usr/bin/env python3
"""Run one PDF Lab page case from a Tau local DAG node."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO = Path("/tmp/pdf_oxide_next_page_20260721")
RUN_DIR = REPO / "artifacts/pdf_lab/live_second_pass_page29_tau_dispatched_20260721T1225Z/page_case_0001_p0029"
SCILLM_ENV = Path("/home/graham/workspace/experiments/scillm/.env")


def main() -> int:
    payload = json.load(sys.stdin)
    artifact_dir = Path(os.environ["TAU_HANDOFF_COMMAND_ARTIFACT_DIR"])
    artifact_dir.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    load_env_file(SCILLM_ENV, env)

    command = [
        str(REPO / ".venv/bin/python"),
        "scripts/pdf_lab/run_page_second_pass_dag.py",
        "--pdf",
        "/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf",
        "--manifest",
        "artifacts/pdf_lab/next_candidate_selection_page29_20260721T1220Z/candidate_manifest.json",
        "--sampled-cases",
        "artifacts/pdf_lab/next_candidate_selection_page29_20260721T1220Z/sampled_page_cases.json",
        "--out",
        str(RUN_DIR.relative_to(REPO)),
        "--case-id",
        "page_case_0001_p0029",
        "--page",
        "29",
        "--ledger",
        "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json",
        "--apply-mode",
        "release",
        "--dpi",
        "72",
        "--model",
        "vlm-free2",
        "--review-mode",
        "live",
        "--patch-mode",
        "dry_run",
        "--patch-backend",
        "opencode_serve",
        "--commit-mode",
        "dry_run",
        "--scillm-base-url",
        "http://localhost:4001",
        "--caller-skill",
        "pdf-lab",
        "--scillm-timeout-s",
        "120",
        "--scillm-preflight-mode",
        "live",
        "--page-orchestrator-mode",
        "live",
        "--patch-prompt-profile",
        "plan_only",
        "--repair-strategy",
        "single",
        "--page-extract-timeout-s",
        "90",
    ]

    command_path = artifact_dir / "harness-command.json"
    stdout_path = artifact_dir / "harness.stdout.txt"
    stderr_path = artifact_dir / "harness.stderr.txt"
    command_receipt_path = artifact_dir / "harness-command-receipt.json"
    command_path.write_text(
        json.dumps(
            {
                "schema": "pdf_lab.tau_dispatched_harness_command.v1",
                "command": command,
                "cwd": str(REPO),
                "run_dir": str(RUN_DIR),
                "scillm_env_loaded": SCILLM_ENV.exists(),
                "scillm_auth_token_in_command": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=str(REPO),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=260,
            check=False,
        )
        exit_code = completed.returncode
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        stdout_path.write_text(as_text(exc.stdout), encoding="utf-8")
        stderr_path.write_text(as_text(exc.stderr), encoding="utf-8")

    observed = inspect_output(RUN_DIR)
    status = "PASS" if exit_code == 0 and observed.get("review_validation_ok") is True else "BLOCKED"
    summary = summarize(exit_code=exit_code, timed_out=timed_out, observed=observed)
    command_receipt = {
        "schema": "pdf_lab.tau_dispatched_page_second_pass_receipt.v1",
        "mocked": False,
        "live": True,
        "page_case_id": "page_case_0001_p0029",
        "page_number": 29,
        "command_exit_code": exit_code,
        "command_timed_out": timed_out,
        "tau_command_timeout_s": 260,
        "scillm_timeout_s": 120,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "run_dir": str(RUN_DIR),
        "observed": observed,
        "status": status,
        "proof_scope": {
            "proves": [
                "Tau dispatched one bounded local command node for page_case_0001_p0029.",
                "The existing PDF Lab one-page harness was exercised with live review-mode and dry-run patch/commit modes.",
                "The command emitted local artifacts and stdout/stderr receipts under the Tau command artifact directory."
            ],
            "does_not_prove": [
                "A Tau-native VLM chat-completions adapter.",
                "A Tau creator-reviewer repair loop.",
                "Provider/model semantic quality.",
                "GitHub mutation or Criterion 6 live apply."
            ]
        }
    }
    command_receipt_path.write_text(
        json.dumps(command_receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    evidence: list[dict[str, Any]] = [
        {"kind": "pdf_lab_page_second_pass_run_receipt", "path": str(command_receipt_path), "status": status},
        {"kind": "pdf_lab_page_second_pass_output_dir", "path": str(RUN_DIR), "status": "PRESENT"},
    ]
    for kind, key in (
        ("pdf_lab_review_validation", "review_validation_path"),
        ("pdf_lab_review_response", "review_response_path"),
        ("pdf_lab_review_error", "review_error_path"),
        ("pdf_lab_review_request", "review_request_path"),
    ):
        path = observed.get(key)
        if isinstance(path, str):
            evidence.append({"kind": kind, "path": path})

    response = {
        "schema": "tau.agent_handoff.v1",
        "github": payload["github"],
        "goal": payload["goal"],
        "previous_subagent": "page29-runner",
        "context": {
            "summary": summary,
            "artifacts": [item["path"] for item in evidence if "path" in item],
        },
        "result": {
            "status": status,
            "summary": summary,
            "evidence": evidence,
        },
        "rationale": "The Tau DAG contract controls dispatch; PDF Lab owns the existing page harness artifacts.",
        "next_agent": {
            "name": "human",
            "executor": "human",
            "reason": "Stop after one page-case run for audit before any repair or broader iteration.",
        },
        "required_evidence": ["pdf_lab_page_second_pass_run_receipt"],
        "stop_condition": "Stop at human or a fail-closed DAG invariant.",
    }
    print(json.dumps(response, sort_keys=True))
    return 0


def load_env_file(path: Path, env: dict[str, str]) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in env:
            env[key] = value.strip().strip('"').strip("'")


def inspect_output(run_dir: Path) -> dict[str, Any]:
    observed: dict[str, Any] = {"run_dir_exists": run_dir.exists()}
    for key, name in (
        ("review_validation_path", "review_validation.json"),
        ("review_response_path", "review_response.json"),
        ("review_error_path", "scillm_review_error.json"),
        ("review_request_path", "review_request.json"),
        ("html_review_artifact_path", "review.html"),
    ):
        matches = sorted(run_dir.rglob(name)) if run_dir.exists() else []
        if matches:
            observed[key] = str(matches[0])
    validation_path = observed.get("review_validation_path")
    if isinstance(validation_path, str):
        try:
            validation = json.loads(Path(validation_path).read_text(encoding="utf-8"))
            observed["review_validation_ok"] = validation.get("ok")
            observed["review_validation_status"] = validation.get("status")
            observed["review_validation_errors"] = validation.get("errors")
            observed["validation_result_status"] = validation.get("validation_result", {}).get("status")
        except Exception as exc:  # noqa: BLE001
            observed["review_validation_parse_error"] = f"{type(exc).__name__}: {exc}"
    return observed


def summarize(*, exit_code: int, timed_out: bool, observed: dict[str, Any]) -> str:
    if timed_out:
        return "Tau-dispatched page29 harness command timed out before producing a terminal review receipt."
    if observed.get("review_validation_ok") is True:
        return "Tau-dispatched page29 harness produced review_validation ok:true."
    if observed.get("review_error_path"):
        return "Tau-dispatched page29 harness produced a review error receipt."
    if exit_code != 0:
        return f"Tau-dispatched page29 harness exited nonzero ({exit_code}) before review_validation ok:true."
    return "Tau-dispatched page29 harness finished without review_validation ok:true."


def as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
