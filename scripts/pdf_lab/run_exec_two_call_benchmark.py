#!/usr/bin/env python3
"""Run the two-call Cursor exec loop across PDF Lab candidate pages and roll up evidence.

Designed for tonight's exec-vs-agents decision:
- Phase-54 packet has 10 candidate pages; 4 currently have open fix_error_requests.
- Pages run sequentially (shared workspace); optional git reset between pages.
- Emits rollup JSON + markdown with pass/fail gates for staying on exec vs moving to /scillm/agents.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
EXEC = REPO / "scripts/pdf_lab/exec_two_call_page_repair.py"
PACKET_MANIFEST = (
    REPO
    / ".plan-iterate/phase-54-toc-backed-candidate-page-selection/evidence-artifacts/nist-toc-backed-candidate-packet/manifest.json"
)
PROJECT_PAGES = Path(
    "/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/"
    "pdf-lab-projects/nist-phase54-toc-backed/pages"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_packet_pages() -> list[int]:
    manifest = json.loads(PACKET_MANIFEST.read_text())
    return sorted(int(entry["page"]) for entry in manifest.get("pages", []))


def fix_error_count(page: int) -> int:
    path = PROJECT_PAGES / f"page_{page:04d}" / "agent_second_pass.json"
    if not path.exists():
        return -1
    return len(json.loads(path.read_text()).get("fix_error_requests") or [])


def git_snapshot(repo: Path) -> str:
    proc = subprocess.run(["git", "stash", "push", "-u", "-m", "exec-benchmark-snapshot"], cwd=repo, text=True, capture_output=True)
    return proc.stdout.strip() or proc.stderr.strip()


def git_reset(repo: Path) -> None:
    allowlist = [
        "python/pdf_oxide/extract_for_pdflab.py",
        "src/tables/mod.rs",
        "src/tables/text_assign.rs",
        "src/tables/types.rs",
        "src/extractors/block_classifier.rs",
    ]
    subprocess.run(["git", "checkout", "--", *allowlist], cwd=repo, check=False)


def run_page(
    page: int,
    *,
    backend: str,
    cursor_model: str,
    artifact_root: Path,
    verify_closure: bool,
    skip_call1: bool,
    diagnose_only: bool,
) -> dict[str, Any]:
    baseline = fix_error_count(page)
    artifact_dir = artifact_root / f"page_{page:04d}"
    cmd = [
        sys.executable,
        str(EXEC),
        "--page",
        str(page),
        "--backend",
        backend,
        "--cursor-model",
        cursor_model,
        "--artifact-dir",
        str(artifact_dir),
    ]
    if skip_call1:
        cmd.append("--skip-call1")
    if diagnose_only:
        cmd.append("--skip-call2")
    if verify_closure and not diagnose_only:
        cmd.append("--verify-closure")

    proc = subprocess.run(cmd, cwd=REPO, text=True, capture_output=True)
    summary: dict[str, Any] = {}
    summary_path = artifact_dir / "summary.json"
    if summary_path.exists():
        raw = summary_path.read_text()
    try:
        summary = json.loads(raw)
    except json.JSONDecodeError:
        summary, _ = json.JSONDecoder().raw_decode(raw)

    call1 = summary.get("call1") or {}
    call2 = summary.get("call2") or {}
    closure = (summary.get("closure") or {}).get("report") or {}

    return {
        "page": page,
        "baseline_fix_errors": baseline,
        "exit_code": proc.returncode,
        "verdict": summary.get("verdict"),
        "should_fix": (summary.get("diagnosis") or {}).get("should_fix"),
        "call1_ok": call1.get("ok"),
        "call1_elapsed_s": call1.get("elapsed_s"),
        "call1_tools": call1.get("tool_call_count"),
        "call2_skipped": call2.get("skipped"),
        "call2_ok": call2.get("ok"),
        "call2_elapsed_s": call2.get("elapsed_s"),
        "call2_tools": call2.get("tool_call_count"),
        "allowlist_touched": summary.get("allowlist_touched") or [],
        "closure_verdict": closure.get("verdict"),
        "fix_error_delta": closure.get("fix_error_delta"),
        "after_fix_errors": closure.get("after_fix_error_count"),
        "artifact_dir": str(artifact_dir),
        "stderr_tail": proc.stderr[-1500:],
    }


def decide(rows: list[dict[str, Any]]) -> dict[str, Any]:
    open_rows = [r for r in rows if (r.get("baseline_fix_errors") or 0) > 0]
    closed_rows = [r for r in rows if (r.get("baseline_fix_errors") or 0) == 0]

    timeouts = sum(1 for r in rows if r.get("verdict") == "agent_call_failed" and (r.get("call2_elapsed_s") or 0) >= 1190)
    call2_ok = sum(1 for r in open_rows if r.get("call2_ok"))
    improved = sum(1 for r in open_rows if r.get("closure_verdict") == "improved")
    closed_by_fix = sum(1 for r in open_rows if r.get("closure_verdict") == "closed")
    false_positive_call2 = sum(1 for r in closed_rows if not r.get("call2_skipped"))

    exec_pass = (
        false_positive_call2 == 0
        and timeouts == 0
        and call2_ok >= max(1, len(open_rows) // 2)
        and (closed_by_fix + improved) >= max(1, len(open_rows) // 2)
    )

    return {
        "recommendation": "stay_on_cursor_exec" if exec_pass else "promote_scillm_standing_agent",
        "exec_pass": exec_pass,
        "open_pages": len(open_rows),
        "closed_control_pages": len(closed_rows),
        "call2_ok_open": call2_ok,
        "closure_improved": improved,
        "closure_closed": closed_by_fix,
        "timeouts": timeouts,
        "closed_pages_ran_call2": false_positive_call2,
        "gates": {
            "skip_call2_on_closed_pages": false_positive_call2 == 0,
            "no_timeouts": timeouts == 0,
            "majority_call2_ok": call2_ok >= max(1, len(open_rows) // 2),
            "majority_closure_gain": (closed_by_fix + improved) >= max(1, len(open_rows) // 2),
        },
    }


def write_markdown(path: Path, rows: list[dict[str, Any]], decision: dict[str, Any], meta: dict[str, Any]) -> None:
    lines = [
        "# PDF Lab two-call exec benchmark",
        "",
        f"- started: {meta['started_at']}",
        f"- finished: {meta['finished_at']}",
        f"- backend: {meta['backend']} / {meta['cursor_model']}",
        f"- pages: {meta['pages']}",
        "",
        f"## Decision: **{decision['recommendation']}**",
        "",
        "| gate | pass |",
        "|------|------|",
    ]
    for gate, ok in decision["gates"].items():
        lines.append(f"| {gate} | {'yes' if ok else 'no'} |")
    lines.extend(["", "## Per-page", "", "| page | baseline | verdict | call1 | call2 | closure | delta |", "|------|----------|---------|-------|-------|---------|-------|"])
    for r in rows:
        lines.append(
            "| {page} | {baseline} | {verdict} | {c1} | {c2} | {closure} | {delta} |".format(
                page=r["page"],
                baseline=r.get("baseline_fix_errors"),
                verdict=r.get("verdict"),
                c1="ok" if r.get("call1_ok") else ("skip" if r.get("call1_ok") is None else "fail"),
                c2="skip" if r.get("call2_skipped") else ("ok" if r.get("call2_ok") else "fail"),
                closure=r.get("closure_verdict") or "-",
                delta=r.get("fix_error_delta") if r.get("fix_error_delta") is not None else "-",
            )
        )
    lines.extend(
        [
            "",
            "## If promoting to /scillm/agents",
            "",
            "- Register `pdf-oxide-implementer` with worker_worktree + declared_write_set matching exec allowlist.",
            "- Keep call1 on cursor plan or move to read-only `scillm-reviewer`.",
            "- Use standing agent for call2 on table-heavy pages (27/456/468) where multi-turn clustering is likely.",
        ]
    )
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", help="Comma-separated page numbers; default = full phase-54 packet")
    parser.add_argument("--open-only", action="store_true", help="Only pages with fix_error_requests > 0")
    parser.add_argument("--diagnose-only", action="store_true", help="Call1 only; skip call2 for all pages")
    parser.add_argument("--skip-call1", action="store_true")
    parser.add_argument("--backend", default="cursor")
    parser.add_argument("--cursor-model", default="composer-2.5")
    parser.add_argument("--verify-closure", action="store_true", help="Re-extract + rematerialize after each fix")
    parser.add_argument("--reset-between-pages", action="store_true", help="git checkout/clean between pages")
    parser.add_argument("--artifact-dir", type=Path)
    args = parser.parse_args()

    pages = [int(p.strip()) for p in args.pages.split(",")] if args.pages else load_packet_pages()
    if args.open_only:
        pages = [p for p in pages if fix_error_count(p) > 0]

    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    artifact_root = args.artifact_dir or (REPO / "artifacts/pdf_lab/exec_two_call/benchmark" / ts)
    artifact_root.mkdir(parents=True, exist_ok=True)

    meta = {
        "schema": "pdf_oxide.exec_two_call_benchmark.v1",
        "started_at": utc_now(),
        "backend": args.backend,
        "cursor_model": args.cursor_model,
        "pages": pages,
        "open_only": args.open_only,
        "diagnose_only": args.diagnose_only,
        "verify_closure": args.verify_closure,
        "reset_between_pages": args.reset_between_pages,
        "artifact_root": str(artifact_root),
    }

    rows: list[dict[str, Any]] = []
    for page in pages:
        if args.reset_between_pages:
            git_reset(REPO)
        row = run_page(
            page,
            backend=args.backend,
            cursor_model=args.cursor_model,
            artifact_root=artifact_root,
            verify_closure=args.verify_closure,
            skip_call1=args.skip_call1,
            diagnose_only=args.diagnose_only,
        )
        rows.append(row)
        (artifact_root / "progress.jsonl").open("a").write(json.dumps(row) + "\n")

    decision = decide(rows)
    meta["finished_at"] = utc_now()
    meta["decision"] = decision
    meta["rows"] = rows

    (artifact_root / "rollup.json").write_text(json.dumps(meta, indent=2) + "\n")
    write_markdown(artifact_root / "rollup.md", rows, decision, meta)
    print(json.dumps({"artifact_root": str(artifact_root), "decision": decision}, indent=2))
    return 0 if decision["exec_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
