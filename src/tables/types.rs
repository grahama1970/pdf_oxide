//! Core data types for table extraction.
//!
//! Coordinate system: PDF points, top-left origin (y=0 is page top).
//! All coordinates are in this space unless explicitly noted otherwise.

use std::fmt;

/// Position of a single character within a text element.
///
/// Used for precise text splitting at column boundaries (like Camelot's LTChar).
#[derive(Debug, Clone)]
pub struct CharPosition {
    /// The character
    pub char: char,
    /// Left x-coordinate
    pub x0: f64,
    /// Right x-coordinate
    pub x1: f64,
}

impl CharPosition {
    /// Horizontal center of this character.
    pub fn x_mid(&self) -> f64 {
        (self.x0 + self.x1) / 2.0
    }
}

/// A text element with position on the page.
///
/// Coordinates are top-left origin: (x0, y0) is top-left corner,
/// (x1, y1) is bottom-right corner. y1 > y0 always.
#[derive(Debug, Clone)]
pub struct TextElement {
    pub text: String,
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
    pub font_size: f64,
    /// Whether the source span uses a bold-weight font.
    pub is_bold: bool,
    /// Optional character-level positions for precise column splitting.
    /// When present, enables Camelot-style character-by-character assignment.
    pub chars: Option<Vec<CharPosition>>,
}

impl TextElement {
    /// Horizontal center of this element.
    pub fn x_mid(&self) -> f64 {
        (self.x0 + self.x1) / 2.0
    }

    /// Vertical center of this element.
    pub fn y_mid(&self) -> f64 {
        (self.y0 + self.y1) / 2.0
    }

    /// Width of this element.
    pub fn width(&self) -> f64 {
        self.x1 - self.x0
    }

    /// Height of this element.
    pub fn height(&self) -> f64 {
        self.y1 - self.y0
    }
}

/// A cell in a table grid.
///
/// Coordinates define the cell rectangle in page space (top-left origin).
/// Edge flags indicate which borders are present (from detected lines).
#[derive(Debug, Clone)]
pub struct Cell {
    /// Left x-coordinate.
    pub x0: f64,
    /// Top y-coordinate (smaller value = higher on page).
    pub y0: f64,
    /// Right x-coordinate.
    pub x1: f64,
    /// Bottom y-coordinate (larger value = lower on page).
    pub y1: f64,
    /// Text content assigned to this cell.
    pub text: String,
    /// Left border present.
    pub left: bool,
    /// Right border present.
    pub right: bool,
    /// Top border present.
    pub top: bool,
    /// Bottom border present.
    pub bottom: bool,
}

impl Cell {
    pub fn new(x0: f64, y0: f64, x1: f64, y1: f64) -> Self {
        Self {
            x0,
            y0,
            x1,
            y1,
            text: String::new(),
            left: false,
            right: false,
            top: false,
            bottom: false,
        }
    }

    /// True if this cell is part of a horizontal span (missing left or right border).
    pub fn hspan(&self) -> bool {
        !self.left || !self.right
    }

    /// True if this cell is part of a vertical span (missing top or bottom border).
    pub fn vspan(&self) -> bool {
        !self.top || !self.bottom
    }

    pub fn width(&self) -> f64 {
        self.x1 - self.x0
    }

    pub fn height(&self) -> f64 {
        self.y1 - self.y0
    }
}

/// A line segment detected in the page image or from PDF paths.
///
/// Represented as two endpoints. For horizontal lines, y0 ≈ y1.
/// For vertical lines, x0 ≈ x1.
#[derive(Debug, Clone, Copy)]
pub struct Segment {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
}

impl Segment {
    pub fn is_horizontal(&self, tol: f64) -> bool {
        (self.y0 - self.y1).abs() <= tol
    }

    pub fn is_vertical(&self, tol: f64) -> bool {
        (self.x0 - self.x1).abs() <= tol
    }

    pub fn length(&self) -> f64 {
        ((self.x1 - self.x0).powi(2) + (self.y1 - self.y0).powi(2)).sqrt()
    }
}

/// A bounding box in page coordinates (top-left origin).
#[derive(Debug, Clone, Copy)]
pub struct BBox {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
}

impl BBox {
    pub fn new(x0: f64, y0: f64, x1: f64, y1: f64) -> Self {
        Self { x0, y0, x1, y1 }
    }

