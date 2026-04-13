"""Tests for clone_types.py — RenderPlan, TruthManifest, validation."""
import json
import tempfile
from pathlib import Path

import pytest

from pdf_oxide.clone import (
    # Types
    PageType,
    BlockType,
    SourceProfileRef,
    SectionBudget,
    PageRegime,
    RenderPlan,
    TruthObject,
    TruthManifest,
    derive_render_plan,
    # Validation
    QidRecovery,
    OrderingResult,
    ValidationResult,
    extract_qids_from_text,
    validate_from_text,
    validate_extraction,
)


class TestPageType:
    """Test PageType enum."""

    def test_values(self):
        assert PageType.FRONT_MATTER.value == "front_matter"
        assert PageType.TABLE_HEAVY.value == "table_heavy"
        assert len(PageType) == 7


class TestBlockType:
    """Test BlockType enum."""

    def test_values(self):
        assert BlockType.HEADING.value == "heading"
        assert BlockType.TABLE_CELL.value == "table_cell"
        assert len(BlockType) == 16


class TestSourceProfileRef:
    """Test SourceProfileRef wrapper."""

    def test_accessors(self):
        profile = {
            "doc_id": "abc123",
            "path": "/test.pdf",
            "page_count": 10,
            "domain": "government",
            "layout_mode": "single_column",
            "has_toc": True,
            "toc_sections": [{"id": 0, "title": "Intro", "page": 1}],
            "table_shapes": [{"page": 3, "rows": 5, "cols": 3}],
            "page_signatures": [{"page_num": 0, "is_blank": False}],
        }
        ref = SourceProfileRef(profile)

        assert ref.doc_id == "abc123"
        assert ref.path == "/test.pdf"
        assert ref.page_count == 10
        assert ref.domain == "government"
        assert ref.has_toc is True
        assert len(ref.toc_sections) == 1
        assert len(ref.table_shapes) == 1

    def test_get_table_shapes_for_page(self):
        profile = {
            "table_shapes": [
                {"page": 1, "rows": 5, "cols": 3},
                {"page": 1, "rows": 3, "cols": 2},
                {"page": 3, "rows": 10, "cols": 4},
            ],
        }
        ref = SourceProfileRef(profile)

        page1_tables = ref.get_table_shapes_for_page(1)
        assert len(page1_tables) == 2

        page2_tables = ref.get_table_shapes_for_page(2)
        assert len(page2_tables) == 0


class TestRenderPlan:
    """Test RenderPlan generation and serialization."""

    def test_derive_from_profile(self):
        profile = {
            "path": "/test.pdf",
            "page_count": 5,
            "domain": "general",
            "layout_mode": "single_column",
            "toc_sections": [
                {"id": 0, "title": "Introduction", "page": 0, "depth": 0},
                {"id": 1, "title": "Methods", "page": 2, "depth": 0},
            ],
            "table_shapes": [{"page": 3, "rows": 5, "cols": 3}],
            "page_signatures": [
                {"page_num": i, "is_blank": False, "table_candidate": False}
                for i in range(5)
            ],
            "running_headers": [],
            "running_footers": [],
            "font_map": {},
            "requirements_pages": [],
            "list_pages": [],
            "footnote_pages": [],
            "callout_pages": [],
            "toc_pages": [],
        }
        ref = SourceProfileRef(profile)
        plan = derive_render_plan(ref, seed=42)

        assert plan.doc_id is not None
        assert plan.source_path == "/test.pdf"
        assert plan.seed == 42
        assert plan.page_count == 5
        assert len(plan.section_budgets) == 2
        assert plan.total_tables() == 1

    def test_serialization(self):
        plan = RenderPlan(
            doc_id="test123",
            source_path="/test.pdf",
            seed=42,
            page_count=10,
        )
        plan.section_budgets.append(
            SectionBudget(
                section_id=0,
                title="Test Section",
                depth=0,
                start_page=0,
                end_page=5,
            )
        )
        plan.page_regimes.append(
            PageRegime(
                page_type=PageType.BODY_TEXT,
                start_page=0,
                end_page=10,
            )
        )

        d = plan.to_dict()
        assert d["doc_id"] == "test123"
        assert d["section_count"] == 1
        assert len(d["page_regimes"]) == 1

    def test_save_load(self):
        plan = RenderPlan(
            doc_id="test123",
            source_path="/test.pdf",
            seed=42,
        )

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            plan.save(f.name)
            loaded = json.loads(Path(f.name).read_text())
            assert loaded["doc_id"] == "test123"


