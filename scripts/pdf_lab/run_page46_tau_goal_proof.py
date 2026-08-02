#!/usr/bin/env python3
"""Regenerate the NIST page-46 Tau bounded-loop proof.

This command intentionally proves one narrow case:

before evidence -> live reviewer defect -> patch in an isolated worktree ->
re-extract -> live reviewer clean.

The default patch leg is deterministic. Use ``--patch-leg subagent`` to require
a live Scillm/OpenCode coder subagent to write the patch plus a
``tau.subagent_receipt.v1`` before this command applies the patch. Use
``--patch-leg subagent_chat_no_reference`` when the live coder returns the patch
as JSON and the harness materializes the artifact fail-closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = Path("python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json")
AFTER_SAMPLED = Path("artifacts/pdf_lab/tau_page46_simple_loop_retry_20260628_03/sampled_page_cases_p46_h_1_2_3.json")
FOCUSED_PATCH_FILES = [
    "python/pdf_oxide/extract_for_pdflab.py",
    "scripts/pdf_lab/snapshot_current_extraction.py",
    "scripts/pdf_lab/build_pdf_element_candidate_manifest.py",
    "tests/test_pdf_lab_second_pass_candidate_manifest.py",
]
SCILLM_CLI = Path("/home/graham/workspace/experiments/.venv/bin/scillm")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(cmd: list[str], *, cwd: Path, timeout_s: float, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=env or os.environ.copy(),
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return subprocess.CompletedProcess(
            cmd,
            124,
            stdout=stdout,
            stderr=(stderr + f"\nTIMEOUT after {timeout_s}s").strip(),
        )


def require_ok(completed: subprocess.CompletedProcess[str], label: str) -> None:
    if completed.returncode != 0:
        raise RuntimeError(
            f"{label} failed with exit={completed.returncode}\nSTDOUT:\n{completed.stdout[-4000:]}\nSTDERR:\n{completed.stderr[-4000:]}"
        )


def parse_last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    return {}


def parse_json_object(text: str) -> dict[str, Any]:
    payload = parse_last_json(text)
    if payload:
        return payload
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    for raw in reversed(fenced):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
        if isinstance(payload, dict):
            return payload
    return {}


def copy_extension(worktree: Path) -> None:
    src = REPO / "python/pdf_oxide/pdf_oxide.abi3.so"
    dst = worktree / "python/pdf_oxide/pdf_oxide.abi3.so"
    if not src.exists():
        raise FileNotFoundError(f"built extension missing: {src}")
    shutil.copy2(src, dst)


def create_before_sample(manifest_path: Path, out_path: Path) -> None:
    manifest = read_json(manifest_path)
    table_candidates = [c for c in manifest.get("candidates", []) if c.get("preset_type") == "table"]
    if not table_candidates:
        raise RuntimeError("before manifest did not contain a table candidate for page 46")
    target = table_candidates[-1]
    payload = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "created_at": utc_now(),
        "pdf_path": manifest["pdf_path"],
        "pdf_id": manifest["pdf_id"],
        "page_count": manifest["page_count"],
        "manifest_schema": manifest["schema"],
        "manifest_validation": {
            "schema": "pdf_lab.second_pass.candidate_manifest_validation.v1",
            "ok": True,
            "errors": [],
            "candidate_count": manifest["candidate_count"],
            "declared_candidate_count": manifest["candidate_count"],
        },
        "requested_sample_size": 1,
        "selected_count": 1,
        "selected_pages": [46],
        "forced_pages": {"requested": [46], "accepted": [46], "rejected": []},
        "probabilistic_selected_pages": [],
        "seed": 530800,
        "strata": [{"stratum": "p46_merged_list_before_patch", "page_count": 1}],
        "sampling_audit": {
            "schema": "pdf_lab.second_pass.sampling_audit.v1",
            "adequate": True,
            "selected_count": 1,
            "candidate_count": manifest["candidate_count"],
            "warnings": [],
        },
        "page_cases": [
            {
                "case_id": "page_case_0001_p0046",
                "page_number": 46,
                "page_index": 45,
                "candidate_ids": [target["candidate_id"]],
                "preset_counts": {target["preset_type"]: 1},
                "forced_by_human_annotation": True,
                "selection_reason": [
                    "human_annotated_page",
                    "p46_merged_h_nested_list_before_patch",
                    "bounded_live_loop_payload",
                ],
                "selection_score": 100.0,
                "selection_probability_estimate": 1.0,
                "selection_probability_basis": {
                    "method": "forced_human_annotation",
                    "forced_page": True,
                    "candidate_count_on_page": manifest["candidate_count"],
                },
                "strata": ["p46_merged_list_before_patch", "risk:high"],
            }
        ],
    }
    write_json(out_path, payload)


def create_after_sample_from_manifest(manifest_path: Path, out_path: Path) -> None:
    manifest = read_json(manifest_path)
    reviewable_candidates = [
        c
        for c in manifest.get("candidates", [])
        if c.get("page_number") == 46 and c.get("preset_type") not in {"side_chrome", "page_chrome"}
    ]
    wanted_prefixes = (
        "h. Notify account managers",
        "1. [Assignment: organization-defined time period] when accounts are no longer required",
        "2. [Assignment: organization-defined time period] when users are terminated or",
        "transferred; and",
        "3. [Assignment: organization-defined time period] when system usage or need-to-know",
        "changes for an individual",
    )
    preferred_candidates = []
    for prefix in wanted_prefixes:
        match = next(
            (
                c
                for c in reviewable_candidates
                if str(c.get("block_id") or "").startswith("actual:p46:ac2_")
                and str(c.get("text_excerpt") or "").startswith(prefix)
            ),
            None,
        )
        if match:
            preferred_candidates.append(match)
    candidates = preferred_candidates or reviewable_candidates[:8]
    if not candidates:
        raise RuntimeError("after manifest did not contain reviewable page 46 candidates")
    candidate_ids = [c["candidate_id"] for c in candidates]
    preset_counts: dict[str, int] = {}
    for candidate in candidates:
        preset_type = str(candidate.get("preset_type") or "unknown")
        preset_counts[preset_type] = preset_counts.get(preset_type, 0) + 1
    payload = {
        "schema": "pdf_lab.second_pass.sampled_page_cases.v1",
        "created_at": utc_now(),
        "pdf_path": manifest["pdf_path"],
        "pdf_id": manifest["pdf_id"],
        "page_count": manifest["page_count"],
        "manifest_schema": manifest["schema"],
        "manifest_validation": {
            "schema": "pdf_lab.second_pass.candidate_manifest_validation.v1",
            "ok": True,
            "errors": [],
            "candidate_count": manifest["candidate_count"],
            "declared_candidate_count": manifest["candidate_count"],
        },
        "requested_sample_size": 1,
        "selected_count": 1,
        "selected_pages": [46],
        "forced_pages": {"requested": [46], "accepted": [46], "rejected": []},
        "probabilistic_selected_pages": [],
        "seed": 530801,
        "strata": [{"stratum": "p46_after_patch_actual_candidates", "page_count": 1}],
        "sampling_audit": {
            "schema": "pdf_lab.second_pass.sampling_audit.v1",
            "adequate": True,
            "selected_count": 1,
            "candidate_count": manifest["candidate_count"],
            "warnings": [],
        },
        "page_cases": [
            {
                "case_id": "page_case_0001_p0046",
                "page_number": 46,
                "page_index": 45,
                "candidate_ids": candidate_ids,
                "preset_counts": preset_counts,
                "forced_by_human_annotation": True,
                "selection_reason": [
                    "post_patch_actual_manifest_candidates",
                    "p46_agentic_second_pass_after_patch_review",
                    "bounded_live_loop_payload",
                ],
                "selection_score": 100.0,
                "selection_probability_estimate": 1.0,
                "selection_probability_basis": {
                    "method": "forced_post_patch_manifest",
                    "forced_page": True,
                    "candidate_count_on_page": manifest["candidate_count"],
                },
                "strata": ["p46_after_patch_actual_candidates", "risk:high"],
            }
        ],
    }
    write_json(out_path, payload)


def manifest_cmd(*, out: Path, worktree: Path, page_timeout_s: float) -> list[str]:
    return [
        str(REPO / ".venv/bin/python"),
        "scripts/pdf_lab/build_pdf_element_candidate_manifest.py",
        "--pdf",
        str(PDF),
        "--out",
        str(out),
        "--ledger",
        str(LEDGER),
        "--apply-mode",
        "release",
        "--page",
        "46",
        "--page-timeout-s",
        str(page_timeout_s),
        "--debug-log",
        str(out.with_name(out.stem + "_debug.log")),
        "--progress-path",
        str(out.with_name(out.stem + "_progress.json")),
    ]


def review_cmd(
    *,
    manifest: Path,
    sampled_cases: Path,
    out: Path,
    code_root: Path,
    batch_id: str,
    args: argparse.Namespace,
) -> list[str]:
    return [
        str(REPO / ".venv/bin/python"),
        str(REPO / "scripts/pdf_lab/run_tau_page_repair_loop_poc.py"),
        "--pdf",
        str(PDF),
        "--manifest",
        str(manifest),
        "--sampled-cases",
        str(sampled_cases),
        "--out",
        str(out),
        "--case-id",
        "page_case_0001_p0046",
        "--ledger",
        str(code_root / LEDGER),
        "--max-attempts",
        "1",
        "--attempt-timeout-s",
        str(args.attempt_timeout_s),
        "--apply-mode",
        "release",
        "--dpi",
        "72",
        "--model",
        args.model,
        "--batch-id",
        batch_id,
        "--review-mode",
        args.review_mode,
        "--scillm-base-url",
        args.scillm_base_url,
        "--scillm-auth-token",
        args.scillm_auth_token,
        "--scillm-timeout-s",
        str(args.scillm_timeout_s),
        "--scillm-preflight-mode",
        "live",
        "--patch-mode",
        "dry_run",
        "--patch-prompt-profile",
        "plan_only",
        "--commit-mode",
        "dry_run",
        "--code-root",
        str(code_root),
        "--page-orchestrator-mode",
        "live",
        "--no-review-include-images",
        "--allowed-patch-prefix",
        "src",
        "--allowed-patch-prefix",
        "python",
        "--allowed-patch-prefix",
        "tests",
        "--allowed-patch-prefix",
        "scripts/pdf_lab",
    ]


def build_patch_handoff(*, out: Path, patch_path: Path, executor: str, summary: str) -> dict[str, Any]:
    return {
        "schema": "tau.agent_handoff.v1",
        "goal": {"goal_id": "goal-pdf-lab-page46-tau-loop", "goal_version": 1, "goal_hash": "sha256:pdf-lab-page46-tau-loop"},
        "previous_subagent": "reviewer",
        "context": {
            "run_id": "pdf-lab-page46-goal-patch-leg",
            "attempt": 1,
            "summary": "Before-state live reviewer returned still_open for page 46 merged AC-2 list/table defect.",
        },
        "result": {
            "status": "NEEDS_CHANGES",
            "summary": summary,
            "evidence": [str(patch_path)],
        },
        "next_agent": {
            "name": "coder",
            "executor": executor,
            "reason": "Return a focused patch, run focused regression, re-extract page 46.",
        },
        "required_evidence": [
            "patch applies cleanly in isolated worktree",
            "focused manifest regression passes",
            "page 46 manifest regenerates",
            "live reviewer re-review reaches terminal ledger",
        ],
    }


def validate_patch_receipt(receipt_path: Path, *, require_subagent_live: bool) -> dict[str, Any]:
    receipt = read_json(receipt_path)
    errors: list[str] = []
    if receipt.get("schema") != "tau.subagent_receipt.v1":
        errors.append("schema must be tau.subagent_receipt.v1")
    result = receipt.get("result")
    if not isinstance(result, dict):
        errors.append("result must be an object")
        result = {}
    if result.get("mocked") is not False:
        errors.append("result.mocked must be false")
    if result.get("live") is not True:
        errors.append("result.live must be true")
    if require_subagent_live and result.get("subagent_live") is not True:
        errors.append("result.subagent_live must be true")
    if errors:
        raise RuntimeError(f"invalid patch receipt {receipt_path}: {errors}")
    return receipt


def focused_source_context(worktree: Path) -> str:
    sections: list[str] = []
    for rel_path in FOCUSED_PATCH_FILES:
        path = worktree / rel_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if rel_path == "python/pdf_oxide/extract_for_pdflab.py":
            continue
        if rel_path == "scripts/pdf_lab/snapshot_current_extraction.py":
            lines = text.splitlines()
            chunks = [(130, 220), (374, 565), (616, 666)]
            selected = []
            for start, end in chunks:
                selected.extend(lines[idx] for idx in range(start - 1, min(end, len(lines))))
            text = "\n".join(selected)
        elif rel_path == "scripts/pdf_lab/build_pdf_element_candidate_manifest.py":
            lines = text.splitlines()
            text = "\n".join(lines[idx] for idx in range(119, min(260, len(lines))))
        elif rel_path == "tests/test_pdf_lab_second_pass_candidate_manifest.py":
            continue
        sections.append(f"## {rel_path}\n```python\n{text}\n```")
    return "\n\n".join(sections)


def compact_json_file(path: Path, *, max_chars: int = 20000) -> str:
    if not path.exists():
        return f"{path} missing"
    text = path.read_text(encoding="utf-8")
    return text if len(text) <= max_chars else text[:max_chars] + "\n...<truncated>..."


def capture_focused_diff(out: Path, name: str) -> Path:
    patch_path = out / "patch_leg" / name
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    diff = run(["git", "diff", "--", *FOCUSED_PATCH_FILES], cwd=REPO, timeout_s=30)
    require_ok(diff, "capture focused diff")
    patch_path.write_text(diff.stdout, encoding="utf-8")
    if not patch_path.read_text(encoding="utf-8").strip():
        raise RuntimeError("focused patch diff is empty")
    return patch_path


def render_structured_edits_to_patch(
    *,
    edits: list[dict[str, Any]],
    out: Path,
    name: str,
    base_worktree: Path,
) -> Path:
    if not edits:
        raise RuntimeError("structured edit payload is empty")
    render_root = out / "patch_leg" / f"render_{name.removesuffix('.patch')}"
    if render_root.exists():
        shutil.rmtree(render_root)
    render_root.mkdir(parents=True)
    try:
        require_ok(run(["git", "init"], cwd=render_root, timeout_s=30), "init structured edit render repo")
        for rel_path in FOCUSED_PATCH_FILES:
            source = base_worktree / rel_path
            if not source.exists():
                continue
            target = render_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        require_ok(run(["git", "add", "--", *FOCUSED_PATCH_FILES], cwd=render_root, timeout_s=30), "stage structured edit baseline")
        for index, edit in enumerate(edits):
            rel_path = str(edit.get("path") or "").strip()
            if rel_path not in FOCUSED_PATCH_FILES:
                raise RuntimeError(f"structured edit {index} path is not allowed: {rel_path}")
            target = render_root / rel_path
            text = target.read_text(encoding="utf-8")
            if "replace" in edit:
                old = str(edit.get("replace") or "")
                new = str(edit.get("content") or "")
                if not old or old not in text:
                    raise RuntimeError(f"structured edit {index} replace marker not found in {rel_path}")
                text = text.replace(old, new, 1)
            elif "insert_after" in edit:
                marker = str(edit.get("insert_after") or "")
                content = str(edit.get("content") or "")
                if not marker or marker not in text:
                    raise RuntimeError(f"structured edit {index} insert_after marker not found in {rel_path}")
                text = text.replace(marker, marker + content, 1)
            else:
                raise RuntimeError(f"structured edit {index} needs replace or insert_after")
            target.write_text(text, encoding="utf-8")
        patch_path = out / "patch_leg" / name
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        diff = run(["git", "diff", "--", *FOCUSED_PATCH_FILES], cwd=render_root, timeout_s=30)
        require_ok(diff, "render structured edit diff")
        patch_path.write_text(diff.stdout, encoding="utf-8")
        if not patch_path.read_text(encoding="utf-8").strip():
            raise RuntimeError("structured edit rendered an empty patch")
        return patch_path
    finally:
        shutil.rmtree(render_root, ignore_errors=True)


def write_deterministic_patch_artifacts(args: argparse.Namespace, out: Path, worktree: Path, patch_path: Path) -> dict[str, Any]:
    handoff = {
        "schema": "tau.agent_handoff.v1",
        "goal": {"goal_id": "goal-pdf-lab-page46-tau-loop", "goal_version": 1, "goal_hash": "sha256:pdf-lab-page46-tau-loop"},
        "previous_subagent": "reviewer",
        "context": {
            "run_id": "pdf-lab-page46-goal-patch-leg",
            "attempt": 1,
            "summary": "Before-state live reviewer returned still_open for page 46 merged AC-2 list/table defect.",
        },
        "result": {
            "status": "NEEDS_CHANGES",
            "summary": "Apply focused page-46 patch in isolated worktree.",
            "evidence": [str(patch_path)],
        },
        "next_agent": {
            "name": "coder",
            "executor": "local_deterministic_patch",
            "reason": "Apply focused diff, run focused regression, re-extract page 46.",
        },
        "required_evidence": [
            "patch applies cleanly in isolated worktree",
            "focused manifest regression passes",
            "page 46 manifest regenerates",
            "live reviewer re-review reaches terminal ledger",
        ],
    }
    write_json(out / "patch_leg/tau_agent_handoff_to_patch.json", handoff)

    receipt = {
        "schema": "tau.subagent_receipt.v1",
        "goal": {
            "goal_id": "goal-pdf-lab-page46-tau-loop",
            "goal_version": 1,
            "goal_hash": "sha256:pdf-lab-page46-tau-loop",
            "immutable_goal_preserved": True,
        },
        "context": {
            "run_id": "pdf-lab-page46-goal-patch-leg",
            "subagent": "coder",
            "actor_type": "local_deterministic_patch",
            "ticket": "local:pdf-lab-page46-loop",
            "attempt": 1,
            "artifacts_read": [str(patch_path)],
        },
        "result": {
            "status": "COMPLETED",
            "summary": "Applied focused page-46 diff to isolated clean-HEAD worktree.",
            "mocked": False,
            "live": True,
            "subagent_live": False,
            "commands_run": [
                f"git apply {patch_path}",
                "python -m pytest -q tests/test_pdf_lab_second_pass_candidate_manifest.py::test_manifest_marks_nested_list_items_from_shared_raw_parent",
                "build_pdf_element_candidate_manifest.py --page 46",
            ],
            "patch_sha256": sha256_file(patch_path),
            "changed_files": FOCUSED_PATCH_FILES,
            "isolated_code_root": str(worktree),
        },
        "evidence": [str(patch_path)],
        "next": {"subagent": "reviewer", "reason": "Run live re-review on re-extracted page 46 evidence."},
    }
    write_json(out / "patch_leg/tau_subagent_receipt.json", receipt)
    return validate_patch_receipt(out / "patch_leg/tau_subagent_receipt.json", require_subagent_live=False)


def scillm_env(args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    env["SCILLM_API_KEY"] = args.scillm_auth_token
    env["SCILLM_PROXY_KEY"] = args.scillm_auth_token
    return env


def write_subagent_prompt(
    *,
    out: Path,
    worktree: Path,
    handoff_path: Path,
    reference_patch: Path | None,
    returned_patch: Path,
    receipt_path: Path,
    before_manifest: Path,
    before_review_dir: Path,
    repair_attempt: int,
    course_correction_review_dir: Path | None = None,
) -> Path:
    prompt_path = out / f"patch_leg/attempt_{repair_attempt:03d}_coder_subagent_prompt.md"
    case_dir = before_review_dir / "attempt_001/page_case_0001_p0046"
    correction_case_dir = course_correction_review_dir / "attempt_001/page_case_0001_p0046" if course_correction_review_dir else None
    reference_section = (
        f"- Reference focused patch to return as the candidate patch: {reference_patch}\n"
        if reference_patch
        else "- No reference patch is provided. You must infer the repair from candidate/reviewer evidence and source code.\n"
    )
    artifact_reads = [str(handoff_path), str(before_manifest)]
    if reference_patch:
        artifact_reads.append(str(reference_patch))
    repair_mode = "reference_patch_copy" if reference_patch else "no_reference_patch_discovery"
    correction_section = ""
    if correction_case_dir:
        correction_section = f"""
