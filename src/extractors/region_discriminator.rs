//! Shared geometric discrimination for table and figure regions.
//!
//! Lattice table extraction and vector figure detection observe the same PDF
//! paths.  This module makes the table-vs-figure decision once, before either
//! candidate stream is materialized into the document result.  Caption text is
//! used only as an exclusive asset anchor (`Table N` versus `Figure N`); the
//! actual decision is based on path operations, grid repetition, candidate
//! table structure, text/grid alignment, connected-component regularity, and
//! label density.

use std::collections::VecDeque;

use crate::elements::{PathContent, PathOperation};
use crate::extractors::block_classifier::{BlockType, ClassifiedBlock};
use crate::geometry::Rect;
use crate::tables::Table;

const PATH_JOIN_MIN: f32 = 4.0;
const MIN_FIGURE_PATHS: usize = 6;
const MIN_FIGURE_WIDTH: f32 = 40.0;
const MIN_FIGURE_HEIGHT: f32 = 25.0;
const COORDINATE_TOLERANCE: f32 = 1.25;
const TABLE_REGION_OVERLAP: f32 = 0.65;
const TEXT_GRID_TOLERANCE: f32 = 3.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum CaptionAnchor {
    Figure(u32),
    Table(u32),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RegionClass {
    Figure,
    Table,
    Unknown,
}

#[derive(Debug, Clone)]
pub(crate) struct RegionDecision {
    pub bbox: Rect,
    pub class: RegionClass,
    pub caption: String,
    pub caption_number: u32,
}

#[derive(Debug, Default)]
struct GeometryEvidence {
    path_count: usize,
    curve_operations: usize,
    diagonal_segments: usize,
    arrow_shapes: usize,
    axis_segments: usize,
    repeated_x: usize,
    repeated_y: usize,
    connected_components: usize,
    irregular_components: usize,
    structured_tables: usize,
    aligned_text_blocks: usize,
    tiny_label_blocks: usize,
    tiny_label_area: f32,
}

pub(crate) fn caption_anchor(text: &str) -> Option<CaptionAnchor> {
    parse_number_after_prefix(text, &["figure ", "fig. ", "fig "])
        .map(CaptionAnchor::Figure)
        .or_else(|| parse_number_after_prefix(text, &["table "]).map(CaptionAnchor::Table))
}

fn parse_number_after_prefix(text: &str, prefixes: &[&str]) -> Option<u32> {
    let lower = text.trim_start().to_ascii_lowercase();
    prefixes.iter().find_map(|prefix| {
        let rest = lower.strip_prefix(prefix)?;
        let digits: String = rest.chars().take_while(char::is_ascii_digit).collect();
        digits.parse().ok()
    })
}

/// Detect and classify vector regions anchored by exclusive asset captions.
///
/// The search is not capped by a caption-height multiplier.  It follows
/// connected painted geometry through the caption's horizontal column until
/// the next same-column asset caption (or the page geometry extent).
pub(crate) fn discriminate_vector_regions(
    blocks: &[ClassifiedBlock],
    paths: &[PathContent],
    tables: &[Table],
    page_height: f32,
) -> Vec<RegionDecision> {
    let painted: Vec<&PathContent> = paths
        .iter()
        .filter(|path| path.has_stroke() || path.has_fill())
        .collect();
    if painted.len() < MIN_FIGURE_PATHS {
        return Vec::new();
    }

    blocks
        .iter()
        .filter_map(|caption| {
            if caption.block_type != BlockType::Caption {
                return None;
            }
            let anchor = caption_anchor(&caption.text)?;
            let region = connected_region_for_caption(caption, blocks, &painted, page_height)?;
            let evidence =
                collect_evidence(&region, caption, &painted, tables, blocks, page_height);
            let class = classify(anchor, &evidence, region);
            let caption_number = match anchor {
                CaptionAnchor::Figure(number) | CaptionAnchor::Table(number) => number,
            };
            Some(RegionDecision {
                bbox: region,
                class,
                caption: caption.text.clone(),
                caption_number,
            })
        })
        .collect()
}

fn classify(anchor: CaptionAnchor, evidence: &GeometryEvidence, region: Rect) -> RegionClass {
    let repeated_grid = evidence.repeated_x >= 3 && evidence.repeated_y >= 3;
    let axis_dominant = evidence.axis_segments
        >= evidence.diagonal_segments + evidence.curve_operations.saturating_mul(2);
    let grid_signature = repeated_grid
        && axis_dominant
        && (evidence.structured_tables > 0 || evidence.aligned_text_blocks >= 3);

    let figure_operation_score = usize::from(evidence.curve_operations > 0) * 4
        + usize::from(evidence.diagonal_segments >= 2) * 3
        + usize::from(evidence.arrow_shapes > 0) * 2;
    let has_sparse_tiny_labels =
        evidence.tiny_label_blocks >= 2 && evidence.tiny_label_area / region.area().max(1.0) < 0.15;
    let figure_structure_score = usize::from(evidence.irregular_components > 0) * 2
        + usize::from(has_sparse_tiny_labels) * 3;
    let figure_signature = evidence.path_count >= MIN_FIGURE_PATHS
        && region.width >= MIN_FIGURE_WIDTH
        && region.height >= MIN_FIGURE_HEIGHT
        && figure_operation_score + figure_structure_score >= 3
        && (evidence.curve_operations > 0 || has_sparse_tiny_labels);

    match anchor {
        // Asset captions are exclusive anchors.  A table caption can never
        // create a figure, and a figure caption can never create a table.
        CaptionAnchor::Table(_) if grid_signature => RegionClass::Table,
        CaptionAnchor::Table(_) => RegionClass::Unknown,
        CaptionAnchor::Figure(_) if figure_signature => RegionClass::Figure,
        CaptionAnchor::Figure(_) => RegionClass::Unknown,
    }
}

fn connected_region_for_caption(
    caption: &ClassifiedBlock,
    blocks: &[ClassifiedBlock],
    paths: &[&PathContent],
    page_height: f32,
) -> Option<Rect> {
    let caption_top = caption.bbox.y + caption.bbox.height;
    let column_left = caption.bbox.x;
    let column_right = caption.bbox.x + caption.bbox.width;
    let next_caption_y = blocks
        .iter()
        .filter(|block| {
            block.block_type == BlockType::Caption
                && block.bbox.y > caption.bbox.y
                && caption_anchor(&block.text).is_some()
                && horizontal_overlap_ratio(&caption.bbox, &block.bbox) >= 0.25
        })
        .map(|block| block.bbox.y)
        .min_by(|left, right| left.total_cmp(right))
        .unwrap_or(page_height);

    let candidates: Vec<&PathContent> = paths
        .iter()
        .copied()
        .filter(|path| {
            let path_right = path.bbox.x + path.bbox.width;
            let path_top = path.bbox.y + path.bbox.height;
            path_right >= column_left
                && path.bbox.x <= column_right
                && path_top >= caption_top
                && path.bbox.y < next_caption_y
        })
        .collect();
    if candidates.len() < MIN_FIGURE_PATHS {
        return None;
    }

    let join_distance = (caption.bbox.height * 0.8).max(PATH_JOIN_MIN);
    let components = connected_components(&candidates, join_distance);
    let nearest_bottom = components
        .iter()
        .map(|component| component_bbox(component, &candidates).y)
        .min_by(|left, right| left.total_cmp(right))?;
    let seed_band = (caption.bbox.height * 2.0).max(16.0);
    let selected: Vec<Rect> = components
        .iter()
        .map(|component| component_bbox(component, &candidates))
        .filter(|bbox| bbox.y <= nearest_bottom + seed_band)
        .collect();
    if selected.is_empty() {
        return None;
    }

    let union = selected
        .iter()
        .skip(1)
        .fold(selected[0], |bbox, component| bbox.union(component));
    let top = (union.y + union.height + caption.bbox.height * 0.75).min(next_caption_y);
    let region = Rect::from_points(column_left, caption_top, column_right, top);
    (region.width >= MIN_FIGURE_WIDTH && region.height >= MIN_FIGURE_HEIGHT).then_some(region)
}

fn connected_components(paths: &[&PathContent], gap: f32) -> Vec<Vec<usize>> {
    let mut seen = vec![false; paths.len()];
    let mut components = Vec::new();
    for start in 0..paths.len() {
        if seen[start] {
            continue;
        }
        seen[start] = true;
        let mut queue = VecDeque::from([start]);
        let mut component = Vec::new();
        while let Some(index) = queue.pop_front() {
            component.push(index);
            for candidate in 0..paths.len() {
                if !seen[candidate]
                    && rects_within_gap(&paths[index].bbox, &paths[candidate].bbox, gap)
                {
                    seen[candidate] = true;
                    queue.push_back(candidate);
                }
            }
        }
        components.push(component);
    }
    components
}

fn rects_within_gap(left: &Rect, right: &Rect, gap: f32) -> bool {
    let left_x1 = left.x + left.width;
    let left_y1 = left.y + left.height;
    let right_x1 = right.x + right.width;
    let right_y1 = right.y + right.height;
    left.x - gap <= right_x1
        && left_x1 + gap >= right.x
        && left.y - gap <= right_y1
        && left_y1 + gap >= right.y
}

fn component_bbox(component: &[usize], paths: &[&PathContent]) -> Rect {
    component
        .iter()
        .skip(1)
        .fold(paths[component[0]].bbox, |bbox, index| bbox.union(&paths[*index].bbox))
}

fn collect_evidence(
    region: &Rect,
    caption: &ClassifiedBlock,
    paths: &[&PathContent],
    tables: &[Table],
    blocks: &[ClassifiedBlock],
    page_height: f32,
) -> GeometryEvidence {
    let region_paths: Vec<&PathContent> = paths
        .iter()
        .copied()
        .filter(|path| path.bbox.intersects(region))
        .collect();
    let mut evidence = GeometryEvidence {
        path_count: region_paths.len(),
        ..GeometryEvidence::default()
    };
    let mut x_coordinates = Vec::new();
    let mut y_coordinates = Vec::new();
    for path in &region_paths {
        collect_operation_evidence(path, &mut evidence, &mut x_coordinates, &mut y_coordinates);
    }
    evidence.repeated_x = repeated_coordinate_count(&mut x_coordinates);
    evidence.repeated_y = repeated_coordinate_count(&mut y_coordinates);

    let components = connected_components(&region_paths, PATH_JOIN_MIN);
    evidence.connected_components = components.len();
    let component_boxes: Vec<Rect> = components
        .iter()
        .map(|component| component_bbox(component, &region_paths))
        .collect();
    if component_boxes.len() > 1 {
        let mean_area =
            component_boxes.iter().map(Rect::area).sum::<f32>() / component_boxes.len() as f32;
        evidence.irregular_components = component_boxes
            .iter()
            .filter(|bbox| {
                let area = bbox.area();
                area < mean_area * 0.35 || area > mean_area * 2.5
            })
            .count();
    }

    for table in tables {
        let Some(table_bbox) = table_rect(table, page_height as f64) else {
            continue;
        };
        if overlap_ratio(region, &table_bbox) < TABLE_REGION_OVERLAP {
            continue;
        }
        if table.num_rows() >= 2 && table.num_cols() >= 2 {
            evidence.structured_tables += 1;
        }
        evidence.aligned_text_blocks += blocks
            .iter()
            .filter(|block| block.block_type != BlockType::Caption)
            .filter(|block| region.intersects(&block.bbox))
            .filter(|block| block_aligns_with_table(block, table, page_height))
            .count();
    }

    for block in blocks.iter().filter(|block| region.intersects(&block.bbox)) {
        if block.font_size <= caption.font_size * 0.8 {
            evidence.tiny_label_blocks += 1;
            // A merged label block can have a large union rectangle spanning
            // sparse ticks or legends. Measure its exact source-line boxes,
            // clipped to the candidate region, instead of charging the empty
            // space between them as label ink.
            evidence.tiny_label_area += if block.lines.is_empty() {
                block.bbox.intersection(region).map_or(0.0, |bbox| bbox.area())
            } else {
                block
                    .lines
                    .iter()
                    .filter_map(|line| line.bbox.intersection(region))
                    .map(|bbox| bbox.area())
                    .sum()
            };
        }
    }
    evidence
}

fn collect_operation_evidence(
    path: &PathContent,
    evidence: &mut GeometryEvidence,
    xs: &mut Vec<f32>,
    ys: &mut Vec<f32>,
) {
    let mut current = None;
    let mut line_segments = 0;
    let mut diagonal_segments = 0;
    for operation in &path.operations {
        match *operation {
            PathOperation::MoveTo(x, y) => current = Some((x, y)),
            PathOperation::LineTo(x, y) => {
                if let Some((previous_x, previous_y)) = current {
                    let dx = (x - previous_x).abs();
                    let dy = (y - previous_y).abs();
                    if dx > COORDINATE_TOLERANCE && dy > COORDINATE_TOLERANCE {
                        evidence.diagonal_segments += 1;
                        diagonal_segments += 1;
                    } else {
                        evidence.axis_segments += 1;
                        if dx <= COORDINATE_TOLERANCE {
                            xs.extend([previous_x, x]);
                        }
                        if dy <= COORDINATE_TOLERANCE {
                            ys.extend([previous_y, y]);
                        }
                    }
                    line_segments += 1;
                }
                current = Some((x, y));
            },
            PathOperation::CurveTo(_, _, _, _, x, y) => {
                evidence.curve_operations += 1;
                current = Some((x, y));
            },
            PathOperation::Rectangle(x, y, width, height) => {
                evidence.axis_segments += 4;
                xs.extend([x, x + width]);
                ys.extend([y, y + height]);
            },
            PathOperation::ClosePath => {},
        }
    }
    if path.has_fill() && line_segments >= 2 && diagonal_segments >= 1 && path.bbox.area() <= 64.0 {
        evidence.arrow_shapes += 1;
    }
}

fn repeated_coordinate_count(coordinates: &mut [f32]) -> usize {
    coordinates.sort_by(f32::total_cmp);
    let mut repeated = 0;
    let mut index = 0;
    while index < coordinates.len() {
        let start = index;
        let anchor = coordinates[index];
        while index < coordinates.len()
            && (coordinates[index] - anchor).abs() <= COORDINATE_TOLERANCE
        {
            index += 1;
        }
        if index - start >= 2 {
            repeated += 1;
        }
    }
    repeated
}

fn table_rect(table: &Table, page_height: f64) -> Option<Rect> {
    let first_col = table.cols.first()?;
    let last_col = table.cols.last()?;
    let first_row = table.rows.first()?;
    let last_row = table.rows.last()?;
    Some(Rect::from_points(
        first_col.0 as f32,
        (page_height - last_row.1) as f32,
        last_col.1 as f32,
        (page_height - first_row.0) as f32,
    ))
}

fn block_aligns_with_table(block: &ClassifiedBlock, table: &Table, page_height: f32) -> bool {
    let center_x = block.bbox.x + block.bbox.width / 2.0;
    let center_y_top = page_height - (block.bbox.y + block.bbox.height / 2.0);
    let aligned_x = table.cols.iter().any(|(left, right)| {
        let center = ((*left + *right) / 2.0) as f32;
        (center_x - center).abs() <= block.bbox.width / 2.0 + TEXT_GRID_TOLERANCE
    });
    let aligned_y = table.rows.iter().any(|(top, bottom)| {
        let center = ((*top + *bottom) / 2.0) as f32;
        (center_y_top - center).abs() <= block.bbox.height / 2.0 + TEXT_GRID_TOLERANCE
    });
    aligned_x && aligned_y
}

fn overlap_ratio(left: &Rect, right: &Rect) -> f32 {
    let Some(intersection) = left.intersection(right) else {
        return 0.0;
    };
    let smaller = left.area().min(right.area());
    if smaller <= 0.0 {
        0.0
    } else {
        intersection.area() / smaller
    }
}

fn horizontal_overlap_ratio(left: &Rect, right: &Rect) -> f32 {
    let overlap = (left.x + left.width).min(right.x + right.width) - left.x.max(right.x);
    overlap.max(0.0) / left.width.min(right.width).max(1.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extractors::block_classifier::BlockLine;
    use crate::layout::Color;
    use crate::tables::Flavor;

    fn block(block_type: BlockType, text: &str, bbox: Rect, font_size: f32) -> ClassifiedBlock {
        ClassifiedBlock {
            lines: Vec::new(),
            block_type,
            text: text.to_string(),
            bbox,
            font_size,
            font_name: "Helvetica".to_string(),
            is_bold: false,
            confidence: 0.9,
            header_level: None,
            header_validation: None,
        }
    }

    #[test]
    fn figure_caption_and_diagonal_curve_geometry_classify_as_figure() {
        let caption = block(
            BlockType::Caption,
            "Figure 3. Architecture",
            Rect::new(50.0, 100.0, 240.0, 10.0),
            9.0,
        );
        let mut arrow = PathContent::from_operations(vec![
            PathOperation::MoveTo(100.0, 160.0),
            PathOperation::LineTo(110.0, 150.0),
            PathOperation::LineTo(105.0, 165.0),
            PathOperation::ClosePath,
        ]);
        arrow.fill_color = Some(Color::black());
        let paths = vec![
            PathContent::rect(70.0, 120.0, 50.0, 20.0),
            PathContent::rect(140.0, 145.0, 50.0, 20.0),
            PathContent::line(95.0, 140.0, 150.0, 155.0),
            PathContent::line(95.0, 140.0, 95.0, 180.0),
            PathContent::circle(190.0, 180.0, 12.0),
            arrow,
        ];

        let decisions = discriminate_vector_regions(&[caption], &paths, &[], 792.0);

        assert_eq!(decisions.len(), 1);
        assert_eq!(decisions[0].class, RegionClass::Figure);
        assert_eq!(decisions[0].caption_number, 3);
        assert_eq!(decisions[0].caption, "Figure 3. Architecture");
    }

    #[test]
    fn sparse_label_area_uses_source_lines_not_merged_union_rectangles() {
        let caption = block(
            BlockType::Caption,
            "Figure 6. Training curves",
            Rect::new(50.0, 100.0, 240.0, 10.0),
            9.0,
        );
        let mut first = block(
            BlockType::Body,
            "0 10 20",
            Rect::new(60.0, 120.0, 180.0, 50.0),
            5.0,
        );
        first.lines = vec![BlockLine {
            bbox: Rect::new(60.0, 120.0, 20.0, 5.0),
            text: "0 10 20".to_string(),
            font_size: 5.0,
            font_name: "Helvetica".to_string(),
            is_bold: false,
            span_sequences: Vec::new(),
        }];
        let mut second = block(
            BlockType::Body,
            "legend",
            Rect::new(70.0, 125.0, 170.0, 45.0),
            5.0,
        );
        second.lines = vec![BlockLine {
            bbox: Rect::new(210.0, 160.0, 20.0, 5.0),
            text: "legend".to_string(),
            font_size: 5.0,
            font_name: "Helvetica".to_string(),
            is_bold: false,
            span_sequences: Vec::new(),
        }];
        let paths = vec![
            PathContent::line(70.0, 120.0, 100.0, 140.0),
            PathContent::line(90.0, 120.0, 120.0, 145.0),
            PathContent::line(110.0, 125.0, 140.0, 150.0),
            PathContent::line(130.0, 130.0, 160.0, 155.0),
            PathContent::line(150.0, 135.0, 180.0, 160.0),
            PathContent::line(170.0, 140.0, 200.0, 165.0),
        ];

        let decisions =
            discriminate_vector_regions(&[caption, first, second], &paths, &[], 792.0);

        assert_eq!(decisions.len(), 1);
        assert_eq!(decisions[0].class, RegionClass::Figure);
        assert_eq!(decisions[0].caption_number, 6);
    }

    #[test]
    fn table_caption_and_repeated_text_aligned_grid_classify_as_table() {
        let caption = block(
            BlockType::Caption,
            "Table 1. Architectures",
            Rect::new(50.0, 100.0, 240.0, 10.0),
            9.0,
        );
        let paths = vec![
            PathContent::line(60.0, 120.0, 240.0, 120.0),
            PathContent::line(60.0, 140.0, 240.0, 140.0),
            PathContent::line(60.0, 160.0, 240.0, 160.0),
            PathContent::line(60.0, 180.0, 240.0, 180.0),
            PathContent::line(60.0, 120.0, 60.0, 180.0),
            PathContent::line(120.0, 120.0, 120.0, 180.0),
            PathContent::line(180.0, 120.0, 180.0, 180.0),
            PathContent::line(240.0, 120.0, 240.0, 180.0),
        ];
        let table = Table::new(
            vec![(60.0, 120.0), (120.0, 180.0), (180.0, 240.0)],
            vec![(612.0, 632.0), (632.0, 652.0), (652.0, 672.0)],
            Flavor::Lattice,
        );
        let labels = vec![
            caption,
            block(BlockType::Body, "A", Rect::new(80.0, 125.0, 10.0, 8.0), 8.0),
            block(BlockType::Body, "B", Rect::new(140.0, 145.0, 10.0, 8.0), 8.0),
            block(BlockType::Body, "C", Rect::new(200.0, 165.0, 10.0, 8.0), 8.0),
        ];

        let decisions = discriminate_vector_regions(&labels, &paths, &[table], 792.0);

        assert_eq!(decisions.len(), 1);
        assert_eq!(decisions[0].class, RegionClass::Table);
    }

    #[test]
    fn asset_caption_kinds_are_mutually_exclusive() {
        assert_eq!(caption_anchor("Figure 7. Responses"), Some(CaptionAnchor::Figure(7)));
        assert_eq!(caption_anchor("Table 8. Results"), Some(CaptionAnchor::Table(8)));
        assert_eq!(caption_anchor("Table 8. Figure 7"), Some(CaptionAnchor::Table(8)));
        assert_eq!(caption_anchor("ordinary prose"), None);
    }
}
