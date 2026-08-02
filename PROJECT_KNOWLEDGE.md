# PDF Oxide Project Knowledge

Last updated: 2026-04-16

## Current Understanding

### PDF Extraction Pipeline Architecture (2026-04-16)

**Two-pass architecture for SPARTA ingestion:**

```
Pass 1: pdf_oxide (Rust) → deterministic extraction (blocks, tables, bboxes)
Pass 2: Python presets   → classification + enrichment (headers, requirements, control IDs)
                               ↓
                         /learn-datalake → ArangoDB /memory → relate to SPARTA Controls
```

**Pass 1 (Rust, deterministic):**
- Raw text blocks with positions
- Table detection with cells
- Font/size metadata
- Same input → same output, always

**Pass 2 (Python, iterative):**
- Classify block types using presets (NIST patterns, control families)
- Detect requirements (SHALL/MUST/SHOULD patterns)
- Extract control ID references (AC-1, SI-4)
- Scanner validates quality

**When /code-runner is invoked:**
```
PDF → pdf_oxide → profile + extraction
                      ↓
                  compare
                      ↓
         match? ──yes──→ /learn-datalake (happy path, no LLM needed)
           │
          no (delta > threshold)
           │
           ↓
      /code-runner → fix classification → re-check → /learn-datalake
```

/code-runner is the **exception handler**, not the normal path. Most PDFs pass through cleanly.

**Key files (extraction calibration):**
- `extract_for_pdflab.py` - Canonical extraction + classification
- `extraction_scanner.py` - Heuristic error detection (quality gate)
- `16_PDF_EXTRACTION_CALIBRATION_TASKS.yaml` - /orchestrate plan for accuracy improvement
- `17_PDF_QUARANTINE_FIX_TASKS.yaml` - /orchestrate plan for fixing failed PDFs
- `tests/test_extraction_classification.py` - Unit tests (28 tests)

### PDF Cloning Pipeline (clone_pdf_v2.py)

The PDF cloning pipeline generates **structurally similar PDFs** with known ground truth for extraction testing and training. It produces PDFs with embedded QID markers that enable deterministic extraction validation.

**Pipeline stages:**
1. **Profile source PDF** - Extract TOC, table shapes, page signatures via `clone_profiler`
2. **Extract style profile** - Opus VLM analyzes 6 representative pages, selects presets (table, header, footer)
3. **Generate manifest** - Opus creates element sequences per section (paragraph, table, list, callout)
4. **Generate content** - LLM batch generates text/table content per section via `/scillm`
5. **Build PDF** - ReportLab renders with preset styles, embeds QID markers
6. **Output TruthManifest** - JSON with exact QID positions for validation

**Key files:**
- `clone_pdf_v2.py` - Main CLI entry point
- `python/pdf_oxide/clone/` - Module directory
  - `clone_builder.py` - PDF rendering with presets
  - `clone_types.py` - RenderPlan, SectionBudget, TruthManifest
  - `manifest_generator.py` - Opus manifest generation
  - `sampler_content.py` - LLM batch content generation
  - `style_extractor.py` - VLM style extraction
  - `presets.py` - TABLE_PRESETS (36), HEADER_PRESETS (8), FOOTER_PRESETS (8), CALLOUT_PRESETS (12)

### Calibration Fixture Workflow

When extraction fails (profile counts vs extraction counts mismatch), use clones as calibration fixtures:

```
Original PDF fails extraction (47 tables profiled, 12 extracted)
        ↓
Clone PDF (same structure, QID markers as ground truth)
        ↓
Run extractor on clone
        ↓
    ┌───┴───┐
  PASS      FAIL
    ↓         ↓
Extractor   Extractor bug
works       (fix before retry)
    ↓
Retry original PDF
    ↓
    ┌───┴───┐
  PASS      FAIL
    ↓         ↓
Done      PDF-specific issue
          (encoding, corruption, layout)
```

### scillm Integration

**Batch content generation:**
- Use `model: "text"` for dynamic Chutes routing with fallback chain
- For guaranteed completion, use `model: "sonnet"` (OAuth, no rate limits)
- Chunk size queried from `/v1/scillm/concurrency?model=<model>`
- Include `X-Caller-Skill` header and `scillm_metadata` with `batch_id`/`item_id`
- Implement exponential backoff for queue busy (429 with "BUSY" in response)

