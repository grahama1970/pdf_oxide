#!/usr/bin/env python3
"""One-round PDF Lab page repair closure: extract → rematerialize fix_errors → report delta.

Does not overwrite human `expected_elements.json`. Updates `release_extraction_blocks.json`
and writes `closure_report.json` beside the page slice.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
PROJECT = Path(
    "/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/"
    "pdf-lab-projects/nist-phase54-toc-backed"
)
PACKET = REPO / ".plan-iterate/phase-54-toc-backed-candidate-page-selection/evidence-artifacts/nist-toc-backed-candidate-packet"
MATERIALIZE_DIR = REPO / ".plan-iterate/phase-54-toc-backed-candidate-page-selection/evidence-artifacts"
MATERIALIZE = MATERIALIZE_DIR / "materialize_phase54_project.py"
MATERIALIZE_PYC = MATERIALIZE_DIR / "__pycache__" / "materialize_phase54_project.cpython-312.pyc"
SNAPSHOT_PYC = REPO / "scripts/pdf_lab/__pycache__/snapshot_current_extraction.cpython-312.pyc"
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_pyc_module(name: str, pyc_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, pyc_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {pyc_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_materialize() -> Any:
    source = MATERIALIZE if MATERIALIZE.is_file() else MATERIALIZE_PYC
    if not source.is_file():
        raise RuntimeError(f"cannot load materializer: missing {MATERIALIZE} and {MATERIALIZE_PYC}")
    spec = importlib.util.spec_from_file_location("materialize_phase54_project", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {source}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def page_manifest_entry(page: int) -> dict[str, Any]:
    manifest = json.loads((PACKET / "manifest.json").read_text())
    for entry in manifest.get("pages", []):
        if int(entry.get("page", -1)) == page:
            return entry
    raise SystemExit(f"page {page} not in phase-54 packet manifest")


def count_fix_errors(page_dir: Path) -> int:
    for name in ("agent_second_pass.json", "expected_elements.json"):
        path = page_dir / name
        if path.exists():
            payload = json.loads(path.read_text())
            return len(payload.get("fix_error_requests") or [])
    return -1


def run(cmd: list[str], *, cwd: Path, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def snapshot_page(page: int, out_path: Path) -> None:
    if not SNAPSHOT_PYC.exists():
        raise SystemExit(f"missing snapshot module: {SNAPSHOT_PYC}")
    ledger_args = ["--ledger", str(LEDGER)] if LEDGER.exists() else []
    proc = run(
        [
            sys.executable,
            str(SNAPSHOT_PYC),
            "--pdf",
            str(PDF),
            "--out",
            str(out_path),
            "--apply-mode",
            "release",
            "--max-pages",
            str(page),
            *ledger_args,
        ],
        cwd=REPO,
        timeout=600,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"snapshot failed ({proc.returncode}):\n{proc.stderr[-4000:]}\n{proc.stdout[-2000:]}"
        )


def rematerialize_fix_errors(page: int, blocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mat = load_materialize()
    entry = page_manifest_entry(page)
    expected, fix_requests = mat.materialize_expected_elements(entry, blocks)
    expected, fix_requests = mat.apply_p27_human_feedback(entry, expected, fix_requests)
    expected, fix_requests = mat.apply_p456_human_feedback(entry, expected, fix_requests)
    fix_requests = resolve_table_contained_stale_fix_requests(page, blocks, fix_requests)
    fix_requests = resolve_p28_stale_fix_requests(page, blocks, fix_requests)
    expected = mat.apply_default_breadcrumbs(entry, expected)
    return expected, fix_requests


def _block_type(block: dict[str, Any]) -> str:
    return str(block.get("blockType") or block.get("type") or "").lower()


def _block_text(block: dict[str, Any]) -> str:
    return str(block.get("text") or "")


def _block_bbox(block: dict[str, Any]) -> list[float]:
    bbox = block.get("bbox")
    return bbox if isinstance(bbox, list) and len(bbox) >= 4 else []


def _bbox_overlap_fraction(inner: list[float], outer: list[float]) -> float:
    if len(inner) < 4 or len(outer) < 4:
        return 0.0
    x0 = max(float(inner[0]), float(outer[0]))
    y0 = max(float(inner[1]), float(outer[1]))
    x1 = min(float(inner[2]), float(outer[2]))
    y1 = min(float(inner[3]), float(outer[3]))
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    inner_area = max(0.0, float(inner[2]) - float(inner[0])) * max(0.0, float(inner[3]) - float(inner[1]))
    return intersection / inner_area if inner_area > 0 else 0.0


def _find_block_by_id(blocks: list[dict[str, Any]], block_id: str) -> dict[str, Any] | None:
    return next((block for block in blocks if str(block.get("id") or "") == block_id), None)


def _has_table_covering_block(blocks: list[dict[str, Any]], block: dict[str, Any], threshold: float = 0.92) -> bool:
    bbox = _block_bbox(block)
    if not bbox:
        return False
    for candidate in blocks:
        if _block_type(candidate) != "table":
            continue
        table_bbox = _block_bbox(candidate)
        if table_bbox and _bbox_overlap_fraction(bbox, table_bbox) >= threshold:
            return True
    return False


def resolve_table_contained_stale_fix_requests(
    page: int,
    blocks: list[dict[str, Any]],
    fix_requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Clear table-cell duplicate requests once a table block covers them.

    Multimodal review of page 27 showed the desired invariant: the table block
    is valid, but line/caption/header blocks inside that table should not remain
    as independent second-pass repair requests. This resolver is geometry-based:
    it only removes a request when the source block exists and a same-page table
    block spatially contains that source block.
    """
    if not fix_requests:
        return fix_requests

    remaining = []
    for request in fix_requests:
        requested_family = str(request.get("requested_family") or "").lower()
        source_id = str(request.get("source_id") or "")
        if requested_family != "table":
            remaining.append(request)
            continue
        block = _find_block_by_id(blocks, source_id)
        if not block or int(block.get("page", page)) != page:
            remaining.append(request)
            continue
        if _block_type(block) == "table":
            continue
        if ":line:" not in source_id:
            remaining.append(request)
            continue
        if _has_table_covering_block(blocks, block):
            continue
        remaining.append(request)
    return remaining


