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
    """Check if text is likely a sentence (body text) rather than a title.

    Titles are typically short and don't contain sentence-like patterns.
    Body text has verbs, is longer, and reads like a sentence.
    """
    # Long text is likely a sentence
    if len(text) > 150:
        return True

    # Contains sentence indicators (common verbs/phrases in body text)
    sentence_indicators = [
        r'\bis\b', r'\bare\b', r'\bwas\b', r'\bwere\b',
        r'\bprovides?\b', r'\bdescribes?\b', r'\bdefines?\b',
        r'\bincludes?\b', r'\bcontains?\b', r'\baddresses\b',
        r'\bensures?\b', r'\bdirected\b', r'\brequires?\b',
        r'\bshall\b', r'\bmust\b', r'\bshould\b', r'\bmay\b',
        r'\bestablishes?\b', r'\bimplements?\b', r'\bmaintains?\b',
    ]
    text_lower = text.lower()
    for pattern in sentence_indicators:
        if re.search(pattern, text_lower):
            return True

    # Multiple sentences (period followed by capital letter)
    if re.search(r'\.\s+[A-Z]', text):
        return True
    
    # Contains common sentence connectors
    connectors = [
        r'\band\b', r'\bor\b', r'\bbut\b', r'\bhowever\b', r'\btherefore\b',
        r'\bmoreover\b', r'\bfurthermore\b', r'\bin addition\b', r'\bfor example\b',
        r'\bsuch as\b', r'\bincluding\b', r'\bas well as\b'
    ]
    for pattern in connectors:
        if re.search(pattern, text_lower):
            return True
    
    # Contains pronouns (common in sentences, rare in titles)
    pronouns = [r'\bthis\b', r'\bthat\b', r'\bthese\b', r'\bthose\b', r'\bit\b', r'\bthey\b']
    for pattern in pronouns:
        if re.search(pattern, text_lower):
            return True
    
    # Ends with punctuation that suggests a complete sentence
    if re.search(r'[.!?]\s*$', text):
        return True

    return False


def classify_block(text: str) -> str:
    """Classify a block based on its text content."""
    clean_text = text.strip()

    # Page numbers - be more specific to avoid false positives
    if re.match(r'^Page\s+\d+(?:\s+of\s+\d+)?$', clean_text, re.I):
        return 'page_number'
    
    # Also catch page numbers at end of line (common pattern)
    if re.match(r'.*\bPage\s+\d+(?:\s+of\s+\d+)?\s*$', clean_text, re.I) and len(clean_text) < 50:
        return 'page_number'

    # Running headers (page chrome with underlines) - check before other patterns
    for pattern in RUNNING_HEADER_PATTERNS:
        if re.search(pattern, clean_text, re.I):
            return 'boilerplate'

    # Boilerplate
    if clean_text in BOILERPLATE:
        return 'boilerplate'

    # Header continuations
    if clean_text in HEADER_CONTINUATIONS:
        return 'header'

    # NIST SP document titles (not running headers - those have underlines)
    if re.match(r'^NIST\s+SP', clean_text, re.I):
        # But make sure it's not a sentence about NIST SP
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Detect table content patterns (common in table cells)
    table_indicators = [
        r'\|\s*\w+\s*\|',  # pipe-separated content
        r'^\s*\w+\s*\|\s*\w+',  # starts with word | word
        r'\w+\s*\|\s*\w+\s*$',  # ends with word | word
        r'^\d+\s*\|\s*\w+',  # starts with number | word
        r'^[\w\s]+\|\s*[\w\s]+\|',  # multiple pipe separators
        r'^\s*\|\s*[\w\s]+\|\s*$',  # content surrounded by pipes
    ]
    
    # Check if this looks like table content
    for pattern in table_indicators:
        if re.search(pattern, clean_text):
            # Additional check: if it's structured and has table-like formatting
            if '|' in clean_text and (len(clean_text) < 200 or clean_text.count('|') >= 2):
                return 'table'
    
    # Additional table detection: structured data with consistent formatting
    lines = clean_text.split('\n')
    if len(lines) >= 2:
        # Check if multiple lines have similar structure (potential table rows)
        pipe_counts = [line.count('|') for line in lines if line.strip()]
        if pipe_counts and len(set(pipe_counts)) <= 2 and max(pipe_counts) >= 2:
            return 'table'
    # Chapter/section headers (including APPENDIX) - but not if followed by sentence
    if re.match(r'^(CHAPTER|INTRODUCTION|THE FUNDAMENTALS|THE CONTROLS|APPENDIX)', clean_text, re.I):
        # Check if this is a sentence (body text) vs a title (header)
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Numbered sections
    if re.match(r'^\d+\.\d+\s+[A-Z]', clean_text):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Control IDs - but not if followed by sentence
    control_pattern = '|'.join(CONTROL_FAMILIES)
    if re.match(rf'^({control_pattern})-\d+', clean_text):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Control enhancements like "(1) ACCOUNT MANAGEMENT | AUTOMATED..."
    if re.match(r'^\(\d+\)\s+[A-Z]', clean_text):
        if not _is_likely_sentence(clean_text):
            return 'header'

    # Table of Contents header
    if 'Table of Contents' in clean_text:
        return 'header'

    # Simple section names (synchronized with classify_toc_title)
    if clean_text.upper() in SIMPLE_HEADERS:
        return 'header'

    # Additional header patterns for titles that might be missed
    # Short, all-caps text that looks like a title
    if (len(clean_text) < 80 and 
        clean_text.isupper() and 
        not _is_likely_sentence(clean_text) and
        not re.match(r'^Page\s+\d+', clean_text, re.I)):
        return 'header'

    # Detect section titles that start with capital letters and are relatively short
    if (re.match(r'^[A-Z][A-Z\s]+$', clean_text) and 
        len(clean_text) < 60 and 
        not _is_likely_sentence(clean_text)):
        return 'header'
    
    # Improve detection of headers that might be misclassified as text
    # Look for patterns that are likely headers but might not match above rules
    
    # Short text with title-case that doesn't look like a sentence
    if (len(clean_text) < 100 and 
        re.match(r'^[A-Z][a-z]+(?:\s+[A-Z][a-z]*)*$', clean_text) and
        not _is_likely_sentence(clean_text) and
        not re.search(r'\b(the|and|or|of|in|on|at|to|for|with|by)\b', clean_text.lower())):
        return 'header'
    
    # Text that looks like a section or subsection title
    if (len(clean_text) < 120 and
        re.match(r'^[A-Z]', clean_text) and
        clean_text.count('.') <= 1 and  # Not multiple sentences
        not _is_likely_sentence(clean_text) and
        not re.search(r'\b(this|that|these|those|which|where|when|how)\b', clean_text.lower())):
        # Additional check: does it end with a period? If so, less likely to be header
        if not clean_text.endswith('.'):
            return 'header'

    return 'text'


