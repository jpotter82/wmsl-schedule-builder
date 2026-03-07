
# --- deterministic day-of-week labels (avoid locale issues) ---
DOWS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']

def dow_label(d):
    """Return fixed 3-letter DOW label for date/datetime."""
    return DOWS[d.weekday()]

# Run seed: None => randomize every run
RUN_SEED = None


def _common_avail_days(team1, team2, team_availability):
    """Return comma-separated DOW labels both teams can play."""
    if not team_availability:
        return ""
    a1 = set(team_availability.get(team1, []))
    a2 = set(team_availability.get(team2, []))
    common = a1 & a2
    if not common:
        return ""
    order = {d:i for i,d in enumerate(DOWS)}
    return ", ".join(sorted(common, key=lambda d: order.get(d, 99)))

def _blackout_summary(team1, team2, team_blackouts, max_dates=30):
    """Return comma-separated blackout dates (YYYY-MM-DD) where either team cannot play."""
    if not team_blackouts:
        return ""
    b1 = set(team_blackouts.get(team1, []))
    b2 = set(team_blackouts.get(team2, []))
    dates = sorted(b1 | b2)
    if not dates:
        return ""
    out = [d.strftime("%Y-%m-%d") for d in dates[:max_dates]]
    if len(dates) > max_dates:
        out.append(f"...(+{len(dates)-max_dates} more)")
    return ", ".join(out)

def check_schedule_against_availability(schedule, team_availability):
    """Return list of (date, day, time, field, team, allowed_days) for any availability violations."""
    violations = []
    for (d, time_str, field_id, home, home_div, away, away_div) in schedule:
        if not home or not away:
            continue
        day = dow_label(d)
        for team in (home, away):
            allowed = team_availability.get(team)
            if allowed is None:
                continue
            if day not in allowed:
                violations.append((d.strftime('%Y-%m-%d'), day, time_str, field_id, team, ",".join(sorted(allowed))))
    return violations

#!/usr/bin/env python3
"""
Softball scheduler (heuristic) + Excel export.

Additions in this version:
  - CSV export writes 1 row PER field_availability slot (including unscheduled/blank slots),
    so row count matches field_availability.
  - XLSX export with:
      * Schedule sheet (same rows as field_availability, blanks for unused slots)
      * Teams sheet
      * Summary sheet (all formulas; updates if you edit Schedule)
      * TeamDate helper sheet (for DH-day counting formulas)
      * Matchup Matrix sheet (formula-based, symmetric counts)
      * Conditional formatting (unused slots, illegal matchups, home==away, matrix heatmap)
Requires:
  pip install openpyxl
Optional:
  pip install prettytable
"""

import csv
import itertools
import random
import math
import re
import os
from datetime import datetime
from collections import defaultdict

try:
    from prettytable import PrettyTable
except ImportError:
    PrettyTable = None

# XLSX export support (optional)
try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import FormulaRule
except Exception:
    Workbook = None
    Font = Alignment = PatternFill = get_column_letter = FormulaRule = None

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import FormulaRule, CellIsRule, ColorScaleRule
except ImportError:
    Workbook = None  # handled in export function

# -------------------------------
# Configurable parameters
# -------------------------------
MAX_RETRIES = 20000            # scheduling backtracking limit
PREFERRED_MIN_GAP = 3         # ideal minimum days between game dates (soft preference)
HARD_MIN_GAP = 2              # absolute minimum days between game dates (hard constraint)
WEEKLY_GAME_LIMIT = 3          # max games per team per week
HOME_AWAY_BALANCE = 11         # desired home games per team (for 22-game seasons)
MAX_IDLE_DAYS = 14             # hard target: no more than one open week between game dates
IDLE_GAP_REPAIR_WEIGHT = 1500  # scoring bonus for placements that shrink long layoffs

# Preferred-day / front-loading controls
PREFERRED_DAY_BONUS_BOTH = 400   # both teams are on a preferred day
PREFERRED_DAY_BONUS_ONE = 150    # only one team is on a preferred day
LATE_DATE_PENALTY_PER_DAY = 12   # discourages consuming late-season inventory too early
SUNDAYS_FIRST = False            # keep chronology natural; Sunday preference should come from scoring, not slot order
SEASON_START_DATE = None          # set in main() from field availability
MAX_CONSECUTIVE_BYE_WEEKS = 1     # allow one bye week, but not two straight empty weeks
BYE_URGENCY_WEIGHT = 2500         # scoring bonus for teams at risk of a second consecutive bye week


# Division A opponent-balance controls (A is DH-only)
A_PAIR_MIN_GAMES = 2          # each A-vs-A pairing should occur at least this many times
A_PAIR_SOFT_CAP = 4           # avoid exceeding this for a pair while some required pairs still unmet

# Pairing balance rules (min games per opponent + soft cap to avoid lopsided repeats)
# NOTE: For divisions where the intra_target_per_team makes min infeasible, the code will automatically
# clamp the effective minimum to floor(avg_games_vs_opponent).
PAIR_RULES = {
    'A': {'min': A_PAIR_MIN_GAMES, 'soft_cap': A_PAIR_SOFT_CAP},
    'B': {'min': 2, 'soft_cap': 4},
    'C': {'min': 2, 'soft_cap': 5},  # 6-team divisions naturally have higher repeats
    'D': {'min': 2, 'soft_cap': 5},
}

def effective_pair_rules(division, intra_target_per_team, n):
    """Return (min_eff, cap_eff) for intra-division opponent balance."""
    base = PAIR_RULES.get(division, {'min': 0, 'soft_cap': 999})
    if n <= 1 or intra_target_per_team <= 0:
        return 0, base.get('soft_cap', 999)
    avg = float(intra_target_per_team) / float(max(1, n - 1))
    min_eff = min(int(base.get('min', 0)), int(math.floor(avg)))
    # keep cap at least (ceil(avg)+1) so we don't dead-end in small divisions
    cap_eff = max(int(base.get('soft_cap', 999)), int(math.ceil(avg)) + 1)
    return min_eff, cap_eff



# Sunday pod rotation:
# For Sunday dates, we try to rotate which division gets pod-style doubleheaders.
# This helps avoid one division (e.g., A) soaking up all Sunday capacity.
SUNDAY_POD_ROTATION = ['B', 'C', 'D', 'A']  # cycle order (can change)
SUNDAY_PODS_PER_SUNDAY = 3  # at most this many *pod sessions* across all divisions on a Sunday
RANDOM_SEED = None           # for repeatable schedules
# Per-division configuration (tweak here)
DIVISION_SETTINGS = {
    # A: 22 games, only DH => 11 DH days exactly
    'A': {'inter': False, 'target_games': 22, 'min_dh': 11, 'max_dh': 11},

    # B/C/D: inter allowed, intra can top up as needed
    'B': {'inter': True,  'target_games': 22, 'min_dh': 7,  'max_dh': 10},
    'C': {'inter': True,  'target_games': 22, 'min_dh': 7,  'max_dh': 10},
    'D': {'inter': True,  'target_games': 22, 'min_dh': 7,  'max_dh': 10},
}

# Inter-division pairing settings (only applied if BOTH divisions have inter=True)
INTER_PAIR_SETTINGS = {
    ('A', 'B'): False,
    ('A', 'C'): False,
    ('A', 'D'): False,
    ('B', 'C'): True,
    ('C', 'D'): True,
    ('B', 'D'): False,
}

# “Average per team” targets.
INTER_DEGREE = {
    ('B', 'C'): 4,
    ('C', 'D'): 6,
}

# -------------------------------
# Helpers
# -------------------------------
def div_of(team):
    return team[0].upper()

def target_games(team):
    return DIVISION_SETTINGS[div_of(team)]['target_games']

def min_dh(team):
    return DIVISION_SETTINGS[div_of(team)]['min_dh']

def max_dh(team):
    return DIVISION_SETTINGS[div_of(team)]['max_dh']

DIV_PRIORITY = {'D': 3, 'C': 2, 'B': 1, 'A': 0}

def game_deficit(team, team_stats):
    return max(0, target_games(team) - team_stats[team]['total_games'])

def dh_deficit(team, doubleheader_count):
    return max(0, min_dh(team) - doubleheader_count[team])

def team_need_key(team, team_stats, doubleheader_count):
    return (
        dh_deficit(team, doubleheader_count),
        game_deficit(team, team_stats),
        DIV_PRIORITY.get(div_of(team), 0),
        -team_stats[team]['home_games'],
        team
    )

def matchup_need_score(home, away, team_stats, doubleheader_count):
    return (
        game_deficit(home, team_stats) + game_deficit(away, team_stats)
    ) * 1000 + (
        dh_deficit(home, doubleheader_count) + dh_deficit(away, doubleheader_count)
    ) * 50 + (
        DIV_PRIORITY.get(div_of(home), 0) + DIV_PRIORITY.get(div_of(away), 0)
    )

def inter_enabled_for_pair(d1, d2):
    d1, d2 = d1.upper(), d2.upper()
    key = (d1, d2) if (d1, d2) in INTER_PAIR_SETTINGS else (d2, d1)
    if key not in INTER_PAIR_SETTINGS or not INTER_PAIR_SETTINGS[key]:
        return False
    return DIVISION_SETTINGS[d1]['inter'] and DIVISION_SETTINGS[d2]['inter']

def pair_degree(d1, d2):
    d1, d2 = d1.upper(), d2.upper()
    key = (d1, d2) if (d1, d2) in INTER_DEGREE else (d2, d1)
    return INTER_DEGREE.get(key, 0)

def min_gap_ok(team, d, team_game_days):
    """Hard gap check: return True if 'team' has no game within HARD_MIN_GAP days of date d."""
    for gd in team_game_days[team]:
        if gd != d and abs((d - gd).days) < HARD_MIN_GAP:
            return False
    return True

# -------------------------------
# Availability helpers
# -------------------------------
def dow_abbrev(d):
    """Return 3-letter day abbrev (Mon/Tue/...) for a date or datetime."""
    try:
        return dow_label(d)
    except Exception:
        return str(d)[:3].title()

def is_team_available(team, d, team_availability, team_blackouts):
    """True if team can play on date d according to weekly availability + blackout dates."""
    dd = d if hasattr(d, "weekday") and not hasattr(d, "date") else d.date()
    dow = dow_abbrev(dd)
    if dow not in team_availability.get(team, set()):
        return False
    if dd in team_blackouts.get(team, set()):
        return False
    return True

def preferred_gap_penalty(team, d, team_game_days, penalty_per_day=500):
    """Soft preference penalty when gap is smaller than PREFERRED_MIN_GAP.
    Returns 0 if the closest existing game day is >= PREFERRED_MIN_GAP days away.
    """
    closest = None
    for gd in team_game_days[team]:
        if gd == d:
            continue
        delta = abs((d - gd).days)
        if closest is None or delta < closest:
            closest = delta
    if closest is None:
        return 0
    if closest >= PREFERRED_MIN_GAP:
        return 0
    return (PREFERRED_MIN_GAP - closest) * penalty_per_day

def longest_idle_gap(team, team_game_days):
    """Largest day gap between consecutive game dates already scheduled for a team."""
    dates = sorted(team_game_days[team])
    if len(dates) < 2:
        return 0
    return max((dates[i] - dates[i - 1]).days for i in range(1, len(dates)))


def longest_idle_gap_after_adding(team, d, team_game_days):
    """Largest day gap after hypothetically adding date d for team."""
    dates = sorted(set(team_game_days[team]) | {d})
    if len(dates) < 2:
        return 0
    return max((dates[i] - dates[i - 1]).days for i in range(1, len(dates)))


def idle_gap_repair_bonus(team, d, team_game_days):
    """
    Positive score when placing team on date d shrinks an existing long layoff.
    This works better than a pure hard reject with the current non-chronological
    greedy passes, because later placements can still split a large gap.
    """
    before = longest_idle_gap(team, team_game_days)
    after = longest_idle_gap_after_adding(team, d, team_game_days)
    if after < before:
        bonus = (before - after) * IDLE_GAP_REPAIR_WEIGHT
        if before > MAX_IDLE_DAYS:
            bonus += (before - MAX_IDLE_DAYS) * IDLE_GAP_REPAIR_WEIGHT
        return bonus
    return 0


def check_max_idle_gap(schedule, teams, max_idle_days=MAX_IDLE_DAYS):
    """Return (team, previous_date, next_date, gap_days) for long layoff violations."""
    by_team = defaultdict(set)
    for (dt, _time, _field, home, _home_div, away, _away_div) in schedule:
        dd = dt.date() if hasattr(dt, 'date') else dt
        by_team[home].add(dd)
        by_team[away].add(dd)

    violations = []
    for team in teams:
        dates = sorted(by_team.get(team, set()))
        for i in range(1, len(dates)):
            gap = (dates[i] - dates[i - 1]).days
            if gap > max_idle_days:
                violations.append((team, dates[i - 1].strftime('%Y-%m-%d'), dates[i].strftime('%Y-%m-%d'), gap))
    return violations


def season_week_index(d, season_start=None):
    """Return a stable season week index (0-based) using Monday-based weeks."""
    from datetime import timedelta
    dd = d.date() if hasattr(d, 'date') and not hasattr(d, 'weekday') else d
    start = season_start or SEASON_START_DATE or dd
    start_monday = start - timedelta(days=start.weekday())
    return (dd - start_monday).days // 7


def team_weeks_played(team, team_game_days, season_start=None):
    return sorted({season_week_index(dd, season_start) for dd in team_game_days[team].keys()})


def max_consecutive_byes(team, team_game_days, season_start=None):
    weeks = team_weeks_played(team, team_game_days, season_start)
    if len(weeks) < 2:
        return 0
    return max(max(0, weeks[i] - weeks[i - 1] - 1) for i in range(1, len(weeks)))


def max_consecutive_byes_after_adding(team, d, team_game_days, season_start=None):
    weeks = set(team_weeks_played(team, team_game_days, season_start))
    weeks.add(season_week_index(d, season_start))
    weeks = sorted(weeks)
    if len(weeks) < 2:
        return 0
    return max(max(0, weeks[i] - weeks[i - 1] - 1) for i in range(1, len(weeks)))


def no_two_consecutive_byes_after_adding(team, d, team_game_days, season_start=None, max_consecutive_byes=MAX_CONSECUTIVE_BYE_WEEKS):
    """True if adding date d does not create more than the allowed consecutive bye weeks between games."""
    return max_consecutive_byes_after_adding(team, d, team_game_days, season_start) <= max_consecutive_byes


def bye_week_urgency_bonus(team, d, team_game_days, season_start=None):
    """Score bonus for placements that reduce/avoid consecutive bye-week stretches."""
    before = max_consecutive_byes(team, team_game_days, season_start)
    after = max_consecutive_byes_after_adding(team, d, team_game_days, season_start)
    if after < before:
        return (before - after) * (BYE_URGENCY_WEIGHT * 2)
    weeks = team_weeks_played(team, team_game_days, season_start)
    if not weeks:
        return 0
    w = season_week_index(d, season_start)
    bonus = 0
    prev_weeks = [wk for wk in weeks if wk < w]
    next_weeks = [wk for wk in weeks if wk > w]
    if prev_weeks:
        gap_from_prev = w - prev_weeks[-1]
        if gap_from_prev == 2:
            bonus += BYE_URGENCY_WEIGHT
        elif gap_from_prev > 2:
            bonus += BYE_URGENCY_WEIGHT * 2
    if next_weeks:
        gap_to_next = next_weeks[0] - w
        if gap_to_next == 2:
            bonus += BYE_URGENCY_WEIGHT
        elif gap_to_next > 2:
            bonus += BYE_URGENCY_WEIGHT * 2
    return bonus


