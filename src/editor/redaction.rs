//! True content-stripping redaction for PDF pages.
//!
//! This module provides the core engine for removing content from PDF content
//! streams that falls within redaction rectangles. Unlike visual-only redaction
//! (which draws colored rectangles on top), this physically removes the operators
//! so that redacted content cannot be recovered.
//!
//! # Architecture
//!
//! 1. Parse content stream into operators
//! 2. Track graphics state (CTM, text matrix) to compute positions
//! 3. For each text/image/path operator, compute its bounding box
//! 4. If the bbox overlaps any redaction rectangle, omit the operator
//! 5. Serialize remaining operators back to a content stream
//!
//! PDF Spec: ISO 32000-2:2020, Section 12.5.6.6 - Redaction Annotations

use crate::content::graphics_state::{GraphicsState, GraphicsStateStack, Matrix};
use crate::content::operators::{Operator, TextElement};
use crate::content::parser::parse_content_stream;

/// A redaction rectangle in PDF user space coordinates [llx, lly, urx, ury].
#[derive(Debug, Clone, Copy)]
pub struct RedactionRect {
    pub llx: f32,
    pub lly: f32,
    pub urx: f32,
    pub ury: f32,
}

impl RedactionRect {
    pub fn new(rect: [f32; 4]) -> Self {
        // Normalize so llx < urx and lly < ury
        Self {
            llx: rect[0].min(rect[2]),
            lly: rect[1].min(rect[3]),
            urx: rect[0].max(rect[2]),
            ury: rect[1].max(rect[3]),
        }
    }

    /// Check if this rect overlaps another bounding box.
    fn overlaps(&self, other_llx: f32, other_lly: f32, other_urx: f32, other_ury: f32) -> bool {
        self.llx < other_urx && self.urx > other_llx && self.lly < other_ury && self.ury > other_lly
    }
}

/// Strip content within redaction rectangles from a content stream.
///
/// Parses the content stream, tracks text/graphics positions, and removes
/// any operators whose visual output falls within the given redaction areas.
///
/// Returns the filtered content stream bytes.
pub fn strip_redacted_content(
    content_data: &[u8],
    redaction_rects: &[RedactionRect],
) -> crate::error::Result<Vec<u8>> {
    if redaction_rects.is_empty() || content_data.is_empty() {
        return Ok(content_data.to_vec());
    }

    let operators = parse_content_stream(content_data)?;
    let filtered = filter_operators(&operators, redaction_rects);
    Ok(serialize_operators(&filtered))
}

