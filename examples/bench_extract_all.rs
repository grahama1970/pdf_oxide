//! Bulk text extraction benchmark — walks corpus directories and extracts text from every PDF.
//!
//! Outputs:
//!   <output_dir>/pdf_oxide/<corpus>__<filename>.txt   — extracted text
//!   <output_dir>/pdf_oxide/results.csv                — path, chars, ms, pages, error
//!
//! Usage:
//!   cargo run --release --example bench_extract_all -- [--output /tmp/text_comparison]

use pdf_oxide::document::PdfDocument;
use std::env;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::Instant;

const PASSWORDS: &[&[u8]] = &[
    b"",
    b"owner",
    b"user",
    b"asdfasdf",
    b"password",
    b"test",
    b"123456",
    b"ownerpass",
    b"userpass",
];

const SKIP_FILES: &[&str] = &["bomb_giant.pdf", "bomb.pdf"];

struct CorpusDef {
    name: &'static str,
    path: PathBuf,
}

fn default_corpora() -> Vec<CorpusDef> {
    let home = env::var("HOME").unwrap_or_else(|_| "/home".to_string());
    vec![
        CorpusDef {
            name: "veraPDF",
            path: PathBuf::from(&home).join("projects/veraPDF-corpus"),
        },
        CorpusDef {
            name: "pdfjs",
            path: PathBuf::from(&home).join("projects/pdf_oxide_tests/pdfs_pdfjs"),
        },
        CorpusDef {
            name: "safedocs",
            path: PathBuf::from(&home).join("projects/pdf_oxide_tests/pdfs_safedocs"),
        },
    ]
}

fn find_pdfs(corpora: &[CorpusDef]) -> Vec<(PathBuf, String)> {
    let mut results = Vec::new();
    for corpus in corpora {
        if !corpus.path.exists() {
            eprintln!("WARNING: corpus '{}' not found at {}", corpus.name, corpus.path.display());
            continue;
        }
        walk_dir(&corpus.path, corpus.name, &mut results);
    }
    results.sort_by(|a, b| a.0.cmp(&b.0));
    results
}

fn walk_dir(dir: &Path, corpus_name: &str, results: &mut Vec<(PathBuf, String)>) {
    let entries = match fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return,
    };
    let mut entries: Vec<_> = entries.filter_map(|e| e.ok()).collect();
    entries.sort_by_key(|e| e.file_name());
    for entry in entries {
        let path = entry.path();
        if path.is_dir() {
            walk_dir(&path, corpus_name, results);
        } else if let Some(ext) = path.extension() {
            if ext.to_ascii_lowercase() == "pdf" {
                results.push((path, corpus_name.to_string()));
            }
        }
    }
}

fn extract_pdf(path: &Path) -> (String, usize, String) {
    let mut doc = match PdfDocument::open(path) {
        Ok(d) => d,
        Err(e) => return (String::new(), 0, format!("{e}")),
    };

    // Try passwords
    for pw in PASSWORDS {
        if !pw.is_empty() {
            let _ = doc.authenticate(pw);
        }
    }

    let page_count = match doc.page_count() {
        Ok(n) => n,
        Err(e) => return (String::new(), 0, format!("{e}")),
    };

    let mut all_text = String::new();
    for i in 0..page_count {
        match doc.extract_text(i) {
            Ok(text) => {
                if i > 0 {
                    all_text.push('\n');
                }
                all_text.push_str(&text);
            },
            Err(e) => {
                return (all_text, page_count, format!("page {i}: {e}"));
            },
        }
    }
    (all_text, page_count, String::new())
}

fn escape_csv(s: &str) -> String {
    if s.contains(',') || s.contains('"') || s.contains('\n') {
        format!("\"{}\"", s.replace('"', "\"\""))
    } else {
        s.to_string()
    }
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let mut output_dir = PathBuf::from("/tmp/text_comparison");

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--output" => {
                i += 1;
                if i < args.len() {
                    output_dir = PathBuf::from(&args[i]);
                }
            },
            _ => {},
        }
        i += 1;
    }

    let text_dir = output_dir.join("pdf_oxide");
    fs::create_dir_all(&text_dir).expect("failed to create output dir");

    let csv_path = output_dir.join("pdf_oxide").join("results.csv");
    let mut csv = fs::File::create(&csv_path).expect("failed to create CSV");
    writeln!(csv, "pdf_path,pdf_filename,corpus,pages,chars,ms,error").unwrap();

    let corpora = default_corpora();
    let pdfs = find_pdfs(&corpora);
    let total = pdfs.len();
    eprintln!("Found {} PDFs across {} corpora", total, corpora.len());

    let mut processed = 0;
    let global_start = Instant::now();

    for (idx, (pdf_path, corpus)) in pdfs.iter().enumerate() {
        let filename = pdf_path.file_name().unwrap_or_default().to_string_lossy();

        if SKIP_FILES.contains(&filename.as_ref()) {
            continue;
        }

        let start = Instant::now();
        let (text, pages, error) = extract_pdf(pdf_path);
        let ms = start.elapsed().as_secs_f64() * 1000.0;
        let chars = text.len();

        // Save text file
        let safe_name = format!("{}__{}", corpus, filename).replace(' ', "_");
        let txt_name = if let Some(stem) = safe_name.strip_suffix(".pdf") {
            format!("{stem}.txt")
        } else if let Some(stem) = safe_name.strip_suffix(".PDF") {
            format!("{stem}.txt")
        } else {
            format!("{safe_name}.txt")
        };

        if !text.is_empty() {
            let txt_path = text_dir.join(&txt_name);
            let _ = fs::write(&txt_path, &text);
        }

        // Write CSV row
        writeln!(
            csv,
            "{},{},{},{},{},{:.1},{}",
            escape_csv(&pdf_path.to_string_lossy()),
            escape_csv(&filename),
            corpus,
            pages,
            chars,
            ms,
            escape_csv(&error),
        )
        .unwrap();

        processed += 1;
        if processed % 100 == 0 || processed == total || !error.is_empty() {
            let tag = if !error.is_empty() {
                format!(" [err: {}]", &error[..error.len().min(40)])
            } else {
                String::new()
            };
            eprintln!(
                "  [{}/{}] chars={:>7} {:.0}ms {}{}",
                idx + 1,
                total,
                chars,
                ms,
                &filename[..filename.len().min(50)],
                tag,
            );
        }
    }

    let total_secs = global_start.elapsed().as_secs_f64();
    eprintln!("\nDone: {} PDFs in {:.1}s", processed, total_secs);
    eprintln!("Output: {}", text_dir.display());
    eprintln!("CSV:    {}", csv_path.display());
}
