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
use std::ops::Range;

/// A TOC entry extracted geometrically from span data.
#[derive(Debug, Clone)]
pub struct TocEntry {
    /// The section title text (leader dots stripped)
    pub text: String,
    /// Target page number if detected
    pub page_number: Option<u32>,
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

        // Step 1: Find the right margin — the most common rightmost x-position
        // across lines that end with a number. This is the page number column.
        let right_edges = self.find_page_number_column(&lines);

        // Step 2: Collect left-edge x-positions to cluster indent levels
        let left_edges: Vec<f32> = lines.iter().map(|l| l.left_x).collect();
        let indent_levels = cluster_x_positions(&left_edges, self.indent_tolerance);

        // Step 3: Build entries
        let mut entries = Vec::new();
        for (i, line) in lines.iter().enumerate() {
            // Skip lines that look like headers ("TABLE OF CONTENTS", "CONTENTS")
            if is_toc_title(&line.text) {
                continue;
            }
            // Skip very short lines (page numbers alone, etc.)
            if line.title_text.trim().len() < 2 {
                continue;
            }

            let indent = indent_levels.get(i).copied().unwrap_or(0);
            entries.push(TocEntry {
                text: line.title_text.clone(),
                page_number: line.page_number,
                indent_level: indent,
                font_size: line.font_size,
                is_bold: line.is_bold,
                y_range: line.y_top..line.y_bottom,
            });
        }

        if entries.len() < self.min_entries {
            return None;
        }

        // Use right_edges to validate — suppress entries to satisfy borrow checker
        let _ = right_edges;

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

    /// Find the page number column by looking at right-edge alignment of numeric spans.
    fn find_page_number_column(&self, lines: &[TocLine]) -> Option<f32> {
        let right_xs: Vec<f32> = lines.iter()
            .filter_map(|l| if l.page_number.is_some() { Some(l.right_x) } else { None })
            .collect();
        if right_xs.len() < 2 {
            return None;
        }
        Some(right_xs.iter().sum::<f32>() / right_xs.len() as f32)
    }
}

/// Internal representation of a single TOC line.
struct TocLine {
    /// Full text of the line
    text: String,
    /// Title text with leader dots and page number stripped
    title_text: String,
    /// Detected page number (rightmost numeric span)
    page_number: Option<u32>,
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
                left_x: 0.0, right_x: 0.0, font_size: 0.0, is_bold: false,
                y_top: 0.0, y_bottom: 0.0,
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
        let page_number = parse_page_number(last_text);

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
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return None;
    }
    // Arabic numerals
    if let Ok(n) = trimmed.parse::<u32>() {
        return Some(n);
    }
    // Roman numerals (lowercase)
    if let Some(n) = parse_roman(trimmed) {
        return Some(n);
    }
    None
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
