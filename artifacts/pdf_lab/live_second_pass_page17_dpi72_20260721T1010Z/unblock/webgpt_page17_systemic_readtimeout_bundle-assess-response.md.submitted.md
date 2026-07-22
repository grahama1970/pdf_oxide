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
git -C webgpt-source checkout --detach 1f9fe12f8c0d972b099a22c7ebb463fac85f5201
```

```json
{
  "schema": "webgpt.source_provenance.v1",
  "repository_url": "https://github.com/grahama1970/pdf_oxide.git",
  "branch": "codex/page45-remaining-second-item",
  "upstream": "origin/main",
  "commit_sha": "1f9fe12f8c0d972b099a22c7ebb463fac85f5201",
  "source_paths": [
    "GOAL.md",
    "artifacts/pdf_lab/page14_scillm_readtimeout_20260721/audit_summary.json",
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

<<<WEBGPT_DONE:20260721T100256Z:e50e07a9>>>

Do not print anything after that marker.
