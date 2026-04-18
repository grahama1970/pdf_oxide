#!/usr/bin/env python3
"""Run table merge verification via VLM.

Takes candidate pairs from render_table_merge_candidates.py and calls
scillm to decide whether each pair should be merged.

Usage:
    python scripts/run_table_merge_verification.py \
        --inputs /tmp/merge_candidates/merge_inputs.json \
        --output /tmp/merge_candidates/merge_results.json
"""

import argparse
import asyncio
import base64
import json
import shutil
from datetime import datetime
from pathlib import Path

import httpx
import os


SCILLM_URL = "http://localhost:4001/v1/chat/completions"

# Schema validation
REQUIRED_FIELDS = {
    'page_a', 'page_b', 'table_a_block_id', 'table_b_block_id',
    'should_merge', 'confidence', 'signals', 'merge_action', 'reason'
}
REQUIRED_SIGNALS = {
    'same_column_structure', 'header_repeated_or_continued', 'page_break_continuity',
    'same_schema_and_style', 'no_new_title_on_right', 'new_caption_or_title_on_right',
    'different_column_structure', 'different_schema', 'intervening_non_table_context',
    'left_table_looks_complete'
}
VALID_CONFIDENCE = {'high', 'medium', 'low'}
VALID_MERGE_ACTIONS = {'merge_drop_repeated_header', 'merge_keep_right_header', 'do_not_merge'}


def validate_response(result: dict) -> list[str]:
    """Validate response against schema. Returns list of errors."""
    errors = []

    # Check required top-level fields
    missing = REQUIRED_FIELDS - set(result.keys())
    if missing:
        errors.append(f"Missing fields: {missing}")

    # Check types
    if 'should_merge' in result and not isinstance(result['should_merge'], bool):
        errors.append(f"should_merge must be bool, got {type(result['should_merge'])}")

    if 'confidence' in result and result['confidence'] not in VALID_CONFIDENCE:
        errors.append(f"Invalid confidence: {result['confidence']}")

    if 'merge_action' in result and result['merge_action'] not in VALID_MERGE_ACTIONS:
        errors.append(f"Invalid merge_action: {result['merge_action']}")

    # Check signals
    if 'signals' in result:
        signals = result['signals']
        missing_signals = REQUIRED_SIGNALS - set(signals.keys())
        if missing_signals:
            errors.append(f"Missing signals: {missing_signals}")
        for sig, val in signals.items():
            if not isinstance(val, bool):
                errors.append(f"Signal {sig} must be bool, got {type(val)}")

    # Check consistency: should_merge=false requires do_not_merge
    if result.get('should_merge') is False and result.get('merge_action') != 'do_not_merge':
        errors.append("should_merge=false but merge_action is not do_not_merge")

    # Check reason length
    if 'reason' in result and len(result['reason'].split()) > 20:
        errors.append(f"Reason exceeds 20 words: {len(result['reason'].split())} words")

    return errors
SCILLM_HEADERS = {
    "Authorization": f"Bearer {os.environ.get('SCILLM_API_KEY', 'sk-dev-proxy-123')}",
    "X-Caller-Skill": "table-merge-verification",
}


async def verify_merge_candidate(
    client: httpx.AsyncClient,
    candidate: dict,
    system_prompt: str,
    audit_dir: Path | None = None,
) -> dict:
    """Verify a single merge candidate via VLM call."""
    # Read and base64-encode the comparison image
    image_path = Path(candidate['image_path'])
    with open(image_path, 'rb') as f:
        img_b64 = base64.b64encode(f.read()).decode()

    # Build user message (JSON only, no image path)
    user_data = {
        'page_a': candidate['page_a'],
        'page_b': candidate['page_b'],
        'table_a_block_id': candidate['table_a_block_id'],
        'table_b_block_id': candidate['table_b_block_id'],
        'table_a_extracted_text': candidate['table_a_extracted_text'],
        'table_b_extracted_text': candidate['table_b_extracted_text'],
        'table_a_bbox_normalized': candidate['table_a_bbox_normalized'],
        'table_b_bbox_normalized': candidate['table_b_bbox_normalized'],
    }
    user_message = json.dumps(user_data, indent=2)

    raw_content = None
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
                "temperature": 0.1,  # Low temperature for consistency
            },
            timeout=120.0,
        )
        resp.raise_for_status()

        raw_content = resp.json()["choices"][0]["message"]["content"]
        result = json.loads(raw_content)

        # Validate response
        validation_errors = validate_response(result)
        if validation_errors:
            result['_validation_errors'] = validation_errors

        # Audit logging
        if audit_dir:
            audit_entry = {
                'timestamp': datetime.utcnow().isoformat(),
                'page_a': candidate['page_a'],
                'page_b': candidate['page_b'],
                'image_path': str(image_path),
                'raw_response': raw_content,
                'parsed_result': result,
                'validation_errors': validation_errors,
            }
            audit_file = audit_dir / f"audit_p{candidate['page_a']}_p{candidate['page_b']}.json"
            with open(audit_file, 'w') as f:
                json.dump(audit_entry, f, indent=2)

            # Copy image to audit dir for easy review
            shutil.copy(image_path, audit_dir / image_path.name)

        return result

    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
        error_result = {
            'page_a': candidate['page_a'],
            'page_b': candidate['page_b'],
            'table_a_block_id': candidate['table_a_block_id'],
            'table_b_block_id': candidate['table_b_block_id'],
            'should_merge': False,
            'confidence': 'low',
            'signals': {sig: False for sig in REQUIRED_SIGNALS},
            'merge_action': 'do_not_merge',
            'reason': f'VLM call failed: {type(e).__name__}',
            '_error': str(e),
            '_raw_response': raw_content,
        }

        # Audit logging for errors too
        if audit_dir:
            audit_entry = {
                'timestamp': datetime.utcnow().isoformat(),
                'page_a': candidate['page_a'],
                'page_b': candidate['page_b'],
                'image_path': str(image_path),
                'raw_response': raw_content,
                'error': str(e),
                'error_type': type(e).__name__,
            }
            audit_file = audit_dir / f"audit_p{candidate['page_a']}_p{candidate['page_b']}_ERROR.json"
            with open(audit_file, 'w') as f:
                json.dump(audit_entry, f, indent=2)

        return error_result


