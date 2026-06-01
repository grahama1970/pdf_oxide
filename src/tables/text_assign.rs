//! Text-to-cell assignment with character-level splitting.
//!
//! Implements Camelot-style text assignment:
//! 1. Find row by vertical midpoint
//! 2. If character positions available, split text at column boundaries
//! 3. Otherwise, assign whole element to column with maximum overlap

use crate::tables::types::{BBox, CharPosition, ExtractConfig, Flavor, Table, TextElement};

const COLUMN_EDGE_TOLERANCE_PT: f64 = 2.0;

const WATERMARK_PHRASES: [&str; 3] = [
    "This publication is available",
    "https://doi.org",
    "free of charge from",
];

/// Assign text elements to table cells with optional character-level splitting.
///
/// When `TextElement.chars` is present, splits text at column boundaries
/// (like Camelot's `split_textline()` using LTChar objects).
///
/// Returns a list of assignment errors (0.0 = perfect fit, >0 = spillover).
pub fn assign_text_to_cells(table: &mut Table, elements: &[TextElement]) -> Vec<f64> {
    let mut errors = Vec::with_capacity(elements.len());

    for elem in elements {
        let text = elem.text.trim();
        if text.is_empty() {
            continue;
        }
        if is_watermark_text(text) {
            continue;
        }

        let y_mid = elem.y_mid();

        // Find row: y_mid must be between row.y0 (top) and row.y1 (bottom)
        let row_idx = match table
            .rows
            .iter()
            .position(|&(ry0, ry1)| y_mid >= ry0 && y_mid <= ry1)
        {
            Some(r) => r,
            None => continue,
        };

        // If character positions available, split across columns
        if let Some(ref chars) = elem.chars {
            let split_errors = assign_with_char_splitting(table, elem, chars, row_idx);
            errors.extend(split_errors);
        } else {
            // Fallback: assign whole element to best-matching column
            if let Some((col_idx, error)) = assign_whole_element(table, elem, row_idx) {
                errors.push(error);
                append_to_cell(table, row_idx, col_idx, text);
            }
        }
    }

    normalize_merged_status_cells(table);
    errors
}

/// Split text element across columns using character positions.
///
/// For each column boundary, finds characters whose center falls within
/// that column and assigns them to the corresponding cell.
fn assign_with_char_splitting(
    table: &mut Table,
    elem: &TextElement,
    chars: &[CharPosition],
    row_idx: usize,
) -> Vec<f64> {
    let mut errors = Vec::new();

    // Group characters by column
    let mut col_chars: Vec<Vec<&CharPosition>> = vec![Vec::new(); table.cols.len()];

    for ch in chars {
        if let Some(col_idx) = column_for_char(table, ch) {
            col_chars[col_idx].push(ch);
        }
    }
    merge_edge_fragments(table, &mut col_chars);

    let nonempty_cols: Vec<usize> = col_chars
        .iter()
        .enumerate()
        .filter_map(|(col_idx, chars)| (!chars.is_empty()).then_some(col_idx))
        .collect();
    if nonempty_cols.len() == 1 {
        let col_idx = nonempty_cols[0];
        let char_text: String = col_chars[col_idx].iter().map(|c| c.char).collect();
        let elem_text = elem.text.trim();
        if normalized_text(&char_text) != normalized_text(elem_text)
            && !is_watermark_text(elem_text)
        {
            let error = assignment_error(elem, table, row_idx, col_idx);
            append_to_cell(table, row_idx, col_idx, elem_text);
            return vec![error];
        }
    }
    if nonempty_cols.len() > 1 {
        let elem_text = elem.text.trim();
        if is_merged_status_text(elem_text) {
            let col_idx = nonempty_cols[0];
            let error = assignment_error(elem, table, row_idx, col_idx);
            append_to_cell(table, row_idx, col_idx, elem_text);
            return vec![error];
        }
        if let Some(col_idx) = status_col_for_row(table, row_idx) {
            let error = assignment_error(elem, table, row_idx, col_idx);
            append_text_fragment(&mut table.cells[row_idx][col_idx].text, elem_text);
            return vec![error];
        }
    }

    // Assign text to each column that has characters
    for (col_idx, chars_in_col) in col_chars.iter().enumerate() {
        if chars_in_col.is_empty() {
            continue;
        }

        // Build text from characters in this column
        let text: String = chars_in_col.iter().map(|c| c.char).collect();
        let text = text.trim();
        if text.is_empty() {
            continue;
        }

        // Compute error based on character bounds vs cell bounds
        let char_x0 = chars_in_col
            .iter()
            .map(|c| c.x0)
            .fold(f64::INFINITY, f64::min);
        let char_x1 = chars_in_col
            .iter()
            .map(|c| c.x1)
            .fold(f64::NEG_INFINITY, f64::max);

        let pseudo_elem = TextElement {
            text: text.to_string(),
            x0: char_x0,
            y0: elem.y0,
            x1: char_x1,
            y1: elem.y1,
            font_size: elem.font_size,
            is_bold: elem.is_bold,
            chars: None,
        };

        let error = assignment_error(&pseudo_elem, table, row_idx, col_idx);
        errors.push(error);

        append_to_cell(table, row_idx, col_idx, text);
    }

    errors
}

