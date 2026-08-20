"""Deterministic guard for the char-dedup glyph-loss defect (p19 family).

deduplicate_overlapping_chars() used to drop any glyph within 2pt of the
previous glyph's x-origin on the same line, whatever the character. Spaces are
~2pt wide, so the first glyph after a narrow space sat at the knife edge and
one real character per affected line was silently deleted before table cell
assembly: "privacy" -> "rivacy", "mapped" -> "apped", "establishes" ->
"stablishes", "federal" -> "ederal" on NIST 800-53r5 printed page 19.

Checks, all live against the real PDF:

1. Glyph parity: extract_chars and extract_spans agree on the count of 'p'
   glyphs for the page (was 35 vs 36).
2. The lattice table serialization contains the intact words and none of the
   four truncated forms.

Exit 0 when both hold.
"""

from __future__ import annotations

import json
import sys

import pdf_oxide

PDF = "/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/NIST_SP_800-53r5.pdf"
PAGE_INDEX = 18  # 0-based; printed page 19 (errata table)

PAIRS = [
    ("rivacy", "privacy"),
    ("apped", "mapped"),
    ("stablishes", "establishes"),
    ("ederal", "ederal"),  # 'federal'/'Federal' — suffix works for both cases
]


def main() -> int:
    doc = pdf_oxide.PdfDocument(PDF)
    problems: list[str] = []

    chars_p = sum(1 for c in doc.extract_chars(PAGE_INDEX) if c.char == "p")
    spans_p = sum(s.text.count("p") for s in doc.extract_spans(PAGE_INDEX))
    if chars_p != spans_p:
        problems.append(f"glyph parity: extract_chars has {chars_p} 'p', spans have {spans_p}")

    tables = doc.read_pdf(pages=str(PAGE_INDEX + 1), flavor="auto") or []
    joined = "\n".join(
        " | ".join(str(cell) for cell in row)
        for table in tables
        for row in (table.get("data") or [])
    )
    if not tables:
        problems.append("no lattice table extracted on the errata page")
    for bare, whole in PAIRS:
        truncated = any(sep + bare in joined for sep in (" ", "|", "\n", "“"))
        if truncated:
            problems.append(f"truncated form {bare!r} present in table serialization")
        if whole not in joined:
            problems.append(f"intact form {whole!r} absent from table serialization")

    report = {
        "source_pdf": PDF,
        "page_index": PAGE_INDEX,
        "p_glyphs_chars_vs_spans": [chars_p, spans_p],
        "problems": problems,
        "passed": not problems,
    }
    print(json.dumps(report, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
