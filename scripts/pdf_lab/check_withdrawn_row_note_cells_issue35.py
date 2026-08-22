#!/usr/bin/env python3
"""Issue #35 guard for withdrawn-row note cells in NIST appendix tables."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
DEFAULT_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
DEFAULT_LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"

EXPECTED_ROWS: dict[int, dict[str, list[str]]] = {
    474: {
        "PE-3(6)": [
            "PE-3(6)",
            "FACILITY PENETRATION TESTING",
            "W: Incorporated into CA-8.",
            "",
        ],
        "PE-5(1)": [
            "PE-5(1)",
            "ACCESS TO OUTPUT BY AUTHORIZED INDIVIDUALS",
            "W: Incorporated into PE-5.",
            "",
        ],
        "PE-7": [
            "PE-7",
            "Visitor Control",
            "W: Incorporated into PE-2 and PE-3.",
            "",
        ],
        "PE-10(1)": [
            "PE-10(1)",
            "ACCIDENTAL AND UNAUTHORIZED ACTIVATION",
            "W: Incorporated into PE-10.",
            "",
        ],
    },
    481: {
        "SA-6": [
            "SA-6",
            "Software Usage Restrictions",
            "W: Incorporated into CM-10 and SI-7.",
            "",
        ],
        "SA-7": [
            "SA-7",
            "User-Installed Software",
            "W: Incorporated into CM-11 and SI-7.",
            "",
        ],
    },
}


def _normalize_cell(text: Any) -> str:
    return " ".join(str(text or "").split())


def _row_cells(row: dict[str, Any]) -> list[str]:
    return [_normalize_cell(cell.get("text")) for cell in row.get("cells") or []]


def _find_row(rows: list[dict[str, Any]], control_id: str) -> list[str] | None:
    compact_target = control_id.replace(" ", "")
    for row in rows:
        cells = _row_cells(row)
        if cells and cells[0].replace(" ", "") == compact_target:
            return cells
    return None


def _extract_page_rows(pdf: Path, ledger: Path, page_number: int) -> list[dict[str, Any]]:
    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    import snapshot_current_extraction as snapshot  # noqa: PLC0415

    payload = snapshot._extract_page(pdf, page_number - 1, ledger, "release")
    tables = [block for block in payload["blocks"] if block.get("type") == "table"]
    if len(tables) != 1:
        raise RuntimeError(f"page {page_number}: expected 1 table, got {len(tables)}")
    return (tables[0].get("raw") or {}).get("rows") or []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    args = parser.parse_args()

    problems: list[dict[str, Any]] = []
    observed: dict[str, Any] = {}
    for page_number, expected_by_control in EXPECTED_ROWS.items():
        rows = _extract_page_rows(args.pdf, args.ledger, page_number)
        page_observed: dict[str, Any] = {}
        for control_id, expected in expected_by_control.items():
            actual = _find_row(rows, control_id)
            page_observed[control_id] = actual
            if actual != expected:
                problems.append(
                    {
                        "page": page_number,
                        "control_id": control_id,
                        "expected": expected,
                        "actual": actual,
                    }
                )
        observed[str(page_number)] = page_observed

    report = {
        "passed": not problems,
        "pdf": str(args.pdf),
        "ledger": str(args.ledger),
        "observed": observed,
        "problems": problems,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
