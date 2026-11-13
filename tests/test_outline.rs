//! Integration tests for outline handling.
//!
//! Does very basic testing of outline parsing.

use pdf_oxide::document::PdfDocument;
use pdf_oxide::outline::Destination::PageIndex;
use pdf_oxide::outline::OutlineItem;

const SIMPLE_PDF_PATH: &str = "tests/fixtures/simple.pdf";
const OUTLINE_PDF_PATH: &str = "tests/fixtures/outline.pdf";

fn build_outline_pdf_with_named_destinations() -> Vec<u8> {
    let objects = [
        "<< /Type /Catalog /Pages 3 0 R /Outlines 2 0 R /Names 9 0 R /Dests 12 0 R >>",
        "<< /Type /Outlines /First 5 0 R /Last 7 0 R /Count 3 >>",
        "<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 3 0 R /MediaBox [0 0 612 792] >>",
        "<< /Title 8 0 R /A 14 0 R /Parent 2 0 R /Next 6 0 R >>",
        "<< /Title <804E6F7465> /Dest (chapter.two) /Parent 2 0 R /Prev 5 0 R /Next 7 0 R >>",
        "<< /Title (Legacy) /Dest /legacy /Parent 2 0 R /Prev 6 0 R >>",
        "<FEFF0049006E00740072006F00640075006300740069006F006E>",
        "<< /Dests 10 0 R >>",
        "<< /Kids [11 0 R] >>",
        "<< /Limits [(chapter.one) (chapter.two)] /Names [(chapter.one) 13 0 R (chapter.two) [4 0 R /XYZ null null null]] >>",
        "<< /legacy [4 0 R /Fit] >>",
        "<< /D [4 0 R /FitH null] >>",
        "<< /S /GoTo /D (chapter.one) >>",
    ];
    build_pdf(&objects)
}

fn build_pdf(objects: &[&str]) -> Vec<u8> {
    let mut pdf = b"%PDF-1.7\n".to_vec();
    let mut offsets = vec![0usize];
    for (index, object) in objects.iter().enumerate() {
        offsets.push(pdf.len());
        pdf.extend_from_slice(format!("{} 0 obj\n{}\nendobj\n", index + 1, object).as_bytes());
    }

    let xref_offset = pdf.len();
    pdf.extend_from_slice(format!("xref\n0 {}\n", offsets.len()).as_bytes());
    pdf.extend_from_slice(b"0000000000 65535 f \r\n");
    for offset in offsets.iter().skip(1) {
        pdf.extend_from_slice(format!("{offset:010} 00000 n \r\n").as_bytes());
    }
    pdf.extend_from_slice(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n",
            offsets.len(),
            xref_offset
        )
        .as_bytes(),
    );
    pdf
}

#[test]
fn test_outline_missing() {
    let mut pdf = PdfDocument::open(SIMPLE_PDF_PATH).expect("Failed to open simple.pdf");
    let outline = pdf.get_outline().expect("Failed to get outline");
    assert!(outline.is_none(), "Outline should should not be found");
}

#[test]
fn test_outline_present() -> Result<(), Box<dyn std::error::Error>> {
    let mut pdf = PdfDocument::open(OUTLINE_PDF_PATH).expect("Failed to open outline.pdf");
    let outline = pdf
        .get_outline()
        .expect("Failed to get outline")
        .expect("Outline should have been found");

    let [embedded, indirect] = outline
        .try_into()
        .expect("Outline should have had exactly two items");

    match embedded {
        OutlineItem {
            title,
            dest: Some(PageIndex(0)),
            children,
        } => {
            assert_eq!(title, "Outline with Embedded Action");
            assert_eq!(children.len(), 0, "Outline items should have no children");
        },
        _ => return Err("Expected destination to be first page".into()),
    }

    match indirect {
        OutlineItem {
            title,
            dest: Some(PageIndex(0)),
            children,
        } => {
            assert_eq!(title, "Outline with Indirectly Referenced Action");
            assert_eq!(children.len(), 0, "Outline items should have no children");
        },
        _ => return Err("Expected destination to be first page".into()),
    }

    Ok(())
}

