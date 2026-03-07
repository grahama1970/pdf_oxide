//! Shape drawing convenience API for PDF content streams.
//!
//! The [`Shape`] struct wraps [`ContentStreamBuilder`] and provides high-level
//! drawing methods for common geometric primitives such as lines, rectangles,
//! circles, polygons, arrows, stars, and decorative paths like squiggles and
//! zigzags.
//!
//! # Example
//!
//! ```ignore
//! use pdf_oxide::writer::Shape;
//!
//! let mut shape = Shape::new();
//! shape.set_stroke_color(0.0, 0.0, 0.0);
//! shape.set_line_width(1.5);
//! shape.draw_rect(72.0, 700.0, 200.0, 100.0);
//! shape.stroke();
//! shape.draw_circle(300.0, 400.0, 50.0);
//! shape.set_fill_color(1.0, 0.0, 0.0);
//! shape.fill();
//! let bytes = shape.finish().unwrap();
//! ```

use crate::error::Result;
use crate::writer::content_stream::ContentStreamBuilder;

/// A convenience wrapper around [`ContentStreamBuilder`] for drawing shapes.
///
/// `Shape` provides high-level drawing methods that internally emit the
/// correct sequence of PDF path-construction operators. After building
/// paths, call [`stroke`](Self::stroke), [`fill`](Self::fill), or
/// [`fill_and_stroke`](Self::fill_and_stroke) to paint them, then call
/// [`finish`](Self::finish) to obtain the serialised content-stream bytes.
#[derive(Debug, Default)]
pub struct Shape {
    builder: ContentStreamBuilder,
}

impl Shape {
    /// Create a new, empty `Shape`.
    pub fn new() -> Self {
        Self {
            builder: ContentStreamBuilder::new(),
        }
    }

    /// Create a `Shape` that wraps an existing [`ContentStreamBuilder`].
    ///
    /// This is useful when you want to mix shape drawing with other
    /// content-stream operations that have already been recorded.
    pub fn from_builder(builder: ContentStreamBuilder) -> Self {
        Self { builder }
    }

    /// Return a mutable reference to the inner [`ContentStreamBuilder`].
    ///
    /// Useful for issuing low-level operations that `Shape` does not
    /// directly expose.
    pub fn builder_mut(&mut self) -> &mut ContentStreamBuilder {
        &mut self.builder
    }

    // ---------------------------------------------------------------
    // Style configuration
    // ---------------------------------------------------------------

    /// Set the fill colour using RGB values in the range `0.0..=1.0`.
    pub fn set_fill_color(&mut self, r: f32, g: f32, b: f32) -> &mut Self {
        self.builder.set_fill_color(r, g, b);
        self
    }

    /// Set the stroke colour using RGB values in the range `0.0..=1.0`.
    pub fn set_stroke_color(&mut self, r: f32, g: f32, b: f32) -> &mut Self {
        self.builder.set_stroke_color(r, g, b);
        self
    }

    /// Set the line width used for stroking paths.
    pub fn set_line_width(&mut self, width: f32) -> &mut Self {
        self.builder.set_line_width(width);
        self
    }

    // ---------------------------------------------------------------
    // Path-painting commands
    // ---------------------------------------------------------------

    /// Stroke the current path.
    pub fn stroke(&mut self) -> &mut Self {
        self.builder.stroke();
        self
    }

    /// Fill the current path using the non-zero winding rule.
    pub fn fill(&mut self) -> &mut Self {
        self.builder.fill();
        self
    }

    /// Fill and then stroke the current path.
    pub fn fill_and_stroke(&mut self) -> &mut Self {
        self.builder.fill_stroke();
        self
    }

    // ---------------------------------------------------------------
    // Convenience drawing methods
    // ---------------------------------------------------------------

    /// Draw a straight line from `(x1, y1)` to `(x2, y2)`.
    ///
    /// The line is added as an open sub-path; call [`stroke`](Self::stroke)
    /// afterwards to make it visible.
    pub fn draw_line(&mut self, x1: f32, y1: f32, x2: f32, y2: f32) -> &mut Self {
        self.builder.move_to(x1, y1);
        self.builder.line_to(x2, y2);
        self
    }

