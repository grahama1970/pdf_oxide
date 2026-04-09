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

_CLONE_SYSTEM_PASS1 = """You are a ReportLab layout expert. You receive a PDF page as an
attachment plus a structural description. Write a self-contained Python script
that reproduces the page layout: positions, fonts, sizes, structure, spacing.

Focus on getting the LAYOUT right:
- Running headers/footers: use drawString at correct y-positions (these are short, single-line)
- Section headings: use drawString at correct sizes and positions (also short, single-line)
- Tables: use reportlab.platypus.Table with explicit column widths and TableStyle grid lines.
  NEVER use drawString for table cell text — Table() handles cell wrapping and alignment.
- Body paragraphs: use reportlab.platypus.Paragraph with a defined width so text wraps
  within margins. Draw with para.wrapOn(c, width, height) then para.drawOn(c, x, y).
  Page width is 612pt with 72pt margins on each side = 468pt text width.
  NEVER use drawString for text longer than ~60 characters — it will overflow the margin.
- Bullet lists: use Paragraph with bullet markup or ListFlowable
- Figure placeholders at correct positions and sizes

Use the text you see in the PDF. Do not worry about exact verbatim accuracy —
a second pass will fix the text. Get the structure and positions right.

RULES:
1. Use the document's actual fonts as specified in the user message. Include the
   font registration code provided. Use DejaVu ONLY for _qid() prefix strings.
   Never use Helvetica or Times-Roman — use the registered TTF fonts.
2. Include the font registration code from the user message at the top of your script.
3. If the page has figures/charts/images, do NOT attempt to draw them. Instead:
   - Draw a placeholder: c.setFillColorRGB(0.9, 0.9, 0.9); c.rect(x, y, w, h, fill=1)
   - Add a comment: # FIGURE: bbox=(x,y,w,h) description="<what you see>"
   - Add a visible caption below: "Figure X: <caption>"

REJECTION CRITERIA — your code is WRONG if:
- You use Helvetica, Times-Roman, or any font other than DejaVu/DejaVu-Bold
- You add decorative elements not in the original (drop caps, circles, borders, shadows, clip art)
- You embellish text formatting beyond what the PDF shows (no artistic fonts, no ornamental styling)
- The script does not save a PDF to the exact path given in the user message
- You output anything besides Python code (no markdown, no explanation)

```python
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
pdfmetrics.registerFont(TTFont('DejaVu', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
pdfmetrics.registerFont(TTFont('DejaVu-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
```

Output ONLY Python code."""


_CLONE_SYSTEM_PASS2 = """You are a compliance editor for ReportLab scripts. You receive
a working ReportLab script (from pass 1) plus exact text strings from the original PDF
and optional visual feedback. Your jobs:
1. Replace approximate text with the EXACT extracted text.
2. Prepend invisible _qid(N) markers to specified strings.
3. Fix any structural issues noted in the feedback (e.g. use Table() instead of drawString).

RULES:
1. Include the _qid() helper below at the top of the script.
2. For each QID in the assignment table, prepend _qid(N) to the FIRST occurrence
   of that exact text string. The _qid() output is invisible zero-width characters.
3. Replace any paraphrased or approximate text with the EXACT extracted text
   provided in the "Verbatim text replacements" section.
4. If structural feedback says to use Table() for tables, refactor drawString calls
   into a reportlab.platypus.Table with proper column widths and TableStyle.
5. Do not add decorative elements (drop caps, circles, borders, ornaments).

REJECTION CRITERIA — your code is WRONG if:
- Any _qid(N) from the assignment table is missing from the code
- You paraphrase or reword the QID target text instead of using it verbatim
- Table cell text uses drawString instead of Table() when feedback says to fix it
- The script does not save a PDF to the exact path given
- You output anything besides Python code

IMPORTANT: The _qid() invisible characters ONLY work with the DejaVu font.
When prepending a QID to text, use this pattern:
  current_font = 'TimesNewRoman'  # whatever font you're using
  c.setFont('DejaVu', 8)
  c.drawString(x, y, _qid(N))  # zero-width, takes no horizontal space
  c.setFont(current_font, 8)
  c.drawString(x, y, "visible text")  # draws at same x position

```python
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

Output ONLY the complete corrected Python script."""


