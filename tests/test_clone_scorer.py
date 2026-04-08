"""Tests for clone_scorer — structural comparison of original vs synthetic PDFs."""
import os
import tempfile
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pdf_oxide.clone_scorer import score_clone


def _make_simple_pdf(path: str, text: str = "Hello World") -> str:
    """Create a minimal PDF with the given text."""
    c = canvas.Canvas(path, pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, text)
    c.save()
    return path


def test_identical_pdfs_score_high():
    """Two identical PDFs should score >= 0.9."""
    with tempfile.TemporaryDirectory() as td:
        pdf_a = _make_simple_pdf(os.path.join(td, "a.pdf"), "Hello World Test")
        pdf_b = _make_simple_pdf(os.path.join(td, "b.pdf"), "Hello World Test")
        result = score_clone(pdf_a, pdf_b)
        assert result["overall"] >= 0.9, f"Expected >= 0.9, got {result['overall']}"
        assert result["pass"] is True
        assert result["text_similarity"] >= 0.9


def test_different_text_scores_low():
    """PDFs with completely different text should score lower."""
    with tempfile.TemporaryDirectory() as td:
        pdf_a = _make_simple_pdf(os.path.join(td, "a.pdf"), "Access Control Requirements")
        pdf_b = _make_simple_pdf(os.path.join(td, "b.pdf"), "Completely Different Content Here")
        result = score_clone(pdf_a, pdf_b)
        assert result["text_similarity"] < 0.5
        assert result["overall"] < 0.9


def test_score_returns_all_fields():
    """Score result should contain all expected fields."""
    with tempfile.TemporaryDirectory() as td:
        pdf = _make_simple_pdf(os.path.join(td, "test.pdf"))
        result = score_clone(pdf, pdf)
        expected_keys = {
            "text_similarity", "block_count_ratio", "table_match",
            "section_recall", "overall", "delta_report", "pass",
        }
        assert expected_keys.issubset(result.keys()), f"Missing keys: {expected_keys - result.keys()}"


def test_delta_report_is_string():
    """Delta report should always be a non-empty string."""
    with tempfile.TemporaryDirectory() as td:
        pdf = _make_simple_pdf(os.path.join(td, "test.pdf"))
        result = score_clone(pdf, pdf)
        assert isinstance(result["delta_report"], str)
        assert len(result["delta_report"]) > 0
