"""
Integration tests for pdf_oxide absorbed pipeline functions.

Tests the Rust implementations against realistic data to verify
they actually work — not just compile.
"""

import pytest
from pdf_oxide import (
    PdfDocument,
    merge_tables,
    map_framework_controls,
)


# ============================================================
# merge_tables() integration tests
# ============================================================

class TestMergeTablesIntegration:
    """Test merge_tables with realistic Camelot-style table data."""

    def _make_table(self, index, page, bbox, cols, rows, title, headers=None):
        """Build a table dict matching the Python API contract."""
        if headers is None:
            headers = [f"col_{i}" for i in range(cols)]
        return {
            "index": index,
            "page": page,
            "bbox": bbox,  # accepts list or tuple
            "column_count": cols,
            "row_count": rows,
            "title": title,
            "headers": headers,
            "headers_are_numeric": all(h.isdigit() for h in headers),
        }

    def test_returns_expected_structure(self):
        """merge_tables returns dict with merged_groups, junk_indices, merge_details."""
        tables = [
            self._make_table(0, 0, [72.0, 100.0, 540.0, 700.0], 3, 10, "Table 1"),
        ]
        result = merge_tables(tables)
        assert isinstance(result, dict)
        assert "merged_groups" in result
        assert "junk_indices" in result
        assert "merge_details" in result

    def test_accepts_list_and_tuple_bbox(self):
        """bbox can be a Python list or tuple."""
        tables_list = [self._make_table(0, 0, [72.0, 100.0, 540.0, 700.0], 3, 10, "T")]
        tables_tuple = [self._make_table(0, 0, (72.0, 100.0, 540.0, 700.0), 3, 10, "T")]
        r1 = merge_tables(tables_list)
        r2 = merge_tables(tables_tuple)
        assert r1["merged_groups"] == r2["merged_groups"]

    def test_single_table_passthrough(self):
        """A single valid table passes through as its own group."""
        tables = [
            self._make_table(0, 0, [72.0, 100.0, 540.0, 700.0], 3, 10, "Revenue Data"),
        ]
        result = merge_tables(tables)
        assert len(result["merged_groups"]) == 1
        assert result["merged_groups"][0] == [0]
        assert result["junk_indices"] == []

    def test_junk_table_filtered(self):
        """1x1 tables are classified as junk."""
        tables = [
            self._make_table(0, 0, [50.0, 50.0, 200.0, 70.0], 1, 1, ""),
            self._make_table(1, 0, [72.0, 100.0, 540.0, 700.0], 4, 15, "Real Table"),
        ]
        result = merge_tables(tables)
        assert 0 in result["junk_indices"]
        assert any(1 in group for group in result["merged_groups"])

    def test_continued_title_merges(self):
        """Tables with 'Continued' in title on consecutive pages merge."""
        tables = [
            self._make_table(0, 3, [72.0, 100.0, 540.0, 700.0], 5, 20,
                           "Table 4-1: System Requirements"),
            self._make_table(1, 4, [72.0, 50.0, 540.0, 400.0], 5, 12,
                           "Table 4-1: System Requirements (Continued)"),
        ]
        result = merge_tables(tables)
        assert len(result["merged_groups"]) == 1
        assert result["merged_groups"][0] == [0, 1]
        assert result["merge_details"][0]["reason"] == "continued_in_title"

    def test_schema_match_merges(self):
        """Same column count + aligned bbox on consecutive pages merge."""
        tables = [
            self._make_table(0, 0, [72.0, 100.0, 540.0, 700.0], 4, 25, "Parts List",
                           ["Part No.", "Description", "Qty", "Unit"]),
            self._make_table(1, 1, [72.0, 80.0, 540.0, 500.0], 4, 15, "",
                           ["0", "1", "2", "3"]),
        ]
        result = merge_tables(tables)
        assert len(result["merged_groups"]) == 1
        assert result["merged_groups"][0] == [0, 1]

    def test_different_schemas_no_merge(self):
        """Tables with different column counts don't merge."""
        tables = [
            self._make_table(0, 0, [72.0, 100.0, 540.0, 700.0], 3, 10, "Table A"),
            self._make_table(1, 1, [72.0, 100.0, 540.0, 700.0], 6, 8, "Table B"),
        ]
        result = merge_tables(tables)
        assert len(result["merged_groups"]) == 2

    def test_non_consecutive_pages_no_merge(self):
        """Tables on non-consecutive pages don't merge even if similar."""
        tables = [
            self._make_table(0, 2, [72.0, 100.0, 540.0, 700.0], 4, 10, "Data"),
            self._make_table(1, 8, [72.0, 100.0, 540.0, 700.0], 4, 10, "Data"),
        ]
        result = merge_tables(tables)
        assert len(result["merged_groups"]) == 2

    def test_misaligned_tables_no_merge(self):
        """Tables with different horizontal positions don't merge."""
        tables = [
            self._make_table(0, 0, [72.0, 100.0, 300.0, 700.0], 3, 10, "Left Table"),
            self._make_table(1, 1, [350.0, 100.0, 540.0, 700.0], 3, 8, "Right Table"),
        ]
        result = merge_tables(tables)
        assert len(result["merged_groups"]) == 2

    def test_three_page_chain_merge(self):
        """Three consecutive pages with matching tables merge into one group."""
        tables = [
            self._make_table(0, 5, [72.0, 100.0, 540.0, 700.0], 4, 20,
                           "Table 7: Component Specifications"),
            self._make_table(1, 6, [72.0, 50.0, 540.0, 700.0], 4, 25,
                           "Table 7: Component Specifications (Continued)"),
            self._make_table(2, 7, [72.0, 50.0, 540.0, 400.0], 4, 10,
                           "Table 7: Component Specifications (Continued)"),
        ]
        result = merge_tables(tables)
        assert len(result["merged_groups"]) == 1
        assert result["merged_groups"][0] == [0, 1, 2]

    def test_realistic_mixed_document(self):
        """Simulate a real document with multiple tables, some merging, some not."""
        tables = [
            # Junk: single-row disclaimer at bottom of page
            self._make_table(0, 0, [72.0, 720.0, 540.0, 740.0], 1, 1, ""),
            # Table 1 spans pages 1-2
            self._make_table(1, 1, [72.0, 100.0, 540.0, 700.0], 5, 30,
                           "Table 1: Risk Assessment Matrix",
                           ["Risk ID", "Description", "Likelihood", "Impact", "Mitigation"]),
            self._make_table(2, 2, [72.0, 50.0, 540.0, 500.0], 5, 15,
                           "Table 1 (Continued)",
                           ["Risk ID", "Description", "Likelihood", "Impact", "Mitigation"]),
            # Standalone table on page 5
            self._make_table(3, 5, [72.0, 200.0, 540.0, 600.0], 3, 8,
                           "Table 2: Budget Summary",
                           ["Category", "Amount", "Notes"]),
            # Table 3 spans pages 8-9 (schema match, no "continued")
            self._make_table(4, 8, [72.0, 100.0, 540.0, 700.0], 4, 20,
                           "Table 3: Personnel Roster",
                           ["Name", "Role", "Clearance", "Status"]),
            self._make_table(5, 9, [72.0, 50.0, 540.0, 400.0], 4, 12, "",
                           ["0", "1", "2", "3"]),
        ]
        result = merge_tables(tables)

        # Junk filtered
        assert 0 in result["junk_indices"]

        # Table 1 merged (indices 1, 2)
        merged_1_2 = [g for g in result["merged_groups"] if 1 in g and 2 in g]
        assert len(merged_1_2) == 1, f"Expected [1,2] merged, got {result['merged_groups']}"

        # Table 2 standalone (index 3)
        standalone_3 = [g for g in result["merged_groups"] if g == [3]]
        assert len(standalone_3) == 1

        # Table 3 merged (indices 4, 5)
        merged_4_5 = [g for g in result["merged_groups"] if 4 in g and 5 in g]
        assert len(merged_4_5) == 1


