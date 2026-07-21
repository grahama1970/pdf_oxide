# PDF Lab Page 17 Systemic SciLLM ReadTimeout Gate

## Current gate

Decide whether the live `local-text` second-pass review family must stop under
the project circuit breaker after three representative `ReadTimeout` failures.

## Local evidence

The current immutable goal says to advance only when each selected page candidate
is proven or explicitly blocked with receipts. The global project instructions
also require a family-level circuit breaker: after three cases in one family
with the same failed gate/error/root cause, stop that family, preserve the three
representative receipts, and mark untouched cases in that family
`blocked_by_systemic_failure`.

Representative failures:

1. Page 14
   - Audit: `artifacts/pdf_lab/page14_scillm_readtimeout_20260721/audit_summary.json`
   - Error: `ReadTimeout`, endpoint `POST /v1/chat/completions`
   - Attempts: 2 live attempts

2. Page 16
   - Audit: `artifacts/pdf_lab/page16_scillm_readtimeout_20260721/audit_summary.json`
   - Error: `ReadTimeout`, endpoint `POST /v1/chat/completions`
   - Attempts: 3 live attempts
   - Payload diagnostic: reduced request JSON from 963,742 to 309,838 chars
     using DPI 72; still timed out
   - Prior WebGPT assessment: `PASS_CURRENT_GATE` for accepting page16 blocked

3. Page 17
   - Harness output: `artifacts/pdf_lab/live_second_pass_page17_dpi72_20260721T1010Z/harness_final_gate.json`
   - Terminal ledger: `artifacts/pdf_lab/live_second_pass_page17_dpi72_20260721T1010Z/page_cases/page_case_0001_p0017/terminal_ledger.json`
   - Error: `ReadTimeout`, endpoint `POST /v1/chat/completions`
   - Request JSON size: 139,076 chars
   - Candidate count: 6
   - Review validation: no candidate findings returned because no model response

## Research context

Brave receipt:
`artifacts/pdf_lab/live_second_pass_page17_dpi72_20260721T1010Z/unblock/brave_systemic_local_text_readtimeout.json`

The relevant point is that read timeouts after successful health checks indicate
that a request was sent and the client timed out while waiting for response data,
not that PDF extraction changed or that the page evidence bundle was malformed.

## Proposed disposition

Stop the live `local-text` review family now. Commit page17 as
`blocked_substrate` and record that the remaining untouched active candidates are
`blocked_by_systemic_failure` for this family until the scillm/tau transport path
can return parseable schema JSON under the 120-second gate.

Do not propose UI/dashboard work. Do not propose editing scillm internals from
`pdf_oxide`. Do not recommend more blind local-text retries.

## Question

Is the circuit-breaker disposition above the correct current gate outcome?

Return one of:

```text
PASS_CURRENT_GATE
BLOCKED_CURRENT_GATE: <one concrete blocker>
REJECTED_SCOPE_EXPANSION
```
