import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_104_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 103, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def _norm(text: object) -> str:
    return " ".join(str(text or "").split())


def test_nist_page_104_control_enhancements_label_is_section_subtitle():
    page = _extract_page_104_with_ledger()
    blocks = page.get("blocks") or []

    matches = [
        block for block in blocks if _norm(block.get("text")) == "Control Enhancements:"
    ]

    assert len(matches) == 1
    label = matches[0]
    assert label.get("id") == "actual:p104:block:18"
    assert label.get("source_type") == "Body"
    assert label.get("type") == "section_subtitle"
    assert label.get("semantic_role") == "nist_control_enhancements_heading"
    assert label.get("bbox") == [
        0.20588235294117646,
        0.6324741093799321,
        0.3617490381976358,
        0.6494018092299952,
    ]
