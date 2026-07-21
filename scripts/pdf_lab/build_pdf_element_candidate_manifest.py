#!/usr/bin/env python3
"""Build a PDF-wide element candidate manifest for second-pass hardening.

The manifest is intentionally model-free. It reuses the existing pdf_oxide
snapshot extraction path, then records page/candidate evidence that later page
DAGs can review and repair.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
PRESET_TYPES = {
    "table",
    "section_heading",
    "list",
    "reference",
    "equation",
    "figure",
    "footnote",
    "toc",
    "side_chrome",
    "appendix",
    "unknown_layout",
    "text",
}


def _is_appendix_heading_text(text: str) -> bool:
    return bool(re.match(r"^\s*(?:APPENDIX|Appendix)\s+[A-Z0-9]\b(?:\s*[:.\-–—]|\s+|$)", text))


class PageCensusTimeout(TimeoutError):
    """Raised when one page exceeds the candidate census page timeout."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def write_census_progress(
    *,
    progress_path: Path | None,
    pdf_path: Path,
    page_count: int,
    limit: int,
    completed_pages: int,
    failed_pages: int,
    current_page_number: int | None,
    status: str,
    last_event: dict[str, Any] | None,
) -> None:
    if progress_path is None:
        return
    payload = {
        "schema": "pdf_lab.second_pass.candidate_census_progress.v1",
        "updated_at": utc_now(),
        "pdf_path": str(pdf_path),
        "page_count": page_count,
        "limit": limit,
        "completed_pages": completed_pages,
        "failed_pages": failed_pages,
        "remaining_pages": max(0, limit - completed_pages - failed_pages),
        "current_page_number": current_page_number,
        "status": status,
        "last_event": last_event,
    }
    write_json(progress_path, payload)


def write_census_event(
    *,
    progress_path: Path | None,
    event: dict[str, Any],
) -> None:
    if progress_path is None:
        return
    event_path = progress_path.with_name("candidate_census_events.jsonl")
    append_jsonl(event_path, event)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    except Exception:
        return None


def _block_type(block: dict[str, Any]) -> str:
    return str(block.get("type") or block.get("blockType") or block.get("source_type") or "").strip()


def _norm_text(text: Any) -> str:
    return " ".join(str(text or "").split())


def _bbox_area(bbox: Any) -> float:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return 0.0
    try:
        x0, y0, x1, y1 = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return 0.0
    if not all(math.isfinite(value) for value in [x0, y0, x1, y1]):
        return 0.0
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _safe_bbox(bbox: Any) -> list[float]:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    try:
        raw_values = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return [0.0, 0.0, 0.0, 0.0]
    if not all(math.isfinite(value) for value in raw_values):
        return [0.0, 0.0, 0.0, 0.0]
    values = [max(0.0, min(1.0, value)) for value in raw_values]
    x0, x1 = sorted((values[0], values[2]))
    y0, y1 = sorted((values[1], values[3]))
    return [x0, y0, x1, y1]


def infer_preset_type(block: dict[str, Any], page_number: int, page_count: int | None = None) -> str:
    """Infer a coarse preset element class from existing extraction fields."""
    raw_type = _block_type(block).lower()
    text = _norm_text(block.get("text"))
    bbox = _safe_bbox(block.get("bbox"))

    if "table" in raw_type:
        return "table"
    if raw_type in {"boilerplate", "header_footer_noise", "page_chrome"}:
        return "side_chrome"
    if raw_type in {"list", "list_item"} or text.startswith(("•", "-", "–", "—")):
        return "list"
    if raw_type in {"reference", "references"} or re.match(r"^\[\s*[A-Za-z0-9][^\]]{0,24}\s*\]", text):
        return "reference"
    if raw_type in {"footnote", "note"} or (bbox[1] >= 0.68 and re.match(r"^\d+\s+\S+", text)):
        return "footnote"
    if raw_type in {"toc", "toc_entry"} or ("..." in text and re.search(r"\.{3,}\s*\d+\b", text)):
        return "toc"
    if raw_type in {"section_header", "section_heading", "header"}:
        if _is_appendix_heading_text(text):
            return "appendix"
        return "section_heading"
    if raw_type in {"figure", "image", "caption"} or re.match(r"^Figure\s+\d+", text, re.I):
        return "figure"
    if raw_type in {"formula", "equation"} or re.search(r"[∑∫√≈≤≥]|(?:^|\s)[A-Za-z]\s*=\s*[^=]", text):
        return "equation"
    if raw_type in {"unknown", "unknown_region", ""}:
        return "unknown_layout"
    if _is_appendix_heading_text(text) or (page_count and page_number > max(1, int(page_count * 0.85))):
        return "appendix"
    return "text"


