//! Section hierarchy extraction with TOC-guided promotion and numbering parsing.
//!
//! Builds a tree of document sections from classified blocks. Enhances the basic
//! font-size-based header detection with:
//! - Section numbering parsing (e.g. "1.2.3 Title" → level 3)
//! - TOC/outline-guided level promotion
//! - False positive filtering (single words, page numbers, etc.)
//! - S04 section builder: flat sections with body content aggregation
//! - S04a layout audit: block ordering validation

use crate::document::PdfDocument;
use crate::error::Result;
use crate::extractors::block_classifier::{
    BlockClassifier, BlockType, ClassifiedBlock, HeaderDisposition,
    analyze_section_numbering,
};
use crate::geometry::Rect;

#[derive(Debug, Clone)]
pub struct Section {
    pub title: String,
    pub level: u8,
    pub page: usize,
    pub bbox: (f32, f32, f32, f32),
    pub children: Vec<Section>,
    pub numbering: Option<String>,
}

#[derive(Debug, Clone)]
pub struct SectionTree {
    pub sections: Vec<Section>,
    pub total_sections: usize,
    pub max_depth: u8,
}

// ---------------------------------------------------------------------------
// Flat section builder (absorbs S04 section_builder.py logic)
// ---------------------------------------------------------------------------

/// A flat section with body content aggregation, suitable for pipeline output.
///
/// This replaces the Python S04 section builder. Each section has:
/// - A header block (title, numbering, level)
/// - Aggregated body content from subsequent non-header blocks
/// - Page span (start/end)
/// - Section metadata (hash, display_title, section_number)
#[derive(Debug, Clone)]
pub struct FlatSection {
    /// Section title (from header block)
    pub title: String,
    /// Display title (stripped of numbering prefix)
    pub display_title: String,
    /// Section level (1-6, from numbering or font heuristics)
    pub level: u8,
    /// Section number string (e.g. "1.2.3")
    pub section_number: String,
    /// First page of this section
    pub page_start: usize,
    /// Last page of this section
    pub page_end: usize,
    /// Bounding box encompassing all blocks in this section
    pub bbox: Rect,
    /// Aggregated body content (text from all non-header blocks)
    pub content: String,
    /// Number of blocks in this section
    pub block_count: usize,
    /// MD5 hash of display_title (for dedup/tracking)
    pub section_hash: String,
    /// Parent section ID index (None for top-level)
    pub parent_idx: Option<usize>,
    /// Header disposition from block_classifier (Accept/Reject/Escalate)
    pub header_disposition: Option<HeaderDisposition>,
}

/// Build flat sections from classified blocks across all pages.
///
/// This is the Rust equivalent of S04's `build_sections_from_blocks()`.
/// It takes classified blocks (from BlockClassifier) and groups them into
/// sections: each header block starts a new section, subsequent non-header
/// blocks become the section's body content.
pub fn build_flat_sections(
    page_blocks: &[(usize, Vec<ClassifiedBlock>)],
) -> Vec<FlatSection> {
    let mut sections: Vec<FlatSection> = Vec::new();
    let mut current: Option<FlatSection> = None;

    for (page, blocks) in page_blocks {
        for block in blocks {
            let is_header = block.block_type == BlockType::Title
                && block.header_level.is_some();

            if is_header {
                // Check disposition — only start a new section for Accept or Escalate
                let disposition = block.header_validation.as_ref()
                    .map(|hv| hv.disposition);

                // If disposition is Reject, treat as body text
                if disposition == Some(HeaderDisposition::Reject) {
                    append_body_block(&mut current, &mut sections, *page, block);
                    continue;
                }

                // Additional negative filter: partial sentence or citation fragment
                if is_partial_sentence(&block.text) || is_citation_fragment(&block.text) {
                    append_body_block(&mut current, &mut sections, *page, block);
                    continue;
                }

                // Flush current section
                if let Some(sec) = current.take() {
                    sections.push(sec);
                }

                // Start new section
                let numbering = analyze_section_numbering(&block.text);
                let display_title = if numbering.has_numbering {
                    numbering.title_text.clone()
                } else {
                    block.text.trim().to_string()
                };

                let section_number = if numbering.has_numbering {
                    numbering.number_text.clone()
                } else {
                    String::new()
                };

                let section_hash = compute_hash(&display_title);
                let level = block.header_level.unwrap_or(1);

                current = Some(FlatSection {
                    title: block.text.trim().to_string(),
                    display_title,
                    level,
                    section_number,
                    page_start: *page,
                    page_end: *page,
                    bbox: block.bbox,
                    content: String::new(),
                    block_count: 1,
                    section_hash,
                    parent_idx: None,
                    header_disposition: disposition,
                });
            } else {
                append_body_block(&mut current, &mut sections, *page, block);
            }
        }
    }

    // Flush last section
    if let Some(sec) = current.take() {
        sections.push(sec);
    }

    // Post-processing: merge continuation headers, assign parents
    let sections = merge_continuation_sections(sections);
    let sections = assign_parents(sections);

    sections
}

