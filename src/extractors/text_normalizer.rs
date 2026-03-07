//! Text normalization for PDF extraction pipeline.
//!
//! Handles Unicode cleanup, ligature expansion, whitespace normalization,
//! and symbol density calculation. Absorbs logic from Python S02/S07b steps.

/// Normalize text extracted from PDF for downstream processing.
///
/// Performs: zero-width char removal, directional mark stripping,
/// soft hyphen removal, whitespace collapse.
pub fn normalize_text(text: &str) -> String {
    let mut result = String::with_capacity(text.len());

    let mut prev_was_space = false;
    for ch in text.chars() {
        // Skip zero-width and invisible formatting characters
        if is_invisible_char(ch) {
            continue;
        }

        // Normalize hyphens
        let ch = normalize_hyphen(ch);

        // Collapse whitespace
        if ch.is_whitespace() {
            if !prev_was_space && !result.is_empty() {
                result.push(' ');
                prev_was_space = true;
            }
            continue;
        }

        prev_was_space = false;
        result.push(ch);
    }

    // Trim trailing space
    if result.ends_with(' ') {
        result.pop();
    }

    result
}

/// Expand common ligatures to their component characters.
pub fn expand_ligatures(text: &str) -> String {
    let mut result = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '\u{FB00}' => result.push_str("ff"),
            '\u{FB01}' => result.push_str("fi"),
            '\u{FB02}' => result.push_str("fl"),
            '\u{FB03}' => result.push_str("ffi"),
            '\u{FB04}' => result.push_str("ffl"),
            '\u{FB05}' | '\u{FB06}' => result.push_str("st"),
            '\u{0132}' => result.push_str("IJ"),
            '\u{0133}' => result.push_str("ij"),
            '\u{0152}' => result.push_str("OE"),
            '\u{0153}' => result.push_str("oe"),
            '\u{00C6}' => result.push_str("AE"),
            '\u{00E6}' => result.push_str("ae"),
            _ => result.push(ch),
        }
    }
    result
}

/// Calculate the ratio of mathematical/special symbols to total characters.
///
/// Used by block classifier to detect equation blocks.
pub fn symbol_density(text: &str) -> f32 {
    let total = text.chars().count();
    if total == 0 {
        return 0.0;
    }
    let symbols = text.chars().filter(|c| is_math_or_special(*c)).count();
    symbols as f32 / total as f32
}

/// Check if text looks like a binary stream leak (non-printable chars dominate).
///
/// Returns true if >30% of chars are non-printable (excluding whitespace).
pub fn is_binary_leak(text: &str) -> bool {
    let total = text.chars().count();
    if total < 10 {
        return false;
    }
    let non_printable = text.chars().filter(|c| {
        !c.is_whitespace() && !c.is_alphanumeric() && !c.is_ascii_punctuation()
            && !is_common_unicode_punct(*c)
    }).count();
    non_printable as f32 / total as f32 > 0.3
}

/// Full normalization pipeline: expand ligatures, then normalize text.
pub fn full_normalize(text: &str) -> String {
    let expanded = expand_ligatures(text);
    normalize_text(&expanded)
}

// --- Private helpers ---

fn is_invisible_char(ch: char) -> bool {
    matches!(ch,
        '\u{200B}' | // zero-width space
        '\u{200C}' | // zero-width non-joiner
        '\u{200D}' | // zero-width joiner
        '\u{200E}' | // LTR mark
        '\u{200F}' | // RTL mark
        '\u{202A}' | // LTR embedding
        '\u{202B}' | // RTL embedding
        '\u{202C}' | // pop directional formatting
        '\u{202D}' | // LTR override
        '\u{202E}' | // RTL override
        '\u{2060}' | // word joiner
        '\u{2061}' | // function application
        '\u{2062}' | // invisible times
        '\u{2063}' | // invisible separator
        '\u{2064}' | // invisible plus
        '\u{FEFF}' | // BOM / zero-width no-break space
        '\u{00AD}'   // soft hyphen
    )
}

