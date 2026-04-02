//! Geometric Table of Contents extraction from known TOC pages.
//!
//! Given spans from pages already identified as TOC (via structure tree or VLM),
//! extracts entries using purely geometric signals — no regex.
//!
//! Signals used:
//! 1. **X-position clustering** — left edge of each line clustered to determine indent level
//! 2. **Right-aligned page numbers** — rightmost span on line, numeric, aligned with others
//! 3. **Font-size hierarchy** — larger/bolder = higher-level entry (secondary signal)
//! 4. **Leader gap detection** — large horizontal gap between title end and page number

use crate::layout::TextSpan;
use crate::utils::safe_float_cmp;
use serde::Serialize;
use std::ops::Range;

/// Classification of a TOC entry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub enum TocEntryType {
    /// Numbered section, chapter, or part
    Section,
    /// "Figure N: ..." entry
    Figure,
    /// "Table N: ..." entry
    Table,
    /// "Appendix A/B" or "Annex ..." entry
    Appendix,
    /// Front matter: abstract, preface, foreword, acknowledgments, glossary, etc.
    FrontMatter,
}

/// A TOC entry extracted geometrically from span data.
#[derive(Debug, Clone, Serialize)]
pub struct TocEntry {
    /// The section title text (leader dots stripped)
    pub text: String,
    /// Target page number as printed in the TOC (may be logical, not physical)
    pub page_number: Option<u32>,
    /// Whether the page number was roman numeral (signals front matter)
    pub is_roman: bool,
    /// Resolved physical page index (0-based), after PageLabels resolution
    pub physical_page: Option<usize>,
    /// Classification of this entry
    pub entry_type: TocEntryType,
    /// Indentation level (0 = top-level, derived from X-position clustering)
    pub indent_level: usize,
    /// Font size of the title text
    pub font_size: f32,
    /// Whether the title is bold
    pub is_bold: bool,
    /// Y-coordinate range on the source page
    pub y_range: Range<f32>,
}

/// Configuration for geometric TOC extraction.
#[derive(Debug, Clone)]
pub struct TocDetector {
    /// Vertical tolerance for grouping spans into lines (points)
    pub line_tolerance: f32,
    /// X-position tolerance for indent clustering (points)
    pub indent_tolerance: f32,
    /// Minimum entries to consider the page a valid TOC
    pub min_entries: usize,
    /// Right-margin tolerance for page number alignment (points)
    pub page_number_alignment_tolerance: f32,
}

impl Default for TocDetector {
    fn default() -> Self {
        Self {
            line_tolerance: 2.0,
            indent_tolerance: 8.0,
            min_entries: 3,
            page_number_alignment_tolerance: 15.0,
        }
    }
}

impl TocDetector {
    pub fn new() -> Self {
        Self::default()
    }

    /// Extract TOC entries from spans on a known TOC page.
    ///
    /// This assumes the page is already identified as containing a TOC
    /// (via structure tree, outline, or VLM classification).
    /// Returns None if fewer than `min_entries` are found.
    pub fn extract_from_spans(&self, spans: &[TextSpan]) -> Option<Vec<TocEntry>> {
        if spans.is_empty() {
            return None;
        }

        let lines = self.group_into_lines(spans);
        if lines.len() < self.min_entries {
            return None;
        }

        // Step 1: Filter out non-entry lines FIRST, then cluster
        let valid_lines: Vec<&TocLine> = lines.iter()
            .filter(|l| !is_toc_title(&l.text) && l.title_text.trim().len() >= 2)
            .collect();

        if valid_lines.len() < self.min_entries {
            return None;
        }

        // Step 2: Cluster indent levels on filtered lines only (fixes misindex bug)
        let left_edges: Vec<f32> = valid_lines.iter().map(|l| l.left_x).collect();
        let indent_levels = cluster_x_positions(&left_edges, self.indent_tolerance);

        // Step 3: Build entries
        let mut entries = Vec::with_capacity(valid_lines.len());
        for (i, line) in valid_lines.iter().enumerate() {
            let indent = indent_levels.get(i).copied().unwrap_or(0);
            let entry_type = classify_toc_entry(&line.title_text, line.is_roman);
            entries.push(TocEntry {
                text: line.title_text.clone(),
                page_number: line.page_number,
                is_roman: line.is_roman,
                physical_page: None, // resolved later via PageLabels
                entry_type,
                indent_level: indent,
                font_size: line.font_size,
                is_bold: line.is_bold,
                y_range: line.y_top..line.y_bottom,
            });
        }

        Some(entries)
    }

