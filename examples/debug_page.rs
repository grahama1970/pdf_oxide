use pdf_oxide::document::PdfDocument;
use std::env;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    env_logger::init();

    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("Usage: {} <pdf_file> [page_index]", args[0]);
        std::process::exit(1);
    }

    let pdf_path = &args[1];
    let specific_page: Option<usize> = args.get(2).and_then(|s| s.parse().ok());
    let mut doc = PdfDocument::open(pdf_path)?;
    let page_count = doc.page_count()?;
    eprintln!("Pages: {}", page_count);

    let pages: Vec<usize> = if let Some(p) = specific_page {
        vec![p]
    } else {
        (0..page_count).collect()
    };

    let mut total = 0;
    for p in pages {
        match doc.extract_text(p) {
            Ok(text) => {
                eprintln!("Page {:2}: {:6} chars", p, text.len());
                total += text.len();
            },
            Err(e) => eprintln!("Page {:2}: ERROR: {}", p, e),
        }
    }
    eprintln!("Total: {} chars", total);

    Ok(())
}
