#!/usr/bin/env python3
"""Run the extraction calibration pipeline.

Pipeline:
1. Sample blocks from extraction JSON
2. Render bbox screenshots for each block
3. Run visual verifier (headless Sonnet, no bash)
4. Aggregate failures by pattern
5. Generate /code-runner task spec
6. (Optional) Run /code-runner repair agent

Usage:
    # Full pipeline with manual code-runner
    python scripts/run_extraction_calibration.py \\
        --extraction /tmp/nist-extraction.json \\
        --pdf /mnt/12tb/compliance/nist/NIST_SP_800-53r5.pdf \\
        --sample 50

    # Auto-run code-runner if failures found
    python scripts/run_extraction_calibration.py \\
        --extraction /tmp/nist-extraction.json \\
        --pdf /mnt/12tb/compliance/nist/NIST_SP_800-53r5.pdf \\
        --sample 50 \\
        --auto-repair
"""

import argparse
import asyncio
import base64
import json
import random
import subprocess
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
import httpx
import os


# Schema validation for VLM responses
REQUIRED_CHECK_FIELDS = {
    'block_id', 'bbox_contains_content', 'bbox_no_adjacent', 'bbox_tight',
    'text_matches', 'text_complete', 'text_no_extra', 'type_correct', 'pass', 'issues'
}


def validate_page_response(result: dict, expected_block_ids: list[str]) -> list[str]:
    """Validate VLM response against schema. Returns list of errors."""
    errors = []

    if 'checks' not in result:
        errors.append("Missing 'checks' array in response")
        return errors

    checks = result['checks']

    # Cardinality check
    if len(checks) != len(expected_block_ids):
        errors.append(f"Expected {len(expected_block_ids)} checks, got {len(checks)}")

    # Order/identity check
    returned_ids = [c.get('block_id', '') for c in checks]
    if returned_ids != expected_block_ids:
        errors.append(f"Block ID mismatch: expected {expected_block_ids}, got {returned_ids}")

    # Field validation per check
    for i, check in enumerate(checks):
        missing = REQUIRED_CHECK_FIELDS - set(check.keys())
        if missing:
            errors.append(f"Check {i}: missing fields {missing}")

        # Type checks
        if 'pass' in check and not isinstance(check['pass'], bool):
            errors.append(f"Check {i}: 'pass' must be bool")
        if 'issues' in check and not isinstance(check['issues'], list):
            errors.append(f"Check {i}: 'issues' must be list")

    return errors