# -------------------------------
# Data loading functions
# -------------------------------
def load_team_availability(file_path):
    """Load per-team day-of-week availability.

    Accepts CSV where each team row contains day tokens in any of these forms:
      - Separate columns: Mon, Tue, Wed, ...
      - A single cell with delimiters: "Mon;Tue;Wed" or "Mon, Tue, Wed"
      - Whitespace-separated: "Mon Tue Wed"
      - Full day names ("Monday") are accepted and normalized to 3-letter form.

    Returns: dict[team] -> set({"Mon","Tue","Wed","Thu","Fri","Sat","Sun"})
    """
    VALID = {"Mon","Tue","Wed","Thu","Fri","Sat","Sun"}
    def norm(tok: str):
        tok = (tok or "").strip()
        if not tok:
            return None
        # accept full day names
        t3 = tok[:3].title()
        if t3 in VALID:
            return t3
        return None

    availability = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader, None)  # header
        for row in reader:
            if not row:
                continue
            team = (row[0] or "").strip()
            if not team:
                continue
            tokens = []
            for cell in row[1:]:
                cell = (cell or "").strip()
                if not cell:
                    continue
                # split on common delimiters
                for part in re.split(r"[;,\s]+", cell):
                    part = part.strip()
                    if part:
                        tokens.append(part)
            days = set()
            for t in tokens:
                d = norm(t)
                if d:
                    days.add(d)
            availability[team] = days
    return availability


def load_team_preferred_days(file_path):
    """Load optional per-team preferred day-of-week values.

    Format matches team_availability.csv semantics:
      Team,PreferredDays
      A1,Mon,Wed
      A2,Tue;Thu

    Missing file => empty dict.
    """
    if not file_path or not os.path.exists(file_path):
        return {}

    VALID = {"Mon","Tue","Wed","Thu","Fri","Sat","Sun"}

    def norm(tok: str):
        tok = (tok or "").strip()
        if not tok:
            return None
        t3 = tok[:3].title()
        if t3 in VALID:
            return t3
        return None

    preferred = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader, None)
        for row in reader:
            if not row:
                continue
            team = (row[0] or "").strip()
            if not team:
                continue
            tokens = []
            for cell in row[1:]:
                cell = (cell or "").strip()
                if not cell:
                    continue
                for part in re.split(r"[;,\s]+", cell):
                    part = part.strip()
                    if part:
                        tokens.append(part)
            days = set()
            for t in tokens:
                d = norm(t)
                if d:
                    days.add(d)
            preferred[team] = days
    return preferred


def preferred_day_bonus(team1, team2, d, team_preferred_days):
    if not team_preferred_days:
        return 0
    dow = dow_label(d)
    t1_pref = dow in team_preferred_days.get(team1, set())
    t2_pref = dow in team_preferred_days.get(team2, set())
    if t1_pref and t2_pref:
        return PREFERRED_DAY_BONUS_BOTH
    if t1_pref or t2_pref:
        return PREFERRED_DAY_BONUS_ONE
    return 0


def late_date_penalty(d, season_start, penalty_per_day=LATE_DATE_PENALTY_PER_DAY):
    if season_start is None:
        return 0
    return max(0, (d - season_start).days) * penalty_per_day


def preferred_day_count(teams, d, team_preferred_days):
    if not team_preferred_days:
        return 0
    dow = dow_label(d)
    return sum(1 for t in teams if dow in team_preferred_days.get(t, set()))


def load_field_availability(file_path):
    """Load (date, slot, field) rows and return a *deduplicated* list sorted in a stable order.

    Notes:
      - The scheduler iterates `field_availability` greedily. If this list is in an odd order
        (or contains duplicate rows), you can get surprising behavior (e.g., late slots filled first,
        repeated rows in output exports, etc.).
      - We deduplicate on (date, slot, field) to protect against accidental duplicate rows in the CSV.
      - We sort in a clear order: (is_sunday, date, time, field). If you don't want Sundays first,
        set SUNDAYS_FIRST = False near the top of the file.
    """
    field_availability = []
    seen = set()
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # header
        for row in reader:
            date_dt = datetime.strptime(row[0].strip(), '%Y-%m-%d')  # midnight datetime
            slot = row[1].strip()
            field = row[2].strip()
            key = (date_dt.date(), slot, field)
            if key in seen:
                continue
            seen.add(key)
            field_availability.append((date_dt, slot, field))

    def _slot_time(slot_str: str):
        try:
            return datetime.strptime(slot_str.strip(), "%I:%M %p")
        except Exception:
            # If a slot string is malformed, push it to the end but keep deterministic ordering.
            return datetime.strptime("11:59 PM", "%I:%M %p")

    # Stable ordering. Many leagues prefer Sundays filled first; make it configurable.
    sundays_first = globals().get("SUNDAYS_FIRST", True)
    if sundays_first:
        field_availability.sort(key=lambda x: (
            (0 if x[0].weekday() == 6 else 1),
            x[0].date(),
            _slot_time(x[1]),
            x[2],
        ))
    else:
        field_availability.sort(key=lambda x: (
            x[0].date(),
            _slot_time(x[1]),
            x[2],
        ))
    return field_availability


def load_team_blackouts(file_path):
    """
    CSV format: Team, Date1, Date2, ...
    Dates: YYYY-MM-DD
    Returns: dict[team] -> set(date)
    """
    blackouts = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # header
        for row in reader:
            team = row[0].strip()
            dates = set()
            for d in row[1:]:
                d = (d or '').strip()
                if not d:
                    continue
                try:
                    dt = datetime.strptime(d, '%Y-%m-%d').date()
                    dates.add(dt)
                except Exception as e:
                    print("Error parsing blackout date '{}' for team {}: {}".format(d, team, e))
            blackouts[team] = dates
    return blackouts

# -------------------------------
# Intra-division matchup generation
# -------------------------------
def _round_robin_pairs(teams):
    teams = list(teams)
    n = len(teams)
    assert n % 2 == 0, "round robin requires even team count"
    left = teams[:n//2]
    right = teams[n//2:]
    rounds = []
    for _ in range(n-1):
        pairs = list(zip(left, reversed(right)))
        rounds.append(pairs)
        right = [left.pop(1)] + right
        left.insert(1, right.pop())
    return rounds

def generate_intra_matchups_for_target(division, teams, intra_target_per_team):
    teams = sorted(teams)
    n = len(teams)
    if n < 2:
        return []

    if intra_target_per_team < 0:
        raise Exception("intra_target_per_team must be >= 0 (got {}) for division {}.".format(intra_target_per_team, division))

    if intra_target_per_team == 0:
        return []

    if intra_target_per_team == 2 * (n - 1):
        matchups = []
        for t1, t2 in itertools.combinations(teams, 2):
            matchups.append((t1, t2))
            matchups.append((t2, t1))
        return matchups

    if n == 8 and intra_target_per_team == 18:
        two_game_count = 3
        pairs = list(itertools.combinations(teams, 2))
        count2 = {t: 0 for t in teams}
        assignment = {}

        def backtrack(i):
            if i == len(pairs):
                return all(count2[t] == two_game_count for t in teams)
            a, b = pairs[i]
            if count2[a] < two_game_count and count2[b] < two_game_count:
                assignment[(a, b)] = 2
                count2[a] += 1
                count2[b] += 1
                if backtrack(i + 1):
                    return True
                count2[a] -= 1
                count2[b] -= 1
                del assignment[(a, b)]
            assignment[(a, b)] = 3
            if backtrack(i + 1):
                return True
            del assignment[(a, b)]
            return False

        if not backtrack(0):
            raise Exception("No valid intra-division assignment found for {} (18 target).".format(division))

        matchups = []
        for (a, b), w in assignment.items():
            if w == 2:
                matchups.extend([(a, b), (b, a)])
            else:
                matchups.extend([(a, b), (b, a)])
                matchups.append((a, b) if random.random() < 0.5 else (b, a))
        return matchups

    if n == 8 and intra_target_per_team == 22:
        matchups = []
        for a, b in itertools.combinations(teams, 2):
            matchups.extend([(a, b), (b, a)])
            matchups.append((a, b) if random.random() < 0.5 else (b, a))

        rounds = _round_robin_pairs(teams)
        rival_pairs = random.choice(rounds)
        for a, b in rival_pairs:
            matchups.append((a, b) if random.random() < 0.5 else (b, a))
        return matchups

    total_slots = n * intra_target_per_team
    if total_slots % 2 != 0:
        raise Exception(
            "Intra target {} with n={} yields odd total participation ({}); cannot form whole games for division {}."
            .format(intra_target_per_team, n, total_slots, division)
        )

    games_left = {t: intra_target_per_team for t in teams}
    home = {t: 0 for t in teams}
    away = {t: 0 for t in teams}
    matchups = []

    if intra_target_per_team >= 2:
        for i in range(n):
            h = teams[i]
            a = teams[(i + 1) % n]
            matchups.append((h, a))
            home[h] += 1
            away[a] += 1
            games_left[h] -= 1
            games_left[a] -= 1

        for i in range(n):
            h = teams[(i + 1) % n]
            a = teams[i]
            matchups.append((h, a))
            home[h] += 1
            away[a] += 1
            games_left[h] -= 1
            games_left[a] -= 1

    elif intra_target_per_team == 1:
        for i in range(n):
            h = teams[i]
            a = teams[(i + 1) % n]
            matchups.append((h, a))
            home[h] += 1
            away[a] += 1
            games_left[h] -= 1
            games_left[a] -= 1

    meet = defaultdict(int)
    for (h, a) in matchups:
        meet[frozenset((h, a))] += 1
    min_pair, soft_cap = effective_pair_rules(division, intra_target_per_team, n)

    guard = 0
    guard_max = 200000

    def teams_by_need():
        return sorted(teams, key=lambda t: games_left[t], reverse=True)

    while any(v > 0 for v in games_left.values()):
        guard += 1
        if guard > guard_max:
            raise Exception("Failed building intra matchups for {}; stuck with remaining={}".format(division, games_left))

        t1 = teams_by_need()[0]
        if games_left[t1] <= 0:
            break

        candidates = [t for t in teams if t != t1 and games_left[t] > 0]
        if not candidates:
            raise Exception("Cannot find opponent to satisfy intra target for {}. Remaining={}".format(division, games_left))

        def meet_key(t2):
            m = meet[frozenset((t1, t2))]
            # Prefer opponents we haven't met enough yet (under min_pair), then fewer repeats.
            under = 1 if m < min_pair else 0
            return (-under, m, -games_left[t2], t2)

        under = [t2 for t2 in candidates if meet[frozenset((t1, t2))] < soft_cap]
        pick_pool = under if under else candidates
        t2 = min(pick_pool, key=meet_key)

        if home[t1] - away[t1] <= home[t2] - away[t2]:
            h, a = t1, t2
        else:
            h, a = t2, t1

        matchups.append((h, a))
        home[h] += 1
        away[a] += 1
        games_left[h] -= 1
        games_left[a] -= 1
        meet[frozenset((t1, t2))] += 1

    return matchups

# -------------------------------
# Inter-division matchup generation
# -------------------------------
def generate_bipartite_regular_matchups(teams1, teams2, degree):
    teams1 = list(teams1)
    teams2 = list(teams2)

    if degree < 0:
        raise Exception("degree must be >= 0")
    if degree == 0:
        return []
    if degree > len(teams2):
        raise Exception(
            "degree={} exceeds opponent count={}; reduce degree or implement repeat-opponent inter matchups."
            .format(degree, len(teams2))
        )

    random.shuffle(teams1)

    total_edges = len(teams1) * degree
    base = total_edges // len(teams2)
    extra = total_edges % len(teams2)

    teams2_shuffled = teams2[:]
    random.shuffle(teams2_shuffled)
    cap = {t: base for t in teams2_shuffled}
    for t in teams2_shuffled[:extra]:
        cap[t] += 1

    edges = []
    for t1 in teams1:
        avail = [t for t in teams2_shuffled if cap[t] > 0]
        if len(avail) < degree:
            raise Exception("No valid bipartite matching found (insufficient capacity).")

        random.shuffle(avail)
        avail.sort(key=lambda t: cap[t], reverse=True)
        chosen = avail[:degree]

        for t2 in chosen:
            edges.append((t1, t2))
            cap[t2] -= 1

    return edges

def generate_inter_division_matchups(division_from, division_to, teams_from, teams_to, degree):
    edges = generate_bipartite_regular_matchups(teams_from, teams_to, degree)
    matchups = []
    for (t1, t2) in edges:
        matchups.append((t1, t2) if random.random() < 0.5 else (t2, t1))
    return matchups

# -------------------------------
# Combine full matchup list
# -------------------------------
def generate_full_matchups(division_teams):
    enabled_pairs = []
    for (d1, d2), enabled in INTER_PAIR_SETTINGS.items():
        if not enabled:
            continue
        if d1 not in division_teams or d2 not in division_teams:
            continue
        if inter_enabled_for_pair(d1, d2):
            enabled_pairs.append((d1, d2))

    inter_per_team = {d: 0 for d in division_teams.keys()}
    for d1, d2 in enabled_pairs:
        deg = pair_degree(d1, d2)
        inter_per_team[d1] += deg
        inter_per_team[d2] += deg

    full_matchups = []
    for div, teams in division_teams.items():
        if div == 'A':
            continue
        intra_target = DIVISION_SETTINGS[div]['target_games'] - inter_per_team.get(div, 0)
        full_matchups.extend(generate_intra_matchups_for_target(div, teams, intra_target))

    for d1, d2 in enabled_pairs:
        deg = pair_degree(d1, d2)
        full_matchups.extend(generate_inter_division_matchups(d1, d2, division_teams[d1], division_teams[d2], deg))

    random.shuffle(full_matchups)

    return full_matchups


def generate_filler_matchups(division_teams, team_stats, schedule, max_new_games=5000):
    """Generate additional flexible matchups to help finish schedules when the fixed matchup list dead-ends.

    Why this exists:
      - The heuristic scheduler starts with a *fixed* list of matchups (opponent graph).
      - With tight calendar constraints (HARD_MIN_GAP, WEEKLY_GAME_LIMIT, availability/blackouts),
        that fixed list can become impossible to place even though there are still plenty of open slots.
      - This function creates *extra* candidate matchups among teams that are still below target,
        preferring intra-division and respecting inter-division enablement rules.

    We keep it conservative:
      - Never creates A games (A is pod-scheduled only).
      - Prefers opponents that haven't been over-used yet (soft caps).
    """
    # Count current meetings (undirected) from the existing schedule
    meet = defaultdict(int)
    for _dt, _slot, _field, home, _hd, away, _ad in schedule:
        if home and away:
            meet[frozenset((home, away))] += 1

    # Teams still needing games (exclude A)
    need = [t for div, teams in division_teams.items() for t in teams
            if div != 'A' and team_stats[t]['total_games'] < target_games(t)]

    if not need:
        return []

    # Precompute per-division effective caps (rough)
    caps = {}
    for div, teams in division_teams.items():
        if div == 'A':
            continue
        n = len(teams)
        intra_target = max(0, DIVISION_SETTINGS[div]['target_games'])  # conservative
        _min_eff, cap_eff = effective_pair_rules(div, intra_target, n)
        caps[div] = cap_eff

    new_matchups = []
    guard = 0
    while guard < max_new_games:
        guard += 1

        # Refresh need list
        need = [t for div, teams in division_teams.items() for t in teams
                if div != 'A' and team_stats[t]['total_games'] < target_games(t)]
        if not need:
            break

        # Pick most-behind team
        need.sort(key=lambda t: (target_games(t) - team_stats[t]['total_games'], t), reverse=True)
        t1 = need[0]
        d1 = div_of(t1)

        # Candidate opponents: also behind, and allowed to play (same div or enabled inter pair)
        opps = []
        for t2 in need[1:]:
            if t2 == t1:
                continue
            d2 = div_of(t2)
            if d1 == d2:
                opps.append(t2)
            else:
                if inter_enabled_for_pair(d1, d2):
                    opps.append(t2)

        if not opps:
            # If no behind opponents are available, fall back to any team in same division
            opps = [t2 for t2 in division_teams.get(d1, []) if t2 != t1 and d1 != 'A']

        if not opps:
            break

        # Prefer opponents with:
        #   - biggest deficit
        #   - lowest current meet count
        #   - below soft cap
        def opp_key(t2):
            d2 = div_of(t2)
            pair = frozenset((t1, t2))
            m = meet[pair]
            cap = max(caps.get(d1, 999), caps.get(d2, 999))
            over = 1 if m >= cap else 0
            return (over, m, -(target_games(t2) - team_stats[t2]['total_games']), t2)

        t2 = min(opps, key=opp_key)

        # Add matchup with randomized home/away orientation
        if random.random() < 0.5:
            new_matchups.append((t1, t2))
        else:
            new_matchups.append((t2, t1))
        meet[frozenset((t1, t2))] += 1

        # Optimistically increment totals so we don't over-generate for a single team
        team_stats[t1]['total_games'] += 1
        team_stats[t2]['total_games'] += 1

    # Roll back the optimistic increments (we only wanted them for generation weighting)
    for (h, a) in new_matchups:
        team_stats[h]['total_games'] -= 1
        team_stats[a]['total_games'] -= 1

    return new_matchups

    return full_matchups

# -------------------------------
# Home/Away Helper
# -------------------------------

def build_sunday_pod_assignment(timeslots_by_date, rotation, seed=42):
    """Return {date: division} assignment for which division gets pod-style DH priority on Sundays.

    We shuffle Sundays (seeded) then round-robin assign divisions. This helps spread Sunday pods.
    """
    sundays = [d for d in timeslots_by_date.keys() if getattr(d, 'weekday', lambda: 0)() == 6]
    sundays = sorted(sundays)
    rnd = random.Random(seed)
    rnd.shuffle(sundays)
    if not rotation:
        rotation = ['A', 'B', 'C', 'D']
    mapping = {}
    for i, d in enumerate(sundays):
        mapping[d] = rotation[i % len(rotation)]
    return mapping


def decide_home_away(t1, t2, team_stats):
    if team_stats[t1]['home_games'] >= HOME_AWAY_BALANCE and team_stats[t2]['home_games'] < HOME_AWAY_BALANCE:
        return t2, t1
    if team_stats[t2]['home_games'] >= HOME_AWAY_BALANCE and team_stats[t1]['home_games'] < HOME_AWAY_BALANCE:
        return t1, t2
    if team_stats[t1]['home_games'] < team_stats[t2]['home_games']:
        return t1, t2
    if team_stats[t2]['home_games'] < team_stats[t1]['home_games']:
        return t2, t1
    return (t1, t2) if random.random() < 0.5 else (t2, t1)


def schedule_doubleheaders_preemptively(all_teams, unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
                                        team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents,
                                        used_slots, schedule=None):
    if schedule is None:
        schedule = []

    # Prefer filling Sundays first (league preference: easiest full-day inventory)
    date_order = sorted(timeslots_by_date.keys(), key=lambda dd: (0 if dd.weekday() == 6 else 1, dd))
    for d in date_order:
        day_of_week = dow_label(d)
        week_num = d.isocalendar()[1]
        slots = timeslots_by_date[d]
        if not slots:
            continue

        teams_by_need = sorted(all_teams, key=lambda t: team_need_key(t, team_stats, doubleheader_count), reverse=True)
        for team in teams_by_need:
            if team and team[0] == 'A':
                continue
            if doubleheader_count[team] >= min_dh(team):
                continue
            if not is_team_available(team, d, team_availability, team_blackouts):
                continue

            games_today = team_game_days[team].get(d, 0)

            if games_today == 0:
                if len(slots) < 2:
                    continue

                for i in range(len(slots) - 1):
                    slot1 = slots[i]
                    slot2 = slots[i + 1]

                    free1 = [entry for entry in field_availability
                             if entry[0].date() == d and entry[1] == slot1 and ((entry[0], slot1, entry[2]) not in used_slots)]
                    free2 = [entry for entry in field_availability
                             if entry[0].date() == d and entry[1] == slot2 and ((entry[0], slot2, entry[2]) not in used_slots)]
                    if not free1 or not free2:
                        continue

                    candidate_matchups = [m for m in unscheduled if team in m]
                    if len(candidate_matchups) < 2:
                        continue

                    for m1, m2 in itertools.combinations(candidate_matchups, 2):
                        opp1 = m1[0] if m1[1] == team else m1[1]
                        opp2 = m2[0] if m2[1] == team else m2[1]
                        if opp1 == opp2:
                            continue

                        if not is_team_available(opp1, d, team_availability, team_blackouts):
                            continue
                        if not is_team_available(opp2, d, team_availability, team_blackouts):
                            continue
                        if team_game_days[opp1].get(d, 0) != 0 or team_game_days[opp2].get(d, 0) != 0:
                            continue

                        if team_stats[team]['weekly_games'][week_num] + 2 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp1]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp2]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue

                        if team_stats[team]['total_games'] + 2 > target_games(team):
                            continue
                        if team_stats[opp1]['total_games'] + 1 > target_games(opp1):
                            continue
                        if team_stats[opp2]['total_games'] + 1 > target_games(opp2):
                            continue

                        home1, away1 = decide_home_away(team, opp1, team_stats)
                        home2, away2 = decide_home_away(team, opp2, team_stats)

                        date1, slot1_str, field1 = free1[0]
                        date2, slot2_str, field2 = free2[0]

                        unscheduled.remove(m1)
                        unscheduled.remove(m2)

                        team_stats[home1]['home_games'] += 1
                        team_stats[away1]['away_games'] += 1
                        team_stats[home2]['home_games'] += 1
                        team_stats[away2]['away_games'] += 1

                        schedule.append((date1, slot1_str, field1, home1, home1[0], away1, away1[0]))
                        schedule.append((date2, slot2_str, field2, home2, home2[0], away2, away2[0]))

                        for t in (team, opp1):
                            team_stats[t]['total_games'] += 1
                            team_stats[t]['weekly_games'][week_num] += 1
                            team_game_days[t][d] += 1
                            team_game_slots[t][d].append(slot1_str)

                        for t in (team, opp2):
                            team_stats[t]['total_games'] += 1
                            team_stats[t]['weekly_games'][week_num] += 1
                            team_game_days[t][d] += 1
                            team_game_slots[t][d].append(slot2_str)

                        doubleheader_count[team] += 1
                        team_doubleheader_opponents[team][d].update([opp1, opp2])

                        used_slots[(date1, slot1_str, field1)] = True
                        used_slots[(date2, slot2_str, field2)] = True
                        break

            elif games_today == 1:
                current_slot = team_game_slots[team][d][0]
                try:
                    idx = slots.index(current_slot)
                except ValueError:
                    continue
                if idx + 1 >= len(slots):
                    continue
                next_slot = slots[idx + 1]

                free_next = [entry for entry in field_availability
                             if entry[0].date() == d and entry[1] == next_slot and ((entry[0], next_slot, entry[2]) not in used_slots)]
                if not free_next:
                    continue

                already_opp = None
                for g in schedule:
                    if g[0].date() == d and (g[3] == team or g[5] == team):
                        already_opp = g[5] if g[3] == team else g[3]
                        break
                if already_opp is None:
                    continue

                if doubleheader_count[team] >= max_dh(team):
                    continue

                candidate_matchups = [m for m in unscheduled if team in m]
                for m in candidate_matchups:
                    opp = m[0] if m[1] == team else m[1]
                    if opp == already_opp:
                        continue
                    if not is_team_available(opp, d, team_availability, team_blackouts):
                        continue
                    if team_game_days[opp].get(d, 0) != 0:
                        continue
                    if team_stats[team]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                        continue
                    if team_stats[opp]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                        continue
                    if team_stats[team]['total_games'] + 1 > target_games(team):
                        continue
                    if team_stats[opp]['total_games'] + 1 > target_games(opp):
                        continue
                    if opp in team_doubleheader_opponents[team][d]:
                        continue
                    home, away = decide_home_away(team, opp, team_stats)
                    date_entry, slot_str, field = free_next[0]

                    unscheduled.remove(m)
                    team_stats[home]['home_games'] += 1
                    team_stats[away]['away_games'] += 1
                    schedule.append((date_entry, slot_str, field, home, home[0], away, away[0]))
                    for t in (team, opp):
                        team_stats[t]['total_games'] += 1
                        team_stats[t]['weekly_games'][week_num] += 1
                        team_game_days[t][d] += 1
                        team_game_slots[t][d].append(slot_str)

                    doubleheader_count[team] += 1
                    team_doubleheader_opponents[team][d].add(opp)
                    used_slots[(date_entry, slot_str, field)] = True
                    break

    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, unscheduled