_FONT_MAP = {
    "ArialMT": ("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf", "Arial"),
    "Arial-BoldMT": ("/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf", "Arial-Bold"),
    "Arial-ItalicMT": ("/usr/share/fonts/truetype/msttcorefonts/Arial_Italic.ttf", "Arial-Italic"),
    "Arial-BoldItalicMT": ("/usr/share/fonts/truetype/msttcorefonts/Arial_Bold_Italic.ttf", "Arial-BoldItalic"),
    "ArialNarrow": ("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf", "ArialNarrow"),  # fallback
    "TimesNewRomanPSMT": ("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf", "TimesNewRoman"),
    "TimesNewRomanPS-BoldMT": ("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Bold.ttf", "TimesNewRoman-Bold"),
    "TimesNewRomanPS-ItalicMT": ("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Italic.ttf", "TimesNewRoman-Italic"),
    "TimesNewRomanPS-BoldItalicMT": ("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman_Bold_Italic.ttf", "TimesNewRoman-BoldItalic"),
    "CourierNewPSMT": ("/usr/share/fonts/truetype/msttcorefonts/Courier_New.ttf", "CourierNew"),
}


def _detect_pdf_fonts(pdf_path: str, pages: list[int]) -> dict[str, tuple[str, str]]:
    """Detect fonts used in specific PDF pages.

    Returns dict mapping internal font name → (ttf_path, reportlab_name).
    Falls back to DejaVu for unknown fonts.
    """
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    found: dict[str, tuple[str, str]] = {}
    dejavu = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVu")
    dejavu_bold = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVu-Bold")

    for pg in pages:
        if pg >= len(reader.pages):
            continue
        page = reader.pages[pg]
        resources = page.get("/Resources")
        if not resources:
            continue
        fonts = resources.get("/Font", {})
        for name, font_ref in fonts.items():
            try:
                font = font_ref.get_object()
                base = str(font.get("/BaseFont", "")).lstrip("/")
                # Strip subset prefix (e.g., AUIVAS+SymbolMT → SymbolMT)
                if "+" in base:
                    base = base.split("+", 1)[1]
                if base in _FONT_MAP:
                    path, rname = _FONT_MAP[base]
                    if os.path.exists(path):
                        found[base] = (path, rname)
            except Exception:
                continue

    # Always include DejaVu as fallback (needed for QID zero-width chars)
    found["DejaVu"] = dejavu
    found["DejaVu-Bold"] = dejavu_bold
    return found


def _build_font_registration(fonts: dict[str, tuple[str, str]]) -> str:
    """Build ReportLab font registration code from detected fonts."""
    lines = [
        "from reportlab.pdfbase import pdfmetrics",
        "from reportlab.pdfbase.ttfonts import TTFont",
    ]
    for base, (path, rname) in sorted(fonts.items()):
        lines.append(f"pdfmetrics.registerFont(TTFont('{rname}', '{path}'))")
    return "\n".join(lines)


def _build_font_instructions(fonts: dict[str, tuple[str, str]]) -> str:
    """Build prompt instructions telling the LLM which fonts to use."""
    if not fonts:
        return ""
    lines = ["Fonts available (use these instead of Helvetica/Times-Roman):\n"]
    for base, (_, rname) in sorted(fonts.items()):
        if base.startswith("DejaVu"):
            continue
        lines.append(f"  '{rname}' — mapped from PDF font {base}")
    lines.append(f"  'DejaVu' / 'DejaVu-Bold' — fallback, required for _qid() strings")
    lines.append("\nUse the document's actual fonts for all text. Use DejaVu ONLY for")
    lines.append("the _qid() prefix (which is invisible zero-width characters).")
    return "\n".join(lines)


def _extract_code(content: str) -> str:
    """Extract Python code from LLM response, stripping markdown fences."""
    if "```python" in content:
        code = content.split("```python")[1].split("```")[0].strip()
    elif "```" in content:
        code = content.split("```")[1].split("```")[0].strip()
    else:
        code = content.strip()
    return _strip_dropcaps(code)