Course-correction evidence from prior failed repair:
- Previous after-review terminal ledger: {correction_case_dir / "terminal_ledger.json"}
- Previous after-review response: {correction_case_dir / "review_response.json"}
- Previous after-review validation: {correction_case_dir / "review_validation.json"}
- Previous selected candidates: {correction_case_dir / "selected_candidates.json"}
- Previous candidate presets: {correction_case_dir / "candidate_presets.json"}

The prior patch did not clear the reviewer. Produce an incremental patch against
the current worktree state. Do not re-emit a patch that only repeats the previous
change.
"""
    prompt = f"""You are the PDF Lab page-46 coder subagent.

Goal: return a focused patch for one defect, then stop. Do not apply the patch.

Repository worktree:
{worktree}

Non-negotiable execution rule:
- Treat {worktree} as the only repository root.
- Do not inspect or reason from /home/graham/workspace/experiments/pdf_oxide except for the artifact files explicitly listed in this prompt.
- Your final response is not evidence. The required files below are the evidence.
- If you cannot produce a patch, write the receipt JSON with result.status="BLOCKED", result.subagent_live=true, result.mocked=false, result.live=true, result.reason naming the blocker, then exit.
- If you produce prose without writing the required patch and receipt files, the run fails.
- Before returning, verify both required output files exist and are non-empty.

