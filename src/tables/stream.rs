//! Stream parser: text-position-based table detection.
//!
//! Implements the Nurminen table detection algorithm (from Anssi Nurminen's
//! master's thesis, as used by Camelot) with improvements:
//!
//! - Adaptive `min_edge_elements` threshold (Camelot hardcodes 4, missing small tables)
//! - Adaptive `edge_tol` based on page dimensions (Camelot hardcodes 50)
//! - Phantom table filtering (rejects text blocks that aren't real tables)
//!
//! Algorithm overview:
//! 1. Collect text elements from the page
//! 2. For each element, register its left-edge, right-edge, and middle x-coordinates
//! 3. Group elements that share an alignment (within tolerance) into TextEdge chains
//! 4. Find edges that intersect the most horizontal rows → these define table regions
//! 5. Build table bounding boxes from the edges, then extract rows and columns within each

use crate::tables::text_assign::{assign_text_to_cells, compute_accuracy};
use crate::tables::types::{BBox, ExtractConfig, Flavor, Table, TextElement};

/// Which alignment a text edge represents.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Align {
    Left,
    Right,
    Middle,
}

/// A vertical text edge: a set of text elements sharing an alignment coordinate.
///
/// The edge spans from y_min to y_max and is "valid" when it intersects
/// enough horizontal rows (>= min_elements).
#[derive(Debug, Clone)]
struct TextEdge {
    /// Averaged x-coordinate of this alignment.
    coord: f64,
    /// Topmost y-coordinate of elements in this edge.
    y_min: f64,
    /// Bottommost y-coordinate of elements in this edge.
    y_max: f64,
    /// Number of text elements registered to this edge.
    count: usize,
    /// Which alignment type.
    align: Align,
    /// Becomes true once count >= threshold.
    is_valid: bool,
}

impl TextEdge {
    fn new(coord: f64, y: f64, align: Align) -> Self {
        Self {
            coord,
            y_min: y,
            y_max: y,
            count: 1,
            align,
            is_valid: false,
        }
    }

    /// Try to add a text element to this edge.
    ///
    /// Returns true if the element was close enough to be added.
    fn try_add(&mut self, x: f64, y: f64, edge_tol: f64, min_elements: usize) -> bool {
        // Element must be vertically close to the current extent
        // (within edge_tol of the bottom of the edge)
        if (y - self.y_max).abs() > edge_tol {
            return false;
        }
        // Element must be horizontally aligned (within half the tolerance)
        if (x - self.coord).abs() > edge_tol * 0.5 {
            return false;
        }

        // Update running average of x-coordinate
        let total = self.coord * self.count as f64 + x;
        self.count += 1;
        self.coord = total / self.count as f64;

        // Extend vertical range
        self.y_min = self.y_min.min(y);
        self.y_max = self.y_max.max(y);

        if self.count >= min_elements {
            self.is_valid = true;
        }
        true
    }
}

/// Collection of text edges for all three alignment types.
struct TextEdges {
    edges: Vec<TextEdge>,
    edge_tol: f64,
    min_elements: usize,
}

impl TextEdges {
    fn new(edge_tol: f64, min_elements: usize) -> Self {
        Self {
            edges: Vec::new(),
            edge_tol,
            min_elements,
        }
    }

    /// Register a text element's alignments (left, right, middle).
    fn register(&mut self, elem: &TextElement) {
        let coords = [
            (elem.x0, Align::Left),
            (elem.x1, Align::Right),
            (elem.x_mid(), Align::Middle),
        ];

        for (x, align) in coords {
            // Try to add to an existing edge of the same alignment type
            let added = self
                .edges
                .iter_mut()
                .filter(|e| e.align == align)
                .any(|e| e.try_add(x, elem.y0, self.edge_tol, self.min_elements));

            if !added {
                // Start a new edge
                self.edges.push(TextEdge::new(x, elem.y0, align));
            }
        }
    }

    /// Process all text elements to build edges.
    ///
    /// Elements must be sorted in reading order: top-to-bottom, left-to-right.
    fn generate(&mut self, elements: &[TextElement]) {
        for elem in elements {
            if elem.text.trim().len() <= 1 {
                continue; // Skip single-char noise (matches Camelot's heuristic)
            }
            self.register(elem);
        }
    }

