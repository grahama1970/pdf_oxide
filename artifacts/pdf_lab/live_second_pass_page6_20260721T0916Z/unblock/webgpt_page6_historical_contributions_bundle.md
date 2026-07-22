# PDF Lab Page6 Historical Contributions Heading Bundle

## Current gate

Run exactly one live model-backed second-pass page case for NIST SP 800-53r5 page 6 and repair only the concrete extraction defect surfaced by that page review.

## Local evidence

- Repository: `grahama1970/pdf_oxide`
- Current commit on `origin/main`: `6f24c1684d50ac006323efa9dda068cbbd189a4b`
- Worktree: `/tmp/pdf_oxide_page45b_1784600709`
- Failed page6 run: `artifacts/pdf_lab/live_second_pass_page6_20260721T0916Z`
- Terminal ledger: `page_cases/page_case_0001_p0006/terminal_ledger.json`
- Terminal status: `still_open`
- Terminal reason: `patch_delegate_dry_run`
- Review response: `page_cases/page_case_0001_p0006/review_response.json`
- Model page status: `defect`
- Candidate count: `7`
- Defect candidate: `cand:p0006:0005:reference`
- Extracted block id: `actual:p6:block:5`
- Extracted text: `HISTORICAL CONTRIBUTIONS TO NIST SPECIAL PUBLICATION 800-53`
- Current extracted type: `reference`
- Current source_type: `Body`
- Current semantic_role: `reference_continuation`
- Bbox: `[0.24578431073357077, 0.41286838415897253, 0.7517472809436274, 0.4321111428617227]`
- Font size: `10.979999542236328`
- Bold: `true`

## Research context

Brave search receipt is saved at:

`artifacts/pdf_lab/live_second_pass_page6_20260721T0916Z/unblock/brave_page6_historical_contributions_heading.json`

The model reviewed the rendered page image and stated that this line is a bold centered section heading/title, not a reference citation. Nearby page text is acknowledgments/frontmatter prose.

## Proposed local next move

Add a focused NIST promotion-ledger rule for page 6 that reclassifies exactly this centered bold frontmatter heading from reference/reference_continuation to an appropriate heading classification, then add a regression that extracts page6 and asserts that the block is no longer a reference.

Allowed files:

- `python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json`
- `tests/test_nist_page6_historical_contributions_heading.py`
- PDF Lab page6 artifacts and `GOAL.md`

Forbidden adjacent scope:

- Do not alter SciLLM internals.
- Do not alter Tau DAG orchestration.
- Do not batch-repair unrelated page6 prose or chrome.
- Do not broaden reference classification globally unless the focused NIST rule cannot pass.

## Question for WebGPT

Is the proposed focused NIST ledger rule the correct current-gate repair for this page6 defect, or is there a smaller pdf_oxide-side repair that should happen first?

Return a concise assessment with one of:

- `PASS_CURRENT_GATE`
- `BLOCKED_CURRENT_GATE: <one concrete blocker>`
- `REJECTED_SCOPE_EXPANSION`