# ============================================================
# map_framework_controls() integration tests
#
# The mapper works by finding control ID patterns (AC-1, SC-7, etc.)
# in the text via regex, then resolving them against the catalog.
# It does NOT do semantic matching of prose to controls.
# ============================================================

class TestFrameworkMapperIntegration:
    """Test map_framework_controls with realistic NIST/CMMC-style data."""

    SAMPLE_CATALOG = [
        ("AC-1", "Access Control Policy and Procedures",
         "The organization develops, documents, and disseminates an access control policy"),
        ("AC-2", "Account Management",
         "The organization manages information system accounts"),
        ("AC-3", "Access Enforcement",
         "The information system enforces approved authorizations for logical access"),
        ("AC-7", "Unsuccessful Logon Attempts",
         "The information system enforces a limit of consecutive invalid logon attempts"),
        ("IA-2", "Identification and Authentication",
         "The information system uniquely identifies and authenticates organizational users"),
        ("SC-7", "Boundary Protection",
         "The information system monitors and controls communications at the external boundary"),
        ("CM-6", "Configuration Settings",
         "The organization establishes and documents configuration settings for IT products"),
        ("AU-2", "Audit Events",
         "The organization determines that the information system is capable of auditing events"),
        ("PE-3", "Physical Access Control",
         "The organization controls physical access to the facility"),
        ("IR-4", "Incident Handling",
         "The organization implements an incident handling capability for security incidents"),
    ]

    def test_returns_expected_structure(self):
        """map_framework_controls returns dict with results and stats."""
        chunks = [
            ("chunk_1", "Per AC-1, the system shall enforce access control policies.", True),
        ]
        result = map_framework_controls(self.SAMPLE_CATALOG, chunks)
        assert isinstance(result, dict)
        assert "results" in result
        assert "stats" in result
        assert "chunks_processed" in result["stats"]

    def test_explicit_control_id_match(self):
        """Chunks containing explicit control IDs (AC-1, SC-7) get matched."""
        chunks = [
            ("req_1", "As required by AC-1, the system shall enforce access control policy.", True),
        ]
        result = map_framework_controls(self.SAMPLE_CATALOG, chunks)
        assert result["stats"]["chunks_with_matches"] >= 1
        matched = [r for r in result["results"] if r.get("matches")]
        assert len(matched) > 0, f"Expected match on AC-1, got {result}"
        # Should resolve to AC-1
        control_ids = [m["control_id"] for r in matched for m in r["matches"]]
        assert "AC-1" in control_ids

    def test_multiple_control_ids_in_one_chunk(self):
        """Multiple control IDs in one chunk all get resolved."""
        chunks = [
            ("req_2", "This requirement addresses AC-2 and SC-7 for boundary protection.", True),
        ]
        result = map_framework_controls(self.SAMPLE_CATALOG, chunks)
        matched = [r for r in result["results"] if r.get("matches")]
        assert len(matched) > 0
        control_ids = [m["control_id"] for r in matched for m in r["matches"]]
        assert "AC-2" in control_ids
        assert "SC-7" in control_ids

    def test_no_control_ids_no_matches(self):
        """Text without control ID patterns produces no matches."""
        chunks = [
            ("req_3", "The cafeteria shall serve lunch between 11:00 and 13:00 daily.", True),
        ]
        result = map_framework_controls(self.SAMPLE_CATALOG, chunks)
        assert result["stats"]["chunks_with_matches"] == 0

    def test_non_requirement_chunks_still_processed(self):
        """Chunks with is_requirement=False still get scanned for control IDs."""
        chunks = [
            ("para_1", "Reference: AC-3 governs access enforcement.", False),
        ]
        result = map_framework_controls(self.SAMPLE_CATALOG, chunks)
        # The mapper scans all chunks for control IDs regardless of is_requirement
        assert result["stats"]["chunks_processed"] == 1

    def test_batch_processing(self):
        """Process a batch of chunks with mixed control ID presence."""
        chunks = [
            ("req_1", "Per AC-3, enforce approved authorizations for logical access.", True),
            ("req_2", "Limit invalid logon attempts per AC-7.", True),
            ("para_1", "This section describes the system architecture.", False),
            ("req_3", "Implement IR-4 incident handling procedures.", True),
            ("req_4", "Boundary protection per SC-7 shall be monitored.", True),
        ]
        result = map_framework_controls(self.SAMPLE_CATALOG, chunks)
        # 4 chunks have control IDs, 1 doesn't
        assert result["stats"]["chunks_with_candidates"] >= 4
        assert result["stats"]["chunks_with_matches"] >= 4

    def test_stats_chunks_processed(self):
        """Stats correctly count all non-empty chunks."""
        chunks = [
            ("r1", "AC-1 compliance required.", True),
            ("r2", "CM-6 settings documented.", True),
            ("r3", "PE-3 physical access controlled.", True),
        ]
        result = map_framework_controls(self.SAMPLE_CATALOG, chunks)
        assert result["stats"]["chunks_processed"] == 3
        assert result["stats"]["chunks_with_candidates"] == 3

    def test_unknown_control_id_found_as_candidate(self):
        """Control IDs not in catalog are still detected as candidates."""
        chunks = [
            ("r1", "Per ZZ-99, do something unusual.", True),
        ]
        result = map_framework_controls(self.SAMPLE_CATALOG, chunks)
        # ZZ-99 matches the regex pattern [A-Z]{2}-\d{1,3}
        assert result["stats"]["chunks_with_candidates"] >= 1


