# PDF Lab Page39 Tau Timeout Gate Review

## Current gate

Assess one live PDF Lab second-pass review failure for page39 and return exactly one ruling:

- `PASS_CURRENT_GATE` only if the next action can safely continue the existing one-page loop without changing the evidence contract.
- `BLOCKED_CURRENT_GATE: <one concrete blocker>` if the page39 timeout means the current gate cannot proceed without a local code/config fix.
- `REJECTED_SCOPE_EXPANSION` if the proposed next move expands beyond page39/Tau transport diagnosis.

Do not propose architecture, dashboards, batch review, direct SciLLM calls from pdf_oxide, or a broader prompt rewrite. The allowed decision is limited to the next page39 action.

## Immutable goal

Harden all PDF Lab page candidates one page/checklist item at a time across the active candidate queue, using the GOAL.md proof ladder: select one candidate, preserve human/visual evidence, create or update the focused regression before patching, run bounded model/executor work only through Tau when useful, perform deterministic project-agent audit, commit and push task-relevant code/artifacts, then advance only when the current candidate is proven or explicitly blocked with receipt artifacts.

## Non-negotiable constraints

- pdf_oxide must not call SciLLM directly. Tau owns model transport.
- One page case at a time.
- Preserve original page image, annotated candidate image, exact extracted JSON, exact candidate presets, prompt payload, response, validation, HTML review, and ZIP bundle.
- Do not claim semantic correctness from mocks.
- Do not advance page39 unless validation produces a non-blocked terminal result or a receipt-backed blocker is recorded.

## Local evidence

Repo: `/tmp/pdf_oxide_integrate_gs001_20260721`

Latest pushed pdf_oxide main before page39:

```text
0f690bd1356a744199fdc20bc6babccae39d4d58 refs/heads/main
```

Already successful live Tau-reviewed pages in this loop:

```text
page41: Tau receipt PASS, provider_live=true, parsed_page_status=clean, 13 findings
page43: Tau receipt PASS, provider_live=true, parsed_page_status=clean, 13 findings
page40: Tau receipt PASS, provider_live=true, parsed_page_status=clean, 11 findings
```

Active page39 artifacts:

```text
artifacts/pdf_lab/fresh_candidate_selection_after_page40_tau_20260721/
artifacts/pdf_lab/live_second_pass_page39_tau_prep_20260721/page_case_0001_p0039/
```

Page39 candidate summary:

```json
{
  "case_id": "page_case_0001_p0039",
  "page_number": 39,
  "candidate_count": 10,
  "preset_counts": [
    {"preset": "footnote", "count": 1},
    {"preset": "side_chrome", "count": 4},
    {"preset": "text", "count": 5}
  ]
}
```

Page39 request validation:

```json
{
  "ok": true,
  "errors": [],
  "image_part_count": 2,
  "text_part_count": 1,
  "scillm_metadata": {
    "batch_id": "pdf-lab-second-pass",
    "case_id": "page_case_0001_p0039",
    "item_id": "page_case_0001_p0039:ef941ce8b1d46ef7",
    "request_sha256": "ef941ce8b1d46ef7dcd2187b7dc88a4cc2e17dd68121c677c4832dba42d43704"
  }
}
```

Page39 Tau live command:

```bash
uv run tau scillm-chat-review \
  --request /tmp/pdf_oxide_integrate_gs001_20260721/artifacts/pdf_lab/live_second_pass_page39_tau_prep_20260721/page_case_0001_p0039/review_request.json \
  --out /tmp/tau-issue122-page39-live-20260721T1436/receipt.json \
  --response-out /tmp/tau-issue122-page39-live-20260721T1436/review_response.json \
  --scillm-base-url http://127.0.0.1:4001 \
  --caller-skill pdf-lab \
  --apply \
  --request-timeout-s 120
```

Page39 Tau live receipt:

```json
{
  "schema": "tau.scillm_chat_review_receipt.v1",
  "status": "BLOCKED",
  "ok": false,
  "live": true,
  "provider_live": false,
  "timed_out": true,
  "http_status": null,
  "duration_seconds": 120.110561,
  "request_timeout_s": 120,
  "review_request_bytes": 992975,
  "model": "vlm-free2",
  "alert_codes": [
    "scillm_chat_review_timeout",
    "review_response_not_parseable"
  ],
  "raw_response_path": null,
  "raw_response_bytes": null
}
```

Receipt files:

```text
/tmp/tau-issue122-page39-live-20260721T1436/receipt.json
/tmp/tau-issue122-page39-live-20260721T1436/receipt.error.json
```

Error receipt:

```json
{
  "status": "TIMEOUT",
  "timed_out": true,
  "http_status": null,
  "error": "timed out",
  "body_excerpt": ""
}
```

## Research context

Brave Search query:

```text
Ollama OpenAI compatible chat completions vision base64 image timeout response_format json_object
```

Top useful results:

- Ollama official OpenAI compatibility docs show `/v1/chat/completions` accepts vision messages using OpenAI-style `image_url` parts with base64 data URLs.
- Ollama official docs and blog document OpenAI-compatible chat completions; the request shape is not obviously invalid solely because it uses base64 image data.
- LiteLLM documentation notes Ollama JSON mode uses provider-specific JSON formatting, so strict JSON response-format behavior may vary by adapter.

## Exact question

Given the evidence above, what is the next correct page39 action inside the existing PDF Lab/Tau proof ladder?

Choose one and justify briefly:

1. Retry the same page39 Tau call once unchanged because this is a single live timeout after three successful adjacent pages.
2. Reduce only non-contract-critical payload size inside pdf_oxide while preserving the one-case evidence contract, then retry through Tau.
3. Treat page39 as Tau/model transport blocked and preserve the blocked terminal receipt without retrying.

Return the ruling first, then a concise reason and the exact next local artifact/command that should be produced.
