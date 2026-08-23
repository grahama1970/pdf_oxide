//! Cross-document reading-order regression coverage for issue #37.
//!
//! These fixtures are synthetic but not NIST-derived. Each case builds a real
//! minimal PDF with Type0/Identity-H text and an explicit ToUnicode CMap, then
//! runs the normal `PdfDocument::extract_text` path and compares the output to
//! committed expected order artifacts.

use pdf_oxide::document::PdfDocument;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::Path;

#[derive(Debug, Deserialize)]
struct ExpectedArtifact {
    cases: Vec<ExpectedCase>,
}

#[derive(Debug, Deserialize, Clone)]
struct ExpectedCase {
    id: String,
    expected_lines: Vec<String>,
    expected_joined: String,
}

#[derive(Debug, Serialize)]
struct CaseReport {
    id: String,
    expected_lines: Vec<String>,
    actual_lines: Vec<String>,
    expected_joined: String,
    actual_joined: String,
    passed: bool,
}

#[derive(Clone)]
struct TextRun {
    text: &'static str,
    font_size: f32,
    matrix: [f32; 6],
}

impl TextRun {
    fn at(text: &'static str, x: f32, y: f32) -> Self {
        Self {
            text,
            font_size: 16.0,
            matrix: [1.0, 0.0, 0.0, 1.0, x, y],
        }
    }

    fn sized_at(text: &'static str, font_size: f32, x: f32, y: f32) -> Self {
        Self {
            text,
            font_size,
            matrix: [1.0, 0.0, 0.0, 1.0, x, y],
        }
    }

    fn rotated_90(text: &'static str, x: f32, y: f32) -> Self {
        Self {
            text,
            font_size: 14.0,
            matrix: [0.0, 1.0, -1.0, 0.0, x, y],
        }
    }
}

fn expected_case(id: &str) -> ExpectedCase {
    let artifact: ExpectedArtifact =
        serde_json::from_str(include_str!("fixtures/issue37_cross_document_expected.json"))
            .expect("expected artifact parses");
    artifact
        .cases
        .into_iter()
        .find(|case| case.id == id)
        .unwrap_or_else(|| panic!("missing expected artifact case {id}"))
}

fn utf16be_hex(text: &str) -> String {
    text.encode_utf16()
        .flat_map(|unit| [(unit >> 8) as u8, unit as u8])
        .map(|byte| format!("{byte:02X}"))
        .collect()
}

fn pdf_string_hex(text: &str, cid_by_char: &BTreeMap<char, u16>) -> String {
    text.chars()
        .map(|ch| format!("{:04X}", cid_by_char[&ch]))
        .collect()
}

fn object(pdf: &mut Vec<u8>, body: &[u8], offsets: &mut Vec<usize>) {
    let id = offsets.len() + 1;
    offsets.push(pdf.len());
    pdf.extend_from_slice(format!("{id} 0 obj\n").as_bytes());
    pdf.extend_from_slice(body);
    pdf.extend_from_slice(b"\nendobj\n");
}

fn stream_object(pdf: &mut Vec<u8>, stream: &[u8], offsets: &mut Vec<usize>) {
    let mut body = format!("<< /Length {} >>\nstream\n", stream.len()).into_bytes();
    body.extend_from_slice(stream);
    body.extend_from_slice(b"\nendstream");
    object(pdf, &body, offsets);
}

fn finalize(pdf: &mut Vec<u8>, offsets: &[usize]) {
    let xref = pdf.len();
    pdf.extend_from_slice(format!("xref\n0 {}\n", offsets.len() + 1).as_bytes());
    pdf.extend_from_slice(b"0000000000 65535 f \r\n");
    for offset in offsets {
        pdf.extend_from_slice(format!("{offset:010} 00000 n \r\n").as_bytes());
    }
    pdf.extend_from_slice(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n",
            offsets.len() + 1
        )
        .as_bytes(),
    );
}

