//! Python bindings via PyO3.
//!
//! This module provides Python bindings for the PDF library, exposing the core functionality
//! through a Python-friendly API with proper error handling and type hints.
//!
//! # Architecture
//!
//! - `PyPdfDocument`: Python wrapper around Rust `PdfDocument`
//! - Error mapping: Rust errors → Python exceptions
//! - Default arguments using `#[pyo3(signature = ...)]`
//!
//! # Example
//!
//! ```python
//! from pdf_oxide import PdfDocument
//!
//! doc = PdfDocument("document.pdf")
//! text = doc.extract_text(0)
//! markdown = doc.to_markdown(0, detect_headings=True)
//! ```

use pyo3::exceptions::{PyIOError, PyRuntimeError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

use crate::converters::ConversionOptions as RustConversionOptions;
use crate::document::PdfDocument as RustPdfDocument;

/// Python wrapper for PdfDocument.
///
/// Provides PDF parsing, text extraction, and format conversion capabilities.
///
/// # Methods
///
/// - `__init__(path)`: Open a PDF file
/// - `version()`: Get PDF version tuple
/// - `page_count()`: Get number of pages
/// - `extract_text(page)`: Extract text from a page
/// - `to_markdown(page, ...)`: Convert page to Markdown
/// - `to_html(page, ...)`: Convert page to HTML
/// - `to_markdown_all(...)`: Convert all pages to Markdown
/// - `to_html_all(...)`: Convert all pages to HTML
use crate::editor::DocumentEditor as RustDocumentEditor;

#[pyclass(name = "PdfDocument", unsendable)]
pub struct PyPdfDocument {
    /// Inner Rust document
    inner: RustPdfDocument,
    /// Path for DOM access (lazy initialization)
    path: String,
    /// Cached editor for DOM access (lazy initialization)
    editor: Option<RustDocumentEditor>,
}

impl PyPdfDocument {
    /// Ensure the editor is initialized, creating it from the path if needed.
    fn ensure_editor(&mut self) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        Ok(())
    }

    /// Build a PyMuPDF-compatible block dict from grouped lines and spans.
    fn build_block_dict<'py>(
        &self,
        py: Python<'py>,
        block_lines: &[(&crate::layout::TextLine, Vec<&crate::layout::TextSpan>)],
    ) -> PyResult<Bound<'py, PyDict>> {
        let block_dict = PyDict::new(py);
        block_dict.set_item("type", 0)?; // text block

        // Compute block bbox from all lines
        let mut bx0 = f32::MAX;
        let mut by0 = f32::MAX;
        let mut bx1 = f32::MIN;
        let mut by1 = f32::MIN;

        let mut lines_list: Vec<Bound<'py, PyDict>> = Vec::new();

        for (line, line_spans) in block_lines {
            bx0 = bx0.min(line.bbox.x);
            by0 = by0.min(line.bbox.y);
            bx1 = bx1.max(line.bbox.x + line.bbox.width);
            by1 = by1.max(line.bbox.y + line.bbox.height);

            let line_dict = PyDict::new(py);
            let mut spans_list: Vec<Bound<'py, PyDict>> = Vec::new();

            for span in line_spans {
                let span_dict = PyDict::new(py);
                span_dict.set_item("text", &span.text)?;
                span_dict.set_item("bbox", (
                    span.bbox.x,
                    span.bbox.y,
                    span.bbox.x + span.bbox.width,
                    span.bbox.y + span.bbox.height,
                ))?;
                span_dict.set_item("font", &span.font_name)?;
                span_dict.set_item("size", span.font_size)?;
                // Flags: bit 0 = superscript, bit 1 = italic, bit 2 = serif,
                // bit 3 = monospace, bit 4 = bold (PyMuPDF convention)
                let mut flags = 0u32;
                if span.font_weight >= crate::layout::FontWeight::Bold { flags |= 16; }
                if span.is_italic { flags |= 2; }
                span_dict.set_item("flags", flags)?;
                span_dict.set_item("color", 0)?;
                spans_list.push(span_dict);
            }

            // If no spans found for this line, create a synthetic one from line text
            if spans_list.is_empty() {
                let span_dict = PyDict::new(py);
                span_dict.set_item("text", &line.text)?;
                span_dict.set_item("bbox", (
                    line.bbox.x, line.bbox.y,
                    line.bbox.x + line.bbox.width,
                    line.bbox.y + line.bbox.height,
                ))?;
                span_dict.set_item("font", "")?;
                span_dict.set_item("size", 0.0f32)?;
                span_dict.set_item("flags", 0)?;
                span_dict.set_item("color", 0)?;
                spans_list.push(span_dict);
            }

            let spans_py = pyo3::types::PyList::new(py, &spans_list)
                .map_err(|e| PyRuntimeError::new_err(format!("list: {}", e)))?;
            line_dict.set_item("spans", spans_py)?;
            lines_list.push(line_dict);
        }

        block_dict.set_item("bbox", (bx0, by0, bx1, by1))?;
        let lines_py = pyo3::types::PyList::new(py, &lines_list)
            .map_err(|e| PyRuntimeError::new_err(format!("list: {}", e)))?;
        block_dict.set_item("lines", lines_py)?;

        Ok(block_dict)
    }

    /// Build a rawdict block with per-character data.
    fn build_rawdict_block<'py>(
        &self,
        py: Python<'py>,
        block_lines: &[(&crate::layout::TextLine, Vec<&crate::layout::TextSpan>)],
        all_chars: &[crate::layout::TextChar],
    ) -> PyResult<Bound<'py, PyDict>> {
        let block_dict = PyDict::new(py);
        block_dict.set_item("type", 0)?;

        let mut bx0 = f32::MAX;
        let mut by0 = f32::MAX;
        let mut bx1 = f32::MIN;
        let mut by1 = f32::MIN;
        let mut lines_list: Vec<Bound<'py, PyDict>> = Vec::new();

        for (line, line_spans) in block_lines {
            bx0 = bx0.min(line.bbox.x);
            by0 = by0.min(line.bbox.y);
            bx1 = bx1.max(line.bbox.x + line.bbox.width);
            by1 = by1.max(line.bbox.y + line.bbox.height);

            let line_dict = PyDict::new(py);
            let mut spans_list: Vec<Bound<'py, PyDict>> = Vec::new();

            for span in line_spans {
                let span_dict = PyDict::new(py);
                span_dict.set_item("text", &span.text)?;
                span_dict.set_item("bbox", (
                    span.bbox.x, span.bbox.y,
                    span.bbox.x + span.bbox.width,
                    span.bbox.y + span.bbox.height,
                ))?;
                span_dict.set_item("font", &span.font_name)?;
                span_dict.set_item("size", span.font_size)?;
                let mut flags = 0u32;
                if span.font_weight >= crate::layout::FontWeight::Bold { flags |= 16; }
                if span.is_italic { flags |= 2; }
                span_dict.set_item("flags", flags)?;
                span_dict.set_item("color", 0)?;

                // Find chars within this span's bbox
                let span_chars: Vec<&crate::layout::TextChar> = all_chars.iter()
                    .filter(|ch| {
                        let ch_cx = ch.bbox.x + ch.bbox.width / 2.0;
                        let ch_cy = ch.bbox.y + ch.bbox.height / 2.0;
                        ch_cx >= span.bbox.x && ch_cx <= span.bbox.x + span.bbox.width
                            && ch_cy >= span.bbox.y && ch_cy <= span.bbox.y + span.bbox.height
                    })
                    .collect();

                let mut chars_list: Vec<Bound<'py, PyDict>> = Vec::new();
                for ch in &span_chars {
                    let char_dict = PyDict::new(py);
                    char_dict.set_item("c", ch.char.to_string())?;
                    char_dict.set_item("bbox", (
                        ch.bbox.x, ch.bbox.y,
                        ch.bbox.x + ch.bbox.width,
                        ch.bbox.y + ch.bbox.height,
                    ))?;
                    char_dict.set_item("origin", (ch.origin_x, ch.origin_y))?;
                    char_dict.set_item("rotation", ch.rotation_degrees)?;
                    chars_list.push(char_dict);
                }

                let chars_py = pyo3::types::PyList::new(py, &chars_list)
                    .map_err(|e| PyRuntimeError::new_err(format!("list: {}", e)))?;
                span_dict.set_item("chars", chars_py)?;
                spans_list.push(span_dict);
            }

            if spans_list.is_empty() {
                let span_dict = PyDict::new(py);
                span_dict.set_item("text", &line.text)?;
                span_dict.set_item("bbox", (
                    line.bbox.x, line.bbox.y,
                    line.bbox.x + line.bbox.width,
                    line.bbox.y + line.bbox.height,
                ))?;
                span_dict.set_item("font", "")?;
                span_dict.set_item("size", 0.0f32)?;
                span_dict.set_item("flags", 0)?;
                span_dict.set_item("color", 0)?;
                let empty_chars = pyo3::types::PyList::empty(py);
                span_dict.set_item("chars", empty_chars)?;
                spans_list.push(span_dict);
            }

            let spans_py = pyo3::types::PyList::new(py, &spans_list)
                .map_err(|e| PyRuntimeError::new_err(format!("list: {}", e)))?;
            line_dict.set_item("spans", spans_py)?;
            lines_list.push(line_dict);
        }

        block_dict.set_item("bbox", (bx0, by0, bx1, by1))?;
        let lines_py = pyo3::types::PyList::new(py, &lines_list)
            .map_err(|e| PyRuntimeError::new_err(format!("list: {}", e)))?;
        block_dict.set_item("lines", lines_py)?;
        Ok(block_dict)
    }
}

#[pymethods]
impl PyPdfDocument {
    /// Open a PDF file.
    ///
    /// Args:
    ///     path (str): Path to the PDF file
    ///
    /// Returns:
    ///     PdfDocument: Opened PDF document
    ///
    /// Raises:
    ///     IOError: If the file cannot be opened or is not a valid PDF
    ///
    /// Example:
    ///     >>> doc = PdfDocument("sample.pdf")
    ///     >>> print(doc.version())
    ///     (1, 7)
    #[new]
    fn new(path: String) -> PyResult<Self> {
        let doc = RustPdfDocument::open(&path)
            .map_err(|e| PyIOError::new_err(format!("Failed to open PDF: {}", e)))?;

        Ok(PyPdfDocument {
            inner: doc,
            path,
            editor: None,
        })
    }

    /// Get PDF version.
    ///
    /// Returns:
    ///     tuple[int, int]: PDF version as (major, minor), e.g. (1, 7) for PDF 1.7
    ///
    /// Example:
    ///     >>> doc = PdfDocument("sample.pdf")
    ///     >>> version = doc.version()
    ///     >>> print(f"PDF {version[0]}.{version[1]}")
    ///     PDF 1.7
    fn version(&self) -> (u8, u8) {
        self.inner.version()
    }

    /// Authenticate with a password to decrypt an encrypted PDF.
    ///
    /// If the PDF is encrypted, opening it automatically tries an empty password.
    /// Call this method to authenticate with a non-empty password.
    ///
    /// Args:
    ///     password (str): The password to authenticate with
    ///
    /// Returns:
    ///     bool: True if authentication succeeded, False if the password was wrong
    ///
    /// Raises:
    ///     RuntimeError: If encryption initialization fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("encrypted.pdf")
    ///     >>> doc.authenticate("secret123")
    ///     True
    fn authenticate(&mut self, password: &str) -> PyResult<bool> {
        self.inner
            .authenticate(password.as_bytes())
            .map_err(|e| PyRuntimeError::new_err(format!("Authentication failed: {}", e)))
    }

    // === Phase 1.2 — from_bytes / to_bytes ===

    /// Open a PDF from bytes in memory.
    ///
    /// Args:
    ///     data (bytes): Raw PDF file data
    ///
    /// Returns:
    ///     PdfDocument: Opened PDF document
    ///
    /// Example:
    ///     >>> with open("doc.pdf", "rb") as f:
    ///     ...     doc = PdfDocument.from_bytes(f.read())
    #[staticmethod]
    fn from_bytes(data: &[u8]) -> PyResult<Self> {
        let doc = RustPdfDocument::open_from_bytes(data.to_vec())
            .map_err(|e| PyIOError::new_err(format!("Failed to open PDF from bytes: {}", e)))?;
        Ok(PyPdfDocument {
            inner: doc,
            path: String::new(),  // no file path for in-memory docs
            editor: None,
        })
    }

    /// Serialize the document to bytes (after modifications).
    ///
    /// Returns:
    ///     bytes: PDF file data
    ///
    /// Example:
    ///     >>> doc = PdfDocument("input.pdf")
    ///     >>> page = doc.page(0)
    ///     >>> page.set_text(text_id, "new")
    ///     >>> doc.save_page(page)
    ///     >>> pdf_bytes = doc.to_bytes()
    fn to_bytes(&mut self) -> PyResult<Vec<u8>> {
        if let Some(ref mut editor) = self.editor {
            editor
                .save_to_bytes()
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to serialize PDF: {}", e)))
        } else {
            // No editor — read the original file
            if !self.path.is_empty() {
                std::fs::read(&self.path)
                    .map_err(|e| PyIOError::new_err(format!("Failed to read file: {}", e)))
            } else {
                Err(PyRuntimeError::new_err(
                    "Cannot serialize: no file path and no editor initialized.",
                ))
            }
        }
    }

    // === Phase 1.3 — is_encrypted / needs_pass ===

    /// Check if the PDF is encrypted.
    ///
    /// Returns:
    ///     bool: True if the document has an encryption dictionary
    ///
    /// Example:
    ///     >>> doc = PdfDocument("file.pdf")
    ///     >>> if doc.is_encrypted:
    ///     ...     doc.authenticate("password")
    #[getter]
    fn is_encrypted(&self) -> bool {
        self.inner.has_encryption()
    }

    // === Phase 1.8 — Metadata reading (Info dict) ===

    /// Get the document Info dictionary (title, author, etc.).
    ///
    /// Returns:
    ///     dict: Metadata fields from the PDF Info dictionary.
    ///           Keys: title, author, subject, keywords, creator, producer
    ///
    /// Example:
    ///     >>> info = doc.get_info()
    ///     >>> print(info.get("title", "Untitled"))
    fn get_info<'py>(&mut self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        use crate::extractors::xmp::XmpExtractor;

        let dict = PyDict::new(py);

        // Use XMP metadata (the standard for modern PDFs)
        if let Ok(Some(xmp)) = XmpExtractor::extract(&mut self.inner) {
            if let Some(title) = xmp.dc_title.as_ref() {
                dict.set_item("title", title)?;
            }
            if !xmp.dc_creator.is_empty() {
                dict.set_item("author", xmp.dc_creator.join(", "))?;
            }
            if let Some(desc) = xmp.dc_description.as_ref() {
                dict.set_item("subject", desc)?;
            }
            if !xmp.dc_subject.is_empty() {
                dict.set_item("keywords", xmp.dc_subject.join(", "))?;
            }
            if let Some(tool) = xmp.xmp_creator_tool.as_ref() {
                dict.set_item("creator", tool)?;
            }
            if let Some(producer) = xmp.pdf_producer.as_ref() {
                dict.set_item("producer", producer)?;
            }
        }

