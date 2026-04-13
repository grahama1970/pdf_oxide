"""Tests for content_generator module."""
import pytest

from pdf_oxide.clone_v3.content_generator import (
    GeneratedTable,
    _infer_column_types,
    _build_generation_prompt,
)
from pdf_oxide.clone_v3.table_extractor import ExtractedTable


class TestGeneratedTable:
    def test_to_dict(self):
        table = GeneratedTable(
            page=0,
            rows=3,
            cols=2,
            bbox=(72, 100, 540, 200),
            ruled=True,
            headers=["ID", "Name"],
            data=[["001", "Alice"], ["002", "Bob"]],
            source_summary="Generated from test",
        )
        d = table.to_dict()
        assert d["page"] == 0
        assert d["rows"] == 3
        assert d["headers"] == ["ID", "Name"]
        assert d["source_summary"] == "Generated from test"


class TestInferColumnTypes:
    def test_identifier_column(self):
        headers = ["ID", "Name", "Status"]
        data = [["001", "Test", "Active"]]
        types = _infer_column_types(headers, data)
        assert types[0] == "identifier"

    def test_status_column(self):
        headers = ["Task", "Status"]
        data = [["Do thing", "Complete"]]
        types = _infer_column_types(headers, data)
        assert types[1] == "status"

    def test_date_column(self):
        headers = ["Event", "Date"]
        data = [["Meeting", "2024-01-15"]]
        types = _infer_column_types(headers, data)
        assert types[1] == "date"

    def test_numeric_from_data(self):
        headers = ["Col A", "Col B"]
        data = [["text", "123.45"]]
        types = _infer_column_types(headers, data)
        assert types[1] == "numeric"

    def test_text_long_content(self):
        headers = ["Description"]
        data = [["A" * 60]]  # > 50 chars
        types = _infer_column_types(headers, data)
        assert types[0] == "text"


class TestBuildGenerationPrompt:
    def test_includes_headers(self):
        extracted = ExtractedTable(
            page=0,
            rows=3,
            cols=2,
            bbox=(0, 0, 100, 100),
            ruled=True,
            headers=["Req ID", "Description"],
            data=[["REQ-001", "Test requirement"]],
        )
        prompt = _build_generation_prompt(extracted)
        assert "Req ID" in prompt
        assert "Description" in prompt

    def test_includes_sample_data(self):
        extracted = ExtractedTable(
            page=0,
            rows=5,
            cols=2,
            bbox=(0, 0, 100, 100),
            ruled=True,
            headers=["A", "B"],
            data=[["r1", "c1"], ["r2", "c2"], ["r3", "c3"], ["r4", "c4"]],
        )
        prompt = _build_generation_prompt(extracted)
        # Should include first 3 rows
        assert "r1" in prompt
        assert "r2" in prompt
        assert "r3" in prompt

    def test_respects_num_rows(self):
        extracted = ExtractedTable(
            page=0,
            rows=3,
            cols=2,
            bbox=(0, 0, 100, 100),
            ruled=True,
            headers=["A", "B"],
            data=[["a", "b"]],
        )
        prompt = _build_generation_prompt(extracted, num_rows=10)
        assert "10 rows" in prompt

    def test_includes_toc_context(self):
        extracted = ExtractedTable(
            page=0,
            rows=2,
            cols=2,
            bbox=(0, 0, 100, 100),
            ruled=True,
            headers=["A", "B"],
            data=[],
        )
        prompt = _build_generation_prompt(extracted, toc_context="Security Controls")
        assert "Security Controls" in prompt
        assert "DOCUMENT CONTEXT" in prompt


# Integration tests require scillm - mark as slow/integration
class TestGenerateSimilarTable:
    @pytest.mark.skip(reason="Requires scillm endpoint")
    def test_generate_similar_table_integration(self):
        """Integration test - requires scillm running."""
        pass


class TestGenerateAllTables:
    @pytest.mark.skip(reason="Requires scillm endpoint")
    def test_generate_all_tables_integration(self):
        """Integration test - requires scillm running."""
        pass
