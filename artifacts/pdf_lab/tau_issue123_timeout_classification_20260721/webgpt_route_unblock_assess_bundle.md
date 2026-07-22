# PDF Oxide Route-Unblock Assessment Bundle

## Current Gate

Determine whether the PDF Lab all-candidates loop has any legitimate
self-unblock path inside `pdf_oxide` after Tau-owned live model canaries failed,
or whether the active gate is blocked on Tau/SciLLM provider route recovery.

## Immutable Goal

Harden all PDF Lab page candidates one page/checklist item at a time across the
active candidate queue, preserving visual/current extraction evidence, using
Tau-owned model/executor transport only, committing and pushing task-relevant
proof, and advancing only when the current candidate is proven or explicitly
blocked with receipt artifacts.

## Hard Constraints

- `pdf_oxide` project agents must not call SciLLM or OpenCode directly.
- Tau owns model transport, DAG/executor orchestration, retries, and receipts.
- Do not recommend implementing chunking, retry, model fallback, or route repair
  inside `pdf_oxide`.
- WebGPT advice is advisory only; deterministic local artifacts decide status.
- Criterion 6 live GitHub apply is unrelated and remains blocked without a
  mutation approval receipt.

## Evidence

- `pdf_oxide` main: `29b4de61c4df26774d2b40c533e7f34459da4de0`
- Tau main: `915a819f97ac3f7e975b45b3afdcf809320cce78`
- `artifacts/pdf_lab/tau_issue123_timeout_classification_20260721/quota-canary-receipt.json`:
  `mocked=false`, `live=true`, `provider_live=false`, `http_status=429`,
  `root_cause_code=scillm_chat_review_provider_quota_exhausted`.
- `/tmp/tau-issue123-local-text-canary-receipt.json`:
  `mocked=false`, `live=true`, `provider_live=false`, `http_status=502`,
  `root_cause_code=scillm_chat_review_route_exhausted`.
- Focused Tau proof before commit:
  `uv run pytest -q tests/test_scillm_chat_review.py tests/test_coding_worker_adapters.py -k "scillm_chat_review or scillm_worker_launch"`
  -> `37 passed, 90 deselected`.
- Focused pdf_oxide proof after import:
  `uv run pytest -q tests/test_pdf_lab_page_second_pass_dag.py -k "validate_review_response or terminal_ledger"`
  -> `72 passed, 145 deselected`.
- Brave Search was run for rate-limit/route-exhaustion context:
  `python /home/graham/workspace/experiments/agent-skills/skills/brave-search/brave_search.py web "OpenAI compatible proxy 502 all groups exhausted circuit breaker quota rate limit" --count 5 --json`.

## Exact Question

Given the source/proof paths attached through provenance and the local-text
canary result quoted above, return one gate ruling:

```text
DIAGNOSIS:
PASS_CURRENT_GATE
```

or

```text
DIAGNOSIS:
BLOCKED_CURRENT_GATE: <one concrete blocker>
```

or

```text
DIAGNOSIS:
REJECTED_SCOPE_EXPANSION
```

Focus only on whether `pdf_oxide` should continue selecting/rerunning PDF Lab
page candidates now, or stop the live model-review family until Tau/SciLLM has
a passing canary or an explicitly approved alternate route.
