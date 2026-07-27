# PDF-Oxide Creator-Reviewer MVP Competition Retry

Status: PASS_WITH_DEGRADED_TRANSPORT
Created: 2026-07-27T13:24:00Z
Ask runtime: `/home/graham/workspace/experiments/agent-skills_issue1029_runtime`
Ask runtime commit: `c330fab92819e876f1df21667c687a3f3645bbcf`
Prior ticket checked: https://github.com/grahama1970/agent-skills/issues/1029
Residual ticket filed: https://github.com/grahama1970/agent-skills/issues/1030

## Source-Derived Step Model

1. Check whether the prior Ask/browser transport ticket was repaired.
   - Implemented: issue 1029 is `CLOSED` with `stateReason=COMPLETED`.
   - Implemented: a clean worktree at `origin/main` was created so the retry used commit `c330fab92819e876f1df21667c687a3f3645bbcf`, not the stale dirty checkout.
   - Implemented: `browser-oracle open-bind --help` exists in the repaired worktree.

2. Recreate clean browser bindings for each competitor.
   - Implemented: fresh browser-oracle projects were created for `webgpt`, `webclaude`, `webkimi`, `webgemini`, and `webgrok`.
   - Implemented: provider gate reported all five handlers `READY` with live tab ids.

3. Run the isolated Ask competition.
   - Implemented: `./run.sh compete` executed the compact page456 creator-reviewer MVP packet with five concurrent handlers.
   - Evidence run directory:
     `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_20260727T1315Z_retry/pdf-oxide-creator-reviewer-mvp-page456-retry-20260727T1315Z`

4. Preserve and score receipts.
   - Implemented: Tau DAG receipt status is `PASS`, `ok=true`, `mocked=false`, `live=true`, `max_observed_concurrency=5`, `execution_seconds=300.570747`.
   - Implemented: join scorecard selected `webgemini` as the only receipt-backed winner.
   - Missing: no local pdf-oxide schema or validator was implemented in this retry.

5. Convert the winner into local deterministic work.
   - Intended: implement a canonical defect/guard schema and one page456 guard object, then prove it locally with JSON Schema validation plus `pytest tests/test_nist_page456_control_table_headers.py`.
   - Missing: this implementation/proof gate remains separate from the Ask competition.

## Competition Outcome

Winner: `webgemini`

Competition selection receipt:

- `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_20260727T1315Z_retry/pdf-oxide-creator-reviewer-mvp-page456-retry-20260727T1315Z/node-artifacts/join/compete-scorecard.json`

Tau DAG receipt:

- `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_20260727T1315Z_retry/pdf-oxide-creator-reviewer-mvp-page456-retry-20260727T1315Z/tau-receipts/dag-receipt.json`

Provider gate:

- `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_20260727T1315Z_retry/pdf-oxide-creator-reviewer-mvp-page456-retry-20260727T1315Z/provider-gate.json`

## Candidate Status

| Candidate | Status | Failure Code | Feature Count | Artifact |
| --- | --- | --- | ---: | --- |
| `webgemini` | `PASS` | none | 1 | `node-artifacts/handler-webgemini/response.md` |
| `webgpt` | `NEEDS_ATTENTION` | `browser_submit_not_accepted` | 0 | `node-artifacts/handler-webgpt/browser-recovery-packet.json` |
| `webclaude` | `NEEDS_ATTENTION` | `browser_handler_timeout` | 0 | `node-artifacts/handler-webclaude/browser-recovery-packet.json` |
| `webkimi` | `NEEDS_ATTENTION` | `browser_handler_timeout` | 0 | `node-artifacts/handler-webkimi/browser-recovery-packet.json` |
| `webgrok` | `NEEDS_ATTENTION` | `browser_handler_timeout` | 0 | `node-artifacts/handler-webgrok/browser-recovery-packet.json` |

## What 1029 Fixed

- `browser-oracle open-bind` exists in the repaired runtime.
- Ask provider gate handled mixed command stdout and many browser tabs well enough to mark all five fresh bindings `READY`.
- The competition no longer failed globally when four browser lanes degraded.
- The join selected `webgemini` from the available receipt-backed candidate instead of returning no winner.

## Remaining Runtime Failure

WebGPT still failed in this real pdf-oxide run:

```text
webgpt.submit tab identity preflight failed for tab 837362433.
unverified_tab_id_with_multiple_chatgpt_tabs
Use --url <conversation-url>, --expect-url, --expect-title, --create-tab, or --allow-unverified-tab-id.
```

The recovery packet suggested re-running the same `open-bind` command, but the failure was caused by Ask invoking `webgpt.submit` with `--tab-id` only in a browser with many ChatGPT tabs. That residual was filed as:

- https://github.com/grahama1970/agent-skills/issues/1030

## Winner Features To Harvest

The `webgemini` response is advisory. Locally checkable ideas worth harvesting:

1. Use a strict defect/guard object as the reviewer output.
2. Include a `PAGE456_INSTANCE` guard for table-header/header-cell lineage.
3. Add a repair taxonomy with at least:
   - `HEADER_CELL_FRAGMENT_LEAK`
   - `TABLE_BOUNDS_MISALIGNMENT`
   - `SECTION_HIERARCHY_POLLUTION`
   - `PRESET_OVERRIDE_REGRESSION`
4. Map defect classes to exact target files and invariants before patching.
5. Accept only from local proof commands:
   - `pytest tests/test_nist_page456_control_table_headers.py`
   - optional Rust unit coverage once mapped to an existing test target.

## Caveat On Gemini Schema

The Gemini schema should not be copied as-is. It contains dotted duplicate fields such as `subject.element_id` and `defect.class` alongside nested fields. The local implementation should normalize this into one canonical nested JSON Schema with `additionalProperties: false`.

## Next Deterministic Gate

Implement the local one-case schema and validator from the winner direction, then validate a page456 guard object with JSON Schema and the existing focused pytest. The Ask competition does not prove pdf_oxide extraction correctness by itself.