def detection_reasons(block: dict[str, Any], preset_type: str, page_number: int, page_count: int | None) -> list[str]:
    raw_type = _block_type(block) or "missing_type"
    text = _norm_text(block.get("text"))
    bbox = _safe_bbox(block.get("bbox"))
    reasons = [f"block_type:{raw_type}", f"preset_type:{preset_type}"]
    if preset_type != "text":
        reasons.append("hardening_interest")
    if bbox[0] <= 0.10 or bbox[1] <= 0.08 or bbox[3] >= 0.92:
        reasons.append("boundary_geometry")
    if page_number <= 20:
        reasons.append("frontmatter_or_early_page")
    if page_count and page_number > max(1, int(page_count * 0.85)):
        reasons.append("late_document_page")
    if len(text) > 600:
        reasons.append("long_text_region")
    if _bbox_area(bbox) > 0.20:
        reasons.append("large_region")
    return reasons


def candidate_record(
    *,
    pdf_id: str,
    page_number: int,
    page_count: int | None,
    block_index: int,
    block: dict[str, Any],
) -> dict[str, Any]:
    preset_type = infer_preset_type(block, page_number, page_count)
    block_id = str(block.get("id") or f"page:{page_number}:block:{block_index}")
    text = _norm_text(block.get("text"))
    table_geometry = block.get("table_geometry") if isinstance(block.get("table_geometry"), dict) else {}
    return {
        "candidate_id": f"cand:p{page_number:04d}:{block_index:04d}:{preset_type}",
        "pdf_id": pdf_id,
        "page_number": page_number,
        "page_index": page_number - 1,
        "block_id": block_id,
        "block_index": block_index,
        "preset_type": preset_type,
        "bbox": _safe_bbox(block.get("bbox")),
        "json_pointer": f"/pages/{page_number - 1}/blocks/{block_index}",
        "text_excerpt": text[:500],
        "features": {
            "block_type": _block_type(block),
            "text_length": len(text),
            "bbox_area": round(_bbox_area(_safe_bbox(block.get("bbox"))), 6),
            "has_toc_entries": bool(block.get("tocEntries") or block.get("toc_entries")),
            "source_type": block.get("source_type"),
            "semantic_role": block.get("semantic_role"),
            **(
                {
                    "table_visible_bbox": table_geometry.get("visible_bbox"),
                    "table_full_normalized_bbox": table_geometry.get("full_normalized_bbox"),
                    "table_bbox_clipped_to_page": table_geometry.get("bbox_clipped_to_page"),
                    "table_off_page_extent": table_geometry.get("off_page_extent"),
                }
                if table_geometry
                else {}
            ),
        },
        "confidence": float(block.get("confidence") or (0.65 if preset_type == "unknown_layout" else 0.8)),
        "detection_reason": detection_reasons(block, preset_type, page_number, page_count),
    }