        Ok(dict)
    }

    /// Get number of pages in the document.
    ///
    /// Returns:
    ///     int: Number of pages
    ///
    /// Raises:
    ///     RuntimeError: If page count cannot be determined
    ///
    /// Example:
    ///     >>> doc = PdfDocument("sample.pdf")
    ///     >>> print(f"Pages: {doc.page_count()}")
    ///     Pages: 42
    fn page_count(&mut self) -> PyResult<usize> {
        self.inner
            .page_count()
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to get page count: {}", e)))
    }

    /// Extract text from a page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     str: Extracted text from the page
    ///
    /// Raises:
    ///     RuntimeError: If text extraction fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("sample.pdf")
    ///     >>> text = doc.extract_text(0)
    ///     >>> print(text[:100])
    #[pyo3(signature = (page, region=None))]
    fn extract_text(
        &mut self,
        page: usize,
        region: Option<(f32, f32, f32, f32)>,
    ) -> PyResult<String> {
        if let Some((x, y, w, h)) = region {
            self.inner
                .extract_text_in_rect(
                    page,
                    crate::geometry::Rect::new(x, y, w, h),
                    crate::layout::RectFilterMode::Intersects,
                )
                .map_err(|e| {
                    PyRuntimeError::new_err(format!("Failed to extract text in region: {}", e))
                })
        } else {
            self.inner
                .extract_text(page)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract text: {}", e)))
        }
    }

    /// Focus extraction on a specific rectangular region of a page (v0.3.14).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     bbox (tuple): (x, y, width, height) in points
    ///
    /// Returns:
    ///     PdfPageRegion: A region object for scoped extraction
    fn within(slf: Py<Self>, page: usize, bbox: (f32, f32, f32, f32)) -> PyResult<PyPdfPageRegion> {
        Ok(PyPdfPageRegion {
            doc: slf,
            page_index: page,
            region: crate::geometry::Rect::new(bbox.0, bbox.1, bbox.2, bbox.3),
        })
    }

    /// Render a page to an image.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     dpi (int): Dots per inch (default: 72)
    ///     format (str): Output format ("png" or "jpeg", default: "png")
    ///
    /// Returns:
    ///     bytes: Image data
    ///
    /// Example:
    ///     >>> doc = PdfDocument("sample.pdf")
    ///     >>> image_bytes = doc.render_page(0, dpi=300)
    ///     >>> with open("page0.png", "wb") as f:
    ///     ...     f.write(image_bytes)
    #[pyo3(signature = (page, dpi=None, format=None))]
    fn render_page(
        &mut self,
        page: usize,
        dpi: Option<u32>,
        format: Option<&str>,
    ) -> PyResult<Vec<u8>> {
        #[cfg(feature = "rendering")]
        {
            let mut options = crate::rendering::RenderOptions::with_dpi(dpi.unwrap_or(72));
            if let Some(fmt) = format {
                match fmt.to_lowercase().as_str() {
                    "jpeg" | "jpg" => {
                        options = options.as_jpeg(85);
                    },
                    _ => {}, // Default is PNG
                }
            }

            crate::rendering::render_page(&mut self.inner, page, &options)
                .map(|img| img.data)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to render page: {}", e)))
        }
        #[cfg(not(feature = "rendering"))]
        {
            let _ = page;
            let _ = dpi;
            let _ = format;
            Err(PyRuntimeError::new_err(
                "Rendering feature not enabled. Please build with 'rendering' feature.",
            ))
        }
    }

    // === Phase 1.4 — Clipped rendering ===

    /// Render a clipped region of a page to an image.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     clip (tuple): Region to render as (x0, y0, x1, y1) in PDF points
    ///     dpi (int): Dots per inch (default: 72)
    ///     format (str): Output format ("png" or "jpeg", default: "png")
    ///
    /// Returns:
    ///     bytes: Image data for the clipped region
    ///
    /// Example:
    ///     >>> clip = (100, 200, 400, 500)
    ///     >>> img = doc.render_page_clipped(0, clip, dpi=150)
    #[pyo3(signature = (page, clip, dpi=None, format=None))]
    fn render_page_clipped(
        &mut self,
        page: usize,
        clip: (f32, f32, f32, f32),
        dpi: Option<u32>,
        format: Option<&str>,
    ) -> PyResult<Vec<u8>> {
        #[cfg(feature = "rendering")]
        {
            let actual_dpi = dpi.unwrap_or(72);
            let mut options = crate::rendering::RenderOptions::with_dpi(actual_dpi);
            let is_jpeg = if let Some(fmt) = format {
                match fmt.to_lowercase().as_str() {
                    "jpeg" | "jpg" => {
                        options = options.as_jpeg(85);
                        true
                    },
                    _ => false,
                }
            } else {
                false
            };

            // Render the full page first
            let full_image = crate::rendering::render_page(&mut self.inner, page, &options)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to render page: {}", e)))?;

            // Get the page dimensions to compute scale
            let (mb_x0, mb_y0, _mb_x1, _mb_y1) = self.inner
                .get_page_media_box(page)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to get page rect: {}", e)))?;

            let scale = actual_dpi as f32 / 72.0;
            let (cx0, cy0, cx1, cy1) = clip;

            // Convert PDF coordinates to pixel coordinates
            let px0 = ((cx0 - mb_x0) * scale) as u32;
            let py0 = ((cy0 - mb_y0) * scale) as u32;
            let px1 = ((cx1 - mb_x0) * scale) as u32;
            let py1 = ((cy1 - mb_y0) * scale) as u32;

            // Decode the full image, crop, and re-encode
            let img = image::load_from_memory(&full_image.data)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to decode rendered image: {}", e)))?;

            let cropped = img.crop_imm(
                px0.min(img.width().saturating_sub(1)),
                py0.min(img.height().saturating_sub(1)),
                (px1 - px0).min(img.width() - px0.min(img.width().saturating_sub(1))),
                (py1 - py0).min(img.height() - py0.min(img.height().saturating_sub(1))),
            );

            let mut buf = Vec::new();
            let mut cursor = std::io::Cursor::new(&mut buf);
            if is_jpeg {
                cropped.write_to(&mut cursor, image::ImageOutputFormat::Jpeg(85))
                    .map_err(|e| PyRuntimeError::new_err(format!("Failed to encode cropped image: {}", e)))?;
            } else {
                cropped.write_to(&mut cursor, image::ImageOutputFormat::Png)
                    .map_err(|e| PyRuntimeError::new_err(format!("Failed to encode cropped image: {}", e)))?;
            }

            Ok(buf)
        }
        #[cfg(not(feature = "rendering"))]
        {
            let _ = (page, clip, dpi, format);
            Err(PyRuntimeError::new_err(
                "Rendering feature not enabled. Please build with 'rendering' feature.",
            ))
        }
    }

    /// Render a page to SVG markup (no rendering dependencies required).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     embed_images (bool): Embed images as base64 data URIs (default True)
    ///     text_as_text (bool): Emit text as <text> elements (default True)
    ///
    /// Returns:
    ///     str: SVG markup string
    ///
    /// Example:
    ///     >>> svg = doc.render_page_to_svg(0)
    ///     >>> with open("page.svg", "w") as f:
    ///     ...     f.write(svg)
    #[pyo3(signature = (page, embed_images=true, text_as_text=true))]
    fn render_page_to_svg(
        &mut self,
        page: usize,
        embed_images: bool,
        text_as_text: bool,
    ) -> PyResult<String> {
        let options = crate::svg_export::SvgOptions {
            embed_images,
            text_as_text,
            class_prefix: String::new(),
        };
        let mut renderer = crate::svg_export::SvgRenderer::with_options(options);
        renderer.render_page(&mut self.inner, page)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to render SVG: {}", e)))
    }

    // === Phase 1.1 — extract_text_dict / extract_text_blocks ===

    /// Extract text as a structured dict (PyMuPDF get_text("dict") compatible).
    ///
    /// Returns a nested structure of blocks → lines → spans with bounding boxes.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     dict: {"blocks": [{"type": 0, "bbox": (x0,y0,x1,y1),
    ///            "lines": [{"spans": [{"text": str, "bbox": ..., "font": str,
    ///            "size": float, "flags": int}]}]}]}
    ///
    /// Example:
    ///     >>> data = doc.extract_text_dict(0)
    ///     >>> for block in data["blocks"]:
    ///     ...     for line in block["lines"]:
    ///     ...         for span in line["spans"]:
    ///     ...             print(span["text"])
    fn extract_text_dict<'py>(&mut self, py: Python<'py>, page: usize) -> PyResult<Bound<'py, PyDict>> {
        // Use extract_text_lines + extract_spans to build the dict structure
        let lines = self.inner.extract_text_lines(page)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract lines: {}", e)))?;
        let spans = self.inner.extract_spans(page)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract spans: {}", e)))?;

        // Group spans into lines, then lines into blocks by vertical gap
        let mut blocks_list: Vec<Bound<'py, PyDict>> = Vec::new();

        // Simple block detection: group consecutive lines with < 1.5x line height gap
        let mut current_block_lines: Vec<(&crate::layout::TextLine, Vec<&crate::layout::TextSpan>)> = Vec::new();
        let mut current_block_bottom = 0.0f32;

        for line in &lines {
            let line_height = line.bbox.height.max(1.0);
            let gap = line.bbox.y - current_block_bottom;

            // Start new block if gap > 1.5x line height
            if !current_block_lines.is_empty() && gap > line_height * 1.5 {
                // Flush current block
                let block_dict = self.build_block_dict(py, &current_block_lines)?;
                blocks_list.push(block_dict);
                current_block_lines.clear();
            }

            // Find spans that belong to this line (by Y overlap)
            let line_spans: Vec<&crate::layout::TextSpan> = spans.iter()
                .filter(|s| {
                    let s_center_y = s.bbox.y + s.bbox.height / 2.0;
                    s_center_y >= line.bbox.y && s_center_y <= line.bbox.y + line.bbox.height
                })
                .collect();

            current_block_lines.push((line, line_spans));
            current_block_bottom = line.bbox.y + line.bbox.height;
        }

        // Flush last block
        if !current_block_lines.is_empty() {
            let block_dict = self.build_block_dict(py, &current_block_lines)?;
            blocks_list.push(block_dict);
        }

        let result = PyDict::new(py);
        let blocks_py = pyo3::types::PyList::new(py, &blocks_list)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to create list: {}", e)))?;
        result.set_item("blocks", blocks_py)?;

        Ok(result)
    }

    /// Extract text as a structured dict with per-character bounding boxes.
    ///
    /// Like extract_text_dict() but each span includes a "chars" list with
    /// per-character bbox and rotation data. Compatible with PyMuPDF get_text("rawdict").
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     dict: Same structure as extract_text_dict, but spans include "chars" key
    ///           with list of {"c": str, "bbox": tuple, "origin": tuple}
    fn extract_text_rawdict<'py>(&mut self, py: Python<'py>, page: usize) -> PyResult<Bound<'py, PyDict>> {
        let lines = self.inner.extract_text_lines(page)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract lines: {}", e)))?;
        let spans = self.inner.extract_spans(page)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract spans: {}", e)))?;
        let chars = self.inner.extract_chars(page)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract chars: {}", e)))?;

        let mut blocks_list: Vec<Bound<'py, PyDict>> = Vec::new();
        let mut current_block_lines: Vec<(&crate::layout::TextLine, Vec<&crate::layout::TextSpan>)> = Vec::new();
        let mut current_block_bottom = 0.0f32;

        for line in &lines {
            let line_height = line.bbox.height.max(1.0);
            let gap = line.bbox.y - current_block_bottom;

            if !current_block_lines.is_empty() && gap > line_height * 1.5 {
                let block_dict = self.build_rawdict_block(py, &current_block_lines, &chars)?;
                blocks_list.push(block_dict);
                current_block_lines.clear();
            }

            let line_spans: Vec<&crate::layout::TextSpan> = spans.iter()
                .filter(|s| {
                    let s_center_y = s.bbox.y + s.bbox.height / 2.0;
                    s_center_y >= line.bbox.y && s_center_y <= line.bbox.y + line.bbox.height
                })
                .collect();

            current_block_lines.push((line, line_spans));
            current_block_bottom = line.bbox.y + line.bbox.height;
        }

        if !current_block_lines.is_empty() {
            let block_dict = self.build_rawdict_block(py, &current_block_lines, &chars)?;
            blocks_list.push(block_dict);
        }

        let result = PyDict::new(py);
        let blocks_py = pyo3::types::PyList::new(py, &blocks_list)
            .map_err(|e| PyRuntimeError::new_err(format!("list: {}", e)))?;
        result.set_item("blocks", blocks_py)?;
        Ok(result)
    }

    /// Extract individual characters from a page.
    ///
    /// This is a **low-level API** for character-level granularity. For most use cases,
    /// prefer `extract_text()` or `extract_spans()` which provide complete text strings.
    ///
    /// Characters are sorted in reading order (top-to-bottom, left-to-right) and
    /// overlapping characters (rendered multiple times for effects) are deduplicated.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     list[TextChar]: Extracted characters with position, font, and style information
    ///
    /// Raises:
    ///     RuntimeError: If character extraction fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("sample.pdf")
    ///     >>> chars = doc.extract_chars(0)
    ///     >>> for ch in chars:
    ///     ...     print(f"'{ch.char}' at ({ch.bbox.x:.1f}, {ch.bbox.y:.1f})")
    #[pyo3(signature = (page, region=None))]
    fn extract_chars(
        &mut self,
        page: usize,
        region: Option<(f32, f32, f32, f32)>,
    ) -> PyResult<Vec<PyTextChar>> {
        let chars_result = if let Some((x, y, w, h)) = region {
            self.inner.extract_chars_in_rect(
                page,
                crate::geometry::Rect::new(x, y, w, h),
                crate::layout::RectFilterMode::Intersects,
            )
        } else {
            self.inner.extract_chars(page)
        };

        chars_result
            .map(|chars| {
                chars
                    .into_iter()
                    .map(|ch| PyTextChar { inner: ch })
                    .collect()
            })
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract characters: {}", e)))
    }

    /// Extract words from a page (groups characters by proximity).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     list[TextWord]: List of words with bounding boxes
    ///
    /// Example:
    ///     >>> doc = PdfDocument("sample.pdf")
    ///     >>> words = doc.extract_words(0)
    ///     >>> for w in words:
    ///     ...     print(f"{w.text} at {w.bbox}")
    #[pyo3(signature = (page, region=None))]
    fn extract_words(
        &mut self,
        page: usize,
        region: Option<(f32, f32, f32, f32)>,
    ) -> PyResult<Vec<PyWord>> {
        let words_result = if let Some((x, y, w, h)) = region {
            self.inner.extract_words_in_rect(
                page,
                crate::geometry::Rect::new(x, y, w, h),
                crate::layout::RectFilterMode::Intersects,
            )
        } else {
            self.inner.extract_words(page)
        };

        words_result
            .map(|words| words.into_iter().map(|w| PyWord { inner: w }).collect())
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract words: {}", e)))
    }

    /// Extract lines of text from a page (groups words by line).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     region (tuple, optional): (x, y, width, height) to filter by
    ///
    /// Returns:
    ///     list[TextLine]: List of text lines with bounding boxes
    #[pyo3(signature = (page, region=None))]
    fn extract_text_lines(
        &mut self,
        page: usize,
        region: Option<(f32, f32, f32, f32)>,
    ) -> PyResult<Vec<PyTextLine>> {
        let lines_result = if let Some((x, y, w, h)) = region {
            self.inner.extract_text_lines_in_rect(
                page,
                crate::geometry::Rect::new(x, y, w, h),
                crate::layout::RectFilterMode::Intersects,
            )
        } else {
            self.inner.extract_text_lines(page)
        };

        lines_result
            .map(|lines| lines.into_iter().map(|l| PyTextLine { inner: l }).collect())
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract lines: {}", e)))
    }

    /// Check if document has a structure tree (Tagged PDF).
    ///
    /// Tagged PDFs contain explicit document structure that defines reading order,
    /// semantic meaning, and accessibility information. This is the PDF-spec-compliant
    /// way to determine reading order.
    ///
    /// Returns:
    ///     bool: True if document has logical structure (Tagged PDF), False otherwise
    ///
    /// Example:
    ///     >>> doc = PdfDocument("sample.pdf")
    ///     >>> if doc.has_structure_tree():
    ///     ...     print("Tagged PDF with logical structure")
    ///     ... else:
    ///     ...     print("Untagged PDF - uses page content order")
    fn has_structure_tree(&mut self) -> bool {
        match self.inner.structure_tree() {
            Ok(Some(_)) => true,
            _ => false,
        }
    }

    /// Convert page to plain text.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     preserve_layout (bool): Preserve visual layout (default: False) [currently unused]
    ///     detect_headings (bool): Detect headings (default: True) [currently unused]
    ///     include_images (bool): Include images (default: True) [currently unused]
    ///     image_output_dir (str | None): Directory for images (default: None) [currently unused]
    ///
    /// Returns:
    ///     str: Plain text from the page
    ///
    /// Raises:
    ///     RuntimeError: If conversion fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("paper.pdf")
    ///     >>> text = doc.to_plain_text(0)
    ///     >>> print(text[:100])
    ///
    /// Note:
    ///     Options parameters are accepted for API consistency but currently unused for plain text.
    #[pyo3(signature = (page, preserve_layout=false, detect_headings=true, include_images=true, image_output_dir=None))]
    fn to_plain_text(
        &mut self,
        page: usize,
        preserve_layout: bool,
        detect_headings: bool,
        include_images: bool,
        image_output_dir: Option<String>,
    ) -> PyResult<String> {
        let options = RustConversionOptions {
            preserve_layout,
            detect_headings,
            extract_tables: false,
            include_images,
            image_output_dir,
            ..Default::default()
        };

        self.inner
            .to_plain_text(page, &options)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to convert to plain text: {}", e)))
    }

    /// Convert all pages to plain text.
    ///
    /// Args:
    ///     preserve_layout (bool): Preserve visual layout (default: False) [currently unused]
    ///     detect_headings (bool): Detect headings (default: True) [currently unused]
    ///     include_images (bool): Include images (default: True) [currently unused]
    ///     image_output_dir (str | None): Directory for images (default: None) [currently unused]
    ///
    /// Returns:
    ///     str: Plain text from all pages separated by horizontal rules
    ///
    /// Raises:
    ///     RuntimeError: If conversion fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("book.pdf")
    ///     >>> text = doc.to_plain_text_all()
    ///     >>> with open("book.txt", "w") as f:
    ///     ...     f.write(text)
    ///
    /// Note:
    ///     Options parameters are accepted for API consistency but currently unused for plain text.
    #[pyo3(signature = (preserve_layout=false, detect_headings=true, include_images=true, image_output_dir=None))]
    fn to_plain_text_all(
        &mut self,
        preserve_layout: bool,
        detect_headings: bool,
        include_images: bool,
        image_output_dir: Option<String>,
    ) -> PyResult<String> {
        let options = RustConversionOptions {
            preserve_layout,
            detect_headings,
            extract_tables: false,
            include_images,
            image_output_dir,
            ..Default::default()
        };

        self.inner.to_plain_text_all(&options).map_err(|e| {
            PyRuntimeError::new_err(format!("Failed to convert all pages to plain text: {}", e))
        })
    }

    /// Convert page to Markdown.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     preserve_layout (bool): Preserve visual layout (default: False)
    ///     detect_headings (bool): Detect headings based on font size (default: True)
    ///     include_images (bool): Include images in output (default: True)
    ///     image_output_dir (str | None): Directory to save images (default: None)
    ///
    /// Returns:
    ///     str: Markdown text
    ///
    /// Raises:
    ///     RuntimeError: If conversion fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("paper.pdf")
    ///     >>> markdown = doc.to_markdown(0, detect_headings=True)
    ///     >>> with open("output.md", "w") as f:
    ///     ...     f.write(markdown)
    #[pyo3(signature = (page, preserve_layout=false, detect_headings=true, include_images=true, image_output_dir=None, embed_images=true, include_form_fields=true))]
    fn to_markdown(
        &mut self,
        page: usize,
        preserve_layout: bool,
        detect_headings: bool,
        include_images: bool,
        image_output_dir: Option<String>,
        embed_images: bool,
        include_form_fields: bool,
    ) -> PyResult<String> {
        let options = RustConversionOptions {
            preserve_layout,
            detect_headings,
            extract_tables: false,
            include_images,
            image_output_dir,
            embed_images,
            include_form_fields,
            ..Default::default()
        };

        self.inner
            .to_markdown(page, &options)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to convert to Markdown: {}", e)))
    }

    /// Convert page to HTML.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     preserve_layout (bool): Preserve visual layout with CSS positioning (default: False)
    ///     detect_headings (bool): Detect headings based on font size (default: True)
    ///     include_images (bool): Include images in output (default: True)
    ///     image_output_dir (str | None): Directory to save images (default: None)
    ///
    /// Returns:
    ///     str: HTML text
    ///
    /// Raises:
    ///     RuntimeError: If conversion fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("paper.pdf")
    ///     >>> html = doc.to_html(0, preserve_layout=False)
    ///     >>> with open("output.html", "w") as f:
    ///     ...     f.write(html)
    #[pyo3(signature = (page, preserve_layout=false, detect_headings=true, include_images=true, image_output_dir=None, embed_images=true, include_form_fields=true))]
    fn to_html(
        &mut self,
        page: usize,
        preserve_layout: bool,
        detect_headings: bool,
        include_images: bool,
        image_output_dir: Option<String>,
        embed_images: bool,
        include_form_fields: bool,
    ) -> PyResult<String> {
        let options = RustConversionOptions {
            preserve_layout,
            detect_headings,
            extract_tables: false,
            include_images,
            image_output_dir,
            embed_images,
            include_form_fields,
            ..Default::default()
        };

        self.inner
            .to_html(page, &options)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to convert to HTML: {}", e)))
    }

    /// Convert all pages to Markdown.
    ///
    /// Args:
    ///     preserve_layout (bool): Preserve visual layout (default: False)
    ///     detect_headings (bool): Detect headings based on font size (default: True)
    ///     include_images (bool): Include images in output (default: True)
    ///     image_output_dir (str | None): Directory to save images (default: None)
    ///
    /// Returns:
    ///     str: Markdown text with all pages separated by horizontal rules
    ///
    /// Raises:
    ///     RuntimeError: If conversion fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("book.pdf")
    ///     >>> markdown = doc.to_markdown_all(detect_headings=True)
    ///     >>> with open("book.md", "w") as f:
    ///     ...     f.write(markdown)
    #[pyo3(signature = (preserve_layout=false, detect_headings=true, include_images=true, image_output_dir=None, embed_images=true, include_form_fields=true))]
    fn to_markdown_all(
        &mut self,
        preserve_layout: bool,
        detect_headings: bool,
        include_images: bool,
        image_output_dir: Option<String>,
        embed_images: bool,
        include_form_fields: bool,
    ) -> PyResult<String> {
        let options = RustConversionOptions {
            preserve_layout,
            detect_headings,
            extract_tables: false,
            include_images,
            image_output_dir,
            embed_images,
            include_form_fields,
            ..Default::default()
        };

        self.inner.to_markdown_all(&options).map_err(|e| {
            PyRuntimeError::new_err(format!("Failed to convert all pages to Markdown: {}", e))
        })
    }

    /// Convert all pages to HTML.
    ///
    /// Args:
    ///     preserve_layout (bool): Preserve visual layout with CSS positioning (default: False)
    ///     detect_headings (bool): Detect headings based on font size (default: True)
    ///     include_images (bool): Include images in output (default: True)
    ///     image_output_dir (str | None): Directory to save images (default: None)
    ///
    /// Returns:
    ///     str: HTML text with all pages wrapped in div.page elements
    ///
    /// Raises:
    ///     RuntimeError: If conversion fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("book.pdf")
    ///     >>> html = doc.to_html_all(preserve_layout=True)
    ///     >>> with open("book.html", "w") as f:
    ///     ...     f.write(html)
    #[pyo3(signature = (preserve_layout=false, detect_headings=true, include_images=true, image_output_dir=None, embed_images=true, include_form_fields=true))]
    fn to_html_all(
        &mut self,
        preserve_layout: bool,
        detect_headings: bool,
        include_images: bool,
        image_output_dir: Option<String>,
        embed_images: bool,
        include_form_fields: bool,
    ) -> PyResult<String> {
        let options = RustConversionOptions {
            preserve_layout,
            detect_headings,
            extract_tables: false,
            include_images,
            image_output_dir,
            embed_images,
            include_form_fields,
            ..Default::default()
        };

        self.inner.to_html_all(&options).map_err(|e| {
            PyRuntimeError::new_err(format!("Failed to convert all pages to HTML: {}", e))
        })
    }

    /// Get a page for DOM-like navigation and editing.
    ///
    /// Returns a `PdfPage` object that provides hierarchical access to page content,
    /// allowing you to query, navigate, and modify elements.
    ///
    /// Args:
    ///     index (int): Page index (0-based)
    ///
    /// Returns:
    ///     PdfPage: Page object with DOM access
    ///
    /// Raises:
    ///     RuntimeError: If page access fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("sample.pdf")
    ///     >>> page = doc.page(0)
    ///     >>> for text in page.find_text_containing("Hello"):
    ///     ...     print(f"{text.value} at {text.bbox}")
    fn page(&mut self, index: usize) -> PyResult<PyPdfPage> {
        // Lazy-initialize editor if needed
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }

        let editor = self.editor.as_mut().unwrap();
        let page = editor
            .get_page(index)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to get page: {}", e)))?;

        Ok(PyPdfPage { inner: page })
    }

    /// Save modifications made via page().set_text() back to a file.
    ///
    /// Args:
    ///     path (str): Output file path
    ///     page (PdfPage): The modified page to save
    ///
    /// Raises:
    ///     RuntimeError: If save fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("input.pdf")
    ///     >>> page = doc.page(0)
    ///     >>> for t in page.find_text_containing("old"):
    ///     ...     page.set_text(t.id, "new")
    ///     >>> doc.save_page(page)
    ///     >>> doc.save("output.pdf")
    fn save_page(&mut self, page: &PyPdfPage) -> PyResult<()> {
        if self.editor.is_none() {
            return Err(PyRuntimeError::new_err("No editor initialized. Call page() first."));
        }

        let editor = self.editor.as_mut().unwrap();
        editor
            .save_page(page.inner.clone())
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to save page: {}", e)))
    }

    /// Save the document to a file.
    ///
    /// This saves any modifications made via page().set_text().
    ///
    /// Args:
    ///     path (str): Output file path
    ///
    /// Raises:
    ///     IOError: If save fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("input.pdf")
    ///     >>> page = doc.page(0)
    ///     >>> page.set_text(text_id, "new text")
    ///     >>> doc.save_page(page)
    ///     >>> doc.save("output.pdf")
    fn save(&mut self, path: &str) -> PyResult<()> {
        use crate::editor::EditableDocument;

        if let Some(ref mut editor) = self.editor {
            editor
                .save(path)
                .map_err(|e| PyIOError::new_err(format!("Failed to save PDF: {}", e)))
        } else {
            Err(PyRuntimeError::new_err(
                "No modifications to save. Use page() and set_text() first.",
            ))
        }
    }

    /// Save the document as an incremental update (append changes to original).
    ///
    /// This preserves the original file structure and encryption settings.
    /// Faster and smaller than a full rewrite for minor changes.
    ///
    /// Args:
    ///     path (str): Output file path
    ///
    /// Raises:
    ///     IOError: If save fails
    fn save_incremental(&mut self, path: &str) -> PyResult<()> {
        use crate::editor::{EditableDocument, SaveOptions};

        if let Some(ref mut editor) = self.editor {
            editor
                .save_with_options(path, SaveOptions::incremental())
                .map_err(|e| PyIOError::new_err(format!("Failed to save incremental: {}", e)))
        } else {
            Err(PyRuntimeError::new_err(
                "No modifications to save. Use page() and set_text() first.",
            ))
        }
    }

    /// Save the document with password encryption.
    ///
    /// Creates a password-protected PDF using AES-256 encryption (the strongest available).
    ///
    /// Args:
    ///     path (str): Output file path
    ///     user_password (str): Password required to open the document (can be empty string
    ///         for no open password, but still apply owner restrictions)
    ///     owner_password (str): Password for full access and changing security settings.
    ///         If empty, defaults to user_password.
    ///     allow_print (bool): Allow printing (default: True)
    ///     allow_copy (bool): Allow copying text and graphics (default: True)
    ///     allow_modify (bool): Allow modifying the document (default: True)
    ///     allow_annotate (bool): Allow adding annotations (default: True)
    ///
    /// Raises:
    ///     RuntimeError: If no modifications have been made
    ///     IOError: If save fails
    ///
    /// Example:
    /// ```text
    /// >>> doc = PdfDocument("input.pdf")
    /// >>> page = doc.page(0)
    /// >>> page.set_text(text_id, "modified")
    /// >>> doc.save_page(page)
    /// >>> doc.save_encrypted("protected.pdf", "user123", "owner456")
    ///
    /// >>> # View-only PDF (no printing, copying, or modifying):
    /// >>> doc.save_encrypted("readonly.pdf", "", "owner456",
    /// ...     allow_print=False, allow_copy=False, allow_modify=False)
    /// ```
    #[pyo3(signature = (path, user_password, owner_password=None, allow_print=true, allow_copy=true, allow_modify=true, allow_annotate=true))]
    fn save_encrypted(
        &mut self,
        path: &str,
        user_password: &str,
        owner_password: Option<&str>,
        allow_print: bool,
        allow_copy: bool,
        allow_modify: bool,
        allow_annotate: bool,
    ) -> PyResult<()> {
        use crate::editor::{
            EditableDocument, EncryptionAlgorithm, EncryptionConfig, Permissions, SaveOptions,
        };

        if let Some(ref mut editor) = self.editor {
            let owner_pwd = owner_password.unwrap_or(user_password);

            let permissions = Permissions {
                print: allow_print,
                print_high_quality: allow_print,
                modify: allow_modify,
                copy: allow_copy,
                annotate: allow_annotate,
                fill_forms: allow_annotate,
                accessibility: true, // Always allow for compliance
                assemble: allow_modify,
            };

            let config = EncryptionConfig::new(user_password, owner_pwd)
                .with_algorithm(EncryptionAlgorithm::Aes256)
                .with_permissions(permissions);

            let options = SaveOptions::with_encryption(config);
            editor
                .save_with_options(path, options)
                .map_err(|e| PyIOError::new_err(format!("Failed to save encrypted PDF: {}", e)))
        } else {
            Err(PyRuntimeError::new_err(
                "No modifications to save. Use page() and set_text() first.",
            ))
        }
    }

    // === Drawing Operations ===

    /// Draw a rectangle on a page.
    ///
    /// Args:
    ///     page_num (int): Zero-based page index
    ///     x (float): X coordinate (left)
    ///     y (float): Y coordinate (bottom)
    ///     width (float): Rectangle width
    ///     height (float): Rectangle height
    ///     color (tuple): Stroke color as (r, g, b) floats 0.0-1.0
    ///     fill (tuple, optional): Fill color as (r, g, b), or None for no fill
    ///     line_width (float): Stroke width in points (default: 1.0)
    ///
    /// Example:
    ///     >>> doc.draw_rect(0, 100, 200, 50, 30, (1.0, 0.0, 0.0))
    ///     >>> doc.draw_rect(0, 100, 200, 50, 30, (0, 0, 0), fill=(1, 1, 0))
    #[pyo3(signature = (page_num, x, y, width, height, color=(0.0, 0.0, 0.0), fill=None, line_width=1.0))]
    fn draw_rect(
        &mut self,
        page_num: usize,
        x: f32,
        y: f32,
        width: f32,
        height: f32,
        color: (f32, f32, f32),
        fill: Option<(f32, f32, f32)>,
        line_width: f32,
    ) -> PyResult<()> {
        self.ensure_editor()?;
        let mut content = Vec::new();
        content.extend_from_slice(b"q\n");
        content.extend_from_slice(format!("{:.4} w\n", line_width).as_bytes());
        content.extend_from_slice(
            format!("{:.4} {:.4} {:.4} RG\n", color.0, color.1, color.2).as_bytes(),
        );
        if let Some(f) = fill {
            content.extend_from_slice(
                format!("{:.4} {:.4} {:.4} rg\n", f.0, f.1, f.2).as_bytes(),
            );
        }
        content.extend_from_slice(
            format!("{:.4} {:.4} {:.4} {:.4} re ", x, y, width, height).as_bytes(),
        );
        if fill.is_some() {
            content.extend_from_slice(b"B\n"); // fill and stroke
        } else {
            content.extend_from_slice(b"S\n"); // stroke only
        }
        content.extend_from_slice(b"Q\n");

        let editor = self.editor.as_mut().unwrap();
        editor
            .add_draw_overlay(page_num, content)
            .map_err(|e| PyRuntimeError::new_err(format!("draw_rect failed: {}", e)))
    }

    /// Draw a line on a page.
    ///
    /// Args:
    ///     page_num (int): Zero-based page index
    ///     x1 (float): Start X
    ///     y1 (float): Start Y
    ///     x2 (float): End X
    ///     y2 (float): End Y
    ///     color (tuple): Stroke color as (r, g, b) floats 0.0-1.0
    ///     line_width (float): Line width in points (default: 1.0)
    #[pyo3(signature = (page_num, x1, y1, x2, y2, color=(0.0, 0.0, 0.0), line_width=1.0))]
    fn draw_line(
        &mut self,
        page_num: usize,
        x1: f32,
        y1: f32,
        x2: f32,
        y2: f32,
        color: (f32, f32, f32),
        line_width: f32,
    ) -> PyResult<()> {
        self.ensure_editor()?;
        let mut content = Vec::new();
        content.extend_from_slice(b"q\n");
        content.extend_from_slice(format!("{:.4} w\n", line_width).as_bytes());
        content.extend_from_slice(
            format!("{:.4} {:.4} {:.4} RG\n", color.0, color.1, color.2).as_bytes(),
        );
        content.extend_from_slice(
            format!("{:.4} {:.4} m {:.4} {:.4} l S\n", x1, y1, x2, y2).as_bytes(),
        );
        content.extend_from_slice(b"Q\n");

        let editor = self.editor.as_mut().unwrap();
        editor
            .add_draw_overlay(page_num, content)
            .map_err(|e| PyRuntimeError::new_err(format!("draw_line failed: {}", e)))
    }

    /// Draw a circle on a page.
    ///
    /// Args:
    ///     page_num (int): Zero-based page index
    ///     cx (float): Center X
    ///     cy (float): Center Y
    ///     radius (float): Radius
    ///     color (tuple): Stroke color as (r, g, b)
    ///     fill (tuple, optional): Fill color or None
    ///     line_width (float): Stroke width (default: 1.0)
    #[pyo3(signature = (page_num, cx, cy, radius, color=(0.0, 0.0, 0.0), fill=None, line_width=1.0))]
    fn draw_circle(
        &mut self,
        page_num: usize,
        cx: f32,
        cy: f32,
        radius: f32,
        color: (f32, f32, f32),
        fill: Option<(f32, f32, f32)>,
        line_width: f32,
    ) -> PyResult<()> {
        self.ensure_editor()?;
        // Approximate circle with 4 Bézier curves (kappa = 0.5522847498)
        let k = 0.5522847498_f32 * radius;
        let mut content = Vec::new();
        content.extend_from_slice(b"q\n");
        content.extend_from_slice(format!("{:.4} w\n", line_width).as_bytes());
        content.extend_from_slice(
            format!("{:.4} {:.4} {:.4} RG\n", color.0, color.1, color.2).as_bytes(),
        );
        if let Some(f) = fill {
            content.extend_from_slice(
                format!("{:.4} {:.4} {:.4} rg\n", f.0, f.1, f.2).as_bytes(),
            );
        }
        // 4 Bézier curves forming a circle
        content.extend_from_slice(format!("{:.4} {:.4} m\n", cx + radius, cy).as_bytes());
        content.extend_from_slice(
            format!(
                "{:.4} {:.4} {:.4} {:.4} {:.4} {:.4} c\n",
                cx + radius, cy + k, cx + k, cy + radius, cx, cy + radius
            )
            .as_bytes(),
        );
        content.extend_from_slice(
            format!(
                "{:.4} {:.4} {:.4} {:.4} {:.4} {:.4} c\n",
                cx - k, cy + radius, cx - radius, cy + k, cx - radius, cy
            )
            .as_bytes(),
        );
        content.extend_from_slice(
            format!(
                "{:.4} {:.4} {:.4} {:.4} {:.4} {:.4} c\n",
                cx - radius, cy - k, cx - k, cy - radius, cx, cy - radius
            )
            .as_bytes(),
        );
        content.extend_from_slice(
            format!(
                "{:.4} {:.4} {:.4} {:.4} {:.4} {:.4} c\n",
                cx + k, cy - radius, cx + radius, cy - k, cx + radius, cy
            )
            .as_bytes(),
        );
        if fill.is_some() {
            content.extend_from_slice(b"B\n");
        } else {
            content.extend_from_slice(b"S\n");
        }
        content.extend_from_slice(b"Q\n");

        let editor = self.editor.as_mut().unwrap();
        editor
            .add_draw_overlay(page_num, content)
            .map_err(|e| PyRuntimeError::new_err(format!("draw_circle failed: {}", e)))
    }

    /// Insert text at a position on a page.
    ///
    /// Args:
    ///     page_num (int): Zero-based page index
    ///     x (float): X coordinate
    ///     y (float): Y coordinate
    ///     text (str): Text to insert
    ///     font_size (float): Font size in points (default: 12.0)
    ///     color (tuple): Text color as (r, g, b) (default: black)
    ///
    /// Note:
    ///     Uses Helvetica font (Base-14, always available). For other fonts,
    ///     use add_text() via page() which supports the DOM API.
    #[pyo3(signature = (page_num, x, y, text, font_size=12.0, color=(0.0, 0.0, 0.0)))]
    fn insert_text(
        &mut self,
        page_num: usize,
        x: f32,
        y: f32,
        text: &str,
        font_size: f32,
        color: (f32, f32, f32),
    ) -> PyResult<()> {
        self.ensure_editor()?;
        // Escape special PDF string characters
        let escaped = text
            .replace('\\', "\\\\")
            .replace('(', "\\(")
            .replace(')', "\\)");
        let mut content = Vec::new();
        content.extend_from_slice(b"q\n");
        content.extend_from_slice(
            format!("{:.4} {:.4} {:.4} rg\n", color.0, color.1, color.2).as_bytes(),
        );
        content.extend_from_slice(b"BT\n");
        content.extend_from_slice(format!("/Helvetica {:.1} Tf\n", font_size).as_bytes());
        content.extend_from_slice(format!("{:.4} {:.4} Td\n", x, y).as_bytes());
        content.extend_from_slice(format!("({}) Tj\n", escaped).as_bytes());
        content.extend_from_slice(b"ET\n");
        content.extend_from_slice(b"Q\n");

        let editor = self.editor.as_mut().unwrap();
        editor
            .add_draw_overlay(page_num, content)
            .map_err(|e| PyRuntimeError::new_err(format!("insert_text failed: {}", e)))
    }

    /// Insert text within a bounding box with word-wrapping.
    ///
    /// Args:
    ///     page_num (int): Zero-based page index
    ///     x (float): Left X coordinate
    ///     y (float): Top Y coordinate (text flows downward)
    ///     width (float): Box width
    ///     height (float): Box height
    ///     text (str): Text to insert
    ///     font_size (float): Font size (default: 12.0)
    ///     color (tuple): Text color (default: black)
    #[pyo3(signature = (page_num, x, y, width, height, text, font_size=12.0, color=(0.0, 0.0, 0.0)))]
    fn insert_textbox(
        &mut self,
        page_num: usize,
        x: f32,
        y: f32,
        width: f32,
        height: f32,
        text: &str,
        font_size: f32,
        color: (f32, f32, f32),
    ) -> PyResult<()> {
        self.ensure_editor()?;
        // Simple word-wrap: approximate char width as font_size * 0.5
        let char_width = font_size * 0.5;
        let chars_per_line = (width / char_width).floor() as usize;
        let line_height = font_size * 1.2;

        let mut lines: Vec<String> = Vec::new();
        for paragraph in text.split('\n') {
            let words: Vec<&str> = paragraph.split_whitespace().collect();
            let mut current_line = String::new();
            for word in words {
                if current_line.is_empty() {
                    current_line = word.to_string();
                } else if current_line.len() + 1 + word.len() <= chars_per_line {
                    current_line.push(' ');
                    current_line.push_str(word);
                } else {
                    lines.push(current_line);
                    current_line = word.to_string();
                }
            }
            if !current_line.is_empty() {
                lines.push(current_line);
            }
        }

        // Clip to box height
        let max_lines = (height / line_height).floor() as usize;
        if lines.len() > max_lines {
            lines.truncate(max_lines);
        }

        let mut content = Vec::new();
        content.extend_from_slice(b"q\n");
        content.extend_from_slice(
            format!("{:.4} {:.4} {:.4} rg\n", color.0, color.1, color.2).as_bytes(),
        );
        content.extend_from_slice(b"BT\n");
        content.extend_from_slice(format!("/Helvetica {:.1} Tf\n", font_size).as_bytes());

        for (i, line) in lines.iter().enumerate() {
            let line_y = y - (i as f32) * line_height;
            let escaped = line
                .replace('\\', "\\\\")
                .replace('(', "\\(")
                .replace(')', "\\)");
            content.extend_from_slice(format!("{:.4} {:.4} Td\n", x, line_y).as_bytes());
            content.extend_from_slice(format!("({}) Tj\n", escaped).as_bytes());
        }

        content.extend_from_slice(b"ET\n");
        content.extend_from_slice(b"Q\n");

        let editor = self.editor.as_mut().unwrap();
        editor
            .add_draw_overlay(page_num, content)
            .map_err(|e| PyRuntimeError::new_err(format!("insert_textbox failed: {}", e)))
    }

    // === Page Manipulation ===

    /// Delete a page from the document.
    ///
    /// Args:
    ///     index (int): Zero-based page index to remove
    fn delete_page(&mut self, index: usize) -> PyResult<()> {
        use crate::editor::EditableDocument;
        self.ensure_editor()?;
        let editor = self.editor.as_mut().unwrap();
        editor
            .remove_page(index)
            .map_err(|e| PyRuntimeError::new_err(format!("delete_page failed: {}", e)))
    }

    /// Move a page from one position to another.
    ///
    /// Args:
    ///     from_index (int): Source page index
    ///     to_index (int): Destination page index
    fn move_page(&mut self, from_index: usize, to_index: usize) -> PyResult<()> {
        use crate::editor::EditableDocument;
        self.ensure_editor()?;
        let editor = self.editor.as_mut().unwrap();
        editor
            .move_page(from_index, to_index)
            .map_err(|e| PyRuntimeError::new_err(format!("move_page failed: {}", e)))
    }

    /// Duplicate a page, appending the copy at the end.
    ///
    /// Args:
    ///     index (int): Page index to duplicate
    ///
    /// Returns:
    ///     int: Index of the new duplicate page
    fn duplicate_page(&mut self, index: usize) -> PyResult<usize> {
        use crate::editor::EditableDocument;
        self.ensure_editor()?;
        let editor = self.editor.as_mut().unwrap();
        editor
            .duplicate_page(index)
            .map_err(|e| PyRuntimeError::new_err(format!("duplicate_page failed: {}", e)))
    }

    /// Merge pages from another PDF into this document.
    ///
    /// Args:
    ///     path (str): Path to the PDF to merge from
    ///     pages (list[int], optional): Specific page indices to merge, or None for all
    ///
    /// Returns:
    ///     int: Number of pages merged
    #[pyo3(signature = (path, pages=None))]
    fn merge_pages(&mut self, path: &str, pages: Option<Vec<usize>>) -> PyResult<usize> {
        self.ensure_editor()?;
        let editor = self.editor.as_mut().unwrap();
        if let Some(page_list) = pages {
            editor
                .merge_pages_from(path, &page_list)
                .map_err(|e| PyRuntimeError::new_err(format!("merge_pages failed: {}", e)))
        } else {
            editor
                .merge_from(path)
                .map_err(|e| PyRuntimeError::new_err(format!("merge_pages failed: {}", e)))
        }
    }

    // === Document Metadata ===

    /// Set the document title.
    ///
    /// Args:
    ///     title (str): Document title
    ///
    /// Example:
    ///     >>> doc.set_title("My Document")
    fn set_title(&mut self, title: &str) -> PyResult<()> {
        // Lazy-initialize editor if needed
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor.set_title(title);
        }
        Ok(())
    }

    /// Set the document author.
    ///
    /// Args:
    ///     author (str): Author name
    fn set_author(&mut self, author: &str) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor.set_author(author);
        }
        Ok(())
    }

    /// Set the document subject.
    ///
    /// Args:
    ///     subject (str): Document subject
    fn set_subject(&mut self, subject: &str) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor.set_subject(subject);
        }
        Ok(())
    }

    /// Set the document keywords.
    ///
    /// Args:
    ///     keywords (str): Comma-separated keywords
    fn set_keywords(&mut self, keywords: &str) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor.set_keywords(keywords);
        }
        Ok(())
    }

    // =========================================================================
    // Page Properties: Rotation, Cropping
    // =========================================================================

    /// Get the rotation of a page in degrees (0, 90, 180, 270).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     int: Rotation in degrees
    ///
    /// Example:
    ///     >>> rotation = doc.page_rotation(0)
    ///     >>> print(f"Page is rotated {rotation} degrees")
    fn page_rotation(&mut self, page: usize) -> PyResult<i32> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .get_page_rotation(page)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to get rotation: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Set the rotation of a page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     degrees (int): Rotation in degrees (0, 90, 180, or 270)
    ///
    /// Example:
    ///     >>> doc.set_page_rotation(0, 90)
    ///     >>> doc.save("rotated.pdf")
    fn set_page_rotation(&mut self, page: usize, degrees: i32) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .set_page_rotation(page, degrees)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to set rotation: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Rotate a page by the given degrees (adds to current rotation).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     degrees (int): Degrees to rotate (will be normalized to 0, 90, 180, 270)
    ///
    /// Example:
    ///     >>> doc.rotate_page(0, 90)  # Rotate 90 degrees clockwise
    ///     >>> doc.save("rotated.pdf")
    fn rotate_page(&mut self, page: usize, degrees: i32) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .rotate_page_by(page, degrees)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to rotate page: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Rotate all pages by the given degrees.
    ///
    /// Args:
    ///     degrees (int): Degrees to rotate (will be normalized to 0, 90, 180, 270)
    ///
    /// Example:
    ///     >>> doc.rotate_all_pages(180)  # Flip all pages upside down
    ///     >>> doc.save("rotated.pdf")
    fn rotate_all_pages(&mut self, degrees: i32) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .rotate_all_pages(degrees)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to rotate pages: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Get the MediaBox of a page (physical page size).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     tuple[float, float, float, float]: (llx, lly, urx, ury) coordinates
    ///
    /// Example:
    ///     >>> llx, lly, urx, ury = doc.page_media_box(0)
    ///     >>> print(f"Page size: {urx - llx} x {ury - lly}")
    fn page_media_box(&mut self, page: usize) -> PyResult<(f32, f32, f32, f32)> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            let box_ = editor
                .get_page_media_box(page)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to get MediaBox: {}", e)))?;
            Ok((box_[0], box_[1], box_[2], box_[3]))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Get the dimensions of a page as (width, height) in points.
    ///
    /// Derived from the MediaBox. 72 points = 1 inch.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     tuple[float, float]: (width, height) in points
    fn page_dimensions(&mut self, page: usize) -> PyResult<(f32, f32)> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            let box_ = editor
                .get_page_media_box(page)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to get page dimensions: {}", e)))?;
            Ok((box_[2] - box_[0], box_[3] - box_[1]))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Set the MediaBox of a page (physical page size).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     llx (float): Lower-left X coordinate
    ///     lly (float): Lower-left Y coordinate
    ///     urx (float): Upper-right X coordinate
    ///     ury (float): Upper-right Y coordinate
    fn set_page_media_box(
        &mut self,
        page: usize,
        llx: f32,
        lly: f32,
        urx: f32,
        ury: f32,
    ) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .set_page_media_box(page, [llx, lly, urx, ury])
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to set MediaBox: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Get the CropBox of a page (visible/printable area).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     tuple[float, float, float, float] | None: (llx, lly, urx, ury) or None if not set
    fn page_crop_box(&mut self, page: usize) -> PyResult<Option<(f32, f32, f32, f32)>> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            let box_ = editor
                .get_page_crop_box(page)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to get CropBox: {}", e)))?;
            Ok(box_.map(|b| (b[0], b[1], b[2], b[3])))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Set the CropBox of a page (visible/printable area).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     llx (float): Lower-left X coordinate
    ///     lly (float): Lower-left Y coordinate
    ///     urx (float): Upper-right X coordinate
    ///     ury (float): Upper-right Y coordinate
    ///
    /// Example:
    /// ```text
    /// >>> # Crop to a 6x9 inch area (72 points = 1 inch)
    /// >>> doc.set_page_crop_box(0, 72, 72, 504, 720)
    /// >>> doc.save("cropped.pdf")
    /// ```
    fn set_page_crop_box(
        &mut self,
        page: usize,
        llx: f32,
        lly: f32,
        urx: f32,
        ury: f32,
    ) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .set_page_crop_box(page, [llx, lly, urx, ury])
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to set CropBox: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Get the ArtBox of a page (artistic content boundary).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     tuple[float, float, float, float] | None: (llx, lly, urx, ury) or None if not set
    fn page_art_box(&mut self, page: usize) -> PyResult<Option<(f32, f32, f32, f32)>> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            let box_ = editor
                .get_page_art_box(page)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to get ArtBox: {}", e)))?;
            Ok(box_.map(|b| (b[0], b[1], b[2], b[3])))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Set the ArtBox of a page (artistic content boundary).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     llx (float): Lower-left X coordinate
    ///     lly (float): Lower-left Y coordinate
    ///     urx (float): Upper-right X coordinate
    ///     ury (float): Upper-right Y coordinate
    fn set_page_art_box(
        &mut self,
        page: usize,
        llx: f32,
        lly: f32,
        urx: f32,
        ury: f32,
    ) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .set_page_art_box(page, [llx, lly, urx, ury])
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to set ArtBox: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Get the BleedBox of a page (bleed area for printing).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     tuple[float, float, float, float] | None: (llx, lly, urx, ury) or None if not set
    fn page_bleed_box(&mut self, page: usize) -> PyResult<Option<(f32, f32, f32, f32)>> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            let box_ = editor
                .get_page_bleed_box(page)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to get BleedBox: {}", e)))?;
            Ok(box_.map(|b| (b[0], b[1], b[2], b[3])))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Set the BleedBox of a page (bleed area for printing).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     llx (float): Lower-left X coordinate
    ///     lly (float): Lower-left Y coordinate
    ///     urx (float): Upper-right X coordinate
    ///     ury (float): Upper-right Y coordinate
    fn set_page_bleed_box(
        &mut self,
        page: usize,
        llx: f32,
        lly: f32,
        urx: f32,
        ury: f32,
    ) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .set_page_bleed_box(page, [llx, lly, urx, ury])
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to set BleedBox: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Get the TrimBox of a page (final trim boundaries).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     tuple[float, float, float, float] | None: (llx, lly, urx, ury) or None if not set
    fn page_trim_box(&mut self, page: usize) -> PyResult<Option<(f32, f32, f32, f32)>> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            let box_ = editor
                .get_page_trim_box(page)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to get TrimBox: {}", e)))?;
            Ok(box_.map(|b| (b[0], b[1], b[2], b[3])))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Set the TrimBox of a page (final trim boundaries).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     llx (float): Lower-left X coordinate
    ///     lly (float): Lower-left Y coordinate
    ///     urx (float): Upper-right X coordinate
    ///     ury (float): Upper-right Y coordinate
    fn set_page_trim_box(
        &mut self,
        page: usize,
        llx: f32,
        lly: f32,
        urx: f32,
        ury: f32,
    ) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .set_page_trim_box(page, [llx, lly, urx, ury])
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to set TrimBox: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Crop margins from all pages.
    ///
    /// Sets the CropBox to be smaller than the MediaBox by the specified margins.
    ///
    /// Args:
    ///     left (float): Left margin in points
    ///     right (float): Right margin in points
    ///     top (float): Top margin in points
    ///     bottom (float): Bottom margin in points
    ///
    /// Example:
    /// ```text
    /// >>> # Crop 0.5 inch from all sides (72 points = 1 inch)
    /// >>> doc.crop_margins(36, 36, 36, 36)
    /// >>> doc.save("cropped.pdf")
    /// ```
    fn crop_margins(&mut self, left: f32, right: f32, top: f32, bottom: f32) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .crop_margins(left, right, top, bottom)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to crop margins: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    // =========================================================================
    // Content Erasing (Whiteout)
    // =========================================================================

    /// Erase a rectangular region on a page by covering it with white.
    ///
    /// This adds a white rectangle overlay that covers the specified region.
    /// The original content is not removed but hidden beneath the white overlay.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     llx (float): Lower-left X coordinate
    ///     lly (float): Lower-left Y coordinate
    ///     urx (float): Upper-right X coordinate
    ///     ury (float): Upper-right Y coordinate
    ///
    /// Example:
    /// ```text
    /// >>> # Erase a region in the upper-left corner
    /// >>> doc.erase_region(0, 72, 700, 200, 792)
    /// >>> doc.save("output.pdf")
    /// ```
    fn erase_region(
        &mut self,
        page: usize,
        llx: f32,
        lly: f32,
        urx: f32,
        ury: f32,
    ) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .erase_region(page, [llx, lly, urx, ury])
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to erase region: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Erase multiple rectangular regions on a page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     rects (list[tuple[float, float, float, float]]): List of (llx, lly, urx, ury) tuples
    ///
    /// Example:
    ///     >>> doc.erase_regions(0, [(72, 700, 200, 792), (300, 300, 500, 400)])
    ///     >>> doc.save("output.pdf")
    fn erase_regions(&mut self, page: usize, rects: Vec<(f32, f32, f32, f32)>) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            let rect_arrays: Vec<[f32; 4]> = rects
                .iter()
                .map(|(llx, lly, urx, ury)| [*llx, *lly, *urx, *ury])
                .collect();
            editor
                .erase_regions(page, &rect_arrays)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to erase regions: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Clear all pending erase operations for a page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    fn clear_erase_regions(&mut self, page: usize) {
        if let Some(ref mut editor) = self.editor {
            editor.clear_erase_regions(page);
        }
    }

    // ========================================================================
    // Annotation Flattening
    // ========================================================================

    /// Flatten annotations on a specific page.
    ///
    /// Renders annotation appearance streams into the page content and removes
    /// the annotations. This makes annotations permanent and non-editable.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Raises:
    ///     RuntimeError: If page index is out of range
    ///
    /// Example:
    ///     >>> doc.flatten_page_annotations(0)  # Flatten page 0
    ///     >>> doc.save("flattened.pdf")
    fn flatten_page_annotations(&mut self, page: usize) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor.flatten_page_annotations(page).map_err(|e| {
                PyRuntimeError::new_err(format!("Failed to flatten annotations: {}", e))
            })
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Flatten annotations on all pages.
    ///
    /// Renders all annotation appearance streams into page content and removes
    /// all annotations from the document.
    ///
    /// Raises:
    ///     RuntimeError: If the operation fails
    ///
    /// Example:
    ///     >>> doc.flatten_all_annotations()
    ///     >>> doc.save("flattened.pdf")
    fn flatten_all_annotations(&mut self) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor.flatten_all_annotations().map_err(|e| {
                PyRuntimeError::new_err(format!("Failed to flatten annotations: {}", e))
            })
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Check if a page is marked for annotation flattening.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     bool: True if the page is marked for flattening
    fn is_page_marked_for_flatten(&self, page: usize) -> bool {
        if let Some(ref editor) = self.editor {
            editor.is_page_marked_for_flatten(page)
        } else {
            false
        }
    }

    /// Unmark a page for annotation flattening.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    fn unmark_page_for_flatten(&mut self, page: usize) {
        if let Some(ref mut editor) = self.editor {
            editor.unmark_page_for_flatten(page);
        }
    }

    // ========================================================================
    // Redaction Application
    // ========================================================================

    /// Apply redactions on a specific page.
    ///
    /// Performs true content-stripping redaction: parses the page content stream,
    /// removes text/images that fall within redaction rectangles, and draws
    /// colored overlays. The redaction annotations are removed from the page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Note:
    ///     This performs true content removal — redacted text cannot be recovered.
    ///
    /// Raises:
    ///     RuntimeError: If page index is out of range
    ///
    /// Example:
    ///     >>> doc.apply_page_redactions(0)
    ///     >>> doc.save("redacted.pdf")
    fn apply_page_redactions(&mut self, page: usize) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .apply_page_redactions(page)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to apply redactions: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Apply redactions on all pages.
    ///
    /// Performs true content-stripping redaction on all pages: parses content
    /// streams, removes text/images within redaction rectangles, draws overlays,
    /// and removes the redaction annotations.
    ///
    /// Raises:
    ///     RuntimeError: If the operation fails
    ///
    /// Example:
    ///     >>> doc.apply_all_redactions()
    ///     >>> doc.save("redacted.pdf")
    fn apply_all_redactions(&mut self) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .apply_all_redactions()
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to apply redactions: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Check if a page is marked for redaction application.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     bool: True if the page is marked for redaction application
    fn is_page_marked_for_redaction(&self, page: usize) -> bool {
        if let Some(ref editor) = self.editor {
            editor.is_page_marked_for_redaction(page)
        } else {
            false
        }
    }

    /// Unmark a page for redaction application.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    fn unmark_page_for_redaction(&mut self, page: usize) {
        if let Some(ref mut editor) = self.editor {
            editor.unmark_page_for_redaction(page);
        }
    }

    // ===== Image Repositioning & Resizing =====

    /// Get information about all images on a page.
    ///
    /// Returns a list of dictionaries with image information including
    /// name, position, size, and transformation matrix.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     list[dict]: List of image info dictionaries with keys:
    ///         - name (str): XObject name (e.g., "Im0")
    ///         - x (float): X position
    ///         - y (float): Y position
    ///         - width (float): Image width
    ///         - height (float): Image height
    ///         - matrix (tuple): 6-element transformation matrix (a, b, c, d, e, f)
    fn page_images(&mut self, page: usize, py: Python<'_>) -> PyResult<Py<PyAny>> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            let images = editor.get_page_images(page).map_err(|e| {
                PyRuntimeError::new_err(format!("Failed to get page images: {}", e))
            })?;

            let result = pyo3::types::PyList::empty(py);
            for img in images {
                let dict = pyo3::types::PyDict::new(py);
                dict.set_item("name", &img.name)?;
                dict.set_item("x", img.bounds[0])?;
                dict.set_item("y", img.bounds[1])?;
                dict.set_item("width", img.bounds[2])?;
                dict.set_item("height", img.bounds[3])?;
                dict.set_item(
                    "matrix",
                    (
                        img.matrix[0],
                        img.matrix[1],
                        img.matrix[2],
                        img.matrix[3],
                        img.matrix[4],
                        img.matrix[5],
                    ),
                )?;
                result.append(dict)?;
            }
            Ok(result.into())
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Reposition an image on a page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     image_name (str): Name of the image XObject (e.g., "Im0")
    ///     x (float): New X position
    ///     y (float): New Y position
    ///
    /// Raises:
    ///     RuntimeError: If the image is not found or operation fails
    fn reposition_image(&mut self, page: usize, image_name: &str, x: f32, y: f32) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .reposition_image(page, image_name, x, y)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to reposition image: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Resize an image on a page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     image_name (str): Name of the image XObject (e.g., "Im0")
    ///     width (float): New width
    ///     height (float): New height
    ///
    /// Raises:
    ///     RuntimeError: If the image is not found or operation fails
    fn resize_image(
        &mut self,
        page: usize,
        image_name: &str,
        width: f32,
        height: f32,
    ) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .resize_image(page, image_name, width, height)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to resize image: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Set both position and size of an image on a page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     image_name (str): Name of the image XObject (e.g., "Im0")
    ///     x (float): New X position
    ///     y (float): New Y position
    ///     width (float): New width
    ///     height (float): New height
    ///
    /// Raises:
    ///     RuntimeError: If the image is not found or operation fails
    fn set_image_bounds(
        &mut self,
        page: usize,
        image_name: &str,
        x: f32,
        y: f32,
        width: f32,
        height: f32,
    ) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .set_image_bounds(page, image_name, x, y, width, height)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to set image bounds: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Clear all image modifications for a specific page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    fn clear_image_modifications(&mut self, page: usize) {
        if let Some(ref mut editor) = self.editor {
            editor.clear_image_modifications(page);
        }
    }

    /// Check if a page has pending image modifications.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     bool: True if the page has pending image modifications
    fn has_image_modifications(&self, page: usize) -> bool {
        if let Some(ref editor) = self.editor {
            editor.has_image_modifications(page)
        } else {
            false
        }
    }

    // ========================================================================
    // Text Search
    // ========================================================================

    /// Search for text in the document.
    ///
    /// Searches all pages for matches of the given pattern (regex supported).
    ///
    /// Args:
    ///     pattern (str): Search pattern (regex or literal text)
    ///     case_insensitive (bool): Case insensitive search (default: False)
    ///     literal (bool): Treat pattern as literal text, not regex (default: False)
    ///     whole_word (bool): Match whole words only (default: False)
    ///     max_results (int): Maximum number of results, 0 = unlimited (default: 0)
    ///
    /// Returns:
    ///     list[dict]: List of search results, each containing:
    ///         - page (int): Page number (0-indexed)
    ///         - text (str): Matched text
    ///         - x (float): X position of match
    ///         - y (float): Y position of match
    ///         - width (float): Width of match bounding box
    ///         - height (float): Height of match bounding box
    ///
    /// Example:
    /// ```text
    /// >>> results = doc.search("hello")
    /// >>> for r in results:
    /// ...     print(f"Found '{r['text']}' on page {r['page']}")
    ///
    /// >>> # Case insensitive regex search
    /// >>> results = doc.search(r"\\d+\\.\\d+", case_insensitive=True)
    /// ```
    #[pyo3(signature = (pattern, case_insensitive=false, literal=false, whole_word=false, max_results=0))]
    fn search(
        &mut self,
        py: Python<'_>,
        pattern: &str,
        case_insensitive: bool,
        literal: bool,
        whole_word: bool,
        max_results: usize,
    ) -> PyResult<Py<PyAny>> {
        use crate::search::{SearchOptions, TextSearcher};

        let options = SearchOptions::new()
            .with_case_insensitive(case_insensitive)
            .with_literal(literal)
            .with_whole_word(whole_word)
            .with_max_results(max_results);

        let results = TextSearcher::search(&mut self.inner, pattern, &options)
            .map_err(|e| PyRuntimeError::new_err(format!("Search failed: {}", e)))?;

        let py_list = pyo3::types::PyList::empty(py);
        for result in results {
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("page", result.page)?;
            dict.set_item("text", &result.text)?;
            dict.set_item("x", result.bbox.x)?;
            dict.set_item("y", result.bbox.y)?;
            dict.set_item("width", result.bbox.width)?;
            dict.set_item("height", result.bbox.height)?;
            py_list.append(dict)?;
        }
        Ok(py_list.into())
    }

    /// Search for text on a specific page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     pattern (str): Search pattern (regex or literal text)
    ///     case_insensitive (bool): Case insensitive search (default: False)
    ///     literal (bool): Treat pattern as literal text, not regex (default: False)
    ///     whole_word (bool): Match whole words only (default: False)
    ///     max_results (int): Maximum number of results, 0 = unlimited (default: 0)
    ///
    /// Returns:
    ///     list[dict]: List of search results (same format as search())
    ///
    /// Example:
    ///     >>> results = doc.search_page(0, "hello")
    #[pyo3(signature = (page, pattern, case_insensitive=false, literal=false, whole_word=false, max_results=0))]
    fn search_page(
        &mut self,
        py: Python<'_>,
        page: usize,
        pattern: &str,
        case_insensitive: bool,
        literal: bool,
        whole_word: bool,
        max_results: usize,
    ) -> PyResult<Py<PyAny>> {
        use crate::search::{SearchOptions, TextSearcher};

        let options = SearchOptions::new()
            .with_case_insensitive(case_insensitive)
            .with_literal(literal)
            .with_whole_word(whole_word)
            .with_max_results(max_results)
            .with_page_range(page, page);

        let results = TextSearcher::search(&mut self.inner, pattern, &options)
            .map_err(|e| PyRuntimeError::new_err(format!("Search failed: {}", e)))?;

        let py_list = pyo3::types::PyList::empty(py);
        for result in results {
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("page", result.page)?;
            dict.set_item("text", &result.text)?;
            dict.set_item("x", result.bbox.x)?;
            dict.set_item("y", result.bbox.y)?;
            dict.set_item("width", result.bbox.width)?;
            dict.set_item("height", result.bbox.height)?;
            py_list.append(dict)?;
        }
        Ok(py_list.into())
    }

    // ========================================================================
    // Structured Extraction: Images, Spans, Paths
    // ========================================================================

    /// Extract image metadata from a page.
    ///
    /// Returns metadata for each image on the page (width, height, color space, etc.).
    /// Does NOT return raw image bytes — use `extract_images_to_files()` for that.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     list[dict]: List of image metadata dictionaries with keys:
    ///         - width (int): Image width in pixels
    ///         - height (int): Image height in pixels
    ///         - color_space (str): Color space (e.g., "DeviceRGB", "DeviceGray")
    ///         - bits_per_component (int): Bits per color component
    ///         - bbox (tuple | None): Bounding box as (x, y, width, height), or None
    ///
    /// Raises:
    ///     RuntimeError: If image extraction fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("sample.pdf")
    ///     >>> images = doc.extract_images(0)
    ///     >>> for img in images:
    ///     ...     print(f"{img['width']}x{img['height']} {img['color_space']}")
    #[pyo3(signature = (page, region=None))]
    fn extract_images(
        &mut self,
        py: Python<'_>,
        page: usize,
        region: Option<(f32, f32, f32, f32)>,
    ) -> PyResult<Py<PyAny>> {
        let images_result = if let Some((x, y, w, h)) = region {
            self.inner
                .extract_images_in_rect(page, crate::geometry::Rect::new(x, y, w, h))
        } else {
            self.inner.extract_images(page)
        };

        let images = images_result
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract images: {}", e)))?;

        let py_list = pyo3::types::PyList::empty(py);
        for img in &images {
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("width", img.width())?;
            dict.set_item("height", img.height())?;
            dict.set_item("color_space", format!("{:?}", img.color_space()))?;
            dict.set_item("bits_per_component", img.bits_per_component())?;
            if let Some(bbox) = img.bbox() {
                dict.set_item("bbox", (bbox.x, bbox.y, bbox.width, bbox.height))?;
            } else {
                dict.set_item("bbox", py.None())?;
            }
            dict.set_item("rotation", img.rotation_degrees())?;
            dict.set_item("matrix", img.matrix())?;
            py_list.append(dict)?;
        }
        Ok(py_list.into())
    }

    /// Extract tables from a page (v0.3.14).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     region (tuple, optional): (x, y, width, height) to filter by
    ///     table_settings (dict, optional): Dictionary of table detection settings
    ///         - horizontal_strategy: "text", "lines", or "both"
    ///         - vertical_strategy: "text", "lines", or "both"
    ///         - column_tolerance: f32
    ///         - row_tolerance: f32
    ///         - min_table_cells: int
    ///         - min_table_columns: int
    ///
    /// Returns:
    ///     list[dict]: List of detected tables
    #[pyo3(signature = (page, region=None, table_settings=None))]
    fn extract_tables(
        &mut self,
        py: Python<'_>,
        page: usize,
        region: Option<(f32, f32, f32, f32)>,
        table_settings: Option<Bound<'_, pyo3::types::PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        let config = table_settings_to_config(table_settings)?;

        let tables_result = if let Some((x, y, w, h)) = region {
            self.inner.extract_tables_in_rect_with_config(
                page,
                crate::geometry::Rect::new(x, y, w, h),
                config,
            )
        } else {
            self.inner.extract_tables_with_config(page, config)
        };

        let tables = tables_result
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract tables: {}", e)))?;

        let py_list = pyo3::types::PyList::empty(py);
        for table in &tables {
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("col_count", table.col_count)?;
            dict.set_item("row_count", table.rows.len())?;
            if let Some(bbox) = table.bbox {
                dict.set_item("bbox", (bbox.x, bbox.y, bbox.width, bbox.height))?;
            } else {
                dict.set_item("bbox", py.None())?;
            }
            dict.set_item("has_header", table.has_header)?;

            // Convert rows/cells to simple Python structures
            let rows_list = pyo3::types::PyList::empty(py);
            for row in &table.rows {
                let row_dict = pyo3::types::PyDict::new(py);
                row_dict.set_item("is_header", row.is_header)?;
                let cells_list = pyo3::types::PyList::empty(py);
                for cell in &row.cells {
                    let cell_dict = pyo3::types::PyDict::new(py);
                    cell_dict.set_item("text", &cell.text)?;
                    if let Some(bbox) = cell.bbox {
                        cell_dict.set_item("bbox", (bbox.x, bbox.y, bbox.width, bbox.height))?;
                    }
                    cells_list.append(cell_dict)?;
                }
                row_dict.set_item("cells", cells_list)?;
                rows_list.append(row_dict)?;
            }
            dict.set_item("rows", rows_list)?;

            py_list.append(dict)?;
        }
        Ok(py_list.into())
    }

    /// Extract tables using Camelot-style algorithms (lattice + stream).
    ///
    /// This uses the Nurminen table detection algorithm for stream mode
    /// and image-processing-based line detection for lattice mode.
    /// When flavor="auto", tries lattice first and falls back to stream.
    ///
    /// Args:
    ///     pages (str, optional): Page range like "1-5" or "all". Default "all".
    ///     flavor (str, optional): "lattice", "stream", or "auto". Default "auto".
    ///     line_scale (int, optional): Line detection scale. Default 15.
    ///     edge_tol (float, optional): Text edge alignment tolerance. Default 50.
    ///
    /// Returns:
    ///     list[dict]: Tables with keys: page, flavor, rows, cols, cells, accuracy, whitespace
    #[pyo3(signature = (pages=None, flavor=None, line_scale=None, edge_tol=None))]
    fn read_pdf(
        &mut self,
        py: Python<'_>,
        pages: Option<&str>,
        flavor: Option<&str>,
        line_scale: Option<u32>,
        edge_tol: Option<f64>,
    ) -> PyResult<Py<PyAny>> {
        use crate::tables::{self, ExtractConfig, Flavor};

        // Parse pages string to Vec<usize>
        let page_list = match pages {
            None | Some("all") => None,
            Some(s) => Some(parse_page_range(s, self.inner.page_count().unwrap_or(0))?),
        };

        let flavor_enum = match flavor {
            Some("lattice") => Some(Flavor::Lattice),
            Some("stream") => Some(Flavor::Stream),
            Some("auto") | None => None, // Auto-detect
            Some(other) => {
                return Err(PyRuntimeError::new_err(format!(
                    "Unknown flavor '{}'. Use 'lattice', 'stream', or 'auto'.",
                    other
                )));
            }
        };

        let mut config = ExtractConfig::default();
        if let Some(ls) = line_scale {
            config.line_scale = ls;
        }
        if let Some(et) = edge_tol {
            config.edge_tol = et;
        }
        if let Some(p) = page_list {
            config.pages = Some(p);
        }

        // Auto-flavor: try lattice first, fall back to stream if no tables found
        let all_tables = if let Some(f) = flavor_enum {
            config.flavor = f;
            tables::extract_tables(&mut self.inner, &config)
                .map_err(|e| PyRuntimeError::new_err(format!("Table extraction failed: {}", e)))?
        } else {
            // Auto: try lattice
            config.flavor = Flavor::Lattice;
            let lattice_tables = tables::extract_tables(&mut self.inner, &config)
                .map_err(|e| PyRuntimeError::new_err(format!("Lattice extraction failed: {}", e)))?;

            if !lattice_tables.is_empty() {
                lattice_tables
            } else {
                // Fall back to stream
                config.flavor = Flavor::Stream;
                tables::extract_tables(&mut self.inner, &config)
                    .map_err(|e| PyRuntimeError::new_err(format!("Stream extraction failed: {}", e)))?
            }
        };

        // Convert to Python dicts
        let py_list = pyo3::types::PyList::empty(py);
        for table in &all_tables {
            let dict = table_to_pydict(py, table)?;
            py_list.append(dict)?;
        }
        Ok(py_list.into())
    }

    /// Extract text spans from a page.
    ///
    /// Spans are groups of characters that share the same font and style.
    /// This is the recommended method for structured text extraction.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     list[TextSpan]: List of text spans with position and style info
    ///
    /// Raises:
    ///     RuntimeError: If span extraction fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("sample.pdf")
    ///     >>> spans = doc.extract_spans(0)
    ///     >>> for span in spans:
    ///     ...     print(f"'{span.text}' font={span.font_name} size={span.font_size}")
    #[pyo3(signature = (page, region=None))]
    fn extract_spans(
        &mut self,
        page: usize,
        region: Option<(f32, f32, f32, f32)>,
    ) -> PyResult<Vec<PyTextSpan>> {
        let spans_result = if let Some((x, y, w, h)) = region {
            self.inner.extract_spans_in_rect(
                page,
                crate::geometry::Rect::new(x, y, w, h),
                crate::layout::RectFilterMode::Intersects,
            )
        } else {
            self.inner.extract_spans(page)
        };

        spans_result
            .map(|spans| spans.into_iter().map(|s| PyTextSpan { inner: s }).collect())
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract spans: {}", e)))
    }

    /// DEBUG: Extract spans in content stream order (unsorted, no merge).
    fn extract_spans_unsorted(&mut self, page: usize) -> PyResult<Vec<PyTextSpan>> {
        self.inner.extract_spans_unsorted(page)
            .map(|spans| spans.into_iter().map(|s| PyTextSpan { inner: s }).collect())
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract unsorted spans: {}", e)))
    }

    /// Get the document outline (bookmarks / table of contents).
    ///
    /// Returns:
    ///     list[dict] | None: Outline tree as nested dicts, or None if no outline.
    ///         Each dict has keys:
    ///         - title (str): Bookmark title
    ///         - page (int | None): Target page index (0-based), or None
    ///         - children (list[dict]): Child bookmarks (same structure)
    ///
    /// Raises:
    ///     RuntimeError: If outline extraction fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("book.pdf")
    ///     >>> outline = doc.get_outline()
    ///     >>> if outline:
    ///     ...     for item in outline:
    ///     ...         print(f"{item['title']} -> page {item['page']}")
    fn get_outline(&mut self, py: Python<'_>) -> PyResult<Option<Py<PyAny>>> {
        let outline = self
            .inner
            .get_outline()
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to get outline: {}", e)))?;

        match outline {
            None => Ok(None),
            Some(items) => {
                let result = outline_items_to_py(py, &items)?;
                Ok(Some(result))
            },
        }
    }

    /// Get annotations from a page.
    ///
    /// Returns annotation metadata including type, position, content, and form field info.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     list[dict]: List of annotation dictionaries. Keys vary by type but may include:
    ///         - subtype (str): Annotation type (e.g., "Text", "Link", "Highlight")
    ///         - rect (tuple | None): Bounding rectangle as (x1, y1, x2, y2)
    ///         - contents (str | None): Text contents
    ///         - author (str | None): Author name
    ///         - creation_date (str | None): Creation date
    ///         - modification_date (str | None): Modification date
    ///         - subject (str | None): Subject
    ///         - color (tuple | None): Color as (r, g, b) tuple
    ///         - opacity (float | None): Opacity (0.0 to 1.0)
    ///         - field_type (str | None): Form field type if widget annotation
    ///         - field_name (str | None): Form field name
    ///         - field_value (str | None): Form field value
    ///         - action_uri (str | None): URI if link annotation
    ///
    /// Raises:
    ///     RuntimeError: If annotation extraction fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("annotated.pdf")
    ///     >>> annotations = doc.get_annotations(0)
    ///     >>> for ann in annotations:
    ///     ...     print(f"{ann['subtype']}: {ann.get('contents', '')}")
    fn get_annotations(&mut self, py: Python<'_>, page: usize) -> PyResult<Py<PyAny>> {
        let annotations = self
            .inner
            .get_annotations(page)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to get annotations: {}", e)))?;

        let py_list = pyo3::types::PyList::empty(py);
        for ann in &annotations {
            let dict = pyo3::types::PyDict::new(py);

            if let Some(ref subtype) = ann.subtype {
                dict.set_item("subtype", subtype)?;
            }
            if let Some(ref contents) = ann.contents {
                dict.set_item("contents", contents)?;
            }
            if let Some(rect) = ann.rect {
                dict.set_item("rect", (rect[0], rect[1], rect[2], rect[3]))?;
            }
            if let Some(ref author) = ann.author {
                dict.set_item("author", author)?;
            }
            if let Some(ref date) = ann.creation_date {
                dict.set_item("creation_date", date)?;
            }
            if let Some(ref date) = ann.modification_date {
                dict.set_item("modification_date", date)?;
            }
            if let Some(ref subject) = ann.subject {
                dict.set_item("subject", subject)?;
            }
            if let Some(ref color) = ann.color {
                if color.len() >= 3 {
                    dict.set_item("color", (color[0], color[1], color[2]))?;
                }
            }
            if let Some(opacity) = ann.opacity {
                dict.set_item("opacity", opacity)?;
            }
            if let Some(ref ft) = ann.field_type {
                dict.set_item("field_type", format!("{:?}", ft))?;
            }
            if let Some(ref name) = ann.field_name {
                dict.set_item("field_name", name)?;
            }
            if let Some(ref val) = ann.field_value {
                dict.set_item("field_value", val)?;
            }
            // Extract URI from link action
            if let Some(crate::annotations::LinkAction::Uri(ref uri)) = ann.action {
                dict.set_item("action_uri", uri)?;
            }

            py_list.append(dict)?;
        }
        Ok(py_list.into())
    }

    /// Remove annotations from a page by index.
    ///
    /// This removes annotations from the page's annotation list. Save the document
    /// afterwards to persist the changes. Useful for creating "clean" PDFs without
    /// markup annotations.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     indices (list[int]): Annotation indices to remove (0-based, as returned by get_annotations)
    ///
    /// Raises:
    ///     RuntimeError: If annotation removal fails
    fn remove_annotations(&mut self, page: usize, indices: Vec<usize>) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .remove_page_annotations(page, &indices)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to remove annotations: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Repair the document, fixing common PDF issues.
    ///
    /// Applies stream-length correction, page-tree validation, broken-reference
    /// fixing, and orphan-object removal.
    ///
    /// Returns:
    ///     dict: A dictionary with keys:
    ///         - ``xref_rebuilt`` (bool)
    ///         - ``stream_lengths_fixed`` (int)
    ///         - ``page_tree_rebuilt`` (bool)
    ///         - ``orphan_objects_removed`` (int)
    ///         - ``broken_references_fixed`` (int)
    ///         - ``total_fixes`` (int)
    ///
    /// Raises:
    ///     RuntimeError: If repair fails
    fn repair(&mut self, py: Python<'_>) -> PyResult<PyObject> {
        self.ensure_editor()?;
        if let Some(ref mut editor) = self.editor {
            let report = editor
                .repair()
                .map_err(|e| PyRuntimeError::new_err(format!("Repair failed: {}", e)))?;
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("xref_rebuilt", report.xref_rebuilt)?;
            dict.set_item("stream_lengths_fixed", report.stream_lengths_fixed)?;
            dict.set_item("page_tree_rebuilt", report.page_tree_rebuilt)?;
            dict.set_item("orphan_objects_removed", report.orphan_objects_removed)?;
            dict.set_item("broken_references_fixed", report.broken_references_fixed)?;
            dict.set_item("total_fixes", report.total_fixes())?;
            Ok(dict.into())
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Classify text blocks on a page.
    ///
    /// Returns a list of classified blocks with block type, text, bbox, and metadata.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     list[dict]: List of block dicts with keys: block_type, text, bbox, font_size, font_name, is_bold, confidence, header_level
    fn classify_blocks(&mut self, py: Python<'_>, page: usize) -> PyResult<PyObject> {
        let doc = &mut self.inner;
        let spans = doc.extract_spans_unsorted(page).map_err(|e| PyRuntimeError::new_err(format!("{}", e)))?;
        if spans.is_empty() {
            return Ok(pyo3::types::PyList::empty(py).into());
        }
        let info = doc.get_page_info(page).map_err(|e| PyRuntimeError::new_err(format!("{}", e)))?;
        let classifier = crate::extractors::block_classifier::BlockClassifier::new(
            info.media_box.width, info.media_box.height, &spans
        );
        let blocks = classifier.classify_spans(&spans);
        let list = pyo3::types::PyList::empty(py);
        for b in &blocks {
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("block_type", format!("{:?}", b.block_type))?;
            dict.set_item("text", &b.text)?;
            dict.set_item("bbox", (b.bbox.x, b.bbox.y, b.bbox.width, b.bbox.height))?;
            dict.set_item("font_size", b.font_size)?;
            dict.set_item("font_name", &b.font_name)?;
            dict.set_item("is_bold", b.is_bold)?;
            dict.set_item("confidence", b.confidence)?;
            dict.set_item("header_level", b.header_level)?;
            // Header validation details (when available)
            if let Some(ref hv) = b.header_validation {
                let hv_dict = header_validation_to_dict(py, hv)?;
                dict.set_item("header_validation", hv_dict)?;
            }
            list.append(dict)?;
        }
        Ok(list.into())
    }

    /// Validate whether text is a section header using heuristic analysis.
    ///
    /// Returns a dict with disposition ("Accept", "Reject", or "Escalate"):
    /// - Accept: Rust is confident this IS a header. Trust is_header directly.
    /// - Reject: Rust is confident this is NOT a header. Trust is_header directly.
    /// - Escalate: Ambiguous — send `features` dict to /assistant cascade for decision.
    ///
    /// The `features` dict contains numeric values suitable for sklearn/ONNX classifier input.
    /// Shadow labels should be logged to training data for future classifier training.
    ///
    /// Args:
    ///     text (str): The candidate header text
    ///     is_bold (bool): Whether the text is bold
    ///     font_size (float): Font size of the text
    ///     median_font_size (float): Median font size on the page (default 11.0)
    ///     max_font_size (float): Max font size on the page (default 18.0)
    ///
    /// Returns:
    ///     dict: Validation result with keys: is_header, confidence, disposition, level,
    ///           reasons, has_numbering, number_text, title_text, depth_level, features
    #[pyo3(signature = (text, is_bold=false, font_size=11.0, median_font_size=11.0, max_font_size=18.0))]
    fn validate_header_text(
        &self, py: Python<'_>, text: &str, is_bold: bool, font_size: f32,
        median_font_size: f32, max_font_size: f32,
    ) -> PyResult<PyObject> {
        let hv = crate::extractors::block_classifier::validate_header(
            text, is_bold, font_size, median_font_size, max_font_size,
        );
        let dict = header_validation_to_dict(py, &hv)?;
        Ok(dict.into())
    }

    /// Profile the document to detect domain, layout, and complexity.
    ///
    /// Returns:
    ///     dict: Profile with keys: page_count, domain, layout, complexity_score, is_scanned, etc.
    fn profile_document(&mut self, py: Python<'_>) -> PyResult<PyObject> {
        let doc = &mut self.inner;
        let profile = crate::extractors::document_profiler::profile_document(doc)
            .map_err(|e| PyRuntimeError::new_err(format!("{}", e)))?;
        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("page_count", profile.page_count)?;
        dict.set_item("domain", &profile.domain)?;
        dict.set_item("complexity_score", profile.complexity_score)?;
        dict.set_item("is_scanned", profile.is_scanned)?;
        dict.set_item("has_toc", profile.has_toc)?;
        dict.set_item("has_outline", profile.has_outline)?;
        dict.set_item("has_tables", profile.has_tables)?;
        dict.set_item("has_images", profile.has_images)?;
        dict.set_item("has_forms", profile.has_forms)?;
        dict.set_item("has_annotations", profile.has_annotations)?;
        dict.set_item("primary_font", &profile.primary_font)?;
        dict.set_item("primary_font_size", profile.primary_font_size)?;
        dict.set_item("title", &profile.title)?;
        dict.set_item("preset", &profile.preset)?;
        let layout = pyo3::types::PyDict::new(py);
        layout.set_item("columns", profile.layout.columns)?;
        layout.set_item("has_header", profile.layout.has_header)?;
        layout.set_item("has_footer", profile.layout.has_footer)?;
        layout.set_item("has_page_numbers", profile.layout.has_page_numbers)?;
        layout.set_item("avg_chars_per_page", profile.layout.avg_chars_per_page)?;
        layout.set_item("page_width", profile.layout.page_width)?;
        layout.set_item("page_height", profile.layout.page_height)?;
        layout.set_item("orientation", &profile.layout.orientation)?;
        dict.set_item("layout", layout)?;
        Ok(dict.into())
    }

    /// Build a section hierarchy from document headers.
    ///
    /// Returns:
    ///     dict: Section tree with keys: sections (list), total_sections, max_depth
    fn get_section_hierarchy(&mut self, py: Python<'_>) -> PyResult<PyObject> {
        let doc = &mut self.inner;
        let tree = crate::extractors::section_hierarchy::build_section_hierarchy(doc)
            .map_err(|e| PyRuntimeError::new_err(format!("{}", e)))?;
        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("total_sections", tree.total_sections)?;
        dict.set_item("max_depth", tree.max_depth)?;
        let flat = tree.to_flat_list();
        let sections = pyo3::types::PyList::empty(py);
        for (level, title, page) in &flat {
            let s = pyo3::types::PyDict::new(py);
            s.set_item("level", level)?;
            s.set_item("title", title)?;
            s.set_item("page", page)?;
            sections.append(s)?;
        }
        dict.set_item("sections", sections)?;
        Ok(dict.into())
    }

    /// Build flat sections from all pages (S04 section builder replacement).
    ///
    /// Classifies blocks on every page, then groups them into sections where
    /// each header starts a new section and subsequent body blocks are aggregated.
    /// Respects HeaderDisposition: Reject headers are treated as body text,
    /// Escalate headers start sections but are flagged for cascade review.
    ///
    /// Returns:
    ///     dict: {sections: list[dict], order_validation: list[dict]}
    ///     Each section dict has: title, display_title, level, section_number,
    ///     page_start, page_end, bbox, content, block_count, section_hash,
    ///     parent_idx, header_disposition
    fn build_flat_sections(&mut self, py: Python<'_>) -> PyResult<PyObject> {
        let doc = &mut self.inner;
        let page_count = doc.page_count().map_err(|e| PyRuntimeError::new_err(format!("{}", e)))?;

        // Classify blocks for all pages
        let mut page_blocks: Vec<(usize, Vec<crate::extractors::block_classifier::ClassifiedBlock>)> = Vec::new();
        for pg in 0..page_count {
            let spans = doc.extract_spans_unsorted(pg).unwrap_or_default();
            if spans.is_empty() {
                continue;
            }
            let (width, height) = doc.get_page_info(pg)
                .ok()
                .map(|info| (info.media_box.width, info.media_box.height))
                .unwrap_or((612.0, 792.0));
            let classifier = crate::extractors::block_classifier::BlockClassifier::new(width, height, &spans);
            let blocks = classifier.classify_spans(&spans);
            page_blocks.push((pg, blocks));
        }

        let sections = crate::extractors::section_hierarchy::build_flat_sections(&page_blocks);
        let order = crate::extractors::section_hierarchy::validate_section_order(&sections, &page_blocks);

        let result = pyo3::types::PyDict::new(py);

        // Serialize sections
        let sec_list = pyo3::types::PyList::empty(py);
        for sec in &sections {
            let d = pyo3::types::PyDict::new(py);
            d.set_item("title", &sec.title)?;
            d.set_item("display_title", &sec.display_title)?;
            d.set_item("level", sec.level)?;
            d.set_item("section_number", &sec.section_number)?;
            d.set_item("page_start", sec.page_start)?;
            d.set_item("page_end", sec.page_end)?;
            d.set_item("bbox", (sec.bbox.x, sec.bbox.y, sec.bbox.width, sec.bbox.height))?;
            d.set_item("content", &sec.content)?;
            d.set_item("block_count", sec.block_count)?;
            d.set_item("section_hash", &sec.section_hash)?;
            d.set_item("parent_idx", sec.parent_idx)?;
            d.set_item("header_disposition", sec.header_disposition.as_ref().map(|hd| match hd {
                crate::extractors::block_classifier::HeaderDisposition::Accept => "Accept",
                crate::extractors::block_classifier::HeaderDisposition::Reject => "Reject",
                crate::extractors::block_classifier::HeaderDisposition::Escalate => "Escalate",
            }))?;
            sec_list.append(d)?;
        }
        result.set_item("sections", sec_list)?;

        // Serialize order validation
        let order_list = pyo3::types::PyList::empty(py);
        for (idx, ok) in &order {
            let d = pyo3::types::PyDict::new(py);
            d.set_item("section_index", idx)?;
            d.set_item("order_ok", ok)?;
            order_list.append(d)?;
        }
        result.set_item("order_validation", order_list)?;

        Ok(result.into())
    }

    /// Assemble content from sections, tables, and figures (S07 replacement).
    ///
    /// Takes pre-built sections/blocks/tables/figures and performs:
    /// - Spatial assignment of tables/figures to sections
    /// - Overlap suppression (text blocks under tables/figures removed)
    /// - Paragraph merging with asset interleaving in reading order
    ///
    /// Args:
    ///     sections (list[dict]): Section dicts with keys: id, title, page_start, page_end, parent_id
    ///     blocks (list[dict]): Block dicts with keys: id, page, bbox, text, block_type, section_id
    ///     tables (list[dict]): Table dicts with keys: id, page, bbox, csv_data, section_id, sort_order, etc.
    ///     figures (list[dict]): Figure dicts with keys: id, page, bbox, image_path, section_id, sort_order, etc.
    ///     page_width (float): Page width for sort order calculation (default 612.0)
    ///
    /// Returns:
    ///     dict: {sections, blocks, tables, figures, merged_content}
    #[pyo3(signature = (sections, blocks, tables, figures, page_width=612.0))]
    fn assemble_content(
        &self, py: Python<'_>,
        sections: Vec<pyo3::Bound<'_, pyo3::types::PyDict>>,
        blocks: Vec<pyo3::Bound<'_, pyo3::types::PyDict>>,
        tables: Vec<pyo3::Bound<'_, pyo3::types::PyDict>>,
        figures: Vec<pyo3::Bound<'_, pyo3::types::PyDict>>,
        page_width: f32,
    ) -> PyResult<PyObject> {
        use crate::extractors::content_assembler::*;

        // Convert Python dicts to Rust structs
        let rust_sections: Vec<ContentSection> = sections.iter().map(|d| {
            ContentSection {
                id: d.get_item("id").ok().flatten().map(|v| v.extract::<String>().unwrap_or_default()).unwrap_or_default(),
                title: d.get_item("title").ok().flatten().map(|v| v.extract::<String>().unwrap_or_default()).unwrap_or_default(),
                page_start: d.get_item("page_start").ok().flatten().map(|v| v.extract::<usize>().unwrap_or(0)).unwrap_or(0),
                page_end: d.get_item("page_end").ok().flatten().map(|v| v.extract::<usize>().unwrap_or(0)).unwrap_or(0),
                parent_id: d.get_item("parent_id").ok().flatten().and_then(|v| v.extract::<String>().ok()),
            }
        }).collect();

        let rust_blocks: Vec<ContentBlock> = blocks.iter().map(|d| {
            let bbox_tuple: (f32, f32, f32, f32) = d.get_item("bbox").ok().flatten()
                .and_then(|v| v.extract().ok()).unwrap_or((0.0, 0.0, 0.0, 0.0));
            ContentBlock {
                id: d.get_item("id").ok().flatten().map(|v| v.extract::<String>().unwrap_or_default()).unwrap_or_default(),
                page: d.get_item("page").ok().flatten().map(|v| v.extract::<usize>().unwrap_or(0)).unwrap_or(0),
                bbox: crate::geometry::Rect::new(bbox_tuple.0, bbox_tuple.1, bbox_tuple.2, bbox_tuple.3),
                text: d.get_item("text").ok().flatten().map(|v| v.extract::<String>().unwrap_or_default()).unwrap_or_default(),
                block_type: d.get_item("block_type").ok().flatten().map(|v| v.extract::<String>().unwrap_or_default()).unwrap_or_else(|| "Text".to_string()),
                section_id: d.get_item("section_id").ok().flatten().and_then(|v| v.extract::<String>().ok()),
                is_equation: d.get_item("is_equation").ok().flatten().map(|v| v.extract::<bool>().unwrap_or(false)).unwrap_or(false),
                latex_content: d.get_item("latex_content").ok().flatten().and_then(|v| v.extract::<String>().ok()),
            }
        }).collect();

        let rust_tables: Vec<ContentTable> = tables.iter().map(|d| {
            let bbox_tuple: (f32, f32, f32, f32) = d.get_item("bbox").ok().flatten()
                .and_then(|v| v.extract().ok()).unwrap_or((0.0, 0.0, 0.0, 0.0));
            ContentTable {
                id: d.get_item("id").ok().flatten().map(|v| v.extract::<String>().unwrap_or_default()).unwrap_or_default(),
                page: d.get_item("page").ok().flatten().map(|v| v.extract::<usize>().unwrap_or(0)).unwrap_or(0),
                bbox: crate::geometry::Rect::new(bbox_tuple.0, bbox_tuple.1, bbox_tuple.2, bbox_tuple.3),
                csv_data: d.get_item("csv_data").ok().flatten().map(|v| v.extract::<String>().unwrap_or_default()).unwrap_or_default(),
                html_data: d.get_item("html_data").ok().flatten().map(|v| v.extract::<String>().unwrap_or_default()).unwrap_or_default(),
                section_id: d.get_item("section_id").ok().flatten().and_then(|v| v.extract::<String>().ok()),
                sort_order: d.get_item("sort_order").ok().flatten().map(|v| v.extract::<i64>().unwrap_or(0)).unwrap_or(0),
                llm_title: d.get_item("llm_title").ok().flatten().and_then(|v| v.extract::<String>().ok()),
                llm_description: d.get_item("llm_description").ok().flatten().and_then(|v| v.extract::<String>().ok()),
                image_path: d.get_item("image_path").ok().flatten().and_then(|v| v.extract::<String>().ok()),
            }
        }).collect();

        let rust_figures: Vec<ContentFigure> = figures.iter().map(|d| {
            let bbox_tuple: (f32, f32, f32, f32) = d.get_item("bbox").ok().flatten()
                .and_then(|v| v.extract().ok()).unwrap_or((0.0, 0.0, 0.0, 0.0));
            ContentFigure {
                id: d.get_item("id").ok().flatten().map(|v| v.extract::<String>().unwrap_or_default()).unwrap_or_default(),
                page: d.get_item("page").ok().flatten().map(|v| v.extract::<usize>().unwrap_or(0)).unwrap_or(0),
                bbox: crate::geometry::Rect::new(bbox_tuple.0, bbox_tuple.1, bbox_tuple.2, bbox_tuple.3),
                image_path: d.get_item("image_path").ok().flatten().map(|v| v.extract::<String>().unwrap_or_default()).unwrap_or_default(),
                section_id: d.get_item("section_id").ok().flatten().and_then(|v| v.extract::<String>().ok()),
                sort_order: d.get_item("sort_order").ok().flatten().map(|v| v.extract::<i64>().unwrap_or(0)).unwrap_or(0),
                llm_title: d.get_item("llm_title").ok().flatten().and_then(|v| v.extract::<String>().ok()),
                llm_description: d.get_item("llm_description").ok().flatten().and_then(|v| v.extract::<String>().ok()),
            }
        }).collect();

        let assembled = assemble_content(rust_sections, rust_blocks, rust_tables, rust_figures, page_width);

        // Serialize to Python dict
        let result = pyo3::types::PyDict::new(py);

        // Sections
        let sec_list = pyo3::types::PyList::empty(py);
        for s in &assembled.sections {
            let d = pyo3::types::PyDict::new(py);
            d.set_item("id", &s.id)?;
            d.set_item("title", &s.title)?;
            d.set_item("page_start", s.page_start)?;
            d.set_item("page_end", s.page_end)?;
            d.set_item("parent_id", s.parent_id.as_deref())?;
            sec_list.append(d)?;
        }
        result.set_item("sections", sec_list)?;

        // Tables (with section_id now assigned)
        let tbl_list = pyo3::types::PyList::empty(py);
        for t in &assembled.tables {
            let d = pyo3::types::PyDict::new(py);
            d.set_item("id", &t.id)?;
            d.set_item("page", t.page)?;
            d.set_item("bbox", (t.bbox.x, t.bbox.y, t.bbox.width, t.bbox.height))?;
            d.set_item("section_id", t.section_id.as_deref())?;
            d.set_item("sort_order", t.sort_order)?;
            d.set_item("csv_data", &t.csv_data)?;
            d.set_item("html_data", &t.html_data)?;
            d.set_item("llm_title", t.llm_title.as_deref())?;
            d.set_item("llm_description", t.llm_description.as_deref())?;
            d.set_item("image_path", t.image_path.as_deref())?;
            tbl_list.append(d)?;
        }
        result.set_item("tables", tbl_list)?;

        // Figures (with section_id now assigned)
        let fig_list = pyo3::types::PyList::empty(py);
        for f in &assembled.figures {
            let d = pyo3::types::PyDict::new(py);
            d.set_item("id", &f.id)?;
            d.set_item("page", f.page)?;
            d.set_item("bbox", (f.bbox.x, f.bbox.y, f.bbox.width, f.bbox.height))?;
            d.set_item("section_id", f.section_id.as_deref())?;
            d.set_item("sort_order", f.sort_order)?;
            d.set_item("image_path", &f.image_path)?;
            d.set_item("llm_title", f.llm_title.as_deref())?;
            d.set_item("llm_description", f.llm_description.as_deref())?;
            fig_list.append(d)?;
        }
        result.set_item("figures", fig_list)?;

        // Merged content
        let mc_list = pyo3::types::PyList::empty(py);
        for m in &assembled.merged_content {
            let d = pyo3::types::PyDict::new(py);
            d.set_item("id", &m.id)?;
            d.set_item("section_id", &m.section_id)?;
            d.set_item("page", m.page)?;
            d.set_item("type", &m.content_type)?;
            d.set_item("content", &m.content)?;
            d.set_item("asset_id", m.asset_id.as_deref())?;
            d.set_item("sort_order", m.sort_order)?;
            d.set_item("bbox", (m.bbox.x, m.bbox.y, m.bbox.width, m.bbox.height))?;
            mc_list.append(d)?;
        }
        result.set_item("merged_content", mc_list)?;

        Ok(result.into())
    }

    /// Run full extraction prediction — bundles profiling, block classification,
    /// section hierarchy, and engineering detection. Returns a single dict with
    /// all analysis results and a recommended extraction strategy.
    /// This is the primary input for Shadow-LEGO cascade decision points.
    fn predict_extraction(&mut self, py: Python<'_>) -> PyResult<PyObject> {
        let doc = &mut self.inner;
        let prediction = crate::extractors::prediction::predict_extraction(doc)
            .map_err(|e| PyRuntimeError::new_err(format!("{}", e)))?;

        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("recommended_strategy", &prediction.recommended_strategy)?;

        // Profile
        let profile = pyo3::types::PyDict::new(py);
        profile.set_item("domain", &prediction.profile.domain)?;
        profile.set_item("complexity_score", prediction.profile.complexity_score)?;
        profile.set_item("is_scanned", prediction.profile.is_scanned)?;
        profile.set_item("has_tables", prediction.profile.has_tables)?;
        profile.set_item("has_images", prediction.profile.has_images)?;
        profile.set_item("has_toc", prediction.profile.has_toc)?;
        profile.set_item("has_outline", prediction.profile.has_outline)?;
        profile.set_item("preset", &prediction.profile.preset)?;
        profile.set_item("primary_font", &prediction.profile.primary_font)?;
        profile.set_item("primary_font_size", prediction.profile.primary_font_size)?;
        profile.set_item("columns", prediction.profile.layout.columns)?;
        profile.set_item("page_count", prediction.profile.page_count)?;
        dict.set_item("profile", profile)?;

        // Engineering
        let eng = pyo3::types::PyDict::new(py);
        eng.set_item("is_engineering", prediction.engineering.is_engineering)?;
        eng.set_item("doc_subtype", &prediction.engineering.doc_subtype)?;
        eng.set_item("drawing_number", prediction.engineering.drawing_number.as_deref())?;
        eng.set_item("revision", prediction.engineering.revision.as_deref())?;
        eng.set_item("distribution_statement", prediction.engineering.distribution_statement.as_deref())?;
        dict.set_item("engineering", eng)?;

        // Sections summary
        let sections = pyo3::types::PyDict::new(py);
        sections.set_item("total_sections", prediction.sections.total_sections)?;
        sections.set_item("max_depth", prediction.sections.max_depth)?;
        dict.set_item("sections", sections)?;

        // Page block summaries
        let pages = pyo3::types::PyList::empty(py);
        for pbs in &prediction.page_block_summary {
            let p = pyo3::types::PyDict::new(py);
            p.set_item("page", pbs.page)?;
            p.set_item("total_blocks", pbs.total_blocks)?;
            p.set_item("title_count", pbs.title_count)?;
            p.set_item("body_count", pbs.body_count)?;
            p.set_item("list_count", pbs.list_count)?;
            p.set_item("table_count", pbs.table_count)?;
            p.set_item("has_header", pbs.has_header)?;
            p.set_item("has_footer", pbs.has_footer)?;
            pages.append(p)?;
        }
        dict.set_item("page_summaries", pages)?;

        Ok(dict.into())
    }

    /// Detect engineering/defense document features.
    ///
    /// Returns a dict with: is_engineering, doc_subtype, elements, drawing_number,
    /// revision, cage_code, distribution_statement.
    fn detect_engineering_features(&mut self, py: Python<'_>) -> PyResult<PyObject> {
        let doc = &mut self.inner;
        let profile = crate::extractors::engineering::detect_engineering_features(doc)
            .map_err(|e| PyRuntimeError::new_err(format!("{}", e)))?;
        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("is_engineering", profile.is_engineering)?;
        dict.set_item("doc_subtype", &profile.doc_subtype)?;
        dict.set_item("drawing_number", profile.drawing_number.as_deref())?;
        dict.set_item("revision", profile.revision.as_deref())?;
        dict.set_item("cage_code", profile.cage_code.as_deref())?;
        dict.set_item("distribution_statement", profile.distribution_statement.as_deref())?;
        let elements = pyo3::types::PyList::empty(py);
        for elem in &profile.elements {
            let e = pyo3::types::PyDict::new(py);
            e.set_item("type", elem.element_type.as_str())?;
            e.set_item("page", elem.page)?;
            e.set_item("confidence", elem.confidence)?;
            e.set_item("text", &elem.text)?;
            e.set_item("bbox", (elem.bbox.x, elem.bbox.y, elem.bbox.width, elem.bbox.height))?;
            elements.append(e)?;
        }
        dict.set_item("elements", elements)?;
        Ok(dict.into())
    }

    /// Run the full document extraction pipeline.
    ///
    /// Returns a comprehensive dict with profile, pages (blocks + text),
    /// figures, sections, engineering features, running headers/footers,
    /// and recommended extraction strategy.
    ///
    /// Args:
    ///     detect_figures: Whether to detect figures (default: True)
    ///     detect_engineering: Whether to detect engineering features (default: True)
    ///     normalize_text: Whether to normalize extracted text (default: True)
    ///     build_sections: Whether to build section hierarchy (default: True)
    ///     max_pages: Maximum pages to process, 0 = all (default: 0)
    ///
    /// Returns:
    ///     dict with keys: profile, pages, figures, sections, engineering,
    ///     running_headers, running_footers, recommended_strategy, page_count
    #[pyo3(signature = (detect_figures=true, detect_engineering=true, normalize_text=true, build_sections=true, max_pages=0))]
    fn extract_document(
        &mut self,
        py: Python<'_>,
        detect_figures: bool,
        detect_engineering: bool,
        normalize_text: bool,
        build_sections: bool,
        max_pages: usize,
    ) -> PyResult<PyObject> {
        use crate::extractors::document_extractor::{extract_document_with_config, ExtractionConfig};

        let config = ExtractionConfig {
            detect_figures,
            detect_engineering,
            normalize_text,
            build_sections,
            max_pages,
        };

        let doc = &mut self.inner;
        let result = extract_document_with_config(doc, &config)
            .map_err(|e| PyRuntimeError::new_err(format!("{}", e)))?;

        let dict = pyo3::types::PyDict::new(py);

        // Profile
        let profile = pyo3::types::PyDict::new(py);
        profile.set_item("domain", &result.profile.domain)?;
        profile.set_item("complexity_score", result.profile.complexity_score)?;
        profile.set_item("is_scanned", result.profile.is_scanned)?;
        profile.set_item("has_tables", result.profile.has_tables)?;
        profile.set_item("has_images", result.profile.has_images)?;
        profile.set_item("has_toc", result.profile.has_toc)?;
        profile.set_item("column_count", result.profile.column_count)?;
        dict.set_item("profile", profile)?;

        // Pages
        let pages_list = pyo3::types::PyList::empty(py);
        for page_result in &result.pages {
            let page_dict = pyo3::types::PyDict::new(py);
            page_dict.set_item("page", page_result.page)?;
            page_dict.set_item("text", &page_result.text)?;

            let blocks_list = pyo3::types::PyList::empty(py);
            for block in &page_result.blocks {
                let b = pyo3::types::PyDict::new(py);
                b.set_item("block_type", &block.block_type)?;
                b.set_item("text", &block.text)?;
                b.set_item("bbox", (block.bbox[0], block.bbox[1], block.bbox[2], block.bbox[3]))?;
                b.set_item("font_size", block.font_size)?;
                b.set_item("font_name", &block.font_name)?;
                b.set_item("is_bold", block.is_bold)?;
                b.set_item("confidence", block.confidence)?;
                b.set_item("header_level", block.header_level)?;
                b.set_item("paragraph_id", block.paragraph_id)?;
                blocks_list.append(b)?;
            }
            page_dict.set_item("blocks", blocks_list)?;
            pages_list.append(page_dict)?;
        }
        dict.set_item("pages", pages_list)?;

        // Figures
        let figures_list = pyo3::types::PyList::empty(py);
        for fig in &result.figures {
            let f = pyo3::types::PyDict::new(py);
            f.set_item("page", fig.page)?;
            f.set_item("bbox", (fig.bbox[0], fig.bbox[1], fig.bbox[2], fig.bbox[3]))?;
            f.set_item("caption", fig.caption.as_deref())?;
            f.set_item("caption_number", fig.caption_number)?;
            f.set_item("context_above", &fig.context_above)?;
            f.set_item("context_below", &fig.context_below)?;
            f.set_item("section_title", fig.section_title.as_deref())?;
            figures_list.append(f)?;
        }
        dict.set_item("figures", figures_list)?;

        // Sections
        let sections_list = pyo3::types::PyList::empty(py);
        for sec in &result.sections {
            let s = pyo3::types::PyDict::new(py);
            s.set_item("title", &sec.title)?;
            s.set_item("level", sec.level)?;
            s.set_item("page", sec.page)?;
            s.set_item("numbering", sec.numbering.as_deref())?;
            sections_list.append(s)?;
        }
        dict.set_item("sections", sections_list)?;

        // Engineering
        if let Some(eng) = &result.engineering {
            let e = pyo3::types::PyDict::new(py);
            e.set_item("has_title_block", eng.has_title_block)?;
            e.set_item("has_revision_table", eng.has_revision_table)?;
            e.set_item("has_drawing_border", eng.has_drawing_border)?;
            e.set_item("security_markings", eng.security_markings.clone())?;
            e.set_item("document_number", eng.document_number.as_deref())?;
            dict.set_item("engineering", e)?;
        } else {
            dict.set_item("engineering", py.None())?;
        }

        // Running headers/footers
        dict.set_item("running_headers", result.running_headers.clone())?;
        dict.set_item("running_footers", result.running_footers.clone())?;

        // Strategy and page count
        dict.set_item("recommended_strategy", &result.recommended_strategy)?;
        dict.set_item("page_count", result.page_count)?;

        Ok(dict.into())
    }

    /// Get link annotations from a page.
    ///
    /// Returns a list of (index, dict) tuples for all Link annotations on the page.
    /// The index can be used with update_link_uri(), update_link_destination(), etc.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     list[tuple[int, dict]]: List of (annot_index, annotation_dict) tuples
    fn get_links(&mut self, page: usize) -> PyResult<Vec<(usize, PyObject)>> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            let links = editor
                .get_links(page)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to get links: {}", e)))?;
            Python::with_gil(|py| {
                let result: Vec<(usize, PyObject)> = links
                    .into_iter()
                    .map(|(idx, annot)| {
                        let dict = PyDict::new(py);
                        dict.set_item("subtype", &annot.subtype).ok();
                        if let Some(rect) = annot.rect {
                            dict.set_item("rect", (rect[0], rect[1], rect[2], rect[3])).ok();
                        }
                        // Extract URI from action if it's a URI action
                        if let Some(ref action) = annot.action {
                            match action {
                                crate::annotations::LinkAction::Uri(uri) => {
                                    dict.set_item("uri", uri).ok();
                                }
                                _ => {}
                            }
                        }
                        if let Some(ref contents) = annot.contents {
                            dict.set_item("contents", contents).ok();
                        }
                        (idx, dict.into())
                    })
                    .collect();
                Ok(result)
            })
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Update a link annotation's URI.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     annot_index (int): Annotation index from get_links()
    ///     new_uri (str): New target URI
    fn update_link_uri(&mut self, page: usize, annot_index: usize, new_uri: &str) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .update_link_uri(page, annot_index, new_uri)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to update link: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Update a link annotation to navigate to a specific page.
    ///
    /// Args:
    ///     page (int): Page index (0-based) where the link is
    ///     annot_index (int): Annotation index from get_links()
    ///     target_page (int): Target page number (0-based)
    fn update_link_destination(&mut self, page: usize, annot_index: usize, target_page: usize) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .update_link_destination(page, annot_index, target_page)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to update link destination: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Update a link annotation to navigate to a named destination.
    ///
    /// Args:
    ///     page (int): Page index (0-based) where the link is
    ///     annot_index (int): Annotation index from get_links()
    ///     dest_name (str): Named destination string
    fn update_link_named_destination(&mut self, page: usize, annot_index: usize, dest_name: &str) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .update_link_named_destination(page, annot_index, dest_name)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to update link destination: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Delete a link annotation from a page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     annot_index (int): Annotation index from get_links()
    fn delete_link(&mut self, page: usize, annot_index: usize) -> PyResult<()> {
        if self.editor.is_none() {
            let editor = RustDocumentEditor::open(&self.path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to open editor: {}", e)))?;
            self.editor = Some(editor);
        }
        if let Some(ref mut editor) = self.editor {
            editor
                .delete_link(page, annot_index)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to delete link: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Detect the type of a page for OCR purposes.
    ///
    /// Returns "native" (has extractable text), "scanned" (full-page scan, needs OCR),
    /// or "hybrid" (mix of text and scanned images).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     str: One of "native", "scanned", or "hybrid"
    ///
    /// Raises:
    ///     RuntimeError: If OCR feature is not enabled or detection fails
    #[cfg(feature = "ocr")]
    fn detect_page_type(&mut self, page: usize) -> PyResult<String> {
        let page_type = crate::ocr::detect_page_type(&mut self.inner, page)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to detect page type: {}", e)))?;
        Ok(match page_type {
            crate::ocr::PageType::NativeText => "native".to_string(),
            crate::ocr::PageType::ScannedPage => "scanned".to_string(),
            crate::ocr::PageType::HybridPage => "hybrid".to_string(),
        })
    }

    /// Extract vector paths (lines, curves, shapes) from a page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     list[dict]: List of path dictionaries with keys:
    ///         - bbox (tuple): Bounding box as (x, y, width, height)
    ///         - stroke_width (float): Stroke line width
    ///         - stroke_color (tuple | None): Stroke color as (r, g, b), or None
    ///         - fill_color (tuple | None): Fill color as (r, g, b), or None
    ///         - line_cap (str): Line cap style ("butt", "round", "square")
    ///         - line_join (str): Line join style ("miter", "round", "bevel")
    ///         - operations_count (int): Number of path operations
    ///
    /// Raises:
    ///     RuntimeError: If path extraction fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("vector.pdf")
    ///     >>> paths = doc.extract_paths(0)
    ///     >>> for p in paths:
    ///     ...     print(f"Path at {p['bbox']}, stroke={p['stroke_color']}")
    /// Extract vector paths from a page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     region (tuple, optional): (x, y, width, height) to filter by
    ///
    /// Returns:
    ///     list[dict]: List of paths with bbox, stroke, fill, and styling
    ///
    /// Example:
    ///     >>> doc = PdfDocument("vector.pdf")
    ///     >>> paths = doc.extract_paths(0)
    ///     >>> for p in paths:
    ///     ...     print(f"Path at {p['bbox']}, stroke={p['stroke_color']}")
    #[pyo3(signature = (page, region=None))]
    fn extract_paths(
        &mut self,
        py: Python<'_>,
        page: usize,
        region: Option<(f32, f32, f32, f32)>,
    ) -> PyResult<Py<PyAny>> {
        let paths = if let Some((x, y, w, h)) = region {
            self.inner
                .extract_paths_in_rect(page, crate::geometry::Rect::new(x, y, w, h))
        } else {
            self.inner.extract_paths(page)
        }
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract paths: {}", e)))?;

        let py_list = pyo3::types::PyList::empty(py);
        for path in &paths {
            py_list.append(path_to_py_dict(py, path)?)?;
        }
        Ok(py_list.into())
    }

    /// Extract only rectangles from a page (v0.3.14).
    ///
    /// Identifies paths that form axis-aligned rectangles (simple 're' ops
    /// or closed paths with 4 perpendicular lines).
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     region (tuple, optional): (x, y, width, height) to filter by
    ///
    /// Returns:
    ///     list[dict]: List of rectangles
    #[pyo3(signature = (page, region=None))]
    fn extract_rects(
        &mut self,
        py: Python<'_>,
        page: usize,
        region: Option<(f32, f32, f32, f32)>,
    ) -> PyResult<Py<PyAny>> {
        let paths = if let Some((x, y, w, h)) = region {
            self.inner
                .extract_rects_in_rect(page, crate::geometry::Rect::new(x, y, w, h))
        } else {
            self.inner.extract_rects(page)
        }
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract rects: {}", e)))?;

        let py_list = pyo3::types::PyList::empty(py);
        for path in &paths {
            py_list.append(path_to_py_dict(py, path)?)?;
        }
        Ok(py_list.into())
    }

    /// Extract only straight lines from a page (v0.3.14).
    ///
    /// Identifies paths that form a single straight line segment.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     region (tuple, optional): (x, y, width, height) to filter by
    ///
    /// Returns:
    ///     list[dict]: List of lines
    #[pyo3(signature = (page, region=None))]
    fn extract_lines(
        &mut self,
        py: Python<'_>,
        page: usize,
        region: Option<(f32, f32, f32, f32)>,
    ) -> PyResult<Py<PyAny>> {
        let paths = if let Some((x, y, w, h)) = region {
            self.inner
                .extract_lines_in_rect(page, crate::geometry::Rect::new(x, y, w, h))
        } else {
            self.inner.extract_lines(page)
        }
        .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract lines: {}", e)))?;

        let py_list = pyo3::types::PyList::empty(py);
        for path in &paths {
            py_list.append(path_to_py_dict(py, path)?)?;
        }
        Ok(py_list.into())
    }

    // ========================================================================
    // OCR Text Extraction (feature-gated)
    // ========================================================================

    /// Extract text from a page using OCR (optical character recognition).
    ///
    /// Falls back to native text extraction when the page has digital text.
    /// Requires the `ocr` feature to be enabled at build time.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     engine (OcrEngine | None): OCR engine instance. Required for scanned pages.
    ///
    /// Returns:
    ///     str: Extracted text from the page
    ///
    /// Raises:
    ///     RuntimeError: If text extraction fails
    ///
    /// Example:
    ///     >>> engine = OcrEngine("det.onnx", "rec.onnx", "dict.txt")
    ///     >>> text = doc.extract_text_ocr(0, engine)
    #[pyo3(signature = (page, engine=None))]
    fn extract_text_ocr(
        &mut self,
        _py: Python<'_>,
        page: usize,
        engine: Option<Bound<'_, PyAny>>,
    ) -> PyResult<String> {
        #[cfg(feature = "ocr")]
        {
            let ocr_engine = if let Some(eng) = engine {
                Some(eng.extract::<&PyOcrEngine>()?)
            } else {
                None
            };
            let engine_inner = ocr_engine.map(|e| &e.inner);
            let options = crate::ocr::OcrExtractOptions::default();
            self.inner
                .extract_text_with_ocr(page, engine_inner, options)
                .map_err(|e| PyRuntimeError::new_err(format!("OCR extraction failed: {}", e)))
        }
        #[cfg(not(feature = "ocr"))]
        {
            let _ = engine;
            let _ = page;
            Err(PyRuntimeError::new_err("OCR feature not enabled. Please install with 'pip install pdf_oxide[ocr]' or build with --features ocr"))
        }
    }

    // ========================================================================
    // OCR Searchable PDF
    // ========================================================================

    /// Make a single page searchable by adding an invisible OCR text layer.
    ///
    /// Runs OCR on the page, then overlays the recognized text as invisible
    /// (rendering mode 3) so it can be searched and selected but not seen.
    /// The document must be saved afterwards for changes to take effect.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///     engine (OcrEngine): OCR engine instance
    ///     dpi (float): Rendering DPI for OCR (default 300.0)
    ///
    /// Returns:
    ///     int: Number of text spans added to the page
    ///
    /// Raises:
    ///     RuntimeError: If OCR or text layer generation fails
    ///
    /// Example:
    ///     >>> engine = OcrEngine("det.onnx", "rec.onnx", "dict.txt")
    ///     >>> doc = PdfDocument("scanned.pdf")
    ///     >>> spans = doc.make_page_searchable(0, engine)
    ///     >>> doc.save("searchable.pdf")
    #[pyo3(signature = (page, engine, dpi=300.0))]
    fn make_page_searchable(
        &mut self,
        page: usize,
        engine: &PyOcrEngine,
        dpi: f32,
    ) -> PyResult<usize> {
        #[cfg(feature = "ocr")]
        {
            self.ensure_editor()?;
            let editor = self.editor.as_mut().unwrap();
            editor
                .make_page_searchable(page, &engine.inner, dpi)
                .map_err(|e| {
                    PyRuntimeError::new_err(format!("Failed to make page searchable: {}", e))
                })
        }
        #[cfg(not(feature = "ocr"))]
        {
            let _ = (page, engine, dpi);
            Err(PyRuntimeError::new_err("OCR feature not enabled"))
        }
    }

    /// Make all pages in the document searchable.
    ///
    /// Iterates over every page, checks if it needs OCR (scanned or hybrid),
    /// and adds an invisible text layer where needed. Pages with sufficient
    /// native text are skipped automatically.
    ///
    /// Args:
    ///     engine (OcrEngine): OCR engine instance
    ///     dpi (float): Rendering DPI for OCR (default 300.0)
    ///
    /// Returns:
    ///     int: Number of pages that were made searchable
    ///
    /// Raises:
    ///     RuntimeError: If OCR or text layer generation fails
    ///
    /// Example:
    ///     >>> engine = OcrEngine("det.onnx", "rec.onnx", "dict.txt")
    ///     >>> doc = PdfDocument("scanned.pdf")
    ///     >>> pages = doc.make_all_searchable(engine)
    ///     >>> print(f"Made {pages} pages searchable")
    ///     >>> doc.save("searchable.pdf")
    #[pyo3(signature = (engine, dpi=300.0))]
    fn make_all_searchable(&mut self, engine: &PyOcrEngine, dpi: f32) -> PyResult<usize> {
        #[cfg(feature = "ocr")]
        {
            self.ensure_editor()?;
            let editor = self.editor.as_mut().unwrap();
            editor
                .make_all_searchable(&engine.inner, dpi)
                .map_err(|e| {
                    PyRuntimeError::new_err(format!(
                        "Failed to make document searchable: {}",
                        e
                    ))
                })
        }
        #[cfg(not(feature = "ocr"))]
        {
            let _ = (engine, dpi);
            Err(PyRuntimeError::new_err("OCR feature not enabled"))
        }
    }

    // ========================================================================
    // Form Fields (AcroForm)
    // ========================================================================

    /// Get all form fields from the document.
    ///
    /// Extracts AcroForm fields including text inputs, checkboxes, radio buttons,
    /// dropdowns, and signature fields. Works with tax forms, insurance documents,
    /// government forms, and any PDF with interactive fields.
    ///
    /// Returns:
    ///     list[FormField]: List of form fields with names, types, values, and metadata
    ///
    /// Raises:
    ///     RuntimeError: If form extraction fails
    ///
    /// Example:
    ///     >>> doc = PdfDocument("w2_form.pdf")
    ///     >>> fields = doc.get_form_fields()
    ///     >>> for f in fields:
    ///     ...     print(f"{f.name}: {f.value}")
    fn get_form_fields(&mut self) -> PyResult<Vec<PyFormField>> {
        use crate::extractors::forms::FormExtractor;

        let fields = FormExtractor::extract_fields(&mut self.inner).map_err(|e| {
            PyRuntimeError::new_err(format!("Failed to extract form fields: {}", e))
        })?;

        Ok(fields
            .into_iter()
            .map(|f| PyFormField { inner: f })
            .collect())
    }

    /// Get the value of a specific form field by name.
    ///
    /// Args:
    ///     name (str): Full qualified field name (e.g., "topmostSubform[0].Page1[0].f1_01[0]")
    ///
    /// Returns:
    ///     str | bool | list | None: The field value, or None if not found
    ///
    /// Raises:
    ///     RuntimeError: If field lookup fails
    ///
    /// Example:
    ///     >>> val = doc.get_form_field_value("employee_name")
    ///     >>> print(val)  # "John Doe"
    fn get_form_field_value(&mut self, name: &str, py: Python<'_>) -> PyResult<Py<PyAny>> {
        self.ensure_editor()?;
        let editor = self.editor.as_mut().unwrap();

        let value = editor
            .get_form_field_value(name)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to get field value: {}", e)))?;

        match value {
            Some(v) => form_field_value_to_python(&v, py),
            None => Ok(py.None()),
        }
    }

    /// Set the value of a form field.
    ///
    /// Args:
    ///     name (str): Full qualified field name
    ///     value (str | bool): New value for the field
    ///
    /// Raises:
    ///     RuntimeError: If the field is not found or value cannot be set
    ///
    /// Example:
    ///     >>> doc.set_form_field_value("employee_name", "Jane Doe")
    ///     >>> doc.save("filled_form.pdf")
    fn set_form_field_value(&mut self, name: &str, value: &Bound<'_, PyAny>) -> PyResult<()> {
        self.ensure_editor()?;
        let editor = self.editor.as_mut().unwrap();

        let field_value = python_to_form_field_value(value)?;

        editor
            .set_form_field_value(name, field_value)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to set field value: {}", e)))
    }

    /// Check if the document contains an XFA form.
    ///
    /// XFA (XML Forms Architecture) is used by some PDF generators (e.g., Adobe LiveCycle).
    /// IRS W-2 and many government forms are hybrid AcroForm + XFA.
    ///
    /// Returns:
    ///     bool: True if the document has XFA form data
    ///
    /// Example:
    ///     >>> if doc.has_xfa():
    ///     ...     print("Document has XFA form data")
    fn has_xfa(&mut self) -> PyResult<bool> {
        use crate::xfa::XfaExtractor;

        XfaExtractor::has_xfa(&mut self.inner)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to check XFA: {}", e)))
    }

    /// Export form data to FDF or XFDF format.
    ///
    /// Args:
    ///     path (str): Output file path
    ///     format (str): Export format, "fdf" or "xfdf" (default: "fdf")
    ///
    /// Raises:
    ///     RuntimeError: If export fails
    ///
    /// Example:
    ///     >>> doc.export_form_data("form_data.fdf")
    ///     >>> doc.export_form_data("form_data.xfdf", format="xfdf")
    #[pyo3(signature = (path, format="fdf"))]
    fn export_form_data(&mut self, path: &str, format: &str) -> PyResult<()> {
        self.ensure_editor()?;
        let editor = self.editor.as_mut().unwrap();

        match format {
            "fdf" => editor
                .export_form_data_fdf(path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to export FDF: {}", e))),
            "xfdf" => editor
                .export_form_data_xfdf(path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to export XFDF: {}", e))),
            _ => Err(PyRuntimeError::new_err(format!(
                "Unknown format '{}'. Use 'fdf' or 'xfdf'.",
                format
            ))),
        }
    }

    // ========================================================================
    // Image Bytes Extraction
    // ========================================================================

    /// Extract image bytes from a page as PNG data.
    ///
    /// Returns actual image pixel data (as PNG), unlike extract_images() which
    /// returns only metadata.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Returns:
    ///     list[dict]: List of dicts with keys: width (int), height (int),
    ///         data (bytes, PNG-encoded), format (str, always "png")
    ///
    /// Raises:
    ///     RuntimeError: If extraction or conversion fails
    fn extract_image_bytes(&mut self, py: Python<'_>, page: usize) -> PyResult<Py<PyAny>> {
        let images = self
            .inner
            .extract_images(page)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract images: {}", e)))?;

        let py_list = pyo3::types::PyList::empty(py);
        for img in &images {
            let png_data = img.to_png_bytes().map_err(|e| {
                PyRuntimeError::new_err(format!("Failed to convert image to PNG: {}", e))
            })?;

            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("width", img.width())?;
            dict.set_item("height", img.height())?;
            dict.set_item("format", "png")?;
            dict.set_item("data", pyo3::types::PyBytes::new(py, &png_data))?;
            py_list.append(dict)?;
        }
        Ok(py_list.into())
    }

    // ========================================================================
    // Form Flattening
    // ========================================================================

    /// Flatten all form fields into page content.
    ///
    /// After flattening, form field values become static text and are no longer editable.
    ///
    /// Raises:
    ///     RuntimeError: If flattening fails
    fn flatten_forms(&mut self) -> PyResult<()> {
        self.ensure_editor()?;
        if let Some(ref mut editor) = self.editor {
            editor
                .flatten_forms()
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to flatten forms: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    /// Flatten form fields on a specific page.
    ///
    /// Args:
    ///     page (int): Page index (0-based)
    ///
    /// Raises:
    ///     RuntimeError: If flattening fails
    fn flatten_forms_on_page(&mut self, page: usize) -> PyResult<()> {
        self.ensure_editor()?;
        if let Some(ref mut editor) = self.editor {
            editor.flatten_forms_on_page(page).map_err(|e| {
                PyRuntimeError::new_err(format!("Failed to flatten forms on page: {}", e))
            })
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    // ========================================================================
    // PDF Merging
    // ========================================================================

    /// Merge another PDF into this document.
    ///
    /// Accepts either a file path (str) or raw PDF bytes.
    ///
    /// Args:
    ///     source: File path (str) or PDF bytes
    ///
    /// Returns:
    ///     int: Number of pages merged
    ///
    /// Raises:
    ///     RuntimeError: If merge fails
    fn merge_from(&mut self, source: &Bound<'_, PyAny>) -> PyResult<usize> {
        self.ensure_editor()?;
        let editor = self.editor.as_mut().unwrap();

        if let Ok(path) = source.extract::<String>() {
            editor
                .merge_from(&path)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to merge PDF: {}", e)))
        } else if let Ok(data) = source.extract::<Vec<u8>>() {
            editor
                .merge_from_bytes(&data)
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to merge PDF: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("source must be a file path (str) or PDF bytes"))
        }
    }

    // ========================================================================
    // File Embedding
    // ========================================================================

    /// Embed a file into the PDF document.
    ///
    /// Args:
    ///     name (str): Display name for the embedded file
    ///     data (bytes): File contents
    ///
    /// Raises:
    ///     RuntimeError: If embedding fails
    fn embed_file(&mut self, name: &str, data: &[u8]) -> PyResult<()> {
        self.ensure_editor()?;
        if let Some(ref mut editor) = self.editor {
            editor
                .embed_file(name, data.to_vec())
                .map_err(|e| PyRuntimeError::new_err(format!("Failed to embed file: {}", e)))
        } else {
            Err(PyRuntimeError::new_err("No document loaded"))
        }
    }

    // ========================================================================
    // Page Labels
    // ========================================================================

    /// Get page label ranges from the document.
    ///
    /// Returns:
    ///     list[dict]: List of dicts with keys: start_page (int), style (str),
    ///         prefix (str | None), start_value (int)
    ///
    /// Raises:
    ///     RuntimeError: If extraction fails
    fn page_labels(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        use crate::extractors::page_labels::PageLabelExtractor;

        let labels = PageLabelExtractor::extract(&mut self.inner)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to get page labels: {}", e)))?;

        let py_list = pyo3::types::PyList::empty(py);
        for label in &labels {
            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("start_page", label.start_page)?;
            dict.set_item("style", format!("{:?}", label.style))?;
            match &label.prefix {
                Some(p) => dict.set_item("prefix", p)?,
                None => dict.set_item("prefix", py.None())?,
            };
            dict.set_item("start_value", label.start_value)?;
            py_list.append(dict)?;
        }
        Ok(py_list.into())
    }

    // ========================================================================
    // XMP Metadata
    // ========================================================================

    /// Get XMP metadata from the document.
    ///
    /// Returns:
    ///     dict | None: Dict with XMP fields (dc_title, dc_creator, etc.) or None
    ///
    /// Raises:
    ///     RuntimeError: If extraction fails
    fn xmp_metadata(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        use crate::extractors::xmp::XmpExtractor;

        let metadata = XmpExtractor::extract(&mut self.inner)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to get XMP metadata: {}", e)))?;

        match metadata {
            None => Ok(py.None()),
            Some(xmp) => {
                let dict = pyo3::types::PyDict::new(py);
                if let Some(ref title) = xmp.dc_title {
                    dict.set_item("dc_title", title)?;
                }
                if !xmp.dc_creator.is_empty() {
                    dict.set_item("dc_creator", &xmp.dc_creator)?;
                }
                if let Some(ref desc) = xmp.dc_description {
                    dict.set_item("dc_description", desc)?;
                }
                if !xmp.dc_subject.is_empty() {
                    dict.set_item("dc_subject", &xmp.dc_subject)?;
                }
                if let Some(ref lang) = xmp.dc_language {
                    dict.set_item("dc_language", lang)?;
                }
                if let Some(ref tool) = xmp.xmp_creator_tool {
                    dict.set_item("xmp_creator_tool", tool)?;
                }
                if let Some(ref date) = xmp.xmp_create_date {
                    dict.set_item("xmp_create_date", date)?;
                }
                if let Some(ref date) = xmp.xmp_modify_date {
                    dict.set_item("xmp_modify_date", date)?;
                }
                if let Some(ref producer) = xmp.pdf_producer {
                    dict.set_item("pdf_producer", producer)?;
                }
                if let Some(ref keywords) = xmp.pdf_keywords {
                    dict.set_item("pdf_keywords", keywords)?;
                }
                Ok(dict.into())
            },
        }
    }

    /// String representation of the document.
    ///
    /// Returns:
    ///     str: Representation showing PDF version
    fn __repr__(&self) -> String {
        format!("PdfDocument(version={}.{})", self.inner.version().0, self.inner.version().1)
    }
}

// === Form Field Type ===

use crate::extractors::forms::{
    field_flags, FieldType as RustFieldType, FieldValue as RustFieldValue,
    FormField as RustFormField,
};

/// A form field extracted from a PDF AcroForm.
///
/// Represents interactive fields like text inputs, checkboxes, radio buttons,
/// dropdowns, and signature fields found in PDF forms.
///
/// Properties:
///     name (str): Full qualified field name
///     field_type (str): Field type ("text", "button", "choice", "signature", or "unknown")
///     value (str | bool | list | None): Field value
///     tooltip (str | None): Tooltip/description text
///     bounds (tuple | None): Bounding box as (x1, y1, x2, y2) or None
///     flags (int | None): Raw field flags bitmask
///     max_length (int | None): Maximum length for text fields
///     is_readonly (bool): Whether the field is read-only
///     is_required (bool): Whether the field is required
///
/// Example:
///     >>> fields = doc.get_form_fields()
///     >>> for f in fields:
///     ...     print(f"{f.name} ({f.field_type}): {f.value}")
#[pyclass(name = "FormField", unsendable)]
pub struct PyFormField {
    inner: RustFormField,
}

#[pymethods]
impl PyFormField {
    /// Full qualified field name (e.g., "topmostSubform[0].Page1[0].f1_01[0]").
    #[getter]
    #[allow(clippy::misnamed_getters)]
    fn name(&self) -> &str {
        &self.inner.full_name
    }

    /// Field type as a string: "text", "button", "choice", "signature", or "unknown".
    #[getter]
    fn field_type(&self) -> &str {
        match &self.inner.field_type {
            RustFieldType::Text => "text",
            RustFieldType::Button => "button",
            RustFieldType::Choice => "choice",
            RustFieldType::Signature => "signature",
            RustFieldType::Unknown(_) => "unknown",
        }
    }

    /// Field value: str for text/name, bool for checkbox, list for multi-select, None if empty.
    #[getter]
    fn value(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        field_value_to_python(&self.inner.value, py)
    }

    /// Tooltip or description text, if set.
    #[getter]
    fn tooltip(&self) -> Option<&str> {
        self.inner.tooltip.as_deref()
    }

    /// Bounding box as (x1, y1, x2, y2), or None if not available.
    #[getter]
    fn bounds(&self) -> Option<(f64, f64, f64, f64)> {
        self.inner.bounds.map(|b| (b[0], b[1], b[2], b[3]))
    }

    /// Raw field flags bitmask (see PDF spec Table 221).
    #[getter]
    fn flags(&self) -> Option<u32> {
        self.inner.flags
    }

    /// Maximum text length for text fields, or None.
    #[getter]
    fn max_length(&self) -> Option<u32> {
        self.inner.max_length
    }

    /// Whether this field is read-only.
    #[getter]
    fn is_readonly(&self) -> bool {
        self.inner
            .flags
            .is_some_and(|f| f & field_flags::READ_ONLY != 0)
    }

    /// Whether this field is required.
    #[getter]
    fn is_required(&self) -> bool {
        self.inner
            .flags
            .is_some_and(|f| f & field_flags::REQUIRED != 0)
    }

    fn __repr__(&self) -> String {
        let val_str = match &self.inner.value {
            RustFieldValue::Text(s) => format!("\"{}\"", s),
            RustFieldValue::Boolean(b) => format!("{}", b),
            RustFieldValue::Name(s) => format!("\"{}\"", s),
            RustFieldValue::Array(v) => format!("{:?}", v),
            RustFieldValue::None => "None".to_string(),
        };
        format!(
            "FormField(name=\"{}\", type=\"{}\", value={})",
            self.inner.full_name,
            self.field_type(),
            val_str
        )
    }
}

/// Convert an extractor FieldValue to a Python object.
fn field_value_to_python(value: &RustFieldValue, py: Python<'_>) -> PyResult<Py<PyAny>> {
    match value {
        RustFieldValue::Text(s) => Ok(s.into_pyobject(py)?.into_any().unbind()),
        RustFieldValue::Name(s) => Ok(s.into_pyobject(py)?.into_any().unbind()),
        RustFieldValue::Boolean(b) => Ok(b.into_pyobject(py)?.to_owned().into_any().unbind()),
        RustFieldValue::Array(v) => Ok(v.into_pyobject(py)?.into_any().unbind()),
        RustFieldValue::None => Ok(py.None()),
    }
}

/// Convert an editor FormFieldValue to a Python object.
fn form_field_value_to_python(
    value: &crate::editor::form_fields::FormFieldValue,
    py: Python<'_>,
) -> PyResult<Py<PyAny>> {
    use crate::editor::form_fields::FormFieldValue;
    match value {
        FormFieldValue::Text(s) => Ok(s.into_pyobject(py)?.into_any().unbind()),
        FormFieldValue::Choice(s) => Ok(s.into_pyobject(py)?.into_any().unbind()),
        FormFieldValue::Boolean(b) => Ok(b.into_pyobject(py)?.to_owned().into_any().unbind()),
        FormFieldValue::MultiChoice(v) => Ok(v.into_pyobject(py)?.into_any().unbind()),
        FormFieldValue::None => Ok(py.None()),
    }
}

/// Convert a Python value to a FormFieldValue.
fn python_to_form_field_value(
    value: &Bound<'_, PyAny>,
) -> PyResult<crate::editor::form_fields::FormFieldValue> {
    use crate::editor::form_fields::FormFieldValue;

    if let Ok(b) = value.extract::<bool>() {
        Ok(FormFieldValue::Boolean(b))
    } else if let Ok(s) = value.extract::<String>() {
        Ok(FormFieldValue::Text(s))
    } else if let Ok(v) = value.extract::<Vec<String>>() {
        Ok(FormFieldValue::MultiChoice(v))
    } else if value.is_none() {
        Ok(FormFieldValue::None)
    } else {
        Err(PyRuntimeError::new_err("Value must be str, bool, list[str], or None"))
    }
}

// === PDF Creation API ===

use crate::api::PdfBuilder as RustPdfBuilder;

/// Python wrapper for PDF creation.
///
/// Provides simple PDF creation from Markdown, HTML, or plain text.
///
/// # Methods
///
/// - `from_markdown(content)`: Create PDF from Markdown
/// - `from_html(content)`: Create PDF from HTML
/// - `from_text(content)`: Create PDF from plain text
/// - `save(path)`: Save PDF to file
///
/// Example:
///     >>> pdf = Pdf.from_markdown("# Hello World")
///     >>> pdf.save("output.pdf")
#[pyclass(name = "Pdf")]
pub struct PyPdf {
    bytes: Vec<u8>,
}

#[pymethods]
impl PyPdf {
    /// Create a PDF from Markdown content.
    ///
    /// Args:
    ///     content (str): Markdown content
    ///     title (str, optional): Document title
    ///     author (str, optional): Document author
    ///
    /// Returns:
    ///     Pdf: Created PDF document
    ///
    /// Raises:
    ///     RuntimeError: If PDF creation fails
    ///
    /// Example:
    ///     >>> pdf = Pdf.from_markdown("# Hello\\n\\nWorld")
    ///     >>> pdf.save("hello.pdf")
    #[staticmethod]
    #[pyo3(signature = (content, title=None, author=None))]
    fn from_markdown(content: &str, title: Option<&str>, author: Option<&str>) -> PyResult<Self> {
        let mut builder = RustPdfBuilder::new();
        if let Some(t) = title {
            builder = builder.title(t);
        }
        if let Some(a) = author {
            builder = builder.author(a);
        }

        let pdf = builder
            .from_markdown(content)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to create PDF: {}", e)))?;

        Ok(PyPdf {
            bytes: pdf.into_bytes(),
        })
    }

    /// Create a PDF from HTML content.
    ///
    /// Args:
    ///     content (str): HTML content
    ///     title (str, optional): Document title
    ///     author (str, optional): Document author
    ///
    /// Returns:
    ///     Pdf: Created PDF document
    ///
    /// Example:
    ///     >>> pdf = Pdf.from_html("<h1>Hello</h1><p>World</p>")
    ///     >>> pdf.save("hello.pdf")
    #[staticmethod]
    #[pyo3(signature = (content, title=None, author=None))]
    fn from_html(content: &str, title: Option<&str>, author: Option<&str>) -> PyResult<Self> {
        let mut builder = RustPdfBuilder::new();
        if let Some(t) = title {
            builder = builder.title(t);
        }
        if let Some(a) = author {
            builder = builder.author(a);
        }

        let pdf = builder
            .from_html(content)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to create PDF: {}", e)))?;

        Ok(PyPdf {
            bytes: pdf.into_bytes(),
        })
    }

    /// Create a PDF from plain text.
    ///
    /// Args:
    ///     content (str): Plain text content
    ///     title (str, optional): Document title
    ///     author (str, optional): Document author
    ///
    /// Returns:
    ///     Pdf: Created PDF document
    ///
    /// Example:
    ///     >>> pdf = Pdf.from_text("Hello, World!")
    ///     >>> pdf.save("hello.pdf")
    #[staticmethod]
    #[pyo3(signature = (content, title=None, author=None))]
    fn from_text(content: &str, title: Option<&str>, author: Option<&str>) -> PyResult<Self> {
        let mut builder = RustPdfBuilder::new();
        if let Some(t) = title {
            builder = builder.title(t);
        }
        if let Some(a) = author {
            builder = builder.author(a);
        }

        let pdf = builder
            .from_text(content)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to create PDF: {}", e)))?;

        Ok(PyPdf {
            bytes: pdf.into_bytes(),
        })
    }

    /// Save the PDF to a file.
    ///
    /// Args:
    ///     path (str): Output file path
    ///
    /// Raises:
    ///     IOError: If the file cannot be written
    ///
    /// Example:
    ///     >>> pdf = Pdf.from_markdown("# Hello")
    ///     >>> pdf.save("output.pdf")
    fn save(&self, path: &str) -> PyResult<()> {
        std::fs::write(path, &self.bytes)
            .map_err(|e| PyIOError::new_err(format!("Failed to save PDF: {}", e)))
    }

    /// Get the PDF as bytes.
    ///
    /// Returns:
    ///     bytes: Raw PDF data
    ///
    /// Example:
    ///     >>> pdf = Pdf.from_markdown("# Hello")
    ///     >>> data = pdf.to_bytes()
    ///     >>> len(data) > 0
    ///     True
    fn to_bytes(&self) -> &[u8] {
        &self.bytes
    }

    /// Create a PDF from an image file.
    ///
    /// Args:
    ///     path (str): Path to the image file (PNG, JPEG)
    ///
    /// Returns:
    ///     Pdf: Created PDF document with image as a page
    ///
    /// Raises:
    ///     RuntimeError: If image loading or PDF creation fails
    #[staticmethod]
    fn from_image(path: &str) -> PyResult<Self> {
        use crate::api::Pdf;
        let pdf = Pdf::from_image(path).map_err(|e| {
            PyRuntimeError::new_err(format!("Failed to create PDF from image: {}", e))
        })?;
        Ok(PyPdf {
            bytes: pdf.into_bytes(),
        })
    }

    /// Create a multi-page PDF from multiple image files.
    ///
    /// Each image becomes a separate page.
    ///
    /// Args:
    ///     paths (list[str]): List of paths to image files
    ///
    /// Returns:
    ///     Pdf: Created PDF document
    ///
    /// Raises:
    ///     RuntimeError: If image loading or PDF creation fails
    #[staticmethod]
    fn from_images(paths: Vec<String>) -> PyResult<Self> {
        use crate::api::Pdf;
        let pdf = Pdf::from_images(&paths).map_err(|e| {
            PyRuntimeError::new_err(format!("Failed to create PDF from images: {}", e))
        })?;
        Ok(PyPdf {
            bytes: pdf.into_bytes(),
        })
    }

    /// Create a PDF from image bytes.
    ///
    /// Args:
    ///     data (bytes): Raw image data (PNG or JPEG)
    ///
    /// Returns:
    ///     Pdf: Created PDF document
    ///
    /// Raises:
    ///     RuntimeError: If image loading or PDF creation fails
    #[staticmethod]
    fn from_image_bytes(data: &[u8]) -> PyResult<Self> {
        use crate::api::Pdf;
        let pdf = Pdf::from_image_bytes(data).map_err(|e| {
            PyRuntimeError::new_err(format!("Failed to create PDF from image bytes: {}", e))
        })?;
        Ok(PyPdf {
            bytes: pdf.into_bytes(),
        })
    }

    /// Get the size of the PDF in bytes.
    ///
    /// Returns:
    ///     int: Size in bytes
    fn __len__(&self) -> usize {
        self.bytes.len()
    }

    /// String representation.
    fn __repr__(&self) -> String {
        format!("Pdf({} bytes)", self.bytes.len())
    }
}

// === Office Conversion API ===

#[cfg(feature = "office")]
use crate::converters::office::OfficeConverter as RustOfficeConverter;

/// Python wrapper for Office to PDF conversion.
///
/// Converts Microsoft Office documents (DOCX, XLSX, PPTX) to PDF.
/// Requires the `office` feature to be enabled.
///
/// # Example
///
/// ```python
/// from pdf_oxide import OfficeConverter
///
/// # Convert a Word document to PDF
/// pdf = OfficeConverter.from_docx("document.docx")
/// pdf.save("document.pdf")
///
/// # Convert from bytes
/// with open("spreadsheet.xlsx", "rb") as f:
///     pdf = OfficeConverter.from_xlsx_bytes(f.read())
///     pdf.save("spreadsheet.pdf")
///
/// # Auto-detect format and convert
/// pdf = OfficeConverter.convert("presentation.pptx")
/// pdf.save("presentation.pdf")
/// ```
#[cfg(feature = "office")]
#[pyclass(name = "OfficeConverter")]
pub struct PyOfficeConverter;

#[cfg(not(feature = "office"))]
#[pyclass(name = "OfficeConverter")]
pub struct PyOfficeConverter;

#[cfg(not(feature = "office"))]
#[pymethods]
impl PyOfficeConverter {
    #[new]
    fn new() -> PyResult<Self> {
        Err(PyRuntimeError::new_err(
            "Office feature not enabled. Please build with 'office' feature.",
        ))
    }

    #[staticmethod]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn convert(
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        Err(PyRuntimeError::new_err(
            "Office feature not enabled. Please build with 'office' feature.",
        ))
    }

    #[staticmethod]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn from_docx(
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        Err(PyRuntimeError::new_err(
            "Office feature not enabled. Please build with 'office' feature.",
        ))
    }

    #[staticmethod]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn from_xlsx(
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        Err(PyRuntimeError::new_err(
            "Office feature not enabled. Please build with 'office' feature.",
        ))
    }

    #[staticmethod]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn from_pptx(
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        Err(PyRuntimeError::new_err(
            "Office feature not enabled. Please build with 'office' feature.",
        ))
    }
}

#[cfg(feature = "office")]
#[pymethods]
impl PyOfficeConverter {
    /// Convert a DOCX file to PDF.
    ///
    /// Args:
    ///     path (str): Path to the DOCX file
    ///
    /// Returns:
    ///     Pdf: Created PDF document
    ///
    /// Raises:
    ///     IOError: If the file cannot be read
    ///     RuntimeError: If conversion fails
    ///
    /// Example:
    ///     >>> pdf = OfficeConverter.from_docx("document.docx")
    ///     >>> pdf.save("document.pdf")
    #[staticmethod]
    fn from_docx(path: &str) -> PyResult<PyPdf> {
        let converter = RustOfficeConverter::new();
        let bytes = converter
            .convert_docx(path)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to convert DOCX: {}", e)))?;
        Ok(PyPdf { bytes })
    }

    /// Convert DOCX bytes to PDF.
    ///
    /// Args:
    ///     data (bytes): DOCX file contents
    ///
    /// Returns:
    ///     Pdf: Created PDF document
    ///
    /// Raises:
    ///     RuntimeError: If conversion fails
    ///
    /// Example:
    ///     >>> with open("document.docx", "rb") as f:
    ///     ...     pdf = OfficeConverter.from_docx_bytes(f.read())
    ///     >>> pdf.save("document.pdf")
    #[staticmethod]
    fn from_docx_bytes(data: &[u8]) -> PyResult<PyPdf> {
        let converter = RustOfficeConverter::new();
        let bytes = converter
            .convert_docx_bytes(data)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to convert DOCX: {}", e)))?;
        Ok(PyPdf { bytes })
    }

    /// Convert an XLSX file to PDF.
    ///
    /// Args:
    ///     path (str): Path to the XLSX file
    ///
    /// Returns:
    ///     Pdf: Created PDF document
    ///
    /// Raises:
    ///     IOError: If the file cannot be read
    ///     RuntimeError: If conversion fails
    ///
    /// Example:
    ///     >>> pdf = OfficeConverter.from_xlsx("spreadsheet.xlsx")
    ///     >>> pdf.save("spreadsheet.pdf")
    #[staticmethod]
    fn from_xlsx(path: &str) -> PyResult<PyPdf> {
        let converter = RustOfficeConverter::new();
        let bytes = converter
            .convert_xlsx(path)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to convert XLSX: {}", e)))?;
        Ok(PyPdf { bytes })
    }

    /// Convert XLSX bytes to PDF.
    ///
    /// Args:
    ///     data (bytes): XLSX file contents
    ///
    /// Returns:
    ///     Pdf: Created PDF document
    ///
    /// Raises:
    ///     RuntimeError: If conversion fails
    ///
    /// Example:
    ///     >>> with open("spreadsheet.xlsx", "rb") as f:
    ///     ...     pdf = OfficeConverter.from_xlsx_bytes(f.read())
    ///     >>> pdf.save("spreadsheet.pdf")
    #[staticmethod]
    fn from_xlsx_bytes(data: &[u8]) -> PyResult<PyPdf> {
        let converter = RustOfficeConverter::new();
        let bytes = converter
            .convert_xlsx_bytes(data)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to convert XLSX: {}", e)))?;
        Ok(PyPdf { bytes })
    }

    /// Convert a PPTX file to PDF.
    ///
    /// Args:
    ///     path (str): Path to the PPTX file
    ///
    /// Returns:
    ///     Pdf: Created PDF document
    ///
    /// Raises:
    ///     IOError: If the file cannot be read
    ///     RuntimeError: If conversion fails
    ///
    /// Example:
    ///     >>> pdf = OfficeConverter.from_pptx("presentation.pptx")
    ///     >>> pdf.save("presentation.pdf")
    #[staticmethod]
    fn from_pptx(path: &str) -> PyResult<PyPdf> {
        let converter = RustOfficeConverter::new();
        let bytes = converter
            .convert_pptx(path)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to convert PPTX: {}", e)))?;
        Ok(PyPdf { bytes })
    }

    /// Convert PPTX bytes to PDF.
    ///
    /// Args:
    ///     data (bytes): PPTX file contents
    ///
    /// Returns:
    ///     Pdf: Created PDF document
    ///
    /// Raises:
    ///     RuntimeError: If conversion fails
    ///
    /// Example:
    ///     >>> with open("presentation.pptx", "rb") as f:
    ///     ...     pdf = OfficeConverter.from_pptx_bytes(f.read())
    ///     >>> pdf.save("presentation.pdf")
    #[staticmethod]
    fn from_pptx_bytes(data: &[u8]) -> PyResult<PyPdf> {
        let converter = RustOfficeConverter::new();
        let bytes = converter
            .convert_pptx_bytes(data)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to convert PPTX: {}", e)))?;
        Ok(PyPdf { bytes })
    }

    /// Auto-detect format and convert to PDF.
    ///
    /// Detects the file format based on extension and converts to PDF.
    /// Supports .docx, .xlsx, .xls, and .pptx files.
    ///
    /// Args:
    ///     path (str): Path to the Office document
    ///
    /// Returns:
    ///     Pdf: Created PDF document
    ///
    /// Raises:
    ///     IOError: If the file cannot be read
    ///     RuntimeError: If conversion fails or format is unsupported
    ///
    /// Example:
    ///     >>> pdf = OfficeConverter.convert("document.docx")
    ///     >>> pdf.save("document.pdf")
    #[staticmethod]
    fn convert(path: &str) -> PyResult<PyPdf> {
        let converter = RustOfficeConverter::new();
        let bytes = converter.convert(path).map_err(|e| {
            PyRuntimeError::new_err(format!("Failed to convert Office document: {}", e))
        })?;
        Ok(PyPdf { bytes })
    }
}

// === DOM Access API ===

use crate::editor::{ElementId, PdfElement, PdfPage as RustPdfPage, PdfText as RustPdfText};

/// A rectangular region within a PDF page for scoped extraction (v0.3.14).
#[pyclass(name = "PdfPageRegion")]
pub struct PyPdfPageRegion {
    pub doc: Py<PyPdfDocument>,
    pub page_index: usize,
    pub region: crate::geometry::Rect,
}

#[pymethods]
impl PyPdfPageRegion {
    /// Get the bounding box of this region.
    #[getter]
    fn bbox(&self) -> (f32, f32, f32, f32) {
        (self.region.x, self.region.y, self.region.width, self.region.height)
    }

    /// Extract text from this region.
    fn extract_text(&self, py: Python<'_>) -> PyResult<String> {
        let mut doc_bound = self.doc.bind(py).borrow_mut();
        doc_bound.extract_text(self.page_index, Some(self.bbox()))
    }

    /// Extract words from this region.
    fn extract_words(&self, py: Python<'_>) -> PyResult<Vec<PyWord>> {
        let mut doc_bound = self.doc.bind(py).borrow_mut();
        doc_bound.extract_words(self.page_index, Some(self.bbox()))
    }

    /// Extract lines from this region.
    fn extract_text_lines(&self, py: Python<'_>) -> PyResult<Vec<PyTextLine>> {
        let mut doc_bound = self.doc.bind(py).borrow_mut();
        doc_bound.extract_text_lines(self.page_index, Some(self.bbox()))
    }

    /// Extract tables from this region.
    #[pyo3(signature = (table_settings=None))]
    fn extract_tables(
        &self,
        py: Python<'_>,
        table_settings: Option<Bound<'_, pyo3::types::PyDict>>,
    ) -> PyResult<Py<PyAny>> {
        let mut doc_bound = self.doc.bind(py).borrow_mut();
        doc_bound.extract_tables(py, self.page_index, Some(self.bbox()), table_settings)
    }

    /// Extract images from this region.
    fn extract_images(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let mut doc_bound = self.doc.bind(py).borrow_mut();
        doc_bound.extract_images(py, self.page_index, Some(self.bbox()))
    }

    /// Extract vector paths from this region.
    fn extract_paths(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let mut doc_bound = self.doc.bind(py).borrow_mut();
        let paths = doc_bound
            .inner
            .extract_paths_in_rect(self.page_index, self.region)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to extract paths: {}", e)))?;

        let py_list = pyo3::types::PyList::empty(py);
        for path in &paths {
            py_list.append(path_to_py_dict(py, path)?)?;
        }
        Ok(py_list.into())
    }

    fn __repr__(&self) -> String {
        format!("PdfPageRegion(page={}, bbox={:?})", self.page_index, self.region)
    }
}

/// Python wrapper for PDF page with DOM-like access.
///
/// Provides hierarchical access to page content elements.
///
/// Example:
///     >>> doc = PdfDocument("sample.pdf")
///     >>> page = doc.page(0)
///     >>> for text in page.find_text_containing("Hello"):
///     ...     print(f"{text.value} at {text.bbox}")
#[pyclass(name = "PdfPage", unsendable)]
pub struct PyPdfPage {
    inner: RustPdfPage,
}

#[pymethods]
impl PyPdfPage {
    /// Get the page index.
    ///
    /// Returns:
    ///     int: Zero-based page index
    #[getter]
    fn index(&self) -> usize {
        self.inner.page_index
    }

    /// Get page width.
    ///
    /// Returns:
    ///     float: Page width in points
    #[getter]
    fn width(&self) -> f32 {
        self.inner.width
    }

    /// Get page height.
    ///
    /// Returns:
    ///     float: Page height in points
    #[getter]
    fn height(&self) -> f32 {
        self.inner.height
    }

    /// Get all top-level elements on the page.
    ///
    /// Returns:
    ///     list[PdfElement]: List of child elements
    ///
    /// Example:
    ///     >>> for elem in page.children():
    ///     ...     if elem.is_text():
    ///     ...         print(elem.as_text().value)
    fn children(&self) -> Vec<PyPdfElement> {
        self.inner
            .children()
            .into_iter()
            .map(|e| PyPdfElement { inner: e })
            .collect()
    }

    /// Find all text elements containing the specified string.
    ///
    /// Args:
    ///     needle (str): String to search for
    ///
    /// Returns:
    ///     list[PdfText]: List of matching text elements
    ///
    /// Example:
    ///     >>> texts = page.find_text_containing("Hello")
    ///     >>> for t in texts:
    ///     ...     print(t.value)
    fn find_text_containing(&self, needle: &str) -> Vec<PyPdfText> {
        self.inner
            .find_text_containing(needle)
            .into_iter()
            .map(|t| PyPdfText { inner: t })
            .collect()
    }

    /// Find all images on the page.
    ///
    /// Returns:
    ///     list[PdfImage]: List of image elements
    fn find_images(&self) -> Vec<PyPdfImage> {
        self.inner
            .find_images()
            .into_iter()
            .map(|i| PyPdfImage { inner: i })
            .collect()
    }

    /// Get element by ID.
    ///
    /// Args:
    ///     element_id (str): The element ID as a string
    ///
    /// Returns:
    ///     PdfElement | None: The element if found, None otherwise
    fn get_element(&self, _element_id: &str) -> Option<PyPdfElement> {
        // Note: ElementId is UUID-based, this is a simplified lookup
        // In practice, users would use the ID from an existing element
        None // Simplified - would need proper ID parsing
    }

    /// Set text content for an element by ID.
    ///
    /// Args:
    ///     text_id: The ID of the text element (from PdfText.id)
    ///     new_text (str): New text content
    ///
    /// Raises:
    ///     RuntimeError: If the element is not found or is not a text element
    ///
    /// Example:
    ///     >>> for t in page.find_text_containing("old"):
    ///     ...     page.set_text(t.id, "new")
    fn set_text(&mut self, text_id: &PyPdfTextId, new_text: &str) -> PyResult<()> {
        self.inner
            .set_text(text_id.inner, new_text)
            .map_err(|e| PyRuntimeError::new_err(format!("Failed to set text: {}", e)))
    }

    // === Annotations ===

    /// Get all annotations on the page.
    ///
    /// Returns:
    ///     list[PdfAnnotation]: List of annotations
    fn annotations(&self) -> Vec<PyAnnotationWrapper> {
        self.inner
            .annotations()
            .iter()
            .map(|a| PyAnnotationWrapper { inner: a.clone() })
            .collect()
    }

    /// Add a link annotation to the page.
    ///
    /// Args:
    ///     x (float): X coordinate
    ///     y (float): Y coordinate
    ///     width (float): Link width
    ///     height (float): Link height
    ///     url (str): Target URL
    ///
    /// Returns:
    ///     str: Annotation ID
    ///
    /// Example:
    ///     >>> page.add_link(100, 700, 50, 12, "https://example.com")
    fn add_link(&mut self, x: f32, y: f32, width: f32, height: f32, url: &str) -> String {
        use crate::writer::LinkAnnotation;
        let link = LinkAnnotation::uri(crate::geometry::Rect::new(x, y, width, height), url);
        let id = self.inner.add_annotation(link);
        format!("{:?}", id)
    }

    /// Add a text highlight annotation.
    ///
    /// Args:
    ///     x (float): X coordinate
    ///     y (float): Y coordinate
    ///     width (float): Highlight width
    ///     height (float): Highlight height
    ///     color (tuple): RGB color as (r, g, b) where each is 0.0-1.0
    ///
    /// Example:
    ///     >>> page.add_highlight(100, 700, 200, 12, (1.0, 1.0, 0.0))  # Yellow
    fn add_highlight(
        &mut self,
        x: f32,
        y: f32,
        width: f32,
        height: f32,
        color: (f32, f32, f32),
    ) -> String {
        use crate::writer::TextMarkupAnnotation;
        use crate::TextMarkupType;
        let rect = crate::geometry::Rect::new(x, y, width, height);
        let highlight = TextMarkupAnnotation::from_rect(TextMarkupType::Highlight, rect)
            .with_color(color.0, color.1, color.2);
        let id = self.inner.add_annotation(highlight);
        format!("{:?}", id)
    }

    /// Add a sticky note annotation.
    ///
    /// Args:
    ///     x (float): X coordinate
    ///     y (float): Y coordinate
    ///     text (str): Note content
    ///
    /// Example:
    ///     >>> page.add_note(100, 700, "This is important!")
    fn add_note(&mut self, x: f32, y: f32, text: &str) -> String {
        use crate::writer::TextAnnotation;
        // Create a small rect for the sticky note icon (24x24 is typical)
        let rect = crate::geometry::Rect::new(x, y, 24.0, 24.0);
        let note = TextAnnotation::new(rect, text);
        let id = self.inner.add_annotation(note);
        format!("{:?}", id)
    }

    // === Phase 1.6 — Additional annotation creation methods ===

    /// Add a free text annotation (text rendered directly on the page).
    ///
    /// Args:
    ///     x (float): X coordinate
    ///     y (float): Y coordinate
    ///     width (float): Width
    ///     height (float): Height
    ///     text (str): Text content
    ///     font_size (float): Font size in points (default: 12.0)
    ///     color (tuple): RGB text color (r, g, b), each 0.0-1.0 (default: black)
    ///
    /// Returns:
    ///     str: Annotation ID
    #[pyo3(signature = (x, y, width, height, text, font_size=12.0, color=None))]
    fn add_freetext(
        &mut self,
        x: f32, y: f32, width: f32, height: f32,
        text: &str,
        font_size: f32,
        color: Option<(f32, f32, f32)>,
    ) -> String {
        use crate::writer::FreeTextAnnotation;
        let rect = crate::geometry::Rect::new(x, y, width, height);
        let mut annot = FreeTextAnnotation::new(rect, text);
        annot = annot.with_font("Helvetica", font_size);
        if let Some((r, g, b)) = color {
            annot = annot.with_text_color(r, g, b);
        }
        let id = self.inner.add_annotation(annot);
        format!("{:?}", id)
    }

    /// Add a rubber stamp annotation.
    ///
    /// Args:
    ///     x (float): X coordinate
    ///     y (float): Y coordinate
    ///     width (float): Width
    ///     height (float): Height
    ///     stamp_type (str): Stamp type — "approved", "experimental", "not_approved",
    ///                       "as_is", "expired", "not_for_public", "confidential",
    ///                       "final", "sold", "departmental", "for_comment",
    ///                       "top_secret", "draft", "for_public"
    ///
    /// Returns:
    ///     str: Annotation ID
    fn add_stamp(&mut self, x: f32, y: f32, width: f32, height: f32, stamp_type: &str) -> String {
        use crate::writer::{StampAnnotation, StampType};
        let rect = crate::geometry::Rect::new(x, y, width, height);
        let stype = match stamp_type.to_lowercase().as_str() {
            "approved" => StampType::Approved,
            "experimental" => StampType::Experimental,
            "not_approved" | "notapproved" => StampType::NotApproved,
            "as_is" | "asis" => StampType::AsIs,
            "expired" => StampType::Expired,
            "not_for_public" | "notforpublic" => StampType::NotForPublicRelease,
            "confidential" => StampType::Confidential,
            "final" => StampType::Final,
            "sold" => StampType::Sold,
            "departmental" => StampType::Departmental,
            "for_comment" | "forcomment" => StampType::ForComment,
            "top_secret" | "topsecret" => StampType::TopSecret,
            "draft" => StampType::Draft,
            "for_public" | "forpublic" => StampType::ForPublicRelease,
            _ => StampType::Draft,
        };
        let stamp = StampAnnotation::new(rect, stype);
        let id = self.inner.add_annotation(stamp);
        format!("{:?}", id)
    }

    /// Add an underline text markup annotation.
    ///
    /// Args:
    ///     x, y, width, height (float): Bounding box
    ///     color (tuple): RGB color (r, g, b), each 0.0-1.0
    fn add_underline(&mut self, x: f32, y: f32, width: f32, height: f32, color: (f32, f32, f32)) -> String {
        use crate::writer::TextMarkupAnnotation;
        use crate::TextMarkupType;
        let rect = crate::geometry::Rect::new(x, y, width, height);
        let annot = TextMarkupAnnotation::from_rect(TextMarkupType::Underline, rect)
            .with_color(color.0, color.1, color.2);
        let id = self.inner.add_annotation(annot);
        format!("{:?}", id)
    }

    /// Add a strikeout text markup annotation.
    fn add_strikeout(&mut self, x: f32, y: f32, width: f32, height: f32, color: (f32, f32, f32)) -> String {
        use crate::writer::TextMarkupAnnotation;
        use crate::TextMarkupType;
        let rect = crate::geometry::Rect::new(x, y, width, height);
        let annot = TextMarkupAnnotation::from_rect(TextMarkupType::StrikeOut, rect)
            .with_color(color.0, color.1, color.2);
        let id = self.inner.add_annotation(annot);
        format!("{:?}", id)
    }

    /// Add a squiggly text markup annotation.
    fn add_squiggly(&mut self, x: f32, y: f32, width: f32, height: f32, color: (f32, f32, f32)) -> String {
        use crate::writer::TextMarkupAnnotation;
        use crate::TextMarkupType;
        let rect = crate::geometry::Rect::new(x, y, width, height);
        let annot = TextMarkupAnnotation::from_rect(TextMarkupType::Squiggly, rect)
            .with_color(color.0, color.1, color.2);
        let id = self.inner.add_annotation(annot);
        format!("{:?}", id)
    }

    /// Add a line annotation between two points.
    ///
    /// Args:
    ///     x1, y1 (float): Start point
    ///     x2, y2 (float): End point
    ///     color (tuple): RGB color (r, g, b), each 0.0-1.0
    fn add_line_annot(
        &mut self,
        x1: f32, y1: f32, x2: f32, y2: f32,
        color: (f32, f32, f32),
    ) -> String {
        use crate::writer::LineAnnotation;
        let annot = LineAnnotation::new((x1 as f64, y1 as f64), (x2 as f64, y2 as f64))
            .with_stroke_color(color.0, color.1, color.2);
        let id = self.inner.add_annotation(annot);
        format!("{:?}", id)
    }

    /// Add a square/rectangle annotation.
    ///
    /// Args:
    ///     x, y, width, height (float): Bounding box
    ///     color (tuple): RGB border color
    ///     fill (tuple, optional): RGB fill color
    #[pyo3(signature = (x, y, width, height, color, fill=None))]
    fn add_square(
        &mut self,
        x: f32, y: f32, width: f32, height: f32,
        color: (f32, f32, f32),
        fill: Option<(f32, f32, f32)>,
    ) -> String {
        use crate::writer::ShapeAnnotation;
        let rect = crate::geometry::Rect::new(x, y, width, height);
        let mut annot = ShapeAnnotation::square(rect)
            .with_stroke_color(color.0, color.1, color.2);
        if let Some((r, g, b)) = fill {
            annot = annot.with_fill_color(r, g, b);
        }
        let id = self.inner.add_annotation(annot);
        format!("{:?}", id)
    }

    /// Add a circle/ellipse annotation.
    #[pyo3(signature = (x, y, width, height, color, fill=None))]
    fn add_circle(
        &mut self,
        x: f32, y: f32, width: f32, height: f32,
        color: (f32, f32, f32),
        fill: Option<(f32, f32, f32)>,
    ) -> String {
        use crate::writer::ShapeAnnotation;
        let rect = crate::geometry::Rect::new(x, y, width, height);
        let mut annot = ShapeAnnotation::circle(rect)
            .with_stroke_color(color.0, color.1, color.2);
        if let Some((r, g, b)) = fill {
            annot = annot.with_fill_color(r, g, b);
        }
        let id = self.inner.add_annotation(annot);
        format!("{:?}", id)
    }

    /// Add a redaction annotation (marks area for redaction).
    ///
    /// Call apply_page_redactions() or apply_all_redactions() to apply.
    ///
    /// Args:
    ///     x, y, width, height (float): Area to redact
    ///     overlay_text (str, optional): Text shown after redaction
    #[pyo3(signature = (x, y, width, height, overlay_text=None))]
    fn add_redact(
        &mut self,
        x: f32, y: f32, width: f32, height: f32,
        overlay_text: Option<&str>,
    ) -> String {
        use crate::writer::RedactAnnotation;
        let rect = crate::geometry::Rect::new(x, y, width, height);
        let mut annot = RedactAnnotation::new(rect);
        if let Some(text) = overlay_text {
            annot = annot.with_overlay_text(text);
        }
        let id = self.inner.add_annotation(annot);
        format!("{:?}", id)
    }

    /// Add a watermark annotation.
    ///
    /// Args:
    ///     text (str): Watermark text
    ///     opacity (float): Opacity 0.0-1.0 (default: 0.3)
    #[pyo3(signature = (text, opacity=0.3))]
    fn add_watermark(&mut self, text: &str, opacity: f32) -> String {
        use crate::writer::WatermarkAnnotation;
        let annot = WatermarkAnnotation::new(text)
            .with_opacity(opacity);
        let id = self.inner.add_annotation(annot);
        format!("{:?}", id)
    }

    /// Remove an annotation by index.
    ///
    /// Args:
    ///     index (int): Annotation index
    ///
    /// Returns:
    ///     bool: True if annotation was removed
    fn remove_annotation(&mut self, index: usize) -> bool {
        self.inner.remove_annotation(index).is_some()
    }

    // === Element Manipulation ===

    /// Add a text element to the page.
    ///
    /// Args:
    ///     text (str): Text content
    ///     x (float): X coordinate
    ///     y (float): Y coordinate
    ///     font_size (float): Font size in points (default: 12.0)
    ///
    /// Returns:
    ///     PdfTextId: ID of the new element
    ///
    /// Example:
    ///     >>> text_id = page.add_text("Hello World", 100, 700, 14.0)
    #[pyo3(signature = (text, x, y, font_size=12.0))]
    fn add_text(&mut self, text: &str, x: f32, y: f32, font_size: f32) -> PyPdfTextId {
        use crate::elements::{FontSpec, TextContent, TextStyle};

        let content = TextContent {
            text: text.to_string(),
            bbox: crate::geometry::Rect::new(x, y, text.len() as f32 * font_size * 0.6, font_size),
            font: FontSpec {
                name: "Helvetica".to_string(),
                size: font_size,
            },
            style: TextStyle::default(),
            reading_order: None,
            origin: None,
            rotation_degrees: None,
            matrix: None,
        };

        let id = self.inner.add_text(content);
        PyPdfTextId { inner: id }
    }

    /// Remove an element by ID.
    ///
    /// Args:
    ///     element_id: Element ID (from PdfText.id, etc.)
    ///
    /// Returns:
    ///     bool: True if element was removed
    fn remove_element(&mut self, element_id: &PyPdfTextId) -> bool {
        self.inner.remove_element(element_id.inner)
    }

    /// String representation.
    fn __repr__(&self) -> String {
        format!(
            "PdfPage(index={}, width={:.1}, height={:.1})",
            self.inner.page_index, self.inner.width, self.inner.height
        )
    }
}

/// Python wrapper for text element ID.
///
/// Used to identify text elements for modification.
#[pyclass(name = "PdfTextId")]
#[derive(Clone)]
pub struct PyPdfTextId {
    inner: ElementId,
}

#[pymethods]
impl PyPdfTextId {
    fn __repr__(&self) -> String {
        format!("PdfTextId({:?})", self.inner)
    }
}

/// Python wrapper for text element.
///
/// Provides access to text content, position, and formatting.
///
/// Example:
///     >>> for text in page.find_text_containing("Hello"):
///     ...     print(f"{text.value} at {text.bbox}")
///     ...     print(f"Font: {text.font_name} {text.font_size}pt")
#[pyclass(name = "PdfText")]
#[derive(Clone)]
pub struct PyPdfText {
    inner: RustPdfText,
}

#[pymethods]
impl PyPdfText {
    /// Get the element ID.
    ///
    /// Returns:
    ///     PdfTextId: The unique element ID
    #[getter]
    fn id(&self) -> PyPdfTextId {
        PyPdfTextId {
            inner: self.inner.id(),
        }
    }

    /// Get the text content.
    ///
    /// Returns:
    ///     str: The text content
    #[getter]
    fn value(&self) -> String {
        self.inner.text().to_string()
    }

    /// Get the text content (alias for value).
    #[getter]
    fn text(&self) -> String {
        self.value()
    }

    /// Get the bounding box as (x, y, width, height).
    ///
    /// Returns:
    ///     tuple[float, float, float, float]: Bounding box coordinates
    #[getter]
    fn bbox(&self) -> (f32, f32, f32, f32) {
        let r = self.inner.bbox();
        (r.x, r.y, r.width, r.height)
    }

    /// Get the font name.
    ///
    /// Returns:
    ///     str: Font name
    #[getter]
    fn font_name(&self) -> String {
        self.inner.font_name().to_string()
    }

    /// Get the font size in points.
    ///
    /// Returns:
    ///     float: Font size
    #[getter]
    fn font_size(&self) -> f32 {
        self.inner.font_size()
    }

    /// Check if text is bold.
    ///
    /// Returns:
    ///     bool: True if bold
    #[getter]
    fn is_bold(&self) -> bool {
        self.inner.is_bold()
    }

    /// Check if text is italic.
    ///
    /// Returns:
    ///     bool: True if italic
    #[getter]
    fn is_italic(&self) -> bool {
        self.inner.is_italic()
    }

    /// Check if text contains a substring.
    ///
    /// Args:
    ///     needle (str): String to search for
    ///
    /// Returns:
    ///     bool: True if text contains needle
    fn contains(&self, needle: &str) -> bool {
        self.inner.contains(needle)
    }

    /// Check if text starts with a prefix.
    ///
    /// Args:
    ///     prefix (str): Prefix to check
    ///
    /// Returns:
    ///     bool: True if text starts with prefix
    fn starts_with(&self, prefix: &str) -> bool {
        self.inner.starts_with(prefix)
    }

    /// Check if text ends with a suffix.
    ///
    /// Args:
    ///     suffix (str): Suffix to check
    ///
    /// Returns:
    ///     bool: True if text ends with suffix
    fn ends_with(&self, suffix: &str) -> bool {
        self.inner.ends_with(suffix)
    }

    /// String representation.
    fn __repr__(&self) -> String {
        let text = self.inner.text();
        let preview = if text.len() > 30 {
            format!("{}...", &text[..30])
        } else {
            text.to_string()
        };
        format!("PdfText({:?})", preview)
    }
}

/// Python wrapper for image element.
#[pyclass(name = "PdfImage")]
#[derive(Clone)]
pub struct PyPdfImage {
    inner: crate::editor::PdfImage,
}

#[pymethods]
impl PyPdfImage {
    /// Get the bounding box as (x, y, width, height).
    #[getter]
    fn bbox(&self) -> (f32, f32, f32, f32) {
        let r = self.inner.bbox();
        (r.x, r.y, r.width, r.height)
    }

    /// Get image width in pixels.
    #[getter]
    fn width(&self) -> u32 {
        self.inner.dimensions().0
    }

    /// Get image height in pixels.
    #[getter]
    fn height(&self) -> u32 {
        self.inner.dimensions().1
    }

    /// Get aspect ratio (width / height).
    #[getter]
    fn aspect_ratio(&self) -> f32 {
        self.inner.aspect_ratio()
    }

    fn __repr__(&self) -> String {
        let (w, h) = self.inner.dimensions();
        format!("PdfImage({}x{})", w, h)
    }
}

/// Python wrapper for annotation.
#[pyclass(name = "PdfAnnotation")]
#[derive(Clone)]
pub struct PyAnnotationWrapper {
    inner: crate::editor::AnnotationWrapper,
}

#[pymethods]
impl PyAnnotationWrapper {
    /// Get the annotation subtype (e.g., "Link", "Highlight", "Text").
    #[getter]
    fn subtype(&self) -> String {
        format!("{:?}", self.inner.subtype())
    }

    /// Get the bounding rectangle as (x, y, width, height).
    #[getter]
    fn rect(&self) -> (f32, f32, f32, f32) {
        let r = self.inner.rect();
        (r.x, r.y, r.width, r.height)
    }

    /// Get the annotation contents/text if available.
    #[getter]
    fn contents(&self) -> Option<String> {
        self.inner.contents().map(|s| s.to_string())
    }

    /// Get the annotation color as (r, g, b) if available.
    #[getter]
    fn color(&self) -> Option<(f32, f32, f32)> {
        self.inner.color()
    }

    /// Check if this annotation has been modified.
    #[getter]
    fn is_modified(&self) -> bool {
        self.inner.is_modified()
    }

    /// Check if this is a new annotation (not loaded from PDF).
    #[getter]
    fn is_new(&self) -> bool {
        self.inner.is_new()
    }

    fn __repr__(&self) -> String {
        format!("PdfAnnotation(subtype={:?})", self.inner.subtype())
    }
}

/// Python wrapper for generic PDF element.
///
/// Can be one of: Text, Image, Path, Table, or Structure.
#[pyclass(name = "PdfElement")]
#[derive(Clone)]
pub struct PyPdfElement {
    inner: PdfElement,
}

#[pymethods]
impl PyPdfElement {
    /// Check if this is a text element.
    fn is_text(&self) -> bool {
        self.inner.is_text()
    }

    /// Check if this is an image element.
    fn is_image(&self) -> bool {
        self.inner.is_image()
    }

    /// Check if this is a path element.
    fn is_path(&self) -> bool {
        self.inner.is_path()
    }

    /// Check if this is a table element.
    fn is_table(&self) -> bool {
        self.inner.is_table()
    }

    /// Check if this is a structure element.
    fn is_structure(&self) -> bool {
        self.inner.is_structure()
    }

    /// Get as text element if this is a text element.
    ///
    /// Returns:
    ///     PdfText | None: The text element, or None if not a text element
    fn as_text(&self) -> Option<PyPdfText> {
        if let PdfElement::Text(t) = &self.inner {
            Some(PyPdfText { inner: t.clone() })
        } else {
            None
        }
    }

    /// Get as image element if this is an image element.
    ///
    /// Returns:
    ///     PdfImage | None: The image element, or None if not an image element
    fn as_image(&self) -> Option<PyPdfImage> {
        if let PdfElement::Image(i) = &self.inner {
            Some(PyPdfImage { inner: i.clone() })
        } else {
            None
        }
    }

    /// Get the bounding box.
    #[getter]
    fn bbox(&self) -> (f32, f32, f32, f32) {
        let r = self.inner.bbox();
        (r.x, r.y, r.width, r.height)
    }

    fn __repr__(&self) -> String {
        match &self.inner {
            PdfElement::Text(t) => format!("PdfElement::Text({:?})", t.text()),
            PdfElement::Image(i) => {
                format!("PdfElement::Image({}x{})", i.dimensions().0, i.dimensions().1)
            },
            PdfElement::Path(_) => "PdfElement::Path(...)".to_string(),
            PdfElement::Table(t) => {
                format!("PdfElement::Table({}x{})", t.row_count(), t.column_count())
            },
            PdfElement::Structure(s) => {
                format!("PdfElement::Structure({:?})", s.structure_type())
            },
        }
    }
}

// === Text Extraction Types ===

/// A single character with its position and styling information.
///
/// Low-level character extraction result containing position, font, and style data
/// for each character in a PDF page. Use `extract_chars()` to get a list of these.
///
/// # Attributes
///
/// - `char` (str): The character itself
/// - `bbox` (tuple): Bounding box as (x, y, width, height)
/// - `font_name` (str): Font family name
/// - `font_size` (float): Font size in points
/// - `font_weight` (str): "normal", "bold", "light", etc.
/// - `is_italic` (bool): Whether the character is italic
/// - `color` (tuple): RGB color as (r, g, b) with values 0.0-1.0
#[pyclass(name = "TextChar")]
#[derive(Clone)]
pub struct PyTextChar {
    inner: RustTextChar,
}

#[pymethods]
impl PyTextChar {
    /// The character itself.
    #[getter]
    fn char(&self) -> char {
        self.inner.char
    }

    /// Bounding box of the character.
    ///
    /// Returns:
    ///     tuple[float, float, float, float]: (x, y, width, height)
    #[getter]
    fn bbox(&self) -> (f32, f32, f32, f32) {
        (
            self.inner.bbox.x,
            self.inner.bbox.y,
            self.inner.bbox.width,
            self.inner.bbox.height,
        )
    }

    /// Font name/family.
    #[getter]
    fn font_name(&self) -> String {
        self.inner.font_name.clone()
    }

    /// Font size in points.
    #[getter]
    fn font_size(&self) -> f32 {
        self.inner.font_size
    }

    /// Font weight as a string.
    ///
    /// Returns:
    ///     str: "normal" or "bold"
    #[getter]
    fn font_weight(&self) -> String {
        match self.inner.font_weight {
            FontWeight::Thin => "thin".to_string(),
            FontWeight::ExtraLight => "extra-light".to_string(),
            FontWeight::Light => "light".to_string(),
            FontWeight::Normal => "normal".to_string(),
            FontWeight::Medium => "medium".to_string(),
            FontWeight::SemiBold => "semi-bold".to_string(),
            FontWeight::Bold => "bold".to_string(),
            FontWeight::ExtraBold => "extra-bold".to_string(),
            FontWeight::Black => "black".to_string(),
        }
    }

    /// Whether the character is italic.
    #[getter]
    fn is_italic(&self) -> bool {
        self.inner.is_italic
    }

    /// Text color as RGB tuple.
    ///
    /// Returns:
    ///     tuple: (r, g, b) with values 0.0-1.0
    #[getter]
    fn color(&self) -> (f32, f32, f32) {
        (self.inner.color.r, self.inner.color.g, self.inner.color.b)
    }

    /// Text rotation angle in degrees.
    #[getter]
    fn rotation_degrees(&self) -> f32 {
        self.inner.rotation_degrees
    }

    /// Baseline X position.
    #[getter]
    fn origin_x(&self) -> f32 {
        self.inner.origin_x
    }

    /// Baseline Y position.
    #[getter]
    fn origin_y(&self) -> f32 {
        self.inner.origin_y
    }

    /// Horizontal distance to next character.
    #[getter]
    fn advance_width(&self) -> f32 {
        self.inner.advance_width
    }

    /// Marked Content ID (for Tagged PDFs).
    ///
    /// Returns:
    ///     int | None: MCID if available, None otherwise
    #[getter]
    fn mcid(&self) -> Option<u32> {
        self.inner.mcid
    }

    fn __repr__(&self) -> String {
        format!(
            "TextChar('{}' at ({:.1}, {:.1}), {}pt {})",
            self.inner.char,
            self.inner.bbox.x,
            self.inner.bbox.y,
            self.inner.font_size as i32,
            self.inner.font_name
        )
    }
}

// === Text Span Type ===

/// A text span with position and style information.
///
/// Spans are groups of characters that share the same font and style.
/// Use `PdfDocument.extract_spans()` to get a list of these.
///
/// # Attributes
///
/// - `text` (str): The text content
/// - `bbox` (tuple): Bounding box as (x, y, width, height)
/// - `font_name` (str): Font family name
/// - `font_size` (float): Font size in points
/// - `is_bold` (bool): Whether the text is bold
/// - `is_italic` (bool): Whether the text is italic
/// - `color` (tuple): RGB color as (r, g, b) with values 0.0-1.0
#[pyclass(name = "TextSpan")]
#[derive(Clone)]
pub struct PyTextSpan {
    inner: crate::layout::TextSpan,
}

#[pymethods]
impl PyTextSpan {
    /// The text content of the span.
    #[getter]
    fn text(&self) -> &str {
        &self.inner.text
    }

    /// Bounding box as (x, y, width, height).
    #[getter]
    fn bbox(&self) -> (f32, f32, f32, f32) {
        (
            self.inner.bbox.x,
            self.inner.bbox.y,
            self.inner.bbox.width,
            self.inner.bbox.height,
        )
    }

    /// Font name/family.
    #[getter]
    fn font_name(&self) -> &str {
        &self.inner.font_name
    }

    /// Font size in points.
    #[getter]
    fn font_size(&self) -> f32 {
        self.inner.font_size
    }

    /// Whether the text is bold (font weight >= 700).
    #[getter]
    fn is_bold(&self) -> bool {
        self.inner.font_weight as u16 >= 700
    }

    /// Whether the text is italic.
    #[getter]
    fn is_italic(&self) -> bool {
        self.inner.is_italic
    }

    /// Text color as (r, g, b) with values 0.0-1.0.
    #[getter]
    fn color(&self) -> (f32, f32, f32) {
        (self.inner.color.r, self.inner.color.g, self.inner.color.b)
    }

    /// Text origin point (x, y) — the baseline start position.
    #[getter]
    fn origin(&self) -> (f32, f32) {
        (self.inner.bbox.x, self.inner.bbox.y + self.inner.bbox.height)
    }

    /// Character spacing (Tc parameter). Default 0.
    #[getter]
    fn char_spacing(&self) -> f32 {
        self.inner.char_spacing
    }

    /// Word spacing (Tw parameter). Default 0.
    #[getter]
    fn word_spacing(&self) -> f32 {
        self.inner.word_spacing
    }

    /// Horizontal scaling (Tz parameter, percentage). Default 100.
    #[getter]
    fn horizontal_scaling(&self) -> f32 {
        self.inner.horizontal_scaling
    }

    fn __repr__(&self) -> String {
        let preview = if self.inner.text.len() > 30 {
            format!("{}...", &self.inner.text[..30])
        } else {
            self.inner.text.clone()
        };
        format!(
            "TextSpan({:?}, font={}, size={:.1})",
            preview, self.inner.font_name, self.inner.font_size
        )
    }
}

/// A word extracted from a PDF page (v0.3.14).
#[pyclass(name = "TextWord")]
#[derive(Clone)]
pub struct PyWord {
    inner: crate::layout::Word,
}

#[pymethods]
impl PyWord {
    #[getter]
    fn text(&self) -> String {
        self.inner.text.clone()
    }

    #[getter]
    fn bbox(&self) -> (f32, f32, f32, f32) {
        (
            self.inner.bbox.x,
            self.inner.bbox.y,
            self.inner.bbox.width,
            self.inner.bbox.height,
        )
    }

    #[getter]
    fn font_name(&self) -> String {
        self.inner.dominant_font.clone()
    }

    #[getter]
    fn font_size(&self) -> f32 {
        self.inner.avg_font_size
    }

    #[getter]
    fn is_bold(&self) -> bool {
        self.inner.is_bold
    }

    #[getter]
    fn is_italic(&self) -> bool {
        self.inner.is_italic
    }

    /// Individual characters that make up this word.
    #[getter]
    fn chars(&self) -> Vec<PyTextChar> {
        self.inner
            .chars
            .iter()
            .map(|c| PyTextChar { inner: c.clone() })
            .collect()
    }

    fn __repr__(&self) -> String {
        format!("TextWord({:?})", self.inner.text)
    }
}

/// A line of text extracted from a PDF page (v0.3.14).
#[pyclass(name = "TextLine")]
#[derive(Clone)]
pub struct PyTextLine {
    inner: crate::layout::TextLine,
}

#[pymethods]
impl PyTextLine {
    #[getter]
    fn text(&self) -> String {
        self.inner.text.clone()
    }

    #[getter]
    fn bbox(&self) -> (f32, f32, f32, f32) {
        (
            self.inner.bbox.x,
            self.inner.bbox.y,
            self.inner.bbox.width,
            self.inner.bbox.height,
        )
    }

    #[getter]
    fn words(&self) -> Vec<PyWord> {
        self.inner
            .words
            .iter()
            .map(|w| PyWord { inner: w.clone() })
            .collect()
    }

    /// Individual characters that make up this line.
    #[getter]
    fn chars(&self) -> Vec<PyTextChar> {
        self.inner
            .words
            .iter()
            .flat_map(|w| w.chars.iter().map(|c| PyTextChar { inner: c.clone() }))
            .collect()
    }

    fn __repr__(&self) -> String {
        format!("TextLine({:?})", self.inner.text)
    }
}

/// Convert HeaderValidation to a Python dictionary with disposition and features.
fn header_validation_to_dict<'py>(
    py: Python<'py>,
    hv: &crate::extractors::block_classifier::HeaderValidation,
) -> PyResult<pyo3::Bound<'py, pyo3::types::PyDict>> {
    use crate::extractors::block_classifier::HeaderDisposition;
    let dict = pyo3::types::PyDict::new(py);
    dict.set_item("is_header", hv.is_header)?;
    dict.set_item("confidence", hv.confidence)?;
    dict.set_item("disposition", match hv.disposition {
        HeaderDisposition::Accept => "Accept",
        HeaderDisposition::Reject => "Reject",
        HeaderDisposition::Escalate => "Escalate",
    })?;
    dict.set_item("level", hv.level)?;
    let reasons: Vec<&str> = hv.reasons.iter().copied().collect();
    dict.set_item("reasons", reasons)?;
    dict.set_item("has_numbering", hv.numbering.has_numbering)?;
    dict.set_item("number_text", &hv.numbering.number_text)?;
    dict.set_item("title_text", &hv.numbering.title_text)?;
    dict.set_item("depth_level", hv.numbering.depth_level)?;

    // Features dict for classifier input
    let feat = pyo3::types::PyDict::new(py);
    feat.set_item("text_len", hv.features.text_len)?;
    feat.set_item("has_number_prefix", hv.features.has_number_prefix)?;
    feat.set_item("font_size", hv.features.font_size)?;
    feat.set_item("size_ratio", hv.features.size_ratio)?;
    feat.set_item("is_bold", hv.features.is_bold)?;
    feat.set_item("ends_with_period", hv.features.ends_with_period)?;
    feat.set_item("ends_with_colon", hv.features.ends_with_colon)?;
    feat.set_item("ends_with_other_punct", hv.features.ends_with_other_punct)?;
    feat.set_item("has_bullet_char", hv.features.has_bullet_char)?;
    feat.set_item("is_caption_pattern", hv.features.is_caption_pattern)?;
    feat.set_item("is_multi_sentence", hv.features.is_multi_sentence)?;
    feat.set_item("word_count", hv.features.word_count)?;
    feat.set_item("title_case_ratio", hv.features.title_case_ratio)?;
    feat.set_item("is_all_caps", hv.features.is_all_caps)?;
    feat.set_item("numbering_depth", hv.features.numbering_depth)?;
    feat.set_item("has_formal_prefix", hv.features.has_formal_prefix)?;
    feat.set_item("has_parentheses", hv.features.has_parentheses)?;
    feat.set_item("is_too_long", hv.features.is_too_long)?;
    dict.set_item("features", feat)?;

    Ok(dict)
}

/// Convert PathContent to a Python dictionary.
fn path_to_py_dict(py: Python<'_>, path: &crate::elements::PathContent) -> PyResult<Py<PyAny>> {
    let dict = pyo3::types::PyDict::new(py);
    dict.set_item("bbox", (path.bbox.x, path.bbox.y, path.bbox.width, path.bbox.height))?;
    dict.set_item("stroke_width", path.stroke_width)?;

    if let Some(ref color) = path.stroke_color {
        dict.set_item("stroke_color", (color.r, color.g, color.b))?;
    } else {
        dict.set_item("stroke_color", py.None())?;
    }

    if let Some(ref color) = path.fill_color {
        dict.set_item("fill_color", (color.r, color.g, color.b))?;
    } else {
        dict.set_item("fill_color", py.None())?;
    }

    let cap_str = match path.line_cap {
        crate::elements::LineCap::Butt => "butt",
        crate::elements::LineCap::Round => "round",
        crate::elements::LineCap::Square => "square",
    };
    dict.set_item("line_cap", cap_str)?;

    let join_str = match path.line_join {
        crate::elements::LineJoin::Miter => "miter",
        crate::elements::LineJoin::Round => "round",
        crate::elements::LineJoin::Bevel => "bevel",
    };
    dict.set_item("line_join", join_str)?;

    dict.set_item("operations_count", path.operations.len())?;

    Ok(dict.into())
}

/// Convert Python table_settings dict to TableDetectionConfig.
fn table_settings_to_config(
    settings: Option<Bound<'_, pyo3::types::PyDict>>,
) -> PyResult<crate::structure::spatial_table_detector::TableDetectionConfig> {
    use crate::structure::spatial_table_detector::{TableDetectionConfig, TableStrategy};
    let mut config = TableDetectionConfig::relaxed();

    if let Some(dict) = settings {
        if let Some(val) = dict.get_item("horizontal_strategy")? {
            let s: String = val.extract()?;
            config.horizontal_strategy = match s.as_str() {
                "lines" => TableStrategy::Lines,
                "text" => TableStrategy::Text,
                "both" => TableStrategy::Both,
                _ => {
                    return Err(PyRuntimeError::new_err(format!(
                        "Invalid horizontal_strategy: {}",
                        s
                    )))
                },
            };
        }
        if let Some(val) = dict.get_item("vertical_strategy")? {
            let s: String = val.extract()?;
            config.vertical_strategy = match s.as_str() {
                "lines" => TableStrategy::Lines,
                "text" => TableStrategy::Text,
                "both" => TableStrategy::Both,
                _ => {
                    return Err(PyRuntimeError::new_err(format!(
                        "Invalid vertical_strategy: {}",
                        s
                    )))
                },
            };
        }
        if let Some(val) = dict.get_item("column_tolerance")? {
            config.column_tolerance = val.extract()?;
        }
        if let Some(val) = dict.get_item("row_tolerance")? {
            config.row_tolerance = val.extract()?;
        }
        if let Some(val) = dict.get_item("min_table_cells")? {
            config.min_table_cells = val.extract()?;
        }
        if let Some(val) = dict.get_item("min_table_columns")? {
            config.min_table_columns = val.extract()?;
        }
    }

    Ok(config)
}

// === Camelot-style Table Helpers ===

/// Parse a page range string like "1-5", "1,3,5", "all" into 0-indexed page numbers.
fn parse_page_range(s: &str, page_count: usize) -> PyResult<Vec<usize>> {
    let s = s.trim();
    if s == "all" || s.is_empty() {
        return Ok((0..page_count).collect());
    }

    let mut pages = Vec::new();
    for part in s.split(',') {
        let part = part.trim();
        if let Some((start, end)) = part.split_once('-') {
            let start: usize = start.trim().parse::<usize>()
                .map_err(|_| PyRuntimeError::new_err(format!("Invalid page number: '{}'", start)))?;
            let end: usize = end.trim().parse::<usize>()
                .map_err(|_| PyRuntimeError::new_err(format!("Invalid page number: '{}'", end)))?;
            // Input is 1-indexed, convert to 0-indexed
            if start == 0 || end == 0 {
                return Err(PyRuntimeError::new_err("Page numbers are 1-indexed"));
            }
            for p in start..=end {
                if p <= page_count {
                    pages.push(p - 1);
                }
            }
        } else {
            let p: usize = part.parse::<usize>()
                .map_err(|_| PyRuntimeError::new_err(format!("Invalid page number: '{}'", part)))?;
            if p == 0 {
                return Err(PyRuntimeError::new_err("Page numbers are 1-indexed"));
            }
            if p <= page_count {
                pages.push(p - 1);
            }
        }
    }
    Ok(pages)
}

/// Convert a tables::Table to a Python dict.
fn table_to_pydict<'py>(
    py: Python<'py>,
    table: &crate::tables::Table,
) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
    let dict = pyo3::types::PyDict::new(py);
    dict.set_item("page", table.page)?;
    dict.set_item("order", table.order)?;
    dict.set_item("flavor", format!("{:?}", table.flavor))?;
    dict.set_item("rows", table.num_rows())?;
    dict.set_item("cols", table.num_cols())?;
    dict.set_item("accuracy", table.accuracy)?;
    dict.set_item("whitespace", table.whitespace)?;

    // Cell data as 2D list of strings
    let data = table.data();
    let py_rows = pyo3::types::PyList::empty(py);
    for row in &data {
        let py_row = pyo3::types::PyList::new(py, row)?;
        py_rows.append(py_row)?;
    }
    dict.set_item("data", py_rows)?;

    // DataFrame-compatible: list of dicts (one per row)
    // Useful for: pd.DataFrame(table["df_data"])
    if !data.is_empty() {
        let headers: Vec<String> = (0..table.num_cols()).map(|i| format!("{}", i)).collect();
        let df_rows = pyo3::types::PyList::empty(py);
        for row in &data {
            let row_dict = pyo3::types::PyDict::new(py);
            for (i, cell) in row.iter().enumerate() {
                row_dict.set_item(&headers[i], cell)?;
            }
            df_rows.append(row_dict)?;
        }
        dict.set_item("df_data", df_rows)?;
    }

    Ok(dict)
}

// === Outline Helper ===

/// Convert OutlineItem tree to Python nested dicts.
fn outline_items_to_py(
    py: Python<'_>,
    items: &[crate::outline::OutlineItem],
) -> PyResult<Py<PyAny>> {
    let py_list = pyo3::types::PyList::empty(py);
    for item in items {
        let dict = pyo3::types::PyDict::new(py);
        dict.set_item("title", &item.title)?;

        match &item.dest {
            Some(crate::outline::Destination::PageIndex(idx)) => {
                dict.set_item("page", *idx)?;
            },
            Some(crate::outline::Destination::Named(name)) => {
                dict.set_item("page", py.None())?;
                dict.set_item("dest_name", name)?;
            },
            None => {
                dict.set_item("page", py.None())?;
            },
        }

        let children = outline_items_to_py(py, &item.children)?;
        dict.set_item("children", children)?;

        py_list.append(dict)?;
    }
    Ok(py_list.into())
}

// === OCR Types (feature-gated) ===

/// OCR engine for extracting text from scanned PDF pages.
///
/// Requires the `ocr` feature to be enabled at build time.
///
/// Example:
///     >>> engine = OcrEngine("det.onnx", "rec.onnx", "dict.txt")
///     >>> text = doc.extract_text_ocr(0, engine)
#[cfg(feature = "ocr")]
#[pyclass(name = "OcrEngine", unsendable)]
pub struct PyOcrEngine {
    inner: crate::ocr::OcrEngine,
}

#[cfg(not(feature = "ocr"))]
#[pyclass(name = "OcrEngine", unsendable)]
pub struct PyOcrEngine {}

#[cfg(not(feature = "ocr"))]
#[pymethods]
impl PyOcrEngine {
    #[new]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn new(_args: &Bound<'_, PyTuple>, _kwargs: Option<Bound<'_, PyDict>>) -> PyResult<Self> {
        Err(PyRuntimeError::new_err("OCR feature not enabled. Please install with 'pip install pdf_oxide[ocr]' or build with --features ocr"))
    }
}

