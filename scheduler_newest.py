#!/usr/bin/env python3
"""
Softball scheduler (heuristic) — fixes for:
  - used_slots key normalization (prevents hidden double-booking)
  - looser same-day adjacency rule (allows 2nd game in next OR next-next slot)
  - fill pass fallback pair generation (can complete seasons even when predetermined matchups run out)
  - small cleanup: remove duplicate A-check, safer breaks in DH pre-schedule
"""

import csv
import itertools
import random
from datetime import datetime, date as date_cls
from collections import defaultdict
import math

try:
    from prettytable import PrettyTable
except ImportError:
    PrettyTable = None

# -------------------------------
# Configurable parameters
# -------------------------------
MAX_RETRIES = 20000            # scheduling backtracking limit
MIN_GAP = 5                    # minimum days between game dates
WEEKLY_GAME_LIMIT = 2          # max games per team per week
HOME_AWAY_BALANCE = 11         # desired home games per team (for 22-game seasons)

# Allow 2nd game of a DH in next slot, or (optionally) the slot after that.
ALLOW_SKIP_ONE_SLOT_FOR_DH = True  # allows idx+2 as alternative to idx+1

# Per-division configuration (tweak here)
DIVISION_SETTINGS = {
    # A: 22 games, only DH => 11 DH days exactly
    'A': {'inter': False, 'target_games': 22, 'min_dh': 11, 'max_dh': 11},

    # B/C/D: inter allowed, intra can top up as needed
    'B': {'inter': True,  'target_games': 22, 'min_dh': 6,  'max_dh': 7},
    'C': {'inter': True,  'target_games': 22, 'min_dh': 6,  'max_dh': 7},
    'D': {'inter': True,  'target_games': 22, 'min_dh': 6,  'max_dh': 7},
}

# Inter-division pairing settings (only applied if BOTH divisions have inter=True)
INTER_PAIR_SETTINGS = {
    # A plays no inter
    ('A', 'B'): False,
    ('A', 'C'): False,
    ('A', 'D'): False,

    # Only allowed inter
    ('B', 'C'): True,
    ('C', 'D'): True,

    # Explicitly not allowed
    ('B', 'D'): False,
}

# How many inter games each team in the pair should have against the other division.
INTER_DEGREE = {
    ('B', 'C'): 4,
    ('C', 'D'): 6,
}

# -------------------------------
# Helpers
# -------------------------------
def div_of(team: str) -> str:
    return team[0].upper()

def target_games(team: str) -> int:
    return DIVISION_SETTINGS[div_of(team)]['target_games']

def min_dh(team: str) -> int:
    return DIVISION_SETTINGS[div_of(team)]['min_dh']

def max_dh(team: str) -> int:
    return DIVISION_SETTINGS[div_of(team)]['max_dh']

# Division priority for fairness (higher = schedule earlier when all else equal)
DIV_PRIORITY = {'D': 3, 'C': 2, 'B': 1, 'A': 0}

def game_deficit(team: str, team_stats) -> int:
    return max(0, target_games(team) - team_stats[team]['total_games'])

def dh_deficit(team: str, doubleheader_count) -> int:
    return max(0, min_dh(team) - doubleheader_count[team])

def team_need_key(team: str, team_stats, doubleheader_count):
    """Sort key: teams with fewer games and fewer DH days go first (descending priority)."""
    return (
        dh_deficit(team, doubleheader_count),
        game_deficit(team, team_stats),
        DIV_PRIORITY.get(div_of(team), 0),
        -team_stats[team]['home_games'],  # slight preference to balance home/away later
        team
    )

def matchup_need_score(home: str, away: str, team_stats, doubleheader_count) -> int:
    """Higher score = more urgent to schedule."""
    return (
        game_deficit(home, team_stats) + game_deficit(away, team_stats)
    ) * 1000 + (
        dh_deficit(home, doubleheader_count) + dh_deficit(away, doubleheader_count)
    ) * 50 + (
        DIV_PRIORITY.get(div_of(home), 0) + DIV_PRIORITY.get(div_of(away), 0)
    )

def inter_enabled_for_pair(d1: str, d2: str) -> bool:
    d1, d2 = d1.upper(), d2.upper()
    key = (d1, d2) if (d1, d2) in INTER_PAIR_SETTINGS else (d2, d1)
    if key not in INTER_PAIR_SETTINGS or not INTER_PAIR_SETTINGS[key]:
        return False
    return DIVISION_SETTINGS[d1]['inter'] and DIVISION_SETTINGS[d2]['inter']

