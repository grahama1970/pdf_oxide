# Senior Code Review: code-runner context persistence

Model: gpt-5.3-codex
Tokens: {"completion_tokens": 1304, "prompt_tokens": 6280, "total_tokens": 7584, "completion_tokens_details": null, "prompt_tokens_details": null}

---

## BUGS

1. `compress_tool_trace`: `if start:` drops valid `start_line=0`. Use `if start is not None`.
2. `run_command` failure detection is weak:
   - `if "error" in content.lower() or "FAILED" in content` misses many failing commands and can false-positive on logs.
   - Prefer exit code from tool payload (if available). If unavailable, parse structured tool result envelope.
3. `tool` message `content` may be non-string (list/dict in OpenAI-style tool outputs). You assume string and call `.split("\n")`. Crash path.
4. `tc["function"]["name"]` / `tc["function"]` direct indexing can KeyError on malformed tool call objects. You already defensive-parse args; do same for name.
5. `pending[tc_id]`: empty `tc_id` collisions if provider omits id → events overwrite linkage. Need synthetic id fallback (`f"{step}:{fn}"`) and/or queue per tool call order.
6. `edit_file` outcome based on `content.startswith("OK")` is protocol-coupled and brittle. If tool returns JSON `{ok:true}`, this mislabels.
7. `_render_trace`: truncating `text[:max_chars]` can cut mid-line/mid-constraint. Prefer line-aware truncation.
8. `build_do_not_constraints`:
   - Declares “inconclusive” in docstring but never emits it.
   - “same error code on same file in 2+ rounds” can be duplicate events within one round; should dedupe by round id.
9. `build_system_prompt`:
   - `read_text()` no encoding specified. Non-UTF8 can blow up.
   - dynamic import `from evidence import recall_similar_fixes` inside function: avoidable overhead and harder static analysis.
10. `build_fix_from_diagnosis`: unguarded `diagnosis.primary_target.file` etc. If diagnosis object is partial, crash.

## DESIGN ISSUES

1. You compress only the **last tool-use loop**, then next round FIX prompt uses `rounds_history[-1].tool_trace`. Good, but ensure round persistence writes both `tool_trace` and `tool_trace_events` every path (success/fail/timeout/zero-write). Snippet suggests possible missing branches.
2. No canonical event schema version. Add `tool_trace_schema_version` for forward compatibility in `/memory`.
3. BM25 retrieval over event dicts is weak unless you also store a flattened lexical field (`trace_lex`: “read file symbol error E0502 mutable borrow ...”).
4. Read summaries are underpowered: first meaningful line often misses why file was read. Capture: detected symbols list + imports + function signature count.
5. Missing causality links: you don’t connect `edit_file` to subsequent failing `run_command` in structure. Add `caused_failure_codes` post-pass mapping.
6. `max_events=12` static is risky across long rounds. Better budget by token estimate and prioritize `(last failing run_command + preceding edits + immediately preceding reads)`.

## PROMPT ENGINEERING

1. Current numbered list is good for models; better than markdown tables.
2. Add explicit section headers with stable keys for parser-friendly consumption:
   - `LAST_ATTEMPT_ACTIONS`
   - `LAST_ATTEMPT_FAILURES`
   - `AVOID_REPEATS`
3. For failures, emit one-line tuple format:
   - `E0502 | src/foo.rs:123 | mutable+immutable borrow overlap | symbol=render_page`
   This is denser and easier to reason over than prose snippets.
4. Don’t inject full `llm_approach` unbounded; cap length like summary.
5. In fix prompt, “do not repeat failed approaches” is too strong for likely_bad. Split wording by confidence tier.

---

### 1) Code quality
Main issues are brittle tool-result parsing, id linkage collisions, non-string `tool.content` crash paths, and unimplemented “inconclusive” tier. Also strengthen guards around nested dict access and diagnosis fields.

### 2) Prompt engineering format
Keep numbered indented list, but add compact failure tuples and stable section labels. JSON is token-heavier and less readable for generation-time reasoning unless you need machine post-processing.

### 3) Token efficiency
Trim:
- `llm_approach` cap (e.g., 200 chars).
- `content_preview` to 50 chars.
- borrow notes from 2 → 1 unless repeated code.
- remove successful read events unless they introduced symbol used in edit.
This keeps signal while cutting fluff.

### 4) Compression tradeoff
Dropping full read content is right. Keep semantic extraction only.
For edits, you need minimal **before-context hash/snippet** (1–2 lines replaced) to avoid retrying same patch pattern. Not full before state.

### 5) Error parsing
Language-agnostic path: parse generic patterns first (`file:line`, `error code`, `exception type`, `FAILED tests x/y`) then plug language-specific adapters (Rust, Python, TS) behind a common interface. Prefer tool-native structured diagnostics if available over regex.

### 6) DO NOT confidence tiers
Useful, not over-engineering. But implement inconclusive for real and soften prompt language accordingly. Hard bans should be rare.

### 7) Memory schema for BM25
Current schema is okay for storage, not optimal for recall. Add flattened searchable text fields and normalized keys (`error_codes`, `symbols`, `files_touched`, `commands_run`, `failure_signature`).

### 8) Missing context for round 3 Rust borrow fix
I’d want:
- exact borrow-conflict pair spans (both lines, same function),
- ownership intent (needs mutable? clone acceptable?),
- last diff hunks attempted (not just preview),
- which command failed (`cargo test <target>` vs `cargo check`),
- whether failure count moved up/down after edit.
You’re missing precise span pairs and diff hunks.

### 9) Import inside hot loop
Move to top-level unless avoiding circular import. Runtime overhead is small but unnecessary; top-level is cleaner and safer for lint/type tooling.