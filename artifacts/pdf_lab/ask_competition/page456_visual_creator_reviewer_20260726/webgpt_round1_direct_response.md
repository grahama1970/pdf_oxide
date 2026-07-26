Worked for 3m 58s
1. Diagnosis of where the project-agent has been spiraling

The project-agent has been treating process state as extraction state. The clearest symptom is that a focused regression passes while the current PDF Lab bundle still shows 95 fix errors and visibly misclassifies the page456 table headers. That means the test, extraction JSON, page image, and overlay are not bound to the same fresh extractor run.

The specific spiral is:

Stale-artifact acceptance: a green regression is being trusted without proving which PDF hash, extractor build, page render, and extraction JSON it exercised.

Text-only correctness: the agent is checking that headings disappear, but not that the corresponding table, header-row, and cell bboxes are correctly placed and labeled over the page.

Process substitution: git status, detached-head concerns, issue organization, and generalized workflow work are displacing the actual page defect.

Batch dilution: “95 fix errors” is being treated as a broad cleanup instead of one independently reviewable defect at a time.

Reviewer prose as evidence: model commentary is being allowed to stand in for a fresh overlay and exact JSON.

Weak regression scope: the existing passing test evidently does not assert all of these together:

the four visible headers are represented as column-header cells;

their boxes align with the ruled table cells;

they belong to the large table;

the source spans are not duplicated as section_header or prose;

the test output corresponds to the same fresh run shown in PDF Lab.

The controlling defect is therefore not merely “bad headings.” It is missing or incorrect geometric table structure with stale proof allowing a false green result.

2. The creator-reviewer loop I recommend

Lock one item.
The active item is only:

P456-TH-01 — reconstruct the four column-header cells for the large ruled table

Do not include the remaining page456 errors or another page.

Creator produces a fresh pre-patch run bundle.
The creator runs the existing pdf_oxide/PDF Lab extraction path through a thin evidence runner. The runner must not parse the PDF independently. It only invokes the repository’s existing page renderer, extractor, overlay renderer, and focused test.

Every output goes under one new run directory and shares one run_id.

The evidence runner records provenance.
Its receipt must include:

source PDF SHA-256;

physical page selector used by the extractor;

page-image SHA-256 and dimensions, expected to be 918x1188;

extractor command argv;

extractor executable or package revision/hash;

exact extraction JSON SHA-256;

annotation SHA-256;

overlay SHA-256;

focused-test command, exit code, and output hash;

UTC generation time;

run ID.

A passing test from another run is irrelevant.

Reviewer inspects the fresh PDF Lab evidence before filing the issue.
The reviewer checks the fresh page image, overlay, and exact JSON together. The reviewer must identify concrete block IDs and geometric failures, not merely state that the table looks wrong.

Reviewer files one concrete GitHub issue.
The issue includes the five representative target IDs, the table bbox, expected row/cell relationships, bbox tolerances, failure signatures, proof command, and links to the fresh pre-patch bundle.

Creator updates the focused regression before changing production extraction.
The regression must invoke the current extraction path or consume the exact run output whose hash is recorded in the test receipt. It must assert both structure and geometry.

Given the currently bad extraction, the corrected regression should fail. A passing result while the fresh JSON still has the listed section_header blocks is itself a failure named STALE_OR_INCOMPLETE_REGRESSION.

Creator patches one extraction behavior.
The patch must use general geometric or table-layout evidence such as ruled boundaries, row alignment, shared vertical extent, column separators, and table containment. It must not branch on page 456, the NIST document identity, control identifiers, or the literal header words.

Creator produces a fresh post-patch bundle with the identical command.
The post-patch run must contain a newly rendered page image, newly rendered overlay, newly extracted JSON, fresh bbox metrics, fresh regression output, and a new receipt. Reusing the earlier PNG or JSON is prohibited even when the source PDF is unchanged.

Reviewer independently reruns and gives a fail-closed verdict.
The reviewer records:

