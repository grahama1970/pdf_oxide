//! Shared text decoding module for PDF text extraction and rendering.
//!
//! This module provides unified text decoding functionality that can be used by both
//! text extraction and rendering components. It handles the complexity of PDF font
//! encodings, including Type0/CID fonts with multi-byte character codes.

use crate::fonts::FontInfo;

pub fn codex_test_marker() -> &str {
    "codex-was-here"
}

/// A decoded glyph with its character code, Unicode representation, and byte consumption.
#[derive(Debug, Clone, PartialEq)]
pub struct DecodedGlyph {
    /// The character code from the PDF content stream
    pub char_code: u32,
    /// The Unicode string representation of this glyph
    pub unicode: String,
    /// Number of bytes consumed from the input to produce this glyph
    pub bytes_consumed: usize,
}

/// Decode PDF text bytes into a sequence of glyphs with Unicode mappings.
///
/// This function handles the complexity of PDF font encodings:
/// - For Type0 fonts (font.subtype == "Type0"), consumes 2 bytes at a time
/// - For simple fonts, consumes 1 byte at a time
/// - Fallback: if no font, treats bytes as Latin-1 (ISO 8859-1)
///
/// # Arguments
///
/// * `bytes` - Raw bytes from PDF content stream
/// * `font` - Optional font information for character mapping
///
/// # Returns
///
/// Vector of decoded glyphs with character codes, Unicode strings, and byte consumption info.
///
/// # Examples
///
/// ```rust
/// use pdf_oxide::fonts::text_decode::decode_pdf_text;
///
/// // Simple ASCII text without font
/// let glyphs = decode_pdf_text(b"Hello", None);
/// assert_eq!(glyphs.len(), 5);
/// assert_eq!(glyphs[0].unicode, "H");
/// assert_eq!(glyphs[0].char_code, 0x48);
/// assert_eq!(glyphs[0].bytes_consumed, 1);
/// ```
pub fn decode_pdf_text(bytes: &[u8], font: Option<&FontInfo>) -> Vec<DecodedGlyph> {
    let mut glyphs = Vec::new();
    
    if let Some(font) = font {
        // Use font-specific decoding
        if font.subtype == "Type0" {
            // Type0 fonts: consume 2 bytes at a time for CID values
            decode_type0_font(bytes, font, &mut glyphs);
        } else {
            // Simple fonts: consume 1 byte at a time
            decode_simple_font(bytes, font, &mut glyphs);
        }
    } else {
        // No font: fallback to Latin-1 encoding
        decode_latin1_fallback(bytes, &mut glyphs);
    }
    
    glyphs
}

/// Decode bytes for Type0/CID fonts (2 bytes per character code).
fn decode_type0_font(bytes: &[u8], font: &FontInfo, glyphs: &mut Vec<DecodedGlyph>) {
    let byte_mode = get_byte_mode(Some(font));
    let mut iter = TextCharIter::new(bytes, Some(font), byte_mode);
    
    while let Some((char_code, bytes_consumed)) = iter.next() {
        let unicode = font
            .char_to_unicode(char_code as u32)
            .unwrap_or_else(|| fallback_char_to_unicode(char_code as u32));
        
        // Filter out replacement characters from failed mappings
        if unicode != "\u{FFFD}" {
            glyphs.push(DecodedGlyph {
                char_code: char_code as u32,
                unicode,
                bytes_consumed,
            });
        }
    }
}

/// Decode bytes for simple fonts (1 byte per character code).
fn decode_simple_font(bytes: &[u8], font: &FontInfo, glyphs: &mut Vec<DecodedGlyph>) {
    // Use pre-computed lookup table for performance
    let table = font.get_byte_to_char_table();
    
    for &byte in bytes {
        let char_code = byte as u32;
        let unicode = if table[byte as usize] != '\0' {
            table[byte as usize].to_string()
        } else {
            // Fallback: multi-char mapping or unmapped byte
            font.char_to_unicode(char_code)
                .unwrap_or_else(|| fallback_char_to_unicode(char_code))
        };
        
        // Filter out replacement characters from failed mappings
        if unicode != "\u{FFFD}" {
            glyphs.push(DecodedGlyph {
                char_code,
                unicode,
                bytes_consumed: 1,
            });
        }
    }
}

