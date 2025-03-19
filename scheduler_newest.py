import csv
import itertools
import random
from datetime import datetime, timedelta
from collections import defaultdict
from prettytable import PrettyTable

# -------------------------------
# Configurable parameters
# -------------------------------
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11
WEEKLY_GAME_LIMIT = 2      # max games per team per week
MAX_RETRIES = 20000        # scheduling backtracking limit
MIN_GAP = 2                # minimum days between game dates

# --- Updated Doubleheader Parameters ---
MIN_DOUBLE_HEADERS = 7     # minimum number of doubleheader sessions per team (each session = 2 games)
MAX_DOUBLE_HEADERS = 9     # maximum allowed doubleheader days per team

# -------------------------------
# Helper Functions
# -------------------------------
def min_gap_ok(team, d, team_game_days):
    """Return True if 'team' has no game scheduled within MIN_GAP days of date d."""
    for gd in team_game_days[team]:
        if gd != d and abs((d - gd).days) < MIN_GAP:
            return False
    return True

def is_legal(matchup):
    """
    Returns True if the matchup is legal.
    Illegal: pairing an A–team with a C–team.
    (Assumes team names begin with their division letter.)
    """
    a, b = matchup
    if (a[0]=='A' and b[0]=='C') or (a[0]=='C' and b[0]=='A'):
        return False
    return True

# -------------------------------
# Data loading functions
# -------------------------------
def load_team_availability(file_path):
    availability = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            team = row[0].strip()
            days = row[1:]
            availability[team] = {day.strip() for day in days if day.strip()}
    return availability

def load_field_availability(file_path):
    field_availability = []
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            date = datetime.strptime(row[0].strip(), '%Y-%m-%d')
            slot = row[1].strip()  # e.g., "10:30 PM"
            field = row[2].strip()
            field_availability.append((date, slot, field))
    # Custom sort: Prioritize Sundays then by date then by time.
    field_availability.sort(key=lambda x: ((0 if x[0].weekday()==6 else 1),
                                           x[0],
                                           datetime.strptime(x[1].strip(), "%I:%M %p")))
    return field_availability

def load_team_blackouts(file_path):
    """
    Loads blackout dates from a CSV file.
    CSV format: Team, Date1, Date2, Date3, ...
    Dates must be in YYYY-MM-DD format.
    Returns a dict mapping team to a set of date objects.
    """
    blackouts = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            team = row[0].strip()
            dates = set()
            for d in row[1:]:
                d = d.strip()
                if d:
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
def assign_intra_division_weights(teams, two_game_count, three_game_count):
    pairs = list(itertools.combinations(sorted(teams), 2))
    count2 = {team: 0 for team in teams}
    assignment = {}
    def backtrack(i):
        if i == len(pairs):
            return all(count2[team] == two_game_count for team in teams)
        team1, team2 = pairs[i]
        if count2[team1] < two_game_count and count2[team2] < two_game_count:
            assignment[(team1, team2)] = 2
            count2[team1] += 1
            count2[team2] += 1
            if backtrack(i+1):
                return True
            count2[team1] -= 1
            count2[team2] -= 1
            del assignment[(team1, team2)]
        assignment[(team1, team2)] = 3
        if backtrack(i+1):
            return True
        del assignment[(team1, team2)]
        return False
    if backtrack(0):
        return assignment
    else:
        raise Exception("No valid intra-division assignment found.")

def generate_intra_matchups(teams, weight_assignment):
    matchups = []
    for (team1, team2), weight in weight_assignment.items():
        if weight == 2:
            matchups.append((team1, team2))
            matchups.append((team2, team1))
        elif weight == 3:
            matchups.append((team1, team2))
            matchups.append((team2, team1))
            if random.random() < 0.5:
                matchups.append((team1, team2))
            else:
                matchups.append((team2, team1))
    return matchups

