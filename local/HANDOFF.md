# Handoff Report: pdf_oxide

**Timestamp**: 2026-07-21T15:45:00Z
**Active Agent**: codex

## 1. Project Overview

- **Ecosystem**: Rust parser plus Python PDF Lab harness/scripts.
- **Core Purpose**: PDF-spec-compliant extraction, with PDF Lab hardening against NIST SP 800-53r5 page candidates.
- **Immutable Goal**: Harden all PDF Lab page candidates one page/checklist item at a time until every active candidate has deterministic evidence or an explicit blocked receipt.

## 2. Verified Current State

- Main repo URL: `https://github.com/grahama1970/pdf_oxide`
- Remote main verification command: `git ls-remote origin refs/heads/main`
- Verified remote main before Tau #123 root-cause receipt import: `67cbdec1acfe3254ecb191a71fc249bd9a1021f2`
- Clean integration worktree: `/tmp/pdf_oxide_integrate_gs001_20260721`
- Integration branch: `codex/integrate-gs001-reconciler-20260721`
- Do not continue from the dirty detached checkout at `/home/graham/workspace/experiments/pdf_oxide` unless intentionally reconciling worktrees.

## 3. What Landed On Main

- `44410852` cherry-picks source repair `61050fc5`.
- `042126b1` records the current-main integration proof and updates `GOAL.md` so model/executor work is Tau-owned only.
- `25808d30` rechecks page34 body-lines-as-headings after GS001 integration.
- `d5b14547` rechecks page45 AC-1 list-threshold markers after GS001 integration.
- Repair scope:
  - `src/extractors/table_block_reconciler.rs`
  - `src/extractors/document_extractor.rs`
  - `src/extractors/mod.rs`
  - `src/python.rs`
  - `python/pdf_oxide/pipeline_extract.py`
  - `python/pdf_oxide/pipeline_types.py`

## 4. Proof

- Focused Python regression:
  - Command: `uv run pytest -q tests/test_nist_table_duplicate_suppression.py tests/test_nist_page_28_regression.py`
  - Result: `15 passed in 2.03s`
  - `mocked:false`, `live:false`
- Rust library baseline:
  - Command: `cargo test --lib`
  - Result: `4543 passed, 1 failed, 8 ignored`
  - Only failure: `pipeline::converters::toc_detector::tests::test_extract_simple_toc`
  - This matches the pinned pre-existing TOC failure class, though the observed pass count is `4543`, not Claude's reported `4530`.
- Integration receipt:
  - `artifacts/pdf_lab/gs001_reconciler_main_integration_20260721/integration_receipt.json`
- Post-GS001 page34/page45/DOI checks:
  - `uv run pytest -q tests/test_nist_page45_chrome_noise.py tests/test_nist_page45_ac1_list_markers.py tests/test_nist_page34_sidebar_chrome.py tests/test_nist_page_28_regression.py`
  - Result: `11 passed in 7.13s`
  - `artifacts/pdf_lab/page34_recheck_after_gs001_20260721/audit_summary.json`
  - `artifacts/pdf_lab/page45_list_threshold_recheck_after_gs001_20260721/audit_summary.json`
  - `artifacts/pdf_lab/doi_chrome_census_20260721/audit_summary.json`
- Full DOI/publication side-chrome census:
  - Command: `uv run python scripts/pdf_lab/build_pdf_element_candidate_manifest.py --pdf /mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf --out artifacts/pdf_lab/doi_chrome_census_20260721/candidate_manifest_full_plain_uv_after_dev_pymupdf.json --ledger python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json --apply-mode release --page-timeout-s 30 --debug-log artifacts/pdf_lab/doi_chrome_census_20260721/candidate_manifest_full_plain_uv_after_dev_pymupdf_debug.log --progress-path artifacts/pdf_lab/doi_chrome_census_20260721/candidate_census_progress_full_plain_uv_after_dev_pymupdf.json`
  - Result: `candidate_count=9333`, `page_count=492`, `extracted_page_count=492`, `census_failure_count=0`
  - DOI/publication anchor analysis: `576` anchor records on `492` pages, `0` non-side-chrome records, `0` combined rotated DOI records outside the left-margin bbox contract.
