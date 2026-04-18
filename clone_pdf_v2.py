#!/usr/bin/env python3
"""Unified CLI for refactored PDF cloning pipeline.

Pipeline:
1. If --source: run clone_profiler to get TOC/tables/signatures
2. Call manifest_generator.generate_manifest() (Opus)
3. Save manifest to JSON (for debugging/reuse)
4. Build SectionBudgets from manifest with element_sequences
5. Call make_llm_content_generator_batch() (sonnet)
6. Build PDF via CloneBuilder
7. Save TruthManifest for validation

Usage:
    # Default: 32 curated sections (covers all structural patterns)
    python clone_pdf_v2.py --source NIST_SP_800-53r5.pdf --output clone.pdf

    # Full PDF (all sections)
    python clone_pdf_v2.py --source NIST_SP_800-53r5.pdf -n 0 --output clone.pdf
    python clone_pdf_v2.py --source NIST_SP_800-53r5.pdf -n all --output clone.pdf

    # Custom section count
    python clone_pdf_v2.py --source NIST_SP_800-53r5.pdf -n 50 --output clone.pdf
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "python")

from pdf_oxide.clone.clone_builder import CloneBuilder
from pdf_oxide.clone.clone_types import RenderPlan, derive_render_plan, SourceProfileRef
from pdf_oxide.clone.manifest_generator import (
    generate_manifest,
    manifest_to_section_budgets,
    validate_preset_coverage,
    DEFAULT_PRESETS,
)
from pdf_oxide.clone.sampler_content import make_llm_content_generator_batch
from pdf_oxide.clone.style_extractor import extract_style_profile, get_default_style_profile


async def run_pipeline(
    source_path: str = None,
    manifest_path: str = None,
    output_path: str = "/tmp/clone_v2.pdf",
    model: str = "text",
    opus_model: str = "opus",
    seed: int = 42,
    max_sections: int = 32,
    skip_figures: bool = True,
    domain: str = "government security",
    extract_style: bool = False,
    discrepancy_path: str = None,
    calibration_mode: bool = False,
):
    """Run the PDF cloning pipeline.

    Args:
        discrepancy_path: Path to ExtractionDiscrepancy JSON for calibration feedback.
            When provided, the cloner prioritizes patterns that failed extraction.
        calibration_mode: When True with discrepancy, generates targeted fixtures
            for testing specific extraction failures (control IDs, tables, etc.)
    """

    # Load discrepancy data if provided
    discrepancy_data = None
    if discrepancy_path:
        print(f"Loading discrepancy data from {discrepancy_path}...")
        discrepancy_data = json.loads(Path(discrepancy_path).read_text())
        print(f"  Discrepancy types: {discrepancy_data.get('discrepancy_types', [])}")
        print(f"  Control detection rate: {discrepancy_data.get('control_detection_rate', 'N/A')}")

    # Step 1: Load or generate manifest
    if manifest_path:
        print(f"Loading manifest from {manifest_path}...")
        manifest_data = json.loads(Path(manifest_path).read_text())
        toc = manifest_data.get("toc", [])
        table_shapes = manifest_data.get("table_shapes", [])
        page_signatures = manifest_data.get("page_signatures", [])
    elif source_path:
        print(f"Profiling source PDF: {source_path}...")
        from pdf_oxide.clone_profiler import profile_for_cloning
        profile = profile_for_cloning(source_path)
        toc = profile.get("toc_sections", [])
        table_shapes = profile.get("table_shapes", [])
        page_signatures = profile.get("page_signatures", [])
    else:
        print("Error: Either --source or --manifest is required")
        sys.exit(1)

    print(f"TOC entries: {len(toc)}")
    print(f"Table shapes: {len(table_shapes)}")

    # Step 1.5: Extract style profile from source PDF (optional)
    style_profile = None
    style_profile_dict = None
    if extract_style and source_path:
        print(f"\nExtracting style profile from source PDF...")
        profiler_data = {
            "toc_sections": toc,
            "table_shapes": table_shapes,
            "page_signatures": page_signatures,
        }
        try:
            style_profile = await extract_style_profile(
                source_path,
                profiler_data,
                model=opus_model,
            )
            style_profile_dict = style_profile.to_dict()
            # Save style profile for debugging
            style_path = Path(output_path).with_suffix(".style.json")
            style_path.write_text(json.dumps(style_profile_dict, indent=2))
            print(f"  Saved style profile: {style_path}")
        except Exception as e:
            print(f"  Style extraction failed, using defaults: {e}")
            style_profile = get_default_style_profile()
            style_profile_dict = style_profile.to_dict()

    # Step 2: Generate enhanced manifest via Opus
    print(f"\nGenerating manifest via {opus_model}...")
    manifest = await generate_manifest(
        toc=toc,
        table_shapes=table_shapes,
        page_signatures=page_signatures,
        presets=DEFAULT_PRESETS,
        model=opus_model,
        style_profile=style_profile_dict,
    )

    # Validate presets
    missing = validate_preset_coverage(manifest, DEFAULT_PRESETS)
    if missing:
        print(f"  Warning: Missing presets: {missing}")

    # Save manifest
    manifest_out = Path(output_path).with_suffix(".manifest.json")
    manifest_out.write_text(json.dumps(manifest, indent=2))
    print(f"  Saved manifest: {manifest_out}")

    # Step 3: Convert to SectionBudgets
    budgets = manifest_to_section_budgets(manifest, domain=domain)
    total_available = len(budgets)

    # Step 3.5: Calibration-aware section prioritization
    if calibration_mode and discrepancy_data:
        import re
        control_re = re.compile(r"^[A-Z]{2}-\d+")
        discrepancy_types = discrepancy_data.get("discrepancy_types", [])

        # If control IDs weren't detected, prioritize control sections
        if "control_id_miss" in discrepancy_types:
            print("\n  Calibration: Prioritizing control ID sections...")
            control_sections = [b for b in budgets if control_re.match(b.title.split()[0])]
            other_sections = [b for b in budgets if not control_re.match(b.title.split()[0])]
            # Put control sections first to ensure they're included
            budgets = control_sections + other_sections
            print(f"    {len(control_sections)} control sections prioritized")

        # If tables over-detected, ensure we include sections with real tables
        if "table_over_detect" in discrepancy_types or "table_empty" in discrepancy_types:
            print("  Calibration: Ensuring table sections have data tables...")
            table_sections = [b for b in budgets if b.table_count > 0]
            print(f"    {len(table_sections)} sections with tables")

    if max_sections and max_sections > 0:
        budgets = budgets[:max_sections]
    print(f"\nSection budgets: {len(budgets)} (of {total_available} available)")

    # Step 4: Generate content via LLM
    print(f"\nGenerating content via {model}...")
    content_gen = await make_llm_content_generator_batch(
        budgets=budgets,
        domain=domain,
        model=model,
        seed=seed,
    )

    # Step 5: Build RenderPlan
    print("\nBuilding render plan...")
    profile_ref = SourceProfileRef({
        "doc_id": Path(source_path or manifest_path).stem,
        "path": source_path or manifest_path,
        "page_count": max((s.get("page", 0) for s in toc), default=10) + 10,
        "domain": domain.split()[0] if domain else "general",
        "layout_mode": "single_column",
        "toc_sections": toc,
        "table_shapes": table_shapes,
        "page_signatures": page_signatures,
    })

    plan = derive_render_plan(profile_ref, seed=seed)

    # Update plan with budgets
    plan.section_budgets = budgets

    # Step 6: Build PDF
    print(f"\nBuilding PDF: {output_path}...")
    # Use style profile presets if available, otherwise use defaults
    header_preset = "doc_title_header"
    footer_preset = "page_number_footer"
    table_preset = "data_grid"
    if style_profile_dict:
        header_preset = style_profile_dict.get("header_preset", header_preset)
        footer_preset = style_profile_dict.get("footer_preset", footer_preset)
        table_preset = style_profile_dict.get("table_preset", table_preset)
        print(f"  Using style profile: header={header_preset}, footer={footer_preset}, table={table_preset}")

    builder = CloneBuilder(
        plan,
        header_preset=header_preset,
        footer_preset=footer_preset,
        table_preset=table_preset,
    )
    manifest_result = builder.build(
        output_path=output_path,
        content_generator=content_gen,
    )

    # Step 7: Save truth manifest
    truth_path = Path(output_path).with_suffix(".truth.json")
    manifest_result.save(str(truth_path))
    print(f"  Saved truth manifest: {truth_path}")

    print(f"\n=== Pipeline Complete ===")
    print(f"  Output PDF: {output_path}")
    print(f"  Sections: {len(budgets)}")
    print(f"  QIDs allocated: {manifest_result.total_qids}")
    print(f"  Tables: {manifest_result.total_tables}")


def main():
    parser = argparse.ArgumentParser(
        description="Unified PDF cloning CLI - Opus manifest + LLM content + presets"
    )
    parser.add_argument(
        "--source", "-s",
        help="Source PDF path to clone"
    )
    parser.add_argument(
        "--manifest", "-m",
        help="Pre-generated manifest JSON (skip Opus call)"
    )
    parser.add_argument(
        "--output", "-o",
        default="/tmp/clone_v2.pdf",
        help="Output PDF path"
    )
    parser.add_argument(
        "--model",
        default="text",
        help="LLM model for content generation (default: text, uses dynamic Chutes routing)"
    )
    parser.add_argument(
        "--opus-model",
        default="opus",
        help="Model for manifest generation (default: opus)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for determinism"
    )
    def parse_max_sections(value):
        """Parse max-sections: int, 0, or 'all' (all = 0 = unlimited)."""
        if value.lower() == "all":
            return 0
        return int(value)

    parser.add_argument(
        "--max-sections", "-n",
        type=parse_max_sections,
        default=32,
        help="Max sections to clone (default: 32 curated). Use 0 or 'all' for entire PDF"
    )
    parser.add_argument(
        "--skip-figures",
        action="store_true",
        default=True,
        help="Skip /create-figure calls, use placeholders (default)"
    )
    parser.add_argument(
        "--generate-figures",
        action="store_true",
        help="Generate figures via /create-figure skill"
    )
    parser.add_argument(
        "--domain",
        default="government security",
        help="Content domain for LLM generation"
    )
    parser.add_argument(
        "--extract-style",
        action="store_true",
        help="Extract visual style profile from source PDF using Opus VLM"
    )
    parser.add_argument(
        "--discrepancy",
        help="Path to ExtractionDiscrepancy JSON for calibration-aware fixture generation"
    )
    parser.add_argument(
        "--calibration-mode",
        action="store_true",
        help="Generate calibration fixture targeting detected extraction failures"
    )

    args = parser.parse_args()

    if not args.source and not args.manifest:
        parser.print_help()
        print("\nError: Either --source or --manifest is required")
        sys.exit(1)

    skip_figures = not args.generate_figures

    asyncio.run(run_pipeline(
        source_path=args.source,
        manifest_path=args.manifest,
        output_path=args.output,
        model=args.model,
        opus_model=args.opus_model,
        seed=args.seed,
        max_sections=args.max_sections,
        skip_figures=skip_figures,
        domain=args.domain,
        extract_style=args.extract_style,
        discrepancy_path=args.discrepancy,
        calibration_mode=args.calibration_mode,
    ))


if __name__ == "__main__":
    main()
