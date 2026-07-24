"""Deterministic page-image rendering and trace-reference validation.

The Python binding does not expose a stable serialization of one PDF page.
The renderer is byte-deterministic under the live re-render gate, so names hash
the source identity and render parameters together with the rendered PNG bytes.
This is stronger than the retrieval contract's canonical-input fallback:
renderer drift produces a new filename instead of silently reusing an old ref.

The rendered PNG hash is also propagated beside every ref.  A pre-existing
canonical name whose bytes differ is rejected with an atomic create-if-absent
operation, making corruption or concurrent writer disagreement visible.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
)

from .pdf_oxide import PdfDocument
from .pipeline_util import sha256_file


if TYPE_CHECKING:
    from .pipeline_types import PipelineResult


PAGE_IMAGE_SCHEMA = "pdf_oxide.page_image.v1"
PAGE_IMAGE_DPI = 150
PAGE_IMAGE_FORMAT = "png"
PAGE_IMAGE_DIRECTORY = "page_images"


def canonical_page_image_filename(
    pdf_sha256: str,
    page_index: int,
    image_bytes: bytes,
    *,
    dpi: int = PAGE_IMAGE_DPI,
    image_format: str = PAGE_IMAGE_FORMAT,
) -> str:
    """Return the content-identity filename for one rendered PDF page."""
    canonical_inputs = {
        "dpi": int(dpi),
        "format": image_format.lower(),
        "page_index": int(page_index),
        "pdf_sha256": pdf_sha256,
        "schema": PAGE_IMAGE_SCHEMA,
    }
    payload = json.dumps(
        canonical_inputs,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(payload)
    digest.update(b"\0")
    digest.update(image_bytes)
    return f"{digest.hexdigest()}.{image_format.lower()}"


def _write_once(path: Path, content: bytes) -> None:
    """Atomically create *path*, rejecting changed bytes at the same identity."""
    temporary_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        try:
            # Hard-link creation is atomic and cannot replace a path created by
            # a concurrent ingest between our render and publication.
            os.link(temporary_name, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise ValueError(
                    "page-image writer found different bytes for existing "
                    f"canonical identity {path.name}"
                ) from None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _pages_for_record(record: Mapping[str, Any]) -> List[int]:
    """Return the inclusive, zero-based page span represented by *record*."""
    if "page_start" in record or "page_end" in record:
        start = int(record.get("page_start", record.get("page", 0)))
        end = int(record.get("page_end", start))
        if end < start:
            raise ValueError(f"invalid page span {start}..{end}")
        return list(range(start, end + 1))

    pages = record.get("pages")
    if isinstance(pages, (list, tuple)) and pages:
        return sorted({int(page) for page in pages})

    if "page" not in record:
        raise ValueError(
            f"record {record.get('id', '<unknown>')} has no page or page span"
        )
    return [int(record["page"])]


def _page_filename_map(result: PipelineResult) -> Dict[int, str]:
    manifest = result.metadata.get("page_images", {})
    images = manifest.get("images", []) if isinstance(manifest, Mapping) else []
    return {
        int(image["page"]): str(image["filename"])
        for image in images
        if isinstance(image, Mapping)
        and "page" in image
        and "filename" in image
    }


def page_image_refs_for_page(
    result: PipelineResult,
    page_index: int,
) -> List[str]:
    """Resolve the rendered page-image reference for one zero-based page."""
    filename = _page_filename_map(result).get(int(page_index))
    return [filename] if filename is not None else []


def attach_page_image_refs(
    result: PipelineResult,
    page_filenames: Mapping[int, str],
    page_hashes: Mapping[int, str],
) -> None:
    """Attach complete page-image coverage to all extraction records."""
    collections: Iterable[List[MutableMapping[str, Any]]] = (
        result.sections,
        result.blocks,
        result.tables,
        result.figures,
        result.requirements,
    )
    for records in collections:
        for record in records:
            pages = _pages_for_record(record)
            missing = [page for page in pages if page not in page_filenames]
            if missing:
                raise ValueError(
                    f"record {record.get('id', '<unknown>')} references "
                    f"unrendered pages {missing}"
                )
            record["page_image_refs"] = [
                page_filenames[page] for page in pages
            ]
            record["page_image_sha256"] = {
                page_filenames[page]: page_hashes[page] for page in pages
            }


def render_page_images(
    pdf_path: str,
    result: PipelineResult,
    output_dir: Path,
    *,
    dpi: int = PAGE_IMAGE_DPI,
    image_format: str = PAGE_IMAGE_FORMAT,
) -> Path:
    """Render every page, attach refs, and return ``page_images/``."""
    if dpi <= 0:
        raise ValueError("page-image DPI must be positive")
    if image_format.lower() != PAGE_IMAGE_FORMAT:
        raise ValueError("pipeline page images currently require PNG format")

    pdf_sha256 = str(
        result.metadata.get("pdf_sha256") or sha256_file(pdf_path)
    )
    page_images_dir = Path(output_dir) / PAGE_IMAGE_DIRECTORY
    page_images_dir.mkdir(parents=True, exist_ok=True)

    document = PdfDocument(pdf_path)
    rendered_page_count = document.page_count()
    if rendered_page_count != result.page_count:
        raise ValueError(
            "page-image renderer page count does not match extraction: "
            f"{rendered_page_count} != {result.page_count}"
        )

    images = []
    page_filenames: Dict[int, str] = {}
    for page_index in range(result.page_count):
        image_bytes = bytes(
            document.render_page(
                page_index,
                dpi=dpi,
                format=image_format,
            )
        )
        byte_sha256 = hashlib.sha256(image_bytes).hexdigest()
        filename = canonical_page_image_filename(
            pdf_sha256,
            page_index,
            image_bytes,
            dpi=dpi,
            image_format=image_format,
        )
        _write_once(page_images_dir / filename, image_bytes)
        page_filenames[page_index] = filename
        images.append(
            {
                "page": page_index,
                "filename": filename,
                "byte_sha256": byte_sha256,
            }
        )

    result.metadata["page_images"] = {
        "schema": PAGE_IMAGE_SCHEMA,
        "directory": PAGE_IMAGE_DIRECTORY,
        "dpi": dpi,
        "format": image_format.lower(),
        "naming": (
            "sha256(canonical JSON of "
            "schema,pdf_sha256,page_index,dpi,format + NUL + PNG bytes)"
        ),
        "images": images,
    }
    attach_page_image_refs(
        result,
        page_filenames,
        {
            int(image["page"]): str(image["byte_sha256"])
            for image in images
        },
    )
    validate_page_image_refs(result, page_images_dir)
    return page_images_dir


def validate_page_image_ref_list(
    refs: Any,
    page_images_dir: Path,
    *,
    owner: str,
    sha256_by_ref: Optional[Mapping[str, str]] = None,
) -> None:
    """Assert that a reference list is safe, unique, and fully resolvable."""
    if not isinstance(refs, list) or not refs:
        raise ValueError(f"{owner} must have a non-empty page_image_refs list")
    if len(refs) != len(set(refs)):
        raise ValueError(f"{owner} has duplicate page_image_refs")
    for ref in refs:
        if (
            not isinstance(ref, str)
            or not ref
            or Path(ref).name != ref
        ):
            raise ValueError(f"{owner} has an invalid page-image filename")
        if not (page_images_dir / ref).is_file():
            raise ValueError(f"{owner} has dangling page-image ref {ref}")
        if sha256_by_ref is not None:
            expected_sha256 = sha256_by_ref.get(ref)
            if expected_sha256 is None:
                raise ValueError(f"{owner} has no SHA-256 for page-image ref {ref}")
            actual_sha256 = hashlib.sha256(
                (page_images_dir / ref).read_bytes()
            ).hexdigest()
            if actual_sha256 != expected_sha256:
                raise ValueError(
                    f"{owner} page-image SHA-256 mismatch for {ref}"
                )


def validate_page_image_refs(
    result: PipelineResult,
    page_images_dir: Path,
) -> None:
    """Assert zero dangling refs across sections and element records."""
    page_images_dir = Path(page_images_dir)
    manifest = result.metadata.get("page_images")
    if not isinstance(manifest, Mapping):
        raise ValueError("page-image manifest is missing")
    images = manifest.get("images")
    if not isinstance(images, list):
        raise ValueError("page-image manifest images must be a list")
    if len(images) != result.page_count:
        raise ValueError(
            "page-image manifest must contain exactly one image per PDF page"
        )
    page_filenames = _page_filename_map(result)
    if set(page_filenames) != set(range(result.page_count)):
        raise ValueError("page-image manifest does not cover every PDF page")
    sha256_by_ref = {
        str(image["filename"]): str(image["byte_sha256"])
        for image in images
        if isinstance(image, Mapping)
        and "filename" in image
        and "byte_sha256" in image
    }
    if len(sha256_by_ref) != result.page_count:
        raise ValueError("page-image manifest has missing or duplicate hashes")

    collections = (
        ("section", result.sections),
        ("block", result.blocks),
        ("table", result.tables),
        ("figure", result.figures),
        ("requirement", result.requirements),
    )
    for kind, records in collections:
        for record in records:
            expected_refs = [
                page_filenames[page] for page in _pages_for_record(record)
            ]
            if record.get("page_image_refs") != expected_refs:
                raise ValueError(
                    f"{kind} {record.get('id', '<unknown>')} has incomplete "
                    "page-image coverage"
                )
            if record.get("page_image_sha256") != {
                ref: sha256_by_ref[ref] for ref in expected_refs
            }:
                raise ValueError(
                    f"{kind} {record.get('id', '<unknown>')} has incorrect "
                    "page-image SHA-256 metadata"
                )
            validate_page_image_ref_list(
                record.get("page_image_refs"),
                page_images_dir,
                owner=f"{kind} {record.get('id', '<unknown>')}",
                sha256_by_ref=record.get("page_image_sha256"),
            )