def parse_toc_entry(text: str) -> dict | None:
    """Parse a TOC entry, returning title and page if matched."""
    # Pattern 1: "Title ... page" (3+ dots)
    # Pattern 2: "Title . page" (single dot + space + number)
    match = re.match(r'^(.+?)(?:\s*\.{3,}\s*|\s+\.\s+)(\d+)$', text)
    if match:
        return {'title': match.group(1).strip(), 'page': int(match.group(2))}
    return None


def boxes_overlap(box1, box2, threshold=0.5) -> bool:
    """Check if two bboxes overlap significantly."""
    x1_1, y1_1, x2_1, y2_1 = box1
    x1_2, y1_2, x2_2, y2_2 = box2

    x_left = max(x1_1, x1_2)
    y_top = max(y1_1, y1_2)
    x_right = min(x2_1, x2_2)
    y_bottom = min(y2_1, y2_2)

    if x_right < x_left or y_bottom < y_top:
        return False

    intersection = (x_right - x_left) * (y_bottom - y_top)
    area1 = (x2_1 - x1_1) * (y2_1 - y1_1)

    if area1 == 0:
        return False

    return intersection / area1 > threshold


# =============================================================================
# Extraction
# =============================================================================

def build_section_ranges(toc_entries: list[dict], effective_page_count: int) -> list[dict]:
    """Build non-decreasing section ranges from TOC entries.

    Ranges are clamped to [0, effective_page_count-1] and never produce end < start.
    """
    headers = [e for e in toc_entries if e.get('type') == 'header' and isinstance(e.get('page'), int)]
    headers.sort(key=lambda e: e['page'])

    ranges = []
    if effective_page_count <= 0:
        return ranges

    for i, entry in enumerate(headers):
        start = max(0, min(entry['page'] - 1, effective_page_count - 1))
        if i + 1 < len(headers):
            next_start = max(0, min(headers[i + 1]['page'] - 1, effective_page_count - 1))
            end = max(start, next_start - 1)
        else:
            end = effective_page_count - 1

        ranges.append({'title': entry['title'], 'start': start, 'end': end})

    return ranges


def section_type_for_page(page_num: int, section_ranges: list[dict]) -> str | None:
    """Map page index to coarse section type using TOC-derived ranges."""
    for section in section_ranges:
        if section['start'] <= page_num <= section['end']:
            title = section['title'].upper()
            if 'ERRATA' in title:
                return 'errata'
            if 'GLOSSARY' in title:
                return 'glossary'
            if 'SUMMARY' in title and ('CONTROL' in title or 'APPENDIX' in title or 'SUMMARIES' in title):
                return 'summaries'
                return 'summaries'
    return None


