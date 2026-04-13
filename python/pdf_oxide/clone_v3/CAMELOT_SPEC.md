# Camelot Table Extraction - Requirements Specification

Based on analysis of camelot-dev/camelot source code with actual implementation references.

## 1. LATTICE MODE (Ruled Tables)

### 1.1 Line Detection
- [ ] Convert PDF page to image at configurable DPI (default: 300)
- [ ] Apply adaptive thresholding (blocksize=15, constant=-2)
- [ ] Detect horizontal lines via morphological operations
- [ ] Detect vertical lines via morphological operations
- [ ] `line_scale` parameter controls kernel size: `page_dimension / line_scale`

**pdf_oxide equivalent**: Use `extract_lines()` which returns drawn lines directly (no image processing needed)

### 1.2 Line Merging

**Source**: `camelot/utils.py` lines 870-896

```python
def merge_close_lines(ar, line_tol=2):
    """Merge lines which are within a tolerance by calculating a moving mean."""
    ret = []
    for a in ar:
        if not ret:
            ret.append(a)
        else:
            temp = ret[-1]
            if math.isclose(temp, a, abs_tol=line_tol):
                temp = (temp + a) / 2.0
                ret[-1] = temp
            else:
                ret.append(a)
    return ret
```

**Usage in lattice.py** (lines 330-331):
```python
cols = merge_close_lines(sorted(cols), line_tol=self.line_tol)
rows = merge_close_lines(sorted(rows, reverse=True), line_tol=self.line_tol)
```

### 1.3 Joint/Intersection Detection

**Source**: `camelot/image_processing.py` lines 236-276

```python
def find_joints(contours, vertical, horizontal):
    """Find joints/intersections inside each table boundary."""
    joints = np.multiply(vertical, horizontal)  # pixel-wise AND
    tables = {}
    for c in contours:
        x, y, w, h = c
        roi = joints[y : y + h, x : x + w]
        jc, __ = cv2.findContours(
            roi.astype(np.uint8), cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
        )
        if len(jc) <= 4:  # minimum 4 joints required
            continue
        joint_coords = []
        for j in jc:
            jx, jy, jw, jh = cv2.boundingRect(j)
            c1, c2 = x + (2 * jx + jw) // 2, y + (2 * jy + jh) // 2
            joint_coords.append((c1, c2))
        tables[(x, y + h, x + w, y)] = joint_coords
    return tables
```

**Algorithm**:
- Multiplies vertical and horizontal line masks pixel-by-pixel
- Uses OpenCV `findContours` to locate individual joints
- Requires minimum 4 joints per table contour

### 1.4 Cell Grid Construction

**Source**: `camelot/core.py` lines 540-543

```python
def __init__(self, cols, rows):
    self.cols = cols
    self.rows = rows
    self.cells = [[Cell(c[0], r[1], c[1], r[0]) for c in cols] for r in rows]
```

**Joint normalization** in `lattice.py` (lines 296-333):
```python
# Merge x coordinates that are close together
joints_normalized = list(map(lambda x: list(x), sorted(joints, key=lambda j: -j[0])))
for idx in range(1, len(joints_normalized)):
    x_left, x_right = joints_normalized[idx - 1][0], joints_normalized[idx][0]
    if x_left - line_tol <= x_right <= x_left + line_tol:
        joints_normalized[idx][0] = x_left

# Extract and merge column/row anchors
cols = list(map(lambda coords: coords[0], joints))
cols.extend([bbox[0], bbox[2]])
rows = list(map(lambda coords: coords[1], joints))
rows.extend([bbox[1], bbox[3]])

cols = merge_close_lines(sorted(cols), line_tol=self.line_tol)
rows = merge_close_lines(sorted(rows, reverse=True), line_tol=self.line_tol)
```

### 1.5 Spanning Cell Detection

**Source**: `camelot/core.py` lines 493-505

```python
@property
def hspan(self) -> bool:
    """Whether or not cell spans horizontally."""
    return not self.left or not self.right

@property
def vspan(self) -> bool:
    """Whether or not cell spans vertically."""
    return not self.top or not self.bottom

@property
def bound(self):
    """The number of sides on which the cell is bounded."""
    return self.top + self.bottom + self.left + self.right
```

### 1.6 Text Copying for Spans

