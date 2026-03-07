//! Invisible text layer writer for searchable PDFs.
//!
//! Converts OCR results into an invisible text overlay that makes scanned
//! PDFs searchable. Text is rendered with mode 3 (invisible) so it can be
//! selected and searched but is not visually displayed.

use super::engine::OcrSpan;
use crate::writer::content_stream::{ContentStreamBuilder, ContentStreamOp};

/// Build an invisible text layer content stream from OCR spans.
///
/// The generated content stream:
/// 1. Sets text rendering mode 3 (invisible)
/// 2. Uses Helvetica font at the estimated size for each span
/// 3. Positions each text span at its detected location
/// 4. Resets text rendering mode to 0 at the end
///
/// # Arguments
///
/// * `spans` - OCR-recognized text spans with positions
/// * `scale` - Scale factor from image coordinates to PDF points (image_dpi / 72.0)
/// * `page_height` - Page height in PDF points (needed for Y-axis flip)
///
/// # Returns
///
/// Raw content stream bytes ready to be added as a page overlay.
pub fn build_invisible_text_layer(
    spans: &[OcrSpan],
    scale: f32,
    page_height: f32,
) -> crate::error::Result<Vec<u8>> {
    if spans.is_empty() {
        return Ok(Vec::new());
    }

    let mut builder = ContentStreamBuilder::new();

    // Save graphics state
    builder.op(ContentStreamOp::SaveState);

    // Set invisible rendering mode (mode 3 = neither fill nor stroke)
    builder.begin_text();
    builder.set_text_rendering_mode(3);

    for span in spans {
        if span.text.trim().is_empty() {
            continue;
        }

        // Convert polygon to axis-aligned bounding box in image coordinates
        let min_x = span.polygon.iter().map(|p| p[0]).fold(f32::MAX, f32::min);
        let max_x = span.polygon.iter().map(|p| p[0]).fold(f32::MIN, f32::max);
        let min_y = span.polygon.iter().map(|p| p[1]).fold(f32::MAX, f32::min);
        let max_y = span.polygon.iter().map(|p| p[1]).fold(f32::MIN, f32::max);

        // Convert image coordinates to PDF points
        let pdf_x = min_x / scale;
        let pdf_width = (max_x - min_x) / scale;
        let height_px = max_y - min_y;

        // PDF coordinate system has origin at bottom-left, Y increases upward.
        // OCR polygon has origin at top-left, Y increases downward.
        // Baseline position: bottom of the text box in PDF coordinates.
        let pdf_y = page_height - (max_y / scale);

        // Estimate font size from text height
        let font_size = (height_px / scale * 0.75).clamp(4.0, 72.0);

        // Calculate horizontal scaling to fit text width
        // This ensures the invisible text covers the same area as the visible text
        let char_count = span.text.chars().count() as f32;
        if char_count <= 0.0 {
            continue;
        }

        // Approximate character width for Helvetica: ~0.5 * font_size
        let natural_width = char_count * font_size * 0.5;
        let h_scale = if natural_width > 0.0 {
            (pdf_width / natural_width * 100.0).clamp(25.0, 400.0)
        } else {
            100.0
        };

        builder.set_font("Helvetica", font_size);

        // Set horizontal scaling to match OCR-detected width
        if (h_scale - 100.0).abs() > 5.0 {
            builder.op(ContentStreamOp::Raw(format!("{:.1} Tz", h_scale)));
        }

        // Position text at the span location
        builder.op(ContentStreamOp::SetTextMatrix(
            1.0, 0.0, 0.0, 1.0, pdf_x, pdf_y,
        ));
        builder.op(ContentStreamOp::ShowText(span.text.clone()));
    }

    // Reset rendering mode to visible and end text object
    builder.set_text_rendering_mode(0);
    builder.end_text();

    // Restore graphics state
    builder.op(ContentStreamOp::RestoreState);

    builder.build()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_empty_spans_produces_empty_output() {
        let result = build_invisible_text_layer(&[], 4.17, 792.0).unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn test_basic_text_layer_generation() {
        let spans = vec![OcrSpan {
            text: "Hello World".to_string(),
            polygon: [
                [100.0, 200.0],
                [400.0, 200.0],
                [400.0, 240.0],
                [100.0, 240.0],
            ],
            confidence: 0.95,
            char_confidences: vec![],
        }];

        let result = build_invisible_text_layer(&spans, 300.0 / 72.0, 792.0).unwrap();
        let content = String::from_utf8_lossy(&result);

        // Should contain invisible rendering mode
        assert!(content.contains("3 Tr"), "Missing invisible text rendering mode");
        // Should contain text
        assert!(content.contains("Hello World"), "Missing text content");
        // Should contain font
        assert!(content.contains("Tf"), "Missing font specification");
        // Should contain text matrix positioning
        assert!(content.contains("Tm"), "Missing text positioning");
        // Should reset rendering mode
        assert!(content.contains("0 Tr"), "Missing rendering mode reset");
        // Should save/restore graphics state
        assert!(content.contains("q\n"), "Missing save state");
        assert!(content.contains("Q\n"), "Missing restore state");
    }

    #[test]
    fn test_whitespace_only_spans_skipped() {
        let spans = vec![OcrSpan {
            text: "   ".to_string(),
            polygon: [[0.0, 0.0], [100.0, 0.0], [100.0, 50.0], [0.0, 50.0]],
            confidence: 0.5,
            char_confidences: vec![],
        }];

        let result = build_invisible_text_layer(&spans, 4.17, 792.0).unwrap();
        let content = String::from_utf8_lossy(&result);
        // Should not contain ShowText for whitespace-only spans
        assert!(!content.contains("Tj"), "Should skip whitespace-only spans");
    }
}
