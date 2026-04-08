"""PDF Cloner — additive features: error injection, figures, filler pages, stitching.

These features layer on top of the core clone loop:
- inject_errors: deterministic text corruption for extractor testing
- generate_figure / insert_figures: placeholder figures for figure_candidate pages
- generate_filler_page: /create-text content for unsampled pages
- stitch_pages: merge window PDFs + filler into full N-page documents
"""
from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

from loguru import logger
from pypdf import PdfReader, PdfWriter


# ── QID encoding ──────────────────────────────────────────────────────
# Invisible IDs embedded in PDF text so the manifest can target specific elements.
# Uses zero-width Unicode characters that survive the ReportLab → DejaVu → PDF
# → pdf_oxide extraction round trip.
#
# Format: U+200B (ZWS, start) + binary bits as U+200C (0) / U+200D (1) + U+2060 (end)
# Distinct start/end delimiters prevent ambiguity from stray ZWS in text.
# Max 20 bits (1M QIDs) — decoder rejects longer runs as noise.

_QID_START = "\u200B"  # zero-width space
_QID_END = "\u2060"    # word joiner (distinct from start to prevent ambiguity)
_BIT_0 = "\u200C"      # zero-width non-joiner
_BIT_1 = "\u200D"      # zero-width joiner
_MAX_QID_BITS = 20     # max 1,048,575 — rejects runaway bit sequences


def encode_qid(qid: int) -> str:
    """Encode an integer QID as an invisible zero-width character sequence."""
    if qid == 0:
        return f"{_QID_START}{_BIT_0}{_QID_END}"
    bits = []
    n = qid
    while n > 0:
        bits.append(_BIT_1 if (n & 1) else _BIT_0)
        n >>= 1
    if len(bits) > _MAX_QID_BITS:
        raise ValueError(f"QID {qid} exceeds {_MAX_QID_BITS}-bit limit")
    return _QID_START + "".join(reversed(bits)) + _QID_END


def decode_qid(text: str, pos: int) -> tuple[int, int] | None:
    """Decode a QID at position pos in text. Returns (qid, end_pos) or None."""
    if pos >= len(text) or text[pos] != _QID_START:
        return None
    result = 0
    i = pos + 1
    bit_count = 0
    while i < len(text) and text[i] in (_BIT_0, _BIT_1):
        bit_count += 1
        if bit_count > _MAX_QID_BITS:
            return None  # reject runaway sequences
        result = (result << 1) | (1 if text[i] == _BIT_1 else 0)
        i += 1
    if i >= len(text) or text[i] != _QID_END or i == pos + 1:
        return None
    return result, i + 1


def find_all_qids(text: str) -> list[tuple[int, int]]:
    """Find all QIDs in extracted text. Returns list of (qid, char_position)."""
    results = []
    i = 0
    while i < len(text):
        if text[i] == _QID_START:
            decoded = decode_qid(text, i)
            if decoded:
                qid, end = decoded
                results.append((qid, i))
                i = end
                continue
        i += 1
    return results


def inject_qids(code: str, qid_map: list[dict]) -> str:
    """Inject invisible QID markers into ReportLab code text strings.

    qid_map: list of {"qid": int, "label": str} — each label is a substring
    to find in the code's text content. The QID is inserted right before
    the first occurrence of that label.
    """
    for entry in qid_map:
        qid_str = encode_qid(entry["qid"])
        label = entry["label"]
        # Find the label inside a string literal and prepend the QID
        if label in code:
            code = code.replace(label, qid_str + label, 1)
    return code


# QID range convention:
#   page_num * 10000 + 0         = page-level QID (unused, reserved)
#   page_num * 10000 + 1..4999   = structural elements (tables, headings, headers, footers, clauses)
#   page_num * 10000 + 5000..9999 = corruptions

_QID_PAGE_MULTIPLIER = 10000
_STRUCTURAL_QID_OFFSET = 1
_CORRUPTION_QID_OFFSET = 5000


