//! Cross-page table merger (S05c replacement).
//!
//! Merges page-split tables using heuristic signals:
//!   1. "Continued" in title → auto-merge
//!   2. Column count match + horizontal alignment → merge
//!
//! The ML classifier path remains in Python; this handles the
//! deterministic heuristic fallback that runs on every PDF.

/// A table entry for merge analysis.
#[derive(Debug, Clone)]
pub struct MergeableTable {
    pub index: usize,
    pub page: usize,
    /// [x0, y0, x1, y1]
    pub bbox: [f32; 4],
    pub column_count: usize,
    pub row_count: usize,
    pub title: String,
    /// Column headers (first row values or explicit headers)
    pub headers: Vec<String>,
    /// Whether headers are numeric placeholder names ("0", "1", "2"...)
    pub headers_are_numeric: bool,
}

/// Result of a merge operation.
#[derive(Debug, Clone)]
pub struct MergeResult {
    /// Indices of tables that were merged (first = target, rest = absorbed)
    pub merged_groups: Vec<Vec<usize>>,
    /// Indices of tables that were dropped as junk
    pub junk_indices: Vec<usize>,
    /// Per-merge metadata
    pub merge_details: Vec<MergeDetail>,
}

#[derive(Debug, Clone)]
pub struct MergeDetail {
    pub target_index: usize,
    pub absorbed_index: usize,
    pub reason: String,
    pub horizontal_iou: f32,
    pub width_ratio: f32,
}

/// Filter junk tables (single-row, single-column → sentence misclassified as table).
fn is_junk_table(t: &MergeableTable) -> bool {
    if t.row_count == 0 {
        return true;
    }
    if t.row_count == 1 && t.column_count == 1 {
        return true;
    }
    false
}

/// 1-D IOU on X axis for bboxes [x0, y0, x1, y1].
fn horizontal_iou(b1: &[f32; 4], b2: &[f32; 4]) -> f32 {
    let inter = (b1[2].min(b2[2]) - b1[0].max(b2[0])).max(0.0);
    let union = b1[2].max(b2[2]) - b1[0].min(b2[0]);
    if union > 0.0 {
        inter / union
    } else {
        0.0
    }
}

/// Word-overlap cosine between two titles.
pub fn word_overlap(t1: &str, t2: &str) -> f32 {
    if t1.is_empty() || t2.is_empty() {
        return 0.0;
    }
    let w1: std::collections::HashSet<String> =
        t1.split_whitespace().map(|w| w.to_lowercase()).collect();
    let w2: std::collections::HashSet<String> =
        t2.split_whitespace().map(|w| w.to_lowercase()).collect();
    if w1.is_empty() || w2.is_empty() {
        return 0.0;
    }
    let intersection = w1.iter().filter(|w| w2.contains(*w)).count();
    intersection as f32 / ((w1.len() as f32).sqrt() * (w2.len() as f32).sqrt())
}

/// Check if two tables on consecutive pages should merge.
fn should_merge_pair(t1: &MergeableTable, t2: &MergeableTable) -> bool {
    // Signal A: "Continued" in title
    if t2.title.to_lowercase().contains("continued") {
        return true;
    }

    // Signal B: Schema match + horizontal alignment
    if t1.column_count == t2.column_count && t1.column_count > 0 {
        let width1 = t1.bbox[2] - t1.bbox[0];
        let width2 = t2.bbox[2] - t2.bbox[0];
        let width_ratio = if width1.max(width2) > 0.0 {
            width1.min(width2) / width1.max(width2)
        } else {
            0.0
        };
        let hiou = horizontal_iou(&t1.bbox, &t2.bbox);
        let headers_compatible = t2.headers_are_numeric || t1.headers == t2.headers;

        if headers_compatible && hiou > 0.5 && width_ratio > 0.9 {
            return true;
        }
    }

    false
}

