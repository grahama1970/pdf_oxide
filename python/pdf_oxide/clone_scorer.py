"""Structural comparison scorer for PDF cloning.

Compares an original PDF page against a synthetic ReportLab clone using
pdf_oxide extraction on both, then computes structural similarity metrics.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from difflib import SequenceMatcher
from typing import Any

import numpy as np
from PIL import Image

import pdf_oxide


def visual_similarity(original_pdf: str, synthetic_pdf: str, dpi: int = 150) -> float:
    """Compare two PDFs visually by rendering to PNG and computing SSIM.

    Uses pdftoppm for rendering and a pure numpy SSIM implementation.
    Returns float 0.0-1.0.
    """
    tmp = tempfile.mkdtemp(prefix="clone_visual_")
    try:
        imgs = []
        for label, pdf_path in [("orig", original_pdf), ("synth", synthetic_pdf)]:
            if not os.path.exists(pdf_path):
                return 0.0
            prefix = os.path.join(tmp, label)
            subprocess.run(
                ["pdftoppm", "-png", "-f", "1", "-l", "1", "-r", str(dpi),
                 "-gray", pdf_path, prefix],
                capture_output=True, timeout=15,
            )
            # pdftoppm names output as prefix-1.png or prefix-01.png
            candidates = [f for f in os.listdir(tmp) if f.startswith(label) and f.endswith(".png")]
            if not candidates:
                return 0.0
            img = Image.open(os.path.join(tmp, sorted(candidates)[-1])).convert("L")
            imgs.append(img)

        # Resize to same dimensions
        w = min(imgs[0].width, imgs[1].width)
        h = min(imgs[0].height, imgs[1].height)
        a = np.array(imgs[0].resize((w, h)), dtype=np.float64)
        b = np.array(imgs[1].resize((w, h)), dtype=np.float64)

        # SSIM computation (simplified, full-image)
        c1 = (0.01 * 255) ** 2
        c2 = (0.03 * 255) ** 2
        mu_a, mu_b = a.mean(), b.mean()
        var_a, var_b = a.var(), b.var()
        cov_ab = ((a - mu_a) * (b - mu_b)).mean()
        ssim = ((2 * mu_a * mu_b + c1) * (2 * cov_ab + c2)) / (
            (mu_a**2 + mu_b**2 + c1) * (var_a + var_b + c2)
        )
        return float(max(0.0, min(1.0, ssim)))
    except Exception:
        return 0.0
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def score_clone(original_pdf: str, synthetic_pdf: str, visual: bool = False) -> dict[str, Any]:
    """Compare original PDF page against synthetic ReportLab clone.

    Uses pdf_oxide to extract blocks/tables/sections from both PDFs,
    then computes structural similarity metrics.

    Returns dict with:
      - text_similarity: float (0-1) — SequenceMatcher on concatenated block text
      - block_count_ratio: float — min(pred,truth)/max(pred,truth) block counts
      - table_match: float (0-1) — table presence + row/col count match
      - section_recall: float (0-1) — fraction of original sections found in synthetic
      - overall: float (0-1) — weighted average
      - delta_report: str — human-readable description of what's missing/wrong
      - pass: bool — overall >= 0.7
    """
    orig_data = _extract_page_data(original_pdf)
    synth_data = _extract_page_data(synthetic_pdf)

    deltas: list[str] = []

    # Text similarity
    orig_text = orig_data["text"]
    synth_text = synth_data["text"]
    if orig_text and synth_text:
        text_similarity = SequenceMatcher(None, orig_text, synth_text).ratio()
    elif not orig_text and not synth_text:
        text_similarity = 1.0
    else:
        text_similarity = 0.0
        if orig_text and not synth_text:
            deltas.append(f"Original has {len(orig_text)} chars of text, synthetic is empty.")
        elif synth_text and not orig_text:
            deltas.append(f"Synthetic has text but original is empty (unexpected).")

    # Block count ratio
    orig_blocks = orig_data["block_count"]
    synth_blocks = synth_data["block_count"]
    if max(orig_blocks, synth_blocks) > 0:
        block_count_ratio = min(orig_blocks, synth_blocks) / max(orig_blocks, synth_blocks)
    else:
        block_count_ratio = 1.0
    if orig_blocks > 0 and abs(orig_blocks - synth_blocks) > 2:
        deltas.append(
            f"Original has {orig_blocks} text blocks, synthetic has {synth_blocks}."
        )

    # Table match
    orig_tables = orig_data["tables"]
    synth_tables = synth_data["tables"]
    table_match = _score_tables(orig_tables, synth_tables, deltas)

    # Section recall
    orig_sections = set(orig_data["section_titles"])
    synth_sections = set(synth_data["section_titles"])
    if orig_sections:
        found = sum(1 for s in orig_sections if s in synth_sections)
        section_recall = found / len(orig_sections)
        missing = orig_sections - synth_sections
        if missing:
            deltas.append(
                f"Missing sections: {', '.join(sorted(missing)[:5])}"
            )
    else:
        section_recall = 1.0

    # Visual similarity (optional)
    vis_score = None
    if visual:
        vis_score = visual_similarity(original_pdf, synthetic_pdf)
        if vis_score < 0.5:
            deltas.append(f"Visual similarity is low ({vis_score:.2f}) — layout may differ significantly.")

    # Weighted overall score
    if visual and vis_score is not None:
        overall = (
            text_similarity * 0.30
            + block_count_ratio * 0.15
            + table_match * 0.25
            + section_recall * 0.10
            + vis_score * 0.20
        )
    else:
        overall = (
            text_similarity * 0.35
            + block_count_ratio * 0.20
            + table_match * 0.30
            + section_recall * 0.15
        )

    delta_report = " | ".join(deltas) if deltas else "No structural differences detected."

    result = {
        "text_similarity": round(text_similarity, 3),
        "block_count_ratio": round(block_count_ratio, 3),
        "table_match": round(table_match, 3),
        "section_recall": round(section_recall, 3),
        "overall": round(overall, 3),
        "delta_report": delta_report,
        "pass": overall >= 0.7,
    }
    if vis_score is not None:
        result["visual_similarity"] = round(vis_score, 3)
    return result


def _extract_page_data(pdf_path: str) -> dict[str, Any]:
    """Extract structural data from all pages of a PDF."""
    doc = pdf_oxide.PdfDocument(pdf_path)
    page_count = doc.page_count()

    all_text = ""
    block_count = 0
    tables: list[dict] = []
    section_titles: list[str] = []

    for pg in range(page_count):
        # Text
        try:
            text = doc.extract_text(pg)
            all_text += text + "\n"
        except Exception:
            pass

        # Blocks (spans grouped by position)
        try:
            spans = doc.extract_spans(pg)
            # Count distinct text lines as blocks (group by y-coordinate)
            if spans:
                ys: set[int] = set()
                for s in spans:
                    if s.bbox and s.text.strip():
                        ys.add(round(s.bbox[1]))
                block_count += len(ys)
        except Exception:
            pass

        # Tables
        try:
            page_tables = doc.extract_tables(pg)
            for t in page_tables:
                tables.append({
                    "rows": t.row_count,
                    "cols": t.col_count,
                    "cells": len(t.cells),
                })
        except Exception:
            pass

    # Section titles from text (look for numbered headings)
    for line in all_text.split("\n"):
        stripped = line.strip()
        if stripped and len(stripped) < 80:
            # Simple heuristic: lines that look like section headings
            words = stripped.split()
            if len(words) >= 2 and len(words) <= 10:
                first = words[0]
                if (first[0].isupper() and len(first) > 1) or first[0].isdigit():
                    section_titles.append(stripped.upper())

    return {
        "text": all_text.strip(),
        "block_count": block_count,
        "tables": tables,
        "section_titles": section_titles[:50],  # cap to avoid noise
    }


def _score_tables(
    orig_tables: list[dict],
    synth_tables: list[dict],
    deltas: list[str],
) -> float:
    """Score table structural match between original and synthetic."""
    if not orig_tables and not synth_tables:
        return 1.0

    if not orig_tables and synth_tables:
        deltas.append(f"Synthetic has {len(synth_tables)} tables but original has none.")
        return 0.5  # not terrible, extra tables aren't as bad

    if orig_tables and not synth_tables:
        total_rows = sum(t["rows"] for t in orig_tables)
        deltas.append(
            f"Original has {len(orig_tables)} tables ({total_rows} total rows), "
            f"synthetic has no tables."
        )
        return 0.0

    # Match tables by position (assume same order)
    scores: list[float] = []
    for i, orig in enumerate(orig_tables):
        if i < len(synth_tables):
            synth = synth_tables[i]
            row_match = min(orig["rows"], synth["rows"]) / max(orig["rows"], synth["rows"]) if max(orig["rows"], synth["rows"]) > 0 else 1.0
            col_match = 1.0 if orig["cols"] == synth["cols"] else 0.5
            scores.append(row_match * 0.7 + col_match * 0.3)
            if orig["rows"] != synth["rows"]:
                deltas.append(
                    f"Table {i+1}: original has {orig['rows']} rows, "
                    f"synthetic has {synth['rows']} rows."
                )
            if orig["cols"] != synth["cols"]:
                deltas.append(
                    f"Table {i+1}: original has {orig['cols']} cols, "
                    f"synthetic has {synth['cols']} cols."
                )
        else:
            deltas.append(f"Table {i+1}: missing in synthetic (had {orig['rows']}r x {orig['cols']}c).")
            scores.append(0.0)

    return sum(scores) / len(scores) if scores else 0.0
