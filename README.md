# skedworx

**Schedules that work for your league.**

skedworx builds a full season schedule from three CSV files, respecting team
availability, blackout dates and field inventory, and favouring 4-team
**doubleheader pods** over single games.

Sign up, configure your season in the browser, upload your CSVs, generate a
schedule, review it, and download an XLSX.

Originally built for the Willoughby Mixed Slo-Pitch Association, whose season is
used throughout this document as a worked example. Nothing in the scheduler is
specific to slo-pitch — it works for anything with teams, venues and time slots.

| | |
|---|---|
| `/` | Public landing page |
| `/app` | The scheduler (sign-in required) |
| `/login`, `/register` | Accounts |

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

Then open <http://localhost:5000>. Create an account, and you'll land in the
scheduler at `/app`.

The first account created becomes the admin, and inherits any configs and uploads
left over from before accounts existed.

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
- **Games Per Week** — each team's rhythm; green on pace, amber over target, blue idle,
  grey column = a week with too few diamonds to seat the league. Each cell also carries
  a marker and screen-reader text, so the state does not depend on colour alone
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

### Set `SKEDWORX_SYNC_RUNS=1` when deploying

Run state is held **in memory** in this process. By default the scheduler runs on a
background thread and the browser polls for progress, which is fine locally but breaks
on any host that runs several worker processes or recycles idle ones (cPanel/Passenger,
multi-worker gunicorn): the status poll can land on a process that never ran the job,
and the UI hangs.

Setting `SKEDWORX_SYNC_RUNS=1` runs the scheduler inside the request instead. There is no
downside — a full 15-attempt run takes **about 1.6 seconds**, well inside any request
timeout — and it removes the failure mode completely. The UI handles both modes.

`passenger_wsgi.py` sets it automatically.

### Shared hosting with Setup Python App (Passenger)

If cPanel → Software has **Setup Python App**, use it — it is faster than CGI because
the process stays warm.

1. Setup Python App → Create; Python **3.9+**; set application root and URL
2. Upload the repository to the application root
3. In the app's virtualenv: `pip install -r requirements.txt`
4. `passenger_wsgi.py` is already included — Passenger picks it up automatically
5. Make sure `configs/`, `uploads/` and `output/` exist and are writable
6. Restart the app from cPanel

### Shared hosting without Setup Python App (CGI)

Many entry-level cPanel plans have no Python app manager. If SSH/Terminal and CGI are
available, the app runs over CGI instead — a fresh process per request, which is fine
here because runs are short and state is on disk.

```bash
cd ~/public_html/wmsl                 # or wherever the app should live
git clone https://github.com/jpotter82/wmsl-schedule-builder.git .
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .htaccess.example .htaccess
cp dispatch.cgi.example dispatch.cgi
chmod 755 dispatch.cgi
mkdir -p configs uploads output && chmod 755 configs uploads output
```

Then point the shebang at an **absolute** interpreter path, because CGI does not
inherit your shell's PATH:

```bash
set +H
sed -i "1s|.*|#!$(pwd)/venv/bin/python|" dispatch.cgi
head -1 dispatch.cgi                  # confirm it is an absolute path
```

`set +H` matters: without it, interactive bash treats the `!` in `#!` as a history
expansion and the command dies with `event not found`. If you are not using a
virtualenv, write the system interpreter literally in **single** quotes instead,
which sidesteps the same trap:

```bash
sed -i '1s|.*|#!/usr/bin/python3.9|' dispatch.cgi
```

Check it runs before involving the browser. CGI scripts read their input from the
environment, so a bare `./dispatch.cgi` only produces a `KeyError: 'REQUEST_METHOD'`
— that is the harness missing, not a broken app:

```bash
env REQUEST_METHOD=GET SCRIPT_NAME=/ PATH_INFO=/ QUERY_STRING= \
    SERVER_NAME=localhost SERVER_PORT=80 SERVER_PROTOCOL=HTTP/1.1 \
    GATEWAY_INTERFACE=CGI/1.1 ./dispatch.cgi </dev/null | head -5
```

