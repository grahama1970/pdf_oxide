#!/usr/bin/env bash
# Deterministic 30-round driver for pdf_oxide extraction improvement.
#
# Each round: orchestrate round_plan.yaml (extract → diff → /code-runner fix → tally)
# Halt when: 30 rounds completed, OR total_defects stops decreasing for 2 consecutive
# rounds (no-progress stop per best-practices-self-improvement-loop).

set -euo pipefail

PLAN="/home/graham/workspace/experiments/pdf_oxide/scripts/pdf_oxide_loop/round_plan.yaml"
ROUNDS_FILE="/tmp/pdf_oxide_rounds.json"
MAX_ROUNDS=30
STALL_LIMIT=2   # halt if no improvement for this many consecutive rounds

prev_total=""
stall_count=0

for round in $(seq 1 $MAX_ROUNDS); do
  echo "=== Round ${round}/${MAX_ROUNDS} ==="
  if ! orchestrate run "$PLAN"; then
    echo "Round ${round}: orchestrate failed, halting"
    exit 1
  fi

  curr_total=$(python3 -c "
import json
r = json.load(open('${ROUNDS_FILE}'))
print(r['rounds'][-1]['total_defects'])
")
  echo "Round ${round}: total_defects=${curr_total}"

  if [[ -n "$prev_total" && "$curr_total" -ge "$prev_total" ]]; then
    stall_count=$((stall_count + 1))
    echo "  no improvement (${stall_count}/${STALL_LIMIT})"
    if [[ "$stall_count" -ge "$STALL_LIMIT" ]]; then
      echo "=== HALT: ${STALL_LIMIT} rounds without improvement ==="
      break
    fi
  else
    stall_count=0
  fi
  prev_total="$curr_total"
done

echo "=== Final tally ==="
cat "$ROUNDS_FILE"
