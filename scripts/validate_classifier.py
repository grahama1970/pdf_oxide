#!/usr/bin/env python3
"""Validate the Tier 0.5 header-verdict classifier on ~100 corpus PDFs.

For each PDF, runs classify_blocks() to get Tier 0 dispositions, then
runs the trained RandomForest classifier on Escalate cases to verify
it resolves them with high confidence.

Usage:
    .venv/bin/python scripts/validate_classifier.py
"""

import json
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np

# Ensure pdf_oxide is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pdf_oxide

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CORPUS_ROOT = Path("/mnt/storage12tb/extractor_corpus")
MODEL_PATH = Path.home() / ".pi" / "models" / "classifiers" / "header_verdict.joblib"
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "artifacts" / "classifier_validation.json"

SAMPLE_SPEC = {
    "arxiv": 30,
    "defense": 25,
    "nasa": 15,
    "nist": 10,
    "engineering": 10,
    "adversarial": 10,
}

MAX_PAGES_PER_PDF = 50  # match wiring.py cap
CONFIDENCE_THRESHOLD = 0.85

FEATURE_ORDER = [
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


def sample_pdfs() -> dict[str, list[Path]]:
    """Sample PDFs from each corpus category."""
    sampled = {}
    for category, n in SAMPLE_SPEC.items():
        cat_dir = CORPUS_ROOT / category
        if not cat_dir.exists():
            print(f"  WARN: {cat_dir} not found, skipping")
            continue
        pdfs = sorted(cat_dir.glob("*.pdf"))
        if len(pdfs) == 0:
            print(f"  WARN: no PDFs in {cat_dir}")
            continue
        k = min(n, len(pdfs))
        sampled[category] = random.sample(pdfs, k)
        print(f"  {category}: {k}/{len(pdfs)} PDFs sampled")
    return sampled


def run_validation():
    """Main validation loop."""
    random.seed(42)

    print("=" * 60)
    print("Header-Verdict Classifier Validation")
    print("=" * 60)

    # Load classifier
    if not MODEL_PATH.exists():
        print(f"ERROR: Model not found at {MODEL_PATH}")
        sys.exit(1)
    clf = joblib.load(MODEL_PATH)
    classes = list(clf.classes_)
    print(f"Loaded classifier: {type(clf).__name__}, classes={classes}")

    # Sample PDFs
    print("\nSampling PDFs...")
    sampled = sample_pdfs()
    total_pdfs = sum(len(v) for v in sampled.values())
    print(f"Total: {total_pdfs} PDFs\n")

    # Counters
    tier0_counts = defaultdict(int)  # Accept/Reject/Escalate from Rust
    clf_resolved = 0  # Escalate cases resolved by classifier
    clf_unresolved = 0  # Escalate cases still ambiguous after classifier
    clf_confidences = []  # confidence on resolved cases
    clf_predictions = defaultdict(int)  # Accept/Reject counts from classifier
    all_confidences = []  # all classifier confidences
    per_category = {}
    crashes = []
    pdf_results = []

    for category, pdf_paths in sampled.items():
        cat_stats = {
            "pdfs": len(pdf_paths),
            "total_blocks": 0,
            "accept": 0,
            "reject": 0,
            "escalate": 0,
            "clf_resolved": 0,
            "clf_unresolved": 0,
            "errors": 0,
        }

        for pdf_path in pdf_paths:
            t0 = time.time()
            pdf_result = {
                "path": str(pdf_path),
                "category": category,
                "status": "ok",
                "total_blocks": 0,
                "accept": 0,
                "reject": 0,
                "escalate": 0,
                "clf_resolved": 0,
                "clf_confidences": [],
            }
            try:
                doc = pdf_oxide.open(str(pdf_path))
                page_count = doc.page_count()
                pages_to_scan = min(page_count, MAX_PAGES_PER_PDF)

                for page_idx in range(pages_to_scan):
                    try:
                        blocks = doc.classify_blocks(page_idx)
                    except Exception:
                        continue

                    for block in blocks:
                        hv = block.get("header_validation")
                        if hv is None:
                            continue

                        disposition = hv.get("disposition", "Reject")
                        features = hv.get("features", {})
                        pdf_result["total_blocks"] += 1
                        tier0_counts[disposition] += 1
                        cat_stats["total_blocks"] += 1

                        if disposition == "Accept":
                            pdf_result["accept"] += 1
                            cat_stats["accept"] += 1
                        elif disposition == "Reject":
                            pdf_result["reject"] += 1
                            cat_stats["reject"] += 1
                        elif disposition == "Escalate":
                            pdf_result["escalate"] += 1
                            cat_stats["escalate"] += 1

                            # Run classifier
                            feature_vec = np.array(
                                [[float(features.get(f, 0.0)) for f in FEATURE_ORDER]]
                            )
                            pred = clf.predict(feature_vec)[0]
                            proba = clf.predict_proba(feature_vec)[0]
                            max_prob = float(max(proba))

                            all_confidences.append(max_prob)

                            if max_prob >= CONFIDENCE_THRESHOLD:
                                clf_resolved += 1
                                cat_stats["clf_resolved"] += 1
                                pdf_result["clf_resolved"] += 1
                                clf_predictions[str(pred)] += 1
                                clf_confidences.append(max_prob)
                                pdf_result["clf_confidences"].append(max_prob)
                            else:
                                clf_unresolved += 1
                                cat_stats["clf_unresolved"] += 1

            except Exception as e:
                pdf_result["status"] = f"error: {e}"
                cat_stats["errors"] += 1
                crashes.append({"path": str(pdf_path), "error": str(e)})

            elapsed = time.time() - t0
            pdf_result["elapsed_s"] = round(elapsed, 3)
            pdf_results.append(pdf_result)

        per_category[category] = cat_stats

    # Compute summary
    total_blocks = sum(tier0_counts.values())
    total_escalate = tier0_counts.get("Escalate", 0)
    resolution_rate = clf_resolved / total_escalate if total_escalate > 0 else 0.0
    mean_confidence = float(np.mean(clf_confidences)) if clf_confidences else 0.0
    median_confidence = float(np.median(clf_confidences)) if clf_confidences else 0.0
    mean_all_confidence = float(np.mean(all_confidences)) if all_confidences else 0.0

    # Confidence distribution buckets
    conf_buckets = {"0.50-0.70": 0, "0.70-0.85": 0, "0.85-0.95": 0, "0.95-1.00": 0}
    for c in all_confidences:
        if c < 0.70:
            conf_buckets["0.50-0.70"] += 1
        elif c < 0.85:
            conf_buckets["0.70-0.85"] += 1
        elif c < 0.95:
            conf_buckets["0.85-0.95"] += 1
        else:
            conf_buckets["0.95-1.00"] += 1

    summary = {
        "total_pdfs": total_pdfs,
        "total_crashes": len(crashes),
        "total_blocks_with_header_validation": total_blocks,
        "tier0_accept": tier0_counts.get("Accept", 0),
        "tier0_reject": tier0_counts.get("Reject", 0),
        "tier0_escalate": total_escalate,
        "classifier_resolved": clf_resolved,
        "classifier_unresolved": clf_unresolved,
        "resolution_rate": round(resolution_rate, 4),
        "classifier_predictions": dict(clf_predictions),
        "mean_confidence_resolved": round(mean_confidence, 4),
        "median_confidence_resolved": round(median_confidence, 4),
        "mean_confidence_all_escalate": round(mean_all_confidence, 4),
        "confidence_distribution": conf_buckets,
        "per_category": per_category,
    }

    # Print report
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"PDFs processed:        {total_pdfs}")
    print(f"Crashes:               {len(crashes)}")
    print(f"Total header blocks:   {total_blocks}")
    print(f"  Tier 0 Accept:       {tier0_counts.get('Accept', 0)}")
    print(f"  Tier 0 Reject:       {tier0_counts.get('Reject', 0)}")
    print(f"  Tier 0 Escalate:     {total_escalate}")
    print()
    print(f"Classifier resolved:   {clf_resolved}/{total_escalate} ({resolution_rate:.1%})")
    print(f"Classifier unresolved: {clf_unresolved}")
    print(f"  -> Accept:           {clf_predictions.get('Accept', 0)}")
    print(f"  -> Reject:           {clf_predictions.get('Reject', 0)}")
    print(f"  -> Escalate:         {clf_predictions.get('Escalate', 0)}")
    print()
    print(f"Mean confidence (resolved):  {mean_confidence:.4f}")
    print(f"Median confidence (resolved):{median_confidence:.4f}")
    print(f"Mean confidence (all esc.):  {mean_all_confidence:.4f}")
    print()
    print("Confidence distribution (all Escalate cases):")
    for bucket, count in conf_buckets.items():
        pct = count / len(all_confidences) * 100 if all_confidences else 0
        print(f"  {bucket}: {count} ({pct:.1f}%)")
    print()
    print("Per-category breakdown:")
    for cat, stats in per_category.items():
        esc = stats["escalate"]
        res = stats["clf_resolved"]
        rate = res / esc if esc > 0 else 1.0
        print(f"  {cat:15s}: {stats['total_blocks']:5d} blocks, "
              f"{stats['accept']:4d} accept, {stats['reject']:4d} reject, "
              f"{esc:4d} escalate, {res:4d} resolved ({rate:.0%})")

    if crashes:
        print(f"\nCrashes ({len(crashes)}):")
        for c in crashes:
            print(f"  {c['path']}: {c['error'][:80]}")

    # Validate thresholds
    print("\n" + "=" * 60)
    print("VALIDATION CHECKS")
    print("=" * 60)
    checks_passed = 0
    checks_total = 3

    if resolution_rate > 0.90:
        print(f"  [PASS] Resolution rate {resolution_rate:.1%} > 90%")
        checks_passed += 1
    else:
        print(f"  [FAIL] Resolution rate {resolution_rate:.1%} <= 90%")

    if mean_confidence > 0.85:
        print(f"  [PASS] Mean confidence {mean_confidence:.4f} > 0.85")
        checks_passed += 1
    else:
        print(f"  [FAIL] Mean confidence {mean_confidence:.4f} <= 0.85")

    if len(crashes) == 0:
        print(f"  [PASS] No crashes")
        checks_passed += 1
    else:
        print(f"  [FAIL] {len(crashes)} crashes")

    print(f"\n{checks_passed}/{checks_total} checks passed")

    # Save results
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(
            {"summary": summary, "crashes": crashes, "pdf_results": pdf_results},
            f,
            indent=2,
            default=str,
        )
    print(f"\nResults saved to {OUTPUT_PATH}")


if __name__ == "__main__":
    run_validation()
