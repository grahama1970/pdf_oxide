You are one participant in a Tau-managed roundtable.
Handler: webgpt

Request:
# WebGPT Review Bundle: pdf_oxide page28 footer source_type failed-closed gate

## Current gate
Determine the next deterministic local repair step for `pdf_oxide` after the live PDF Lab second-pass gate failed closed on GS001/NIST page 28.

Return exactly one of:

- `PASS_CURRENT_GATE` only if the existing page28 artifacts already justify treating the gate as passed.
- `BLOCKED_CURRENT_GATE: <one blocker>` if a human decision or missing external authority is required.
- `REPAIR_CURRENT_GATE: <bounded repair>` if the project agent should apply a narrow code/preset/test repair next.

## Research context
- (PDF) Header and Footer Extraction by Page-Association: https://www.researchgate.net/publication/221253782_Header_and_Footer_Extraction_by_Page-Association
  eliminate accidental good text matches between a header/footer and a normal body text line. The final similarity is defined as the product of the two components: ... The proposed method has been tested on 9 documents with different styles, including 7 periodicals and 2 · books. All the 1156 pages are scanned and recognized using a commercial OCR engine. The header/footer · extraction system then runs on the generated text and bounding box information.
- Extract Text from a PDF — pypdf 6.14.2 documentation: https://pypdf.readthedocs.io/en/stable/user/extract-text.html
  The following example reads the text of page four of this PDF document, but ignores the header (y &gt; 720) and footer (y &lt; 50). In this file we also need to include new line characters (y == 0). from pypdf import PdfReader reader = PdfReader(&quot;GeoBase_NHNC1_Data_Model_UML_EN.pdf&quot;) page = reader.pages[3] parts = [] def visitor_body(text, cm, tm, font_dict, font_size): y = tm[5] if 50 &lt; y &lt; 720 or y == 0: parts.append(text) page.extract_text(visitor_text=visitor_body) text_body = &quot;&quot;.join(parts) print(text_body)
- Extract Text from a PDF — pypdf 3.16.2 documentation: https://pypdf.readthedocs.io/en/3.16.2/user/extract-text.html
  The following example reads the text of page 4 of this PDF document, but ignores header (y &lt; 720) and footer (y &gt; 50). from pypdf import PdfReader reader = PdfReader(&quot;GeoBase_NHNC1_Data_Model_UML_EN.pdf&quot;) page = reader.pages[3] parts = [] def visitor_body(text, cm, tm, font_dict, font_size): y = tm[5] if y &gt; 50 and y &lt; 720: parts.append(text) page.extract_text(visitor_text=visitor_body) text_body = &quot;&quot;.join(parts) print(text_body)
- The RAG Playbook: Advanced Parsing for PDFs That Hate You - Tables, Footnotes, & Figures: https://lettersfromacoder.substack.com/p/the-rag-playbook-advanced-parsing
  Many PDFs have consistent headers/footers that you can spot by frequency. Detect by Font Size/Style: Often, the main body text is one font size, and footnotes are smaller. A clever approach is to use pdfplumber’s character analysis. Then you can separate text by those sizes. This isn’t foolproof (what if a quote uses a smaller font?), but in structured documents it holds surprisingly well. I’ve used this trick: parse page by page, classify text segments into “main text vs.
- r/computervision on Reddit: How to extract the different sections of a pdf document using image processing?: https://www.reddit.com/r/computervision/comments/dli63d/how_to_extract_the_different_sections_of_a_pdf/
  For step 3, if your documents are all the same header/body/footer layout, you can just use a few if statements to distinguish them based on shape. If they’re not all similar shape, you may have to use a classifier. If you’re interested in good results without a lot of labeled data, using location in the image as features will work better than pixels.

## Local evidence summary
- Harness final gate: `{"bundle_consistency_ok": true, "errors": ["readiness failed: page aggregate resolved"], "ok": false, "readiness_ok": false, "schema": "pdf_lab.second_pass.harness_final_gate.v1", "terminal_status": "failed_closed"}`
- Harness aggregate status_counts: `{"still_open": 1}`
- Candidate count: `18`
- Review validation: `{"candidate_count": 18, "errors": [], "expected_candidate_ids": ["cand:p0028:0000:side_chrome", "cand:p0028:0001:side_chrome", "cand:p0028:0002:side_chrome", "cand:p0028:0003:side_chrome", "cand:p0028:0004:section_heading", "cand:p0028:0005:section_heading", "cand:p0028:0006:text", "cand:p0028:0007:text", "cand:p0028:0008:text", "cand:p0028:0009:text", "cand:p0028:0010:list", "cand:p0028:0011:footnote", "cand:p0028:0012:footnote", "cand:p0028:0013:footnote", "cand:p0028:0014:footnote", "cand:p0028:0015:footnote", "cand:p0028:0016:footnote", "cand:p0028:0017:footnote"], "ok": true, "page_case": {"case_id": "page_case_0001_p0028", "page_number": 28}, "schema": "pdf_lab.second_pass.review_validation.v1", "seen_candidate_ids": ["cand:p0028:0000:side_chrome", "cand:p0028:0001:side_chrome", "cand:p0028:0002:side_chrome", "cand:p0028:0003:side_chrome", "cand:p0028:0004:section_heading", "cand:p0028:0005:section_heading", "cand:p0028:0006:text", "cand:p0028:0007:text", "cand:p0028:0008:text", "cand:p0028:0009:text", "cand:p0028:0010:list", "cand:p0028:0011:footnote", "cand:p0028:0012:footnote", "cand:p0028:0013:footnote", "cand:p0028:0014:footnote", "cand:p0028:0015:footnote", "cand:p0028:0016:footnote", "cand:p0028:0017:footnote"]}`
- Terminal ledger: `{"allowed_terminal_statuses": ["blocked_substrate", "human_needed", "patched_confirmed", "rejected_with_proof", "reviewed_clean", "still_open"], "case_id": "page_case_0001_p0028", "commit_sha": null, "evidence_artifacts": ["state.json", "sampled_candidate_manifest.json", "page_before.json", "page_before.png", "page_candidates.png", "selected_candidates.json", "candidate_presets.json", "review_request.json", "review_request_validation.json", "scillm_orchestrator_page_dag_spec.json", "scillm_orchestrator_page_dag_spec_validation.json", "scillm_orchestrator_page_submission.json", "scillm_orchestrator_page_submission_validation.json", "review_validation.json", "scillm_review_preflight.json", "scillm_review_receipt.json", "review_response.json", "scillm_page_orchestrator_run_request.json", "scillm_page_orchestrator_run_validation.json", "scillm_page_orchestrator_run_receipt.json", "patch_baseline.json", "patch_evidence_workspace.json", "patch_request.json", "patch_validation.json", "patch_attempts_ledger.json", "patch_attempt_01_validation.json", "patch_attempt_01_prompt_contract.json", "patch_attempt_01_prompt_review_payload.txt", "review.html", "terminal_ledger_validation.json"], "page_number": 28, "reason": "patch_delegate_dry_run", "schema": "pdf_lab.second_pass.page_terminal_ledger.v1", "terminal_status": "still_open"}`
- Page orchestrator transport run: `otr-9e0554595832`

