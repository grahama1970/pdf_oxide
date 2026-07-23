//! Figure detection and spatial analysis for PDF extraction pipeline.
//!
//! Detects figures (images) on a page, associates captions, finds context text,
//! and maps figures to their containing sections. Absorbs logic from Python S06.

use crate::document::PdfDocument;
use crate::elements::PathContent;
use crate::error::Result;
use crate::extractors::block_classifier::{BlockClassifier, BlockType, ClassifiedBlock};
use crate::geometry::Rect;

/// A vector illustration must contain several independently painted paths and
/// enough construction operations to distinguish it from rules/underlines.
const VECTOR_MIN_PATHS: usize = 6;
const VECTOR_MIN_OPERATIONS: usize = 12;
const VECTOR_MIN_SUBSTANTIAL_PATHS: usize = 2;
/// A vector figure needs genuinely two-dimensional, non-rectangular geometry.
/// Axis-aligned rules and rectangle grids are table-like even when dense.
const VECTOR_MIN_NON_RULE_PATHS: usize = 2;
const VECTOR_NON_RULE_RATIO_NUMERATOR: usize = 1;
const VECTOR_NON_RULE_RATIO_DENOMINATOR: usize = 2;
const VECTOR_COMPLEX_OPERATIONS_PER_PATH: usize = 4;
const VECTOR_TWO_DIMENSIONAL_EPSILON: f32 = 0.5;
const VECTOR_MIN_WIDTH: f32 = 40.0;
const VECTOR_MIN_HEIGHT: f32 = 25.0;
/// Restrict candidates to the band immediately above a classified caption.
const VECTOR_CAPTION_GAP_TOLERANCE: f32 = 2.0;
const VECTOR_MAX_HEIGHT_FACTOR: f32 = 20.0;
const VECTOR_HORIZONTAL_PADDING_FACTOR: f32 = 2.0;
const VECTOR_HORIZONTAL_PADDING_RATIO: f32 = 0.08;
const VECTOR_TOP_PADDING_FACTOR: f32 = 0.75;
const VECTOR_DUPLICATE_OVERLAP: f32 = 0.5;

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
    let spans = doc.extract_spans_unsorted(page).unwrap_or_default();
    let (width, height) = doc
        .get_page_info(page)
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
    let paths = doc.extract_paths(page).unwrap_or_default();
    detect_figures_from_blocks_and_paths(doc, page, blocks, &paths)
}

