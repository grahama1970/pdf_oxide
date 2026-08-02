#!/usr/bin/env python3
"""Build a deterministic post-commit NIST candidate scan and proof ledger.

This intentionally avoids model judgment. It records current release-mode
extraction metrics for every page, selects the highest-interest pages by local
rules, renders artifacts for selected pages, and writes a proof summary that can
be reconciled or extended by later visual/model review.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

REPO = Path(__file__).resolve().parents[2]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def render_page(doc: fitz.Document, page: int, out: Path, dpi: int) -> None:
    page_obj = doc.load_page(page - 1)
    pix = page_obj.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    pix.save(out)


def block_type(block: dict[str, Any]) -> str:
    return str(block.get("type") or block.get("blockType") or "")


def risk_reasons(blocks: list[dict[str, Any]]) -> list[str]:
    counts = Counter(block_type(block) for block in blocks)
    reasons: list[str] = []
    if counts.get("unknown_region", 0):
        reasons.append("remaining_unknown_region")
    if counts.get("table", 0) and counts.get("unknown_region", 0):
        reasons.append("table_with_unknown_region")
    return reasons


def selection_score(blocks: list[dict[str, Any]], reasons: list[str]) -> int:
    counts = Counter(block_type(block) for block in blocks)
    return (
        len(blocks)
        + counts["table"] * 16
        + counts["list"] * 8
        + counts["reference"] * 6
        + counts["footnote"] * 6
        + counts["section_heading"] * 4
        + counts["unknown_region"] * 40
        + len(reasons) * 100
        + max(0, counts["paragraph_block"] - 8)
    )


def summarize_page(extraction: dict[str, Any]) -> dict[str, Any]:
    page = int(extraction["page"])
    blocks = extraction.get("blocks") or []
    counts = dict(sorted(Counter(block_type(block) for block in blocks).items()))
    reasons = risk_reasons(blocks)
    return {
        "page": page,
        "block_count": len(blocks),
        "counts": counts,
        "candidate_reasons": reasons,
        "risk_count": len(reasons),
        "selection_score": selection_score(blocks, reasons),
    }


def cheap_extract_page(
    doc: Any,
    snapshot: Any,
    apply_ledger: Any,
    applier_config: Any,
    ledger: dict[str, Any],
    ledger_path: Path,
    page: int,
) -> dict[str, Any]:
    page_index = page - 1
    page_w, page_h = doc.page_dimensions(page_index)
    raw_elements: list[dict[str, Any]] = []

    for index, block in enumerate(doc.classify_blocks(page_index) or []):
        if not isinstance(block, dict):
            continue
        raw_elements.append(
            {
                "id": f"actual:p{page}:block:{index}",
                "page": page,
                "pdf_page_index": page_index,
                "type": "unknown_region",
                "source_type": str(block.get("block_type") or "unknown"),
                "bbox": snapshot._norm_bbox_block(block.get("bbox"), page_w, page_h),
                "text": str(block.get("text") or "").strip(),
                "font_size": block.get("font_size"),
                "font_name": block.get("font_name"),
                "is_bold": block.get("is_bold"),
                "raw": block,
            }
        )

    return {
        "page": page,
        "pdf_page_index": page_index,
        "page_dimensions_pts": [page_w, page_h],
        "ledger_path": str(ledger_path),
        "apply_mode": "release",
        "blocks": apply_ledger(raw_elements, ledger, applier_config),
    }


def render_overlay(page_png: Path, blocks: list[dict[str, Any]], out: Path) -> None:
    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    import run_next30_agentic_second_pass as second_pass  # noqa: PLC0415

    second_pass.render_overlay(page_png, blocks, out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    sys.path.insert(0, str(REPO / "python"))
    import pdf_oxide  # noqa: PLC0415
    import snapshot_current_extraction as snapshot  # noqa: PLC0415
    from pdf_oxide.presets.applier import ApplierConfig, apply_ledger  # noqa: PLC0415

    commit = git_head()
    command = " ".join(sys.argv)
    source_doc = fitz.open(args.pdf)
    pdf_doc = pdf_oxide.open(str(args.pdf))
    ledger_payload = json.loads(args.ledger.read_text(encoding="utf-8"))
    applier_config = ApplierConfig(mode="release")
    extraction_debug = args.out / "extraction_debug.log"
    progress_log = args.out / "scan_progress.jsonl"
    try:
        page_count = source_doc.page_count
        pages: list[dict[str, Any]] = []
        for page in range(1, page_count + 1):
            with extraction_debug.open("a", encoding="utf-8") as debug, contextlib.redirect_stdout(debug), contextlib.redirect_stderr(debug):
                extraction = cheap_extract_page(
                    pdf_doc,
                    snapshot,
                    apply_ledger,
                    applier_config,
                    ledger_payload,
                    args.ledger,
                    page,
                )
            page_summary = summarize_page(extraction)
            pages.append(page_summary)
            with progress_log.open("a", encoding="utf-8") as progress:
                progress.write(json.dumps(page_summary, sort_keys=True) + "\n")

        risk_pages = [page for page in pages if page["risk_count"] > 0]
        selected = sorted(pages, key=lambda item: (-item["selection_score"], item["page"]))[: args.count]
        selected_pages = [int(item["page"]) for item in selected]

        for item in selected:
            page = int(item["page"])
            with extraction_debug.open("a", encoding="utf-8") as debug, contextlib.redirect_stdout(debug), contextlib.redirect_stderr(debug):
                extraction = snapshot._extract_page(args.pdf, page - 1, args.ledger, "release")
            item["materialized_counts"] = dict(
                sorted(Counter(block_type(block) for block in extraction.get("blocks") or []).items())
            )
            item["materialized_block_count"] = len(extraction.get("blocks") or [])
            page_dir = args.out / "pages" / f"page_{page:04d}"
            render_page(source_doc, page, page_dir / "page.png", args.dpi)
            write_json(page_dir / "release_extraction_blocks.json", extraction)
            render_overlay(page_dir / "page.png", extraction.get("blocks") or [], page_dir / "bbox_overlay.png")
    finally:
        source_doc.close()

    scan = {
        "schema": "pdf_oxide.nist_candidate_scan.post_commit.v1",
        "created_at": utc_now(),
        "commit": commit,
        "command": command,
        "pdf": str(args.pdf),
        "ledger": str(args.ledger),
        "page_count": page_count,
        "candidate_count": len(risk_pages),
        "candidate_pages": [int(item["page"]) for item in risk_pages],
        "risk_page_count": len(risk_pages),
        "selected_count": len(selected),
        "selected_pages": selected_pages,
        "selected": selected,
        "pages": pages,
    }
    write_json(args.out / "scan.json", scan)

    reconciliation = []
    for item in selected:
        page = int(item["page"])
        status = "accepted_clean" if item["risk_count"] == 0 else "human_needed"
        reconciliation.append(
            {
                "page": page,
                "status": status,
                "candidate_reasons": item["candidate_reasons"],
                "artifact_dir": f"pages/page_{page:04d}",
                "reason": (
                    "current deterministic release extraction has no local invariant risks"
                    if status == "accepted_clean"
                    else "current deterministic scan surfaced invariant risks requiring visual review"
                ),
            }
        )

    proof = {
        "schema": "pdf_oxide.nist_candidate_scan.proof_summary.v1",
        "created_at": utc_now(),
        "commit": commit,
        "pdf": str(args.pdf),
        "ledger": str(args.ledger),
        "page_count": page_count,
        "candidate_count": len(risk_pages),
        "candidate_pages": [int(item["page"]) for item in risk_pages],
        "selected_count": len(selected),
        "selected_pages": selected_pages,
        "reconciled_count": sum(1 for item in reconciliation if item["status"] != "human_needed"),
        "unresolved_count": sum(1 for item in reconciliation if item["status"] == "human_needed"),
        "commands": [command],
        "artifacts": [
            str(args.out / "scan.json"),
            str(args.out / "proof_summary.json"),
            str(extraction_debug),
            str(progress_log),
        ],
        "reconciliation": reconciliation,
    }
    write_json(args.out / "proof_summary.json", proof)
    print(json.dumps({"out": str(args.out), "candidate_count": len(risk_pages), "selected_pages": selected_pages}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