    /// Draw an axis-aligned rectangle.
    ///
    /// `(x, y)` is the lower-left corner in PDF coordinate space.
    pub fn draw_rect(&mut self, x: f32, y: f32, w: f32, h: f32) -> &mut Self {
        self.builder.rect(x, y, w, h);
        self
    }

    /// Draw a rectangle with rounded corners.
    ///
    /// `radius` is clamped so it does not exceed half the width or height.
    pub fn draw_rounded_rect(
        &mut self,
        x: f32,
        y: f32,
        w: f32,
        h: f32,
        radius: f32,
    ) -> &mut Self {
        self.builder.rounded_rect(x, y, w, h, radius);
        self
    }

    /// Draw a circle centred at `(cx, cy)` with the given `radius`.
    pub fn draw_circle(&mut self, cx: f32, cy: f32, r: f32) -> &mut Self {
        self.builder.circle(cx, cy, r);
        self
    }

    /// Draw an ellipse centred at `(cx, cy)` with radii `rx` and `ry`.
    pub fn draw_ellipse(&mut self, cx: f32, cy: f32, rx: f32, ry: f32) -> &mut Self {
        self.builder.ellipse(cx, cy, rx, ry);
        self
    }

    /// Draw a closed polygon through the given `points`.
    ///
    /// At least two points are required; if fewer are supplied the call is a
    /// no-op. The path is automatically closed.
    pub fn draw_polygon(&mut self, points: &[(f32, f32)]) -> &mut Self {
        if points.len() < 2 {
            return self;
        }
        self.builder.move_to(points[0].0, points[0].1);
        for &(x, y) in &points[1..] {
            self.builder.line_to(x, y);
        }
        self.builder.close_path();
        self
    }

    /// Draw an open polyline through the given `points`.
    ///
    /// Unlike [`draw_polygon`](Self::draw_polygon), the path is **not**
    /// closed. At least two points are required; fewer is a no-op.
    pub fn draw_polyline(&mut self, points: &[(f32, f32)]) -> &mut Self {
        if points.len() < 2 {
            return self;
        }
        self.builder.move_to(points[0].0, points[0].1);
        for &(x, y) in &points[1..] {
            self.builder.line_to(x, y);
        }
        self
    }

    /// Draw a circular arc from `start_angle` to `end_angle` (in radians).
    ///
    /// The arc is centred at `(cx, cy)` with the given `radius`. Angles are
    /// measured counter-clockwise from the positive x-axis. The arc is
    /// approximated with cubic Bezier segments, each spanning at most 90
    /// degrees.
    pub fn draw_arc(
        &mut self,
        cx: f32,
        cy: f32,
        r: f32,
        start_angle: f32,
        end_angle: f32,
    ) -> &mut Self {
        self.emit_arc(cx, cy, r, r, start_angle, end_angle, true);
        self
    }

    /// Draw a pie/sector shape (an arc with lines back to the centre).
    ///
    /// This produces a closed wedge shape suitable for pie charts.
    pub fn draw_sector(
        &mut self,
        cx: f32,
        cy: f32,
        r: f32,
        start_angle: f32,
        end_angle: f32,
    ) -> &mut Self {
        self.builder.move_to(cx, cy);
        let sx = cx + r * start_angle.cos();
        let sy = cy + r * start_angle.sin();
        self.builder.line_to(sx, sy);
        self.emit_arc(cx, cy, r, r, start_angle, end_angle, false);
        self.builder.close_path();
        self
    }