**Tested performance (32 sections):**
| Model | Success Rate | Time |
|-------|--------------|------|
| `text` (Chutes) | 50% (queue contention) | 470s |
| `sonnet` (OAuth) | 100% | 94s |
- 2026-06-01 NIST SP 800-53r5 next-30 agentic second-pass loop is ledger-closed, not zero-finding. Original 68 findings are all patched_confirmed_by_snapshot_and_model_rerun. Supplemental rerun findings total 13: 9 patched, 2 rejected false-positive with proof, and 2 concretely blocked. The final 30-page gpt-5.5 rerun completed with 30 ok/raw-ok pages and 3 findings: p30 rotated side-chrome bbox overlap blocked on line/oriented geometry, p100 Control: field-label finding rejected by schema decision, and p232 nested numeric list collapse blocked on line-level nested-list materialization.
- 2026-06-01 transition note: local/HANDOFF.md now contains the exact next-session /goal for a fresh NIST follow-on 30-page agentic second-pass loop. The next agent should build or verify a new packet excluding original packet pages 27,28,34,35,45,401,421,455,456,468 and prior next30 pages 16,17,30,51,100,116,137,157,165,178,187,190,194,200,232,235,251,253,254,260,264,298,336,338,355,382,399,403,409,413; run gpt-5.5 sequentially with raw artifacts; create a new append-only ledger rather than mixing with the old next30 ledger; and close only by patched/rejected/human-only/blocked proof plus focused tests and final rerun.
- 2026-06-01 NIST fresh follow-on next-30 loop is ledger-closed with blockers, not zero-finding. Fresh packet: artifacts/pdf_lab/project_agent_hardening/fresh_next30_review_packet_20260601Tnow; selected pages 15,22,24,23,482,21,46,457,20,222,481,470,485,466,404,490,464,19,415,416,419,458,186,489,486,461,402,474,417,410 and excludes original + prior next30 pages. Final v2 rerun: artifacts/pdf_lab/project_agent_hardening/fresh_next30_agentic_second_pass_20260601Tnow_final_full_rerun_v2/summary.json with page_count=30, ok_count=30, raw_ok_count=30, finding_count=26, human_needed_count=0. Ledger: artifacts/pdf_lab/project_agent_hardening/fresh_next30_agentic_triage_ledger_20260601Tnow/triage_ledger.json; final reconciliation has unmapped_count=0.
- Lessons from the 2026-06-01 fresh loop: the runner prompt must include final materialized fields such as semantic_role, parent_id, label, target_page, dot_leader, and an explicit note that source_type is raw provenance rather than final classification. Without those fields, gpt-5.5 can report false positives such as TOC entries typed as reference even though they are correctly semantic_role=toc_entry and grouped under a toc parent.
- Bounded patches proven in the fresh loop: reference appendix hanging-indent continuation coverage now spans pages 402-419; gray-band references appendix dividers classify as section_heading/reference_section_heading; reference fragments like Volume + 4. merge into the preceding reference continuation; the frontmatter Errata title is a section_heading/frontmatter_section_heading; explicit References: lead lines use semantic_role=reference_lead; reference token/URL normalization applies to both reference_lead and reference_continuation. Focused suite after these changes: 56 passed in 1.97s.
- Remaining concrete blockers from the fresh loop: 25 findings require real ruled/shaded table-grid materialization before heading/list/body classifiers, not preset-only retagging; 2 rotated side-chrome geometry findings require rotation-aware line-level geometry or clipped margin bboxes; 1 nested list finding on page 46 requires line-level child spans or core nested-list structure. These should be treated as substrate blockers, not ordinary unresolved model findings.
- 2026-06-04 process change: PDF Lab hardening should use a deliberate teacher/student/human-in-the-loop loop per page/candidate. The Project Agent selects one candidate from PDF Lab evidence, justifies the selection, the human annotates or confirms the visual defect, the scillm/OpenCode executor attempts a focused regression-first patch in an isolated code root, and the Project Agent audits deterministic artifacts before moving to the next page. Broad /goal-style loops and unrelated pytest expansion caused drift and should not be used as the default for current pdf_oxide core extraction hardening.
- 2026-06-04 page-by-page hardening advanced to nist_phase54_page_0046 after page 45 student-harness proof was blocked by scillm #11/#12. Active issue: grahama1970/pdf_oxide#3. Selected defect: pdf_oxide_core nested list segmentation where AC-2 item h and numbered children 1/2/3 are merged into actual:p46:block:18, while same-page d/i nested-list exemplars are correctly split.
- 2026-06-29 page-46 Tau while-loop sanity proof is now repeatable as scripts/pdf_lab/run_page46_tau_goal_proof.py. Fresh live run artifacts are under artifacts/pdf_lab/page46_tau_goal_proof_rerun_live: before candidate_count=6 and live reviewer terminal_status=still_open reason=patch_delegate_dry_run; isolated patch leg applied current focused diff sha256=526acfbfe4b978d75a323dac87b3c470da841de67fe28027490296bab7567feb; after candidate_count=28 and live reviewer terminal_status=reviewed_clean reason=scillm_review_validated_clean; validation file page46_tau_loop_goal_proof_validation.json reports ok=true. Boundary: this proves the bounded page-46 evidence/reviewer/patch/re-extract/reviewer loop with live reviewer and live extraction, but not autonomous coder generation or document-wide extraction quality.
- 2026-06-29 page-46 Tau loop advanced one rung beyond deterministic patch apply. Current command scripts/pdf_lab/run_page46_tau_goal_proof.py supports --patch-leg subagent. Fresh live artifact artifacts/pdf_lab/page46_tau_goal_proof_subagent_live_current/page46_tau_loop_goal_proof_validation.json reports ok=true, errors=0. The loop generated before evidence candidate_count=6, live reviewer terminal_status=still_open reason=patch_delegate_dry_run, called Scillm delegate model opencode/deepseek-v4-flash as coder, required tau.subagent_receipt.v1 with result.mocked=false result.live=true result.subagent_live=true, applied the returned patch sha256=526acfbfe4b978d75a323dac87b3c470da841de67fe28027490296bab7567feb in an isolated worktree, re-extracted page 46 candidate_count=28, and live reviewer returned reviewed_clean with review_validation ok=true. Boundary: this proves live coder-subagent orchestration using a provided focused reference patch, not autonomous patch discovery from scratch or document-wide extraction quality.

