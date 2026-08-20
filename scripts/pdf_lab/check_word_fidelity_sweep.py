"""Word-fidelity sweep: pdf_oxide vs PyMuPDF over the census pages.

For every page in the historical-findings census, every alphabetic word of 4+
characters that PyMuPDF extracts must also appear in pdf_oxide's extract_text
(plus lattice table serialization). Normalization covers NFKC ligatures and
the document's own U+019F 'Ɵ'-for-'ti' ToUnicode quirk.

This guards the two content-fidelity root causes fixed on 2026-08-20:

- char dedup deleting the glyph after a narrow space
  ("privacy" -> "rivacy" family);
- intra-line TJ back-jumps serializing out of reading order in the untagged
  MuPDF-style assembler ("executive orders" -> "executive rders ... o").

Exit 0 when no census page is missing any word.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

import pdf_oxide
import pymupdf

REPO = Path(__file__).resolve().parents[2]
PDF = "/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/NIST_SP_800-53r5.pdf"
LEDGER = REPO / "artifacts/pdf_lab/census_regen_20260820/seed.json"


def words(text: str) -> set[str]:
    text = unicodedata.normalize("NFKC", text).replace("Ɵ", "ti")
    return {w.lower() for w in re.findall(r"[A-Za-z]{4,}", text)}


def main() -> int:
    ledger = json.loads(LEDGER.read_text())
    pages = sorted({e["page"] for e in ledger["entries"]})
    doc = pdf_oxide.PdfDocument(PDF)
    oracle = pymupdf.open(PDF)

    detail: dict[int, list[str]] = {}
    for page in pages:
        idx = page - 1
        ours = words(doc.extract_text(idx))
        try:
            tables = doc.read_pdf(pages=str(page), flavor="auto") or []
            ours |= words(
                "\n".join(
                    " | ".join(str(cell) for cell in row)
                    for table in tables
                    for row in (table.get("data") or [])
                )
            )
        except Exception:  # noqa: BLE001 -- table absence must not mask text loss
            pass
        missing = sorted(words(oracle[idx].get_text()) - ours)
        if missing:
            detail[page] = missing

    report = {
        "source_pdf": PDF,
        "pages_swept": len(pages),
        "pages_with_missing_words": len(detail),
        "detail": detail,
        "passed": not detail,
    }
    print(json.dumps(report, indent=2))
    return 0 if not detail else 1


if __name__ == "__main__":
    sys.exit(main())
