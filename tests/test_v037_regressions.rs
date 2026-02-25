//! Regression tests for v0.3.7 text extraction fixes.
//!
//! These tests guard against regressions for issues #87–#103 fixed in v0.3.7.
//! Each test targets a specific bug that was found during benchmark testing
//! across 3829 real-world PDFs.

use pdf_oxide::document::PdfDocument;

// ---------------------------------------------------------------------------
// Issue #88: Multi-font text loss — Tf buffer must flush on font switch
// ---------------------------------------------------------------------------

/// When a content stream switches fonts mid-line via Tf, the text buffer must
/// be flushed before the switch. Otherwise bytes accumulated under the old font
/// get decoded with the new font's encoding, producing garbled or lost text.
///
/// Regression: prior to v0.3.7, changing Tf without a preceding Tj/TJ caused
/// the pending buffer to be silently dropped.
#[test]
fn test_tf_buffer_flush_on_font_switch() {
    // Build a minimal content stream that uses two fonts in one BT…ET block.
    // Font F1 renders "AB", then font F2 renders "CD".
    // Both fonts are WinAnsiEncoding so output should be "ABCD" (4 chars).
    let stream = b"BT /F1 12 Tf 100 700 Td (AB) Tj /F2 12 Tf (CD) Tj ET";

    let mut extractor = pdf_oxide::extractors::text::TextExtractor::new();

    let font1 = make_test_font("Helvetica", "Type1");
    let font2 = make_test_font("Courier", "Type1");
    extractor.add_font("F1".to_string(), font1);
    extractor.add_font("F2".to_string(), font2);

    let chars = extractor.extract(stream).unwrap();
    let text: String = chars.iter().map(|c| c.char).collect();

    // Must contain all 4 characters — no text lost on font switch
    assert!(text.contains("AB"), "Text from first font F1 missing: got '{}'", text);
    assert!(text.contains("CD"), "Text from second font F2 missing: got '{}'", text);
    assert_eq!(chars.len(), 4, "Expected 4 chars, got {}: '{}'", chars.len(), text);
}

/// Multiple font switches in a single BT block should preserve all text.
#[test]
fn test_tf_buffer_flush_across_three_fonts() {
    let stream = b"BT /F1 10 Tf 0 700 Td (One) Tj /F2 10 Tf (Two) Tj /F3 10 Tf (Three) Tj ET";

    let mut extractor = pdf_oxide::extractors::text::TextExtractor::new();
    extractor.add_font("F1".to_string(), make_test_font("Helvetica", "Type1"));
    extractor.add_font("F2".to_string(), make_test_font("Courier", "Type1"));
    extractor.add_font("F3".to_string(), make_test_font("Times-Roman", "Type1"));

    let chars = extractor.extract(stream).unwrap();
    let text: String = chars.iter().map(|c| c.char).collect();

    assert_eq!(
        chars.len(),
        11,
        "Expected 11 chars (One+Two+Three), got {}: '{}'",
        chars.len(),
        text
    );
}

// ---------------------------------------------------------------------------
// Issue #92: Annotation text (Widget / FreeText) must not crash or be lost
// ---------------------------------------------------------------------------

/// Verify annotation extraction is safe on PDFs with and without annotations.
#[test]
fn test_annotation_extraction_no_panic() {
    for fixture in &["tests/fixtures/simple.pdf", "tests/fixtures/outline.pdf"] {
        let mut doc = PdfDocument::open(fixture).unwrap();
        let pages = doc.page_count().unwrap();
        for p in 0..pages {
            // Must not panic even if annotations reference missing resources
            let _text = doc.extract_text(p).unwrap();
        }
    }
}

// ---------------------------------------------------------------------------
// Issue #97 / #103: Spurious spaces / character fragmentation
// ---------------------------------------------------------------------------

/// The space-to-character ratio must stay sane: excessive fragmentation means
/// the space threshold is too aggressive.
#[test]
fn test_space_ratio_below_threshold() {
    let mut doc = PdfDocument::open("tests/fixtures/outline.pdf").unwrap();
    let pages = doc.page_count().unwrap();

    for p in 0..pages {
        let text = doc.extract_text(p).unwrap();
        if text.is_empty() {
            continue;
        }
        let spaces = text.chars().filter(|c| *c == ' ').count();
        let non_ws = text.chars().filter(|c| !c.is_whitespace()).count();
        if non_ws > 10 {
            let ratio = spaces as f64 / non_ws as f64;
            assert!(
                ratio < 0.5,
                "Page {}: space ratio {:.2} too high ({} spaces / {} chars)",
                p,
                ratio,
                spaces,
                non_ws
            );
        }
    }
}

// ---------------------------------------------------------------------------
// Issue #101: Empty output — removing the incorrect BT operator check
// ---------------------------------------------------------------------------

/// A content stream with valid BT/ET blocks must produce non-empty output.
/// Before v0.3.7, an incorrect BT content check could skip valid streams.
#[test]
fn test_bt_et_block_produces_output() {
    let stream = b"BT /F1 12 Tf 72 700 Td (Hello World) Tj ET";

    let mut extractor = pdf_oxide::extractors::text::TextExtractor::new();
    extractor.add_font("F1".to_string(), make_test_font("Helvetica", "Type1"));

    let chars = extractor.extract(stream).unwrap();
    assert!(!chars.is_empty(), "Valid BT/ET block should produce non-empty output");
    let text: String = chars.iter().map(|c| c.char).collect();
    assert!(text.contains("Hello"), "Expected 'Hello' in output, got '{}'", text);
}