/// Detect raster and vector figures while reusing already-extracted page paths.
///
/// Raster figures retain the existing image-metadata path. Vector-only figures
/// are inferred from dense path geometry in the narrow band immediately above
/// a classified caption; no text vocabulary is consulted here.
pub(crate) fn detect_figures_from_blocks_and_paths(
    doc: &mut PdfDocument,
    page: usize,
    blocks: &[ClassifiedBlock],
    paths: &[PathContent],
) -> Result<Vec<DetectedFigure>> {
    let image_meta = doc.extract_image_metadata(page).unwrap_or_default();
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

    for vector_bbox in detect_vector_regions(blocks, paths) {
        // Vector marks commonly overlay raster figures. Preserve the raster
        // detector's single region rather than emitting a duplicate.
        if figures
            .iter()
            .any(|figure| overlap_ratio(&figure.bbox, &vector_bbox) >= VECTOR_DUPLICATE_OVERLAP)
        {
            continue;
        }

        let (caption, caption_number) = find_caption(blocks, &vector_bbox);
        let context_above = find_context(blocks, &vector_bbox, true);
        let context_below = find_context(blocks, &vector_bbox, false);
        let section_title = find_section(blocks, &vector_bbox);
        figures.push(DetectedFigure {
            bbox: vector_bbox,
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

/// Find dense vector regions directly above caption blocks.
///
/// The caption contributes only a geometric anchor and hard lower boundary.
/// The candidate decision itself uses path geometry and paint operations only.
fn detect_vector_regions(blocks: &[ClassifiedBlock], paths: &[PathContent]) -> Vec<Rect> {
    let painted_paths: Vec<&PathContent> = paths
        .iter()
        .filter(|path| path.has_stroke() || path.has_fill())
        .collect();
    if painted_paths.len() < VECTOR_MIN_PATHS {
        return Vec::new();
    }

    let mut regions = Vec::new();
    for caption in blocks.iter().filter(|block| {
        block.block_type == BlockType::Caption && parse_figure_number(&block.text).is_some()
    }) {
        let caption_top = caption.bbox.y + caption.bbox.height;
        let search_top = caption_top + caption.bbox.height * VECTOR_MAX_HEIGHT_FACTOR;
        let horizontal_margin = caption.bbox.height * VECTOR_HORIZONTAL_PADDING_FACTOR;
        let search_left = caption.bbox.x - horizontal_margin;
        let search_right = caption.bbox.x + caption.bbox.width + horizontal_margin;

        let candidates: Vec<&PathContent> = painted_paths
            .iter()
            .copied()
            .filter(|path| {
                let path_left = path.bbox.x;
                let path_right = path.bbox.x + path.bbox.width;
                let path_bottom = path.bbox.y;
                let path_top = path.bbox.y + path.bbox.height;
                path_right >= search_left
                    && path_left <= search_right
                    && path_bottom >= caption_top - VECTOR_CAPTION_GAP_TOLERANCE
                    && path_bottom <= search_top
                    && path_top >= caption_top
            })
            .collect();

        if candidates.len() < VECTOR_MIN_PATHS {
            continue;
        }
        let operation_count: usize = candidates.iter().map(|path| path.operations.len()).sum();
        if operation_count < VECTOR_MIN_OPERATIONS {
            continue;
        }
        let substantial_paths = candidates
            .iter()
            .filter(|path| path.bbox.width.max(path.bbox.height) >= caption.bbox.height)
            .count();
        if substantial_paths < VECTOR_MIN_SUBSTANTIAL_PATHS {
            continue;
        }
        let path_union = candidates
            .iter()
            .skip(1)
            .fold(candidates[0].bbox, |bbox, path| bbox.union(&path.bbox));
        if path_union.width < VECTOR_MIN_WIDTH || path_union.height < VECTOR_MIN_HEIGHT {
            continue;
        }

        // Expand just enough to cover labels immediately outside path strokes,
        // while the caption top remains a hard lower boundary. Horizontal
        // expansion is capped by the caption extent.
        let horizontal_padding = (caption.bbox.height * VECTOR_HORIZONTAL_PADDING_FACTOR)
            .max(path_union.width * VECTOR_HORIZONTAL_PADDING_RATIO);
        let left = (path_union.x - horizontal_padding).max(caption.bbox.x);
        let right = (path_union.x + path_union.width + horizontal_padding)
            .min(caption.bbox.x + caption.bbox.width);
        let top =
            path_union.y + path_union.height + caption.bbox.height * VECTOR_TOP_PADDING_FACTOR;
        let region = Rect::from_points(left, caption_top, right, top);
        if region.width < VECTOR_MIN_WIDTH || region.height < VECTOR_MIN_HEIGHT {
            continue;
        }

        // Judge only geometry inside the bounded output region. Paths elsewhere
        // in the broad caption search band must not make a clipped rectangle
        // grid look illustrative.
        let region_paths: Vec<&PathContent> = candidates
            .iter()
            .copied()
            .filter(|path| path.bbox.intersects(&region))
            .collect();
        let two_dimensional_paths: Vec<&PathContent> = region_paths
            .iter()
            .copied()
            .filter(|path| {
                path.bbox.width.abs() >= VECTOR_TWO_DIMENSIONAL_EPSILON
                    && path.bbox.height.abs() >= VECTOR_TWO_DIMENSIONAL_EPSILON
            })
            .collect();
        let non_rule_paths = two_dimensional_paths
            .iter()
            .filter(|path| !path.is_rectangle())
            .count();
        let region_operation_count: usize =
            region_paths.iter().map(|path| path.operations.len()).sum();
        let has_non_rule_majority = non_rule_paths * VECTOR_NON_RULE_RATIO_DENOMINATOR
            > two_dimensional_paths.len() * VECTOR_NON_RULE_RATIO_NUMERATOR;
        let has_complex_construction =
            region_operation_count > region_paths.len() * VECTOR_COMPLEX_OPERATIONS_PER_PATH;
        if non_rule_paths >= VECTOR_MIN_NON_RULE_PATHS
            && (has_non_rule_majority || has_complex_construction)
        {
            regions.push(region);
        }
    }

    regions
}

fn overlap_ratio(a: &Rect, b: &Rect) -> f32 {
    let Some(intersection) = a.intersection(b) else {
        return 0.0;
    };
    let smaller_area = a.area().min(b.area());
    if smaller_area <= 0.0 {
        0.0
    } else {
        intersection.area() / smaller_area
    }
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
        },
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

    context_blocks
        .iter()
        .take(2)
        .map(|b| b.text.as_str())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Find the nearest preceding Title block (section this figure belongs to).
fn find_section(blocks: &[ClassifiedBlock], fig_bbox: &Rect) -> Option<String> {
    let fig_top = fig_bbox.y;

    blocks
        .iter()
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
                lines: Vec::new(),
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
                lines: Vec::new(),
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
                lines: Vec::new(),
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
                lines: Vec::new(),
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
        let blocks = vec![ClassifiedBlock {
            lines: Vec::new(),
            block_type: BlockType::Body,
            text: "Just body".to_string(),
            bbox: Rect::new(10.0, 500.0, 400.0, 14.0),
            font_size: 11.0,
            font_name: "Arial".to_string(),
            is_bold: false,
            confidence: 0.9,
            header_level: None,
            header_validation: None,
        }];
        let fig_bbox = Rect::new(50.0, 200.0, 200.0, 80.0);
        let (caption, num) = find_caption(&blocks, &fig_bbox);
        assert!(caption.is_none());
        assert!(num.is_none());
    }

    fn classified_block(block_type: BlockType, text: &str, bbox: Rect) -> ClassifiedBlock {
        ClassifiedBlock {
            lines: Vec::new(),
            block_type,
            text: text.to_string(),
            bbox,
            font_size: 9.0,
            font_name: "Helvetica".to_string(),
            is_bold: false,
            confidence: 0.9,
            header_level: None,
            header_validation: None,
        }
    }

    #[test]
    fn dense_vector_geometry_above_caption_creates_bounded_region() {
        let blocks = vec![
            classified_block(
                BlockType::Caption,
                "Figure 1. caption",
                Rect::new(100.0, 200.0, 220.0, 10.0),
            ),
            classified_block(
                BlockType::Body,
                "body below caption",
                Rect::new(100.0, 170.0, 220.0, 20.0),
            ),
        ];
        let paths = vec![
            PathContent::rect(130.0, 225.0, 60.0, 30.0),
            PathContent::rect(220.0, 250.0, 60.0, 30.0),
            PathContent::line(130.0, 220.0, 280.0, 220.0),
            PathContent::line(130.0, 220.0, 130.0, 290.0),
            PathContent::line(190.0, 240.0, 220.0, 265.0),
            PathContent::line(190.0, 255.0, 280.0, 280.0),
            PathContent::line(220.0, 250.0, 280.0, 220.0),
        ];

        let regions = detect_vector_regions(&blocks, &paths);

        assert_eq!(regions.len(), 1);
        assert_eq!(regions[0].y, 210.0);
        assert!(regions[0].x <= 130.0);
        assert!(regions[0].right() >= 280.0);
        assert!(regions[0].bottom() > 290.0);
    }

    #[test]
    fn vector_region_starts_above_caption_and_excludes_body_below() {
        let caption = classified_block(
            BlockType::Caption,
            "Figure 1. caption",
            Rect::new(100.0, 200.0, 220.0, 10.0),
        );
        let body = classified_block(
            BlockType::Body,
            "body below caption",
            Rect::new(100.0, 175.0, 220.0, 20.0),
        );
        let paths = vec![
            PathContent::rect(130.0, 225.0, 60.0, 30.0),
            PathContent::rect(220.0, 250.0, 60.0, 30.0),
            PathContent::line(130.0, 220.0, 280.0, 220.0),
            PathContent::line(130.0, 220.0, 130.0, 290.0),
            PathContent::line(190.0, 240.0, 220.0, 265.0),
            PathContent::line(190.0, 255.0, 280.0, 280.0),
            PathContent::line(220.0, 250.0, 280.0, 220.0),
        ];

        let regions = detect_vector_regions(&[caption.clone(), body.clone()], &paths);

        assert_eq!(regions.len(), 1);
        assert!(!regions[0].intersects(&caption.bbox));
        assert!(!regions[0].intersects(&body.bbox));
    }

    #[test]
    fn dense_rules_above_non_figure_caption_do_not_create_figure() {
        let caption = classified_block(
            BlockType::Caption,
            "Table 1. Measurements",
            Rect::new(100.0, 200.0, 220.0, 10.0),
        );
        let paths = vec![
            PathContent::rect(130.0, 225.0, 60.0, 30.0),
            PathContent::rect(220.0, 250.0, 60.0, 30.0),
            PathContent::line(130.0, 220.0, 280.0, 220.0),
            PathContent::line(130.0, 220.0, 130.0, 290.0),
            PathContent::line(190.0, 240.0, 220.0, 265.0),
            PathContent::line(190.0, 255.0, 280.0, 280.0),
            PathContent::line(220.0, 250.0, 280.0, 220.0),
        ];

        assert!(detect_vector_regions(&[caption], &paths).is_empty());
    }

    #[test]
    fn dense_rules_without_figure_caption_do_not_create_figure() {
        let body = classified_block(
            BlockType::Body,
            "Ordinary standards prose",
            Rect::new(100.0, 200.0, 220.0, 10.0),
        );
        let paths = vec![
            PathContent::rect(130.0, 225.0, 60.0, 30.0),
            PathContent::rect(220.0, 250.0, 60.0, 30.0),
            PathContent::line(130.0, 220.0, 280.0, 220.0),
            PathContent::line(130.0, 220.0, 130.0, 290.0),
            PathContent::line(190.0, 240.0, 220.0, 265.0),
            PathContent::line(190.0, 255.0, 280.0, 280.0),
            PathContent::line(220.0, 250.0, 280.0, 220.0),
        ];

        assert!(detect_vector_regions(&[body], &paths).is_empty());
    }

    #[test]
    fn rectangular_rule_grid_above_figure_caption_is_not_a_vector_figure() {
        let caption = classified_block(
            BlockType::Caption,
            "Figure 1. rule grid",
            Rect::new(100.0, 200.0, 220.0, 10.0),
        );
        let paths = vec![
            PathContent::rect(110.0, 220.0, 50.0, 30.0),
            PathContent::rect(160.0, 220.0, 50.0, 30.0),
            PathContent::rect(210.0, 220.0, 50.0, 30.0),
            PathContent::rect(260.0, 220.0, 50.0, 30.0),
            PathContent::line(110.0, 220.0, 310.0, 220.0),
            PathContent::line(110.0, 250.0, 310.0, 250.0),
            PathContent::line(110.0, 220.0, 110.0, 250.0),
        ];

        assert!(detect_vector_regions(&[caption], &paths).is_empty());
    }
}