# -------------------------------
# Dedicated Doubleheader pass (Two-phase), per-division min/max
# -------------------------------
def force_minimum_doubleheaders(all_teams, unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
                                team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents,
                                used_slots, schedule=None):
    if schedule is None:
        schedule = []

    teams = sorted(all_teams, key=lambda t: team_need_key(t, team_stats, doubleheader_count), reverse=True)

    # Phase 1: ensure each team gets at least 1 DH day (if min_dh > 0)
    for team in teams:
        if team and team[0] == 'A':
            continue
        if min_dh(team) <= 0 or doubleheader_count[team] >= 1:
            continue

        date_order = sorted(timeslots_by_date.keys(), key=lambda dd: (0 if dd.weekday() == 6 else 1, dd))
        for d in date_order:
            day_of_week = dow_label(d)
            if d in team_blackouts.get(team, set()) or day_of_week not in team_availability.get(team, set()):
                continue
            week_num = d.isocalendar()[1]
            sorted_slots = timeslots_by_date[d]
            games_today = team_game_days[team].get(d, 0)

            if games_today != 1:
                continue

            try:
                idx = sorted_slots.index(team_game_slots[team][d][0])
            except ValueError:
                continue
            if idx + 1 >= len(sorted_slots):
                continue
            next_slot = sorted_slots[idx + 1]

            free_fields = [entry for entry in field_availability
                           if entry[0].date() == d and entry[1] == next_slot and ((entry[0], next_slot, entry[2]) not in used_slots)]
            if not free_fields:
                continue

            already_opp = None
            for g in schedule:
                if g[0].date() == d and (g[3] == team or g[5] == team):
                    already_opp = g[5] if g[3] == team else g[3]
                    break
            if already_opp is None:
                continue

            if doubleheader_count[team] >= max_dh(team):
                break

            candidate = [m for m in unscheduled if team in m]
            for m in candidate:
                opp = m[0] if m[1] == team else m[1]
                if opp == already_opp:
                    continue
                if not is_team_available(opp, d, team_availability, team_blackouts):
                    continue
                if team_game_days[opp].get(d, 0) != 0:
                    continue
                if team_stats[team]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                    continue
                if team_stats[opp]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                    continue
                if team_stats[team]['total_games'] + 1 > target_games(team):
                    continue
                if team_stats[opp]['total_games'] + 1 > target_games(opp):
                    continue
                if opp in team_doubleheader_opponents[team][d]:
                    continue
                home, away = decide_home_away(team, opp, team_stats)
                date_entry, slot_str, field = free_fields[0]

                unscheduled.remove(m)
                team_stats[home]['home_games'] += 1
                team_stats[away]['away_games'] += 1
                schedule.append((date_entry, slot_str, field, home, home[0], away, away[0]))
                for t in (team, opp):
                    team_stats[t]['total_games'] += 1
                    team_stats[t]['weekly_games'][week_num] += 1
                    team_game_days[t][d] += 1
                    team_game_slots[t][d].append(slot_str)

                doubleheader_count[team] += 1
                team_doubleheader_opponents[team][d].add(opp)
                used_slots[(date_entry, slot_str, field)] = True
                break

            if doubleheader_count[team] >= 1:
                break

    # Phase 2: push teams toward their per-division minimum DH days.
    teams = sorted(all_teams, key=lambda t: team_need_key(t, team_stats, doubleheader_count), reverse=True)
    for team in teams:
        if team and team[0] == 'A':
            continue
        while doubleheader_count[team] < min_dh(team):
            if doubleheader_count[team] >= max_dh(team):
                break

            scheduled = False
            date_order = sorted(timeslots_by_date.keys(), key=lambda dd: (0 if dd.weekday() == 6 else 1, dd))
            for d in date_order:
                day_of_week = dow_label(d)
                if d in team_blackouts.get(team, set()) or day_of_week not in team_availability.get(team, set()):
                    continue
                week_num = d.isocalendar()[1]
                sorted_slots = timeslots_by_date[d]
                games_today = team_game_days[team].get(d, 0)

                if games_today == 1:
                    try:
                        idx = sorted_slots.index(team_game_slots[team][d][0])
                    except ValueError:
                        continue
                    if idx + 1 >= len(sorted_slots):
                        continue
                    next_slot = sorted_slots[idx + 1]

                    free_fields = [entry for entry in field_availability
                                   if entry[0].date() == d and entry[1] == next_slot and ((entry[0], next_slot, entry[2]) not in used_slots)]
                    if not free_fields:
                        continue

                    already_opp = None
                    for g in schedule:
                        if g[0].date() == d and (g[3] == team or g[5] == team):
                            already_opp = g[5] if g[3] == team else g[3]
                            break
                    if already_opp is None:
                        continue

                    candidate = [m for m in unscheduled if team in m]
                    for m in candidate:
                        opp = m[0] if m[1] == team else m[1]
                        if opp == already_opp:
                            continue
                        if not is_team_available(opp, d, team_availability, team_blackouts):
                            continue
                        if team_game_days[opp].get(d, 0) != 0:
                            continue
                        if team_stats[team]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[team]['total_games'] + 1 > target_games(team):
                            continue
                        if team_stats[opp]['total_games'] + 1 > target_games(opp):
                            continue
                        if opp in team_doubleheader_opponents[team][d]:
                            continue

                        home, away = decide_home_away(team, opp, team_stats)
                        date_entry, slot_str, field = free_fields[0]

                        unscheduled.remove(m)
                        team_stats[home]['home_games'] += 1
                        team_stats[away]['away_games'] += 1
                        schedule.append((date_entry, slot_str, field, home, home[0], away, away[0]))

                        for t in (team, opp):
                            team_stats[t]['total_games'] += 1
                            team_stats[t]['weekly_games'][week_num] += 1
                            team_game_days[t][d] += 1
                            team_game_slots[t][d].append(slot_str)

                        doubleheader_count[team] += 1
                        team_doubleheader_opponents[team][d].add(opp)
                        used_slots[(date_entry, slot_str, field)] = True
                        scheduled = True
                        break

                if scheduled:
                    break

            if not scheduled:
                break

    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, unscheduled

# -------------------------------
# A Division DH-only scheduling (pair doubleheaders)
# -------------------------------

