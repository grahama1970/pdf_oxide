//! SVG renderer for PDF pages.
//!
//! Walks the content stream operator list and emits SVG elements,
//! mirroring the rasterization pipeline in `page_renderer.rs`.

use crate::content::graphics_state::{GraphicsState, GraphicsStateStack, Matrix};
use crate::content::operators::{Operator, TextElement};
use crate::content::parser::parse_content_stream;
use crate::error::Result;
use crate::object::Object;
use crate::PdfDocument;
use std::fmt::Write;

/// Options for SVG rendering.
#[derive(Debug, Clone)]
pub struct SvgOptions {
    /// Whether to embed images as base64 data URIs.
    pub embed_images: bool,
    /// Whether to include text as `<text>` elements (true) or paths (false).
    pub text_as_text: bool,
    /// CSS class prefix for styling.
    pub class_prefix: String,
}

impl Default for SvgOptions {
    fn default() -> Self {
        Self {
            embed_images: true,
            text_as_text: true,
            class_prefix: String::new(),
        }
    }
}

/// SVG renderer that converts PDF page content to SVG markup.
pub struct SvgRenderer {
    options: SvgOptions,
}

impl SvgRenderer {
    /// Create a new SVG renderer with default options.
    pub fn new() -> Self {
        Self {
            options: SvgOptions::default(),
        }
    }

    /// Create a new SVG renderer with custom options.
    pub fn with_options(options: SvgOptions) -> Self {
        Self { options }
    }

