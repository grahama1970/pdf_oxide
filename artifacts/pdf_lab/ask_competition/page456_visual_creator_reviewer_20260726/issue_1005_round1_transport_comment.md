Fresh receipt from the PDF Lab page456 competition retry:

- Ask run: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide/pdf-oxide-page456-visual-creator-reviewer-r1-degraded-20260726`
- Request target: `pdf-lab-page456-visual-creator-reviewer-loop`
- Handlers requested: `webkimi`, `webgpt`, `webclaude`, `webgrok`
- Prompt was degraded text/coordinate evidence because WebGPT attachment upload already failed with `AI_UPLOAD_FILE_TO_TAB`.

Observed terminal state:

```json
{
  "status": "BLOCKED",
  "verdict": "COMMAND_FAILED",
  "node_progress": [
    {"node_id":"handler-webclaude","status":"BLOCKED"},
    {"node_id":"handler-webgpt","status":"RUNNING"},
    {"node_id":"handler-webgrok","status":"RUNNING"},
    {"node_id":"handler-webkimi","status":"RUNNING"},
    {"node_id":"join","status":"PENDING"}
  ]
}
```

Node receipts after process loss/restart:

```text
handler-webclaude: status ERROR, verdict browser_tab_read_timeout, response.raw.md 0 bytes
handler-webgpt:    status ERROR, verdict browser_tab_identity_mismatch, response absent
handler-webgrok:   status ERROR, verdict prompt_too_large_or_stalled, response.raw.md 0 bytes
handler-webkimi:   status ERROR, verdict browser_provider_rate_limited, response.raw.md 0 bytes
```

Important mismatch:

- WebGPT `response.meta.json` tab preflight reports `ok: true` and expected URL matched tab `837361214`.
- WebGPT `node-receipt.json` still reports `browser_tab_identity_mismatch`.

Impact:

The competition produced no model responses to score. The PDF Lab workflow needs a robust way to pass visual UX evidence and continue/salvage independent lanes when one browser lane fails.
