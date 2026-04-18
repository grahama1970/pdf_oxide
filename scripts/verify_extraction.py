#!/usr/bin/env python3
"""PDF extraction verification with keep/discard loop.

Design pattern: propose → apply → score → keep/discard
- Proposer: human (fixes code based on VLM failures)
- Scorer: VLM verification accuracy
- Keep/discard: git commit on improvement, git checkout on regression

Usage:
    # First run - establish baseline
    python scripts/verify_extraction.py --pdf /path/to/doc.pdf --sample 30

    # Fix extraction code based on failures...

    # Re-run - auto keep/discard based on accuracy
    python scripts/verify_extraction.py --pdf /path/to/doc.pdf --sample 30

    # Reset baseline
    python scripts/verify_extraction.py --pdf /path/to/doc.pdf --sample 30 --reset
"""

import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import fitz
import httpx

# State file for tracking best accuracy
STATE_FILE = Path("/tmp/.verify_extraction_state.json")

# Files tracked for keep/discard
TRACKED_FILES = [
    "python/pdf_oxide/extract_for_pdflab.py",
    "python/pdf_oxide/extraction_scanner.py",
]

SCILLM_URL = "http://localhost:4001/v1/chat/completions"
SCILLM_HEADERS = {
    "Authorization": f"Bearer {os.environ.get('SCILLM_API_KEY', 'sk-dev-proxy-123')}",
    "X-Caller-Skill": "verify-extraction",
}

PROMPT_PATH = Path("/tmp/verify_extraction_checklist.txt")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"best_accuracy": 0.0, "best_commit": None}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def extract_pdf(pdf_path: Path, max_pages: int | None = None) -> list[dict]:
    """Run extraction and return blocks."""
    from pdf_oxide.extract_for_pdflab import extract_pdf as _extract

    result = _extract(pdf_path)
    blocks = result.get("blocks", [])

    # Filter by page if specified
    if max_pages:
        blocks = [b for b in blocks if b.get("page", 0) < max_pages]

    # Filter out boilerplate (page chrome)
    blocks = [b for b in blocks if b.get("blockType") != "boilerplate"]

    return blocks