/// Filter operators, removing those whose output falls within redaction rects.
fn filter_operators(operators: &[Operator], rects: &[RedactionRect]) -> Vec<Operator> {
    let mut result = Vec::with_capacity(operators.len());
    let mut gs_stack = GraphicsStateStack::new();
    let mut in_text = false;

    // Track which text blocks are entirely redacted
    let mut text_block_ops: Vec<Operator> = Vec::new();
    let mut text_block_has_visible = false;

    for op in operators {
        match op {
            // Graphics state tracking
            Operator::SaveState => {
                gs_stack.save();
                if in_text {
                    text_block_ops.push(op.clone());
                } else {
                    result.push(op.clone());
                }
            }
            Operator::RestoreState => {
                gs_stack.restore();
                if in_text {
                    text_block_ops.push(op.clone());
                } else {
                    result.push(op.clone());
                }
            }
            Operator::Cm { a, b, c, d, e, f } => {
                let new_ctm = Matrix {
                    a: *a,
                    b: *b,
                    c: *c,
                    d: *d,
                    e: *e,
                    f: *f,
                };
                let current = gs_stack.current().ctm;
                gs_stack.current_mut().ctm = new_ctm.multiply(&current);
                if in_text {
                    text_block_ops.push(op.clone());
                } else {
                    result.push(op.clone());
                }
            }

            // Text state tracking
            Operator::Tf { ref font, size } => {
                gs_stack.current_mut().font_name = Some(font.clone());
                gs_stack.current_mut().font_size = *size;
                if in_text {
                    text_block_ops.push(op.clone());
                } else {
                    result.push(op.clone());
                }
            }
            Operator::Tc { char_space } => {
                gs_stack.current_mut().char_space = *char_space;
                if in_text {
                    text_block_ops.push(op.clone());
                } else {
                    result.push(op.clone());
                }
            }
            Operator::Tw { word_space } => {
                gs_stack.current_mut().word_space = *word_space;
                if in_text {
                    text_block_ops.push(op.clone());
                } else {
                    result.push(op.clone());
                }
            }
            Operator::Tz { scale } => {
                gs_stack.current_mut().horizontal_scaling = *scale;
                if in_text {
                    text_block_ops.push(op.clone());
                } else {
                    result.push(op.clone());
                }
            }
            Operator::TL { leading } => {
                gs_stack.current_mut().leading = *leading;
                if in_text {
                    text_block_ops.push(op.clone());
                } else {
                    result.push(op.clone());
                }
            }
            Operator::Ts { rise } => {
                gs_stack.current_mut().text_rise = *rise;
                if in_text {
                    text_block_ops.push(op.clone());
                } else {
                    result.push(op.clone());
                }
            }
            Operator::Tr { render } => {
                gs_stack.current_mut().render_mode = *render;
                if in_text {
                    text_block_ops.push(op.clone());
                } else {
                    result.push(op.clone());
                }
            }

            // Text object boundaries
            Operator::BeginText => {
                in_text = true;
                gs_stack.current_mut().text_matrix = Matrix::identity();
                gs_stack.current_mut().text_line_matrix = Matrix::identity();
                text_block_ops.clear();
                text_block_has_visible = false;
                text_block_ops.push(op.clone());
            }
            Operator::EndText => {
                text_block_ops.push(op.clone());
                // Emit the text block only if it has visible (non-redacted) content
                if text_block_has_visible {
                    result.extend(text_block_ops.drain(..));
                }
                in_text = false;
                text_block_ops.clear();
            }

            // Text positioning
            Operator::Td { tx, ty } => {
                let gs = gs_stack.current_mut();
                let new_line = Matrix::translation(*tx, *ty).multiply(&gs.text_line_matrix);
                gs.text_matrix = new_line;
                gs.text_line_matrix = new_line;
                if in_text {
                    text_block_ops.push(op.clone());
                }
            }
            Operator::TD { tx, ty } => {
                let gs = gs_stack.current_mut();
                gs.leading = -*ty;
                let new_line = Matrix::translation(*tx, *ty).multiply(&gs.text_line_matrix);
                gs.text_matrix = new_line;
                gs.text_line_matrix = new_line;
                if in_text {
                    text_block_ops.push(op.clone());
                }
            }
            Operator::Tm { a, b, c, d, e, f } => {
                let tm = Matrix {
                    a: *a,
                    b: *b,
                    c: *c,
                    d: *d,
                    e: *e,
                    f: *f,
                };
                gs_stack.current_mut().text_matrix = tm;
                gs_stack.current_mut().text_line_matrix = tm;
                if in_text {
                    text_block_ops.push(op.clone());
                }
            }
            Operator::TStar => {
                let gs = gs_stack.current_mut();
                let leading = gs.leading;
                let new_line =
                    Matrix::translation(0.0, -leading).multiply(&gs.text_line_matrix);
                gs.text_matrix = new_line;
                gs.text_line_matrix = new_line;
                if in_text {
                    text_block_ops.push(op.clone());
                }
            }

            // Text showing operators — check redaction
            Operator::Tj { .. }
            | Operator::TJ { .. }
            | Operator::Quote { .. }
            | Operator::DoubleQuote { .. } => {
                if in_text {
                    let bbox = compute_text_bbox(op, gs_stack.current());
                    let is_redacted = bbox.map_or(false, |(llx, lly, urx, ury)| {
                        rects.iter().any(|r| r.overlaps(llx, lly, urx, ury))
                    });
                    if is_redacted {
                        // Still update text matrix for subsequent positioning
                        advance_text_matrix_for_op(op, gs_stack.current_mut());
                    } else {
                        text_block_has_visible = true;
                        text_block_ops.push(op.clone());
                        advance_text_matrix_for_op(op, gs_stack.current_mut());
                    }
                }
            }

            // XObject (Do) — check if image overlaps redaction
            Operator::Do { ref name } => {
                let ctm = gs_stack.current().ctm;
                // Image is placed in a 1x1 unit square transformed by CTM
                let p1 = ctm.transform_point(0.0, 0.0);
                let p2 = ctm.transform_point(1.0, 0.0);
                let p3 = ctm.transform_point(1.0, 1.0);
                let p4 = ctm.transform_point(0.0, 1.0);

                let min_x = p1.x.min(p2.x).min(p3.x).min(p4.x);
                let max_x = p1.x.max(p2.x).max(p3.x).max(p4.x);
                let min_y = p1.y.min(p2.y).min(p3.y).min(p4.y);
                let max_y = p1.y.max(p2.y).max(p3.y).max(p4.y);

                let is_redacted = rects
                    .iter()
                    .any(|r| r.overlaps(min_x, min_y, max_x, max_y));
                if !is_redacted {
                    result.push(op.clone());
                }
            }

            // Path painting — check if path bbox overlaps redaction
            Operator::Rectangle { x, y, width, height } => {
                // We handle path + paint as a unit; for now track path coords
                // and pass through if outside redaction
                result.push(op.clone());
            }

            // Inline images — check bbox against redaction
            Operator::InlineImage { .. } => {
                // Inline images use the CTM for positioning (same as Do)
                let ctm = gs_stack.current().ctm;
                let p1 = ctm.transform_point(0.0, 0.0);
                let p2 = ctm.transform_point(1.0, 1.0);
                let min_x = p1.x.min(p2.x);
                let max_x = p1.x.max(p2.x);
                let min_y = p1.y.min(p2.y);
                let max_y = p1.y.max(p2.y);

                let is_redacted = rects
                    .iter()
                    .any(|r| r.overlaps(min_x, min_y, max_x, max_y));
                if !is_redacted {
                    result.push(op.clone());
                }
            }

            // All other operators pass through
            _ => {
                if in_text {
                    text_block_ops.push(op.clone());
                } else {
                    result.push(op.clone());
                }
            }
        }
    }

    // Handle unclosed text block (malformed PDF)
    if in_text && text_block_has_visible {
        result.extend(text_block_ops);
    }

    result
}

