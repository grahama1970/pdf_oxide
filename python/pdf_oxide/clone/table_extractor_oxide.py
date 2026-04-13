"""Pure pdf_oxide table extraction - Lattice and Stream methods.

Implements Camelot-equivalent algorithms using pdf_oxide primitives.

## FUNCTIONAL REQUIREMENTS (from Camelot analysis)

### LATTICE MODE (for ruled/bordered tables):
1. Extract horizontal/vertical lines from PDF drawing commands
2. Identify line intersections (joints) as cell corners
3. Merge close lines within `line_tol` tolerance
4. Build cell grid from intersection points
5. Assign text to cells based on spatial overlap
6. Handle spanning cells (missing edges)

### STREAM MODE (for borderless tables):
1. Extract positioned text (words with bboxes)
2. Cluster text vertically into rows using `row_tol`
3. Detect column count from modal element count per row
4. Calculate column boundaries from text x-positions
5. Merge close columns within `column_tol`
6. Assign text to cells based on row/column membership

### TEXT ASSIGNMENT (both modes):
1. Find row: text y-midpoint within row bounds
2. Find column: maximize x-overlap fraction
3. Handle multi-line cells: join text with spaces
4. Calculate assignment error for diagnostics

### TOLERANCES:
- line_tol: 2px (merge close lines)
- row_tol: 3px (cluster text into rows)
- column_tol: 5px (merge close columns)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd

import pdf_oxide


@dataclass
class ExtractedTable:
    """Table extracted from PDF with structure and content."""
    page: int
    rows: int
    cols: int
    bbox: Tuple[float, float, float, float]
    ruled: bool
    headers: List[str]
    data: List[List[str]]
    accuracy: float = 1.0  # 1.0 - mean(assignment_errors)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page": self.page,
            "rows": self.rows,
            "cols": self.cols,
            "bbox": list(self.bbox),
            "ruled": self.ruled,
            "headers": self.headers,
            "data": self.data,
            "accuracy": self.accuracy,
        }

    def to_dataframe(self) -> pd.DataFrame:
        if self.headers and self.data:
            return pd.DataFrame(self.data, columns=self.headers)
        elif self.data:
            return pd.DataFrame(self.data)
        return pd.DataFrame()

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ExtractedTable":
        return cls(
            page=d["page"],
            rows=d["rows"],
            cols=d["cols"],
            bbox=tuple(d["bbox"]),
            ruled=d.get("ruled", True),
            headers=d.get("headers", []),
            data=d.get("data", []),
        )


# =============================================================================
# CELL WITH EDGE TRACKING (Camelot: core.py:493-505)
# =============================================================================

@dataclass
class Cell:
    """Cell with edge presence tracking for spanning detection."""
    x0: float  # left
    y0: float  # top
    x1: float  # right
    y1: float  # bottom
    text: str = ""
    top: bool = True
    bottom: bool = True
    left: bool = True
    right: bool = True

    @property
    def hspan(self) -> bool:
        """Cell spans horizontally if missing left or right edge."""
        return not self.left or not self.right

    @property
    def vspan(self) -> bool:
        """Cell spans vertically if missing top or bottom edge."""
        return not self.top or not self.bottom

    @property
    def bound(self) -> int:
        """Number of sides on which cell is bounded."""
        return self.top + self.bottom + self.left + self.right


def _compute_union_coverage(segments: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Compute union of overlapping/adjacent segments.

    Args:
        segments: List of (start, end) tuples

    Returns:
        Merged non-overlapping segments sorted by start
    """
    if not segments:
        return []

    # Sort by start position
    sorted_segs = sorted(segments, key=lambda s: s[0])
    merged = [list(sorted_segs[0])]

    for start, end in sorted_segs[1:]:
        last = merged[-1]
        if start <= last[1]:  # Overlapping or adjacent
            last[1] = max(last[1], end)
        else:
            merged.append([start, end])

    return [(s[0], s[1]) for s in merged]


def _check_edge_has_line(
    edge_coord: float,
    is_horizontal: bool,
    cell_start: float,
    cell_end: float,
    lines: List[Dict],
    line_tol: float = 2.0,
    coverage_threshold: float = 0.95,
) -> bool:
    """Check if an edge has line(s) covering it.

    FIX: Now checks union coverage of multiple line segments, not just single lines.
    This handles broken ruling lines (many tiny segments).

    Args:
        edge_coord: y-coordinate for horizontal edge, x for vertical
        is_horizontal: True for top/bottom edges, False for left/right
        cell_start: start of edge extent (x0 for horizontal, y0 for vertical)
        cell_end: end of edge extent (x1 for horizontal, y1 for vertical)
        lines: horizontal or vertical lines to check
        line_tol: tolerance for edge matching
        coverage_threshold: minimum fraction of edge that must be covered (default 95%)
    """
    # Collect all line segments that align with this edge
    aligned_segments = []

    for line in lines:
        if is_horizontal:
            # Horizontal line: check y matches
            if abs(line['y'] - edge_coord) <= line_tol:
                # Clip to cell extent and add segment
                seg_start = max(line['x0'], cell_start - line_tol)
                seg_end = min(line['x1'], cell_end + line_tol)
                if seg_end > seg_start:
                    aligned_segments.append((seg_start, seg_end))
        else:
            # Vertical line: check x matches
            if abs(line['x'] - edge_coord) <= line_tol:
                # Clip to cell extent and add segment
                seg_start = max(line['y0'], cell_start - line_tol)
                seg_end = min(line['y1'], cell_end + line_tol)
                if seg_end > seg_start:
                    aligned_segments.append((seg_start, seg_end))

    if not aligned_segments:
        return False

    # Compute union coverage
    merged = _compute_union_coverage(aligned_segments)

    # Calculate total covered length
    edge_length = cell_end - cell_start
    if edge_length <= 0:
        return True  # Degenerate edge

    covered_length = 0.0
    for seg_start, seg_end in merged:
        # Only count coverage within cell bounds
        overlap_start = max(seg_start, cell_start)
        overlap_end = min(seg_end, cell_end)
        if overlap_end > overlap_start:
            covered_length += overlap_end - overlap_start

    coverage = covered_length / edge_length
    return coverage >= coverage_threshold


