"""Pinned regression fixtures from the two rolled-back repair transactions.

Panel precondition (roundtable 2026-07-22, unanimous): every page that caused
a prior rollback is a CI fixture BEFORE any redesigned repair lands. Any
regression here is S0 by definition, regardless of the change's nominal class.

Runs against the installed pdf_oxide package (build the wheel and install it
into the test venv first). The source PDF is the SHA-pinned GS001 fixture.
"""

import json
import pathlib

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
    result = extract_pdf(str(PDF), PipelineConfig(features=[], sync_to_arango=False))
    blocks = {}
    for block in result.to_dict()["blocks"]:
        blocks.setdefault(block["page"], []).append(block)
    return blocks


@pytest.mark.parametrize(
    "fx",
    FIXTURES["fixtures"],
    ids=lambda fx: f"p{fx['page']}:{fx['text_prefix'][:12]}",
)
def test_rollback_fixture_holds(extraction, fx):
    candidates = [
        b
        for b in extraction.get(fx["page"], [])
        if (b.get("text") or "").strip().startswith(fx["text_prefix"])
    ]
    assert candidates, f"fixture text vanished from page {fx['page']}: {fx['text_prefix']!r}"
    types = {b["type"] for b in candidates}
    assert fx["expected_type"] in types, (
        f"S0 REGRESSION ({fx['origin']}): page {fx['page']} "
        f"{fx['text_prefix']!r} expected {fx['expected_type']}, got {types}"
    )