## Defect findings from live VLM response
```json
[
  {
    "candidate_id": "cand:p0028:0002:side_chrome",
    "evidence": "The extracted JSON identifies this block as `type: header_footer_noise` with `semantic_role: page_chrome` and `source_type: Body`. The text content 'CHAPTER ONE PAGE 1' and bounding box are accurate. Visually, this element is a page footer.",
    "rationale": "The element is correctly classified as `side_chrome` (header/footer noise) with an accurate bounding box and text. However, its `source_type` is incorrectly identified as 'Body' instead of 'Footer', which is a semantic misclassification based on its position and role in the document.",
    "status": "defect",
    "suggested_fix_surface": "pdf_oxide_core"
  }
]
```

## Exact candidate JSON
```json
{
  "bbox": [
    0.14705882352941177,
    0.9414804632013495,
    0.8528853273080066,
    0.9549618345318418
  ],
  "block_id": "actual:p28:block:2",
  "block_index": 2,
  "candidate_id": "cand:p0028:0002:side_chrome",
  "confidence": 0.8,
  "detection_reason": [
    "block_type:header_footer_noise",
    "preset_type:side_chrome",
    "hardening_interest",
    "boundary_geometry"
  ],
  "features": {
    "bbox_area": 0.009516,
    "block_type": "header_footer_noise",
    "has_toc_entries": false,
    "semantic_role": "page_chrome",
    "source_type": "Body",
    "text_length": 18
  },
  "json_pointer": "/pages/27/blocks/2",
  "page_index": 27,
  "page_number": 28,
  "pdf_id": "NIST_SP_800-53r5:fc63bcd61715d018",
  "preset_type": "side_chrome",
  "text_excerpt": "CHAPTER ONE PAGE 1"
}
```

## Exact extracted block JSON
```json
{
  "bbox": [
    0.14705882352941177,
    0.9414804632013495,
    0.8528853273080066,
    0.9549618345318418
  ],
  "font_name": "TT0",
  "font_size": 8.271428108215332,
  "id": "actual:p28:block:2",
  "is_bold": false,
  "page": 28,
  "pdf_page_index": 27,
  "raw": {
    "bbox": [
      90.0,
      38.15999984741211,
      434.03399658203125,
      9.0
    ],
    "block_type": "Body",
    "confidence": 0.8999999761581421,
    "font_name": "TT0",
    "font_size": 8.271428108215332,
    "header_level": null,
    "is_bold": false,
    "text": "CHAPTER ONE  PAGE 1"
  },
  "semantic_role": "page_chrome",
  "source_type": "Body",
  "text": "CHAPTER ONE  PAGE 1",
  "type": "header_footer_noise"
}
```

## Local code boundary discovered
`rg` shows source type construction and page-chrome mapping in `scripts/pdf_lab/snapshot_current_extraction.py`:

- `source_type = str(block.get("block_type") or "unknown")` around line 366.
- page chrome candidate `actual:p28:block:2` already has `type: header_footer_noise` and `semantic_role: page_chrome`.
- It keeps `source_type: Body` because raw `block_type` was `Body` even though the normalized bbox y range is `0.941..0.955`, matching footer/page-bottom geometry.

## Constraints
- `pdf_oxide` owns extractor/preset tests and artifacts.
- Tau owns DAG/agentic harness work.
- Do not modify SciLLM internals from this repo.
- Criterion 6 live GitHub apply remains blocked without approval receipt; do not ask for live mutation.
- The next action must be a narrow deterministic repair or proof artifact, not broad architecture.
- Do not broaden into dashboard/report work.

## Exact question
Is page28 a real extractor/source-type defect that should be repaired now? If so, what is the smallest deterministic repair path, likely file boundary, and focused proof command/artifact? If the correct path is not to repair code yet, name the missing proof artifact exactly.

Return a concise position with these Markdown headings:
## Position
## Evidence
## Uncertainties
## Blockers

---

Completion contract for browser automation:

At the very end of your final answer, print exactly:

<<<WEBGPT_DONE:20260721T112302Z:02ec2484>>>

Do not print anything after that marker.