def pair_degree(d1: str, d2: str) -> int:
    d1, d2 = d1.upper(), d2.upper()
    key = (d1, d2) if (d1, d2) in INTER_DEGREE else (d2, d1)
    return INTER_DEGREE.get(key, 0)

def min_gap_ok(team, d: date_cls, team_game_days):
    """Return True if 'team' has no game scheduled within MIN_GAP days of date d."""
    for gd in team_game_days[team]:
        if gd != d and abs((d - gd).days) < MIN_GAP:
            return False
    return True

# -------------------------------
# Slot normalization (CRITICAL FIX)
# -------------------------------
def slot_key(d_or_dt, slot_str: str, field: str):
    """Canonical slot identity: (date, time_str, field)."""
    d = d_or_dt.date() if hasattr(d_or_dt, "date") else d_or_dt
    return (d, slot_str.strip(), field)

def is_used(used_slots: dict, d_or_dt, slot_str: str, field: str) -> bool:
    return used_slots.get(slot_key(d_or_dt, slot_str, field), False)

def mark_used(used_slots: dict, d_or_dt, slot_str: str, field: str) -> None:
    used_slots[slot_key(d_or_dt, slot_str, field)] = True

# -------------------------------
# Same-day DH adjacency helper (loosened)
# -------------------------------
def allowed_second_game_slots(d: date_cls, current_slot: str, timeslots_by_date) -> set[str]:
    """Given the first slot, return allowed slots for a 2nd game on the same day."""
    slots = timeslots_by_date.get(d, [])
    try:
        idx = slots.index(current_slot)
    except ValueError:
        return set()
    allowed = set()
    if idx + 1 < len(slots):
        allowed.add(slots[idx + 1])
    if ALLOW_SKIP_ONE_SLOT_FOR_DH and idx + 2 < len(slots):
        allowed.add(slots[idx + 2])
    return allowed

def slot_is_valid_for_team_same_day(team: str, d: date_cls, slot: str, team_game_slots, timeslots_by_date) -> bool:
    """If team already has 1 game on day d, enforce allowed 2nd-slot rule."""
    if not team_game_slots[team][d]:
        return True
    current = team_game_slots[team][d][0]
    return slot in allowed_second_game_slots(d, current, timeslots_by_date)

# -------------------------------
# Data loading functions
# -------------------------------
def load_team_availability(file_path):
    availability = {}
    with open(file_path, mode='r', newline='') as file:
        reader = csv.reader(file)
        next(reader)  # header
        for row in reader:
            team = row[0].strip()
            days = row[1:]
            availability[team] = {day.strip() for day in days if day and day.strip()}
    return availability

def load_field_availability(file_path):
    field_availability = []
    with open(file_path, mode='r', newline='') as file:
        reader = csv.reader(file)
        next(reader)  # header
        for row in reader:
            dt = datetime.strptime(row[0].strip(), '%Y-%m-%d')  # midnight
            slot = row[1].strip()
            field = row[2].strip()
            field_availability.append((dt, slot, field))

    # Custom sort: Prioritize Sundays then by date then by time.
    field_availability.sort(key=lambda x: (
        (0 if x[0].weekday() == 6 else 1),
        x[0],
        datetime.strptime(x[1].strip(), "%I:%M %p")
    ))
    return field_availability

def load_team_blackouts(file_path):
    """
    CSV format: Team, Date1, Date2, ...
    Dates: YYYY-MM-DD
    Returns: dict[team] -> set(date)
    """
    blackouts = {}
    with open(file_path, mode='r', newline='') as file:
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
                    print(f"Error parsing blackout date '{d}' for team {team}: {e}")
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
        raise Exception(f"intra_target_per_team must be >= 0 (got {intra_target_per_team}) for division {division}.")
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
            raise Exception(f"No valid intra-division assignment found for {division} (18 target).")

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
            f"Intra target {intra_target_per_team} with n={n} yields odd total participation ({total_slots}); "
            f"cannot form whole games for division {division}."
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

    avg_meet = intra_target_per_team / max(1, (n - 1))
    soft_cap = int(math.ceil(avg_meet)) + 1

    guard = 0
    guard_max = 200000
    while any(v > 0 for v in games_left.values()):
        guard += 1
        if guard > guard_max:
            raise Exception(f"Failed building intra matchups for {division}; stuck with remaining={games_left}.")

        t1 = max(teams, key=lambda t: games_left[t])
        if games_left[t1] <= 0:
            break

        candidates = [t for t in teams if t != t1 and games_left[t] > 0]
        if not candidates:
            raise Exception(f"Cannot find opponent to satisfy intra target for {division}. Remaining={games_left}")

        def meet_key(t2):
            return (meet[frozenset((t1, t2))], -games_left[t2], t2)

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
            f"degree={degree} exceeds opponent count={len(teams2)}; "
            "reduce degree or implement repeat-opponent inter matchups."
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
        full_matchups.extend(
            generate_inter_division_matchups(d1, d2, division_teams[d1], division_teams[d2], deg)
        )

    random.shuffle(full_matchups)
    return full_matchups

