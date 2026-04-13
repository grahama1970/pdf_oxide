"""Pipeline orchestration for clone."""
from __future__ import annotations
import argparse
import json
import hashlib
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Literal

from pypdf import PdfWriter, PdfReader

from pdf_oxide.clone_profiler import profile_for_cloning
from .analyzer import analyze_document
from .synthesizer import QidAllocator, TextSynthesizer, synthesize_page
from .renderer import render_page
from .schemas import DocumentManifest, PageManifest


def clone_pages(
    pdf_path: str,
    pages: list[int],
    seed: int = 42,
    domain: str = "government",
    corpus_type: Literal["gold", "source_inspired", "round_trip"] = "source_inspired",
) -> tuple[bytes, DocumentManifest]:
    """Clone specified pages from a PDF.

    Args:
        pdf_path: Source PDF path
        pages: 1-indexed page numbers to clone
        seed: Random seed for deterministic synthesis
        domain: Text domain for corpus
        corpus_type: Type of corpus being generated

    Returns:
        (merged_pdf_bytes, document_manifest)
    """
    doc_id = hashlib.md5(pdf_path.encode()).hexdigest()[:8]

    # Profile document for TOC, fonts, and table detection
    profile = profile_for_cloning(pdf_path)

    # Stage 1: Analyze pages with PyMuPDF + profiler table data
    proposals = analyze_document(pdf_path, pages, profile=profile)

    # Initialize synthesizer and QID allocator
    synthesizer = TextSynthesizer(seed=seed, domain=domain)
    qid_allocator = QidAllocator(doc_id=doc_id, seed=seed)

    # Process each page
    page_pdfs: list[bytes] = []
    page_manifests: list[PageManifest] = []

    for proposal in proposals:
        # Stage 2: Synthesize text + inject QIDs
        synth_blocks = synthesize_page(proposal, qid_allocator, synthesizer)

        # Stage 3: Render and emit manifest
        pdf_bytes, manifest = render_page(proposal, synth_blocks)

        page_pdfs.append(pdf_bytes)
        page_manifests.append(manifest)

    # Merge pages into single PDF
    writer = PdfWriter()
    for pdf_bytes in page_pdfs:
        reader = PdfReader(BytesIO(pdf_bytes))
        for page in reader.pages:
            writer.add_page(page)

    merged_buf = BytesIO()
    writer.write(merged_buf)
    merged_bytes = merged_buf.getvalue()

    # Build document manifest
    doc_manifest = DocumentManifest(
        source_path=pdf_path,
        seed=seed,
        generated_at=datetime.now(timezone.utc).isoformat(),
        corpus_type=corpus_type,
        pages=page_manifests,
    )

    return merged_bytes, doc_manifest


def main():
    parser = argparse.ArgumentParser(description="Clone PDF pages with synthetic text")
    parser.add_argument("--src", required=True, help="Source PDF path")
    parser.add_argument("--out", required=True, help="Output PDF path")
    parser.add_argument("--pages", default="1", help="Pages: '1,5,10' or '1-5' or 'all'")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--domain", default="government", help="Text domain")
    parser.add_argument("--save-manifest", action="store_true", help="Save manifest JSON")
    args = parser.parse_args()

    # Parse page spec
    import fitz
    doc = fitz.open(args.src)
    total_pages = doc.page_count
    doc.close()

    if args.pages == "all":
        pages = list(range(1, total_pages + 1))
    elif "-" in args.pages:
        start, end = args.pages.split("-")
        pages = list(range(int(start), int(end) + 1))
    else:
        pages = [int(p.strip()) for p in args.pages.split(",")]

    print(f"Cloning {len(pages)} pages from {args.src}")

    pdf_bytes, manifest = clone_pages(
        args.src,
        pages,
        seed=args.seed,
        domain=args.domain,
    )

    # Write output
    Path(args.out).write_bytes(pdf_bytes)
    print(f"Wrote {len(pdf_bytes)} bytes to {args.out}")
    print(f"  {manifest.total_blocks} blocks, {manifest.total_qids} QIDs")

    if args.save_manifest:
        manifest_path = args.out.replace(".pdf", ".manifest.json")
        Path(manifest_path).write_text(manifest.model_dump_json(indent=2))
        print(f"  Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