#[cfg(feature = "ocr")]
#[pymethods]
impl PyOcrEngine {
    /// Create a new OCR engine.
    ///
    /// Args:
    ///     det_model_path (str): Path to the text detection ONNX model
    ///     rec_model_path (str): Path to the text recognition ONNX model
    ///     dict_path (str): Path to the character dictionary file
    ///     config (OcrConfig | None): Optional OCR configuration
    ///
    /// Raises:
    ///     RuntimeError: If model loading fails
    ///
    /// Example:
    ///     >>> engine = OcrEngine("det.onnx", "rec.onnx", "dict.txt")
    ///     >>> engine_custom = OcrEngine("det.onnx", "rec.onnx", "dict.txt",
    ///     ...     OcrConfig(det_threshold=0.5))
    #[new]
    #[pyo3(signature = (det_model_path, rec_model_path, dict_path, config=None))]
    fn new(
        det_model_path: &str,
        rec_model_path: &str,
        dict_path: &str,
        config: Option<&PyOcrConfig>,
    ) -> PyResult<Self> {
        let ocr_config = config.map(|c| c.inner.clone()).unwrap_or_default();
        let engine =
            crate::ocr::OcrEngine::new(det_model_path, rec_model_path, dict_path, ocr_config)
                .map_err(|e| {
                    PyRuntimeError::new_err(format!("Failed to create OCR engine: {}", e))
                })?;
        Ok(PyOcrEngine { inner: engine })
    }