    /// Render a PDF page to an SVG string.
    pub fn render_page(&mut self, doc: &mut PdfDocument, page_num: usize) -> Result<String> {
        let page_info = doc.get_page_info(page_num)?;
        let media_box = page_info.media_box;

        let width = media_box.width;
        let height = media_box.height;

        let content_data = doc.get_page_content_data(page_num)?;
        let operators = parse_content_stream(&content_data)?;
        let resources = doc.get_page_resources(page_num)?;

        let mut svg = String::with_capacity(4096);

        // SVG header
        write!(
            svg,
            r#"<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{}" height="{}" viewBox="{} {} {} {}">"#,
            width, height, media_box.x, media_box.y, width, height
        )
        .unwrap();
        svg.push('\n');

        // PDF uses Y-up, SVG uses Y-down. Apply a flip transform.
        write!(svg, r#"<g transform="translate(0,{}) scale(1,-1)">"#, height).unwrap();
        svg.push('\n');

        self.process_operators(&mut svg, &operators, doc, page_num, &resources)?;

        svg.push_str("</g>\n</svg>\n");
        Ok(svg)
    }

    fn process_operators(
        &self,
        svg: &mut String,
        operators: &[Operator],
        doc: &mut PdfDocument,
        page_num: usize,
        resources: &Object,
    ) -> Result<()> {
        let mut gs_stack = GraphicsStateStack::new();
        let mut path_data = String::new();
        let mut in_text_object = false;
        let mut group_depth: usize = 0;

        for op in operators {
            match op {
                // Graphics state
                Operator::SaveState => {
                    gs_stack.save();
                    svg.push_str("<g>\n");
                    group_depth += 1;
                },
                Operator::RestoreState => {
                    gs_stack.restore();
                    if group_depth > 0 {
                        svg.push_str("</g>\n");
                        group_depth -= 1;
                    }
                },
                Operator::Cm { a, b, c, d, e, f } => {
                    let matrix = Matrix {
                        a: *a,
                        b: *b,
                        c: *c,
                        d: *d,
                        e: *e,
                        f: *f,
                    };
                    let current = gs_stack.current_mut();
                    current.ctm = matrix.multiply(&current.ctm);
                },

                // Color operators
                Operator::SetFillRgb { r, g, b } => {
                    gs_stack.current_mut().fill_color_rgb = (*r, *g, *b);
                },
                Operator::SetStrokeRgb { r, g, b } => {
                    gs_stack.current_mut().stroke_color_rgb = (*r, *g, *b);
                },
                Operator::SetFillGray { gray } => {
                    gs_stack.current_mut().fill_color_rgb = (*gray, *gray, *gray);
                },
                Operator::SetStrokeGray { gray } => {
                    gs_stack.current_mut().stroke_color_rgb = (*gray, *gray, *gray);
                },
                Operator::SetFillCmyk { c, m, y, k } => {
                    let (r, g, b) = cmyk_to_rgb(*c, *m, *y, *k);
                    gs_stack.current_mut().fill_color_rgb = (r, g, b);
                },
                Operator::SetStrokeCmyk { c, m, y, k } => {
                    let (r, g, b) = cmyk_to_rgb(*c, *m, *y, *k);
                    gs_stack.current_mut().stroke_color_rgb = (r, g, b);
                },

                // Line style
                Operator::SetLineWidth { width } => {
                    gs_stack.current_mut().line_width = *width;
                },
                Operator::SetLineCap { cap_style } => {
                    gs_stack.current_mut().line_cap = *cap_style;
                },
                Operator::SetLineJoin { join_style } => {
                    gs_stack.current_mut().line_join = *join_style;
                },
                Operator::SetDash { array, phase } => {
                    gs_stack.current_mut().dash_pattern = (array.clone(), *phase);
                },

                // Path construction
                Operator::MoveTo { x, y } => {
                    write!(path_data, "M{} {} ", fmt_f32(*x), fmt_f32(*y)).unwrap();
                },
                Operator::LineTo { x, y } => {
                    write!(path_data, "L{} {} ", fmt_f32(*x), fmt_f32(*y)).unwrap();
                },
                Operator::CurveTo {
                    x1,
                    y1,
                    x2,
                    y2,
                    x3,
                    y3,
                } => {
                    write!(
                        path_data,
                        "C{} {} {} {} {} {} ",
                        fmt_f32(*x1),
                        fmt_f32(*y1),
                        fmt_f32(*x2),
                        fmt_f32(*y2),
                        fmt_f32(*x3),
                        fmt_f32(*y3)
                    )
                    .unwrap();
                },
                Operator::CurveToV { x2, y2, x3, y3 } => {
                    // v operator: first control point = current point (use S as approximation)
                    write!(
                        path_data,
                        "S{} {} {} {} ",
                        fmt_f32(*x2),
                        fmt_f32(*y2),
                        fmt_f32(*x3),
                        fmt_f32(*y3)
                    )
                    .unwrap();
                },
                Operator::CurveToY { x1, y1, x3, y3 } => {
                    // y operator: second control point = endpoint
                    write!(
                        path_data,
                        "C{} {} {} {} {} {} ",
                        fmt_f32(*x1),
                        fmt_f32(*y1),
                        fmt_f32(*x3),
                        fmt_f32(*y3),
                        fmt_f32(*x3),
                        fmt_f32(*y3)
                    )
                    .unwrap();
                },
                Operator::Rectangle {
                    x,
                    y,
                    width,
                    height,
                } => {
                    write!(
                        path_data,
                        "M{} {} L{} {} L{} {} L{} {} Z ",
                        fmt_f32(*x),
                        fmt_f32(*y),
                        fmt_f32(x + width),
                        fmt_f32(*y),
                        fmt_f32(x + width),
                        fmt_f32(y + height),
                        fmt_f32(*x),
                        fmt_f32(y + height),
                    )
                    .unwrap();
                },
                Operator::ClosePath => {
                    path_data.push_str("Z ");
                },

                // Path painting
                Operator::Stroke => {
                    if !path_data.is_empty() {
                        let gs = gs_stack.current();
                        self.emit_path(svg, &path_data, gs, PathPaint::Stroke);
                        path_data.clear();
                    }
                },
                Operator::Fill => {
                    if !path_data.is_empty() {
                        let gs = gs_stack.current();
                        self.emit_path(svg, &path_data, gs, PathPaint::Fill);
                        path_data.clear();
                    }
                },
                Operator::FillEvenOdd => {
                    if !path_data.is_empty() {
                        let gs = gs_stack.current();
                        self.emit_path(svg, &path_data, gs, PathPaint::FillEvenOdd);
                        path_data.clear();
                    }
                },
                Operator::CloseFillStroke => {
                    path_data.push_str("Z ");
                    if !path_data.is_empty() {
                        let gs = gs_stack.current();
                        self.emit_path(svg, &path_data, gs, PathPaint::FillStroke);
                        path_data.clear();
                    }
                },
                Operator::EndPath => {
                    path_data.clear();
                },

                // Text
                Operator::BeginText => {
                    in_text_object = true;
                    let gs = gs_stack.current_mut();
                    gs.text_matrix = Matrix::identity();
                    gs.text_line_matrix = Matrix::identity();
                },
                Operator::EndText => {
                    in_text_object = false;
                },
                Operator::Td { tx, ty } => {
                    if in_text_object {
                        let gs = gs_stack.current_mut();
                        let translation = Matrix::translation(*tx, *ty);
                        gs.text_line_matrix = gs.text_line_matrix.multiply(&translation);
                        gs.text_matrix = gs.text_line_matrix;
                    }
                },
                Operator::TD { tx, ty } => {
                    if in_text_object {
                        let gs = gs_stack.current_mut();
                        gs.leading = -(*ty);
                        let translation = Matrix::translation(*tx, *ty);
                        gs.text_line_matrix = gs.text_line_matrix.multiply(&translation);
                        gs.text_matrix = gs.text_line_matrix;
                    }
                },
                Operator::Tm { a, b, c, d, e, f } => {
                    if in_text_object {
                        let gs = gs_stack.current_mut();
                        gs.text_matrix = Matrix {
                            a: *a,
                            b: *b,
                            c: *c,
                            d: *d,
                            e: *e,
                            f: *f,
                        };
                        gs.text_line_matrix = gs.text_matrix;
                    }
                },
                Operator::TStar => {
                    if in_text_object {
                        let gs = gs_stack.current_mut();
                        let leading = gs.leading;
                        let translation = Matrix::translation(0.0, -leading);
                        gs.text_line_matrix = gs.text_line_matrix.multiply(&translation);
                        gs.text_matrix = gs.text_line_matrix;
                    }
                },
                Operator::Tf { font, size } => {
                    let gs = gs_stack.current_mut();
                    gs.font_name = Some(font.clone());
                    gs.font_size = *size;
                },
                Operator::Tc { char_space } => {
                    gs_stack.current_mut().char_space = *char_space;
                },
                Operator::Tw { word_space } => {
                    gs_stack.current_mut().word_space = *word_space;
                },
                Operator::Tz { scale } => {
                    gs_stack.current_mut().horizontal_scaling = *scale;
                },
                Operator::TL { leading } => {
                    gs_stack.current_mut().leading = *leading;
                },
                Operator::Ts { rise } => {
                    gs_stack.current_mut().text_rise = *rise;
                },
                Operator::Tr { render } => {
                    gs_stack.current_mut().render_mode = *render;
                },

                // Text showing
                Operator::Tj { text } | Operator::Quote { text } => {
                    if in_text_object && self.options.text_as_text {
                        let gs = gs_stack.current();
                        self.emit_text(svg, text, gs);
                        // Advance text matrix
                        let gs_mut = gs_stack.current_mut();
                        let advance = self.compute_text_advance(text, gs_mut);
                        let translation = Matrix::translation(advance, 0.0);
                        gs_mut.text_matrix = gs_mut.text_matrix.multiply(&translation);
                    }
                },
                Operator::TJ { array } => {
                    if in_text_object && self.options.text_as_text {
                        for item in array {
                            match item {
                                TextElement::String(bytes) => {
                                    let gs = gs_stack.current();
                                    self.emit_text(svg, bytes, gs);
                                    let gs_mut = gs_stack.current_mut();
                                    let advance = self.compute_text_advance(bytes, gs_mut);
                                    let translation = Matrix::translation(advance, 0.0);
                                    gs_mut.text_matrix = gs_mut.text_matrix.multiply(&translation);
                                },
                                TextElement::Offset(adj) => {
                                    let gs_mut = gs_stack.current_mut();
                                    let offset = -adj / 1000.0 * gs_mut.font_size;
                                    let h_scale = gs_mut.horizontal_scaling / 100.0;
                                    let translation = Matrix::translation(offset * h_scale, 0.0);
                                    gs_mut.text_matrix = gs_mut.text_matrix.multiply(&translation);
                                },
                            }
                        }
                    }
                },

                // XObject (images)
                Operator::Do { name } => {
                    if self.options.embed_images {
                        let gs = gs_stack.current();
                        self.emit_xobject(svg, name, gs, resources, doc, page_num);
                    }
                },

                _ => {},
            }
        }

        // Close any unclosed groups
        for _ in 0..group_depth {
            svg.push_str("</g>\n");
        }

        Ok(())
    }

    fn emit_path(&self, svg: &mut String, path_data: &str, gs: &GraphicsState, paint: PathPaint) {
        let ctm = &gs.ctm;
        let transform = format!(
            "matrix({},{},{},{},{},{})",
            fmt_f32(ctm.a),
            fmt_f32(ctm.b),
            fmt_f32(ctm.c),
            fmt_f32(ctm.d),
            fmt_f32(ctm.e),
            fmt_f32(ctm.f)
        );

        let fill = match paint {
            PathPaint::Stroke => "none".to_string(),
            PathPaint::Fill | PathPaint::FillStroke | PathPaint::FillEvenOdd => {
                rgb_to_css(gs.fill_color_rgb)
            },
        };

        let stroke = match paint {
            PathPaint::Fill | PathPaint::FillEvenOdd => "none".to_string(),
            PathPaint::Stroke | PathPaint::FillStroke => rgb_to_css(gs.stroke_color_rgb),
        };

        let fill_rule = match paint {
            PathPaint::FillEvenOdd => " fill-rule=\"evenodd\"",
            _ => "",
        };

        let stroke_width = if matches!(paint, PathPaint::Stroke | PathPaint::FillStroke) {
            format!(" stroke-width=\"{}\"", fmt_f32(gs.line_width))
        } else {
            String::new()
        };

        let line_cap = match gs.line_cap {
            0 => "",
            1 => " stroke-linecap=\"round\"",
            2 => " stroke-linecap=\"square\"",
            _ => "",
        };

        let line_join = match gs.line_join {
            0 => "",
            1 => " stroke-linejoin=\"round\"",
            2 => " stroke-linejoin=\"bevel\"",
            _ => "",
        };

        let dash = if !gs.dash_pattern.0.is_empty() {
            let pattern: Vec<String> = gs.dash_pattern.0.iter().map(|v| fmt_f32(*v)).collect();
            format!(
                " stroke-dasharray=\"{}\" stroke-dashoffset=\"{}\"",
                pattern.join(","),
                fmt_f32(gs.dash_pattern.1)
            )
        } else {
            String::new()
        };

        write!(
            svg,
            r#"<path d="{}" fill="{}" stroke="{}"{}{}{}{}{} transform="{}"/>"#,
            path_data.trim(),
            fill,
            stroke,
            fill_rule,
            stroke_width,
            line_cap,
            line_join,
            dash,
            transform,
        )
        .unwrap();
        svg.push('\n');
    }

    fn emit_text(&self, svg: &mut String, text_bytes: &[u8], gs: &GraphicsState) {
        // Decode text (PDF encoding → UTF-8)
        let text_str = String::from_utf8_lossy(text_bytes);
        if text_str.trim().is_empty() {
            return;
        }

        // Compute position from text matrix × CTM
        let tm = &gs.text_matrix;
        let ctm = &gs.ctm;
        let combined = tm.multiply(ctm);

        let x = combined.e;
        let y = combined.f;
        let font_size = gs.font_size
            * (combined.a * combined.a + combined.b * combined.b)
                .sqrt()
                .abs();

        let fill_color = rgb_to_css(gs.fill_color_rgb);
        let font_name = gs.font_name.as_deref().unwrap_or("Helvetica");

        // In SVG coordinate space (after our Y-flip transform), we need to
        // flip text back so it reads correctly.
        // The outer <g> has scale(1,-1), so text would appear mirrored.
        // Apply a local scale(1,-1) to un-mirror the text.
        let escaped = xml_escape(&text_str);
        write!(
            svg,
            r#"<text x="{}" y="{}" font-family="{}" font-size="{}" fill="{}" transform="scale(1,-1) translate(0,{})">{}</text>"#,
            fmt_f32(x),
            fmt_f32(-y), // negate Y because of the outer Y-flip
            font_name,
            fmt_f32(font_size),
            fill_color,
            fmt_f32(2.0 * y), // undo the Y-flip offset
            escaped,
        )
        .unwrap();
        svg.push('\n');
    }

    fn emit_xobject(
        &self,
        svg: &mut String,
        name: &str,
        gs: &GraphicsState,
        resources: &Object,
        doc: &mut PdfDocument,
        _page_num: usize,
    ) {
        // Try to extract image from XObject resources
        if let Object::Dictionary(res_dict) = resources {
            if let Some(Object::Dictionary(xobjects)) = res_dict.get("XObjects") {
                if let Some(xobj_ref_obj) = xobjects.get(name) {
                    if let Some(obj_ref) = xobj_ref_obj.as_reference() {
                        if let Ok(xobj) = doc.load_object(obj_ref) {
                            if let Some(xobj_dict) = xobj.as_dict() {
                                let subtype = xobj_dict
                                    .get("Subtype")
                                    .and_then(|s| s.as_name())
                                    .unwrap_or("");

                                if subtype == "Image" {
                                    if let Ok(image_data) = xobj.decode_stream_data() {
                                        let width = xobj_dict
                                            .get("Width")
                                            .and_then(|w| w.as_integer())
                                            .unwrap_or(1)
                                            as f32;
                                        let height = xobj_dict
                                            .get("Height")
                                            .and_then(|h| h.as_integer())
                                            .unwrap_or(1)
                                            as f32;

                                        let ctm = &gs.ctm;
                                        let b64 = base64_encode(&image_data);
                                        write!(
                                            svg,
                                            r#"<image x="0" y="0" width="{}" height="{}" transform="matrix({},{},{},{},{},{})" href="data:image/png;base64,{}"/>"#,
                                            width, height,
                                            fmt_f32(ctm.a), fmt_f32(ctm.b), fmt_f32(ctm.c), fmt_f32(ctm.d),
                                            fmt_f32(ctm.e), fmt_f32(ctm.f),
                                            b64,
                                        )
                                        .unwrap();
                                        svg.push('\n');
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    fn compute_text_advance(&self, text: &[u8], gs: &GraphicsState) -> f32 {
        let font_size = gs.font_size;
        let h_scale = gs.horizontal_scaling / 100.0;
        let char_space = gs.char_space;
        let word_space = gs.word_space;
        let w0: f32 = 600.0; // Default glyph width in 1/1000 units

        let mut total: f32 = 0.0;
        for &byte in text {
            total += (w0 / 1000.0 * font_size + char_space) * h_scale;
            if byte == 32 {
                total += word_space * h_scale;
            }
        }
        total
    }
}

impl Default for SvgRenderer {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Clone, Copy)]
enum PathPaint {
    Fill,
    FillEvenOdd,
    Stroke,
    FillStroke,
}

/// Format f32 concisely (avoid trailing zeros).
fn fmt_f32(v: f32) -> String {
    if v == v.round() && v.abs() < 1e6 {
        format!("{}", v as i32)
    } else {
        format!("{:.4}", v)
            .trim_end_matches('0')
            .trim_end_matches('.')
            .to_string()
    }
}

/// Convert RGB (0.0-1.0) to CSS color string.
fn rgb_to_css(rgb: (f32, f32, f32)) -> String {
    let r = (rgb.0 * 255.0).round() as u8;
    let g = (rgb.1 * 255.0).round() as u8;
    let b = (rgb.2 * 255.0).round() as u8;
    if r == 0 && g == 0 && b == 0 {
        "#000".to_string()
    } else if r == 255 && g == 255 && b == 255 {
        "#fff".to_string()
    } else {
        format!("#{:02x}{:02x}{:02x}", r, g, b)
    }
}

/// CMYK to RGB conversion.
fn cmyk_to_rgb(c: f32, m: f32, y: f32, k: f32) -> (f32, f32, f32) {
    let r = (1.0 - c) * (1.0 - k);
    let g = (1.0 - m) * (1.0 - k);
    let b = (1.0 - y) * (1.0 - k);
    (r, g, b)
}

/// XML-escape a string for SVG text content.
fn xml_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for ch in s.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&apos;"),
            _ => out.push(ch),
        }
    }
    out
}

/// Simple base64 encoder.
fn base64_encode(data: &[u8]) -> String {
    const CHARS: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut result = String::with_capacity((data.len() + 2) / 3 * 4);
    for chunk in data.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = chunk.get(1).copied().unwrap_or(0) as u32;
        let b2 = chunk.get(2).copied().unwrap_or(0) as u32;
        let triple = (b0 << 16) | (b1 << 8) | b2;
        result.push(CHARS[((triple >> 18) & 0x3F) as usize] as char);
        result.push(CHARS[((triple >> 12) & 0x3F) as usize] as char);
        if chunk.len() > 1 {
            result.push(CHARS[((triple >> 6) & 0x3F) as usize] as char);
        } else {
            result.push('=');
        }
        if chunk.len() > 2 {
            result.push(CHARS[(triple & 0x3F) as usize] as char);
        } else {
            result.push('=');
        }
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fmt_f32() {
        assert_eq!(fmt_f32(0.0), "0");
        assert_eq!(fmt_f32(1.0), "1");
        assert_eq!(fmt_f32(72.5), "72.5");
        assert_eq!(fmt_f32(3.14159), "3.1416");
    }

    #[test]
    fn test_rgb_to_css() {
        assert_eq!(rgb_to_css((0.0, 0.0, 0.0)), "#000");
        assert_eq!(rgb_to_css((1.0, 1.0, 1.0)), "#fff");
        assert_eq!(rgb_to_css((1.0, 0.0, 0.0)), "#ff0000");
    }

    #[test]
    fn test_xml_escape() {
        assert_eq!(xml_escape("Hello"), "Hello");
        assert_eq!(xml_escape("a < b & c"), "a &lt; b &amp; c");
        assert_eq!(xml_escape("\"test\""), "&quot;test&quot;");
    }

    #[test]
    fn test_base64_encode() {
        assert_eq!(base64_encode(b"Hello"), "SGVsbG8=");
        assert_eq!(base64_encode(b"Hi"), "SGk=");
        assert_eq!(base64_encode(b"ABC"), "QUJD");
    }

    #[test]
    fn test_cmyk_to_rgb() {
        let (r, g, b) = cmyk_to_rgb(0.0, 0.0, 0.0, 0.0);
        assert!((r - 1.0).abs() < 0.001);
        assert!((g - 1.0).abs() < 0.001);
        assert!((b - 1.0).abs() < 0.001);

        let (r, g, b) = cmyk_to_rgb(0.0, 0.0, 0.0, 1.0);
        assert!(r.abs() < 0.001);
        assert!(g.abs() < 0.001);
        assert!(b.abs() < 0.001);
    }
}