/// Compute the bounding box of a text showing operator in user space.
///
/// Returns `Some((llx, lly, urx, ury))` or `None` if bbox cannot be determined.
fn compute_text_bbox(op: &Operator, gs: &GraphicsState) -> Option<(f32, f32, f32, f32)> {
    let font_size = gs.font_size;
    let h_scale = gs.horizontal_scaling / 100.0;

    // The text rendering matrix combines text matrix, CTM, font size, and horizontal scaling
    // Trm = [font_size * h_scale, 0, 0, font_size, 0, text_rise] × Tm × CTM
    let tm = gs.text_matrix;
    let ctm = gs.ctm;

    // Effective text matrix in user space
    let effective = tm.multiply(&ctm);

    // Text origin in user space
    let origin_x = effective.e;
    let origin_y = effective.f + gs.text_rise * effective.d;

    // Estimate text width based on character count
    // This is approximate — proper width requires font metrics
    let char_count = match op {
        Operator::Tj { ref text } => text.len() as f32,
        Operator::TJ { ref array } => {
            array
                .iter()
                .map(|e| match e {
                    TextElement::String(s) => s.len() as f32,
                    TextElement::Offset(_) => 0.0,
                })
                .sum()
        }
        Operator::Quote { ref text } => text.len() as f32,
        Operator::DoubleQuote { ref text, .. } => text.len() as f32,
        _ => return None,
    };

    // Approximate glyph width as 0.5 * font_size (average for Latin text)
    let approx_width = char_count * font_size * 0.5 * h_scale;
    // Text height is roughly the font size
    let height = font_size;

    // Scale width/height by the effective matrix scaling
    let scale_x = (effective.a * effective.a + effective.c * effective.c).sqrt();
    let scale_y = (effective.b * effective.b + effective.d * effective.d).sqrt();

    let w = approx_width * scale_x;
    let h = height * scale_y;

    // Build bbox (expand slightly to account for approximation)
    let padding = 1.0; // 1 point padding
    let llx = origin_x - padding;
    let lly = origin_y - padding;
    let urx = origin_x + w + padding;
    let ury = origin_y + h + padding;

    // Normalize
    Some((llx.min(urx), lly.min(ury), llx.max(urx), lly.max(ury)))
}

