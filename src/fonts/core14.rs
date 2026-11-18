//! Metrics for the 14 standard Type 1 fonts (ISO 32000-1:2008, 9.6.2.2).
//!
//! A simple font that names one of the standard 14 fonts may omit /Widths;
//! a conforming reader then uses the font's known metrics. The metrics here are
//! the Adobe Core14 AFM files, vendored unmodified in `core14_afm/` together with
//! their license (`core14_afm/LICENSE`, which must accompany the AFM files).
//!
//! Only the exact standard names (optionally behind a 6-letter subset prefix,
//! ISO 32000-1 9.6.4) are mapped. Aliases such as `Arial` or `TimesNewRoman`
//! are not sanctioned by ISO 32000-1 and are left unmapped.

use std::collections::HashMap;
use std::sync::OnceLock;

/// Parsed metrics of one standard font.
#[derive(Debug)]
pub struct Core14Metrics {
    /// Width (1/1000 em) by the font's built-in encoding code (AFM `C`).
    pub by_code: [Option<f32>; 256],
    /// Width (1/1000 em) by AFM glyph name (AFM `N`).
    pub by_name: HashMap<&'static str, f32>,
    /// Width (1/1000 em) by Unicode scalar, via the Adobe Glyph List.
    pub by_unicode: HashMap<char, f32>,
}

const NAMES: [&str; 14] = [
    "Courier",
    "Courier-Bold",
    "Courier-BoldOblique",
    "Courier-Oblique",
    "Helvetica",
    "Helvetica-Bold",
    "Helvetica-BoldOblique",
    "Helvetica-Oblique",
    "Symbol",
    "Times-Bold",
    "Times-BoldItalic",
    "Times-Italic",
    "Times-Roman",
    "ZapfDingbats",
];

const AFM: [&str; 14] = [
    include_str!("core14_afm/Courier.afm"),
    include_str!("core14_afm/Courier-Bold.afm"),
    include_str!("core14_afm/Courier-BoldOblique.afm"),
    include_str!("core14_afm/Courier-Oblique.afm"),
    include_str!("core14_afm/Helvetica.afm"),
    include_str!("core14_afm/Helvetica-Bold.afm"),
    include_str!("core14_afm/Helvetica-BoldOblique.afm"),
    include_str!("core14_afm/Helvetica-Oblique.afm"),
    include_str!("core14_afm/Symbol.afm"),
    include_str!("core14_afm/Times-Bold.afm"),
    include_str!("core14_afm/Times-BoldItalic.afm"),
    include_str!("core14_afm/Times-Italic.afm"),
    include_str!("core14_afm/Times-Roman.afm"),
    include_str!("core14_afm/ZapfDingbats.afm"),
];

static PARSED: [OnceLock<Core14Metrics>; 14] = [const { OnceLock::new() }; 14];

/// Index of the standard font named by `base_font`, if it is one of the 14.
///
/// Strips a subset prefix (`ABCDEF+`, six uppercase letters, ISO 32000-1 9.6.4).
pub fn standard_font_index(base_font: &str) -> Option<usize> {
    let name = match base_font.split_once('+') {
        Some((tag, rest)) if tag.len() == 6 && tag.bytes().all(|b| b.is_ascii_uppercase()) => rest,
        _ => base_font,
    };
    NAMES.iter().position(|n| *n == name)
}

/// Metrics of the standard font named by `base_font`, if it is one of the 14.
pub fn metrics_for(base_font: &str) -> Option<&'static Core14Metrics> {
    let idx = standard_font_index(base_font)?;
    Some(PARSED[idx].get_or_init(|| parse_afm(AFM[idx])))
}

/// True for the two symbolic standard fonts, whose built-in encoding is their own.
pub fn is_symbolic_standard(base_font: &str) -> bool {
    matches!(standard_font_index(base_font), Some(8) | Some(13))
}

fn parse_afm(src: &'static str) -> Core14Metrics {
    let mut by_code = [None; 256];
    let mut by_name = HashMap::new();
    let mut by_unicode = HashMap::new();
    let mut in_metrics = false;
    for line in src.lines() {
        let line = line.trim();
        if line.starts_with("StartCharMetrics") {
            in_metrics = true;
            continue;
        }
        if line.starts_with("EndCharMetrics") {
            break;
        }
        if !in_metrics {
            continue;
        }
        // "C 32 ; WX 278 ; N space ; B 0 0 0 0 ;"
        let mut code: Option<i32> = None;
        let mut wx: Option<f32> = None;
        let mut name: Option<&'static str> = None;
        for field in line.split(';') {
            let mut it = field.split_whitespace();
            match (it.next(), it.next()) {
                (Some("C"), Some(v)) => code = v.parse().ok(),
                (Some("WX"), Some(v)) => wx = v.parse().ok(),
                (Some("N"), Some(v)) => name = Some(v),
                _ => {},
            }
        }
        let Some(w) = wx else { continue };
        if let Some(c) = code {
            if (0..256).contains(&c) {
                by_code[c as usize] = Some(w);
            }
        }
        if let Some(n) = name {
            by_name.insert(n, w);
            if let Some(u) = super::font_dict::glyph_name_to_unicode(n) {
                by_unicode.entry(u).or_insert(w);
            }
        }
    }
    Core14Metrics {
        by_code,
        by_name,
        by_unicode,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_and_subset_prefix() {
        assert_eq!(standard_font_index("Helvetica"), Some(4));
        assert_eq!(standard_font_index("ABCDEF+Times-Roman"), Some(12));
        assert_eq!(standard_font_index("abcdef+Times-Roman"), None);
        assert_eq!(standard_font_index("Arial"), None);
        assert_eq!(standard_font_index("TimesNewRoman"), None);
        assert!(is_symbolic_standard("Symbol"));
        assert!(!is_symbolic_standard("Courier"));
    }

    #[test]
    fn helvetica_widths_match_afm() {
        let m = metrics_for("Helvetica").unwrap();
        assert_eq!(m.by_code[32], Some(278.0)); // space
        assert_eq!(m.by_unicode.get(&'E'), Some(&667.0));
        assert_eq!(m.by_unicode.get(&'x'), Some(&500.0));
        assert_eq!(m.by_unicode.get(&'.'), Some(&278.0));
        let c = metrics_for("Courier").unwrap();
        assert!(c.by_unicode.values().all(|&w| w == 600.0));
    }

    #[test]
    fn all_fourteen_parse() {
        for n in NAMES {
            let m = metrics_for(n).unwrap();
            assert!(m.by_name.len() >= 190, "{n}: {}", m.by_name.len());
        }
    }
}
