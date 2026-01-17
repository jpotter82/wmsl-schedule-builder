#!/usr/bin/env python3
"""
Softball Scheduler (CP-SAT / OR-Tools) — per-team INTER overrides + rich debug output

Flow:
  1) load/normalize inputs
  2) generate required matchup demand from per-team INTER targets + derived per-team INTRA targets
  3) build CP-SAT model to assign games to slots (hard constraints) + optimize soft preferences
  4) validate final schedule
  5) export schedule.csv + print debug summaries + top violations

Requires:
  pip install ortools
Optional (for nicer tables):
  pip install prettytable

CSV inputs:
  field_availability.csv: date(YYYY-MM-DD), time(HH:MM AM/PM), field_id
  team_availability.csv:  team_id, day1, day2, ...   where days are: Mon Tue Wed Thu Fri Sat Sun
  blackout_dates.csv:     team_id, date(YYYY-MM-DD), [optional time(HH:MM AM/PM)]
    - If time blank => team cannot play any game that date.
    - If time present => team cannot play that specific date+time.
"""

import csv
import math
import random
from dataclasses import dataclass
from datetime import datetime, date
from collections import defaultdict, Counter
from typing import Dict, List, Tuple, Set, Optional

from ortools.sat.python import cp_model


# ============================================================
# CONFIG (EDIT THIS)
# ============================================================

RANDOM_SEED = 42

FIELD_AVAILABILITY_CSV = "field_availability.csv"
TEAM_AVAILABILITY_CSV = "team_availability.csv"
BLACKOUTS_CSV = "blackout_dates.csv"

GAMES_PER_TEAM = 22
HOME_GAMES_TARGET = 11

MIN_DAYS_BETWEEN = 5         # min rest days between games (except within doubleheader)
SUNDAY_WEIGHT = 5            # (currently used indirectly via penalties; see PENALTIES)

# Teams per division (IDs must match CSVs)
DIVISION_TEAMS: Dict[str, List[str]] = {
    "A": [f"A{i}" for i in range(1, 7)],   # A1..A6
    "B": [f"B{i}" for i in range(1, 7)],   # B1..B6
    "C": [f"C{i}" for i in range(1, 9)],   # C1..C8
    "D": [f"D{i}" for i in range(1, 9)],   # D1..D8
}

# Fixed INTRA targets for divisions where you want the intra count locked.
# For any division omitted, INTRA will be derived per-team as (GAMES_PER_TEAM - total_inter_for_team).
INTRA_DIV_GAMES_FIXED: Dict[str, int] = {
    "A": 22,  # A plays only A
    "B": 16,  # B plays B 2x+ and remainder vs C
    "C": 14,  # C plays C 2x each
    # D flexible based on D vs C inter
}

# NEW: per-team inter targets (OVERRIDES)
# team_id -> {other_division_letter: games_vs_that_division}
# Teams not listed default to 0 inter games.
INTER_TEAM_TARGETS: Dict[str, Dict[str, int]] = {
    # B: remainder vs C only (B intra fixed 16 => inter total 6)
    **{f"B{i}": {"C": 6} for i in range(1, 7)},

    # C: inter total 8 per team (since intra fixed 14). Uneven split:
    **{f"C{i}": {"B": 5, "D": 3} for i in range(1, 5)},
    **{f"C{i}": {"B": 4, "D": 4} for i in range(5, 9)},

    # D: remainder vs C only; uneven per team to match totals:
    **{f"D{i}": {"C": 4} for i in range(1, 5)},
    **{f"D{i}": {"C": 3} for i in range(5, 9)},

    # A omitted => no inter
}

INTER_DIV_MAX_PER_OPPONENT = 1  # usually 1 for inter-division play (avoid repeats)

# Doubleheader sessions by division (1 session = 2 games)
DOUBLE_HEADERS_BY_DIV: Dict[str, Dict[str, int]] = {
    "A": {"min_sessions": 11, "max_sessions": 11},
    "B": {"min_sessions": 4,  "max_sessions": 6},
    "C": {"min_sessions": 3,  "max_sessions": 5},
    "D": {"min_sessions": 3,  "max_sessions": 5},
}

# Soft objective weights (bigger = more important)
# NOTE: Sunday preference is implemented as a penalty for NON-Sunday slots.
PENALTIES = {
    "home_away_deviation": 50,  # per game deviation from HOME_GAMES_TARGET
    "non_sunday_slot": 1,       # penalty for using non-Sunday slots
}

# Debug / Reporting
DEBUG = True
USE_PRETTYTABLE = True
DEBUG_MATCHUP_TABLE = True          # big output
DEBUG_INTER_TARGETS = True
TOP_VIOLATIONS_N = 10               # how many "worst offenders" to show

OUTPUT_SCHEDULE_CSV = "schedule.csv"


# ============================================================
# DATA STRUCTURES
# ============================================================

DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass(frozen=True)
class Slot:
    slot_id: int
    d: date
    time_str: str
    field_id: str
    dow: str
    is_sunday: bool
    dt_sort_key: Tuple

    @property
    def date_str(self) -> str:
        return self.d.strftime("%Y-%m-%d")


@dataclass(frozen=True)
class Game:
    game_id: int
    t1: str
    t2: str
    div1: str
    div2: str
    kind: str  # "INTRA" or "INTER"


# ============================================================
# HELPERS
# ============================================================

def parse_time_key(time_str: str) -> Tuple[int, int]:
    dt = datetime.strptime(time_str.strip(), "%I:%M %p")
    return (dt.hour, dt.minute)


