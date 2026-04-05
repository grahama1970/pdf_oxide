#!/usr/bin/env python3
"""
Train header-verdict classifier with iterative holdout gate.

Input:  artifacts/header_verdict_labels.jsonl  (529,340 rows, 18 features)
Output: ~/.pi/models/classifiers/header_verdict.joblib
        artifacts/training_summary.json
"""

import json
import time
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import f1_score, precision_recall_fscore_support
import joblib

REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_PATH = REPO_ROOT / "artifacts" / "header_verdict_labels.jsonl"
SUMMARY_PATH = REPO_ROOT / "artifacts" / "training_summary.json"
MODEL_DIR = Path.home() / ".pi" / "models" / "classifiers"
MODEL_PATH = MODEL_DIR / "header_verdict.joblib"

LABEL_ORDER = ["Accept", "Reject", "Escalate"]

# Gate thresholds
MACRO_F1_THRESHOLD = 0.90
PER_CLASS_RECALL_THRESHOLD = 0.80

# Training config
INITIAL_N_ESTIMATORS = 200
N_ESTIMATORS_INCREMENT = 100
MAX_ROUNDS = 5


def load_data(path: Path):
    features = []
    labels = []
    with open(path) as f:
        for line in f:
            row = json.loads(line)
            features.append(row["features"])
            labels.append(row["label"])
    X = np.array(features, dtype=np.float64)
    X = np.where(np.isinf(X), np.sign(X) * 1e9, X)
    X = np.nan_to_num(X, nan=0.0)
    return X.astype(np.float32), np.array(labels)


def evaluate(clf, X, y):
    """Evaluate classifier, return macro_f1, per_class dict, and gate pass status."""
    y_pred = clf.predict(X)
    macro_f1 = f1_score(y, y_pred, average="macro")
    prec, rec, f1s, _ = precision_recall_fscore_support(
        y, y_pred, labels=LABEL_ORDER, zero_division=0
    )

    per_class = {}
    min_recall = 1.0
    for i, cls in enumerate(LABEL_ORDER):
        per_class[cls] = {
            "precision": round(float(prec[i]), 4),
            "recall": round(float(rec[i]), 4),
            "f1": round(float(f1s[i]), 4),
        }
        min_recall = min(min_recall, float(rec[i]))

    gate_pass = macro_f1 >= MACRO_F1_THRESHOLD and min_recall >= PER_CLASS_RECALL_THRESHOLD
    return round(macro_f1, 4), per_class, gate_pass


def main():
    print(f"Loading data from {INPUT_PATH} ...", flush=True)
    X, y = load_data(INPUT_PATH)
    print(f"Loaded {len(X):,} rows, {X.shape[1]} features", flush=True)

    classes, counts = np.unique(y, return_counts=True)
    for cls, cnt in zip(classes, counts):
        print(f"  {cls}: {cnt:,} ({100 * cnt / len(y):.1f}%)")

    # 80% train, 10% holdout, 10% test (stratified)
    X_train, X_temp, y_train, y_temp = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )
    X_holdout, X_test, y_holdout, y_test = train_test_split(
        X_temp, y_temp, test_size=0.50, random_state=42, stratify=y_temp
    )
    print(f"\nTrain: {len(X_train):,}  |  Holdout: {len(X_holdout):,}  |  Test: {len(X_test):,}", flush=True)

    # Iterative training with holdout gate
    best_clf = None
    best_round = 0
    best_n_estimators = INITIAL_N_ESTIMATORS
    best_holdout_f1 = 0.0
    best_holdout_per_class = {}
    threshold_met = False

    n_estimators = INITIAL_N_ESTIMATORS

    for round_num in range(1, MAX_ROUNDS + 1):
        print(f"\n--- Round {round_num} (n_estimators={n_estimators}) ---", flush=True)

        clf = RandomForestClassifier(
            n_estimators=n_estimators,
            class_weight="balanced",
            n_jobs=-1,
            random_state=42,
        )

        t0 = time.perf_counter()
        clf.fit(X_train, y_train)
        train_sec = time.perf_counter() - t0
        print(f"  Train time: {train_sec:.1f}s", flush=True)

        holdout_f1, holdout_per_class, gate_pass = evaluate(clf, X_holdout, y_holdout)

        print(f"  Holdout macro-F1: {holdout_f1:.4f}", flush=True)
        for cls in LABEL_ORDER:
            p = holdout_per_class[cls]
            print(f"    {cls:10s}: P={p['precision']:.3f}  R={p['recall']:.3f}  F1={p['f1']:.3f}")

        # Track best
        if holdout_f1 > best_holdout_f1:
            best_clf = clf
            best_round = round_num
            best_n_estimators = n_estimators
            best_holdout_f1 = holdout_f1
            best_holdout_per_class = holdout_per_class

        if gate_pass:
            print(f"  GATE PASSED at round {round_num}", flush=True)
            threshold_met = True
            break
        else:
            print(f"  Gate not passed. Increasing n_estimators.", flush=True)
            n_estimators += N_ESTIMATORS_INCREMENT

    # Final evaluation on test set
    print(f"\n{'=' * 60}")
    print(f"Best round: {best_round} (n_estimators={best_n_estimators})")
    print(f"Evaluating on test set...", flush=True)

    test_f1, test_per_class, _ = evaluate(best_clf, X_test, y_test)

    print(f"Test macro-F1: {test_f1:.4f}", flush=True)
    for cls in LABEL_ORDER:
        p = test_per_class[cls]
        print(f"  {cls:10s}: P={p['precision']:.3f}  R={p['recall']:.3f}  F1={p['f1']:.3f}")

    # Save model
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(best_clf, MODEL_PATH)
    print(f"\nModel saved to {MODEL_PATH}", flush=True)

    # Save training summary
    summary = {
        "threshold_met": threshold_met,
        "backbone": "random_forest",
        "best_iteration": best_round,
        "n_estimators": best_n_estimators,
        "holdout_macro_f1": best_holdout_f1,
        "holdout_per_class": best_holdout_per_class,
        "test_macro_f1": test_f1,
        "test_per_class": test_per_class,
        "model_path": str(MODEL_PATH),
        "training_samples": len(X_train),
        "classes": LABEL_ORDER,
        "features": X.shape[1],
    }

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"Summary saved to {SUMMARY_PATH}")
    print(f"{'=' * 60}")

    if not threshold_met:
        print("WARNING: Holdout gate was NOT met after all rounds.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
