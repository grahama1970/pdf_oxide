#!/usr/bin/env python3
"""Run a bounded two-call PDF Lab page repair loop.

This intentionally fails closed around prior failure modes:
- `cargo check` must pass before any agent repair call.
- Only allowlisted files may remain modified after the agent call.
- `cargo check` must pass after the repair call.
- `maturin develop` is gated behind postflight cargo check (runs when allowlisted files change).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sys

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from scillm_exec_poll import run_scillm_cursor_exec

import os

# Hard cap: each scillm cursor_exec call must finish within 5 minutes.
EXEC_CALL_MAX_TIMEOUT_S = 300.0


def _bounded_timeout(env_key: str, default_s: float) -> float:
    raw = float(os.environ.get(env_key, str(default_s)))
    return min(max(raw, 30.0), EXEC_CALL_MAX_TIMEOUT_S)


CALL1_TIMEOUT_S = _bounded_timeout("PDF_LAB_CALL1_TIMEOUT_S", 180.0)
CALL2_TIMEOUT_S = _bounded_timeout("PDF_LAB_CALL2_TIMEOUT_S", 300.0)
CALL1_IDLE_TIMEOUT_S = min(
    float(os.environ.get("PDF_LAB_CALL1_IDLE_TIMEOUT_S", "60")),
    CALL1_TIMEOUT_S / 2,
)
CALL2_IDLE_TIMEOUT_S = min(
    float(os.environ.get("PDF_LAB_CALL2_IDLE_TIMEOUT_S", "90")),
    CALL2_TIMEOUT_S / 2,
)
CALL1_MAX_TOOLS = int(os.environ.get("PDF_LAB_CALL1_MAX_TOOLS", "20"))
CALL2_MAX_TOOLS = int(os.environ.get("PDF_LAB_CALL2_MAX_TOOLS", "60"))


SCILLM_URL = os.environ.get("SCILLM_URL", "http://127.0.0.1:4001")
SCILLM_HEADERS = {
    "Authorization": os.environ.get("SCILLM_AUTH", "Bearer sk-dev-proxy-123"),
    "Content-Type": "application/json",
    "X-Caller-Skill": "pdf-oxide-exec-two-call",
}
DEFAULT_SCILLM_CALL1_PROFILE = "cursor-plan"
DEFAULT_SCILLM_CALL2_PROFILE = "cursor-auto"

ALLOWLIST = {
    "python/pdf_oxide/extract_for_pdflab.py",
    "src/tables/mod.rs",
    "src/tables/text_assign.rs",
    "src/tables/types.rs",
    "src/extractors/block_classifier.rs",
}
HARNESS_PATH_PREFIXES = (
    ".scillm/",
    ".cursor/rules/pdf-lab-exec-selected-skills/",
)
DEFAULT_BACKEND = "scillm-cursor"
DEFAULT_CURSOR_MODEL = "auto"
DEFAULT_PI_MODEL = "minimax-m2.7"
DEFAULT_MODEL_PROVIDER = "opencode-go"
CURSOR_SCRIPT = Path(__file__).resolve().parent / "run_cursor_selected_skills.sh"
SKILLS_ROOT = Path(os.environ.get("SKILLS_ROOT", Path.home() / ".claude/skills"))
CALL1_SKILLS = "review-extraction,extract-pdf"
CALL2_SKILLS = "best-practices-rust,best-practices-python,extract-pdf"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(cmd: list[str], *, cwd: Path, log_path: Path | None = None, input_text: str | None = None, timeout: int | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        env=env,
    )
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps(
                {
                    "command": cmd,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                    "finished_at": utc_now(),
                },
                indent=2,
            )
            + "\\n"
        )
    return proc


def load_cursor_api_key(env: dict[str, str]) -> None:
    if env.get("CURSOR_API_KEY"):
        return
    zshrc = Path.home() / ".zshrc"
    if not zshrc.exists():
        return
    for line in zshrc.read_text().splitlines():
        if line.startswith("export CURSOR_API_KEY="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            env["CURSOR_API_KEY"] = value
            return


def git_changed_files(repo: Path) -> list[str]:
    proc = run(["git", "diff", "--name-only"], cwd=repo)
    files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    proc_cached = run(["git", "diff", "--cached", "--name-only"], cwd=repo)
    files.extend(line.strip() for line in proc_cached.stdout.splitlines() if line.strip())
    return sorted(set(files))


def is_harness_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in HARNESS_PATH_PREFIXES)


def restore_paths(repo: Path, paths: list[str]) -> None:
    if paths:
        run(["git", "checkout", "--", *paths], cwd=repo)


def load_or_build_diagnosis(repo: Path, page: int, artifact_dir: Path) -> dict[str, Any]:
    existing = artifact_dir / "deterministic_diagnosis.json"
    if existing.exists():
        return json.loads(existing.read_text())

    candidate = Path(
        "/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/pdf-lab-projects/nist-phase54-toc-backed/pages"
    ) / f"page_{page:04d}" / "agent_second_pass.json"
    evidence: list[dict[str, Any]] = []
    if candidate.exists():
        payload = json.loads(candidate.read_text())
        for item in payload.get("fix_error_requests", []):
            evidence.append(
                {
                    "artifact": "agent_second_pass.json",
                    "source_id": item.get("source_id") or item.get("id"),
                    "current_type": item.get("current_type") or item.get("family"),
                    "requested_family": item.get("requested_family") or item.get("requested_type"),
                    "issue": item.get("issue") or item.get("reason"),
                }
            )
    diagnosis = {
        "page": page,
        "status": "disparity_confirmed" if evidence else "missing_fix_error_evidence",
        "should_fix": bool(evidence),
        "symptoms": [
            "table-cell or chrome fix_error_requests require deterministic repair",
        ],
        "evidence": evidence,
        "likely_owner": "pdf_oxide_core_or_preset",
        "recommended_lane": "core_extraction",
        "fix_error_request_count": len(evidence),
        "deterministic_baseline": True,
        "generated_at": utc_now(),
    }
    existing.write_text(json.dumps(diagnosis, indent=2)  + "\n")
    return diagnosis


def build_diagnose_prompt(page: int, diagnosis: dict[str, Any]) -> str:
    return (
        f"Call 1: diagnose only for PDF Lab page {page}. Do not edit any files.\\n"
        "Review the deterministic diagnosis and confirm or refine it.\\n"
        "Return one JSON object only with keys: status, should_fix, symptoms, evidence, "
        "likely_owner, recommended_lane, hypothesis, verification_plan.\\n\\n"
        "Deterministic diagnosis:\\n"
        + json.dumps(diagnosis, indent=2)
    )


def build_fix_prompt(page: int, diagnosis: dict[str, Any]) -> str:
    return (
        f"Call 2: minimal allowlisted fix for PDF Lab page {page}.\\n"
        "You are repairing pdf_oxide extraction behavior.\\n"
        "HARD RULES:\\n"
        "- Touch only these allowlisted files:\\n"
        + "".join(f"  - {path}\\n" for path in sorted(ALLOWLIST))
        + "- Do not edit tests, plan-iterate files, PROJECT_KNOWLEDGE.md, UX Lab UI, Cargo.toml, or pyproject.toml.\\n"
        "- Prefer the smallest deterministic Rust/Python extraction fix.\\n"
        "- Return one JSON object only: status, files_touched, hypothesis, verification_plan.\\n\\n"
        "Diagnosis:\\n"
        + json.dumps(diagnosis, indent=2)
    )


def _parse_jsonish(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        obj = json.loads(stripped)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(stripped[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def parse_cursor_meta(meta: dict[str, Any]) -> dict[str, Any] | None:
    result = meta.get("result")
    if not isinstance(result, dict):
        return None
    text = result.get("result")
    if not isinstance(text, str):
        return None
    return _parse_jsonish(text)


def run_cursor_agent(
    repo: Path,
    *,
    prompt: str,
    skills_csv: str,
    artifact_dir: Path,
    label: str,
    model: str,
    plan_mode: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    if not CURSOR_SCRIPT.exists():
        raise FileNotFoundError(f"missing cursor harness script: {CURSOR_SCRIPT}")

    run_ctx = artifact_dir / f"cursor_{label}"
    events_path = artifact_dir / f"{label}_events.jsonl"
    prompt_path = artifact_dir / f"{label}_prompt.txt"
    prompt_path.write_text(prompt)

    env = os.environ.copy()
    load_cursor_api_key(env)
    timeout_s = CALL2_TIMEOUT_S if label.startswith("call2") else CALL1_TIMEOUT_S
    env.update(
        {
            "WORKSPACE": str(repo),
            "SKILLS_ROOT": str(SKILLS_ROOT),
            "SKILLS_CSV": skills_csv,
            "PROMPT_FILE": str(prompt_path),
            "RUN_CTX": str(run_ctx),
            "EVENTS_OUT": str(events_path),
            "CURSOR_MODEL": model,
            "CURSOR_MODE": "plan" if plan_mode else "",
            "CURSOR_FORCE": "1" if force else "0",
            "TIMEOUT_S": str(int(timeout_s)),
        }
    )

    started = datetime.now(timezone.utc)
    proc = subprocess.run(
        [str(CURSOR_SCRIPT)],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=1200,
        env=env,
    )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()

    meta_path = run_ctx / "run_meta.json"
    meta: dict[str, Any] = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
    elif proc.stdout.strip():
        try:
            meta = json.loads(proc.stdout.strip().splitlines()[-1])
        except json.JSONDecodeError:
            meta = {}

    parsed = parse_cursor_meta(meta)
    result = meta.get("result") if isinstance(meta.get("result"), dict) else {}
    receipt = {
        "label": label,
        "backend": "cursor-headless",
        "model": model,
        "skills_csv": skills_csv,
        "run_ctx": str(run_ctx),
        "events_path": str(events_path),
        "meta_path": str(meta_path),
        "exit_code": proc.returncode,
        "agent_exit_code": meta.get("agent_exit_code", proc.returncode),
        "elapsed_s": elapsed,
        "stderr_tail": proc.stderr[-2000:],
        "event_line_count": len(events_path.read_text().splitlines()) if events_path.exists() else 0,
        "tool_call_count": meta.get("tool_call_count"),
        "session_id": meta.get("session_id"),
        "api_key_source": meta.get("api_key_source"),
        "is_error": result.get("is_error"),
        "duration_ms": result.get("duration_ms"),
        "parsed": parsed,
        "ok": proc.returncode == 0 and result.get("is_error") is False,
    }
    (artifact_dir / f"{label}_receipt.json").write_text(json.dumps(receipt, indent=2)  + "\n")
    return receipt


def _parse_pi_json_events(stdout: str) -> dict[str, Any]:
    text_parts: list[str] = []
    final_message: dict[str, Any] | None = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            final_message = message
    if final_message:
        for part in final_message.get("content", []):
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
    return {"text": "".join(text_parts).strip()}


def run_opencode(repo: Path, prompt: str, model: str, artifact_dir: Path, label: str = "call2_fix") -> dict[str, Any]:
    model_name = model.split("/", 1)[-1] if "/" in model else model
    events_path = artifact_dir / f"{label}_events.jsonl"
    cmd = [
        os.environ.get("PI_BIN", "/home/graham/bin/pi"),
        "--mode",
        "json",
        "--provider",
        DEFAULT_MODEL_PROVIDER,
        "--model",
        model_name,
        "--no-session",
        "--no-skills",
        "--no-extensions",
        "--no-prompt-templates",
        "--no-themes",
        "--thinking",
        "off",
        "--tools",
        "read,grep,find,ls,edit,write",
        "-p",
        prompt,
    ]
    started = datetime.now(timezone.utc)
    proc = subprocess.run(cmd, cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1200)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    events_path.write_text(proc.stdout)
    extracted = _parse_pi_json_events(proc.stdout)
    parsed = _parse_jsonish(extracted.get("text", ""))
    receipt = {
        "label": label,
        "backend": "pi-opencode-go",
        "model": model,
        "model_id": f"{DEFAULT_MODEL_PROVIDER}/{model_name}",
        "exit_code": proc.returncode,
        "elapsed_s": elapsed,
        "stderr_tail": proc.stderr[-2000:],
        "events_path": str(events_path),
        "event_line_count": len(proc.stdout.splitlines()),
        "parsed": parsed,
        "ok": proc.returncode == 0 and isinstance(parsed, dict),
    }
    (artifact_dir / f"{label}_receipt.json").write_text(json.dumps(receipt, indent=2)  + "\n")
    return receipt


def merge_diagnosis(base: dict[str, Any], agent: dict[str, Any] | None) -> dict[str, Any]:
    if not agent:
        return base
    merged = dict(base)
    for key in ("status", "should_fix", "symptoms", "evidence", "likely_owner", "recommended_lane"):
        if key in agent:
            merged[key] = agent[key]
    merged["agent_diagnosis"] = agent
    merged["merged_at"] = utc_now()
    return merged




def run_closure_verification(repo: Path, page: int, artifact_dir: Path, *, maturin: bool) -> dict[str, Any]:
    closure_script = repo / "scripts/pdf_lab/run_page_repair_closure.py"
    closure_artifact = artifact_dir / "closure"
    cmd = [
        sys.executable,
        str(closure_script),
        "--page",
        str(page),
        "--artifact-dir",
        str(closure_artifact),
    ]
    if maturin:
        cmd.append("--maturin")
    proc = subprocess.run(cmd, cwd=repo, text=True, capture_output=True, timeout=1800)
    report_path = closure_artifact / "closure_report.json"
    report: dict[str, Any] = {}
    if report_path.exists():
        report = json.loads(report_path.read_text())
    return {
        "exit_code": proc.returncode,
        "artifact_dir": str(closure_artifact),
        "report": report,
        "stderr_tail": proc.stderr[-2000:],
    }

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--backend", default=DEFAULT_BACKEND, choices=["cursor", "scillm-cursor", "pi-opencode"])
    parser.add_argument("--cursor-model", default=DEFAULT_CURSOR_MODEL)
    parser.add_argument("--pi-model", default=DEFAULT_PI_MODEL)
    parser.add_argument("--skip-call1", action="store_true", help="Skip Cursor/plan diagnose agent call")
    parser.add_argument("--skip-call2", action="store_true", help="Skip fix agent call (diagnose-only)")
    parser.add_argument("--verify-closure", action="store_true", help="Run rematerialize closure after fix")
    parser.add_argument("--skip-rebuild", action="store_true")
    parser.add_argument("--artifact-dir")
    args = parser.parse_args()

    repo = Path.cwd()
    suffix = args.cursor_model.replace(".", "") if args.backend == "cursor" else args.pi_model.replace(".", "")
    artifact_dir = Path(args.artifact_dir or f"artifacts/pdf_lab/exec_two_call/page_{args.page:04d}_{suffix}")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "schema": "pdf_oxide.exec_two_call_page_repair.v1",
        "started_at": utc_now(),
        "page": args.page,
        "artifact_dir": str(artifact_dir),
        "backend": args.backend,
        "cursor_model": args.cursor_model,
        "pi_model": args.pi_model,
        "allowlist": sorted(ALLOWLIST),
    }

    pre = run(["cargo", "check"], cwd=repo, log_path=artifact_dir / "preflight_cargo_check.json", timeout=600)
    summary["preflight_cargo_check"] = {"exit_code": pre.returncode, "log": str(artifact_dir / "preflight_cargo_check.json")}
    if pre.returncode != 0:
        summary["verdict"] = "preflight_cargo_check_failed"
        summary["finished_at"] = utc_now()
        (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
        return 2

    diagnosis = load_or_build_diagnosis(repo, args.page, artifact_dir)

    call1_receipt: dict[str, Any] | None = None
    if args.backend in {"cursor", "scillm-cursor"} and not args.skip_call1:
        diagnose_prompt = build_diagnose_prompt(args.page, diagnosis)
        if args.backend == "scillm-cursor":
            call1_receipt = run_scillm_cursor_exec(
                repo,
                prompt=diagnose_prompt,
                skills_csv=CALL1_SKILLS,
                artifact_dir=artifact_dir,
                label="call1_diagnose",
                profile=DEFAULT_SCILLM_CALL1_PROFILE,
                scillm_url=SCILLM_URL,
                headers=SCILLM_HEADERS,
                force=False,
                timeout_s=CALL1_TIMEOUT_S,
                idle_timeout_s=CALL1_IDLE_TIMEOUT_S,
                max_tool_calls=CALL1_MAX_TOOLS,
                allow_write_paths=None,
            )
        else:
            call1_receipt = run_cursor_agent(
                repo,
                prompt=diagnose_prompt,
                skills_csv=CALL1_SKILLS,
                artifact_dir=artifact_dir,
                label="call1_diagnose",
                model=args.cursor_model,
                plan_mode=True,
                force=False,
            )
        diagnosis = merge_diagnosis(diagnosis, call1_receipt.get("parsed"))
        summary["call1"] = call1_receipt

    (artifact_dir / "diagnosis.json").write_text(json.dumps(diagnosis, indent=2)  + "\n")
    fix_prompt = build_fix_prompt(args.page, diagnosis)

    before_changed = git_changed_files(repo)

    if args.skip_call2 or not diagnosis.get("should_fix"):
        summary.update({
            "call2": {"skipped": True, "reason": "skip_call2" if args.skip_call2 else "should_fix_false"},
            "verdict": "diagnose_only" if args.skip_call2 else "no_fix_needed",
            "allowlist_touched": [],
        })
        (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
        raise SystemExit(0)

    if call1_receipt is not None and not call1_receipt.get("ok"):
        summary.update({
            "call1": call1_receipt,
            "call2": {"skipped": True, "reason": "call1_not_ok"},
            "verdict": "call1_diagnose_failed",
            "allowlist_touched": [],
            "finished_at": utc_now(),
        })
        (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, indent=2))
        raise SystemExit(5)

    if args.backend == "scillm-cursor":
        call2_receipt = run_scillm_cursor_exec(
            repo,
            prompt=fix_prompt,
            skills_csv=CALL2_SKILLS,
            artifact_dir=artifact_dir,
            label="call2_fix",
            profile=DEFAULT_SCILLM_CALL2_PROFILE,
            scillm_url=SCILLM_URL,
            headers=SCILLM_HEADERS,
            force=True,
            timeout_s=CALL2_TIMEOUT_S,
            idle_timeout_s=CALL2_IDLE_TIMEOUT_S,
            max_tool_calls=CALL2_MAX_TOOLS,
            allow_write_paths=sorted(ALLOWLIST),
        )
    elif args.backend == "cursor":
        call2_receipt = run_cursor_agent(
            repo,
            prompt=fix_prompt,
            skills_csv=CALL2_SKILLS,
            artifact_dir=artifact_dir,
            label="call2_fix",
            model=args.cursor_model,
            plan_mode=False,
            force=True,
        )
    else:
        call2_receipt = run_opencode(repo, fix_prompt, args.pi_model, artifact_dir, label="call2_fix")

    after_changed = git_changed_files(repo)
    non_allowlisted = [
        path
        for path in after_changed
        if path not in ALLOWLIST and not is_harness_path(path) and not path.startswith(str(artifact_dir))
    ]
    if non_allowlisted:
        restore_paths(repo, non_allowlisted)
    remaining_changed = git_changed_files(repo)
    remaining_non_allowlisted = [path for path in remaining_changed if path not in ALLOWLIST and not is_harness_path(path)]

    post = run(["cargo", "check"], cwd=repo, log_path=artifact_dir / "postflight_cargo_check.json", timeout=600)
    allowlist_touched = [path for path in remaining_changed if path in ALLOWLIST]
    should_rebuild = bool(allowlist_touched) and not args.skip_rebuild

    summary.update(
        {
            "before_changed": before_changed,
            "call2": call2_receipt,
            "changed_after_call": after_changed,
            "reverted_non_allowlisted": non_allowlisted,
            "remaining_changed": remaining_changed,
            "remaining_non_allowlisted": remaining_non_allowlisted,
            "allowlist_touched": allowlist_touched,
            "postflight_cargo_check": {"exit_code": post.returncode, "log": str(artifact_dir / "postflight_cargo_check.json")},
            "rebuild": {"skipped": not should_rebuild, "reason": "no allowlist touches" if not allowlist_touched else None},
        }
    )

    if remaining_non_allowlisted:
        summary["verdict"] = "non_allowlisted_changes_remaining"
        code = 3
    elif post.returncode != 0:
        summary["verdict"] = "postflight_cargo_check_failed"
        code = 4
    elif call1_receipt is not None and not call1_receipt.get("ok"):
        summary["verdict"] = "call1_diagnose_failed"
        code = 5
    elif not call2_receipt.get("ok"):
        summary["verdict"] = "agent_call_failed"
        code = 6
    elif should_rebuild:
        maturin = run(["uv", "run", "maturin", "develop"], cwd=repo, log_path=artifact_dir / "maturin_develop.json", timeout=900)
        summary["rebuild"] = {
            "skipped": False,
            "exit_code": maturin.returncode,
            "log": str(artifact_dir / "maturin_develop.json"),
        }
        code = 0 if maturin.returncode == 0 else 7
        summary["verdict"] = "pipeline_ok" if code == 0 else "maturin_failed"
    else:
        summary["verdict"] = "pipeline_ok"
        code = 0

    if args.verify_closure and summary.get("verdict") == "pipeline_ok":
        summary["closure"] = run_closure_verification(
            repo,
            args.page,
            artifact_dir,
            maturin=should_rebuild,
        )
        closure_report = summary["closure"].get("report") or {}
        summary["fix_error_delta"] = (closure_report.get("after_rematerialize") or {}).get("fix_error_delta")
        summary["closure_verdict"] = closure_report.get("verdict")

    summary["finished_at"] = utc_now()
    (artifact_dir / "summary.json").write_text(json.dumps(summary, indent=2)  + "\n")
    print(json.dumps(summary, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