- Page41 current-candidate prep:
  - Selection artifacts:
    - `artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/stale_selection_rejection.json`
    - `artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/candidate_manifest_unreviewed_pages.json`
    - `artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/sampled_page_cases_unreviewed.json`
  - Selection result: initial page 15 sample rejected as stale because `GOAL.md` already records page15 reviewed-clean; filtered manifest has `453` pages and `8897` candidates; selected page 41 with `13` candidates.
  - Prep command:
    - `uv run python scripts/pdf_lab/run_page_second_pass_dag.py --pdf /mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf --manifest artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/candidate_manifest_unreviewed_pages.json --sampled-cases artifacts/pdf_lab/fresh_candidate_selection_after_doi_chrome_20260721/sampled_page_cases_unreviewed.json --out artifacts/pdf_lab/live_second_pass_page41_tau_prep_20260721 --page 41 --ledger python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json --apply-mode release --review-mode dry_run --scillm-preflight-mode dry_run --patch-mode dry_run --patch-backend scillm_orchestrator --page-orchestrator-mode dry_run --caller-skill pdf-lab --model vlm-free2 --page-extract-timeout-s 30`
  - Prep result: `case_dir=artifacts/pdf_lab/live_second_pass_page41_tau_prep_20260721/page_case_0001_p0041`, `terminal_status=still_open` because the model review was deliberately not executed from pdf_oxide.
  - Deterministic validation:
    - `review_request_validation.ok=true`, `image_part_count=2`, `text_part_count=1`, request SHA `0d5241ec3e224f72291138120f888832baf054a7dc4e4564183cc6f0725eff43`
    - `review_bundle_validation.ok=true`
    - `scillm_orchestrator_page_submission_validation.ok=true`, DAG spec SHA `d555159d2a6dcc10c20a6eef494aa312fa48291cbdb133f6bd2e07c272fbfa23`

## 5. Page41 Tau Boundary Resolution

- Page41 was explicitly blocked at the Tau boundary, not by a new pdf_oxide extraction failure.
- Historical block receipt:
  - `artifacts/pdf_lab/page41_tau_boundary_blocked_20260721/audit_summary.json`
- Tau issue and fix:
  - `https://github.com/grahama1970/tau/issues/122`
  - Title: `Add Tau-owned generic SciLLM chat/VLM review executor for PDF Lab page cases`
  - Tau commit: `b48d815c34ee603e79f4c6f64edcfc92ccf3d8d4`
  - New Tau command: `tau scillm-chat-review`
  - Tau proof bundle: `/tmp/tau-issue122-pdf-lab-executor/experiments/goal-locked-subagents/proofs/issue-122-pdf-lab-chat-review-20260721/`
- Historical external unblock attempts:
  - Brave Search artifact: `artifacts/pdf_lab/live_second_pass_page41_tau_prep_20260721/webgpt_unblock_brave_search.json`
  - WebGPT browser-oracle doctor artifact: `artifacts/pdf_lab/page41_tau_boundary_blocked_20260721/webgpt_browser_oracle_doctor.json`; readiness `needs_attention`, issue `tab_stale_manual_binding`, tab `837359458`
  - Ask/Tau WebClaude run dir: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_page41_tau_boundary/pdf-lab-page41-tau-boundary-webclaude-20260721`
  - Ask/Tau DAG receipt: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_page41_tau_boundary/pdf-lab-page41-tau-boundary-webclaude-20260721/tau-receipts/dag-receipt.json`; status `BLOCKED`, verdict `COMMAND_FAILED`, `mocked=false`, `live=true`, `provider_live=false`
  - WebClaude node receipt: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_page41_tau_boundary/pdf-lab-page41-tau-boundary-webclaude-20260721/node-artifacts/handler-webclaude/node-receipt.json`; timeout waiting for sentinel `<<<CLAUDE_DONE:20260721T140332Z:a9b9f1df>>>`, `raw_chars=50093`, `clean_chars=0`, `raw_contains_sentinel=false`
- Live resolution:
  - Tau live command: `uv run tau scillm-chat-review --request /tmp/pdf_oxide_integrate_gs001_20260721/artifacts/pdf_lab/live_second_pass_page41_tau_prep_20260721/page_case_0001_p0041/review_request.json --out /tmp/tau-issue122-page41-live-20260721T1422/receipt.json --response-out /tmp/tau-issue122-page41-live-20260721T1422/review_response.json --scillm-base-url http://127.0.0.1:4001 --caller-skill pdf-lab --apply --request-timeout-s 120`
  - Tau result: `status=PASS`, `provider_live=true`, `http_status=200`, `duration_seconds=20.373208`, parsed schema `pdf_lab.second_pass.review_response.v1`, `parsed_candidate_finding_count=13`, `parsed_page_status=clean`
  - pdf_oxide case receipt: `artifacts/pdf_lab/live_second_pass_page41_tau_prep_20260721/page_case_0001_p0041/tau_scillm_chat_review_receipt.json`
  - pdf_oxide review response: `artifacts/pdf_lab/live_second_pass_page41_tau_prep_20260721/page_case_0001_p0041/review_response.json`
  - pdf_oxide validation: `review_validation.ok=true`; `terminal_ledger.terminal_status=reviewed_clean`; `terminal_ledger_validation.ok=true`
  - Page audit: `artifacts/pdf_lab/page41_tau_live_review_20260721/audit_summary.json`
