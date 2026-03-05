#!/usr/bin/env bash
set -euo pipefail

SCRIPT="${1:-scheduler_newest.py}"     # or scheduler_new.py / scheduler_newest.py
PY="${PY:-python3.11}"
MAX_TRIES="${MAX_TRIES:-1500}"          # safety cap
OUTDIR="${OUTDIR:-runs_$(date +%Y%m%d_%H%M%S)}"

mkdir -p "$OUTDIR"

# Match either of your possible output styles:
#  1) "Critical: Teams below minimum DH days: [...]"
#  2) "Critical: The following teams did not meet the minimum doubleheader sessions ..."
CRIT_DH_REGEX='^Critical: .*minimum (DH|doubleheader)'

# Match: "Critical: Teams below target games: ['B2', 'B4', ...]"
CRIT_BELOW_TARGET_REGEX='^Critical: Teams below target games:'

count_below_target_teams() {
  # Reads full OUTPUT on stdin, prints integer count
  local line inside
  line="$(grep -m1 -E "$CRIT_BELOW_TARGET_REGEX" || true)"
  [[ -z "$line" ]] && { echo 0; return; }

  # Extract content inside [ ... ]
  inside="$(sed -n "s/^Critical: Teams below target games: *\\(\\[.*\\]\\).*/\\1/p" <<<"$line")"
  [[ -z "$inside" ]] && { echo 0; return; }

  # If it's [] => 0
  if [[ "$inside" =~ ^\[[[:space:]]*\]$ ]]; then
    echo 0
    return
  fi

  # Count items by counting comma-separated tokens inside brackets.
  # This works with both "['A','B']" and "['A', 'B']" styles.
  inside="${inside#[}"   # strip leading [
  inside="${inside%]}"   # strip trailing ]
  # split by comma and count non-empty chunks
  awk -v s="$inside" 'BEGIN{
    n=0;
    split(s,a,",");
    for(i in a){
      gsub(/^[ \t\r\n]+|[ \t\r\n]+$/, "", a[i]);
      if(a[i]!="") n++;
    }
    print n;
  }'
}

cleanup_outputs() {
  rm -f softball_schedule.csv softball_schedule.xlsx
}

for ((i=1; i<=MAX_TRIES; i++)); do
  echo "=== Attempt $i/$MAX_TRIES ==="

  # Prevent a previous attempt's outputs from "leaking" into this attempt.
  cleanup_outputs

  LOG="$OUTDIR/attempt_${i}.log"

  # Run and capture combined output
  set +e
  OUTPUT="$($PY "$SCRIPT" 2>&1 | tee "$LOG")"
  RC=${PIPESTATUS[0]}
  set -e

  # Extract seed if present (nice for reproducibility)
  SEED="$(echo "$OUTPUT" | sed -n 's/^Using RNG seed: \([0-9]\+\).*/\1/p' | head -n1)"
  [[ -z "$SEED" ]] && SEED="noseed"

  # If the scheduler crashed, log it, remove any partial outputs, and keep going
  if [[ $RC -ne 0 ]]; then
    echo "Attempt $i failed (exit $RC). See $LOG (seed=$SEED)"
    cleanup_outputs
    continue
  fi

  # 1) Fail if DH critical line exists
  if echo "$OUTPUT" | grep -Eq "$CRIT_DH_REGEX"; then
    echo "Attempt $i: DH minimum critical line present. (seed=$SEED)"
    cleanup_outputs
    continue
  fi

  # 2) Fail if below-target list has >= 10 teams
  BELOW_COUNT="$(echo "$OUTPUT" | count_below_target_teams)"
  if [[ "$BELOW_COUNT" -ne 0 ]]; then
    echo "Attempt $i: Teams below target games = $BELOW_COUNT (>=10). (seed=$SEED)"
    cleanup_outputs
    continue
  fi

  echo "✅ Success on attempt $i (seed=$SEED). DH critical line absent, below-target teams=$BELOW_COUNT (<10)."
  [[ -f softball_schedule.csv  ]] && mv -f softball_schedule.csv  "$OUTDIR/SUCCESS_attempt_${i}_seed_${SEED}.csv"
  [[ -f softball_schedule.xlsx ]] && mv -f softball_schedule.xlsx "$OUTDIR/SUCCESS_attempt_${i}_seed_${SEED}.xlsx"
  exit 0
done

echo "❌ No success after $MAX_TRIES attempts. Logs in: $OUTDIR"
exit 2#!/usr/bin/env bash
set -euo pipefail

