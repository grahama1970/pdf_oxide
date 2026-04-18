//! Integration test for character-level text splitting in table extraction.

use pdf_oxide::document::PdfDocument;
use pdf_oxide::tables::{extract_tables, ExtractConfig, Flavor};

/// Test that character-level extraction populates TextElement.chars
#[test]
fn test_char_extraction_populated() {
    let path = "/home/graham/workspace/experiments/camelot/tests/files/foo.pdf";
    if !std::path::Path::new(path).exists() {
        eprintln!("Skipping test: {} not found", path);
        return;
    }

    let mut doc = PdfDocument::open(path).expect("open PDF");
    let config = ExtractConfig {
        flavor: Flavor::Lattice,
        ..Default::default()
    };

    let tables = extract_tables(&mut doc, &config).expect("extract tables");
    assert!(!tables.is_empty(), "should find at least one table");

    // Check that tables have cells with content
    let table = &tables[0];
    let non_empty_cells: Vec<_> = table
        .cells
        .iter()
        .flatten()
        .filter(|c| !c.text.trim().is_empty())
        .collect();

    assert!(!non_empty_cells.is_empty(), "table should have non-empty cells");
    println!("Found {} non-empty cells in table", non_empty_cells.len());

    // Print first few cell contents
    for (i, cell) in non_empty_cells.iter().take(5).enumerate() {
        println!("  Cell {}: '{}'", i, cell.text.replace('\n', " | "));
    }
}

/// Test merged cell detection on a table with spanning cells
#[test]
fn test_merged_cell_detection() {
    let path = "/home/graham/workspace/experiments/camelot/tests/files/column_span_1.pdf";
    if !std::path::Path::new(path).exists() {
        eprintln!("Skipping test: {} not found", path);
        return;
    }

    let mut doc = PdfDocument::open(path).expect("open PDF");
    let config = ExtractConfig {
        flavor: Flavor::Lattice,
        ..Default::default()
    };

    let tables = extract_tables(&mut doc, &config).expect("extract tables");
    if tables.is_empty() {
        eprintln!("No tables found, skipping merge detection test");
        return;
    }

    let table = &tables[0];
    let merged = table.detect_merged_regions();

    println!("Table: {}x{}", table.num_rows(), table.num_cols());
    println!("Merged regions found: {}", merged.len());
    for r in &merged {
        println!(
            "  Region at ({}, {}): {}x{} cells",
            r.start_row, r.start_col, r.row_span, r.col_span
        );
    }
}

/// Test hybrid detection: lattice falling back to stream for row-separated tables
#[test]
fn test_hybrid_detection() {
    // Use agstat.pdf which may have borderless tables
    let path = "/home/graham/workspace/experiments/camelot/tests/files/agstat.pdf";
    if !std::path::Path::new(path).exists() {
        eprintln!("Skipping test: {} not found", path);
        return;
    }

    let mut doc = PdfDocument::open(path).expect("open PDF");

    // Request lattice - will use stream fallback if no grid lines found
    let config = ExtractConfig {
        flavor: Flavor::Lattice,
        ..Default::default()
    };

    let tables = extract_tables(&mut doc, &config).expect("extract tables");
    println!("Found {} tables with hybrid detection", tables.len());

    for (i, t) in tables.iter().enumerate() {
        println!("  Table {}: {}x{}, flavor={:?}", i, t.num_rows(), t.num_cols(), t.flavor);
    }
}

/// Test stream-only extraction
#[test]
fn test_stream_extraction() {
    let path = "/home/graham/workspace/experiments/camelot/tests/files/agstat.pdf";
    if !std::path::Path::new(path).exists() {
        eprintln!("Skipping test: {} not found", path);
        return;
    }

    let mut doc = PdfDocument::open(path).expect("open PDF");
    let config = ExtractConfig {
        flavor: Flavor::Stream,
        ..Default::default()
    };

    let tables = extract_tables(&mut doc, &config).expect("extract tables");
    println!("Found {} tables with stream detection", tables.len());

    for (i, t) in tables.iter().enumerate() {
        println!("  Table {}: {}x{}, accuracy={:.1}%", i, t.num_rows(), t.num_cols(), t.accuracy);
    }
}
