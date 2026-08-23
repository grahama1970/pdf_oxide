"""Agentic second-pass adjudication harness for historical findings.

Two subcommands:

  packet  --page N        Print a compact adjudication packet for one page:
                          every unverified finding on that page plus the fresh
                          block evidence relevant to it (type counts and block
                          excerpts matching the finding's block_ids). The
                          adjudicating agent reads this and writes decisions.

  apply   --decisions F   Apply a decisions JSONL to the ledger. Each line:
                          {"page": N, "finding_id": "...",
                           "status": "resolved_by_current_extraction"|"still_broken",
                           "evidence": "quoted fresh-evidence justification"}
                          Every decision must name a finding that exists and is
                          currently unverified; unknown findings fail closed.
                          The updated ledger and a delta report are written
                          next to the input ledger.

The harness never decides anything itself. It moves evidence to the agent and
records the agent's decisions with provenance (commit, evidence root, time).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from census_adjudication_events import (
    append_adjudication_event,
    migrate_legacy_adjudications,
    project_current_statuses,
)


REPO = Path(__file__).resolve().parents[2]
CENSUS = REPO / "artifacts/pdf_lab/census_regen_20260820"
LEDGER = CENSUS / "seed.json"
EVIDENCE = CENSUS / "current_evidence/pages"
TOKEN_COUNTER_SWEEP = CENSUS / "token_counter_sweep_issue31.json"
TEXT_LOSS_PATTERN = re.compile(
    r"\b(text[- ]loss|truncat(?:e|ed|ion)?|missing|omitted|lost)\b",
    re.IGNORECASE,
)


def _load_ledger() -> dict:
    return json.loads(LEDGER.read_text())


def _text_loss_resolution_needs_token_counter(entry: dict, decision: dict) -> bool:
    if decision.get("status") != "resolved_by_current_extraction":
        return False
    text = " ".join(
        str(entry.get(key) or "")
        for key in (
            "finding_id",
            "extraction_defect",
            "expected_type_or_action",
            "visible_evidence",
            "patch_hint",
            "next_action",
        )
    )
    return bool(TEXT_LOSS_PATTERN.search(text))


def _run_token_counter_preflight() -> tuple[bool, dict]:
    command = [
        sys.executable,
        str(REPO / "scripts/pdf_lab/check_word_fidelity_sweep.py"),
        "--output",
        str(TOKEN_COUNTER_SWEEP),
        "--require-region-token-fidelity",
    ]
    result = subprocess.run(command, capture_output=True, text=True, cwd=REPO)
    report: dict = {
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
        "artifact": str(TOKEN_COUNTER_SWEEP),
    }
    if TOKEN_COUNTER_SWEEP.exists():
        try:
            data = json.loads(TOKEN_COUNTER_SWEEP.read_text())
        except json.JSONDecodeError as exc:
            report["artifact_error"] = str(exc)
        else:
            report["artifact_readback"] = {
                "pages_swept": data.get("pages_swept"),
                "bulk_closure_count": data.get("bulk_closure_count"),
                "page_counter_mismatches": data.get("page_counter_mismatches"),
                "region_token_loss_total": data.get("region_token_loss_total"),
                "region_token_gain_total": data.get("region_token_gain_total"),
                "region_token_fidelity_passed": data.get("region_token_fidelity_passed"),
                "passed": data.get("passed"),
            }
    return result.returncode == 0, report


def _page_blocks(page: int) -> list[dict]:
    path = EVIDENCE / f"page_{page:04d}" / "release_extraction_blocks.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data.get("blocks") or data


def cmd_packet(page: int) -> int:
    ledger = _load_ledger()
    project_current_statuses(ledger)
    entries = [
        e
        for e in ledger["entries"]
        if e.get("page") == page and e.get("current_status") == "unverified"
    ]
    blocks = _page_blocks(page)
    type_counts = Counter(b.get("type") for b in blocks)
    packet: dict = {
        "page": page,
        "fresh_block_count": len(blocks),
        "fresh_type_counts": dict(type_counts),
        "findings": [],
    }
    for e in entries:
        wanted = set(e.get("block_ids") or [])
        related = [
            {
                "id": b.get("id"),
                "type": b.get("type"),
                "semantic_role": b.get("semantic_role"),
                "text": str(b.get("text") or "")[:160],
            }
            for b in blocks
            if b.get("id") in wanted
        ]
        packet["findings"].append(
            {
                "finding_id": e["finding_id"],
                "severity": e.get("severity"),
                "owner": e.get("owner"),
                "extraction_defect": e.get("extraction_defect"),
                "expected_type_or_action": e.get("expected_type_or_action"),
                "visible_evidence": e.get("visible_evidence"),
                "historical_block_ids": sorted(wanted)[:8],
                "matching_fresh_blocks": related[:10],
            }
        )
    print(json.dumps(packet, indent=1, ensure_ascii=False))
    return 0


def cmd_apply(decisions_path: Path) -> int:
    ledger = _load_ledger()
    project_current_statuses(ledger)
    index = {
        (e.get("page"), e.get("finding_id")): e
        for e in ledger["entries"]
    }
    decisions = [
        json.loads(line)
        for line in decisions_path.read_text().splitlines()
        if line.strip()
    ]
    gated_keys = []
    for d in decisions:
        entry = index.get((d["page"], d["finding_id"]))
        if entry and _text_loss_resolution_needs_token_counter(entry, d):
            gated_keys.append([d["page"], d["finding_id"]])
    preflight_report = None
    if gated_keys:
        ok, preflight_report = _run_token_counter_preflight()
        if not ok:
            print(
                json.dumps(
                    {
                        "applied": 0,
                        "problems": [
                            "token Counter sweep preflight failed for text-loss closure"
                        ],
                        "gated_keys": gated_keys,
                        "token_counter_preflight": preflight_report,
                    },
                    indent=1,
                    ensure_ascii=False,
                )
            )
            return 1

    applied, problems = [], []
    for d in decisions:
        key = (d["page"], d["finding_id"])
        entry = index.get(key)
        if entry is None:
            problems.append(f"unknown finding {key}")
            continue
        if entry.get("current_status") not in ("unverified", "still_broken"):
            problems.append(
                f"{key} is {entry.get('current_status')}; only unverified or "
                "still_broken entries may be (re-)adjudicated"
            )
            continue
        if d["status"] not in (
            "resolved_by_current_extraction",
            "still_broken",
            "flag_for_human",
        ):
            problems.append(f"{key}: bad status {d['status']!r}")
            continue
        if not str(d.get("evidence", "")).strip():
            problems.append(f"{key}: decision has no quoted evidence")
            continue

        event = {
            "event_type": "human_flag" if d["status"] == "flag_for_human" else "adjudication_decision",
            "adjudicator": "claude-agentic-second-pass",
            "status": d["status"],
            "decided_at": datetime.now(timezone.utc).isoformat(),
            "evidence_root": str(EVIDENCE),
            "commit": subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO
            ).stdout.strip(),
            "evidence": d["evidence"],
        }
        append_adjudication_event(entry, event)
        applied.append(key)

    if problems:
        report = {"applied": 0, "problems": problems}
        if preflight_report is not None:
            report["gated_keys"] = gated_keys
            report["token_counter_preflight"] = preflight_report
        print(json.dumps(report, indent=1, ensure_ascii=False))
        return 1
    counts = project_current_statuses(ledger)
    LEDGER.write_text(json.dumps(ledger, indent=1, ensure_ascii=False))
    report = {
        "applied": len(applied),
        "applied_keys": [list(k) for k in applied],
        "status_counts": counts,
    }
    if preflight_report is not None:
        report["gated_keys"] = gated_keys
        report["token_counter_preflight"] = preflight_report
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


def cmd_migrate_events(check_only: bool) -> int:
    ledger = _load_ledger()
    before = Counter(e.get("current_status") for e in ledger["entries"])
    migration = migrate_legacy_adjudications(ledger)
    after = Counter(e.get("current_status") for e in ledger["entries"])
    report = {
        **migration,
        "before_status_counts": dict(sorted(before.items())),
        "after_status_counts": dict(sorted(after.items())),
        "counts_match": before == after,
        "check_only": check_only,
    }
    if before != after:
        print(json.dumps(report, indent=1, ensure_ascii=False))
        return 1
    if not check_only:
        LEDGER.write_text(json.dumps(ledger, indent=1, ensure_ascii=False))
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("packet")
    p1.add_argument("--page", type=int, required=True)
    p2 = sub.add_parser("apply")
    p2.add_argument("--decisions", type=Path, required=True)
    p3 = sub.add_parser("migrate-events")
    p3.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    if args.cmd == "packet":
        return cmd_packet(args.page)
    if args.cmd == "migrate-events":
        return cmd_migrate_events(args.check_only)
    return cmd_apply(args.decisions)


if __name__ == "__main__":
    sys.exit(main())
