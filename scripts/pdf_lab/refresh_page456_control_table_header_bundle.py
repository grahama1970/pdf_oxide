#!/usr/bin/env python3
"""Materialize a fresh page456 control-table-header evidence bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

REPO = Path(__file__).resolve().parents[2]
DEFAULT_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
DEFAULT_LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"
DEFAULT_TEST = REPO / "tests/test_nist_page456_control_table_headers.py"

TARGET_IDS = {
    "actual:p456:line:2": "CONTROL",
    "actual:p456:line:3": "NUMBER",
    "actual:p456:line:52": "CONTROL NAME",
    "actual:p456:line:98": "IMPLEMENTED",
    "actual:p456:line:106": "ASSURANCE",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_page(pdf: Path, page_1based: int, out: Path, dpi: int) -> tuple[int, int]:
    with fitz.open(pdf) as doc:
        page = doc.load_page(page_1based - 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.save(out)
        return pix.width, pix.height


def render_overlay(page_png: Path, blocks: list[dict[str, Any]], out: Path) -> None:
    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    try:
        import run_next30_agentic_second_pass as second_pass  # noqa: PLC0415

        second_pass.render_overlay(page_png, blocks, out)
    finally:
        with contextlib_suppress_value_error():
            sys.path.remove(str(REPO / "scripts/pdf_lab"))


class contextlib_suppress_value_error:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> bool:
        return exc_type is ValueError


def extract_page(pdf: Path, ledger: Path) -> dict[str, Any]:
    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    try:
        import snapshot_current_extraction as snapshot  # noqa: PLC0415

        return snapshot._extract_page(pdf, 455, ledger, "release")
    finally:
        with contextlib_suppress_value_error():
            sys.path.remove(str(REPO / "scripts/pdf_lab"))


def run_regression(test_path: Path, out: Path) -> dict[str, Any]:
    command = ["python", "-m", "pytest", "-q", str(test_path)]
    env = os.environ.copy()
    env["PYTHONPATH"] = "python"
    completed = subprocess.run(
        command,
        cwd=REPO,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    out.write_text(completed.stdout, encoding="utf-8")
    return {
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "output": str(out),
    }


def summarize_targets(extraction: dict[str, Any]) -> dict[str, Any]:
    blocks = extraction.get("blocks") or []
    by_id = {str(block.get("id")): block for block in blocks if block.get("id")}
    target_blocks = {}
    for block_id, expected_text in TARGET_IDS.items():
        block = by_id.get(block_id)
        target_blocks[block_id] = {
            "expected_text": expected_text,
            "present": block is not None,
            "type": block.get("type") if block else None,
            "source_type": block.get("source_type") if block else None,
            "text": " ".join(str((block or {}).get("text") or "").split()),
            "bbox": (block or {}).get("bbox"),
        }
    tables = [block for block in blocks if block.get("type") == "table"]
    leaks = [
        block
        for block in target_blocks.values()
        if block["present"] and block["type"] in {"section_header", "section_heading", "paragraph", "prose"}
    ]
    return {
        "target_blocks": target_blocks,
        "target_leak_count": len(leaks),
        "table_count": len(tables),
        "tables": [
            {
                "id": table.get("id"),
                "bbox": table.get("bbox"),
                "text_preview": " ".join(str(table.get("text") or "").split())[:500],
            }
            for table in tables
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--test", type=Path, default=DEFAULT_TEST)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dpi", type=int, default=108)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    page_png = args.out / "page.png"
    extraction_json = args.out / "extraction.pdf_oxide.json"
    overlay_png = args.out / "overlay.png"
    regression_stdout = args.out / "regression.stdout.txt"
    bbox_metrics = args.out / "bbox-metrics.json"

    width, height = render_page(args.pdf, 456, page_png, args.dpi)
    extraction = extract_page(args.pdf, args.ledger)
    write_json(extraction_json, extraction)
    render_overlay(page_png, extraction.get("blocks") or [], overlay_png)
    write_json(bbox_metrics, summarize_targets(extraction))
    regression = run_regression(args.test, regression_stdout)

    artifacts = {
        "page_png": str(page_png),
        "extraction_json": str(extraction_json),
        "overlay_png": str(overlay_png),
        "bbox_metrics": str(bbox_metrics),
        "regression_stdout": str(regression_stdout),
    }
    hashes = {name: sha256(Path(path)) for name, path in artifacts.items()}
    receipt = {
        "schema": "pdf_oxide.page456_control_table_header_evidence.v1",
        "created_at": utc_now(),
        "run_id": args.run_id,
        "page": 456,
        "pdf": str(args.pdf),
        "pdf_sha256": sha256(args.pdf),
        "ledger": str(args.ledger),
        "ledger_sha256": sha256(args.ledger),
        "dpi": args.dpi,
        "page_image_dimensions": [width, height],
        "extraction_command": "snapshot_current_extraction._extract_page(pdf, 455, ledger, 'release')",
        "overlay_command": "run_next30_agentic_second_pass.render_overlay(page_png, blocks, overlay_png)",
        "regression": regression,
        "target_summary": json.loads(bbox_metrics.read_text(encoding="utf-8")),
        "artifacts": artifacts,
        "sha256": hashes,
    }
    write_json(args.out / "receipt.json", receipt)
    (args.out / "SHA256SUMS").write_text(
        "".join(f"{digest}  {Path(artifacts[name]).name}\n" for name, digest in sorted(hashes.items())),
        encoding="utf-8",
    )
    print(json.dumps({"ok": regression["exit_code"] == 0, "receipt": str(args.out / "receipt.json")}, indent=2))
    return 0 if regression["exit_code"] == 0 else regression["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
