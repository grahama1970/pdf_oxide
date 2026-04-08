"""PDF Cloner — sampling, rendering, and manifest building.

TOC-guided window sampling, page rendering to PNG/spans, and
structural metadata enrichment for clone windows.
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
from typing import Any

from loguru import logger
from pypdf import PdfReader, PdfWriter

import pdf_oxide


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
    """Convert flat outline entries into page regions with start/end and structural hints."""
    seen: set[int] = set()
    sorted_entries: list[dict] = []
    for e in outline:
        p = e.get("page")
        if p is None:
            continue
        p = int(p) - 1
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

    # Propagate parent hints to children
    _num_re = re.compile(r"^(\d+(?:\.\d+)*)")

    def _title_depth(title: str) -> int:
        m = _num_re.match(title.strip())
        if m:
            return m.group(1).count(".") + 1
        return 0

    for i, region in enumerate(regions):
        if not region["hints"]:
            my_depth = _title_depth(region["title"])
            if my_depth > 0:
                for j in range(i - 1, -1, -1):
                    parent = regions[j]
                    parent_depth = _title_depth(parent["title"])
                    if parent_depth < my_depth and parent["hints"]:
                        region["hints"] = list(parent["hints"])
                        break
                    if parent_depth <= 0 and j < i - 5:
                        break

    return regions


def build_sampling_plan(pdf_path: str, max_windows: int = 20, seed: int = 42) -> dict:
    """Generate a stratified window sampling plan for a PDF."""
    from pdf_oxide.clone_profiler import profile_for_cloning

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

    signatures = profile.get("page_signatures", []) or []
    sig_by_page: dict[int, dict] = {int(s.get("page_num", -1)): s for s in signatures}

    def _page_complexity(p: int) -> int:
        s = sig_by_page.get(p, {})
        return (int(bool(s.get("table_candidate")))
                + int(bool(s.get("equation_candidate")))
                + int(bool(s.get("figure_candidate")))
                + int(bool(s.get("has_images"))))

    regions = _outline_to_regions(outline, total_pages)

    def _region_priority(r: dict) -> tuple:
        h = r["hints"]
        type_rank = 0 if "tables" in h else 1 if "requirements" in h else 2 if "figures" in h else 3 if "appendix" in h else 4
        return (type_rank, -r["size"])

    # Phase 1: One representative window per structural region
    for region in sorted(regions, key=_region_priority):
        pages_in_region = list(range(region["start"], region["end"] + 1))
        if not pages_in_region:
            continue
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

    # Phase 2: Span windows
    table_pages = {int(s.get("page_num", -1)) for s in signatures if s.get("table_candidate")}
    for p in sorted(table_pages):
        if p + 1 in table_pages:
            add_window([p, p + 1], "span", "table_continuation")

    # Phase 3: Fill remaining slots
    large_regions = sorted(regions, key=lambda r: r["size"], reverse=True)
    for region in large_regions:
        pages_in_region = list(range(region["start"], region["end"] + 1))
        random.shuffle(pages_in_region)
        for p in pages_in_region:
            add_window([p], "anchor", f"region_fill:{region['title'][:30]}")

    # Phase 4: Backfill
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


def render_windows(
    pdf_path: str,
    sampling_plan: dict,
    output_dir: str,
    page_signatures: list[dict] | None = None,
) -> list[dict]:
    """Render each window in sampling_plan to PNGs and span JSON files."""
    doc = pdf_oxide.PdfDocument(pdf_path)
    rendered: list[dict] = []

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

            png_path = os.path.join(win_dir, f"page_{page_num}.png")
            pdf_page_1based = page_num + 1
            prefix = os.path.join(win_dir, f"_render_{page_num}")
            subprocess.run(
                ["pdftoppm", "-png", "-f", str(pdf_page_1based), "-l", str(pdf_page_1based),
                 "-r", "150", pdf_path, prefix],
                capture_output=True, timeout=30,
            )
            rendered_file = f"{prefix}-{pdf_page_1based}.png"
            if os.path.exists(rendered_file):
                os.rename(rendered_file, png_path)
            elif os.path.exists(f"{prefix}.png"):
                os.rename(f"{prefix}.png", png_path)
            png_paths.append(png_path)

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

        # Extract window pages into a mini-PDF
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


def build_clone_manifest(
    profile: dict,
    sampling_plan: dict,
    doc,
    output_dir: str | None = None,
) -> list[dict]:
    """Enrich each window in sampling_plan with structural metadata from profile."""
    toc_sections = profile.get("toc_sections", []) or []
    table_shapes = profile.get("table_shapes", []) or []
    spanning_tables = profile.get("page_spanning_tables", []) or []
    requirements_pages = set(profile.get("requirements_pages", []) or [])
    running_headers = profile.get("running_headers", []) or []
    running_footers = profile.get("running_footers", []) or []
    page_signatures = profile.get("page_signatures", []) or []

    tables_by_page: dict[int, list[dict]] = {}
    for ts in table_shapes:
        pg = int(ts.get("page", -1))
        tables_by_page.setdefault(pg, []).append(ts)

    sig_by_page: dict[int, dict] = {int(s.get("page_num", -1)): s for s in page_signatures}
    _clause_pat = re.compile(r"\d+\.\d+\.\d+")

    enriched: list[dict] = []

    for window in sampling_plan.get("windows", []):
        source_pages: list[int] = [int(p) for p in window.get("source_pages", [])]
        page_set = set(source_pages)

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

        matched_spanning = None
        for span in spanning_tables:
            sp_start = int(span.get("start_page", -1))
            sp_end = int(span.get("end_page", -1))
            if any(sp_start <= pg <= sp_end for pg in source_pages):
                matched_spanning = span
                break

        is_requirements = bool(page_set & requirements_pages)
        running_header = running_headers[0] if running_headers else None
        running_footer = running_footers[0] if running_footers else None
        page_char_counts = [sig_by_page.get(pg, {}).get("char_count", 0) for pg in source_pages]
        has_images = any(sig_by_page.get(pg, {}).get("has_images", False) for pg in source_pages)
        has_equations = any(sig_by_page.get(pg, {}).get("equation_candidate", False) for pg in source_pages)

        clause_ids: set[str] = set()
        for pg in source_pages:
            try:
                text = doc.extract_text(pg)
                for m in _clause_pat.findall(text):
                    clause_ids.add(m)
            except Exception:
                pass
        clause_count = len(clause_ids)

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

    if output_dir:
        manifest_path = os.path.join(output_dir, "clone_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(enriched, f, indent=2)
        print(f"Clone manifest written to {manifest_path}")

    return enriched
