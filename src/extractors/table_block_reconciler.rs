//! Reconcile detected tables with the classified block stream.
//!
//! The lattice table detector and the block classifier run independently over
//! the same page text. On table pages both emit the same content: the table
//! carries the grid structure (with lossy cell text from its own decoding
//! path), and the block stream re-emits every cell fragment as a standalone
//! misclassified block (Title/Body/...) with intact text. Neither output alone
//! is correct.
//!
//! This module merges them: for each accepted table, blocks geometrically
//! contained in the table region are decomposed into their text spans, spans
//! are assigned to grid cells by centroid, cell text is rebuilt from the
//! intact span text, and only blocks whose every non-whitespace character is
//! proven present in the rebuilt cells are consumed (removed from the
//! standalone block stream). Anything ambiguous fails open: the block is
//! retained unchanged.
//!
//! Matching is purely positional (bbox containment + centroid), so the same
//! rule applies to every detected table on every page and no path executes on
//! pages without tables.
//!
//! Coordinate note: `ClassifiedBlock`/`TextSpan` bboxes are xywh with a
//! bottom-left origin; `Table` cells are x0y0x1y1 with a top-left origin.
//! Everything here is converted to top-origin x0y0x1y1 via the page height.

use crate::extractors::block_classifier::ClassifiedBlock;
use crate::layout::TextSpan;
use crate::tables::Table;

/// Minimum fraction of a block's area that must lie inside the table region.
const CONTAINMENT_THRESHOLD: f64 = 0.98;

/// Provenance record for a block consumed into a table.
#[derive(Debug, Clone)]
pub struct ConsumedBlock {
    /// Index of the block in the original classified block list.
    pub block_index: usize,
    /// Original classified type (Debug format), e.g. "Title".
    pub original_type: String,
    /// Original block text, preserved verbatim.
    pub text: String,
    /// Original block bbox (xywh, bottom-left origin, as classified).
    pub bbox: (f32, f32, f32, f32),
    /// Table order on the page this block was consumed into.
    pub table_order: usize,
    /// Distinct (row, col) cells that received this block's spans.
    pub cells: Vec<(usize, usize)>,
}

/// Result of reconciling one page.
#[derive(Debug, Clone, Default)]
pub struct ReconciliationResult {
    /// Indices (into the original block list) of consumed blocks.
    pub consumed: Vec<ConsumedBlock>,
    /// Indices of blocks inside a table region that were retained fail-open.
    pub retained_ambiguous: Vec<usize>,
}

#[derive(Debug, Clone, Copy)]
struct TopRect {
    x0: f64,
    y0: f64,
    x1: f64,
    y1: f64,
}

impl TopRect {
    fn area(&self) -> f64 {
        (self.x1 - self.x0).max(0.0) * (self.y1 - self.y0).max(0.0)
    }

    fn intersection_area(&self, other: &TopRect) -> f64 {
        let w = self.x1.min(other.x1) - self.x0.max(other.x0);
        let h = self.y1.min(other.y1) - self.y0.max(other.y0);
        w.max(0.0) * h.max(0.0)
    }

    fn contains_point(&self, x: f64, y: f64) -> bool {
        x >= self.x0 && x <= self.x1 && y >= self.y0 && y <= self.y1
    }
}

/// Convert an xywh bottom-origin rect to top-origin x0y0x1y1.
fn to_top_rect(x: f32, y: f32, w: f32, h: f32, page_height: f64) -> TopRect {
    TopRect {
        x0: x as f64,
        y0: page_height - y as f64 - h as f64,
        x1: x as f64 + w as f64,
        y1: page_height - y as f64,
    }
}

fn table_bbox(table: &Table) -> Option<TopRect> {
    let (first_col, last_col) = (table.cols.first()?, table.cols.last()?);
    let (first_row, last_row) = (table.rows.first()?, table.rows.last()?);
    Some(TopRect {
        x0: first_col.0,
        y0: first_row.0,
        x1: last_col.1,
        y1: last_row.1,
    })
}