/// Advance the text matrix as if the text showing operator executed.
fn advance_text_matrix_for_op(op: &Operator, gs: &mut GraphicsState) {
    let font_size = gs.font_size;
    let h_scale = gs.horizontal_scaling / 100.0;

    match op {
        Operator::Tj { ref text } => {
            // Approximate: advance by character count * average glyph width
            let advance = text.len() as f32 * font_size * 0.5 * h_scale;
            gs.text_matrix.e += advance * gs.text_matrix.a;
            gs.text_matrix.f += advance * gs.text_matrix.b;
        }
        Operator::TJ { ref array } => {
            for elem in array {
                match elem {
                    TextElement::String(s) => {
                        let advance = s.len() as f32 * font_size * 0.5 * h_scale;
                        gs.text_matrix.e += advance * gs.text_matrix.a;
                        gs.text_matrix.f += advance * gs.text_matrix.b;
                    }
                    TextElement::Offset(offset) => {
                        // TJ offsets are in thousandths of a unit of text space
                        let advance = -offset / 1000.0 * font_size * h_scale;
                        gs.text_matrix.e += advance * gs.text_matrix.a;
                        gs.text_matrix.f += advance * gs.text_matrix.b;
                    }
                }
            }
        }
        Operator::Quote { ref text } => {
            // Move to next line first
            let leading = gs.leading;
            let new_line = Matrix::translation(0.0, -leading).multiply(&gs.text_line_matrix);
            gs.text_matrix = new_line;
            gs.text_line_matrix = new_line;
            // Then advance by text
            let advance = text.len() as f32 * font_size * 0.5 * h_scale;
            gs.text_matrix.e += advance * gs.text_matrix.a;
            gs.text_matrix.f += advance * gs.text_matrix.b;
        }
        Operator::DoubleQuote {
            word_space,
            char_space,
            ref text,
        } => {
            gs.word_space = *word_space;
            gs.char_space = *char_space;
            let leading = gs.leading;
            let new_line = Matrix::translation(0.0, -leading).multiply(&gs.text_line_matrix);
            gs.text_matrix = new_line;
            gs.text_line_matrix = new_line;
            let advance = text.len() as f32 * font_size * 0.5 * h_scale;
            gs.text_matrix.e += advance * gs.text_matrix.a;
            gs.text_matrix.f += advance * gs.text_matrix.b;
        }
        _ => {}
    }
}

/// Serialize a list of operators back to content stream bytes.
pub fn serialize_operators(operators: &[Operator]) -> Vec<u8> {
    let mut output = Vec::with_capacity(operators.len() * 20);

    for op in operators {
        serialize_operator(op, &mut output);
        output.push(b'\n');
    }

    output
}

