# WMSL Schedule Builder

A scheduling tool for the Willoughby Mixed Slo-Pitch Association. It builds a full
season schedule from three CSV files, respecting team availability, blackout dates
and field inventory, and favouring 4-team **doubleheader pods** over single games.

Runs as a local web app: edit season settings in the browser, upload your CSVs,
generate a schedule, review it, and download an XLSX.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Using the App](#using-the-app)
- [Input CSV Formats](#input-csv-formats)
- [Configuration Reference](#configuration-reference)
- [Understanding the Output](#understanding-the-output)
- [Capacity Planning](#capacity-planning) — read this before blaming the scheduler
- [Deployment](#deployment)
- [Project Structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Quick Start

Requires **Python 3.9+** (developed on 3.14).

```bash
pip install -r requirements.txt
```

```bash
python app.py
```

Then open <http://localhost:5000>.

The dev server runs with `debug=True` and auto-reloads on code changes. See
[Deployment](#deployment) before exposing it to anyone else.

---

## Using the App

The page is a four-step accordion.

### 1. Season Configuration

Set divisions, per-division rules, weekly pacing and Sunday handling. Hover any
field label for an explanation. Save named presets and reload them later — configs
live as JSON in `configs/`.

### 2. Upload CSV Files

Drag and drop (or browse for) the three input files. See
[Input CSV Formats](#input-csv-formats). The app reports how many rows it read from each.

### 3. Run Scheduler

Generates the schedule. With **Attempts > 1** it runs repeatedly with different random
seeds and keeps the best result, showing progress as it goes.

### 4. Results & Download

- Summary tiles: games short, idle weeks, overloaded weeks, violations
- **Team Summary** — games, home/away split, doubleheader days, playable dates
- **Games Per Week** — each team's rhythm; green on pace, yellow over target, red idle,
  grey column = a week with too few diamonds to seat the league
- **Matchup Matrix** — how many times each pair of teams meets
- **Schedule Preview** — first 100 games
- Downloads: XLSX, CSV, unscheduled matchups, remaining needs

Output files are suffixed with the config name, e.g. `softball_schedule_wmsl-fall-2026.xlsx`.

---

## Input CSV Formats

All three files need a header row (its contents are ignored — only column position matters).

### `team_availability.csv`

Which **days of the week** each team can play. The first column is the team name;
everything after it is treated as day tokens.

```csv
Team,Days
A1,Mon,Wed,Fri
A2,Tue,Thu,Sat
B1,Mon,Thu,Sun
```

The parser is deliberately forgiving. All of these work:

- Separate columns — `A1,Mon,Wed,Fri`
- One delimited cell — `A1,"Mon;Wed;Fri"` or `A1,"Mon, Wed, Fri"`
- Space separated — `A1,"Mon Wed Fri"`
- Full day names — `Monday` is normalised to `Mon`

Unrecognised tokens are ignored silently, so check the load count reported after upload.

> **Team names encode the division.** The first character is the division letter:
> `A1`–`A6` are Division A, `B1`–`B8` are Division B. Team counts in the config
> generate these names, so they must line up with your CSV.

### `field_availability.csv`

Every available slot, one row per date + time + diamond. **This file defines your
entire capacity** — the scheduler can never place more games than there are rows here.

```csv
Date,Time,Diamond
2026-08-17,6:30 PM,Diamond 1
2026-08-17,6:30 PM,Diamond 2
2026-08-17,7:50 PM,Diamond 1
2026-08-17,7:50 PM,Diamond 2
```

- **Date** — `YYYY-MM-DD`
- **Time** — 12-hour with a space, e.g. `6:30 PM`, `10:30 AM` (`%I:%M %p`).
  Malformed times sort to the end of the day rather than failing the run.
- **Diamond** — any label

Duplicate `(date, time, diamond)` rows are removed automatically.

> **Doubleheader pods need two back-to-back times on two diamonds** — four rows per pod.
> A date with only one time slot can never host a pod.

### `team_blackouts.csv`

Dates a team cannot play. First column is the team; the rest are dates. Teams with no
blackouts can be omitted entirely.

```csv
Team,Blackoutdates
A6,2026-04-07,2026-04-09,2026-04-15
B7,2026-04-09,2026-04-10
```

Dates are `YYYY-MM-DD`.

---

## Configuration Reference

### Divisions

| Setting | Meaning |
|---|---|
| **Name** | Single letter. Team names are generated from it (`B` + 8 teams → `B1`–`B8`). |
| **Teams** | Team count. For pods, multiples of 4 fit best. |
| **Target** | Games per team for the season. Must be even if **DH Only** is ticked. |
| **Min DH** | Minimum doubleheader *days* per team (a DH day = 2 games). |
| **Max DH** | Maximum doubleheader days per team. |
| **Pair Min** | Minimum times each pair of teams should meet. Auto-lowered if infeasible. |
| **Pair Cap** | Soft ceiling on repeat meetings between the same two teams. |
| **DH Only** | Division plays *only* pod doubleheaders — never single games. |
| **Inter** | Allows cross-division play. Has no effect on its own (see below). |

#### `DH Only` requires team count divisible by 4

A pod seats exactly 4 teams, so a pure-pod division needs `teams × DH days` to divide
by 4. An 8-team division works cleanly. A 6-team division does **not**:
`6 × 7 = 42`, and `42 ÷ 4 = 10.5` — so two teams will always finish short. For odd
sizes, leave **DH Only** off and set `Min DH = Max DH` instead; doubleheaders formed
from two singles on the same day escape the divide-by-4 constraint.

#### Inter-division play needs three things

Cross-division games only happen when **all** of these are true:

1. **Inter** ticked on *both* divisions
2. The pair enabled under **Inter-Division Play**
3. That pair's games-per-team above 0

A **DH Only** division cannot play cross-division games — it builds its own pod
matchups. The app warns you when a combination can't produce games.

### General

| Setting | Meaning |
|---|---|
| **Weekly Game Limit** | **Hard** ceiling on games per team per week. |
| **Home/Away Balance** | Target home games per team (usually Target ÷ 2). |
| **Hard Min Gap** | Minimum days between a team's game dates. |
| **Preferred Min Gap** | Soft preference; closer games are penalised, not blocked. |
| **Longest Layoff (days)** | Target *maximum* days between a team's games. Default 14 (at most one empty week). |
| **Fill First N Weeks** | Fill the opening N weeks before spreading out. `0` = off. |
| **Attempts** | Run N times with different seeds, keep the best. |
| **Random Seed** | Blank = random. Enter a reported seed to reproduce a schedule exactly. |

### Weekly Pacing

`Weekly Game Limit` is a hard ceiling. These control the *shape* of the season —
without them, raising the limit produces 4-game weeks next to empty ones.

| Setting | Meaning |
|---|---|
| **Target Games / Week** | What the scheduler aims for. Blank = derived from total games ÷ usable weeks. Weeks too small to host the league are excluded from that maths. |
| **Even Weeks vs More Games** | How hard to push for even pacing. See the levels below. |

Packing games in fills more of the calendar; spreading them out leaves some unplaced.
The setting picks where you sit between those. Measured on a tight 6-week season:

| Level | Stored value | Games short | 4-game weeks |
|---|---|---|---|
| **Off** — most games, lumpy weeks | `0` | 4 | 32 |
| **Light** — trim the worst pile-ups | `500` | 10 | 14 |
| **Moderate** — noticeably steadier | `1500` | 12 | 6 |
| **Strong** — steadiest weeks, fewest games | `2500` | 14 | 5 |

Your own numbers will differ; the shape of the trade-off won't.

**Pair it with Longest Layoff.** The two settings solve different halves of the same
problem — pacing stops games bunching into one week, the layoff target stops them
drifting too far apart — and they reinforce each other rather than competing:

| | Games short | Worst layoff | Teams over target |
|---|---|---|---|
| Neither | 4 | 20 days | 5 |
| Layoff target only | 8 | 17 days | 4 |
| Pacing only | 14 | 16 days | 1 |
| **Both** | **12** | **14 days** | **0** |

Note the last row places *more* games than pacing alone: breaking up a long layoff
usually means filling a slot that would otherwise have gone unused.

It is a **single dial** driving placement scoring, the pod gate and best-of-N selection
together — **Off** is a genuine revert to unpaced behaviour. `2500` is full strength;
values above it are clamped and behave identically. Configs holding an off-scale value
(for example from the earlier 0–5000 scale) still load, shown as a *Custom* entry.

### Sunday Pods

| Setting | Meaning |
|---|---|
| **Pods per Sunday** | Hard cap on pods across all divisions on one Sunday. Each pod needs 2 adjacent times × 2 diamonds. |
| **Division Rotation** | Comma-separated order. Each Sunday is assigned to the next division, which gets first claim on that Sunday's first pod. |
| **Sunday Preference** | Normal / Prefer / Strongly prefer. Sundays are already filled before weekdays by default, so this only bites when Sunday slots would otherwise go unused. |
| **Sundays are doubleheaders only** | Never place single games on a Sunday, so a lone game can't occupy half a back-to-back pair and block a pod. |

> Sundays are already scanned before weekdays by default. If your Sundays are already
> fully used, **Sunday Priority** will not change anything — there is nothing left to prioritise.

---

## Understanding the Output

### Summary tiles

| Tile | Meaning |
|---|---|
| **Total Games** | Games actually placed. |
| **Games Short of Target** | Summed across all teams. `0` means every team hit its target. |
| **Idle Weeks** | Team-weeks with no games, excluding weeks too small to seat the league. |
| **Overloaded Weeks** | Team-weeks above the weekly target. |
| **Violations** | Games scheduled against availability or blackouts. **Should always be 0.** |

### Files

| File | Contents |
|---|---|
| `softball_schedule_<config>.xlsx` | Full workbook — schedule, per-team stats, diagnostics |
| `softball_schedule_<config>.csv` | One row per field slot, including unfilled ones |
| `unscheduled_matchups_<config>.csv` | Matchups that could not be placed |
| `team_remaining_needs_<config>.csv` | Per-team shortfall against target |

Written to `output/`, **cleared at the start of every run** — download anything you
want to keep before regenerating.

---

## Capacity Planning

Most "the scheduler is broken" reports are really capacity problems. Check these first.

### 1. Season length × weekly limit caps every team

```
max games per team = season weeks × Weekly Game Limit
```

A 6-week season with a limit of 2 caps every team at **12 games**. A 14-game target
is then unreachable no matter what else you change. Either lengthen the season, raise
the weekly limit, or lower the target.

### 2. Slot utilisation

```
games needed = teams × target ÷ 2
```

Compare that to the row count of `field_availability.csv`. Above roughly **85%**
utilisation the schedule gets very hard to complete, because availability and gap
rules mean not every slot can host every matchup. At 95% almost every slot has to be
filled perfectly.

### 3. Per-week capacity

A week with few diamonds cannot give every team a game, and no setting fixes that.
The **Games Per Week** grid greys out those columns — idle weeks there are unavoidable.

### 4. Pods need adjacent slots

A pod requires 2 back-to-back times on 2 diamonds. Dates with a single time slot can
host single games only.

### When everything is tight, settings become trade-offs

At high utilisation there is no slack, so every constraint you add costs games
elsewhere. Adding dates or diamonds is the only change that buys options instead of
trading one problem for another.

---

## Deployment

### ⚠️ Run exactly one worker process

Run state is held **in memory** in a module-level dict, guarded by a `threading.Lock`,
with the scheduler on a background thread. Under multiple worker processes,
`/api/status` and `/api/results` would hit a worker that never ran the job and appear
to hang or return nothing.

**Always deploy with a single worker.** The workload is one league admin generating a
schedule occasionally, so this is not a practical limitation.

### Local network / small production

Do **not** ship `app.run(debug=True)` — the debugger allows remote code execution.

Windows:

```bash
pip install waitress && waitress-serve --listen=0.0.0.0:5000 --threads=4 app:app
```

Linux / macOS:

```bash
pip install gunicorn && gunicorn --workers 1 --threads 4 --timeout 300 --bind 0.0.0.0:5000 app:app
```

`--workers 1` is required. `--timeout 300` matters because a multi-attempt run can
take minutes.

### Cloud (Render, Railway, Fly.io, Azure App Service …)

Start command:

```bash
gunicorn --workers 1 --threads 4 --timeout 300 --bind 0.0.0.0:$PORT app:app
```

Before deploying, be aware of these:

- **Ephemeral filesystem.** `configs/`, `uploads/` and `output/` are local directories.
  On most platforms they are wiped on restart or redeploy, so **saved configs will not
  survive**. Attach a persistent volume, or move configs to object storage / a database.
- **Request timeouts.** Many platforms cap requests at 30–60s. Scheduler runs are
  already backgrounded and polled, so this is usually fine — but raise the server
  timeout as above.
- **No authentication.** The app has no login. Anyone who can reach it can upload files
  and generate schedules. Put it behind auth or restrict it to a private network.
- **Memory.** A run holds the full schedule in memory. Comfortable within a 512 MB instance.

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
RUN mkdir -p configs uploads output
EXPOSE 5000
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--timeout", "300", "--bind", "0.0.0.0:5000", "app:app"]
```

Persist configs across restarts:

```bash
docker run -p 5000:5000 -v wmsl-configs:/app/configs wmsl-scheduler
```

---

## Project Structure

```
├── app.py                  # Flask app — routes and API
├── scheduler_wrapper.py    # Bridge: applies config, runs attempts, scores results
├── scheduler_newest.py     # Scheduling engine
├── requirements.txt
├── templates/index.html    # Single-page UI
├── static/js/app.js        # Frontend logic
├── configs/                # Saved season presets (JSON)
├── uploads/                # Most recently uploaded CSVs
└── output/                 # Generated files (cleared each run)
```

### API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/config/defaults` | Default configuration |
| GET / POST / DELETE | `/api/configs/<name>` | Load / save / delete a preset |
| GET | `/api/configs` | List presets |
| POST | `/api/validate` | Config warnings (advisory) |
| POST | `/api/upload` | Upload the three CSVs |
| POST | `/api/run` | Start a run (background) |
| GET | `/api/status` | Poll status, log and attempt progress |
| GET | `/api/results` | Stats, weekly table, matrix, preview |
| GET | `/api/download/<file>` | Download an output file |

### Legacy files

`scheduler.py`, `scheduler_new.py`, `scheduler_v2.py`, `index.html`,
`doubleheaders.csv`, `reschedule_availability.csv` and `rerun_until_no_min_dh.*`
are from earlier iterations and are **not used** by the web app. They are kept for
reference and can be removed.

`max_retries` still appears in saved configs but is vestigial — the backtracking loop
it governed was replaced by bounded multi-pass greedy filling. It is no longer shown
in the UI.

---

## Troubleshooting

**Teams far short of target.** Work through [Capacity Planning](#capacity-planning).
Check season weeks × weekly limit first — it is the most common cause.

**Empty slots left in the schedule.** Normal when constraints prevent a legal matchup
in that slot. `unscheduled_matchups_*.csv` lists what could not be placed.

**Violations above 0.** Should never happen; it means a game was scheduled against
availability or a blackout. Treat as a bug and report the config and CSVs.

**Config saved but settings look wrong on reload.** Presets store only known fields.
Configs saved before a setting existed pick up its default on load.

**"A scheduler run is already in progress" (HTTP 409).** Only one run at a time. Wait
for it to finish; state resets automatically.

**Port 5000 already in use.** Another process (often a previous instance) holds it.
Stop it, or change the port at the bottom of `app.py`.

**Schedules differ between runs.** Expected — each attempt uses a random seed. The
results banner reports the winning seed; enter it under **Random Seed** to reproduce
that exact schedule.

---

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
