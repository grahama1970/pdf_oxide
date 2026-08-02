#!/usr/bin/env python3
"""Build a historical model-review finding reconciliation ledger.

This script intentionally does not decide whether a historical finding is fixed.
It turns prior model-review artifacts into deterministic reconciliation inputs
that can be checked against fresh current extraction evidence later.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PAGE_RE = re.compile(r"page[_-](\d+)")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def page_from_path(path: Path) -> int | None:
    for part in reversed(path.parts):
        match = PAGE_RE.search(part)
        if match:
            return int(match.group(1))
    return None


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def finding_family(finding: dict[str, Any], page: int) -> str:
    explicit = normalize_text(finding.get("finding_id"))
    if explicit:
        return explicit
    defect = normalize_text(finding.get("extraction_defect"))
    expected = normalize_text(finding.get("expected_type_or_action"))
    seed = (defect or expected or "unnamed").lower()
    seed = re.sub(r"[^a-z0-9]+", "_", seed).strip("_")[:80]
    return f"p{page}_{seed or 'unnamed'}"


def owner_for(finding: dict[str, Any]) -> str:
    owner = normalize_text(finding.get("recommended_owner")).lower()
    if owner in {"pdf_oxide_core", "nist_preset_ledger", "snapshot_tooling"}:
        return owner
    if owner in {"materializer", "table_detector", "extractor"}:
        return "pdf_oxide_core"
    if "preset" in owner or "ledger" in owner:
        return "nist_preset_ledger"
    if "snapshot" in owner or "tool" in owner:
        return "snapshot_tooling"
    return "unknown"


def next_action_for(finding: dict[str, Any]) -> str:
    owner = owner_for(finding)
    if owner in {"pdf_oxide_core", "nist_preset_ledger", "snapshot_tooling"}:
        return "rerun"
    return "visual_review"


def extract_review_payload(path: Path, payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("findings"), list) or isinstance(payload.get("human_needed"), list):
        return payload

    content = (
        payload.get("body", {})
        .get("choices", [{}])[0]
        .get("message", {})
        .get("content")
    )
    if isinstance(content, str):
        try:
            nested = json.loads(content)
        except json.JSONDecodeError:
            return None
        if isinstance(nested, dict):
            return nested
    return None


def iter_artifact_findings(root: Path) -> tuple[list[dict[str, Any]], Counter[str]]:
    records: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    wanted_names = {
        "model_review.json",
        "response.raw.json",
        "summary.json",
        "findings.json",
    }

    for path in sorted(root.rglob("*.json")):
        if path.name not in wanted_names and not path.name.startswith("response.raw.attempt"):
            continue
        payload = read_json(path)
        review = extract_review_payload(path, payload)
        if not isinstance(review, dict):
            continue
        findings = review.get("findings")
        if not isinstance(findings, list) or not findings:
            continue

        source_counts[path.name] += 1
        inferred_page = page_from_path(path)
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            page = finding.get("page") or review.get("page") or inferred_page
            if page is None:
                continue
            try:
                page_int = int(page)
            except (TypeError, ValueError):
                continue
            records.append(
                {
                    "page": page_int,
                    "finding": finding,
                    "source_artifact": str(path),
                }
            )
    return records, source_counts


def build_ledger(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        page = int(record["page"])
        family = finding_family(record["finding"], page)
        grouped[(page, family)].append(record)

    entries: list[dict[str, Any]] = []
    for (page, family), items in sorted(grouped.items()):
        findings = [item["finding"] for item in items]
        severities = Counter(normalize_text(finding.get("severity")) or "unknown" for finding in findings)
        confidences = Counter(normalize_text(finding.get("confidence")) or "unknown" for finding in findings)
        owners = Counter(owner_for(finding) for finding in findings)
        representative = findings[-1]
        source_artifacts = sorted({item["source_artifact"] for item in items})
        entries.append(
            {
                "page": page,
                "finding_id": family,
                "source_artifact": source_artifacts[0],
                "source_artifacts": source_artifacts,
                "source_count": len(source_artifacts),
                "current_status": "unverified",
                "current_evidence": [],
                "owner": owners.most_common(1)[0][0],
                "next_action": next_action_for(representative),
                "severity": severities.most_common(1)[0][0],
                "confidence": confidences.most_common(1)[0][0],
                "block_ids": sorted(
                    {
                        str(block_id)
                        for finding in findings
                        for block_id in (finding.get("block_ids") or [])
                    }
                ),
                "visible_evidence": normalize_text(representative.get("visible_evidence")),
                "extraction_defect": normalize_text(representative.get("extraction_defect")),
                "expected_type_or_action": normalize_text(representative.get("expected_type_or_action")),
                "patch_hint": normalize_text(representative.get("patch_hint")),
            }
        )
    return entries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO / "artifacts/pdf_lab/project_agent_hardening",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO
        / "artifacts/pdf_lab/project_agent_hardening/post_commit_nist_candidate_scan_20260601/historical_finding_reconciliation.json",
    )
    args = parser.parse_args()

    records, source_counts = iter_artifact_findings(args.root)
    entries = build_ledger(records)
    by_status = Counter(entry["current_status"] for entry in entries)
    by_owner = Counter(entry["owner"] for entry in entries)
    pages = sorted({entry["page"] for entry in entries})
    payload = {
        "schema": "pdf_oxide.nist_historical_finding_reconciliation.v1",
        "created_at": utc_now(),
        "commit": git_head(),
        "artifact_root": str(args.root),
        "record_count_before_dedupe": len(records),
        "finding_count": len(entries),
        "page_count": len(pages),
        "pages": pages,
        "status_counts": dict(sorted(by_status.items())),
        "owner_counts": dict(sorted(by_owner.items())),
        "source_file_counts": dict(sorted(source_counts.items())),
        "allowed_statuses": [
            "resolved_by_current_extraction",
            "still_repro",
            "false_positive",
            "human_needed",
            "blocked",
            "unverified",
        ],
        "entries": entries,
    }
    write_json(args.out, payload)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "record_count_before_dedupe": len(records),
                "finding_count": len(entries),
                "page_count": len(pages),
                "pages": pages,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
