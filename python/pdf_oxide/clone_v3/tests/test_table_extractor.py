"""Tests for table_extractor module."""
import pytest
from pathlib import Path

from pdf_oxide.clone_v3.table_extractor import (
    ExtractedTable,
    extract_all_tables,
    extract_tables_from_page,
)


# Use generated fixtures for testing
FIXTURES_DIR = Path(__file__).parents[4] / "tests" / "fixtures" / "generated"
TABLE_FIXTURES_PDF = FIXTURES_DIR / "table_fixtures.pdf"


@pytest.fixture
def table_shapes():
    """Sample table shapes from profiler."""
    return [
        {"page": 0, "rows": 4, "cols": 3, "bbox": [72, 100, 540, 200], "ruled": True},
    ]


class TestExtractedTable:
    def test_to_dict(self):
        table = ExtractedTable(
            page=0,
            rows=3,
            cols=2,
            bbox=(72, 100, 540, 200),
            ruled=True,
            headers=["Col A", "Col B"],
            data=[["R1C1", "R1C2"], ["R2C1", "R2C2"]],
        )
        d = table.to_dict()
        assert d["page"] == 0
        assert d["rows"] == 3
        assert d["cols"] == 2
        assert d["headers"] == ["Col A", "Col B"]
        assert len(d["data"]) == 2

    def test_from_dict_roundtrip(self):
        original = ExtractedTable(
            page=1,
            rows=5,
            cols=3,
            bbox=(50, 50, 500, 300),
            ruled=False,
            headers=["H1", "H2", "H3"],
            data=[["a", "b", "c"]],
        )
        d = original.to_dict()
        restored = ExtractedTable.from_dict(d)
        assert restored.page == original.page
        assert restored.rows == original.rows
        assert restored.headers == original.headers

    def test_to_dataframe(self):
        table = ExtractedTable(
            page=0,
            rows=3,
            cols=2,
            bbox=(0, 0, 100, 100),
            ruled=True,
            headers=["Name", "Value"],
            data=[["Alice", "100"], ["Bob", "200"]],
        )
        df = table.to_dataframe()
        assert list(df.columns) == ["Name", "Value"]
        assert len(df) == 2
        assert df.iloc[0]["Name"] == "Alice"


class TestExtractTablesFromPage:
    @pytest.mark.skipif(
        not TABLE_FIXTURES_PDF.exists(),
        reason="table_fixtures.pdf not generated"
    )
    def test_extract_from_fixture(self):
        from pdf_oxide.clone_profiler import profile_for_cloning

        profile = profile_for_cloning(str(TABLE_FIXTURES_PDF))
        table_shapes = profile.get("table_shapes", [])

        if not table_shapes:
            pytest.skip("No tables found in fixture")

        # Extract from first page with tables
        first_page = table_shapes[0]["page"]
        tables = extract_tables_from_page(
            str(TABLE_FIXTURES_PDF), first_page, table_shapes
        )

        assert len(tables) > 0
        assert all(isinstance(t, ExtractedTable) for t in tables)


class TestExtractAllTables:
    @pytest.mark.skipif(
        not TABLE_FIXTURES_PDF.exists(),
        reason="table_fixtures.pdf not generated"
    )
    def test_extract_all_from_fixture(self):
        from pdf_oxide.clone_profiler import profile_for_cloning

        profile = profile_for_cloning(str(TABLE_FIXTURES_PDF))
        table_shapes = profile.get("table_shapes", [])

        if not table_shapes:
            pytest.skip("No tables found in fixture")

        tables = extract_all_tables(str(TABLE_FIXTURES_PDF), table_shapes)

        assert len(tables) == len(table_shapes)
        # Check that at least some tables have content
        with_content = [t for t in tables if t.data]
        assert len(with_content) > 0, "Expected some tables with extracted content"
