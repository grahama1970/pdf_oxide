//! Text-order reproducer for NIST 800-53r5 page 27 (printed PAGE 1).
//!
//! Diagnostic binary requested by WebGPT R5 for GS001 row 4 ("derMon
//! information systems" artifact). Dumps page-27 text spans in three
//! orderings so we can identify where "Modern" becomes "derMon":
//!
//!   1. raw content-stream order  — spans sorted by `TextSpan.sequence`
//!   2. geometric reading order   — spans sorted by (-y, x)
//!   3. final block text order    — `BlockClassifier::classify_spans` output
//!
//! Usage:
//!
//!     cargo run --release --bin text_order_reproducer_nist_p27 -- \
//!       /home/graham/workspace/experiments/pi-mono/packages/ux-lab/public/NIST_SP_800-53r5.pdf \
//!       /tmp/pdf-lab-golden-slices-r5/nist_page_28_printed_page_1/diagnostics/text_order_reproducer
//!
//! Writes:
//!
//!   <out>/spans_raw_order.json
//!   <out>/spans_geometric_order.json
//!   <out>/blocks_final_order.json
//!   <out>/summary.md            (Modern/derMon highlights)
//!
//! Does NOT mutate any extractor behavior; this is read-only diagnostics.

use pdf_oxide::document::PdfDocument;
use pdf_oxide::extractors::block_classifier::BlockClassifier;
use pdf_oxide::layout::TextSpan;
use serde::Serialize;
use std::env;
use std::fs;
use std::path::PathBuf;

const PAGE_INDEX: usize = 27;
const NEEDLES: &[&str] = &["Modern", "derMon", "Mon", "der"];

#[derive(Serialize)]
struct SpanView {
    /// Position in the emitted ordering (0-based)
    rank: usize,
    /// Original content-stream sequence number
    sequence: usize,
    text: String,
    text_len_chars: usize,
    bbox_x: f32,
    bbox_y: f32,
    bbox_width: f32,
    bbox_height: f32,
    font_name: String,
    font_size: f32,
    font_weight: u16,
    is_italic: bool,
    split_boundary_before: bool,
    offset_semantic: bool,
    char_spacing: f32,
    word_spacing: f32,
    horizontal_scaling: f32,
    /// Block-id this span falls inside (by bbox containment) after the
    /// full classify+merge+promote pipeline. None if no block contains it.
    block_id: Option<String>,
    /// Hits any of the NIST page-27 needles (Modern/derMon/Mon/der)?
    has_needle: bool,
}

#[derive(Serialize)]
struct BlockView {
    block_id: String,
    block_type: String,
    text: String,
    text_len_chars: usize,
    bbox_x: f32,
    bbox_y: f32,
    bbox_width: f32,
    bbox_height: f32,
    avg_font_size: f32,
    is_bold: bool,
    header_level: Option<u8>,
    line_count: usize,
    /// Number of source spans whose bbox center lies inside this block.
    span_count: usize,
}

fn span_in_block_bbox(span: &TextSpan, block_bbox: &pdf_oxide::geometry::Rect) -> bool {
    let sx = span.bbox.x + span.bbox.width * 0.5;
    let sy = span.bbox.y + span.bbox.height * 0.5;
    sx >= block_bbox.x
        && sx <= block_bbox.x + block_bbox.width
        && sy >= block_bbox.y
        && sy <= block_bbox.y + block_bbox.height
}

fn has_needle(text: &str) -> bool {
    NEEDLES.iter().any(|n| text.contains(n))
}

