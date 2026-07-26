## Candidate Lock

Candidate: page456
Item: P456-TH-01
Defect class: table column-header structure and geometry
Page image dimensions: 918x1188

This issue covers only the top header row of the large ruled table titled `TABLE C-1: ACCESS CONTROL FAMILY`. It does not cover the other page456 fix errors.

## Current Evidence

Advisory reviewer source:

- `artifacts/pdf_lab/ask_competition/page456_visual_creator_reviewer_20260726/webgpt_round1_direct_response.md`
- Transport meta: `artifacts/pdf_lab/ask_competition/page456_visual_creator_reviewer_20260726/webgpt_round1_direct_response.meta.json`
- Important caveat: WebGPT produced raw response text with sentinel, but Surf marked the clean transport receipt failed with `missing_controlled_tab_id_or_contaminated_clean_output`.

Local PDF Lab evidence:

- Page image: `artifacts/pdf_lab/scillm_bug_report_pilot_gpt55_clusters/page_0456/page.png`
- Overlay: `artifacts/pdf_lab/scillm_bug_report_pilot_gpt55_clusters/page_0456/bbox_overlay.png`
- Candidate bundle: `artifacts/pdf_lab/scillm_bug_report_pilot_gpt55_clusters/page_0456/candidate_bundle.json`
- Extraction JSON: `artifacts/pdf_lab/scillm_bug_report_pilot_gpt55_clusters/page_0456/release_extraction_blocks.json`
- Existing focused regression: `tests/test_nist_page456_control_table_headers.py`

Freshness problem to resolve before patching:

- The candidate bundle reports 95 fix errors and shows the visible column header spans as `section_header` blocks.
- The current focused regression has also been observed passing locally.
- That mismatch means the next creator step must produce one fresh run directory where the page image, overlay, extraction JSON, and regression receipt all share the same run id.

## Annotated Parent Table

Normalized bbox:

```json
[
  0.14666667015723933,
  0.11371211812953756,
  0.8525490230984158,
  0.9040908813476562
]
```

Approximate pixel edges on the 918x1188 page:

```json
[
  134.64,
  135.09,
  782.64,
  1074.06
]
```

The fresh annotation must contain exact pixel and normalized bboxes for the parent table, the header row, and each of the four header cells.

## Target Extracted Blocks

- `actual:p456:line:2`
  - text: `CONTROL`
  - current_type: `section_header`
  - expected membership: first column-header cell

- `actual:p456:line:3`
  - text: `NUMBER`
  - current_type: `section_header`
  - expected membership: first column-header cell

- `actual:p456:line:52`
  - text: `CONTROL NAME`
  - current_type: `section_header`
  - expected membership: second column-header cell

- `actual:p456:line:98`
  - text: `IMPLEMENTED`
  - current_type: `section_header`
  - expected membership: third column-header cell, including the spatially associated `BY` span

- `actual:p456:line:106`
  - text: `ASSURANCE`
  - current_type: `section_header`
  - expected membership: fourth column-header cell

## Expected Extraction Behavior

1. The large ruled region is represented as one table object whose bbox matches the human annotation within 2 pixels on every edge.
2. The top ruled row is represented as the table's header row.
3. The header row contains exactly four ordered column-header cells: `CONTROL NUMBER`, `CONTROL NAME`, `IMPLEMENTED BY`, `ASSURANCE`.
4. The four cell bboxes are children of the same header row, contained within the annotated parent table, ordered left-to-right, aligned with ruled column boundaries, within 3 pixels per cell edge, and not overlapping neighboring cells by more than 1 pixel except for a shared border line.
5. The extraction JSON preserves source lineage from the listed block ids to the resulting header cells.
6. The listed source spans are not also emitted as standalone `section_header` or prose blocks.
7. The PDF Lab overlay visibly renders the parent table bbox, the header-row bbox, all four cell bboxes, and canonical table/header/cell labels.
8. Production extraction logic must not match page456, the NIST document title, control ids, or the literal header phrases.

## Required Regression

Update or replace:

`tests/test_nist_page456_control_table_headers.py`

The regression must run the existing `pdf_oxide` extraction path or verify the exact fresh extraction artifact named in its receipt.

It must assert:

- one containing table;
- one top header row;
- exactly four ordered header cells;
- annotated bbox tolerances;
- source-block lineage;
- no duplicate `section_header` or prose emission for the five target block ids;
- extraction JSON hash equals the hash recorded for the same run id.

## Failure Signatures

- `P456_TH_TARGET_REMAINS_SECTION_HEADER`
- `P456_TH_HEADER_ROW_MISSING`
- `P456_TH_CELL_COUNT_NOT_FOUR`
- `P456_TH_LABEL_SEQUENCE_WRONG`
- `P456_TH_IMPLEMENTED_BY_SPLIT_OR_INCOMPLETE`
- `P456_TH_PARENT_TABLE_MISMATCH`
- `P456_TH_HEADER_BBOX_OUTSIDE_TABLE`
- `P456_TH_CELL_EDGE_ERROR_GT_3PX`
- `P456_TH_CELL_OVERLAP_GT_1PX`
- `P456_TH_SOURCE_LINEAGE_MISSING`
- `P456_TH_DUPLICATE_HEADING_OR_PROSE`
- `P456_TH_OVERLAY_LABEL_OR_BOX_MISSING`
- `P456_TH_SOURCE_SPECIFIC_SHORTCUT`
- `P456_TH_STALE_OR_INCOMPLETE_REGRESSION`
- `P456_TH_RECEIPT_RUN_ID_MISMATCH`

## Creator Checklist

- [ ] Produce fresh pre-patch page image, extraction JSON, overlay, bbox metrics, regression output, and receipt under one run id.
- [ ] Demonstrate the corrected regression fails for the intended page456 structural/geometric assertion before production patching, unless the fresh JSON already proves the issue is resolved.
- [ ] Patch only P456-TH-01.
- [ ] Produce an entirely fresh post-patch bundle using the same command.
- [ ] Link the creator receipt and exact extraction diff.

## Reviewer Checklist

- [ ] Confirm all evidence belongs to one fresh run.
- [ ] Inspect page image and overlay at native 918x1188 resolution.
- [ ] Inspect exact extraction JSON and source lineage.
- [ ] Confirm four cell labels and bboxes against human annotation.
- [ ] Rerun the focused proof command.
- [ ] Inspect changed production files for source-specific shortcuts.
- [ ] Record PASS, FAIL, or evidenced BLOCKED with reviewer receipt.

## Status

PENDING. This issue is a reviewer-filed work item, not closure evidence.
