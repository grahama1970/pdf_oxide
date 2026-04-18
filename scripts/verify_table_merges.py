#!/usr/bin/env python3
"""Table merge candidate verification.

Finds table merge candidates deterministically, then uses VLM to adjudicate.

Usage:
    python scripts/verify_table_merges.py --pdf /path/to/doc.pdf
    python scripts/verify_table_merges.py --pdf /path/to/doc.pdf --output merges.json
"""

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path

import fitz
import httpx

SCILLM_URL = "http://localhost:4001/v1/chat/completions"
SCILLM_HEADERS = {
    "Authorization": f"Bearer {os.environ.get('SCILLM_API_KEY', 'sk-dev-proxy-123')}",
    "X-Caller-Skill": "verify-table-merges",
}

PROMPT_PATH = Path("/tmp/verify_table_merge_prompt.txt")


def extract_blocks(pdf_path: Path, max_pages: int | None = None) -> list[dict]:
    """Extract blocks from PDF."""
    from pdf_oxide.extract_for_pdflab import extract_pdf

    result = extract_pdf(pdf_path)
    blocks = result.get("blocks", [])

    if max_pages:
        blocks = [b for b in blocks if b.get("page", 0) < max_pages]

    return blocks


def find_merge_candidates(
    blocks: list[dict],
    bottom_threshold: float = 0.75,
    top_threshold: float = 0.25,
) -> list[dict]:
    """Find table merge candidates deterministically.

    Policy:
    - Adjacent pages only
    - Candidate A = bottommost table on page A (by y1)
    - Candidate B = topmost table on page B (by y0)
    - Require positional cue: A near bottom OR B near top
    """
    # Group table blocks by page
    tables_by_page: dict[int, list[dict]] = {}
    for b in blocks:
        if b.get("blockType") != "table":
            continue
        page = b.get("page", 0)
        tables_by_page.setdefault(page, []).append(b)

    candidates = []
    pages = sorted(tables_by_page.keys())

    for i, page_a in enumerate(pages[:-1]):
        page_b = pages[i + 1]

        if page_b != page_a + 1:
            continue

        tables_a = tables_by_page.get(page_a, [])
        tables_b = tables_by_page.get(page_b, [])

        if not tables_a or not tables_b:
            continue

        # Bottommost on A, topmost on B
        candidate_a = max(tables_a, key=lambda b: b["bbox"][3])
        candidate_b = min(tables_b, key=lambda b: b["bbox"][1])

        # Positional filter
        a_near_bottom = candidate_a["bbox"][3] >= bottom_threshold
        b_near_top = candidate_b["bbox"][1] <= top_threshold

        if not (a_near_bottom or b_near_top):
            continue

        candidates.append({
            "page_a": page_a,
            "page_b": page_b,
            "table_a": candidate_a,
            "table_b": candidate_b,
        })

    return candidates


def render_comparison(doc: fitz.Document, candidate: dict, context_ratio: float = 0.10, dpi: int = 150) -> bytes:
    """Render side-by-side comparison image."""
    from PIL import Image, ImageDraw, ImageFont
    import io

    def render_crop(page_num: int, bbox: list[float]) -> Image.Image:
        page = doc[page_num]
        width, height = page.rect.width, page.rect.height

        x0, y0, x1, y1 = bbox[0] * width, bbox[1] * height, bbox[2] * width, bbox[3] * height
        block_height = y1 - y0
        context_px = block_height * context_ratio

        crop_y0 = max(0, y0 - context_px)
        crop_y1 = min(height, y1 + context_px)
        crop_rect = fitz.Rect(0, crop_y0, width, crop_y1)

        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, clip=crop_rect)
        return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

    img_a = render_crop(candidate["page_a"], candidate["table_a"]["bbox"])
    img_b = render_crop(candidate["page_b"], candidate["table_b"]["bbox"])

    # Side by side
    gap = 20
    label_height = 30
    max_height = max(img_a.height, img_b.height)
    canvas = Image.new("RGB", (img_a.width + gap + img_b.width, max_height + label_height), (255, 255, 255))

    y_a = label_height + (max_height - img_a.height) // 2
    y_b = label_height + (max_height - img_b.height) // 2
    canvas.paste(img_a, (0, y_a))
    canvas.paste(img_b, (img_a.width + gap, y_b))

    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    draw.text((10, 5), f"Page {candidate['page_a']} (left)", fill=(0, 0, 0), font=font)
    draw.text((img_a.width + gap + 10, 5), f"Page {candidate['page_b']} (right)", fill=(0, 0, 0), font=font)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


