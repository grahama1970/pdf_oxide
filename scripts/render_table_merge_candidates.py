#!/usr/bin/env python3
"""Render side-by-side comparison images for table merge candidates.

For each candidate pair (last table on page N, first table on page N+1),
creates a composite image with:
- Left panel: table A with ~10% vertical context
- Right panel: table B with ~10% vertical context
- Labels showing page numbers

Usage:
    python scripts/render_table_merge_candidates.py \
        --extraction /tmp/nist-extraction.json \
        --pdf /mnt/12tb/compliance/nist/NIST_SP_800-53r5.pdf \
        --output /tmp/merge_candidates/
"""

import argparse
import json
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont


def find_merge_candidates(
    extraction_path: Path,
    bottom_threshold: float = 0.75,
    top_threshold: float = 0.25,
) -> list[dict]:
    """Find deterministic merge candidate pairs.

    Selection policy:
    - Adjacent pages only
    - Candidate A = bottommost table block on page A (by y1, not y0)
    - Candidate B = topmost table block on page B (by y0)
    - Positional prefilter: A near page bottom OR B near page top

    This avoids brittleness from footers, headers, captions, or page numbers
    that might appear after/before the actual table content.

    Args:
        extraction_path: Path to extraction JSON
        bottom_threshold: Candidate A must end below this fraction of page height (0.75 = lower 25%)
        top_threshold: Candidate B must start above this fraction of page height (0.25 = upper 25%)
    """
    with open(extraction_path) as f:
        data = json.load(f)

    blocks = data.get('blocks', [])

    # Group table blocks by page
    tables_by_page: dict[int, list[dict]] = {}
    for b in blocks:
        if b.get('blockType') != 'table':
            continue
        page = b.get('page', 0)
        tables_by_page.setdefault(page, []).append(b)

    candidates = []
    pages = sorted(tables_by_page.keys())

    for i, page_a in enumerate(pages[:-1]):
        page_b = pages[i + 1]

        # Skip if not consecutive
        if page_b != page_a + 1:
            continue

        tables_a = tables_by_page.get(page_a, [])
        tables_b = tables_by_page.get(page_b, [])

        if not tables_a or not tables_b:
            continue

        # Bottommost table on page A (largest y1 / bbox[3])
        candidate_a = max(tables_a, key=lambda b: b['bbox'][3])

        # Topmost table on page B (smallest y0 / bbox[1])
        candidate_b = min(tables_b, key=lambda b: b['bbox'][1])

        # Positional prefilter: require at least one positional cue
        # - A ends in lower portion of page, OR
        # - B starts in upper portion of page
        a_near_bottom = candidate_a['bbox'][3] >= bottom_threshold
        b_near_top = candidate_b['bbox'][1] <= top_threshold

        if not (a_near_bottom or b_near_top):
            continue

        candidates.append({
            'page_a': page_a,
            'page_b': page_b,
            'table_a': candidate_a,
            'table_b': candidate_b,
        })

    return candidates


def render_crop_with_context(
    doc: fitz.Document,
    page_num: int,
    bbox_normalized: list[float],
    context_ratio: float = 0.10,
    dpi: int = 150,
) -> Image.Image:
    """Render a cropped region with vertical context."""
    page = doc[page_num]
    width, height = page.rect.width, page.rect.height

    # Convert normalized bbox to absolute
    x0 = bbox_normalized[0] * width
    y0 = bbox_normalized[1] * height
    x1 = bbox_normalized[2] * width
    y1 = bbox_normalized[3] * height

    # Add vertical context
    block_height = y1 - y0
    context_px = block_height * context_ratio

    # Expand vertically, clamp to page bounds
    crop_y0 = max(0, y0 - context_px)
    crop_y1 = min(height, y1 + context_px)

    # Keep full width for better context
    crop_rect = fitz.Rect(0, crop_y0, width, crop_y1)

    # Render at higher resolution for the crop
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, clip=crop_rect)

    # Convert to PIL
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    return img


