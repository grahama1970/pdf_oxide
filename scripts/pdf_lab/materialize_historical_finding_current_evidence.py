#!/usr/bin/env python3
"""Materialize current release extraction evidence for historical findings."""
from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[2]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_page(doc: fitz.Document, page: int, out: Path, dpi: int) -> None:
    page_obj = doc.load_page(page - 1)
    pix = page_obj.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    pix.save(out)


def block_type(block: dict[str, Any]) -> str:
    return str(block.get("type") or block.get("blockType") or "")


def render_fast_overlay(page_png: Path, blocks: list[dict[str, Any]], out: Path) -> None:
    img = Image.open(page_png).convert("RGBA")
    draw = ImageDraw.Draw(img)
    colors = {
        "header_footer_noise": (128, 128, 128, 220),
        "running_header": (128, 128, 128, 220),
        "running_footer": (128, 128, 128, 220),
        "paragraph_block": (20, 170, 80, 230),
        "list": (50, 120, 220, 230),
        "section_heading": (20, 90, 220, 230),
        "reference": (160, 70, 210, 230),
        "table": (230, 40, 40, 240),
        "footnote": (245, 170, 25, 240),
    }
    width, height = img.size
    for block in blocks:
        bbox = block.get("bbox") or []
        if len(bbox) != 4:
            continue
        x0, y0, x1, y1 = [float(value) for value in bbox]
        box = [x0 * width, y0 * height, x1 * width, y1 * height]
        color = colors.get(block_type(block), (245, 170, 25, 230))
        draw.rectangle(box, outline=color, width=3)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out)


def evidence_paths(out_root: Path, page: int) -> dict[str, str]:
    page_dir = out_root / "pages" / f"page_{page:04d}"
    return {
        "page_image": str(page_dir / "page.png"),
        "bbox_overlay": str(page_dir / "bbox_overlay_current.png"),
        "release_extraction_blocks": str(page_dir / "release_extraction_blocks.json"),
    }


