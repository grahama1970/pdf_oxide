import contextlib
import re
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_34_with_ledger():
    if not NIST_PDF.exists():
        pytest.skip(f"NIST source PDF not present: {NIST_PDF}")

    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NIST_PDF, 33, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def test_nist_page_34_sidebar_chrome_does_not_contaminate_body_or_headings():
    page = _extract_page_34_with_ledger()
    blocks = page.get("blocks") or []

    sidebar_chrome = [
        block
        for block in blocks
        if block.get("type") == "header_footer_noise"
        and "doi.org/10.6028/NIST.SP.800" in " ".join(str(block.get("text") or "").split())
    ]
    assert len(sidebar_chrome) == 1

    contaminated_body = []
    for block in blocks:
        text = " ".join(str(block.get("text") or "").split())
        if block.get("type") != "header_footer_noise" and (
            "This publication is available" in text
            or "doi.org/10.6028/NIST.SP.800" in text
            or text in {"-", "3r55"}
        ):
            contaminated_body.append(
                {
                    "id": block.get("id"),
                    "type": block.get("type"),
                    "source_type": block.get("source_type"),
                    "text": text,
                    "bbox": block.get("bbox"),
                }
            )
    assert contaminated_body == []

    long_body_headings = []
    for block in blocks:
        text = " ".join(str(block.get("text") or "").split())
        if block.get("type") == "section_heading" and len(text.split()) > 7 and not re.match(
            r"^(CHAPTER|\d+\.\d+)",
            text,
        ):
            long_body_headings.append(
                {
                    "id": block.get("id"),
                    "text": text,
                    "bbox": block.get("bbox"),
                }
            )
    assert long_body_headings == []