class TestTruthManifest:
    """Test TruthManifest for render-time oracle."""

    def test_register_objects(self):
        manifest = TruthManifest(
            doc_id="test123",
            source_path="/test.pdf",
            output_path="/test_clone.pdf",
            seed=42,
        )

        obj = TruthObject(
            qid="QID_0000000000000001",
            block_type=BlockType.PARAGRAPH,
            logical_text="Hello world",
            rendered_text="[QID_0000000000000001]Hello world",
            page_num=0,
            sequence_num=0,
        )
        manifest.register(obj)

        assert manifest.total_qids == 1
        assert "QID_0000000000000001" in manifest.qid_to_object
        assert 0 in manifest.page_qid_order
        assert manifest.page_qid_order[0] == ["QID_0000000000000001"]

    def test_register_table_structure(self):
        manifest = TruthManifest(
            doc_id="test123",
            source_path="/test.pdf",
            output_path="/test_clone.pdf",
            seed=42,
        )

        manifest.register_table_structure(
            table_id="t0",
            rows=3,
            cols=2,
            cell_qids=[
                ["QID_A", "QID_B"],
                ["QID_C", "QID_D"],
                ["QID_E", "QID_F"],
            ],
        )

        assert manifest.total_tables == 1
        assert manifest.table_structures["t0"]["rows"] == 3
        assert manifest.table_structures["t0"]["cols"] == 2

    def test_save_load(self):
        manifest = TruthManifest(
            doc_id="test123",
            source_path="/test.pdf",
            output_path="/test_clone.pdf",
            seed=42,
        )

        obj = TruthObject(
            qid="QID_0000000000000001",
            block_type=BlockType.HEADING,
            logical_text="Title",
            rendered_text="[QID_0000000000000001]Title",
            page_num=0,
            sequence_num=0,
            section_id=1,
            depth=0,
        )
        manifest.register(obj)
        manifest.register_section(1, "Title", 0, "QID_0000000000000001")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            manifest.save(f.name)
            loaded = TruthManifest.load(f.name)

            assert loaded.doc_id == "test123"
            assert loaded.total_qids == 1
            assert loaded.total_sections == 1
            assert loaded.qid_to_object["QID_0000000000000001"].block_type == BlockType.HEADING