def team_division(team: str) -> str:
    return team[0].upper()


def all_teams_list() -> List[str]:
    return sorted([t for div in DIVISION_TEAMS for t in DIVISION_TEAMS[div]])


def _try_prettytable():
    if not USE_PRETTYTABLE:
        return None
    try:
        from prettytable import PrettyTable
        return PrettyTable
    except Exception:
        return None


# ============================================================
# CSV LOADERS
# ============================================================

def load_field_availability(path: str) -> List[Slot]:
    slots: List[Slot] = []
    with open(path, newline="") as f:
        r = csv.reader(f)
        header = next(r, None)
        if not header:
            raise ValueError(f"{path}: empty file")
        for row in r:
            if not row or len(row) < 3:
                continue
            d = datetime.strptime(row[0].strip(), "%Y-%m-%d").date()
            time_str = row[1].strip()
            field_id = row[2].strip()
            dow = DAY_ABBR[d.weekday()]
            slots.append(
                Slot(
                    slot_id=len(slots),
                    d=d,
                    time_str=time_str,
                    field_id=field_id,
                    dow=dow,
                    is_sunday=(dow == "Sun"),
                    dt_sort_key=(d.toordinal(),) + parse_time_key(time_str),
                )
            )

    # stable sort then re-id
    slots.sort(key=lambda s: (s.d.toordinal(), parse_time_key(s.time_str), s.field_id))
    slots = [
        Slot(
            slot_id=i,
            d=s.d,
            time_str=s.time_str,
            field_id=s.field_id,
            dow=s.dow,
            is_sunday=s.is_sunday,
            dt_sort_key=s.dt_sort_key,
        )
        for i, s in enumerate(slots)
    ]
    return slots


def load_team_availability(path: str) -> Dict[str, Set[str]]:
    avail: Dict[str, Set[str]] = {}
    with open(path, newline="") as f:
        r = csv.reader(f)
        _header = next(r, None)
        for row in r:
            if not row:
                continue
            team = row[0].strip()
            days = {c.strip() for c in row[1:] if c and c.strip()}
            avail[team] = days
    return avail


def load_blackouts(path: str) -> Tuple[Dict[str, Set[date]], Dict[str, Set[Tuple[date, str]]]]:
    by_date: Dict[str, Set[date]] = defaultdict(set)
    by_datetime: Dict[str, Set[Tuple[date, str]]] = defaultdict(set)

    with open(path, newline="") as f:
        r = csv.reader(f)
        _header = next(r, None)
        for row in r:
            if not row or len(row) < 2:
                continue
            team = row[0].strip()
            d = datetime.strptime(row[1].strip(), "%Y-%m-%d").date()
            t = row[2].strip() if len(row) >= 3 and row[2] else ""
            if t:
                by_datetime[team].add((d, t))
            else:
                by_date[team].add(d)

    return by_date, by_datetime


# ============================================================
# TARGET DERIVATION + SANITY
# ============================================================

def derive_inter_targets_per_team() -> Dict[str, Dict[str, int]]:
    targets: Dict[str, Dict[str, int]] = {}
    for t in all_teams_list():
        tdiv = team_division(t)
        dmap = dict(INTER_TEAM_TARGETS.get(t, {}))
        if tdiv in dmap:
            raise ValueError(f"INTER_TEAM_TARGETS: team {t} has self-division entry {tdiv} (invalid).")
        for od, c in dmap.items():
            if od not in DIVISION_TEAMS:
                raise ValueError(f"INTER_TEAM_TARGETS: team {t} references unknown division {od}.")
            if c < 0:
                raise ValueError(f"INTER_TEAM_TARGETS: team {t} has negative target vs {od}.")
        targets[t] = dmap
    return targets


def sanity_check_config_and_targets(inter_targets: Dict[str, Dict[str, int]]) -> Dict[str, int]:
    teams = all_teams_list()

    intra_per_team: Dict[str, int] = {}
    for t in teams:
        itot = sum(inter_targets.get(t, {}).values())
        if itot > GAMES_PER_TEAM:
            raise ValueError(f"Team {t} has inter total {itot} > {GAMES_PER_TEAM}.")
        intra_per_team[t] = GAMES_PER_TEAM - itot

    for div, fixed_intra in INTRA_DIV_GAMES_FIXED.items():
        for t in DIVISION_TEAMS[div]:
            if intra_per_team[t] != fixed_intra:
                raise ValueError(
                    f"Fixed intra mismatch: {t} derived intra={intra_per_team[t]} but "
                    f"INTRA_DIV_GAMES_FIXED[{div}]={fixed_intra}."
                )

    for div, tlist in DIVISION_TEAMS.items():
        s = sum(intra_per_team[t] for t in tlist)
        if s % 2 != 0:
            raise ValueError(f"Intra degree sum for division {div} is odd ({s}); cannot form integer games.")

    # Inter totals consistency across division pairs
    total_div_pair = defaultdict(int)  # (from_div,to_div) totals
    for t in teams:
        from_div = team_division(t)
        for to_div, c in inter_targets[t].items():
            total_div_pair[(from_div, to_div)] += c

    divs = sorted(DIVISION_TEAMS.keys())
    for i in range(len(divs)):
        for j in range(i + 1, len(divs)):
            d1, d2 = divs[i], divs[j]
            a = total_div_pair[(d1, d2)]
            b = total_div_pair[(d2, d1)]
            if a != b:
                raise ValueError(f"Inter totals mismatch {d1}<->{d2}: {d1}->{d2}={a} but {d2}->{d1}={b}.")

    # Doubleheader bounds
    for div, cfg in DOUBLE_HEADERS_BY_DIV.items():
        mn = cfg["min_sessions"]
        mx = cfg["max_sessions"]
        if mn > mx:
            raise ValueError(f"Doubleheaders bounds invalid for {div}: min_sessions > max_sessions")
        if 2 * mn > GAMES_PER_TEAM:
            raise ValueError(f"Doubleheaders impossible for {div}: min_sessions implies >{GAMES_PER_TEAM} games")

    return intra_per_team


