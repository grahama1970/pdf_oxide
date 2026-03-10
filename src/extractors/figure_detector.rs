//! Figure detection and spatial analysis for PDF extraction pipeline.
//!
//! Detects figures (images) on a page, associates captions, finds context text,
//! and maps figures to their containing sections. Absorbs logic from Python S06.

use crate::document::PdfDocument;
use crate::error::Result;
use crate::extractors::block_classifier::{BlockClassifier, BlockType, ClassifiedBlock};
use crate::geometry::Rect;

/// A detected figure with associated metadata.
#[derive(Debug, Clone)]
pub struct DetectedFigure {
    pub bbox: Rect,
    pub page: usize,
    pub caption: Option<String>,
    pub caption_number: Option<u32>,
    pub context_above: String,
    pub context_below: String,
    pub section_title: Option<String>,
}

/// Detect figures on a page by finding images and associating captions/context.
pub fn detect_figures(doc: &mut PdfDocument, page: usize) -> Result<Vec<DetectedFigure>> {
    // Quick metadata check — no stream decompression
    let image_meta = doc.extract_image_metadata(page).unwrap_or_default();
    if image_meta.is_empty() {
        return Ok(vec![]);
    }

    let spans = doc.extract_spans_unsorted(page).unwrap_or_default();
    let (width, height) = doc.get_page_info(page)
        .ok()
        .map(|info| (info.media_box.width, info.media_box.height))
        .unwrap_or((612.0, 792.0));

    let classifier = BlockClassifier::new(width, height, &spans);
    let blocks = classifier.classify_spans(&spans);

    detect_figures_from_blocks(doc, page, &blocks)
}

/// Detect figures using pre-classified blocks (avoids re-extracting spans).
///
/// Uses `extract_image_metadata()` which reads XObject dictionaries WITHOUT
/// decompressing image streams — ~100x faster than full `extract_images()`.
pub fn detect_figures_from_blocks(
    doc: &mut PdfDocument,
    page: usize,
    blocks: &[ClassifiedBlock],
) -> Result<Vec<DetectedFigure>> {
    let image_meta = doc.extract_image_metadata(page).unwrap_or_default();
    if image_meta.is_empty() {
        return Ok(vec![]);
    }

    let mut figures = Vec::new();

    for meta in &image_meta {
        let img_bbox = meta.bbox;

        if img_bbox.width < 50.0 || img_bbox.height < 50.0 {
            continue;
        }

        // Find closest caption (block with type Caption near this image)
        let (caption, caption_number) = find_caption(blocks, &img_bbox);

        // Find context text above and below
        let context_above = find_context(blocks, &img_bbox, true);
        let context_below = find_context(blocks, &img_bbox, false);

        // Find containing section (nearest preceding Title)
        let section_title = find_section(blocks, &img_bbox);

        figures.push(DetectedFigure {
            bbox: img_bbox,
            page,
            caption,
            caption_number,
            context_above,
            context_below,
            section_title,
        });
    }

    Ok(figures)
}

/// Find the closest Caption block to the figure bbox.
fn find_caption(blocks: &[ClassifiedBlock], fig_bbox: &Rect) -> (Option<String>, Option<u32>) {
    let mut best: Option<(&ClassifiedBlock, f32)> = None;

    for block in blocks {
        if block.block_type != BlockType::Caption {
            continue;
        }
        let dist = vertical_center_distance(fig_bbox, &block.bbox);
        if dist < fig_bbox.height * 2.0 {
            if best.is_none() || dist < best.unwrap().1 {
                best = Some((block, dist));
            }
        }
    }

    match best {
        Some((block, _)) => {
            let number = parse_figure_number(&block.text);
            (Some(block.text.clone()), number)
        }
        None => (None, None),
    }
}

/// Parse "Figure N" or "Fig. N" from caption text.
fn parse_figure_number(text: &str) -> Option<u32> {
    let lower = text.to_lowercase();
    for prefix in &["figure ", "fig. ", "fig "] {
        if let Some(rest) = lower.strip_prefix(prefix) {
            // Take digits after the prefix
            let num_str: String = rest.chars().take_while(|c| c.is_ascii_digit()).collect();
            if let Ok(n) = num_str.parse() {
                return Some(n);
            }
        }
    }
    None
}

