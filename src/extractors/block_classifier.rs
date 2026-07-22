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
    ChapterLabel,
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
            BlockType::ChapterLabel => "chapter_label",
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

/// One line of text inside a `ClassifiedBlock`.
///
/// Preserves the per-line geometry that `classify_line` computes from
/// spans and that `merge_consecutive_body` would otherwise collapse into a
/// single block-level bbox. Required by `paragraph_bbox_audit` on the
/// Python side (WebGPT 2026-05-12).
#[derive(Debug, Clone)]
pub struct BlockLine {
    /// Line bounding box in page points (xywh, top-left origin).
    pub bbox: Rect,
    /// Joined text content of all spans on this line.
    pub text: String,
    /// Average font size of the spans on this line.
    pub font_size: f32,
    /// Font name of the first span on this line.
    pub font_name: String,
    /// Whether any span on this line is bold.
    pub is_bold: bool,
}

impl BlockLine {
    /// Build one `BlockLine` from the spans that share a line.
    fn from_spans(spans: &[&TextSpan]) -> Self {
        let bbox = spans.iter().fold(spans[0].bbox, |acc, s| acc.union(&s.bbox));
        let text: String = spans
            .iter()
            .map(|s| s.text.as_str())
            .collect::<Vec<_>>()
            .join("");
        let avg_font_size = spans.iter().map(|s| s.font_size).sum::<f32>() / spans.len() as f32;
        let font_name = spans[0].font_name.clone();
        let is_bold = spans
            .iter()
            .any(|s| s.font_weight == crate::layout::text_block::FontWeight::Bold);
        Self {
            bbox,
            text: text.trim().to_string(),
            font_size: avg_font_size,
            font_name,
            is_bold,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ClassifiedBlock {
    /// The classified block type
    pub block_type: BlockType,
    /// Full text content of the block
    pub text: String,
    /// Bounding box in PDF coordinates (page points, xywh, top-left origin)
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
    /// One entry per source line that contributes to this block. For
    /// non-merged blocks this is a single entry. For Body blocks merged by
    /// `merge_consecutive_body`, one entry per original line in source order.
    pub lines: Vec<BlockLine>,
}

pub struct BlockClassifier {
    page_width: f32,
    page_height: f32,
    median_font_size: f32,
    max_font_size: f32,
    header_ratio: f32,
    repeated_margin_origins: Vec<MarginOrigin>,
    underline_rects: Vec<Rect>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum MarginSide {
    Left,
    Right,
}

#[derive(Debug, Clone, Copy)]
struct MarginOrigin {
    side: MarginSide,
    coordinate: f32,
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
        // Estimate the body face by rendered text coverage, not by the number
        // of TextSpan records. A PDF may split small page chrome into hundreds
        // of short spans while storing each full body line in one long span;
        // an unweighted span median then makes body text look artificially
        // large to the heading classifier.
        let mut size_weights: Vec<(f32, usize)> = spans
            .iter()
            .map(|span| {
                let visible_chars = span.text.chars().filter(|c| !c.is_whitespace()).count();
                (span.font_size, visible_chars.max(1))
            })
            .collect();
        size_weights.sort_by(|a, b| {
            a.0.partial_cmp(&b.0)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let auto_median = if size_weights.is_empty() {
            12.0
        } else {
            let total_weight: usize = size_weights.iter().map(|(_, weight)| weight).sum();
            let midpoint = total_weight / 2;
            let mut cumulative = 0;
            size_weights
                .iter()
                .find_map(|(size, weight)| {
                    cumulative += weight;
                    (cumulative > midpoint).then_some(*size)
                })
                .unwrap_or(12.0)
        };
        let median_font_size = body_font_size_override.unwrap_or(auto_median);
        let max_font_size = size_weights.last().map(|(size, _)| *size).unwrap_or(12.0);
        let repeated_margin_origins =
            detect_repeated_margin_origins(spans, page_width, page_height, median_font_size);

        Self {
            page_width,
            page_height,
            median_font_size,
            max_font_size,
            header_ratio: header_ratio_override.unwrap_or(1.2),
            repeated_margin_origins,
            underline_rects: Vec::new(),
        }
    }

    /// Supply painted page rectangles that may represent text underlines.
    ///
    /// Callers may pass all filled or stroked path bounding boxes. Strict
    /// thinness, baseline, and overlap checks are applied before a rectangle
    /// is accepted as an underline.
    pub fn with_underline_rects(mut self, underline_rects: Vec<Rect>) -> Self {
        self.underline_rects = underline_rects;
        self
    }

    /// Classify all spans on a page into typed blocks.
    pub fn classify_spans(&self, spans: &[TextSpan]) -> Vec<ClassifiedBlock> {
        let lines = group_spans_into_lines(spans);

        let mut blocks = Vec::new();
        let mut underlined = Vec::new();
        for line_spans in &lines {
            if line_spans.is_empty() {
                continue;
            }
            let mut block = self.classify_line(line_spans);
            block.lines = vec![BlockLine::from_spans(line_spans)];
            underlined.push(self.has_text_underline(&block.bbox, block.font_size));
            blocks.push(block);
        }

        promote_repeated_reference_entries(&mut blocks, self.page_width, self.page_height);
        merge_reference_continuations(&mut blocks, self.page_width);
        promote_control_catalog_headings(
            &mut blocks,
            &underlined,
            self.page_width,
            self.page_height,
        );
        merge_consecutive_body(&mut blocks);
        promote_isolated_heading_blocks(&mut blocks);
        // WebGPT 2026-05-13 R8 — suppress empty-text classified blocks
        // before they reach the release element list. PDFs occasionally
        // emit zero-width whitespace-only blocks; these should not appear
        // as paragraph content. Non-text structures (tables, figures) are
        // emitted by separate pipelines and are not affected.
        blocks.retain(|b| !b.text.trim().is_empty());
        merge_list_runs_and_continuations(&mut blocks);

        blocks
    }

    fn has_text_underline(&self, text_bbox: &Rect, font_size: f32) -> bool {
        if text_bbox.width <= 0.0 {
            return false;
        }

        self.underline_rects.iter().any(|rule| {
            let rule_thickness = rule.height.max(0.0);
            if rule.width < font_size * 0.5
                || rule.width > self.page_width * 0.70
                || rule_thickness > (font_size * 0.15).max(1.5)
            {
                return false;
            }

            let overlap = (text_bbox.x + text_bbox.width).min(rule.x + rule.width)
                - text_bbox.x.max(rule.x);
            let covers_text = overlap.max(0.0) / text_bbox.width >= 0.60;
            let rule_top = rule.y + rule.height;
            let lies_at_baseline = rule_top <= text_bbox.y + font_size * 0.15
                && rule.y >= text_bbox.y - font_size * 0.35;
            covers_text && lies_at_baseline
        })
    }

    fn is_repeated_margin_chrome(&self, bbox: &Rect, size_ratio: f32) -> bool {
        if size_ratio > 1.30 {
            return false;
        }

        let tolerance = self.page_width * 0.01;
        self.repeated_margin_origins.iter().any(|origin| {
            let coordinate = match origin.side {
                MarginSide::Left => bbox.x,
                MarginSide::Right => bbox.x + bbox.width,
            };
            (coordinate - origin.coordinate).abs() <= tolerance
        })
    }

    fn classify_line(&self, spans: &[&TextSpan]) -> ClassifiedBlock {
        // WebGPT 2026-05-13 R6 — sort spans by (bbox.x, sequence) before
        // joining their text. PDFs can emit "Modern" via TJ kerning shifts
        // that put glyph fragments out of left-to-right content-stream
        // order (e.g. "der" drawn before "Mo" then "n"). When `classify_blocks`
        // feeds spans from `extract_spans_unsorted`, the iteration order
        // matches content-stream order rather than reading order, producing
        // artifacts like "derMon". Sorting here normalizes the line text
        // without altering page-level block order or per-span bbox/font
        // calculations below (those still use the original `spans` slice).
        let mut sorted_for_text: Vec<&TextSpan> = spans.to_vec();
        sorted_for_text.sort_by(|a, b| {
            a.bbox
                .x
                .partial_cmp(&b.bbox.x)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then(a.sequence.cmp(&b.sequence))
        });
        let text: String = sorted_for_text
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

        // bbox.y is the BOTTOM edge in a bottom-origin page space, but every
        // threshold below is written in top-origin terms ("top 8%", "bottom 8%",
        // "bottom of page"). Normalise to the distance-from-top of the block's
        // top edge so the rules mean what their comments say.
        let y_ratio = (self.page_height - bbox.y - bbox.height) / self.page_height;
        let x_center = bbox.x + bbox.width / 2.0;
        let page_center = self.page_width / 2.0;
        let is_centered = (x_center - page_center).abs() < self.page_width * 0.1;
        let size_ratio = avg_font_size / self.median_font_size;
        let trimmed = text.trim();
        // Running furniture is commonly a wide rule/title pair fully inside
        // the page's top or bottom band. Permit modest body-face estimation
        // drift on sparse/table pages, while retaining the strong geometry.
        let is_wide_margin_chrome = bbox.width >= self.page_width * 0.60 && size_ratio <= 1.20;

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
            // But check if it's actually a section header that happens to be at the bottom.
            // A running footer may restate the chapter/section name beside a page
            // marker ("<Chapter name>   PAGE 12"); structural page chrome wins.
            if !is_content_exception(trimmed) || has_page_marker(trimmed) || is_wide_margin_chrome {
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
        if y_ratio < 0.08
            && trimmed.len() < 200
            && (avg_font_size <= self.median_font_size || is_wide_margin_chrome)
        {
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
        // Assembled quarter-turn text has a tall axis-aligned page box. Older
        // producers may instead emit several short edge-aligned spans, so keep
        // the repeated-origin and below-body-size fallbacks. Positional and
        // typographic only.
        let is_at_page_edge =
            bbox.x < self.page_width * 0.06 || bbox.x + bbox.width > self.page_width * 0.94;
        let is_tall_rotated_span = bbox.height > bbox.width * 3.0;
        if is_at_page_edge
            && (is_tall_rotated_span
                || size_ratio < 0.95
                || self.is_repeated_margin_chrome(&bbox, size_ratio))
        {
            return self.make_block(
                BlockType::Boilerplate,
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

        // List labels are structural, but a marker alone is ambiguous with a
        // section number. Accept only body-ish lines inside the content area.
        // The small allowance above the body face covers mixed-font pages where
        // the weighted median sits just below the list face; bold text receives
        // no such allowance because enlarged bold labels are heading-shaped.
        // `is_list_item` rejects multi-level decimal section numbers such as
        // "2.2", while retaining alpha-numeric list labels such as "h.3".
        let is_bodyish_list = size_ratio <= 1.20
            && bbox.x >= self.page_width * 0.08
            && bbox.x + bbox.width <= self.page_width * 0.92
            && (!is_bold || size_ratio <= 1.15);
        if is_list_item(trimmed) && is_bodyish_list {
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

        // Rotated margin chrome (DOI watermark, spine text): a span far taller
        // than it is wide, hugging a page edge, is furniture rather than content.
        // Purely geometric -- no text, no page index.
        // Chapter/part/appendix label: a short standalone divider line naming a
        // structural unit, set above the section title it introduces.
        if y_ratio < 0.4 && trimmed.len() < 60 && is_structural_label(trimmed) {
            return self.make_block(
                BlockType::ChapterLabel,
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

        // Callout-box title: some documents set a short title at body size,
        // relying on bold, all-caps, and centered placement rather than font
        // enlargement. Require a geometrically short line as well as centered
        // placement so a full-width prose fragment cannot qualify merely
        // because its center happens to coincide with the page center.
        let char_count = trimmed.chars().count();
        if is_bold
            && is_all_caps_text(trimmed)
            && (5..=60).contains(&char_count)
            && is_centered
            && bbox.width <= self.page_width * 0.6
        {
            return self.make_block(
                BlockType::Title,
                text,
                bbox,
                avg_font_size,
                font_name,
                is_bold,
                0.85,
                Some(2),
                None,
            );
        }

        // Section heading: short, bold, set noticeably above body size. Left-aligned
        // headings are the norm in technical documents, so centring must not be
        // required. Typographic only.
        if is_bold && size_ratio > 1.25 && trimmed.len() < 200 {
            return self.make_block(
                BlockType::Title,
                text,
                bbox,
                avg_font_size,
                font_name,
                is_bold,
                0.85,
                Some(1),
                None,
            );
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
            lines: Vec::new(),
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

fn detect_repeated_margin_origins(
    spans: &[TextSpan],
    page_width: f32,
    page_height: f32,
    median_font_size: f32,
) -> Vec<MarginOrigin> {
    let mut candidates: Vec<(MarginSide, f32, f32)> = Vec::new();
    for span in spans {
        if span.text.trim().is_empty() || span.font_size > median_font_size * 1.30 {
            continue;
        }
        if span.bbox.x < page_width * 0.06 {
            candidates.push((MarginSide::Left, span.bbox.x, span.bbox.y));
        }
        let right = span.bbox.x + span.bbox.width;
        if right > page_width * 0.94 {
            candidates.push((MarginSide::Right, right, span.bbox.y));
        }
    }

    let x_tolerance = page_width * 0.01;
    let y_tolerance = median_font_size * 0.60;
    let mut origins = Vec::new();
    for &(side, coordinate, _) in &candidates {
        let mut aligned_y: Vec<f32> = candidates
            .iter()
            .filter(|(other_side, other_coordinate, _)| {
                *other_side == side && (*other_coordinate - coordinate).abs() <= x_tolerance
            })
            .map(|(_, _, y)| *y)
            .collect();
        aligned_y.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        aligned_y.dedup_by(|a, b| (*a - *b).abs() <= y_tolerance);

        let spans_page_height = aligned_y
            .first()
            .zip(aligned_y.last())
            .map(|(first, last)| last - first)
            .unwrap_or(0.0);
        if aligned_y.len() >= 3
            && spans_page_height >= page_height * 0.25
            && !origins.iter().any(|existing: &MarginOrigin| {
                existing.side == side && (existing.coordinate - coordinate).abs() <= x_tolerance
            })
        {
            origins.push(MarginOrigin { side, coordinate });
        }
    }
    origins
}

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

/// Promote citation-shaped body lines only when page geometry shows that they
/// belong to a repeated hanging-indent stream.
///
/// The two signals are deliberately conjunctive:
///
/// 1. the line begins with a compact bracketed citation token; and
/// 2. another candidate in the page's content band has the same left anchor
///    and a compatible font size; and
/// 3. a wide candidate does not continue as flush-left running prose.
///
/// The shared anchor defines a page-local column/region. This covers both
/// bibliography entries and glossary source tags without relying on author,
/// venue, section-title, or page-number vocabulary. A bracketed line isolated
/// from every such stream, or embedded in flush-left prose, is left as Body.
fn promote_repeated_reference_entries(
    blocks: &mut [ClassifiedBlock],
    page_width: f32,
    page_height: f32,
) {
    if page_width <= 0.0 || page_height <= 0.0 {
        return;
    }

    let candidates: Vec<usize> = blocks
        .iter()
        .enumerate()
        .filter_map(|(index, block)| {
            if block.block_type != BlockType::Body || !has_leading_citation_token(&block.text) {
                return None;
            }

            let top_ratio = 1.0 - (block.bbox.y + block.bbox.height) / page_height;
            let inside_content_band = (0.08..=0.92).contains(&top_ratio);
            let inside_content_width = block.bbox.x >= page_width * 0.06
                && block.bbox.x <= page_width * 0.94;
            (inside_content_band && inside_content_width).then_some(index)
        })
        .collect();

    let x_tolerance = (page_width * 0.01).max(2.0);
    let mut promote = Vec::new();
    for &index in &candidates {
        let anchor = &blocks[index];
        let has_regional_repetition = candidates.iter().copied().any(|other_index| {
            if other_index == index {
                return false;
            }
            let other = &blocks[other_index];
            let font_ratio = other.font_size / anchor.font_size.max(1.0);
            (other.bbox.x - anchor.bbox.x).abs() <= x_tolerance
                && (0.85..=1.18).contains(&font_ratio)
        });
        let continues_as_running_prose = anchor.bbox.width >= page_width * 0.40
            && blocks.get(index + 1).is_some_and(|next| {
                let font_ratio = next.font_size / anchor.font_size.max(1.0);
                let vertical_gap = anchor.bbox.y - (next.bbox.y + next.bbox.height);
                !has_leading_citation_token(&next.text)
                    && (next.bbox.x - anchor.bbox.x).abs() <= x_tolerance
                    && (0.85..=1.18).contains(&font_ratio)
                    && next.bbox.y <= anchor.bbox.y
                    && vertical_gap >= -anchor.font_size * 0.5
                    && vertical_gap <= anchor.font_size * 1.5
            });
        if has_regional_repetition && !continues_as_running_prose {
            promote.push(index);
        }
    }

    for index in promote {
        blocks[index].block_type = BlockType::Reference;
        blocks[index].confidence = 0.8;
    }
}

/// Merge tightly led hanging-indent lines into their reference anchor.
///
/// This is a core layout operation rather than a document preset: continuation
/// membership is decided only by font metrics, line geometry, and column
/// membership. The content classifier may initially label a continuation as
/// Body, List, or Boilerplate; those labels do not override the source layout.
fn merge_reference_continuations(blocks: &mut Vec<ClassifiedBlock>, page_width: f32) {
    use BlockType::{Body, Boilerplate, List, Reference};

    let mut index = 0;
    while index < blocks.len() {
        if blocks[index].block_type != Reference {
            index += 1;
            continue;
        }

        loop {
            let Some(next) = blocks.get(index + 1) else {
                break;
            };
            if !matches!(next.block_type, Body | List | Boilerplate) {
                break;
            }

            let previous_line = blocks[index].lines.last().cloned().unwrap_or(BlockLine {
                bbox: blocks[index].bbox,
                text: blocks[index].text.clone(),
                font_size: blocks[index].font_size,
                font_name: blocks[index].font_name.clone(),
                is_bold: blocks[index].is_bold,
            });
            if !is_hanging_indent_continuation(&blocks[index], &previous_line, next, page_width) {
                break;
            }

            let absorbed = blocks.remove(index + 1);
            blocks[index].text.push(' ');
            blocks[index].text.push_str(&absorbed.text);
            blocks[index].bbox = blocks[index].bbox.union(&absorbed.bbox);
            blocks[index].lines.extend(absorbed.lines);
        }
        index += 1;
    }
}

fn is_hanging_indent_continuation(
    anchor: &ClassifiedBlock,
    previous_line: &BlockLine,
    next: &ClassifiedBlock,
    page_width: f32,
) -> bool {
    if page_width <= 0.0 || anchor.font_size <= 0.0 || next.font_size <= 0.0 {
        return false;
    }

    let anchor_font_ratio = next.font_size / anchor.font_size;
    let leading_font_ratio = next.font_size / previous_line.font_size.max(1.0);
    if !(0.85..=1.18).contains(&anchor_font_ratio)
        || (anchor.lines.len() > 1 && !(0.97..=1.03).contains(&leading_font_ratio))
    {
        return false;
    }

    // Bibliographic titles and venues may switch between roman and italic
    // faces while preserving the same metrics. Exact font identity is ideal;
    // equal size and emphasis admit that face-only variation.
    let compatible_face = next.font_name == previous_line.font_name
        || ((leading_font_ratio - 1.0).abs() <= 0.03 && next.is_bold == previous_line.is_bold);
    if !compatible_face {
        return false;
    }

    let indent = next.bbox.x - anchor.bbox.x;
    if indent <= 0.5 || indent > anchor.font_size * 4.0 {
        return false;
    }

    let page_midpoint = page_width / 2.0;
    let crosses_column_boundary = (anchor.bbox.x < page_midpoint && next.bbox.x >= page_midpoint)
        || (anchor.bbox.x >= page_midpoint && next.bbox.x < page_midpoint);
    if crosses_column_boundary || next.bbox.y > previous_line.bbox.y {
        return false;
    }

    let vertical_gap = previous_line.bbox.y - (next.bbox.y + next.bbox.height);
    vertical_gap >= -anchor.font_size * 0.5 && vertical_gap <= anchor.font_size * 1.5
}

/// A compact citation token at the beginning of a line. Token recognition is
/// intentionally shape-only; regional geometry decides whether it is a
/// reference entry rather than an isolated inline citation.
fn has_leading_citation_token(text: &str) -> bool {
    let trimmed = text.trim_start();
    let Some(close) = trimmed.find(']') else {
        return false;
    };
    if !trimmed.starts_with('[') || !(2..=24).contains(&close) {
        return false;
    }

    let token = &trimmed[1..close];
    token.chars().any(char::is_alphanumeric)
        && !token.contains('[')
        && !token.chars().any(char::is_control)
}

/// Heuristic: does this block's text look like a section heading?
///
/// True for short, mostly-uppercase, alphabetic-dominant text. Used as a
/// guard in `merge_consecutive_body` so a standalone bold all-caps heading
/// like "INTRODUCTION" does not get absorbed into the surrounding Body
/// paragraph just because vertical spacing is small (WebGPT 2026-05-13 R4).
fn looks_like_heading(b: &ClassifiedBlock) -> bool {
    let text = b.text.trim();
    let len = text.chars().count();
    if !(3..=60).contains(&len) {
        return false;
    }
    let alpha: usize = text.chars().filter(|c| c.is_alphabetic()).count();
    if alpha == 0 {
        return false;
    }
    let upper: usize = text
        .chars()
        .filter(|c| c.is_uppercase() || (c.is_alphabetic() && !c.is_lowercase()))
        .count();
    let upper_ratio = upper as f32 / alpha as f32;
    if upper_ratio < 0.80 {
        return false;
    }
    // A standalone heading should be one or two words OR end with no body
    // sentence punctuation.
    let trailing = text.chars().last();
    if matches!(trailing, Some('.') | Some(',') | Some(';') | Some(':')) {
        // Sentence-final punctuation suggests body prose, not a heading.
        return false;
    }
    true
}

fn merge_consecutive_body(blocks: &mut Vec<ClassifiedBlock>) {
    let mut i = 0;
    while i + 1 < blocks.len() {
        // R4 heading-protection guard (WebGPT 2026-05-13): never absorb a
        // heading-shaped block (the current one OR the next one) into a Body
        // paragraph. Prevents the NIST 800-53r5 page-28 INTRODUCTION + subtitle
        // + first body paragraph collapse.
        if blocks[i].block_type == BlockType::Body
            && blocks[i + 1].block_type == BlockType::Body
            && (blocks[i].bbox.y - blocks[i + 1].bbox.y).abs() < blocks[i].font_size * 1.5
            && !looks_like_heading(&blocks[i])
            && !looks_like_heading(&blocks[i + 1])
        {
            let next = blocks.remove(i + 1);
            blocks[i].text.push(' ');
            blocks[i].text.push_str(&next.text);
            blocks[i].bbox = blocks[i].bbox.union(&next.bbox);
            // WebGPT 2026-05-12: preserve per-line evidence through merges so
            // paragraph_bbox_audit can walk lines[].bbox after this collapse.
            blocks[i].lines.extend(next.lines);
        } else {
            i += 1;
        }
    }
}

/// Promote the recurring control-catalog heading pair before body lines merge:
/// a compact control identifier plus title, followed by a styled colon label.
///
/// The rule is deliberately structural. It requires page chrome earlier in
/// source order, a short heading in the page body, a generic identifier shape,
/// and tight vertical geometry. The optional lead-in must be bold or
/// visibly underlined and sit immediately below the promoted heading.
fn promote_control_catalog_headings(
    blocks: &mut [ClassifiedBlock],
    underlined: &[bool],
    page_width: f32,
    page_height: f32,
) {
    use BlockType::{Body, Boilerplate, Caption, Footer, Header, PageNumber, Subtitle, Title};

    if blocks.len() != underlined.len() || page_width <= 0.0 || page_height <= 0.0 {
        return;
    }

    let mut seen_chrome = false;
    let mut heading_indices = Vec::new();

    for (index, block) in blocks.iter().enumerate() {
        if matches!(block.block_type, Header | Footer | PageNumber | Boilerplate) {
            seen_chrome = true;
            continue;
        }

        // Hard negative guard: a block already classified as Caption is never
        // eligible for this promotion, regardless of its text or geometry.
        if block.block_type == Caption {
            continue;
        }
        if block.block_type != Body {
            continue;
        }

        // Hard negative guard: URLs and link-like lead-ins never become
        // headings, even when another typographic signal happens to match.
        if starts_with_url_scheme_or_link_phrase(&block.text) {
            continue;
        }

        let top_ratio = 1.0 - (block.bbox.y + block.bbox.height) / page_height;
        if seen_chrome
            && (0.08..=0.92).contains(&top_ratio)
            && block.text.trim().chars().count() <= 120
            && block.bbox.width <= page_width * 0.75
            && has_compact_control_id_prefix(&block.text)
        {
            heading_indices.push(index);
        }
    }

    for &index in &heading_indices {
        blocks[index].block_type = Title;
        blocks[index].header_level = Some(2);

        let label_index = index + 1;
        if label_index >= blocks.len() {
            continue;
        }

        let label = &blocks[label_index];
        // Repeat both hard guards for the paired lead-in; the heading match
        // must never authorize retyping protected or link-shaped content.
        if label.block_type == Caption
            || label.block_type != Body
            || starts_with_url_scheme_or_link_phrase(&label.text)
            || !(label.is_bold || underlined[label_index])
            || !is_compact_colon_label(&label.text)
        {
            continue;
        }

        let heading = &blocks[index];
        let vertical_gap = heading.bbox.y - (label.bbox.y + label.bbox.height);
        let font_ratio = label.font_size / heading.font_size.max(1.0);
        let indent = label.bbox.x - heading.bbox.x;
        if (-heading.font_size..=heading.font_size * 1.75).contains(&vertical_gap)
            && (0.72..=1.15).contains(&font_ratio)
            && (-heading.font_size * 0.5..=heading.font_size * 4.0).contains(&indent)
            && label.bbox.width <= page_width * 0.35
        {
            blocks[label_index].block_type = Subtitle;
            blocks[label_index].header_level = Some(3);
        }
    }
}

/// Generic compact control identifier: 2-5 uppercase ASCII letters, a dash,
/// digits, and optional parenthesized numeric suffixes, followed by a title.
fn has_compact_control_id_prefix(text: &str) -> bool {
    let trimmed = text.trim();
    let Some((token, title)) = trimmed.split_once(char::is_whitespace) else {
        return false;
    };
    if !title.chars().any(char::is_alphabetic) {
        return false;
    }

    let Some((family, number)) = token.split_once('-') else {
        return false;
    };
    if !(2..=5).contains(&family.len())
        || !family.bytes().all(|byte| byte.is_ascii_uppercase())
    {
        return false;
    }

    let digit_count = number.bytes().take_while(u8::is_ascii_digit).count();
    if digit_count == 0 {
        return false;
    }

    let mut suffix = &number[digit_count..];
    while !suffix.is_empty() {
        let Some(rest) = suffix.strip_prefix('(') else {
            return false;
        };
        let Some(close) = rest.find(')') else {
            return false;
        };
        let inner = &rest[..close];
        if inner.is_empty() || !inner.bytes().all(|byte| byte.is_ascii_digit()) {
            return false;
        }
        suffix = &rest[close + 1..];
    }
    true
}

/// A short standalone colon label such as a one- or two-word lead-in.
fn is_compact_colon_label(text: &str) -> bool {
    let trimmed = text.trim();
    let Some(stem) = trimmed.strip_suffix(':') else {
        return false;
    };
    let words: Vec<&str> = stem.split_whitespace().collect();
    !stem.is_empty()
        && stem.chars().count() <= 40
        && (1..=3).contains(&words.len())
        && words.iter().all(|word| {
            word.chars()
                .all(|character| character.is_alphabetic() || character == '-')
        })
}

/// True for a leading RFC-style URL scheme or generic link-like wording.
fn starts_with_url_scheme_or_link_phrase(text: &str) -> bool {
    let trimmed = text.trim_start();
    let first_token = trimmed.split_whitespace().next().unwrap_or_default();
    if first_token.to_ascii_lowercase().starts_with("www.") {
        return true;
    }
    if let Some((scheme, remainder)) = first_token.split_once(':') {
        if !scheme.is_empty()
            && (remainder.starts_with("//")
                || matches!(
                    scheme.to_ascii_lowercase().as_str(),
                    "mailto" | "tel" | "urn" | "doi" | "data" | "file"
                ))
            && scheme
                .bytes()
                .enumerate()
                .all(|(index, byte)| {
                    if index == 0 {
                        byte.is_ascii_alphabetic()
                    } else {
                        byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'-' | b'.')
                    }
                })
        {
            return true;
        }
    }

    let words: Vec<String> = trimmed
        .split_whitespace()
        .take(3)
        .map(|word| {
            word.trim_matches(|character: char| !character.is_alphanumeric())
                .to_ascii_lowercase()
        })
        .collect();
    matches!(words.first().map(String::as_str), Some("link" | "hyperlink" | "url"))
        || matches!(
            (words.first().map(String::as_str), words.get(1).map(String::as_str)),
            (Some("quick" | "direct"), Some("link")) | (Some("click"), Some("here"))
        )
}

/// Subtitle-shaped neighbor check for `promote_isolated_heading_blocks`.
///
/// True for uppercase-dominant, alphabetic-dominant blocks without sentence-
/// final punctuation, up to 200 characters. Loosened from
/// `looks_like_heading` (which caps at 60 chars) so a multi-line all-caps
/// subtitle counts as a heading-shaped neighbor for context detection only.
fn heading_shaped_subtitle(b: &ClassifiedBlock) -> bool {
    let text = b.text.trim();
    let len = text.chars().count();
    if !(3..=200).contains(&len) {
        return false;
    }
    let alpha: usize = text.chars().filter(|c| c.is_alphabetic()).count();
    if alpha == 0 {
        return false;
    }
    let upper: usize = text
        .chars()
        .filter(|c| c.is_uppercase() || (c.is_alphabetic() && !c.is_lowercase()))
        .count();
    if (upper as f32 / alpha as f32) < 0.80 {
        return false;
    }
    let trailing = text.chars().last();
    if matches!(trailing, Some('.') | Some(',') | Some(';') | Some(':')) {
        return false;
    }
    true
}

/// Narrow post-merge promotion: after `merge_consecutive_body` has separated
/// heading-shaped blocks (R4 guard), promote isolated short uppercase Body
/// blocks to Title when neighbor context confirms a heading/body boundary
/// (WebGPT 2026-05-13 R5).
///
/// Conservative criteria — all must hold:
///   1. Block is currently `BlockType::Body`
///   2. Block passes the strict `looks_like_heading` predicate (short, mostly
///      uppercase, alphabetic-dominant, no terminal sentence punctuation)
///   3. Previous block is `Title` OR a heading-shaped Body (e.g. CHAPTER ONE
///      followed by INTRODUCTION) — or the block is the first on the page
///   4. Next block is Body / Title / Subtitle (the heading sits at the start
///      of a section) — or the block is the last on the page AND prev is
///      Title
///
/// This deliberately does NOT touch `classify_line`; the broader corpus-wide
/// behavior of single-line bold-uppercase detection is unchanged. Only blocks
/// that survived past `merge_consecutive_body` while still typed Body — i.e.
/// those the R4 heading-protection guard already refused to merge — are
/// promoted, and only when their neighbors confirm the boundary.
fn promote_isolated_heading_blocks(blocks: &mut Vec<ClassifiedBlock>) {
    use BlockType::{Body, Subtitle, Title};

    let n = blocks.len();
    if n == 0 {
        return;
    }

    let mut to_promote: Vec<usize> = Vec::new();
    for i in 0..n {
        let b = &blocks[i];
        if b.block_type != Body {
            continue;
        }
        // A citation-shaped line that lacked regional repetition must remain
        // excluded; its uppercase token is not heading evidence.
        if has_leading_citation_token(&b.text) {
            continue;
        }
        if !looks_like_heading(b) {
            continue;
        }

        let prev_ok = if i == 0 {
            // No predecessor (or only chrome filtered out earlier); accept
            // if there's a clear successor context.
            true
        } else {
            let p = &blocks[i - 1];
            p.block_type == Title
                || (p.block_type == Body && heading_shaped_subtitle(p))
        };

        let next_ok = if i + 1 >= n {
            i > 0 && blocks[i - 1].block_type == Title
        } else {
            let q = &blocks[i + 1];
            matches!(q.block_type, Body | Title | Subtitle)
        };

        if prev_ok && next_ok {
            to_promote.push(i);
        }
    }

    for i in to_promote {
        let inherited_level = if i > 0 && blocks[i - 1].block_type == Title {
            blocks[i - 1].header_level.map(|lvl| lvl.saturating_add(1))
        } else {
            None
        };
        blocks[i].block_type = Title;
        blocks[i].header_level = Some(inherited_level.unwrap_or(2));
    }
}

/// Narrow post-classification pass: merge bullet-list runs and absorb
/// indented body continuation lines into their preceding list anchor
/// (WebGPT 2026-05-13 R8).
///
/// Runs after `merge_consecutive_body`, `promote_isolated_heading_blocks`
/// and the empty-block filter. Conservative criteria — every neighbor
/// absorbed into a list anchor must satisfy ALL of:
///
///   - y_gap between anchor's bottom and neighbor's top is at most one
///     line-height of slack (i.e. they are visually contiguous);
///   - font size is within ±15% of the anchor (rules out footnotes which
///     are emitted at ~9pt vs body's ~11pt);
///   - either:
///       * neighbor is `Body` AND its x is indented past the anchor's x
///         (bullet text continuation, e.g. x=108 under anchor at x=90), OR
///       * neighbor is `List` AND its x matches the anchor's x within 2pt
///         (sibling bullet in the same run);
///   - neighbor is not a footnote/footer/header/page-number block.
///
/// The merge preserves `lines[]` so paragraph_bbox_audit consumers see
/// per-bullet-item evidence. The anchor's `block_type` stays `List`.
fn merge_list_runs_and_continuations(blocks: &mut Vec<ClassifiedBlock>) {
    use BlockType::{Body, List};

    let mut i = 0;
    while i + 1 < blocks.len() {
        if blocks[i].block_type != List {
            i += 1;
            continue;
        }
        let anchor_x = blocks[i].bbox.x;
        let anchor_fs = blocks[i].font_size.max(1.0);

        loop {
            if i + 1 >= blocks.len() {
                break;
            }
            // Inspect the next block. Only Body or List are candidates;
            // anything else (Footer/Header/Footnote/PageNumber/Caption/
            // Reference/Equation/Boilerplate/Title/Subtitle/TOC) breaks
            // the run.
            if !matches!(blocks[i + 1].block_type, Body | List) {
                break;
            }

            let next = &blocks[i + 1];
            // pdf_oxide bboxes use PDF bottom-left origin: bbox.y is the
            // BOTTOM y, and bbox.y + height is the TOP y. A block that
            // appears visually below another has a SMALLER bbox.y.
            //
            // Visual whitespace between anchor (above) and next (below) is
            // (anchor.bottom_y) - (next.top_y) =
            // anchor.bbox.y - (next.bbox.y + next.bbox.height)
            //
            // Positive = whitespace between them. Negative = vertical
            // overlap (can happen after bbox.union of earlier merges).
            let anchor_bottom_y = blocks[i].bbox.y;
            let next_top_y = next.bbox.y + next.bbox.height;
            let y_gap = anchor_bottom_y - next_top_y;
            // next must be visually below anchor (i.e. not above it on
            // the page) — bottom_y(anchor) >= top_y(next)+epsilon would
            // place next above anchor.
            if next.bbox.y > blocks[i].bbox.y {
                break;
            }
            if y_gap > anchor_fs * 1.5 {
                break;
            }
            if y_gap < -anchor_fs * 2.0 {
                break;
            }

            let fs_ratio = next.font_size / anchor_fs;
            if !(0.85..=1.18).contains(&fs_ratio) {
                break;
            }

            // Indentation classification.
            let dx = next.bbox.x - anchor_x;
            let is_continuation =
                next.block_type == Body && dx > 0.5 && dx <= anchor_fs * 4.0;
            let is_sibling_list = next.block_type == List && dx.abs() <= 2.0;
            if !is_continuation && !is_sibling_list {
                break;
            }

            // Absorb next into anchor.
            let absorbed = blocks.remove(i + 1);
            blocks[i].text.push(' ');
            blocks[i].text.push_str(&absorbed.text);
            blocks[i].bbox = blocks[i].bbox.union(&absorbed.bbox);
            blocks[i].lines.extend(absorbed.lines);
            // anchor stays typed List; iterate to look for further merges.
        }
        i += 1;
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

/// True when the text carries a running page marker, e.g. "PAGE 12", "p. 12",
/// or a bare trailing page number.
///
/// Positional/typographic only: no document-specific phrase, no page index,
/// no control identifier.
fn has_page_marker(text: &str) -> bool {
    let trimmed = text.trim();
    let lower = trimmed.to_lowercase();
    let mut words = lower.split_whitespace().peekable();
    while let Some(word) = words.next() {
        let word = word.trim_matches(|c: char| !c.is_alphanumeric());
        if matches!(word, "page" | "pg" | "p") {
            if let Some(next) = words.peek() {
                if next
                    .trim_matches(|c: char| !c.is_ascii_digit())
                    .parse::<u32>()
                    .is_ok()
                {
                    return true;
                }
            }
        }
    }
    trimmed
        .split_whitespace()
        .last()
        .map(|last: &str| {
            last.trim_matches(|c: char| !c.is_ascii_digit())
                .parse::<u32>()
                .is_ok()
        })
        .unwrap_or(false)
}

/// True for a short standalone structural divider such as "CHAPTER ONE",
/// "PART II", "APPENDIX C". Structural vocabulary only -- no document-specific
/// phrase, no page index, no identifier from the source corpus.
fn is_structural_label(text: &str) -> bool {
    let trimmed = text.trim();
    let lower = trimmed.to_lowercase();
    let mut words = lower.split_whitespace();
    let head = match words.next() {
        Some(w) => w.trim_matches(|c: char| !c.is_alphanumeric()).to_string(),
        None => return false,
    };
    if !matches!(head.as_str(), "chapter" | "part" | "appendix" | "section" | "annex") {
        return false;
    }
    // exactly one trailing token: a numeral, roman numeral, letter, or number word
    let rest: Vec<&str> = words.collect();
    if rest.len() != 1 {
        return false;
    }
    let tail = rest[0].trim_matches(|c: char| !c.is_alphanumeric());
    !tail.is_empty() && tail.len() <= 8
}

/// True when the alphabetic content of the text is entirely upper case.
fn is_all_caps_text(text: &str) -> bool {
    let letters: Vec<char> = text.chars().filter(|c| c.is_alphabetic()).collect();
    if letters.len() < 8 {
        return false;
    }
    letters.iter().all(|c| c.is_uppercase())
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
    // A marker must be the complete first token. This keeps decimal section
    // numbers ("2.2") out while admitting list nesting labels ("h.3").
    let marker = match trimmed.split_whitespace().next() {
        Some(marker) => marker,
        None => return false,
    };

    if marker.starts_with('(') && marker.ends_with(')') {
        let inner = &marker[1..marker.len() - 1];
        return inner.parse::<u32>().is_ok()
            || (inner.len() == 1 && inner.as_bytes()[0].is_ascii_alphabetic());
    }

    let has_closing_delimiter = marker.ends_with(')') || marker.ends_with('.');
    let marker = marker
        .strip_suffix(')')
        .or_else(|| marker.strip_suffix('.'))
        .unwrap_or(marker);
    let parts: Vec<&str> = marker.split('.').collect();
    match parts.as_slice() {
        [single] => {
            has_closing_delimiter
                && (single.parse::<u32>().is_ok()
                    || (single.len() == 1 && single.as_bytes()[0].is_ascii_alphabetic()))
        },
        [letter, number] => {
            letter.len() == 1
                && letter.as_bytes()[0].is_ascii_alphabetic()
                && !number.is_empty()
                && number.bytes().all(|b| b.is_ascii_digit())
        },
        _ => false,
    }
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
        assert!(is_list_item("a. First lettered item"));
        assert!(is_list_item("i. Roman-shaped lettered item"));
        assert!(is_list_item("l. Later lettered item"));
        assert!(is_list_item("h.3 Nested alpha-numeric item"));
        assert!(is_list_item("i.1 Nested roman-shaped alpha-numeric item"));
        assert!(is_list_item("a) Sub item"));
        assert!(is_list_item("(3) Third option"));
        assert!(!is_list_item("2.2 TITLE CASE HEADING"));
        assert!(!is_list_item("2.2.1 DEEPER HEADING"));
        assert!(!is_list_item("A normal sentence starts with an article."));
        assert!(!is_list_item("I think this is ordinary prose."));
        assert!(!is_list_item("1 bare number without a list delimiter"));
        assert!(!is_list_item("Regular text here"));
    }

    #[test]
    fn test_bodyish_lettered_and_numbered_items_do_not_capture_headings_or_prose() {
        let mut spans = vec![
            make_span("a. First list entry", 126.0, 700.0, 11.6, false),
            make_span("i. Ninth list entry", 126.0, 675.0, 11.6, false),
            make_span("l. Twelfth list entry", 126.0, 650.0, 11.6, false),
            make_span("h.3 Nested list entry", 126.0, 625.0, 11.6, false),
            make_span("i.1 Another nested list entry", 126.0, 600.0, 11.6, false),
            make_span("1. Numeric list entry", 144.0, 575.0, 11.6, false),
            make_span("2.2 TITLE CASE HEADING", 90.0, 550.0, 13.0, true),
            make_span(
                "Ordinary prose remains body text even at the slightly elevated list face.",
                90.0,
                500.0,
                11.6,
                false,
            ),
        ];
        // Preserve coverage for the previous bold, body-sized list behavior.
        spans.push(make_span("b. Bold list entry", 126.0, 475.0, 11.4, true));

        let expected_chars: String = spans.iter().map(|span| span.text.as_str()).collect();
        let classifier =
            BlockClassifier::new_with_overrides(612.0, 792.0, &spans, Some(10.0), None);
        let blocks = classifier.classify_spans(&spans);

        for marker in ["a.", "i.", "l.", "h.3", "i.1", "1.", "b."] {
            let block = blocks
                .iter()
                .find(|block| block.text.contains(marker))
                .unwrap_or_else(|| panic!("missing retained list item {marker}"));
            assert_eq!(block.block_type, BlockType::List, "{marker} must be a list");
        }

        let heading = blocks
            .iter()
            .find(|block| block.text.contains("2.2 TITLE CASE HEADING"))
            .expect("numbered heading must be retained");
        assert_eq!(heading.block_type, BlockType::Title);

        let prose = blocks
            .iter()
            .find(|block| block.text.contains("Ordinary prose remains body text"))
            .expect("ordinary prose must be retained");
        assert_eq!(prose.block_type, BlockType::Body);

        let actual_chars: String = blocks.iter().map(|block| block.text.as_str()).collect();
        let actual_chars: String = actual_chars
            .chars()
            .filter(|c| !c.is_whitespace())
            .collect();
        let expected_chars: String = expected_chars
            .chars()
            .filter(|c| !c.is_whitespace())
            .collect();
        assert_eq!(actual_chars, expected_chars, "classification must retain every character");
    }

    #[test]
    fn test_caption_detection() {
        assert!(is_caption("Figure 1: System architecture", 0.9));
        assert!(is_caption("Table 3.2 — Results summary", 0.95));
        assert!(!is_caption("The figure shows that...", 1.0));
    }

    #[test]
    fn test_repeated_reference_stream_covers_hanging_and_same_line_entries() {
        let spans = vec![
            make_span(
                "[ZX 100] First standard entry begins here",
                95.0,
                700.0,
                10.5,
                false,
            ),
            make_span(
                "hanging-indent continuation for the first entry.",
                113.0,
                685.0,
                10.8,
                false,
            ),
            make_span(
                "[QY 200] Cooper DA, Example H, Sample R (2020) Long-form entry",
                95.0,
                650.0,
                10.4,
                false,
            ),
            make_span(
                "hanging-indent continuation for the long-form entry.",
                113.0,
                635.0,
                10.8,
                false,
            ),
        ];
        let expected_text: String = spans.iter().map(|span| span.text.as_str()).collect();
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);

        for token in ["[ZX 100]", "[QY 200]"] {
            let entry = blocks
                .iter()
                .find(|block| block.text.starts_with(token))
                .unwrap_or_else(|| panic!("missing reference entry {token}"));
            assert_eq!(entry.block_type, BlockType::Reference);
            assert!(
                entry.text.contains("hanging-indent continuation"),
                "continuation must merge into its reference anchor: {:?}",
                entry.text
            );
        }
        assert_eq!(
            blocks
                .iter()
                .filter(|block| block.block_type == BlockType::Reference)
                .count(),
            2,
            "two anchors must produce exactly two merged reference blocks"
        );
        let actual_text: String = blocks.iter().map(|block| block.text.as_str()).collect();
        assert_eq!(
            actual_text.chars().filter(|c| !c.is_whitespace()).collect::<String>(),
            expected_text.chars().filter(|c| !c.is_whitespace()).collect::<String>(),
            "reference promotion must not delete text"
        );
    }

    #[test]
    fn test_repeated_reference_stream_covers_short_source_tags() {
        let spans = vec![
            make_span("[ZX 100]", 95.0, 700.0, 10.2, false),
            make_span(
                "A definition set to the right of the source column.",
                226.0,
                680.0,
                11.0,
                false,
            ),
            make_span("[QY 200]", 95.0, 430.0, 10.0, false),
            make_span(
                "Another definition in the same glossary layout.",
                226.0,
                410.0,
                11.0,
                false,
            ),
        ];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);

        let source_tags: Vec<&ClassifiedBlock> = blocks
            .iter()
            .filter(|block| block.block_type == BlockType::Reference)
            .collect();
        assert_eq!(source_tags.len(), 2);
        assert!(source_tags.iter().any(|block| block.text == "[ZX 100]"));
        assert!(source_tags.iter().any(|block| block.text == "[QY 200]"));
    }

    #[test]
    fn test_isolated_bracketed_line_cannot_borrow_from_another_column() {
        let spans = vec![
            make_span("[ZX 100] First entry", 95.0, 700.0, 10.2, false),
            make_span("[QY 200] Second entry", 95.0, 650.0, 10.2, false),
            make_span("[RS 300]", 330.0, 500.0, 10.2, false),
        ];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);

        assert_eq!(
            blocks
                .iter()
                .filter(|block| block.block_type == BlockType::Reference)
                .count(),
            2
        );
        let isolated = blocks
            .iter()
            .find(|block| block.text.contains("[RS 300]"))
            .expect("isolated bracketed line must be retained");
        assert_eq!(isolated.block_type, BlockType::Body);
    }

    #[test]
    fn test_reference_continuation_does_not_cross_column_boundary() {
        let spans = vec![
            make_span("[ZX 100] First entry", 290.0, 700.0, 10.0, false),
            make_span("opposite-column text", 310.0, 685.0, 10.0, false),
            make_span("[QY 200] Second entry", 290.0, 650.0, 10.0, false),
            make_span("more opposite-column text", 310.0, 635.0, 10.0, false),
        ];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);

        let references: Vec<_> = blocks
            .iter()
            .filter(|block| block.block_type == BlockType::Reference)
            .collect();
        assert_eq!(references.len(), 2);
        assert!(
            references
                .iter()
                .all(|block| !block.text.contains("opposite-column")),
            "cross-column neighbors must not merge into reference anchors: {blocks:?}"
        );
        assert!(
            blocks
                .iter()
                .any(|block| block.text.contains("opposite-column text")),
            "cross-column text must remain present as a separate block: {blocks:?}"
        );
    }

    #[test]
    fn test_bracket_prefixed_running_paragraph_lines_remain_body() {
        let spans = vec![
            make_span(
                "[12] continues the running paragraph across nearly the full text column",
                50.0,
                700.0,
                10.0,
                false,
            ),
            make_span(
                "with a flush-left continuation on the following line",
                50.0,
                685.0,
                10.0,
                false,
            ),
            make_span(
                "[34] another running paragraph line spanning nearly the full text column",
                50.0,
                650.0,
                10.0,
                false,
            ),
            make_span(
                "and this continuation also remains flush left",
                50.0,
                635.0,
                10.0,
                false,
            ),
        ];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);

        assert!(
            blocks
                .iter()
                .all(|block| block.block_type != BlockType::Reference),
            "flush-left running prose must not become references: {blocks:?}"
        );
        let actual_text: String = blocks.iter().map(|block| block.text.as_str()).collect();
        let expected_text: String = spans.iter().map(|span| span.text.as_str()).collect();
        assert_eq!(
            actual_text
                .chars()
                .filter(|character| !character.is_whitespace())
                .collect::<String>(),
            expected_text
                .chars()
                .filter(|character| !character.is_whitespace())
                .collect::<String>(),
            "running-prose guard must not delete text"
        );
    }

    #[test]
    fn test_control_catalog_heading_shape_and_negative_guards() {
        assert!(has_compact_control_id_prefix("ZX-7 Retention Overview"));
        assert!(has_compact_control_id_prefix(
            "QRS-42(3) Alternate Processing"
        ));
        assert!(!has_compact_control_id_prefix("ZX-7"));
        assert!(!has_compact_control_id_prefix(
            "zx-7 Retention Overview"
        ));
        assert!(!has_compact_control_id_prefix(
            "TABLE C-7: Retention Overview"
        ));

        assert!(starts_with_url_scheme_or_link_phrase(
            "https://example.invalid/reference"
        ));
        assert!(starts_with_url_scheme_or_link_phrase(
            "Quick link to a summary"
        ));
        assert!(!starts_with_url_scheme_or_link_phrase(
            "ZX-7 Retention Overview"
        ));
        assert!(!starts_with_url_scheme_or_link_phrase("Control:"));

        let spans = vec![make_span("body", 90.0, 500.0, 11.0, false)];
        let classifier = BlockClassifier::new_with_overrides(
            612.0,
            792.0,
            &spans,
            Some(11.0),
            None,
        );
        let block = |block_type, text: &str, bbox, font_size, is_bold| {
            classifier.make_block(
                block_type,
                text.to_string(),
                bbox,
                font_size,
                "TestFont".to_string(),
                is_bold,
                0.8,
                None,
                None,
            )
        };

        let mut blocks = vec![
            block(
                BlockType::Header,
                "RUNNING HEADER",
                Rect::new(90.0, 750.0, 180.0, 8.0),
                8.0,
                false,
            ),
            block(
                BlockType::Body,
                "ZX-7 RETENTION OVERVIEW",
                Rect::new(90.0, 650.0, 190.0, 11.0),
                11.0,
                false,
            ),
            block(
                BlockType::Body,
                "Control:",
                Rect::new(126.0, 632.0, 40.0, 10.0),
                10.0,
                false,
            ),
            // Deliberately control-ID-shaped: its existing Caption type is the
            // decisive guard and must survive unchanged.
            block(
                BlockType::Caption,
                "ZX-8 TABLE CAPTION",
                Rect::new(180.0, 600.0, 150.0, 9.0),
                9.0,
                true,
            ),
            block(
                BlockType::Body,
                "https://example.invalid/reference",
                Rect::new(90.0, 580.0, 180.0, 10.0),
                10.0,
                true,
            ),
            block(
                BlockType::Body,
                "Quick link to a summary",
                Rect::new(90.0, 560.0, 160.0, 10.0),
                10.0,
                true,
            ),
            block(
                BlockType::Body,
                "QZ-8 ALTERNATE PROCESSING",
                Rect::new(90.0, 530.0, 190.0, 11.0),
                11.0,
                true,
            ),
            block(
                BlockType::Body,
                "Plain Label:",
                Rect::new(126.0, 512.0, 64.0, 10.0),
                10.0,
                false,
            ),
        ];
        let original_text: String = blocks.iter().map(|item| item.text.as_str()).collect();
        let underlined = vec![false, false, true, false, false, false, false, false];

        promote_control_catalog_headings(&mut blocks, &underlined, 612.0, 792.0);

        assert_eq!(blocks[1].block_type, BlockType::Title);
        assert_eq!(blocks[2].block_type, BlockType::Subtitle);
        assert_eq!(blocks[3].block_type, BlockType::Caption);
        assert_eq!(blocks[4].block_type, BlockType::Body);
        assert_eq!(blocks[5].block_type, BlockType::Body);
        assert_eq!(blocks[6].block_type, BlockType::Title);
        assert_eq!(blocks[7].block_type, BlockType::Body);
        let final_text: String = blocks.iter().map(|item| item.text.as_str()).collect();
        assert_eq!(final_text, original_text, "promotion must not delete text");
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
    fn test_body_font_frequency_uses_text_coverage_not_span_count() {
        // Many tiny chrome fragments can outnumber the much longer body-text
        // spans on a page. They must not pull the body-font estimate down and
        // make ordinary body-size text look like large-font heading text.
        let mut spans = Vec::new();
        for sequence in 0..60 {
            let mut chrome = make_span("x", 20.0 + sequence as f32, 770.0, 9.0, false);
            chrome.sequence = sequence;
            spans.push(chrome);
        }

        let prose = "ordinary body prose continues across nearly the full text column without ending here";
        let continuation = "and this following line completes the same paragraph in the normal body face.";
        spans.push(make_span(prose, 90.0, 700.0, 11.0, true));
        spans.push(make_span(continuation, 90.0, 685.0, 11.0, false));
        spans.push(make_span(
            "2.2 CONTROL STRUCTURE AND ORGANIZATION",
            90.0,
            650.0,
            14.0,
            true,
        ));

        let expected_chars: String = spans.iter().map(|span| span.text.as_str()).collect();
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);

        let prose_block = blocks
            .iter()
            .find(|block| block.text.contains(prose))
            .expect("body prose must be retained");
        assert_eq!(prose_block.block_type, BlockType::Body);
        assert!(prose_block.text.contains(continuation));

        let heading = blocks
            .iter()
            .find(|block| block.text.contains("CONTROL STRUCTURE"))
            .expect("real heading must be retained");
        assert_eq!(heading.block_type, BlockType::Title);

        let actual_chars: String = blocks
            .iter()
            .flat_map(|block| block.text.chars())
            .filter(|c| !c.is_whitespace())
            .collect();
        let expected_chars: String = expected_chars
            .chars()
            .filter(|c| !c.is_whitespace())
            .collect();
        assert_eq!(actual_chars, expected_chars, "classification must not delete text");
    }

    #[test]
    fn test_body_sized_centered_all_caps_callout_titles_remain_headings() {
        const PAGE_WIDTH: f32 = 612.0;
        let titles = [
            "CONTROL BASELINES",
            "FEDERAL RECORDS MANAGEMENT COLLABORATION",
        ];

        for title in titles {
            let mut callout_title = make_span(title, 0.0, 700.0, 11.0, true);
            callout_title.bbox.x = (PAGE_WIDTH - callout_title.bbox.width) / 2.0;
            let body = make_span(
                "ordinary body prose supplies the dominant body-size coverage on this page.",
                72.0,
                680.0,
                11.0,
                false,
            );
            let expected_text = format!("{}{}", callout_title.text, body.text);
            let spans = vec![callout_title, body];

            let classifier = BlockClassifier::new(PAGE_WIDTH, 792.0, &spans);
            let blocks = classifier.classify_spans(&spans);

            let heading = blocks
                .iter()
                .find(|block| block.text == title)
                .expect("callout title must be retained as its own block");
            assert_eq!(heading.block_type, BlockType::Title);
            assert_eq!(heading.font_size, 11.0, "body-size title must be rescued");

            let actual_text: String = blocks.iter().map(|block| block.text.as_str()).collect();
            assert_eq!(actual_text, expected_text, "classification must retain every character");
        }
    }

    #[test]
    fn test_centered_full_width_prose_stays_body() {
        const PAGE_WIDTH: f32 = 612.0;
        let prose_fragments = [
            "The organization defines the applicable controls and",
            "systems and services operating within the authorization",
            "records are retained according to the approved schedule",
            "and responsibilities remain assigned across the enterprise",
        ];

        for prose in prose_fragments {
            let mut span = make_span(prose, 0.0, 700.0, 11.0, true);
            span.bbox.width = PAGE_WIDTH * 0.9;
            span.bbox.x = (PAGE_WIDTH - span.bbox.width) / 2.0;
            let spans = vec![span];

            let classifier = BlockClassifier::new(PAGE_WIDTH, 792.0, &spans);
            let blocks = classifier.classify_spans(&spans);

            assert_eq!(blocks.len(), 1);
            assert_eq!(blocks[0].block_type, BlockType::Body);
            assert_eq!(blocks[0].text, prose, "classification must retain every character");
        }
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

    // --- BlockLine preservation tests (WebGPT 2026-05-12 PR A1) ---

    /// Bbox/text/font/count invariants must not change just because we now
    /// preserve per-line evidence on ClassifiedBlock. This builds three
    /// synthetic Body lines that merge_consecutive_body will collapse and
    /// asserts the collapse semantics are unchanged.
    #[test]
    fn test_lines_preserved_through_body_merge() {
        // Three short Body lines at increasing Y, close enough to merge.
        // Note: descending Y in PDF coordinates (top-of-page = high y).
        let spans = vec![
            make_span("Line one of body.", 90.0, 700.0, 11.0, false),
            make_span("Line two of body.", 90.0, 685.0, 11.0, false),
            make_span("Line three of body.", 90.0, 670.0, 11.0, false),
        ];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);

        // All three lines collapse to one Body block (existing semantic).
        assert_eq!(blocks.len(), 1, "expected 1 merged Body block, got {}", blocks.len());
        let block = &blocks[0];
        assert_eq!(block.block_type, BlockType::Body);

        // Block text is the existing concatenation (with spaces).
        assert!(block.text.contains("Line one of body."));
        assert!(block.text.contains("Line two of body."));
        assert!(block.text.contains("Line three of body."));

        // Block bbox is the union of the three line bboxes (existing semantic).
        // Union: x in [90, 90+19*11*0.5=194.5], y in [670, 711].
        assert!(block.bbox.x <= 90.0 + 0.01);
        assert!(block.bbox.y <= 670.0 + 0.01);

        // NEW: per-line evidence is preserved.
        assert_eq!(block.lines.len(), 3, "expected 3 lines preserved after merge");

        // Each line bbox stays within the block bbox (line union == block bbox).
        let line_union = block.lines.iter().fold(
            block.lines[0].bbox,
            |acc, line| acc.union(&line.bbox),
        );
        let drift_x = (block.bbox.x - line_union.x).abs();
        let drift_y = (block.bbox.y - line_union.y).abs();
        let drift_w = (block.bbox.width - line_union.width).abs();
        let drift_h = (block.bbox.height - line_union.height).abs();
        assert!(drift_x < 0.5, "block.bbox.x drift {} from line union", drift_x);
        assert!(drift_y < 0.5, "block.bbox.y drift {} from line union", drift_y);
        assert!(drift_w < 0.5, "block.bbox.width drift {} from line union", drift_w);
        assert!(drift_h < 0.5, "block.bbox.height drift {} from line union", drift_h);

        // Lines appear in source order.
        assert_eq!(block.lines[0].text, "Line one of body.");
        assert_eq!(block.lines[1].text, "Line two of body.");
        assert_eq!(block.lines[2].text, "Line three of body.");
    }

    /// Single-line non-merged block still gets a lines entry of length 1.
    #[test]
    fn test_single_line_block_has_one_line() {
        let spans = vec![make_span("A single title.", 90.0, 700.0, 18.0, true)];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);
        assert_eq!(blocks.len(), 1);
        assert_eq!(blocks[0].lines.len(), 1);
        assert_eq!(blocks[0].lines[0].text, "A single title.");
        assert!(blocks[0].lines[0].is_bold);
    }

    /// Make_block default still produces an empty lines vec — required so
    /// that non-classify_spans constructors of ClassifiedBlock keep working.
    #[test]
    fn test_make_block_default_lines_empty() {
        let spans = vec![make_span("dummy", 0.0, 0.0, 11.0, false)];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let block = classifier.make_block(
            BlockType::Body,
            "x".to_string(),
            Rect { x: 0.0, y: 0.0, width: 1.0, height: 1.0 },
            11.0,
            "F".to_string(),
            false,
            0.5,
            None,
            None,
        );
        assert_eq!(block.lines.len(), 0);
    }

    /// merge_consecutive_body must not collapse non-Body neighbors. Header
    /// + Body stays as two distinct blocks, each with its own lines.
    #[test]
    fn test_header_body_not_merged_each_has_lines() {
        let spans = vec![
            // Big bold heading at the top
            make_span("HEADING", 90.0, 700.0, 18.0, true),
            // Body line below (close enough to not be page-header noise)
            make_span("Body paragraph text.", 90.0, 660.0, 11.0, false),
        ];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);
        assert_eq!(blocks.len(), 2);
        assert_eq!(blocks[0].lines.len(), 1);
        assert_eq!(blocks[1].lines.len(), 1);
    }

    /// GS001 R6 row-4 regression — kerned NIST 800-53r5 page-27 text where
    /// the PDF's TJ operator emits "Modern" as fragments in content-stream
    /// order ["der", "Mo", "n information systems"] (out of x-order), and a
    /// superscript footnote marker "1" appears slightly above the baseline.
    ///
    /// Pre-R6: classify_line joined spans in iteration order →
    /// "derMon information systems1 can include...".
    /// Post-R6 (this test): classify_line sorts by (bbox.x, sequence) before
    /// joining text →
    /// "Modern information systems1 can include...".
    ///
    /// The page-level block ordering is unchanged; only the per-line text
    /// concatenation is re-sorted.
    #[test]
    fn test_classify_line_kerned_span_order_yields_modern_not_dermon() {
        // Build spans with the same x-positions WebGPT specified in the R6
        // plan: der(x=105), Mo(x=90), "n information systems"(x=120),
        // superscript "1"(x=220, y slightly above baseline), and the rest of
        // the line " can include..." (x=224).
        let mut der = make_span("der", 105.0, 631.3, 10.98, false);
        der.sequence = 57; // content-stream order: der drawn FIRST
        let mut mo = make_span("Mo", 90.0, 631.3, 10.98, false);
        mo.sequence = 58; // then "Mo" via TJ backward shift
        let mut n_rest = make_span("n information systems", 120.0, 631.3, 10.98, false);
        n_rest.sequence = 59;
        let mut sup_one = make_span("1", 220.6, 635.3, 7.02, false);
        sup_one.sequence = 60; // superscript footnote marker
        let mut can_include = make_span(
            " can include a variety of computing platforms",
            224.1,
            631.3,
            10.98,
            false,
        );
        can_include.sequence = 61;
        let spans = vec![der, mo, n_rest, sup_one, can_include];

        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);
        assert_eq!(blocks.len(), 1, "all spans should group into a single line/block");
        let text = &blocks[0].text;
        assert!(
            !text.contains("derMon"),
            "post-R6 text must not contain the 'derMon' artifact; got: {text:?}"
        );
        assert!(
            !text.starts_with("1Modern"),
            "post-R6 text must not start with '1Modern' (would mean superscript leaked to front); got: {text:?}"
        );
        assert!(
            text.starts_with("Modern information systems"),
            "post-R6 text must begin exactly 'Modern information systems...'; got: {text:?}"
        );
    }

    /// GS001 R8 — bullet-list run grouping. The classifier should merge a
    /// bullet anchor with its sibling bullets AND its body continuations
    /// into ONE List block. Footnotes following the run must NOT be
    /// absorbed.
    #[test]
    fn test_merge_list_runs_groups_bullets_and_continuations() {
        // PDF bottom-left origin (page-points): larger y is higher on
        // the page. NIST page-28-like geometry: bullets at x=90,
        // continuations at x=108, footnote at x=90 below the list with
        // smaller font + larger y_gap.
        let spans = vec![
            // Header paragraph above (won't be merged with the list run)
            make_span("Preamble text.", 90.0, 300.0, 11.0, false),
            // Bullet 1 (below preamble)
            make_span("• First bullet text starts here.", 90.0, 270.0, 11.0, false),
            // Continuation of bullet 1 (indented, just below bullet 1)
            make_span("continuation of first bullet.", 108.0, 255.0, 11.0, false),
            // Bullet 2 (sibling)
            make_span("• Second bullet text.", 90.0, 240.0, 11.0, false),
            // Bullet 3 (sibling)
            make_span("• Third bullet text.", 90.0, 225.0, 11.0, false),
            // Continuation of bullet 3 (indented)
            make_span("continuation of third bullet.", 108.0, 210.0, 11.0, false),
            // Footnote below — smaller font (9pt) + larger y_gap. Must NOT
            // be absorbed into the list run.
            make_span("1 Footnote text at base column.", 90.0, 170.0, 9.0, false),
        ];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);

        // Expected blocks: [Preamble Body, merged-List, Footnote Body]
        let list_blocks: Vec<&ClassifiedBlock> = blocks
            .iter()
            .filter(|b| b.block_type == BlockType::List)
            .collect();
        assert_eq!(
            list_blocks.len(),
            1,
            "bullet-list run should merge into a single List block, got {} list blocks: {:?}",
            list_blocks.len(),
            blocks
                .iter()
                .map(|b| (format!("{:?}", b.block_type), b.text.chars().take(40).collect::<String>()))
                .collect::<Vec<_>>()
        );
        let list = list_blocks[0];
        for needle in [
            "First bullet text",
            "continuation of first",
            "Second bullet text",
            "Third bullet text",
            "continuation of third",
        ] {
            assert!(
                list.text.contains(needle),
                "merged list missing fragment {needle:?}; got: {:?}",
                list.text
            );
        }
        assert!(
            !list.text.contains("Footnote"),
            "merged list should NOT absorb the footnote; got: {:?}",
            list.text
        );
        // Footnote should remain its own block
        let footnotes: Vec<&ClassifiedBlock> = blocks
            .iter()
            .filter(|b| b.text.contains("Footnote"))
            .collect();
        assert_eq!(footnotes.len(), 1, "footnote should be preserved as a separate block");
        // Preamble untouched
        let preambles: Vec<&ClassifiedBlock> = blocks
            .iter()
            .filter(|b| b.text.contains("Preamble"))
            .collect();
        assert_eq!(preambles.len(), 1, "preamble should remain separate from the list run");
    }

    /// GS001 R8 — empty-text classified blocks must not appear in the
    /// final block list.
    #[test]
    fn test_classify_spans_drops_empty_text_blocks() {
        let spans = vec![
            make_span("Real paragraph text.", 90.0, 100.0, 11.0, false),
            make_span("   ", 90.0, 130.0, 11.0, false), // whitespace-only
            make_span("", 90.0, 160.0, 11.0, false),    // empty
            make_span("Second paragraph.", 90.0, 200.0, 11.0, false),
        ];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);
        for b in &blocks {
            assert!(
                !b.text.trim().is_empty(),
                "empty-text block leaked through the filter: {b:?}"
            );
        }
        // We should still see both real paragraphs (possibly merged into one body block)
        let total_text: String = blocks.iter().map(|b| b.text.as_str()).collect();
        assert!(total_text.contains("Real paragraph text"));
        assert!(total_text.contains("Second paragraph"));
    }

    /// Companion control — when spans already arrive in x-order, the sort
    /// is a no-op and the joined text is unchanged.
    #[test]
    fn test_classify_line_already_sorted_spans_unchanged() {
        let mut a = make_span("Hello ", 90.0, 700.0, 11.0, false);
        a.sequence = 1;
        let mut b = make_span("world.", 130.0, 700.0, 11.0, false);
        b.sequence = 2;
        let spans = vec![a, b];
        let classifier = BlockClassifier::new(612.0, 792.0, &spans);
        let blocks = classifier.classify_spans(&spans);
        assert_eq!(blocks.len(), 1);
        assert_eq!(blocks[0].text, "Hello world.");
    }

    #[test]
    fn test_repeated_edge_aligned_body_face_text_is_boilerplate_without_deletion() {
        let spans = vec![
            make_span("First rotated margin segment", 21.0, 565.0, 10.0, false),
            make_span("Second rotated margin segment", 21.0, 370.0, 10.0, false),
            make_span("Third rotated margin segment", 21.0, 220.0, 10.0, false),
            make_span("Ordinary prose remains inside the body column.", 90.0, 500.0, 10.0, false),
        ];
        let expected: String = spans.iter().map(|span| span.text.as_str()).collect();
        let classifier =
            BlockClassifier::new_with_overrides(612.0, 792.0, &spans, Some(10.0), None);
        let blocks = classifier.classify_spans(&spans);

        let margin_blocks: Vec<_> = blocks
            .iter()
            .filter(|block| block.text.contains("rotated margin segment"))
            .collect();
        assert_eq!(margin_blocks.len(), 3);
        assert!(margin_blocks
            .iter()
            .all(|block| block.block_type == BlockType::Boilerplate));
        assert_eq!(
            blocks
                .iter()
                .find(|block| block.text.contains("Ordinary prose"))
                .map(|block| block.block_type),
            Some(BlockType::Body)
        );

        let actual: String = blocks.iter().map(|block| block.text.as_str()).collect();
        let without_whitespace = |value: String| {
            value
                .chars()
                .filter(|character| !character.is_whitespace())
                .collect::<String>()
        };
        assert_eq!(without_whitespace(actual), without_whitespace(expected));
    }

    #[test]
    fn test_assembled_tall_margin_span_is_boilerplate_without_text_deletion() {
        let mut margin = make_span("Complete rotated margin line", 21.0, 220.0, 10.0, false);
        margin.bbox = Rect::new(21.0, 220.0, 10.0, 330.0);
        let body =
            make_span("Ordinary prose remains inside the body column.", 90.0, 500.0, 10.0, false);
        let expected = format!("{}{}", margin.text, body.text);
        let spans = vec![margin, body];
        let classifier =
            BlockClassifier::new_with_overrides(612.0, 792.0, &spans, Some(10.0), None);

        let blocks = classifier.classify_spans(&spans);

        assert_eq!(blocks.len(), 2);
        assert_eq!(blocks[0].block_type, BlockType::Boilerplate);
        assert_eq!(blocks[0].text, "Complete rotated margin line");
        let actual: String = blocks.iter().map(|block| block.text.as_str()).collect();
        assert_eq!(actual, expected);
    }

    #[test]
    fn test_single_body_face_line_at_page_edge_is_not_boilerplate() {
        let spans = vec![make_span(
            "A single marginal prose note remains content.",
            21.0,
            400.0,
            10.0,
            false,
        )];
        let classifier =
            BlockClassifier::new_with_overrides(612.0, 792.0, &spans, Some(10.0), None);
        let blocks = classifier.classify_spans(&spans);
        assert_eq!(blocks.len(), 1);
        assert_eq!(blocks[0].block_type, BlockType::Body);
    }

    #[test]
    fn test_wide_margin_band_chrome_tolerates_skewed_body_face() {
        let mut header = make_span("RUNNING DOCUMENT HEADER", 90.0, 748.0, 8.0, false);
        header.bbox.width = 410.0;
        let mut rule = make_span("________________________________", 90.0, 738.0, 8.0, false);
        rule.bbox.width = 410.0;
        let mut footer = make_span("Chapter Overview", 90.0, 38.0, 7.0, false);
        footer.bbox.width = 410.0;
        let spans = vec![header, rule, footer];
        let classifier = BlockClassifier::new_with_overrides(612.0, 792.0, &spans, Some(7.0), None);
        let blocks = classifier.classify_spans(&spans);

        assert_eq!(blocks.len(), 3);
        assert_eq!(blocks[0].block_type, BlockType::Header);
        assert_eq!(blocks[1].block_type, BlockType::Header);
        assert_eq!(blocks[2].block_type, BlockType::Footer);
    }
}