Expect `Status: 200 OK` and headers. A traceback here is a real fault; nothing at all
usually means the shebang is wrong or the file is not executable.

Once it works, comment out the `cgitb.enable()` line in `dispatch.cgi` so internal
errors are not shown to visitors.

Shared hosting has one real advantage over free cloud tiers: a **persistent
filesystem**, so saved configs survive restarts.

### Updating a CGI deployment

`dispatch.cgi` and `.htaccess` are **gitignored on purpose**. Both need host-specific
edits — the interpreter path, the data directory — so keeping them untracked means
`git pull` cannot overwrite your deployment.

There is one sharp edge. `dispatch.cgi` used to be tracked. The commit that untracked
it records a deletion, so the first `git pull` past that commit **removes the file
from the working tree** on any host cloned before it. The site then returns 500 with
nothing useful in the log, because the entry point is simply gone. Recreate it:

```bash
cd ~/skedworx && cp dispatch.cgi.example dispatch.cgi \
  && sed -i '1s|.*|#!/usr/bin/python3.9|' dispatch.cgi \
  && chmod 755 dispatch.cgi && head -1 dispatch.cgi
```

Because that failure mode is silent, verify after **every** pull:

```bash
cd ~/skedworx && git pull origin main && ls -l dispatch.cgi && head -1 dispatch.cgi
```

`dispatch.cgi` must exist, be mode `755`, and start with an absolute interpreter path.
If a pull ever aborts with *"local changes would be overwritten by merge: dispatch.cgi"*,
that is the older tracked copy still in the index — `git rm --cached dispatch.cgi`
resolves it without touching the file on disk.

A pull that changes only `static/` or `templates/` needs no CGI work at all, but do
hard-refresh the browser: CSS and JS are served with normal caching, and a stale cache
looks exactly like a failed deploy.

### Password reset email

A user who forgets their password uses **Forgot password?** on the sign-in page. They
receive a link that works once and expires in an hour; using it signs them out
everywhere else.

Without SMTP configured the link is only written to the error log, so **nobody can
actually recover an account until you set this up**. A cPanel mailbox on your own
domain is enough.

Put the settings in `~/skedworx-secrets.env` (`chmod 600`), **above** the web root —
`dispatch.cgi` loads it if present. `dispatch.cgi` itself sits in the document root, so
a mailbox password written there is one broken handler away from being served as plain
text. `KEY=VALUE` per line, unquoted:

| Variable | Example | |
|---|---|---|
| `SKEDWORX_BASE_URL` | `https://schedule.wmsl.ca` | strongly recommended |
| `SKEDWORX_SMTP_HOST` | `mail.wmsl.ca` | required |
| `SKEDWORX_SMTP_FROM` | `skedworx <skedworx@wmsl.ca>` | required |
| `SKEDWORX_SMTP_PORT` | `587`, or `465` with SSL | default 587 |
| `SKEDWORX_SMTP_USER` | `skedworx@wmsl.ca` | |
| `SKEDWORX_SMTP_PASSWORD` | | |
| `SKEDWORX_SMTP_SSL` | `1` to use SSL instead of STARTTLS | |

Set `SKEDWORX_BASE_URL`. Without it the link is built from the `Host` header, which the
requester controls — someone could trigger a reset for your address and have the mail
arrive pointing at their own site, capturing the token when you clicked it.

Check it from the host before trusting it:

```bash
cd ~/skedworx && python3 check_smtp.py                  # settings, connect, authenticate
cd ~/skedworx && python3 check_smtp.py you@example.com  # ...and send a real message
```

`check_smtp.py` reports what is set (never the password), then connects, negotiates TLS
and logs in, naming the specific failure. Worth using rather than testing through the
reset form: that form is deliberately silent — identical response whether or not the
address exists, and send failures are logged rather than shown — so a broken mailbox
looks exactly like a working one from the browser.

If the test message lands in spam, add SPF and DKIM records for the domain in cPanel.

