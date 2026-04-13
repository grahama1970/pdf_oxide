from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from pdf_oxide import clone_profiler
from pdf_oxide.clone_profiler import _normalize_outline_items
from pdf_oxide.clone_sampler import _flatten_outline


def _build_sample_pdf(pdf_path: Path) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(pdf_path), pagesize=LETTER)
    c.setFont("Times-Roman", 12)
    c.drawString(72, 740, "1 Introduction")
    c.save()

    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()
    writer.append(reader)
    writer.add_outline_item("1 Introduction", 0)
    with pdf_path.open("wb") as handle:
        writer.write(handle)


@pytest.fixture()
def fake_survey():
    return {
        "page_count": 1,
        "domain": "nist",
        "complexity_score": 1,
        "columns": 1,
        "has_toc": True,
        "toc_entry_count": 1,
        "table_pages": [],
        "figure_pages": [],
        "equation_pages": [],
        "page_details": [{"page": 0, "char_count": 100, "has_images": False, "is_blank": False}],
        "has_tables": False,
        "has_figures": False,
        "has_equations": False,
        "section_style": "numbered",
        "is_scanned": False,
        "table_shapes": [],
        "page_spanning_tables": [],
        "running_headers": [],
        "running_footers": [],
        "requirements_pages": [],
        "requirements_source": "text_regex",
        "list_pages": [],
        "footnote_pages": [],
        "callout_pages": [],
    }


class _DummyDoc:
    def __init__(self, path: str):
        self.path = path

    def get_toc(self):
        return []

    def get_section_map(self):
        return {}

    def extract_text(self, _page: int) -> str:
        return "INTRODUCTION OVERVIEW 1.2.3"

    def extract_spans(self, _page: int):
        return []

    def extract_paths(self, _page: int):
        return []


def test_profile_uses_pypdf_outline_and_fonts(tmp_path: Path, monkeypatch, fake_survey):
    pdf_path = tmp_path / "sample.pdf"
    _build_sample_pdf(pdf_path)

    monkeypatch.setattr(clone_profiler, "survey_document", lambda _doc, enrich_profile=True: fake_survey)
    monkeypatch.setattr(clone_profiler.pdf_oxide, "PdfDocument", lambda _path: _DummyDoc(_path))

    profile = clone_profiler.profile_for_cloning(str(pdf_path))

    assert profile["toc_pages"] == [1]
    assert profile["toc_sections"], "toc_sections should be populated from PyPDF outline"
    assert profile["font_detection_source"] == "pypdf"
    assert any("TimesNewRoman" in fam for fam in profile["font_families"])
    times_entry = profile["font_map"]["Times-Roman"]
    assert times_entry["pages"] == [1]
    assert times_entry["ttf_path"] and times_entry["ttf_path"].endswith("Times_New_Roman.ttf")


def test_profile_handles_missing_outline(tmp_path: Path, monkeypatch, fake_survey):
    pdf_path = tmp_path / "plain.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(pdf_path), pagesize=LETTER)
    c.setFont("Times-Roman", 12)
    c.drawString(72, 720, "Plain page")
    c.save()

    monkeypatch.setattr(clone_profiler, "survey_document", lambda _doc, enrich_profile=True: fake_survey)
    monkeypatch.setattr(clone_profiler.pdf_oxide, "PdfDocument", lambda _path: _DummyDoc(_path))

    profile = clone_profiler.profile_for_cloning(str(pdf_path))
    assert profile["toc_pages"] == []
    assert profile["toc_sections"] == []
    assert profile["font_map"], "font_map should still detect fonts from Resources"


def test_flatten_outline_compatible_with_sampler(tmp_path: Path, monkeypatch, fake_survey):
    pdf_path = tmp_path / "outline.pdf"
    _build_sample_pdf(pdf_path)

    monkeypatch.setattr(clone_profiler, "survey_document", lambda _doc, enrich_profile=True: fake_survey)
    monkeypatch.setattr(clone_profiler.pdf_oxide, "PdfDocument", lambda _path: _DummyDoc(_path))

    reader = PdfReader(str(pdf_path))
    outline_tree = _normalize_outline_items(reader)
    flat = _flatten_outline(outline_tree)
    assert flat, "flattened outline should not be empty"
    assert flat[0]["page"] == 1