    /// Detect whether a page is likely a TOC page (for the VLM-less heuristic path).
    /// Returns a confidence score 0.0-1.0.
    pub fn score_toc_likelihood(&self, spans: &[TextSpan]) -> f32 {
        if spans.is_empty() {
            return 0.0;
        }

        let lines = self.group_into_lines(spans);
        if lines.len() < 3 {
            return 0.0;
        }

        let mut signals = 0.0f32;
        let mut total_weight = 0.0f32;

        // Signal 1: Many lines ending with a number (weight: 3)
        let lines_with_numbers = lines.iter().filter(|l| l.page_number.is_some()).count();
        let number_ratio = lines_with_numbers as f32 / lines.len() as f32;
        signals += number_ratio * 3.0;
        total_weight += 3.0;

        // Signal 2: Right-aligned page numbers (weight: 2)
        if lines_with_numbers >= 3 {
            let right_xs: Vec<f32> = lines.iter()
                .filter_map(|l| if l.page_number.is_some() { Some(l.right_x) } else { None })
                .collect();
            if !right_xs.is_empty() {
                let mean = right_xs.iter().sum::<f32>() / right_xs.len() as f32;
                let variance = right_xs.iter().map(|x| (x - mean).powi(2)).sum::<f32>() / right_xs.len() as f32;
                let alignment_score = if variance.sqrt() < self.page_number_alignment_tolerance { 1.0 } else { 0.3 };
                signals += alignment_score * 2.0;
            }
            total_weight += 2.0;
        }

        // Signal 3: Multiple indent levels (weight: 1)
        let left_edges: Vec<f32> = lines.iter().map(|l| l.left_x).collect();
        let indent_levels = cluster_x_positions(&left_edges, self.indent_tolerance);
        let unique_levels: std::collections::HashSet<usize> = indent_levels.iter().copied().collect();
        let indent_score = if unique_levels.len() >= 2 { 1.0 } else { 0.3 };
        signals += indent_score * 1.0;
        total_weight += 1.0;

        // Signal 4: Ascending page numbers (weight: 2)
        let page_nums: Vec<u32> = lines.iter().filter_map(|l| l.page_number).collect();
        if page_nums.len() >= 3 {
            let ascending_pairs = page_nums.windows(2).filter(|w| w[1] >= w[0]).count();
            let ascending_ratio = ascending_pairs as f32 / (page_nums.len() - 1) as f32;
            signals += ascending_ratio * 2.0;
        }
        total_weight += 2.0;

        if total_weight > 0.0 { signals / total_weight } else { 0.0 }
    }

    /// Group spans into logical lines based on Y-coordinate proximity.
    fn group_into_lines(&self, spans: &[TextSpan]) -> Vec<TocLine> {
        if spans.is_empty() {
            return Vec::new();
        }

        // Sort by Y (top-to-bottom in page coords)
        let mut sorted: Vec<&TextSpan> = spans.iter().collect();
        sorted.sort_by(|a, b| safe_float_cmp(a.bbox.top(), b.bbox.top()));

        let mut lines: Vec<Vec<&TextSpan>> = Vec::new();
        let mut current: Vec<&TextSpan> = vec![sorted[0]];

        for span in sorted.iter().skip(1) {
            let ref_y = current[0].bbox.top();
            if (span.bbox.top() - ref_y).abs() <= self.line_tolerance {
                current.push(span);
            } else {
                current.sort_by(|a, b| safe_float_cmp(a.bbox.left(), b.bbox.left()));
                lines.push(current);
                current = vec![span];
            }
        }
        if !current.is_empty() {
            current.sort_by(|a, b| safe_float_cmp(a.bbox.left(), b.bbox.left()));
            lines.push(current);
        }

        lines.iter().map(|line_spans| TocLine::from_spans(line_spans)).collect()
    }

}

