#!/usr/bin/env python3
"""
Built-in benchmark: pdf_oxide vs PyMuPDF on real-world PDFs.

Focus: finding bugs in pdf_oxide by comparing against PyMuPDF reference output.

Measures per-PDF:
  - Open success/failure (crash detection)
  - Page count agreement
  - Text extraction time (both engines)
  - Text content similarity (difflib SequenceMatcher)
  - Character count delta
  - Missing text detection (text in PyMuPDF but absent from pdf_oxide)

Usage:
  python scripts/benchmark_vs_pymupdf.py --corpus /mnt/storage12tb/extractor_corpus --sample 50
  python scripts/benchmark_vs_pymupdf.py --pdf /path/to/single.pdf
  python scripts/benchmark_vs_pymupdf.py --corpus /mnt/storage12tb/extractor_corpus --category arxiv --limit 20
"""

import argparse
import json
import os
import random
import signal
import sys
import time
import traceback
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path


# Timeout handler for stuck PDFs
class TimeoutError(Exception):
    pass


def timeout_handler(signum, frame):
    raise TimeoutError("Processing timed out")


def extract_with_pymupdf(pdf_path: str, timeout_sec: int = 60) -> dict:
    """Extract text from all pages using PyMuPDF. Returns dict with results."""
    import pymupdf

    result = {
        "success": False,
        "page_count": 0,
        "texts": [],         # per-page text
        "full_text": "",
        "char_count": 0,
        "time_ms": 0.0,
        "error": None,
    }

    try:
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout_sec)

        start = time.perf_counter()
        doc = pymupdf.open(pdf_path)
        result["page_count"] = len(doc)

        texts = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text()
            texts.append(text)

        result["texts"] = texts
        result["full_text"] = "\n".join(texts)
        result["char_count"] = len(result["full_text"])
        elapsed = time.perf_counter() - start
        result["time_ms"] = elapsed * 1000
        result["success"] = True
        doc.close()

    except TimeoutError:
        result["error"] = f"Timeout after {timeout_sec}s"
    except BaseException as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler if 'old_handler' in dir() else signal.SIG_DFL)

    return result


def extract_with_pdf_oxide(pdf_path: str, timeout_sec: int = 60) -> dict:
    """Extract text from all pages using pdf_oxide. Returns dict with results."""
    import pdf_oxide

    result = {
        "success": False,
        "page_count": 0,
        "texts": [],
        "full_text": "",
        "char_count": 0,
        "time_ms": 0.0,
        "error": None,
    }

    try:
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(timeout_sec)

        start = time.perf_counter()
        doc = pdf_oxide.PdfDocument(pdf_path)
        result["page_count"] = doc.page_count()

        texts = []
        for page_num in range(result["page_count"]):
            text = doc.extract_text(page_num)
            texts.append(text)

        result["texts"] = texts
        result["full_text"] = "\n".join(texts)
        result["char_count"] = len(result["full_text"])
        elapsed = time.perf_counter() - start
        result["time_ms"] = elapsed * 1000
        result["success"] = True

    except TimeoutError:
        result["error"] = f"Timeout after {timeout_sec}s"
    except BaseException as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler if 'old_handler' in dir() else signal.SIG_DFL)

    return result


def compute_similarity(text_a: str, text_b: str) -> float:
    """Compute text similarity ratio (0.0 to 1.0) using SequenceMatcher."""
    if not text_a and not text_b:
        return 1.0
    if not text_a or not text_b:
        return 0.0
    # Quick check: if lengths differ by >5x, texts are clearly different (binary leakage)
    ratio = len(text_a) / max(len(text_b), 1)
    if ratio > 5.0 or ratio < 0.2:
        return 0.01
    # Limit to 5K chars — autojunk=False is O(n²), so keep it bounded
    a = text_a[:5000]
    b = text_b[:5000]
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def compute_page_avg_similarity(texts_a: list[str], texts_b: list[str]) -> float:
    """Compute average per-page similarity. More robust than whole-doc comparison.

    SequenceMatcher.ratio() has pathological O(n^2) behavior when texts are
    nearly identical but reordered (e.g. header/footer placement differences).
    Per-page comparison limits the blast radius of such pathological cases.
    """
    max_pages = max(len(texts_a), len(texts_b))
    if max_pages == 0:
        return 1.0

    total_sim = 0.0
    total_weight = 0.0

    for i in range(max_pages):
        a = texts_a[i] if i < len(texts_a) else ""
        b = texts_b[i] if i < len(texts_b) else ""

        # Weight by page text length (empty pages don't drag down score)
        weight = max(len(a), len(b))
        if weight == 0:
            continue

        sim = compute_similarity(a, b)
        total_sim += sim * weight
        total_weight += weight

    return total_sim / total_weight if total_weight > 0 else 1.0


