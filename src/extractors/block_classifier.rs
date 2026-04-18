use crate::geometry::Rect;
use crate::layout::text_block::TextSpan;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum BlockType {
    Header,
    Body,
    Footer,
    PageNumber,
    List,
    Caption,
    Footnote,
    Title,
    Subtitle,
    TableOfContents,
    Reference,
    Equation,
    Boilerplate,
}

impl BlockType {
    /// Returns the string representation of the block type.
    pub fn as_str(&self) -> &'static str {
        match self {
            BlockType::Header => "header",
            BlockType::Body => "body",
            BlockType::Footer => "footer",
            BlockType::PageNumber => "page_number",
            BlockType::List => "list",
            BlockType::Caption => "caption",
            BlockType::Footnote => "footnote",
            BlockType::Title => "title",
            BlockType::Subtitle => "subtitle",
            BlockType::TableOfContents => "toc",
            BlockType::Reference => "reference",
            BlockType::Equation => "equation",
            BlockType::Boilerplate => "boilerplate",
        }
    }
}

/// Section numbering type detected in text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NumberingType {
    /// Decimal like "1.2.3"
    Decimal,
    /// Decimal with parenthesis like "1)"
    DecimalParen,
    /// Roman numeral like "IV."
    Roman,
    /// Roman numeral in parentheses like "(iv)"
    RomanParen,
    /// Alphabetic like "A." or "A.1"
    Alpha,
    /// Labeled like "Appendix A" or "Chapter 3"
    Labeled,
    /// No numbering detected
    None,
}

/// Result of analyzing section numbering in text.
#[derive(Debug, Clone)]
pub struct NumberingAnalysis {
    /// Whether any numbering was detected
    pub has_numbering: bool,
    /// Type of numbering found
    pub numbering_type: NumberingType,
    /// Depth level (e.g., "1.2.3" = depth 3)
    pub depth_level: u8,
    /// The extracted number text (e.g., "1.2.3")
    pub number_text: String,
    /// The extracted title text (after the number)
    pub title_text: String,
    /// Confidence in the numbering detection (0.0-1.0)
    pub confidence: f32,
}

/// Disposition: what Rust recommends the caller do with this block.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HeaderDisposition {
    /// Rust is confident this IS a header (confidence >= 0.85). No escalation needed.
    Accept,
    /// Rust is confident this is NOT a header (confidence <= 0.15). No escalation needed.
    Reject,
    /// Ambiguous — caller should escalate to classifier or LLM.
    /// The `features` dict is designed to feed directly into `/assistant` cascade.
    Escalate,
}

/// Result of header validation: extracted features + disposition.
///
/// For `Accept`/`Reject`, the caller can trust `is_header`.
/// For `Escalate`, the caller should send `features` to the cascade
/// (classifier → LLM) and use the cascade's decision instead.
#[derive(Debug, Clone)]
pub struct HeaderValidation {
    /// Rust's best guess — only authoritative when disposition != Escalate
    pub is_header: bool,
    /// Raw confidence score (0.0-1.0)
    pub confidence: f32,
    /// What the caller should do
    pub disposition: HeaderDisposition,
    /// Header level if detected (1=highest)
    pub level: Option<u8>,
    /// Positive and negative signals found (human-readable for debugging)
    pub reasons: Vec<&'static str>,
    /// Numbering analysis
    pub numbering: NumberingAnalysis,
    /// Extracted features suitable for classifier input (sklearn/ONNX)
    pub features: HeaderFeatures,
}

/// Numeric features extracted from the text, suitable for classifier input.
///
/// These match the feature set used by the `header-verdict` sklearn model
/// registered in `/assistant`'s model_registry.json.
#[derive(Debug, Clone)]
pub struct HeaderFeatures {
    /// Length of text in characters
    pub text_len: usize,
    /// Whether text starts with a section number (e.g. "1.2.3 ")
    pub has_number_prefix: bool,
    /// Font size of the block
    pub font_size: f32,
    /// Font size relative to page median
    pub size_ratio: f32,
    /// Whether any span is bold
    pub is_bold: bool,
    /// Text ends with "."
    pub ends_with_period: bool,
    /// Text ends with ":"
    pub ends_with_colon: bool,
    /// Text ends with ";" or ","
    pub ends_with_other_punct: bool,
    /// Starts with a bullet character
    pub has_bullet_char: bool,
    /// Contains "Table|Figure N" caption pattern
    pub is_caption_pattern: bool,
    /// Multiple sentence breaks detected
    pub is_multi_sentence: bool,
    /// Word count
    pub word_count: usize,
    /// Ratio of capitalized words (0.0-1.0)
    pub title_case_ratio: f32,
    /// All alphabetic chars are uppercase
    pub is_all_caps: bool,
    /// Section numbering depth (0 = no numbering)
    pub numbering_depth: u8,
    /// Has formal prefix (Chapter, Section, Appendix, etc.)
    pub has_formal_prefix: bool,
    /// Contains parentheses
    pub has_parentheses: bool,
    /// Text length > 180
    pub is_too_long: bool,
}

#[derive(Debug, Clone)]
pub struct ClassifiedBlock {
    /// The classified block type
    pub block_type: BlockType,
    /// Full text content of the block
    pub text: String,
    /// Bounding box in PDF coordinates
    pub bbox: Rect,
    /// Average font size of the block
    pub font_size: f32,
    /// Primary font name
    pub font_name: String,
    /// Whether any span in the block is bold
    pub is_bold: bool,
    /// Classification confidence (0.0-1.0)
    pub confidence: f32,
    /// Header level if this is a title/section header (0=document title, 1-6=sections)
    pub header_level: Option<u8>,
    /// Detailed header validation (only populated for candidate headers)
    pub header_validation: Option<HeaderValidation>,
}

pub struct BlockClassifier {
    page_width: f32,
    page_height: f32,
    median_font_size: f32,
    max_font_size: f32,
    header_ratio: f32,
}

impl BlockClassifier {
    /// Create a new block classifier with page dimensions and font statistics from spans.
    pub fn new(page_width: f32, page_height: f32, spans: &[TextSpan]) -> Self {
        Self::new_with_overrides(page_width, page_height, spans, None, None)
    }