async def run_verification(
    inputs: list[dict],
    prompt_path: Path,
    output_path: Path,
    audit_dir: Path | None = None,
) -> list[dict]:
    """Run verification on all candidates sequentially (Claude OAuth safety)."""
    with open(prompt_path) as f:
        system_prompt = f.read()

    # Strip rationale header if present
    lines = system_prompt.split('\n')
    clean_lines = []
    in_rationale = False
    for line in lines:
        if line.startswith('# RATIONALE'):
            in_rationale = True
            continue
        if in_rationale and not line.startswith('#'):
            in_rationale = False
        if not in_rationale:
            clean_lines.append(line)
    system_prompt = '\n'.join(clean_lines)

    results = []
    total = len(inputs)
    validation_error_count = 0

    async with httpx.AsyncClient() as client:
        for i, candidate in enumerate(inputs):
            print(f"  [{i+1}/{total}] Pages {candidate['page_a']}-{candidate['page_b']}...")

            result = await verify_merge_candidate(client, candidate, system_prompt, audit_dir)
            results.append(result)

            # Summary
            action = result.get('merge_action', 'unknown')
            confidence = result.get('confidence', '?')
            if '_validation_errors' in result:
                validation_error_count += 1
                print(f"    -> {action} ({confidence}) [VALIDATION ERRORS]")
            elif '_error' in result:
                print(f"    -> {action} ({confidence}) [API ERROR]")
            else:
                print(f"    -> {action} ({confidence})")

            # Save incremental results
            with open(output_path, 'w') as f:
                json.dump(results, f, indent=2)

    if validation_error_count > 0:
        print(f"\n  Warning: {validation_error_count} responses had validation errors")

    return results


def main():
    parser = argparse.ArgumentParser(description='Run table merge verification')
    parser.add_argument('--inputs', '-i', type=Path, required=True, help='merge_inputs.json from render script')
    parser.add_argument('--prompt', type=Path, default=Path('/tmp/verify_table_merge_prompt.txt'))
    parser.add_argument('--output', '-o', type=Path, default=None)
    parser.add_argument('--audit', '-a', type=Path, default=None, help='Audit log directory (saves images + raw responses)')

    args = parser.parse_args()

    if args.output is None:
        args.output = args.inputs.parent / 'merge_results.json'

    # Create audit directory if specified
    audit_dir = None
    if args.audit:
        audit_dir = args.audit
        audit_dir.mkdir(parents=True, exist_ok=True)
        print(f'Audit logging enabled: {audit_dir}')

    # Load inputs
    with open(args.inputs) as f:
        inputs = json.load(f)

    print(f'Verifying {len(inputs)} merge candidates...')
    results = asyncio.run(run_verification(inputs, args.prompt, args.output, audit_dir))

    # Summary
    merge_count = sum(1 for r in results if r.get('should_merge', False))
    no_merge_count = len(results) - merge_count
    error_count = sum(1 for r in results if '_error' in r)
    validation_error_count = sum(1 for r in results if '_validation_errors' in r)

    print(f'\nSummary:')
    print(f'  Merge: {merge_count}')
    print(f'  Do not merge: {no_merge_count}')
    if error_count > 0:
        print(f'  API errors: {error_count}')
    if validation_error_count > 0:
        print(f'  Validation errors: {validation_error_count}')
    print(f'  Results saved to: {args.output}')
    if audit_dir:
        print(f'  Audit logs saved to: {audit_dir}')


if __name__ == '__main__':
    main()
