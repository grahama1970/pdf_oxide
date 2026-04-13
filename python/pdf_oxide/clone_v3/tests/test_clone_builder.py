"""Tests for clone_builder module."""
import pytest

from pdf_oxide.clone_v3.clone_builder import (
    QidAllocator,
    get_styles,
    build_table_with_qids,
)
from pdf_oxide.clone_v3.content_generator import GeneratedTable


class TestQidAllocator:
    def test_allocate_returns_qid_and_token(self):
        allocator = QidAllocator(doc_id="test123", seed=42)
        qid, token = allocator.allocate("cell", 0, 1, 2)

        assert qid.startswith("QID_")
        assert len(qid) == 16  # QID_ + 12 hex chars
        assert isinstance(token, int)
        assert 0 <= token < 2**20

    def test_deterministic_allocation(self):
        allocator1 = QidAllocator(doc_id="test", seed=42)
        allocator2 = QidAllocator(doc_id="test", seed=42)

        qid1, _ = allocator1.allocate("cell", 0, 0, 0)
        qid2, _ = allocator2.allocate("cell", 0, 0, 0)

        assert qid1 == qid2

    def test_different_seeds_different_qids(self):
        allocator1 = QidAllocator(doc_id="test", seed=42)
        allocator2 = QidAllocator(doc_id="test", seed=43)

        qid1, _ = allocator1.allocate("cell", 0, 0, 0)
        qid2, _ = allocator2.allocate("cell", 0, 0, 0)

        assert qid1 != qid2

    def test_register_and_get_manifest(self):
        allocator = QidAllocator(doc_id="test", seed=42)
        qid, _ = allocator.allocate("cell", 0, 0, 0)
        allocator.register(qid, "Hello World")

        manifest = allocator.get_manifest()
        assert qid in manifest
        assert manifest[qid] == "Hello World"

    def test_multiple_registrations(self):
        allocator = QidAllocator(doc_id="test", seed=42)

        qid1, _ = allocator.allocate("cell", 0, 0, 0)
        qid2, _ = allocator.allocate("cell", 0, 0, 1)

        allocator.register(qid1, "First")
        allocator.register(qid2, "Second")

        manifest = allocator.get_manifest()
        assert len(manifest) == 2
        assert manifest[qid1] == "First"
        assert manifest[qid2] == "Second"


class TestGetStyles:
    def test_returns_expected_styles(self):
        styles = get_styles()

        assert "title" in styles
        assert "h1" in styles
        assert "h2" in styles
        assert "h3" in styles
        assert "body" in styles
        # Note: cell/header_cell styles handled inline in build_table_with_qids

    def test_styles_have_font_sizes(self):
        styles = get_styles()

        assert styles["title"].fontSize > styles["h1"].fontSize
        assert styles["h1"].fontSize > styles["body"].fontSize


class TestBuildTableWithQids:
    def test_builds_table_with_headers(self):
        generated = GeneratedTable(
            page=0,
            rows=3,
            cols=2,
            bbox=(72, 100, 540, 200),
            ruled=True,
            headers=["Col A", "Col B"],
            data=[["R1C1", "R1C2"], ["R2C1", "R2C2"]],
            source_summary="Test table",
        )
        allocator = QidAllocator(doc_id="test", seed=42)

        table, cell_manifests = build_table_with_qids(
            generated, allocator, table_id="t0"
        )

        # Table should be created
        assert table is not None
        # Should have cell manifests for headers + data
        assert len(cell_manifests) == 6  # 2 headers + 4 data cells

    def test_allocates_qids_for_cells(self):
        generated = GeneratedTable(
            page=0,
            rows=2,
            cols=2,
            bbox=(0, 0, 100, 100),
            ruled=True,
            headers=["A", "B"],
            data=[["a1", "b1"]],
            source_summary="Test",
        )
        allocator = QidAllocator(doc_id="test", seed=42)

        table, cell_manifests = build_table_with_qids(
            generated, allocator, table_id="t0"
        )

        manifest = allocator.get_manifest()
        # Should have QIDs for headers + data cells
        assert len(manifest) >= 4  # 2 headers + 2 data cells

    def test_cell_manifests_contain_qids(self):
        generated = GeneratedTable(
            page=0,
            rows=2,
            cols=2,
            bbox=(0, 0, 100, 100),
            ruled=True,
            headers=["H1", "H2"],
            data=[["d1", "d2"]],
            source_summary="Test",
        )
        allocator = QidAllocator(doc_id="test", seed=42)

        table, cell_manifests = build_table_with_qids(
            generated, allocator, table_id="t0"
        )

        # Check manifest structure
        for cm in cell_manifests:
            assert "qid" in cm
            assert cm["qid"].startswith("QID_")
            assert "text" in cm
            assert "row" in cm
            assert "col" in cm


# Integration tests
class TestClonePdf:
    @pytest.mark.skip(reason="Requires scillm endpoint and PDF fixtures")
    def test_clone_pdf_integration(self):
        """Integration test - requires scillm and fixtures."""
        pass
