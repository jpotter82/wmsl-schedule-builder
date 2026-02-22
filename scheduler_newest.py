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
from datetime import datetime
from collections import defaultdict

try:
    from prettytable import PrettyTable
except ImportError:
    PrettyTable = None

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
MIN_GAP = 5                    # minimum days between game dates
WEEKLY_GAME_LIMIT = 2          # max games per team per week
HOME_AWAY_BALANCE = 11         # desired home games per team (for 22-game seasons)

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
    """Return True if 'team' has no game scheduled within MIN_GAP days of date d."""
    for gd in team_game_days[team]:
        if gd != d and abs((d - gd).days) < MIN_GAP:
            return False
    return True

# -------------------------------
# Data loading functions
# -------------------------------
def load_team_availability(file_path):
    availability = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # header
        for row in reader:
            team = row[0].strip()
            days = row[1:]
            availability[team] = {day.strip() for day in days if day and day.strip()}
    return availability

def load_field_availability(file_path):
    field_availability = []
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # header
        for row in reader:
            date = datetime.strptime(row[0].strip(), '%Y-%m-%d')
            slot = row[1].strip()
            field = row[2].strip()
            field_availability.append((date, slot, field))

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

    avg_meet = float(intra_target_per_team) / max(1, (n - 1))
    soft_cap = int(math.ceil(avg_meet)) + 1

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

        for d in sorted(timeslots_by_date.keys()):
            day_of_week = d.strftime('%a')
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
            for d in sorted(timeslots_by_date.keys()):
                day_of_week = d.strftime('%a')
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
def schedule_A_pair_doubleheaders(division_teams, team_availability, field_availability, team_blackouts,
                                  timeslots_by_date, team_stats, doubleheader_count,
                                  team_game_days, team_game_slots, used_slots, schedule=None):
    if schedule is None:
        schedule = []
    if not isinstance(schedule, list):
        raise TypeError("schedule must be list[game_tuple], got {}".format(type(schedule)))

    A_teams = list(division_teams.get('A', []))
    if not A_teams:
        return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, used_slots

    target_sessions = DIVISION_SETTINGS['A']['target_games'] // 2  # 22 games => 11 sessions

    sessions_done = defaultdict(int)
    pair_sessions = defaultdict(int)

    slots_by_date_field = defaultdict(list)
    for date_dt, slot, field in field_availability:
        slots_by_date_field[(date_dt.date(), field)].append(slot)

    
    # Lookup for the canonical datetime object from field_availability (these are midnight datetimes).
    # Important: keep (dt, slot, field) keys consistent everywhere (used_slots, exports, etc.)
    dt_by_key = {}
    for date_dt, slot, field in field_availability:
        dt_by_key[(date_dt.date(), slot, field)] = date_dt

    for k in list(slots_by_date_field.keys()):
        slots_by_date_field[k] = sorted(set(slots_by_date_field[k]), key=lambda s: datetime.strptime(s, "%I:%M %p"))

    adjacent_pairs = []
    for (d, field), slots in slots_by_date_field.items():
        for i in range(len(slots) - 1):
            adjacent_pairs.append((d, field, slots[i], slots[i + 1]))
    adjacent_pairs.sort(key=lambda x: (x[0], datetime.strptime(x[2], "%I:%M %p"), x[1]))

    def can_play_dh(team, d):
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

    def place_game(dt, slot, field, home, away):
        schedule.append((dt, slot, field, home, home[0], away, away[0]))
        used_slots[(dt, slot, field)] = True

        d = dt.date()
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

    for _pass in range(6):
        progress = False
        for (d, field, s1, s2) in adjacent_pairs:
            if all(sessions_done[t] >= target_sessions for t in A_teams):
                break

            dt1 = dt_by_key.get((d, s1, field))
            dt2 = dt_by_key.get((d, s2, field))

            if dt1 is None or dt2 is None:
                # Should not happen unless field_availability has gaps / duplicates
                continue

            if used_slots.get((dt1, s1, field), False) or used_slots.get((dt2, s2, field), False):
                continue

            pair = choose_pair()
            if not pair:
                continue
            t1, t2 = pair
            if not (can_play_dh(t1, d) and can_play_dh(t2, d)):
                continue

            place_game(dt1, s1, field, t1, t2)
            place_game(dt2, s2, field, t2, t1)

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

        for date, slot, field in field_availability:
            if used_slots.get((date, slot, field), False):
                continue

            d = date.date()
            day_of_week = date.strftime('%a')
            week_num = date.isocalendar()[1]

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

                valid_slot = True
                for team in (t1, t2):
                    if team_game_slots[team][d]:
                        current = team_game_slots[team][d][0]
                        sorted_slots = timeslots_by_date[d]
                        try:
                            idx = sorted_slots.index(current)
                        except ValueError:
                            valid_slot = False
                            break
                        if idx + 1 >= len(sorted_slots):
                            valid_slot = False
                            break
                        required_slot = sorted_slots[idx + 1]
                        if slot != required_slot:
                            valid_slot = False
                            break
                if not valid_slot:
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
            break

        retry_count = 0 if progress_made else retry_count + 1

    if unscheduled:
        print("Warning: Retry limit reached in primary scheduling. Some predetermined matchups could not be scheduled.")
    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, unscheduled