def build_structural_qid_map(clone_brief: dict, source_pages: list[int]) -> list[dict]:
    """Build QID map entries for structural elements described in clone_brief.

    Returns list of {"qid": int, "element_type": str, "label": str, "detail": dict}
    ready for inject_qids (uses "qid" and "label" fields).
    """
    entries: list[dict] = []
    page_num = source_pages[0] if source_pages else 0
    qid_base = page_num * _QID_PAGE_MULTIPLIER + _STRUCTURAL_QID_OFFSET
    idx = 0

    # Tables — look for first-cell text or column header text
    for table in clone_brief.get("tables", []):
        idx += 1
        entries.append({
            "qid": qid_base + idx,
            "element_type": "table",
            "label": None,  # no label yet — matched after code generation
            "detail": {"page": table.get("page"), "rows": table.get("rows"), "cols": table.get("cols")},
        })

    # TOC section heading
    toc_section = clone_brief.get("toc_section")
    if toc_section:
        idx += 1
        # Use the first 40 chars of section title as the label to find in code
        label = toc_section[:40].strip()
        entries.append({
            "qid": qid_base + idx,
            "element_type": "heading",
            "label": label,
            "detail": {"toc_section": toc_section, "toc_parent": clone_brief.get("toc_parent")},
        })

    # Running header
    rh = clone_brief.get("running_header")
    if rh:
        idx += 1
        header_text = rh["text"] if isinstance(rh, dict) else str(rh)
        if header_text.strip():
            entries.append({
                "qid": qid_base + idx,
                "element_type": "running_header",
                "label": header_text.strip()[:40],
                "detail": {"text": header_text},
            })

    # Running footer
    rf = clone_brief.get("running_footer")
    if rf:
        idx += 1
        footer_text = rf["text"] if isinstance(rf, dict) else str(rf)
        if footer_text.strip():
            entries.append({
                "qid": qid_base + idx,
                "element_type": "running_footer",
                "label": footer_text.strip()[:40],
                "detail": {"text": footer_text},
            })

    # Spanning table marker
    spanning = clone_brief.get("spanning_table")
    if spanning:
        idx += 1
        entries.append({
            "qid": qid_base + idx,
            "element_type": "spanning_table",
            "label": None,
            "detail": spanning,
        })

    return entries


def inject_structural_qids(code: str, structural_entries: list[dict]) -> tuple[str, list[dict]]:
    """Inject QIDs for structural elements into clean ReportLab code.

    For entries with a label, prepend the QID to the first occurrence.
    For entries without a label (tables), find table-like patterns in code
    and inject QIDs into the first cell text of each table.

    Returns (modified_code, entries_with_injection_status).
    """
    # First pass: inject labeled entries (headings, headers, footers)
    labeled = [e for e in structural_entries if e.get("label")]
    qid_map = [{"qid": e["qid"], "label": e["label"]} for e in labeled]
    code = inject_qids(code, qid_map)

    # Mark which labeled entries were actually found in code
    for entry in labeled:
        qid_str = encode_qid(entry["qid"])
        entry["injected"] = (qid_str + entry["label"]) in code

    # Second pass: inject table QIDs — match drawString, Paragraph, and Table cell text
    table_entries = [e for e in structural_entries if e["element_type"] in ("table", "spanning_table") and not e.get("label")]
    if table_entries:
        # Match text in drawString('text'), Paragraph('text'), and ['text'] table data
        _cell_pat = re.compile(
            r"""(?:drawString\s*\(\s*[0-9.]+\s*,\s*[0-9.]+\s*,\s*|Paragraph\s*\(\s*|[\[,]\s*)(['"])([^'"]{3,})\1"""
        )
        matches = list(_cell_pat.finditer(code))
        for ti, entry in enumerate(table_entries):
            if ti < len(matches):
                m = matches[ti]
                cell_text = m.group(2)
                entry["label"] = cell_text[:30]
                qid_str = encode_qid(entry["qid"])
                text_start = m.start(2)
                code = code[:text_start] + qid_str + code[text_start:]
                entry["injected"] = True
                # Re-find matches since offsets shifted
                matches = list(_cell_pat.finditer(code))
            else:
                entry["injected"] = False

    # Entries without labels that weren't matched
    for entry in structural_entries:
        if "injected" not in entry:
            entry["injected"] = False

    return code, structural_entries


# ── Deterministic error injection ──────────────────────────────────────
# Applied AFTER the LLM returns passing ReportLab code, BEFORE final PDF.

_ERROR_SUBS = {
    "ligature": [("ffi", "\ufb03"), ("ffl", "\ufb04"), ("fi", "\ufb01"), ("fl", "\ufb02"), ("ff", "\ufb00")],
    "hyphen": [("-", "\u2013"), ("-", "\u2014"), ("-", "\u2010")],
    "homoglyph": [("l", "\u2113"), ("O", "\u041e"), ("o", "\u043e"), ("a", "\u0430"),
                  ("e", "\u0435"), ("i", "\u0456"), ("c", "\u0441"), ("p", "\u0440")],
    "invisible": [("", "\u200b"), ("", "\u200c"), ("", "\u2060")],  # ZWS, ZWNJ, word joiner
    "quote": [("'", "\u2019"), ("'", "\u2018"), ('"', "\u201c"), ('"', "\u201d")],
    "space": [(" ", "\u00a0")],
}