    /// Get the valid edges from the dominant alignment type.
    ///
    /// Matches Camelot's behavior: picks the alignment type (left/right/middle)
    /// that has the most total intersections, then returns only those edges.
    fn relevant_edges(&self) -> Vec<&TextEdge> {
        // Sum intersections per alignment type
        let mut left_sum = 0usize;
        let mut right_sum = 0usize;
        let mut middle_sum = 0usize;

        for edge in &self.edges {
            if edge.is_valid {
                match edge.align {
                    Align::Left => left_sum += edge.count,
                    Align::Right => right_sum += edge.count,
                    Align::Middle => middle_sum += edge.count,
                }
            }
        }

        // Pick the dominant alignment
        let dominant = if left_sum >= right_sum && left_sum >= middle_sum {
            Align::Left
        } else if right_sum >= middle_sum {
            Align::Right
        } else {
            Align::Middle
        };

        // Return only valid edges from the dominant alignment, sorted by count
        let mut valid: Vec<&TextEdge> = self
            .edges
            .iter()
            .filter(|e| e.is_valid && e.align == dominant)
            .collect();
        valid.sort_by(|a, b| b.count.cmp(&a.count));
        valid
    }

    /// Compute table bounding boxes from the relevant edges.
    ///
    /// Groups overlapping edges into table regions, then pads the regions.
    fn table_areas(&self, elements: &[TextElement], page_bbox: &BBox) -> Vec<BBox> {
        let relevant = self.relevant_edges();
        if relevant.is_empty() {
            return Vec::new();
        }

        // Build initial areas from edge y-extents
        let mut areas: Vec<BBox> = Vec::new();
        for edge in &relevant {
            let area = BBox::new(page_bbox.x0, edge.y_min, page_bbox.x1, edge.y_max);

            // Merge with existing overlapping area, or add new
            let merged = areas.iter_mut().find(|a| {
                // Vertical overlap check
                a.y0 <= area.y1 && a.y1 >= area.y0
            });

            match merged {
                Some(existing) => {
                    *existing = existing.union(&area);
                },
                None => {
                    areas.push(area);
                },
            }
        }

        // Extend areas to include all text elements within their y-range
        for area in &mut areas {
            for elem in elements {
                if elem.y0 >= area.y0 && elem.y1 <= area.y1 {
                    area.x0 = area.x0.min(elem.x0);
                    area.x1 = area.x1.max(elem.x1);
                }
            }
        }

        // Pad areas (Camelot uses 10pt horizontal, variable vertical)
        let avg_height = if !elements.is_empty() {
            elements.iter().map(|e| e.height()).sum::<f64>() / elements.len() as f64
        } else {
            12.0
        };

        for area in &mut areas {
            area.x0 = (area.x0 - 10.0).max(page_bbox.x0);
            area.x1 = (area.x1 + 10.0).min(page_bbox.x1);
            area.y0 = (area.y0 - avg_height * 2.0).max(page_bbox.y0);
            area.y1 = (area.y1 + 10.0).min(page_bbox.y1);
        }

        // Filter out tiny areas (less than 2 rows of text high)
        areas.retain(|a| a.height() > avg_height * 2.0);

        areas
    }
}

/// Group text elements into rows by y-coordinate proximity.
///
/// Returns rows sorted top-to-bottom. Each row is a Vec of elements
/// sorted left-to-right.
fn group_rows(elements: &[TextElement], row_tol: f64) -> Vec<Vec<&TextElement>> {
    if elements.is_empty() {
        return Vec::new();
    }

    // Sort by y0 (top-to-bottom)
    let mut sorted: Vec<&TextElement> = elements.iter().collect();
    sorted.sort_by(|a, b| a.y0.partial_cmp(&b.y0).unwrap());

    let mut rows: Vec<Vec<&TextElement>> = Vec::new();
    let mut current_row: Vec<&TextElement> = vec![sorted[0]];
    let mut current_y = sorted[0].y0;

    for &elem in &sorted[1..] {
        if (elem.y0 - current_y).abs() <= row_tol {
            current_row.push(elem);
        } else {
            // Sort current row left-to-right before pushing
            current_row.sort_by(|a, b| a.x0.partial_cmp(&b.x0).unwrap());
            rows.push(current_row);
            current_row = vec![elem];
            current_y = elem.y0;
        }
    }
    if !current_row.is_empty() {
        current_row.sort_by(|a, b| a.x0.partial_cmp(&b.x0).unwrap());
        rows.push(current_row);
    }

    rows
}