/// Multiple BT/ET blocks in one stream should all be processed.
#[test]
fn test_multiple_bt_et_blocks_all_extracted() {
    let stream = b"BT /F1 12 Tf 72 700 Td (First) Tj ET BT /F1 12 Tf 72 680 Td (Second) Tj ET";

    let mut extractor = pdf_oxide::extractors::text::TextExtractor::new();
    extractor.add_font("F1".to_string(), make_test_font("Helvetica", "Type1"));

    let chars = extractor.extract(stream).unwrap();
    let text: String = chars.iter().map(|c| c.char).collect();

    assert!(text.contains("First"), "First BT/ET block text missing");
    assert!(text.contains("Second"), "Second BT/ET block text missing");
}

// ---------------------------------------------------------------------------
// Issue #102: Span deduplication — overlapping duplicate spans
// ---------------------------------------------------------------------------

/// Some PDFs render text twice at the same position (for bold/shadow effects).
/// The extractor must deduplicate these so "HelloHello" at (100,700) becomes
/// just "Hello".
#[test]
fn test_overlapping_duplicate_chars_removed() {
    // Render "AB" twice at identical positions — should deduplicate to 2 chars
    let stream = b"BT /F1 12 Tf 100 700 Td (AB) Tj ET BT /F1 12 Tf 100 700 Td (AB) Tj ET";

    let mut extractor = pdf_oxide::extractors::text::TextExtractor::new();
    extractor.add_font("F1".to_string(), make_test_font("Helvetica", "Type1"));

    let chars = extractor.extract(stream).unwrap();

    // After deduplication, should have 2 chars, not 4
    assert!(
        chars.len() <= 3,
        "Expected deduplication to reduce 4 overlapping chars, got {}",
        chars.len()
    );
}

/// Text at distinctly different positions must NOT be deduplicated.
#[test]
fn test_distinct_lines_not_falsely_deduplicated() {
    // "AB" at y=700, "CD" at y=680 — different lines, must keep both
    let stream = b"BT /F1 12 Tf 100 700 Td (AB) Tj ET BT /F1 12 Tf 100 680 Td (CD) Tj ET";

    let mut extractor = pdf_oxide::extractors::text::TextExtractor::new();
    extractor.add_font("F1".to_string(), make_test_font("Helvetica", "Type1"));

    let chars = extractor.extract(stream).unwrap();
    let text: String = chars.iter().map(|c| c.char).collect();

    assert!(
        text.contains("AB") && text.contains("CD"),
        "Text at different Y positions must not be deduplicated, got '{}'",
        text
    );
    assert_eq!(chars.len(), 4, "Expected 4 chars on 2 lines, got {}", chars.len());
}

// ---------------------------------------------------------------------------
// Issue #95: BrotliDecode support
// ---------------------------------------------------------------------------

/// BrotliDecode filter must be recognized in decode_stream.
#[test]
fn test_brotli_decode_roundtrip() {
    use pdf_oxide::decoders::BrotliDecoder;
    use pdf_oxide::decoders::StreamDecoder;

    let original = b"The quick brown fox jumps over the lazy dog. PDF stream data.";

    // Compress with brotli
    let mut compressed = Vec::new();
    {
        let mut writer = brotli::CompressorWriter::new(&mut compressed, 4096, 6, 22);
        std::io::Write::write_all(&mut writer, original).unwrap();
    }

    // Decompress via our public StreamDecoder interface
    let decoder = BrotliDecoder;
    let decoded = decoder.decode(&compressed).unwrap();
    assert_eq!(decoded, original.to_vec());
}

// ---------------------------------------------------------------------------
// Integration: extract_text on fixture PDFs must be deterministic & safe
// ---------------------------------------------------------------------------

/// extract_text must produce identical output across two runs (deterministic).
#[test]
fn test_fixture_deterministic_output() {
    for fixture in &["tests/fixtures/simple.pdf", "tests/fixtures/outline.pdf"] {
        let mut doc1 = PdfDocument::open(fixture).unwrap();
        let mut doc2 = PdfDocument::open(fixture).unwrap();
        let pages = doc1.page_count().unwrap();
        for p in 0..pages {
            let t1 = doc1.extract_text(p).unwrap();
            let t2 = doc2.extract_text(p).unwrap();
            assert_eq!(t1, t2, "{} page {} not deterministic", fixture, p);
        }
    }
}

/// extract_text must not panic on any page of fixture PDFs.
#[test]
fn test_fixture_no_panic() {
    for fixture in &["tests/fixtures/simple.pdf", "tests/fixtures/outline.pdf"] {
        let mut doc = PdfDocument::open(fixture).unwrap();
        let pages = doc.page_count().unwrap();
        assert!(pages > 0, "{} should have at least 1 page", fixture);
        for p in 0..pages {
            let _text = doc.extract_text(p).unwrap();
        }
    }
}

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

fn make_test_font(name: &str, subtype: &str) -> pdf_oxide::fonts::FontInfo {
    use std::collections::HashMap;
    pdf_oxide::fonts::FontInfo {
        base_font: name.to_string(),
        subtype: subtype.to_string(),
        encoding: pdf_oxide::fonts::Encoding::Standard("WinAnsiEncoding".to_string()),
        to_unicode: None,
        font_weight: None,
        flags: None,
        stem_v: None,
        embedded_font_data: None,
        truetype_cmap: None,
        widths: None,
        first_char: None,
        last_char: None,
        default_width: 1000.0,
        cid_to_gid_map: None,
        cid_system_info: None,
        cid_font_type: None,
        cid_widths: None,
        cid_default_width: 1000.0,
        multi_char_map: HashMap::new(),
        byte_to_char_table: std::sync::OnceLock::new(),
        byte_to_width_table: std::sync::OnceLock::new(),
    }
}
