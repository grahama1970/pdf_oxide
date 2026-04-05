#!/usr/bin/env python3
"""
Phase V end-to-end validation: compare pdf_oxide pipeline output against PyMuPDF
on the same PDFs from the 12TB corpus.

Compares:
  1. Raw text similarity (SequenceMatcher)
  2. Block count comparison
  3. Section count comparison
  4. Profile domain match
  5. Timing (ms) for both extractors

Usage:
  python scripts/validate_phase_v.py run
  python scripts/validate_phase_v.py run --quick
  python scripts/validate_phase_v.py run --sample 50 --category defense
"""

import json
import random
import signal
import time
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Phase V validation: pdf_oxide vs PyMuPDF end-to-end.")
console = Console()

CATEGORIES = ["arxiv", "defense", "nasa", "nist", "engineering", "industry", "adversarial"]

# Expected domain mappings per corpus category
DOMAIN_EXPECTATIONS = {
    "arxiv": {"academic", "arxiv", "general", "unknown"},
    "defense": {"defense", "government", "engineering", "general", "unknown", "standards", "nist"},
    "nasa": {"nasa", "government", "engineering", "general", "unknown"},
    "nist": {"nist", "standards", "government", "general", "unknown"},
    "engineering": {"engineering", "defense", "general", "unknown"},
    "industry": {"industry", "general", "unknown", "engineering", "standards"},
    "adversarial": set(),  # anything goes
}


class _Timeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _Timeout("Processing timed out")


def sample_corpus(
    corpus_dir: str,
    sample_size: int,
    category: Optional[str] = None,
    seed: int = 42,
) -> list[tuple[str, str]]:
    """Sample PDFs from the corpus, stratified by category."""
    cats = [category] if category else CATEGORIES

    all_pdfs: list[tuple[str, str]] = []
    for cat in cats:
        cat_dir = Path(corpus_dir) / cat
        if not cat_dir.exists():
            continue
        for p in cat_dir.glob("**/*.pdf"):
            all_pdfs.append((cat, str(p)))

    random.seed(seed)

    if category:
        random.shuffle(all_pdfs)
        return all_pdfs[:sample_size]

    # Stratified: min 2 per category, then fill remaining proportionally
    by_cat: dict[str, list[str]] = defaultdict(list)
    for cat, path in all_pdfs:
        by_cat[cat].append(path)

    result: list[tuple[str, str]] = []
    remaining = sample_size

    for cat in sorted(by_cat.keys()):
        paths = by_cat[cat]
        n = min(2, len(paths), remaining)
        sampled = random.sample(paths, n)
        for p in sampled:
            result.append((cat, p))
        remaining -= n
        by_cat[cat] = [p for p in paths if p not in set(sampled)]

    if remaining > 0:
        pool = [(cat, p) for cat, paths in by_cat.items() for p in paths]
        random.shuffle(pool)
        result.extend(pool[:remaining])

    return result


def extract_oxide(pdf_path: str, timeout_sec: int = 60) -> dict:
    """Run pdf_oxide extract_document() and collect metrics."""
    import pdf_oxide

    out = {
        "success": False,
        "texts": [],
        "raw_texts": [],
        "page_count": 0,
        "time_ms": 0.0,
        "block_count": 0,
        "section_count": 0,
        "figure_count": 0,
        "profile_domain": "",
        "profile_preset": "",
        "is_scanned": False,
        "strategy": "",
        "block_types": [],
        "error": None,
    }

    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_sec)

        start = time.perf_counter()
        doc = pdf_oxide.PdfDocument(pdf_path)
        extraction = doc.extract_document()
        out["time_ms"] = (time.perf_counter() - start) * 1000

        pages = extraction.get("pages", [])
        profile = extraction.get("profile", {})

        out["page_count"] = extraction.get("page_count", len(pages))
        out["texts"] = [p.get("text", "") for p in pages]
        out["section_count"] = len(extraction.get("sections", []))
        out["figure_count"] = len(extraction.get("figures", []))
        out["strategy"] = extraction.get("recommended_strategy", "")
        out["profile_domain"] = profile.get("domain", "")
        out["profile_preset"] = profile.get("preset", "")
        out["is_scanned"] = profile.get("is_scanned", False)

        block_types: set[str] = set()
        total_blocks = 0
        for p in pages:
            blocks = p.get("blocks", [])
            total_blocks += len(blocks)
            for b in blocks:
                block_types.add(b.get("block_type", "unknown"))
        out["block_count"] = total_blocks
        out["block_types"] = sorted(block_types)

        # Raw per-page text for apples-to-apples comparison
        for pg in range(doc.page_count()):
            out["raw_texts"].append(doc.extract_text(pg))

        out["success"] = True

    except _Timeout:
        out["error"] = f"Timeout after {timeout_sec}s"
    except Exception as e:
        out["error"] = str(e)
    finally:
        signal.alarm(0)

    return out