def _strip_dropcaps(code: str) -> str:
    """Remove drop cap / decorative text embellishments from ReportLab code.

    LLMs like to add large initial letters (drop caps), circles, ellipses,
    and other decorative elements that don't exist in the original PDF.
    This strips them deterministically.
    """
    import re
    lines = code.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # Remove circle/ellipse drawing (decorative, not in originals)
        if re.match(r'c\.(circle|ellipse)\(', stripped):
            continue
        # Remove oversized single-character drawString (drop caps)
        # Pattern: drawString with a 1-char string at font size >= 24
        if re.match(r"c\.drawString\(.+,\s*['\"].\s*['\"]", stripped):
            # Check if preceded by a setFont with large size
            if cleaned and re.search(r"setFont\(.+,\s*(\d+)", cleaned[-1]):
                size = int(re.search(r"setFont\(.+,\s*(\d+)", cleaned[-1]).group(1))
                if size >= 24:
                    cleaned.pop()  # remove the setFont too
                    continue
        # Remove setFontSize calls for drop caps (>= 36pt single use)
        if re.match(r'c\.setFontSize\(\s*(\d+)', stripped):
            size = int(re.match(r'c\.setFontSize\(\s*(\d+)', stripped).group(1))
            if size >= 36:
                continue
        cleaned.append(line)
    return "\n".join(cleaned)


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

    # Compress: if more than 25 zones, keep only header/footer zones + first/last
    # content zones + a summary count (avoids context dilution on table pages)
    max_zones = 25

    lines = ["Page structure (extracted from the original PDF):\n"]
    page_height = 792  # letter

    # Sort top to bottom
    sorted_ys = sorted(zones.keys(), reverse=True)

    # If too many zones, keep header (top 5), footer (bottom 3), first/last content
    if len(sorted_ys) > max_zones:
        header_ys = [y for y in sorted_ys if y > 712][:5]
        footer_ys = [y for y in sorted_ys if y < 60][:3]
        content_ys = [y for y in sorted_ys if 60 <= y <= 712]
        # Keep first 8 and last 4 content zones + a gap marker
        kept_content = content_ys[:8] + content_ys[-4:]
        sorted_ys = sorted(set(header_ys + kept_content + footer_ys), reverse=True)
        lines.append(f"  (page has {len(zones)} text zones total — showing {len(sorted_ys)} key zones)\n")

    zone_count = 0
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


def _load_spans(span_paths: list[str] | None) -> list[dict]:
    """Load span JSON files into a flat list."""
    import json as _json
    all_spans: list[dict] = []
    if span_paths:
        for path in span_paths:
            if os.path.exists(path):
                with open(path) as f:
                    all_spans.extend(_json.load(f))
    return all_spans


def _extract_key_text(brief: dict, all_spans: list[dict]) -> dict:
    """Extract key text strings from spans for QID targeting.

    Returns dict with keys: header_text, footer_text, heading_text, table_cell_text.
    Values are None if not found.
    """
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
            table_spans.sort(key=lambda s: -s["bbox"][1])
            table_cell_text = table_spans[0]["text"].strip()[:60]

    return {
        "header_text": header_text,
        "footer_text": footer_text,
        "heading_text": heading_text,
        "table_cell_text": table_cell_text,
    }


def _build_qid_instructions(
    brief: dict,
    source_pages: list[int],
    span_paths: list[str] | None = None,
) -> str:
    """Build QID assignments tied to real extracted text from spans."""
    from pdf_oxide.clone_additive import _QID_PAGE_MULTIPLIER, _STRUCTURAL_QID_OFFSET

    page_num = source_pages[0] if source_pages else 0
    qid_base = page_num * _QID_PAGE_MULTIPLIER + _STRUCTURAL_QID_OFFSET
    idx = 0

    all_spans = _load_spans(span_paths)
    kt = _extract_key_text(brief, all_spans)

    lines = [
        "QID assignments — prepend _qid(N) to these EXACT text strings:\n"
    ]

    if kt["header_text"]:
        idx += 1
        lines.append(f'  _qid({qid_base + idx}) → "{kt["header_text"][:60]}"')
        lines.append(f'                 (running header)')

    if kt["heading_text"]:
        idx += 1
        lines.append(f'  _qid({qid_base + idx}) → "{kt["heading_text"]}"')
        lines.append(f'                 (heading / title)')

    if kt["table_cell_text"]:
        idx += 1
        lines.append(f'  _qid({qid_base + idx}) → "{kt["table_cell_text"]}"')
        lines.append(f'                 (first text in table)')

    if brief.get("has_images"):
        idx += 1
        lines.append(f'  _qid({qid_base + idx}) → figure caption text')
        lines.append(f'                 (caption you write below the figure placeholder)')

    if kt["footer_text"]:
        idx += 1
        lines.append(f'  _qid({qid_base + idx}) → "{kt["footer_text"][:60]}"')
        lines.append(f'                 (running footer)')

    if idx == 0:
        return ""
    return "\n".join(lines)