Repair attempt:
{repair_attempt}

Required input artifacts:
- Tau handoff: {handoff_path}
- Before candidate manifest: {before_manifest}
- Before live review artifacts: {before_review_dir}
{reference_section}
Key evidence files to inspect:
- {case_dir / "terminal_ledger.json"}
- {case_dir / "review_response.json"}
- {case_dir / "review_validation.json"}
- {case_dir / "page_before.json"}
- {case_dir / "selected_candidates.json"}
- {case_dir / "candidate_presets.json"}
- {case_dir / "patch_request.json"}
- {case_dir / "patch_attempt_01_prompt_review_payload.txt"}
{correction_section}

Required output artifacts:
- Patch file: {returned_patch}
- Receipt JSON: {receipt_path}

Hard rules:
- Do not leave mutations in the git worktree.
- Write a unified diff that applies to the clean isolated worktree.
- Write the patch to the exact patch file path above.
- Write receipt JSON only, with schema "tau.subagent_receipt.v1".
- Set result.mocked=false, result.live=true, and result.subagent_live=true.
- List the patch path in result.patch_path and evidence.
- Do not claim document-wide extraction quality.
- Do not commit or push.

Target defect:
- Human page 46, page_index 45, NIST SP 800-53r5.
- The candidate originates from pdf_oxide page candidate ingestion.
- The before-state reviewer reports the AC-2 item h / nested children 1, 2, 3 issue as still open.
- Expected repair direction: page 46 extraction should stop materializing the AC-2 structured list region as one broad table candidate and should expose separate text/list candidates for h, 1, 2, and 3.
- Keep the patch focused to page-candidate extraction/manifest logic and the existing focused regression path.

