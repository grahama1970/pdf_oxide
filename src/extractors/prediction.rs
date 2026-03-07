use crate::document::PdfDocument;
use crate::error::Result;
use crate::extractors::block_classifier::{BlockClassifier, BlockType};
use crate::extractors::document_profiler::{self, DocumentProfile};
use crate::extractors::engineering::{self, EngineeringProfile};
use crate::extractors::section_hierarchy::{self, SectionTree};

/// Full extraction prediction for a document — bundles profiling, block classification,
/// section hierarchy, and engineering detection into a single call.
/// This is the primary input for Shadow-LEGO cascade decision points.
#[derive(Debug, Clone)]
pub struct ExtractionPrediction {
    pub profile: DocumentProfile,
    pub engineering: EngineeringProfile,
    pub sections: SectionTree,
    pub page_block_summary: Vec<PageBlockSummary>,
    pub recommended_strategy: String,
}

#[derive(Debug, Clone)]
pub struct PageBlockSummary {
    pub page: usize,
    pub total_blocks: usize,
    pub title_count: usize,
    pub body_count: usize,
    pub list_count: usize,
    pub table_count: usize,
    pub caption_count: usize,
    pub footnote_count: usize,
    pub has_header: bool,
    pub has_footer: bool,
    pub has_page_number: bool,
}

/// Run all analysis passes and produce a unified prediction.
pub fn predict_extraction(doc: &mut PdfDocument) -> Result<ExtractionPrediction> {
    let profile = document_profiler::profile_document(doc)?;
    let engineering = engineering::detect_engineering_features(doc)?;
    let sections = section_hierarchy::build_section_hierarchy(doc)?;

    let page_count = doc.page_count().unwrap_or(0);
    let sample_pages: Vec<usize> = if page_count <= 5 {
        (0..page_count).collect()
    } else {
        vec![0, 1, 2, page_count / 2, page_count - 1]
    };

    let mut page_block_summary = Vec::new();
    for &pg in &sample_pages {
        let spans = doc.extract_spans_unsorted(pg).unwrap_or_default();
        if spans.is_empty() {
            page_block_summary.push(PageBlockSummary {
                page: pg,
                total_blocks: 0,
                title_count: 0,
                body_count: 0,
                list_count: 0,
                table_count: 0,
                caption_count: 0,
                footnote_count: 0,
                has_header: false,
                has_footer: false,
                has_page_number: false,
            });
            continue;
        }

        let (width, height) = doc.get_page_info(pg)
            .ok()
            .map(|info| (info.media_box.width, info.media_box.height))
            .unwrap_or((612.0, 792.0));

        let classifier = BlockClassifier::new(width, height, &spans);
        let blocks = classifier.classify_spans(&spans);

        let tables = doc.extract_tables(pg).unwrap_or_default();

        page_block_summary.push(PageBlockSummary {
            page: pg,
            total_blocks: blocks.len(),
            title_count: blocks.iter().filter(|b| b.block_type == BlockType::Title).count(),
            body_count: blocks.iter().filter(|b| b.block_type == BlockType::Body).count(),
            list_count: blocks.iter().filter(|b| b.block_type == BlockType::List).count(),
            table_count: tables.len(),
            caption_count: blocks.iter().filter(|b| b.block_type == BlockType::Caption).count(),
            footnote_count: blocks.iter().filter(|b| b.block_type == BlockType::Footnote).count(),
            has_header: blocks.iter().any(|b| b.block_type == BlockType::Header),
            has_footer: blocks.iter().any(|b| b.block_type == BlockType::Footer),
            has_page_number: blocks.iter().any(|b| b.block_type == BlockType::PageNumber),
        });
    }

    let recommended_strategy = recommend_strategy(&profile, &engineering);

    Ok(ExtractionPrediction {
        profile,
        engineering,
        sections,
        page_block_summary,
        recommended_strategy,
    })
}

fn recommend_strategy(profile: &DocumentProfile, engineering: &EngineeringProfile) -> String {
    if profile.is_scanned {
        return "ocr_first".to_string();
    }
    if engineering.is_engineering {
        return match engineering.doc_subtype.as_str() {
            "engineering_drawing" => "drawing_extraction".to_string(),
            "assembly_drawing" => "drawing_extraction".to_string(),
            "defense_specification" => "structured_extraction".to_string(),
            _ => "structured_extraction".to_string(),
        };
    }
    match profile.domain.as_str() {
        "academic" => "academic_extraction".to_string(),
        "ietf" => "plain_text_extraction".to_string(),
        "slides" => "slide_extraction".to_string(),
        _ => {
            if profile.layout.columns > 1 {
                "multi_column_extraction".to_string()
            } else if profile.has_tables {
                "structured_extraction".to_string()
            } else {
                "standard_extraction".to_string()
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_recommend_strategy_scanned() {
        let mut profile = make_test_profile();
        profile.is_scanned = true;
        let eng = make_test_engineering();
        assert_eq!(recommend_strategy(&profile, &eng), "ocr_first");
    }

    #[test]
    fn test_recommend_strategy_academic() {
        let mut profile = make_test_profile();
        profile.domain = "academic".to_string();
        let eng = make_test_engineering();
        assert_eq!(recommend_strategy(&profile, &eng), "academic_extraction");
    }

    #[test]
    fn test_recommend_strategy_engineering() {
        let profile = make_test_profile();
        let mut eng = make_test_engineering();
        eng.is_engineering = true;
        eng.doc_subtype = "engineering_drawing".to_string();
        assert_eq!(recommend_strategy(&profile, &eng), "drawing_extraction");
    }

    fn make_test_profile() -> DocumentProfile {
        DocumentProfile {
            page_count: 10,
            domain: "general".to_string(),
            layout: crate::extractors::document_profiler::LayoutProfile {
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
            is_scanned: false,
            has_toc: false,
            has_outline: false,
            has_tables: false,
            has_images: false,
            has_forms: false,
            has_annotations: false,
            languages: vec!["en".to_string()],
            primary_font: "Arial".to_string(),
            primary_font_size: 11.0,
            title: None,
            preset: "general_document".to_string(),
        }
    }

    fn make_test_engineering() -> EngineeringProfile {
        EngineeringProfile {
            is_engineering: false,
            doc_subtype: "unknown".to_string(),
            elements: Vec::new(),
            drawing_number: None,
            revision: None,
            cage_code: None,
            distribution_statement: None,
        }
    }
}
