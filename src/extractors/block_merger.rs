//! Block merging and overlap suppression for PDF extraction pipeline.
//!
//! Absorbs logic from Python S07_duckdb_ingest: overlap suppression,
//! paragraph formation, running header/footer dedup.

use crate::extractors::block_classifier::{BlockType, ClassifiedBlock};
use crate::geometry::Rect;

/// A merged block with additional metadata from the merging process.
#[derive(Debug, Clone)]
pub struct MergedBlock {
    pub block_type: BlockType,
    pub text: String,
    pub bbox: Rect,
    pub font_size: f32,
    pub font_name: String,
    pub is_bold: bool,
    pub confidence: f32,
    pub header_level: Option<u8>,
    pub paragraph_id: usize,
    pub is_running_header: bool,
    pub is_running_footer: bool,
}

impl MergedBlock {
    fn from_classified(block: &ClassifiedBlock, paragraph_id: usize) -> Self {
        MergedBlock {
            block_type: block.block_type,
            text: block.text.clone(),
            bbox: block.bbox,
            font_size: block.font_size,
            font_name: block.font_name.clone(),
            is_bold: block.is_bold,
            confidence: block.confidence,
            header_level: block.header_level,
            paragraph_id,
            is_running_header: false,
            is_running_footer: false,
        }
    }
}

/// Merge classified blocks: suppress overlaps, form paragraphs, assign IDs.
pub fn merge_blocks(blocks: &[ClassifiedBlock], _page_height: f32) -> Vec<MergedBlock> {
    if blocks.is_empty() {
        return vec![];
    }

    // Step 1: Remove overlapping blocks (keep the one with more text)
    let deduped = suppress_overlaps(blocks);

    // Step 2: Merge consecutive body blocks into paragraphs
    merge_paragraphs(&deduped)
}

/// Mark blocks that appear as running headers/footers across pages.
///
/// Takes blocks from multiple pages. If a Header or Footer block's text
/// appears on 3+ pages, mark all instances as running_header/running_footer.
pub fn mark_running_headers_footers(page_blocks: &mut [Vec<MergedBlock>]) {
    use std::collections::HashMap;

    // Count header text occurrences
    let mut header_counts: HashMap<String, usize> = HashMap::new();
    let mut footer_counts: HashMap<String, usize> = HashMap::new();

    for blocks in page_blocks.iter() {
        for block in blocks {
            let normalized = block.text.trim().to_lowercase();
            if normalized.is_empty() {
                continue;
            }
            match block.block_type {
                BlockType::Header | BlockType::PageNumber => {
                    *header_counts.entry(normalized.clone()).or_default() += 1;
                },
                BlockType::Footer => {
                    *footer_counts.entry(normalized.clone()).or_default() += 1;
                },
                _ => {},
            }
        }
    }

    // Mark blocks appearing on 3+ pages
    for blocks in page_blocks.iter_mut() {
        for block in blocks.iter_mut() {
            let normalized = block.text.trim().to_lowercase();
            if let Some(&count) = header_counts.get(&normalized) {
                if count >= 3
                    && matches!(block.block_type, BlockType::Header | BlockType::PageNumber)
                {
                    block.is_running_header = true;
                }
            }
            if let Some(&count) = footer_counts.get(&normalized) {
                if count >= 3 && block.block_type == BlockType::Footer {
                    block.is_running_footer = true;
                }
            }
        }
    }
}

/// Compute IOU (Intersection over Union) of two rectangles.
fn bbox_iou(a: &Rect, b: &Rect) -> f32 {
    let ax1 = a.x;
    let ay1 = a.y;
    let ax2 = a.x + a.width;
    let ay2 = a.y + a.height;

    let bx1 = b.x;
    let by1 = b.y;
    let bx2 = b.x + b.width;
    let by2 = b.y + b.height;

    let ix1 = ax1.max(bx1);
    let iy1 = ay1.max(by1);
    let ix2 = ax2.min(bx2);
    let iy2 = ay2.min(by2);

    if ix2 <= ix1 || iy2 <= iy1 {
        return 0.0;
    }

    let intersection = (ix2 - ix1) * (iy2 - iy1);
    let area_a = a.width * a.height;
    let area_b = b.width * b.height;
    let union = area_a + area_b - intersection;

    if union <= 0.0 {
        return 0.0;
    }

    intersection / union
}