    /// Create a block classifier with optional overrides for convergence tuning.
    ///
    /// `body_font_size_override`: If set, use this as the median font size instead
    /// of auto-computing from spans. Fixes misclassification when code/mono fonts
    /// skew the median.
    ///
    /// `header_ratio_override`: If set, use this ratio instead of 1.2x for the
    /// large_font_threshold in validate_header.
    pub fn new_with_overrides(
        page_width: f32,
        page_height: f32,
        spans: &[TextSpan],
        body_font_size_override: Option<f32>,
        header_ratio_override: Option<f32>,
    ) -> Self {
        let mut sizes: Vec<f32> = spans.iter().map(|s| s.font_size).collect();
        sizes.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let auto_median = if sizes.is_empty() {
            12.0
        } else {
            sizes[sizes.len() / 2]
        };
        let median_font_size = body_font_size_override.unwrap_or(auto_median);
        let max_font_size = sizes.last().copied().unwrap_or(12.0);

        Self {
            page_width,
            page_height,
            median_font_size,
            max_font_size,
            header_ratio: header_ratio_override.unwrap_or(1.2),
        }
    }

    /// Classify all spans on a page into typed blocks.
    pub fn classify_spans(&self, spans: &[TextSpan]) -> Vec<ClassifiedBlock> {
        let lines = group_spans_into_lines(spans);

        let mut blocks = Vec::new();
        for line_spans in &lines {
            if line_spans.is_empty() {
                continue;
            }
            let block = self.classify_line(line_spans);
            blocks.push(block);
        }

        merge_consecutive_body(&mut blocks);

        blocks
    }

    fn classify_line(&self, spans: &[&TextSpan]) -> ClassifiedBlock {
        let text: String = spans
            .iter()
            .map(|s| s.text.as_str())
            .collect::<Vec<_>>()
            .join("");
        let text = text.trim().to_string();

        let bbox = spans
            .iter()
            .fold(spans[0].bbox, |acc, s| acc.union(&s.bbox));
        let avg_font_size = spans.iter().map(|s| s.font_size).sum::<f32>() / spans.len() as f32;
        let is_bold = spans
            .iter()
            .any(|s| s.font_weight == crate::layout::text_block::FontWeight::Bold);
        let font_name = spans[0].font_name.clone();

        let y_ratio = bbox.y / self.page_height;
        let x_center = bbox.x + bbox.width / 2.0;
        let page_center = self.page_width / 2.0;
        let is_centered = (x_center - page_center).abs() < self.page_width * 0.1;
        let size_ratio = avg_font_size / self.median_font_size;
        let trimmed = text.trim();

        // Page number detection (top/bottom of page)
        if is_page_number(trimmed, y_ratio) {
            return self.make_block(
                BlockType::PageNumber,
                text,
                bbox,
                avg_font_size,
                font_name,
                is_bold,
                0.9,
                None,
                None,
            );
        }

        // Boilerplate detection (TLP, copyright, arXiv stamps, etc.)
        if is_boilerplate(trimmed) {
            return self.make_block(
                BlockType::Boilerplate,
                text,
                bbox,
                avg_font_size,
                font_name,
                is_bold,
                0.9,
                None,
                None,
            );
        }

        // Running footer (bottom 8% of page, short text)
        if y_ratio > 0.92 && trimmed.len() < 200 {
            // But check if it's actually a section header that happens to be at the bottom
            if !is_content_exception(trimmed) {
                return self.make_block(
                    BlockType::Footer,
                    text,
                    bbox,
                    avg_font_size,
                    font_name,
                    is_bold,
                    0.85,
                    None,
                    None,
                );
            }
        }

        // Running header (top 8% of page, short text, not larger than median)
        if y_ratio < 0.08 && trimmed.len() < 200 && avg_font_size <= self.median_font_size {
            if !is_content_exception(trimmed) {
                return self.make_block(
                    BlockType::Header,
                    text,
                    bbox,
                    avg_font_size,
                    font_name,
                    is_bold,
                    0.8,
                    None,
                    None,
                );
            }
        }

        // Caption detection (Figure/Table prefix)
        if is_caption(trimmed, size_ratio) {
            return self.make_block(
                BlockType::Caption,
                text,
                bbox,
                avg_font_size,
                font_name,
                is_bold,
                0.8,
                None,
                None,
            );
        }

        // Footnote detection (small font, bottom of page, starts with marker)
        if size_ratio < 0.85 && y_ratio > 0.75 && trimmed.len() < 500 {
            if trimmed.starts_with(|c: char| c.is_ascii_digit())
                || trimmed.starts_with('*')
                || trimmed.starts_with('†')
            {
                return self.make_block(
                    BlockType::Footnote,
                    text,
                    bbox,
                    avg_font_size,
                    font_name,
                    is_bold,
                    0.75,
                    None,
                    None,
                );
            }
        }

        // Reference/bibliography detection
        if is_reference_entry(trimmed) {
            return self.make_block(
                BlockType::Reference,
                text,
                bbox,
                avg_font_size,
                font_name,
                is_bold,
                0.7,
                None,
                None,
            );
        }

        // TOC entry detection
        if is_toc_entry(trimmed) {
            return self.make_block(
                BlockType::TableOfContents,
                text,
                bbox,
                avg_font_size,
                font_name,
                is_bold,
                0.8,
                None,
                None,
            );
        }

        // Equation detection
        if is_equation(trimmed) {
            return self.make_block(
                BlockType::Equation,
                text,
                bbox,
                avg_font_size,
                font_name,
                is_bold,
                0.6,
                None,
                None,
            );
        }

        // List item detection (but not if bold/large — those are likely numbered headers)
        // Must come after caption check since "Table 1." could match list pattern
        if is_list_item(trimmed) && !is_bold && size_ratio <= 1.15 {
            // Extra check: don't classify numbered headers as list items
            let numbering = analyze_section_numbering(trimmed);
            if !numbering.has_numbering || numbering.depth_level <= 1 {
                // Only if it doesn't look like a real section number
                if !numbering.has_numbering {
                    return self.make_block(
                        BlockType::List,
                        text,
                        bbox,
                        avg_font_size,
                        font_name,
                        is_bold,
                        0.85,
                        None,
                        None,
                    );
                }
            }
        }

        // Title detection (document title: large font, centered, near top)
        if size_ratio > 1.8 && is_centered && y_ratio < 0.4 && trimmed.len() < 200 {
            return self.make_block(
                BlockType::Title,
                text,
                bbox,
                avg_font_size,
                font_name,
                is_bold,
                0.85,
                Some(0),
                None,
            );
        }

        // Section header detection — the main S03-equivalent logic
        // Candidate: bold or larger-than-median font, short text
        if (is_bold || size_ratio > 1.15) && trimmed.len() < 200 {
            let validation = validate_header_with_ratio(
                trimmed,
                is_bold,
                avg_font_size,
                self.median_font_size,
                self.max_font_size,
                self.header_ratio,
            );

            if validation.is_header {
                let level = validation.level.unwrap_or_else(|| {
                    compute_header_level(size_ratio, is_bold, &self.max_font_size, avg_font_size)
                });
                return self.make_block(
                    BlockType::Title,
                    text,
                    bbox,
                    avg_font_size,
                    font_name,
                    is_bold,
                    validation.confidence,
                    Some(level),
                    Some(validation),
                );
            } else {
                // Demoted to body — the block looks like a header visually but fails heuristics
                return self.make_block(
                    BlockType::Body,
                    text,
                    bbox,
                    avg_font_size,
                    font_name,
                    is_bold,
                    validation.confidence,
                    None,
                    Some(validation),
                );
            }
        }

        // Default: body text
        self.make_block(
            BlockType::Body,
            text,
            bbox,
            avg_font_size,
            font_name,
            is_bold,
            0.9,
            None,
            None,
        )
    }

