use crate::document::PdfDocument;
use crate::error::Result;
use crate::extractors::block_classifier::{BlockClassifier, BlockType};
use crate::layout::text_block::TextSpan;
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub struct DocumentProfile {
    pub page_count: usize,
    pub domain: String,
    pub layout: LayoutProfile,
    pub complexity_score: u8,
    pub is_scanned: bool,
    pub has_toc: bool,
    pub has_outline: bool,
    pub has_tables: bool,
    pub has_images: bool,
    pub has_forms: bool,
    pub has_annotations: bool,
    pub languages: Vec<String>,
    pub primary_font: String,
    pub primary_font_size: f32,
    pub title: Option<String>,
    pub preset: String,
}

#[derive(Debug, Clone)]
pub struct LayoutProfile {
    pub columns: u8,
    pub has_header: bool,
    pub has_footer: bool,
    pub has_page_numbers: bool,
    pub has_margin_notes: bool,
    pub avg_chars_per_page: f32,
    pub page_width: f32,
    pub page_height: f32,
    pub orientation: String,
}

pub fn profile_document(doc: &mut PdfDocument) -> Result<DocumentProfile> {
    let page_count = doc.page_count().unwrap_or(0);
    let sample_pages = select_sample_pages(page_count);

    // Gather data from sample pages
    let mut all_spans: Vec<TextSpan> = Vec::new();
    let mut total_chars: usize = 0;
    let mut page_dims: Option<(f32, f32)> = None;
    let mut has_images = false;
    let mut scanned_pages = 0;

    for &pg in &sample_pages {
        let spans = doc.extract_spans_unsorted(pg).unwrap_or_default();
        let text = doc.extract_text(pg).unwrap_or_default();
        total_chars += text.len();

        if spans.is_empty() && !text.is_empty() {
            scanned_pages += 1;
        } else if spans.is_empty() && text.is_empty() {
            scanned_pages += 1;
        }

        if page_dims.is_none() {
            if let Ok(info) = doc.get_page_info(pg) {
                page_dims = Some((info.media_box.width, info.media_box.height));
            }
        }

        let images = doc.extract_images(pg).unwrap_or_default();
        if !images.is_empty() {
            has_images = true;
        }

        all_spans.extend(spans);
    }

    let (page_width, page_height) = page_dims.unwrap_or((612.0, 792.0));
    let is_scanned = scanned_pages as f32 / sample_pages.len() as f32 > 0.5;
    let avg_chars = if sample_pages.is_empty() { 0.0 } else { total_chars as f32 / sample_pages.len() as f32 };

    // Font analysis
    let (primary_font, primary_font_size) = analyze_fonts(&all_spans);

    // Column detection
    let columns = detect_columns(&all_spans, page_width);

    // Block classification for first page
    let first_page_spans = doc.extract_spans_unsorted(0).unwrap_or_default();
    let classifier = BlockClassifier::new(page_width, page_height, &first_page_spans);
    let blocks = classifier.classify_spans(&first_page_spans);

    let has_header = blocks.iter().any(|b| b.block_type == BlockType::Header);
    let has_footer = blocks.iter().any(|b| b.block_type == BlockType::Footer);
    let has_page_numbers = blocks.iter().any(|b| b.block_type == BlockType::PageNumber);
    let has_toc = blocks.iter().any(|b| b.block_type == BlockType::TableOfContents);
    let title = blocks.iter()
        .find(|b| b.block_type == BlockType::Title && b.header_level == Some(0))
        .map(|b| b.text.clone());

    // Outline detection
    let has_outline = match doc.get_outline() {
        Ok(Some(items)) => !items.is_empty(),
        _ => false,
    };

    // Form detection
    let has_forms = false; // TODO: check for AcroForm

    // Annotation detection
    let has_annotations = sample_pages.iter().any(|&pg| {
        doc.get_annotations(pg).map(|a| !a.is_empty()).unwrap_or(false)
    });

    // Table detection on first few pages
    let has_tables = sample_pages.iter().take(3).any(|&pg| {
        doc.extract_tables(pg).map(|t| !t.is_empty()).unwrap_or(false)
    });

    // Domain detection
    let first_page_text = doc.extract_text(0).unwrap_or_default();
    let domain = detect_domain(&first_page_text, &title, page_count);
    let preset = domain_to_preset(&domain);

    // Complexity scoring
    let complexity_score = compute_complexity(
        page_count, columns, has_tables, has_images, has_forms,
        is_scanned, has_annotations, avg_chars,
    );

    let orientation = if page_width > page_height { "landscape" } else { "portrait" }.to_string();

    Ok(DocumentProfile {
        page_count,
        domain,
        layout: LayoutProfile {
            columns,
            has_header,
            has_footer,
            has_page_numbers,
            has_margin_notes: false,
            avg_chars_per_page: avg_chars,
            page_width,
            page_height,
            orientation,
        },
        complexity_score,
        is_scanned,
        has_toc,
        has_outline,
        has_tables,
        has_images,
        has_forms,
        has_annotations,
        languages: vec!["en".to_string()],
        primary_font,
        primary_font_size,
        title,
        preset,
    })
}