## Decisions

| Decision | Rationale | Date |
|----------|-----------|------|
| Two-pass extraction: Rust + Python | Rust stable/fast, Python presets evolvable | 2026-04-16 |
| /code-runner only on profile mismatch | Most PDFs pass cleanly, save LLM tokens for exceptions | 2026-04-16 |
| Scanner as quality gate before /learn-datalake | Catch classification errors before SPARTA ingestion | 2026-04-16 |
| Sentence detection for header classification | Prevents body text with control IDs from being misclassified | 2026-04-16 |
| Running headers → boilerplate | Page chrome (NIST SP 800-53 banner) not document content | 2026-04-16 |
| Use sonnet for batch content generation | No Chutes queue contention, 100% success rate | 2026-04-15 |
| Style extraction via mini-PDF | 6 representative pages keeps VLM context manageable | 2026-04-15 |
| QID markers for ground truth | Enables deterministic extraction validation | 2026-04-15 |
| Preset system for styling | 56 presets cover common document styles | 2026-04-15 |
| Default 32 sections (not all) | Covers structural patterns without ~9min generation time | 2026-04-15 |
| Preset alias normalization | Handle LLM output variations (bullet→bullet_list) | 2026-04-15 |
| ExtractionDiscrepancy feedback | Self-correction loop for calibration fixtures | 2026-04-15 |

## Calibration Feedback Loop (2026-04-15)

The PDF cloner now supports extraction discrepancy feedback for self-correction:

**Flow:**
```
1. Profile source PDF → expected counts (TOC sections, tables)
2. Run extractor → actual counts
3. Compare → ExtractionDiscrepancy dataclass
4. If significant discrepancy → clone with --calibration-mode
5. Calibration prioritizes failed patterns (control IDs, tables)
```

**CLI flags:**
- `--discrepancy /path/to/discrepancy.json` — load extraction failure data
- `--calibration-mode` — prioritize sections matching failed patterns

**Discrepancy types:**
- `control_id_miss` — XX-N patterns not detected → prioritize control sections
- `table_over_detect` — false positive tables → include real data tables
- `table_empty` — empty table artifacts → tables with content
- `section_over_detect` — noise as sections → structured headers

**Example (NIST SP 800-53):**
| Metric | Original | Calibration Fixture |
|--------|----------|-------------------|
| Control sections | 0% detected | 14 AC-* sections |
| Tables | 243 (5x over) | 8 real data tables |

**Key files:**
- `python/pdf_oxide/clone/clone_types.py` — `ExtractionDiscrepancy`, `DiscrepancyType`
- `clone_pdf_v2.py` — `--discrepancy`, `--calibration-mode` flags

## Open Questions

