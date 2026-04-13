"""Table extraction benchmark: pdf_oxide vs Camelot vs VLM (PDF) vs VLM (PNG).

Compares 4 extraction methods across multiple table pages with pandas integrity checks.
"""
from __future__ import annotations
import base64
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
import httpx
import pandas as pd

from pdf_oxide.clone_profiler import profile_for_cloning


@dataclass
class TableResult:
    """Result from a single extraction method."""
    method: str
    rows: int | None = None
    cols: int | None = None
    df: pd.DataFrame | None = None
    error: str | None = None
    latency_ms: float = 0.0


@dataclass
class IntegrityCheck:
    """Pandas-based integrity validation per Codex review."""
    shape: tuple[int, int] = (0, 0)
    whitespace_pct: float = 0.0
    mixed_dtype_cols: int = 0
    jagged: bool = False
    row_length_var: float = 0.0
    empty_rows: int = 0
    empty_cols: int = 0
    header_repeated: bool = False
    numeric_parse_fails: int = 0
    valid: bool = False


@dataclass
class PageBenchmark:
    """Benchmark results for a single page."""
    page_id: str  # e.g., "1.pdf:3"
    results: dict[str, TableResult] = field(default_factory=dict)
    integrity: dict[str, IntegrityCheck] = field(default_factory=dict)
    agreement: bool = False


def check_table_integrity(df: pd.DataFrame) -> IntegrityCheck:
    """Validate DataFrame integrity per Codex review fixes."""
    checks = IntegrityCheck()
    checks.shape = df.shape
    checks.empty_rows = int((df.isna().all(axis=1)).sum())
    checks.empty_cols = int((df.isna().all(axis=0)).sum())

    # Whitespace check - use .astype("string") for safety
    # Include both "object" and "str" for pandas 2/3 compat
    str_cols = df.select_dtypes(include=["object", "string"])
    if not str_cols.empty:
        try:
            str_df = str_cols.astype("string")
            ws_count = str_df.apply(
                lambda c: c.str.strip().eq("") | c.isna()
            ).sum().sum()
            checks.whitespace_pct = float(ws_count / str_df.size * 100)
        except Exception:
            checks.whitespace_pct = 0.0

    # Mixed dtype - use infer_dtype excluding nulls
    checks.mixed_dtype_cols = 0
    for col in df.columns:
        non_null = df[col].dropna()
        if len(non_null) > 0:
            types = non_null.apply(type).nunique()
            if types > 1:
                checks.mixed_dtype_cols += 1

    # Jagged detection - check row-length variance before coercion
    row_lengths = df.notna().sum(axis=1)
    checks.row_length_var = float(row_lengths.std()) if len(row_lengths) > 1 else 0.0
    checks.jagged = checks.row_length_var > 1.5

    # Header repeated in data
    if len(df) > 1:
        checks.header_repeated = bool((df.iloc[1:].values == df.columns.values).any())

    # Numeric parse failures
    checks.numeric_parse_fails = 0
    for col in df.columns:
        if df[col].dtype == "object":
            original_na = df[col].isna().sum()
            coerced_na = pd.to_numeric(df[col], errors="coerce").isna().sum()
            if coerced_na > original_na:
                checks.numeric_parse_fails += 1

    # Overall validity
    checks.valid = (
        checks.empty_rows == 0
        and checks.empty_cols == 0
        and checks.whitespace_pct < 10.0
        and checks.mixed_dtype_cols == 0
        and not checks.jagged
        and not checks.header_repeated
    )

    return checks


def extract_pdf_oxide(pdf_path: str, page_num: int) -> TableResult:
    """Extract table using pdf_oxide profiler."""
    import time
    start = time.perf_counter()

    try:
        profile = profile_for_cloning(pdf_path)
        tables = [t for t in profile.get("table_shapes", []) if t["page"] == page_num - 1]

        if not tables:
            return TableResult(method="pdf_oxide", error="no table found")

        t = tables[0]
        latency = (time.perf_counter() - start) * 1000

        # pdf_oxide gives rows/cols but not cell content
        return TableResult(
            method="pdf_oxide",
            rows=t["rows"],
            cols=t["cols"],
            latency_ms=latency,
        )
    except Exception as e:
        return TableResult(method="pdf_oxide", error=str(e))


def extract_camelot(pdf_path: str, page_num: int) -> TableResult:
    """Extract table using Camelot."""
    import time
    start = time.perf_counter()

    try:
        import camelot
        tables = camelot.read_pdf(pdf_path, pages=str(page_num), flavor="lattice")

        if len(tables) == 0:
            # Try stream flavor for borderless
            tables = camelot.read_pdf(pdf_path, pages=str(page_num), flavor="stream")

        if len(tables) == 0:
            return TableResult(method="camelot", error="no table found")

        df = tables[0].df
        latency = (time.perf_counter() - start) * 1000

        return TableResult(
            method="camelot",
            rows=df.shape[0],
            cols=df.shape[1],
            df=df,
            latency_ms=latency,
        )
    except Exception as e:
        return TableResult(method="camelot", error=str(e))


