"""PDF Cloner — profiler and family assignment.

Wraps survey_document + structural analysis into a cloning-ready profile dict.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

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


def profile_for_cloning(pdf_path: str) -> Dict[str, Any]:
    """Profile a PDF for cloning — structural features, TOC, tables, requirements."""
    doc = pdf_oxide.PdfDocument(pdf_path)
    survey = survey_document(doc, enrich_profile=True)
    toc = doc.get_toc() or []
    _ = doc.get_section_map()

    page_count = int(survey.get("page_count", 0) or 0)

    result = {
        "doc_id": hashlib.md5(pdf_path.encode("utf-8")).hexdigest(),
        "path": pdf_path,
        "page_count": page_count,
        "domain": survey.get("domain", "general"),
        "complexity_score": survey.get("complexity_score", 1),
        "layout_mode": "multi_column" if int(survey.get("columns", 1) or 1) > 1 else "single_column",
        "has_toc": bool(survey.get("has_toc", False)),
        "toc_entry_count": int(survey.get("toc_entry_count", 0) or 0),
        "toc_pages": [e.get("page") for e in toc if isinstance(e, dict)] or [],
        "lof_entries": [e for e in toc if isinstance(e, dict) and e.get("entry_type") == "Figure"],
        "lot_entries": [e for e in toc if isinstance(e, dict) and e.get("entry_type") == "Table"],
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
        "table_shapes": survey.get("table_shapes", []),
        "page_spanning_tables": survey.get("page_spanning_tables", []),
        "running_headers": survey.get("running_headers", []),
        "running_footers": survey.get("running_footers", []),
    }

    # Derive requirements_pages from TOC (preferred) or text regex (fallback)
    from pdf_oxide.clone_sampler import _flatten_outline, _outline_to_regions
    outline = _flatten_outline(doc.get_outline() or [])
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

    # Count requirement clauses in body text (3.x.y patterns)
    _clause_pat = re.compile(r"^(\d+\.\d+\.\d+)\s")
    body_clauses: set[str] = set()
    for pg in range(page_count):
        try:
            text = doc.extract_text(pg)
        except Exception:
            continue
        for line in text.split("\n"):
            cm = _clause_pat.match(line.strip())
            if cm:
                body_clauses.add(cm.group(1))
    result["toc_section_count"] = len(toc_sections)
    result["toc_verified_count"] = sum(1 for s in toc_sections if s["verified"])
    result["body_clause_count"] = len(body_clauses)
    result["section_count"] = len(toc_sections) + len(body_clauses)

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