def compute_word_jaccard(text_a: str, text_b: str) -> float:
    """Order-insensitive word overlap. Catches content differences without
    being affected by reading order variations."""
    words_a = set(text_a.split())
    words_b = set(text_b.split())
    union = words_a | words_b
    if not union:
        return 1.0
    return len(words_a & words_b) / len(union)


def find_missing_words(reference: str, candidate: str, min_word_len: int = 4) -> list[str]:
    """Find significant words in reference that are completely absent from candidate."""
    ref_words = set(w for w in reference.split() if len(w) >= min_word_len)
    cand_words = set(w for w in candidate.split() if len(w) >= min_word_len)
    missing = ref_words - cand_words
    # Filter out likely noise (numbers, short codes)
    return sorted(w for w in missing if any(c.isalpha() for c in w))[:50]


def compare_page_texts(pymupdf_texts: list[str], oxide_texts: list[str]) -> list[dict]:
    """Compare per-page text extraction."""
    results = []
    max_pages = max(len(pymupdf_texts), len(oxide_texts))

    for i in range(max_pages):
        page_result = {"page": i}

        pymu_text = pymupdf_texts[i] if i < len(pymupdf_texts) else ""
        oxide_text = oxide_texts[i] if i < len(oxide_texts) else ""

        page_result["pymupdf_chars"] = len(pymu_text)
        page_result["oxide_chars"] = len(oxide_text)
        page_result["char_delta"] = len(oxide_text) - len(pymu_text)

        if pymu_text or oxide_text:
            page_result["similarity"] = compute_similarity(pymu_text, oxide_text)
        else:
            page_result["similarity"] = 1.0

        # Flag pages with significant differences
        if page_result["similarity"] < 0.5 and len(pymu_text) > 50:
            page_result["flag"] = "LOW_SIMILARITY"
            page_result["missing_words"] = find_missing_words(pymu_text, oxide_text)[:20]
        elif len(pymu_text) > 100 and len(oxide_text) == 0:
            page_result["flag"] = "OXIDE_EMPTY"
        elif len(oxide_text) > 100 and len(pymu_text) == 0:
            page_result["flag"] = "PYMUPDF_EMPTY"

        results.append(page_result)

    return results


def sample_pdfs(corpus_dir: Path, n: int = 50, category: str = None, seed: int = 42) -> list[Path]:
    """Sample diverse PDFs from corpus."""
    random.seed(seed)

    categories = [
        "arxiv", "defense", "nasa", "nist", "engineering", "industry",
        "adversarial", "edge_cases", "archive_org", "ietf", "inbox",
    ]

    if category:
        categories = [category]

    by_cat = {}
    for cat in categories:
        cat_dir = corpus_dir / cat
        if cat_dir.is_dir():
            pdfs = sorted(cat_dir.glob("*.pdf"))
            if pdfs:
                by_cat[cat] = pdfs

    if not by_cat:
        print(f"No PDFs found in {corpus_dir}")
        sys.exit(1)

    samples = []

    # Ensure at least 1 per category
    for cat, pdfs in by_cat.items():
        samples.append(random.choice(pdfs))

    # Fill remaining proportionally
    remaining = n - len(samples)
    total_pdfs = sum(len(v) for v in by_cat.values())
    for cat, pdfs in by_cat.items():
        n_extra = max(0, int(remaining * len(pdfs) / total_pdfs))
        pool = [p for p in pdfs if p not in samples]
        samples.extend(random.sample(pool, min(n_extra, len(pool))))

    # Add some large PDFs for stress testing
    all_pdfs = []
    for pdfs in by_cat.values():
        all_pdfs.extend(pdfs)

    sized = []
    for p in random.sample(all_pdfs, min(200, len(all_pdfs))):
        try:
            sized.append((os.path.getsize(p), p))
        except OSError:
            pass
    sized.sort()

    # Add a few large PDFs
    for sz, p in sized[-5:]:
        if p not in samples and sz > 5_000_000:
            samples.append(p)
            if len(samples) >= n:
                break

    return sorted(samples, key=lambda p: (p.parent.name, p.name))