def create_comparison_image(
    img_a: Image.Image,
    img_b: Image.Image,
    page_a: int,
    page_b: int,
    gap: int = 20,
) -> Image.Image:
    """Create side-by-side comparison with labels."""
    # Determine canvas size
    max_height = max(img_a.height, img_b.height)
    label_height = 30
    total_width = img_a.width + gap + img_b.width
    total_height = max_height + label_height

    # Create canvas
    canvas = Image.new('RGB', (total_width, total_height), color=(255, 255, 255))

    # Paste images (vertically centered)
    y_offset_a = label_height + (max_height - img_a.height) // 2
    y_offset_b = label_height + (max_height - img_b.height) // 2

    canvas.paste(img_a, (0, y_offset_a))
    canvas.paste(img_b, (img_a.width + gap, y_offset_b))

    # Draw labels
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16)
    except OSError:
        font = ImageFont.load_default()

    # Left label
    label_a = f"Page {page_a} (left)"
    draw.text((10, 5), label_a, fill=(0, 0, 0), font=font)

    # Right label
    label_b = f"Page {page_b} (right)"
    draw.text((img_a.width + gap + 10, 5), label_b, fill=(0, 0, 0), font=font)

    # Draw separator line
    sep_x = img_a.width + gap // 2
    draw.line([(sep_x, label_height), (sep_x, total_height)], fill=(200, 200, 200), width=2)

    return canvas


def render_merge_candidate(
    pdf_path: Path,
    candidate: dict,
    output_dir: Path,
    dpi: int = 150,
) -> Path:
    """Render a single merge candidate comparison image."""
    doc = fitz.open(str(pdf_path))

    page_a = candidate['page_a']
    page_b = candidate['page_b']
    table_a = candidate['table_a']
    table_b = candidate['table_b']

    # Render crops with context
    img_a = render_crop_with_context(doc, page_a, table_a['bbox'], dpi=dpi)
    img_b = render_crop_with_context(doc, page_b, table_b['bbox'], dpi=dpi)

    # Create comparison
    comparison = create_comparison_image(img_a, img_b, page_a, page_b)

    # Save
    output_path = output_dir / f"merge_candidate_p{page_a}_p{page_b}.png"
    comparison.save(str(output_path))

    doc.close()
    return output_path


def prepare_merge_input(candidate: dict, image_path: Path) -> dict:
    """Prepare input JSON for the merge verifier."""
    return {
        'page_a': candidate['page_a'],
        'page_b': candidate['page_b'],
        'table_a_block_id': candidate['table_a']['id'],
        'table_b_block_id': candidate['table_b']['id'],
        'table_a_extracted_text': candidate['table_a'].get('text', '')[:1000],
        'table_b_extracted_text': candidate['table_b'].get('text', '')[:1000],
        'table_a_bbox_normalized': candidate['table_a']['bbox'],
        'table_b_bbox_normalized': candidate['table_b']['bbox'],
        'image_path': str(image_path),
    }


def main():
    parser = argparse.ArgumentParser(description='Render table merge candidate images')
    parser.add_argument('--extraction', '-e', type=Path, required=True, help='Extraction JSON path')
    parser.add_argument('--pdf', '-p', type=Path, required=True, help='Source PDF path')
    parser.add_argument('--output', '-o', type=Path, default=Path('/tmp/merge_candidates'))
    parser.add_argument('--dpi', type=int, default=150)

    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    # Find candidates
    print(f'Finding merge candidates in {args.extraction}...')
    candidates = find_merge_candidates(args.extraction)
    print(f'  Found {len(candidates)} candidate pairs')

    if not candidates:
        print('No table merge candidates found.')
        return

    # Render each candidate
    print(f'Rendering comparison images...')
    inputs = []
    for i, candidate in enumerate(candidates):
        print(f'  [{i+1}/{len(candidates)}] Pages {candidate["page_a"]}-{candidate["page_b"]}')
        image_path = render_merge_candidate(args.pdf, candidate, args.output, args.dpi)
        inputs.append(prepare_merge_input(candidate, image_path))

    # Save inputs for verifier
    inputs_path = args.output / 'merge_inputs.json'
    with open(inputs_path, 'w') as f:
        json.dump(inputs, f, indent=2)
    print(f'  Saved {len(inputs)} inputs to {inputs_path}')

    print(f'\nTo verify, run:')
    print(f'  python scripts/run_table_merge_verification.py --inputs {inputs_path}')


if __name__ == '__main__':
    main()