- Page43 follow-on live review:
  - Selection artifacts:
    - `artifacts/pdf_lab/fresh_candidate_selection_after_page41_tau_20260721/candidate_manifest_unreviewed_pages.json`
    - `artifacts/pdf_lab/fresh_candidate_selection_after_page41_tau_20260721/sampled_page_cases.json`
  - Selection result: page 43 with `13` candidates: `side_chrome=4`, `text=3`, `section_heading=3`, `list=1`, `footnote=2`.
  - Prep artifact: `artifacts/pdf_lab/live_second_pass_page43_tau_prep_20260721/page_case_0001_p0043/`
  - Tau live command: `uv run tau scillm-chat-review --request /tmp/pdf_oxide_integrate_gs001_20260721/artifacts/pdf_lab/live_second_pass_page43_tau_prep_20260721/page_case_0001_p0043/review_request.json --out /tmp/tau-issue122-page43-live-20260721T1429/receipt.json --response-out /tmp/tau-issue122-page43-live-20260721T1429/review_response.json --scillm-base-url http://127.0.0.1:4001 --caller-skill pdf-lab --apply --request-timeout-s 120`
  - Tau result: `status=PASS`, `provider_live=true`, `http_status=200`, `duration_seconds=22.64361`, parsed schema `pdf_lab.second_pass.review_response.v1`, `parsed_candidate_finding_count=13`, `parsed_page_status=clean`
  - pdf_oxide validation: `review_validation.ok=true`; `terminal_ledger.terminal_status=reviewed_clean`; `terminal_ledger_validation.ok=true`; `review_bundle_validation.ok=true`
  - Page audit: `artifacts/pdf_lab/page43_tau_live_review_20260721/audit_summary.json`
- Page40 follow-on live review:
  - Selection artifacts:
    - `artifacts/pdf_lab/fresh_candidate_selection_after_page43_tau_20260721/candidate_manifest_unreviewed_pages.json`
    - `artifacts/pdf_lab/fresh_candidate_selection_after_page43_tau_20260721/sampled_page_cases.json`
  - Selection result: page 40 with `11` candidates: `side_chrome=4`, `text=5`, `section_heading=1`, `footnote=1`.
  - Prep artifact: `artifacts/pdf_lab/live_second_pass_page40_tau_prep_20260721/page_case_0001_p0040/`
  - Tau live command: `uv run tau scillm-chat-review --request /tmp/pdf_oxide_integrate_gs001_20260721/artifacts/pdf_lab/live_second_pass_page40_tau_prep_20260721/page_case_0001_p0040/review_request.json --out /tmp/tau-issue122-page40-live-20260721T1432/receipt.json --response-out /tmp/tau-issue122-page40-live-20260721T1432/review_response.json --scillm-base-url http://127.0.0.1:4001 --caller-skill pdf-lab --apply --request-timeout-s 120`
  - Tau result: `status=PASS`, `provider_live=true`, `http_status=200`, `duration_seconds=52.409736`, parsed schema `pdf_lab.second_pass.review_response.v1`, `parsed_candidate_finding_count=11`, `parsed_page_status=clean`
  - pdf_oxide validation: `review_validation.ok=true`; `terminal_ledger.terminal_status=reviewed_clean`; `terminal_ledger_validation.ok=true`; `review_bundle_validation.ok=true`
  - Page audit: `artifacts/pdf_lab/page40_tau_live_review_20260721/audit_summary.json`
