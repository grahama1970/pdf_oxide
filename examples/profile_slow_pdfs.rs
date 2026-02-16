//! Profile slow PDFs - measures per-page extract_text timing with breakdown

use pdf_oxide::document::PdfDocument;
use std::env;
use std::time::Instant;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("Usage: {} <pdf_file> [page_number]", args[0]);
        std::process::exit(1);
    }

    let pdf_path = &args[1];
    let specific_page: Option<usize> = args.get(2).and_then(|s| s.parse().ok());

    let t0 = Instant::now();
    let mut doc = PdfDocument::open(pdf_path)?;
    let open_time = t0.elapsed();
    eprintln!("Open: {:.1}ms", open_time.as_secs_f64() * 1000.0);

    let page_count = doc.page_count()?;
    eprintln!("Pages: {}", page_count);

    // If specific page requested, profile just that page
    if let Some(page_idx) = specific_page {
        eprintln!("\n=== Profile page {} ===", page_idx);

        let t2 = Instant::now();
        let text = doc.extract_text(page_idx)?;
        let extract_time = t2.elapsed();
        eprintln!(
            "  extract_text: {:.1}ms ({} chars)",
            extract_time.as_secs_f64() * 1000.0,
            text.len()
        );

        // Second call (cached fonts)
        let t3 = Instant::now();
        let text2 = doc.extract_text(page_idx)?;
        let extract_time2 = t3.elapsed();
        eprintln!(
            "  extract_text (2nd): {:.1}ms ({} chars)",
            extract_time2.as_secs_f64() * 1000.0,
            text2.len()
        );

        return Ok(());
    }

    // Otherwise, per-page timing
    let mut total_chars = 0usize;
    let mut total_time = std::time::Duration::ZERO;

    for page_idx in 0..page_count {
        let t1 = Instant::now();
        let text = doc.extract_text(page_idx)?;
        let elapsed = t1.elapsed();
        total_time += elapsed;
        let chars = text.len();
        total_chars += chars;

        if elapsed.as_millis() > 100 {
            eprintln!(
                "  Page {}/{}: {:.1}ms ({} chars) *** SLOW ***",
                page_idx,
                page_count,
                elapsed.as_secs_f64() * 1000.0,
                chars
            );
        } else {
            eprintln!(
                "  Page {}/{}: {:.1}ms ({} chars)",
                page_idx,
                page_count,
                elapsed.as_secs_f64() * 1000.0,
                chars
            );
        }
    }

    eprintln!(
        "\nTotal: {:.1}ms for {} pages, {} chars",
        total_time.as_secs_f64() * 1000.0,
        page_count,
        total_chars
    );

    Ok(())
}
