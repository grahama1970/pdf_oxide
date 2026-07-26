#!/usr/bin/env python3
"""Materialize a fresh page28 list-continuation evidence bundle."""
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
DEFAULT_TEST = REPO / "tests/test_nist_page28_list_continuation.py"
HISTORICAL_BUG_REPORT = (
    REPO / "artifacts/pdf_lab/scillm_bug_report_pilot_gpt55_clusters/page_0028_list_continuation/bug_report.json"
)
EXPECTED_PHRASES = [
    "what security and privacy controls are needed",
    "and to adequately manage mission/business risks or risks to individuals",
    "have the selected controls been implemented",
    "what is the required level of assurance",
    "controls, as designed and implemented, are effective",
]
LEAK_PHRASES = [
    "and to adequately manage mission/business risks",
    "controls, as designed and implemented, are effective",
]


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
    return " ".join(str(text or "").split()).lower()


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


def summarize_page28_list_continuation(extraction: dict[str, Any]) -> dict[str, Any]:
    blocks = extraction.get("blocks") or []
    historical_bug_report = load_json_if_present(HISTORICAL_BUG_REPORT)
    list_blocks = [block for block in blocks if block.get("type") == "list"]
    matching_lists = [
        block
        for block in list_blocks
        if "what security and privacy controls are needed" in normalize(block.get("text"))
    ]
    matching_list = matching_lists[0] if matching_lists else None
    matching_list_text = normalize(matching_list.get("text")) if matching_list else ""
    missing_expected_phrases = [
        phrase for phrase in EXPECTED_PHRASES if phrase not in matching_list_text
    ]
    leaked_continuations = [
        {
            "id": block.get("id"),
            "type": block.get("type"),
            "source_type": block.get("source_type"),
            "text": block.get("text"),
            "bbox": block.get("bbox"),
        }
        for block in blocks
        if block.get("type") == "paragraph_block"
        and any(phrase in normalize(block.get("text")) for phrase in LEAK_PHRASES)
    ]
    return {
        "historical_bug_report": str(HISTORICAL_BUG_REPORT),
        "historical_bug_report_count": len(historical_bug_report.get("bug_reports") or []),
        "block_count": len(blocks),
        "list_block_count": len(list_blocks),
        "matching_list_block_count": len(matching_lists),
        "matching_list_block": matching_list,
        "missing_expected_phrase_count": len(missing_expected_phrases),
        "missing_expected_phrases": missing_expected_phrases,
        "paragraph_continuation_leak_count": len(leaked_continuations),
        "paragraph_continuation_leaks": leaked_continuations,
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
    if summary.get("matching_list_block_count") != 1:
        errors.append("matching_list_block_count_not_one")
    if summary.get("missing_expected_phrase_count") != 0:
        errors.append("list_continuation_phrases_missing")
    if summary.get("paragraph_continuation_leak_count") != 0:
        errors.append("paragraph_continuation_leaks_present")
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
    write_json(bbox_metrics, summarize_page28_list_continuation(extraction))
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
        "schema": "pdf_oxide.page28_list_continuation_evidence.v1",
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