- Page39 follow-on live review:
  - Selection artifacts:
    - `artifacts/pdf_lab/fresh_candidate_selection_after_page40_tau_20260721/candidate_manifest_unreviewed_pages.json`
    - `artifacts/pdf_lab/fresh_candidate_selection_after_page40_tau_20260721/sampled_page_cases.json`
  - Selection result: page 39 with `10` candidates: `side_chrome=4`, `text=5`, `footnote=1`.
  - Initial prep artifact: `artifacts/pdf_lab/live_second_pass_page39_tau_prep_20260721/page_case_0001_p0039/`
  - Initial Tau live command timed out twice at the 120s gate:
    - `/tmp/tau-issue122-page39-live-20260721T1436/receipt.json`
    - `/tmp/tau-issue122-page39-live-retry2-20260721T1443/receipt.json`
  - Required unblock attempts:
    - Brave Search artifact: `artifacts/pdf_lab/page39_tau_timeout_unblock_20260721/brave_search_ollama_timeout.json`
    - WebGPT assess bundle: `artifacts/pdf_lab/page39_tau_timeout_unblock_20260721/webgpt_assess_bundle.md`
    - WebGPT routing failed before submission because exact tab `837359458` was not open: `BLOCKED_WEBGPT_TAB_IDENTITY_MISSING`.
  - Contract-preserving payload reduction:
    - Reduced-DPI prep artifact: `artifacts/pdf_lab/live_second_pass_page39_tau_prep_dpi110_20260721/page_case_0001_p0039/`
    - Request validation remained valid: `image_part_count=2`, `text_part_count=1`, `review_request_validation.ok=true`
    - Payload reduced from `992975` bytes to `700183` bytes.
  - Reduced-DPI Tau live command also timed out at the 120s gate:
    - `/tmp/tau-issue122-page39-live-dpi110-20260721T1448/receipt.json`
  - Circuit breaker:
    - Three matching live failures with signature `scillm_chat_review_timeout+review_response_not_parseable@120s`.
    - No further page39 live model retries should run until a focused Tau/model transport repair check exists.
    - Tau owner ticket: `https://github.com/grahama1970/tau/issues/123`
    - pdf_oxide terminal ledger is valid as `blocked_substrate`, reason `scillm_review_call_failed`.
    - Page audit: `artifacts/pdf_lab/page39_tau_timeout_blocked_20260721/audit_summary.json`
    - Review bundle: `artifacts/pdf_lab/live_second_pass_page39_tau_prep_dpi110_20260721/page_case_0001_p0039/review_bundle.zip`
- Page401 follow-on after page39 block:
  - Selection artifacts:
    - `artifacts/pdf_lab/fresh_candidate_selection_after_page39_tau_20260721/candidate_manifest_unreviewed_pages.json`
    - `artifacts/pdf_lab/fresh_candidate_selection_after_page39_tau_20260721/sampled_page_cases.json`
  - Selection result: page 401 with `31` candidates: `reference=21`, `side_chrome=4`, `text=5`, `footnote=1`.
  - Prep artifact: `artifacts/pdf_lab/live_second_pass_page401_tau_prep_20260721/page_case_0001_p0401/`
  - Request validation: `review_request_validation.ok=true`, `image_part_count=2`, `text_part_count=1`, `review_request_bytes=854883`.
  - Tau live command timed out at the 120s gate:
    - `/tmp/tau-issue122-page401-live-20260721T1457/receipt.json`
  - This matched page39's signature: `scillm_chat_review_timeout+review_response_not_parseable@120s`.
  - No page401 retry was run because the live-review family was already circuit-broken by page39.
  - Tau issue #123 was updated with page401 evidence: `https://github.com/grahama1970/tau/issues/123#issuecomment-5035599797`
  - pdf_oxide terminal ledger is valid as `blocked_substrate`, reason `scillm_review_call_failed`.
  - Page audit: `artifacts/pdf_lab/page401_tau_timeout_blocked_20260721/audit_summary.json`
  - Review bundle: `artifacts/pdf_lab/live_second_pass_page401_tau_prep_20260721/page_case_0001_p0401/review_bundle.zip`
