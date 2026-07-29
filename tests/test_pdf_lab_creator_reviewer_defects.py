import importlib.util
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = REPO / "scripts/pdf_lab/validate_creator_reviewer_defects.py"
FIXTURE = REPO / "tests/fixtures/pdf_lab/page456_creator_reviewer_defects.json"
PAGE27_FIXTURE = REPO / "tests/fixtures/pdf_lab/page27_creator_reviewer_defects.json"
EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/page456_control_table_header/runs/page456-control-table-header-after-cellbbox-20260726T130204Z/extraction.pdf_oxide.json"
)
PAGE27_EXTRACTION = (
    REPO
    / "artifacts/pdf_lab/creator_reviewer_next_candidate_page27_20260729T160711Z/extraction.pdf_oxide.json"
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


def test_page27_current_extraction_fails_running_footer_label_contract():
    result = validator.validate(PAGE27_FIXTURE, PAGE27_EXTRACTION)

    assert result["status"] == "FAIL"
    assert result["summary"] == {"check_count": 1, "passed": 0, "failed": 1}
    check = result["checks"][0]
    assert check["id"] == "page27-printed-page-number-xxv-is-running-footer"
    assert check["candidate_count"] == 1
    assert check["matching_label_count"] == 0
    assert check["expected_label"] == "running_footer"
    assert check["candidates"][0]["type"] == "header_footer_noise"
    assert check["candidates"][0]["text"] == "xxv"


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
