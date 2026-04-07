#!/usr/bin/env python3
"""
Render comparison script for PDF rendering regression tests.

Renders each fixture page with both pdf_oxide render_page AND pdftoppm,
saving outputs side-by-side and providing a summary comparison table.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

# Add the parent directory to Python path to import pdf_oxide
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    import pdf_oxide
    PDF_OXIDE_AVAILABLE = True
except ImportError:
    PDF_OXIDE_AVAILABLE = False

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def load_manifest() -> Dict[str, Any]:
    """Load the test manifest."""
    manifest_path = Path(__file__).parent / "manifest.json"
    with open(manifest_path) as f:
        return json.load(f)


def ensure_fixture_exists(fixture_file: str) -> Path:
    """Ensure fixture file exists, creating if necessary."""
    base_dir = Path(__file__).parent
    fixture_path = base_dir / fixture_file
    
    if fixture_file == "simple_test.pdf":
        # Create the simple test PDF if it doesn't exist
        if not fixture_path.exists():
            print(f"Creating {fixture_file}...")
            create_fixtures_script = base_dir / "create_fixtures.py"
            subprocess.run([sys.executable, str(create_fixtures_script)], check=True)
    
    if not fixture_path.exists():
        print(f"Warning: Fixture file not found: {fixture_path}")
        return None
    
    return fixture_path


def render_with_pdf_oxide(pdf_path: Path, page: int, output_path: Path) -> bool:
    """
    Render a PDF page using pdf_oxide.
    
    Args:
        pdf_path: Path to PDF file
        page: Page number (1-indexed)
        output_path: Output image path
        
    Returns:
        True if successful, False otherwise
    """
    if not PDF_OXIDE_AVAILABLE:
        print("pdf_oxide not available")
        return False
    
    try:
        # Load PDF document
        doc = pdf_oxide.PdfDocument(str(pdf_path))
        
        # Render page (pdf_oxide uses 0-indexed pages internally)
        image_bytes = doc.render_page(page - 1, dpi=150)
        
        # Save image
        with open(output_path, 'wb') as f:
            f.write(image_bytes)
        
        return True
        
    except Exception as e:
        print(f"pdf_oxide render failed: {e}")
        return False


def render_with_pdftoppm(pdf_path: Path, page: int, output_path: Path) -> bool:
    """
    Render a PDF page using pdftoppm.
    
    Args:
        pdf_path: Path to PDF file
        page: Page number (1-indexed)
        output_path: Output image path
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Use pdftoppm to render single page
        cmd = [
            'pdftoppm',
            '-png',
            '-r', '150',  # 150 DPI to match pdf_oxide
            '-f', str(page),  # First page
            '-l', str(page),  # Last page
            str(pdf_path),
            str(output_path.with_suffix(''))  # pdftoppm adds -1.png suffix
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"pdftoppm failed: {result.stderr}")
            return False
        
        # pdftoppm creates filename-1.png, rename to our desired name
        generated_file = output_path.with_name(f"{output_path.stem}-1.png")
        if generated_file.exists():
            generated_file.rename(output_path)
            return True
        else:
            print(f"pdftoppm output not found: {generated_file}")
            return False
            
    except FileNotFoundError:
        print("pdftoppm not found. Install poppler-utils: sudo apt-get install poppler-utils")
        return False
    except Exception as e:
        print(f"pdftoppm render failed: {e}")
        return False


def create_side_by_side_comparison(pdf_oxide_path: Path, pdftoppm_path: Path, output_path: Path) -> bool:
    """Create a side-by-side comparison image."""
    if not PIL_AVAILABLE:
        print("PIL not available, skipping side-by-side comparison")
        return False
    
    try:
        # Load images
        img1 = Image.open(pdf_oxide_path) if pdf_oxide_path.exists() else None
        img2 = Image.open(pdftoppm_path) if pdftoppm_path.exists() else None
        
        if img1 is None and img2 is None:
            return False
        
        # Handle case where only one image exists
        if img1 is None:
            img1 = Image.new('RGB', img2.size, color='white')
        if img2 is None:
            img2 = Image.new('RGB', img1.size, color='white')
        
        # Resize to same height if needed
        if img1.height != img2.height:
            target_height = min(img1.height, img2.height)
            img1 = img1.resize((int(img1.width * target_height / img1.height), target_height))
            img2 = img2.resize((int(img2.width * target_height / img2.height), target_height))
        
        # Create side-by-side image
        total_width = img1.width + img2.width + 10  # 10px gap
        combined = Image.new('RGB', (total_width, img1.height), color='white')
        
        combined.paste(img1, (0, 0))
        combined.paste(img2, (img1.width + 10, 0))
        
        combined.save(output_path)
        return True
        
    except Exception as e:
        print(f"Failed to create comparison: {e}")
        return False