/// A predicted section with its page span, derived from consecutive TOC entries.
#[derive(Debug, Clone, Serialize)]
pub struct SectionSpan {
    /// Section title from TOC
    pub title: String,
    /// Start page as physical page index (0-based)
    pub start_page: u32,
    /// End page as physical page index (0-based)
    pub end_page: u32,
    /// Indent level from TOC (0 = top-level)
    pub level: usize,
    /// Classification of this entry
    pub entry_type: TocEntryType,
}

/// Build an ordered section map from TOC entries.
///
/// Takes flat TOC entries (from geometric extraction or structure tree) and
/// computes page spans by looking at consecutive page numbers. Each section
/// runs from its stated page to the page before the next section starts.
///
/// `total_pages` is used to bound the last section's end page.
///
/// Returns entries in document order (ascending page number).
/// Entries without page numbers are skipped.
pub fn build_section_map(entries: &[TocEntry], total_pages: u32) -> Vec<SectionSpan> {
    // Use physical_page if resolved. For non-roman entries without physical_page,
    // treat page_number as physical (arabic pages usually are). For roman entries
    // without resolution, skip — we can't map them reliably.
    let mut with_pages: Vec<(&TocEntry, u32)> = entries.iter()
        .filter_map(|e| {
            if e.text.trim().is_empty() { return None; }
            let page = if let Some(p) = e.physical_page {
                p as u32
            } else if !e.is_roman {
                // Arabic page numbers are typically physical indices (1-based in TOC)
                // Subtract 1 to convert to 0-based, but clamp to 0
                e.page_number?.saturating_sub(1)
            } else {
                return None; // Roman numeral without PageLabels resolution — skip
            };
            if page >= total_pages { return None; } // bounds check
            Some((e, page))
        })
        .collect();
    with_pages.sort_by_key(|(_, page)| *page);

    if with_pages.is_empty() {
        return Vec::new();
    }

    let mut result = Vec::with_capacity(with_pages.len());
    for i in 0..with_pages.len() {
        let (entry, start) = with_pages[i];
        let end = if i + 1 < with_pages.len() {
            let next_start = with_pages[i + 1].1;
            if next_start > start { next_start - 1 } else { start }
        } else {
            total_pages.saturating_sub(1)
        };

        result.push(SectionSpan {
            title: entry.text.trim().to_string(),
            start_page: start,
            end_page: end,
            level: entry.indent_level,
            entry_type: entry.entry_type.clone(),
        });
    }

    result
}

/// Build a section map from outline items (bookmark tree).
///
/// Outline items already have titles and page destinations.
/// This converts them to the same `SectionSpan` format.
pub fn build_section_map_from_outline(
    items: &[(String, Option<u32>, usize)], // (title, page, depth)
    total_pages: u32,
) -> Vec<SectionSpan> {
    let entries: Vec<TocEntry> = items.iter().map(|(title, page, depth)| {
        let entry_type = classify_toc_entry(title, false);
        TocEntry {
            text: title.clone(),
            page_number: *page,
            is_roman: false,
            physical_page: page.map(|p| p as usize), // outline pages are already physical
            entry_type,
            indent_level: *depth,
            font_size: 0.0,
            is_bold: false,
            y_range: 0.0..0.0,
        }
    }).collect();
    build_section_map(&entries, total_pages)
}

