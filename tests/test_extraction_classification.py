import re

from pdf_oxide.extract_for_pdflab import (
    build_section_ranges,
    section_type_for_page,
    _merge_bracket_citation_rows,
    _is_bullet_list_text,
    _is_numbered_footnote_text,
    _is_footer_page_chrome,
    _is_sidebar_page_chrome,
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


def test_pdflab_bbox_aware_list_footnote_and_footer_chrome_classification():
    assert _is_bullet_list_text('\u2022\nWhat security controls are needed?')
    assert _is_numbered_footnote_text(
        '1 An information system is a discrete set of information resources.',
        [0.14, 0.72, 0.84, 0.75],
    )
    assert not _is_numbered_footnote_text('1.1 PURPOSE', [0.14, 0.72, 0.84, 0.75])
    assert _is_footer_page_chrome('CHAPTER ONE\nPAGE 1', [0.14, 0.94, 0.85, 0.95])


def test_pdflab_sidebar_chrome_allows_interleaved_table_text():
    assert _is_sidebar_page_chrome(
        'This publication is available AC-2(4) free of charge from:',
        [0.032912, 0.272136, 0.345618, 0.289303],
    )
    assert _is_sidebar_page_chrome(
        'https://doi.org/10.6028/NIST.SP.800',
        [0.032912, 0.518727, 0.270618, 0.533091],
    )
    assert not _is_sidebar_page_chrome(
        'This publication is available AC-2(4) free of charge from:',
        [0.147, 0.272136, 0.345618, 0.289303],
    )
