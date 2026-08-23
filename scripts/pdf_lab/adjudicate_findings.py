"""Agentic second-pass adjudication harness for historical findings.

Two subcommands:

  packet  --page N        Print or write a compact adjudication packet for one
                          page: every unverified finding on that page plus the
                          fresh block evidence relevant to it (type counts and
                          block excerpts matching the finding's block_ids). With
                          --write, each finding gets a copyable one-case packet
                          containing source page evidence, pdf_oxide JSON,
                          decision input, and a read-back receipt.

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
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
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
EVIDENCE_ROOT = CENSUS / "current_evidence"
EVIDENCE = EVIDENCE_ROOT / "pages"
PACKET_ROOT = EVIDENCE_ROOT / "adjudication_packets"
TOKEN_COUNTER_SWEEP = CENSUS / "token_counter_sweep_issue31.json"
TEXT_LOSS_PATTERN = re.compile(
    r"\b(text[- ]loss|truncat(?:e|ed|ion)?|missing|omitted|lost)\b",
    re.IGNORECASE,
)


def _load_ledger() -> dict:
    return json.loads(LEDGER.read_text())


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")


def _repo_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO))
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return safe or "unnamed"


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    ).stdout.strip()


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


def _page_evidence_paths(page: int) -> dict[str, Path]:
    page_dir = EVIDENCE / f"page_{page:04d}"
    return {
        "page_image": page_dir / "page.png",
        "bbox_overlay": page_dir / "bbox_overlay_current.png",
        "release_extraction_blocks": page_dir / "release_extraction_blocks.json",
    }


def _finding_packet_dir(root: Path, page: int, finding_id: str) -> Path:
    return root / f"page_{page:04d}" / _safe_name(finding_id)


def _copy_packet_file(source: Path, packet_dir: Path, target_name: str) -> dict:
    if not source.exists():
        return {
            "source": _repo_path(source),
            "packet_path": None,
            "present": False,
        }
    target = packet_dir / target_name
    shutil.copy2(source, target)
    return {
        "source": _repo_path(source),
        "packet_path": _repo_path(target),
        "present": True,
        "sha256": _sha256(target),
        "bytes": target.stat().st_size,
    }


def _write_zip(packet_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(packet_dir.iterdir()):
            if path == zip_path or path.is_dir():
                continue
            archive.write(path, arcname=path.name)


def _write_one_case_packet(entry: dict, blocks: list[dict], out_root: Path) -> dict:
    page = int(entry["page"])
    finding_id = str(entry["finding_id"])
    packet_dir = _finding_packet_dir(out_root, page, finding_id)
    packet_dir.mkdir(parents=True, exist_ok=True)

    evidence_paths = _page_evidence_paths(page)
    source_evidence = {
        "page_image": _copy_packet_file(evidence_paths["page_image"], packet_dir, "page.png"),
        "bbox_overlay": _copy_packet_file(
            evidence_paths["bbox_overlay"],
            packet_dir,
            "bbox_overlay_current.png",
        ),
        "pdf_oxide_json": _copy_packet_file(
            evidence_paths["release_extraction_blocks"],
            packet_dir,
            "pdf_oxide_release_extraction.json",
        ),
    }
    wanted = set(entry.get("block_ids") or [])
    related = [
        {
            "id": block.get("id"),
            "type": block.get("type"),
            "semantic_role": block.get("semantic_role"),
            "bbox": block.get("bbox"),
            "text": str(block.get("text") or ""),
        }
        for block in blocks
        if not wanted or block.get("id") in wanted
    ]
    type_counts = Counter(block.get("type") for block in blocks)
    readback_path = packet_dir / "readback.json"
    decision_input_path = packet_dir / "decision_input.jsonl"
    decision_input = {
        "page": page,
        "finding_id": finding_id,
        "status": None,
        "evidence": None,
        "packet_readback": _repo_path(readback_path),
    }
    decision_input_path.write_text(json.dumps(decision_input, ensure_ascii=False) + "\n")

    packet = {
        "schema": "pdf_oxide.census_adjudication_packet.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "commit": _git_head(),
        "page": page,
        "finding_id": finding_id,
        "finding": {
            "severity": entry.get("severity"),
            "owner": entry.get("owner"),
            "current_status": entry.get("current_status"),
            "extraction_defect": entry.get("extraction_defect"),
            "expected_type_or_action": entry.get("expected_type_or_action"),
            "visible_evidence": entry.get("visible_evidence"),
            "historical_block_ids": sorted(wanted),
        },
        "source_page_evidence": source_evidence,
        "pdf_oxide": {
            "block_count": len(blocks),
            "type_counts": dict(type_counts),
            "matching_or_page_blocks": related[:80],
        },
        "decision_input": {
            "path": _repo_path(decision_input_path),
            "template": decision_input,
        },
    }
    packet_path = packet_dir / "packet.json"
    _write_json(packet_path, packet)

    missing = [
        name
        for name, artifact in source_evidence.items()
        if not artifact.get("present")
    ]
    readback = {
        "schema": "pdf_oxide.census_adjudication_packet_readback.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "packet": _repo_path(packet_path),
        "page": page,
        "finding_id": finding_id,
        "source_page_evidence_present": not missing,
        "missing_source_page_evidence": missing,
        "pdf_oxide_json_readback": {
            "path": source_evidence["pdf_oxide_json"].get("packet_path"),
            "present": source_evidence["pdf_oxide_json"].get("present"),
            "sha256": source_evidence["pdf_oxide_json"].get("sha256"),
            "block_count": len(blocks),
            "type_counts": dict(type_counts),
        },
        "decision_input_readback": {
            "path": _repo_path(decision_input_path),
            "page": decision_input["page"],
            "finding_id": decision_input["finding_id"],
            "has_packet_readback": bool(decision_input["packet_readback"]),
        },
        "packet_ready": not missing
        and bool(source_evidence["pdf_oxide_json"].get("present"))
        and decision_input["page"] == page
        and decision_input["finding_id"] == finding_id,
    }
    _write_json(readback_path, readback)
    zip_path = packet_dir / "packet.zip"
    _write_zip(packet_dir, zip_path)
    return {
        "page": page,
        "finding_id": finding_id,
        "packet": _repo_path(packet_path),
        "readback": _repo_path(readback_path),
        "zip": _repo_path(zip_path),
        "zip_sha256": _sha256(zip_path),
        "packet_ready": readback["packet_ready"],
    }


def cmd_packet(
    page: int,
    *,
    finding_id: str | None = None,
    write: bool = False,
    out_root: Path = PACKET_ROOT,
) -> int:
    ledger = _load_ledger()
    project_current_statuses(ledger)
    entries = [
        e
        for e in ledger["entries"]
        if e.get("page") == page
        and (
            e.get("current_status") == "unverified"
            or (finding_id is not None and e.get("finding_id") == finding_id)
        )
    ]
    if finding_id is not None:
        entries = [e for e in entries if e.get("finding_id") == finding_id]
    blocks = _page_blocks(page)
    type_counts = Counter(b.get("type") for b in blocks)
    if write:
        packets = [_write_one_case_packet(entry, blocks, out_root) for entry in entries]
        report = {
            "page": page,
            "finding_id": finding_id,
            "packet_count": len(packets),
            "packets": packets,
        }
        print(json.dumps(report, indent=1, ensure_ascii=False))
        return 0 if packets and all(packet["packet_ready"] for packet in packets) else 1

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


def _resolve_packet_readback_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = REPO / path
    if path.is_dir():
        path = path / "readback.json"
    return path


def _packet_problem(decision: dict) -> str | None:
    raw_path = decision.get("packet_readback") or decision.get("packet_path")
    key = (decision.get("page"), decision.get("finding_id"))
    if not raw_path:
        return f"{key}: decision has no packet_readback"
    readback_path = _resolve_packet_readback_path(str(raw_path))
    if not readback_path.exists():
        return f"{key}: packet readback does not exist: {_repo_path(readback_path)}"
    try:
        readback = json.loads(readback_path.read_text())
    except json.JSONDecodeError as exc:
        return f"{key}: packet readback is not valid JSON: {exc}"
    if not readback.get("packet_ready"):
        return f"{key}: packet readback is not ready"
    if readback.get("page") != decision.get("page"):
        return f"{key}: packet page mismatch {readback.get('page')!r}"
    if readback.get("finding_id") != decision.get("finding_id"):
        return f"{key}: packet finding mismatch {readback.get('finding_id')!r}"
    return None


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
        packet_problem = _packet_problem(d)
        if packet_problem:
            problems.append(packet_problem)
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
            "evidence_packet_readback": d.get("packet_readback") or d.get("packet_path"),
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
    p1.add_argument("--finding-id")
    p1.add_argument("--write", action="store_true")
    p1.add_argument("--out", type=Path, default=PACKET_ROOT)
    p2 = sub.add_parser("apply")
    p2.add_argument("--decisions", type=Path, required=True)
    p3 = sub.add_parser("migrate-events")
    p3.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    if args.cmd == "packet":
        return cmd_packet(
            args.page,
            finding_id=args.finding_id,
            write=args.write,
            out_root=args.out,
        )
    if args.cmd == "migrate-events":
        return cmd_migrate_events(args.check_only)
    return cmd_apply(args.decisions)


if __name__ == "__main__":
    sys.exit(main())
