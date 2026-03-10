use crate::document::PdfDocument;
use crate::error::Result;
use crate::geometry::Rect;
use crate::layout::text_block::TextSpan;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EngineeringElement {
    TitleBlock,
    RevisionTable,
    DrawingBorder,
    PartsTable,
    NotesBlock,
    DistributionStatement,
    SecurityMarking,
    DrawingNumber,
    ApprovalBlock,
}

impl EngineeringElement {
    pub fn as_str(&self) -> &'static str {
        match self {
            EngineeringElement::TitleBlock => "title_block",
            EngineeringElement::RevisionTable => "revision_table",
            EngineeringElement::DrawingBorder => "drawing_border",
            EngineeringElement::PartsTable => "parts_table",
            EngineeringElement::NotesBlock => "notes_block",
            EngineeringElement::DistributionStatement => "distribution_statement",
            EngineeringElement::SecurityMarking => "security_marking",
            EngineeringElement::DrawingNumber => "drawing_number",
            EngineeringElement::ApprovalBlock => "approval_block",
        }
    }
}

#[derive(Debug, Clone)]
pub struct DetectedElement {
    pub element_type: EngineeringElement,
    pub bbox: Rect,
    pub page: usize,
    pub text: String,
    pub confidence: f32,
}

#[derive(Debug, Clone)]
pub struct EngineeringProfile {
    pub is_engineering: bool,
    pub doc_subtype: String,
    pub elements: Vec<DetectedElement>,
    pub drawing_number: Option<String>,
    pub revision: Option<String>,
    pub cage_code: Option<String>,
    pub distribution_statement: Option<String>,
}

/// Analyze a document for engineering/defense document features.
pub fn detect_engineering_features(doc: &mut PdfDocument) -> Result<EngineeringProfile> {
    let page_count = doc.page_count().unwrap_or(0);
    if page_count == 0 {
        return Ok(empty_profile());
    }

    // Sample first page (title page) and last few pages
    let pages_to_check: Vec<usize> = if page_count <= 3 {
        (0..page_count).collect()
    } else {
        vec![0, 1, page_count - 1]
    };

    let mut page_data: Vec<(&[TextSpan], f32, f32, usize)> = Vec::new();
    let mut owned_spans: Vec<Vec<TextSpan>> = Vec::new();

    for &pg in &pages_to_check {
        let spans = doc.extract_spans_unsorted(pg).unwrap_or_default();
        let (width, height) = doc.get_page_info(pg)
            .ok()
            .map(|info| (info.media_box.width, info.media_box.height))
            .unwrap_or((612.0, 792.0));
        owned_spans.push(spans);
        // Store width, height, pg for later — we'll index into owned_spans
        page_data.push((&[], width, height, pg));
    }

    // Fix up references
    let mut all_elements: Vec<DetectedElement> = Vec::new();
    for (i, (_, width, height, pg)) in page_data.iter().enumerate() {
        if owned_spans[i].is_empty() {
            continue;
        }
        let mut page_elements = detect_page_elements(&owned_spans[i], *width, *height, *pg);
        all_elements.append(&mut page_elements);
    }

    build_engineering_profile(all_elements, page_count)
}

/// Detect engineering features from pre-extracted spans (avoids re-extracting).
pub fn detect_engineering_features_from_spans(
    page_spans: &[(&[TextSpan], f32, f32, usize)],
    page_count: usize,
) -> Result<EngineeringProfile> {
    if page_count == 0 {
        return Ok(empty_profile());
    }

    let mut all_elements: Vec<DetectedElement> = Vec::new();
    for &(spans, width, height, pg) in page_spans {
        if spans.is_empty() {
            continue;
        }
        let mut page_elements = detect_page_elements(spans, width, height, pg);
        all_elements.append(&mut page_elements);
    }

    build_engineering_profile(all_elements, page_count)
}

fn build_engineering_profile(
    all_elements: Vec<DetectedElement>,
    page_count: usize,
) -> Result<EngineeringProfile> {
    // Extract metadata from detected elements
    let drawing_number = extract_drawing_number(&all_elements);
    let revision = extract_revision(&all_elements);
    let cage_code = extract_cage_code(&all_elements);
    let distribution_statement = extract_distribution_statement(&all_elements);

    let is_engineering = !all_elements.is_empty()
        && all_elements.iter().any(|e| matches!(
            e.element_type,
            EngineeringElement::TitleBlock
                | EngineeringElement::DrawingBorder
                | EngineeringElement::DrawingNumber
                | EngineeringElement::RevisionTable
        ));

    let doc_subtype = classify_engineering_subtype(&all_elements, page_count);

    Ok(EngineeringProfile {
        is_engineering,
        doc_subtype,
        elements: all_elements,
        drawing_number,
        revision,
        cage_code,
        distribution_statement,
    })
}

