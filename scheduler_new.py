#!/usr/bin/env python3
import csv
import itertools
import random
from datetime import datetime
from collections import defaultdict
from prettytable import PrettyTable

# -------------------------------
# Configurable parameters
# -------------------------------
MAX_RETRIES = 20000            # scheduling backtracking limit
MIN_GAP = 5                    # minimum days between game dates
WEEKLY_GAME_LIMIT = 2          # max games per team per week
HOME_AWAY_BALANCE = 11         # desired home games per team (for 22-game seasons)

# Per-division configuration (tweak here)
DIVISION_SETTINGS = {
    # A: no inter, only doubleheaders => 11 DH days * 2 games = 22
    'A': {'inter': False, 'target_games': 22, 'min_dh': 11, 'max_dh': 11},

    # B/C/D: allow inter; DH ranges can be whatever you want (examples)
    'B': {'inter': True,  'target_games': 22, 'min_dh': 4,  'max_dh': 7},
    'C': {'inter': True,  'target_games': 22, 'min_dh': 3,  'max_dh': 6},
    'D': {'inter': True,  'target_games': 22, 'min_dh': 3,  'max_dh': 6},
}

# Inter-division pairing settings (only applied if BOTH divisions have inter=True)
INTER_PAIR_SETTINGS = {
    ('A', 'B'): False,
    ('A', 'C'): False,
    ('A', 'D'): False,

    ('B', 'C'): True,
    ('B', 'D'): True,
    ('C', 'D'): True,
}

