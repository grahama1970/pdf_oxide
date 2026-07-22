# PDF Lab Page 14 SciLLM Review Timeout

## Current gate

Run one live model-backed second-pass review for page 14 and require the page terminal state to be resolved with `review_validation.ok == true`.

## Failure receipt

- Harness output: `artifacts/pdf_lab/live_second_pass_page14_20260721T1100Z/harness_final_gate.json`
- Terminal status: `failed_closed`
- Page terminal ledger: `artifacts/pdf_lab/live_second_pass_page14_20260721T1100Z/page_cases/page_case_0001_p0014/terminal_ledger.json`
- Terminal reason: `scillm_review_call_failed`
- Error receipt: `artifacts/pdf_lab/live_second_pass_page14_20260721T1100Z/page_cases/page_case_0001_p0014/scillm_review_error.json`
- Error: `ReadTimeout`, `error: timed out`, endpoint `POST /v1/chat/completions`
- Preflight receipt: `artifacts/pdf_lab/live_second_pass_page14_20260721T1100Z/page_cases/page_case_0001_p0014/scillm_review_preflight.json`
- Preflight health: `/health/liveliness` returned 200 and `/v1/scillm/health` returned 200.

## Request shape

- Model: `local-text`
- Prompt text length: 12,476 characters
- Image evidence parts: 2
- Request sha256: `b0dd4bd8a93c53ab9bfe7b4e642f66cf6cb5ee1fd5d9f18fc8bc97f65d1e3020`
- Harness model timeout: 120 seconds

## Research context

`$brave-search` receipt: `artifacts/pdf_lab/live_second_pass_page14_20260721T1100Z/unblock/brave_scillm_chat_readtimeout.json`

The relevant operational point is that HTTP read timeouts on live model calls can be transient even when health checks are green, so one exact retry under the same timeout is a reasonable discriminator before changing code.

## Proposed next step

Rerun the exact page 14 one-case live harness under the same 120s model timeout. If it passes, record the first timeout and passing retry as evidence. If it times out again, treat page 14 as a repeated substrate blocker and do not alter `pdf_oxide` extraction code.

## Question

Is the exact same-timeout page 14 retry the correct current-gate next step, or is there a narrower local diagnostic that should run first?

Return one of:

```text
PASS_CURRENT_GATE
BLOCKED_CURRENT_GATE: <one concrete blocker>
REJECTED_SCOPE_EXPANSION
```
