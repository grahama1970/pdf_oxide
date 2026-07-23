//! Table extraction from PDF pages.
//!
//! Two extraction flavors:
//! - **Lattice**: Detects tables from ruled lines in rendered page images.
//!   Best for tables with visible borders/gridlines.
//! - **Stream**: Detects tables from text element positions using the
//!   Nurminen algorithm. Best for borderless tables.
//!
//! Algorithms are based on Camelot's proven approaches, reimplemented in
//! pure Rust with improvements:
//! - No OpenCV or Ghostscript dependencies
//! - Adaptive thresholds instead of hardcoded magic numbers
//! - Phantom table filtering
//! - Multi-page strategy caching

pub mod lattice;
pub mod stream;
pub mod text_assign;
pub mod types;

pub use types::{
    BBox, Cell, CharPosition, ExtractConfig, Flavor, MergedRegion, Segment, Strategy, Table,
    TextElement,
};

use crate::document::PdfDocument;

/// Extract tables from a PDF document.
///
/// Processes the specified pages (or all pages if `config.pages` is None)
/// using the configured flavor (Lattice or Stream).
///
/// Returns tables sorted by (page, y-position).
pub fn extract_tables(doc: &mut PdfDocument, config: &ExtractConfig) -> crate::Result<Vec<Table>> {
    let page_count = doc.page_count()?;
    let pages: Vec<usize> = match &config.pages {
        Some(p) => p.iter().filter(|&&p| p < page_count).cloned().collect(),
        None => (0..page_count).collect(),
    };

    let mut all_tables = Vec::new();

    for &page_num in &pages {
        let page_tables = extract_page(doc, page_num, config)?;
        for (i, mut table) in page_tables.into_iter().enumerate() {
            table.page = page_num;
            table.order = i;
            all_tables.push(table);
        }
    }

    Ok(all_tables)
}

/// Extract tables from a single page.
fn extract_page(
    doc: &mut PdfDocument,
    page_num: usize,
    config: &ExtractConfig,
) -> crate::Result<Vec<Table>> {
    // Get page dimensions from media box
    // Rect has {x, y, width, height} where (x,y) is the origin
    let page_info = doc.get_page_info(page_num)?;
    let mb = page_info.media_box;
    let page_width = mb.width as f64;
    let page_height = mb.height as f64;
    let mb_x0 = mb.x as f64;
    let mb_y0 = mb.y as f64;

    // Extract text spans
    let spans = doc.extract_spans(page_num)?;

    // Extract characters for precise column splitting (Camelot LTChar equivalent)
    let chars = doc.extract_chars(page_num).unwrap_or_default();

    // Convert spans to TextElements with character-level positions.
    let elements: Vec<TextElement> = spans
        .iter()
        .map(|span| {
            let ox = span.bbox.x as f64;
            let oy = span.bbox.y as f64;
            let ow = span.bbox.width as f64;
            let oh = span.bbox.height as f64;

            // Normalize to (0,0)-based coords and convert to top-left origin.
            let x0 = ox - mb_x0;
            let x1 = ox - mb_x0 + ow;
            let y0_top = page_height - (oy - mb_y0) - oh;
            let y1_bottom = page_height - (oy - mb_y0);

            // Match chars back to the span so stream extraction can split cells precisely.
            let span_chars: Vec<CharPosition> = chars
                .iter()
                .filter(|ch| {
                    let ch_cx = (ch.bbox.x + ch.bbox.width / 2.0) as f64;
                    let ch_cy = (ch.bbox.y + ch.bbox.height / 2.0) as f64;
                    ch_cx >= ox - 1.0
                        && ch_cx <= ox + ow + 1.0
                        && ch_cy >= oy - 1.0
                        && ch_cy <= oy + oh + 1.0
                })
                .map(|ch| {
                    let cx0 = ch.bbox.x as f64 - mb_x0;
                    let cx1 = (ch.bbox.x + ch.bbox.width) as f64 - mb_x0;
                    CharPosition {
                        char: ch.char,
                        x0: cx0,
                        x1: cx1,
                    }
                })
                .collect();

            TextElement {
                text: span.text.clone(),
                x0,
                y0: y0_top,
                x1,
                y1: y1_bottom,
                font_size: span.font_size as f64,
                is_bold: span.font_weight.is_bold(),
                chars: if span_chars.is_empty() {
                    None
                } else {
                    Some(span_chars)
                },
            }
        })
        .collect();

    if config.strategy == Strategy::DefinitionList {
        return Ok(text_assign::extract_definition_list_table(
            &elements,
            page_width,
            page_height,
            config,
        )
        .into_iter()
        .collect());
    }

    match config.flavor {
        Flavor::Stream => Ok(stream::extract_stream(&elements, page_width, page_height, config)),
        Flavor::Lattice => {
            // Try vector path-based detection first (more accurate than rendering)
            let paths = doc.extract_paths(page_num).unwrap_or_default();
            let (h_segs, v_segs) = lattice::paths_to_segments(&paths, page_height, 5.0);

            // Horizontal-rule-delimited tables (for example booktabs) may
            // contain only a few vertical rules. Detect their full geometric
            // regions alongside lattice so a tiny lattice fragment cannot
            // prevent recovery of the real table.
            let ruled_tables = lattice::extract_ruled_regions_from_paths(
                &h_segs,
                &v_segs,
                &elements,
                page_width,
                config,
            );

            if h_segs.len() >= 2 && v_segs.len() >= 2 {
                let lattice_tables = lattice::extract_lattice_from_paths(
                    &h_segs,
                    &v_segs,
                    &elements,
                    page_width,
                    page_height,
                    config,
                );
                let tables = merge_lattice_and_ruled(lattice_tables, ruled_tables.clone());
                if !tables.is_empty() {
                    return Ok(tables);
                }
            }

            if !ruled_tables.is_empty() {
                return Ok(ruled_tables);
            }

            // Hybrid fallback: if we have horizontal lines but no vertical lines,
            // this is likely a "row-separated" table (common in government docs).
            // Try stream detection instead.
            if h_segs.len() >= 2 && v_segs.len() < 2 {
                log::debug!(
                    "Page {}: {} h_segs, {} v_segs - trying stream for row-separated table",
                    page_num,
                    h_segs.len(),
                    v_segs.len()
                );
                let stream_tables =
                    stream::extract_stream(&elements, page_width, page_height, config);
                if !stream_tables.is_empty() {
                    return Ok(stream_tables);
                }
            }

            // Fall back to image-based detection
            #[cfg(feature = "rendering")]
            {
                use crate::rendering::{render_page, RenderOptions};

                let opts = RenderOptions::with_dpi(300);
                match render_page(doc, page_num, &opts) {
                    Ok(rendered) => {
                        match image::load_from_memory(rendered.as_bytes()) {
                            Ok(img) => {
                                let gray = img.to_luma8();
                                let tables = lattice::extract_lattice(
                                    &gray,
                                    &elements,
                                    page_width,
                                    page_height,
                                    config,
                                );
                                // Final fallback to stream if image-based also fails
                                if tables.is_empty() {
                                    return Ok(stream::extract_stream(
                                        &elements,
                                        page_width,
                                        page_height,
                                        config,
                                    ));
                                }
                                Ok(tables)
                            },
                            Err(_) => Ok(stream::extract_stream(
                                &elements,
                                page_width,
                                page_height,
                                config,
                            )),
                        }
                    },
                    Err(_) => {
                        Ok(stream::extract_stream(&elements, page_width, page_height, config))
                    },
                }
            }
            #[cfg(not(feature = "rendering"))]
            {
                log::warn!(
                    "Lattice extraction requires 'rendering' feature; falling back to stream"
                );
                Ok(stream::extract_stream(&elements, page_width, page_height, config))
            }
        },
    }
}

