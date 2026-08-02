#!/usr/bin/env bash
# Live end-to-end proof for the RustSec dependency remediation (#8 #9 #10 #11 #14 #16).
#
# This is deliberately NOT a cargo/pytest run. Those prove the tree compiles and
# that in-repo expectations hold; they cannot prove that the rebuilt Python
# extension actually loads under a real interpreter, or that the bumped XML
# parser still reads a real document. Both are exactly what these advisories
# touched:
#
#   pyo3      -> the Python extension module itself   (#8, #9)
#   quick-xml -> XMP / XFA / office XML parsing       (#10, #11)
#   lzw       -> removed; weezl decodes LZW streams   (#14)
#   rustybuzz -> removed; rendering path must survive (#16)
#
# So the run below builds a release wheel, installs it into a throwaway
# interpreter, imports it, and extracts from a real 40MB NIST PDF whose content
# streams are LZW/Flate compressed. It writes a receipt that is read back by the
# caller.
#
# Usage: scripts/pdf_lab/security_advisory_live_e2e.sh --allow-live
set -euo pipefail

if [[ "${1:-}" != "--allow-live" ]]; then
    echo "refusing to run: this performs a real wheel build and corpus extraction" >&2
    echo "usage: $0 --allow-live" >&2
    exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

OUT_DIR="${E2E_OUT_DIR:-$REPO_ROOT/artifacts/pdf_lab/security_advisories_live_e2e}"
mkdir -p "$OUT_DIR"
RECEIPT="$OUT_DIR/receipt.json"
VENV="$OUT_DIR/venv"

CORPUS="${E2E_CORPUS_PDF:-/mnt/storage12tb/extractor_corpus/NIST.SP.800-53Ar5.pdf}"
if [[ ! -f "$CORPUS" ]]; then
    echo "corpus PDF not found: $CORPUS" >&2
    echo "set E2E_CORPUS_PDF to a real PDF to run this proof" >&2
    exit 3
fi

echo "== 1/4 building release wheel (pyo3 0.29, abi3) =="
uv run maturin build --release --features python,rendering,office >/dev/null
WHEEL="$(ls -t "$REPO_ROOT"/target/wheels/pdf_oxide-*.whl | head -1)"
[[ -n "$WHEEL" ]] || { echo "no wheel produced" >&2; exit 4; }

echo "== 2/4 installing into a throwaway interpreter =="
rm -rf "$VENV"
uv venv "$VENV" --python 3.12 -q
uv pip install --python "$VENV/bin/python" -q "$WHEEL"

echo "== 3/4 live import + real-corpus extraction =="
CORPUS="$CORPUS" RECEIPT="$RECEIPT" WHEEL="$WHEEL" \
    "$VENV/bin/python" "$REPO_ROOT/scripts/pdf_lab/security_advisory_live_e2e.py"

echo "== 4/4 cargo audit against the same tree =="
AUDIT_JSON="$OUT_DIR/cargo_audit.txt"
"$HOME/.cargo/bin/cargo-audit" audit > "$AUDIT_JSON" 2>&1 || true

python3 - "$RECEIPT" "$AUDIT_JSON" <<'PY'
import json, sys, pathlib
receipt_path, audit_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
receipt = json.loads(receipt_path.read_text())
audit = audit_path.read_text()
cleared = {}
for issue, adv in [("8","RUSTSEC-2026-0176"), ("9","RUSTSEC-2026-0177"),
                   ("10","RUSTSEC-2026-0194"), ("11","RUSTSEC-2026-0195"),
                   ("14","RUSTSEC-2020-0144"), ("16","RUSTSEC-2026-0206")]:
    cleared[f"#{issue} {adv}"] = adv not in audit
receipt["cargo_audit_cleared"] = cleared
receipt["cargo_audit_output"] = str(audit_path)
still = [k for k, v in cleared.items() if not v]
receipt["all_target_advisories_cleared"] = not still
receipt_path.write_text(json.dumps(receipt, indent=2))
print(json.dumps(cleared, indent=2))
if still:
    print("STILL PRESENT:", still, file=sys.stderr)
    sys.exit(5)
PY

echo
echo "receipt: $RECEIPT"
