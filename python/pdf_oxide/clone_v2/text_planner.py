"""Stage 2: Deterministic text planner — assign /create-text corpus chunks + QIDs.

Takes a PageBlueprint, assigns synthetic text to each element by content type,
assigns QIDs via QidAllocator, and produces a PagePlan ready for rendering.
"""
from __future__ import annotations

import hashlib
import sys

from loguru import logger

from pdf_oxide.clone_v2.qid import QidAllocator
from pdf_oxide.clone_v2.schemas import (
    ElementBlueprint,
    ElementPlan,
    FigureSpec,
    PageBlueprint,
    PagePlan,
    TableCellPlan,
    TablePlan,
)


# Map element types to /create-text content types
_TYPE_MAP = {
    "heading": "heading",
    "paragraph": "heading",  # fallback — prose not in all banks
    "requirement": "heading",
    "list": "heading",
    "footnote": "heading",
    "caption": "heading",
    "equation": "heading",
    "header": "heading",
    "footer": "heading",
}


def _load_create_text():
    """Load the /create-text corpus interface."""
    sys.path.insert(0, "/home/graham/.claude/skills/create-text")
    from create_text import create_text
    return create_text


def _chunk_key(doc_id: str, page_no: int, eid: str, seed: int) -> int:
    """Deterministic chunk selection key from element identity."""
    h = hashlib.md5(f"{doc_id}:{page_no}:{eid}:{seed}".encode()).hexdigest()
    return int(h[:8], 16)


def assign_synthetic_text(
    blueprint: PageBlueprint,
    seed: int,
    domain: str = "government",
) -> PagePlan:
    """Convert a PageBlueprint into a PagePlan with synthetic text + QIDs.

    For each element:
    1. Select a /create-text corpus chunk by content type
    2. Assign a QID via QidAllocator
    3. Build an ElementPlan with text + QID
    """
    create_text = _load_create_text()
    allocator = QidAllocator(
        doc_id=blueprint.document_id,
        page_number=blueprint.page_number,
    )

    # Pre-fetch corpus chunks by type
    chunk_cache: dict[str, list[dict]] = {}
    for el in blueprint.elements:
        ct = _TYPE_MAP.get(el.type, "heading")
        if ct not in chunk_cache:
            chunks = _fetch_chunks(create_text, ct, domain, count=50, seed=seed)
            chunk_cache[ct] = chunks

    elements: list[ElementPlan] = []

    for el in blueprint.iter_elements():
        if el.type == "table" and el.table:
            plan = _plan_table_element(el, allocator, chunk_cache, blueprint, seed, domain, create_text)
        elif el.type == "figure":
            plan = _plan_figure_element(el, allocator)
        else:
            plan = _plan_text_element(el, allocator, chunk_cache, blueprint, seed)
        elements.append(plan)

    page_plan = PagePlan(
        document_id=blueprint.document_id,
        page_number=blueprint.page_number,
        page_size=blueprint.page_size,
        elements=elements,
        qid_manifest=allocator.manifest(),
    )

    logger.info(
        f"Planned page {blueprint.page_number}: {len(elements)} elements, "
        f"{len(page_plan.qid_manifest)} QIDs"
    )
    return page_plan


def _fetch_chunks(
    create_text,
    content_type: str,
    domain: str,
    count: int,
    seed: int,
) -> list[dict]:
    """Fetch corpus chunks, with domain fallback."""
    for d in [domain, "government", "nist", "engineering", "other"]:
        try:
            chunks = create_text(content_type=content_type, domain=d, count=count, seed=seed)
            if chunks:
                return chunks
        except Exception:
            continue
    return []


def _select_chunk(
    chunks: list[dict],
    doc_id: str,
    page_no: int,
    eid: str,
    seed: int,
) -> str:
    """Deterministically select a chunk from the pool."""
    if not chunks:
        return f"[placeholder text for {eid}]"
    key = _chunk_key(doc_id, page_no, eid, seed)
    idx = key % len(chunks)
    return chunks[idx].get("text", "").strip()[:500]


def _plan_text_element(
    el: ElementBlueprint,
    allocator: QidAllocator,
    chunk_cache: dict[str, list[dict]],
    blueprint: PageBlueprint,
    seed: int,
) -> ElementPlan:
    """Plan a text element (heading, paragraph, list, etc.)."""
    ct = _TYPE_MAP.get(el.type, "heading")
    chunks = chunk_cache.get(ct, [])
    text = _select_chunk(chunks, blueprint.document_id, blueprint.page_number, el.eid, seed)
    qid, token, _ = allocator.assign(el.eid)

    return ElementPlan(
        blueprint_id=el.eid,
        type=el.type,
        bbox=el.bbox,
        style=el.style,
        semantic=el.semantic,
        reading_order=el.reading_order,
        qid=qid,
        qid_token=token,
        text=text,
    )


def _plan_table_element(
    el: ElementBlueprint,
    allocator: QidAllocator,
    chunk_cache: dict[str, list[dict]],
    blueprint: PageBlueprint,
    seed: int,
    domain: str,
    create_text,
) -> ElementPlan:
    """Plan a table element with per-cell text and QIDs."""
    # Table-level QID
    qid, token, _ = allocator.assign(el.eid)

    # Fetch table_cell chunks
    if "table_cell" not in chunk_cache:
        chunk_cache["table_cell"] = _fetch_chunks(create_text, "table_cell", domain, count=100, seed=seed)
    cell_chunks = chunk_cache.get("table_cell", [])

    cell_plans: list[TableCellPlan] = []
    for cell in el.table.cells:
        cell_id = f"r{cell.r}c{cell.c}"
        cell_text = _select_chunk(cell_chunks, blueprint.document_id, blueprint.page_number, f"{el.eid}_{cell_id}", seed)
        cell_qid, cell_token, _ = allocator.assign(el.eid, subid=cell_id)
        cell_plans.append(TableCellPlan(
            spec=cell,
            text=cell_text[:100],  # truncate for cell fit
            qid=cell_qid,
            qid_token=cell_token,
        ))

    table_plan = TablePlan(spec=el.table, cells=cell_plans)

    return ElementPlan(
        blueprint_id=el.eid,
        type="table",
        bbox=el.bbox,
        style=el.style,
        semantic=el.semantic,
        reading_order=el.reading_order,
        qid=qid,
        qid_token=token,
        table=table_plan,
    )


def _plan_figure_element(
    el: ElementBlueprint,
    allocator: QidAllocator,
) -> ElementPlan:
    """Plan a figure element (placeholder, no text)."""
    qid, token, _ = allocator.assign(el.eid)
    figure = el.figure or FigureSpec()

    return ElementPlan(
        blueprint_id=el.eid,
        type="figure",
        bbox=el.bbox,
        style=el.style,
        semantic=el.semantic,
        reading_order=el.reading_order,
        qid=qid,
        qid_token=token,
        figure=figure,
    )
