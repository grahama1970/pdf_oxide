# PDF Lab Page3 Transport Auth Unblock Bundle

## Current gate

Run exactly one live model-backed second-pass page case for NIST SP 800-53r5 page 3, preserving the PDF Lab evidence contract. Do not expand into dashboards, broad batch review, SciLLM internals, or Tau DAG internals.

## Local evidence

- Repository: `grahama1970/pdf_oxide`
- Current commit on `origin/main`: `9a4366ecbcd72a8896ca73865b968b0334f8fb45`
- Worktree: `/tmp/pdf_oxide_page45b_1784600709`
- Harness command family: `scripts/pdf_lab/run_second_pass_harness.py`
- Page3 failed run: `artifacts/pdf_lab/live_second_pass_page3_20260721T0842Z`
- Terminal ledger: `page_cases/page_case_0001_p0003/terminal_ledger.json`
- Terminal status: `blocked_substrate`
- Terminal reason: `page_orchestrator_registration_failed`
- Failing endpoint: `POST /v1/scillm/opencode/transport/runs`
- Error artifact: `page_cases/page_case_0001_p0003/scillm_page_orchestrator_run_error.json`
- Error: `401 Unauthorized`
- Registration validation: `page_cases/page_case_0001_p0003/scillm_page_orchestrator_run_validation.json`
- Registration validation error: `page orchestrator run receipt missing`
- Review bundle validation: `page_cases/page_case_0001_p0003/review_bundle_validation.json`, `ok: true`
- Orchestrator submission validation: `page_cases/page_case_0001_p0003/scillm_orchestrator_page_submission_validation.json`, `ok: true`

## Research context

Brave search receipt is saved at:

`artifacts/pdf_lab/live_second_pass_page3_20260721T0842Z/unblock/brave_transport_auth_401_optional_bearer.json`

Local auth probe showed the transport endpoint returned request-schema validation (`422`) for POST `/v1/scillm/opencode/transport/runs` when called without an explicit bearer token or with current proxy environment service keys, but the page3 harness run returned `401` when passed a stale fallback token through `--scillm-auth-token`.

## Proposed local next move

Rerun the same page3 command without `--scillm-auth-token`, so the client does not send a stale explicit bearer token. Preserve the failed auth receipt and the rerun receipt. Do not modify SciLLM internals from this repository.

## Question for WebGPT

Given the evidence above, is the next local action correctly scoped as a single rerun of the page3 harness without the stale explicit auth token, or is there a pdf_oxide-side code change that should happen before rerunning?

Return a concise assessment with one of:

- `PASS_CURRENT_GATE`
- `BLOCKED_CURRENT_GATE: <one concrete blocker>`
- `REJECTED_SCOPE_EXPANSION`
