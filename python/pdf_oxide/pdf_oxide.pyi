"""Type stubs for pdf_oxide Rust bindings."""

# ruff: noqa: N802
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ============================================================================
# PdfDocument - PDF Reading and Extraction
# ============================================================================

class PdfDocument:
    """
    PDF document parser and converter with specification compliance.

    Provides high-performance PDF parsing with multiple output formats,
    all supporting automatic reading order detection for multi-column layouts.

    Features:
        - ISO 32000-1:2008 PDF specification compliance
        - 70-80% character recovery with advanced font support
        - Automatic multi-column layout detection (4 strategies)
        - Complex script support (RTL, CJK, Devanagari, Thai)
        - OCR support for scanned PDFs (optional)
        - 47.9× faster than PyMuPDF4LLM

    Methods:
        - ``__init__(path)``: Open a PDF file
        - ``version()``: Get PDF version tuple
        - ``page_count()``: Get number of pages
        - ``extract_text(page)``: Extract text from a page
        - ``to_markdown(page, ...)``: Convert page to Markdown
        - ``to_html(page, ...)``: Convert page to HTML
        - ``to_markdown_all(...)``: Convert all pages to Markdown
        - ``to_html_all(...)``: Convert all pages to HTML

    Example:
        >>> doc = PdfDocument("sample.pdf")
        >>> print(doc.version())
        (1, 7)
        >>> text = doc.extract_text(0)
        >>> markdown = doc.to_markdown(0, detect_headings=True)
    """

    def __init__(self, path: str) -> None:
        """
        Open a PDF file.

        Args:
            path: Path to the PDF file

        Returns:
            PdfDocument: Opened PDF document

        Raises:
            IOError: If the file cannot be opened or is not a valid PDF

        Example:
            >>> doc = PdfDocument("sample.pdf")
            >>> print(doc.version())
            (1, 7)
        """
        ...

    def version(self) -> Tuple[int, int]:
        """
        Get PDF version.

        Returns:
            Tuple of (major, minor) version numbers, e.g. (1, 7) for PDF 1.7

        Example:
            >>> doc = PdfDocument("sample.pdf")
            >>> version = doc.version()
            >>> print(f"PDF {version[0]}.{version[1]}")
            PDF 1.7
        """
        ...

    def page_count(self) -> int:
        """
        Get number of pages in the document.

        Returns:
            Number of pages

        Raises:
            RuntimeError: If page count cannot be determined

        Example:
            >>> doc = PdfDocument("sample.pdf")
            >>> print(f"Pages: {doc.page_count()}")
            Pages: 42
        """
        ...

    def extract_text(self, page: int) -> str:
        """
        Extract text from a page.

        Args:
            page: Page index (0-based)

        Returns:
            Extracted text from the page

        Raises:
            RuntimeError: If text extraction fails

        Example:
            >>> doc = PdfDocument("sample.pdf")
            >>> text = doc.extract_text(0)
            >>> print(text[:100])
        """
        ...

    def render_page(
        self,
        page: int,
        dpi: Optional[int] = None,
        format: Optional[str] = None,
    ) -> bytes:
        """Render one zero-based PDF page as PNG or JPEG bytes."""
        ...

    def extract_text_ocr(self, page: int, engine: Optional[OcrEngine] = None) -> str:
        """
        Extract text from a page using OCR (optical character recognition).

        Falls back to native text extraction when the page has digital text.
        Requires the `ocr` feature to be enabled at build time.

        Args:
            page: Page index (0-based)
            engine: OCR engine instance. Required for scanned pages.

        Returns:
            Extracted text from the page

        Raises:
            RuntimeError: If text extraction fails

        Example:
            >>> engine = OcrEngine("det.onnx", "rec.onnx", "dict.txt")
            >>> text = doc.extract_text_ocr(0, engine)
        """
        ...

    def has_structure_tree(self) -> bool:
        """
        Check if document has a structure tree (Tagged PDF).

        Tagged PDFs contain explicit document structure that defines reading order,
        semantic meaning, and accessibility information. This is the PDF-spec-compliant
        way to determine reading order.

        Returns:
            True if document has logical structure (Tagged PDF), False otherwise

        Example:
            >>> doc = PdfDocument("sample.pdf")
            >>> if doc.has_structure_tree():
            ...     print("Tagged PDF with logical structure")
            ... else:
            ...     print("Untagged PDF - uses page content order")
        """
        ...

    def to_plain_text(
        self,
        page: int,
        preserve_layout: bool = False,
        detect_headings: bool = True,
        include_images: bool = True,
        image_output_dir: Optional[str] = None,
    ) -> str:
        """
        Convert a page to plain text.

        Args:
            page: Page index (0-based)
            preserve_layout: Preserve visual layout (default: False) [currently unused]
            detect_headings: Detect headings (default: True) [currently unused]
            include_images: Include images (default: True) [currently unused]
            image_output_dir: Directory for images (default: None) [currently unused]

        Returns:
            Plain text from the page

        Raises:
            RuntimeError: If conversion fails

        Example:
            >>> doc = PdfDocument("paper.pdf")
            >>> text = doc.to_plain_text(0)
            >>> print(text[:100])

        Note:
            Options parameters are accepted for API consistency but currently unused for plain text.
        """
        ...

    def to_plain_text_all(
        self,
        preserve_layout: bool = False,
        detect_headings: bool = True,
        include_images: bool = True,
        image_output_dir: Optional[str] = None,
    ) -> str:
        """
        Convert all pages to plain text.

        Args:
            preserve_layout: Preserve visual layout (default: False) [currently unused]
            detect_headings: Detect headings (default: True) [currently unused]
            include_images: Include images (default: True) [currently unused]
            image_output_dir: Directory for images (default: None) [currently unused]

        Returns:
            Plain text from all pages separated by horizontal rules

        Raises:
            RuntimeError: If conversion fails

        Example:
            >>> doc = PdfDocument("book.pdf")
            >>> text = doc.to_plain_text_all()
            >>> with open("book.txt", "w") as f:
            ...     f.write(text)

        Note:
            Options parameters are accepted for API consistency but currently unused for plain text.
        """
        ...

    def to_markdown(
        self,
        page: int,
        preserve_layout: bool = False,
        detect_headings: bool = True,
        include_images: bool = True,
        image_output_dir: Optional[str] = None,
        embed_images: bool = True,
    ) -> str:
        """
        Convert a page to Markdown with intelligent layout handling.

        Uses pluggable reading order strategies for accurate multi-column detection.
        Automatically handles complex scripts and maintains logical structure.

        Args:
            page: Page index (0-based)
            preserve_layout: Preserve visual layout (default: False)
            detect_headings: Detect headings based on font size (default: True)
            include_images: Include images in output (default: True)
            image_output_dir: Directory to save images (default: None)
            embed_images: Embed images as base64 data URIs (default: True)

        Returns:
            Markdown text

        Raises:
            RuntimeError: If conversion fails

        Example:
            >>> doc = PdfDocument("paper.pdf")
            >>> markdown = doc.to_markdown(0, detect_headings=True)
            >>> with open("output.md", "w") as f:
            ...     f.write(markdown)
        """
        ...

    def to_html(
        self,
        page: int,
        preserve_layout: bool = False,
        detect_headings: bool = True,
        include_images: bool = True,
        image_output_dir: Optional[str] = None,
        embed_images: bool = True,
    ) -> str:
        """
        Convert a page to HTML with semantic structure.

        Produces semantic HTML with proper reading order. Automatically detects
        multi-column layouts and converts to single-column HTML structure.

        Args:
            page: Page index (0-based)
            preserve_layout: Preserve visual layout with CSS positioning (default: False)
            detect_headings: Detect headings based on font size (default: True)
            include_images: Include images in output (default: True)
            image_output_dir: Directory to save images (default: None)
            embed_images: Embed images as base64 data URIs (default: True)

        Returns:
            HTML text

        Raises:
            RuntimeError: If conversion fails

        Example:
            >>> doc = PdfDocument("paper.pdf")
            >>> html = doc.to_html(0, preserve_layout=False)
            >>> with open("output.html", "w") as f:
            ...     f.write(html)
        """
        ...

    def to_markdown_all(
        self,
        preserve_layout: bool = False,
        detect_headings: bool = True,
        include_images: bool = True,
        image_output_dir: Optional[str] = None,
        embed_images: bool = True,
    ) -> str:
        """
        Convert all pages to Markdown format.

        Pages are separated by horizontal rules (---).

        Args:
            preserve_layout: Preserve visual layout (default: False)
            detect_headings: Detect headings based on font size (default: True)
            include_images: Include images in output (default: True)
            image_output_dir: Directory to save images (default: None)
            embed_images: Embed images as base64 data URIs (default: True)

        Returns:
            Markdown text with all pages separated by horizontal rules

        Raises:
            RuntimeError: If conversion fails

        Example:
            >>> doc = PdfDocument("book.pdf")
            >>> markdown = doc.to_markdown_all(detect_headings=True)
            >>> with open("book.md", "w") as f:
            ...     f.write(markdown)
        """
        ...

    def to_html_all(
        self,
        preserve_layout: bool = False,
        detect_headings: bool = True,
        include_images: bool = True,
        image_output_dir: Optional[str] = None,
        embed_images: bool = True,
    ) -> str:
        """
        Convert all pages to HTML format.

        Each page is wrapped in a div.page element with a data-page attribute.

        Args:
            preserve_layout: Preserve visual layout with CSS positioning (default: False)
            detect_headings: Detect headings based on font size (default: True)
            include_images: Include images in output (default: True)
            image_output_dir: Directory to save images (default: None)
            embed_images: Embed images as base64 data URIs (default: True)

        Returns:
            HTML text with all pages wrapped in div.page elements

        Raises:
            RuntimeError: If conversion fails

        Example:
            >>> doc = PdfDocument("book.pdf")
            >>> html = doc.to_html_all(preserve_layout=True)
            >>> with open("book.html", "w") as f:
            ...     f.write(html)
        """
        ...

    def page(self, index: int) -> PdfPage:
        """
        Get a page for DOM-like navigation and editing.

        Returns a PdfPage object that provides hierarchical access to page content,
        allowing you to query, navigate, and modify elements.

        Args:
            index: Page index (0-based)

        Returns:
            Page object with DOM access

        Raises:
            RuntimeError: If page access fails

        Example:
            >>> doc = PdfDocument("sample.pdf")
            >>> page = doc.page(0)
            >>> for text in page.find_text_containing("Hello"):
            ...     print(f"{text.value} at {text.bbox}")
        """
        ...

    def save_page(self, page: PdfPage) -> None:
        """
        Save modifications made via page().set_text() back to a file.

        Args:
            page: The modified page to save

        Raises:
            RuntimeError: If save fails

        Example:
            >>> doc = PdfDocument("input.pdf")
            >>> page = doc.page(0)
            >>> for t in page.find_text_containing("old"):
            ...     page.set_text(t.id, "new")
            >>> doc.save_page(page)
            >>> doc.save("output.pdf")
        """
        ...

    def save(self, path: str) -> None:
        """
        Save the document to a file.

        This saves any modifications made via page().set_text().

        Args:
            path: Output file path

        Raises:
            IOError: If save fails

        Example:
            >>> doc = PdfDocument("input.pdf")
            >>> page = doc.page(0)
            >>> page.set_text(text_id, "new text")
            >>> doc.save_page(page)
            >>> doc.save("output.pdf")
        """
        ...

    def save_encrypted(
        self,
        path: str,
        user_password: str,
        owner_password: Optional[str] = None,
        allow_print: bool = True,
        allow_copy: bool = True,
        allow_modify: bool = True,
        allow_annotate: bool = True,
    ) -> None:
        """
        Save the document with password encryption.

        Creates a password-protected PDF using AES-256 encryption (the strongest available).

        Args:
            path: Output file path
            user_password: Password required to open the document (can be empty string
                for no open password, but still apply owner restrictions)
            owner_password: Password for full access and changing security settings.
                If empty, defaults to user_password.
            allow_print: Allow printing (default: True)
            allow_copy: Allow copying text and graphics (default: True)
            allow_modify: Allow modifying the document (default: True)
            allow_annotate: Allow adding annotations (default: True)

        Raises:
            RuntimeError: If no modifications have been made
            IOError: If save fails

        Example:
            >>> doc = PdfDocument("input.pdf")
            >>> page = doc.page(0)
            >>> page.set_text(text_id, "modified")
            >>> doc.save_page(page)
            >>> doc.save_encrypted("protected.pdf", "user123", "owner456")
            >>>
            >>> # View-only PDF (no printing, copying, or modifying):
            >>> doc.save_encrypted("readonly.pdf", "", "owner456",
            ...     allow_print=False, allow_copy=False, allow_modify=False)
        """
        ...

    def set_title(self, title: str) -> None:
        """
        Set the document title.

        Args:
            title: Document title

        Example:
            >>> doc.set_title("My Document")
        """
        ...

    def set_author(self, author: str) -> None:
        """
        Set the document author.

        Args:
            author: Author name
        """
        ...

    def set_subject(self, subject: str) -> None:
        """
        Set the document subject.

        Args:
            subject: Document subject
        """
        ...

    def set_keywords(self, keywords: str) -> None:
        """
        Set the document keywords.

        Args:
            keywords: Comma-separated keywords
        """
        ...

    def page_rotation(self, page: int) -> int:
        """
        Get the rotation of a page in degrees (0, 90, 180, 270).

        Args:
            page: Page index (0-based)

        Returns:
            Rotation in degrees

        Example:
            >>> rotation = doc.page_rotation(0)
            >>> print(f"Page is rotated {rotation} degrees")
        """
        ...

    def set_page_rotation(self, page: int, degrees: int) -> None:
        """
        Set the rotation of a page.

        Args:
            page: Page index (0-based)
            degrees: Rotation in degrees (0, 90, 180, or 270)

        Example:
            >>> doc.set_page_rotation(0, 90)
            >>> doc.save("rotated.pdf")
        """
        ...

    def rotate_page(self, page: int, degrees: int) -> None:
        """
        Rotate a page by the given degrees (adds to current rotation).

        Args:
            page: Page index (0-based)
            degrees: Degrees to rotate (will be normalized to 0, 90, 180, 270)

        Example:
            >>> doc.rotate_page(0, 90)  # Rotate 90 degrees clockwise
            >>> doc.save("rotated.pdf")
        """
        ...

    def rotate_all_pages(self, degrees: int) -> None:
        """
        Rotate all pages by the given degrees.

        Args:
            degrees: Degrees to rotate (will be normalized to 0, 90, 180, 270)

        Example:
            >>> doc.rotate_all_pages(180)  # Flip all pages upside down
            >>> doc.save("rotated.pdf")
        """
        ...

    def page_media_box(self, page: int) -> Tuple[float, float, float, float]:
        """
        Get the MediaBox of a page (physical page size).

        Args:
            page: Page index (0-based)

        Returns:
            (llx, lly, urx, ury) coordinates

        Example:
            >>> llx, lly, urx, ury = doc.page_media_box(0)
            >>> print(f"Page size: {urx - llx} x {ury - lly}")
        """
        ...

    def set_page_media_box(self, page: int, llx: float, lly: float, urx: float, ury: float) -> None:
        """
        Set the MediaBox of a page (physical page size).

        Args:
            page: Page index (0-based)
            llx: Lower-left X coordinate
            lly: Lower-left Y coordinate
            urx: Upper-right X coordinate
            ury: Upper-right Y coordinate
        """
        ...

    def page_crop_box(self, page: int) -> Optional[Tuple[float, float, float, float]]:
        """
        Get the CropBox of a page (visible/printable area).

        Args:
            page: Page index (0-based)

        Returns:
            (llx, lly, urx, ury) or None if not set
        """
        ...

    def set_page_crop_box(self, page: int, llx: float, lly: float, urx: float, ury: float) -> None:
        """
        Set the CropBox of a page (visible/printable area).

        Args:
            page: Page index (0-based)
            llx: Lower-left X coordinate
            lly: Lower-left Y coordinate
            urx: Upper-right X coordinate
            ury: Upper-right Y coordinate

        Example:
            >>> # Crop to a 6x9 inch area (72 points = 1 inch)
            >>> doc.set_page_crop_box(0, 72, 72, 504, 720)
            >>> doc.save("cropped.pdf")
        """
        ...

    def crop_margins(self, left: float, right: float, top: float, bottom: float) -> None:
        """
        Crop margins from all pages.

        Sets the CropBox to be smaller than the MediaBox by the specified margins.

        Args:
            left: Left margin in points
            right: Right margin in points
            top: Top margin in points
            bottom: Bottom margin in points

        Example:
            >>> # Crop 0.5 inch from all sides (72 points = 1 inch)
            >>> doc.crop_margins(36, 36, 36, 36)
            >>> doc.save("cropped.pdf")
        """
        ...

    def erase_region(self, page: int, llx: float, lly: float, urx: float, ury: float) -> None:
        """
        Erase a rectangular region on a page by covering it with white.

        This adds a white rectangle overlay that covers the specified region.
        The original content is not removed but hidden beneath the white overlay.

        Args:
            page: Page index (0-based)
            llx: Lower-left X coordinate
            lly: Lower-left Y coordinate
            urx: Upper-right X coordinate
            ury: Upper-right Y coordinate

        Example:
            >>> # Erase a region in the upper-left corner
            >>> doc.erase_region(0, 72, 700, 200, 792)
            >>> doc.save("output.pdf")
        """
        ...

    def erase_regions(self, page: int, rects: List[Tuple[float, float, float, float]]) -> None:
        """
        Erase multiple rectangular regions on a page.

        Args:
            page: Page index (0-based)
            rects: List of (llx, lly, urx, ury) tuples

        Example:
            >>> doc.erase_regions(0, [(72, 700, 200, 792), (300, 300, 500, 400)])
            >>> doc.save("output.pdf")
        """
        ...

    def clear_erase_regions(self, page: int) -> None:
        """
        Clear all pending erase operations for a page.

        Args:
            page: Page index (0-based)
        """
        ...

    def flatten_page_annotations(self, page: int) -> None:
        """
        Flatten annotations on a specific page.

        Renders annotation appearance streams into the page content and removes
        the annotations. This makes annotations permanent and non-editable.

        Args:
            page: Page index (0-based)

        Raises:
            RuntimeError: If page index is out of range

        Example:
            >>> doc.flatten_page_annotations(0)  # Flatten page 0
            >>> doc.save("flattened.pdf")
        """
        ...

    def flatten_all_annotations(self) -> None:
        """
        Flatten annotations on all pages.

        Renders all annotation appearance streams into page content and removes
        all annotations from the document.

        Raises:
            RuntimeError: If the operation fails

        Example:
            >>> doc.flatten_all_annotations()
            >>> doc.save("flattened.pdf")
        """
        ...

    def is_page_marked_for_flatten(self, page: int) -> bool:
        """
        Check if a page is marked for annotation flattening.

        Args:
            page: Page index (0-based)

        Returns:
            True if the page is marked for flattening
        """
        ...

    def unmark_page_for_flatten(self, page: int) -> None:
        """
        Unmark a page for annotation flattening.

        Args:
            page: Page index (0-based)
        """
        ...

    def apply_page_redactions(self, page: int) -> None:
        """
        Apply redactions on a specific page.

        Finds all redaction annotations on the page, draws colored overlays
        to hide the content, and removes the redaction annotations.

        Args:
            page: Page index (0-based)

        Raises:
            RuntimeError: If page index is out of range

        Note:
            This creates visual overlays but does not remove underlying content.

        Example:
            >>> doc.apply_page_redactions(0)
            >>> doc.save("redacted.pdf")
        """
        ...

    def apply_all_redactions(self) -> None:
        """
        Apply redactions on all pages.

        Finds all redaction annotations throughout the document, draws
        colored overlays to hide content, and removes the redaction annotations.

        Raises:
            RuntimeError: If the operation fails

        Example:
            >>> doc.apply_all_redactions()
            >>> doc.save("redacted.pdf")
        """
        ...

    def is_page_marked_for_redaction(self, page: int) -> bool:
        """
        Check if a page is marked for redaction application.

        Args:
            page: Page index (0-based)

        Returns:
            True if the page is marked for redaction application
        """
        ...

    def unmark_page_for_redaction(self, page: int) -> None:
        """
        Unmark a page for redaction application.

        Args:
            page: Page index (0-based)
        """
        ...

    def page_images(self, page: int) -> List[Dict[str, Any]]:
        """
        Get information about all images on a page.

        Returns a list of dictionaries with image information including
        name, position, size, and transformation matrix.

        Args:
            page: Page index (0-based)

        Returns:
            List of image info dictionaries with keys:
                - name (str): XObject name (e.g., "Im0")
                - x (float): X position
                - y (float): Y position
                - width (float): Image width
                - height (float): Image height
                - matrix (tuple): 6-element transformation matrix (a, b, c, d, e, f)
        """
        ...

    def reposition_image(self, page: int, image_name: str, x: float, y: float) -> None:
        """
        Reposition an image on a page.

        Args:
            page: Page index (0-based)
            image_name: Name of the image XObject (e.g., "Im0")
            x: New X position
            y: New Y position

        Raises:
            RuntimeError: If the image is not found or operation fails
        """
        ...

    def resize_image(self, page: int, image_name: str, width: float, height: float) -> None:
        """
        Resize an image on a page.

        Args:
            page: Page index (0-based)
            image_name: Name of the image XObject (e.g., "Im0")
            width: New width
            height: New height

        Raises:
            RuntimeError: If the image is not found or operation fails
        """
        ...

    def set_image_bounds(
        self,
        page: int,
        image_name: str,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> None:
        """
        Set both position and size of an image on a page.

        Args:
            page: Page index (0-based)
            image_name: Name of the image XObject (e.g., "Im0")
            x: New X position
            y: New Y position
            width: New width
            height: New height

        Raises:
            RuntimeError: If the image is not found or operation fails
        """
        ...

    def clear_image_modifications(self, page: int) -> None:
        """
        Clear all image modifications for a specific page.

        Args:
            page: Page index (0-based)
        """
        ...

    def has_image_modifications(self, page: int) -> bool:
        """
        Check if a page has pending image modifications.

        Args:
            page: Page index (0-based)

        Returns:
            True if the page has pending image modifications
        """
        ...

    def search(
        self,
        pattern: str,
        case_insensitive: bool = False,
        literal: bool = False,
        whole_word: bool = False,
        max_results: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Search for text in the document.

        Searches all pages for matches of the given pattern (regex supported).

        Args:
            pattern: Search pattern (regex or literal text)
            case_insensitive: Case insensitive search (default: False)
            literal: Treat pattern as literal text, not regex (default: False)
            whole_word: Match whole words only (default: False)
            max_results: Maximum number of results, 0 = unlimited (default: 0)

        Returns:
            List of search results, each containing:
                - page (int): Page number (0-indexed)
                - text (str): Matched text
                - x (float): X position of match
                - y (float): Y position of match
                - width (float): Width of match bounding box
                - height (float): Height of match bounding box

        Example:
            >>> results = doc.search("hello")
            >>> for r in results:
            ...     print(f"Found '{r['text']}' on page {r['page']}")
            >>>
            >>> # Case insensitive regex search
            >>> results = doc.search(r"\\d+\\.\\d+", case_insensitive=True)
        """
        ...

    def search_page(
        self,
        page: int,
        pattern: str,
        case_insensitive: bool = False,
        literal: bool = False,
        whole_word: bool = False,
        max_results: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Search for text on a specific page.

        Args:
            page: Page index (0-based)
            pattern: Search pattern (regex or literal text)
            case_insensitive: Case insensitive search (default: False)
            literal: Treat pattern as literal text, not regex (default: False)
            whole_word: Match whole words only (default: False)
            max_results: Maximum number of results, 0 = unlimited (default: 0)

        Returns:
            List of search results (same format as search())

        Example:
            >>> results = doc.search_page(0, "hello")
        """
        ...

    def __repr__(self) -> str:
        """
        String representation of the document.

        Returns:
            Representation showing PDF version
        """
        ...

# ============================================================================
# Pdf - PDF Creation
# ============================================================================

class Pdf:
    """
    PDF creation from Markdown, HTML, or plain text.

    Provides simple PDF creation from various content formats.

    Methods:
        - ``from_markdown(content)``: Create PDF from Markdown
        - ``from_html(content)``: Create PDF from HTML
        - ``from_text(content)``: Create PDF from plain text
        - ``save(path)``: Save PDF to file

    Example:
        >>> pdf = Pdf.from_markdown("# Hello World")
        >>> pdf.save("output.pdf")
    """

    @staticmethod
    def from_markdown(
        content: str, title: Optional[str] = None, author: Optional[str] = None
    ) -> Pdf:
        """
        Create a PDF from Markdown content.

        Args:
            content: Markdown content
            title: Document title (optional)
            author: Document author (optional)

        Returns:
            Created PDF document

        Raises:
            RuntimeError: If PDF creation fails

        Example:
            >>> pdf = Pdf.from_markdown("# Hello\\n\\nWorld")
            >>> pdf.save("hello.pdf")
        """
        ...

    @staticmethod
    def from_html(content: str, title: Optional[str] = None, author: Optional[str] = None) -> Pdf:
        """
        Create a PDF from HTML content.

        Args:
            content: HTML content
            title: Document title (optional)
            author: Document author (optional)

        Returns:
            Created PDF document

        Example:
            >>> pdf = Pdf.from_html("<h1>Hello</h1><p>World</p>")
            >>> pdf.save("hello.pdf")
        """
        ...

    @staticmethod
    def from_text(content: str, title: Optional[str] = None, author: Optional[str] = None) -> Pdf:
        """
        Create a PDF from plain text.

        Args:
            content: Plain text content
            title: Document title (optional)
            author: Document author (optional)

        Returns:
            Created PDF document

        Example:
            >>> pdf = Pdf.from_text("Hello, World!")
            >>> pdf.save("hello.pdf")
        """
        ...

    def save(self, path: str) -> None:
        """
        Save the PDF to a file.

        Args:
            path: Output file path

        Raises:
            IOError: If the file cannot be written

        Example:
            >>> pdf = Pdf.from_markdown("# Hello")
            >>> pdf.save("output.pdf")
        """
        ...

    def to_bytes(self) -> bytes:
        """
        Get the PDF as bytes.

        Returns:
            Raw PDF data

        Example:
            >>> pdf = Pdf.from_markdown("# Hello")
            >>> data = pdf.to_bytes()
            >>> len(data) > 0
            True
        """
        ...

    def __len__(self) -> int:
        """
        Get the size of the PDF in bytes.

        Returns:
            Size in bytes
        """
        ...

    def __repr__(self) -> str:
        """String representation."""
        ...

# ============================================================================
# DOM Access API
# ============================================================================

class PdfPage:
    """
    PDF page with DOM-like access.

    Provides hierarchical access to page content elements.

    Example:
        >>> doc = PdfDocument("sample.pdf")
        >>> page = doc.page(0)
        >>> for text in page.find_text_containing("Hello"):
        ...     print(f"{text.value} at {text.bbox}")
    """

    @property
    def index(self) -> int:
        """
        Get the page index.

        Returns:
            Zero-based page index
        """
        ...

    @property
    def width(self) -> float:
        """
        Get page width.

        Returns:
            Page width in points
        """
        ...

    @property
    def height(self) -> float:
        """
        Get page height.

        Returns:
            Page height in points
        """
        ...

    def children(self) -> List[PdfElement]:
        """
        Get all top-level elements on the page.

        Returns:
            List of child elements

        Example:
            >>> for elem in page.children():
            ...     if elem.is_text():
            ...         print(elem.as_text().value)
        """
        ...

    def find_text_containing(self, needle: str) -> List[PdfText]:
        """
        Find all text elements containing the specified string.

        Args:
            needle: String to search for

        Returns:
            List of matching text elements

        Example:
            >>> texts = page.find_text_containing("Hello")
            >>> for t in texts:
            ...     print(t.value)
        """
        ...

    def find_images(self) -> List[PdfImage]:
        """
        Find all images on the page.

        Returns:
            List of image elements
        """
        ...

    def get_element(self, element_id: str) -> Optional[PdfElement]:
        """
        Get element by ID.

        Args:
            element_id: The element ID as a string

        Returns:
            The element if found, None otherwise
        """
        ...

    def set_text(self, text_id: PdfTextId, new_text: str) -> None:
        """
        Set text content for an element by ID.

        Args:
            text_id: The ID of the text element (from PdfText.id)
            new_text: New text content

        Raises:
            RuntimeError: If the element is not found or is not a text element

        Example:
            >>> for t in page.find_text_containing("old"):
            ...     page.set_text(t.id, "new")
        """
        ...

    def annotations(self) -> List[PdfAnnotation]:
        """
        Get all annotations on the page.

        Returns:
            List of annotations
        """
        ...

    def add_link(self, x: float, y: float, width: float, height: float, url: str) -> str:
        """
        Add a link annotation to the page.

        Args:
            x: X coordinate
            y: Y coordinate
            width: Link width
            height: Link height
            url: Target URL

        Returns:
            Annotation ID

        Example:
            >>> page.add_link(100, 700, 50, 12, "https://example.com")
        """
        ...

    def add_highlight(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        color: Tuple[float, float, float],
    ) -> str:
        """
        Add a text highlight annotation.

        Args:
            x: X coordinate
            y: Y coordinate
            width: Highlight width
            height: Highlight height
            color: RGB color as (r, g, b) where each is 0.0-1.0

        Returns:
            Annotation ID

        Example:
            >>> page.add_highlight(100, 700, 200, 12, (1.0, 1.0, 0.0))  # Yellow
        """
        ...

    def add_note(self, x: float, y: float, text: str) -> str:
        """
        Add a sticky note annotation.

        Args:
            x: X coordinate
            y: Y coordinate
            text: Note content

        Returns:
            Annotation ID

        Example:
            >>> page.add_note(100, 700, "This is important!")
        """
        ...

    def remove_annotation(self, index: int) -> bool:
        """
        Remove an annotation by index.

        Args:
            index: Annotation index

        Returns:
            True if annotation was removed
        """
        ...

    def add_text(self, text: str, x: float, y: float, font_size: float = 12.0) -> PdfTextId:
        """
        Add a text element to the page.

        Args:
            text: Text content
            x: X coordinate
            y: Y coordinate
            font_size: Font size in points (default: 12.0)

        Returns:
            ID of the new element

        Example:
            >>> text_id = page.add_text("Hello World", 100, 700, 14.0)
        """
        ...

    def remove_element(self, element_id: PdfTextId) -> bool:
        """
        Remove an element by ID.

        Args:
            element_id: Element ID (from PdfText.id, etc.)

        Returns:
            True if element was removed
        """
        ...

    def __repr__(self) -> str:
        """String representation."""
        ...

class PdfTextId:
    """
    Text element ID for modification.

    Used to identify text elements for modification via page.set_text().
    """

    def __repr__(self) -> str:
        """String representation."""
        ...

class PdfText:
    """
    Text element with content, position, and formatting.

    Provides access to text content, position, and formatting information.

    Example:
        >>> for text in page.find_text_containing("Hello"):
        ...     print(f"{text.value} at {text.bbox}")
        ...     print(f"Font: {text.font_name} {text.font_size}pt")
    """

    @property
    def id(self) -> PdfTextId:
        """
        Get the element ID.

        Returns:
            The unique element ID
        """
        ...

    @property
    def value(self) -> str:
        """
        Get the text content.

        Returns:
            The text content
        """
        ...

    @property
    def text(self) -> str:
        """
        Get the text content (alias for value).

        Returns:
            The text content
        """
        ...

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        """
        Get the bounding box as (x, y, width, height).

        Returns:
            Bounding box coordinates
        """
        ...

    @property
    def font_name(self) -> str:
        """
        Get the font name.

        Returns:
            Font name
        """
        ...

    @property
    def font_size(self) -> float:
        """
        Get the font size in points.

        Returns:
            Font size
        """
        ...

    @property
    def is_bold(self) -> bool:
        """
        Check if text is bold.

        Returns:
            True if bold
        """
        ...

    @property
    def is_italic(self) -> bool:
        """
        Check if text is italic.

        Returns:
            True if italic
        """
        ...

    def contains(self, needle: str) -> bool:
        """
        Check if text contains a substring.

        Args:
            needle: String to search for

        Returns:
            True if text contains needle
        """
        ...

    def starts_with(self, prefix: str) -> bool:
        """
        Check if text starts with a prefix.

        Args:
            prefix: Prefix to check

        Returns:
            True if text starts with prefix
        """
        ...

    def ends_with(self, suffix: str) -> bool:
        """
        Check if text ends with a suffix.

        Args:
            suffix: Suffix to check

        Returns:
            True if text ends with suffix
        """
        ...

    def __repr__(self) -> str:
        """String representation."""
        ...

class PdfImage:
    """
    Image element.

    Provides access to image position and dimensions.
    """

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        """
        Get the bounding box as (x, y, width, height).

        Returns:
            Bounding box coordinates
        """
        ...

    @property
    def width(self) -> int:
        """
        Get image width in pixels.

        Returns:
            Image width
        """
        ...

    @property
    def height(self) -> int:
        """
        Get image height in pixels.

        Returns:
            Image height
        """
        ...

    @property
    def aspect_ratio(self) -> float:
        """
        Get aspect ratio (width / height).

        Returns:
            Aspect ratio
        """
        ...

    def __repr__(self) -> str:
        """String representation."""
        ...

class PdfAnnotation:
    """
    PDF annotation.

    Represents a PDF annotation with type, position, and content.
    """

    @property
    def subtype(self) -> str:
        """
        Get the annotation subtype (e.g., "Link", "Highlight", "Text").

        Returns:
            Annotation subtype
        """
        ...

    @property
    def rect(self) -> Tuple[float, float, float, float]:
        """
        Get the bounding rectangle as (x, y, width, height).

        Returns:
            Bounding rectangle
        """
        ...

    @property
    def contents(self) -> Optional[str]:
        """
        Get the annotation contents/text if available.

        Returns:
            Contents text or None
        """
        ...

    @property
    def color(self) -> Optional[Tuple[float, float, float]]:
        """
        Get the annotation color as (r, g, b) if available.

        Returns:
            RGB color tuple or None
        """
        ...

    @property
    def is_modified(self) -> bool:
        """
        Check if this annotation has been modified.

        Returns:
            True if modified
        """
        ...

    @property
    def is_new(self) -> bool:
        """
        Check if this is a new annotation (not loaded from PDF).

        Returns:
            True if new
        """
        ...

    def __repr__(self) -> str:
        """String representation."""
        ...

class PdfElement:
    """
    Generic PDF element (Text, Image, Path, Table, or Structure).

    Can be one of: Text, Image, Path, Table, or Structure.
    Use is_* methods to check type, and as_* methods to convert.
    """

    def is_text(self) -> bool:
        """
        Check if this is a text element.

        Returns:
            True if text element
        """
        ...

    def is_image(self) -> bool:
        """
        Check if this is an image element.

        Returns:
            True if image element
        """
        ...

    def is_path(self) -> bool:
        """
        Check if this is a path element.

        Returns:
            True if path element
        """
        ...

    def is_table(self) -> bool:
        """
        Check if this is a table element.

        Returns:
            True if table element
        """
        ...

    def is_structure(self) -> bool:
        """
        Check if this is a structure element.

        Returns:
            True if structure element
        """
        ...

    def as_text(self) -> Optional[PdfText]:
        """
        Get as text element if this is a text element.

        Returns:
            The text element, or None if not a text element
        """
        ...

    def as_image(self) -> Optional[PdfImage]:
        """
        Get as image element if this is an image element.

        Returns:
            The image element, or None if not an image element
        """
        ...

    @property
    def bbox(self) -> Tuple[float, float, float, float]:
        """
        Get the bounding box as (x, y, width, height).

        Returns:
            Bounding box coordinates
        """
        ...

    def __repr__(self) -> str:
        """String representation."""
        ...

# ============================================================================
# Advanced Graphics Types
# ============================================================================

class Color:
    """
    RGB Color for PDF graphics.

    Represents an RGB color with components in the range 0.0 to 1.0.

    Example:
        >>> color = Color(1.0, 0.0, 0.0)  # Red
        >>> color = Color.red()
        >>> color = Color.from_hex("#FF0000")
    """

    def __init__(self, r: float, g: float, b: float) -> None:
        """
        Create a new RGB color.

        Args:
            r: Red component (0.0 to 1.0)
            g: Green component (0.0 to 1.0)
            b: Blue component (0.0 to 1.0)
        """
        ...

    @staticmethod
    def from_hex(hex_str: str) -> Color:
        """
        Create color from hex string.

        Args:
            hex_str: Hex color like "#FF0000" or "FF0000"

        Returns:
            Color instance

        Example:
            >>> red = Color.from_hex("#FF0000")
        """
        ...

    @staticmethod
    def black() -> Color:
        """Black color (0, 0, 0)."""
        ...

    @staticmethod
    def white() -> Color:
        """White color (1, 1, 1)."""
        ...

    @staticmethod
    def red() -> Color:
        """Red color (1, 0, 0)."""
        ...

    @staticmethod
    def green() -> Color:
        """Green color (0, 1, 0)."""
        ...

    @staticmethod
    def blue() -> Color:
        """Blue color (0, 0, 1)."""
        ...

    @property
    def r(self) -> float:
        """Get red component."""
        ...

    @property
    def g(self) -> float:
        """Get green component."""
        ...

    @property
    def b(self) -> float:
        """Get blue component."""
        ...

    def __repr__(self) -> str:
        """String representation."""
        ...

class BlendMode:
    """
    Blend modes for transparency effects.

    PDF blend modes control how overlapping colors combine.

    Example:
        >>> gs = ExtGState().blend_mode(BlendMode.MULTIPLY())
    """

    @staticmethod
    def NORMAL() -> BlendMode:
        """Normal blend mode (default)."""
        ...

    @staticmethod
    def MULTIPLY() -> BlendMode:
        """Multiply blend mode - darkens by multiplying colors."""
        ...

    @staticmethod
    def SCREEN() -> BlendMode:
        """Screen blend mode - lightens by inverting, multiplying, and inverting again."""
        ...

    @staticmethod
    def OVERLAY() -> BlendMode:
        """Overlay blend mode - combines Multiply and Screen."""
        ...

    @staticmethod
    def DARKEN() -> BlendMode:
        """Darken blend mode - selects darker of two colors."""
        ...

    @staticmethod
    def LIGHTEN() -> BlendMode:
        """Lighten blend mode - selects lighter of two colors."""
        ...

    @staticmethod
    def COLOR_DODGE() -> BlendMode:
        """Color dodge blend mode - brightens the base color."""
        ...

    @staticmethod
    def COLOR_BURN() -> BlendMode:
        """Color burn blend mode - darkens the base color."""
        ...

    @staticmethod
    def HARD_LIGHT() -> BlendMode:
        """Hard light blend mode - similar to shining a harsh spotlight."""
        ...

    @staticmethod
    def SOFT_LIGHT() -> BlendMode:
        """Soft light blend mode - similar to shining a diffused spotlight."""
        ...

    @staticmethod
    def DIFFERENCE() -> BlendMode:
        """Difference blend mode - subtracts colors."""
        ...

    @staticmethod
    def EXCLUSION() -> BlendMode:
        """Exclusion blend mode - similar to Difference but lower contrast."""
        ...

    def __repr__(self) -> str:
        """String representation."""
        ...

class ExtGState:
    """
    Extended Graphics State for transparency and blend effects.

    Controls transparency (alpha) and blend modes for PDF graphics.

    Example:
        >>> gs = ExtGState().alpha(0.5).blend_mode(BlendMode.MULTIPLY())
    """

    def __init__(self) -> None:
        """Create a new ExtGState builder."""
        ...

    def fill_alpha(self, alpha: float) -> ExtGState:
        """
        Set fill opacity (0.0 = transparent, 1.0 = opaque).

        Args:
            alpha: Opacity value from 0.0 to 1.0

        Returns:
            New ExtGState with fill alpha set
        """
        ...

    def stroke_alpha(self, alpha: float) -> ExtGState:
        """
        Set stroke opacity (0.0 = transparent, 1.0 = opaque).

        Args:
            alpha: Opacity value from 0.0 to 1.0

        Returns:
            New ExtGState with stroke alpha set
        """
        ...

    def alpha(self, alpha: float) -> ExtGState:
        """
        Set both fill and stroke opacity.

        Args:
            alpha: Opacity value from 0.0 to 1.0

        Returns:
            New ExtGState with both alphas set
        """
        ...

    def blend_mode(self, mode: BlendMode) -> ExtGState:
        """
        Set blend mode.

        Args:
            mode: Blend mode to use

        Returns:
            New ExtGState with blend mode set
        """
        ...

    @staticmethod
    def semi_transparent() -> ExtGState:
        """Create semi-transparent state (50% opacity)."""
        ...

    def __repr__(self) -> str:
        """String representation."""
        ...

class LinearGradient:
    """
    Linear gradient builder.

    Creates linear gradients for PDF graphics.

    Example:
        >>> gradient = LinearGradient() \\
        ...     .start(0, 0).end(100, 100) \\
        ...     .add_stop(0.0, Color.red()) \\
        ...     .add_stop(1.0, Color.blue())
    """

    def __init__(self) -> None:
        """Create a new linear gradient."""
        ...

    def start(self, x: float, y: float) -> LinearGradient:
        """
        Set start point.

        Args:
            x: X coordinate
            y: Y coordinate

        Returns:
            New gradient with start point set
        """
        ...

    def end(self, x: float, y: float) -> LinearGradient:
        """
        Set end point.

        Args:
            x: X coordinate
            y: Y coordinate

        Returns:
            New gradient with end point set
        """
        ...

    def add_stop(self, position: float, color: Color) -> LinearGradient:
        """
        Add a color stop.

        Args:
            position: Position along gradient (0.0 to 1.0)
            color: Color at this position

        Returns:
            New gradient with color stop added
        """
        ...

    def extend(self, extend: bool) -> LinearGradient:
        """
        Set whether to extend gradient beyond endpoints.

        Args:
            extend: True to extend gradient

        Returns:
            New gradient with extend setting
        """
        ...

    @staticmethod
    def horizontal(width: float, start_color: Color, end_color: Color) -> LinearGradient:
        """
        Create a horizontal gradient.

        Args:
            width: Width of gradient
            start_color: Color at left edge
            end_color: Color at right edge

        Returns:
            Horizontal gradient
        """
        ...

    @staticmethod
    def vertical(height: float, start_color: Color, end_color: Color) -> LinearGradient:
        """
        Create a vertical gradient.

        Args:
            height: Height of gradient
            start_color: Color at bottom edge
            end_color: Color at top edge

        Returns:
            Vertical gradient
        """
        ...

    def __repr__(self) -> str:
        """String representation."""
        ...

class RadialGradient:
    """
    Radial gradient builder.

    Creates radial gradients for PDF graphics.

    Example:
        >>> gradient = RadialGradient.centered(50, 50, 50) \\
        ...     .add_stop(0.0, Color.white()) \\
        ...     .add_stop(1.0, Color.black())
    """

    def __init__(self) -> None:
        """Create a new radial gradient."""
        ...

    @staticmethod
    def centered(cx: float, cy: float, radius: float) -> RadialGradient:
        """
        Create a centered radial gradient.

        Args:
            cx: Center X coordinate
            cy: Center Y coordinate
            radius: Outer radius

        Returns:
            Centered radial gradient
        """
        ...

    def inner_circle(self, cx: float, cy: float, radius: float) -> RadialGradient:
        """
        Set inner circle.

        Args:
            cx: Center X coordinate
            cy: Center Y coordinate
            radius: Inner radius

        Returns:
            New gradient with inner circle set
        """
        ...

    def outer_circle(self, cx: float, cy: float, radius: float) -> RadialGradient:
        """
        Set outer circle.

        Args:
            cx: Center X coordinate
            cy: Center Y coordinate
            radius: Outer radius

        Returns:
            New gradient with outer circle set
        """
        ...

    def add_stop(self, position: float, color: Color) -> RadialGradient:
        """
        Add a color stop.

        Args:
            position: Position along gradient (0.0 = inner, 1.0 = outer)
            color: Color at this position

        Returns:
            New gradient with color stop added
        """
        ...

    def __repr__(self) -> str:
        """String representation."""
        ...

class LineCap:
    """
    Line cap styles.

    Controls how line endpoints are drawn.
    """

    @staticmethod
    def BUTT() -> LineCap:
        """Butt cap (default) - line ends at endpoint."""
        ...

    @staticmethod
    def ROUND() -> LineCap:
        """Round cap - semicircular cap at endpoint."""
        ...

    @staticmethod
    def SQUARE() -> LineCap:
        """Square cap - square cap extending beyond endpoint."""
        ...

class LineJoin:
    """
    Line join styles.

    Controls how line segments are joined.
    """

    @staticmethod
    def MITER() -> LineJoin:
        """Miter join (default) - sharp corner."""
        ...

    @staticmethod
    def ROUND() -> LineJoin:
        """Round join - rounded corner."""
        ...

    @staticmethod
    def BEVEL() -> LineJoin:
        """Bevel join - flat corner."""
        ...

class PatternPresets:
    """
    Pattern presets for common fill patterns.

    Provides factory methods for common tiling patterns.

    Example:
        >>> content = PatternPresets.checkerboard(10, Color.white(), Color.black())
    """

    @staticmethod
    def horizontal_stripes(
        width: float, height: float, stripe_height: float, color: Color
    ) -> bytes:
        """
        Create horizontal stripes pattern.

        Args:
            width: Pattern width
            height: Pattern height
            stripe_height: Height of each stripe
            color: Stripe color

        Returns:
            Pattern content stream bytes
        """
        ...

    @staticmethod
    def vertical_stripes(width: float, height: float, stripe_width: float, color: Color) -> bytes:
        """
        Create vertical stripes pattern.

        Args:
            width: Pattern width
            height: Pattern height
            stripe_width: Width of each stripe
            color: Stripe color

        Returns:
            Pattern content stream bytes
        """
        ...

    @staticmethod
    def checkerboard(size: float, color1: Color, color2: Color) -> bytes:
        """
        Create checkerboard pattern.

        Args:
            size: Size of each square
            color1: First color
            color2: Second color

        Returns:
            Pattern content stream bytes
        """
        ...

    @staticmethod
    def dots(spacing: float, radius: float, color: Color) -> bytes:
        """
        Create dot pattern.

        Args:
            spacing: Distance between dot centers
            radius: Dot radius
            color: Dot color

        Returns:
            Pattern content stream bytes
        """
        ...

    @staticmethod
    def diagonal_lines(size: float, line_width: float, color: Color) -> bytes:
        """
        Create diagonal lines pattern.

        Args:
            size: Pattern cell size
            line_width: Line width
            color: Line color

        Returns:
            Pattern content stream bytes
        """
        ...

    @staticmethod
    def crosshatch(size: float, line_width: float, color: Color) -> bytes:
        """
        Create crosshatch pattern.

        Args:
            size: Pattern cell size
            line_width: Line width
            color: Line color

        Returns:
            Pattern content stream bytes
        """
        ...

# ============================================================================
# Office Conversion (optional, requires office feature)
# ============================================================================

class OfficeConverter:
    """
    Office to PDF conversion (requires office feature).

    Converts Microsoft Office documents (DOCX, XLSX, PPTX) to PDF.

    Example:
        >>> # Convert a Word document to PDF
        >>> pdf = OfficeConverter.from_docx("document.docx")
        >>> pdf.save("document.pdf")
        >>>
        >>> # Convert from bytes
        >>> with open("spreadsheet.xlsx", "rb") as f:
        ...     pdf = OfficeConverter.from_xlsx_bytes(f.read())
        ...     pdf.save("spreadsheet.pdf")
        >>>
        >>> # Auto-detect format and convert
        >>> pdf = OfficeConverter.convert("presentation.pptx")
        >>> pdf.save("presentation.pdf")
    """

    @staticmethod
    def from_docx(path: str) -> Pdf:
        """
        Convert a DOCX file to PDF.

        Args:
            path: Path to the DOCX file

        Returns:
            Created PDF document

        Raises:
            IOError: If the file cannot be read
            RuntimeError: If conversion fails

        Example:
            >>> pdf = OfficeConverter.from_docx("document.docx")
            >>> pdf.save("document.pdf")
        """
        ...

    @staticmethod
    def from_docx_bytes(data: bytes) -> Pdf:
        """
        Convert DOCX bytes to PDF.

        Args:
            data: DOCX file contents

        Returns:
            Created PDF document

        Raises:
            RuntimeError: If conversion fails

        Example:
            >>> with open("document.docx", "rb") as f:
            ...     pdf = OfficeConverter.from_docx_bytes(f.read())
            >>> pdf.save("document.pdf")
        """
        ...

    @staticmethod
    def from_xlsx(path: str) -> Pdf:
        """
        Convert an XLSX file to PDF.

        Args:
            path: Path to the XLSX file

        Returns:
            Created PDF document

        Raises:
            IOError: If the file cannot be read
            RuntimeError: If conversion fails

        Example:
            >>> pdf = OfficeConverter.from_xlsx("spreadsheet.xlsx")
            >>> pdf.save("spreadsheet.pdf")
        """
        ...

    @staticmethod
    def from_xlsx_bytes(data: bytes) -> Pdf:
        """
        Convert XLSX bytes to PDF.

        Args:
            data: XLSX file contents

        Returns:
            Created PDF document

        Raises:
            RuntimeError: If conversion fails

        Example:
            >>> with open("spreadsheet.xlsx", "rb") as f:
            ...     pdf = OfficeConverter.from_xlsx_bytes(f.read())
            >>> pdf.save("spreadsheet.pdf")
        """
        ...

    @staticmethod
    def from_pptx(path: str) -> Pdf:
        """
        Convert a PPTX file to PDF.

        Args:
            path: Path to the PPTX file

        Returns:
            Created PDF document

        Raises:
            IOError: If the file cannot be read
            RuntimeError: If conversion fails

        Example:
            >>> pdf = OfficeConverter.from_pptx("presentation.pptx")
            >>> pdf.save("presentation.pdf")
        """
        ...

    @staticmethod
    def from_pptx_bytes(data: bytes) -> Pdf:
        """
        Convert PPTX bytes to PDF.

        Args:
            data: PPTX file contents

        Returns:
            Created PDF document

        Raises:
            RuntimeError: If conversion fails

        Example:
            >>> with open("presentation.pptx", "rb") as f:
            ...     pdf = OfficeConverter.from_pptx_bytes(f.read())
            >>> pdf.save("presentation.pdf")
        """
        ...

    @staticmethod
    def convert(path: str) -> Pdf:
        """
        Auto-detect format and convert to PDF.

        Detects the file format based on extension and converts to PDF.
        Supports .docx, .xlsx, .xls, and .pptx files.

        Args:
            path: Path to the Office document

        Returns:
            Created PDF document

        Raises:
            IOError: If the file cannot be read
            RuntimeError: If conversion fails or format is unsupported

        Example:
            >>> pdf = OfficeConverter.convert("document.docx")
            >>> pdf.save("document.pdf")
        """
        ...

# ============================================================================
# OCR (optional, requires ocr feature)
# ============================================================================

class OcrConfig:
    """
    OCR configuration.

    Args:
        use_v5: Use PaddleOCR v5 models (default: False)
        det_threshold: Detection confidence threshold (default: 0.3)
        box_threshold: Box confidence threshold (default: 0.5)
        rec_threshold: Recognition confidence threshold (default: 0.5)
        num_threads: Number of threads for ONNX Runtime (default: 0 = auto)
        max_candidates: Maximum number of candidates for recognition (default: 1)
    """

    def __init__(
        self,
        use_v5: bool = False,
        det_threshold: float = 0.3,
        box_threshold: float = 0.5,
        rec_threshold: float = 0.5,
        num_threads: int = 0,
        max_candidates: int = 1,
    ) -> None: ...

class OcrEngine:
    """
    OCR engine for extracting text from scanned PDF pages.

    Requires the `ocr` feature to be enabled at build time.

    Example:
        >>> engine = OcrEngine("det.onnx", "rec.onnx", "dict.txt")
        >>> text = doc.extract_text_ocr(0, engine)
    """

    def __init__(
        self,
        det_model_path: str,
        rec_model_path: str,
        dict_path: str,
        config: Optional[OcrConfig] = None,
    ) -> None:
        """
        Create a new OCR engine.

        Args:
            det_model_path: Path to the text detection ONNX model
            rec_model_path: Path to the text recognition ONNX model
            dict_path: Path to the character dictionary file
            config: Optional OCR configuration
        """
        ...

# ============================================================================
# Module-level constants
# ============================================================================

VERSION: str
"""Package version string."""
