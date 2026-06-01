from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _norm_bbox_block(bbox: Any, page_w: float, page_h: float) -> list[float]:
    if not bbox or len(bbox) != 4 or page_w <= 0 or page_h <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    x, y, width, height = [float(v) for v in bbox]
    x0 = x
    x1 = x + width
    y0 = page_h - y - height
    y1 = page_h - y
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    return [
        max(0.0, min(1.0, x0 / page_w)),
        max(0.0, min(1.0, y0 / page_h)),
        max(0.0, min(1.0, x1 / page_w)),
        max(0.0, min(1.0, y1 / page_h)),
    ]


def _table_metrics(table: dict[str, Any]) -> dict[str, Any]:
    data = table.get("data")
    if isinstance(data, list):
        return {
            "row_count": len(data),
            "column_count": max(
                (len(row) for row in data if isinstance(row, (list, tuple))),
                default=0,
            ),
        }

    rows = table.get("rows")
    if isinstance(rows, list):
        row_count = len(rows)
        column_count = max(
            (len(row) for row in rows if isinstance(row, (list, tuple))),
            default=0,
        )
    else:
        row_count = int(table.get("row_count") or table.get("rows_count") or 0)
        column_count = int(table.get("column_count") or table.get("columns_count") or 0)
    return {
        "row_count": row_count,
        "column_count": column_count,
    }


def _norm_bbox_corners(bbox: Any, page_w: float, page_h: float) -> list[float]:
    if not bbox or len(bbox) != 4 or page_w <= 0 or page_h <= 0:
        return [0.0, 0.0, 0.0, 0.0]
    x0, y0, x1, y1 = [float(v) for v in bbox]
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    return [
        max(0.0, min(1.0, x0 / page_w)),
        max(0.0, min(1.0, y0 / page_h)),
        max(0.0, min(1.0, x1 / page_w)),
        max(0.0, min(1.0, y1 / page_h)),
    ]


def _table_bbox(table: dict[str, Any], page_w: float, page_h: float) -> list[float]:
    bbox = table.get("bbox") or table.get("bounding_box") or table.get("rect")
    if isinstance(bbox, dict):
        bbox = [
            bbox.get("x0", bbox.get("left", 0.0)),
            bbox.get("y0", bbox.get("top", 0.0)),
            bbox.get("x1", bbox.get("right", 0.0)),
            bbox.get("y1", bbox.get("bottom", 0.0)),
        ]
    if not bbox or len(bbox) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    if "data" in table or "flavor" in table:
        return _norm_bbox_corners(bbox, page_w, page_h)
    return _norm_bbox_block(bbox, page_w, page_h)


def _clean_table_cell(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def _table_data(table: dict[str, Any]) -> list[list[str]]:
    data = table.get("data")
    if isinstance(data, list):
        rows: list[list[str]] = []
        for row in data:
            if isinstance(row, (list, tuple)):
                rows.append([_clean_table_cell(cell) for cell in row])
        return rows

    rows = table.get("rows")
    if not isinstance(rows, list):
        return []
    out: list[list[str]] = []
    for row in rows:
        cells = row.get("cells") if isinstance(row, dict) else row
        if not isinstance(cells, list):
            continue
        values: list[str] = []
        for cell in cells:
            text = cell.get("text") if isinstance(cell, dict) else cell
            values.append(_clean_table_cell(text))
        out.append(values)
    return out


def _table_text(table: dict[str, Any]) -> str:
    rows = _table_data(table)
    if rows:
        lines = [
            " | ".join(cell for cell in row).strip()
            for row in rows
        ]
        return "\n".join(line for line in lines if line)
    explicit = table.get("text") or table.get("markdown")
    if explicit:
        return str(explicit)
    return ""


def _raw_table_payload(table: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    raw = {**table, **metrics}
    data = _table_data(table)
    if data:
        raw["rows"] = [
            {"cells": [{"text": cell} for cell in row]}
            for row in data
        ]
    return raw


def _extract_tables_for_snapshot(doc: Any, page_index: int) -> list[dict[str, Any]]:
    """Use the shared Camelot-style extractor, falling back to the legacy API."""
    try:
        tables = doc.read_pdf(pages=str(page_index + 1), flavor="auto") or []
        tables = [table for table in tables if isinstance(table, dict)]
        if tables:
            return tables
    except Exception:
        pass

    try:
        return [
            table if isinstance(table, dict) else {"raw_value": table}
            for table in (doc.extract_tables(page_index) or [])
        ]
    except Exception:
        return []


def _extract_page(pdf_path: Path, page_index: int, ledger_path: Path | None, apply_mode: str) -> dict[str, Any]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))
    import pdf_oxide  # noqa: PLC0415
    from pdf_oxide.presets.applier import ApplierConfig, apply_ledger  # noqa: PLC0415

    doc = pdf_oxide.open(str(pdf_path))
    page_w, page_h = doc.page_dimensions(page_index)
    raw_elements: list[dict[str, Any]] = []

    for index, block in enumerate(doc.classify_blocks(page_index) or []):
        if not isinstance(block, dict):
            continue
        raw_elements.append(
            {
                "id": f"actual:p{page_index + 1}:block:{index}",
                "page": page_index + 1,
                "pdf_page_index": page_index,
                "type": "unknown_region",
                "source_type": str(block.get("block_type") or "unknown"),
                "bbox": _norm_bbox_block(block.get("bbox"), page_w, page_h),
                "text": str(block.get("text") or "").strip(),
                "font_size": block.get("font_size"),
                "font_name": block.get("font_name"),
                "is_bold": block.get("is_bold"),
                "raw": block,
            }
        )

    for index, table in enumerate(_extract_tables_for_snapshot(doc, page_index)):
        metrics = _table_metrics(table)
        raw_elements.append(
            {
                "id": f"actual:p{page_index + 1}:table:{index}",
                "page": page_index + 1,
                "pdf_page_index": page_index,
                "type": "table",
                "source_type": "table",
                "bbox": _table_bbox(table, page_w, page_h),
                "text": _table_text(table),
                "raw": _raw_table_payload(table, metrics),
            }
        )

    if ledger_path and ledger_path.exists():
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        blocks = apply_ledger(raw_elements, ledger, ApplierConfig(mode=apply_mode))
        ledger_used = str(ledger_path)
    else:
        blocks = raw_elements
        ledger_used = None

    return {
        "page": page_index + 1,
        "pdf_page_index": page_index,
        "page_dimensions_pts": [page_w, page_h],
        "ledger_path": ledger_used,
        "apply_mode": apply_mode,
        "blocks": blocks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--apply-mode", default="release")
    parser.add_argument("--max-pages", required=True, type=int)
    parser.add_argument("--ledger", type=Path)
    args = parser.parse_args()

    page_index = max(0, args.max_pages - 1)
    payload = {
        "schema": "pdf_lab.current_extraction_snapshot.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pdf_path": str(args.pdf),
        "pages": [_extract_page(args.pdf, page_index, args.ledger, args.apply_mode)],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
