#!/usr/bin/env python3
"""GS001 contract and goal lock tool (fail closed).

Two subcommands:

  lock-contract  Validate the GS001 expected contract and, when complete,
                 emit its canonical sha256 in a contract-lock receipt.
  lock-goal      Validate GOAL.md pins and, when every pin is resolved,
                 emit the goal_hash in a goal-lock receipt.

Both refuse to lock while anything is pending. There is no --force: an
immutable goal that cannot be computed honestly must not exist yet.

Usage:
  python3 scripts/gs001_lock.py lock-contract \
      --contract golden_slices/gs001_nist_page28/expected_elements_v3.draft.json
  python3 scripts/gs001_lock.py lock-goal --goal GOAL.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

PENDING_MARKER = "PENDING"


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def contract_blockers(contract: dict) -> list[str]:
    blockers: list[str] = []
    if contract.get("contract_status") != "locked":
        blockers.append(
            f"contract_status is {contract.get('contract_status')!r}, not 'locked'"
        )
    source_pdf = contract.get("source_pdf") or {}
    sha = str(source_pdf.get("sha256") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", sha):
        blockers.append(f"source_pdf.sha256 unresolved: {sha!r}")
    rows = contract.get("expected_elements") or []
    if not rows:
        blockers.append("expected_elements is empty")
    for row in rows:
        row_id = row.get("id", "<no id>")
        if row.get("pending_recovery"):
            blockers.append(f"{row_id}: pending_recovery")
            continue
        for field in ("page", "type", "text", "bbox"):
            value = row.get(field)
            if value in (None, "", []):
                blockers.append(f"{row_id}: field {field!r} unresolved")
        if isinstance(row.get("bbox_status"), str) and PENDING_MARKER in row["bbox_status"]:
            blockers.append(f"{row_id}: bbox_status pending")
    for waiver in contract.get("waivers") or []:
        if not waiver.get("signed_by"):
            blockers.append(f"waiver {waiver.get('waiver_id', '<no id>')} is unsigned")
    return blockers


def lock_contract(contract_path: Path, receipt_path: Path | None) -> int:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    blockers = contract_blockers(contract)
    if blockers:
        print("CONTRACT NOT LOCKABLE (fail closed):")
        for blocker in blockers:
            print(f"  - {blocker}")
        return 1
    contract_hash = _sha256_bytes(_canonical_json(contract))
    receipt = {
        "schema_version": "pdf_lab.expected_contract_lock.v1",
        "slice_id": contract.get("slice_id"),
        "contract_path": str(contract_path),
        "contract_version": contract.get("contract_version"),
        "expected_row_count": len(contract.get("expected_elements") or []),
        "waiver_count": len(contract.get("waivers") or []),
        "contract_sha256": contract_hash,
    }
    output = receipt_path or contract_path.parent / "expected_contract_lock.json"
    output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return 0


GOAL_PIN_PATTERN = re.compile(r"^\s*-\s*`(?P<key>[a-z0-9_]+)`\s*:\s*(?P<value>.+?)\s*$")


def goal_pins(goal_text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    in_pins = False
    for line in goal_text.splitlines():
        if line.strip().startswith("## Goal-lock pins"):
            in_pins = True
            continue
        if in_pins and line.startswith("## "):
            break
        if in_pins:
            match = GOAL_PIN_PATTERN.match(line)
            if match:
                pins[match.group("key")] = match.group("value").strip("`")
    return pins


def lock_goal(goal_path: Path, receipt_path: Path | None) -> int:
    goal_text = goal_path.read_text(encoding="utf-8")
    pins = goal_pins(goal_text)
    if not pins:
        print("GOAL NOT LOCKABLE: no '## Goal-lock pins' section found")
        return 1
    pending = {key: value for key, value in pins.items() if PENDING_MARKER in value}
    if pending:
        print("GOAL NOT LOCKABLE (fail closed) — unresolved pins:")
        for key, value in pending.items():
            print(f"  - {key}: {value}")
        return 1
    goal_hash = _sha256_bytes(goal_text.encode("utf-8"))
    receipt = {
        "schema_version": "pdf_lab.goal_lock.v1",
        "goal_path": str(goal_path),
        "goal_id": pins.get("goal_id"),
        "goal_version": pins.get("goal_version"),
        "pins": pins,
        "goal_hash": goal_hash,
    }
    output = receipt_path or goal_path.parent / "goal_lock.json"
    output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    contract_cmd = sub.add_parser("lock-contract")
    contract_cmd.add_argument("--contract", type=Path, required=True)
    contract_cmd.add_argument("--receipt", type=Path, default=None)
    goal_cmd = sub.add_parser("lock-goal")
    goal_cmd.add_argument("--goal", type=Path, required=True)
    goal_cmd.add_argument("--receipt", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "lock-contract":
        return lock_contract(args.contract, args.receipt)
    return lock_goal(args.goal, args.receipt)


if __name__ == "__main__":
    sys.exit(main())
