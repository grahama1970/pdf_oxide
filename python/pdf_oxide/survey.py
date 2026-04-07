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
from typing import Any, Dict, List

from .survey_text import (
    detect_formulas as _detect_formulas,
    find_formula_pages as _find_formula_pages,
    find_equation_pages as _find_equation_pages,
    detect_section_style as _detect_section_style,
    estimate_section_count as _estimate_section_count,
    estimate_sections_from_font_data as _estimate_sections_from_font_data,
    detect_toc_from_text as _detect_toc_from_text,
)

# ── Constants ──

LINE_TOLERANCE = 3.0
MIN_TABLE_LINES = 3


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
    table_pages_text_align: set = set()
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

        # Borderless table detection via text alignment
        if pg not in table_pages_drawing:
            _scan_page_text_alignment(doc, pg, table_pages_text_align, drawing_density)

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
    # Merge drawing + text-alignment candidates for Pass 2 targeting
    all_table_candidates = table_pages_drawing | table_pages_text_align

    baseline_pages = {p for p in (0, 1, 2) if p < page_count}
    if all_table_candidates:
        table_target_pages = all_table_candidates | baseline_pages
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
    has_borderless = (len(table_pages_text_align) > 0
                      or len(table_pages_confirmed) > len(table_pages_drawing))
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

    # TOC — structural outline first, then text-based fallback
    has_toc = False
    toc_entries = []
    toc_source = "none"
    try:
        outline = doc.get_outline()
        if outline:
            has_toc = True
            toc_source = "outline"
            toc_entries = [
                {"title": e.get("title", ""), "level": e.get("level", 1), "page": e.get("page", 0)}
                for e in outline
            ]
    except Exception:
        pass

    # Text-based TOC detection when no structural outline exists
    if not has_toc:
        toc_page, toc_score = _detect_toc_from_text(doc, page_count)
        if toc_page is not None:
            has_toc = True
            toc_source = "text"

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

    # ══ PASS 3: Structural detail (table shapes, spanning, requirements, headers) ══

    # Table shapes from line drawings (works even when extract_tables returns 0)
    table_shapes: list[dict] = []
    for pg in sorted(all_table_candidates):
        shape = _estimate_table_shape_from_lines(doc, pg)
        if shape:
            table_shapes.append(shape)

    # Page-spanning table detection
    page_spanning_tables = _detect_page_spanning_tables(table_shapes)

    # Fix table_count_estimate: use line-based shapes when extract_tables fails
    if est_table_count == 0 and table_shapes:
        # Count distinct tables (group consecutive pages as one table)
        distinct_tables = 0
        prev_page = -10
        for shape in table_shapes:
            if shape["page"] != prev_page + 1:
                distinct_tables += 1
            prev_page = shape["page"]
        est_table_count = distinct_tables

    # Requirements pages
    requirements_pages = _detect_requirements_pages(doc, page_count)

    # Running headers/footers
    running_headers, running_footers = _detect_running_headers_footers(doc, page_count)

    result = {
        "page_count": page_count,
        "columns": columns,
        "has_toc": has_toc,
        "toc_source": toc_source,
        "toc_entry_count": len(toc_entries),
        "has_sections": has_sections,
        "section_count": section_count,
        "section_style": section_style,
        "section_estimate": section_estimate,
        "font_section_estimate": font_section_estimate,
        "has_formulas": has_formulas,
        "formula_pages": formula_pages,
        "has_tables": len(table_pages_confirmed) > 0 or len(all_table_candidates) > 0,
        "table_pages": sorted(set(table_pages_confirmed) | all_table_candidates),
        "table_count_estimate": est_table_count,
        "table_style": table_style,
        "table_pages_confirmed": table_pages_confirmed,
        "table_pages_drawing": sorted(table_pages_drawing),
        "table_pages_text_align": sorted(table_pages_text_align),
        "has_figures": len(image_pages) > 0,
        "figure_pages": image_pages,
        "figure_count_estimate": len(image_pages),
        "has_equations": len(equation_pages) > 0,
        "equation_pages": equation_pages,
        "equation_count_estimate": len(equation_pages),
        "table_shapes": [{k: v for k, v in s.items() if k != "col_positions"} for s in table_shapes],
        "page_spanning_tables": page_spanning_tables,
        "requirements_pages": requirements_pages,
        "requirements_density": len(requirements_pages) / max(page_count, 1),
        "running_headers": running_headers,
        "running_footers": running_footers,
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


# ── Borderless table detection via text alignment ──

# Tolerance for grouping spans into the same text line (points)
_Y_LINE_TOL = 3.0
# Tolerance for matching x-start positions across lines (points)
_X_ALIGN_TOL = 5.0
# Minimum consecutive aligned lines to flag as borderless table candidate
_MIN_ALIGNED_ROWS = 3
# Minimum distinct column starts to qualify as tabular (not just a list)
_MIN_COLUMNS = 3


def _scan_page_text_alignment(
    doc, page_idx: int, candidate_pages: set, density: list,
) -> None:
    """Detect borderless tables from columnar text alignment patterns.

    Groups spans into lines by y-proximity, records the set of x-start
    positions per line, then looks for runs of consecutive lines that
    share the same column structure (same x-starts within tolerance).
    If 3+ consecutive lines share 3+ aligned column starts, the page
    is flagged as a borderless table candidate.
    """
    try:
        spans = doc.extract_spans(page_idx)
    except Exception:
        return

    if not spans or len(spans) < 10:
        return

    # Group spans into lines by y-coordinate proximity
    lines: list[list] = []
    current_line: list = []
    last_y: float | None = None

    for span in spans:
        bbox = span.bbox
        if not bbox:
            continue
        y = bbox[1]
        if last_y is not None and abs(y - last_y) > _Y_LINE_TOL:
            if current_line:
                lines.append(current_line)
            current_line = []
        current_line.append(span)
        last_y = y

    if current_line:
        lines.append(current_line)

    if len(lines) < _MIN_ALIGNED_ROWS:
        return

    # For each line, collect sorted unique x-start positions (raw points)
    def _x_starts(line_spans: list) -> list[float]:
        seen: set[int] = set()
        xs: list[float] = []
        for s in line_spans:
            if s.bbox:
                bucket = round(s.bbox[0] / _X_ALIGN_TOL)
                if bucket not in seen:
                    seen.add(bucket)
                    xs.append(s.bbox[0])
        return sorted(xs)

    x_per_line = [_x_starts(line) for line in lines]

    def _sigs_match(a: list[float], b: list[float]) -> bool:
        """Two lines match if same column count and each x within tolerance."""
        if len(a) != len(b) or len(a) < _MIN_COLUMNS:
            return False
        return all(abs(xa - xb) <= _X_ALIGN_TOL * 2 for xa, xb in zip(a, b))

    # Find runs of consecutive lines with matching column structure
    best_run = 0
    current_run = 1

    for i in range(1, len(x_per_line)):
        if _sigs_match(x_per_line[i - 1], x_per_line[i]):
            current_run += 1
        else:
            best_run = max(best_run, current_run)
            current_run = 1

    best_run = max(best_run, current_run)

    if best_run >= _MIN_ALIGNED_ROWS:
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




# ── Table shape estimation from line drawings ──

_CLUSTER_TOL = 3.0


def _estimate_table_shape_from_lines(doc, page_idx: int) -> dict | None:
    """Estimate table rows x cols from horizontal/vertical line drawings.

    Returns dict with page, rows, cols, col_positions, bbox, ruled
    or None if no table grid found.
    """
    try:
        paths = doc.extract_paths(page_idx)
    except Exception:
        return None

    h_lines: list[tuple[float, float, float]] = []
    v_lines: list[tuple[float, float, float]] = []

    for p in paths:
        bbox = p.get("bbox")
        if not bbox:
            continue
        x, y, w, h = bbox
        if h < LINE_TOLERANCE and w > 10:
            h_lines.append((round(y, 1), round(x, 1), round(x + w, 1)))
        elif w < LINE_TOLERANCE and h > 10:
            v_lines.append((round(x, 1), round(y, 1), round(y + h, 1)))

    if len(h_lines) < MIN_TABLE_LINES or len(v_lines) < 2:
        return None

    # Cluster horizontal lines by y → row separators
    h_lines.sort(key=lambda l: l[0])
    row_ys: list[float] = []
    for y, _, _ in h_lines:
        if not row_ys or abs(y - row_ys[-1]) > _CLUSTER_TOL:
            row_ys.append(y)

    # Cluster vertical lines by x → column separators
    v_lines.sort(key=lambda l: l[0])
    col_xs: list[float] = []
    for x, _, _ in v_lines:
        if not col_xs or abs(x - col_xs[-1]) > _CLUSTER_TOL:
            col_xs.append(x)

    rows = max(0, len(row_ys) - 1)
    cols = max(0, len(col_xs) - 1)

    if rows < 1 or cols < 1:
        return None

    # Compute bounding box from extremes
    all_x = [x1 for _, x1, _ in h_lines] + [x2 for _, _, x2 in h_lines]
    all_y = [y for y, _, _ in h_lines]
    table_bbox = [min(all_x), min(all_y), max(all_x), max(all_y)]

    return {
        "page": page_idx,
        "rows": rows,
        "cols": cols,
        "col_positions": col_xs,
        "bbox": table_bbox,
        "ruled": True,
    }


def _detect_page_spanning_tables(table_shapes: list[dict]) -> list[dict]:
    """Detect page-spanning tables by matching column structure across consecutive pages."""
    if len(table_shapes) < 2:
        return []

    spanning: list[dict] = []
    i = 0
    while i < len(table_shapes) - 1:
        curr = table_shapes[i]
        # Find the run of consecutive pages with same column count + positions
        run_start = curr["page"]
        run_cols = curr["cols"]
        run_positions = curr["col_positions"]
        total_rows = curr["rows"]
        j = i + 1

        while j < len(table_shapes):
            nxt = table_shapes[j]
            if nxt["page"] != table_shapes[j - 1]["page"] + 1:
                break
            if nxt["cols"] != run_cols:
                break
            # Check column positions match within tolerance
            if len(run_positions) == len(nxt["col_positions"]):
                if not all(abs(a - b) < 10 for a, b in zip(run_positions, nxt["col_positions"])):
                    break
            total_rows += nxt["rows"]
            j += 1

        if j > i + 1:  # span of 2+ pages
            spanning.append({
                "start_page": run_start,
                "end_page": table_shapes[j - 1]["page"],
                "pages": j - i,
                "cols": run_cols,
                "total_rows": total_rows,
            })
            i = j
        else:
            i += 1

    return spanning


# ── Requirements page detection ──

_REQ_SHALL_RE = re.compile(r"\b(shall|must)\b", re.IGNORECASE)
_REQ_CLAUSE_RE = re.compile(r"\b\d+\.\d+\.\d+\b")


def _detect_requirements_pages(doc, page_count: int) -> list[int]:
    """Detect pages containing requirements language (shall/must, numbered clauses)."""
    req_pages: list[int] = []
    for pg in range(page_count):
        try:
            text = doc.extract_text(pg)
        except Exception:
            continue
        shall_count = len(_REQ_SHALL_RE.findall(text))
        clause_count = len(_REQ_CLAUSE_RE.findall(text))
        if shall_count >= 2 or clause_count >= 3:
            req_pages.append(pg)
    return req_pages


# ── Running header/footer detection ──

def _detect_running_headers_footers(
    doc, page_count: int,
) -> tuple[list[str], list[str]]:
    """Detect repeating text at top/bottom of pages (headers/footers).

    Samples middle pages and finds text spans that repeat 3+ times
    at consistent y-coordinates.
    """
    if page_count < 5:
        return [], []

    # Sample 20 pages from the middle (skip first/last 2)
    start = min(2, page_count - 1)
    end = max(start + 1, page_count - 2)
    sample_pages = list(range(start, min(end, start + 20)))

    try:
        _, page_h = doc.page_dimensions(0)
    except Exception:
        page_h = 792.0

    header_texts: dict[str, int] = {}
    footer_texts: dict[str, int] = {}

    for pg in sample_pages:
        try:
            spans = doc.extract_spans(pg)
        except Exception:
            continue
        if not spans:
            continue
        for s in spans:
            y = s.bbox[1] if s.bbox else 0
            text = s.text.strip()
            if len(text) < 3 or len(text) > 80:
                continue
            if y > page_h - 60:  # top region (PDF y=0 at bottom)
                header_texts[text] = header_texts.get(text, 0) + 1
            elif y < 60:  # bottom region
                footer_texts[text] = footer_texts.get(text, 0) + 1

    min_count = 3
    headers = sorted(
        [t for t, c in header_texts.items() if c >= min_count],
        key=lambda t: -header_texts[t],
    )
    footers = sorted(
        [t for t, c in footer_texts.items() if c >= min_count],
        key=lambda t: -footer_texts[t],
    )
    return headers, footers
