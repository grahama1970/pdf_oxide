#!/usr/bin/env python3
"""Materialize a fresh page28 page-chrome evidence bundle."""
from __future__ import annotations

import argparse
import contextlib
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
DEFAULT_TEST = REPO / "tests/test_nist_page28_page_chrome.py"
HISTORICAL_BUG_REPORT = (
    REPO / "artifacts/pdf_lab/scillm_bug_report_pilot_gpt55_clusters/page_0028_page_chrome/bug_report.json"
)


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


def normalize(text: Any) -> str:
    return " ".join(str(text or "").split())


def render_page(pdf: Path, page_1based: int, out: Path, dpi: int) -> tuple[int, int]:
    with fitz.open(pdf) as doc:
        page = doc.load_page(page_1based - 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
        out.parent.mkdir(parents=True, exist_ok=True)
        pix.save(out)
        return pix.width, pix.height


def extract_page(pdf: Path, ledger: Path) -> dict[str, Any]:
    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    try:
        import snapshot_current_extraction as snapshot  # noqa: PLC0415

        return snapshot._extract_page(pdf, 27, ledger, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(str(REPO / "scripts/pdf_lab"))


def render_overlay(page_png: Path, blocks: list[dict[str, Any]], out: Path) -> None:
    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    try:
        import run_next30_agentic_second_pass as second_pass  # noqa: PLC0415

        second_pass.render_overlay(page_png, blocks, out)
    finally:
        with contextlib.suppress(ValueError):
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


def load_json_if_present(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def has_doi_sidebar_text(block: dict[str, Any]) -> bool:
    text = normalize(block.get("text"))
    return (
        "doi.org/10.6028/NIST.SP.800" in text
        or "This publication is available free of charge" in text
    )


def summarize_page28_chrome(extraction: dict[str, Any]) -> dict[str, Any]:
    blocks = extraction.get("blocks") or []
    historical_bug_report = load_json_if_present(HISTORICAL_BUG_REPORT)
    doi_blocks = [block for block in blocks if has_doi_sidebar_text(block)]
    doi_chrome_blocks = [
        block
        for block in doi_blocks
        if block.get("type") == "header_footer_noise"
        and block.get("source_type") == "RotatedSideChrome"
    ]
    body_doi_leaks = [
        {
            "id": block.get("id"),
            "type": block.get("type"),
            "source_type": block.get("source_type"),
            "text": normalize(block.get("text")),
            "bbox": block.get("bbox"),
        }
        for block in doi_blocks
        if block.get("type") in {"paragraph_block", "list"}
    ]
    type_counts: dict[str, int] = {}
    for block in blocks:
        block_type = str(block.get("type") or "")
        type_counts[block_type] = type_counts.get(block_type, 0) + 1
    return {
        "historical_bug_report": str(HISTORICAL_BUG_REPORT),
        "historical_bug_report_count": len(historical_bug_report.get("bug_reports") or []),
        "block_count": len(blocks),
        "type_counts": type_counts,
        "doi_block_count": len(doi_blocks),
        "doi_chrome_block_count": len(doi_chrome_blocks),
        "doi_chrome_block": doi_chrome_blocks[0] if doi_chrome_blocks else None,
        "body_doi_leak_count": len(body_doi_leaks),
        "body_doi_leaks": body_doi_leaks,
    }


def validate_receipt(receipt: dict[str, Any], extraction_json: Path) -> dict[str, Any]:
    actual_hash = sha256(extraction_json)
    recorded_hash = str((receipt.get("sha256") or {}).get("extraction_json") or "")
    extraction_path = Path((receipt.get("artifacts") or {}).get("extraction_json") or "")
    run_id = str(receipt.get("run_id") or "")
    summary = receipt.get("target_summary") or {}
    errors: list[str] = []
    if actual_hash != recorded_hash:
        errors.append("extraction_json_hash_mismatch")
    if extraction_path.parent.name != run_id:
        errors.append("receipt_run_id_mismatch")
    if summary.get("doi_block_count") != 1:
        errors.append("doi_block_count_not_one")
    if summary.get("doi_chrome_block_count") != 1:
        errors.append("doi_chrome_block_count_not_one")
    if summary.get("body_doi_leak_count") != 0:
        errors.append("body_doi_leaks_present")
    return {
        "ok": not errors,
        "errors": errors,
        "extraction_json_sha256_actual": actual_hash,
        "extraction_json_sha256_recorded": recorded_hash,
        "run_id": run_id,
        "extraction_parent_run_id": extraction_path.parent.name,
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

    width, height = render_page(args.pdf, 28, page_png, args.dpi)
    extraction = extract_page(args.pdf, args.ledger)
    write_json(extraction_json, extraction)
    render_overlay(page_png, extraction.get("blocks") or [], overlay_png)
    write_json(bbox_metrics, summarize_page28_chrome(extraction))
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
        "schema": "pdf_oxide.page28_page_chrome_evidence.v1",
        "created_at": utc_now(),
        "run_id": args.run_id,
        "page": 28,
        "pdf": str(args.pdf),
        "pdf_sha256": sha256(args.pdf),
        "ledger": str(args.ledger),
        "ledger_sha256": sha256(args.ledger),
        "dpi": args.dpi,
        "page_image_dimensions": [width, height],
        "extraction_command": "snapshot_current_extraction._extract_page(pdf, 27, ledger, 'release')",
        "overlay_command": "run_next30_agentic_second_pass.render_overlay(page_png, blocks, overlay_png)",
        "regression": regression,
        "target_summary": json.loads(bbox_metrics.read_text(encoding="utf-8")),
        "artifacts": artifacts,
        "sha256": hashes,
    }
    receipt["receipt_validation"] = validate_receipt(receipt, extraction_json)
    write_json(args.out / "receipt.json", receipt)
    (args.out / "SHA256SUMS").write_text(
        "".join(f"{digest}  {Path(artifacts[name]).name}\n" for name, digest in sorted(hashes.items())),
        encoding="utf-8",
    )
    ok = regression["exit_code"] == 0 and receipt["receipt_validation"]["ok"]
    print(json.dumps({"ok": ok, "receipt": str(args.out / "receipt.json")}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
