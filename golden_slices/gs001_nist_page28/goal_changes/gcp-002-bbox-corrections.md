# Goal-change packet GCP-002 — correct two agent-derived bboxes

Status: APPLIED BY AGENT, AWAITING HUMAN SIGNATURE
Raised: 2026-07-18 · Author: project agent · Signer: __________ (graham@grahama.co)

## Why

After the classifier and merger repairs, page 28 sits at 9/11. Both remaining
misses are `bbox_iou_below_threshold` with the block TYPE already correct, and
both trace to bboxes the agent derived in GCP-001 — not to extractor defects.

## Row 1 — separator rule wrongly included

The bbox was derived from a geometric "top 60pt band", which swept in both the
running header text AND the horizontal separator rule beneath it. The rule is
page furniture already covered by `gs001-waiver-002`, so the expected element
should cover the header text alone.

  before  [90.0, 35.43, 521.26, 55.87]   (text + rule)
  after   header text line only, separator excluded

## Row 2 — rotated text has two legitimate conventions

The contract bbox describes the DOI watermark as it appears visually: a tall,
narrow span down the left margin. pdf_oxide reports rotated text in text-flow
orientation: a wide, short span. Neither is incorrect; they are different
representations of the same glyph run, and no IoU threshold can reconcile them.

The human v2 labelling anticipated this. Row 2's `match_strategy` is
`text_contains_or_bbox_region` — OR semantics, text match alone sufficient.
The comparator implements AND. Honouring the declared strategy is implementing
documented human intent, NOT widening the gate to force a pass.

Recorded as `bbox_match: waived_rotated_text_orientation`.

## Not done here

Row 3's `allowed_types` includes `section_heading`, which the AGENT authored —
not the human. That is deliberately NOT relied upon; row 3 passes because the
engine now emits a real `ChapterLabel` type, not because the gate was widened.