    pub fn width(&self) -> f64 {
        self.x1 - self.x0
    }

    pub fn height(&self) -> f64 {
        self.y1 - self.y0
    }

    /// Check if this bbox contains a point.
    pub fn contains_point(&self, x: f64, y: f64) -> bool {
        x >= self.x0 && x <= self.x1 && y >= self.y0 && y <= self.y1
    }

    /// Check if this bbox overlaps with another.
    pub fn overlaps(&self, other: &BBox) -> bool {
        self.x0 < other.x1 && self.x1 > other.x0 && self.y0 < other.y1 && self.y1 > other.y0
    }

    /// Merge with another bbox, returning the union.
    pub fn union(&self, other: &BBox) -> BBox {
        BBox {
            x0: self.x0.min(other.x0),
            y0: self.y0.min(other.y0),
            x1: self.x1.max(other.x1),
            y1: self.y1.max(other.y1),
        }
    }
}

/// An extracted table with cell grid and metadata.
#[derive(Debug, Clone)]
pub struct Table {
    /// Column boundaries: list of (x_left, x_right) pairs, left to right.
    pub cols: Vec<(f64, f64)>,
    /// Row boundaries: list of (y_top, y_bottom) pairs, top to bottom.
    pub rows: Vec<(f64, f64)>,
    /// Cell grid: cells[row][col].
    pub cells: Vec<Vec<Cell>>,
    /// Extraction flavor used.
    pub flavor: Flavor,
    /// Text assignment accuracy (0-100, higher is better).
    pub accuracy: f64,
    /// Percentage of empty cells (0-100).
    pub whitespace: f64,
    /// Page number this table was extracted from (0-indexed).
    pub page: usize,
    /// Table index on the page (0-indexed).
    pub order: usize,
}

impl Table {
    /// Create a new table from column and row boundaries.
    ///
    /// Builds the cell grid automatically.
    pub fn new(cols: Vec<(f64, f64)>, rows: Vec<(f64, f64)>, flavor: Flavor) -> Self {
        let cells: Vec<Vec<Cell>> = rows
            .iter()
            .map(|&(ry0, ry1)| {
                cols.iter()
                    .map(|&(cx0, cx1)| Cell::new(cx0, ry0, cx1, ry1))
                    .collect()
            })
            .collect();

        Self {
            cols,
            rows,
            cells,
            flavor,
            accuracy: 0.0,
            whitespace: 0.0,
            page: 0,
            order: 0,
        }
    }

    pub fn num_rows(&self) -> usize {
        self.rows.len()
    }

    pub fn num_cols(&self) -> usize {
        self.cols.len()
    }

    /// Get the 2D text data as a Vec<Vec<String>>.
    pub fn data(&self) -> Vec<Vec<String>> {
        self.cells
            .iter()
            .map(|row| row.iter().map(|c| c.text.clone()).collect())
            .collect()
    }

    /// Compute whitespace percentage: fraction of cells with empty text.
    pub fn compute_whitespace(&mut self) {
        let total = self.num_rows() * self.num_cols();
        if total == 0 {
            self.whitespace = 0.0;
            return;
        }
        let empty: usize = self
            .cells
            .iter()
            .flatten()
            .filter(|c| c.text.trim().is_empty())
            .count();
        self.whitespace = 100.0 * empty as f64 / total as f64;
    }

    /// Set all border edges on perimeter cells.
    pub fn set_border(&mut self) {
        let nrows = self.num_rows();
        let ncols = self.num_cols();
        if nrows == 0 || ncols == 0 {
            return;
        }
        for r in 0..nrows {
            self.cells[r][0].left = true;
            self.cells[r][ncols - 1].right = true;
        }
        for c in 0..ncols {
            self.cells[0][c].top = true;
            self.cells[nrows - 1][c].bottom = true;
        }
    }