def fill_missing_games(schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
                       team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled,
                       team_availability, team_blackouts, field_availability):
    retry_count = 0
    while any(stats['total_games'] < target_games(team) for team, stats in team_stats.items()) and retry_count < MAX_RETRIES:
        progress = False

        for date, slot, field in field_availability:
            if used_slots.get((date, slot, field), False):
                continue

            d = date.date()
            day_of_week = date.strftime('%a')
            week_num = date.isocalendar()[1]

            best = None
            best_score = -1

            for (t1, t2) in unscheduled:
                if div_of(t1) == 'A' or div_of(t2) == 'A':
                    continue
                if team_stats[t1]['total_games'] >= target_games(t1) and team_stats[t2]['total_games'] >= target_games(t2):
                    continue
                if day_of_week not in team_availability.get(t1, set()) or day_of_week not in team_availability.get(t2, set()):
                    continue
                if d in team_blackouts.get(t1, set()) or d in team_blackouts.get(t2, set()):
                    continue
                if not (min_gap_ok(t1, d, team_game_days) and min_gap_ok(t2, d, team_game_days)):
                    continue
                if slot in team_game_slots[t1][d] or slot in team_game_slots[t2][d]:
                    continue

                valid_slot = True
                for team in (t1, t2):
                    if team_game_slots[team][d]:
                        current = team_game_slots[team][d][0]
                        sorted_slots = timeslots_by_date[d]
                        try:
                            idx = sorted_slots.index(current)
                        except ValueError:
                            valid_slot = False
                            break
                        if idx + 1 >= len(sorted_slots):
                            valid_slot = False
                            break
                        required_slot = sorted_slots[idx + 1]
                        if slot != required_slot:
                            valid_slot = False
                            break
                if not valid_slot:
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
            unscheduled.remove((t1, t2))
            progress = True
            break

        retry_count = 0 if progress else retry_count + 1

    return schedule, team_stats, doubleheader_count, unscheduled

# -------------------------------
# Export helpers: make outputs match field_availability row count
# -------------------------------
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
            writer.writerow([dt.strftime('%Y-%m-%d'), dt.strftime('%a'), slot, field, home, home_div, away, away_div])
    return rows

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

