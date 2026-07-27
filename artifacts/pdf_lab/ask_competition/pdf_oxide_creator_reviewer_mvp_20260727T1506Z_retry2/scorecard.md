# Ask Competition Retry2 Scorecard - pdf_oxide Creator-Reviewer MVP

Immutable Goal: NOT_MET

## Source-Derived Step Model

1. Define one page/candidate packet for page456 table-header/header-cell evidence.
   - Implemented: compact request names the page, current labels, table bbox, row/column counts, header source ids, focused proof, and relevant files.
   - Intended/missing: no local schema file or repair-planner implementation has been added from this competition yet.

2. Ask isolated competitors for an executable creator-reviewer contract.
   - Implemented: `$ask compete` was run through `skills/ask/run.sh` on agent-skills `origin/main` `635b4b1df`.
   - Intended/missing: full five-handler competition did not reach a join node because three browser lanes did not terminate inside the intended bound.

3. Require machine-executable defect objects, not reviewer prose.
   - Implemented: both live successful responses included schema fields for labels, bbox, defect class, repair plan, and validation.
   - Intended/missing: the returned schemas are advisory until a local pdf_oxide schema/validator and tests consume them.

4. Map defect objects to repair target files/functions.
   - Implemented: WebGPT proposed explicit repair-plan target and invariant gating; WebGemini proposed a lighter target/isolation contract.
   - Intended/missing: no patch planner is implemented in pdf_oxide yet.

5. Replay the same page and accept only from local proof.
   - Implemented: request anchored acceptance to page456 metrics and focused regression `tests/test_nist_page456_control_table_headers.py`.
   - Intended/missing: no new local replay was run for an implemented schema because no schema/patch was applied in this slice.

6. Preserve transport failures as tickets, not prose drift.
   - Implemented: #1032 was filed against `agent-skills` with live Ask/Surf timeout-control evidence.
   - Intended/missing: #1032 remains open; five-handler competition cannot be treated as a complete Ask selection phase until that runtime issue is repaired or bypassed by an accepted narrower handler set.

## Ticket Readback

- #1030: OPEN, `Ask WebGPT compete lane rejects fresh open-bind tab when many ChatGPT tabs are open`.
- #1031: CLOSED/COMPLETED, `Ask compete browser handlers time out without actionable provider receipts`, repaired at `6f9fab04688d2e51fa532638a139dc161ed7f38b` with later main at `635b4b1df`.
- #1032: OPEN, `Ask compete fresh-temporary run does not bound child browser provider workers`.

## Run 1: Five-Handler Retry

- Ask runtime: `/home/graham/workspace/experiments/agent-skills_issue1029_runtime`, `HEAD == origin/main == 635b4b1df`.
- Command mode: `./run.sh compete`, handlers `webgpt`, `webclaude`, `webkimi`, `webgemini`, `webgrok`, `--browser-tab-lifecycle fresh-temporary`, `--execute`, `--poll-timeout-seconds 900`.
- Run directory: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_retry2-20260727T144051Z/pdf-oxide-creator-reviewer-mvp-page456-retry2-20260727T144051Z`.
- Mocked: no.
- Live: yes for browser lanes that reached providers.
- Status: interrupted after preserving evidence because the run remained active beyond 1300 seconds and did not produce a final join scorecard.

### Candidate Receipts

- `webgpt`: PASS, provider_live=true, response_chars=26619, tab_id `837362531`.
- `webgemini`: PASS, provider_live=true, response_chars=9594, tab_id `837362539`.
- `webclaude`: no terminal node receipt; remained stuck in provider read path beyond requested bound.
- `webkimi`: no terminal node receipt; nested Surf process observed under `timeout 2760s`.
- `webgrok`: no terminal node receipt; nested Surf process observed under `timeout 2760s`.

### Preserved Evidence

- Process evidence: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_retry2-20260727T144051Z/pdf-oxide-creator-reviewer-mvp-page456-retry2-20260727T144051Z/runtime-stall-evidence/processes.txt`.
- Partial receipts: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_retry2-20260727T144051Z/pdf-oxide-creator-reviewer-mvp-page456-retry2-20260727T144051Z/runtime-stall-evidence/partial-node-receipts.jsonl`.
- Artifact inventory: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_retry2-20260727T144051Z/pdf-oxide-creator-reviewer-mvp-page456-retry2-20260727T144051Z/runtime-stall-evidence/artifacts-present.txt`.

## Run 2: Reduced Working-Lanes Retry

- Command mode: `./run.sh compete`, handlers `webgpt`, `webgemini`, `--browser-tab-lifecycle fresh-temporary`, `--execute`, `--poll-timeout-seconds 900`.
- Run directory: `/mnt/storage12tb/skills/ask/outputs/pdf_oxide_creator_reviewer_mvp_retry2-working-lanes-20260727T150546Z/pdf-oxide-creator-reviewer-mvp-page456-retry2-working-lanes-20260727T150546Z`.
- Mocked: no.
- Live: no provider calls; blocked before Tau execution.
- Status: BLOCKED.
- Failure: `browser_tab_lifecycle_failed`, `browser_window_create_failed`.
- Evidence: `browser-tab-lifecycle.json` shows `/skills/surf/run.sh window.new https://chatgpt.com/ --json --unfocused` timed out after 60.064 seconds with return code 124.

## Local Judging From Partial Live Responses

No official Ask winner was selected because no complete join scorecard exists.

From the two live successful candidates, WebGPT has the stronger promotable design signal:

- It defines a more executable defect state for `TABLE_HEADER_SOURCE_REEMITTED`.
- It separates guard/current-state objects from open regression objects.
- It names a local invariant evaluator tied to page456 metrics: `table_count=1`, `target_leak_count=0`, `target_lineage_count=5`, 43 rows, 4 columns, header row, four column headers, and the focused pytest.
- It requires the repair planner to refuse unknown or ambiguous repair classes.

WebGemini is useful but less specific:

- It provides a compact schema and page456 guard concept.
- Its `VERIFIED_FEATURE` lines are higher-level and less directly translatable into a validator/repair planner.

## Result

The retry answered the ticket question partially:

- #1030 symptom appears improved on `635b4b1df`: WebGPT produced a live PASS in a many-tab/fresh-lifecycle compete run.
- #1031 is not sufficient for this five-handler use case: the new run still failed to produce bounded timeout receipts and required manual cleanup.
- #1032 now tracks the remaining Ask/Surf fresh-lifecycle timeout and child-process cleanup defect.

This artifact is not proof that pdf_oxide extraction correctness improved. It is evidence that the external competition can now get useful live WebGPT/WebGemini schema proposals, while the full requested browser competition is still blocked by Ask/Surf runtime behavior.
