"""Debug page coordinate system for a PDF."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pdf_oxide
import fitz

pdf_path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/storage12tb/extractor_corpus/ietf/rfc9136.pdf"
page_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0

# PyMuPDF spans for comparison
mu_doc = fitz.open(pdf_path)
mu_page = mu_doc[page_idx]
print(f"=== PyMuPDF Page {page_idx} ===")
print(f"MediaBox: {mu_page.mediabox}")
print(f"CropBox: {mu_page.cropbox}")
print(f"Rect: {mu_page.rect}")
print(f"Rotation: {mu_page.rotation}")

# Get PyMuPDF spans with positions
blocks = mu_page.get_text("dict")["blocks"]
print(f"\nPyMuPDF spans (first 20):")
for block in blocks[:5]:
    if "lines" in block:
        for line in block["lines"][:5]:
            for span in line["spans"][:3]:
                text = span["text"][:50]
                bbox = span["bbox"]
                print(f"  Y={bbox[1]:7.1f} X={bbox[0]:7.1f} W={bbox[2]-bbox[0]:6.1f} FS={span['size']:5.1f} {text}")

# pdf_oxide spans
print(f"\n=== pdf_oxide spans (first 20) ===")
doc = pdf_oxide.PdfDocument(pdf_path)
dims = doc.page_dimensions(page_idx)
print(f"Page dimensions: {dims}")
spans = doc.extract_spans(page_idx)
for s in spans[:20]:
    x, y, w, h = s.bbox
    text = s.text[:50].replace('\n', '\\n')
    print(f"  Y={y:7.1f} X={x:7.1f} W={w:6.1f} H={h:5.1f} FS={s.font_size:5.1f} {text}")