fn non_ws_chars(text: &str) -> usize {
    text.chars().filter(|c| !c.is_whitespace()).count()
}

fn non_ws_multiset(text: &str) -> std::collections::BTreeMap<char, usize> {
    let mut chars = std::collections::BTreeMap::new();
    for ch in text.chars().filter(|ch| !ch.is_whitespace()) {
        *chars.entry(ch).or_insert(0) += 1;
    }
    chars
}

/// One span assigned to one cell during reconciliation.
struct AssignedRun {
    row: usize,
    col: usize,
    x0: f64,
    y0: f64,
    text: String,
}

/// Borrowed geometric/text view of one block, independent of block struct.
///
/// The reconciler only needs geometry and raw text, so both the classifier
/// stream (`ClassifiedBlock`) and the document extractor's merged stream can
/// feed it. Text must be UN-normalized: character accounting compares block
/// text against the raw span text that built it.
pub struct BlockView<'a> {
    /// xywh, bottom-left origin (as carried by classified/merged blocks).
    pub bbox: (f32, f32, f32, f32),
    /// Raw block text.
    pub text: &'a str,
    /// Type label for provenance (Debug form, e.g. "Title").
    pub type_label: String,
}

/// Reconcile classified blocks with detected tables on one page, in place.
///
/// Consumed blocks are removed from `blocks`; affected cell text in `tables`
/// is rebuilt from the intact span text. Blocks that cannot be fully and
/// unambiguously accounted for are retained unchanged (fail open).
pub fn reconcile_page(
    blocks: &mut Vec<ClassifiedBlock>,
    tables: &mut [Table],
    spans: &[TextSpan],
    page_height: f64,
) -> ReconciliationResult {
    let views: Vec<BlockView> = blocks
        .iter()
        .map(|b| BlockView {
            bbox: (b.bbox.x, b.bbox.y, b.bbox.width, b.bbox.height),
            text: &b.text,
            type_label: format!("{:?}", b.block_type),
        })
        .collect();
    let result = reconcile_views(&views, tables, spans, page_height);
    let mut consumed: Vec<usize> = result.consumed.iter().map(|c| c.block_index).collect();
    consumed.sort_unstable_by(|a, b| b.cmp(a));
    for index in consumed {
        blocks.remove(index);
    }
    result
}