def run_benchmark(pdf_paths: list[Path], timeout_sec: int = 60) -> dict:
    """Run the full benchmark on the given PDF paths."""
    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_pdfs": len(pdf_paths),
        "timeout_sec": timeout_sec,
        "per_pdf": [],
        "summary": {},
    }

    # Counters
    both_pass = 0
    oxide_only_fail = 0
    pymupdf_only_fail = 0
    both_fail = 0
    page_count_mismatches = 0
    total_similarity = 0.0
    similarity_count = 0
    oxide_times = []
    pymupdf_times = []
    bugs = []  # Potential pdf_oxide bugs

    print(f"\n{'=' * 90}")
    print(f"  pdf_oxide vs PyMuPDF Benchmark — {len(pdf_paths)} PDFs")
    print(f"{'=' * 90}\n")

    for i, pdf_path in enumerate(pdf_paths, 1):
        file_size = os.path.getsize(pdf_path)
        file_size_str = f"{file_size / 1024:.0f}KB" if file_size < 1_000_000 else f"{file_size / 1_000_000:.1f}MB"
        cat = pdf_path.parent.name
        name = pdf_path.name

        # Skip extremely large files (>200MB) to avoid timeouts
        if file_size > 200_000_000:
            print(f"  [{i:3d}/{len(pdf_paths)}] SKIP  {cat:15s}/{name[:45]:45s} {file_size_str:>10s}  (too large)")
            continue

        pdf_result = {
            "path": str(pdf_path),
            "category": cat,
            "filename": name,
            "file_size": file_size,
        }

        # Extract with PyMuPDF
        pymu = extract_with_pymupdf(str(pdf_path), timeout_sec)
        pdf_result["pymupdf"] = {
            "success": pymu["success"],
            "page_count": pymu["page_count"],
            "char_count": pymu["char_count"],
            "time_ms": pymu["time_ms"],
            "error": pymu["error"],
        }

        # Extract with pdf_oxide
        oxide = extract_with_pdf_oxide(str(pdf_path), timeout_sec)
        pdf_result["oxide"] = {
            "success": oxide["success"],
            "page_count": oxide["page_count"],
            "char_count": oxide["char_count"],
            "time_ms": oxide["time_ms"],
            "error": oxide["error"],
        }

        # Compare results
        if pymu["success"] and oxide["success"]:
            both_pass += 1
            oxide_times.append(oxide["time_ms"])
            pymupdf_times.append(pymu["time_ms"])

            # Page count check
            if pymu["page_count"] != oxide["page_count"]:
                page_count_mismatches += 1
                pdf_result["page_count_mismatch"] = True
                bugs.append({
                    "type": "PAGE_COUNT_MISMATCH",
                    "file": str(pdf_path),
                    "pymupdf": pymu["page_count"],
                    "oxide": oxide["page_count"],
                })

            # Text similarity — per-page weighted average (robust against pathological cases)
            sim = compute_page_avg_similarity(pymu["texts"], oxide["texts"])
            word_j = compute_word_jaccard(pymu["full_text"], oxide["full_text"])
            pdf_result["text_similarity"] = sim
            pdf_result["word_jaccard"] = word_j
            total_similarity += sim
            similarity_count += 1

            # Per-page comparison
            page_comparisons = compare_page_texts(pymu["texts"], oxide["texts"])
            flagged_pages = [p for p in page_comparisons if "flag" in p]
            if flagged_pages:
                pdf_result["flagged_pages"] = flagged_pages

            # Character count delta
            char_delta = oxide["char_count"] - pymu["char_count"]
            pdf_result["char_delta"] = char_delta

            # Detect potential bugs
            if sim < 0.3 and pymu["char_count"] > 100:
                missing = find_missing_words(pymu["full_text"], oxide["full_text"])
                bugs.append({
                    "type": "VERY_LOW_SIMILARITY",
                    "file": str(pdf_path),
                    "similarity": sim,
                    "pymupdf_chars": pymu["char_count"],
                    "oxide_chars": oxide["char_count"],
                    "sample_missing_words": missing[:20],
                })
            elif sim < 0.7 and pymu["char_count"] > 100:
                bugs.append({
                    "type": "LOW_SIMILARITY",
                    "file": str(pdf_path),
                    "similarity": sim,
                    "pymupdf_chars": pymu["char_count"],
                    "oxide_chars": oxide["char_count"],
                })

            if pymu["char_count"] > 200 and oxide["char_count"] < 20:
                bugs.append({
                    "type": "OXIDE_EMPTY_TEXT",
                    "file": str(pdf_path),
                    "pymupdf_chars": pymu["char_count"],
                    "oxide_chars": oxide["char_count"],
                })

            # Speed comparison
            speedup = pymu["time_ms"] / oxide["time_ms"] if oxide["time_ms"] > 0 else float("inf")
            pdf_result["speedup"] = speedup

            # Print progress
            sim_bar = "#" * int(sim * 20) + "." * (20 - int(sim * 20))
            speed_str = f"{speedup:.1f}x" if speedup < 100 else ">100x"
            status = "OK" if sim > 0.8 else "LOW" if sim > 0.5 else "BAD"
            print(
                f"  [{i:3d}/{len(pdf_paths)}] {status:3s}  {cat:15s}/{name[:45]:45s} "
                f"{file_size_str:>10s}  sim={sim:.2f} [{sim_bar}]  "
                f"oxide={oxide['time_ms']:7.1f}ms  pymu={pymu['time_ms']:7.1f}ms  {speed_str}"
            )

        elif pymu["success"] and not oxide["success"]:
            oxide_only_fail += 1
            bugs.append({
                "type": "OXIDE_CRASH",
                "file": str(pdf_path),
                "error": oxide["error"],
                "pymupdf_works": True,
                "pymupdf_chars": pymu["char_count"],
            })
            print(
                f"  [{i:3d}/{len(pdf_paths)}] FAIL {cat:15s}/{name[:45]:45s} "
                f"{file_size_str:>10s}  oxide FAILED: {oxide['error'][:60]}"
            )

        elif not pymu["success"] and oxide["success"]:
            pymupdf_only_fail += 1
            print(
                f"  [{i:3d}/{len(pdf_paths)}] WIN  {cat:15s}/{name[:45]:45s} "
                f"{file_size_str:>10s}  oxide OK, pymu failed: {pymu['error'][:60]}"
            )

        else:
            both_fail += 1
            print(
                f"  [{i:3d}/{len(pdf_paths)}] BOTH {cat:15s}/{name[:45]:45s} "
                f"{file_size_str:>10s}  both failed"
            )

        results["per_pdf"].append(pdf_result)

    # Summary statistics
    avg_similarity = total_similarity / similarity_count if similarity_count else 0
    avg_oxide_ms = sum(oxide_times) / len(oxide_times) if oxide_times else 0
    avg_pymupdf_ms = sum(pymupdf_times) / len(pymupdf_times) if pymupdf_times else 0

    oxide_times_sorted = sorted(oxide_times)
    pymupdf_times_sorted = sorted(pymupdf_times)

    def percentile(data, pct):
        if not data:
            return 0
        idx = int(len(data) * pct / 100)
        return data[min(idx, len(data) - 1)]

    results["summary"] = {
        "both_pass": both_pass,
        "oxide_only_fail": oxide_only_fail,
        "pymupdf_only_fail": pymupdf_only_fail,
        "both_fail": both_fail,
        "page_count_mismatches": page_count_mismatches,
        "avg_text_similarity": avg_similarity,
        "oxide_avg_ms": avg_oxide_ms,
        "pymupdf_avg_ms": avg_pymupdf_ms,
        "oxide_p50_ms": percentile(oxide_times_sorted, 50),
        "oxide_p95_ms": percentile(oxide_times_sorted, 95),
        "pymupdf_p50_ms": percentile(pymupdf_times_sorted, 50),
        "pymupdf_p95_ms": percentile(pymupdf_times_sorted, 95),
        "avg_speedup": avg_pymupdf_ms / avg_oxide_ms if avg_oxide_ms > 0 else 0,
        "bug_count": len(bugs),
    }
    results["bugs"] = bugs

    # Print summary
    print(f"\n{'=' * 90}")
    print("  RESULTS SUMMARY")
    print(f"{'=' * 90}\n")

    total_tested = both_pass + oxide_only_fail + pymupdf_only_fail + both_fail
    print(f"  Pass rates:")
    print(f"    Both pass:         {both_pass:4d} / {total_tested} ({both_pass/total_tested*100:.1f}%)" if total_tested else "")
    print(f"    pdf_oxide fails:   {oxide_only_fail:4d} / {total_tested} ({oxide_only_fail/total_tested*100:.1f}%)" if total_tested else "")
    print(f"    PyMuPDF fails:     {pymupdf_only_fail:4d} / {total_tested}" if total_tested else "")
    print(f"    Both fail:         {both_fail:4d} / {total_tested}" if total_tested else "")
    print()

    # Compute average word Jaccard
    word_jaccards = [pr.get("word_jaccard", 0) for pr in results["per_pdf"] if "word_jaccard" in pr]
    avg_word_jaccard = sum(word_jaccards) / len(word_jaccards) if word_jaccards else 0

    print(f"  Text quality:")
    print(f"    Avg similarity (per-page): {avg_similarity:.4f} ({avg_similarity*100:.1f}%)")
    print(f"    Avg word Jaccard:          {avg_word_jaccard:.4f} ({avg_word_jaccard*100:.1f}%)")
    print(f"    Page count mismatches: {page_count_mismatches}")
    print()

    print(f"  Performance:")
    print(f"    pdf_oxide avg:     {avg_oxide_ms:.1f}ms  (p50={percentile(oxide_times_sorted, 50):.1f}ms, p95={percentile(oxide_times_sorted, 95):.1f}ms)")
    print(f"    PyMuPDF avg:       {avg_pymupdf_ms:.1f}ms  (p50={percentile(pymupdf_times_sorted, 50):.1f}ms, p95={percentile(pymupdf_times_sorted, 95):.1f}ms)")
    if avg_oxide_ms > 0:
        print(f"    Speedup:           {avg_pymupdf_ms/avg_oxide_ms:.1f}x faster")
    print()

    if bugs:
        print(f"  POTENTIAL BUGS ({len(bugs)}):")
        print(f"  {'=' * 86}")

        # Group by type
        by_type = defaultdict(list)
        for bug in bugs:
            by_type[bug["type"]].append(bug)

        for bug_type, items in sorted(by_type.items()):
            print(f"\n    {bug_type} ({len(items)}):")
            for item in items[:5]:
                fname = Path(item["file"]).name
                if bug_type == "OXIDE_CRASH":
                    print(f"      - {fname}: {item['error'][:80]}")
                elif bug_type in ("VERY_LOW_SIMILARITY", "LOW_SIMILARITY"):
                    print(f"      - {fname}: sim={item['similarity']:.2f} pymu={item['pymupdf_chars']}c oxide={item['oxide_chars']}c")
                    if "sample_missing_words" in item and item["sample_missing_words"]:
                        print(f"        missing words: {', '.join(item['sample_missing_words'][:10])}")
                elif bug_type == "OXIDE_EMPTY_TEXT":
                    print(f"      - {fname}: pymu={item['pymupdf_chars']}c, oxide={item['oxide_chars']}c")
                elif bug_type == "PAGE_COUNT_MISMATCH":
                    print(f"      - {fname}: pymu={item['pymupdf']}p, oxide={item['oxide']}p")
            if len(items) > 5:
                print(f"      ... and {len(items) - 5} more")
    else:
        print("  No bugs detected!")

    print(f"\n{'=' * 90}\n")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark pdf_oxide vs PyMuPDF — find bugs by comparing extraction output"
    )
    parser.add_argument(
        "--corpus", type=Path,
        default=Path("/mnt/storage12tb/extractor_corpus"),
        help="Corpus directory (default: /mnt/storage12tb/extractor_corpus)",
    )
    parser.add_argument("--pdf", type=Path, help="Benchmark a single PDF")
    parser.add_argument("--sample", type=int, default=50, help="Number of PDFs to sample (default: 50)")
    parser.add_argument("--category", type=str, help="Limit to a specific category")
    parser.add_argument("--limit", type=int, help="Max PDFs (after category filter)")
    parser.add_argument("--timeout", type=int, default=60, help="Per-PDF timeout in seconds (default: 60)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for sampling (default: 42)")
    parser.add_argument("--output", type=Path, help="Save JSON results to file")
    parser.add_argument("--all", action="store_true", help="Run on ALL PDFs in corpus (no sampling)")

    args = parser.parse_args()

    # Verify both libraries
    try:
        import pdf_oxide
        print(f"  pdf_oxide {pdf_oxide.__version__}")
    except ImportError:
        print("ERROR: pdf_oxide not installed. Run: maturin develop")
        sys.exit(1)

    try:
        import pymupdf
        print(f"  PyMuPDF {pymupdf.__version__}")
    except ImportError:
        print("ERROR: PyMuPDF not installed. Run: pip install pymupdf")
        sys.exit(1)

    # Collect PDFs
    if args.pdf:
        pdf_paths = [args.pdf]
    elif args.all:
        pdf_paths = sorted(args.corpus.rglob("*.pdf"))
        if args.category:
            pdf_paths = [p for p in pdf_paths if p.parent.name == args.category]
        if args.limit:
            pdf_paths = pdf_paths[:args.limit]
    else:
        pdf_paths = sample_pdfs(args.corpus, args.sample, args.category, args.seed)
        if args.limit:
            pdf_paths = pdf_paths[:args.limit]

    print(f"  Testing {len(pdf_paths)} PDFs\n")

    # Run benchmark
    results = run_benchmark(pdf_paths, args.timeout)

    # Save results
    if args.output:
        # Strip per-page text data to keep JSON manageable
        for pr in results["per_pdf"]:
            pr.pop("flagged_pages", None)
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"  Results saved to: {args.output}")

    # Exit with error code if bugs found
    bug_count = len(results.get("bugs", []))
    if bug_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
