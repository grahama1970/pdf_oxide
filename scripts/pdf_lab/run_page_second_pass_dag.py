#!/usr/bin/env python3
"""Run one self-contained second-pass page DAG case.

This first implementation supports deterministic dry-run evidence
materialization. Model review and OpenCode patch nodes are explicit future
extensions; in dry-run mode the page fails closed as ``still_open``.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import html as html_lib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO = Path(__file__).resolve().parents[2]
PAGE_CASE_ID_RE = re.compile(r"^page_case_\d{4}_p(?P<page_number>\d{4})$")
TERMINAL_STATUSES = {
    "reviewed_clean",
    "patched_confirmed",
    "rejected_with_proof",
    "blocked_substrate",
    "human_needed",
    "still_open",
}
SAMPLED_CANDIDATE_MANIFEST_SCHEMA = "pdf_lab.second_pass.sampled_candidate_manifest.v1"
SELECTED_CANDIDATES_SCHEMA = "pdf_lab.second_pass.selected_candidates.v1"
CANDIDATE_PRESETS_SCHEMA = "pdf_lab.second_pass.candidate_presets.v1"
REVIEW_STATUSES = {"clean", "defect", "unsure", "substrate_blocked"}
DEFAULT_OPENCODE_SKILLS = ["memory", "debugger", "scillm"]
DEFAULT_TRANSPORT_CHILD_MODE = "apply_patches"
DEFAULT_ALLOWED_PATCH_PREFIXES = ["python/pdf_oxide/", "src/", "scripts/pdf_lab/", "tests/"]
TRANSPORT_SUCCESS_DELIVERY_STATES = {"completed", "acted", "idle_seen"}
DEFAULT_SCILLM_ORCHESTRATOR_OPENCODE_MODEL = "opencode-go/kimi-k2.6"
PATCH_PROMPT_PROFILES = {"full", "compact", "plan_only"}
PATCH_REPAIR_STRATEGIES = {"single", "split", "chat_plan_split"}
PAGE_ORCHESTRATOR_MODES = {"dry_run", "live"}
PATCH_PROMPT_MAX_CHARS = 8000
PATCH_PROMPT_REQUIRED_MARKERS = [
    "## Role",
    "## Task",
    "## Context",
    "## Constraints",
    "## Output Format",
    "PATCH_APPLIED",
    "PATCH_DELEGATE_BLOCKED",
]
PROMPT_WEASEL_WORDS = [
    "relevant",
    "appropriate",
    "comprehensive",
    "thorough",
    "ensure",
    "consider",
    "properly",
    "meaningful",
    "various",
    "as needed",
    "leverage",
    "utilize",
]
PROMPT_PAYLOAD_REPLACEMENTS = {
    "relevant": "named",
    "appropriate": "specific",
    "comprehensive": "complete for this case",
    "thorough": "evidence checked",
    "ensure": "verify",
    "consider": "check",
    "properly": "per the evidence",
    "meaningful": "specific",
    "various": "listed",
    "as needed": "only when required by the listed evidence",
    "leverage": "use",
    "utilize": "use",
}
PROMPT_PAYLOAD_EXACT_KEYS = {
    "candidate_id",
    "status",
    "schema",
    "preset_type",
    "block_id",
    "json_pointer",
    "source_type",
    "semantic_role",
    "case_id",
    "page_number",
    "page_index",
    "workspace_root",
    "case_dir",
    "artifact_case_dir",
    "page_before_json",
    "page_before_image",
    "page_candidates_image",
    "candidate_presets",
    "review_response",
    "review_validation",
    "patch_targets",
}
REQUIRED_PATCHED_CONFIRMED_ARTIFACTS = {
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
}
MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS = {
    "sampled_candidate_manifest.json",
    "selected_candidates.json",
    "page_before.json",
    "page_before.png",
    "page_candidates.png",
    "candidate_presets.json",
    "review_request.json",
    "review_request_validation.json",
    "review_validation.json",
    "scillm_orchestrator_page_dag_spec.json",
    "scillm_orchestrator_page_dag_spec_validation.json",
    "scillm_orchestrator_page_submission.json",
    "scillm_orchestrator_page_submission_validation.json",
    "terminal_ledger.json",
    "terminal_ledger_validation.json",
    "review.html",
}
BLOCKED_PAGE_REVIEW_BUNDLE_ARTIFACTS_BY_REASON = {
    "page_dag_setup_failed": {
        "sampled_candidate_manifest.json",
        "selected_candidates.json",
        "candidate_presets.json",
        "review_validation.json",
        "page_dag_setup_error.json",
        "terminal_ledger.json",
        "terminal_ledger_validation.json",
        "review.html",
    },
    "page_extraction_failed": {
        "sampled_candidate_manifest.json",
        "selected_candidates.json",
        "candidate_presets.json",
        "review_validation.json",
        "scillm_orchestrator_page_dag_spec.json",
        "scillm_orchestrator_page_dag_spec_validation.json",
        "page_extraction_error.json",
        "terminal_ledger.json",
        "terminal_ledger_validation.json",
        "review.html",
    },
}
PATCH_EVIDENCE_WORKSPACE_FILES = [
    "page_before.json",
    "page_before.png",
    "page_candidates.png",
    "candidate_presets.json",
    "review_response.json",
    "review_validation.json",
    "selected_candidates.json",
    "sampled_candidate_manifest.json",
]


class PageExtractionTimeout(TimeoutError):
    """Raised when page extraction exceeds the page DAG timeout."""


def is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_plain_bool(value: Any) -> bool:
    return type(value) is bool


def transport_raw_line_count(stream: dict[str, Any], *, label: str, parse_errors: list[dict[str, Any]]) -> int:
    value = stream.get("raw_line_count", 0)
    if not is_plain_int(value) or value < 0:
        parse_errors.append(
            {
                "event_type": "parse_error",
                "source": label,
                "field": "raw_line_count",
                "error": f"raw_line_count must be a non-negative integer: {value!r}",
            }
        )
        return 0
    return value


def transport_saw_message_completed(stream: dict[str, Any], *, label: str, parse_errors: list[dict[str, Any]]) -> bool:
    if "saw_message_completed" not in stream:
        return False
    value = stream.get("saw_message_completed")
    if type(value) is not bool:
        parse_errors.append(
            {
                "event_type": "parse_error",
                "source": label,
                "field": "saw_message_completed",
                "error": f"saw_message_completed must be a boolean: {value!r}",
            }
        )
        return False
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_patch_agent_sequence(primary_agent: str, agent_sequence: list[str] | None) -> list[str]:
    raw_agents = agent_sequence if agent_sequence else [primary_agent]
    agents: list[str] = []
    for agent in raw_agents:
        cleaned = str(agent).strip()
        if not cleaned or cleaned in agents:
            continue
        validate_opencode_agent_profile(cleaned)
        agents.append(cleaned)
    if not agents:
        raise ValueError("at least one OpenCode patch agent profile is required")
    return agents


def expand_patch_agent_sequence_for_transport_retries(
    agents: list[str],
    *,
    patch_mode: str,
    patch_backend: str,
) -> list[str]:
    if patch_mode == "live" and patch_backend == "scillm_orchestrator" and len(agents) == 1:
        return [agents[0], agents[0]]
    return agents


def resolve_effective_opencode_model(*, patch_mode: str, patch_backend: str, opencode_model: str | None) -> str | None:
    if opencode_model:
        return opencode_model
    if patch_mode == "live" and patch_backend == "scillm_orchestrator":
        return DEFAULT_SCILLM_ORCHESTRATOR_OPENCODE_MODEL
    return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stable_json_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def cli_setup_failure_page_case(case_id: str | None, page_number: int | None) -> dict[str, Any]:
    derived_page_number = page_number
    if derived_page_number is None and case_id:
        match = PAGE_CASE_ID_RE.fullmatch(case_id)
        if match is not None:
            derived_page_number = int(match.group("page_number"))
    if type(derived_page_number) is not int or derived_page_number < 1:
        derived_page_number = 1

    derived_case_id = case_id
    if derived_case_id:
        identity = validate_page_case_identity(
            {"case_id": derived_case_id, "page_number": derived_page_number},
        )
        if identity["ok"] is not True:
            derived_case_id = None
    if not derived_case_id:
        derived_case_id = f"page_case_0000_p{derived_page_number:04d}"

    return {
        "case_id": derived_case_id,
        "page_number": derived_page_number,
        "page_index": derived_page_number - 1,
        "candidate_ids": [],
        "strata": ["setup_failure"],
        "selection_probability_estimate": 0.0,
        "selection_reason": ["page_dag_setup_failed"],
    }


def write_page_dag_setup_failure_artifacts(
    *,
    out_dir: Path,
    pdf_path: Path,
    case_id: str | None,
    page_number: int | None,
    code_root: Path,
    opencode_model: str | None,
    patch_prompt_profile: str,
    repair_strategy: str,
    page_extract_timeout_s: float | None,
    page_orchestrator_mode: str,
    error: Exception,
) -> dict[str, Any]:
    page_case = cli_setup_failure_page_case(case_id, page_number)
    case_dir = out_dir / str(page_case["case_id"])
    case_dir.mkdir(parents=True, exist_ok=True)
    receipts = ReceiptWriter(case_dir)
    state = {
        "schema": "pdf_lab.second_pass.page_state.v1",
        "case_id": page_case["case_id"],
        "page_number": page_case["page_number"],
        "pdf_path": str(pdf_path),
        "code_root": str(code_root.resolve()),
        "opencode_model": opencode_model,
        "requested_opencode_model": opencode_model,
        "patch_prompt_profile": patch_prompt_profile,
        "repair_strategy": repair_strategy,
        "page_extract_timeout_s": page_extract_timeout_s,
        "page_orchestrator_mode": page_orchestrator_mode,
        "page_orchestrator_transport_run_id": None,
        "terminal_status": None,
        "created_at": utc_now(),
    }
    write_json(case_dir / "state.json", state)
    receipts.write(
        "initialize_page_case",
        input_artifacts=[],
        output_artifacts=["state.json"],
        command_or_endpoint="run_page_second_pass_dag.initialize_page_case",
        validator_result={"ok": True},
        next_allowed_nodes=["load_cli_inputs"],
    )

    setup_error = {
        "schema": "pdf_lab.second_pass.substrate_error.v1",
        "node_id": "load_cli_inputs",
        "endpoint": "run_page_second_pass_dag.main",
        "case_id": page_case["case_id"],
        "page_number": page_case["page_number"],
        "error_type": type(error).__name__,
        "error": str(error),
    }
    write_json(case_dir / "page_dag_setup_error.json", setup_error)
    write_json(case_dir / "sampled_candidate_manifest.json", build_sampled_candidate_manifest(page_case, []))
    write_json(case_dir / "selected_candidates.json", build_selected_candidates(page_case, []))
    write_json(case_dir / "candidate_presets.json", build_candidate_presets(page_case, []))
    review_validation = {
        "schema": "pdf_lab.second_pass.review_validation.v1",
        "ok": False,
        "errors": ["page_dag_setup_failed"],
        "page_case": {"case_id": page_case["case_id"], "page_number": page_case["page_number"]},
        "candidate_count": 0,
        "expected_candidate_ids": [],
        "seen_candidate_ids": [],
    }
    write_json(case_dir / "review_validation.json", review_validation)
    receipts.write(
        "load_cli_inputs",
        input_artifacts=[],
        output_artifacts=[
            "page_dag_setup_error.json",
            "sampled_candidate_manifest.json",
            "selected_candidates.json",
            "candidate_presets.json",
            "review_validation.json",
        ],
        command_or_endpoint="run_page_second_pass_dag.main",
        validator_result={
            "ok": False,
            "errors": ["page_dag_setup_failed"],
            "error_type": type(error).__name__,
        },
        next_allowed_nodes=["write_page_terminal_ledger"],
        exit_code=1,
    )
    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": page_case["case_id"],
        "page_number": page_case["page_number"],
        "terminal_status": "blocked_substrate",
        "reason": "page_dag_setup_failed",
        "allowed_terminal_statuses": sorted(TERMINAL_STATUSES),
        "evidence_artifacts": [
            "state.json",
            "sampled_candidate_manifest.json",
            "selected_candidates.json",
            "candidate_presets.json",
            "page_dag_setup_error.json",
            "review_validation.json",
            "review.html",
        ],
        "commit_sha": None,
    }
    return finalize_page_case(case_dir=case_dir, receipts=receipts, state=state, terminal=terminal)


def read_required_json_object(path: Path, artifact_name: str) -> tuple[dict[str, Any], list[str]]:
    try:
        payload = load_json(path)
    except Exception as exc:  # noqa: BLE001 - validation ledgers must expose malformed evidence.
        return {}, [f"{artifact_name} unreadable: {type(exc).__name__}: {exc}"]
    if not isinstance(payload, dict):
        return {}, [f"{artifact_name} is not a JSON object"]
    return payload, []


def ensure_git_info_exclude(repo: Path, pattern: str) -> None:
    git_dir = repo / ".git"
    if not git_dir.is_dir():
        return
    info_dir = git_dir / "info"
    info_dir.mkdir(parents=True, exist_ok=True)
    exclude_path = info_dir / "exclude"
    existing = exclude_path.read_text(encoding="utf-8") if exclude_path.is_file() else ""
    if pattern not in existing.splitlines():
        suffix = "" if existing.endswith("\n") or not existing else "\n"
        exclude_path.write_text(existing + suffix + pattern + "\n", encoding="utf-8")


def materialize_patch_evidence_workspace(case_dir: Path, code_root: Path, case_id: str) -> dict[str, Any]:
    ensure_git_info_exclude(code_root, ".pdf_lab_runtime/")
    workspace_case_dir = code_root / ".pdf_lab_runtime" / "page_cases" / case_id
    workspace_case_dir.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, str]] = []
    missing: list[str] = []
    for name in PATCH_EVIDENCE_WORKSPACE_FILES:
        source = case_dir / name
        if not source.is_file():
            missing.append(name)
            continue
        dest = workspace_case_dir / name
        shutil.copyfile(source, dest)
        copied.append({"artifact": name, "source": str(source.resolve()), "workspace_path": str(dest.resolve())})
    result = {
        "schema": "pdf_lab.second_pass.patch_evidence_workspace.v1",
        "case_id": case_id,
        "code_root": str(code_root.resolve()),
        "workspace_case_dir": str(workspace_case_dir.resolve()),
        "git_info_exclude_pattern": ".pdf_lab_runtime/",
        "copied": copied,
        "missing": missing,
        "ok": not missing,
    }
    write_json(case_dir / "patch_evidence_workspace.json", result)
    return result


def _image_data_uri(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


class ReceiptWriter:
    def __init__(self, case_dir: Path) -> None:
        self.case_dir = case_dir
        self.receipt_dir = case_dir / "receipts"
        self.receipt_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        node_id: str,
        *,
        input_artifacts: list[str],
        output_artifacts: list[str],
        command_or_endpoint: str,
        validator_result: dict[str, Any],
        next_allowed_nodes: list[str],
        exit_code: int = 0,
        started_at: str | None = None,
    ) -> dict[str, Any]:
        receipt = {
            "schema": "pdf_lab.second_pass.node_receipt.v1",
            "node_id": node_id,
            "started_at": started_at or utc_now(),
            "finished_at": utc_now(),
            "input_artifacts": input_artifacts,
            "output_artifacts": output_artifacts,
            "command_or_endpoint": command_or_endpoint,
            "exit_code": exit_code,
            "validator_result": validator_result,
            "next_allowed_nodes": next_allowed_nodes,
        }
        write_json(self.receipt_dir / f"{node_id}.json", receipt)
        return receipt


def _case_by_id_or_page(sampled_cases: dict[str, Any], case_id: str | None, page_number: int | None) -> dict[str, Any]:
    cases = sampled_cases.get("page_cases") or []
    for case in cases:
        if case_id and case.get("case_id") == case_id:
            return case
        case_page_number = case.get("page_number")
        if page_number is not None and type(case_page_number) is int and case_page_number == page_number:
            return case
    raise ValueError("page case not found")


def validate_page_case_identity(page_case: dict[str, Any], *, allow_after_patch: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    case_id = page_case.get("case_id")
    page_number = page_case.get("page_number")
    if not isinstance(case_id, str) or not case_id:
        errors.append("page_case case_id must be non-empty")
    else:
        base_case_id = case_id
        if allow_after_patch and case_id.endswith(":after_patch"):
            base_case_id = case_id.removesuffix(":after_patch")
        case_id_match = PAGE_CASE_ID_RE.fullmatch(base_case_id)
        if case_id_match is None:
            errors.append(f"{case_id} case_id must match page_case_####_p####")
        elif type(page_number) is int and int(case_id_match.group("page_number")) != page_number:
            errors.append(f"{case_id} case_id page suffix does not match page_number {page_number}")
    if type(page_number) is not int or page_number < 1:
        errors.append("page_case page_number must be a positive integer")
    return {
        "schema": "pdf_lab.second_pass.page_case_identity_validation.v1",
        "ok": not errors,
        "errors": errors,
        "case_id": case_id,
        "page_number": page_number,
    }


def _candidate_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(candidate["candidate_id"]): candidate
        for candidate in manifest.get("candidates") or []
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }


def materialize_case_candidates(manifest: dict[str, Any], page_case: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = _candidate_map(manifest)
    selected = []
    missing = []
    for candidate_id in page_case.get("candidate_ids") or []:
        candidate = candidates.get(candidate_id)
        if candidate is None:
            missing.append(candidate_id)
        else:
            selected.append(candidate)
    if missing:
        raise ValueError(f"sampled page case references missing candidate_ids: {missing}")
    return selected


def build_sampled_candidate_manifest(page_case: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": SAMPLED_CANDIDATE_MANIFEST_SCHEMA,
        "page_case": page_case,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def build_selected_candidates(page_case: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": SELECTED_CANDIDATES_SCHEMA,
        "page_case": {
            "case_id": page_case.get("case_id"),
            "page_number": page_case.get("page_number"),
        },
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def candidate_presets_case_id_matches_review_page_case(preset_case_id: Any, review_case_id: Any) -> bool:
    if preset_case_id == review_case_id:
        return True
    if isinstance(preset_case_id, str) and isinstance(review_case_id, str) and review_case_id.endswith(":after_patch"):
        return preset_case_id == review_case_id.removesuffix(":after_patch")
    return False


def extract_page(
    pdf_path: Path,
    page_number: int,
    ledger_path: Path | None,
    apply_mode: str,
    repo: Path = REPO,
) -> dict[str, Any]:
    for module_name in list(sys.modules):
        if module_name == "snapshot_current_extraction" or module_name == "pdf_oxide" or module_name.startswith("pdf_oxide."):
            sys.modules.pop(module_name, None)
    script_path = str(repo / "scripts/pdf_lab")
    python_path = str(repo / "python")
    sys.path.insert(0, python_path)
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot  # noqa: PLC0415

        return snapshot._extract_page(pdf_path, page_number - 1, ledger_path, apply_mode)
    finally:
        for path in (script_path, python_path):
            with contextlib.suppress(ValueError):
                sys.path.remove(path)


def extract_page_subprocess(
    pdf_path: Path,
    page_number: int,
    ledger_path: Path | None,
    apply_mode: str,
    repo: Path,
    timeout_s: float,
) -> dict[str, Any]:
    child_code = (
        "import json\n"
        "import sys\n"
        "from pathlib import Path\n\n"
        f"sys.path.insert(0, {str(repo / 'scripts/pdf_lab')!r})\n"
        f"sys.path.insert(0, {str(repo / 'python')!r})\n"
        "import snapshot_current_extraction as snapshot\n\n"
        "pdf_path = Path(sys.argv[1])\n"
        "page_index = int(sys.argv[2])\n"
        "ledger_path = None if sys.argv[3] == '__NONE__' else Path(sys.argv[3])\n"
        "apply_mode = sys.argv[4]\n"
        "out_path = Path(sys.argv[5])\n"
        "page = snapshot._extract_page(pdf_path, page_index, ledger_path, apply_mode)\n"
        "out_path.write_text(json.dumps(page), encoding='utf-8')\n"
    )
    with tempfile.TemporaryDirectory(prefix="pdf_lab_page_dag_extract_") as tmpdir:
        out_path = Path(tmpdir) / "page.json"
        completed: subprocess.CompletedProcess[str] | None = None
        try:
            env = os.environ.copy()
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    child_code,
                    str(pdf_path),
                    str(page_number - 1),
                    str(ledger_path) if ledger_path else "__NONE__",
                    apply_mode,
                    str(out_path),
                ],
                cwd=repo,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout_s,
                check=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise PageExtractionTimeout(f"page {page_number} extraction exceeded page_extract_timeout_s={timeout_s}") from exc
        except subprocess.CalledProcessError as exc:
            stderr_tail = (exc.stderr or "")[-1000:]
            stdout_tail = (exc.stdout or "")[-1000:]
            raise RuntimeError(
                f"page {page_number} extraction subprocess failed with exit code {exc.returncode}: "
                f"stderr_tail={stderr_tail!r} stdout_tail={stdout_tail!r}"
            ) from exc
        return json.loads(out_path.read_text(encoding="utf-8"))


def extract_page_for_code_root(
    pdf_path: Path,
    page_number: int,
    ledger_path: Path | None,
    apply_mode: str,
    code_root: Path,
    page_extract_timeout_s: float | None = None,
) -> dict[str, Any]:
    repo = REPO if code_root.resolve() == REPO.resolve() else code_root
    if repo.resolve() != REPO.resolve():
        return extract_page_subprocess(
            pdf_path,
            page_number,
            ledger_path,
            apply_mode,
            repo,
            page_extract_timeout_s if page_extract_timeout_s is not None and page_extract_timeout_s > 0 else 300.0,
        )
    if page_extract_timeout_s is not None and page_extract_timeout_s > 0:
        return extract_page_subprocess(pdf_path, page_number, ledger_path, apply_mode, repo, page_extract_timeout_s)
    if repo.resolve() == REPO.resolve():
        return extract_page(pdf_path, page_number, ledger_path, apply_mode)
    return extract_page(pdf_path, page_number, ledger_path, apply_mode, repo=repo)


def run_extract_page_for_code_root(
    *,
    pdf_path: Path,
    page_number: int,
    ledger_path: Path | None,
    apply_mode: str,
    code_root: Path,
    page_extract_timeout_s: float | None,
) -> dict[str, Any]:
    if page_extract_timeout_s is not None and page_extract_timeout_s > 0:
        return extract_page_for_code_root(
            pdf_path,
            page_number,
            ledger_path,
            apply_mode,
            code_root,
            page_extract_timeout_s=page_extract_timeout_s,
        )
    return extract_page_for_code_root(pdf_path, page_number, ledger_path, apply_mode, code_root)


def render_original_page(pdf_path: Path, page_number: int, out: Path, dpi: int) -> None:
    import fitz  # noqa: PLC0415

    doc = fitz.open(pdf_path)
    try:
        page = doc.load_page(page_number - 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.save(out)
    finally:
        doc.close()


def normalized_candidate_bbox(candidate: dict[str, Any], index: int) -> tuple[float, float, float, float]:
    bbox = candidate.get("bbox")
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or not all(type(value) in {int, float} and math.isfinite(float(value)) for value in bbox)
    ):
        raise ValueError(f"candidate[{index}] bbox must be four finite numbers")
    x0, y0, x1, y1 = [float(value) for value in bbox]
    if any(value < 0.0 or value > 1.0 for value in [x0, y0, x1, y1]):
        raise ValueError(f"candidate[{index}] bbox values must be normalized to [0, 1]")
    if x0 > x1 or y0 > y1:
        raise ValueError(f"candidate[{index}] bbox coordinates are not ordered [x0, y0, x1, y1]")
    return x0, y0, x1, y1


def render_candidate_overlay(pdf_path: Path, page_number: int, candidates: list[dict[str, Any]], out: Path, dpi: int) -> None:
    import fitz  # noqa: PLC0415

    doc = fitz.open(pdf_path)
    try:
        page = doc.load_page(page_number - 1)
        width = float(page.rect.width)
        height = float(page.rect.height)
        for idx, candidate in enumerate(candidates):
            x0, y0, x1, y1 = normalized_candidate_bbox(candidate, idx)
            rect = fitz.Rect(x0 * width, y0 * height, x1 * width, y1 * height)
            color = (1, 0, 0) if candidate.get("preset_type") in {"unknown_layout", "side_chrome"} else (0, 0.35, 1)
            page.draw_rect(rect, color=color, width=1.5)
            label_point = fitz.Point(rect.x0, max(8, rect.y0 - 2))
            page.insert_text(label_point, str(idx + 1), fontsize=8, color=color)
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.save(out)
    finally:
        doc.close()


def build_candidate_presets(page_case: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    def candidate_question(candidate: dict[str, Any]) -> str:
        features = candidate.get("features") if isinstance(candidate.get("features"), dict) else {}
        if candidate.get("preset_type") == "table" and features.get("table_bbox_clipped_to_page") is True:
            return (
                "Does the rendered page evidence agree with this table candidate's visible bbox, "
                "row/column structure, and explicit off-page/full-bbox metadata?"
            )
        return (
            "Does the rendered page evidence agree with this extracted candidate's preset type, "
            "bounding box, text, and nearby structure?"
        )

    return {
        "schema": CANDIDATE_PRESETS_SCHEMA,
        "page_case": {
            "case_id": page_case.get("case_id"),
            "page_number": page_case.get("page_number"),
        },
        "candidate_count": len(candidates),
        "candidates": [
            {
                "candidate_id": candidate["candidate_id"],
                "preset_type": candidate["preset_type"],
                "bbox": candidate.get("bbox"),
                "features": candidate.get("features") or {},
                "question": candidate_question(candidate),
                "allowed_review_statuses": ["clean", "defect", "unsure", "substrate_blocked"],
            }
            for candidate in candidates
        ],
    }


def build_review_request(
    *,
    case_dir: Path,
    page_case: dict[str, Any],
    page_json_path: str,
    original_image_path: str,
    annotated_image_path: str,
    candidate_presets_path: str,
    model: str,
    batch_id: str,
) -> dict[str, Any]:
    item_id = str(page_case["case_id"])
    page_json = load_json(case_dir / page_json_path)
    candidate_presets = load_json(case_dir / candidate_presets_path)
    prompt_text = (
        "You are reviewing PDF Oxide extraction evidence for one page. "
        "Compare the original rendered page, the annotated candidate image, "
        "the extracted JSON, and the candidate preset questions. Return JSON only. "
        "You may classify candidates as clean, defect, unsure, or substrate_blocked. "
        "Do not claim that a bug is fixed, patched, resolved, committed, or closed.\n\n"
        "Required response schema:\n"
        "{\n"
        '  "schema": "pdf_lab.second_pass.review_response.v1",\n'
        '  "page_status": "clean|defect|unsure|substrate_blocked",\n'
        '  "candidate_findings": [\n'
        '    {"candidate_id": "...", "status": "clean|defect|unsure|substrate_blocked", '
        '"evidence": "...", "rationale": "...", "suggested_fix_surface": "..."}\n'
        "  ],\n"
        '  "page_rationale": "..."\n'
        "}\n\n"
        f"Page case:\n{json.dumps(page_case, indent=2, sort_keys=True)}\n\n"
        f"Candidate presets:\n{json.dumps(candidate_presets, indent=2, sort_keys=True)}\n\n"
        f"Extracted page JSON:\n{json.dumps(page_json, indent=2, sort_keys=True)}"
    )
    scillm_payload = {
        "model": model,
        "reasoning_effort": "high",
        "response_format": {"type": "json_object"},
        "scillm_metadata": {"batch_id": batch_id, "item_id": item_id},
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": _image_data_uri(case_dir / original_image_path)}},
                    {"type": "image_url", "image_url": {"url": _image_data_uri(case_dir / annotated_image_path)}},
                ],
            }
        ],
    }
    return {
        "schema": "pdf_lab.second_pass.review_request.v1",
        "endpoint": "POST /v1/chat/completions",
        "model": model,
        "reasoning_effort": "high",
        "response_format": {"type": "json_object"},
        "scillm_metadata": {"batch_id": batch_id, "item_id": item_id},
        "page_case": page_case,
        "artifacts": {
            "page_json": page_json_path,
            "original_image": original_image_path,
            "annotated_image": annotated_image_path,
            "candidate_presets": candidate_presets_path,
        },
        "scillm_payload": scillm_payload,
        "required_response_schema": {
            "schema": "pdf_lab.second_pass.review_response.v1",
            "page_status": ["clean", "defect", "unsure", "substrate_blocked"],
            "candidate_findings": "one finding per candidate_id",
            "page_rationale": "non-empty page-level rationale tied to rendered and extracted evidence",
        },
    }


def validate_review_request_contract(case_dir: Path, review_request: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if review_request.get("schema") != "pdf_lab.second_pass.review_request.v1":
        errors.append("review_request schema mismatch")
    if review_request.get("endpoint") != "POST /v1/chat/completions":
        errors.append("review_request endpoint mismatch")
    if review_request.get("response_format") != {"type": "json_object"}:
        errors.append("review_request response_format must require json_object")
    if review_request.get("required_response_schema", {}).get("schema") != "pdf_lab.second_pass.review_response.v1":
        errors.append("review_request required_response_schema mismatch")
    artifacts = review_request.get("artifacts")
    if not isinstance(artifacts, dict):
        errors.append("review_request artifacts must be an object")
        artifacts = {}
    for key in ["page_json", "original_image", "annotated_image", "candidate_presets"]:
        artifact = artifacts.get(key)
        if not isinstance(artifact, str) or not artifact:
            errors.append(f"review_request artifacts.{key} missing")
        elif Path(artifact).is_absolute() or ".." in Path(artifact).parts:
            errors.append(f"review_request artifacts.{key} unsafe path: {artifact}")
        elif not (case_dir / artifact).is_file():
            errors.append(f"review_request artifact does not exist: {artifact}")
    payload = review_request.get("scillm_payload")
    if not isinstance(payload, dict):
        errors.append("review_request scillm_payload must be an object")
        payload = {}
    if payload.get("model") != review_request.get("model"):
        errors.append("scillm_payload model must match review_request model")
    if payload.get("reasoning_effort") != review_request.get("reasoning_effort"):
        errors.append("scillm_payload reasoning_effort must match review_request reasoning_effort")
    if payload.get("response_format") != {"type": "json_object"}:
        errors.append("scillm_payload response_format must require json_object")
    if payload.get("scillm_metadata") != review_request.get("scillm_metadata"):
        errors.append("scillm_payload scillm_metadata must match review_request scillm_metadata")
    metadata = payload.get("scillm_metadata")
    if not isinstance(metadata, dict) or not metadata.get("batch_id") or not metadata.get("item_id"):
        errors.append("scillm_payload scillm_metadata must include batch_id and item_id")
    page_case = review_request.get("page_case")
    if not isinstance(page_case, dict):
        errors.append("review_request page_case must be an object")
        page_case = {}
    page_case_identity = validate_page_case_identity(page_case, allow_after_patch=True)
    if page_case_identity["ok"] is not True:
        errors.extend(f"review_request {error}" for error in page_case_identity["errors"])
    case_id = page_case.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        errors.append("review_request page_case.case_id must be non-empty")
    elif isinstance(metadata, dict) and metadata.get("item_id") != case_id:
        errors.append("scillm_payload scillm_metadata.item_id must match review_request page_case.case_id")
    page_case_number = page_case.get("page_number")
    if type(page_case_number) is int:
        page_json_artifact = artifacts.get("page_json")
        if (
            isinstance(page_json_artifact, str)
            and page_json_artifact
            and not Path(page_json_artifact).is_absolute()
            and ".." not in Path(page_json_artifact).parts
            and (case_dir / page_json_artifact).is_file()
        ):
            try:
                page_json_payload = json.loads((case_dir / page_json_artifact).read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - malformed evidence is a validation failure.
                errors.append(f"review_request artifacts.page_json unreadable: {type(exc).__name__}: {exc}")
            else:
                if isinstance(page_json_payload, dict):
                    for key in ["page", "page_number"]:
                        if key in page_json_payload and page_json_payload.get(key) != page_case_number:
                            errors.append(f"review_request artifacts.page_json {key} does not match page_case.page_number")
                    if "pdf_page_index" in page_json_payload and page_json_payload.get("pdf_page_index") != page_case_number - 1:
                        errors.append("review_request artifacts.page_json pdf_page_index does not match page_case.page_number")
                else:
                    errors.append("review_request artifacts.page_json must contain a JSON object")
    expected_candidate_ids = page_case.get("candidate_ids")
    candidate_presets_artifact = artifacts.get("candidate_presets")
    if isinstance(expected_candidate_ids, list) and (
        isinstance(candidate_presets_artifact, str)
        and candidate_presets_artifact
        and not Path(candidate_presets_artifact).is_absolute()
        and ".." not in Path(candidate_presets_artifact).parts
        and (case_dir / candidate_presets_artifact).is_file()
    ):
        try:
            candidate_presets_payload = json.loads((case_dir / candidate_presets_artifact).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - malformed evidence is a validation failure.
            errors.append(f"review_request artifacts.candidate_presets unreadable: {type(exc).__name__}: {exc}")
        else:
            if not isinstance(candidate_presets_payload, dict):
                errors.append("review_request artifacts.candidate_presets must contain a JSON object")
                candidate_presets_payload = {}
            if candidate_presets_payload.get("schema") != CANDIDATE_PRESETS_SCHEMA:
                errors.append("review_request artifacts.candidate_presets schema mismatch")
            preset_page_case = candidate_presets_payload.get("page_case")
            if not isinstance(preset_page_case, dict):
                errors.append("review_request artifacts.candidate_presets page_case must be an object")
                preset_page_case = {}
            if not candidate_presets_case_id_matches_review_page_case(preset_page_case.get("case_id"), case_id):
                errors.append("review_request artifacts.candidate_presets page_case.case_id does not match page_case.case_id")
            if preset_page_case.get("page_number") != page_case_number:
                errors.append("review_request artifacts.candidate_presets page_case.page_number does not match page_case.page_number")
            preset_candidates = candidate_presets_payload.get("candidates") if isinstance(candidate_presets_payload, dict) else None
            if not isinstance(preset_candidates, list):
                errors.append("review_request artifacts.candidate_presets candidates must be a list")
            else:
                preset_candidate_ids = sorted(
                    candidate["candidate_id"]
                    for candidate in preset_candidates
                    if isinstance(candidate, dict) and isinstance(candidate.get("candidate_id"), str)
                )
                if len(preset_candidate_ids) != len(preset_candidates):
                    errors.append("review_request artifacts.candidate_presets candidates contain missing candidate_id")
                candidate_count = candidate_presets_payload.get("candidate_count")
                if type(candidate_count) is not int or candidate_count < 0:
                    errors.append("review_request artifacts.candidate_presets candidate_count must be a non-negative integer")
                elif candidate_count != len(preset_candidates):
                    errors.append("review_request artifacts.candidate_presets candidate_count does not match candidates")
                if preset_candidate_ids != sorted(str(candidate_id) for candidate_id in expected_candidate_ids):
                    errors.append("review_request artifacts.candidate_presets candidate_ids do not match page_case.candidate_ids")
    messages = payload.get("messages")
    if not isinstance(messages, list) or len(messages) != 1:
        errors.append("scillm_payload must contain exactly one user message")
        content = []
    else:
        message = messages[0]
        if not isinstance(message, dict) or message.get("role") != "user":
            errors.append("scillm_payload message role must be user")
            content = []
        else:
            content = message.get("content")
    if not isinstance(content, list):
        errors.append("scillm_payload user content must be a list")
        content = []
    text_parts = [part for part in content if isinstance(part, dict) and part.get("type") == "text"]
    image_parts = [part for part in content if isinstance(part, dict) and part.get("type") == "image_url"]
    prompt_text = ""
    if len(text_parts) != 1:
        errors.append("scillm_payload must include exactly one text prompt part")
    else:
        prompt_text = str(text_parts[0].get("text") or "")
        if not prompt_text.strip():
            errors.append("scillm_payload text prompt is empty")
    if prompt_text:
        expected_page_case_text = f"Page case:\n{json.dumps(page_case, indent=2, sort_keys=True)}"
        if expected_page_case_text not in prompt_text:
            errors.append("scillm_payload text prompt does not include current review_request page_case")
        for artifact_key, heading in [
            ("candidate_presets", "Candidate presets"),
            ("page_json", "Extracted page JSON"),
        ]:
            artifact_name = artifacts.get(artifact_key)
            if (
                isinstance(artifact_name, str)
                and artifact_name
                and not Path(artifact_name).is_absolute()
                and ".." not in Path(artifact_name).parts
                and (case_dir / artifact_name).is_file()
            ):
                try:
                    artifact_payload = json.loads((case_dir / artifact_name).read_text(encoding="utf-8"))
                except Exception as exc:  # noqa: BLE001 - malformed evidence is a validation failure.
                    errors.append(f"review_request artifacts.{artifact_key} unreadable: {type(exc).__name__}: {exc}")
                    continue
                expected_artifact_text = f"{heading}:\n{json.dumps(artifact_payload, indent=2, sort_keys=True)}"
                if expected_artifact_text not in prompt_text:
                    errors.append(f"scillm_payload text prompt does not include current artifacts.{artifact_key}")
    if len(image_parts) != 2:
        errors.append("scillm_payload must include exactly two image_url evidence parts")
    else:
        expected_image_artifacts = [
            ("original_image", artifacts.get("original_image")),
            ("annotated_image", artifacts.get("annotated_image")),
        ]
        for idx, image_part in enumerate(image_parts, start=1):
            image_url = image_part.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else None
            if not isinstance(url, str) or not url.startswith("data:image/") or ";base64," not in url:
                errors.append(f"scillm_payload image_url part {idx} must be a base64 data URI")
                continue
            artifact_key, artifact_name = expected_image_artifacts[idx - 1]
            if isinstance(artifact_name, str) and artifact_name and not Path(artifact_name).is_absolute() and ".." not in Path(artifact_name).parts:
                artifact_path = case_dir / artifact_name
                if artifact_path.is_file():
                    try:
                        payload_bytes = base64.b64decode(url.split(";base64,", 1)[1], validate=True)
                    except Exception as exc:  # noqa: BLE001 - malformed base64 is a validation failure.
                        errors.append(f"scillm_payload image_url part {idx} base64 decode failed: {type(exc).__name__}: {exc}")
                    else:
                        if payload_bytes != artifact_path.read_bytes():
                            errors.append(f"scillm_payload image_url part {idx} does not match artifacts.{artifact_key}")
    return {
        "schema": "pdf_lab.second_pass.review_request_validation.v1",
        "ok": not errors,
        "errors": errors,
        "artifact_paths": artifacts,
        "image_part_count": len(image_parts),
        "text_part_count": len(text_parts),
        "scillm_metadata": payload.get("scillm_metadata"),
    }


def build_page_orchestrator_dag_spec(
    *,
    page_case: dict[str, Any],
    candidates: list[dict[str, Any]],
    review_request_artifact: str | None,
    patch_backend: str,
    patch_mode: str,
    review_mode: str,
    repair_strategy: str,
    opencode_agent: str,
    opencode_agent_sequence: list[str] | None,
    opencode_model: str | None,
    code_root: Path,
    caller_skill: str,
    page_extract_timeout_s: float | None,
    status: str,
) -> dict[str, Any]:
    patch_agents = expand_patch_agent_sequence_for_transport_retries(
        normalize_patch_agent_sequence(opencode_agent, opencode_agent_sequence),
        patch_mode=patch_mode,
        patch_backend=patch_backend,
    )
    if patch_backend == "scillm_orchestrator":
        patch_endpoint = "POST /v1/scillm/opencode/transport/runs + children + message"
        patch_surface = "scillm_opencode_transport"
        patch_runtime_owner = "scillm_orchestrator"
    else:
        patch_endpoint = "POST /v1/scillm/opencode/runs"
        patch_surface = "scillm_opencode_serve"
        patch_runtime_owner = "scillm_opencode_serve"
    nodes = [
        {
            "node_id": "load_sampled_candidate_manifest",
            "state_owner": "scillm_orchestrator",
            "runtime_owner": "pdf_lab_deterministic_harness",
            "kind": "deterministic_materialization",
            "required_outputs": ["sampled_candidate_manifest.json"],
        },
        {
            "node_id": "extract_page_json",
            "state_owner": "scillm_orchestrator",
            "runtime_owner": "pdf_lab_deterministic_harness",
            "kind": "deterministic_extractor",
            "timeout_s": page_extract_timeout_s,
            "required_outputs": ["page_before.json"],
            "failure_terminal_status": "blocked_substrate",
        },
        {
            "node_id": "render_page_evidence",
            "state_owner": "scillm_orchestrator",
            "runtime_owner": "pdf_lab_deterministic_harness",
            "kind": "deterministic_visual_evidence",
            "required_outputs": ["page_before.png", "page_candidates.png"],
        },
        {
            "node_id": "scillm_one_shot_page_review",
            "state_owner": "scillm_orchestrator",
            "runtime_owner": "scillm_chat",
            "kind": "bounded_llm_review",
            "endpoint": "POST /v1/chat/completions",
            "required_headers": ["Authorization", "X-Caller-Skill"],
            "caller_skill": caller_skill,
            "mode": review_mode,
            "request_artifact": review_request_artifact,
            "required_outputs": ["review_response.json", "review_validation.json"],
        },
        {
            "node_id": "validate_review_response",
            "state_owner": "scillm_orchestrator",
            "runtime_owner": "pdf_lab_deterministic_harness",
            "kind": "deterministic_gate",
            "required_outputs": ["review_validation.json"],
        },
        {
            "node_id": "patch_delegate_attempts",
            "state_owner": "scillm_orchestrator",
            "runtime_owner": patch_runtime_owner,
            "kind": "bounded_patch_executor",
            "endpoint": patch_endpoint,
            "surface": patch_surface,
            "required_headers": ["Authorization", "X-Caller-Skill"],
            "caller_skill": caller_skill,
            "mode": patch_mode,
            "agent_profiles": patch_agents,
            "opencode_model": opencode_model,
            "required_outputs": ["patch_attempts_ledger.json", "patch_validation.json"],
        },
        {
            "node_id": "validate_patch_file_scope",
            "state_owner": "scillm_orchestrator",
            "runtime_owner": "pdf_lab_deterministic_harness",
            "kind": "deterministic_gate",
            "required_outputs": ["patch_delta.json", "patch_scope_validation.json"],
        },
        {
            "node_id": "run_page_targeted_tests",
            "state_owner": "scillm_orchestrator",
            "runtime_owner": "pdf_lab_deterministic_harness",
            "kind": "deterministic_gate",
            "required_outputs": ["test_validation.json"],
        },
        {
            "node_id": "reextract_page_after_patch",
            "state_owner": "scillm_orchestrator",
            "runtime_owner": "pdf_lab_deterministic_harness",
            "kind": "deterministic_extractor",
            "timeout_s": page_extract_timeout_s,
            "required_outputs": [
                "page_after.json",
                "page_after.png",
                "page_after_candidates.png",
                "review_after_request.json",
                "review_after_request_validation.json",
            ],
        },
        {
            "node_id": "rerun_page_review_after_patch",
            "state_owner": "scillm_orchestrator",
            "runtime_owner": "scillm_chat",
            "kind": "bounded_llm_review",
            "endpoint": "POST /v1/chat/completions",
            "required_headers": ["Authorization", "X-Caller-Skill"],
            "caller_skill": caller_skill,
            "required_outputs": ["review_after_response.json", "review_after_validation.json"],
        },
        {
            "node_id": "deterministic_page_closure_gate",
            "state_owner": "scillm_orchestrator",
            "runtime_owner": "pdf_lab_deterministic_harness",
            "kind": "deterministic_gate",
            "required_inputs": [
                "patch_delta.json",
                "patch_scope_validation.json",
                "test_validation.json",
                "page_after.json",
                "review_after_response.json",
                "review_after_validation.json",
                "review_after_request_validation.json",
            ],
            "required_outputs": [],
        },
        {
            "node_id": "commit_page_bug_fix",
            "state_owner": "scillm_orchestrator",
            "runtime_owner": "pdf_lab_git_gate",
            "kind": "deterministic_commit_gate",
            "required_outputs": ["commit_gate.json", "commit_acceptance_gate.json", "revertability_check.json"],
        },
    ]
    if repair_strategy == "split":
        nodes.insert(
            5,
            {
                "node_id": "repair_diagnosis_attempts",
                "state_owner": "scillm_orchestrator",
                "runtime_owner": patch_runtime_owner,
                "kind": "bounded_diagnosis_executor",
                "endpoint": patch_endpoint,
                "surface": patch_surface,
                "required_headers": ["Authorization", "X-Caller-Skill"],
                "caller_skill": caller_skill,
                "mode": patch_mode,
                "agent_profiles": patch_agents,
                "opencode_model": opencode_model,
                "required_outputs": ["repair_diagnosis_validation.json"],
            },
        )
    elif repair_strategy == "chat_plan_split":
        nodes.insert(
            5,
            {
                "node_id": "scillm_repair_plan",
                "state_owner": "scillm_orchestrator",
                "runtime_owner": "scillm_chat",
                "kind": "bounded_llm_repair_plan",
                "endpoint": "POST /v1/chat/completions",
                "required_headers": ["Authorization", "X-Caller-Skill"],
                "caller_skill": caller_skill,
                "mode": patch_mode,
                "required_outputs": ["repair_plan_validation.json"],
            },
        )
    return {
        "schema": "pdf_lab.second_pass.page_orchestrator_dag_spec.v1",
        "case_id": page_case["case_id"],
        "page_number": page_case["page_number"],
        "status": status,
        "target_dag_state_owner": "scillm_orchestrator",
        "orchestration_contract": {
            "mode": "scillm_transport_parent_owns_page_dag_state",
            "local_pdf_lab_role": "deterministic_substrate_and_acceptance_gate",
            "project_agent_role": "final_reviewer_only",
            "executor_outputs_are": "evidence_not_truth",
            "state_owner_required_on_every_node": "scillm_orchestrator",
        },
        "current_planner_role": "pdf_lab_project_agent_final_reviewer_only",
        "deterministic_gate_owner": "pdf_lab_harness",
        "model_and_agent_call_rule": "all model, VLM, diagnosis, and patch delegate nodes must use scillm localhost HTTP endpoints with X-Caller-Skill",
        "transport_contract": {
            "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
            "stream": True,
            "required_event_artifacts": ["transport_event_stream.json", "transport_events.jsonl"],
            "terminal_event": "message.completed",
            "blocked_events": ["permission_requested", "session_error", "tool_call:status=error"],
        },
        "page_case": page_case,
        "candidate_ids": [candidate["candidate_id"] for candidate in candidates],
        "candidate_count": len(candidates),
        "patch_backend": patch_backend,
        "patch_mode": patch_mode,
        "review_mode": review_mode,
        "repair_strategy": repair_strategy,
        "code_root": str(code_root.resolve()),
        "nodes": nodes,
        "terminal_requirements": {
            "terminal_ledger": "terminal_ledger.json",
            "review_bundle": "review_bundle.zip",
            "patched_confirmed_requires": [*sorted(REQUIRED_PATCHED_CONFIRMED_ARTIFACTS), "commit_sha"],
            "one_git_commit_per_verified_bug_fix": True,
        },
    }


def validate_page_orchestrator_dag_spec(spec: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if spec.get("schema") != "pdf_lab.second_pass.page_orchestrator_dag_spec.v1":
        errors.append("schema must be pdf_lab.second_pass.page_orchestrator_dag_spec.v1")
    spec_identity = validate_page_case_identity(
        {
            "case_id": spec.get("case_id"),
            "page_number": spec.get("page_number"),
        },
    )
    if spec_identity["ok"] is not True:
        errors.extend(f"orchestrator DAG {error}" for error in spec_identity["errors"])
    candidate_ids = spec.get("candidate_ids")
    if not isinstance(candidate_ids, list):
        errors.append("orchestrator DAG candidate_ids must be a list")
        normalized_candidate_ids: list[str] = []
    else:
        invalid_candidate_ids = [
            candidate_id
            for candidate_id in candidate_ids
            if not isinstance(candidate_id, str) or not candidate_id
        ]
        if invalid_candidate_ids:
            errors.append(f"orchestrator DAG candidate_ids must be non-empty strings: {invalid_candidate_ids}")
        duplicate_candidate_ids = sorted(
            candidate_id
            for candidate_id, count in Counter(
                candidate_id
                for candidate_id in candidate_ids
                if isinstance(candidate_id, str) and candidate_id
            ).items()
            if count > 1
        )
        if duplicate_candidate_ids:
            errors.append(f"orchestrator DAG candidate_ids contain duplicates: {duplicate_candidate_ids}")
        normalized_candidate_ids = [
            candidate_id for candidate_id in candidate_ids if isinstance(candidate_id, str) and candidate_id
        ]
        candidate_count = spec.get("candidate_count")
        if type(candidate_count) is not int or candidate_count < 0:
            errors.append("orchestrator DAG candidate_count must be a non-negative integer")
        elif candidate_count != len(candidate_ids):
            errors.append("orchestrator DAG candidate_count does not match candidate_ids")
    node_ids = [node.get("node_id") for node in spec.get("nodes") or [] if isinstance(node, dict)]
    for required in [
        "extract_page_json",
        "scillm_one_shot_page_review",
        "validate_review_response",
        "patch_delegate_attempts",
        "reextract_page_after_patch",
        "rerun_page_review_after_patch",
        "deterministic_page_closure_gate",
        "commit_page_bug_fix",
    ]:
        if required not in node_ids:
            errors.append(f"orchestrator DAG missing node: {required}")
    terminal_requirements = spec.get("terminal_requirements")
    if not isinstance(terminal_requirements, dict):
        errors.append("terminal_requirements must be an object")
        terminal_requirements = {}
    patched_confirmed_requires = terminal_requirements.get("patched_confirmed_requires")
    expected_patched_confirmed_requires = {*REQUIRED_PATCHED_CONFIRMED_ARTIFACTS, "commit_sha"}
    if not isinstance(patched_confirmed_requires, list):
        errors.append("terminal_requirements.patched_confirmed_requires must be a list")
    elif set(str(item) for item in patched_confirmed_requires) != expected_patched_confirmed_requires:
        errors.append("terminal_requirements.patched_confirmed_requires does not match patched-confirmed evidence contract")
    if terminal_requirements.get("one_git_commit_per_verified_bug_fix") is not True:
        errors.append("terminal_requirements must require one_git_commit_per_verified_bug_fix")
    for node in spec.get("nodes") or []:
        if node.get("state_owner") != spec.get("target_dag_state_owner"):
            errors.append(f"{node.get('node_id')} state_owner must match target_dag_state_owner")
        owner = node.get("runtime_owner")
        if owner in {"scillm_chat", "scillm_orchestrator", "scillm_opencode_serve"}:
            if "X-Caller-Skill" not in (node.get("required_headers") or []):
                errors.append(f"{node.get('node_id')} missing X-Caller-Skill header contract")
            endpoint = str(node.get("endpoint") or "")
            if not endpoint.startswith("POST /v1/"):
                errors.append(f"{node.get('node_id')} missing scillm endpoint contract")
        if owner == "scillm_orchestrator" and "/transport/" not in str(node.get("endpoint") or ""):
            errors.append(f"{node.get('node_id')} must use scillm transport when runtime_owner=scillm_orchestrator")
    if spec.get("patch_backend") == "scillm_orchestrator":
        patch_nodes = [node for node in spec.get("nodes") or [] if node.get("node_id") in {"patch_delegate_attempts", "repair_diagnosis_attempts"}]
        if not patch_nodes:
            errors.append("scillm_orchestrator backend missing patch transport node")
        for node in patch_nodes:
            if node.get("runtime_owner") != "scillm_orchestrator":
                errors.append(f"{node.get('node_id')} runtime_owner must be scillm_orchestrator")
            if node.get("surface") != "scillm_opencode_transport":
                errors.append(f"{node.get('node_id')} surface must be scillm_opencode_transport")
    return {
        "schema": "pdf_lab.second_pass.page_orchestrator_dag_spec_validation.v1",
        "ok": not errors,
        "errors": errors,
        "case_id": spec.get("case_id"),
        "page_number": spec.get("page_number"),
        "dag_spec_sha256": stable_json_sha256(spec),
        "candidate_count": len(normalized_candidate_ids),
        "candidate_ids": sorted(normalized_candidate_ids),
        "node_count": len(spec.get("nodes") or []),
        "target_dag_state_owner": spec.get("target_dag_state_owner"),
    }


def build_page_orchestrator_submission(
    *,
    case_dir: Path,
    page_case: dict[str, Any],
    dag_spec: dict[str, Any],
    dag_spec_artifact: str,
    code_root: Path,
    timeout_s: float,
) -> dict[str, Any]:
    dag_hash = stable_json_sha256(dag_spec)
    dag_node_id = f"pdf_lab_second_pass_page:{page_case['case_id']}"
    orchestrator_context = {
        "schema": "pdf_lab.second_pass.scillm_orchestrator_context.v1",
        "dag_spec_sha256": dag_hash,
        "dag_spec_artifact": dag_spec_artifact,
        "case_dir": str(case_dir.resolve()),
        "case_id": page_case["case_id"],
        "page_number": page_case["page_number"],
        "target_dag_state_owner": "scillm_orchestrator",
        "ownership_contract": dag_spec.get("orchestration_contract"),
        "required_terminal_gate": "deterministic_pdf_lab_acceptance_before_commit",
    }
    return {
        "schema": "pdf_lab.second_pass.scillm_orchestrator_page_submission.v1",
        "endpoint": "POST /v1/scillm/opencode/transport/runs",
        "dag_node_id": dag_node_id,
        "target_dag_state_owner": "scillm_orchestrator",
        "ownership_contract": dag_spec.get("orchestration_contract"),
        "case_id": page_case["case_id"],
        "page_number": page_case["page_number"],
        "case_dir": str(case_dir.resolve()),
        "code_root": str(code_root.resolve()),
        "dag_spec_artifact": dag_spec_artifact,
        "dag_spec_sha256": dag_hash,
        "timeout_s": timeout_s,
        "transport_create_body": {
            "dag_node_id": dag_node_id,
            "workspace": str(code_root.resolve()),
            "title": f"pdf-lab second-pass page DAG {page_case['case_id']}",
            "orchestrator_context": orchestrator_context,
        },
        "required_followup": [
            "transport parent registration receipt",
            "child worker receipts for scillm-owned delegated nodes",
            "event stream artifacts for each OpenCode transport child",
            "deterministic pdf-lab acceptance gate before terminal success",
        ],
        "scillm_metadata": {
            "graph_node": "scillm_orchestrator_page_dag",
            "case_id": page_case["case_id"],
            "page_number": page_case["page_number"],
            "dag_spec_sha256": dag_hash,
        },
    }


def validate_page_orchestrator_submission(
    submission: dict[str, Any],
    *,
    dag_spec: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    expected_case_id = submission.get("case_id")
    expected_page_number = submission.get("page_number")
    if submission.get("schema") != "pdf_lab.second_pass.scillm_orchestrator_page_submission.v1":
        errors.append("submission schema mismatch")
    if not expected_case_id:
        errors.append("submission missing case_id")
    if type(expected_page_number) is not int:
        errors.append("submission missing integer page_number")
    else:
        submission_identity = validate_page_case_identity(
            {
                "case_id": expected_case_id,
                "page_number": expected_page_number,
            },
        )
        if submission_identity["ok"] is not True:
            errors.extend(f"submission {error}" for error in submission_identity["errors"])
    if submission.get("target_dag_state_owner") != "scillm_orchestrator":
        errors.append("submission target_dag_state_owner must be scillm_orchestrator")
    if submission.get("dag_spec_sha256") != stable_json_sha256(dag_spec):
        errors.append("submission dag_spec_sha256 does not match current DAG spec")
    metadata = submission.get("scillm_metadata")
    if not isinstance(metadata, dict):
        errors.append("submission missing scillm_metadata")
    else:
        if metadata.get("case_id") != expected_case_id:
            errors.append("submission scillm_metadata case_id does not match submission")
        if metadata.get("page_number") != expected_page_number:
            errors.append("submission scillm_metadata page_number does not match submission")
        if metadata.get("dag_spec_sha256") != submission.get("dag_spec_sha256"):
            errors.append("submission scillm_metadata dag_spec_sha256 does not match submission")
    create_body = submission.get("transport_create_body")
    if not isinstance(create_body, dict):
        errors.append("submission missing transport_create_body")
    else:
        if not str(create_body.get("dag_node_id") or "").startswith("pdf_lab_second_pass_page:"):
            errors.append("transport_create_body missing page-level DAG node id")
        if not create_body.get("workspace"):
            errors.append("transport_create_body missing workspace")
        context = create_body.get("orchestrator_context")
        if not isinstance(context, dict):
            errors.append("transport_create_body missing orchestrator_context")
        else:
            if context.get("schema") != "pdf_lab.second_pass.scillm_orchestrator_context.v1":
                errors.append("orchestrator_context schema mismatch")
            if context.get("case_id") != expected_case_id:
                errors.append("orchestrator_context case_id does not match submission")
            if context.get("page_number") != expected_page_number:
                errors.append("orchestrator_context page_number does not match submission")
            if context.get("dag_spec_sha256") != stable_json_sha256(dag_spec):
                errors.append("orchestrator_context dag_spec_sha256 does not match current DAG spec")
            if context.get("target_dag_state_owner") != "scillm_orchestrator":
                errors.append("orchestrator_context target_dag_state_owner must be scillm_orchestrator")
            if context.get("required_terminal_gate") != "deterministic_pdf_lab_acceptance_before_commit":
                errors.append("orchestrator_context missing deterministic terminal gate")
    if not isinstance(submission.get("ownership_contract"), dict):
        errors.append("submission missing ownership_contract")
    return {
        "schema": "pdf_lab.second_pass.scillm_orchestrator_page_submission_validation.v1",
        "ok": not errors,
        "errors": errors,
        "dag_spec_sha256": submission.get("dag_spec_sha256"),
        "target_dag_state_owner": submission.get("target_dag_state_owner"),
        "case_id": expected_case_id,
        "page_number": expected_page_number,
    }


def build_page_orchestrator_run_request(
    *,
    case_dir: Path,
    page_case: dict[str, Any],
    submission: dict[str, Any],
    dag_spec_artifact: str,
    code_root: Path,
    timeout_s: float,
) -> dict[str, Any]:
    dag_node_id = submission["dag_node_id"]
    return {
        "schema": "pdf_lab.second_pass.page_orchestrator_run_request.v1",
        "endpoint": "POST /v1/scillm/opencode/transport/runs",
        "dag_node_id": dag_node_id,
        "timeout_s": timeout_s,
        "cwd": str(code_root.resolve()),
        "case_dir": str(case_dir.resolve()),
        "dag_spec_artifact": dag_spec_artifact,
        "dag_spec_sha256": submission["dag_spec_sha256"],
        "orchestrator_submission_schema": submission["schema"],
        "target_dag_state_owner": submission["target_dag_state_owner"],
        "create_run_body": submission["transport_create_body"],
        "scillm_metadata": {
            "graph_node": "scillm_orchestrator_page_dag",
            "case_id": page_case["case_id"],
            "page_number": page_case["page_number"],
            "dag_spec_sha256": submission["dag_spec_sha256"],
        },
    }


def call_page_orchestrator_run(
    request: dict[str, Any],
    *,
    base_url: str,
    auth_token: str,
    caller_skill: str,
    timeout_s: float,
) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "X-Caller-Skill": caller_skill,
        "Content-Type": "application/json",
    }
    response = httpx.post(
        f"{base_url.rstrip('/')}/v1/scillm/opencode/transport/runs",
        headers=headers,
        json=request["create_run_body"],
        timeout=timeout_s,
    )
    response.raise_for_status()
    raw = response.json()
    return {
        "schema": "pdf_lab.second_pass.page_orchestrator_run_receipt.v1",
        "endpoint": "POST /v1/scillm/opencode/transport/runs",
        "http_status": response.status_code,
        "request_metadata": request["scillm_metadata"],
        "transport_run_id": raw.get("transport_run_id"),
        "create_response": raw,
        "observation": raw.get("observation"),
    }


def validate_page_orchestrator_run_receipt(
    receipt: dict[str, Any] | None,
    *,
    mode: str,
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    expected_metadata = request.get("scillm_metadata") if isinstance(request, dict) else None
    expected_case_id = expected_metadata.get("case_id") if isinstance(expected_metadata, dict) else None
    expected_page_number = expected_metadata.get("page_number") if isinstance(expected_metadata, dict) else None
    expected_dag_spec_sha256 = expected_metadata.get("dag_spec_sha256") if isinstance(expected_metadata, dict) else None
    expected_cwd = request.get("cwd") if isinstance(request, dict) else None
    if isinstance(request, dict):
        if request.get("schema") != "pdf_lab.second_pass.page_orchestrator_run_request.v1":
            errors.append("page orchestrator run request schema mismatch")
        if request.get("endpoint") != "POST /v1/scillm/opencode/transport/runs":
            errors.append("page orchestrator run request endpoint mismatch")
        request_timeout_s = request.get("timeout_s")
        if (
            isinstance(request_timeout_s, bool)
            or not isinstance(request_timeout_s, int | float)
            or not math.isfinite(float(request_timeout_s))
            or float(request_timeout_s) <= 0
        ):
            errors.append("page orchestrator run request timeout_s must be a positive finite number")
        if not isinstance(expected_metadata, dict):
            errors.append("page orchestrator run request missing scillm_metadata")
        else:
            request_identity = validate_page_case_identity(
                {
                    "case_id": expected_case_id,
                    "page_number": expected_page_number,
                },
            )
            if request_identity["ok"] is not True:
                errors.extend(f"page orchestrator run request {error}" for error in request_identity["errors"])
            if expected_dag_spec_sha256 != request.get("dag_spec_sha256"):
                errors.append("page orchestrator run request scillm_metadata.dag_spec_sha256 must match request.dag_spec_sha256")
    if mode == "dry_run":
        return {
            "schema": "pdf_lab.second_pass.page_orchestrator_run_validation.v1",
            "ok": not errors,
            "errors": errors,
            "mode": mode,
            "transport_run_id": None,
            "registered": False,
            "case_id": expected_case_id,
            "page_number": expected_page_number,
            "dag_spec_sha256": expected_dag_spec_sha256,
        }
    if not isinstance(receipt, dict):
        errors.append("page orchestrator run receipt missing")
    elif receipt.get("schema") != "pdf_lab.second_pass.page_orchestrator_run_receipt.v1":
        errors.append("page orchestrator run receipt schema mismatch")
    else:
        if receipt.get("endpoint") != "POST /v1/scillm/opencode/transport/runs":
            errors.append("page orchestrator run receipt endpoint mismatch")
        if receipt.get("http_status") != 200:
            errors.append("page orchestrator run receipt http_status must be 200")
        transport_run_id = receipt.get("transport_run_id")
        if not transport_run_id:
            errors.append("page orchestrator run receipt missing transport_run_id")
        if isinstance(request, dict):
            create_run_body = request.get("create_run_body")
            if not isinstance(create_run_body, dict):
                errors.append("page orchestrator run request missing create_run_body")
            elif create_run_body.get("dag_node_id") != request.get("dag_node_id"):
                errors.append("page orchestrator run request create_run_body.dag_node_id must match request.dag_node_id")
        create_response = receipt.get("create_response")
        if isinstance(create_response, dict):
            create_transport_run_id = create_response.get("transport_run_id")
            if create_transport_run_id and create_transport_run_id != transport_run_id:
                errors.append("page orchestrator create_response transport_run_id does not match receipt")
            create_workspace = create_response.get("workspace")
            if create_workspace and isinstance(expected_cwd, str) and create_workspace != expected_cwd:
                errors.append("page orchestrator create_response workspace does not match request cwd")
            create_observation = create_response.get("observation")
            if isinstance(create_observation, dict):
                create_observation_transport_run_id = create_observation.get("transport_run_id")
                if create_observation_transport_run_id and create_observation_transport_run_id != transport_run_id:
                    errors.append("page orchestrator create_response observation transport_run_id does not match receipt")
        elif create_response is not None:
            errors.append("page orchestrator run receipt create_response must be an object")
        request_metadata = receipt.get("request_metadata")
        if not isinstance(request_metadata, dict):
            errors.append("page orchestrator run receipt missing request_metadata")
        elif isinstance(expected_metadata, dict):
            if request_metadata.get("case_id") != expected_case_id:
                errors.append("page orchestrator run receipt request_metadata case_id does not match request")
            if request_metadata.get("page_number") != expected_page_number:
                errors.append("page orchestrator run receipt request_metadata page_number does not match request")
            if request_metadata.get("dag_spec_sha256") != expected_dag_spec_sha256:
                errors.append("page orchestrator run receipt request_metadata dag_spec_sha256 does not match request")
        observation = receipt.get("observation")
        if not isinstance(observation, dict):
            errors.append("page orchestrator run receipt missing observation")
        else:
            observation_transport_run_id = observation.get("transport_run_id")
            if observation_transport_run_id and observation_transport_run_id != transport_run_id:
                errors.append("page orchestrator observation transport_run_id does not match receipt")
    return {
        "schema": "pdf_lab.second_pass.page_orchestrator_run_validation.v1",
        "ok": not errors,
        "errors": errors,
        "mode": mode,
        "transport_run_id": receipt.get("transport_run_id") if isinstance(receipt, dict) else None,
        "registered": not errors,
        "case_id": expected_case_id,
        "page_number": expected_page_number,
        "dag_spec_sha256": expected_dag_spec_sha256,
    }


def parse_scillm_review_content(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"review content is not JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("review content must be a JSON object")
    return value


def validate_review_response(
    review: dict[str, Any],
    expected_candidate_ids: list[str],
    *,
    receipt: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
    page_case: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    expected_metadata = request.get("scillm_metadata") if isinstance(request, dict) else None
    validation_page_case = page_case if isinstance(page_case, dict) else request.get("page_case") if isinstance(request, dict) else None
    if receipt is not None:
        if not isinstance(receipt, dict):
            errors.append("review receipt must be an object")
        else:
            if receipt.get("schema") != "pdf_lab.second_pass.scillm_review_receipt.v1":
                errors.append("review receipt schema mismatch")
            if receipt.get("endpoint") != "POST /v1/chat/completions":
                errors.append("review receipt endpoint mismatch")
            if receipt.get("http_status") != 200:
                errors.append("review receipt http_status must be 200")
            receipt_metadata = receipt.get("scillm_metadata")
            if not isinstance(receipt_metadata, dict):
                errors.append("review receipt missing scillm_metadata")
            elif isinstance(expected_metadata, dict):
                for key in ["batch_id", "item_id"]:
                    if receipt_metadata.get(key) != expected_metadata.get(key):
                        errors.append(f"review receipt scillm_metadata {key} does not match request")
            if "review_response" in receipt and receipt.get("review_response") != review:
                errors.append("review receipt review_response does not match validated review response")
    if review.get("schema") != "pdf_lab.second_pass.review_response.v1":
        errors.append("schema must be pdf_lab.second_pass.review_response.v1")
    if review.get("page_status") not in REVIEW_STATUSES:
        errors.append("page_status must be one of clean, defect, unsure, substrate_blocked")
    if not isinstance(review.get("page_rationale"), str) or not review.get("page_rationale", "").strip():
        errors.append("page_rationale must be non-empty")
    for forbidden in ["terminal_status", "patched_confirmed", "agent_resolved", "commit_sha"]:
        if forbidden in review:
            errors.append(f"review must not include terminal/closure field: {forbidden}")
    findings = review.get("candidate_findings")
    if not isinstance(findings, list):
        errors.append("candidate_findings must be a list")
        findings = []
    if not isinstance(expected_candidate_ids, list):
        errors.append("expected_candidate_ids must be a list")
        normalized_expected_candidate_ids: list[str] = []
    else:
        invalid_expected_candidate_ids = [
            candidate_id
            for candidate_id in expected_candidate_ids
            if not isinstance(candidate_id, str) or not candidate_id
        ]
        if invalid_expected_candidate_ids:
            errors.append(f"expected_candidate_ids must be non-empty strings: {invalid_expected_candidate_ids}")
        duplicate_expected_candidate_ids = sorted(
            candidate_id
            for candidate_id, count in Counter(
                candidate_id
                for candidate_id in expected_candidate_ids
                if isinstance(candidate_id, str) and candidate_id
            ).items()
            if count > 1
        )
        if duplicate_expected_candidate_ids:
            errors.append(f"expected_candidate_ids contain duplicates: {duplicate_expected_candidate_ids}")
        normalized_expected_candidate_ids = [
            candidate_id
            for candidate_id in expected_candidate_ids
            if isinstance(candidate_id, str) and candidate_id
        ]
    expected = set(normalized_expected_candidate_ids)
    seen: set[str] = set()
    finding_statuses: list[str] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            errors.append(f"candidate_findings[{index}] must be an object")
            continue
        candidate_id = finding.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            errors.append(f"candidate_findings[{index}].candidate_id must be non-empty")
            continue
        seen.add(candidate_id)
        status = finding.get("status")
        if status not in REVIEW_STATUSES:
            errors.append(f"candidate_findings[{index}].status must be a closed vocabulary value")
        else:
            finding_statuses.append(str(status))
        if not isinstance(finding.get("evidence"), str) or not finding.get("evidence", "").strip():
            errors.append(f"candidate_findings[{index}].evidence must be non-empty")
        if not isinstance(finding.get("rationale"), str) or not finding.get("rationale", "").strip():
            errors.append(f"candidate_findings[{index}].rationale must be non-empty")
        suggested_fix_surface = finding.get("suggested_fix_surface")
        if status == "clean" and suggested_fix_surface not in (None, "", "none"):
            errors.append(f"candidate_findings[{index}].suggested_fix_surface must be none for clean findings")
        if status == "defect" and (
            not isinstance(suggested_fix_surface, str)
            or not suggested_fix_surface.strip()
            or suggested_fix_surface.strip().lower() == "none"
        ):
            errors.append(f"candidate_findings[{index}].suggested_fix_surface must identify a fix surface for defect findings")
    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    if missing:
        errors.append(f"missing candidate findings: {missing}")
    if extra:
        errors.append(f"unexpected candidate findings: {extra}")
    duplicate_candidate_ids = sorted(candidate_id for candidate_id, count in Counter(seen_candidate for seen_candidate in [
        finding.get("candidate_id")
        for finding in findings
        if isinstance(finding, dict) and isinstance(finding.get("candidate_id"), str) and finding.get("candidate_id")
    ]).items() if count > 1)
    if duplicate_candidate_ids:
        errors.append(f"duplicate candidate findings: {duplicate_candidate_ids}")
    page_status = review.get("page_status")
    if page_status == "clean" and any(status != "clean" for status in finding_statuses):
        errors.append("page_status clean requires every candidate finding status to be clean")
    if page_status == "defect" and "defect" not in finding_statuses:
        errors.append("page_status defect requires at least one defect candidate finding")
    if page_status == "substrate_blocked":
        if "substrate_blocked" not in finding_statuses:
            errors.append("page_status substrate_blocked requires at least one substrate_blocked candidate finding")
        if any(status not in {"clean", "substrate_blocked"} for status in finding_statuses):
            errors.append("page_status substrate_blocked allows only clean or substrate_blocked candidate findings")
    return {
        "schema": "pdf_lab.second_pass.review_validation.v1",
        "ok": not errors,
        "errors": errors,
        "page_case": {
            "case_id": validation_page_case.get("case_id") if isinstance(validation_page_case, dict) else None,
            "page_number": validation_page_case.get("page_number") if isinstance(validation_page_case, dict) else None,
        },
        "candidate_count": len(normalized_expected_candidate_ids),
        "expected_candidate_ids": sorted(normalized_expected_candidate_ids),
        "seen_candidate_ids": sorted(seen),
    }


def call_scillm_review(
    review_request: dict[str, Any],
    *,
    base_url: str,
    auth_token: str,
    caller_skill: str,
    timeout_s: float,
) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    response = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {auth_token}",
            "X-Caller-Skill": caller_skill,
            "Content-Type": "application/json",
        },
        json=review_request["scillm_payload"],
        timeout=timeout_s,
    )
    response.raise_for_status()
    raw = response.json()
    content = raw["choices"][0]["message"]["content"]
    return {
        "schema": "pdf_lab.second_pass.scillm_review_receipt.v1",
        "endpoint": "POST /v1/chat/completions",
        "http_status": response.status_code,
        "scillm_metadata": review_request["scillm_metadata"],
        "raw_response": raw,
        "review_response": parse_scillm_review_content(content),
    }


def preflight_scillm_surface(
    *,
    base_url: str,
    auth_token: str,
    caller_skill: str,
    surface: str,
    timeout_s: float,
    verify_caller_contract: bool = True,
) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "X-Caller-Skill": caller_skill,
        "Content-Type": "application/json",
    }
    root = base_url.rstrip("/")
    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    def get_json(path: str) -> dict[str, Any]:
        response = httpx.get(f"{root}{path}", headers=headers, timeout=timeout_s)
        try:
            payload: Any = response.json()
        except Exception:  # noqa: BLE001 - response text is still useful preflight evidence.
            payload = {"text": getattr(response, "text", "")}
        checks.append({"path": path, "http_status": response.status_code, "payload": payload})
        response.raise_for_status()
        return payload

    def post_json(path: str, payload: dict[str, Any], *, include_caller_skill: bool = True) -> dict[str, Any]:
        post_headers = dict(headers)
        if not include_caller_skill:
            post_headers.pop("X-Caller-Skill", None)
        response = httpx.post(f"{root}{path}", headers=post_headers, json=payload, timeout=timeout_s)
        try:
            response_payload: Any = response.json()
        except Exception:  # noqa: BLE001 - response text is still useful preflight evidence.
            response_payload = {"text": getattr(response, "text", "")}
        check = {
            "path": path,
            "method": "POST",
            "http_status": response.status_code,
            "include_caller_skill": include_caller_skill,
            "payload": response_payload,
        }
        checks.append(check)
        return check

    try:
        live = get_json("/health/liveliness")
        if live.get("status") != "ok":
            errors.append("scillm liveliness status is not ok")
        if verify_caller_contract:
            caller_contract = post_json(
                "/v1/chat/completions",
                {
                    "model": "local-text",
                    "messages": [{"role": "user", "content": "pdf-lab caller contract negative probe"}],
                    "max_tokens": 1,
                },
                include_caller_skill=False,
            )
            caller_contract_text = json.dumps(caller_contract.get("payload"), sort_keys=True)
            if caller_contract["http_status"] != 400 or "caller_skill_required" not in caller_contract_text:
                errors.append("missing-caller chat contract did not return caller_skill_required")
        if surface == "chat":
            health = get_json("/v1/scillm/health")
            if health.get("status") != "ok":
                errors.append("scillm health status is not ok")
        elif surface == "opencode_serve":
            health = get_json("/v1/scillm/health")
            if health.get("status") != "ok":
                errors.append("scillm health status is not ok")
            opencode_health = get_json("/v1/scillm/opencode/health")
            opencode_status = opencode_health.get("status")
            if opencode_status not in {"ok", "healthy", "enabled"} and not opencode_health.get("opencode_serve"):
                errors.append("scillm opencode health status is not ok")
        elif surface == "opencode_transport":
            capabilities = get_json("/v1/scillm/opencode/transport/capabilities")
            if not capabilities.get("transport_api"):
                errors.append("transport_api capability is not enabled")
            if capabilities.get("event_stream") != "sse_with_reasoning":
                errors.append("transport event_stream is not sse_with_reasoning")
            if not capabilities.get("child_sessions"):
                errors.append("transport child_sessions capability is not enabled")
        else:
            errors.append(f"unknown scillm preflight surface: {surface}")
    except Exception as exc:  # noqa: BLE001 - substrate failures must be ledgered.
        errors.append(f"scillm preflight failed: {type(exc).__name__}: {exc}")

    return {
        "schema": "pdf_lab.second_pass.scillm_preflight.v1",
        "surface": surface,
        "base_url": base_url,
        "caller_skill": caller_skill,
        "checks": checks,
        "ok": not errors,
        "errors": errors,
    }


def route_review_result(validation: dict[str, Any], review: dict[str, Any] | None, review_mode: str) -> tuple[str, str]:
    if review_mode == "dry_run":
        return "still_open", "dry_run_review_not_executed"
    if not validation.get("ok"):
        return "human_needed", "review_validation_failed"
    assert review is not None
    page_status = review.get("page_status")
    if review_mode == "fixture" and page_status == "clean":
        return "human_needed", "fixture_review_cannot_prove_clean"
    if page_status == "clean":
        return "reviewed_clean", "scillm_review_validated_clean"
    if page_status == "defect":
        return "still_open", "defect_patch_not_implemented"
    if page_status == "substrate_blocked":
        return "blocked_substrate", "scillm_review_validated_substrate_blocked"
    return "human_needed", "scillm_review_validated_unsure"


def validate_opencode_agent_profile(agent: str) -> None:
    if not agent or not agent.strip():
        raise ValueError("OpenCode agent profile must be non-empty")
    if agent.startswith("opencode-go/") or agent.startswith("chutes-") or "/" in agent:
        raise ValueError("OpenCode agent must be an agent profile such as 'build', not a chat model id")


def load_review_fixture(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError("review fixture must be a JSON object")
    if payload.get("schema") == "pdf_lab.second_pass.review_fixture.v1":
        review = payload.get("review_response")
        if not isinstance(review, dict):
            raise ValueError("review fixture schema requires review_response object")
        return review
    return payload


def _defect_findings(review: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not review:
        return []
    return [
        finding
        for finding in review.get("candidate_findings") or []
        if isinstance(finding, dict) and finding.get("status") == "defect"
    ]


def _truncate_patch_prompt_text(value: Any, max_chars: int = 120) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def compact_patch_prompt_defect_findings(defects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for finding in defects:
        item = {
            "candidate_id": finding.get("candidate_id"),
            "status": finding.get("status"),
            "suggested_fix_surface": _truncate_patch_prompt_text(finding.get("suggested_fix_surface"), 180),
            "rationale": _truncate_patch_prompt_text(finding.get("rationale"), 120),
        }
        compact.append({key: value for key, value in item.items() if value not in (None, "")})
    return compact


def clustered_patch_prompt_defect_scope(defects: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a small implementation scope from visual findings for live patch delegates."""
    required_test_name = "test_page_top_title_and_qid_table_row_are_not_footer_or_reference"
    joined = " ".join(
        str(finding.get(key) or "")
        for finding in defects
        for key in ("candidate_id", "suggested_fix_surface", "rationale", "status")
    ).lower()
    candidate_ids = [
        str(finding.get("candidate_id"))
        for finding in defects
        if isinstance(finding.get("candidate_id"), str)
    ]
    primary_scope = "classification_defect"
    likely_files = ["src/extractors/block_classifier.rs", "tests/test_integration.py"]
    actionable_findings: list[dict[str, Any]] = []
    if "table" in joined and any(
        token in joined
        for token in (
            "bbox",
            "bounding box",
            "boundary",
            "crop",
            "clipped",
            "off-page",
            "off page",
            "outside the page",
            "extends beyond",
        )
    ):
        primary_scope = "table_geometry_cropping_defect"
        required_test_name = "test_off_page_table_bbox_does_not_emit_clipped_text_as_visible"
        likely_files = [
            "scripts/pdf_lab/snapshot_current_extraction.py",
            "tests/test_nist_table_duplicate_suppression.py",
        ]
        actionable_findings.append({
            "defect_class": primary_scope,
            "candidate_ids": [
                candidate_id
                for candidate_id in candidate_ids
                if ":table" in candidate_id
            ][:3],
            "required_behavior": (
                "Tables whose raw bbox extends beyond the rendered page/crop boundary must not "
                "treat off-page or clipped cell text as normally visible page text."
            ),
            "likely_code_surface": "scripts/pdf_lab/snapshot_current_extraction.py",
            "implementation_hint": (
                "Inspect table bbox normalization and table text construction; when raw table "
                "geometry extends beyond page bounds, clip or explicitly mark out-of-crop text "
                "instead of routing the finding through block classification."
            ),
            "test_hint": (
                "Add a focused Python regression named "
                f"`{required_test_name}` that exercises the page-level table extraction path; "
                "do not edit generated artifacts."
            ),
        })
    if "reference" in joined and "table" in joined:
        primary_scope = "table_rows_misclassified_as_references"
        actionable_findings.append({
            "defect_class": primary_scope,
            "candidate_ids": [
                candidate_id
                for candidate_id in candidate_ids
                if ":reference" in candidate_id
            ][:5],
            "required_behavior": (
                "Text rows visually contained by a table must not be emitted as "
                "Reference blocks or duplicate non-table blocks."
            ),
            "likely_code_surface": "src/extractors/block_classifier.rs",
            "implementation_hint": (
                "Inspect `fn is_reference_entry`; if a line starts with bracketed IDs but "
                "contains multiple bracketed cell markers, reject it as a reference entry."
            ),
            "test_hint": (
                "Add a small Rust unit test named "
                f"`{required_test_name}` near `is_reference_entry` or the classifier tests; "
                "do not inspect PDF fixtures or Python bindings for this helper-level defect."
            ),
        })
    if "footer" in joined or "top margin" in joined or "page title" in joined or "title/header" in joined:
        actionable_findings.append({
            "defect_class": "top_page_title_misclassified_as_footer",
            "candidate_ids": [
                candidate_id
                for candidate_id in candidate_ids
                if ":unknown_layout" in candidate_id
            ][:3],
            "required_behavior": "Top-of-page title/header text must not be classified as Footer.",
            "likely_code_surface": "src/extractors/block_classifier.rs",
            "implementation_hint": (
                "Inspect the line-level `y_ratio` used before footer detection; if PDF "
                "coordinates are bottom-origin there, compute visual top/bottom ratio from "
                "`page_height - (bbox.y + bbox.height)` before applying footer thresholds."
            ),
            "test_hint": (
                "Add a small Rust unit test named "
                f"`{required_test_name}` for a top-page line not becoming Footer; "
                "do not spend the run creating a synthetic PDF."
            ),
        })
    if not actionable_findings:
        actionable_findings = compact_patch_prompt_defect_findings(defects[:3])
    return {
        "primary_scope": primary_scope,
        "candidate_count": len(candidate_ids),
        "representative_candidate_ids": candidate_ids[:8],
        "actionable_findings": actionable_findings[:3],
        "required_test_name": required_test_name,
        "likely_files": likely_files,
        "max_initial_file_reads": 2,
        "block_if_not_localized": (
            "If the fix is not localizable after reading the likely files and evidence JSON, "
            "return PATCH_DELEGATE_BLOCKED reason=unsupported_defect."
        ),
    }


def sanitize_patch_prompt_payload(value: Any, *, key: str | None = None) -> Any:
    """Remove banned vague words from dynamic prose before prompt serialization."""
    if key in PROMPT_PAYLOAD_EXACT_KEYS:
        return value
    if isinstance(value, str):
        sanitized = value
        for needle, replacement in PROMPT_PAYLOAD_REPLACEMENTS.items():
            if " " in needle:
                sanitized = re.sub(re.escape(needle), replacement, sanitized, flags=re.IGNORECASE)
            else:
                sanitized = re.sub(rf"\b{re.escape(needle)}\b", replacement, sanitized, flags=re.IGNORECASE)
        return sanitized
    if isinstance(value, list):
        return [sanitize_patch_prompt_payload(item, key=key) for item in value]
    if isinstance(value, dict):
        return {
            item_key: sanitize_patch_prompt_payload(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    return value


def build_patch_worker_prompt(
    *,
    executor_label: str,
    case_dir: Path,
    evidence_case_dir: Path | None = None,
    workspace_root: Path,
    page_case: dict[str, Any],
    candidates: list[dict[str, Any]],
    review_response: dict[str, Any],
    prompt_profile: str = "full",
    repair_diagnosis: dict[str, Any] | None = None,
) -> str:
    if prompt_profile not in PATCH_PROMPT_PROFILES:
        raise ValueError(f"unknown patch prompt profile: {prompt_profile}")
    case_dir_abs = case_dir.resolve()
    evidence_case_dir_abs = (evidence_case_dir or case_dir).resolve()
    workspace_root_abs = workspace_root.resolve()
    defects = sanitize_patch_prompt_payload(_defect_findings(review_response))
    diagnosis_summary = None
    if repair_diagnosis is not None:
        if repair_diagnosis.get("schema") == "pdf_lab.second_pass.scillm_repair_plan_receipt.v1":
            diagnosis_summary = sanitize_patch_prompt_payload({
                "receipt_schema": repair_diagnosis.get("schema"),
                "repair_plan": repair_diagnosis.get("repair_plan"),
                "status": "completed",
            })
        else:
            raw = repair_diagnosis.get("raw_response") or repair_diagnosis.get("message_response") or {}
            diagnosis_summary = sanitize_patch_prompt_payload({
                "receipt_schema": repair_diagnosis.get("schema"),
                "assistant_text": raw.get("assistant_text") or raw.get("output") or raw.get("text"),
                "run_id": raw.get("run_id") or repair_diagnosis.get("transport_run_id"),
                "status": raw.get("status") or raw.get("delivery_state"),
            })
    evidence_paths = {
        "case_dir": str(evidence_case_dir_abs),
        "artifact_case_dir": str(case_dir_abs),
        "workspace_root": str(workspace_root_abs),
        "case_files": {
            "page_before_json": "page_before.json",
            "page_before_image": "page_before.png",
            "page_candidates_image": "page_candidates.png",
            "candidate_presets": "candidate_presets.json",
            "review_response": "review_response.json",
            "review_validation": "review_validation.json",
        },
    }
    contract = (
        "## Role\n"
        f"Bounded {executor_label} patch executor for one PDF Oxide pdf-lab page DAG node.\n\n"
        "## Task\n"
        "Perform the patch now. Create or edit source files in the workspace root, add one focused regression test, and leave a non-empty git diff.\n\n"
        "## Context\n"
        f"- Workspace root: {workspace_root_abs}\n"
        f"- Evidence case directory: {evidence_case_dir_abs}\n"
        "- The deterministic harness owns validation, re-extraction, second review, terminal status, git commit, and rollback proof.\n"
        "- A valid patch session must leave a non-empty diff in the workspace root.\n\n"
        "## Constraints\n"
        "- Start by checking `pwd`; if outside the workspace root, run `cd` to the workspace root.\n"
        "- Read `review_response`, `review_validation`, `page_before.json`, and `candidate_presets` from the evidence paths.\n"
        "- If `defect_findings[].suggested_fix_surface` names exact file paths, edit those paths directly after checking the JSON evidence; do not spend the run searching the repository.\n"
        "- Edit only `python/pdf_oxide/`, `src/`, `scripts/pdf_lab/`, or `tests/`.\n"
        "- Do not edit generated artifacts as proof.\n"
        "- Do not commit. Do not mark the page fixed. Do not claim terminal closure.\n"
        "- If the workspace or evidence is inaccessible, do not edit files.\n"
        "- Do not return only a plan, todo list, promise, or progress update. A response without a git diff is invalid unless it ends with `PATCH_DELEGATE_BLOCKED` and a concrete blocker.\n\n"
    )
    output_format = (
        "\n## Output Format\n"
        "End with exactly one of these status lines:\n"
        "- `PATCH_APPLIED changed_files=<comma-separated paths> tests=<test file paths> commands=<commands run or commands to run>`\n"
        "- `PATCH_DELEGATE_BLOCKED reason=<workspace_missing|evidence_missing|unsupported_defect|no_code_defect|other>: <one sentence>`\n"
        "Do not output commentary after the status line.\n"
    )
    if prompt_profile == "compact":
        compact_candidates = [
            {
                "candidate_id": candidate.get("candidate_id"),
                "preset_type": candidate.get("preset_type"),
                "json_pointer": candidate.get("json_pointer"),
                "text_excerpt": candidate.get("text_excerpt"),
                "bbox": candidate.get("bbox"),
            }
            for candidate in candidates
        ]
        compact_payload = {
            "evidence_paths": evidence_paths,
            "page_case": {
                "case_id": page_case.get("case_id"),
                "page_number": page_case.get("page_number"),
                "strata": page_case.get("strata"),
                "candidate_ids": page_case.get("candidate_ids"),
            },
            "candidate_summaries": compact_candidates,
            "defect_findings": defects,
            "repair_diagnosis": diagnosis_summary,
        }
        return (
            contract
            + "## Compact Evidence Payload\n"
            + "Use these exact JSON fields: `evidence_paths`, `page_case`, `candidate_summaries`, `defect_findings`, `repair_diagnosis`.\n"
            + "- Read `review_response`, `page_before.json`, and `candidate_presets` from the evidence paths.\n"
            + "- If the defect finding names exact files, create or edit exactly those files.\n"
            + "- Otherwise, make the smallest extractor change that addresses the defect class.\n"
            + "- Add one focused regression test under `tests/`.\n"
            + "- Stop after creating the patch and test file; do not run broad repository searches.\n\n"
            + f"{json.dumps(compact_payload, indent=2, sort_keys=True)}"
            + output_format
        )
    if prompt_profile == "plan_only":
        fallback_scope = clustered_patch_prompt_defect_scope(defects) if diagnosis_summary is None else None
        plan_payload = {
            "evidence_paths": evidence_paths,
            "page_case": {
                "case_id": page_case.get("case_id"),
                "page_number": page_case.get("page_number"),
                "candidate_ids": page_case.get("candidate_ids"),
            },
            "repair_diagnosis": diagnosis_summary,
            "fallback_scope": fallback_scope,
        }
        return (
            contract
            + "## Plan Payload\n"
            + "Use these exact JSON fields: `evidence_paths`, `page_case`, `repair_diagnosis`, `fallback_scope`.\n"
            + "- If `repair_diagnosis` is null, use `fallback_scope.actionable_findings` as the whole patch scope.\n"
            + "- Apply `implementation_hint` entries directly after reading the named function or local block.\n"
            + "- Inspect only `fallback_scope.likely_files`; do not run broad repository searches.\n"
            + "- If the defect is not localizable within `fallback_scope.max_initial_file_reads`, return `PATCH_DELEGATE_BLOCKED reason=unsupported_defect: <one sentence>`.\n"
            + "- Add or update exactly one focused regression test; a Rust unit test is preferred when the scoped code surface is Rust.\n"
            + "- If `fallback_scope.required_test_name` is present, use that exact test function name.\n"
            + "- Follow `test_hint`; do not inspect PDF fixtures or Python bindings when a helper-level Rust unit test is named.\n"
            + "- Do not repeat visual review; the harness already owns visual review and closure.\n\n"
            + f"{json.dumps(plan_payload, indent=2, sort_keys=True)}"
            + output_format
        )
    return (
        contract
        + f"Evidence paths:\n{json.dumps(evidence_paths, indent=2, sort_keys=True)}\n\n"
        + f"Page case:\n{json.dumps(page_case, indent=2, sort_keys=True)}\n\n"
        + f"Selected candidates:\n{json.dumps(candidates, indent=2, sort_keys=True)}\n\n"
        + f"Validated review response:\n{json.dumps(review_response, indent=2, sort_keys=True)}\n\n"
        + f"Defect findings:\n{json.dumps(defects, indent=2, sort_keys=True)}\n\n"
        + f"Repair diagnosis:\n{json.dumps(diagnosis_summary, indent=2, sort_keys=True)}"
        + output_format
    )


def build_repair_diagnosis_prompt(
    *,
    executor_label: str,
    case_dir: Path,
    workspace_root: Path,
    page_case: dict[str, Any],
    candidates: list[dict[str, Any]],
    review_response: dict[str, Any],
    prompt_profile: str = "compact",
) -> str:
    case_dir_abs = case_dir.resolve()
    workspace_root_abs = workspace_root.resolve()
    if prompt_profile not in PATCH_PROMPT_PROFILES:
        raise ValueError(f"unknown patch prompt profile: {prompt_profile}")
    compact_candidates = [
        {
            "candidate_id": candidate.get("candidate_id"),
            "preset_type": candidate.get("preset_type"),
            "json_pointer": candidate.get("json_pointer"),
            "text_excerpt": candidate.get("text_excerpt"),
            "bbox": candidate.get("bbox"),
        }
        for candidate in candidates
    ]
    payload = {
        "workspace_root": str(workspace_root_abs),
        "case_dir": str(case_dir_abs),
        "evidence_paths": {
            "page_before_json": str(case_dir_abs / "page_before.json"),
            "page_before_image": str(case_dir_abs / "page_before.png"),
            "page_candidates_image": str(case_dir_abs / "page_candidates.png"),
            "candidate_presets": str(case_dir_abs / "candidate_presets.json"),
            "review_response": str(case_dir_abs / "review_response.json"),
            "review_validation": str(case_dir_abs / "review_validation.json"),
        },
        "page_case": {
            "case_id": page_case.get("case_id"),
            "page_number": page_case.get("page_number"),
            "strata": page_case.get("strata"),
            "candidate_ids": page_case.get("candidate_ids"),
        },
        "candidate_summaries": compact_candidates,
        "defect_findings": _defect_findings(review_response),
    }
    return (
        f"You are a bounded {executor_label} diagnosis executor for one PDF Oxide pdf-lab page DAG node. "
        "Inspect the evidence and repository only enough to produce a focused repair plan. Do not edit files. "
        "Do not commit. Do not claim the page fixed. If the workspace or evidence is inaccessible, return "
        "`PATCH_DELEGATE_BLOCKED: <reason>`.\n\n"
        "Return concise text with these headings:\n"
        "1. Evidence read\n"
        "2. Likely extractor fault\n"
        "3. Minimal files to inspect or patch\n"
        "4. Regression test to add\n"
        "5. Patch constraints\n\n"
        f"Diagnosis payload:\n{json.dumps(payload, indent=2, sort_keys=True)}"
    )


def build_scillm_repair_plan_request(
    *,
    case_dir: Path,
    page_case: dict[str, Any],
    candidates: list[dict[str, Any]],
    review_response: dict[str, Any],
    model: str,
    batch_id: str,
) -> dict[str, Any]:
    item_id = f"{page_case['case_id']}:repair_plan"
    compact_candidates = [
        {
            "candidate_id": candidate.get("candidate_id"),
            "preset_type": candidate.get("preset_type"),
            "json_pointer": candidate.get("json_pointer"),
            "text_excerpt": candidate.get("text_excerpt"),
            "bbox": candidate.get("bbox"),
        }
        for candidate in candidates
    ]
    payload = {
        "case_dir": str(case_dir.resolve()),
        "page_case": {
            "case_id": page_case.get("case_id"),
            "page_number": page_case.get("page_number"),
            "strata": page_case.get("strata"),
            "candidate_ids": page_case.get("candidate_ids"),
        },
        "candidate_summaries": compact_candidates,
        "defect_findings": _defect_findings(review_response),
        "review_response": review_response,
    }
    prompt = (
        "You are producing a bounded repair plan for one PDF Oxide pdf-lab page case. "
        "Use only the provided evidence summary. Do not claim the page is fixed. Do not write code. "
        "Return JSON only with this schema:\n"
        "{\n"
        '  "schema": "pdf_lab.second_pass.repair_plan.v1",\n'
        '  "summary": "...",\n'
        '  "suspected_fault": "...",\n'
        '  "patch_targets": ["python/pdf_oxide/... or tests/..."],\n'
        '  "test_plan": ["focused regression test idea"],\n'
        '  "patch_constraints": ["smallest safe change", "no generated artifacts"],\n'
        '  "confidence": "low|medium|high"\n'
        "}\n\n"
        f"Evidence summary:\n{json.dumps(payload, indent=2, sort_keys=True)}"
    )
    scillm_payload = {
        "model": model,
        "reasoning_effort": "high",
        "response_format": {"type": "json_object"},
        "scillm_metadata": {"batch_id": batch_id, "item_id": item_id},
        "messages": [{"role": "user", "content": prompt}],
    }
    return {
        "schema": "pdf_lab.second_pass.scillm_repair_plan_request.v1",
        "endpoint": "POST /v1/chat/completions",
        "model": model,
        "scillm_metadata": {"batch_id": batch_id, "item_id": item_id},
        "page_case": page_case,
        "scillm_payload": scillm_payload,
        "required_response_schema": "pdf_lab.second_pass.repair_plan.v1",
    }


def validate_repair_plan_request_contract(repair_plan_request: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if repair_plan_request.get("schema") != "pdf_lab.second_pass.scillm_repair_plan_request.v1":
        errors.append("repair_plan_request schema mismatch")
    if repair_plan_request.get("endpoint") != "POST /v1/chat/completions":
        errors.append("repair_plan_request endpoint mismatch")
    if repair_plan_request.get("required_response_schema") != "pdf_lab.second_pass.repair_plan.v1":
        errors.append("repair_plan_request required_response_schema mismatch")
    payload = repair_plan_request.get("scillm_payload")
    if not isinstance(payload, dict):
        errors.append("repair_plan_request scillm_payload must be an object")
        payload = {}
    if payload.get("response_format") != {"type": "json_object"}:
        errors.append("repair_plan_request response_format must require json_object")
    if payload.get("scillm_metadata") != repair_plan_request.get("scillm_metadata"):
        errors.append("repair_plan_request scillm_payload metadata must match top-level metadata")
    metadata = payload.get("scillm_metadata")
    if not isinstance(metadata, dict) or not metadata.get("batch_id") or not metadata.get("item_id"):
        errors.append("repair_plan_request scillm_metadata must include batch_id and item_id")
    page_case = repair_plan_request.get("page_case")
    if not isinstance(page_case, dict):
        errors.append("repair_plan_request page_case must be an object")
        page_case = {}
    page_case_identity = validate_page_case_identity(page_case)
    if page_case_identity["ok"] is not True:
        errors.extend(f"repair_plan_request {error}" for error in page_case_identity["errors"])
    case_id = page_case.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        errors.append("repair_plan_request page_case.case_id must be non-empty")
    elif isinstance(metadata, dict) and metadata.get("item_id") != f"{case_id}:repair_plan":
        errors.append("repair_plan_request scillm_metadata.item_id must match page_case.case_id repair-plan suffix")
    return {
        "schema": "pdf_lab.second_pass.repair_plan_request_validation.v1",
        "ok": not errors,
        "errors": errors,
        "scillm_metadata": metadata if isinstance(metadata, dict) else None,
    }


def validate_repair_plan(
    plan: dict[str, Any] | None,
    *,
    receipt: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    expected_metadata = request.get("scillm_metadata") if isinstance(request, dict) else None
    request_page_case = request.get("page_case") if isinstance(request, dict) else None
    if isinstance(request_page_case, dict):
        page_case = {
            "case_id": request_page_case.get("case_id"),
            "page_number": request_page_case.get("page_number"),
        }
        candidate_ids = request_page_case.get("candidate_ids")
        expected_candidate_ids = sorted(candidate_ids) if isinstance(candidate_ids, list) and all(isinstance(item, str) for item in candidate_ids) else []
    else:
        page_case = None
        expected_candidate_ids = []
    if receipt is not None:
        if not isinstance(receipt, dict):
            errors.append("repair plan receipt must be an object")
        else:
            if receipt.get("schema") != "pdf_lab.second_pass.scillm_repair_plan_receipt.v1":
                errors.append("repair plan receipt schema mismatch")
            if receipt.get("endpoint") != "POST /v1/chat/completions":
                errors.append("repair plan receipt endpoint mismatch")
            if receipt.get("http_status") != 200:
                errors.append("repair plan receipt http_status must be 200")
            receipt_metadata = receipt.get("scillm_metadata")
            if not isinstance(receipt_metadata, dict):
                errors.append("repair plan receipt missing scillm_metadata")
            elif isinstance(expected_metadata, dict):
                for key in ["batch_id", "item_id"]:
                    if receipt_metadata.get(key) != expected_metadata.get(key):
                        errors.append(f"repair plan receipt scillm_metadata {key} does not match request")
            if "repair_plan" in receipt and receipt.get("repair_plan") != plan:
                errors.append("repair plan receipt repair_plan does not match validated repair plan")
    if not isinstance(plan, dict):
        errors.append("repair plan missing or not an object")
        plan = {}
    if plan.get("schema") != "pdf_lab.second_pass.repair_plan.v1":
        errors.append("schema must be pdf_lab.second_pass.repair_plan.v1")
    for field in ["summary", "suspected_fault"]:
        if not isinstance(plan.get(field), str) or not plan.get(field, "").strip():
            errors.append(f"{field} must be non-empty")
    for field in ["patch_targets", "test_plan", "patch_constraints"]:
        value = plan.get(field)
        if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
            errors.append(f"{field} must be a non-empty list of strings")
    if plan.get("confidence") not in {"low", "medium", "high"}:
        errors.append("confidence must be low, medium, or high")
    for forbidden in ["terminal_status", "patched_confirmed", "commit_sha", "agent_resolved"]:
        if forbidden in plan:
            errors.append(f"repair plan must not include terminal/closure field: {forbidden}")
    return {
        "schema": "pdf_lab.second_pass.repair_plan_validation.v1",
        "ok": not errors,
        "errors": errors,
        "page_case": page_case,
        "candidate_count": len(expected_candidate_ids),
        "expected_candidate_ids": expected_candidate_ids,
    }


def call_scillm_repair_plan(
    repair_plan_request: dict[str, Any],
    *,
    base_url: str,
    auth_token: str,
    caller_skill: str,
    timeout_s: float,
) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    response = httpx.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {auth_token}",
            "X-Caller-Skill": caller_skill,
            "Content-Type": "application/json",
        },
        json=repair_plan_request["scillm_payload"],
        timeout=timeout_s,
    )
    response.raise_for_status()
    raw = response.json()
    content = raw["choices"][0]["message"]["content"]
    plan = parse_scillm_review_content(content)
    return {
        "schema": "pdf_lab.second_pass.scillm_repair_plan_receipt.v1",
        "endpoint": "POST /v1/chat/completions",
        "http_status": response.status_code,
        "scillm_metadata": repair_plan_request["scillm_metadata"],
        "raw_response": raw,
        "repair_plan": plan,
    }


def prompt_text_from_patch_request(patch_request: dict[str, Any]) -> str:
    prompt = patch_request.get("prompt")
    if isinstance(prompt, str):
        return prompt
    message_body = patch_request.get("message_body")
    if isinstance(message_body, dict) and isinstance(message_body.get("prompt"), str):
        return str(message_body["prompt"])
    return ""


def measure_prompt_text(prompt: str) -> dict[str, Any]:
    return {
        "char_count": len(prompt),
        "word_count": len(prompt.split()),
        "line_count": prompt.count("\n") + 1 if prompt else 0,
    }


def _contains_word_or_phrase(prompt: str, needle: str) -> bool:
    lowered = prompt.lower()
    if " " in needle:
        return needle in lowered
    import re  # noqa: PLC0415

    return re.search(rf"\b{re.escape(needle)}\b", lowered) is not None


def validate_patch_prompt_contract(
    patch_request: dict[str, Any],
    *,
    live_patch_required: bool,
    expected_page_case: dict[str, Any] | None = None,
    max_chars: int = PATCH_PROMPT_MAX_CHARS,
) -> dict[str, Any]:
    prompt = prompt_text_from_patch_request(patch_request)
    metrics = measure_prompt_text(prompt)
    missing_markers = [marker for marker in PATCH_PROMPT_REQUIRED_MARKERS if marker not in prompt]
    found_weasel_words = [word for word in PROMPT_WEASEL_WORDS if _contains_word_or_phrase(prompt, word)]
    errors: list[str] = []
    warnings: list[str] = []
    if not prompt.strip():
        errors.append("patch prompt is empty")
    if live_patch_required and metrics["char_count"] > max_chars:
        errors.append(f"patch prompt exceeds live max chars: {metrics['char_count']} > {max_chars}")
    if missing_markers:
        errors.append(f"patch prompt missing required markers: {missing_markers}")
    if found_weasel_words:
        errors.append(f"patch prompt contains banned vague words: {found_weasel_words}")
    if patch_request.get("prompt_profile") != "plan_only":
        warnings.append("live patch delegates should prefer prompt_profile=plan_only after a validated repair plan")
    if "Do not commit" not in prompt:
        errors.append("patch prompt must forbid commits")
    if "Workspace root:" not in prompt:
        errors.append("patch prompt must name the workspace root")
    if "review_response" not in prompt or "review_validation" not in prompt:
        errors.append("patch prompt must cite review_response and review_validation evidence fields")
    metadata = patch_request.get("scillm_metadata")
    if not isinstance(metadata, dict):
        errors.append("patch request missing scillm_metadata")
        metadata = {}
    schema = patch_request.get("schema")
    endpoint = patch_request.get("endpoint")
    if schema == "pdf_lab.second_pass.opencode_patch_request.v1":
        if endpoint != "POST /v1/scillm/opencode/runs":
            errors.append("opencode patch request endpoint mismatch")
    elif schema == "pdf_lab.second_pass.scillm_orchestrator_patch_request.v1":
        expected_endpoint = "POST /v1/scillm/opencode/transport/runs + children + message"
        if endpoint != expected_endpoint:
            errors.append("scillm orchestrator patch request endpoint mismatch")
        expected_dag_node_id = None
        case_id = metadata.get("case_id")
        if isinstance(case_id, str) and case_id:
            expected_dag_node_id = f"pdf_lab_second_pass_patch:{case_id}"
        if expected_dag_node_id is not None:
            if patch_request.get("dag_node_id") != expected_dag_node_id:
                errors.append("scillm orchestrator patch request dag_node_id must match scillm_metadata.case_id")
            create_run_body = patch_request.get("create_run_body")
            if not isinstance(create_run_body, dict) or create_run_body.get("dag_node_id") != expected_dag_node_id:
                errors.append("scillm orchestrator patch request create_run_body.dag_node_id must match scillm_metadata.case_id")
    else:
        errors.append("patch request schema mismatch")
    if expected_page_case is not None:
        page_case_identity = validate_page_case_identity(expected_page_case)
        if page_case_identity["ok"] is not True:
            errors.extend(f"patch request {error}" for error in page_case_identity["errors"])
        expected_case_id = expected_page_case.get("case_id")
        if metadata.get("case_id") != expected_case_id:
            errors.append("patch request scillm_metadata.case_id must match page_case.case_id")
        expected_page_number = expected_page_case.get("page_number")
        if metadata.get("page_number") != expected_page_number:
            errors.append("patch request scillm_metadata.page_number must match page_case.page_number")
    top_level_agent = patch_request.get("agent")
    if "agent" in metadata and metadata.get("agent") != top_level_agent:
        errors.append("patch request scillm_metadata.agent must match patch_request.agent")
    for field in ["attempt_index", "attempt_count", "transport_retry_fresh_parent"]:
        top_has = field in patch_request
        metadata_has = field in metadata
        if top_has != metadata_has:
            errors.append(f"patch request {field} must be present in both request and scillm_metadata")
            continue
        if not top_has:
            continue
        top_value = patch_request.get(field)
        metadata_value = metadata.get(field)
        if top_value != metadata_value:
            errors.append(f"patch request scillm_metadata.{field} must match patch_request.{field}")
        if field in {"attempt_index", "attempt_count"}:
            if type(top_value) is not int or top_value < 1:
                errors.append(f"patch request {field} must be a positive integer")
        elif type(top_value) is not bool:
            errors.append("patch request transport_retry_fresh_parent must be boolean")
    return {
        "schema": "pdf_lab.second_pass.patch_prompt_contract.v1",
        "ok": not errors,
        "live_patch_required": live_patch_required,
        "max_chars": max_chars,
        "metrics": metrics,
        "prompt_profile": patch_request.get("prompt_profile"),
        "missing_markers": missing_markers,
        "banned_weasel_words": found_weasel_words,
        "warnings": warnings,
        "errors": errors,
    }


def build_patch_prompt_review_payload(
    patch_request: dict[str, Any],
    prompt_contract: dict[str, Any],
) -> str:
    prompt = prompt_text_from_patch_request(patch_request)
    metadata = patch_request.get("scillm_metadata") or {}
    return (
        "# REVIEW REQUEST FOR WEB LLM\n"
        "#\n"
        "# Purpose: Validate the PDF Oxide pdf-lab OpenCode patch delegate prompt before live execution.\n"
        "# Consumer: scripts/pdf_lab/run_page_second_pass_dag.py -> scillm/OpenCode patch node.\n"
        "# Why this matters: A vague or oversized patch prompt can leave the executor silent or produce unauditable edits.\n"
        "# Input: patch_request.prompt or patch_request.message_body.prompt with page evidence paths and repair plan fields.\n"
        "# Output: One bounded patch attempt ending with PATCH_APPLIED or PATCH_DELEGATE_BLOCKED.\n"
        "# Last reviewed: 2026-06-02 by pdf-lab deterministic prompt gate.\n"
        "#\n"
        "# Review criteria:\n"
        "# 1. Is the patch task one bounded code-edit task?\n"
        "# 2. Are workspace root, evidence paths, editable prefixes, and no-commit constraints concrete?\n"
        "# 3. Is the final status grammar testable by code?\n"
        "# 4. Is the prompt short enough for a live OpenCode patch delegate?\n"
        "# 5. Are vague words absent according to best-practices-prompt?\n"
        "#\n"
        f"# Graph node: {metadata.get('graph_node')}\n"
        f"# Case ID: {metadata.get('case_id')}\n"
        f"# Page number: {metadata.get('page_number')}\n"
        f"# Prompt profile: {patch_request.get('prompt_profile')}\n"
        f"# Prompt contract ok: {prompt_contract.get('ok')}\n"
        "\n"
        "================================================================================\n"
        "PROMPT CONTRACT RESULT\n"
        "================================================================================\n"
        f"{json.dumps(prompt_contract, indent=2, sort_keys=True)}\n\n"
        "================================================================================\n"
        "USER PROMPT SENT TO OPENCODE\n"
        "================================================================================\n\n"
        f"{prompt}\n\n"
        "## VALID OUTPUT EXAMPLE\n"
        "PATCH_APPLIED changed_files=python/pdf_oxide/layout_classifier.py,tests/test_layout_classifier.py "
        "tests=tests/test_layout_classifier.py commands=uv run pytest tests/test_layout_classifier.py -q\n\n"
        "## INVALID OUTPUT EXAMPLES\n"
        "- `Done, the page is fixed.` Invalid: claims closure and omits changed files/tests.\n"
        "- `I think this might be fixed.` Invalid: no diff/status grammar.\n"
        "- `PATCH_APPLIED changed_files=artifacts/pdf_lab/page.json tests= commands=` Invalid: edits generated artifact and omits test.\n"
    )


def write_patch_prompt_contract_artifacts(
    case_dir: Path,
    patch_request: dict[str, Any],
    *,
    artifact_prefix: str,
    live_patch_required: bool,
    expected_page_case: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    prompt_contract = validate_patch_prompt_contract(
        patch_request,
        live_patch_required=live_patch_required,
        expected_page_case=expected_page_case,
    )
    contract_name = f"{artifact_prefix}prompt_contract.json"
    review_payload_name = f"{artifact_prefix}prompt_review_payload.txt"
    write_json(case_dir / contract_name, prompt_contract)
    (case_dir / review_payload_name).write_text(
        build_patch_prompt_review_payload(patch_request, prompt_contract),
        encoding="utf-8",
    )
    return prompt_contract, [contract_name, review_payload_name]


def build_opencode_patch_request(
    *,
    case_dir: Path,
    evidence_case_dir: Path | None = None,
    page_case: dict[str, Any],
    candidates: list[dict[str, Any]],
    review_response: dict[str, Any],
    agent: str,
    opencode_model: str | None,
    skills: list[str],
    timeout_s: float,
    cleanup_session: bool,
    cwd: Path,
    prompt_profile: str = "plan_only",
    repair_diagnosis: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    validate_opencode_agent_profile(agent)
    prompt = build_patch_worker_prompt(
        executor_label="OpenCode",
        case_dir=case_dir,
        evidence_case_dir=evidence_case_dir,
        workspace_root=cwd,
        page_case=page_case,
        candidates=candidates,
        review_response=review_response,
        prompt_profile=prompt_profile,
        repair_diagnosis=repair_diagnosis,
    )
    request = {
        "schema": "pdf_lab.second_pass.opencode_patch_request.v1",
        "endpoint": "POST /v1/scillm/opencode/runs",
        "agent": agent,
        "opencode_model": opencode_model,
        "skills": skills,
        "timeout_s": timeout_s,
        "cleanup_session": cleanup_session,
        "cwd": str(cwd.resolve()),
        "prompt_profile": prompt_profile,
        "prompt": prompt,
        "scillm_metadata": {
            "graph_node": "opencode_patch_attempt",
            "case_id": page_case["case_id"],
            "page_number": page_case["page_number"],
        },
    }
    if opencode_model:
        request["model"] = opencode_model
    if run_id:
        request["run_id"] = run_id
    return request


def build_opencode_repair_diagnosis_request(
    *,
    case_dir: Path,
    page_case: dict[str, Any],
    candidates: list[dict[str, Any]],
    review_response: dict[str, Any],
    agent: str,
    opencode_model: str | None,
    skills: list[str],
    timeout_s: float,
    cleanup_session: bool,
    cwd: Path,
    prompt_profile: str = "compact",
    run_id: str | None = None,
) -> dict[str, Any]:
    validate_opencode_agent_profile(agent)
    prompt = build_repair_diagnosis_prompt(
        executor_label="OpenCode",
        case_dir=case_dir,
        workspace_root=cwd,
        page_case=page_case,
        candidates=candidates,
        review_response=review_response,
        prompt_profile=prompt_profile,
    )
    request = {
        "schema": "pdf_lab.second_pass.opencode_repair_diagnosis_request.v1",
        "endpoint": "POST /v1/scillm/opencode/runs",
        "agent": agent,
        "opencode_model": opencode_model,
        "skills": skills,
        "timeout_s": timeout_s,
        "cleanup_session": cleanup_session,
        "cwd": str(cwd.resolve()),
        "prompt_profile": prompt_profile,
        "prompt": prompt,
        "page_case": {
            "case_id": page_case["case_id"],
            "page_number": page_case["page_number"],
            "candidate_ids": page_case.get("candidate_ids"),
        },
        "candidate_count": len(candidates),
        "candidate_ids": [candidate["candidate_id"] for candidate in candidates],
        "scillm_metadata": {
            "graph_node": "opencode_repair_diagnosis_attempt",
            "case_id": page_case["case_id"],
            "page_number": page_case["page_number"],
        },
    }
    if opencode_model:
        request["model"] = opencode_model
    if run_id:
        request["run_id"] = run_id
    return request


def call_opencode_patch(
    patch_request: dict[str, Any],
    *,
    base_url: str,
    auth_token: str,
    caller_skill: str,
    timeout_s: float,
) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    url = f"{base_url.rstrip('/')}/v1/scillm/opencode/runs"
    body = {
        "prompt": patch_request["prompt"],
        "agent": patch_request["agent"],
        "skills": patch_request["skills"],
        "timeout_s": patch_request["timeout_s"],
        "cleanup_session": patch_request["cleanup_session"],
        "cwd": patch_request["cwd"],
        "scillm_metadata": patch_request["scillm_metadata"],
    }
    if patch_request.get("model"):
        body["model"] = patch_request["model"]
    if patch_request.get("run_id"):
        body["run_id"] = patch_request["run_id"]
    response = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {auth_token}",
            "X-Caller-Skill": caller_skill,
            "Content-Type": "application/json",
        },
        json=body,
        timeout=timeout_s,
    )
    response.raise_for_status()
    raw = response.json()
    return {
        "schema": "pdf_lab.second_pass.opencode_patch_receipt.v1",
        "endpoint": "POST /v1/scillm/opencode/runs",
        "http_status": response.status_code,
        "request_metadata": patch_request["scillm_metadata"],
        "raw_response": raw,
    }


def safe_opencode_child_run_id(case_dir: Path, page_case: dict[str, Any], attempt_index: int) -> str:
    case_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(page_case.get("case_id") or "page-case")).strip("-")
    digest = hashlib.sha256(str(case_dir.resolve()).encode("utf-8")).hexdigest()[:10]
    return f"oc-pdflab-{case_id}-a{attempt_index:02d}-{digest}"


def opencode_workspace_route(*, workspace_path: str | None) -> str | None:
    if not workspace_path:
        return None
    workspace_token = base64.b64encode(str(workspace_path).encode("utf-8")).decode("ascii").rstrip("=")
    return f"/{workspace_token}"


def opencode_child_run_urls(
    *,
    base_url: str,
    run_id: str,
    session_id: str | None = None,
    workspace_path: str | None = None,
) -> dict[str, Any]:
    root = base_url.rstrip("/")
    opencode_base_url = os.environ.get("OPENCODE_SERVER_URL", "http://127.0.0.1:4098").rstrip("/")
    urls: dict[str, Any] = {
        "scillm_run_url": f"{root}/v1/scillm/opencode/runs/{run_id}",
        "scillm_status_url": f"{root}/v1/scillm/opencode/runs/{run_id}/status",
        "scillm_events_url": f"{root}/v1/scillm/opencode/runs/{run_id}/events?tail=200",
        "scillm_diff_url": f"{root}/v1/scillm/opencode/runs/{run_id}/diff",
        "opencode_base_url": opencode_base_url,
    }
    if session_id:
        api_url = f"{opencode_base_url}/session/{session_id}"
        urls["opencode_session_api_url"] = api_url
        workspace_route = opencode_workspace_route(workspace_path=workspace_path)
        if workspace_route:
            urls["opencode_workspace_url"] = f"{opencode_base_url}{workspace_route}"
    return urls


def fetch_opencode_run_snapshot(
    *,
    base_url: str,
    auth_token: str,
    caller_skill: str,
    run_id: str,
    timeout_s: float = 5.0,
) -> dict[str, Any] | None:
    import httpx  # noqa: PLC0415

    response = httpx.get(
        f"{base_url.rstrip('/')}/v1/scillm/opencode/runs/{run_id}",
        headers={
            "Authorization": f"Bearer {auth_token}",
            "X-Caller-Skill": caller_skill,
        },
        timeout=timeout_s,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else None


def human_monitor_from_scillm_sources(
    *,
    snapshot: dict[str, Any] | None,
    raw: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Prefer scillm human_monitor bundle over locally recomputed URLs."""
    for source in (snapshot, raw):
        if not isinstance(source, dict):
            continue
        monitor = source.get("human_monitor")
        if isinstance(monitor, dict):
            return monitor
        status = source.get("status")
        if isinstance(status, dict):
            nested = status.get("human_monitor")
            if isinstance(nested, dict):
                return nested
    return None


def write_opencode_child_run_handle(
    case_dir: Path,
    *,
    artifact_name: str,
    base_url: str,
    run_id: str,
    session_id: str | None = None,
    snapshot: dict[str, Any] | None = None,
    receipt: dict[str, Any] | None = None,
    workspace_path: str | None = None,
    state: str = "pending",
) -> str:
    raw = receipt.get("raw_response") if isinstance(receipt, dict) else None
    if isinstance(raw, dict):
        session_id = str(raw.get("session_id") or session_id or "") or None
        workspace_path = str(raw.get("cwd") or workspace_path or "") or None
        artifacts = raw.get("artifacts") if isinstance(raw.get("artifacts"), dict) else {}
        status = raw.get("status")
    else:
        artifacts = {}
        status = None
    if isinstance(snapshot, dict):
        session_id = str(snapshot.get("session_id") or session_id or "") or None
        workspace_path = str(
            snapshot.get("workspace_path")
            or snapshot.get("cwd")
            or workspace_path
            or ""
        ) or None
        if not artifacts and isinstance(snapshot.get("artifacts"), dict):
            artifacts = snapshot["artifacts"]
        if status is None:
            snapshot_status = snapshot.get("status")
            if isinstance(snapshot_status, dict):
                status = snapshot_status.get("state") or snapshot_status.get("phase")
    raw_dict = raw if isinstance(raw, dict) else None
    human_monitor = human_monitor_from_scillm_sources(snapshot=snapshot, raw=raw_dict)
    if isinstance(human_monitor, dict):
        session_id = str(human_monitor.get("session_id") or session_id or "") or None
        workspace_path = str(human_monitor.get("workspace_path") or workspace_path or "") or None
    payload = {
        "schema": "pdf_lab.second_pass.opencode_child_run_handle.v1",
        "state": state,
        "run_id": run_id,
        "session_id": session_id,
        "workspace_path": workspace_path,
        "urls": opencode_child_run_urls(
            base_url=base_url,
            run_id=run_id,
            session_id=session_id,
            workspace_path=workspace_path,
        ),
        "status": status or state,
        "artifacts": artifacts,
        "headers_required": {
            "scillm": ["Authorization: Bearer <token>", "X-Caller-Skill: pdf-lab"],
            "opencode_browser": {
                "basic_auth_required": True,
                "username": os.environ.get("OPENCODE_SERVER_USERNAME", "opencode"),
                "password_env": "OPENCODE_SERVER_PASSWORD",
            },
        },
        "note": (
            "session_id is populated after OpenCode serve creates the session; "
            "use scillm_run_url/events while it is pending."
            if not session_id
            else (
                "Give the human human_monitor_url for live progress; "
                "opencode_session_api_url is JSON only."
            )
        ),
    }
    if isinstance(human_monitor, dict):
        payload["human_monitor"] = human_monitor
        monitor_url = (
            human_monitor.get("scillm_chat_monitor_url")
            or human_monitor.get("human_monitor_url")
            or human_monitor.get("opencode_workspace_url")
        )
        if isinstance(monitor_url, str) and monitor_url.strip():
            payload["human_monitor_url"] = monitor_url.strip()
        workspace_url = human_monitor.get("opencode_workspace_url")
        if isinstance(workspace_url, str) and workspace_url.strip():
            payload["urls"]["opencode_workspace_url"] = workspace_url.strip()
        chat_monitor_url = human_monitor.get("scillm_chat_monitor_url")
        if isinstance(chat_monitor_url, str) and chat_monitor_url.strip():
            payload["urls"]["scillm_chat_monitor_url"] = chat_monitor_url.strip()
        for key in (
            "opencode_session_api_url",
            "opencode_messages_api_url",
            "opencode_diff_api_url",
        ):
            value = human_monitor.get(key)
            if isinstance(value, str) and value.strip():
                payload["urls"][key] = value.strip()
        for key in (
            "scillm_run_url",
            "scillm_status_url",
            "scillm_events_url",
            "scillm_diff_url",
        ):
            value = human_monitor.get(key)
            if isinstance(value, str) and value.strip() and run_id in value:
                payload["urls"][key] = value.strip()
        instruction = human_monitor.get("human_instruction")
        if isinstance(instruction, str) and instruction.strip():
            payload["note"] = instruction.strip()
    write_json(case_dir / artifact_name, payload)
    return artifact_name


def call_opencode_patch_observable(
    patch_request: dict[str, Any],
    *,
    base_url: str,
    auth_token: str,
    caller_skill: str,
    timeout_s: float,
    case_dir: Path,
    artifact_prefix: str,
) -> tuple[dict[str, Any], list[str]]:
    run_id = str(patch_request.get("run_id") or "").strip()
    if not run_id:
        run_id = safe_opencode_child_run_id(
            case_dir,
            {"case_id": patch_request["scillm_metadata"].get("case_id")},
            int(patch_request.get("attempt_index") or 1),
        )
        patch_request["run_id"] = run_id
    handle_artifact = f"{artifact_prefix}opencode_child_run_handle.json"
    workspace_path = str(patch_request.get("cwd") or "") or None
    artifacts = [
        write_opencode_child_run_handle(
            case_dir,
            artifact_name=handle_artifact,
            base_url=base_url,
            run_id=run_id,
            workspace_path=workspace_path,
            state="starting",
        )
    ]
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def worker() -> None:
        try:
            result["receipt"] = call_opencode_patch(
                patch_request,
                base_url=base_url,
                auth_token=auth_token,
                caller_skill=caller_skill,
                timeout_s=timeout_s,
            )
        except BaseException as exc:  # noqa: BLE001 - worker error is re-raised in caller thread.
            error["exception"] = exc

    thread = threading.Thread(target=worker, name=f"pdf-lab-opencode-{run_id}", daemon=True)
    thread.start()
    deadline = time.monotonic() + min(timeout_s, 30.0)
    last_snapshot: dict[str, Any] | None = None
    while thread.is_alive() and time.monotonic() < deadline:
        time.sleep(1.0)
        try:
            snapshot = fetch_opencode_run_snapshot(
                base_url=base_url,
                auth_token=auth_token,
                caller_skill=caller_skill,
                run_id=run_id,
                timeout_s=5.0,
            )
        except Exception:
            snapshot = None
        if not isinstance(snapshot, dict):
            continue
        last_snapshot = snapshot
        write_opencode_child_run_handle(
            case_dir,
            artifact_name=handle_artifact,
            base_url=base_url,
            run_id=run_id,
            snapshot=snapshot,
            workspace_path=workspace_path,
            state="running",
        )
        if snapshot.get("session_id"):
            break
    thread.join(timeout=max(timeout_s + 5.0, 5.0))
    if thread.is_alive():
        raise TimeoutError(f"OpenCode serve call did not return within timeout_s={timeout_s}")
    if "exception" in error:
        raise error["exception"]
    receipt = result["receipt"]
    write_opencode_child_run_handle(
        case_dir,
        artifact_name=handle_artifact,
        base_url=base_url,
        run_id=run_id,
        snapshot=last_snapshot,
        receipt=receipt,
        workspace_path=workspace_path,
        state="finished",
    )
    return receipt, artifacts


def parse_transport_sse_response(
    response: Any,
    *,
    max_elapsed_s: float | None = None,
    deadline_event_is_error: bool = True,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    raw_lines: list[str] = []
    final_result: dict[str, Any] | None = None
    event_type_counts: dict[str, int] = {}
    tool_errors: list[dict[str, Any]] = []
    session_errors: list[dict[str, Any]] = []
    permission_requests: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    saw_message_completed = False
    started_at = time.monotonic()
    for line in response.iter_lines():
        deadline_exceeded = max_elapsed_s is not None and time.monotonic() - started_at >= max_elapsed_s
        if not line:
            if deadline_exceeded:
                deadline_event = {
                    "event_type": "session_error" if deadline_event_is_error else "stream_deadline",
                    "status": "error" if deadline_event_is_error else "stopped",
                    "error_type": "stream_deadline_exceeded",
                    "error": f"transport stream exceeded {max_elapsed_s:.1f}s parse deadline",
                }
                events.append(deadline_event)
                event_type = str(deadline_event["event_type"])
                event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
                if deadline_event_is_error:
                    session_errors.append(deadline_event)
                break
            continue
        raw_lines.append(line)
        if not line.startswith("data:"):
            continue
        payload_text = line.removeprefix("data:").strip()
        if payload_text == "[DONE]":
            continue
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            error_event = {"event_type": "parse_error", "raw": payload_text}
            events.append(error_event)
            parse_errors.append(error_event)
            continue
        events.append(payload)
        event_type = str(payload.get("event_type") or "unknown")
        event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
        if event_type == "tool_call" and payload.get("status") == "error":
            tool_errors.append(payload)
        elif event_type in {"session_error", "message.failed"}:
            session_errors.append(payload)
        elif event_type == "permission_requested":
            permission_requests.append(payload)
        elif event_type == "message.completed" and isinstance(payload.get("result"), dict):
            saw_message_completed = True
            final_result = payload["result"]
        if deadline_exceeded:
            deadline_event = {
                "event_type": "session_error" if deadline_event_is_error else "stream_deadline",
                "status": "error" if deadline_event_is_error else "stopped",
                "error_type": "stream_deadline_exceeded",
                "error": f"transport stream exceeded {max_elapsed_s:.1f}s parse deadline",
            }
            events.append(deadline_event)
            event_type = str(deadline_event["event_type"])
            event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
            if deadline_event_is_error:
                session_errors.append(deadline_event)
            break
    delivery_state = None
    if final_result:
        delivery_state = final_result.get("delivery_state") or final_result.get("status")
    return {
        "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
        "event_count": len(events),
        "event_type_counts": event_type_counts,
        "events": events,
        "raw_line_count": len(raw_lines),
        "final_result": final_result or {},
        "delivery_state": delivery_state or "unknown",
        "saw_message_completed": saw_message_completed,
        "tool_errors": tool_errors,
        "session_errors": session_errors,
        "permission_requests": permission_requests,
        "parse_errors": parse_errors,
    }


def merge_transport_event_streams(primary: dict[str, Any], replay: dict[str, Any] | None) -> dict[str, Any]:
    if not replay:
        return primary
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for stream in (primary, replay):
        for event in stream.get("events") or []:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("event_id") or "")
            key = event_id or json.dumps(event, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
    event_type_counts: dict[str, int] = {}
    tool_errors: list[dict[str, Any]] = []
    session_errors: list[dict[str, Any]] = []
    permission_requests: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    raw_line_count = 0
    final_result = primary.get("final_result") or replay.get("final_result") or {}
    saw_message_completed = False
    for label, stream in [("primary", primary), ("replay", replay)]:
        raw_line_count += transport_raw_line_count(stream, label=label, parse_errors=parse_errors)
        saw_message_completed = (
            saw_message_completed
            or transport_saw_message_completed(stream, label=label, parse_errors=parse_errors)
        )
        for parse_error in stream.get("parse_errors") or []:
            if isinstance(parse_error, dict):
                parse_errors.append(parse_error)
    for event in events:
        event_type = str(event.get("event_type") or "unknown")
        event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
        if event_type == "tool_call" and event.get("status") == "error":
            tool_errors.append(event)
        elif event_type in {"session_error", "message.failed"}:
            session_errors.append(event)
        elif event_type == "permission_requested":
            permission_requests.append(event)
        elif event_type == "parse_error":
            parse_errors.append(event)
        elif event_type == "message.completed" and isinstance(event.get("result"), dict):
            final_result = final_result or event["result"]
            saw_message_completed = True
    delivery_state = final_result.get("delivery_state") or final_result.get("status") or primary.get("delivery_state") or replay.get("delivery_state")
    return {
        "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
        "event_count": len(events),
        "event_type_counts": event_type_counts,
        "events": events,
        "raw_line_count": raw_line_count,
        "final_result": final_result,
        "delivery_state": delivery_state or "unknown",
        "saw_message_completed": saw_message_completed,
        "tool_errors": tool_errors,
        "session_errors": session_errors,
        "permission_requests": permission_requests,
        "parse_errors": parse_errors,
        "merged_replay": True,
    }


def build_transport_session_error_stream(error_type: str, error: str) -> dict[str, Any]:
    event = {
        "event_type": "session_error",
        "status": "error",
        "error_type": error_type,
        "error": error,
    }
    return {
        "schema": "pdf_lab.second_pass.scillm_transport_event_stream.v1",
        "event_count": 1,
        "event_type_counts": {"session_error": 1},
        "events": [event],
        "raw_line_count": 0,
        "final_result": {
            "delivery_state": "failed",
            "status": "failed",
            "error_type": error_type,
            "error": error,
        },
        "delivery_state": "failed",
        "saw_message_completed": False,
        "tool_errors": [],
        "session_errors": [event],
        "permission_requests": [],
        "parse_errors": [],
    }


def write_transport_event_artifacts(case_dir: Path, event_stream: dict[str, Any], *, prefix: str = "") -> list[str]:
    stream_name = f"{prefix}transport_event_stream.json"
    events_name = f"{prefix}transport_events.jsonl"
    write_json(case_dir / stream_name, event_stream)
    with (case_dir / events_name).open("w", encoding="utf-8") as fh:
        for event in event_stream.get("events") or []:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
    return [stream_name, events_name]


def materialize_opencode_host_artifacts(case_dir: Path, receipt: dict[str, Any] | None, *, prefix: str = "") -> list[str]:
    if not isinstance(receipt, dict):
        return []
    raw = receipt.get("raw_response")
    if not isinstance(raw, dict):
        return []
    artifacts = raw.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    copied: list[str] = []
    event_counts: dict[str, int] = {}
    events_tail: list[dict[str, Any]] = []
    mapping = [
        ("host_status_json", f"{prefix}opencode_host_status.json"),
        ("host_opencode_result_json", f"{prefix}opencode_host_result.json"),
        ("host_events_jsonl", f"{prefix}opencode_host_events.jsonl"),
    ]
    for source_key, artifact_name in mapping:
        source_raw = artifacts.get(source_key)
        if not source_raw:
            continue
        source = Path(str(source_raw))
        if not source.is_file():
            continue
        dest = case_dir / artifact_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        copied.append(artifact_name)
        if source_key == "host_events_jsonl":
            for line in source.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    event = {"event": "unparseable", "raw": line}
                event_name = str(event.get("event") or "unknown")
                event_counts[event_name] = event_counts.get(event_name, 0) + 1
                events_tail.append(event)
                if len(events_tail) > 20:
                    events_tail.pop(0)
    if copied:
        summary_name = f"{prefix}opencode_host_artifacts_summary.json"
        write_json(
            case_dir / summary_name,
            {
                "schema": "pdf_lab.second_pass.opencode_host_artifacts_summary.v1",
                "run_id": raw.get("run_id"),
                "session_id": raw.get("session_id"),
                "status": raw.get("status"),
                "assistant_text_present": bool(str(raw.get("assistant_text") or "").strip()),
                "diff_present": has_nonempty_patch_artifact(raw.get("diff")),
                "skills": raw.get("skills"),
                "copied_artifacts": copied,
                "event_counts": event_counts,
                "events_tail": events_tail,
            },
        )
        copied.append(summary_name)
    return copied


def has_nonempty_patch_artifact(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(has_nonempty_patch_artifact(item) for item in value)
    if isinstance(value, dict):
        return any(has_nonempty_patch_artifact(item) for item in value.values())
    return bool(value)


def build_scillm_orchestrator_patch_request(
    *,
    case_dir: Path,
    evidence_case_dir: Path | None = None,
    page_case: dict[str, Any],
    candidates: list[dict[str, Any]],
    review_response: dict[str, Any],
    agent: str,
    opencode_model: str | None,
    skills: list[str],
    timeout_s: float,
    cwd: Path,
    child_mode: str = DEFAULT_TRANSPORT_CHILD_MODE,
    prompt_profile: str = "plan_only",
    repair_diagnosis: dict[str, Any] | None = None,
    transport_run_id: str | None = None,
) -> dict[str, Any]:
    validate_opencode_agent_profile(agent)
    dag_node_id = f"pdf_lab_second_pass_patch:{page_case['case_id']}"
    prompt = build_patch_worker_prompt(
        executor_label="scillm OpenCode transport",
        case_dir=case_dir,
        evidence_case_dir=evidence_case_dir,
        workspace_root=cwd,
        page_case=page_case,
        candidates=candidates,
        review_response=review_response,
        prompt_profile=prompt_profile,
        repair_diagnosis=repair_diagnosis,
    )
    create_body = {
        "dag_node_id": dag_node_id,
        "workspace": str(cwd.resolve()),
        "title": f"pdf-lab second-pass patch {page_case['case_id']}",
    }
    if transport_run_id:
        create_body["transport_run_id"] = transport_run_id
    request = {
        "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_request.v1",
        "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
        "dag_node_id": dag_node_id,
        "transport_run_id": transport_run_id,
        "agent": agent,
        "opencode_model": opencode_model,
        "role": "patch",
        "child_mode": child_mode,
        "prompt_profile": prompt_profile,
        "skills": skills,
        "timeout_s": timeout_s,
        "cwd": str(cwd.resolve()),
        "create_run_body": create_body,
        "create_child_body": {
            "role": "patch",
            "agent": agent,
            "mode": child_mode,
            "title": f"Patch {page_case['case_id']}",
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
            "graph_node": "scillm_orchestrator_patch_attempt",
            "case_id": page_case["case_id"],
            "page_number": page_case["page_number"],
        },
    }
    if opencode_model:
        request["message_body"]["model"] = opencode_model
    return request


def build_scillm_orchestrator_repair_diagnosis_request(
    *,
    case_dir: Path,
    page_case: dict[str, Any],
    candidates: list[dict[str, Any]],
    review_response: dict[str, Any],
    agent: str,
    opencode_model: str | None,
    skills: list[str],
    timeout_s: float,
    cwd: Path,
    prompt_profile: str = "compact",
    transport_run_id: str | None = None,
) -> dict[str, Any]:
    validate_opencode_agent_profile(agent)
    dag_node_id = f"pdf_lab_second_pass_diagnose:{page_case['case_id']}"
    prompt = build_repair_diagnosis_prompt(
        executor_label="scillm OpenCode transport",
        case_dir=case_dir,
        workspace_root=cwd,
        page_case=page_case,
        candidates=candidates,
        review_response=review_response,
        prompt_profile=prompt_profile,
    )
    create_body = {
        "dag_node_id": dag_node_id,
        "workspace": str(cwd.resolve()),
        "title": f"pdf-lab second-pass diagnosis {page_case['case_id']}",
    }
    if transport_run_id:
        create_body["transport_run_id"] = transport_run_id
    request = {
        "schema": "pdf_lab.second_pass.scillm_orchestrator_repair_diagnosis_request.v1",
        "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
        "dag_node_id": dag_node_id,
        "transport_run_id": transport_run_id,
        "agent": agent,
        "opencode_model": opencode_model,
        "role": "diagnose",
        "child_mode": "read_only",
        "prompt_profile": prompt_profile,
        "skills": skills,
        "timeout_s": timeout_s,
        "cwd": str(cwd.resolve()),
        "create_run_body": create_body,
        "create_child_body": {
            "role": "diagnose",
            "agent": agent,
            "mode": "read_only",
            "title": f"Diagnose {page_case['case_id']}",
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
            "graph_node": "scillm_orchestrator_repair_diagnosis_attempt",
            "case_id": page_case["case_id"],
            "page_number": page_case["page_number"],
        },
        "page_case": {
            "case_id": page_case["case_id"],
            "page_number": page_case["page_number"],
            "candidate_ids": page_case.get("candidate_ids"),
        },
        "candidate_count": len(candidates),
        "candidate_ids": [candidate["candidate_id"] for candidate in candidates],
    }
    if opencode_model:
        request["message_body"]["model"] = opencode_model
    return request


def call_scillm_orchestrator_patch(
    patch_request: dict[str, Any],
    *,
    base_url: str,
    auth_token: str,
    caller_skill: str,
    timeout_s: float,
) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    headers = {
        "Authorization": f"Bearer {auth_token}",
        "X-Caller-Skill": caller_skill,
        "Content-Type": "application/json",
    }
    root = f"{base_url.rstrip('/')}/v1/scillm/opencode/transport"
    with httpx.Client(timeout=timeout_s) as client:
        if patch_request.get("transport_run_id"):
            transport_run_id = str(patch_request["transport_run_id"])
            create_raw = {
                "transport_run_id": transport_run_id,
                "reused_existing_parent": True,
                "request": patch_request["create_run_body"],
            }
        else:
            create_response = client.post(f"{root}/runs", headers=headers, json=patch_request["create_run_body"])
            create_response.raise_for_status()
            create_raw = create_response.json()
            transport_run_id = create_raw["transport_run_id"]
        child_response = client.post(
            f"{root}/runs/{transport_run_id}/children",
            headers=headers,
            json=patch_request["create_child_body"],
        )
        child_response.raise_for_status()
        child_raw = child_response.json()
        message_status_code: int | None = None
        try:
            with client.stream(
                "POST",
                f"{root}/runs/{transport_run_id}/message",
                headers=headers,
                json=patch_request["message_body"],
            ) as message_response:
                message_status_code = message_response.status_code
                message_response.raise_for_status()
                event_stream = parse_transport_sse_response(message_response, max_elapsed_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 - transport replay may still contain the stored worker failure.
            event_stream = build_transport_session_error_stream(type(exc).__name__, str(exc))
        try:
            replay_timeout = httpx.Timeout(timeout_s, read=5.0)
            with client.stream(
                "GET",
                f"{root}/runs/{transport_run_id}/events/stream",
                headers=headers,
                timeout=replay_timeout,
            ) as replay_response:
                replay_response.raise_for_status()
                replay_stream = parse_transport_sse_response(
                    replay_response,
                    max_elapsed_s=10.0,
                    deadline_event_is_error=False,
                )
            event_stream = merge_transport_event_streams(event_stream, replay_stream)
        except Exception as exc:  # noqa: BLE001 - replay is additive evidence; primary stream remains authoritative.
            event_stream["event_replay_error"] = {
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        message_raw = event_stream["final_result"]
    return {
        "schema": "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1",
        "endpoint": "POST /v1/scillm/opencode/transport/runs + children + message",
        "http_status": message_status_code,
        "request_metadata": patch_request["scillm_metadata"],
        "transport_run_id": transport_run_id,
        "create_response": create_raw,
        "child_response": child_raw,
        "event_stream": event_stream,
        "message_response": message_raw,
        "observation": message_raw.get("observation") or child_raw.get("observation") or create_raw.get("observation"),
    }


def patch_delegate_has_terminal_sentinel(assistant_text: str) -> bool:
    return "PATCH_APPLIED" in assistant_text or "PATCH_DELEGATE_BLOCKED" in assistant_text


def split_delegate_path_list(value: str) -> list[str]:
    return [
        item.strip().strip("'\"")
        for item in re.split(r"[,\s]+", value.strip())
        if item.strip().strip("'\"")
    ]


def parse_patch_applied_claim(assistant_text: str) -> dict[str, Any]:
    line = next((item.strip() for item in assistant_text.splitlines() if "PATCH_APPLIED" in item), "")
    claim: dict[str, Any] = {
        "schema": "pdf_lab.second_pass.patch_applied_claim.v1",
        "status": "missing",
        "raw_line": line,
        "changed_files": [],
        "tests": [],
        "commands": "",
        "errors": [],
    }
    if not line:
        claim["errors"].append("PATCH_APPLIED line missing")
        return claim
    claim["status"] = "applied"
    key_matches = list(re.finditer(r"\b(changed_files|tests|commands)=", line))
    values: dict[str, str] = {}
    for index, match in enumerate(key_matches):
        key = match.group(1)
        value_start = match.end()
        value_end = key_matches[index + 1].start() if index + 1 < len(key_matches) else len(line)
        values[key] = line[value_start:value_end].strip()
    claim["changed_files"] = split_delegate_path_list(values.get("changed_files", ""))
    claim["tests"] = split_delegate_path_list(values.get("tests", ""))
    claim["commands"] = values.get("commands", "").strip()
    for required_key in ["changed_files", "tests", "commands"]:
        if required_key not in values:
            claim["errors"].append(f"PATCH_APPLIED missing {required_key}= field")
    if not claim["changed_files"]:
        claim["errors"].append("PATCH_APPLIED changed_files field is empty")
    if not claim["tests"]:
        claim["errors"].append("PATCH_APPLIED tests field is empty")
    if not claim["commands"]:
        claim["errors"].append("PATCH_APPLIED commands field is empty")
    return claim


def patch_delegate_stopped_after_tool_call(raw: dict[str, Any]) -> bool:
    message = raw.get("message")
    if not isinstance(message, dict):
        return False
    info = message.get("info")
    finish = info.get("finish") if isinstance(info, dict) else None
    if finish != "tool-calls":
        return False
    parts = message.get("parts")
    saw_tool = any(isinstance(part, dict) and part.get("type") == "tool" for part in parts or [])
    return saw_tool and not has_nonempty_patch_artifact(raw.get("diff")) and not has_nonempty_patch_artifact(raw.get("artifacts"))


def timeout_value_is_positive_finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def validate_runtime_timeout_inputs(
    *,
    scillm_timeout_s: Any,
    opencode_timeout_s: Any,
    page_extract_timeout_s: Any,
) -> list[str]:
    errors: list[str] = []
    if not timeout_value_is_positive_finite(scillm_timeout_s):
        errors.append(f"scillm_timeout_s must be a positive finite number: {scillm_timeout_s!r}")
    if not timeout_value_is_positive_finite(opencode_timeout_s):
        errors.append(f"opencode_timeout_s must be a positive finite number: {opencode_timeout_s!r}")
    if page_extract_timeout_s is not None and not timeout_value_is_positive_finite(page_extract_timeout_s):
        errors.append(f"page_extract_timeout_s must be null or a positive finite number: {page_extract_timeout_s!r}")
    return errors


def validate_runtime_boolean_inputs(*, opencode_cleanup_session: Any) -> list[str]:
    errors: list[str] = []
    if type(opencode_cleanup_session) is not bool:
        errors.append(f"opencode_cleanup_session must be a boolean: {opencode_cleanup_session!r}")
    return errors


def validate_optional_string_list(value: Any, *, field_name: str) -> list[str]:
    errors: list[str] = []
    if value is None:
        return errors
    if not isinstance(value, list):
        return [f"{field_name} must be a list of non-empty strings or null: {value!r}"]
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{field_name}[{index}] must be a non-empty string: {item!r}")
    return errors


def validate_runtime_list_inputs(
    *,
    opencode_agent_sequence: Any,
    opencode_skills: Any,
    allowed_patch_prefixes: Any,
    validation_commands: Any,
) -> list[str]:
    errors: list[str] = []
    for field_name, value in [
        ("opencode_agent_sequence", opencode_agent_sequence),
        ("opencode_skills", opencode_skills),
        ("allowed_patch_prefixes", allowed_patch_prefixes),
        ("validation_commands", validation_commands),
    ]:
        errors.extend(validate_optional_string_list(value, field_name=field_name))
    return errors


def validate_delegate_request_timeout(request: dict[str, Any] | None, errors: list[str], *, label: str) -> None:
    if not isinstance(request, dict):
        return
    validates_full_request = request.get("schema") is not None or "timeout_s" in request or "message_body" in request
    if not validates_full_request:
        return
    request_timeout_s = request.get("timeout_s")
    request_timeout_ok = timeout_value_is_positive_finite(request_timeout_s)
    if not request_timeout_ok:
        errors.append(f"{label} request timeout_s must be a positive finite number")
    if "message_body" not in request:
        return
    message_body = request.get("message_body")
    if not isinstance(message_body, dict):
        errors.append(f"{label} request message_body must be an object")
        return
    message_timeout_s = message_body.get("timeout_s")
    if not timeout_value_is_positive_finite(message_timeout_s):
        errors.append(f"{label} request message_body.timeout_s must be a positive finite number")
    elif request_timeout_ok and float(message_timeout_s) != float(request_timeout_s):
        errors.append(f"{label} request message_body.timeout_s must match request.timeout_s")


def validate_patch_delegate_receipt(
    receipt: dict[str, Any] | None,
    *,
    patch_mode: str,
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected_metadata = request.get("scillm_metadata") if isinstance(request, dict) else None
    if patch_mode == "dry_run":
        return {
            "schema": "pdf_lab.second_pass.patch_delegate_validation.v1",
            "ok": False,
            "errors": ["patch_delegate_dry_run"],
            "patch_status": "not_attempted",
        }
    errors: list[str] = []
    validate_delegate_request_timeout(request, errors, label="patch delegate")
    assistant_text = ""
    if not isinstance(receipt, dict):
        errors.append("patch receipt missing")
        raw = {}
        status = "missing"
        artifacts = None
    else:
        request_metadata = receipt.get("request_metadata")
        if not isinstance(request_metadata, dict):
            errors.append("patch receipt missing request_metadata")
        elif isinstance(expected_metadata, dict):
            for key in [
                "graph_node",
                "case_id",
                "page_number",
                "attempt_index",
                "attempt_count",
                "agent",
                "transport_retry_fresh_parent",
            ]:
                if key in expected_metadata and request_metadata.get(key) != expected_metadata.get(key):
                    errors.append(f"patch receipt request_metadata {key} does not match request")
    if isinstance(receipt, dict) and receipt.get("schema") == "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1":
        if receipt.get("endpoint") != "POST /v1/scillm/opencode/transport/runs + children + message":
            errors.append("transport patch receipt endpoint mismatch")
        if receipt.get("http_status") != 200:
            errors.append("transport patch receipt http_status must be 200")
        raw = receipt.get("message_response")
        if not isinstance(raw, dict):
            errors.append("orchestrator receipt missing message_response")
            raw = {}
        status = raw.get("delivery_state")
        event_stream = receipt.get("event_stream")
        if not isinstance(event_stream, dict):
            errors.append("orchestrator receipt missing event_stream")
            event_stream = {}
        if status not in TRANSPORT_SUCCESS_DELIVERY_STATES:
            errors.append(f"transport delivery_state is not completed/acted/idle_seen: {status}")
        if not event_stream.get("saw_message_completed"):
            errors.append("transport stream did not include message.completed")
        if event_stream.get("parse_errors"):
            errors.append("transport stream contained unparsable data events")
        if event_stream.get("session_errors"):
            errors.append("transport stream contained session_error events")
            for session_error in event_stream.get("session_errors") or []:
                if isinstance(session_error, dict) and session_error.get("error_type"):
                    errors.append(
                        f"transport session_error {session_error.get('error_type')}: {session_error.get('error') or ''}".strip()
                    )
        if event_stream.get("tool_errors"):
            errors.append("transport stream contained failed tool_call events")
        if event_stream.get("permission_requests"):
            errors.append("transport stream requested permission and did not complete unattended")
        if isinstance(event_stream.get("final_result"), dict) and event_stream.get("final_result") != raw:
            errors.append("transport patch event_stream.final_result does not match message_response")
        if raw.get("error"):
            errors.append("transport message returned error")
        assistant_text = str(raw.get("assistant_text") or "")
        if not assistant_text.strip():
            errors.append("transport patch delegate produced no assistant_text terminal sentinel")
        elif not patch_delegate_has_terminal_sentinel(assistant_text):
            errors.append("transport patch delegate response missing PATCH_APPLIED/PATCH_DELEGATE_BLOCKED sentinel")
        if patch_delegate_stopped_after_tool_call(raw):
            errors.append("transport patch delegate stopped after tool call without terminal sentinel or diff")
        if "PATCH_DELEGATE_BLOCKED" in assistant_text:
            errors.append("transport patch delegate reported blocked substrate")
        message = raw.get("message")
        if isinstance(message, dict):
            info = message.get("info")
            if isinstance(info, dict) and info.get("error"):
                errors.append("transport message contained worker/provider error")
        if not raw.get("assistant_text") and not raw.get("diff") and raw.get("message"):
            errors.append("transport message completed without assistant text or diff")
        artifacts = {
            "transport_run_id": receipt.get("transport_run_id"),
            "observation": receipt.get("observation"),
            "diff": raw.get("diff"),
            "event_count": event_stream.get("event_count"),
        }
        if not has_nonempty_patch_artifact(raw.get("diff")):
            errors.append("transport patch delegate produced no diff")
    elif isinstance(receipt, dict):
        if receipt.get("schema") != "pdf_lab.second_pass.opencode_patch_receipt.v1":
            errors.append("OpenCode patch receipt schema mismatch")
        if receipt.get("endpoint") != "POST /v1/scillm/opencode/runs":
            errors.append("OpenCode patch receipt endpoint mismatch")
        if receipt.get("http_status") != 200:
            errors.append("OpenCode patch receipt http_status must be 200")
        raw = receipt.get("raw_response")
        if not isinstance(raw, dict):
            errors.append("patch receipt missing raw_response")
            raw = {}
        status = raw.get("status")
        if status not in {"completed", "success", "ok"}:
            errors.append(f"OpenCode run status is not completed/success/ok: {status}")
            if status == "timeout":
                errors.append("OpenCode run timed out before producing a patch diff")
        if raw.get("error"):
            errors.append("OpenCode run returned error")
        assistant_text = str(raw.get("assistant_text") or raw.get("output") or raw.get("text") or "")
        if not assistant_text.strip():
            errors.append("OpenCode patch delegate produced no assistant_text terminal sentinel")
        elif not patch_delegate_has_terminal_sentinel(assistant_text):
            errors.append("OpenCode patch delegate response missing PATCH_APPLIED/PATCH_DELEGATE_BLOCKED sentinel")
        if patch_delegate_stopped_after_tool_call(raw):
            errors.append("OpenCode patch delegate stopped after tool call without terminal sentinel or diff")
        if "PATCH_DELEGATE_BLOCKED" in assistant_text:
            errors.append("OpenCode patch delegate reported blocked substrate")
        artifacts = raw.get("artifacts")
        if not has_nonempty_patch_artifact(raw.get("diff")) and not has_nonempty_patch_artifact(artifacts):
            errors.append("OpenCode patch delegate produced no diff or patch artifact")
    applied_claim = parse_patch_applied_claim(assistant_text) if "PATCH_APPLIED" in assistant_text else None
    if applied_claim is not None and applied_claim["errors"]:
        errors.extend(applied_claim["errors"])
    if artifacts is not None and not isinstance(artifacts, (dict, list)):
        errors.append("OpenCode artifacts must be object or list when present")
    return {
        "schema": "pdf_lab.second_pass.patch_delegate_validation.v1",
        "ok": not errors,
        "errors": errors,
        "patch_status": status or "unknown",
        "artifacts_present": bool(artifacts),
        "applied_claim": applied_claim,
    }


def validate_repair_diagnosis_delegate_receipt(
    receipt: dict[str, Any] | None,
    *,
    patch_mode: str,
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected_metadata = request.get("scillm_metadata") if isinstance(request, dict) else None
    request_page_case = request.get("page_case") if isinstance(request, dict) else None
    if isinstance(request_page_case, dict):
        page_case = {
            "case_id": request_page_case.get("case_id"),
            "page_number": request_page_case.get("page_number"),
        }
        candidate_ids = request_page_case.get("candidate_ids")
        expected_candidate_ids = sorted(candidate_ids) if isinstance(candidate_ids, list) and all(isinstance(item, str) for item in candidate_ids) else []
    else:
        page_case = None
        expected_candidate_ids = []
    if patch_mode == "dry_run":
        return {
            "schema": "pdf_lab.second_pass.repair_diagnosis_validation.v1",
            "ok": False,
            "errors": ["repair_diagnosis_dry_run"],
            "diagnosis_status": "not_attempted",
            "assistant_text_present": False,
            "page_case": page_case,
            "candidate_count": len(expected_candidate_ids),
            "expected_candidate_ids": expected_candidate_ids,
        }
    errors: list[str] = []
    validate_delegate_request_timeout(request, errors, label="repair diagnosis delegate")
    assistant_text = ""
    status = "missing"
    if not isinstance(receipt, dict):
        errors.append("repair diagnosis receipt missing")
    else:
        request_metadata = receipt.get("request_metadata")
        if not isinstance(request_metadata, dict):
            errors.append("repair diagnosis receipt missing request_metadata")
        elif isinstance(expected_metadata, dict):
            for key in ["graph_node", "case_id", "page_number", "attempt_index", "attempt_count", "agent"]:
                if key in expected_metadata and request_metadata.get(key) != expected_metadata.get(key):
                    errors.append(f"repair diagnosis receipt request_metadata {key} does not match request")
    if isinstance(receipt, dict) and receipt.get("schema") == "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1":
        if receipt.get("endpoint") != "POST /v1/scillm/opencode/transport/runs + children + message":
            errors.append("transport diagnosis receipt endpoint mismatch")
        if receipt.get("http_status") != 200:
            errors.append("transport diagnosis receipt http_status must be 200")
        raw = receipt.get("message_response")
        if not isinstance(raw, dict):
            errors.append("orchestrator diagnosis receipt missing message_response")
            raw = {}
        status = str(raw.get("delivery_state") or raw.get("status") or "unknown")
        event_stream = receipt.get("event_stream")
        if not isinstance(event_stream, dict):
            errors.append("orchestrator diagnosis receipt missing event_stream")
            event_stream = {}
        if status not in TRANSPORT_SUCCESS_DELIVERY_STATES:
            errors.append(f"transport diagnosis delivery_state is not completed/acted/idle_seen: {status}")
        if not event_stream.get("saw_message_completed"):
            errors.append("transport diagnosis stream did not include message.completed")
        if event_stream.get("parse_errors"):
            errors.append("transport diagnosis stream contained unparsable data events")
        if event_stream.get("session_errors"):
            errors.append("transport diagnosis stream contained session_error events")
        if event_stream.get("tool_errors"):
            errors.append("transport diagnosis stream contained failed tool_call events")
        if isinstance(event_stream.get("final_result"), dict) and event_stream.get("final_result") != raw:
            errors.append("transport diagnosis event_stream.final_result does not match message_response")
        assistant_text = str(raw.get("assistant_text") or "")
        if has_nonempty_patch_artifact(raw.get("diff")):
            errors.append("repair diagnosis delegate must not produce a patch diff")
    elif isinstance(receipt, dict):
        if receipt.get("schema") != "pdf_lab.second_pass.opencode_patch_receipt.v1":
            errors.append("OpenCode diagnosis receipt schema mismatch")
        if receipt.get("endpoint") != "POST /v1/scillm/opencode/runs":
            errors.append("OpenCode diagnosis receipt endpoint mismatch")
        if receipt.get("http_status") != 200:
            errors.append("OpenCode diagnosis receipt http_status must be 200")
        raw = receipt.get("raw_response")
        if not isinstance(raw, dict):
            errors.append("repair diagnosis receipt missing raw_response")
            raw = {}
        status = str(raw.get("status") or "unknown")
        if status not in {"completed", "success", "ok"}:
            errors.append(f"OpenCode diagnosis status is not completed/success/ok: {status}")
            if status == "timeout":
                errors.append("OpenCode diagnosis timed out before producing a repair plan")
        assistant_text = str(raw.get("assistant_text") or raw.get("output") or raw.get("text") or "")
        if has_nonempty_patch_artifact(raw.get("diff")):
            errors.append("repair diagnosis delegate must not produce a patch diff")
    if not assistant_text.strip():
        errors.append("repair diagnosis delegate produced no assistant_text repair plan")
    if "PATCH_DELEGATE_BLOCKED" in assistant_text:
        errors.append("repair diagnosis delegate reported blocked substrate")
    return {
        "schema": "pdf_lab.second_pass.repair_diagnosis_validation.v1",
        "ok": not errors,
        "errors": errors,
        "diagnosis_status": status,
        "assistant_text_present": bool(assistant_text.strip()),
        "page_case": page_case,
        "candidate_count": len(expected_candidate_ids),
        "expected_candidate_ids": expected_candidate_ids,
    }


def patch_validation_has_delegate_timeout(validation: dict[str, Any] | None) -> bool:
    if not validation:
        return False
    validation_errors = validation_error_list(validation, "patch_validation")
    return any(
        "stream deadline" in str(error)
        or "stream_deadline" in str(error)
        or "timed out" in str(error).lower()
        or "status is not completed/success/ok: timeout" in str(error)
        for error in validation_errors
    )


def patch_validation_has_recoverable_transport_failure(validation: dict[str, Any] | None) -> bool:
    if not validation:
        return False
    errors = [str(error) for error in validation_error_list(validation, "patch_validation")]
    joined = "\n".join(errors).lower()
    return any(
        marker in joined
        for marker in [
            "remoteprotocolerror",
            "incomplete chunked read",
            "connection refused",
            "stream_deadline",
            "stream deadline",
            "transport stream did not include message.completed",
        ]
    )


def validation_error_list(validation: dict[str, Any] | None, label: str) -> list[str]:
    if not isinstance(validation, dict):
        return [f"{label} missing"]
    raw_errors = validation.get("errors")
    if raw_errors is None:
        return []
    if not isinstance(raw_errors, list):
        return [f"{label} errors must be a list"]
    if not all(isinstance(error, str) for error in raw_errors):
        return [f"{label} errors must be a list of strings"]
    return raw_errors


def build_patch_delegate_bug_report(
    *,
    case_id: str,
    page_number: int,
    code_root: Path,
    patch_backend: str,
    patch_mode: str,
    terminal_reason: str,
    patch_request: dict[str, Any] | None,
    patch_receipt: dict[str, Any] | None,
    patch_error: dict[str, Any] | None,
    patch_validation: dict[str, Any],
    patch_attempts_ledger: dict[str, Any] | None,
    transport_event_artifacts: list[str],
    opencode_host_artifacts: list[str],
) -> dict[str, Any]:
    receipt_schema = patch_receipt.get("schema") if isinstance(patch_receipt, dict) else None
    raw_response: dict[str, Any] = {}
    if isinstance(patch_receipt, dict):
        if receipt_schema == "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1":
            raw_response = patch_receipt.get("message_response") if isinstance(patch_receipt.get("message_response"), dict) else {}
        else:
            raw_response = patch_receipt.get("raw_response") if isinstance(patch_receipt.get("raw_response"), dict) else {}
    event_stream = patch_receipt.get("event_stream") if isinstance(patch_receipt, dict) and isinstance(patch_receipt.get("event_stream"), dict) else {}
    return {
        "schema": "pdf_lab.second_pass.scillm_patch_delegate_bug_report.v1",
        "title": "scillm OpenCode patch delegate failed to complete a bounded pdf-lab patch",
        "case_id": case_id,
        "page_number": page_number,
        "code_root": str(code_root.resolve()),
        "patch_backend": patch_backend,
        "patch_mode": patch_mode,
        "terminal_reason": terminal_reason,
        "expected": [
            "transport or serve executor completes within timeout",
            "assistant_text includes PATCH_APPLIED or PATCH_DELEGATE_BLOCKED",
            "successful patch attempt returns a non-empty diff touching only allowed files",
            "blocked attempt returns a concrete substrate reason without pretending success",
        ],
        "observed": {
            "validation_errors": validation_error_list(patch_validation, "patch_validation"),
            "receipt_schema": receipt_schema,
            "endpoint": patch_request.get("endpoint") if isinstance(patch_request, dict) else None,
            "transport_run_id": patch_receipt.get("transport_run_id") if isinstance(patch_receipt, dict) else None,
            "http_status": patch_receipt.get("http_status") if isinstance(patch_receipt, dict) else None,
            "delivery_state_or_status": raw_response.get("delivery_state") or raw_response.get("status"),
            "assistant_text_present": bool(str(raw_response.get("assistant_text") or raw_response.get("output") or raw_response.get("text") or "").strip()),
            "diff_present": has_nonempty_patch_artifact(raw_response.get("diff")),
            "event_count": event_stream.get("event_count"),
            "saw_message_completed": event_stream.get("saw_message_completed"),
            "session_errors": event_stream.get("session_errors") or [],
            "tool_errors": event_stream.get("tool_errors") or [],
            "permission_requests": event_stream.get("permission_requests") or [],
            "patch_error": patch_error,
        },
        "artifacts": {
            "request": "patch_request.json",
            "receipt": "patch_receipt.json" if patch_receipt is not None else None,
            "validation": "patch_validation.json",
            "attempts_ledger": "patch_attempts_ledger.json" if patch_attempts_ledger is not None else None,
            "transport_event_artifacts": transport_event_artifacts,
            "opencode_host_artifacts": opencode_host_artifacts,
            "terminal_ledger": "terminal_ledger.json",
        },
        "scillm_project_agent_bug_report": (
            "The pdf-lab harness submitted a bounded patch delegate request through scillm, "
            "but the executor failed to return terminal patch evidence. Fix scillm/OpenCode so "
            "this request either completes with PATCH_APPLIED plus a diff, or returns "
            "PATCH_DELEGATE_BLOCKED with a concrete substrate reason."
        ),
    }


def git_changed_files(repo: Path = REPO) -> list[str]:
    tracked = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if tracked.returncode != 0:
        raise RuntimeError(tracked.stderr.strip() or "git diff --name-only failed")
    staged = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if staged.returncode != 0:
        raise RuntimeError(staged.stderr.strip() or "git diff --cached --name-only failed")
    untracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--others", "--exclude-standard"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if untracked.returncode != 0:
        raise RuntimeError(untracked.stderr.strip() or "git ls-files --others failed")
    return sorted({
        path.strip()
        for path in [*tracked.stdout.splitlines(), *staged.stdout.splitlines(), *untracked.stdout.splitlines()]
        if path.strip()
    })


def is_generated_patch_artifact(path: str) -> bool:
    parts = Path(path).parts
    return "__pycache__" in parts or path.endswith((".pyc", ".pyo"))


def cleanup_python_bytecode_caches(cwd: Path) -> list[str]:
    bytecode_cleanup: list[str] = []
    for pycache_dir in cwd.rglob("__pycache__"):
        if pycache_dir.is_dir():
            relative = pycache_dir.relative_to(cwd)
            if any(part.startswith(".") for part in relative.parts):
                continue
            shutil.rmtree(pycache_dir, ignore_errors=True)
            if not pycache_dir.exists():
                bytecode_cleanup.append(str(relative))
    return sorted(bytecode_cleanup)


def meaningful_patch_files(paths: list[str]) -> list[str]:
    return sorted({path for path in paths if not is_generated_patch_artifact(path)})


def compute_patch_delta(before_files: list[str], after_files: list[str]) -> dict[str, Any]:
    raw_before = set(before_files)
    raw_after = set(after_files)
    ignored_generated = sorted(
        path for path in raw_after - raw_before
        if is_generated_patch_artifact(path)
    )
    before = set(meaningful_patch_files(before_files))
    after = set(meaningful_patch_files(after_files))
    delta = sorted(after - before)
    unchanged_dirty = sorted(before & after)
    return {
        "schema": "pdf_lab.second_pass.patch_delta.v1",
        "baseline_changed_files": sorted(before),
        "after_changed_files": sorted(after),
        "patch_changed_files": delta,
        "ignored_generated_files": ignored_generated,
        "preexisting_dirty_files_still_dirty": unchanged_dirty,
        "ok": bool(delta),
        "errors": [] if delta else ["patch produced no isolatable new changed files"],
    }


def validate_patch_scope(
    changed_files: list[str],
    allowed_prefixes: list[str],
    delegate_claim: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    if not changed_files:
        errors.append("patch produced no changed files")
    disallowed = [
        path for path in changed_files
        if not any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in allowed_prefixes)
    ]
    if disallowed:
        errors.append(f"patch touched disallowed files: {disallowed}")
    python_test_files = [path for path in changed_files if path.startswith("tests/") and path.endswith(".py")]
    rust_test_files = [
        path
        for path in changed_files
        if path.startswith("src/") and path.endswith(".rs")
    ]
    test_files = python_test_files
    if delegate_claim is not None:
        claimed_tests_preview = sorted(str(path) for path in delegate_claim.get("tests") or [])
        test_files = sorted(set(python_test_files) | (set(rust_test_files) & set(claimed_tests_preview)))
    if not test_files:
        errors.append("patch must add or update at least one regression test under tests/ or claimed in-file Rust unit test under src/")
    generated_artifacts = [
        path for path in changed_files
        if path.startswith("artifacts/") or path.startswith(".plan-iterate/")
    ]
    if generated_artifacts:
        errors.append(f"patch must not use generated artifacts as proof: {generated_artifacts}")
    if delegate_claim is not None:
        if delegate_claim.get("schema") != "pdf_lab.second_pass.patch_applied_claim.v1":
            errors.append("patch delegate claim schema is invalid")
        for claim_error in validation_error_list(delegate_claim, "patch delegate claim"):
            errors.append(f"patch delegate claim invalid: {claim_error}")
        claimed_changed_files = sorted(delegate_claim.get("changed_files") or [])
        claimed_tests = sorted(str(path) for path in delegate_claim.get("tests") or [])
        claimed_patch_files = sorted(set(claimed_changed_files) | set(claimed_tests))
        observed_changed_files = sorted(changed_files)
        if claimed_patch_files != observed_changed_files:
            errors.append(
                "PATCH_APPLIED changed_files do not match observed patch delta: "
                f"claimed={claimed_patch_files} observed={observed_changed_files}"
            )
        observed_tests = sorted(test_files)
        if claimed_tests != observed_tests:
            errors.append(
                "PATCH_APPLIED tests do not match observed regression test delta: "
                f"claimed={claimed_tests} observed={observed_tests}"
            )
    return {
        "schema": "pdf_lab.second_pass.patch_scope_validation.v1",
        "ok": not errors,
        "errors": errors,
        "changed_files": changed_files,
        "allowed_prefixes": allowed_prefixes,
        "test_files": test_files,
        "delegate_claim": delegate_claim,
    }


def run_validation_commands(
    commands: list[str],
    cwd: Path = REPO,
    required_test_files: list[str] | None = None,
) -> dict[str, Any]:
    required_test_files = sorted(required_test_files or [])
    results = []
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=cwd,
            shell=True,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        results.append(
            {
                "command": command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )
    bytecode_cleanup = cleanup_python_bytecode_caches(cwd)
    errors = [f"command failed: {item['command']}" for item in results if item["exit_code"] != 0]
    zero_test_commands = []
    for item in results:
        if item["exit_code"] != 0 or not item["command"].strip().startswith("cargo test"):
            continue
        test_counts = [
            int(match.group(1))
            for match in re.finditer(
                r"\brunning\s+(\d+)\s+tests?\b",
                f"{item['stdout']}\n{item['stderr']}",
                flags=re.IGNORECASE,
            )
        ]
        if test_counts and max(test_counts) == 0:
            zero_test_commands.append(item["command"])
    if zero_test_commands:
        errors.append(f"validation command ran zero cargo tests: {zero_test_commands}")
    if not commands:
        errors.append("no validation commands configured")
    covered_test_files = sorted(
        test_file
        for test_file in required_test_files
        if any(test_file in command for command in commands)
    )
    missing_test_file_coverage = sorted(set(required_test_files) - set(covered_test_files))
    if missing_test_file_coverage:
        errors.append(f"validation commands did not cover changed regression tests: {missing_test_file_coverage}")
    command_test_files = sorted(
        {
            match.rstrip(".,)")
            for command in commands
            for match in re.findall(r"(?<![\w./-])((?:tests|src)/[^\s'\"#;]+?\.(?:py|rs))", command)
        }
    )
    return {
        "schema": "pdf_lab.second_pass.test_validation.v1",
        "ok": not errors,
        "errors": errors,
        "results": results,
        "test_files": sorted(set(command_test_files) | set(covered_test_files)),
        "required_test_files": required_test_files,
        "covered_test_files": covered_test_files,
        "missing_test_file_coverage": missing_test_file_coverage,
        "bytecode_cache_cleanup": bytecode_cleanup,
    }


def git_staged_files(repo: Path = REPO) -> list[str]:
    staged = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if staged.returncode != 0:
        raise RuntimeError(staged.stderr.strip() or "git diff --cached --name-only failed")
    return sorted(path.strip() for path in staged.stdout.splitlines() if path.strip())


def git_unstage_files(repo: Path, paths: list[str]) -> dict[str, Any]:
    if not paths:
        return {
            "schema": "pdf_lab.second_pass.git_unstage_cleanup.v1",
            "ok": True,
            "paths": [],
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "staged_files_after_cleanup": git_staged_files(repo),
        }
    cleanup = subprocess.run(
        ["git", "-C", str(repo), "reset", "-q", "HEAD", "--", *paths],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    staged_after = git_staged_files(repo)
    attempted_still_staged = sorted(path for path in staged_after if path in set(paths))
    errors: list[str] = []
    if cleanup.returncode != 0:
        errors.append(cleanup.stderr.strip() or cleanup.stdout.strip() or "git reset HEAD failed")
    if attempted_still_staged:
        errors.append(f"attempted patch files remained staged after cleanup: {attempted_still_staged}")
    return {
        "schema": "pdf_lab.second_pass.git_unstage_cleanup.v1",
        "ok": not errors,
        "errors": errors,
        "paths": paths,
        "exit_code": cleanup.returncode,
        "stdout": cleanup.stdout,
        "stderr": cleanup.stderr,
        "staged_files_after_cleanup": staged_after,
    }


def git_commit_files(commit_sha: str, repo: Path = REPO) -> list[str]:
    committed = subprocess.run(
        ["git", "-C", str(repo), "show", "--pretty=format:", "--name-only", commit_sha],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if committed.returncode != 0:
        raise RuntimeError(committed.stderr.strip() or "git show --name-only failed")
    return sorted(path.strip() for path in committed.stdout.splitlines() if path.strip())


def git_changed_files_for_paths(repo: Path, paths: list[str]) -> list[str]:
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all", "--", *paths],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or "git status --porcelain failed")
    changed: list[str] = []
    for line in status.stdout.splitlines():
        if not line:
            continue
        path = line[3:] if len(line) > 3 else ""
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            changed.append(path.strip())
    return sorted(changed)


def verify_commit_revertability(commit_sha: str, repo: Path = REPO) -> dict[str, Any]:
    worktree_dir = repo / ".pdf_lab_revert_checks" / commit_sha
    if worktree_dir.exists():
        shutil.rmtree(worktree_dir)
    add_worktree = subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree_dir), commit_sha],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if add_worktree.returncode != 0:
        return {
            "schema": "pdf_lab.second_pass.revertability_check.v1",
            "ok": False,
            "commit_sha": commit_sha,
            "method": "git worktree add --detach + git revert --no-commit",
            "worktree_dir": str(worktree_dir),
            "errors": [add_worktree.stderr.strip() or add_worktree.stdout.strip() or "git worktree add failed"],
            "revert_exit_code": None,
            "revert_stdout": "",
            "revert_stderr": "",
            "status_after_revert": "",
        }
    try:
        revert = subprocess.run(
            ["git", "-C", str(worktree_dir), "revert", "--no-commit", commit_sha],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        status = subprocess.run(
            ["git", "-C", str(worktree_dir), "status", "--short"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        errors: list[str] = []
        if revert.returncode != 0:
            errors.append(revert.stderr.strip() or revert.stdout.strip() or "git revert --no-commit failed")
        if status.returncode != 0:
            errors.append(status.stderr.strip() or "git status after revert failed")
        return {
            "schema": "pdf_lab.second_pass.revertability_check.v1",
            "ok": not errors,
            "commit_sha": commit_sha,
            "method": "git worktree add --detach + git revert --no-commit",
            "worktree_dir": str(worktree_dir),
            "errors": errors,
            "revert_exit_code": revert.returncode,
            "revert_stdout": revert.stdout,
            "revert_stderr": revert.stderr,
            "status_after_revert": status.stdout,
        }
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree_dir)], check=False)
        if worktree_dir.exists():
            shutil.rmtree(worktree_dir)


def build_commit_message(*, page_number: int, case_id: str, changed_files: list[str]) -> str:
    return (
        f"pdf-lab: fix page {page_number} second-pass defect\n\n"
        f"PDF-Lab-Case: {case_id}\n"
        "Reviewed-By: pdf-lab-second-pass-harness\n"
        "Persona-Role: deterministic-pdf-extraction-validator\n"
        f"Issue-Codes: pdf-lab-second-pass,page-{page_number}\n"
        f"Changed-Files: {','.join(changed_files)}"
    )


def validate_commit_gate_acceptance(commit_gate: dict[str, Any] | None) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(commit_gate, dict):
        errors.append("missing commit_gate")
        commit_gate = {}
    revertability = commit_gate.get("revertability_check")
    if commit_gate.get("schema") != "pdf_lab.second_pass.commit_gate.v1":
        errors.append("commit_gate schema mismatch")
    if commit_gate.get("ok") is not True:
        errors.append("commit_gate ok is not true")
    commit_sha = commit_gate.get("commit_sha")
    if not isinstance(commit_sha, str) or not commit_sha:
        errors.append("commit_gate commit_sha must be a non-empty string")
    if commit_gate.get("exact_file_match") is not True:
        errors.append("commit_gate exact_file_match is not true")
    changed_files = commit_gate.get("changed_files")
    committed_files = commit_gate.get("committed_files")
    if not isinstance(changed_files, list) or not all(isinstance(path, str) and path for path in changed_files):
        errors.append("commit_gate changed_files must be a non-empty list of strings")
        changed_files = []
    if not isinstance(committed_files, list) or not all(isinstance(path, str) and path for path in committed_files):
        errors.append("commit_gate committed_files must be a non-empty list of strings")
        committed_files = []
    if changed_files and committed_files and sorted(changed_files) != sorted(committed_files):
        errors.append("commit_gate changed_files do not match committed_files")
    if not isinstance(revertability, dict) or revertability.get("ok") is not True:
        errors.append("commit_gate revertability_check ok is not true")
    elif revertability.get("schema") != "pdf_lab.second_pass.revertability_check.v1":
        errors.append("commit_gate revertability_check schema mismatch")
    elif revertability.get("commit_sha") != commit_gate.get("commit_sha"):
        errors.append("commit_gate revertability_check commit_sha does not match commit_gate commit_sha")
    return {
        "schema": "pdf_lab.second_pass.commit_acceptance_gate.v1",
        "ok": not errors,
        "errors": errors,
        "commit_sha": commit_gate.get("commit_sha"),
        "commit_gate_ok": commit_gate.get("ok"),
        "exact_file_match": commit_gate.get("exact_file_match"),
        "revertability_ok": revertability.get("ok") if isinstance(revertability, dict) else None,
    }


def create_patch_commit(
    *,
    commit_mode: str,
    changed_files: list[str],
    message: str,
    repo: Path = REPO,
) -> dict[str, Any]:
    if commit_mode == "dry_run":
        return {
            "schema": "pdf_lab.second_pass.commit_gate.v1",
            "ok": False,
            "mode": "dry_run",
            "errors": ["commit_dry_run"],
            "commit_sha": None,
            "changed_files": changed_files,
            "preexisting_staged_files": [],
            "committed_files": [],
            "exact_file_match": False,
            "revertability_check": None,
        }
    if not changed_files:
        return {
            "schema": "pdf_lab.second_pass.commit_gate.v1",
            "ok": False,
            "mode": commit_mode,
            "errors": ["no_changed_files_to_commit"],
            "commit_sha": None,
            "changed_files": changed_files,
            "preexisting_staged_files": git_staged_files(repo),
            "committed_files": [],
            "exact_file_match": False,
            "revertability_check": None,
        }
    preexisting_staged = git_staged_files(repo)
    if preexisting_staged:
        return {
            "schema": "pdf_lab.second_pass.commit_gate.v1",
            "ok": False,
            "mode": commit_mode,
            "errors": [f"preexisting staged files would break one-fix commit isolation: {preexisting_staged}"],
            "commit_sha": None,
            "changed_files": changed_files,
            "preexisting_staged_files": preexisting_staged,
            "committed_files": [],
            "exact_file_match": False,
            "revertability_check": None,
        }
    changed_files_under_paths = git_changed_files_for_paths(repo, changed_files)
    exact_pre_stage_file_match = changed_files_under_paths == sorted(changed_files)
    if not exact_pre_stage_file_match:
        return {
            "schema": "pdf_lab.second_pass.commit_gate.v1",
            "ok": False,
            "mode": commit_mode,
            "errors": [f"changed files under requested paths did not match isolated patch delta before staging: {changed_files_under_paths}"],
            "commit_sha": None,
            "changed_files": changed_files,
            "changed_files_under_paths": changed_files_under_paths,
            "preexisting_staged_files": preexisting_staged,
            "staged_files_after_add": [],
            "committed_files": [],
            "exact_file_match": False,
            "revertability_check": None,
        }
    subprocess.run(["git", "-C", str(repo), "add", "--", *changed_files], check=True)
    staged_after_add = git_staged_files(repo)
    exact_staged_file_match = staged_after_add == sorted(changed_files)
    if not exact_staged_file_match:
        cleanup = git_unstage_files(repo, changed_files)
        return {
            "schema": "pdf_lab.second_pass.commit_gate.v1",
            "ok": False,
            "mode": commit_mode,
            "errors": [f"staged files did not match isolated patch delta before commit: {staged_after_add}"],
            "commit_sha": None,
            "changed_files": changed_files,
            "changed_files_under_paths": changed_files_under_paths,
            "preexisting_staged_files": preexisting_staged,
            "staged_files_after_add": staged_after_add,
            "committed_files": [],
            "exact_file_match": False,
            "index_cleanup": cleanup,
            "revertability_check": None,
        }
    commit = subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", message],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if commit.returncode != 0:
        cleanup = git_unstage_files(repo, changed_files)
        return {
            "schema": "pdf_lab.second_pass.commit_gate.v1",
            "ok": False,
            "mode": commit_mode,
            "errors": [commit.stderr.strip() or commit.stdout.strip() or "git commit failed"],
            "commit_sha": None,
            "changed_files": changed_files,
            "changed_files_under_paths": changed_files_under_paths,
            "preexisting_staged_files": preexisting_staged,
            "staged_files_after_add": staged_after_add,
            "committed_files": [],
            "exact_file_match": False,
            "index_cleanup": cleanup,
            "revertability_check": None,
        }
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    committed_files = git_commit_files(sha, repo)
    exact_file_match = committed_files == sorted(changed_files)
    revertability = verify_commit_revertability(sha, repo) if exact_file_match else None
    ok = exact_file_match and bool(revertability and revertability["ok"])
    errors = []
    if not exact_file_match:
        errors.append(f"committed files did not match isolated patch delta: {committed_files}")
    if exact_file_match and (not revertability or not revertability["ok"]):
        errors.append("commit revertability check failed")
    return {
        "schema": "pdf_lab.second_pass.commit_gate.v1",
        "ok": ok,
        "mode": commit_mode,
        "errors": errors,
        "commit_sha": sha,
        "changed_files": changed_files,
        "changed_files_under_paths": changed_files_under_paths,
        "preexisting_staged_files": preexisting_staged,
        "staged_files_after_add": staged_after_add,
        "committed_files": committed_files,
        "exact_file_match": exact_file_match,
        "revertability_check": revertability,
    }


def package_bundle(case_dir: Path, out: Path) -> None:
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(case_dir.rglob("*")):
            if path.is_file() and path != out:
                bundle.write(path, str(path.relative_to(case_dir)))


def validate_page_review_bundle(case_dir: Path, zip_path: Path, terminal: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    minimum_bundle_artifacts = BLOCKED_PAGE_REVIEW_BUNDLE_ARTIFACTS_BY_REASON.get(
        str(terminal.get("reason")),
        MINIMUM_PAGE_REVIEW_BUNDLE_ARTIFACTS,
    )
    evidence_artifacts = terminal.get("evidence_artifacts")
    invalid_evidence_artifacts: list[Any] = []
    if not isinstance(evidence_artifacts, list):
        errors.append("terminal evidence_artifacts must be a list of artifact names")
        evidence_artifacts = []
    else:
        invalid_evidence_artifacts = [
            artifact
            for artifact in evidence_artifacts
            if not isinstance(artifact, str) or not artifact
        ]
        if invalid_evidence_artifacts:
            errors.append(f"terminal evidence_artifacts contains invalid artifact names: {invalid_evidence_artifacts}")
    unsafe_evidence_artifacts = sorted(
        artifact
        for artifact in evidence_artifacts
        if isinstance(artifact, str) and artifact and (Path(artifact).is_absolute() or ".." in Path(artifact).parts)
    )
    duplicate_evidence_artifacts = sorted(
        artifact
        for artifact, count in Counter(
            artifact for artifact in evidence_artifacts if isinstance(artifact, str) and artifact
        ).items()
        if count > 1
    )
    required_zip_entries = sorted(
        {
            *minimum_bundle_artifacts,
            *[
                artifact
                for artifact in evidence_artifacts
                if isinstance(artifact, str)
                and artifact
                and artifact not in unsafe_evidence_artifacts
            ],
        }
    )
    missing_artifacts = sorted(
        artifact for artifact in required_zip_entries if not (case_dir / artifact).is_file()
    )
    zip_entries: list[str] = []
    duplicate_zip_entries: list[str] = []
    mismatched_zip_entries: list[str] = []
    unsafe_zip_entries: list[str] = []
    terminal_ledger_matches_argument = False
    terminal_ledger_validation_matches_recomputed = False
    terminal_ledger_validation_ok = False
    if duplicate_evidence_artifacts:
        errors.append(f"terminal evidence_artifacts contains duplicate artifact names: {duplicate_evidence_artifacts}")
    terminal_ledger_path = case_dir / "terminal_ledger.json"
    if terminal_ledger_path.is_file():
        terminal_ledger_payload, read_errors = read_required_json_object(
            terminal_ledger_path,
            "terminal_ledger.json",
        )
        errors.extend(read_errors)
        if terminal_ledger_payload:
            if terminal_ledger_payload == terminal:
                terminal_ledger_matches_argument = True
            else:
                errors.append("terminal_ledger.json does not match terminal argument")
    terminal_ledger_validation_path = case_dir / "terminal_ledger_validation.json"
    if terminal_ledger_matches_argument and terminal_ledger_validation_path.is_file():
        terminal_ledger_validation_payload, read_errors = read_required_json_object(
            terminal_ledger_validation_path,
            "terminal_ledger_validation.json",
        )
        errors.extend(read_errors)
        if terminal_ledger_validation_payload:
            recomputed_terminal_validation = validate_page_terminal_ledger(case_dir, terminal)
            if terminal_ledger_validation_payload == recomputed_terminal_validation:
                terminal_ledger_validation_matches_recomputed = True
                terminal_ledger_validation_ok = recomputed_terminal_validation.get("ok") is True
            else:
                errors.append("terminal_ledger_validation.json does not match recomputed terminal validation")
            if recomputed_terminal_validation.get("ok") is not True:
                errors.append("terminal_ledger_validation ok is not true")
    if unsafe_evidence_artifacts:
        errors.append(f"terminal evidence_artifacts contains unsafe bundle paths: {unsafe_evidence_artifacts}")
    if not zip_path.is_file():
        errors.append("review bundle zip is missing")
    else:
        with zipfile.ZipFile(zip_path) as bundle:
            zip_entries = bundle.namelist()
            unsafe_zip_entries = sorted(
                entry
                for entry in zip_entries
                if Path(entry).is_absolute() or ".." in Path(entry).parts
            )
            for artifact in required_zip_entries:
                source = case_dir / artifact
                if artifact in zip_entries and source.is_file():
                    if bundle.read(artifact) != source.read_bytes():
                        mismatched_zip_entries.append(artifact)
        entry_counts = Counter(zip_entries)
        duplicate_zip_entries = sorted(entry for entry, count in entry_counts.items() if count > 1)
        if duplicate_zip_entries:
            errors.append(f"duplicate zip entries: {duplicate_zip_entries}")
        if unsafe_zip_entries:
            errors.append(f"unsafe zip entries: {unsafe_zip_entries}")
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
        and not unsafe_zip_entries
        and not mismatched_zip_entries
    )
    return {
        "schema": "pdf_lab.second_pass.page_review_bundle_validation.v1",
        "ok": not errors,
        "errors": errors,
        "case_id": terminal.get("case_id"),
        "page_number": terminal.get("page_number"),
        "terminal_status": terminal.get("terminal_status"),
        "zip_path": str(zip_path),
        "required_zip_entries": required_zip_entries,
        "zip_entry_count": len(zip_entries),
        "zip_content_ok": zip_content_ok,
        "terminal_ledger_matches_argument": terminal_ledger_matches_argument,
        "terminal_ledger_validation_matches_recomputed": terminal_ledger_validation_matches_recomputed,
        "terminal_ledger_validation_ok": terminal_ledger_validation_ok,
        "missing_artifacts": missing_artifacts,
        "missing_expected_zip_entries": missing_expected_zip_entries,
        "duplicate_zip_entries": duplicate_zip_entries,
        "duplicate_evidence_artifacts": duplicate_evidence_artifacts,
        "invalid_evidence_artifacts": invalid_evidence_artifacts,
        "unsafe_evidence_artifacts": unsafe_evidence_artifacts,
        "unsafe_zip_entries": unsafe_zip_entries,
        "mismatched_zip_entries": sorted(mismatched_zip_entries),
    }


def validate_page_terminal_ledger(case_dir: Path, terminal: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []

    def read_required_json_artifact(artifact: str) -> dict[str, Any]:
        path = case_dir / artifact
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - malformed evidence is a validation failure.
            errors.append(f"{artifact} unreadable: {type(exc).__name__}: {exc}")
            return {}
        if not isinstance(payload, dict):
            errors.append(f"{artifact} must contain a JSON object")
            return {}
        return payload

    def validate_candidate_count(
        payload: dict[str, Any],
        expected_count: int,
        label: str,
        *,
        expected_label: str = "candidates",
    ) -> None:
        candidate_count = payload.get("candidate_count")
        if type(candidate_count) is not int or candidate_count < 0:
            errors.append(f"{label} candidate_count must be a non-negative integer")
        elif candidate_count != expected_count:
            errors.append(f"{label} candidate_count does not match {expected_label}")

    if terminal.get("schema") != "pdf_lab.second_pass.page_terminal_ledger.v1":
        errors.append("terminal ledger schema mismatch")
    terminal_status = terminal.get("terminal_status")
    terminal_reason = terminal.get("reason")
    if terminal_status not in TERMINAL_STATUSES:
        errors.append(f"invalid terminal_status: {terminal_status}")
    if not terminal.get("case_id"):
        errors.append("missing case_id")
    if type(terminal.get("page_number")) is not int:
        errors.append("missing integer page_number")
    else:
        terminal_identity = validate_page_case_identity(
            {
                "case_id": terminal.get("case_id"),
                "page_number": terminal.get("page_number"),
            },
        )
        if terminal_identity["ok"] is not True:
            errors.extend(f"terminal ledger {error}" for error in terminal_identity["errors"])
    if not isinstance(terminal.get("reason"), str) or not terminal.get("reason", "").strip():
        errors.append("missing terminal reason")
    evidence_artifacts = terminal.get("evidence_artifacts")
    if not isinstance(evidence_artifacts, list) or not all(isinstance(item, str) and item for item in evidence_artifacts):
        errors.append("evidence_artifacts must be a list of artifact names")
        evidence_artifacts = []
    duplicate_evidence_artifacts = sorted(
        artifact
        for artifact, count in Counter(evidence_artifacts).items()
        if count > 1
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

    state_artifact = read_required_json_artifact("state.json") if "state.json" in evidence_artifacts else {}
    if state_artifact:
        if state_artifact.get("case_id") != terminal.get("case_id"):
            errors.append("state.json case_id does not match terminal ledger")
        if state_artifact.get("page_number") != terminal.get("page_number"):
            errors.append("state.json page_number does not match terminal ledger")
    sampled_manifest = (
        read_required_json_artifact("sampled_candidate_manifest.json")
        if "sampled_candidate_manifest.json" in evidence_artifacts
        else {}
    )
    selected_candidates_artifact = (
        read_required_json_artifact("selected_candidates.json")
        if "selected_candidates.json" in evidence_artifacts
        else {}
    )
    candidate_presets_artifact = (
        read_required_json_artifact("candidate_presets.json")
        if "candidate_presets.json" in evidence_artifacts or (case_dir / "candidate_presets.json").is_file()
        else {}
    )
    selected_candidate_ids_from_artifact: list[str] = []
    if selected_candidates_artifact:
        if selected_candidates_artifact.get("schema") != SELECTED_CANDIDATES_SCHEMA:
            errors.append("selected_candidates schema mismatch")
        selected_page_case = selected_candidates_artifact.get("page_case")
        if not isinstance(selected_page_case, dict):
            errors.append("selected_candidates page_case must be an object")
            selected_page_case = {}
        if selected_page_case.get("case_id") != terminal.get("case_id"):
            errors.append("selected_candidates page_case.case_id does not match terminal ledger")
        if selected_page_case.get("page_number") != terminal.get("page_number"):
            errors.append("selected_candidates page_case.page_number does not match terminal ledger")
        selected_candidates = selected_candidates_artifact.get("candidates")
        if not isinstance(selected_candidates, list):
            errors.append("selected_candidates candidates is not a list")
            selected_candidates = []
        selected_candidate_ids_from_artifact = sorted(
            candidate["candidate_id"]
            for candidate in selected_candidates
            if isinstance(candidate, dict) and isinstance(candidate.get("candidate_id"), str)
        )
        duplicate_selected_candidate_ids = sorted(
            candidate_id
            for candidate_id, count in Counter(selected_candidate_ids_from_artifact).items()
            if count > 1
        )
        if duplicate_selected_candidate_ids:
            errors.append(f"selected_candidates candidate_ids contain duplicates: {duplicate_selected_candidate_ids}")
        if len(selected_candidate_ids_from_artifact) != len(selected_candidates):
            errors.append("selected_candidates candidates contain missing candidate_id")
        validate_candidate_count(selected_candidates_artifact, len(selected_candidates), "selected_candidates")
    if candidate_presets_artifact:
        if candidate_presets_artifact.get("schema") != CANDIDATE_PRESETS_SCHEMA:
            errors.append("candidate_presets schema mismatch")
        preset_page_case = candidate_presets_artifact.get("page_case")
        if not isinstance(preset_page_case, dict):
            errors.append("candidate_presets page_case must be an object")
            preset_page_case = {}
        if preset_page_case.get("case_id") != terminal.get("case_id"):
            errors.append("candidate_presets page_case.case_id does not match terminal ledger")
        if preset_page_case.get("page_number") != terminal.get("page_number"):
            errors.append("candidate_presets page_case.page_number does not match terminal ledger")
        preset_candidates = candidate_presets_artifact.get("candidates")
        if not isinstance(preset_candidates, list):
            errors.append("candidate_presets candidates is not a list")
            preset_candidates = []
        preset_candidate_ids = sorted(
            candidate["candidate_id"]
            for candidate in preset_candidates
            if isinstance(candidate, dict) and isinstance(candidate.get("candidate_id"), str)
        )
        duplicate_preset_candidate_ids = sorted(
            candidate_id
            for candidate_id, count in Counter(preset_candidate_ids).items()
            if count > 1
        )
        if duplicate_preset_candidate_ids:
            errors.append(f"candidate_presets candidate_ids contain duplicates: {duplicate_preset_candidate_ids}")
        if len(preset_candidate_ids) != len(preset_candidates):
            errors.append("candidate_presets candidates contain missing candidate_id")
        validate_candidate_count(candidate_presets_artifact, len(preset_candidates), "candidate_presets")
        if selected_candidate_ids_from_artifact and preset_candidate_ids != selected_candidate_ids_from_artifact:
            errors.append("candidate_presets candidate_ids do not match selected_candidates")
    manifest_candidate_ids: list[str] = []
    if sampled_manifest:
        if sampled_manifest.get("schema") != SAMPLED_CANDIDATE_MANIFEST_SCHEMA:
            errors.append("sampled_candidate_manifest schema mismatch")
        page_case = sampled_manifest.get("page_case")
        if not isinstance(page_case, dict):
            errors.append("sampled_candidate_manifest page_case must be an object")
            page_case = {}
        if page_case.get("case_id") != terminal.get("case_id"):
            errors.append("sampled_candidate_manifest page_case.case_id does not match terminal ledger")
        if page_case.get("page_number") != terminal.get("page_number"):
            errors.append("sampled_candidate_manifest page_case.page_number does not match terminal ledger")
        manifest_candidates = sampled_manifest.get("candidates")
        if not isinstance(manifest_candidates, list):
            errors.append("sampled_candidate_manifest candidates must be a list")
            manifest_candidates = []
        manifest_candidate_ids = sorted(
            candidate["candidate_id"]
            for candidate in manifest_candidates
            if isinstance(candidate, dict) and isinstance(candidate.get("candidate_id"), str)
        )
        duplicate_manifest_candidate_ids = sorted(
            candidate_id
            for candidate_id, count in Counter(manifest_candidate_ids).items()
            if count > 1
        )
        if duplicate_manifest_candidate_ids:
            errors.append(f"sampled_candidate_manifest candidate_ids contain duplicates: {duplicate_manifest_candidate_ids}")
        if len(manifest_candidate_ids) != len(manifest_candidates):
            errors.append("sampled_candidate_manifest candidates contain missing candidate_id")
        validate_candidate_count(sampled_manifest, len(manifest_candidates), "sampled_candidate_manifest")
        page_case_candidate_ids = page_case.get("candidate_ids")
        if not isinstance(page_case_candidate_ids, list) or not all(
            isinstance(candidate_id, str) for candidate_id in page_case_candidate_ids
        ):
            errors.append("sampled_candidate_manifest page_case.candidate_ids must be a list of strings")
        else:
            duplicate_page_case_candidate_ids = sorted(
                candidate_id
                for candidate_id, count in Counter(page_case_candidate_ids).items()
                if count > 1
            )
            if duplicate_page_case_candidate_ids:
                errors.append(
                    "sampled_candidate_manifest page_case.candidate_ids contain duplicates: "
                    f"{duplicate_page_case_candidate_ids}"
                )
            if sorted(page_case_candidate_ids) != manifest_candidate_ids:
                errors.append("sampled_candidate_manifest page_case.candidate_ids do not match candidates")
    if sampled_manifest and selected_candidates_artifact:
        if manifest_candidate_ids != selected_candidate_ids_from_artifact:
            errors.append("selected_candidates candidate_ids do not match sampled_candidate_manifest")

    def validate_preflight_artifact(artifact: str, expected_surfaces: set[str]) -> dict[str, Any]:
        preflight = read_required_json_artifact(artifact)
        if not preflight:
            return {}
        if preflight.get("schema") != "pdf_lab.second_pass.scillm_preflight.v1":
            errors.append(f"{artifact} schema mismatch")
        surface = preflight.get("surface")
        if surface not in expected_surfaces:
            errors.append(f"{artifact} surface does not match expected scillm surface")
        if not isinstance(preflight.get("caller_skill"), str) or not preflight.get("caller_skill", "").strip():
            errors.append(f"{artifact} caller_skill must be non-empty")
        elif preflight.get("caller_skill") != "pdf-lab":
            errors.append(f"{artifact} caller_skill must be pdf-lab")
        if not isinstance(preflight.get("checks"), list):
            errors.append(f"{artifact} checks must be a list")
        preflight_errors = preflight.get("errors")
        if not isinstance(preflight_errors, list):
            errors.append(f"{artifact} errors must be a list")
            preflight_errors = []
        if preflight.get("ok") is True and preflight_errors:
            errors.append(f"{artifact} ok true requires empty errors")
        if preflight.get("ok") is False and not preflight_errors:
            errors.append(f"{artifact} ok false requires non-empty errors")
        if not is_plain_bool(preflight.get("ok")):
            errors.append(f"{artifact} ok must be boolean")
        checks = preflight.get("checks") if isinstance(preflight.get("checks"), list) else []
        if preflight.get("ok") is True:
            def successful_check_matches(
                path: str,
                *,
                method: str | None = None,
                payload_matches: Callable[[dict[str, Any]], bool] | None = None,
            ) -> bool:
                return any(
                    isinstance(check, dict)
                    and check.get("path") == path
                    and (method is None or check.get("method") == method)
                    and check.get("http_status") == 200
                    and isinstance(check.get("payload"), dict)
                    and (payload_matches is None or payload_matches(check["payload"]))
                    for check in checks
                )

            def opencode_health_payload_matches(payload: dict[str, Any]) -> bool:
                return payload.get("status") in {"ok", "healthy", "enabled"} or payload.get("opencode_serve") is True

            def transport_capabilities_payload_matches(payload: dict[str, Any]) -> bool:
                return (
                    payload.get("transport_api") is True
                    and payload.get("event_stream") == "sse_with_reasoning"
                    and payload.get("child_sessions") is True
                )

            if not any(
                isinstance(check, dict)
                and check.get("path") == "/health/liveliness"
                and check.get("http_status") == 200
                and isinstance(check.get("payload"), dict)
                and check["payload"].get("status") == "ok"
                for check in checks
            ):
                errors.append(f"{artifact} ok true requires successful /health/liveliness check")
            if not any(
                isinstance(check, dict)
                and check.get("path") == "/v1/chat/completions"
                and check.get("method") == "POST"
                and check.get("include_caller_skill") is False
                and check.get("http_status") == 400
                and "caller_skill_required" in json.dumps(check.get("payload"), sort_keys=True)
                for check in checks
            ):
                errors.append(f"{artifact} ok true requires missing-caller caller_skill_required check")
            if surface in {"chat", "opencode_serve"} and not successful_check_matches(
                "/v1/scillm/health",
                payload_matches=lambda payload: payload.get("status") == "ok",
            ):
                errors.append(f"{artifact} ok true requires /v1/scillm/health check")
            if surface == "opencode_serve" and not successful_check_matches(
                "/v1/scillm/opencode/health",
                payload_matches=opencode_health_payload_matches,
            ):
                errors.append(f"{artifact} ok true requires /v1/scillm/opencode/health check")
            if surface == "opencode_transport" and not successful_check_matches(
                "/v1/scillm/opencode/transport/capabilities",
                payload_matches=transport_capabilities_payload_matches,
            ):
                errors.append(f"{artifact} ok true requires /v1/scillm/opencode/transport/capabilities check")
        return preflight

    if "scillm_review_preflight.json" in evidence_artifacts:
        validate_preflight_artifact("scillm_review_preflight.json", {"chat"})
    if "scillm_patch_preflight.json" in evidence_artifacts:
        expected_patch_surfaces = {"opencode_serve", "opencode_transport"}
        patch_request_for_preflight = read_required_json_artifact("patch_request.json") if "patch_request.json" in evidence_artifacts else {}
        diagnosis_request_for_preflight = (
            read_required_json_artifact("repair_diagnosis_request.json")
            if "repair_diagnosis_request.json" in evidence_artifacts
            else {}
        )
        preflight_request = patch_request_for_preflight or diagnosis_request_for_preflight
        endpoint = preflight_request.get("endpoint") if isinstance(preflight_request, dict) else None
        if endpoint == "POST /v1/scillm/opencode/runs":
            expected_patch_surfaces = {"opencode_serve"}
        elif endpoint == "POST /v1/scillm/opencode/transport/runs + children + message":
            expected_patch_surfaces = {"opencode_transport"}
        validate_preflight_artifact("scillm_patch_preflight.json", expected_patch_surfaces)

    page_orchestrator_dag_spec = (
        read_required_json_artifact("scillm_orchestrator_page_dag_spec.json")
        if "scillm_orchestrator_page_dag_spec.json" in evidence_artifacts
        else {}
    )
    page_orchestrator_dag_spec_validation = (
        read_required_json_artifact("scillm_orchestrator_page_dag_spec_validation.json")
        if "scillm_orchestrator_page_dag_spec_validation.json" in evidence_artifacts
        else {}
    )
    if page_orchestrator_dag_spec_validation:
        if page_orchestrator_dag_spec_validation.get("schema") != "pdf_lab.second_pass.page_orchestrator_dag_spec_validation.v1":
            errors.append("scillm_orchestrator_page_dag_spec_validation schema mismatch")
        if page_orchestrator_dag_spec_validation.get("case_id") != terminal.get("case_id"):
            errors.append("scillm_orchestrator_page_dag_spec_validation case_id does not match terminal ledger")
        if page_orchestrator_dag_spec_validation.get("page_number") != terminal.get("page_number"):
            errors.append("scillm_orchestrator_page_dag_spec_validation page_number does not match terminal ledger")
        if page_orchestrator_dag_spec:
            recomputed_page_orchestrator_dag_spec_validation = validate_page_orchestrator_dag_spec(page_orchestrator_dag_spec)
            if page_orchestrator_dag_spec_validation != recomputed_page_orchestrator_dag_spec_validation:
                errors.append("scillm_orchestrator_page_dag_spec_validation does not match recomputed page_orchestrator_dag_spec contract")

    page_orchestrator_submission = (
        read_required_json_artifact("scillm_orchestrator_page_submission.json")
        if "scillm_orchestrator_page_submission.json" in evidence_artifacts
        else {}
    )
    page_orchestrator_submission_validation = (
        read_required_json_artifact("scillm_orchestrator_page_submission_validation.json")
        if "scillm_orchestrator_page_submission_validation.json" in evidence_artifacts
        else {}
    )
    if page_orchestrator_submission_validation:
        if page_orchestrator_submission_validation.get("schema") != "pdf_lab.second_pass.scillm_orchestrator_page_submission_validation.v1":
            errors.append("scillm_orchestrator_page_submission_validation schema mismatch")
        if page_orchestrator_submission_validation.get("case_id") != terminal.get("case_id"):
            errors.append("scillm_orchestrator_page_submission_validation case_id does not match terminal ledger")
        if page_orchestrator_submission_validation.get("page_number") != terminal.get("page_number"):
            errors.append("scillm_orchestrator_page_submission_validation page_number does not match terminal ledger")
        if page_orchestrator_submission and page_orchestrator_dag_spec:
            recomputed_page_orchestrator_submission_validation = validate_page_orchestrator_submission(
                page_orchestrator_submission,
                dag_spec=page_orchestrator_dag_spec,
            )
            if page_orchestrator_submission_validation != recomputed_page_orchestrator_submission_validation:
                errors.append("scillm_orchestrator_page_submission_validation does not match recomputed page_orchestrator_submission contract")

    page_orchestrator_run_request = (
        read_required_json_artifact("scillm_page_orchestrator_run_request.json")
        if "scillm_page_orchestrator_run_request.json" in evidence_artifacts
        else {}
    )
    page_orchestrator_run_validation = (
        read_required_json_artifact("scillm_page_orchestrator_run_validation.json")
        if "scillm_page_orchestrator_run_validation.json" in evidence_artifacts
        else {}
    )
    page_orchestrator_run_receipt = (
        read_required_json_artifact("scillm_page_orchestrator_run_receipt.json")
        if "scillm_page_orchestrator_run_receipt.json" in evidence_artifacts
        or (case_dir / "scillm_page_orchestrator_run_receipt.json").is_file()
        else {}
    )
    if page_orchestrator_run_validation:
        if page_orchestrator_run_validation.get("schema") != "pdf_lab.second_pass.page_orchestrator_run_validation.v1":
            errors.append("scillm_page_orchestrator_run_validation schema mismatch")
        if page_orchestrator_run_validation.get("case_id") != terminal.get("case_id"):
            errors.append("scillm_page_orchestrator_run_validation case_id does not match terminal ledger")
        if page_orchestrator_run_validation.get("page_number") != terminal.get("page_number"):
            errors.append("scillm_page_orchestrator_run_validation page_number does not match terminal ledger")
        if page_orchestrator_run_request:
            recomputed_page_orchestrator_run_validation = validate_page_orchestrator_run_receipt(
                page_orchestrator_run_receipt or None,
                mode=str(page_orchestrator_run_validation.get("mode") or "dry_run"),
                request=page_orchestrator_run_request
                if page_orchestrator_run_request.get("schema") == "pdf_lab.second_pass.page_orchestrator_run_request.v1"
                else None,
            )
            if page_orchestrator_run_validation != recomputed_page_orchestrator_run_validation:
                errors.append("scillm_page_orchestrator_run_validation does not match recomputed page_orchestrator_run contract")

    patch_evidence_workspace_artifact = (
        read_required_json_artifact("patch_evidence_workspace.json")
        if "patch_evidence_workspace.json" in evidence_artifacts
        else {}
    )
    patch_baseline_artifact = read_required_json_artifact("patch_baseline.json") if "patch_baseline.json" in evidence_artifacts else {}
    if patch_evidence_workspace_artifact:
        if patch_evidence_workspace_artifact.get("schema") != "pdf_lab.second_pass.patch_evidence_workspace.v1":
            errors.append("patch_evidence_workspace schema mismatch")
        if patch_evidence_workspace_artifact.get("case_id") != terminal.get("case_id"):
            errors.append("patch_evidence_workspace case_id does not match terminal ledger")
        if not is_plain_bool(patch_evidence_workspace_artifact.get("ok")):
            errors.append("patch_evidence_workspace ok must be boolean")
        copied = patch_evidence_workspace_artifact.get("copied")
        missing = patch_evidence_workspace_artifact.get("missing")
        if not isinstance(copied, list):
            errors.append("patch_evidence_workspace copied must be a list")
            copied = []
        if not isinstance(missing, list) or not all(isinstance(item, str) for item in missing):
            errors.append("patch_evidence_workspace missing must be a list of strings")
            missing = []
        copied_artifacts = sorted(
            item.get("artifact")
            for item in copied
            if isinstance(item, dict) and isinstance(item.get("artifact"), str)
        )
        if len(copied_artifacts) != len(copied):
            errors.append("patch_evidence_workspace copied entries must include artifact names")
        workspace_artifacts = sorted(copied_artifacts + list(missing))
        if workspace_artifacts != sorted(PATCH_EVIDENCE_WORKSPACE_FILES):
            errors.append("patch_evidence_workspace copied and missing artifacts do not match required workspace files")
        if patch_evidence_workspace_artifact.get("ok") is True and missing:
            errors.append("patch_evidence_workspace ok true requires empty missing list")
        if patch_evidence_workspace_artifact.get("ok") is False and not missing:
            errors.append("patch_evidence_workspace ok false requires non-empty missing list")
    if patch_baseline_artifact:
        if patch_baseline_artifact.get("schema") != "pdf_lab.second_pass.patch_baseline.v1":
            errors.append("patch_baseline schema mismatch")
        changed_files = patch_baseline_artifact.get("changed_files")
        if not isinstance(changed_files, list) or not all(isinstance(path, str) for path in changed_files):
            errors.append("patch_baseline changed_files must be a list of strings")
            changed_files = []
        if not is_plain_bool(patch_baseline_artifact.get("dirty")):
            errors.append("patch_baseline dirty must be boolean")
        elif patch_baseline_artifact.get("dirty") != bool(changed_files):
            errors.append("patch_baseline dirty does not match changed_files")
        embedded_workspace = patch_baseline_artifact.get("patch_evidence_workspace")
        if not isinstance(embedded_workspace, dict):
            errors.append("patch_baseline patch_evidence_workspace must be an object")
        elif patch_evidence_workspace_artifact and embedded_workspace != patch_evidence_workspace_artifact:
            errors.append("patch_baseline patch_evidence_workspace does not match patch_evidence_workspace artifact")

    def validate_substrate_error_artifact(
        artifact: str,
        *,
        expected_node_ids: set[str] | None = None,
        expected_endpoints: set[str] | None = None,
    ) -> dict[str, Any]:
        payload = read_required_json_artifact(artifact) if artifact in evidence_artifacts else {}
        if not payload:
            return {}
        label = artifact.removesuffix(".json")
        if payload.get("schema") != "pdf_lab.second_pass.substrate_error.v1":
            errors.append(f"{label} schema mismatch")
        if expected_node_ids is not None and payload.get("node_id") not in expected_node_ids:
            errors.append(f"{label} node_id mismatch")
        if expected_endpoints is not None and payload.get("endpoint") not in expected_endpoints:
            errors.append(f"{label} endpoint mismatch")
        if payload.get("case_id") != terminal.get("case_id"):
            errors.append(f"{label} case_id does not match terminal ledger")
        if payload.get("page_number") != terminal.get("page_number"):
            errors.append(f"{label} page_number does not match terminal ledger")
        if (
            not isinstance(payload.get("error_type"), str)
            or not payload.get("error_type", "").strip()
        ):
            errors.append(f"{label} error_type must be non-empty")
        if (
            not isinstance(payload.get("error"), str)
            or not payload.get("error", "").strip()
        ):
            errors.append(f"{label} error must be non-empty")
        return payload

    page_extraction_error_artifact = validate_substrate_error_artifact(
        "page_extraction_error.json",
        expected_node_ids={"extract_page_json"},
        expected_endpoints={"snapshot_current_extraction._extract_page"},
    )
    if page_extraction_error_artifact:
        if terminal_reason == "page_extraction_failed" and terminal_status != "blocked_substrate":
            errors.append("page_extraction_failed terminal ledger must be blocked_substrate")
    elif terminal_reason == "page_extraction_failed":
        errors.append("page_extraction_failed terminal ledger requires page_extraction_error.json")

    page_dag_setup_error_artifact = validate_substrate_error_artifact(
        "page_dag_setup_error.json",
        expected_node_ids={"load_cli_inputs"},
        expected_endpoints={"run_page_second_pass_dag.main"},
    )
    if page_dag_setup_error_artifact:
        if terminal_reason == "page_dag_setup_failed" and terminal_status != "blocked_substrate":
            errors.append("page_dag_setup_failed terminal ledger must be blocked_substrate")
    elif terminal_reason == "page_dag_setup_failed":
        errors.append("page_dag_setup_failed terminal ledger requires page_dag_setup_error.json")

    scillm_review_error_artifact = validate_substrate_error_artifact(
        "scillm_review_error.json",
        expected_node_ids={"scillm_one_shot_page_review"},
        expected_endpoints={"POST /v1/chat/completions", "fixture:review_response"},
    )
    if terminal_reason == "scillm_review_call_failed" and not scillm_review_error_artifact:
        errors.append("scillm_review_call_failed terminal ledger requires scillm_review_error.json")
    if terminal_reason == "review_fixture_load_failed" and not scillm_review_error_artifact:
        errors.append("review_fixture_load_failed terminal ledger requires scillm_review_error.json")

    repair_plan_error_artifact = validate_substrate_error_artifact(
        "repair_plan_error.json",
        expected_endpoints={"prepare:POST /v1/chat/completions", "POST /v1/chat/completions"},
    )
    if terminal_reason == "repair_plan_call_failed" and not repair_plan_error_artifact:
        errors.append("repair_plan_call_failed terminal ledger requires repair_plan_error.json")
    if terminal_reason == "repair_plan_failed" and not repair_plan_error_artifact:
        errors.append("repair_plan_failed terminal ledger requires repair_plan_error.json")

    repair_diagnosis_error_artifact = validate_substrate_error_artifact("repair_diagnosis_error.json")
    if terminal_reason == "repair_diagnosis_call_failed" and not repair_diagnosis_error_artifact:
        errors.append("repair_diagnosis_call_failed terminal ledger requires repair_diagnosis_error.json")

    patch_error_artifact = validate_substrate_error_artifact("patch_error.json")
    if terminal_reason == "patch_delegate_call_failed" and not patch_error_artifact:
        errors.append("patch_delegate_call_failed terminal ledger requires patch_error.json")

    after_review_error_artifact = validate_substrate_error_artifact(
        "scillm_after_review_error.json",
        expected_node_ids={"rerun_page_review_after_patch"},
        expected_endpoints={"POST /v1/chat/completions", "fixture:review_after_response"},
    )
    if terminal_reason == "after_review_call_failed" and not after_review_error_artifact:
        errors.append("after_review_call_failed terminal ledger requires scillm_after_review_error.json")

    review_request = read_required_json_artifact("review_request.json") if "review_request.json" in evidence_artifacts else {}
    review_request_validation = (
        read_required_json_artifact("review_request_validation.json")
        if "review_request_validation.json" in evidence_artifacts
        else {}
    )
    if review_request_validation:
        if review_request_validation.get("schema") != "pdf_lab.second_pass.review_request_validation.v1":
            errors.append("review_request_validation schema mismatch")
        if review_request:
            recomputed_review_request_validation = validate_review_request_contract(case_dir, review_request)
            if review_request_validation != recomputed_review_request_validation:
                errors.append("review_request_validation does not match recomputed review_request contract")
    review_validation = read_required_json_artifact("review_validation.json") if "review_validation.json" in evidence_artifacts else {}
    review_response = read_required_json_artifact("review_response.json") if "review_response.json" in evidence_artifacts else {}
    review_fixture = (
        read_required_json_artifact("review_fixture.json")
        if "review_fixture.json" in evidence_artifacts or (case_dir / "review_fixture.json").is_file()
        else {}
    )
    review_receipt = (
        read_required_json_artifact("scillm_review_receipt.json")
        if "scillm_review_receipt.json" in evidence_artifacts or (case_dir / "scillm_review_receipt.json").is_file()
        else {}
    )
    if review_validation:
        if review_validation.get("schema") != "pdf_lab.second_pass.review_validation.v1":
            errors.append("review_validation schema mismatch")
        validation_page_case = review_validation.get("page_case")
        if not isinstance(validation_page_case, dict):
            errors.append("review_validation page_case must be an object")
            validation_page_case = {}
        if validation_page_case.get("case_id") != terminal.get("case_id"):
            errors.append("review_validation page_case.case_id does not match terminal ledger")
        if validation_page_case.get("page_number") != terminal.get("page_number"):
            errors.append("review_validation page_case.page_number does not match terminal ledger")
        if not isinstance(review_validation.get("errors"), list):
            errors.append("review_validation errors must be a list")
            review_errors = []
        else:
            review_errors = review_validation.get("errors")
        if selected_candidate_ids_from_artifact:
            expected_ids = sorted(str(candidate_id) for candidate_id in review_validation.get("expected_candidate_ids") or [])
            seen_ids = sorted(str(candidate_id) for candidate_id in review_validation.get("seen_candidate_ids") or [])
            validate_candidate_count(
                review_validation,
                len(selected_candidate_ids_from_artifact),
                "review_validation",
                expected_label="selected_candidates",
            )
            if expected_ids != selected_candidate_ids_from_artifact:
                errors.append("review_validation expected_candidate_ids do not match selected_candidates")
            if review_response and seen_ids != selected_candidate_ids_from_artifact:
                errors.append("review_validation seen_candidate_ids do not match selected_candidates")
            if not review_response and seen_ids:
                errors.append("review_validation seen_candidate_ids must be empty without review_response")
            if review_response:
                recomputed_review_validation = validate_review_response(
                    review_response,
                    selected_candidate_ids_from_artifact,
                    receipt=review_receipt or None,
                    request=review_request or None,
                    page_case={"case_id": terminal.get("case_id"), "page_number": terminal.get("page_number")},
                )
                if review_validation != recomputed_review_validation:
                    errors.append("review_validation does not match recomputed review_response contract")
        if terminal_status == "reviewed_clean":
            if review_validation.get("ok") is not True:
                errors.append("reviewed_clean terminal ledger requires review_validation.ok true")
            if not review_response:
                errors.append("reviewed_clean terminal ledger requires review_response.json")
        elif terminal_reason == "defect_patch_not_implemented" and review_validation.get("ok") is not True:
            errors.append("defect_patch_not_implemented terminal ledger requires review_validation.ok true")
        elif terminal_reason == "review_validation_failed" and review_validation.get("ok") is not False:
            errors.append("review_validation_failed terminal ledger requires review_validation.ok false")
        elif terminal_reason == "dry_run_review_not_executed":
            if review_validation.get("ok") is not False:
                errors.append("dry_run_review_not_executed terminal ledger requires review_validation.ok false")
            if "dry_run_review_not_executed" not in review_errors:
                errors.append("dry_run_review_not_executed terminal ledger requires matching review_validation error")
            if review_response:
                errors.append("dry_run_review_not_executed terminal ledger must not include review_response.json")
        elif terminal_reason == "scillm_review_call_failed" and "scillm_review_call_failed" not in review_errors:
            errors.append("scillm_review_call_failed terminal ledger requires matching review_validation error")
        elif terminal_reason == "review_fixture_load_failed" and "review_fixture_load_failed" not in review_errors:
            errors.append("review_fixture_load_failed terminal ledger requires matching review_validation error")
    if review_response:
        if review_response.get("schema") != "pdf_lab.second_pass.review_response.v1":
            errors.append("review_response schema mismatch")
        page_status = review_response.get("page_status")
        findings = review_response.get("candidate_findings")
        if terminal_status == "reviewed_clean" and page_status != "clean":
            errors.append("reviewed_clean terminal ledger requires review_response page_status clean")
        if terminal_reason == "defect_patch_not_implemented" and page_status != "defect":
            errors.append("defect_patch_not_implemented terminal ledger requires review_response page_status defect")
        if terminal_reason == "scillm_review_validated_substrate_blocked" and page_status != "substrate_blocked":
            errors.append("scillm_review_validated_substrate_blocked terminal ledger requires review_response page_status substrate_blocked")
        if terminal_reason == "scillm_review_validated_unsure" and page_status != "unsure":
            errors.append("scillm_review_validated_unsure terminal ledger requires review_response page_status unsure")
        if terminal_status == "reviewed_clean":
            if not isinstance(findings, list):
                errors.append("reviewed_clean terminal ledger requires review_response candidate_findings list")
            elif any(not isinstance(finding, dict) or finding.get("status") != "clean" for finding in findings):
                errors.append("reviewed_clean terminal ledger requires all review_response candidate_findings clean")
        if selected_candidate_ids_from_artifact and isinstance(findings, list):
            response_candidate_ids = sorted(
                finding["candidate_id"]
                for finding in findings
                if isinstance(finding, dict) and isinstance(finding.get("candidate_id"), str)
            )
            if response_candidate_ids != selected_candidate_ids_from_artifact:
                errors.append("review_response candidate_findings do not match selected_candidates")
    if review_fixture:
        if review_fixture.get("schema") != "pdf_lab.second_pass.review_fixture_materialized.v1":
            errors.append("review_fixture schema mismatch")
        if not isinstance(review_fixture.get("source_path"), str) or not review_fixture.get("source_path", "").strip():
            errors.append("review_fixture source_path must be non-empty")
        fixture_review_response = review_fixture.get("review_response")
        if not isinstance(fixture_review_response, dict):
            errors.append("review_fixture review_response must be an object")
        elif review_response and fixture_review_response != review_response:
            errors.append("review_fixture review_response does not match review_response")
    if "repair_plan_validation.json" in evidence_artifacts:
        repair_plan_validation = read_required_json_artifact("repair_plan_validation.json")
        repair_plan_request = read_required_json_artifact("repair_plan_request.json")
        repair_plan_receipt = (
            read_required_json_artifact("repair_plan_receipt.json")
            if "repair_plan_receipt.json" in evidence_artifacts or (case_dir / "repair_plan_receipt.json").is_file()
            else {}
        )
        if repair_plan_validation:
            if repair_plan_validation.get("schema") != "pdf_lab.second_pass.repair_plan_validation.v1":
                errors.append("repair_plan_validation schema mismatch")
            if not isinstance(repair_plan_validation.get("errors"), list):
                errors.append("repair_plan_validation errors must be a list")
            repair_plan_page_case = repair_plan_validation.get("page_case")
            if not isinstance(repair_plan_page_case, dict):
                errors.append("repair_plan_validation page_case must be an object")
                repair_plan_page_case = {}
            if repair_plan_page_case.get("case_id") != terminal.get("case_id"):
                errors.append("repair_plan_validation page_case.case_id does not match terminal ledger")
            if repair_plan_page_case.get("page_number") != terminal.get("page_number"):
                errors.append("repair_plan_validation page_case.page_number does not match terminal ledger")
            if selected_candidate_ids_from_artifact:
                expected_repair_plan_ids = sorted(str(candidate_id) for candidate_id in repair_plan_validation.get("expected_candidate_ids") or [])
                validate_candidate_count(
                    repair_plan_validation,
                    len(selected_candidate_ids_from_artifact),
                    "repair_plan_validation",
                    expected_label="selected_candidates",
                )
                if expected_repair_plan_ids != selected_candidate_ids_from_artifact:
                    errors.append("repair_plan_validation expected_candidate_ids do not match selected_candidates")
            if repair_plan_validation.get("ok") is True:
                if "repair_plan_request.json" not in evidence_artifacts or not repair_plan_request:
                    errors.append("repair_plan_validation ok true requires repair_plan_request.json evidence")
                if "repair_plan_receipt.json" not in evidence_artifacts or not repair_plan_receipt:
                    errors.append("repair_plan_validation ok true requires repair_plan_receipt.json evidence")
            if repair_plan_receipt:
                recomputed_repair_plan_validation = validate_repair_plan(
                    repair_plan_receipt.get("repair_plan"),
                    receipt=repair_plan_receipt,
                    request=repair_plan_request if repair_plan_request.get("schema") == "pdf_lab.second_pass.scillm_repair_plan_request.v1" else None,
                )
                if repair_plan_validation != recomputed_repair_plan_validation:
                    errors.append("repair_plan_validation does not match recomputed repair_plan contract")
    if "repair_diagnosis_validation.json" in evidence_artifacts:
        repair_diagnosis_validation = read_required_json_artifact("repair_diagnosis_validation.json")
        repair_diagnosis_request = read_required_json_artifact("repair_diagnosis_request.json")
        repair_diagnosis_receipt = (
            read_required_json_artifact("repair_diagnosis_receipt.json")
            if "repair_diagnosis_receipt.json" in evidence_artifacts or (case_dir / "repair_diagnosis_receipt.json").is_file()
            else {}
        )
        if repair_diagnosis_validation:
            if repair_diagnosis_validation.get("schema") != "pdf_lab.second_pass.repair_diagnosis_validation.v1":
                errors.append("repair_diagnosis_validation schema mismatch")
            if not isinstance(repair_diagnosis_validation.get("errors"), list):
                errors.append("repair_diagnosis_validation errors must be a list")
            repair_diagnosis_page_case = repair_diagnosis_validation.get("page_case")
            if not isinstance(repair_diagnosis_page_case, dict):
                errors.append("repair_diagnosis_validation page_case must be an object")
                repair_diagnosis_page_case = {}
            if repair_diagnosis_page_case.get("case_id") != terminal.get("case_id"):
                errors.append("repair_diagnosis_validation page_case.case_id does not match terminal ledger")
            if repair_diagnosis_page_case.get("page_number") != terminal.get("page_number"):
                errors.append("repair_diagnosis_validation page_case.page_number does not match terminal ledger")
            if selected_candidate_ids_from_artifact:
                expected_repair_diagnosis_ids = sorted(
                    str(candidate_id) for candidate_id in repair_diagnosis_validation.get("expected_candidate_ids") or []
                )
                validate_candidate_count(
                    repair_diagnosis_validation,
                    len(selected_candidate_ids_from_artifact),
                    "repair_diagnosis_validation",
                    expected_label="selected_candidates",
                )
                if expected_repair_diagnosis_ids != selected_candidate_ids_from_artifact:
                    errors.append("repair_diagnosis_validation expected_candidate_ids do not match selected_candidates")
            if repair_diagnosis_validation.get("ok") is True:
                if "repair_diagnosis_request.json" not in evidence_artifacts or not repair_diagnosis_request:
                    errors.append("repair_diagnosis_validation ok true requires repair_diagnosis_request.json evidence")
                if "repair_diagnosis_receipt.json" not in evidence_artifacts or not repair_diagnosis_receipt:
                    errors.append("repair_diagnosis_validation ok true requires repair_diagnosis_receipt.json evidence")
            if repair_diagnosis_receipt:
                recomputed_repair_diagnosis_validation = validate_repair_diagnosis_delegate_receipt(
                    repair_diagnosis_receipt,
                    patch_mode="live",
                    request=repair_diagnosis_request
                    if repair_diagnosis_request.get("schema")
                    in {
                        "pdf_lab.second_pass.opencode_repair_diagnosis_request.v1",
                        "pdf_lab.second_pass.scillm_orchestrator_repair_diagnosis_request.v1",
                    }
                    else None,
                )
                if repair_diagnosis_validation != recomputed_repair_diagnosis_validation:
                    errors.append("repair_diagnosis_validation does not match recomputed repair_diagnosis contract")
    if "patch_attempts_ledger.json" in evidence_artifacts:
        patch_attempts_ledger = read_required_json_artifact("patch_attempts_ledger.json")
        patch_validation = read_required_json_artifact("patch_validation.json")
        if patch_attempts_ledger:
            if patch_validation and not isinstance(patch_validation.get("errors"), list):
                errors.append("patch_validation errors must be a list")
            if patch_attempts_ledger.get("schema") != "pdf_lab.second_pass.patch_attempts_ledger.v1":
                errors.append("patch_attempts_ledger schema mismatch")
            ledger_page_case = patch_attempts_ledger.get("page_case")
            if not isinstance(ledger_page_case, dict):
                errors.append("patch_attempts_ledger page_case must be an object")
                ledger_page_case = {}
            if ledger_page_case.get("case_id") != terminal.get("case_id"):
                errors.append("patch_attempts_ledger page_case.case_id does not match terminal ledger")
            if ledger_page_case.get("page_number") != terminal.get("page_number"):
                errors.append("patch_attempts_ledger page_case.page_number does not match terminal ledger")
            ledger_candidate_ids = patch_attempts_ledger.get("candidate_ids")
            if not isinstance(ledger_candidate_ids, list) or not all(isinstance(candidate_id, str) for candidate_id in ledger_candidate_ids):
                errors.append("patch_attempts_ledger candidate_ids must be a list of strings")
                ledger_candidate_ids = []
            validate_candidate_count(
                patch_attempts_ledger,
                len(ledger_candidate_ids),
                "patch_attempts_ledger",
                expected_label="candidate_ids",
            )
            if selected_candidate_ids_from_artifact and sorted(ledger_candidate_ids) != selected_candidate_ids_from_artifact:
                errors.append("patch_attempts_ledger candidate_ids do not match selected_candidates")
            attempts = patch_attempts_ledger.get("attempts")
            if not isinstance(attempts, list):
                errors.append("patch_attempts_ledger attempts is not a list")
                attempts = []
            attempt_count = patch_attempts_ledger.get("attempt_count")
            if type(attempt_count) is not int or attempt_count < 0:
                errors.append("patch_attempts_ledger attempt_count must be a non-negative integer")
            elif attempt_count != len(attempts):
                errors.append("patch_attempts_ledger attempt_count does not match attempts length")
            agent_sequence = patch_attempts_ledger.get("agent_sequence")
            if not isinstance(agent_sequence, list):
                errors.append("patch_attempts_ledger agent_sequence is not a list")
                agent_sequence = []
            selected_attempt_index = patch_attempts_ledger.get("selected_attempt_index")
            if selected_attempt_index is not None and (type(selected_attempt_index) is not int or selected_attempt_index < 1):
                errors.append("patch_attempts_ledger selected_attempt_index must be null or a positive integer")
            expected_selected_attempt = next(
                (
                    attempt.get("attempt_index")
                    for attempt in attempts
                    if isinstance(attempt, dict)
                    and attempt.get("ok") is True
                    and type(attempt.get("attempt_index")) is int
                    and attempt.get("attempt_index") >= 1
                ),
                None,
            )
            if selected_attempt_index != expected_selected_attempt:
                errors.append("patch_attempts_ledger selected_attempt_index does not match first ok attempt")
            selected_or_final_validation: dict[str, Any] | None = None

            def is_safe_patch_attempt_artifact(artifact: str) -> bool:
                artifact_path = Path(artifact)
                return not artifact_path.is_absolute() and ".." not in artifact_path.parts

            for index, attempt in enumerate(attempts):
                if not isinstance(attempt, dict):
                    errors.append(f"patch_attempts_ledger attempts[{index}] is not an object")
                    continue
                attempt_index = attempt.get("attempt_index")
                if type(attempt_index) is not int or attempt_index < 1:
                    errors.append(f"patch_attempts_ledger attempts[{index}].attempt_index must be positive integer")
                if index < len(agent_sequence) and attempt.get("agent") != agent_sequence[index]:
                    errors.append(f"patch_attempts_ledger attempts[{index}].agent does not match agent_sequence")
                if not is_plain_bool(attempt.get("ok")):
                    errors.append(f"patch_attempts_ledger attempts[{index}].ok must be boolean")
                validation_artifact = attempt.get("validation_artifact")
                if not isinstance(validation_artifact, str) or not validation_artifact:
                    errors.append(f"patch_attempts_ledger attempts[{index}].validation_artifact must be non-empty")
                    attempt_validation = {}
                elif not is_safe_patch_attempt_artifact(validation_artifact):
                    errors.append(f"patch_attempts_ledger attempts[{index}].validation_artifact contains unsafe artifact path")
                    attempt_validation = {}
                else:
                    attempt_validation = read_required_json_artifact(validation_artifact)
                    if validation_artifact not in evidence_artifacts:
                        errors.append(f"patch_attempts_ledger attempts[{index}].validation_artifact is not declared terminal evidence")
                if attempt_validation:
                    if attempt_validation.get("schema") != "pdf_lab.second_pass.patch_delegate_validation.v1":
                        errors.append(f"patch_attempts_ledger attempts[{index}].validation_artifact schema mismatch")
                    if not isinstance(attempt_validation.get("errors"), list):
                        errors.append(f"patch_attempts_ledger attempts[{index}].validation_artifact errors must be a list")
                    if attempt_validation.get("ok") != attempt.get("ok"):
                        errors.append(f"patch_attempts_ledger attempts[{index}].ok does not match validation_artifact")
                    validation_errors = attempt_validation.get("errors") if isinstance(attempt_validation.get("errors"), list) else []
                    if not isinstance(attempt.get("errors"), list):
                        errors.append(f"patch_attempts_ledger attempts[{index}].errors must be a list")
                        attempt_errors = []
                    else:
                        attempt_errors = attempt.get("errors")
                    if validation_errors != attempt_errors:
                        errors.append(f"patch_attempts_ledger attempts[{index}].errors do not match validation_artifact")
                attempt_request: dict[str, Any] = {}
                attempt_receipt: dict[str, Any] = {}
                for artifact_key in ["request_artifact", "receipt_artifact"]:
                    attempt_artifact = attempt.get(artifact_key)
                    if not isinstance(attempt_artifact, str) or not attempt_artifact or not is_safe_patch_attempt_artifact(attempt_artifact):
                        continue
                    if attempt_artifact in evidence_artifacts:
                        payload = read_required_json_artifact(attempt_artifact)
                        if artifact_key == "request_artifact":
                            attempt_request = payload
                        else:
                            attempt_receipt = payload
                if attempt_validation and attempt_request and attempt_receipt:
                    recomputed_attempt_validation = validate_patch_delegate_receipt(
                        attempt_receipt,
                        patch_mode=str(patch_attempts_ledger.get("patch_mode") or "live"),
                        request=attempt_request
                        if attempt_request.get("schema")
                        in {
                            "pdf_lab.second_pass.opencode_patch_request.v1",
                            "pdf_lab.second_pass.scillm_orchestrator_patch_request.v1",
                        }
                        else None,
                    )
                    if attempt_validation != recomputed_attempt_validation:
                        errors.append(
                            f"patch_attempts_ledger attempts[{index}].validation_artifact does not match recomputed request/receipt validation"
                        )
                if attempt.get("ok") is True:
                    for artifact_key in ["request_artifact", "receipt_artifact"]:
                        attempt_artifact = attempt.get(artifact_key)
                        if not isinstance(attempt_artifact, str) or not attempt_artifact:
                            errors.append(f"patch_attempts_ledger attempts[{index}].{artifact_key} must be non-empty for ok attempt")
                            continue
                        if not is_safe_patch_attempt_artifact(attempt_artifact):
                            errors.append(f"patch_attempts_ledger attempts[{index}].{artifact_key} contains unsafe artifact path")
                            continue
                        if attempt_artifact not in evidence_artifacts:
                            errors.append(
                                f"patch_attempts_ledger attempts[{index}].{artifact_key} is not declared terminal evidence"
                            )
                if attempt.get("ok") is True or index == len(attempts) - 1:
                    selected_or_final_validation = attempt_validation
            if patch_validation and selected_or_final_validation:
                if patch_validation.get("schema") != selected_or_final_validation.get("schema"):
                    errors.append("patch_validation schema does not match selected/final patch attempt validation")
                if patch_validation.get("ok") != selected_or_final_validation.get("ok"):
                    errors.append("patch_validation ok does not match selected/final patch attempt validation")
                patch_validation_errors = validation_error_list(patch_validation, "patch_validation")
                selected_or_final_validation_errors = validation_error_list(
                    selected_or_final_validation,
                    "selected/final patch attempt validation",
                )
                if patch_validation_errors != selected_or_final_validation_errors:
                    errors.append("patch_validation errors do not match selected/final patch attempt validation")
                if patch_validation != selected_or_final_validation:
                    errors.append("patch_validation does not match selected/final patch attempt validation")
    patch_delta_artifact = read_required_json_artifact("patch_delta.json") if "patch_delta.json" in evidence_artifacts else {}
    patch_scope_validation_artifact = (
        read_required_json_artifact("patch_scope_validation.json")
        if "patch_scope_validation.json" in evidence_artifacts
        else {}
    )
    if patch_delta_artifact:
        if patch_delta_artifact.get("schema") != "pdf_lab.second_pass.patch_delta.v1":
            errors.append("patch_delta schema mismatch")
        if not is_plain_bool(patch_delta_artifact.get("ok")):
            errors.append("patch_delta ok must be boolean")
        if not isinstance(patch_delta_artifact.get("patch_changed_files"), list):
            errors.append("patch_delta patch_changed_files is not a list")
        if not isinstance(patch_delta_artifact.get("errors"), list):
            errors.append("patch_delta errors is not a list")
    if patch_scope_validation_artifact:
        if patch_scope_validation_artifact.get("schema") != "pdf_lab.second_pass.patch_scope_validation.v1":
            errors.append("patch_scope_validation schema mismatch")
        if not is_plain_bool(patch_scope_validation_artifact.get("ok")):
            errors.append("patch_scope_validation ok must be boolean")
        changed_files = patch_scope_validation_artifact.get("changed_files")
        if not isinstance(changed_files, list) or not all(isinstance(path, str) for path in changed_files):
            errors.append("patch_scope_validation changed_files is not a list of strings")
            changed_files = []
        allowed_prefixes = patch_scope_validation_artifact.get("allowed_prefixes")
        if not isinstance(allowed_prefixes, list) or not all(isinstance(prefix, str) for prefix in allowed_prefixes):
            errors.append("patch_scope_validation allowed_prefixes is not a list of strings")
            allowed_prefixes = []
        if not isinstance(patch_scope_validation_artifact.get("test_files"), list):
            errors.append("patch_scope_validation test_files is not a list")
        if not isinstance(patch_scope_validation_artifact.get("errors"), list):
            errors.append("patch_scope_validation errors is not a list")
        if patch_delta_artifact and isinstance(patch_delta_artifact.get("patch_changed_files"), list):
            delta_changed_files = sorted(str(path) for path in patch_delta_artifact.get("patch_changed_files") or [])
            if sorted(changed_files) != delta_changed_files:
                errors.append("patch_scope_validation changed_files do not match patch_delta patch_changed_files")
            if patch_delta_artifact.get("ok") is True and allowed_prefixes:
                recomputed_patch_scope_validation = validate_patch_scope(
                    delta_changed_files,
                    allowed_prefixes,
                    patch_scope_validation_artifact.get("delegate_claim")
                    if isinstance(patch_scope_validation_artifact.get("delegate_claim"), dict)
                    else None,
                )
                if patch_scope_validation_artifact != recomputed_patch_scope_validation:
                    errors.append("patch_scope_validation does not match recomputed patch scope contract")
        if terminal_reason == "patch_scope_validation_failed" and patch_scope_validation_artifact.get("ok") is not False:
            errors.append("patch_scope_validation_failed terminal ledger requires patch_scope_validation.ok false")
    test_validation_artifact = read_required_json_artifact("test_validation.json") if "test_validation.json" in evidence_artifacts else {}
    if test_validation_artifact:
        if test_validation_artifact.get("schema") != "pdf_lab.second_pass.test_validation.v1":
            errors.append("test_validation schema mismatch")
        if not is_plain_bool(test_validation_artifact.get("ok")):
            errors.append("test_validation ok must be boolean")
        if not isinstance(test_validation_artifact.get("errors"), list):
            errors.append("test_validation errors is not a list")
        elif test_validation_artifact.get("ok") is True and test_validation_artifact.get("errors"):
            errors.append("test_validation ok true requires empty errors")
        elif test_validation_artifact.get("ok") is False and not test_validation_artifact.get("errors"):
            errors.append("test_validation ok false requires non-empty errors")
        required_test_files = test_validation_artifact.get("required_test_files")
        covered_test_files = test_validation_artifact.get("covered_test_files")
        missing_test_file_coverage = test_validation_artifact.get("missing_test_file_coverage")
        if not isinstance(required_test_files, list):
            errors.append("test_validation required_test_files is not a list")
            required_test_files = []
        if not isinstance(covered_test_files, list):
            errors.append("test_validation covered_test_files is not a list")
            covered_test_files = []
        if not isinstance(missing_test_file_coverage, list):
            errors.append("test_validation missing_test_file_coverage is not a list")
            missing_test_file_coverage = []
        if patch_scope_validation_artifact and isinstance(patch_scope_validation_artifact.get("test_files"), list):
            expected_test_files = sorted(str(path) for path in patch_scope_validation_artifact.get("test_files") or [])
            if sorted(str(path) for path in required_test_files) != expected_test_files:
                errors.append("test_validation required_test_files do not match patch_scope_validation test_files")
            if sorted(str(path) for path in covered_test_files) != expected_test_files:
                errors.append("test_validation covered_test_files do not match patch_scope_validation test_files")
            if missing_test_file_coverage:
                errors.append("test_validation missing_test_file_coverage is not empty")
        if terminal_reason == "targeted_tests_failed" and test_validation_artifact.get("ok") is not False:
            errors.append("targeted_tests_failed terminal ledger requires test_validation.ok false")
    if terminal_status == "patched_confirmed":
        commit_sha = terminal.get("commit_sha")
        if not isinstance(commit_sha, str) or not commit_sha:
            errors.append("patched_confirmed terminal ledger commit_sha must be a non-empty string")
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
            elif artifact != "terminal_ledger_validation.json" and not (case_dir / artifact).is_file():
                errors.append(f"patched_confirmed terminal ledger artifact missing on disk: {artifact}")
        patch_scope_validation = read_required_json_artifact("patch_scope_validation.json")
        test_validation = read_required_json_artifact("test_validation.json")
        selected_candidates = read_required_json_artifact("selected_candidates.json")
        review_after_request = read_required_json_artifact("review_after_request.json")
        review_after_request_validation = read_required_json_artifact("review_after_request_validation.json")
        review_after_validation = read_required_json_artifact("review_after_validation.json")
        review_after_response = read_required_json_artifact("review_after_response.json")
        after_review_receipt = (
            read_required_json_artifact("scillm_after_review_receipt.json")
            if "scillm_after_review_receipt.json" in evidence_artifacts or (case_dir / "scillm_after_review_receipt.json").is_file()
            else {}
        )
        after_review_fixture = (
            read_required_json_artifact("review_after_fixture.json")
            if "review_after_fixture.json" in evidence_artifacts or (case_dir / "review_after_fixture.json").is_file()
            else {}
        )
        commit_acceptance = read_required_json_artifact("commit_acceptance_gate.json")
        commit_gate = read_required_json_artifact("commit_gate.json")
        revertability = read_required_json_artifact("revertability_check.json")
        selected_candidate_ids: list[str] = []
        if patch_scope_validation:
            if patch_scope_validation.get("schema") != "pdf_lab.second_pass.patch_scope_validation.v1":
                errors.append("patch_scope_validation schema mismatch")
            if patch_scope_validation.get("ok") is not True:
                errors.append("patch_scope_validation.ok is not true")
            if not isinstance(patch_scope_validation.get("changed_files"), list):
                errors.append("patch_scope_validation changed_files is not a list")
            if not isinstance(patch_scope_validation.get("test_files"), list):
                errors.append("patch_scope_validation test_files is not a list")
        if test_validation:
            if test_validation.get("schema") != "pdf_lab.second_pass.test_validation.v1":
                errors.append("test_validation schema mismatch")
            if test_validation.get("ok") is not True:
                errors.append("test_validation.ok is not true")
            if patch_scope_validation and isinstance(patch_scope_validation.get("test_files"), list):
                required_tests = sorted(str(path) for path in patch_scope_validation.get("test_files") or [])
                validation_required_tests = sorted(str(path) for path in test_validation.get("required_test_files") or [])
                validation_covered_tests = sorted(str(path) for path in test_validation.get("covered_test_files") or [])
                validation_missing_tests = sorted(str(path) for path in test_validation.get("missing_test_file_coverage") or [])
                if validation_required_tests and validation_required_tests != required_tests:
                    errors.append("test_validation required_test_files do not match patch_scope_validation test_files")
                if validation_covered_tests != required_tests:
                    errors.append("test_validation covered_test_files do not match patch_scope_validation test_files")
                if validation_missing_tests:
                    errors.append("test_validation missing_test_file_coverage is not empty")
        if review_after_request_validation:
            if review_after_request_validation.get("schema") != "pdf_lab.second_pass.review_request_validation.v1":
                errors.append("review_after_request_validation schema mismatch")
            if review_after_request_validation.get("ok") is not True:
                errors.append("review_after_request_validation.ok is not true")
            if review_after_request.get("schema") == "pdf_lab.second_pass.review_request.v1":
                recomputed_after_request_validation = validate_review_request_contract(case_dir, review_after_request)
                if review_after_request_validation != recomputed_after_request_validation:
                    errors.append("review_after_request_validation does not match recomputed review_after_request contract")
        if review_after_validation:
            if review_after_validation.get("schema") != "pdf_lab.second_pass.review_validation.v1":
                errors.append("review_after_validation schema mismatch")
            if review_after_validation.get("ok") is not True:
                errors.append("review_after_validation.ok is not true")
            expected_after_case = {
                "case_id": f"{terminal.get('case_id')}:after_patch",
                "page_number": terminal.get("page_number"),
            }
            after_validation_page_case = review_after_validation.get("page_case")
            if not isinstance(after_validation_page_case, dict):
                errors.append("review_after_validation page_case must be an object")
                after_validation_page_case = {}
            if after_validation_page_case.get("case_id") != expected_after_case["case_id"]:
                errors.append("review_after_validation page_case.case_id does not match after-patch page case")
            if after_validation_page_case.get("page_number") != expected_after_case["page_number"]:
                errors.append("review_after_validation page_case.page_number does not match terminal ledger")
            if selected_candidates:
                if selected_candidates.get("schema") != SELECTED_CANDIDATES_SCHEMA:
                    errors.append("selected_candidates schema mismatch")
                selected_page_case = selected_candidates.get("page_case")
                if not isinstance(selected_page_case, dict):
                    errors.append("selected_candidates page_case must be an object")
                    selected_page_case = {}
                if selected_page_case.get("case_id") != terminal.get("case_id"):
                    errors.append("selected_candidates page_case.case_id does not match terminal ledger")
                if selected_page_case.get("page_number") != terminal.get("page_number"):
                    errors.append("selected_candidates page_case.page_number does not match terminal ledger")
                candidates = selected_candidates.get("candidates")
                if not isinstance(candidates, list):
                    errors.append("selected_candidates candidates is not a list")
                else:
                    selected_candidate_ids = sorted(
                        candidate["candidate_id"]
                        for candidate in candidates
                        if isinstance(candidate, dict) and isinstance(candidate.get("candidate_id"), str)
                    )
                    if len(selected_candidate_ids) != len(candidates):
                        errors.append("selected_candidates candidates contain missing candidate_id")
                    validate_candidate_count(selected_candidates, len(candidates), "selected_candidates")
            if selected_candidate_ids:
                expected_after_ids = sorted(str(candidate_id) for candidate_id in review_after_validation.get("expected_candidate_ids") or [])
                seen_after_ids = sorted(str(candidate_id) for candidate_id in review_after_validation.get("seen_candidate_ids") or [])
                validate_candidate_count(
                    review_after_validation,
                    len(selected_candidate_ids),
                    "review_after_validation",
                    expected_label="selected_candidates",
                )
                if expected_after_ids != selected_candidate_ids:
                    errors.append("review_after_validation expected_candidate_ids do not match selected_candidates")
                if seen_after_ids != selected_candidate_ids:
                    errors.append("review_after_validation seen_candidate_ids do not match selected_candidates")
                if review_after_response:
                    recomputed_after_validation = validate_review_response(
                        review_after_response,
                        selected_candidate_ids,
                        receipt=after_review_receipt or None,
                        request=review_after_request if review_after_request.get("schema") == "pdf_lab.second_pass.review_request.v1" else None,
                        page_case=expected_after_case,
                    )
                    if review_after_validation != recomputed_after_validation:
                        errors.append("review_after_validation does not match recomputed review_after_response contract")
            if (
                review_after_validation.get("ok") is True
                and review_after_request.get("schema") == "pdf_lab.second_pass.review_request.v1"
                and not after_review_receipt
                and not after_review_fixture
            ):
                errors.append("review_after_validation ok true requires scillm_after_review_receipt.json or review_after_fixture.json evidence")
        if review_after_response:
            if review_after_response.get("schema") != "pdf_lab.second_pass.review_response.v1":
                errors.append("review_after_response schema mismatch")
            if review_after_response.get("page_status") != "clean":
                errors.append("review_after_response page_status is not clean")
            findings = review_after_response.get("candidate_findings")
            if not isinstance(findings, list):
                errors.append("review_after_response candidate_findings is not a list")
            else:
                if any(not isinstance(finding, dict) or finding.get("status") != "clean" for finding in findings):
                    errors.append("review_after_response candidate_findings are not all clean")
                if selected_candidate_ids:
                    response_candidate_ids = sorted(
                        finding["candidate_id"]
                        for finding in findings
                        if isinstance(finding, dict) and isinstance(finding.get("candidate_id"), str)
                    )
                    if response_candidate_ids != selected_candidate_ids:
                        errors.append("review_after_response candidate_findings do not match selected_candidates")
        if after_review_fixture:
            if after_review_fixture.get("schema") != "pdf_lab.second_pass.review_after_fixture_materialized.v1":
                errors.append("review_after_fixture schema mismatch")
            if not isinstance(after_review_fixture.get("source_path"), str) or not after_review_fixture.get("source_path", "").strip():
                errors.append("review_after_fixture source_path must be non-empty")
            fixture_review_response = after_review_fixture.get("review_response")
            if not isinstance(fixture_review_response, dict):
                errors.append("review_after_fixture review_response must be an object")
            elif review_after_response and fixture_review_response != review_after_response:
                errors.append("review_after_fixture review_response does not match review_after_response")
        if commit_acceptance:
            if commit_acceptance.get("schema") != "pdf_lab.second_pass.commit_acceptance_gate.v1":
                errors.append("commit_acceptance_gate schema mismatch")
            if commit_acceptance.get("ok") is not True:
                errors.append("commit_acceptance_gate.ok is not true")
            if terminal.get("commit_acceptance_ok") != commit_acceptance.get("ok"):
                errors.append("terminal commit_acceptance_ok does not match commit_acceptance_gate.ok")
            if commit_acceptance.get("commit_sha") != commit_sha:
                errors.append("commit_acceptance_gate commit_sha does not match terminal ledger")
            if commit_gate:
                recomputed_commit_acceptance = validate_commit_gate_acceptance(commit_gate)
                if commit_acceptance != recomputed_commit_acceptance:
                    errors.append("commit_acceptance_gate does not match recomputed commit_gate acceptance")
        if commit_gate:
            if commit_gate.get("schema") != "pdf_lab.second_pass.commit_gate.v1":
                errors.append("commit_gate schema mismatch")
            if commit_gate.get("ok") is not True:
                errors.append("commit_gate.ok is not true")
            if terminal.get("commit_gate_ok") != commit_gate.get("ok"):
                errors.append("terminal commit_gate_ok does not match commit_gate.ok")
            if commit_gate.get("commit_sha") != commit_sha:
                errors.append("commit_gate commit_sha does not match terminal ledger")
            if commit_gate.get("exact_file_match") is not True:
                errors.append("commit_gate.exact_file_match is not true")
            if terminal.get("commit_exact_file_match") != commit_gate.get("exact_file_match"):
                errors.append("terminal commit_exact_file_match does not match commit_gate.exact_file_match")
            if patch_scope_validation and isinstance(patch_scope_validation.get("changed_files"), list):
                expected_changed_files = sorted(str(path) for path in patch_scope_validation.get("changed_files") or [])
                changed_files = commit_gate.get("changed_files")
                if not isinstance(changed_files, list):
                    errors.append("commit_gate changed_files is not a list")
                else:
                    actual_changed_files = sorted(str(path) for path in changed_files)
                    if actual_changed_files != expected_changed_files:
                        errors.append("commit_gate changed_files do not match patch_scope_validation changed_files")
                committed_files = commit_gate.get("committed_files")
                if not isinstance(committed_files, list):
                    errors.append("commit_gate committed_files is not a list")
                else:
                    actual_committed_files = sorted(str(path) for path in committed_files)
                    if actual_committed_files != expected_changed_files:
                        errors.append("commit_gate committed_files do not match patch_scope_validation changed_files")
            commit_gate_revertability = commit_gate.get("revertability_check")
            if not isinstance(commit_gate_revertability, dict):
                errors.append("commit_gate.revertability_check missing or not an object")
            else:
                if commit_gate_revertability.get("schema") != "pdf_lab.second_pass.revertability_check.v1":
                    errors.append("commit_gate.revertability_check schema mismatch")
                if commit_gate_revertability.get("ok") is not True:
                    errors.append("commit_gate.revertability_check.ok is not true")
                if commit_gate_revertability.get("commit_sha") != commit_sha:
                    errors.append("commit_gate.revertability_check commit_sha does not match terminal ledger")
        if revertability:
            if revertability.get("schema") != "pdf_lab.second_pass.revertability_check.v1":
                errors.append("revertability_check schema mismatch")
            if revertability.get("ok") is not True:
                errors.append("revertability_check.ok is not true")
            if terminal.get("commit_revertability_ok") != revertability.get("ok"):
                errors.append("terminal commit_revertability_ok does not match revertability_check.ok")
            if revertability.get("commit_sha") != commit_sha:
                errors.append("revertability_check commit_sha does not match terminal ledger")
            if commit_gate and isinstance(commit_gate.get("revertability_check"), dict):
                if revertability != commit_gate.get("revertability_check"):
                    errors.append("revertability_check does not match commit_gate.revertability_check")
    else:
        if terminal.get("commit_sha"):
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


def _safe_json_for_html(value: Any) -> str:
    return html_lib.escape(json.dumps(value, indent=2, sort_keys=True), quote=False)


def _artifact_link(name: str) -> str:
    escaped = html_lib.escape(name)
    return f'<a href="{escaped}">{escaped}</a>'


def render_review_html(case_dir: Path, terminal: dict[str, Any]) -> None:
    page_case = load_json(case_dir / "sampled_candidate_manifest.json").get("page_case") or {}
    candidates = load_json(case_dir / "selected_candidates.json").get("candidates") or []
    review_validation = load_json(case_dir / "review_validation.json")
    available_artifacts = [name for name in terminal.get("evidence_artifacts") or [] if (case_dir / name).is_file()]
    before_figures = []
    if (case_dir / "page_before.png").exists():
        before_figures.append('<figure><img src="page_before.png" alt="Rendered page before review"><figcaption>Rendered page before review</figcaption></figure>')
    if (case_dir / "page_candidates.png").exists():
        before_figures.append('<figure><img src="page_candidates.png" alt="Annotated candidate bounding boxes"><figcaption>Annotated candidate bounding boxes</figcaption></figure>')
    before_evidence = (
        '<div class="image-grid">' + "".join(before_figures) + "</div>"
        if before_figures
        else '<pre>No rendered page images were produced before this case failed closed.</pre>'
    )
    after_images = ""
    if (case_dir / "page_after.png").exists() and (case_dir / "page_after_candidates.png").exists():
        after_images = """
        <section>
          <h2>After Patch Evidence</h2>
          <div class="image-grid">
            <figure><img src="page_after.png" alt="Rendered page after patch"><figcaption>Rendered page after patch</figcaption></figure>
            <figure><img src="page_after_candidates.png" alt="Annotated candidates after patch"><figcaption>Annotated candidates after patch</figcaption></figure>
          </div>
        </section>
        """
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>PDF Lab Second-Pass Review {html_lib.escape(str(terminal.get("case_id")))}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1c2430;
      --muted: #5b6572;
      --line: #c9d2dc;
      --panel: #f7f9fb;
      --accent: #0b5cad;
    }}
    body {{
      margin: 0;
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: #ffffff;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 24px;
    }}
    header {{
      border-bottom: 1px solid var(--line);
      margin-bottom: 20px;
      padding-bottom: 12px;
    }}
    h1, h2 {{
      margin: 0 0 10px;
      letter-spacing: 0;
    }}
    h1 {{
      font-size: 22px;
    }}
    h2 {{
      font-size: 17px;
    }}
    section {{
      margin: 22px 0;
    }}
    .meta, .candidate, pre {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
    }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 8px;
      padding: 12px;
    }}
    .label {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
    }}
    .value {{
      font-weight: 650;
      overflow-wrap: anywhere;
    }}
    .image-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 16px;
    }}
    figure {{
      margin: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      overflow: hidden;
      background: #fff;
    }}
    img {{
      display: block;
      width: 100%;
      height: auto;
    }}
    figcaption {{
      padding: 8px 10px;
      color: var(--muted);
      border-top: 1px solid var(--line);
    }}
    .candidate {{
      margin: 10px 0;
      padding: 10px;
    }}
    .candidate code {{
      color: var(--accent);
    }}
    pre {{
      overflow: auto;
      padding: 12px;
      max-height: 420px;
      white-space: pre-wrap;
    }}
    a {{
      color: var(--accent);
    }}
    ul {{
      columns: 2;
      padding-left: 18px;
    }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>PDF Lab Second-Pass Page Review</h1>
    <div class="meta">
      <div><div class="label">Case</div><div class="value">{html_lib.escape(str(terminal.get("case_id")))}</div></div>
      <div><div class="label">Page</div><div class="value">{html_lib.escape(str(terminal.get("page_number")))}</div></div>
      <div><div class="label">Status</div><div class="value">{html_lib.escape(str(terminal.get("terminal_status")))}</div></div>
      <div><div class="label">Reason</div><div class="value">{html_lib.escape(str(terminal.get("reason")))}</div></div>
      <div><div class="label">Commit</div><div class="value">{html_lib.escape(str(terminal.get("commit_sha") or "none"))}</div></div>
    </div>
  </header>

	  <section>
	    <h2>Before Evidence</h2>
	    {before_evidence}
	  </section>
  {after_images}
  <section>
    <h2>Selected Candidates</h2>
    {''.join(
        '<div class="candidate"><code>' + html_lib.escape(str(candidate.get("candidate_id"))) + '</code>'
        + '<div>' + html_lib.escape(str(candidate.get("preset_type"))) + '</div>'
        + '<pre>' + _safe_json_for_html(candidate) + '</pre></div>'
        for candidate in candidates
    )}
  </section>

  <section>
    <h2>Review Validation</h2>
    <pre>{_safe_json_for_html(review_validation)}</pre>
  </section>

  <section>
    <h2>Page Case</h2>
    <pre>{_safe_json_for_html(page_case)}</pre>
  </section>

  <section>
    <h2>Artifacts</h2>
    <ul>{''.join('<li>' + _artifact_link(name) + '</li>' for name in available_artifacts)}</ul>
  </section>
</main>
</body>
</html>
"""
    (case_dir / "review.html").write_text(html, encoding="utf-8")


def finalize_page_case(
    *,
    case_dir: Path,
    receipts: ReceiptWriter,
    state: dict[str, Any],
    terminal: dict[str, Any],
) -> dict[str, Any]:
    if "terminal_ledger_validation.json" not in terminal.get("evidence_artifacts", []):
        terminal["evidence_artifacts"].append("terminal_ledger_validation.json")
    write_json(case_dir / "terminal_ledger.json", terminal)
    receipts.write(
        "write_page_terminal_ledger",
        input_artifacts=[name for name in ["review_request.json", "review_validation.json"] if (case_dir / name).exists()],
        output_artifacts=["terminal_ledger.json"],
        command_or_endpoint="run_page_second_pass_dag.write_page_terminal_ledger",
        validator_result={"ok": terminal["terminal_status"] in TERMINAL_STATUSES},
        next_allowed_nodes=["render_page_review_artifact"],
    )
    render_review_html(case_dir, terminal)
    receipts.write(
        "render_page_review_artifact",
        input_artifacts=["terminal_ledger.json", "review_validation.json"],
        output_artifacts=["review.html"],
        command_or_endpoint="run_page_second_pass_dag.render_review_html",
        validator_result={"ok": (case_dir / "review.html").is_file()},
        next_allowed_nodes=["validate_page_terminal_ledger"],
    )
    terminal_validation = validate_page_terminal_ledger(case_dir, terminal)
    write_json(case_dir / "terminal_ledger_validation.json", terminal_validation)
    receipts.write(
        "validate_page_terminal_ledger",
        input_artifacts=["terminal_ledger.json", "review.html"],
        output_artifacts=["terminal_ledger_validation.json"],
        command_or_endpoint="run_page_second_pass_dag.validate_page_terminal_ledger",
        validator_result={"ok": terminal_validation["ok"], "errors": terminal_validation["errors"]},
        next_allowed_nodes=["package_page_review_bundle"],
    )
    package_bundle(case_dir, case_dir / "review_bundle.zip")
    bundle_validation = validate_page_review_bundle(case_dir, case_dir / "review_bundle.zip", terminal)
    write_json(case_dir / "review_bundle_validation.json", bundle_validation)
    receipts.write(
        "package_page_review_bundle",
        input_artifacts=["terminal_ledger.json", "review.html"],
        output_artifacts=["review_bundle.zip", "review_bundle_validation.json"],
        command_or_endpoint="zipfile.ZipFile",
        validator_result={
            "ok": bundle_validation["ok"],
            "errors": bundle_validation["errors"],
            "missing_expected_zip_entries": bundle_validation["missing_expected_zip_entries"],
        },
        next_allowed_nodes=[],
    )
    state["terminal_status"] = terminal["terminal_status"]
    state["updated_at"] = utc_now()
    write_json(case_dir / "state.json", state)
    return {"case_dir": str(case_dir), "terminal_status": terminal["terminal_status"], "page_number": terminal["page_number"]}


def run_page_case(
    *,
    pdf_path: Path,
    manifest: dict[str, Any],
    sampled_cases: dict[str, Any],
    out_dir: Path,
    case_id: str | None,
    page_number: int | None,
    ledger_path: Path | None,
    apply_mode: str,
    dpi: int,
    model: str,
    batch_id: str,
    review_mode: str = "dry_run",
    review_fixture_path: Path | None = None,
    review_after_fixture_path: Path | None = None,
    scillm_base_url: str = "http://localhost:4001",
    scillm_auth_token: str = "sk-dev-proxy-123",
    caller_skill: str = "pdf-lab",
    scillm_timeout_s: float = 180.0,
    scillm_preflight_mode: str = "dry_run",
    patch_mode: str = "dry_run",
    patch_backend: str = "opencode_serve",
    opencode_agent: str = "build",
    opencode_agent_sequence: list[str] | None = None,
    opencode_model: str | None = None,
    patch_prompt_profile: str = "plan_only",
    repair_strategy: str = "single",
    opencode_timeout_s: float = 600.0,
    opencode_cleanup_session: bool = True,
    opencode_skills: list[str] | None = None,
    allowed_patch_prefixes: list[str] | None = None,
    validation_commands: list[str] | None = None,
    commit_mode: str = "dry_run",
    code_root: Path = REPO,
    page_extract_timeout_s: float | None = None,
    page_orchestrator_mode: str = "dry_run",
) -> dict[str, Any]:
    code_root = code_root.resolve()
    effective_opencode_model = resolve_effective_opencode_model(
        patch_mode=patch_mode,
        patch_backend=patch_backend,
        opencode_model=opencode_model,
    )
    if patch_prompt_profile not in PATCH_PROMPT_PROFILES:
        raise ValueError(f"unknown patch prompt profile: {patch_prompt_profile}")
    if repair_strategy not in PATCH_REPAIR_STRATEGIES:
        raise ValueError(f"unknown repair strategy: {repair_strategy}")
    if page_orchestrator_mode not in PAGE_ORCHESTRATOR_MODES:
        raise ValueError(f"unknown page orchestrator mode: {page_orchestrator_mode}")
    runtime_timeout_errors = validate_runtime_timeout_inputs(
        scillm_timeout_s=scillm_timeout_s,
        opencode_timeout_s=opencode_timeout_s,
        page_extract_timeout_s=page_extract_timeout_s,
    )
    if runtime_timeout_errors:
        raise ValueError("; ".join(runtime_timeout_errors))
    runtime_boolean_errors = validate_runtime_boolean_inputs(opencode_cleanup_session=opencode_cleanup_session)
    if runtime_boolean_errors:
        raise ValueError("; ".join(runtime_boolean_errors))
    runtime_list_errors = validate_runtime_list_inputs(
        opencode_agent_sequence=opencode_agent_sequence,
        opencode_skills=opencode_skills,
        allowed_patch_prefixes=allowed_patch_prefixes,
        validation_commands=validation_commands,
    )
    if runtime_list_errors:
        raise ValueError("; ".join(runtime_list_errors))
    page_case = _case_by_id_or_page(sampled_cases, case_id, page_number)
    page_case_identity = validate_page_case_identity(page_case)
    if page_case_identity["ok"] is not True:
        raise ValueError("; ".join(page_case_identity["errors"]))
    page_number = int(page_case["page_number"])
    case_dir = out_dir / str(page_case["case_id"])
    case_dir.mkdir(parents=True, exist_ok=True)
    receipts = ReceiptWriter(case_dir)

    state = {
        "schema": "pdf_lab.second_pass.page_state.v1",
        "case_id": page_case["case_id"],
        "page_number": page_number,
        "pdf_path": str(pdf_path),
        "code_root": str(code_root),
        "opencode_model": effective_opencode_model,
        "requested_opencode_model": opencode_model,
        "patch_prompt_profile": patch_prompt_profile,
        "repair_strategy": repair_strategy,
        "page_extract_timeout_s": page_extract_timeout_s,
        "page_orchestrator_mode": page_orchestrator_mode,
        "page_orchestrator_transport_run_id": None,
        "terminal_status": None,
        "created_at": utc_now(),
    }
    write_json(case_dir / "state.json", state)
    receipts.write(
        "initialize_page_case",
        input_artifacts=[],
        output_artifacts=["state.json"],
        command_or_endpoint="run_page_second_pass_dag.initialize_page_case",
        validator_result={"ok": True},
        next_allowed_nodes=["load_sampled_candidate_manifest"],
    )

    candidates = materialize_case_candidates(manifest, page_case)
    write_json(case_dir / "sampled_candidate_manifest.json", build_sampled_candidate_manifest(page_case, candidates))
    receipts.write(
        "load_sampled_candidate_manifest",
        input_artifacts=["state.json"],
        output_artifacts=["sampled_candidate_manifest.json"],
        command_or_endpoint="run_page_second_pass_dag.load_sampled_candidate_manifest",
        validator_result={"ok": True, "candidate_count": len(candidates)},
        next_allowed_nodes=["extract_page_json", "render_original_page"],
    )

    try:
        page_json = run_extract_page_for_code_root(
            pdf_path=pdf_path,
            page_number=page_number,
            ledger_path=ledger_path,
            apply_mode=apply_mode,
            code_root=code_root,
            page_extract_timeout_s=page_extract_timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 - page extraction substrate failures must be ledgered.
        extraction_error = {
            "schema": "pdf_lab.second_pass.substrate_error.v1",
            "node_id": "extract_page_json",
            "endpoint": "snapshot_current_extraction._extract_page",
            "case_id": page_case["case_id"],
            "page_number": page_number,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "page_extract_timeout_s": page_extract_timeout_s,
        }
        write_json(case_dir / "page_extraction_error.json", extraction_error)
        write_json(case_dir / "selected_candidates.json", build_selected_candidates(page_case, candidates))
        write_json(case_dir / "candidate_presets.json", build_candidate_presets(page_case, candidates))
        orchestrator_dag_spec = build_page_orchestrator_dag_spec(
            page_case=page_case,
            candidates=candidates,
            review_request_artifact=None,
            patch_backend=patch_backend,
            patch_mode=patch_mode,
            review_mode=review_mode,
            repair_strategy=repair_strategy,
            opencode_agent=opencode_agent,
            opencode_agent_sequence=opencode_agent_sequence,
            opencode_model=effective_opencode_model,
            code_root=code_root,
            caller_skill=caller_skill,
            page_extract_timeout_s=page_extract_timeout_s,
            status="blocked_before_model_nodes",
        )
        orchestrator_dag_spec_validation = validate_page_orchestrator_dag_spec(orchestrator_dag_spec)
        write_json(case_dir / "scillm_orchestrator_page_dag_spec.json", orchestrator_dag_spec)
        write_json(case_dir / "scillm_orchestrator_page_dag_spec_validation.json", orchestrator_dag_spec_validation)
        review_validation = {
            "schema": "pdf_lab.second_pass.review_validation.v1",
            "ok": False,
            "errors": ["page_extraction_failed"],
            "page_case": {"case_id": page_case["case_id"], "page_number": page_number},
            "candidate_count": len(candidates),
            "expected_candidate_ids": [candidate["candidate_id"] for candidate in candidates],
            "seen_candidate_ids": [],
        }
        write_json(case_dir / "review_validation.json", review_validation)
        receipts.write(
            "extract_page_json",
            input_artifacts=["sampled_candidate_manifest.json"],
            output_artifacts=["page_extraction_error.json"],
            command_or_endpoint="snapshot_current_extraction._extract_page",
            validator_result={"ok": False, "errors": ["page_extraction_failed"], "error_type": type(exc).__name__},
            next_allowed_nodes=["write_page_terminal_ledger"],
            exit_code=1,
        )
        terminal = {
            "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
            "case_id": page_case["case_id"],
            "page_number": page_number,
            "terminal_status": "blocked_substrate",
            "reason": "page_extraction_failed",
            "allowed_terminal_statuses": sorted(TERMINAL_STATUSES),
            "evidence_artifacts": [
                "state.json",
                "sampled_candidate_manifest.json",
                "selected_candidates.json",
                "candidate_presets.json",
                "scillm_orchestrator_page_dag_spec.json",
                "scillm_orchestrator_page_dag_spec_validation.json",
                "page_extraction_error.json",
                "review_validation.json",
                "review.html",
            ],
            "commit_sha": None,
        }
        return finalize_page_case(case_dir=case_dir, receipts=receipts, state=state, terminal=terminal)
    write_json(case_dir / "page_before.json", page_json)
    receipts.write(
        "extract_page_json",
        input_artifacts=["sampled_candidate_manifest.json"],
        output_artifacts=["page_before.json"],
        command_or_endpoint="snapshot_current_extraction._extract_page",
        validator_result={"ok": True, "block_count": len(page_json.get("blocks") or [])},
        next_allowed_nodes=["select_page_candidates"],
    )

    render_original_page(pdf_path, page_number, case_dir / "page_before.png", dpi)
    receipts.write(
        "render_original_page",
        input_artifacts=["state.json"],
        output_artifacts=["page_before.png"],
        command_or_endpoint="fitz.Page.get_pixmap",
        validator_result={"ok": (case_dir / "page_before.png").is_file()},
        next_allowed_nodes=["render_annotated_candidates"],
    )

    write_json(case_dir / "selected_candidates.json", build_selected_candidates(page_case, candidates))
    receipts.write(
        "select_page_candidates",
        input_artifacts=["sampled_candidate_manifest.json", "page_before.json"],
        output_artifacts=["selected_candidates.json"],
        command_or_endpoint="run_page_second_pass_dag.select_page_candidates",
        validator_result={"ok": bool(candidates), "candidate_count": len(candidates)},
        next_allowed_nodes=["render_annotated_candidates", "inject_candidate_presets"],
    )

    render_candidate_overlay(pdf_path, page_number, candidates, case_dir / "page_candidates.png", dpi)
    receipts.write(
        "render_annotated_candidates",
        input_artifacts=["page_before.png", "selected_candidates.json"],
        output_artifacts=["page_candidates.png"],
        command_or_endpoint="fitz.Page.draw_rect",
        validator_result={"ok": (case_dir / "page_candidates.png").is_file()},
        next_allowed_nodes=["build_model_ready_payload"],
    )

    candidate_presets = build_candidate_presets(page_case, candidates)
    write_json(case_dir / "candidate_presets.json", candidate_presets)
    receipts.write(
        "inject_candidate_presets",
        input_artifacts=["selected_candidates.json"],
        output_artifacts=["candidate_presets.json"],
        command_or_endpoint="run_page_second_pass_dag.inject_candidate_presets",
        validator_result={"ok": len(candidate_presets["candidates"]) == len(candidates)},
        next_allowed_nodes=["build_model_ready_payload"],
    )

    review_request = build_review_request(
        case_dir=case_dir,
        page_case=page_case,
        page_json_path="page_before.json",
        original_image_path="page_before.png",
        annotated_image_path="page_candidates.png",
        candidate_presets_path="candidate_presets.json",
        model=model,
        batch_id=batch_id,
    )
    write_json(case_dir / "review_request.json", review_request)
    review_request_validation = validate_review_request_contract(case_dir, review_request)
    write_json(case_dir / "review_request_validation.json", review_request_validation)
    orchestrator_dag_spec = build_page_orchestrator_dag_spec(
        page_case=page_case,
        candidates=candidates,
        review_request_artifact="review_request.json",
        patch_backend=patch_backend,
        patch_mode=patch_mode,
        review_mode=review_mode,
        repair_strategy=repair_strategy,
        opencode_agent=opencode_agent,
        opencode_agent_sequence=opencode_agent_sequence,
        opencode_model=effective_opencode_model,
        code_root=code_root,
        caller_skill=caller_skill,
        page_extract_timeout_s=page_extract_timeout_s,
        status="ready_for_scillm_orchestrator_nodes",
    )
    orchestrator_dag_spec_validation = validate_page_orchestrator_dag_spec(orchestrator_dag_spec)
    write_json(case_dir / "scillm_orchestrator_page_dag_spec.json", orchestrator_dag_spec)
    write_json(case_dir / "scillm_orchestrator_page_dag_spec_validation.json", orchestrator_dag_spec_validation)
    page_orchestrator_submission = build_page_orchestrator_submission(
        case_dir=case_dir,
        page_case=page_case,
        dag_spec=orchestrator_dag_spec,
        dag_spec_artifact="scillm_orchestrator_page_dag_spec.json",
        code_root=code_root,
        timeout_s=min(opencode_timeout_s, 45.0),
    )
    page_orchestrator_submission_validation = validate_page_orchestrator_submission(
        page_orchestrator_submission,
        dag_spec=orchestrator_dag_spec,
    )
    write_json(case_dir / "scillm_orchestrator_page_submission.json", page_orchestrator_submission)
    write_json(
        case_dir / "scillm_orchestrator_page_submission_validation.json",
        page_orchestrator_submission_validation,
    )
    receipts.write(
        "build_model_ready_payload",
        input_artifacts=["page_before.json", "page_before.png", "page_candidates.png", "candidate_presets.json"],
        output_artifacts=[
            "review_request.json",
            "review_request_validation.json",
            "scillm_orchestrator_page_dag_spec.json",
            "scillm_orchestrator_page_dag_spec_validation.json",
            "scillm_orchestrator_page_submission.json",
            "scillm_orchestrator_page_submission_validation.json",
        ],
        command_or_endpoint="run_page_second_pass_dag.build_model_ready_payload",
        validator_result={
            "ok": review_request_validation["ok"] and orchestrator_dag_spec_validation["ok"] and page_orchestrator_submission_validation["ok"],
            "scillm_metadata": review_request["scillm_metadata"],
            "review_request_errors": review_request_validation["errors"],
            "orchestrator_dag_errors": orchestrator_dag_spec_validation["errors"],
            "orchestrator_submission_errors": page_orchestrator_submission_validation["errors"],
        },
        next_allowed_nodes=["scillm_one_shot_page_review"],
    )
    page_transport_run_id: str | None = None
    page_orchestrator_run_request = build_page_orchestrator_run_request(
        case_dir=case_dir,
        page_case=page_case,
        submission=page_orchestrator_submission,
        dag_spec_artifact="scillm_orchestrator_page_dag_spec.json",
        code_root=code_root,
        timeout_s=min(opencode_timeout_s, 45.0),
    )
    write_json(case_dir / "scillm_page_orchestrator_run_request.json", page_orchestrator_run_request)
    page_orchestrator_run_receipt: dict[str, Any] | None = None
    page_orchestrator_run_error: dict[str, Any] | None = None
    if page_orchestrator_mode == "live":
        try:
            page_orchestrator_run_receipt = call_page_orchestrator_run(
                page_orchestrator_run_request,
                base_url=scillm_base_url,
                auth_token=scillm_auth_token,
                caller_skill=caller_skill,
                timeout_s=min(opencode_timeout_s, 45.0),
            )
            write_json(case_dir / "scillm_page_orchestrator_run_receipt.json", page_orchestrator_run_receipt)
        except Exception as exc:  # noqa: BLE001 - live orchestrator registration failures must be ledgered.
            page_orchestrator_run_error = {
                "schema": "pdf_lab.second_pass.substrate_error.v1",
                "node_id": "register_page_orchestrator_run",
                "endpoint": "POST /v1/scillm/opencode/transport/runs",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            write_json(case_dir / "scillm_page_orchestrator_run_error.json", page_orchestrator_run_error)
    page_orchestrator_run_validation = validate_page_orchestrator_run_receipt(
        page_orchestrator_run_receipt,
        mode=page_orchestrator_mode,
        request=page_orchestrator_run_request,
    )
    write_json(case_dir / "scillm_page_orchestrator_run_validation.json", page_orchestrator_run_validation)
    page_transport_run_id = page_orchestrator_run_validation.get("transport_run_id")
    state["page_orchestrator_transport_run_id"] = page_transport_run_id
    write_json(case_dir / "state.json", state)
    receipts.write(
        "register_page_orchestrator_run",
        input_artifacts=[
            "scillm_orchestrator_page_dag_spec.json",
            "scillm_orchestrator_page_dag_spec_validation.json",
            "scillm_orchestrator_page_submission.json",
            "scillm_orchestrator_page_submission_validation.json",
        ],
        output_artifacts=[
            "scillm_page_orchestrator_run_request.json",
            *(["scillm_page_orchestrator_run_receipt.json"] if page_orchestrator_run_receipt is not None else []),
            *(["scillm_page_orchestrator_run_error.json"] if page_orchestrator_run_error is not None else []),
            "scillm_page_orchestrator_run_validation.json",
        ],
        command_or_endpoint="POST /v1/scillm/opencode/transport/runs" if page_orchestrator_mode == "live" else "dry_run:POST /v1/scillm/opencode/transport/runs",
        validator_result={"ok": page_orchestrator_run_validation["ok"], "errors": page_orchestrator_run_validation["errors"]},
        next_allowed_nodes=["scillm_one_shot_page_review"] if page_orchestrator_run_validation["ok"] else ["write_page_terminal_ledger"],
        exit_code=0 if page_orchestrator_run_validation["ok"] else 1,
    )
    if not page_orchestrator_run_validation["ok"]:
        review_validation = {
            "schema": "pdf_lab.second_pass.review_validation.v1",
            "ok": False,
            "errors": ["page_orchestrator_registration_failed"],
            "page_case": {"case_id": page_case["case_id"], "page_number": page_number},
            "candidate_count": len(candidates),
            "expected_candidate_ids": [candidate["candidate_id"] for candidate in candidates],
            "seen_candidate_ids": [],
        }
        write_json(case_dir / "review_validation.json", review_validation)
        terminal = {
            "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
            "case_id": page_case["case_id"],
            "page_number": page_number,
            "terminal_status": "blocked_substrate",
            "reason": "page_orchestrator_registration_failed",
            "allowed_terminal_statuses": sorted(TERMINAL_STATUSES),
            "evidence_artifacts": [
                "state.json",
                "sampled_candidate_manifest.json",
                "page_before.json",
                "page_before.png",
                "page_candidates.png",
                "selected_candidates.json",
                "candidate_presets.json",
                "review_request.json",
                "review_request_validation.json",
                "scillm_orchestrator_page_dag_spec.json",
                "scillm_orchestrator_page_dag_spec_validation.json",
                "scillm_orchestrator_page_submission.json",
                "scillm_orchestrator_page_submission_validation.json",
                "scillm_page_orchestrator_run_request.json",
                *(["scillm_page_orchestrator_run_error.json"] if page_orchestrator_run_error is not None else []),
                "scillm_page_orchestrator_run_validation.json",
                "review_validation.json",
                "review.html",
            ],
            "commit_sha": None,
        }
        return finalize_page_case(case_dir=case_dir, receipts=receipts, state=state, terminal=terminal)

    review_response: dict[str, Any] | None = None
    review_receipt: dict[str, Any] | None = None
    review_error: dict[str, Any] | None = None
    review_preflight: dict[str, Any] | None = None
    review_fixture_artifact: str | None = None
    if review_mode == "live":
        try:
            if scillm_preflight_mode == "live":
                review_preflight = preflight_scillm_surface(
                    base_url=scillm_base_url,
                    auth_token=scillm_auth_token,
                    caller_skill=caller_skill,
                    surface="chat",
                    timeout_s=min(scillm_timeout_s, 15.0),
                )
                write_json(case_dir / "scillm_review_preflight.json", review_preflight)
                receipts.write(
                    "scillm_review_preflight",
                    input_artifacts=["review_request.json"],
                    output_artifacts=["scillm_review_preflight.json"],
                    command_or_endpoint="GET /health/liveliness + GET /v1/scillm/health",
                    validator_result={"ok": review_preflight["ok"], "errors": review_preflight["errors"]},
                    next_allowed_nodes=["scillm_one_shot_page_review"] if review_preflight["ok"] else ["write_page_terminal_ledger"],
                    exit_code=0 if review_preflight["ok"] else 1,
                )
                if not review_preflight["ok"]:
                    raise RuntimeError(f"scillm review preflight failed: {review_preflight['errors']}")
            review_receipt = call_scillm_review(
                review_request,
                base_url=scillm_base_url,
                auth_token=scillm_auth_token,
                caller_skill=caller_skill,
                timeout_s=scillm_timeout_s,
            )
            review_response = review_receipt["review_response"]
            write_json(case_dir / "scillm_review_receipt.json", review_receipt)
            write_json(case_dir / "review_response.json", review_response)
            receipts.write(
                "scillm_one_shot_page_review",
                input_artifacts=["review_request.json"],
                output_artifacts=["scillm_review_receipt.json", "review_response.json"],
                command_or_endpoint="POST /v1/chat/completions",
                validator_result={"ok": True, "http_status": review_receipt["http_status"]},
                next_allowed_nodes=["validate_review_response"],
            )
        except Exception as exc:  # noqa: BLE001 - external substrate failures must be ledgered.
            review_error = {
                "schema": "pdf_lab.second_pass.substrate_error.v1",
                "node_id": "scillm_one_shot_page_review",
                "endpoint": "POST /v1/chat/completions",
                "case_id": page_case["case_id"],
                "page_number": page_number,
                "preflight_artifact": "scillm_review_preflight.json" if review_preflight is not None else None,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            write_json(case_dir / "scillm_review_error.json", review_error)
            receipts.write(
                "scillm_one_shot_page_review",
                input_artifacts=["review_request.json"],
                output_artifacts=["scillm_review_error.json"],
                command_or_endpoint="POST /v1/chat/completions",
                validator_result={"ok": False, "errors": ["scillm_review_call_failed"], "error_type": type(exc).__name__},
                next_allowed_nodes=["validate_review_response"],
                exit_code=1,
            )
    elif review_mode == "fixture":
        try:
            if review_fixture_path is None:
                raise ValueError("review_mode=fixture requires review_fixture_path")
            review_fixture = {
                "schema": "pdf_lab.second_pass.review_fixture_materialized.v1",
                "source_path": str(review_fixture_path),
                "review_response": load_review_fixture(review_fixture_path),
            }
            review_response = review_fixture["review_response"]
            review_fixture_artifact = "review_fixture.json"
            write_json(case_dir / review_fixture_artifact, review_fixture)
            write_json(case_dir / "review_response.json", review_response)
            receipts.write(
                "scillm_one_shot_page_review",
                input_artifacts=["review_request.json", str(review_fixture_path)],
                output_artifacts=[review_fixture_artifact, "review_response.json"],
                command_or_endpoint="fixture:review_response",
                validator_result={"ok": True, "source_path": str(review_fixture_path)},
                next_allowed_nodes=["validate_review_response"],
            )
        except Exception as exc:  # noqa: BLE001 - fixture failures must be ledgered.
            review_error = {
                "schema": "pdf_lab.second_pass.substrate_error.v1",
                "node_id": "scillm_one_shot_page_review",
                "endpoint": "fixture:review_response",
                "case_id": page_case["case_id"],
                "page_number": page_number,
                "preflight_artifact": None,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            write_json(case_dir / "scillm_review_error.json", review_error)
            receipts.write(
                "scillm_one_shot_page_review",
                input_artifacts=["review_request.json"],
                output_artifacts=["scillm_review_error.json"],
                command_or_endpoint="fixture:review_response",
                validator_result={"ok": False, "errors": ["review_fixture_load_failed"], "error_type": type(exc).__name__},
                next_allowed_nodes=["validate_review_response"],
                exit_code=1,
            )
    else:
        receipts.write(
            "scillm_one_shot_page_review",
            input_artifacts=["review_request.json"],
            output_artifacts=[],
            command_or_endpoint="dry_run:POST /v1/chat/completions",
            validator_result={"ok": False, "reason": "dry_run_review_not_executed"},
            next_allowed_nodes=["write_page_terminal_ledger"],
        )

    if review_response is not None:
        review_validation = validate_review_response(
            review_response,
            [candidate["candidate_id"] for candidate in candidates],
            receipt=review_receipt,
            request=review_request,
            page_case=page_case,
        )
    else:
        review_validation = {
            "schema": "pdf_lab.second_pass.review_validation.v1",
            "ok": False,
            "errors": (
                ["review_fixture_load_failed"]
                if review_error is not None and review_error.get("endpoint") == "fixture:review_response"
                else ["scillm_review_call_failed"]
                if review_error is not None
                else ["dry_run_review_not_executed"]
            ),
            "page_case": {"case_id": page_case["case_id"], "page_number": page_number},
            "candidate_count": len(candidates),
            "expected_candidate_ids": [candidate["candidate_id"] for candidate in candidates],
            "seen_candidate_ids": [],
        }
    write_json(case_dir / "review_validation.json", review_validation)
    receipts.write(
        "validate_review_response",
        input_artifacts=["review_response.json"] if review_response is not None else ["review_request.json"],
        output_artifacts=["review_validation.json"],
        command_or_endpoint="run_page_second_pass_dag.validate_review_response",
        validator_result={"ok": review_validation["ok"], "errors": review_validation["errors"]},
        next_allowed_nodes=["route_page_result"],
    )
    if review_error is not None:
        terminal_status, terminal_reason = (
            ("human_needed", "review_fixture_load_failed")
            if review_error.get("endpoint") == "fixture:review_response"
            else ("blocked_substrate", "scillm_review_call_failed")
        )
    else:
        terminal_status, terminal_reason = route_review_result(review_validation, review_response, review_mode)
    patch_baseline: dict[str, Any] | None = None
    patch_delta: dict[str, Any] | None = None
    patch_receipt: dict[str, Any] | None = None
    patch_error: dict[str, Any] | None = None
    patch_validation: dict[str, Any] | None = None
    patch_request: dict[str, Any] | None = None
    patch_scope_validation: dict[str, Any] | None = None
    patch_preflight: dict[str, Any] | None = None
    patch_evidence_workspace: dict[str, Any] | None = None
    patch_evidence_case_dir: Path | None = None
    repair_diagnosis_receipt: dict[str, Any] | None = None
    repair_diagnosis_validation: dict[str, Any] | None = None
    repair_diagnosis_error: dict[str, Any] | None = None
    repair_plan_receipt: dict[str, Any] | None = None
    repair_plan_validation: dict[str, Any] | None = None
    repair_plan_error: dict[str, Any] | None = None
    test_validation: dict[str, Any] | None = None
    after_review_response: dict[str, Any] | None = None
    after_review_error: dict[str, Any] | None = None
    after_review_validation: dict[str, Any] | None = None
    after_review_fixture_artifact: str | None = None
    commit_gate: dict[str, Any] | None = None
    commit_acceptance_gate: dict[str, Any] | None = None
    transport_event_artifacts: list[str] = []
    opencode_host_artifacts: list[str] = []
    prompt_contract_artifacts: list[str] = []
    patch_attempts: list[dict[str, Any]] = []
    patch_attempts_ledger: dict[str, Any] | None = None
    patch_delegate_bug_report_artifact: str | None = None
    if terminal_status == "still_open" and terminal_reason == "defect_patch_not_implemented" and review_response is not None:
        patch_evidence_workspace = materialize_patch_evidence_workspace(case_dir, code_root, str(page_case["case_id"]))
        patch_evidence_case_dir = Path(str(patch_evidence_workspace["workspace_case_dir"]))
        baseline_changed_files = git_changed_files(code_root)
        patch_baseline = {
            "schema": "pdf_lab.second_pass.patch_baseline.v1",
            "changed_files": baseline_changed_files,
            "dirty": bool(baseline_changed_files),
            "patch_evidence_workspace": patch_evidence_workspace,
        }
        write_json(case_dir / "patch_baseline.json", patch_baseline)
        patch_agents = expand_patch_agent_sequence_for_transport_retries(
            normalize_patch_agent_sequence(opencode_agent, opencode_agent_sequence),
            patch_mode=patch_mode,
            patch_backend=patch_backend,
        )
        for attempt_index, attempt_agent in enumerate(patch_agents, start=1):
            attempt_prefix = f"patch_attempt_{attempt_index:02d}_"
            attempt_node_id = f"opencode_patch_attempt_{attempt_index:02d}"
            attempt_transport_artifacts: list[str] = []
            attempt_child_handle_artifacts: list[str] = []
            attempt_diagnosis_transport_artifacts: list[str] = []
            attempt_opencode_host_artifacts: list[str] = []
            attempt_diagnosis_opencode_host_artifacts: list[str] = []
            attempt_error: dict[str, Any] | None = None
            attempt_diagnosis_error: dict[str, Any] | None = None
            patch_receipt = None
            patch_error = None
            attempt_repair_diagnosis_receipt: dict[str, Any] | None = None
            attempt_repair_diagnosis_validation: dict[str, Any] | None = None
            attempt_repair_plan_receipt: dict[str, Any] | None = None
            attempt_repair_plan_validation: dict[str, Any] | None = None
            if repair_strategy == "chat_plan_split":
                repair_plan_request = build_scillm_repair_plan_request(
                    case_dir=case_dir,
                    page_case=page_case,
                    candidates=candidates,
                    review_response=review_response,
                    model=model,
                    batch_id=batch_id,
                )
                repair_plan_request["attempt_index"] = attempt_index
                repair_plan_request["attempt_count"] = len(patch_agents)
                repair_plan_request_artifact = f"{attempt_prefix}repair_plan_request.json"
                write_json(case_dir / repair_plan_request_artifact, repair_plan_request)
                write_json(case_dir / "repair_plan_request.json", repair_plan_request)
                repair_plan_request_validation = validate_repair_plan_request_contract(repair_plan_request)
                repair_plan_request_validation_artifact = f"{attempt_prefix}repair_plan_request_validation.json"
                write_json(case_dir / repair_plan_request_validation_artifact, repair_plan_request_validation)
                write_json(case_dir / "repair_plan_request_validation.json", repair_plan_request_validation)
                receipts.write(
                    f"scillm_repair_plan_attempt_{attempt_index:02d}",
                    input_artifacts=["review_response.json", "review_validation.json", "patch_baseline.json"],
                    output_artifacts=[
                        repair_plan_request_artifact,
                        "repair_plan_request.json",
                        repair_plan_request_validation_artifact,
                        "repair_plan_request_validation.json",
                    ],
                    command_or_endpoint="prepare:POST /v1/chat/completions",
                    validator_result={
                        "ok": repair_plan_request_validation["ok"],
                        "errors": repair_plan_request_validation["errors"],
                        "patch_mode": patch_mode,
                        "agent": attempt_agent,
                        "attempt_index": attempt_index,
                        "repair_strategy": repair_strategy,
                    },
                    next_allowed_nodes=[attempt_node_id] if repair_plan_request_validation["ok"] else ["write_page_terminal_ledger"],
                    exit_code=0 if repair_plan_request_validation["ok"] else 1,
                )
                if patch_mode == "live" and not repair_plan_request_validation["ok"]:
                    repair_plan_error = {
                        "schema": "pdf_lab.second_pass.substrate_error.v1",
                        "node_id": f"scillm_repair_plan_attempt_{attempt_index:02d}",
                        "endpoint": "prepare:POST /v1/chat/completions",
                        "case_id": page_case["case_id"],
                        "page_number": page_number,
                        "attempt_index": attempt_index,
                        "error_type": "RepairPlanRequestValidationFailed",
                        "error": "; ".join(repair_plan_request_validation["errors"]),
                    }
                    write_json(case_dir / f"{attempt_prefix}repair_plan_error.json", repair_plan_error)
                    write_json(case_dir / "repair_plan_error.json", repair_plan_error)
                elif patch_mode == "live":
                    try:
                        attempt_repair_plan_receipt = call_scillm_repair_plan(
                            repair_plan_request,
                            base_url=scillm_base_url,
                            auth_token=scillm_auth_token,
                            caller_skill=caller_skill,
                            timeout_s=scillm_timeout_s,
                        )
                        repair_plan_receipt_artifact = f"{attempt_prefix}repair_plan_receipt.json"
                        write_json(case_dir / repair_plan_receipt_artifact, attempt_repair_plan_receipt)
                        write_json(case_dir / "repair_plan_receipt.json", attempt_repair_plan_receipt)
                    except Exception as exc:  # noqa: BLE001 - live substrate failures must be ledgered.
                        repair_plan_error = {
                            "schema": "pdf_lab.second_pass.substrate_error.v1",
                            "node_id": f"scillm_repair_plan_attempt_{attempt_index:02d}",
                            "endpoint": "POST /v1/chat/completions",
                            "case_id": page_case["case_id"],
                            "page_number": page_number,
                            "attempt_index": attempt_index,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                        write_json(case_dir / f"{attempt_prefix}repair_plan_error.json", repair_plan_error)
                        write_json(case_dir / "repair_plan_error.json", repair_plan_error)
                if repair_plan_error is not None:
                    repair_plan_validation_errors = (
                        ["repair_plan_request_validation_failed", *repair_plan_request_validation["errors"]]
                        if repair_plan_error.get("error_type") == "RepairPlanRequestValidationFailed"
                        else ["repair_plan_call_failed"]
                    )
                    attempt_repair_plan_validation = {
                        "schema": "pdf_lab.second_pass.repair_plan_validation.v1",
                        "ok": False,
                        "errors": repair_plan_validation_errors,
                        "page_case": {"case_id": page_case["case_id"], "page_number": page_number},
                        "candidate_count": len(candidates),
                        "expected_candidate_ids": sorted(candidate["candidate_id"] for candidate in candidates),
                    }
                elif attempt_repair_plan_receipt is None:
                    attempt_repair_plan_validation = {
                        "schema": "pdf_lab.second_pass.repair_plan_validation.v1",
                        "ok": False,
                        "errors": ["repair_plan_dry_run"],
                        "page_case": {"case_id": page_case["case_id"], "page_number": page_number},
                        "candidate_count": len(candidates),
                        "expected_candidate_ids": sorted(candidate["candidate_id"] for candidate in candidates),
                    }
                else:
                    attempt_repair_plan_validation = validate_repair_plan(
                        attempt_repair_plan_receipt.get("repair_plan"),
                        receipt=attempt_repair_plan_receipt,
                        request=repair_plan_request,
                    )
                repair_plan_validation_artifact = f"{attempt_prefix}repair_plan_validation.json"
                write_json(case_dir / repair_plan_validation_artifact, attempt_repair_plan_validation)
                write_json(case_dir / "repair_plan_validation.json", attempt_repair_plan_validation)
                repair_plan_receipt = attempt_repair_plan_receipt
                repair_plan_validation = attempt_repair_plan_validation
                if not attempt_repair_plan_validation["ok"]:
                    patch_validation_errors = validation_error_list(
                        attempt_repair_plan_validation,
                        "repair_plan_validation",
                    ) or ["repair_plan_failed"]
                    patch_validation = {
                        "schema": "pdf_lab.second_pass.patch_delegate_validation.v1",
                        "ok": False,
                        "errors": patch_validation_errors,
                        "patch_status": "repair_plan_failed",
                        "artifacts_present": False,
                    }
                    validation_artifact = f"{attempt_prefix}validation.json"
                    write_json(case_dir / validation_artifact, patch_validation)
                    write_json(case_dir / "patch_validation.json", patch_validation)
                    patch_attempts.append(
                        {
                            "attempt_index": attempt_index,
                            "agent": attempt_agent,
                            "request_artifact": None,
                            "receipt_artifact": None,
                            "error_artifact": None,
                            "validation_artifact": validation_artifact,
                            "repair_plan_request_artifact": repair_plan_request_artifact,
                            "repair_plan_request_validation_artifact": repair_plan_request_validation_artifact,
                            "repair_plan_receipt_artifact": f"{attempt_prefix}repair_plan_receipt.json" if attempt_repair_plan_receipt is not None else None,
                            "repair_plan_error_artifact": f"{attempt_prefix}repair_plan_error.json" if repair_plan_error is not None else None,
                            "repair_plan_validation_artifact": repair_plan_validation_artifact,
                            "ok": False,
                            "errors": validation_error_list(patch_validation, "patch_validation"),
                        }
                    )
                    if patch_mode == "dry_run" or repair_plan_error is not None:
                        break
                    continue
                attempt_repair_diagnosis_receipt = attempt_repair_plan_receipt
                attempt_repair_diagnosis_validation = {
                    "schema": "pdf_lab.second_pass.repair_diagnosis_validation.v1",
                    "ok": True,
                    "errors": [],
                    "diagnosis_status": "completed",
                    "assistant_text_present": True,
                    "source": "scillm_repair_plan",
                    "page_case": {"case_id": page_case["case_id"], "page_number": page_number},
                    "candidate_count": len(candidates),
                    "expected_candidate_ids": sorted(candidate["candidate_id"] for candidate in candidates),
                }
            if repair_strategy == "split":
                if patch_backend == "scillm_orchestrator":
                    diagnosis_request = build_scillm_orchestrator_repair_diagnosis_request(
                        case_dir=case_dir,
                        page_case=page_case,
                        candidates=candidates,
                        review_response=review_response,
                        agent=attempt_agent,
                        opencode_model=effective_opencode_model,
                        skills=opencode_skills or DEFAULT_OPENCODE_SKILLS,
                        timeout_s=opencode_timeout_s,
                        cwd=code_root,
                        prompt_profile=patch_prompt_profile,
                        transport_run_id=page_transport_run_id,
                    )
                    diagnosis_endpoint = "prepare:POST /v1/scillm/opencode/transport/runs + children + message"
                else:
                    diagnosis_request = build_opencode_repair_diagnosis_request(
                        case_dir=case_dir,
                        page_case=page_case,
                        candidates=candidates,
                        review_response=review_response,
                        agent=attempt_agent,
                        opencode_model=effective_opencode_model,
                        skills=opencode_skills or DEFAULT_OPENCODE_SKILLS,
                        timeout_s=opencode_timeout_s,
                        cleanup_session=opencode_cleanup_session,
                        cwd=code_root,
                        prompt_profile=patch_prompt_profile,
                    )
                    diagnosis_endpoint = "prepare:POST /v1/scillm/opencode/runs"
                diagnosis_request["attempt_index"] = attempt_index
                diagnosis_request["attempt_count"] = len(patch_agents)
                diagnosis_request["attempt_agent_sequence"] = patch_agents
                diagnosis_request["scillm_metadata"]["attempt_index"] = attempt_index
                diagnosis_request["scillm_metadata"]["attempt_count"] = len(patch_agents)
                diagnosis_request["scillm_metadata"]["agent"] = attempt_agent
                diagnosis_request_artifact = f"{attempt_prefix}diagnosis_request.json"
                write_json(case_dir / diagnosis_request_artifact, diagnosis_request)
                write_json(case_dir / "repair_diagnosis_request.json", diagnosis_request)
                receipts.write(
                    f"repair_diagnosis_attempt_{attempt_index:02d}",
                    input_artifacts=["review_response.json", "review_validation.json", "patch_baseline.json"],
                    output_artifacts=[diagnosis_request_artifact, "repair_diagnosis_request.json"],
                    command_or_endpoint=diagnosis_endpoint,
                    validator_result={
                        "ok": True,
                        "patch_mode": patch_mode,
                        "patch_backend": patch_backend,
                        "agent": attempt_agent,
                        "attempt_index": attempt_index,
                        "repair_strategy": repair_strategy,
                        "patch_prompt_profile": patch_prompt_profile,
                    },
                    next_allowed_nodes=[attempt_node_id],
                )
                if patch_mode == "live":
                    try:
                        if scillm_preflight_mode == "live" and patch_preflight is None:
                            patch_preflight = preflight_scillm_surface(
                                base_url=scillm_base_url,
                                auth_token=scillm_auth_token,
                                caller_skill=caller_skill,
                                surface="opencode_transport" if patch_backend == "scillm_orchestrator" else "opencode_serve",
                                timeout_s=min(opencode_timeout_s, 15.0),
                            )
                            write_json(case_dir / "scillm_patch_preflight.json", patch_preflight)
                            receipts.write(
                                "scillm_patch_preflight",
                                input_artifacts=[diagnosis_request_artifact],
                                output_artifacts=["scillm_patch_preflight.json"],
                                command_or_endpoint=(
                                    "GET /health/liveliness + GET /v1/scillm/opencode/transport/capabilities"
                                    if patch_backend == "scillm_orchestrator"
                                    else "GET /health/liveliness + GET /v1/scillm/health"
                                ),
                                validator_result={"ok": patch_preflight["ok"], "errors": patch_preflight["errors"]},
                                next_allowed_nodes=[f"repair_diagnosis_attempt_{attempt_index:02d}"] if patch_preflight["ok"] else ["write_page_terminal_ledger"],
                                exit_code=0 if patch_preflight["ok"] else 1,
                            )
                            if not patch_preflight["ok"]:
                                raise RuntimeError(f"scillm patch preflight failed: {patch_preflight['errors']}")
                        if patch_backend == "scillm_orchestrator":
                            attempt_repair_diagnosis_receipt = call_scillm_orchestrator_patch(
                                diagnosis_request,
                                base_url=scillm_base_url,
                                auth_token=scillm_auth_token,
                                caller_skill=caller_skill,
                                timeout_s=opencode_timeout_s + 30,
                            )
                        else:
                            attempt_repair_diagnosis_receipt = call_opencode_patch(
                                diagnosis_request,
                                base_url=scillm_base_url,
                                auth_token=scillm_auth_token,
                                caller_skill=caller_skill,
                                timeout_s=opencode_timeout_s + 30,
                            )
                        diagnosis_receipt_artifact = f"{attempt_prefix}diagnosis_receipt.json"
                        write_json(case_dir / diagnosis_receipt_artifact, attempt_repair_diagnosis_receipt)
                        write_json(case_dir / "repair_diagnosis_receipt.json", attempt_repair_diagnosis_receipt)
                        attempt_diagnosis_opencode_host_artifacts = materialize_opencode_host_artifacts(
                            case_dir,
                            attempt_repair_diagnosis_receipt,
                            prefix=f"{attempt_prefix}diagnosis_",
                        )
                        opencode_host_artifacts.extend(attempt_diagnosis_opencode_host_artifacts)
                        if attempt_repair_diagnosis_receipt.get("schema") == "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1":
                            attempt_diagnosis_transport_artifacts = write_transport_event_artifacts(
                                case_dir,
                                attempt_repair_diagnosis_receipt.get("event_stream") or {},
                                prefix=f"{attempt_prefix}diagnosis_",
                            )
                    except Exception as exc:  # noqa: BLE001 - diagnosis substrate failures must be ledgered.
                        attempt_diagnosis_error = {
                            "schema": "pdf_lab.second_pass.substrate_error.v1",
                            "node_id": f"repair_diagnosis_attempt_{attempt_index:02d}",
                            "endpoint": diagnosis_request["endpoint"],
                            "case_id": page_case["case_id"],
                            "page_number": page_number,
                            "patch_backend": patch_backend,
                            "agent": attempt_agent,
                            "attempt_index": attempt_index,
                            "preflight_artifact": "scillm_patch_preflight.json" if patch_preflight is not None else None,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                        repair_diagnosis_error = attempt_diagnosis_error
                        write_json(case_dir / f"{attempt_prefix}diagnosis_error.json", attempt_diagnosis_error)
                        write_json(case_dir / "repair_diagnosis_error.json", attempt_diagnosis_error)
                if attempt_diagnosis_error is not None:
                    attempt_repair_diagnosis_validation = {
                        "schema": "pdf_lab.second_pass.repair_diagnosis_validation.v1",
                        "ok": False,
                        "errors": ["repair_diagnosis_call_failed"],
                        "diagnosis_status": "substrate_error",
                        "assistant_text_present": False,
                        "page_case": {"case_id": page_case["case_id"], "page_number": page_number},
                        "candidate_count": len(candidates),
                        "expected_candidate_ids": sorted(candidate["candidate_id"] for candidate in candidates),
                    }
                else:
                    attempt_repair_diagnosis_validation = validate_repair_diagnosis_delegate_receipt(
                        attempt_repair_diagnosis_receipt,
                        patch_mode=patch_mode,
                        request=diagnosis_request,
                    )
                diagnosis_validation_artifact = f"{attempt_prefix}diagnosis_validation.json"
                write_json(case_dir / diagnosis_validation_artifact, attempt_repair_diagnosis_validation)
                write_json(case_dir / "repair_diagnosis_validation.json", attempt_repair_diagnosis_validation)
                repair_diagnosis_receipt = attempt_repair_diagnosis_receipt
                repair_diagnosis_validation = attempt_repair_diagnosis_validation
                if not attempt_repair_diagnosis_validation["ok"]:
                    patch_validation_errors = validation_error_list(
                        attempt_repair_diagnosis_validation,
                        "repair_diagnosis_validation",
                    ) or ["repair_diagnosis_failed"]
                    patch_validation = {
                        "schema": "pdf_lab.second_pass.patch_delegate_validation.v1",
                        "ok": False,
                        "errors": patch_validation_errors,
                        "patch_status": attempt_repair_diagnosis_validation.get("diagnosis_status") or "diagnosis_failed",
                        "artifacts_present": False,
                    }
                    validation_artifact = f"{attempt_prefix}validation.json"
                    write_json(case_dir / validation_artifact, patch_validation)
                    write_json(case_dir / "patch_validation.json", patch_validation)
                    patch_attempts.append(
                        {
                            "attempt_index": attempt_index,
                            "agent": attempt_agent,
                            "request_artifact": None,
                            "receipt_artifact": None,
                            "error_artifact": None,
                            "validation_artifact": validation_artifact,
                            "diagnosis_request_artifact": diagnosis_request_artifact,
                            "diagnosis_receipt_artifact": f"{attempt_prefix}diagnosis_receipt.json" if attempt_repair_diagnosis_receipt is not None else None,
                            "diagnosis_error_artifact": f"{attempt_prefix}diagnosis_error.json" if attempt_diagnosis_error is not None else None,
                            "diagnosis_validation_artifact": diagnosis_validation_artifact,
                            "transport_event_artifacts": attempt_diagnosis_transport_artifacts,
                            "opencode_host_artifacts": attempt_diagnosis_opencode_host_artifacts,
                            "ok": False,
                            "errors": validation_error_list(patch_validation, "patch_validation"),
                        }
                    )
                    if patch_mode == "dry_run" or attempt_diagnosis_error is not None:
                        break
                    continue
            if patch_backend == "scillm_orchestrator":
                transport_retry_fresh_parent = attempt_index > 1 and attempt_agent == patch_agents[attempt_index - 2]
                patch_request = build_scillm_orchestrator_patch_request(
                    case_dir=case_dir,
                    evidence_case_dir=patch_evidence_case_dir,
                    page_case=page_case,
                    candidates=candidates,
                    review_response=review_response,
                    agent=attempt_agent,
                    opencode_model=effective_opencode_model,
                    skills=opencode_skills or DEFAULT_OPENCODE_SKILLS,
                    timeout_s=opencode_timeout_s,
                    cwd=code_root,
                    prompt_profile=patch_prompt_profile,
                    repair_diagnosis=attempt_repair_diagnosis_receipt if repair_strategy in {"split", "chat_plan_split"} else None,
                    transport_run_id=None if transport_retry_fresh_parent else page_transport_run_id,
                )
                patch_endpoint = "prepare:POST /v1/scillm/opencode/transport/runs + children + message"
            else:
                transport_retry_fresh_parent = False
                patch_request = build_opencode_patch_request(
                    case_dir=case_dir,
                    evidence_case_dir=patch_evidence_case_dir,
                    page_case=page_case,
                    candidates=candidates,
                    review_response=review_response,
                    agent=attempt_agent,
                    opencode_model=effective_opencode_model,
                    skills=opencode_skills or DEFAULT_OPENCODE_SKILLS,
                    timeout_s=opencode_timeout_s,
                    cleanup_session=opencode_cleanup_session,
                    cwd=code_root,
                    prompt_profile=patch_prompt_profile,
                    repair_diagnosis=attempt_repair_diagnosis_receipt if repair_strategy in {"split", "chat_plan_split"} else None,
                )
                patch_endpoint = "prepare:POST /v1/scillm/opencode/runs"
            patch_request["attempt_index"] = attempt_index
            patch_request["attempt_count"] = len(patch_agents)
            patch_request["attempt_agent_sequence"] = patch_agents
            patch_request["transport_retry_fresh_parent"] = transport_retry_fresh_parent
            patch_request["scillm_metadata"]["attempt_index"] = attempt_index
            patch_request["scillm_metadata"]["attempt_count"] = len(patch_agents)
            patch_request["scillm_metadata"]["agent"] = attempt_agent
            patch_request["scillm_metadata"]["transport_retry_fresh_parent"] = transport_retry_fresh_parent
            request_artifact = f"{attempt_prefix}request.json"
            write_json(case_dir / request_artifact, patch_request)
            write_json(case_dir / "patch_request.json", patch_request)
            prompt_contract, attempt_prompt_contract_artifacts = write_patch_prompt_contract_artifacts(
                case_dir,
                patch_request,
                artifact_prefix=attempt_prefix,
                live_patch_required=patch_mode == "live",
                expected_page_case=page_case,
            )
            prompt_contract_artifacts.extend(attempt_prompt_contract_artifacts)
            receipts.write(
                attempt_node_id,
                input_artifacts=["review_response.json", "review_validation.json", "patch_baseline.json"],
                output_artifacts=[request_artifact, "patch_request.json", *attempt_prompt_contract_artifacts],
                command_or_endpoint=patch_endpoint,
                validator_result={
                    "ok": prompt_contract["ok"] or patch_mode != "live",
                    "patch_mode": patch_mode,
                    "patch_backend": patch_backend,
                    "agent": attempt_agent,
                    "attempt_index": attempt_index,
                    "attempt_count": len(patch_agents),
                    "opencode_model": effective_opencode_model,
                    "requested_opencode_model": opencode_model,
                    "opencode_model_defaulted": effective_opencode_model is not None and opencode_model is None,
                    "patch_prompt_profile": patch_prompt_profile,
                    "repair_strategy": repair_strategy,
                    "baseline_dirty": patch_baseline["dirty"],
                    "baseline_changed_count": len(baseline_changed_files),
                    "prompt_contract_ok": prompt_contract["ok"],
                    "prompt_contract_errors": prompt_contract["errors"],
                },
                next_allowed_nodes=["validate_patch_scope"] if prompt_contract["ok"] or patch_mode != "live" else ["write_page_terminal_ledger"],
                exit_code=0 if prompt_contract["ok"] or patch_mode != "live" else 1,
            )
            if patch_mode == "live" and not prompt_contract["ok"]:
                patch_validation = {
                    "schema": "pdf_lab.second_pass.patch_delegate_validation.v1",
                    "ok": False,
                    "errors": ["patch_prompt_contract_failed", *prompt_contract.get("errors", [])],
                    "patch_status": "prompt_contract_failed",
                    "artifacts_present": False,
                    "prompt_contract_artifacts": attempt_prompt_contract_artifacts,
                }
                validation_artifact = f"{attempt_prefix}validation.json"
                write_json(case_dir / validation_artifact, patch_validation)
                write_json(case_dir / "patch_validation.json", patch_validation)
                patch_attempts.append(
                    {
                        "attempt_index": attempt_index,
                        "agent": attempt_agent,
                        "request_artifact": request_artifact,
                        "receipt_artifact": None,
                        "error_artifact": None,
                        "validation_artifact": validation_artifact,
                        "prompt_contract_artifacts": attempt_prompt_contract_artifacts,
                        "transport_event_artifacts": attempt_transport_artifacts,
                        "opencode_child_handle_artifacts": attempt_child_handle_artifacts,
                        "diagnosis_request_artifact": f"{attempt_prefix}diagnosis_request.json" if repair_strategy == "split" else None,
                        "diagnosis_receipt_artifact": (
                            f"{attempt_prefix}diagnosis_receipt.json"
                            if attempt_repair_diagnosis_receipt is not None and repair_strategy == "split"
                            else None
                        ),
                        "diagnosis_error_artifact": f"{attempt_prefix}diagnosis_error.json" if attempt_diagnosis_error is not None else None,
                        "diagnosis_validation_artifact": f"{attempt_prefix}diagnosis_validation.json" if attempt_repair_diagnosis_validation is not None else None,
                        "repair_plan_request_artifact": f"{attempt_prefix}repair_plan_request.json" if repair_strategy == "chat_plan_split" else None,
                        "repair_plan_request_validation_artifact": (
                            f"{attempt_prefix}repair_plan_request_validation.json" if repair_strategy == "chat_plan_split" else None
                        ),
                        "repair_plan_receipt_artifact": f"{attempt_prefix}repair_plan_receipt.json" if attempt_repair_plan_receipt is not None else None,
                        "repair_plan_error_artifact": f"{attempt_prefix}repair_plan_error.json" if repair_plan_error is not None else None,
                        "repair_plan_validation_artifact": f"{attempt_prefix}repair_plan_validation.json" if attempt_repair_plan_validation is not None else None,
                        "opencode_host_artifacts": attempt_opencode_host_artifacts,
                        "ok": False,
                        "errors": validation_error_list(patch_validation, "patch_validation"),
                    }
                )
                break
            if patch_mode == "live":
                try:
                    if scillm_preflight_mode == "live" and patch_preflight is None:
                        patch_preflight = preflight_scillm_surface(
                            base_url=scillm_base_url,
                            auth_token=scillm_auth_token,
                            caller_skill=caller_skill,
                            surface="opencode_transport" if patch_backend == "scillm_orchestrator" else "opencode_serve",
                            timeout_s=min(opencode_timeout_s, 15.0),
                        )
                        write_json(case_dir / "scillm_patch_preflight.json", patch_preflight)
                        receipts.write(
                            "scillm_patch_preflight",
                            input_artifacts=[request_artifact],
                            output_artifacts=["scillm_patch_preflight.json"],
                            command_or_endpoint=(
                                "GET /health/liveliness + GET /v1/scillm/opencode/transport/capabilities"
                                if patch_backend == "scillm_orchestrator"
                                else "GET /health/liveliness + GET /v1/scillm/health"
                            ),
                            validator_result={"ok": patch_preflight["ok"], "errors": patch_preflight["errors"]},
                            next_allowed_nodes=[attempt_node_id] if patch_preflight["ok"] else ["write_page_terminal_ledger"],
                            exit_code=0 if patch_preflight["ok"] else 1,
                        )
                        if not patch_preflight["ok"]:
                            raise RuntimeError(f"scillm patch preflight failed: {patch_preflight['errors']}")
                    if patch_backend == "scillm_orchestrator":
                        patch_receipt = call_scillm_orchestrator_patch(
                            patch_request,
                            base_url=scillm_base_url,
                            auth_token=scillm_auth_token,
                            caller_skill=caller_skill,
                            timeout_s=opencode_timeout_s + 30,
                        )
                    else:
                        patch_receipt, attempt_child_handle_artifacts = call_opencode_patch_observable(
                            patch_request,
                            base_url=scillm_base_url,
                            auth_token=scillm_auth_token,
                            caller_skill=caller_skill,
                            timeout_s=opencode_timeout_s + 30,
                            case_dir=case_dir,
                            artifact_prefix=attempt_prefix,
                        )
                    receipt_artifact = f"{attempt_prefix}receipt.json"
                    write_json(case_dir / receipt_artifact, patch_receipt)
                    write_json(case_dir / "patch_receipt.json", patch_receipt)
                    attempt_opencode_host_artifacts = materialize_opencode_host_artifacts(
                        case_dir,
                        patch_receipt,
                        prefix=attempt_prefix,
                    )
                    opencode_host_artifacts.extend(attempt_opencode_host_artifacts)
                    if patch_receipt.get("schema") == "pdf_lab.second_pass.scillm_orchestrator_patch_receipt.v1":
                        attempt_transport_artifacts = write_transport_event_artifacts(
                            case_dir,
                            patch_receipt.get("event_stream") or {},
                            prefix=attempt_prefix,
                        )
                        transport_event_artifacts = write_transport_event_artifacts(
                            case_dir,
                            patch_receipt.get("event_stream") or {},
                        )
                except Exception as exc:  # noqa: BLE001 - external substrate failures must be ledgered.
                    if not attempt_child_handle_artifacts:
                        child_handle_artifact = f"{attempt_prefix}opencode_child_run_handle.json"
                        if (case_dir / child_handle_artifact).is_file():
                            attempt_child_handle_artifacts = [child_handle_artifact]
                    attempt_error = {
                        "schema": "pdf_lab.second_pass.substrate_error.v1",
                        "node_id": attempt_node_id,
                        "endpoint": patch_request["endpoint"],
                        "case_id": page_case["case_id"],
                        "page_number": page_number,
                        "patch_backend": patch_backend,
                        "agent": attempt_agent,
                        "attempt_index": attempt_index,
                        "preflight_artifact": "scillm_patch_preflight.json" if patch_preflight is not None else None,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                    patch_error = attempt_error
                    write_json(case_dir / f"{attempt_prefix}error.json", attempt_error)
                    write_json(case_dir / "patch_error.json", attempt_error)
            if attempt_error is not None:
                patch_validation = {
                    "schema": "pdf_lab.second_pass.patch_delegate_validation.v1",
                    "ok": False,
                    "errors": ["patch_delegate_call_failed"],
                    "patch_status": "substrate_error",
                    "artifacts_present": False,
                }
            else:
                patch_validation = validate_patch_delegate_receipt(
                    patch_receipt,
                    patch_mode=patch_mode,
                    request=patch_request,
                )
            validation_artifact = f"{attempt_prefix}validation.json"
            write_json(case_dir / validation_artifact, patch_validation)
            write_json(case_dir / "patch_validation.json", patch_validation)
            patch_attempts.append(
                {
                    "attempt_index": attempt_index,
                    "agent": attempt_agent,
                    "request_artifact": request_artifact,
                    "receipt_artifact": f"{attempt_prefix}receipt.json" if patch_receipt is not None else None,
                    "error_artifact": f"{attempt_prefix}error.json" if attempt_error is not None else None,
                    "validation_artifact": validation_artifact,
                    "prompt_contract_artifacts": attempt_prompt_contract_artifacts,
                    "transport_event_artifacts": attempt_transport_artifacts,
                    "opencode_child_handle_artifacts": attempt_child_handle_artifacts,
                    "transport_retry_fresh_parent": transport_retry_fresh_parent,
                    "diagnosis_request_artifact": f"{attempt_prefix}diagnosis_request.json" if repair_strategy == "split" else None,
                    "diagnosis_receipt_artifact": (
                        f"{attempt_prefix}diagnosis_receipt.json"
                        if repair_strategy == "split" and attempt_repair_diagnosis_receipt is not None
                        else None
                    ),
                    "diagnosis_error_artifact": f"{attempt_prefix}diagnosis_error.json" if attempt_diagnosis_error is not None else None,
                    "diagnosis_validation_artifact": f"{attempt_prefix}diagnosis_validation.json" if attempt_repair_diagnosis_validation is not None else None,
                    "diagnosis_transport_event_artifacts": attempt_diagnosis_transport_artifacts,
                    "opencode_host_artifacts": attempt_opencode_host_artifacts,
                    "diagnosis_opencode_host_artifacts": attempt_diagnosis_opencode_host_artifacts,
                    "repair_plan_request_artifact": f"{attempt_prefix}repair_plan_request.json" if repair_strategy == "chat_plan_split" else None,
                    "repair_plan_request_validation_artifact": (
                        f"{attempt_prefix}repair_plan_request_validation.json" if repair_strategy == "chat_plan_split" else None
                    ),
                    "repair_plan_receipt_artifact": f"{attempt_prefix}repair_plan_receipt.json" if attempt_repair_plan_receipt is not None else None,
                    "repair_plan_validation_artifact": f"{attempt_prefix}repair_plan_validation.json" if attempt_repair_plan_validation is not None else None,
                    "ok": patch_validation["ok"],
                    "errors": validation_error_list(patch_validation, "patch_validation"),
                }
            )
            if patch_validation["ok"] or patch_mode == "dry_run" or attempt_error is not None and patch_preflight is not None and not patch_preflight.get("ok"):
                break
            if (
                patch_backend == "scillm_orchestrator"
                and patch_mode == "live"
                and not patch_validation_has_recoverable_transport_failure(patch_validation)
            ):
                break
        patch_attempts_ledger = {
            "schema": "pdf_lab.second_pass.patch_attempts_ledger.v1",
            "page_case": {
                "case_id": page_case["case_id"],
                "page_number": page_number,
            },
            "candidate_count": len(candidates),
            "candidate_ids": [candidate["candidate_id"] for candidate in candidates],
            "patch_backend": patch_backend,
            "patch_mode": patch_mode,
            "patch_prompt_profile": patch_prompt_profile,
            "repair_strategy": repair_strategy,
            "agent_sequence": patch_agents,
            "attempt_count": len(patch_attempts),
            "selected_attempt_index": next((item["attempt_index"] for item in patch_attempts if item["ok"]), None),
            "attempts": patch_attempts,
        }
        write_json(case_dir / "patch_attempts_ledger.json", patch_attempts_ledger)
        write_json(case_dir / "patch_validation.json", patch_validation)
        patch_request_inputs = ["patch_request.json"] if (case_dir / "patch_request.json").exists() else []
        receipts.write(
            "validate_patch_scope",
            input_artifacts=patch_request_inputs
            + (["repair_diagnosis_request.json", "repair_diagnosis_validation.json"] if repair_diagnosis_validation is not None else [])
            + (["repair_diagnosis_receipt.json"] if repair_diagnosis_receipt is not None else [])
            + (["repair_diagnosis_error.json"] if repair_diagnosis_error is not None else [])
            + (
                ["repair_plan_request.json", "repair_plan_request_validation.json", "repair_plan_validation.json"]
                if repair_plan_validation is not None
                else []
            )
            + (["repair_plan_receipt.json"] if repair_plan_receipt is not None else [])
            + (["repair_plan_error.json"] if repair_plan_error is not None else [])
            + ["patch_attempts_ledger.json"]
            + prompt_contract_artifacts
            + [
                artifact
                for attempt in patch_attempts
                for artifact in attempt.get("opencode_child_handle_artifacts", [])
                if isinstance(artifact, str) and artifact
            ]
            + (["scillm_patch_preflight.json"] if patch_preflight is not None else [])
            + (["patch_receipt.json"] if patch_receipt is not None else [])
            + opencode_host_artifacts
            + transport_event_artifacts
            + (["patch_error.json"] if patch_error is not None else []),
            output_artifacts=["patch_validation.json"],
            command_or_endpoint="run_page_second_pass_dag.validate_patch_delegate_receipt",
            validator_result={"ok": patch_validation["ok"], "errors": patch_validation["errors"]},
            next_allowed_nodes=["validate_patch_file_scope"] if patch_validation["ok"] else ["write_page_terminal_ledger"],
        )
        if patch_mode == "dry_run":
            if repair_strategy == "chat_plan_split" and repair_plan_validation is not None:
                terminal_status, terminal_reason = "still_open", "repair_plan_dry_run"
            elif repair_strategy == "split" and repair_diagnosis_validation is not None:
                terminal_status, terminal_reason = "still_open", "repair_diagnosis_dry_run"
            else:
                terminal_status, terminal_reason = "still_open", "patch_delegate_dry_run"
        elif patch_validation["ok"]:
            patch_delegate_bytecode_cleanup = cleanup_python_bytecode_caches(code_root)
            after_changed_files = git_changed_files(code_root)
            patch_delta = compute_patch_delta(baseline_changed_files, after_changed_files)
            patch_delta["patch_delegate_bytecode_cleanup"] = patch_delegate_bytecode_cleanup
            write_json(case_dir / "patch_delta.json", patch_delta)
            if not patch_delta["ok"]:
                patch_scope_validation = {
                    "schema": "pdf_lab.second_pass.patch_scope_validation.v1",
                    "ok": False,
                    "errors": patch_delta["errors"],
                    "changed_files": [],
                    "allowed_prefixes": allowed_patch_prefixes or DEFAULT_ALLOWED_PATCH_PREFIXES,
                    "test_files": [],
                }
            else:
                patch_scope_validation = validate_patch_scope(
                    patch_delta["patch_changed_files"],
                    allowed_patch_prefixes or DEFAULT_ALLOWED_PATCH_PREFIXES,
                    patch_validation.get("applied_claim") if patch_validation else None,
                )
            write_json(case_dir / "patch_scope_validation.json", patch_scope_validation)
            receipts.write(
                "validate_patch_file_scope",
                input_artifacts=["patch_validation.json", "patch_baseline.json"],
                output_artifacts=["patch_delta.json", "patch_scope_validation.json"],
                command_or_endpoint="git diff --name-only + git ls-files --others + run_page_second_pass_dag.compute_patch_delta + validate_patch_scope",
                validator_result={"ok": patch_scope_validation["ok"], "errors": patch_scope_validation["errors"]},
                next_allowed_nodes=["run_page_targeted_tests"] if patch_scope_validation["ok"] else ["write_page_terminal_ledger"],
            )
            if patch_scope_validation["ok"]:
                test_validation = run_validation_commands(
                    validation_commands or [],
                    code_root,
                    required_test_files=patch_scope_validation.get("test_files") or [],
                )
                write_json(case_dir / "test_validation.json", test_validation)
                receipts.write(
                    "run_page_targeted_tests",
                    input_artifacts=["patch_scope_validation.json"],
                    output_artifacts=["test_validation.json"],
                    command_or_endpoint="run_page_second_pass_dag.run_validation_commands",
                    validator_result={"ok": test_validation["ok"], "errors": test_validation["errors"]},
                    next_allowed_nodes=["reextract_page_after_patch"] if test_validation["ok"] else ["write_page_terminal_ledger"],
                )
                if test_validation["ok"]:
                    page_after = run_extract_page_for_code_root(
                        pdf_path=pdf_path,
                        page_number=page_number,
                        ledger_path=ledger_path,
                        apply_mode=apply_mode,
                        code_root=code_root,
                        page_extract_timeout_s=page_extract_timeout_s,
                    )
                    write_json(case_dir / "page_after.json", page_after)
                    render_original_page(pdf_path, page_number, case_dir / "page_after.png", dpi)
                    render_candidate_overlay(pdf_path, page_number, candidates, case_dir / "page_after_candidates.png", dpi)
                    receipts.write(
                        "reextract_page_after_patch",
                        input_artifacts=["test_validation.json"],
                        output_artifacts=["page_after.json", "page_after.png", "page_after_candidates.png"],
                        command_or_endpoint="snapshot_current_extraction._extract_page",
                        validator_result={"ok": True, "block_count": len(page_after.get("blocks") or [])},
                        next_allowed_nodes=["rerun_page_review_after_patch"],
                    )
                    after_case = dict(page_case)
                    after_case["case_id"] = f"{page_case['case_id']}:after_patch"
                    after_review_request = build_review_request(
                        case_dir=case_dir,
                        page_case=after_case,
                        page_json_path="page_after.json",
                        original_image_path="page_after.png",
                        annotated_image_path="page_after_candidates.png",
                        candidate_presets_path="candidate_presets.json",
                        model=model,
                        batch_id=batch_id,
                    )
                    write_json(case_dir / "review_after_request.json", after_review_request)
                    after_review_request_validation = validate_review_request_contract(case_dir, after_review_request)
                    write_json(case_dir / "review_after_request_validation.json", after_review_request_validation)
                    after_review_receipt: dict[str, Any] | None = None
                    if review_after_fixture_path is not None:
                        try:
                            after_review_fixture = {
                                "schema": "pdf_lab.second_pass.review_after_fixture_materialized.v1",
                                "source_path": str(review_after_fixture_path),
                                "review_response": load_review_fixture(review_after_fixture_path),
                            }
                            after_review_response = after_review_fixture["review_response"]
                            after_review_fixture_artifact = "review_after_fixture.json"
                            write_json(case_dir / after_review_fixture_artifact, after_review_fixture)
                            write_json(case_dir / "review_after_response.json", after_review_response)
                        except Exception as exc:  # noqa: BLE001 - fixture failures must be ledgered.
                            after_review_error = {
                                "schema": "pdf_lab.second_pass.substrate_error.v1",
                                "node_id": "rerun_page_review_after_patch",
                                "endpoint": "fixture:review_after_response",
                                "case_id": page_case["case_id"],
                                "page_number": page_number,
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            }
                            write_json(case_dir / "scillm_after_review_error.json", after_review_error)
                    elif review_mode == "live":
                        try:
                            after_review_receipt = call_scillm_review(
                                after_review_request,
                                base_url=scillm_base_url,
                                auth_token=scillm_auth_token,
                                caller_skill=caller_skill,
                                timeout_s=scillm_timeout_s,
                            )
                            after_review_response = after_review_receipt["review_response"]
                            write_json(case_dir / "scillm_after_review_receipt.json", after_review_receipt)
                            write_json(case_dir / "review_after_response.json", after_review_response)
                        except Exception as exc:  # noqa: BLE001 - external substrate failures must be ledgered.
                            after_review_error = {
                                "schema": "pdf_lab.second_pass.substrate_error.v1",
                                "node_id": "rerun_page_review_after_patch",
                                "endpoint": "POST /v1/chat/completions",
                                "case_id": page_case["case_id"],
                                "page_number": page_number,
                                "error_type": type(exc).__name__,
                                "error": str(exc),
                            }
                            write_json(case_dir / "scillm_after_review_error.json", after_review_error)
                    after_review_validation = validate_review_response(
                        after_review_response,
                        [candidate["candidate_id"] for candidate in candidates],
                        receipt=after_review_receipt,
                        request=after_review_request,
                        page_case=after_case,
                    ) if after_review_response is not None else {
                        "schema": "pdf_lab.second_pass.review_validation.v1",
                        "ok": False,
                        "errors": ["after_review_call_failed"] if after_review_error is not None else ["after_review_not_executed"],
                        "page_case": {"case_id": after_case["case_id"], "page_number": after_case["page_number"]},
                        "candidate_count": len(candidates),
                        "expected_candidate_ids": [candidate["candidate_id"] for candidate in candidates],
                        "seen_candidate_ids": [],
                    }
                    write_json(case_dir / "review_after_validation.json", after_review_validation)
                    receipts.write(
                        "rerun_page_review_after_patch",
                        input_artifacts=["review_after_request.json", "review_after_request_validation.json"],
                        output_artifacts=[
                            "review_after_request_validation.json",
                            "review_after_validation.json",
                            *(["review_after_fixture.json", "review_after_response.json"] if after_review_fixture_artifact is not None else []),
                            *(
                                ["scillm_after_review_receipt.json", "review_after_response.json"]
                                if after_review_response is not None and after_review_fixture_artifact is None
                                else []
                            ),
                            *(["scillm_after_review_error.json"] if after_review_error is not None else []),
                        ],
                        command_or_endpoint="fixture:review_after_response" if after_review_fixture_artifact is not None else "POST /v1/chat/completions",
                        validator_result={
                            "ok": after_review_request_validation["ok"] and after_review_validation["ok"],
                            "request_errors": after_review_request_validation["errors"],
                            "errors": after_review_validation["errors"],
                        },
                        next_allowed_nodes=["deterministic_page_closure_gate"],
                    )
                    if after_review_error is not None:
                        after_status, after_reason = "blocked_substrate", "after_review_call_failed"
                    elif not after_review_request_validation["ok"]:
                        after_status, after_reason = "still_open", "after_review_request_validation_failed"
                    else:
                        after_status, after_reason = route_review_result(
                            after_review_validation,
                            after_review_response,
                            "live" if after_review_fixture_artifact is not None else review_mode,
                        )
                    closure_ok = after_review_request_validation["ok"] and after_status == "reviewed_clean"
                    receipts.write(
                        "deterministic_page_closure_gate",
                        input_artifacts=[
                            "patch_delta.json",
                            "patch_scope_validation.json",
                            "test_validation.json",
                            "page_after.json",
                            "review_after_request_validation.json",
                            "review_after_validation.json",
                        ],
                        output_artifacts=[],
                        command_or_endpoint="run_page_second_pass_dag.deterministic_page_closure_gate",
                        validator_result={
                            "ok": closure_ok,
                            "after_status": after_status,
                            "after_reason": after_reason,
                            "after_review_request_errors": after_review_request_validation["errors"],
                            "after_review_source": "fixture" if after_review_fixture_artifact is not None else review_mode,
                        },
                        next_allowed_nodes=["commit_page_bug_fix"] if closure_ok else ["write_page_terminal_ledger"],
                    )
                    if closure_ok:
                        commit_gate = create_patch_commit(
                            commit_mode=commit_mode,
                            changed_files=patch_scope_validation["changed_files"],
                            message=build_commit_message(
                                page_number=page_number,
                                case_id=str(page_case["case_id"]),
                                changed_files=patch_scope_validation["changed_files"],
                            ),
                            repo=code_root,
                        )
                        write_json(case_dir / "commit_gate.json", commit_gate)
                        if commit_gate.get("revertability_check") is not None:
                            write_json(case_dir / "revertability_check.json", commit_gate["revertability_check"])
                        commit_acceptance_gate = validate_commit_gate_acceptance(commit_gate)
                        write_json(case_dir / "commit_acceptance_gate.json", commit_acceptance_gate)
                        receipts.write(
                            "commit_page_bug_fix",
                            input_artifacts=["review_after_validation.json"],
                            output_artifacts=[
                                "commit_gate.json",
                                "commit_acceptance_gate.json",
                                *(["revertability_check.json"] if commit_gate.get("revertability_check") is not None else []),
                            ],
                            command_or_endpoint="git add + git commit",
                            validator_result={"ok": commit_acceptance_gate["ok"], "errors": commit_acceptance_gate["errors"]},
                            next_allowed_nodes=["write_page_terminal_ledger"],
                        )
                        if commit_acceptance_gate["ok"]:
                            terminal_status, terminal_reason = (
                                "patched_confirmed",
                                "patch_validated_and_committed_with_after_fixture"
                                if after_review_fixture_artifact is not None
                                else "patch_validated_and_committed",
                            )
                        else:
                            terminal_status, terminal_reason = "still_open", "commit_gate_failed"
                    else:
                        if after_review_error is not None:
                            terminal_status, terminal_reason = "blocked_substrate", "after_review_call_failed"
                        else:
                            terminal_status, terminal_reason = "still_open", "after_review_not_clean"
                else:
                    terminal_status, terminal_reason = "still_open", "targeted_tests_failed"
            else:
                terminal_status, terminal_reason = "still_open", "patch_scope_validation_failed"
        else:
            if patch_error is not None:
                terminal_status, terminal_reason = "blocked_substrate", "patch_delegate_call_failed"
            elif repair_diagnosis_error is not None:
                terminal_status, terminal_reason = "blocked_substrate", "repair_diagnosis_call_failed"
            elif repair_diagnosis_validation is not None and patch_validation_has_delegate_timeout(repair_diagnosis_validation):
                terminal_status, terminal_reason = "blocked_substrate", "repair_diagnosis_timeout"
            elif repair_plan_error is not None:
                terminal_status, terminal_reason = "blocked_substrate", "repair_plan_call_failed"
            elif repair_plan_validation is not None and not repair_plan_validation.get("ok"):
                terminal_status, terminal_reason = "still_open", "repair_plan_failed"
            elif patch_validation_has_delegate_timeout(patch_validation):
                terminal_status, terminal_reason = "blocked_substrate", "patch_delegate_timeout"
            elif patch_validation and any(
                "patch_prompt_contract_failed" in error
                for error in validation_error_list(patch_validation, "patch_validation")
            ):
                terminal_status, terminal_reason = "still_open", "patch_prompt_contract_failed"
            elif patch_validation and any(
                "repair_diagnosis_dry_run" in error
                for error in validation_error_list(patch_validation, "patch_validation")
            ):
                terminal_status, terminal_reason = "still_open", "repair_diagnosis_dry_run"
            elif patch_validation and any(
                "worker/provider error" in error
                or "blocked substrate" in error
                or "session_error" in error
                or "failed tool_call" in error
                for error in validation_error_list(patch_validation, "patch_validation")
            ):
                terminal_status, terminal_reason = "blocked_substrate", "patch_delegate_substrate_error"
            else:
                terminal_status, terminal_reason = "still_open", "patch_delegate_failed"
        if (
            patch_mode == "live"
            and terminal_status == "blocked_substrate"
            and patch_validation is not None
            and not patch_validation.get("ok")
        ):
            patch_delegate_bug_report_artifact = "scillm_patch_delegate_bug_report.json"
            write_json(
                case_dir / patch_delegate_bug_report_artifact,
                build_patch_delegate_bug_report(
                    case_id=page_case["case_id"],
                    page_number=page_number,
                    code_root=code_root,
                    patch_backend=patch_backend,
                    patch_mode=patch_mode,
                    terminal_reason=terminal_reason,
                    patch_request=patch_request,
                    patch_receipt=patch_receipt,
                    patch_error=patch_error,
                    patch_validation=patch_validation,
                    patch_attempts_ledger=patch_attempts_ledger,
                    transport_event_artifacts=transport_event_artifacts,
                    opencode_host_artifacts=opencode_host_artifacts,
                ),
            )
    receipts.write(
        "route_page_result",
        input_artifacts=["review_validation.json"],
        output_artifacts=[patch_delegate_bug_report_artifact] if patch_delegate_bug_report_artifact else [],
        command_or_endpoint="run_page_second_pass_dag.route_review_result",
        validator_result={"ok": terminal_status in TERMINAL_STATUSES, "terminal_status": terminal_status},
        next_allowed_nodes=["write_page_terminal_ledger"],
    )

    terminal = {
        "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
        "case_id": page_case["case_id"],
        "page_number": page_number,
        "terminal_status": terminal_status,
        "reason": terminal_reason,
        "allowed_terminal_statuses": sorted(TERMINAL_STATUSES),
        "evidence_artifacts": [
            "state.json",
            "sampled_candidate_manifest.json",
            "page_before.json",
            "page_before.png",
            "page_candidates.png",
            "selected_candidates.json",
            "candidate_presets.json",
            "review_request.json",
            "review_request_validation.json",
            "scillm_orchestrator_page_dag_spec.json",
            "scillm_orchestrator_page_dag_spec_validation.json",
            "scillm_orchestrator_page_submission.json",
            "scillm_orchestrator_page_submission_validation.json",
            "review_validation.json",
        ],
        "commit_sha": None,
    }
    if review_preflight is not None:
        terminal["evidence_artifacts"].append("scillm_review_preflight.json")
    if review_fixture_artifact is not None:
        terminal["evidence_artifacts"].append(review_fixture_artifact)
    if review_response is not None:
        if review_mode == "live":
            terminal["evidence_artifacts"].append("scillm_review_receipt.json")
        terminal["evidence_artifacts"].append("review_response.json")
    if review_error is not None:
        terminal["evidence_artifacts"].append("scillm_review_error.json")
    terminal["evidence_artifacts"].extend(["scillm_page_orchestrator_run_request.json", "scillm_page_orchestrator_run_validation.json"])
    if page_orchestrator_run_receipt is not None:
        terminal["evidence_artifacts"].append("scillm_page_orchestrator_run_receipt.json")
    if page_orchestrator_run_error is not None:
        terminal["evidence_artifacts"].append("scillm_page_orchestrator_run_error.json")
    if patch_baseline is not None:
        terminal["evidence_artifacts"].append("patch_baseline.json")
    if patch_evidence_workspace is not None:
        terminal["evidence_artifacts"].append("patch_evidence_workspace.json")
    if repair_diagnosis_validation is not None:
        terminal["evidence_artifacts"].extend(["repair_diagnosis_request.json", "repair_diagnosis_validation.json"])
    if repair_diagnosis_receipt is not None:
        terminal["evidence_artifacts"].append("repair_diagnosis_receipt.json")
    if repair_diagnosis_error is not None:
        terminal["evidence_artifacts"].append("repair_diagnosis_error.json")
    if repair_plan_validation is not None:
        terminal["evidence_artifacts"].extend(["repair_plan_request.json", "repair_plan_request_validation.json", "repair_plan_validation.json"])
    if repair_plan_receipt is not None:
        terminal["evidence_artifacts"].append("repair_plan_receipt.json")
    if repair_plan_error is not None:
        terminal["evidence_artifacts"].append("repair_plan_error.json")
    if patch_validation is not None:
        if (case_dir / "patch_request.json").exists():
            terminal["evidence_artifacts"].append("patch_request.json")
        terminal["evidence_artifacts"].append("patch_validation.json")
    if patch_attempts_ledger is not None:
        terminal["evidence_artifacts"].append("patch_attempts_ledger.json")
        terminal["evidence_artifacts"].extend(
            str(attempt["validation_artifact"])
            for attempt in patch_attempts_ledger.get("attempts") or []
            if isinstance(attempt, dict) and attempt.get("validation_artifact")
        )
        terminal["evidence_artifacts"].extend(
            str(attempt[artifact_key])
            for attempt in patch_attempts_ledger.get("attempts") or []
            if isinstance(attempt, dict) and attempt.get("ok") is True
            for artifact_key in ["request_artifact", "receipt_artifact"]
            if attempt.get(artifact_key)
        )
        terminal["evidence_artifacts"].extend(
            str(artifact)
            for attempt in patch_attempts_ledger.get("attempts") or []
            if isinstance(attempt, dict)
            for artifact in attempt.get("opencode_child_handle_artifacts") or []
            if isinstance(artifact, str) and artifact
        )
    if patch_delegate_bug_report_artifact is not None:
        terminal["evidence_artifacts"].append(patch_delegate_bug_report_artifact)
    if prompt_contract_artifacts:
        terminal["evidence_artifacts"].extend(prompt_contract_artifacts)
    if patch_preflight is not None:
        terminal["evidence_artifacts"].append("scillm_patch_preflight.json")
    if patch_receipt is not None:
        terminal["evidence_artifacts"].append("patch_receipt.json")
        terminal["evidence_artifacts"].extend(transport_event_artifacts)
    if opencode_host_artifacts:
        terminal["evidence_artifacts"].extend(opencode_host_artifacts)
    if patch_error is not None:
        terminal["evidence_artifacts"].append("patch_error.json")
    if patch_delta is not None:
        terminal["evidence_artifacts"].append("patch_delta.json")
    if patch_scope_validation is not None:
        terminal["evidence_artifacts"].append("patch_scope_validation.json")
    if test_validation is not None:
        terminal["evidence_artifacts"].append("test_validation.json")
    if after_review_validation is not None:
        terminal["evidence_artifacts"].extend(
            [
                "page_after.json",
                "page_after.png",
                "page_after_candidates.png",
                "review_after_request.json",
                "review_after_request_validation.json",
                "review_after_validation.json",
            ]
        )
    if after_review_fixture_artifact is not None:
        terminal["evidence_artifacts"].append(after_review_fixture_artifact)
    if after_review_response is not None:
        if after_review_fixture_artifact is None:
            terminal["evidence_artifacts"].append("scillm_after_review_receipt.json")
        terminal["evidence_artifacts"].append("review_after_response.json")
    if after_review_error is not None:
        terminal["evidence_artifacts"].append("scillm_after_review_error.json")
    if commit_gate is not None:
        terminal["evidence_artifacts"].append("commit_gate.json")
        if commit_acceptance_gate is not None:
            terminal["evidence_artifacts"].append("commit_acceptance_gate.json")
        if commit_gate.get("revertability_check") is not None:
            terminal["evidence_artifacts"].append("revertability_check.json")
        terminal["commit_sha"] = commit_gate.get("commit_sha")
        terminal["commit_gate_ok"] = commit_gate.get("ok")
        terminal["commit_exact_file_match"] = commit_gate.get("exact_file_match")
        revertability = commit_gate.get("revertability_check")
        terminal["commit_revertability_ok"] = revertability.get("ok") if isinstance(revertability, dict) else None
        terminal["commit_acceptance_ok"] = commit_acceptance_gate.get("ok") if commit_acceptance_gate is not None else False
    terminal["evidence_artifacts"].append("review.html")
    terminal["evidence_artifacts"].append("terminal_ledger_validation.json")
    write_json(case_dir / "terminal_ledger.json", terminal)
    receipts.write(
        "write_page_terminal_ledger",
        input_artifacts=["review_request.json"],
        output_artifacts=["terminal_ledger.json"],
        command_or_endpoint="run_page_second_pass_dag.write_page_terminal_ledger",
        validator_result={"ok": terminal["terminal_status"] in TERMINAL_STATUSES},
        next_allowed_nodes=["render_page_review_artifact"],
    )

    render_review_html(case_dir, terminal)
    receipts.write(
        "render_page_review_artifact",
        input_artifacts=["terminal_ledger.json", "page_before.png", "page_candidates.png", "selected_candidates.json", "review_validation.json"],
        output_artifacts=["review.html"],
        command_or_endpoint="run_page_second_pass_dag.render_review_html",
        validator_result={"ok": (case_dir / "review.html").is_file()},
        next_allowed_nodes=["validate_page_terminal_ledger"],
    )

    terminal_validation = validate_page_terminal_ledger(case_dir, terminal)
    write_json(case_dir / "terminal_ledger_validation.json", terminal_validation)
    receipts.write(
        "validate_page_terminal_ledger",
        input_artifacts=["terminal_ledger.json", "review.html"],
        output_artifacts=["terminal_ledger_validation.json"],
        command_or_endpoint="run_page_second_pass_dag.validate_page_terminal_ledger",
        validator_result={"ok": terminal_validation["ok"], "errors": terminal_validation["errors"]},
        next_allowed_nodes=["package_page_review_bundle"],
    )

    package_bundle(case_dir, case_dir / "review_bundle.zip")
    bundle_validation = validate_page_review_bundle(case_dir, case_dir / "review_bundle.zip", terminal)
    write_json(case_dir / "review_bundle_validation.json", bundle_validation)
    receipts.write(
        "package_page_review_bundle",
        input_artifacts=["terminal_ledger.json", "review.html"],
        output_artifacts=["review_bundle.zip", "review_bundle_validation.json"],
        command_or_endpoint="zipfile.ZipFile",
        validator_result={
            "ok": bundle_validation["ok"],
            "errors": bundle_validation["errors"],
            "missing_expected_zip_entries": bundle_validation["missing_expected_zip_entries"],
        },
        next_allowed_nodes=[],
    )
    state["terminal_status"] = terminal["terminal_status"]
    state["updated_at"] = utc_now()
    write_json(case_dir / "state.json", state)
    return {"case_dir": str(case_dir), "terminal_status": terminal["terminal_status"], "page_number": page_number}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--sampled-cases", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--page", type=int)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--apply-mode", default="release")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--batch-id", default="pdf-lab-second-pass")
    parser.add_argument("--review-mode", choices=["dry_run", "live", "fixture"], default="dry_run")
    parser.add_argument("--review-fixture", type=Path, dest="review_fixture_path")
    parser.add_argument("--review-after-fixture", type=Path, dest="review_after_fixture_path")
    parser.add_argument("--scillm-base-url", default=os.environ.get("SCILLM_API_BASE", "http://localhost:4001"))
    parser.add_argument("--scillm-auth-token", default=os.environ.get("SCILLM_PROXY_KEY", "sk-dev-proxy-123"))
    parser.add_argument("--caller-skill", default="pdf-lab")
    parser.add_argument("--scillm-timeout-s", type=float, default=180.0)
    parser.add_argument("--scillm-preflight-mode", choices=["dry_run", "live"], default="live")
    parser.add_argument("--patch-mode", choices=["dry_run", "live"], default="dry_run")
    parser.add_argument("--patch-backend", choices=["opencode_serve", "scillm_orchestrator"], default="opencode_serve")
    parser.add_argument("--opencode-agent", default="build")
    parser.add_argument("--opencode-agent-sequence", action="append", dest="opencode_agent_sequence")
    parser.add_argument("--opencode-model")
    parser.add_argument("--patch-prompt-profile", choices=sorted(PATCH_PROMPT_PROFILES), default="plan_only")
    parser.add_argument("--repair-strategy", choices=sorted(PATCH_REPAIR_STRATEGIES), default="single")
    parser.add_argument("--opencode-timeout-s", type=float, default=600.0)
    parser.add_argument("--opencode-keep-session", action="store_true")
    parser.add_argument("--opencode-skill", action="append", dest="opencode_skills")
    parser.add_argument("--allowed-patch-prefix", action="append", dest="allowed_patch_prefixes")
    parser.add_argument("--validation-command", action="append", dest="validation_commands")
    parser.add_argument("--commit-mode", choices=["dry_run", "live"], default="dry_run")
    parser.add_argument("--code-root", type=Path, default=REPO)
    parser.add_argument("--page-extract-timeout-s", type=float)
    parser.add_argument("--page-orchestrator-mode", choices=sorted(PAGE_ORCHESTRATOR_MODES), default="dry_run")
    args = parser.parse_args()

    if not args.case_id and args.page is None:
        print("one of --case-id or --page is required", file=sys.stderr)
        return 2
    try:
        result = run_page_case(
            pdf_path=args.pdf,
            manifest=load_json(args.manifest),
            sampled_cases=load_json(args.sampled_cases),
            out_dir=args.out,
            case_id=args.case_id,
            page_number=args.page,
            ledger_path=args.ledger,
            apply_mode=args.apply_mode,
            dpi=args.dpi,
            model=args.model,
            batch_id=args.batch_id,
            review_mode=args.review_mode,
            review_fixture_path=args.review_fixture_path,
            review_after_fixture_path=args.review_after_fixture_path,
            scillm_base_url=args.scillm_base_url,
            scillm_auth_token=args.scillm_auth_token,
            caller_skill=args.caller_skill,
            scillm_timeout_s=args.scillm_timeout_s,
            scillm_preflight_mode=args.scillm_preflight_mode,
            patch_mode=args.patch_mode,
            patch_backend=args.patch_backend,
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
            commit_mode=args.commit_mode,
            code_root=args.code_root,
            page_extract_timeout_s=args.page_extract_timeout_s,
            page_orchestrator_mode=args.page_orchestrator_mode,
        )
    except Exception as exc:  # noqa: BLE001 - CLI setup failures must leave copyable page evidence.
        result = write_page_dag_setup_failure_artifacts(
            out_dir=args.out,
            pdf_path=args.pdf,
            case_id=args.case_id,
            page_number=args.page,
            code_root=args.code_root,
            opencode_model=args.opencode_model,
            patch_prompt_profile=args.patch_prompt_profile,
            repair_strategy=args.repair_strategy,
            page_extract_timeout_s=args.page_extract_timeout_s,
            page_orchestrator_mode=args.page_orchestrator_mode,
            error=exc,
        )
        print(json.dumps(result, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