/// Core reconciliation over block views. Does NOT mutate the block list;
/// callers remove `result.consumed[*].block_index` themselves.
pub fn reconcile_views(
    blocks: &[BlockView<'_>],
    tables: &mut [Table],
    spans: &[TextSpan],
    page_height: f64,
) -> ReconciliationResult {
    let mut result = ReconciliationResult::default();
    if tables.is_empty() || blocks.is_empty() {
        return result;
    }

    let span_rects: Vec<TopRect> = spans
        .iter()
        .map(|s| to_top_rect(s.bbox.x, s.bbox.y, s.bbox.width, s.bbox.height, page_height))
        .collect();

    let mut consumed_indices: Vec<usize> = Vec::new();

    for (table_idx, table) in tables.iter_mut().enumerate() {
        // Consumption is authorized for ruled (lattice) tables only. Stream
        // detection has no line evidence and is too false-positive-prone to
        // let it swallow prose blocks; its tables pass through untouched.
        if table.flavor != crate::tables::Flavor::Lattice {
            continue;
        }
        let Some(tb) = table_bbox(table) else { continue };
        let mut assigned_runs: Vec<AssignedRun> = Vec::new();
        let mut cells_to_clear = std::collections::BTreeSet::new();

        for (block_index, block) in blocks.iter().enumerate() {
            if consumed_indices.contains(&block_index) {
                continue;
            }
            let (bx, by, bw, bh) = block.bbox;
            let br = to_top_rect(bx, by, bw, bh, page_height);
            let block_area = br.area();
            if block_area <= 0.0 {
                continue;
            }
            let contained = br.intersection_area(&tb) / block_area >= CONTAINMENT_THRESHOLD;
            let centroid_in =
                tb.contains_point((br.x0 + br.x1) / 2.0, (br.y0 + br.y1) / 2.0);
            if !contained || !centroid_in {
                continue;
            }

            // Recover the block's spans geometrically: span centroid inside
            // the block rect (small epsilon for rounding at block edges).
            let eps = 0.5;
            let member_spans: Vec<usize> = span_rects
                .iter()
                .enumerate()
                .filter(|(_, sr)| {
                    let cx = (sr.x0 + sr.x1) / 2.0;
                    let cy = (sr.y0 + sr.y1) / 2.0;
                    cx >= br.x0 - eps
                        && cx <= br.x1 + eps
                        && cy >= br.y0 - eps
                        && cy <= br.y1 + eps
                })
                .map(|(i, _)| i)
                .collect();

            // Assign each member span to exactly one grid cell by centroid.
            let mut block_runs: Vec<AssignedRun> = Vec::new();
            let mut ambiguous = false;
            for &si in &member_spans {
                let sr = &span_rects[si];
                let cx = (sr.x0 + sr.x1) / 2.0;
                let cy = (sr.y0 + sr.y1) / 2.0;
                let mut hit: Option<(usize, usize)> = None;
                for (ri, row) in table.cells.iter().enumerate() {
                    for (ci, cell) in row.iter().enumerate() {
                        if cx >= cell.x0 && cx <= cell.x1 && cy >= cell.y0 && cy <= cell.y1 {
                            if hit.is_some() {
                                ambiguous = true;
                            }
                            hit = Some((ri, ci));
                        }
                    }
                }
                match hit {
                    Some((ri, ci)) if !ambiguous => block_runs.push(AssignedRun {
                        row: ri,
                        col: ci,
                        x0: sr.x0,
                        y0: sr.y0,
                        text: spans[si].text.clone(),
                    }),
                    _ => {
                        ambiguous = true;
                        break;
                    },
                }
            }

            // Character accounting: every non-whitespace character of the
            // block text must be represented by the assigned runs.
            let block_chars = non_ws_chars(block.text);
            let run_chars: usize = block_runs.iter().map(|r| non_ws_chars(&r.text)).sum();
            let run_text: String = block_runs.iter().map(|run| run.text.as_str()).collect();
            if ambiguous
                || block_chars == 0
                || run_chars != block_chars
                || non_ws_multiset(block.text) != non_ws_multiset(&run_text)
            {
                result.retained_ambiguous.push(block_index);
                continue;
            }

            result.consumed.push(ConsumedBlock {
                block_index,
                original_type: block.type_label.clone(),
                text: block.text.to_string(),
                bbox: block.bbox,
                table_order: table_idx,
                cells: {
                    let mut cells: Vec<(usize, usize)> =
                        block_runs.iter().map(|r| (r.row, r.col)).collect();
                    cells.sort_unstable();
                    cells.dedup();
                    cells
                },
            });
            for (row, cells) in table.cells.iter().enumerate() {
                for (col, cell) in cells.iter().enumerate() {
                    let cell_rect = TopRect {
                        x0: cell.x0,
                        y0: cell.y0,
                        x1: cell.x1,
                        y1: cell.y1,
                    };
                    if cell_rect.intersection_area(&br) > 0.0 {
                        cells_to_clear.insert((row, col));
                    }
                }
            }
            consumed_indices.push(block_index);
            assigned_runs.extend(block_runs);
        }

        // Rebuild text for every cell that received runs from consumed blocks.
        if !assigned_runs.is_empty() {
            // Remove lossy detector fragments only where an exactly-accounted
            // consumed block geometrically touched the cell. Ambiguous blocks
            // never reach this set and therefore always fail open unchanged.
            for (row, col) in cells_to_clear {
                table.cells[row][col].text.clear();
            }
            assigned_runs.sort_by(|a, b| {
                (a.row, a.col)
                    .cmp(&(b.row, b.col))
                    .then(a.y0.partial_cmp(&b.y0).unwrap_or(std::cmp::Ordering::Equal))
                    .then(a.x0.partial_cmp(&b.x0).unwrap_or(std::cmp::Ordering::Equal))
            });
            let mut idx = 0;
            while idx < assigned_runs.len() {
                let (row, col) = (assigned_runs[idx].row, assigned_runs[idx].col);
                let mut end = idx;
                while end < assigned_runs.len()
                    && assigned_runs[end].row == row
                    && assigned_runs[end].col == col
                {
                    end += 1;
                }
                // Join runs on the same baseline with spaces, distinct
                // baselines with newlines (baseline tolerance 2pt).
                let mut lines: Vec<String> = Vec::new();
                let mut line = String::new();
                let mut line_y = f64::NEG_INFINITY;
                for run in &assigned_runs[idx..end] {
                    if line.is_empty() || (run.y0 - line_y).abs() <= 2.0 {
                        if !line.is_empty() {
                            line.push(' ');
                        }
                    } else {
                        lines.push(std::mem::take(&mut line));
                    }
                    line.push_str(run.text.trim());
                    line_y = run.y0;
                }
                if !line.is_empty() {
                    lines.push(line);
                }
                table.cells[row][col].text = lines.join("\n");
                idx = end;
            }
        }
    }

    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extractors::block_classifier::{BlockClassifier, BlockType};
    use crate::geometry::Rect;
    use crate::layout::text_block::{Color, FontWeight};
    use crate::tables::{Flavor, Table};

    const PAGE_H: f64 = 792.0;

    fn span(text: &str, x: f32, y_top: f32, w: f32, h: f32) -> TextSpan {
        TextSpan {
            text: text.to_string(),
            bbox: Rect {
                x,
                y: (PAGE_H as f32) - y_top - h,
                width: w,
                height: h,
            },
            font_name: "Helvetica".to_string(),
            font_size: 9.0,
            font_weight: FontWeight::Normal,
            is_italic: false,
            color: Color { r: 0.0, g: 0.0, b: 0.0 },
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

    fn classify(spans: &[TextSpan]) -> Vec<ClassifiedBlock> {
        BlockClassifier::new(612.0, PAGE_H as f32, spans).classify_spans(spans)
    }

    /// Two cells' worth of spans inside a 2x1 table: blocks are consumed,
    /// cell text is rebuilt from intact span text.
    #[test]
    fn consumes_contained_blocks_and_rebuilds_cells() {
        let spans = vec![
            span("CONTROL NUMBER", 100.0, 110.0, 80.0, 10.0),
            span("AC-4(11) CONFIGURATION", 100.0, 140.0, 120.0, 10.0),
        ];
        let mut blocks = classify(&spans);
        let n_before = blocks.len();
        assert!(n_before >= 1);

        let mut tables = vec![Table::new(
            vec![(90.0, 400.0)],
            vec![(100.0, 130.0), (130.0, 160.0)],
            Flavor::Lattice,
        )];
        // Lossy text the lattice path produced on its own.
        tables[0].cells[0][0].text = "CONTRL NMBER".to_string();
        tables[0].cells[1][0].text = "AC-4(11) CONFIURATIN".to_string();

        let result = reconcile_page(&mut blocks, &mut tables, &spans, PAGE_H);

        assert_eq!(result.consumed.len(), n_before, "all in-table blocks consumed");
        assert!(blocks.is_empty(), "no standalone duplicates remain");
        assert_eq!(tables[0].cells[0][0].text, "CONTROL NUMBER");
        assert_eq!(tables[0].cells[1][0].text, "AC-4(11) CONFIGURATION");
    }

    /// A block outside every table region is untouched.
    #[test]
    fn blocks_outside_tables_are_untouched() {
        let spans = vec![span("A caption below the table", 100.0, 700.0, 150.0, 10.0)];
        let mut blocks = classify(&spans);
        let n = blocks.len();
        let mut tables = vec![Table::new(
            vec![(90.0, 400.0)],
            vec![(100.0, 160.0)],
            Flavor::Lattice,
        )];
        let before = tables[0].cells[0][0].text.clone();

        let result = reconcile_page(&mut blocks, &mut tables, &spans, PAGE_H);

        assert!(result.consumed.is_empty());
        assert_eq!(blocks.len(), n);
        assert_eq!(tables[0].cells[0][0].text, before);
    }

    /// No tables: nothing happens at all.
    #[test]
    fn no_tables_no_change() {
        let spans = vec![span("Plain body text on a page", 100.0, 300.0, 150.0, 10.0)];
        let mut blocks = classify(&spans);
        let n = blocks.len();
        let result = reconcile_page(&mut blocks, &mut [], &spans, PAGE_H);
        assert!(result.consumed.is_empty());
        assert_eq!(blocks.len(), n);
    }

    /// A block whose spans cannot all be assigned to cells is retained
    /// unchanged (fail open), never partially consumed.
    #[test]
    fn ambiguous_assignment_fails_open() {
        // Span centroid lies inside the table bbox but outside every cell
        // row boundary (gap between rows).
        let spans = vec![span("ORPHAN RUN", 100.0, 131.0, 60.0, 8.0)];
        let mut blocks = classify(&spans);
        let n = blocks.len();
        assert!(n >= 1);
        let mut tables = vec![Table::new(
            vec![(90.0, 400.0)],
            vec![(100.0, 130.0), (145.0, 175.0)],
            Flavor::Lattice,
        )];

        let result = reconcile_page(&mut blocks, &mut tables, &spans, PAGE_H);

        assert!(result.consumed.is_empty());
        assert_eq!(blocks.len(), n, "fail-open retains the block");
        assert!(!result.retained_ambiguous.is_empty());
    }

    /// Character accounting: if the block text has characters no span
    /// provides, the block is retained.
    #[test]
    fn accounting_mismatch_fails_open() {
        let spans = vec![span("SHORT", 100.0, 110.0, 40.0, 10.0)];
        let mut blocks = classify(&spans);
        assert!(!blocks.is_empty());
        // Corrupt the block text so accounting cannot balance.
        blocks[0].text = "SHORT PLUS EXTRA TEXT".to_string();
        blocks[0].block_type = BlockType::Body;
        let n = blocks.len();
        let mut tables = vec![Table::new(
            vec![(90.0, 400.0)],
            vec![(100.0, 130.0)],
            Flavor::Lattice,
        )];

        let result = reconcile_page(&mut blocks, &mut tables, &spans, PAGE_H);

        assert!(result.consumed.is_empty());
        assert_eq!(blocks.len(), n);
    }

    /// Equal character counts are insufficient: different characters must
    /// fail open so reconciliation can never authorize text deletion.
    #[test]
    fn same_length_character_mismatch_fails_open() {
        let spans = vec![span("TABLE DATA", 100.0, 110.0, 70.0, 10.0)];
        let mut blocks = classify(&spans);
        assert!(!blocks.is_empty());
        blocks[0].text = "TABLF DATA".to_string();
        let n = blocks.len();
        let mut tables = vec![Table::new(
            vec![(90.0, 400.0)],
            vec![(100.0, 130.0)],
            Flavor::Lattice,
        )];

        let result = reconcile_page(&mut blocks, &mut tables, &spans, PAGE_H);

        assert!(result.consumed.is_empty());
        assert_eq!(blocks.len(), n, "same-length mismatch must retain the block");
        assert!(!result.retained_ambiguous.is_empty());
    }
}