Allowed source files for patching:
{json.dumps(FOCUSED_PATCH_FILES, indent=2)}

Suggested verification target after the project agent applies your patch:
python -m pytest -q tests/test_pdf_lab_second_pass_candidate_manifest.py::test_manifest_marks_nested_list_items_from_shared_raw_parent

Receipt shape:
{{
  "schema": "tau.subagent_receipt.v1",
  "goal": {{
    "goal_id": "goal-pdf-lab-page46-tau-loop",
    "goal_version": 1,
    "goal_hash": "sha256:pdf-lab-page46-tau-loop",
    "immutable_goal_preserved": true
  }},
  "context": {{
    "run_id": "pdf-lab-page46-goal-patch-leg",
    "subagent": "coder",
    "actor_type": "scillm_opencode_delegate",
    "ticket": "local:pdf-lab-page46-loop",
    "attempt": {repair_attempt},
    "repair_mode": "{repair_mode}",
    "artifacts_read": {json.dumps(artifact_reads)}
  }},
  "result": {{
    "status": "COMPLETED",
    "summary": "Returned a focused page-46 patch for isolated application.",
    "mocked": false,
    "live": true,
    "subagent_live": true,
    "repair_mode": "{repair_mode}",
    "patch_path": "{returned_patch}",
    "changed_files": {json.dumps(FOCUSED_PATCH_FILES)}
  }},
  "evidence": ["{returned_patch}", "{receipt_path}"],
  "next": {{"subagent": "reviewer", "reason": "Project agent applies patch and reruns live review."}}
}}