# ============================================================
# MATCHUP DEMAND GENERATION
# ============================================================

def generate_intra_edges_variable_degrees(
    teams: List[str],
    degree_target: Dict[str, int],
    rnd: random.Random,
    max_per_opponent: Optional[int] = None,
) -> List[Tuple[str, str, str]]:
    n = len(teams)
    if n < 2:
        raise ValueError("Division must have at least 2 teams for intra scheduling.")

    need = {t: int(degree_target[t]) for t in teams}
    if any(v < 0 for v in need.values()):
        raise ValueError("Negative intra degree target encountered.")
    if sum(need.values()) % 2 != 0:
        raise ValueError("Sum of intra degrees must be even.")

    if max_per_opponent is None:
        max_deg = max(need.values()) if need else 0
        max_per_opponent = math.ceil(max_deg / max(1, n - 1)) + 2

    pair_count = defaultdict(int)
    games: List[Tuple[str, str, str]] = []

    while True:
        remaining = [t for t in teams if need[t] > 0]
        if not remaining:
            break

        remaining.sort(key=lambda t: (-need[t], t))
        u = remaining[0]

        candidates = [
            v for v in teams
            if v != u and need[v] > 0 and pair_count[tuple(sorted((u, v)))] < max_per_opponent
        ]
        if not candidates:
            candidates = [v for v in teams if v != u and need[v] > 0]
            if not candidates:
                raise RuntimeError(f"Intra generation stuck: team {u} still needs {need[u]} but no candidates remain.")

        rnd.shuffle(candidates)
        candidates.sort(key=lambda v: (pair_count[tuple(sorted((u, v)))], -need[v], v))

        v = candidates[0]
        p = tuple(sorted((u, v)))
        pair_count[p] += 1
        need[u] -= 1
        need[v] -= 1
        games.append((u, v, "INTRA"))

    return games


def generate_bipartite_edges_variable_degrees(
    left_teams: List[str],
    right_teams: List[str],
    left_targets: Dict[str, int],
    right_targets: Dict[str, int],
    max_per_opponent: int,
    rnd: random.Random,
) -> List[Tuple[str, str]]:
    L_need = {u: int(left_targets.get(u, 0)) for u in left_teams}
    R_need = {v: int(right_targets.get(v, 0)) for v in right_teams}

    if any(x < 0 for x in L_need.values()) or any(x < 0 for x in R_need.values()):
        raise ValueError("Negative bipartite degree target encountered.")

    total_L = sum(L_need.values())
    total_R = sum(R_need.values())
    if total_L != total_R:
        raise ValueError(f"Bipartite totals mismatch: left={total_L} right={total_R}")

    pair_count = defaultdict(int)
    edges: List[Tuple[str, str]] = []

    left_order = left_teams[:]
    right_order = right_teams[:]
    rnd.shuffle(left_order)
    rnd.shuffle(right_order)

    while True:
        remaining_left = [u for u in left_order if L_need[u] > 0]
        remaining_right = [v for v in right_order if R_need[v] > 0]
        if not remaining_left and not remaining_right:
            break
        if not remaining_left or not remaining_right:
            raise RuntimeError("Bipartite construction stuck: one side still has needs but the other doesn't.")

        remaining_left.sort(key=lambda u: (-L_need[u], u))
        u = remaining_left[0]

        candidates = [v for v in right_order if R_need[v] > 0 and pair_count[(u, v)] < max_per_opponent]
        if not candidates:
            raise RuntimeError(
                f"No feasible inter opponent for {u}. remaining={L_need[u]} cap={max_per_opponent}. "
                f"Consider relaxing INTER_DIV_MAX_PER_OPPONENT or targets."
            )

        rnd.shuffle(candidates)
        candidates.sort(key=lambda v: (-R_need[v], v))
        v = candidates[0]

        edges.append((u, v))
        pair_count[(u, v)] += 1
        L_need[u] -= 1
        R_need[v] -= 1

    return edges


def generate_all_games(rnd: random.Random) -> List[Game]:
    inter_targets = derive_inter_targets_per_team()
    intra_per_team = sanity_check_config_and_targets(inter_targets)

    raw_pairs: List[Tuple[str, str, str]] = []

    # INTRA
    for div, teams in DIVISION_TEAMS.items():
        degree_target = {t: intra_per_team[t] for t in teams}
        raw_pairs.extend(generate_intra_edges_variable_degrees(teams, degree_target, rnd))

    # INTER
    divs = sorted(DIVISION_TEAMS.keys())
    for i in range(len(divs)):
        for j in range(i + 1, len(divs)):
            d1, d2 = divs[i], divs[j]
            left = DIVISION_TEAMS[d1]
            right = DIVISION_TEAMS[d2]

            left_targets = {u: inter_targets[u].get(d2, 0) for u in left}
            right_targets = {v: inter_targets[v].get(d1, 0) for v in right}

            if sum(left_targets.values()) == 0 and sum(right_targets.values()) == 0:
                continue

            edges = generate_bipartite_edges_variable_degrees(
                left_teams=left,
                right_teams=right,
                left_targets=left_targets,
                right_targets=right_targets,
                max_per_opponent=INTER_DIV_MAX_PER_OPPONENT,
                rnd=rnd,
            )
            for u, v in edges:
                raw_pairs.append((u, v, "INTER"))

    games: List[Game] = []
    for gid, (a, b, kind) in enumerate(raw_pairs):
        games.append(Game(gid, a, b, team_division(a), team_division(b), kind))

    # sanity: each team plays exactly GAMES_PER_TEAM demand
    cnt = Counter()
    for g in games:
        cnt[g.t1] += 1
        cnt[g.t2] += 1
    for t in all_teams_list():
        if cnt[t] != GAMES_PER_TEAM:
            raise RuntimeError(f"Demand generation error: team {t} has {cnt[t]} games, expected {GAMES_PER_TEAM}")

    return games


