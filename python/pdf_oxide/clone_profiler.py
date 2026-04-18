"""PDF Cloner — profiler and family assignment.

Wraps survey_document + structural analysis into a cloning-ready profile dict.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from pypdf import PdfReader
from pypdf.generic import Destination, IndirectObject

import pdf_oxide
from pdf_oxide.survey import survey_document


def _build_page_signatures(doc, survey: Dict[str, Any]) -> List[Dict[str, Any]]:
    page_details = survey.get("page_details", []) or []
    table_pages = set(survey.get("table_pages", []) or [])
    figure_pages = set(survey.get("figure_pages", []) or [])
    equation_pages = set(survey.get("equation_pages", []) or [])

    signatures: List[Dict[str, Any]] = []
    for idx, detail in enumerate(page_details):
        page_num = int(detail.get("page", idx))
        signatures.append(
            {
                "page_num": page_num,
                "char_count": int(detail.get("char_count", 0) or 0),
                "has_images": bool(detail.get("has_images", False)),
                "is_blank": bool(detail.get("is_blank", False)),
                "table_candidate": page_num in table_pages,
                "figure_candidate": page_num in figure_pages,
                "equation_candidate": page_num in equation_pages,
            }
        )
    return signatures


_FONT_MAP: Dict[str, Tuple[str, str]] = {
    "ArialMT": ("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf", "Arial"),
    "Arial-BoldMT": ("/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf", "Arial-Bold"),
    "Arial-ItalicMT": ("/usr/share/fonts/truetype/msttcorefonts/Arial_Italic.ttf", "Arial-Italic"),
    "Arial-BoldItalicMT": ("/usr/share/fonts/truetype/msttcorefonts/Arial_Bold_Italic.ttf", "Arial-BoldItalic"),
    "ArialNarrow": ("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf", "ArialNarrow"),
    "TimesNewRomanPSMT": ("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf", "TimesNewRoman"),
    "TimesNewRomanPS-BoldMT": ("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Bold.ttf", "TimesNewRoman-Bold"),
    "TimesNewRomanPS-ItalicMT": ("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Italic.ttf", "TimesNewRoman-Italic"),
    "TimesNewRomanPS-BoldItalicMT": (
        "/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Bold_Italic.ttf",
        "TimesNewRoman-BoldItalic",
    ),
    "Times-Roman": ("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf", "TimesNewRoman"),
    "Times-Bold": ("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Bold.ttf", "TimesNewRoman-Bold"),
    "Times-Italic": ("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Italic.ttf", "TimesNewRoman-Italic"),
    "Times-BoldItalic": ("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Bold_Italic.ttf", "TimesNewRoman-BoldItalic"),
}

def _normalize_font_name(name: str) -> str:
    name = name.lstrip("/")
    if "+" in name:
        name = name.split("+", 1)[1]
    return name


def _lookup_font(base_name: str) -> Tuple[str, str] | Tuple[None, str]:
    if base_name in _FONT_MAP:
        return _FONT_MAP[base_name]
    fallback = base_name.split("-")[0]
    if fallback in _FONT_MAP:
        return _FONT_MAP[fallback]
    return (None, base_name)


def _font_descriptor_embedded(descriptor: Any | None) -> bool:
    if not descriptor or not hasattr(descriptor, "get"):
        return False
    return any(descriptor.get(key) for key in ("/FontFile", "/FontFile2", "/FontFile3"))


def _detect_fonts_with_pypdf(reader: PdfReader | None) -> Dict[str, Any]:
    if reader is None:
        return {"fonts": {}, "families": []}

    fonts: Dict[str, Dict[str, Any]] = {}
    for idx, page in enumerate(reader.pages):
        resources = page.get("/Resources")
        if not resources:
            continue
        font_dict = resources.get("/Font", {})
        for _, font_ref in font_dict.items():
            try:
                font_obj = font_ref.get_object()
            except Exception:
                continue
            base_name = _normalize_font_name(str(font_obj.get("/BaseFont", "")))
            if not base_name:
                continue
            descriptor = font_obj.get("/FontDescriptor")
            ttf_path, reportlab_name = _lookup_font(base_name)
            entry = fonts.setdefault(
                base_name,
                {
                    "base_name": base_name,
                    "reportlab_name": reportlab_name,
                    "ttf_path": ttf_path,
                    "pages": set(),
                    "is_embedded": False,
                },
            )
            entry["pages"].add(idx + 1)
            entry["is_embedded"] = entry["is_embedded"] or _font_descriptor_embedded(descriptor)

    for entry in fonts.values():
        entry["pages"] = sorted(entry["pages"])

    return {
        "fonts": {name: dict(data) for name, data in fonts.items()},
        "families": sorted({data["reportlab_name"] for data in fonts.values()}),
    }


def _normalize_outline_items(reader: PdfReader | None) -> list[dict]:
    if reader is None:
        return []

    try:
        raw_outline = reader.outline
    except Exception:
        raw_outline = []

    def _walk(items, level: int = 1) -> list[dict]:
        results: list[dict] = []
        last_entry: dict | None = None
        for item in items or []:
            if isinstance(item, list):
                children = _walk(item, level + 1)
                if last_entry is not None:
                    last_entry.setdefault("children", []).extend(children)
                else:
                    results.extend(children)
                continue

            entry = _normalize_outline_entry(reader, item, level)
            if entry:
                results.append(entry)
                last_entry = entry
        return results

    return _walk(raw_outline or [])


def _normalize_outline_entry(reader: PdfReader, item: Any, level: int) -> dict | None:
    title = ""
    page_num: Optional[int] = None

    if isinstance(item, Destination):
        title = item.title or ""
        try:
            page_num = reader.get_destination_page_number(item) + 1
        except Exception:
            page_num = None
    elif isinstance(item, dict):
        title = str(item.get("/Title", "")).strip()
        page_ref = item.get("/Page")
        if isinstance(page_ref, IndirectObject):
            try:
                page_num = reader._get_page_number_by_indirect(page_ref) + 1
            except Exception:
                page_num = None
    else:
        title = str(getattr(item, "title", item))

    if page_num is None:
        return None

    return {"title": title, "page": page_num, "level": level, "children": []}


def _extract_actual_table_shapes(doc, page_count: int) -> List[Dict[str, Any]]:
    """Extract actual table dimensions using pdf_oxide's table extractor.

    This gives accurate rows/cols/bbox, unlike line-based estimation which
    picks up page borders and headers giving wrong dimensions.
    """
    table_shapes = []
    for page_idx in range(page_count):
        try:
            page_tables = doc.read_pdf(
                pages=str(page_idx + 1),
                flavor="lattice",
                line_scale=40,
            )
            for t in page_tables:
                table_shapes.append({
                    "page": page_idx,
                    "rows": t.get("rows", 0),
                    "cols": t.get("cols", 0),
                    "bbox": t.get("bbox"),
                    "ruled": True,
                })
        except Exception:
            pass
    return table_shapes


def profile_for_cloning(pdf_path: str) -> Dict[str, Any]:
    """Profile a PDF for cloning — structural features, TOC, tables, requirements."""
    try:
        reader = PdfReader(pdf_path)
    except Exception:
        reader = None

    doc = pdf_oxide.PdfDocument(pdf_path)
    survey = survey_document(doc, enrich_profile=True)
    outline_tree = _normalize_outline_items(reader)
    font_info = _detect_fonts_with_pypdf(reader)
    _ = doc.get_section_map()

    page_count = int(survey.get("page_count", 0) or 0)

    # Use actual table extraction for accurate dimensions (not line-based estimation)
    # Line-based estimation from survey uses full-page lines which gives wrong bboxes
    table_shapes = _extract_actual_table_shapes(doc, page_count)
    if not table_shapes:
        # Fallback to survey's line-based estimation if extraction fails
        table_shapes = survey.get("table_shapes", [])

    result = {
        "doc_id": hashlib.md5(pdf_path.encode("utf-8")).hexdigest(),
        "path": pdf_path,
        "page_count": page_count,
        "domain": survey.get("domain", "general"),
        "complexity_score": survey.get("complexity_score", 1),
        "layout_mode": "multi_column" if int(survey.get("columns", 1) or 1) > 1 else "single_column",
        "has_toc": bool(survey.get("has_toc", False)),
        "toc_entry_count": int(survey.get("toc_entry_count", 0) or 0),
        "toc_pages": [e.get("page") for e in outline_tree if e.get("page")] or [],
        "lof_entries": [
            {"title": e.get("title"), "page": e.get("page")}
            for e in outline_tree
            if "figure" in str(e.get("title", "")).lower()
        ],
        "lot_entries": [
            {"title": e.get("title"), "page": e.get("page")}
            for e in outline_tree
            if "table" in str(e.get("title", "")).lower()
        ],
        "has_tables": bool(survey.get("has_tables", False)),
        "table_density": len(survey.get("table_pages", []) or []) / max(page_count, 1),
        "has_figures": bool(survey.get("has_figures", False)),
        "figure_density": len(survey.get("figure_pages", []) or []) / max(page_count, 1),
        "has_equations": bool(survey.get("has_equations", False)),
        "has_engineering": survey.get("domain") in ("engineering", "defense"),
        "section_count": int(survey.get("section_count", 0) or 0),
        "section_style": survey.get("section_style"),
        "is_scanned": bool(survey.get("is_scanned", False)),
        "page_signatures": _build_page_signatures(doc, survey),
        "table_shapes": table_shapes,
        "page_spanning_tables": survey.get("page_spanning_tables", []),
        "running_headers": survey.get("running_headers", []),
        "running_footers": survey.get("running_footers", []),
    }

    # Derive requirements_pages from TOC (preferred) or text regex (fallback)
    from pdf_oxide.clone_sampler import _flatten_outline, _outline_to_regions

    outline = _flatten_outline(outline_tree)
    regions = _outline_to_regions(outline, page_count)
    toc_req_pages = []
    for r in regions:
        if "requirements" in r.get("hints", []):
            toc_req_pages.extend(range(r["start"], r["end"] + 1))
    if toc_req_pages:
        result["requirements_pages"] = sorted(set(toc_req_pages))
        result["requirements_source"] = "toc"
    else:
        result["requirements_pages"] = survey.get("requirements_pages", [])
        result["requirements_source"] = "text_regex"
    result["requirements_density"] = len(result["requirements_pages"]) / max(page_count, 1)

    # ── Build nested TOC with parent_id + verify each entry exists on its page ──
    _num_pat = re.compile(r"^(\d+(?:\.\d+)*)")
    toc_sections: list[dict] = []
    parent_stack: list[tuple[int, int]] = []
    for i, entry in enumerate(outline):
        title = entry.get("title", "").strip()
        m = _num_pat.match(title)
        depth = m.group(1).count(".") + 1 if m else 0
        while parent_stack and parent_stack[-1][0] >= depth:
            parent_stack.pop()
        parent_id = parent_stack[-1][1] if parent_stack else None
        if depth > 0:
            parent_stack.append((depth, i))
        else:
            parent_stack = []
        pg = entry.get("page")
        pg_idx = int(pg) - 1 if pg else None
        verified = False
        verified_page = pg_idx
        if pg_idx is not None:
            title_words = [w for w in title.upper().split() if len(w) > 3]
            if title_words:
                for check_pg in [pg_idx, pg_idx - 1, pg_idx + 1]:
                    if check_pg < 0 or check_pg >= page_count:
                        continue
                    try:
                        page_text = doc.extract_text(check_pg).upper()
                        found = sum(1 for w in title_words if w in page_text)
                        if found >= max(1, len(title_words) // 2):
                            verified = True
                            verified_page = check_pg
                            break
                    except Exception:
                        continue
        toc_sections.append({
            "id": i, "title": title, "depth": depth,
            "parent_id": parent_id, "page": verified_page,
            "verified": verified,
        })
    result["toc_sections"] = toc_sections
    result["font_detection_source"] = "pypdf" if font_info["fonts"] else "unknown"
    result["font_map"] = font_info["fonts"]
    result["font_families"] = font_info["families"]

    # Count requirement clauses in body text (3.x.y patterns)
    _clause_pat = re.compile(r"^(\d+\.\d+\.\d+)\s")
    # Control ID pattern: AC-1, SI-7, PM-11, AC-2(1), etc.
    # In NIST PDFs, control ID is often on its own line (no title on same line)
    _control_id_pat = re.compile(
        r"^(?:\[QID_[A-F0-9]+\])?"  # Optional QID marker
        r"([A-Z]{2}-\d+(?:\(\d+\))?)"  # Control ID
        r"\s*$"  # End of line (control ID alone) or whitespace
    )
    body_clauses: set[str] = set()
    control_ids: set[str] = set()
    for pg in range(page_count):
        try:
            text = doc.extract_text(pg)
        except Exception:
            continue
        for line in text.split("\n"):
            stripped = line.strip()
            # Skip TOC entries (dot leaders)
            if '...' in stripped:
                continue
            cm = _clause_pat.match(stripped)
            if cm:
                body_clauses.add(cm.group(1))
            ctrl = _control_id_pat.match(stripped)
            if ctrl:
                control_ids.add(ctrl.group(1).split('(')[0])  # Normalize AC-2(1) -> AC-2
    result["toc_section_count"] = len(toc_sections)
    result["toc_verified_count"] = sum(1 for s in toc_sections if s["verified"])
    result["body_clause_count"] = len(body_clauses)
    result["control_id_count"] = len(control_ids)
    result["control_ids"] = sorted(control_ids)
    result["section_count"] = len(toc_sections) + len(body_clauses) + len(control_ids)

    # List, footnote, callout detection
    _bullet_re = re.compile(r"^[\u2022\u2023\u25E6\u2043\u2219\u2013\u2014\-]\s")
    list_pages: list[int] = []
    footnote_pages: list[int] = []
    callout_pages: list[int] = []
    for pg in range(min(page_count, 30)):
        try:
            spans = doc.extract_spans(pg)
        except Exception:
            continue
        if not spans:
            continue
        sizes = [s.font_size for s in spans if s.font_size > 0]
        if not sizes:
            continue
        sizes.sort()
        median_sz = sizes[len(sizes) // 2]
        bullets = 0
        footnotes = 0
        for s in spans:
            t = s.text.strip()
            if not t:
                continue
            y = s.bbox[1] if s.bbox else 0
            if _bullet_re.match(t):
                bullets += 1
            if s.font_size > 0 and s.font_size < median_sz * 0.8 and y < 80 and len(t) > 10:
                footnotes += 1
        if bullets >= 2:
            list_pages.append(pg)
        if footnotes >= 1:
            footnote_pages.append(pg)
        try:
            paths = doc.extract_paths(pg)
            for p in paths:
                bbox = p.get("bbox")
                if bbox and bbox[2] > 100 and bbox[3] > 30 and bbox[2] < 500 and bbox[3] < 300:
                    callout_pages.append(pg)
                    break
        except Exception:
            pass
    result["list_pages"] = list_pages
    result["footnote_pages"] = footnote_pages
    result["callout_pages"] = callout_pages

    # ── Deterministic structural metrics ──
    table_shapes = result.get("table_shapes", [])
    spanning = result.get("page_spanning_tables", [])
    req_pages = result.get("requirements_pages", [])
    distinct_table_count = 0
    prev_pg = -10
    for s in table_shapes:
        if s["page"] != prev_pg + 1:
            distinct_table_count += 1
        prev_pg = s["page"]
    largest_table = max(table_shapes, key=lambda s: s["rows"]) if table_shapes else None
    sigs = result.get("page_signatures", [])
    table_shape_by_pg = {s["page"]: s for s in table_shapes}
    avg_chars = sum(s.get("char_count", 0) for s in sigs) / max(len(sigs), 1)

    def _complexity(sig: dict) -> float:
        pg = sig.get("page_num", -1)
        score = 0.0
        ts = table_shape_by_pg.get(pg)
        if ts:
            score += ts["rows"] * ts["cols"] * 0.3
        chars = sig.get("char_count", 0)
        if avg_chars > 0:
            score += min(chars / avg_chars, 3.0) * 0.2
        if sig.get("has_images"):
            score += 0.2
        if sig.get("equation_candidate"):
            score += 0.3
        return score

    most_complex = max(range(len(sigs)), key=lambda i: _complexity(sigs[i])) if sigs else 0
    all_hints = set()
    for r in regions:
        all_hints.update(r.get("hints", []))
    result["metrics"] = {
        "page_count": page_count,
        "distinct_table_count": distinct_table_count,
        "table_pages_count": len(table_shapes),
        "spanning_table_count": len(spanning),
        "spanning_tables_total_rows": sum(s["total_rows"] for s in spanning),
        "largest_table": {"page": largest_table["page"], "rows": largest_table["rows"], "cols": largest_table["cols"]} if largest_table else None,
        "requirements_page_count": len(req_pages),
        "requirements_source": result.get("requirements_source", "none"),
        "figure_page_count": len(survey.get("figure_pages", [])),
        "equation_page_count": len(survey.get("equation_pages", [])),
        "list_page_count": len(list_pages),
        "footnote_page_count": len(footnote_pages),
        "callout_page_count": len(callout_pages),
        "toc_region_count": len(regions),
        "toc_section_count": result["toc_section_count"],
        "toc_verified_count": result["toc_verified_count"],
        "body_clause_count": result["body_clause_count"],
        "section_count": result["section_count"],
        "structural_hint_types": sorted(all_hints),
        "running_header_count": len(result.get("running_headers", [])),
        "running_footer_count": len(result.get("running_footers", [])),
        "most_complex_page": most_complex,
        "most_complex_page_score": round(_complexity(sigs[most_complex]), 2) if sigs else 0,
        "section_style": result.get("section_style"),
        "domain": result.get("domain", "general"),
        "is_scanned": result.get("is_scanned", False),
    }

    return result


def assign_family(signature: dict) -> dict:
    """Assign a document family based on rule-based signature matching."""
    domain = signature.get("domain")
    table_density = float(signature.get("table_density", 0.0) or 0.0)
    section_style = signature.get("section_style")
    has_engineering = bool(signature.get("has_engineering", False))
    layout_mode = signature.get("layout_mode")
    has_equations = bool(signature.get("has_equations", False))
    has_toc = bool(signature.get("has_toc", False))
    section_count = int(signature.get("section_count", 0) or 0)
    is_scanned = bool(signature.get("is_scanned", False))
    figure_density = float(signature.get("figure_density", 0.0) or 0.0)

    rules_matched: list[str] = []

    if domain == "defense" and table_density > 0.3 and section_style == "decimal":
        family_id = "defense_spec_requirements_tables"
        confidence = 0.9
        rules_matched.append("rule_1_defense_table_decimal")
    elif domain == "defense" and has_engineering:
        family_id = "defense_engineering_drawings"
        confidence = 0.9
        rules_matched.append("rule_2_defense_engineering")
    elif domain == "engineering" and table_density > 0.2:
        family_id = "engineering_spec_tables"
        confidence = 0.9
        rules_matched.append("rule_3_engineering_tables")
    elif domain == "academic" and layout_mode == "multi_column" and has_equations:
        family_id = "academic_twocol_math"
        confidence = 0.9
        rules_matched.append("rule_4_academic_twocol_math")
    elif domain == "academic" and layout_mode == "multi_column":
        family_id = "academic_twocol_prose"
        confidence = 0.9
        rules_matched.append("rule_5_academic_twocol_prose")
    elif domain == "academic":
        family_id = "academic_singlecol"
        confidence = 0.9
        rules_matched.append("rule_6_academic_singlecol")
    elif has_toc and section_count > 20 and table_density > 0.1:
        family_id = "technical_manual_mixed"
        confidence = 0.7
        rules_matched.append("rule_7_toc_sections_tables")
    elif is_scanned:
        family_id = "scanned_mixed"
        confidence = 0.7
        rules_matched.append("rule_8_scanned")
    else:
        family_id = "general_prose"
        confidence = 0.5
        rules_matched.append("rule_9_default")

    subfamily_id: Optional[str] = None
    if table_density > 0.35 and has_toc:
        subfamily_id = "appendix_matrix_tables"
        rules_matched.append("subfamily_table_heavy_appendices")
    elif figure_density > 0.3:
        subfamily_id = "figure_heavy_sections"
        rules_matched.append("subfamily_figure_heavy_sections")

    return {
        "family_id": family_id,
        "subfamily_id": subfamily_id,
        "confidence": confidence,
        "rules_matched": rules_matched,
    }


def profile_and_assign(pdf_path: str) -> dict:
    """Profile and assign family based on rule-based signature matching."""
    signature = profile_for_cloning(pdf_path)
    assigned = assign_family(signature)
    return {**signature, **assigned}
