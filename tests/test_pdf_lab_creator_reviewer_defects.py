import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = REPO / "scripts/pdf_lab/validate_creator_reviewer_defects.py"
FIXTURE = REPO / "tests/fixtures/pdf_lab/page456_creator_reviewer_defects.json"
PAGE27_FIXTURE = REPO / "tests/fixtures/pdf_lab/page27_creator_reviewer_defects.json"
PAGE30_FIXTURE = REPO / "tests/fixtures/pdf_lab/page30_creator_reviewer_defects.json"
PAGE30_TEXT_FIXTURE = REPO / "tests/fixtures/pdf_lab/page30_body_hyphen_word_join_defects.json"
PAGE19_TABLE_TEXT_FIXTURE = (
    REPO / "tests/fixtures/pdf_lab/page19_table_citation_hyphen_spacing_defects.json"
)
PAGE22_TABLE_TEXT_FIXTURE = (
    REPO / "tests/fixtures/pdf_lab/page22_table_citation_hyphen_spacing_defects.json"
)
PAGE186_TEXT_FIXTURE = REPO / "tests/fixtures/pdf_lab/page186_list_hyphen_wrap_spacing_defects.json"
PAGE235_TEXT_FIXTURE = REPO / "tests/fixtures/pdf_lab/page235_body_hyphen_wrap_spacing_defects.json"
PAGE157_TABLE_FIXTURE = REPO / "tests/fixtures/pdf_lab/page157_false_table_defects.json"
PAGE399_BBOX_FIXTURE = REPO / "tests/fixtures/pdf_lab/page399_field_split_child_bbox_defects.json"
PAGE403_TABLE_FIXTURE = (
    REPO / "tests/fixtures/pdf_lab/page403_reference_header_table_false_positive_defects.json"
)
EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-after-cellbbox-20260726T130204Z/extraction.pdf_oxide.json"
)
PAGE27_EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/creator_reviewer_page27_running_footer_repair_20260729T161228Z/extraction.pdf_oxide.json"
)
PAGE30_EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/creator_reviewer_page30_rotated_side_chrome_bbox_repair_20260729T171712Z/extraction.pdf_oxide.json"
)
PAGE30_TEXT_EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/creator_reviewer_page30_body_hyphen_word_join_20260729T1740Z/current_evidence/pages/page_0030/release_extraction_blocks.json"
)
PAGE19_TABLE_TEXT_EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/creator_reviewer_page19_table_citation_hyphen_spacing_20260729T1825Z/current_evidence/pages/page_0019/release_extraction_blocks.json"
)
PAGE22_TABLE_TEXT_EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/creator_reviewer_page22_table_citation_hyphen_spacing_20260729T1835Z/current_evidence/pages/page_0022/release_extraction_blocks.json"
)
PAGE186_TEXT_EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/creator_reviewer_page186_list_hyphen_wrap_spacing_20260729T2020Z/current_evidence/pages/page_0186/release_extraction_blocks.json"
)
PAGE235_TEXT_EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/creator_reviewer_page235_body_hyphen_wrap_spacing_20260729T2010Z/current_evidence/pages/page_0235/release_extraction_blocks.json"
)
PAGE157_TABLE_EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/creator_reviewer_page157_false_table_20260729T2030Z/current_evidence/pages/page_0157/release_extraction_blocks.json"
)
PAGE399_BBOX_EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/creator_reviewer_page399_field_split_child_bbox_20260729T2115Z/current_evidence/pages/page_0399/release_extraction_blocks.json"
)
PAGE403_TABLE_EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/creator_reviewer_page403_reference_header_false_table_20260729T1750Z/current_evidence/pages/page_0403/release_extraction_blocks.json"
)
SCHEMA_PATH = REPO / "schemas/pdf_lab/creator_reviewer_defects.schema.json"


_SPEC = importlib.util.spec_from_file_location("creator_reviewer_validator", VALIDATOR_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"could not load validator from {VALIDATOR_PATH}")
validator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(validator)


def test_creator_reviewer_defect_schema_names_required_contract():
    schema = json.loads(SCHEMA_PATH.read_text())

    assert (
        schema["properties"]["schema"]["const"] == "pdf_oxide.pdf_lab.creator_reviewer_defects.v1"
    )
    check_props = schema["properties"]["checks"]["items"]["properties"]
    assert "REGION_LABEL_MISMATCH" in check_props["defect_class"]["enum"]
    assert "REGION_BBOX_MISMATCH" in check_props["defect_class"]["enum"]
    assert "TEXT_CONTENT_MISMATCH" in check_props["defect_class"]["enum"]
    assert "TABLE_FALSE_POSITIVE" in check_props["defect_class"]["enum"]
    assert "TABLE_CELL_TOP_LEVEL_LEAK" in check_props["defect_class"]["enum"]
    assert check_props["expected_state"]["enum"] == ["absent_top_level", "present"]
    for required in [
        "region_bbox",
        "actual_label",
        "expected_label",
        "text",
        "owner",
        "proof_command",
    ]:
        assert required in schema["properties"]["checks"]["items"]["required"]