**Source**: `camelot/core.py` lines 768-803

```python
def _copy_horizontal_text(self):
    for i in range(len(self.cells)):
        for j in range(len(self.cells[i])):
            if not self.cells[i][j].text and self.cells[i][j].hspan:
                # check left
                k = 1
                while (j - k) >= 0:
                    if self.cells[i][j - k].text and self.cells[i][j - k].right:
                        self.cells[i][j].text = self.cells[i][j - k].text
                        break
                    k += 1

def _copy_vertical_text(self):
    for i in range(len(self.cells)):
        for j in range(len(self.cells[i])):
            if not self.cells[i][j].text and self.cells[i][j].vspan:
                # check top
                k = 1
                while (i - k) >= 0:
                    if self.cells[i - k][j].text and self.cells[i - k][j].bottom:
                        self.cells[i][j].text = self.cells[i - k][j].text
                        break
                    k += 1
```

---

## 2. STREAM MODE (Borderless Tables)

### 2.1 Text Edge Detection (Nurminen Algorithm)

**Source**: `camelot/core.py` lines 134-146

```python
def update_coords(self, x, textline, edge_tol=50):
    """Update text edge coordinates."""
    if math.isclose(self.y0, textline.y0, abs_tol=edge_tol):
        self.register_aligned_textline(textline, x)
        self.y0 = textline.y0
        # textedge is valid only if it extends over required number of textlines
        if len(self.textlines) > TEXTEDGE_REQUIRED_ELEMENTS:
            self.is_valid = True
```

**Nurminen table detection** in `stream.py` (lines 79-103):
```python
def _nurminen_table_detection(self, textlines):
    """Anssi Nurminen's Table detection algorithm.
    Link: https://dspace.cc.tut.fi/dpub/bitstream/handle/123456789/21520/Nurminen.pdf
    """
    textlines.sort(key=lambda x: (-x.y0, x.x0))  # reading order
    textedges = TextEdges(edge_tol=self.edge_tol)
    textedges.generate(textlines)  # generate left, middle, right edges
    relevant_textedges = textedges.get_relevant()
    self.textedges.extend(relevant_textedges)
    table_bbox = textedges.get_table_areas(textlines, relevant_textedges)
    if not table_bbox:
        table_bbox = {(0, 0, self.pdf_width, self.pdf_height): None}
    return table_bbox
```

### 2.2 Row Clustering

**Source**: `camelot/parsers/base.py` lines 314-350

```python
@staticmethod
def _group_rows(text, row_tol=2):
    """Group PDFMiner text objects into rows vertically within a tolerance."""
    row_y = None
    rows = []
    temp = []
    text.sort(key=lambda x: (-x.y0, x.x0))  # reading order
    non_empty_text = [t for t in text if t.get_text().strip()]
    for t in non_empty_text:
        if row_y is None:
            row_y = t.y0
        elif not math.isclose(row_y, t.y0, abs_tol=row_tol):
            rows.append(sorted(temp, key=lambda t: t.x0))
            temp = []
            row_y = t.y0  # update row's y as we go (forgiving gradual change)
        temp.append(t)
    rows.append(sorted(temp, key=lambda t: t.x0))
    return rows
```

### 2.3 Column Detection via Modal Count

**Source**: `camelot/parsers/stream.py` lines 129-168

```python
rows_grouped = self._group_rows(self.t_bbox["horizontal"], row_tol=self.row_tol)
elements = [len(r) for r in rows_grouped]

if not len(elements):
    cols = [(text_x_min, text_x_max)]
else:
    ncols = max(set(elements), key=elements.count)  # MODE CALCULATION
    if ncols == 1:
        # page usually contains no tables, but try removing 1s
        elements = list(filter(lambda x: x != 1, elements))
        if elements:
            ncols = max(set(elements), key=elements.count)
    # Extract x-bounds from rows matching modal count
    cols = [(t.x0, t.x1) for r in rows_grouped if len(r) == ncols for t in r]
```

**Key**: `max(set(elements), key=elements.count)` finds most frequent element count

### 2.4 Column Merging

**Source**: `camelot/parsers/base.py` lines 353-393

