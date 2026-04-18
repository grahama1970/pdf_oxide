//! Text rasterizer - renders PDF text using tiny-skia + fontdb + ttf-parser.
//!
//! Uses system fonts via fontdb as fallback when embedded fonts aren't available.
//! Renders actual glyph outlines from TrueType/OpenType fonts.

use super::create_fill_paint;
use crate::content::operators::TextElement;
use crate::content::GraphicsState;
use crate::document::PdfDocument;
use crate::error::Result;
use crate::fonts::text_decode::decode_pdf_text;
use crate::fonts::FontInfo;
use crate::object::Object;

use tiny_skia::{Paint, PathBuilder, Pixmap, Transform};

/// Rasterizer for PDF text operations.
pub struct TextRasterizer {
    font_db: fontdb::Database,
    /// Cached fallback font ID
    fallback_font_id: Option<fontdb::ID>,
}

impl TextRasterizer {
    /// Create a new text rasterizer with system fonts loaded.
    pub fn new() -> Self {
        let mut db = fontdb::Database::new();
        db.load_system_fonts();

        // Find a good fallback font (prefer common sans-serif)
        let fallback = db
            .faces()
            .find(|f| {
                f.families.iter().any(|(name, _)| {
                    let n = name.to_lowercase();
                    n == "liberation sans" || n == "dejavu sans" || n == "arial" || n == "noto sans"
                }) && f.style == fontdb::Style::Normal
                    && f.weight == fontdb::Weight(400)
            })
            .map(|f| f.id);

        // If no preferred font, just grab the first normal-weight sans font
        let fallback = fallback.or_else(|| {
            db.faces()
                .find(|f| f.style == fontdb::Style::Normal && f.weight == fontdb::Weight(400))
                .map(|f| f.id)
        });

        Self {
            font_db: db,
            fallback_font_id: fallback,
        }
    }

    /// Render a text string (Tj operator).
    pub fn render_text(
        &self,
        pixmap: &mut Pixmap,
        text: &[u8],
        base_transform: Transform,
        gs: &GraphicsState,
        font_info: Option<&FontInfo>,
        _resources: &Object,
        _doc: &mut PdfDocument,
        clip_mask: Option<&tiny_skia::Mask>,
    ) -> Result<()> {
        let text_matrix = &gs.text_matrix;
        let font_size = gs.font_size;
        let paint = create_fill_paint(gs, "Normal");
        let x = text_matrix.e;
        let y = text_matrix.f;

        self.render_text_glyphs(
            pixmap,
            text,
            x,
            y,
            font_size,
            &paint,
            base_transform,
            gs,
            font_info,
            clip_mask,
        )?;
        Ok(())
    }

    /// Render a TJ array (text with positioning adjustments).
    pub fn render_tj_array(
        &self,
        pixmap: &mut Pixmap,
        array: &[TextElement],
        base_transform: Transform,
        gs: &GraphicsState,
        font_info: Option<&FontInfo>,
        _resources: &Object,
        _doc: &mut PdfDocument,
        clip_mask: Option<&tiny_skia::Mask>,
    ) -> Result<()> {
        let paint = create_fill_paint(gs, "Normal");
        let font_size = gs.font_size;
        let text_matrix = &gs.text_matrix;
        let mut current_x = text_matrix.e;
        let y = text_matrix.f;

        for element in array {
            match element {
                TextElement::String(text) => {
                    self.render_text_glyphs(
                        pixmap,
                        text,
                        current_x,
                        y,
                        font_size,
                        &paint,
                        base_transform,
                        gs,
                        font_info,
                        clip_mask,
                    )?;
                    // Advance using font widths if available, else fallback
                    let advance = self.compute_text_advance(text, font_info, font_size, gs);
                    current_x += advance;
                },
                TextElement::Offset(offset) => {
                    let adjustment = -(*offset) / 1000.0 * font_size;
                    current_x += adjustment;
                },
            }
        }

        Ok(())
    }

    /// Compute text advance width using PDF font widths when available.
    fn compute_text_advance(
        &self,
        text: &[u8],
        font_info: Option<&FontInfo>,
        font_size: f32,
        gs: &GraphicsState,
    ) -> f32 {
        let h_scale = gs.horizontal_scaling / 100.0;
        let char_space = gs.char_space;
        let word_space = gs.word_space;

        if let Some(font) = font_info {
            // Use PDF font widths for accurate positioning
            let decoded = decode_pdf_text(text, Some(font));
            let mut total: f32 = 0.0;
            for glyph in &decoded {
                let w = font.get_glyph_width(glyph.char_code as u16);
                total += (w / 1000.0 * font_size + char_space) * h_scale;
                // Word spacing for space character
                if glyph.unicode == " " {
                    total += word_space * h_scale;
                }
            }
            total
        } else {
            // Fallback: fixed-width estimate
            let char_count = text.len() as f32;
            char_count * font_size * 0.5
        }
    }

