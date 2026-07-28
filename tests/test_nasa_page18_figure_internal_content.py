import contextlib
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
NASA_PDF = Path("/mnt/storage12tb/extractor_corpus/engineering/12 NASA_SP-2016-6105 Rev 2.pdf")
PAGE_INDEX = 17

INTERNAL_LABELS = [
    "Project Management",
    "Project Management Activities",
    "Systems Engineering System Design Processes",
    "Product Realization Processes",
    "Technical Management Processes",
    "PP&C",
    "Common Areas",
    "Stakeholder Expectations Definition",
    "Resource Management",
    "Setting up Project Team",
]


def _plain(text):
    return " ".join(str(text or "").split())


def _require_nasa_pdf():
    if not NASA_PDF.exists():
        pytest.skip(f"NASA source PDF not present: {NASA_PDF}")


def _extract_snapshot_page_18():
    _require_nasa_pdf()
    script_path = str(REPO / "scripts/pdf_lab")
    sys.path.insert(0, script_path)
    try:
        import snapshot_current_extraction as snapshot

        return snapshot._extract_page(NASA_PDF, PAGE_INDEX, None, "release")
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(script_path)


def _bbox_contains(outer, inner, eps=0.002):
    return (
        len(outer) == 4
        and len(inner) == 4
        and inner[0] >= outer[0] - eps
        and inner[1] >= outer[1] - eps
        and inner[2] <= outer[2] + eps
        and inner[3] <= outer[3] + eps
    )


def test_nasa_page_18_figure_internal_labels_are_absorbed_by_extract_document():
    _require_nasa_pdf()
    import pdf_oxide

    doc = pdf_oxide.PdfDocument(str(NASA_PDF))
    extraction = doc.extract_document()
    page = extraction["pages"][PAGE_INDEX]
    page_text = _plain(
        "\n".join(
            str(block.get("text") or "")
            for block in page.get("blocks") or []
            if block.get("block_type") != "Caption"
        )
    )

    figures = [
        fig
        for fig in extraction.get("figures") or []
        if fig.get("page") == PAGE_INDEX
        and fig.get("caption") == "Figure 2.0-1 SE in Context of Overall Project Management"
    ]
    assert len(figures) == 1
    figure_text = _plain(
        "\n".join(
            str(block.get("text") or "")
            for block in figures[0].get("content_blocks") or []
        )
    )

    missing = [label for label in INTERNAL_LABELS if label not in figure_text]
    leaked = [label for label in INTERNAL_LABELS if label in page_text]
    assert missing == []
    assert leaked == []


def test_nasa_page_18_pdf_lab_snapshot_exposes_one_figure_with_no_orphaned_internal_labels():
    page = _extract_snapshot_page_18()
    blocks = page.get("blocks") or []
    figures = [
        block
        for block in blocks
        if block.get("type") == "figure"
        and block.get("caption") == "Figure 2.0-1 SE in Context of Overall Project Management"
    ]
    assert len(figures) == 1

    figure = figures[0]
    figure_text = _plain(figure.get("text"))
    missing = [label for label in INTERNAL_LABELS if label not in figure_text]
    assert missing == []

    leaked = []
    for block in blocks:
        if block.get("type") == "figure":
            continue
        if block.get("source_type") == "Caption":
            continue
        text = _plain(block.get("text"))
        if any(label in text for label in INTERNAL_LABELS):
            leaked.append(
                {
                    "id": block.get("id"),
                    "type": block.get("type"),
                    "source_type": block.get("source_type"),
                    "text": " ".join(text.split())[:120],
                    "bbox": block.get("bbox"),
                }
            )
        elif _bbox_contains(figure.get("bbox") or [], block.get("bbox") or []):
            leaked.append(
                {
                    "id": block.get("id"),
                    "type": block.get("type"),
                    "source_type": block.get("source_type"),
                    "text": " ".join(text.split())[:120],
                    "bbox": block.get("bbox"),
                }
            )
    assert leaked == []