```python
@staticmethod
def _merge_columns(cl, column_tol=0):
    """Merge column boundaries if they overlap or lie within a tolerance."""
    merged = []
    for higher in cl:
        if not merged:
            merged.append(higher)
        else:
            lower = merged[-1]
            if column_tol >= 0:
                if higher[0] <= lower[1] or math.isclose(
                    higher[0], lower[1], abs_tol=column_tol
                ):
                    upper_bound = max(lower[1], higher[1])
                    lower_bound = min(lower[0], higher[0])
                    merged[-1] = (lower_bound, upper_bound)
                else:
                    merged.append(higher)
            elif column_tol < 0:
                if higher[0] <= lower[1]:
                    if math.isclose(higher[0], lower[1], abs_tol=abs(column_tol)):
                        merged.append(higher)
                    else:
                        upper_bound = max(lower[1], higher[1])
                        lower_bound = min(lower[0], higher[0])
                        merged[-1] = (lower_bound, upper_bound)
                else:
                    merged.append(higher)
    return merged
```

---

## 3. TEXT ASSIGNMENT (Both Modes)

### 3.1 Row/Column Assignment

**Source**: `camelot/utils.py` lines 1162-1242

```python
def get_table_index(table, t, direction, split_text=False, flag_size=False, strip_text=""):
    """Get indices of the table cell where a text object lies."""
    r_idx, c_idx = [-1] * 2
    for r in range(len(table.rows)):
        # Check if text y-midpoint falls within row bounds
        if (t.y0 + t.y1) / 2.0 < table.rows[r][0] and (t.y0 + t.y1) / 2.0 > table.rows[r][1]:
            lt_col_overlap = []
            for c in table.cols:
                if c[0] <= t.x1 and c[1] >= t.x0:
                    left = t.x0 if c[0] <= t.x0 else c[0]
                    right = t.x1 if c[1] >= t.x1 else c[1]
                    lt_col_overlap.append(abs(left - right) / abs(c[0] - c[1]))
                else:
                    lt_col_overlap.append(-1)
            r_idx = r
            c_idx = lt_col_overlap.index(max(lt_col_overlap))  # max overlap wins
            break
    if r_idx == -1:
        return [], 1.0
    error = calculate_assignment_error(t, table, r_idx, c_idx)
    # ... return logic
```

**Assignment logic**:
1. Find row where text y-midpoint falls within row bounds
2. Calculate overlap ratio for each column: `|intersection| / |column_width|`
3. Assign to column with maximum overlap

### 3.2 Assignment Error Calculation

**Source**: `camelot/utils.py` lines 1245-1280

```python
def calculate_assignment_error(t, table, r_idx, c_idx):
    """Calculate how much text extends outside the assigned cell."""
    y0_offset, y1_offset, x0_offset, x1_offset = [0] * 4
    if t.y0 > table.rows[r_idx][0]:
        y0_offset = abs(t.y0 - table.rows[r_idx][0])
    if t.y1 < table.rows[r_idx][1]:
        y1_offset = abs(t.y1 - table.rows[r_idx][1])
    if t.x0 < table.cols[c_idx][0]:
        x0_offset = abs(t.x0 - table.cols[c_idx][0])
    if t.x1 > table.cols[c_idx][1]:
        x1_offset = abs(t.x1 - table.cols[c_idx][1])

    x = 1.0 if abs(t.x0 - t.x1) == 0.0 else abs(t.x0 - t.x1)
    y = 1.0 if abs(t.y0 - t.y1) == 0.0 else abs(t.y0 - t.y1)

    charea = x * y
    error = ((x * (y0_offset + y1_offset)) + (y * (x0_offset + x1_offset))) / charea
    return error
```

---

## 4. TEXT DEDUPLICATION

### 4.1 Overlap Detection and Removal

**Source**: `camelot/utils.py` lines 557-600

```python
def text_in_bbox(bbox, text):
    """Return text objects inside bbox, discarding overlapping duplicates."""
    lb = (bbox[0], bbox[1])
    rt = (bbox[2], bbox[3])
    t_bbox = [
        t for t in text
        if lb[0] - 2 <= (t.x0 + t.x1) / 2.0 <= rt[0] + 2
        and lb[1] - 2 <= (t.y0 + t.y1) / 2.0 <= rt[1] + 2
    ]

    # Avoid duplicate text by discarding overlapping boxes
    rest = {t for t in t_bbox}
    for ba in t_bbox:
        for bb in rest.copy():
            if ba == bb:
                continue
            if bbox_intersect(ba, bb):
                ba_area = bbox_area(ba)
                # if intersection > 80% of ba's size, keep the longest
                if ba_area == 0 or (bbox_intersection_area(ba, bb) / ba_area) > 0.8:
                    if bbox_longer(bb, ba):
                        rest.discard(ba)
    return list(rest)
```