- [ ] Batch manifest generation for 358+ section documents (Opus output truncation at ~32)
- [ ] Figure generation integration (`/create-figure` skill)
- [ ] Cross-reference resolution in cloned PDFs
- [x] Automated discrepancy detection in `/learn-datalake` supervisor → scanner + profile comparison
- [ ] Wire extraction_scanner into /learn-datalake ingestion pre-hook
- [ ] Add requirement detection (SHALL/MUST/SHOULD) to scanner
- [ ] Document-type presets beyond NIST (ISO, CMMC, FedRAMP)
- [ ] Should the next NIST candidate packet be a broad fresh 30-page stratified sample, or should it target the two known blocked classes first: rotated side-chrome geometry and nested numeric list materialization?
- [ ] Should the next NIST hardening phase implement core table-grid materialization for ruled/shaded NIST tables before running another broad 30-page packet?

## Integration Points

- `/learn-datalake` - Ingests extracted content → ArangoDB /memory → relates to SPARTA Controls
- `/pdf-lab` - PDF debugging, extraction testing, visual inspection
- `/scillm` - LLM batch calls for content generation
- `/review-pdf` - Quality gates on extraction
- `/code-runner` - Second-pass classification when profile mismatch detected
- `/orchestrate` - Runs calibration and quarantine fix plans
- `/memory` - Stores extracted tables/requirements, links to sparta_controls collection

### Table Extraction Validation (2026-04-17)

**Key insight:** PyMuPDF `find_tables()` is unreliable - produces ~30% false positives on NIST documents.

**Solution:** Use `pdf_oxide.survey_document()` as ground truth filter:
- `survey['table_pages']` is the authoritative list of pages with tables
- Only run PyMuPDF table detection on pages in this list
- Additional heuristic filtering for remaining artifacts

**Results on NIST SP 800-53r5:**
| Stage | Tables | Precision | Recall |
|-------|--------|-----------|--------|
| PyMuPDF raw | 62 | 77% | 100% |
| + Heuristic filter | 57 | 83% | 98% |
| + pdf_oxide validation | 47 | **100%** | **98%** |

**Heuristic filters (after pdf_oxide page validation):**
1. Skip tables with <30 chars and no newlines (artifacts)
2. Skip single uppercase phrases <8 words (section headers)

**PyMuPDF limitations:**
- Detects "Discussion:" text layouts as tables
- Detects text with pipe `|` characters as tables
- Fails to find some tables (page 30 in NIST 800-53r5)

**Files modified:**
- `python/pdf_oxide/extract_for_pdflab.py` - Added pdf_oxide survey validation

## Skill Patterns Learned

### scillm Batch Best Practices

```python
# Query optimal chunk size
resp = await client.get(f"http://localhost:4001/v1/scillm/concurrency?model={model}")
chunk_size = resp.json().get("chunk_size", 2)

# Chunked processing with backoff
for attempt in range(max_retries):
    try:
        resp = await client.post(...)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429 and "BUSY" in e.response.text:
            wait = 30 + random.uniform(0, 30) * (attempt + 1)
            await asyncio.sleep(wait)
            continue
        raise
```

### Style Extraction Prompt Structure

V3 prompt pattern for VLM style extraction:
1. Explicit preset closed vocabulary (all 56 presets listed)
2. Tie-break resolution rules for ambiguous matches
3. Fallback defaults when no match found
4. Constraint that selected presets MUST be from the provided list

## Recent Decisions

| Date | Decision | Why |
|------|----------|-----|
| 2026-06-01 | Do not treat one next-30 packet as NIST-wide hardening proof | The next-30 run hardened many observed NIST core/preset/materializer patterns, but two final supplemental blockers still need deeper line-level/oriented geometry work and another stratified 30-page packet is needed before claiming reasonable NIST-wide confidence. |
| 2026-06-01 | Classify fresh NIST follow-on residuals by substrate blockers, not model-finding count | The final v2 rerun still has 26 findings, but final reconciliation maps every finding to a ledger status: table-grid materialization, rotated side-chrome geometry, or nested list segmentation. Closure should be based on the append-only ledger and proof artifacts, not zero findings. |
| 2026-06-04 | Use one-candidate human annotation loop for current PDF Lab hardening | The prior broad goal/packet approach produced drift, false closure pressure, and token-heavy side work. The new default is one selected page/candidate at a time: justify from artifacts, get human annotation, require the scillm/OpenCode executor to write the focused regression before patching, audit deterministic proof, then advance only after the current candidate is proven or explicitly blocked. |
