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
