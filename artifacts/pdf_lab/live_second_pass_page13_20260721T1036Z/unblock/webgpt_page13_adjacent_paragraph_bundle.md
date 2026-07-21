# PDF Lab Page 13 Adjacent Paragraph Defect

## Current gate

Make one live model-backed second-pass page review reach a passing terminal state for page 13 without modifying SciLLM internals or Tau orchestration.

## Failure receipt

- Harness output: `artifacts/pdf_lab/live_second_pass_page13_20260721T1036Z/harness_final_gate.json`
- Terminal status: `failed_closed`
- Page terminal ledger: `artifacts/pdf_lab/live_second_pass_page13_20260721T1036Z/page_cases/page_case_0001_p0013/terminal_ledger.json`
- Terminal reason: `patch_delegate_dry_run`
- Validator receipt: `artifacts/pdf_lab/live_second_pass_page13_20260721T1036Z/page_cases/page_case_0001_p0013/review_validation.json`
- Model response: `artifacts/pdf_lab/live_second_pass_page13_20260721T1036Z/page_cases/page_case_0001_p0013/review_response.json`

## Model finding

The reviewer found a real extraction defect:

- `cand:p0013:0005:text`: first line of paragraph
- `cand:p0013:0006:section_heading`: middle line misclassified as heading
- `cand:p0013:0007:text`: final line of paragraph

The three lines should be one paragraph:

```text
Throughout this publication, examples are used to illustrate, clarify, or explain certain items in chapter sections, controls, and control enhancements. These examples are illustrative in nature and are not intended to limit or constrain the application of controls or control enhancements by organizations.
```

## Current extraction evidence

All three blocks are page 13, x-aligned around `0.1833`, non-bold, 10.02pt, and vertically adjacent:

- `actual:p13:block:5`, `type=paragraph_block`, `source_type=Body`, bbox `[0.18333332834680097, 0.1539892331518308, 0.8148121553308824, 0.17091695226804174]`
- `actual:p13:block:6`, `type=section_heading`, `source_type=Title`, bbox `[0.18331694758795444, 0.16944939199120107, 0.8150315128899868, 0.186377111107412]`
- `actual:p13:block:7`, `type=paragraph_block`, `source_type=Body`, bbox `[0.1833169226552926, 0.18483364220821497, 0.8148931366166258, 0.21729742878615255]`

## Research context

`$brave-search` receipt: `artifacts/pdf_lab/live_second_pass_page13_20260721T1036Z/unblock/brave_adjacent_paragraph_line_merge.json`

The relevant PDF extraction principle is that line fragments with matching paragraph geometry and line spacing should be grouped into a logical paragraph, while headings need stronger evidence than a single title-like source label when font/weight/position match body text continuation.

## Proposed narrow fix

Use existing `nist_sp_800_53r5_promotion_ledger.json` mechanisms only:

1. Add a focused regression extracting NIST page 13 with the release ledger.
2. Add a guarded ledger rule for this exact frontmatter paragraph:
   - demote the false `section_heading` middle line to `paragraph_block`;
   - merge the three adjacent lines into one paragraph block with normalized spaces.
3. Guard by page, exact/prefix text, x/y band, font size, non-bold body font, and adjacency so this does not affect real headings.
4. Rerun page 13 live and require `review_validation.ok == true` and `harness_final_gate.ok == true`.

## Question

Is the proposed guarded ledger merge/demotion the correct current-gate fix, or is there a narrower local fix that better preserves the existing extraction contract?

Return one of:

```text
PASS_CURRENT_GATE
BLOCKED_CURRENT_GATE: <one concrete blocker>
REJECTED_SCOPE_EXPANSION
```
