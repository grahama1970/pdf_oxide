"""Tests for the document-wide regression differ."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pdf_lab.cross_page_regression import diff_extractions


def _write(tmp_path: Path, name: str, blocks: list[dict]) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps({"blocks": blocks}), encoding="utf-8")
    return p


def _blk(page, bid, btype, text="", bbox=None):
    return {"page": page, "id": bid, "type": btype, "text": text, "bbox": bbox or [0, 0, 1, 1]}


def test_detects_type_change_and_buckets_transition(tmp_path):
    base = _write(tmp_path, "b.json", [_blk(1, "a", "Body", "CHAPTER ONE PAGE 5")])
    cand = _write(tmp_path, "c.json", [_blk(1, "a", "Header", "CHAPTER ONE PAGE 5")])
    r = diff_extractions(base, cand)
    assert r["type_changes"] == 1
    assert r["pages_touched"] == 1
    assert r["transitions"] == {"Body -> Header": 1}
    assert r["structural_change"] is False


def test_added_or_removed_blocks_flagged_as_structural(tmp_path):
    base = _write(tmp_path, "b.json", [_blk(1, "a", "Body"), _blk(1, "b", "Body")])
    cand = _write(tmp_path, "c.json", [_blk(1, "a", "Body")])
    r = diff_extractions(base, cand)
    assert r["structural_change"] is True
    assert r["blocks_only_in_baseline"] == 1
    # a merge/split is not a reclassification and must not be silently counted as one
    assert r["type_changes"] == 0


def test_off_pattern_changes_surface_unintended_edits(tmp_path):
    base = _write(tmp_path, "b.json", [
        _blk(1, "a", "Body", "CHAPTER ONE PAGE 5"),
        _blk(2, "b", "Body", "Modern information systems can include"),
    ])
    cand = _write(tmp_path, "c.json", [
        _blk(1, "a", "Header", "CHAPTER ONE PAGE 5"),
        _blk(2, "b", "Header", "Modern information systems can include"),
    ])
    r = diff_extractions(base, cand, expect_pattern=r"page\s*\d+")
    assert r["type_changes"] == 2
    # the body paragraph reclassified as chrome is the one that should stand out
    assert r["off_pattern_changes"] == 1
    assert "Modern information" in r["off_pattern_sample"][0]["text"]


def test_frozen_page_changes_are_reported_per_page(tmp_path):
    base = _write(tmp_path, "b.json", [_blk(20, "a", "Body"), _blk(31, "b", "Title")])
    cand = _write(tmp_path, "c.json", [_blk(20, "a", "Body"), _blk(31, "b", "Header")])
    r = diff_extractions(base, cand, frozen_pages=[20, 31, 99])
    assert r["frozen_page_changes"] == {"20": 0, "31": 1, "99": 0}


def test_identical_extractions_report_no_change(tmp_path):
    blocks = [_blk(1, "a", "Body"), _blk(2, "b", "Title")]
    base = _write(tmp_path, "b.json", blocks)
    cand = _write(tmp_path, "c.json", blocks)
    r = diff_extractions(base, cand)
    assert r["type_changes"] == 0
    assert r["structural_change"] is False