    fn __repr__(&self) -> String {
        "OcrEngine(...)".to_string()
    }
}

/// Configuration for OCR processing.
///
/// All parameters are optional and have sensible defaults.
///
/// Example:
///     >>> config = OcrConfig(det_threshold=0.5, num_threads=8)
///     >>> engine = OcrEngine("det.onnx", "rec.onnx", "dict.txt", config)
#[cfg(feature = "ocr")]
#[pyclass(name = "OcrConfig")]
#[derive(Clone)]
pub struct PyOcrConfig {
    inner: crate::ocr::OcrConfig,
}

#[cfg(not(feature = "ocr"))]
#[pyclass(name = "OcrConfig")]
#[derive(Clone)]
pub struct PyOcrConfig {}

#[cfg(not(feature = "ocr"))]
#[pymethods]
impl PyOcrConfig {
    #[new]
    #[pyo3(signature = (**_kwargs))]
    fn new(_kwargs: Option<Bound<'_, PyDict>>) -> PyResult<Self> {
        Err(PyRuntimeError::new_err("OCR feature not enabled. Please install with 'pip install pdf_oxide[ocr]' or build with --features ocr"))
    }
}

#[cfg(feature = "ocr")]
#[pymethods]
impl PyOcrConfig {
    /// Create OCR configuration with optional parameters.
    ///
    /// Args:
    ///     det_threshold (float): Detection threshold (0.0-1.0, default: 0.3)
    ///     box_threshold (float): Box threshold (0.0-1.0, default: 0.6)
    ///     rec_threshold (float): Recognition threshold (0.0-1.0, default: 0.5)
    ///     num_threads (int): Number of threads (default: 4)
    ///     max_candidates (int): Max text candidates (default: 1000)
    ///     use_v5 (bool): Use PP-OCRv5 optimized settings (default: False).
    ///         When True, uses high-resolution input for detection (up to 4000px)
    ///         which is required for PP-OCRv5 server models.
    #[new]
    #[pyo3(signature = (det_threshold=None, box_threshold=None, rec_threshold=None, num_threads=None, max_candidates=None, use_v5=false))]
    fn new(
        det_threshold: Option<f32>,
        box_threshold: Option<f32>,
        rec_threshold: Option<f32>,
        num_threads: Option<usize>,
        max_candidates: Option<usize>,
        use_v5: bool,
    ) -> Self {
        let mut config = if use_v5 {
            crate::ocr::OcrConfig::v5()
        } else {
            crate::ocr::OcrConfig::default()
        };
        if let Some(v) = det_threshold {
            config.det_threshold = v;
        }
        if let Some(v) = box_threshold {
            config.box_threshold = v;
        }
        if let Some(v) = rec_threshold {
            config.rec_threshold = v;
        }
        if let Some(v) = num_threads {
            config.num_threads = v;
        }
        if let Some(v) = max_candidates {
            config.max_candidates = v;
        }
        PyOcrConfig { inner: config }
    }

