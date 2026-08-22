#!/usr/bin/env python3
"""Issue #36 guard for NIST appendix-grid small-caps table cells."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
DEFAULT_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
DEFAULT_LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"

EXPECTED_ROWS: dict[int, dict[str, list[str]]] = {
    457: {
        "AC-4(17)": ["AC-4(17)", "DOMAIN AUTHENTICATION", "S", ""],
        "AC-4(32)": ["AC-4(32)", "PROCESS REQUIREMENTS FOR INFORMATION TRANSFER", "S", ""],
    },
    464: {
        "CM-2(4)": ["CM-2(4)", "UNAUTHORIZED SOFTWARE", "W: Incorporated into", "CM-7."],
        "CM-4(1)": ["CM-4(1)", "SEPARATE TEST ENVIRONMENTS", "O", "√"],
    },
    486: {
        "SC-12(3)": ["SC-12(3)", "ASYMMETRIC KEYS", "O/S", ""],
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
        if cells and cells[0].replace(" ", "").replace("\n", "") == compact_target:
            return cells
        if cells and compact_target in cells[0].replace(" ", "").replace("\n", ""):
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


def _dump_p457_ac4_17_evidence(pdf: Path, output_dir: Path) -> Path:
    sys.path.insert(0, str(REPO / "python"))
    import pdf_oxide  # noqa: PLC0415

    doc = pdf_oxide.PdfDocument(str(pdf))
    page_index = 456
    chars = []
    for index, ch in enumerate(doc.extract_chars(page_index)):
        x, y, width, height = ch.bbox
        if 85.0 <= x <= 240.0 and 210.0 <= y <= 230.0:
            chars.append(
                {
                    "index": index,
                    "char": ch.char,
                    "bbox": [x, y, width, height],
                    "font_name": ch.font_name,
                    "font_size": ch.font_size,
                    "origin": [ch.origin_x, ch.origin_y],
                }
            )

    spans = []
    for index, span in enumerate(doc.extract_spans(page_index)):
        x, y, width, height = span.bbox
        if 20.0 <= x <= 240.0 and 210.0 <= y <= 230.0:
            spans.append(
                {
                    "index": index,
                    "text": span.text,
                    "bbox": [x, y, width, height],
                    "font_name": span.font_name,
                    "font_size": span.font_size,
                }
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "p457_ac4_17_char_dump.json"
    path.write_text(
        json.dumps(
            {
                "pdf": str(pdf),
                "page": 457,
                "case": "AC-4(17)",
                "chars_text": "".join(item["char"] for item in chars),
                "chars": chars,
                "spans": spans,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp")
        / f"pdf_oxide_issue36_small_caps_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
    )
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

    char_dump = _dump_p457_ac4_17_evidence(args.pdf, args.output_dir)
    report = {
        "passed": not problems,
        "pdf": str(args.pdf),
        "ledger": str(args.ledger),
        "char_dump": str(char_dump),
        "observed": observed,
        "problems": problems,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