fn normalize_hyphen(ch: char) -> char {
    match ch {
        '\u{2010}' | // hyphen
        '\u{2011}' | // non-breaking hyphen
        '\u{2012}' | // figure dash
        '\u{FE63}' | // small hyphen-minus
        '\u{FF0D}'   // fullwidth hyphen-minus
            => '-',
        '\u{2013}' => '\u{2013}', // en dash — keep as-is
        '\u{2014}' => '\u{2014}', // em dash — keep as-is
        _ => ch,
    }
}

fn is_math_or_special(ch: char) -> bool {
    matches!(ch,
        '=' | '+' | '<' | '>' | '~' | '^' | '|' | '\\' |
        '\u{2200}'..='\u{22FF}' | // Mathematical Operators
        '\u{2190}'..='\u{21FF}' | // Arrows
        '\u{2300}'..='\u{23FF}' | // Misc Technical
        '\u{27C0}'..='\u{27EF}' | // Misc Mathematical Symbols-A
        '\u{2980}'..='\u{29FF}' | // Misc Mathematical Symbols-B
        '\u{2A00}'..='\u{2AFF}' | // Supplemental Mathematical Operators
        '\u{0391}'..='\u{03C9}' | // Greek letters
        '\u{00B1}' | '\u{00D7}' | '\u{00F7}'   // plus-minus, times, division
    )
}

fn is_common_unicode_punct(ch: char) -> bool {
    matches!(ch,
        '\u{2018}'..='\u{201F}' | // smart quotes
        '\u{2013}' | '\u{2014}' | // en/em dash
        '\u{2026}' | // ellipsis
        '\u{00A9}' | '\u{00AE}' | '\u{2122}' | // copyright, registered, trademark
        '\u{00B0}' | // degree
        '\u{00A7}' | // section sign
        '\u{00B6}' | // pilcrow
        '\u{2022}' | '\u{2023}' | '\u{25CF}' | '\u{25CB}' | // bullets
        '\u{00AB}' | '\u{00BB}'   // guillemets
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalize_removes_zero_width() {
        assert_eq!(normalize_text("Hello\u{200B}World"), "HelloWorld");
        assert_eq!(normalize_text("\u{FEFF}Start"), "Start");
    }

    #[test]
    fn test_normalize_removes_soft_hyphen() {
        assert_eq!(normalize_text("Hello\u{00AD}World"), "HelloWorld");
    }

    #[test]
    fn test_normalize_removes_directional_marks() {
        assert_eq!(normalize_text("ABC\u{200E}\u{200F}DEF"), "ABCDEF");
    }

    #[test]
    fn test_normalize_collapses_whitespace() {
        assert_eq!(normalize_text("hello   world"), "hello world");
        assert_eq!(normalize_text("  leading"), "leading");
        assert_eq!(normalize_text("trailing  "), "trailing");
    }

    #[test]
    fn test_normalize_hyphen() {
        assert_eq!(normalize_text("figure\u{2010}dash"), "figure-dash");
        assert_eq!(normalize_text("non\u{2011}breaking"), "non-breaking");
    }

    #[test]
    fn test_expand_ligatures() {
        assert_eq!(expand_ligatures("\u{FB01}nd"), "find");
        assert_eq!(expand_ligatures("\u{FB00}ect"), "ffect");
        assert_eq!(expand_ligatures("\u{FB03}ce"), "ffice");
        assert_eq!(expand_ligatures("\u{FB02}oor"), "floor");
    }

    #[test]
    fn test_symbol_density() {
        assert!(symbol_density("x=y+z") > 0.3);
        assert!(symbol_density("Hello world") < 0.05);
        assert_eq!(symbol_density(""), 0.0);
    }

    #[test]
    fn test_binary_leak() {
        assert!(!is_binary_leak("Normal text here"));
        // Construct text with many non-printable chars
        let leak: String = (0..20).map(|i| if i % 3 == 0 { '\u{0001}' } else { 'a' }).collect();
        assert!(is_binary_leak(&leak));
    }

    #[test]
    fn test_full_normalize() {
        assert_eq!(full_normalize("\u{200B}\u{FB01}nd\u{00AD}ing  "), "finding");
    }
}
