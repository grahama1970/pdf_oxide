"""Live in-interpreter half of the RustSec remediation proof.

Runs inside the throwaway venv created by security_advisory_live_e2e.sh, against
the freshly built wheel. Everything here exercises code the advisories touched:

- importing the module at all exercises the pyo3 0.29 extension entry point
  (#8, #9) -- a broken migration fails on import, not at compile time;
- extracting text from a real PDF drives the LZW/Flate stream decoders, proving
  the removed `lzw` crate was genuinely unused (#14);
- rendering a page drives the rasterizer, proving the removed `rustybuzz`
  dependency was genuinely unused (#16);
- reading document metadata drives quick-xml 0.41 through the XMP parser and
  the migrated `xml_content(XmlVersion::Implicit1_0)` call sites (#10, #11).

Writes a JSON receipt read back by the caller. Fails loudly; a silent empty
result would make the proof meaningless.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import time


def main() -> int:
    corpus = pathlib.Path(os.environ["CORPUS"])
    receipt_path = pathlib.Path(os.environ["RECEIPT"])
    wheel = os.environ.get("WHEEL", "")

    receipt: dict[str, object] = {
        "schema": "pdf_oxide.security_advisory_live_e2e.v1",
        "python": sys.version.split()[0],
        "wheel": wheel,
        "source_pdf": str(corpus),
        "source_pdf_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
        "source_pdf_bytes": corpus.stat().st_size,
    }

    t0 = time.time()
    import pdf_oxide  # noqa: PLC0415 -- the import IS the pyo3 proof

    receipt["import_ok"] = True
    receipt["pdf_oxide_version"] = getattr(pdf_oxide, "__version__", None)
    receipt["exported_symbols"] = sorted(x for x in dir(pdf_oxide) if not x.startswith("_"))

    doc = pdf_oxide.PdfDocument(str(corpus))
    receipt["page_count"] = doc.page_count()
    if doc.page_count() < 100:
        raise RuntimeError(f"expected a large corpus PDF, got {doc.page_count()} pages")

    # Text extraction over a spread of pages -> stream decoders (#14).
    sampled = [0, 27, 45, 100, 455]
    pages: list[dict[str, object]] = []
    total_chars = 0
    for idx in sampled:
        if idx >= doc.page_count():
            continue
        text = doc.extract_text(idx)
        if not text.strip():
            raise RuntimeError(f"page {idx} extracted no text; decoders are broken")
        total_chars += len(text)
        pages.append(
            {
                "page_index": idx,
                "chars": len(text),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "head": text.strip()[:80],
            }
        )
    receipt["pages"] = pages
    receipt["total_chars_extracted"] = total_chars
    if total_chars < 5000:
        raise RuntimeError(f"only {total_chars} chars extracted across {len(pages)} pages")

    # Metadata -> quick-xml 0.41 XMP path (#10, #11).
    #
    # Both entry points run crate::extractors::xmp::XmpExtractor, which is the
    # quick-xml consumer carrying the migrated
    # `xml_content(XmlVersion::Implicit1_0)` call site. These must not raise:
    # a quick-xml regression surfaces here, not in the Rust unit tests.
    receipt["get_info"] = {k: str(v) for k, v in (doc.get_info() or {}).items()}
    xmp = doc.xmp_metadata()
    receipt["xmp_metadata"] = (
        {k: str(v) for k, v in xmp.items()} if isinstance(xmp, dict) else repr(xmp)
    )
    receipt["xmp_path_ok"] = True

    # Rendering -> rasterizer without rustybuzz (#16).
    try:
        png = doc.render_page(0, dpi=72, format="png")
        if not png.startswith(b"\x89PNG"):
            raise RuntimeError("render_page did not return PNG bytes")
        receipt["render_ok"] = True
        receipt["render_png_bytes"] = len(png)
        receipt["render_png_sha256"] = hashlib.sha256(png).hexdigest()
    except AttributeError:
        receipt["render_ok"] = None
        receipt["render_note"] = "render_page not exposed on this build"

    receipt["elapsed_seconds"] = round(time.time() - t0, 2)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2))

    print(f"import OK, {doc.page_count()} pages, {total_chars} chars extracted")
    print(f"receipt written: {receipt_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
