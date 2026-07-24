"""Pinned regression fixtures from the two rolled-back repair transactions.

Panel precondition (roundtable 2026-07-22, unanimous): every page that caused
a prior rollback is a CI fixture BEFORE any redesigned repair lands. Any
regression here is S0 by definition, regardless of the change's nominal class.

Runs against the installed pdf_oxide package (build the wheel and install it
into the test venv first). The source PDF is the SHA-pinned GS001 fixture.
"""

import hashlib
import json
import pathlib
import re
from collections import Counter, defaultdict

import pytest


pdf_oxide = pytest.importorskip("pdf_oxide")

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "rollback_fixtures.json").read_text()
)
PDF = pathlib.Path(
    "~/workspace/experiments/pdf_oxide-gs001/golden_slices/gs001_nist_page28/source/NIST_SP_800-53r5.pdf"
).expanduser()


@pytest.fixture(scope="module")
def extraction():
    from pdf_oxide.pipeline import extract_pdf
    from pdf_oxide.pipeline_types import PipelineConfig

    if not PDF.is_file():
        pytest.skip(f"pinned source PDF not present: {PDF}")
    actual_pdf_hash = hashlib.sha256(PDF.read_bytes()).hexdigest()
    assert actual_pdf_hash == FIXTURES["pdf_sha256"], (
        f"source PDF hash mismatch: expected {FIXTURES['pdf_sha256']}, "
        f"got {actual_pdf_hash}"
    )
    result = extract_pdf(str(PDF), PipelineConfig(features=[], sync_to_arango=False))
    return result.to_dict()


@pytest.fixture(scope="module")
def blocks_by_page(extraction):
    blocks = defaultdict(list)
    for block in extraction["blocks"]:
        blocks.setdefault(block["page"], []).append(block)
    return blocks


def _norm_s1(text):
    """Match the S1 determinism canonicalizer exactly."""
    return re.sub(r"\s+", "", text or "")


def _canonicalize_s1(extraction):
    """Return S1 page records plus whole-document semantic type totals."""
    pages = defaultdict(list)
    type_counts = Counter()

    for block in extraction["blocks"]:
        pages[block["page"]].append(
            ("B", block["type"], _norm_s1(block["text"]))
        )
        type_counts[block["type"]] += 1

    for table in extraction["tables"]:
        cells = _norm_s1(
            " ".join(
                str(cell)
                for row in table.get("data", [])
                for cell in row
            )
        )
        pages[table["page"]].append(
            ("T", f"{table['rows']}x{table['cols']}", cells)
        )
        type_counts["Table"] += 1

    for figure in extraction["figures"]:
        content = _norm_s1(
            "".join(
                block.get("text", "")
                for block in (figure.get("content_blocks") or [])
            )
        )
        pages[figure["page"]].append(("F", "", content))
        type_counts["Figure"] += 1

    return {page: sorted(records) for page, records in pages.items()}, type_counts


@pytest.fixture(scope="module")
def canonical_output(extraction):
    return _canonicalize_s1(extraction)


def _page_hash(records):
    return hashlib.sha256(json.dumps(records).encode()).hexdigest()


def _assert_page_hash(page, records, expected_hash):
    actual_hash = _page_hash(records)
    assert actual_hash == expected_hash, (
        f"HASH-PINNED DRIFT: page {page} expected {expected_hash}, "
        f"got {actual_hash}"
    )


@pytest.mark.parametrize(
    "fx",
    FIXTURES["fixtures"],
    ids=lambda fx: f"p{fx['page']}:{fx['text_prefix'][:12]}",
)
def test_rollback_fixture_holds(blocks_by_page, fx):
    candidates = [
        b
        for b in blocks_by_page.get(fx["page"], [])
        if (b.get("text") or "").strip().startswith(fx["text_prefix"])
    ]
    assert candidates, f"fixture text vanished from page {fx['page']}: {fx['text_prefix']!r}"
    types = {b["type"] for b in candidates}
    assert fx["expected_type"] in types, (
        f"S0 REGRESSION ({fx['origin']}): page {fx['page']} "
        f"{fx['text_prefix']!r} expected {fx['expected_type']}, got {types}"
    )


# Assertion-pinned fixtures can miss unrelated drift on the same page. These
# S3-parity-derived full-page hashes make drift detection total: any canonical
# block, table-cell, or figure-content change on a pinned page fails.
@pytest.mark.parametrize(
    ("page", "expected_hash"),
    sorted(
        (
            int(page),
            expected_hash,
        )
        for page, expected_hash in FIXTURES["hash_pinned"]["page_hashes"].items()
    ),
    ids=lambda value: f"p{value}" if isinstance(value, int) else None,
)
def test_hash_pinned_page_holds(canonical_output, page, expected_hash):
    pages, _ = canonical_output
    _assert_page_hash(page, pages.get(page, []), expected_hash)


def test_hash_pinned_full_document_type_counts(canonical_output):
    _, type_counts = canonical_output
    assert dict(sorted(type_counts.items())) == FIXTURES["hash_pinned"][
        "full_document_type_counts"
    ]


def test_hash_pin_rejects_perturbed_expected_hash(canonical_output):
    """Negative control: one wrong nibble must trip the real hash assertion."""
    pages, _ = canonical_output
    page = min(int(page) for page in FIXTURES["hash_pinned"]["page_hashes"])
    actual_hash = _page_hash(pages.get(page, []))
    perturbed_hash = ("0" if actual_hash[0] != "0" else "1") + actual_hash[1:]

    with pytest.raises(AssertionError, match=rf"HASH-PINNED DRIFT: page {page}"):
        _assert_page_hash(page, pages.get(page, []), perturbed_hash)