/// Append a body block to the current section, or create an untitled section.
fn append_body_block(
    current: &mut Option<FlatSection>,
    sections: &mut Vec<FlatSection>,
    page: usize,
    block: &ClassifiedBlock,
) {
    let text = block.text.trim();
    if text.is_empty() {
        return;
    }

    if let Some(ref mut sec) = current {
        if !sec.content.is_empty() {
            sec.content.push('\n');
        }
        sec.content.push_str(text);
        sec.block_count += 1;
        sec.page_end = sec.page_end.max(page);
        sec.bbox = sec.bbox.union(&block.bbox);
    } else {
        // No current section — create untitled section for leading content
        let mut sec = FlatSection {
            title: String::new(),
            display_title: String::new(),
            level: 0,
            section_number: String::new(),
            page_start: page,
            page_end: page,
            bbox: block.bbox,
            content: text.to_string(),
            block_count: 1,
            section_hash: String::new(),
            parent_idx: None,
            header_disposition: None,
        };
        // If there's a next section, this will be merged into it
        *current = Some(sec);
    }
}

/// Merge continuation headers ("(continued)", lowercase-starting titles).
fn merge_continuation_sections(sections: Vec<FlatSection>) -> Vec<FlatSection> {
    if sections.is_empty() {
        return sections;
    }

    let mut merged: Vec<FlatSection> = Vec::new();

    for sec in sections {
        let title = sec.title.trim();

        // Merge untitled leading section into next
        if merged.is_empty() && title.is_empty() && sec.content.is_empty() {
            continue;
        }

        // Check for continuation patterns
        let is_continuation = {
            let lower = title.to_lowercase();
            lower.contains("(continued)") || lower.ends_with("- continued")
        };

        // Check for lowercase-starting title (mid-sentence fragment)
        let is_lowercase_start = !title.is_empty()
            && title.chars().next().map_or(false, |c| c.is_lowercase());

        if !merged.is_empty() && (is_continuation || is_lowercase_start) {
            let prev = merged.last_mut().unwrap();
            // Merge body content
            if !sec.content.is_empty() {
                if !prev.content.is_empty() {
                    prev.content.push('\n');
                }
                prev.content.push_str(&sec.content);
            }
            prev.block_count += sec.block_count;
            prev.page_end = prev.page_end.max(sec.page_end);
            prev.bbox = prev.bbox.union(&sec.bbox);
            continue;
        }

        // Merge untitled section into next titled one
        if !merged.is_empty() {
            let prev = merged.last().unwrap();
            if prev.title.is_empty() && !sec.title.is_empty() {
                let mut prev = merged.pop().unwrap();
                // Prepend prev's content to this section
                let mut new_sec = sec;
                if !prev.content.is_empty() {
                    let old_content = std::mem::take(&mut new_sec.content);
                    new_sec.content = prev.content;
                    if !old_content.is_empty() {
                        new_sec.content.push('\n');
                        new_sec.content.push_str(&old_content);
                    }
                }
                new_sec.block_count += prev.block_count;
                new_sec.page_start = new_sec.page_start.min(prev.page_start);
                new_sec.bbox = new_sec.bbox.union(&prev.bbox);
                merged.push(new_sec);
                continue;
            }
        }

        merged.push(sec);
    }

    merged
}