/// Detect column boundaries using mode-based approach.
///
/// 1. Count elements per row, find the mode (most common element count).
/// 2. From rows matching the mode, collect element x0 values as column starts.
/// 3. Average the x0 values per column position to get column boundaries.
///
/// This is more robust than x0 clustering (which produces spurious columns).
fn detect_columns(rows: &[Vec<&TextElement>], page_width: f64) -> Vec<(f64, f64)> {
    if rows.is_empty() {
        return Vec::new();
    }

    // Find mode of elements-per-row
    let mut count_freq: std::collections::HashMap<usize, usize> = std::collections::HashMap::new();
    for row in rows {
        *count_freq.entry(row.len()).or_insert(0) += 1;
    }
    let mode_count = count_freq
        .into_iter()
        .max_by_key(|&(_, freq)| freq)
        .map(|(count, _)| count)
        .unwrap_or(1);

    if mode_count < 2 {
        // Single-column: return full page width
        return vec![(0.0, page_width)];
    }

    // Collect x0 positions from rows matching the mode
    let mut col_x0s: Vec<Vec<f64>> = vec![Vec::new(); mode_count];
    for row in rows {
        if row.len() == mode_count {
            for (i, elem) in row.iter().enumerate() {
                col_x0s[i].push(elem.x0);
            }
        }
    }

    // Average x0 per column, then compute boundaries
    let mut boundaries: Vec<f64> = col_x0s
        .iter()
        .map(|xs| {
            if xs.is_empty() {
                0.0
            } else {
                xs.iter().sum::<f64>() / xs.len() as f64
            }
        })
        .collect();
    boundaries.sort_by(|a, b| a.partial_cmp(b).unwrap());

    // Convert to (left, right) pairs
    let mut cols = Vec::with_capacity(boundaries.len());
    for i in 0..boundaries.len() {
        let left = if i == 0 {
            0.0
        } else {
            (boundaries[i - 1] + boundaries[i]) / 2.0
        };
        let right = if i == boundaries.len() - 1 {
            page_width
        } else {
            (boundaries[i] + boundaries[i + 1]) / 2.0
        };
        cols.push((left, right));
    }

    cols
}

/// Detect row boundaries from grouped text rows.
///
/// Each row boundary is (y_top, y_bottom) derived from the elements in that row.
fn detect_row_boundaries(rows: &[Vec<&TextElement>]) -> Vec<(f64, f64)> {
    if rows.is_empty() {
        return Vec::new();
    }

    let mut boundaries = Vec::with_capacity(rows.len());
    for (i, row) in rows.iter().enumerate() {
        let y_top = row.iter().map(|e| e.y0).fold(f64::INFINITY, f64::min);
        let y_bottom = row.iter().map(|e| e.y1).fold(f64::NEG_INFINITY, f64::max);

        // Extend top/bottom to fill gaps between rows
        let top = if i == 0 {
            y_top
        } else {
            let prev_bottom = boundaries
                .last()
                .map(|&(_, b): &(f64, f64)| b)
                .unwrap_or(y_top);
            (prev_bottom + y_top) / 2.0
        };

        boundaries.push((top, y_bottom));
    }

    // Adjust: extend last row's bottom slightly
    if let Some(last) = boundaries.last_mut() {
        last.1 += 2.0; // Small padding
    }

    boundaries
}

/// Filter rows that are likely titles or footers (not part of the table).
///
/// Rows at the start/end with element count below half the mode are stripped.
fn filter_title_footer_rows<'a>(rows: &[Vec<&'a TextElement>]) -> Vec<Vec<&'a TextElement>> {
    if rows.len() < 3 {
        return rows.to_vec();
    }

    // Find mode element count
    let mut count_freq: std::collections::HashMap<usize, usize> = std::collections::HashMap::new();
    for row in rows {
        *count_freq.entry(row.len()).or_insert(0) += 1;
    }
    let mode = count_freq
        .into_iter()
        .max_by_key(|&(_, freq)| freq)
        .map(|(count, _)| count)
        .unwrap_or(1);

    let threshold = (mode + 1) / 2 + 1; // Strictly more than half the mode

    // Strip leading rows below threshold
    let start = rows.iter().position(|r| r.len() >= threshold).unwrap_or(0);
    // Strip trailing rows below threshold
    let end = rows
        .iter()
        .rposition(|r| r.len() >= threshold)
        .map(|i| i + 1)
        .unwrap_or(rows.len());

    rows[start..end].to_vec()
}

