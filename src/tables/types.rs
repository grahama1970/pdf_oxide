//! Core data types for table extraction.
//!
//! Coordinate system: PDF points, top-left origin (y=0 is page top).
//! All coordinates are in this space unless explicitly noted otherwise.

use std::fmt;

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
        let empty: usize = self.cells.iter().flatten().filter(|c| c.text.trim().is_empty()).count();
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

/// Configuration for table extraction.
#[derive(Debug, Clone)]
pub struct ExtractConfig {
    pub flavor: Flavor,
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
}

impl Default for ExtractConfig {
    fn default() -> Self {
        Self {
            flavor: Flavor::Lattice,
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
        }
    }
}