def _build_cells_with_edges(
    rows: List[List[float]],
    cols: List[List[float]],
    h_lines: List[Dict],
    v_lines: List[Dict],
    line_tol: float = 2.0,
) -> List[List[Cell]]:
    """Build cell grid with edge presence detection.

    Checks each cell edge against actual lines to detect spanning cells.
    """
    cells = []
    for r_idx, (y_top, y_bottom) in enumerate(rows):
        row_cells = []
        for c_idx, (x_left, x_right) in enumerate(cols):
            cell = Cell(x0=x_left, y0=y_top, x1=x_right, y1=y_bottom)

            # Check each edge for line presence
            cell.top = _check_edge_has_line(y_top, True, x_left, x_right, h_lines, line_tol)
            cell.bottom = _check_edge_has_line(y_bottom, True, x_left, x_right, h_lines, line_tol)
            cell.left = _check_edge_has_line(x_left, False, y_bottom, y_top, v_lines, line_tol)
            cell.right = _check_edge_has_line(x_right, False, y_bottom, y_top, v_lines, line_tol)

            row_cells.append(cell)
        cells.append(row_cells)
    return cells


def _copy_spanning_text(cells: List[List[Cell]], shift_text: List[str] = None) -> None:
    """Copy text into spanning cells from neighbors.

    Camelot logic (core.py:768-803):
    - hspan: copy text from left neighbor with right edge
    - vspan: copy text from top neighbor with bottom edge

    Args:
        cells: 2D grid of Cell objects (modified in place)
        shift_text: direction preferences ['l', 't'] for left then top
    """
    if shift_text is None:
        shift_text = ['l', 't']

    num_rows = len(cells)
    num_cols = len(cells[0]) if cells else 0

    for direction in shift_text:
        if direction == 'l':
            # Copy horizontally (left to right)
            for i in range(num_rows):
                for j in range(num_cols):
                    if not cells[i][j].text and cells[i][j].hspan:
                        # Look left for text
                        k = 1
                        while (j - k) >= 0:
                            if cells[i][j - k].text and cells[i][j - k].right:
                                cells[i][j].text = cells[i][j - k].text
                                break
                            k += 1
        elif direction == 't':
            # Copy vertically (top to bottom)
            for i in range(num_rows):
                for j in range(num_cols):
                    if not cells[i][j].text and cells[i][j].vspan:
                        # Look up for text
                        k = 1
                        while (i - k) >= 0:
                            if cells[i - k][j].text and cells[i - k][j].bottom:
                                cells[i][j].text = cells[i - k][j].text
                                break
                            k += 1


# =============================================================================
# LATTICE MODE: Line-based extraction
# =============================================================================

def _extract_lines(doc: pdf_oxide.PdfDocument, page: int) -> Tuple[List[Dict], List[Dict]]:
    """Extract horizontal and vertical lines from page.

    Returns:
        (horizontal_lines, vertical_lines) where each line is:
        {'x0': float, 'y0': float, 'x1': float, 'y1': float}
    """
    raw_lines = doc.extract_lines(page)

    horizontal = []
    vertical = []

    for line in raw_lines:
        bbox = line.get('bbox', (0, 0, 0, 0))
        x0, y0, width, height = bbox
        x1, y1 = x0 + width, y0 + height

        # Horizontal: height ~= 0
        if abs(height) < 2:
            horizontal.append({'x0': min(x0, x1), 'y': y0, 'x1': max(x0, x1)})
        # Vertical: width ~= 0
        elif abs(width) < 2:
            vertical.append({'x': x0, 'y0': min(y0, y1), 'y1': max(y0, y1)})

    return horizontal, vertical


def _merge_close_values(values: List[float], tol: float) -> List[float]:
    """Merge values within tolerance into their average."""
    if not values:
        return []

    values = sorted(values)
    merged = [values[0]]

    for v in values[1:]:
        if abs(v - merged[-1]) <= tol:
            # Merge: update to average
            merged[-1] = (merged[-1] + v) / 2
        else:
            merged.append(v)

    return merged