def _get_synthetic_text(content_type: str, domain: str, count: int, seed: int) -> list[dict]:
    """Get synthetic text chunks from /create-text corpus.

    Returns real extracted text from the datalake that matches the content type
    and domain of the page being cloned. This replaces the original text so
    the synthetic PDF has different content but the same structure.
    """
    import sys
    sys.path.insert(0, "/home/graham/.claude/skills/create-text")
    try:
        from create_text import create_text
        # Map clone content types to /create-text content types
        type_map = {
            "prose": "heading",  # fallback — prose not in all banks
            "requirements": "heading",
            "spanning_table": "table_cell",
            "table": "table_cell",
        }
        ct = type_map.get(content_type, "heading")
        # Try requested domain, fall back to government
        for d in [domain, "government", "nist", "engineering"]:
            try:
                chunks = create_text(content_type=ct, domain=d, count=count, seed=seed)
                if chunks:
                    return chunks
            except Exception:
                continue
        return []
    except ImportError:
        logger.warning("create-text skill not available, using original text")
        return []


def _build_synthetic_text_section(chunks: list[dict], brief: dict) -> str:
    """Build a text section for the LLM prompt using /create-text chunks.

    Tells the LLM to use these text chunks instead of copying from the PDF.
    """
    if not chunks:
        return ""
    lines = [
        "SYNTHETIC TEXT — use these text chunks instead of copying from the original PDF.",
        "Place each chunk in the appropriate location matching the page structure.\n",
    ]
    for i, chunk in enumerate(chunks):
        text = chunk.get("text", "").strip()[:200]
        ct = chunk.get("content_type", "text")
        lines.append(f"  Chunk {i+1} ({ct}): \"{text}\"")
    return "\n".join(lines)


def _build_verbatim_text_list(all_spans: list[dict]) -> str:
    """Build a list of verbatim text strings from spans for pass 2.

    Groups spans by y-zone and returns exact extracted text that the LLM
    should use to replace any paraphrased text in its pass-1 code.
    """
    if not all_spans:
        return ""

    zones: dict[int, list[dict]] = {}
    for s in all_spans:
        y = round(s["bbox"][1])
        zones.setdefault(y, []).append(s)

    lines = ["Verbatim text from the original PDF — replace approximate text with these EXACT strings:\n"]
    sorted_ys = sorted(zones.keys(), reverse=True)

    # Limit to 40 zones to keep prompt reasonable
    if len(sorted_ys) > 40:
        sorted_ys = sorted_ys[:20] + sorted_ys[-10:]

    for y in sorted_ys:
        spans = zones[y]
        text = " ".join(s["text"].strip() for s in spans if s["text"].strip())
        if not text or len(text) < 3:
            continue
        # Show exact text with y-position so LLM can match to its drawString calls
        lines.append(f'  y≈{y}: "{text[:150]}"')

    return "\n".join(lines)


def _get_expected_qids(brief: dict, source_pages: list[int]) -> set[int]:
    """Return the set of QID integers that should be in the generated code."""
    from pdf_oxide.clone_additive import _QID_PAGE_MULTIPLIER, _STRUCTURAL_QID_OFFSET
    page_num = source_pages[0] if source_pages else 0
    qid_base = page_num * _QID_PAGE_MULTIPLIER + _STRUCTURAL_QID_OFFSET
    qids = set()
    idx = 0
    # Mirror the order in _build_qid_instructions
    if any(s["bbox"][1] > 720 and s["text"].strip() for s in [] ):  # header — always assigned
        pass
    # Simpler: just count from 1 to however many QIDs were assigned
    # The QID instructions function assigns idx 1..N sequentially
    # We can parse from the instructions string, but easier: recompute
    idx = 0
    # header
    idx += 1; qids.add(qid_base + idx)
    # heading (if present — we always try)
    idx += 1; qids.add(qid_base + idx)
    # table cell (if tables)
    if brief.get("tables"):
        idx += 1; qids.add(qid_base + idx)
    # figure (if has_images)
    if brief.get("has_images"):
        idx += 1; qids.add(qid_base + idx)
    # footer
    idx += 1; qids.add(qid_base + idx)
    return qids