/// Decode bytes using Latin-1 fallback when no font is available.
fn decode_latin1_fallback(bytes: &[u8], glyphs: &mut Vec<DecodedGlyph>) {
    for &byte in bytes {
        let char_code = byte as u32;
        let unicode = char::from(byte).to_string();
        
        glyphs.push(DecodedGlyph {
            char_code,
            unicode,
            bytes_consumed: 1,
        });
    }
}

/// Fallback character-to-Unicode mapping for unmapped character codes.
///
/// This function provides Unicode mappings for common characters that may not be
/// properly mapped in font dictionaries, including punctuation, mathematical
/// operators, Greek letters, and currency symbols.
pub fn fallback_char_to_unicode(char_code: u32) -> String {
    match char_code {
        // ==================================================================================
        // PRIORITY 1: Common Punctuation (most frequently failing)
        // ==================================================================================
        0x2014 => "—".to_string(),        // Em dash
        0x2013 => "–".to_string(),        // En dash
        0x2018 => "\u{2018}".to_string(), // Left single quotation mark (')
        0x2019 => "\u{2019}".to_string(), // Right single quotation mark (')
        0x201C => "\u{201C}".to_string(), // Left double quotation mark (")
        0x201D => "\u{201D}".to_string(), // Right double quotation mark (")
        0x2022 => "•".to_string(),        // Bullet
        0x2026 => "…".to_string(),        // Horizontal ellipsis
        0x00B0 => "°".to_string(),        // Degree sign

        // ==================================================================================
        // PRIORITY 2: Mathematical Operators (common in academic papers)
        // ==================================================================================
        0x00B1 => "±".to_string(), // Plus-minus sign
        0x00D7 => "×".to_string(), // Multiplication sign
        0x00F7 => "÷".to_string(), // Division sign
        0x2202 => "∂".to_string(), // Partial differential
        0x2207 => "∇".to_string(), // Nabla (del operator)
        0x220F => "∏".to_string(), // N-ary product
        0x2211 => "∑".to_string(), // N-ary summation
        0x221A => "√".to_string(), // Square root
        0x221E => "∞".to_string(), // Infinity
        0x2260 => "≠".to_string(), // Not equal to
        0x2261 => "≡".to_string(), // Identical to
        0x2264 => "≤".to_string(), // Less-than or equal to
        0x2265 => "≥".to_string(), // Greater-than or equal to
        0x222B => "∫".to_string(), // Integral
        0x2248 => "≈".to_string(), // Almost equal to
        0x2282 => "⊂".to_string(), // Subset of
        0x2283 => "⊃".to_string(), // Superset of
        0x2286 => "⊆".to_string(), // Subset of or equal to
        0x2287 => "⊇".to_string(), // Superset of or equal to
        0x2208 => "∈".to_string(), // Element of
        0x2209 => "∉".to_string(), // Not an element of
        0x2200 => "∀".to_string(), // For all
        0x2203 => "∃".to_string(), // There exists
        0x2205 => "∅".to_string(), // Empty set
        0x2227 => "∧".to_string(), // Logical and
        0x2228 => "∨".to_string(), // Logical or
        0x00AC => "¬".to_string(), // Not sign
        0x2192 => "→".to_string(), // Rightwards arrow
        0x2190 => "←".to_string(), // Leftwards arrow
        0x2194 => "↔".to_string(), // Left right arrow
        0x21D2 => "⇒".to_string(), // Rightwards double arrow
        0x21D4 => "⇔".to_string(), // Left right double arrow

        // ==================================================================================
        // PRIORITY 3: Greek Letters (common in scientific/mathematical texts)
        // ==================================================================================
        // Lowercase Greek
        0x03B1 => "α".to_string(), // Alpha
        0x03B2 => "β".to_string(), // Beta
        0x03B3 => "γ".to_string(), // Gamma
        0x03B4 => "δ".to_string(), // Delta
        0x03B5 => "ε".to_string(), // Epsilon
        0x03B6 => "ζ".to_string(), // Zeta
        0x03B7 => "η".to_string(), // Eta
        0x03B8 => "θ".to_string(), // Theta
        0x03B9 => "ι".to_string(), // Iota
        0x03BA => "κ".to_string(), // Kappa
        0x03BB => "λ".to_string(), // Lambda
        0x03BC => "μ".to_string(), // Mu
        0x03BD => "ν".to_string(), // Nu
        0x03BE => "ξ".to_string(), // Xi
        0x03BF => "ο".to_string(), // Omicron
        0x03C0 => "π".to_string(), // Pi
        0x03C1 => "ρ".to_string(), // Rho
        0x03C2 => "ς".to_string(), // Final sigma
        0x03C3 => "σ".to_string(), // Sigma
        0x03C4 => "τ".to_string(), // Tau
        0x03C5 => "υ".to_string(), // Upsilon
        0x03C6 => "φ".to_string(), // Phi
        0x03C7 => "χ".to_string(), // Chi
        0x03C8 => "ψ".to_string(), // Psi
        0x03C9 => "ω".to_string(), // Omega

        // Uppercase Greek
        0x0391 => "Α".to_string(), // Alpha
        0x0392 => "Β".to_string(), // Beta
        0x0393 => "Γ".to_string(), // Gamma
        0x0394 => "Δ".to_string(), // Delta
        0x0395 => "Ε".to_string(), // Epsilon
        0x0396 => "Ζ".to_string(), // Zeta
        0x0397 => "Η".to_string(), // Eta
        0x0398 => "Θ".to_string(), // Theta
        0x0399 => "Ι".to_string(), // Iota
        0x039A => "Κ".to_string(), // Kappa
        0x039B => "Λ".to_string(), // Lambda
        0x039C => "Μ".to_string(), // Mu
        0x039D => "Ν".to_string(), // Nu
        0x039E => "Ξ".to_string(), // Xi
        0x039F => "Ο".to_string(), // Omicron
        0x03A0 => "Π".to_string(), // Pi
        0x03A1 => "Ρ".to_string(), // Rho
        0x03A3 => "Σ".to_string(), // Sigma
        0x03A4 => "Τ".to_string(), // Tau
        0x03A5 => "Υ".to_string(), // Upsilon
        0x03A6 => "Φ".to_string(), // Phi
        0x03A7 => "Χ".to_string(), // Chi
        0x03A8 => "Ψ".to_string(), // Psi
        0x03A9 => "Ω".to_string(), // Omega

        // ==================================================================================
        // PRIORITY 4: Currency Symbols
        // ==================================================================================
        0x20AC => "€".to_string(), // Euro
        0x00A3 => "£".to_string(), // Pound sterling
        0x00A5 => "¥".to_string(), // Yen
        0x00A2 => "¢".to_string(), // Cent
        0x20A3 => "₣".to_string(), // French franc
        0x20A4 => "₤".to_string(), // Lira
        0x20A9 => "₩".to_string(), // Won
        0x20AA => "₪".to_string(), // New shekel
        0x20AB => "₫".to_string(), // Dong
        0x20B9 => "₹".to_string(), // Indian rupee

        // ==================================================================================
        // PRIORITY 5: Direct Unicode (for valid ranges)
        // ==================================================================================
        // Valid Unicode: BMP (0x0000-0xD7FF, 0xE000-0xFFFF) and supplementary planes
        // Excludes surrogate pairs (0xD800-0xDFFF)
        code => {
            if let Some(ch) = char::from_u32(code) {
                if (0xE000..=0xF8FF).contains(&code) {
                    log::debug!("Private Use Area character: U+{:04X}", code);
                }
                ch.to_string()
            } else {
                log::warn!("Character code 0x{:04X} is not a valid Unicode code point", code);
                "?".to_string()
            }
        },
    }
}

