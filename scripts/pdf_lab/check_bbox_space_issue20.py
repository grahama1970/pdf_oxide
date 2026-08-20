"""Deterministic bbox_space predicate for issue #20.

Three checks, all live:

1. STAMPING - a freshly built annotation call and a freshly extracted snapshot
   page both carry a bbox_space object that validates against
   contracts/bbox_space_v1.schema.json (per-page entries for the annotation
   call, one object for the snapshot page).

2. CONVENTION - block bboxes really are pdf_points_bottom_left_xywh: for a
   sampled block on 1512.03385v1 p4, the box interpreted bottom-left must
   contain the PyMuPDF search rect of its first words (top-left oracle,
   flipped), and the top-left interpretation must NOT.

3. HONESTY - the checker itself must be falsifiable: interpreting the same
   block with the wrong convention must fail containment.

Exit 0 only when all hold.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ARXIV = "/mnt/storage12tb/extractor_corpus/inbox/arxiv/1512.03385v1.pdf"
NIST = Path("/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/NIST_SP_800-53r5.pdf")
LEDGER = REPO / "python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json"


def validate_stamp(stamp: dict, problems: list[str], where: str) -> None:
    if not isinstance(stamp, dict):
        problems.append(f"{where}: bbox_space missing or not an object")
        return
    if stamp.get("schema") != "pdf_oxide.bbox_space.v1":
        problems.append(f"{where}: schema={stamp.get('schema')!r}")
    if stamp.get("space") != "pdf_points_bottom_left_xywh":
        problems.append(f"{where}: space={stamp.get('space')!r}")


def validate_page_stamp(stamp: dict, problems: list[str], where: str) -> None:
    validate_stamp(stamp, problems, where)
    if not isinstance(stamp, dict):
        return
    box = stamp.get("crop_box")
    if not (isinstance(box, list) and len(box) == 4 and box[2] > 0 and box[3] > 0):
        problems.append(f"{where}: crop_box={box!r}")
    if stamp.get("rotation") not in (0, 90, 180, 270):
        problems.append(f"{where}: rotation={stamp.get('rotation')!r}")


def main() -> int:
    problems: list[str] = []

    import pdf_oxide
    import pymupdf
    from pdf_oxide.annotation_call import build_annotation_call
    from pdf_oxide.pipeline import extract_pdf

    # --- 1a. annotation call stamping (live build over the real arxiv PDF).
    result = extract_pdf(ARXIV)
    call = build_annotation_call(result)
    stamp = call.get("bbox_space")
    validate_stamp(stamp if isinstance(stamp, dict) else {}, problems, "annotation_call")
    pages = (stamp or {}).get("pages") if isinstance(stamp, dict) else None
    if not isinstance(pages, dict) or not pages:
        problems.append("annotation_call: bbox_space.pages missing/empty")
    else:
        validate_page_stamp(pages.get("0", {}), problems, "annotation_call.pages[0]")

    # --- 1b. snapshot page stamping (live extraction of the NIST page).
    sys.path.insert(0, str(REPO / "scripts/pdf_lab"))
    try:
        import snapshot_current_extraction as snapshot

        page = snapshot._extract_page(NIST, 44, LEDGER, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(str(REPO / "scripts/pdf_lab"))
    validate_page_stamp(page.get("bbox_space", {}), problems, "snapshot_page")

    # --- 2/3. convention proof against the PyMuPDF oracle.
    doc = pdf_oxide.PdfDocument(ARXIV)
    m = pymupdf.open(ARXIV)
    page_index = 4
    height = m[page_index].rect.height
    anchor = "We argue that this optimization"
    rects = m[page_index].search_for(anchor)
    if not rects:
        problems.append("oracle: search_for found nothing")
    else:
        r = rects[0]
        target = None
        for block in doc.classify_blocks(page_index):
            if " ".join(str(block.get("text", "")).split()).startswith("We argue"):
                target = block["bbox"]
                break
        if target is None:
            problems.append("oracle: anchor block not extracted")
        else:
            x, y, w, h = target
            # bottom-left reading, flipped into the oracle's top-left space:
            bl_top, bl_bottom = height - y - h, height - y
            tl_top, tl_bottom = y, y + h
            margin = 3.0
            bl_contains = bl_top <= r.y0 + margin and bl_bottom >= r.y1 - margin and x <= r.x0 + margin
            tl_contains = tl_top <= r.y0 + margin and tl_bottom >= r.y1 - margin and x <= r.x0 + margin
            if not bl_contains:
                problems.append(
                    f"convention: bottom-left reading does not contain oracle line "
                    f"(block top {bl_top:.1f}..{bl_bottom:.1f}, oracle {r.y0:.1f}..{r.y1:.1f})"
                )
            if tl_contains:
                problems.append(
                    "honesty: top-left reading ALSO contains the oracle line; the check "
                    "cannot distinguish conventions on this sample"
                )

    report = {
        "arxiv_pdf": ARXIV,
        "nist_pdf": str(NIST),
        "problems": problems,
        "passed": not problems,
    }
    print(json.dumps(report, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