    fn make_block(
        &self,
        block_type: BlockType,
        text: String,
        bbox: Rect,
        font_size: f32,
        font_name: String,
        is_bold: bool,
        confidence: f32,
        header_level: Option<u8>,
        header_validation: Option<HeaderValidation>,
    ) -> ClassifiedBlock {
        ClassifiedBlock {
            block_type,
            text,
            bbox,
            font_size,
            font_name,
            is_bold,
            confidence,
            header_level,
            header_validation,
        }
    }
}

// ---------------------------------------------------------------------------
// Section numbering analysis (replaces Python's analyze_section_numbering)
// ---------------------------------------------------------------------------

/// Analyze section numbering patterns in text.
///
/// Detects decimal (1.2.3), parenthesized (1), (iv)), roman (IV.), alpha (A.),
/// and labeled (Chapter 3, Appendix A) numbering schemes.
pub fn analyze_section_numbering(text: &str) -> NumberingAnalysis {
    let t = text.trim();
    if t.is_empty() {
        return NumberingAnalysis {
            has_numbering: false,
            numbering_type: NumberingType::None,
            depth_level: 0,
            number_text: String::new(),
            title_text: t.to_string(),
            confidence: 0.0,
        };
    }

    // Try each pattern in priority order
    // 1. Labeled: "Appendix A Title" / "Chapter 3 Title"
    if let Some(r) = try_labeled(t) {
        return r;
    }
    // 2. Decimal: "1.2.3 Title" or "1.2.3. Title" or "1 Title"
    if let Some(r) = try_decimal(t) {
        return r;
    }
    // 3. Decimal paren: "1) Title"
    if let Some(r) = try_decimal_paren(t) {
        return r;
    }
    // 4. Roman paren: "(iv) Title"
    if let Some(r) = try_roman_paren(t) {
        return r;
    }
    // 5. Roman: "IV. Title"
    if let Some(r) = try_roman(t) {
        return r;
    }
    // 6. Alpha: "A. Title" or "A.1 Title"
    if let Some(r) = try_alpha(t) {
        return r;
    }

    NumberingAnalysis {
        has_numbering: false,
        numbering_type: NumberingType::None,
        depth_level: 0,
        number_text: String::new(),
        title_text: t.to_string(),
        confidence: 0.0,
    }
}

fn try_labeled(text: &str) -> Option<NumberingAnalysis> {
    let lower = text.to_lowercase();
    let labels = ["appendix", "annex", "section", "chapter", "part"];
    for label in &labels {
        if !lower.starts_with(label) {
            continue;
        }
        let rest = &text[label.len()..];
        if !rest.starts_with(|c: char| c.is_whitespace()) {
            continue;
        }
        let rest = rest.trim_start();
        // Extract the number/identifier part
        let num_end = rest
            .find(|c: char| c == ':' || c == '.' || c == '-' || c == '–' || c == '—')
            .or_else(|| rest.find(|c: char| c.is_whitespace()));
        let (num_text, title_text) = if let Some(pos) = num_end {
            let num = rest[..pos].trim();
            let title = rest[pos..].trim_start_matches(|c: char| {
                c == ':' || c == '.' || c == '-' || c == '–' || c == '—' || c.is_whitespace()
            });
            (num.to_string(), title.to_string())
        } else {
            (rest.trim().to_string(), String::new())
        };

        if num_text.is_empty() {
            continue;
        }

        return Some(NumberingAnalysis {
            has_numbering: true,
            numbering_type: NumberingType::Labeled,
            depth_level: 1,
            number_text: num_text,
            title_text,
            confidence: 0.90,
        });
    }
    None
}

fn try_decimal(text: &str) -> Option<NumberingAnalysis> {
    let t = text.trim_start();
    // Match: digits optionally followed by .digits, then separator and title
    // "1.2.3 Title", "1.2.3. Title", "1: Title", "1 Title"
    let mut i = 0;
    let bytes = t.as_bytes();
    if bytes.is_empty() || !bytes[0].is_ascii_digit() {
        return None;
    }

    // Consume number part: digits(.digits)*(.alpha)?
    while i < bytes.len() && bytes[i].is_ascii_digit() {
        i += 1;
    }
    while i < bytes.len() && bytes[i] == b'.' {
        i += 1;
        // After a dot, consume digits or a single lowercase letter
        if i < bytes.len() && bytes[i].is_ascii_digit() {
            while i < bytes.len() && bytes[i].is_ascii_digit() {
                i += 1;
            }
        } else if i < bytes.len() && bytes[i].is_ascii_lowercase() {
            i += 1;
        }
    }

    let num_text = &t[..i];
    let rest = &t[i..];

    // Must have separator (.:)-–— or space) then text
    let rest_trimmed = rest.trim_start_matches(|c: char| {
        c == ':' || c == '.' || c == ')' || c == '-' || c == '–' || c == '—'
    });
    if rest_trimmed.is_empty() && rest.is_empty() {
        return None; // Just a number with no title
    }
    let rest_trimmed = rest_trimmed.trim_start();
    if rest_trimmed.is_empty() && !rest.trim().is_empty() {
        // Had separator but no title — still valid (e.g., "1.2.3.")
    }

    // Require at least a space or separator after the number
    if !rest.is_empty()
        && !rest.starts_with(|c: char| {
            c.is_whitespace()
                || c == ':'
                || c == '.'
                || c == ')'
                || c == '-'
                || c == '–'
                || c == '—'
        })
    {
        return None;
    }

    // Count depth
    let depth = num_text.matches('.').count() as u8 + 1;
    let depth = depth.min(6);

    Some(NumberingAnalysis {
        has_numbering: true,
        numbering_type: NumberingType::Decimal,
        depth_level: depth,
        number_text: num_text.to_string(),
        title_text: rest_trimmed.to_string(),
        confidence: 0.95,
    })
}