def calculate_simple_density(image_path: Path) -> float:
    """Calculate a simple non-white pixel density for the entire image."""
    if not PIL_AVAILABLE or not image_path.exists():
        return 0.0
    
    try:
        img = Image.open(image_path).convert('RGB')
        pixels = list(img.getdata())
        
        non_white_count = 0
        for r, g, b in pixels:
            if r < 250 or g < 250 or b < 250:  # Allow slight tolerance
                non_white_count += 1
        
        return non_white_count / len(pixels) if pixels else 0.0
        
    except Exception:
        return 0.0


def render_fixture(fixture: Dict[str, Any], output_dir: Path) -> Dict[str, Any]:
    """
    Render a single fixture with both renderers.
    
    Returns:
        Dictionary with render results and metrics
    """
    name = fixture['name']
    fixture_file = fixture['file']
    page = fixture['page']
    
    print(f"\nProcessing fixture: {name}")
    print(f"  File: {fixture_file}, Page: {page}")
    print(f"  Description: {fixture['description']}")
    
    # Ensure fixture file exists
    pdf_path = ensure_fixture_exists(fixture_file)
    if pdf_path is None:
        return {
            'name': name,
            'success': False,
            'error': 'Fixture file not found'
        }
    
    # Output paths
    pdf_oxide_path = output_dir / f"{name}_pdf_oxide.png"
    pdftoppm_path = output_dir / f"{name}_pdftoppm.png"
    comparison_path = output_dir / f"{name}_comparison.png"
    
    # Render with both tools
    pdf_oxide_success = render_with_pdf_oxide(pdf_path, page, pdf_oxide_path)
    pdftoppm_success = render_with_pdftoppm(pdf_path, page, pdftoppm_path)
    
    # Create side-by-side comparison
    comparison_success = create_side_by_side_comparison(pdf_oxide_path, pdftoppm_path, comparison_path)
    
    # Calculate simple densities for quick comparison
    pdf_oxide_density = calculate_simple_density(pdf_oxide_path)
    pdftoppm_density = calculate_simple_density(pdftoppm_path)
    
    print(f"  pdf_oxide: {'✓' if pdf_oxide_success else '✗'} (density: {pdf_oxide_density:.4f})")
    print(f"  pdftoppm:  {'✓' if pdftoppm_success else '✗'} (density: {pdftoppm_density:.4f})")
    
    return {
        'name': name,
        'success': pdf_oxide_success or pdftoppm_success,
        'pdf_oxide_success': pdf_oxide_success,
        'pdftoppm_success': pdftoppm_success,
        'comparison_success': comparison_success,
        'pdf_oxide_density': pdf_oxide_density,
        'pdftoppm_density': pdftoppm_density,
        'description': fixture['description']
    }


def main():
    """Main rendering comparison function."""
    print("PDF Rendering Regression Test")
    print("=" * 50)
    
    # Load manifest
    try:
        manifest = load_manifest()
    except Exception as e:
        print(f"Failed to load manifest: {e}")
        return 1
    
    # Create output directory
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)
    
    # Check dependencies
    print(f"pdf_oxide available: {PDF_OXIDE_AVAILABLE}")
    print(f"PIL available: {PIL_AVAILABLE}")
    
    # Process each fixture
    results = []
    for fixture in manifest['fixtures']:
        result = render_fixture(fixture, output_dir)
        results.append(result)
    
    # Print summary table
    print("\n" + "=" * 80)
    print("SUMMARY TABLE")
    print("=" * 80)
    print(f"{'Fixture':<20} {'pdf_oxide':<12} {'pdftoppm':<12} {'Density Comparison':<20}")
    print("-" * 80)
    
    for result in results:
        if not result['success']:
            print(f"{result['name']:<20} {'ERROR':<12} {'ERROR':<12} {result.get('error', 'Unknown error')}")
            continue
            
        pdf_oxide_status = f"{result['pdf_oxide_density']:.4f}" if result['pdf_oxide_success'] else "FAIL"
        pdftoppm_status = f"{result['pdftoppm_density']:.4f}" if result['pdftoppm_success'] else "FAIL"
        
        # Compare densities
        if result['pdf_oxide_success'] and result['pdftoppm_success']:
            ratio = result['pdf_oxide_density'] / result['pdftoppm_density'] if result['pdftoppm_density'] > 0 else float('inf')
            comparison = f"ratio: {ratio:.2f}"
        else:
            comparison = "incomplete"
        
        print(f"{result['name']:<20} {pdf_oxide_status:<12} {pdftoppm_status:<12} {comparison:<20}")
    
    print("-" * 80)
    successful_fixtures = sum(1 for r in results if r['success'])
    print(f"Processed: {successful_fixtures}/{len(results)} fixtures")
    print(f"Output directory: {output_dir}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())