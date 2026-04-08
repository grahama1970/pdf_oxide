"""Tests for the 3 additive clone features: error injection, visual coherence, figure generation."""
import os
import tempfile

import pytest

NIST_PDF = "/mnt/storage12tb/extractor_corpus/nist/nist_sp_800_171.pdf"


class TestInjectErrors:
    def test_reproducible(self):
        from pdf_oxide.clone_pdf import inject_errors
        code = "c.drawString(72, 700, 'The system shall provide effective protection.')"
        r1 = inject_errors(code, seed=42, error_rate=0.5)
        r2 = inject_errors(code, seed=42, error_rate=0.5)
        assert r1 == r2

    def test_different_seed(self):
        from pdf_oxide.clone_pdf import inject_errors
        code = "c.drawString(72, 700, 'The official finding shall confirm effective filtering of all classified traffic data.')"
        r1 = inject_errors(code, seed=42, error_rate=0.8)
        r2 = inject_errors(code, seed=99, error_rate=0.8)
        assert r1 != r2

    def test_preserves_code_structure(self):
        from pdf_oxide.clone_pdf import inject_errors
        code = "c.drawString(72, 700, 'Security officer shall review findings.')\nc.save()"
        result = inject_errors(code, seed=42, error_rate=0.5)
        assert "drawString" in result
        assert "c.save()" in result

    def test_has_unicode(self):
        from pdf_oxide.clone_pdf import inject_errors
        code = "c.drawString(72, 700, 'The official finding shall confirm effective filtering of all traffic.')"
        result = inject_errors(code, seed=0, error_rate=1.0)
        has_non_ascii = any(ord(c) > 127 for c in result)
        assert has_non_ascii, f"High error rate should introduce non-ASCII chars. Got: {repr(result)}"

    def test_track_returns_manifest(self):
        from pdf_oxide.clone_pdf import inject_errors
        code = "c.drawString(72, 700, 'The official finding shall confirm effective filtering of all traffic.')"
        result, manifest = inject_errors(code, seed=0, error_rate=1.0, track=True)
        assert isinstance(manifest, list)
        assert len(manifest) > 0
        entry = manifest[0]
        assert "id" in entry
        assert entry["id"].startswith("ERR_")
        assert "type" in entry
        assert "original" in entry
        assert "corrupted" in entry
        assert "char_offset_in_string" in entry
        assert "code_offset" in entry
        assert entry["original"] != entry["corrupted"]

    def test_track_false_returns_string(self):
        from pdf_oxide.clone_pdf import inject_errors
        code = "c.drawString(72, 700, 'The official finding shall confirm effective filtering.')"
        result = inject_errors(code, seed=0, error_rate=0.5)
        assert isinstance(result, str)

    def test_manifest_ids_unique(self):
        from pdf_oxide.clone_pdf import inject_errors
        code = "c.drawString(72, 700, 'The official finding shall confirm effective filtering of all traffic and data.')"
        _, manifest = inject_errors(code, seed=0, error_rate=1.0, track=True)
        ids = [e["id"] for e in manifest]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {ids}"


class TestVisualSimilarity:
    @pytest.mark.skipif(not os.path.exists(NIST_PDF), reason="NIST PDF not available")
    def test_identical_pdfs(self):
        from pdf_oxide.clone_scorer import visual_similarity
        score = visual_similarity(NIST_PDF, NIST_PDF)
        assert score > 0.95

    def test_missing_pdf(self):
        from pdf_oxide.clone_scorer import visual_similarity
        score = visual_similarity("/nonexistent.pdf", "/also_nonexistent.pdf")
        assert score == 0.0

    @pytest.mark.skipif(not os.path.exists(NIST_PDF), reason="NIST PDF not available")
    def test_score_clone_with_visual(self):
        from pdf_oxide.clone_scorer import score_clone
        result = score_clone(NIST_PDF, NIST_PDF, visual=True)
        assert "visual_similarity" in result
        assert result["visual_similarity"] > 0.9

    def test_score_clone_without_visual(self):
        from pdf_oxide.clone_scorer import score_clone
        result = score_clone(NIST_PDF, NIST_PDF, visual=False)
        assert "visual_similarity" not in result


class TestGenerateFigure:
    def test_generic(self):
        from pdf_oxide.clone_pdf import generate_figure
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            result = generate_figure("generic", "defense", path, seed=42)
            assert os.path.exists(result)
            from PIL import Image
            img = Image.open(result)
            assert img.width == 400
            assert img.height == 300
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_fallback_on_bad_skill(self):
        from pdf_oxide.clone_pdf import generate_figure
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = f.name
        try:
            result = generate_figure("chart", "defense", path, seed=7)
            assert os.path.exists(result)
        finally:
            if os.path.exists(path):
                os.unlink(path)