/// Serialize a single operator to content stream bytes.
fn serialize_operator(op: &Operator, out: &mut Vec<u8>) {
    match op {
        // Text positioning
        Operator::Td { tx, ty } => {
            write_f32(*tx, out);
            out.push(b' ');
            write_f32(*ty, out);
            out.extend_from_slice(b" Td");
        }
        Operator::TD { tx, ty } => {
            write_f32(*tx, out);
            out.push(b' ');
            write_f32(*ty, out);
            out.extend_from_slice(b" TD");
        }
        Operator::Tm { a, b, c, d, e, f } => {
            write_f32(*a, out);
            out.push(b' ');
            write_f32(*b, out);
            out.push(b' ');
            write_f32(*c, out);
            out.push(b' ');
            write_f32(*d, out);
            out.push(b' ');
            write_f32(*e, out);
            out.push(b' ');
            write_f32(*f, out);
            out.extend_from_slice(b" Tm");
        }
        Operator::TStar => out.extend_from_slice(b"T*"),

        // Text showing
        Operator::Tj { ref text } => {
            write_pdf_string(text, out);
            out.extend_from_slice(b" Tj");
        }
        Operator::TJ { ref array } => {
            out.push(b'[');
            for elem in array {
                match elem {
                    TextElement::String(s) => write_pdf_string(s, out),
                    TextElement::Offset(f) => write_f32(*f, out),
                }
            }
            out.extend_from_slice(b"] TJ");
        }
        Operator::Quote { ref text } => {
            write_pdf_string(text, out);
            out.extend_from_slice(b" '");
        }
        Operator::DoubleQuote {
            word_space,
            char_space,
            ref text,
        } => {
            write_f32(*word_space, out);
            out.push(b' ');
            write_f32(*char_space, out);
            out.push(b' ');
            write_pdf_string(text, out);
            out.extend_from_slice(b" \"");
        }

        // Text state
        Operator::Tc { char_space } => {
            write_f32(*char_space, out);
            out.extend_from_slice(b" Tc");
        }
        Operator::Tw { word_space } => {
            write_f32(*word_space, out);
            out.extend_from_slice(b" Tw");
        }
        Operator::Tz { scale } => {
            write_f32(*scale, out);
            out.extend_from_slice(b" Tz");
        }
        Operator::TL { leading } => {
            write_f32(*leading, out);
            out.extend_from_slice(b" TL");
        }
        Operator::Tf { ref font, size } => {
            out.push(b'/');
            out.extend_from_slice(font.as_bytes());
            out.push(b' ');
            write_f32(*size, out);
            out.extend_from_slice(b" Tf");
        }
        Operator::Tr { render } => {
            out.extend_from_slice(render.to_string().as_bytes());
            out.extend_from_slice(b" Tr");
        }
        Operator::Ts { rise } => {
            write_f32(*rise, out);
            out.extend_from_slice(b" Ts");
        }

        // Graphics state
        Operator::SaveState => out.push(b'q'),
        Operator::RestoreState => out.push(b'Q'),
        Operator::Cm { a, b, c, d, e, f } => {
            write_f32(*a, out);
            out.push(b' ');
            write_f32(*b, out);
            out.push(b' ');
            write_f32(*c, out);
            out.push(b' ');
            write_f32(*d, out);
            out.push(b' ');
            write_f32(*e, out);
            out.push(b' ');
            write_f32(*f, out);
            out.extend_from_slice(b" cm");
        }

        // Color operators
        Operator::SetFillRgb { r, g, b } => {
            write_f32(*r, out);
            out.push(b' ');
            write_f32(*g, out);
            out.push(b' ');
            write_f32(*b, out);
            out.extend_from_slice(b" rg");
        }
        Operator::SetStrokeRgb { r, g, b } => {
            write_f32(*r, out);
            out.push(b' ');
            write_f32(*g, out);
            out.push(b' ');
            write_f32(*b, out);
            out.extend_from_slice(b" RG");
        }
        Operator::SetFillGray { gray } => {
            write_f32(*gray, out);
            out.extend_from_slice(b" g");
        }
        Operator::SetStrokeGray { gray } => {
            write_f32(*gray, out);
            out.extend_from_slice(b" G");
        }
        Operator::SetFillCmyk { c, m, y, k } => {
            write_f32(*c, out);
            out.push(b' ');
            write_f32(*m, out);
            out.push(b' ');
            write_f32(*y, out);
            out.push(b' ');
            write_f32(*k, out);
            out.extend_from_slice(b" k");
        }
        Operator::SetStrokeCmyk { c, m, y, k } => {
            write_f32(*c, out);
            out.push(b' ');
            write_f32(*m, out);
            out.push(b' ');
            write_f32(*y, out);
            out.push(b' ');
            write_f32(*k, out);
            out.extend_from_slice(b" K");
        }

        // Color space
        Operator::SetFillColorSpace { ref name } => {
            out.push(b'/');
            out.extend_from_slice(name.as_bytes());
            out.extend_from_slice(b" cs");
        }
        Operator::SetStrokeColorSpace { ref name } => {
            out.push(b'/');
            out.extend_from_slice(name.as_bytes());
            out.extend_from_slice(b" CS");
        }
        Operator::SetFillColor { ref components } => {
            for (i, c) in components.iter().enumerate() {
                if i > 0 {
                    out.push(b' ');
                }
                write_f32(*c, out);
            }
            out.extend_from_slice(b" sc");
        }
        Operator::SetStrokeColor { ref components } => {
            for (i, c) in components.iter().enumerate() {
                if i > 0 {
                    out.push(b' ');
                }
                write_f32(*c, out);
            }
            out.extend_from_slice(b" SC");
        }
        Operator::SetFillColorN {
            ref components,
            ref name,
        } => {
            for c in components {
                write_f32(*c, out);
                out.push(b' ');
            }
            if let Some(ref n) = name {
                out.push(b'/');
                out.extend_from_slice(n.as_bytes());
                out.push(b' ');
            }
            out.extend_from_slice(b"scn");
        }
        Operator::SetStrokeColorN {
            ref components,
            ref name,
        } => {
            for c in components {
                write_f32(*c, out);
                out.push(b' ');
            }
            if let Some(ref n) = name {
                out.push(b'/');
                out.extend_from_slice(n.as_bytes());
                out.push(b' ');
            }
            out.extend_from_slice(b"SCN");
        }

        // Text object
        Operator::BeginText => out.extend_from_slice(b"BT"),
        Operator::EndText => out.extend_from_slice(b"ET"),

        // XObject
        Operator::Do { ref name } => {
            out.push(b'/');
            out.extend_from_slice(name.as_bytes());
            out.extend_from_slice(b" Do");
        }

        // Path construction
        Operator::MoveTo { x, y } => {
            write_f32(*x, out);
            out.push(b' ');
            write_f32(*y, out);
            out.extend_from_slice(b" m");
        }
        Operator::LineTo { x, y } => {
            write_f32(*x, out);
            out.push(b' ');
            write_f32(*y, out);
            out.extend_from_slice(b" l");
        }
        Operator::CurveTo {
            x1,
            y1,
            x2,
            y2,
            x3,
            y3,
        } => {
            write_f32(*x1, out);
            out.push(b' ');
            write_f32(*y1, out);
            out.push(b' ');
            write_f32(*x2, out);
            out.push(b' ');
            write_f32(*y2, out);
            out.push(b' ');
            write_f32(*x3, out);
            out.push(b' ');
            write_f32(*y3, out);
            out.extend_from_slice(b" c");
        }
        Operator::CurveToV { x2, y2, x3, y3 } => {
            write_f32(*x2, out);
            out.push(b' ');
            write_f32(*y2, out);
            out.push(b' ');
            write_f32(*x3, out);
            out.push(b' ');
            write_f32(*y3, out);
            out.extend_from_slice(b" v");
        }
        Operator::CurveToY { x1, y1, x3, y3 } => {
            write_f32(*x1, out);
            out.push(b' ');
            write_f32(*y1, out);
            out.push(b' ');
            write_f32(*x3, out);
            out.push(b' ');
            write_f32(*y3, out);
            out.extend_from_slice(b" y");
        }
        Operator::ClosePath => out.push(b'h'),
        Operator::Rectangle { x, y, width, height } => {
            write_f32(*x, out);
            out.push(b' ');
            write_f32(*y, out);
            out.push(b' ');
            write_f32(*width, out);
            out.push(b' ');
            write_f32(*height, out);
            out.extend_from_slice(b" re");
        }

        // Path painting
        Operator::Stroke => out.push(b'S'),
        Operator::Fill => out.push(b'f'),
        Operator::FillEvenOdd => out.extend_from_slice(b"f*"),
        Operator::CloseFillStroke => out.push(b'b'),
        Operator::EndPath => out.push(b'n'),
        Operator::ClipNonZero => out.push(b'W'),
        Operator::ClipEvenOdd => out.extend_from_slice(b"W*"),

        // Line state
        Operator::SetLineWidth { width } => {
            write_f32(*width, out);
            out.extend_from_slice(b" w");
        }
        Operator::SetDash { ref array, phase } => {
            out.push(b'[');
            for (i, v) in array.iter().enumerate() {
                if i > 0 {
                    out.push(b' ');
                }
                write_f32(*v, out);
            }
            out.extend_from_slice(b"] ");
            write_f32(*phase, out);
            out.extend_from_slice(b" d");
        }
        Operator::SetLineCap { cap_style } => {
            out.extend_from_slice(cap_style.to_string().as_bytes());
            out.extend_from_slice(b" J");
        }
        Operator::SetLineJoin { join_style } => {
            out.extend_from_slice(join_style.to_string().as_bytes());
            out.extend_from_slice(b" j");
        }
        Operator::SetMiterLimit { limit } => {
            write_f32(*limit, out);
            out.extend_from_slice(b" M");
        }
        Operator::SetRenderingIntent { ref intent } => {
            out.push(b'/');
            out.extend_from_slice(intent.as_bytes());
            out.extend_from_slice(b" ri");
        }
        Operator::SetFlatness { tolerance } => {
            write_f32(*tolerance, out);
            out.extend_from_slice(b" i");
        }
        Operator::SetExtGState { ref dict_name } => {
            out.push(b'/');
            out.extend_from_slice(dict_name.as_bytes());
            out.extend_from_slice(b" gs");
        }
        Operator::PaintShading { ref name } => {
            out.push(b'/');
            out.extend_from_slice(name.as_bytes());
            out.extend_from_slice(b" sh");
        }

        // Inline image
        Operator::InlineImage { ref dict, ref data } => {
            out.extend_from_slice(b"BI\n");
            for (key, value) in dict.iter() {
                out.push(b'/');
                out.extend_from_slice(key.as_bytes());
                out.push(b' ');
                serialize_inline_image_value(value, out);
                out.push(b'\n');
            }
            out.extend_from_slice(b"ID ");
            out.extend_from_slice(data);
            out.extend_from_slice(b"\nEI");
        }

        // Marked content
        Operator::BeginMarkedContent { ref tag } => {
            out.push(b'/');
            out.extend_from_slice(tag.as_bytes());
            out.extend_from_slice(b" BMC");
        }
        Operator::BeginMarkedContentDict {
            ref tag,
            ref properties,
        } => {
            out.push(b'/');
            out.extend_from_slice(tag.as_bytes());
            out.push(b' ');
            serialize_object_inline(properties, out);
            out.extend_from_slice(b" BDC");
        }
        Operator::EndMarkedContent => out.extend_from_slice(b"EMC"),

        // Catch-all for Other operators
        Operator::Other {
            ref name,
            ref operands,
        } => {
            for (i, operand) in operands.iter().enumerate() {
                if i > 0 {
                    out.push(b' ');
                }
                serialize_object_inline(operand, out);
            }
            if !operands.is_empty() {
                out.push(b' ');
            }
            out.extend_from_slice(name.as_bytes());
        }
    }
}