# -------------------------------
# Home/Away Helper
# -------------------------------
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

# -------------------------------
# Preemptive Doubleheader Scheduling
# -------------------------------
def schedule_doubleheaders_preemptively(all_teams, unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
                                        team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents,
                                        used_slots, schedule=None):
    if schedule is None:
        schedule = []

    for d in sorted(timeslots_by_date.keys()):
        day_of_week = d.strftime('%a')
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
            if day_of_week not in team_availability.get(team, set()):
                continue
            if d in team_blackouts.get(team, set()):
                continue

            games_today = team_game_days[team].get(d, 0)

            # Case 1: no games yet today -> try schedule 2 games (two different opponents)
            if games_today == 0:
                if len(slots) < 2:
                    continue

                dh_scheduled = False
                for i in range(len(slots) - 1):
                    slot1 = slots[i]
                    slot2 = slots[i + 1]

                    free1 = [entry for entry in field_availability
                             if entry[0].date() == d and entry[1] == slot1 and (not is_used(used_slots, entry[0], slot1, entry[2]))]
                    free2 = [entry for entry in field_availability
                             if entry[0].date() == d and entry[1] == slot2 and (not is_used(used_slots, entry[0], slot2, entry[2]))]
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

                        if day_of_week not in team_availability.get(opp1, set()) or d in team_blackouts.get(opp1, set()):
                            continue
                        if day_of_week not in team_availability.get(opp2, set()) or d in team_blackouts.get(opp2, set()):
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

                        mark_used(used_slots, date1, slot1_str, field1)
                        mark_used(used_slots, date2, slot2_str, field2)

                        dh_scheduled = True
                        break

                    if dh_scheduled:
                        break

            # Case 2: already 1 game today -> try add allowed 2nd slot (next or next-next)
            elif games_today == 1:
                current_slot = team_game_slots[team][d][0]
                allowed = allowed_second_game_slots(d, current_slot, timeslots_by_date)
                if not allowed:
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
                    if day_of_week not in team_availability.get(opp, set()) or d in team_blackouts.get(opp, set()):
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

                    placed = False
                    for next_slot in sorted(allowed, key=lambda s: datetime.strptime(s, "%I:%M %p")):
                        free_next = [entry for entry in field_availability
                                     if entry[0].date() == d and entry[1] == next_slot and (not is_used(used_slots, entry[0], next_slot, entry[2]))]
                        if not free_next:
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
                        mark_used(used_slots, date_entry, slot_str, field)
                        placed = True
                        break

                    if placed:
                        break

    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, unscheduled

