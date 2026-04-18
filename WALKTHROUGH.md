# pdf_oxide: Honest State Assessment

**Date:** 2026-04-16  
**Status:** Confused - architecture mixed up by recent ad-hoc changes  
**Reviewed by:** Self-assessment (no persona consulted yet)

---

## What pdf_oxide Actually Is

pdf_oxide is a **Rust PDF parsing library** with Python bindings. The core purpose:

```
PDF bytes → Parsed structure → Extracted content (text, tables, images)
```

### The Rust Core (`src/`)

This is the foundation - **well-architected and documented**:

| Component | File | Purpose |
|-----------|------|---------|
| Lexer | `src/lexer.rs` | Tokenize PDF bytes |
| Parser | `src/parser.rs` | Parse PDF objects |
| Stream Decoder | `src/stream_decoder.rs` | Decompress (Flate, LZW, etc.) |
| Layout Analysis | `src/layout/` | DBSCAN clustering, XY-Cut segmentation |
| Text Extraction | `src/text.rs` | Characters → words → lines → text |
| Font Parser | `src/font.rs` | Handle encodings and metrics |
| Exporters | `src/converters/` | Markdown, HTML, plain text |

**Quality metrics from docs:** 99.8/100 Markdown, 100/100 whitespace, O(n log n) layout

### Python Bindings (`python/pdf_oxide/`)

| File | Purpose | Status |
|------|---------|--------|
| `pdf_oxide.py` | PyO3 bindings to Rust | Works |
| `survey.py` | Document profiling | Works |
| `survey_text.py` | Text-based analysis helpers | Works |
| `pipeline.py` | Main extraction entry point | Works |
| `pipeline_extract.py` | Extraction implementation | **BROKEN - I added wrong code** |
| `clone_profiler.py` | Profile PDFs for cloning | Works |
| `clone/` | PDF cloning module | Complex, partially works |

---

## The Clone Pipeline (Self-Improvement Loop)

**Original intent:** Generate PDFs with known structure (QID markers) to validate extraction accuracy.

```
Source PDF → Profile → Generate Clone with QIDs → Extract Clone → Compare to Truth → Improve Extractor
```

### Components

1. **`clone_profiler.py`** - Analyzes source PDF
   - Extracts PDF outline via pypdf (`_normalize_outline_items`)
   - Parses visible TOC pages
   - Counts control IDs, clauses, tables, figures
   - Returns structured profile dict

2. **`clone/clone_builder.py`** - Builds clone PDFs
   - QID allocation system
   - ReportLab-based PDF generation
   - Truth manifest output

3. **`clone/clone_validate.py`** - Validates extraction
   - Compares extracted content to truth manifest
   - QID recovery checking
   - Grid/table validation

---

## What I Broke

### The `pipeline_extract.py` Mess

I added a NIST-specific control ID scan to `_build_sections()`:

```python
# Lines 82-124 - THIS DOESN'T BELONG HERE
# Second pass: augment with control ID headers from blocks
_CONTROL_ID_RE = re.compile(r"([A-Z]{2}-\d+...)")
for page_data in raw.get("pages", []):
    for blk in page_data.get("blocks", []):
        match = _CONTROL_ID_RE.match(text)
        if match:
            sections.append(...)  # WRONG - control IDs are not sections
```

**The problem:** I confused two different concepts:

| Concept | What It Is | Where It Belongs |
|---------|------------|------------------|
| **Sections** | Document structure from TOC/outline | `doc.get_outline()`, Rust `build_sections` |
| **Control IDs** | NIST-specific entities (AC-1, SI-7) | Entity extraction, NOT section detection |

### What Sections Actually Are

1. **PDF Outline** - `doc.get_outline()` returns embedded bookmarks (11 entries for NIST 800-53)
2. **TOC Parsing** - `survey.py` parses visible TOC pages
3. **Font-based detection** - Rust `build_sections=True` uses font size/bold

### What Control IDs Are

Control IDs (AC-1, SI-7, PM-11) are **entities** specific to NIST compliance docs. They should be:
- Extracted via `/extract-entities` skill
- Counted by profiler as a metric
- NOT treated as document sections

---

## Current Section Detection Flow

### Profiler (`clone_profiler.py`) - CORRECT

```python
# Uses PDF outline (bookmarks)
outline_tree = _normalize_outline_items(reader)

# Uses TOC text parsing
toc_sections = [...]  # from survey

# Counts control IDs as SEPARATE metric
control_ids: set[str] = set()
for line in text:
    match = _control_id_pat.match(line)
    if match:
        control_ids.add(match.group(1))

result["control_id_count"] = len(control_ids)  # Just a count, not sections
```

### Extractor (`pipeline_extract.py`) - WRONG

```python
def _build_sections(raw):
    # First pass: Rust-detected sections (correct)
    for s in raw.get("sections", []):
        sections.append(...)

    # Second pass: WRONG - scans for control IDs
    for blk in blocks:
        if _CONTROL_ID_RE.match(text):
            sections.append(...)  # Treating control IDs as sections
```

---

## What Should Be Fixed

### 1. Remove control ID scan from `_build_sections()`

The extractor should only get sections from:
- Rust's `build_sections=True` (font/style detection)
- PDF outline (`doc.get_outline()`)

Control IDs are entities, not sections.

### 2. Use `/extract-entities` for control IDs

NIST control IDs should be extracted separately:
```bash
/extract-entities --type control_id /path/to/nist.pdf
```

### 3. Clarify profiler vs extractor roles

| Component | Role | Output |
|-----------|------|--------|
| **Profiler** | Estimate document structure for cloning | Profile dict with counts/estimates |
| **Extractor** | Extract actual content with positions | Sections, blocks, tables, figures |
| **Validator** | Compare extraction to truth manifest | Pass/fail with metrics |

---

## What I Don't Know

1. **Rust `build_sections` behavior** - What exactly does it detect? Font-based? I saw it return 674 sections for NIST but none with numbering.

2. **Clone pipeline completeness** - Is the clone/ module actually working end-to-end? I haven't run a full clone→extract→validate cycle.

3. **Table detection accuracy** - Profiler says 47 table pages, extractor found 49. Close but not identical.

4. **Survey vs profiler relationship** - Both analyze PDFs. What's the difference? When to use which?

---

## Files Changed Recently (git status)

```
M  python/pdf_oxide/clone_pdf.py
M  python/pdf_oxide/clone_profiler.py
M  python/pdf_oxide/pipeline_extract.py  ← I broke this
M  tests/test_clone_additive.py
?? python/pdf_oxide/clone/
?? python/pdf_oxide/clone_v3/
?? python/pdf_oxide/clone_v4.py
?? python/pdf_oxide/clone_v5.py
?? tests/test_profiler_extractor_convergence.py  ← I added this
```

---

## Recommended Next Steps

1. **Revert my `pipeline_extract.py` changes** - Remove the control ID regex scan
2. **Understand Rust section detection** - Read `src/layout/` to understand what `build_sections` does
3. **Test clone pipeline end-to-end** - Does clone→extract→validate work?
4. **Document the actual architecture** - Update this walkthrough with verified information

---

## Bottom Line

**State:** Confused. I made changes without fully understanding the architecture.

**What I broke:** Mixed up sections (document structure) with control IDs (NIST entities) in `pipeline_extract.py`.

**What works:** The Rust core, survey module, and profiler appear to be well-designed. The clone/ module has a clear architecture but I haven't verified it works.

**What's next:** Need to understand the actual section detection flow and fix what I broke.
