#!/usr/bin/env env python3
"""Three-tier speed benchmark: PyMuPDF vs pdf_oxide raw vs pdf_oxide pipeline."""

import json
import signal
import time
from pathlib import Path

import typer

app = typer.Typer()

CORPUS = Path("/mnt/storage12tb/extractor_corpus")


class _Timeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _Timeout()


def time_pymupdf(pdf_path: str, timeout: int = 30) -> dict:
    """Time PyMuPDF get_text() across all pages."""
    import fitz

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    try:
        start = time.perf_counter()
        doc = fitz.open(pdf_path)
        for page in doc:
            page.get_text()
        elapsed = (time.perf_counter() - start) * 1000
        pc = len(doc)
        doc.close()
        return {"time_ms": elapsed, "page_count": pc, "error": None}
    except _Timeout:
        return {"time_ms": -1, "page_count": 0, "error": "timeout"}
    except Exception as e:
        return {"time_ms": -1, "page_count": 0, "error": str(e)}
    finally:
        signal.alarm(0)


def time_oxide_raw(pdf_path: str, timeout: int = 30) -> dict:
    """Time pdf_oxide extract_text() across all pages (no pipeline)."""
    import pdf_oxide

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    try:
        start = time.perf_counter()
        doc = pdf_oxide.PdfDocument(pdf_path)
        pc = doc.page_count()
        for pg in range(pc):
            doc.extract_text(pg)
        elapsed = (time.perf_counter() - start) * 1000
        return {"time_ms": elapsed, "page_count": pc, "error": None}
    except _Timeout:
        return {"time_ms": -1, "page_count": 0, "error": "timeout"}
    except Exception as e:
        return {"time_ms": -1, "page_count": 0, "error": str(e)}
    finally:
        signal.alarm(0)


def time_oxide_pipeline(pdf_path: str, timeout: int = 60) -> dict:
    """Time pdf_oxide extract_document() full pipeline."""
    import pdf_oxide

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    try:
        start = time.perf_counter()
        doc = pdf_oxide.PdfDocument(pdf_path)
        doc.extract_document()
        elapsed = (time.perf_counter() - start) * 1000
        pc = doc.page_count()
        return {"time_ms": elapsed, "page_count": pc, "error": None}
    except _Timeout:
        return {"time_ms": -1, "page_count": 0, "error": "timeout"}
    except Exception as e:
        return {"time_ms": -1, "page_count": 0, "error": str(e)}
    finally:
        signal.alarm(0)