# -------------------------------
# Dedicated Doubleheader pass (Two-phase)
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

        for d in sorted(timeslots_by_date.keys()):
            day_of_week = d.strftime('%a')
            if d in team_blackouts.get(team, set()) or day_of_week not in team_availability.get(team, set()):
                continue
            week_num = d.isocalendar()[1]
            games_today = team_game_days[team].get(d, 0)

            if games_today != 1:
                continue

            current_slot = team_game_slots[team][d][0]
            allowed = allowed_second_game_slots(d, current_slot, timeslots_by_date)
            if not allowed:
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
            scheduled = False
            for m in candidate:
                opp = m[0] if m[1] == team else m[1]
                if opp == already_opp:
                    continue
                if day_of_week not in team_availability.get(opp, set()) or d in team_blackouts.get(opp, set()):
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

                for next_slot in sorted(allowed, key=lambda s: datetime.strptime(s, "%I:%M %p")):
                    free_fields = [entry for entry in field_availability
                                   if entry[0].date() == d and entry[1] == next_slot and (not is_used(used_slots, entry[0], next_slot, entry[2]))]
                    if not free_fields:
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
                    mark_used(used_slots, date_entry, slot_str, field)
                    scheduled = True
                    break

                if scheduled:
                    break

            if scheduled:
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
            for d in sorted(timeslots_by_date.keys()):
                day_of_week = d.strftime('%a')
                if d in team_blackouts.get(team, set()) or day_of_week not in team_availability.get(team, set()):
                    continue
                week_num = d.isocalendar()[1]
                games_today = team_game_days[team].get(d, 0)

                if games_today != 1:
                    continue

                current_slot = team_game_slots[team][d][0]
                allowed = allowed_second_game_slots(d, current_slot, timeslots_by_date)
                if not allowed:
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
                    if day_of_week not in team_availability.get(opp, set()) or d in team_blackouts.get(opp, set()):
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

                    placed = False
                    for next_slot in sorted(allowed, key=lambda s: datetime.strptime(s, "%I:%M %p")):
                        free_fields = [entry for entry in field_availability
                                       if entry[0].date() == d and entry[1] == next_slot and (not is_used(used_slots, entry[0], next_slot, entry[2]))]
                        if not free_fields:
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
                        mark_used(used_slots, date_entry, slot_str, field)
                        scheduled = True
                        placed = True
                        break

                    if placed:
                        break

                if scheduled:
                    break

            if not scheduled:
                break

    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, unscheduled

# -------------------------------
# A Division DH-only scheduling (pair doubleheaders)
# -------------------------------
def schedule_A_pair_doubleheaders(division_teams, team_availability, field_availability, team_blackouts,
                                  timeslots_by_date, team_stats, doubleheader_count,
                                  team_game_days, team_game_slots, used_slots, schedule=None):
    if schedule is None:
        schedule = []
    if not isinstance(schedule, list):
        raise TypeError(f"schedule must be list[game_tuple], got {type(schedule)}")

    A_teams = list(division_teams.get('A', []))
    if not A_teams:
        return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, used_slots

    target_sessions = DIVISION_SETTINGS['A']['target_games'] // 2  # 11

    sessions_done = defaultdict(int)
    pair_sessions = defaultdict(int)

    adjacent_pairs = []
    for d, slots in timeslots_by_date.items():
        for i in range(len(slots) - 1):
            adjacent_pairs.append((d, slots[i], slots[i + 1]))
    adjacent_pairs.sort(key=lambda x: (x[0], datetime.strptime(x[1], "%I:%M %p")))

    def can_play_dh(team: str, d: date_cls):
        dow = d.strftime('%a')
        if dow not in team_availability.get(team, set()):
            return False
        if d in team_blackouts.get(team, set()):
            return False
        if team_game_days[team].get(d, 0) != 0:
            return False
        if not min_gap_ok(team, d, team_game_days):
            return False
        wk = d.isocalendar()[1]
        if team_stats[team]['weekly_games'].get(wk, 0) + 2 > WEEKLY_GAME_LIMIT:
            return False
        if team_stats[team]['total_games'] + 2 > DIVISION_SETTINGS['A']['target_games']:
            return False
        return True

    def choose_pair():
        need = [t for t in A_teams if sessions_done[t] < target_sessions]
        if len(need) < 2:
            return None
        need.sort(key=lambda t: (target_sessions - sessions_done[t], DIVISION_SETTINGS['A']['target_games'] - team_stats[t]['total_games']), reverse=True)
        pool = need[:6]
        best = None
        best_key = None
        for i in range(len(pool)):
            for j in range(i + 1, len(pool)):
                a, b = pool[i], pool[j]
                key = tuple(sorted((a, b)))
                score = (pair_sessions[key], -((target_sessions - sessions_done[a]) + (target_sessions - sessions_done[b])))
                if best_key is None or score < best_key:
                    best_key = score
                    best = (a, b)
        return best

    def place_game(d: date_cls, slot: str, field: str, home: str, away: str):
        dt = datetime.combine(d, datetime.min.time())
        schedule.append((dt, slot, field, home, home[0], away, away[0]))
        mark_used(used_slots, d, slot, field)

        wk = dt.isocalendar()[1]
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

    for _pass in range(8):
        progress = False
        for (d, s1, s2) in adjacent_pairs:
            if all(sessions_done[t] >= target_sessions for t in A_teams):
                break

            pair = choose_pair()
            if not pair:
                continue
            t1, t2 = pair
            if not (can_play_dh(t1, d) and can_play_dh(t2, d)):
                continue

            free1 = [entry for entry in field_availability if entry[0].date() == d and entry[1] == s1 and (not is_used(used_slots, d, s1, entry[2]))]
            free2 = [entry for entry in field_availability if entry[0].date() == d and entry[1] == s2 and (not is_used(used_slots, d, s2, entry[2]))]
            if not free1 or not free2:
                continue

            fields1 = {e[2] for e in free1}
            fields2 = {e[2] for e in free2}
            common = sorted(fields1.intersection(fields2))
            if common:
                field = common[0]
            else:
                field = free1[0][2]
                if not any(e[2] == field for e in free2):
                    field = free2[0][2]

            place_game(d, s1, field, t1, t2)
            place_game(d, s2, field, t2, t1)

            sessions_done[t1] += 1
            sessions_done[t2] += 1
            pair_sessions[tuple(sorted((t1, t2)))] += 1
            doubleheader_count[t1] += 1
            doubleheader_count[t2] += 1
            progress = True

        if not progress:
            break

    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, used_slots

