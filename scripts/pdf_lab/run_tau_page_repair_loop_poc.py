#!/usr/bin/env python3
"""Bounded one-page PDF Lab repair loop proof-of-concept.

This wrapper intentionally keeps the loop simple. It delegates one attempt to
``run_page_second_pass_dag.py``, reads the attempt terminal ledger, and decides
whether another attempt is allowed. It does not claim semantic success by
itself; the terminal ledger and review bundle remain the evidence.
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


REPO = Path(__file__).resolve().parents[2]
TERMINAL_SUCCESS = {"reviewed_clean", "patched_confirmed", "rejected_with_proof"}
TERMINAL_STOP = TERMINAL_SUCCESS | {"blocked_substrate", "human_needed"}
LOOP_SCHEMA = "pdf_lab.tau_page_repair_loop_poc.v1"
TAU_GOAL_ID = "goal-pdf-lab-one-page-repair-loop"
TAU_GOAL_HASH = "sha256:pdf-lab-one-page-repair-loop"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def parse_attempt_stdout(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def build_attempt_command(args: argparse.Namespace, attempt_dir: Path) -> list[str]:
    cmd = [
        str(args.attempt_python),
        str(REPO / "scripts/pdf_lab/run_page_second_pass_dag.py"),
        "--pdf",
        str(args.pdf),
        "--manifest",
        str(args.manifest),
        "--sampled-cases",
        str(args.sampled_cases),
        "--out",
        str(attempt_dir),
        "--apply-mode",
        args.apply_mode,
        "--dpi",
        str(args.dpi),
        "--model",
        args.model,
        "--batch-id",
        f"{args.batch_id}-attempt-{args.start_attempt + args.current_attempt - 1:03d}",
        "--review-mode",
        args.review_mode,
        "--scillm-base-url",
        args.scillm_base_url,
        "--scillm-auth-token",
        args.scillm_auth_token,
        "--caller-skill",
        args.caller_skill,
        "--scillm-timeout-s",
        str(args.scillm_timeout_s),
        "--scillm-preflight-mode",
        args.scillm_preflight_mode,
        "--patch-mode",
        args.patch_mode,
        "--patch-backend",
        args.patch_backend,
        "--opencode-agent",
        args.opencode_agent,
        "--patch-prompt-profile",
        args.patch_prompt_profile,
        "--repair-strategy",
        args.repair_strategy,
        "--opencode-timeout-s",
        str(args.opencode_timeout_s),
        "--commit-mode",
        args.commit_mode,
        "--code-root",
        str(args.code_root),
        "--page-orchestrator-mode",
        args.page_orchestrator_mode,
    ]
    if args.case_id:
        cmd.extend(["--case-id", args.case_id])
    if args.page is not None:
        cmd.extend(["--page", str(args.page)])
    if args.ledger:
        cmd.extend(["--ledger", str(args.ledger)])
    if args.review_fixture:
        cmd.extend(["--review-fixture", str(args.review_fixture)])
    if args.review_after_fixture:
        cmd.extend(["--review-after-fixture", str(args.review_after_fixture)])
    if not args.review_include_images:
        cmd.append("--no-review-include-images")
    if args.opencode_model:
        cmd.extend(["--opencode-model", args.opencode_model])
    if args.page_extract_timeout_s is not None:
        cmd.extend(["--page-extract-timeout-s", str(args.page_extract_timeout_s)])
    for agent in args.opencode_agent_sequence or []:
        cmd.extend(["--opencode-agent-sequence", agent])
    for skill in args.opencode_skill or []:
        cmd.extend(["--opencode-skill", skill])
    for prefix in args.allowed_patch_prefix or []:
        cmd.extend(["--allowed-patch-prefix", prefix])
    for validation_command in args.validation_command or []:
        cmd.extend(["--validation-command", validation_command])
    if args.opencode_keep_session:
        cmd.append("--opencode-keep-session")
    return cmd


def tau_result_status(terminal_status: str, returncode: int) -> str:
    if terminal_status in TERMINAL_SUCCESS:
        return "COMPLETED"
    if terminal_status in {"blocked_substrate", "human_needed", "attempt_failed"} or returncode != 0:
        return "BLOCKED"
    if terminal_status == "still_open":
        return "NEEDS_CHANGES"
    return "INSUFFICIENT_EVIDENCE"


def next_tau_agent(terminal_status: str) -> dict[str, str]:
    if terminal_status in TERMINAL_SUCCESS:
        return {
            "subagent": "human",
            "reason": "Inspect the final terminal ledger before any broader batch claim.",
            "executor": "human",
        }
    if terminal_status == "still_open":
        return {
            "subagent": "coder",
            "reason": "The reviewer still sees an actionable defect or the dry-run stopped before mutation.",
            "executor": "either",
        }
    if terminal_status == "human_needed":
        return {
            "subagent": "human",
            "reason": "The evidence gate requested human judgement.",
            "executor": "human",
        }
    return {
        "subagent": "reviewer",
        "reason": "The loop stopped before a clean repair receipt and needs evidence review.",
        "executor": "either",
    }


def write_tau_handoff(
    *,
    path: Path,
    run_id: str,
    attempt_number: int,
    next_agent_name: str,
    summary: str,
    evidence: list[str],
    reason: str,
) -> dict[str, Any]:
    handoff = {
        "schema": "tau.agent_handoff.v1",
        "github": {
            "repo": "grahama1970/pdf_oxide",
            "target": "local",
        },
        "goal": {
            "goal_id": TAU_GOAL_ID,
            "goal_version": 1,
            "goal_hash": TAU_GOAL_HASH,
        },
        "previous_subagent": "pdf-lab-tau-loop",
        "context": {
            "run_id": run_id,
            "summary": summary,
            "artifacts": evidence,
            "attempt": attempt_number,
        },
        "result": {
            "status": "NEEDS_REVIEW",
            "summary": summary,
            "evidence": evidence,
        },
        "rationale": reason,
        "next_agent": {
            "name": next_agent_name,
            "executor": "either" if next_agent_name != "human" else "human",
            "reason": reason,
        },
        "required_evidence": [
            "PDF Lab terminal ledger exists and validates.",
            "Review bundle contains page image, annotated candidate image, extraction JSON, review request, validation, and HTML review artifact.",
        ],
        "stop_condition": "Next actor returns tau.subagent_receipt.v1 and PDF Lab terminal ledger remains valid.",
    }
    write_json(path, handoff)
    return handoff


def write_tau_subagent_receipt(
    *,
    path: Path,
    run_id: str,
    attempt_number: int,
    terminal_status: str,
    terminal_reason: str,
    returncode: int,
    command: list[str],
    artifacts: list[str],
    mocked: bool,
    live: bool,
) -> dict[str, Any]:
    next_agent = next_tau_agent(terminal_status)
    receipt = {
        "schema": "tau.subagent_receipt.v1",
        "goal": {
            "goal_id": TAU_GOAL_ID,
            "goal_version": 1,
            "goal_hash": TAU_GOAL_HASH,
            "immutable_goal_preserved": True,
        },
        "context": {
            "run_id": run_id,
            "subagent": "pdf-lab-tau-loop",
            "actor_type": "tau",
            "ticket": "local:pdf-lab-page-repair-loop",
            "artifacts_read": artifacts,
            "assumptions": [
                "Human page numbers are one-based; pdf_oxide page_index is page_number - 1.",
                "The PDF Lab terminal ledger is the authority for attempt status.",
            ],
            "unknowns": [],
            "attempt": attempt_number,
        },
        "result": {
            "status": tau_result_status(terminal_status, returncode),
            "summary": f"Attempt {attempt_number} ended with terminal_status={terminal_status} reason={terminal_reason or 'none'}.",
            "mocked": mocked,
            "live": live,
            "artifacts": artifacts,
            "commands_run": [" ".join(command)],
            "returncode": returncode,
            "terminal_status": terminal_status,
            "terminal_reason": terminal_reason,
        },
        "rationale": "The next transition is determined only from the PDF Lab terminal ledger and validation artifacts.",
        "evidence": artifacts,
        "next": next_agent,
        "stop_condition": "Loop stops on clean/confirmed/blocker/human-needed or bounded max attempts.",
    }
    write_json(path, receipt)
    return receipt


def record_project_knowledge(args: argparse.Namespace, receipt: dict[str, Any]) -> dict[str, Any]:
    if not args.record_project_knowledge:
        return {"attempted": False, "reason": "record_project_knowledge_disabled"}

    project_knowledge = REPO / "PROJECT_KNOWLEDGE.md"
    if not project_knowledge.is_file():
        return {"attempted": True, "ok": False, "error": "PROJECT_KNOWLEDGE.md missing"}

    terminal = receipt.get("terminal") if isinstance(receipt.get("terminal"), dict) else {}
    if terminal.get("terminal_status") != "patched_confirmed":
        return {"attempted": False, "reason": "terminal_status_not_patched_confirmed"}

    line = (
        f"- {utc_now()} Tau POC loop recorded patched page "
        f"{terminal.get('page_number')} with case {terminal.get('case_id')}; "
        f"terminal ledger: {receipt.get('terminal_ledger_path')}; "
        f"review bundle: {receipt.get('review_bundle_path')}."
    )
    with project_knowledge.open("a", encoding="utf-8") as handle:
        handle.write("\n## PDF Lab Tau Loop POC Notes\n\n")
        handle.write(line + "\n")
    return {"attempted": True, "ok": True, "path": str(project_knowledge)}


def record_memory(args: argparse.Namespace, receipt: dict[str, Any]) -> dict[str, Any]:
    if not args.record_memory:
        return {"attempted": False, "reason": "record_memory_disabled"}

    terminal = receipt.get("terminal") if isinstance(receipt.get("terminal"), dict) else {}
    if terminal.get("terminal_status") != "patched_confirmed":
        return {"attempted": False, "reason": "terminal_status_not_patched_confirmed"}

    try:
        import httpx
    except ImportError as exc:
        return {"attempted": True, "ok": False, "error": f"httpx unavailable: {exc}"}

    document = {
        "_key": f"pdf_oxide:fixed_page:{terminal.get('case_id')}:{terminal.get('page_number')}",
        "schema": "pdf_lab.fixed_page_memory.v1",
        "project": "pdf_oxide",
        "kind": "fixed_pdf_page",
        "page_number": terminal.get("page_number"),
        "case_id": terminal.get("case_id"),
        "terminal_status": terminal.get("terminal_status"),
        "terminal_reason": terminal.get("terminal_reason"),
        "terminal_ledger_path": receipt.get("terminal_ledger_path"),
        "review_bundle_path": receipt.get("review_bundle_path"),
        "recorded_at": utc_now(),
        "tags": ["pdf_lab", "pdf_oxide", "fixed_pdf_page", "project:pdf_oxide"],
    }
    try:
        with httpx.Client(base_url=args.memory_base_url, timeout=10.0) as client:
            response = client.post(
                "/store",
                json={"collection": "project_knowledge", "document": document},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # noqa: BLE001 - receipt must record failed persistence.
        return {"attempted": True, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"attempted": True, "ok": True, "response": payload}


def run_loop(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "loop_events.jsonl"
    attempts: list[dict[str, Any]] = []
    final_status = "max_attempts_exhausted"
    final_reason = ""
    final_receipt: dict[str, Any] | None = None

    for attempt in range(1, args.max_attempts + 1):
        args.current_attempt = attempt
        attempt_number = args.start_attempt + attempt - 1
        attempt_dir = out_dir / f"attempt_{attempt_number:03d}"
        run_id = f"{args.batch_id}-attempt-{attempt_number:03d}"
        write_tau_handoff(
            path=attempt_dir / "tau_agent_handoff.json",
            run_id=run_id,
            attempt_number=attempt_number,
            next_agent_name="reviewer",
            summary=f"Run PDF Lab one-page repair attempt {attempt_number}.",
            evidence=[],
            reason="Review the current page evidence before allowing a bounded patch attempt.",
        )
        cmd = build_attempt_command(args, attempt_dir)
        started_at = utc_now()
        completed = subprocess.run(
            cmd,
            cwd=REPO,
            text=True,
            capture_output=True,
            env=os.environ.copy(),
            timeout=args.attempt_timeout_s,
            check=False,
        )
        finished_at = utc_now()
        parsed = parse_attempt_stdout(completed.stdout)
        case_dir = Path(parsed["case_dir"]).resolve() if isinstance(parsed.get("case_dir"), str) else None
        terminal_path = case_dir / "terminal_ledger.json" if case_dir else None
        terminal_validation_path = case_dir / "terminal_ledger_validation.json" if case_dir else None
        review_bundle_path = case_dir / "review_bundle.zip" if case_dir else None

        terminal: dict[str, Any] = {}
        terminal_validation: dict[str, Any] = {}
        read_errors: list[str] = []
        if terminal_path and terminal_path.is_file():
            terminal = read_json(terminal_path)
        else:
            read_errors.append("terminal_ledger.json missing")
        if terminal_validation_path and terminal_validation_path.is_file():
            terminal_validation = read_json(terminal_validation_path)
        else:
            read_errors.append("terminal_ledger_validation.json missing")

        terminal_status = str(terminal.get("terminal_status") or parsed.get("terminal_status") or "attempt_failed")
        terminal_reason = str(terminal.get("terminal_reason") or "")
        artifacts = [
            path
            for path in [
                str(terminal_path) if terminal_path else None,
                str(terminal_validation_path) if terminal_validation_path else None,
                str(review_bundle_path) if review_bundle_path else None,
            ]
            if path
        ]
        tau_receipt = write_tau_subagent_receipt(
            path=attempt_dir / "tau_subagent_receipt.json",
            run_id=run_id,
            attempt_number=attempt_number,
            terminal_status=terminal_status,
            terminal_reason=terminal_reason,
            returncode=completed.returncode,
            command=cmd,
            artifacts=artifacts,
            mocked=args.review_mode != "live" or args.patch_mode != "live" or args.commit_mode != "live",
            live=args.review_mode == "live" or args.patch_mode == "live" or args.commit_mode == "live",
        )
        attempt_receipt = {
            "schema": "pdf_lab.tau_page_repair_loop_attempt.v1",
            "attempt": attempt_number,
            "started_at": started_at,
            "finished_at": finished_at,
            "command": cmd,
            "returncode": completed.returncode,
            "case_dir": str(case_dir) if case_dir else None,
            "terminal_ledger_path": str(terminal_path) if terminal_path else None,
            "terminal_validation_path": str(terminal_validation_path) if terminal_validation_path else None,
            "review_bundle_path": str(review_bundle_path) if review_bundle_path else None,
            "terminal": terminal,
            "terminal_validation": terminal_validation,
            "terminal_status": terminal_status,
            "terminal_reason": terminal_reason,
            "tau_agent_handoff_path": str(attempt_dir / "tau_agent_handoff.json"),
            "tau_subagent_receipt_path": str(attempt_dir / "tau_subagent_receipt.json"),
            "tau_subagent_receipt": tau_receipt,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
            "read_errors": read_errors,
        }
        write_json(attempt_dir / "tau_loop_attempt_receipt.json", attempt_receipt)
        append_jsonl(events_path, attempt_receipt)
        attempts.append(attempt_receipt)
        final_receipt = attempt_receipt

        if completed.returncode != 0 and not terminal:
            final_status = "attempt_command_failed"
            final_reason = f"returncode={completed.returncode}"
            break
        if terminal_status in TERMINAL_STOP:
            final_status = terminal_status
            final_reason = terminal_reason
            break
        if terminal_status != "still_open":
            final_status = "unexpected_terminal_status"
            final_reason = terminal_status
            break
        if args.stop_after_dry_run and (
            args.review_mode == "dry_run" or args.patch_mode == "dry_run" or args.commit_mode == "dry_run"
        ):
            final_status = terminal_status
            final_reason = terminal_reason or "dry_run_stop"
            break
        final_status = terminal_status
        final_reason = terminal_reason

    summary = {
        "schema": LOOP_SCHEMA,
        "created_at": utc_now(),
        "tau_role": "bounded_loop_harness",
        "mocked": args.review_mode != "live" or args.patch_mode != "live" or args.commit_mode != "live",
        "live": args.review_mode == "live" or args.patch_mode == "live" or args.commit_mode == "live",
        "proof_scope": "bounded one-page command orchestration; semantic correctness depends on attempt review bundles",
        "page": args.page,
        "case_id": args.case_id,
        "max_attempts": args.max_attempts,
        "attempt_count": len(attempts),
        "final_status": final_status,
        "final_reason": final_reason,
        "attempt_receipts": [str(out_dir / f"attempt_{args.start_attempt + index:03d}" / "tau_loop_attempt_receipt.json") for index in range(len(attempts))],
        "loop_events": str(events_path),
        "final_terminal_ledger": final_receipt.get("terminal_ledger_path") if final_receipt else None,
        "final_review_bundle": final_receipt.get("review_bundle_path") if final_receipt else None,
        "does_not_prove": [
            "document-wide extraction quality",
            "unbounded autonomous repair",
            "semantic correctness without live review evidence",
        ],
    }
    if final_receipt:
        summary["project_knowledge_record"] = record_project_knowledge(args, final_receipt)
        summary["memory_record"] = record_memory(args, final_receipt)
    write_json(out_dir / "tau_loop_summary.json", summary)
    return summary


def loop_exit_code(summary: dict[str, Any]) -> int:
    if summary.get("final_status") in TERMINAL_SUCCESS:
        return 0
    if (
        summary.get("final_status") == "still_open"
        and summary.get("final_reason") == "dry_run_stop"
        and summary.get("mocked") is True
        and summary.get("live") is False
    ):
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--sampled-cases", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--page", type=int)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--start-attempt", type=int, default=1)
    parser.add_argument("--attempt-timeout-s", type=float, default=1800.0)
    parser.add_argument("--attempt-python", type=Path, default=Path(os.environ.get("PDF_LAB_ATTEMPT_PYTHON", sys.executable)))
    parser.add_argument("--apply-mode", default="release")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument("--batch-id", default="pdf-lab-tau-loop-poc")
    parser.add_argument("--review-mode", choices=["dry_run", "live", "fixture"], default="dry_run")
    parser.add_argument("--review-fixture", type=Path)
    parser.add_argument("--review-after-fixture", type=Path)
    parser.add_argument("--review-include-images", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--scillm-base-url", default=os.environ.get("SCILLM_API_BASE", "http://localhost:4001"))
    parser.add_argument("--scillm-auth-token", default=os.environ.get("SCILLM_PROXY_KEY", "sk-dev-proxy-123"))
    parser.add_argument("--caller-skill", default="pdf-lab")
    parser.add_argument("--scillm-timeout-s", type=float, default=180.0)
    parser.add_argument("--scillm-preflight-mode", choices=["dry_run", "live"], default="live")
    parser.add_argument("--patch-mode", choices=["dry_run", "live"], default="dry_run")
    parser.add_argument("--patch-backend", choices=["opencode_serve", "scillm_orchestrator"], default="scillm_orchestrator")
    parser.add_argument("--opencode-agent", default="build")
    parser.add_argument("--opencode-agent-sequence", action="append")
    parser.add_argument("--opencode-model")
    parser.add_argument("--patch-prompt-profile", choices=["full", "compact", "plan_only"], default="compact")
    parser.add_argument("--repair-strategy", choices=["single", "split", "chat_plan_split"], default="single")
    parser.add_argument("--opencode-timeout-s", type=float, default=600.0)
    parser.add_argument("--opencode-keep-session", action="store_true")
    parser.add_argument("--opencode-skill", action="append")
    parser.add_argument("--allowed-patch-prefix", action="append")
    parser.add_argument("--validation-command", action="append")
    parser.add_argument("--commit-mode", choices=["dry_run", "live"], default="dry_run")
    parser.add_argument("--code-root", type=Path, default=REPO)
    parser.add_argument("--page-extract-timeout-s", type=float)
    parser.add_argument("--page-orchestrator-mode", choices=["dry_run", "live"], default="dry_run")
    parser.add_argument("--stop-after-dry-run", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--record-project-knowledge", action="store_true")
    parser.add_argument("--record-memory", action="store_true")
    parser.add_argument("--memory-base-url", default="http://127.0.0.1:8601")
    args = parser.parse_args()

    if not args.case_id and args.page is None:
        print("one of --case-id or --page is required", file=sys.stderr)
        return 2
    if args.max_attempts < 1:
        print("--max-attempts must be >= 1", file=sys.stderr)
        return 2

    try:
        summary = run_loop(args)
    except subprocess.TimeoutExpired as exc:
        summary = {
            "schema": LOOP_SCHEMA,
            "created_at": utc_now(),
            "final_status": "attempt_timeout",
            "final_reason": str(exc),
            "mocked": args.review_mode != "live" or args.patch_mode != "live" or args.commit_mode != "live",
            "live": args.review_mode == "live" or args.patch_mode == "live" or args.commit_mode == "live",
        }
        write_json(args.out / "tau_loop_summary.json", summary)
        print(json.dumps(summary, sort_keys=True))
        return 124

    print(json.dumps(summary, sort_keys=True))
    return loop_exit_code(summary)


if __name__ == "__main__":
    raise SystemExit(main())