    /// Detect merged cell regions based on missing internal borders.
    ///
    /// Returns a list of merged regions. Each region spans multiple cells
    /// that are connected by missing borders.
    ///
    /// Algorithm: flood-fill from each cell, following paths where internal
    /// borders are missing. Track visited cells to avoid duplicates.
    pub fn detect_merged_regions(&self) -> Vec<MergedRegion> {
        let nrows = self.num_rows();
        let ncols = self.num_cols();
        if nrows == 0 || ncols == 0 {
            return Vec::new();
        }

        let mut visited = vec![vec![false; ncols]; nrows];
        let mut regions = Vec::new();

        for r in 0..nrows {
            for c in 0..ncols {
                if visited[r][c] {
                    continue;
                }

                // Flood-fill to find connected cells
                let (region, size) = self.flood_fill_region(r, c, &mut visited);
                // A flood-fill component can be an L or a U of missing rules.
                // Its bounding box then covers cells that are NOT part of the
                // merged cell, which moves text into the wrong column and can
                // cross a table boundary. Report only components that are
                // exactly their own rectangle.
                let rectangle = size == region.row_span * region.col_span;
                // A real merged cell carries ONE label. A component whose
                // covered cells each already hold their own text is a run of
                // undetected separators, not a span: merging it would fold
                // three distinct column headers into the first cell and drop
                // the other two (NASA-STD-3001 p82).
                let mut labelled = 0usize;
                for r in region.start_row..region.start_row + region.row_span {
                    for c in region.start_col..region.start_col + region.col_span {
                        if !self.cells[r][c].text.trim().is_empty() {
                            labelled += 1;
                        }
                    }
                }
                if rectangle && labelled <= 1 && (region.row_span > 1 || region.col_span > 1) {
                    regions.push(region);
                }
            }
        }

        regions
    }

    /// Merged regions that are real row/column spans.
    ///
    /// Only meaningful when the grid carries ruling information: tables with no
    /// internal border at all (stream/whitespace tables) and a region covering
    /// the whole grid yield no spans.
    pub fn span_regions(&self) -> Vec<MergedRegion> {
        let nrows = self.num_rows();
        let ncols = self.num_cols();
        if nrows == 0 || ncols == 0 || self.cells.len() != nrows {
            return Vec::new();
        }
        // An axis carries span information only if at least one internal rule
        // exists on it. A table ruled only horizontally has no column borders
        // at all: that is "no vertical rules", not "every row spans all columns".
        let has_internal_v = (0..nrows)
            .any(|r| (0..ncols - 1).any(|c| self.cells[r][c].right || self.cells[r][c + 1].left));
        let has_internal_h = (0..nrows - 1)
            .any(|r| (0..ncols).any(|c| self.cells[r][c].bottom || self.cells[r + 1][c].top));
        if !has_internal_v && !has_internal_h {
            return Vec::new();
        }
        let mut view = self.clone();
        for row in view.cells.iter_mut() {
            for cell in row.iter_mut() {
                if !has_internal_v {
                    cell.left = true;
                    cell.right = true;
                }
                if !has_internal_h {
                    cell.top = true;
                    cell.bottom = true;
                }
            }
        }
        // Flood-fill components are reported as bounding boxes, and the box of
        // an irregular component (an L or a U of missing rules) can overlap
        // other regions. Overlapping regions make the anchor convention
        // ambiguous, so keep a disjoint set: largest area first (row-major on
        // ties), skipping any region that overlaps one already kept. Text in a
        // skipped region still lands in the kept region that covers it.
        let mut all: Vec<MergedRegion> = view
            .detect_merged_regions()
            .into_iter()
            .filter(|m| !(m.row_span == nrows && m.col_span == ncols))
            .collect();
        all.sort_by(|a, b| {
            (b.row_span * b.col_span)
                .cmp(&(a.row_span * a.col_span))
                .then(a.start_row.cmp(&b.start_row))
                .then(a.start_col.cmp(&b.start_col))
        });
        let overlaps = |a: &MergedRegion, b: &MergedRegion| {
            a.start_row < b.start_row + b.row_span
                && b.start_row < a.start_row + a.row_span
                && a.start_col < b.start_col + b.col_span
                && b.start_col < a.start_col + a.col_span
        };
        let mut regions: Vec<MergedRegion> = Vec::new();
        for m in all {
            if regions.iter().all(|k| !overlaps(k, &m)) {
                regions.push(m);
            }
        }
        regions.sort_by(|a, b| a.start_row.cmp(&b.start_row).then(a.start_col.cmp(&b.start_col)));
        regions
    }