def sample_blocks(extraction_path: Path, n: int, stratify: bool = True) -> list[dict]:
    """Sample blocks from extraction JSON, stratified by block type."""
    with open(extraction_path) as f:
        data = json.load(f)

    blocks = data.get('blocks', [])

    # Guard: empty input
    if not blocks:
        return []

    # Filter to blocks with valid 'id' field
    blocks = [b for b in blocks if 'id' in b]
    if not blocks:
        return []

    # Filter out boilerplate (page chrome - running headers, footers, page numbers)
    # These are not content blocks and should not be verified
    blocks = [b for b in blocks if b.get('blockType') != 'boilerplate']
    if not blocks:
        return []

    if not stratify:
        return random.sample(blocks, min(n, len(blocks)))

    # Stratify by blockType
    by_type: dict[str, list[dict]] = {}
    for b in blocks:
        t = b.get('blockType', 'unknown')
        by_type.setdefault(t, []).append(b)

    # Guard: empty types (shouldn't happen if blocks exist, but be safe)
    types = list(by_type.keys())
    if not types:
        return random.sample(blocks, min(n, len(blocks)))

    # Sample proportionally, minimum 3 per type
    samples = []
    per_type = max(3, n // len(types))

    for t in types:
        type_blocks = by_type[t]
        samples.extend(random.sample(type_blocks, min(per_type, len(type_blocks))))

    # Fill remainder randomly
    remaining = n - len(samples)
    if remaining > 0:
        all_ids = {b['id'] for b in samples}
        candidates = [b for b in blocks if b['id'] not in all_ids]
        samples.extend(random.sample(candidates, min(remaining, len(candidates))))

    return samples[:n]


def render_page_with_bboxes(pdf_path: Path, page_num: int, blocks: list[dict], output_dir: Path, dpi: int = 150) -> Path:
    """Render a full page with numbered bbox overlays."""
    doc = fitz.open(str(pdf_path))
    page = doc[page_num]
    width, height = page.rect.width, page.rect.height

    # Draw bbox rectangles with numbers
    for i, block in enumerate(blocks):
        bbox = block['bbox']
        rect = fitz.Rect(
            bbox[0] * width,
            bbox[1] * height,
            bbox[2] * width,
            bbox[3] * height,
        )
        # Red rectangle
        page.draw_rect(rect, color=(1, 0, 0), width=1.5)
        # Number label
        page.insert_text(
            (rect.x0 + 2, rect.y0 + 10),
            str(i + 1),
            fontsize=8,
            color=(1, 0, 0),
        )

    pix = page.get_pixmap(dpi=dpi)
    output_path = output_dir / f"page_{page_num}.png"
    pix.save(str(output_path))

    doc.close()
    return output_path


def prepare_page_input(page_num: int, blocks: list[dict], screenshot_path: Path) -> dict:
    """Prepare input JSON for page-level verification."""
    return {
        'page': page_num,
        'blocks': [
            {
                'block_id': b['id'],
                'bbox_normalized': b['bbox'],
                'extracted_text': b.get('text', '')[:500],  # Cap text length
                'block_type': b.get('blockType', 'unknown'),
            }
            for b in blocks
        ],
        'screenshot_path': str(screenshot_path),
    }


def group_blocks_by_page(blocks: list[dict]) -> dict[int, list[dict]]:
    """Group blocks by page number."""
    by_page: dict[int, list[dict]] = {}
    for b in blocks:
        page = b.get('page', 0)
        by_page.setdefault(page, []).append(b)
    return by_page


SCILLM_URL = "http://localhost:4001/v1/chat/completions"
SCILLM_HEADERS = {
    "Authorization": f"Bearer {os.environ.get('SCILLM_API_KEY', 'sk-dev-proxy-123')}",
    "X-Caller-Skill": "extraction-calibration",
}


async def verify_page(
    client: httpx.AsyncClient,
    page_input: dict,
    system_prompt: str,
) -> dict:
    """Verify all blocks on a page via single VLM call.

    Returns dict with:
    - On success: checks array with pass/fail for each block
    - On validation error: _validation_error flag (distinct from extraction failure)
    - On API error: _api_error flag (distinct from extraction failure)
    """
    # Read and base64-encode the page screenshot
    screenshot_path = Path(page_input['screenshot_path'])
    with open(screenshot_path, 'rb') as f:
        img_b64 = base64.b64encode(f.read()).decode()

    # Build user message with all blocks on page
    user_message = json.dumps({
        'page': page_input['page'],
        'blocks': page_input['blocks'],
    }, indent=2)

    expected_block_ids = [b['block_id'] for b in page_input['blocks']]

    try:
        resp = await client.post(
            SCILLM_URL,
            headers=SCILLM_HEADERS,
            json={
                "model": "vlm-claude",
                "messages": [
                    {"role": "system", "content": system_prompt},
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

        content = resp.json()["choices"][0]["message"]["content"]
        result = json.loads(content)
        result['page'] = page_input['page']

        # Schema validation - distinguish malformed VLM output from extraction failures
        validation_errors = validate_page_response(result, expected_block_ids)
        if validation_errors:
            return {
                'page': page_input['page'],
                'checks': [],
                'pass_count': 0,
                'fail_count': 0,
                '_validation_error': True,
                '_validation_errors': validation_errors,
                '_raw_response': content,
            }

        # Compute pass/fail counts if not already present
        if 'checks' in result and 'pass_count' not in result:
            result['pass_count'] = sum(1 for c in result['checks'] if c.get('pass', False))
            result['fail_count'] = sum(1 for c in result['checks'] if not c.get('pass', True))

        return result

    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
        # API/transport error - NOT an extraction failure
        # Mark distinctly so aggregator can exclude from repair batch
        return {
            'page': page_input['page'],
            'checks': [],
            'pass_count': 0,
            'fail_count': 0,
            '_api_error': True,
            '_error_type': type(e).__name__,
            '_error': str(e),
        }




async def run_page_verification(
    pdf_path: Path,
    blocks_by_page: dict[int, list[dict]],
    prompt_path: Path,
    screenshots_dir: Path,
    results_dir: Path,
) -> list[dict]:
    """Run page-level verification for all pages."""
    with open(prompt_path) as f:
        system_prompt = f.read()

    results = []
    total_pages = len(blocks_by_page)

    async with httpx.AsyncClient() as client:
        for i, (page_num, blocks) in enumerate(sorted(blocks_by_page.items())):
            print(f"  Page {page_num} ({i+1}/{total_pages}, {len(blocks)} blocks)...")

            # Render page with numbered bboxes
            screenshot_path = render_page_with_bboxes(
                pdf_path, page_num, blocks, screenshots_dir
            )

            # Prepare input
            page_input = prepare_page_input(page_num, blocks, screenshot_path)

            # Verify (sequential for Claude OAuth safety)
            result = await verify_page(client, page_input, system_prompt)
            results.append(result)

            # Save individual page result
            result_path = results_dir / f"page_{page_num}_result.json"
            with open(result_path, 'w') as f:
                json.dump(result, f, indent=2)

            # Summary
            if result.get('_validation_error'):
                print(f"    ⚠ Schema validation error: {result['_validation_errors'][:2]}")
            elif result.get('_api_error'):
                print(f"    ⚠ API error ({result['_error_type']}): {result['_error'][:50]}...")
            elif 'checks' in result:
                pass_count = result.get('pass_count', 0)
                fail_count = result.get('fail_count', 0)
                print(f"    ✓ {pass_count} passed, ✗ {fail_count} failed")

    return results


def main():
    parser = argparse.ArgumentParser(description='Run extraction calibration pipeline')
    parser.add_argument('--extraction', '-e', type=Path, required=True, help='Extraction JSON path')
    parser.add_argument('--pdf', '-p', type=Path, required=True, help='Source PDF path')
    parser.add_argument('--sample', '-n', type=int, default=50, help='Number of blocks to sample')
    parser.add_argument('--prompt', type=Path, default=Path('/tmp/verify_extraction_checklist.txt'))
    parser.add_argument('--output', '-o', type=Path, default=Path('/tmp/calibration'))
    parser.add_argument('--auto-repair', action='store_true', help='Auto-run /code-runner if failures found')
    parser.add_argument('--no-stratify', action='store_true', help='Disable stratified sampling')

    args = parser.parse_args()

    # Create output directories
    args.output.mkdir(parents=True, exist_ok=True)
    screenshots_dir = args.output / 'screenshots'
    screenshots_dir.mkdir(exist_ok=True)
    results_dir = args.output / 'results'
    results_dir.mkdir(exist_ok=True)

    # Step 1: Sample blocks
    print(f'Sampling {args.sample} blocks from {args.extraction}...')
    blocks = sample_blocks(args.extraction, args.sample, stratify=not args.no_stratify)
    print(f'  Sampled {len(blocks)} blocks')

    # Step 2: Group by page
    blocks_by_page = group_blocks_by_page(blocks)
    print(f'  Blocks span {len(blocks_by_page)} pages')

    # Step 3: Run page-level verification
    print(f'Running visual verifier (page-by-page)...')
    results = asyncio.run(run_page_verification(
        args.pdf,
        blocks_by_page,
        args.prompt,
        screenshots_dir,
        results_dir,
    ))

    # Save all results
    all_results_path = args.output / 'all_results.json'
    with open(all_results_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f'  Saved results to {all_results_path}')

    # Compute summary - exclude API/validation errors from extraction accuracy
    valid_results = [r for r in results if not r.get('_api_error') and not r.get('_validation_error')]
    api_errors = sum(1 for r in results if r.get('_api_error'))
    validation_errors = sum(1 for r in results if r.get('_validation_error'))

    total_pass = sum(r.get('pass_count', 0) for r in valid_results)
    total_fail = sum(r.get('fail_count', 0) for r in valid_results)
    total_checked = total_pass + total_fail
    accuracy = total_pass / total_checked * 100 if total_checked > 0 else 0

    print(f'\nSummary:')
    print(f'  Extraction: {total_pass}/{total_checked} passed ({accuracy:.1f}% accuracy)')
    if api_errors > 0:
        print(f'  API errors: {api_errors} pages (excluded from accuracy)')
    if validation_errors > 0:
        print(f'  Schema errors: {validation_errors} pages (excluded from accuracy)')

    # Step 4: Aggregate failures (only extraction failures, not API/validation errors)
    if total_fail > 0:
        print(f'\nAggregating {total_fail} extraction failures...')
        try:
            subprocess.run([
                'python', '-m', 'pdf_oxide.verification_aggregator',
                str(all_results_path),
                '--output', str(args.output / 'repair_batch.json'),
                '--task-spec', str(args.output / 'repair_task.json'),
                '--summary',
            ], cwd='/home/graham/workspace/experiments/pdf_oxide', check=True)
        except subprocess.CalledProcessError as e:
            print(f'  ✗ Aggregator failed: {e}')
            return

        # Step 5: Optionally run code-runner
        if args.auto_repair:
            task_spec_path = args.output / 'repair_task.json'
            if task_spec_path.exists():
                print(f'\nRunning /code-runner repair agent...')
                try:
                    subprocess.run([
                        '.pi/skills/code-runner/run.sh', 'run', str(task_spec_path),
                    ], cwd='/home/graham/workspace/experiments/pdf_oxide', check=True)
                except subprocess.CalledProcessError as e:
                    print(f'  ✗ Code-runner failed: {e}')
        else:
            print(f'\nTo run repair agent manually:')
            print(f'  .pi/skills/code-runner/run.sh run {args.output / "repair_task.json"}')
    else:
        print('\nNo extraction failures found - extraction looks correct!')


if __name__ == '__main__':
    main()