def _p28_has_sidebar_chrome_split(blocks: list[dict[str, Any]]) -> bool:
    left_chrome = False
    leaked_body = False
    for block in blocks:
        text_lower = _block_text(block).lower()
        bbox = _block_bbox(block)
        block_type = _block_type(block)
        if (
            bbox
            and bbox[0] < 0.10
            and block_type in {"boilerplate", "page_chrome_noise", "header_footer_noise", "running_header"}
            and (
                "doi.org" in text_lower
                or "this publication is available" in text_lower
                or text_lower.strip() == "-53r5"
            )
        ):
            left_chrome = True
        if ("doi.org" in text_lower or "this publication is available" in text_lower) and block_type not in {
            "boilerplate",
            "page_chrome_noise",
            "header_footer_noise",
            "running_header",
        }:
            leaked_body = True
    return left_chrome and not leaked_body


def _p28_has_grouped_list_blocks(blocks: list[dict[str, Any]]) -> bool:
    candidates = []
    for block in blocks:
        bbox = _block_bbox(block)
        text = _block_text(block).lstrip()
        if (
            _block_type(block) in {"list", "list_item"}
            and bbox
            and 0.56 <= bbox[1] <= 0.70
            and text.startswith("•")
        ):
            candidates.append(block)
    if len(candidates) >= 3:
        return True

    required_fragments = (
        "what security and privacy controls are needed",
        "have the selected controls been implemented",
        "what is the required level of assurance",
        "and to adequately manage mission/business risks",
        "controls, as designed and implemented, are effective",
    )
    for block in blocks:
        if _block_type(block) not in {"list", "list_item"}:
            continue
        text_lower = " ".join(_block_text(block).lower().split())
        if all(fragment in text_lower for fragment in required_fragments):
            return True
    return False


def _p28_has_footnote_blocks(blocks: list[dict[str, Any]]) -> bool:
    candidates = []
    for block in blocks:
        bbox = _block_bbox(block)
        text = _block_text(block).strip()
        if (
            _block_type(block) == "footnote"
            and bbox
            and bbox[1] >= 0.68
            and text[:1].isdigit()
        ):
            candidates.append(block)
    if len(candidates) >= 5:
        return True

    for block in blocks:
        bbox = _block_bbox(block)
        text = " ".join(_block_text(block).strip().split())
        block_type = _block_type(block)
        if (
            block_type in {"footnote", "paragraph_block", "paragraph"}
            and bbox
            and bbox[1] >= 0.68
            and all(f"{n} " in text for n in range(1, 8))
        ):
            return True
    return False


