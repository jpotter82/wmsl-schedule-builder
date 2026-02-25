#!/usr/bin/env bash
set -euo pipefail

SCRIPT="${1:-scheduler_newest.py}"     # or scheduler_new.py / scheduler_newest.py
PY="${PY:-python3.11}"
MAX_TRIES="${MAX_TRIES:-500}"          # safety cap
OUTDIR="${OUTDIR:-runs_$(date +%Y%m%d_%H%M%S)}"

mkdir -p "$OUTDIR"

# Match either of your possible output styles:
#  1) "Critical: Teams below minimum DH days: [...]"
#  2) "Critical: The following teams did not meet the minimum doubleheader sessions ..."
CRIT_REGEX='^Critical: .*minimum (DH|doubleheader)'

for ((i=1; i<=MAX_TRIES; i++)); do
  echo "=== Attempt $i/$MAX_TRIES ==="

  LOG="$OUTDIR/attempt_${i}.log"
  # Run and capture combined output
  set +e
  OUTPUT="$($PY "$SCRIPT" 2>&1 | tee "$LOG")"
  RC=${PIPESTATUS[0]}
  set -e

  # If the scheduler crashed, log it and keep going
  if [[ $RC -ne 0 ]]; then
    echo "Attempt $i failed (exit $RC). See $LOG"
    continue
  fi

  # Extract seed if present (nice for reproducibility)
  SEED="$(echo "$OUTPUT" | sed -n 's/^Using RNG seed: \([0-9]\+\).*/\1/p' | head -n1)"
  [[ -z "$SEED" ]] && SEED="noseed"

  if echo "$OUTPUT" | grep -Eq "$CRIT_REGEX"; then
    echo "Attempt $i: still has minimum-DH critical line. (seed=$SEED)"
    # Keep any produced schedules for debugging
    [[ -f softball_schedule.csv  ]] && mv -f softball_schedule.csv  "$OUTDIR/attempt_${i}_seed_${SEED}.csv"
    [[ -f softball_schedule.xlsx ]] && mv -f softball_schedule.xlsx "$OUTDIR/attempt_${i}_seed_${SEED}.xlsx"
    continue
  fi

  echo "✅ Success on attempt $i (seed=$SEED). No minimum-DH critical line."
  [[ -f softball_schedule.csv  ]] && mv -f softball_schedule.csv  "$OUTDIR/SUCCESS_attempt_${i}_seed_${SEED}.csv"
  [[ -f softball_schedule.xlsx ]] && mv -f softball_schedule.xlsx "$OUTDIR/SUCCESS_attempt_${i}_seed_${SEED}.xlsx"
  exit 0
done

echo "❌ No success after $MAX_TRIES attempts. Logs/schedules in: $OUTDIR"
exit 2
