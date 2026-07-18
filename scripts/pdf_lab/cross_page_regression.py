"""Document-wide regression diff between two pdf_oxide extractions.

A page-scoped comparison answers "did the targeted row improve?". It cannot
answer "did this patch quietly change 300 other pages?" -- and a classifier
change almost always has a blast radius wider than the page that surfaced it.

This runs over full-document extraction JSON from a baseline build and a
candidate build and reports:

  * block count delta (added/removed blocks are a structural change, not a
    reclassification, and deserve separate scrutiny)
  * every block whose ``type`` changed, bucketed by transition
  * how many distinct pages were touched
  * the changes that fall on the pinned frozen regression set
  * whether every change matches an expected pattern (anti-overfit signal:
    a memorized fix moves one block, a structural fix moves a whole class)

Both extractions MUST come from wheels built from the same worktree with
identical flags, or the diff measures the source difference rather than the
patch. See GS001 run gs001-bringup-20260718 for a case where a baseline
taken from a dirty checkout made the comparison meaningless.

Usage::

    python3 scripts/pdf_lab/cross_page_regression.py \
        --baseline artifacts/.../full-extraction-baseline.json \
        --candidate artifacts/.../full-extraction-patched.json \
        --frozen-pages 20 468 401 415 483 34 31 32 33 23 \
        --expect-pattern 'page\\s*\\d+' \
        --out artifacts/.../cross_page_regression.json
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path
from typing import Any


def _load_blocks(path: Path) -> dict[tuple[Any, Any], dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {(b.get("page"), b.get("id")): b for b in payload.get("blocks", [])}


def diff_extractions(
    baseline_path: Path,
    candidate_path: Path,
    *,
    frozen_pages: list[int] | None = None,
    expect_pattern: str | None = None,
) -> dict:
    base = _load_blocks(baseline_path)
    cand = _load_blocks(candidate_path)

    only_baseline = sorted(set(base) - set(cand), key=lambda k: (k[0] or 0, str(k[1])))
    only_candidate = sorted(set(cand) - set(base), key=lambda k: (k[0] or 0, str(k[1])))

    transitions: collections.Counter = collections.Counter()
    pages_touched: collections.Counter = collections.Counter()
    changes: list[dict] = []
    for key in set(base) & set(cand):
        before, after = base[key]["type"], cand[key]["type"]
        if before == after:
            continue
        page = key[0]
        transitions[f"{before} -> {after}"] += 1
        pages_touched[page] += 1
        changes.append(
            {
                "page": page,
                "block_id": key[1],
                "from": before,
                "to": after,
                "text": (base[key].get("text") or "")[:120],
                "bbox": base[key].get("bbox"),
            }
        )

    off_pattern: list[dict] = []
    if expect_pattern:
        rx = re.compile(expect_pattern, re.IGNORECASE)
        off_pattern = [c for c in changes if not rx.search(c["text"] or "")]

    frozen_hits = {}
    if frozen_pages:
        frozen_hits = {
            str(page): [c for c in changes if c["page"] == page] for page in frozen_pages
        }

    structural = bool(only_baseline or only_candidate)
    return {
        "schema_version": "pdf_lab.cross_page_regression.v1",
        "baseline": str(baseline_path),
        "candidate": str(candidate_path),
        "baseline_block_count": len(base),
        "candidate_block_count": len(cand),
        "blocks_only_in_baseline": len(only_baseline),
        "blocks_only_in_candidate": len(only_candidate),
        "structural_change": structural,
        "type_changes": len(changes),
        "pages_touched": len(pages_touched),
        "transitions": dict(transitions.most_common()),
        "frozen_page_changes": {k: len(v) for k, v in frozen_hits.items()},
        "frozen_page_detail": frozen_hits,
        "off_pattern_changes": len(off_pattern),
        "off_pattern_sample": off_pattern[:20],
        "changes": changes,
        "note": (
            "A single-block change is a memorization smell; a whole-class change "
            "across many pages is the signature of a structural fix. Neither is "
            "promotion evidence on its own -- read it alongside the page-scoped "
            "regression verdict."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", required=True, type=Path)
    ap.add_argument("--candidate", required=True, type=Path)
    ap.add_argument("--frozen-pages", nargs="*", type=int, default=None)
    ap.add_argument("--expect-pattern", default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    report = diff_extractions(
        args.baseline,
        args.candidate,
        frozen_pages=args.frozen_pages,
        expect_pattern=args.expect_pattern,
    )
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"blocks {report['baseline_block_count']} -> {report['candidate_block_count']}")
    if report["structural_change"]:
        print(
            f"  STRUCTURAL: {report['blocks_only_in_baseline']} removed, "
            f"{report['blocks_only_in_candidate']} added -- not a pure reclassification"
        )
    print(f"type changes: {report['type_changes']} across {report['pages_touched']} pages")
    for transition, count in report["transitions"].items():
        print(f"  {transition}: {count}")
    if report["frozen_page_changes"]:
        print("frozen regression set:")
        for page, count in report["frozen_page_changes"].items():
            print(f"  page {page}: {count}")
    if args.expect_pattern:
        print(f"changes not matching {args.expect_pattern!r}: {report['off_pattern_changes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