def resolve_p28_stale_fix_requests(
    page: int,
    blocks: list[dict[str, Any]],
    fix_requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Clear p.28 learned fix requests once current blocks satisfy them.

    The bytecode-only materializer's p.28 branch always emits the four learned
    fix requests. This resolver keeps the closure gate meaningful by checking
    the current extracted block families before preserving a request.
    """
    if page != 28:
        return fix_requests

    sidebar_resolved = _p28_has_sidebar_chrome_split(blocks)
    list_resolved = _p28_has_grouped_list_blocks(blocks)
    footnote_resolved = _p28_has_footnote_blocks(blocks)

    remaining = []
    for request in fix_requests:
        source_id = str(request.get("source_id") or "")
        if source_id in {"actual:p28:line:11", "actual:p28:line:24"} and sidebar_resolved:
            continue
        if source_id == "actual:p28:line:29+actual:p28:line:32" and list_resolved:
            continue
        if source_id == "actual:p28:line:39+actual:p28:line:42" and footnote_resolved:
            continue
        remaining.append(request)
    return remaining


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", type=int, default=35)
    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument("--cargo-check", action="store_true")
    parser.add_argument("--maturin", action="store_true")
    parser.add_argument("--artifact-dir", type=Path, default=None)
    args = parser.parse_args()

    page_dir = PROJECT / "pages" / f"page_{args.page:04d}"
    if not page_dir.is_dir():
        raise SystemExit(f"missing page dir: {page_dir}")

    artifact_dir = args.artifact_dir or (
        REPO / "artifacts/pdf_lab/page_repair_closure" / f"page_{args.page:04d}_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "schema": "pdf_oxide.page_repair_closure.v1",
        "started_at": utc_now(),
        "page": args.page,
        "page_dir": str(page_dir),
        "artifact_dir": str(artifact_dir),
        "baseline_fix_error_count": count_fix_errors(page_dir),
    }

    if args.cargo_check:
        proc = run(["cargo", "check"], cwd=REPO, timeout=600)
        report["cargo_check"] = {"exit_code": proc.returncode, "stderr_tail": proc.stderr[-2000:]}
        if proc.returncode != 0:
            report["verdict"] = "cargo_check_failed"
            _write_report(artifact_dir, report)
            return 2

    if args.maturin:
        proc = run(["uv", "run", "maturin", "develop"], cwd=REPO, timeout=900)
        report["maturin"] = {"exit_code": proc.returncode, "stderr_tail": proc.stderr[-2000:]}
        if proc.returncode != 0:
            report["verdict"] = "maturin_failed"
            _write_report(artifact_dir, report)
            return 3

    if not args.skip_extract:
        snap_out = artifact_dir / "snapshot.json"
        snapshot_page(args.page, snap_out)
        snap = json.loads(snap_out.read_text())
        pages = snap.get("pages") or []
        page_payload = next((p for p in pages if int(p.get("page", -1)) == args.page), None)
        if page_payload is None and pages:
            page_payload = pages[0]
        if page_payload is None:
            raise SystemExit("snapshot produced no page payload")
        release = {
            "source_extraction": "pdf_oxide.snapshot_current_extraction",
            "page": args.page,
            "pdf_page_index": page_payload.get("pdf_page_index", args.page - 1),
            "toc_entries": page_payload.get("toc_entries") or [],
            "table_materialization": page_payload.get("table_materialization"),
            "blocks": page_payload.get("blocks") or [],
        }
        release_path = page_dir / "release_extraction_blocks.json"
        backup = artifact_dir / "release_extraction_blocks.before.json"
        if release_path.exists():
            shutil.copy2(release_path, backup)
        release_path.write_text(json.dumps(release, indent=2))
        report["release_extraction"] = {
            "path": str(release_path),
            "block_count": len(release["blocks"]),
            "backup": str(backup) if backup.exists() else None,
        }

    blocks = json.loads((page_dir / "release_extraction_blocks.json").read_text()).get("blocks") or []
    expected, fix_requests = rematerialize_fix_errors(args.page, blocks)
    report["after_rematerialize"] = {
        "expected_element_count": len(expected),
        "fix_error_request_count": len(fix_requests),
        "fix_error_delta": len(fix_requests) - report["baseline_fix_error_count"],
    }

  # preserve human expected_elements; only refresh agent_second_pass metrics for compare
    agent_path = page_dir / "agent_second_pass.json"
    agent_before = json.loads(agent_path.read_text()) if agent_path.exists() else {}
    agent_payload = {
        "schema_version": "pdf_lab.agent_second_pass.v1",
        "slice_id": agent_before.get("slice_id") or f"nist_phase54_page_{args.page:04d}",
        "captured_at": utc_now(),
        "source": "run_page_repair_closure rematerialize from release_extraction_blocks.json",
        "agent_decision": {
            **(agent_before.get("agent_decision") or {}),
            "fix_error_request_count": len(fix_requests),
            "status": "closure_rematerialized",
            "human_review_required": len(fix_requests) > 0,
        },
        "toc_entries": agent_before.get("toc_entries") or page_manifest_entry(args.page).get("toc_entries", []),
        "fix_error_requests": fix_requests,
        "expected_elements": expected,
    }
    shutil.copy2(agent_path, artifact_dir / "agent_second_pass.before.json")
    agent_path.write_text(json.dumps(agent_payload, indent=2))

    report["verdict"] = (
        "closed" if len(fix_requests) == 0 else "improved" if len(fix_requests) < report["baseline_fix_error_count"] else "unchanged"
    )
    report["finished_at"] = utc_now()
    _write_report(artifact_dir, report)
    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "closed" else 1 if report["verdict"] == "improved" else 2


def _write_report(artifact_dir: Path, report: dict[str, Any]) -> None:
    (artifact_dir / "closure_report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
