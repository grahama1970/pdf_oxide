#!/usr/bin/env python3
"""
Score rendering outputs by computing non-white pixel density in known text regions.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any

try:
    from PIL import Image
    import numpy as np
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def load_manifest() -> Dict[str, Any]:
    """Load the test manifest."""
    manifest_path = Path(__file__).parent / "manifest.json"
    with open(manifest_path) as f:
        return json.load(f)


def calculate_non_white_density(image_path: Path, regions: List[Dict]) -> float:
    """
    Calculate the density of non-white pixels in specified regions.
    
    Args:
        image_path: Path to the rendered image
        regions: List of region dictionaries with x, y, width, height
        
    Returns:
        Fraction of non-white pixels in the regions (0.0 to 1.0)
    """
    if not PIL_AVAILABLE:
        print("PIL/Pillow not available, cannot score images")
        return 0.0
        
    if not image_path.exists():
        print(f"Image not found: {image_path}")
        return 0.0
    
    try:
        # Load image and convert to RGB
        img = Image.open(image_path).convert('RGB')
        img_array = np.array(img)
        
        total_pixels = 0
        non_white_pixels = 0
        
        for region in regions:
            x, y, width, height = region['x'], region['y'], region['width'], region['height']
            
            # Clamp region to image bounds
            x = max(0, min(x, img_array.shape[1]))
            y = max(0, min(y, img_array.shape[0]))
            x2 = max(x, min(x + width, img_array.shape[1]))
            y2 = max(y, min(y + height, img_array.shape[0]))
            
            if x2 <= x or y2 <= y:
                continue
                
            # Extract region
            region_pixels = img_array[y:y2, x:x2]
            
            # Count non-white pixels (allowing for slight variations)
            # White is (255, 255, 255), allow small tolerance
            white_threshold = 250
            is_white = np.all(region_pixels >= white_threshold, axis=2)
            
            region_total = region_pixels.shape[0] * region_pixels.shape[1]
            region_non_white = np.sum(~is_white)
            
            total_pixels += region_total
            non_white_pixels += region_non_white
        
        if total_pixels == 0:
            return 0.0
            
        return non_white_pixels / total_pixels
        
    except Exception as e:
        print(f"Error processing {image_path}: {e}")
        return 0.0


def score_fixture(fixture: Dict[str, Any], output_dir: Path) -> Dict[str, Any]:
    """
    Score a single fixture by comparing pdf_oxide and pdftoppm outputs.
    
    Returns:
        Dictionary with scoring results
    """
    name = fixture['name']
    
    # Paths to rendered images
    pdf_oxide_path = output_dir / f"{name}_pdf_oxide.png"
    pdftoppm_path = output_dir / f"{name}_pdftoppm.png"
    
    # Calculate densities
    pdf_oxide_density = calculate_non_white_density(pdf_oxide_path, fixture['text_regions'])
    pdftoppm_density = calculate_non_white_density(pdftoppm_path, fixture['text_regions'])
    
    return {
        'name': name,
        'description': fixture['description'],
        'pdf_oxide_density': pdf_oxide_density,
        'pdftoppm_density': pdftoppm_density,
        'pdf_oxide_exists': pdf_oxide_path.exists(),
        'pdftoppm_exists': pdftoppm_path.exists()
    }


def main():
    """Main scoring function."""
    manifest = load_manifest()
    output_dir = Path(__file__).parent / "output"
    
    if not output_dir.exists():
        print(f"Output directory not found: {output_dir}")
        print("Run render_comparison.py first to generate images")
        return 1
    
    pass_threshold = manifest['scoring']['pass_threshold']
    results = []
    
    print("Scoring rendering regression tests...")
    print("=" * 80)
    
    for fixture in manifest['fixtures']:
        result = score_fixture(fixture, output_dir)
        results.append(result)
        
        # Determine pass/fail
        pdf_oxide_pass = result['pdf_oxide_density'] >= pass_threshold
        pdftoppm_pass = result['pdftoppm_density'] >= pass_threshold
        
        print(f"\nFixture: {result['name']}")
        print(f"Description: {result['description']}")
        print(f"PDF Oxide density: {result['pdf_oxide_density']:.4f} {'✓' if pdf_oxide_pass else '✗'}")
        print(f"pdftoppm density:  {result['pdftoppm_density']:.4f} {'✓' if pdftoppm_pass else '✗'}")
        print(f"Images exist: pdf_oxide={result['pdf_oxide_exists']}, pdftoppm={result['pdftoppm_exists']}")
    
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    total_fixtures = len(results)
    pdf_oxide_passes = sum(1 for r in results if r['pdf_oxide_density'] >= pass_threshold)
    pdftoppm_passes = sum(1 for r in results if r['pdftoppm_density'] >= pass_threshold)
    
    print(f"Total fixtures: {total_fixtures}")
    print(f"PDF Oxide passes: {pdf_oxide_passes}/{total_fixtures}")
    print(f"pdftoppm passes: {pdftoppm_passes}/{total_fixtures}")
    print(f"Pass threshold: {pass_threshold}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())