    fn __repr__(&self) -> String {
        format!(
            "OcrConfig(det_threshold={}, rec_threshold={}, threads={})",
            self.inner.det_threshold, self.inner.rec_threshold, self.inner.num_threads
        )
    }
}

// === Advanced Graphics Types ===

use crate::layout::{Color as RustColor, FontWeight, TextChar as RustTextChar};
use crate::writer::{
    BlendMode as RustBlendMode, LineCap as RustLineCap, LineJoin as RustLineJoin,
    PatternPresets as RustPatternPresets,
};

/// RGB Color for PDF graphics.
///
/// Example:
///     >>> color = Color(1.0, 0.0, 0.0)  # Red
///     >>> color = Color.red()
///     >>> color = Color.from_hex("#FF0000")
#[pyclass(name = "Color")]
#[derive(Clone)]
pub struct PyColor {
    inner: RustColor,
}

#[pymethods]
impl PyColor {
    /// Create a new RGB color.
    ///
    /// Args:
    ///     r (float): Red component (0.0 to 1.0)
    ///     g (float): Green component (0.0 to 1.0)
    ///     b (float): Blue component (0.0 to 1.0)
    #[new]
    fn new(r: f32, g: f32, b: f32) -> Self {
        PyColor {
            inner: RustColor::new(r, g, b),
        }
    }

