# PDF Oxide Page28 Tau Boundary Review Bundle

## Requested Review

Run a two-reviewer boundary review of the agent's entire current approach. The
human believes the agent is spiraling, drifting, and under-reporting the drift.
Evaluate that directly. Return a concrete ruling, not a broad plan.

Each reviewer should answer:

1. Did the project agent drift by starting a pdf_oxide-side chunked live-review implementation?
2. What is the correct ownership boundary between `pdf_oxide`, Tau, and SciLLM for the page28 live-review timeout?
3. Should the next action be a Tau-owned GitHub ticket, a pdf_oxide local patch, a direct retry, or a human-blocked stop?
4. If a Tau issue is correct, provide the exact issue title, target, labels, body outline, and acceptance proof.
5. If a pdf_oxide patch is correct, name the exact file boundary and the deterministic proof, while respecting that Tau owns DAG/model orchestration.
6. State whether the current approach is spiraling. If yes, name the specific behavior to stop immediately.
7. Restate the immutable goal and the exact current blocker within that goal.

Required final format:

```text
VERDICT: <TAU_TICKET | PDF_OXIDE_PATCH | DIRECT_RETRY | BLOCKED_NEEDS_HUMAN>
WHY:
NEXT_ACTION:
NON_GOALS:
PROOF_REQUIRED:
```

## Immutable Goal

Harden all PDF Lab page candidates one page/checklist item at a time across the active candidate queue, using the GOAL.md proof ladder: select one candidate, preserve human/visual evidence, create or update the focused regression before patching, run bounded scillm/OpenCode executor only when useful, perform deterministic project-agent audit, commit and push task-relevant code/artifacts, then advance only when the current candidate is proven or explicitly blocked with receipt artifacts.

## Human Boundary Statements

- Tau owns DAG/agentic harness work.
- Tau controls SciLLM in its internals.
- Do not touch SciLLM internals from pdf_oxide.
- Criterion 6 live GitHub apply remains blocked until a valid approval receipt exists.
- Stop only if genuinely blocked or the immutable goal is achieved.
- The human challenged drift after the agent spent too long on page28 and started a chunked-review implementation inside pdf_oxide.

## Current Worktree

- Worktree: `/tmp/pdf_oxide_page45b_1784600709`
- Branch: `codex/page45-remaining-second-item`
- HEAD: `2540e6eb2732751951d8c2b9721d59506977f6f0`
- Remote: `git@github.com:grahama1970/pdf_oxide.git`
- Tau repo exists locally at `/home/graham/workspace/experiments/tau`, but it has unrelated dirty/untracked work. Do not edit Tau files directly from this task.

## Current Page

- Active page: page28.
- Active live output dir: `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z`
- Page case: `page_case_0001_p0028`
- Candidate count: 18.

## Proven Narrow Local Work

### Footer Source-Type Repair

- Code: `scripts/pdf_lab/snapshot_current_extraction.py`
- Test: `tests/test_pdf_lab_snapshot_current_extraction.py`
- Proof command:

```bash
pytest -q tests/test_pdf_lab_snapshot_current_extraction.py -k page28_footer_source_type
```

- Result: `1 passed, 15 deselected`
- Deterministic artifact: `artifacts/pdf_lab/page28_footer_source_type_20260721/audit_summary.json`, `ok:true`

### Generic `text` Preset Prompt Contract

- Code: `scripts/pdf_lab/run_page_second_pass_dag.py`
- Test: `tests/test_pdf_lab_page_second_pass_dag.py`
- Proof command:

```bash
pytest -q tests/test_pdf_lab_page_second_pass_dag.py -k "text_preset_semantic_subtype_contract"
```

- Result: `1 passed, 216 deselected`

This repair clarified that `preset_type: text` is a broad review stratum and that `section_subtitle` can be clean-compatible when text, bbox, and visual role are accurate. It did not weaken `validate_review_response`.

## Latest Live Failure

Authenticated run command family:

