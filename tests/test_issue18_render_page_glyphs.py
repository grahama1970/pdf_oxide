"""Regression test for ticket #18: render_page must not double-draw glyphs."""

from __future__ import annotations

import subprocess
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops

from pdf_oxide import PdfDocument


SOURCE_PDF = Path("/mnt/storage12tb/extractor_corpus/inbox/arxiv/1512.03385v1.pdf")
PAGE_INDEX = 4
DPI = 96
CAPTION_BAND = (0.25, 0.32)
CAPTION_LINE_X = (0.055, 0.527)
CAPTION_LINE_Y_IN_BAND = (0.38, 0.64)
PIXEL_DELTA = 32
MAX_DIFFERING_PIXEL_RATIO = 0.29


def _caption_band(image: Image.Image) -> Image.Image:
    rgb = image.convert("RGB")
    top = round(rgb.height * CAPTION_BAND[0])
    bottom = round(rgb.height * CAPTION_BAND[1])
    return rgb.crop((0, top, rgb.width, bottom))


def _primary_caption_line(caption_band: Image.Image) -> Image.Image:
    left = round(caption_band.width * CAPTION_LINE_X[0])
    right = round(caption_band.width * CAPTION_LINE_X[1])
    top = round(caption_band.height * CAPTION_LINE_Y_IN_BAND[0])
    bottom = round(caption_band.height * CAPTION_LINE_Y_IN_BAND[1])
    return caption_band.crop((left, top, right, bottom))


def test_render_page_caption_matches_pdftoppm_without_doubled_glyphs(tmp_path):
    assert SOURCE_PDF.is_file(), f"missing ticket fixture: {SOURCE_PDF}"

    document = PdfDocument(str(SOURCE_PDF))
    oxide_png = document.render_page(PAGE_INDEX, dpi=DPI, format="png")
    oxide = Image.open(BytesIO(oxide_png)).convert("RGB")

    poppler_prefix = tmp_path / "page-5"
    subprocess.run(
        [
            "pdftoppm",
            "-f",
            str(PAGE_INDEX + 1),
            "-l",
            str(PAGE_INDEX + 1),
            "-r",
            str(DPI),
            "-png",
            "-singlefile",
            str(SOURCE_PDF),
            str(poppler_prefix),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    poppler = Image.open(poppler_prefix.with_suffix(".png")).convert("RGB")

    assert oxide.size == poppler.size
    oxide_crop = _primary_caption_line(_caption_band(oxide))
    poppler_crop = _primary_caption_line(_caption_band(poppler))
    difference = ImageChops.difference(oxide_crop, poppler_crop).convert("L")
    differing_pixels = sum(pixel > PIXEL_DELTA for pixel in difference.tobytes())
    differing_ratio = differing_pixels / (difference.width * difference.height)

    assert differing_ratio <= MAX_DIFFERING_PIXEL_RATIO, (
        "caption-band render diverges from pdftoppm: "
        f"{differing_ratio:.6f} pixels differ by >{PIXEL_DELTA} "
        f"(limit {MAX_DIFFERING_PIXEL_RATIO:.6f}); doubled glyphs suspected"
    )
