"""PDF extraction for PDF Lab UI.

Creates extraction JSON compatible with ux-lab/PdfLabView.
Uses PyMuPDF (fitz) for text/table extraction and Python classification.

This is the CANONICAL source for classification logic.
tests/test_extraction_classification.py mirrors these functions.
"""
import json
import re
from pathlib import Path
import fitz

import pdf_oxide


# =============================================================================
# Classification Constants (keep in sync with test_extraction_classification.py)
# =============================================================================

HEADER_CONTINUATIONS = [
    'Systems and Organizations',
    'Information Systems and Organizations',
]

SIMPLE_HEADERS = ['ABSTRACT', 'KEYWORDS', 'ERRATA', 'REFERENCES', 'GLOSSARY', 'ACRONYMS']

BOILERPLATE = ['NIST Special Publication', 'Document Title Page 1']

# Running header patterns (page chrome that repeats)
RUNNING_HEADER_PATTERNS = [
    r'^NIST\s+SP\s+800-\d+.*_{10,}',  # NIST SP with underline
    r'_{50,}$',  # Long underline at end
]

# Control family prefixes (NIST SP 800-53)
CONTROL_FAMILIES = [
    'AC', 'AT', 'AU', 'CA', 'CM', 'CP', 'IA', 'IR', 'MA', 'MP',
    'PE', 'PL', 'PM', 'PS', 'PT', 'RA', 'SA', 'SC', 'SI', 'SR'
]


# =============================================================================
# Classification Functions
# =============================================================================

def classify_toc_title(title: str) -> str:
    """Classify a TOC entry title as header or text."""
    title = title.strip()

    # Document title (NIST SP...)
    if re.match(r'^NIST\s+SP', title, re.I):
        return 'header'

    # Chapter/section headers (including APPENDIX)
    if re.match(r'^(CHAPTER|INTRODUCTION|THE FUNDAMENTALS|THE CONTROLS|APPENDIX)', title, re.I):
        return 'header'

    # Numbered sections like "1.1 PURPOSE"
    if re.match(r'^\d+\.\d+\s+[A-Z]', title):
        return 'header'

    # Control IDs like "AC-1 POLICY"
    control_pattern = '|'.join(CONTROL_FAMILIES)
    if re.match(rf'^({control_pattern})-\d+', title):
        return 'header'

    # Simple section names
    if title.upper() in SIMPLE_HEADERS:
        return 'header'

    return 'text'


def _is_likely_sentence(text: str) -> bool:
    """Check if text is likely a sentence (body text) rather than a title."""
    # Long text is likely a sentence
    if len(text) > 150:
        return True

    # Contains sentence indicators (common verbs/phrases in body text)
    sentence_indicators = [
        r'\bis\b', r'\bare\b', r'\bwas\b', r'\bwere\b',
        r'\bprovides?\b', r'\bdescribes?\b', r'\bdefines?\b',
        r'\bincludes?\b', r'\bcontains?\b', r'\baddresses\b',
        r'\bensures?\b', r'\brequires?\b', r'\bshall\b', r'\bmust\b'
    ]
    text_lower = text.lower()
    for pattern in sentence_indicators:
        if re.search(pattern, text_lower):
            return True

    # Multiple sentences (period followed by capital letter)
    if re.search(r'\.\s+[A-Z]', text):
        return True

    return False