/// Merge page-split tables using deterministic heuristics.
///
/// Tables must be sorted by (page, y-position) before calling.
pub fn merge_tables(tables: &[MergeableTable]) -> MergeResult {
    let junk_indices: Vec<usize> = tables
        .iter()
        .filter(|t| is_junk_table(t))
        .map(|t| t.index)
        .collect();

    let clean: Vec<&MergeableTable> = tables.iter().filter(|t| !is_junk_table(t)).collect();

    if clean.len() < 2 {
        return MergeResult {
            merged_groups: clean.iter().map(|t| vec![t.index]).collect(),
            junk_indices,
            merge_details: vec![],
        };
    }

    let mut merged_groups: Vec<Vec<usize>> = Vec::new();
    let mut merge_details: Vec<MergeDetail> = Vec::new();
    let mut skip_indices: std::collections::HashSet<usize> = std::collections::HashSet::new();

    for i in 0..clean.len() {
        if skip_indices.contains(&i) {
            continue;
        }

        let t1 = clean[i];

        // Check if t1 can chain onto the last merged group
        // (handles 3+ page chain merges where t1 follows a skipped table)
        let chain_onto_last = if let Some(last_group) = merged_groups.last() {
            if let Some(&last_idx) = last_group.last() {
                // Find the table with this index to check page consecutiveness
                if let Some(last_table) = clean.iter().find(|t| t.index == last_idx) {
                    last_table.page + 1 == t1.page && should_merge_pair(last_table, t1)
                } else {
                    false
                }
            } else {
                false
            }
        } else {
            false
        };

        if chain_onto_last {
            let last_table_idx = *merged_groups.last().unwrap().last().unwrap();
            let last_table = clean.iter().find(|t| t.index == last_table_idx).unwrap();
            let hiou = horizontal_iou(&last_table.bbox, &t1.bbox);
            let width1 = last_table.bbox[2] - last_table.bbox[0];
            let width2 = t1.bbox[2] - t1.bbox[0];
            let width_ratio = if width1.max(width2) > 0.0 {
                width1.min(width2) / width1.max(width2)
            } else {
                0.0
            };
            let reason = if t1.title.to_lowercase().contains("continued") {
                "continued_in_title".to_string()
            } else {
                format!("schema_match_aligned(iou={:.2},wr={:.2})", hiou, width_ratio)
            };

            merge_details.push(MergeDetail {
                target_index: last_table.index,
                absorbed_index: t1.index,
                reason,
                horizontal_iou: hiou,
                width_ratio,
            });
            merged_groups.last_mut().unwrap().push(t1.index);
            continue;
        }

        if i + 1 < clean.len() && !skip_indices.contains(&(i + 1)) {
            let t2 = clean[i + 1];

            // Must be consecutive pages
            if t2.page == t1.page + 1 && should_merge_pair(t1, t2) {
                let hiou = horizontal_iou(&t1.bbox, &t2.bbox);
                let width1 = t1.bbox[2] - t1.bbox[0];
                let width2 = t2.bbox[2] - t2.bbox[0];
                let width_ratio = if width1.max(width2) > 0.0 {
                    width1.min(width2) / width1.max(width2)
                } else {
                    0.0
                };
                let reason = if t2.title.to_lowercase().contains("continued") {
                    "continued_in_title".to_string()
                } else {
                    format!("schema_match_aligned(iou={:.2},wr={:.2})", hiou, width_ratio)
                };

                merge_details.push(MergeDetail {
                    target_index: t1.index,
                    absorbed_index: t2.index,
                    reason,
                    horizontal_iou: hiou,
                    width_ratio,
                });

                merged_groups.push(vec![t1.index, t2.index]);
                skip_indices.insert(i + 1);
                continue;
            }
        }

        merged_groups.push(vec![t1.index]);
    }

    MergeResult {
        merged_groups,
        junk_indices,
        merge_details,
    }
}

/// Compute merge features for external classifier consumption.
pub fn compute_merge_features(t1: &MergeableTable, t2: &MergeableTable) -> MergeFeatures {
    let width1 = t1.bbox[2] - t1.bbox[0];
    let width2 = t2.bbox[2] - t2.bbox[0];

    MergeFeatures {
        col_count_match: t1.column_count == t2.column_count && t1.column_count > 0,
        width_ratio: if width1.max(width2) > 0.0 {
            width1.min(width2) / width1.max(width2)
        } else {
            0.0
        },
        horizontal_iou: horizontal_iou(&t1.bbox, &t2.bbox),
        title_has_continued: t2.title.to_lowercase().contains("continued"),
        title_word_overlap: word_overlap(&t1.title, &t2.title),
        row_count_ratio: if t1.row_count.max(t2.row_count) > 0 {
            t1.row_count.min(t2.row_count) as f32 / t1.row_count.max(t2.row_count) as f32
        } else {
            0.0
        },
        consecutive_pages: t2.page == t1.page + 1,
    }
}

#[derive(Debug, Clone)]
pub struct MergeFeatures {
    pub col_count_match: bool,
    pub width_ratio: f32,
    pub horizontal_iou: f32,
    pub title_has_continued: bool,
    pub title_word_overlap: f32,
    pub row_count_ratio: f32,
    pub consecutive_pages: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_table(
        index: usize,
        page: usize,
        bbox: [f32; 4],
        cols: usize,
        rows: usize,
        title: &str,
    ) -> MergeableTable {
        MergeableTable {
            index,
            page,
            bbox,
            column_count: cols,
            row_count: rows,
            title: title.to_string(),
            headers: (0..cols).map(|i| format!("col_{}", i)).collect(),
            headers_are_numeric: false,
        }
    }