fn empty_profile() -> EngineeringProfile {
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

fn detect_page_elements(spans: &[TextSpan], page_width: f32, page_height: f32, page: usize) -> Vec<DetectedElement> {
    let mut elements = Vec::new();

    // Check for title block (bottom-right quadrant, dense short text)
    if let Some(tb) = detect_title_block(spans, page_width, page_height, page) {
        elements.push(tb);
    }

    // Check for revision table (typically top-right or near title block)
    if let Some(rt) = detect_revision_table(spans, page_width, page_height, page) {
        elements.push(rt);
    }

    // Check for drawing border (spans forming a rectangular frame)
    if let Some(db) = detect_drawing_border(spans, page_width, page_height, page) {
        elements.push(db);
    }

    // Check for distribution statement
    if let Some(ds) = detect_distribution_statement(spans, page) {
        elements.push(ds);
    }

    // Check for security markings
    if let Some(sm) = detect_security_marking(spans, page_width, page_height, page) {
        elements.push(sm);
    }

    // Check for drawing number
    if let Some(dn) = detect_drawing_number(spans, page_width, page_height, page) {
        elements.push(dn);
    }

    // Check for parts/BOM table
    if let Some(pt) = detect_parts_table(spans, page) {
        elements.push(pt);
    }

    // Check for approval block
    if let Some(ab) = detect_approval_block(spans, page_width, page_height, page) {
        elements.push(ab);
    }

    // Check for notes block
    if let Some(nb) = detect_notes_block(spans, page) {
        elements.push(nb);
    }

    elements
}

/// Title block: bottom-right quadrant, contains labels like TITLE, DRAWN, CHECKED, SCALE, SIZE
fn detect_title_block(spans: &[TextSpan], page_width: f32, page_height: f32, page: usize) -> Option<DetectedElement> {
    let br_spans: Vec<&TextSpan> = spans.iter()
        .filter(|s| {
            s.bbox.x + s.bbox.width > page_width * 0.5
                && s.bbox.y > page_height * 0.7
        })
        .collect();

    if br_spans.is_empty() {
        return None;
    }

    let combined_text: String = br_spans.iter().map(|s| s.text.as_str()).collect::<Vec<_>>().join(" ");
    let lower = combined_text.to_lowercase();

    let title_block_keywords = [
        "title", "drawn", "checked", "approved", "scale", "size",
        "dwg no", "drawing no", "sheet", "rev", "date", "cage code",
        "tolerances", "material", "finish", "weight",
    ];

    let keyword_hits = title_block_keywords.iter()
        .filter(|kw| lower.contains(*kw))
        .count();

    if keyword_hits >= 3 {
        let bbox = compute_bounding_box(&br_spans);
        Some(DetectedElement {
            element_type: EngineeringElement::TitleBlock,
            bbox,
            page,
            text: combined_text,
            confidence: (keyword_hits as f32 / 6.0).min(1.0),
        })
    } else {
        None
    }
}

/// Revision table: contains REV, ECN/ECO, DATE columns, typically near top-right or above title block.
/// Must have short label-like spans (not body text) and be in the right page region.
fn detect_revision_table(spans: &[TextSpan], page_width: f32, page_height: f32, page: usize) -> Option<DetectedElement> {
    // Only look for short spans (< 60 chars) that contain revision keywords
    // and are in the upper-right or bottom-right quadrant
    let rev_primary = ["rev", "revision"];
    let rev_secondary = ["ecn", "eco", "date", "by", "zone"];

    let candidate_spans: Vec<&TextSpan> = spans.iter()
        .filter(|s| {
            let lower = s.text.to_lowercase();
            let is_short = s.text.trim().len() < 60;
            let in_right_region = s.bbox.x + s.bbox.width > page_width * 0.5
                || s.bbox.y < page_height * 0.15
                || s.bbox.y > page_height * 0.7;
            is_short && in_right_region
                && (rev_primary.iter().any(|kw| lower.contains(kw))
                    || rev_secondary.iter().any(|kw| lower.contains(kw)))
        })
        .collect();

    // Must have at least one primary keyword match
    let has_primary = candidate_spans.iter().any(|s| {
        let lower = s.text.to_lowercase();
        rev_primary.iter().any(|kw| lower.contains(kw))
    });

    if !has_primary || candidate_spans.len() < 2 {
        return None;
    }

    let bbox = compute_bounding_box(&candidate_spans);
    let text: String = candidate_spans.iter().map(|s| s.text.as_str()).collect::<Vec<_>>().join(" ");
    Some(DetectedElement {
        element_type: EngineeringElement::RevisionTable,
        bbox,
        page,
        text,
        confidence: if candidate_spans.len() >= 4 { 0.9 } else { 0.7 },
    })
}

/// Drawing border: text very close to page margins forming a frame
fn detect_drawing_border(spans: &[TextSpan], page_width: f32, page_height: f32, page: usize) -> Option<DetectedElement> {
    let margin = page_width * 0.04; // ~24pt on letter size

    // Check for zone markers (A, B, C, D or 1, 2, 3, 4 along edges)
    let edge_spans: Vec<&TextSpan> = spans.iter()
        .filter(|s| {
            let near_left = s.bbox.x < margin;
            let near_right = s.bbox.x + s.bbox.width > page_width - margin;
            let near_top = s.bbox.y < margin * 1.5;
            let near_bottom = s.bbox.y + s.bbox.height > page_height - margin * 1.5;
            (near_left || near_right || near_top || near_bottom)
                && s.text.trim().len() <= 3
        })
        .collect();

    // Need at least 4 zone markers to suggest a drawing border
    if edge_spans.len() >= 4 {
        let bbox = Rect {
            x: 0.0,
            y: 0.0,
            width: page_width,
            height: page_height,
        };
        let text: String = edge_spans.iter().map(|s| s.text.trim()).collect::<Vec<_>>().join(" ");
        Some(DetectedElement {
            element_type: EngineeringElement::DrawingBorder,
            bbox,
            page,
            text,
            confidence: (edge_spans.len() as f32 / 8.0).min(0.95),
        })
    } else {
        None
    }
}

/// Distribution statement: "DISTRIBUTION STATEMENT A/B/C/D/E/F" or "APPROVED FOR PUBLIC RELEASE"
fn detect_distribution_statement(spans: &[TextSpan], page: usize) -> Option<DetectedElement> {
    for span in spans {
        let lower = span.text.to_lowercase();
        if lower.contains("distribution statement")
            || lower.contains("approved for public release")
            || lower.contains("distribution authorized to")
            || lower.contains("destruction notice")
            || (lower.contains("distribution") && lower.contains("unlimited"))
        {
            return Some(DetectedElement {
                element_type: EngineeringElement::DistributionStatement,
                bbox: span.bbox,
                page,
                text: span.text.clone(),
                confidence: 0.95,
            });
        }
    }
    None
}

/// Security marking: UNCLASSIFIED, CUI, FOUO, SECRET, etc.
fn detect_security_marking(spans: &[TextSpan], page_width: f32, _page_height: f32, page: usize) -> Option<DetectedElement> {
    let markings = [
        "unclassified", "classified", "secret", "top secret",
        "confidential", "fouo", "cui", "controlled unclassified",
        "noforn", "rel to", "orcon",
    ];

    for span in spans {
        let lower = span.text.to_lowercase();
        let is_centered = {
            let x_center = span.bbox.x + span.bbox.width / 2.0;
            (x_center - page_width / 2.0).abs() < page_width * 0.15
        };

        for marking in &markings {
            if lower.contains(marking) && (is_centered || span.text.len() < 40) {
                return Some(DetectedElement {
                    element_type: EngineeringElement::SecurityMarking,
                    bbox: span.bbox,
                    page,
                    text: span.text.clone(),
                    confidence: 0.9,
                });
            }
        }
    }
    None
}

/// Drawing number: alphanumeric pattern like "12345-67890" or "DWG-ABC-123"
fn detect_drawing_number(spans: &[TextSpan], page_width: f32, page_height: f32, page: usize) -> Option<DetectedElement> {
    // Look in bottom-right area (title block region)
    let candidates: Vec<&TextSpan> = spans.iter()
        .filter(|s| s.bbox.x + s.bbox.width > page_width * 0.5 && s.bbox.y > page_height * 0.7)
        .collect();

    for span in &candidates {
        let text = span.text.trim();
        if is_drawing_number_pattern(text) {
            return Some(DetectedElement {
                element_type: EngineeringElement::DrawingNumber,
                bbox: span.bbox,
                page,
                text: text.to_string(),
                confidence: 0.85,
            });
        }
    }

    // Also check for "DWG NO" label nearby
    for span in spans {
        let lower = span.text.to_lowercase();
        if lower.contains("dwg no") || lower.contains("drawing no") || lower.contains("part no") {
            return Some(DetectedElement {
                element_type: EngineeringElement::DrawingNumber,
                bbox: span.bbox,
                page,
                text: span.text.clone(),
                confidence: 0.8,
            });
        }
    }

    None
}

/// Parts table / BOM: contains QTY, PART NUMBER, DESCRIPTION, MATERIAL
fn detect_parts_table(spans: &[TextSpan], page: usize) -> Option<DetectedElement> {
    let bom_keywords = ["qty", "quantity", "part number", "part no", "description", "material",
        "item", "nomenclature", "specification", "bill of materials", "bom"];

    let matching_spans: Vec<&TextSpan> = spans.iter()
        .filter(|s| {
            let lower = s.text.to_lowercase();
            bom_keywords.iter().any(|kw| lower.contains(kw))
        })
        .collect();

    let keyword_hits = bom_keywords.iter()
        .filter(|kw| {
            matching_spans.iter().any(|s| s.text.to_lowercase().contains(*kw))
        })
        .count();

    if keyword_hits >= 3 {
        let bbox = compute_bounding_box(&matching_spans);
        let text: String = matching_spans.iter().map(|s| s.text.as_str()).collect::<Vec<_>>().join(" ");
        Some(DetectedElement {
            element_type: EngineeringElement::PartsTable,
            bbox,
            page,
            text,
            confidence: (keyword_hits as f32 / 5.0).min(0.95),
        })
    } else {
        None
    }
}

/// Approval block: APPROVED BY, CHECKED BY, DRAWN BY with date fields
fn detect_approval_block(spans: &[TextSpan], page_width: f32, page_height: f32, page: usize) -> Option<DetectedElement> {
    let approval_keywords = ["approved by", "checked by", "drawn by", "designed by",
        "engineer", "signature", "approvals"];

    let matching_spans: Vec<&TextSpan> = spans.iter()
        .filter(|s| {
            let lower = s.text.to_lowercase();
            approval_keywords.iter().any(|kw| lower.contains(kw))
                && s.bbox.y > page_height * 0.6
        })
        .collect();

    if matching_spans.len() >= 2 {
        let bbox = compute_bounding_box(&matching_spans);
        let text: String = matching_spans.iter().map(|s| s.text.as_str()).collect::<Vec<_>>().join(" ");
        Some(DetectedElement {
            element_type: EngineeringElement::ApprovalBlock,
            bbox,
            page,
            text,
            confidence: 0.8,
        })
    } else {
        None
    }
}

/// Notes block: "NOTES:", "GENERAL NOTES", numbered notes
fn detect_notes_block(spans: &[TextSpan], page: usize) -> Option<DetectedElement> {
    for (i, span) in spans.iter().enumerate() {
        let lower = span.text.to_lowercase();
        if lower.starts_with("notes") || lower.starts_with("general notes")
            || lower.starts_with("unless otherwise specified")
        {
            // Gather subsequent spans as part of notes block
            let note_spans: Vec<&TextSpan> = std::iter::once(span)
                .chain(spans[i + 1..].iter().take(20))
                .collect();
            let bbox = compute_bounding_box(&note_spans);
            let text: String = note_spans.iter().map(|s| s.text.as_str()).collect::<Vec<_>>().join(" ");
            return Some(DetectedElement {
                element_type: EngineeringElement::NotesBlock,
                bbox,
                page,
                text,
                confidence: 0.75,
            });
        }
    }
    None
}

fn is_drawing_number_pattern(text: &str) -> bool {
    let trimmed = text.trim();
    if trimmed.len() < 5 || trimmed.len() > 30 {
        return false;
    }
    // Pattern: alphanumeric with dashes (e.g., "12345-67890", "ABC-DEF-123")
    let has_dash = trimmed.contains('-');
    let alnum_count = trimmed.chars().filter(|c| c.is_alphanumeric()).count();
    let total = trimmed.chars().count();
    has_dash && alnum_count as f32 / total as f32 > 0.7 && trimmed.chars().any(|c| c.is_ascii_digit())
}

fn compute_bounding_box(spans: &[&TextSpan]) -> Rect {
    if spans.is_empty() {
        return Rect { x: 0.0, y: 0.0, width: 0.0, height: 0.0 };
    }
    spans.iter().skip(1).fold(spans[0].bbox, |acc, s| acc.union(&s.bbox))
}

fn classify_engineering_subtype(elements: &[DetectedElement], page_count: usize) -> String {
    let has_drawing_border = elements.iter().any(|e| e.element_type == EngineeringElement::DrawingBorder);
    let has_title_block = elements.iter().any(|e| e.element_type == EngineeringElement::TitleBlock);
    let has_parts_table = elements.iter().any(|e| e.element_type == EngineeringElement::PartsTable);
    let has_dist_stmt = elements.iter().any(|e| e.element_type == EngineeringElement::DistributionStatement);
    let has_security = elements.iter().any(|e| e.element_type == EngineeringElement::SecurityMarking);

    if has_drawing_border && has_title_block {
        return "engineering_drawing".to_string();
    }
    if has_parts_table && has_title_block {
        return "assembly_drawing".to_string();
    }
    if has_dist_stmt && has_security && page_count > 10 {
        return "defense_specification".to_string();
    }
    if has_dist_stmt || has_security {
        return "defense_document".to_string();
    }
    if has_title_block {
        return "engineering_document".to_string();
    }

    "unknown".to_string()
}

fn extract_drawing_number(elements: &[DetectedElement]) -> Option<String> {
    elements.iter()
        .find(|e| e.element_type == EngineeringElement::DrawingNumber)
        .map(|e| e.text.clone())
}

fn extract_revision(elements: &[DetectedElement]) -> Option<String> {
    elements.iter()
        .find(|e| e.element_type == EngineeringElement::RevisionTable)
        .and_then(|e| {
            // Try to extract revision letter/number from the text
            let lower = e.text.to_lowercase();
            if let Some(idx) = lower.find("rev") {
                let after = &e.text[idx + 3..];
                let rev = after.trim().split_whitespace().next().unwrap_or("").trim_matches(|c: char| !c.is_alphanumeric());
                if !rev.is_empty() {
                    return Some(rev.to_string());
                }
            }
            None
        })
}

fn extract_cage_code(elements: &[DetectedElement]) -> Option<String> {
    for element in elements {
        let lower = element.text.to_lowercase();
        if let Some(idx) = lower.find("cage") {
            let after = &element.text[idx + 4..];
            let code = after.trim().split_whitespace().next().unwrap_or("")
                .trim_matches(|c: char| !c.is_alphanumeric());
            if code.len() == 5 && code.chars().all(|c| c.is_alphanumeric()) {
                return Some(code.to_uppercase());
            }
        }
    }
    None
}

fn extract_distribution_statement(elements: &[DetectedElement]) -> Option<String> {
    elements.iter()
        .find(|e| e.element_type == EngineeringElement::DistributionStatement)
        .map(|e| e.text.clone())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::text_block::{FontWeight, Color};

    fn make_span(text: &str, x: f32, y: f32, font_size: f32) -> TextSpan {
        TextSpan {
            text: text.to_string(),
            bbox: Rect { x, y, width: text.len() as f32 * font_size * 0.5, height: font_size },
            font_name: "TestFont".to_string(),
            font_size,
            font_weight: FontWeight::Normal,
            is_italic: false,
            color: Color { r: 0.0, g: 0.0, b: 0.0 },
            mcid: None,
            sequence: 0,
            split_boundary_before: false,
            offset_semantic: false,
            char_spacing: 0.0,
            word_spacing: 0.0,
            horizontal_scaling: 100.0,
            primary_detected: false,
        }
    }

    #[test]
    fn test_title_block_detection() {
        let spans = vec![
            make_span("TITLE: Widget Assembly", 400.0, 720.0, 10.0),
            make_span("DRAWN: J. Smith", 400.0, 735.0, 8.0),
            make_span("CHECKED: M. Jones", 400.0, 748.0, 8.0),
            make_span("APPROVED: R. Brown", 400.0, 760.0, 8.0),
            make_span("SCALE: 1:1", 500.0, 720.0, 8.0),
            make_span("DATE: 2025-01-15", 500.0, 735.0, 8.0),
        ];

        let elements = detect_page_elements(&spans, 612.0, 792.0, 0);
        assert!(elements.iter().any(|e| e.element_type == EngineeringElement::TitleBlock));
    }

    #[test]
    fn test_distribution_statement() {
        let spans = vec![
            make_span("DISTRIBUTION STATEMENT A: Approved for public release", 100.0, 750.0, 9.0),
        ];
        let elements = detect_page_elements(&spans, 612.0, 792.0, 0);
        assert!(elements.iter().any(|e| e.element_type == EngineeringElement::DistributionStatement));
    }

    #[test]
    fn test_security_marking() {
        let spans = vec![
            make_span("UNCLASSIFIED", 250.0, 10.0, 10.0),
        ];
        let elements = detect_page_elements(&spans, 612.0, 792.0, 0);
        assert!(elements.iter().any(|e| e.element_type == EngineeringElement::SecurityMarking));
    }

    #[test]
    fn test_drawing_number_pattern() {
        assert!(is_drawing_number_pattern("12345-67890"));
        assert!(is_drawing_number_pattern("ABC-DEF-123"));
        assert!(is_drawing_number_pattern("DWG-2024-001"));
        assert!(!is_drawing_number_pattern("Hello World"));
        assert!(!is_drawing_number_pattern("A"));
        assert!(!is_drawing_number_pattern("abcdefghijklmnopqrstuvwxyz1234567890"));
    }

    #[test]
    fn test_parts_table_detection() {
        let spans = vec![
            make_span("ITEM", 50.0, 200.0, 8.0),
            make_span("QTY", 100.0, 200.0, 8.0),
            make_span("PART NUMBER", 150.0, 200.0, 8.0),
            make_span("DESCRIPTION", 250.0, 200.0, 8.0),
            make_span("MATERIAL", 400.0, 200.0, 8.0),
            make_span("1", 50.0, 215.0, 8.0),
            make_span("2", 100.0, 215.0, 8.0),
            make_span("ABC-123", 150.0, 215.0, 8.0),
        ];
        let elements = detect_page_elements(&spans, 612.0, 792.0, 0);
        assert!(elements.iter().any(|e| e.element_type == EngineeringElement::PartsTable));
    }

    #[test]
    fn test_classify_subtype() {
        let drawing_elements = vec![
            DetectedElement {
                element_type: EngineeringElement::DrawingBorder,
                bbox: Rect { x: 0.0, y: 0.0, width: 612.0, height: 792.0 },
                page: 0,
                text: "A B C D".to_string(),
                confidence: 0.8,
            },
            DetectedElement {
                element_type: EngineeringElement::TitleBlock,
                bbox: Rect { x: 400.0, y: 700.0, width: 200.0, height: 80.0 },
                page: 0,
                text: "TITLE DRAWN CHECKED".to_string(),
                confidence: 0.9,
            },
        ];
        assert_eq!(classify_engineering_subtype(&drawing_elements, 1), "engineering_drawing");

        let defense_elements = vec![
            DetectedElement {
                element_type: EngineeringElement::DistributionStatement,
                bbox: Rect { x: 100.0, y: 750.0, width: 400.0, height: 12.0 },
                page: 0,
                text: "DISTRIBUTION STATEMENT A".to_string(),
                confidence: 0.95,
            },
            DetectedElement {
                element_type: EngineeringElement::SecurityMarking,
                bbox: Rect { x: 250.0, y: 10.0, width: 100.0, height: 12.0 },
                page: 0,
                text: "UNCLASSIFIED".to_string(),
                confidence: 0.9,
            },
        ];
        assert_eq!(classify_engineering_subtype(&defense_elements, 50), "defense_specification");
    }

    #[test]
    fn test_notes_block_detection() {
        let spans = vec![
            make_span("NOTES:", 50.0, 400.0, 10.0),
            make_span("1. All dimensions in inches.", 50.0, 415.0, 8.0),
            make_span("2. Surface finish 125 RMS.", 50.0, 428.0, 8.0),
        ];
        let elements = detect_page_elements(&spans, 612.0, 792.0, 0);
        assert!(elements.iter().any(|e| e.element_type == EngineeringElement::NotesBlock));
    }
}
