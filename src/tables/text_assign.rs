//! Text-to-cell assignment using overlap-based column matching.
//!
//! Ports Camelot's `get_table_index()` logic: for each text element,
//! find the row by vertical midpoint, then the column with maximum
//! horizontal overlap ratio.

use crate::tables::types::{Table, TextElement};

/// Assign text elements to table cells.
///
/// For each text element:
/// 1. Find the row whose y-range contains the element's vertical midpoint.
/// 2. Among columns that overlap the element horizontally, pick the one
///    with the highest overlap ratio (overlap_width / column_width).
/// 3. Append the text to that cell, separated by newlines if multiple
///    elements land in the same cell.
///
/// Returns a list of assignment errors (0.0 = perfect fit, >0 = spillover).
pub fn assign_text_to_cells(table: &mut Table, elements: &[TextElement]) -> Vec<f64> {
    let mut errors = Vec::with_capacity(elements.len());

    for elem in elements {
        let text = elem.text.trim();
        if text.is_empty() {
            continue;
        }

        let y_mid = elem.y_mid();

        // Find row: y_mid must be between row.y0 (top) and row.y1 (bottom)
        let row_idx = table.rows.iter().position(|&(ry0, ry1)| y_mid >= ry0 && y_mid <= ry1);
        let row_idx = match row_idx {
            Some(r) => r,
            None => continue, // Text outside all rows
        };

        // Find column with maximum horizontal overlap
        let mut best_col: Option<usize> = None;
        let mut best_ratio: f64 = 0.0;

        for (c, &(cx0, cx1)) in table.cols.iter().enumerate() {
            // Check horizontal overlap between element and column
            if cx0 <= elem.x1 && cx1 >= elem.x0 {
                let left = elem.x0.max(cx0);
                let right = elem.x1.min(cx1);
                let col_width = (cx1 - cx0).abs();
                if col_width > 0.0 {
                    let ratio = (right - left).abs() / col_width;
                    if ratio > best_ratio {
                        best_ratio = ratio;
                        best_col = Some(c);
                    }
                }
            }
        }

        let col_idx = match best_col {
            Some(c) => c,
            None => continue, // Text doesn't overlap any column
        };

        // Compute assignment error: how much the element spills outside the cell
        let error = assignment_error(elem, table, row_idx, col_idx);
        errors.push(error);

        // Append text to cell
        let cell = &mut table.cells[row_idx][col_idx];
        if cell.text.is_empty() {
            cell.text = text.to_string();
        } else {
            cell.text.push('\n');
            cell.text.push_str(text);
        }
    }

    errors
}

/// Calculate how much a text element spills outside its assigned cell.
///
/// Returns 0.0 for a perfect fit, >0 for spillover. The error is the
/// fraction of the element's area that falls outside the cell boundaries.
///
/// Ported from Camelot's `compute_accuracy` helper.
fn assignment_error(elem: &TextElement, table: &Table, row: usize, col: usize) -> f64 {
    let (ry0, ry1) = table.rows[row]; // (top, bottom)
    let (cx0, cx1) = table.cols[col]; // (left, right)

    // How much the element extends beyond cell boundaries
    let y_top_spill = (ry0 - elem.y0).max(0.0); // element above cell top
    let y_bot_spill = (elem.y1 - ry1).max(0.0); // element below cell bottom
    let x_left_spill = (cx0 - elem.x0).max(0.0); // element left of cell
    let x_right_spill = (elem.x1 - cx1).max(0.0); // element right of cell

    let w = elem.width().max(1.0);
    let h = elem.height().max(1.0);
    let area = w * h;

    // Spillover area as fraction of element area
    let spill = (w * (y_top_spill + y_bot_spill)) + (h * (x_left_spill + x_right_spill));
    spill / area
}

/// Compute overall accuracy from assignment errors.
///
/// accuracy = 100 * (1 - mean(errors))
pub fn compute_accuracy(errors: &[f64]) -> f64 {
    if errors.is_empty() {
        return 100.0;
    }
    let mean_error: f64 = errors.iter().sum::<f64>() / errors.len() as f64;
    100.0 * (1.0 - mean_error).max(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tables::types::Flavor;

    fn make_table() -> Table {
        // 2x2 table: cols at [0,100] and [100,200], rows at [0,50] and [50,100]
        Table::new(
            vec![(0.0, 100.0), (100.0, 200.0)],
            vec![(0.0, 50.0), (50.0, 100.0)],
            Flavor::Stream,
        )
    }

    #[test]
    fn assign_centered_element() {
        let mut table = make_table();
        let elements = vec![TextElement {
            text: "hello".into(),
            x0: 10.0,
            y0: 10.0,
            x1: 80.0,
            y1: 30.0,
            font_size: 12.0,
        }];
        let errors = assign_text_to_cells(&mut table, &elements);
        assert_eq!(table.cells[0][0].text, "hello");
        assert!(errors[0] < 0.01, "error should be near zero: {}", errors[0]);
    }

    #[test]
    fn assign_to_second_column() {
        let mut table = make_table();
        let elements = vec![TextElement {
            text: "world".into(),
            x0: 120.0,
            y0: 60.0,
            x1: 180.0,
            y1: 80.0,
            font_size: 12.0,
        }];
        let errors = assign_text_to_cells(&mut table, &elements);
        assert_eq!(table.cells[1][1].text, "world");
        assert!(errors[0] < 0.01);
    }

    #[test]
    fn skip_empty_text() {
        let mut table = make_table();
        let elements = vec![TextElement {
            text: "   ".into(),
            x0: 10.0,
            y0: 10.0,
            x1: 80.0,
            y1: 30.0,
            font_size: 12.0,
        }];
        let errors = assign_text_to_cells(&mut table, &elements);
        assert!(errors.is_empty());
        assert!(table.cells[0][0].text.is_empty());
    }

    #[test]
    fn multiple_elements_same_cell() {
        let mut table = make_table();
        let elements = vec![
            TextElement {
                text: "line1".into(),
                x0: 10.0, y0: 10.0, x1: 80.0, y1: 20.0, font_size: 12.0,
            },
            TextElement {
                text: "line2".into(),
                x0: 10.0, y0: 25.0, x1: 80.0, y1: 35.0, font_size: 12.0,
            },
        ];
        assign_text_to_cells(&mut table, &elements);
        assert_eq!(table.cells[0][0].text, "line1\nline2");
    }

    #[test]
    fn accuracy_perfect() {
        assert!((compute_accuracy(&[0.0, 0.0, 0.0]) - 100.0).abs() < 0.01);
    }

    #[test]
    fn accuracy_with_errors() {
        // Mean error 0.1 => accuracy 90
        let acc = compute_accuracy(&[0.1, 0.1, 0.1]);
        assert!((acc - 90.0).abs() < 0.01);
    }
}
