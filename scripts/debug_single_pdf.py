"""Debug text extraction for a single PDF — compare oxide vs pymupdf output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pdf_oxide
import fitz
from difflib import SequenceMatcher

pdf_path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/storage12tb/extractor_corpus/ietf/rfc9136.pdf"
page = int(sys.argv[2]) if len(sys.argv) > 2 else 0

mu_doc = fitz.open(pdf_path)
ox_doc = pdf_oxide.PdfDocument(pdf_path)

mu_text = mu_doc[page].get_text()
ox_text = ox_doc.extract_text(page)

print(f"=== PyMuPDF (page {page}, {len(mu_text)} chars) ===")
print(mu_text[:2000])
print(f"\n=== pdf_oxide (page {page}, {len(ox_text)} chars) ===")
print(ox_text[:2000])

sim = SequenceMatcher(None, mu_text, ox_text).ratio()
print(f"\n=== Similarity: {sim:.3f} ===")

# Show first divergence
for i, (a, b) in enumerate(zip(mu_text, ox_text)):
    if a != b:
        print(f"\nFirst divergence at char {i}:")
        print(f"  PyMuPDF: ...{repr(mu_text[max(0,i-30):i+30])}...")
        print(f"  oxide:   ...{repr(ox_text[max(0,i-30):i+30])}...")
        break
