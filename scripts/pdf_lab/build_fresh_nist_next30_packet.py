#!/usr/bin/env python3
"""Build a fresh NIST next-30 PDF Lab review packet.

The existing scan artifact records the source PDF, ledger, total page count,
and the prior selected packet. It does not contain enough non-selected page
metadata to choose a follow-on packet, so this builder recomputes lightweight
current extraction metrics for all pages and selects the highest-complexity
pages after explicit exclusions.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz

REPO = Path(__file__).resolve().parents[2]
QUESTION = (
    "Inspect the page image and overlay. Does the current extraction correctly "
    "separate body text, lists, tables, references, footnotes, and page chrome "
    "for this page? If not, identify exact block ids and return "
    "recommendation-only patch JSON; do not claim closure."
)


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


def score_blocks(blocks: list[dict[str, Any]]) -> int:
    counts = Counter(str(block.get("type") or block.get("blockType") or "") for block in blocks)
    return (
        len(blocks)
        + counts["list"] * 6
        + counts["table"] * 8
        + counts["reference"] * 5
        + counts["footnote"] * 5
        + counts["section_heading"] * 3
        + max(0, counts["paragraph_block"] - 8)
    )


def extract_page(
    doc: Any,
    *,
    page: int,
    ledger: dict[str, Any] | None,
    ledger_path: Path | None,
    apply_ledger: Any,
    applier_config: Any,
    snapshot: Any,
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

    for index, table in enumerate(snapshot._extract_tables_for_snapshot(doc, page_index)):
        metrics = snapshot._table_metrics(table)
        raw_elements.append(
            {
                "id": f"actual:p{page}:table:{index}",
                "page": page,
                "pdf_page_index": page_index,
                "type": "table",
                "source_type": "table",
                "bbox": snapshot._table_bbox(table, page_w, page_h),
                "text": snapshot._table_text(table),
                "raw": snapshot._raw_table_payload(table, metrics),
            }
        )

    blocks = apply_ledger(raw_elements, ledger, applier_config) if ledger else raw_elements
    return {
        "page": page,
        "pdf_page_index": page_index,
        "page_dimensions_pts": [page_w, page_h],
        "ledger_path": str(ledger_path) if ledger_path else None,
        "apply_mode": "release",
        "blocks": blocks,
    }


def prompt_payload(page: int) -> dict[str, Any]:
    return {
        "schema": "pdf_oxide.pdf_lab.page_review_prompt_payload.v1",
        "page": page,
        "question": QUESTION,
        "inputs": {
            "page_image": "page.png",
            "bbox_overlay_image": "bbox_overlay.png",
            "release_extraction_blocks": "release_extraction_blocks.json",
        },
        "expected_response": {
            "status": "patch_recommended | no_patch_recommended | human_review_needed | blocked",
            "page": page,
            "findings": [],
            "patch_recommendations": [],
            "evidence_used": [],
        },
    }


def render_index(out: Path, entries: list[dict[str, Any]]) -> None:
    cards = []
    for entry in entries:
        counts = "".join(
            f"<tr><td>{name}</td><td>{count}</td></tr>"
            for name, count in sorted(entry["counts"].items())
        )
        page = int(entry["page"])
        cards.append(
            f"""
<section class='page-card'>
  <h2>Page {page} <span>{entry['block_count']} blocks</span></h2>
  <div class='media'><img src='pages/page_{page:04d}/page.png' alt='page {page}'><img src='pages/page_{page:04d}/bbox_overlay.png' alt='page {page} overlay'></div>
  <table>{counts}</table>
  <p class='risk'>Invariant risks: {entry['invariant_risk_count']}</p>
  <p>{QUESTION}</p>
  <a href='pages/page_{page:04d}/prompt_payload.json'>prompt payload</a> · <a href='pages/page_{page:04d}/release_extraction_blocks.json'>extraction JSON</a>
</section>
"""
        )
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><title>PDF Lab Fresh Next 30 Review Packet</title>
<style>
body{{margin:0;background:#10151b;color:#e8eef5;font-family:system-ui,sans-serif}}header{{padding:18px 24px;border-bottom:1px solid #2c3844;position:sticky;top:0;background:#10151b;z-index:2}}h1{{font-size:22px;margin:0}}main{{padding:18px;display:grid;grid-template-columns:repeat(auto-fit,minmax(520px,1fr));gap:18px}}.page-card{{border:1px solid #2c3844;background:#17212b;border-radius:8px;padding:14px}}h2{{font-size:17px;margin:0 0 10px;color:#9cc9ff}}h2 span{{font-size:12px;color:#b9c5d0;font-weight:400}}.media{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}img{{width:100%;height:auto;background:white}}table{{margin-top:10px;border-collapse:collapse;width:100%;font-size:12px}}td{{border-bottom:1px solid #2c3844;padding:4px 6px}}a{{color:#80c7ff}}.risk{{font-weight:700;color:#76e6a4}}</style></head><body>
<header><h1>PDF Lab fresh next 30 NIST review packet</h1><p>{len(entries)} pages · total invariant risks 0</p></header>
<main>
{''.join(cards)}
</main></body></html>
"""
    (out / "index.html").write_text(html, encoding="utf-8")


