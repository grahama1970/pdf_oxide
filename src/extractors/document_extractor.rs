//! Unified document extraction orchestrator.
//!
//! Runs the full extraction pipeline in a single call: profile → classify → merge →
//! detect figures → build section hierarchy → normalize text. Returns a comprehensive
//! ExtractionResult that replaces the multi-step Python pipeline.

use serde::Serialize;

use crate::document::PdfDocument;
use crate::error::Result;
use crate::extractors::block_classifier::{BlockClassifier, BlockType, ClassifiedBlock};
use crate::extractors::block_merger::{merge_blocks, mark_running_headers_footers, MergedBlock};
use crate::extractors::document_profiler::{profile_document_with_cache, DocumentProfile};
use crate::extractors::engineering::{detect_engineering_features_from_spans, EngineeringProfile};
use crate::extractors::figure_detector::{detect_figures_from_blocks, DetectedFigure};
use crate::extractors::section_hierarchy::{build_section_hierarchy_from_classified, SectionTree};
use crate::extractors::text_normalizer::full_normalize;
use crate::layout::text_block::TextSpan;

/// Complete extraction result from a PDF document.
#[derive(Debug, Clone, Serialize)]
pub struct ExtractionResult {
    /// Document profile (domain, layout, complexity)
    pub profile: ProfileSummary,
    /// Per-page extracted blocks (merged, normalized)
    pub pages: Vec<PageResult>,
    /// Detected figures across all pages
    pub figures: Vec<FigureSummary>,
    /// Section hierarchy (flat list for easy consumption)
    pub sections: Vec<SectionSummary>,
    /// Engineering features (if detected)
    pub engineering: Option<EngineeringSummary>,
    /// Running headers/footers detected
    pub running_headers: Vec<String>,
    pub running_footers: Vec<String>,
    /// Recommended extraction strategy
    pub recommended_strategy: String,
    /// Total page count
    pub page_count: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProfileSummary {
    pub domain: String,
    pub complexity_score: u8,
    pub is_scanned: bool,
    pub has_tables: bool,
    pub has_images: bool,
    pub has_toc: bool,
    /// Where the TOC was found: "structure_tree", "outline", or "none"
    pub toc_source: String,
    /// Pages the TOC spans (from structure tree; empty if from outline or none)
    pub toc_pages: Vec<u32>,
    pub column_count: u8,
}

#[derive(Debug, Clone, Serialize)]
pub struct PageResult {
    pub page: usize,
    pub blocks: Vec<BlockSummary>,
    pub text: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct BlockSummary {
    pub block_type: String,
    pub text: String,
    pub bbox: [f32; 4],
    pub font_size: f32,
    pub font_name: String,
    pub is_bold: bool,
    pub confidence: f32,
    pub header_level: Option<u8>,
    pub paragraph_id: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct FigureSummary {
    pub page: usize,
    pub bbox: [f32; 4],
    pub caption: Option<String>,
    pub caption_number: Option<u32>,
    pub context_above: String,
    pub context_below: String,
    pub section_title: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SectionSummary {
    pub title: String,
    pub level: u8,
    pub page: usize,
    pub numbering: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct EngineeringSummary {
    pub has_title_block: bool,
    pub has_revision_table: bool,
    pub has_drawing_border: bool,
    pub security_markings: Vec<String>,
    pub document_number: Option<String>,
}

/// Configuration for document extraction.
#[derive(Debug, Clone)]
pub struct ExtractionConfig {
    /// Whether to detect figures (default: true)
    pub detect_figures: bool,
    /// Whether to detect engineering features (default: true)
    pub detect_engineering: bool,
    /// Whether to normalize text (default: true)
    pub normalize_text: bool,
    /// Whether to build section hierarchy (default: true)
    pub build_sections: bool,
    /// Maximum pages to process (0 = all)
    pub max_pages: usize,
    /// Override the body font size used for header classification.
    /// When set, the BlockClassifier uses this instead of auto-computing
    /// the median from spans. This is the key tuning knob for convergence:
    /// if the auto-detected median is wrong (e.g., code-heavy docs where
    /// monospace font dominates), setting the true body size fixes header
    /// classification.
    pub body_font_size_override: Option<f32>,
    /// Override the header-to-body font ratio threshold (default: 1.2).
    /// Fonts >= body_size * this ratio are considered potential headers.
    pub header_ratio_override: Option<f32>,
}

impl Default for ExtractionConfig {
    fn default() -> Self {
        Self {
            detect_figures: true,
            detect_engineering: true,
            normalize_text: true,
            build_sections: true,
            max_pages: 0,
            body_font_size_override: None,
            header_ratio_override: None,
        }
    }
}

/// Run the full extraction pipeline on a PDF document.
///
/// This is the primary entry point for the `/extract-pdf` skill, replacing the
/// multi-step Python pipeline with a single Rust call.
pub fn extract_document(doc: &mut PdfDocument) -> Result<ExtractionResult> {
    extract_document_with_config(doc, &ExtractionConfig::default())
}

/// Run the full extraction pipeline with custom configuration.
///
/// Optimized to extract spans ONCE per page and share cached data across all
/// pipeline stages (profiling, classification, figures, sections, engineering).
pub fn extract_document_with_config(
    doc: &mut PdfDocument,
    config: &ExtractionConfig,
) -> Result<ExtractionResult> {
    let page_count = doc.page_count().unwrap_or(0);
    let max_pages = if config.max_pages > 0 {
        config.max_pages.min(page_count)
    } else {
        page_count
    };

    // Step 1: Extract spans and classify blocks ONCE per page (shared by all stages)
    let mut all_spans: Vec<Vec<TextSpan>> = Vec::with_capacity(max_pages);
    let mut all_classified: Vec<Vec<ClassifiedBlock>> = Vec::with_capacity(max_pages);
    let mut all_dims: Vec<(f32, f32)> = Vec::with_capacity(max_pages);
    let mut all_page_blocks: Vec<Vec<MergedBlock>> = Vec::new();
    let mut pages: Vec<PageResult> = Vec::new();

    for pg in 0..max_pages {
        let spans = doc.extract_spans_unsorted(pg).unwrap_or_default();
        if spans.is_empty() {
            all_spans.push(vec![]);
            all_classified.push(vec![]);
            all_dims.push((612.0, 792.0));
            all_page_blocks.push(vec![]);
            pages.push(PageResult {
                page: pg,
                blocks: vec![],
                text: String::new(),
            });
            continue;
        }

        let (width, height) = doc.get_page_info(pg)
            .ok()
            .map(|info| (info.media_box.width, info.media_box.height))
            .unwrap_or((612.0, 792.0));

        let classifier = BlockClassifier::new_with_overrides(
            width, height, &spans,
            config.body_font_size_override,
            config.header_ratio_override,
        );
        let classified = classifier.classify_spans(&spans);
        let merged = merge_blocks(&classified, height);

        // Build page text from merged blocks
        let page_text: String = merged.iter()
            .filter(|b| matches!(b.block_type, BlockType::Body | BlockType::Title | BlockType::List))
            .map(|b| {
                if config.normalize_text {
                    full_normalize(&b.text)
                } else {
                    b.text.clone()
                }
            })
            .collect::<Vec<_>>()
            .join("\n");

        let block_summaries: Vec<BlockSummary> = merged.iter()
            .map(|b| BlockSummary {
                block_type: format!("{:?}", b.block_type),
                text: if config.normalize_text { full_normalize(&b.text) } else { b.text.clone() },
                bbox: [b.bbox.x, b.bbox.y, b.bbox.width, b.bbox.height],
                font_size: b.font_size,
                font_name: b.font_name.clone(),
                is_bold: b.is_bold,
                confidence: b.confidence,
                header_level: b.header_level,
                paragraph_id: b.paragraph_id,
            })
            .collect();

        all_spans.push(spans);
        all_classified.push(classified);
        all_dims.push((width, height));
        all_page_blocks.push(merged);
        pages.push(PageResult {
            page: pg,
            blocks: block_summaries,
            text: page_text,
        });
    }

    // Step 2: Profile using cached spans and first-page classified blocks
    let first_page_blocks = if !all_classified.is_empty() { &all_classified[0] } else { &[] as &[ClassifiedBlock] };
    let profile = profile_document_with_cache(doc, &all_spans, first_page_blocks)?;

    // Step 3: Mark running headers/footers across pages
    mark_running_headers_footers(&mut all_page_blocks);

    let mut running_headers: Vec<String> = Vec::new();
    let mut running_footers: Vec<String> = Vec::new();
    for page_blocks in &all_page_blocks {
        for block in page_blocks {
            if block.is_running_header {
                let text = block.text.trim().to_string();
                if !running_headers.contains(&text) {
                    running_headers.push(text);
                }
            }
            if block.is_running_footer {
                let text = block.text.trim().to_string();
                if !running_footers.contains(&text) {
                    running_footers.push(text);
                }
            }
        }
    }

    // Step 4: Detect figures using cached classified blocks (only extract_images is new)
    let mut figures: Vec<FigureSummary> = Vec::new();
    if config.detect_figures {
        for pg in 0..max_pages {
            if let Ok(detected) = detect_figures_from_blocks(doc, pg, &all_classified[pg]) {
                for fig in detected {
                    figures.push(FigureSummary {
                        page: fig.page,
                        bbox: [fig.bbox.x, fig.bbox.y, fig.bbox.width, fig.bbox.height],
                        caption: fig.caption,
                        caption_number: fig.caption_number,
                        context_above: fig.context_above,
                        context_below: fig.context_below,
                        section_title: fig.section_title,
                    });
                }
            }
        }
    }

    // Step 5: Build section hierarchy from cached classified blocks
    let mut sections: Vec<SectionSummary> = Vec::new();
    if config.build_sections {
        let page_blocks: Vec<(usize, Vec<ClassifiedBlock>)> = all_classified.iter()
            .enumerate()
            .map(|(pg, blocks)| (pg, blocks.clone()))
            .collect();
        let outline = doc.get_outline().ok().flatten();
        if let Ok(tree) = build_section_hierarchy_from_classified(&page_blocks, outline) {
            for section in &tree.sections {
                flatten_sections(section, &mut sections);
            }
        }
    }

    // Step 6: Detect engineering features from cached spans (sample pages only)
    let engineering = if config.detect_engineering {
        let pages_to_check: Vec<usize> = if max_pages <= 3 {
            (0..max_pages).collect()
        } else {
            vec![0, 1, max_pages - 1]
        };
        let eng_data: Vec<(&[TextSpan], f32, f32, usize)> = pages_to_check.iter()
            .map(|&pg| (all_spans[pg].as_slice(), all_dims[pg].0, all_dims[pg].1, pg))
            .collect();
        detect_engineering_features_from_spans(&eng_data, page_count).ok().and_then(|eng| {
            if !eng.is_engineering {
                return None;
            }
            use crate::extractors::engineering::EngineeringElement;
            let has_title_block = eng.elements.iter().any(|e| matches!(e.element_type, EngineeringElement::TitleBlock));
            let has_revision_table = eng.elements.iter().any(|e| matches!(e.element_type, EngineeringElement::RevisionTable));
            let has_drawing_border = eng.elements.iter().any(|e| matches!(e.element_type, EngineeringElement::DrawingBorder));
            let security_markings: Vec<String> = eng.elements.iter()
                .filter(|e| matches!(e.element_type, EngineeringElement::SecurityMarking))
                .map(|e| e.text.clone())
                .collect();
            Some(EngineeringSummary {
                has_title_block,
                has_revision_table,
                has_drawing_border,
                security_markings,
                document_number: eng.drawing_number.clone(),
            })
        })
    } else {
        None
    };

    // Step 7: Determine TOC source (structure tree > outline > none)
    let (toc_source, toc_pages) = {
        let mut source = "none".to_string();
        let mut pages = Vec::new();
        if let Ok(Some(struct_tree)) = doc.structure_tree() {
            if let Some(toc) = crate::structure::extract_toc_from_structure(&struct_tree) {
                source = "structure_tree".to_string();
                pages = toc.toc_pages;
            }
        }
        if source == "none" {
            if let Ok(Some(outline)) = doc.get_outline() {
                if !outline.is_empty() {
                    source = "outline".to_string();
                }
            }
        }
        (source, pages)
    };

    // Step 8: Recommend strategy
    let recommended_strategy = recommend_strategy(&profile, &engineering);

    Ok(ExtractionResult {
        profile: ProfileSummary {
            domain: profile.domain.clone(),
            complexity_score: profile.complexity_score,
            is_scanned: profile.is_scanned,
            has_tables: profile.has_tables,
            has_images: profile.has_images,
            has_toc: profile.has_toc || toc_source != "none",
            toc_source,
            toc_pages,
            column_count: profile.layout.columns,
        },
        pages,
        figures,
        sections,
        engineering,
        running_headers,
        running_footers,
        recommended_strategy,
        page_count,
    })
}

fn flatten_sections(section: &crate::extractors::section_hierarchy::Section, out: &mut Vec<SectionSummary>) {
    out.push(SectionSummary {
        title: section.title.clone(),
        level: section.level,
        page: section.page,
        numbering: section.numbering.clone(),
    });
    for child in &section.children {
        flatten_sections(child, out);
    }
}

fn recommend_strategy(profile: &DocumentProfile, engineering: &Option<EngineeringSummary>) -> String {
    if profile.is_scanned {
        return "ocr_first".to_string();
    }

    if let Some(eng) = engineering {
        if eng.has_drawing_border || eng.has_title_block {
            return "drawing_extraction".to_string();
        }
    }

    match profile.domain.as_str() {
        "academic" | "arxiv" => "academic_extraction".to_string(),
        "defense" | "standards" | "nist" => "structured_extraction".to_string(),
        "engineering" => "drawing_extraction".to_string(),
        "slides" => "slide_extraction".to_string(),
        _ => "structured_extraction".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use crate::extractors::document_profiler::LayoutProfile;

    fn make_profile(domain: &str, is_scanned: bool) -> DocumentProfile {
        DocumentProfile {
            page_count: 10,
            domain: domain.to_string(),
            layout: LayoutProfile {
                columns: 1,
                has_header: false,
                has_footer: false,
                has_page_numbers: false,
                has_margin_notes: false,
                avg_chars_per_page: 2000.0,
                page_width: 612.0,
                page_height: 792.0,
                orientation: "portrait".to_string(),
            },
            complexity_score: 3,
            is_scanned,
            has_toc: false,
            has_outline: false,
            has_tables: false,
            has_images: false,
            has_forms: false,
            has_annotations: false,
            languages: vec![],
            primary_font: "Arial".to_string(),
            primary_font_size: 11.0,
            title: None,
            preset: "default".to_string(),
        }
    }

    #[test]
    fn test_recommend_strategy_scanned() {
        let profile = make_profile("unknown", true);
        assert_eq!(recommend_strategy(&profile, &None), "ocr_first");
    }

    #[test]
    fn test_recommend_strategy_engineering() {
        let profile = make_profile("engineering", false);
        let eng = Some(EngineeringSummary {
            has_title_block: true,
            has_revision_table: true,
            has_drawing_border: true,
            security_markings: vec![],
            document_number: Some("DWG-001".to_string()),
        });
        assert_eq!(recommend_strategy(&profile, &eng), "drawing_extraction");
    }

    #[test]
    fn test_recommend_strategy_academic() {
        let profile = make_profile("academic", false);
        assert_eq!(recommend_strategy(&profile, &None), "academic_extraction");
    }

    #[test]
    fn test_extraction_config_default() {
        let config = ExtractionConfig::default();
        assert!(config.detect_figures);
        assert!(config.detect_engineering);
        assert!(config.normalize_text);
        assert!(config.build_sections);
        assert_eq!(config.max_pages, 0);
    }
}
