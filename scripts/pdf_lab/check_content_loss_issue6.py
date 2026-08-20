"""Deterministic content-loss check for issue #6 (800-53Ar5 p46 flowchart).

The Pre-Assessment/Assessment/Post-Assessment flowchart on page index 46 was
misread as a lattice table and five blocks' text vanished from the output.
This checks that the ticket's named texts survive SOMEWHERE on the page --
as blocks, table cells, or figure content. Whitespace is stripped before
matching so line wrapping and hyphenation of spaces cannot mask a hit.

Exit 0 when every named text survives, 1 otherwise.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata

import pdf_oxide

PDF = "/mnt/storage12tb/extractor_corpus/inbox/government/NIST.SP.800-53Ar5.pdf"
PAGE = 46

# The five texts issue #6 records as lost (ligature/space-insensitive keys).
REQUIRED = [
    "Pre-Assessment",
    "Ensure assessment plan is appropriately tailored",
    "Review assessor findings",
    "Notify key organizational officials of impending assessment",
    "Plans of Action and Milestones",
]


def squash(text: str) -> str:
    """Normalize to the comparison alphabet.

    Two document quirks must be neutralized or the check reports loss that is
    not loss:

    - fi/ffi ligatures (U+FB01/U+FB03): NFKC expands them.
    - The flowchart font's ToUnicode maps the 'ti' pair to U+019F 'Ɵ'
      ("NoƟfy", "AcƟon"). PyMuPDF reproduces the identical codepoint, so this
      is the document's encoding, not an extractor defect. The issue's own
      "ligature-aware search" missed it, which is how surviving text was
      recorded as lost.

    Hyphens/dashes and all whitespace are stripped so wrapping cannot mask a
    hit; the result is lowercased.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("Ɵ", "ti").replace("ŧ", "ti")
    return re.sub(r"[-‐-―\s]", "", text).lower()


def main() -> int:
    doc = pdf_oxide.PdfDocument(PDF)
    corpus = squash(doc.extract_text(PAGE))
    # Include table cell text explicitly in case extract_text omits it.
    try:
        for table in doc.extract_tables(PAGE) or []:
            corpus += squash(json.dumps(table, ensure_ascii=False))
    except Exception:  # noqa: BLE001 -- tables API absence must not mask block loss
        pass

    missing = [t for t in REQUIRED if squash(t) not in corpus]
    report = {
        "source_pdf": PDF,
        "page_index": PAGE,
        "required": len(REQUIRED),
        "present": len(REQUIRED) - len(missing),
        "missing": missing,
        "passed": not missing,
    }
    print(json.dumps(report, indent=2))
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
