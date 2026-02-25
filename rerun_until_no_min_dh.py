#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

CRIT_RE = re.compile(r"^Critical: .*minimum (DH|doubleheader)", re.IGNORECASE | re.MULTILINE)
SEED_RE = re.compile(r"^Using RNG seed:\s*(\d+)\s*$", re.MULTILINE)

def run_once(py: str, script: str) -> tuple[int, str]:
    p = subprocess.run([py, script], capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")

def main():
    py = os.environ.get("PY", "python3.11")
    script = os.environ.get("SCRIPT", "scheduler_newest.py")
    max_tries = int(os.environ.get("MAX_TRIES", "500"))
    outdir = Path(os.environ.get("OUTDIR", f"runs_{datetime.now().strftime('%Y%m%d_%H%M%S')}"))
    outdir.mkdir(parents=True, exist_ok=True)

    for i in range(1, max_tries + 1):
        print(f"=== Attempt {i}/{max_tries} ===")
        rc, out = run_once(py, script)

        log = outdir / f"attempt_{i}.log"
        log.write_text(out, encoding="utf-8", errors="replace")

        if rc != 0:
            print(f"Attempt {i} crashed (exit {rc}). See {log}")
            continue

        seed = "noseed"
        m = SEED_RE.search(out)
        if m:
            seed = m.group(1)

        has_crit = bool(CRIT_RE.search(out))
        # move produced schedules out of the way (so next run doesn't overwrite)
        def move_if_exists(src: str, dst: Path):
            if Path(src).exists():
                shutil.move(src, dst)

        if has_crit:
            print(f"Attempt {i}: still has minimum-DH critical line (seed={seed}).")
            move_if_exists("softball_schedule.csv",  outdir / f"attempt_{i}_seed_{seed}.csv")
            move_if_exists("softball_schedule.xlsx", outdir / f"attempt_{i}_seed_{seed}.xlsx")
            continue

        print(f"✅ Success on attempt {i} (seed={seed}).")
        move_if_exists("softball_schedule.csv",  outdir / f"SUCCESS_attempt_{i}_seed_{seed}.csv")
        move_if_exists("softball_schedule.xlsx", outdir / f"SUCCESS_attempt_{i}_seed_{seed}.xlsx")
        return

    raise SystemExit(f"❌ No success after {max_tries} attempts. Outputs in {outdir}")

if __name__ == "__main__":
    main()
