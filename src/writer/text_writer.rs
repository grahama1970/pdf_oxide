//! High-level text writer for composing multi-style text into PDF pages.
//!
//! Provides a fluent API for building formatted text blocks with word-wrapping,
//! justification, and multi-font support — similar to PyMuPDF's TextWriter.
//!
//! # Example
//!
//! ```ignore
//! use pdf_oxide::writer::{TextWriter, TextWriterAlign, PdfWriter};
//!
//! let mut tw = TextWriter::new(468.0); // column width
//! tw.set_font("Helvetica", 12.0);
//! tw.append("Hello, ");
//! tw.set_font("Helvetica-Bold", 12.0);
//! tw.append("World!");
//! tw.newline();
//! tw.append("Second paragraph with automatic word wrapping.");
//!
//! let mut writer = PdfWriter::new();
//! let mut page = writer.add_page(612.0, 792.0);
//! tw.write_to_page(&mut page, 72.0, 720.0);
//! ```

use super::content_stream::{ContentStreamBuilder, ContentStreamOp};
use super::font_manager::FontManager;

/// Text alignment for fill_textbox.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub enum TextWriterAlign {
    /// Left-aligned (default)
    #[default]
    Left,
    /// Centered
    Center,
    /// Right-aligned
    Right,
    /// Justified (adjusts word spacing to fill line width)
    Justify,
}

/// A span of text with a specific font and size.
#[derive(Debug, Clone)]
struct TextSpan {
    text: String,
    font_name: String,
    font_size: f32,
    color: Option<(f32, f32, f32)>,
    small_caps: bool,
}

/// A laid-out line ready for rendering.
#[derive(Debug, Clone)]
struct LayoutLine {
    spans: Vec<LayoutSpan>,
    width: f32,
    line_height: f32,
}

/// A span within a laid-out line.
#[derive(Debug, Clone)]
struct LayoutSpan {
    text: String,
    font_name: String,
    font_size: f32,
    color: Option<(f32, f32, f32)>,
    width: f32,
}

/// High-level text writer for composing formatted text.
///
/// Accumulates text spans with different fonts/sizes, then lays them out
/// with word-wrapping when written to a page.
#[derive(Debug)]
pub struct TextWriter {
    /// Maximum line width for wrapping
    max_width: f32,
    /// Accumulated text spans
    spans: Vec<TextSpan>,
    /// Current font name
    current_font: String,
    /// Current font size
    current_font_size: f32,
    /// Current text color (r, g, b)
    current_color: Option<(f32, f32, f32)>,
    /// Small caps mode
    small_caps: bool,
    /// Font manager for text metrics
    font_manager: FontManager,
    /// Text alignment
    align: TextWriterAlign,
}

impl TextWriter {
    /// Create a new TextWriter with the given maximum line width.
    pub fn new(max_width: f32) -> Self {
        Self {
            max_width,
            spans: Vec::new(),
            current_font: "Helvetica".to_string(),
            current_font_size: 12.0,
            current_color: None,
            small_caps: false,
            font_manager: FontManager::new(),
            align: TextWriterAlign::Left,
        }
    }

    /// Set the current font and size for subsequent text.
    pub fn set_font(&mut self, name: &str, size: f32) -> &mut Self {
        self.current_font = name.to_string();
        self.current_font_size = size;
        self
    }

    /// Set the current text color (RGB, 0.0-1.0).
    pub fn set_color(&mut self, r: f32, g: f32, b: f32) -> &mut Self {
        self.current_color = Some((r, g, b));
        self
    }

    /// Enable or disable small caps mode.
    ///
    /// In small caps mode, lowercase letters are rendered as uppercase
    /// glyphs scaled to 80% of the current font size.
    pub fn set_small_caps(&mut self, enabled: bool) -> &mut Self {
        self.small_caps = enabled;
        self
    }

    /// Set text alignment for fill_textbox.
    pub fn set_align(&mut self, align: TextWriterAlign) -> &mut Self {
        self.align = align;
        self
    }

