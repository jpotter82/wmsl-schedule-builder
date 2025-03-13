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
MIN_DOUBLE_HEADERS = 5     # minimum number of doubleheader sessions per team (each session = 2 games)
MAX_DOUBLE_HEADERS = 5     # maximum allowed doubleheader days per team

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
                # Find the already scheduled game for the team on day d.
                current_slot = team_game_slots[team][d][0]
                try:
                    idx = slots.index(current_slot)
                except ValueError:
                    continue
                if idx + 1 >= len(slots):
                    continue
                next_slot = slots[idx + 1]
                free_fields_next = [entry for entry in field_availability 
                                     if entry