def extract_pymupdf(pdf_path: str, timeout_sec: int = 30) -> dict:
    """Extract text using PyMuPDF."""
    import pymupdf

    out = {
        "success": False,
        "texts": [],
        "page_count": 0,
        "time_ms": 0.0,
        "error": None,
    }

    try:
        old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(timeout_sec)

        start = time.perf_counter()
        doc = pymupdf.open(pdf_path)
        out["page_count"] = len(doc)

        for page_num in range(len(doc)):
            out["texts"].append(doc[page_num].get_text())

        out["time_ms"] = (time.perf_counter() - start) * 1000
        out["success"] = True
        doc.close()

    except _Timeout:
        out["error"] = f"Timeout after {timeout_sec}s"
    except Exception as e:
        out["error"] = str(e)
    finally:
        signal.alarm(0)

    return out


def text_similarity(texts_a: list[str], texts_b: list[str], max_chars: int = 5000) -> float:
    """Compute mean SequenceMatcher ratio across pages."""
    if not texts_a and not texts_b:
        return 1.0

    scores = []
    n = max(len(texts_a), len(texts_b))

    for i in range(n):
        a = (texts_a[i] if i < len(texts_a) else "")[:max_chars]
        b = (texts_b[i] if i < len(texts_b) else "")[:max_chars]

        if not a and not b:
            scores.append(1.0)
        elif not a or not b:
            scores.append(0.01)
        else:
            scores.append(SequenceMatcher(None, a, b, autojunk=False).ratio())

    return sum(scores) / len(scores) if scores else 0.0


def domain_matches_category(domain: str, category: str) -> bool:
    """Check if the detected domain is reasonable for the corpus category."""
    expected = DOMAIN_EXPECTATIONS.get(category, set())
    if not expected:
        return True  # adversarial — anything goes
    return domain in expected


