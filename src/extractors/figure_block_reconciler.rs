//! Reconcile detected figures with the merged block and table streams.
//!
//! Image figures, text blocks, and lattice tables are detected independently.
//! Text drawn over an illustrated figure can therefore be emitted twice: once
//! as visual figure content and again as standalone prose (or as a spurious
//! table over callout boxes). This module absorbs geometrically contained text
//! blocks into the figure and identifies table regions geometrically covered
//! by a figure.
//!
//! Absorption is fail-open. A block is consumed only when exactly one figure
//! contains it and the intact source spans account for exactly the same
//! non-whitespace characters as the raw block text. The consumed text is kept
//! verbatim in [`ConsumedBlock`], so reconciliation changes classification and
//! placement but never deletes text. Ambiguous blocks remain unchanged.
//!
//! Matching uses only page geometry and character accounting. If a page has no
//! detected figures, this module returns immediately without affecting blocks
//! or tables.

use std::collections::HashMap;

use crate::extractors::figure_detector::DetectedFigure;
use crate::layout::TextSpan;
use crate::tables::Table;

/// Minimum fraction of a text block's area that must lie inside a figure.
const BLOCK_CONTAINMENT_THRESHOLD: f64 = 0.98;
/// Table rules can protrude beyond an image XObject (for example, leader lines
/// attached to a callout). Require the table centroid and a strict majority of
/// its region to be inside the figure.
const TABLE_OVERLAP_THRESHOLD: f64 = 0.5;
/// Tolerance for centroid membership at block edges.
const EDGE_EPSILON: f64 = 0.5;

/// Borrowed geometric/text view of one merged block.
pub struct BlockView<'a> {
    /// xywh in PDF user space (bottom-left origin).
    pub bbox: (f32, f32, f32, f32),
    /// Raw, unnormalized block text.
    pub text: &'a str,
    /// Original classification label.
    pub type_label: String,
}

/// Provenance for a block absorbed into a figure.
#[derive(Debug, Clone, serde::Serialize)]
pub struct ConsumedBlock {
    /// Index in the original merged block stream.
    pub block_index: usize,
    /// Index of the containing detected figure on the page.
    pub figure_index: usize,
    /// Original classification label.
    pub original_type: String,
    /// Original raw text, preserved verbatim.
    pub text: String,
    /// Original xywh bbox in PDF user space.
    pub bbox: (f32, f32, f32, f32),
}

/// Provenance for a table suppressed because it lies inside a figure.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SuppressedTable {
    /// Index in the original page table stream.
    pub table_index: usize,
    /// Index of the containing detected figure on the page.
    pub figure_index: usize,
}

/// Result of reconciling one page. Callers perform removals by index.
#[derive(Debug, Clone, Default)]
pub struct ReconciliationResult {
    /// Blocks safely absorbed into exactly one figure.
    pub consumed: Vec<ConsumedBlock>,
    /// In-figure block indices retained because attribution was ambiguous.
    pub retained_ambiguous: Vec<usize>,
    /// Tables safely identified as enclosed figure artifacts.
    pub suppressed_tables: Vec<SuppressedTable>,
}

#[derive(Debug, Clone, Copy)]
struct PageRect {
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
}

impl PageRect {
    fn from_xywh(x: f32, y: f32, width: f32, height: f32) -> Self {
        Self {
            x0: x as f64,
            y0: y as f64,
            x1: (x + width) as f64,
            y1: (y + height) as f64,
        }
    }

    fn area(&self) -> f64 {
        (self.x1 - self.x0).max(0.0) * (self.y1 - self.y0).max(0.0)
    }

    fn intersection_area(&self, other: &Self) -> f64 {
        let width = self.x1.min(other.x1) - self.x0.max(other.x0);
        let height = self.y1.min(other.y1) - self.y0.max(other.y0);
        width.max(0.0) * height.max(0.0)
    }