PASS: visual geometry, JSON structure, bbox metrics, lineage, and regression all pass;

FAIL: any acceptance assertion fails;

BLOCKED: only an external dependency or human annotation decision prevents execution.

The checklist item closes only on reviewer PASS.
A merged patch, green creator test, or closed GitHub issue does not advance the candidate. Page456 remains active until every page456 checklist item is independently proven or explicitly blocked.

3. The exact first GitHub issue the reviewer should file for page456

Title

[PDF Lab][page456][P456-TH-01] Emit four ruled-table column-header cells with accurate overlay bboxes

Body

Markdown
## Candidate lock

Candidate: page456
Item: P456-TH-01
Defect class: table column-header structure and geometry
Page image dimensions: 918x1188

This issue covers only the top header row of the large ruled table titled
"TABLE C-1: ACCESS CONTROL FAMILY". It does not cover the other page456
fix errors.

## Fresh baseline evidence

Run ID:
Fresh-run receipt:
Source PDF SHA-256:
Extractor revision/hash:
Exact extraction JSON:
Page image:
Human annotation:
Rendered overlay:
Focused regression receipt:

All links above must refer to the same run ID. A regression receipt from a
different extraction run is not acceptable evidence.

## Annotated parent table

Normalized bbox:

[
  0.14666667015723933,
  0.11371211812953756,
  0.8525490230984158,
  0.9040908813476562
]

Approximate pixel edges on the 918x1188 page:

[
  134.64,
  135.09,
  782.64,
  1074.06
]

The fresh annotation must contain exact pixel and normalized bboxes for:
- the parent table;
- the header row;
- each of the four header cells.

## Target extracted blocks

- actual:p456:line:2
  - text: "CONTROL"
  - current_type: section_header
  - expected membership: first column-header cell

- actual:p456:line:3
  - text: "NUMBER"
  - current_type: section_header
  - expected membership: first column-header cell

- actual:p456:line:52
  - text: "CONTROL NAME"
  - current_type: section_header
  - expected membership: second column-header cell

- actual:p456:line:98
  - text: "IMPLEMENTED"
  - current_type: section_header
  - expected membership: third column-header cell
  - include the spatially associated "BY" span in this cell, whether it is
    currently a separate block or fragment

- actual:p456:line:106
  - text: "ASSURANCE"
  - current_type: section_header
  - expected membership: fourth column-header cell

## Expected extraction behavior

1. The large ruled region is represented as one table object whose bbox
   matches the human annotation within 2 pixels on every edge.

2. The top ruled row is represented as the table's header row.

3. The header row contains exactly four ordered column-header cells:
   - CONTROL NUMBER
   - CONTROL NAME
   - IMPLEMENTED BY
   - ASSURANCE

4. The four cell bboxes:
   - are children of the same header row;
   - are contained within the annotated parent table;
   - follow left-to-right page order;
   - align with the ruled column boundaries;
   - match the human-annotated cell edges within 3 pixels per edge;
   - do not overlap neighboring cells by more than 1 pixel, excluding a
     shared border line;
   - cover the contributing source text spans.

5. The extraction JSON preserves source lineage from the listed block IDs
   to the resulting header cells.

6. The listed source spans are not also emitted as standalone
   section_header or prose blocks.

7. The PDF Lab overlay visibly renders:
   - the parent table bbox;
   - the header-row bbox;
   - all four cell bboxes;
   - the existing repository's canonical labels for table, header row,
     and column-header cell.

8. Production extraction logic must not match:
   - page456;
   - the NIST document title;
   - control IDs;
   - the literal header phrases.

## Required regression

Path:

tests_test_pdf_lab/test_page456_control_table_header.py

The regression must run the existing pdf_oxide extraction path or verify the
exact fresh extraction artifact named in its receipt.

It must assert:
- one containing table;
- one top header row;
- exactly four ordered header cells;
- annotated bbox tolerances;
- source-block lineage;
- no duplicate section_header/prose emission;
- extraction JSON hash equals the hash recorded for the same run ID.