def test_page456_current_receipt_satisfies_creator_reviewer_defect_checks():
    result = validator.validate(FIXTURE, EXTRACTION)

    assert result["status"] == "PASS"
    assert result["summary"] == {"check_count": 3, "passed": 3, "failed": 0}
    assert {check["status"] for check in result["checks"]} == {"PASS"}
    assert all(check["leaking_candidate_count"] == 0 for check in result["checks"])


def test_page27_current_extraction_satisfies_running_footer_label_contract():
    result = validator.validate(PAGE27_FIXTURE, PAGE27_EXTRACTION)

    assert result["status"] == "PASS"
    assert result["summary"] == {"check_count": 1, "passed": 1, "failed": 0}
    check = result["checks"][0]
    assert check["id"] == "page27-printed-page-number-xxv-is-running-footer"
    assert check["candidate_count"] == 1
    assert check["matching_label_count"] == 1
    assert check["expected_label"] == "running_footer"
    assert check["candidates"][0]["type"] == "running_footer"
    assert check["candidates"][0]["text"] == "xxv"


def test_page30_current_extraction_satisfies_rotated_side_chrome_bbox_contract():
    result = validator.validate(PAGE30_FIXTURE, PAGE30_EXTRACTION)

    assert result["status"] == "PASS"
    assert result["summary"] == {"check_count": 1, "passed": 1, "failed": 0}
    check = result["checks"][0]
    assert check["id"] == "page30-rotated-side-chrome-bbox-margin-constrained"
    assert check["candidate_count"] == 1
    assert check["matching_bbox_count"] == 1
    assert check["candidates"][0]["type"] == "header_footer_noise"
    assert check["candidates"][0]["bbox"][2] <= 0.07


def test_page30_bundle_evidence_path_resolves_from_repo_root():
    result = validator.validate(PAGE30_FIXTURE)

    assert result["status"] == "PASS"
    assert result["extraction_json"] == str(PAGE30_EXTRACTION)


def test_page30_current_extraction_satisfies_body_hyphen_word_join_contract():
    result = validator.validate(PAGE30_TEXT_FIXTURE, PAGE30_TEXT_EXTRACTION)

    assert result["status"] == "PASS"
    assert result["summary"] == {"check_count": 1, "passed": 1, "failed": 0}
    check = result["checks"][0]
    assert check["id"] == "page30-body-organization-wide-risk-spacing"
    assert check["candidate_count"] == 1
    assert check["matching_text_count"] == 1
    assert check["candidates"][0]["contains_expected_text"] is True
    assert check["candidates"][0]["contains_forbidden_text"] is False


def test_page30_validator_fails_if_body_hyphen_word_join_is_corrupted(tmp_path):
    extraction = json.loads(PAGE30_TEXT_EXTRACTION.read_text())
    mutated = False
    for block in extraction["blocks"]:
        if block.get("id") == "actual:p30:block:4":
            block["text"] = str(block["text"]).replace(
                "organization-wide risk management process",
                "organization- widerisk management process",
            )
            mutated = True
            break
    assert mutated
    corrupted_path = tmp_path / "page30_corrupted_body_text.json"
    corrupted_path.write_text(json.dumps(extraction), encoding="utf-8")

    result = validator.validate(PAGE30_TEXT_FIXTURE, corrupted_path)

    assert result["status"] == "FAIL"
    failed = [check for check in result["checks"] if check["status"] == "FAIL"]
    assert [check["id"] for check in failed] == ["page30-body-organization-wide-risk-spacing"]
    assert failed[0]["matching_text_count"] == 0
    assert failed[0]["candidates"][0]["contains_forbidden_text"] is True


def test_page19_current_table_text_satisfies_citation_hyphen_spacing_contract():
    result = validator.validate(PAGE19_TABLE_TEXT_FIXTURE, PAGE19_TABLE_TEXT_EXTRACTION)

    assert result["status"] == "PASS"
    assert result["summary"] == {"check_count": 1, "passed": 1, "failed": 0}
    check = result["checks"][0]
    assert check["id"] == "page19-table-omb-a130-citation-hyphen-spacing"
    assert check["candidate_count"] == 1
    assert check["matching_text_count"] == 1
    assert check["candidates"][0]["contains_expected_text"] is True
    assert check["candidates"][0]["contains_forbidden_text"] is False