fn try_decimal_paren(text: &str) -> Option<NumberingAnalysis> {
    let t = text.trim_start();
    let bytes = t.as_bytes();
    if bytes.is_empty() || !bytes[0].is_ascii_digit() {
        return None;
    }
    let mut i = 0;
    while i < bytes.len() && bytes[i].is_ascii_digit() {
        i += 1;
    }
    if i >= bytes.len() || bytes[i] != b')' {
        return None;
    }
    let num_text = &t[..i];
    let rest = t[i + 1..].trim_start();
    if rest.is_empty() {
        return None;
    }

    Some(NumberingAnalysis {
        has_numbering: true,
        numbering_type: NumberingType::DecimalParen,
        depth_level: 1,
        number_text: num_text.to_string(),
        title_text: rest.to_string(),
        confidence: 0.90,
    })
}

fn try_roman_paren(text: &str) -> Option<NumberingAnalysis> {
    let t = text.trim_start();
    if !t.starts_with('(') {
        return None;
    }
    let close = t.find(')')?;
    if close > 8 || close < 2 {
        return None;
    }
    let inner = &t[1..close];
    if !inner.chars().all(|c| "ivxlcdmIVXLCDM".contains(c)) {
        return None;
    }
    let rest = t[close + 1..].trim_start();
    if rest.is_empty() {
        return None;
    }

    Some(NumberingAnalysis {
        has_numbering: true,
        numbering_type: NumberingType::RomanParen,
        depth_level: 1,
        number_text: inner.to_string(),
        title_text: rest.to_string(),
        confidence: 0.85,
    })
}

fn try_roman(text: &str) -> Option<NumberingAnalysis> {
    let t = text.trim_start();
    let bytes = t.as_bytes();
    if bytes.is_empty() {
        return None;
    }
    // Must start with a valid roman numeral character
    if !b"IVXLCDM".contains(&bytes[0].to_ascii_uppercase()) {
        return None;
    }
    let mut i = 0;
    while i < bytes.len() && b"IVXLCDMivxlcdm.".contains(&bytes[i]) {
        i += 1;
    }
    // Must end with a dot followed by space
    if i == 0 {
        return None;
    }
    let num_part = &t[..i];
    // Require trailing dot
    if !num_part.ends_with('.') {
        return None;
    }
    let num_text = num_part.trim_end_matches('.');
    // Validate it's actually roman numerals (not words like "ID", "DIV")
    if !num_text.chars().all(|c| "IVXLCDMivxlcdm.".contains(c)) {
        return None;
    }
    // Must have at least a space after
    let rest = t[i..].trim_start();
    if rest.is_empty() {
        return None;
    }

    Some(NumberingAnalysis {
        has_numbering: true,
        numbering_type: NumberingType::Roman,
        depth_level: num_text.matches('.').count() as u8 + 1,
        number_text: num_text.to_string(),
        title_text: rest.to_string(),
        confidence: 0.85,
    })
}

fn try_alpha(text: &str) -> Option<NumberingAnalysis> {
    let t = text.trim_start();
    let bytes = t.as_bytes();
    if bytes.is_empty() || !bytes[0].is_ascii_uppercase() {
        return None;
    }
    // "A." or "A.1." etc.
    let mut i = 1;
    while i < bytes.len() && (bytes[i] == b'.' || bytes[i].is_ascii_digit()) {
        i += 1;
    }
    if i < 2 || !t[..i].contains('.') {
        return None;
    }
    let num_text = t[..i].trim_end_matches('.');
    let rest = t[i..].trim_start();
    // Title must not start with '=' (avoids "A = B" style equations)
    if rest.starts_with('=') {
        return None;
    }
    if rest.is_empty() {
        return None;
    }

    let depth = num_text.matches('.').count() as u8 + 1;

    Some(NumberingAnalysis {
        has_numbering: true,
        numbering_type: NumberingType::Alpha,
        depth_level: depth,
        number_text: num_text.to_string(),
        title_text: rest.to_string(),
        confidence: 0.80,
    })
}

// ---------------------------------------------------------------------------
// Header validation (replaces Python's is_probable_pdf_section_header + S03 heuristics)
// ---------------------------------------------------------------------------

/// Validate whether text that visually looks like a header (bold/large font)
/// is actually a section header or should be demoted to body text.
///
/// Returns `HeaderValidation` with:
/// - `features`: numeric feature vector for classifier input
/// - `disposition`: Accept (>= 0.85), Reject (<= 0.15), or Escalate (ambiguous)
///
/// For `Accept`/`Reject`, the caller can trust `is_header` directly.
/// For `Escalate`, the caller should send `features` to the `/assistant` cascade
/// (classifier → LLM) and use the cascade's decision instead. Shadow labels
/// accumulate in training data until the classifier can be trained.
///
/// This absorbs the logic from:
/// - S03 suspicious_headers auto-accept/auto-reject heuristics
/// - sections/heuristics.py::is_probable_pdf_section_header()
pub fn validate_header(
    text: &str,
    is_bold: bool,
    font_size: f32,
    median_font_size: f32,
    max_font_size: f32,
) -> HeaderValidation {
    validate_header_with_ratio(text, is_bold, font_size, median_font_size, max_font_size, 1.2)
}