## Failure signatures

The item fails with a named signature when any condition occurs:

- P456_TH_TARGET_REMAINS_SECTION_HEADER
- P456_TH_HEADER_ROW_MISSING
- P456_TH_CELL_COUNT_NOT_FOUR
- P456_TH_LABEL_SEQUENCE_WRONG
- P456_TH_IMPLEMENTED_BY_SPLIT_OR_INCOMPLETE
- P456_TH_PARENT_TABLE_MISMATCH
- P456_TH_HEADER_BBOX_OUTSIDE_TABLE
- P456_TH_CELL_EDGE_ERROR_GT_3PX
- P456_TH_CELL_OVERLAP_GT_1PX
- P456_TH_SOURCE_LINEAGE_MISSING
- P456_TH_DUPLICATE_HEADING_OR_PROSE
- P456_TH_OVERLAY_LABEL_OR_BOX_MISSING
- P456_TH_SOURCE_SPECIFIC_SHORTCUT
- P456_TH_STALE_OR_INCOMPLETE_REGRESSION
- P456_TH_RECEIPT_RUN_ID_MISMATCH

## Creator checklist

- [ ] Produce fresh pre-patch page image, extraction JSON, overlay, bbox
      metrics, regression output, and receipt under one run ID
- [ ] Demonstrate the corrected regression fails for the intended page456
      structural/geometric assertion before production patching
- [ ] Patch only P456-TH-01
- [ ] Produce an entirely fresh post-patch bundle using the same command
- [ ] Link the creator receipt and exact extraction diff

## Reviewer checklist

- [ ] Confirm all evidence belongs to one fresh run
- [ ] Inspect page image and overlay at native 918x1188 resolution
- [ ] Inspect exact extraction JSON and source lineage
- [ ] Confirm four cell labels and bboxes against human annotation
- [ ] Rerun the focused proof command
- [ ] Inspect changed production files for source-specific shortcuts
- [ ] Record PASS, FAIL, or evidenced BLOCKED with reviewer receipt

## Verdict

Status: PENDING

Reviewer:
Reviewer receipt:
Reason:
4. The first local proof command and artifact outputs the creator must produce

Use a thin evidence runner in the existing scripts_pdf_lab area. It must invoke the current repository extraction and rendering functions; it must not implement another extractor.

Bash
run_id="page456-control-table-header-$(date -u +%Y%m%dT%H%M%SZ)"

python scripts_pdf_lab/refresh_candidate.py \
  --candidate-manifest prompts_pdf_lab/page456-control-table-header.json \
  --annotation artifacts_pdf_lab/page456/control-table-header/human-annotation.json \
  --test tests_test_pdf_lab/test_page456_control_table_header.py \
  --run-id "$run_id" \
  --output-dir "artifacts_pdf_lab/page456/control-table-header/runs/$run_id"

The command must produce, even when its audit exits nonzero:

artifacts_pdf_lab/page456/control-table-header/runs/<run_id>/
├── page.png
├── human-annotation.json
├── extraction.pdf_oxide.json
├── overlay.png
├── bbox-metrics.json
├── regression.stdout.txt
├── regression.json
├── extraction-command.json
├── receipt.json
└── SHA256SUMS

Required contents:

page.png

freshly rendered;

exactly 918x1188;

hash recorded in receipt.json.

human-annotation.json

copied byte-for-byte from the annotation used for this run;

parent table, header-row, and four header-cell boxes;

coordinate space and page dimensions explicitly recorded.

extraction.pdf_oxide.json

exact unedited output from the current extractor;

no post-processing solely for display.

overlay.png

freshly rendered from this run’s page image, extraction JSON, and annotation;

shows parent-table, header-row, and cell boxes with labels.

bbox-metrics.json

predicted and annotated boxes;

per-edge pixel errors;

containment;

ordering;

overlap;