def page_summary(page_number: int, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(candidate["preset_type"] for candidate in candidates)
    risk_count = sum(
        1
        for candidate in candidates
        if candidate["preset_type"] not in {"text"} or "hardening_interest" in candidate["detection_reason"]
    )
    return {
        "page_number": page_number,
        "candidate_count": len(candidates),
        "risk_candidate_count": risk_count,
        "preset_counts": dict(sorted(counts.items())),
    }


def build_manifest_from_pages(
    *,
    pdf_path: Path,
    pages: list[dict[str, Any]],
    page_count: int | None,
    ledger_path: Path | None,
    apply_mode: str,
    command: list[str] | None = None,
    census_failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    pdf_id = f"{pdf_path.stem}:{sha256_file(pdf_path)[:16]}" if pdf_path.exists() else pdf_path.stem
    candidates: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for page in pages:
        page_number = int(page.get("page") or (int(page.get("pdf_page_index", 0)) + 1))
        page_candidates = [
            candidate_record(
                pdf_id=pdf_id,
                page_number=page_number,
                page_count=page_count,
                block_index=index,
                block=block,
            )
            for index, block in enumerate(page.get("blocks") or [])
            if isinstance(block, dict)
        ]
        candidates.extend(page_candidates)
        summaries.append(page_summary(page_number, page_candidates))

    manifest = {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "created_at": utc_now(),
        "git_head": git_head(),
        "command": " ".join(command or sys.argv),
        "pdf_path": str(pdf_path),
        "pdf_id": pdf_id,
        "page_count": page_count,
        "ledger_path": str(ledger_path) if ledger_path else None,
        "apply_mode": apply_mode,
        "preset_types": sorted(PRESET_TYPES),
        "candidate_count": len(candidates),
        "extracted_page_count": len(pages),
        "census_failure_count": len(census_failures or []),
        "census_failures": census_failures or [],
        "page_count_with_candidates": sum(1 for summary in summaries if summary["candidate_count"] > 0),
        "preset_counts": dict(sorted(Counter(candidate["preset_type"] for candidate in candidates).items())),
        "pages": summaries,
        "candidates": candidates,
    }
    return manifest


def _extract_one_page_with_timeout(
    *,
    snapshot: Any,
    pdf_path: Path,
    page_index: int,
    ledger_path: Path | None,
    apply_mode: str,
    page_timeout_s: float | None,
) -> dict[str, Any]:
    if page_timeout_s is not None and page_timeout_s > 0:
        return _extract_one_page_in_subprocess(
            pdf_path=pdf_path,
            page_index=page_index,
            ledger_path=ledger_path,
            apply_mode=apply_mode,
            page_timeout_s=page_timeout_s,
        )

    def _handle_timeout(signum, frame):  # noqa: ARG001 - signal handler contract.
        raise PageCensusTimeout(f"page {page_index + 1} exceeded page_timeout_s={page_timeout_s}")

    timeout_enabled = page_timeout_s is not None and page_timeout_s > 0
    previous_handler = None
    if timeout_enabled:
        previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, _handle_timeout)
        signal.setitimer(signal.ITIMER_REAL, float(page_timeout_s))
    try:
        return snapshot._extract_page(pdf_path, page_index, ledger_path, apply_mode)
    finally:
        if timeout_enabled:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)


def _extract_one_page_in_subprocess(
    *,
    pdf_path: Path,
    page_index: int,
    ledger_path: Path | None,
    apply_mode: str,
    page_timeout_s: float,
) -> dict[str, Any]:
    child_code = f"""
import json
import sys
from pathlib import Path

sys.path.insert(0, {str(REPO / "scripts/pdf_lab")!r})
import snapshot_current_extraction as snapshot

pdf_path = Path(sys.argv[1])
page_index = int(sys.argv[2])
ledger_path = None if sys.argv[3] == "__NONE__" else Path(sys.argv[3])
apply_mode = sys.argv[4]
out_path = Path(sys.argv[5])
page = snapshot._extract_page(pdf_path, page_index, ledger_path, apply_mode)
out_path.write_text(json.dumps(page), encoding="utf-8")
"""
    with tempfile.TemporaryDirectory(prefix="pdf_lab_page_census_") as tmpdir:
        out_path = Path(tmpdir) / "page.json"
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    child_code,
                    str(pdf_path),
                    str(page_index),
                    str(ledger_path) if ledger_path else "__NONE__",
                    apply_mode,
                    str(out_path),
                ],
                cwd=REPO,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=page_timeout_s,
                check=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise PageCensusTimeout(f"page {page_index + 1} exceeded page_timeout_s={page_timeout_s}") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"page {page_index + 1} census subprocess failed with exit code {exc.returncode}") from exc
        return json.loads(out_path.read_text(encoding="utf-8"))