def _find_intersections(
    h_lines: List[Dict],
    v_lines: List[Dict],
    line_tol: float = 2.0,
) -> Tuple[List[float], List[float]]:
    """Find unique x and y coordinates where lines intersect.

    Returns:
        (sorted_x_coords, sorted_y_coords)
    """
    x_coords = set()
    y_coords = set()

    for h in h_lines:
        for v in v_lines:
            # Check if they intersect
            if h['x0'] - line_tol <= v['x'] <= h['x1'] + line_tol:
                if v['y0'] - line_tol <= h['y'] <= v['y1'] + line_tol:
                    x_coords.add(v['x'])
                    y_coords.add(h['y'])

    # Merge close coordinates
    x_list = _merge_close_values(list(x_coords), line_tol)
    y_list = _merge_close_values(list(y_coords), line_tol)

    return sorted(x_list), sorted(y_list, reverse=True)  # y descending (top to bottom)


def _lattice_extract(
    doc: pdf_oxide.PdfDocument,
    page: int,
    table_bbox: Tuple[float, float, float, float],
    line_tol: float = 2.0,
) -> Tuple[List[List[float]], List[List[float]]]:
    """Extract row and column boundaries using lattice (line-based) method.

    Returns:
        (rows, cols) where:
        - rows: List of [y_top, y_bottom] for each row
        - cols: List of [x_left, x_right] for each column
    """
    h_lines, v_lines = _extract_lines(doc, page)

    # Filter lines within table bbox (with tolerance for edge matching)
    x0, y0, x1, y1 = table_bbox
    tol = line_tol
    h_lines = [h for h in h_lines if y0 - tol <= h['y'] <= y1 + tol and h['x0'] < x1 + tol and h['x1'] > x0 - tol]
    v_lines = [v for v in v_lines if x0 - tol <= v['x'] <= x1 + tol and v['y0'] < y1 + tol and v['y1'] > y0 - tol]

    if not h_lines or not v_lines:
        return [], []

    # Find intersection coordinates
    x_coords, y_coords = _find_intersections(h_lines, v_lines, line_tol)

    if len(x_coords) < 2 or len(y_coords) < 2:
        return [], []

    # Build row boundaries (y coords, top to bottom)
    # y_coords is sorted descending (top to bottom in PDF coords)
    rows = []
    for i in range(len(y_coords) - 1):
        y_top, y_bottom = y_coords[i], y_coords[i + 1]
        # FIX: Assert coordinate system invariant
        assert y_top >= y_bottom, f"Row coords inverted: y_top={y_top} < y_bottom={y_bottom}"
        rows.append([y_top, y_bottom])

    # Build column boundaries (x coords, left to right)
    cols = []
    for i in range(len(x_coords) - 1):
        x_left, x_right = x_coords[i], x_coords[i + 1]
        # FIX: Assert coordinate system invariant
        assert x_left <= x_right, f"Col coords inverted: x_left={x_left} > x_right={x_right}"
        cols.append([x_left, x_right])

    return rows, cols


# =============================================================================
# STREAM MODE: Text-position-based extraction
# =============================================================================

# Nurminen algorithm constants (Camelot: core.py)
TEXTEDGE_REQUIRED_ELEMENTS = 4


@dataclass
class TextEdge:
    """Text edge for Nurminen algorithm.

    Represents a vertical alignment edge (left, right, or middle of text).
    """
    x: float  # x-coordinate of the edge
    y0: float  # bottom y-coordinate
    y1: float  # top y-coordinate
    alignment: str  # 'left', 'right', or 'middle'
    textlines: List[Dict] = None  # words aligned to this edge
    is_valid: bool = False

    def __post_init__(self):
        if self.textlines is None:
            self.textlines = []


def _generate_text_edges(
    words: List[Dict],
    edge_tol: float = 50.0,
) -> List[TextEdge]:
    """Generate text edges from words using Nurminen algorithm.

    Camelot logic (core.py:134-227):
    - Create edges at left, right, and middle of each word
    - Cluster edges within edge_tol
    - Edge is valid if it has >= TEXTEDGE_REQUIRED_ELEMENTS aligned words

    FIX: Separate clustering by alignment type to avoid contamination.
    FIX: Use sorted x-coords and binary search for deterministic matching.
    """
    if not words:
        return []

    # Separate edge clustering by alignment type (fixes non-determinism)
    edges_by_alignment: Dict[str, List[TextEdge]] = {
        'left': [],
        'middle': [],
        'right': [],
    }

    for word in words:
        for alignment, x in [
            ('left', word['x0']),
            ('middle', (word['x0'] + word['x1']) / 2),
            ('right', word['x1']),
        ]:
            edge_list = edges_by_alignment[alignment]

            # Find matching edge using sorted search (deterministic)
            matched_edge = None
            matched_idx = -1

            # Binary search for closest edge within tolerance
            # Keep edges sorted by x for deterministic behavior
            for idx, edge in enumerate(edge_list):
                if abs(edge.x - x) <= edge_tol:
                    matched_edge = edge
                    matched_idx = idx
                    break

            if matched_edge:
                # Update existing edge - update x to weighted average for stability
                n = len(matched_edge.textlines)
                matched_edge.x = (matched_edge.x * n + x) / (n + 1)
                matched_edge.textlines.append(word)
                # Update vertical extent
                matched_edge.y0 = min(matched_edge.y0, word['y1'])
                matched_edge.y1 = max(matched_edge.y1, word['y0'])
                if len(matched_edge.textlines) >= TEXTEDGE_REQUIRED_ELEMENTS:
                    matched_edge.is_valid = True
            else:
                # Create new edge and insert sorted by x
                new_edge = TextEdge(
                    x=x,
                    y0=word['y1'],
                    y1=word['y0'],
                    alignment=alignment,
                    textlines=[word],
                )
                # Insert in sorted position
                insert_idx = 0
                for idx, edge in enumerate(edge_list):
                    if edge.x > x:
                        break
                    insert_idx = idx + 1
                edge_list.insert(insert_idx, new_edge)

    # Collect all valid edges
    all_edges = []
    for alignment, edge_list in edges_by_alignment.items():
        all_edges.extend([e for e in edge_list if e.is_valid])

    return all_edges