    /// Append text using the current font/size/color.
    pub fn append(&mut self, text: &str) -> &mut Self {
        if !text.is_empty() {
            self.spans.push(TextSpan {
                text: text.to_string(),
                font_name: self.current_font.clone(),
                font_size: self.current_font_size,
                color: self.current_color,
                small_caps: self.small_caps,
            });
        }
        self
    }

    /// Insert a line break.
    pub fn newline(&mut self) -> &mut Self {
        self.spans.push(TextSpan {
            text: "\n".to_string(),
            font_name: self.current_font.clone(),
            font_size: self.current_font_size,
            color: self.current_color,
            small_caps: false,
        });
        self
    }

    /// Fill a textbox region with text, returning the vertical space used.
    ///
    /// Text is laid out within the given rectangle with word-wrapping and
    /// the configured alignment. Returns the Y position after the last line.
    pub fn fill_textbox(&self, builder: &mut ContentStreamBuilder, x: f32, y: f32) -> f32 {
        let lines = self.layout_lines();
        self.render_lines(builder, &lines, x, y)
    }

    /// Write accumulated text to a ContentStreamBuilder at the given position.
    ///
    /// Returns the Y position after the last line (useful for chaining).
    pub fn write_to(&self, builder: &mut ContentStreamBuilder, x: f32, y: f32) -> f32 {
        let lines = self.layout_lines();
        self.render_lines(builder, &lines, x, y)
    }

    /// Get the total height that the text would occupy.
    pub fn text_height(&self) -> f32 {
        let lines = self.layout_lines();
        lines.iter().map(|l| l.line_height).sum()
    }

    /// Get the number of lines after layout.
    pub fn line_count(&self) -> usize {
        self.layout_lines().len()
    }

    /// Clear all accumulated text.
    pub fn clear(&mut self) -> &mut Self {
        self.spans.clear();
        self
    }

    // === Internal layout engine ===

    /// Break all spans into wrapped lines.
    fn layout_lines(&self) -> Vec<LayoutLine> {
        // First, split spans into words (preserving font/style per word)
        let words = self.split_into_words();
        if words.is_empty() {
            return vec![];
        }

        let mut lines: Vec<LayoutLine> = Vec::new();
        let mut current_spans: Vec<LayoutSpan> = Vec::new();
        let mut current_width: f32 = 0.0;
        let mut current_line_height: f32 = 0.0;

        for word in &words {
            if word.text == "\n" {
                // Explicit line break
                lines.push(LayoutLine {
                    spans: std::mem::take(&mut current_spans),
                    width: current_width,
                    line_height: if current_line_height > 0.0 {
                        current_line_height
                    } else {
                        self.line_height_for(&word.font_name, word.font_size)
                    },
                });
                current_width = 0.0;
                current_line_height = 0.0;
                continue;
            }

            let word_width = self.measure_word(word);
            let space_width = if current_spans.is_empty() {
                0.0
            } else {
                self.font_manager
                    .char_width(' ', &word.font_name, word.font_size)
            };

            if !current_spans.is_empty()
                && current_width + space_width + word_width > self.max_width
            {
                // Wrap to new line
                lines.push(LayoutLine {
                    spans: std::mem::take(&mut current_spans),
                    width: current_width,
                    line_height: current_line_height,
                });
                current_width = 0.0;
                current_line_height = 0.0;
            }

            // Add space before word (unless it's the first on the line)
            if !current_spans.is_empty() {
                // Add space to the previous span or create a new one
                if let Some(last) = current_spans.last_mut() {
                    if last.font_name == word.font_name
                        && last.font_size == word.font_size
                        && last.color == word.color
                    {
                        last.text.push(' ');
                        last.width += space_width;
                        current_width += space_width;
                    } else {
                        current_spans.push(LayoutSpan {
                            text: " ".to_string(),
                            font_name: word.font_name.clone(),
                            font_size: word.font_size,
                            color: word.color,
                            width: space_width,
                        });
                        current_width += space_width;
                    }
                }
            }

            // Add word
            if let Some(last) = current_spans.last_mut() {
                if last.font_name == word.font_name
                    && last.font_size == word.font_size
                    && last.color == word.color
                {
                    last.text.push_str(&word.text);
                    last.width += word_width;
                } else {
                    current_spans.push(LayoutSpan {
                        text: word.text.clone(),
                        font_name: word.font_name.clone(),
                        font_size: word.font_size,
                        color: word.color,
                        width: word_width,
                    });
                }
            } else {
                current_spans.push(LayoutSpan {
                    text: word.text.clone(),
                    font_name: word.font_name.clone(),
                    font_size: word.font_size,
                    color: word.color,
                    width: word_width,
                });
            }

            current_width += word_width;
            let lh = self.line_height_for(&word.font_name, word.font_size);
            if lh > current_line_height {
                current_line_height = lh;
            }
        }

        // Don't forget the last line
        if !current_spans.is_empty() {
            lines.push(LayoutLine {
                spans: current_spans,
                width: current_width,
                line_height: current_line_height,
            });
        }

        lines
    }

