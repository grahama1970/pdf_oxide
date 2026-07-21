# PDF Oxide Page41 Tau Boundary Review Bundle

## Current Gate

Run one live model-backed second-pass review lifecycle for the next current
PDF Lab page candidate without letting `pdf_oxide` call SciLLM/OpenCode
directly. Tau must own model/executor transport.

## Immutable Goal

Harden all PDF Lab page candidates one page/checklist item at a time across the
active candidate queue, preserving visual/human evidence, focused regression
proof, Tau-owned model/executor DAG/harness contracts, deterministic audit,
commits, pushes, and blocked receipts when needed.

## Deterministic State

- Repo: `https://github.com/grahama1970/pdf_oxide`
- Clean worktree: `/tmp/pdf_oxide_integrate_gs001_20260721`
- Latest pushed `origin/main`: `b801350ac2b703497508bacae5df541ea3f6e9e1`
- DOI-chrome census receipt:
  `artifacts/pdf_lab/doi_chrome_census_20260721/audit_summary.json`
- DOI result: 492 pages, 9333 candidates, 576 DOI/publication anchor records,
  0 non-side-chrome records, 0 combined rotated DOI left-margin bbox escapes.
- Focused post-GS001 tests:
  `uv run pytest -q tests/test_nist_page45_chrome_noise.py tests/test_nist_page45_ac1_list_markers.py tests/test_nist_page34_sidebar_chrome.py tests/test_nist_page_28_regression.py`
  returned `11 passed in 7.13s`.

## Fresh Candidate Selection

Initial fresh selector chose page 15, but `GOAL.md` already records page 15 as
reviewed-clean evidence, so that selection was rejected as stale.

Filtered manifest excludes completed/blocked pages from `GOAL.md`:

- Excluded pages: 1-28, 34, 35, 36, 42, 45, 46, 76, 104, 157, 421, 456.
- Filtered manifest:
  `artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/candidate_manifest_unreviewed_pages.json`
- Fresh selected page:
  `artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/sampled_page_cases_unreviewed.json`
- Selected page: 41.
- Selected candidates: 13 total: 4 side_chrome, 5 text, 1 section_heading, 3 footnote.

## Page41 Evidence Prep

Command was deterministic only; all model/runtime modes were forced dry-run:

```bash
uv run python scripts/pdf_lab/run_page_second_pass_dag.py \
  --pdf /mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf \
  --manifest artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/candidate_manifest_unreviewed_pages.json \
  --sampled-cases artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/sampled_page_cases_unreviewed.json \
  --out artifacts/pdf_lab/live_second_pass_page41_tau_prep_20260721 \
  --page 41 \
  --ledger python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json \
  --apply-mode release \
  --review-mode dry_run \
  --scillm-preflight-mode dry_run \
  --patch-mode dry_run \
  --patch-backend scillm_orchestrator \
  --page-orchestrator-mode dry_run \
  --caller-skill pdf-lab \
  --model vlm-free2 \
  --page-extract-timeout-s 30
```

Result:

- Case dir:
  `artifacts/pdf_lab/live_second_pass_page41_tau_prep_20260721/page_case_0001_p0041`
- Terminal status: `still_open`
- Terminal reason: `dry_run_review_not_executed`
- `review_request_validation.ok`: true
- `scillm_orchestrator_page_submission_validation.ok`: true
- `review_validation.ok`: false only because `dry_run_review_not_executed`
- Required evidence exists: `page_before.png`, `page_candidates.png`,
  `page_before.json`, `candidate_presets.json`, `review_request.json`,
  `review.html`, `review_bundle.zip`.

## Tau Boundary Found

Tau local docs/code show:

- `uv run tau dag-run <spec>` generic DAG runner exists.
- Tau `scillm-worker-launch` exists for `/v1/scillm/opencode/runs`.
- I did not find an obvious Tau-owned generic `/v1/chat/completions` VLM
  executor that can consume the PDF Lab `review_request.json` image/text payload
  and write `review_response.json` plus a provider-live Tau receipt.

Filed Tau issue:

- `https://github.com/grahama1970/tau/issues/122`

## WebGPT/Brave Unblock Attempts

Brave Search pre-step:

- Query: `grahama1970 tau scillm chat completions worker executor`
- Artifact:
  `artifacts/pdf_lab/live_second_pass_page41_tau_prep_20260721/webgpt_unblock_brave_search.json`

WebGPT exact tab attempt:

- Human-provided tab id: `837359458`
- URL:
  `https://chatgpt.com/g/g-p-6a2421edf6c48191bf65737182e5a073-pdf-lab/c/6a5beb30-367c-83ea-888b-97cb1a22cf2c`
- Browser Oracle doctor: `readiness=needs_attention`,
  issue `tab_stale_manual_binding`, `tab_open=false`.

WebClaude binding:

- Project: `webclaude`
- Tab: `837360442`
- Doctor: `readiness=ready`

## Exact Question

Review only this current gate. Is the correct next action:

1. Treat page41 live model review as blocked until Tau implements or exposes a
   generic SciLLM chat/VLM review executor for PDF Lab page cases, preserving
   the deterministic page41 prep artifacts and Tau issue #122; or
2. Use an existing Tau command/surface I missed to execute this exact
   `review_request.json` through Tau-owned transport without `pdf_oxide`
   directly calling SciLLM?

Return exactly one ruling:

`PASS_CURRENT_GATE` if the blocked-at-Tau disposition is correct.

`BLOCKED_CURRENT_GATE: <one concrete blocker>` if the disposition is wrong or
missing a required artifact.

`REJECTED_SCOPE_EXPANSION` if this bundle tries to move outside the current
page41/Tau-boundary gate.
