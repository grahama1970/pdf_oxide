"""PDF Cloner: profile → sample → manifest → LLM → execute → score → iterate → stitch.

Thin orchestration layer. Domain logic lives in:
- clone_profiler.py — profiling + family assignment
- clone_sampler.py — sampling, rendering, manifest building
- clone_additive.py — error injection, figures, filler, stitching
- clone_scorer.py — structural comparison scoring
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess

import typer
from loguru import logger

import pdf_oxide

# Re-export public API for backward compatibility
from pdf_oxide.clone_profiler import (
    assign_family,
    profile_and_assign,
    profile_for_cloning,
)
from pdf_oxide.clone_sampler import (
    build_clone_manifest,
    build_sampling_plan,
    render_windows,
)
from pdf_oxide.clone_additive import (
    _CORRUPTION_QID_OFFSET,
    _QID_PAGE_MULTIPLIER,
    build_structural_qid_map,
    encode_qid,
    find_all_qids,
    generate_figure,
    generate_filler_page,
    inject_errors,
    inject_qids,
    inject_structural_qids,
    insert_figures,
    stitch_pages,
)

app = typer.Typer(name="clone_pdf", help="PDF Cloner — profile, sample, clone, score")


# ── Clone loop ────────────────────────────────────────────────────────

SCILLM_URL = os.environ.get("SCILLM_URL", "http://localhost:4001")
SCILLM_KEY = os.environ.get("SCILLM_PROXY_KEY", "sk-dev-proxy-123")
_SCILLM_HEADERS = {"Authorization": f"Bearer {SCILLM_KEY}", "Content-Type": "application/json"}

_CLONE_SYSTEM = """You are a ReportLab PDF recreation expert. You receive a PDF page as an
attachment plus a structural description extracted from the original. Write a
self-contained Python script that produces a synthetic PDF matching the layout.

Your output is scored by extracting these elements from both PDFs and comparing:
- Running headers and footers (exact text match)
- Section headings and subheadings (text + hierarchy level)
- Tables (row count, column count, cell text content)
- Body paragraphs (text blocks in reading order)
- Requirement clauses (numbered items like "3.1.1 Limit information system...")
- Figure captions (text below figures)
- Footnotes (small text at page bottom)
- Equations (if present — use text representation)
Matching the text content exactly is most important. Layout positions matter less.

RULES:
1. Use DejaVu font for ALL text — never Helvetica or Times-Roman.
2. Include the _qid() helper and font registration below at the top of your script.
3. For each QID in the assignment table, prepend _qid(N) to that EXACT text string.
   The _qid() output is invisible zero-width characters — it won't affect layout.
4. If the page has figures/charts/images, do NOT attempt to draw them. Instead:
   - Draw a placeholder: c.rect(x, y, w, h, fill=1) with light gray fill
   - Add a comment: # FIGURE: bbox=(x,y,w,h) description="<what you see>"
   - Add a visible caption below with its QID: _qid(N) + "Figure X: <caption>"

```python
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
pdfmetrics.registerFont(TTFont('DejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
pdfmetrics.registerFont(TTFont('DejaVu-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))

def _qid(n):
    S, E, B0, B1 = '\\u200b', '\\u2060', '\\u200c', '\\u200d'
    if n == 0: return S + B0 + E
    bits = []
    v = n
    while v > 0:
        bits.append(B1 if (v & 1) else B0)
        v >>= 1
    return S + ''.join(reversed(bits)) + E
```