def test_page19_validator_fails_if_table_citation_hyphen_space_reappears(tmp_path):
    extraction = json.loads(PAGE19_TABLE_TEXT_EXTRACTION.read_text())
    mutated = False
    for block in extraction["blocks"]:
        if block.get("id") == "actual:p19:table:0":
            block["text"] = str(block["text"]).replace("[OMB A-130]", "[OMB A- 130]", 1)
            mutated = True
            break
    assert mutated
    corrupted_path = tmp_path / "page19_corrupted_table_text.json"
    corrupted_path.write_text(json.dumps(extraction), encoding="utf-8")

    result = validator.validate(PAGE19_TABLE_TEXT_FIXTURE, corrupted_path)

    assert result["status"] == "FAIL"
    failed = [check for check in result["checks"] if check["status"] == "FAIL"]
    assert [check["id"] for check in failed] == ["page19-table-omb-a130-citation-hyphen-spacing"]
    assert failed[0]["matching_text_count"] == 0
    assert failed[0]["candidates"][0]["contains_forbidden_text"] is True


def test_page22_current_table_text_satisfies_citation_hyphen_spacing_contract():
    result = validator.validate(PAGE22_TABLE_TEXT_FIXTURE, PAGE22_TABLE_TEXT_EXTRACTION)

    assert result["status"] == "PASS"
    assert result["summary"] == {"check_count": 1, "passed": 1, "failed": 0}
    check = result["checks"][0]
    assert check["id"] == "page22-table-sp800-160-1-citation-hyphen-spacing"
    assert check["candidate_count"] == 1
    assert check["matching_text_count"] == 1
    assert check["candidates"][0]["contains_expected_text"] is True
    assert check["candidates"][0]["contains_forbidden_text"] is False


def test_page22_validator_fails_if_table_citation_hyphen_space_reappears(tmp_path):
    extraction = json.loads(PAGE22_TABLE_TEXT_EXTRACTION.read_text())
    mutated = False
    for block in extraction["blocks"]:
        if block.get("id") == "actual:p22:table:0":
            block["text"] = str(block["text"]).replace("[SP 800-160-1]", "[SP 800-160- 1]", 1)
            mutated = True
            break
    assert mutated
    corrupted_path = tmp_path / "page22_corrupted_table_text.json"
    corrupted_path.write_text(json.dumps(extraction), encoding="utf-8")

    result = validator.validate(PAGE22_TABLE_TEXT_FIXTURE, corrupted_path)

    assert result["status"] == "FAIL"
    failed = [check for check in result["checks"] if check["status"] == "FAIL"]
    assert [check["id"] for check in failed] == ["page22-table-sp800-160-1-citation-hyphen-spacing"]
    assert failed[0]["matching_text_count"] == 0
    assert failed[0]["candidates"][0]["contains_forbidden_text"] is True


def test_page186_current_extraction_satisfies_list_hyphen_wrap_contract():
    result = validator.validate(PAGE186_TEXT_FIXTURE, PAGE186_TEXT_EXTRACTION)

    assert result["status"] == "PASS"
    assert result["summary"] == {"check_count": 1, "passed": 1, "failed": 0}
    check = result["checks"][0]
    assert check["id"] == "page186-list-organization-defined-spacing"
    assert check["candidate_count"] == 1
    assert check["matching_text_count"] == 1
    assert check["candidates"][0]["contains_expected_text"] is True
    assert check["candidates"][0]["contains_forbidden_text"] is False


def test_page186_validator_fails_if_list_hyphen_wrap_space_reappears(tmp_path):
    extraction = json.loads(PAGE186_TEXT_EXTRACTION.read_text())
    mutated = False
    for block in extraction["blocks"]:
        if block.get("id") == "actual:p186:block:8":
            block["text"] = str(block["text"]).replace(
                "organization-defined entities",
                "organization- defined entities",
                1,
            )
            mutated = True
            break
    assert mutated
    corrupted_path = tmp_path / "page186_corrupted_list_text.json"
    corrupted_path.write_text(json.dumps(extraction), encoding="utf-8")

    result = validator.validate(PAGE186_TEXT_FIXTURE, corrupted_path)

    assert result["status"] == "FAIL"
    failed = [check for check in result["checks"] if check["status"] == "FAIL"]
    assert [check["id"] for check in failed] == ["page186-list-organization-defined-spacing"]
    assert failed[0]["matching_text_count"] == 0
    assert failed[0]["candidates"][0]["contains_forbidden_text"] is True


