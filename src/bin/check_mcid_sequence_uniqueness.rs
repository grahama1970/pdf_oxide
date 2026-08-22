//! Audit MCID span sequence uniqueness over selected PDF pages.

use pdf_oxide::document::PdfDocument;
use serde::Serialize;
use std::collections::{BTreeMap, HashMap};
use std::env;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Serialize)]
struct DuplicateSpan {
    index: usize,
    text: String,
    bbox: [f32; 4],
}

#[derive(Debug, Serialize)]
struct DuplicateGroup {
    page: usize,
    mcid: u32,
    sequence: usize,
    spans: Vec<DuplicateSpan>,
}

#[derive(Debug, Serialize)]
struct PageReport {
    page: usize,
    span_count: usize,
    mcid_span_count: usize,
    duplicate_group_count: usize,
}

#[derive(Debug, Serialize)]
struct Report {
    passed: bool,
    pdf: String,
    pages_scanned: usize,
    span_count: usize,
    mcid_span_count: usize,
    duplicate_groups: usize,
    page_reports: Vec<PageReport>,
    duplicates: Vec<DuplicateGroup>,
}

fn usage(program: &str) -> String {
    format!("Usage: {program} <pdf> --pages 1,2,3 [--output report.json]")
}

fn parse_pages(raw: &str) -> Result<Vec<usize>, String> {
    let mut pages = Vec::new();
    for part in raw.split(',') {
        let trimmed = part.trim();
        if trimmed.is_empty() {
            continue;
        }
        let page = trimmed
            .parse::<usize>()
            .map_err(|err| format!("bad page {trimmed:?}: {err}"))?;
        if page == 0 {
            return Err("pages are 1-based; got 0".to_string());
        }
        pages.push(page);
    }
    pages.sort_unstable();
    pages.dedup();
    if pages.is_empty() {
        return Err("page list is empty".to_string());
    }
    Ok(pages)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 4 {
        eprintln!(
            "{}",
            usage(
                args.first()
                    .map(String::as_str)
                    .unwrap_or("check_mcid_sequence_uniqueness")
            )
        );
        std::process::exit(2);
    }

    let pdf = PathBuf::from(&args[1]);
    let mut pages: Option<Vec<usize>> = None;
    let mut output: Option<PathBuf> = None;
    let mut index = 2;
    while index < args.len() {
        match args[index].as_str() {
            "--pages" => {
                index += 1;
                let Some(raw) = args.get(index) else {
                    return Err("--pages requires a value".into());
                };
                pages = Some(parse_pages(raw)?);
            },
            "--output" => {
                index += 1;
                let Some(raw) = args.get(index) else {
                    return Err("--output requires a value".into());
                };
                output = Some(PathBuf::from(raw));
            },
            other => return Err(format!("unknown argument: {other}").into()),
        }
        index += 1;
    }
    let pages = pages.ok_or_else(|| "--pages is required".to_string())?;

    let mut doc = PdfDocument::open(&pdf)?;
    let page_count = doc.page_count()?;
    let mut page_reports = Vec::new();
    let mut duplicates = Vec::new();
    let mut total_span_count = 0usize;
    let mut total_mcid_span_count = 0usize;

    for page in pages {
        if page > page_count {
            return Err(format!("page {page} out of range; document has {page_count} pages").into());
        }
        let spans = doc.extract_spans_unsorted(page - 1)?;
        total_span_count += spans.len();

        let mut by_key: HashMap<(u32, usize), Vec<DuplicateSpan>> = HashMap::new();
        for (span_index, span) in spans.iter().enumerate() {
            let Some(mcid) = span.mcid else {
                continue;
            };
            total_mcid_span_count += 1;
            by_key
                .entry((mcid, span.sequence))
                .or_default()
                .push(DuplicateSpan {
                    index: span_index,
                    text: span.text.clone(),
                    bbox: [span.bbox.x, span.bbox.y, span.bbox.width, span.bbox.height],
                });
        }

        let mut page_duplicate_count = 0usize;
        let ordered: BTreeMap<(u32, usize), Vec<DuplicateSpan>> = by_key.into_iter().collect();
        for ((mcid, sequence), grouped_spans) in ordered {
            if grouped_spans.len() < 2 {
                continue;
            }
            page_duplicate_count += 1;
            duplicates.push(DuplicateGroup {
                page,
                mcid,
                sequence,
                spans: grouped_spans,
            });
        }

        page_reports.push(PageReport {
            page,
            span_count: spans.len(),
            mcid_span_count: spans.iter().filter(|span| span.mcid.is_some()).count(),
            duplicate_group_count: page_duplicate_count,
        });
    }

    let report = Report {
        passed: duplicates.is_empty(),
        pdf: pdf.display().to_string(),
        pages_scanned: page_reports.len(),
        span_count: total_span_count,
        mcid_span_count: total_mcid_span_count,
        duplicate_groups: duplicates.len(),
        page_reports,
        duplicates,
    };
    let rendered = serde_json::to_string_pretty(&report)?;
    if let Some(path) = output {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(&path, format!("{rendered}\n"))?;
    }
    println!("{rendered}");
    if report.passed {
        Ok(())
    } else {
        std::process::exit(1);
    }
}