    /// Anchor `(row, col)` of the span region containing cell `(row, col)`
    /// whose columns fully contain `[x0, x1]`, if any.
    pub fn span_anchor_for(
        &self,
        regions: &[MergedRegion],
        row: usize,
        x0: f64,
        x1: f64,
        tol: f64,
    ) -> Option<(usize, usize)> {
        regions.iter().find_map(|m| {
            if row < m.start_row || row >= m.start_row + m.row_span || m.col_span < 2 {
                return None;
            }
            let rx0 = self.cols[m.start_col].0;
            let rx1 = self.cols[m.start_col + m.col_span - 1].1;
            (x0 >= rx0 - tol && x1 <= rx1 + tol).then_some((m.start_row, m.start_col))
        })
    }

    /// Move the text of every spanned region into its top-left anchor cell
    /// (row-major order, space-joined) and clear the covered cells, so the
    /// grid's `data()` puts a spanning label in the column where its span
    /// starts rather than wherever its glyphs happen to sit.
    pub fn consolidate_span_text(&mut self) {
        for region in self.span_regions() {
            // Refuse to merge a region whose covered cells each already hold
            // their own label: that is a run of undetected separators, not one
            // merged cell. Merging it moves several column headers into the
            // first cell, where the per-cell rebuild cannot put them back
            // (NASA-STD-3001 p82 lost 17 characters this way). Leaving the
            // cells alone is lossless and visible.
            let labelled = (region.start_row..region.start_row + region.row_span)
                .flat_map(|r| {
                    (region.start_col..region.start_col + region.col_span)
                        .map(move |c| (r, c))
                })
                .filter(|&(r, c)| !self.cells[r][c].text.trim().is_empty())
                .count();
            if labelled > 1 {
                continue;
            }
            let mut parts: Vec<String> = Vec::new();
            for r in region.start_row..region.start_row + region.row_span {
                for c in region.start_col..region.start_col + region.col_span {
                    let text = std::mem::take(&mut self.cells[r][c].text);
                    let text = text.trim();
                    if !text.is_empty() {
                        parts.push(text.to_string());
                    }
                }
            }
            self.cells[region.start_row][region.start_col].text = parts.join(" ");
        }
    }

    /// Flood-fill to find all cells connected to (start_row, start_col) via missing borders.
    fn flood_fill_region(
        &self,
        start_row: usize,
        start_col: usize,
        visited: &mut [Vec<bool>],
    ) -> (MergedRegion, usize) {
        let nrows = self.num_rows();
        let ncols = self.num_cols();

        let mut min_row = start_row;
        let mut max_row = start_row;
        let mut min_col = start_col;
        let mut max_col = start_col;

        let mut stack = vec![(start_row, start_col)];
        let mut size = 0usize;

        while let Some((r, c)) = stack.pop() {
            if visited[r][c] {
                continue;
            }
            visited[r][c] = true;
            size += 1;

            min_row = min_row.min(r);
            max_row = max_row.max(r);
            min_col = min_col.min(c);
            max_col = max_col.max(c);

            let cell = &self.cells[r][c];

            // Check right neighbor (if cell has no right border OR neighbor has no left)
            if c + 1 < ncols && !visited[r][c + 1] {
                let neighbor = &self.cells[r][c + 1];
                if !cell.right || !neighbor.left {
                    stack.push((r, c + 1));
                }
            }

            // Check left neighbor
            if c > 0 && !visited[r][c - 1] {
                let neighbor = &self.cells[r][c - 1];
                if !cell.left || !neighbor.right {
                    stack.push((r, c - 1));
                }
            }

            // Check bottom neighbor
            if r + 1 < nrows && !visited[r + 1][c] {
                let neighbor = &self.cells[r + 1][c];
                if !cell.bottom || !neighbor.top {
                    stack.push((r + 1, c));
                }
            }

            // Check top neighbor
            if r > 0 && !visited[r - 1][c] {
                let neighbor = &self.cells[r - 1][c];
                if !cell.top || !neighbor.bottom {
                    stack.push((r - 1, c));
                }
            }
        }

        (
            MergedRegion {
                start_row: min_row,
                start_col: min_col,
                row_span: max_row - min_row + 1,
                col_span: max_col - min_col + 1,
            },
            size,
        )
    }
}