def _select_relevant_edges(edges: List[TextEdge]) -> List[TextEdge]:
    """Select most relevant edges for table detection.

    Camelot selects edges with maximum aligned textlines per alignment type.
    """
    # Group by alignment
    by_alignment = {'left': [], 'right': [], 'middle': []}
    for edge in edges:
        by_alignment[edge.alignment].append(edge)

    # Select best edge per alignment
    relevant = []
    for alignment, edge_list in by_alignment.items():
        if edge_list:
            best = max(edge_list, key=lambda e: len(e.textlines))
            relevant.append(best)

    return relevant


def _detect_columns_nurminen(
    words: List[Dict],
    edge_tol: float = 50.0,
    column_tol: float = 0.0,
) -> List[List[float]]:
    """Detect column boundaries using Nurminen text edge algorithm.

    This is the full Camelot stream mode column detection algorithm.
    Falls back to modal count method if Nurminen doesn't find clear edges.
    """
    edges = _generate_text_edges(words, edge_tol)
    relevant = _select_relevant_edges(edges)

    if not relevant:
        return []

    # Use left edges as column left boundaries
    left_edges = sorted([e for e in relevant if e.alignment == 'left'], key=lambda e: e.x)

    if len(left_edges) < 2:
        return []

    # Build columns from consecutive left edges
    cols = []
    for i in range(len(left_edges) - 1):
        x_left = left_edges[i].x
        x_right = left_edges[i + 1].x
        if x_right > x_left + column_tol:
            cols.append([x_left, x_right])

    # Add last column extending to rightmost text
    if left_edges:
        max_x = max(w['x1'] for w in words)
        last_left = left_edges[-1].x
        if max_x > last_left + column_tol:
            cols.append([last_left, max_x])

    return cols


def _cluster_rows(
    words: List[Dict],
    row_tol: float = 3.0,
) -> List[List[Dict]]:
    """Cluster words into rows by y-position.

    Words within row_tol of each other are grouped together.
    """
    if not words:
        return []

    # Sort by y descending (top to bottom), then x
    sorted_words = sorted(words, key=lambda w: (-w['y'], w['x']))

    rows = []
    current_row = [sorted_words[0]]
    current_y = sorted_words[0]['y']

    for word in sorted_words[1:]:
        if abs(word['y'] - current_y) <= row_tol:
            current_row.append(word)
        else:
            # New row
            current_row.sort(key=lambda w: w['x'])
            rows.append(current_row)
            current_row = [word]
            current_y = word['y']

    # Last row
    if current_row:
        current_row.sort(key=lambda w: w['x'])
        rows.append(current_row)

    return rows


def _detect_columns(
    rows: List[List[Dict]],
    column_tol: float = 5.0,
) -> List[List[float]]:
    """Detect column boundaries from text positions.

    Uses modal column count (most common elements per row).
    """
    if not rows:
        return []

    # Find modal column count
    counts = [len(r) for r in rows]
    mode_count = max(set(counts), key=counts.count)

    if mode_count < 1:
        return []

    # Collect x-ranges from rows with modal count
    x_ranges = []
    for row in rows:
        if len(row) == mode_count:
            for word in row:
                x_ranges.append((word['x0'], word['x1']))

    if not x_ranges:
        # Fallback: use all rows
        for row in rows:
            for word in row:
                x_ranges.append((word['x0'], word['x1']))

    # Sort by left x
    x_ranges.sort(key=lambda r: r[0])

    # Merge overlapping/close ranges into columns
    cols = []
    if x_ranges:
        current = list(x_ranges[0])
        for x0, x1 in x_ranges[1:]:
            if x0 <= current[1] + column_tol:
                # Overlapping or close - merge
                current[0] = min(current[0], x0)
                current[1] = max(current[1], x1)
            else:
                cols.append(current)
                current = [x0, x1]
        cols.append(current)

    return cols


def _stream_extract(
    words: List[Dict],
    row_tol: float = 3.0,
    column_tol: float = 5.0,
    edge_tol: float = 50.0,
    use_nurminen: bool = True,
) -> Tuple[List[List[float]], List[List[float]]]:
    """Extract row and column boundaries using stream (text-based) method.

    Args:
        words: List of word dicts with text and bbox
        row_tol: Tolerance for clustering rows (default: 3.0)
        column_tol: Tolerance for merging columns (default: 5.0)
        edge_tol: Tolerance for text edge alignment - Nurminen (default: 50.0)
        use_nurminen: Use Nurminen algorithm for column detection (default: True)

    Returns:
        (rows, cols) where:
        - rows: List of [y_top, y_bottom] for each row
        - cols: List of [x_left, x_right] for each column
    """
    if not words:
        return [], []

    # Cluster into rows
    clustered_rows = _cluster_rows(words, row_tol)

    if not clustered_rows:
        return [], []

    # Build row boundaries from clusters
    rows = []
    for i, row in enumerate(clustered_rows):
        y_top = max(w['y'] for w in row)
        y_bottom = min(w['y'] - w.get('height', 10) for w in row)

        # Extend to meet adjacent rows
        if i > 0 and rows:
            # Split gap with previous row
            gap_mid = (rows[-1][1] + y_top) / 2
            rows[-1][1] = gap_mid
            y_top = gap_mid

        rows.append([y_top, y_bottom])

    # Detect columns - try Nurminen first, fall back to modal count
    cols = []
    if use_nurminen:
        cols = _detect_columns_nurminen(words, edge_tol, column_tol)

    # Fall back to modal count method
    if not cols:
        cols = _detect_columns(clustered_rows, column_tol)

    return rows, cols