def generate_intra_division_matchups(division, teams):
    if division == 'B':
        matchups = []
        for team1, team2 in itertools.combinations(sorted(teams), 2):
            matchups.append((team1, team2))
            matchups.append((team2, team1))
        return matchups
    elif division in ['A', 'C']:
        two_game_count = 3
        three_game_count = (len(teams) - 1) - two_game_count
        weight_assignment = assign_intra_division_weights(teams, two_game_count, three_game_count)
        return generate_intra_matchups(teams, weight_assignment)
    else:
        raise Exception("Unknown division")

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
            if backtrack(i+1):
                return True
            for t in combo:
                capacity[t] += 1
        return False
    if backtrack(0):
        edges = []
        for team in teams1_order:
            for opp in assignment[team]:
                edges.append((team, opp))
        return edges
    else:
        raise Exception("No valid bipartite matching found.")

def generate_inter_division_matchups(division_from, division_to, teams_from, teams_to):
    degree = 4
    edges = generate_bipartite_regular_matchups(teams_from, teams_to, degree)
    matchups = []
    for (t1, t2) in edges:
        matchups.append((t1, t2) if random.random() < 0.5 else (t2, t1))
    return matchups

# -------------------------------
# Combine full matchup list
# -------------------------------
def generate_full_matchups(division_teams):
    full_matchups = []
    for div, teams in division_teams.items():
        full_matchups.extend(generate_intra_division_matchups(div, teams))
    inter_AB = generate_inter_division_matchups('A', 'B', division_teams['A'], division_teams['B'])
    full_matchups.extend(inter_AB)
    inter_BC = generate_inter_division_matchups('B', 'C', division_teams['B'], division_teams['C'])
    full_matchups.extend(inter_BC)
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
    elif team_stats[t2]['home_games'] < team_stats[t1]['home_games']:
        return t2, t1
    else:
        return (t1, t2) if random.random() < 0.5 else (t2, t1)

