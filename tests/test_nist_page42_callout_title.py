from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


NIST_PDF = Path("/mnt/storage12tb/extractor_corpus/source/standards/NIST_SP_800-53r5.pdf")
LEDGER = Path(__file__).resolve().parent.parent / "python" / "pdf_oxide" / "presets" / "document_families" / "nist_sp_800_53r5_promotion_ledger.json"


def _extract_page_42_with_ledger():
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts" / "pdf_lab"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import snapshot_current_extraction as snapshot

    return snapshot._extract_page(NIST_PDF, 41, LEDGER, "release")


def _load_candidate_manifest_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "pdf_lab" / "build_pdf_element_candidate_manifest.py"
    spec = importlib.util.spec_from_file_location("build_pdf_element_candidate_manifest_page42_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_page42_appendix_c_reference_prose_is_not_appendix_preset():
    manifest = _load_candidate_manifest_module()
    block = {
        "type": "paragraph_block",
        "source_type": "Body",
        "bbox": [0.147, 0.09, 0.852, 0.193],
        "text": (
            "Organizations can select assurance-related controls to define system "
            "development activities. Assurance-related controls are identified "
            "in the control summary tables in Appendix C."
        ),
    }

    assert manifest.infer_preset_type(block, 42, 492) == "text"


def test_page42_evidence_of_control_implementation_is_section_heading():
    page = _extract_page_42_with_ledger()
    title = next(
        block
        for block in page["blocks"]
        if block.get("text") == "EVIDENCE OF CONTROL IMPLEMENTATION"
    )

    assert title["source_type"] == "Body"
    assert title["type"] == "section_heading"
    assert title["semantic_role"] == "nist_callout_heading"