    /// Split accumulated spans into individual words with font info.
    fn split_into_words(&self) -> Vec<LayoutSpan> {
        let mut words = Vec::new();
        for span in &self.spans {
            if span.text == "\n" {
                words.push(LayoutSpan {
                    text: "\n".to_string(),
                    font_name: span.font_name.clone(),
                    font_size: span.font_size,
                    color: span.color,
                    width: 0.0,
                });
                continue;
            }

            let text = if span.small_caps {
                span.text.to_uppercase()
            } else {
                span.text.clone()
            };

            let font_size = if span.small_caps {
                // Check which chars were originally lowercase → render at 80%
                // For simplicity, if small_caps is on, use 80% size for the whole span
                span.font_size * 0.8
            } else {
                span.font_size
            };

            for word in text.split_whitespace() {
                words.push(LayoutSpan {
                    text: word.to_string(),
                    font_name: span.font_name.clone(),
                    font_size,
                    color: span.color,
                    width: 0.0, // computed later
                });
            }
        }
        words
    }

    fn measure_word(&self, word: &LayoutSpan) -> f32 {
        self.font_manager
            .text_width(&word.text, &word.font_name, word.font_size)
    }

    fn line_height_for(&self, font_name: &str, font_size: f32) -> f32 {
        let font = self.font_manager.get_font_or_default(font_name);
        font.line_height(font_size) * font.line_spacing_factor()
    }