# -------------------------------
# NEW: Preemptive Doubleheader Scheduling
# -------------------------------
def schedule_doubleheaders_preemptively(unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
                                        team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents,
                                        used_slots, schedule=None):
    if schedule is None:
        schedule = []
        
    # Process each available date.
    for d in sorted(timeslots_by_date.keys()):
        day_of_week = d.strftime('%a')
        week_num = d.isocalendar()[1]
        slots = timeslots_by_date[d]
        if not slots:
            continue

        # Process each team that still needs a doubleheader.
        for team in list(team_stats.keys()):
            if doubleheader_count[team] >= MIN_DOUBLE_HEADERS:
                continue
            if day_of_week not in team_availability.get(team, set()):
                continue
            if d in team_blackouts.get(team, set()):
                continue

            games_today = team_game_days[team].get(d, 0)
            # Case 1: No game scheduled yet – try to schedule a doubleheader (2 games) on an empty day.
            if games_today == 0:
                if len(slots) < 2:
                    continue
                for i in range(len(slots) - 1):
                    slot1 = slots[i]
                    slot2 = slots[i+1]
                    free_fields_slot1 = [entry for entry in field_availability 
                                           if entry[0].date() == d and entry[1] == slot1 and ((entry[0], slot1, entry[2]) not in used_slots)]
                    free_fields_slot2 = [entry for entry in field_availability 
                                           if entry[0].date() == d and entry[1] == slot2 and ((entry[0], slot2, entry[2]) not in used_slots)]
                    if not free_fields_slot1 or not free_fields_slot2:
                        continue

                    # Find two distinct matchups from unscheduled that involve the team.
                    candidate_matchups = [m for m in unscheduled if team in m]
                    if len(candidate_matchups) < 2:
                        continue

                    found = False
                    for combo in itertools.combinations(candidate_matchups, 2):
                        m1, m2 = combo
                        opp1 = m1[0] if m1[1] == team else m1[1]
                        opp2 = m2[0] if m2[1] == team else m2[1]
                        if opp1 == opp2:
                            continue
                        # Check that each opponent is available and not blacked out.
                        if day_of_week not in team_availability.get(opp1, set()) or d in team_blackouts.get(opp1, set()):
                            continue
                        if day_of_week not in team_availability.get(opp2, set()) or d in team_blackouts.get(opp2, set()):
                            continue
                        # Ensure opponents are not already scheduled on d.
                        if team_game_days[opp1].get(d, 0) != 0 or team_game_days[opp2].get(d, 0) != 0:
                            continue
                        # Check weekly limits: team gets +2, opponents get +1 each.
                        if team_stats[team]['weekly_games'][week_num] + 2 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp1]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp2]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue

                        # Determine home/away for each game.
                        home1, away1 = decide_home_away(team, opp1, team_stats)
                        home2, away2 = decide_home_away(team, opp2, team_stats)
                        entry1 = free_fields_slot1[0]
                        entry2 = free_fields_slot2[0]
                        date1, slot1_str, field1 = entry1
                        date2, slot2_str, field2 = entry2

                        # Schedule the two games.
                        unscheduled.remove(m1)
                        unscheduled.remove(m2)
                        team_stats[home1]['home_games'] += 1
                        team_stats[away1]['away_games'] += 1
                        team_stats[home2]['home_games'] += 1
                        team_stats[away2]['away_games'] += 1

                        game1 = (date1, slot1_str, field1, home1, home1[0], away1, away1[0])
                        game2 = (date2, slot2_str, field2, home2, home2[0], away2, away2[0])
                        schedule.append(game1)
                        schedule.append(game2)

                        # Update stats for team and opponents.
                        for t in [team, opp1]:
                            team_stats[t]['total_games'] += 1
                            team_stats[t]['weekly_games'][week_num] += 1
                            team_game_days[t][d] += 1
                            team_game_slots[t][d].append(slot1_str)
                        for t in [team, opp2]:
                            team_stats[t]['total_games'] += 1
                            team_stats[t]['weekly_games'][week_num] += 1
                            team_game_days[t][d] += 1
                            team_game_slots[t][d].append(slot2_str)
                        doubleheader_count[team] += 1
                        team_doubleheader_opponents[team][d].update([opp1, opp2])
                        used_slots[(date1, slot1_str, field1)] = True
                        used_slots[(date2, slot2_str, field2)] = True
                        found = True
                        break
                    if found:
                        break

            # Case 2: Already one game scheduled today – try to add a game to form a doubleheader.
            elif games_today == 1:
                current_slot = team_game_slots[team][d][0]
                try:
                    idx = slots.index(current_slot)
                except ValueError:
                    continue
                if idx + 1 >= len(slots):
                    continue
                next_slot = slots[idx + 1]
                free_fields_next = [entry for entry in field_availability 
                                     if entry[0].date() == d and entry[1] == next_slot and ((entry[0], next_slot, entry[2]) not in used_slots)]
                if not free_fields_next:
                    continue

                already_opponent = None
                for game in schedule:
                    if game[0].date() == d and (game[3] == team or game[5] == team):
                        already_opponent = game[5] if game[3] == team else game[3]
                        break
                if already_opponent is None:
                    continue

                candidate_matchups = [m for m in unscheduled if team in m]
                if not candidate_matchups:
                    continue

                found = False
                for m in candidate_matchups:
                    opp = m[0] if m[1] == team else m[1]
                    if opp == already_opponent:
                        continue
                    if day_of_week not in team_availability.get(opp, set()) or d in team_blackouts.get(opp, set()):
                        continue
                    if team_game_days[opp].get(d, 0) != 0:
                        continue
                    if team_stats[team]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                        continue
                    if team_stats[opp]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                        continue

                    home, away = decide_home_away(team, opp, team_stats)
                    entry = free_fields_next[0]
                    date_entry, slot_str, field = entry
                    unscheduled.remove(m)
                    team_stats[home]['home_games'] += 1
                    team_stats[away]['away_games'] += 1
                    game = (date_entry, slot_str, field, home, home[0], away, away[0])
                    schedule.append(game)
                    for t in [team, opp]:
                        team_stats[t]['total_games'] += 1
                        team_stats[t]['weekly_games'][week_num] += 1
                        team_game_days[t][d] += 1
                        team_game_slots[t][d].append(slot_str)
                    doubleheader_count[team] += 1
                    team_doubleheader_opponents[team][d].add(opp)
                    used_slots[(date_entry, slot_str, field)] = True
                    found = True
                    break
                if found:
                    continue

    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, unscheduled