    /// Draw a wavy (squiggle) line from `(x1, y1)` to `(x2, y2)`.
    ///
    /// `amplitude` controls the height of each wave, and `wavelength`
    /// controls the distance between peaks.
    pub fn draw_squiggle(
        &mut self,
        x1: f32,
        y1: f32,
        x2: f32,
        y2: f32,
        amplitude: f32,
        wavelength: f32,
    ) -> &mut Self {
        if wavelength <= 0.0 {
            return self.draw_line(x1, y1, x2, y2);
        }

        let dx = x2 - x1;
        let dy = y2 - y1;
        let length = (dx * dx + dy * dy).sqrt();
        if length < f32::EPSILON {
            return self;
        }

        // Unit vectors along and perpendicular to the line.
        let ux = dx / length;
        let uy = dy / length;
        let nx = -uy; // normal x
        let ny = ux; // normal y

        let half = wavelength / 2.0;
        let num_halves = (length / half).floor() as usize;

        self.builder.move_to(x1, y1);

        for i in 0..num_halves {
            let sign = if i % 2 == 0 { 1.0 } else { -1.0 };
            let t_start = i as f32 * half;
            let t_end = ((i + 1) as f32 * half).min(length);
            let t_mid = (t_start + t_end) / 2.0;

            // Control point at the midpoint of this half-wave, offset by amplitude.
            let cpx = x1 + ux * t_mid + nx * amplitude * sign;
            let cpy = y1 + uy * t_mid + ny * amplitude * sign;

            let ex = x1 + ux * t_end;
            let ey = y1 + uy * t_end;

            // Quadratic-like curve via two identical control points.
            self.builder.curve_to(cpx, cpy, cpx, cpy, ex, ey);
        }

        // If there is a remainder beyond the last full half-wave, draw a line.
        let covered = num_halves as f32 * half;
        if covered < length - f32::EPSILON {
            self.builder.line_to(x2, y2);
        }

        self
    }

    /// Draw a zigzag line from `(x1, y1)` to `(x2, y2)`.
    ///
    /// `amplitude` controls the height of the zag, and `segments` is the
    /// number of zigzag segments (peak-to-peak count).
    pub fn draw_zigzag(
        &mut self,
        x1: f32,
        y1: f32,
        x2: f32,
        y2: f32,
        amplitude: f32,
        segments: usize,
    ) -> &mut Self {
        if segments == 0 {
            return self.draw_line(x1, y1, x2, y2);
        }

        let dx = x2 - x1;
        let dy = y2 - y1;
        let length = (dx * dx + dy * dy).sqrt();
        if length < f32::EPSILON {
            return self;
        }

        let ux = dx / length;
        let uy = dy / length;
        let nx = -uy;
        let ny = ux;

        let seg_len = length / segments as f32;

        self.builder.move_to(x1, y1);

        for i in 0..segments {
            let sign = if i % 2 == 0 { 1.0 } else { -1.0 };
            let t_mid = (i as f32 + 0.5) * seg_len;
            let t_end = (i + 1) as f32 * seg_len;

            // Peak of this segment.
            let px = x1 + ux * t_mid + nx * amplitude * sign;
            let py = y1 + uy * t_mid + ny * amplitude * sign;
            self.builder.line_to(px, py);

            // End of this segment (back on the baseline).
            let ex = x1 + ux * t_end;
            let ey = y1 + uy * t_end;
            self.builder.line_to(ex, ey);
        }

        self
    }

    /// Draw a line with an arrowhead at the endpoint `(x2, y2)`.
    ///
    /// `head_length` controls the size of the arrowhead.
    pub fn draw_arrow(
        &mut self,
        x1: f32,
        y1: f32,
        x2: f32,
        y2: f32,
        head_length: f32,
    ) -> &mut Self {
        let dx = x2 - x1;
        let dy = y2 - y1;
        let length = (dx * dx + dy * dy).sqrt();
        if length < f32::EPSILON {
            return self;
        }

        let ux = dx / length;
        let uy = dy / length;

        // Draw the shaft.
        self.builder.move_to(x1, y1);
        self.builder.line_to(x2, y2);

        // Arrowhead: two lines from the tip backwards at ~30 degrees.
        let angle = std::f32::consts::FRAC_PI_6; // 30 degrees
        let cos_a = angle.cos();
        let sin_a = angle.sin();

        // Rotate the negative-direction unit vector by +/- angle.
        let lx = -ux * cos_a - (-uy) * sin_a;
        let ly = -ux * sin_a + (-uy) * cos_a;
        let rx = -ux * cos_a + (-uy) * sin_a;
        let ry = (-ux) * (-sin_a) + (-uy) * cos_a;

        self.builder.move_to(x2, y2);
        self.builder
            .line_to(x2 + lx * head_length, y2 + ly * head_length);
        self.builder.move_to(x2, y2);
        self.builder
            .line_to(x2 + rx * head_length, y2 + ry * head_length);

        self
    }

