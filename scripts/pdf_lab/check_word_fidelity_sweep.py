"""Token-fidelity sweep: pdf_oxide vs PyMuPDF over the census pages.

For every page in the historical-findings census, every alphanumeric token
that PyMuPDF extracts must have the same multiplicity in pdf_oxide's
``extract_text`` output. This upgrades the old 4+-letter word-set guard to an
all-token Counter oracle, including short tokens and repeated tokens.

The report also records coarse region Counter diagnostics from positioned
extraction. Those diagnostics are intentionally reported separately because the
current positioned pdf_oxide APIs do not yet match the release ``extract_text``
path on all census pages.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pymupdf

import pdf_oxide


REPO = Path(__file__).resolve().parents[2]
PDF = "/home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/NIST_SP_800-53r5.pdf"
LEDGER = REPO / "artifacts/pdf_lab/census_regen_20260820/seed.json"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
DEFAULT_COLUMNS = 4
DEFAULT_ROWS = 4
DEFAULT_NEIGHBOR_RADIUS = 3
EXPECTED_BULK_CLOSURES = 111


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("Ɵ", "ti")


def tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_PATTERN.finditer(normalize_text(text))]


def token_counter(text: str) -> Counter[str]:
    return Counter(tokens(text))


def counter_delta(expected: Counter[str], actual: Counter[str]) -> dict[str, Any]:
    missing = expected - actual
    extra = actual - expected
    return {
        "missing_total": sum(missing.values()),
        "extra_total": sum(extra.values()),
        "missing": missing.most_common(20),
        "extra": extra.most_common(20),
    }


def counter_matches(expected: Counter[str], actual: Counter[str]) -> bool:
    return not (expected - actual) and not (actual - expected)


def contains_word_fidelity_evidence(value: Any) -> bool:
    if isinstance(value, str):
        return "Word-fidelity sweep" in value
    if isinstance(value, dict):
        return any(contains_word_fidelity_evidence(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_word_fidelity_evidence(item) for item in value)
    return False


def bin_id(
    center_x: float,
    center_y: float,
    page_width: float,
    page_height: float,
    columns: int,
    rows: int,
) -> str:
    col = max(0, min(columns - 1, int(center_x / (page_width / columns))))
    row = max(0, min(rows - 1, int(center_y / (page_height / rows))))
    return f"r{row}c{col}"


def pymupdf_region_counters(
    page: pymupdf.Page,
    *,
    columns: int,
    rows: int,
) -> dict[str, Counter[str]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)
    for x0, y0, x1, y1, text, *_ in page.get_text("words"):
        center_x = (float(x0) + float(x1)) / 2.0
        center_y = (float(y0) + float(y1)) / 2.0
        counters[bin_id(center_x, center_y, page_width, page_height, columns, rows)].update(
            tokens(text)
        )
    return dict(counters)


def pdf_oxide_region_counters(
    doc: pdf_oxide.PdfDocument,
    page_number: int,
    page: pymupdf.Page,
    *,
    columns: int,
    rows: int,
) -> dict[str, Counter[str]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    page_width = float(page.rect.width)
    page_height = float(page.rect.height)

    # extract_spans uses PDF bottom-left coordinates; PyMuPDF bins use top-left.
    for span in doc.extract_spans(page_number - 1):
        x, y, width, height = span.bbox
        center_y = page_height - (float(y) + float(height) / 2.0)
        normalized = normalize_text(span.text).lower()
        text_len = max(1, len(normalized))
        for match in TOKEN_PATTERN.finditer(normalized):
            token_center = (match.start() + match.end()) / (2.0 * text_len)
            center_x = float(x) + float(width) * token_center
            region = bin_id(center_x, center_y, page_width, page_height, columns, rows)
            counters[region][match.group(0)] += 1
    return dict(counters)


def compare_region_counters(
    expected: dict[str, Counter[str]],
    actual: dict[str, Counter[str]],
) -> dict[str, dict[str, Any]]:
    mismatches: dict[str, dict[str, Any]] = {}
    for key in sorted(set(expected) | set(actual)):
        if counter_matches(expected.get(key, Counter()), actual.get(key, Counter())):
            continue
        mismatches[key] = counter_delta(expected.get(key, Counter()), actual.get(key, Counter()))
    return mismatches


def parse_bin(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"r(\d+)c(\d+)", value)
    if not match:
        raise ValueError(f"invalid bin id: {value}")
    return int(match.group(1)), int(match.group(2))


def neighbor_counter_delta(
    expected: dict[str, Counter[str]],
    actual: dict[str, Counter[str]],
    radius: int,
) -> dict[str, Any]:
    actual_remaining = {key: Counter(value) for key, value in actual.items()}
    unresolved_expected: Counter[str] = Counter()

    for expected_bin, expected_counter in expected.items():
        expected_row, expected_col = parse_bin(expected_bin)
        candidate_bins = sorted(
            actual_remaining,
            key=lambda actual_bin: max(
                abs(parse_bin(actual_bin)[0] - expected_row),
                abs(parse_bin(actual_bin)[1] - expected_col),
            ),
        )
        for token, count in expected_counter.items():
            remaining = count
            for actual_bin in candidate_bins:
                actual_row, actual_col = parse_bin(actual_bin)
                distance = max(abs(actual_row - expected_row), abs(actual_col - expected_col))
                if distance > radius:
                    continue
                consumed = min(remaining, actual_remaining[actual_bin][token])
                if consumed:
                    actual_remaining[actual_bin][token] -= consumed
                    remaining -= consumed
                if remaining == 0:
                    break
            if remaining:
                unresolved_expected[token] += remaining

    unresolved_actual: Counter[str] = Counter()
    for counter in actual_remaining.values():
        unresolved_actual.update(+counter)

    return {
        "missing_total": sum(unresolved_expected.values()),
        "extra_total": sum(unresolved_actual.values()),
        "missing": unresolved_expected.most_common(20),
        "extra": unresolved_actual.most_common(20),
    }


def build_report(columns: int, rows: int, require_region_parity: bool) -> dict[str, Any]:
    ledger = json.loads(LEDGER.read_text())
    entries = ledger["entries"]
    pages = sorted({entry["page"] for entry in entries})
    bulk_entries = [entry for entry in entries if contains_word_fidelity_evidence(entry)]

    doc = pdf_oxide.PdfDocument(PDF)
    oracle = pymupdf.open(PDF)

    page_detail: dict[str, dict[str, Any]] = {}
    region_detail: dict[str, dict[str, Any]] = {}
    region_bin_mismatches = 0
    neighbor_detail: dict[str, dict[str, Any]] = {}

    for page_number in pages:
        page_index = page_number - 1
        expected_page = token_counter(oracle[page_index].get_text())
        actual_page = token_counter(doc.extract_text(page_index))
        if not counter_matches(expected_page, actual_page):
            page_detail[str(page_number)] = counter_delta(expected_page, actual_page)

        expected_regions = pymupdf_region_counters(oracle[page_index], columns=columns, rows=rows)
        actual_regions = pdf_oxide_region_counters(
            doc,
            page_number,
            oracle[page_index],
            columns=columns,
            rows=rows,
        )
        mismatched_regions = compare_region_counters(expected_regions, actual_regions)
        if mismatched_regions:
            region_detail[str(page_number)] = mismatched_regions
            region_bin_mismatches += len(mismatched_regions)

        neighbor_delta = neighbor_counter_delta(
            expected_regions,
            actual_regions,
            DEFAULT_NEIGHBOR_RADIUS,
        )
        if neighbor_delta["missing_total"] or neighbor_delta["extra_total"]:
            neighbor_detail[str(page_number)] = neighbor_delta

    page_counter_passed = not page_detail
    region_counter_passed = not region_detail
    region_neighbor_counter_passed = not neighbor_detail
    bulk_closure_count_passed = len(bulk_entries) == EXPECTED_BULK_CLOSURES
    passed = (
        page_counter_passed
        and bulk_closure_count_passed
        and (region_counter_passed or not require_region_parity)
    )

    return {
        "source_pdf": PDF,
        "ledger": str(LEDGER),
        "token_pattern": TOKEN_PATTERN.pattern,
        "normalization": "NFKC plus document U+019F-to-ti replacement",
        "pages_swept": len(pages),
        "bulk_closure_count": len(bulk_entries),
        "expected_bulk_closure_count": EXPECTED_BULK_CLOSURES,
        "bulk_closure_count_passed": bulk_closure_count_passed,
        "page_counter_source": {
            "pdf_oxide": "PdfDocument.extract_text(page_index)",
            "oracle": "PyMuPDF Page.get_text()",
        },
        "page_counter_mismatches": len(page_detail),
        "page_counter_passed": page_counter_passed,
        "page_detail": page_detail,
        "region_counter_source": {
            "pdf_oxide": "PdfDocument.extract_spans(page_index)",
            "oracle": "PyMuPDF Page.get_text('words')",
            "coordinate_note": "pdf_oxide span y is converted from PDF bottom-left to PyMuPDF top-left before binning",
        },
        "region_bins": {"columns": columns, "rows": rows},
        "region_counter_mismatches": len(region_detail),
        "region_bin_mismatches": region_bin_mismatches,
        "region_counter_passed": region_counter_passed,
        "region_neighbor_radius": DEFAULT_NEIGHBOR_RADIUS,
        "region_neighbor_counter_mismatches": len(neighbor_detail),
        "region_neighbor_missing_total": sum(
            item["missing_total"] for item in neighbor_detail.values()
        ),
        "region_neighbor_extra_total": sum(item["extra_total"] for item in neighbor_detail.values()),
        "region_neighbor_counter_passed": region_neighbor_counter_passed,
        "region_neighbor_detail": neighbor_detail,
        "region_detail": region_detail,
        "region_proof_boundary": (
            "diagnostic only unless require_region_parity is true; current positioned "
            "pdf_oxide APIs do not yet provide full per-region parity on this corpus"
        ),
        "require_region_parity": require_region_parity,
        "passed": passed,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional path to write the JSON report.")
    parser.add_argument("--columns", type=int, default=DEFAULT_COLUMNS)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument(
        "--require-region-parity",
        action="store_true",
        help="Fail the command unless coarse positioned region Counters also match.",
    )
    return parser.parse_args()


def stdout_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_pdf": report["source_pdf"],
        "ledger": report["ledger"],
        "token_pattern": report["token_pattern"],
        "normalization": report["normalization"],
        "pages_swept": report["pages_swept"],
        "bulk_closure_count": report["bulk_closure_count"],
        "expected_bulk_closure_count": report["expected_bulk_closure_count"],
        "bulk_closure_count_passed": report["bulk_closure_count_passed"],
        "page_counter_mismatches": report["page_counter_mismatches"],
        "page_counter_passed": report["page_counter_passed"],
        "region_bins": report["region_bins"],
        "region_counter_mismatches": report["region_counter_mismatches"],
        "region_bin_mismatches": report["region_bin_mismatches"],
        "region_counter_passed": report["region_counter_passed"],
        "region_neighbor_radius": report["region_neighbor_radius"],
        "region_neighbor_counter_mismatches": report["region_neighbor_counter_mismatches"],
        "region_neighbor_missing_total": report["region_neighbor_missing_total"],
        "region_neighbor_extra_total": report["region_neighbor_extra_total"],
        "region_neighbor_counter_passed": report["region_neighbor_counter_passed"],
        "region_proof_boundary": report["region_proof_boundary"],
        "require_region_parity": report["require_region_parity"],
        "passed": report["passed"],
    }


def main() -> int:
    args = parse_args()
    report = build_report(args.columns, args.rows, args.require_region_parity)
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    print(json.dumps(stdout_summary(report), indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