# -------------------------------
# NEW: Dedicated Doubleheader Optimization Pass (Two-Phase)
# -------------------------------
def force_minimum_doubleheaders(unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
                                team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents,
                                used_slots, schedule):
    # Phase 1: Ensure each team gets at least one doubleheader.
    teams = list(team_stats.keys())
    random.shuffle(teams)
    for team in teams:
        if doubleheader_count[team] >= 1:
            continue
        scheduled_for_team = False
        for d in sorted(timeslots_by_date.keys()):
            day_of_week = d.strftime('%a')
            if d in team_blackouts.get(team, set()) or day_of_week not in team_availability.get(team, set()):
                continue
            week_num = d.isocalendar()[1]
            games_today = team_game_days[team].get(d, 0)
            sorted_slots = timeslots_by_date[d]
            if games_today == 1:
                try:
                    idx = sorted_slots.index(team_game_slots[team][d][0])
                except ValueError:
                    continue
                if idx+1 >= len(sorted_slots):
                    continue
                next_slot = sorted_slots[idx+1]
                free_fields = [entry for entry in field_availability 
                               if entry[0].date() == d and entry[1] == next_slot and ((entry[0], next_slot, entry[2]) not in used_slots)]
                if not free_fields:
                    continue
                # Identify already scheduled opponent.
                already_opponent = None
                for game in schedule:
                    if game[0].date() == d and (game[3]==team or game[5]==team):
                        already_opponent = game[5] if game[3]==team else game[3]
                        break
                if already_opponent is None:
                    continue
                candidate_matchups = [m for m in unscheduled if team in m and ((m[0] if m[1]==team else m[1]) != already_opponent)]
                if not candidate_matchups:
                    continue
                for m in candidate_matchups:
                    opp = m[0] if m[1]==team else m[1]
                    if day_of_week not in team_availability.get(opp, set()) or d in team_blackouts.get(opp, set()):
                        continue
                    if team_game_days[opp].get(d, 0) != 0:
                        continue
                    if team_stats[team]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                        continue
                    if team_stats[opp]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                        continue
                    home, away = decide_home_away(team, opp, team_stats)
                    entry = free_fields[0]
                    date_entry, slot_str, field = entry
                    unscheduled.remove(m)
                    team_stats[home]['home_games'] += 1
                    team_stats[away]['away_games'] += 1
                    game = (date_entry, slot_str, field, home, home[0], away, away[0])
                    schedule.append(game)
                    for t in [team, opp]:
                        team_stats[t]['total_games'] += 1
                        team_stats[t]['weekly_games'][week_num] += 1
                        team_game_days[t][d] += 1
                        team_game_slots[t][d].append(slot_str)
                    doubleheader_count[team] += 1
                    team_doubleheader_opponents[team][d].add(opp)
                    used_slots[(date_entry, slot_str, field)] = True
                    scheduled_for_team = True
                    break
                if scheduled_for_team:
                    break
            elif games_today == 0:
                if len(sorted_slots) < 2:
                    continue
                for i in range(len(sorted_slots)-1):
                    slot1 = sorted_slots[i]
                    slot2 = sorted_slots[i+1]
                    free_fields1 = [entry for entry in field_availability if entry[0].date() == d and entry[1]==slot1 and ((entry[0], slot1, entry[2]) not in used_slots)]
                    free_fields2 = [entry for entry in field_availability if entry[0].date() == d and entry[1]==slot2 and ((entry[0], slot2, entry[2]) not in used_slots)]
                    if not free_fields1 or not free_fields2:
                        continue
                    candidate_matchups = [m for m in unscheduled if team in m]
                    if len(candidate_matchups) < 2:
                        continue
                    for combo in itertools.combinations(candidate_matchups, 2):
                        m1, m2 = combo
                        opp1 = m1[0] if m1[1]==team else m1[1]
                        opp2 = m2[0] if m2[1]==team else m2[1]
                        if opp1 == opp2:
                            continue
                        if day_of_week not in team_availability.get(opp1, set()) or d in team_blackouts.get(opp1, set()):
                            continue
                        if day_of_week not in team_availability.get(opp2, set()) or d in team_blackouts.get(opp2, set()):
                            continue
                        if team_stats[team]['weekly_games'][week_num] + 2 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp1]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp2]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue
                        home1, away1 = decide_home_away(team, opp1, team_stats)
                        home2, away2 = decide_home_away(team, opp2, team_stats)
                        entry1 = free_fields1[0]
                        entry2 = free_fields2[0]
                        date1, slot1_str, field1 = entry1
                        date2, slot2_str, field2 = entry2
                        unscheduled.remove(m1)
                        unscheduled.remove(m2)
                        team_stats[home1]['home_games'] += 1
                        team_stats[away1]['away_games'] += 1
                        team_stats[home2]['home_games'] += 1
                        team_stats[away2]['away_games'] += 1
                        game1 = (date1, slot1_str, field1, home1, home1[0], away1, away1[0])
                        game2 = (date2, slot2_str, field2, home2, home2[0], away2, away2[0])
                        schedule.append(game1)
                        schedule.append(game2)
                        for t in [team, opp1]:
                            team_stats[t]['total_games'] += 1
                            team_stats[t]['weekly_games'][week_num] += 1
                            team_game_days[t][d] += 1
                            team_game_slots[t][d].append(slot1_str)
                        for t in [team, opp2]:
                            team_stats[t]['total_games'] += 1
                            team_stats[t]['weekly_games'][week_num] += 1
                            team_game_days[t][d] += 1
                            team_game_slots[t][d].append(slot2_str)
                        doubleheader_count[team] += 1
                        team_doubleheader_opponents[team][d].update([opp1, opp2])
                        used_slots[(date1, slot1_str, field1)] = True
                        used_slots[(date2, slot2_str, field2)] = True
                        scheduled_for_team = True
                        break
                    if scheduled_for_team:
                        break
            # End of phase 1 for this team.
        # End Phase 1 loop.

    # Phase 2: For teams still below MIN_DOUBLE_HEADERS, try to add extra sessions.
    teams = list(team_stats.keys())
    random.shuffle(teams)
    for team in teams:
        while doubleheader_count[team] < MIN_DOUBLE_HEADERS:
            scheduled_for_team = False
            for d in sorted(timeslots_by_date.keys()):
                day_of_week = d.strftime('%a')
                if d in team_blackouts.get(team, set()) or day_of_week not in team_availability.get(team, set()):
                    continue
                week_num = d.isocalendar()[1]
                games_today = team_game_days[team].get(d, 0)
                sorted_slots = timeslots_by_date[d]
                if games_today == 1:
                    try:
                        idx = sorted_slots.index(team_game_slots[team][d][0])
                    except ValueError:
                        continue
                    if idx+1 >= len(sorted_slots):
                        continue
                    next_slot = sorted_slots[idx+1]
                    free_fields = [entry for entry in field_availability 
                                   if entry[0].date() == d and entry[1]==next_slot and ((entry[0], next_slot, entry[2]) not in used_slots)]
                    if not free_fields:
                        continue
                    already_opponent = None
                    for game in schedule:
                        if game[0].date() == d and (game[3]==team or game[5]==team):
                            already_opponent = game[5] if game[3]==team else game[3]
                            break
                    if already_opponent is None:
                        continue
                    candidate_matchups = [m for m in unscheduled if team in m and ((m[0] if m[1]==team else m[1]) != already_opponent)]
                    if not candidate_matchups:
                        continue
                    for m in candidate_matchups:
                        opp = m[0] if m[1]==team else m[1]
                        if day_of_week not in team_availability.get(opp, set()) or d in team_blackouts.get(opp, set()):
                            continue
                        if team_game_days[opp].get(d, 0) != 0:
                            continue
                        if team_stats[team]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue
                        home, away = decide_home_away(team, opp, team_stats)
                        entry = free_fields[0]
                        date_entry, slot_str, field = entry
                        unscheduled.remove(m)
                        team_stats[home]['home_games'] += 1
                        team_stats[away]['away_games'] += 1
                        game = (date_entry, slot_str, field, home, home[0], away, away[0])
                        schedule.append(game)
                        for t in [team, opp]:
                            team_stats[t]['total_games'] += 1
                            team_stats[t]['weekly_games'][week_num] += 1
                            team_game_days[t][d] += 1
                            team_game_slots[t][d].append(slot_str)
                        doubleheader_count[team] += 1
                        team_doubleheader_opponents[team][d].add(opp)
                        used_slots[(date_entry, slot_str, field)] = True
                        scheduled_for_team = True
                        break
                    if scheduled_for_team:
                        break
                elif games_today == 0:
                    if len(sorted_slots) < 2:
                        continue
                    for i in range(len(sorted_slots)-1):
                        slot1 = sorted_slots[i]
                        slot2 = sorted_slots[i+1]
                        free_fields1 = [entry for entry in field_availability if entry[0].date() == d and entry[1]==slot1 and ((entry[0], slot1, entry[2]) not in used_slots)]
                        free_fields2 = [entry for entry in field_availability if entry[0].date() == d and entry[1]==slot2 and ((entry[0], slot2, entry[2]) not in used_slots)]
                        if not free_fields1 or not free_fields2:
                            continue
                        candidate_matchups = [m for m in unscheduled if team in m]
                        if len(candidate_matchups) < 2:
                            continue
                        for combo in itertools.combinations(candidate_matchups, 2):
                            m1, m2 = combo
                            opp1 = m1[0] if m1[1]==team else m1[1]
                            opp2 = m2[0] if m2[1]==team else m2[1]
                            if opp1 == opp2:
                                continue
                            if day_of_week not in team_availability.get(opp1, set()) or d in team_blackouts.get(opp1, set()):
                                continue
                            if day_of_week not in team_availability.get(opp2, set()) or d in team_blackouts.get(opp2, set()):
                                continue
                            if team_stats[team]['weekly_games'][week_num] + 2 > WEEKLY_GAME_LIMIT:
                                continue
                            if team_stats[opp1]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                                continue
                            if team_stats[opp2]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                                continue
                            home1, away1 = decide_home_away(team, opp1, team_stats)
                            home2, away2 = decide_home_away(team, opp2, team_stats)
                            entry1 = free_fields1[0]
                            entry2 = free_fields2[0]
                            date1, slot1_str, field1 = entry1
                            date2, slot2_str, field2 = entry2
                            unscheduled.remove(m1)
                            unscheduled.remove(m2)
                            team_stats[home1]['home_games'] += 1
                            team_stats[away1]['away_games'] += 1
                            team_stats[home2]['home_games'] += 1
                            team_stats[away2]['away_games'] += 1
                            game1 = (date1, slot1_str, field1, home1, home1[0], away1, away1[0])
                            game2 = (date2, slot2_str, field2, home2, home2[0], away2, away2[0])
                            schedule.append(game1)
                            schedule.append(game2)
                            for t in [team, opp1]:
                                team_stats[t]['total_games'] += 1
                                team_stats[t]['weekly_games'][week_num] += 1
                                team_game_days[t][d] += 1
                                team_game_slots[t][d].append(slot1_str)
                            for t in [team, opp2]:
                                team_stats[t]['total_games'] += 1
                                team_stats[t]['weekly_games'][week_num] += 1
                                team_game_days[t][d] += 1
                                team_game_slots[t][d].append(slot2_str)
                            doubleheader_count[team] += 1
                            team_doubleheader_opponents[team][d].update([opp1, opp2])
                            used_slots[(date1, slot1_str, field1)] = True
                            used_slots[(date2, slot2_str, field2)] = True
                            scheduled_for_team = True
                            break
                        if scheduled_for_team:
                            break
                if scheduled_for_team:
                    break
            if not scheduled_for_team:
                break
    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, unscheduled