/// Find Body blocks near the figure (above or below) for context.
fn find_context(blocks: &[ClassifiedBlock], fig_bbox: &Rect, above: bool) -> String {
    let threshold = fig_bbox.height.max(30.0) * 2.0;
    let mut context_blocks: Vec<&ClassifiedBlock> = Vec::new();

    for block in blocks {
        if block.block_type != BlockType::Body {
            continue;
        }
        let block_center_y = block.bbox.y + block.bbox.height / 2.0;
        let fig_center_y = fig_bbox.y + fig_bbox.height / 2.0;

        if above {
            // Block is above the figure
            if block_center_y < fig_center_y && (fig_center_y - block_center_y) < threshold {
                context_blocks.push(block);
            }
        } else {
            // Block is below the figure
            if block_center_y > fig_center_y && (block_center_y - fig_center_y) < threshold {
                context_blocks.push(block);
            }
        }
    }

    // Take up to 2 nearest blocks
    context_blocks.sort_by(|a, b| {
        let da = vertical_center_distance(fig_bbox, &a.bbox);
        let db = vertical_center_distance(fig_bbox, &b.bbox);
        da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
    });

    context_blocks.iter()
        .take(2)
        .map(|b| b.text.as_str())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Find the nearest preceding Title block (section this figure belongs to).
fn find_section(blocks: &[ClassifiedBlock], fig_bbox: &Rect) -> Option<String> {
    let fig_top = fig_bbox.y;

    blocks.iter()
        .filter(|b| b.block_type == BlockType::Title && b.bbox.y < fig_top)
        .last()
        .map(|b| b.text.clone())
}

fn vertical_center_distance(a: &Rect, b: &Rect) -> f32 {
    let a_center = a.y + a.height / 2.0;
    let b_center = b.y + b.height / 2.0;
    (a_center - b_center).abs()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_figure_number() {
        assert_eq!(parse_figure_number("Figure 3: System diagram"), Some(3));
        assert_eq!(parse_figure_number("Fig. 12 — Results"), Some(12));
        assert_eq!(parse_figure_number("fig 1"), Some(1));
        assert_eq!(parse_figure_number("Table 5"), None);
    }

    #[test]
    fn test_find_caption() {
        let blocks = vec![
            ClassifiedBlock {
                block_type: BlockType::Body,
                text: "Some body text".to_string(),
                bbox: Rect::new(10.0, 10.0, 400.0, 14.0),
                font_size: 11.0,
                font_name: "Arial".to_string(),
                is_bold: false,
                confidence: 0.9,
                header_level: None,
                header_validation: None,
            },
            ClassifiedBlock {
                block_type: BlockType::Caption,
                text: "Figure 3: Architecture diagram".to_string(),
                bbox: Rect::new(10.0, 300.0, 300.0, 12.0),
                font_size: 10.0,
                font_name: "Arial".to_string(),
                is_bold: false,
                confidence: 0.8,
                header_level: None,
                header_validation: None,
            },
        ];
        let fig_bbox = Rect::new(50.0, 200.0, 200.0, 80.0);
        let (caption, num) = find_caption(&blocks, &fig_bbox);
        assert_eq!(caption.unwrap(), "Figure 3: Architecture diagram");
        assert_eq!(num, Some(3));
    }

    #[test]
    fn test_find_section() {
        let blocks = vec![
            ClassifiedBlock {
                block_type: BlockType::Title,
                text: "1. Introduction".to_string(),
                bbox: Rect::new(10.0, 50.0, 200.0, 18.0),
                font_size: 16.0,
                font_name: "Arial".to_string(),
                is_bold: true,
                confidence: 0.9,
                header_level: Some(1),
                header_validation: None,
            },
            ClassifiedBlock {
                block_type: BlockType::Body,
                text: "Body text".to_string(),
                bbox: Rect::new(10.0, 100.0, 400.0, 14.0),
                font_size: 11.0,
                font_name: "Arial".to_string(),
                is_bold: false,
                confidence: 0.9,
                header_level: None,
                header_validation: None,
            },
        ];
        let fig_bbox = Rect::new(50.0, 200.0, 200.0, 80.0);
        assert_eq!(find_section(&blocks, &fig_bbox).unwrap(), "1. Introduction");
    }

    #[test]
    fn test_no_caption_found() {
        let blocks = vec![
            ClassifiedBlock {
                block_type: BlockType::Body,
                text: "Just body".to_string(),
                bbox: Rect::new(10.0, 500.0, 400.0, 14.0),
                font_size: 11.0,
                font_name: "Arial".to_string(),
                is_bold: false,
                confidence: 0.9,
                header_level: None,
                header_validation: None,
            },
        ];
        let fig_bbox = Rect::new(50.0, 200.0, 200.0, 80.0);
        let (caption, num) = find_caption(&blocks, &fig_bbox);
        assert!(caption.is_none());
        assert!(num.is_none());
    }
}
