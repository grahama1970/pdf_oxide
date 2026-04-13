"""Round-trip benchmark: ReportLab fixture → extraction → manifest comparison.

This is the VALID benchmark. The manifest is ground truth (we know exactly what
ReportLab wrote). Extractors are compared against this oracle.

Extractors tested:
- PyMuPDF (fitz): Raw text extraction
- Camelot: Structured table extraction (lattice + stream)
"""
from __future__ import annotations
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz

from .fixture_generator import FixtureManifest, generate_fixture, PRESETS


# Try to import Camelot (optional)
try:
    import camelot
    CAMELOT_AVAILABLE = True
except ImportError:
    CAMELOT_AVAILABLE = False


@dataclass
class CellMatch:
    """Result of matching an extracted cell to manifest."""
    manifest_qid: str
    manifest_text: str
    extracted_text: str | None
    qid_found: bool
    text_match: bool
    row: int
    col: int


@dataclass
class TableResult:
    """Extraction result for a single table."""
    table_id: str
    preset: str
    manifest_rows: int
    manifest_cols: int
    manifest_cells: int
    extracted_cells: int
    qid_recovery_rate: float
    text_match_rate: float
    cell_matches: list[CellMatch] = field(default_factory=list)


@dataclass
class BenchmarkResult:
    """Overall benchmark result."""
    fixture_id: str
    pdf_path: str
    total_tables: int
    total_cells: int
    total_qids_found: int
    total_text_matches: int
    overall_qid_recovery: float
    overall_text_accuracy: float
    by_preset: dict[str, dict[str, float]] = field(default_factory=dict)
    table_results: list[TableResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    extractor_comparison: dict[str, ExtractorResult] = field(default_factory=dict)


def extract_text_from_page(pdf_path: str, page_num: int) -> str:
    """Extract all text from a PDF page using PyMuPDF."""
    doc = fitz.open(pdf_path)
    page = doc[page_num - 1]  # 0-indexed
    text = page.get_text()
    doc.close()
    return text


def extract_qids_from_text(text: str) -> dict[str, str]:
    """Extract QID markers and their associated text from extracted text.

    Returns dict mapping QID -> text following the QID marker.
    """
    # Pattern: [QID_xxx]text
    pattern = r'\[QID_([A-F0-9]+)\]([^\[]*)'
    matches = re.findall(pattern, text)

    result = {}
    for qid_hash, following_text in matches:
        qid = f"QID_{qid_hash}"
        # Clean up text (strip whitespace, newlines)
        clean_text = following_text.strip().split('\n')[0].strip()
        result[qid] = clean_text

    return result


def extract_with_camelot(pdf_path: str, page_num: int) -> dict[str, str]:
    """Extract QIDs from a page using Camelot table extraction.

    Returns dict mapping QID -> text.
    """
    if not CAMELOT_AVAILABLE:
        return {}

    extracted_qids = {}

    try:
        # Try lattice first (for ruled tables)
        tables = camelot.read_pdf(pdf_path, pages=str(page_num), flavor="lattice")
        if len(tables) == 0:
            # Try stream (for borderless tables)
            tables = camelot.read_pdf(pdf_path, pages=str(page_num), flavor="stream")

        for table in tables:
            # Get all cell text
            df = table.df
            for _, row in df.iterrows():
                for cell in row:
                    cell_text = str(cell)
                    qids = extract_qids_from_text(cell_text)
                    extracted_qids.update(qids)

    except Exception as e:
        pass  # Camelot can fail on some pages

    return extracted_qids


@dataclass
class ExtractorResult:
    """Result from a single extractor."""
    name: str
    qids_found: int
    text_matches: int
    qid_recovery: float
    text_accuracy: float
    latency_ms: float


def run_benchmark(
    pdf_path: str,
    manifest_path: str,
) -> BenchmarkResult:
    """Run the round-trip benchmark.

    Args:
        pdf_path: Path to the fixture PDF
        manifest_path: Path to the manifest JSON

    Returns:
        BenchmarkResult with extraction accuracy metrics
    """
    # Load manifest
    manifest_data = json.loads(Path(manifest_path).read_text())
    manifest = FixtureManifest(
        fixture_id=manifest_data["fixture_id"],
        generated_at=manifest_data["generated_at"],
        seed=manifest_data["seed"],
        pages=[],
        presets_used=manifest_data["presets_used"],
    )

    # Extract text from each page
    doc = fitz.open(pdf_path)
    page_texts = {}
    for page_num in range(1, doc.page_count + 1):
        page_texts[page_num] = extract_text_from_page(pdf_path, page_num)
    doc.close()

    # Process each table from manifest
    table_results = []
    total_cells = 0
    total_qids_found = 0
    total_text_matches = 0
    preset_stats: dict[str, dict[str, int]] = {}
    errors = []

    for page_data in manifest_data["pages"]:
        page_num = page_data["page_num"]
        page_text = page_texts.get(page_num, "")

        # Extract all QIDs from this page
        extracted_qids = extract_qids_from_text(page_text)

        for table_data in page_data["tables"]:
            table_id = table_data["table_id"]
            preset = table_data["preset"]

            # Initialize preset stats
            if preset not in preset_stats:
                preset_stats[preset] = {"cells": 0, "qids_found": 0, "text_matches": 0}

            cell_matches = []
            table_qids_found = 0
            table_text_matches = 0

            for cell_data in table_data["cells"]:
                qid = cell_data["qid"]
                manifest_text = cell_data["text"]
                row = cell_data["row"]
                col = cell_data["col"]

                # Check if QID was extracted
                qid_found = qid in extracted_qids
                extracted_text = extracted_qids.get(qid)

                # Check text match (normalize for comparison)
                text_match = False
                if extracted_text:
                    # Normalize: lowercase, strip, remove extra whitespace
                    norm_manifest = " ".join(manifest_text.lower().split())
                    norm_extracted = " ".join(extracted_text.lower().split())
                    text_match = norm_manifest == norm_extracted or norm_manifest in norm_extracted

                cell_matches.append(CellMatch(
                    manifest_qid=qid,
                    manifest_text=manifest_text,
                    extracted_text=extracted_text,
                    qid_found=qid_found,
                    text_match=text_match,
                    row=row,
                    col=col,
                ))

                if qid_found:
                    table_qids_found += 1
                    total_qids_found += 1
                    preset_stats[preset]["qids_found"] += 1

                if text_match:
                    table_text_matches += 1
                    total_text_matches += 1
                    preset_stats[preset]["text_matches"] += 1

                total_cells += 1
                preset_stats[preset]["cells"] += 1

            # Compute table-level rates
            n_cells = len(table_data["cells"])
            qid_rate = table_qids_found / n_cells if n_cells > 0 else 0.0
            text_rate = table_text_matches / n_cells if n_cells > 0 else 0.0

            table_results.append(TableResult(
                table_id=table_id,
                preset=preset,
                manifest_rows=table_data["rows"],
                manifest_cols=table_data["cols"],
                manifest_cells=n_cells,
                extracted_cells=table_qids_found,
                qid_recovery_rate=qid_rate,
                text_match_rate=text_rate,
                cell_matches=cell_matches,
            ))

    # Compute overall rates
    overall_qid = total_qids_found / total_cells if total_cells > 0 else 0.0
    overall_text = total_text_matches / total_cells if total_cells > 0 else 0.0

    # Compute by-preset rates
    by_preset = {}
    for preset, stats in preset_stats.items():
        n = stats["cells"]
        by_preset[preset] = {
            "cells": n,
            "qid_recovery": stats["qids_found"] / n if n > 0 else 0.0,
            "text_accuracy": stats["text_matches"] / n if n > 0 else 0.0,
        }

    # Run extractor comparison
    extractor_comparison = {}

    # PyMuPDF extractor (already computed above)
    extractor_comparison["pymupdf"] = ExtractorResult(
        name="PyMuPDF",
        qids_found=total_qids_found,
        text_matches=total_text_matches,
        qid_recovery=overall_qid,
        text_accuracy=overall_text,
        latency_ms=0.0,  # not timed in main loop
    )

    # Camelot extractor
    if CAMELOT_AVAILABLE:
        camelot_start = time.perf_counter()
        camelot_qids_found = 0
        camelot_text_matches = 0

        for page_data in manifest_data["pages"]:
            page_num = page_data["page_num"]
            camelot_qids = extract_with_camelot(pdf_path, page_num)

            for table_data in page_data["tables"]:
                for cell_data in table_data["cells"]:
                    qid = cell_data["qid"]
                    manifest_text = cell_data["text"]

                    if qid in camelot_qids:
                        camelot_qids_found += 1
                        extracted_text = camelot_qids[qid]
                        norm_manifest = " ".join(manifest_text.lower().split())
                        norm_extracted = " ".join(extracted_text.lower().split())
                        if norm_manifest == norm_extracted or norm_manifest in norm_extracted:
                            camelot_text_matches += 1

        camelot_latency = (time.perf_counter() - camelot_start) * 1000
        extractor_comparison["camelot"] = ExtractorResult(
            name="Camelot",
            qids_found=camelot_qids_found,
            text_matches=camelot_text_matches,
            qid_recovery=camelot_qids_found / total_cells if total_cells > 0 else 0.0,
            text_accuracy=camelot_text_matches / total_cells if total_cells > 0 else 0.0,
            latency_ms=camelot_latency,
        )

    return BenchmarkResult(
        fixture_id=manifest_data["fixture_id"],
        pdf_path=pdf_path,
        total_tables=len(table_results),
        total_cells=total_cells,
        total_qids_found=total_qids_found,
        total_text_matches=total_text_matches,
        overall_qid_recovery=overall_qid,
        overall_text_accuracy=overall_text,
        by_preset=by_preset,
        table_results=table_results,
        errors=errors,
        extractor_comparison=extractor_comparison,
    )


def print_report(result: BenchmarkResult) -> None:
    """Print human-readable benchmark report."""
    print("=" * 80)
    print("ROUND-TRIP BENCHMARK REPORT")
    print("=" * 80)
    print(f"Fixture ID: {result.fixture_id}")
    print(f"PDF: {result.pdf_path}")
    print()

    # Overall summary
    print("OVERALL RESULTS")
    print("-" * 80)
    print(f"  Tables:           {result.total_tables}")
    print(f"  Total Cells:      {result.total_cells}")
    print(f"  QIDs Recovered:   {result.total_qids_found} ({result.overall_qid_recovery:.1%})")
    print(f"  Text Matches:     {result.total_text_matches} ({result.overall_text_accuracy:.1%})")
    print()

    # By preset
    print("BY PRESET")
    print("-" * 80)
    print(f"{'Preset':<20} | {'Cells':>6} | {'QID Recovery':>12} | {'Text Accuracy':>13}")
    print("-" * 80)
    for preset, stats in sorted(result.by_preset.items()):
        print(f"{preset:<20} | {stats['cells']:>6} | {stats['qid_recovery']:>11.1%} | {stats['text_accuracy']:>12.1%}")
    print("-" * 80)
    print()

    # Table details
    print("TABLE DETAILS")
    print("-" * 80)
    for tr in result.table_results:
        status = "PASS" if tr.qid_recovery_rate == 1.0 else "PARTIAL" if tr.qid_recovery_rate > 0 else "FAIL"
        print(f"  {tr.table_id} ({tr.preset}): {tr.extracted_cells}/{tr.manifest_cells} QIDs ({tr.qid_recovery_rate:.0%}) [{status}]")

        # Show failures
        if tr.qid_recovery_rate < 1.0:
            missing = [cm for cm in tr.cell_matches if not cm.qid_found][:3]
            for cm in missing:
                print(f"    - MISSING: [{cm.row},{cm.col}] {cm.manifest_qid} '{cm.manifest_text[:30]}'")

    print("=" * 80)
    print()

    # Extractor comparison
    if result.extractor_comparison:
        print("EXTRACTOR COMPARISON")
        print("-" * 80)
        print(f"{'Extractor':<15} | {'QIDs Found':>10} | {'QID Recovery':>12} | {'Text Accuracy':>13} | {'Latency':>10}")
        print("-" * 80)
        for name, er in result.extractor_comparison.items():
            latency_str = f"{er.latency_ms:.0f}ms" if er.latency_ms > 0 else "-"
            print(f"{er.name:<15} | {er.qids_found:>10} | {er.qid_recovery:>11.1%} | {er.text_accuracy:>12.1%} | {latency_str:>10}")
        print("-" * 80)
        print()

    print("=" * 80)

    # Verdict
    if result.overall_qid_recovery >= 0.95:
        print("VERDICT: PASS - QID recovery >= 95%")
    elif result.overall_qid_recovery >= 0.80:
        print("VERDICT: PARTIAL - QID recovery 80-95%")
    else:
        print("VERDICT: FAIL - QID recovery < 80%")


def generate_and_benchmark(
    output_dir: str = "tests/fixtures/generated",
    presets: list[str] | None = None,
    seed: int = 42,
    verbose: bool = True,
) -> BenchmarkResult:
    """Generate fixtures and run benchmark in one step.

    This is the main entry point for automated testing.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_path = out_dir / "benchmark_fixtures.pdf"
    manifest_path = out_dir / "benchmark_fixtures.manifest.json"

    # Generate fixtures
    manifest = generate_fixture(str(pdf_path), presets=presets, seed=seed)
    manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2))

    if verbose:
        print(f"Generated: {pdf_path}")
        print(f"  Tables: {sum(len(p.tables) for p in manifest.pages)}")
        print(f"  Cells:  {sum(len(t.cells) for p in manifest.pages for t in p.tables)}")
        print()

    # Run benchmark
    result = run_benchmark(str(pdf_path), str(manifest_path))

    if verbose:
        print_report(result)

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Round-trip table extraction benchmark")
    parser.add_argument("--pdf", help="PDF fixture path (or generate new)")
    parser.add_argument("--manifest", help="Manifest JSON path")
    parser.add_argument("--generate", action="store_true", help="Generate and benchmark")
    parser.add_argument("--output-dir", default="tests/fixtures/generated")
    parser.add_argument("--presets", nargs="+", help="Presets to test")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if args.generate or (not args.pdf and not args.manifest):
        # Generate and benchmark
        result = generate_and_benchmark(
            output_dir=args.output_dir,
            presets=args.presets,
            seed=args.seed,
            verbose=not args.json,
        )
    else:
        # Benchmark existing
        if not args.pdf or not args.manifest:
            print("Error: --pdf and --manifest required when not generating")
            sys.exit(1)
        result = run_benchmark(args.pdf, args.manifest)
        if not args.json:
            print_report(result)

    if args.json:
        output = {
            "fixture_id": result.fixture_id,
            "total_tables": result.total_tables,
            "total_cells": result.total_cells,
            "qid_recovery": result.overall_qid_recovery,
            "text_accuracy": result.overall_text_accuracy,
            "by_preset": result.by_preset,
            "verdict": "PASS" if result.overall_qid_recovery >= 0.95 else "PARTIAL" if result.overall_qid_recovery >= 0.80 else "FAIL",
        }
        print(json.dumps(output, indent=2))

    # Exit code based on verdict
    if result.overall_qid_recovery >= 0.95:
        sys.exit(0)
    elif result.overall_qid_recovery >= 0.80:
        sys.exit(1)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