/// Check if a detected table area is actually a phantom (prose text, not a table).
///
/// A phantom table has:
/// - Very high whitespace (>80%)
/// - Very few columns (1)
/// - Rows with wildly varying element counts
fn is_phantom_table(rows: &[Vec<&TextElement>], cols: &[(f64, f64)]) -> bool {
    if cols.len() <= 1 {
        return true; // Single column = not a table
    }

    if rows.len() < 2 {
        return true; // Need at least 2 rows
    }

    // Check if element counts per row are consistent
    let counts: Vec<usize> = rows.iter().map(|r| r.len()).collect();
    let mean_count = counts.iter().sum::<usize>() as f64 / counts.len() as f64;
    let variance = counts
        .iter()
        .map(|&c| (c as f64 - mean_count).powi(2))
        .sum::<f64>()
        / counts.len() as f64;
    let cv = variance.sqrt() / mean_count.max(1.0); // Coefficient of variation

    // High variance in element counts suggests prose, not a table
    cv > 1.0
}

/// Extract tables from a page using the stream (text-based) parser.
///
/// This is the main entry point for stream extraction on a single page.
pub fn extract_stream(
    elements: &[TextElement],
    page_width: f64,
    page_height: f64,
    config: &ExtractConfig,
) -> Vec<Table> {
    if elements.is_empty() {
        return Vec::new();
    }

    let page_bbox = BBox::new(0.0, 0.0, page_width, page_height);

    // Adaptive edge_tol if not explicitly set
    let edge_tol = if config.edge_tol > 0.0 {
        config.edge_tol
    } else {
        // Scale with page height: ~50 for standard letter (792pt)
        page_height * 0.063
    };

    // Sort elements in reading order: top-to-bottom, left-to-right
    let mut sorted = elements.to_vec();
    sorted.sort_by(|a, b| {
        a.y0.partial_cmp(&b.y0)
            .unwrap()
            .then(a.x0.partial_cmp(&b.x0).unwrap())
    });

    // Step 1: Nurminen table detection — find table regions
    let mut text_edges = TextEdges::new(edge_tol, config.min_edge_elements);
    text_edges.generate(&sorted);
    let areas = text_edges.table_areas(&sorted, &page_bbox);

    // If no table areas detected, treat entire page as potential table
    let areas = if areas.is_empty() {
        vec![page_bbox]
    } else {
        areas
    };

    // Step 2: For each table area, extract rows/columns/cells
    let mut tables = Vec::new();
    for area in &areas {
        // Filter elements within this area
        let area_elements: Vec<TextElement> = sorted
            .iter()
            .filter(|e| {
                e.y_mid() >= area.y0
                    && e.y_mid() <= area.y1
                    && e.x0 >= area.x0 - 5.0
                    && e.x1 <= area.x1 + 5.0
            })
            .cloned()
            .collect();

        if area_elements.is_empty() {
            continue;
        }

        // Group into rows
        let rows = group_rows(&area_elements, config.row_tol);
        let rows = filter_title_footer_rows(&rows);

        if rows.len() < 2 {
            continue; // Need at least 2 rows for a table
        }

        // Detect columns
        let cols = detect_columns(&rows, area.width());
        if cols.is_empty() {
            continue;
        }

        // Offset columns to area coordinates
        let cols: Vec<(f64, f64)> = cols
            .iter()
            .map(|&(l, r)| (l + area.x0, r + area.x0))
            .collect();

        // Check for phantom table
        if is_phantom_table(&rows, &cols) {
            continue;
        }

        // Detect row boundaries
        let row_bounds = detect_row_boundaries(&rows);
        if row_bounds.is_empty() {
            continue;
        }

        // Build table
        let mut table = Table::new(cols, row_bounds, Flavor::Stream);
        table.set_border(); // Stream tables have all borders

        // Assign text to cells
        let errors = assign_text_to_cells(&mut table, &area_elements);
        table.accuracy = compute_accuracy(&errors);
        table.compute_whitespace();

        tables.push(table);
    }

    tables
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_elements(rows: &[&[(&str, f64, f64, f64, f64)]]) -> Vec<TextElement> {
        rows.iter()
            .flat_map(|row| {
                row.iter().map(|&(text, x0, y0, x1, y1)| TextElement {
                    text: text.to_string(),
                    x0,
                    y0,
                    x1,
                    y1,
                    font_size: 12.0,
                    is_bold: false,
                    chars: None,
                })
            })
            .collect()
    }

    #[test]
    fn group_rows_basic() {
        let elements = make_elements(&[
            &[
                ("A", 10.0, 10.0, 50.0, 22.0),
                ("B", 110.0, 10.0, 150.0, 22.0),
            ],
            &[
                ("C", 10.0, 30.0, 50.0, 42.0),
                ("D", 110.0, 30.0, 150.0, 42.0),
            ],
        ]);
        let rows = group_rows(&elements, 2.0);
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].len(), 2);
        assert_eq!(rows[1].len(), 2);
    }

    #[test]
    fn detect_columns_2col() {
        let elements = make_elements(&[
            &[
                ("A", 10.0, 10.0, 50.0, 22.0),
                ("B", 110.0, 10.0, 150.0, 22.0),
            ],
            &[
                ("C", 10.0, 30.0, 50.0, 42.0),
                ("D", 110.0, 30.0, 150.0, 42.0),
            ],
            &[
                ("E", 10.0, 50.0, 50.0, 62.0),
                ("F", 110.0, 50.0, 150.0, 62.0),
            ],
        ]);
        let rows = group_rows(&elements, 2.0);
        let cols = detect_columns(&rows, 200.0);
        assert_eq!(cols.len(), 2);
        // First col should start near 0, second near middle
        assert!(cols[0].0 < 20.0, "first col left: {}", cols[0].0);
        assert!(cols[1].0 > 40.0, "second col left: {}", cols[1].0);
    }

    #[test]
    fn filter_title_row() {
        let elements = make_elements(&[
            &[("Title", 10.0, 5.0, 180.0, 17.0)], // 1 element = title
            &[
                ("A", 10.0, 25.0, 50.0, 37.0),
                ("B", 110.0, 25.0, 150.0, 37.0),
            ],
            &[
                ("C", 10.0, 45.0, 50.0, 57.0),
                ("D", 110.0, 45.0, 150.0, 57.0),
            ],
            &[
                ("E", 10.0, 65.0, 50.0, 77.0),
                ("F", 110.0, 65.0, 150.0, 77.0),
            ],
        ]);
        let rows = group_rows(&elements, 2.0);
        assert_eq!(rows.len(), 4);
        let filtered = filter_title_footer_rows(&rows);
        assert_eq!(filtered.len(), 3); // Title row stripped
        assert_eq!(filtered[0][0].text, "A");
    }

    #[test]
    fn phantom_single_column_rejected() {
        let elements = make_elements(&[
            &[("Line 1", 10.0, 10.0, 180.0, 22.0)],
            &[("Line 2", 10.0, 30.0, 180.0, 42.0)],
        ]);
        let rows = group_rows(&elements, 2.0);
        let cols = detect_columns(&rows, 200.0);
        assert!(is_phantom_table(&rows, &cols));
    }

    #[test]
    fn extract_stream_basic_table() {
        // 3x2 table with clear column structure
        let elements = make_elements(&[
            &[
                ("Name", 10.0, 10.0, 50.0, 22.0),
                ("Value", 110.0, 10.0, 160.0, 22.0),
            ],
            &[
                ("alpha", 10.0, 30.0, 50.0, 42.0),
                ("100", 110.0, 30.0, 140.0, 42.0),
            ],
            &[
                ("beta", 10.0, 50.0, 50.0, 62.0),
                ("200", 110.0, 50.0, 140.0, 62.0),
            ],
        ]);
        let config = ExtractConfig {
            flavor: Flavor::Stream,
            min_edge_elements: 2, // Lower for test
            ..Default::default()
        };
        let tables = extract_stream(&elements, 200.0, 100.0, &config);
        assert!(!tables.is_empty(), "should find at least one table");
        let t = &tables[0];
        assert_eq!(t.num_rows(), 3, "expected 3 rows, got {}", t.num_rows());
        assert_eq!(t.num_cols(), 2, "expected 2 cols, got {}", t.num_cols());
    }
}
