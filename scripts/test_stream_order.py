"""Quick test: compare geometric sort vs content-stream order.

For each PDF, extracts text two ways:
1. Current: extract_text() with sort_spans_block_order (geometric)
2. Stream order: extract_spans() preserving sequence field order, assemble text manually

If stream order scores higher on SequenceMatcher vs PyMuPDF, the geometric
sort is hurting accuracy.
"""

import sys
import os
import random
from pathlib import Path
from difflib import SequenceMatcher

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pdf_oxide
import fitz  # PyMuPDF reference


def extract_text_stream_order(doc, page_idx):
    """Extract text preserving content stream order (sequence field).

    Only sort within same-Y lines by X position. Between lines,
    preserve the content stream sequence order.
    """
    spans = doc.extract_spans(page_idx)
    if not spans:
        return ""

    # Sort by sequence (content stream order) as primary
    # This is how the PDF author intended the text to be read
    spans.sort(key=lambda s: s.sequence)

    # Group into lines: spans with similar Y (within 2pt)
    lines = []
    current_line = [spans[0]]

    for span in spans[1:]:
        prev = current_line[-1]
        y_diff = abs(span.bbox[1] - prev.bbox[1])

        if y_diff <= 2.0:
            current_line.append(span)
        else:
            lines.append(current_line)
            current_line = [span]

    if current_line:
        lines.append(current_line)

    # Within each line, sort by X (left to right)
    for line in lines:
        line.sort(key=lambda s: s.bbox[0])

    # Assemble text
    text_parts = []
    for line in lines:
        line_text = ""
        for i, span in enumerate(line):
            if i > 0:
                prev = line[i-1]
                prev_end_x = prev.bbox[0] + prev.bbox[2]  # x + width
                gap = span.bbox[0] - prev_end_x
                fs = max(span.font_size, prev.font_size, 6.0)
                if gap > fs * 0.25:
                    line_text += " "
            line_text += span.text
        text_parts.append(line_text)

    return "\n".join(text_parts)


def compare_on_pdf(pdf_path):
    """Compare both methods on a single PDF, return (geometric_sim, stream_sim)."""
    try:
        # PyMuPDF reference
        mu_doc = fitz.open(str(pdf_path))
        if mu_doc.page_count == 0:
            return None

        # Only test first 3 pages for speed
        max_pages = min(mu_doc.page_count, 3)

        mu_text = ""
        for i in range(max_pages):
            mu_text += mu_doc[i].get_text()
        mu_doc.close()

        if not mu_text.strip():
            return None

        # pdf_oxide geometric sort (current)
        oxide_doc = pdf_oxide.PdfDocument(str(pdf_path))
        geo_text = ""
        for i in range(max_pages):
            geo_text += oxide_doc.extract_text(i)

        # pdf_oxide content stream order
        stream_text = ""
        for i in range(max_pages):
            stream_text += extract_text_stream_order(oxide_doc, i)

        # Compare
        geo_sim = SequenceMatcher(None, mu_text, geo_text).ratio()
        stream_sim = SequenceMatcher(None, mu_text, stream_text).ratio()

        return (geo_sim, stream_sim)
    except Exception as e:
        print(f"  Error: {e}")
        return None


def main():
    corpus_dir = Path("/mnt/storage12tb/extractor_corpus")

    # Get all PDFs
    all_pdfs = []
    for category in corpus_dir.iterdir():
        if category.is_dir():
            for pdf in category.glob("*.pdf"):
                all_pdfs.append(pdf)

    # Sample 30 for quick test
    random.seed(42)
    sample = random.sample(all_pdfs, min(30, len(all_pdfs)))

    geo_wins = 0
    stream_wins = 0
    ties = 0
    geo_total = 0.0
    stream_total = 0.0
    count = 0

    results = []

    for pdf_path in sample:
        category = pdf_path.parent.name
        name = pdf_path.name[:40]
        result = compare_on_pdf(pdf_path)

        if result is None:
            continue

        geo_sim, stream_sim = result
        count += 1
        geo_total += geo_sim
        stream_total += stream_sim

        diff = stream_sim - geo_sim
        marker = ""
        if diff > 0.02:
            stream_wins += 1
            marker = "<<< STREAM BETTER"
        elif diff < -0.02:
            geo_wins += 1
            marker = ">>> GEO BETTER"
        else:
            ties += 1

        results.append((diff, category, name, geo_sim, stream_sim))
        print(f"  [{category}] {name}: geo={geo_sim:.3f} stream={stream_sim:.3f} {marker}")

    print(f"\n{'='*70}")
    print(f"Results ({count} PDFs):")
    print(f"  Geometric avg: {geo_total/count:.4f} ({geo_total/count*100:.1f}%)")
    print(f"  Stream avg:    {stream_total/count:.4f} ({stream_total/count*100:.1f}%)")
    print(f"  Stream wins:   {stream_wins}")
    print(f"  Geo wins:      {geo_wins}")
    print(f"  Ties (<2%):    {ties}")

    # Show biggest improvements with stream order
    results.sort(reverse=True)
    print(f"\nBiggest stream-order improvements:")
    for diff, cat, name, geo, stream in results[:10]:
        print(f"  +{diff:+.3f}  [{cat}] {name}: {geo:.3f} -> {stream:.3f}")

    print(f"\nBiggest stream-order regressions:")
    for diff, cat, name, geo, stream in results[-5:]:
        print(f"  {diff:+.3f}  [{cat}] {name}: {geo:.3f} -> {stream:.3f}")


if __name__ == "__main__":
    main()