    /// Render text using actual glyph outlines from system fonts.
    ///
    /// Decodes PDF bytes through the shared text decoder (using FontInfo when
    /// available) to get proper Unicode, then renders glyph outlines from the
    /// system fallback font.
    fn render_text_glyphs(
        &self,
        pixmap: &mut Pixmap,
        text: &[u8],
        x: f32,
        y: f32,
        font_size: f32,
        paint: &Paint,
        base_transform: Transform,
        gs: &GraphicsState,
        font_info: Option<&FontInfo>,
        clip_mask: Option<&tiny_skia::Mask>,
    ) -> Result<()> {
        let text_transform = Transform::from_row(
            gs.text_matrix.a,
            gs.text_matrix.b,
            gs.text_matrix.c,
            gs.text_matrix.d,
            0.0,
            0.0,
        );
        let transform = base_transform.pre_concat(text_transform);

        let font_id = match self.fallback_font_id {
            Some(id) => id,
            None => return Ok(()), // No font available, skip rendering
        };

        // Decode PDF bytes through the shared text decoder.
        // With FontInfo: uses ToUnicode CMap, encoding tables, etc.
        // Without FontInfo: falls back to Latin-1 (same as before).
        let decoded = decode_pdf_text(text, font_info);

        self.font_db
            .with_face_data(font_id, |font_data, face_index| {
                let face = match ttf_parser::Face::parse(font_data, face_index) {
                    Ok(f) => f,
                    Err(_) => return,
                };

                let units_per_em = face.units_per_em() as f32;
                let scale = font_size / units_per_em;
                let mut current_x = x;
                let mut glyphs_found = 0u32;
                let mut glyphs_missing = 0u32;

                for glyph in &decoded {
                    for ch in glyph.unicode.chars() {
                        if ch == ' ' {
                            current_x += font_size * 0.25 + gs.word_space;
                            continue;
                        }

                        let glyph_id = match face.glyph_index(ch) {
                            Some(id) => {
                                glyphs_found += 1;
                                id
                            },
                            None => {
                                glyphs_missing += 1;
                                current_x += font_size * 0.5;
                                continue;
                            },
                        };

                        // Get advance width from system font
                        let advance = face
                            .glyph_hor_advance(glyph_id)
                            .unwrap_or((units_per_em * 0.5) as u16)
                            as f32
                            * scale;

                        // Build glyph path
                        let mut builder = GlyphPathBuilder::new(current_x, y, scale);
                        if face.outline_glyph(glyph_id, &mut builder).is_some() {
                            if let Some(path) = builder.finish() {
                                pixmap.fill_path(
                                    &path,
                                    paint,
                                    tiny_skia::FillRule::EvenOdd,
                                    transform,
                                    clip_mask,
                                );
                            }
                        }

                        current_x += advance + gs.char_space;
                    }
                }

                if glyphs_found > 0 || glyphs_missing > 0 {
                    let preview: String = decoded
                        .iter()
                        .flat_map(|g| g.unicode.chars())
                        .take(30)
                        .collect();
                    eprintln!(
                        "[text_raster] '{}' found={} missing={} font_size={:.1} x={:.0} y={:.0}",
                        preview, glyphs_found, glyphs_missing, font_size, x, y
                    );
                }
            });

        Ok(())
    }
}

/// Converts ttf-parser glyph outline callbacks into a tiny-skia path.
struct GlyphPathBuilder {
    builder: PathBuilder,
    x_offset: f32,
    y_offset: f32,
    scale: f32,
}

impl GlyphPathBuilder {
    fn new(x_offset: f32, y_offset: f32, scale: f32) -> Self {
        Self {
            builder: PathBuilder::new(),
            x_offset,
            y_offset,
            scale,
        }
    }

    fn finish(self) -> Option<tiny_skia::Path> {
        self.builder.finish()
    }

    fn tx(&self, x: f32) -> f32 {
        self.x_offset + x * self.scale
    }

    fn ty(&self, y: f32) -> f32 {
        // Glyph y-axis goes up, PDF y-axis goes up — no flip needed here,
        // the base_transform handles the PDF→pixel flip
        self.y_offset + y * self.scale
    }
}

impl ttf_parser::OutlineBuilder for GlyphPathBuilder {
    fn move_to(&mut self, x: f32, y: f32) {
        self.builder.move_to(self.tx(x), self.ty(y));
    }

    fn line_to(&mut self, x: f32, y: f32) {
        self.builder.line_to(self.tx(x), self.ty(y));
    }

    fn quad_to(&mut self, x1: f32, y1: f32, x: f32, y: f32) {
        self.builder
            .quad_to(self.tx(x1), self.ty(y1), self.tx(x), self.ty(y));
    }

    fn curve_to(&mut self, x1: f32, y1: f32, x2: f32, y2: f32, x: f32, y: f32) {
        self.builder.cubic_to(
            self.tx(x1),
            self.ty(y1),
            self.tx(x2),
            self.ty(y2),
            self.tx(x),
            self.ty(y),
        );
    }

    fn close(&mut self) {
        self.builder.close();
    }
}

impl Default for TextRasterizer {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_text_rasterizer_new() {
        let rasterizer = TextRasterizer::new();
        assert!(rasterizer.fallback_font_id.is_some(), "Should find at least one system font");
    }
}