class TestFillerAndStitch:
    def test_generate_filler_page(self):
        from pdf_oxide.clone_pdf import generate_filler_page
        import pdf_oxide
        profile = {
            "domain": "government", "page_count": 3,
            "page_signatures": [{"page_num": i, "table_candidate": False,
                                  "figure_candidate": False, "equation_candidate": False} for i in range(3)],
            "requirements_pages": [], "running_headers": [{"text": "Test"}],
        }
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = f.name
        try:
            result = generate_filler_page(0, profile, path, seed=42)
            assert os.path.exists(result)
            doc = pdf_oxide.PdfDocument(result)
            assert doc.page_count() >= 1
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_stitch_all_filler(self):
        from pdf_oxide.clone_pdf import stitch_pages
        import pdf_oxide
        profile = {
            "domain": "government", "page_count": 3,
            "page_signatures": [{"page_num": i, "table_candidate": False,
                                  "figure_candidate": False, "equation_candidate": False} for i in range(3)],
            "requirements_pages": [], "running_headers": [{"text": "Test"}],
        }
        out_dir = tempfile.mkdtemp(prefix="stitch_test_")
        try:
            result = stitch_pages(NIST_PDF, out_dir, [], profile, seed=42)
            assert os.path.exists(result)
            doc = pdf_oxide.PdfDocument(result)
            assert doc.page_count() == 3
        finally:
            import shutil
            shutil.rmtree(out_dir, ignore_errors=True)

    def test_stitch_with_synthetic(self):
        from pdf_oxide.clone_pdf import stitch_pages
        import pdf_oxide
        # Simulate 1 passing window on page 0
        profile = {
            "domain": "government", "page_count": 3,
            "page_signatures": [{"page_num": i, "table_candidate": False,
                                  "figure_candidate": False, "equation_candidate": False} for i in range(3)],
            "requirements_pages": [], "running_headers": [],
        }
        out_dir = tempfile.mkdtemp(prefix="stitch_synth_")
        try:
            # Create a fake synthetic PDF (just use page 0 of NIST)
            from pypdf import PdfReader, PdfWriter
            reader = PdfReader(NIST_PDF)
            writer = PdfWriter()
            writer.add_page(reader.pages[0])
            synth_path = os.path.join(out_dir, "synthetic.pdf")
            with open(synth_path, "wb") as f:
                writer.write(f)
            clone_results = [{"status": "pass", "source_pages": [0], "synthetic_pdf": synth_path}]
            result = stitch_pages(NIST_PDF, out_dir, clone_results, profile, seed=42)
            assert os.path.exists(result)
            doc = pdf_oxide.PdfDocument(result)
            assert doc.page_count() == 3  # 1 synthetic + 2 filler
        finally:
            import shutil
            shutil.rmtree(out_dir, ignore_errors=True)


class TestStructuralQIDs:
    def test_build_structural_qid_map(self):
        from pdf_oxide.clone_additive import build_structural_qid_map
        brief = {
            "tables": [{"page": 5, "rows": 10, "cols": 3}],
            "toc_section": "3.1 Access Control",
            "toc_parent": "3 Security Requirements",
            "running_header": {"text": "NIST SP 800-171"},
            "running_footer": {"text": "Page 5"},
            "spanning_table": None,
        }
        entries = build_structural_qid_map(brief, [5])
        types = [e["element_type"] for e in entries]
        assert "table" in types
        assert "heading" in types
        assert "running_header" in types
        assert "running_footer" in types
        # All QIDs should be in the 50000+ range (page 5 * 10000 + offset)
        for e in entries:
            assert 50001 <= e["qid"] < 55000

    def test_inject_structural_qids_heading(self):
        from pdf_oxide.clone_additive import inject_structural_qids, encode_qid, find_all_qids
        code = "c.drawString(72, 700, '3.1 Access Control')\nc.drawString(72, 680, 'Some body text here.')"
        entries = [{
            "qid": 50002, "element_type": "heading",
            "label": "3.1 Access Control", "detail": {},
        }]
        result_code, result_entries = inject_structural_qids(code, entries)
        assert encode_qid(50002) in result_code
        assert result_entries[0]["injected"] is True

    def test_inject_structural_qids_table(self):
        from pdf_oxide.clone_additive import inject_structural_qids, find_all_qids
        code = "c.drawString(72, 700, 'Control ID')\nc.drawString(200, 700, 'Description')"
        entries = [{
            "qid": 50001, "element_type": "table",
            "label": None, "detail": {"rows": 10, "cols": 3},
        }]
        result_code, result_entries = inject_structural_qids(code, entries)
        assert result_entries[0]["injected"] is True
        assert result_entries[0]["label"] is not None  # should have picked up cell text

    def test_structural_qids_no_match(self):
        from pdf_oxide.clone_additive import inject_structural_qids
        code = "c.drawString(72, 700, 'Hello World')"
        entries = [{
            "qid": 50002, "element_type": "heading",
            "label": "3.1 Access Control", "detail": {},
        }]
        result_code, result_entries = inject_structural_qids(code, entries)
        assert result_entries[0]["injected"] is False

    def test_corruption_qid_offset(self):
        from pdf_oxide.clone_additive import _CORRUPTION_QID_OFFSET, _STRUCTURAL_QID_OFFSET, _QID_PAGE_MULTIPLIER
        # Structural QIDs start at 1, corruption QIDs start at 5000
        # Up to 4999 structural elements per page, 5000 corruptions
        assert _STRUCTURAL_QID_OFFSET == 1
        assert _CORRUPTION_QID_OFFSET == 5000
        assert _QID_PAGE_MULTIPLIER == 10000
        assert _CORRUPTION_QID_OFFSET > _STRUCTURAL_QID_OFFSET


class TestInsertFigures:
    def test_replaces_rect(self):
        from pdf_oxide.clone_pdf import insert_figures
        code = "c.rect(72, 300, 468, 250, fill=1)"
        result = insert_figures(code, ["/tmp/fig.png"])
        assert "drawImage" in result
        assert "fill=1" not in result

    def test_no_figures(self):
        from pdf_oxide.clone_pdf import insert_figures
        code = "c.drawString(72, 700, 'Hello')"
        result = insert_figures(code, [])
        assert result == code