fn select_sample_pages(page_count: usize) -> Vec<usize> {
    if page_count == 0 {
        return vec![];
    }
    if page_count <= 5 {
        return (0..page_count).collect();
    }
    // Sample: first 3, middle, last 2
    let mut pages = vec![0, 1, 2];
    pages.push(page_count / 2);
    if page_count > 3 {
        pages.push(page_count - 2);
    }
    pages.push(page_count - 1);
    pages.sort();
    pages.dedup();
    pages
}

fn analyze_fonts(spans: &[TextSpan]) -> (String, f32) {
    if spans.is_empty() {
        return ("Unknown".to_string(), 12.0);
    }

    let mut font_counts: HashMap<(&str, u32), usize> = HashMap::new();
    for span in spans {
        let key_size = (span.font_size * 10.0) as u32;
        *font_counts.entry((&span.font_name, key_size)).or_default() += span.text.len();
    }

    let (best_key, _) = font_counts.iter().max_by_key(|(_, &count)| count).unwrap();
    (best_key.0.to_string(), best_key.1 as f32 / 10.0)
}

fn detect_columns(spans: &[TextSpan], page_width: f32) -> u8 {
    if spans.is_empty() {
        return 1;
    }

    // Look at X positions of text spans to detect column boundaries
    let margin = page_width * 0.08;
    let content_width = page_width - 2.0 * margin;

    // Collect X start positions (excluding headers/footers)
    let x_positions: Vec<f32> = spans.iter()
        .filter(|s| s.bbox.width > 10.0 && s.text.len() > 5)
        .map(|s| s.bbox.x)
        .collect();

    if x_positions.is_empty() {
        return 1;
    }

    // Histogram of X positions (10-unit bins)
    let mut bins: HashMap<u32, usize> = HashMap::new();
    for &x in &x_positions {
        let bin = (x / 10.0) as u32;
        *bins.entry(bin).or_default() += 1;
    }

    // Count distinct X clusters
    let mut sorted_bins: Vec<(u32, usize)> = bins.into_iter().collect();
    sorted_bins.sort_by_key(|&(bin, _)| bin);

    // Find significant start positions (>10% of total spans start here)
    let threshold = x_positions.len() / 10;
    let significant: Vec<f32> = sorted_bins.iter()
        .filter(|&&(_, count)| count > threshold)
        .map(|&(bin, _)| bin as f32 * 10.0)
        .collect();

    if significant.len() <= 1 {
        return 1;
    }

    // Check if significant positions suggest multi-column
    let mid = margin + content_width / 2.0;
    let has_left_column = significant.iter().any(|&x| x < mid - 20.0);
    let has_right_column = significant.iter().any(|&x| x > mid + 20.0);

    if has_left_column && has_right_column {
        2
    } else {
        1
    }
}

