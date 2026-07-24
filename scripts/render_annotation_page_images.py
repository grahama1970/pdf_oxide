#!/usr/bin/env python3
"""Mount annotation calls and render only their referenced PDF pages.

Run with the repository wheel environment:
    .venv/bin/python scripts/render_annotation_page_images.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
from pathlib import Path
from typing import Any

from pdf_oxide import PdfDocument
from pdf_oxide.pipeline_page_images import canonical_page_image_filename


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CALLS_ROOT = Path(
    "/home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls"
)
DEFAULT_ARTIFACTS_ROOT = REPO_ROOT / "artifacts" / "pdf-lab"
DEFAULT_RECEIPT = REPO_ROOT / "artifacts" / "ux_competition" / "round4" / "page-image-generation.json"
DPI = 96

SOURCE_PDFS = {
    "NIST_SP_800-53r5": Path(
        "/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf"
    ),
    "NIST.SP.800-53Ar5": Path(
        "/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53Ar5.pdf"
    ),
    "NASA_SP-2016-6105": Path(
        "/mnt/storage12tb/extractor_corpus/engineering/12 NASA_SP-2016-6105 Rev 2.pdf"
    ),
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_dimensions(value: bytes) -> tuple[int, int]:
    if value[:8] != b"\x89PNG\r\n\x1a\n" or value[12:16] != b"IHDR":
        raise ValueError("engine renderer did not return a PNG")
    return struct.unpack(">II", value[16:24])


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def mount_calls(source_root: Path, artifacts_root: Path) -> dict[str, dict[str, Any]]:
    mounted: dict[str, dict[str, Any]] = {}
    for source in sorted(source_root.glob("*/annotation_call.json")):
        call = json.loads(source.read_text(encoding="utf-8"))
        if call.get("schema") != "pdf_oxide.annotation_call.v1":
            raise ValueError(f"unsupported annotation call: {source}")
        doc = source.parent.name
        destination = artifacts_root / "annotation-calls" / doc / "annotation_call.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        mounted[doc] = call
    return mounted


def render_document(
    doc: str,
    call: dict[str, Any],
    pdf_path: Path,
    artifacts_root: Path,
) -> dict[str, Any]:
    expected_pdf_sha = str(call["pdf_sha256"])
    actual_pdf_sha = sha256_file(pdf_path)
    if actual_pdf_sha != expected_pdf_sha:
        raise ValueError(
            f"{doc} source PDF hash mismatch: {actual_pdf_sha} != {expected_pdf_sha}"
        )

    pages = sorted({int(item["page"]) for item in call["items"]})
    document = PdfDocument(str(pdf_path))
    if pages and pages[-1] >= document.page_count():
        raise ValueError(f"{doc} annotation page exceeds the {document.page_count()}-page PDF")

    doc_root = artifacts_root / "annotation-calls" / doc
    image_root = doc_root / "page_images"
    image_root.mkdir(parents=True, exist_ok=True)
    index_rows: list[dict[str, Any]] = []
    for page in pages:
        image_bytes = bytes(document.render_page(page, dpi=DPI, format="png"))
        width, height = png_dimensions(image_bytes)
        filename = canonical_page_image_filename(
            actual_pdf_sha,
            page,
            image_bytes,
            dpi=DPI,
            image_format="png",
        )
        image_path = image_root / filename
        if image_path.exists():
            if image_path.read_bytes() != image_bytes:
                raise ValueError(f"content-addressed page image changed: {image_path}")
        else:
            image_path.write_bytes(image_bytes)
        index_rows.append(
            {
                "doc": doc,
                "page": page,
                "pdf_sha256": actual_pdf_sha,
                "page_image_refs": [
                    {
                        "sha256": filename.removesuffix(".png"),
                        "byte_sha256": sha256_bytes(image_bytes),
                        "href": f"page_images/{filename}",
                        "mime_type": "image/png",
                        "page": page,
                        "width": width,
                        "height": height,
                        "pdf_sha256": actual_pdf_sha,
                    }
                ],
            }
        )

    index_path = doc_root / "page_images_v1.json"
    write_json(
        index_path,
        {
            "schema": "pdf_oxide.page_images_index.v1",
            "doc": doc,
            "pdf_sha256": actual_pdf_sha,
            "source_pdf": str(pdf_path),
            "source_pdf_sha256": actual_pdf_sha,
            "dpi": DPI,
            "format": "png",
            "href_base": "relative_to_index",
            "pages": index_rows,
        },
    )
    return {
        "doc": doc,
        "annotation_items": len(call["items"]),
        "referenced_pages": len(pages),
        "rendered_images": len(index_rows),
        "page_image_index": str(index_path),
        "source_pdf": str(pdf_path),
        "source_pdf_sha256": actual_pdf_sha,
    }


def write_working_examples(
    artifacts_root: Path,
    calls: dict[str, dict[str, Any]],
    rendered_docs: dict[str, dict[str, Any]],
) -> None:
    doc = "NIST_SP_800-53r5"
    if doc not in rendered_docs:
        return
    call = calls[doc]
    item = call["items"][0]
    index_path = artifacts_root / "annotation-calls" / doc / "page_images_v1.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    image_by_page = {row["page"]: row["page_image_refs"][0] for row in index["pages"]}
    image = image_by_page[int(item["page"])]
    pdf_width = float(image["width"]) * 72.0 / DPI
    pdf_height = float(image["height"]) * 72.0 / DPI
    x, y, width, height = map(float, item["bbox"])
    top = pdf_height - y - height
    normalized_bbox = [
        max(0.0, min(1.0, x / pdf_width)),
        max(0.0, min(1.0, top / pdf_height)),
        max(0.0, min(1.0, (x + width) / pdf_width)),
        max(0.0, min(1.0, (top + height) / pdf_height)),
    ]

    calibration_root = artifacts_root / "calibration"
    calibration_root.mkdir(parents=True, exist_ok=True)
    sample = {
        "doc": doc,
        "quintile": 0,
        "page": int(item["page"]),
        "bbox": normalized_bbox,
        "type": str(item.get("current_type") or item["kind"]),
        "confidence": float(item["confidence"]),
        "text": str(item.get("text_excerpt") or ""),
        "label": None,
    }
    (calibration_root / "sample_v1.jsonl").write_text(
        json.dumps(sample, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    write_json(
        calibration_root / "page_images_v1.json",
        {
            "schema": "pdf_oxide.page_images_index.v1",
            "href_base": "relative_to_index",
            "pages": [
                {
                    "doc": doc,
                    "page": sample["page"],
                    "pdf_sha256": call["pdf_sha256"],
                    "page_image_refs": [
                        {
                            **image,
                            "href": (
                                f"../annotation-calls/{doc}/page_images/"
                                f"{image['sha256']}.png"
                            ),
                        }
                    ],
                }
            ],
        },
    )
    write_json(
        artifacts_root / "round4_retrieval_result.json",
        {
            "answer": "The mounted annotation evidence identifies this extracted element.",
            "pdf_sha256": call["pdf_sha256"],
            "section_path": ["NIST SP 800-53 Revision 5", "Mounted annotation evidence"],
            "evidence": [
                {
                    "element_id": f"{doc}-page-{item['page']}-annotation-0",
                    "doc": doc,
                    "type": str(item.get("current_type") or item["kind"]),
                    "page": int(item["page"]),
                    "pdf_sha256": call["pdf_sha256"],
                    "section_path": [
                        "NIST SP 800-53 Revision 5",
                        "Mounted annotation evidence",
                    ],
                    "text": str(item.get("text_excerpt") or ""),
                }
            ],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls-root", type=Path, default=DEFAULT_CALLS_ROOT)
    parser.add_argument("--artifacts-root", type=Path, default=DEFAULT_ARTIFACTS_ROOT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument(
        "--documents",
        nargs="+",
        choices=sorted(SOURCE_PDFS),
        default=sorted(SOURCE_PDFS),
    )
    args = parser.parse_args()

    calls = mount_calls(args.calls_root, args.artifacts_root)
    rendered: dict[str, dict[str, Any]] = {}
    for doc in args.documents:
        if doc not in calls:
            raise ValueError(f"annotation call not found for {doc}")
        rendered[doc] = render_document(
            doc,
            calls[doc],
            SOURCE_PDFS[doc],
            args.artifacts_root,
        )
    write_working_examples(args.artifacts_root, calls, rendered)

    wheel_binary = Path(__import__("pdf_oxide.pdf_oxide", fromlist=[""]).__file__)
    receipt = {
        "schema": "pdf_oxide.round4_bounded_page_images.v1",
        "renderer": {
            "module": str(wheel_binary),
            "sha256": sha256_file(wheel_binary),
            "dpi": DPI,
            "format": "png",
        },
        "artifacts_root": str(args.artifacts_root),
        "mounted_annotation_calls": {
            doc: len(call["items"]) for doc, call in sorted(calls.items())
        },
        "documents": [rendered[doc] for doc in args.documents],
        "not_rendered": [
            {
                "doc": doc,
                "reason": "not requested in this bounded run",
                "referenced_pages": len({int(item["page"]) for item in calls[doc]["items"]}),
            }
            for doc in sorted(SOURCE_PDFS)
            if doc not in rendered and doc in calls
        ],
    }
    write_json(args.receipt, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