Check it end to end after configuring, and watch the error log: send failures are
logged rather than shown, because the page is deliberately identical whether or not
the address has an account.

If you are locked out before SMTP works, set a password directly on the host:

```bash
cd ~/skedworx && python3 -c "
import auth
u = auth.get_user_by_email('you@example.com')
auth.set_password(u.user_id, 'a-new-long-password')
print('updated', u.email)"
```

### Run state is shared through a file

`/api/run`, `/api/status` and `/api/results` are three separate requests, and the
process answering the later two is often not the one that ran the scheduler — always
under CGI, intermittently under Passenger or multi-worker gunicorn. Run state is
therefore mirrored to `.run_state.json` (written atomically) and read back from there,
so results survive whichever process happens to serve the next request.

This is also why `SKEDWORX_SYNC_RUNS=1` matters: a background thread would be killed when
a CGI process exits, taking the run with it.

### Local network / small production

Do **not** ship `app.run(debug=True)` — the debugger allows remote code execution.

Windows:

```bash
set SKEDWORX_SYNC_RUNS=1 && pip install waitress && waitress-serve --listen=0.0.0.0:5000 app:app
```

Linux / macOS:

```bash
SKEDWORX_SYNC_RUNS=1 gunicorn --workers 2 --timeout 120 --bind 0.0.0.0:5000 app:app
```

With `SKEDWORX_SYNC_RUNS=1` multiple workers are safe, because no state has to survive
between requests.

### Cloud (Render, Railway, Fly.io, Azure App Service …)

Start command:

```bash
SKEDWORX_SYNC_RUNS=1 gunicorn --workers 2 --timeout 120 --bind 0.0.0.0:$PORT app:app
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
ENV SKEDWORX_SYNC_RUNS=1
CMD ["gunicorn", "--workers", "2", "--timeout", "120", "--bind", "0.0.0.0:5000", "app:app"]
```

Persist configs across restarts:

```bash
docker run -p 5000:5000 -v wmsl-configs:/app/configs wmsl-scheduler
```

---

## Branding

The palette, typography and logo come from the skedworx brand sheet. Colours live as
CSS variables in `static/css/skedworx.css`, and nothing hard-codes a hex outside that
file, so a palette change lands everywhere at once.

| Token | Value | Brand name | Use |
|---|---|---|---|
| `--brand` | `#42D32A` | Primary Green | fills, borders, large shapes — **never text** |
| `--brand-ink` | `#288019` | derived | text, buttons, links |
| `--brand-700` | `#216B15` | derived | hover |
| `--brand-300` / `--accent` | `#78C043` | Accent Green | |
| `--ink` | `#0F1720` | Deep Navy | body text |
| `--ink-600` | `#64748B` | Slate | muted text |
| `--surface` | `#F3F4F6` | Light Gray | |
| `--surface-2` | `#FFFFFF` | White | |

**Why there are two greens.** The sheet's Primary Green `#42D32A` is a bright lime. It
measures **1.98:1** as text on white, and the same against white text on top of it —
far below the 4.5:1 WCAG AA floor. It is the identity colour and belongs on fills and
borders, where contrast rules do not apply. Anything carrying text uses `--brand-ink`,
the same hue and saturation darkened until it clears AA on white (5.01:1) and on the
grey surface (4.55:1). Putting `--brand` behind a button label is the one mistake this
palette makes easy, so check any new rule that pairs green with `#fff`.

Slate is `#64748B`, not the `#6474BB` printed on the sheet — that value is a
periwinkle blue rather than a grey, and reads as a transcription slip. Using it as
written turns muted body copy purple across every page.

Typography is **Poppins** (400/500/600/700) from Google Fonts, with a system stack
fallback.

### Logo assets

The logo is used as supplied rather than redrawn, so no fidelity is lost. Drop the
artwork in at these paths:

| File | Used by | Source |
|---|---|---|
| `static/img/skedworx-hero.png` | Landing page headline | `brand/hero-img.png` |
| `static/img/skedworx-icon.png` | Nav and compact placements | supplied at final size |
| `static/img/favicon.png` | Browser tab | `static/img/skedworx-icon.png` |
| `static/img/spreadsheet-chaos.jpg` | Homepage: the problem | `brand/spreadsheet_solving.png` |
| `static/img/home-plate.jpg` | Homepage: how it fits together | `brand/diamond-problem-matrix.png` |

Originals live in `brand/`, outside `static/`, so the full-resolution art and the
brand sheet are not served over the web. The versions under `static/img/` are resized
to their actual display size — the raw drops total 5.4 MB, the shipped set 583 KB,
which matters on a landing page.

The two marketing illustrations ship as **JPEG**: they are detailed, near-photographic
artwork where PNG lands around 480 KB each. The hero lockup stays **PNG-8**, because it
is flat colour and JPEG rings visibly around the letterforms.

The nav pairs the diamond mark with the wordmark set in Poppins rather than using the
full lockup: it renders about 34px tall, where "schedules that work for your league"
would be a few pixels high. In the wordmark `sked` is the deep navy and `worx` is
green, matching the logo.

Regenerate the web assets after changing the source art:

```bash
python - <<'EOF'
from PIL import Image
def flat(src, box):
    im = Image.open(src).convert('RGBA'); im.thumbnail(box, Image.LANCZOS)
    bg = Image.new('RGB', im.size, (255, 255, 255)); bg.paste(im, mask=im.split()[3])
    return bg

for src, dst, box in (('brand/spreadsheet_solving.png',    'static/img/spreadsheet-chaos.jpg', (1200, 1200)),
                      ('brand/diamond-problem-matrix.png', 'static/img/home-plate.jpg',        (1000, 1000))):
    flat(src, box).save(dst, quality=90, optimize=True, progressive=True)

flat('brand/hero-img.png', (960, 960)).quantize(colors=128).save(
    'static/img/skedworx-hero.png', optimize=True)

ic = Image.open('static/img/skedworx-icon.png').convert('RGBA')
ic.thumbnail((64, 64), Image.LANCZOS); ic.save('static/img/favicon.png', optimize=True)
EOF
```

SVG works too — change the filename in `templates/wordmark.html`, which is the single
definition every page includes.

If an asset is missing, the image hides itself and a styled text wordmark takes over,
so a fresh checkout shows the brand rather than a broken-image icon.

---

## Project Structure

```
├── app.py                  # Flask app — routes and API
├── auth.py                 # Accounts, sessions, per-user storage
├── scheduler_wrapper.py    # Bridge: applies config, runs attempts, scores results
├── scheduler_newest.py     # Scheduling engine
├── dispatch.cgi            # CGI entry point (shared hosting)
├── passenger_wsgi.py       # Passenger entry point (cPanel Python app)
├── requirements.txt
├── mailer.py               # Password reset email (smtplib)
├── check_smtp.py           # Diagnose mail config from the host
├── test_password_reset.py  # Reset-flow tests: python test_password_reset.py
├── templates/
│   ├── home.html           # Public landing page
│   ├── index.html          # The scheduler UI
│   ├── wordmark.html       # The logo lockup — defined once, included everywhere
│   ├── login.html, register.html, forgot.html, reset.html, auth_base.html
│   └── icons/              # Inline SVG partials
├── static/
│   ├── css/skedworx.css    # Design tokens shared by every page
│   ├── css/home.css        # Landing page styles
│   ├── img/                # Web-sized assets only (583 KB total)
│   └── js/app.js           # Scheduler frontend
├── brand/                  # Source artwork and brand sheet — NOT web-served
└── data/                   # Accounts, signing key, per-user files (gitignored)
    └── users/<id>/{configs,uploads,output}/
```

### API

All `/api/*` routes require a signed-in session and act only on that user's data.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Public landing page |
| GET | `/app` | The scheduler (sign-in required) |
| GET/POST | `/login`, `/register` | Accounts |
| GET/POST | `/forgot` | Request a password reset link |
| GET/POST | `/reset/<token>` | Choose a new password |
| GET | `/logout` | End the session |
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