# DejaVu Sans supports full Unicode — injected into errored code so corruptions
# (ligatures, homoglyphs, Cyrillic, invisible chars) survive ReportLab → PDF.
_DEJAVU_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_REGISTRATION = f"""
# --- inject_errors: register Unicode font for corruption glyphs ---
from reportlab.pdfbase import pdfmetrics as _pm
from reportlab.pdfbase.ttfonts import TTFont as _TTF
_pm.registerFont(_TTF('DejaVu', '{_DEJAVU_FONT_PATH}'))
_pm.registerFont(_TTF('DejaVu-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
# --- end font registration ---
"""


def inject_errors(
    code: str,
    seed: int = 42,
    error_rate: float = 0.05,
    track: bool = False,
) -> str | tuple[str, list[dict]]:
    """Deterministically corrupt text literals in ReportLab code.

    If track=True, returns (corrupted_code, manifest) where manifest is a list
    of dicts recording each corruption for ground truth testing.
    """
    rng = random.Random(seed)
    # Match string literals that look like natural language content:
    # - At least 10 chars
    # - Exclude strings with Python syntax or HTML tags: ( ) = { } [ ] < >
    _str_pat = re.compile(r"""(?<=['"])([^'"()\[\]{}=<>]{10,})(?=['"])""")
    corruption_id = 0
    manifest: list[dict] = []

    def _is_content_text(text: str) -> bool:
        """Return True if text looks like natural language, not code."""
        return " " in text and any(c.isalpha() for c in text)

    def _corrupt_text(text: str, match_start: int) -> str:
        nonlocal corruption_id
        words = text.split(" ")
        char_offset = 0
        for i, word in enumerate(words):
            if rng.random() > error_rate:
                char_offset += len(word) + 1
                continue
            # Find all applicable substitutions for this word
            applicable: list[tuple[str, str, str]] = []
            for cat, subs in _ERROR_SUBS.items():
                for old, new in subs:
                    if old == "":
                        # Invisible char insertion — only in words >= 2 chars
                        if len(word) >= 2:
                            applicable.append((cat, old, new))
                    elif old in word:
                        applicable.append((cat, old, new))
            if not applicable:
                char_offset += len(word) + 1
                continue
            cat, old, new = rng.choice(applicable)
            original_word = word
            if old == "":
                # Insert inside word (not at edges) to survive rendering
                pos = rng.randint(1, max(1, len(word) - 1))
                words[i] = word[:pos] + new + word[pos:]
            else:
                words[i] = word.replace(old, new, 1)
            if words[i] != original_word:
                corruption_id += 1
                manifest.append({
                    "id": f"ERR_{corruption_id:04d}",
                    "type": cat,
                    "original": original_word,
                    "corrupted": words[i],
                    "char_offset_in_string": char_offset,
                    "code_offset": match_start + char_offset,
                    "word_index": i,
                })
            char_offset += len(word) + 1
        return " ".join(words)

    def _replace_match(m: re.Match) -> str:
        if not _is_content_text(m.group(0)):
            return m.group(0)
        return _corrupt_text(m.group(0), m.start())

    result = _str_pat.sub(_replace_match, code)

    if manifest:
        # Inject DejaVu font registration so corruption glyphs survive in PDF.
        # Insert after the last 'from reportlab' or 'import reportlab' line.
        lines = result.split("\n")
        insert_idx = 0
        for idx, line in enumerate(lines):
            if "reportlab" in line and ("import" in line or "from" in line):
                insert_idx = idx + 1
        lines.insert(insert_idx, _FONT_REGISTRATION)
        result = "\n".join(lines)

        # Replace standard font names with DejaVu in style definitions
        for old_font in ("Helvetica-Bold", "Helvetica"):
            result = result.replace(f"'{old_font}'", "'DejaVu'")
            result = result.replace(f'"{old_font}"', '"DejaVu"')
        result = result.replace("'Times-Roman'", "'DejaVu'")
        result = result.replace('"Times-Roman"', '"DejaVu"')

    if track:
        return result, manifest
    return result