@app.command()
def run(
    corpus: str = typer.Option(
        "/mnt/storage12tb/extractor_corpus",
        help="Path to corpus directory",
    ),
    sample: int = typer.Option(20, help="Number of PDFs to sample"),
    category: Optional[str] = typer.Option(None, help="Limit to a specific category"),
    quick: bool = typer.Option(False, help="Quick mode: sample only 5 PDFs"),
    seed: int = typer.Option(42, help="Random seed for reproducibility"),
    output: str = typer.Option(
        "scripts/phase_v_results.json",
        help="Output JSON path",
    ),
    timeout: int = typer.Option(60, help="Per-PDF timeout in seconds"),
    verbose: bool = typer.Option(False, help="Show per-PDF detail during run"),
):
    """Run Phase V validation comparing pdf_oxide vs PyMuPDF."""
    n = 5 if quick else sample

    console.print(f"[bold]Phase V Validation[/bold] -- sampling {n} PDFs from {corpus}")

    pdfs = sample_corpus(corpus, n, category, seed)
    console.print(f"Selected {len(pdfs)} PDFs")

    cat_counts = defaultdict(int)
    for cat, _ in pdfs:
        cat_counts[cat] += 1
    for cat, cnt in sorted(cat_counts.items()):
        console.print(f"  {cat}: {cnt}")

    # -- Run both extractors on every PDF --
    per_pdf: list[dict] = []
    raw_scores: list[float] = []
    pipe_scores: list[float] = []
    oxide_times: list[float] = []
    pymupdf_times: list[float] = []
    domain_hits = 0
    domain_total = 0
    crashes = 0
    timeouts = 0

    for idx, (cat, path) in enumerate(pdfs):
        fname = Path(path).name
        short = fname[:50] + "..." if len(fname) > 50 else fname

        oxide = extract_oxide(path, timeout_sec=timeout)
        mupdf = extract_pymupdf(path, timeout_sec=timeout)

        entry: dict = {
            "file": path,
            "filename": fname,
            "category": cat,
            "oxide_success": oxide["success"],
            "pymupdf_success": mupdf["success"],
            "oxide_error": oxide["error"],
            "pymupdf_error": mupdf["error"],
            "oxide_time_ms": round(oxide["time_ms"], 1),
            "pymupdf_time_ms": round(mupdf["time_ms"], 1),
            "page_count": oxide["page_count"] if oxide["success"] else mupdf["page_count"],
            "raw_text_parity": None,
            "pipeline_text_parity": None,
            "block_count": None,
            "section_count": None,
            "figure_count": None,
            "profile_domain": None,
            "domain_match": None,
            "strategy": None,
            "block_types": None,
        }

        status = "OK"

        if not oxide["success"]:
            if oxide["error"] and "Timeout" in oxide["error"]:
                timeouts += 1
                status = "TIMEOUT"
            else:
                crashes += 1
                status = "CRASH"
        else:
            entry["block_count"] = oxide["block_count"]
            entry["section_count"] = oxide["section_count"]
            entry["figure_count"] = oxide["figure_count"]
            entry["profile_domain"] = oxide["profile_domain"]
            entry["strategy"] = oxide["strategy"]
            entry["block_types"] = oxide["block_types"]

            oxide_times.append(oxide["time_ms"])

            if mupdf["success"]:
                pymupdf_times.append(mupdf["time_ms"])

                raw_sim = text_similarity(oxide["raw_texts"], mupdf["texts"])
                pipe_sim = text_similarity(oxide["texts"], mupdf["texts"])
                entry["raw_text_parity"] = round(raw_sim, 4)
                entry["pipeline_text_parity"] = round(pipe_sim, 4)
                raw_scores.append(raw_sim)
                pipe_scores.append(pipe_sim)

                if raw_sim < 0.5:
                    status = "LOW_SIM"

            dm = domain_matches_category(oxide["profile_domain"], cat)
            entry["domain_match"] = dm
            domain_total += 1
            if dm:
                domain_hits += 1

        per_pdf.append(entry)

        label = f"[{idx+1}/{len(pdfs)}]"
        raw_str = f"raw={entry['raw_text_parity']:.2f}" if entry["raw_text_parity"] is not None else "raw=N/A"
        console.print(
            f"  {label:>8s} {status:8s} {cat:12s} {short:50s} "
            f"{raw_str}  {oxide['time_ms']:.0f}ms"
        )
        if verbose and oxide["error"]:
            console.print(f"           [red]ERROR: {oxide['error']}[/red]")

    # -- Compute summary --
    def safe_mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    def pct_above(xs, thresh):
        return (sum(1 for x in xs if x >= thresh) / len(xs) * 100) if xs else 0.0

    summary = {
        "total_pdfs": len(per_pdf),
        "successful": len(per_pdf) - crashes - timeouts,
        "crashes": crashes,
        "timeouts": timeouts,
        "mean_raw_text_parity": round(safe_mean(raw_scores), 4),
        "mean_pipeline_text_parity": round(safe_mean(pipe_scores), 4),
        "pct_above_80": round(pct_above(raw_scores, 0.80), 1),
        "pct_above_90": round(pct_above(raw_scores, 0.90), 1),
        "mean_oxide_ms": round(safe_mean(oxide_times), 1),
        "mean_pymupdf_ms": round(safe_mean(pymupdf_times), 1),
        "speed_ratio": round(
            safe_mean(pymupdf_times) / safe_mean(oxide_times), 2
        ) if oxide_times and pymupdf_times and safe_mean(oxide_times) > 0 else None,
        "profile_domain_match_pct": round(
            domain_hits / domain_total * 100, 1
        ) if domain_total > 0 else None,
        "mean_block_count": round(safe_mean([
            e["block_count"] for e in per_pdf if e["block_count"] is not None
        ]), 1),
        "mean_section_count": round(safe_mean([
            e["section_count"] for e in per_pdf if e["section_count"] is not None
        ]), 1),
    }

    # -- Write JSON output --
    output_data = {
        "date": datetime.now(timezone.utc).isoformat(),
        "sample_size": len(per_pdf),
        "quick_mode": quick,
        "corpus": corpus,
        "seed": seed,
        "summary": summary,
        "per_pdf": per_pdf,
    }

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output_data, f, indent=2, default=str)
    console.print(f"\nResults written to [bold]{out_path}[/bold]")

    # -- Rich summary table --
    console.print()
    table = Table(title="Phase V Validation Summary", show_lines=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total PDFs", str(summary["total_pdfs"]))
    table.add_row("Successful", str(summary["successful"]))
    table.add_row("Crashes", str(summary["crashes"]))
    table.add_row("Timeouts", str(summary["timeouts"]))
    table.add_row("Mean raw text parity", f"{summary['mean_raw_text_parity']:.3f}")
    table.add_row("Mean pipeline text parity", f"{summary['mean_pipeline_text_parity']:.3f}")
    table.add_row(">= 80% parity", f"{summary['pct_above_80']:.1f}%")
    table.add_row(">= 90% parity", f"{summary['pct_above_90']:.1f}%")
    table.add_row("Mean pdf_oxide (ms)", f"{summary['mean_oxide_ms']:.1f}")
    table.add_row("Mean PyMuPDF (ms)", f"{summary['mean_pymupdf_ms']:.1f}")
    table.add_row(
        "Speed ratio (PyMuPDF/oxide)",
        f"{summary['speed_ratio']:.2f}x" if summary["speed_ratio"] else "N/A",
    )
    table.add_row(
        "Domain match %",
        f"{summary['profile_domain_match_pct']:.1f}%" if summary["profile_domain_match_pct"] is not None else "N/A",
    )
    table.add_row("Mean blocks/PDF", f"{summary['mean_block_count']:.1f}")
    table.add_row("Mean sections/PDF", f"{summary['mean_section_count']:.1f}")

    console.print(table)

    # -- Per-category breakdown table --
    cat_data: dict[str, list[dict]] = defaultdict(list)
    for e in per_pdf:
        cat_data[e["category"]].append(e)

    cat_table = Table(title="Per-Category Breakdown", show_lines=True)
    cat_table.add_column("Category", style="bold")
    cat_table.add_column("N", justify="right")
    cat_table.add_column("OK", justify="right")
    cat_table.add_column("Raw Parity", justify="right")
    cat_table.add_column(">=80%", justify="right")
    cat_table.add_column("Oxide ms", justify="right")
    cat_table.add_column("MuPDF ms", justify="right")
    cat_table.add_column("Blocks", justify="right")
    cat_table.add_column("Sections", justify="right")
    cat_table.add_column("Domain OK", justify="right")

    for cat in sorted(cat_data.keys()):
        entries = cat_data[cat]
        n_cat = len(entries)
        n_ok = sum(1 for e in entries if e["oxide_success"])
        raw_sims = [e["raw_text_parity"] for e in entries if e["raw_text_parity"] is not None]
        avg_raw = safe_mean(raw_sims)
        p80 = pct_above(raw_sims, 0.80)
        ot = safe_mean([e["oxide_time_ms"] for e in entries if e["oxide_success"]])
        mt = safe_mean([e["pymupdf_time_ms"] for e in entries if e["pymupdf_success"]])
        blk = safe_mean([e["block_count"] for e in entries if e["block_count"] is not None])
        sec = safe_mean([e["section_count"] for e in entries if e["section_count"] is not None])
        dm = sum(1 for e in entries if e.get("domain_match")) / max(n_ok, 1) * 100

        cat_table.add_row(
            cat,
            str(n_cat),
            str(n_ok),
            f"{avg_raw:.3f}",
            f"{p80:.0f}%",
            f"{ot:.0f}",
            f"{mt:.0f}",
            f"{blk:.1f}",
            f"{sec:.1f}",
            f"{dm:.0f}%",
        )

    console.print(cat_table)

    # -- Verdict --
    console.print()
    if crashes == 0 and raw_scores:
        mean_raw = safe_mean(raw_scores)
        if mean_raw >= 0.80:
            console.print("[bold green]VERDICT: PASS[/bold green] -- Phase V validation successful")
        elif mean_raw >= 0.70:
            console.print("[bold yellow]VERDICT: ACCEPTABLE[/bold yellow] -- minor improvements needed")
        else:
            console.print("[bold red]VERDICT: NEEDS WORK[/bold red] -- text quality below threshold")
    elif crashes > 0:
        console.print(f"[bold red]VERDICT: UNSTABLE[/bold red] -- {crashes} crash(es)")
    else:
        console.print("[bold yellow]VERDICT: INCONCLUSIVE[/bold yellow] -- no comparisons made")


if __name__ == "__main__":
    app()
