Sequential fallback receipts after concurrent `$ask compete` failed:

Artifact directory:

`/home/graham/workspace/experiments/pdf_oxide/artifacts/pdf_lab/ask_competition/page456_visual_creator_reviewer_20260726`

Direct WebGPT:

- Command used `surf webgpt.submit` with explicit `--tab-id 837361214` and expected URL.
- Preflight passed when `--expect-url https://chatgpt.com/c/6a65f03c-2c08-83ea-a9c2-e51934bed5f8` was provided.
- Receipt: `webgpt_round1_direct_response.receipt.json`
- Meta: `webgpt_round1_direct_response.meta.json`
- The prompt was submitted and raw output contains the sentinel.
- Surf still returned failure:

```text
failure: missing_controlled_tab_id_or_contaminated_clean_output
proof_status: unknown_failure
response_proof_status: response_unproven
submitted_to_chatgpt: true
raw_contains_sentinel: true
clean_contains_sentinel: false
```

Direct Claude:

- Command used `surf claude.submit --tab-id 837361216 --url https://claude.ai/new`.
- Meta: `webclaude_round1_direct_response.meta.json`
- Raw output is 0 bytes.
- Failure:

```text
Command '['/home/graham/workspace/experiments/agent-skills/skills/surf/run.sh', 'read', '--tab-id', '837361216']' timed out after 60 seconds
```

Direct Grok:

- Command used `surf grok.submit --tab-id 837361217 --url https://grok.com/`.
- Meta: `webgrok_round1_direct_response.meta.json`
- Raw output is 0 bytes.
- Failure:

```text
Error: Unknown message type: GROK_EVALUATE
```

Net impact:

For PDF Lab competition use, the official concurrent `$ask compete` path and the sequential direct provider fallback both need better failure handling. WebGPT produced recoverable advisory content, but the transport receipt remained failed. Claude and Grok produced no response. Kimi has a browser-oracle binding but no direct `surf kimi.submit` command exposed by `surf --help`.