def test_page235_current_extraction_satisfies_body_hyphen_wrap_contract():
    result = validator.validate(PAGE235_TEXT_FIXTURE, PAGE235_TEXT_EXTRACTION)

    assert result["status"] == "PASS"
    assert result["summary"] == {"check_count": 1, "passed": 1, "failed": 0}
    check = result["checks"][0]
    assert check["id"] == "page235-body-organization-wide-risk-spacing"
    assert check["candidate_count"] == 1
    assert check["matching_text_count"] == 1
    assert check["candidates"][0]["contains_expected_text"] is True
    assert check["candidates"][0]["contains_forbidden_text"] is False


def test_page235_validator_fails_if_body_hyphen_wrap_space_reappears(tmp_path):
    extraction = json.loads(PAGE235_TEXT_EXTRACTION.read_text())
    mutated = False
    for block in extraction["blocks"]:
        if block.get("id") == "actual:p235:block:4":
            block["text"] = str(block["text"]).replace(
                "organization-wide risk management strategy",
                "organization- wide risk management strategy",
                1,
            )
            mutated = True
            break
    assert mutated
    corrupted_path = tmp_path / "page235_corrupted_body_text.json"
    corrupted_path.write_text(json.dumps(extraction), encoding="utf-8")

    result = validator.validate(PAGE235_TEXT_FIXTURE, corrupted_path)

    assert result["status"] == "FAIL"
    failed = [check for check in result["checks"] if check["status"] == "FAIL"]
    assert [check["id"] for check in failed] == ["page235-body-organization-wide-risk-spacing"]
    assert failed[0]["matching_text_count"] == 0
    assert failed[0]["candidates"][0]["contains_forbidden_text"] is True


def test_page403_current_extraction_suppresses_reference_header_false_table():
    result = validator.validate(PAGE403_TABLE_FIXTURE, PAGE403_TABLE_EXTRACTION)

    assert result["status"] == "PASS"
    assert result["summary"] == {"check_count": 1, "passed": 1, "failed": 0}
    check = result["checks"][0]
    assert check["id"] == "page403-reference-header-strip-is-not-table"
    assert check["candidate_count"] == 0
    assert check["spurious_table_count"] == 0


def test_page157_current_extraction_suppresses_full_page_false_table():
    result = validator.validate(PAGE157_TABLE_FIXTURE, PAGE157_TABLE_EXTRACTION)

    assert result["status"] == "PASS"
    assert result["summary"] == {"check_count": 1, "passed": 1, "failed": 0}
    check = result["checks"][0]
    assert check["id"] == "page157-full-page-chrome-artifact-is-not-table"
    assert check["candidate_count"] == 0
    assert check["spurious_table_count"] == 0


def test_page157_validator_fails_if_full_page_false_table_reappears(tmp_path):
    extraction = json.loads(PAGE157_TABLE_EXTRACTION.read_text())
    extraction["blocks"].append(
        {
            "id": "actual:p157:table:synthetic-full-page-chrome",
            "page": 157,
            "source_type": "table",
            "type": "table",
            "bbox": [
                0.03441176383323919,
                0.044999984779743235,
                0.8529026835572486,
                0.9518181820108433,
            ],
            "text": "Control Enhancements: None\nReferences: None.",
            "raw": {"row_count": 53, "column_count": 0, "col_count": 19},
        }
    )
    false_table_path = tmp_path / "page157_full_page_false_table.json"
    false_table_path.write_text(json.dumps(extraction), encoding="utf-8")

    result = validator.validate(PAGE157_TABLE_FIXTURE, false_table_path)

    assert result["status"] == "FAIL"
    failed = [check for check in result["checks"] if check["status"] == "FAIL"]
    assert [check["id"] for check in failed] == ["page157-full-page-chrome-artifact-is-not-table"]
    assert failed[0]["spurious_table_count"] == 1


def test_page399_current_extraction_satisfies_field_split_child_bbox_contract():
    result = validator.validate(PAGE399_BBOX_FIXTURE, PAGE399_BBOX_EXTRACTION)

    assert result["status"] == "PASS"
    assert result["summary"] == {"check_count": 2, "passed": 2, "failed": 0}
    assert {check["id"] for check in result["checks"]} == {
        "page399-enhancement2-discussion-child-bbox",
        "page399-enhancement2-related-controls-child-bbox",
    }
    assert all(check["matching_bbox_count"] == 1 for check in result["checks"])


