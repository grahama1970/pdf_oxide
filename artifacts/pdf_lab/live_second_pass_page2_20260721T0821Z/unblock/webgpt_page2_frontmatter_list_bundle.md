# PDF Lab Page2 Frontmatter List Misclassification Gate

## Current Gate

Decide the next narrow repair for one live PDF Lab second-pass failure on NIST
SP 800-53r5 page 2. Do not broaden to other pages or dashboards.

## Failure Summary

Live one-page harness:

- Command family: `scripts/pdf_lab/run_second_pass_harness.py` with
  `--candidate-census-page 2`, `--review-mode live`, `--model local-text`,
  `--scillm-timeout-s 120`, `--patch-mode dry_run`, `--page-orchestrator-mode live`.
- Harness output: `artifacts/pdf_lab/live_second_pass_page2_20260721T0821Z/harness_report.json`
- Harness terminal status: `failed_closed`
- Page terminal status: `still_open`
- Page reason: `patch_delegate_dry_run`

The live model parsed successfully and identified one defect:

- Candidate: `cand:p0002:0009:list`
- Block: `actual:p2:block:9`
- Text: `U.S. Department of Commerce`
- Current extracted type: `list`
- Current source_type/raw block_type: `List`
- BBox: `[0.6077451020284416, 0.8100605974293719, 0.8529001871744791, 0.8303333629261364]`
- Model finding: this is a single-line issuing-organization attribution on a
  title/frontmatter page, not a list item.

Relevant local receipts:

- Page image: `artifacts/pdf_lab/live_second_pass_page2_20260721T0821Z/page_cases/page_case_0001_p0002/page_before.png`
- Annotated candidates: `artifacts/pdf_lab/live_second_pass_page2_20260721T0821Z/page_cases/page_case_0001_p0002/page_candidates.png`
- Extracted page JSON: `artifacts/pdf_lab/live_second_pass_page2_20260721T0821Z/page_cases/page_case_0001_p0002/page_before.json`
- Candidate presets: `artifacts/pdf_lab/live_second_pass_page2_20260721T0821Z/page_cases/page_case_0001_p0002/candidate_presets.json`
- Model response: `artifacts/pdf_lab/live_second_pass_page2_20260721T0821Z/page_cases/page_case_0001_p0002/review_response.json`
- Validator: `artifacts/pdf_lab/live_second_pass_page2_20260721T0821Z/page_cases/page_case_0001_p0002/review_validation.json`
- Terminal ledger: `artifacts/pdf_lab/live_second_pass_page2_20260721T0821Z/page_cases/page_case_0001_p0002/terminal_ledger.json`

## Research Context

Brave raw search was saved at:

`artifacts/pdf_lab/live_second_pass_page2_20260721T0821Z/unblock/brave_frontmatter_attribution_list_search.json`

The search was not very authoritative for PDF internals. It mostly confirmed
that "U.S. Department of Commerce" appears as organization/agency attribution
language in frontmatter contexts, not as a list construct. The actionable
evidence here is therefore the local page image, extracted JSON, and live model
finding.

## Source-Derived Hypothesis

This looks like a NIST/frontmatter preset repair, not a general PDF parser
repair:

1. The raw extractor classified the line as `List`.
2. The text has no bullet, marker, enumeration, indentation ladder, or sibling
   list context.
3. It is on page 2 title/frontmatter near the issuer/footer attribution region.
4. Similar page1 frontmatter title/DOI material passed live review after no code
   changes.

Likely smallest repair: add or adjust a NIST SP 800-53r5 promotion ledger rule
that retypes `U.S. Department of Commerce` on early frontmatter/title pages from
`list` to `paragraph_block` or an attribution-like semantic role, without
changing core list detection.

## Question For WebGPT

Return a concise gate ruling only:

`PASS_CURRENT_GATE` if the above repair surface is sufficiently justified.

`BLOCKED_CURRENT_GATE: <one concrete blocker>` if more evidence is required
before patching.

`REJECTED_SCOPE_EXPANSION` if this should not be treated as a narrow NIST
frontmatter preset repair.

Also name the expected focused regression invariant in one sentence.
