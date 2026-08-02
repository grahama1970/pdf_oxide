"""Regression test for page 45 AC-1 policy/procedures list fragmentation.

Baseline: 8 separate list/list-like blocks for the AC-1 control list region.
Expected: 1 merged semantic list region; Related Controls stays separate.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_applier():
    """Load the applier module directly to avoid pdf_oxide/__init__ Rust import."""
    applier_path = Path(__file__).resolve().parents[1] / "python" / "pdf_oxide" / "presets" / "applier.py"
    spec = importlib.util.spec_from_file_location("applier", applier_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_applier = _load_applier()
ApplierConfig = _applier.ApplierConfig
apply_ledger = _applier.apply_ledger


def _load_ledger() -> dict:
    ledger_path = Path(__file__).resolve().parents[1] / "python" / "pdf_oxide" / "presets" / "document_families" / "nist_sp_800_53r5_promotion_ledger.json"
    return json.loads(ledger_path.read_text(encoding="utf-8"))


# Raw elements for page 45 AC-1 region BEFORE ledger application.
# Reconstructed from baseline_snapshot.json raw fields with type reset to unknown_region.
_P45_AC1_RAW_ELEMENTS = [
    {
        "id": "actual:p45:block:8",
        "page": 45,
        "type": "unknown_region",
        "source_type": "Body",
        "text": "AC-1 POLICY AND PROCEDURES",
        "font_name": "TT2",
        "font_size": 10.98,
        "is_bold": True,
        "bbox": [0.147, 0.166, 0.402, 0.185],
    },
    {
        "id": "actual:p45:block:9",
        "page": 45,
        "type": "unknown_region",
        "source_type": "Body",
        "text": "Control:",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.206, 0.191, 0.260, 0.208],
    },
    {
        "id": "actual:p45:block:10",
        "page": 45,
        "type": "unknown_region",
        "source_type": "List",
        "text": "a. Develop, document, and disseminate to [Assignment: organization-defined personnel or roles]: 1. [Selection (one or more): Organization-level; Mission/business process-level; System- level] access control policy that:",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.206, 0.214, 0.824, 0.285],
    },
    {
        "id": "actual:p45:block:11",
        "page": 45,
        "type": "unknown_region",
        "source_type": "List",
        "text": "(a) Addresses purpose, scope, roles, responsibilities, management commitment, coordination among organizational entities, and compliance; and",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.265, 0.291, 0.804, 0.323],
    },
    {
        "id": "actual:p45:block:12",
        "page": 45,
        "type": "unknown_region",
        "source_type": "List",
        "text": "(b) Is consistent with applicable laws, executive orders, directives, regulations, policies, standards, and guidelines; and",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.265, 0.329, 0.849, 0.361],
    },
    {
        "id": "actual:p45:block:13",
        "page": 45,
        "type": "unknown_region",
        "source_type": "Body",
        "text": "2. Procedures to facilitate the implementation of the access control policy and the associated access controls;",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.235, 0.367, 0.794, 0.400],
    },
    {
        "id": "actual:p45:block:14",
        "page": 45,
        "type": "unknown_region",
        "source_type": "List",
        "text": "b. Designate an [Assignment: organization-defined official] to manage the development, documentation, and dissemination of the access control policy and procedures; and",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.206, 0.406, 0.804, 0.438],
    },
    {
        "id": "actual:p45:block:15",
        "page": 45,
        "type": "unknown_region",
        "source_type": "Body",
        "text": "c. Review and update the current access control:",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.206, 0.444, 0.543, 0.461],
    },
    {
        "id": "actual:p45:block:16",
        "page": 45,
        "type": "unknown_region",
        "source_type": "Body",
        "text": "1. Policy [Assignment: organization-defined frequency] and following [Assignment: organization-defined events]; and",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.235, 0.467, 0.795, 0.500],
    },
    {
        "id": "actual:p45:block:17",
        "page": 45,
        "type": "unknown_region",
        "source_type": "Body",
        "text": "2. Procedures [Assignment: organization-defined frequency] and following [Assignment: organization-defined events].",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.235, 0.506, 0.831, 0.538],
    },
    {
        "id": "actual:p45:block:18",
        "page": 45,
        "type": "unknown_region",
        "source_type": "Body",
        "text": "Discussion: Access control policy and procedures address the controls in the AC family that are implemented within systems and organizations. The risk management strategy is an important factor in establishing such policies aPnd procedures. olicies and procedures contribute to security and privacy assurance. Therefore, it is important that security and privacy programs collaborate on the development of access control policy and procedures. Security and privacy program policies and procedures at the organization level are preferable, in general, and may obviate the need for mission- or system-specific policies and procedures. The policy can be included as part of the general security and privacy policy or be represented by multiple policies reflecting the complex nature of organizations. Procedures can be established for security and privacy programs, for mission or business processes, and for systems, if needed. Procedures describe how the policies or controls are implemented and can be directed at the individual or role that is the object of the procedure. Procedures can be documented in system security and privacy plans or in one or more separate documents. Events that may precipitate an update to access control policy and procedures include assessment or audit findings, security incidents or breaches, or changes in laws, executive orders, directives, regulations, policies, standards, and guidelines. Simply restating controls does not constitute an organizational policy or procedure.",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.206, 0.544, 0.856, 0.788],
    },
    {
        "id": "actual:p45:block:19",
        "page": 45,
        "type": "unknown_region",
        "source_type": "Body",
        "text": "Related Controls:  IA-1, PM-9, PM-24, PS-8, SI-12.",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.206, 0.798, 0.532, 0.815],
    },
    {
        "id": "actual:p45:block:20",
        "page": 45,
        "type": "unknown_region",
        "source_type": "Body",
        "text": "Control Enhancements:  None.",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.206, 0.821, 0.409, 0.838],
    },
    {
        "id": "actual:p45:block:21",
        "page": 45,
        "type": "unknown_region",
        "source_type": "Body",
        "text": "References:  [OMB A-130], [SP 800-12], [SP 800-30], [SP 800-39], [SP 800-100], [IR 7874].",
        "font_name": "TT0",
        "font_size": 10.02,
        "is_bold": False,
        "bbox": [0.206, 0.844, 0.794, 0.861],
    },
]


def test_p45_ac1_list_blocks_merged_to_one():
    """After ledger application, page 45 AC-1 list items must form one merged block."""
    ledger = _load_ledger()
    cfg = ApplierConfig(mode="release")
    result = apply_ledger([dict(e) for e in _P45_AC1_RAW_ELEMENTS], ledger, cfg)

    p45_blocks = [b for b in result if b.get("page") == 45]

    list_blocks = [
        b for b in p45_blocks
        if b.get("type") == "list"
    ]

    related_controls = [
        b for b in p45_blocks
        if "Related Controls" in (b.get("text") or "")
    ]

    # The AC-1 policy/procedures region should be ONE semantic list block.
    assert len(list_blocks) == 1, (
        f"Expected exactly 1 merged list block for AC-1 policy/procedures, "
        f"got {len(list_blocks)}: {[b['id'] for b in list_blocks]}"
    )

    # Related Controls must remain a separate non-list block.
    assert len(related_controls) == 1, (
        f"Expected exactly 1 Related Controls block, got {len(related_controls)}"
    )
    assert related_controls[0].get("type") != "list", (
        f"Related Controls must not be typed as list, got {related_controls[0].get('type')}"
    )

    # The merged list text should contain the key markers.
    merged_text = list_blocks[0].get("text", "")
    assert "a. Develop, document, and disseminate" in merged_text
    assert "(a) Addresses purpose, scope" in merged_text
    assert "(b) Is consistent with applicable laws" in merged_text
    assert "2. Procedures to facilitate" in merged_text
    assert "b. Designate an" in merged_text
    assert "c. Review and update" in merged_text
    assert "1. Policy [Assignment:" in merged_text
    assert "2. Procedures [Assignment:" in merged_text

    # The merged list should NOT swallow Discussion or Related Controls.
    assert "Discussion:" not in merged_text
    assert "Related Controls:" not in merged_text


if __name__ == "__main__":
    test_p45_ac1_list_blocks_merged_to_one()
    print("PASS")
