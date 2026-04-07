#!/usr/bin/env python3
"""
Create test fixture PDFs for rendering regression tests.
"""

import os
import sys
from pathlib import Path

def create_simple_test_pdf():
    """Create a simple PDF with known text at known positions using ReportLab."""
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
    except ImportError:
        print("ReportLab not available, creating minimal PDF manually")
        return create_minimal_pdf()
    
    output_path = Path(__file__).parent / "simple_test.pdf"
    
    # Create PDF with known text at specific coordinates
    c = canvas.Canvas(str(output_path), pagesize=letter)
    width, height = letter
    
    # Add text at known positions for testing
    c.setFont("Helvetica", 12)
    c.drawString(100, height - 100, "Hello World")  # Top area
    c.drawString(100, height - 200, "Test Text Line 1")
    c.drawString(100, height - 220, "Test Text Line 2")
    
    # Add some larger text
    c.setFont("Helvetica-Bold", 18)
    c.drawString(100, height - 300, "Large Bold Text")
    
    # Add text in different positions
    c.setFont("Times-Roman", 10)
    c.drawString(300, height - 400, "Right side text")
    c.drawString(50, 100, "Bottom text")
    
    c.save()
    return output_path

def create_minimal_pdf():
    """Create a minimal PDF without ReportLab dependencies."""
    output_path = Path(__file__).parent / "simple_test.pdf"
    
    # Minimal PDF with "Hello World" text
    pdf_content = """%PDF-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj

2 0 obj
<<
/Type /Pages
/Kids [3 0 R]
/Count 1
>>
endobj

3 0 obj
<<
/Type /Page
/Parent 2 0 R
/MediaBox [0 0 612 792]
/Contents 4 0 R
/Resources <<
  /Font <<
    /F1 5 0 R
  >>
>>
>>
endobj

4 0 obj
<<
/Length 44
>>
stream
BT
/F1 12 Tf
100 700 Td
(Hello World) Tj
ET
endstream
endobj

5 0 obj
<<
/Type /Font
/Subtype /Type1
/BaseFont /Helvetica
>>
endobj

xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000273 00000 n 
0000000367 00000 n 
trailer
<<
/Size 6
/Root 1 0 R
>>
startxref
445
%%EOF"""
    
    with open(output_path, 'w') as f:
        f.write(pdf_content)
    
    return output_path

if __name__ == "__main__":
    pdf_path = create_simple_test_pdf()
    print(f"Created test PDF: {pdf_path}")