# -------------------------------
# A Division DH-only scheduling (4-team pods across two fields)
# -------------------------------
def schedule_A_pod_doubleheaders(division_teams, team_availability, field_availability, team_blackouts,
                                 timeslots_by_date, team_stats, doubleheader_count,
                                 team_game_days, team_game_slots, used_slots, schedule=None, sunday_assignment=None, sunday_pods_used=None, team_preferred_days=None):
    """
    Schedule Division A as *doubleheaders only* using 4-team "pod" sessions across BOTH fields.

    A pod session on date d uses two adjacent slots (s1,s2) and two different fields (f1,f2):
      Slot s1:
        Game1: t1 vs t2  (on f1)
        Game2: t3 vs t4  (on f2)
      Slot s2:
        Game3: t1 vs t3  (on f1)
        Game4: t2 vs t4  (on f2)

    This guarantees each team plays exactly 2 games that day with DIFFERENT opponents.
    By construction, each team gets 1 home + 1 away within the pod:
      Slot s1 home teams: t1, t3
      Slot s2 home teams: t2, t4
    """
    if schedule is None:
        schedule = []
    if not isinstance(schedule, list):
        raise TypeError("schedule must be list[game_tuple], got {}".format(type(schedule)))

    A_teams = list(division_teams.get('A', []))
    if not A_teams:
        return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, used_slots

    # 22 games => 11 DH days (sessions) per team
    target_sessions = DIVISION_SETTINGS['A']['target_games'] // 2

    sessions_done = defaultdict(int)      # team -> sessions completed
    pair_meets = defaultdict(int)         # frozenset({a,b}) -> number of games already between them (within A pods)

    # Build fast lookup for canonical datetime from field_availability (midnight dt)
    dt_by_key = {}
    for date_dt, slot, field in field_availability:
        dt_by_key[(date_dt.date(), slot, field)] = date_dt

    # Build per-date slot ordering + list of adjacent slot pairs.
    #
    # We *do* want some A pods on Sundays (easier attendance), but we don't want A to dominate
    # every Sunday all season.
    #
    # Strategy:
    #   1) Ensure each A team gets at least MIN_SUNDAY_SESSIONS_PER_TEAM DH sessions on Sundays
    #   2) After that, prefer weekday pods.
    all_dates = sorted({dt.date() for (dt, _slot, _field) in field_availability})
    sunday_dates = [d for d in all_dates if d.weekday() == 6]
    weekday_dates = [d for d in all_dates if d.weekday() != 6]

    # Policy: limit how many A "pods" can run on any given Sunday.
    # A "pod" = 4 A-teams playing 2 games each across two fields and two adjacent slots.
    MAX_A_PODS_PER_SUNDAY = SUNDAY_PODS_PER_SUNDAY
    MIN_SUNDAY_SESSIONS_PER_TEAM = 1
    adjacent_slot_pairs_by_date = {}
    for d in all_dates:
        slots = sorted(set(timeslots_by_date.get(d, [])), key=lambda s: datetime.strptime(s.strip(), "%I:%M %p"))
        pairs = []
        for i in range(len(slots) - 1):
            pairs.append((slots[i], slots[i + 1]))
        adjacent_slot_pairs_by_date[d] = pairs

    # Build per-date set of fields available
    fields_by_date = defaultdict(set)
    for date_dt, slot, field in field_availability:
        fields_by_date[date_dt.date()].add(field)

    def can_play_pod(team, d):
        dow = dow_abbrev(d)
        if not is_team_available(team, d, team_availability, team_blackouts):
            return False
        # must have no other games that date
        if team_game_days[team].get(d, 0) != 0:
            return False
        # gap constraint vs other days
        if not min_gap_ok(team, d, team_game_days):
            return False
        if not no_two_consecutive_byes_after_adding(team, d, team_game_days):
            return False
        wk = d.isocalendar()[1]
        if team_stats[team]['weekly_games'].get(wk, 0) + 2 > WEEKLY_GAME_LIMIT:
            return False
        if team_stats[team]['total_games'] + 2 > DIVISION_SETTINGS['A']['target_games']:
            return False
        return True

    def available_fields_for_pair(d, s1, s2):
        """Return fields that are free (unused) for BOTH slots s1 and s2 on date d."""
        out = []
        for f in sorted(fields_by_date.get(d, [])):
            dt1 = dt_by_key.get((d, s1, f))
            dt2 = dt_by_key.get((d, s2, f))
            if dt1 is None or dt2 is None:
                continue
            if used_slots.get((dt1, s1, f), False) or used_slots.get((dt2, s2, f), False):
                continue
            out.append(f)
        return out
    def choose_four(eligible):
        """Pick 4 eligible teams for a pod session, *actively* balancing opponents.

        Goals:
          1) Ensure every A-vs-A pairing happens at least A_PAIR_MIN_GAMES times (as feasible)
          2) Avoid extreme repeats early (e.g., A1 vs A2 six times while A1 vs A8 once)
          3) Still finish all required sessions

        We evaluate both which 4 teams to use AND the internal pod layout (who plays whom),
        because the layout determines the 4 games created:
            slot1: t1-vs-t2, t3-vs-t4
            slot2: t1-vs-t4, t2-vs-t3
        """
        need = [t for t in eligible if sessions_done[t] < target_sessions]
        if len(need) < 4:
            return None

        # Prefer teams with biggest remaining sessions; keep pool small for speed
        need.sort(
            key=lambda t: (
                target_sessions - sessions_done[t],
                DIVISION_SETTINGS['A']['target_games'] - team_stats[t]['total_games'],
                t,
            ),
            reverse=True,
        )
        pool = need[:10] if len(need) > 10 else need

        # Helper: does team still have any unmet "must play" pairs?
        def has_unmet_pairs(team: str) -> bool:
            for other in A_teams:
                if other == team:
                    continue
                if pair_meets[frozenset((team, other))] < A_PAIR_MIN_GAMES:
                    return True
            return False

        best = None
        best_score = None  # smaller is better (lexicographic)

        # Evaluate combinations of 4 from pool, and also try all internal layouts
        for combo in itertools.combinations(pool, 4):
            # Try all unique permutations (layout matters). 24 is small.
            for perm in itertools.permutations(combo, 4):
                t1, t2, t3, t4 = perm

                games = [
                    frozenset((t1, t2)),
                    frozenset((t3, t4)),
                    frozenset((t1, t4)),
                    frozenset((t2, t3)),
                ]

                # Hard-ish guard:
                # If a pair is already at/over soft cap, don't schedule it IF either team still has
                # any unmet required pair elsewhere.
                blocked = False
                for g in games:
                    a, b = tuple(g)
                    if pair_meets[g] >= A_PAIR_SOFT_CAP and (has_unmet_pairs(a) or has_unmet_pairs(b)):
                        blocked = True
                        break
                if blocked:
                    continue

                # Count how many of the games help satisfy the minimum pair requirement
                unmet_hits = sum(1 for g in games if pair_meets[g] < A_PAIR_MIN_GAMES)

                # Prefer layouts that:
                #  - maximize unmet_hits
                #  - minimize total existing meetings for these pairs
                #  - then prefer using teams with larger remaining session deficits
                total_meets = sum(pair_meets[g] for g in games)
                rem = sum((target_sessions - sessions_done[t]) for t in (t1, t2, t3, t4))

                # Also reduce spread (avoid spiking any single pair too quickly)
                after_counts = [pair_meets[g] + 1 for g in games]
                spread = max(after_counts) - min(after_counts)

                score = (-unmet_hits, total_meets, spread, -rem, tuple(sorted(combo)))
                if best_score is None or score < best_score:
                    best_score = score
                    best = (t1, t2, t3, t4)

        return best

    def place_game(d, slot, field, home, away):
        dt = dt_by_key.get((d, slot, field))
        if dt is None:
            return False
        # Hard cap
        if team_stats[home]['total_games'] >= target_games(home) or team_stats[away]['total_games'] >= target_games(away):
            return False
        schedule.append((dt, slot, field, home, home[0], away, away[0]))
        used_slots[(dt, slot, field)] = True

        wk = d.isocalendar()[1]
        team_stats[home]['total_games'] += 1
        team_stats[away]['total_games'] += 1
        team_stats[home]['home_games'] += 1
        team_stats[away]['away_games'] += 1
        team_stats[home]['weekly_games'][wk] = team_stats[home]['weekly_games'].get(wk, 0) + 1
        team_stats[away]['weekly_games'][wk] = team_stats[away]['weekly_games'].get(wk, 0) + 1
        team_game_days[home][d] += 1
        team_game_days[away][d] += 1
        team_game_slots[home][d].append(slot)
        team_game_slots[away][d].append(slot)
        return True

    # Iterate dates/slots in chronological order. We do multiple passes to work around blocked days/slots.
    #
    # We allow multiple pods per date EXCEPT Sundays, which are capped by MAX_A_PODS_PER_SUNDAY.
    sunday_sessions_done = {t: 0 for t in A_teams}
    season_start = min((dt.date() for dt, _slot, _field in field_availability), default=None)

    for _pass in range(12):
        progress = False

        need_more_sunday = any(sunday_sessions_done[t] < MIN_SUNDAY_SESSIONS_PER_TEAM for t in A_teams)

        # Balance A-division day-of-week distribution:
        # Prefer dates whose weekday is currently under-used by A, with a small seeded shuffle
        # to avoid repeatedly picking the same day pattern.
        a_dow_load = defaultdict(int)
        for _dt, _slot, _field, _home, _hdiv, _away, _adiv in schedule:
            if (_home and _home[0] == 'A') or (_away and _away[0] == 'A'):
                a_dow_load[dow_label(_dt)] += 1

        rnd = random.Random((RANDOM_SEED or 0) + (_pass * 97) + 13)

        def _date_key(dd):
            # Front-load the season while still balancing A weekday usage and honoring preferred days.
            active_needers = [t for t in A_teams if sessions_done[t] < target_sessions]
            return (
                late_date_penalty(dd, season_start),
                a_dow_load.get(dow_label(dd), 0),
                -preferred_day_count(active_needers, dd, team_preferred_days),
                dd,
                rnd.random(),
            )

        # If we have a Sunday rotation, push Sundays assigned to A to the front of the Sunday list.
        if sunday_assignment:
            sunday_dates_ordered = [sd for sd in sunday_dates if sunday_assignment.get(sd) == 'A'] +                                    [sd for sd in sunday_dates if sunday_assignment.get(sd) != 'A']
        else:
            sunday_dates_ordered = list(sunday_dates)

        # Sort within weekday/Sunday groups by current A day-load to smooth out heavy Mondays/Tuesdays.
        weekday_dates_ordered = sorted(list(weekday_dates), key=_date_key)
        sunday_dates_ordered = sorted(list(sunday_dates_ordered), key=_date_key)

        date_order = (sunday_dates_ordered + weekday_dates_ordered) if need_more_sunday else (weekday_dates_ordered + sunday_dates_ordered)

        for d in date_order:
            # Sunday pod rotation + global cap:
            # - If this Sunday is assigned to a different division, only let A use it while we still
            #   need to satisfy the minimum Sunday sessions per A team.
            if sunday_assignment and d.weekday() == 6 and sunday_assignment.get(d) not in (None, 'A'):
                continue
            if sunday_pods_used is not None and d.weekday() == 6 and sunday_pods_used.get(d, 0) >= SUNDAY_PODS_PER_SUNDAY:
                continue



            if all(sessions_done[t] >= target_sessions for t in A_teams):
                break

            pods_today = 0

            # Try to schedule as many pods as possible on this date across distinct adjacent slot pairs.
            for (s1, s2) in adjacent_slot_pairs_by_date.get(d, []):
                if all(sessions_done[t] >= target_sessions for t in A_teams):
                    break

                # Cap A pods on Sundays
                if d.weekday() == 6 and pods_today >= MAX_A_PODS_PER_SUNDAY:
                    break

                free_fields = available_fields_for_pair(d, s1, s2)
                if len(free_fields) < 2:
                    continue

                eligible = [t for t in A_teams if can_play_pod(t, d) and sessions_done[t] < target_sessions]
                # If we're still trying to give every A team at least MIN_SUNDAY_SESSIONS_PER_TEAM on Sundays,
                # restrict the pool to teams that still need a Sunday session so we don't keep re-using the same 4 teams.
                if need_more_sunday and d.weekday() == 6:
                    need_sun = [t for t in eligible if sunday_sessions_done[t] < MIN_SUNDAY_SESSIONS_PER_TEAM]
                    if len(need_sun) >= 4:
                        eligible = need_sun

                if len(eligible) < 4:
                    continue

                four = choose_four(eligible)
                if not four:
                    continue
                t1, t2, t3, t4 = four

                # assign two distinct fields
                f1, f2 = free_fields[0], free_fields[1]

                ok = True
                ok &= place_game(d, s1, f1, t1, t2)  # t1 home
                ok &= place_game(d, s1, f2, t3, t4)  # t3 home
                ok &= place_game(d, s2, f1, t2, t3)  # t2 home (vs t3)
                ok &= place_game(d, s2, f2, t4, t1)  # t4 home (vs t1)
                if not ok:
                    continue

                for t in (t1, t2, t3, t4):
                    sessions_done[t] += 1
                    doubleheader_count[t] += 1

                pair_meets[frozenset((t1, t2))] += 1
                pair_meets[frozenset((t3, t4))] += 1
                pair_meets[frozenset((t2, t3))] += 1
                pair_meets[frozenset((t4, t1))] += 1

                progress = True
                pods_today += 1
                if sunday_pods_used is not None and d.weekday() == 6:
                    sunday_pods_used[d] = sunday_pods_used.get(d, 0) + 1
                if d.weekday() == 6:
                    for t in (t1, t2, t3, t4):
                        sunday_sessions_done[t] += 1
                # continue scanning later slot pairs on same date to potentially schedule another pod

        if not progress:
            break

    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, used_slots


# -------------------------------
# B/C/D Doubleheader pods (after A)
# -------------------------------
def _pop_matchup_any_orientation(unscheduled, a, b):
    """Remove and return one matchup between a and b (either (a,b) or (b,a))."""
    try:
        idx = unscheduled.index((a, b))
        return unscheduled.pop(idx)
    except ValueError:
        pass
    try:
        idx = unscheduled.index((b, a))
        return unscheduled.pop(idx)
    except ValueError:
        return None