/// Remove blocks that overlap by >80% IOU, keeping the one with more text.
fn suppress_overlaps(blocks: &[ClassifiedBlock]) -> Vec<ClassifiedBlock> {
    let mut keep = vec![true; blocks.len()];

    for i in 0..blocks.len() {
        if !keep[i] {
            continue;
        }
        for j in (i + 1)..blocks.len() {
            if !keep[j] {
                continue;
            }
            let iou = bbox_iou(&blocks[i].bbox, &blocks[j].bbox);
            if iou > 0.8 {
                // Keep the block with more text
                if blocks[i].text.len() >= blocks[j].text.len() {
                    keep[j] = false;
                } else {
                    keep[i] = false;
                    break;
                }
            }
        }
    }

    blocks
        .iter()
        .enumerate()
        .filter(|(idx, _)| keep[*idx])
        .map(|(_, b)| b.clone())
        .collect()
}

/// Merge consecutive Body blocks separated by <1.5x font_size into paragraphs.
/// True for a short all-caps line that could be a standfirst under a title.
///
/// Deliberately does NOT decide on its own: the caller must also confirm the
/// preceding block is a Title. Typography alone cannot separate a standfirst
/// from a body-size all-caps heading.
fn is_standfirst_candidate(text: &str) -> bool {
    let trimmed = text.trim();
    if trimmed.len() > 200 {
        return false;
    }
    let letters: Vec<char> = trimmed.chars().filter(|c| c.is_alphabetic()).collect();
    if letters.len() < 8 {
        return false;
    }
    letters.iter().all(|c| c.is_uppercase())
}

/// Block types that participate in vertical run merging.
///
/// Body and Footnote flow together: a footnote's continuation lines are
/// classified Body (no leading marker), so restricting merging to Body alone
/// leaves footnote runs fragmented once footnotes are typed correctly.
fn is_flowable(block_type: BlockType) -> bool {
    matches!(block_type, BlockType::Body | BlockType::Footnote)
}

fn merge_paragraphs(blocks: &[ClassifiedBlock]) -> Vec<MergedBlock> {
    let mut result: Vec<MergedBlock> = Vec::new();
    let mut paragraph_id: usize = 0;

    for block in blocks {
        if is_flowable(block.block_type) {
            // Check if we can merge with the previous block
            if let Some(prev) = result.last_mut() {
                if is_flowable(prev.block_type) {
                    // Footnote runs need the true inter-line gap. Body paragraph
                    // merging keeps its legacy measure deliberately: with the true
                    // gap, this document's paragraph breaks (min 5.37pt) and
                    // intra-paragraph line gaps (max 4.91pt) are too close to
                    // separate with any fixed multiple of font size, and picking one
                    // that happens to split them here would be tuning to this file.
                    // Widening body merging is a separate, adaptive-threshold change.
                    let footnote_run = prev.block_type == BlockType::Footnote
                        || block.block_type == BlockType::Footnote;
                    let vertical_gap = if footnote_run {
                        vertical_gap_true(&prev.bbox, &block.bbox)
                    } else {
                        vertical_distance(&prev.bbox, &block.bbox)
                    };
                    let threshold = prev.font_size.max(block.font_size) * 1.5;

                    if vertical_gap < threshold {
                        // Merge into existing paragraph
                        prev.text.push(' ');
                        prev.text.push_str(&block.text);
                        prev.bbox = prev.bbox.union(&block.bbox);
                        // A run that contains any footnote line IS a footnote block:
                        // continuation lines of a footnote classify as Body because
                        // they carry no leading marker, and the first footnote can
                        // fall just outside the footnote band. Merging them back
                        // into one block is what makes the run addressable.
                        if block.block_type == BlockType::Footnote {
                            prev.block_type = BlockType::Footnote;
                        }
                        continue;
                    }
                }
            }
        }

        // Context-aware subtitle. A standfirst is defined by POSITION -- it
        // directly follows a title -- not by typography. NIST sets many real
        // headings ("ACCOUNTS", "CREDENTIALS") at body size in all caps, so a
        // per-block typographic rule cannot tell them apart and eats 56 of them
        // document-wide. Here the preceding block is known, so the test is exact.
        let mut block_type = block.block_type;
        if block_type == BlockType::Body && is_standfirst_candidate(&block.text) {
            if let Some(prev) = result.last() {
                match prev.block_type {
                    // Standfirst: directly under a title.
                    BlockType::Title => block_type = BlockType::Subtitle,
                    // Chapter title: the line a chapter/appendix label introduces.
                    // "INTRODUCTION" after "CHAPTER ONE" is caught by the bold
                    // heading rule, but "THE FUNDAMENTALS" and "GLOSSARY" are set
                    // unbolded and would fall through to body. Position decides.
                    BlockType::ChapterLabel if block.font_size > prev.font_size * 0.8 => {
                        block_type = BlockType::Title
                    }
                    _ => {}
                }
            }
        }

        // New block / new paragraph
        if block_type == BlockType::Body {
            paragraph_id += 1;
        }
        let mut merged = MergedBlock::from_classified(block, paragraph_id);
        merged.block_type = block_type;
        result.push(merged);
    }

    result
}

