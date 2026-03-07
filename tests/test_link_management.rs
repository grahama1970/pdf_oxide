//! Integration tests for link management (Milestone 2b).

use pdf_oxide::editor::{DocumentEditor, EditableDocument};
use pdf_oxide::geometry::Rect;
use pdf_oxide::writer::{LinkAnnotation, PdfWriter};
use pdf_oxide::PdfDocument;
use std::fs;
use tempfile::tempdir;

/// Helper: create a PDF with a link annotation.
fn create_pdf_with_link(uri: &str) -> Vec<u8> {
    let mut writer = PdfWriter::new();
    let mut page = writer.add_page(612.0, 792.0);
    page.add_text("Click here", 72.0, 720.0, "Helvetica", 12.0);
    page.add_link(LinkAnnotation::uri(
        Rect::new(72.0, 710.0, 100.0, 20.0),
        uri,
    ));
    writer.finish().unwrap()
}

#[test]
fn test_get_links_returns_link_annotations() {
    let dir = tempdir().unwrap();
    let input_path = dir.path().join("input.pdf");

    let pdf_bytes = create_pdf_with_link("https://example.com");
    fs::write(&input_path, &pdf_bytes).unwrap();

    let mut editor = DocumentEditor::open(&input_path).unwrap();
    let links = editor.get_links(0).unwrap();
    assert!(!links.is_empty(), "Should find at least one link annotation");

    let (idx, annot) = &links[0];
    assert_eq!(*idx, 0);
    assert_eq!(
        annot.subtype_enum,
        pdf_oxide::annotation_types::AnnotationSubtype::Link
    );
}

#[test]
fn test_update_link_uri() {
    let dir = tempdir().unwrap();
    let input_path = dir.path().join("input.pdf");
    let output_path = dir.path().join("output.pdf");

    let pdf_bytes = create_pdf_with_link("https://old-url.com");
    fs::write(&input_path, &pdf_bytes).unwrap();

    // Modify link URI
    {
        let mut editor = DocumentEditor::open(&input_path).unwrap();
        let links = editor.get_links(0).unwrap();
        assert!(!links.is_empty());
        let (idx, _) = &links[0];
        editor.update_link_uri(0, *idx, "https://new-url.com").unwrap();
        editor.save(&output_path).unwrap();
    }

    // Verify the output contains the new URI
    let output_content = fs::read(&output_path).unwrap();
    let output_str = String::from_utf8_lossy(&output_content);
    assert!(
        output_str.contains("new-url.com"),
        "Output should contain the new URI"
    );

    // Verify the output is a valid PDF
    let mut pdf = PdfDocument::open(&output_path).unwrap();
    assert_eq!(pdf.page_count().unwrap(), 1);
}

#[test]
fn test_delete_link() {
    let dir = tempdir().unwrap();
    let input_path = dir.path().join("input.pdf");
    let output_path = dir.path().join("output.pdf");

    let pdf_bytes = create_pdf_with_link("https://example.com");
    fs::write(&input_path, &pdf_bytes).unwrap();

    // Delete the link
    {
        let mut editor = DocumentEditor::open(&input_path).unwrap();
        let links = editor.get_links(0).unwrap();
        assert!(!links.is_empty());
        let (idx, _) = &links[0];
        editor.delete_link(0, *idx).unwrap();
        editor.save(&output_path).unwrap();
    }

    // Verify the link is gone
    let mut editor2 = DocumentEditor::open(&output_path).unwrap();
    let links = editor2.get_links(0).unwrap();
    assert!(links.is_empty(), "Link should have been deleted");
}
