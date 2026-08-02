#!/usr/bin/env bash
# Direct Cursor headless harness (no scillm). Used by exec_two_call_page_repair.py --backend cursor.
set -euo pipefail

WORKSPACE="${WORKSPACE:?WORKSPACE required}"
RUN_CTX="${RUN_CTX:?RUN_CTX required}"
EVENTS_OUT="${EVENTS_OUT:?EVENTS_OUT required}"
PROMPT_FILE="${PROMPT_FILE:?PROMPT_FILE required}"
SKILLS_ROOT="${SKILLS_ROOT:-$HOME/.claude/skills}"
SKILLS_CSV="${SKILLS_CSV:-}"
CURSOR_MODEL="${CURSOR_MODEL:-auto}"
CURSOR_MODE="${CURSOR_MODE:-}"
CURSOR_FORCE="${CURSOR_FORCE:-0}"
RULE_NAME="${RULE_NAME:-pdf-lab-exec-selected-skills}"
AGENT_BIN="${PDF_LAB_CURSOR_AGENT_BIN:-/home/graham/.local/bin/agent}"
TIMEOUT_S="${TIMEOUT_S:-300}"

mkdir -p "$RUN_CTX"
cp "$PROMPT_FILE" "$RUN_CTX/prompt.md"
: >"$EVENTS_OUT"

# Optional skill staging (same idea as scillm _materialize_cursor_harness)
SELECTED_SKILLS="$RUN_CTX/skills"
MANIFEST="$RUN_CTX/selected-skills.md"
mkdir -p "$SELECTED_SKILLS"

{
  echo "# Selected skills for Cursor headless run"
  echo
} >"$MANIFEST"

if [[ -n "$SKILLS_CSV" ]]; then
  RULE_DIR="$WORKSPACE/.cursor/rules/$RULE_NAME"
  if ! mkdir -p "$RULE_DIR" 2>/dev/null; then
    echo "warn: cannot write $RULE_DIR (often root-owned after scillm docker); continuing without workspace rule" >&2
    RULE_DIR=""
  fi
  IFS=',' read -r -a _skills <<<"$SKILLS_CSV"
  for skill in "${_skills[@]}"; do
    skill="${skill// /}"
    [[ -z "$skill" ]] && continue
    src="$SKILLS_ROOT/$skill/SKILL.md"
    if [[ ! -f "$src" ]]; then
      echo "missing skill: $src" >&2
      exit 2
    fi
    dest="$SELECTED_SKILLS/$skill"
    rm -rf "$dest"
    cp -a "$SKILLS_ROOT/$skill" "$dest"
    echo "- \`$skill\`: \`.scillm/cursor-headless/$(basename "$RUN_CTX")/skills/$skill/SKILL.md\`" >>"$MANIFEST"
  done
  if [[ -n "${RULE_DIR:-}" ]]; then
    cat >"$RULE_DIR/RULE.md" <<EOF
---
description: Harness-selected skills for pdf_oxide direct cursor run.
alwaysApply: true
---

Only use skills listed in the manifest for this run. Read each SKILL.md before use.
Manifest: \`.scillm/cursor-headless/$(basename "$RUN_CTX")/selected-skills.md\`
EOF
  fi
fi

PROMPT_TEXT="$(cat "$RUN_CTX/prompt.md")"

cmd=(
  "$AGENT_BIN"
  -p
  --trust
  --workspace
  "$WORKSPACE"
  --output-format
  stream-json
  --stream-partial-output
  --model
  "$CURSOR_MODEL"
)
if [[ "$CURSOR_FORCE" == "1" ]]; then
  cmd+=(--force)
fi
if [[ -n "$CURSOR_MODE" ]]; then
  cmd+=(--mode "$CURSOR_MODE")
fi
cmd+=("$PROMPT_TEXT")

export CURSOR_API_KEY="${CURSOR_API_KEY:-}"
if [[ -z "$CURSOR_API_KEY" ]] && [[ -f "$HOME/.zshrc" ]]; then
  # shellcheck disable=SC1090
  val="$(grep -E '^export CURSOR_API_KEY=' "$HOME/.zshrc" | tail -n1 | sed -E 's/^export CURSOR_API_KEY=//; s/^["'\'']//; s/["'\'']$//')"
  export CURSOR_API_KEY="$val"
fi

set +e
timeout --signal=TERM "${TIMEOUT_S}s" "${cmd[@]}" 2>"$RUN_CTX/stderr.log" | tee "$RUN_CTX/stdout.stream" | while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  echo "$line" >>"$EVENTS_OUT"
done
agent_exit=$?
set -e

python3 - "$RUN_CTX" "$EVENTS_OUT" "$agent_exit" <<'PY'
import json, sys
from pathlib import Path

run_ctx = Path(sys.argv[1])
events_path = Path(sys.argv[2])
agent_exit = int(sys.argv[3])

state = {
    "text_parts": [],
    "session_id": None,
    "model": None,
    "api_key_source": None,
    "tool_count": 0,
    "result_event": None,
}

for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    if not isinstance(event, dict):
        continue
    if event.get("type") == "system" and event.get("subtype") == "init":
        state["session_id"] = event.get("session_id") or state["session_id"]
        state["model"] = event.get("model") or state["model"]
        state["api_key_source"] = event.get("apiKeySource") or state["api_key_source"]
    if event.get("type") == "tool_call" and event.get("subtype") == "started":
        state["tool_count"] += 1
    msg = event.get("message")
    if event.get("type") == "assistant" and isinstance(msg, dict):
        for part in msg.get("content", []):
            if isinstance(part, dict) and part.get("type") == "text":
                state["text_parts"].append(part.get("text") or "")
    if event.get("type") == "result":
        state["result_event"] = event

re = state["result_event"]
text = ""
is_error = True
duration_ms = None
if isinstance(re, dict):
    is_error = bool(re.get("is_error"))
    if isinstance(re.get("result"), str):
        text = re["result"].strip()
    duration_ms = re.get("duration_ms")
if not text and state["text_parts"]:
    text = "".join(state["text_parts"]).strip()
if isinstance(re, dict) and re.get("subtype") == "success":
    is_error = False

meta = {
    "agent_exit_code": agent_exit,
    "session_id": state["session_id"],
    "tool_call_count": state["tool_count"],
    "api_key_source": state["api_key_source"],
    "result": {
        "is_error": is_error,
        "result": text,
        "duration_ms": duration_ms,
        "subtype": (re or {}).get("subtype"),
    },
}
(run_ctx / "run_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
print(json.dumps(meta))
PY

chmod +x /home/graham/workspace/experiments/pdf_oxide/scripts/pdf_lab/run_cursor_selected_skills.sh