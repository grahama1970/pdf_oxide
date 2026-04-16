"""Extraction Scanner - Self-improvement loop for PDF extraction.

Iterates over extracted pages and flags potential classification errors.
Supports random sampling stratified by TOC sections.
"""
import json
import re
import random
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class ExtractionIssue:
    """A potential extraction error."""
    page: int
    block_id: str
    issue_type: str
    actual_type: str
    expected_type: str | None
    text_snippet: str
    reason: str


@dataclass
class ScanReport:
    """Report of all issues found."""
    total_pages: int
    pages_scanned: int
    total_blocks: int
    blocks_scanned: int
    issues: list[ExtractionIssue] = field(default_factory=list)

    def summary(self) -> dict:
        by_type = defaultdict(list)
        for issue in self.issues:
            by_type[issue.issue_type].append(issue)
        return {
            'total_pages': self.total_pages,
            'pages_scanned': self.pages_scanned,
            'total_blocks': self.total_blocks,
            'blocks_scanned': self.blocks_scanned,
            'issue_count': len(self.issues),
            'by_type': {k: len(v) for k, v in by_type.items()},
        }

    def print_report(self):
        print(f"\n{'='*60}")
        print(f"EXTRACTION SCAN REPORT")
        print(f"{'='*60}")
        print(f"Pages: {self.pages_scanned}/{self.total_pages} scanned")
        print(f"Blocks: {self.blocks_scanned}/{self.total_blocks} scanned")
        print(f"Issues found: {len(self.issues)}")
        print()

        by_type = defaultdict(list)
        for issue in self.issues:
            by_type[issue.issue_type].append(issue)

        for issue_type, issues in sorted(by_type.items()):
            print(f"\n## {issue_type} ({len(issues)} issues)")
            print("-" * 40)
            for issue in issues[:5]:  # Show first 5 of each type
                snippet = issue.text_snippet[:60] + "..." if len(issue.text_snippet) > 60 else issue.text_snippet
                print(f"  Page {issue.page}, {issue.block_id}: {issue.actual_type} -> {issue.expected_type or '?'}")
                print(f"    Text: {snippet!r}")
                print(f"    Reason: {issue.reason}")
def _is_likely_sentence(text: str) -> bool:
    """Check if text is likely a sentence (body text) rather than a title.

    Must match extract_for_pdflab.py's logic.
    """
    import re

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


def _is_likely_sentence(text: str) -> bool:
    """Check if text is likely a sentence (body text) rather than a title.

    Must match extract_for_pdflab.py's logic.
    """
    import re

    # Long text is likely a sentence
    if len(text) > 150:
        return True

    # Contains sentence indicators (common verbs/phrases in body text)
    sentence_indicators = [
        r'\bis\b', r'\bare\b', r'\bwas\b', r'\bwere\b',
        r'\bprovides?\b', r'\bdescribes?\b', r'\bdefines?\b',
        r'\bincludes?\b', r'\bcontains?\b', r'\baddresses\b',
        r'\bensures?\b', r'\bdirected\b', r'\brequires?\b',
    ]
    text_lower = text.lower()
    for pattern in sentence_indicators:
        if re.search(pattern, text_lower):
            return True

    # Multiple sentences (period followed by capital letter)
    if re.search(r'\.\s+[A-Z]', text):
        return True

    return False


def should_be_header(text: str) -> tuple[bool, str]:
    """Check if text matches header patterns.

    Returns (True, reason) only if text matches header pattern AND
    is not likely a sentence (body text that happens to contain a header pattern).
    """
    text = text.strip()

    # If it looks like a sentence, it's not a header even if it matches patterns
    if _is_likely_sentence(text):
        return False, ''

    # Check regex patterns
    for pattern, reason in HEADER_PATTERNS:
        if re.match(pattern, text, re.I):
            return True, reason

    # Check simple headers
    if text.upper() in SIMPLE_HEADERS:
        return True, 'Simple section name'

    # Check continuations
    if text in HEADER_CONTINUATIONS:
        return True, 'Header continuation'

    return False, ''


def should_be_page_number(text: str) -> bool:
    """Check if text matches page number pattern."""
    return bool(re.match(PAGE_NUMBER_PATTERN, text.strip(), re.I))


def scan_block(block: dict) -> ExtractionIssue | None:
    """Scan a single block for potential issues."""
    text = block.get('text', '').strip()
    block_type = block.get('blockType', 'unknown')
    block_id = block.get('id', 'unknown')
    page = block.get('page', -1)

    # Skip empty blocks
    if not text:
        return None

    # Check: text matches header pattern but classified as 'text'
    if block_type == 'text':
        is_header, reason = should_be_header(text)
        if is_header:
            return ExtractionIssue(
                page=page,
                block_id=block_id,
                issue_type='missed_header',
                actual_type=block_type,
                expected_type='header',
                text_snippet=text,
                reason=reason,
            )

        # Check: page number pattern classified as text
        if should_be_page_number(text):
            return ExtractionIssue(
                page=page,
                block_id=block_id,
                issue_type='missed_page_number',
                actual_type=block_type,
                expected_type='page_number',
                text_snippet=text,
                reason='Matches page number pattern',
            )

    # Check: header that's suspiciously long (might be body text)
    # Exception: TOC blocks with dot leaders are correctly long headers
    is_toc_block = '...' in text or re.search(r'\.{3,}', text)
    if block_type == 'header' and len(text) > 200 and not is_toc_block:
        return ExtractionIssue(
            page=page,
            block_id=block_id,
            issue_type='suspicious_long_header',
            actual_type=block_type,
            expected_type='text?',
            text_snippet=text,
            reason=f'Header is {len(text)} chars - possibly misclassified body text',
        )

    # Check: very short text blocks (might be fragmented)
    if block_type == 'text' and len(text) < 20 and not text.isdigit():
        # This is a soft flag - might be legitimate
        pass  # Don't flag for now, too noisy

    return None


