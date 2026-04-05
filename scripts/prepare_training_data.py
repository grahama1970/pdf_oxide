#!/usr/bin/env python3
"""Prepare training dataset from shadow JSONL for header-verdict classifier.

Reads ~/.pi/skills/extract-pdf/shadow/header-verdict.jsonl (529K entries),
extracts 18 numeric features + predicted label per entry, writes to
artifacts/header_verdict_labels.jsonl.
"""

import json
import sys
from collections import Counter
from pathlib import Path

SHADOW_PATH = Path.home() / ".pi/skills/extract-pdf/shadow/header-verdict.jsonl"
OUTPUT_PATH = Path("/home/graham/workspace/experiments/pdf_oxide/artifacts/header_verdict_labels.jsonl")

FEATURE_KEYS = [
    "text_len",
    "has_number_prefix",
    "font_size",
    "size_ratio",
    "is_bold",
    "ends_with_period",
    "ends_with_colon",
    "ends_with_other_punct",
    "has_bullet_char",
    "is_caption_pattern",
    "is_multi_sentence",
    "word_count",
    "title_case_ratio",
    "is_all_caps",
    "numbering_depth",
    "has_formal_prefix",
    "has_parentheses",
    "is_too_long",
]


def to_num(val):
    """Convert bool to int, pass through float/int."""
    if isinstance(val, bool):
        return int(val)
    return val


def main():
    if not SHADOW_PATH.exists():
        print(f"ERROR: shadow file not found: {SHADOW_PATH}", file=sys.stderr)
        sys.exit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    counts = Counter()
    row_id = 0
    skipped = 0

    with (
        SHADOW_PATH.open("r", encoding="utf-8") as fin,
        OUTPUT_PATH.open("w", encoding="utf-8") as fout,
    ):
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            features_raw = entry.get("features", {})
            predicted = entry.get("predicted")
            confidence = entry.get("confidence")

            if not features_raw or not predicted:
                skipped += 1
                continue

            try:
                features = [to_num(features_raw[k]) for k in FEATURE_KEYS]
            except KeyError as e:
                skipped += 1
                continue

            out = {
                "id": row_id,
                "features": features,
                "label": predicted,
                "confidence": confidence,
            }
            fout.write(json.dumps(out) + "\n")
            counts[predicted] += 1
            row_id += 1

            if row_id % 100_000 == 0:
                print(f"  processed {row_id:,} rows ...", flush=True)

    total = row_id
    print(f"\nDone.")
    print(f"  Total rows written : {total:,}")
    print(f"  Skipped (errors)   : {skipped:,}")
    print(f"\nClass distribution:")
    for label, count in sorted(counts.items(), key=lambda x: -x[1]):
        pct = 100.0 * count / total if total else 0
        print(f"  {label:12s}: {count:>8,}  ({pct:.1f}%)")
    print(f"\nOutput: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