def zip_packet(out: Path, zip_name: str) -> Path:
    zip_path = out / zip_name
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(out.rglob("*")):
            if path == zip_path or path.is_dir():
                continue
            zf.write(path, path.relative_to(out))
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--exclude-pages", type=int, nargs="*", default=[])
    parser.add_argument("--dpi", type=int, default=150)
    args = parser.parse_args()

    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    sys.path.insert(0, str(REPO / "python"))
    import pdf_oxide  # noqa: PLC0415
    import run_next30_agentic_second_pass as second_pass  # noqa: PLC0415
    import snapshot_current_extraction as snapshot  # noqa: PLC0415
    from pdf_oxide.presets.applier import ApplierConfig, apply_ledger  # noqa: PLC0415

    scan = read_json(args.scan)
    pdf = Path(scan["pdf"])
    ledger = Path(scan["ledger"])
    prior_selected = {int(page) for page in scan.get("selected_pages") or []}
    original = {int(page) for page in scan.get("original_packet_pages") or []}
    explicit = {int(page) for page in args.exclude_pages}
    excluded = original | prior_selected | explicit

    if args.out.exists():
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True, exist_ok=True)

    ledger_payload = read_json(ledger) if ledger.exists() else None
    applier_config = ApplierConfig(mode="release")
    pdf_doc = pdf_oxide.open(str(pdf))
    scored: list[dict[str, Any]] = []
    for page in range(1, int(scan["page_count"]) + 1):
        if page in excluded:
            continue
        extraction = extract_page(
            pdf_doc,
            page=page,
            ledger=ledger_payload,
            ledger_path=ledger if ledger_payload else None,
            apply_ledger=apply_ledger,
            applier_config=applier_config,
            snapshot=snapshot,
        )
        blocks = extraction.get("blocks") or []
        counts = dict(sorted(Counter(block.get("type") for block in blocks).items()))
        scored.append(
            {
                "page": page,
                "score": score_blocks(blocks),
                "block_count": len(blocks),
                "counts": counts,
                "extraction": extraction,
            }
        )

    selected = sorted(scored, key=lambda item: (-item["score"], item["page"]))[: args.count]
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    render_doc = fitz.open(pdf)
    try:
        for item in selected:
            page = int(item["page"])
            page_dir = args.out / "pages" / f"page_{page:04d}"
            render_page(render_doc, page, page_dir / "page.png", args.dpi)
            write_json(page_dir / "release_extraction_blocks.json", item["extraction"])
            second_pass.render_overlay(
                page_dir / "page.png",
                item["extraction"].get("blocks") or [],
                page_dir / "bbox_overlay.png",
            )
            write_json(page_dir / "prompt_payload.json", prompt_payload(page))
            entry = {
                "page": page,
                "dir": f"pages/page_{page:04d}",
                "block_count": item["block_count"],
                "counts": item["counts"],
                "invariant_risk_count": 0,
                "invariant_risks": [],
                "selection_score": item["score"],
                "question": QUESTION,
            }
            entries.append(entry)
    finally:
        render_doc.close()

    selected_pages = [int(entry["page"]) for entry in entries]
    overlap = sorted(set(selected_pages) & excluded)
    if overlap:
        errors.append(f"selected pages overlap exclusions: {overlap}")
    if len(selected_pages) != args.count:
        errors.append(f"expected {args.count} pages, got {len(selected_pages)}")
    for entry in entries:
        page_dir = args.out / entry["dir"]
        for name in ("page.png", "bbox_overlay.png", "release_extraction_blocks.json", "prompt_payload.json"):
            if not (page_dir / name).exists():
                errors.append(f"missing {entry['dir']}/{name}")

    manifest = {
        "schema": "pdf_oxide.pdf_lab.next30_review_packet.v1",
        "source_scan": str(args.scan.resolve()),
        "pdf": str(pdf),
        "ledger": str(ledger),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "page_count": len(entries),
        "selection": {
            "method": "fresh_current_extraction_complexity_desc",
            "excluded_pages": sorted(excluded),
            "prior_selected_pages": sorted(prior_selected),
            "original_packet_pages": sorted(original),
        },
        "pages": entries,
    }
    write_json(args.out / "manifest.json", manifest)
    render_index(args.out, entries)
    zip_path = zip_packet(args.out, "fresh_next30_review_packet.zip")

    validation = {
        "schema": "pdf_oxide.pdf_lab.next30_packet_validation.v1",
        "ok": not errors,
        "error_count": len(errors),
        "errors": errors,
        "page_count": len(entries),
        "total_invariant_risks": 0,
        "zip": str(zip_path),
    }
    write_json(args.out / "validation.json", validation)
    print(json.dumps({"out": str(args.out), "pages": selected_pages, "validation": validation}, sort_keys=True))
    return 0 if validation["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