def generate_figure(
    content_type: str = "generic",
    domain: str = "defense",
    output_path: str = "/tmp/clone_figure.png",
    width: int = 400,
    height: int = 300,
    seed: int = 42,
) -> str:
    """Generate a placeholder figure image for figure_candidate pages."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    rng = random.Random(seed)

    if content_type in ("chart", "diagram"):
        try:
            import subprocess as _sp
            skill_dir = os.path.expanduser("~/.pi/skills/create-figure")
            prompts = {
                "chart": f"bar chart of compliance assessment scores for {domain} controls",
                "diagram": f"flowchart of {domain} system architecture",
            }
            _sp.run(
                ["uv", "run", "--script", "generate.py", prompts[content_type],
                 "--output", output_path, "--size", f"{width}x{height}"],
                cwd=skill_dir, capture_output=True, timeout=30,
            )
            if os.path.exists(output_path):
                return output_path
        except Exception:
            pass

    try:
        from PIL import Image, ImageDraw, ImageFont
        colors = {
            "defense": (180, 190, 200), "academic": (200, 200, 180),
            "engineering": (190, 200, 190), "medical": (200, 190, 190),
        }
        bg = colors.get(domain, (195, 195, 195))
        img = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(img)
        draw.rectangle([2, 2, width - 3, height - 3], outline=(120, 120, 120), width=2)
        label = f"Figure {rng.randint(1, 99)}"
        sublabel = {"chart": "Performance Data", "diagram": "System Overview",
                     "photo": "Reference Image", "generic": "Placeholder"}.get(content_type, "")
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
            font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
        except Exception:
            font = ImageFont.load_default()
            font_sm = font
        bbox_l = draw.textbbox((0, 0), label, font=font)
        lw = bbox_l[2] - bbox_l[0]
        draw.text(((width - lw) // 2, height // 2 - 20), label, fill=(60, 60, 60), font=font)
        if sublabel:
            bbox_s = draw.textbbox((0, 0), sublabel, font=font_sm)
            sw = bbox_s[2] - bbox_s[0]
            draw.text(((width - sw) // 2, height // 2 + 10), sublabel, fill=(100, 100, 100), font=font_sm)
        img.save(output_path)
        return output_path
    except ImportError:
        Path(output_path).write_bytes(b"")
        return output_path


def insert_figures(code: str, figure_paths: list[str]) -> str:
    """Replace placeholder rectangles in ReportLab code with drawImage calls."""
    if not figure_paths:
        return code
    _rect_pat = re.compile(
        r"c\.rect\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*[^)]*fill\s*=\s*1[^)]*\)"
    )
    fig_idx = 0

    def _replace_rect(m: re.Match) -> str:
        nonlocal fig_idx
        if fig_idx >= len(figure_paths):
            return m.group(0)
        path = figure_paths[fig_idx]
        fig_idx += 1
        x, y, w, h = m.group(1), m.group(2), m.group(3), m.group(4)
        return f"c.drawImage('{path}', {x}, {y}, width={w}, height={h})"
    return _rect_pat.sub(_replace_rect, code)


def _get_page_content_type(page_num: int, profile: dict) -> str:
    """Determine content type for a page from profiler data."""
    sigs = profile.get("page_signatures", [])
    sig = next((s for s in sigs if s.get("page_num") == page_num), {})
    if sig.get("table_candidate"):
        return "table_cell"
    if sig.get("figure_candidate"):
        return "prose"
    if sig.get("equation_candidate"):
        return "latex_equation"
    req_pages = profile.get("requirements_pages", [])
    if page_num in req_pages:
        return "requirement"
    return "prose"


def generate_filler_page(
    page_num: int,
    profile: dict,
    output_path: str,
    seed: int = 42,
    corrupt: str | None = None,
) -> str:
    """Generate a single filler PDF page using ReportLab with /create-text content."""
    import subprocess as _sp

    domain = profile.get("domain", "government")
    content_type = _get_page_content_type(page_num, profile)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    skill_dir = os.path.expanduser("~/.pi/skills/create-text")
    cmd = [
        skill_dir + "/run.sh", "select",
        "-t", content_type, "-d", domain,
        "-n", "8", "-s", str(seed + page_num),
    ]
    if corrupt:
        cmd.extend(["--corrupt", corrupt])

    try:
        result = _sp.run(cmd, capture_output=True, text=True, timeout=15)
        lines = [
            l.strip() for l in result.stdout.split("\n")
            if l.strip() and not l.startswith("---")
        ]
    except Exception:
        lines = [f"Section {page_num + 1}", "Content placeholder for page {}.".format(page_num + 1)]

    if not lines:
        lines = [f"Section {page_num + 1}", "Content placeholder."]

    text_escaped = "\n".join(lines).replace("\\", "\\\\").replace("'", "\\'")
    running_headers = profile.get("running_headers", [])
    if running_headers:
        h = running_headers[0]
        header_text = h["text"] if isinstance(h, dict) else str(h)
    else:
        header_text = ""
    header_escaped = header_text.replace("\\", "\\\\").replace("'", "\\'")

    code = f"""
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

