"""Deterministic page-45 predicate for issue #2 (NIST 800-53r5, AC-1).

Covers the checklist's stated requirements against the current item-level list
contract established by issue #3 (commit 1fd6c066):

  item 1  The AC-1 control body materializes as item-level list blocks, each
          exactly once, with no absorption of `Related Controls`. The original
          2026-06-04 receipt asserted ONE merged region; that shape is
          superseded by #3, which requires item-level blocks from core. A
          merged-region view is a downstream structural-grouping concern.
  item 2  `Control Enhancements: None.` carries the NIST semantic label.
  item 3  The quick-link text classifies as a section link.
  item 4  `AC-1 POLICY AND PROCEDURES` is exactly one heading block, not split.
  item 6  Chrome text does not leak into non-chrome blocks.

Item 5 (TOC lineage) stays with the snapshot pytest suite, which asserts it
directly (tests/test_pdf_lab_snapshot_current_extraction.py).

Exit 0 when all checks hold, 1 otherwise.
"""

from __future__ import annotations

import contextlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PDF = Path("/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"

EXPECTED_ITEMS = [
    "a. Develop, document, and disseminate",
    "1. [Selection (one or more): Organization-level",
    "(a) Addresses purpose, scope, roles",
    "(b) Is consistent with applicable laws",
    "2. Procedures to facilitate the implementation",
    "b. Designate an [Assignment: organization-defined official]",
    "c. Review and update the current access control:",
]

CHROME = re.compile(
    r"This publication is available|https://doi\.org|NIST SP 800-53|CHAPTER THREE|PAGE 18|________"
)


def main() -> int:
    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    try:
        import snapshot_current_extraction as snapshot

        page = snapshot._extract_page(PDF, 44, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(str(REPO / "scripts/pdf_lab"))

    blocks = page.get("blocks") or []
    texts = [" ".join(str(b.get("text") or "").split()) for b in blocks]
    problems: list[str] = []

    # item 1 — item-level materialization, each exactly once.
    for prefix in EXPECTED_ITEMS:
        count = sum(t.startswith(prefix) for t in texts)
        if count != 1:
            problems.append(f"item1: expected exactly 1 block starting {prefix!r}, got {count}")
    if any("Related Controls" in t and t.startswith(("a.", "b.", "c.")) for t in texts):
        problems.append("item1: a list item absorbed 'Related Controls'")

    # item 2 — labeled enhancements.
    enh = [b for b in blocks if b.get("type") == "labeled_enhancements"]
    if len(enh) != 1 or enh[0].get("semantic_role") != "nist_control_enhancements_none":
        problems.append(f"item2: labeled_enhancements blocks={len(enh)}")

    # item 3 — quick link.
    links = [b for b in blocks if b.get("type") == "section_link"]
    if len(links) != 1 or links[0].get("semantic_role") != "nist_quick_link":
        problems.append(f"item3: section_link blocks={len(links)}")

    # item 4 — one unsplit AC-1 heading.
    headings = [t for t in texts if t == "AC-1 POLICY AND PROCEDURES"]
    if len(headings) != 1:
        problems.append(f"item4: AC-1 heading blocks={len(headings)}")
    if any(t == "AC-1" for t in texts):
        problems.append("item4: bare 'AC-1' fragment present (heading split)")

    # item 6 — chrome containment.
    leaks = [
        b.get("id")
        for b in blocks
        if CHROME.search(str(b.get("text") or "")) and b.get("type") != "header_footer_noise"
    ]
    if leaks:
        problems.append(f"item6: chrome text leaked into {leaks}")

    report = {
        "source_pdf": str(PDF),
        "zero_based_page_index": 44,
        "block_count": len(blocks),
        "problems": problems,
        "passed": not problems,
    }
    print(json.dumps(report, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
