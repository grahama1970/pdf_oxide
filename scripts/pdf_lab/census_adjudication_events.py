"""Append-only adjudication event helpers for the census ledger."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


ADJUDICATED_STATUSES = {
    "resolved_by_current_extraction",
    "still_broken",
}

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def status_counts(ledger: dict[str, Any]) -> dict[str, int]:
    counts = Counter(entry.get("current_status") for entry in ledger["entries"])
    return dict(sorted(counts.items(), key=lambda item: str(item[0])))


def legacy_adjudication_event(entry: dict[str, Any]) -> dict[str, Any] | None:
    adjudication = entry.get("adjudication")
    if not isinstance(adjudication, dict):
        return None
    status = adjudication.get("status")
    if status not in ADJUDICATED_STATUSES:
        return None
    return {
        "event_type": "adjudication_decision",
        "status": status,
        "adjudicator": adjudication.get("adjudicator"),
        "decided_at": adjudication.get("decided_at"),
        "evidence_root": adjudication.get("evidence_root"),
        "commit": adjudication.get("commit"),
        "evidence": adjudication.get("evidence"),
        "migration_source": "legacy_adjudication",
    }


def human_flag_event(entry: dict[str, Any]) -> dict[str, Any] | None:
    human_flag = entry.get("human_flag")
    if not isinstance(human_flag, dict):
        return None
    return {
        "event_type": "human_flag",
        "status": "flag_for_human",
        "adjudicator": human_flag.get("flagged_by"),
        "decided_at": human_flag.get("flagged_at"),
        "commit": human_flag.get("commit"),
        "evidence": human_flag.get("reason"),
        "migration_source": "legacy_human_flag",
    }


def append_adjudication_event(entry: dict[str, Any], event: dict[str, Any]) -> None:
    events = entry.setdefault("adjudication_events", [])
    if not isinstance(events, list):
        raise ValueError("adjudication_events must be a list")
    events.append(deepcopy(event))


def latest_adjudicated_status(entry: dict[str, Any]) -> str | None:
    events = entry.get("adjudication_events") or []
    if not isinstance(events, list):
        return None
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        status = event.get("status")
        if status in ADJUDICATED_STATUSES:
            return str(status)
    return None


def derived_current_status(entry: dict[str, Any]) -> str:
    adjudicated = latest_adjudicated_status(entry)
    if adjudicated:
        return adjudicated
    deterministic = entry.get("deterministic_status")
    if deterministic:
        return str(deterministic)
    return "unverified"


def project_current_statuses(ledger: dict[str, Any]) -> dict[str, int]:
    for entry in ledger.get("entries") or []:
        entry["current_status"] = derived_current_status(entry)
    counts = status_counts(ledger)
    ledger["status_counts"] = counts
    return counts


def migrate_legacy_adjudications(ledger: dict[str, Any], *, migrated_at: str | None = None) -> dict[str, Any]:
    migrated_at = migrated_at or utc_now()
    converted_adjudications = 0
    converted_human_flags = 0
    deterministic_backfilled = 0

    for entry in ledger.get("entries") or []:
        if entry.get("adjudication_events"):
            entry.pop("adjudication", None)
            entry.pop("human_flag", None)
            continue

        legacy_event = legacy_adjudication_event(entry)
        if legacy_event is not None:
            legacy_event["migrated_at"] = migrated_at
            append_adjudication_event(entry, legacy_event)
            converted_adjudications += 1
            entry.pop("adjudication", None)
        else:
            current_status = entry.get("current_status") or "unverified"
            if not entry.get("deterministic_status"):
                entry["deterministic_status"] = current_status
                deterministic_backfilled += 1

        flag_event = human_flag_event(entry)
        if flag_event is not None:
            flag_event["migrated_at"] = migrated_at
            append_adjudication_event(entry, flag_event)
            converted_human_flags += 1
            entry.pop("human_flag", None)

    counts = project_current_statuses(ledger)
    ledger["adjudication_event_migrated_at"] = migrated_at
    return {
        "converted_adjudications": converted_adjudications,
        "converted_human_flags": converted_human_flags,
        "deterministic_backfilled": deterministic_backfilled,
        "status_counts": counts,
    }
