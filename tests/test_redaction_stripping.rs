//! Integration tests for true content-stripping redaction.
//!
//! These tests verify that applying redactions physically removes content
//! from the content stream, not just visually covering it.

use pdf_oxide::editor::{DocumentEditor, EditableDocument};
use pdf_oxide::geometry::Rect;
use pdf_oxide::writer::{PdfWriter, RedactAnnotation};
use pdf_oxide::PdfDocument;
use std::fs;
use tempfile::tempdir;

/// Helper: create a PDF with known text and a Redact annotation.
fn create_pdf_with_redaction(visible_text: &str, secret_text: &str) -> Vec<u8> {
    let mut writer = PdfWriter::new();
    let mut page = writer.add_page(612.0, 792.0);

    // Add visible text at top of page
    page.add_text(visible_text, 72.0, 720.0, "Helvetica", 12.0);

    // Add secret text lower on the page
    page.add_text(secret_text, 72.0, 500.0, "Helvetica", 12.0);

    // Add a Redact annotation covering the secret text area
    // Rect::new(x, y, width, height) — y=490, height=30 covers y=490..520
    page.add_redact(RedactAnnotation::new(Rect::new(60.0, 490.0, 350.0, 30.0)));

    writer.finish().unwrap()
}

#[test]
fn test_true_redaction_removes_text_from_bytes() {
    let dir = tempdir().unwrap();
    let input_path = dir.path().join("input.pdf");
    let output_path = dir.path().join("output.pdf");

    // Create PDF with visible and secret text
    let pdf_bytes = create_pdf_with_redaction("PUBLIC INFORMATION", "TOP SECRET DATA");
    fs::write(&input_path, &pdf_bytes).unwrap();

    // Verify the input contains the secret text in raw bytes
    let input_content = fs::read(&input_path).unwrap();
    assert!(
        input_content
            .windows(b"TOP SECRET DATA".len())
            .any(|w| w == b"TOP SECRET DATA"),
        "Input PDF should contain the secret text"
    );

    // Apply redactions
    {
        let mut editor = DocumentEditor::open(&input_path).unwrap();
        editor.apply_page_redactions(0).unwrap();
        editor.save(&output_path).unwrap();
    }

    // Verify the output does NOT contain the secret text
    let output_content = fs::read(&output_path).unwrap();
    assert!(
        !output_content
            .windows(b"TOP SECRET DATA".len())
            .any(|w| w == b"TOP SECRET DATA"),
        "Output PDF should NOT contain the secret text in raw bytes"
    );

    // Verify the output is a valid PDF that can be opened
    let mut pdf = PdfDocument::open(&output_path).unwrap();
    assert_eq!(pdf.page_count().unwrap(), 1);
}

#[test]
fn test_redaction_overlay_covers_area() {
    let dir = tempdir().unwrap();
    let input_path = dir.path().join("input.pdf");
    let output_path = dir.path().join("output.pdf");

    let pdf_bytes = create_pdf_with_redaction("VISIBLE", "HIDDEN");
    fs::write(&input_path, &pdf_bytes).unwrap();

    {
        let mut editor = DocumentEditor::open(&input_path).unwrap();
        editor.apply_page_redactions(0).unwrap();
        editor.save(&output_path).unwrap();
    }

    // Verify the output contains redaction overlay (filled rectangle)
    let output_content = fs::read(&output_path).unwrap();
    let output_str = String::from_utf8_lossy(&output_content);
    // The overlay should contain "re f" (rectangle fill)
    assert!(
        output_str.contains("re f") || output_str.contains("re\nf"),
        "Output should contain redaction overlay rectangle"
    );
}

#[test]
fn test_redaction_with_no_annotations_is_noop() {
    let dir = tempdir().unwrap();
    let input_path = dir.path().join("input.pdf");
    let output_path = dir.path().join("output.pdf");

    // Create a simple PDF with no redact annotations
    let mut writer = PdfWriter::new();
    let mut page = writer.add_page(612.0, 792.0);
    page.add_text("Normal text", 72.0, 720.0, "Helvetica", 12.0);
    let pdf_bytes = writer.finish().unwrap();
    fs::write(&input_path, &pdf_bytes).unwrap();

    {
        let mut editor = DocumentEditor::open(&input_path).unwrap();
        editor.apply_page_redactions(0).unwrap();
        editor.save(&output_path).unwrap();
    }

    // Verify text is still present
    let mut pdf = PdfDocument::open(&output_path).unwrap();
    let text = pdf.extract_text(0).unwrap();
    assert!(text.contains("Normal"), "Text should be preserved when no redactions");
}