/// Assign whole element to column with maximum overlap (fallback mode).
fn assign_whole_element(table: &Table, elem: &TextElement, row_idx: usize) -> Option<(usize, f64)> {
    let mut best_col: Option<usize> = None;
    let mut best_ratio: f64 = 0.0;

    for (c, &(cx0, cx1)) in table.cols.iter().enumerate() {
        if cx0 <= elem.x1 && cx1 >= elem.x0 {
            let left = elem.x0.max(cx0);
            let right = elem.x1.min(cx1);
            let col_width = (cx1 - cx0).abs();
            if col_width > 0.0 {
                let ratio = (right - left).abs() / col_width;
                if ratio > best_ratio {
                    best_ratio = ratio;
                    best_col = Some(c);
                }
            }
        }
    }

    best_col.map(|col_idx| {
        let error = assignment_error(elem, table, row_idx, col_idx);
        (col_idx, error)
    })
}

/// Append text to a cell, joining with newline if cell already has content.
fn append_to_cell(table: &mut Table, row: usize, col: usize, text: &str) {
    let cell = &mut table.cells[row][col];
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return;
    }
    let existing_lines: Vec<String> = cell.text.lines().map(|line| line.to_string()).collect();
    let mut replaced = false;
    let updated_lines: Vec<String> = existing_lines
        .iter()
        .map(|line| {
            if should_replace_duplicate_fragment(line.trim(), trimmed) {
                replaced = true;
                trimmed.to_string()
            } else {
                line.clone()
            }
        })
        .collect();
    if replaced {
        cell.text = updated_lines.join("\n");
        return;
    }
    if cell
        .text
        .lines()
        .any(|line| is_duplicate_cell_line(line.trim(), trimmed))
    {
        return;
    }
    if cell.text.is_empty() {
        cell.text = trimmed.to_string();
    } else {
        cell.text.push('\n');
        cell.text.push_str(trimmed);
    }
}

fn column_for_char(table: &Table, ch: &CharPosition) -> Option<usize> {
    let x_mid = ch.x_mid();

    for (c, &(cx0, cx1)) in table.cols.iter().enumerate() {
        if x_mid >= cx0 && x_mid <= cx1 {
            return Some(c);
        }
    }

    let mut nearest: Option<(usize, f64)> = None;
    for (c, &(cx0, cx1)) in table.cols.iter().enumerate() {
        let distance = if x_mid < cx0 {
            cx0 - x_mid
        } else if x_mid > cx1 {
            x_mid - cx1
        } else {
            0.0
        };
        if distance <= COLUMN_EDGE_TOLERANCE_PT && nearest.map_or(true, |(_, best)| distance < best)
        {
            nearest = Some((c, distance));
        }
    }

    nearest.map(|(c, _)| c)
}

fn merge_edge_fragments<'a>(table: &Table, col_chars: &mut [Vec<&'a CharPosition>]) {
    if table.cols.len() < 2 {
        return;
    }

    for col_idx in 0..table.cols.len() - 1 {
        if col_chars[col_idx].is_empty() || col_chars[col_idx + 1].is_empty() {
            continue;
        }
        if col_chars[col_idx].len() > 2 {
            continue;
        }

        let next_left = table.cols[col_idx + 1].0;
        let near_next_left = col_chars[col_idx]
            .iter()
            .all(|ch| (next_left - ch.x_mid()).abs() <= COLUMN_EDGE_TOLERANCE_PT);
        if near_next_left {
            let mut moved = std::mem::take(&mut col_chars[col_idx]);
            moved.append(&mut col_chars[col_idx + 1]);
            col_chars[col_idx + 1] = moved;
        }
    }
}

fn normalized_text(text: &str) -> String {
    text.chars().filter(|ch| !ch.is_whitespace()).collect()
}

fn is_watermark_text(text: &str) -> bool {
    WATERMARK_PHRASES.iter().any(|phrase| text.contains(phrase))
}

fn status_col_for_row(table: &Table, row: usize) -> Option<usize> {
    table.cells[row]
        .iter()
        .enumerate()
        .find_map(|(idx, cell)| is_merged_status_text(cell.text.trim()).then_some(idx))
}

fn is_merged_status_text(text: &str) -> bool {
    let trimmed = text.trim();
    trimmed.starts_with("W: Incorporated into ")
        || trimmed.starts_with("W: Moved to ")
        || trimmed.starts_with("W: Withdrawn")
}

fn normalize_merged_status_cells(table: &mut Table) {
    for row in &mut table.cells {
        let Some(status_col) = row
            .iter()
            .enumerate()
            .find_map(|(idx, cell)| is_merged_status_text(cell.text.trim()).then_some(idx))
        else {
            continue;
        };

        for col_idx in status_col + 1..row.len() {
            let continuation = row[col_idx].text.trim().to_string();
            if continuation.is_empty() || is_assurance_marker(&continuation) {
                continue;
            }
            append_text_fragment(&mut row[status_col].text, &continuation);
            row[col_idx].text.clear();
        }
    }
}

fn append_text_fragment(text: &mut String, fragment: &str) {
    if text.is_empty() {
        *text = fragment.to_string();
        return;
    }
    if text.ends_with('-') {
        text.push_str(fragment);
    } else {
        text.push(' ');
        text.push_str(fragment);
    }
}

fn is_assurance_marker(text: &str) -> bool {
    matches!(text.trim(), "√" | "O" | "S" | "O/S")
}