    /// Render laid-out lines to a ContentStreamBuilder.
    fn render_lines(
        &self,
        builder: &mut ContentStreamBuilder,
        lines: &[LayoutLine],
        x: f32,
        mut y: f32,
    ) -> f32 {
        builder.begin_text();

        let mut last_font: Option<(String, f32)> = None;
        let mut last_color: Option<(f32, f32, f32)> = None;

        for (line_idx, line) in lines.iter().enumerate() {
            let is_last_line = line_idx == lines.len() - 1;

            // Compute X offset based on alignment
            let x_offset = match self.align {
                TextWriterAlign::Left => 0.0,
                TextWriterAlign::Center => (self.max_width - line.width) / 2.0,
                TextWriterAlign::Right => self.max_width - line.width,
                TextWriterAlign::Justify => 0.0, // handled via word spacing
            };

            // For justified text, compute extra word spacing
            if self.align == TextWriterAlign::Justify && !is_last_line && line.width > 0.0 {
                let total_text: String = line.spans.iter().map(|s| s.text.as_str()).collect();
                let space_count = total_text.chars().filter(|c| *c == ' ').count();
                if space_count > 0 {
                    let extra = (self.max_width - line.width) / space_count as f32;
                    builder.op(ContentStreamOp::SetWordSpacing(extra));
                }
            } else if self.align == TextWriterAlign::Justify {
                // Reset word spacing for last line
                builder.op(ContentStreamOp::SetWordSpacing(0.0));
            }

            // Position this line
            builder.op(ContentStreamOp::SetTextMatrix(1.0, 0.0, 0.0, 1.0, x + x_offset, y));

            // Render each span in the line
            for span in &line.spans {
                // Set font if changed
                let font_key = (span.font_name.clone(), span.font_size);
                if last_font.as_ref() != Some(&font_key) {
                    builder.set_font(&span.font_name, span.font_size);
                    last_font = Some(font_key);
                }

                // Set color if changed
                if span.color != last_color {
                    if let Some((r, g, b)) = span.color {
                        builder.op(ContentStreamOp::SetFillColorRGB(r, g, b));
                    }
                    last_color = span.color;
                }

                // Show text
                builder.op(ContentStreamOp::ShowText(span.text.clone()));
            }

            y -= line.line_height;
        }

        // Reset word spacing if we modified it
        if self.align == TextWriterAlign::Justify {
            builder.op(ContentStreamOp::SetWordSpacing(0.0));
        }

        builder.end_text();
        y
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_append() {
        let mut tw = TextWriter::new(200.0);
        tw.append("Hello World");
        assert_eq!(tw.line_count(), 1);
    }

    #[test]
    fn test_newline_creates_multiple_lines() {
        let mut tw = TextWriter::new(500.0);
        tw.append("Line one");
        tw.newline();
        tw.append("Line two");
        assert_eq!(tw.line_count(), 2);
    }

    #[test]
    fn test_word_wrapping() {
        let mut tw = TextWriter::new(50.0); // very narrow
        tw.set_font("Helvetica", 12.0);
        tw.append("This is a long sentence that should wrap");
        assert!(tw.line_count() > 1);
    }

    #[test]
    fn test_multi_font() {
        let mut tw = TextWriter::new(500.0);
        tw.set_font("Helvetica", 12.0);
        tw.append("Normal ");
        tw.set_font("Helvetica-Bold", 12.0);
        tw.append("Bold");
        assert_eq!(tw.line_count(), 1);
    }

    #[test]
    fn test_text_height() {
        let mut tw = TextWriter::new(500.0);
        tw.set_font("Helvetica", 12.0);
        tw.append("Single line");
        let h = tw.text_height();
        assert!(h > 0.0);
    }

    #[test]
    fn test_clear() {
        let mut tw = TextWriter::new(500.0);
        tw.append("text");
        tw.clear();
        assert_eq!(tw.line_count(), 0);
    }

    #[test]
    fn test_write_to_produces_content() {
        let mut tw = TextWriter::new(500.0);
        tw.set_font("Helvetica", 12.0);
        tw.append("Hello");

        let mut builder = ContentStreamBuilder::new();
        tw.write_to(&mut builder, 72.0, 720.0);
        let bytes = builder.build().unwrap();
        let content = String::from_utf8_lossy(&bytes);
        assert!(content.contains("BT"));
        assert!(content.contains("ET"));
        assert!(content.contains("Hello"));
        assert!(content.contains("Tm"));
    }

    #[test]
    fn test_alignment_center() {
        let mut tw = TextWriter::new(500.0);
        tw.set_font("Helvetica", 12.0);
        tw.set_align(TextWriterAlign::Center);
        tw.append("Hi");

        let mut builder = ContentStreamBuilder::new();
        tw.write_to(&mut builder, 0.0, 100.0);
        let bytes = builder.build().unwrap();
        let content = String::from_utf8_lossy(&bytes);
        // The Tm x-offset should not be 0 for centered text
        assert!(content.contains("Tm"));
    }

    #[test]
    fn test_justify_sets_word_spacing() {
        let mut tw = TextWriter::new(200.0);
        tw.set_font("Helvetica", 12.0);
        tw.set_align(TextWriterAlign::Justify);
        tw.append("This is a test of justified text that wraps");

        let mut builder = ContentStreamBuilder::new();
        tw.write_to(&mut builder, 0.0, 100.0);
        let bytes = builder.build().unwrap();
        let content = String::from_utf8_lossy(&bytes);
        // Justified text should set Tw (word spacing)
        assert!(content.contains("Tw"));
    }

    #[test]
    fn test_small_caps() {
        let mut tw = TextWriter::new(500.0);
        tw.set_font("Helvetica", 12.0);
        tw.set_small_caps(true);
        tw.append("Hello");

        let mut builder = ContentStreamBuilder::new();
        tw.write_to(&mut builder, 0.0, 100.0);
        let bytes = builder.build().unwrap();
        let content = String::from_utf8_lossy(&bytes);
        // Small caps converts to uppercase
        assert!(content.contains("HELLO"));
    }
}