# ============================================================
# SOLVER
# ============================================================

def build_feasible_slots_for_game(
    game: Game,
    slots: List[Slot],
    team_avail: Dict[str, Set[str]],
    blackout_by_date: Dict[str, Set[date]],
    blackout_by_dt: Dict[str, Set[Tuple[date, str]]],
) -> List[int]:
    tA, tB = game.t1, game.t2
    availA = team_avail.get(tA, set(DAY_ABBR))
    availB = team_avail.get(tB, set(DAY_ABBR))
    bdateA = blackout_by_date.get(tA, set())
    bdateB = blackout_by_date.get(tB, set())
    bdtA = blackout_by_dt.get(tA, set())
    bdtB = blackout_by_dt.get(tB, set())

    feasible = []
    for s in slots:
        if s.dow not in availA or s.dow not in availB:
            continue
        if s.d in bdateA or s.d in bdateB:
            continue
        if (s.d, s.time_str) in bdtA or (s.d, s.time_str) in bdtB:
            continue
        feasible.append(s.slot_id)
    return feasible


def solve_schedule(
    games: List[Game],
    slots: List[Slot],
    team_avail: Dict[str, Set[str]],
    blackout_by_date: Dict[str, Set[date]],
    blackout_by_dt: Dict[str, Set[Tuple[date, str]]],
) -> Tuple[Dict[int, int], Dict[int, int]]:
    model = cp_model.CpModel()
    all_teams = all_teams_list()

    # slots grouped by datetime, and times per date
    slots_by_datetime: Dict[Tuple[date, str], List[int]] = defaultdict(list)
    times_by_date: Dict[date, List[str]] = defaultdict(list)
    for s in slots:
        slots_by_datetime[(s.d, s.time_str)].append(s.slot_id)
        if s.time_str not in times_by_date[s.d]:
            times_by_date[s.d].append(s.time_str)
    for d in times_by_date:
        times_by_date[d].sort(key=parse_time_key)

    adjacency_pairs: List[Tuple[date, str, str]] = []
    for d, tlist in times_by_date.items():
        for i in range(len(tlist) - 1):
            adjacency_pairs.append((d, tlist[i], tlist[i + 1]))

    x: Dict[Tuple[int, int], cp_model.IntVar] = {}
    feasible_slots_for_game: Dict[int, List[int]] = {}
    home_is_t1: Dict[int, cp_model.IntVar] = {}
    games_for_slot: Dict[int, List[cp_model.IntVar]] = defaultdict(list)
    team_time: Dict[Tuple[str, date, str], cp_model.IntVar] = {}

    # x vars for feasible slots; assign each game exactly once
    for g in games:
        feasible = build_feasible_slots_for_game(g, slots, team_avail, blackout_by_date, blackout_by_dt)
        if not feasible:
            raise RuntimeError(f"No feasible slots for game {g.game_id}: {g.t1} vs {g.t2}")
        feasible_slots_for_game[g.game_id] = feasible

        for s_id in feasible:
            var = model.NewBoolVar(f"x_g{g.game_id}_s{s_id}")
            x[(g.game_id, s_id)] = var
            games_for_slot[s_id].append(var)

        model.Add(sum(x[(g.game_id, s_id)] for s_id in feasible) == 1)
        home_is_t1[g.game_id] = model.NewBoolVar(f"home_is_t1_g{g.game_id}")

    # slot occupancy <= 1
    for s in slots:
        if games_for_slot[s.slot_id]:
            model.Add(sum(games_for_slot[s.slot_id]) <= 1)

    # team_time: team plays at (date,time) regardless of field -> forces <=1
    # NOTE: this is O(teams * datetimes * games). Works for typical league sizes; can optimize later.
    for team in all_teams:
        for (d, t) in slots_by_datetime.keys():
            involved = []
            for g in games:
                if g.t1 != team and g.t2 != team:
                    continue
                for s_id in slots_by_datetime[(d, t)]:
                    if (g.game_id, s_id) in x:
                        involved.append(x[(g.game_id, s_id)])
            if not involved:
                continue
            tt = model.NewBoolVar(f"teamtime_{team}_{d}_{t}".replace(" ", ""))
            team_time[(team, d, t)] = tt
            model.Add(sum(involved) == tt)

    # MIN_DAYS_BETWEEN (except same-day)
    if MIN_DAYS_BETWEEN > 0:
        for team in all_teams:
            keys = [(d, t) for (tm, d, t) in team_time.keys() if tm == team]
            keys.sort(key=lambda k: (k[0].toordinal(), parse_time_key(k[1])))
            for i in range(len(keys)):
                d1, t1 = keys[i]
                for j in range(i + 1, len(keys)):
                    d2, t2 = keys[j]
                    if d1 == d2:
                        continue
                    day_diff = abs((d2 - d1).days)
                    if day_diff < MIN_DAYS_BETWEEN:
                        model.Add(team_time[(team, d1, t1)] + team_time[(team, d2, t2)] <= 1)
                    else:
                        break

    # DH session vars (adjacent times on same date)
    dh_session: Dict[Tuple[str, date, str], cp_model.IntVar] = {}
    for team in all_teams:
        for (d, t1, t2) in adjacency_pairs:
            if (team, d, t1) not in team_time or (team, d, t2) not in team_time:
                continue
            a = team_time[(team, d, t1)]
            b = team_time[(team, d, t2)]
            dh = model.NewBoolVar(f"dh_{team}_{d}_{t1}".replace(" ", ""))
            dh_session[(team, d, t1)] = dh
            model.Add(dh <= a)
            model.Add(dh <= b)
            model.Add(dh >= a + b - 1)

    # at most 2 games/day; if 2 then must be adjacent (i.e., have a dh_session that day)
    for team in all_teams:
        for d, tlist in times_by_date.items():
            day_vars = [team_time[(team, d, t)] for t in tlist if (team, d, t) in team_time]
            if not day_vars:
                continue
            model.Add(sum(day_vars) <= 2)
            dh_vars = [dh_session[(team, d, t)] for t in tlist if (team, d, t) in dh_session]
            if dh_vars:
                model.Add(sum(dh_vars) >= sum(day_vars) - 1)
            else:
                model.Add(sum(day_vars) <= 1)

    # prevent same opponent twice same date (ensures DH opponent differs)
    games_between: Dict[Tuple[str, str], List[int]] = defaultdict(list)
    for g in games:
        u, v = sorted((g.t1, g.t2))
        games_between[(u, v)].append(g.game_id)

    slots_by_date: Dict[date, List[int]] = defaultdict(list)
    for s in slots:
        slots_by_date[s.d].append(s.slot_id)

    for (u, v), g_ids in games_between.items():
        for d, s_ids in slots_by_date.items():
            vars_on_d = []
            for gid in g_ids:
                for sid in s_ids:
                    if (gid, sid) in x:
                        vars_on_d.append(x[(gid, sid)])
            if vars_on_d:
                model.Add(sum(vars_on_d) <= 1)

    # DH min/max sessions per team
    for team in all_teams:
        div = team_division(team)
        mn = DOUBLE_HEADERS_BY_DIV[div]["min_sessions"]
        mx = DOUBLE_HEADERS_BY_DIV[div]["max_sessions"]
        team_dh_vars = [v for (tm, _, _), v in dh_session.items() if tm == team]
        if team_dh_vars:
            model.Add(sum(team_dh_vars) >= mn)
            model.Add(sum(team_dh_vars) <= mx)
        else:
            if mn > 0:
                raise RuntimeError(
                    f"Team {team} requires doubleheaders but no adjacent timeslots exist in field availability."
                )

    # Home/away objective variables
    home_games = {t: [] for t in all_teams}
    for g in games:
        h = home_is_t1[g.game_id]
        home_games[g.t1].append(h)
        inv = model.NewIntVar(0, 1, f"home_is_t2_g{g.game_id}")
        model.Add(inv + h == 1)
        home_games[g.t2].append(inv)

    objective_terms = []

    # Home/away deviation
    for t in all_teams:
        hg = model.NewIntVar(0, GAMES_PER_TEAM, f"home_count_{t}")
        model.Add(hg == sum(home_games[t]))
        dev = model.NewIntVar(0, GAMES_PER_TEAM, f"home_dev_{t}")
        model.AddAbsEquality(dev, hg - HOME_GAMES_TARGET)
        objective_terms.append(PENALTIES["home_away_deviation"] * dev)

    # Sunday preference: penalize non-Sunday
    for (gid, sid), var in x.items():
        if not slots[sid].is_sunday:
            objective_terms.append(PENALTIES["non_sunday_slot"] * var)

    model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 120.0
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = RANDOM_SEED

    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"No feasible schedule found. Solver status: {solver.StatusName(status)}")

    game_to_slot: Dict[int, int] = {}
    home_flag: Dict[int, int] = {}

    for g in games:
        feasible = feasible_slots_for_game[g.game_id]
        chosen = None
        for sid in feasible:
            if solver.Value(x[(g.game_id, sid)]) == 1:
                chosen = sid
                break
        if chosen is None:
            raise RuntimeError(f"Internal error: game {g.game_id} not assigned")
        game_to_slot[g.game_id] = chosen
        home_flag[g.game_id] = solver.Value(home_is_t1[g.game_id])

    return game_to_slot, home_flag


