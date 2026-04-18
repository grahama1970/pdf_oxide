"""Tests for sampler-aware content generator."""
from __future__ import annotations

from pdf_oxide.clone.clone_types import SectionBudget
from pdf_oxide.clone.sampler_content import (
    generate_section_content,
    make_content_generator,
    _map_content_type,
    _map_domain,
)


def test_map_content_type_requirement():
    budget = SectionBudget(
        section_id=0, title="Test", depth=0, start_page=0, end_page=1,
        content_type="requirements"
    )
    assert _map_content_type(budget) == "requirement"


def test_map_content_type_prose():
    budget = SectionBudget(
        section_id=0, title="Test", depth=0, start_page=0, end_page=1,
        content_type="prose"
    )
    assert _map_content_type(budget) == "prose"


def test_map_domain_defense():
    budget = SectionBudget(
        section_id=0, title="Test", depth=0, start_page=0, end_page=1,
        domain="defense"
    )
    assert _map_domain(budget) == "defense"


def test_map_domain_nist():
    budget = SectionBudget(
        section_id=0, title="Test", depth=0, start_page=0, end_page=1,
        domain="government"
    )
    assert _map_domain(budget) == "nist"


def test_generate_section_content_returns_paragraphs_and_tables():
    budget = SectionBudget(
        section_id=1,
        title="Test Section",
        depth=0,
        start_page=0,
        end_page=2,
        paragraph_count=3,
        table_count=1,
        content_type="prose",
        domain="general",
    )
    content = generate_section_content(budget, seed=42)

    assert "paragraphs" in content
    assert "tables" in content
    assert len(content["paragraphs"]) >= 1
    assert len(content["tables"]) == 1


def test_generate_section_content_uses_sampler_hints():
    budget = SectionBudget(
        section_id=2,
        title="Requirements Section",
        depth=0,
        start_page=0,
        end_page=3,
        paragraph_count=2,
        table_count=0,
        has_requirements=True,
        content_type="requirement",
        domain="nist",
        sampler_hints={
            "sampled_pages": [0, 1, 2],
            "content_votes": {"requirements": 2, "prose": 1},
            "table_windows": 2,
            "avg_char_count": 1500,
        },
    )
    content = generate_section_content(budget, seed=99)

    # Should have 2 tables based on table_windows hint
    assert len(content["tables"]) == 2

    # Should have ~3 paragraphs based on avg_char_count (1500 / 500 = 3)
    assert len(content["paragraphs"]) >= 1


def test_generate_section_content_deterministic():
    budget = SectionBudget(
        section_id=5,
        title="Deterministic Test",
        depth=1,
        start_page=10,
        end_page=12,
        paragraph_count=2,
        table_count=1,
    )
    content1 = generate_section_content(budget, seed=123)
    content2 = generate_section_content(budget, seed=123)

    assert content1["paragraphs"] == content2["paragraphs"]
    assert content1["tables"] == content2["tables"]


def test_generate_section_content_different_seeds():
    budget = SectionBudget(
        section_id=5,
        title="Seed Test",
        depth=1,
        start_page=10,
        end_page=12,
        paragraph_count=2,
        table_count=1,
    )
    content1 = generate_section_content(budget, seed=100)
    content2 = generate_section_content(budget, seed=200)

    # Different seeds should produce different content (fallback includes seed in hash)
    assert content1["paragraphs"] != content2["paragraphs"]


def test_make_content_generator_callable():
    gen = make_content_generator(seed=42)
    budget = SectionBudget(
        section_id=0, title="Test", depth=0, start_page=0, end_page=1,
        paragraph_count=1, table_count=0,
    )
    content = gen(budget)

    assert "paragraphs" in content
    assert len(content["paragraphs"]) >= 1


def test_fallback_table_structure():
    budget = SectionBudget(
        section_id=3,
        title="Table Test",
        depth=0,
        start_page=0,
        end_page=1,
        table_count=1,
        has_requirements=True,
    )
    content = generate_section_content(budget, seed=42)

    table = content["tables"][0]
    assert "headers" in table
    assert "rows" in table
    assert len(table["headers"]) == 3
    assert len(table["rows"]) >= 3
    # Requirement tables should have Control ID header
    assert "Control" in table["headers"][0]