SCRIPT="${1:-scheduler_newest.py}"     # or scheduler_new.py / scheduler_newest.py
PY="${PY:-python3.11}"
MAX_TRIES="${MAX_TRIES:-500}"          # safety cap
OUTDIR="${OUTDIR:-runs_$(date +%Y%m%d_%H%M%S)}"

mkdir -p "$OUTDIR"

# Match either of your possible output styles:
#  1) "Critical: Teams below minimum DH days: [...]"
#  2) "Critical: The following teams did not meet the minimum doubleheader sessions ..."
CRIT_DH_REGEX='^Critical: .*minimum (DH|doubleheader)'

# Match: "Critical: Teams below target games: ['B2', 'B4', ...]"
CRIT_BELOW_TARGET_REGEX='^Critical: Teams below target games:'

count_below_target_teams() {
  # Reads full OUTPUT on stdin, prints integer count
  local line inside
  line="$(grep -m1 -E "$CRIT_BELOW_TARGET_REGEX" || true)"
  [[ -z "$line" ]] && { echo 0; return; }

  # Extract content inside [ ... ]
  inside="$(sed -n "s/^Critical: Teams below target games: *\\(\\[.*\\]\\).*/\\1/p" <<<"$line")"
  [[ -z "$inside" ]] && { echo 0; return; }

  # If it's [] => 0
  if [[ "$inside" =~ ^\[[[:space:]]*\]$ ]]; then
    echo 0
    return
  fi

  # Count items by counting comma-separated tokens inside brackets.
  # This works with both "['A','B']" and "['A', 'B']" styles.
  inside="${inside#[}"   # strip leading [
  inside="${inside%]}"   # strip trailing ]
  # split by comma and count non-empty chunks
  awk -v s="$inside" 'BEGIN{
    n=0;
    split(s,a,",");
    for(i in a){
      gsub(/^[ \t\r\n]+|[ \t\r\n]+$/, "", a[i]);
      if(a[i]!="") n++;
    }
    print n;
  }'
}

cleanup_outputs() {
  rm -f softball_schedule.csv softball_schedule.xlsx
}

for ((i=1; i<=MAX_TRIES; i++)); do
  echo "=== Attempt $i/$MAX_TRIES ==="

  # Prevent a previous attempt's outputs from "leaking" into this attempt.
  cleanup_outputs

  LOG="$OUTDIR/attempt_${i}.log"

  # Run and capture combined output
  set +e
  OUTPUT="$($PY "$SCRIPT" 2>&1 | tee "$LOG")"
  RC=${PIPESTATUS[0]}
  set -e

  # Extract seed if present (nice for reproducibility)
  SEED="$(echo "$OUTPUT" | sed -n 's/^Using RNG seed: \([0-9]\+\).*/\1/p' | head -n1)"
  [[ -z "$SEED" ]] && SEED="noseed"

  # If the scheduler crashed, log it, remove any partial outputs, and keep going
  if [[ $RC -ne 0 ]]; then
    echo "Attempt $i failed (exit $RC). See $LOG (seed=$SEED)"
    cleanup_outputs
    continue
  fi

  # 1) Fail if DH critical line exists
  if echo "$OUTPUT" | grep -Eq "$CRIT_DH_REGEX"; then
    echo "Attempt $i: DH minimum critical line present. (seed=$SEED)"
    cleanup_outputs
    continue
  fi

  # 2) Fail if below-target list has >= 10 teams
  BELOW_COUNT="$(echo "$OUTPUT" | count_below_target_teams)"
  if [[ "$BELOW_COUNT" -ge 10 ]]; then
    echo "Attempt $i: Teams below target games = $BELOW_COUNT (>=10). (seed=$SEED)"
    cleanup_outputs
    continue
  fi

  echo "✅ Success on attempt $i (seed=$SEED). DH critical line absent, below-target teams=$BELOW_COUNT (<10)."
  [[ -f softball_schedule.csv  ]] && mv -f softball_schedule.csv  "$OUTDIR/SUCCESS_attempt_${i}_seed_${SEED}.csv"
  [[ -f softball_schedule.xlsx ]] && mv -f softball_schedule.xlsx "$OUTDIR/SUCCESS_attempt_${i}_seed_${SEED}.xlsx"
  exit 0
done

echo "❌ No success after $MAX_TRIES attempts. Logs in: $OUTDIR"
exit 2