Output ONLY Python code."""


def _build_page_structure(span_paths: list[str], brief: dict) -> str:
    """Build a page structure description from extracted span JSON files.

    Groups spans into structural zones (header, headings, body, table, footer)
    so the LLM knows what's on the page before it looks at the PDF image.
    """
    import json as _json

    all_spans: list[dict] = []
    for path in span_paths:
        if os.path.exists(path):
            with open(path) as f:
                all_spans.extend(_json.load(f))

    if not all_spans:
        return ""

    # Group by y-position bands (round to nearest int)
    zones: dict[int, list[dict]] = {}
    for s in all_spans:
        y = round(s["bbox"][1])
        zones.setdefault(y, []).append(s)

    lines = ["Page structure (extracted from the original PDF):\n"]
    page_height = 792  # letter

    # Sort top to bottom
    sorted_ys = sorted(zones.keys(), reverse=True)

    for y in sorted_ys:
        spans = zones[y]
        text = " ".join(s["text"].strip() for s in spans if s["text"].strip())
        if not text:
            continue

        fonts = set(s["font_name"] for s in spans)
        sizes = sorted(set(round(s["font_size"], 1) for s in spans))
        size_str = f"{sizes[0]}pt" if len(sizes) == 1 else f"{sizes[0]}-{sizes[-1]}pt"

        # Classify by position
        if y > page_height - 80:  # top 80pt = header zone
            label = "HEADER"
        elif y < 60:  # bottom 60pt = footer zone
            label = "FOOTER"
        elif any("Bold" in f or "bold" in f or f.endswith("3") or f.endswith("4") for f in fonts):
            if sizes and sizes[-1] >= 11:
                label = "HEADING"
            else:
                label = "BOLD"
        else:
            label = "TEXT"

        # Truncate long text for prompt efficiency
        display_text = text[:120] + "..." if len(text) > 120 else text
        lines.append(f'  {label} (y={y}, {size_str}): "{display_text}"')

    # Add table summary if present
    tables = brief.get("tables", [])
    if tables:
        for t in tables:
            lines.append(f"\n  TABLE: {t.get('rows')}r x {t.get('cols')}c, "
                         f"bbox=({t.get('bbox', [0,0,0,0])[0]:.0f}, {t.get('bbox', [0,0,0,0])[1]:.0f}, "
                         f"{t.get('bbox', [0,0,0,0])[2]:.0f}, {t.get('bbox', [0,0,0,0])[3]:.0f})")

    spanning = brief.get("spanning_table")
    if spanning:
        lines.append(f"\n  SPANNING TABLE: pages {spanning.get('start_page')}-{spanning.get('end_page')}, "
                     f"{spanning.get('total_rows')} total rows, {spanning.get('cols')} cols")

    if brief.get("has_images"):
        lines.append("\n  PAGE CONTAINS FIGURES/IMAGES — describe what you see and use placeholder rectangles")

    return "\n".join(lines)


def _build_qid_instructions(
    brief: dict,
    source_pages: list[int],
    span_paths: list[str] | None = None,
) -> str:
    """Build QID assignments tied to real extracted text from spans."""
    import json as _json
    from pdf_oxide.clone_additive import _QID_PAGE_MULTIPLIER, _STRUCTURAL_QID_OFFSET

    page_num = source_pages[0] if source_pages else 0
    qid_base = page_num * _QID_PAGE_MULTIPLIER + _STRUCTURAL_QID_OFFSET
    idx = 0

    # Load spans to find real text for QID targets
    all_spans: list[dict] = []
    if span_paths:
        for path in span_paths:
            if os.path.exists(path):
                with open(path) as f:
                    all_spans.extend(_json.load(f))

    # Find header text (top of page, small font)
    header_spans = [s for s in all_spans if s["bbox"][1] > 720 and s["text"].strip()]
    header_text = " ".join(s["text"].strip() for s in header_spans[:2]) if header_spans else None

    # Find footer text (bottom of page)
    footer_spans = [s for s in all_spans if s["bbox"][1] < 60 and s["text"].strip()]
    footer_text = " ".join(s["text"].strip() for s in footer_spans[:2]) if footer_spans else None

    # Find first bold/large text (heading or table title)
    heading_spans = sorted(
        [s for s in all_spans if s["text"].strip() and s["font_size"] >= 10
         and s["bbox"][1] < 720 and s["bbox"][1] > 60],
        key=lambda s: -s["bbox"][1],  # top first
    )
    heading_text = heading_spans[0]["text"].strip()[:80] if heading_spans else None

    # Find first table cell text (smaller font, in table bbox area)
    tables = brief.get("tables", [])
    table_cell_text = None
    if tables and all_spans:
        tbox = tables[0].get("bbox", [0, 0, 612, 792])
        table_spans = [s for s in all_spans
                       if tbox[1] <= s["bbox"][1] <= tbox[3]
                       and s["text"].strip()
                       and len(s["text"].strip()) > 2]
        if table_spans:
            # First span in the table area (top-down)
            table_spans.sort(key=lambda s: -s["bbox"][1])
            table_cell_text = table_spans[0]["text"].strip()[:60]

    lines = [
        "QID assignments — prepend _qid(N) to these EXACT text strings:\n"
    ]

    if header_text:
        idx += 1
        lines.append(f'  _qid({qid_base + idx}) → "{header_text[:60]}"')
        lines.append(f'                 (running header)')

    if heading_text:
        idx += 1
        lines.append(f'  _qid({qid_base + idx}) → "{heading_text}"')
        lines.append(f'                 (heading / title)')

    if table_cell_text:
        idx += 1
        lines.append(f'  _qid({qid_base + idx}) → "{table_cell_text}"')
        lines.append(f'                 (first text in table)')

    if brief.get("has_images"):
        idx += 1
        lines.append(f'  _qid({qid_base + idx}) → figure caption text')
        lines.append(f'                 (caption you write below the figure placeholder)')

    if footer_text:
        idx += 1
        lines.append(f'  _qid({qid_base + idx}) → "{footer_text[:60]}"')
        lines.append(f'                 (running footer)')

    if idx == 0:
        return ""
    return "\n".join(lines)


async def clone_pdf(
    pdf_path: str,
    output_dir: str,
    max_windows: int = 5,
    seed: int = 42,
    model: str = "claude-opus-4-6",
    max_rounds: int = 5,
    inject_errors_enabled: bool = False,
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

        with open(window_pdf, "rb") as f:
            pdf_b64 = base64.b64encode(f.read()).decode()
        num_pages = len(win["source_pages"])

        # Find span files for this window
        span_paths = [
            os.path.join(win_dir, f"spans_{pg}.json")
            for pg in win["source_pages"]
        ]

        page_structure = _build_page_structure(span_paths, brief)
        qid_instructions = _build_qid_instructions(brief, win["source_pages"], span_paths)

        user_text = (
            f"Recreate the attached {num_pages}-page PDF using ReportLab.\n\n"
            + (f"{page_structure}\n\n" if page_structure else "")
            + f"Output: {num_pages} page(s), letter size (612x792 pts).\n"
            f"Save to: {synthetic_pdf}\n"
            f"Run with: .venv/bin/python {code_path}\n"
            + (f"\n{qid_instructions}\n" if qid_instructions else "")
            + f"\nCode only."
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

            if "```python" in content:
                code = content.split("```python")[1].split("```")[0].strip()
            elif "```" in content:
                code = content.split("```")[1].split("```")[0].strip()
            else:
                code = content.strip()

            with open(code_path, "w", encoding="utf-8") as f:
                f.write(code)

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

                win_idx = manifest.index(win)

                # ── Step 1: Verify structural QIDs the LLM embedded ──
                structural_entries = build_structural_qid_map(brief, win["source_pages"])
                try:
                    synth_doc = pdf_oxide.PdfDocument(synthetic_pdf)
                    synth_text = "".join(
                        synth_doc.extract_text(p) for p in range(synth_doc.page_count())
                    )
                except Exception:
                    synth_text = ""
                found_struct_qids = {q for q, _ in find_all_qids(synth_text)}
                for entry in structural_entries:
                    entry["verified"] = entry["qid"] in found_struct_qids
                    if not entry["verified"]:
                        entry["failure_reason"] = "qid_not_in_pdf"
                verified_structural = [e for e in structural_entries if e["verified"]]
                win_result["structural_qids"] = structural_entries  # keep all for debugging
                logger.info(
                    f"{wid}: {len(verified_structural)}/{len(structural_entries)} "
                    f"structural QIDs verified"
                )

                # ── Step 2: Inject corruptions + corruption QIDs ──
                if inject_errors_enabled:
                    with open(code_path, "r", encoding="utf-8") as f:
                        clean_code = f.read()
                    errored_code, error_manifest = inject_errors(
                        clean_code, seed=seed + win_idx, track=True,
                    )
                    errored_code_path = os.path.join(win_dir, "reportlab_code_errors.py")
                    errored_pdf = os.path.join(win_dir, "synthetic_errors.pdf")
                    errored_code = errored_code.replace(synthetic_pdf, errored_pdf)

                    # Corruption QIDs start at page*10000 + _CORRUPTION_QID_OFFSET
                    page_num = win["source_pages"][0] if win["source_pages"] else 0
                    qid_base = page_num * _QID_PAGE_MULTIPLIER + _CORRUPTION_QID_OFFSET
                    qid_map = []
                    for ci, entry in enumerate(error_manifest):
                        entry["qid"] = qid_base + ci + 1
                        qid_map.append({"qid": entry["qid"], "label": entry["corrupted"]})
                    errored_code = inject_qids(errored_code, qid_map)

                    with open(errored_code_path, "w", encoding="utf-8") as f:
                        f.write(errored_code)
                    try:
                        subprocess.run(
                            [".venv/bin/python", errored_code_path],
                            capture_output=True, text=True, timeout=30,
                            cwd=os.path.dirname(os.path.dirname(os.path.dirname(
                                os.path.abspath(__file__)))),
                        )
                        if os.path.exists(errored_pdf):
                            win_result["errored_pdf"] = errored_pdf
                            try:
                                err_doc = pdf_oxide.PdfDocument(errored_pdf)
                                err_text = "".join(
                                    err_doc.extract_text(p) for p in range(err_doc.page_count())
                                )
                            except Exception:
                                err_text = ""
                            verified_count = 0
                            found_qids = find_all_qids(err_text)
                            found_qid_set = {q for q, _ in found_qids}
                            for entry in error_manifest:
                                text_present = entry["corrupted"] in err_text
                                qid_present = entry.get("qid") in found_qid_set
                                entry["verified"] = text_present and qid_present
                                entry["qid_verified"] = qid_present
                                if not text_present:
                                    entry["failure_reason"] = "text_not_found"
                                elif not qid_present:
                                    entry["failure_reason"] = "qid_not_found"
                                if entry["verified"]:
                                    verified_count += 1
                            win_result["corruption_count"] = len([e for e in error_manifest if e["verified"]])
                            sidecar_path = os.path.join(win_dir, "corruption_manifest.json")
                            with open(sidecar_path, "w", encoding="utf-8") as f:
                                json.dump({
                                    "window_id": wid,
                                    "source_pages": win["source_pages"],
                                    "seed": seed + win_idx,
                                    "error_rate": 0.05,
                                    "injected": len(error_manifest),
                                    "verified": verified_count,
                                    "structural_qids": verified_structural,
                                    "corruptions": error_manifest,  # keep all, gate on verified downstream
                                }, f, indent=2, ensure_ascii=False)
                            win_result["corruption_manifest"] = sidecar_path
                            logger.info(
                                f"{wid}: error-injected PDF at {errored_pdf} "
                                f"({verified_count}/{len(error_manifest)} corruptions verified)"
                            )
                    except Exception as e:
                        logger.warning(f"{wid}: error injection failed: {e}")
                break

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

    if summary["passed"] > 0:
        corrupt_mode = "all" if inject_errors_enabled else None
        stitched = stitch_pages(
            pdf_path, output_dir, results, profile,
            seed=seed, corrupt=corrupt_mode,
            use_errored=inject_errors_enabled,
        )
        if stitched:
            summary["stitched_pdf"] = stitched
            stitched_pages = pdf_oxide.PdfDocument(stitched).page_count()
            summary["stitched_pages"] = stitched_pages
            logger.info(f"Stitched PDF: {stitched} ({stitched_pages} pages)")

        # Assemble single document manifest: structural expectations + corruptions
        pages: list[dict] = []
        all_corruptions: list[dict] = []
        for win_manifest_entry in manifest:
            win_id = win_manifest_entry["window_id"]
            brief = win_manifest_entry.get("clone_brief", {})
            win_result = next(
                (r for r in results if r.get("window_id") == win_id and r.get("status") == "pass"),
                None,
            )
            if not win_result:
                continue

            # Collect per-window structural QIDs and corruptions
            win_structural = win_result.get("structural_qids", [])
            win_corruptions: list[dict] = []
            if inject_errors_enabled:
                cm_path = win_result.get("corruption_manifest")
                if cm_path and os.path.exists(cm_path):
                    with open(cm_path) as f:
                        win_cm = json.load(f)
                    win_corruptions = win_cm.get("corruptions", [])
                    all_corruptions.extend(win_corruptions)

            for pg in win_manifest_entry["source_pages"]:
                pages.append({
                    "page": pg,
                    "window_id": win_id,
                    "source": "synthetic",
                    "content_type": brief.get("content_type"),
                    "toc_section": brief.get("toc_section"),
                    "toc_parent": brief.get("toc_parent"),
                    "tables": brief.get("tables", []),
                    "spanning_table": brief.get("spanning_table"),
                    "is_requirements": brief.get("is_requirements", False),
                    "clause_count": brief.get("clause_count", 0),
                    "running_header": brief.get("running_header"),
                    "running_footer": brief.get("running_footer"),
                    "char_count": brief.get("page_char_counts", [0])[0] if brief.get("page_char_counts") else 0,
                    "has_images": brief.get("has_images", False),
                    "has_equations": brief.get("has_equations", False),
                    "score": win_result.get("score", {}),
                    "structural_qids": win_structural,
                    "corruptions": [c for c in win_corruptions],
                })

        all_structural_qids = []
        for pg in pages:
            all_structural_qids.extend(pg.get("structural_qids", []))

        doc_manifest_path = os.path.join(output_dir, "document_manifest.json")
        with open(doc_manifest_path, "w", encoding="utf-8") as f:
            json.dump({
                "stitched_pdf": stitched,
                "original_pdf": pdf_path,
                "total_pages": profile.get("page_count", 0),
                "synthetic_pages": len(pages),
                "filler_pages": profile.get("page_count", 0) - len(pages),
                "total_structural_qids": len(all_structural_qids),
                "total_corruptions": len(all_corruptions),
                "pages": pages,
            }, f, indent=2, ensure_ascii=False)
        summary["document_manifest"] = doc_manifest_path
        logger.info(
            f"Document manifest: {doc_manifest_path} "
            f"({len(pages)} pages, {len(all_corruptions)} corruptions)"
        )

    with open(os.path.join(output_dir, "clone_summary.json"), "w") as f:
        json.dump(summary, f, indent=2, default=str)

    return summary


# ── CLI commands ──────────────────────────────────────────────────────

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


@app.command("stitch")
def stitch_cmd(
    pdf_path: str = typer.Argument(..., help="Path to original PDF file"),
    output_dir: str = typer.Option("/tmp/clone_output", "-o", help="Clone output directory (must have clone_summary.json)"),
    seed: int = typer.Option(42, "--seed", help="Random seed"),
    corrupt: str = typer.Option(None, "--corrupt", help="Corruption type for filler pages (e.g. 'all', 'ligature')"),
) -> None:
    """Stitch cloned windows + filler pages into a full N-page document."""
    summary_path = os.path.join(output_dir, "clone_summary.json")
    if not os.path.exists(summary_path):
        typer.echo(f"No clone_summary.json in {output_dir} — run clone first")
        raise typer.Exit(1)
    with open(summary_path) as f:
        summary = json.load(f)
    prof = profile_for_cloning(pdf_path)
    result = stitch_pages(
        pdf_path, output_dir, summary["windows"], prof,
        seed=seed, corrupt=corrupt,
    )
    if result:
        page_count = pdf_oxide.PdfDocument(result).page_count()
        typer.echo(f"Stitched: {result} ({page_count} pages)")
    else:
        typer.echo("Stitching failed")


if __name__ == "__main__":
    app()