def test_page399_validator_fails_if_field_split_child_bbox_reuses_parent_width(tmp_path):
    extraction = json.loads(PAGE399_BBOX_EXTRACTION.read_text())
    mutated = False
    for block in extraction["blocks"]:
        if block.get("id") == "actual:p399:block:22#related_controls":
            block["bbox"][2] = 0.8445478451797386
            mutated = True
            break
    assert mutated
    wide_child_path = tmp_path / "page399_wide_field_split_child.json"
    wide_child_path.write_text(json.dumps(extraction), encoding="utf-8")

    result = validator.validate(PAGE399_BBOX_FIXTURE, wide_child_path)

    assert result["status"] == "FAIL"
    failed = [check for check in result["checks"] if check["status"] == "FAIL"]
    assert [check["id"] for check in failed] == ["page399-enhancement2-related-controls-child-bbox"]
    assert failed[0]["matching_bbox_count"] == 0


def test_page403_validator_fails_if_reference_header_false_table_reappears(tmp_path):
    extraction = json.loads(PAGE403_TABLE_EXTRACTION.read_text())
    extraction["blocks"].append(
        {
            "id": "actual:p403:table:synthetic-reference-header",
            "page": 403,
            "source_type": "table",
            "type": "table",
            "bbox": [0.0, 0.0571970066638908, 1.0, 0.11047979797979798],
            "text": (
                "__________ | __________________________________________________\n"
                "[5 CFR 73 | 1] Code of Federal Regulations"
            ),
            "raw": {"row_count": 2, "column_count": 2},
        }
    )
    false_table_path = tmp_path / "page403_reference_header_false_table.json"
    false_table_path.write_text(json.dumps(extraction), encoding="utf-8")

    result = validator.validate(PAGE403_TABLE_FIXTURE, false_table_path)

    assert result["status"] == "FAIL"
    failed = [check for check in result["checks"] if check["status"] == "FAIL"]
    assert [check["id"] for check in failed] == ["page403-reference-header-strip-is-not-table"]
    assert failed[0]["spurious_table_count"] == 1


def test_page30_validator_fails_if_rotated_side_chrome_bbox_overlaps_body(tmp_path):
    extraction = json.loads(PAGE30_EXTRACTION.read_text())
    for block in extraction["blocks"]:
        text = " ".join(str(block.get("text") or "").split())
        if text.startswith("This publication is available free of charge from:"):
            block["bbox"] = [
                0.03441176383323919,
                0.27643939702197756,
                0.5297058735018462,
                0.7178787847962043,
            ]
            break
    leaked_path = tmp_path / "page30_wide_side_chrome_bbox.json"
    leaked_path.write_text(json.dumps(extraction), encoding="utf-8")

    result = validator.validate(PAGE30_FIXTURE, leaked_path)

    assert result["status"] == "FAIL"
    failed = [check for check in result["checks"] if check["status"] == "FAIL"]
    assert [check["id"] for check in failed] == [
        "page30-rotated-side-chrome-bbox-margin-constrained"
    ]
    assert failed[0]["matching_bbox_count"] == 0
    assert failed[0]["candidates"][0]["bbox"][2] > 0.50


def test_page456_validator_fails_if_table_header_reappears_as_top_level_block(tmp_path):
    extraction = json.loads(EXTRACTION.read_text())
    extraction["blocks"].append(
        {
            "id": "actual:p456:line:synthetic-leak",
            "page": 456,
            "source_type": "Header",
            "type": "section_header",
            "bbox": [0.606, 0.124, 0.728, 0.150],
            "text": "IMPLEMENTED BY",
        }
    )
    leaked_path = tmp_path / "page456_leaked.json"
    leaked_path.write_text(json.dumps(extraction), encoding="utf-8")

    result = validator.validate(FIXTURE, leaked_path)

    assert result["status"] == "FAIL"
    failed = [check for check in result["checks"] if check["status"] == "FAIL"]
    assert [check["id"] for check in failed] == ["page456-implemented-by-header-not-top-level"]
    assert failed[0]["leaking_candidate_count"] == 1
    assert failed[0]["candidates"][0]["id"] == "actual:p456:line:synthetic-leak"


def test_creator_reviewer_defect_bundle_shape_is_fail_closed(tmp_path):
    bad_bundle = tmp_path / "bad.json"
    bad_bundle.write_text(json.dumps({"schema": "wrong", "checks": []}), encoding="utf-8")

    result = validator.validate(bad_bundle, EXTRACTION)

    assert result["status"] == "INVALID"
    assert "schema must be pdf_oxide.pdf_lab.creator_reviewer_defects.v1" in result["errors"]
    assert "checks must be a non-empty list" in result["errors"]