/// Validate header with a custom font ratio threshold (for convergence tuning).
pub fn validate_header_with_ratio(
    text: &str,
    is_bold: bool,
    font_size: f32,
    median_font_size: f32,
    max_font_size: f32,
    header_ratio: f32,
) -> HeaderValidation {
    let trimmed = text.trim();
    let numbering = analyze_section_numbering(trimmed);

    // --- Step 1: Extract features (pure observation, no decisions) ---
    let words: Vec<&str> = trimmed.split_whitespace().collect();
    let word_count = words.len();
    let size_ratio = if median_font_size > 0.0 {
        font_size / median_font_size
    } else {
        1.0
    };

    let cap_count = words
        .iter()
        .filter(|w| w.chars().next().map_or(false, |c| c.is_uppercase()))
        .count();
    let title_case_ratio = if word_count > 0 {
        cap_count as f32 / word_count as f32
    } else {
        0.0
    };

    let alpha_chars: Vec<char> = trimmed.chars().filter(|c| c.is_alphabetic()).collect();
    let is_all_caps = !alpha_chars.is_empty() && alpha_chars.iter().all(|c| c.is_uppercase());

    let lower = trimmed.to_lowercase();
    let has_formal_prefix = lower.starts_with("chapter ")
        || lower.starts_with("section ")
        || lower.starts_with("part ")
        || lower.starts_with("article ")
        || lower.starts_with("appendix ")
        || lower.starts_with("annex ")
        || lower.starts_with("module ")
        || lower.starts_with("unit ");

    let features = HeaderFeatures {
        text_len: trimmed.len(),
        has_number_prefix: numbering.has_numbering,
        font_size,
        size_ratio,
        is_bold,
        ends_with_period: trimmed.ends_with('.'),
        ends_with_colon: trimmed.ends_with(':'),
        ends_with_other_punct: trimmed.ends_with(';') || trimmed.ends_with(','),
        has_bullet_char: has_bullet_prefix(trimmed),
        is_caption_pattern: is_strict_caption_pattern(trimmed),
        is_multi_sentence: count_sentence_breaks(trimmed) >= 2,
        word_count,
        title_case_ratio,
        is_all_caps,
        numbering_depth: numbering.depth_level,
        has_formal_prefix,
        has_parentheses: trimmed.contains('(') && trimmed.contains(')'),
        is_too_long: trimmed.len() > 180,
    };

    // --- Step 2: Compute confidence from features ---
    let mut confidence: f32 = 0.0;
    let mut reasons: Vec<&'static str> = Vec::new();
    let mut level: Option<u8> = None;

    // Requirement ID negative (engineering docs) — hard reject
    if trimmed.starts_with("REQ-") || trimmed.starts_with("req-") {
        return HeaderValidation {
            is_header: false,
            confidence: 0.0,
            disposition: HeaderDisposition::Reject,
            level: None,
            reasons: vec!["requirement_id_negative"],
            numbering,
            features,
        };
    }

    // --- Positive signals ---

    if features.has_number_prefix {
        confidence = confidence.max(0.9);
        reasons.push("numbering");
        level = Some(numbering.depth_level);
    }

    let large_font_threshold = median_font_size * header_ratio;
    if features.is_bold && font_size >= large_font_threshold {
        confidence = confidence.max(0.75);
        reasons.push("bold_large_font");
    }

    if features.has_formal_prefix {
        confidence = confidence.max(0.85);
        reasons.push("formal_prefix");
    }

    if is_roman_start(trimmed) {
        confidence = confidence.max(0.7);
        reasons.push("roman_start");
    }

    if word_count >= 2 && word_count <= 15 && features.title_case_ratio >= 0.7 {
        confidence = confidence.max(0.45);
        reasons.push("title_case_like");
    }

    if features.is_all_caps
        && trimmed.len() >= 5
        && trimmed.len() <= 60
        && !trimmed.chars().any(|c| c.is_ascii_digit())
    {
        confidence = confidence.max(0.45);
        reasons.push("all_caps_medium");
    }

    // --- Negative signals (only when NOT numbered) ---
    if !features.has_number_prefix {
        if features.is_caption_pattern {
            confidence = confidence.min(0.05);
            reasons.push("caption_negative");
        }

        if (features.ends_with_period || trimmed.ends_with(';')) && trimmed.len() > 5 {
            confidence = confidence.min(0.10);
            reasons.push("sentence_negative");
        }

        if trimmed.len() <= 40 && features.ends_with_colon {
            confidence = confidence.min(0.10);
            reasons.push("short_colon_negative");
        }

        if trimmed.ends_with(',') {
            confidence = confidence.min(0.05);
            reasons.push("trailing_comma_negative");
        }

        if features.is_multi_sentence {
            confidence = confidence.min(0.01);
            reasons.push("multi_sentence_negative");
        }

        if features.has_parentheses {
            confidence = confidence.min(0.35);
            reasons.push("parentheses_negative");
        }

        if word_count == 1 && !trimmed.chars().any(|c| c.is_ascii_digit()) {
            confidence = confidence.min(0.3);
            reasons.push("single_word_negative");
        }

        if trimmed.len() < 10 && features.is_all_caps {
            confidence = confidence.min(0.25);
            reasons.push("short_all_caps_negative");
        }

        if features.is_too_long {
            confidence = confidence.min(0.25);
            reasons.push("too_long");
        }

        if features.has_bullet_char {
            confidence = confidence.min(0.05);
            reasons.push("bullet_prefix_negative");
        }

        if is_continued_pattern(trimmed) {
            confidence = confidence.min(0.05);
            reasons.push("continued_negative");
        }

        if try_alpha(trimmed).is_some() {
            confidence = confidence.max(0.6);
            reasons.push("letter_section");
        }
    }

    // Compute level if not set
    if level.is_none() && confidence >= 0.5 {
        level = Some(if numbering.has_numbering {
            numbering.depth_level
        } else {
            2
        });
    }

    // --- Step 3: Set disposition based on confidence bands ---
    let is_header = confidence >= 0.5;
    let disposition = if confidence >= 0.85 {
        HeaderDisposition::Accept
    } else if confidence <= 0.15 {
        HeaderDisposition::Reject
    } else {
        HeaderDisposition::Escalate
    };

    HeaderValidation {
        is_header,
        confidence,
        disposition,
        level,
        reasons,
        numbering,
        features,
    }
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

fn group_spans_into_lines(spans: &[TextSpan]) -> Vec<Vec<&TextSpan>> {
    if spans.is_empty() {
        return vec![];
    }

    let mut lines: Vec<Vec<&TextSpan>> = vec![vec![&spans[0]]];

    for span in &spans[1..] {
        let last_line = lines.last().unwrap();
        let last_span = last_line.last().unwrap();
        let y_diff = (span.bbox.y - last_span.bbox.y).abs();
        let threshold = last_span.font_size.max(span.font_size) * 0.5;

        if y_diff < threshold {
            lines.last_mut().unwrap().push(span);
        } else {
            lines.push(vec![span]);
        }
    }

    lines
}

fn merge_consecutive_body(blocks: &mut Vec<ClassifiedBlock>) {
    let mut i = 0;
    while i + 1 < blocks.len() {
        if blocks[i].block_type == BlockType::Body
            && blocks[i + 1].block_type == BlockType::Body
            && (blocks[i].bbox.y - blocks[i + 1].bbox.y).abs() < blocks[i].font_size * 1.5
        {
            let next = blocks.remove(i + 1);
            blocks[i].text.push(' ');
            blocks[i].text.push_str(&next.text);
            blocks[i].bbox = blocks[i].bbox.union(&next.bbox);
        } else {
            i += 1;
        }
    }
}

fn is_page_number(text: &str, y_ratio: f32) -> bool {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return false;
    }
    if y_ratio > 0.08 && y_ratio < 0.92 {
        return false;
    }
    if trimmed.parse::<u32>().is_ok() && trimmed.len() <= 5 {
        return true;
    }
    if trimmed.chars().all(|c| "ivxlcdmIVXLCDM".contains(c)) && trimmed.len() <= 6 {
        return true;
    }
    if trimmed.starts_with("Page ") || trimmed.starts_with("page ") {
        return true;
    }
    if trimmed.starts_with('-') && trimmed.ends_with('-') && trimmed.len() < 10 {
        return true;
    }
    // "1 / 10" or "1/10" style
    let parts: Vec<&str> = trimmed.split('/').collect();
    if parts.len() == 2
        && parts[0].trim().parse::<u32>().is_ok()
        && parts[1].trim().parse::<u32>().is_ok()
    {
        return true;
    }
    // "[1]" style at top/bottom
    if trimmed.starts_with('[') && trimmed.ends_with(']') && trimmed.len() < 8 {
        let inner = &trimmed[1..trimmed.len() - 1];
        if inner.trim().parse::<u32>().is_ok() {
            return true;
        }
    }
    false
}

