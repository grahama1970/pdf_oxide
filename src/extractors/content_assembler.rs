//! Content assembly: spatial section assignment, overlap suppression, paragraph merging.
//!
//! Absorbs S07 JSON assembler logic. Given flat sections (from block classifier +
//! section hierarchy), tables, and figures, this module:
//! 1. Assigns tables/figures to sections via spatial proximity or page range
//! 2. Suppresses text blocks that overlap >50% with tables/figures
//! 3. Merges contiguous text blocks into paragraphs, interleaving assets in reading order
//!
//! The result is an `AssembledContent` struct ready for JSON serialization.

use crate::geometry::Rect;

// ---------------------------------------------------------------------------
// Input types (lightweight, constructed from Python or Rust callers)
// ---------------------------------------------------------------------------

/// A text block from the block classifier / section hierarchy.
#[derive(Debug, Clone)]
pub struct ContentBlock {
    pub id: String,
    pub page: usize,
    pub bbox: Rect,
    pub text: String,
    pub block_type: String,
    pub section_id: Option<String>,
    pub is_equation: bool,
    pub latex_content: Option<String>,
}

/// A table extracted by Camelot or similar.
#[derive(Debug, Clone)]
pub struct ContentTable {
    pub id: String,
    pub page: usize,
    pub bbox: Rect,
    pub csv_data: String,
    pub html_data: String,
    pub section_id: Option<String>,
    pub sort_order: i64,
    pub llm_title: Option<String>,
    pub llm_description: Option<String>,
    pub image_path: Option<String>,
}

/// A detected figure.
#[derive(Debug, Clone)]
pub struct ContentFigure {
    pub id: String,
    pub page: usize,
    pub bbox: Rect,
    pub image_path: String,
    pub section_id: Option<String>,
    pub sort_order: i64,
    pub llm_title: Option<String>,
    pub llm_description: Option<String>,
}

/// A section from the flat section builder.
#[derive(Debug, Clone)]
pub struct ContentSection {
    pub id: String,
    pub title: String,
    pub page_start: usize,
    pub page_end: usize,
    pub parent_id: Option<String>,
}

// ---------------------------------------------------------------------------
// Output types
// ---------------------------------------------------------------------------

/// A merged content entry (text paragraph, table ref, figure ref, equation, section header).
#[derive(Debug, Clone)]
pub struct MergedContentEntry {
    pub id: String,
    pub section_id: String,
    pub page: usize,
    pub content_type: String, // "text", "table", "figure", "equation", "section"
    pub content: String,
    pub asset_id: Option<String>,
    pub sort_order: i64,
    pub bbox: Rect,
}

/// Full assembled content — the Rust equivalent of S07's assembled_content.json.
#[derive(Debug, Clone)]
pub struct AssembledContent {
    pub sections: Vec<ContentSection>,
    pub blocks: Vec<ContentBlock>,
    pub tables: Vec<ContentTable>,
    pub figures: Vec<ContentFigure>,
    pub merged_content: Vec<MergedContentEntry>,
}

// ---------------------------------------------------------------------------
// Sort order computation
// ---------------------------------------------------------------------------

fn calculate_sort_order(page: usize, x: f32, y: f32, page_width: f32) -> i64 {
    let mid = page_width / 2.0;
    let column: i64 = if x < mid { 0 } else { 1 };
    page as i64 * 1_000_000 + column * 100_000 + y as i64
}

// ---------------------------------------------------------------------------
// Spatial section assignment (S07's _assign_assets_to_sections)
// ---------------------------------------------------------------------------

