"""Re-test the predicate of each open extraction ticket against current main.

A ticket whose described defect no longer reproduces is stale and closable.
A ticket whose defect still reproduces is live and must stay open.

Nothing here is inferred from ticket prose: each check drives the real
extractor over the real source PDF named in the ticket and reports what came
back. Every result is written to a receipt for read-back.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

import pdf_oxide

NIST_R5 = "/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/NIST_SP_800-53r5.pdf"
NIST_AR5 = "/mnt/storage12tb/extractor_corpus/inbox/government/NIST.SP.800-53Ar5.pdf"
NASA = "/mnt/storage12tb/extractor_corpus/inbox/government/NASA_SE_Handbook_SP-2016-6105_8eeb48.pdf"

PUA = re.compile(r"[-]")
results: dict[str, object] = {}


def sha(path: str) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def check_issue_3() -> dict[str, object]:
    """#3: item 'h.' must not be merged with its numbered children 1/2/3."""
    doc = pdf_oxide.PdfDocument(NIST_R5)
    out: dict[str, object] = {"source_pdf": NIST_R5, "sha256": sha(NIST_R5)}
    for page in (45, 46):
        text = doc.extract_text(page)
        if "Notify account managers" not in text:
            out[f"page_{page}"] = "anchor 'Notify account managers' not on this page"
            continue
        # Reconstruct the h. item's run and see whether the numbered children
        # are glued into the same run rather than separated.
        idx = text.index("Notify account managers")
        window = text[idx : idx + 600]
        merged = bool(re.search(r"within:\s*1\.", window))
        out[f"page_{page}"] = {
            "anchor_found": True,
            "children_merged_into_parent": merged,
            "window": window[:300],
        }
    return out


def check_issue_6() -> dict[str, object]:
    """#6: flowchart p46 must not be shredded into a lattice table with text lost."""
    doc = pdf_oxide.PdfDocument(NIST_AR5)
    lost_texts = [
        "Pre-Assessment",
        "Ensure assessment plan is appropriately tailored",
        "Review assessor findings",
        "Notify key organizational officials of impending assessment",
        "Plans of Action and Milestones",
    ]
    text = doc.extract_text(46)
    found = {t: (t.replace(" ", "") in text.replace(" ", "")) for t in lost_texts}
    return {
        "source_pdf": NIST_AR5,
        "sha256": sha(NIST_AR5),
        "page_index": 46,
        "page_chars": len(text),
        "texts_present_on_page": found,
        "all_five_present": all(found.values()),
        "missing": [t for t, ok in found.items() if not ok],
    }


def check_issue_21() -> dict[str, object]:
    """#21: no Private Use Area codepoints may survive into extracted text."""
    doc = pdf_oxide.PdfDocument(NASA)
    out: dict[str, object] = {"source_pdf": NASA, "sha256": sha(NASA)}
    per_page = {}
    for page in (18, 19, 20):
        text = doc.extract_text(page)
        hits = PUA.findall(text)
        per_page[page] = {
            "chars": len(text),
            "pua_count": len(hits),
            "pua_codepoints": sorted({f"U+{ord(c):04X}" for c in hits}),
            "has_anchor": "Technical Management Processes" in text,
        }
    out["pages"] = per_page
    out["any_pua"] = any(p["pua_count"] > 0 for p in per_page.values())
    # Sweep more broadly: PUA anywhere in the first 60 pages.
    doc_pua = 0
    for page in range(0, min(60, doc.page_count())):
        doc_pua += len(PUA.findall(doc.extract_text(page)))
    out["pua_in_first_60_pages"] = doc_pua
    return out


def main() -> int:
    for name, fn in [("issue_3", check_issue_3), ("issue_6", check_issue_6), ("issue_21", check_issue_21)]:
        try:
            results[name] = fn()
        except Exception as exc:  # noqa: BLE001 -- recorded, never swallowed
            results[name] = {"error": repr(exc)}
    out = pathlib.Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