/// Detect boilerplate content that should not be treated as document content.
fn is_boilerplate(text: &str) -> bool {
    let trimmed = text.trim();
    let lower = trimmed.to_lowercase();

    // TLP markers
    if lower.starts_with("tlp:") || lower.starts_with("tlp :") {
        return true;
    }
    // Copyright / proprietary / confidential
    if lower.starts_with("confidential")
        || lower.starts_with("proprietary")
        || lower.starts_with("copyright")
    {
        return true;
    }
    // arXiv stamps
    if lower.starts_with("arxiv:") {
        return true;
    }
    // "- continued" or "(continued)"
    if is_continued_pattern(trimmed) {
        return true;
    }
    // Comments "// ..."
    if trimmed.starts_with("//") {
        return true;
    }
    // "Page X of Y"
    if lower.starts_with("page ") && (lower.contains(" of ") || lower.contains("/")) {
        let words: Vec<&str> = lower.split_whitespace().collect();
        if words.len() >= 3 && words.len() <= 5 {
            return true;
        }
    }
    // Document reference patterns
    if lower.starts_with("document no")
        || lower.starts_with("document number")
        || lower.starts_with("document ref")
        || lower.starts_with("revision:")
        || lower.starts_with("version:")
        || lower.starts_with("revision ")
        || lower.starts_with("version ")
    {
        return trimmed.len() < 80;
    }
    // "all rights reserved"
    if lower.contains("all rights reserved") {
        return true;
    }
    // "printed on"
    if lower.starts_with("printed on") {
        return true;
    }
    false
}

/// Check if text is a section header exception that should NOT be filtered
/// even when in the header/footer region.
fn is_content_exception(text: &str) -> bool {
    let trimmed = text.trim();
    // Numbered section headers (1.1 Title)
    let numbering = analyze_section_numbering(trimmed);
    if numbering.has_numbering && numbering.numbering_type == NumberingType::Decimal {
        return true;
    }
    // "Section N", "Chapter N", "Appendix X"
    let lower = trimmed.to_lowercase();
    if lower.starts_with("section ")
        || lower.starts_with("chapter ")
        || lower.starts_with("appendix ")
    {
        return true;
    }
    false
}

fn is_list_item(text: &str) -> bool {
    let trimmed = text.trim();
    // Bullet points
    if trimmed.starts_with('•')
        || trimmed.starts_with('·')
        || trimmed.starts_with('◦')
        || trimmed.starts_with('▪')
        || trimmed.starts_with('▸')
        || trimmed.starts_with('-')
        || trimmed.starts_with('–')
        || trimmed.starts_with('—')
    {
        return true;
    }
    // Numbered lists: "1.", "1)", "(1)", "a.", "a)", "(a)"
    let bytes = trimmed.as_bytes();
    if bytes.len() >= 2 {
        if bytes[0] == b'(' {
            if let Some(close) = trimmed.find(')') {
                if close <= 4 {
                    let inner = &trimmed[1..close];
                    if inner.parse::<u32>().is_ok()
                        || (inner.len() == 1 && inner.chars().next().unwrap().is_alphabetic())
                    {
                        return true;
                    }
                }
            }
        } else if bytes[0].is_ascii_digit() || bytes[0].is_ascii_alphabetic() {
            if let Some(pos) = trimmed.find(|c: char| c == '.' || c == ')') {
                if pos <= 4 {
                    let prefix = &trimmed[..pos];
                    if prefix.parse::<u32>().is_ok()
                        || (prefix.len() == 1 && prefix.chars().next().unwrap().is_alphabetic())
                    {
                        return true;
                    }
                }
            }
        }
    }
    false
}

fn is_caption(text: &str, size_ratio: f32) -> bool {
    let lower = text.to_lowercase();
    let has_prefix = lower.starts_with("figure ")
        || lower.starts_with("fig. ")
        || lower.starts_with("fig ")
        || lower.starts_with("table ")
        || lower.starts_with("chart ")
        || lower.starts_with("diagram ")
        || lower.starts_with("exhibit ")
        || lower.starts_with("plate ")
        || lower.starts_with("listing ");
    has_prefix && (size_ratio < 1.1 || text.len() < 300)
}

/// Strict caption pattern for negative signal: "Table|Figure N[.M][.:]"
fn is_strict_caption_pattern(text: &str) -> bool {
    let trimmed = text.trim();
    let lower = trimmed.to_lowercase();
    // Match "Table N", "Figure N", "Exhibit N", "Listing N" possibly with ".M" and trailing ".:" or "("
    for prefix in &["table ", "figure ", "exhibit ", "listing "] {
        if lower.starts_with(prefix) {
            let rest = &trimmed[prefix.len()..];
            // Must start with a digit
            if rest.starts_with(|c: char| c.is_ascii_digit()) {
                return true;
            }
        }
    }
    false
}

fn is_reference_entry(text: &str) -> bool {
    let trimmed = text.trim();
    if trimmed.starts_with('[') {
        if let Some(close) = trimmed.find(']') {
            if close < 20 {
                return true;
            }
        }
    }
    false
}

fn is_toc_entry(text: &str) -> bool {
    if text.contains("....") || text.contains(". . .") {
        let last_word = text.split_whitespace().last().unwrap_or("");
        if last_word.parse::<u32>().is_ok() {
            return true;
        }
    }
    false
}

fn is_equation(text: &str) -> bool {
    let trimmed = text.trim();
    if trimmed.len() > 200 || trimmed.len() < 3 {
        return false;
    }
    let math_chars = trimmed
        .chars()
        .filter(|c| "=+−×÷∑∏∫∂√∞≈≠≤≥∈∉⊂⊃∩∪αβγδεζηθλμπσφψω".contains(*c))
        .count();
    let total = trimmed.chars().count();
    if total > 0 && math_chars as f32 / total as f32 > 0.15 {
        return true;
    }
    false
}

fn compute_header_level(size_ratio: f32, is_bold: bool, max_font_size: &f32, font_size: f32) -> u8 {
    if *max_font_size > 0.0 && font_size >= *max_font_size * 0.95 {
        return 1;
    }
    if size_ratio > 1.6 {
        return 1;
    }
    if size_ratio > 1.3 {
        return 2;
    }
    if size_ratio > 1.15 && is_bold {
        return 3;
    }
    if is_bold {
        return 4;
    }
    5
}

