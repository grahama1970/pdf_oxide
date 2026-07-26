import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_27_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 26, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_27_top_running_header_is_one_page_chrome_text_band():
    page = _extract_page_27_with_ledger()
    blocks = page.get("blocks") or []

    top_headers = [
        block
        for block in blocks
        if block.get("type") == "header_footer_noise"
        and block.get("source_type") == "Header"
        and isinstance(block.get("bbox"), list)
        and block["bbox"][3] <= 0.065
        and "NIST SP 800-53" in str(block.get("text") or "")
    ]

    assert len(top_headers) == 1
    header_text = " ".join(str(top_headers[0].get("text") or "").split())
    assert "NIST SP 800-53, REV. 5" in header_text
    assert "SECURITY AND PRIVACY CONTROLS FOR INFORMATION SYSTEMS AND ORGANIZATIONS" in header_text
    assert top_headers[0]["bbox"] == pytest.approx(
        [0.1470588, 0.0447380, 0.8507378, 0.0582194],
        abs=0.004,
    )


def test_nist_page_27_page_chrome_does_not_emit_running_header_fragments_as_body():
    page = _extract_page_27_with_ledger()
    blocks = page.get("blocks") or []

    bad_fragments = []
    for block in blocks:
        text = " ".join(str(block.get("text") or "").split())
        if text in {
            "NIST SP 800-53, REV. 5",
            "SECURITY AND PRIVACY CONTROLS FOR INFORMATION SYSTEMS AND ORGANIZATIONS",
        }:
            bad_fragments.append(
                {
                    "id": block.get("id"),
                    "type": block.get("type"),
                    "source_type": block.get("source_type"),
                    "text": text,
                    "bbox": block.get("bbox"),
                }
            )

    assert bad_fragments == []
    assert all(block.get("type") != "running_header" for block in blocks)