/// Assign parent indices based on level hierarchy.
fn assign_parents(mut sections: Vec<FlatSection>) -> Vec<FlatSection> {
    // Walk backwards from each section to find the nearest section with lower level
    for i in 0..sections.len() {
        let current_level = sections[i].level;
        if current_level <= 1 {
            continue;
        }
        // Find nearest preceding section with lower level
        for j in (0..i).rev() {
            if sections[j].level < current_level {
                sections[i].parent_idx = Some(j);
                break;
            }
        }
    }
    sections
}

/// Compute MD5 hash of a string (for section dedup/tracking).
fn compute_hash(text: &str) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut hasher = DefaultHasher::new();
    text.trim().to_lowercase().hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

// ---------------------------------------------------------------------------
// S04 negative header filters (feature extraction, not hard decisions)
// ---------------------------------------------------------------------------

/// Detect partial sentences that should not be section headers.
///
/// Absorbs S04's `_is_partial_sentence()`. These are body text fragments
/// that got bold/large font styling but aren't actual headers.
fn is_partial_sentence(text: &str) -> bool {
    let t = text.trim();
    if t.is_empty() {
        return false;
    }
    // Very long text is almost certainly not a header
    if t.len() > 120 {
        return true;
    }
    // Starts with lowercase
    if t.chars().next().map_or(false, |c| c.is_lowercase()) {
        return true;
    }
    // Ends with continuation punctuation
    if t.ends_with(',') || t.ends_with(';') {
        return true;
    }
    // Starts with continuation words
    let lower = t.to_lowercase();
    let continuation = [
        "and ", "or ", "but ", "that ", "which ", "where ", "when ",
        "including ", "such as ", "as well as ", "in addition ",
        "for example", "e.g.", "i.e.",
    ];
    if continuation.iter().any(|w| lower.starts_with(w)) {
        return true;
    }
    // Contains clause-internal markers
    let internal = [
        " e.g. ", " i.e. ", " etc.", " et al.",
        " is defined as ", " contains ", " includes ",
    ];
    if internal.iter().any(|m| lower.contains(m)) {
        return true;
    }
    false
}

/// Detect citation fragments, footnotes, and inline list items.
///
/// Absorbs S04's `_is_citation_footnote_list_item()` — the most brittle
/// patterns. Only catches the clearest cases; ambiguous ones should go
/// through the cascade.
fn is_citation_fragment(text: &str) -> bool {
    let t = text.trim();
    if t.is_empty() {
        return false;
    }
    let bytes = t.as_bytes();

    // Number followed by bracket: "4 ]." (citation fragment)
    if bytes[0].is_ascii_digit() {
        let mut i = 0;
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
        }
        // Skip whitespace
        while i < bytes.len() && bytes[i] == b' ' {
            i += 1;
        }
        if i < bytes.len() && bytes[i] == b']' {
            return true;
        }
    }

    // Pure numeric: "0.315", "12.5%"
    if t.chars().all(|c| c.is_ascii_digit() || c == '.' || c == ',' || c == '%' || c == ' ') {
        return true;
    }

    // DOI pattern
    let lower = t.to_lowercase();
    if lower.starts_with("doi:") || lower.starts_with("10.") || lower.contains("doi.org") {
        return true;
    }

    // Page range: "51-60.", "199-209."
    if bytes[0].is_ascii_digit() {
        let has_dash = t.contains('-');
        let stripped = t.trim_end_matches('.');
        if has_dash && stripped.chars().all(|c| c.is_ascii_digit() || c == '-') {
            return true;
        }
    }

    // Starts with lowercase (body text fragment)
    if bytes[0].is_ascii_lowercase() {
        return true;
    }

    // Ends with continuation punctuation
    if t.ends_with('-') || t.ends_with('–') || t.ends_with('—') {
        return true;
    }

    false
}

// ---------------------------------------------------------------------------
// S04a: Layout validation (block ordering within sections)
// ---------------------------------------------------------------------------

/// Validate that blocks within sections are in canonical reading order.
///
/// Absorbs S04a's layout audit. Returns a list of (section_index, is_ok) pairs.
/// Canonical order: (page, y, x) ascending.
pub fn validate_section_order(sections: &[FlatSection], page_blocks: &[(usize, Vec<ClassifiedBlock>)]) -> Vec<(usize, bool)> {
    // Build a flat list of (page, block) tuples for each section
    // Since FlatSection doesn't store individual block bboxes, we validate
    // at the section level: sections should be in page order
    let mut results = Vec::new();

    for (i, sec) in sections.iter().enumerate() {
        if i == 0 {
            results.push((i, true));
            continue;
        }
        let prev = &sections[i - 1];
        // Check ordering: current section should start at same or later page
        let ok = sec.page_start >= prev.page_start
            || (sec.page_start == prev.page_start && sec.bbox.y >= prev.bbox.y);
        results.push((i, ok));
    }

    results
}