    /// Draw a star centred at `(cx, cy)`.
    ///
    /// `outer_r` is the radius to the outer tips, `inner_r` is the radius
    /// to the inner vertices, and `points` is the number of tips (e.g., 5
    /// for a classic five-pointed star).
    ///
    /// At least 2 points are required; fewer is a no-op.
    pub fn draw_star(
        &mut self,
        cx: f32,
        cy: f32,
        outer_r: f32,
        inner_r: f32,
        points: usize,
    ) -> &mut Self {
        if points < 2 {
            return self;
        }

        let total_vertices = points * 2;
        let angle_step = std::f32::consts::TAU / total_vertices as f32;
        // Start from the top (negative y in screen coords, but PDF y-up so
        // we start at +PI/2).
        let start_angle = std::f32::consts::FRAC_PI_2;

        for i in 0..total_vertices {
            let angle = start_angle + i as f32 * angle_step;
            let r = if i % 2 == 0 { outer_r } else { inner_r };
            let px = cx + r * angle.cos();
            let py = cy + r * angle.sin();
            if i == 0 {
                self.builder.move_to(px, py);
            } else {
                self.builder.line_to(px, py);
            }
        }
        self.builder.close_path();
        self
    }

    // ---------------------------------------------------------------
    // Finalisation
    // ---------------------------------------------------------------

    /// Consume the `Shape` and return the serialised content-stream bytes.
    pub fn finish(self) -> Result<Vec<u8>> {
        self.builder.build()
    }

    /// Consume the `Shape` and return the inner [`ContentStreamBuilder`].
    pub fn into_builder(self) -> ContentStreamBuilder {
        self.builder
    }

    // ---------------------------------------------------------------
    // Internal helpers
    // ---------------------------------------------------------------

    /// Emit a circular/elliptical arc using cubic Bezier approximation.
    ///
    /// If `initial_move` is true, a `move_to` is emitted for the arc start
    /// point; otherwise the arc continues from the current point.
    fn emit_arc(
        &mut self,
        cx: f32,
        cy: f32,
        rx: f32,
        ry: f32,
        start: f32,
        end: f32,
        initial_move: bool,
    ) {
        // Normalise so we always sweep in the positive direction.
        let mut sweep = end - start;
        if sweep.abs() < f32::EPSILON {
            return;
        }
        if sweep < 0.0 {
            sweep += std::f32::consts::TAU;
        }

        let max_segment = std::f32::consts::FRAC_PI_2; // 90 degrees
        let num_segments = (sweep / max_segment).ceil() as usize;
        let seg_angle = sweep / num_segments as f32;

        let mut angle = start;

        if initial_move {
            let sx = cx + rx * angle.cos();
            let sy = cy + ry * angle.sin();
            self.builder.move_to(sx, sy);
        }

        for _ in 0..num_segments {
            let a1 = angle;
            let a2 = angle + seg_angle;
            self.emit_arc_segment(cx, cy, rx, ry, a1, a2);
            angle = a2;
        }
    }