# -------------------------------
# Primary scheduling
# -------------------------------
def schedule_games(matchups, team_availability, field_availability, team_blackouts,
                   schedule, team_stats, doubleheader_count,
                   team_game_days, team_game_slots, team_doubleheader_opponents,
                   used_slots, timeslots_by_date):

    unscheduled = matchups[:]
    retry_count = 0

    while unscheduled and retry_count < MAX_RETRIES:
        progress_made = False

        for date_dt, slot, field in field_availability:
            if is_used(used_slots, date_dt, slot, field):
                continue

            d = date_dt.date()
            day_of_week = date_dt.strftime('%a')
            week_num = date_dt.isocalendar()[1]

            best = None
            best_score = -1

            for (t1, t2) in unscheduled:
                if div_of(t1) == 'A' or div_of(t2) == 'A':
                    continue

                if day_of_week not in team_availability.get(t1, set()) or day_of_week not in team_availability.get(t2, set()):
                    continue
                if d in team_blackouts.get(t1, set()) or d in team_blackouts.get(t2, set()):
                    continue

                if team_stats[t1]['total_games'] >= target_games(t1) or team_stats[t2]['total_games'] >= target_games(t2):
                    continue
                if (team_stats[t1]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or
                    team_stats[t2]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT):
                    continue

                if not (min_gap_ok(t1, d, team_game_days) and min_gap_ok(t2, d, team_game_days)):
                    continue

                if slot in team_game_slots[t1][d] or slot in team_game_slots[t2][d]:
                    continue

                if not (slot_is_valid_for_team_same_day(t1, d, slot, team_game_slots, timeslots_by_date) and
                        slot_is_valid_for_team_same_day(t2, d, slot, team_game_slots, timeslots_by_date)):
                    continue

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

            schedule.append((date_dt, slot, field, home, home[0], away, away[0]))

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

            mark_used(used_slots, d, slot, field)
            unscheduled.remove((t1, t2))
            progress_made = True
            break

        retry_count = 0 if progress_made else retry_count + 1

    if unscheduled:
        print("Warning: Retry limit reached in primary scheduling. Some predetermined matchups could not be scheduled.")
    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, unscheduled

# -------------------------------
# Fill missing games (with fallback pairing generation)
# -------------------------------
def _pair_allowed(t1, t2) -> bool:
    if t1 == t2:
        return False
    d1, d2 = div_of(t1), div_of(t2)
    if d1 == 'A' or d2 == 'A':
        return False
    if d1 == d2:
        return True
    return inter_enabled_for_pair(d1, d2)