// ---------------------------------------------------------------------------
// Tree builder (existing logic)
// ---------------------------------------------------------------------------

pub fn build_section_hierarchy(doc: &mut PdfDocument) -> Result<SectionTree> {
    let page_count = doc.page_count().unwrap_or(0);
    let mut all_headers: Vec<(usize, ClassifiedBlock)> = Vec::new();

    for pg in 0..page_count {
        let spans = doc.extract_spans_unsorted(pg).unwrap_or_default();
        if spans.is_empty() {
            continue;
        }

        let (width, height) = doc.get_page_info(pg)
            .ok()
            .map(|info| (info.media_box.width, info.media_box.height))
            .unwrap_or((612.0, 792.0));

        let classifier = BlockClassifier::new(width, height, &spans);
        let blocks = classifier.classify_spans(&spans);

        for block in blocks {
            if block.block_type == BlockType::Title && block.header_level.is_some() {
                all_headers.push((pg, block));
            }
        }
    }

    // Filter false positives
    let filtered: Vec<(usize, ClassifiedBlock)> = all_headers
        .into_iter()
        .filter(|(_, block)| !is_false_positive_header(&block.text))
        .collect();

    // Parse numbering and adjust levels
    let adjusted = adjust_levels_from_numbering(&filtered);

    // Try TOC-guided promotion from document outline
    let final_headers = match doc.get_outline() {
        Ok(Some(outline)) => promote_from_outline(&adjusted, &outline),
        _ => adjusted,
    };

    let sections = build_tree(&final_headers);
    let total = count_sections(&sections);
    let max_depth = max_section_depth(&sections, 0);

    Ok(SectionTree {
        sections,
        total_sections: total,
        max_depth,
    })
}

/// Filter out headers that are likely false positives.
fn is_false_positive_header(text: &str) -> bool {
    let trimmed = text.trim();

    // Empty or very short
    if trimmed.len() < 2 {
        return true;
    }

    // Pure page numbers
    if trimmed.chars().all(|c| c.is_ascii_digit() || c == '-' || c == '.') {
        return true;
    }

    // Common running headers that aren't real sections
    let lower = trimmed.to_lowercase();
    if matches!(lower.as_str(),
        "abstract" | "references" | "bibliography" | "acknowledgments" |
        "acknowledgements" | "table of contents" | "contents" |
        "list of figures" | "list of tables" | "index" | "appendix" |
        "glossary" | "acronyms" | "abbreviations"
    ) {
        // These are valid section-like headings, keep them
        return false;
    }

    // Single word titles that are less than 3 chars (likely artifacts)
    if !trimmed.contains(' ') && trimmed.len() < 3 {
        return true;
    }

    // Citation fragment or partial sentence
    if is_citation_fragment(trimmed) || is_partial_sentence(trimmed) {
        return true;
    }

    false
}

/// Parse section numbering like "1.2.3" from header text and adjust levels accordingly.
fn adjust_levels_from_numbering(
    headers: &[(usize, ClassifiedBlock)],
) -> Vec<(usize, ClassifiedBlock, Option<String>)> {
    let mut result = Vec::new();

    for (page, block) in headers {
        let (numbering, level_from_numbering) = parse_section_numbering(&block.text);

        let mut adjusted_block = block.clone();
        if let Some(num_level) = level_from_numbering {
            // Numbering-derived level takes precedence over font-size heuristic
            adjusted_block.header_level = Some(num_level);
        }

        result.push((*page, adjusted_block, numbering));
    }

    result
}