def schedule_division_pod_doubleheaders(div, division_teams, unscheduled,
                                       team_availability, field_availability, team_blackouts, timeslots_by_date,
                                       team_stats, doubleheader_count, team_game_days, team_game_slots,
                                       team_doubleheader_opponents, used_slots, schedule=None, sunday_assignment=None, sunday_pods_used=None, team_preferred_days=None):
    """Schedule 4-team pod doubleheaders *within a division* to satisfy min_dh() targets.

    This uses the same A-style pod structure (two fields, two adjacent slots) so each of the 4 teams
    plays 2 games that day against DIFFERENT opponents.

    It only consumes matchups that already exist in `unscheduled` (either orientation), so we don't
    accidentally create extra games.
    """
    if schedule is None:
        schedule = []

    teams = list(division_teams.get(div, []))
    if len(teams) < 4:
        return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, unscheduled

    # Fast lookup for canonical datetime from field_availability
    dt_by_key = {(dt.date(), slot, field): dt for (dt, slot, field) in field_availability}

    # Date order follows field_availability sort (Sundays first), but we keep unique dates
    unique_dates = []
    seen = set()
    for dt, _slot, _field in field_availability:
        d = dt.date()
        if d not in seen:
            unique_dates.append(d)
            seen.add(d)

    # Fields available per date
    fields_by_date = defaultdict(set)
    for dt, _slot, field in field_availability:
        fields_by_date[dt.date()].add(field)

    def can_play_pod(team, d):
        dow = dow_abbrev(d)
        if not is_team_available(team, d, team_availability, team_blackouts):
            return False
        if team_game_days[team].get(d, 0) != 0:
            return False
        if not min_gap_ok(team, d, team_game_days):
            return False
        wk = d.isocalendar()[1]
        if team_stats[team]['weekly_games'].get(wk, 0) + 2 > WEEKLY_GAME_LIMIT:
            return False
        if team_stats[team]['total_games'] + 2 > target_games(team):
            return False
        if doubleheader_count[team] >= max_dh(team):
            return False
        return True

    def available_fields_for_pair(d, s1, s2):
        out = []
        for f in sorted(fields_by_date.get(d, [])):
            dt1 = dt_by_key.get((d, s1, f))
            dt2 = dt_by_key.get((d, s2, f))
            if dt1 is None or dt2 is None:
                continue
            if used_slots.get((dt1, s1, f), False) or used_slots.get((dt2, s2, f), False):
                continue
            out.append(f)
        return out

    def place_game(d, slot, field, t1, t2):
        """Place a single game (t1 vs t2) on (d,slot,field) with balanced home/away."""
        dt = dt_by_key.get((d, slot, field))
        if dt is None:
            return False
        # Hard cap
        if team_stats[t1]['total_games'] >= target_games(t1) or team_stats[t2]['total_games'] >= target_games(t2):
            return False
        home, away = decide_home_away(t1, t2, team_stats)

        # hard cap: never exceed target home balance too much
        if team_stats[home]['home_games'] >= HOME_AWAY_BALANCE and team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
            home, away = away, home
        schedule.append((dt, slot, field, home, home[0], away, away[0]))
        used_slots[(dt, slot, field)] = True

        wk = d.isocalendar()[1]
        team_stats[home]['total_games'] += 1
        team_stats[away]['total_games'] += 1
        team_stats[home]['home_games'] += 1
        team_stats[away]['away_games'] += 1
        team_stats[home]['weekly_games'][wk] = team_stats[home]['weekly_games'].get(wk, 0) + 1
        team_stats[away]['weekly_games'][wk] = team_stats[away]['weekly_games'].get(wk, 0) + 1
        team_game_days[home][d] += 1
        team_game_days[away][d] += 1
        team_game_slots[home][d].append(slot)
        team_game_slots[away][d].append(slot)
        return True

    season_start = min((dt.date() for dt, _slot, _field in field_availability), default=None)

    # Greedy scheduling: multiple passes to reach min_dh.
    for _pass in range(10):
        progress = False

        # Light day-of-week balancing:
        # Prefer scheduling pods on days this division has used less so far.
        dow_counts = defaultdict(int)
        for (dt0, _slot0, _field0, home0, _hd0, away0, _ad0) in schedule:
            if div_of(home0) == div and div_of(away0) == div:
                dow_counts[dow_label(dt0)] += 1

        rnd = random.Random((RANDOM_SEED or 0) + (_pass * 131) + ord(div))
        # Front-load the season, then use under-used weekdays and preferred-day coverage as tie-breakers.
        needers = [t for t in teams if doubleheader_count[t] < min_dh(t)]
        date_order = sorted(unique_dates, key=lambda dd: (
            late_date_penalty(dd, season_start),
            0 if dd.weekday() == 6 else 1,
            -preferred_day_count(needers, dd, team_preferred_days),
            dow_counts[dow_label(dd)],
            rnd.random()
        ))

        # Stop early if everyone in division has hit min DH
        if all(doubleheader_count[t] >= min_dh(t) for t in teams):
            break

        for d in date_order:
            # Sunday pod rotation: only allow this division's pods on Sundays assigned to it
            if sunday_assignment and d.weekday() == 6:
                assigned = sunday_assignment.get(d)
                # First pod on a Sunday is reserved for the assigned division (rotation).
                # If there is remaining Sunday pod capacity (SUNDAY_PODS_PER_SUNDAY > 1),
                # allow other divisions to use the extra pod(s).
                if sunday_pods_used is None:
                    if assigned not in (None, div):
                        continue
                else:
                    if sunday_pods_used.get(d, 0) == 0 and assigned not in (None, div):
                        continue
            if sunday_pods_used is not None and d.weekday() == 6 and sunday_pods_used.get(d, 0) >= SUNDAY_PODS_PER_SUNDAY:
                continue
                continue
            if all(doubleheader_count[t] >= min_dh(t) for t in teams):
                break

            # adjacent slot pairs available that date
            slots = sorted(set(timeslots_by_date.get(d, [])), key=lambda s: datetime.strptime(s.strip(), "%I:%M %p"))
            for i in range(len(slots) - 1):
                s1, s2 = slots[i], slots[i + 1]
                free_fields = available_fields_for_pair(d, s1, s2)
                if len(free_fields) < 2:
                    continue

                # pick 4 eligible teams that still need DHs
                eligible = [t for t in teams if can_play_pod(t, d) and doubleheader_count[t] < min_dh(t)]
                if len(eligible) < 4:
                    continue

                eligible.sort(key=lambda t: team_need_key(t, team_stats, doubleheader_count), reverse=True)
                pool = eligible[:10]

                chosen = None
                chosen_layout = None

                # Try combos then permutations to find one that matches existing matchups.
                for combo in itertools.combinations(pool, 4):
                    for perm in itertools.permutations(combo, 4):
                        t1, t2, t3, t4 = perm
                        # Need these undirected pairs available in unscheduled
                        needed_pairs = [(t1, t2), (t3, t4), (t1, t3), (t2, t4)]
                        if all(((a, b) in unscheduled or (b, a) in unscheduled) for (a, b) in needed_pairs):
                            chosen = combo
                            chosen_layout = (t1, t2, t3, t4)
                            break
                    if chosen_layout:
                        break
                if not chosen_layout:
                    continue

                t1, t2, t3, t4 = chosen_layout

                # Consume matchups (one each) BEFORE placing; if any pop fails, rollback and skip.
                pops = []
                ok = True
                for a, b in [(t1, t2), (t3, t4), (t1, t3), (t2, t4)]:
                    m = _pop_matchup_any_orientation(unscheduled, a, b)
                    if m is None:
                        ok = False
                        break
                    pops.append(m)
                if not ok:
                    # rollback
                    unscheduled.extend(pops)
                    continue

                f1, f2 = free_fields[0], free_fields[1]

                # Place pod games
                ok = True
                ok &= place_game(d, s1, f1, t1, t2)
                ok &= place_game(d, s1, f2, t3, t4)
                ok &= place_game(d, s2, f1, t1, t3)
                ok &= place_game(d, s2, f2, t2, t4)

                if not ok:
                    # rollback placements is messy; instead, mark failed by re-adding matchups
                    unscheduled.extend(pops)
                    continue

                # Mark DH day for each team and record opponents played that day
                for team, opps in (
                    (t1, {t2, t3}),
                    (t2, {t1, t4}),
                    (t3, {t4, t1}),
                    (t4, {t3, t2}),
                ):
                    doubleheader_count[team] += 1
                    team_doubleheader_opponents[team][d].update(opps)

                progress = True
                if sunday_pods_used is not None and d.weekday() == 6:
                    sunday_pods_used[d] = sunday_pods_used.get(d, 0) + 1
                # continue scanning for more pods on same date

        if not progress:
            break

    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, unscheduled

# -------------------------------
# Primary scheduling

# -------------------------------

def schedule_games(matchups, team_availability, field_availability, team_blackouts,
                   schedule, team_stats, doubleheader_count,
                   team_game_days, team_game_slots, team_doubleheader_opponents,
                   used_slots, timeslots_by_date, sunday_assignment=None, team_preferred_days=None):
    """
    Greedy single-game / DH-second-game placement for any remaining matchups.

    Performance note:
      The old retry/backtracking loop could take a long time when the remaining
      matchups are hard to place. This version does bounded multi-pass greedy
      filling: iterate all open slots, place the best matchup we can, repeat
      a few passes until no progress.

    Returns updated schedule + remaining unscheduled matchups.
    """
    unscheduled = list(matchups)

    def slot_ok_for_team(team, d, slot):
        # cannot play same timeslot twice in a day
        if slot in team_game_slots[team][d]:
            return False

        # If team already has a game today, the next game must be the immediate next timeslot (DH adjacency rule)
        if team_game_slots[team][d]:
            current = team_game_slots[team][d][0]
            sorted_slots = timeslots_by_date[d]
            try:
                idx = sorted_slots.index(current)
            except ValueError:
                return False
            if idx + 1 >= len(sorted_slots):
                return False
            required_slot = sorted_slots[idx + 1]
            return slot == required_slot

        return True

    # More passes helps the greedy filler converge after pods consume many prime slots.
    season_start = min((dt.date() for dt, _slot, _field in field_availability), default=None)
    max_passes = 20
    for _pass in range(max_passes):
        progress_made = False

        slots_iter = sorted(
            field_availability,
            key=lambda x: (x[0].date(), datetime.strptime(x[1].strip(), "%I:%M %p"), x[2])
        )
        for date, slot, field in slots_iter:
            if used_slots.get((date, slot, field), False):
                continue

            d = date.date()
            day_of_week = dow_label(date)
            week_num = date.isocalendar()[1]

            best = None
            best_score = -1

            for (t1, t2) in unscheduled:
                # A games are scheduled only by A-pod / A-pair routines
                if div_of(t1) == 'A' or div_of(t2) == 'A':
                    continue

                # availability / blackouts
                if not (is_team_available(t1, d, team_availability, team_blackouts) and is_team_available(t2, d, team_availability, team_blackouts)):
                    continue

                # target / weekly limits
                if team_stats[t1]['total_games'] >= target_games(t1) or team_stats[t2]['total_games'] >= target_games(t2):
                    continue
                if (team_stats[t1]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or
                    team_stats[t2]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT):
                    continue

                # min gap
                if not (min_gap_ok(t1, d, team_game_days) and min_gap_ok(t2, d, team_game_days)):
                    continue

                # hard cadence rule: no two consecutive bye weeks
                if not (no_two_consecutive_byes_after_adding(t1, d, team_game_days) and no_two_consecutive_byes_after_adding(t2, d, team_game_days)):
                    continue

                # slot adjacency rules for DH second game
                if not slot_ok_for_team(t1, d, slot) or not slot_ok_for_team(t2, d, slot):
                    continue

                # DH constraints: if either team is adding a 2nd game today, enforce max DH days and "different opponent same day"
                can_double = True
                for team, opp in ((t1, t2), (t2, t1)):
                    if team_game_days[team][d] == 1:
                        if doubleheader_count[team] >= max_dh(team):
                            can_double = False
                            break
                        if team_doubleheader_opponents[team][d] and opp in team_doubleheader_opponents[team][d]:
                            can_double = False
                            break
                if not can_double:
                    continue

                score = matchup_need_score(t1, t2, team_stats, doubleheader_count)

                # Soft gap preference (allow 2-day gaps, prefer 3+)
                score -= preferred_gap_penalty(t1, d, team_game_days)
                score -= preferred_gap_penalty(t2, d, team_game_days)

                # Strong preference for placements that break up long layoffs
                score += idle_gap_repair_bonus(t1, d, team_game_days)
                score += idle_gap_repair_bonus(t2, d, team_game_days)

                # Strong preference for teams at risk of a second straight bye week
                score += bye_week_urgency_bonus(t1, d, team_game_days)
                score += bye_week_urgency_bonus(t2, d, team_game_days)

                # Prefer team-friendly days where possible
                score += preferred_day_bonus(t1, t2, d, team_preferred_days)

                # Front-load the schedule to preserve later dates for rainouts / makeup games
                score -= late_date_penalty(d, season_start)

                if sunday_assignment and d.weekday() == 6:
                    assigned = sunday_assignment.get(d)
                    if assigned and div_of(t1) == assigned and div_of(t2) == assigned:
                        score += 500

                # Pepper singles: if this placement would create a doubleheader day,
                # prefer doing so only when that team still *needs* DH days.
                # (Otherwise we can strand the schedule at ~15–18 games because DH
                # consumes the full weekly limit.)
                dh_penalty = 0
                for team in (t1, t2):
                    if team_game_days[team][d] == 1 and doubleheader_count[team] >= min_dh(team):
                        dh_penalty += 2000
                score -= dh_penalty
                if score > best_score:
                    best_score = score
                    best = (t1, t2)

            if best is None:
                continue

            t1, t2 = best
            home, away = decide_home_away(t1, t2, team_stats)

            # Hard cap to avoid exceeding desired home balance too much
            if team_stats[home]['home_games'] >= HOME_AWAY_BALANCE:
                if team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
                    home, away = away, home
                else:
                    continue

            schedule.append((date, slot, field, home, home[0], away, away[0]))

            for team in (home, away):
                team_stats[team]['total_games'] += 1
                team_stats[team]['weekly_games'][week_num] += 1
                team_game_slots[team][d].append(slot)
                team_game_days[team][d] += 1

            team_stats[home]['home_games'] += 1
            team_stats[away]['away_games'] += 1

            for team, opp in ((home, away), (away, home)):
                if team_game_days[team][d] == 2:
                    doubleheader_count[team] += 1
                    team_doubleheader_opponents[team][d].add(opp)

            used_slots[(date, slot, field)] = True
            unscheduled.remove((t1, t2))
            progress_made = True

        if not progress_made:
            break

    if unscheduled:
        print("Warning: Some predetermined matchups could not be scheduled ({} remaining).".format(len(unscheduled)))

    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, unscheduled