fn detect_domain(text: &str, title: &Option<String>, page_count: usize) -> String {
    let lower = text.to_lowercase();
    let title_lower = title.as_deref().unwrap_or("").to_lowercase();

    // Defense/military
    if lower.contains("mil-std") || lower.contains("mil-hdbk") || lower.contains("mil-spec")
        || lower.contains("department of defense") || lower.contains("distribution statement")
        || lower.contains("navpers") || lower.contains("navsea") || lower.contains("darpa")
        || lower.contains("dtic") || lower.contains("unclassified")
    {
        return "defense".to_string();
    }

    // NIST/standards
    if lower.contains("nist sp") || lower.contains("nist special publication")
        || lower.contains("national institute of standards")
        || lower.contains("fips pub")
    {
        return "standards".to_string();
    }

    // IETF/RFC
    if lower.contains("request for comments") || lower.contains("rfc ")
        || lower.contains("internet engineering task force")
    {
        return "ietf".to_string();
    }

    // NASA
    if lower.contains("nasa") || lower.contains("national aeronautics")
        || lower.contains("nasa technical") || lower.contains("ntrs")
    {
        return "nasa".to_string();
    }

    // Academic/arxiv
    if lower.contains("abstract") && lower.contains("introduction")
        && (lower.contains("references") || lower.contains("bibliography"))
    {
        return "academic".to_string();
    }

    // Engineering
    if lower.contains("drawing no") || lower.contains("revision")
        || lower.contains("engineering change") || lower.contains("bill of materials")
        || lower.contains("specifications") || title_lower.contains("specification")
    {
        return "engineering".to_string();
    }

    // Legal
    if lower.contains("hereby") && lower.contains("whereas")
        || lower.contains("terms and conditions") || lower.contains("contract")
    {
        return "legal".to_string();
    }

    // Slide deck
    if page_count > 5 && lower.len() < 500 {
        return "slides".to_string();
    }

    "general".to_string()
}

fn domain_to_preset(domain: &str) -> String {
    match domain {
        "defense" => "defense_document",
        "standards" => "standards_publication",
        "ietf" => "rfc_document",
        "nasa" => "technical_report",
        "academic" => "academic_paper",
        "engineering" => "engineering_document",
        "legal" => "legal_document",
        "slides" => "slide_deck",
        _ => "general_document",
    }.to_string()
}

fn compute_complexity(
    page_count: usize, columns: u8, has_tables: bool, has_images: bool,
    has_forms: bool, is_scanned: bool, has_annotations: bool, avg_chars: f32,
) -> u8 {
    let mut score: u8 = 1;

    if page_count > 50 { score += 1; }
    if page_count > 200 { score += 1; }
    if columns > 1 { score += 1; }
    if has_tables { score += 1; }
    if has_images { score += 1; }
    if has_forms { score += 1; }
    if is_scanned { score += 2; }
    if has_annotations { score += 1; }
    if avg_chars > 3000.0 { score += 1; }

    score.min(10)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_domain_detection() {
        assert_eq!(detect_domain("MIL-STD-498 Department of Defense", &None, 50), "defense");
        assert_eq!(detect_domain("NIST SP 800-53 National Institute of Standards", &None, 100), "standards");
        assert_eq!(detect_domain("Request for Comments: 9136 IETF", &None, 20), "ietf");
        assert_eq!(detect_domain("Abstract\nIntroduction\nReferences", &None, 12), "academic");
        assert_eq!(detect_domain("Hello world this is general text", &None, 5), "general");
    }

    #[test]
    fn test_complexity_scoring() {
        // Simple document
        assert_eq!(compute_complexity(10, 1, false, false, false, false, false, 1000.0), 1);
        // Complex document
        let score = compute_complexity(300, 2, true, true, true, true, true, 5000.0);
        assert!(score >= 8);
    }

    #[test]
    fn test_sample_pages() {
        assert_eq!(select_sample_pages(0), Vec::<usize>::new());
        assert_eq!(select_sample_pages(3), vec![0, 1, 2]);
        let pages = select_sample_pages(100);
        assert!(pages.contains(&0));
        assert!(pages.contains(&50));
        assert!(pages.contains(&99));
    }
}