/// Check for bullet prefix characters.
fn has_bullet_prefix(text: &str) -> bool {
    let trimmed = text.trim();
    trimmed.starts_with('•')
        || trimmed.starts_with('●')
        || trimmed.starts_with('▪')
        || trimmed.starts_with('‣')
        || trimmed.starts_with('⁃')
        || trimmed.starts_with('–')
        || trimmed.starts_with('—')
        || trimmed.starts_with('·')
        || (trimmed.starts_with('-') && trimmed.len() > 1 && trimmed.as_bytes()[1] == b' ')
        || (trimmed.starts_with('*') && trimmed.len() > 1 && trimmed.as_bytes()[1] == b' ')
        || (trimmed.starts_with('+') && trimmed.len() > 1 && trimmed.as_bytes()[1] == b' ')
}

/// Check for "(continued)" or "- continued" patterns.
fn is_continued_pattern(text: &str) -> bool {
    let lower = text.trim().to_lowercase();
    lower == "(continued)" || lower.ends_with("- continued") || lower.ends_with("(continued)")
}

/// Check if text starts with a roman numeral followed by a dot.
fn is_roman_start(text: &str) -> bool {
    let t = text.trim();
    let mut i = 0;
    let bytes = t.as_bytes();
    while i < bytes.len() && b"IVXLCDM".contains(&bytes[i].to_ascii_uppercase()) {
        i += 1;
    }
    if i == 0 || i >= bytes.len() {
        return false;
    }
    // Must be followed by ". "
    bytes[i] == b'.' && i + 1 < bytes.len() && bytes[i + 1] == b' '
}