def _merge_bracket_citation_rows(table_text: str) -> str:
    lines = [ln for ln in table_text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return table_text

    merged = [lines[0]]
    citation_re = re.compile(r'^\[\s*[^\]]+\s*\]$')

    for line in lines[1:]:
        stripped = line.strip()
        if citation_re.match(stripped) and len(merged) > 1 and '|' in merged[-1]:
            merged[-1] = f"{merged[-1]} {stripped}"
        else:
            merged.append(line)

    return '\n'.join(merged)


def extract_pdf(pdf_path: str, output_path: str | None = None, max_pages: int | None = None) -> dict:
    """Extract PDF content for PDF Lab UI."""
    doc = fitz.open(pdf_path)
    pdf_name = Path(pdf_path).name

    effective_page_count = min(doc.page_count, max_pages) if max_pages else doc.page_count

    blocks = []
    block_id = 0
    toc_entries_all = []

    print(f'Extracting {effective_page_count} pages from {pdf_name}...')

    for page_num in range(effective_page_count):
        if page_num % 50 == 0:
            print(f'  Page {page_num}...')

        page = doc[page_num]
        page_width, page_height = page.rect.width, page.rect.height

        tables = page.find_tables()
        table_bboxes = [t.bbox for t in tables.tables]

        for i, table in enumerate(tables.tables):
            bbox = table.bbox
            norm_bbox = [bbox[0] / page_width, bbox[1] / page_height, bbox[2] / page_width, bbox[3] / page_height]
            table_text = None

            try:
                table_data = table.extract()
                if table_data and any(any(cell for cell in row if cell and str(cell).strip()) for row in table_data):
                    table_text_parts = []
                    for row in table_data:
                        if row:
                            row_cells = [str(cell).strip() for cell in row if cell and str(cell).strip()]
                            if row_cells:
                                table_text_parts.append(' | '.join(row_cells))
                    if table_text_parts:
                        table_text = '\n'.join(table_text_parts)
            except Exception:
                pass

            if not table_text:
                try:
                    table_rect = fitz.Rect(bbox)
                    extracted_text = page.get_text('text', clip=table_rect).strip()
                    if extracted_text and len(extracted_text) > 10:
                        cleaned_text = re.sub(r'\n\s*\n\s*\n+', '\n\n', extracted_text)
                        cleaned_text = re.sub(r'[ \t]+', ' ', cleaned_text)
                        table_text = cleaned_text.strip()
                except Exception:
                    pass

            if not table_text:
                table_text = f'[Table {i+1}: {table.row_count} rows x {table.col_count} cols]'

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

        text_dict = page.get_text('dict', flags=fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_SPANS)

        for blk in text_dict.get('blocks', []):
            if blk.get('type') != 0:
                continue

            bbox = blk['bbox']
            if any(boxes_overlap(bbox, tb, 0.7) for tb in table_bboxes):
                continue

            text_parts = []
            for line in blk.get('lines', []):
                line_text = ''.join(span.get('text', '') for span in line.get('spans', []) if span.get('text', ''))
                if line_text:
                    text_parts.append(line_text)

            text = re.sub(r'[ \t]+', ' ', re.sub(r'\n\s*\n\s*\n+', '\n\n', '\n'.join(text_parts))).strip()
            if not text:
                continue

            norm_bbox = [bbox[0] / page_width, bbox[1] / page_height, bbox[2] / page_width, bbox[3] / page_height]
            block_type = classify_block(text)
            toc_entries = None

            if '...' in text or re.search(r'\s+\.\s+\d+', text):
                parsed_entries = []
                for line in text.split('\n'):
                    entry = parse_toc_entry(line.strip())
                    if entry:
                        entry_type = classify_toc_title(entry['title'])
                        parsed = {'title': entry['title'], 'page': entry['page'], 'type': entry_type}
                        parsed_entries.append(parsed)
                        toc_entries_all.append(parsed)

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

    section_ranges = build_section_ranges(toc_entries_all, effective_page_count)

    for block in blocks:
        if block.get('blockType') != 'table':
            continue
        section_type = section_type_for_page(block.get('page', -1), section_ranges)
        if section_type in {'glossary', 'acronyms'}:
            block['text'] = _merge_bracket_citation_rows(block.get('text', ''))

    result = {'pdfUrl': f'/{pdf_name}', 'pageCount': effective_page_count, 'blocks': blocks}

    type_counts = {}
    for b in blocks:
        t = b['blockType']
        type_counts[t] = type_counts.get(t, 0) + 1

    print('\nExtraction complete:')
    for t, c in sorted(type_counts.items()):
        print(f'  {t}: {c}')

    if output_path:
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2)
        print(f'\nSaved to: {output_path}')

    return result


# =============================================================================
# CLI
# =============================================================================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Extract PDF for PDF Lab UI')
    parser.add_argument('pdf_path', help='Path to PDF file')
    parser.add_argument('-o', '--output', help='Output JSON path')

    args = parser.parse_args()

    output = args.output
    if not output:
        pdf_name = Path(args.pdf_path).stem
        output = f'{pdf_name}-extraction.json'

    extract_pdf(args.pdf_path, output)