def extract_vlm_pdf(pdf_path: str, page_num: int) -> TableResult:
    """Extract table by sending PDF page to VLM via scillm."""
    import time
    start = time.perf_counter()

    try:
        # Extract single page as PDF bytes
        doc = fitz.open(pdf_path)
        single_page_doc = fitz.open()
        single_page_doc.insert_pdf(doc, from_page=page_num - 1, to_page=page_num - 1)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            single_page_doc.save(f.name)
            pdf_bytes = Path(f.name).read_bytes()
            pdf_b64 = base64.b64encode(pdf_bytes).decode()

        prompt = """Analyze this PDF page. If there's a table, return JSON:
{"rows": <int>, "cols": <int>, "headers": [<list of header texts>]}
If no table, return {"rows": 0, "cols": 0}. Return ONLY the JSON."""

        resp = httpx.post(
            "http://localhost:4001/v1/chat/completions",
            headers={"Authorization": "Bearer sk-dev-proxy-123"},
            json={
                "model": "text-gemini",  # Gemini supports PDF inlineData
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"inlineData": {"mimeType": "application/pdf", "data": pdf_b64}},
                ]}],
                "temperature": 0,
            },
            timeout=60.0,
        )

        latency = (time.perf_counter() - start) * 1000
        content = resp.json()["choices"][0]["message"]["content"]

        # Parse JSON from response
        import re
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            return TableResult(
                method="vlm_pdf",
                rows=data.get("rows", 0),
                cols=data.get("cols", 0),
                latency_ms=latency,
            )
        return TableResult(method="vlm_pdf", error="no JSON in response")

    except Exception as e:
        return TableResult(method="vlm_pdf", error=str(e))


def extract_vlm_png(pdf_path: str, page_num: int) -> TableResult:
    """Extract table by sending PNG render to VLM via scillm."""
    import time
    start = time.perf_counter()

    try:
        # Render page to PNG
        doc = fitz.open(pdf_path)
        page = doc[page_num - 1]
        pix = page.get_pixmap(dpi=150)

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            pix.save(f.name)
            png_path = f.name

        prompt = """Analyze this image of a PDF page. If there's a table, return JSON:
{"rows": <int>, "cols": <int>, "headers": [<list of header texts>]}
If no table, return {"rows": 0, "cols": 0}. Return ONLY the JSON."""

        resp = httpx.post(
            "http://localhost:4001/v1/chat/completions",
            headers={"Authorization": "Bearer sk-dev-proxy-123"},
            json={
                "model": "vlm",
                "messages": prompt,
                "file_path": png_path,
                "temperature": 0,
            },
            timeout=60.0,
        )

        latency = (time.perf_counter() - start) * 1000
        content = resp.json()["choices"][0]["message"]["content"]

        # Parse JSON from response
        import re
        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            return TableResult(
                method="vlm_png",
                rows=data.get("rows", 0),
                cols=data.get("cols", 0),
                latency_ms=latency,
            )
        return TableResult(method="vlm_png", error="no JSON in response")

    except Exception as e:
        return TableResult(method="vlm_png", error=str(e))


def benchmark_page(pdf_path: str, page_num: int) -> PageBenchmark:
    """Run all 4 extraction methods on a single page."""
    page_id = f"{Path(pdf_path).name}:{page_num}"
    benchmark = PageBenchmark(page_id=page_id)

    # Run all extractors
    benchmark.results["pdf_oxide"] = extract_pdf_oxide(pdf_path, page_num)
    benchmark.results["camelot"] = extract_camelot(pdf_path, page_num)
    benchmark.results["vlm_pdf"] = extract_vlm_pdf(pdf_path, page_num)
    benchmark.results["vlm_png"] = extract_vlm_png(pdf_path, page_num)

    # Run integrity checks on DataFrames
    for method, result in benchmark.results.items():
        if result.df is not None:
            benchmark.integrity[method] = check_table_integrity(result.df)

    # Check agreement (all methods agree on rows×cols)
    shapes = []
    for r in benchmark.results.values():
        if r.rows is not None and r.cols is not None:
            shapes.append((r.rows, r.cols))
    benchmark.agreement = len(set(shapes)) == 1 and len(shapes) == 4

    return benchmark


