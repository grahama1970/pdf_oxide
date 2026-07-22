# PDF Lab Page42 SciLLM Auth Unblock Bundle

## Current Gate

Run exactly one fresh model-backed PDF Lab second-pass review case selected from current extraction evidence:

- Fresh manifest: `artifacts/pdf_lab/fresh_candidate_selection_20260721T0721Z/candidate_manifest.json`
- Fresh sample: `artifacts/pdf_lab/fresh_candidate_selection_20260721T0721Z/sampled_page_cases.json`
- Selected page: `page_0042`
- Live harness output: `artifacts/pdf_lab/live_second_pass_page42_20260721T0724Z/`
- Mutation policy: no code patching; `--patch-mode dry_run --commit-mode dry_run`

## Blocking Defect

The live page42 harness failed closed before model review:

```text
terminal_status: failed_closed
page terminal_status: blocked_substrate
reason: scillm_review_call_failed
```

Exact error receipt:

```json
{
  "endpoint": "POST /v1/chat/completions",
  "error": "scillm review preflight failed: ['missing-caller chat contract did not return caller_skill_required', \"scillm preflight failed: HTTPStatusError: Client error '401 Unauthorized' for url 'http://localhost:4001/v1/scillm/health'\\nFor more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401\"]",
  "error_type": "RuntimeError",
  "node_id": "scillm_one_shot_page_review"
}
```

The preflight artifact shows:

```json
{
  "base_url": "http://localhost:4001",
  "caller_skill": "pdf-lab",
  "errors": [
    "missing-caller chat contract did not return caller_skill_required",
    "scillm preflight failed: HTTPStatusError: Client error '401 Unauthorized' for url 'http://localhost:4001/v1/scillm/health'"
  ],
  "checks": [
    {
      "path": "/v1/scillm/health",
      "http_status": 401,
      "payload": {
        "error": {
          "advice": "Auth failed. Use 'Authorization: Bearer sk-dev-proxy-123' header. Check that the scillm proxy is running on :4001.",
          "message": "Invalid API key"
        }
      }
    }
  ]
}
```

## Proposed Next Command

Retry the same one-page live harness with the auth token passed through the harness flag, without touching SciLLM internals:

```bash
python scripts/pdf_lab/run_second_pass_harness.py \
  --pdf /mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf \
  --ledger python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json \
  --apply-mode release \
  --out artifacts/pdf_lab/live_second_pass_page42_20260721T0730Z \
  --candidate-census-page 42 \
  --sample-size 1 \
  --seed 46 \
  --review-mode live \
  --patch-mode dry_run \
  --patch-backend opencode_serve \
  --commit-mode dry_run \
  --scillm-base-url http://localhost:4001 \
  --scillm-auth-token sk-dev-proxy-123 \
  --scillm-timeout-s 120 \
  --page-orchestrator-mode dry_run \
  --patch-prompt-profile plan_only \
  --repair-strategy single \
  --candidate-census-timeout-s 120 \
  --candidate-page-timeout-s 90 \
  --page-extract-timeout-s 90 \
  --caller-skill pdf-lab \
  --stop-on-nonterminal
```

## Research context

Brave Search query:

```text
OpenAI compatible proxy Authorization Bearer header 401 X-Caller-Skill local proxy
```

Distilled result:

- OpenAI-compatible proxy documentation and issue reports confirm the normal auth convention is `Authorization: Bearer <key>`.
- The local SciLLM proxy itself returned the actionable expected header: `Authorization: Bearer sk-dev-proxy-123`.
- This is a caller transport/config retry, not a reason to edit SciLLM internals.

Raw search receipt:

`artifacts/pdf_lab/live_second_pass_page42_20260721T0724Z/unblock/brave_scillm_auth_search.json`

## Question For WebGPT

DIAGNOSIS: Is the proposed retry with `--scillm-auth-token sk-dev-proxy-123` the correct next step for this current gate, or is there another local artifact/command that should be checked first?

Return exactly one ruling:

```text
PASS_CURRENT_GATE
BLOCKED_CURRENT_GATE: <one concrete blocker>
REJECTED_SCOPE_EXPANSION
```

Do not propose SciLLM internal edits. Do not expand into architecture or DAG harness redesign.
