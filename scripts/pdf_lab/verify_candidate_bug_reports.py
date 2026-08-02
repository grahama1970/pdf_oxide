from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return payload.get("blocks") or payload.get("elements") or []


def _source_parts(source_id: str) -> list[str]:
    if not source_id.startswith("actual:"):
        return [source_id]
    return [part for part in source_id.split("+") if part.startswith("actual:")]


def _page_from_report(path: Path, report: dict[str, Any]) -> str:
    page = report.get("page")
    if page is None:
        page = path.parent.name.split("_")[1]
    return f"{int(page):04d}"


def _after_blocks(after_root: Path, page: str) -> list[dict[str, Any]]:
    path = after_root / f"page_{page}_release_after_hardening.json"
    if not path.exists():
        return []
    return _blocks(_load_json(path))


def _ids(blocks: list[dict[str, Any]]) -> set[str]:
    return {str(block.get("id")) for block in blocks}


def _types_by_id(blocks: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(block.get("id")): str(block.get("type") or block.get("blockType") or "")
        for block in blocks
    }


def _resolve_active_report(
    *,
    report_path: Path,
    report_payload: dict[str, Any],
    item: dict[str, Any],
    after_root: Path,
    sidebar_verification: dict[str, Any],
) -> dict[str, Any]:
    source_id = str(item.get("source_id") or "")
    cluster_id = str(report_payload.get("cluster_id") or report_path.parent.name)
    page = _page_from_report(report_path, report_payload)
    blocks = _after_blocks(after_root, page)
    remaining_ids = _ids(blocks)
    types = _types_by_id(blocks)

    if cluster_id == "table_duplicate_cells":
        if source_id == "human:p456:page_chrome_noise:3":
            chrome_ids = {
                str(chrome.get("id"))
                for chrome in sidebar_verification.get("chrome_elements", [])
            }
            ok = bool(sidebar_verification.get("ok")) and "actual:p456:line:9" in chrome_ids
            return {
                "status": "resolved" if ok else "unresolved",
                "reason": "side_margin_chrome_isolated_as_header_footer_noise"
                if ok
                else "side_margin_chrome_not_proven_resolved",
            }

        parts = _source_parts(source_id)
        if parts and all(part not in remaining_ids for part in parts):
            return {
                "status": "resolved",
                "reason": "reported_standalone_duplicate_ids_removed_after_table_suppression",
            }
        return {
            "status": "unresolved",
            "reason": "reported_duplicate_ids_still_materialized",
            "remaining_source_ids": [part for part in parts if part in remaining_ids],
        }

    if report_path.parent.name == "page_0027_page_chrome":
        running_headers = [
            block for block in blocks
            if str(block.get("type") or block.get("blockType")) == "running_header"
        ]
        if len(running_headers) == 1:
            text = str(running_headers[0].get("text") or "")
            if "NIST SP 800-53" in text and "SECURITY AND PRIVACY CONTROLS" in text:
                return {
                    "status": "resolved",
                    "reason": "top_running_header_fragments_merged_to_one_page_chrome_band",
                    "running_header_count_after_hardening": 1,
                }
        return {
            "status": "human_annotation_required",
            "reason": "top_running_header_still_materialized_as_separate_left_and_right_chrome_blocks",
            "running_header_count_after_hardening": len(running_headers),
        }

    if source_id.startswith("actual:"):
        parts = _source_parts(source_id)
        chrome_types = {"header_footer_noise", "running_header", "running_footer", "boilerplate"}
        if all(part not in remaining_ids or types.get(part) in chrome_types for part in parts):
            return {
                "status": "resolved",
                "reason": "source_ids_absent_or_now_page_chrome",
            }
    return {
        "status": "human_annotation_required",
        "reason": "no_deterministic_rule_for_this_active_report",
    }


def audit(
    reports_root: Path,
    after_root: Path,
    page28_comparison: Path,
    table_verification: Path,
    sidebar_verification: Path,
) -> dict[str, Any]:
    table_payload = _load_json(table_verification) if table_verification.exists() else {}
    sidebar_payload = _load_json(sidebar_verification) if sidebar_verification.exists() else {}
    page28_payload = _load_json(page28_comparison) if page28_comparison.exists() else {}

    items: list[dict[str, Any]] = []
    for report_path in sorted(reports_root.glob("*/bug_report.json")):
        payload = _load_json(report_path)
        for item in payload.get("bug_reports", []):
            row = {
                "report": str(report_path),
                "cluster_id": payload.get("cluster_id"),
                "page": payload.get("page"),
                "source_id": item.get("source_id"),
                "bug_present": item.get("bug_present"),
                "confidence": item.get("confidence"),
                "expected_family": item.get("expected_family"),
                "current_family": item.get("current_family"),
            }
            if item.get("bug_present") is False:
                row["status"] = "stale_no_active_bug"
                row["reason"] = "analyzer_report_marked_bug_present_false"
            else:
                row.update(
                    _resolve_active_report(
                        report_path=report_path,
                        report_payload=payload,
                        item=item,
                        after_root=after_root,
                        sidebar_verification=sidebar_payload,
                    )
                )
            items.append(row)

    active = [item for item in items if item["bug_present"] is True]
    resolved = [item for item in active if item["status"] == "resolved"]
    human_queue = [
        item for item in active
        if item["status"] in {"human_annotation_required", "unresolved"}
    ]
    return {
        "schema": "pdf_lab.candidate_bug_report_audit.v1",
        "reports_root": str(reports_root),
        "after_root": str(after_root),
        "evidence": {
            "table_verification": str(table_verification),
            "table_verification_ok": table_payload.get("ok"),
            "sidebar_verification": str(sidebar_verification),
            "sidebar_verification_ok": sidebar_payload.get("ok"),
            "page28_comparison": str(page28_comparison),
            "page28_summary": page28_payload.get("summary"),
        },
        "summary": {
            "total_report_items": len(items),
            "active_bug_items": len(active),
            "stale_or_not_bug_items": len(items) - len(active),
            "deterministically_resolved_active_items": len(resolved),
            "human_annotation_required_items": len(human_queue),
            "resolved_active_ratio": (len(resolved) / len(active)) if active else None,
        },
        "human_annotation_queue": human_queue,
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-root", type=Path, required=True)
    parser.add_argument("--after-root", type=Path, required=True)
    parser.add_argument("--page28-comparison", type=Path, required=True)
    parser.add_argument("--table-verification", type=Path, required=True)
    parser.add_argument("--sidebar-verification", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    payload = audit(
        args.reports_root,
        args.after_root,
        args.page28_comparison,
        args.table_verification,
        args.sidebar_verification,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