```bash
set -a
. /home/graham/workspace/experiments/scillm/.env
set +a
python scripts/pdf_lab/run_second_pass_harness.py \
  --pdf /mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf \
  --ledger python/pdf_oxide/presets/document_families/nist_sp_800_53r5_promotion_ledger.json \
  --apply-mode release \
  --out artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z \
  --candidate-census-page 28 \
  --sample-size 1 \
  --seed 28 \
  --review-mode live \
  --patch-mode dry_run \
  --patch-backend opencode_serve \
  --commit-mode dry_run \
  --model vlm-free2 \
  --scillm-base-url http://localhost:4001 \
  --caller-skill pdf-lab \
  --scillm-timeout-s 120 \
  --dpi 72 \
  --page-orchestrator-mode live \
  --patch-prompt-profile plan_only \
  --repair-strategy single \
  --candidate-census-timeout-s 120 \
  --candidate-page-timeout-s 90 \
  --page-extract-timeout-s 90 \
  --human-annotated-pages-json artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z/human_forced_pages.json \
  --stop-on-nonterminal
```

Command exit summary:

```json
{"out":"artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z","selected_pages":[28],"terminal_status":"failed_closed"}
```

Key receipts:

- `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z/harness_final_gate.json`
  - `ok:false`
  - `terminal_status:"failed_closed"`
- `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z/page_cases/page_case_0001_p0028/scillm_page_orchestrator_run_receipt.json`
  - transport registration succeeded
  - transport run `otr-46cb91a2f219`
  - HTTP status `200`
- `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z/page_cases/page_case_0001_p0028/scillm_review_error.json`

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

- `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z/page_cases/page_case_0001_p0028/review_validation.json`

```json
{
  "candidate_count": 18,
  "errors": ["scillm_review_call_failed"],
  "ok": false,
  "seen_candidate_ids": []
}
```

Request shape from generated live `review_request.json`:

- model: `vlm-free2`
- images embedded: 2
- prompt chars: 39236
- candidate_count: 18
- generated prompt contains the new `text`/`section_subtitle` compatibility rule
- generated prompt contains page/candidate status consistency rule

## Drift Event And Rollback

The agent started implementing a pdf_oxide-side chunked live-review path in `scripts/pdf_lab/run_page_second_pass_dag.py` and corresponding tests. The human identified this as drift because Tau owns DAG/model orchestration.

Rollback was performed. Confirmation command:

```bash
rg -n "chunked_review|chunk_review|review_chunk|LIVE_REVIEW_CANDIDATE_CHUNK_SIZE" scripts/pdf_lab/run_page_second_pass_dag.py tests/test_pdf_lab_page_second_pass_dag.py || true
```

Result: no matches.

## Prior External Context

Brave Search artifact:

- `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z/unblock/brave_vlm_timeout_context.json`

Prior WebGPT/Tau artifact:

- `artifacts/pdf_lab/live_second_pass_page28_vlm_free2_auth_prompt_repaired_orchestrator_live_20260721T1140Z/unblock/ask_webgpt_timeout/ask-tau-webgpt-review-bundle-pdf-oxide-p-b2aec86c58fd/node-artifacts/handler-webgpt/response.md`

That WebGPT response recommended splitting large page review calls, but the project agent should not implement that in pdf_oxide if it is Tau-owned orchestration.

GitHub check:

```bash
gh issue list --repo grahama1970/tau --state all --search "pdf_oxide page28 SciLLM ReadTimeout" --limit 20 --json number,title,state,labels,url
```

Result: `[]`

## Constraints For Reviewers

- Do not treat WebGPT/WebClaude advice as closure proof.
- Do not recommend direct SciLLM internals changes from pdf_oxide.
- Do not broaden into dashboards, aggregate reports, Criterion 6 apply, or all-page batch retries.
- The next step must preserve page28 evidence and the one-page proof ladder.
- If the answer is `TAU_TICKET`, the ticket must be independently actionable and include deterministic acceptance proof.
- If the answer is `PDF_OXIDE_PATCH`, the patch must be a narrow local bug fix, not DAG/model transport redesign.