**Key**: 80% overlap threshold - if boxes overlap by >80%, keep the longer one

---

## 5. BBOX UTILITY FUNCTIONS

**Source**: `camelot/utils.py`

```python
def bbox_intersection_area(ba, bb) -> float:
    """Return area of the intersection of two bounding boxes."""
    x_left = max(ba.x0, bb.x0)
    y_top = min(ba.y1, bb.y1)
    x_right = min(ba.x1, bb.x1)
    y_bottom = max(ba.y0, bb.y0)
    if x_right < x_left or y_bottom > y_top:
        return 0.0
    return (x_right - x_left) * (y_top - y_bottom)

def bbox_area(bb) -> float:
    """Return area of a bounding box."""
    return (bb.x1 - bb.x0) * (bb.y1 - bb.y0)

def bbox_intersect(ba, bb) -> bool:
    """Return True if two bounding boxes intersect."""
    return ba.x1 >= bb.x0 and bb.x1 >= ba.x0 and ba.y1 >= bb.y0 and bb.y1 >= ba.y0
```

---

## 6. TOLERANCE PARAMETERS

| Parameter | Default | Mode | Purpose | Source |
|-----------|---------|------|---------|--------|
| `line_scale` | 15 | Lattice | Line detection sensitivity | lattice.py |
| `line_tol` | 2 | Lattice | Merge close lines | utils.py:870 |
| `joint_tol` | 2 | Lattice | Map joints to edges | lattice.py |
| `threshold_blocksize` | 15 | Lattice | Adaptive threshold | image_processing.py |
| `threshold_constant` | -2 | Lattice | Adaptive threshold | image_processing.py |
| `row_tol` | 2 | Stream | Cluster text into rows | base.py:314 |
| `column_tol` | 0 | Stream | Merge adjacent columns | base.py:353 |
| `edge_tol` | 50 | Stream | Text edge alignment | core.py:134 |

---

## 7. IMPLEMENTATION STATUS (pdf_oxide)

### Implemented
- [x] Line extraction from PDF drawing commands
- [x] Line merging with tolerance (`merge_close_lines` equivalent)
- [x] Intersection detection (geometric, not image-based)
- [x] Cell grid construction from sorted coordinates
- [x] Row clustering by y-position (`_group_rows` equivalent)
- [x] Column detection via modal count
- [x] Text-to-cell assignment by overlap
- [x] Lattice/stream mode selection
- [x] Spanning cell detection (hspan/vspan) - `Cell` class with edge tracking
- [x] Text copying for merged cells - `_copy_spanning_text()`
- [x] Text splitting across cell boundaries - `_split_text_across_columns()`
- [x] Assignment error calculation - `_calculate_assignment_error()`
- [x] Text deduplication (80% overlap rule) - `_deduplicate_words()`
- [x] Accuracy scoring - returned in `ExtractedTable.accuracy`
- [x] Nurminen text edge detection - `_generate_text_edges()`, `_detect_columns_nurminen()`

### Robustness Fixes (Code Review)
- [x] Nurminen clustering separated by alignment type (deterministic)
- [x] Multi-segment union coverage for span detection (handles broken lines)
- [x] Spatial grid binning for O(n) deduplication (scalable)
- [x] Coordinate system assertions (catches y-axis bugs early)
- [x] 95% coverage threshold for edge detection (configurable)

### Not Needed (pdf_oxide advantage)
- Image conversion (we have native line access)
- Morphological operations (we have vector data)
- OpenCV dependency (pure Python/Rust)
- Ghostscript dependency (pure Python/Rust)

## 8. PERFORMANCE

| Metric | Camelot | pdf_oxide |
|--------|---------|-----------|
| Profile + Extract | ~1000ms | ~38ms |
| Speedup | 1x | **~26x** |
| Dependencies | OpenCV, Ghostscript | Pure Python/Rust |
