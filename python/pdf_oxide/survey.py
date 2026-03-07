"""Full document survey — scans every page to estimate content locations.

Returns a structured dict with table pages, figure pages, equation pages,
column layout, sections, and TOC info. Designed as a fast pre-pass that
downstream tools (extractor, extract-tables, etc.) consume directly.

This is the single source of truth for PDF profiling. The shared
``common.pdf_profiler`` module delegates here.

Usage:
    from pdf_oxide.survey import survey_document
    from pdf_oxide import PdfDocument

    doc = PdfDocument("input.pdf")
    survey = survey_document(doc)

    print(survey["table_pages"])      # [3, 5, 12, 13]
    print(survey["columns"])          # 2
    print(survey["equation_pages"])   # [7, 8, 9]
    print(survey["has_toc"])          # True

    # With Rust base profile enrichment:
    survey = survey_document(doc, enrich_profile=True)
    print(survey["domain"])           # "engineering"
    print(survey["is_scanned"])       # False
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

# ── Constants ──

LINE_TOLERANCE = 3.0
MIN_TABLE_LINES = 3

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


# ── Main entry point ──

def survey_document(
    doc,
    max_table_budget: int = 50,
    enrich_profile: bool = False,
) -> Dict[str, Any]:
    """Scan every page of a PDF and produce a complete content survey.

    Args:
        doc: A pdf_oxide.PdfDocument instance (already opened).
        max_table_budget: Max pages to run table extraction on (expensive).
        enrich_profile: If True, merge Rust-level ``profile_document()``
            fields (domain, complexity_score, is_scanned, preset, layout,
            primary_font, title) into the result.

    Returns:
        Dict with keys:
            page_count, columns, has_toc, has_sections, section_count,
            section_style, has_formulas, formula_pages, has_tables,
            table_pages, table_count_estimate, table_style, has_figures,
            figure_pages, figure_count_estimate, has_equations,
            equation_pages, equation_count_estimate, page_details,
            _engine.  When *enrich_profile* is True also: domain,
            complexity_score, is_scanned, preset, layout, primary_font,
            primary_font_size, title.
    """
    page_count = doc.page_count()

    # Accumulators
    full_text = ""
    table_pages_drawing: set = set()
    drawing_density: list = []
    image_pages: list = []
    all_font_sizes: list = []
    page_font_lines: list = []
    page_details: list = []
    column_votes: list = []

    # Font sampling
    font_sample_count = min(20, page_count)
    if font_sample_count >= page_count:
        font_sample_indices = set(range(page_count))
    else:
        step = page_count / font_sample_count
        font_sample_indices = {int(i * step) for i in range(font_sample_count)}

    # ══ PASS 1: Cheap signals on ALL pages ══
    for pg in range(page_count):
        page_text = ""
        has_images = False
        char_count = 0

        # Text
        try:
            page_text = doc.extract_text(pg)
            full_text += page_text + "\n"
            char_count = len(page_text)
        except Exception:
            pass

        # Images
        try:
            imgs = doc.extract_images(pg)
            if imgs:
                has_images = True
                # Count significant images (not icons)
                sig_count = sum(
                    1 for img in imgs
                    if img.get("bbox") and img["bbox"][2] >= 50 and img["bbox"][3] >= 50
                )
                if sig_count > 0:
                    image_pages.append(pg)
        except Exception:
            pass

        # Table region estimation via line drawings
        _scan_page_drawings(doc, pg, table_pages_drawing, drawing_density)

        # Filled-rectangle table detection (some PDFs use rects instead of paths)
        if pg not in table_pages_drawing:
            _scan_page_rects(doc, pg, table_pages_drawing, drawing_density)

        # Font data (sampled)
        if pg in font_sample_indices:
            _collect_font_data(doc, pg, all_font_sizes, page_font_lines)

        # Column detection (first 5 non-blank pages)
        if len(column_votes) < 5 and char_count > 50:
            cols = _detect_columns_from_spans(doc, pg)
            column_votes.append(cols)

        page_details.append({
            "page": pg,
            "char_count": char_count,
            "has_images": has_images,
            "is_blank": char_count == 0 and not has_images,
        })

    # ══ PASS 2: Targeted table extraction ══
    baseline_pages = {p for p in (0, 1, 2) if p < page_count}
    if table_pages_drawing:
        table_target_pages = table_pages_drawing | baseline_pages
    else:
        spread_count = min(max_table_budget, max(10, int(page_count ** 0.5)))
        step = max(1, page_count / spread_count)
        spread_pages = {int(i * step) for i in range(spread_count)}
        table_target_pages = spread_pages | baseline_pages

    if len(table_target_pages) > max_table_budget:
        drawing_density.sort(key=lambda x: x[1], reverse=True)
        top_pages = {p for p, _ in drawing_density[:max_table_budget - 3]}
        table_target_pages = top_pages | baseline_pages

    table_pages_confirmed: list = []
    total_table_count = 0
    max_tables_per_page = 0

    for pg in sorted(table_target_pages):
        try:
            tables = doc.extract_tables(pg)
            n = len(tables)
            if n > 0:
                table_pages_confirmed.append(pg)
                total_table_count += n
                max_tables_per_page = max(max_tables_per_page, n)
                page_details[pg]["table_count"] = n
        except Exception:
            pass

    # Extrapolate if we sampled
    table_sample_size = len(table_target_pages)
    if table_sample_size < page_count and len(table_pages_confirmed) > 0:
        ratio = page_count / table_sample_size
        est_table_count = int(total_table_count * ratio)
    else:
        est_table_count = total_table_count

    # Table style
    has_bordered = len(table_pages_drawing) > 0
    has_borderless = len(table_pages_confirmed) > len(table_pages_drawing)
    if has_bordered and has_borderless:
        table_style = "mixed"
    elif has_borderless:
        table_style = "borderless"
    elif has_bordered:
        table_style = "bordered"
    else:
        table_style = "none"

    # ══ Post-processing ══

    # Columns
    if not column_votes:
        columns = 1
    else:
        twos = sum(1 for c in column_votes if c >= 2)
        columns = 2 if twos > len(column_votes) / 2 else 1

    # TOC
    has_toc = False
    toc_entries = []
    try:
        outline = doc.get_outline()
        if outline:
            has_toc = True
            toc_entries = [
                {"title": e.get("title", ""), "level": e.get("level", 1), "page": e.get("page", 0)}
                for e in outline
            ]
    except Exception:
        pass

    # Text-based analysis
    has_formulas = _detect_formulas(full_text)
    formula_pages = _find_formula_pages(doc, page_count)
    section_style = _detect_section_style(full_text)
    section_estimate = _estimate_section_count(full_text)
    font_section_estimate = _estimate_sections_from_font_data(
        all_font_sizes, page_font_lines, font_sample_count, page_count,
    )
    has_sections = (section_estimate.get("estimated_count", 0) > 0
                    or font_section_estimate.get("estimated_count", 0) > 0
                    or len(toc_entries) > 0)
    section_count = max(
        section_estimate.get("estimated_count", 0),
        font_section_estimate.get("estimated_count", 0),
        len(toc_entries),
    )

    # Equation detection via block classifier (if available)
    equation_pages = _find_equation_pages(doc, page_count)

    result = {
        "page_count": page_count,
        "columns": columns,
        "has_toc": has_toc,
        "toc_entry_count": len(toc_entries),
        "has_sections": has_sections,
        "section_count": section_count,
        "section_style": section_style,
        "section_estimate": section_estimate,
        "font_section_estimate": font_section_estimate,
        "has_formulas": has_formulas,
        "formula_pages": formula_pages,
        "has_tables": len(table_pages_confirmed) > 0 or len(table_pages_drawing) > 0,
        "table_pages": sorted(set(table_pages_confirmed) | table_pages_drawing),
        "table_count_estimate": est_table_count,
        "table_style": table_style,
        "table_pages_confirmed": table_pages_confirmed,
        "table_pages_drawing": sorted(table_pages_drawing),
        "has_figures": len(image_pages) > 0,
        "figure_pages": image_pages,
        "figure_count_estimate": len(image_pages),
        "has_equations": len(equation_pages) > 0,
        "equation_pages": equation_pages,
        "equation_count_estimate": len(equation_pages),
        "page_details": page_details,
        "drawing_density_top10": sorted(drawing_density, key=lambda x: x[1], reverse=True)[:10],
        "_engine": "pdf_oxide",
    }

    # Enrich with Rust-level profile_document() if requested
    if enrich_profile:
        try:
            base = doc.profile_document()
            result["domain"] = base.get("domain", "general")
            result["complexity_score"] = base.get("complexity_score", 1)
            result["is_scanned"] = base.get("is_scanned", False)
            result["preset"] = base.get("preset", "general_document")
            result["layout"] = base.get("layout", {})
            result["primary_font"] = base.get("primary_font", "")
            result["primary_font_size"] = base.get("primary_font_size", 12.0)
            result["title"] = base.get("title")
        except Exception:
            pass

    return result


# ── Drawing-based table detection ──

def _scan_page_drawings(
    doc, page_idx: int, candidate_pages: set, density: list,
) -> None:
    """Scan page line drawings for table grid patterns."""
    try:
        paths = doc.extract_paths(page_idx)
    except Exception:
        return

    h_lines: list = []
    v_lines: list = []

    for path in paths:
        bbox = path.get("bbox")
        if not bbox:
            continue
        x, y, w, h = bbox
        if h < LINE_TOLERANCE and w > 10:
            h_lines.append((y, x, x + w))
        elif w < LINE_TOLERANCE and h > 10:
            v_lines.append((x, y, y + h))

    if len(h_lines) < MIN_TABLE_LINES or len(v_lines) < 2:
        return

    # Validate table-like grid
    h_widths = [x2 - x1 for _, x1, x2 in h_lines]
    try:
        page_w, _ = doc.page_dimensions(page_idx)
    except Exception:
        page_w = 612.0

    if h_widths and page_w > 0:
        if max(h_widths) < page_w * 0.25:
            return
        if len(h_widths) > 1:
            mean_w = sum(h_widths) / len(h_widths)
            if mean_w > 0:
                std_w = (sum((w - mean_w) ** 2 for w in h_widths) / len(h_widths)) ** 0.5
                if std_w / mean_w > 0.6:
                    return

    # Vertical column structure check
    if len(v_lines) >= 2:
        v_xs = sorted(x for x, _, _ in v_lines)
        x_clusters: list = []
        for x in v_xs:
            matched = False
            for i, (cx, count) in enumerate(x_clusters):
                if abs(x - cx) < 5.0:
                    x_clusters[i] = ((cx * count + x) / (count + 1), count + 1)
                    matched = True
                    break
            if not matched:
                x_clusters.append((x, 1))
        if len(x_clusters) > 20:
            return
        if sum(1 for _, c in x_clusters if c >= 2) < 2:
            return

    h_ys = sorted(set(round(y, 0) for y, _, _ in h_lines))
    if not h_ys:
        return

    region_count = 1
    for i in range(1, len(h_ys)):
        if h_ys[i] - h_ys[i - 1] > 30:
            region_count += 1

    candidate_pages.add(page_idx)
    density.append((page_idx, region_count))


# ── Filled-rectangle table detection ──

def _scan_page_rects(
    doc, page_idx: int, candidate_pages: set, density: list,
) -> None:
    """Detect tables made of filled rectangles + lines (not stroked paths).

    Some PDFs (e.g. diesel engine manuals) draw table borders as thin
    filled rects rather than stroked line paths.
    """
    try:
        rects = doc.extract_rects(page_idx)
        lines = doc.extract_lines(page_idx)
    except Exception:
        return

    h_count = 0
    v_count = 0

    for item in (rects or []) + (lines or []):
        bbox = item.get("bbox") if isinstance(item, dict) else getattr(item, "bbox", None)
        if not bbox:
            continue
        x, y, w, h = bbox
        if h < LINE_TOLERANCE and w > 10:
            h_count += 1
        elif w < LINE_TOLERANCE and h > 10:
            v_count += 1

    if h_count >= 2 and v_count >= 2:
        candidate_pages.add(page_idx)
        density.append((page_idx, 1))


# ── Column detection ──

def _detect_columns_from_spans(doc, page_idx: int) -> int:
    """Detect 1 vs 2 column layout from span x-coordinates."""
    try:
        spans = doc.extract_spans(page_idx)
    except Exception:
        return 1

    if not spans or len(spans) < 5:
        return 1

    try:
        page_w, _ = doc.page_dimensions(page_idx)
    except Exception:
        page_w = 612.0

    # Collect x-start positions of non-trivial spans
    # TextSpan: bbox is (x, y, w, h) tuple
    x_positions = []
    for s in spans:
        bbox = s.bbox
        text = s.text
        if bbox and len(text) > 5 and bbox[2] > 10:  # bbox[2] = width
            x_positions.append(bbox[0])

    if len(x_positions) < 5:
        return 1

    # Histogram (10-unit bins)
    bins: dict = {}
    for x in x_positions:
        b = int(x / 10)
        bins[b] = bins.get(b, 0) + 1

    threshold = len(x_positions) / 10
    significant = [b * 10.0 for b, count in bins.items() if count > threshold]

    if len(significant) <= 1:
        return 1

    margin = page_w * 0.08
    content_w = page_w - 2 * margin
    mid = margin + content_w / 2.0
    has_left = any(x < mid - 20 for x in significant)
    has_right = any(x > mid + 20 for x in significant)

    return 2 if (has_left and has_right) else 1


# ── Font data collection ──

def _collect_font_data(
    doc, page_idx: int, all_sizes: list, page_lines: list,
) -> None:
    """Collect font size and line data from spans.

    TextSpan objects have attributes: text, font_size, font_name, is_bold,
    is_italic, bbox (x, y, w, h tuple).
    """
    try:
        spans = doc.extract_spans(page_idx)
    except Exception:
        return

    lines_on_page: list = []
    current_line: list = []
    last_y = None

    for span in spans:
        sz = span.font_size
        txt = span.text.strip()
        bbox = span.bbox  # (x, y, w, h)
        y = bbox[1] if bbox else None

        if sz > 0 and len(txt) > 0:
            all_sizes.append(sz)

        if last_y is not None and y is not None and abs(y - last_y) > 3:
            if current_line:
                first = current_line[0]
                line_text = " ".join(s.text for s in current_line).strip()
                flags = 0
                if first.is_bold:
                    flags |= 16
                if first.is_italic:
                    flags |= 2
                lines_on_page.append({
                    "text": line_text,
                    "size": first.font_size,
                    "flags": flags,
                })
                current_line = []

        current_line.append(span)
        last_y = y

    if current_line:
        first = current_line[0]
        line_text = " ".join(s.text for s in current_line).strip()
        flags = 0
        if first.is_bold:
            flags |= 16
        if first.is_italic:
            flags |= 2
        lines_on_page.append({
            "text": line_text,
            "size": first.font_size,
            "flags": flags,
        })

    page_lines.append(lines_on_page)


# ── Text analysis ──

def _detect_formulas(text: str) -> bool:
    for pat in FORMULA_PATTERNS:
        if re.search(pat, text, re.MULTILINE | re.DOTALL):
            return True
    return False


def _find_formula_pages(doc, page_count: int) -> List[int]:
    """Find pages containing formula/equation patterns."""
    pages = []
    for pg in range(page_count):
        try:
            text = doc.extract_text(pg)
            if _detect_formulas(text):
                pages.append(pg)
        except Exception:
            pass
    return pages


def _find_equation_pages(doc, page_count: int) -> List[int]:
    """Find pages with equation blocks via block classifier."""
    try:
        # Try using the Rust block classifier
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
        # Fall back to formula detection
        return _find_formula_pages(doc, page_count)


def _detect_section_style(text: str) -> Optional[str]:
    for style, pat in SECTION_PATTERNS.items():
        if re.search(pat, text, re.MULTILINE | re.IGNORECASE):
            return style
    return None


def _estimate_section_count(text: str) -> Dict[str, Any]:
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


def _estimate_sections_from_font_data(
    all_sizes: list, page_lines: list,
    pages_sampled: int, total_pages: int,
) -> Dict[str, Any]:
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