def classify_block(text: str) -> str:
    """Classify a block based on its text content."""
    clean_text = text.strip()

    # Page numbers
    if re.match(r'^Page\s+\d+(?:\s+of\s+\d+)?$', clean_text, re.I):
        return 'page_number'

    # Running headers (page chrome with underlines)
    for pattern in RUNNING_HEADER_PATTERNS:
        if re.search(pattern, clean_text, re.I):
            return 'boilerplate'

    # Boilerplate
    if clean_text in BOILERPLATE:
        return 'boilerplate'

    # Header continuations
    if clean_text in HEADER_CONTINUATIONS:
        return 'header'

    # NIST SP document titles
    if re.match(r'^NIST\s+SP', clean_text, re.I):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Table content patterns
    if '|' in clean_text and len(clean_text.split('|')) >= 3:
        return 'table'

    # Chapter/section headers
    if re.match(r'^(CHAPTER|INTRODUCTION|THE FUNDAMENTALS|THE CONTROLS|APPENDIX)', clean_text, re.I):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Numbered sections
    if re.match(r'^\d+\.\d+\s+[A-Z]', clean_text):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Control IDs
    control_pattern = '|'.join(CONTROL_FAMILIES)
    if re.match(rf'^({control_pattern})-\d+', clean_text):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Control enhancements
    if re.match(r'^\(\d+\)\s+[A-Z]', clean_text):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Table of Contents header
    if 'Table of Contents' in clean_text:
        return 'header'

    # Simple section names
    if clean_text.upper() in SIMPLE_HEADERS:
        return 'header'

    return 'text'


def parse_toc_entry(line: str) -> dict | None:
    """Parse a TOC line with dot leaders."""
    # Simple pattern: title ... page_number
    match = re.match(r'^(.+?)\.{3,}\s*(\d+)\s*$', line.strip())
    if match:
        return {
            'title': match.group(1).strip(),
            'page': int(match.group(2))
        }
    return None


def boxes_overlap(bbox1, bbox2, threshold=0.1):
    """Check if two bounding boxes overlap significantly."""
    x1_min, y1_min, x1_max, y1_max = bbox1
    x2_min, y2_min, x2_max, y2_max = bbox2

    x_overlap = max(0, min(x1_max, x2_max) - max(x1_min, x2_min))
    y_overlap = max(0, min(y1_max, y2_max) - max(y1_min, y2_min))
    intersection = x_overlap * y_overlap

    area1 = (x1_max - x1_min) * (y1_max - y1_min)
    area2 = (x2_max - x2_min) * (y2_max - y2_min)
    smaller = min(area1, area2)
    if smaller <= 0:
        return False
    return (intersection / smaller) >= threshold


# =============================================================================
# TOC-based section typing
# =============================================================================

def _normalize_toc_entries(toc_entries_or_doc) -> list[dict]:
    """Accept either a TOC entry list or a pdf_oxide document."""
    if hasattr(toc_entries_or_doc, 'get_toc'):
        toc_entries = toc_entries_or_doc.get_toc().get('entries', [])
    elif isinstance(toc_entries_or_doc, dict):
        toc_entries = toc_entries_or_doc.get('entries', [])
    else:
        toc_entries = toc_entries_or_doc
    return [entry for entry in toc_entries if isinstance(entry, dict)]


def _structured_toc_entries(oxide_doc) -> list[dict]:
    """Return pdf_oxide TOC entries with a non-empty title."""
    return [
        entry for entry in _normalize_toc_entries(oxide_doc)
        if (entry.get('title') or entry.get('text') or '').strip()
    ]


def _classify_section_title(title: str) -> str:
    upper = title.upper()
    if 'ERRATA' in upper:
        return 'errata'
    if 'GLOSSARY' in upper:
        return 'glossary'
    if 'ACRONYM' in upper:
        return 'acronyms'
    if ('SUMMARY' in upper or 'SUMMARIES' in upper) and ('CONTROL' in upper or 'APPENDIX' in upper):
        return 'summaries'
    if 'REFERENCE' in upper:
        return 'references'
    return 'body'