    /// Create color from hex string.
    ///
    /// Args:
    ///     hex_str (str): Hex color like "#FF0000" or "FF0000"
    ///
    /// Example:
    ///     >>> red = Color.from_hex("#FF0000")
    #[staticmethod]
    fn from_hex(hex_str: &str) -> PyResult<Self> {
        let hex = hex_str.trim_start_matches('#');
        if hex.len() != 6 {
            return Err(PyRuntimeError::new_err("Invalid hex color format"));
        }
        let r = u8::from_str_radix(&hex[0..2], 16)
            .map_err(|_| PyRuntimeError::new_err("Invalid hex color"))?;
        let g = u8::from_str_radix(&hex[2..4], 16)
            .map_err(|_| PyRuntimeError::new_err("Invalid hex color"))?;
        let b = u8::from_str_radix(&hex[4..6], 16)
            .map_err(|_| PyRuntimeError::new_err("Invalid hex color"))?;
        Ok(PyColor {
            inner: RustColor::new(r as f32 / 255.0, g as f32 / 255.0, b as f32 / 255.0),
        })
    }

    /// Black color.
    #[staticmethod]
    fn black() -> Self {
        PyColor {
            inner: RustColor::black(),
        }
    }

    /// White color.
    #[staticmethod]
    fn white() -> Self {
        PyColor {
            inner: RustColor::white(),
        }
    }