fn is_duplicate_cell_line(existing: &str, new: &str) -> bool {
    if existing == new {
        return true;
    }

    let existing_norm = normalized_text(existing);
    let new_norm = normalized_text(new);
    if existing_norm.is_empty() || new_norm.is_empty() {
        return false;
    }
    if is_known_distinct_header_fragment(&new_norm) {
        return false;
    }
    if !is_suspicious_duplicate_fragment(existing, new) {
        return false;
    }

    let new_len = new_norm.chars().count();
    let existing_len = existing_norm.chars().count();
    if existing_len < new_len {
        return false;
    }

    let lcs = lcs_len(&existing_norm, &new_norm);
    if new_len < 6 {
        return new_len >= 3 && existing_len >= new_len * 2 && lcs == new_len;
    }
    if new_len * 2 >= existing_len && lcs * 4 >= new_len * 3 {
        return true;
    }
    existing_len >= new_len + 10 && lcs * 10 >= new_len * 9
}

fn should_replace_duplicate_fragment(existing: &str, replacement: &str) -> bool {
    let existing_len = normalized_text(existing).chars().count();
    let replacement_len = normalized_text(replacement).chars().count();
    if replacement_len <= existing_len {
        return false;
    }
    if existing_len < 6 {
        return is_duplicate_cell_line(replacement, existing);
    }
    existing_len * 5 >= replacement_len * 3 && is_duplicate_cell_line(replacement, existing)
}

fn is_known_distinct_header_fragment(norm: &str) -> bool {
    matches!(norm, "CONTROLNAME")
}

fn is_suspicious_duplicate_fragment(existing: &str, new: &str) -> bool {
    let existing_words = normalized_words(existing);
    let new_words = normalized_words(new);
    if existing_words.is_empty() || new_words.is_empty() {
        return false;
    }

    new_words.iter().enumerate().any(|(idx, new_word)| {
        let has_exact_word = existing_words
            .iter()
            .any(|existing_word| existing_word == new_word);
        if has_exact_word {
            return false;
        }
        if is_trailing_acronym(&new_words, idx) {
            return false;
        }
        if is_common_continuation_word(new_word) {
            return false;
        }
        if new_word.chars().count() < 3 {
            return true;
        }
        existing_words
            .iter()
            .any(|existing_word| is_suspicious_word_fragment(existing_word, new_word))
    })
}

fn is_trailing_acronym(words: &[String], idx: usize) -> bool {
    if idx + 1 != words.len() || idx == 0 {
        return false;
    }
    let acronym = &words[idx];
    let acronym_len = acronym.chars().count();
    if acronym_len < 2 || acronym_len > idx {
        return false;
    }
    let start = idx - acronym_len;
    let candidate: String = words[start..idx]
        .iter()
        .filter_map(|word| word.chars().next())
        .collect();
    candidate == *acronym
}

fn is_common_continuation_word(word: &str) -> bool {
    matches!(word, "TO" | "AND" | "OR" | "AS" | "ANY" | "ITS")
}

fn is_suspicious_word_fragment(existing_word: &str, new_word: &str) -> bool {
    let new_len = new_word.chars().count();
    let existing_len = existing_word.chars().count();
    if existing_len <= new_len {
        return false;
    }
    if existing_word.strip_suffix('S') == Some(new_word) {
        return false;
    }
    if existing_word.starts_with(new_word) || existing_word.contains(new_word) {
        return true;
    }
    if new_len < 4 {
        return false;
    }
    let same_first = existing_word.chars().next() == new_word.chars().next();
    same_first && lcs_len(existing_word, new_word) * 5 >= new_len * 4
}

fn normalized_words(norm: &str) -> Vec<String> {
    norm.split(|ch: char| !ch.is_ascii_alphanumeric())
        .filter(|word| !word.is_empty())
        .map(|word| word.to_ascii_uppercase())
        .collect()
}

fn lcs_len(a: &str, b: &str) -> usize {
    let b_chars: Vec<char> = b.chars().collect();
    let mut prev = vec![0; b_chars.len() + 1];
    let mut curr = vec![0; b_chars.len() + 1];

    for a_ch in a.chars() {
        for (idx, b_ch) in b_chars.iter().enumerate() {
            curr[idx + 1] = if a_ch == *b_ch {
                prev[idx] + 1
            } else {
                curr[idx].max(prev[idx + 1])
            };
        }
        std::mem::swap(&mut prev, &mut curr);
        curr.fill(0);
    }

    prev[b_chars.len()]
}

/// Calculate how much a text element spills outside its assigned cell.
fn assignment_error(elem: &TextElement, table: &Table, row: usize, col: usize) -> f64 {
    let (ry0, ry1) = table.rows[row];
    let (cx0, cx1) = table.cols[col];

    let y_top_spill = (ry0 - elem.y0).max(0.0);
    let y_bot_spill = (elem.y1 - ry1).max(0.0);
    let x_left_spill = (cx0 - elem.x0).max(0.0);
    let x_right_spill = (elem.x1 - cx1).max(0.0);

    let w = elem.width().max(1.0);
    let h = elem.height().max(1.0);
    let area = w * h;

    let spill = (w * (y_top_spill + y_bot_spill)) + (h * (x_left_spill + x_right_spill));
    spill / area
}

/// Compute overall accuracy from assignment errors.
pub fn compute_accuracy(errors: &[f64]) -> f64 {
    if errors.is_empty() {
        return 100.0;
    }
    let mean_error: f64 = errors.iter().sum::<f64>() / errors.len() as f64;
    100.0 * (1.0 - mean_error).max(0.0)
}