/// Parse section numbering from text. Returns (numbering_string, inferred_level).
fn parse_section_numbering(text: &str) -> (Option<String>, Option<u8>) {
    let trimmed = text.trim();

    // Pattern: "N.N.N... Title"
    if let Some(pos) = trimmed.find(|c: char| c.is_whitespace()) {
        let prefix = &trimmed[..pos];

        // Check for dotted numbering: "1.2.3" or "A.1.2"
        if is_dotted_numbering(prefix) {
            let dot_count = prefix.chars().filter(|c| *c == '.').count();
            let level = (dot_count + 1).min(6) as u8;
            return (Some(prefix.to_string()), Some(level));
        }

        // Check for simple number: "1" at start followed by space then text
        if prefix.len() <= 3 && prefix.chars().all(|c| c.is_ascii_digit()) {
            return (Some(prefix.to_string()), Some(1));
        }
    }

    // Pattern: "(a)" or "(i)" lettered/roman sub-sections
    if trimmed.starts_with('(') {
        if let Some(close) = trimmed.find(')') {
            let inner = &trimmed[1..close];
            if inner.len() <= 4 && (
                inner.chars().all(|c| c.is_ascii_lowercase()) ||
                inner.chars().all(|c| c.is_ascii_digit())
            ) {
                let numbering = trimmed[..=close].to_string();
                return (Some(numbering), Some(3));
            }
        }
    }

    (None, None)
}

/// Check if a string is dotted numbering like "1.2", "A.1.2", "IV.3"
fn is_dotted_numbering(s: &str) -> bool {
    if !s.contains('.') {
        return false;
    }

    let parts: Vec<&str> = s.split('.').collect();
    if parts.len() < 2 || parts.len() > 6 {
        return false;
    }

    // Each part must be a number or short alpha (A, B, I, II, etc.)
    parts.iter().all(|part| {
        !part.is_empty() && (
            part.chars().all(|c| c.is_ascii_digit()) ||
            (part.len() <= 4 && part.chars().all(|c| c.is_ascii_uppercase()))
        )
    })
}

/// Use the document outline (TOC) to promote/demote headers.
fn promote_from_outline(
    headers: &[(usize, ClassifiedBlock, Option<String>)],
    outline: &[crate::outline::OutlineItem],
) -> Vec<(usize, ClassifiedBlock, Option<String>)> {
    let mut outline_map: Vec<(String, u8)> = Vec::new();
    flatten_outline(outline, 1, &mut outline_map);

    if outline_map.is_empty() {
        return headers.to_vec();
    }

    let mut result = Vec::new();
    for (page, block, numbering) in headers {
        let mut adjusted_block = block.clone();
        let title_normalized = normalize_for_matching(&block.text);

        if let Some((_, outline_level)) = outline_map.iter()
            .find(|(outline_title, _)| {
                let outline_normalized = normalize_for_matching(outline_title);
                titles_match(&title_normalized, &outline_normalized)
            })
        {
            adjusted_block.header_level = Some(*outline_level);
        }

        result.push((*page, adjusted_block, numbering.clone()));
    }

    result
}

fn flatten_outline(items: &[crate::outline::OutlineItem], level: u8, out: &mut Vec<(String, u8)>) {
    for item in items {
        out.push((item.title.clone(), level));
        if !item.children.is_empty() {
            flatten_outline(&item.children, level + 1, out);
        }
    }
}

/// Normalize title for fuzzy matching: lowercase, strip numbering, collapse whitespace.
fn normalize_for_matching(title: &str) -> String {
    let trimmed = title.trim();

    let stripped = if let Some(pos) = trimmed.find(|c: char| c.is_whitespace()) {
        let prefix = &trimmed[..pos];
        if is_dotted_numbering(prefix) || prefix.chars().all(|c| c.is_ascii_digit()) {
            trimmed[pos..].trim()
        } else {
            trimmed
        }
    } else {
        trimmed
    };

    stripped.to_lowercase()
}

/// Check if two normalized titles match (exact or fuzzy).
fn titles_match(a: &str, b: &str) -> bool {
    if a == b {
        return true;
    }

    if a.len() > 5 && b.len() > 5 {
        if a.starts_with(b) || b.starts_with(a) {
            return true;
        }
    }

    false
}