    /// Red color.
    #[staticmethod]
    fn red() -> Self {
        PyColor {
            inner: RustColor::new(1.0, 0.0, 0.0),
        }
    }

    /// Green color.
    #[staticmethod]
    fn green() -> Self {
        PyColor {
            inner: RustColor::new(0.0, 1.0, 0.0),
        }
    }

    /// Blue color.
    #[staticmethod]
    fn blue() -> Self {
        PyColor {
            inner: RustColor::new(0.0, 0.0, 1.0),
        }
    }

    /// Get red component.
    #[getter]
    fn r(&self) -> f32 {
        self.inner.r
    }

    /// Get green component.
    #[getter]
    fn g(&self) -> f32 {
        self.inner.g
    }

    /// Get blue component.
    #[getter]
    fn b(&self) -> f32 {
        self.inner.b
    }

    fn __repr__(&self) -> String {
        format!("Color({}, {}, {})", self.inner.r, self.inner.g, self.inner.b)
    }
}

/// Blend modes for transparency effects.
///
/// Example:
///     >>> gs = ExtGState().blend_mode(BlendMode.MULTIPLY)
#[pyclass(name = "BlendMode")]
#[derive(Clone)]
pub struct PyBlendMode {
    inner: RustBlendMode,
}

#[pymethods]
impl PyBlendMode {
    /// Normal blend mode (default).
    #[staticmethod]
    #[allow(non_snake_case)]
    fn NORMAL() -> Self {
        PyBlendMode {
            inner: RustBlendMode::Normal,
        }
    }