async def verify_candidate(client: httpx.AsyncClient, candidate: dict, img_bytes: bytes, prompt: str) -> dict:
    """Verify a single merge candidate via VLM."""
    img_b64 = base64.b64encode(img_bytes).decode()

    user_data = {
        "page_a": candidate["page_a"],
        "page_b": candidate["page_b"],
        "table_a_block_id": candidate["table_a"]["id"],
        "table_b_block_id": candidate["table_b"]["id"],
        "table_a_extracted_text": candidate["table_a"].get("text", "")[:1000],
        "table_b_extracted_text": candidate["table_b"].get("text", "")[:1000],
        "table_a_bbox_normalized": candidate["table_a"]["bbox"],
        "table_b_bbox_normalized": candidate["table_b"]["bbox"],
    }

    try:
        resp = await client.post(
            SCILLM_URL,
            headers=SCILLM_HEADERS,
            json={
                "model": "vlm-claude",
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": [
                        {"type": "text", "text": json.dumps(user_data, indent=2)},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    ]},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        return json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception as e:
        return {
            "page_a": candidate["page_a"],
            "page_b": candidate["page_b"],
            "should_merge": False,
            "merge_action": "do_not_merge",
            "confidence": "low",
            "reason": f"VLM error: {type(e).__name__}",
            "_error": str(e),
        }


async def run_verification(pdf_path: Path, candidates: list[dict], prompt: str) -> list[dict]:
    """Verify all candidates sequentially."""
    doc = fitz.open(str(pdf_path))
    results = []

    async with httpx.AsyncClient() as client:
        for i, candidate in enumerate(candidates):
            print(f"  [{i+1}/{len(candidates)}] Pages {candidate['page_a']}-{candidate['page_b']}...", end=" ", flush=True)

            img_bytes = render_comparison(doc, candidate)
            result = await verify_candidate(client, candidate, img_bytes, prompt)
            results.append(result)

            action = result.get("merge_action", "unknown")
            confidence = result.get("confidence", "?")
            print(f"→ {action} ({confidence})")

    doc.close()
    return results


def main():
    parser = argparse.ArgumentParser(description="Verify table merge candidates")
    parser.add_argument("--pdf", "-p", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--output", "-o", type=Path, default=None)

    args = parser.parse_args()

    if not PROMPT_PATH.exists():
        print(f"Error: Prompt not found at {PROMPT_PATH}")
        return

    prompt = PROMPT_PATH.read_text()

    print(f"Extracting {args.pdf.name}...")
    blocks = extract_blocks(args.pdf, args.max_pages)
    tables = [b for b in blocks if b.get("blockType") == "table"]
    print(f"  {len(tables)} tables found")

    print("Finding merge candidates...")
    candidates = find_merge_candidates(blocks)
    print(f"  {len(candidates)} candidates")

    if not candidates:
        print("No merge candidates found.")
        return

    print("Running VLM verification...")
    results = asyncio.run(run_verification(args.pdf, candidates, prompt))

    # Summary
    merge_count = sum(1 for r in results if r.get("should_merge", False))
    print(f"\n{'='*60}")
    print(f"Merge: {merge_count}")
    print(f"Do not merge: {len(results) - merge_count}")

    if args.output:
        args.output.write_text(json.dumps(results, indent=2))
        print(f"Results saved to: {args.output}")

    # Show merge decisions
    print("\nDecisions:")
    for r in results:
        action = r.get("merge_action", "unknown")
        reason = r.get("reason", "")[:50]
        print(f"  Pages {r['page_a']}-{r['page_b']}: {action} — {reason}")


if __name__ == "__main__":
    main()