doc = SimpleDocTemplate('{output_path}', pagesize=letter,
    topMargin=0.75*inch, bottomMargin=0.75*inch,
    leftMargin=1*inch, rightMargin=1*inch)

styles = getSampleStyleSheet()
body = styles['BodyText']
body.fontSize = 10
body.leading = 14

heading = ParagraphStyle('Heading', parent=styles['Heading2'], fontSize=12, spaceAfter=12)
header_style = ParagraphStyle('Header', parent=styles['Normal'], fontSize=8, textColor='gray')

story = []
story.append(Paragraph('{header_escaped}', header_style))
story.append(Spacer(1, 6))

text = '''{text_escaped}'''
for para in text.split('\\n'):
    para = para.strip()
    if not para:
        continue
    if len(para) < 60 and para[0].isupper():
        story.append(Paragraph(para, heading))
    else:
        story.append(Paragraph(para, body))
    story.append(Spacer(1, 4))

doc.build(story)
"""
    code_path = output_path.replace(".pdf", "_filler.py")
    with open(code_path, "w", encoding="utf-8") as f:
        f.write(code)

    try:
        _sp.run(
            [".venv/bin/python", code_path],
            capture_output=True, text=True, timeout=15,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))),
        )
    except Exception:
        pass

    if os.path.exists(output_path):
        return output_path
    # Fallback: blank page
    from reportlab.lib.pagesizes import letter as _letter
    from reportlab.pdfgen import canvas as _canvas
    c = _canvas.Canvas(output_path, pagesize=_letter)
    c.showPage()
    c.save()
    return output_path


def stitch_pages(
    pdf_path: str,
    output_dir: str,
    clone_results: list[dict],
    profile: dict,
    seed: int = 42,
    corrupt: str | None = None,
    use_errored: bool = False,
) -> str:
    """Stitch cloned window PDFs + filler pages into a full N-page document."""
    total_pages = int(profile.get("page_count", 0) or 0)
    if total_pages <= 0:
        return ""

    stitched_path = os.path.join(output_dir, "stitched.pdf")
    filler_dir = os.path.join(output_dir, "filler")
    os.makedirs(filler_dir, exist_ok=True)

    page_to_pdf: dict[int, str] = {}
    for win in clone_results:
        if win.get("status") != "pass":
            continue
        pdf_key = "errored_pdf" if use_errored and "errored_pdf" in win else "synthetic_pdf"
        synth = win.get(pdf_key, "")
        if not synth or not os.path.exists(synth):
            continue
        for pg in win.get("source_pages", []):
            page_to_pdf[pg] = synth

    writer = PdfWriter()
    synth_reader_cache: dict[str, PdfReader] = {}

    for pg in range(total_pages):
        if pg in page_to_pdf:
            synth_path = page_to_pdf[pg]
            if synth_path not in synth_reader_cache:
                synth_reader_cache[synth_path] = PdfReader(synth_path)
            reader = synth_reader_cache[synth_path]
            win = next((w for w in clone_results
                       if pg in w.get("source_pages", []) and w.get("status") == "pass"), None)
            if win:
                src_pages = win["source_pages"]
                page_idx = src_pages.index(pg) if pg in src_pages else 0
                if page_idx < len(reader.pages):
                    writer.add_page(reader.pages[page_idx])
                    continue
            if reader.pages:
                writer.add_page(reader.pages[0])
        else:
            filler_path = os.path.join(filler_dir, f"page_{pg:04d}.pdf")
            generate_filler_page(pg, profile, filler_path, seed=seed, corrupt=corrupt)
            if os.path.exists(filler_path):
                filler_reader = PdfReader(filler_path)
                if filler_reader.pages:
                    writer.add_page(filler_reader.pages[0])
                else:
                    writer.add_blank_page(width=612, height=792)
            else:
                writer.add_blank_page(width=612, height=792)

    with open(stitched_path, "wb") as f:
        writer.write(f)

    logger.info(
        f"Stitched {total_pages} pages: "
        f"{len(page_to_pdf)} synthetic, "
        f"{total_pages - len(page_to_pdf)} filler → {stitched_path}"
    )
    return stitched_path