# =============================================================================
# TEXT DEDUPLICATION (Camelot: utils.py:557-600)
# =============================================================================

def _bbox_intersection_area(w1: Dict, w2: Dict) -> float:
    """Calculate intersection area of two word bboxes."""
    x_left = max(w1['x0'], w2['x0'])
    y_bottom = max(w1['y1'], w2['y1'])
    x_right = min(w1['x1'], w2['x1'])
    y_top = min(w1['y0'], w2['y0'])

    if x_right < x_left or y_top < y_bottom:
        return 0.0
    return (x_right - x_left) * (y_top - y_bottom)


def _bbox_area(w: Dict) -> float:
    """Calculate area of word bbox."""
    return abs(w['x1'] - w['x0']) * abs(w['y0'] - w['y1'])


def _deduplicate_words(words: List[Dict], overlap_threshold: float = 0.8) -> List[Dict]:
    """Remove overlapping duplicate words, keeping the longer text.

    Camelot logic: if intersection > 80% of bbox area, keep longer text.

    FIX: Uses spatial grid binning for O(n) average case instead of O(n²).
    Only compares words in same or adjacent grid cells.
    """
    if not words:
        return []

    if len(words) < 10:
        # Small list - use simple O(n²) for clarity
        return _deduplicate_words_simple(words, overlap_threshold)

    # Compute grid cell size based on average word dimensions
    avg_width = sum(abs(w['x1'] - w['x0']) for w in words) / len(words)
    avg_height = sum(abs(w['y0'] - w['y1']) for w in words) / len(words)
    cell_size = max(avg_width, avg_height, 10.0) * 2  # 2x average size

    # Build spatial grid
    grid: Dict[Tuple[int, int], List[int]] = {}

    for idx, w in enumerate(words):
        # Get grid cell for word center
        cx = (w['x0'] + w['x1']) / 2
        cy = (w['y0'] + w['y1']) / 2
        cell_x = int(cx / cell_size)
        cell_y = int(cy / cell_size)

        key = (cell_x, cell_y)
        if key not in grid:
            grid[key] = []
        grid[key].append(idx)

    # Check overlaps only within same/adjacent cells
    remaining = set(range(len(words)))

    for (cell_x, cell_y), indices in grid.items():
        # Collect indices from this cell and 8 neighbors
        neighbor_indices = set(indices)
        for dx in [-1, 0, 1]:
            for dy in [-1, 0, 1]:
                if dx == 0 and dy == 0:
                    continue
                neighbor_key = (cell_x + dx, cell_y + dy)
                if neighbor_key in grid:
                    neighbor_indices.update(grid[neighbor_key])

        # Check overlaps within this neighborhood
        idx_list = sorted(neighbor_indices)
        for i_pos, i in enumerate(idx_list):
            if i not in remaining:
                continue
            for j in idx_list[i_pos + 1:]:
                if j not in remaining:
                    continue

                w1, w2 = words[i], words[j]
                area1 = _bbox_area(w1)
                area2 = _bbox_area(w2)
                intersection = _bbox_intersection_area(w1, w2)

                # Check if overlap exceeds threshold for either word
                if area1 > 0 and (intersection / area1) > overlap_threshold:
                    if len(w2['text']) >= len(w1['text']):
                        remaining.discard(i)
                    else:
                        remaining.discard(j)
                elif area2 > 0 and (intersection / area2) > overlap_threshold:
                    if len(w1['text']) >= len(w2['text']):
                        remaining.discard(j)
                    else:
                        remaining.discard(i)

    return [words[i] for i in sorted(remaining)]


def _deduplicate_words_simple(words: List[Dict], overlap_threshold: float = 0.8) -> List[Dict]:
    """Simple O(n²) deduplication for small word lists."""
    remaining = set(range(len(words)))

    for i in range(len(words)):
        if i not in remaining:
            continue
        for j in range(i + 1, len(words)):
            if j not in remaining:
                continue

            w1, w2 = words[i], words[j]
            area1 = _bbox_area(w1)
            area2 = _bbox_area(w2)
            intersection = _bbox_intersection_area(w1, w2)

            if area1 > 0 and (intersection / area1) > overlap_threshold:
                if len(w2['text']) >= len(w1['text']):
                    remaining.discard(i)
                else:
                    remaining.discard(j)
            elif area2 > 0 and (intersection / area2) > overlap_threshold:
                if len(w1['text']) >= len(w2['text']):
                    remaining.discard(j)
                else:
                    remaining.discard(i)

    return [words[i] for i in sorted(remaining)]


