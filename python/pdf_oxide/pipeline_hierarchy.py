"""Deterministic section hierarchy and traceability helpers.

This module does not classify headings.  It adapts the Rust classifier output
into the ordered tree and element-addressing contract used by downstream
pipeline stages.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .pipeline_util import md5


DocumentPosition = Tuple[int, int, float, float, int]
NUMBERED_SECTION = re.compile(r"^\s*((?:\d+\.)*\d+)\.?\s+\S")
LETTERED_SECTION = re.compile(r"^\s*([A-Z])\.\s+\S")


def _bbox(value: Any) -> Optional[List[float]]:
    """Return a JSON-safe four-number bbox, or ``None``."""
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [float(coordinate) for coordinate in value]
    except (TypeError, ValueError):
        return None


def _position(
    page: int,
    bbox: Any,
    fallback_order: int = 0,
) -> DocumentPosition:
    """Order by page, positioned headings first, then y-descending and x."""
    normalized = _bbox(bbox)
    if normalized is None:
        return (int(page), 1, 0.0, 0.0, int(fallback_order))
    x, y = normalized[0], normalized[1]
    return (int(page), 0, -y, x, int(fallback_order))


def provenance(
    pdf_sha256: str,
    page: int,
    bbox: Any,
) -> Dict[str, Any]:
    """Build the canonical source provenance payload."""
    return {
        "pdf_sha256": pdf_sha256,
        "page": int(page),
        "bbox": _bbox(bbox),
    }


def _hierarchy_level(title: str, classifier_level: int) -> int:
    """Prefer explicit section-number depth without changing classification."""
    numbered = NUMBERED_SECTION.match(title)
    if numbered:
        return numbered.group(1).count(".") + 1
    if LETTERED_SECTION.match(title):
        return 1
    return classifier_level


def build_section_tree(
    raw: Mapping[str, Any],
    pdf_sha256: str,
) -> List[Dict[str, Any]]:
    """Build ordered sections and parent/child links from Rust output.

    Heading membership and levels come exclusively from the Rust classifier.
    Document order is reconstructed from the matching heading block position:
    page first, then y-descending, with x and raw order as stable tie-breakers.
    Each parent is the nearest preceding section whose level is lower than the
    current level.  This naturally handles skipped levels.
    """
    headings: Dict[Tuple[int, str], List[Tuple[int, Any]]] = defaultdict(list)
    for page_data in raw.get("pages", []):
        page = int(page_data.get("page", 0))
        for block_order, block in enumerate(page_data.get("blocks", [])):
            if block.get("block_type") != "Title":
                continue
            headings[(page, str(block.get("text", "")).strip())].append(
                (block_order, block.get("bbox"))
            )

    heading_offsets: Dict[Tuple[int, str], int] = defaultdict(int)
    positioned: List[Tuple[DocumentPosition, Dict[str, Any]]] = []
    for raw_order, raw_section in enumerate(raw.get("sections", [])):
        title = str(raw_section.get("title", "")).strip()
        page = int(raw_section.get("page", raw_section.get("page_start", 0)))
        matches = headings.get((page, title), [])
        match_offset = heading_offsets[(page, title)]
        if match_offset < len(matches):
            block_order, heading_bbox = matches[match_offset]
            heading_offsets[(page, title)] += 1
        else:
            block_order = raw_order
            heading_bbox = raw_section.get("bbox")

        section_id = md5(f"sec_{title}_{page}")
        classifier_level = int(raw_section.get("level", 1))
        positioned.append(
            (
                _position(page, heading_bbox, block_order),
                {
                    "id": section_id,
                    "title": title,
                    "text": title,
                    "level": classifier_level,
                    "hierarchy_level": _hierarchy_level(
                        title, classifier_level
                    ),
                    "page_start": page,
                    "page_end": int(raw_section.get("page_end", page)),
                    "numbering": raw_section.get(
                        "numbering", raw_section.get("section_number")
                    ),
                    "bbox": _bbox(heading_bbox),
                    "provenance": provenance(pdf_sha256, page, heading_bbox),
                    "parent_id": None,
                    "children_ids": [],
                    "depth": 0,
                    "doc_order": 0,
                    "block_ids": [],
                    "section_path": "",
                },
            )
        )

    positioned.sort(key=lambda entry: entry[0])
    sections = [section for _, section in positioned]

    stack: List[Dict[str, Any]] = []
    for doc_order, section in enumerate(sections):
        section["doc_order"] = doc_order
        while stack and int(stack[-1]["hierarchy_level"]) >= int(
            section["hierarchy_level"]
        ):
            stack.pop()
        if stack:
            parent = stack[-1]
            section["parent_id"] = parent["id"]
            section["depth"] = int(parent["depth"]) + 1
            parent["children_ids"].append(section["id"])
            section["section_path"] = (
                f"{parent['section_path']} > {section['title']}"
            )
        else:
            section["section_path"] = section["title"]
        stack.append(section)

    return sections


def section_for_item(
    item: Mapping[str, Any],
    sections: Sequence[Mapping[str, Any]],
    page: int,
) -> Optional[str]:
    """Return the nearest preceding section in geometric document order."""
    if not sections:
        return None
    # An element at the exact heading coordinates belongs to that heading.
    # Use the largest tie order so the matched section boundary compares as
    # preceding even when it was not the first raw section on the page.
    item_position = _position(page, item.get("bbox"), 2**31 - 1)
    best: Optional[str] = None
    for fallback_order, section in enumerate(sections):
        section_page = int(section.get("page_start", 0))
        section_position = _position(
            section_page,
            section.get("bbox")
            or (section.get("provenance") or {}).get("bbox"),
            fallback_order,
        )
        if section_position <= item_position:
            best = str(section["id"])
        else:
            break
    return best


def section_path(
    section_id: Optional[str],
    sections: Sequence[Mapping[str, Any]],
) -> str:
    """Resolve the human-readable path for a section id."""
    if section_id is None:
        return ""
    for section in sections:
        if section.get("id") == section_id:
            return str(section.get("section_path", ""))
    return ""


def attach_block_ids(
    sections: Sequence[Dict[str, Any]],
    blocks: Iterable[Mapping[str, Any]],
) -> None:
    """Attach block ids in the already-deterministic block stream order."""
    by_id = {section["id"]: section for section in sections}
    for section in sections:
        section["block_ids"] = []
    for block in blocks:
        section = by_id.get(block.get("section_id"))
        if section is not None and block.get("id") is not None:
            section["block_ids"].append(block["id"])
