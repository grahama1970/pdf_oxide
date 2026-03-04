# OCR Guide — Extracting Text from Scanned PDFs

PDFOxide can extract text from scanned PDFs using [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) models via ONNX Runtime. This guide covers model selection, configuration, and best practices.

## How It Works

The OCR pipeline has three stages:

1. **Detection** (DBNet++): Finds text regions (bounding boxes) in the page image
2. **Recognition** (SVTR): Reads text from each cropped region
3. **Postprocessing**: Sorts results in reading order and joins into text

PDFOxide automatically detects whether a page is scanned or has native text, so you can use the same API for both.

## Quick Start

```bash
# 1. Download recommended models (~12.5 MB total)
./scripts/setup_ocr_models.sh

# 2. Run (Rust)
cargo run --features ocr --example ocr_scanned_pdf -- \
    --pdf scanned.pdf \
    --det .models/det.onnx \
    --rec .models/rec.onnx \
    --dict .models/en_dict.txt
```

## Model Selection

PDFOxide supports PaddleOCR v3, v4, and v5 models. Detection and recognition models can be mixed across versions.

### Recommended: V4 Detection + V5 Recognition

| Component | Model | Size | Source |
|-----------|-------|------|--------|
| Detection | ch_PP-OCRv4_det | 4.7 MB | [deepghs/paddleocr](https://huggingface.co/deepghs/paddleocr) |
| Recognition | en_PP-OCRv5_mobile_rec | 7.8 MB | [monkt/paddleocr-onnx](https://huggingface.co/monkt/paddleocr-onnx) |
| Dictionary | PP-OCRv5 English | 4 KB | Same as recognition |

This combination delivers the best English accuracy because:

- **V4 detection** reliably segments text lines with minimal false positives. It uses a MaxSide resize strategy that downscales images to 960px, which is well-matched to its training data.
- **V5 recognition** has the highest character-level accuracy for English text. It processes cropped text regions independently of the detection model's resize strategy.

### All Tested Combinations

| Combination | Detection | Recognition | English Accuracy | Total Size | Config |
|---|---|---|---|---|---|
| **V4 det + V5 rec** | ch_PP-OCRv4_det | en_PP-OCRv5_mobile_rec | Best | ~12.5 MB | `OcrConfig::default()` |
| V4 det + V4 rec | ch_PP-OCRv4_det | en_PP-OCRv4_rec | Good | ~12.4 MB | `OcrConfig::default()` |
| V5 det + V5 rec | PP-OCRv5_server_det | en_PP-OCRv5_mobile_rec | Good (different error profile) | ~96 MB | `OcrConfig::v5()` |
| V3 det + V3 rec | en_PP-OCRv3_det | en_PP-OCRv3_rec | Fair | ~11 MB | `OcrConfig::default()` |

### When to Use Full V5

The full V5 stack (V5 detection + V5 recognition) uses a much larger detection model (88 MB vs 4.7 MB). Use it when:

- You need to detect text in complex layouts (mixed orientations, curved text)
- The V4 detector misses text regions in your documents
- You have sufficient memory and don't mind slower inference

For standard English documents (reports, articles, invoices), the V4+V5 combination is both faster and more accurate.

## Detection Resize Strategies

The detection model needs to resize input images before inference. PDFOxide supports two strategies:

### MaxSide (V3/V4 default)

Scales the image **down** so the longest side fits within a limit (default: 960px). This is fast and works well with V3/V4 models that were trained on smaller inputs.

```
Original: 2480×3508 (300 DPI A4 scan)
After MaxSide(960): 679×960
```

### MinSide (V5)

Scales the image **up** so the shortest side is at least a minimum (default: 64px), but caps the longest side at a limit (default: 4000px). This preserves high resolution, which V5 server models need for accurate detection.

```
Original: 2480×3508 (300 DPI A4 scan)
After MinSide(64, 4000): 2480×3508 (unchanged — already above minimum)

Original: 30×20 (tiny image)
After MinSide(64, 4000): 96×64 (scaled up)
```

**Important:** Using MaxSide with V5 detection models (or MinSide with V3/V4 models) will produce poor results. Always match the strategy to the model version.

## Configuration Reference

### Rust

```rust
use pdf_oxide::ocr::{OcrConfig, OcrEngine, DetResizeStrategy};

// Default config (V3/V4 detection + any recognition)
let config = OcrConfig::default();

// V5 config (V5 detection + any recognition)
let config = OcrConfig::v5();

// Custom config
let config = OcrConfig::builder()
    .det_threshold(0.3)       // Detection confidence (0.0-1.0, default: 0.3)
    .box_threshold(0.6)       // Box filter threshold (0.0-1.0, default: 0.6)
    .rec_threshold(0.5)       // Recognition confidence (0.0-1.0, default: 0.5)
    .num_threads(4)           // ONNX Runtime threads (default: 4)
    .max_candidates(1000)     // Max text box candidates (default: 1000)
    .unclip_ratio(1.5)        // Box expansion ratio (default: 1.5)
    .rec_target_height(48)    // Recognition input height (default: 48)
    .det_resize_strategy(DetResizeStrategy::MaxSide { max_side: 960 })
    .build();

let engine = OcrEngine::new("det.onnx", "rec.onnx", "dict.txt", config)?;
```

### Python

```bash
# Recommended: Install with OCR support
pip install pdf_oxide[ocr]
```

```python
from pdf_oxide import OcrConfig, OcrEngine

# Default config (V3/V4 detection)
config = OcrConfig()

# V5 config
config = OcrConfig(use_v5=True)

# Custom config
config = OcrConfig(
    det_threshold=0.3,
    box_threshold=0.6,
    rec_threshold=0.5,
    num_threads=4,
    max_candidates=1000,
    use_v5=False,  # True for V5 detection models
)

engine = OcrEngine(
    det_model_path=".models/det.onnx",
    rec_model_path=".models/rec.onnx",
    dict_path=".models/en_dict.txt",
    config=config,  # Optional, defaults to OcrConfig()
)
```

## Page Type Detection

PDFOxide automatically classifies pages before extraction:

| Page Type | Description | Action |
|-----------|-------------|--------|
| **NativeText** | Has substantial embedded text | Uses standard text extraction |
| **ScannedPage** | Large image, no/minimal text | Full OCR |
| **HybridPage** | Some native text + large images | Uses whichever source produces more text |

```rust
use pdf_oxide::ocr::{detect_page_type, PageType};

match detect_page_type(&mut doc, 0)? {
    PageType::NativeText => println!("Native text"),
    PageType::ScannedPage => println!("Needs OCR"),
    PageType::HybridPage => println!("Mixed content"),
}
```

## Dictionary Setup

PaddleOCR dictionaries are text files with one character per line. The model's output classes map to dictionary entries by index.

**Critical:** The dictionary must include a space character as the last line. PaddleOCR models output space as the final class (e.g., index 96 for V3/V4 with 97 classes, index 437 for V5 with 438 classes). If space is missing, words will run together.

```bash
# Download dictionary
curl -L https://huggingface.co/monkt/paddleocr-onnx/resolve/main/languages/english/dict.txt -o dict.txt

# Add space as last line (required!)
echo " " >> dict.txt
```

The `setup_ocr_models.sh` script handles this automatically.

## ONNX Runtime Setup

The OCR feature requires ONNX Runtime v1.23+ at runtime.

### Option 1: System Install

```bash
# Ubuntu/Debian
apt install libonnxruntime-dev

# Or download from GitHub releases
wget https://github.com/microsoft/onnxruntime/releases/download/v1.23.0/onnxruntime-linux-x64-1.23.0.tgz
tar xzf onnxruntime-linux-x64-1.23.0.tgz
```

### Option 2: Environment Variables

```bash
export ORT_LIB_LOCATION=/path/to/onnxruntime/lib
export ORT_PREFER_DYNAMIC_LINK=1

# Then build
cargo build --features ocr
```

### macOS

```bash
brew install onnxruntime
export ORT_LIB_LOCATION=$(brew --prefix onnxruntime)/lib
```

### WebAssembly

OCR is **not supported** in WebAssembly builds. ONNX Runtime requires native code execution and is not available in the browser or Node.js WASM environment.

## Troubleshooting

### Garbled output (e.g., `0I0f0m0j0p...`)

The dictionary file may be incorrect or the space character is missing. Re-download the dictionary and ensure space is the last line:
```bash
echo " " >> .models/en_dict.txt
```

### Words run together (no spaces)

Same cause — the space character is missing from the end of the dictionary file.

### V5 detection produces worse results than V4

Make sure you're using `OcrConfig::v5()` (Rust) or `OcrConfig(use_v5=True)` (Python). V5 models need the MinSide resize strategy to preserve image resolution. Using the default MaxSide strategy will downscale the image to 960px, which is too small for the V5 detector.

### Build error: `no method named tls_config`

This is a known bug in `ort-sys` 2.0.0-rc.11 when using the `download-binaries` feature. Install ONNX Runtime manually and set `ORT_LIB_LOCATION` instead.

### Python segfault (exit code 139)

Ensure you're using the latest version with the infinite recursion fix. Older versions had a bug where `extract_text()` → `needs_ocr()` → `detect_page_type()` → `extract_text()` caused a stack overflow.

## Examples

### Rust

```bash
cargo run --features ocr --example ocr_scanned_pdf -- \
    --pdf scanned.pdf \
    --det .models/det.onnx \
    --rec .models/rec.onnx \
    --dict .models/en_dict.txt

# With V5 detection
cargo run --features ocr --example ocr_scanned_pdf -- \
    --pdf scanned.pdf \
    --det .models/v5/det.onnx \
    --rec .models/v5/rec.onnx \
    --dict .models/v5/en_dict.txt \
    --v5
```

### Python

```bash
python examples/ocr_example.py scanned.pdf \
    --det .models/det.onnx \
    --rec .models/rec.onnx \
    --dict .models/en_dict.txt

# With V5 detection
python examples/ocr_example.py scanned.pdf \
    --det .models/v5/det.onnx \
    --rec .models/v5/rec.onnx \
    --dict .models/v5/en_dict.txt \
    --v5
```