fn build_tree(headers: &[(usize, ClassifiedBlock, Option<String>)]) -> Vec<Section> {
    if headers.is_empty() {
        return vec![];
    }

    let mut root_sections: Vec<Section> = Vec::new();
    let mut stack: Vec<(u8, usize)> = Vec::new();

    for (page, block, numbering) in headers {
        let level = block.header_level.unwrap_or(5);
        let section = Section {
            title: block.text.trim().to_string(),
            level,
            page: *page,
            bbox: (block.bbox.x, block.bbox.y, block.bbox.width, block.bbox.height),
            children: Vec::new(),
            numbering: numbering.clone(),
        };

        while let Some(&(parent_level, _)) = stack.last() {
            if parent_level >= level {
                stack.pop();
            } else {
                break;
            }
        }

        if let Some(&(_, _parent_idx)) = stack.last() {
            insert_child(&mut root_sections, &stack, section);
            stack.push((level, 0));
        } else {
            let idx = root_sections.len();
            root_sections.push(section);
            stack.push((level, idx));
        }
    }

    root_sections
}

fn insert_child(sections: &mut Vec<Section>, stack: &[(u8, usize)], child: Section) {
    if stack.is_empty() {
        sections.push(child);
        return;
    }

    let (_, root_idx) = stack[0];
    if root_idx >= sections.len() {
        sections.push(child);
        return;
    }

    let mut current = &mut sections[root_idx];
    for &(_, _) in &stack[1..] {
        if current.children.is_empty() {
            current.children.push(child);
            return;
        }
        current = current.children.last_mut().unwrap();
    }
    current.children.push(child);
}

fn count_sections(sections: &[Section]) -> usize {
    sections.iter().map(|s| 1 + count_sections(&s.children)).sum()
}

fn max_section_depth(sections: &[Section], depth: u8) -> u8 {
    if sections.is_empty() {
        return depth;
    }
    sections.iter()
        .map(|s| max_section_depth(&s.children, depth + 1))
        .max()
        .unwrap_or(depth)
}

impl Section {
    pub fn to_flat_list(&self) -> Vec<(u8, String, usize)> {
        let mut result = vec![(self.level, self.title.clone(), self.page)];
        for child in &self.children {
            result.extend(child.to_flat_list());
        }
        result
    }
}