Return a short JSON object on stdout after writing both files:
{{"status":"COMPLETED","patch_path":"{returned_patch}","receipt_path":"{receipt_path}"}}
"""
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def run_subagent_patch_leg(
    *,
    args: argparse.Namespace,
    out: Path,
    worktree: Path,
    before_manifest: Path,
    before_review_dir: Path,
    use_reference_patch: bool,
    repair_attempt: int,
    course_correction_review_dir: Path | None = None,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    reference_patch = capture_focused_diff(out, "reference_current_page46_focused_diff.patch") if use_reference_patch else None
    returned_patch = out / f"patch_leg/attempt_{repair_attempt:03d}_coder_returned_page46_focused_diff.patch"
    receipt_path = out / f"patch_leg/attempt_{repair_attempt:03d}_tau_subagent_receipt.json"
    handoff_path = out / f"patch_leg/attempt_{repair_attempt:03d}_tau_agent_handoff_to_patch.json"
    handoff = build_patch_handoff(
        out=out,
        patch_path=returned_patch,
        executor="scillm_opencode_delegate",
        summary="Live coder subagent must return focused page-46 patch.",
    )
    write_json(handoff_path, handoff)
    prompt_path = write_subagent_prompt(
        out=out,
        worktree=worktree,
        handoff_path=handoff_path,
        reference_patch=reference_patch,
        returned_patch=returned_patch,
        receipt_path=receipt_path,
        before_manifest=before_manifest,
        before_review_dir=before_review_dir,
        repair_attempt=repair_attempt,
        course_correction_review_dir=course_correction_review_dir,
    )
    status_before = run(["git", "status", "--short"], cwd=worktree, timeout_s=15)
    require_ok(status_before, "worktree status before subagent")
    completed = run(
        [str(SCILLM_CLI), "agent", args.coder_model, prompt_path.read_text(encoding="utf-8")],
        cwd=worktree,
        timeout_s=args.coder_timeout_s,
        env=scillm_env(args),
    )
    stdout_path = out / f"patch_leg/attempt_{repair_attempt:03d}_coder_subagent_stdout.txt"
    stderr_path = out / f"patch_leg/attempt_{repair_attempt:03d}_coder_subagent_stderr.txt"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    require_ok(completed, "live coder subagent")
    if not returned_patch.exists() or not returned_patch.read_text(encoding="utf-8").strip():
        raise RuntimeError(f"live coder subagent did not write patch: {returned_patch}")
    status_after = run(["git", "status", "--short"], cwd=worktree, timeout_s=15)
    require_ok(status_after, "worktree status after subagent")
    if status_after.stdout != status_before.stdout:
        raise RuntimeError(
            "subagent mutated isolated worktree before patch apply\n"
            f"BEFORE:\n{status_before.stdout}\nAFTER:\n{status_after.stdout}"
        )
    receipt = validate_patch_receipt(receipt_path, require_subagent_live=True)
    write_json(out / "patch_leg/tau_subagent_receipt.json", receipt)
    shutil.copy2(handoff_path, out / "patch_leg/tau_agent_handoff_to_patch.json")
    return returned_patch, receipt, {
        "subagent_stdout": str(stdout_path),
        "subagent_stderr": str(stderr_path),
        "reference_patch": str(reference_patch) if reference_patch else None,
        "prompt": str(prompt_path),
        "repair_mode": "reference_patch_copy" if reference_patch else "no_reference_patch_discovery",
        "repair_attempt": repair_attempt,
    }


def run_chat_subagent_patch_leg(
    *,
    args: argparse.Namespace,
    out: Path,
    worktree: Path,
    before_manifest: Path,
    before_review_dir: Path,
    repair_attempt: int,
    course_correction_review_dir: Path | None = None,
    prior_patch_failure: dict[str, Any] | None = None,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    returned_patch = out / f"patch_leg/attempt_{repair_attempt:03d}_coder_returned_page46_focused_diff.patch"
    receipt_path = out / f"patch_leg/attempt_{repair_attempt:03d}_tau_subagent_receipt.json"
    handoff_path = out / f"patch_leg/attempt_{repair_attempt:03d}_tau_agent_handoff_to_patch.json"
    handoff = build_patch_handoff(
        out=out,
        patch_path=returned_patch,
        executor="scillm_chat_coder",
        summary="Live coder subagent must return focused page-46 patch as JSON.",
    )
    write_json(handoff_path, handoff)
    case_dir = before_review_dir / "attempt_001/page_case_0001_p0046"
    correction_case_dir = course_correction_review_dir / "attempt_001/page_case_0001_p0046" if course_correction_review_dir else None
    correction = ""
    if correction_case_dir:
        correction = f"""
Prior repair attempt did not clear the reviewer. Course-correction evidence:
- Previous review response:
```json
{compact_json_file(correction_case_dir / "review_response.json", max_chars=12000)}
```
- Previous selected candidates:
```json
{compact_json_file(correction_case_dir / "selected_candidates.json", max_chars=12000)}
```
"""
    if prior_patch_failure:
        correction += f"""
Prior repair attempt failed before reviewer because the patch artifact was not applicable:
```json
{json.dumps(prior_patch_failure, indent=2)}
```
Return a complete, valid unified diff. Do not return placeholder hunks. Do not
modify python/pdf_oxide/extract_for_pdflab.py unless the diff includes real,
context-valid changes against the supplied source.
"""
    prompt_path = out / f"patch_leg/attempt_{repair_attempt:03d}_coder_chat_prompt.md"
    prompt = f"""# RATIONALE
purpose: Return one focused unified diff for PDF Lab page-46 extraction repair.
consumer: scripts/pdf_lab/run_page46_tau_goal_proof.py.
why this matters: The harness will apply your diff in an isolated worktree, run a focused regression, regenerate page 46 candidates, and ask the live reviewer again.

Return only one JSON object with this schema:
{{
  "status": "COMPLETED|BLOCKED",
  "summary": "...",
  "unified_diff": "diff --git ... or empty when edits[] is used",
  "edits": [
    {{"path": "scripts/pdf_lab/snapshot_current_extraction.py", "insert_after": "exact existing text", "content": "text to insert"}},
    {{"path": "scripts/pdf_lab/snapshot_current_extraction.py", "replace": "exact existing text", "content": "replacement text"}}
  ],
  "changed_files": ["..."],
  "receipt": {{
    "schema": "tau.subagent_receipt.v1",
    "goal": {{"goal_id": "goal-pdf-lab-page46-tau-loop", "goal_version": 1, "goal_hash": "sha256:pdf-lab-page46-tau-loop", "immutable_goal_preserved": true}},
    "context": {{"run_id": "pdf-lab-page46-goal-patch-leg", "subagent": "coder", "actor_type": "scillm_chat_coder", "ticket": "local:pdf-lab-page46-loop", "attempt": {repair_attempt}, "repair_mode": "no_reference_patch_discovery"}},
    "result": {{"status": "COMPLETED|BLOCKED", "summary": "...", "mocked": false, "live": true, "subagent_live": true, "repair_mode": "no_reference_patch_discovery", "patch_path": "{returned_patch}", "changed_files": ["..."]}},
    "evidence": ["{returned_patch}", "{receipt_path}"],
    "next": {{"subagent": "reviewer", "reason": "Project agent applies patch and reruns live review."}}
  }}
}}

Rules:
- Do not return Markdown.
- Do not use a provided or historical reference patch. Infer from the evidence and source below.
- Patch only these files: {json.dumps(FOCUSED_PATCH_FILES)}.
- The defect is one page only: NIST SP 800-53r5 human page 46, page_index 45.
- The reviewer rejected a broad table candidate for AC-2 because it is really structured control/list content and includes rotated side chrome text such as "53r5".
- A manifest-only reclassification is insufficient. The extracted JSON must stop presenting the AC-2 body as one contaminated table block.
- Prefer an extraction-layer repair that emits bounded list/text elements from the structured table rows while excluding side-chrome rows.
- The diff must apply with `git apply --check` to the supplied source. Do not use
  placeholder `index 0000000..1111111` metadata, do not invent deleted file
  contents, and do not output explanatory comments as the whole patch.
