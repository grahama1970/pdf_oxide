import re

from pdf_oxide.extract_for_pdflab import (
    build_section_ranges,
    section_type_for_page,
    _merge_bracket_citation_rows,
)


def test_build_section_ranges_same_page_non_negative():
    toc = [
        {'title': 'ERRATA', 'page': 19, 'type': 'header'},
        {'title': 'GLOSSARY', 'page': 19, 'type': 'header'},
        {'title': 'ACRONYMS', 'page': 21, 'type': 'header'},
    ]
    ranges = build_section_ranges(toc, effective_page_count=25)
    assert ranges[0]['start'] == 18
    assert ranges[0]['end'] >= ranges[0]['start']
    assert ranges[1]['start'] == 18
    assert ranges[1]['end'] >= ranges[1]['start']


def test_build_section_ranges_honors_effective_page_limit():
    toc = [
        {'title': 'GLOSSARY', 'page': 420, 'type': 'header'},
        {'title': 'ACRONYMS', 'page': 450, 'type': 'header'},
    ]
    ranges = build_section_ranges(toc, effective_page_count=430)
    assert all(0 <= r['start'] <= 429 for r in ranges)
    assert all(0 <= r['end'] <= 429 for r in ranges)


def test_section_type_for_page_mapping():
    ranges = [
        {'title': 'ERRATA', 'start': 18, 'end': 26},
        {'title': 'GLOSSARY', 'start': 420, 'end': 449},
        {'title': 'ACRONYMS', 'start': 450, 'end': 453},
        {'title': 'APPENDIX F CONTROL SUMMARIES', 'start': 454, 'end': 491},
    ]
    assert section_type_for_page(20, ranges) == 'errata'
    assert section_type_for_page(430, ranges) == 'glossary'
    assert section_type_for_page(451, ranges) == 'acronyms'
    assert section_type_for_page(470, ranges) == 'summaries'


def test_merge_bracket_citation_rows_glossary():
    table_text = """TERM | DEFINITION
Access Control | Limits system access
[ SP 800-128 ]
Audit | Events recorded"""
    merged = _merge_bracket_citation_rows(table_text)
    rows = [r for r in merged.splitlines()[1:] if r.strip()]
    assert not any(r.lstrip().startswith('[') for r in rows)
    assert any('[ SP 800-128 ]' in r for r in rows)
    assert all('|' in r for r in rows)