/// A merged cell region spanning multiple grid cells.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MergedRegion {
    /// Top-left row index (0-indexed).
    pub start_row: usize,
    /// Top-left column index (0-indexed).
    pub start_col: usize,
    /// Number of rows this region spans.
    pub row_span: usize,
    /// Number of columns this region spans.
    pub col_span: usize,
}

impl fmt::Display for Table {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "Table({}x{}, flavor={:?}, accuracy={:.1}, page={})",
            self.num_rows(),
            self.num_cols(),
            self.flavor,
            self.accuracy,
            self.page,
        )
    }
}

/// Extraction flavor.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Flavor {
    /// Line-based detection using rendered page image.
    Lattice,
    /// Text-position-based detection using Nurminen algorithm.
    Stream,
}

/// Shared extraction strategy applied before the flavor-specific detector.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Strategy {
    /// Use the standard lattice/stream detector flow.
    Auto,
    /// Extract a two-column borderless definition list.
    DefinitionList,
}

/// Configuration for table extraction.
#[derive(Debug, Clone)]
pub struct ExtractConfig {
    pub flavor: Flavor,
    pub strategy: Strategy,
    /// Pages to extract (0-indexed). None = all pages.
    pub pages: Option<Vec<usize>>,

    // -- Lattice parameters --
    /// Structuring element scale: image_dim / line_scale = kernel size.
    /// Smaller = detects shorter lines. Default: 15.
    pub line_scale: u32,
    /// Adaptive threshold block size (must be odd). Default: 15.
    pub threshold_blocksize: u32,
    /// Constant subtracted from mean in adaptive threshold. Default: -2.
    pub threshold_constant: f64,
    /// Additional dilation iterations after erode+dilate. Default: 0.
    pub iterations: u32,
    /// Tolerance for merging close joints/lines (in pixels). Default: 2.
    pub line_tol: f64,
    /// Tolerance for matching lines to cell edges. Default: 2.
    pub joint_tol: f64,

    // -- Stream parameters --
    /// Tolerance for vertical text edge alignment (in PDF points). Default: 50.
    /// Adaptive: auto-scaled based on page height when set to 0.
    pub edge_tol: f64,
    /// Tolerance for grouping text elements into rows. Default: 2.
    pub row_tol: f64,
    /// Minimum aligned text elements for a valid edge. Default: 3.
    /// Camelot uses 4, but that misses small tables. 3 is more adaptive.
    pub min_edge_elements: usize,
    /// Left-column split position for definition-list extraction.
    pub definition_list_column_ratio: f64,
    /// Y tolerance for grouping definition-list entries into rows.
    pub definition_list_row_tol: f64,
}