/// Byte grouping mode for CID font character code decoding.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ByteMode {
    /// Single-byte codes (simple fonts, some predefined CMaps)
    OneByte,
    /// Always 2-byte codes (Identity-H/V, UCS2)
    TwoByte,
    /// Shift-JIS variable-width (1 or 2 bytes depending on lead byte)
    ShiftJIS,
}

/// Get byte grouping mode for a font.
pub fn get_byte_mode(font: Option<&FontInfo>) -> ByteMode {
    if let Some(font) = font {
        if font.subtype == "Type0" {
            match &font.encoding {
                crate::fonts::Encoding::Identity => ByteMode::TwoByte,
                crate::fonts::Encoding::Standard(name) => {
                    if (name.contains("Identity") && !name.contains("OneByteIdentity"))
                        || name.contains("UCS2")
                        || name.contains("UTF16")
                    {
                        ByteMode::TwoByte
                    } else if name.contains("RKSJ") {
                        ByteMode::ShiftJIS
                    } else if name.contains("EUC")
                        || name.contains("GBK")
                        || name.contains("GBpc")
                        || name.contains("GB-")
                        || name.contains("CNS")
                        || name.contains("B5")
                        || name.contains("KSC")
                        || name.contains("KSCms")
                    {
                        // CIDs are typically 2-byte values in these CMaps
                        ByteMode::TwoByte
                    } else {
                        ByteMode::OneByte
                    }
                },
                _ => ByteMode::OneByte,
            }
        } else {
            ByteMode::OneByte
        }
    } else {
        ByteMode::OneByte
    }
}

