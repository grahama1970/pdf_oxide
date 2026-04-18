"""Aggregator for visual verification results.

Collects verification JSONs from headless VLM verifier, groups failures by pattern,
and generates /code-runner task specs for the repair agent.

Usage:
    python -m pdf_oxide.verification_aggregator /tmp/verifications/*.json --output /tmp/repair_batch.json
    python -m pdf_oxide.verification_aggregator /tmp/verifications/*.json --task-spec /tmp/repair_task.json
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import TypedDict


class CheckResult(TypedDict):
    block_id: str
    bbox_contains_content: bool
    bbox_no_adjacent: bool
    bbox_tight: bool
    text_matches: bool
    text_complete: bool
    text_no_extra: bool
    type_correct: bool
    pass_: bool  # 'pass' in JSON
    issues: list[str]


class PageResult(TypedDict):
    page: int
    checks: list[CheckResult]
    pass_count: int
    fail_count: int


class FailureGroup(TypedDict):
    issue_type: str
    count: int
    examples: list[dict]
    affected_blocks: list[str]


def load_verifications(paths: list[Path]) -> list[PageResult]:
    """Load verification JSONs from files."""
    results = []
    for path in paths:
        with open(path) as f:
            data = json.load(f)
            if isinstance(data, list):
                results.extend(data)
            else:
                results.append(data)
    return results


def flatten_to_checks(results: list[PageResult]) -> list[dict]:
    """Flatten page results to individual check results.

    Excludes pages with API or validation errors - those are infra issues,
    not extraction failures, and should not trigger repair agent.
    """
    checks = []
    for r in results:
        # Skip pages with infra errors
        if r.get('_api_error') or r.get('_validation_error'):
            continue

        page = r.get('page', 0)
        for check in r.get('checks', []):
            # Create a copy to avoid mutating the original
            check_copy = dict(check)
            check_copy['page'] = page
            checks.append(check_copy)
    return checks


def filter_failures(checks: list[dict]) -> list[dict]:
    """Filter to only failed checks."""
    return [c for c in checks if not c.get('pass', True)]


def categorize_issue(check: dict) -> str:
    """Categorize a failed check by its primary issue type."""
    # Priority order: classification > bbox > text
    if not check.get('type_correct', True):
        return 'classification_error'
    if not check.get('bbox_contains_content', True):
        return 'bbox_clips_content'
    if not check.get('bbox_no_adjacent', True):
        return 'bbox_includes_adjacent'
    if not check.get('bbox_tight', True):
        return 'bbox_not_tight'
    if not check.get('text_matches', True):
        return 'text_mismatch'
    if not check.get('text_complete', True):
        return 'text_truncated'
    if not check.get('text_no_extra', True):
        return 'text_extra_content'
    # Table issues
    if not check.get('table_rows_complete', True):
        return 'table_missing_rows'
    if not check.get('table_columns_complete', True):
        return 'table_missing_columns'
    if not check.get('table_structure_correct', True):
        return 'table_structure_error'
    return 'unknown'


def group_by_issue_type(failures: list[dict]) -> dict[str, FailureGroup]:
    """Group failures by issue type."""
    groups: dict[str, list[dict]] = defaultdict(list)

    for f in failures:
        issue_type = categorize_issue(f)
        groups[issue_type].append(f)

    result = {}
    for issue_type, items in groups.items():
        result[issue_type] = {
            'issue_type': issue_type,
            'count': len(items),
            'examples': items[:5],  # Cap at 5 examples per type
            'affected_blocks': [i.get('block_id', 'unknown') for i in items],
        }

    return result


def generate_repair_batch(groups: dict[str, FailureGroup]) -> dict:
    """Generate repair batch JSON for the repair agent."""
    return {
        'total_failures': sum(g['count'] for g in groups.values()),
        'issue_groups': groups,
        'priority_order': sorted(
            groups.keys(),
            key=lambda k: groups[k]['count'],
            reverse=True
        ),
    }


def generate_task_spec(
    repair_batch: dict,
    cwd: str = '/home/graham/workspace/experiments/pdf_oxide',
    output_dir: str = '/tmp/extraction-repair',
) -> dict:
    """Generate /code-runner task spec from repair batch."""

    # Build prompt from failure groups
    prompt_lines = [
        'Fix PDF extraction errors identified by the visual verifier.',
        '',
        'The verifier found these issue types:',
        '',
    ]

    for issue_type in repair_batch['priority_order']:
        group = repair_batch['issue_groups'][issue_type]
        prompt_lines.append(f'## Issue: {issue_type} ({group["count"]} failures)')
        prompt_lines.append('')

        prompt_lines.append('Example failures:')
        for ex in group['examples'][:3]:
            prompt_lines.append(f'  - block_id: {ex.get("block_id")}, page: {ex.get("page")}')
            if ex.get('issues'):
                for issue in ex['issues'][:2]:
                    prompt_lines.append(f'    issue: {issue}')
        prompt_lines.append('')

    # Map issue types to files
    issue_to_file = {
        'classification_error': 'python/pdf_oxide/extract_for_pdflab.py (classify_block function)',
        'bbox_clips_content': 'src/extraction/mod.rs (bbox calculation)',
        'bbox_includes_adjacent': 'src/extraction/mod.rs (block merging logic)',
        'bbox_not_tight': 'src/extraction/mod.rs (bbox trimming)',
        'text_mismatch': 'src/extraction/mod.rs (text extraction)',
        'text_truncated': 'src/extraction/mod.rs (text buffer size)',
        'text_extra_content': 'src/extraction/mod.rs (block boundaries)',
    }

    prompt_lines.append('Files to investigate based on issue types:')
    for issue_type in repair_batch['priority_order']:
        file_hint = issue_to_file.get(issue_type, 'unknown')
        prompt_lines.append(f'  - {issue_type}: {file_hint}')
    prompt_lines.append('')

    prompt_lines.extend([
        'Do NOT modify the visual verifier prompt or aggregator.',
        'The verifier is ground truth. Fix the extraction code to match.',
    ])

    return {
        'task_id': 'extraction-repair',
        'title': f'Fix {repair_batch["total_failures"]} extraction errors',
        'prompt': '\n'.join(prompt_lines),
        'backend': 'claude',
        'cwd': cwd,
        'output_dir': output_dir,
        'allowlist': [
            'python/pdf_oxide/extract_for_pdflab.py',
            'python/pdf_oxide/extraction_scanner.py',
            'src/extraction/',
            'tests/test_extraction_classification.py',
        ],
        'definition_of_done': {
            'command': 'cd /home/graham/workspace/experiments/pdf_oxide && uv run pytest tests/test_extraction_classification.py -v',
            'assertion': 'passed',
        },
        'max_rounds': 5,
        'read_context': [
            '/tmp/verify_extraction_checklist.txt',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description='Aggregate visual verification results')
    parser.add_argument('inputs', nargs='+', type=Path, help='Verification JSON files or glob pattern')
    parser.add_argument('--output', '-o', type=Path, help='Output repair batch JSON')
    parser.add_argument('--task-spec', type=Path, help='Output /code-runner task spec')
    parser.add_argument('--summary', action='store_true', help='Print summary to stdout')

    args = parser.parse_args()

    # Load page results and flatten to checks
    results = load_verifications(args.inputs)

    # Count infra errors (excluded from extraction accuracy)
    api_errors = sum(1 for r in results if r.get('_api_error'))
    validation_errors = sum(1 for r in results if r.get('_validation_error'))

    checks = flatten_to_checks(results)  # Excludes API/validation error pages
    failures = filter_failures(checks)

    total_checks = len(checks)
    total_pass = total_checks - len(failures)

    if args.summary or not (args.output or args.task_spec):
        print(f'Total checks: {total_checks}')
        print(f'Passed: {total_pass}')
        print(f'Failed: {len(failures)}')
        print(f'Accuracy: {total_pass / total_checks * 100:.1f}%' if total_checks > 0 else 'N/A')
        if api_errors > 0:
            print(f'API errors (excluded): {api_errors} pages')
        if validation_errors > 0:
            print(f'Schema errors (excluded): {validation_errors} pages')

    if not failures:
        print('No failures to repair.')
        return

    # Group and generate
    groups = group_by_issue_type(failures)
    repair_batch = generate_repair_batch(groups)

    if args.summary or not (args.output or args.task_spec):
        print(f'\nIssue types:')
        for issue_type in repair_batch['priority_order']:
            g = groups[issue_type]
            print(f'  {issue_type}: {g["count"]} failures')

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(repair_batch, f, indent=2)
        print(f'\nWrote repair batch to: {args.output}')

    if args.task_spec:
        task_spec = generate_task_spec(repair_batch)
        with open(args.task_spec, 'w') as f:
            json.dump(task_spec, f, indent=2)
        print(f'Wrote task spec to: {args.task_spec}')
        print(f'\nRun with: .pi/skills/code-runner/run.sh run {args.task_spec}')


if __name__ == '__main__':
    main()
