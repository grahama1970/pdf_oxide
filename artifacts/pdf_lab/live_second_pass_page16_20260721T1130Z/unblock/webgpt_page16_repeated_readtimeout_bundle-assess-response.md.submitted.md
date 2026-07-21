## GOAL LOCK - read first, obey throughout
Work on ONLY the single current gate / goal stated in this request. You are
FORBIDDEN from drifting into easier, adjacent, or tangential work - no unrelated
refactors, renames, new tooling, extra features, unrequested tests, or broader
architecture - none of which close the stated gate. If the stated gate is
unclear, out of scope, or blocked, say so and stop; do NOT substitute a
different, easier problem to look productive.

## Authoritative source provenance
Use the pushed repository state below as the only source of truth. Clone it and check out the exact detached commit before inspecting the declared paths.

```bash
git clone --filter=blob:none https://github.com/grahama1970/pdf_oxide.git webgpt-source
git -C webgpt-source checkout --detach 95c2215de0a1b84bd75f71d3cf320936b3212f9f
```

```json
{
  "schema": "webgpt.source_provenance.v1",
  "repository_url": "https://github.com/grahama1970/pdf_oxide.git",
  "branch": "codex/page45-remaining-second-item",
  "upstream": "origin/main",
  "commit_sha": "95c2215de0a1b84bd75f71d3cf320936b3212f9f",
  "source_paths": [
    "scripts/pdf_lab/run_second_pass_harness.py",
    "tests/test_pdf_lab_second_pass_harness.py",
    "GOAL.md",
    "artifacts/pdf_lab/page16_scillm_readtimeout_20260721/audit_summary.json"
  ],
  "proof_cwd": "."
}
```

## Research directive
Before answering, use your own web search to research current, authoritative
sources for this problem, and cite the source URLs you relied on. The bundle may
also include a "## Research context" section the project agent gathered via
brave-search; treat it as a starting point, not a limit.

## Output contract: ASSESS
Diagnose where the project agent is blocked or spiraling. Do NOT write code.
Return, in order:
- DIAGNOSIS: <root cause of the block or spiral>
- EVIDENCE: <what in the bundle/research supports it>
- CURRENT_GATE: <the one gate that must be closed next>
- NEXT_STEP: <single concrete action>
End with exactly one ruling line:
PASS_CURRENT_GATE | BLOCKED_CURRENT_GATE: <one concrete blocker> | REJECTED_SCOPE_EXPANSION

---

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


---

## GOAL LOCK - final check (this is the last instruction; it wins)
Before you send your answer, re-read the stated gate/goal above and verify EVERY
line of your response directly serves it. Delete anything that is a side-quest,
nice-to-have, or adjacent improvement. Do not expand scope. Return only what the
output contract requires. If you cannot make real progress on the stated gate,
return the contract's block/ruling instead of solving an easier, unrelated
problem.

---

Completion contract for browser automation:

At the very end of your final answer, print exactly:

<<<WEBGPT_DONE:20260721T095501Z:5c230e4f>>>

Do not print anything after that marker.
