"""Deterministic PUA check for issue #21 (NASA SP-2016-6105 p19).

Uses explicit ord() range comparisons rather than a regex character class.
A `[\\uE000-\\uF8FF]` literal silently degrades to `[-]` (a literal hyphen) if the
escape is interpreted before the regex sees it, which makes the check count
hyphens and report a false result in either direction. Do not reintroduce one.

Private Use Area (BMP): U+E000-U+F8FF.

Exit 0 when no PUA codepoints survive on the sampled pages, 1 otherwise.
"""

from __future__ import annotations

import json
import sys

import pdf_oxide

PDF = "/mnt/storage12tb/extractor_corpus/engineering/12 NASA_SP-2016-6105 Rev 2.pdf"
ANCHOR = "Technical Management Process"
PUA_LO, PUA_HI = 0xE000, 0xF8FF


def is_pua(ch: str) -> bool:
    return PUA_LO <= ord(ch) <= PUA_HI


def pua_chars(text: str) -> list[str]:
    return [c for c in text if is_pua(c)]


def main() -> int:
    # Issue #21 names NASA SP-2016-6105 page 19 (0-based) specifically. Default to
    # that page only. Wingdings PUA on other pages is a separate defect: this crate
    # has Symbol and ZapfDingbats encoding tables but no Wingdings table, so those
    # codepoints cannot be mapped without inventing one. Pass explicit page numbers
    # to widen the sweep.
    pages = [int(a) for a in sys.argv[1:]] or [19]
    doc = pdf_oxide.PdfDocument(PDF)

    total = 0
    per_page: dict[int, dict[str, int]] = {}
    anchors: list[dict[str, object]] = []

    for page in pages:
        found: dict[str, int] = {}
        for block in doc.classify_blocks(page):
            text = str(block.get("text", ""))
            for ch in pua_chars(text):
                key = f"U+{ord(ch):04X}"
                found[key] = found.get(key, 0) + 1
                total += 1
            if ANCHOR in text:
                anchors.append(
                    {
                        "page": page,
                        "block_type": block.get("block_type") or block.get("type"),
                        "pua_count": len(pua_chars(text)),
                        "leading_40": text[:40],
                    }
                )
        if found:
            per_page[page] = found

    # The bullet must decode AND the block must be reachable. An empty anchor list
    # would make a zero PUA count meaningless — it would prove only that we failed
    # to extract the paragraph at all.
    anchor_found = bool(anchors)
    anchor_clean = all(a["pua_count"] == 0 for a in anchors)

    report = {
        "source_pdf": PDF,
        "pages_checked": pages,
        "pua_total": total,
        "pua_by_page": per_page,
        "anchor_blocks": anchors,
        "anchor_found": anchor_found,
        "anchor_clean": anchor_clean,
        "passed": total == 0 and anchor_found and anchor_clean,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