impl Default for ExtractConfig {
    fn default() -> Self {
        Self {
            flavor: Flavor::Lattice,
            strategy: Strategy::Auto,
            pages: None,
            line_scale: 15,
            threshold_blocksize: 15,
            threshold_constant: -2.0,
            iterations: 0,
            line_tol: 2.0,
            joint_tol: 2.0,
            edge_tol: 50.0,
            row_tol: 2.0,
            min_edge_elements: 3,
            definition_list_column_ratio: 0.35,
            definition_list_row_tol: 4.0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_3x3_table() -> Table {
        Table::new(
            vec![(0.0, 100.0), (100.0, 200.0), (200.0, 300.0)],
            vec![(0.0, 50.0), (50.0, 100.0), (100.0, 150.0)],
            Flavor::Lattice,
        )
    }

    #[test]
    fn no_merged_cells_with_all_borders() {
        let mut table = make_3x3_table();
        // Set all internal borders
        for row in &mut table.cells {
            for cell in row {
                cell.left = true;
                cell.right = true;
                cell.top = true;
                cell.bottom = true;
            }
        }

        let regions = table.detect_merged_regions();
        assert!(regions.is_empty(), "should have no merged regions");
    }

    #[test]
    fn horizontal_merge_2_cells() {
        let mut table = make_3x3_table();
        // Set all borders first
        for row in &mut table.cells {
            for cell in row {
                cell.left = true;
                cell.right = true;
                cell.top = true;
                cell.bottom = true;
            }
        }
        // Remove border between (0,0) and (0,1) to create horizontal merge
        table.cells[0][0].right = false;
        table.cells[0][1].left = false;

        let regions = table.detect_merged_regions();
        assert_eq!(regions.len(), 1, "should detect one merged region");
        let r = &regions[0];
        assert_eq!(r.start_row, 0);
        assert_eq!(r.start_col, 0);
        assert_eq!(r.row_span, 1);
        assert_eq!(r.col_span, 2);
    }

    #[test]
    fn vertical_merge_2_cells() {
        let mut table = make_3x3_table();
        for row in &mut table.cells {
            for cell in row {
                cell.left = true;
                cell.right = true;
                cell.top = true;
                cell.bottom = true;
            }
        }
        // Remove border between (0,0) and (1,0)
        table.cells[0][0].bottom = false;
        table.cells[1][0].top = false;

        let regions = table.detect_merged_regions();
        assert_eq!(regions.len(), 1);
        let r = &regions[0];
        assert_eq!(r.start_row, 0);
        assert_eq!(r.start_col, 0);
        assert_eq!(r.row_span, 2);
        assert_eq!(r.col_span, 1);
    }

    #[test]
    fn rectangular_merge_2x2() {
        let mut table = make_3x3_table();
        for row in &mut table.cells {
            for cell in row {
                cell.left = true;
                cell.right = true;
                cell.top = true;
                cell.bottom = true;
            }
        }
        // Create 2x2 merge at top-left
        table.cells[0][0].right = false;
        table.cells[0][1].left = false;
        table.cells[0][0].bottom = false;
        table.cells[1][0].top = false;
        table.cells[0][1].bottom = false;
        table.cells[1][1].top = false;
        table.cells[1][0].right = false;
        table.cells[1][1].left = false;

        let regions = table.detect_merged_regions();
        assert_eq!(regions.len(), 1);
        let r = &regions[0];
        assert_eq!(r.row_span, 2);
        assert_eq!(r.col_span, 2);
    }

    /// Fully ruled 4x3 grid with the G4 `merged_cells` layout: row 0 spans all
    /// columns, (1,0) spans rows 1-2, (3,0) spans columns 0-1.
    #[test]
    fn span_regions_and_anchor_text_for_merged_layout() {
        let mut table = Table::new(
            vec![(0.0, 100.0), (100.0, 200.0), (200.0, 300.0)],
            vec![(0.0, 10.0), (10.0, 20.0), (20.0, 30.0), (30.0, 40.0)],
            Flavor::Lattice,
        );
        for row in &mut table.cells {
            for cell in row {
                cell.left = true;
                cell.right = true;
                cell.top = true;
                cell.bottom = true;
            }
        }
        for c in 0..2 {
            table.cells[0][c].right = false;
            table.cells[0][c + 1].left = false;
        }
        table.cells[1][0].bottom = false;
        table.cells[2][0].top = false;
        table.cells[3][0].right = false;
        table.cells[3][1].left = false;
        table.cells[0][1].text = "System Limits".into();
        table.cells[1][0].text = "Nominal mode".into();
        table.cells[3][0].text = "Emergency mode".into();

        let mut spans: Vec<(usize, usize, usize, usize)> = table
            .span_regions()
            .iter()
            .map(|m| (m.start_row, m.start_col, m.row_span, m.col_span))
            .collect();
        spans.sort();
        assert_eq!(spans, vec![(0, 0, 1, 3), (1, 0, 2, 1), (3, 0, 1, 2)]);

        table.consolidate_span_text();
        assert_eq!(table.cells[0][0].text, "System Limits");
        assert_eq!(table.cells[0][1].text, "");
        assert_eq!(table.cells[1][0].text, "Nominal mode");

        // An element lying inside the row-0 span maps to its anchor.
        let regions = table.span_regions();
        assert_eq!(table.span_anchor_for(&regions, 0, 120.0, 180.0, 2.0), Some((0, 0)));
        assert_eq!(table.span_anchor_for(&regions, 2, 120.0, 180.0, 2.0), None);
    }

    /// Horizontal rules only (booktabs-style): no column-rule evidence, so no
    /// spans, even though every internal vertical border is absent.
    #[test]
    fn span_regions_ignore_axis_without_internal_rules() {
        let mut table = make_3x3_table();
        for row in &mut table.cells {
            for cell in row {
                cell.top = true;
                cell.bottom = true;
            }
        }
        assert!(table.span_regions().is_empty());
        // No rules at all (stream table): no spans.
        let bare = make_3x3_table();
        assert!(bare.span_regions().is_empty());
    }
}