# How many inter games each team in the pair should have against the other division.
# With 8 teams per division, degree=4 means each team plays 4 opponents from the other division (1 game each).
# “Average per team” targets. The solver will distribute slight unevenness.
INTER_DEGREE = {
    # B vs (C,D): B needs about 8 inter games total if B does full intra (14 games)
    ('B', 'C'): 4,   # target ~4 inter games per B team vs C
    ('B', 'D'): 4,   # target ~4 inter games per B team vs D

    # C vs D: C and D each need ~12 inter if they do full intra (10 games)
    ('C', 'D'): 6,   # target “play each opponent once” (6 games per team)
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
                    print(f"Error parsing blackout date '{d}' for team {team}: {e}")
            blackouts[team] = dates
    return blackouts

# -------------------------------
# Intra-division matchup generation
# -------------------------------
def _round_robin_pairs(teams):
    # standard circle method yields rounds of disjoint pairs (n even)
    teams = list(teams)
    n = len(teams)
    assert n % 2 == 0, "round robin requires even team count"
    left = teams[:n//2]
    right = teams[n//2:]
    rounds = []
    for _ in range(n-1):
        pairs = list(zip(left, reversed(right)))
        rounds.append(pairs)
        # rotate
        right = [left.pop(1)] + right
        left.insert(1, right.pop())
    return rounds

def generate_intra_matchups_for_target(division, teams, intra_target_per_team):
    """
    Generates directed (home, away) matchups *within* a division so each team has intra_target_per_team games.

    Supported (for 8 teams):
      - 14 games/team -> double round robin (2 vs each opponent)
      - 18 games/team -> 3 opponents twice, 4 opponents three times (legacy A/C behavior)
      - 22 games/team -> mostly 3 vs each opponent (21) + one "rival" opponent 4th game (balanced via perfect matching)
    """
    teams = sorted(teams)

    if intra_target_per_team == 14:
        # 2 games vs each opponent (home/away)
        matchups = []
        for t1, t2 in itertools.combinations(teams, 2):
            matchups.append((t1, t2))
            matchups.append((t2, t1))
        return matchups

    if intra_target_per_team == 18:
        # legacy: 3 opponents x2, 4 opponents x3  => 18
        two_game_count = 3  # each team has exactly 3 "2-game" opponents

        pairs = list(itertools.combinations(teams, 2))
        count2 = {t: 0 for t in teams}
        assignment = {}

        def backtrack(i):
            if i == len(pairs):
                return all(count2[t] == two_game_count for t in teams)

            a, b = pairs[i]
            # Try weight=2
            if count2[a] < two_game_count and count2[b] < two_game_count:
                assignment[(a, b)] = 2
                count2[a] += 1
                count2[b] += 1
                if backtrack(i + 1):
                    return True
                count2[a] -= 1
                count2[b] -= 1
                del assignment[(a, b)]

            # Fallback weight=3
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

    if intra_target_per_team == 22:
        # 7 opponents *3 = 21 plus 1 extra game vs a "rival" opponent.
        # Implement as:
        #   - every pair gets 3 games (2+1 random home)
        #   - plus 1 extra game for a perfect-matching set of 4 pairs (adds 1 game to each team)
        matchups = []
        for a, b in itertools.combinations(teams, 2):
            matchups.extend([(a, b), (b, a)])  # 2 games
            matchups.append((a, b) if random.random() < 0.5 else (b, a))  # +1 game (random home)

        # Add one extra game per team using disjoint pairs
        rounds = _round_robin_pairs(teams)
        rival_pairs = random.choice(rounds)  # 4 disjoint pairs for 8 teams
        for a, b in rival_pairs:
            matchups.append((a, b) if random.random() < 0.5 else (b, a))  # 4th game between rivals

        return matchups

    raise Exception(f"Unsupported intra_target_per_team={intra_target_per_team} for division {division}.")

# -------------------------------
# Inter-division matchup generation
# -------------------------------
def generate_bipartite_regular_matchups(teams1, teams2, degree):
    teams1_order = teams1[:]
    random.shuffle(teams1_order)
    assignment = {t: [] for t in teams1_order}
    capacity = {t: degree for t in teams2}

    def backtrack(i):
        if i == len(teams1_order):
            return True
        team = teams1_order[i]
        available = [t for t in teams2 if capacity[t] > 0]
        for combo in itertools.combinations(available, degree):
            assignment[team] = list(combo)
            for t in combo:
                capacity[t] -= 1
            if backtrack(i + 1):
                return True
            for t in combo:
                capacity[t] += 1
        return False

    if not backtrack(0):
        raise Exception("No valid bipartite matching found.")

    edges = []
    for team in teams1_order:
        for opp in assignment[team]:
            edges.append((team, opp))
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
    # Determine which inter pairs are enabled and compute intra targets accordingly.
    enabled_pairs = []
    for (d1, d2), enabled in INTER_PAIR_SETTINGS.items():
        if enabled and inter_enabled_for_pair(d1, d2):
            enabled_pairs.append((d1, d2))

    # Compute inter games per team by division.
    inter_per_team = {d: 0 for d in division_teams.keys()}
    for d1, d2 in enabled_pairs:
        deg = pair_degree(d1, d2)
        inter_per_team[d1] += deg
        inter_per_team[d2] += deg

    # Build intra matchups
    full_matchups = []
    for div, teams in division_teams.items():
        intra_target = DIVISION_SETTINGS[div]['target_games'] - inter_per_team.get(div, 0)
        full_matchups.extend(generate_intra_matchups_for_target(div, teams, intra_target))

    # Build enabled inter matchups
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

        for team in all_teams:
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

                        # opp availability / blackouts
                        if day_of_week not in team_availability.get(opp1, set()) or d in team_blackouts.get(opp1, set()):
                            continue
                        if day_of_week not in team_availability.get(opp2, set()) or d in team_blackouts.get(opp2, set()):
                            continue
                        if team_game_days[opp1].get(d, 0) != 0 or team_game_days[opp2].get(d, 0) != 0:
                            continue

                        # weekly limit checks
                        if team_stats[team]['weekly_games'][week_num] + 2 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp1]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp2]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue

                        # target games cap
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

                        # Apply schedule
                        unscheduled.remove(m1)
                        unscheduled.remove(m2)

                        team_stats[home1]['home_games'] += 1
                        team_stats[away1]['away_games'] += 1
                        team_stats[home2]['home_games'] += 1
                        team_stats[away2]['away_games'] += 1

                        schedule.append((date1, slot1_str, field1, home1, home1[0], away1, away1[0]))
                        schedule.append((date2, slot2_str, field2, home2, home2[0], away2, away2[0]))

                        # update counts
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
                        break  # stop after scheduling a DH for this team on this date

            # Case 2: already 1 game today -> try add adjacent slot to make DH
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

                # who did we already play today?
                already_opp = None
                for g in schedule:
                    if g[0].date() == d and (g[3] == team or g[5] == team):
                        already_opp = g[5] if g[3] == team else g[3]
                        break
                if already_opp is None:
                    continue

                # enforce per-division max DH days
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
# Dedicated Doubleheader pass (Two-phase), now per-division min/max
# -------------------------------
def force_minimum_doubleheaders(all_teams, unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
                                team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents,
                                used_slots, schedule):

    teams = all_teams[:]
    random.shuffle(teams)

    # Phase 1: ensure each team gets at least 1 DH day (if min_dh > 0)
    for team in teams:
        if min_dh(team) <= 0 or doubleheader_count[team] >= 1:
            continue

        for d in sorted(timeslots_by_date.keys()):
            day_of_week = d.strftime('%a')
            if d in team_blackouts.get(team, set()) or day_of_week not in team_availability.get(team, set()):
                continue
            week_num = d.isocalendar()[1]
            sorted_slots = timeslots_by_date[d]
            games_today = team_game_days[team].get(d, 0)

            # We only add the second game adjacent
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
    teams = all_teams[:]
    random.shuffle(teams)
    for team in teams:
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
# Primary scheduling
# -------------------------------
def schedule_games(matchups, team_availability, field_availability, team_blackouts,
                   schedule, team_stats, doubleheader_count,
                   team_game_days, team_game_slots, team_doubleheader_opponents,
                   used_slots, timeslots_by_date):

    unscheduled = matchups[:]  # local copy
    retry_count = 0

    while unscheduled and retry_count < MAX_RETRIES:
        progress_made = False

        for date, slot, field in field_availability:
            if used_slots.get((date, slot, field), False):
                continue

            d = date.date()
            day_of_week = date.strftime('%a')
            week_num = date.isocalendar()[1]

            for matchup in unscheduled[:]:
                home, away = matchup

                if day_of_week not in team_availability.get(home, set()) or day_of_week not in team_availability.get(away, set()):
                    continue
                if d in team_blackouts.get(home, set()) or d in team_blackouts.get(away, set()):
                    continue

                if team_stats[home]['total_games'] >= target_games(home) or team_stats[away]['total_games'] >= target_games(away):
                    continue
                if (team_stats[home]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or
                    team_stats[away]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT):
                    continue
                if not (min_gap_ok(home, d, team_game_days) and min_gap_ok(away, d, team_game_days)):
                    continue
                if slot in team_game_slots[home][d] or slot in team_game_slots[away][d]:
                    continue

                # If a team already has a game today, the new game must be in the immediate next timeslot.
                valid_slot = True
                for team in (home, away):
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

                # Enforce per-division max DH days + ensure different opponents on DH day
                can_double = True
                for team, opp in ((home, away), (away, home)):
                    if team_game_days[team][d] == 1:
                        if doubleheader_count[team] >= max_dh(team):
                            can_double = False
                            break
                        if team_doubleheader_opponents[team][d] and opp in team_doubleheader_opponents[team][d]:
                            can_double = False
                            break
                if not can_double:
                    continue

                # Home/Away balancing
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
                unscheduled.remove(matchup)
                progress_made = True
                break

            if progress_made:
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

            for matchup in unscheduled[:]:
                home, away = matchup

                if team_stats[home]['total_games'] >= target_games(home) and team_stats[away]['total_games'] >= target_games(away):
                    continue
                if day_of_week not in team_availability.get(home, set()) or day_of_week not in team_availability.get(away, set()):
                    continue
                if d in team_blackouts.get(home, set()) or d in team_blackouts.get(away, set()):
                    continue
                if not (min_gap_ok(home, d, team_game_days) and min_gap_ok(away, d, team_game_days)):
                    continue
                if slot in team_game_slots[home][d] or slot in team_game_slots[away][d]:
                    continue

                # must be adjacent slot if already playing today
                valid_slot = True
                for team in (home, away):
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

                # DH constraints
                can_double = True
                for team, opp in ((home, away), (away, home)):
                    if team_game_days[team][d] == 1:
                        if doubleheader_count[team] >= max_dh(team):
                            can_double = False
                            break
                        if opp in team_doubleheader_opponents[team][d]:
                            can_double = False
                            break
                if not can_double:
                    continue

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
                unscheduled.remove(matchup)
                progress = True
                break

            if progress:
                break

        retry_count = 0 if progress else retry_count + 1

    return schedule, team_stats, doubleheader_count, unscheduled

# -------------------------------
# Output / Debug
# -------------------------------
def output_schedule_to_csv(schedule, output_file):
    sorted_schedule = sorted(schedule, key=lambda game: (
        game[0],
        datetime.strptime(game[1].strip(), "%I:%M %p"),
        game[2]
    ))
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Diamond", "Home Team", "Home Division", "Away Team", "Away Division"])
        for game in sorted_schedule:
            date, slot, field, home, home_div, away, away_div = game
            writer.writerow([date.strftime('%Y-%m-%d'), slot, field, home, home_div, away, away_div])

def print_schedule_summary(team_stats):
    table = PrettyTable()
    table.field_names = ["Division", "Team", "Target", "Total Games", "Home Games", "Away Games"]
    for team, stats in sorted(team_stats.items()):
        table.add_row([div_of(team), team, target_games(team), stats['total_games'], stats['home_games'], stats['away_games']])
    print("\nSchedule Summary:")
    print(table)

def print_doubleheader_summary(doubleheader_count):
    table = PrettyTable()
    table.field_names = ["Team", "Division", "DH Days", "Min", "Max"]
    for team in sorted(doubleheader_count.keys()):
        table.add_row([team, div_of(team), doubleheader_count[team], min_dh(team), max_dh(team)])
    print("\nDoubleheader Summary (Days with 2 games):")
    print(table)

def generate_matchup_table(schedule, division_teams):
    matchup_count = defaultdict(lambda: defaultdict(int))
    for game in schedule:
        home_team = game[3]
        away_team = game[5]
        matchup_count[home_team][away_team] += 1
        matchup_count[away_team][home_team] += 1
    all_teams = sorted([team for teams in division_teams.values() for team in teams])
    table = PrettyTable()
    table.field_names = ["Team"] + all_teams
    for team in all_teams:
        row = [team] + [matchup_count[team][opp] for opp in all_teams]
        table.add_row(row)
    print("\nMatchup Table:")
    print(table)

# -------------------------------
# Main
# -------------------------------
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    team_blackouts = load_team_blackouts('team_blackouts.csv')

    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    all_teams = [t for div in ('A', 'B', 'C') for t in division_teams[div]]

    # Initialize state
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

    # Build timeslots_by_date mapping.
    timeslots_by_date = defaultdict(list)
    for date, slot, field in field_availability:
        d = date.date()
        if slot not in timeslots_by_date[d]:
            timeslots_by_date[d].append(slot)
    for d in timeslots_by_date:
        timeslots_by_date[d].sort(key=lambda s: datetime.strptime(s.strip(), "%I:%M %p"))

    # Seed team_stats keys (important: so summaries include teams even if 0 games)
    for t in all_teams:
        _ = team_stats[t]  # touch

    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")

    unscheduled = matchups[:]

    # Preemptively schedule DHs (per-division min/max)
    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     team_doubleheader_opponents, used_slots, unscheduled) = schedule_doubleheaders_preemptively(
        all_teams, unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
        team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots
    )

    # Dedicated DH optimization pass
    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     team_doubleheader_opponents, used_slots, unscheduled) = force_minimum_doubleheaders(
        all_teams, unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
        team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, schedule
    )

    # Primary scheduling pass
    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     team_doubleheader_opponents, used_slots, unscheduled) = schedule_games(
        unscheduled, team_availability, field_availability, team_blackouts,
        schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
        team_doubleheader_opponents, used_slots, timeslots_by_date
    )

    # Fill missing games if needed
    if any(team_stats[t]['total_games'] < target_games(t) for t in all_teams):
        print("Filling missing games...")
        (schedule, team_stats, doubleheader_count, unscheduled) = fill_missing_games(
            schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
            team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled,
            team_availability, team_blackouts, field_availability
        )

    # Final checks
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
