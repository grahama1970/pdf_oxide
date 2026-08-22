#!/usr/bin/env python3
"""Issue #39 live MCID sequence uniqueness check over the NIST census pages."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
DEFAULT_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
DEFAULT_LEDGER = REPO / "artifacts/pdf_lab/census_regen_20260820/seed.json"


def _read_pages(ledger: Path) -> list[int]:
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    pages = payload.get("pages")
    if not isinstance(pages, list):
        pages = sorted({entry["page"] for entry in payload.get("entries") or [] if entry.get("page")})
    return sorted({int(page) for page in pages})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp")
        / f"pdf_oxide_issue39_mcid_sequence_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json",
    )
    args = parser.parse_args()

    pages = _read_pages(args.ledger)
    command = [
        "cargo",
        "run",
        "--quiet",
        "--bin",
        "check_mcid_sequence_uniqueness",
        "--",
        str(args.pdf),
        "--pages",
        ",".join(str(page) for page in pages),
        "--output",
        str(args.output),
    ]
    result = subprocess.run(command, cwd=REPO, text=True, capture_output=True, check=False)
    try:
        scanner_report: dict[str, Any] = json.loads(args.output.read_text(encoding="utf-8"))
    except FileNotFoundError:
        scanner_report = {}

    report = {
        "passed": result.returncode == 0 and bool(scanner_report.get("passed")),
        "problems": [] if result.returncode == 0 and scanner_report.get("passed") else ["mcid sequence scan failed"],
        "command": command,
        "returncode": result.returncode,
        "stdout_bytes": len(result.stdout.encode("utf-8")),
        "stderr_lines": len(result.stderr.splitlines()),
        "stderr_tail": result.stderr.splitlines()[-12:],
        "pdf": str(args.pdf),
        "ledger": str(args.ledger),
        "pages_from_ledger": len(pages),
        "scanner_report": str(args.output),
        "pages_scanned": scanner_report.get("pages_scanned"),
        "span_count": scanner_report.get("span_count"),
        "mcid_span_count": scanner_report.get("mcid_span_count"),
        "duplicate_groups": scanner_report.get("duplicate_groups"),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
