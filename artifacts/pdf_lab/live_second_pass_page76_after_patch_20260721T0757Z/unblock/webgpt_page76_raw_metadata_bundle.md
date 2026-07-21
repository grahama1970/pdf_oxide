# PDF Oxide Page76 Second-Pass Unblock Bundle

## Current gate
One live model-backed second-pass repair lifecycle for NIST SP 800-53r5 page 76 must reach a terminal status other than `still_open`/`blocked` after deterministic local patching.

## Repository state
Repo: /tmp/pdf_oxide_page45b_1784600709
Current branch: codex/page45-remaining-second-item
Last pushed main before this page76 patch: f1bbb263aec4d7a192dd2171acf2ee110e8df22a
Uncommitted current patch touches only:
- python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json
- tests/test_nist_page76_label_spacing_roles.py
- generated artifacts under artifacts/pdf_lab/forced_candidate_selection_page76_20260721T0753Z and live_second_pass_page76*

## Research context
A Brave Search pre-step was run and saved at:
artifacts/pdf_lab/live_second_pass_page76_after_patch_20260721T0757Z/brave_raw_vs_normalized_review_search.json
Distilled finding: external/general PDF extraction guidance separates raw extraction metadata from normalized consumer text. A reviewer should judge the consumer-facing normalized text unless the contract explicitly says raw metadata is part of the output contract.

## What happened
1. Live page76 model review before patch returned `still_open`.
2. It reported double spaces after `Discussion:` / `Related Controls:` and footer raw order `CHAPTER THREE   49 PAGE` instead of normalized rendered order `CHAPTER THREE PAGE 49`.
3. I added a focused regression, confirmed it failed, and patched the NIST ledger rules:
   - widen `Discussion:` and `Related Controls:` semantic-role bbox guards from x<0.22 to x<0.25;
   - normalize label text from `Label:  body` to `Label: body` using regex capture fields;
   - accept footer shape `CHAPTER THREE PAGE 49` as page chrome.
4. Deterministic tests now pass:
   - `pytest -q tests/test_nist_page76_label_spacing_roles.py` => 2 passed
   - `pytest -q tests/test_nist_page76_label_spacing_roles.py tests/test_nist_page45_discussion_role.py tests/test_nist_page45_related_controls.py tests/test_pdf_lab_second_pass_candidate_manifest.py` => 15 passed
5. Live page76 model review after patch still returned `still_open`.

## Key evidence paths
Pre-patch live review:
- artifacts/pdf_lab/live_second_pass_page76_20260721T0753Z/page_case_0002_p0076/review_response.json
- artifacts/pdf_lab/live_second_pass_page76_20260721T0753Z/page_case_0002_p0076/page_before.json

Post-patch live review:
- artifacts/pdf_lab/live_second_pass_page76_after_patch_20260721T0757Z/page_case_0002_p0076/review_response.json
- artifacts/pdf_lab/live_second_pass_page76_after_patch_20260721T0757Z/page_case_0002_p0076/page_before.json
- artifacts/pdf_lab/live_second_pass_page76_after_patch_20260721T0757Z/page_case_0002_p0076/review_request.json
- artifacts/pdf_lab/live_second_pass_page76_after_patch_20260721T0757Z/page_case_0002_p0076/terminal_ledger.json

## Post-patch local extraction truth
From `page_before.json` after the patch:
- `actual:p76:block:2`: top-level `text` is `CHAPTER THREE PAGE 49`, `semantic_role` is `page_chrome`, but nested `raw.text` remains `CHAPTER THREE   49 PAGE`.
- `actual:p76:block:5`: top-level `text` is `Related Controls: AU-2, AU-6, AU-12, AU-14.`, `semantic_role` is `related_controls`, but nested `raw.text` remains `Related Controls:  AU-2, AU-6, AU-12, AU-14.`.
- `Discussion:` and other `Related Controls:` blocks have normalized top-level text and semantic roles.
- Several blocks still have top-level and raw `is_bold: true`; the model says the rendered page is not bold.

## Current uncertainty
The live model is judging nested `raw.text` and raw/top-level `is_bold` fields in `page_before.json`, not just the normalized top-level consumer extraction. The page76 repair should not spiral into raw metadata mutation if the one-page review contract should focus on normalized output. But if `is_bold` is considered part of the extracted JSON contract, then the next local patch should target font-weight classification for body/list blocks with TT0/TT2 on page76-like NIST control body text.

## Question for WebGPT
Return a concise advisory ruling for this exact gate:
1. Should the next patch remove/suppress `raw` metadata from the model-ready review payload, or instruct the reviewer to judge only top-level normalized fields unless raw metadata is explicitly named?
2. Or should the next patch repair `is_bold` extraction for these page76 body/list blocks?
3. What is the minimal deterministic local test to add next?

Do not propose dashboards, broad architecture, or batch campaigns. Answer only this one page76 gate.
