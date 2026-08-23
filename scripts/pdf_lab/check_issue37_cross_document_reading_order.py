#!/usr/bin/env python3
"""Run issue #37 cross-document reading-order regression coverage."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO / "artifacts/pdf_lab/issue37_cross_document_reading_order_report.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PDF_OXIDE_ISSUE37_REPORT"] = str(args.output)

    command = [
        "cargo",
        "test",
        "--test",
        "test_issue37_cross_document_reading_order",
        "--features",
        "python,rendering,office",
        "issue37_cross_document_expected_artifact_report",
        "--",
        "--nocapture",
    ]
    result = subprocess.run(command, cwd=REPO, env=env, text=True, capture_output=True, check=False)

    try:
        report: dict[str, Any] = json.loads(args.output.read_text(encoding="utf-8"))
    except FileNotFoundError:
        report = {
            "schema": "pdf_oxide.issue37.cross_document_reading_order_report.v1",
            "passed": False,
            "problems": [f"missing report artifact: {args.output}"],
        }

    report.setdefault("problems", [])
    if result.returncode != 0:
        report["passed"] = False
        report["problems"].append(f"cargo test exited {result.returncode}")

    summary = {
        "schema": "pdf_oxide.issue37.cross_document_reading_order_check.v1",
        "passed": bool(report.get("passed")),
        "problems": report.get("problems") or [],
        "report": str(args.output),
        "case_count": report.get("case_count"),
        "passed_case_count": report.get("passed_case_count"),
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout.splitlines()[-20:],
        "stderr_tail": result.stderr.splitlines()[-20:],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