/// Resolve logical page numbers to physical page indices using PageLabels.
///
/// Takes TOC entries with logical page numbers (e.g., roman "iv" = 4, arabic "1" = 1)
/// and a pre-computed label-to-physical mapping, then sets `physical_page` on each entry.
pub fn resolve_page_labels(entries: &mut [TocEntry], labels: &[(String, usize)]) {
    // Build reverse lookup: label string -> physical page index
    let label_map: std::collections::HashMap<String, usize> = labels.iter()
        .map(|(label, idx)| (label.to_lowercase(), *idx))
        .collect();

    for entry in entries.iter_mut() {
        if entry.physical_page.is_some() {
            continue; // already resolved
        }
        if let Some(page_num) = entry.page_number {
            // Try matching the page number as a label string
            let candidates = if entry.is_roman {
                // For roman numerals, try lowercase roman representation
                vec![to_roman_lower(page_num)]
            } else {
                vec![page_num.to_string()]
            };
            for candidate in &candidates {
                if let Some(&physical) = label_map.get(&candidate.to_lowercase()) {
                    entry.physical_page = Some(physical);
                    break;
                }
            }
        }
    }
}

fn to_roman_lower(n: u32) -> String {
    if n == 0 { return String::new(); }
    let values = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"),
                  (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
                  (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")];
    let mut result = String::new();
    let mut remaining = n;
    for &(val, sym) in &values {
        while remaining >= val {
            result.push_str(sym);
            remaining -= val;
        }
    }
    result
}

/// Internal representation of a single TOC line.
struct TocLine {
    /// Full text of the line
    text: String,
    /// Title text with leader dots and page number stripped
    title_text: String,
    /// Detected page number (rightmost numeric span)
    page_number: Option<u32>,
    /// Whether the page number was roman numeral
    is_roman: bool,
    /// Left edge of the leftmost non-leader span (for indent clustering)
    left_x: f32,
    /// Right edge of the rightmost span
    right_x: f32,
    /// Dominant font size on this line
    font_size: f32,
    /// Whether the dominant font is bold
    is_bold: bool,
    /// Y-coordinates
    y_top: f32,
    y_bottom: f32,
}

impl TocLine {
    fn from_spans(spans: &[&TextSpan]) -> Self {
        if spans.is_empty() {
            return Self {
                text: String::new(), title_text: String::new(), page_number: None,
                is_roman: false, left_x: 0.0, right_x: 0.0, font_size: 0.0,
                is_bold: false, y_top: 0.0, y_bottom: 0.0,
            };
        }

        let full_text: String = spans.iter().map(|s| s.text.as_str()).collect::<Vec<_>>().join(" ");
        let left_x = spans.first().map(|s| s.bbox.left()).unwrap_or(0.0);
        let right_x = spans.last().map(|s| s.bbox.right()).unwrap_or(0.0);
        let y_top = spans.iter().map(|s| s.bbox.top()).min_by(|a, b| safe_float_cmp(*a, *b)).unwrap_or(0.0);
        let y_bottom = spans.iter().map(|s| s.bbox.bottom()).max_by(|a, b| safe_float_cmp(*a, *b)).unwrap_or(0.0);

        // Find dominant font (most text by span count)
        let font_size = spans.iter().map(|s| s.font_size).max_by(|a, b| safe_float_cmp(*a, *b)).unwrap_or(12.0);
        let is_bold = spans.iter().any(|s| s.font_weight.is_bold());

        // Check if the rightmost span is a page number
        let last_span = spans.last().unwrap();
        let last_text = last_span.text.trim();
        let (page_number, is_roman) = parse_page_number_typed(last_text);

        // Build title text: everything except the page number and leader dots
        let title_text = if page_number.is_some() && spans.len() > 1 {
            // Take all spans except the last (page number), strip leaders
            let title_spans: Vec<&str> = spans[..spans.len()-1].iter()
                .map(|s| s.text.as_str())
                .collect();
            strip_leaders(&title_spans.join(" "))
        } else if page_number.is_some() {
            // Single span with embedded page number — try to split
            strip_leaders(&full_text.trim_end_matches(last_text).to_string())
        } else {
            strip_leaders(&full_text)
        };

        Self {
            text: full_text,
            title_text: title_text.trim().to_string(),
            page_number,
            is_roman,
            left_x,
            right_x,
            font_size,
            is_bold,
            y_top,
            y_bottom,
        }
    }
}

/// Parse a string as a page number — arabic or roman numerals.
fn parse_page_number(text: &str) -> Option<u32> {
    parse_page_number_typed(text).0
}

/// Parse page number, also returning whether it was roman numeral.
fn parse_page_number_typed(text: &str) -> (Option<u32>, bool) {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return (None, false);
    }
    // Arabic numerals first
    if let Ok(n) = trimmed.parse::<u32>() {
        return (Some(n), false);
    }
    // Roman numerals
    if let Some(n) = parse_roman(trimmed) {
        return (Some(n), true);
    }
    (None, false)
}

/// Classify a TOC entry by its title text.
pub fn classify_toc_entry(text: &str, is_roman_page: bool) -> TocEntryType {
    let lower = text.trim().to_lowercase();

    // Figure entries
    if lower.starts_with("figure ") || lower.starts_with("fig. ") || lower.starts_with("fig ") {
        return TocEntryType::Figure;
    }

    // Table entries (but not "Table of Contents")
    if (lower.starts_with("table ") && !lower.contains("contents"))
        || lower.starts_with("tab. ")
    {
        return TocEntryType::Table;
    }

    // Appendix / Annex
    if lower.starts_with("appendix ") || lower.starts_with("annex ") {
        return TocEntryType::Appendix;
    }

    // Front matter keywords
    const FRONT_MATTER: &[&str] = &[
        "abstract", "acknowledgment", "acknowledgement", "acknowledgments",
        "acknowledgements", "foreword", "preface", "executive summary",
        "list of figures", "list of tables", "list of acronyms",
        "list of abbreviations", "list of symbols", "glossary",
        "acronyms", "abbreviations", "revision history", "document history",
        "table of contents", "contents",
    ];
    if FRONT_MATTER.iter().any(|&fm| lower == fm || lower.starts_with(&format!("{} ", fm))) {
        return TocEntryType::FrontMatter;
    }

    // Roman numeral page number is a strong front-matter signal
    if is_roman_page {
        return TocEntryType::FrontMatter;
    }

    TocEntryType::Section
}

/// Parse a roman numeral string (i, ii, iii, iv, v, vi, vii, viii, ix, x, etc.)
fn parse_roman(text: &str) -> Option<u32> {
    let lower = text.to_lowercase();
    let mut total = 0u32;
    let mut prev = 0u32;
    for c in lower.chars().rev() {
        let val = match c {
            'i' => 1, 'v' => 5, 'x' => 10, 'l' => 50,
            'c' => 100, 'd' => 500, 'm' => 1000,
            _ => return None,
        };
        if val < prev { total -= val; } else { total += val; }
        prev = val;
    }
    if total > 0 { Some(total) } else { None }
}

/// Strip dot leaders and similar filler characters from text.
/// Uses character analysis, not regex.
fn strip_leaders(text: &str) -> String {
    let mut result = String::with_capacity(text.len());
    let mut in_leader = false;
    let mut leader_start = 0;

    for (i, c) in text.char_indices() {
        if is_leader_char(c) {
            if !in_leader {
                leader_start = i;
                in_leader = true;
            }
        } else {
            if in_leader {
                let leader_len = i - leader_start;
                // Only strip if it's a substantial run (3+ chars)
                if leader_len < 3 {
                    result.push_str(&text[leader_start..i]);
                }
                in_leader = false;
            }
            result.push(c);
        }
    }
    // Don't append trailing leader
    result
}

fn is_leader_char(c: char) -> bool {
    matches!(c, '.' | '·' | '•' | '…' | '‥' | '․' | '─' | '—' | '-' | '_')
}

/// Check if a line is the TOC title itself (e.g. "TABLE OF CONTENTS").
fn is_toc_title(text: &str) -> bool {
    let upper = text.trim().to_uppercase();
    upper == "TABLE OF CONTENTS"
        || upper == "CONTENTS"
        || upper == "TABLE DES MATIÈRES"
        || upper == "INDEX"
        || upper == "TOC"
}

/// Cluster a list of X-positions into indent levels.
/// Returns a vec of the same length mapping each position to its level (0-based).
fn cluster_x_positions(positions: &[f32], tolerance: f32) -> Vec<usize> {
    if positions.is_empty() {
        return Vec::new();
    }

    // Collect unique X positions (within tolerance)
    let mut centroids: Vec<f32> = Vec::new();
    for &x in positions {
        let found = centroids.iter().any(|&c| (c - x).abs() < tolerance);
        if !found {
            centroids.push(x);
        }
    }
    centroids.sort_by(|a, b| safe_float_cmp(*a, *b));

    // Map each position to its nearest centroid index
    positions.iter().map(|&x| {
        centroids.iter()
            .enumerate()
            .min_by(|(_, a), (_, b)| safe_float_cmp((*a - x).abs(), (*b - x).abs()))
            .map(|(i, _)| i)
            .unwrap_or(0)
    }).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geometry::Rect;
    use crate::layout::text_block::{FontWeight, Color};

    fn make_span(text: &str, x: f32, y: f32, width: f32, font_size: f32, bold: bool) -> TextSpan {
        TextSpan {
            text: text.to_string(),
            bbox: Rect::new(x, y, x + width, y + font_size),
            font_size,
            font_name: "TestFont".to_string(),
            font_weight: if bold { FontWeight::Bold } else { FontWeight::Normal },
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
    fn test_parse_page_number_arabic() {
        assert_eq!(parse_page_number("42"), Some(42));
        assert_eq!(parse_page_number(" 7 "), Some(7));
        assert_eq!(parse_page_number(""), None);
        assert_eq!(parse_page_number("abc"), None);
    }

    #[test]
    fn test_parse_page_number_roman() {
        assert_eq!(parse_page_number("iv"), Some(4));
        assert_eq!(parse_page_number("ix"), Some(9));
        assert_eq!(parse_page_number("xii"), Some(12));
        assert_eq!(parse_page_number("III"), Some(3));
    }

    #[test]
    fn test_strip_leaders() {
        assert_eq!(strip_leaders("Introduction..........."), "Introduction");
        assert_eq!(strip_leaders("Chapter 1"), "Chapter 1");
        assert_eq!(strip_leaders("1.1 Methods"), "1.1 Methods"); // dots < 3 consecutive, preserved
        assert_eq!(strip_leaders("Results ─────"), "Results ");
    }

    #[test]
    fn test_cluster_x_positions() {
        let positions = vec![72.0, 72.5, 90.0, 90.2, 108.0, 72.1];
        let levels = cluster_x_positions(&positions, 8.0);
        assert_eq!(levels[0], 0); // 72.0
        assert_eq!(levels[1], 0); // 72.5
        assert_eq!(levels[2], 1); // 90.0
        assert_eq!(levels[3], 1); // 90.2
        assert_eq!(levels[4], 2); // 108.0
        assert_eq!(levels[5], 0); // 72.1
    }

    #[test]
    fn test_is_toc_title() {
        assert!(is_toc_title("TABLE OF CONTENTS"));
        assert!(is_toc_title("  Contents  "));
        assert!(!is_toc_title("Introduction"));
    }

    #[test]
    fn test_classify_toc_entry() {
        assert_eq!(classify_toc_entry("1. Introduction", false), TocEntryType::Section);
        assert_eq!(classify_toc_entry("Figure 1: System Overview", false), TocEntryType::Figure);
        assert_eq!(classify_toc_entry("Fig. 3 Architecture", false), TocEntryType::Figure);
        assert_eq!(classify_toc_entry("Table 2: Results", false), TocEntryType::Table);
        assert_eq!(classify_toc_entry("Appendix A: Glossary", false), TocEntryType::Appendix);
        assert_eq!(classify_toc_entry("Annex B: Test Data", false), TocEntryType::Appendix);
        assert_eq!(classify_toc_entry("Abstract", false), TocEntryType::FrontMatter);
        assert_eq!(classify_toc_entry("List of Figures", false), TocEntryType::FrontMatter);
        assert_eq!(classify_toc_entry("Glossary", false), TocEntryType::FrontMatter);
        assert_eq!(classify_toc_entry("Executive Summary", false), TocEntryType::FrontMatter);
        // Roman page number signals front matter
        assert_eq!(classify_toc_entry("Some Section", true), TocEntryType::FrontMatter);
        assert_eq!(classify_toc_entry("Some Section", false), TocEntryType::Section);
    }

    #[test]
    fn test_parse_page_number_typed() {
        assert_eq!(parse_page_number_typed("42"), (Some(42), false));
        assert_eq!(parse_page_number_typed("iv"), (Some(4), true));
        assert_eq!(parse_page_number_typed("XII"), (Some(12), true));
        assert_eq!(parse_page_number_typed("abc"), (None, false));
    }

    #[test]
    fn test_extract_simple_toc() {
        let detector = TocDetector::new();
        let spans = vec![
            make_span("Chapter 1: Introduction", 72.0, 100.0, 200.0, 12.0, true),
            make_span("1", 500.0, 100.0, 10.0, 12.0, false),
            make_span("  1.1 Background", 90.0, 120.0, 150.0, 11.0, false),
            make_span("5", 500.0, 120.0, 10.0, 11.0, false),
            make_span("  1.2 Methods", 90.0, 140.0, 120.0, 11.0, false),
            make_span("12", 500.0, 140.0, 15.0, 11.0, false),
            make_span("Chapter 2: Results", 72.0, 160.0, 180.0, 12.0, true),
            make_span("25", 500.0, 160.0, 15.0, 12.0, false),
        ];

        let entries = detector.extract_from_spans(&spans);
        assert!(entries.is_some());
        let entries = entries.unwrap();
        assert_eq!(entries.len(), 4);

        assert_eq!(entries[0].text, "Chapter 1: Introduction");
        assert_eq!(entries[0].page_number, Some(1));
        assert_eq!(entries[0].indent_level, 0);
        assert_eq!(entries[0].entry_type, TocEntryType::Section);
        assert!(!entries[0].is_roman);

        assert_eq!(entries[1].page_number, Some(5));
        assert_eq!(entries[1].indent_level, 1); // indented

        assert_eq!(entries[3].text, "Chapter 2: Results");
        assert_eq!(entries[3].page_number, Some(25));
        assert_eq!(entries[3].indent_level, 0);
    }

    #[test]
    fn test_score_toc_likelihood() {
        let detector = TocDetector::new();
        let spans = vec![
            make_span("Introduction", 72.0, 100.0, 200.0, 12.0, false),
            make_span("1", 500.0, 100.0, 10.0, 12.0, false),
            make_span("Background", 72.0, 120.0, 150.0, 12.0, false),
            make_span("5", 500.0, 120.0, 10.0, 12.0, false),
            make_span("Methods", 72.0, 140.0, 120.0, 12.0, false),
            make_span("12", 500.0, 140.0, 15.0, 12.0, false),
            make_span("Results", 72.0, 160.0, 120.0, 12.0, false),
            make_span("25", 500.0, 160.0, 15.0, 12.0, false),
        ];
        let score = detector.score_toc_likelihood(&spans);
        assert!(score > 0.6, "TOC-like page should score high, got {}", score);
    }
}
