"""Self-contained PDF extraction pipeline.

Replaces the extractor project's S01-S14 pipeline entirely.

Core extraction is always synchronous, deterministic, and fast.
Post-extraction capabilities are opt-in plugins enabled via features=[...].

Usage:
    from pdf_oxide import extract_pdf, PipelineConfig

    # Pure extraction — no plugins, no side effects
    result = extract_pdf("document.pdf")

    # With plugins
    result = extract_pdf("document.pdf", config=PipelineConfig(
        features=["arango", "describe", "requirements"]
    ))
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from .pipeline_decrypt import maybe_decrypt
from .pipeline_extract import extract_content
from .pipeline_flatten import flatten
from .pipeline_types import PipelineConfig, PipelineResult
from .pipeline_util import log
from .plugins.base import registry
from . import plugins as _plugins  # noqa: F401 — auto-register all plugins


def extract_pdf(
    pdf_path: str,
    config: Optional[PipelineConfig] = None,
) -> PipelineResult:
    """Extract a PDF: parse + flatten + optional plugins.

    No LLM calls in core extraction. Fast. Deterministic. Debuggable.
    Plugins (arango, describe, requirements, etc.) run after core
    extraction if enabled via config.features.

    Single asyncio.run() call per best-practices-python
    async-single-asyncio-run rule.

    Args:
        pdf_path: Path to PDF file
        config: Pipeline configuration (uses defaults if None)

    Returns:
        PipelineResult with all extracted data
    """
    if config is None:
        config = PipelineConfig()

    t_total = time.monotonic()

    # Step 0: Decrypt if needed (pikepdf, temp file cleaned up automatically)
    with maybe_decrypt(pdf_path, password=config.decrypt_password) as usable_path:
        result = _extract_and_process(usable_path, config)

    result.timings["total"] = time.monotonic() - t_total
    log(f"Done in {result.timings['total']:.1f}s")

    return result


def _extract_and_process(
    pdf_path: str,
    config: PipelineConfig,
) -> PipelineResult:
    """Core extraction + flatten + plugins (called inside decrypt context)."""

    # Step 1: Core Rust extraction (always runs, sync)
    log(f"Extracting: {Path(pdf_path).name}")
    result = extract_content(pdf_path, config)
    log(
        f"Extracted: {len(result.blocks)} blocks, {len(result.tables)} tables, "
        f"{len(result.figures)} figures in {result.timings['extraction']:.1f}s"
    )

    # Step 2: Flatten into datalake_chunks format (sync)
    result.flattened = flatten(result)
    log(f"Flattened: {len(result.flattened)} chunks")

    # Step 3: Auto-enable arango plugin if sync_to_arango is set
    if config.sync_to_arango and "arango" not in config.features:
        config.features.insert(0, "arango")

    # Step 4: Run enabled plugins — single asyncio.run() for all async work
    if config.features:
        log(f"Running plugins: {config.features}")
        report = asyncio.run(
            registry.run_all(config.features, result, config)
        )
        result.metadata["plugin_report"] = report
        for name, status in report.items():
            log(f"  {name}: {status}")

    # Step 5: Write output JSON if output_dir set
    if config.output_dir:
        out_dir = Path(config.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "extracted.json"
        out_path.write_text(
            json.dumps(
                result.to_dict(), indent=2, ensure_ascii=False, default=str
            )
        )
        log(f"Output: {out_path}")

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="pdf_oxide PDF extraction pipeline"
    )
    parser.add_argument("pdf_path", type=str, help="Path to PDF file")
    parser.add_argument(
        "--output-dir", type=str, help="Output directory for JSON"
    )
    parser.add_argument(
        "--no-arango", action="store_true", help="Skip ArangoDB sync"
    )
    parser.add_argument(
        "--flavor", default="auto", help="Table extraction flavor: lattice, stream, or auto"
    )
    parser.add_argument(
        "--features",
        type=str,
        default="",
        help="Comma-separated plugin features (e.g. arango,describe)",
    )
    args = parser.parse_args()

    features = [f.strip() for f in args.features.split(",") if f.strip()]
    cfg = PipelineConfig(
        output_dir=Path(args.output_dir) if args.output_dir else None,
        sync_to_arango=not args.no_arango,
        table_flavor=args.flavor,
        features=features,
    )
    result = extract_pdf(args.pdf_path, cfg)
    print(
        f"Extracted {len(result.tables)} tables, "
        f"{len(result.figures)} figures, "
        f"{len(result.flattened)} chunks in "
        f"{result.timings.get('total', 0):.1f}s"
    )