# =============================================================================
# TEXT ASSIGNMENT
# =============================================================================

def _get_words_in_bbox(
    doc: pdf_oxide.PdfDocument,
    page: int,
    bbox: Tuple[float, float, float, float],
) -> List[Dict]:
    """Extract words within bounding box."""
    all_words = doc.extract_words(page)
    x0, y0, x1, y1 = bbox

    words = []
    for w in all_words:
        wb = w.bbox  # (x, y, width, height)
        wx = wb[0] + wb[2] / 2  # center x
        wy = wb[1]  # top y

        if x0 <= wx <= x1 and y0 <= wy <= y1:
            words.append({
                'text': w.text,
                'x': wx,
                'y': wy,
                'x0': wb[0],
                'x1': wb[0] + wb[2],
                'y0': wb[1],
                'y1': wb[1] + wb[3],
                'height': wb[3],
            })

    return words


def _split_text_across_columns(
    word: Dict,
    cols: List[List[float]],
) -> List[Tuple[int, str]]:
    """Split text that spans multiple columns.

    Camelot logic (utils.py:1162-1242, split_text=True):
    - Find columns that the text bbox overlaps
    - Split text proportionally based on column widths
    - Return list of (col_idx, text_portion) tuples

    Since we work with words (not characters), we do proportional splitting
    based on overlap ratio with each column.
    """
    results = []
    text = word['text']
    word_width = word['x1'] - word['x0']

    if word_width <= 0:
        return []

    # Find overlapping columns
    overlapping_cols = []
    for c_idx, (x_left, x_right) in enumerate(cols):
        if word['x1'] > x_left and word['x0'] < x_right:
            # Calculate overlap
            left = max(word['x0'], x_left)
            right = min(word['x1'], x_right)
            overlap = right - left
            if overlap > 0:
                overlapping_cols.append((c_idx, overlap))

    if len(overlapping_cols) <= 1:
        # Text fits in one column, no splitting needed
        return []

    # Split text proportionally
    total_overlap = sum(o for _, o in overlapping_cols)
    chars = list(text)
    char_idx = 0

    for c_idx, overlap in overlapping_cols:
        # Proportion of text for this column
        char_count = int(round(len(chars) * overlap / total_overlap))
        if char_count > 0 and char_idx < len(chars):
            portion = ''.join(chars[char_idx:char_idx + char_count])
            if portion.strip():
                results.append((c_idx, portion))
            char_idx += char_count

    # Handle remaining characters (rounding errors)
    if char_idx < len(chars):
        remaining = ''.join(chars[char_idx:])
        if remaining.strip() and results:
            # Add to last column
            last_idx, last_text = results[-1]
            results[-1] = (last_idx, last_text + remaining)

    return results


def _calculate_assignment_error(
    word: Dict,
    rows: List[List[float]],
    cols: List[List[float]],
    r_idx: int,
    c_idx: int,
) -> float:
    """Calculate assignment error: % of word bbox outside assigned cell.

    Camelot formula (utils.py:1245-1280):
    error = ((width * y_overflow) + (height * x_overflow)) / text_area
    """
    if r_idx < 0 or c_idx < 0:
        return 1.0

    y_top, y_bottom = rows[r_idx]
    x_left, x_right = cols[c_idx]

    # Calculate overflow on each side
    y0_offset = max(0, word['y0'] - y_top) if word['y0'] > y_top else 0
    y1_offset = max(0, y_bottom - word['y1']) if word['y1'] < y_bottom else 0
    x0_offset = max(0, x_left - word['x0']) if word['x0'] < x_left else 0
    x1_offset = max(0, word['x1'] - x_right) if word['x1'] > x_right else 0

    # Word dimensions (avoid div by zero)
    width = abs(word['x1'] - word['x0']) or 1.0
    height = abs(word['y0'] - word['y1']) or 1.0
    area = width * height

    # Camelot error formula
    error = ((width * (y0_offset + y1_offset)) + (height * (x0_offset + x1_offset))) / area
    return min(error, 1.0)  # Cap at 1.0


def _assign_text_to_cell(
    word: Dict,
    rows: List[List[float]],
    cols: List[List[float]],
) -> Tuple[int, int, float]:
    """Assign word to cell (row_idx, col_idx, error).

    Row: y-midpoint must be within row bounds
    Column: maximize x-overlap fraction
    Returns assignment error as third element.
    """
    y_mid = word['y']

    # Find row
    row_idx = -1
    for r_idx, (y_top, y_bottom) in enumerate(rows):
        if y_bottom <= y_mid <= y_top:
            row_idx = r_idx
            break

    if row_idx == -1:
        return -1, -1, 1.0

    # Find column with max overlap
    col_idx = -1
    max_overlap = -1

    for c_idx, (x_left, x_right) in enumerate(cols):
        # Calculate overlap
        if word['x1'] >= x_left and word['x0'] <= x_right:
            left = max(word['x0'], x_left)
            right = min(word['x1'], x_right)
            overlap = (right - left) / (x_right - x_left) if x_right > x_left else 0

            if overlap > max_overlap:
                max_overlap = overlap
                col_idx = c_idx

    # Calculate assignment error
    error = _calculate_assignment_error(word, rows, cols, row_idx, col_idx)

    return row_idx, col_idx, error