/// Count sentence breaks (". X" where X is uppercase).
fn count_sentence_breaks(text: &str) -> usize {
    let chars: Vec<char> = text.chars().collect();
    let mut count = 0;
    for i in 0..chars.len().saturating_sub(2) {
        if (chars[i] == '.' || chars[i] == '!' || chars[i] == '?')
            && chars[i + 1] == ' '
            && chars.get(i + 2).map_or(false, |c| c.is_uppercase())
        {
            count += 1;
        }
    }
    count
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::layout::text_block::{Color, FontWeight};

    fn make_span(text: &str, x: f32, y: f32, font_size: f32, bold: bool) -> TextSpan {
        TextSpan {
            text: text.to_string(),
            bbox: Rect {
                x,
                y,
                width: text.len() as f32 * font_size * 0.5,
                height: font_size,
            },
            font_name: "TestFont".to_string(),
            font_size,
            font_weight: if bold {
                FontWeight::Bold
            } else {
                FontWeight::Normal
            },
            is_italic: false,
            color: Color {
                r: 0.0,
                g: 0.0,
                b: 0.0,
            },
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

    // --- Numbering Analysis Tests ---

    #[test]
    fn test_decimal_numbering() {
        let r = analyze_section_numbering("1.2.3 Introduction");
        assert!(r.has_numbering);
        assert_eq!(r.numbering_type, NumberingType::Decimal);
        assert_eq!(r.depth_level, 3);
        assert_eq!(r.number_text, "1.2.3");
        assert_eq!(r.title_text, "Introduction");
    }

    #[test]
    fn test_decimal_single() {
        let r = analyze_section_numbering("3 Results");
        assert!(r.has_numbering);
        assert_eq!(r.numbering_type, NumberingType::Decimal);
        assert_eq!(r.depth_level, 1);
        assert_eq!(r.number_text, "3");
    }

    #[test]
    fn test_labeled_numbering() {
        let r = analyze_section_numbering("Appendix A Security Controls");
        assert!(r.has_numbering);
        assert_eq!(r.numbering_type, NumberingType::Labeled);
        assert_eq!(r.number_text, "A");
    }

    #[test]
    fn test_roman_paren() {
        let r = analyze_section_numbering("(iv) Overview");
        assert!(r.has_numbering);
        assert_eq!(r.numbering_type, NumberingType::RomanParen);
    }

    #[test]
    fn test_no_numbering() {
        let r = analyze_section_numbering("This is just body text.");
        assert!(!r.has_numbering);
    }

    // --- Header Validation Tests ---

    #[test]
    fn test_numbered_header_accepted() {
        let v = validate_header("1.2 Introduction", true, 14.0, 11.0, 18.0);
        assert!(v.is_header);
        assert!(v.confidence >= 0.9);
        assert!(v.reasons.contains(&"numbering"));
    }

    #[test]
    fn test_sentence_ending_rejected() {
        let v = validate_header("This is a sentence that ends.", true, 14.0, 11.0, 18.0);
        assert!(!v.is_header, "Sentence-ending text should be rejected as header");
        assert!(v.reasons.contains(&"sentence_negative"));
    }

    #[test]
    fn test_colon_label_rejected() {
        let v = validate_header("Name:", true, 14.0, 11.0, 18.0);
        assert!(!v.is_header, "Short colon label should not be a header");
        assert!(v.reasons.contains(&"short_colon_negative"));
    }

    #[test]
    fn test_caption_rejected() {
        let v = validate_header("Table 3.2 Results", false, 10.0, 11.0, 18.0);
        assert!(!v.is_header);
        assert!(v.reasons.contains(&"caption_negative"));
    }

    #[test]
    fn test_bullet_prefix_rejected() {
        let v = validate_header("• First item in list", true, 14.0, 11.0, 18.0);
        assert!(!v.is_header);
        assert!(v.reasons.contains(&"bullet_prefix_negative"));
    }

    #[test]
    fn test_formal_prefix_accepted() {
        let v = validate_header("Chapter 5 Discussion", true, 16.0, 11.0, 18.0);
        assert!(v.is_header);
        assert!(v.reasons.contains(&"formal_prefix"));
    }

    #[test]
    fn test_requirement_id_rejected() {
        let v = validate_header("REQ-AUTH-001: User must authenticate", true, 14.0, 11.0, 18.0);
        assert!(!v.is_header);
        assert!(v.reasons.contains(&"requirement_id_negative"));
    }

    #[test]
    fn test_continued_pattern_rejected() {
        let v = validate_header("(continued)", true, 14.0, 11.0, 18.0);
        assert!(!v.is_header);
    }

    #[test]
    fn test_multi_sentence_rejected() {
        let v =
            validate_header("First sentence. Second sentence. Third one.", true, 14.0, 11.0, 18.0);
        assert!(!v.is_header);
        assert!(v.reasons.contains(&"multi_sentence_negative"));
    }

    // --- Boilerplate Tests ---

    #[test]
    fn test_boilerplate_tlp() {
        assert!(is_boilerplate("TLP: WHITE"));
        assert!(is_boilerplate("TLP:GREEN"));
    }

    #[test]
    fn test_boilerplate_copyright() {
        assert!(is_boilerplate("Copyright © 2024 Acme Corp"));
        assert!(is_boilerplate("CONFIDENTIAL"));
    }

    #[test]
    fn test_boilerplate_arxiv() {
        assert!(is_boilerplate("arXiv:2503.20461"));
    }

    #[test]
    fn test_not_boilerplate() {
        assert!(!is_boilerplate("1.2 Introduction"));
        assert!(!is_boilerplate("The system architecture consists of three main components."));
    }

    // --- Content Exception Tests ---

    #[test]
    fn test_content_exception_numbered() {
        assert!(is_content_exception("1.1 Introduction"));
        assert!(is_content_exception("Section 3 Results"));
        assert!(!is_content_exception("Page 5"));
    }

    // --- Original Tests ---

    #[test]
    fn test_page_number_detection() {
        assert!(is_page_number("42", 0.95));
        assert!(is_page_number("iv", 0.03));
        assert!(is_page_number("Page 5", 0.96));
        assert!(!is_page_number("42", 0.5));
        assert!(!is_page_number("Hello world", 0.95));
    }

    #[test]
    fn test_list_item_detection() {
        assert!(is_list_item("• First item"));
        assert!(is_list_item("1. First item"));
        assert!(is_list_item("a) Sub item"));
        assert!(is_list_item("(3) Third option"));
        assert!(!is_list_item("Regular text here"));
    }

    #[test]
    fn test_caption_detection() {
        assert!(is_caption("Figure 1: System architecture", 0.9));
        assert!(is_caption("Table 3.2 — Results summary", 0.95));
        assert!(!is_caption("The figure shows that...", 1.0));
    }

    #[test]
    fn test_toc_entry() {
        assert!(is_toc_entry("Introduction .................. 1"));
        assert!(is_toc_entry("3.1 Methods . . . . . . . . . 42"));
        assert!(!is_toc_entry("This is a normal sentence."));
    }

    #[test]
    fn test_classify_header() {
        let spans = vec![
            make_span("1. INTRODUCTION", 72.0, 700.0, 16.0, true),
            make_span("This is body text about the topic.", 72.0, 680.0, 11.0, false),
            make_span("More body text continues here.", 72.0, 668.0, 11.0, false),
        ];

        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);

        assert!(blocks.len() >= 2);
        assert_eq!(blocks[0].block_type, BlockType::Title);
        assert!(blocks[0].header_level.is_some());
    }

    #[test]
    fn test_sentence_ending_demotes_to_body() {
        // Bold text that ends with "." — should be demoted to body
        let spans = vec![make_span(
            "The analysis shows significant results.",
            72.0,
            700.0,
            14.0,
            true,
        )];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);
        assert_eq!(blocks[0].block_type, BlockType::Body);
    }

    #[test]
    fn test_numbered_header_not_demoted() {
        // Bold numbered header ending with "." — numbering should override sentence negative
        let spans = vec![make_span("1.2 Introduction", 72.0, 700.0, 14.0, true)];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);
        assert_eq!(blocks[0].block_type, BlockType::Title);
    }

    #[test]
    fn test_count_sentence_breaks() {
        assert_eq!(count_sentence_breaks("Hello. World. Test."), 2);
        assert_eq!(count_sentence_breaks("Hello world"), 0);
        assert_eq!(count_sentence_breaks("Dr. Smith is here."), 1);
    }

    #[test]
    fn test_page_number_formats() {
        assert!(is_page_number("1/10", 0.95));
        assert!(is_page_number("1 / 10", 0.95));
        assert!(is_page_number("[5]", 0.03));
    }

    // --- Disposition Tests ---

    #[test]
    fn test_disposition_accept_numbered() {
        let v = validate_header("1.2 Introduction", true, 14.0, 11.0, 18.0);
        assert_eq!(v.disposition, HeaderDisposition::Accept);
        assert!(v.features.has_number_prefix);
        assert_eq!(v.features.numbering_depth, 2);
    }

    #[test]
    fn test_disposition_accept_formal_prefix() {
        let v = validate_header("Appendix A Security Controls", true, 14.0, 11.0, 18.0);
        assert_eq!(v.disposition, HeaderDisposition::Accept);
        assert!(v.features.has_formal_prefix);
    }

    #[test]
    fn test_disposition_reject_bullet() {
        let v = validate_header("• First item in list", true, 14.0, 11.0, 18.0);
        assert_eq!(v.disposition, HeaderDisposition::Reject);
        assert!(v.features.has_bullet_char);
    }

    #[test]
    fn test_disposition_reject_multi_sentence() {
        let v =
            validate_header("First sentence. Second sentence. Third one.", true, 14.0, 11.0, 18.0);
        assert_eq!(v.disposition, HeaderDisposition::Reject);
        assert!(v.features.is_multi_sentence);
    }

    #[test]
    fn test_disposition_escalate_ambiguous() {
        // Bold text, slightly larger than median, but ends with period — ambiguous
        let v = validate_header("Analysis Results", true, 13.0, 11.0, 18.0);
        // This has bold_large_font (13 >= 11*1.2=13.2 → no) and title_case (0.45)
        // So confidence is 0.45 → Escalate
        assert_eq!(v.disposition, HeaderDisposition::Escalate);
    }

    #[test]
    fn test_disposition_reject_requirement_id() {
        let v = validate_header("REQ-AUTH-001: User must authenticate", true, 14.0, 11.0, 18.0);
        assert_eq!(v.disposition, HeaderDisposition::Reject);
    }

    // --- Features Extraction Tests ---

    #[test]
    fn test_features_extraction() {
        let v = validate_header("1.2.3 System Architecture Overview", true, 14.0, 11.0, 18.0);
        assert!(v.features.has_number_prefix);
        assert_eq!(v.features.numbering_depth, 3);
        assert!(v.features.is_bold);
        assert!(!v.features.ends_with_period);
        assert!(!v.features.ends_with_colon);
        assert!(!v.features.has_bullet_char);
        assert!(!v.features.is_caption_pattern);
        assert!(!v.features.is_multi_sentence);
        assert_eq!(v.features.word_count, 4);
        assert!(v.features.title_case_ratio >= 0.7);
        assert!(!v.features.is_too_long);
        assert!(!v.features.has_parentheses);
    }

    #[test]
    fn test_features_size_ratio() {
        let v = validate_header("METHODS", false, 16.0, 11.0, 18.0);
        assert!((v.features.size_ratio - 16.0 / 11.0).abs() < 0.01);
        assert!(v.features.is_all_caps);
        assert_eq!(v.features.font_size, 16.0);
    }
}
