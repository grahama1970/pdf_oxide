#!/usr/bin/env python3
"""
Export all 5 extractor classifiers to ONNX format for embedding in pdf_oxide Rust.

Classifiers:
  1. Table Strategy (GradientBoosting, 21 features → 9 classes)
  2. Glossary Section (LogisticRegression, 4 features → 2 classes)
  3. Shadow S00 (GradientBoosting, 22 features → 3 classes)
  4. Table Strategy Vision (EfficientNet-B0 + EdgeNeXt, 224x224 → 3 classes)
  5. Merge Classifier (EfficientNet-B0 vision + RandomForest tabular → 2 classes)

Usage:
    python training/export_classifiers_to_onnx.py --all
    python training/export_classifiers_to_onnx.py --model table-strategy
    python training/export_classifiers_to_onnx.py --validate --corpus-dir /path/to/pdfs

Output: models/onnx/*.onnx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Paths — resolve relative to this repo and sibling repos
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
EXTRACTOR_ROOT = REPO_ROOT.parent / "extractor"
PI_MONO_ROOT = REPO_ROOT.parent / "pi-mono"
CLASSIFIER_MODELS = PI_MONO_ROOT / ".pi" / "skills" / "create-table-classifier" / "models"
OUTPUT_DIR = REPO_ROOT / "models" / "onnx"

# Model source paths
MODELS = {
    "table-strategy": {
        "type": "sklearn",
        "path": EXTRACTOR_ROOT / "models" / "table-strategy-classifier" / "model.joblib",
        "label_encoder": EXTRACTOR_ROOT / "models" / "table-strategy-classifier" / "label_encoder.joblib",
        "n_features": 21,
        "n_classes": 9,
        "description": "GradientBoosting: 21 tabular features → 9 table extraction strategies",
    },
    "glossary-section": {
        "type": "sklearn",
        "path": EXTRACTOR_ROOT / "models" / "glossary-section-classifier" / "model.joblib",
        "n_features": 4,
        "n_classes": 2,
        "description": "LogisticRegression: 4 features → glossary_section / non_glossary",
    },
    "shadow-s00": {
        "type": "sklearn",
        "path": CLASSIFIER_MODELS / "shadow-s00" / "shadow_s00_model.joblib",
        "n_features": 22,
        "n_classes": 3,
        "description": "GradientBoosting: 22 features → lattice_sufficient / needs_stream / no_tables",
    },
    "table-vision-efficientnet": {
        "type": "pytorch",
        "path": CLASSIFIER_MODELS / "table-classifier-efficientnet-b0" / "best_model.pth",
        "config": CLASSIFIER_MODELS / "table-classifier-efficientnet-b0",
        "backbone": "efficientnet_b0",
        "input_shape": (1, 3, 224, 224),
        "n_classes": 3,
        "class_names": ["lattice", "stream", "lattice_sensitive"],
        "description": "EfficientNet-B0: 224x224 image → 3 table strategies",
    },
    "table-vision-edgenext": {
        "type": "pytorch",
        "path": CLASSIFIER_MODELS / "table-classifier-edgenext-small" / "best_model.pth",
        "config": CLASSIFIER_MODELS / "table-classifier-edgenext-small",
        "backbone": "edgenext_small",
        "input_shape": (1, 3, 224, 224),
        "n_classes": 3,
        "class_names": ["lattice", "stream", "lattice_sensitive"],
        "description": "EdgeNeXt-Small: 224x224 image → 3 table strategies",
    },
    "merge-vision": {
        "type": "pytorch",
        "path": CLASSIFIER_MODELS / "merge-classifier-final" / "best_model.pth",
        "config": CLASSIFIER_MODELS / "merge-classifier-final",
        "backbone": "efficientnet_b0",
        "input_shape": (1, 3, 224, 224),
        "n_classes": 2,
        "class_names": ["merge", "separate"],
        "description": "EfficientNet-B0: 224x224 side-by-side image → merge / separate",
    },
    "merge-tabular": {
        "type": "sklearn",
        "path": CLASSIFIER_MODELS / "merge-classifier-tabular" / "model.joblib",
        "n_features": 8,
        "n_classes": 2,
        "feature_names": [
            "col_count_match", "width_ratio", "horizontal_iou",
            "title_has_continued", "same_section", "s05b_title_similarity",
            "gap_between_tables_px", "row_count_ratio",
        ],
        "description": "RandomForest: 8 tabular features → merge / separate",
    },
}


# ---------------------------------------------------------------------------
# sklearn → ONNX
# ---------------------------------------------------------------------------

def export_sklearn(name: str, spec: dict[str, Any]) -> Path:
    """Export a scikit-learn model to ONNX via skl2onnx."""
    import joblib
    from skl2onnx import convert_sklearn
    from skl2onnx.common.data_types import FloatTensorType

    print(f"[{name}] Loading sklearn model from {spec['path']}")
    model = joblib.load(spec["path"])

    n_features = spec["n_features"]
    initial_type = [("X", FloatTensorType([None, n_features]))]

    print(f"[{name}] Converting to ONNX (input: {n_features} features)")
    onnx_model = convert_sklearn(
        model,
        name,
        initial_types=initial_type,
        target_opset=13,
        options={id(model): {"zipmap": False}},  # return arrays, not dicts
    )

    output_path = OUTPUT_DIR / f"{name}.onnx"
    with open(output_path, "wb") as f:
        f.write(onnx_model.SerializeToString())

    print(f"[{name}] Saved to {output_path} ({output_path.stat().st_size / 1024:.1f} KB)")

    # Also export label encoder if present
    if "label_encoder" in spec and spec["label_encoder"].exists():
        le = joblib.load(spec["label_encoder"])
        classes = list(le.classes_)
        meta_path = OUTPUT_DIR / f"{name}_labels.json"
        meta_path.write_text(json.dumps({"classes": classes}, indent=2))
        print(f"[{name}] Label mapping saved to {meta_path}")

    return output_path


def validate_sklearn(name: str, spec: dict[str, Any], onnx_path: Path) -> bool:
    """Validate ONNX output matches sklearn predictions exactly."""
    import joblib
    import onnxruntime as ort

    model = joblib.load(spec["path"])
    session = ort.InferenceSession(str(onnx_path))

    n_features = spec["n_features"]
    np.random.seed(42)
    test_inputs = np.random.randn(100, n_features).astype(np.float32)

    # sklearn predictions
    sk_preds = model.predict(test_inputs)
    sk_proba = model.predict_proba(test_inputs)

    # ONNX predictions
    input_name = session.get_inputs()[0].name
    onnx_result = session.run(None, {input_name: test_inputs})
    onnx_preds = onnx_result[0]  # labels
    onnx_proba = onnx_result[1]  # probabilities

    # Compare
    pred_match = np.array_equal(sk_preds, onnx_preds)
    proba_close = np.allclose(sk_proba, onnx_proba, atol=1e-5)

    if pred_match and proba_close:
        print(f"[{name}] ONNX validation PASSED (100/100 predictions match)")
    else:
        pred_diff = np.sum(sk_preds != onnx_preds)
        print(f"[{name}] ONNX validation FAILED: {pred_diff}/100 predictions differ")

    return pred_match and proba_close


# ---------------------------------------------------------------------------
# PyTorch → ONNX
# ---------------------------------------------------------------------------

def _load_pytorch_model(spec: dict[str, Any]):
    """Load a PyTorch vision model from checkpoint."""
    import torch
    import torchvision.models as models

    backbone = spec["backbone"]
    n_classes = spec["n_classes"]
    checkpoint_path = spec["path"]

    if backbone == "efficientnet_b0":
        model = models.efficientnet_b0(weights=None)
        model.classifier[1] = torch.nn.Linear(
            model.classifier[1].in_features, n_classes
        )
    elif backbone == "edgenext_small":
        try:
            model = models.edgenext_small(weights=None)
        except AttributeError:
            # Older torchvision — try timm
            import timm
            model = timm.create_model("edgenext_small", pretrained=False, num_classes=n_classes)
            state = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
            if isinstance(state, dict) and "model_state_dict" in state:
                state = state["model_state_dict"]
            model.load_state_dict(state, strict=False)
            return model

        model.classifier[2] = torch.nn.Linear(
            model.classifier[2].in_features, n_classes
        )
    else:
        raise ValueError(f"Unknown backbone: {backbone}")

    print(f"  Loading checkpoint from {checkpoint_path}")
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "model_state_dict" in state:
        state = state["model_state_dict"]
    model.load_state_dict(state, strict=False)
    model.eval()
    return model


def export_pytorch(name: str, spec: dict[str, Any]) -> Path:
    """Export a PyTorch vision model to ONNX."""
    import torch

    print(f"[{name}] Loading PyTorch model ({spec['backbone']})")
    model = _load_pytorch_model(spec)

    input_shape = spec["input_shape"]
    dummy_input = torch.randn(*input_shape)

    output_path = OUTPUT_DIR / f"{name}.onnx"
    print(f"[{name}] Exporting to ONNX (input: {input_shape})")

    torch.onnx.export(
        model,
        dummy_input,
        str(output_path),
        export_params=True,
        opset_version=13,
        do_constant_folding=True,
        input_names=["image"],
        output_names=["logits"],
        dynamic_axes={
            "image": {0: "batch_size"},
            "logits": {0: "batch_size"},
        },
    )

    # Save class mapping metadata
    meta = {
        "classes": spec["class_names"],
        "backbone": spec["backbone"],
        "input_shape": list(input_shape),
        "preprocessing": {
            "resize": [224, 224],
            "normalize_mean": [0.485, 0.456, 0.406],
            "normalize_std": [0.229, 0.224, 0.225],
        },
    }
    meta_path = OUTPUT_DIR / f"{name}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))

    print(f"[{name}] Saved to {output_path} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return output_path


def validate_pytorch(name: str, spec: dict[str, Any], onnx_path: Path) -> bool:
    """Validate ONNX output matches PyTorch predictions."""
    import onnxruntime as ort
    import torch

    model = _load_pytorch_model(spec)
    session = ort.InferenceSession(str(onnx_path))

    np.random.seed(42)
    test_input_np = np.random.randn(*spec["input_shape"]).astype(np.float32)
    test_input_torch = torch.from_numpy(test_input_np)

    # PyTorch prediction
    with torch.no_grad():
        pt_logits = model(test_input_torch).numpy()

    # ONNX prediction
    input_name = session.get_inputs()[0].name
    onnx_logits = session.run(None, {input_name: test_input_np})[0]

    close = np.allclose(pt_logits, onnx_logits, atol=1e-4)
    if close:
        print(f"[{name}] ONNX validation PASSED (logits match within 1e-4)")
    else:
        max_diff = np.max(np.abs(pt_logits - onnx_logits))
        print(f"[{name}] ONNX validation WARNING: max logit diff = {max_diff:.6f}")

    # Check class predictions match
    pt_class = np.argmax(pt_logits, axis=1)
    onnx_class = np.argmax(onnx_logits, axis=1)
    class_match = np.array_equal(pt_class, onnx_class)
    if not class_match:
        print(f"[{name}] ONNX class prediction MISMATCH")

    return close and class_match


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def export_model(name: str) -> Path | None:
    """Export a single model to ONNX."""
    spec = MODELS[name]
    model_type = spec["type"]

    if not spec["path"].exists():
        print(f"[{name}] SKIPPED — model not found at {spec['path']}")
        return None

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if model_type == "sklearn":
        return export_sklearn(name, spec)
    elif model_type == "pytorch":
        return export_pytorch(name, spec)
    else:
        print(f"[{name}] Unknown model type: {model_type}")
        return None


def validate_model(name: str, onnx_path: Path) -> bool:
    """Validate a single ONNX model against its original."""
    spec = MODELS[name]
    if spec["type"] == "sklearn":
        return validate_sklearn(name, spec, onnx_path)
    elif spec["type"] == "pytorch":
        return validate_pytorch(name, spec, onnx_path)
    return False


def main():
    parser = argparse.ArgumentParser(description="Export classifiers to ONNX")
    parser.add_argument("--all", action="store_true", help="Export all classifiers")
    parser.add_argument("--model", type=str, choices=list(MODELS.keys()),
                        help="Export a specific model")
    parser.add_argument("--validate", action="store_true",
                        help="Validate ONNX against originals after export")
    parser.add_argument("--list", action="store_true", help="List available models")
    args = parser.parse_args()

    if args.list:
        for name, spec in MODELS.items():
            status = "FOUND" if spec["path"].exists() else "MISSING"
            print(f"  {name:30s} [{status}] {spec['description']}")
        return

    if not args.all and not args.model:
        parser.print_help()
        return

    targets = list(MODELS.keys()) if args.all else [args.model]
    results: dict[str, dict] = {}

    for name in targets:
        print(f"\n{'='*60}")
        print(f"Exporting: {name}")
        print(f"{'='*60}")

        onnx_path = export_model(name)
        result = {"exported": onnx_path is not None}

        if onnx_path and args.validate:
            result["valid"] = validate_model(name, onnx_path)

        results[name] = result

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for name, result in results.items():
        status = "OK" if result["exported"] else "SKIP"
        valid = ""
        if "valid" in result:
            valid = " | valid" if result["valid"] else " | INVALID"
        print(f"  {name:30s} [{status}]{valid}")

    # Save manifest
    manifest = {
        name: {
            "onnx_file": f"{name}.onnx",
            "type": MODELS[name]["type"],
            "description": MODELS[name]["description"],
            "n_features": MODELS[name].get("n_features"),
            "n_classes": MODELS[name].get("n_classes"),
            "input_shape": MODELS[name].get("input_shape"),
        }
        for name in targets
        if results[name]["exported"]
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    print(f"\nManifest written to {manifest_path}")


if __name__ == "__main__":
    main()
