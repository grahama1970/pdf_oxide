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
                }
                BlockType::Footer => {
                    *footer_counts.entry(normalized.clone()).or_default() += 1;
                }
                _ => {}
            }
        }
    }

    // Mark blocks appearing on 3+ pages
    for blocks in page_blocks.iter_mut() {
        for block in blocks.iter_mut() {
            let normalized = block.text.trim().to_lowercase();
            if let Some(&count) = header_counts.get(&normalized) {
                if count >= 3 && matches!(block.block_type, BlockType::Header | BlockType::PageNumber) {
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

    blocks.iter()
        .enumerate()
        .filter(|(idx, _)| keep[*idx])
        .map(|(_, b)| b.clone())
        .collect()
}

/// Merge consecutive Body blocks separated by <1.5x font_size into paragraphs.
fn merge_paragraphs(blocks: &[ClassifiedBlock]) -> Vec<MergedBlock> {
    let mut result: Vec<MergedBlock> = Vec::new();
    let mut paragraph_id: usize = 0;

    for block in blocks {
        if block.block_type == BlockType::Body {
            // Check if we can merge with the previous block
            if let Some(prev) = result.last_mut() {
                if prev.block_type == BlockType::Body {
                    let vertical_gap = vertical_distance(&prev.bbox, &block.bbox);
                    let threshold = prev.font_size.max(block.font_size) * 1.5;

                    if vertical_gap < threshold {
                        // Merge into existing paragraph
                        prev.text.push(' ');
                        prev.text.push_str(&block.text);
                        prev.bbox = prev.bbox.union(&block.bbox);
                        continue;
                    }
                }
            }
        }

        // New block / new paragraph
        if block.block_type == BlockType::Body {
            paragraph_id += 1;
        }
        result.push(MergedBlock::from_classified(block, paragraph_id));
    }

    result
}

/// Vertical distance between two rects (gap between bottom of a and top of b).
fn vertical_distance(a: &Rect, b: &Rect) -> f32 {
    let a_bottom = a.y + a.height;
    let b_top = b.y;
    // In PDF coordinates, Y increases downward in our Rect
    (b_top - a_bottom).abs()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_block(text: &str, x: f32, y: f32, w: f32, h: f32, block_type: BlockType) -> ClassifiedBlock {
        ClassifiedBlock {
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
            make_block("Third sentence.", 10.0, 38.0, 400.0, 14.0, BlockType::Body),  // gap = 0, < 1.5*11
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
        let mut pages: Vec<Vec<MergedBlock>> = (0..4).map(|_| {
            vec![
                MergedBlock {
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
                },
            ]
        }).collect();

        mark_running_headers_footers(&mut pages);

        for page in &pages {
            assert!(page[0].is_running_header);
        }
    }
}