def fill_missing_games(schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
                       team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled,
                       team_availability, team_blackouts, field_availability, sunday_assignment=None, team_preferred_days=None):
    """
    Top-up pass after schedule_games. Works only with remaining unscheduled matchups.
    Uses the same bounded multi-pass greedy approach as schedule_games.
    """
    remaining = list(unscheduled)

    def slot_ok_for_team(team, d, slot):
        if slot in team_game_slots[team][d]:
            return False
        if team_game_slots[team][d]:
            current = team_game_slots[team][d][0]
            sorted_slots = timeslots_by_date[d]
            try:
                idx = sorted_slots.index(current)
            except ValueError:
                return False
            if idx + 1 >= len(sorted_slots):
                return False
            required_slot = sorted_slots[idx + 1]
            return slot == required_slot
        return True

    season_start = min((dt.date() for dt, _slot, _field in field_availability), default=None)
    max_passes = 20
    for _pass in range(max_passes):
        progress = False

        # stop early if nobody is below target or we have no matchups left
        if not remaining:
            break
        if not any(team_stats[t]['total_games'] < target_games(t) for t in team_stats.keys()):
            break

        slots_iter = sorted(
            field_availability,
            key=lambda x: (x[0].date(), datetime.strptime(x[1].strip(), "%I:%M %p"), x[2])
        )
        for date, slot, field in slots_iter:
            if used_slots.get((date, slot, field), False):
                continue

            d = date.date()
            day_of_week = dow_label(date)
            week_num = date.isocalendar()[1]

            best = None
            best_score = -1

            for (t1, t2) in remaining:
                if div_of(t1) == 'A' or div_of(t2) == 'A':
                    continue

                # if both teams already at target, skip
                if team_stats[t1]['total_games'] >= target_games(t1) or team_stats[t2]['total_games'] >= target_games(t2):
                    continue

                if not (is_team_available(t1, d, team_availability, team_blackouts) and is_team_available(t2, d, team_availability, team_blackouts)):
                    continue

                if (team_stats[t1]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or
                    team_stats[t2]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT):
                    continue

                if not (min_gap_ok(t1, d, team_game_days) and min_gap_ok(t2, d, team_game_days)):
                    continue

                if not slot_ok_for_team(t1, d, slot) or not slot_ok_for_team(t2, d, slot):
                    continue

                can_double = True
                for team, opp in ((t1, t2), (t2, t1)):
                    if team_game_days[team][d] == 1:
                        if doubleheader_count[team] >= max_dh(team):
                            can_double = False
                            break
                        if opp in team_doubleheader_opponents[team][d]:
                            can_double = False
                            break
                if not can_double:
                    continue

                score = matchup_need_score(t1, t2, team_stats, doubleheader_count)

                # Strong preference for placements that break up long layoffs
                score += idle_gap_repair_bonus(t1, d, team_game_days)
                score += idle_gap_repair_bonus(t2, d, team_game_days)

                # Strong preference for teams at risk of a second straight bye week
                score += bye_week_urgency_bonus(t1, d, team_game_days)
                score += bye_week_urgency_bonus(t2, d, team_game_days)

                # Prefer team-friendly days where possible
                score += preferred_day_bonus(t1, t2, d, team_preferred_days)

                # Front-load the schedule to preserve later dates for rainouts / makeup games
                score -= late_date_penalty(d, season_start)

                if sunday_assignment and d.weekday() == 6:
                    assigned = sunday_assignment.get(d)
                    if assigned and div_of(t1) == assigned and div_of(t2) == assigned:
                        score += 500

                dh_penalty = 0
                for team in (t1, t2):
                    if team_game_days[team][d] == 1 and doubleheader_count[team] >= min_dh(team):
                        dh_penalty += 2000
                score -= dh_penalty
                if score > best_score:
                    best_score = score
                    best = (t1, t2)

            if best is None:
                continue

            t1, t2 = best
            home, away = decide_home_away(t1, t2, team_stats)

            if team_stats[home]['home_games'] >= HOME_AWAY_BALANCE:
                if team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
                    home, away = away, home
                else:
                    continue

            schedule.append((date, slot, field, home, home[0], away, away[0]))

            for team in (home, away):
                team_stats[team]['total_games'] += 1
                team_stats[team]['weekly_games'][week_num] += 1
                team_game_slots[team][d].append(slot)
                team_game_days[team][d] += 1

            team_stats[home]['home_games'] += 1
            team_stats[away]['away_games'] += 1

            for team, opp in ((home, away), (away, home)):
                if team_game_days[team][d] == 2:
                    doubleheader_count[team] += 1
                    team_doubleheader_opponents[team][d].add(opp)

            used_slots[(date, slot, field)] = True
            remaining.remove((t1, t2))
            progress = True

        if not progress:
            break

    return schedule, team_stats, doubleheader_count, remaining


def build_slot_rows(field_availability, scheduled_games):
    """
    Returns list of rows (one per field_availability entry) with blank home/away when unused.
    scheduled_games: list of game tuples (datetime, slot_str, field, home, home_div, away, away_div)
    """
    game_by_key = {}
    for g in scheduled_games:
        dt, slot, field, home, home_div, away, away_div = g
        game_by_key[(dt.date(), slot, field)] = g

    rows = []
    for dt, slot, field in field_availability:
        g = game_by_key.get((dt.date(), slot, field))
        if g is None:
            rows.append((dt, slot, field, "", "", "", ""))
        else:
            _, _, _, home, home_div, away, away_div = g
            rows.append((dt, slot, field, home, home_div, away, away_div))
    return rows




def output_schedule_to_csv_full(field_availability, schedule, output_file):
    rows = build_slot_rows(field_availability, schedule)
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Day", "Time", "Diamond", "Home Team", "Home Division", "Away Team", "Away Division"])
        for dt, slot, field, home, home_div, away, away_div in rows:
            writer.writerow([dt.strftime('%Y-%m-%d'), dow_label(dt), slot, field, home, home_div, away, away_div])
    return rows


# -------------------------------
# Unscheduled matchups reporting (for manual fill)
# -------------------------------
def summarize_remaining_matchups(remaining_matchups):
    """Aggregate remaining matchups into counts.

    Returns:
      oriented_counts: dict[(home, away)] -> count
      unordered_counts: dict[(tmin, tmax)] -> count
    """
    oriented = defaultdict(int)
    unordered = defaultdict(int)
    for a, b in remaining_matchups or []:
        oriented[(a, b)] += 1
        t1, t2 = (a, b) if a <= b else (b, a)
        unordered[(t1, t2)] += 1
    return oriented, unordered


def _current_unordered_meet_counts(schedule):
    """Return dict[(tmin,tmax)] -> games already scheduled between the pair."""
    counts = defaultdict(int)
    for (date_str, time_str, field_id, home, home_div, away, away_div) in schedule or []:
        if not home or not away:
            continue
        t1, t2 = (home, away) if home <= away else (away, home)
        counts[(t1, t2)] += 1
    return counts


def suggest_best_fit_manual_matchups(all_teams, schedule, team_stats, doubleheader_count,
                                     team_availability=None, team_blackouts=None, max_pairs=None):
    """Greedy 'best fit' list of matchups among ONLY teams currently short of target games.

    Goal: produce a simple, reasonable list to manually place that:
      - fixes game deficits (as much as possible)
      - prefers pairs that have played each other the least (based on current schedule matrix)
      - breaks ties by pairing teams with bigger deficits

    Returns list of dict rows ready for XLSX export.
    """
    if not team_stats:
        return []

    # 1) identify teams short
    needs = {t: max(0, target_games(t) - int(team_stats[t].get('total_games', 0))) for t in all_teams}
    short = sorted([t for t in all_teams if needs.get(t, 0) > 0])
    if not short:
        return []

    total_missing = sum(needs[t] for t in short)
    meet_counts = _current_unordered_meet_counts(schedule)

    # 2) greedy pairing
    remaining = dict(needs)
    rows = []
    # Cap to what math allows
    target_pairs = total_missing // 2
    if max_pairs is not None:
        target_pairs = min(target_pairs, int(max_pairs))

    def _pair_key(t1, t2):
        a, b = (t1, t2) if t1 <= t2 else (t2, t1)
        played = meet_counts.get((a, b), 0)
        # lower played is better; higher opponent need is better; prefer intra slightly (optional)
        same_div = 1 if div_of(t1) == div_of(t2) else 0
        return (played, -remaining.get(t2, 0), -same_div, t2)

    while len(rows) < target_pairs:
        # pick the team with biggest games deficit; tie-break by DH deficit
        candidates1 = [t for t in short if remaining.get(t, 0) > 0]
        if len(candidates1) < 2:
            break
        t1 = sorted(candidates1, key=lambda t: (-remaining[t], -dh_deficit(t, doubleheader_count), t))[0]

        candidates2 = [t for t in candidates1 if t != t1]
        if not candidates2:
            break
        t2 = sorted(candidates2, key=lambda t: _pair_key(t1, t))[0]

        a, b = (t1, t2) if t1 <= t2 else (t2, t1)
        played = meet_counts.get((a, b), 0)

        rows.append({
            "Team 1": t1,
            "Div 1": div_of(t1),
            "Needs 1": int(remaining.get(t1, 0)),
            "DH Need 1": int(dh_deficit(t1, doubleheader_count)),
            "Team 2": t2,
            "Div 2": div_of(t2),
            "Needs 2": int(remaining.get(t2, 0)),
            "DH Need 2": int(dh_deficit(t2, doubleheader_count)),
            "Current Meetings": int(played),
            "Type": "INTRA" if div_of(t1) == div_of(t2) else "INTER",
            "Common Avail Days": _common_avail_days(t1, t2, team_availability),
            "Blackouts": _blackout_summary(t1, t2, team_blackouts),
        })

        # update remaining deficits
        remaining[t1] = max(0, remaining.get(t1, 0) - 1)
        remaining[t2] = max(0, remaining.get(t2, 0) - 1)

    # Add a small tail note if odd deficit remains (can't be paired cleanly)
    leftover = [(t, remaining[t]) for t in short if remaining.get(t, 0) > 0]
    if leftover:
        rows.append({
            "Team 1": "",
            "Div 1": "",
            "Needs 1": "",
            "DH Need 1": "",
            "Team 2": "",
            "Div 2": "",
            "Needs 2": "",
            "DH Need 2": "",
            "Current Meetings": "",
            "Type": "",
            "Common Avail Days": "",
            "Blackouts": "",
        })
        rows.append({
            "Team 1": "Leftover needs (odd / not pairable):",
            "Div 1": "",
            "Needs 1": ", ".join([f"{t}:{n}" for t, n in leftover]),
            "DH Need 1": "",
            "Team 2": "",
            "Div 2": "",
            "Needs 2": "",
            "DH Need 2": "",
            "Current Meetings": "",
            "Type": "",
            "Common Avail Days": "",
            "Blackouts": "",
        })

    return rows


def output_unscheduled_matchups_csv(remaining_matchups, output_file):
    """Write remaining matchups to CSV, aggregated by unordered pair.

    Columns: Division1, Team1, Division2, Team2, RemainingGames
    """
    _oriented, unordered = summarize_remaining_matchups(remaining_matchups)
    rows = []
    for (t1, t2), cnt in sorted(unordered.items(), key=lambda x: (-x[1], x[0][0], x[0][1])):
        rows.append((div_of(t1), t1, div_of(t2), t2, cnt))

    with open(output_file, mode='w', newline='') as f:
        w = csv.writer(f)
        w.writerow(["Division1", "Team1", "Division2", "Team2", "RemainingGames"])
        for r in rows:
            w.writerow(list(r))
    return rows

def output_team_remaining_needs_csv(all_teams, team_stats, doubleheader_count, output_file):
    """Write per-team remaining needs to CSV.

    Columns: Division, Team, TargetGames, ScheduledGames, GamesRemaining, MinDH, ScheduledDHDays, DHDaysRemainingToMin
    """
    with open(output_file, mode='w', newline='') as f:
        w = csv.writer(f)
        w.writerow(["Division","Team","TargetGames","ScheduledGames","GamesRemaining","MinDH","ScheduledDHDays","DHDaysRemainingToMin"])
        for t in sorted(all_teams, key=lambda x: (div_of(x), x)):
            target = target_games(t)
            scheduled = team_stats[t]['total_games']
            games_rem = max(0, target - scheduled)
            mindh = min_dh(t)
            dh_done = doubleheader_count[t]
            dh_rem = max(0, mindh - dh_done)
            w.writerow([div_of(t), t, target, scheduled, games_rem, mindh, dh_done, dh_rem])