def _build_cell_grid(
    words: List[Dict],
    rows: List[List[float]],
    cols: List[List[float]],
    deduplicate: bool = True,
    split_text: bool = False,
) -> Tuple[List[List[str]], float]:
    """Build cell text grid from words.

    Args:
        words: List of word dicts with text and bbox
        rows: List of [y_top, y_bottom] row boundaries
        cols: List of [x_left, x_right] column boundaries
        deduplicate: Remove overlapping duplicate words (default: True)
        split_text: Split text spanning multiple columns (default: False)

    Returns:
        (grid, accuracy) where accuracy = 1.0 - mean(assignment_errors)
    """
    if not rows or not cols:
        return [], 1.0

    # Deduplicate overlapping words (Camelot: 80% overlap threshold)
    if deduplicate:
        words = _deduplicate_words(words, overlap_threshold=0.8)

    # Initialize empty grid
    grid = [["" for _ in cols] for _ in rows]
    errors = []

    # Assign each word
    for word in words:
        # Check if text spans multiple columns
        if split_text:
            split_results = _split_text_across_columns(word, cols)
            if split_results:
                # Word spans multiple columns - assign split portions
                r_idx, _, _ = _assign_text_to_cell(word, rows, cols)
                if r_idx >= 0:
                    for c_idx, text_portion in split_results:
                        if c_idx < len(cols):
                            if grid[r_idx][c_idx]:
                                grid[r_idx][c_idx] += " " + text_portion
                            else:
                                grid[r_idx][c_idx] = text_portion
                    # Estimate error as 0 for split text (it's intentional)
                    errors.append(0.0)
                continue

        # Normal assignment
        r_idx, c_idx, error = _assign_text_to_cell(word, rows, cols)
        if r_idx >= 0 and c_idx >= 0:
            errors.append(error)
            if grid[r_idx][c_idx]:
                grid[r_idx][c_idx] += " " + word['text']
            else:
                grid[r_idx][c_idx] = word['text']

    # Calculate accuracy (Camelot: 1.0 - mean error)
    accuracy = 1.0 - (sum(errors) / len(errors)) if errors else 1.0

    return grid, accuracy


# =============================================================================
# MAIN EXTRACTION FUNCTIONS
# =============================================================================

def _build_cell_grid_lattice(
    words: List[Dict],
    rows: List[List[float]],
    cols: List[List[float]],
    h_lines: List[Dict],
    v_lines: List[Dict],
    line_tol: float = 2.0,
    deduplicate: bool = True,
) -> Tuple[List[List[str]], float]:
    """Build cell grid for lattice mode with spanning cell support.

    Returns:
        (grid, accuracy) where grid handles merged cells via text copying
    """
    if not rows or not cols:
        return [], 1.0

    # Deduplicate overlapping words
    if deduplicate:
        words = _deduplicate_words(words, overlap_threshold=0.8)

    # Build cells with edge tracking
    cells = _build_cells_with_edges(rows, cols, h_lines, v_lines, line_tol)

    errors = []

    # Assign words to cells
    for word in words:
        r_idx, c_idx, error = _assign_text_to_cell(word, rows, cols)
        if r_idx >= 0 and c_idx >= 0 and r_idx < len(cells) and c_idx < len(cells[0]):
            errors.append(error)
            if cells[r_idx][c_idx].text:
                cells[r_idx][c_idx].text += " " + word['text']
            else:
                cells[r_idx][c_idx].text = word['text']

    # Copy text into spanning cells (Camelot: shift_text=['l', 't'])
    _copy_spanning_text(cells, shift_text=['l', 't'])

    # Convert to string grid
    grid = [[cell.text for cell in row] for row in cells]

    # Calculate accuracy
    accuracy = 1.0 - (sum(errors) / len(errors)) if errors else 1.0

    return grid, accuracy


def extract_table_lattice(
    doc: pdf_oxide.PdfDocument,
    page: int,
    bbox: Tuple[float, float, float, float],
    line_tol: float = 2.0,
) -> ExtractedTable:
    """Extract table using lattice (line-based) method with spanning cell support."""
    # Get lines for edge detection
    h_lines, v_lines = _extract_lines(doc, page)

    # Filter to table bbox
    x0, y0, x1, y1 = bbox
    tol = line_tol
    h_lines = [h for h in h_lines if y0 - tol <= h['y'] <= y1 + tol and h['x0'] < x1 + tol and h['x1'] > x0 - tol]
    v_lines = [v for v in v_lines if x0 - tol <= v['x'] <= x1 + tol and v['y0'] < y1 + tol and v['y1'] > y0 - tol]

    rows, cols = _lattice_extract(doc, page, bbox, line_tol)
    words = _get_words_in_bbox(doc, page, bbox)

    if not rows or not cols:
        # Fallback to stream
        return extract_table_stream(doc, page, bbox)

    # Build grid with edge detection and spanning cell support
    grid, accuracy = _build_cell_grid_lattice(words, rows, cols, h_lines, v_lines, line_tol)

    headers = grid[0] if grid else []
    data = grid[1:] if len(grid) > 1 else []

    return ExtractedTable(
        page=page,
        rows=len(grid),
        cols=len(cols),
        bbox=bbox,
        ruled=True,
        headers=headers,
        data=data,
        accuracy=accuracy,
    )