/// Assign tables and figures to sections via spatial proximity, then page range fallback.
pub fn assign_assets_to_sections(
    blocks: &[ContentBlock],
    tables: &mut [ContentTable],
    figures: &mut [ContentFigure],
    sections: &[ContentSection],
) {
    // Build lookup: page -> blocks on that page
    let mut blocks_by_page: std::collections::HashMap<usize, Vec<&ContentBlock>> =
        std::collections::HashMap::new();
    for b in blocks {
        blocks_by_page.entry(b.page).or_default().push(b);
    }

    let assign_spatial = |page: usize,
                          bbox: &Rect,
                          blocks_by_page: &std::collections::HashMap<usize, Vec<&ContentBlock>>|
     -> Option<String> {
        let page_blocks = blocks_by_page.get(&page)?;
        if page_blocks.is_empty() {
            return None;
        }
        let best = page_blocks.iter().min_by(|a, b| {
            let da = (a.bbox.y - bbox.y).abs() + (a.bbox.x - bbox.x).abs();
            let db = (b.bbox.y - bbox.y).abs() + (b.bbox.x - bbox.x).abs();
            da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
        })?;
        best.section_id.clone()
    };

    let assign_page_range = |page: usize, sections: &[ContentSection]| -> Option<String> {
        let mut candidates: Vec<&ContentSection> = sections
            .iter()
            .filter(|s| s.page_start <= page && page <= s.page_end)
            .collect();
        if candidates.is_empty() {
            candidates = sections.iter().filter(|s| s.page_start <= page).collect();
        }
        if candidates.is_empty() {
            return None;
        }
        candidates.sort_by_key(|s| {
            let span = s.page_end.saturating_sub(s.page_start);
            (span, std::cmp::Reverse(s.page_start))
        });
        Some(candidates[0].id.clone())
    };

    for table in tables.iter_mut() {
        if table.section_id.is_some() {
            continue;
        }
        if let Some(sid) = assign_spatial(table.page, &table.bbox, &blocks_by_page) {
            table.section_id = Some(sid);
        } else if let Some(sid) = assign_page_range(table.page, sections) {
            table.section_id = Some(sid);
        }
    }

    for figure in figures.iter_mut() {
        if figure.section_id.is_some() {
            continue;
        }
        if let Some(sid) = assign_spatial(figure.page, &figure.bbox, &blocks_by_page) {
            figure.section_id = Some(sid);
        } else if let Some(sid) = assign_page_range(figure.page, sections) {
            figure.section_id = Some(sid);
        }
    }
}

// ---------------------------------------------------------------------------
// Overlap suppression (S07's _get_clean_blocks)
// ---------------------------------------------------------------------------

fn box_overlap(a: &Rect, b: &Rect) -> f32 {
    let ax1 = a.x + a.width;
    let ay1 = a.y + a.height;
    let bx1 = b.x + b.width;
    let by1 = b.y + b.height;

    let ix = (ax1.min(bx1) - a.x.max(b.x)).max(0.0);
    let iy = (ay1.min(by1) - a.y.max(b.y)).max(0.0);
    ix * iy
}

/// Remove blocks whose area overlaps >50% with any table or figure on the same page.
pub fn suppress_overlapping_blocks(
    blocks: &[ContentBlock],
    tables: &[ContentTable],
    figures: &[ContentFigure],
) -> Vec<ContentBlock> {
    blocks
        .iter()
        .filter(|b| {
            let area = b.bbox.width * b.bbox.height;
            if area <= 0.0 {
                return true;
            }
            // Check tables
            for t in tables {
                if b.page == t.page {
                    let overlap = box_overlap(&b.bbox, &t.bbox);
                    if overlap >= 0.5 * area {
                        return false;
                    }
                }
            }
            // Check figures
            for f in figures {
                if b.page == f.page {
                    let overlap = box_overlap(&b.bbox, &f.bbox);
                    if overlap >= 0.5 * area {
                        return false;
                    }
                }
            }
            true
        })
        .cloned()
        .collect()
}

// ---------------------------------------------------------------------------
// Paragraph merging with asset interleaving (S07's _merge_contiguous_blocks)
// ---------------------------------------------------------------------------

