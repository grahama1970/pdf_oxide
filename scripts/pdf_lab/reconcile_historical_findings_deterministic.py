#!/usr/bin/env python3
"""Conservatively reconcile historical findings against current evidence.

This first pass only updates findings with direct structural evidence in the
current release extraction. It intentionally leaves visual/text-fidelity cases
unverified unless a deterministic current defect is obvious.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

TABLE_TEXT_FIDELITY_TERMS = (
    "wrapped",
    "wrap",
    "truncat",
    "omission",
    "missing",
    "loss",
    "reconstruction",
    "serialized text",
    "cell text",
    "continuation",
    "row boundary",
    "row_boundary",
    "row merge",
    "row_merge",
    "row merges",
    "row_merges",
    "boundary",
    "merge",
    "split row",
    "multiline",
)

TABLE_STRUCTURE_TERMS = (
    "false_negative",
    "not_materialized",
    "not_structured",
    "flattened",
    "missed",
    "linearized",
    "rows_as_text",
    "rows_as_headings",
    "body_not_structured",
    "grid_not_materialized",
)

FOOTER_TERMS = ("footer", "chrome", "page number", "header_footer_noise")
FOOTER_TEXT_RE = re.compile(r"(chapter\s+\w+\s+\d+\s*page|chapter\s+\w+\s+\d+page|appendix\s+[a-z]\s+page\s+\d+|page\s+\d+$)", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def block_type(block: dict[str, Any]) -> str:
    return str(block.get("type") or block.get("blockType") or "")


def table_metrics(block: dict[str, Any]) -> dict[str, int]:
    raw = block.get("raw") if isinstance(block.get("raw"), dict) else {}
    row_count = int(raw.get("row_count") or raw.get("rows_count") or 0)
    column_count = int(raw.get("column_count") or raw.get("columns_count") or 0)
    rows = raw.get("rows")
    if isinstance(rows, list):
        row_count = max(row_count, len(rows))
        column_count = max(
            column_count,
            max(
                (
                    len(row.get("cells") or [])
                    for row in rows
                    if isinstance(row, dict)
                ),
                default=0,
            ),
        )
    return {"row_count": row_count, "column_count": column_count}


def load_page_extractions(evidence_root: Path) -> dict[int, dict[str, Any]]:
    pages: dict[int, dict[str, Any]] = {}
    for path in sorted((evidence_root / "pages").glob("page_*/release_extraction_blocks.json")):
        payload = read_json(path)
        page = int(payload.get("page") or path.parent.name.rsplit("_", 1)[-1])
        pages[page] = payload
    return pages


def entry_text(entry: dict[str, Any]) -> str:
    fields = [
        entry.get("finding_id"),
        entry.get("expected_type_or_action"),
        entry.get("extraction_defect"),
        entry.get("patch_hint"),
    ]
    return " ".join(str(field or "").lower() for field in fields)


def has_table_structure(blocks: list[dict[str, Any]]) -> tuple[bool, list[dict[str, Any]]]:
    evidence = []
    for block in blocks:
        if block_type(block) != "table":
            continue
        metrics = table_metrics(block)
        if metrics["row_count"] >= 2 and metrics["column_count"] >= 2:
            evidence.append(
                {
                    "block_id": block.get("id"),
                    "type": "table",
                    **metrics,
                }
            )
    return bool(evidence), evidence


def structural_table_finding(text: str) -> bool:
    if "table" not in text and "grid" not in text:
        return False
    if any(term in text for term in TABLE_TEXT_FIDELITY_TERMS):
        return False
    return any(term in text for term in TABLE_STRUCTURE_TERMS)


def footer_noise_finding(text: str) -> bool:
    head = text.split(" ", 1)[0]
    return (
        ("footer" in head or "chrome" in head or "side_chrome" in head or "bottom-footer" in head)
        and any(term in text for term in FOOTER_TERMS)
        and (
        "paragraph_block" in text or "body" in text or "suppress" in text or "noise" in text
    )
    )


def reconcile_entry(entry: dict[str, Any], extraction: dict[str, Any] | None) -> dict[str, Any]:
    if extraction is None:
        return {
            "status": "blocked",
            "reason": "current extraction evidence is missing for page",
            "evidence": [],
        }

    text = entry_text(entry)
    blocks = extraction.get("blocks") or []

    if structural_table_finding(text):
        ok, table_evidence = has_table_structure(blocks)
        if ok:
            return {
                "status": "resolved_by_current_extraction",
                "reason": "current release extraction contains structured table block(s) with row/column metrics",
                "evidence": table_evidence,
            }
        return {
            "status": "still_repro",
            "reason": "historical structural table defect remains: no current table block with row/column metrics",
            "evidence": [
                {
                    "current_type_counts": dict(sorted(Counter(block_type(block) for block in blocks).items())),
                }
            ],
        }

    if footer_noise_finding(text):
        leaking_blocks = [
            {
                "block_id": block.get("id"),
                "type": block_type(block),
                "text": str(block.get("text") or "")[:160],
            }
            for block in blocks
            if block_type(block) not in {"header_footer_noise", "running_header", "running_footer"}
            and FOOTER_TEXT_RE.search(str(block.get("text") or ""))
        ]
        if leaking_blocks:
            return {
                "status": "still_repro",
                "reason": "current non-noise block still contains footer/header chrome text",
                "evidence": leaking_blocks,
            }
        return {
            "status": "resolved_by_current_extraction",
            "reason": "current extraction has no footer/header chrome text in non-noise blocks",
            "evidence": [
                {
                    "current_type_counts": dict(sorted(Counter(block_type(block) for block in blocks).items())),
                }
            ],
        }

    if entry.get("finding_id") == "toc_entries_typed_as_references":
        reference_count = sum(1 for block in blocks if block_type(block) == "reference")
        if reference_count:
            return {
                "status": "still_repro",
                "reason": "current TOC page still contains reference-typed entries",
                "evidence": [{"reference_count": reference_count}],
            }

    return {
        "status": "unverified",
        "reason": "requires visual/text-fidelity review beyond this deterministic structural pass",
        "evidence": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reconciliation",
        type=Path,
        default=REPO
        / "artifacts/pdf_lab/project_agent_hardening/post_commit_nist_candidate_scan_20260601/historical_finding_reconciliation.json",
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=REPO
        / "artifacts/pdf_lab/project_agent_hardening/post_commit_nist_candidate_scan_20260601/historical_current_evidence",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO
        / "artifacts/pdf_lab/project_agent_hardening/post_commit_nist_candidate_scan_20260601/deterministic_reconciliation_report.json",
    )
    args = parser.parse_args()

    reconciliation = read_json(args.reconciliation)
    extractions = load_page_extractions(args.evidence_root)
    updates = []
    previous_statuses = Counter(entry["current_status"] for entry in reconciliation.get("entries") or [])

    for entry in reconciliation.get("entries") or []:
        result = reconcile_entry(entry, extractions.get(int(entry["page"])))
        old_status = entry.get("current_status")
        entry["current_status"] = result["status"]
        entry["reconciliation_reason"] = result["reason"]
        entry["reconciliation_evidence"] = result["evidence"]
        if result["status"] != old_status:
            updates.append(
                {
                    "page": entry["page"],
                    "finding_id": entry["finding_id"],
                    "old_status": old_status,
                    "status": result["status"],
                    "reason": result["reason"],
                    "evidence": result["evidence"],
                }
            )

    status_counts = Counter(entry["current_status"] for entry in reconciliation.get("entries") or [])
    reconciliation["deterministic_reconciled_at"] = utc_now()
    reconciliation["deterministic_reconciled_commit"] = git_head()
    reconciliation["status_counts"] = dict(sorted(status_counts.items()))
    reconciliation.setdefault("commands", []).append(" ".join(["python", *map(str, __import__("sys").argv)]))
    write_json(args.reconciliation, reconciliation)

    report = {
        "schema": "pdf_oxide.nist_historical_deterministic_reconciliation_report.v1",
        "created_at": utc_now(),
        "commit": git_head(),
        "reconciliation": str(args.reconciliation),
        "evidence_root": str(args.evidence_root),
        "updated_count": len(updates),
        "previous_status_counts": dict(sorted(previous_statuses.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "updates": updates,
    }
    write_json(args.report, report)
    print(json.dumps({"report": str(args.report), "updated_count": len(updates), "status_counts": dict(sorted(status_counts.items()))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