source-block-to-cell lineage;

failure signatures.

regression.json

command argv;

exit code;

collected test count;

named assertions;

extraction hash exercised by the test;

run ID.

receipt.json

all input/output hashes;

source PDF hash;

extractor revision/hash;

physical page selector;

all command argv arrays;

run ID;

audit verdict.

The first run should not be accepted merely because pytest returns zero. With the described fresh extraction, either the updated regression should fail on the structural/geometric defect or the bundle audit should emit:

P456_TH_STALE_OR_INCOMPLETE_REGRESSION

when the regression passes but the fresh extraction or bbox metrics still contain any listed failure.

A minimal receipt check after the command is:

Bash
run_dir="artifacts_pdf_lab/page456/control-table-header/runs/$run_id"

test -s "$run_dir/page.png"
test -s "$run_dir/extraction.pdf_oxide.json"
test -s "$run_dir/overlay.png"
test -s "$run_dir/bbox-metrics.json"
test -s "$run_dir/regression.json"
test -s "$run_dir/receipt.json"

sha256sum -c "$run_dir/SHA256SUMS"

jq -e --arg run_id "$run_id" '
  .run_id == $run_id and
  .page.width == 918 and
  .page.height == 1188 and
  .artifacts.page_sha256 != null and
  .artifacts.extraction_sha256 != null and
  .artifacts.overlay_sha256 != null and
  .artifacts.annotation_sha256 != null and
  .regression.extraction_sha256 == .artifacts.extraction_sha256
' "$run_dir/receipt.json"

That command and bundle establish only a fresh baseline or failure state. They do not prove that page456 is fixed.

5. How to score this against other model proposals in rounds 2 and 3

Use a 100-point rubric with hard disqualifiers. Score proposals on what the project agent can actually verify, not on prose confidence.

A. Visual geometry and labeling — 35 points

10: requires a fresh native-resolution page image.

10: requires annotated parent-table, header-row, and four cell bboxes.

10: defines measurable edge, containment, order, and overlap tolerances.

5: requires visible canonical labels in the PDF Lab overlay.

A proposal receives at most 10/35 if it only says “convert headings to a table.”

B. Freshness and receipt binding — 25 points

10: one run ID binds page image, extraction JSON, overlay, annotation, and test.

5: records source PDF and extractor hashes.

5: detects a green regression that exercised stale or different extraction output.

5: requires entirely fresh post-patch artifacts.

A proposal receives zero in this category if it accepts the currently passing regression without reconciling the stale UX bundle.

C. Concrete GitHub work unit — 15 points

5: one page456 issue and one defect item.

5: names target block IDs and expected outcomes.

5: includes proof command, failure signatures, and receipt links.

Issue templates, labels, or project boards earn no points by themselves.

D. Creator-reviewer independence — 15 points

5: creator establishes a fresh baseline before patching.

5: reviewer independently inspects visual and JSON evidence.

5: closure requires reviewer PASS or explicit BLOCKED evidence.

A creator self-certification without independent rerun receives at most 5/15.

E. Anti-overfitting and anti-spiral behavior — 10 points

5: prohibits page, document, control-ID, and literal-text shortcuts.

5: prevents batching, dashboard work, git cleanup, or unrelated refactors from advancing the candidate.

Hard disqualifiers

Regardless of numeric score, do not promote a proposal that:

claims page456 or the broader PDF goal is complete without local artifacts;

accepts a regression unrelated to the fresh extraction hash;

lacks page-image and overlay proof;

omits concrete bbox acceptance criteria;

proposes literal header-text or page-number matching;

advances to another candidate before page456 receives fresh visual, JSON, regression, and reviewer receipts;

replaces the existing pdf_oxide extraction path with a parallel parser.

For round 2, score the proposed contract against the locally returned baseline artifacts and identify mismatches. For round 3, score only the implemented creator/reviewer loop and reviewer receipt; reduce or discard any round-1 points that were not realized in local files and commands.
