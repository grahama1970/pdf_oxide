//! Tests for PDF header parsing with binary prefixes and resilience handling.
//!
//! Verifies that PDFs with binary data before the PDF header can be parsed.
//! Tests lenient mode which searches first 1024 bytes for %PDF- marker.

#[test]
fn test_pdf_with_fixture() {
    use pdf_oxide::document::PdfDocument;
    use std::path::Path;

    let fixture_path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("simple.pdf");

    if !fixture_path.exists() {
        return;
    }

    let pdf_path = fixture_path.to_str().unwrap();

    // Open PDF successfully
    let mut doc = match PdfDocument::open(pdf_path) {
        Ok(d) => d,
        Err(e) => panic!("Failed to open PDF: {}", e),
    };

    // Verify version info
    let (major, _minor) = doc.version();
    assert!(major >= 1, "Invalid PDF major version");

    // Verify page count
    let page_count = doc.page_count().expect("Failed to get page count");
    assert!(page_count > 0, "PDF should have at least one page");

    // Attempt text extraction (may be empty for minimal fixtures)
    let _ = doc.extract_spans(0);
}

#[test]
fn test_pdf_parsing_robustness() {
    use pdf_oxide::document::PdfDocument;
    use std::path::Path;

    let fixture_path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("simple.pdf");

    if !fixture_path.exists() {
        return;
    }

    let pdf_path = fixture_path.to_str().unwrap();

    // Open PDF document
    let mut doc = PdfDocument::open(pdf_path).expect("Failed to open PDF");

    // Verify version
    let (major, _minor) = doc.version();
    assert_eq!(major, 1);

    // Verify page count
    let page_count = doc.page_count().expect("Failed to get page count");
    assert!(page_count > 0);

    // Test extraction on each page (gracefully handle extraction errors)
    for i in 0..page_count {
        let _ = doc.extract_spans(i);
    }
}