fn view_for_span(
    rank: usize,
    span: &TextSpan,
    block_ids: &[Option<String>],
    raw_index: usize,
) -> SpanView {
    SpanView {
        rank,
        sequence: span.sequence,
        text: span.text.clone(),
        text_len_chars: span.text.chars().count(),
        bbox_x: span.bbox.x,
        bbox_y: span.bbox.y,
        bbox_width: span.bbox.width,
        bbox_height: span.bbox.height,
        font_name: span.font_name.clone(),
        font_size: span.font_size,
        font_weight: span.font_weight as u16,
        is_italic: span.is_italic,
        split_boundary_before: span.split_boundary_before,
        offset_semantic: span.offset_semantic,
        char_spacing: span.char_spacing,
        word_spacing: span.word_spacing,
        horizontal_scaling: span.horizontal_scaling,
        block_id: block_ids.get(raw_index).cloned().unwrap_or(None),
        has_needle: has_needle(&span.text),
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        eprintln!("Usage: {} <pdf_path> <output_dir>", args[0]);
        std::process::exit(1);
    }
    let pdf_path = &args[1];
    let out_dir = PathBuf::from(&args[2]);
    fs::create_dir_all(&out_dir)?;

    let mut doc = PdfDocument::open(pdf_path)?;
    let page_count = doc.page_count()?;
    if PAGE_INDEX >= page_count {
        return Err(format!(
            "page index {PAGE_INDEX} out of range (0..{page_count})"
        )
        .into());
    }

    // GS001 R5 row-4 evidence: capture BOTH span extraction paths.
    //   extract_spans()          → spans sorted (geometric reading order)
    //   extract_spans_unsorted() → spans in raw PDF content-stream order
    //
    // The Python `classify_blocks` binding (src/python.rs:3653) uses the
    // _unsorted_ path. classify_spans then concatenates each line's spans
    // in iteration order (block_classifier.rs::classify_line:298). When the
    // PDF emits kerned text with negative TJ shifts, individual span
    // fragments arrive in content-stream order that does NOT match left-to-
    // right x order, producing artifacts like "Modern" → "derMon".
    let sorted_spans = doc.extract_spans(PAGE_INDEX)?;
    let unsorted_spans = doc.extract_spans_unsorted(PAGE_INDEX)?;
    let page_info = doc.get_page_info(PAGE_INDEX)?;
    let page_width = page_info.media_box.width;
    let page_height = page_info.media_box.height;

    // Run classify_spans on BOTH paths so we can compare the resulting
    // block texts side-by-side. The "release" path (Python classify_blocks
    // binding) uses the unsorted spans; this is what produces "derMon".
    let classifier_sorted = BlockClassifier::new(page_width, page_height, &sorted_spans);
    let classifier_unsorted = BlockClassifier::new(page_width, page_height, &unsorted_spans);
    let blocks_sorted = classifier_sorted.classify_spans(&sorted_spans);
    let blocks_unsorted = classifier_unsorted.classify_spans(&unsorted_spans);

    // The "blocks" returned to release JSON consumers come from the
    // unsorted path. Use those for span→block_id attribution.
    let raw_spans = unsorted_spans.clone();
    let blocks = blocks_unsorted.clone();

    let block_views: Vec<BlockView> = blocks
        .iter()
        .enumerate()
        .map(|(i, b)| BlockView {
            block_id: format!("actual:p{PAGE_INDEX}:block:{i}"),
            block_type: format!("{:?}", b.block_type),
            text: b.text.clone(),
            text_len_chars: b.text.chars().count(),
            bbox_x: b.bbox.x,
            bbox_y: b.bbox.y,
            bbox_width: b.bbox.width,
            bbox_height: b.bbox.height,
            avg_font_size: b.font_size,
            is_bold: b.is_bold,
            header_level: b.header_level,
            line_count: b.lines.len(),
            span_count: raw_spans.iter().filter(|s| span_in_block_bbox(s, &b.bbox)).count(),
        })
        .collect();

    // Map each raw span to a block-id (by bbox containment of span center).
    let span_block_ids: Vec<Option<String>> = raw_spans
        .iter()
        .map(|span| {
            blocks.iter().enumerate().find_map(|(i, b)| {
                if span_in_block_bbox(span, &b.bbox) {
                    Some(format!("actual:p{PAGE_INDEX}:block:{i}"))
                } else {
                    None
                }
            })
        })
        .collect();

    // View 1: raw content-stream order (by `sequence`)
    let mut raw_indices: Vec<usize> = (0..raw_spans.len()).collect();
    raw_indices.sort_by_key(|&i| raw_spans[i].sequence);
    let raw_view: Vec<SpanView> = raw_indices
        .iter()
        .enumerate()
        .map(|(rank, &i)| view_for_span(rank, &raw_spans[i], &span_block_ids, i))
        .collect();

    // View 2: geometric reading order (-y, x). Top-of-page = highest y in PDF
    // coords, so descending y, then ascending x.
    let mut geo_indices: Vec<usize> = (0..raw_spans.len()).collect();
    geo_indices.sort_by(|&a, &b| {
        let ya = raw_spans[a].bbox.y;
        let yb = raw_spans[b].bbox.y;
        yb.partial_cmp(&ya).unwrap_or(std::cmp::Ordering::Equal).then_with(|| {
            raw_spans[a]
                .bbox
                .x
                .partial_cmp(&raw_spans[b].bbox.x)
                .unwrap_or(std::cmp::Ordering::Equal)
        })
    });
    let geo_view: Vec<SpanView> = geo_indices
        .iter()
        .enumerate()
        .map(|(rank, &i)| view_for_span(rank, &raw_spans[i], &span_block_ids, i))
        .collect();

    fs::write(
        out_dir.join("spans_raw_order.json"),
        serde_json::to_string_pretty(&raw_view)?,
    )?;
    fs::write(
        out_dir.join("spans_geometric_order.json"),
        serde_json::to_string_pretty(&geo_view)?,
    )?;
    fs::write(
        out_dir.join("blocks_final_order.json"),
        serde_json::to_string_pretty(&block_views)?,
    )?;

    // Summary: highlight every span whose text touches Modern/derMon needles
    // plus its immediate neighbors in each ordering.
    let mut summary = String::new();
    summary.push_str("# NIST 800-53r5 page 27 — text-order reproducer\n\n");
    summary.push_str(&format!("Total spans: {}\n", raw_spans.len()));
    summary.push_str(&format!(
        "Total blocks (after classify + merge_consecutive_body + promote_isolated_heading_blocks): {}\n\n",
        blocks.len()
    ));
    summary.push_str("Page MediaBox: ");
    summary.push_str(&format!(
        "{:.1} × {:.1} pt\n\n",
        page_width, page_height
    ));

    fn dump_ordering(
        summary: &mut String,
        label: &str,
        view: &[SpanView],
    ) {
        summary.push_str(&format!("## Ordering: {}\n\n", label));
        summary.push_str("Needle hits and their immediate (±2) neighbors:\n\n");
        let hits: Vec<usize> = view
            .iter()
            .enumerate()
            .filter(|(_, sv)| sv.has_needle)
            .map(|(i, _)| i)
            .collect();
        if hits.is_empty() {
            summary.push_str("- (no needle hits)\n\n");
            return;
        }
        for hit in &hits {
            let lo = hit.saturating_sub(2);
            let hi = (*hit + 3).min(view.len());
            summary.push_str(&format!("### hit @ rank {}\n\n", hit));
            summary.push_str("| rank | seq | text | bbox(x,y,w,h) | font | block_id |\n");
            summary.push_str("|---:|---:|---|---|---|---|\n");
            for r in lo..hi {
                let sv = &view[r];
                let marker = if r == *hit { "**" } else { "" };
                summary.push_str(&format!(
                    "| {m}{r}{m} | {seq} | {m}{text}{m} | ({x:.1},{y:.1},{w:.1},{h:.1}) | {fn_}@{fs}pt | {bid} |\n",
                    m = marker,
                    r = sv.rank,
                    seq = sv.sequence,
                    text = sv.text.replace('|', "\\|").replace('\n', "\\n"),
                    x = sv.bbox_x,
                    y = sv.bbox_y,
                    w = sv.bbox_width,
                    h = sv.bbox_height,
                    fn_ = sv.font_name,
                    fs = sv.font_size,
                    bid = sv.block_id.clone().unwrap_or_else(|| "—".to_string()),
                ));
            }
            summary.push('\n');
        }
    }

    dump_ordering(&mut summary, "raw content-stream order (by sequence)", &raw_view);
    dump_ordering(&mut summary, "geometric reading order (-y, x)", &geo_view);

    summary.push_str("## Final blocks (after classify + merge + promote)\n\n");
    summary.push_str("| block_id | type | text (first 80 chars) | bbox |\n");
    summary.push_str("|---|---|---|---|\n");
    for bv in &block_views {
        let needle_marker = if has_needle(&bv.text) { "🔍 " } else { "" };
        let preview: String = bv.text.chars().take(80).collect();
        summary.push_str(&format!(
            "| {bid} | {btype} | {nm}{prev} | ({x:.1},{y:.1},{w:.1},{h:.1}) |\n",
            bid = bv.block_id,
            btype = bv.block_type,
            nm = needle_marker,
            prev = preview.replace('|', "\\|").replace('\n', " "),
            x = bv.bbox_x,
            y = bv.bbox_y,
            w = bv.bbox_width,
            h = bv.bbox_height,
        ));
    }

    // --- Row-4 smoking gun: sorted vs unsorted classify path -------------
    let sorted_block_views: Vec<BlockView> = blocks_sorted
        .iter()
        .enumerate()
        .map(|(i, b)| BlockView {
            block_id: format!("sorted_path:p{PAGE_INDEX}:block:{i}"),
            block_type: format!("{:?}", b.block_type),
            text: b.text.clone(),
            text_len_chars: b.text.chars().count(),
            bbox_x: b.bbox.x,
            bbox_y: b.bbox.y,
            bbox_width: b.bbox.width,
            bbox_height: b.bbox.height,
            avg_font_size: b.font_size,
            is_bold: b.is_bold,
            header_level: b.header_level,
            line_count: b.lines.len(),
            span_count: 0,
        })
        .collect();
    fs::write(
        out_dir.join("blocks_sorted_path.json"),
        serde_json::to_string_pretty(&sorted_block_views)?,
    )?;

    let mut comparison = String::new();
    comparison.push_str("# Row-4 finding — Modern vs derMon\n\n");
    comparison.push_str("## Two span-extraction paths\n\n");
    comparison.push_str("| path | called by | total spans | sample seqs for needle hits |\n");
    comparison.push_str("|---|---|---:|---|\n");
    let unsorted_needle_seqs: Vec<usize> = unsorted_spans
        .iter()
        .filter(|s| has_needle(&s.text))
        .map(|s| s.sequence)
        .collect();
    let sorted_needle_seqs: Vec<usize> = sorted_spans
        .iter()
        .filter(|s| has_needle(&s.text))
        .map(|s| s.sequence)
        .collect();
    comparison.push_str(&format!(
        "| extract_spans_unsorted() | Python `classify_blocks` (release path, src/python.rs:3656) | {} | {:?} |\n",
        unsorted_spans.len(),
        unsorted_needle_seqs,
    ));
    comparison.push_str(&format!(
        "| extract_spans()          | this reproducer's reference | {} | {:?} |\n\n",
        sorted_spans.len(),
        sorted_needle_seqs,
    ));

    comparison.push_str("## Block-text diff at the Modern/derMon site\n\n");

    fn blocks_with_needle<'a>(bs: &'a [pdf_oxide::extractors::block_classifier::ClassifiedBlock]) -> Vec<&'a pdf_oxide::extractors::block_classifier::ClassifiedBlock> {
        bs.iter().filter(|b| has_needle(&b.text)).collect()
    }
    let unsorted_hits = blocks_with_needle(&blocks_unsorted);
    let sorted_hits = blocks_with_needle(&blocks_sorted);
    comparison.push_str("### Unsorted path (RELEASE — produces derMon)\n\n");
    for b in &unsorted_hits {
        let preview: String = b.text.chars().take(160).collect();
        comparison.push_str(&format!(
            "- type={:?} bbox=({:.1},{:.1},{:.1},{:.1}) text={:?}\n",
            b.block_type, b.bbox.x, b.bbox.y, b.bbox.width, b.bbox.height, preview
        ));
    }
    comparison.push_str("\n### Sorted path (would-be fix — produces Modern correctly)\n\n");
    for b in &sorted_hits {
        let preview: String = b.text.chars().take(160).collect();
        comparison.push_str(&format!(
            "- type={:?} bbox=({:.1},{:.1},{:.1},{:.1}) text={:?}\n",
            b.block_type, b.bbox.x, b.bbox.y, b.bbox.width, b.bbox.height, preview
        ));
    }

    comparison.push_str("\n## Ownership boundary identified\n\n");
    comparison.push_str("The `derMon` artifact originates at `classify_line`'s span-text join\n");
    comparison.push_str("(`src/extractors/block_classifier.rs:298`), which concatenates spans\n");
    comparison.push_str("in iteration order. When the Python binding\n");
    comparison.push_str("`classify_blocks` (`src/python.rs:3656`) feeds spans from\n");
    comparison.push_str("`extract_spans_unsorted()` (raw PDF content-stream order), the\n");
    comparison.push_str("kerning shifts emitted by the NIST PDF's text-showing operators\n");
    comparison.push_str("(negative TJ adjustments for tight letter spacing) cause the\n");
    comparison.push_str("\"Modern\" glyphs to enter the span list as smaller fragments out\n");
    comparison.push_str("of left-to-right order — e.g. \"der\" appears in the content stream\n");
    comparison.push_str("BEFORE \"Mo\" because TJ uses a backward-then-forward draw sequence.\n");
    comparison.push_str("classify_line joins these as content-stream order: \"der\" + \"Mo\"\n");
    comparison.push_str("+ \"n\" = \"derMon\".\n\n");
    comparison.push_str("The same `classify_spans()` call on `extract_spans()` (sorted)\n");
    comparison.push_str("output produces \"Modern\" correctly because the sort restores\n");
    comparison.push_str("geometric x-order before line grouping.\n\n");
    comparison.push_str("## Local ownership — NOT a broad text-extraction-pipeline bug\n\n");
    comparison.push_str("Three candidate narrow fixes (in order of preference, none\n");
    comparison.push_str("applied in R5 per WebGPT's evidence-first directive):\n\n");
    comparison.push_str("1. **Switch `classify_blocks` Python binding to `extract_spans`**\n");
    comparison.push_str("   (sorted). One-line change to `src/python.rs:3656`. Affects only\n");
    comparison.push_str("   the Python release-mode `classify_blocks` entry; does not touch\n");
    comparison.push_str("   the text-extraction pipeline. Risk: any caller that relies on\n");
    comparison.push_str("   content-stream order would break (none expected — the WASM\n");
    comparison.push_str("   binding at `src/wasm.rs:425` and the Rust API at\n");
    comparison.push_str("   `src/api/pdf_builder.rs:750` need a similar audit).\n");
    comparison.push_str("2. **Sort spans by bbox.x inside `classify_line`** before joining\n");
    comparison.push_str("   text. Single function change in\n");
    comparison.push_str("   `src/extractors/block_classifier.rs:294-300`. Affects every\n");
    comparison.push_str("   call site of `classify_spans`. Slightly broader, but still\n");
    comparison.push_str("   strictly local to one function.\n");
    comparison.push_str("3. **Sort spans by bbox.x inside `group_spans_into_lines`**\n");
    comparison.push_str("   per-line groups before they're handed to classify_line.\n");
    comparison.push_str("   Identical observable effect to (2) with slightly more code.\n\n");
    comparison.push_str("R5 does NOT apply any of these. WebGPT R5 directive:\n");
    comparison.push_str("> Do not patch the extraction pipeline until the reproducer\n");
    comparison.push_str("> identifies the local ownership boundary.\n\n");
    comparison.push_str("The boundary is identified above. Awaiting R6 route decision.\n");

    fs::write(out_dir.join("comparison_modern_vs_derMon.md"), &comparison)?;
    fs::write(out_dir.join("summary.md"), &summary)?;

    println!("Reproducer written to: {}", out_dir.display());
    println!("  - spans_raw_order.json ({} spans, unsorted path)", raw_view.len());
    println!("  - spans_geometric_order.json ({} spans, unsorted path -y/x)", geo_view.len());
    println!("  - blocks_final_order.json ({} blocks, unsorted path)", block_views.len());
    println!("  - blocks_sorted_path.json ({} blocks, sorted path - would-be fix)", sorted_block_views.len());
    println!("  - comparison_modern_vs_derMon.md (smoking-gun side-by-side)");
    println!("  - summary.md (Modern/derMon highlights)");

    Ok(())
}
