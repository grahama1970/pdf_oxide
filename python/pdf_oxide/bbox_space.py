"""Canonical bbox_space stamp for pdf_oxide artifacts (issue #20).

Every artifact that carries bounding boxes must declare which coordinate space
they are in. The canonical space, verified against a PyMuPDF word-geometry
oracle on 1512.03385v1 p4 (block top edge 593.4 vs oracle line 595.8 under the
bottom-left reading; the top-left reading misses by ~500pt), is:

    pdf_points_bottom_left_xywh

i.e. PDF user-space points, origin at the bottom-left of the page, boxes as
(x, y, width, height) with y the BOTTOM edge.

Contract: contracts/bbox_space_v1.schema.json.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

BBOX_SPACE_SCHEMA = "pdf_oxide.bbox_space.v1"
BBOX_SPACE = "pdf_points_bottom_left_xywh"


def bbox_space_stamp(
    page_width: float,
    page_height: float,
    *,
    crop_box: Optional[Sequence[float]] = None,
    rotation: int = 0,
) -> Dict[str, Any]:
    """Build a bbox_space object valid against bbox_space_v1.schema.json.

    ``crop_box`` defaults to the full page (0, 0, width, height) when the PDF
    declares none, which is the correct reading of an absent /CropBox.
    """
    if rotation not in (0, 90, 180, 270):
        raise ValueError(f"rotation must be one of 0/90/180/270, got {rotation!r}")
    box = list(crop_box) if crop_box is not None else [0.0, 0.0, float(page_width), float(page_height)]
    if len(box) != 4:
        raise ValueError(f"crop_box must have 4 entries, got {len(box)}")
    return {
        "schema": BBOX_SPACE_SCHEMA,
        "space": BBOX_SPACE,
        "crop_box": [float(v) for v in box],
        "rotation": int(rotation),
    }


def document_bbox_space_stamp(doc: Any, page_count: int) -> Dict[str, Any]:
    """Document-level stamp: the shared space plus per-page geometry.

    The space constant applies to every page; crop_box/rotation can vary per
    page, so the document stamp records them per page index under ``pages``.
    """
    pages: Dict[str, Dict[str, Any]] = {}
    for page in range(page_count):
        width, height = doc.page_dimensions(page)
        pages[str(page)] = bbox_space_stamp(
            width,
            height,
            crop_box=doc.page_crop_box(page),
            rotation=doc.page_rotation(page) or 0,
        )
    return {
        "schema": BBOX_SPACE_SCHEMA,
        "space": BBOX_SPACE,
        "pages": pages,
    }