def fill_missing_games(schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
                       team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled,
                       team_availability, team_blackouts, field_availability, all_teams):
    retry_count = 0

    def teams_needing_games():
        return [t for t in all_teams if div_of(t) != 'A' and team_stats[t]['total_games'] < target_games(t)]

    def select_fallback_pair(d, slot, day_of_week, week_num):
        need = teams_needing_games()
        if len(need) < 2:
            return None

        need.sort(key=lambda t: (game_deficit(t, team_stats), dh_deficit(t, doubleheader_count), DIV_PRIORITY.get(div_of(t), 0)), reverse=True)
        pool = need[:10]

        best = None
        best_score = -1
        for i in range(len(pool)):
            for j in range(i + 1, len(pool)):
                t1, t2 = pool[i], pool[j]
                if not _pair_allowed(t1, t2):
                    continue

                if day_of_week not in team_availability.get(t1, set()) or day_of_week not in team_availability.get(t2, set()):
                    continue
                if d in team_blackouts.get(t1, set()) or d in team_blackouts.get(t2, set()):
                    continue

                if team_stats[t1]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or team_stats[t2]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT:
                    continue
                if not (min_gap_ok(t1, d, team_game_days) and min_gap_ok(t2, d, team_game_days)):
                    continue
                if slot in team_game_slots[t1][d] or slot in team_game_slots[t2][d]:
                    continue
                if not (slot_is_valid_for_team_same_day(t1, d, slot, team_game_slots, timeslots_by_date) and
                        slot_is_valid_for_team_same_day(t2, d, slot, team_game_slots, timeslots_by_date)):
                    continue

                for team, opp in ((t1, t2), (t2, t1)):
                    if team_game_days[team][d] == 1:
                        if doubleheader_count[team] >= max_dh(team):
                            break
                        if opp in team_doubleheader_opponents[team][d]:
                            break
                else:
                    intra_bonus = 200 if div_of(t1) == div_of(t2) else 0
                    score = intra_bonus + matchup_need_score(t1, t2, team_stats, doubleheader_count)
                    if score > best_score:
                        best_score = score
                        best = (t1, t2)

        return best

    while teams_needing_games() and retry_count < MAX_RETRIES:
        progress = False

        for date_dt, slot, field in field_availability:
            if is_used(used_slots, date_dt, slot, field):
                continue

            d = date_dt.date()
            day_of_week = date_dt.strftime('%a')
            week_num = date_dt.isocalendar()[1]

            best = None
            best_score = -1
            best_from_unscheduled = False

            for (t1, t2) in unscheduled:
                if div_of(t1) == 'A' or div_of(t2) == 'A':
                    continue
                if team_stats[t1]['total_games'] >= target_games(t1) and team_stats[t2]['total_games'] >= target_games(t2):
                    continue
                if day_of_week not in team_availability.get(t1, set()) or day_of_week not in team_availability.get(t2, set()):
                    continue
                if d in team_blackouts.get(t1, set()) or d in team_blackouts.get(t2, set()):
                    continue
                if team_stats[t1]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or team_stats[t2]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT:
                    continue
                if not (min_gap_ok(t1, d, team_game_days) and min_gap_ok(t2, d, team_game_days)):
                    continue
                if slot in team_game_slots[t1][d] or slot in team_game_slots[t2][d]:
                    continue
                if not (slot_is_valid_for_team_same_day(t1, d, slot, team_game_slots, timeslots_by_date) and
                        slot_is_valid_for_team_same_day(t2, d, slot, team_game_slots, timeslots_by_date)):
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
                if score > best_score:
                    best_score = score
                    best = (t1, t2)
                    best_from_unscheduled = True

            if best is None:
                fb = select_fallback_pair(d, slot, day_of_week, week_num)
                if fb is None:
                    continue
                best = fb
                best_from_unscheduled = False

            t1, t2 = best
            home, away = decide_home_away(t1, t2, team_stats)

            if team_stats[home]['home_games'] >= HOME_AWAY_BALANCE:
                if team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
                    home, away = away, home
                else:
                    continue

            schedule.append((date_dt, slot, field, home, home[0], away, away[0]))

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

            mark_used(used_slots, d, slot, field)
            if best_from_unscheduled and (t1, t2) in unscheduled:
                unscheduled.remove((t1, t2))

            progress = True
            break

        retry_count = 0 if progress else retry_count + 1

    return schedule, team_stats, doubleheader_count, unscheduled

# -------------------------------
# Output / Reporting
# -------------------------------
def output_schedule_to_csv(schedule, output_file):
    sorted_schedule = sorted(schedule, key=lambda game: (
        game[0].date(),
        datetime.strptime(game[1].strip(), "%I:%M %p"),
        game[2]
    ))
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Diamond", "Home Team", "Home Division", "Away Team", "Away Division"])
        for game in sorted_schedule:
            date_dt, slot, field, home, home_div, away, away_div = game
            writer.writerow([date_dt.strftime('%Y-%m-%d'), slot, field, home, home_div, away, away_div])