# ============================================================
# OUTPUT + VALIDATION + DEBUG
# ============================================================

def build_schedule_rows(games, slots, game_to_slot, home_flag):
    rows = []
    for g in games:
        s = slots[game_to_slot[g.game_id]]
        if home_flag[g.game_id] == 1:
            home, away = g.t1, g.t2
        else:
            home, away = g.t2, g.t1
        rows.append({
            "date": s.date_str,
            "date_obj": s.d,
            "time": s.time_str,
            "time_key": parse_time_key(s.time_str),
            "field_id": s.field_id,
            "home_team": home,
            "away_team": away,
            "home_div": team_division(home),
            "away_div": team_division(away),
            "kind": g.kind,
            "is_sunday": s.is_sunday,
        })
    rows.sort(key=lambda r: (r["date_obj"].toordinal(), r["time_key"], r["field_id"]))
    return rows


def compute_team_stats(schedule_rows):
    team_stats = defaultdict(lambda: {"total_games": 0, "home_games": 0, "away_games": 0, "sunday_games": 0})
    for r in schedule_rows:
        h = r["home_team"]
        a = r["away_team"]
        team_stats[h]["total_games"] += 1
        team_stats[h]["home_games"] += 1
        team_stats[a]["total_games"] += 1
        team_stats[a]["away_games"] += 1
        if r["is_sunday"]:
            team_stats[h]["sunday_games"] += 1
            team_stats[a]["sunday_games"] += 1
    return team_stats


