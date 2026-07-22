# PDF Lab Page 9 Review-Validation Gate

## Current gate

Make one live model-backed second-pass page review return a parseable and validator-valid `pdf_lab.second_pass.review_response.v1` response under the existing one-case PDF Lab harness.

## Failure receipt

- Harness output: `artifacts/pdf_lab/live_second_pass_page9_20260721T0954Z/harness_final_gate.json`
- Terminal status: `failed_closed`
- Page terminal ledger: `artifacts/pdf_lab/live_second_pass_page9_20260721T0954Z/page_cases/page_case_0001_p0009/terminal_ledger.json`
- Terminal reason: `review_validation_failed`
- Validator receipt: `artifacts/pdf_lab/live_second_pass_page9_20260721T0954Z/page_cases/page_case_0001_p0009/review_validation.json`
- Validator error: `candidate_findings[4].suggested_fix_surface must be none for clean findings`
- Model response: `artifacts/pdf_lab/live_second_pass_page9_20260721T0954Z/page_cases/page_case_0001_p0009/review_response.json`

## What happened

The live model returned:

- `page_status: clean`
- all `candidate_findings[].status: clean`
- one clean finding, `cand:p0009:0004:text`, with `suggested_fix_surface: "Consider refining semantic role to 'heading' if more granularity is desired."`

The validator rejects that because clean findings must not propose a fix surface. The extraction was not judged defective by the model; this is a prompt/schema contract failure.

## Research context

`$brave-search` receipt: `artifacts/pdf_lab/live_second_pass_page9_20260721T0954Z/unblock/brave_clean_finding_null_suggested_fix_surface.json`

The relevant general JSON-schema point from the search results is that nullable fields must be explicitly allowed and documented as null. That supports making the prompt contract explicit: `suggested_fix_surface` may be a string for defect/unsure/substrate-blocked findings, but must be `null` or `"none"` for clean findings.

## Local code evidence

Current prompt builder in `scripts/pdf_lab/run_page_second_pass_dag.py` describes:

```text
"evidence": "...", "rationale": "...", "suggested_fix_surface": "..."}
```

Current validator in `scripts/pdf_lab/run_page_second_pass_dag.py` rejects clean findings with non-none `suggested_fix_surface`.

## Proposed narrow fix

Patch only the PDF Lab prompt/schema contract and focused tests:

1. Update the live review prompt schema text so `suggested_fix_surface` is `null|"none"|non-empty string`.
2. Add explicit response rules:
   - for `status == "clean"`, `suggested_fix_surface` must be `null` or `"none"`;
   - for non-clean statuses, it must identify the concrete fix surface or blocker surface.
3. Update `required_response_schema` metadata if needed so the model-ready payload carries the same invariant.
4. Add or update a focused test proving the rendered review request includes the clean/null invariant.
5. Rerun exactly page 9 live and require `review_validation.ok == true` and `harness_final_gate.ok == true`.

## Question

Is the proposed prompt/schema-contract patch the correct current-gate fix, or is there a narrower local fix that better preserves the existing validator contract?

Return one of:

```text
PASS_CURRENT_GATE
BLOCKED_CURRENT_GATE: <one concrete blocker>
REJECTED_SCOPE_EXPANSION
```