def print_schedule_summary(team_stats):
    rows = []
    for team, stats in sorted(team_stats.items()):
        rows.append([div_of(team), team, target_games(team), stats['total_games'], stats['home_games'], stats['away_games']])

    print("\nSchedule Summary:")
    if PrettyTable:
        table = PrettyTable()
        table.field_names = ["Division", "Team", "Target", "Total Games", "Home Games", "Away Games"]
        for r in rows:
            table.add_row(r)
        print(table)
    else:
        header = ["Division","Team","Target","Total","Home","Away"]
        print(" | ".join(header))
        for r in rows:
            print(" | ".join(map(str, r)))

def print_doubleheader_summary(doubleheader_count):
    rows = []
    for team in sorted(doubleheader_count.keys()):
        rows.append([team, div_of(team), doubleheader_count[team], min_dh(team), max_dh(team)])

    print("\nDoubleheader Summary (Days with 2 games):")
    if PrettyTable:
        table = PrettyTable()
        table.field_names = ["Team", "Division", "DH Days", "Min", "Max"]
        for r in rows:
            table.add_row(r)
        print(table)
    else:
        header = ["Team","Div","DH","Min","Max"]
        print(" | ".join(header))
        for r in rows:
            print(" | ".join(map(str, r)))

def generate_matchup_table(schedule, division_teams):
    matchup_count = defaultdict(lambda: defaultdict(int))
    for date_dt, slot, field, home_team, home_div, away_team, away_div in schedule:
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

def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    team_blackouts = load_team_blackouts('team_blackouts.csv')

    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(6)],
        'D': [f'D{i+1}' for i in range(6)],
    }
    all_teams = [t for div in ('A', 'B', 'C', 'D') for t in division_teams[div]]

    schedule = []
    team_stats = defaultdict(lambda: {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'weekly_games': defaultdict(int)
    })
    used_slots = {}  # keyed by (date, time_str, field)

    team_game_days = defaultdict(lambda: defaultdict(int))
    team_game_slots = defaultdict(lambda: defaultdict(list))
    team_doubleheader_opponents = defaultdict(lambda: defaultdict(set))
    doubleheader_count = defaultdict(int)

    timeslots_by_date = defaultdict(list)
    for date_dt, slot, field in field_availability:
        d = date_dt.date()
        if slot not in timeslots_by_date[d]:
            timeslots_by_date[d].append(slot)
    for d in timeslots_by_date:
        timeslots_by_date[d].sort(key=lambda s: datetime.strptime(s.strip(), "%I:%M %p"))

    for t in all_teams:
        _ = team_stats[t]

    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")

    unscheduled = matchups[:]

    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     team_doubleheader_opponents, used_slots, unscheduled) = schedule_doubleheaders_preemptively(
        all_teams, unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
        team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots
    )

    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     team_doubleheader_opponents, used_slots, unscheduled) = force_minimum_doubleheaders(
        all_teams, unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
        team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, schedule
    )

    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     used_slots) = schedule_A_pair_doubleheaders(
        division_teams, team_availability, field_availability, team_blackouts, timeslots_by_date,
        team_stats, doubleheader_count, team_game_days, team_game_slots, used_slots, schedule
    )

    unscheduled = [m for m in unscheduled if div_of(m[0]) != 'A' and div_of(m[1]) != 'A']

    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     team_doubleheader_opponents, used_slots, unscheduled) = schedule_games(
        unscheduled, team_availability, field_availability, team_blackouts,
        schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
        team_doubleheader_opponents, used_slots, timeslots_by_date
    )

    if any(team_stats[t]['total_games'] < target_games(t) for t in all_teams if div_of(t) != 'A'):
        print("Filling missing games (with fallback pairing)...")
        (schedule, team_stats, doubleheader_count, unscheduled) = fill_missing_games(
            schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
            team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled,
            team_availability, team_blackouts, field_availability, all_teams
        )

    missing = [t for t in all_teams if team_stats[t]['total_games'] < target_games(t)]
    if missing:
        print(f"Critical: Teams below target games: {missing}")

    under_dh = [t for t in all_teams if doubleheader_count[t] < min_dh(t)]
    if under_dh:
        print(f"Critical: Teams below minimum DH days: {under_dh}")

    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    print_doubleheader_summary(doubleheader_count)
    generate_matchup_table(schedule, division_teams)

if __name__ == "__main__":
    main()
