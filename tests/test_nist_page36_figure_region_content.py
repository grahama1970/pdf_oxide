import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_36_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 35, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_36_figure_region_content_is_figure():
    page = _extract_page_36_with_ledger()
    blocks = page.get("blocks") or []
    by_id = {block.get("id"): block for block in blocks}

    expected_figure_ids = {
        "actual:p36:block:10",
        "actual:p36:block:11",
        "actual:p36:block:12",
        "actual:p36:block:13",
        "actual:p36:block:14",
        "actual:p36:block:15",
        "actual:p36:block:16",
        "actual:p36:block:17",
        "actual:p36:block:18",
        "actual:p36:block:19",
        "actual:p36:block:21",
        "actual:p36:block:24",
        "actual:p36:block:25",
        "actual:p36:table:0",
        "actual:p36:table:1",
        "actual:p36:table:2",
    }

    missing = sorted(expected_figure_ids - set(by_id))
    assert missing == []

    offenders = [
        {
            "id": block_id,
            "type": by_id[block_id].get("type"),
            "semantic_role": by_id[block_id].get("semantic_role"),
            "source_type": by_id[block_id].get("source_type"),
            "text": " ".join(str(by_id[block_id].get("text") or "").split())[:120],
        }
        for block_id in sorted(expected_figure_ids)
        if by_id[block_id].get("type") != "figure"
        or by_id[block_id].get("semantic_role") != "figure_content"
    ]
    assert offenders == []

    caption = by_id["actual:p36:block:8"]
    assert caption.get("type") == "caption"
    assert caption.get("semantic_role") == "figure_caption"