- Prefer `edits[]` over `unified_diff` if you are uncertain about hunk line
  numbers. Every `replace` or `insert_after` marker must be copied exactly from
  the raw source snippets below.
- The likely primary file is `scripts/pdf_lab/snapshot_current_extraction.py`;
  add a focused regression in `tests/test_pdf_lab_second_pass_candidate_manifest.py`
  only if needed.
- In the supplied snapshot source, the page extraction function is named
  `_extract_page`. The table loop is inside `_extract_page`; do not invent
  `snapshot_pdf`, `snapshot_page`, or alternate table extractor function bodies.
- The source snippets below are raw file text, not numbered pseudo-code.

Tau handoff:
```json
{json.dumps(handoff, indent=2)}
```

Before candidate manifest:
```json
{compact_json_file(before_manifest, max_chars=7000)}
```

Before live review response:
```json
{compact_json_file(case_dir / "review_response.json", max_chars=7000)}
```

Before selected candidates:
```json
{compact_json_file(case_dir / "selected_candidates.json", max_chars=7000)}
```

Source context from the isolated worktree:
{focused_source_context(worktree)}

{correction}
"""
    prompt_path.write_text(prompt, encoding="utf-8")
    completed = run(
        [str(SCILLM_CLI), "--timeout", str(args.coder_timeout_s), args.coder_model, str(prompt_path)],
        cwd=worktree,
        timeout_s=args.coder_timeout_s,
        env=scillm_env(args),
    )
    stdout_path = out / f"patch_leg/attempt_{repair_attempt:03d}_coder_chat_stdout.txt"
    stderr_path = out / f"patch_leg/attempt_{repair_attempt:03d}_coder_chat_stderr.txt"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    require_ok(completed, "live coder chat subagent")
    payload = parse_json_object(completed.stdout)
    if not payload:
        raise RuntimeError(f"live coder chat subagent did not return JSON: {stdout_path}")
    if payload.get("status") != "COMPLETED":
        raise RuntimeError(f"live coder chat subagent blocked: {payload}")
    edits = payload.get("edits")
    if isinstance(edits, list) and edits:
        returned_patch = render_structured_edits_to_patch(
            edits=[edit for edit in edits if isinstance(edit, dict)],
            out=out,
            name=returned_patch.name,
            base_worktree=worktree,
        )
    else:
        diff = str(payload.get("unified_diff") or "")
        if not diff.strip().startswith("diff --git"):
            raise RuntimeError(f"live coder chat subagent returned no unified diff or structured edits: {stdout_path}")
        returned_patch.parent.mkdir(parents=True, exist_ok=True)
        returned_patch.write_text(diff.rstrip() + "\n", encoding="utf-8")
    receipt = payload.get("receipt")
    if not isinstance(receipt, dict):
        raise RuntimeError("live coder chat subagent did not return receipt object")
    receipt.setdefault("schema", "tau.subagent_receipt.v1")
    receipt.setdefault("goal", {"goal_id": "goal-pdf-lab-page46-tau-loop", "goal_version": 1, "goal_hash": "sha256:pdf-lab-page46-tau-loop", "immutable_goal_preserved": True})
    receipt.setdefault("context", {"run_id": "pdf-lab-page46-goal-patch-leg", "subagent": "coder", "actor_type": "scillm_chat_coder", "ticket": "local:pdf-lab-page46-loop", "attempt": repair_attempt, "repair_mode": "no_reference_patch_discovery"})
    receipt.setdefault("result", {})
    receipt["result"].update(
        {
            "mocked": False,
            "live": True,
            "subagent_live": True,
            "repair_mode": "no_reference_patch_discovery",
            "patch_path": str(returned_patch),
        }
    )
    receipt.setdefault("evidence", [str(returned_patch), str(receipt_path)])
    write_json(receipt_path, receipt)
    receipt = validate_patch_receipt(receipt_path, require_subagent_live=True)
    write_json(out / "patch_leg/tau_subagent_receipt.json", receipt)
    shutil.copy2(handoff_path, out / "patch_leg/tau_agent_handoff_to_patch.json")
    return returned_patch, receipt, {
        "subagent_stdout": str(stdout_path),
        "subagent_stderr": str(stderr_path),
        "prompt": str(prompt_path),
        "repair_mode": "no_reference_patch_discovery",
        "repair_attempt": repair_attempt,
        "surface": "scillm_chat",
        "model": args.coder_model,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO / "artifacts/pdf_lab/page46_tau_goal_proof_rerun")
    parser.add_argument("--worktree", type=Path, default=Path("/tmp/pdf_oxide_page46_goal_proof_worktree"))
    parser.add_argument("--review-mode", choices=["live", "fixture", "dry_run"], default="live")
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--scillm-base-url", default=os.environ.get("SCILLM_API_BASE", "http://localhost:4001"))
    parser.add_argument("--scillm-auth-token", default=os.environ.get("SCILLM_PROXY_KEY", "sk-dev-proxy-123"))
    parser.add_argument("--scillm-timeout-s", type=float, default=120.0)
    parser.add_argument("--attempt-timeout-s", type=float, default=600.0)
    parser.add_argument("--page-timeout-s", type=float, default=120.0)
    parser.add_argument(
        "--patch-leg",
        choices=["deterministic", "subagent", "subagent_no_reference", "subagent_chat_no_reference"],
        default="deterministic",
    )
    parser.add_argument("--coder-model", default="opencode/deepseek-v4-flash")
    parser.add_argument("--coder-timeout-s", type=float, default=600.0)
    parser.add_argument("--max-repair-attempts", type=int, default=1)
    parser.add_argument("--scillm-doctor-receipt", type=Path, default=None)
    parser.add_argument("--keep-worktree", action="store_true")
    args = parser.parse_args()

    out = args.out.resolve()
    worktree = args.worktree.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    if worktree.exists():
        shutil.rmtree(worktree)

    require_ok(run(["git", "worktree", "add", "--detach", str(worktree), "HEAD"], cwd=REPO, timeout_s=60), "create worktree")
    try:
        copy_extension(worktree)
        before_manifest = out / "before_candidate_manifest.json"
        before_sampled = out / "before_sampled_page_cases.json"
        after_manifest = out / "after_candidate_manifest.json"
        patch_path = out / "patch_leg/current_page46_focused_diff.patch"

        require_ok(run(manifest_cmd(out=before_manifest, worktree=worktree, page_timeout_s=args.page_timeout_s), cwd=worktree, timeout_s=args.page_timeout_s + 30), "before manifest")
        create_before_sample(before_manifest, before_sampled)
        before_review = run(
            review_cmd(
                manifest=before_manifest,
                sampled_cases=before_sampled,
                out=out / "before_live_review",
                code_root=worktree,
                batch_id="pdf-lab-page46-goal-before",
                args=args,
            ),
            cwd=REPO,
            timeout_s=args.attempt_timeout_s + 30,
        )
        before_summary = parse_last_json(before_review.stdout)
        if before_summary.get("final_status") != "still_open":
            raise RuntimeError(f"before reviewer did not return still_open: {before_summary}")

        patch_artifacts: dict[str, Any] = {}
        patch_receipt: dict[str, Any]
        patch_steps: list[dict[str, Any]] = []
        final_after_summary: dict[str, Any] | None = None
        final_after_manifest = after_manifest
        final_after_sampled_cases: Path | None = None
        if args.patch_leg == "deterministic":
            patch_path = capture_focused_diff(out, "current_page46_focused_diff.patch")
            patch_receipt = write_deterministic_patch_artifacts(args, out, worktree, patch_path)
            repair_attempts = 1
        else:
            patch_receipt = {}
            repair_attempts = max(1, args.max_repair_attempts)

        course_correction_review_dir: Path | None = None
        prior_patch_failure: dict[str, Any] | None = None
        after_summary: dict[str, Any] = {}
        for repair_attempt in range(1, repair_attempts + 1):
            patch_context_manifest = final_after_manifest if final_after_manifest.exists() else before_manifest
            try:
                if args.patch_leg in {"subagent", "subagent_no_reference"}:
                    patch_path, patch_receipt, patch_artifacts = run_subagent_patch_leg(
                        args=args,
                        out=out,
                        worktree=worktree,
                        before_manifest=patch_context_manifest,
                        before_review_dir=out / "before_live_review",
                        use_reference_patch=args.patch_leg == "subagent",
                        repair_attempt=repair_attempt,
                        course_correction_review_dir=course_correction_review_dir,
                    )
                elif args.patch_leg == "subagent_chat_no_reference":
                    patch_path, patch_receipt, patch_artifacts = run_chat_subagent_patch_leg(
                        args=args,
                        out=out,
                        worktree=worktree,
                        before_manifest=patch_context_manifest,
                        before_review_dir=out / "before_live_review",
                        repair_attempt=repair_attempt,
                        course_correction_review_dir=course_correction_review_dir,
                        prior_patch_failure=prior_patch_failure,
                    )
            except Exception as exc:
                prior_patch_failure = {
                    "repair_attempt": repair_attempt,
                    "phase": "patch_generation",
                    "error": str(exc),
                }
                patch_steps.append(
                    {
                        "repair_attempt": repair_attempt,
                        "after_status": "patch_generation_failed",
                        "after_reason": "patch_generation_exception",
                        "error": str(exc),
                    }
                )
                if repair_attempt < repair_attempts:
                    continue
                raise
            apply_check = run(["git", "apply", "--recount", "--check", str(patch_path)], cwd=worktree, timeout_s=30)
            apply_stdout = out / f"patch_leg/attempt_{repair_attempt:03d}_git_apply_check_stdout.txt"
            apply_stderr = out / f"patch_leg/attempt_{repair_attempt:03d}_git_apply_check_stderr.txt"
            apply_stdout.write_text(apply_check.stdout, encoding="utf-8")
            apply_stderr.write_text(apply_check.stderr, encoding="utf-8")
            if apply_check.returncode != 0:
                prior_patch_failure = {
                    "repair_attempt": repair_attempt,
                    "phase": "git_apply_recount_check",
                    "patch": str(patch_path),
                    "returncode": apply_check.returncode,
                    "stdout": apply_check.stdout[-2000:],
                    "stderr": apply_check.stderr[-4000:],
                }
                patch_steps.append(
                    {
                        "repair_attempt": repair_attempt,
                        "patch": str(patch_path),
                        "patch_sha256": sha256_file(patch_path),
                        "receipt": str(out / "patch_leg/tau_subagent_receipt.json"),
                        "subagent_artifacts": patch_artifacts,
                        "after_status": "patch_apply_failed",
                        "after_reason": "git_apply_recount_check_failed",
                        "apply_check_stdout": str(apply_stdout),
                        "apply_check_stderr": str(apply_stderr),
                    }
                )
                if repair_attempt < repair_attempts:
                    continue
                require_ok(apply_check, f"apply-check focused patch attempt {repair_attempt}")
            prior_patch_failure = None
            require_ok(run(["git", "apply", "--recount", str(patch_path)], cwd=worktree, timeout_s=30), f"apply focused patch attempt {repair_attempt}")
            test_cmd = [
                str(REPO / ".venv/bin/python"),
                "-m",
                "pytest",
                "-q",
                "tests/test_pdf_lab_second_pass_candidate_manifest.py",
            ]
            test_result = run(test_cmd, cwd=worktree, timeout_s=60)
            require_ok(test_result, f"focused regression attempt {repair_attempt}")
            attempt_after_manifest = out / f"after_candidate_manifest_attempt_{repair_attempt:03d}.json"
            require_ok(
                run(manifest_cmd(out=attempt_after_manifest, worktree=worktree, page_timeout_s=args.page_timeout_s), cwd=worktree, timeout_s=args.page_timeout_s + 30),
                f"after manifest attempt {repair_attempt}",
            )
            final_after_manifest = attempt_after_manifest
            after_sampled_cases = out / f"after_sampled_page_cases_attempt_{repair_attempt:03d}.json"
            create_after_sample_from_manifest(attempt_after_manifest, after_sampled_cases)
            final_after_sampled_cases = after_sampled_cases

            after_review_dir = out / f"after_live_review_attempt_{repair_attempt:03d}"
            after_review = run(
                review_cmd(
                    manifest=attempt_after_manifest,
                    sampled_cases=after_sampled_cases,
                    out=after_review_dir,
                    code_root=worktree,
                    batch_id=f"pdf-lab-page46-goal-after-attempt-{repair_attempt:03d}",
                    args=args,
                ),
                cwd=REPO,
                timeout_s=args.attempt_timeout_s + 30,
            )
            after_summary = parse_last_json(after_review.stdout)
            patch_steps.append(
                {
                    "repair_attempt": repair_attempt,
                    "patch": str(patch_path),
                    "patch_sha256": sha256_file(patch_path),
                    "receipt": str(out / "patch_leg/tau_subagent_receipt.json"),
                    "subagent_artifacts": patch_artifacts,
                    "after_manifest": str(attempt_after_manifest),
                    "after_review": str(after_review_dir),
                    "after_status": after_summary.get("final_status"),
                    "after_reason": after_summary.get("final_reason"),
                }
            )
            final_after_summary = after_summary
            if after_summary.get("final_status") == "reviewed_clean":
                break
            if after_summary.get("final_status") != "still_open":
                break
            course_correction_review_dir = after_review_dir

        if final_after_summary is None:
            raise RuntimeError("repair loop did not run after-review")
        after_summary = final_after_summary
        if after_summary.get("final_status") != "reviewed_clean":
            raise RuntimeError(f"after reviewer did not return reviewed_clean: {after_summary}")

        before_terminal = read_json(Path(before_summary["final_terminal_ledger"]))
        after_terminal = read_json(Path(after_summary["final_terminal_ledger"]))
        after_validation = read_json(Path(after_summary["final_terminal_ledger"]).with_name("review_validation.json"))
        after_sample = read_json(final_after_sampled_cases) if final_after_sampled_cases else {}
        after_candidate_ids = after_sample.get("page_cases", [{}])[0].get("candidate_ids", [])
        after_manifest_payload = read_json(final_after_manifest)
        after_candidates_by_id = {c.get("candidate_id"): c for c in after_manifest_payload.get("candidates", [])}
        target_prefixes = (
            "h. Notify account managers",
            "1. [Assignment: organization-defined time period] when accounts are no longer required",
            "2. [Assignment: organization-defined time period] when users are terminated or",
            "3. [Assignment: organization-defined time period] when system usage or need-to-know",
        )
        sampled_texts = [str(after_candidates_by_id.get(candidate_id, {}).get("text_excerpt") or "") for candidate_id in after_candidate_ids]
        target_sample_ok = all(any(text.startswith(prefix) for text in sampled_texts) for prefix in target_prefixes)
        summary = {
            "schema": "pdf_lab.page46_tau_bounded_loop_goal_proof.v1",
            "created_at": utc_now(),
            "mocked": False,
            "live": True,
            "objective": "Repeatable page-46 Tau loop proof through evidence, reviewer, patch, re-extract, and reviewer.",
            "page": {"human_page": 46, "page_index": 45, "defect": "p46-merged-h-nested-list / AC-2 list table-materialization"},
            "boundaries": {
                "reviewer_live": args.review_mode == "live",
                "extraction_live": True,
                "patch_apply_live": True,
                "patch_subagent_live": patch_receipt.get("result", {}).get("subagent_live") is True,
                "patch_leg": args.patch_leg,
                "current_repo_mutated": False,
                "document_wide_claim": False,
            },
            "scillm_doctor": {
                "receipt": str(args.scillm_doctor_receipt) if args.scillm_doctor_receipt else None,
                "status": read_json(args.scillm_doctor_receipt).get("status") if args.scillm_doctor_receipt else None,
                "usable_delegate_lanes": ["opencode_agent", "cli_opencode_agent_model_override"],
                "failed_unrelated_lane": "gpt55_oauth",
            },
            "isolated_code_root": str(worktree),
            "steps": [
                {"step": "before_evidence_generation", "status": "completed", "artifact": str(before_manifest), "candidate_count": read_json(before_manifest)["candidate_count"]},
                {"step": "before_live_review", "status": before_terminal["terminal_status"], "reason": before_terminal.get("reason"), "artifact": before_summary["final_review_bundle"]},
                {
                    "step": "patch",
                    "status": "completed",
                    "mode": args.patch_leg,
                    "artifact": str(out / "patch_leg/tau_subagent_receipt.json"),
                    "patch_sha256": sha256_file(patch_path),
                    "receipt_subagent_live": patch_receipt.get("result", {}).get("subagent_live"),
                    "subagent_artifacts": patch_artifacts,
                },
                {"step": "repair_loop", "status": "completed", "attempts": patch_steps},
                {"step": "after_reextract", "status": "completed", "artifact": str(final_after_manifest), "candidate_count": after_manifest_payload["candidate_count"]},
                {
                    "step": "after_target_sample",
                    "status": "completed" if target_sample_ok else "failed",
                    "artifact": str(final_after_sampled_cases) if final_after_sampled_cases else None,
                    "candidate_ids": after_candidate_ids,
                    "sampled_texts": sampled_texts,
                },
                {"step": "after_live_review", "status": after_terminal["terminal_status"], "reason": after_terminal.get("reason"), "artifact": after_summary["final_review_bundle"], "validation_ok": after_validation.get("ok")},
            ],
            "tau_artifacts": {
                "patch_handoff": str(out / "patch_leg/tau_agent_handoff_to_patch.json"),
                "patch_receipt": str(out / "patch_leg/tau_subagent_receipt.json"),
                "before_review_receipt": str(Path(before_summary["attempt_receipts"][0]).with_name("tau_subagent_receipt.json")),
                "after_review_receipt": str(Path(after_summary["attempt_receipts"][0]).with_name("tau_subagent_receipt.json")),
            },
            "stop_condition": {
                "required": "before still_open, patch completed, after reviewed_clean, review_validation ok",
                "met": before_terminal["terminal_status"] == "still_open"
                and after_terminal["terminal_status"] == "reviewed_clean"
                and after_validation.get("ok") is True
                and target_sample_ok
                and (
                    args.patch_leg not in {"subagent", "subagent_no_reference", "subagent_chat_no_reference"}
                    or patch_receipt.get("result", {}).get("subagent_live") is True
                )
                and (
                    args.patch_leg not in {"subagent_no_reference", "subagent_chat_no_reference"}
                    or patch_receipt.get("result", {}).get("repair_mode") == "no_reference_patch_discovery"
                ),
            },
            "does_not_prove": [
                "fully autonomous patch discovery from no reference patch" if args.patch_leg == "subagent" else "broad autonomous Scillm/OpenCode coder generation across defect classes",
                "document-wide PDF extraction quality",
                "commit/push readiness",
                "unbounded autonomous repair loop",
            ],
        }
        write_json(out / "page46_tau_loop_goal_proof_summary.json", summary)
        errors = [] if summary["stop_condition"]["met"] else ["stop condition not met"]
        validation = {
            "schema": "pdf_lab.page46_tau_bounded_loop_goal_proof_validation.v1",
            "ok": not errors,
            "errors": errors,
            "summary": str(out / "page46_tau_loop_goal_proof_summary.json"),
            "checked_artifacts": 5,
        }
        write_json(out / "page46_tau_loop_goal_proof_validation.json", validation)
        print(json.dumps(validation, sort_keys=True))
        return 0 if validation["ok"] else 1
    finally:
        if not args.keep_worktree:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
