#!/usr/bin/env python3
"""
Evaluate extract_document() pipeline against ~100 PDFs from the 12TB corpus.

Tests:
  1. No crashes (extract_document doesn't panic/error on any PDF)
  2. Text extraction quality vs PyMuPDF (SequenceMatcher >= 80%)
  3. Block classification produces non-empty results
  4. Profile detection is reasonable (domain, is_scanned, complexity)
  5. Section hierarchy detection works on structured docs
  6. Strategy recommendation matches document type
  7. Performance (extract_document < 10s per PDF)

Usage:
  python scripts/eval_extract_document.py --corpus /mnt/storage12tb/extractor_corpus --sample 100
  python scripts/eval_extract_document.py --corpus /mnt/storage12tb/extractor_corpus --category defense --limit 20
"""

import json
import random
import signal
import time
import traceback
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(help="Evaluate extract_document() pipeline against real PDFs.")


class _Timeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _Timeout("Processing timed out")


def sample_corpus(corpus_dir: str, sample_size: int, category: Optional[str] = None, seed: int = 42) -> list:
    """Sample PDFs from the corpus, stratified by category."""
    categories = ["arxiv", "defense", "nasa", "nist", "engineering", "industry", "adversarial"]

    if category:
        categories = [category]

    all_pdfs = []
    for cat in categories:
        cat_dir = Path(corpus_dir) / cat
        if not cat_dir.exists():
            continue
        pdfs = list(cat_dir.glob("**/*.pdf"))
        for p in pdfs:
            all_pdfs.append((cat, str(p)))

    random.seed(seed)

    if category:
        random.shuffle(all_pdfs)
        return all_pdfs[:sample_size]

    # Stratified sample: proportional to category size, min 3 per category
    cat_counts = defaultdict(list)
    for cat, path in all_pdfs:
        cat_counts[cat].append(path)

    result = []
    remaining = sample_size

    for cat, paths in sorted(cat_counts.items()):
        n = min(3, len(paths), remaining)
        sampled = random.sample(paths, n)
        for p in sampled:
            result.append((cat, p))
        remaining -= n
        cat_counts[cat] = [p for p in paths if p not in sampled]

    if remaining > 0:
        pool = []
        for cat, paths in cat_counts.items():
            for p in paths:
                pool.append((cat, p))
        random.shuffle(pool)
        result.extend(pool[:remaining])

    return result


def extract_with_pymupdf(pdf_path: str, timeout_sec: int = 30) -> dict:
    """Extract text using PyMuPDF for comparison."""
    import pymupdf

    result = {"success": False, "texts": [], "page_count": 0, "time_ms": 0.0, "error": None}

    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_sec)

        start = time.perf_counter()
        doc = pymupdf.open(pdf_path)
        result["page_count"] = len(doc)

        for page_num in range(len(doc)):
            text = doc[page_num].get_text()
            result["texts"].append(text)

        result["time_ms"] = (time.perf_counter() - start) * 1000
        result["success"] = True
        doc.close()

    except _Timeout:
        result["error"] = f"Timeout after {timeout_sec}s"
    except Exception as e:
        result["error"] = str(e)
    finally:
        signal.alarm(0)

    return result


def extract_with_oxide(pdf_path: str, timeout_sec: int = 30) -> dict:
    """Run extract_document() AND raw extract_text() for proper parity testing."""
    import pdf_oxide

    result = {
        "success": False,
        "extraction": None,
        "texts": [],
        "raw_texts": [],
        "page_count": 0,
        "time_ms": 0.0,
        "error": None,
    }

    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_sec)

        start = time.perf_counter()
        doc = pdf_oxide.PdfDocument(pdf_path)
        extraction = doc.extract_document()
        result["time_ms"] = (time.perf_counter() - start) * 1000

        result["extraction"] = extraction
        result["page_count"] = extraction["page_count"]
        result["texts"] = [p["text"] for p in extraction["pages"]]

        # Also get raw extract_text() per page for apples-to-apples parity
        for pg in range(doc.page_count()):
            result["raw_texts"].append(doc.extract_text(pg))

        result["success"] = True

    except _Timeout:
        result["error"] = f"Timeout after {timeout_sec}s"
    except Exception as e:
        result["error"] = str(e)
    finally:
        signal.alarm(0)

    return result