def build_section_ranges(toc_entries_or_doc, effective_page_count: int) -> list[dict]:
    """Build non-decreasing section ranges from TOC entries.

    Tests exercise both one-based synthetic TOC pages and zero-based pdf_oxide
    TOC pages, so the base is inferred from the presence of page 0.
    """
    entries = _normalize_toc_entries(toc_entries_or_doc)
    if effective_page_count <= 0:
        return []

    headers = [entry for entry in entries if isinstance(entry.get('page'), int)]
    headers.sort(key=lambda entry: entry['page'])

    if not headers:
        return [{'title': 'document', 'start': 0, 'end': effective_page_count - 1, 'type': 'body'}]

    page_base = 0 if any(entry.get('page') == 0 for entry in headers) else 1
    max_index = effective_page_count - 1
    ranges = []

    for idx, entry in enumerate(headers):
        title = (entry.get('title') or entry.get('text') or '').strip()
        start = max(0, min(entry['page'] - page_base, max_index))

        if idx + 1 < len(headers):
            next_start = max(0, min(headers[idx + 1]['page'] - page_base, max_index))
            end = max(start, next_start - 1)
        else:
            end = max_index

        ranges.append({
            'title': title,
            'start': start,
            'end': end,
            'type': _classify_section_title(title),
        })

    return ranges


def section_type_for_page(page_num: int, ranges: list[dict]) -> str:
    """Look up which section a page belongs to."""
    for section in ranges:
        if section['start'] <= page_num <= section['end']:
            return _classify_section_title(section.get('title', ''))
    return 'body'


def _merge_bracket_citation_rows(table_text: str) -> str:
    """Attach glossary citation rows to the preceding term definition."""
    lines = [line.rstrip() for line in table_text.splitlines() if line.strip()]
    if len(lines) < 2:
        return table_text

    citation_re = re.compile(r'^\[\s*[^\]]+\s*\]$')
    merged = [lines[0]]

    for line in lines[1:]:
        stripped = line.strip()
        if '|' in stripped:
            left, right = [part.strip() for part in stripped.split('|', 1)]
            if citation_re.match(left) and len(merged) > 1 and '|' in merged[-1]:
                prev_left, prev_right = [part.strip() for part in merged[-1].split('|', 1)]
                extra = ' '.join(part for part in (left, right) if part)
                merged[-1] = f'{prev_left} | {prev_right} {extra}'.strip()
                continue
        elif citation_re.match(stripped) and len(merged) > 1 and '|' in merged[-1]:
            prev_left, prev_right = [part.strip() for part in merged[-1].split('|', 1)]
            merged[-1] = f'{prev_left} | {prev_right} {stripped}'.strip()
            continue
        merged.append(stripped)

    return '\n'.join(merged)