def compute_doubleheader_days(schedule_rows):
    team_date_games = defaultdict(int)  # (team, date_str) -> count
    for r in schedule_rows:
        d = r["date"]
        team_date_games[(r["home_team"], d)] += 1
        team_date_games[(r["away_team"], d)] += 1
    dh_days = Counter()
    for (team, _d), c in team_date_games.items():
        if c >= 2:
            dh_days[team] += 1
    return dh_days


def compute_dh_sessions_adjacent(schedule_rows):
    """
    Adjacency-based DH sessions per team (date -> consecutive times)
    Returns:
      sessions_per_team (Counter)
      sessions_map[(team, date_str)] = list of (t1,t2)
    """
    # team->date->list of time_keys
    team_date_times = defaultdict(lambda: defaultdict(list))
    date_all_times = defaultdict(set)
    for r in schedule_rows:
        date_all_times[r["date_obj"]].add(r["time"])
        team_date_times[r["home_team"]][r["date_obj"]].append(r["time"])
        team_date_times[r["away_team"]][r["date_obj"]].append(r["time"])

    sessions_map = defaultdict(list)
    sessions_per_team = Counter()

    for team, dmap in team_date_times.items():
        for d, times in dmap.items():
            uniq = sorted(set(times), key=parse_time_key)
            if len(uniq) != 2:
                continue
            all_times = sorted(date_all_times[d], key=parse_time_key)
            t1, t2 = uniq
            i1 = all_times.index(t1)
            i2 = all_times.index(t2)
            if abs(i2 - i1) == 1:
                sessions_map[(team, d.strftime("%Y-%m-%d"))].append((min(t1, t2, key=parse_time_key), max(t1, t2, key=parse_time_key)))
                sessions_per_team[team] += 1

    return sessions_per_team, sessions_map


def validate_solution(
    schedule_rows,
    team_avail: Dict[str, Set[str]],
    blackout_by_date: Dict[str, Set[date]],
    blackout_by_dt: Dict[str, Set[Tuple[date, str]]],
) -> None:
    # Slot occupancy
    used_slots = Counter((r["date"], r["time"], r["field_id"]) for r in schedule_rows)
    for k, c in used_slots.items():
        if c > 1:
            raise ValueError(f"Slot used by {c} games: {k}")

    # Team not double-booked same datetime
    team_dt = Counter()
    for r in schedule_rows:
        team_dt[(r["home_team"], r["date"], r["time"])] += 1
        team_dt[(r["away_team"], r["date"], r["time"])] += 1
    for k, c in team_dt.items():
        if c > 1:
            raise ValueError(f"Team double-booked at date+time: {k} count={c}")

    # Availability & blackouts
    # Need dow: derive from date_obj
    for r in schedule_rows:
        d_obj = r["date_obj"]
        dow = DAY_ABBR[d_obj.weekday()]
        for team in (r["home_team"], r["away_team"]):
            if dow not in team_avail.get(team, set(DAY_ABBR)):
                raise ValueError(f"Team {team} scheduled on unavailable day {dow} at {r['date']} {r['time']}")
            if d_obj in blackout_by_date.get(team, set()):
                raise ValueError(f"Team {team} scheduled on blackout date {r['date']}")
            if (d_obj, r["time"]) in blackout_by_dt.get(team, set()):
                raise ValueError(f"Team {team} scheduled on blackout datetime {r['date']} {r['time']}")

    # Games per team
    played = Counter()
    for r in schedule_rows:
        played[r["home_team"]] += 1
        played[r["away_team"]] += 1
    for t in all_teams_list():
        if played[t] != GAMES_PER_TEAM:
            raise ValueError(f"Team {t} has {played[t]} games (expected {GAMES_PER_TEAM})")

    # MIN_DAYS_BETWEEN (except same-day)
    if MIN_DAYS_BETWEEN > 0:
        team_dates = defaultdict(list)
        for r in schedule_rows:
            team_dates[r["home_team"]].append(r["date_obj"])
            team_dates[r["away_team"]].append(r["date_obj"])
        for t, ds in team_dates.items():
            ds_sorted = sorted(ds)
            for i in range(len(ds_sorted)):
                for j in range(i + 1, len(ds_sorted)):
                    if ds_sorted[i] == ds_sorted[j]:
                        continue
                    if abs((ds_sorted[j] - ds_sorted[i]).days) < MIN_DAYS_BETWEEN:
                        raise ValueError(f"Team {t} violates MIN_DAYS_BETWEEN between {ds_sorted[i]} and {ds_sorted[j]}")


