#!/usr/bin/env bash
# Minimal direct Cursor agent smoke test (no scillm).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
ART="$REPO/artifacts/pdf_lab/cursor_smoke/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$ART"
PROMPT_FILE="$ART/prompt.txt"
cat >"$PROMPT_FILE" <<'EOF'
Read-only smoke test. Do not edit any files.
Return one JSON object only: {"status":"ok","smoke":true}
EOF

export WORKSPACE="$REPO"
export RUN_CTX="$ART/run_ctx"
export EVENTS_OUT="$ART/cursor-events.jsonl"
export PROMPT_FILE
export SKILLS_CSV=""
export CURSOR_MODEL="${CURSOR_MODEL:-auto}"
export CURSOR_MODE="${CURSOR_MODE:-plan}"
export CURSOR_FORCE=0
export TIMEOUT_S="${TIMEOUT_S:-120}"

echo "artifact_dir=$ART"
"$REPO/scripts/pdf_lab/run_cursor_selected_skills.sh" | tee "$ART/stdout_meta.json"
echo "---"
python3 - <<PY
import json
from pathlib import Path
art = Path("$ART")
meta = json.loads((art / "run_ctx" / "run_meta.json").read_text())
print("agent_exit_code", meta.get("agent_exit_code"))
print("tool_call_count", meta.get("tool_call_count"))
print("is_error", meta.get("result", {}).get("is_error"))
print("text_head", (meta.get("result", {}).get("result") or "")[:200])
events = (art / "cursor-events.jsonl").read_text().splitlines()
print("event_lines", len(events))
term = [json.loads(l) for l in events if l.strip() and json.loads(l).get("type")=="result"]
print("terminal", term[-1].get("subtype") if term else "none")
PY