    #[test]
    fn test_no_merge_non_consecutive_pages() {
        let tables = vec![
            make_table(0, 0, [50.0, 100.0, 500.0, 400.0], 3, 5, "Table 1"),
            make_table(1, 5, [50.0, 100.0, 500.0, 400.0], 3, 5, "Table 2"),
        ];
        let result = merge_tables(&tables);
        assert_eq!(result.merged_groups.len(), 2);
        assert!(result.merge_details.is_empty());
    }

    #[test]
    fn test_merge_continued_title() {
        let tables = vec![
            make_table(0, 0, [50.0, 100.0, 500.0, 700.0], 3, 10, "Table 1"),
            make_table(1, 1, [50.0, 50.0, 500.0, 300.0], 3, 5, "Table 1 (Continued)"),
        ];
        let result = merge_tables(&tables);
        assert_eq!(result.merged_groups.len(), 1);
        assert_eq!(result.merged_groups[0], vec![0, 1]);
        assert_eq!(result.merge_details[0].reason, "continued_in_title");
    }

    #[test]
    fn test_merge_schema_match_aligned() {
        let tables = vec![
            make_table(0, 2, [50.0, 100.0, 500.0, 700.0], 4, 8, "Data Table"),
            make_table(1, 3, [50.0, 50.0, 500.0, 300.0], 4, 3, ""),
        ];
        let result = merge_tables(&tables);
        assert_eq!(result.merged_groups.len(), 1);
        assert_eq!(result.merged_groups[0], vec![0, 1]);
    }

    #[test]
    fn test_no_merge_different_column_count() {
        let tables = vec![
            make_table(0, 0, [50.0, 100.0, 500.0, 700.0], 3, 10, "Table A"),
            make_table(1, 1, [50.0, 50.0, 500.0, 300.0], 5, 5, "Table B"),
        ];
        let result = merge_tables(&tables);
        assert_eq!(result.merged_groups.len(), 2);
    }

    #[test]
    fn test_no_merge_misaligned() {
        let tables = vec![
            make_table(0, 0, [50.0, 100.0, 300.0, 700.0], 3, 10, "Table A"),
            make_table(1, 1, [350.0, 50.0, 550.0, 300.0], 3, 5, "Table B"),
        ];
        let result = merge_tables(&tables);
        assert_eq!(result.merged_groups.len(), 2);
    }

    #[test]
    fn test_junk_filtering() {
        let tables = vec![
            MergeableTable {
                index: 0,
                page: 0,
                bbox: [50.0, 100.0, 500.0, 120.0],
                column_count: 1,
                row_count: 1,
                title: String::new(),
                headers: vec!["0".into()],
                headers_are_numeric: true,
            },
            make_table(1, 0, [50.0, 200.0, 500.0, 600.0], 3, 10, "Real Table"),
        ];
        let result = merge_tables(&tables);
        assert_eq!(result.junk_indices, vec![0]);
        assert_eq!(result.merged_groups.len(), 1);
        assert_eq!(result.merged_groups[0], vec![1]);
    }

    #[test]
    fn test_horizontal_iou_identical() {
        let b1 = [50.0, 0.0, 500.0, 100.0];
        let b2 = [50.0, 0.0, 500.0, 100.0];
        assert!((horizontal_iou(&b1, &b2) - 1.0).abs() < 0.001);
    }

    #[test]
    fn test_horizontal_iou_partial() {
        let b1 = [50.0, 0.0, 300.0, 100.0];
        let b2 = [200.0, 0.0, 500.0, 100.0];
        let iou = horizontal_iou(&b1, &b2);
        assert!(iou > 0.0 && iou < 1.0);
    }

    #[test]
    fn test_merge_features() {
        let t1 = make_table(0, 0, [50.0, 100.0, 500.0, 700.0], 3, 10, "Table 1");
        let t2 = make_table(1, 1, [50.0, 50.0, 500.0, 300.0], 3, 5, "Table 1 (Continued)");
        let features = compute_merge_features(&t1, &t2);
        assert!(features.col_count_match);
        assert!(features.title_has_continued);
        assert!(features.consecutive_pages);
        assert!(features.horizontal_iou > 0.9);
        assert!(features.width_ratio > 0.9);
    }

    #[test]
    fn test_numeric_headers_compatible() {
        let mut t1 = make_table(0, 0, [50.0, 100.0, 500.0, 700.0], 3, 10, "Data");
        t1.headers = vec!["Name".into(), "Value".into(), "Unit".into()];
        let mut t2 = make_table(1, 1, [50.0, 50.0, 500.0, 300.0], 3, 5, "");
        t2.headers = vec!["0".into(), "1".into(), "2".into()];
        t2.headers_are_numeric = true;
        let tables = vec![t1, t2];
        let result = merge_tables(&tables);
        assert_eq!(result.merged_groups.len(), 1);
    }
}