/// Iterator over characters in a PDF string based on font encoding.
pub struct TextCharIter<'a> {
    bytes: &'a [u8],
    byte_mode: ByteMode,
    index: usize,
}

impl<'a> TextCharIter<'a> {
    pub fn new(bytes: &'a [u8], _font: Option<&FontInfo>, byte_mode: ByteMode) -> Self {
        Self {
            bytes,
            byte_mode,
            index: 0,
        }
    }
}

impl<'a> Iterator for TextCharIter<'a> {
    type Item = (u16, usize); // (char_code, bytes_consumed)

    fn next(&mut self) -> Option<Self::Item> {
        if self.index >= self.bytes.len() {
            return None;
        }

        let (char_code, bytes_consumed) = match self.byte_mode {
            ByteMode::TwoByte if self.index + 1 < self.bytes.len() => {
                (((self.bytes[self.index] as u16) << 8) | (self.bytes[self.index + 1] as u16), 2)
            },
            ByteMode::ShiftJIS => {
                let b = self.bytes[self.index];
                let is_lead = (0x81..=0x9F).contains(&b) || (0xE0..=0xFC).contains(&b);
                if is_lead && self.index + 1 < self.bytes.len() {
                    (((b as u16) << 8) | (self.bytes[self.index + 1] as u16), 2)
                } else {
                    (b as u16, 1)
                }
            },
            _ => (self.bytes[self.index] as u16, 1),
        };

        self.index += bytes_consumed;
        Some((char_code, bytes_consumed))
    }
}

