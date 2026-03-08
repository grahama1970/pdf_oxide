"""Text-based analysis helpers for the survey module.

Extracted from survey.py to keep each module under 800 lines.
Handles: formula detection, equation pages, section estimation,
TOC detection from rendered text, and font-based section counting.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

# ── Constants (shared with survey.py via import) ──

FORMULA_PATTERNS = [
    r"\$\$.+?\$\$",
    r"\$[^$]+\$",
    r"\\begin\{equation\}",
    r"\\frac\{",
    r"\\sum|\\int|\\prod",
    r"\\alpha|\\beta|\\gamma|\\theta|\\pi",
    r"[∑∫∂√∞±×÷≤≥≠≈]",
]

SECTION_PATTERNS = {
    "decimal": r"^\d+\.\d+",
    "roman": r"^[IVXLCDM]+\.",
    "chapter": r"^Chapter\s+\d+",
}

SECTION_COUNT_PATTERNS = [
    r"^\s*\d+(?:\.\d+)*(?:\.[a-z])?[.:)\-–—\s]",
    r"^\s*(?:Appendix|Annex|Section|Chapter|Part)\s+[A-Za-z0-9IVXLCDM.]+",
    r"^\s*[IVXLCDM]+\.\s+[A-Z]",
    r"^\s*[A-Z]\.\s+[A-Z]",
]

CAPTION_RE = re.compile(
    r"^\s*(?:Table|Figure|Fig\.?|Listing|Algorithm|Exhibit)\s+\d",
    re.IGNORECASE,
)
SECTION_NUMBER_RE = re.compile(
    r"^\s*(?:"
    r"\d{1,2}(?:\.\d{1,3}){0,3}"
    r"|[A-Z](?:\.\d{1,3}){0,2}"
    r"|[IVXLC]{1,5}"
    r")\s+[A-Z]",
)

COMMON_HEADERS = {
    "abstract", "introduction", "conclusion", "summary", "overview",
    "background", "methods", "results", "discussion", "references",
    "appendix", "glossary", "acronyms", "definitions", "requirements",
    "scope", "purpose", "architecture", "design", "implementation",
}

# TOC line pattern: text followed by dots/spaces then a page number
# Matches: "1.2 Introduction ......... 12" or "Chapter 3  45"
_TOC_LINE_RE = re.compile(
    r"^(.{3,80}?)"           # title text (3-80 chars, non-greedy)
    r"\s*"                    # optional space
    r"[.\u2024\u2025\u2026·\s]{2,}"  # dot leaders or spaces (2+)
    r"\s*"                    # optional space
    r"(\d{1,4})\s*$",        # page number at end of line
)

# Also match lines that are just "Title  <number>" with big whitespace gap
_TOC_SPACED_RE = re.compile(
    r"^(.{3,80}?)"           # title
    r"\s{4,}"                # 4+ spaces (tab-like gap)
    r"(\d{1,4})\s*$",        # page number
)

# Common TOC heading keywords
_TOC_HEADING_WORDS = {
    "table of contents", "contents", "table des matieres",
    "index", "inhalt", "sommaire",
}


# ── Formula / Equation detection ──

def detect_formulas(text: str) -> bool:
    """Check if text contains formula/equation patterns."""
    for pat in FORMULA_PATTERNS:
        if re.search(pat, text, re.MULTILINE | re.DOTALL):
            return True
    return False


def find_formula_pages(doc, page_count: int) -> List[int]:
    """Find pages containing formula/equation patterns."""
    pages = []
    for pg in range(page_count):
        try:
            text = doc.extract_text(pg)
            if detect_formulas(text):
                pages.append(pg)
        except Exception:
            pass
    return pages


def find_equation_pages(doc, page_count: int) -> List[int]:
    """Find pages with equation blocks via block classifier."""
    try:
        pages = []
        for pg in range(page_count):
            try:
                blocks = doc.classify_blocks(pg)
                if any(b.get("block_type") == "Equation" for b in blocks):
                    pages.append(pg)
            except Exception:
                pass
        return pages
    except Exception:
        return find_formula_pages(doc, page_count)


# ── Section detection ──

def detect_section_style(text: str) -> Optional[str]:
    """Detect the primary section numbering style."""
    for style, pat in SECTION_PATTERNS.items():
        if re.search(pat, text, re.MULTILINE | re.IGNORECASE):
            return style
    return None


def estimate_section_count(text: str) -> Dict[str, Any]:
    """Estimate section count from text patterns."""
    lines = text.split("\n")
    counts = {"decimal": 0, "labeled": 0, "roman": 0, "alpha": 0}
    seen: set = set()

    for line in lines:
        s = line.strip()
        if not s or len(s) < 3:
            continue
        for i, pat in enumerate(SECTION_COUNT_PATTERNS):
            match = re.match(pat, s, re.IGNORECASE)
            if match:
                matched = match.group(0).strip()[:20]
                if matched not in seen:
                    seen.add(matched)
                    names = ["decimal", "labeled", "roman", "alpha"]
                    counts[names[i]] += 1
                break

    header_count = 0
    lower = text.lower()
    for h in COMMON_HEADERS:
        if re.search(rf"(?:^|\d\.?\s+){h}\b", lower, re.MULTILINE):
            header_count += 1

    total = sum(counts.values())
    return {
        "estimated_count": total,
        "by_pattern": counts,
        "common_headers_found": header_count,
        "primary_style": max(counts, key=counts.get) if total > 0 else None,
    }


def estimate_sections_from_font_data(
    all_sizes: list, page_lines: list,
    pages_sampled: int, total_pages: int,
) -> Dict[str, Any]:
    """Estimate section count from font size data."""
    if not all_sizes:
        return {"estimated_count": 0, "sampled_count": 0,
                "pages_sampled": pages_sampled, "body_font_size": 0}

    import statistics as stats

    rounded = [round(s, 1) for s in all_sizes]
    paragraph_sizes = [s for s in rounded if s >= 8.0]
    if paragraph_sizes:
        body_size = Counter(paragraph_sizes).most_common(1)[0][0]
    else:
        body_size = stats.median(all_sizes)

    heading_threshold = max(body_size * 1.18, 9.5)
    heading_count = 0
    seen_texts: set = set()

    for lines in page_lines:
        page_headings = 0
        for line_info in lines:
            text = line_info["text"]
            size = line_info["size"]
            flags = line_info.get("flags", 0)
            is_bold = bool(flags & 16)

            if len(text) < 2 or len(text) > 120:
                continue
            if CAPTION_RE.match(text):
                continue
            if text.isdigit():
                continue

            is_heading = False
            if size >= heading_threshold:
                is_heading = True
            elif is_bold and size > body_size * 1.10 and size >= 9.0:
                is_heading = True
            elif is_bold and abs(size - body_size) < 1.0 and len(text) < 80:
                if SECTION_NUMBER_RE.match(text):
                    is_heading = True
                elif len(text) < 50 and text[0].isupper() and not text[0].isdigit():
                    if len(text.split()) <= 6:
                        is_heading = True

            if is_heading:
                normalized = text.strip().lower()[:60]
                if normalized not in seen_texts:
                    seen_texts.add(normalized)
                    heading_count += 1
                    page_headings += 1
                if page_headings >= 8:
                    break

    if pages_sampled < total_pages and pages_sampled > 0:
        extrapolated = int((heading_count / pages_sampled) * total_pages)
    else:
        extrapolated = heading_count

    return {
        "estimated_count": extrapolated,
        "sampled_count": heading_count,
        "pages_sampled": pages_sampled,
        "body_font_size": round(body_size, 1),
    }


# ── TOC detection from rendered text ──

def detect_toc_from_text(
    doc, page_count: int, max_scan: int = 10,
) -> Tuple[Optional[int], float]:
    """Detect a table of contents from rendered page text.

    Scans the first *max_scan* pages for TOC-like patterns:
    1. A heading containing "contents" / "table of contents"
    2. Lines matching "Title text ...... 42" (dot-leader + page number)

    A page is flagged as TOC if >= 40% of its non-blank lines match
    the TOC line pattern.

    Returns:
        (page_index, confidence) where confidence is 0.0-1.0,
        or (None, 0.0) if no TOC detected.
    """
    scan_limit = min(max_scan, page_count)
    best_page: Optional[int] = None
    best_score: float = 0.0

    for pg in range(scan_limit):
        try:
            text = doc.extract_text(pg)
        except Exception:
            continue

        if not text or len(text) < 30:
            continue

        lines = text.split("\n")
        non_blank = [ln for ln in lines if ln.strip()]
        if len(non_blank) < 3:
            continue

        # Check for TOC heading keyword on this page
        has_toc_heading = False
        first_lines = " ".join(ln.strip().lower() for ln in non_blank[:5])
        for kw in _TOC_HEADING_WORDS:
            if kw in first_lines:
                has_toc_heading = True
                break

        # Count TOC-pattern lines
        toc_matches = 0
        for ln in non_blank:
            if _TOC_LINE_RE.match(ln.strip()) or _TOC_SPACED_RE.match(ln.strip()):
                toc_matches += 1

        if toc_matches < 3:
            continue

        ratio = toc_matches / len(non_blank)

        # Score: ratio of TOC lines, boosted if heading found
        score = ratio
        if has_toc_heading:
            score = min(1.0, score + 0.2)

        if score > best_score and ratio >= 0.4:
            best_score = score
            best_page = pg

    return best_page, best_score