def extract_table_stream(
    doc: pdf_oxide.PdfDocument,
    page: int,
    bbox: Tuple[float, float, float, float],
    row_tol: float = 3.0,
    column_tol: float = 5.0,
    edge_tol: float = 50.0,
    split_text: bool = False,
) -> ExtractedTable:
    """Extract table using stream (text-based) method.

    Args:
        doc: PDF document
        page: Page number (0-indexed)
        bbox: Table bounding box (x0, y0, x1, y1)
        row_tol: Tolerance for clustering rows (default: 3.0)
        column_tol: Tolerance for merging columns (default: 5.0)
        edge_tol: Tolerance for Nurminen text edge detection (default: 50.0)
        split_text: Split text spanning multiple columns (default: False)
    """
    words = _get_words_in_bbox(doc, page, bbox)

    if not words:
        return ExtractedTable(
            page=page, rows=0, cols=0, bbox=bbox,
            ruled=False, headers=[], data=[], accuracy=0.0,
        )

    rows, cols = _stream_extract(words, row_tol, column_tol, edge_tol)

    if not rows or not cols:
        return ExtractedTable(
            page=page, rows=0, cols=0, bbox=bbox,
            ruled=False, headers=[], data=[], accuracy=0.0,
        )

    grid, accuracy = _build_cell_grid(words, rows, cols, split_text=split_text)

    headers = grid[0] if grid else []
    data = grid[1:] if len(grid) > 1 else []

    return ExtractedTable(
        page=page,
        rows=len(grid),
        cols=len(cols),
        bbox=bbox,
        ruled=False,
        headers=headers,
        data=data,
        accuracy=accuracy,
    )


def extract_table_from_shape(
    doc: pdf_oxide.PdfDocument,
    shape: Dict[str, Any],
    row_tol: float = 3.0,
    line_tol: float = 2.0,
) -> ExtractedTable:
    """Extract table using appropriate method based on shape.ruled."""
    page = shape["page"]
    bbox = tuple(shape["bbox"])
    ruled = shape.get("ruled", True)

    if ruled:
        return extract_table_lattice(doc, page, bbox, line_tol)
    else:
        return extract_table_stream(doc, page, bbox, row_tol)


def extract_tables_from_page(
    pdf_path_or_doc,
    page: int,
    table_shapes: List[Dict[str, Any]],
    row_tol: float = 3.0,
) -> List[ExtractedTable]:
    """Extract all tables from a single page."""
    if isinstance(pdf_path_or_doc, str):
        doc = pdf_oxide.PdfDocument(pdf_path_or_doc)
    else:
        doc = pdf_path_or_doc

    page_shapes = [s for s in table_shapes if s.get("page") == page]
    return [extract_table_from_shape(doc, s, row_tol) for s in page_shapes]


def extract_all_tables(
    pdf_path: str,
    table_shapes: List[Dict[str, Any]],
    row_tol: float = 3.0,
) -> List[ExtractedTable]:
    """Extract all tables from PDF."""
    doc = pdf_oxide.PdfDocument(pdf_path)

    all_tables: List[ExtractedTable] = []
    pages = sorted(set(s.get("page", 0) for s in table_shapes))

    for page in pages:
        page_tables = extract_tables_from_page(doc, page, table_shapes, row_tol)
        all_tables.extend(page_tables)

    return all_tables


# CLI
if __name__ == "__main__":
    import argparse
    import json
    import time

    from pdf_oxide.clone_profiler import profile_for_cloning

    parser = argparse.ArgumentParser(description="Extract tables (lattice/stream)")
    parser.add_argument("pdf", help="PDF file path")
    parser.add_argument("--page", "-p", type=int, help="Specific page (0-indexed)")
    parser.add_argument("--json", "-j", action="store_true", help="Output JSON")
    parser.add_argument("--benchmark", "-b", action="store_true", help="Show timing")
    parser.add_argument("--flavor", "-f", choices=["lattice", "stream", "auto"],
                        default="auto", help="Extraction method")

    args = parser.parse_args()

    start = time.perf_counter()
    profile = profile_for_cloning(args.pdf)
    profile_time = (time.perf_counter() - start) * 1000

    table_shapes = profile.get("table_shapes", [])
    if not table_shapes:
        print("No tables found")
        exit(0)

    # Override ruled flag based on flavor
    if args.flavor != "auto":
        for s in table_shapes:
            s["ruled"] = (args.flavor == "lattice")

    start = time.perf_counter()
    if args.page is not None:
        doc = pdf_oxide.PdfDocument(args.pdf)
        tables = extract_tables_from_page(doc, args.page, table_shapes)
    else:
        tables = extract_all_tables(args.pdf, table_shapes)
    extract_time = (time.perf_counter() - start) * 1000

    if args.json:
        print(json.dumps([t.to_dict() for t in tables], indent=2))
    else:
        for t in tables:
            print(f"\n=== Page {t.page}, {t.rows}x{t.cols} ({'lattice' if t.ruled else 'stream'}) ===")
            print(f"Headers: {t.headers}")
            print(f"Data rows: {len(t.data)}")
            if t.data:
                df = t.to_dataframe()
                print(df.to_string())

    if args.benchmark:
        print(f"\n--- Timing ---")
        print(f"Profile: {profile_time:.1f}ms")
        print(f"Extract: {extract_time:.1f}ms")
        print(f"Total:   {profile_time + extract_time:.1f}ms")
