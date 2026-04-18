# PDF Oxide - The Fastest PDF Toolkit for Python, Rust, CLI & AI

The fastest PDF library for text extraction, image extraction, and markdown conversion. Rust core with Python bindings, WASM support, CLI tool, and MCP server for AI assistants. 0.8ms mean per document, 5x faster than PyMuPDF, 15x faster than pypdf. 100% pass rate on 3,830 real-world PDFs. MIT licensed.

[![Crates.io](https://img.shields.io/crates/v/pdf_oxide.svg)](https://crates.io/crates/pdf_oxide)
[![PyPI](https://img.shields.io/pypi/v/pdf_oxide.svg)](https://pypi.org/project/pdf_oxide/)
[![PyPI Downloads](https://img.shields.io/pypi/dm/pdf-oxide)](https://pypi.org/project/pdf-oxide/)
[![npm](https://img.shields.io/npm/v/pdf-oxide-wasm)](https://www.npmjs.com/package/pdf-oxide-wasm)
[![Documentation](https://docs.rs/pdf_oxide/badge.svg)](https://docs.rs/pdf_oxide)
[![Build Status](https://github.com/yfedoseev/pdf_oxide/workflows/CI/badge.svg)](https://github.com/yfedoseev/pdf_oxide/actions)
[![License: MIT OR Apache-2.0](https://img.shields.io/badge/License-MIT%20OR%20Apache--2.0-blue.svg)](https://opensource.org/licenses)

## Quick Start

### Python
```python
from pdf_oxide import PdfDocument

doc = PdfDocument("paper.pdf")
text = doc.extract_text(0)
chars = doc.extract_chars(0)
markdown = doc.to_markdown(0, detect_headings=True)

# Single-call document extraction (full pipeline)
result = doc.extract_document()
for section in result["sections"]:
    print(f"{section['title']} (level {section['level']})")
```

```bash
pip install pdf_oxide
```

### Rust
```rust
use pdf_oxide::PdfDocument;

let mut doc = PdfDocument::open("paper.pdf")?;
let text = doc.extract_text(0)?;
let images = doc.extract_images(0)?;
let markdown = doc.to_markdown(0, Default::default())?;
```

```toml
[dependencies]
pdf_oxide = "0.3"
```

### CLI
```bash
pdf-oxide text document.pdf
pdf-oxide markdown document.pdf -o output.md
pdf-oxide search document.pdf "pattern"
pdf-oxide merge a.pdf b.pdf -o combined.pdf
```

```bash
brew install yfedoseev/tap/pdf-oxide
```

### MCP Server (for AI assistants)
```bash
# Install
brew install yfedoseev/tap/pdf-oxide   # includes pdf-oxide-mcp

# Configure in Claude Desktop / Claude Code / Cursor
{
  "mcpServers": {
    "pdf-oxide": { "command": "crgx", "args": ["pdf_oxide_mcp@latest"] }
  }
}
```

## Why pdf_oxide?

- **Fast** -- 0.8ms mean per document, 5x faster than PyMuPDF, 15x faster than pypdf, 29x faster than pdfplumber
- **Reliable** -- 100% pass rate on 3,830 test PDFs, zero panics, zero timeouts
- **Complete** -- Text extraction, image extraction, PDF creation, and editing in one library
- **Intelligent** -- Block classification, TOC detection, section hierarchy, engineering feature detection
- **Multi-platform** -- Rust, Python, JavaScript/WASM, CLI, and MCP server for AI assistants
- **Extensible** -- Python plugin system for post-extraction enrichment (ArangoDB sync, embeddings, taxonomy)
- **Permissive license** -- MIT / Apache-2.0 -- use freely in commercial and open-source projects

## Performance

Benchmarked on 3,830 PDFs from three independent public test suites (veraPDF, Mozilla pdf.js, DARPA SafeDocs). Text extraction libraries only (no OCR). Single-thread, 60s timeout, no warm-up.

### Python Libraries

| Library | Mean | p99 | Pass Rate | License |
|---------|------|-----|-----------|---------|
| **PDF Oxide** | **0.8ms** | **9ms** | **100%** | **MIT** |
| PyMuPDF | 4.6ms | 28ms | 99.3% | AGPL-3.0 |
| pypdfium2 | 4.1ms | 42ms | 99.2% | Apache-2.0 |
| pymupdf4llm | 55.5ms | 280ms | 99.1% | AGPL-3.0 |
| pdftext | 7.3ms | 82ms | 99.0% | GPL-3.0 |
| pdfminer | 16.8ms | 124ms | 98.8% | MIT |
| pdfplumber | 23.2ms | 189ms | 98.8% | MIT |
| markitdown | 108.8ms | 378ms | 98.6% | MIT |
| pypdf | 12.1ms | 97ms | 98.4% | BSD-3 |

### Rust Libraries

| Library | Mean | p99 | Pass Rate | Text Extraction |
|---------|------|-----|-----------|-----------------|
| **PDF Oxide** | **0.8ms** | **9ms** | **100%** | **Built-in** |
| oxidize_pdf | 13.5ms | 11ms | 99.1% | Basic |
| unpdf | 2.8ms | 10ms | 95.1% | Basic |
| pdf_extract | 4.08ms | 37ms | 91.5% | Basic |
| lopdf | 0.3ms | 2ms | 80.2% | No built-in extraction |

### Text Quality

99.5% text parity vs PyMuPDF and pypdfium2 across the full corpus. PDF Oxide extracts text from 7-10x more "hard" files than it misses vs any competitor.

### Corpus

| Suite | PDFs | Pass Rate |
|-------|-----:|----------:|
| [veraPDF](https://github.com/veraPDF/veraPDF-corpus) (PDF/A compliance) | 2,907 | 100% |
| [Mozilla pdf.js](https://github.com/mozilla/pdf.js/tree/master/test/pdfs) | 897 | 99.2% |
| [SafeDocs](https://github.com/pdf-association/safedocs) (targeted edge cases) | 26 | 100% |
| **Total** | **3,830** | **100%** |

100% pass rate on all valid PDFs -- the 7 non-passing files across the corpus are intentionally broken test fixtures (missing PDF header, fuzz-corrupted catalogs, invalid xref streams).

## Features

| Extract | Analyze | Create | Edit |
|---------|---------|--------|------|
| Text & Layout | Block Classification | Documents | Annotations |
| Images | TOC Detection | Tables | Form Fields |
| Tables | Section Hierarchy | Graphics | Bookmarks |
| Forms | Document Profiling | Templates | Links |
| Annotations | Engineering Features | Images | Content |
| Bookmarks | Column Detection | | |

### Document Intelligence

Beyond raw extraction, pdf_oxide provides structural understanding of documents:

- **Block Classification** -- Classifies content blocks into 13 types (Header, Body, Footer, PageNumber, List, Caption, Footnote, Title, Subtitle, TableOfContents, Reference, Equation, Boilerplate) with confidence scores and header level detection (0-6)
- **TOC Detection** -- Geometric span analysis extracts table of contents without regex. Detects indent levels via x-position clustering, right-aligned page numbers, leader dots, and roman numeral front-matter
- **Section Hierarchy** -- Builds ordered section-to-page-span mapping from TOC entries, PDF outlines, or classified blocks. Entry type classification: Section, Figure, Table, Appendix, FrontMatter
- **Document Profiling** -- Classifies domain (academic, defense, engineering, legal), estimates complexity, detects layout type (single/multi-column), and recommends extraction strategy
- **Engineering Feature Detection** -- Detects title blocks, revision tables, drawing borders, CAGE codes, distribution statements, and security markings in defense/engineering documents

## Python API

```python
from pdf_oxide import PdfDocument

doc = PdfDocument("report.pdf")
print(f"Pages: {doc.page_count()}")
print(f"Version: {doc.version()}")

# 1. Single-call document extraction (full pipeline)
result = doc.extract_document()
print(f"Domain: {result['profile']['domain']}")
print(f"Sections: {len(result['sections'])}")
for page in result["pages"]:
    for block in page["blocks"]:
        print(f"  [{block['block_type']}] {block['text'][:80]}")

# 2. Section map (TOC -> outline -> geometric fallback)
section_map = doc.get_section_map()
for entry in section_map:
    print(f"  {'  ' * entry['level']}{entry['title']} (pp. {entry['start_page']}-{entry['end_page']})")

# 3. Document survey (lightweight profiling)
survey = doc.survey_document()
print(f"Has TOC: {survey['has_toc']}, Tables: {survey['has_tables']}")

# 4. Scoped extraction (extract from a specific region)
header = doc.within(0, (0, 700, 612, 92)).extract_text()

# 5. Word-level extraction
words = doc.extract_words(0)
for w in words:
    print(f"{w.text} at {w.bbox}")

# 6. Line-level extraction
lines = doc.extract_text_lines(0)
for line in lines:
    print(f"Line: {line.text}")

# 7. Table extraction
tables = doc.extract_tables(0)
for table in tables:
    print(f"Table with {table.row_count} rows")

# 8. PyMuPDF-compatible dict format
text_dict = doc.extract_text_dict(0)  # blocks/lines/spans with bboxes

# 9. Character-level extraction
chars = doc.extract_chars(0)

# 10. Traditional text extraction
text = doc.extract_text(0)
```

### Rendering

```python
# Render page to PNG/JPEG
png_bytes = doc.render_page(0, dpi=150, format="png")

# Render clipped region
clip = (100, 200, 400, 500)  # x, y, w, h
cropped = doc.render_page_clipped(0, clip, dpi=150)

# SVG export
svg = doc.render_page_to_svg(0)
```

### Form Fields

```python
# Extract form fields
fields = doc.get_form_fields()
for f in fields:
    print(f"{f.name} ({f.field_type}) = {f.value}")

# Fill and save
doc.set_form_field_value("employee_name", "Jane Doe")
doc.set_form_field_value("wages", "85000.00")
doc.save("filled.pdf")
```

### In-Memory Processing

```python
# Load from bytes
with open("report.pdf", "rb") as f:
    doc = PdfDocument.from_bytes(f.read())

# Serialize back to bytes
modified_bytes = doc.to_bytes()
```

## Python Pipeline

For production document processing at scale, pdf_oxide includes a pipeline system with plugin-based enrichment:

```python
from pdf_oxide.pipeline import extract_pdf
from pdf_oxide.pipeline_types import PipelineConfig

config = PipelineConfig(
    sync_to_arango=True,
    generate_embeddings=True,
    extract_requirements=True,
)

result = extract_pdf("document.pdf", config)
print(f"Extracted {len(result.chunks)} chunks from {result.page_count} pages")
```

### Plugins

The pipeline supports post-extraction enrichment via plugins:

| Plugin | Purpose |
|--------|---------|
| `arango` | Sync extracted chunks to ArangoDB for datalake storage |
| `describe` | VLM-powered title/description enrichment for tables and figures |
| `requirements` | Extract SHALL/MUST/MAY requirements with numbering and conditionals |
| `controls` | Map document sections to NIST control frameworks |
| `taxonomy` | Classify chunks by domain taxonomy |
| `embeddings` | Generate vector embeddings for semantic search |
| `lean4` | Export selected sections to Lean4 formal proofs |

## Rust API

```rust
use pdf_oxide::PdfDocument;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut doc = PdfDocument::open("paper.pdf")?;

    // Extract text
    let text = doc.extract_text(0)?;

    // Character-level extraction
    let chars = doc.extract_chars(0)?;

    // Extract images
    let images = doc.extract_images(0)?;

    // Vector graphics
    let paths = doc.extract_paths(0)?;

    Ok(())
}
```

### Form Fields (Rust)

```rust
use pdf_oxide::editor::{DocumentEditor, EditableDocument, SaveOptions};
use pdf_oxide::editor::form_fields::FormFieldValue;

let mut editor = DocumentEditor::open("w2.pdf")?;
editor.set_form_field_value("employee_name", FormFieldValue::Text("Jane Doe".into()))?;
editor.save_with_options("filled.pdf", SaveOptions::incremental())?;
```

## Installation

### Python

```bash
pip install pdf_oxide
```

Wheels available for Linux, macOS, and Windows. Python 3.8-3.14.

### Rust

```toml
[dependencies]
pdf_oxide = "0.3"
```

### JavaScript/WASM

```bash
npm install pdf-oxide-wasm
```

```javascript
const { WasmPdfDocument } = require("pdf-oxide-wasm");
```

### CLI

```bash
brew install yfedoseev/tap/pdf-oxide    # Homebrew (macOS/Linux)
cargo install pdf_oxide_cli             # Cargo
cargo binstall pdf_oxide_cli            # Pre-built binary via cargo-binstall
```

### MCP Server

```bash
brew install yfedoseev/tap/pdf-oxide    # Included with CLI in Homebrew
cargo install pdf_oxide_mcp             # Cargo
```

## CLI

22 commands for PDF processing directly from your terminal:

```bash
pdf-oxide text report.pdf                      # Extract text
pdf-oxide markdown report.pdf -o report.md     # Convert to Markdown
pdf-oxide html report.pdf -o report.html       # Convert to HTML
pdf-oxide info report.pdf                      # Show metadata
pdf-oxide search report.pdf "neural.?network"  # Search (regex)
pdf-oxide images report.pdf -o ./images/       # Extract images
pdf-oxide merge a.pdf b.pdf -o combined.pdf    # Merge PDFs
pdf-oxide split report.pdf -o ./pages/         # Split into pages
pdf-oxide watermark doc.pdf "DRAFT"            # Add watermark
pdf-oxide forms w2.pdf --fill "name=Jane"      # Fill form fields
```

Run `pdf-oxide` with no arguments for interactive REPL mode. Use `--pages 1-5` to process specific pages, `--json` for machine-readable output.

## MCP Server

`pdf-oxide-mcp` lets AI assistants (Claude, Cursor, etc.) extract content from PDFs locally via the [Model Context Protocol](https://modelcontextprotocol.io/).

Add to your MCP client configuration:

```json
{
  "mcpServers": {
    "pdf-oxide": { "command": "crgx", "args": ["pdf_oxide_mcp@latest"] }
  }
}
```

The server exposes an `extract` tool that supports text, markdown, and HTML output formats with optional page ranges and image extraction. All processing runs locally -- no files leave your machine.

## Building from Source

```bash
# Clone and build
git clone https://github.com/yfedoseev/pdf_oxide
cd pdf_oxide
cargo build --release

# Run tests (4,491 lib tests)
cargo test

# Build Python bindings
maturin develop
```

## Test Coverage

4,491 library tests covering:

- TOC extraction (geometric analysis, roman numerals, entry classification, leader stripping)
- Block classification (numbering analysis, header signals, feature extraction)
- Document extraction (profiling, section hierarchy, engineering detection)
- Table extraction (lattice/stream flavors, intersection pipeline, text-edge detection)
- Text extraction (character deduplication, form XObjects, rotated text, UTF-8 safety)
- Rendering (SVG, PNG, JPEG, clipped regions, embedding)
- Encryption, encoding, fonts, compliance, and edge cases

## Architecture

```
pdf_oxide (Rust core)
  src/
    extractors/          # Block classifier, document extractor, figure detector,
                         # engineering features, section hierarchy, profiler
    pipeline/
      converters/
        toc_detector.rs  # Geometric TOC detection + section mapping
    tables/              # Lattice + stream table extraction
    text/                # Word boundary, ligatures, CJK, RTL, hyphenation
    fonts/               # CMap, CID, TrueType, Type1, subsetting
    encryption/          # AES, RC4, certificate-based
    writer/              # PDF creation and modification
    python.rs            # PyO3 bindings (5,500+ lines)

  python/pdf_oxide/      # Python pipeline + plugins
    pipeline.py          # Main extraction orchestrator
    pipeline_extract.py  # Rust extraction wrapper
    pipeline_flatten.py  # Datalake chunk flattening
    plugins/             # Extensible enrichment (arango, describe, embeddings, ...)

  pdf_oxide_cli/         # CLI tool (22 commands)
  pdf_oxide_mcp/         # MCP server for AI assistants
```

## PDF Cloning (Test Fixture Generation)

Generate structurally similar PDFs with known ground truth for extraction testing and training:

```bash
# Clone a PDF with style extraction
python clone_pdf_v2.py --source document.pdf --output clone.pdf --extract-style

# Clone with specific model for content generation
python clone_pdf_v2.py --source document.pdf --output clone.pdf --model sonnet
```

**Use case:** When extraction fails (e.g., 47 tables profiled but only 12 extracted), generate a clone with QID markers as ground truth. If extraction passes on the clone, the issue is PDF-specific. If it fails, there's an extractor bug.

**Pipeline:**
1. Profile source PDF (TOC, tables, page signatures)
2. Extract visual style via VLM (header/footer/table presets)
3. Generate content structure manifest
4. LLM batch generates text/table content
5. Render PDF with embedded QID markers
6. Output TruthManifest JSON for validation

See `python/pdf_oxide/clone/` for the module and `PROJECT_KNOWLEDGE.md` for architecture details.

## Documentation

- **[Full Documentation](https://pdf.oxide.fyi)** - Complete documentation site
- **[Getting Started (Rust)](https://pdf.oxide.fyi/docs/getting-started/rust)** - Rust guide
- **[Getting Started (Python)](https://pdf.oxide.fyi/docs/getting-started/python)** - Python guide
- **[Getting Started (WASM)](https://pdf.oxide.fyi/docs/getting-started/javascript)** - Browser and Node.js guide
- **[Getting Started (CLI)](https://pdf.oxide.fyi/docs/getting-started/cli)** - CLI guide
- **[Getting Started (MCP)](https://pdf.oxide.fyi/docs/getting-started/mcp)** - MCP server for AI assistants
- **[API Docs](https://docs.rs/pdf_oxide)** - Full Rust API reference
- **[Performance Benchmarks](https://pdf.oxide.fyi/docs/performance)** - Full benchmark methodology and results

## Use Cases

- **RAG / LLM pipelines** -- Convert PDFs to clean Markdown for retrieval-augmented generation with LangChain, LlamaIndex, or any framework
- **AI assistants** -- Give Claude, Cursor, or any MCP-compatible tool direct PDF access via the MCP server
- **Document processing at scale** -- Extract text, images, and metadata from thousands of PDFs in seconds
- **Data extraction** -- Pull structured data from forms, tables, and layouts
- **Defense & engineering** -- Extract from MIL-STD documents with title block, revision table, and CAGE code detection
- **Datalake ingestion** -- Pipeline system with ArangoDB sync, embeddings, and taxonomy classification
- **Academic research** -- Parse papers, extract citations, and process large corpora
- **PDF generation** -- Create invoices, reports, certificates, and templated documents programmatically
- **PyMuPDF alternative** -- MIT licensed, 5x faster, no AGPL restrictions

## License

Dual-licensed under [MIT](LICENSE-MIT) or [Apache-2.0](LICENSE-APACHE) at your option. Unlike AGPL-licensed alternatives, pdf_oxide can be used freely in any project -- commercial or open-source -- with no copyleft restrictions.

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

```bash
cargo build && cargo test && cargo fmt && cargo clippy -- -D warnings
```

## Citation

```bibtex
@software{pdf_oxide,
  title = {PDF Oxide: Fast PDF Toolkit for Rust and Python},
  author = {Yury Fedoseev},
  year = {2025},
  url = {https://github.com/yfedoseev/pdf_oxide}
}
```

---

**Rust** + **Python** + **WASM** + **CLI** + **MCP** | MIT/Apache-2.0 | 100% pass rate on 3,830 PDFs | 0.8ms mean | 5x faster than PyMuPDF | 4,491 tests