def extract_pages_with_failures(
    pdf_path: Path,
    ledger_path: Path | None,
    apply_mode: str,
    max_pages: int | None,
    *,
    page_timeout_s: float | None = None,
    progress_path: Path | None = None,
    page_numbers: list[int] | None = None,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    import fitz  # noqa: PLC0415
    import snapshot_current_extraction as snapshot  # noqa: PLC0415

    source_doc = fitz.open(pdf_path)
    try:
        page_count = source_doc.page_count
    finally:
        source_doc.close()
    if page_numbers:
        selected_page_numbers = sorted({page for page in page_numbers if 1 <= page <= page_count})
        if max_pages:
            selected_page_numbers = selected_page_numbers[:max_pages]
        page_indices = [page - 1 for page in selected_page_numbers]
    else:
        limit = min(page_count, max_pages) if max_pages else page_count
        page_indices = list(range(limit))
    limit = len(page_indices)
    pages = []
    failures = []
    write_census_progress(
        progress_path=progress_path,
        pdf_path=pdf_path,
        page_count=page_count,
        limit=limit,
        completed_pages=0,
        failed_pages=0,
        current_page_number=None,
        status="started",
        last_event=None,
    )
    for page_index in page_indices:
        page_number = page_index + 1
        started_at = utc_now()
        start_monotonic = time.monotonic()
        start_event = {
            "schema": "pdf_lab.second_pass.candidate_census_event.v1",
            "event": "page_started",
            "created_at": started_at,
            "page_number": page_number,
            "page_index": page_index,
            "page_timeout_s": page_timeout_s,
        }
        write_census_event(progress_path=progress_path, event=start_event)
        write_census_progress(
            progress_path=progress_path,
            pdf_path=pdf_path,
            page_count=page_count,
            limit=limit,
            completed_pages=len(pages),
            failed_pages=len(failures),
            current_page_number=page_number,
            status="page_started",
            last_event=start_event,
        )
        try:
            page = _extract_one_page_with_timeout(
                snapshot=snapshot,
                pdf_path=pdf_path,
                page_index=page_index,
                ledger_path=ledger_path,
                apply_mode=apply_mode,
                page_timeout_s=page_timeout_s,
            )
            pages.append(page)
            finish_event = {
                "schema": "pdf_lab.second_pass.candidate_census_event.v1",
                "event": "page_completed",
                "created_at": utc_now(),
                "page_number": page_number,
                "page_index": page_index,
                "duration_s": round(time.monotonic() - start_monotonic, 3),
                "block_count": len(page.get("blocks") or []) if isinstance(page, dict) else None,
            }
            write_census_event(progress_path=progress_path, event=finish_event)
            write_census_progress(
                progress_path=progress_path,
                pdf_path=pdf_path,
                page_count=page_count,
                limit=limit,
                completed_pages=len(pages),
                failed_pages=len(failures),
                current_page_number=page_number,
                status="page_completed",
                last_event=finish_event,
            )
        except Exception as exc:  # noqa: BLE001 - page-level census failures must be manifest evidence.
            failure = {
                "schema": "pdf_lab.second_pass.page_census_failure.v1",
                "page_number": page_number,
                "page_index": page_index,
                "status": "timeout" if isinstance(exc, PageCensusTimeout) else "substrate_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "page_timeout_s": page_timeout_s,
                "duration_s": round(time.monotonic() - start_monotonic, 3),
            }
            failures.append(failure)
            failure_event = {
                "schema": "pdf_lab.second_pass.candidate_census_event.v1",
                "event": "page_failed",
                "created_at": utc_now(),
                **failure,
            }
            write_census_event(progress_path=progress_path, event=failure_event)
            write_census_progress(
                progress_path=progress_path,
                pdf_path=pdf_path,
                page_count=page_count,
                limit=limit,
                completed_pages=len(pages),
                failed_pages=len(failures),
                current_page_number=page_number,
                status="page_failed",
                last_event=failure_event,
            )
            continue
    finish_event = {
        "schema": "pdf_lab.second_pass.candidate_census_event.v1",
        "event": "completed",
        "created_at": utc_now(),
        "completed_pages": len(pages),
        "failed_pages": len(failures),
        "limit": limit,
    }
    write_census_event(progress_path=progress_path, event=finish_event)
    write_census_progress(
        progress_path=progress_path,
        pdf_path=pdf_path,
        page_count=page_count,
        limit=limit,
        completed_pages=len(pages),
        failed_pages=len(failures),
        current_page_number=None,
        status="completed",
        last_event=finish_event,
    )
    return pages, page_count, failures


def extract_pages(pdf_path: Path, ledger_path: Path | None, apply_mode: str, max_pages: int | None) -> tuple[list[dict[str, Any]], int]:
    pages, page_count, _failures = extract_pages_with_failures(
        pdf_path,
        ledger_path,
        apply_mode,
        max_pages,
        page_timeout_s=None,
    )
    return pages, page_count


def build_failed_manifest(
    *,
    pdf_path: Path,
    ledger_path: Path | None,
    apply_mode: str,
    command: list[str] | None,
    error: BaseException,
) -> dict[str, Any]:
    return {
        "schema": "pdf_lab.second_pass.candidate_manifest.v1",
        "ok": False,
        "errors": [f"candidate census failed: {type(error).__name__}: {error}"],
        "created_at": utc_now(),
        "git_head": git_head(),
        "command": " ".join(command or sys.argv),
        "pdf_path": str(pdf_path),
        "pdf_id": pdf_path.stem,
        "page_count": None,
        "ledger_path": str(ledger_path) if ledger_path else None,
        "apply_mode": apply_mode,
        "preset_types": sorted(PRESET_TYPES),
        "candidate_count": 0,
        "extracted_page_count": 0,
        "census_failure_count": 1,
        "census_failures": [
            {
                "schema": "pdf_lab.second_pass.page_census_failure.v1",
                "page_number": None,
                "page_index": None,
                "status": "substrate_error",
                "error_type": type(error).__name__,
                "error": str(error),
                "page_timeout_s": None,
            }
        ],
        "page_count_with_candidates": 0,
        "preset_counts": {},
        "pages": [],
        "candidates": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--apply-mode", default="release")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--page", type=int, action="append", dest="page_numbers")
    parser.add_argument("--page-timeout-s", type=float)
    parser.add_argument("--debug-log", type=Path)
    parser.add_argument("--progress-path", type=Path)
    args = parser.parse_args()

    try:
        if args.debug_log:
            args.debug_log.parent.mkdir(parents=True, exist_ok=True)
            with args.debug_log.open("w", encoding="utf-8") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                pages, page_count, census_failures = extract_pages_with_failures(
                    args.pdf,
                    args.ledger,
                    args.apply_mode,
                    args.max_pages,
                    page_timeout_s=args.page_timeout_s,
                    progress_path=args.progress_path,
                    page_numbers=args.page_numbers,
                )
        else:
            pages, page_count, census_failures = extract_pages_with_failures(
                args.pdf,
                args.ledger,
                args.apply_mode,
                args.max_pages,
                page_timeout_s=args.page_timeout_s,
                progress_path=args.progress_path,
                page_numbers=args.page_numbers,
            )
    except Exception as exc:  # noqa: BLE001 - census CLI must leave deterministic failure evidence.
        manifest = build_failed_manifest(
            pdf_path=args.pdf,
            ledger_path=args.ledger,
            apply_mode=args.apply_mode,
            command=sys.argv,
            error=exc,
        )
        write_json(args.out, manifest)
        print(json.dumps({"out": str(args.out), "candidate_count": 0, "ok": False}), file=sys.stderr)
        return 2
    manifest = build_manifest_from_pages(
        pdf_path=args.pdf,
        pages=pages,
        page_count=page_count,
        ledger_path=args.ledger,
        apply_mode=args.apply_mode,
        census_failures=census_failures,
    )
    write_json(args.out, manifest)
    print(json.dumps({"out": str(args.out), "candidate_count": manifest["candidate_count"], "page_count": page_count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
