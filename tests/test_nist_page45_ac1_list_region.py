"""Regression test for NIST 800-53r5 page 45 AC-1 list region.

Single bug: the AC-1 policy/procedures nested control list is fragmented into
multiple top-level list blocks. Expected: one semantic list_region parent
containing all AC-1 list items; Related Controls remains a separate
non-list block.

This test FAILS on baseline (8 separate list blocks, no list_region) and
PASSES only after the structural_grouping_rule entry is added to the NIST
promotion ledger.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


PDF_PATH = Path("/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/NIST_SP_800-53r5.pdf")
LEDGER_PATH = Path(__file__).resolve().parent.parent / "python" / "pdf_oxide" / "presets" / "document_families" / "nist_sp_800_53r5_promotion_ledger.json"


def _load_snapshot_module():
    path = Path(__file__).resolve().parents[1] / "scripts/pdf_lab/snapshot_current_extraction.py"
    spec = importlib.util.spec_from_file_location("snapshot_current_extraction", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def page45_blocks():
    if not PDF_PATH.exists():
        pytest.fail(f"source PDF not present: {PDF_PATH}")
    if not LEDGER_PATH.exists():
        pytest.fail(f"ledger not present: {LEDGER_PATH}")

    mod = _load_snapshot_module()
    page = mod._extract_page(PDF_PATH, page_index=44, ledger_path=LEDGER_PATH, apply_mode="release")
    return page.get("blocks", [])


def test_ac1_list_items_grouped_into_one_region(page45_blocks):
    """All AC-1 list items (a., 1., (a), (b), 2., b., c., 1., 2.) must live
    under a single list_region synthetic parent."""
    list_regions = [b for b in page45_blocks if b.get("type") == "list_region"]
    assert list_regions, (
        "No list_region found on page 45; expected one region grouping AC-1 list items. "
        f"Block types present: {set(b.get('type') for b in page45_blocks)}"
    )

    # There should be exactly one list_region for the AC-1 policy/procedures list
    assert len(list_regions) == 1, (
        f"Expected exactly one list_region, got {len(list_regions)}: "
        f"{[(r.get('id'), r.get('entry_count')) for r in list_regions]}"
    )

    region = list_regions[0]
    region_text = " ".join(str(region.get("text") or "").split())
    child_ids = {b.get("id") for b in page45_blocks if b.get("parent_id") == region.get("id")}

    # The region must contain the key list markers
    for fragment in [
        "a. Develop",
        "(a) Addresses",
        "(b) Is consistent",
        "2. Procedures",
        "b. Designate",
        "c. Review",
        "1. Policy",
        "2. Procedures",
    ]:
        assert fragment in region_text, (
            f"list_region missing expected fragment {fragment!r}; "
            f"region text was {region_text[:400]!r}"
        )

    # The region must have at least 8 child list items (the baseline had 8 separate blocks)
    assert region.get("entry_count", 0) >= 8, (
        f"list_region should contain at least 8 list items, got {region.get('entry_count')}; "
        f"child_ids: {child_ids}"
    )


def test_related_controls_remains_separate_non_list_block(page45_blocks):
    """Related Controls must NOT be absorbed into the AC-1 list_region."""
    related = [
        b for b in page45_blocks
        if "related controls" in " ".join(str(b.get("text") or "").split()).lower()
    ]
    assert related, "No block containing 'Related Controls' found on page 45"
    assert len(related) == 1, f"Expected exactly one Related Controls block, got {len(related)}"
    rc = related[0]
    assert rc.get("type") != "list_region", (
        f"Related Controls block {rc.get('id')} was incorrectly typed as list_region"
    )
    assert rc.get("parent_id") is None, (
        f"Related Controls block {rc.get('id')} should not have a parent_id"
    )


def test_no_fragments_outside_region(page45_blocks):
    """No standalone list blocks should remain outside the list_region for
    the AC-1 policy/procedures area."""
    list_regions = [b for b in page45_blocks if b.get("type") == "list_region"]
    if not list_regions:
        pytest.skip("list_region not present — baseline behavior")

    region = list_regions[0]
    standalone_lists = [
        b for b in page45_blocks
        if b.get("type") == "list"
        and b.get("parent_id") != region.get("id")
        and "related controls" not in " ".join(str(b.get("text") or "").split()).lower()
    ]
    # It's OK if there are zero standalone lists (all merged) or if the only
    # standalone lists are non-AC-1 items elsewhere on the page.
    ac1_markers = {"a.", "(a)", "(b)", "b.", "c."}
    orphaned_ac1 = [
        b for b in standalone_lists
        if any(str(b.get("text") or "").strip().startswith(m) for m in ac1_markers)
    ]
    assert not orphaned_ac1, (
        f"AC-1 list items orphaned outside list_region: "
        f"{[(b.get('id'), b.get('text')[:80]) for b in orphaned_ac1]}"
    )
