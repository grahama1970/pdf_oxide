"""Pipeline utility functions."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Optional


def md5(s: str) -> str:
    """MD5 hash for generating deterministic keys."""
    return hashlib.md5(str(s).encode()).hexdigest()


def assign_section(
    item: Dict, sections: List[Dict], page: int
) -> Optional[str]:
    """Assign an item to the best-matching section by page proximity."""
    if not sections:
        return None
    best = None
    for s in sections:
        if s["page_start"] <= page:
            best = s["id"]
    return best


def data_to_csv(data: List[List[str]]) -> str:
    """Convert 2D list to CSV string."""
    if not data:
        return ""
    lines = []
    for row in data:
        lines.append(
            ",".join(f'"{c}"' if "," in str(c) else str(c) for c in row)
        )
    return "\n".join(lines)


def data_to_html(data: List[List[str]]) -> str:
    """Convert 2D list to HTML table string."""
    if not data:
        return ""
    rows_html = []
    for i, row in enumerate(data):
        tag = "th" if i == 0 else "td"
        cells = "".join(f"<{tag}>{c}</{tag}>" for c in row)
        rows_html.append(f"<tr>{cells}</tr>")
    return f"<table>{''.join(rows_html)}</table>"


def safe_json(text: str) -> Dict:
    """Parse JSON from LLM response, with repair fallback."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to extract JSON from markdown code blocks
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Try json_repair if available
    try:
        import json_repair

        return json_repair.loads(text)
    except (ImportError, Exception):
        pass
    return {}


def log(msg: str) -> None:
    """Simple logging -- use loguru if available, else print."""
    try:
        from loguru import logger

        logger.info(msg)
    except ImportError:
        print(f"[pdf_oxide.pipeline] {msg}")