/// Merge contiguous text blocks into paragraphs, interleaving tables/figures in reading order.
pub fn merge_content(
    sections: &[ContentSection],
    blocks: &[ContentBlock],
    tables: &[ContentTable],
    figures: &[ContentFigure],
    page_width: f32,
) -> Vec<MergedContentEntry> {
    let clean_blocks = suppress_overlapping_blocks(blocks, tables, figures);

    // Index by section_id
    let mut blocks_by_sec: std::collections::HashMap<&str, Vec<&ContentBlock>> =
        std::collections::HashMap::new();
    for b in &clean_blocks {
        if let Some(ref sid) = b.section_id {
            if !b.text.trim().is_empty() {
                blocks_by_sec.entry(sid.as_str()).or_default().push(b);
            }
        }
    }

    let mut tables_by_sec: std::collections::HashMap<&str, Vec<&ContentTable>> =
        std::collections::HashMap::new();
    for t in tables {
        if let Some(ref sid) = t.section_id {
            tables_by_sec.entry(sid.as_str()).or_default().push(t);
        }
    }

    let mut figures_by_sec: std::collections::HashMap<&str, Vec<&ContentFigure>> =
        std::collections::HashMap::new();
    for f in figures {
        if let Some(ref sid) = f.section_id {
            figures_by_sec.entry(sid.as_str()).or_default().push(f);
        }
    }

    let mut merged: Vec<MergedContentEntry> = Vec::new();
    let mut content_id: usize = 0;

    for section in sections {
        let sid = section.id.as_str();

        // Build unified sorted object list: (sort_order, type, index)
        enum ObjRef<'a> {
            Block(&'a ContentBlock),
            Table(&'a ContentTable),
            Figure(&'a ContentFigure),
        }

        let mut objects: Vec<(i64, ObjRef)> = Vec::new();

        if let Some(sec_blocks) = blocks_by_sec.get(sid) {
            for b in sec_blocks {
                let so = calculate_sort_order(b.page, b.bbox.x, b.bbox.y, page_width);
                objects.push((so, ObjRef::Block(b)));
            }
        }
        if let Some(sec_tables) = tables_by_sec.get(sid) {
            for t in sec_tables {
                objects.push((t.sort_order, ObjRef::Table(t)));
            }
        }
        if let Some(sec_figures) = figures_by_sec.get(sid) {
            for f in sec_figures {
                objects.push((f.sort_order, ObjRef::Figure(f)));
            }
        }

        objects.sort_by_key(|o| o.0);

        // Merge contiguous text blocks
        let mut text_buffer: Vec<&str> = Vec::new();
        let mut text_bboxes: Vec<Rect> = Vec::new();
        let mut current_page: Option<usize> = None;
        let mut text_sort_base: i64 = 0;

        let mut flush_text = |merged: &mut Vec<MergedContentEntry>,
                              content_id: &mut usize,
                              text_buffer: &mut Vec<&str>,
                              text_bboxes: &mut Vec<Rect>,
                              current_page: &Option<usize>,
                              text_sort_base: i64,
                              section_id: &str| {
            if text_buffer.is_empty() {
                return;
            }
            let mut text = text_buffer.join(" ");
            // Hyphen removal (line-break hyphens)
            text = text.replace("- ", "");
            // Collapse whitespace
            text = collapse_whitespace(&text);

            let envelope = envelope_bbox(&text_bboxes);
            *content_id += 1;
            merged.push(MergedContentEntry {
                id: format!("mc_{}", content_id),
                section_id: section_id.to_string(),
                page: current_page.unwrap_or(0),
                content_type: "text".to_string(),
                content: text,
                asset_id: None,
                sort_order: text_sort_base,
                bbox: envelope,
            });
            text_buffer.clear();
            text_bboxes.clear();
        };

        for (sort_order, obj) in &objects {
            match obj {
                ObjRef::Block(b) => {
                    if b.block_type == "SectionHeader" {
                        flush_text(
                            &mut merged,
                            &mut content_id,
                            &mut text_buffer,
                            &mut text_bboxes,
                            &current_page,
                            text_sort_base,
                            sid,
                        );
                        content_id += 1;
                        merged.push(MergedContentEntry {
                            id: format!("mc_{}", content_id),
                            section_id: sid.to_string(),
                            page: b.page,
                            content_type: "section".to_string(),
                            content: b.text.trim().to_string(),
                            asset_id: None,
                            sort_order: *sort_order,
                            bbox: b.bbox,
                        });
                        current_page = Some(b.page);
                        text_sort_base = sort_order + 1;
                    } else if b.is_equation {
                        flush_text(
                            &mut merged,
                            &mut content_id,
                            &mut text_buffer,
                            &mut text_bboxes,
                            &current_page,
                            text_sort_base,
                            sid,
                        );
                        content_id += 1;
                        merged.push(MergedContentEntry {
                            id: format!("mc_{}", content_id),
                            section_id: sid.to_string(),
                            page: b.page,
                            content_type: "equation".to_string(),
                            content: b.latex_content.as_deref().unwrap_or(&b.text).to_string(),
                            asset_id: Some(b.id.clone()),
                            sort_order: *sort_order,
                            bbox: b.bbox,
                        });
                        current_page = Some(b.page);
                        text_sort_base = sort_order + 1;
                    } else {
                        // Regular text — buffer for paragraph merging
                        match current_page {
                            None => {
                                current_page = Some(b.page);
                                text_sort_base = *sort_order;
                            },
                            Some(cp) if cp != b.page => {
                                flush_text(
                                    &mut merged,
                                    &mut content_id,
                                    &mut text_buffer,
                                    &mut text_bboxes,
                                    &current_page,
                                    text_sort_base,
                                    sid,
                                );
                                current_page = Some(b.page);
                                text_sort_base = *sort_order;
                            },
                            _ => {},
                        }
                        text_buffer.push(b.text.trim());
                        text_bboxes.push(b.bbox);
                    }
                },
                ObjRef::Table(t) => {
                    flush_text(
                        &mut merged,
                        &mut content_id,
                        &mut text_buffer,
                        &mut text_bboxes,
                        &current_page,
                        text_sort_base,
                        sid,
                    );
                    content_id += 1;
                    merged.push(MergedContentEntry {
                        id: format!("mc_{}", content_id),
                        section_id: sid.to_string(),
                        page: t.page,
                        content_type: "table".to_string(),
                        content: t.llm_title.as_deref().unwrap_or("Table").to_string(),
                        asset_id: Some(t.id.clone()),
                        sort_order: *sort_order,
                        bbox: t.bbox,
                    });
                    current_page = Some(t.page);
                    text_sort_base = sort_order + 1;
                },
                ObjRef::Figure(f) => {
                    flush_text(
                        &mut merged,
                        &mut content_id,
                        &mut text_buffer,
                        &mut text_bboxes,
                        &current_page,
                        text_sort_base,
                        sid,
                    );
                    content_id += 1;
                    merged.push(MergedContentEntry {
                        id: format!("mc_{}", content_id),
                        section_id: sid.to_string(),
                        page: f.page,
                        content_type: "figure".to_string(),
                        content: f.llm_title.as_deref().unwrap_or("Figure").to_string(),
                        asset_id: Some(f.id.clone()),
                        sort_order: *sort_order,
                        bbox: f.bbox,
                    });
                    current_page = Some(f.page);
                    text_sort_base = sort_order + 1;
                },
            }
        }

        // Flush remaining text
        flush_text(
            &mut merged,
            &mut content_id,
            &mut text_buffer,
            &mut text_bboxes,
            &current_page,
            text_sort_base,
            sid,
        );
    }

    merged
}

/// Compute envelope bounding box of multiple rects.
fn envelope_bbox(bboxes: &[Rect]) -> Rect {
    if bboxes.is_empty() {
        return Rect::new(0.0, 0.0, 0.0, 0.0);
    }
    let mut min_x = f32::MAX;
    let mut min_y = f32::MAX;
    let mut max_x = f32::MIN;
    let mut max_y = f32::MIN;
    for b in bboxes {
        min_x = min_x.min(b.x);
        min_y = min_y.min(b.y);
        max_x = max_x.max(b.x + b.width);
        max_y = max_y.max(b.y + b.height);
    }
    Rect::new(min_x, min_y, max_x - min_x, max_y - min_y)
}

/// Collapse runs of whitespace to single spaces.
fn collapse_whitespace(s: &str) -> String {
    let mut result = String::with_capacity(s.len());
    let mut last_was_space = false;
    for c in s.chars() {
        if c.is_whitespace() {
            if !last_was_space {
                result.push(' ');
                last_was_space = true;
            }
        } else {
            result.push(c);
            last_was_space = false;
        }
    }
    result.trim().to_string()
}

// ---------------------------------------------------------------------------
// Full assembly entry point
// ---------------------------------------------------------------------------

/// Assemble content from sections, blocks, tables, and figures.
///
/// This is the Rust equivalent of S07's `run_assemble_corpus()`.
/// Call from Python after collecting sections (from `build_flat_sections`),
/// tables (from Camelot), and figures (from figure detector).
pub fn assemble_content(
    sections: Vec<ContentSection>,
    mut blocks: Vec<ContentBlock>,
    mut tables: Vec<ContentTable>,
    mut figures: Vec<ContentFigure>,
    page_width: f32,
) -> AssembledContent {
    // 1. Assign unassigned assets to sections
    assign_assets_to_sections(&blocks, &mut tables, &mut figures, &sections);

    // 2. Merge content with overlap suppression and paragraph formation
    let merged_content = merge_content(&sections, &blocks, &tables, &figures, page_width);

    AssembledContent {
        sections,
        blocks,
        tables,
        figures,
        merged_content,
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    fn make_block(
        id: &str,
        page: usize,
        x: f32,
        y: f32,
        w: f32,
        h: f32,
        text: &str,
        section_id: &str,
    ) -> ContentBlock {
        ContentBlock {
            id: id.to_string(),
            page,
            bbox: Rect::new(x, y, w, h),
            text: text.to_string(),
            block_type: "Text".to_string(),
            section_id: Some(section_id.to_string()),
            is_equation: false,
            latex_content: None,
        }
    }

    fn make_table(id: &str, page: usize, x: f32, y: f32, w: f32, h: f32) -> ContentTable {
        ContentTable {
            id: id.to_string(),
            page,
            bbox: Rect::new(x, y, w, h),
            csv_data: "a,b\n1,2".to_string(),
            html_data: String::new(),
            section_id: None,
            sort_order: calculate_sort_order(page, x, y, 612.0),
            llm_title: Some("Test Table".to_string()),
            llm_description: None,
            image_path: None,
        }
    }

    fn make_figure(id: &str, page: usize, x: f32, y: f32, w: f32, h: f32) -> ContentFigure {
        ContentFigure {
            id: id.to_string(),
            page,
            bbox: Rect::new(x, y, w, h),
            image_path: "fig.png".to_string(),
            section_id: None,
            sort_order: calculate_sort_order(page, x, y, 612.0),
            llm_title: Some("Test Figure".to_string()),
            llm_description: None,
        }
    }

    fn make_section(id: &str, title: &str, page_start: usize, page_end: usize) -> ContentSection {
        ContentSection {
            id: id.to_string(),
            title: title.to_string(),
            page_start,
            page_end,
            parent_id: None,
        }
    }

    #[test]
    fn test_spatial_assignment() {
        let blocks = vec![make_block(
            "b1",
            0,
            10.0,
            100.0,
            400.0,
            14.0,
            "Text near table",
            "sec1",
        )];
        let mut tables = vec![make_table("t1", 0, 10.0, 120.0, 400.0, 100.0)];
        let sections = vec![make_section("sec1", "Introduction", 0, 2)];

        assign_assets_to_sections(&blocks, &mut tables, &mut [].as_mut_slice(), &sections);
        assert_eq!(tables[0].section_id.as_deref(), Some("sec1"));
    }

    #[test]
    fn test_page_range_fallback() {
        let blocks = vec![]; // No blocks on page 3
        let mut figures = vec![make_figure("f1", 3, 50.0, 200.0, 300.0, 200.0)];
        let sections = vec![
            make_section("sec1", "Intro", 0, 1),
            make_section("sec2", "Methods", 2, 5),
        ];

        assign_assets_to_sections(&blocks, &mut [].as_mut_slice(), &mut figures, &sections);
        assert_eq!(figures[0].section_id.as_deref(), Some("sec2"));
    }

    #[test]
    fn test_overlap_suppression() {
        let blocks = vec![
            make_block("b1", 0, 10.0, 100.0, 400.0, 14.0, "Keep me", "sec1"),
            make_block("b2", 0, 10.0, 120.0, 400.0, 80.0, "Overlap with table", "sec1"),
        ];
        let tables = vec![make_table("t1", 0, 10.0, 120.0, 400.0, 100.0)];

        let clean = suppress_overlapping_blocks(&blocks, &tables, &[]);
        assert_eq!(clean.len(), 1);
        assert_eq!(clean[0].id, "b1");
    }

    #[test]
    fn test_paragraph_merging() {
        let sections = vec![make_section("sec1", "Intro", 0, 0)];
        let blocks = vec![
            make_block("b1", 0, 10.0, 100.0, 400.0, 14.0, "First sentence.", "sec1"),
            make_block("b2", 0, 10.0, 114.0, 400.0, 14.0, "Second sentence.", "sec1"),
        ];

        let merged = merge_content(&sections, &blocks, &[], &[], 612.0);
        assert_eq!(merged.len(), 1);
        assert!(merged[0].content.contains("First sentence."));
        assert!(merged[0].content.contains("Second sentence."));
        assert_eq!(merged[0].content_type, "text");
    }

    #[test]
    fn test_table_interleaving() {
        let sections = vec![make_section("sec1", "Results", 0, 0)];
        let blocks = vec![
            make_block("b1", 0, 10.0, 50.0, 400.0, 14.0, "Before table.", "sec1"),
            make_block("b2", 0, 10.0, 300.0, 400.0, 14.0, "After table.", "sec1"),
        ];
        let tables = vec![{
            let mut t = make_table("t1", 0, 10.0, 100.0, 400.0, 150.0);
            t.section_id = Some("sec1".to_string());
            t
        }];

        let merged = merge_content(&sections, &blocks, &tables, &[], 612.0);
        // Should be: text("Before table."), table, text("After table.")
        assert_eq!(merged.len(), 3);
        assert_eq!(merged[0].content_type, "text");
        assert_eq!(merged[1].content_type, "table");
        assert_eq!(merged[2].content_type, "text");
    }

    #[test]
    fn test_full_assembly() {
        let sections = vec![make_section("sec1", "Intro", 0, 0)];
        let blocks = vec![make_block(
            "b1",
            0,
            10.0,
            50.0,
            400.0,
            14.0,
            "Hello world.",
            "sec1",
        )];
        let tables = vec![make_table("t1", 0, 10.0, 200.0, 400.0, 100.0)];
        let figures = vec![];

        let result = assemble_content(sections, blocks, tables, figures, 612.0);
        assert_eq!(result.sections.len(), 1);
        assert_eq!(result.tables[0].section_id.as_deref(), Some("sec1"));
        assert!(!result.merged_content.is_empty());
    }

    #[test]
    fn test_collapse_whitespace() {
        assert_eq!(collapse_whitespace("hello   world"), "hello world");
        assert_eq!(collapse_whitespace("  leading  trailing  "), "leading trailing");
        assert_eq!(collapse_whitespace("no\nnewlines\there"), "no newlines here");
    }

    #[test]
    fn test_envelope_bbox() {
        let bboxes = vec![
            Rect::new(10.0, 20.0, 100.0, 50.0),
            Rect::new(5.0, 30.0, 200.0, 40.0),
        ];
        let env = envelope_bbox(&bboxes);
        assert!((env.x - 5.0).abs() < 0.01);
        assert!((env.y - 20.0).abs() < 0.01);
        assert!((env.width - 200.0).abs() < 0.01);
        assert!((env.height - 50.0).abs() < 0.01);
    }
}