# ============================================================
# Phase 2: classify_blocks, profile_document, etc.
# ============================================================

class TestPhase2OnRealPDFs:
    """Test Phase 2 Rust functions against real PDF fixtures."""

    @pytest.fixture
    def doc_with_text(self):
        """Use 1.pdf which has actual text content."""
        try:
            return PdfDocument("tests/fixtures/1.pdf")
        except (OSError, RuntimeError):
            pytest.skip("1.pdf fixture not available")

    @pytest.fixture
    def simple_doc(self):
        try:
            return PdfDocument("tests/fixtures/simple.pdf")
        except (OSError, RuntimeError):
            pytest.skip("simple.pdf fixture not available")

    def test_classify_blocks_returns_list(self, doc_with_text):
        """classify_blocks returns a list of block dicts."""
        result = doc_with_text.classify_blocks(0)
        assert isinstance(result, list)
        for block in result:
            assert isinstance(block, dict)
            assert "block_type" in block
            assert "bbox" in block
            assert "text" in block

    def test_classify_blocks_types_are_strings(self, doc_with_text):
        """Block types should be non-empty strings."""
        result = doc_with_text.classify_blocks(0)
        for block in result:
            bt = block["block_type"]
            assert isinstance(bt, str) and len(bt) > 0, \
                f"Invalid block type: {bt!r}"

    def test_classify_blocks_has_content(self, doc_with_text):
        """A PDF with text should produce at least one block."""
        result = doc_with_text.classify_blocks(0)
        assert len(result) > 0, "Expected at least one block from a PDF with text"

    def test_profile_document_returns_dict(self, doc_with_text):
        """profile_document returns domain/layout/complexity info."""
        result = doc_with_text.profile_document()
        assert isinstance(result, dict)
        assert "domain" in result
        assert "complexity_score" in result
        assert "page_count" in result
        assert "is_scanned" in result

    def test_profile_document_domain_is_string(self, doc_with_text):
        """Domain should be a non-empty string."""
        result = doc_with_text.profile_document()
        assert isinstance(result["domain"], str)
        assert len(result["domain"]) > 0

    def test_profile_document_complexity_is_int(self, doc_with_text):
        """Complexity score should be an integer 1-5."""
        result = doc_with_text.profile_document()
        score = result["complexity_score"]
        assert isinstance(score, int)
        assert 1 <= score <= 5

    def test_get_section_hierarchy_returns_dict(self, doc_with_text):
        """get_section_hierarchy returns dict with sections list."""
        result = doc_with_text.get_section_hierarchy()
        assert isinstance(result, dict)
        assert "sections" in result
        assert "total_sections" in result
        assert isinstance(result["sections"], list)

    def test_predict_extraction_returns_dict(self, doc_with_text):
        """predict_extraction bundles all analysis."""
        result = doc_with_text.predict_extraction()
        assert isinstance(result, dict)
        assert "recommended_strategy" in result
        assert "profile" in result
        assert "page_summaries" in result

    def test_predict_extraction_strategy_is_string(self, doc_with_text):
        """Strategy should be a known extraction strategy."""
        result = doc_with_text.predict_extraction()
        strategy = result["recommended_strategy"]
        assert isinstance(strategy, str)
        assert len(strategy) > 0

    def test_extract_spans_have_all_fields(self, doc_with_text):
        """TextSpans have text, bbox, font_name, font_size, is_bold, is_italic."""
        spans = doc_with_text.extract_spans(0)
        assert len(spans) > 0, "Expected spans from a PDF with text"
        for span in spans:
            assert hasattr(span, "text")
            assert hasattr(span, "bbox")
            assert hasattr(span, "font_name")
            assert hasattr(span, "font_size")
            assert hasattr(span, "is_bold")
            assert hasattr(span, "is_italic")
            bbox = span.bbox
            assert len(bbox) == 4
            assert all(isinstance(v, float) for v in bbox)

    def test_text_extraction_produces_content(self, doc_with_text):
        """Text extraction on a real PDF produces non-empty text."""
        text = doc_with_text.extract_text(0)
        assert isinstance(text, str)
        assert len(text.strip()) > 0, "Expected non-empty text from 1.pdf"

    def test_page_dimensions_returns_tuple(self, doc_with_text):
        """page_dimensions returns (width, height) tuple."""
        dims = doc_with_text.page_dimensions(0)
        assert isinstance(dims, tuple)
        assert len(dims) == 2
        w, h = dims
        assert w > 0
        assert h > 0
        assert 100 < w < 2000
        assert 100 < h < 2000

    def test_empty_pdf_still_works(self, simple_doc):
        """Even a minimal PDF with no text should not crash."""
        result = simple_doc.classify_blocks(0)
        assert isinstance(result, list)
        profile = simple_doc.profile_document()
        assert isinstance(profile, dict)
        prediction = simple_doc.predict_extraction()
        assert isinstance(prediction, dict)