# -------------------------------
# Modified Scheduling functions (now accept existing state)
# -------------------------------
def schedule_games(matchups, team_availability, field_availability, team_blackouts,
                   schedule=None, team_stats=None, doubleheader_count=None,
                   team_game_days=None, team_game_slots=None, team_doubleheader_opponents=None,
                   used_slots=None, timeslots_by_date=None):
    # Initialize state if not provided.
    if schedule is None:
        schedule = []
    if team_stats is None:
        team_stats = defaultdict(lambda: {
            'total_games': 0,
            'home_games': 0,
            'away_games': 0,
            'weekly_games': defaultdict(int)
        })
    if doubleheader_count is None:
        doubleheader_count = defaultdict(int)
    if team_game_days is None:
        team_game_days = defaultdict(lambda: defaultdict(int))
    if team_game_slots is None:
        team_game_slots = defaultdict(lambda: defaultdict(list))
    if team_doubleheader_opponents is None:
        team_doubleheader_opponents = defaultdict(lambda: defaultdict(set))
    if used_slots is None:
        used_slots = {}
    if timeslots_by_date is None:
        timeslots_by_date = defaultdict(list)
        for date, slot, field in field_availability:
            d = date.date()
            if slot not in timeslots_by_date[d]:
                timeslots_by_date[d].append(slot)
        for d in timeslots_by_date:
            timeslots_by_date[d].sort(key=lambda s: datetime.strptime(s.strip(), "%I:%M %p"))

    unscheduled = matchups[:]  # work on a local copy
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
                if team_stats[home]['total_games'] >= MAX_GAMES or team_stats[away]['total_games'] >= MAX_GAMES:
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
                        if idx+1 >= len(sorted_slots):
                            valid_slot = False
                            break
                        required_slot = sorted_slots[idx+1]
                        if slot != required_slot:
                            valid_slot = False
                            break
                if not valid_slot:
                    continue
                # Enforce doubleheader limits and ensure different opponents if this becomes a doubleheader.
                can_double = True
                for team, opp in ((home, away), (away, home)):
                    if team_game_days[team][d] == 1:
                        if doubleheader_count[team] >= MAX_DOUBLE_HEADERS:
                            can_double = False
                            break
                        if team_doubleheader_opponents[team][d] and opp in team_doubleheader_opponents[team][d]:
                            can_double = False
                            break
                if not can_double:
                    continue
                # Home/Away balancing.
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
        if not progress_made:
            retry_count += 1
        else:
            retry_count = 0
    if unscheduled:
        print("Warning: Retry limit reached in primary scheduling. Some predetermined matchups could not be scheduled.")
    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled

def fill_missing_games(schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
                       team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled,
                       team_availability, team_blackouts, field_availability):
    retry_count = 0
    while any(stats['total_games'] < MAX_GAMES for stats in team_stats.values()) and retry_count < MAX_RETRIES:
        progress = False
        for date, slot, field in field_availability:
            if used_slots.get((date, slot, field), False):
                continue
            d = date.date()
            day_of_week = date.strftime('%a')
            week_num = date.isocalendar()[1]
            for matchup in unscheduled[:]:
                home, away = matchup
                if team_stats[home]['total_games'] >= MAX_GAMES and team_stats[away]['total_games'] >= MAX_GAMES:
                    continue
                if day_of_week not in team_availability.get(home, set()) or day_of_week not in team_availability.get(away, set()):
                    continue
                if d in team_blackouts.get(home, set()) or d in team_blackouts.get(away, set()):
                    continue
                if not (min_gap_ok(home, d, team_game_days) and min_gap_ok(away, d, team_game_days)):
                    continue
                if slot in team_game_slots[home][d] or slot in team_game_slots[away][d]:
                    continue
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
                        if idx+1 >= len(sorted_slots):
                            valid_slot = False
                            break
                        required_slot = sorted_slots[idx+1]
                        if slot != required_slot:
                            valid_slot = False
                            break
                if not valid_slot:
                    continue
                can_double = True
                for team, opp in ((home, away), (away, home)):
                    if team_game_days[team][d] == 1:
                        if doubleheader_count[team] >= MAX_DOUBLE_HEADERS:
                            can_double = False
                            break
                        if team_doubleheader_opponents[team][d] and opp in team_doubleheader_opponents[team][d]:
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
        if not progress:
            retry_count += 1
        else:
            retry_count = 0
    return schedule, team_stats, doubleheader_count, unscheduled

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
    table.field_names = ["Division", "Team", "Total Games", "Home Games", "Away Games"]
    for team, stats in sorted(team_stats.items()):
        table.add_row([team[0], team, stats['total_games'], stats['home_games'], stats['away_games']])
    print("\nSchedule Summary:")
    print(table)