#[test]
fn test_indirect_titles_and_named_destinations() {
    let mut pdf = PdfDocument::open_from_bytes(build_outline_pdf_with_named_destinations())
        .expect("open generated outline PDF");
    let outline = pdf
        .get_outline()
        .expect("parse generated outline")
        .expect("outline present");

    assert_eq!(outline.len(), 3);
    assert_eq!(outline[0].title, "Introduction");
    assert!(matches!(outline[0].dest, Some(PageIndex(0))));
    assert_eq!(outline[1].title, "•Note");
    assert!(matches!(outline[1].dest, Some(PageIndex(0))));
    assert_eq!(outline[2].title, "Legacy");
    assert!(matches!(outline[2].dest, Some(PageIndex(0))));
}

/// Catalog + one-page tree + single outline item whose /Dest is `dest`,
/// followed by `extra` objects starting at object 6.
fn build_single_item_outline_pdf(catalog_extra: &str, dest: &str, extra: &[String]) -> Vec<u8> {
    let catalog = format!("<< /Type /Catalog /Pages 3 0 R /Outlines 2 0 R {catalog_extra} >>");
    let item = format!("<< /Title (Item) /Parent 2 0 R {dest} >>");
    let mut objects: Vec<String> = vec![
        catalog,
        "<< /Type /Outlines /First 5 0 R /Last 5 0 R /Count 1 >>".to_string(),
        "<< /Type /Pages /Kids [4 0 R] /Count 1 >>".to_string(),
        "<< /Type /Page /Parent 3 0 R /MediaBox [0 0 612 792] >>".to_string(),
        item,
    ];
    objects.extend(extra.iter().cloned());
    let refs: Vec<&str> = objects.iter().map(String::as_str).collect();
    build_pdf(&refs)
}

fn single_outline_item(pdf: Vec<u8>) -> OutlineItem {
    let mut doc = PdfDocument::open_from_bytes(pdf).expect("open generated PDF");
    let outline = doc
        .get_outline()
        .expect("outline must not error")
        .expect("outline present");
    assert_eq!(outline.len(), 1);
    outline.into_iter().next().unwrap()
}

#[test]
fn test_self_referential_destination_dictionary_does_not_crash() {
    // 6 0 obj << /D 6 0 R >>: the /D wrapper points back at itself.
    let pdf = build_single_item_outline_pdf("", "/Dest 6 0 R", &["<< /D 6 0 R >>".to_string()]);
    let item = single_outline_item(pdf);
    assert_eq!(item.title, "Item");
    assert!(item.dest.is_none());
}

#[test]
fn test_reference_cycle_destination_does_not_crash() {
    // 6 0 R -> 7 0 R -> 6 0 R
    let pdf = build_single_item_outline_pdf(
        "",
        "/Dest 6 0 R",
        &["7 0 R".to_string(), "6 0 R".to_string()],
    );
    let item = single_outline_item(pdf);
    assert_eq!(item.title, "Item");
    assert!(item.dest.is_none());
}

#[test]
fn test_deep_name_tree_is_capped_and_outline_returned() {
    // /Names /Dests root is a chain of 200 nested /Kids nodes; the leaf is
    // beyond the depth cap, so the name stays unresolved but nothing fails.
    let depth = 200usize;
    let mut extra = vec!["<< /Dests 7 0 R >>".to_string()];
    for i in 0..depth {
        extra.push(format!("<< /Kids [{} 0 R] >>", 8 + i));
    }
    extra.push("<< /Names [(deep) [4 0 R /Fit]] >>".to_string());
    let pdf = build_single_item_outline_pdf("/Names 6 0 R", "/Dest (deep)", &extra);
    let item = single_outline_item(pdf);
    assert_eq!(item.title, "Item");
    assert!(!matches!(item.dest, Some(PageIndex(_))));
}

#[test]
fn test_shallow_name_tree_still_resolves() {
    let extra = vec![
        "<< /Dests 7 0 R >>".to_string(),
        "<< /Kids [8 0 R] >>".to_string(),
        "<< /Names [(near) [4 0 R /Fit]] >>".to_string(),
    ];
    let pdf = build_single_item_outline_pdf("/Names 6 0 R", "/Dest (near)", &extra);
    let item = single_outline_item(pdf);
    assert!(matches!(item.dest, Some(PageIndex(0))));
}