#[derive(Debug, Clone)]
struct DefinitionPair {
    term: String,
    definition: String,
    bbox: BBox,
}

/// Extract a borderless two-column definition list as a single shared table.
pub fn extract_definition_list_table(
    elements: &[TextElement],
    page_width: f64,
    _page_height: f64,
    config: &ExtractConfig,
) -> Option<Table> {
    let x_mid = page_width * config.definition_list_column_ratio;
    let row_tol = config.definition_list_row_tol.max(0.5);

    let mut filtered: Vec<&TextElement> = elements
        .iter()
        .filter(|elem| should_keep_for_definition_list(elem, page_width))
        .collect();
    filtered.sort_by(|a, b| {
        a.y0.partial_cmp(&b.y0)
            .unwrap()
            .then_with(|| a.x0.partial_cmp(&b.x0).unwrap())
    });

    let rows = group_definition_rows(&filtered, row_tol);
    let pairs = collect_definition_pairs(&rows, x_mid)?;

    let mut left_x0 = f64::INFINITY;
    let mut left_x1 = f64::NEG_INFINITY;
    let mut right_x0 = f64::INFINITY;
    let mut right_x1 = f64::NEG_INFINITY;
    let mut table_bbox: Option<BBox> = None;

    for pair in &pairs {
        table_bbox = Some(match table_bbox {
            Some(bbox) => bbox.union(&pair.bbox),
            None => pair.bbox,
        });
    }

    for row in &rows {
        for elem in row {
            if elem.x0 < x_mid {
                left_x0 = left_x0.min(elem.x0);
                left_x1 = left_x1.max(elem.x1.min(x_mid));
            } else {
                right_x0 = right_x0.min(elem.x0.max(x_mid));
                right_x1 = right_x1.max(elem.x1);
            }
        }
    }

    let bbox = table_bbox?;
    let left_col = (
        if left_x0.is_finite() {
            left_x0
        } else {
            bbox.x0
        },
        if left_x1.is_finite() && left_x1 > left_x0 {
            left_x1
        } else {
            x_mid.max(bbox.x0)
        },
    );
    let right_col = (
        if right_x0.is_finite() && right_x0 > left_col.1 {
            right_x0
        } else {
            left_col.1
        },
        if right_x1.is_finite() {
            right_x1
        } else {
            bbox.x1
        },
    );

    let row_bounds: Vec<(f64, f64)> = pairs
        .iter()
        .map(|pair| (pair.bbox.y0, pair.bbox.y1.max(pair.bbox.y0 + 1.0)))
        .collect();
    let mut table = Table::new(vec![left_col, right_col], row_bounds, Flavor::Stream);
    for (row_idx, pair) in pairs.iter().enumerate() {
        table.cells[row_idx][0].text = pair.term.clone();
        table.cells[row_idx][1].text = pair.definition.clone();
    }
    table.set_border();
    table.accuracy = 100.0;
    table.compute_whitespace();
    Some(table)
}

fn should_keep_for_definition_list(elem: &TextElement, page_width: f64) -> bool {
    let text = elem.text.trim();
    if text.is_empty() {
        return false;
    }
    if WATERMARK_PHRASES.iter().any(|phrase| text.contains(phrase)) {
        return false;
    }

    !(elem.x1 <= page_width * 0.05 && text.chars().count() <= 5)
}

fn group_definition_rows<'a>(
    elements: &'a [&'a TextElement],
    row_tol: f64,
) -> Vec<Vec<&'a TextElement>> {
    let mut rows: Vec<Vec<&TextElement>> = Vec::new();
    let mut current_row: Vec<&TextElement> = Vec::new();
    let mut current_y = None;

    for &elem in elements {
        match current_y {
            None => {
                current_y = Some(elem.y0);
                current_row.push(elem);
            },
            Some(y0) if (elem.y0 - y0).abs() <= row_tol => {
                current_row.push(elem);
            },
            Some(_) => {
                current_row.sort_by(|a, b| a.x0.partial_cmp(&b.x0).unwrap());
                rows.push(current_row);
                current_row = vec![elem];
                current_y = Some(elem.y0);
            },
        }
    }

    if !current_row.is_empty() {
        current_row.sort_by(|a, b| a.x0.partial_cmp(&b.x0).unwrap());
        rows.push(current_row);
    }

    rows
}

fn collect_definition_pairs(rows: &[Vec<&TextElement>], x_mid: f64) -> Option<Vec<DefinitionPair>> {
    let mut pairs = Vec::new();
    let mut current_term: Option<String> = None;
    let mut current_definition: Vec<String> = Vec::new();
    let mut current_bbox: Option<BBox> = None;

    for row in rows {
        let (left, right): (Vec<_>, Vec<_>) = row.iter().copied().partition(|elem| elem.x0 < x_mid);
        let left_text = join_row_text(&left);
        let right_text = join_row_text(&right);
        let row_bbox = union_row_bbox(row);

        if current_term.is_some() && is_citation_row(&left_text, &right_text) {
            push_definition_part(
                &mut current_definition,
                citation_payload(&left_text, &right_text),
            );
            current_bbox = Some(merge_bbox(current_bbox, row_bbox));
            continue;
        }

        if !left_text.is_empty() && starts_new_definition_term(&left, &left_text) {
            flush_pair(
                &mut pairs,
                current_term.take(),
                &mut current_definition,
                current_bbox.take(),
            );
            current_term = Some(left_text);
            current_bbox = Some(row_bbox);
            if !right_text.is_empty() {
                push_definition_part(&mut current_definition, right_text);
            }
            continue;
        }

        if current_term.is_some() {
            if !right_text.is_empty() {
                push_definition_part(&mut current_definition, right_text);
            }
            if !left_text.is_empty() {
                push_definition_part(&mut current_definition, left_text);
            }
            current_bbox = Some(merge_bbox(current_bbox, row_bbox));
        }
    }

    flush_pair(&mut pairs, current_term.take(), &mut current_definition, current_bbox.take());

    if pairs.is_empty() {
        None
    } else {
        Some(pairs)
    }
}