fn table_bbox(table: &Table) -> Option<BBox> {
    Some(BBox::new(
        table.cols.first()?.0,
        table.rows.first()?.0,
        table.cols.last()?.1,
        table.rows.last()?.1,
    ))
}

fn intersection_area(a: BBox, b: BBox) -> f64 {
    let width = (a.x1.min(b.x1) - a.x0.max(b.x0)).max(0.0);
    let height = (a.y1.min(b.y1) - a.y0.max(b.y0)).max(0.0);
    width * height
}

fn bbox_area(bbox: BBox) -> f64 {
    bbox.width().max(0.0) * bbox.height().max(0.0)
}

/// Prefer a complete lattice grid when it covers the ruled region. Otherwise
/// retain the larger horizontal-rule region and suppress lattice fragments it
/// geometrically contains, preventing duplicate table output.
fn merge_lattice_and_ruled(lattice_tables: Vec<Table>, ruled_tables: Vec<Table>) -> Vec<Table> {
    let mut accepted_ruled = Vec::new();
    for ruled in ruled_tables {
        let Some(ruled_bbox) = table_bbox(&ruled) else {
            continue;
        };
        let covered_by_lattice = lattice_tables.iter().any(|lattice| {
            table_bbox(lattice).is_some_and(|lattice_bbox| {
                intersection_area(ruled_bbox, lattice_bbox) / bbox_area(ruled_bbox).max(1.0) >= 0.90
            })
        });
        if !covered_by_lattice {
            accepted_ruled.push(ruled);
        }
    }

    let mut merged: Vec<Table> = lattice_tables
        .into_iter()
        .filter(|lattice| {
            let Some(lattice_bbox) = table_bbox(lattice) else {
                return true;
            };
            !accepted_ruled.iter().any(|ruled| {
                table_bbox(ruled).is_some_and(|ruled_bbox| {
                    intersection_area(lattice_bbox, ruled_bbox)
                        / bbox_area(lattice_bbox).max(1.0)
                        >= 0.90
                })
            })
        })
        .collect();
    merged.extend(accepted_ruled);
    merged.sort_by(|a, b| {
        let ay = a.rows.first().map(|row| row.0).unwrap_or(f64::INFINITY);
        let by = b.rows.first().map(|row| row.0).unwrap_or(f64::INFINITY);
        ay.partial_cmp(&by).unwrap_or(std::cmp::Ordering::Equal)
    });
    merged
}

#[cfg(test)]
mod ruled_merge_tests {
    use super::*;

    #[test]
    fn full_ruled_region_supersedes_enclosed_lattice_fragment() {
        let fragment = Table::new(
            vec![(120.0, 180.0), (180.0, 240.0)],
            vec![(110.0, 130.0), (130.0, 150.0)],
            Flavor::Lattice,
        );
        let ruled = Table::new(
            vec![(50.0, 150.0), (150.0, 250.0), (250.0, 350.0)],
            vec![(100.0, 125.0), (125.0, 150.0), (150.0, 175.0)],
            Flavor::Lattice,
        );

        let merged = merge_lattice_and_ruled(vec![fragment], vec![ruled]);

        assert_eq!(merged.len(), 1, "enclosed fragment must not be duplicated");
        assert_eq!(merged[0].num_rows(), 3);
        assert_eq!(merged[0].num_cols(), 3);
        assert_eq!(table_bbox(&merged[0]).unwrap().x0, 50.0);
    }
}