/// Legacy decode function for backward compatibility.
///
/// This function maintains the exact same behavior as the original
/// `decode_text_to_unicode` function from the text extractor.
pub fn decode_text_to_unicode(bytes: &[u8], font: Option<&FontInfo>) -> String {
    let raw_result = if let Some(font) = font {
        let mut result = String::new();
        // Use pre-computed lookup table for performance if it's a simple font
        if font.subtype != "Type0" {
            let table = font.get_byte_to_char_table();
            for &byte in bytes {
                let c = table[byte as usize];
                if c != '\0' {
                    result.push(c);
                } else {
                    // Fallback: multi-char mapping or unmapped byte
                    let char_str = font
                        .char_to_unicode(byte as u32)
                        .unwrap_or_else(|| fallback_char_to_unicode(byte as u32));
                    if char_str != "\u{FFFD}" {
                        result.push_str(&char_str);
                    }
                }
            }
        } else {
            // Complex font: use unified iterator for robust multi-byte decoding
            let byte_mode = get_byte_mode(Some(font));
            let mut iter = TextCharIter::new(bytes, Some(font), byte_mode);
            while let Some((char_code, _)) = iter.next() {
                let char_str = font
                    .char_to_unicode(char_code as u32)
                    .unwrap_or_else(|| fallback_char_to_unicode(char_code as u32));
                if char_str != "\u{FFFD}" {
                    result.push_str(&char_str);
                }
            }
        }
        result
    } else {
        // No font - fallback to Latin-1 (ISO 8859-1) encoding
        // Per PDF Spec ISO 32000-1:2008, Section 9.6.6, Latin-1 maps bytes 0x00-0xFF
        // directly to Unicode code points U+0000-U+00FF
        log::warn!(
            "⚠️  No font provided for {} bytes, using Latin-1 fallback (PDF spec compliant)",
            bytes.len()
        );
        bytes.iter().map(|&b| char::from(b)).collect()
    };

    // Filter control characters from failed encoding resolution
    // Keep: \t (0x09), \n (0x0A), \r (0x0D), and all printable chars (>= 0x20)
    let mut filtered = String::with_capacity(raw_result.len());
    for c in raw_result.chars() {
        if c >= '\x20' || c == '\t' || c == '\n' || c == '\r' {
            filtered.push(c);
        }
    }
    filtered
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_decode_pdf_text_no_font_latin1() {
        let glyphs = decode_pdf_text(b"Hello", None);
        assert_eq!(glyphs.len(), 5);
        assert_eq!(glyphs[0].unicode, "H");
        assert_eq!(glyphs[0].char_code, 0x48);
        assert_eq!(glyphs[0].bytes_consumed, 1);
        assert_eq!(glyphs[4].unicode, "o");
        assert_eq!(glyphs[4].char_code, 0x6F);
        assert_eq!(glyphs[4].bytes_consumed, 1);
    }

    #[test]
    fn test_decode_pdf_text_no_font_high_bytes() {
        let bytes = [0xC0, 0xE9, 0xF1]; // À é ñ in Latin-1
        let glyphs = decode_pdf_text(&bytes, None);
        assert_eq!(glyphs.len(), 3);
        assert_eq!(glyphs[0].unicode, "À");
        assert_eq!(glyphs[0].char_code, 0xC0);
        assert_eq!(glyphs[1].unicode, "é");
        assert_eq!(glyphs[1].char_code, 0xE9);
        assert_eq!(glyphs[2].unicode, "ñ");
        assert_eq!(glyphs[2].char_code, 0xF1);
    }

    #[test]
    fn test_fallback_char_to_unicode_common_punctuation() {
        assert_eq!(fallback_char_to_unicode(0x2014), "—"); // Em dash
        assert_eq!(fallback_char_to_unicode(0x2013), "–"); // En dash
        assert_eq!(fallback_char_to_unicode(0x2022), "•"); // Bullet
        assert_eq!(fallback_char_to_unicode(0x2026), "…"); // Ellipsis
        assert_eq!(fallback_char_to_unicode(0x00B0), "°"); // Degree
    }

    #[test]
    fn test_fallback_char_to_unicode_math_operators() {
        assert_eq!(fallback_char_to_unicode(0x00B1), "±"); // Plus-minus
        assert_eq!(fallback_char_to_unicode(0x00D7), "×"); // Multiply
        assert_eq!(fallback_char_to_unicode(0x221E), "∞"); // Infinity
        assert_eq!(fallback_char_to_unicode(0x2264), "≤"); // Less or equal
        assert_eq!(fallback_char_to_unicode(0x2265), "≥"); // Greater or equal
        assert_eq!(fallback_char_to_unicode(0x2260), "≠"); // Not equal
        assert_eq!(fallback_char_to_unicode(0x221A), "√"); // Square root
        assert_eq!(fallback_char_to_unicode(0x222B), "∫"); // Integral
        assert_eq!(fallback_char_to_unicode(0x2211), "∑"); // Summation
    }

    #[test]
    fn test_fallback_char_to_unicode_greek_letters() {
        assert_eq!(fallback_char_to_unicode(0x03B1), "α"); // alpha
        assert_eq!(fallback_char_to_unicode(0x03B2), "β"); // beta
        assert_eq!(fallback_char_to_unicode(0x03C0), "π"); // pi
        assert_eq!(fallback_char_to_unicode(0x03C9), "ω"); // omega
        assert_eq!(fallback_char_to_unicode(0x0393), "Γ"); // Gamma
        assert_eq!(fallback_char_to_unicode(0x03A9), "Ω"); // Omega
    }

    #[test]
    fn test_fallback_char_to_unicode_currency() {
        assert_eq!(fallback_char_to_unicode(0x20AC), "€"); // Euro
        assert_eq!(fallback_char_to_unicode(0x00A3), "£"); // Pound
        assert_eq!(fallback_char_to_unicode(0x00A5), "¥"); // Yen
        assert_eq!(fallback_char_to_unicode(0x00A2), "¢"); // Cent
    }

    #[test]
    fn test_fallback_char_to_unicode_direct_unicode() {
        assert_eq!(fallback_char_to_unicode(0x41), "A");
        assert_eq!(fallback_char_to_unicode(0x20), " ");
    }

    #[test]
    fn test_fallback_char_to_unicode_invalid_code_point() {
        // Surrogate pairs are invalid Unicode code points
        assert_eq!(fallback_char_to_unicode(0xD800), "?");
        assert_eq!(fallback_char_to_unicode(0xDFFF), "?");
    }

    #[test]
    fn test_fallback_char_to_unicode_private_use_area() {
        let result = fallback_char_to_unicode(0xE000);
        // Should be a valid character in the Private Use Area
        assert_eq!(result.chars().count(), 1);
        assert_eq!(result.chars().next().unwrap() as u32, 0xE000);
    }

    #[test]
    fn test_decode_text_to_unicode_no_font() {
        let result = decode_text_to_unicode(b"Hello", None);
        assert_eq!(result, "Hello");
    }

    #[test]
    fn test_decode_text_to_unicode_no_font_high_bytes() {
        let bytes = [0xC0, 0xE9, 0xF1]; // À é ñ in Latin-1
        let result = decode_text_to_unicode(&bytes, None);
        assert_eq!(result, "Àéñ");
    }

    #[test]
    fn test_decode_text_to_unicode_filters_control_chars() {
        let bytes = [0x48, 0x01, 0x65, 0x02, 0x6C, 0x03, 0x6C, 0x04, 0x6F]; // H\x01e\x02l\x03l\x04o
        let result = decode_text_to_unicode(&bytes, None);
        assert_eq!(result, "Hello"); // Control chars filtered out
    }

    // Additional tests for the requested functionality

    #[test]
    fn test_decode_pdf_text_simple_font_ascii() {
        let font = create_mock_simple_font();
        let glyphs = decode_pdf_text(b"ABC", Some(&font));
        assert_eq!(glyphs.len(), 3);
        assert_eq!(glyphs[0].unicode, "A");
        assert_eq!(glyphs[0].char_code, 0x41);
        assert_eq!(glyphs[0].bytes_consumed, 1);
        assert_eq!(glyphs[1].unicode, "B");
        assert_eq!(glyphs[1].char_code, 0x42);
        assert_eq!(glyphs[1].bytes_consumed, 1);
        assert_eq!(glyphs[2].unicode, "C");
        assert_eq!(glyphs[2].char_code, 0x43);
        assert_eq!(glyphs[2].bytes_consumed, 1);
    }

    #[test]
    fn test_decode_pdf_text_high_bytes_no_font_latin1_fallback() {
        let bytes = [0x80, 0x90, 0xFF]; // High bytes
        let glyphs = decode_pdf_text(&bytes, None);
        assert_eq!(glyphs.len(), 3);
        assert_eq!(glyphs[0].unicode, "\u{0080}");
        assert_eq!(glyphs[0].char_code, 0x80);
        assert_eq!(glyphs[1].unicode, "\u{0090}");
        assert_eq!(glyphs[1].char_code, 0x90);
        assert_eq!(glyphs[2].unicode, "\u{00FF}");
        assert_eq!(glyphs[2].char_code, 0xFF);
    }

    #[test]
    fn test_decode_text_to_unicode_control_character_filtering() {
        // Test that control characters (< 0x20) are filtered except tab/newline
        let bytes = [
            0x48, // 'H'
            0x01, // Control char (should be filtered)
            0x65, // 'e'
            0x09, // Tab (should be kept)
            0x6C, // 'l'
            0x0A, // Newline (should be kept)
            0x6C, // 'l'
            0x0D, // Carriage return (should be kept)
            0x6F, // 'o'
            0x1F, // Control char (should be filtered)
        ];
        let result = decode_text_to_unicode(&bytes, None);
        assert_eq!(result, "He\tl\nl\ro");
    }

    #[test]
    fn test_decode_pdf_text_empty_input() {
        let glyphs = decode_pdf_text(&[], None);
        assert_eq!(glyphs.len(), 0);
        
        let font = create_mock_simple_font();
        let glyphs = decode_pdf_text(&[], Some(&font));
        assert_eq!(glyphs.len(), 0);
        
        let type0_font = create_mock_type0_font();
        let glyphs = decode_pdf_text(&[], Some(&type0_font));
        assert_eq!(glyphs.len(), 0);
    }

    #[test]
    fn test_decode_pdf_text_type0_font_2byte_iteration() {
        let font = create_mock_type0_font();
        let bytes = [0x00, 0x41, 0x00, 0x42, 0x00, 0x43]; // 2-byte codes for A, B, C
        let glyphs = decode_pdf_text(&bytes, Some(&font));
        
        // The glyphs should be processed in 2-byte chunks
        assert_eq!(glyphs.len(), 3);
        assert_eq!(glyphs[0].char_code, 0x0041);
        assert_eq!(glyphs[0].unicode, "A");
        assert_eq!(glyphs[0].bytes_consumed, 2);
        assert_eq!(glyphs[1].char_code, 0x0042);
        assert_eq!(glyphs[1].unicode, "B");
        assert_eq!(glyphs[1].bytes_consumed, 2);
        assert_eq!(glyphs[2].char_code, 0x0043);
        assert_eq!(glyphs[2].unicode, "C");
        assert_eq!(glyphs[2].bytes_consumed, 2);
    }

    // Helper functions to create mock fonts for testing
    fn create_mock_simple_font() -> FontInfo {
        use crate::fonts::Encoding;
        use std::collections::HashMap;
        
        FontInfo {
            base_font: "TestFont".to_string(),
            subtype: "Type1".to_string(),
            encoding: Encoding::Identity,
            to_unicode: None,
            font_weight: None,
            flags: None,
            stem_v: None,
            embedded_font_data: None,
            truetype_cmap: std::sync::OnceLock::new(),
            is_truetype_font: false,
            cid_to_gid_map: None,
            cid_system_info: None,
            cid_font_type: None,
            widths: None,
            first_char: Some(0),
            last_char: Some(255),
            default_width: 1000.0,
            cid_widths: None,
            cid_default_width: 1000.0,
            multi_char_map: HashMap::new(),
            byte_to_char_table: {
                let mut table = ['\0'; 256];
                // Map ASCII range
                for i in 0x20..=0x7E {
                    table[i] = char::from(i as u8);
                }
                let once_lock = std::sync::OnceLock::new();
                let _ = once_lock.set(table);
                once_lock
            },
            byte_to_width_table: {
                let table = [1000.0; 256];
                let once_lock = std::sync::OnceLock::new();
                let _ = once_lock.set(table);
                once_lock
            },
        }
    }

    fn create_mock_type0_font() -> FontInfo {
        use crate::fonts::Encoding;
        use std::collections::HashMap;
        
        FontInfo {
            base_font: "TestType0Font".to_string(),
            subtype: "Type0".to_string(),
            encoding: Encoding::Identity,
            to_unicode: None,
            font_weight: None,
            flags: None,
            stem_v: None,
            embedded_font_data: None,
            truetype_cmap: std::sync::OnceLock::new(),
            is_truetype_font: false,
            cid_to_gid_map: None,
            cid_system_info: None,
            cid_font_type: None,
            widths: None,
            first_char: Some(0),
            last_char: Some(65535),
            default_width: 1000.0,
            cid_widths: None,
            cid_default_width: 1000.0,
            multi_char_map: HashMap::new(),
            byte_to_char_table: {
                let table = ['\0'; 256]; // Not used for Type0 fonts
                let once_lock = std::sync::OnceLock::new();
                let _ = once_lock.set(table);
                once_lock
            },
            byte_to_width_table: {
                let table = [1000.0; 256];
                let once_lock = std::sync::OnceLock::new();
                let _ = once_lock.set(table);
                once_lock
            },
        }
    }
}