/// Vertical distance between two rects (gap between bottom of a and top of b).
fn vertical_distance(a: &Rect, b: &Rect) -> f32 {
    let a_bottom = a.y + a.height;
    let b_top = b.y;
    (b_top - a_bottom).abs()
}

/// True separation between two vertical spans, origin-agnostic.
///
/// `vertical_distance` above adds span height to the separation because Rect.y
/// is the BOTTOM edge in a bottom-origin page space; it reports 20-47pt for
/// lines 2-4pt apart. Correct for footnote runs, where real inter-line gaps
/// must be compared against the merge threshold.
fn vertical_gap_true(a: &Rect, b: &Rect) -> f32 {
    let a_lo = a.y;
    let a_hi = a.y + a.height;
    let b_lo = b.y;
    let b_hi = b.y + b.height;
    (a_lo.max(b_lo) - a_hi.min(b_hi)).max(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_block(
        text: &str,
        x: f32,
        y: f32,
        w: f32,
        h: f32,
        block_type: BlockType,
    ) -> ClassifiedBlock {
        ClassifiedBlock {
            lines: Vec::new(),
            block_type,
            text: text.to_string(),
            bbox: Rect::new(x, y, w, h),
            font_size: 11.0,
            font_name: "Arial".to_string(),
            is_bold: false,
            confidence: 0.9,
            header_level: None,
            header_validation: None,
        }
    }

    #[test]
    fn test_overlap_suppression() {
        let blocks = vec![
            make_block("Short", 10.0, 10.0, 100.0, 20.0, BlockType::Body),
            make_block("Longer text here", 10.0, 10.0, 100.0, 20.0, BlockType::Body), // same bbox, more text
        ];
        let result = suppress_overlaps(&blocks);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].text, "Longer text here");
    }

    #[test]
    fn test_no_overlap() {
        let blocks = vec![
            make_block("First", 10.0, 10.0, 100.0, 20.0, BlockType::Body),
            make_block("Second", 10.0, 100.0, 100.0, 20.0, BlockType::Body),
        ];
        let result = suppress_overlaps(&blocks);
        assert_eq!(result.len(), 2);
    }

    #[test]
    fn test_paragraph_merge() {
        let blocks = vec![
            make_block("First sentence.", 10.0, 10.0, 400.0, 14.0, BlockType::Body),
            make_block("Second sentence.", 10.0, 24.0, 400.0, 14.0, BlockType::Body), // gap = 0, < 1.5*11
            make_block("Third sentence.", 10.0, 38.0, 400.0, 14.0, BlockType::Body), // gap = 0, < 1.5*11
        ];
        let result = merge_paragraphs(&blocks);
        assert_eq!(result.len(), 1);
        assert!(result[0].text.contains("First sentence."));
        assert!(result[0].text.contains("Third sentence."));
    }

    #[test]
    fn test_paragraph_split() {
        let blocks = vec![
            make_block("Paragraph 1.", 10.0, 10.0, 400.0, 14.0, BlockType::Body),
            make_block("Paragraph 2.", 10.0, 100.0, 400.0, 14.0, BlockType::Body), // large gap
        ];
        let result = merge_paragraphs(&blocks);
        assert_eq!(result.len(), 2);
        assert_ne!(result[0].paragraph_id, result[1].paragraph_id);
    }

    #[test]
    fn test_non_body_blocks_not_merged() {
        let blocks = vec![
            make_block("Title", 10.0, 10.0, 200.0, 20.0, BlockType::Title),
            make_block("Body text.", 10.0, 32.0, 400.0, 14.0, BlockType::Body),
        ];
        let result = merge_paragraphs(&blocks);
        assert_eq!(result.len(), 2);
        assert_eq!(result[0].block_type, BlockType::Title);
    }

    #[test]
    fn test_running_headers() {
        let mut pages: Vec<Vec<MergedBlock>> = (0..4)
            .map(|_| {
                vec![MergedBlock {
                    block_type: BlockType::Header,
                    text: "Chapter 1 - Introduction".to_string(),
                    bbox: Rect::new(10.0, 10.0, 200.0, 14.0),
                    font_size: 10.0,
                    font_name: "Arial".to_string(),
                    is_bold: false,
                    confidence: 0.8,
                    header_level: None,
                    paragraph_id: 0,
                    is_running_header: false,
                    is_running_footer: false,
                }]
            })
            .collect();

        mark_running_headers_footers(&mut pages);

        for page in &pages {
            assert!(page[0].is_running_header);
        }
    }
}
