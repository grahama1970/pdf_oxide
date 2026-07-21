# PDF Lab Page 16 Repeated SciLLM Review Timeout

## Current gate

Continue the PDF Lab all-candidates hardening loop after page 14 was explicitly
blocked with receipt artifacts. Run one live model-backed second-pass review for
page 16 and require a resolved page terminal state with `review_validation.ok ==
true`, or preserve a concrete blocked receipt.

## Failure receipts

Page 14 already produced two matching live substrate timeout receipts:

- First page14 run: `artifacts/pdf_lab/live_second_pass_page14_20260721T1100Z/harness_final_gate.json`
- Page14 retry: `artifacts/pdf_lab/live_second_pass_page14_retry_20260721T1115Z/harness_final_gate.json`
- Both page14 error receipts: `ReadTimeout`, `error: timed out`, endpoint `POST /v1/chat/completions`
- Page14 blocked audit: `artifacts/pdf_lab/page14_scillm_readtimeout_20260721/audit_summary.json`

Page 16 now produced the same endpoint/error signature:

- Harness output: `artifacts/pdf_lab/live_second_pass_page16_20260721T1130Z/harness_final_gate.json`
- Terminal status: `failed_closed`
- Page terminal ledger: `artifacts/pdf_lab/live_second_pass_page16_20260721T1130Z/page_cases/page_case_0001_p0016/terminal_ledger.json`
- Terminal reason: `scillm_review_call_failed`
- Error receipt: `artifacts/pdf_lab/live_second_pass_page16_20260721T1130Z/page_cases/page_case_0001_p0016/scillm_review_error.json`
- Error: `ReadTimeout`, `error: timed out`, endpoint `POST /v1/chat/completions`
- Preflight receipt: `artifacts/pdf_lab/live_second_pass_page16_20260721T1130Z/page_cases/page_case_0001_p0016/scillm_review_preflight.json`
- Preflight health: `/health/liveliness` returned 200 and `/v1/scillm/health` returned 200.

## Request shape

- Model: `local-text`
- Page14 request JSON size: 225,704 characters
- Page16 request JSON size: 963,742 characters
- Page16 candidates: 13 (`side_chrome`, `section_heading`, `text`, `list`, `table`)
- Page16 image evidence parts: original page image and candidate overlay
- Harness model timeout: 120 seconds

## Research context

`$brave-search` receipt:
`artifacts/pdf_lab/live_second_pass_page16_20260721T1130Z/unblock/brave_large_payload_readtimeout.json`

Operational reading: repeated OpenAI-compatible read timeouts after successful
health checks usually indicate live transport/model latency or payload-size
pressure, not a deterministic extraction-code defect. Page16 is much larger than
page14, so a size-bound diagnostic may be more useful than another blind retry.

## Proposed next step

Treat this as a repeated live model transport blocker unless there is a narrower
local diagnostic that preserves the current one-page proof contract without
changing `scillm` internals. Candidate diagnostics could include measuring the
model-ready payload sections, checking whether images dominate payload size, or
running the same prompt through an already-supported alternate live model route
if that does not relax the contract.

Do not propose dashboard/UI work. Do not propose editing `scillm` internals from
`pdf_oxide`. Tau owns DAG harness internals.

## Question

For the current gate, is this enough evidence to mark the active live
second-pass transport family blocked, or is there one narrower local diagnostic
that should run before blocking page16?

Return one of:

```text
PASS_CURRENT_GATE
BLOCKED_CURRENT_GATE: <one concrete blocker>
REJECTED_SCOPE_EXPANSION
```