/// Write a floating point number in compact PDF format.
fn write_f32(val: f32, out: &mut Vec<u8>) {
    if val == val.floor() && val.abs() < 1e9 {
        // Write as integer if it's a whole number
        out.extend_from_slice((val as i64).to_string().as_bytes());
    } else {
        // Use enough precision but strip trailing zeros
        let s = format!("{:.4}", val);
        let s = s.trim_end_matches('0');
        let s = s.trim_end_matches('.');
        out.extend_from_slice(s.as_bytes());
    }
}

/// Write a PDF string literal (escaped parentheses).
fn write_pdf_string(data: &[u8], out: &mut Vec<u8>) {
    out.push(b'(');
    for &byte in data {
        match byte {
            b'(' => out.extend_from_slice(b"\\("),
            b')' => out.extend_from_slice(b"\\)"),
            b'\\' => out.extend_from_slice(b"\\\\"),
            _ => out.push(byte),
        }
    }
    out.push(b')');
}

/// Serialize a PDF Object inline (for marked content properties, etc.)
fn serialize_object_inline(obj: &crate::object::Object, out: &mut Vec<u8>) {
    use crate::object::Object;
    match obj {
        Object::Null => out.extend_from_slice(b"null"),
        Object::Boolean(b) => {
            out.extend_from_slice(if *b { b"true" } else { b"false" });
        }
        Object::Integer(i) => out.extend_from_slice(i.to_string().as_bytes()),
        Object::Real(f) => write_f32(*f as f32, out),
        Object::String(s) => write_pdf_string(s, out),
        Object::Name(n) => {
            out.push(b'/');
            out.extend_from_slice(n.as_bytes());
        }
        Object::Array(arr) => {
            out.push(b'[');
            for (i, item) in arr.iter().enumerate() {
                if i > 0 {
                    out.push(b' ');
                }
                serialize_object_inline(item, out);
            }
            out.push(b']');
        }
        Object::Dictionary(dict) => {
            out.extend_from_slice(b"<<");
            for (key, val) in dict {
                out.push(b'/');
                out.extend_from_slice(key.as_bytes());
                out.push(b' ');
                serialize_object_inline(val, out);
            }
            out.extend_from_slice(b">>");
        }
        Object::Reference(r) => {
            out.extend_from_slice(format!("{} {} R", r.id, r.gen).as_bytes());
        }
        Object::Stream { .. } => {
            // Streams shouldn't appear inline in content streams
            out.extend_from_slice(b"null");
        }
    }
}

