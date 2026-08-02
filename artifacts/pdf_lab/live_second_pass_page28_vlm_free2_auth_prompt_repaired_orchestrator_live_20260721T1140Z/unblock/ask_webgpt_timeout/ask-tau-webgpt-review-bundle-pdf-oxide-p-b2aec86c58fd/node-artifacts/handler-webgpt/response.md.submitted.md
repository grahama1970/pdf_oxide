You are one participant in a Tau-managed roundtable.
Handler: webgpt

Request:
# WebGPT Review Bundle: pdf_oxide page28 live VLM timeout after prompt-contract repair

## Current immutable-goal item
Page28 is the current one-page PDF Lab hardening item. The goal is not batch completion; it is one page/checklist item at a time with deterministic local artifacts.

## Current gate
A repaired page28 live second-pass run failed closed because the live model review call timed out before producing `review_response.json`.

Return exactly one of:

- `PASS_CURRENT_GATE` only if the existing artifacts already justify treating the page28 gate as passed.
- `BLOCKED_CURRENT_GATE: <one blocker>` if progress requires a human decision or missing external authority.
- `REPAIR_CURRENT_GATE: <bounded repair>` if the project agent should apply a narrow code/prompt/test repair next.

## Research context
- How to Prompt LLMs for Vision: Tips to Boost Accuracy: https://blog.roboflow.com/prompting-tips-for-large-language-models-with-vision/
  Below is the generationConfig dictionary, which shows how the above parameters can be used with Gemini Inference inside the payload: &quot;generationConfig&quot;: { &quot;temperature&quot;: 0, &quot;topK&quot;: 5, &quot;topP&quot;: 0.9, &quot;maxOutputTokens&quot;: 8000, &quot;stopSequences&quot;: [&quot;&lt;THE END&gt;&quot;], # The LLM stops generating when this sequence appears. &quot;thinkingConfig&quot;: { &quot;thinkingBudget&quot;: -1 # Dynamic thinking enabled } } Large Multimodal Models like Google Gemini have transformed computer vision by integrating text and image understanding. By carefully structuring prompts, leveraging contextual cues and examples, and using structured outputs or grounding search, you can significantly improve accuracy in tasks such as object detection, OCR, and visual question answering.
- Structured Outputs - Ollama: https://docs.ollama.com/capabilities/structured-outputs
  Fetch the complete documentation index at: /llms.txt · Use this file to discover all available pages before exploring further. ... Ollama’s Cloud currently does not support structured outputs. Structured outputs let you enforce a JSON schema on model responses so you can reliably extract structured data, describe images, or keep every reply consistent.
- Vision Language Model Prompt Engineering Guide for Image and Video Understanding | NVIDIA Technical Blog: https://developer.nvidia.com/blog/vision-language-model-prompt-engineering-guide-for-image-and-video-understanding/
  <strong>The VLM can also be prompted to output in a structured format such as JSON so that the response can easily be parsed and sent to a database or a notification service</strong>.
- LLM Structured Output in 2026: Stop Parsing JSON with Regex and Do It Right - DEV Community: https://dev.to/pockit_tools/llm-structured-output-in-2026-stop-parsing-json-with-regex-and-do-it-right-34pk
  When you JSON.parse() a raw LLM response, you&#x27;re making several dangerous assumptions: ... Structured output eliminates all six of these problems by constraining the model&#x27;s output at the token generation level — not after the fact. Level 1: Prompt Engineering (Unreliable) &quot;Return JSON with fields: name, email, score&quot; → Works 80-95% of the time → Fails silently on edge cases → No type guarantees Level 2: Function Calling / Tool Use (Better) Define a function schema, model &quot;calls&quot; it → Works 95-99% of the time → Schema is a hint, not a constraint → Can still produce invalid values within valid types Level 3: Native Structured Output (Best) Constrained decoding with JSON Schema → Works 100% of the time (schema-valid guaranteed) → Uses finite state machines to mask invalid tokens → Types AND values are enforced at generation time
- Best Structured Prompt Formats for LLMs, Ranked — MightyBot: https://mightybot.ai/blog/best-structured-prompt-formats-for-llms/
  It is a compact, lossless way to represent JSON-like data for LLM input by declaring repeated fields once and encoding uniform object arrays as rows. It is most useful when many records share the same schema. ... TOON can be better than JSON when the input is a uniform array of objects because it declares fields once and streams rows compactly. It is not automatically better for small, deeply nested, or irregular payloads, and it may require extra prompt instructions that reduce the token savings on short inputs.

Raw Brave artifact: `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z/unblock/brave_vlm_timeout_context.json`.

## Local proof before this failure
- Footer source type repair deterministic audit: `artifacts/pdf_lab/page28_footer_source_type_20260721/audit_summary.json`, `ok:true`.
- Prompt/preset contract regression: `pytest -q tests/test_pdf_lab_page_second_pass_dag.py -k "text_preset_semantic_subtype_contract"` -> `1 passed, 216 deselected`.
- The generated live review prompt includes all three contract strings:
  - `preset_type text is a broad review stratum`: True
  - `section_subtitle is clean-compatible`: True
  - `page_status clean requires every candidate finding status to be clean`: True

## Live authenticated run
Command family: `python scripts/pdf_lab/run_second_pass_harness.py --candidate-census-page 28 --review-mode live --patch-mode dry_run --patch-backend opencode_serve --commit-mode dry_run --model vlm-free2 --scillm-base-url http://localhost:4001 --scillm-timeout-s 120 --page-orchestrator-mode live --stop-on-nonterminal`.

Output dir: `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z`.

Auth/transport evidence:
- `/v1/scillm/auth` succeeded when the harness environment loaded `/home/graham/workspace/experiments/scillm/.env` and used `SCILLM_MASTER_KEY`.
- Page orchestrator registration succeeded: transport run `otr-46cb91a2f219`, HTTP status `200`.

## Failure signature
`scillm_review_error.json`:
```json
{
  "case_id": "page_case_0001_p0028",
  "endpoint": "POST /v1/chat/completions",
  "error": "timed out",
  "error_type": "ReadTimeout",
  "node_id": "scillm_one_shot_page_review",
  "page_number": 28,
  "preflight_artifact": "scillm_review_preflight.json",
  "schema": "pdf_lab.second_pass.substrate_error.v1"
}
```

`review_validation.json`:
```json
{
  "candidate_count": 18,
  "errors": [
    "scillm_review_call_failed"
  ],
  "expected_candidate_ids": [
    "cand:p0028:0000:side_chrome",
    "cand:p0028:0001:side_chrome",
    "cand:p0028:0002:side_chrome",
    "cand:p0028:0003:side_chrome",
    "cand:p0028:0004:section_heading",
    "cand:p0028:0005:section_heading",
    "cand:p0028:0006:text",
    "cand:p0028:0007:text",
    "cand:p0028:0008:text",
    "cand:p0028:0009:text",
    "cand:p0028:0010:list",
    "cand:p0028:0011:footnote",
    "cand:p0028:0012:footnote",
    "cand:p0028:0013:footnote",
    "cand:p0028:0014:footnote",
    "cand:p0028:0015:footnote",
    "cand:p0028:0016:footnote",
    "cand:p0028:0017:footnote"
  ],
  "ok": false,
  "page_case": {
    "case_id": "page_case_0001_p0028",
    "page_number": 28
  },
  "schema": "pdf_lab.second_pass.review_validation.v1",
  "seen_candidate_ids": []
}
```

`terminal_ledger.json`:
```json
{
  "allowed_terminal_statuses": [
    "blocked_substrate",
    "human_needed",
    "patched_confirmed",
    "rejected_with_proof",
    "reviewed_clean",
    "still_open"
  ],
  "case_id": "page_case_0001_p0028",
  "commit_sha": null,
  "evidence_artifacts": [
    "state.json",
    "sampled_candidate_manifest.json",
    "page_before.json",
    "page_before.png",
    "page_candidates.png",
    "selected_candidates.json",
    "candidate_presets.json",
    "review_request.json",
    "review_request_validation.json",
    "scillm_orchestrator_page_dag_spec.json",
    "scillm_orchestrator_page_dag_spec_validation.json",
    "scillm_orchestrator_page_submission.json",
    "scillm_orchestrator_page_submission_validation.json",
    "review_validation.json",
    "scillm_review_preflight.json",
    "scillm_review_error.json",
    "scillm_page_orchestrator_run_request.json",
    "scillm_page_orchestrator_run_validation.json",
    "scillm_page_orchestrator_run_receipt.json",
    "review.html",
    "terminal_ledger_validation.json"
  ],
  "page_number": 28,
  "reason": "scillm_review_call_failed",
  "schema": "pdf_lab.second_pass.page_terminal_ledger.v1",
  "terminal_status": "blocked_substrate"
}
```

## Request shape
- model: `vlm-free2`
- model_supports_images: `True`
- image_evidence_in_payload: `True`
- image parts embedded: `2`
- prompt chars: `39236`
- candidate_count: `18`
- expected candidate ids: `18`

## Constraints
- Do not weaken `validate_review_response`.
- Do not modify Tau or SciLLM internals from pdf_oxide.
- Do not broaden into dashboards, batch retries, or Criterion 6 GitHub apply.
- Preserve the one-case evidence contract: original page image, annotated candidate image, exact extracted JSON, exact candidate presets, model-ready prompt payload, response/validation when available, copyable ZIP, HTML review artifact and CDP proof after a successful live review.
- The next retry must be one page28 live gate only.

## Exact question
What is the smallest bounded pdf_oxide-side repair for this live VLM ReadTimeout? Should the agent reduce the model payload by splitting page28 candidate review into smaller bounded live calls and joining validated findings, change the prompt/request artifact shape, adjust timeout within the stated 120s target, or choose a different existing model route? Name the exact file boundary, test command, and live acceptance artifact.

Return a concise position with these Markdown headings:
## Position
## Evidence
## Uncertainties
## Blockers

---

Completion contract for browser automation:

At the very end of your final answer, print exactly:

<<<WEBGPT_DONE:20260721T114222Z:34f5e7d3>>>

Do not print anything after that marker.
