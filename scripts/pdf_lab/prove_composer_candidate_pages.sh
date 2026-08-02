#!/usr/bin/env bash
# Prove/disprove: Composer 2.5 direct Cursor on phase-54 candidates (no scillm).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
export PDF_LAB_CALL1_TIMEOUT_S="${PDF_LAB_CALL1_TIMEOUT_S:-300}"
export PDF_LAB_CALL2_TIMEOUT_S="${PDF_LAB_CALL2_TIMEOUT_S:-600}"
export PDF_LAB_CURSOR_AGENT_BIN="${PDF_LAB_CURSOR_AGENT_BIN:-/home/graham/.local/bin/agent}"
ARTIFACT_ROOT="${1:-artifacts/pdf_lab/composer25_proof/$(date -u +%Y%m%dT%H%M%SZ)}"
MODEL="${CURSOR_MODEL:-composer-2.5}"
echo "artifact_root=$ARTIFACT_ROOT model=$MODEL"
python3 scripts/pdf_lab/run_exec_two_call_benchmark.py \
  --backend cursor \
  --cursor-model "$MODEL" \
  --open-only \
  --verify-closure \
  --reset-between-pages \
  --artifact-dir "$ARTIFACT_ROOT"
echo "rollup: $ARTIFACT_ROOT/rollup.json"
echo "report: $ARTIFACT_ROOT/rollup.md"