def add_unscheduled_to_workbook(wb, remaining_matchups, all_teams, team_stats, doubleheader_count, sched_last, weeks_count=None):
    """Add two sheets: Unscheduled (one row per remaining matchup) and Remaining Needs.

    Unscheduled is intentionally NOT aggregated so you can walk down the list and paste games
    into open slots, then delete rows as you go.

    Also adds a formula column that lists week numbers where BOTH teams currently have 0 games
    scheduled (updates automatically when you edit the Schedule sheet).
    """
    if remaining_matchups is None:
        remaining_matchups = []

    # ---------------- Unscheduled ----------------
    ws_u = wb.create_sheet("Unscheduled")
    ws_u.append(["Home Team", "Away Team", "Home Div", "Away Div", "WeeksBothZero"])
    for cell in ws_u[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
        cell.alignment = Alignment(horizontal="center")

    # Determine the Weeks range (written by export_schedule_to_xlsx)
    # weeks_count is the number of week numbers in Weeks!A2:A{...}
    if not weeks_count:
        # best-effort: infer by scanning the Weeks sheet (if present)
        try:
            ws_w = wb["Weeks"]
            weeks_count = max(0, ws_w.max_row - 1)
        except Exception:
            weeks_count = 0

    weeks_range = None
    if weeks_count and weeks_count > 0:
        weeks_range = f"Weeks!$A$2:$A${weeks_count+1}"

    for i, (home, away) in enumerate(remaining_matchups, start=2):
        ws_u.cell(row=i, column=1, value=home)
        ws_u.cell(row=i, column=2, value=away)
        ws_u.cell(row=i, column=3, value=div_of(home))
        ws_u.cell(row=i, column=4, value=div_of(away))

        if weeks_range:
            # Excel 365 dynamic array formula
            ws_u.cell(
                row=i,
                column=5,
                value=(
                    f'=IFERROR(LET('
                    f'w,{weeks_range},'
                    f'h,$A{i},a,$B{i},'
                    f'hg,COUNTIFS(Schedule!$I$2:$I${sched_last},w,Schedule!$E$2:$E${sched_last},h)+COUNTIFS(Schedule!$I$2:$I${sched_last},w,Schedule!$F$2:$F${sched_last},h),'
                    f'ag,COUNTIFS(Schedule!$I$2:$I${sched_last},w,Schedule!$E$2:$E${sched_last},a)+COUNTIFS(Schedule!$I$2:$I${sched_last},w,Schedule!$F$2:$F${sched_last},a),'
                    f'TEXTJOIN(", ",TRUE,FILTER(w,(hg=0)*(ag=0)))'
                    f'),"")'
                )
            )
        else:
            ws_u.cell(row=i, column=5, value="")

    _autofit(ws_u, ws_u.max_row, 5, min_width=10, max_width=24)
    ws_u.freeze_panes = "A2"
    ws_u.auto_filter.ref = f"A1:E{ws_u.max_row}"

    # ---------------- Remaining Needs ----------------
    ws_n = wb.create_sheet("Remaining Needs")
    ws_n.append(["Division","Team","TargetGames","ScheduledGames","GamesRemaining","MinDH","ScheduledDHDays","DHDaysRemainingToMin"])
    for cell in ws_n[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
        cell.alignment = Alignment(horizontal="center")

    for t in sorted(all_teams, key=lambda x: (div_of(x), x)):
        target = target_games(t)
        scheduled = team_stats[t]['total_games']
        games_rem = max(0, target - scheduled)
        mindh = min_dh(t)
        dh_done = doubleheader_count[t]
        dh_rem = max(0, mindh - dh_done)
        ws_n.append([div_of(t), t, target, scheduled, games_rem, mindh, dh_done, dh_rem])

    _autofit(ws_n, ws_n.max_row, 8, min_width=12, max_width=22)
    ws_n.freeze_panes = "A2"
    ws_n.auto_filter.ref = f"A1:H{ws_n.max_row}"

# -------------------------------
# XLSX export (formulas + conditional formatting + matchup matrix)
# -------------------------------
def _autofit(ws, max_row, max_col, min_width=10, max_width=40):
    for col in range(1, max_col + 1):
        letter = get_column_letter(col)
        best = 0
        for r in range(1, max_row + 1):
            v = ws.cell(row=r, column=col).value
            if v is None:
                continue
            best = max(best, len(str(v)))
        ws.column_dimensions[letter].width = max(min_width, min(max_width, best + 2))

def _schedule_row_annotations(rows, team_preferred_days=None):
    """Return per-row annotations for Schedule export."""
    scheduled_only = []
    for idx, (dt, slot, field, home, home_div, away, away_div) in enumerate(rows, start=2):
        if home and away:
            scheduled_only.append((idx, dt, slot, field, home, home_div, away, away_div))

    # Running totals / previous dates per team
    running = defaultdict(int)
    previous_dates = {}
    metadata = {}
    by_team_date = defaultdict(int)
    by_pair = defaultdict(list)

    for idx, dt, slot, field, home, home_div, away, away_div in scheduled_only:
        d = dt.date()
        by_team_date[(home, d)] += 1
        by_team_date[(away, d)] += 1
        by_pair[tuple(sorted((home, away)))].append(d)

    pair_seen = defaultdict(int)
    for idx, dt, slot, field, home, home_div, away, away_div in scheduled_only:
        d = dt.date()
        running[home] += 1
        running[away] += 1
        home_last = previous_dates.get(home)
        away_last = previous_dates.get(away)
        home_days = (d - home_last).days if home_last else ""
        away_days = (d - away_last).days if away_last else ""

        same_day_home = by_team_date[(home, d)]
        same_day_away = by_team_date[(away, d)]
        is_dh = same_day_home > 1 or same_day_away > 1
        pair_key = tuple(sorted((home, away)))
        pair_seen[pair_key] += 1
        pair_dates = sorted(set(by_pair[pair_key]))
        recent_repeat = False
        if len(pair_dates) > 1:
            pos = pair_dates.index(d)
            prev_d = pair_dates[pos - 1] if pos > 0 else None
            next_d = pair_dates[pos + 1] if pos + 1 < len(pair_dates) else None
            if prev_d and (d - prev_d).days < 14:
                recent_repeat = True
            if next_d and (next_d - d).days < 14:
                recent_repeat = True

        game_type_parts = ["INTRA" if home_div == away_div else "INTER"]
        if is_dh:
            game_type_parts.append("DH")
        else:
            game_type_parts.append("SINGLE")

        pref_label = "N/A"
        if team_preferred_days:
            dow = dow_label(dt)
            home_pref = dow in team_preferred_days.get(home, set())
            away_pref = dow in team_preferred_days.get(away, set())
            if home_pref and away_pref:
                pref_label = "Both"
            elif home_pref or away_pref:
                pref_label = "One"
            else:
                pref_label = "None"

        flags = []
        if home_days != "" and home_days > MAX_IDLE_DAYS:
            flags.append(f"{home} layoff")
        if away_days != "" and away_days > MAX_IDLE_DAYS:
            flags.append(f"{away} layoff")
        if pref_label == "None":
            flags.append("Non-preferred day")
        if recent_repeat:
            flags.append("Quick rematch")

        metadata[idx] = {
            "game_type": " ".join(game_type_parts),
            "home_after": running[home],
            "away_after": running[away],
            "home_last": home_last,
            "away_last": away_last,
            "home_days_since": home_days,
            "away_days_since": away_days,
            "preferred_match": pref_label,
            "flag": "; ".join(flags) if flags else "OK",
        }
        previous_dates[home] = d
        previous_dates[away] = d

    return metadata


def _build_team_summary(schedule, all_teams, team_stats, doubleheader_count, team_preferred_days=None):
    by_team_dates = defaultdict(list)
    preferred_hits = defaultdict(int)
    preferred_misses = defaultdict(int)

    for (dt, _time, _field, home, _home_div, away, _away_div) in sorted(schedule, key=lambda g: (g[0], g[1], g[2])):
        d = dt.date() if hasattr(dt, 'date') else dt
        by_team_dates[home].append(d)
        by_team_dates[away].append(d)
        if team_preferred_days:
            dow = dow_label(dt)
            for t in (home, away):
                if dow in team_preferred_days.get(t, set()):
                    preferred_hits[t] += 1
                else:
                    preferred_misses[t] += 1

    rows = []
    for t in sorted(all_teams, key=lambda x: (div_of(x), x)):
        dates = sorted(set(by_team_dates.get(t, [])))
        longest_gap = 0
        max_gap_warning = ""
        if len(dates) >= 2:
            longest_gap = max((dates[i] - dates[i - 1]).days for i in range(1, len(dates)))
            if longest_gap > MAX_IDLE_DAYS:
                max_gap_warning = f"> {MAX_IDLE_DAYS} days"
        rows.append({
            "Division": div_of(t),
            "Team": t,
            "Team Name": "",
            "Total Games": int(team_stats[t].get('total_games', 0)) if team_stats else 0,
            "Home": int(team_stats[t].get('home_games', 0)) if team_stats else 0,
            "Away": int(team_stats[t].get('away_games', 0)) if team_stats else 0,
            "DH Days": int(doubleheader_count[t]) if doubleheader_count else 0,
            "Last Scheduled Game": dates[-1] if dates else "",
            "Longest Gap": longest_gap,
            "Max Gap Warning": max_gap_warning or "OK",
            "Preferred Hits": preferred_hits[t] if team_preferred_days else "",
            "Preferred Misses": preferred_misses[t] if team_preferred_days else "",
            "Games Remaining": max(0, target_games(t) - (int(team_stats[t].get('total_games', 0)) if team_stats else 0)),
            "DH Remaining To Min": max(0, min_dh(t) - (int(doubleheader_count[t]) if doubleheader_count else 0)),
        })
    return rows


def export_schedule_to_xlsx(field_availability, schedule, division_teams, output_path, remaining_matchups=None, team_stats=None, doubleheader_count=None, team_availability=None, team_blackouts=None, team_preferred_days=None):
    if Workbook is None:
        raise RuntimeError("openpyxl is not installed. Run: pip install openpyxl")

    rows = build_slot_rows(field_availability, schedule)

    wb = Workbook()
    try:
        wb.calculation.calcMode = "auto"
        wb.calculation.fullCalcOnLoad = True
    except Exception:
        pass

    all_teams = sorted([t for div in sorted(division_teams.keys()) for t in division_teams[div]])
    annotations = _schedule_row_annotations(rows, team_preferred_days=team_preferred_days)

    # ---------------- Schedule ----------------
    ws = wb.active
    ws.title = "Schedule"

    headers = [
        "Date", "Day", "Time", "Diamond", "Home Team", "Away Team", "Home Div", "Away Div",
        "Week #", "SlotIndex", "Game Type", "Home Games After", "Away Games After",
        "Home Last Game", "Away Last Game", "Home Days Since Last", "Away Days Since Last",
        "Preferred Match", "Flag"
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.fill = PatternFill("solid", fgColor="D9E1F2")

    slots_by_date = defaultdict(list)
    for dt0, slot0, _field0 in field_availability:
        d0 = dt0.date()
        slots_by_date[d0].append(slot0)
    slot_index_by_date_slot = {}
    for d0, slots0 in slots_by_date.items():
        uniq = sorted(set(slots0), key=lambda s: datetime.strptime(s.strip(), "%I:%M %p"))
        for i, s in enumerate(uniq, start=1):
            slot_index_by_date_slot[(d0, s)] = i

    for excel_row, (dt, slot, field, home, home_div, away, away_div) in enumerate(rows, start=2):
        d = dt.date()
        wk = d.isocalendar()[1]
        slot_idx = slot_index_by_date_slot.get((d, slot), "")
        meta = annotations.get(excel_row, {})
        ws.append([
            d, dow_label(dt), slot, field, home, away, home_div, away_div, wk, slot_idx,
            meta.get("game_type", "OPEN" if not home else ""),
            meta.get("home_after", ""), meta.get("away_after", ""),
            meta.get("home_last", ""), meta.get("away_last", ""),
            meta.get("home_days_since", ""), meta.get("away_days_since", ""),
            meta.get("preferred_match", ""), meta.get("flag", "Open Slot" if not home else "")
        ])

    n = len(rows)
    for r in range(2, n + 2):
        ws.cell(row=r, column=1).number_format = "yyyy-mm-dd"
        ws.cell(row=r, column=3).number_format = "@"
        ws.cell(row=r, column=14).number_format = "yyyy-mm-dd"
        ws.cell(row=r, column=15).number_format = "yyyy-mm-dd"

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:S{n + 1}"
    ws.column_dimensions['J'].hidden = True
    _autofit(ws, n + 1, 19)

    # ---------------- Teams ----------------
    ws_t = wb.create_sheet("Teams")
    ws_t.append(["Team", "Division", "Team Name"])
    for cell in ws_t[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
    for t in all_teams:
        ws_t.append([t, div_of(t), ""])
    ws_t.freeze_panes = "A2"
    ws_t.auto_filter.ref = f"A1:C{len(all_teams)+1}"
    _autofit(ws_t, len(all_teams) + 1, 3, min_width=8, max_width=24)

    # ---------------- Team Summary ----------------
    ws_ts = wb.create_sheet("Team Summary")
    summary_rows = _build_team_summary(schedule, all_teams, team_stats or defaultdict(dict), doubleheader_count or defaultdict(int), team_preferred_days=team_preferred_days)
    summary_headers = [
        "Division", "Team", "Team Name", "Total Games", "Home", "Away", "DH Days",
        "Last Scheduled Game", "Longest Gap", "Max Gap Warning", "Preferred Hits",
        "Preferred Misses", "Games Remaining", "DH Remaining To Min"
    ]
    ws_ts.append(summary_headers)
    for cell in ws_ts[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
        cell.alignment = Alignment(horizontal="center")
    for row_num, row in enumerate(summary_rows, start=2):
        ws_ts.append([row[h] for h in summary_headers])
        ws_ts.cell(row=row_num, column=3, value=f'=IFERROR(VLOOKUP(B{row_num},Teams!$A:$C,3,FALSE),B{row_num})')
        ws_ts.cell(row=row_num, column=8).number_format = "yyyy-mm-dd"
    ws_ts.freeze_panes = "A2"
    ws_ts.auto_filter.ref = f"A1:N{len(summary_rows)+1}"
    _autofit(ws_ts, len(summary_rows)+1, 14, min_width=10, max_width=24)

    # ---------------- Open Slots ----------------
    ws_o = wb.create_sheet("Open Slots")
    ws_o.append(["Date", "Day", "Time", "Diamond", "Week #", "Season Phase"])
    for cell in ws_o[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
        cell.alignment = Alignment(horizontal="center")
    used_keys = {(dt.date(), slot, field) for (dt, slot, field, home, _hd, away, _ad) in rows if home and away}
    season_dates = sorted(dt.date() for dt, _, _ in field_availability)
    season_start = season_dates[0] if season_dates else None
    season_end = season_dates[-1] if season_dates else None
    total_span = max(1, (season_end - season_start).days) if season_start and season_end else 1
    open_rows = 0
    for dt, slot, field in field_availability:
        if (dt.date(), slot, field) in used_keys:
            continue
        open_rows += 1
        day_offset = (dt.date() - season_start).days if season_start else 0
        ratio = day_offset / total_span if total_span else 0
        phase = "Early" if ratio < 0.34 else ("Mid" if ratio < 0.67 else "Late")
        ws_o.append([dt.date(), dow_label(dt), slot, field, dt.date().isocalendar()[1], phase])
        ws_o.cell(row=open_rows + 1, column=1).number_format = "yyyy-mm-dd"
    ws_o.freeze_panes = "A2"
    ws_o.auto_filter.ref = f"A1:F{max(2, open_rows+1)}"
    _autofit(ws_o, max(2, open_rows+1), 6, min_width=10, max_width=18)

    # ---------------- Upload ----------------
    ws_up = wb.create_sheet("Upload")
    upload_headers = ["Date", "Time", "Type", "Duration", "Home Team", "Home Division", "Away Team", "Away Division", "Location"]
    ws_up.append(upload_headers)
    for cell in ws_up[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
        cell.alignment = Alignment(horizontal="center")
    upload_row = 2
    for sched_row in range(2, n + 2):
        if not ws.cell(row=sched_row, column=5).value or not ws.cell(row=sched_row, column=6).value:
            continue
        ws_up.cell(row=upload_row, column=1, value=f"=Schedule!A{sched_row}")
        ws_up.cell(row=upload_row, column=2, value=f"=Schedule!C{sched_row}")
        ws_up.cell(row=upload_row, column=3, value="Game")
        ws_up.cell(row=upload_row, column=4, value=f'=IF(Schedule!B{sched_row}="Sun",80,70)')
        ws_up.cell(row=upload_row, column=5, value=f'=IFERROR(VLOOKUP(Schedule!E{sched_row},Teams!$A:$C,3,FALSE),Schedule!E{sched_row})')
        ws_up.cell(row=upload_row, column=6, value=f'="Division "&Schedule!G{sched_row}')
        ws_up.cell(row=upload_row, column=7, value=f'=IFERROR(VLOOKUP(Schedule!F{sched_row},Teams!$A:$C,3,FALSE),Schedule!F{sched_row})')
        ws_up.cell(row=upload_row, column=8, value=f'="Division "&Schedule!H{sched_row}')
        ws_up.cell(row=upload_row, column=9, value=f"=Schedule!D{sched_row}")
        ws_up.cell(row=upload_row, column=1).number_format = "yyyy-mm-dd"
        upload_row += 1
    ws_up.freeze_panes = "A2"
    ws_up.auto_filter.ref = f"A1:I{max(2, upload_row-1)}"
    _autofit(ws_up, max(2, upload_row-1), 9, min_width=10, max_width=24)

    # ---------------- Suggested Manual Matchups ----------------
    ws_s = wb.create_sheet("Suggested Matchups")
    ws_s.append(["Team 1", "Div 1", "Needs 1", "DH Need 1",
                 "Team 2", "Div 2", "Needs 2", "DH Need 2",
                 "Current Meetings", "Type", "Common Avail Days", "Blackouts"])
    for cell in ws_s[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
        cell.alignment = Alignment(horizontal="center")

    suggested_rows = suggest_best_fit_manual_matchups(
        all_teams=all_teams,
        schedule=schedule,
        team_stats=team_stats,
        doubleheader_count=doubleheader_count or {t: 0 for t in all_teams},
        team_availability=team_availability,
        team_blackouts=team_blackouts,
    )

    for row in suggested_rows:
        ws_s.append([
            row.get("Team 1", ""), row.get("Div 1", ""), row.get("Needs 1", ""), row.get("DH Need 1", ""),
            row.get("Team 2", ""), row.get("Div 2", ""), row.get("Needs 2", ""), row.get("DH Need 2", ""),
            row.get("Current Meetings", ""), row.get("Type", ""), row.get("Common Avail Days", ""), row.get("Blackouts", "")
        ])

    last_s = max(2, len(suggested_rows) + 1)
    ws_s.freeze_panes = "A2"
    ws_s.auto_filter.ref = f"A1:L{last_s}"
    for rr in range(2, last_s + 1):
        ws_s.cell(row=rr, column=11).alignment = Alignment(wrap_text=True, vertical="top")
        ws_s.cell(row=rr, column=12).alignment = Alignment(wrap_text=True, vertical="top")
    _autofit(ws_s, last_s, 12, min_width=10, max_width=24)

    # ---------------- Unscheduled Matches ----------------
    ws_u = wb.create_sheet("Unscheduled Matches")
    ws_u.append(["Home Div", "Home Team", "Away Div", "Away Team", "Remaining", "Home Needs", "Away Needs", "Type", "Available Days", "Blackouts"])
    for cell in ws_u[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
        cell.alignment = Alignment(horizontal="center")

    rows_u = []
    if remaining_matchups:
        if team_stats is not None:
            current_games = {t: int(team_stats[t].get('total_games', 0)) for t in all_teams}
        else:
            current_games = {t: 0 for t in all_teams}
            for (d, time_str, field_id, home, home_div, away, away_div) in schedule:
                if home and away:
                    current_games[home] = current_games.get(home, 0) + 1
                    current_games[away] = current_games.get(away, 0) + 1

        needs = {t: max(0, target_games(t) - current_games.get(t, 0)) for t in all_teams}
        below = {t for t in all_teams if needs.get(t, 0) > 0}

        oriented, _unordered = summarize_remaining_matchups(remaining_matchups)

        for (home, away), cnt in oriented.items():
            if home == away:
                continue
            if (home in below) or (away in below):
                rows_u.append((
                    div_of(home), home,
                    div_of(away), away,
                    int(cnt),
                    int(needs.get(home, 0)),
                    int(needs.get(away, 0)),
                    "INTRA" if div_of(home) == div_of(away) else "INTER",
                    _common_avail_days(home, away, team_availability),
                    _blackout_summary(home, away, team_blackouts)
                ))

        rows_u.sort(key=lambda r: (-r[4], r[0], r[1], r[2], r[3]))

    for r in rows_u:
        ws_u.append(list(r))

    last_u = max(2, len(rows_u) + 1)
    ws_u.freeze_panes = "A2"
    ws_u.auto_filter.ref = f"A1:J{last_u}"
    for rr in range(2, last_u + 1):
        ws_u.cell(row=rr, column=9).alignment = Alignment(wrap_text=True, vertical="top")
        ws_u.cell(row=rr, column=10).alignment = Alignment(wrap_text=True, vertical="top")
    _autofit(ws_u, last_u, 10, min_width=10, max_width=22)

    # ---------------- TeamDate (helper: games/day + non-adjacent DH detection) ----------------
    ws_td = wb.create_sheet("TeamDate")
    ws_td.append(["Key", "Date", "Team", "GamesThatDay", "MinSlot", "MaxSlot", "NonAdjFlag", "WeekNum"])
    for cell in ws_td[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")

    unique_dates = sorted({dt.date() for (dt, _, _) in field_availability})

    sched_first = 2
    sched_last = n + 1
    date_rng = f"Schedule!$A${sched_first}:$A${sched_last}"
    home_rng = f"Schedule!$E${sched_first}:$E${sched_last}"
    away_rng = f"Schedule!$F${sched_first}:$F${sched_last}"
    home_div_rng = f"Schedule!$G${sched_first}:$G${sched_last}"
    away_div_rng = f"Schedule!$H${sched_first}:$H${sched_last}"
    week_rng = f"Schedule!$I${sched_first}:$I${sched_last}"
    slotidx_rng = f"Schedule!$J${sched_first}:$J${sched_last}"
    day_rng = f"Schedule!$B${sched_first}:$B${sched_last}"

    row_idx = 2
    for d in unique_dates:
        wk = d.isocalendar()[1]
        for t in all_teams:
            ws_td.cell(row=row_idx, column=1, value='=TEXT($B{r},"yyyymmdd")&"|"&$C{r}'.format(r=row_idx))
            ws_td.cell(row=row_idx, column=2, value=d)
            ws_td.cell(row=row_idx, column=3, value=t)
            ws_td.cell(
                row=row_idx,
                column=4,
                value='=COUNTIFS({date_rng},$B{r},{home_rng},$C{r})+COUNTIFS({date_rng},$B{r},{away_rng},$C{r})'.format(
                    date_rng=date_rng, home_rng=home_rng, away_rng=away_rng, r=row_idx
                )
            )
            ws_td.cell(
                row=row_idx,
                column=5,
                value='=MIN(IFERROR(MINIFS({slotidx_rng},{date_rng},$B{r},{home_rng},$C{r}),9999),IFERROR(MINIFS({slotidx_rng},{date_rng},$B{r},{away_rng},$C{r}),9999))'.format(
                    slotidx_rng=slotidx_rng, date_rng=date_rng, home_rng=home_rng, away_rng=away_rng, r=row_idx
                )
            )
            ws_td.cell(
                row=row_idx,
                column=6,
                value='=MAX(IFERROR(MAXIFS({slotidx_rng},{date_rng},$B{r},{home_rng},$C{r}),0),IFERROR(MAXIFS({slotidx_rng},{date_rng},$B{r},{away_rng},$C{r}),0))'.format(
                    slotidx_rng=slotidx_rng, date_rng=date_rng, home_rng=home_rng, away_rng=away_rng, r=row_idx
                )
            )
            ws_td.cell(row=row_idx, column=7, value='=IF($D{r}<>2,0,IF($F{r}-$E{r}=1,0,1))'.format(r=row_idx))
            ws_td.cell(row=row_idx, column=8, value=wk)
            ws_td.cell(row=row_idx, column=2).number_format = "yyyy-mm-dd"
            row_idx += 1

    td_last = row_idx - 1
    ws_td.freeze_panes = "A2"
    ws_td.auto_filter.ref = f"A1:H{td_last}"
    _autofit(ws_td, td_last, 8, min_width=10, max_width=18)

    # ---------------- Weeks (helper for Unscheduled formulas) ----------------
    ws_w = wb.create_sheet("Weeks")
    ws_w.append(["WeekNum"])
    for cell in ws_w[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
    unique_weeks = sorted({dt.date().isocalendar()[1] for (dt, _, _) in field_availability})
    for wk in unique_weeks:
        ws_w.append([wk])
    ws_w.freeze_panes = "A2"
    ws_w.auto_filter.ref = f"A1:A{len(unique_weeks)+1}"

    # Legacy helper tabs
    add_unscheduled_to_workbook(wb, remaining_matchups, all_teams, team_stats or defaultdict(dict), doubleheader_count or defaultdict(int), sched_last, weeks_count=len(unique_weeks))

    wb.save(output_path)


def generate_matchup_table(schedule, division_teams):
    matchup_count = defaultdict(lambda: defaultdict(int))
    for date, slot, field, home_team, home_div, away_team, away_div in schedule:
        matchup_count[home_team][away_team] += 1
        matchup_count[away_team][home_team] += 1

    all_teams = sorted([team for teams in division_teams.values() for team in teams])

    if PrettyTable:
        table = PrettyTable()
        table.field_names = ["Team"] + all_teams
        for team in all_teams:
            row = [team] + [matchup_count[team][opp] for opp in all_teams]
            table.add_row(row)
        print("\nMatchup Table:")
        print(table)
    else:
        print("\nMatchup Table (CSV):")
        print("Team," + ",".join(all_teams))
        for team in all_teams:
            row = [str(matchup_count[team][opp]) for opp in all_teams]
            print(team + "," + ",".join(row))

# -------------------------------
# Main
# -------------------------------

def print_schedule_summary(team_stats):
    rows = []
    for team in sorted(team_stats.keys(), key=lambda t: (t[0], int(t[1:]) if t[1:].isdigit() else t[1:])):
        stats = team_stats[team]
        rows.append([team[0], team, target_games(team), stats.get('total_games', 0), stats.get('home_games', 0), stats.get('away_games', 0)])
    print("\nSchedule Summary:")
    if PrettyTable:
        table = PrettyTable()
        table.field_names = ["Division", "Team", "Target", "Total Games", "Home Games", "Away Games"]
        for row in rows:
            table.add_row(row)
        print(table)
    else:
        print("Division,Team,Target,Total Games,Home Games,Away Games")
        for row in rows:
            print(",".join(str(x) for x in row))


def print_doubleheader_summary(doubleheader_count):
    rows = []
    for team in sorted(doubleheader_count.keys(), key=lambda t: (t[0], int(t[1:]) if t[1:].isdigit() else t[1:])):
        rows.append([team[0], team, min_dh(team), doubleheader_count.get(team, 0)])
    print("\nDoubleheader Summary:")
    if PrettyTable:
        table = PrettyTable()
        table.field_names = ["Division", "Team", "Min DH", "DH Days"]
        for row in rows:
            table.add_row(row)
        print(table)
    else:
        print("Division,Team,Min DH,DH Days")
        for row in rows:
            print(",".join(str(x) for x in row))


def main():
    global RUN_SEED
    # --- RNG setup ---
    global RANDOM_SEED

    if RANDOM_SEED is None:
        import os
        RUN_SEED = int.from_bytes(os.urandom(4), "big")
    else:
        RUN_SEED = RANDOM_SEED

    random.seed(RUN_SEED)
    print(f"Using RNG seed: {RUN_SEED}")

    team_availability = load_team_availability('team_availability.csv')
    team_preferred_days = load_team_preferred_days('team_preferred_days.csv')
    # Debug: confirm we loaded what we think we loaded
    _ta_path = os.path.abspath('team_availability.csv')
    print(f"Loaded team availability from: {_ta_path} (teams={len(team_availability)})")
    for _t in sorted([t for t in team_availability if len(team_availability[t]) < 6]):
        print(f"  Restricted: {_t}: {sorted(team_availability[_t])}")
    if 'C1' in team_availability:
        print(f"  Sanity C1: {sorted(team_availability['C1'])}")
    if 'C2' in team_availability:
        print(f"  Sanity C2: {sorted(team_availability['C2'])}")

    if team_preferred_days:
        _tp_path = os.path.abspath('team_preferred_days.csv')
        print(f"Loaded team preferred days from: {_tp_path} (teams={len(team_preferred_days)})")
        for _t in sorted(team_preferred_days):
            if team_preferred_days[_t]:
                print(f"  Preferred: {_t}: {sorted(team_preferred_days[_t])}")
    else:
        print("No team_preferred_days.csv found (preferred day bonus disabled).")

    field_availability = load_field_availability('field_availability.csv')
    global SEASON_START_DATE
    SEASON_START_DATE = min((dt.date() for dt, _slot, _field in field_availability), default=None)
    team_blackouts = load_team_blackouts('team_blackouts.csv')

    division_teams = {
        'A': ["A{}".format(i+1) for i in range(8)],
        'B': ["B{}".format(i+1) for i in range(8)],
        'C': ["C{}".format(i+1) for i in range(6)],
        'D': ["D{}".format(i+1) for i in range(6)],
    }
    all_teams = [t for div in ('A', 'B', 'C', 'D') for t in division_teams[div]]

    schedule = []
    team_stats = defaultdict(lambda: {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'weekly_games': defaultdict(int),
            })
    used_slots = {}
    team_game_days = defaultdict(lambda: defaultdict(int))
    team_game_slots = defaultdict(lambda: defaultdict(list))
    team_doubleheader_opponents = defaultdict(lambda: defaultdict(set))
    doubleheader_count = defaultdict(int)

    timeslots_by_date = defaultdict(list)
    for date, slot, field in field_availability:
        d = date.date()
        if slot not in timeslots_by_date[d]:
            timeslots_by_date[d].append(slot)
    for d in timeslots_by_date:
        timeslots_by_date[d].sort(key=lambda s: datetime.strptime(s.strip(), "%I:%M %p"))

    for t in all_teams:
        _ = team_stats[t]

    # ---------------------------------------------------------
    # Sunday pod rotation assignment (for pod-style doubleheaders on Sundays)
    # ---------------------------------------------------------
    # We rotate which division is allowed to run *pod* doubleheaders on each Sunday.
    # This prevents one division (often A) from soaking up all Sunday inventory.
    #
    # IMPORTANT: This affects pod-style DH only. Singles can still be scheduled on Sundays.
    sunday_assignment = build_sunday_pod_assignment(
        timeslots_by_date,
        rotation=SUNDAY_POD_ROTATION,
        seed=(RANDOM_SEED if RANDOM_SEED is not None else random.randint(1, 10_000_000))
    )

    # Track total pods used per Sunday across all divisions (hard cap via SUNDAY_PODS_PER_SUNDAY)
    # Format: {date: int}
    sunday_pods_used = {}
  
    matchups = generate_full_matchups(division_teams)
    print("\nTotal generated matchups (unscheduled): {}".format(len(matchups)))

    unscheduled = matchups[:]

    # Schedule Division A FIRST so B/C/D don't consume the prime Sunday + adjacent-slot inventory
    # that A requires to hit 11 DH days per team.
    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     used_slots) = schedule_A_pod_doubleheaders(
        division_teams, team_availability, field_availability, team_blackouts, timeslots_by_date,
        team_stats, doubleheader_count, team_game_days, team_game_slots, used_slots, schedule,
        sunday_assignment=sunday_assignment, sunday_pods_used=sunday_pods_used,
        team_preferred_days=team_preferred_days
    )

    # Remove any remaining A matchups from the single-game pool (A is DH-only).
    unscheduled = [m for m in unscheduled if div_of(m[0]) != 'A' and div_of(m[1]) != 'A']

    # Build B/C/D doubleheader pods (same-day 2-game sets) BEFORE single-game placement.
    # Pod structure guarantees teams do NOT play the same opponent back-to-back in a DH.
    for div in ('B', 'C', 'D'):
        (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
         team_doubleheader_opponents, used_slots, unscheduled) = schedule_division_pod_doubleheaders(
            div, division_teams, unscheduled,
            team_availability, field_availability, team_blackouts, timeslots_by_date,
            team_stats, doubleheader_count, team_game_days, team_game_slots,
            team_doubleheader_opponents, used_slots, schedule,
            sunday_assignment=sunday_assignment, sunday_pods_used=sunday_pods_used,
            team_preferred_days=team_preferred_days)

    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     team_doubleheader_opponents, used_slots, unscheduled) = schedule_games(
        unscheduled, team_availability, field_availability, team_blackouts,
        schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
        team_doubleheader_opponents, used_slots, timeslots_by_date,
        sunday_assignment=sunday_assignment, team_preferred_days=team_preferred_days)

    if any(team_stats[t]['total_games'] < target_games(t) for t in all_teams):
        print("Filling missing games...")
        (schedule, team_stats, doubleheader_count, unscheduled) = fill_missing_games(
            schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
            team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled,
            team_availability, team_blackouts, field_availability,
            sunday_assignment=sunday_assignment, team_preferred_days=team_preferred_days)

    missing = [t for t in all_teams if team_stats[t]['total_games'] < target_games(t)]

    over = [t for t in all_teams if team_stats[t]['total_games'] > target_games(t)]
    if over:
        print('Critical: Teams ABOVE target games (hard cap violated): {}'.format(over))
    if missing:
        print("Critical: Teams below target games: {}".format(missing))

    under_dh = [t for t in all_teams if doubleheader_count[t] < min_dh(t)]
    if under_dh:
        print("Critical: Teams below minimum DH days: {}".format(under_dh))

    idle_gap_violations = check_max_idle_gap(schedule, all_teams)
    if idle_gap_violations:
        print("Critical: Teams with layoff gaps greater than {} days (showing up to 50):".format(MAX_IDLE_DAYS))
        for v in idle_gap_violations[:50]:
            print("  ", v)

    bye_week_violations = [(t, max_consecutive_byes(t, team_game_days)) for t in all_teams if max_consecutive_byes(t, team_game_days) > MAX_CONSECUTIVE_BYE_WEEKS]
    if bye_week_violations:
        print("Critical: Teams with more than {} consecutive bye week(s):".format(MAX_CONSECUTIVE_BYE_WEEKS))
        for t, gap in bye_week_violations:
            print("   {} -> {} consecutive bye weeks".format(t, gap))

    # Export CSV + XLSX with full slot list (row count == field_availability)
    # Hard validation: no team is scheduled on a disallowed day
    _av_viol = check_schedule_against_availability(schedule, team_availability)
    if _av_viol:
        print("ERROR: Team availability violations detected (showing up to 50):")
        for v in _av_viol[:50]:
            print("  ", v)
        raise SystemExit(2)

    output_schedule_to_csv_full(field_availability, schedule, 'softball_schedule.csv')
    # Also write templates for manual scheduling
    output_unscheduled_matchups_csv(unscheduled, 'unscheduled_matchups.csv')
    output_team_remaining_needs_csv(all_teams, team_stats, doubleheader_count, 'team_remaining_needs.csv')
    export_schedule_to_xlsx(field_availability, schedule, division_teams, 'softball_schedule.xlsx', remaining_matchups=unscheduled, team_stats=team_stats, doubleheader_count=doubleheader_count, team_availability=team_availability, team_blackouts=team_blackouts, team_preferred_days=team_preferred_days)

    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    print_doubleheader_summary(doubleheader_count)
    generate_matchup_table(schedule, division_teams)
    print("\nWrote: softball_schedule.csv ({} rows)".format(len(field_availability)))
    print("Wrote: softball_schedule.xlsx")

if __name__ == "__main__":
    main()