def sample_blocks(blocks: list[dict], n: int) -> list[dict]:
    """Stratified sample by block type."""
    import random

    if not blocks:
        return []

    by_type: dict[str, list[dict]] = {}
    for b in blocks:
        t = b.get("blockType", "unknown")
        by_type.setdefault(t, []).append(b)

    samples = []
    types = list(by_type.keys())
    per_type = max(3, n // len(types)) if types else n

    for t in types:
        type_blocks = by_type[t]
        samples.extend(random.sample(type_blocks, min(per_type, len(type_blocks))))

    return samples[:n]


def group_by_page(blocks: list[dict]) -> dict[int, list[dict]]:
    """Group blocks by page number."""
    by_page: dict[int, list[dict]] = {}
    for b in blocks:
        page = b.get("page", 0)
        by_page.setdefault(page, []).append(b)
    return by_page


def render_page_with_bboxes(doc: fitz.Document, page_num: int, blocks: list[dict], dpi: int = 150) -> bytes:
    """Render page with numbered bbox overlays, return PNG bytes."""
    page = doc[page_num]
    width, height = page.rect.width, page.rect.height

    for i, block in enumerate(blocks):
        bbox = block["bbox"]
        rect = fitz.Rect(
            bbox[0] * width, bbox[1] * height,
            bbox[2] * width, bbox[3] * height,
        )
        page.draw_rect(rect, color=(1, 0, 0), width=1.5)
        page.insert_text((rect.x0 + 2, rect.y0 + 10), str(i + 1), fontsize=8, color=(1, 0, 0))

    pix = page.get_pixmap(dpi=dpi)
    return pix.tobytes("png")


async def verify_page(client: httpx.AsyncClient, page_num: int, blocks: list[dict], img_bytes: bytes, prompt: str) -> dict:
    """Verify blocks on a page via VLM."""
    img_b64 = base64.b64encode(img_bytes).decode()

    user_message = json.dumps({
        "page": page_num,
        "blocks": [
            {
                "block_id": b["id"],
                "bbox_normalized": b["bbox"],
                "extracted_text": b.get("text", "")[:500],
                "block_type": b.get("blockType", "unknown"),
            }
            for b in blocks
        ],
    }, indent=2)

    try:
        resp = await client.post(
            SCILLM_URL,
            headers=SCILLM_HEADERS,
            json={
                "model": "vlm-claude",
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": [
                        {"type": "text", "text": user_message},
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
        return {"_error": str(e), "checks": [], "pass_count": 0, "fail_count": len(blocks)}


async def run_verification(pdf_path: Path, blocks: list[dict], prompt: str) -> tuple[float, list[dict]]:
    """Run VLM verification, return (accuracy, failures)."""
    by_page = group_by_page(blocks)
    doc = fitz.open(str(pdf_path))

    all_checks = []
    total_pass = 0
    total_fail = 0

    async with httpx.AsyncClient() as client:
        for page_num, page_blocks in sorted(by_page.items()):
            print(f"  Page {page_num} ({len(page_blocks)} blocks)...", end=" ", flush=True)

            img_bytes = render_page_with_bboxes(doc, page_num, page_blocks)
            result = await verify_page(client, page_num, page_blocks, img_bytes, prompt)

            if "_error" in result:
                print(f"ERROR: {result['_error'][:40]}")
                total_fail += len(page_blocks)
            else:
                p = result.get("pass_count", 0)
                f = result.get("fail_count", 0)
                total_pass += p
                total_fail += f
                print(f"✓{p} ✗{f}")

                for check in result.get("checks", []):
                    if not check.get("pass", True):
                        check["page"] = page_num
                        all_checks.append(check)

    doc.close()

    total = total_pass + total_fail
    accuracy = total_pass / total if total > 0 else 0.0

    return accuracy, all_checks


def categorize_failures(failures: list[dict]) -> dict[str, list[dict]]:
    """Group failures by issue type."""
    categories: dict[str, list[dict]] = {}

    for f in failures:
        # Determine primary issue
        if not f.get("type_correct", True):
            cat = "classification_error"
        elif not f.get("bbox_contains_content", True):
            cat = "bbox_clips_content"
        elif not f.get("bbox_no_adjacent", True):
            cat = "bbox_includes_adjacent"
        elif not f.get("text_matches", True):
            cat = "text_mismatch"
        elif not f.get("text_complete", True):
            cat = "text_truncated"
        else:
            cat = "other"

        categories.setdefault(cat, []).append(f)

    return categories


def git_has_changes() -> bool:
    """Check if tracked files have uncommitted changes."""
    result = subprocess.run(
        ["git", "diff", "--name-only"] + TRACKED_FILES,
        capture_output=True, text=True, cwd=Path(__file__).parent.parent
    )
    return bool(result.stdout.strip())


def git_commit(message: str):
    """Commit tracked files."""
    cwd = Path(__file__).parent.parent
    subprocess.run(["git", "add"] + TRACKED_FILES, cwd=cwd)
    subprocess.run(["git", "commit", "-m", message], cwd=cwd)


def git_discard():
    """Discard changes to tracked files."""
    cwd = Path(__file__).parent.parent
    subprocess.run(["git", "checkout"] + TRACKED_FILES, cwd=cwd)


def main():
    parser = argparse.ArgumentParser(description="PDF extraction verification with keep/discard loop")
    parser.add_argument("--pdf", "-p", type=Path, required=True)
    parser.add_argument("--sample", "-n", type=int, default=30)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--reset", action="store_true", help="Reset baseline accuracy")
    parser.add_argument("--no-git", action="store_true", help="Disable git keep/discard")

    args = parser.parse_args()

    if not PROMPT_PATH.exists():
        print(f"Error: Prompt not found at {PROMPT_PATH}")
        sys.exit(1)

    prompt = PROMPT_PATH.read_text()
    state = load_state()

    if args.reset:
        state = {"best_accuracy": 0.0, "best_commit": None}
        save_state(state)
        print("Reset baseline to 0%")

    print(f"Extracting {args.pdf.name} (max {args.max_pages} pages)...")
    blocks = extract_pdf(args.pdf, args.max_pages)
    print(f"  {len(blocks)} content blocks")

    print(f"Sampling {args.sample} blocks...")
    sample = sample_blocks(blocks, args.sample)
    print(f"  {len(sample)} sampled across {len(group_by_page(sample))} pages")

    print("Running VLM verification...")
    accuracy, failures = asyncio.run(run_verification(args.pdf, sample, prompt))

    print(f"\n{'='*60}")
    print(f"Accuracy: {accuracy:.1%} ({len(sample) - len(failures)}/{len(sample)} passed)")
    print(f"Previous best: {state['best_accuracy']:.1%}")

    # Categorize and display failures
    if failures:
        print(f"\nFailures by category:")
        categories = categorize_failures(failures)
        for cat, items in sorted(categories.items(), key=lambda x: -len(x[1])):
            print(f"  {cat}: {len(items)}")
            for item in items[:3]:
                issues = ", ".join(item.get("issues", [])[:2])
                print(f"    - {item.get('block_id')} (page {item.get('page')}): {issues}")

    # Keep/discard logic
    if not args.no_git and git_has_changes():
        print(f"\n{'='*60}")
        if accuracy > state["best_accuracy"]:
            print(f"IMPROVED: {state['best_accuracy']:.1%} → {accuracy:.1%}")
            git_commit(f"extraction: {accuracy:.1%} accuracy (improved from {state['best_accuracy']:.1%})")
            state["best_accuracy"] = accuracy
            save_state(state)
            print("Changes committed.")
        else:
            print(f"REGRESSED: {accuracy:.1%} < {state['best_accuracy']:.1%}")
            git_discard()
            print("Changes discarded.")
    elif accuracy > state["best_accuracy"]:
        state["best_accuracy"] = accuracy
        save_state(state)
        print(f"\nNew baseline: {accuracy:.1%}")


if __name__ == "__main__":
    main()