# ============================================================
# End-to-end: extract spans -> classify -> profile
# ============================================================

class TestEndToEndPipeline:
    """Test the full pipeline flow: open PDF -> extract -> classify -> profile."""

    def test_full_pipeline_multipage(self):
        """Run complete pipeline on multi-page 1.pdf."""
        try:
            doc = PdfDocument("tests/fixtures/1.pdf")
        except (OSError, RuntimeError):
            pytest.skip("1.pdf not available")

        # Step 1: Basic metadata
        page_count = doc.page_count()
        assert page_count >= 1

        # Step 2: Extract text from all pages
        total_chars = 0
        for i in range(page_count):
            text = doc.extract_text(i)
            assert isinstance(text, str)
            total_chars += len(text)
        assert total_chars > 0, "Expected some text across all pages"

        # Step 3: Extract spans from page 0
        spans = doc.extract_spans(0)
        assert isinstance(spans, list)
        assert len(spans) > 0

        # Step 4: Classify blocks
        blocks = doc.classify_blocks(0)
        assert isinstance(blocks, list)
        assert len(blocks) > 0

        # Step 5: Profile document
        profile = doc.profile_document()
        assert isinstance(profile, dict)
        assert "domain" in profile
        assert "complexity_score" in profile

        # Step 6: Predict extraction strategy
        prediction = doc.predict_extraction()
        assert isinstance(prediction, dict)
        assert "recommended_strategy" in prediction
        assert isinstance(prediction["recommended_strategy"], str)

        # Step 7: Section hierarchy
        sections = doc.get_section_hierarchy()
        assert isinstance(sections, dict)
        assert "sections" in sections

        print(f"\nPipeline complete for 1.pdf:")
        print(f"  Pages: {page_count}")
        print(f"  Total chars: {total_chars}")
        print(f"  Spans (p0): {len(spans)}")
        print(f"  Blocks (p0): {len(blocks)}")
        print(f"  Domain: {profile['domain']}")
        print(f"  Complexity: {profile['complexity_score']}")
        print(f"  Strategy: {prediction['recommended_strategy']}")
        print(f"  Sections: {sections['total_sections']}")

    def test_merge_tables_with_extracted_data(self):
        """Simulate extracting tables from a PDF and merging them."""
        try:
            doc = PdfDocument("tests/fixtures/1.pdf")
        except (OSError, RuntimeError):
            pytest.skip("1.pdf not available")

        page_count = doc.page_count()
        if page_count < 2:
            pytest.skip("Need multi-page PDF for table merge test")

        # Simulate Camelot-extracted tables from consecutive pages
        tables = []
        for i in range(min(page_count, 3)):
            dims = doc.page_dimensions(i)
            tables.append({
                "index": i,
                "page": i,
                "bbox": [72.0, 100.0, dims[0] - 72.0, dims[1] - 100.0],
                "column_count": 4,
                "row_count": 10 + i * 5,
                "title": f"Table {i+1}" + (" (Continued)" if i > 0 else ""),
                "headers": ["Col A", "Col B", "Col C", "Col D"],
                "headers_are_numeric": False,
            })

        result = merge_tables(tables)
        assert isinstance(result, dict)
        assert len(result["merged_groups"]) >= 1
        # With "Continued" in title on consecutive pages, should merge
        if page_count >= 2:
            first_group = result["merged_groups"][0]
            assert len(first_group) >= 2, \
                f"Expected merge of continued tables, got groups: {result['merged_groups']}"

        print(f"\nTable merge on {page_count}-page PDF:")
        print(f"  Input tables: {len(tables)}")
        print(f"  Merged groups: {result['merged_groups']}")
        print(f"  Junk: {result['junk_indices']}")

    def test_framework_mapper_on_real_requirements(self):
        """Test framework mapper with realistic requirement text containing control IDs."""
        catalog = [
            ("AC-2", "Account Management", "Manage system accounts"),
            ("AC-3", "Access Enforcement", "Enforce approved authorizations"),
            ("SC-7", "Boundary Protection", "Monitor external boundary"),
            ("IA-2", "Identification and Authentication", "Identify and authenticate users"),
            ("CM-6", "Configuration Settings", "Document configuration settings"),
        ]

        # Realistic requirement chunks from a defense document
        chunks = [
            ("3.1.1", "The system shall implement AC-2 account management controls including provisioning, deprovisioning, and periodic review of all system accounts.", True),
            ("3.1.2", "Access enforcement per AC-3 shall ensure only authorized users can access classified information.", True),
            ("3.2.1", "The system shall implement SC-7 boundary protection mechanisms including firewalls and intrusion detection.", True),
            ("3.2.2", "Multi-factor authentication per IA-2 is required for all privileged users.", True),
            ("3.3.1", "This paragraph describes the overall system architecture and does not reference any specific controls.", False),
            ("3.3.2", "Configuration management per CM-6 shall ensure all baselines are documented.", True),
        ]

        result = map_framework_controls(catalog, chunks)

        # 5 chunks have control IDs
        assert result["stats"]["chunks_with_candidates"] >= 5
        assert result["stats"]["chunks_with_matches"] >= 5

        # Verify specific control IDs were resolved
        all_control_ids = set()
        for r in result["results"]:
            for m in r.get("matches", []):
                all_control_ids.add(m["control_id"])

        assert "AC-2" in all_control_ids
        assert "AC-3" in all_control_ids
        assert "SC-7" in all_control_ids
        assert "IA-2" in all_control_ids
        assert "CM-6" in all_control_ids

        print(f"\nFramework mapper results:")
        print(f"  Chunks processed: {result['stats']['chunks_processed']}")
        print(f"  With candidates: {result['stats']['chunks_with_candidates']}")
        print(f"  With matches: {result['stats']['chunks_with_matches']}")
        print(f"  Control IDs found: {sorted(all_control_ids)}")
