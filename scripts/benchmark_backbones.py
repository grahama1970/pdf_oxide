#!/usr/bin/env python3
"""
Benchmark classifier backbones on header-verdict training data.

Input:  artifacts/header_verdict_labels.jsonl  (529,340 rows)
Output: artifacts/benchmark_report.json
"""

import json
import time
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import f1_score, precision_recall_fscore_support
from sklearn.utils.class_weight import compute_sample_weight

REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_PATH = REPO_ROOT / "artifacts" / "header_verdict_labels.jsonl"
OUTPUT_PATH = REPO_ROOT / "artifacts" / "benchmark_report.json"

LABEL_ORDER = ["Accept", "Reject", "Escalate"]


def load_data(path: Path):
    features = []
    labels = []
    with open(path) as f:
        for line in f:
            row = json.loads(line)
            features.append(row["features"])
            labels.append(row["label"])
    X = np.array(features, dtype=np.float64)
    # Replace inf/-inf with large finite values, then clip
    X = np.where(np.isinf(X), np.sign(X) * 1e9, X)
    X = np.nan_to_num(X, nan=0.0)
    return X.astype(np.float32), np.array(labels)


def evaluate_backbone(name: str, clf, X_train, X_test, y_train, y_test, use_sample_weight=False, encode_labels=False):
    print(f"\n[{name}] Training on {len(X_train):,} samples...", flush=True)
    # sklearn 1.8 MLP + early_stopping + string labels = isnan crash; encode to int
    if encode_labels:
        label_map = {lbl: i for i, lbl in enumerate(LABEL_ORDER)}
        inv_map = {i: lbl for lbl, i in label_map.items()}
        y_tr = np.array([label_map[l] for l in y_train])
        y_te = np.array([label_map[l] for l in y_test])
    else:
        y_tr, y_te = y_train, y_test
    t0 = time.perf_counter()
    if use_sample_weight:
        sw = compute_sample_weight("balanced", y_train)
        clf.fit(X_train, y_tr, sample_weight=sw)
    else:
        clf.fit(X_train, y_tr)
    train_sec = time.perf_counter() - t0
    print(f"  Train time: {train_sec:.1f}s", flush=True)

    t1 = time.perf_counter()
    y_pred_raw = clf.predict(X_test)
    if encode_labels:
        y_pred = np.array([inv_map[int(p)] for p in y_pred_raw])
    else:
        y_pred = y_pred_raw
    infer_sec = time.perf_counter() - t1
    latency_ms = infer_sec * 1000

    macro_f1 = f1_score(y_test, y_pred, average="macro")
    prec, rec, f1s, _ = precision_recall_fscore_support(
        y_test, y_pred, labels=LABEL_ORDER, zero_division=0
    )

    per_class = {}
    for i, cls in enumerate(LABEL_ORDER):
        per_class[cls] = {
            "precision": round(float(prec[i]), 4),
            "recall": round(float(rec[i]), 4),
            "f1": round(float(f1s[i]), 4),
        }

    print(f"  Macro-F1: {macro_f1:.4f}  |  Latency: {latency_ms:.1f}ms", flush=True)
    for cls in LABEL_ORDER:
        p = per_class[cls]
        print(f"    {cls:10s}: P={p['precision']:.3f}  R={p['recall']:.3f}  F1={p['f1']:.3f}")

    return {
        "macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "latency_ms": round(latency_ms, 1),
        "train_sec": round(train_sec, 1),
    }


def main():
    print(f"Loading data from {INPUT_PATH} ...", flush=True)
    X, y = load_data(INPUT_PATH)
    print(f"Loaded {len(X):,} rows, {X.shape[1]} features", flush=True)

    classes, counts = np.unique(y, return_counts=True)
    for cls, cnt in zip(classes, counts):
        print(f"  {cls}: {cnt:,} ({100*cnt/len(y):.1f}%)")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )
    print(f"\nTrain: {len(X_train):,}  |  Test: {len(X_test):,}", flush=True)

    # HistGradientBoostingClassifier = sklearn's fast gradient boosting (histogram-based)
    # It replaces GradientBoostingClassifier for large datasets and supports class_weight natively.
    backbones = {
        "random_forest": (
            RandomForestClassifier(
                n_estimators=200,
                class_weight="balanced",
                n_jobs=-1,
                random_state=42,
            ),
            False,
        ),
        "gradient_boosting": (
            HistGradientBoostingClassifier(
                max_iter=200,
                learning_rate=0.1,
                max_depth=5,
                class_weight="balanced",
                random_state=42,
            ),
            False,  # HistGB supports class_weight directly
        ),
        "mlp": (
            MLPClassifier(
                hidden_layer_sizes=(128, 64),
                max_iter=200,
                random_state=42,
                early_stopping=False,  # early_stopping+string labels crashes in sklearn 1.7
                learning_rate_init=1e-3,
            ),
            False,  # MLP does not use class_weight; class imbalance handled via labels
        ),
    }

    # XGBoost (optional)
    try:
        from xgboost import XGBClassifier
        backbones["xgboost"] = (
            XGBClassifier(
                n_estimators=200,
                learning_rate=0.1,
                max_depth=6,
                eval_metric="mlogloss",
                random_state=42,
                n_jobs=-1,
            ),
            True,
        )
        print("XGBoost available — will benchmark it.", flush=True)
    except ImportError:
        print("XGBoost not installed — skipping.", flush=True)

    results = {}
    for name, (clf, use_sw) in backbones.items():
        results[name] = evaluate_backbone(
            name, clf, X_train, X_test, y_train, y_test,
            use_sample_weight=use_sw,
        )

    best_name = max(results, key=lambda k: results[k]["macro_f1"])
    best_f1 = results[best_name]["macro_f1"]

    report = {
        "status": "ok" if best_f1 >= 0.85 else "below_threshold",
        "best_backbone": best_name,
        "best_macro_f1": best_f1,
        "threshold": 0.85,
        "results": results,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'='*60}")
    print(f"WINNER: {best_name}  (macro-F1 = {best_f1:.4f})")
    print(f"Status: {report['status']}")
    print(f"Report written to {OUTPUT_PATH}")
    print("="*60)

    print("\nSummary:")
    print(f"  {'Backbone':<22} {'Macro-F1':>10} {'Latency(ms)':>12} {'Train(s)':>10}")
    print(f"  {'-'*22} {'-'*10} {'-'*12} {'-'*10}")
    for bname, r in sorted(results.items(), key=lambda x: -x[1]["macro_f1"]):
        marker = " <-- BEST" if bname == best_name else ""
        print(f"  {bname:<22} {r['macro_f1']:>10.4f} {r['latency_ms']:>12.1f} {r['train_sec']:>10.1f}{marker}")


if __name__ == "__main__":
    main()