def write_schedule_csv(schedule_rows, out_path: str) -> None:
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "time", "field_id", "home_team", "away_team", "home_div", "away_div", "is_doubleheader", "dh_session_id"])

        # compute dh session ids (adjacent)
        sessions_per_team, sessions_map = compute_dh_sessions_adjacent(schedule_rows)
        # give stable ids by (team, date, first_time)
        session_id_by_key = {}
        sid_counter = 1
        for (team, d), pairs in sorted(sessions_map.items(), key=lambda x: (x[0][0], x[0][1])):
            for (t1, t2) in pairs:
                key = (team, d, t1)
                session_id_by_key[key] = f"DH{sid_counter:03d}_{team}_{d.replace('-', '')}"
                sid_counter += 1

        # Mark a game as DH if either home or away team has a DH session that date/time.
        for r in schedule_rows:
            d = r["date"]
            t = r["time"]
            dh_id = ""
            is_dh = "N"
            # if team has session (team,d,t1) and t is in (t1,t2) then tag
            for team in (r["home_team"], r["away_team"]):
                for (t1, t2) in sessions_map.get((team, d), []):
                    if t in (t1, t2):
                        dh_id = session_id_by_key.get((team, d, t1), "")
                        is_dh = "Y"
                        break
                if is_dh == "Y":
                    break

            w.writerow([r["date"], r["time"], r["field_id"], r["home_team"], r["away_team"], r["home_div"], r["away_div"], is_dh, dh_id])


# -------------------------------
# Debug table outputs (old-style parity)
# -------------------------------

def print_schedule_summary(team_stats):
    PrettyTable = _try_prettytable()
    if PrettyTable:
        table = PrettyTable()
        table.field_names = ["Division", "Team", "Total Games", "Home Games", "Away Games", "Sun Games"]
        for team in sorted(team_stats.keys()):
            s = team_stats[team]
            table.add_row([team_division(team), team, s["total_games"], s["home_games"], s["away_games"], s["sunday_games"]])
        print("\nSchedule Summary:")
        print(table)
    else:
        print("\nSchedule Summary:")
        print("Div Team Total Home Away Sun")
        for team in sorted(team_stats.keys()):
            s = team_stats[team]
            print(team_division(team), team, s["total_games"], s["home_games"], s["away_games"], s["sunday_games"])


def print_doubleheader_summary(doubleheader_days):
    PrettyTable = _try_prettytable()
    if PrettyTable:
        table = PrettyTable()
        table.field_names = ["Team", "Doubleheader Days (2 games)"]
        for team in sorted(doubleheader_days.keys()):
            table.add_row([team, int(doubleheader_days[team])])
        print("\nDoubleheader Summary (Days with 2 games):")
        print(table)
    else:
        print("\nDoubleheader Summary (Days with 2 games):")
        for team in sorted(doubleheader_days.keys()):
            print(team, int(doubleheader_days[team]))


def generate_matchup_table(schedule_rows, division_teams):
    matchup_count = defaultdict(lambda: defaultdict(int))
    for r in schedule_rows:
        h = r["home_team"]
        a = r["away_team"]
        matchup_count[h][a] += 1
        matchup_count[a][h] += 1

    all_teams = sorted([t for teams in division_teams.values() for t in teams])

    PrettyTable = _try_prettytable()
    if PrettyTable:
        table = PrettyTable()
        table.field_names = ["Team"] + all_teams
        for team in all_teams:
            row = [team] + [matchup_count[team][opp] for opp in all_teams]
            table.add_row(row)
        print("\nMatchup Table:")
        print(table)
    else:
        print("\nMatchup Table (CSV-ish):")
        print(",".join(["Team"] + all_teams))
        for team in all_teams:
            row = [team] + [str(matchup_count[team][opp]) for opp in all_teams]
            print(",".join(row))


def print_inter_target_check(schedule_rows, inter_targets_per_team):
    achieved = defaultdict(lambda: Counter())  # team -> Counter(div->count)
    for r in schedule_rows:
        h, a = r["home_team"], r["away_team"]
        hd, ad = r["home_div"], r["away_div"]
        if hd != ad:
            achieved[h][ad] += 1
            achieved[a][hd] += 1

    print("\nInter Target Check (achieved vs target):")
    any_mismatch = False
    for team in all_teams_list():
        targets = inter_targets_per_team.get(team, {})
        div_keys = sorted(set(list(targets.keys()) + list(achieved[team].keys())))
        for od in div_keys:
            t = targets.get(od, 0)
            a = achieved[team][od]
            if a != t:
                any_mismatch = True
                print("  {} vs {}: achieved {} target {}".format(team, od, a, t))
    if not any_mismatch:
        print("  All teams match inter targets exactly.")


# -------------------------------
# TOP VIOLATIONS / WORST OFFENDERS
# -------------------------------

def compute_repeat_counts(schedule_rows):
    """Return matchup counts (undirected) and intra-only repeat counts."""
    pair_counts = Counter()
    intra_pair_counts = Counter()
    for r in schedule_rows:
        u, v = sorted((r["home_team"], r["away_team"]))
        pair_counts[(u, v)] += 1
        if r["home_div"] == r["away_div"]:
            intra_pair_counts[(u, v)] += 1
    return pair_counts, intra_pair_counts


def compute_inter_achieved(schedule_rows):
    achieved = defaultdict(lambda: Counter())  # team -> Counter(div->count)
    for r in schedule_rows:
        h, a = r["home_team"], r["away_team"]
        hd, ad = r["home_div"], r["away_div"]
        if hd != ad:
            achieved[h][ad] += 1
            achieved[a][hd] += 1
    return achieved


