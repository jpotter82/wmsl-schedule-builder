#!/usr/bin/env bash
set -euo pipefail

SCRIPT="${1:-scheduler_newest.py}"     # or scheduler_new.py / scheduler_newest.py
PY="${PY:-python3.11}"
MAX_TRIES="${MAX_TRIES:-5000}"         # bump this up when you want
OUTDIR="${OUTDIR:-runs_$(date +%Y%m%d_%H%M%S)}"

mkdir -p "$OUTDIR"

# Detect DH-minimum critical line
CRIT_DH_REGEX='^Critical: .*minimum (DH|doubleheader)'

# Detect below-target line
CRIT_BELOW_TARGET_REGEX='^Critical: Teams below target games:'

cleanup_outputs() {
  rm -f softball_schedule.csv softball_schedule.xlsx
}

extract_seed() {
  # stdin: OUTPUT -> prints seed or "noseed"
  local seed
  seed="$(sed -n 's/^Using RNG seed: \([0-9]\+\).*/\1/p' | head -n1 || true)"
  [[ -z "${seed:-}" ]] && seed="noseed"
  echo "$seed"
}

count_below_target_teams() {
  # stdin: full OUTPUT -> prints integer count
  local line inside
  line="$(grep -m1 -E "$CRIT_BELOW_TARGET_REGEX" || true)"
  [[ -z "$line" ]] && { echo 0; return; }

  inside="$(sed -n "s/^Critical: Teams below target games: *\\(\\[.*\\]\\).*/\\1/p" <<<"$line")"
  [[ -z "$inside" ]] && { echo 0; return; }

  if [[ "$inside" =~ ^\[[[:space:]]*\]$ ]]; then
    echo 0
    return
  fi

  inside="${inside#[}"
  inside="${inside%]}"
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

BEST_COUNT=999999
BEST_SEED="none"
BEST_ATTEMPT=0

BEST_CSV="$OUTDIR/BEST.csv"
BEST_XLSX="$OUTDIR/BEST.xlsx"
BEST_LOG="$OUTDIR/BEST.log"

for ((i=1; i<=MAX_TRIES; i++)); do
  echo "=== Attempt $i/$MAX_TRIES ==="

  # Prevent any old outputs from leaking into this attempt
  cleanup_outputs

  LOG="$OUTDIR/attempt_${i}.log"

  set +e
  OUTPUT="$($PY "$SCRIPT" 2>&1 | tee "$LOG")"
  RC=${PIPESTATUS[0]}
  set -e

  SEED="$(echo "$OUTPUT" | extract_seed)"

  # Crash => discard outputs and continue
  if [[ $RC -ne 0 ]]; then
    echo "Attempt $i failed (exit $RC). (seed=$SEED) See $LOG"
    cleanup_outputs
    continue
  fi

  # DH-minimum violation => discard outputs and continue
  if echo "$OUTPUT" | grep -Eq "$CRIT_DH_REGEX"; then
    echo "Attempt $i rejected: DH minimum critical present. (seed=$SEED)"
    cleanup_outputs
    continue
  fi

  BELOW_COUNT="$(echo "$OUTPUT" | count_below_target_teams)"
  echo "Attempt $i OK on DH. Teams below target games = $BELOW_COUNT (seed=$SEED)"

  # If this attempt didn't even produce a schedule file, treat it as unusable
  if [[ ! -f softball_schedule.csv && ! -f softball_schedule.xlsx ]]; then
    echo "Attempt $i produced no schedule files; skipping save."
    continue
  fi

  # New best?
  if [[ "$BELOW_COUNT" -lt "$BEST_COUNT" ]]; then
    BEST_COUNT="$BELOW_COUNT"
    BEST_SEED="$SEED"
    BEST_ATTEMPT="$i"

    # Overwrite BEST artifacts
    [[ -f softball_schedule.csv  ]] && cp -f softball_schedule.csv  "$BEST_CSV"
    [[ -f softball_schedule.xlsx ]] && cp -f softball_schedule.xlsx "$BEST_XLSX"
    cp -f "$LOG" "$BEST_LOG"

    echo "🏆 New BEST: below-target=$BEST_COUNT (attempt=$BEST_ATTEMPT seed=$BEST_SEED)"
  fi

  # Moon shot: perfect schedule on this metric
  if [[ "$BELOW_COUNT" -eq 0 ]]; then
    echo "🌕 Moon shot hit! 0 teams below target (attempt=$i seed=$SEED)."
    # Move the exact success artifacts (optional; BEST already has them)
    [[ -f softball_schedule.csv  ]] && mv -f softball_schedule.csv  "$OUTDIR/SUCCESS_attempt_${i}_seed_${SEED}.csv"
    [[ -f softball_schedule.xlsx ]] && mv -f softball_schedule.xlsx "$OUTDIR/SUCCESS_attempt_${i}_seed_${SEED}.xlsx"
    exit 0
  fi

  # Not a success => remove attempt outputs (keep only BEST copies)
  cleanup_outputs
done

echo "❌ No perfect run after $MAX_TRIES attempts."
echo "Best found: below-target=$BEST_COUNT (attempt=$BEST_ATTEMPT seed=$BEST_SEED)"
echo "Best artifacts saved as:"
echo "  $BEST_CSV"
echo "  $BEST_XLSX"
echo "  $BEST_LOG"
exit 2