def extract_page_subprocess(
    pdf: Path,
    ledger: Path,
    page: int,
    page_dir: Path,
    timeout_seconds: int,
    debug_log: Path,
) -> dict[str, Any] | None:
    wrapped = page_dir / "release_extraction_snapshot_wrapped.json"
    if wrapped.exists():
        wrapped.unlink()
    command = [
        "timeout",
        "-k",
        "5s",
        f"{timeout_seconds}s",
        "python",
        str(REPO / "scripts/pdf_lab/snapshot_current_extraction.py"),
        "--pdf",
        str(pdf),
        "--ledger",
        str(ledger),
        "--apply-mode",
        "release",
        "--max-pages",
        str(page),
        "--out",
        str(wrapped),
    ]
    with debug_log.open("a", encoding="utf-8") as debug:
        debug.write(f"\n[page {page}] {' '.join(command)}\n")
        try:
            subprocess.run(
                command,
                cwd=REPO,
                check=True,
                stdout=debug,
                stderr=debug,
            )
        except subprocess.CalledProcessError as exc:
            debug.write(f"[page {page}] extraction_failed: {type(exc).__name__}: {exc}\n")
            return None
    payload = read_json(wrapped)
    pages = payload.get("pages") if isinstance(payload, dict) else None
    if not isinstance(pages, list) or not pages:
        return None
    return pages[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--reconciliation", required=True, type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO
        / "artifacts/pdf_lab/project_agent_hardening/post_commit_nist_candidate_scan_20260601/historical_current_evidence",
    )
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--page-timeout-seconds", type=int, default=45)
    args = parser.parse_args()

    reconciliation = read_json(args.reconciliation)
    pages = [int(page) for page in reconciliation.get("pages") or []]
    if not pages:
        pages = sorted({int(entry["page"]) for entry in reconciliation.get("entries") or []})

    command = " ".join(sys.argv)
    extraction_debug = args.out / "extraction_debug.log"
    manifest_pages: list[dict[str, Any]] = []

    source_doc = fitz.open(args.pdf)
    try:
        for page in pages:
            page_dir = args.out / "pages" / f"page_{page:04d}"
            for stale_name in ("release_extraction_blocks.json", "bbox_overlay_current.png"):
                stale = page_dir / stale_name
                if stale.exists():
                    stale.unlink()
            render_page(source_doc, page, page_dir / "page.png", args.dpi)
            with extraction_debug.open("a", encoding="utf-8") as debug, contextlib.redirect_stdout(debug), contextlib.redirect_stderr(debug):
                extraction = extract_page_subprocess(
                    args.pdf,
                    args.ledger,
                    page,
                    page_dir,
                    args.page_timeout_seconds,
                    extraction_debug,
                )
            if extraction is None:
                manifest_pages.append(
                    {
                        "page": page,
                        "status": "blocked",
                        "reason": "current release extraction timed out or failed",
                        "artifact_dir": str(page_dir),
                        "page_image": str(page_dir / "page.png"),
                        "debug_log": str(extraction_debug),
                    }
                )
                continue
            blocks = extraction.get("blocks") or []
            write_json(page_dir / "release_extraction_blocks.json", extraction)
            render_fast_overlay(
                page_dir / "page.png",
                blocks,
                page_dir / "bbox_overlay_current.png",
            )
            counts = dict(sorted(Counter(block_type(block) for block in blocks).items()))
            manifest_pages.append(
                {
                    "page": page,
                    "status": "materialized",
                    "block_count": len(blocks),
                    "counts": counts,
                    "artifact_dir": str(page_dir),
                    **evidence_paths(args.out, page),
                }
            )
    finally:
        source_doc.close()

    page_evidence = {
        item["page"]: evidence_paths(args.out, item["page"])
        for item in manifest_pages
        if item.get("status") == "materialized"
    }
    blocked_pages = {
        item["page"]: item
        for item in manifest_pages
        if item.get("status") == "blocked"
    }
    for entry in reconciliation.get("entries") or []:
        page = int(entry["page"])
        evidence = page_evidence.get(page)
        entry["current_evidence"] = [evidence] if evidence else []
        if page in blocked_pages:
            entry["current_status"] = "blocked"
            entry["current_evidence"] = [
                {
                    "page_image": blocked_pages[page]["page_image"],
                    "debug_log": blocked_pages[page]["debug_log"],
                    "reason": blocked_pages[page]["reason"],
                }
            ]

    reconciliation["current_evidence_generated_at"] = utc_now()
    reconciliation["current_evidence_commit"] = git_head()
    reconciliation["current_evidence_out"] = str(args.out)
    reconciliation.setdefault("commands", []).append(command)
    write_json(args.reconciliation, reconciliation)

    manifest = {
        "schema": "pdf_oxide.nist_historical_current_evidence.v1",
        "created_at": utc_now(),
        "commit": git_head(),
        "command": command,
        "pdf": str(args.pdf),
        "ledger": str(args.ledger),
        "reconciliation": str(args.reconciliation),
        "page_count": len(manifest_pages),
        "materialized_page_count": sum(1 for item in manifest_pages if item.get("status") == "materialized"),
        "blocked_page_count": sum(1 for item in manifest_pages if item.get("status") == "blocked"),
        "pages": manifest_pages,
        "artifacts": [
            str(args.out / "manifest.json"),
            str(extraction_debug),
        ],
    }
    write_json(args.out / "manifest.json", manifest)
    print(
        json.dumps(
            {
                "out": str(args.out),
                "page_count": len(manifest_pages),
                "materialized_page_count": sum(1 for item in manifest_pages if item.get("status") == "materialized"),
                "blocked_page_count": sum(1 for item in manifest_pages if item.get("status") == "blocked"),
                "pages": [item["page"] for item in manifest_pages],
                "reconciliation": str(args.reconciliation),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