fn build_minimal_tounicode_pdf(runs: &[TextRun]) -> Vec<u8> {
    let mut chars = BTreeMap::<char, u16>::new();
    for run in runs {
        for ch in run.text.chars() {
            if !chars.contains_key(&ch) {
                let next = chars.len() as u16 + 1;
                chars.insert(ch, next);
            }
        }
    }

    let mut bfchar = String::new();
    for (ch, cid) in &chars {
        bfchar.push_str(&format!("<{cid:04X}> <{}>\n", utf16be_hex(&ch.to_string())));
    }
    let cmap = format!(
        "/CIDInit /ProcSet findresource begin\n\
         12 dict begin\n\
         begincmap\n\
         /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> def\n\
         /CMapName /Issue37ToUnicode def\n\
         /CMapType 2 def\n\
         1 begincodespacerange\n\
         <0000> <FFFF>\n\
         endcodespacerange\n\
         {} beginbfchar\n\
         {}\
         endbfchar\n\
         endcmap\n\
         CMapName currentdict /CMap defineresource pop\n\
         end\n\
         end\n",
        chars.len(),
        bfchar
    );

    let mut content = String::new();
    for run in runs {
        let matrix = run
            .matrix
            .iter()
            .map(|value| {
                if value.fract() == 0.0 {
                    format!("{value:.0}")
                } else {
                    value.to_string()
                }
            })
            .collect::<Vec<_>>()
            .join(" ");
        content.push_str(&format!(
            "BT /F1 {} Tf {matrix} Tm <{}> Tj ET\n",
            run.font_size,
            pdf_string_hex(run.text, &chars)
        ));
    }

    let mut pdf = b"%PDF-1.4\n%\xE2\xE3\xCF\xD3\n".to_vec();
    let mut offsets = Vec::new();
    object(&mut pdf, b"<< /Type /Catalog /Pages 2 0 R >>", &mut offsets);
    object(&mut pdf, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", &mut offsets);
    object(
        &mut pdf,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 7 0 R >>",
        &mut offsets,
    );
    object(
        &mut pdf,
        b"<< /Type /Font /Subtype /Type0 /BaseFont /Issue37Sans /Encoding /Identity-H /DescendantFonts [5 0 R] /ToUnicode 6 0 R >>",
        &mut offsets,
    );
    object(
        &mut pdf,
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Issue37Sans /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> /FontDescriptor 8 0 R /DW 600 >>",
        &mut offsets,
    );
    stream_object(&mut pdf, cmap.as_bytes(), &mut offsets);
    stream_object(&mut pdf, content.as_bytes(), &mut offsets);
    object(
        &mut pdf,
        b"<< /Type /FontDescriptor /FontName /Issue37Sans /Flags 4 /FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 >>",
        &mut offsets,
    );
    finalize(&mut pdf, &offsets);
    pdf
}

fn normalized_lines(text: &str) -> Vec<String> {
    text.lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .map(ToOwned::to_owned)
        .collect()
}

fn run_case(id: &str, runs: &[TextRun]) -> CaseReport {
    let expected = expected_case(id);
    let mut doc = PdfDocument::open_from_bytes(build_minimal_tounicode_pdf(runs))
        .unwrap_or_else(|err| panic!("{id}: PDF did not open: {err}"));
    let text = doc
        .extract_text(0)
        .unwrap_or_else(|err| panic!("{id}: extract_text failed: {err}"));
    let lines = normalized_lines(&text);
    let joined = lines.join(" ");
    let passed = lines == expected.expected_lines && joined == expected.expected_joined;

    CaseReport {
        id: id.to_string(),
        expected_lines: expected.expected_lines,
        actual_lines: lines,
        expected_joined: expected.expected_joined,
        actual_joined: joined,
        passed,
    }
}

fn assert_case(id: &str, runs: &[TextRun]) {
    let report = run_case(id, runs);

    assert_eq!(
        report.actual_lines, report.expected_lines,
        "{id} joined text was {:?}",
        report.actual_joined
    );
    assert_eq!(
        report.actual_joined, report.expected_joined,
        "{id} lines were {:?}",
        report.actual_lines
    );
}

fn case_definitions() -> Vec<(&'static str, Vec<TextRun>)> {
    vec![
        (
            "rtl_hebrew_positioned_runs",
            vec![
                TextRun::at("שלום", 340.0, 700.0),
                TextRun::at("עולם", 260.0, 700.0),
            ],
        ),
        (
            "bidi_latin_hebrew_runs",
            vec![
                TextRun::at("LTR-A", 72.0, 700.0),
                TextRun::at("שלום", 170.0, 700.0),
                TextRun::at("LTR-B", 260.0, 700.0),
            ],
        ),
        (
            "math_like_super_sub_runs",
            vec![
                TextRun::at("x", 72.0, 700.0),
                TextRun::sized_at("2", 10.0, 84.0, 710.0),
                TextRun::at(" + y", 100.0, 700.0),
                TextRun::sized_at("1", 10.0, 138.0, 690.0),
                TextRun::at(" = 5", 154.0, 700.0),
            ],
        ),
        (
            "rotated_side_run",
            vec![
                TextRun::rotated_90("ROTATED SIDE", 40.0, 110.0),
                TextRun::at("MAIN TOP", 72.0, 720.0),
                TextRun::at("MAIN BOTTOM", 72.0, 680.0),
            ],
        ),
    ]
}

fn write_report(path: &Path, cases: &[CaseReport]) {
    let passed_case_count = cases.iter().filter(|case| case.passed).count();
    let problems = cases
        .iter()
        .filter(|case| !case.passed)
        .map(|case| format!("{} order mismatch", case.id))
        .collect::<Vec<_>>();
    let report = serde_json::json!({
        "schema": "pdf_oxide.issue37.cross_document_reading_order_report.v1",
        "source": "tests/test_issue37_cross_document_reading_order.rs",
        "expected_artifact": "tests/fixtures/issue37_cross_document_expected.json",
        "extractor": "PdfDocument::extract_text",
        "mocked": false,
        "fixture_backed": true,
        "case_count": cases.len(),
        "passed_case_count": passed_case_count,
        "passed": problems.is_empty(),
        "problems": problems,
        "cases": cases,
    });
    fs::write(path, serde_json::to_string_pretty(&report).expect("report serializes"))
        .unwrap_or_else(|err| panic!("failed to write report {}: {err}", path.display()));
}

#[test]
fn issue37_rtl_hebrew_positioned_runs() {
    assert_case(
        "rtl_hebrew_positioned_runs",
        &[
            TextRun::at("שלום", 340.0, 700.0),
            TextRun::at("עולם", 260.0, 700.0),
        ],
    );
}

#[test]
fn issue37_bidi_latin_hebrew_runs() {
    assert_case(
        "bidi_latin_hebrew_runs",
        &[
            TextRun::at("LTR-A", 72.0, 700.0),
            TextRun::at("שלום", 170.0, 700.0),
            TextRun::at("LTR-B", 260.0, 700.0),
        ],
    );
}

#[test]
fn issue37_math_like_super_sub_runs() {
    assert_case(
        "math_like_super_sub_runs",
        &[
            TextRun::at("x", 72.0, 700.0),
            TextRun::sized_at("2", 10.0, 84.0, 710.0),
            TextRun::at(" + y", 100.0, 700.0),
            TextRun::sized_at("1", 10.0, 138.0, 690.0),
            TextRun::at(" = 5", 154.0, 700.0),
        ],
    );
}

#[test]
fn issue37_rotated_side_run() {
    assert_case(
        "rotated_side_run",
        &[
            TextRun::rotated_90("ROTATED SIDE", 40.0, 110.0),
            TextRun::at("MAIN TOP", 72.0, 720.0),
            TextRun::at("MAIN BOTTOM", 72.0, 680.0),
        ],
    );
}

#[test]
fn issue37_cross_document_expected_artifact_report() {
    let cases = case_definitions()
        .into_iter()
        .map(|(id, runs)| run_case(id, &runs))
        .collect::<Vec<_>>();

    if let Ok(path) = env::var("PDF_OXIDE_ISSUE37_REPORT") {
        write_report(Path::new(&path), &cases);
    }

    for case in cases {
        assert!(
            case.passed,
            "{} mismatch: expected {:?}, got {:?}",
            case.id, case.expected_lines, case.actual_lines
        );
    }
}
