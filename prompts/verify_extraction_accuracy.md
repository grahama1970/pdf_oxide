# Extraction Accuracy Verification Prompt

## Task

Verify that PDF extraction classifications are accurate by visually inspecting bbox regions.

## Input

You are given:
1. **PDF path**: `{pdf_path}`
2. **Extraction JSON**: `{extraction_json_path}` containing blocks with:
   - `id`: block identifier
   - `page`: 0-indexed page number
   - `bbox`: normalized [x0, y0, x1, y1] coordinates (0-1 range)
   - `blockType`: claimed classification (header, text, table, boilerplate)
   - `text`: extracted text content

## Process

For a sample of blocks (stratified by blockType and page):

### Step 1: Render bbox region
```python
import fitz
doc = fitz.open(pdf_path)
page = doc[block['page']]
width, height = page.rect.width, page.rect.height

# Denormalize bbox
bbox = block['bbox']
rect = fitz.Rect(
    bbox[0] * width,
    bbox[1] * height,
    bbox[2] * width,
    bbox[3] * height
)

# Render with padding
padding = 10
clip = rect + (-padding, -padding, padding, padding)
pix = page.get_pixmap(clip=clip, dpi=150)
pix.save(f"/tmp/verify_{block['id']}.png")
```

### Step 2: Examine each rendered region

For each screenshot, answer:

1. **What type of content is this?**
   - Header/title (large text, bold, section name)
   - Body text (paragraph, normal font)
   - Table (grid, rows/columns, data)
   - Page number ("Page X of Y")
   - Running header/boilerplate (repeated page chrome)
   - List item (bullet, numbered)
   - Control ID (e.g., "AC-1 POLICY AND PROCEDURES")
   - TOC entry (title ... page number)

2. **Does extracted text match what you see?**
   - Exact match
   - Partial match (truncated or extra content)
   - Mismatch (wrong text entirely)

3. **Is the bbox accurate?**
   - Correct (fully contains the content)
   - Too small (cuts off content)
   - Too large (includes adjacent content)
   - Wrong location

### Step 3: Report findings

For each block examined, output:

```json
{
  "block_id": "block_123",
  "page": 5,
  "claimed_type": "header",
  "actual_type": "body_text",
  "classification_correct": false,
  "text_match": "exact",
  "bbox_accuracy": "correct",
  "notes": "This is a paragraph that happens to start with a control ID, not a section header"
}
```

## Sampling Strategy

Don't examine all blocks (8,911 is too many). Sample:

1. **By type**: 5 blocks per blockType (header, text, table, boilerplate)
2. **By page region**: 
   - First 10 pages (front matter, TOC)
   - Middle pages (body content)
   - Last 10 pages (appendices, glossary)
3. **Edge cases**: 
   - Shortest text blocks (<20 chars)
   - Longest text blocks (>500 chars)
   - Blocks with control IDs in text

Total sample: ~50-100 blocks

## Definition of Done

Output a verification report:

```json
{
  "pdf": "NIST_SP_800-53r5.pdf",
  "total_blocks": 8911,
  "blocks_verified": 75,
  "accuracy": {
    "classification_correct": 71,
    "classification_incorrect": 4,
    "accuracy_rate": 0.947
  },
  "errors": [
    {
      "block_id": "block_4246",
      "claimed": "header",
      "actual": "body_text",
      "reason": "Paragraph starting with PL-8, not a section header"
    }
  ],
  "bbox_issues": [],
  "text_match_issues": []
}
```

## Acceptance Criteria

- Classification accuracy ≥ 95%
- No bbox issues (content fully contained)
- Text extraction matches visual content