def _validate_code_qids(code: str, expected_qids: set[int]) -> list[int]:
    """Check which expected _qid(N) calls are missing from the generated code.
    Returns list of missing QID integers."""
    import re
    found = set()
    for m in re.finditer(r'_qid\(\s*(\d+)\s*\)', code):
        found.add(int(m.group(1)))
    return sorted(expected_qids - found)


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
        all_spans = _load_spans(span_paths)
        verbatim_text = _build_verbatim_text_list(all_spans)

        # Get synthetic text from /create-text corpus
        content_type = brief.get("content_type", "prose")
        domain = profile.get("domain", "government")
        # Estimate chunk count from span zones
        zone_count = len(set(round(s["bbox"][1]) for s in all_spans)) if all_spans else 10
        synthetic_chunks = _get_synthetic_text(
            content_type, domain, count=min(zone_count, 20),
            seed=seed + hash(wid) % 10000,
        )
        synthetic_text_section = _build_synthetic_text_section(synthetic_chunks, brief)

        # Detect fonts from original PDF
        detected_fonts = _detect_pdf_fonts(pdf_path, win["source_pages"])
        font_registration = _build_font_registration(detected_fonts)
        font_instructions = _build_font_instructions(detected_fonts)

        # Pass 1 user message: layout + structure + fonts, no QIDs
        pass1_user_text = (
            f"Recreate the attached {num_pages}-page PDF using ReportLab.\n\n"
            + (f"{page_structure}\n\n" if page_structure else "")
            + (f"{font_instructions}\n\n" if font_instructions else "")
            + f"Font registration code to include at the top of your script:\n```python\n{font_registration}\n```\n\n"
            + f"Output: {num_pages} page(s), letter size (612x792 pts).\n"
            f"Save to: {synthetic_pdf}\n"
            f"Run with: .venv/bin/python {code_path}\n"
            f"\nCode only."
        )

        # Pass 2 user message template: synthetic text + QIDs (code inserted at runtime)
        # Use /create-text corpus chunks instead of original text when available
        text_section = synthetic_text_section if synthetic_chunks else verbatim_text
        text_intro = (
            "Replace the text content with the synthetic text chunks below."
            if synthetic_chunks else
            "Fix the text to match the original PDF exactly."
        )
        pass2_user_template = (
            f"Here is the ReportLab script from pass 1. {text_intro}\n"
            "Add _qid() markers to the specified strings.\n\n"
            "```python\n{pass1_code}\n```\n\n"
            + (f"{text_section}\n\n" if text_section else "")
            + (f"{qid_instructions}\n\n" if qid_instructions else "")
            + f"Save to: {synthetic_pdf}\n"
            f"Output the complete corrected Python script. Code only."
        )

        pass1_conversation: list[dict] = [
            {"role": "system", "content": _CLONE_SYSTEM_PASS1},
            {"role": "user", "content": [
                {"type": "text", "text": pass1_user_text},
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

            # ── Pass 1: Layout ──
            logger.info(f"{wid} round {round_num}/{max_rounds} pass 1 (layout): calling {model}...")

            try:
                resp = httpx.post(
                    f"{SCILLM_URL}/v1/chat/completions",
                    json={"model": model, "max_tokens": 16384, "messages": pass1_conversation},
                    headers=_SCILLM_HEADERS,
                    timeout=120,
                )
                if resp.status_code != 200:
                    logger.error(f"{wid} round {round_num} pass 1: scillm {resp.status_code}")
                    win_result["error"] = f"scillm pass1 {resp.status_code}"
                    break
                pass1_content = resp.json()["choices"][0]["message"]["content"]
            except Exception as e:
                logger.error(f"{wid} round {round_num} pass 1: {e}")
                win_result["error"] = str(e)
                break

            pass1_code = _extract_code(pass1_content)

            # Quick syntax check on pass 1 before sending to pass 2
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(pass1_code)
            exec_result = subprocess.run(
                [".venv/bin/python", "-c", f"compile(open('{code_path}').read(), '{code_path}', 'exec')"],
                capture_output=True, text=True, timeout=10,
                cwd=os.path.dirname(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__)))),
            )
            if exec_result.returncode != 0:
                logger.warning(f"{wid} round {round_num} pass 1: syntax error, retrying")
                pass1_conversation.append({"role": "assistant", "content": pass1_content})
                pass1_conversation.append({"role": "user", "content": (
                    f"Syntax error:\n```\n{exec_result.stderr[:500]}\n```\n"
                    f"Fix the error. Write the complete corrected script. Code only."
                )})
                continue

            # ── Structural feedback for pass 2 ──
            structural_feedback = []
            has_tables = bool(brief.get("tables") or brief.get("spanning_table"))
            if has_tables and "Table(" not in pass1_code and pass1_code.count("drawString") > 20:
                structural_feedback.append(
                    "STRUCTURAL FIX REQUIRED: This page has a table but you used drawString "
                    "for cell text. Refactor into a reportlab.platypus.Table with proper "
                    "column widths and TableStyle grid. Use Paragraph() for cell content "
                    "that needs wrapping."
                )
            if "c.circle(" in pass1_code or "c.ellipse(" in pass1_code:
                structural_feedback.append(
                    "STRUCTURAL FIX REQUIRED: Remove decorative circles/ellipses — "
                    "the original PDF has no such elements."
                )
            # Detect long drawString lines that will overflow the right margin
            import re as _re
            long_draws = 0
            for m in _re.finditer(r"drawString\(.+?,\s*['\"](.{80,})['\"]", pass1_code):
                long_draws += 1
            if long_draws > 3 and "Paragraph(" not in pass1_code:
                structural_feedback.append(
                    "STRUCTURAL FIX REQUIRED: Body text uses drawString with long strings "
                    "that overflow the right margin. Use reportlab.platypus Paragraph() "
                    "with a defined width (e.g. 468pt = 612 - 72*2 margins) so text wraps. "
                    "Build paragraphs with SimpleDocTemplate or flowables drawn on canvas "
                    "via Paragraph.wrapOn()/drawOn()."
                )

            # ── Pass 2: Text + QIDs + structural fixes ──
            logger.info(f"{wid} round {round_num}/{max_rounds} pass 2 (text+QID): calling {model}...")

            structural_section = ""
            if structural_feedback:
                structural_section = "\n\nStructural issues to fix:\n" + "\n".join(structural_feedback) + "\n"

            pass2_user_text = pass2_user_template.format(pass1_code=pass1_code) + structural_section
            pass2_conversation: list[dict] = [
                {"role": "system", "content": _CLONE_SYSTEM_PASS2},
                {"role": "user", "content": pass2_user_text},
            ]

            try:
                resp = httpx.post(
                    f"{SCILLM_URL}/v1/chat/completions",
                    json={"model": model, "max_tokens": 16384, "messages": pass2_conversation},
                    headers=_SCILLM_HEADERS,
                    timeout=120,
                )
                if resp.status_code != 200:
                    logger.error(f"{wid} round {round_num} pass 2: scillm {resp.status_code}")
                    win_result["error"] = f"scillm pass2 {resp.status_code}"
                    break
                pass2_content = resp.json()["choices"][0]["message"]["content"]
            except Exception as e:
                logger.error(f"{wid} round {round_num} pass 2: {e}")
                win_result["error"] = str(e)
                break

            code = _extract_code(pass2_content)

            # Pre-exec validation: check all required _qid() calls are present
            expected_qids = _get_expected_qids(brief, win["source_pages"])
            missing_qids = _validate_code_qids(code, expected_qids)
            if missing_qids:
                logger.warning(f"{wid} round {round_num}: missing QIDs in code: {missing_qids}")

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
                # Feed error back to pass 1 conversation for next round
                pass1_conversation.append({"role": "assistant", "content": pass1_content})
                pass1_conversation.append({"role": "user", "content": (
                    f"The script from pass 2 failed execution:\n```\n{exec_output}\n```\n"
                    f"Rewrite the layout script to avoid this issue. Code only."
                )})
                continue

            if not os.path.exists(synthetic_pdf):
                logger.warning(f"{wid} round {round_num}: no synthetic.pdf produced")
                pass1_conversation.append({"role": "assistant", "content": pass1_content})
                pass1_conversation.append({"role": "user", "content": (
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

            # Feed score back to pass 1 conversation for next round
            pass1_conversation.append({"role": "assistant", "content": pass1_content})
            feedback_parts = [
                f"Score: {score['overall']:.3f} (need >= 0.7)",
                f"Delta: {score['delta_report']}",
            ]
            try:
                synth_doc = pdf_oxide.PdfDocument(synthetic_pdf)
                synth_text = "".join(
                    synth_doc.extract_text(p) for p in range(synth_doc.page_count())
                )
                found = {q for q, _ in find_all_qids(synth_text)}
                expected = _get_expected_qids(brief, win["source_pages"])
                missing = expected - found
                if missing:
                    feedback_parts.append(f"Missing QIDs in final PDF: {sorted(missing)}")
                feedback_parts.append(f"Extracted text (first 300 chars): {synth_text[:300]}")
            except Exception:
                pass
            feedback_parts.append(
                "The layout needs improvement. Rewrite the layout script. Code only."
            )
            pass1_conversation.append({"role": "user", "content": "\n".join(feedback_parts)})

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
