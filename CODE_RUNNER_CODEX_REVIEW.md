# Codex 5.3 Review of Code-Runner Context Persistence Design

Model: gpt-5.3-codex
Tokens: {"completion_tokens": 1014, "prompt_tokens": 5313, "total_tokens": 6327, "completion_tokens_details": null, "prompt_tokens_details": null}

---

## STRENGTHS

- **You identified the right bottleneck**: losing within-round tool interaction is exactly why iterative repair quality stalls.
- **Separation of concerns is solid**: tool loop, diagnosis, prompt assembly, memory are cleanly split.
- **Design is practical, not theoretical**: compressed trace + last-2 rounds is a good token-aware compromise.
- **You already have strong anchors** (immutable original request, DoD, allowlist), which helps prevent drift when adding more context.
- **Including trace in both system (history) and user (immediate fix) is directionally correct**: system for persistent policy/context, user for current objective.

---

## ISSUES

- **Trace compression is too syntax-driven** and may miss high-value semantics (error codes, symbol names, borrow spans, “first changed here then reverted there”).
- **Current error extraction is brittle** (`"error"` substring). Rust diagnostics often require multi-line context (`E0502`, spans, notes, “first borrow occurs here…”).
- **DO NOT accumulation can overfit**: some “failed” attempts were failed because of adjacent mistakes, not core strategy invalidity.
- **Potential prompt duplication/conflict**: same prior history appears in system and user; if wording differs, model may prioritize inconsistent constraints.
- **No explicit confidence/state tagging** for prior conclusions (e.g., “hypothesis”, “confirmed”, “invalidated”).
- **Cross-session memory schema is under-specified**: compressed trace text alone is weak for retrieval unless you index structured facets.

---

## RECOMMENDATIONS

1. **Upgrade trace schema from plain text to structured events first, render later**  
   Store per action: `{round, step, tool, file, line_range, symbol?, command, exit_code, rust_error_codes[], primary_error, outcome}`.

2. **Capture Rust diagnostics explicitly**  
   Parse and persist:
   - `error[E0xxx]`
   - primary span file:line
   - related notes (“borrow later used here”, “first mutable borrow”)
   - involved symbols/types (`PdfDocument`, `FontInfo`, function names)

3. **Add “attempt verdict” metadata**  
   For each strategy/DO-NOT item: `status = {confirmed_bad, likely_bad, inconclusive}`.  
   Only hard-ban `confirmed_bad`.

4. **Use system for stable history, user for immediate delta**  
   Keep full prior rounds only in system; in user include only “last round actions + current diagnosis + concrete next objective”.

5. **Keep read_file summaries, but semantic**  
   Yes to 1-line summaries—but include *why it mattered*:
   “read page_renderer.rs:180-220 → found pre_resolve_fonts requires &mut PdfDocument”.

6. **For /memory, store dual form**  
   - Human-readable compressed trace
   - Structured vector/index fields (error codes, symbols, crate/module, fix pattern, outcome score)

7. **Add stagnation guardrails**  
   If same error code + same failing tests repeat 2 rounds, force strategy switch template (e.g., “minimal repro reasoning”, “type-signature-first rewrite”, “revert-and-rebuild smallest path”).

8. **Token control**  
   Cap by importance, not only chars/turns: keep last failure-causing edit + final compiler/test output + one prior contrasting attempt.

---

## ANSWERS TO YOUR 7 QUESTIONS

1. **Is compressed trace dense enough? include more run_command output?**  
   Mostly good start, but **include more**: at least first primary Rust error block + one related note + error code + span. Not full logs.

2. **Turn history in system or user?**  
   **System** for durable prior-round context. **User** should carry only current objective + last-round critical trace snippet.

3. **Is DO NOT accumulation safe?**  
   Not fully. It can over-constrain. Add confidence tiers and expiration/pruning for soft constraints.

4. **Rust borrow errors: extra context to capture?**  
   Yes: error code (`E0502/E0499/E0507`), both borrow spans, function signature at failure site, involved lifetimes/types, and whether borrow comes from loop/iterator closure.

5. **Keep 1-line read_file summary?**  
   **Yes**, but semantic, not just “read happened.” Include discovered invariant or API constraint.

6. **Compressed trace for cross-session memory?**  
   Useful but insufficient alone. Store structured fields for retrieval + compressed text for prompt injection.

7. **Prompt engineering issues overall?**  
   Strong architecture. Main risks: duplicated context, over-long constraints, and hard-negative “DO NOT” poisoning. Fix with hierarchy (system=history, user=task), confidence tags, and contradiction checks before dispatch.