    fn contains_point(&self, x: f64, y: f64, epsilon: f64) -> bool {
        x >= self.x0 - epsilon
            && x <= self.x1 + epsilon
            && y >= self.y0 - epsilon
            && y <= self.y1 + epsilon
    }

    fn centroid(&self) -> (f64, f64) {
        ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)
    }

    fn covers_object(&self, object: &Self, threshold: f64) -> bool {
        let area = object.area();
        if area <= 0.0 {
            return false;
        }
        let (cx, cy) = object.centroid();
        self.intersection_area(object) / area >= threshold && self.contains_point(cx, cy, 0.0)
    }
}

fn table_rect(table: &Table, page_height: f64) -> Option<PageRect> {
    let first_col = table.cols.first()?;
    let last_col = table.cols.last()?;
    let first_row = table.rows.first()?;
    let last_row = table.rows.last()?;
    Some(PageRect {
        x0: first_col.0,
        y0: page_height - last_row.1,
        x1: last_col.1,
        y1: page_height - first_row.0,
    })
}

fn non_whitespace_multiset(text: &str) -> HashMap<char, usize> {
    let mut counts = HashMap::new();
    for character in text.chars().filter(|character| !character.is_whitespace()) {
        *counts.entry(character).or_insert(0) += 1;
    }
    counts
}