@app.command()
def run(
    n: int = typer.Option(100, help="Number of PDFs to benchmark"),
    timeout: int = typer.Option(30, help="Per-PDF timeout in seconds"),
):
    """Run three-tier speed benchmark."""
    # Collect PDFs
    categories = sorted(d.name for d in CORPUS.iterdir() if d.is_dir())
    pdfs = []
    for cat in categories:
        cat_pdfs = sorted((CORPUS / cat).glob("*.pdf"))
        per_cat = max(2, n // len(categories))
        pdfs.extend((cat, str(p)) for p in cat_pdfs[:per_cat])
    pdfs = pdfs[:n]

    typer.echo(f"Benchmarking {len(pdfs)} PDFs across 3 tiers...\n")

    results = []
    pymupdf_times = []
    oxide_raw_times = []
    oxide_pipe_times = []
    page_counts = []

    for i, (cat, path) in enumerate(pdfs):
        name = Path(path).name[:40]
        typer.echo(f"[{i+1}/{len(pdfs)}] {cat}/{name}")

        r_mu = time_pymupdf(path, timeout)
        r_raw = time_oxide_raw(path, timeout)
        r_pipe = time_oxide_pipeline(path, timeout + 30)

        entry = {
            "category": cat,
            "file": Path(path).name,
            "page_count": r_mu["page_count"] or r_raw["page_count"] or r_pipe["page_count"],
            "pymupdf_ms": r_mu["time_ms"],
            "oxide_raw_ms": r_raw["time_ms"],
            "oxide_pipeline_ms": r_pipe["time_ms"],
            "pymupdf_err": r_mu["error"],
            "oxide_raw_err": r_raw["error"],
            "oxide_pipe_err": r_pipe["error"],
        }
        results.append(entry)

        if r_mu["time_ms"] > 0:
            pymupdf_times.append(r_mu["time_ms"])
        if r_raw["time_ms"] > 0:
            oxide_raw_times.append(r_raw["time_ms"])
        if r_pipe["time_ms"] > 0:
            oxide_pipe_times.append(r_pipe["time_ms"])
        if entry["page_count"] > 0:
            page_counts.append(entry["page_count"])

    # Summary
    def stats(times):
        if not times:
            return {"mean": 0, "median": 0, "total": 0, "count": 0}
        s = sorted(times)
        return {
            "mean": sum(s) / len(s),
            "median": s[len(s) // 2],
            "total": sum(s),
            "count": len(s),
            "p95": s[int(len(s) * 0.95)] if len(s) > 1 else s[0],
        }

    mu_s = stats(pymupdf_times)
    raw_s = stats(oxide_raw_times)
    pipe_s = stats(oxide_pipe_times)
    total_pages = sum(page_counts)

    typer.echo("\n" + "=" * 70)
    typer.echo("SPEED BENCHMARK RESULTS")
    typer.echo("=" * 70)
    typer.echo(f"PDFs tested: {len(results)}, Total pages: {total_pages}")
    typer.echo("")
    typer.echo(f"{'Tier':<25} {'Mean ms':<10} {'Median ms':<12} {'P95 ms':<10} {'Total s':<10}")
    typer.echo("-" * 70)
    typer.echo(
        f"{'PyMuPDF get_text()':<25} {mu_s['mean']:>8.1f}  {mu_s['median']:>10.1f}  "
        f"{mu_s.get('p95',0):>8.1f}  {mu_s['total']/1000:>8.1f}"
    )
    typer.echo(
        f"{'pdf_oxide extract_text()':<25} {raw_s['mean']:>8.1f}  {raw_s['median']:>10.1f}  "
        f"{raw_s.get('p95',0):>8.1f}  {raw_s['total']/1000:>8.1f}"
    )
    typer.echo(
        f"{'pdf_oxide pipeline':<25} {pipe_s['mean']:>8.1f}  {pipe_s['median']:>10.1f}  "
        f"{pipe_s.get('p95',0):>8.1f}  {pipe_s['total']/1000:>8.1f}"
    )
    typer.echo("")

    if mu_s["mean"] > 0:
        typer.echo(f"Raw oxide / PyMuPDF ratio:      {raw_s['mean']/mu_s['mean']:.2f}x")
        typer.echo(f"Pipeline oxide / PyMuPDF ratio:  {pipe_s['mean']/mu_s['mean']:.2f}x")
    if raw_s["mean"] > 0:
        typer.echo(f"Pipeline / Raw oxide ratio:      {pipe_s['mean']/raw_s['mean']:.2f}x")

    typer.echo("")
    if total_pages > 0:
        typer.echo(f"Per-page averages:")
        typer.echo(f"  PyMuPDF:        {mu_s['total']/total_pages:.1f} ms/page")
        typer.echo(f"  oxide raw:      {raw_s['total']/total_pages:.1f} ms/page")
        typer.echo(f"  oxide pipeline: {pipe_s['total']/total_pages:.1f} ms/page")

    # Per-category breakdown
    typer.echo("\nPer-category (mean ms):")
    typer.echo(f"{'Category':<15} {'PyMuPDF':<10} {'Oxide Raw':<12} {'Pipeline':<12} {'Raw/Mu':<8}")
    typer.echo("-" * 60)
    for cat in categories:
        cat_results = [r for r in results if r["category"] == cat]
        if not cat_results:
            continue
        cat_mu = [r["pymupdf_ms"] for r in cat_results if r["pymupdf_ms"] > 0]
        cat_raw = [r["oxide_raw_ms"] for r in cat_results if r["oxide_raw_ms"] > 0]
        cat_pipe = [r["oxide_pipeline_ms"] for r in cat_results if r["oxide_pipeline_ms"] > 0]
        mu_avg = sum(cat_mu) / len(cat_mu) if cat_mu else 0
        raw_avg = sum(cat_raw) / len(cat_raw) if cat_raw else 0
        pipe_avg = sum(cat_pipe) / len(cat_pipe) if cat_pipe else 0
        ratio = f"{raw_avg/mu_avg:.1f}x" if mu_avg > 0 else "N/A"
        typer.echo(f"{cat:<15} {mu_avg:>8.0f}  {raw_avg:>10.0f}  {pipe_avg:>10.0f}  {ratio:>6}")

    # Save results
    out_path = Path(__file__).parent / "speed_results.json"
    with open(out_path, "w") as f:
        json.dump(
            {"results": results, "summary": {"pymupdf": mu_s, "oxide_raw": raw_s, "oxide_pipeline": pipe_s}},
            f,
            indent=2,
        )
    typer.echo(f"\nDetailed results: {out_path}")


if __name__ == "__main__":
    app()