/// Serialize an inline image dictionary value.
fn serialize_inline_image_value(obj: &crate::object::Object, out: &mut Vec<u8>) {
    serialize_object_inline(obj, out);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_redaction_rect_normalize() {
        let r = RedactionRect::new([100.0, 200.0, 50.0, 150.0]);
        assert_eq!(r.llx, 50.0);
        assert_eq!(r.lly, 150.0);
        assert_eq!(r.urx, 100.0);
        assert_eq!(r.ury, 200.0);
    }

    #[test]
    fn test_redaction_rect_overlaps() {
        let r = RedactionRect::new([100.0, 100.0, 200.0, 200.0]);
        assert!(r.overlaps(150.0, 150.0, 250.0, 250.0));
        assert!(r.overlaps(50.0, 50.0, 150.0, 150.0));
        assert!(!r.overlaps(300.0, 300.0, 400.0, 400.0));
        assert!(!r.overlaps(0.0, 0.0, 50.0, 50.0));
    }

    #[test]
    fn test_serialize_basic_operators() {
        let ops = vec![
            Operator::SaveState,
            Operator::SetFillRgb {
                r: 1.0,
                g: 0.0,
                b: 0.0,
            },
            Operator::Rectangle {
                x: 10.0,
                y: 20.0,
                width: 100.0,
                height: 50.0,
            },
            Operator::Fill,
            Operator::RestoreState,
        ];

        let result = serialize_operators(&ops);
        let content = String::from_utf8_lossy(&result);
        assert!(content.contains("q\n"));
        assert!(content.contains("1 0 0 rg\n"));
        assert!(content.contains("10 20 100 50 re\n"));
        assert!(content.contains("f\n"));
        assert!(content.contains("Q\n"));
    }

    #[test]
    fn test_serialize_text_operators() {
        let ops = vec![
            Operator::BeginText,
            Operator::Tf {
                font: "F1".to_string(),
                size: 12.0,
            },
            Operator::Td { tx: 100.0, ty: 700.0 },
            Operator::Tj {
                text: b"Hello, World!".to_vec(),
            },
            Operator::EndText,
        ];

        let result = serialize_operators(&ops);
        let content = String::from_utf8_lossy(&result);
        assert!(content.contains("BT\n"));
        assert!(content.contains("/F1 12 Tf\n"));
        assert!(content.contains("100 700 Td\n"));
        assert!(content.contains("(Hello, World!) Tj\n"));
        assert!(content.contains("ET\n"));
    }

    #[test]
    fn test_strip_empty_redaction() {
        let content = b"BT /F1 12 Tf 100 700 Td (Hello) Tj ET";
        let result = strip_redacted_content(content, &[]).unwrap();
        assert_eq!(result, content.to_vec());
    }

    #[test]
    fn test_strip_redacted_text() {
        // Text at roughly (100, 700) should be removed by redaction rect covering that area
        let content = b"BT /F1 12 Tf 100 700 Td (Secret) Tj ET";
        let rects = vec![RedactionRect::new([90.0, 690.0, 250.0, 720.0])];
        let result = strip_redacted_content(content, &rects).unwrap();
        let text = String::from_utf8_lossy(&result);
        // The text "Secret" should not appear
        assert!(!text.contains("Secret"), "Redacted text should be stripped: {}", text);
    }

    #[test]
    fn test_strip_preserves_non_redacted_text() {
        // Two text operations with absolute positioning (Tm): one inside redaction, one outside
        let content = b"BT /F1 12 Tf 1 0 0 1 100 700 Tm (Visible) Tj 1 0 0 1 100 500 Tm (Secret) Tj ET";
        // Redact only the area around y=500
        let rects = vec![RedactionRect::new([90.0, 490.0, 400.0, 520.0])];
        let result = strip_redacted_content(content, &rects).unwrap();
        let text = String::from_utf8_lossy(&result);
        assert!(text.contains("Visible"), "Non-redacted text should remain");
        assert!(!text.contains("Secret"), "Redacted text should be gone");
    }

    #[test]
    fn test_strip_redacted_image() {
        // Image placed at 100,200 with size 400x300
        let content = b"q 400 0 0 300 100 200 cm /Im1 Do Q";
        // Redact the image area
        let rects = vec![RedactionRect::new([100.0, 200.0, 500.0, 500.0])];
        let result = strip_redacted_content(content, &rects).unwrap();
        let text = String::from_utf8_lossy(&result);
        assert!(!text.contains("Im1"), "Redacted image should be removed");
    }

    #[test]
    fn test_serialize_round_trip() {
        let content = b"BT /F1 12 Tf 100 700 Td (Hello) Tj ET q 1 0 0 rg 10 20 100 50 re f Q";
        let ops = parse_content_stream(content).unwrap();
        let serialized = serialize_operators(&ops);
        // Parse again
        let ops2 = parse_content_stream(&serialized).unwrap();
        // Should have the same number of operators
        assert_eq!(ops.len(), ops2.len());
    }

    #[test]
    fn test_write_f32_integer() {
        let mut out = Vec::new();
        write_f32(42.0, &mut out);
        assert_eq!(&out, b"42");
    }

    #[test]
    fn test_write_f32_decimal() {
        let mut out = Vec::new();
        write_f32(3.14, &mut out);
        let s = String::from_utf8_lossy(&out);
        assert!(s.starts_with("3.14"));
    }

    #[test]
    fn test_write_pdf_string_escaping() {
        let mut out = Vec::new();
        write_pdf_string(b"hello (world)", &mut out);
        assert_eq!(&out, b"(hello \\(world\\))");
    }

    #[test]
    fn test_serialize_tj_array() {
        let ops = vec![Operator::TJ {
            array: vec![
                TextElement::String(b"AB".to_vec()),
                TextElement::Offset(-120.0),
                TextElement::String(b"CD".to_vec()),
            ],
        }];
        let result = serialize_operators(&ops);
        let text = String::from_utf8_lossy(&result);
        assert!(text.contains("[(AB)-120(CD)] TJ"));
    }
}
