#!/usr/bin/env python3
"""Minimal scillm exec two-call runner for one PDF Lab page (poll/tail aware)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from scillm_exec_poll import run_scillm_cursor_exec

ALLOWLIST = [
    "python/pdf_oxide/extract_for_pdflab.py",
    "src/extractors/block_classifier.rs",
    "src/tables/mod.rs",
    "src/tables/text_assign.rs",
    "src/tables/types.rs",
]
SCILLM_URL = "http://127.0.0.1:4001"
HEADERS = {
    "Authorization": "Bearer sk-dev-proxy-123",
    "Content-Type": "application/json",
    "X-Caller-Skill": "pdf-oxide-exec-two-call",
}
PROJECT_PAGES = Path(
    "/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/pdf-lab-projects/nist-phase54-toc-backed/pages"
)


def load_diagnosis(page: int) -> dict[str, Any]:
    path = PROJECT_PAGES / f"page_{page:04d}" / "agent_second_pass.json"
    evidence = []
    if path.exists():
        for item in json.loads(path.read_text()).get("fix_error_requests", []):
            evidence.append({
                "source_id": item.get("source_id") or item.get("id"),
                "issue": item.get("issue") or item.get("reason"),
            })
    return {
        "page": page,
        "should_fix": bool(evidence),
        "fix_error_request_count": len(evidence),
        "evidence": evidence[:20],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--diagnose-only", action="store_true")
    args = parser.parse_args()

    ad = Path(args.artifact_dir)
    ad.mkdir(parents=True, exist_ok=True)
    repo = Path.cwd()
    dx = load_diagnosis(args.page)

    c1 = run_scillm_cursor_exec(
        repo,
        prompt="Diagnose page %s. No edits. Return JSON only.\n%s" % (args.page, json.dumps(dx, indent=2)),
        skills_csv="review-extraction,extract-pdf",
        artifact_dir=ad,
        label="call1",
        profile="cursor-plan",
        scillm_url=SCILLM_URL,
        headers=HEADERS,
        force=False,
        timeout_s=600,
        idle_timeout_s=120,
        allow_write_paths=ALLOWLIST,
    )
    summary: dict[str, Any] = {
        "page": args.page,
        "baseline_fix_errors": dx["fix_error_request_count"],
        "call1": c1,
    }

    if not args.diagnose_only and dx["should_fix"]:
        c2 = run_scillm_cursor_exec(
            repo,
            prompt="Fix page %s. Allowlist only: %s. Return JSON only.\n%s" % (args.page, ALLOWLIST, json.dumps(dx, indent=2)),
            skills_csv="best-practices-rust,best-practices-python,extract-pdf",
            artifact_dir=ad,
            label="call2",
            profile="cursor-auto",
            scillm_url=SCILLM_URL,
            headers=HEADERS,
            force=True,
            timeout_s=1200,
            idle_timeout_s=300,
            allow_write_paths=ALLOWLIST,
        )
        summary["call2"] = c2
        summary["verdict"] = "call2_ok" if c2["ok"] else "call2_failed"
    else:
        summary["call2"] = {"skipped": True}
        summary["verdict"] = "diagnose_only" if args.diagnose_only else "no_fix_needed"

    (ad / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["verdict"] in {"diagnose_only", "no_fix_needed", "call2_ok"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