def export_schedule_to_xlsx(field_availability, schedule, division_teams, output_path):
    if Workbook is None:
        raise RuntimeError("openpyxl is not installed. Run: pip install openpyxl")

    rows = build_slot_rows(field_availability, schedule)

    wb = Workbook()

    # ---------------- Schedule ----------------
    ws = wb.active
    ws.title = "Schedule"

    headers = ["Date", "Day", "Time", "Diamond", "Home Team", "Away Team", "Home Div", "Away Div", "WeekNum", "SlotIndex"]  # last 2 will be hidden
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.fill = PatternFill("solid", fgColor="D9E1F2")

    
    # Build per-date slot order index (1..N) for adjacency checks in Excel.
    slots_by_date = defaultdict(list)
    for dt0, slot0, _field0 in field_availability:
        d0 = dt0.date()
        slots_by_date[d0].append(slot0)
    slot_index_by_date_slot = {}
    for d0, slots0 in slots_by_date.items():
        uniq = sorted(set(slots0), key=lambda s: datetime.strptime(s.strip(), "%I:%M %p"))
        for i, s in enumerate(uniq, start=1):
            slot_index_by_date_slot[(d0, s)] = i

    for (dt, slot, field, home, home_div, away, away_div) in rows:
            d = dt.date()
            wk = d.isocalendar()[1]
            slot_idx = slot_index_by_date_slot.get((d, slot), "")
            ws.append([d, dt.strftime('%a'), slot, field, home, away, home_div, away_div, wk, slot_idx])

    n = len(rows)
    # set formats
    for r in range(2, n + 2):
        ws.cell(row=r, column=1).number_format = "yyyy-mm-dd"
        ws.cell(row=r, column=3).number_format = "@"

    # Freeze header and apply filter
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = "A1:H{}".format(n + 1)

    # Hide helper columns
    ws.column_dimensions['I'].hidden = True
    ws.column_dimensions['J'].hidden = True

    # Conditional formatting
    # (1) Unused slot (Home blank) -> light gray
    ws.conditional_formatting.add(
        "A2:H{}".format(n + 1),
        FormulaRule(formula=['$E2=""'], fill=PatternFill("solid", fgColor="F2F2F2"))
    )
    # (2) Home==Away (bad) -> red fill
    ws.conditional_formatting.add(
        "E2:F{}".format(n + 1),
        FormulaRule(formula=['AND($E2<>"",$F2<>"",$E2=$F2)'], fill=PatternFill("solid", fgColor="FFC7CE"))
    )
    # (3) Illegal A vs C (or C vs A) -> red fill across row
    ws.conditional_formatting.add(
        "A2:H{}".format(n + 1),
        FormulaRule(formula=['OR(AND(LEFT($E2,1)="A",LEFT($F2,1)="C"),AND(LEFT($E2,1)="C",LEFT($F2,1)="A"))'],
                   fill=PatternFill("solid", fgColor="FFC7CE"))
    )

    _autofit(ws, n + 1, 8)

    # ---------------- Teams ----------------
    ws_t = wb.create_sheet("Teams")
    ws_t.append(["Team", "Division"])
    ws_t["A1"].font = ws_t["B1"].font = Font(bold=True)
    ws_t["A1"].fill = ws_t["B1"].fill = PatternFill("solid", fgColor="D9E1F2")

    all_teams = sorted([t for div in sorted(division_teams.keys()) for t in division_teams[div]])
    for t in all_teams:
        ws_t.append([t, div_of(t)])
    _autofit(ws_t, len(all_teams) + 1, 2, min_width=8, max_width=16)


    # ---------------- TeamDate (helper: games/day + non-adjacent DH detection) ----------------
    ws_td = wb.create_sheet("TeamDate")
    ws_td.append(["Key", "Date", "Team", "GamesThatDay", "MinSlot", "MaxSlot", "NonAdjFlag", "WeekNum"])
    for cell in ws_td[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")

    # Unique dates from field availability
    unique_dates = sorted({dt.date() for (dt, _, _) in field_availability})

    # schedule ranges (include helper cols in Schedule)
    sched_first = 2
    sched_last = n + 1
    date_rng = "Schedule!$A${}:$A${}".format(sched_first, sched_last)
    home_rng = "Schedule!$E${}:$E${}".format(sched_first, sched_last)
    away_rng = "Schedule!$F${}:$F${}".format(sched_first, sched_last)
    week_rng = "Schedule!$I${}:$I${}".format(sched_first, sched_last)
    slotidx_rng = "Schedule!$J${}:$J${}".format(sched_first, sched_last)

    row_idx = 2
    for d in unique_dates:
        wk = d.isocalendar()[1]
        for t in all_teams:
            # Key
            ws_td.cell(row=row_idx, column=1, value='=TEXT($B{r},"yyyymmdd")&"|"&$C{r}'.format(r=row_idx))
            ws_td.cell(row=row_idx, column=2, value=d)
            ws_td.cell(row=row_idx, column=3, value=t)

            # GamesThatDay = count home + count away
            ws_td.cell(
                row=row_idx,
                column=4,
                value='=COUNTIFS({date_rng},$B{r},{home_rng},$C{r})+COUNTIFS({date_rng},$B{r},{away_rng},$C{r})'.format(
                    date_rng=date_rng, home_rng=home_rng, away_rng=away_rng, r=row_idx
                )
            )

            # MinSlot: MIN of home/away mins; use IFERROR to avoid #VALUE
            ws_td.cell(
                row=row_idx,
                column=5,
                value='=MIN(IFERROR(MINIFS({slotidx_rng},{date_rng},$B{r},{home_rng},$C{r}),9999),IFERROR(MINIFS({slotidx_rng},{date_rng},$B{r},{away_rng},$C{r}),9999))'.format(
                    slotidx_rng=slotidx_rng, date_rng=date_rng, home_rng=home_rng, away_rng=away_rng, r=row_idx
                )
            )
            # MaxSlot
            ws_td.cell(
                row=row_idx,
                column=6,
                value='=MAX(IFERROR(MAXIFS({slotidx_rng},{date_rng},$B{r},{home_rng},$C{r}),0),IFERROR(MAXIFS({slotidx_rng},{date_rng},$B{r},{away_rng},$C{r}),0))'.format(
                    slotidx_rng=slotidx_rng, date_rng=date_rng, home_rng=home_rng, away_rng=away_rng, r=row_idx
                )
            )
            # NonAdjFlag: if >=2 games and slots not consecutive / compact
            ws_td.cell(
                row=row_idx,
                column=7,
                value='=IF($D{r}<=1,0,IF(($F{r}-$E{r}+1)<>$D{r},1,0))'.format(r=row_idx)
            )
            ws_td.cell(row=row_idx, column=8, value=wk)
            row_idx += 1

    ws_td.freeze_panes = "A2"
    _autofit(ws_td, row_idx - 1, 8, min_width=10, max_width=20)

    # ---------------- TeamWeek (helper: weekly limit detection) ----------------
    ws_tw = wb.create_sheet("TeamWeek")
    ws_tw.append(["Key", "Team", "WeekNum", "GamesInWeek", "WeeklyLimitFlag"])
    for cell in ws_tw[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")

    unique_weeks = sorted({d.isocalendar()[1] for d in unique_dates})
    r2 = 2
    for t in all_teams:
        for wk in unique_weeks:
            ws_tw.cell(row=r2, column=1, value='=$B{r}&"|"&$C{r}'.format(r=r2))
            ws_tw.cell(row=r2, column=2, value=t)
            ws_tw.cell(row=r2, column=3, value=wk)
            ws_tw.cell(
                row=r2,
                column=4,
                value='=COUNTIFS({week_rng},$C{r},{home_rng},$B{r})+COUNTIFS({week_rng},$C{r},{away_rng},$B{r})'.format(
                    week_rng=week_rng, home_rng=home_rng, away_rng=away_rng, r=r2
                )
            )
            ws_tw.cell(
                row=r2,
                column=5,
                value='=IF($D{r}>2,1,0)'.format(r=r2, limit=2)
            )
            r2 += 1

    ws_tw.freeze_panes = "A2"
    _autofit(ws_tw, r2 - 1, 5, min_width=10, max_width=20)

    # ---------------- Summary (formulas) ----------------
    ws_s = wb.create_sheet("Summary")
    headers = ["Division", "Team", "Target", "Total Games", "Home Games", "Away Games", "DH Days", "Min DH", "Max DH"]
    ws_s.append(headers)
    for cell in ws_s[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")

    # TeamDate ranges
    td_last = row_idx - 1
    td_team_rng = "TeamDate!$B$2:$B${}".format(td_last)
    td_games_rng = "TeamDate!$C$2:$C${}".format(td_last)

    for i, t in enumerate(all_teams, start=2):
        ws_s.cell(row=i, column=1, value=div_of(t))
        ws_s.cell(row=i, column=2, value=t)
        ws_s.cell(row=i, column=3, value=target_games(t))

        # total games (home + away)
        ws_s.cell(row=i, column=4, value='=COUNTIF({0},$B{1})+COUNTIF({2},$B{1})'.format(home_rng, i, away_rng))
        ws_s.cell(row=i, column=5, value='=COUNTIF({0},$B{1})'.format(home_rng, i))
        ws_s.cell(row=i, column=6, value='=COUNTIF({0},$B{1})'.format(away_rng, i))

        # DH days = count TeamDate rows where team==this and GamesThatDay>=2
        ws_s.cell(row=i, column=7, value='=COUNTIFS({0},$B{1},{2},">=2")'.format(td_team_rng, i, td_games_rng))
        ws_s.cell(row=i, column=8, value=min_dh(t))
        ws_s.cell(row=i, column=9, value=max_dh(t))

    ws_s.freeze_panes = "A2"
    ws_s.auto_filter.ref = "A1:I{}".format(len(all_teams) + 1)

    # conditional formatting: flag teams under target games
    ws_s.conditional_formatting.add(
        "D2:D{}".format(len(all_teams) + 1),
        FormulaRule(formula=['$D2<$C2'], fill=PatternFill("solid", fgColor="FFC7CE"))
    )
    # flag teams under min DH
    ws_s.conditional_formatting.add(
        "G2:G{}".format(len(all_teams) + 1),
        FormulaRule(formula=['$G2<$H2'], fill=PatternFill("solid", fgColor="FFC7CE"))
    )

    _autofit(ws_s, len(all_teams) + 1, 9, min_width=10, max_width=18)

    # ---------------- Matchup Matrix ----------------
    ws_m = wb.create_sheet("Matchup Matrix")
    ws_m.append(["Team"] + all_teams)
    for cell in ws_m[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
        cell.alignment = Alignment(horizontal="center")

    for r, team_r in enumerate(all_teams, start=2):
        ws_m.cell(row=r, column=1, value=team_r).font = Font(bold=True)
        for c, team_c in enumerate(all_teams, start=2):
            # symmetric count of games regardless of home/away
            ws_m.cell(
                row=r, column=c,
                value='=COUNTIFS({0},$A{2},{1},{3})+COUNTIFS({0},{3},{1},$A{2})'.format(
                    home_rng, away_rng, r, get_column_letter(c) + "1"
                )
            )
        # diagonal blank
        ws_m.cell(row=r, column=r).value = ""

    ws_m.freeze_panes = "B2"
    _autofit(ws_m, len(all_teams) + 1, len(all_teams) + 1, min_width=6, max_width=14)

    # Heatmap style for matrix values (exclude headers)
    start_cell = "B2"
    end_cell = "{}{}".format(get_column_letter(len(all_teams) + 1), len(all_teams) + 1)
    ws_m.conditional_formatting.add(
        "{}:{}".format(start_cell, end_cell),
        ColorScaleRule(start_type='num', start_value=0,
                       mid_type='percentile', mid_value=50,
                       end_type='percentile', end_value=90)
    )

    wb.save(output_path)

# -------------------------------
# Console summaries
# -------------------------------
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
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
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
        'weekly_games': defaultdict(int)
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

    matchups = generate_full_matchups(division_teams)
    print("\nTotal generated matchups (unscheduled): {}".format(len(matchups)))

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

    if any(team_stats[t]['total_games'] < target_games(t) for t in all_teams):
        print("Filling missing games...")
        (schedule, team_stats, doubleheader_count, unscheduled) = fill_missing_games(
            schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
            team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled,
            team_availability, team_blackouts, field_availability
        )

    missing = [t for t in all_teams if team_stats[t]['total_games'] < target_games(t)]
    if missing:
        print("Critical: Teams below target games: {}".format(missing))

    under_dh = [t for t in all_teams if doubleheader_count[t] < min_dh(t)]
    if under_dh:
        print("Critical: Teams below minimum DH days: {}".format(under_dh))

    # Export CSV + XLSX with full slot list (row count == field_availability)
    output_schedule_to_csv_full(field_availability, schedule, 'softball_schedule.csv')
    export_schedule_to_xlsx(field_availability, schedule, division_teams, 'softball_schedule.xlsx')

    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    print_doubleheader_summary(doubleheader_count)
    generate_matchup_table(schedule, division_teams)
    print("\nWrote: softball_schedule.csv ({} rows)".format(len(field_availability)))
    print("Wrote: softball_schedule.xlsx")

if __name__ == "__main__":
    main()