def print_top_violations(schedule_rows, team_stats, dh_sessions_per_team, inter_targets_per_team):
    print("\n=== TOP VIOLATIONS / WORST OFFENDERS ===")

    # 1) Home/Away deviation
    devs = []
    for team, s in team_stats.items():
        dev = abs(s["home_games"] - HOME_GAMES_TARGET)
        devs.append((dev, team, s["home_games"], s["away_games"]))
    devs.sort(reverse=True)
    print("\n1) Home/Away deviation (abs(home - target)):")
    for dev, team, hg, ag in devs[:TOP_VIOLATIONS_N]:
        if dev == 0:
            break
        print(f"  {team}: dev={dev} (home={hg}, away={ag})")

    # 2) Non-Sunday usage (lower Sunday count is "worse")
    # We'll rank by non-Sunday games desc (i.e., total - sunday)
    ns = []
    for team, s in team_stats.items():
        non_sun = s["total_games"] - s["sunday_games"]
        ns.append((non_sun, team, s["sunday_games"]))
    ns.sort(reverse=True)
    print("\n2) Non-Sunday games (higher is worse):")
    for non_sun, team, sun in ns[:TOP_VIOLATIONS_N]:
        print(f"  {team}: non_sunday={non_sun} (sunday={sun})")

    # 3) Doubleheader sessions outside bounds
    dh_viol = []
    for team in all_teams_list():
        div = team_division(team)
        mn = DOUBLE_HEADERS_BY_DIV[div]["min_sessions"]
        mx = DOUBLE_HEADERS_BY_DIV[div]["max_sessions"]
        got = int(dh_sessions_per_team.get(team, 0))
        if got < mn or got > mx:
            dh_viol.append((abs(got - (mn if got < mn else mx)), team, got, mn, mx))
    dh_viol.sort(reverse=True)
    print("\n3) Doubleheader sessions outside bounds (should be NONE):")
    if not dh_viol:
        print("  None (all teams within min/max).")
    else:
        for diff, team, got, mn, mx in dh_viol[:TOP_VIOLATIONS_N]:
            print(f"  {team}: sessions={got}, required=[{mn}..{mx}]")

    # 4) Inter target mismatches (should be NONE if demand generation correct)
    achieved = compute_inter_achieved(schedule_rows)
    mism = []
    for team in all_teams_list():
        targets = inter_targets_per_team.get(team, {})
        keys = set(targets.keys()) | set(achieved[team].keys())
        for od in keys:
            t = targets.get(od, 0)
            a = achieved[team][od]
            if a != t:
                mism.append((abs(a - t), team, od, a, t))
    mism.sort(reverse=True)
    print("\n4) Inter target mismatches (should be NONE):")
    if not mism:
        print("  None (all teams match inter targets).")
    else:
        for diff, team, od, a, t in mism[:TOP_VIOLATIONS_N]:
            print(f"  {team} vs {od}: achieved={a}, target={t} (diff={diff})")

    # 5) Intra matchup concentration (teams repeatedly playing same opponent a lot)
    pair_counts, intra_pair_counts = compute_repeat_counts(schedule_rows)
    intra_repeats = []
    for (u, v), c in intra_pair_counts.items():
        # "repeat pressure" = games between pair - 2 (since 2 is a common baseline; tweak as needed)
        # We'll just show biggest c.
        intra_repeats.append((c, u, v))
    intra_repeats.sort(reverse=True)
    print("\n5) Highest repeated INTRA matchups (pair counts):")
    for c, u, v in intra_repeats[:TOP_VIOLATIONS_N]:
        print(f"  {u} vs {v}: {c} times")

    # 6) Overall repeated matchups (includes inter; inter repeats should be 1 max if cap=1)
    repeats = []
    for (u, v), c in pair_counts.items():
        if c > 1:
            repeats.append((c, u, v))
    repeats.sort(reverse=True)
    print("\n6) Overall repeated matchups > 1 (inter repeats should be rare/none with cap=1):")
    if not repeats:
        print("  None.")
    else:
        for c, u, v in repeats[:TOP_VIOLATIONS_N]:
            print(f"  {u} vs {v}: {c} times")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    rnd = random.Random(RANDOM_SEED)

    slots = load_field_availability(FIELD_AVAILABILITY_CSV)
    team_avail = load_team_availability(TEAM_AVAILABILITY_CSV)
    blackout_by_date, blackout_by_dt = load_blackouts(BLACKOUTS_CSV)

    games = generate_all_games(rnd)

    game_to_slot, home_flag = solve_schedule(games, slots, team_avail, blackout_by_date, blackout_by_dt)

    schedule_rows = build_schedule_rows(games, slots, game_to_slot, home_flag)

    # Validate hard constraints (raise if anything is broken)
    validate_solution(schedule_rows, team_avail, blackout_by_date, blackout_by_dt)

    # Write output CSV
    write_schedule_csv(schedule_rows, OUTPUT_SCHEDULE_CSV)
    print(f"\nWrote: {OUTPUT_SCHEDULE_CSV}")

    if DEBUG:
        team_stats = compute_team_stats(schedule_rows)
        print_schedule_summary(team_stats)

        dh_sessions_per_team, _dh_map = compute_dh_sessions_adjacent(schedule_rows)
        # Also show "days with 2 games" (old-style)
        dh_days = compute_doubleheader_days(schedule_rows)
        print_doubleheader_summary(dh_days)

        if DEBUG_INTER_TARGETS:
            inter_targets = derive_inter_targets_per_team()
            print_inter_target_check(schedule_rows, inter_targets)

        # Top violations / worst offenders
        inter_targets = derive_inter_targets_per_team()
        print_top_violations(schedule_rows, team_stats, dh_sessions_per_team, inter_targets)

        if DEBUG_MATCHUP_TABLE:
            generate_matchup_table(schedule_rows, DIVISION_TEAMS)


if __name__ == "__main__":
    main()