def parse_toc_sections(blocks: list[dict]) -> list[dict]:
    """Extract TOC entries to understand document structure."""
    toc_sections = []

    for block in blocks:
        toc_entries = block.get('tocEntries')
        if toc_entries:
            for entry in toc_entries:
                toc_sections.append({
                    'title': entry.get('title', ''),
                    'page': entry.get('page', 0),
                    'qid': entry.get('qid', ''),
                })

    return toc_sections


def get_pages_by_toc_section(toc_sections: list[dict], total_pages: int) -> dict[str, list[int]]:
    """Group pages by their TOC section."""
    if not toc_sections:
        return {'all': list(range(total_pages))}

    # Sort by page number
    sorted_toc = sorted(toc_sections, key=lambda x: x['page'])

    sections = {}
    for i, entry in enumerate(sorted_toc):
        start_page = entry['page'] - 1  # Convert to 0-indexed
        if i + 1 < len(sorted_toc):
            end_page = sorted_toc[i + 1]['page'] - 1
        else:
            end_page = total_pages

        title = entry['title']
        pages = list(range(max(0, start_page), min(end_page, total_pages)))
        if pages:
            sections[title] = pages

    return sections


def sample_pages_stratified(
    sections: dict[str, list[int]],
    n_per_section: int = 2,
    seed: int | None = None
) -> list[int]:
    """Sample pages stratified by TOC section."""
    if seed is not None:
        random.seed(seed)

    sampled = set()
    for section_name, pages in sections.items():
        if pages:
            n = min(n_per_section, len(pages))
            sampled.update(random.sample(pages, n))

    return sorted(sampled)


def sample_pages_random(total_pages: int, n: int = 20, seed: int | None = None) -> list[int]:
    """Sample N random pages."""
    if seed is not None:
        random.seed(seed)
    return sorted(random.sample(range(total_pages), min(n, total_pages)))


def scan_extraction(
    extraction: dict,
    pages: list[int] | None = None,
    sample_mode: str = 'all',  # 'all', 'random', 'stratified'
    sample_size: int = 20,
    seed: int | None = None,
) -> ScanReport:
    """Scan extraction for potential issues.

    Args:
        extraction: The extraction JSON dict
        pages: Specific pages to scan (overrides sample_mode)
        sample_mode: 'all', 'random', or 'stratified'
        sample_size: Number of pages to sample (for random) or per-section (for stratified)
        seed: Random seed for reproducibility

    Returns:
        ScanReport with all issues found
    """
    blocks = extraction.get('blocks', [])
    total_pages = extraction.get('pageCount', 0)

    # Determine which pages to scan
    if pages is not None:
        pages_to_scan = set(pages)
    elif sample_mode == 'all':
        pages_to_scan = set(range(total_pages))
    elif sample_mode == 'random':
        pages_to_scan = set(sample_pages_random(total_pages, sample_size, seed))
    elif sample_mode == 'stratified':
        toc_sections = parse_toc_sections(blocks)
        sections_by_page = get_pages_by_toc_section(toc_sections, total_pages)
        pages_to_scan = set(sample_pages_stratified(sections_by_page, sample_size, seed))
    else:
        pages_to_scan = set(range(total_pages))

    # Scan blocks
    issues = []
    blocks_scanned = 0

    for block in blocks:
        page = block.get('page', -1)
        if page not in pages_to_scan:
            continue

        blocks_scanned += 1
        issue = scan_block(block)
        if issue:
            issues.append(issue)

    return ScanReport(
        total_pages=total_pages,
        pages_scanned=len(pages_to_scan),
        total_blocks=len(blocks),
        blocks_scanned=blocks_scanned,
        issues=issues,
    )


def load_and_scan(
    extraction_path: str | Path,
    **kwargs
) -> ScanReport:
    """Load extraction JSON and scan it."""
    with open(extraction_path) as f:
        extraction = json.load(f)
    return scan_extraction(extraction, **kwargs)


# CLI interface
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Scan PDF extraction for potential errors')
    parser.add_argument('extraction_json', help='Path to extraction JSON file')
    parser.add_argument('--mode', choices=['all', 'random', 'stratified'], default='all',
                        help='Sampling mode')
    parser.add_argument('--sample-size', type=int, default=20,
                        help='Sample size (pages for random, per-section for stratified)')
    parser.add_argument('--seed', type=int, default=None,
                        help='Random seed for reproducibility')
    parser.add_argument('--pages', type=str, default=None,
                        help='Specific pages to scan (comma-separated)')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')

    args = parser.parse_args()

    pages = None
    if args.pages:
        pages = [int(p.strip()) for p in args.pages.split(',')]

    report = load_and_scan(
        args.extraction_json,
        pages=pages,
        sample_mode=args.mode,
        sample_size=args.sample_size,
        seed=args.seed,
    )

    if args.json:
        import json
        output = {
            'summary': report.summary(),
            'issues': [
                {
                    'page': i.page,
                    'block_id': i.block_id,
                    'issue_type': i.issue_type,
                    'actual_type': i.actual_type,
                    'expected_type': i.expected_type,
                    'text_snippet': i.text_snippet,
                    'reason': i.reason,
                }
                for i in report.issues
            ]
        }
        print(json.dumps(output, indent=2))
    else:
        report.print_report()