impl SectionTree {
    pub fn to_flat_list(&self) -> Vec<(u8, String, usize)> {
        let mut result = Vec::new();
        for section in &self.sections {
            result.extend(section.to_flat_list());
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_count_sections() {
        let sections = vec![
            Section {
                title: "Chapter 1".to_string(), level: 1, page: 0,
                bbox: (0.0, 0.0, 100.0, 20.0),
                children: vec![
                    Section {
                        title: "Section 1.1".to_string(), level: 2, page: 1,
                        bbox: (0.0, 0.0, 100.0, 20.0),
                        children: vec![],
                        numbering: Some("1.1".to_string()),
                    },
                    Section {
                        title: "Section 1.2".to_string(), level: 2, page: 2,
                        bbox: (0.0, 0.0, 100.0, 20.0),
                        children: vec![],
                        numbering: Some("1.2".to_string()),
                    },
                ],
                numbering: Some("1".to_string()),
            },
        ];
        assert_eq!(count_sections(&sections), 3);
        assert_eq!(max_section_depth(&sections, 0), 2);
    }

    #[test]
    fn test_flat_list() {
        let tree = SectionTree {
            sections: vec![
                Section {
                    title: "Intro".to_string(), level: 1, page: 0,
                    bbox: (0.0, 0.0, 100.0, 20.0),
                    children: vec![
                        Section {
                            title: "Background".to_string(), level: 2, page: 1,
                            bbox: (0.0, 0.0, 100.0, 20.0),
                            children: vec![],
                            numbering: None,
                        },
                    ],
                    numbering: None,
                },
            ],
            total_sections: 2,
            max_depth: 2,
        };
        let flat = tree.to_flat_list();
        assert_eq!(flat.len(), 2);
        assert_eq!(flat[0].1, "Intro");
        assert_eq!(flat[1].1, "Background");
    }

    #[test]
    fn test_parse_section_numbering() {
        assert_eq!(parse_section_numbering("1.2.3 Methods"), (Some("1.2.3".to_string()), Some(3)));
        assert_eq!(parse_section_numbering("1 Introduction"), (Some("1".to_string()), Some(1)));
        assert_eq!(parse_section_numbering("A.1 Appendix"), (Some("A.1".to_string()), Some(2)));
        assert_eq!(parse_section_numbering("(a) Sub-item"), (Some("(a)".to_string()), Some(3)));
        assert_eq!(parse_section_numbering("Introduction"), (None, None));
    }

    #[test]
    fn test_is_dotted_numbering() {
        assert!(is_dotted_numbering("1.2"));
        assert!(is_dotted_numbering("1.2.3"));
        assert!(is_dotted_numbering("A.1"));
        assert!(is_dotted_numbering("IV.3"));
        assert!(!is_dotted_numbering("1"));
        assert!(!is_dotted_numbering("hello.world"));
        assert!(!is_dotted_numbering(""));
    }

    #[test]
    fn test_false_positive_filter() {
        assert!(is_false_positive_header(""));
        assert!(is_false_positive_header("1"));
        assert!(is_false_positive_header("42"));
        assert!(is_false_positive_header("12-34"));
        assert!(!is_false_positive_header("1 Introduction"));
        assert!(!is_false_positive_header("Abstract"));
        assert!(!is_false_positive_header("References"));
    }

    #[test]
    fn test_titles_match() {
        assert!(titles_match("introduction", "introduction"));
        assert!(titles_match("introduction to the topic", "introduction to the topic of"));
        assert!(!titles_match("intro", "conclusion"));
    }

    #[test]
    fn test_normalize_for_matching() {
        assert_eq!(normalize_for_matching("1.2 Methods"), "methods");
        assert_eq!(normalize_for_matching("3 Results"), "results");
        assert_eq!(normalize_for_matching("Introduction"), "introduction");
    }

    // --- S04 Section Builder Tests ---

    #[test]
    fn test_partial_sentence_detection() {
        assert!(is_partial_sentence("and the analysis continues"));
        assert!(is_partial_sentence("including several key factors,"));
        assert!(is_partial_sentence("this is a very long text that goes on and on and on and on and exceeds the 120 character limit for what we consider a reasonable section header title length in documents"));
        assert!(!is_partial_sentence("Introduction"));
        assert!(!is_partial_sentence("1.2 System Architecture"));
    }

    #[test]
    fn test_citation_fragment_detection() {
        assert!(is_citation_fragment("4 ]. At their core"));
        assert!(is_citation_fragment("0.315"));
        assert!(is_citation_fragment("51-60."));
        assert!(is_citation_fragment("doi:10.1145/3447247"));
        assert!(is_citation_fragment("lower case body text"));
        assert!(!is_citation_fragment("1 Introduction"));
        assert!(!is_citation_fragment("Results and Discussion"));
    }

    #[test]
    fn test_flat_sections_basic() {
        use crate::extractors::block_classifier::NumberingAnalysis;
        use crate::extractors::block_classifier::NumberingType;

        let header = ClassifiedBlock {
            block_type: BlockType::Title,
            text: "1.2 Methods".to_string(),
            bbox: Rect::new(10.0, 100.0, 200.0, 18.0),
            font_size: 14.0,
            font_name: "Arial".to_string(),
            is_bold: true,
            confidence: 0.9,
            header_level: Some(2),
            header_validation: None,
        };
        let body1 = ClassifiedBlock {
            block_type: BlockType::Body,
            text: "We used standard methods.".to_string(),
            bbox: Rect::new(10.0, 130.0, 400.0, 12.0),
            font_size: 11.0,
            font_name: "Arial".to_string(),
            is_bold: false,
            confidence: 0.9,
            header_level: None,
            header_validation: None,
        };
        let body2 = ClassifiedBlock {
            block_type: BlockType::Body,
            text: "Data was collected over 3 months.".to_string(),
            bbox: Rect::new(10.0, 145.0, 400.0, 12.0),
            font_size: 11.0,
            font_name: "Arial".to_string(),
            is_bold: false,
            confidence: 0.9,
            header_level: None,
            header_validation: None,
        };

        let page_blocks = vec![(0, vec![header, body1, body2])];
        let sections = build_flat_sections(&page_blocks);

        assert_eq!(sections.len(), 1);
        assert_eq!(sections[0].title, "1.2 Methods");
        assert_eq!(sections[0].display_title, "Methods");
        assert_eq!(sections[0].section_number, "1.2");
        assert_eq!(sections[0].level, 2);
        assert!(sections[0].content.contains("We used standard methods."));
        assert!(sections[0].content.contains("Data was collected"));
        assert_eq!(sections[0].block_count, 3);
    }

    #[test]
    fn test_flat_sections_parent_assignment() {
        let h1 = ClassifiedBlock {
            block_type: BlockType::Title,
            text: "1 Introduction".to_string(),
            bbox: Rect::new(10.0, 100.0, 200.0, 18.0),
            font_size: 16.0,
            font_name: "Arial".to_string(),
            is_bold: true,
            confidence: 0.9,
            header_level: Some(1),
            header_validation: None,
        };
        let h2 = ClassifiedBlock {
            block_type: BlockType::Title,
            text: "1.1 Background".to_string(),
            bbox: Rect::new(10.0, 200.0, 200.0, 16.0),
            font_size: 14.0,
            font_name: "Arial".to_string(),
            is_bold: true,
            confidence: 0.9,
            header_level: Some(2),
            header_validation: None,
        };

        let page_blocks = vec![(0, vec![h1, h2])];
        let sections = build_flat_sections(&page_blocks);

        assert_eq!(sections.len(), 2);
        assert!(sections[0].parent_idx.is_none()); // level 1, no parent
        assert_eq!(sections[1].parent_idx, Some(0)); // level 2, parent is section 0
    }

    #[test]
    fn test_continuation_merge() {
        let h1 = ClassifiedBlock {
            block_type: BlockType::Title,
            text: "3 Results".to_string(),
            bbox: Rect::new(10.0, 100.0, 200.0, 18.0),
            font_size: 16.0,
            font_name: "Arial".to_string(),
            is_bold: true,
            confidence: 0.9,
            header_level: Some(1),
            header_validation: None,
        };
        let body = ClassifiedBlock {
            block_type: BlockType::Body,
            text: "First result paragraph.".to_string(),
            bbox: Rect::new(10.0, 130.0, 400.0, 12.0),
            font_size: 11.0,
            font_name: "Arial".to_string(),
            is_bold: false,
            confidence: 0.9,
            header_level: None,
            header_validation: None,
        };
        let cont = ClassifiedBlock {
            block_type: BlockType::Title,
            text: "3 Results (continued)".to_string(),
            bbox: Rect::new(10.0, 100.0, 200.0, 18.0),
            font_size: 16.0,
            font_name: "Arial".to_string(),
            is_bold: true,
            confidence: 0.9,
            header_level: Some(1),
            header_validation: None,
        };
        let body2 = ClassifiedBlock {
            block_type: BlockType::Body,
            text: "Second result paragraph.".to_string(),
            bbox: Rect::new(10.0, 130.0, 400.0, 12.0),
            font_size: 11.0,
            font_name: "Arial".to_string(),
            is_bold: false,
            confidence: 0.9,
            header_level: None,
            header_validation: None,
        };

        let page_blocks = vec![
            (0, vec![h1, body]),
            (1, vec![cont, body2]),
        ];
        let sections = build_flat_sections(&page_blocks);

        // "(continued)" section should be merged with the previous one
        assert_eq!(sections.len(), 1);
        assert!(sections[0].content.contains("First result"));
        assert!(sections[0].content.contains("Second result"));
        assert_eq!(sections[0].page_end, 1);
    }

    #[test]
    fn test_section_order_validation() {
        let sec1 = FlatSection {
            title: "Intro".to_string(),
            display_title: "Intro".to_string(),
            level: 1,
            section_number: "1".to_string(),
            page_start: 0,
            page_end: 0,
            bbox: Rect::new(10.0, 100.0, 200.0, 18.0),
            content: String::new(),
            block_count: 1,
            section_hash: String::new(),
            parent_idx: None,
            header_disposition: None,
        };
        let sec2 = FlatSection {
            title: "Methods".to_string(),
            display_title: "Methods".to_string(),
            level: 1,
            section_number: "2".to_string(),
            page_start: 1,
            page_end: 2,
            bbox: Rect::new(10.0, 50.0, 200.0, 18.0),
            content: String::new(),
            block_count: 1,
            section_hash: String::new(),
            parent_idx: None,
            header_disposition: None,
        };

        let results = validate_section_order(&[sec1, sec2], &[]);
        assert!(results.iter().all(|(_, ok)| *ok));
    }
}
