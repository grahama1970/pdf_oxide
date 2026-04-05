"""Debug span Y coordinates for a PDF page."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import pdf_oxide

pdf_path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/storage12tb/extractor_corpus/ietf/rfc9136.pdf"
page = int(sys.argv[2]) if len(sys.argv) > 2 else 0

doc = pdf_oxide.PdfDocument(pdf_path)
spans = doc.extract_spans(page)

print(f"Page {page}: {len(spans)} spans")
print(f"{'Y':>8} {'X':>8} {'W':>6} {'FS':>5} Text")
print("-" * 80)
for s in spans[:50]:
    x, y, w, h = s.bbox
    text = s.text[:60].replace('\n', '\\n')
    print(f"{y:8.1f} {x:8.1f} {w:6.1f} {s.font_size:5.1f} {text}")