class TestValidation:
    """Test validation functions."""

    def test_extract_qids_from_text(self):
        text = "[QID_0000000000000001]Hello [QID_0000000000000002]World"
        qids = extract_qids_from_text(text)
        assert qids == ["QID_0000000000000001", "QID_0000000000000002"]

    def test_extract_qids_empty(self):
        assert extract_qids_from_text("No QIDs here") == []

    def test_validate_from_text_perfect(self):
        manifest = TruthManifest(
            doc_id="test", source_path="", output_path="", seed=42
        )
        for i in range(3):
            qid = f"QID_{i:016d}"
            manifest.register(TruthObject(
                qid=qid,
                block_type=BlockType.PARAGRAPH,
                logical_text=f"Para {i}",
                rendered_text=f"[{qid}]Para {i}",
                page_num=0,
                sequence_num=i,
            ))

        text = "[QID_0000000000000000]Para 0 [QID_0000000000000001]Para 1 [QID_0000000000000002]Para 2"
        result = validate_from_text(manifest, text)

        assert result.qid_recovery.recovery_rate == 1.0
        assert result.ordering.ordering_score == 1.0
        assert result.passed

    def test_validate_from_text_missing(self):
        manifest = TruthManifest(
            doc_id="test", source_path="", output_path="", seed=42
        )
        for i in range(3):
            qid = f"QID_{i:016d}"
            manifest.register(TruthObject(
                qid=qid,
                block_type=BlockType.PARAGRAPH,
                logical_text=f"Para {i}",
                rendered_text=f"[{qid}]Para {i}",
                page_num=0,
                sequence_num=i,
            ))

        # Missing middle QID
        text = "[QID_0000000000000000]Para 0 [QID_0000000000000002]Para 2"
        result = validate_from_text(manifest, text)

        assert result.qid_recovery.found == 2
        assert result.qid_recovery.expected == 3
        assert "QID_0000000000000001" in result.qid_recovery.missing

    def test_validate_from_text_inverted(self):
        manifest = TruthManifest(
            doc_id="test", source_path="", output_path="", seed=42
        )
        for i in range(3):
            qid = f"QID_{i:016d}"
            manifest.register(TruthObject(
                qid=qid,
                block_type=BlockType.PARAGRAPH,
                logical_text=f"Para {i}",
                rendered_text=f"[{qid}]Para {i}",
                page_num=0,
                sequence_num=i,
            ))

        # Inverted order
        text = "[QID_0000000000000002]Para 2 [QID_0000000000000001]Para 1 [QID_0000000000000000]Para 0"
        result = validate_from_text(manifest, text)

        assert result.qid_recovery.recovery_rate == 1.0
        assert result.ordering.ordering_score < 1.0
        assert len(result.ordering.inversions) > 0

    def test_validation_result_summary(self):
        result = ValidationResult(
            manifest_path="test.json",
            extraction_source="test.pdf",
            qid_recovery=QidRecovery(expected=100, found=95, missing=["Q1", "Q2", "Q3", "Q4", "Q5"]),
            ordering=OrderingResult(total_pairs=50, correct_pairs=48),
        )

        summary = result.summary()
        assert "95/100" in summary
        assert "48/50" in summary


class TestIntegration:
    """Integration tests with real profile data."""

    def test_profile_to_plan_to_manifest(self):
        """End-to-end: profile → plan → manifest → validation."""
        # Minimal profile
        profile = {
            "path": "/test.pdf",
            "page_count": 3,
            "domain": "general",
            "layout_mode": "single_column",
            "toc_sections": [
                {"id": 0, "title": "Introduction", "page": 0, "depth": 0},
            ],
            "table_shapes": [],
            "page_signatures": [
                {"page_num": i, "is_blank": False} for i in range(3)
            ],
            "running_headers": [],
            "running_footers": [],
            "font_map": {},
            "requirements_pages": [],
            "list_pages": [],
            "footnote_pages": [],
            "callout_pages": [],
            "toc_pages": [],
        }

        # Derive plan
        ref = SourceProfileRef(profile)
        plan = derive_render_plan(ref, seed=42)
        assert plan.doc_id is not None

        # Create manifest
        manifest = TruthManifest(
            doc_id=plan.doc_id,
            source_path=plan.source_path,
            output_path="/clone.pdf",
            seed=plan.seed,
        )

        # Register objects
        for i, budget in enumerate(plan.section_budgets):
            qid = f"QID_{i:016d}"
            obj = TruthObject(
                qid=qid,
                block_type=BlockType.HEADING,
                logical_text=budget.title,
                rendered_text=f"[{qid}]{budget.title}",
                page_num=budget.start_page,
                sequence_num=i,
                section_id=budget.section_id,
                depth=budget.depth,
            )
            manifest.register(obj)

        # Validate
        extracted = "[QID_0000000000000000]Introduction"
        result = validate_from_text(manifest, extracted)
        assert result.qid_recovery.recovery_rate == 1.0
