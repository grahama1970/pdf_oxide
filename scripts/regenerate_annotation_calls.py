#!/usr/bin/env python3
"""Regenerate the four adjudication annotation calls from pinned extraction data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_oxide.annotation_call import write_annotation_call
from pdf_oxide.pipeline import extract_pdf
from pdf_oxide.pipeline_types import PipelineConfig


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = Path(
    "/home/graham/workspace/experiments/pdf_oxide-gs001/artifacts/pdf-lab/annotation-calls"
)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts" / "pdf-lab" / "annotation-calls"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--documents", nargs="*", default=None)
    args = parser.parse_args()

    regenerated = []
    for source_call_path in sorted(args.source_root.glob("*/annotation_call.json")):
        document = source_call_path.parent.name
        if args.documents is not None and document not in args.documents:
            continue
        extracted_path = source_call_path.with_name("extracted.json")
        source_call = json.loads(source_call_path.read_text(encoding="utf-8"))
        extracted = json.loads(extracted_path.read_text(encoding="utf-8"))
        source_pdf = str(extracted["source_pdf"])
        result = extract_pdf(
            source_pdf,
            PipelineConfig(
                features=[],
                sync_to_arango=False,
                render_page_images=False,
            ),
        )
        extra_items = [
            item for item in source_call["items"] if item.get("reason") != "low_confidence"
        ]
        output_path = args.output_root / document / "annotation_call.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_annotation_call(result, output_path, extra_items=extra_items)
        regenerated.append(
            {
                "document": document,
                "output": str(output_path),
                "items": len(json.loads(output_path.read_text(encoding="utf-8"))["items"]),
            }
        )

    print(json.dumps({"regenerated": regenerated}, indent=2))


if __name__ == "__main__":
    main()