def print_report(benchmarks: list[PageBenchmark]) -> None:
    """Print benchmark report."""
    print("=" * 80)
    print("TABLE EXTRACTION BENCHMARK REPORT")
    print(f"Pages tested: {len(benchmarks)}")
    print("=" * 80)
    print()

    # Per-page results
    print("PER-PAGE RESULTS")
    print("-" * 80)
    print(f"{'Page':<20} | {'pdf_oxide':^10} | {'Camelot':^10} | {'VLM PDF':^10} | {'VLM PNG':^10} | Agree")
    print("-" * 80)

    for b in benchmarks:
        def fmt(r: TableResult) -> str:
            if r.error:
                return "ERR"
            if r.rows is None:
                return "?"
            return f"{r.rows}x{r.cols}"

        row = [
            f"{b.page_id:<20}",
            f"{fmt(b.results.get('pdf_oxide', TableResult('?'))):^10}",
            f"{fmt(b.results.get('camelot', TableResult('?'))):^10}",
            f"{fmt(b.results.get('vlm_pdf', TableResult('?'))):^10}",
            f"{fmt(b.results.get('vlm_png', TableResult('?'))):^10}",
            "Y" if b.agreement else "N",
        ]
        print(" | ".join(row))

    print("-" * 80)
    print()

    # Agreement matrix
    methods = ["pdf_oxide", "camelot", "vlm_pdf", "vlm_png"]
    print("AGREEMENT MATRIX (% pages where methods agree on rows x cols)")
    print("-" * 80)
    header = f"{'':^12}" + " | ".join(f"{m:^10}" for m in methods)
    print(header)
    print("-" * 80)

    for m1 in methods:
        row_parts = [f"{m1:^12}"]
        for m2 in methods:
            if m1 == m2:
                row_parts.append(f"{'-':^10}")
            else:
                agree = 0
                total = 0
                for b in benchmarks:
                    r1 = b.results.get(m1)
                    r2 = b.results.get(m2)
                    if r1 and r2 and r1.rows is not None and r2.rows is not None:
                        total += 1
                        if r1.rows == r2.rows and r1.cols == r2.cols:
                            agree += 1
                pct = (agree / total * 100) if total > 0 else 0
                row_parts.append(f"{pct:^10.0f}%")
        print(" | ".join(row_parts))

    print("-" * 80)
    print()

    # Integrity summary
    print("PANDAS INTEGRITY SUMMARY")
    print("-" * 80)
    valid_count = {"camelot": 0}
    total_count = {"camelot": 0}
    for b in benchmarks:
        for method, check in b.integrity.items():
            if method not in valid_count:
                valid_count[method] = 0
                total_count[method] = 0
            total_count[method] += 1
            if check.valid:
                valid_count[method] += 1

    for method in valid_count:
        total = total_count[method]
        valid = valid_count[method]
        pct = (valid / total * 100) if total > 0 else 0
        print(f"{method}: {valid}/{total} valid ({pct:.0f}%)")

    print("=" * 80)


def find_table_pages(pdf_paths: list[str]) -> list[tuple[str, int]]:
    """Find all pages with tables across PDFs."""
    pages = []
    for pdf_path in pdf_paths:
        try:
            profile = profile_for_cloning(pdf_path)
            for ts in profile.get("table_shapes", []):
                pages.append((pdf_path, ts["page"] + 1))  # 1-indexed
        except Exception:
            pass
    return pages


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Benchmark table extraction methods")
    parser.add_argument("--pdf", nargs="+", help="PDF files to test")
    parser.add_argument("--limit", type=int, default=30, help="Max pages to test")
    parser.add_argument("--output", help="Output JSON path")
    args = parser.parse_args()

    if args.pdf:
        pdf_paths = args.pdf
    else:
        # Default test PDFs
        pdf_paths = ["/home/graham/workspace/experiments/pdf_oxide/tests/fixtures/1.pdf"]

    # Find table pages
    table_pages = find_table_pages(pdf_paths)
    print(f"Found {len(table_pages)} pages with tables")

    # Limit pages
    if len(table_pages) > args.limit:
        table_pages = table_pages[:args.limit]
        print(f"Testing {args.limit} pages")

    # Run benchmarks
    benchmarks = []
    for pdf_path, page_num in table_pages:
        print(f"Benchmarking {Path(pdf_path).name}:{page_num}...")
        b = benchmark_page(pdf_path, page_num)
        benchmarks.append(b)

    # Print report
    print_report(benchmarks)

    # Save JSON if requested
    if args.output:
        output_data = []
        for b in benchmarks:
            page_data = {"page_id": b.page_id, "agreement": b.agreement, "results": {}}
            for method, r in b.results.items():
                page_data["results"][method] = {
                    "rows": r.rows,
                    "cols": r.cols,
                    "error": r.error,
                    "latency_ms": r.latency_ms,
                }
            output_data.append(page_data)
        Path(args.output).write_text(json.dumps(output_data, indent=2))
        print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