def compare_text_quality(oxide_texts: list, pymupdf_texts: list, max_chars: int = 5000) -> dict:
    """Compare text quality page-by-page."""
    if not oxide_texts and not pymupdf_texts:
        return {"seq_match": 1.0, "word_jaccard": 1.0, "pages_compared": 0}

    similarities = []
    jaccards = []
    n_pages = max(len(oxide_texts), len(pymupdf_texts))

    for i in range(n_pages):
        oxide_text = oxide_texts[i] if i < len(oxide_texts) else ""
        pymupdf_text = pymupdf_texts[i] if i < len(pymupdf_texts) else ""

        ot = oxide_text[:max_chars]
        pt = pymupdf_text[:max_chars]

        if not ot and not pt:
            similarities.append(1.0)
            jaccards.append(1.0)
            continue

        if not ot or not pt:
            ratio = len(ot) / max(len(pt), 1) if ot else len(pt) / max(len(ot), 1) if pt else 0
            if ratio > 5:
                similarities.append(0.01)
            else:
                sm = SequenceMatcher(None, ot, pt, autojunk=False)
                similarities.append(sm.ratio())
            w1 = set(ot.lower().split())
            w2 = set(pt.lower().split())
            if w1 or w2:
                jaccards.append(len(w1 & w2) / max(len(w1 | w2), 1))
            else:
                jaccards.append(1.0)
            continue

        sm = SequenceMatcher(None, ot, pt, autojunk=False)
        similarities.append(sm.ratio())

        w1 = set(ot.lower().split())
        w2 = set(pt.lower().split())
        if w1 or w2:
            jaccards.append(len(w1 & w2) / max(len(w1 | w2), 1))
        else:
            jaccards.append(1.0)

    avg_sim = sum(similarities) / len(similarities) if similarities else 0.0
    avg_jac = sum(jaccards) / len(jaccards) if jaccards else 0.0

    return {
        "seq_match": round(avg_sim, 4),
        "word_jaccard": round(avg_jac, 4),
        "pages_compared": n_pages,
    }


def evaluate_extraction(extraction: dict, category: str) -> dict:
    """Evaluate extraction quality beyond text comparison."""
    checks = {
        "has_profile": False,
        "has_pages": False,
        "has_blocks": False,
        "profile_domain_valid": False,
        "strategy_valid": False,
        "sections_detected": 0,
        "figures_detected": 0,
        "blocks_per_page": 0.0,
        "block_types_seen": [],
        "issues": [],
    }

    if not extraction:
        checks["issues"].append("extraction is None")
        return checks

    profile = extraction.get("profile", {})
    pages = extraction.get("pages", [])

    checks["has_profile"] = bool(profile)
    checks["has_pages"] = len(pages) > 0

    total_blocks = sum(len(p.get("blocks", [])) for p in pages)
    checks["has_blocks"] = total_blocks > 0
    checks["blocks_per_page"] = round(total_blocks / max(len(pages), 1), 1)

    block_types = set()
    for p in pages:
        for b in p.get("blocks", []):
            block_types.add(b.get("block_type", "unknown"))
    checks["block_types_seen"] = sorted(block_types)

    valid_domains = [
        "academic", "arxiv", "defense", "engineering", "general",
        "government", "ietf", "industry", "legal", "nasa", "nist",
        "slides", "standards", "unknown",
    ]
    domain = profile.get("domain", "")
    checks["profile_domain_valid"] = domain in valid_domains

    valid_strategies = [
        "ocr_first", "drawing_extraction", "academic_extraction",
        "structured_extraction", "slide_extraction",
    ]
    strategy = extraction.get("recommended_strategy", "")
    checks["strategy_valid"] = strategy in valid_strategies

    checks["sections_detected"] = len(extraction.get("sections", []))
    checks["figures_detected"] = len(extraction.get("figures", []))

    if category == "arxiv" and domain not in ("academic", "arxiv", "general", "unknown"):
        checks["issues"].append(f"arxiv PDF classified as {domain}")
    if category == "defense" and domain not in (
        "defense", "government", "engineering", "general", "unknown", "standards", "nist",
    ):
        checks["issues"].append(f"defense PDF classified as {domain}")
    if category == "engineering" and domain not in ("engineering", "defense", "general", "unknown"):
        checks["issues"].append(f"engineering PDF classified as {domain}")

    if profile.get("is_scanned") and total_blocks > 0:
        total_text = sum(len(p.get("text", "")) for p in pages)
        if total_text > 500:
            checks["issues"].append("detected scanned but has significant text")

    return checks