fn starts_new_definition_term(left: &[&TextElement], left_text: &str) -> bool {
    let short_term = left_text.chars().count() < 40;
    left.iter().any(|elem| elem.is_bold) || short_term
}

fn is_citation_row(left_text: &str, right_text: &str) -> bool {
    if left_text.is_empty() {
        return false;
    }
    if !is_bracket_citation(left_text) {
        return false;
    }
    right_text.is_empty() || !right_text.contains('|')
}

fn citation_payload(left_text: &str, right_text: &str) -> String {
    if right_text.is_empty() {
        left_text.to_string()
    } else {
        format!("{left_text} {right_text}")
    }
}

fn is_bracket_citation(text: &str) -> bool {
    let trimmed = text.trim();
    trimmed.starts_with('[') && trimmed.ends_with(']') && trimmed.len() >= 3
}

fn join_row_text(elements: &[&TextElement]) -> String {
    elements
        .iter()
        .map(|elem| elem.text.trim())
        .filter(|text| !text.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

fn union_row_bbox(row: &[&TextElement]) -> BBox {
    row.iter().fold(
        BBox::new(f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY),
        |bbox, elem| {
            BBox::new(
                bbox.x0.min(elem.x0),
                bbox.y0.min(elem.y0),
                bbox.x1.max(elem.x1),
                bbox.y1.max(elem.y1),
            )
        },
    )
}

fn merge_bbox(existing: Option<BBox>, next: BBox) -> BBox {
    match existing {
        Some(bbox) => bbox.union(&next),
        None => next,
    }
}

fn push_definition_part(parts: &mut Vec<String>, text: impl Into<String>) {
    let text = text.into();
    let trimmed = text.trim();
    if !trimmed.is_empty() {
        parts.push(trimmed.to_string());
    }
}

fn flush_pair(
    pairs: &mut Vec<DefinitionPair>,
    term: Option<String>,
    definition_parts: &mut Vec<String>,
    bbox: Option<BBox>,
) {
    let Some(term) = term else {
        definition_parts.clear();
        return;
    };
    let definition = definition_parts.join(" ").trim().to_string();
    definition_parts.clear();
    if definition.is_empty() {
        return;
    }
    if let Some(bbox) = bbox {
        pairs.push(DefinitionPair {
            term,
            definition,
            bbox,
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tables::types::Flavor;

    fn make_table() -> Table {
        Table::new(
            vec![(0.0, 100.0), (100.0, 200.0)],
            vec![(0.0, 50.0), (50.0, 100.0)],
            Flavor::Stream,
        )
    }

    #[test]
    fn assign_centered_element() {
        let mut table = make_table();
        let elements = vec![TextElement {
            text: "hello".into(),
            x0: 10.0,
            y0: 10.0,
            x1: 80.0,
            y1: 30.0,
            font_size: 12.0,
            chars: None,
            is_bold: false,
        }];
        let errors = assign_text_to_cells(&mut table, &elements);
        assert_eq!(table.cells[0][0].text, "hello");
        assert!(errors[0] < 0.01);
    }

    #[test]
    fn assign_to_second_column() {
        let mut table = make_table();
        let elements = vec![TextElement {
            text: "world".into(),
            x0: 120.0,
            y0: 60.0,
            x1: 180.0,
            y1: 80.0,
            font_size: 12.0,
            chars: None,
            is_bold: false,
        }];
        let errors = assign_text_to_cells(&mut table, &elements);
        assert_eq!(table.cells[1][1].text, "world");
        assert!(errors[0] < 0.01);
    }

    #[test]
    fn skip_empty_text() {
        let mut table = make_table();
        let elements = vec![TextElement {
            text: "   ".into(),
            x0: 10.0,
            y0: 10.0,
            x1: 80.0,
            y1: 30.0,
            font_size: 12.0,
            chars: None,
            is_bold: false,
        }];
        let errors = assign_text_to_cells(&mut table, &elements);
        assert!(errors.is_empty());
        assert!(table.cells[0][0].text.is_empty());
    }

    #[test]
    fn multiple_elements_same_cell() {
        let mut table = make_table();
        let elements = vec![
            TextElement {
                text: "line1".into(),
                x0: 10.0,
                y0: 10.0,
                x1: 80.0,
                y1: 20.0,
                font_size: 12.0,
                chars: None,
                is_bold: false,
            },
            TextElement {
                text: "line2".into(),
                x0: 10.0,
                y0: 25.0,
                x1: 80.0,
                y1: 35.0,
                font_size: 12.0,
                chars: None,
                is_bold: false,
            },
        ];
        assign_text_to_cells(&mut table, &elements);
        assert_eq!(table.cells[0][0].text, "line1\nline2");
    }

    #[test]
    fn split_text_across_columns() {
        let mut table = make_table();
        // Text "AB" spans both columns: 'A' in col 0, 'B' in col 1
        let elements = vec![TextElement {
            text: "AB".into(),
            x0: 50.0,
            y0: 10.0,
            x1: 150.0,
            y1: 30.0,
            font_size: 12.0,
            is_bold: false,
            chars: Some(vec![
                CharPosition {
                    char: 'A',
                    x0: 50.0,
                    x1: 90.0,
                }, // center=70, in col 0
                CharPosition {
                    char: 'B',
                    x0: 110.0,
                    x1: 150.0,
                }, // center=130, in col 1
            ]),
        }];
        let errors = assign_text_to_cells(&mut table, &elements);

        assert_eq!(table.cells[0][0].text, "A", "col 0 should have 'A'");
        assert_eq!(table.cells[0][1].text, "B", "col 1 should have 'B'");
        assert_eq!(errors.len(), 2, "should have 2 assignments");
    }

    #[test]
    fn split_text_all_in_one_column() {
        let mut table = make_table();
        // Text "AB" where both chars are in column 0
        let elements = vec![TextElement {
            text: "AB".into(),
            x0: 10.0,
            y0: 10.0,
            x1: 80.0,
            y1: 30.0,
            font_size: 12.0,
            is_bold: false,
            chars: Some(vec![
                CharPosition {
                    char: 'A',
                    x0: 10.0,
                    x1: 40.0,
                }, // center=25
                CharPosition {
                    char: 'B',
                    x0: 45.0,
                    x1: 80.0,
                }, // center=62.5
            ]),
        }];
        assign_text_to_cells(&mut table, &elements);

        assert_eq!(table.cells[0][0].text, "AB");
        assert!(table.cells[0][1].text.is_empty());
    }

    #[test]
    fn split_text_keeps_chars_near_column_edge() {
        let mut table = make_table();
        let elements = vec![TextElement {
            text: "privacy".into(),
            x0: 98.5,
            y0: 60.0,
            x1: 150.0,
            y1: 80.0,
            font_size: 12.0,
            is_bold: false,
            chars: Some(vec![
                CharPosition {
                    char: 'p',
                    x0: 98.2,
                    x1: 99.2,
                },
                CharPosition {
                    char: 'r',
                    x0: 101.0,
                    x1: 102.0,
                },
                CharPosition {
                    char: 'i',
                    x0: 103.0,
                    x1: 104.0,
                },
                CharPosition {
                    char: 'v',
                    x0: 105.0,
                    x1: 106.0,
                },
                CharPosition {
                    char: 'a',
                    x0: 107.0,
                    x1: 108.0,
                },
                CharPosition {
                    char: 'c',
                    x0: 109.0,
                    x1: 110.0,
                },
                CharPosition {
                    char: 'y',
                    x0: 111.0,
                    x1: 112.0,
                },
            ]),
        }];

        assign_text_to_cells(&mut table, &elements);

        assert_eq!(table.cells[1][1].text, "privacy");
    }

    #[test]
    fn append_to_cell_skips_exact_duplicate_lines() {
        let mut table = make_table();
        append_to_cell(&mut table, 0, 0, "12-10-2020");
        append_to_cell(&mut table, 0, 0, "12-10-2020");
        append_to_cell(&mut table, 0, 0, "Editorial");

        assert_eq!(table.cells[0][0].text, "12-10-2020\nEditorial");
    }

    #[test]
    fn append_to_cell_skips_fuzzy_duplicate_fragments() {
        let mut table = make_table();
        append_to_cell(&mut table, 0, 0, "DOMAIN AUTHENTICATION");
        append_to_cell(&mut table, 0, 0, "DOMAI UTHENTIAT");
        append_to_cell(&mut table, 0, 0, "CONNECTIONS TO PUBLIC NETWORKS");
        append_to_cell(&mut table, 0, 0, "CONNECTINS O UB");
        append_to_cell(&mut table, 0, 0, "CONTROL NAME");
        append_to_cell(&mut table, 0, 0, "CONTROL ENHANCEMENT NAME");
        append_to_cell(
            &mut table,
            0,
            0,
            "Call Out Box: Change “Special Publication 800-53B contains control baselines” to “SP 800-53B contains security and privacy control",
        );
        append_to_cell(&mut table, 0, 0, "baselines”");
        append_to_cell(
            &mut table,
            0,
            0,
            "Control PL-2 References: Change “[OMB A-130, Appendix II]” to",
        );
        append_to_cell(&mut table, 0, 0, "“[OMB A-130]”");
        append_to_cell(
            &mut table,
            0,
            0,
            "Control SC-17 Discussion: Change “Public Key Infrastructure” to",
        );
        append_to_cell(&mut table, 0, 0, "“Public Key Infrastructure (PKI)”");
        append_to_cell(
            &mut table,
            0,
            0,
            "Control AC-19 Discussion: Change “the organizational network” to",
        );
        append_to_cell(&mut table, 0, 0, "“its network”");
        append_to_cell(
            &mut table,
            0,
            0,
            "Control SC-19: Change “addressed by other controls for protocols”",
        );
        append_to_cell(&mut table, 0, 0, "to “addressed as any other technology or protocol”");
        append_to_cell(
            &mut table,
            0,
            0,
            "Control Enhancement SC-31(2): Change “ Selection (one or more); ”",
        );
        append_to_cell(&mut table, 0, 0, "to “ Selection (one or more): ”");
        append_to_cell(&mut table, 1, 0, "PROCE");
        append_to_cell(&mut table, 1, 0, "PROCESS REQUIREMENTS FOR INFORMATION TRANSFER");
        append_to_cell(&mut table, 0, 1, "IENT");
        append_to_cell(
            &mut table,
            0,
            1,
            "IDENTIFICATION OF FUNCTIONS, PORTS, PROTOCOLS, AND SERVICES",
        );

        assert_eq!(
            table.cells[0][0].text,
            "DOMAIN AUTHENTICATION\nCONNECTIONS TO PUBLIC NETWORKS\nCONTROL NAME\nCONTROL ENHANCEMENT NAME\nCall Out Box: Change “Special Publication 800-53B contains control baselines” to “SP 800-53B contains security and privacy control\nbaselines”\nControl PL-2 References: Change “[OMB A-130, Appendix II]” to\n“[OMB A-130]”\nControl SC-17 Discussion: Change “Public Key Infrastructure” to\n“Public Key Infrastructure (PKI)”\nControl AC-19 Discussion: Change “the organizational network” to\n“its network”\nControl SC-19: Change “addressed by other controls for protocols”\nto “addressed as any other technology or protocol”\nControl Enhancement SC-31(2): Change “ Selection (one or more); ”\nto “ Selection (one or more): ”"
        );
        assert_eq!(table.cells[1][0].text, "PROCESS REQUIREMENTS FOR INFORMATION TRANSFER");
        assert_eq!(
            table.cells[0][1].text,
            "IDENTIFICATION OF FUNCTIONS, PORTS, PROTOCOLS, AND SERVICES"
        );
    }

    #[test]
    fn split_text_uses_span_text_when_single_column_chars_are_incomplete() {
        let mut table = make_table();
        let elements = vec![TextElement {
            text: "privacy".into(),
            x0: 110.0,
            y0: 60.0,
            x1: 150.0,
            y1: 80.0,
            font_size: 12.0,
            is_bold: false,
            chars: Some(vec![
                CharPosition {
                    char: 'r',
                    x0: 111.0,
                    x1: 112.0,
                },
                CharPosition {
                    char: 'i',
                    x0: 113.0,
                    x1: 114.0,
                },
                CharPosition {
                    char: 'v',
                    x0: 115.0,
                    x1: 116.0,
                },
                CharPosition {
                    char: 'a',
                    x0: 117.0,
                    x1: 118.0,
                },
                CharPosition {
                    char: 'c',
                    x0: 119.0,
                    x1: 120.0,
                },
                CharPosition {
                    char: 'y',
                    x0: 121.0,
                    x1: 122.0,
                },
            ]),
        }];

        assign_text_to_cells(&mut table, &elements);

        assert_eq!(table.cells[1][1].text, "privacy");
    }

    #[test]
    fn split_text_skips_watermark_span_instead_of_assigning_partial_chars() {
        let mut table = make_table();
        let elements = vec![TextElement {
            text: "https://doi.org/10.6028/NIST.SP.800 12-10-2020".into(),
            x0: 110.0,
            y0: 60.0,
            x1: 180.0,
            y1: 80.0,
            font_size: 12.0,
            is_bold: false,
            chars: Some(
                "12-10-2020"
                    .chars()
                    .enumerate()
                    .map(|(idx, ch)| {
                        let x = 112.0 + idx as f64 * 2.0;
                        CharPosition {
                            char: ch,
                            x0: x,
                            x1: x + 1.0,
                        }
                    })
                    .collect(),
            ),
        }];

        assign_text_to_cells(&mut table, &elements);

        assert!(table.cells[1][1].text.is_empty());
    }

    #[test]
    fn assign_text_skips_watermark_spans_before_cell_assignment() {
        let mut table = make_table();
        let elements = vec![TextElement {
            text: "This publication is available free of charge from:".into(),
            x0: 110.0,
            y0: 60.0,
            x1: 180.0,
            y1: 80.0,
            font_size: 12.0,
            is_bold: false,
            chars: None,
        }];

        let errors = assign_text_to_cells(&mut table, &elements);

        assert!(errors.is_empty());
        assert!(table.cells[1][1].text.is_empty());
    }

    #[test]
    fn split_text_preserves_merged_status_span_across_columns() {
        let mut table = Table::new(
            vec![(0.0, 100.0), (100.0, 160.0), (160.0, 220.0)],
            vec![(0.0, 50.0), (50.0, 100.0)],
            Flavor::Stream,
        );
        let elements = vec![TextElement {
            text: "W: Incorporated into AC-4.".into(),
            x0: 110.0,
            y0: 60.0,
            x1: 210.0,
            y1: 80.0,
            font_size: 12.0,
            is_bold: false,
            chars: Some(vec![
                CharPosition {
                    char: 'W',
                    x0: 110.0,
                    x1: 112.0,
                },
                CharPosition {
                    char: ':',
                    x0: 113.0,
                    x1: 114.0,
                },
                CharPosition {
                    char: ' ',
                    x0: 115.0,
                    x1: 116.0,
                },
                CharPosition {
                    char: 'n',
                    x0: 120.0,
                    x1: 122.0,
                },
                CharPosition {
                    char: 't',
                    x0: 150.0,
                    x1: 152.0,
                },
                CharPosition {
                    char: 'C',
                    x0: 170.0,
                    x1: 172.0,
                },
                CharPosition {
                    char: '-',
                    x0: 173.0,
                    x1: 174.0,
                },
                CharPosition {
                    char: '4',
                    x0: 175.0,
                    x1: 177.0,
                },
                CharPosition {
                    char: '.',
                    x0: 178.0,
                    x1: 179.0,
                },
            ]),
        }];

        assign_text_to_cells(&mut table, &elements);

        assert_eq!(table.cells[1][1].text, "W: Incorporated into AC-4.");
        assert!(table.cells[1][2].text.is_empty());
    }

    #[test]
    fn split_text_keeps_status_continuation_in_existing_status_cell() {
        let mut table = Table::new(
            vec![(0.0, 100.0), (100.0, 160.0), (160.0, 220.0)],
            vec![(0.0, 50.0), (50.0, 100.0)],
            Flavor::Stream,
        );
        let elements = vec![
            TextElement {
                text: "W: Incorporated into AC-2, SI-".into(),
                x0: 110.0,
                y0: 60.0,
                x1: 190.0,
                y1: 70.0,
                font_size: 12.0,
                is_bold: false,
                chars: Some(vec![
                    CharPosition {
                        char: 'W',
                        x0: 110.0,
                        x1: 112.0,
                    },
                    CharPosition {
                        char: '-',
                        x0: 170.0,
                        x1: 172.0,
                    },
                ]),
            },
            TextElement {
                text: "3, SI-4, SI-5, and SI-10.".into(),
                x0: 110.0,
                y0: 72.0,
                x1: 190.0,
                y1: 82.0,
                font_size: 12.0,
                is_bold: false,
                chars: Some(vec![
                    CharPosition {
                        char: '3',
                        x0: 110.0,
                        x1: 112.0,
                    },
                    CharPosition {
                        char: 'S',
                        x0: 170.0,
                        x1: 172.0,
                    },
                ]),
            },
        ];

        assign_text_to_cells(&mut table, &elements);

        assert_eq!(
            table.cells[1][1].text,
            "W: Incorporated into AC-2, SI-3, SI-4, SI-5, and SI-10."
        );
        assert!(table.cells[1][2].text.is_empty());
    }

    #[test]
    fn definition_list_extracts_rows() {
        let config = ExtractConfig {
            strategy: crate::tables::types::Strategy::DefinitionList,
            definition_list_column_ratio: 0.35,
            ..Default::default()
        };
        let elements = vec![
            TextElement {
                text: "Access Control".into(),
                x0: 10.0,
                y0: 10.0,
                x1: 140.0,
                y1: 24.0,
                font_size: 12.0,
                is_bold: true,
                chars: None,
            },
            TextElement {
                text: "Limits system access".into(),
                x0: 220.0,
                y0: 10.0,
                x1: 420.0,
                y1: 24.0,
                font_size: 12.0,
                is_bold: false,
                chars: None,
            },
            TextElement {
                text: "Audit".into(),
                x0: 10.0,
                y0: 40.0,
                x1: 80.0,
                y1: 54.0,
                font_size: 12.0,
                is_bold: true,
                chars: None,
            },
            TextElement {
                text: "Events recorded".into(),
                x0: 220.0,
                y0: 40.0,
                x1: 390.0,
                y1: 54.0,
                font_size: 12.0,
                is_bold: false,
                chars: None,
            },
        ];

        let table = extract_definition_list_table(&elements, 600.0, 800.0, &config).unwrap();
        assert_eq!(table.num_rows(), 2);
        assert_eq!(table.cells[0][0].text, "Access Control");
        assert_eq!(table.cells[0][1].text, "Limits system access");
    }

    #[test]
    fn definition_list_merges_citation_rows() {
        let config = ExtractConfig {
            strategy: crate::tables::types::Strategy::DefinitionList,
            definition_list_column_ratio: 0.35,
            ..Default::default()
        };
        let elements = vec![
            TextElement {
                text: "Access Control".into(),
                x0: 10.0,
                y0: 10.0,
                x1: 140.0,
                y1: 24.0,
                font_size: 12.0,
                is_bold: true,
                chars: None,
            },
            TextElement {
                text: "Limits system access".into(),
                x0: 220.0,
                y0: 10.0,
                x1: 420.0,
                y1: 24.0,
                font_size: 12.0,
                is_bold: false,
                chars: None,
            },
            TextElement {
                text: "[ SP 800-128 ]".into(),
                x0: 12.0,
                y0: 26.0,
                x1: 110.0,
                y1: 38.0,
                font_size: 12.0,
                is_bold: false,
                chars: None,
            },
        ];

        let table = extract_definition_list_table(&elements, 600.0, 800.0, &config).unwrap();
        assert_eq!(table.num_rows(), 1);
        assert!(table.cells[0][1].text.contains("[ SP 800-128 ]"));
    }

    #[test]
    fn accuracy_perfect() {
        assert!((compute_accuracy(&[0.0, 0.0, 0.0]) - 100.0).abs() < 0.01);
    }

    #[test]
    fn accuracy_with_errors() {
        let acc = compute_accuracy(&[0.1, 0.1, 0.1]);
        assert!((acc - 90.0).abs() < 0.01);
    }
}