/// Reconcile block and table views against detected figure regions.
///
/// This function does not mutate either input stream. Callers remove the
/// returned block/table indices after recording provenance.
pub fn reconcile_views(
    blocks: &[BlockView<'_>],
    figures: &[DetectedFigure],
    tables: &[Table],
    spans: &[TextSpan],
    page_height: f64,
) -> ReconciliationResult {
    let mut result = ReconciliationResult::default();
    if figures.is_empty() {
        return result;
    }

    let figure_rects: Vec<PageRect> = figures
        .iter()
        .map(|figure| {
            PageRect::from_xywh(figure.bbox.x, figure.bbox.y, figure.bbox.width, figure.bbox.height)
        })
        .collect();
    let block_rects: Vec<PageRect> = blocks
        .iter()
        .map(|block| {
            let (x, y, width, height) = block.bbox;
            PageRect::from_xywh(x, y, width, height)
        })
        .collect();
    let span_rects: Vec<PageRect> = spans
        .iter()
        .map(|span| {
            PageRect::from_xywh(span.bbox.x, span.bbox.y, span.bbox.width, span.bbox.height)
        })
        .collect();

    // A span centroid must belong to only one merged block. Overlapping block
    // membership makes source-character attribution ambiguous.
    let span_block_memberships: Vec<Vec<usize>> = span_rects
        .iter()
        .map(|span_rect| {
            let (cx, cy) = span_rect.centroid();
            block_rects
                .iter()
                .enumerate()
                .filter(|(_, block_rect)| block_rect.contains_point(cx, cy, EDGE_EPSILON))
                .map(|(index, _)| index)
                .collect()
        })
        .collect();

    for (block_index, block) in blocks.iter().enumerate() {
        let containing_figures: Vec<usize> = figure_rects
            .iter()
            .enumerate()
            .filter(|(_, figure_rect)| {
                figure_rect.covers_object(&block_rects[block_index], BLOCK_CONTAINMENT_THRESHOLD)
            })
            .map(|(index, _)| index)
            .collect();
        if containing_figures.is_empty() {
            continue;
        }
        if containing_figures.len() != 1 {
            result.retained_ambiguous.push(block_index);
            continue;
        }
        let figure_index = containing_figures[0];

        let member_spans: Vec<usize> = span_block_memberships
            .iter()
            .enumerate()
            .filter(|(_, memberships)| memberships.as_slice() == [block_index])
            .map(|(span_index, _)| span_index)
            .collect();
        let has_ambiguous_member = span_block_memberships.iter().any(|memberships| {
            memberships.contains(&block_index) && memberships.as_slice() != [block_index]
        });
        let spans_are_unambiguous = !has_ambiguous_member
            && !member_spans.is_empty()
            && member_spans.iter().all(|&span_index| {
                figure_rects[figure_index]
                    .covers_object(&span_rects[span_index], BLOCK_CONTAINMENT_THRESHOLD)
            });

        let block_characters = non_whitespace_multiset(block.text);
        let mut span_characters = HashMap::new();
        for &span_index in &member_spans {
            for (character, count) in non_whitespace_multiset(&spans[span_index].text) {
                *span_characters.entry(character).or_insert(0) += count;
            }
        }

        if !spans_are_unambiguous
            || block_characters.is_empty()
            || block_characters != span_characters
        {
            result.retained_ambiguous.push(block_index);
            continue;
        }

        result.consumed.push(ConsumedBlock {
            block_index,
            figure_index,
            original_type: block.type_label.clone(),
            text: block.text.to_string(),
            bbox: block.bbox,
        });
    }

    for (table_index, table) in tables.iter().enumerate() {
        let Some(rect) = table_rect(table, page_height) else {
            continue;
        };
        let containing_figures: Vec<usize> = figure_rects
            .iter()
            .enumerate()
            .filter(|(_, figure_rect)| figure_rect.covers_object(&rect, TABLE_OVERLAP_THRESHOLD))
            .map(|(index, _)| index)
            .collect();
        if containing_figures.len() == 1 {
            result.suppressed_tables.push(SuppressedTable {
                table_index,
                figure_index: containing_figures[0],
            });
        }
    }

    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geometry::Rect;
    use crate::layout::text_block::{Color, FontWeight};
    use crate::tables::Flavor;

    const PAGE_HEIGHT: f64 = 792.0;

    fn span(text: &str, x: f32, y: f32, width: f32, height: f32) -> TextSpan {
        TextSpan {
            text: text.to_string(),
            bbox: Rect::new(x, y, width, height),
            font_name: "Helvetica".to_string(),
            font_size: 9.0,
            font_weight: FontWeight::Normal,
            is_italic: false,
            color: Color {
                r: 0.0,
                g: 0.0,
                b: 0.0,
            },
            mcid: None,
            sequence: 0,
            split_boundary_before: false,
            offset_semantic: false,
            char_spacing: 0.0,
            word_spacing: 0.0,
            horizontal_scaling: 100.0,
            primary_detected: false,
        }
    }

    fn figure(x: f32, y: f32, width: f32, height: f32) -> DetectedFigure {
        DetectedFigure {
            bbox: Rect::new(x, y, width, height),
            page: 0,
            caption: None,
            caption_number: None,
            context_above: String::new(),
            context_below: String::new(),
            section_title: None,
        }
    }

    #[test]
    fn contained_block_is_absorbed_with_verbatim_text() {
        let spans = vec![
            span("Alpha", 120.0, 220.0, 30.0, 10.0),
            span("Beta", 155.0, 220.0, 25.0, 10.0),
        ];
        let blocks = vec![BlockView {
            bbox: (115.0, 215.0, 75.0, 20.0),
            text: "Alpha Beta",
            type_label: "Body".to_string(),
        }];

        let result = reconcile_views(
            &blocks,
            &[figure(100.0, 200.0, 300.0, 300.0)],
            &[],
            &spans,
            PAGE_HEIGHT,
        );

        assert_eq!(result.consumed.len(), 1);
        assert_eq!(result.consumed[0].text, "Alpha Beta");
        assert_eq!(result.consumed[0].original_type, "Body");
        assert!(result.retained_ambiguous.is_empty());
    }

    #[test]
    fn character_mismatch_retains_block_fail_open() {
        let spans = vec![span("Alpha", 120.0, 220.0, 30.0, 10.0)];
        let blocks = vec![BlockView {
            bbox: (115.0, 215.0, 75.0, 20.0),
            text: "Alpha Extra",
            type_label: "List".to_string(),
        }];

        let result = reconcile_views(
            &blocks,
            &[figure(100.0, 200.0, 300.0, 300.0)],
            &[],
            &spans,
            PAGE_HEIGHT,
        );

        assert!(result.consumed.is_empty());
        assert_eq!(result.retained_ambiguous, vec![0]);
    }

    #[test]
    fn overlapping_figures_retain_block_fail_open() {
        let spans = vec![span("Alpha", 120.0, 220.0, 30.0, 10.0)];
        let blocks = vec![BlockView {
            bbox: (115.0, 215.0, 50.0, 20.0),
            text: "Alpha",
            type_label: "Body".to_string(),
        }];
        let figures = vec![
            figure(100.0, 200.0, 300.0, 300.0),
            figure(110.0, 210.0, 200.0, 200.0),
        ];

        let result = reconcile_views(&blocks, &figures, &[], &spans, PAGE_HEIGHT);

        assert!(result.consumed.is_empty());
        assert_eq!(result.retained_ambiguous, vec![0]);
    }

    #[test]
    fn overlapping_block_membership_retains_both_blocks_fail_open() {
        let spans = vec![span("Alpha", 120.0, 220.0, 30.0, 10.0)];
        let blocks = vec![
            BlockView {
                bbox: (115.0, 215.0, 50.0, 20.0),
                text: "Alpha",
                type_label: "Body".to_string(),
            },
            BlockView {
                bbox: (118.0, 218.0, 50.0, 20.0),
                text: "Alpha",
                type_label: "List".to_string(),
            },
        ];

        let result = reconcile_views(
            &blocks,
            &[figure(100.0, 200.0, 300.0, 300.0)],
            &[],
            &spans,
            PAGE_HEIGHT,
        );

        assert!(result.consumed.is_empty());
        assert_eq!(result.retained_ambiguous, vec![0, 1]);
    }

    #[test]
    fn table_inside_one_figure_is_suppressed_but_outside_table_is_not() {
        let tables = vec![
            Table::new(vec![(120.0, 250.0)], vec![(300.0, 400.0)], Flavor::Lattice),
            Table::new(vec![(450.0, 550.0)], vec![(100.0, 180.0)], Flavor::Lattice),
        ];
        // First table converts to bottom-origin y=392..492.
        let figures = vec![figure(100.0, 350.0, 300.0, 250.0)];

        let result = reconcile_views(&[], &figures, &tables, &[], PAGE_HEIGHT);

        assert_eq!(
            result.suppressed_tables,
            vec![SuppressedTable {
                table_index: 0,
                figure_index: 0,
            }]
        );
    }

    #[test]
    fn table_with_majority_overlap_and_centroid_inside_is_suppressed() {
        // Bottom-origin table rect is x=70..120, y=392..492. The figure starts
        // at x=90, so 60% of the table area and its centroid are inside.
        let tables = vec![Table::new(
            vec![(70.0, 120.0)],
            vec![(300.0, 400.0)],
            Flavor::Lattice,
        )];
        let figures = vec![figure(90.0, 350.0, 300.0, 250.0)];

        let result = reconcile_views(&[], &figures, &tables, &[], PAGE_HEIGHT);

        assert_eq!(result.suppressed_tables.len(), 1);
    }

    #[test]
    fn no_figures_is_strict_no_op() {
        let spans = vec![span("Plain prose", 100.0, 300.0, 60.0, 10.0)];
        let blocks = vec![BlockView {
            bbox: (95.0, 295.0, 75.0, 20.0),
            text: "Plain prose",
            type_label: "Body".to_string(),
        }];
        let tables = vec![Table::new(
            vec![(90.0, 200.0)],
            vec![(100.0, 160.0)],
            Flavor::Lattice,
        )];

        let result = reconcile_views(&blocks, &[], &tables, &spans, PAGE_HEIGHT);

        assert!(result.consumed.is_empty());
        assert!(result.retained_ambiguous.is_empty());
        assert!(result.suppressed_tables.is_empty());
    }
}