def _strip_watermark_phrases(text: str) -> str:
    """Remove watermark boilerplate fragments from extracted table text."""
    cleaned = text
    for phrase in WATERMARK_PHRASES:
        cleaned = cleaned.replace(phrase, '')
    cleaned = re.sub(r' {2,}', ' ', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


WATERMARK_PHRASES = (
    'This publication is available',
    'https://doi.org',
    'free of charge from',
)


def _normalize_chrome_text(text: str) -> str:
    """Normalize text for frequency-based chrome detection."""
    return re.sub(r'\d+', '#', text.strip())[:120]


def detect_doc_chrome(doc, max_pages: int, top_bottom_frac: float = 0.08,
                      min_page_fraction: float = 0.30) -> tuple[set, set]:
    """Document-wide running-chrome and rotated-watermark detection.

    Generalizable (no PDF-specific content). Uses:
      - y-position: text in top/bottom N% of page is chrome candidate.
      - line rotation (line.dir): non-horizontal lines are watermark candidates.
      - frequency: any normalized string recurring in >=min_page_fraction of
        pages is flagged as chrome/watermark.

    Returns (chrome_strings, watermark_strings) as sets of normalized strings.
    """
    from collections import Counter
    top_bottom_counter: Counter = Counter()
    sidebar_counter: Counter = Counter()
    pages_scanned = min(max_pages, doc.page_count)

    for page_num in range(pages_scanned):
        page = doc[page_num]
        h = page.rect.height
        for blk in page.get_text('dict').get('blocks', []):
            if blk.get('type') != 0:
                continue
            for line in blk.get('lines', []):
                spans = line.get('spans', [])
                text = ' '.join(s.get('text', '').strip() for s in spans).strip()
                if not text or len(text) < 3:
                    continue
                norm = _normalize_chrome_text(text)
                bbox = line.get('bbox', (0.0, 0.0, 0.0, 0.0))
                _, y0, _, y1 = bbox
                dx, dy = line.get('dir', (1.0, 0.0))
                if abs(dy) > 0.1:
                    sidebar_counter[norm] += 1
                    continue
                if y1 < h * top_bottom_frac or y0 > h * (1.0 - top_bottom_frac):
                    top_bottom_counter[norm] += 1

    threshold = max(5, int(pages_scanned * min_page_fraction))
    chrome = {s for s, c in top_bottom_counter.items() if c >= threshold}
    watermarks = {s for s, c in sidebar_counter.items() if c >= threshold}
    return chrome, watermarks


def _line_is_rotated(line: dict) -> bool:
    dx, dy = line.get('dir', (1.0, 0.0))
    return abs(dy) > 0.1

# Page-footer row patterns: "APPENDIX X | PAGE N", "CHAPTER N | PAGE M".
_FOOTER_ROW_RE = re.compile(
    r'^\s*(APPENDIX\s+[A-Z]|CHAPTER\s+\w+)\s*\|\s*PAGE\s+\d+\s*$',
    re.IGNORECASE,
)
# Rotated-watermark fragments that leak into leftmost columns (e.g. NIST "53r5").
_SIDEBAR_FRAGMENT_RE = re.compile(r'^\d+[A-Za-z]+\d*$')


def _is_footer_or_watermark_row(row_text: str) -> bool:
    stripped = row_text.strip()
    if not stripped:
        return False
    if _FOOTER_ROW_RE.match(stripped):
        return True
    first_cell = stripped.split('|', 1)[0].strip()
    return bool(_SIDEBAR_FRAGMENT_RE.match(first_cell))


def _strip_sidebar_fragment(cell: str) -> str:
    tokens = cell.split()
    if len(tokens) >= 2 and _SIDEBAR_FRAGMENT_RE.match(tokens[0]):
        return ' '.join(tokens[1:])
    return cell


def extract_pdf(pdf_path: str, output_path: str | None = None, max_pages: int | None = None) -> dict:
    """Extract PDF content for PDF Lab UI - optimized for speed."""
    doc = fitz.open(pdf_path)
    pdf_name = Path(pdf_path).name

    # TOC is the structure source for section-aware extraction.
    oxide_doc = pdf_oxide.open(pdf_path)
    toc_entries = _structured_toc_entries(oxide_doc)

    total_pages = doc.page_count
    if max_pages is not None:
        total_pages = min(total_pages, max_pages)

    section_ranges = build_section_ranges(toc_entries, total_pages)

    # Document-wide chrome/watermark detection (position + rotation + frequency).
    # Generalizable — no PDF-specific strings.
    doc_chrome, doc_watermarks = detect_doc_chrome(doc, total_pages)

    blocks = []
    block_id = 0

    print(f'Extracting {total_pages} pages from {pdf_name}...')
    print(f'  pdf_oxide TOC sections:')
    for r in section_ranges:
        print(f"    pages {r['start']}-{r['end']}: [{r['type']}] {r['title'][:60]}")

    for page_num in range(total_pages):
        if page_num % 50 == 0:
            print(f'  Page {page_num}...')

        page = doc[page_num]
        page_width, page_height = page.rect.width, page.rect.height
        section_type = section_type_for_page(page_num, section_ranges)

        # Borderless tabular sections use the shared Rust definition-list path so
        # glossary/acronym extraction is not reimplemented per caller.
        if section_type in ('glossary', 'acronyms'):
            x_mid_frac = 0.25 if section_type == 'acronyms' else 0.35
            try:
                rust_tables = oxide_doc.extract_tables(
                    page_num,
                    strategy='definition_list',
                    x_mid_ratio=x_mid_frac,
                )
            except Exception:
                rust_tables = []

            for table in rust_tables:
                rows = table.get('data') or []
                table_text_parts = ['TERM | DEFINITION']
                for row in rows:
                    if len(row) < 2:
                        continue
                    term = _strip_sidebar_fragment(_strip_watermark_phrases(str(row[0]).strip()))
                    definition = _strip_watermark_phrases(str(row[1]).strip())
                    if term and definition and not _is_footer_or_watermark_row(f'{term} | {definition}'):
                        table_text_parts.append(f'{term} | {definition}')

                if len(table_text_parts) == 1:
                    continue

                table_text = _merge_bracket_citation_rows('\n'.join(table_text_parts))
                table_text_parts = table_text.splitlines()

                x0, y0, x1, y1 = table.get('bbox', (0.0, 0.0, page_width, page_height))
                norm_bbox = [
                    x0 / page_width,
                    y0 / page_height,
                    x1 / page_width,
                    y1 / page_height,
                ]
                blocks.append({
                    'id': f'block_{block_id}',
                    'page': page_num,
                    'bbox': norm_bbox,
                    'blockType': 'table',
                    'text': '\n'.join(table_text_parts),
                    'qids': None,
                    'tocEntries': None,
                    'confidence': 0.9,
                    'tableKind': section_type,
                })
                block_id += 1
            continue

        table_bboxes = []
        try:
            tables = page.find_tables()
            table_bboxes = [t.bbox for t in tables.tables]

            for i, table in enumerate(tables.tables):
                bbox = table.bbox
                norm_bbox = [
                    bbox[0] / page_width,
                    bbox[1] / page_height,
                    bbox[2] / page_width,
                    bbox[3] / page_height,
                ]

                try:
                    table_data = table.extract()
                    if table_data:
                        table_text_parts = []
                        for row in table_data:
                            if row:
                                row_cells = [str(cell).strip() for cell in row if cell and str(cell).strip()]
                                if row_cells:
                                    table_text_parts.append(' | '.join(row_cells))
                        table_text = '\n'.join(table_text_parts) if table_text_parts else f'[Table {i+1}]'
                    else:
                        table_text = f'[Table {i+1}]'
                except Exception:
                    table_text = f'[Table {i+1}]'

                table_text = _strip_watermark_phrases(table_text)
                table_text = '\n'.join(
                    line for line in table_text.splitlines()
                    if not _is_footer_or_watermark_row(line)
                )

                clean_text = table_text.replace('\n', ' ').strip()
                if len(table_text) < 30 and '\n' not in table_text:
                    continue
                if clean_text.isupper() and len(clean_text.split()) < 8 and '\n' not in table_text:
                    continue

                blocks.append({
                    'id': f'block_{block_id}',
                    'page': page_num,
                    'bbox': norm_bbox,
                    'blockType': 'table',
                    'text': table_text,
                    'qids': None,
                    'tocEntries': None,
                    'confidence': 0.95,
                })
                block_id += 1
        except Exception:
            table_bboxes = []

        # Simple text extraction
        try:
            text_dict = page.get_text('dict')

            for blk in text_dict.get('blocks', []):
                if blk.get('type') != 0:  # Skip image blocks
                    continue

                bbox = blk['bbox']

                # Skip if overlaps with table
                if any(boxes_overlap(bbox, tb, 0.5) for tb in table_bboxes):
                    continue

                # Blocks whose lines are all rotated (sidebar/watermark) → boilerplate.
                lines = blk.get('lines', [])
                if lines and all(_line_is_rotated(ln) for ln in lines):
                    rotated_text_parts = []
                    for line in lines:
                        line_parts = [s.get('text', '').strip() for s in line.get('spans', [])
                                      if s.get('text', '').strip()]
                        if line_parts:
                            rotated_text_parts.append(' '.join(line_parts))
                    rotated_text = '\n'.join(rotated_text_parts).strip()
                    if rotated_text:
                        norm_bbox = [
                            bbox[0] / page_width, bbox[1] / page_height,
                            bbox[2] / page_width, bbox[3] / page_height,
                        ]
                        blocks.append({
                            'id': f'block_{block_id}',
                            'page': page_num,
                            'bbox': norm_bbox,
                            'blockType': 'boilerplate',
                            'text': rotated_text,
                            'qids': None,
                            'tocEntries': None,
                            'confidence': 0.9,
                        })
                        block_id += 1
                    continue

                # Simple text extraction (skip rotated lines within mixed blocks).
                text_parts = []
                for line in lines:
                    if _line_is_rotated(line):
                        continue
                    line_text = []
                    for span in line.get('spans', []):
                        span_text = span.get('text', '').strip()
                        if span_text:
                            line_text.append(span_text)
                    if line_text:
                        text_parts.append(' '.join(line_text))

                text = '\n'.join(text_parts).strip()
                if not text or len(text) < 3:
                    continue

                # Doc-wide chrome filter: recurring top/bottom strings become boilerplate.
                norm = _normalize_chrome_text(text)
                if norm in doc_chrome or norm in doc_watermarks:
                    # Emit as boilerplate so UI can still show, but not as content.
                    norm_bbox = [
                        bbox[0] / page_width, bbox[1] / page_height,
                        bbox[2] / page_width, bbox[3] / page_height,
                    ]
                    blocks.append({
                        'id': f'block_{block_id}',
                        'page': page_num,
                        'bbox': norm_bbox,
                        'blockType': 'boilerplate',
                        'text': text,
                        'qids': None,
                        'tocEntries': None,
                        'confidence': 0.9,
                    })
                    block_id += 1
                    continue

                # Simple bbox normalization
                norm_bbox = [
                    bbox[0] / page_width,
                    bbox[1] / page_height,
                    bbox[2] / page_width,
                    bbox[3] / page_height,
                ]

                # Classify
                block_type = classify_block(text)

                # Simple TOC detection
                toc_entries = None
                if '...' in text:
                    lines = text.split('\n')
                    parsed_entries = []
                    for line in lines[:5]:  # Limit for speed
                        entry = parse_toc_entry(line.strip())
                        if entry:
                            entry_type = classify_toc_title(entry['title'])
                            parsed_entries.append({
                                'title': entry['title'],
                                'page': entry['page'],
                                'type': entry_type,
                            })
                    if parsed_entries:
                        toc_entries = parsed_entries
                        block_type = 'header'

                blocks.append({
                    'id': f'block_{block_id}',
                    'page': page_num,
                    'bbox': norm_bbox,
                    'blockType': block_type,
                    'text': text,
                    'qids': None,
                    'tocEntries': toc_entries,
                    'confidence': 0.95,
                })
                block_id += 1
        except Exception as e:
            print(f'Warning: Error processing page {page_num}: {e}')
            continue

    result = {
        'pdfUrl': f'/{pdf_name}',
        'pageCount': total_pages,
        'blocks': blocks,
    }

    # Print summary
    type_counts = {}
    for b in blocks:
        t = b['blockType']
        type_counts[t] = type_counts.get(t, 0) + 1

    print(f'\nExtraction complete:')
    for t, c in sorted(type_counts.items()):
        print(f'  {t}: {c}')

    if output_path:
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2)
        print(f'\nSaved to: {output_path}')

    return result


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Extract PDF for PDF Lab UI')
    parser.add_argument('pdf_path', help='Path to PDF file')
    parser.add_argument('-o', '--output', help='Output JSON path')
    parser.add_argument('--max-pages', type=int, help='Maximum pages to process (for testing)')

    args = parser.parse_args()

    output = args.output
    if not output:
        pdf_name = Path(args.pdf_path).stem
        output = f'{pdf_name}-extraction.json'

    extract_pdf(args.pdf_path, output, max_pages=args.max_pages)