def print_doubleheader_summary(doubleheader_count):
    table = PrettyTable()
    table.field_names = ["Team", "Doubleheader Days"]
    for team, count in sorted(doubleheader_count.items()):
        table.add_row([team, count])
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
# Main function
# -------------------------------
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    team_blackouts = load_team_blackouts('team_blackouts.csv')
    
    print("\nTeam Availability Debug:")
    for team, days in team_availability.items():
        print(f"Team {team}: {', '.join(days)}")
    if not team_availability:
        print("ERROR: Team availability is empty!")
    
    print("\nField Availability Debug:")
    for entry in field_availability:
        print(f"Field Slot: {entry}")
    if not field_availability:
        print("ERROR: Field availability is empty!")
    
    print("\nTeam Blackouts Debug:")
    for team, dates in team_blackouts.items():
        print(f"Team {team} Blackouts: {', '.join(str(d) for d in dates)}")
        
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    
    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")
    
    # Initialize state used by scheduling functions.
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
    
    unscheduled = matchups[:]  # Copy of all matchups
    
    # Preemptively schedule doubleheaders for teams under the minimum requirement.
    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     team_doubleheader_opponents, used_slots, unscheduled) = schedule_doubleheaders_preemptively(
        unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
        team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots
    )
    
    # Dedicated doubleheader optimization pass (Two-phase).
    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     team_doubleheader_opponents, used_slots, unscheduled) = force_minimum_doubleheaders(
        unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
        team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, schedule
    )
    
    # Primary scheduling pass for the remaining (mostly single) games.
    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled) = schedule_games(
        unscheduled, team_availability, field_availability, team_blackouts,
        schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
        team_doubleheader_opponents, used_slots, timeslots_by_date
    )
    
    # Fill missing games if some teams haven't reached MAX_GAMES.
    if any(stats['total_games'] < MAX_GAMES for stats in team_stats.values()):
        print("Filling missing games...")
        (schedule, team_stats, doubleheader_count, unscheduled) = fill_missing_games(
            schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
            team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled,
            team_availability, team_blackouts, field_availability
        )
    
    # Final checks.
    missing = [team for team, stats in team_stats.items() if stats['total_games'] < MAX_GAMES]
    if missing:
        print("Critical: The following teams did not reach the required {} games: {}".format(MAX_GAMES, missing))
    under_dh = [team for team, count in doubleheader_count.items() if count < MIN_DOUBLE_HEADERS]
    if under_dh:
        print("Critical: The following teams did not meet the minimum doubleheader sessions ({} required): {}".format(MIN_DOUBLE_HEADERS, under_dh))
    
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    print_doubleheader_summary(doubleheader_count)
    generate_matchup_table(schedule, division_teams)

if __name__ == "__main__":
    main()