    /// Emit a single Bezier segment approximating an arc of <= 90 degrees.
    fn emit_arc_segment(
        &mut self,
        cx: f32,
        cy: f32,
        rx: f32,
        ry: f32,
        a1: f32,
        a2: f32,
    ) {
        let half = (a2 - a1) / 2.0;
        let alpha = (4.0 / 3.0) * (1.0 - half.cos()) / half.sin();

        let cos1 = a1.cos();
        let sin1 = a1.sin();
        let cos2 = a2.cos();
        let sin2 = a2.sin();

        let p1x = cx + rx * cos1;
        let p1y = cy + ry * sin1;
        let p2x = cx + rx * cos2;
        let p2y = cy + ry * sin2;

        let cp1x = p1x - rx * sin1 * alpha;
        let cp1y = p1y + ry * cos1 * alpha;
        let cp2x = p2x + rx * sin2 * alpha;
        let cp2y = p2y - ry * cos2 * alpha;

        self.builder.curve_to(cp1x, cp1y, cp2x, cp2y, p2x, p2y);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_draw_line() {
        let mut s = Shape::new();
        s.draw_line(0.0, 0.0, 100.0, 200.0);
        s.stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        assert!(text.contains("0 0 m"));
        assert!(text.contains("100 200 l"));
        assert!(text.contains("S"));
    }

    #[test]
    fn test_draw_rect() {
        let mut s = Shape::new();
        s.draw_rect(10.0, 20.0, 100.0, 50.0);
        s.stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        assert!(text.contains("10 20 100 50 re"));
    }

    #[test]
    fn test_draw_rounded_rect() {
        let mut s = Shape::new();
        s.draw_rounded_rect(10.0, 20.0, 200.0, 100.0, 15.0);
        s.fill_and_stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        // Should contain moves, lines, and curves for the rounded corners.
        assert!(text.contains("m"));
        assert!(text.contains("c"));
        assert!(text.contains("B"));
    }

    #[test]
    fn test_draw_circle() {
        let mut s = Shape::new();
        s.draw_circle(100.0, 100.0, 50.0);
        s.fill();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        // Circle starts with move_to at (cx + r, cy).
        assert!(text.contains("150 100 m"));
        assert!(text.contains("h")); // close_path
        assert!(text.contains("f")); // fill
    }

    #[test]
    fn test_draw_ellipse() {
        let mut s = Shape::new();
        s.draw_ellipse(200.0, 300.0, 80.0, 40.0);
        s.stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        assert!(text.contains("280 300 m")); // move_to(cx + rx, cy)
        assert!(text.contains("h"));
    }

    #[test]
    fn test_draw_polygon() {
        let mut s = Shape::new();
        let pts = vec![(0.0, 0.0), (100.0, 0.0), (50.0, 80.0)];
        s.draw_polygon(&pts);
        s.fill();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        assert!(text.contains("0 0 m"));
        assert!(text.contains("100 0 l"));
        assert!(text.contains("50 80 l"));
        assert!(text.contains("h")); // closed
    }

    #[test]
    fn test_draw_polygon_too_few_points() {
        let mut s = Shape::new();
        s.draw_polygon(&[(1.0, 2.0)]);
        let bytes = s.finish().unwrap();
        // Should be empty (no-op).
        let text = String::from_utf8(bytes).unwrap();
        assert!(text.trim().is_empty());
    }

    #[test]
    fn test_draw_polyline() {
        let mut s = Shape::new();
        let pts = vec![(0.0, 0.0), (50.0, 50.0), (100.0, 0.0)];
        s.draw_polyline(&pts);
        s.stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        assert!(text.contains("0 0 m"));
        assert!(text.contains("50 50 l"));
        assert!(text.contains("100 0 l"));
        // No close_path for polyline.
        assert!(!text.contains("h\n"));
    }

    #[test]
    fn test_draw_arc() {
        let mut s = Shape::new();
        s.draw_arc(100.0, 100.0, 50.0, 0.0, std::f32::consts::FRAC_PI_2);
        s.stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        // Should have a move_to and at least one curve_to.
        assert!(text.contains("m"));
        assert!(text.contains("c"));
    }

    #[test]
    fn test_draw_sector() {
        let mut s = Shape::new();
        s.draw_sector(100.0, 100.0, 50.0, 0.0, std::f32::consts::FRAC_PI_2);
        s.fill();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        // Sector starts at centre, goes to arc start, draws arc, closes.
        assert!(text.contains("100 100 m")); // move to centre
        assert!(text.contains("h")); // closed
        assert!(text.contains("f")); // filled
    }

    #[test]
    fn test_draw_squiggle() {
        let mut s = Shape::new();
        s.draw_squiggle(0.0, 0.0, 200.0, 0.0, 5.0, 20.0);
        s.stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        assert!(text.contains("0 0 m"));
        assert!(text.contains("c")); // Bezier curves
    }

    #[test]
    fn test_draw_squiggle_zero_wavelength() {
        let mut s = Shape::new();
        s.draw_squiggle(0.0, 0.0, 100.0, 0.0, 5.0, 0.0);
        s.stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        // Falls back to a straight line.
        assert!(text.contains("0 0 m"));
        assert!(text.contains("100 0 l"));
    }

    #[test]
    fn test_draw_zigzag() {
        let mut s = Shape::new();
        s.draw_zigzag(0.0, 0.0, 200.0, 0.0, 10.0, 4);
        s.stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        assert!(text.contains("0 0 m"));
        // Should contain multiple line_to ops.
        let line_count = text.matches(" l\n").count();
        assert!(
            line_count >= 8,
            "expected at least 8 line_to ops, got {}",
            line_count
        );
    }

    #[test]
    fn test_draw_zigzag_zero_segments() {
        let mut s = Shape::new();
        s.draw_zigzag(0.0, 0.0, 100.0, 0.0, 5.0, 0);
        s.stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        assert!(text.contains("0 0 m"));
        assert!(text.contains("100 0 l"));
    }

    #[test]
    fn test_draw_arrow() {
        let mut s = Shape::new();
        s.draw_arrow(0.0, 0.0, 100.0, 0.0, 10.0);
        s.stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        // Shaft line.
        assert!(text.contains("0 0 m"));
        assert!(text.contains("100 0 l"));
        // Two arrowhead lines from the tip.
        let move_count = text.matches(" m\n").count();
        assert!(
            move_count >= 3,
            "expected 3 move_to ops (shaft + 2 head lines), got {}",
            move_count
        );
    }

    #[test]
    fn test_draw_star() {
        let mut s = Shape::new();
        s.draw_star(100.0, 100.0, 50.0, 25.0, 5);
        s.fill();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        // 5-pointed star has 10 vertices: 1 move_to + 9 line_to + close.
        assert!(text.contains("m"));
        assert!(text.contains("h"));
        let line_count = text.matches(" l\n").count();
        assert_eq!(line_count, 9, "5-pointed star needs 9 line_to ops");
    }

    #[test]
    fn test_draw_star_too_few_points() {
        let mut s = Shape::new();
        s.draw_star(50.0, 50.0, 30.0, 15.0, 1);
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        assert!(text.trim().is_empty());
    }

    #[test]
    fn test_style_methods() {
        let mut s = Shape::new();
        s.set_fill_color(1.0, 0.0, 0.0);
        s.set_stroke_color(0.0, 0.0, 1.0);
        s.set_line_width(2.5);
        s.draw_rect(10.0, 10.0, 50.0, 50.0);
        s.fill_and_stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        assert!(text.contains("1 0 0 rg")); // fill colour
        assert!(text.contains("0 0 1 RG")); // stroke colour
        assert!(text.contains("2.5 w")); // line width
        assert!(text.contains("B")); // fill_stroke
    }

    #[test]
    fn test_chaining() {
        let mut s = Shape::new();
        s.set_line_width(1.0)
            .set_stroke_color(0.0, 0.0, 0.0)
            .draw_line(0.0, 0.0, 100.0, 100.0)
            .stroke()
            .draw_circle(200.0, 200.0, 30.0)
            .stroke();
        let bytes = s.finish().unwrap();
        assert!(!bytes.is_empty());
    }

    #[test]
    fn test_from_builder() {
        let mut csb = ContentStreamBuilder::new();
        csb.set_line_width(3.0);
        let mut s = Shape::from_builder(csb);
        s.draw_rect(0.0, 0.0, 100.0, 100.0);
        s.stroke();
        let bytes = s.finish().unwrap();
        let text = String::from_utf8(bytes).unwrap();
        assert!(text.contains("3 w")); // from the original builder
        assert!(text.contains("0 0 100 100 re"));
    }

    #[test]
    fn test_into_builder() {
        let mut s = Shape::new();
        s.draw_circle(50.0, 50.0, 25.0);
        let builder = s.into_builder();
        // Should be able to build from the returned builder.
        let bytes = builder.build().unwrap();
        assert!(!bytes.is_empty());
    }
}