- Tau #123 root-cause classification:
  - Tau issue: `https://github.com/grahama1970/tau/issues/123`
  - Tau commits:
    - `7009e45461dfd8fb9bb5c1fe56ebcd339941b50f`: fail-closed timeout/no-parse ambiguity classifier.
    - `915a819f97ac3f7e975b45b3afdcf809320cce78`: quota/rate-limit and route-exhaustion classifier.
  - Tau proof comments:
    - `https://github.com/grahama1970/tau/issues/123#issuecomment-5035757958`
    - `https://github.com/grahama1970/tau/issues/123#issuecomment-5035953118`
  - Tau issue state: closed with deterministic proof.
  - Imported pdf_oxide artifacts:
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/page39-diagnostic-receipt.json`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/page39-diagnostic-receipt.error.json`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/quota-canary-receipt.json`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/quota-canary-receipt.error.json`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/quota-canary-receipt.raw-response.json`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/local-text-canary-receipt.json`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/local-text-canary-receipt.error.json`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/local-text-canary-receipt.raw-response.json`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/vlm-free2-resume-canary-20260721T154322Z-receipt.json`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/vlm-free2-resume-canary-20260721T154322Z-receipt.error.json`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/brave_search_route_recovery_20260721.json`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/webgpt_route_unblock_attempt.json`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/webgpt_route_unblock_retry_stderr.txt`
    - `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/tau-audit-summary.json`
  - Live receipt boundary:
    - `mocked=false`
    - `live=true`
    - `provider_live=false`
    - short page39 timeout diagnostic: `root_cause_code=scillm_chat_review_service_unresponsive`
    - longer minimal Tau canary: `http_status=429`, `root_cause_code=scillm_chat_review_provider_quota_exhausted`
    - local-text alternate canary: `http_status=502`, `root_cause_code=scillm_chat_review_route_exhausted`
    - resumed minimal Tau `vlm-free2` canary at `2026-07-21T15:43:22Z`: `timed_out=true`, `duration_seconds=150.124672`, `root_cause_code=scillm_chat_review_service_unresponsive`
    - fresh Brave Search artifact: `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/brave_search_route_recovery_20260721.json`
    - WebGPT exact-tab escalation: `BLOCKED_WEBGPT_TAB_IDENTITY_MISSING` for tab `837359458`
    - `recommended_next_action=do not retry PDF Lab page payloads against these model routes; wait for quota/cooldown recovery or switch Tau to an approved non-exhausted model route, then require a minimal Tau canary PASS`
  - Important correction: the current blocker is no longer an ambiguous `review_response_not_parseable` page payload failure. Tau now proves the live `vlm-free2` route is provider quota/rate-limit exhausted when allowed to surface the upstream error, and a text-only alternate route is also exhausted.

## 6. Campaign Status

| Field | Value |
|-------|-------|
| `passed` | `3` for the page40, page41, and page43 live-review items |
| `failed` | `0` |
| `blocked_by_systemic_failure` | `2` for page39 and page401 Tau live-review timeout family |
| `explicitly_blocked` | `2` |
| `not_run` | `448` unreviewed pages remaining after page401 |
| Active page/checklist item | blocked pending `vlm-free2` provider quota/rate-limit recovery or approved alternate Tau model route proven by Tau-owned minimal canary PASS |
| Latest failure signature | resumed `vlm-free2=scillm_chat_review_service_unresponsive` after 150.124672s; prior `vlm-free2=scillm_chat_review_provider_quota_exhausted`; `local-text=scillm_chat_review_route_exhausted`; WebGPT exact-tab escalation blocked by `BLOCKED_WEBGPT_TAB_IDENTITY_MISSING` |

## 7. Important Correction To Claude Report

- The claimed commits `61050fc5`, `897ca8b6`, `a41ea818`, and `8bff1849` were not on `origin/main` when checked; `origin/main` was `1becfd79`.
- The code repair `61050fc5` applied cleanly to current main and is now on main as `44410852`.
- The old evidence commits did not cleanly cherry-pick:
  - `897ca8b6` conflicted on an old `artifacts/pdf-lab` directory rename split.
  - It also conflicted because `scripts/pdf_lab/cross_page_regression.py` and `scripts/pdf_lab/promotion_gate.py` are deleted on current main.
- I did not resurrect deleted scripts or force old evidence commits onto current main.

## 8. Boundaries

- `pdf_oxide` project agents must not call SciLLM or OpenCode directly.
- Tau owns model transport, OpenCode/SciLLM execution, DAG orchestration, retries, merge semantics, and receipts.
- `pdf_oxide` may prepare deterministic page evidence and Tau DAG contract inputs.
- Tau issues remain the model-route boundary:
  - `https://github.com/grahama1970/tau/issues/120`
  - `https://github.com/grahama1970/tau/issues/122`
  - `https://github.com/grahama1970/tau/issues/123`

## 9. Remaining Candidate Classes

Use the same one-candidate proof ladder, without direct SciLLM calls from `pdf_oxide`:

1. Do not select another live model-review candidate until a Tau-owned minimal canary returns PASS against `vlm-free2` or against an explicitly approved alternate Tau model route. The attempted `local-text` alternate is not currently usable.
2. Any candidate whose model/executor review is required must go through Tau DAG contracts, not direct SciLLM/OpenCode calls from this repo.
3. Criterion 6 live GitHub apply remains blocked until a valid approval receipt for mutation exists.
4. After route recovery or approved route replacement is proven, resume by selecting the next fresh current-extraction candidate after excluding page401.

Before patching the next item, produce a selection receipt with source page image/current extraction/model-review artifacts and the focused regression that will prove that one checklist item.
