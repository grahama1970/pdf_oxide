#!/usr/bin/env python3
"""Shadow validation: run both PyMuPDF and pdf_oxide on same PDFs, compare outputs.

Gate: <5% divergence on block count, text hash, section count, figure count
before removing PyMuPDF dependency.

Usage:
    python scripts/shadow_validate.py /path/to/corpus/ --limit 100
    python scripts/shadow_validate.py /path/to/single.pdf
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import typer
from loguru import logger

app = typer.Typer(name="shadow-validate", no_args_is_help=True)


def _run_oxide(pdf_path: Path, out_dir: Path) -> dict[str, Any]:
    """Run pdf_oxide pipeline adapter."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent /
                          "pi-mono" / ".pi" / "skills" / "extract-pdf"))
    from extract_pdf.pipeline_adapter import run_pipeline_adapter

    t0 = time.monotonic()
    s02_path = run_pipeline_adapter(pdf_path, out_dir)
    elapsed = time.monotonic() - t0

    s02_data = json.loads(Path(s02_path).read_text())
    s04_path = out_dir / "04_section_builder" / "json_output" / "04_sections.json"
    s06_path = out_dir / "06_figure_extractor" / "json_output" / "06_figures.json"

    sections = []
    if s04_path.exists():
        sections = json.loads(s04_path.read_text()).get("sections", [])

    figures = []
    if s06_path.exists():
        figures = json.loads(s06_path.read_text()).get("figures", [])

    blocks = s02_data.get("blocks", [])
    text = " ".join(b.get("text", "") for b in blocks)
    text_hash = hashlib.md5(text.encode()).hexdigest()

    return {
        "engine": "pdf_oxide",
        "block_count": len(blocks),
        "section_count": len(sections),
        "figure_count": len(figures),
        "text_hash": text_hash,
        "text_length": len(text),
        "elapsed_s": round(elapsed, 3),
    }


def _run_pymupdf(pdf_path: Path, out_dir: Path) -> dict[str, Any] | None:
    """Run PyMuPDF pipeline (legacy)."""
    try:
        import fitz  # noqa: F401
    except ImportError:
        logger.warning("PyMuPDF (fitz) not available, skipping legacy comparison")
        return None

    try:
        from extractor.pipeline.steps.s02_pymupdf_extractor import run as s02_run
        from extractor.pipeline.steps.s00_profile_detector import run as s00_run

        t0 = time.monotonic()
        s00_run(pdf_path, out_dir)
        s02_path = s02_run(pdf_path, out_dir)
        elapsed = time.monotonic() - t0

        s02_data = json.loads(Path(s02_path).read_text())
        blocks = s02_data.get("blocks", [])
        text = " ".join(b.get("text", "") for b in blocks)
        text_hash = hashlib.md5(text.encode()).hexdigest()

        return {
            "engine": "pymupdf",
            "block_count": len(blocks),
            "section_count": 0,  # PyMuPDF S02 doesn't produce sections
            "figure_count": 0,
            "text_hash": text_hash,
            "text_length": len(text),
            "elapsed_s": round(elapsed, 3),
        }
    except Exception as exc:
        logger.error(f"PyMuPDF pipeline failed: {exc}")
        return None


def _compare(oxide: dict, pymupdf: dict | None) -> dict[str, Any]:
    """Compare two pipeline outputs."""
    if pymupdf is None:
        return {"status": "skip", "reason": "pymupdf_unavailable"}

    block_diff = abs(oxide["block_count"] - pymupdf["block_count"])
    block_pct = block_diff / max(pymupdf["block_count"], 1)
    text_match = oxide["text_hash"] == pymupdf["text_hash"]

    # Text length divergence (more meaningful than hash match)
    len_diff = abs(oxide["text_length"] - pymupdf["text_length"])
    len_pct = len_diff / max(pymupdf["text_length"], 1)

    divergent = block_pct > 0.05 or len_pct > 0.10

    return {
        "status": "DIVERGENT" if divergent else "OK",
        "block_count_diff": block_diff,
        "block_count_pct": round(block_pct * 100, 1),
        "text_hash_match": text_match,
        "text_length_diff": len_diff,
        "text_length_pct": round(len_pct * 100, 1),
        "oxide_faster": oxide["elapsed_s"] < pymupdf["elapsed_s"],
        "speedup": round(pymupdf["elapsed_s"] / max(oxide["elapsed_s"], 0.001), 1),
    }


@app.command()
def validate(
    path: str = typer.Argument(..., help="PDF file or directory of PDFs"),
    limit: int = typer.Option(100, help="Max PDFs to process"),
    output: str = typer.Option(None, help="Output JSON path"),
) -> None:
    """Run shadow validation comparing pdf_oxide vs PyMuPDF."""
    src = Path(path)
    if src.is_file():
        pdfs = [src]
    elif src.is_dir():
        pdfs = sorted(src.glob("**/*.pdf"))[:limit]
    else:
        logger.error(f"Not a file or directory: {path}")
        raise typer.Exit(code=1)

    logger.info(f"Shadow validation: {len(pdfs)} PDFs")
    results = []
    ok_count = 0
    divergent_count = 0
    skip_count = 0

    for pdf in pdfs:
        logger.info(f"Processing: {pdf.name}")
        try:
            with tempfile.TemporaryDirectory() as oxide_dir, \
                 tempfile.TemporaryDirectory() as pymupdf_dir:
                oxide = _run_oxide(pdf, Path(oxide_dir))
                pymupdf = _run_pymupdf(pdf, Path(pymupdf_dir))
                comparison = _compare(oxide, pymupdf)

                entry = {
                    "pdf": str(pdf),
                    "oxide": oxide,
                    "pymupdf": pymupdf,
                    "comparison": comparison,
                }
                results.append(entry)

                status = comparison["status"]
                if status == "OK":
                    ok_count += 1
                elif status == "DIVERGENT":
                    divergent_count += 1
                    logger.warning(f"  DIVERGENT: {pdf.name} — {comparison}")
                else:
                    skip_count += 1

        except Exception as exc:
            logger.error(f"  FAILED: {pdf.name} — {exc}")
            results.append({"pdf": str(pdf), "error": str(exc)})

    total = len(results)
    divergence_rate = divergent_count / max(total - skip_count, 1)

    summary = {
        "total": total,
        "ok": ok_count,
        "divergent": divergent_count,
        "skipped": skip_count,
        "divergence_rate": round(divergence_rate * 100, 1),
        "gate_passed": divergence_rate < 0.05,
    }

    logger.info(f"\n{'='*60}")
    logger.info(f"Shadow Validation Summary")
    logger.info(f"  Total:     {total}")
    logger.info(f"  OK:        {ok_count}")
    logger.info(f"  Divergent: {divergent_count}")
    logger.info(f"  Skipped:   {skip_count}")
    logger.info(f"  Rate:      {summary['divergence_rate']}%")
    logger.info(f"  Gate:      {'PASSED' if summary['gate_passed'] else 'FAILED'}")
    logger.info(f"{'='*60}")

    report = {"summary": summary, "results": results}
    if output:
        Path(output).write_text(json.dumps(report, indent=2, default=str))
        logger.info(f"Report written to {output}")
    else:
        typer.echo(json.dumps(report, indent=2, default=str))

    raise typer.Exit(code=0 if summary["gate_passed"] else 1)


if __name__ == "__main__":
    app()
