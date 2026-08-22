#!/usr/bin/env python3
"""Issue #34 guard for append-only census adjudication events."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

from census_adjudication_events import (
    latest_adjudicated_status,
    migrate_legacy_adjudications,
    project_current_statuses,
)


REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "artifacts/pdf_lab/census_regen_20260820/seed.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def counts_by_status(ledger: dict) -> dict[str, int]:
    counts = Counter(entry.get("current_status") for entry in ledger.get("entries") or [])
    return dict(sorted(counts.items(), key=lambda item: str(item[0])))


def count_events(ledger: dict) -> int:
    return sum(len(entry.get("adjudication_events") or []) for entry in ledger.get("entries") or [])


def first_adjudicated_entry(ledger: dict) -> dict:
    for entry in ledger.get("entries") or []:
        if latest_adjudicated_status(entry):
            return entry
    raise AssertionError("no adjudicated entry found after migration")


def run_reconciler_over_one_adjudicated_entry(entry: dict, workdir: Path) -> dict:
    event_status = latest_adjudicated_status(entry)
    if not event_status:
        raise AssertionError("probe entry has no adjudicated status")

    mini_ledger = {
        "schema": "pdf_oxide.issue34.reconciler_probe.v1",
        "entries": [deepcopy(entry)],
        "status_counts": {event_status: 1},
    }
    mini_ledger["entries"][0]["deterministic_status"] = "unverified"
    project_current_statuses(mini_ledger)

    ledger_path = workdir / "probe_ledger.json"
    report_path = workdir / "probe_report.json"
    evidence_root = workdir / "empty_evidence"
    (evidence_root / "pages").mkdir(parents=True, exist_ok=True)
    write_json(ledger_path, mini_ledger)

    command = [
        sys.executable,
        str(REPO / "scripts/pdf_lab/reconcile_historical_findings_deterministic.py"),
        "--reconciliation",
        str(ledger_path),
        "--evidence-root",
        str(evidence_root),
        "--report",
        str(report_path),
    ]
    result = subprocess.run(command, cwd=REPO, text=True, capture_output=True, check=False)
    rewritten = read_json(ledger_path)
    rewritten_entry = rewritten["entries"][0]
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "report": str(report_path),
        "event_status": event_status,
        "deterministic_status": rewritten_entry.get("deterministic_status"),
        "current_status": rewritten_entry.get("current_status"),
        "current_status_preserved": rewritten_entry.get("current_status") == event_status,
        "deterministic_overwrote_current": rewritten_entry.get("current_status")
        == rewritten_entry.get("deterministic_status"),
        "event_count": len(rewritten_entry.get("adjudication_events") or []),
    }


def main() -> int:
    ledger = read_json(LEDGER)
    before_status_counts = counts_by_status(ledger)
    legacy_adjudications = sum(1 for entry in ledger.get("entries") or [] if entry.get("adjudication"))
    legacy_human_flags = sum(1 for entry in ledger.get("entries") or [] if entry.get("human_flag"))
    event_count_before = count_events(ledger)

    migrated = deepcopy(ledger)
    migration = migrate_legacy_adjudications(
        migrated,
        migrated_at="2026-08-22T00:00:00+00:00",
    )
    after_status_counts = counts_by_status(migrated)
    event_count_after = count_events(migrated)
    lingering_legacy_adjudications = sum(
        1 for entry in migrated.get("entries") or [] if entry.get("adjudication")
    )

    workdir = Path("/tmp/pdf_oxide_issue34_adjudication_events")
    workdir.mkdir(parents=True, exist_ok=True)
    reconciler_probe = run_reconciler_over_one_adjudicated_entry(
        first_adjudicated_entry(migrated),
        workdir,
    )

    problems = []
    if before_status_counts != after_status_counts:
        problems.append("migration changed projected current_status counts")
    if lingering_legacy_adjudications:
        problems.append("migration left legacy adjudication objects")
    if legacy_adjudications and migration["converted_adjudications"] != legacy_adjudications:
        problems.append("legacy adjudication conversion count mismatch")
    if legacy_human_flags and migration["converted_human_flags"] != legacy_human_flags:
        problems.append("legacy human flag conversion count mismatch")
    if event_count_after < event_count_before + migration["converted_adjudications"]:
        problems.append("adjudication event count did not increase as expected")
    if reconciler_probe["returncode"] != 0:
        problems.append("deterministic reconciler probe returned non-zero")
    if not reconciler_probe["current_status_preserved"]:
        problems.append("deterministic reconciler overwrote adjudicated current_status")
    if reconciler_probe["deterministic_status"] != "blocked":
        problems.append("deterministic reconciler did not write deterministic_status in missing-evidence probe")

    report = {
        "passed": not problems,
        "problems": problems,
        "ledger": str(LEDGER),
        "entries": len(ledger.get("entries") or []),
        "legacy_adjudications": legacy_adjudications,
        "legacy_human_flags": legacy_human_flags,
        "event_count_before": event_count_before,
        "event_count_after": event_count_after,
        "migration": migration,
        "before_status_counts": before_status_counts,
        "after_status_counts": after_status_counts,
        "counts_match": before_status_counts == after_status_counts,
        "lingering_legacy_adjudications": lingering_legacy_adjudications,
        "reconciler_probe": reconciler_probe,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