    /// Multiply blend mode.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn MULTIPLY() -> Self {
        PyBlendMode {
            inner: RustBlendMode::Multiply,
        }
    }

    /// Screen blend mode.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn SCREEN() -> Self {
        PyBlendMode {
            inner: RustBlendMode::Screen,
        }
    }

    /// Overlay blend mode.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn OVERLAY() -> Self {
        PyBlendMode {
            inner: RustBlendMode::Overlay,
        }
    }

    /// Darken blend mode.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn DARKEN() -> Self {
        PyBlendMode {
            inner: RustBlendMode::Darken,
        }
    }

    /// Lighten blend mode.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn LIGHTEN() -> Self {
        PyBlendMode {
            inner: RustBlendMode::Lighten,
        }
    }

    /// Color dodge blend mode.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn COLOR_DODGE() -> Self {
        PyBlendMode {
            inner: RustBlendMode::ColorDodge,
        }
    }

    /// Color burn blend mode.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn COLOR_BURN() -> Self {
        PyBlendMode {
            inner: RustBlendMode::ColorBurn,
        }
    }

    /// Hard light blend mode.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn HARD_LIGHT() -> Self {
        PyBlendMode {
            inner: RustBlendMode::HardLight,
        }
    }

    /// Soft light blend mode.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn SOFT_LIGHT() -> Self {
        PyBlendMode {
            inner: RustBlendMode::SoftLight,
        }
    }

    /// Difference blend mode.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn DIFFERENCE() -> Self {
        PyBlendMode {
            inner: RustBlendMode::Difference,
        }
    }

    /// Exclusion blend mode.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn EXCLUSION() -> Self {
        PyBlendMode {
            inner: RustBlendMode::Exclusion,
        }
    }

    fn __repr__(&self) -> String {
        format!("BlendMode.{}", self.inner.as_pdf_name())
    }
}

/// Extended Graphics State for transparency and blend effects.
///
/// Example:
///     >>> gs = ExtGState().alpha(0.5).blend_mode(BlendMode.MULTIPLY)
#[pyclass(name = "ExtGState")]
#[derive(Clone)]
pub struct PyExtGState {
    fill_alpha: Option<f32>,
    stroke_alpha: Option<f32>,
    blend_mode: Option<RustBlendMode>,
}

#[pymethods]
impl PyExtGState {
    /// Create a new ExtGState builder.
    #[new]
    fn new() -> Self {
        PyExtGState {
            fill_alpha: None,
            stroke_alpha: None,
            blend_mode: None,
        }
    }

    /// Set fill opacity (0.0 = transparent, 1.0 = opaque).
    fn fill_alpha(&self, alpha: f32) -> Self {
        PyExtGState {
            fill_alpha: Some(alpha.clamp(0.0, 1.0)),
            stroke_alpha: self.stroke_alpha,
            blend_mode: self.blend_mode,
        }
    }

    /// Set stroke opacity (0.0 = transparent, 1.0 = opaque).
    fn stroke_alpha(&self, alpha: f32) -> Self {
        PyExtGState {
            fill_alpha: self.fill_alpha,
            stroke_alpha: Some(alpha.clamp(0.0, 1.0)),
            blend_mode: self.blend_mode,
        }
    }

    /// Set both fill and stroke opacity.
    fn alpha(&self, alpha: f32) -> Self {
        let a = alpha.clamp(0.0, 1.0);
        PyExtGState {
            fill_alpha: Some(a),
            stroke_alpha: Some(a),
            blend_mode: self.blend_mode,
        }
    }

    /// Set blend mode.
    fn blend_mode(&self, mode: &PyBlendMode) -> Self {
        PyExtGState {
            fill_alpha: self.fill_alpha,
            stroke_alpha: self.stroke_alpha,
            blend_mode: Some(mode.inner),
        }
    }

    /// Create semi-transparent state (50% opacity).
    #[staticmethod]
    fn semi_transparent() -> Self {
        PyExtGState {
            fill_alpha: Some(0.5),
            stroke_alpha: Some(0.5),
            blend_mode: None,
        }
    }

    fn __repr__(&self) -> String {
        let mut parts = Vec::new();
        if let Some(a) = self.fill_alpha {
            parts.push(format!("fill_alpha={}", a));
        }
        if let Some(a) = self.stroke_alpha {
            parts.push(format!("stroke_alpha={}", a));
        }
        if let Some(ref m) = self.blend_mode {
            parts.push(format!("blend_mode={}", m.as_pdf_name()));
        }
        format!("ExtGState({})", parts.join(", "))
    }
}

/// Linear gradient builder.
///
/// Example:
///     >>> gradient = LinearGradient() \
///     ...     .start(0, 0).end(100, 100) \
///     ...     .add_stop(0.0, Color.red()) \
///     ...     .add_stop(1.0, Color.blue())
#[pyclass(name = "LinearGradient")]
#[derive(Clone)]
pub struct PyLinearGradient {
    start: (f32, f32),
    end: (f32, f32),
    stops: Vec<(f32, RustColor)>,
    extend_start: bool,
    extend_end: bool,
}

#[pymethods]
impl PyLinearGradient {
    /// Create a new linear gradient.
    #[new]
    fn new() -> Self {
        PyLinearGradient {
            start: (0.0, 0.0),
            end: (100.0, 0.0),
            stops: Vec::new(),
            extend_start: true,
            extend_end: true,
        }
    }

    /// Set start point.
    fn start(&self, x: f32, y: f32) -> Self {
        PyLinearGradient {
            start: (x, y),
            end: self.end,
            stops: self.stops.clone(),
            extend_start: self.extend_start,
            extend_end: self.extend_end,
        }
    }

    /// Set end point.
    fn end(&self, x: f32, y: f32) -> Self {
        PyLinearGradient {
            start: self.start,
            end: (x, y),
            stops: self.stops.clone(),
            extend_start: self.extend_start,
            extend_end: self.extend_end,
        }
    }

    /// Add a color stop.
    ///
    /// Args:
    ///     position (float): Position along gradient (0.0 to 1.0)
    ///     color (Color): Color at this position
    fn add_stop(&self, position: f32, color: &PyColor) -> Self {
        let mut stops = self.stops.clone();
        stops.push((position.clamp(0.0, 1.0), color.inner));
        PyLinearGradient {
            start: self.start,
            end: self.end,
            stops,
            extend_start: self.extend_start,
            extend_end: self.extend_end,
        }
    }

    /// Set whether to extend gradient beyond endpoints.
    fn extend(&self, extend: bool) -> Self {
        PyLinearGradient {
            start: self.start,
            end: self.end,
            stops: self.stops.clone(),
            extend_start: extend,
            extend_end: extend,
        }
    }

    /// Create a horizontal gradient.
    #[staticmethod]
    fn horizontal(width: f32, start_color: &PyColor, end_color: &PyColor) -> Self {
        PyLinearGradient {
            start: (0.0, 0.0),
            end: (width, 0.0),
            stops: vec![(0.0, start_color.inner), (1.0, end_color.inner)],
            extend_start: true,
            extend_end: true,
        }
    }

    /// Create a vertical gradient.
    #[staticmethod]
    fn vertical(height: f32, start_color: &PyColor, end_color: &PyColor) -> Self {
        PyLinearGradient {
            start: (0.0, 0.0),
            end: (0.0, height),
            stops: vec![(0.0, start_color.inner), (1.0, end_color.inner)],
            extend_start: true,
            extend_end: true,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "LinearGradient(({}, {}) -> ({}, {}), {} stops)",
            self.start.0,
            self.start.1,
            self.end.0,
            self.end.1,
            self.stops.len()
        )
    }
}

/// Radial gradient builder.
///
/// Example:
///     >>> gradient = RadialGradient.centered(50, 50, 50) \
///     ...     .add_stop(0.0, Color.white()) \
///     ...     .add_stop(1.0, Color.black())
#[pyclass(name = "RadialGradient")]
#[derive(Clone)]
pub struct PyRadialGradient {
    inner_center: (f32, f32),
    inner_radius: f32,
    outer_center: (f32, f32),
    outer_radius: f32,
    stops: Vec<(f32, RustColor)>,
}

#[pymethods]
impl PyRadialGradient {
    /// Create a new radial gradient.
    #[new]
    fn new() -> Self {
        PyRadialGradient {
            inner_center: (50.0, 50.0),
            inner_radius: 0.0,
            outer_center: (50.0, 50.0),
            outer_radius: 50.0,
            stops: Vec::new(),
        }
    }

    /// Create a centered radial gradient.
    #[staticmethod]
    fn centered(cx: f32, cy: f32, radius: f32) -> Self {
        PyRadialGradient {
            inner_center: (cx, cy),
            inner_radius: 0.0,
            outer_center: (cx, cy),
            outer_radius: radius,
            stops: Vec::new(),
        }
    }

    /// Set inner circle.
    fn inner_circle(&self, cx: f32, cy: f32, radius: f32) -> Self {
        PyRadialGradient {
            inner_center: (cx, cy),
            inner_radius: radius,
            outer_center: self.outer_center,
            outer_radius: self.outer_radius,
            stops: self.stops.clone(),
        }
    }

    /// Set outer circle.
    fn outer_circle(&self, cx: f32, cy: f32, radius: f32) -> Self {
        PyRadialGradient {
            inner_center: self.inner_center,
            inner_radius: self.inner_radius,
            outer_center: (cx, cy),
            outer_radius: radius,
            stops: self.stops.clone(),
        }
    }

    /// Add a color stop.
    fn add_stop(&self, position: f32, color: &PyColor) -> Self {
        let mut stops = self.stops.clone();
        stops.push((position.clamp(0.0, 1.0), color.inner));
        PyRadialGradient {
            inner_center: self.inner_center,
            inner_radius: self.inner_radius,
            outer_center: self.outer_center,
            outer_radius: self.outer_radius,
            stops,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "RadialGradient(center=({}, {}), radius={}, {} stops)",
            self.outer_center.0,
            self.outer_center.1,
            self.outer_radius,
            self.stops.len()
        )
    }
}

/// Line cap styles.
#[pyclass(name = "LineCap")]
#[derive(Clone)]
pub struct PyLineCap {
    #[allow(dead_code)]
    inner: RustLineCap,
}

#[pymethods]
impl PyLineCap {
    /// Butt cap (default).
    #[staticmethod]
    #[allow(non_snake_case)]
    fn BUTT() -> Self {
        PyLineCap {
            inner: RustLineCap::Butt,
        }
    }

    /// Round cap.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn ROUND() -> Self {
        PyLineCap {
            inner: RustLineCap::Round,
        }
    }

    /// Square cap.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn SQUARE() -> Self {
        PyLineCap {
            inner: RustLineCap::Square,
        }
    }
}

/// Line join styles.
#[pyclass(name = "LineJoin")]
#[derive(Clone)]
pub struct PyLineJoin {
    #[allow(dead_code)]
    inner: RustLineJoin,
}

#[pymethods]
impl PyLineJoin {
    /// Miter join (default).
    #[staticmethod]
    #[allow(non_snake_case)]
    fn MITER() -> Self {
        PyLineJoin {
            inner: RustLineJoin::Miter,
        }
    }

    /// Round join.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn ROUND() -> Self {
        PyLineJoin {
            inner: RustLineJoin::Round,
        }
    }

    /// Bevel join.
    #[staticmethod]
    #[allow(non_snake_case)]
    fn BEVEL() -> Self {
        PyLineJoin {
            inner: RustLineJoin::Bevel,
        }
    }
}

/// Pattern presets for common fill patterns.
///
/// Example:
///     >>> content = PatternPresets.checkerboard(10, Color.white(), Color.black())
#[pyclass(name = "PatternPresets")]
pub struct PyPatternPresets;

#[pymethods]
impl PyPatternPresets {
    /// Create horizontal stripes pattern.
    #[staticmethod]
    fn horizontal_stripes(width: f32, height: f32, stripe_height: f32, color: &PyColor) -> Vec<u8> {
        RustPatternPresets::horizontal_stripes(width, height, stripe_height, color.inner)
    }

    /// Create vertical stripes pattern.
    #[staticmethod]
    fn vertical_stripes(width: f32, height: f32, stripe_width: f32, color: &PyColor) -> Vec<u8> {
        RustPatternPresets::vertical_stripes(width, height, stripe_width, color.inner)
    }

    /// Create checkerboard pattern.
    #[staticmethod]
    fn checkerboard(size: f32, color1: &PyColor, color2: &PyColor) -> Vec<u8> {
        RustPatternPresets::checkerboard(size, color1.inner, color2.inner)
    }

    /// Create dot pattern.
    #[staticmethod]
    fn dots(spacing: f32, radius: f32, color: &PyColor) -> Vec<u8> {
        RustPatternPresets::dots(spacing, radius, color.inner)
    }

    /// Create diagonal lines pattern.
    #[staticmethod]
    fn diagonal_lines(size: f32, line_width: f32, color: &PyColor) -> Vec<u8> {
        RustPatternPresets::diagonal_lines(size, line_width, color.inner)
    }

    /// Create crosshatch pattern.
    #[staticmethod]
    fn crosshatch(size: f32, line_width: f32, color: &PyColor) -> Vec<u8> {
        RustPatternPresets::crosshatch(size, line_width, color.inner)
    }
}

// ==========================================================================
// Phase 1.7 — Geometry classes: Rect, Point
// ==========================================================================

/// A rectangle in PDF coordinate space.
///
/// Uses two-corner format (x0, y0, x1, y1) for PyMuPDF compatibility.
/// Internally converts to pdf_oxide's (x, y, width, height) format.
///
/// Example:
///     >>> r = Rect(100, 200, 300, 400)
///     >>> print(r.width, r.height)
///     200.0 200.0
///     >>> r2 = Rect(150, 250, 350, 450)
///     >>> r.intersects(r2)
///     True
#[pyclass(name = "Rect")]
#[derive(Clone, Debug)]
pub struct PyRect {
    pub x0: f32,
    pub y0: f32,
    pub x1: f32,
    pub y1: f32,
}

#[pymethods]
impl PyRect {
    /// Create a new Rect from two corners (x0, y0, x1, y1).
    #[new]
    fn new(x0: f32, y0: f32, x1: f32, y1: f32) -> Self {
        PyRect { x0, y0, x1, y1 }
    }

    /// Create from (x, y, width, height) — pdf_oxide native format.
    #[staticmethod]
    fn from_xywh(x: f32, y: f32, width: f32, height: f32) -> Self {
        PyRect {
            x0: x,
            y0: y,
            x1: x + width,
            y1: y + height,
        }
    }

    #[getter]
    fn x0(&self) -> f32 { self.x0 }
    #[getter]
    fn y0(&self) -> f32 { self.y0 }
    #[getter]
    fn x1(&self) -> f32 { self.x1 }
    #[getter]
    fn y1(&self) -> f32 { self.y1 }

    #[getter]
    fn width(&self) -> f32 { self.x1 - self.x0 }
    #[getter]
    fn height(&self) -> f32 { self.y1 - self.y0 }

    /// Check if this rect intersects another.
    fn intersects(&self, other: &PyRect) -> bool {
        !(self.x1 < other.x0 || other.x1 < self.x0
            || self.y1 < other.y0 || other.y1 < self.y0)
    }

    /// Return the union (bounding box) of two rects.
    fn union_rect(&self, other: &PyRect) -> PyRect {
        PyRect {
            x0: self.x0.min(other.x0),
            y0: self.y0.min(other.y0),
            x1: self.x1.max(other.x1),
            y1: self.y1.max(other.y1),
        }
    }

    /// Check if a point is inside this rect.
    fn contains_point(&self, x: f32, y: f32) -> bool {
        x >= self.x0 && x <= self.x1 && y >= self.y0 && y <= self.y1
    }

    /// Return as a 4-tuple (x0, y0, x1, y1).
    fn as_tuple(&self) -> (f32, f32, f32, f32) {
        (self.x0, self.y0, self.x1, self.y1)
    }

    fn __repr__(&self) -> String {
        format!("Rect({:.1}, {:.1}, {:.1}, {:.1})", self.x0, self.y0, self.x1, self.y1)
    }
}

impl PyRect {
    /// Convert to internal Rust Rect (x, y, width, height) — not exposed to Python.
    pub fn to_internal(&self) -> crate::geometry::Rect {
        crate::geometry::Rect::from_points(self.x0, self.y0, self.x1, self.y1)
    }
}

/// A 2D point in PDF coordinate space.
///
/// Example:
///     >>> p = Point(100.0, 200.0)
///     >>> print(p.x, p.y)
///     100.0 200.0
#[pyclass(name = "Point")]
#[derive(Clone, Debug)]
pub struct PyPoint {
    pub x: f32,
    pub y: f32,
}

#[pymethods]
impl PyPoint {
    #[new]
    fn new(x: f32, y: f32) -> Self {
        PyPoint { x, y }
    }

    #[getter]
    fn x(&self) -> f32 { self.x }
    #[getter]
    fn y(&self) -> f32 { self.y }

    fn __repr__(&self) -> String {
        format!("Point({:.1}, {:.1})", self.x, self.y)
    }
}


/// Map control references (NIST, SPARTA, CWE, ATT&CK, etc.) in text chunks
/// against a control catalog using regex + fuzzy matching.
///
/// Args:
///     catalog_entries: list of (control_id, _key, source_framework) from ArangoDB
///     chunks: list of (chunk_key, text, is_requirement) to scan
///     fuzz_threshold: minimum similarity for fuzzy match (default: 0.75)
///
/// Returns:
///     dict with {results: list[dict], stats: dict}
#[pyfunction]
#[pyo3(signature = (catalog_entries, chunks, fuzz_threshold=0.75))]
fn map_framework_controls(
    py: Python<'_>,
    catalog_entries: Vec<(String, String, String)>,
    chunks: Vec<(String, String, bool)>,
    fuzz_threshold: f32,
) -> PyResult<PyObject> {
    use crate::extractors::framework_mapper::{ControlCatalog, map_controls};

    // Build catalog from Python data
    let mut catalog = ControlCatalog::new();
    catalog.load(catalog_entries);

    // Run matching
    let (results, stats) = map_controls(&chunks, &catalog, fuzz_threshold);

    // Serialize results
    let dict = pyo3::types::PyDict::new(py);

    let results_list = pyo3::types::PyList::empty(py);
    for r in &results {
        let rd = pyo3::types::PyDict::new(py);
        rd.set_item("chunk_key", &r.chunk_key)?;
        rd.set_item("is_requirement", r.is_requirement)?;
        rd.set_item("candidates_found", r.candidates_found)?;

        let matches_list = pyo3::types::PyList::empty(py);
        for m in &r.matches {
            let md = pyo3::types::PyDict::new(py);
            md.set_item("candidate", &m.candidate)?;
            md.set_item("control_id", &m.control_id)?;
            md.set_item("control_key", &m.control_key)?;
            md.set_item("framework", &m.framework)?;
            md.set_item("confidence", m.confidence)?;
            md.set_item("method", &m.method)?;
            md.set_item("context_window", &m.context_window)?;
            matches_list.append(md)?;
        }
        rd.set_item("matches", matches_list)?;
        results_list.append(rd)?;
    }
    dict.set_item("results", results_list)?;

    let stats_dict = pyo3::types::PyDict::new(py);
    stats_dict.set_item("chunks_processed", stats.chunks_processed)?;
    stats_dict.set_item("chunks_with_candidates", stats.chunks_with_candidates)?;
    stats_dict.set_item("chunks_with_matches", stats.chunks_with_matches)?;
    stats_dict.set_item("requirement_chunks", stats.requirement_chunks)?;
    stats_dict.set_item("total_candidates", stats.total_candidates)?;
    stats_dict.set_item("exact_matches", stats.exact_matches)?;
    stats_dict.set_item("parent_exact_matches", stats.parent_exact_matches)?;
    stats_dict.set_item("fuzzy_matches", stats.fuzzy_matches)?;
    stats_dict.set_item("unmatched", stats.unmatched)?;
    dict.set_item("stats", stats_dict)?;

    Ok(dict.into())
}

/// Merge page-split tables using heuristic signals.
///
/// Args:
///     tables: list of dicts with keys: index, page, bbox [x0,y0,x1,y1],
///             column_count, row_count, title, headers, headers_are_numeric
///
/// Returns:
///     dict with {merged_groups: list[list[int]], junk_indices: list[int],
///                merge_details: list[dict]}
#[pyfunction]
fn merge_tables(py: Python<'_>, tables: Vec<pyo3::Bound<'_, pyo3::types::PyDict>>) -> PyResult<PyObject> {
    use crate::extractors::table_merger;

    let mut rust_tables = Vec::with_capacity(tables.len());
    for t in &tables {
        let index: usize = t.get_item("index")?.ok_or_else(|| PyRuntimeError::new_err("missing index"))?.extract()?;
        let page: usize = t.get_item("page")?.ok_or_else(|| PyRuntimeError::new_err("missing page"))?.extract()?;
        let bbox_raw = t.get_item("bbox")?.ok_or_else(|| PyRuntimeError::new_err("missing bbox"))?;
        let bbox_vec: Vec<f32> = bbox_raw.extract().map_err(|_| {
            PyRuntimeError::new_err("bbox must be a sequence of 4 floats [x0, y0, x1, y1]")
        })?;
        if bbox_vec.len() != 4 {
            return Err(PyRuntimeError::new_err(format!("bbox must have 4 elements, got {}", bbox_vec.len())));
        }
        let column_count: usize = t.get_item("column_count")?.ok_or_else(|| PyRuntimeError::new_err("missing column_count"))?.extract()?;
        let row_count: usize = t.get_item("row_count")?.ok_or_else(|| PyRuntimeError::new_err("missing row_count"))?.extract()?;
        let title: String = t.get_item("title")?.map(|v| v.extract().unwrap_or_default()).unwrap_or_default();
        let headers: Vec<String> = t.get_item("headers")?.map(|v| v.extract().unwrap_or_default()).unwrap_or_default();
        let headers_are_numeric: bool = t.get_item("headers_are_numeric")?.map(|v| v.extract().unwrap_or(false)).unwrap_or(false);

        rust_tables.push(table_merger::MergeableTable {
            index, page,
            bbox: [bbox_vec[0], bbox_vec[1], bbox_vec[2], bbox_vec[3]],
            column_count, row_count, title, headers, headers_are_numeric,
        });
    }

    let result = table_merger::merge_tables(&rust_tables);

    let dict = pyo3::types::PyDict::new(py);

    let groups = pyo3::types::PyList::empty(py);
    for g in &result.merged_groups {
        let inner = pyo3::types::PyList::new(py, g).map_err(|e| PyRuntimeError::new_err(format!("{}", e)))?;
        groups.append(inner)?;
    }
    dict.set_item("merged_groups", groups)?;

    let junk = pyo3::types::PyList::new(py, &result.junk_indices).map_err(|e| PyRuntimeError::new_err(format!("{}", e)))?;
    dict.set_item("junk_indices", junk)?;

    let details = pyo3::types::PyList::empty(py);
    for d in &result.merge_details {
        let dd = pyo3::types::PyDict::new(py);
        dd.set_item("target_index", d.target_index)?;
        dd.set_item("absorbed_index", d.absorbed_index)?;
        dd.set_item("reason", &d.reason)?;
        dd.set_item("horizontal_iou", d.horizontal_iou)?;
        dd.set_item("width_ratio", d.width_ratio)?;
        details.append(dd)?;
    }
    dict.set_item("merge_details", details)?;

    Ok(dict.into())
}

/// Python module for PDF library.
///
/// This is the internal module (pdf_oxide) that gets imported by the Python package.
#[pymodule]
fn pdf_oxide(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Document reading
    m.add_class::<PyPdfDocument>()?;

    // PDF creation
    m.add_class::<PyPdf>()?;

    // DOM access types
    m.add_class::<PyPdfPage>()?;
    m.add_class::<PyPdfText>()?;
    m.add_class::<PyPdfTextId>()?;
    m.add_class::<PyPdfImage>()?;
    m.add_class::<PyPdfElement>()?;
    m.add_class::<PyAnnotationWrapper>()?;

    // Text extraction types
    m.add_class::<PyTextChar>()?;
    m.add_class::<PyTextSpan>()?;
    m.add_class::<PyWord>()?;
    m.add_class::<PyTextLine>()?;
    m.add_class::<PyPdfPageRegion>()?;

    // Form field types
    m.add_class::<PyFormField>()?;

    // OCR types (optional, requires ocr feature)
    m.add_class::<PyOcrEngine>()?;
    m.add_class::<PyOcrConfig>()?;

    // Advanced graphics
    m.add_class::<PyColor>()?;
    m.add_class::<PyBlendMode>()?;
    m.add_class::<PyExtGState>()?;
    m.add_class::<PyLinearGradient>()?;
    m.add_class::<PyRadialGradient>()?;
    m.add_class::<PyLineCap>()?;
    m.add_class::<PyLineJoin>()?;
    m.add_class::<PyPatternPresets>()?;

    // Office conversion (optional, requires office feature)
    m.add_class::<PyOfficeConverter>()?;

    // Geometry types (Phase 1.7)
    m.add_class::<PyRect>()?;
    m.add_class::<PyPoint>()?;

    // Standalone functions
    m.add_function(wrap_pyfunction!(map_framework_controls, m)?)?;
    m.add_function(wrap_pyfunction!(merge_tables, m)?)?;

    m.add("VERSION", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
