"""PDF Cloner: Preset discovery and validation via synthetic fixtures."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import typer
from loguru import logger

from pypdf import PdfReader, PdfWriter

import pdf_oxide
from pdf_oxide.survey import survey_document

app = typer.Typer(name="clone_pdf", help="PDF Cloner — profile, sample, clone, score")


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
        # Fallback: text-based detection from survey
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
        # Verify: check title words on stated page, then adjacent pages
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
    signature = profile_for_cloning(pdf_path)
    assigned = assign_family(signature)
    return {**signature, **assigned}


def _flatten_outline(entries: list, out: list | None = None) -> list[dict]:
    """Flatten nested outline into a flat list with page numbers."""
    if out is None:
        out = []
    for e in entries or []:
        if isinstance(e, dict):
            out.append(e)
            _flatten_outline(e.get("children", []), out)
    return out


def _outline_to_regions(outline: list[dict], total_pages: int) -> list[dict]:
    """Convert flat outline entries into page regions with start/end and structural hints.

    Each region spans from its outline page to the next outline entry's page - 1.
    Titles are scanned for structural keywords (table, figure, requirement, etc.)
    """
    # Deduplicate and sort by page
    seen: set[int] = set()
    sorted_entries: list[dict] = []
    for e in outline:
        p = e.get("page")
        if p is None:
            continue
        p = int(p) - 1  # outline pages are 1-based
        if p < 0 or p >= total_pages or p in seen:
            continue
        seen.add(p)
        sorted_entries.append({**e, "page_0": p})
    sorted_entries.sort(key=lambda e: e["page_0"])

    regions: list[dict] = []
    for i, entry in enumerate(sorted_entries):
        start = entry["page_0"]
        end = sorted_entries[i + 1]["page_0"] - 1 if i + 1 < len(sorted_entries) else total_pages - 1
        title = str(entry.get("title", "")).lower()

        # Detect structural hints from title
        hints: list[str] = []
        if any(kw in title for kw in ("table", "mapping", "matrix")):
            hints.append("tables")
        if any(kw in title for kw in ("figure", "list of figure")):
            hints.append("figures")
        if any(kw in title for kw in ("requirement",)):
            hints.append("requirements")
        if any(kw in title for kw in ("appendix", "annex")):
            hints.append("appendix")
        if any(kw in title for kw in ("glossary", "acronym", "reference", "bibliography")):
            hints.append("reference_material")
        if any(kw in title for kw in ("introduction", "purpose", "scope", "overview")):
            hints.append("intro")
        if any(kw in title for kw in ("content", "toc")):
            hints.append("toc")

        regions.append({
            "title": entry.get("title", ""),
            "start": start,
            "end": end,
            "size": end - start + 1,
            "hints": hints,
            "level": entry.get("level", 1),
        })

    # Propagate parent hints to children — e.g. "3 The Requirements" hints
    # flow down to "3.1 Access Control", "3.5 Identification", etc.
    # Infer hierarchy from title numbering: "3" is parent of "3.1", "3.1" parent of "3.1.1"
    import re as _re
    _num_re = _re.compile(r"^(\d+(?:\.\d+)*)")

    def _title_depth(title: str) -> int:
        m = _num_re.match(title.strip())
        if m:
            return m.group(1).count(".") + 1  # "3" → 1, "3.1" → 2, "3.1.1" → 3
        return 0  # Appendix, TOC, etc — top level

    for i, region in enumerate(regions):
        if not region["hints"]:
            my_depth = _title_depth(region["title"])
            if my_depth > 0:
                # Walk backwards to find a parent with hints (lower depth)
                for j in range(i - 1, -1, -1):
                    parent = regions[j]
                    parent_depth = _title_depth(parent["title"])
                    if parent_depth < my_depth and parent["hints"]:
                        region["hints"] = list(parent["hints"])
                        break
                    if parent_depth <= 0 and j < i - 5:
                        break  # too far back, give up

    return regions


def build_sampling_plan(pdf_path: str, max_windows: int = 20, seed: int = 42) -> dict:
    random.seed(seed)
    profile = profile_for_cloning(pdf_path)
    doc = pdf_oxide.PdfDocument(pdf_path)
    outline = _flatten_outline(doc.get_outline() or [])

    total_pages = int(profile.get("page_count", 0) or 0)
    if total_pages <= 0:
        return {
            "strategy": "toc_guided_structural_stratified",
            "seed": seed,
            "total_pages": 0,
            "windows": [],
            "regions": [],
            "category_counts": {"anchor": 0, "boundary": 0, "pathology": 0, "span": 0},
        }

    windows: list[dict] = []
    category_counts = {"anchor": 0, "boundary": 0, "pathology": 0, "span": 0}
    seen_pages: set[tuple[int, ...]] = set()

    def add_window(source_pages: list[int], category: str, reason: str) -> bool:
        norm_pages = sorted(set(int(p) for p in source_pages if 0 <= int(p) < total_pages))
        if not norm_pages or len(windows) >= max_windows:
            return False
        key = tuple(norm_pages)
        if key in seen_pages:
            return False
        seen_pages.add(key)
        category_counts[category] += 1
        windows.append(
            {
                "window_id": f"WIN_{len(windows) + 1:04d}",
                "source_pages": norm_pages,
                "category": category,
                "selection_reason": [reason],
            }
        )
        return True

    # Build page signature lookup
    signatures = profile.get("page_signatures", []) or []
    sig_by_page: dict[int, dict] = {int(s.get("page_num", -1)): s for s in signatures}

    def _page_complexity(p: int) -> int:
        s = sig_by_page.get(p, {})
        return (int(bool(s.get("table_candidate")))
                + int(bool(s.get("equation_candidate")))
                + int(bool(s.get("figure_candidate")))
                + int(bool(s.get("has_images"))))

    # Build structural regions from outline
    regions = _outline_to_regions(outline, total_pages)

    # Phase 1: One representative window per structural region
    # Priority: tables > requirements > figures > appendix > other
    # Larger regions get priority within each type (more structural variety)
    def _region_priority(r: dict) -> tuple:
        h = r["hints"]
        type_rank = 0 if "tables" in h else 1 if "requirements" in h else 2 if "figures" in h else 3 if "appendix" in h else 4
        return (type_rank, -r["size"])

    for region in sorted(regions, key=_region_priority):
        pages_in_region = list(range(region["start"], region["end"] + 1))
        if not pages_in_region:
            continue

        # Pick the page with highest structural complexity
        # For multi-page regions, skip the first page (often just a heading)
        candidates = pages_in_region[1:] if len(pages_in_region) > 2 else pages_in_region
        best_page = max(candidates, key=_page_complexity)
        hints = region["hints"]
        title_short = region["title"][:40]

        if "tables" in hints:
            add_window([best_page], "pathology", f"region_tables:{title_short}")
        elif "requirements" in hints:
            add_window([best_page], "pathology", f"region_requirements:{title_short}")
        elif "figures" in hints:
            add_window([best_page], "pathology", f"region_figures:{title_short}")
        elif "appendix" in hints or "reference_material" in hints:
            add_window([best_page], "boundary", f"region_ref:{title_short}")
        elif "toc" in hints:
            add_window([best_page], "boundary", f"region_toc:{title_short}")
        else:
            add_window([best_page], "anchor", f"region_content:{title_short}")

    # Phase 2: Span windows — consecutive table pages for cross-page table testing
    table_pages = {int(s.get("page_num", -1)) for s in signatures if s.get("table_candidate")}
    for p in sorted(table_pages):
        if p + 1 in table_pages:
            add_window([p, p + 1], "span", "table_continuation")

    # Phase 3: Fill remaining slots with anchors from underrepresented regions
    # Prefer regions with more pages (they have more structural variety)
    large_regions = sorted(regions, key=lambda r: r["size"], reverse=True)
    for region in large_regions:
        pages_in_region = list(range(region["start"], region["end"] + 1))
        random.shuffle(pages_in_region)
        for p in pages_in_region:
            add_window([p], "anchor", f"region_fill:{region['title'][:30]}")

    # Phase 4: Backfill any remaining slots
    all_pages = list(range(total_pages))
    random.shuffle(all_pages)
    for p in all_pages:
        add_window([p], "anchor", "backfill")

    return {
        "strategy": "toc_guided_structural_stratified",
        "seed": seed,
        "total_pages": total_pages,
        "regions": [{"title": r["title"], "pages": f"{r['start']}-{r['end']}", "hints": r["hints"]} for r in regions],
        "windows": windows,
        "category_counts": category_counts,
    }


# ---------------------------------------------------------------------------
# Task 4 — render_windows: extract PNGs + spans per sampled window
# ---------------------------------------------------------------------------

def render_windows(
    pdf_path: str,
    sampling_plan: dict,
    output_dir: str,
    page_signatures: list[dict] | None = None,
) -> list[dict]:
    """Render each window in sampling_plan to PNGs and span JSON files."""
    doc = pdf_oxide.PdfDocument(pdf_path)
    rendered: list[dict] = []

    # Build table candidate lookup from page signatures
    table_pages: set[int] = set()
    for sig in page_signatures or []:
        if sig.get("table_candidate"):
            table_pages.add(int(sig.get("page_num", -1)))

    for window in sampling_plan.get("windows", []):
        wid = str(window.get("window_id", ""))
        source_pages: list[int] = [int(p) for p in window.get("source_pages", [])]
        win_dir = os.path.join(output_dir, wid)
        os.makedirs(win_dir, exist_ok=True)

        png_paths: list[str] = []
        span_paths: list[str] = []

        for page_num in source_pages:
            if page_num < 0:
                continue

            # Render PNG via pdftoppm (poppler) — pdf_oxide render_page has text rendering issues
            png_path = os.path.join(win_dir, f"page_{page_num}.png")
            pdf_page_1based = page_num + 1  # pdftoppm uses 1-based pages
            prefix = os.path.join(win_dir, f"_render_{page_num}")
            import subprocess
            subprocess.run(
                ["pdftoppm", "-png", "-f", str(pdf_page_1based), "-l", str(pdf_page_1based),
                 "-r", "150", pdf_path, prefix],
                capture_output=True, timeout=30,
            )
            # pdftoppm outputs prefix-{pagenum}.png
            rendered_file = f"{prefix}-{pdf_page_1based}.png"
            if os.path.exists(rendered_file):
                os.rename(rendered_file, png_path)
            elif os.path.exists(f"{prefix}.png"):
                os.rename(f"{prefix}.png", png_path)
            png_paths.append(png_path)

            # Extract spans (page_num is 0-based, same as pdf_oxide indexing)
            spans = doc.extract_spans(page_num)
            span_data = [
                {
                    "text": s.text,
                    "bbox": list(s.bbox),
                    "font_name": s.font_name,
                    "font_size": s.font_size,
                }
                for s in spans
            ]
            span_path = os.path.join(win_dir, f"spans_{page_num}.json")
            with open(span_path, "w", encoding="utf-8") as f:
                json.dump(span_data, f, indent=2)
            span_paths.append(span_path)

        # Extract window pages into a mini-PDF via pypdf
        window_pdf_path = os.path.join(win_dir, "window.pdf")
        reader = PdfReader(pdf_path)
        writer = PdfWriter()
        for page_num in source_pages:
            if 0 <= page_num < len(reader.pages):
                writer.add_page(reader.pages[page_num])
        with open(window_pdf_path, "wb") as f:
            writer.write(f)

        has_table_hint = any(p in table_pages for p in source_pages)
        rendered.append({
            "window_id": wid,
            "pdf_path": pdf_path,
            "window_pdf_path": window_pdf_path,
            "png_paths": png_paths,
            "span_paths": span_paths,
            "source_pages": source_pages,
            "has_table_hint": has_table_hint,
        })
        logger.debug(f"Rendered {wid}: {len(png_paths)} pages")

    return rendered


# ---------------------------------------------------------------------------
# Task 5b — build_clone_manifest: enrich sampling plan with structural metadata
# ---------------------------------------------------------------------------

def build_clone_manifest(
    profile: dict,
    sampling_plan: dict,
    doc,
    output_dir: str | None = None,
) -> list[dict]:
    """Enrich each window in sampling_plan with structural metadata from profile."""
    # Build fast lookup tables
    toc_sections = profile.get("toc_sections", []) or []
    table_shapes = profile.get("table_shapes", []) or []
    spanning_tables = profile.get("page_spanning_tables", []) or []
    requirements_pages = set(profile.get("requirements_pages", []) or [])
    running_headers = profile.get("running_headers", []) or []
    running_footers = profile.get("running_footers", []) or []
    page_signatures = profile.get("page_signatures", []) or []

    # Index: page -> list of table shapes
    tables_by_page: dict[int, list[dict]] = {}
    for ts in table_shapes:
        pg = int(ts.get("page", -1))
        tables_by_page.setdefault(pg, []).append(ts)

    # Index: page -> page signature
    sig_by_page: dict[int, dict] = {int(s.get("page_num", -1)): s for s in page_signatures}

    # Clause pattern for requirement detection
    _clause_pat = re.compile(r"\d+\.\d+\.\d+")

    enriched: list[dict] = []

    for window in sampling_plan.get("windows", []):
        source_pages: list[int] = [int(p) for p in window.get("source_pages", [])]
        page_set = set(source_pages)

        # 1. TOC sections covering these pages
        matched_sections = [
            s for s in toc_sections
            if s.get("page") is not None and int(s["page"]) in page_set
        ]
        primary_section = matched_sections[0] if matched_sections else None
        toc_section_title = primary_section["title"] if primary_section else None
        parent_section = next(
            (s for s in toc_sections if primary_section and s["id"] == primary_section.get("parent_id")),
            None,
        )
        toc_parent_title = parent_section["title"] if parent_section else toc_section_title

        # 2. Table shapes for these pages
        window_tables = []
        for pg in source_pages:
            for ts in tables_by_page.get(pg, []):
                window_tables.append({
                    "page": pg,
                    "rows": ts.get("rows", 0),
                    "cols": ts.get("cols", 0),
                    "ruled": bool(ts.get("ruled", False)),
                    "bbox": ts.get("bbox"),
                })

        # 3. Page-spanning tables that overlap these pages
        matched_spanning = None
        for span in spanning_tables:
            sp_start = int(span.get("start_page", -1))
            sp_end = int(span.get("end_page", -1))
            if any(sp_start <= pg <= sp_end for pg in source_pages):
                matched_spanning = span
                break

        # 4. Requirements pages
        is_requirements = bool(page_set & requirements_pages)

        # 5. Running headers / footers (use first non-empty values)
        running_header = running_headers[0] if running_headers else None
        running_footer = running_footers[0] if running_footers else None

        # 6. Page-level signature data
        page_char_counts = [sig_by_page.get(pg, {}).get("char_count", 0) for pg in source_pages]
        has_images = any(sig_by_page.get(pg, {}).get("has_images", False) for pg in source_pages)
        has_equations = any(sig_by_page.get(pg, {}).get("equation_candidate", False) for pg in source_pages)

        # 7. Clause count — scan text for \d+\.\d+\.\d+ patterns
        clause_ids: set[str] = set()
        for pg in source_pages:
            try:
                text = doc.extract_text(pg)
                for m in _clause_pat.findall(text):
                    clause_ids.add(m)
            except Exception:
                pass
        clause_count = len(clause_ids)

        # 8. Determine primary content type
        category = window.get("category", "")
        if matched_spanning:
            content_type = "spanning_table"
        elif window_tables and is_requirements:
            content_type = "mixed"
        elif window_tables:
            content_type = "table"
        elif is_requirements:
            content_type = "requirements"
        elif category == "anchor" and toc_section_title:
            content_type = "toc"
        else:
            content_type = "prose"

        clone_brief = {
            "content_type": content_type,
            "toc_section": toc_section_title,
            "toc_parent": toc_parent_title,
            "tables": window_tables,
            "spanning_table": matched_spanning,
            "is_requirements": is_requirements,
            "clause_count": clause_count,
            "running_header": running_header,
            "running_footer": running_footer,
            "page_char_counts": page_char_counts,
            "has_images": has_images,
            "has_equations": has_equations,
        }

        enriched_window = {**window, "clone_brief": clone_brief}
        enriched.append(enriched_window)

        # Human-readable summary line
        pages_str = ",".join(str(p) for p in source_pages)
        section_str = f" [{toc_section_title[:40]}]" if toc_section_title else ""
        table_str = f" {len(window_tables)}tbl" if window_tables else ""
        req_str = " REQ" if is_requirements else ""
        span_str = " SPAN" if matched_spanning else ""
        print(
            f"  {window['window_id']} p{pages_str} ({content_type})"
            f"{section_str}{table_str}{req_str}{span_str}"
            f" clauses={clause_count}"
        )

    # Write manifest to output_dir if provided
    if output_dir:
        manifest_path = os.path.join(output_dir, "clone_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(enriched, f, indent=2)
        print(f"Clone manifest written to {manifest_path}")

    return enriched


# ---------------------------------------------------------------------------
# Task 6 — clone_pdf: LLM self-improvement loop via scillm
# ---------------------------------------------------------------------------

SCILLM_URL = os.environ.get("SCILLM_URL", "http://localhost:4001")
SCILLM_KEY = os.environ.get("SCILLM_PROXY_KEY", "sk-dev-proxy-123")
_SCILLM_HEADERS = {"Authorization": f"Bearer {SCILLM_KEY}", "Content-Type": "application/json"}

_CLONE_SYSTEM = """You write Python scripts using ReportLab that recreate PDF page layouts.
You receive a PDF page. Write a self-contained Python script that produces a synthetic PDF matching its structure.
The synthetic is scored by comparing text extraction on both PDFs: same text, same blocks, same tables, same headings.
Output ONLY Python code."""


async def clone_pdf(
    pdf_path: str,
    output_dir: str,
    max_windows: int = 5,
    seed: int = 42,
    model: str = "claude-opus-4-6",
    max_rounds: int = 5,
) -> dict:
    """Clone PDF structure by generating ReportLab code for sampled windows.

    Pipeline: profile → sample → render → manifest → LLM → execute → score → iterate.
    Returns summary dict with per-window scores and round counts.
    """
    import httpx
    from pdf_oxide.clone_scorer import score_clone

    os.makedirs(output_dir, exist_ok=True)
    doc = pdf_oxide.PdfDocument(pdf_path)
    logger.info(f"Profiling {pdf_path}...")
    profile = profile_for_cloning(pdf_path)

    logger.info(f"Building sampling plan (max_windows={max_windows}, seed={seed})...")
    plan = build_sampling_plan(pdf_path, max_windows=max_windows, seed=seed)

    logger.info(f"Rendering {len(plan['windows'])} windows...")
    windows = render_windows(pdf_path, plan, output_dir, profile.get("page_signatures"))

    logger.info("Building clone manifest...")
    manifest = build_clone_manifest(profile, plan, doc, output_dir)

    results: list[dict] = []

    for win in manifest:
        wid = win["window_id"]
        brief = win["clone_brief"]
        win_dir = os.path.join(output_dir, wid)
        window_pdf = os.path.join(win_dir, "window.pdf")
        synthetic_pdf = os.path.join(win_dir, "synthetic.pdf")
        code_path = os.path.join(win_dir, "reportlab_code.py")

        if not os.path.exists(window_pdf):
            logger.warning(f"{wid}: window.pdf missing, skipping")
            results.append({"window_id": wid, "status": "skip", "reason": "no window.pdf"})
            continue

        # Read mini-PDF
        with open(window_pdf, "rb") as f:
            pdf_b64 = base64.b64encode(f.read()).decode()
        num_pages = len(win["source_pages"])

        # Build prompt — minimal, Claude reads the PDF directly
        context = ""
        if brief.get("spanning_table"):
            sp = brief["spanning_table"]
            context = (
                f"\nContext: This page is part of a table spanning pages "
                f"{sp['start_page']}-{sp['end_page']} "
                f"({sp['total_rows']} rows, {sp['cols']} cols). "
                f"The table continues from the previous page.\n"
            )

        user_text = (
            f"Recreate the attached {num_pages}-page PDF using ReportLab.\n"
            f"{context}\n"
            f"Output: {num_pages} page(s), letter size (612x792 pts).\n"
            f"Save to: {synthetic_pdf}\n"
            f"Run with: .venv/bin/python {code_path}\n"
            f"Code only."
        )

        conversation: list[dict] = [
            {"role": "system", "content": _CLONE_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {
                    "url": f"data:application/pdf;base64,{pdf_b64}",
                }},
            ]},
        ]

        best_score: dict | None = None
        win_result = {
            "window_id": wid,
            "source_pages": win["source_pages"],
            "content_type": brief.get("content_type", "unknown"),
            "rounds": 0,
            "status": "fail",
        }

        for round_num in range(1, max_rounds + 1):
            win_result["rounds"] = round_num
            logger.info(f"{wid} round {round_num}/{max_rounds}: calling {model}...")

            # Call scillm
            try:
                resp = httpx.post(
                    f"{SCILLM_URL}/v1/chat/completions",
                    json={"model": model, "max_tokens": 16384, "messages": conversation},
                    headers=_SCILLM_HEADERS,
                    timeout=120,
                )
                if resp.status_code != 200:
                    logger.error(f"{wid} round {round_num}: scillm {resp.status_code}")
                    win_result["error"] = f"scillm {resp.status_code}"
                    break
                content = resp.json()["choices"][0]["message"]["content"]
            except Exception as e:
                logger.error(f"{wid} round {round_num}: {e}")
                win_result["error"] = str(e)
                break

            # Extract Python code
            if "```python" in content:
                code = content.split("```python")[1].split("```")[0].strip()
            elif "```" in content:
                code = content.split("```")[1].split("```")[0].strip()
            else:
                code = content.strip()

            with open(code_path, "w", encoding="utf-8") as f:
                f.write(code)

            # Execute
            import subprocess
            exec_result = subprocess.run(
                [".venv/bin/python", code_path],
                capture_output=True, text=True, timeout=30,
                cwd=os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)
                ))),
            )
            exec_ok = exec_result.returncode == 0
            exec_output = exec_result.stderr[:500] if not exec_ok else "OK"

            if not exec_ok:
                logger.warning(f"{wid} round {round_num}: execution failed")
                # Append error to conversation for retry
                conversation.append({"role": "assistant", "content": content})
                conversation.append({"role": "user", "content": (
                    f"Execution failed:\n```\n{exec_output}\n```\n"
                    f"Fix the error. Write the complete corrected script. Code only."
                )})
                continue

            if not os.path.exists(synthetic_pdf):
                logger.warning(f"{wid} round {round_num}: no synthetic.pdf produced")
                conversation.append({"role": "assistant", "content": content})
                conversation.append({"role": "user", "content": (
                    f"The script ran but no PDF was created at {synthetic_pdf}. "
                    f"Fix the output path. Code only."
                )})
                continue

            # Score
            score = score_clone(window_pdf, synthetic_pdf)
            logger.info(
                f"{wid} round {round_num}: score={score['overall']:.3f} "
                f"pass={score['pass']}"
            )

            best_score = score
            win_result["score"] = score

            if score["pass"]:
                win_result["status"] = "pass"
                win_result["synthetic_pdf"] = synthetic_pdf
                break

            # Append code + delta for retry
            conversation.append({"role": "assistant", "content": content})
            conversation.append({"role": "user", "content": (
                f"Score: {score['overall']:.3f} (need >= 0.7)\n"
                f"Delta: {score['delta_report']}\n\n"
                f"Fix the issues. Write the complete corrected script. Code only."
            )})

        if win_result["status"] != "pass" and best_score:
            win_result["synthetic_exists"] = os.path.exists(synthetic_pdf)

        results.append(win_result)
        logger.info(
            f"{wid}: {win_result['status']} in {win_result['rounds']} rounds"
            + (f" (score={best_score['overall']:.3f})" if best_score else "")
        )

    summary = {
        "pdf_path": pdf_path,
        "output_dir": output_dir,
        "windows": results,
        "passed": sum(1 for r in results if r.get("status") == "pass"),
        "total": len(results),
        "model": model,
    }
    # Write summary
    with open(os.path.join(output_dir, "clone_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)

    return summary


@app.command("profile")
def profile(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    output_json: bool = typer.Option(False, "--json", is_flag=True, help="Output as JSON"),
) -> None:
    """Profile a PDF for cloning — wraps survey_document + profile into DocumentSignature."""
    result = profile_for_cloning(pdf_path)
    if output_json:
        print(json.dumps(result))
    else:
        typer.echo(f"doc_id:     {result['doc_id']}")
        typer.echo(f"domain:     {result['domain']}")
        typer.echo(f"pages:      {result['page_count']}")
        typer.echo(f"layout:     {result['layout_mode']}")
        typer.echo(f"has_toc:    {result['has_toc']}")
        typer.echo(f"tables:     {result['has_tables']} (density={result['table_density']:.2f})")
        typer.echo(f"figures:    {result['has_figures']} (density={result['figure_density']:.2f})")
        typer.echo(f"sections:   {result['section_count']} ({result['section_style']})")
        typer.echo(f"complexity: {result['complexity_score']}")


@app.command("family")
def family(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    output_json: bool = typer.Option(False, "--json", is_flag=True, help="Output as JSON"),
) -> None:
    """Profile and assign family based on rule-based signature matching."""
    result = profile_and_assign(pdf_path)
    if output_json:
        print(json.dumps(result))
    else:
        typer.echo(f"family: {result['family_id']} (confidence={result['confidence']})")


@app.command("sample")
def sample(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    max_windows: int = typer.Option(20, "--max-windows", help="Maximum windows to sample"),
    seed: int = typer.Option(42, "--seed", help="Random seed"),
    output_json: bool = typer.Option(False, "--json", is_flag=True, help="Output as JSON"),
) -> None:
    """Generate a stratified window sampling plan for a PDF."""
    result = build_sampling_plan(pdf_path, max_windows=max_windows, seed=seed)
    if output_json:
        print(json.dumps(result))
    else:
        typer.echo(f"Strategy: {result.get('strategy')}")
        typer.echo(f"Windows: {len(result.get('windows', []))}")
        for w in result.get("windows", []):
            typer.echo(f"  {w['window_id']}: pages={w['source_pages']} cat={w['category']} reason={w['selection_reason']}")


@app.command("render")
def render_cmd(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    max_windows: int = typer.Option(5, "--max-windows", help="Maximum windows"),
    seed: int = typer.Option(42, "--seed", help="Random seed"),
    output_dir: str = typer.Option("/tmp/clone_render", "-o", help="Output directory"),
) -> None:
    """Render sampled windows to PNGs and span JSON files."""
    plan = build_sampling_plan(pdf_path, max_windows=max_windows, seed=seed)
    result = render_windows(pdf_path, plan, output_dir)
    for r in result:
        typer.echo(f"{r['window_id']}: {len(r['png_paths'])} PNGs, {len(r['span_paths'])} span files")


@app.command("clone")
def clone_cmd(
    pdf_path: str = typer.Argument(..., help="Path to PDF file"),
    output_dir: str = typer.Option("/tmp/clone_output", "-o", help="Output directory"),
    max_windows: int = typer.Option(5, "--max-windows", help="Maximum windows"),
    seed: int = typer.Option(42, "--seed", help="Random seed"),
    model: str = typer.Option("claude-opus-4-6", "--model", help="scillm model name"),
    max_rounds: int = typer.Option(5, "--max-rounds", help="Max self-improvement rounds per window"),
) -> None:
    """Full clone pipeline: profile → sample → render → manifest → LLM → execute → score."""
    result = asyncio.run(clone_pdf(
        pdf_path, output_dir,
        max_windows=max_windows, seed=seed,
        model=model, max_rounds=max_rounds,
    ))
    typer.echo(f"Passed: {result['passed']}/{result['total']} windows")
    for w in result.get("windows", []):
        score_str = f" score={w['score']['overall']:.3f}" if "score" in w else ""
        typer.echo(f"  {w['window_id']}: {w['status']} ({w['rounds']} rounds){score_str}")


if __name__ == "__main__":
    app()