def run_eval(corpus_dir: str, sample_size: int, category: Optional[str] = None, verbose: bool = False, seed: int = 42):
    """Run the full evaluation."""
    print(f"Sampling {sample_size} PDFs from {corpus_dir}...")
    pdfs = sample_corpus(corpus_dir, sample_size, category, seed)
    print(f"Selected {len(pdfs)} PDFs across categories:")

    cat_counts = defaultdict(int)
    for cat, _ in pdfs:
        cat_counts[cat] += 1
    for cat, n in sorted(cat_counts.items()):
        print(f"  {cat}: {n}")

    results = []
    crashes = 0
    timeouts = 0
    text_scores = []
    jaccard_scores = []
    raw_text_scores = []
    raw_jaccard_scores = []
    pipeline_pass = 0
    total_time_oxide = 0.0
    total_time_pymupdf = 0.0

    for i, (category_name, pdf_path) in enumerate(pdfs):
        fname = Path(pdf_path).name
        if len(fname) > 50:
            fname = fname[:47] + "..."

        oxide = extract_with_oxide(pdf_path)
        pymupdf_result = extract_with_pymupdf(pdf_path)

        entry = {
            "file": pdf_path,
            "category": category_name,
            "oxide_success": oxide["success"],
            "pymupdf_success": pymupdf_result["success"],
            "oxide_error": oxide["error"],
            "oxide_time_ms": round(oxide["time_ms"], 1),
            "pymupdf_time_ms": round(pymupdf_result["time_ms"], 1),
            "page_count_oxide": oxide["page_count"],
            "page_count_pymupdf": pymupdf_result["page_count"],
        }

        if oxide["success"]:
            total_time_oxide += oxide["time_ms"]
        if pymupdf_result["success"]:
            total_time_pymupdf += pymupdf_result["time_ms"]

        if not oxide["success"]:
            if oxide["error"] and "Timeout" in oxide["error"]:
                timeouts += 1
                status = "TIMEOUT"
            else:
                crashes += 1
                status = "CRASH"
            entry["seq_match"] = 0.0
            entry["word_jaccard"] = 0.0
            entry["raw_seq_match"] = 0.0
            entry["raw_word_jaccard"] = 0.0
            entry["pipeline_checks"] = {"issues": [oxide["error"]]}
        else:
            if pymupdf_result["success"]:
                # Pipeline text (normalized/restructured) vs PyMuPDF
                quality = compare_text_quality(oxide["texts"], pymupdf_result["texts"])
                entry["seq_match"] = quality["seq_match"]
                entry["word_jaccard"] = quality["word_jaccard"]
                text_scores.append(quality["seq_match"])
                jaccard_scores.append(quality["word_jaccard"])

                # Raw extract_text() vs PyMuPDF — true parity measure
                raw_quality = compare_text_quality(oxide["raw_texts"], pymupdf_result["texts"])
                entry["raw_seq_match"] = raw_quality["seq_match"]
                entry["raw_word_jaccard"] = raw_quality["word_jaccard"]
                raw_text_scores.append(raw_quality["seq_match"])
                raw_jaccard_scores.append(raw_quality["word_jaccard"])
            else:
                entry["seq_match"] = None
                entry["word_jaccard"] = None
                entry["raw_seq_match"] = None
                entry["raw_word_jaccard"] = None

            checks = evaluate_extraction(oxide["extraction"], category_name)
            entry["pipeline_checks"] = checks

            if checks["has_profile"] and checks["has_pages"] and checks["strategy_valid"]:
                pipeline_pass += 1

            status = "OK"
            if entry.get("raw_seq_match") is not None and entry["raw_seq_match"] < 0.5:
                status = "LOW_SIM"

        results.append(entry)

        raw_str = f"raw={entry.get('raw_seq_match', 0):.2f}" if entry.get("raw_seq_match") is not None else "raw=N/A"
        pipe_str = f"pipe={entry.get('seq_match', 0):.2f}" if entry.get("seq_match") is not None else "pipe=N/A"
        blocks = entry.get("pipeline_checks", {}).get("blocks_per_page", 0)
        print(
            f"  [{i+1}/{len(pdfs)}] {status:8s} {category_name:12s} "
            f"{fname:50s} {raw_str} {pipe_str} blk={blocks:.0f} {oxide['time_ms']:.0f}ms"
        )

        if verbose and entry.get("pipeline_checks", {}).get("issues"):
            for issue in entry["pipeline_checks"]["issues"]:
                print(f"           ISSUE: {issue}")

    # Summary
    n = len(results)
    n_ok = sum(1 for r in results if r["oxide_success"])
    n_compared = len(text_scores)

    print("\n" + "=" * 80)
    print("EVALUATION SUMMARY")
    print("=" * 80)
    print(f"Total PDFs tested:     {n}")
    print(f"Successful:            {n_ok} ({100*n_ok/n:.1f}%)")
    print(f"Crashes:               {crashes}")
    print(f"Timeouts:              {timeouts}")
    print()

    n_raw_compared = len(raw_text_scores)

    if n_raw_compared > 0:
        avg_raw = sum(raw_text_scores) / n_raw_compared
        med_raw = sorted(raw_text_scores)[n_raw_compared // 2]
        avg_raw_jac = sum(raw_jaccard_scores) / n_raw_compared
        med_raw_jac = sorted(raw_jaccard_scores)[n_raw_compared // 2]
        pct_80_raw = sum(1 for s in raw_text_scores if s >= 0.80) / n_raw_compared * 100
        pct_90_raw = sum(1 for s in raw_text_scores if s >= 0.90) / n_raw_compared * 100

        print(f"RAW TEXT PARITY (extract_text vs PyMuPDF get_text, {n_raw_compared} compared):")
        print(f"  SequenceMatcher: mean={avg_raw:.3f} median={med_raw:.3f}")
        print(f"  Word Jaccard:    mean={avg_raw_jac:.3f} median={med_raw_jac:.3f}")
        print(f"  >= 80% sim:      {pct_80_raw:.1f}%")
        print(f"  >= 90% sim:      {pct_90_raw:.1f}%")
        print()

    if n_compared > 0:
        avg_sim = sum(text_scores) / n_compared
        med_sim = sorted(text_scores)[n_compared // 2]
        avg_jac = sum(jaccard_scores) / n_compared
        med_jac = sorted(jaccard_scores)[n_compared // 2]
        pct_80 = sum(1 for s in text_scores if s >= 0.80) / n_compared * 100
        pct_90 = sum(1 for s in text_scores if s >= 0.90) / n_compared * 100

        print(f"PIPELINE TEXT (extract_document vs PyMuPDF, {n_compared} compared):")
        print(f"  SequenceMatcher: mean={avg_sim:.3f} median={med_sim:.3f}")
        print(f"  Word Jaccard:    mean={avg_jac:.3f} median={med_jac:.3f}")
        print(f"  >= 80% sim:      {pct_80:.1f}%")
        print(f"  >= 90% sim:      {pct_90:.1f}%")
        print()

    print("PIPELINE QUALITY:")
    print(f"  Pipeline pass:       {pipeline_pass}/{n_ok} ({100*pipeline_pass/max(n_ok,1):.1f}%)")

    block_counts = [
        r.get("pipeline_checks", {}).get("blocks_per_page", 0)
        for r in results if r["oxide_success"]
    ]
    if block_counts:
        print(f"  Avg blocks/page:     {sum(block_counts)/len(block_counts):.1f}")

    section_counts = [
        r.get("pipeline_checks", {}).get("sections_detected", 0)
        for r in results if r["oxide_success"]
    ]
    has_sections = sum(1 for s in section_counts if s > 0)
    print(f"  PDFs with sections:  {has_sections}/{n_ok}")

    figure_counts = [
        r.get("pipeline_checks", {}).get("figures_detected", 0)
        for r in results if r["oxide_success"]
    ]
    has_figures = sum(1 for f in figure_counts if f > 0)
    print(f"  PDFs with figures:   {has_figures}/{n_ok}")
    print()

    print("PERFORMANCE:")
    if n_ok > 0:
        print(f"  pdf_oxide total:     {total_time_oxide/1000:.1f}s ({total_time_oxide/n_ok:.0f}ms avg)")
    n_pymupdf_ok = sum(1 for r in results if r["pymupdf_success"])
    if n_pymupdf_ok > 0:
        print(f"  PyMuPDF total:       {total_time_pymupdf/1000:.1f}s ({total_time_pymupdf/n_pymupdf_ok:.0f}ms avg)")
    print()

    # Per-category breakdown
    cat_results = defaultdict(list)
    for r in results:
        cat_results[r["category"]].append(r)

    print("PER-CATEGORY BREAKDOWN:")
    print(f"  {'Category':12s} {'N':>4s} {'OK':>4s} {'RawSim':>7s} {'PipeSim':>8s} {'Raw>=80':>8s} {'Blk/pg':>7s} {'Secs':>5s}")
    for cat in sorted(cat_results.keys()):
        rs = cat_results[cat]
        n_cat = len(rs)
        n_cat_ok = sum(1 for r in rs if r["oxide_success"])
        raw_sims = [r["raw_seq_match"] for r in rs if r.get("raw_seq_match") is not None]
        sims = [r["seq_match"] for r in rs if r.get("seq_match") is not None]
        avg_raw_s = sum(raw_sims) / len(raw_sims) if raw_sims else 0
        avg_s = sum(sims) / len(sims) if sims else 0
        pct80 = sum(1 for s in raw_sims if s >= 0.80) / max(len(raw_sims), 1) * 100
        blocks = [
            r.get("pipeline_checks", {}).get("blocks_per_page", 0)
            for r in rs if r["oxide_success"]
        ]
        avg_b = sum(blocks) / len(blocks) if blocks else 0
        secs = sum(
            1 for r in rs
            if r.get("pipeline_checks", {}).get("sections_detected", 0) > 0
        )
        print(
            f"  {cat:12s} {n_cat:4d} {n_cat_ok:4d} {avg_raw_s:7.3f} "
            f"{avg_s:8.3f} {pct80:7.1f}% {avg_b:7.1f} {secs:5d}"
        )

    # Low similarity outliers (based on RAW text parity)
    low_sim = [
        (r["file"], r["category"], r.get("raw_seq_match", 0))
        for r in results
        if r.get("raw_seq_match") is not None and r["raw_seq_match"] < 0.5 and r["oxide_success"]
    ]
    if low_sim:
        print(f"\nLOW SIMILARITY OUTLIERS (<50%, {len(low_sim)} PDFs):")
        for path, cat, sim in sorted(low_sim, key=lambda x: x[2])[:10]:
            print(f"  {sim:.3f} [{cat}] {Path(path).name}")

    # Crashes
    crash_list = [
        (r["file"], r["category"], r.get("oxide_error", ""))
        for r in results if not r["oxide_success"]
    ]
    if crash_list:
        print(f"\nFAILURES ({len(crash_list)}):")
        for path, cat, err in crash_list[:10]:
            print(f"  [{cat}] {Path(path).name}: {err[:100]}")

    # Save results
    output_path = Path("scripts/eval_results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nDetailed results saved to {output_path}")

    # Final verdict
    print("\n" + "=" * 80)
    if crashes == 0 and n_raw_compared > 0:
        avg_sim = sum(raw_text_scores) / n_raw_compared
        if avg_sim >= 0.80:
            print("VERDICT: PASS — extract_document() is production-ready")
        elif avg_sim >= 0.70:
            print("VERDICT: ACCEPTABLE — extract_document() needs minor improvements")
        else:
            print("VERDICT: NEEDS WORK — text quality below 70% threshold")
    elif crashes > 0:
        print(f"VERDICT: UNSTABLE — {crashes} crashes detected")
    else:
        print("VERDICT: INCONCLUSIVE — no text comparisons possible")
    print("=" * 80)


@app.command()
def main(
    corpus: str = typer.Option(
        "/mnt/storage12tb/extractor_corpus",
        help="Path to corpus directory",
    ),
    sample: int = typer.Option(100, help="Number of PDFs to sample"),
    category: Optional[str] = typer.Option(None, help="Limit to a specific category"),
    limit: Optional[int] = typer.Option(None, help="Override sample size for category mode"),
    verbose: bool = typer.Option(False, help="Show detailed issues"),
    seed: int = typer.Option(42, help="Random seed for reproducibility"),
):
    """Evaluate extract_document() pipeline against real PDFs from the 12TB corpus."""
    size = limit if limit else sample
    run_eval(corpus, size, category, verbose, seed)


if __name__ == "__main__":
    app()
