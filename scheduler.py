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
MAX_RETRIES = 10000        # scheduling backtracking limit
MIN_GAP = 2                # minimum days between game dates
MIN_DOUBLE_HEADERS = 5     # minimum number of doubleheader sessions per team (each session = 2 games)

# Inter–division scheduling constraints:
# In Division A, only teams A5-A8 play inter–division games (vs. B)
# In Division C, only teams C1-C4 play inter–division games (vs. B)
INTER_DIVISION_GAMES_A = 4  # each eligible A team (A5-A8) plays 4 inter–division games
INTER_DIVISION_GAMES_C = 4  # each eligible C team (C1-C4) plays 4 inter–division games

# For balance:
# For A vs B: total games = 4 * 4 = 16, so each B team gets 16/8 = 2 games.
# For B vs C: total games = 4 * 4 = 16, so each B team gets 16/8 = 2 games.

# -------------------------------
# Helper Functions
# -------------------------------
def min_gap_ok(team, d, team_game_days):
    """
    Check if scheduling a game for team on date 'd' is allowed given the MIN_GAP constraint.
    Allows a game on the same day if it creates a doubleheader (but no more than 2 games per day).
    """
    # Allow if already a game on d but not more than 1 (i.e. one game already is fine – a second game makes a session)
    if team_game_days[team].get(d, 0) >= 2:
        return False
    # Enforce the gap constraint for other dates.
    for gd in team_game_days[team]:
        if gd != d and abs((d - gd).days) < MIN_GAP:
            return False
    return True

def is_legal(matchup):
    """
    Returns True if the matchup is legal.
    Illegal: pairing an A–team with a C–team.
    Assumes team names begin with their division letter.
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
            slot = row[1].strip()
            field = row[2].strip()
            field_availability.append((date, slot, field))
    field_availability.sort(key=lambda x: x[0])
    return field_availability

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
            if backtrack(i + 1):
                return True
            count2[team1] -= 1
            count2[team2] -= 1
            del assignment[(team1, team2)]
        assignment[(team1, team2)] = 3
        if backtrack(i + 1):
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
        three_game_count = (len(teams) - 1) - two_game_count  # For 8 teams: 7-3 = 4
        weight_assignment = assign_intra_division_weights(teams, two_game_count, three_game_count)
        return generate_intra_matchups(teams, weight_assignment)
    else:
        raise Exception("Unknown division")

# -------------------------------
# Inter-division matchup generation with new constraints
# -------------------------------
def generate_bipartite_matchups(teams_from, teams_to, degree_from, degree_to):
    """
    Generates a bipartite matchup where each team in teams_from gets exactly degree_from games
    and each team in teams_to gets exactly degree_to games.
    Necessary condition: len(teams_from)*degree_from == len(teams_to)*degree_to.
    """
    total_games = len(teams_from) * degree_from
    if total_games != len(teams_to) * degree_to:
        raise Exception(f"Degree mismatch: {len(teams_from)}*{degree_from} != {len(teams_to)}*{degree_to}")
    assignment = {t: [] for t in teams_from}
    capacity = {t: degree_to for t in teams_to}
    teams_from_order = teams_from[:]
    random.shuffle(teams_from_order)
    
    def backtrack(i):
        if i == len(teams_from_order):
            return True
        team = teams_from_order[i]
        available = [t for t in teams_to if capacity[t] > 0]
        for combo in itertools.combinations(available, degree_from):
            assignment[team] = list(combo)
            for t in combo:
                capacity[t] -= 1
            if backtrack(i + 1):
                return True
            for t in combo:
                capacity[t] += 1
        return False
    
    if backtrack(0):
        edges = []
        for team in teams_from:
            for opp in assignment[team]:
                edges.append((team, opp))
        return edges
    else:
        raise Exception("No valid bipartite matching found.")

def generate_inter_division_matchups_custom(teams_from, teams_to, degree_from, degree_to):
    """
    Generates inter-division matchups with custom degree constraints and randomizes home/away.
    """
    edges = generate_bipartite_matchups(teams_from, teams_to, degree_from, degree_to)
    matchups = []
    for (t1, t2) in edges:
        # Randomly decide home/away for each game.
        if random.random() < 0.5:
            matchups.append((t1, t2))
        else:
            matchups.append((t2, t1))
    return matchups

def generate_full_matchups(division_teams):
    full_matchups = []
    
    # Intra-division games:
    for div, teams in division_teams.items():
        full_matchups.extend(generate_intra_division_matchups(div, teams))
    
    # Inter-division games with new constraints:
    # Division A: Only A5-A8 play inter-division (vs. B)
    eligible_A = division_teams['A'][4:]  # A5-A8
    # For these 4 teams, each plays INTER_DIVISION_GAMES_A games.
    # Total games = 4 * INTER_DIVISION_GAMES_A. To balance, each B team gets:
    degree_B_from_A = (len(eligible_A) * INTER_DIVISION_GAMES_A) // len(division_teams['B'])
    inter_AB = generate_inter_division_matchups_custom(eligible_A, division_teams['B'],
                                                       INTER_DIVISION_GAMES_A, degree_B_from_A)
    full_matchups.extend(inter_AB)
    
    # Division C: Only C1-C4 play inter-division (vs. B)
    eligible_C = division_teams['C'][:4]  # C1-C4
    # For these 4 teams, each plays INTER_DIVISION_GAMES_C games.
    # To balance, each B team gets:
    degree_B_from_C = (len(eligible_C) * INTER_DIVISION_GAMES_C) // len(division_teams['B'])
    inter_BC = generate_inter_division_matchups_custom(division_teams['B'], eligible_C,
                                                       degree_B_from_C, INTER_DIVISION_GAMES_C)
    full_matchups.extend(inter_BC)
    
    random.shuffle(full_matchups)
    return full_matchups

# -------------------------------
# Home/Away Helper (optional)
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
# Scheduling functions (updated for doubleheaders)
# -------------------------------
def schedule_games(matchups, team_availability, field_availability):
    schedule = []
    team_stats = defaultdict(lambda: {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'weekly_games': defaultdict(int)
    })
    # Mark each slot as used.
    used_slots = {}
    # Record games per team per day (date -> count)
    team_game_days = defaultdict(lambda: defaultdict(int))
    # Count of doubleheader sessions per team.
    # Note: Each doubleheader session represents 2 games on the same day.
    doubleheader_count = defaultdict(int)
    
    unscheduled_matchups = matchups[:]
    retry_count = 0
    while unscheduled_matchups and retry_count < MAX_RETRIES:
        progress_made = False
        for date, slot, field in field_availability:
            if used_slots.get((date, slot, field), False):
                continue
            day_of_week = date.strftime('%a')
            week_num = date.isocalendar()[1]
            for matchup in unscheduled_matchups[:]:
                home, away = matchup
                # Check team availability.
                if day_of_week not in team_availability.get(home, set()) or day_of_week not in team_availability.get(away, set()):
                    continue
                if team_stats[home]['total_games'] >= MAX_GAMES or team_stats[away]['total_games'] >= MAX_GAMES:
                    continue
                if (team_stats[home]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or
                    team_stats[away]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT):
                    continue
                # Enforce the minimum gap (allowing doubleheaders).
                if not (min_gap_ok(home, date.date(), team_game_days) and min_gap_ok(away, date.date(), team_game_days)):
                    continue
                # Home/Away check (inline; could also use decide_home_away)
                if team_stats[home]['home_games'] >= HOME_AWAY_BALANCE:
                    if team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
                        home, away = away, home
                    else:
                        continue
                # Schedule the game.
                schedule.append((date, slot, field, home, home[0], away, away[0]))
                team_stats[home]['total_games'] += 1
                team_stats[home]['home_games'] += 1
                team_stats[away]['total_games'] += 1
                team_stats[away]['away_games'] += 1
                team_stats[home]['weekly_games'][week_num] += 1
                team_stats[away]['weekly_games'][week_num] += 1
                used_slots[(date, slot, field)] = True
                # Update game day counts and doubleheader count.
                for team in (home, away):
                    prev = team_game_days[team].get(date.date(), 0)
                    team_game_days[team][date.date()] = prev + 1
                    # If this scheduling makes it a doubleheader session (exactly 2 games on that day),
                    # count that as one session.
                    if prev == 1:
                        doubleheader_count[team] += 1
                unscheduled_matchups.remove(matchup)
                progress_made = True
                break
            if progress_made:
                break
        if not progress_made:
            retry_count += 1
        else:
            retry_count = 0
    if unscheduled_matchups:
        print("Warning: Retry limit reached. Some matchups could not be scheduled.")
    
    # Warn if any team did not reach the minimum doubleheader session count.
    # Note: Each session corresponds to 2 games on the same day.
    for team in team_stats:
        if doubleheader_count[team] < MIN_DOUBLE_HEADERS:
            required_games = MIN_DOUBLE_HEADERS * 2
            actual_games = doubleheader_count[team] * 2
            print(f"Warning: Team {team} has {actual_games} doubleheader games (i.e. {doubleheader_count[team]} sessions), "
                  f"less than the minimum required {required_games} games (i.e. {MIN_DOUBLE_HEADERS} sessions).")
    
    return schedule, team_stats

def output_schedule_to_csv(schedule, output_file):
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Diamond", "Home Team", "Home Division", "Away Team", "Away Division"])
        for game in schedule:
            date, slot, field, home, home_div, away, away_div = game
            writer.writerow([date.strftime('%Y-%m-%d'), slot, field, home, home_div, away, away_div])

def print_schedule_summary(team_stats):
    table = PrettyTable()
    table.field_names = ["Division", "Team", "Total Games", "Home Games", "Away Games"]
    for team, stats in sorted(team_stats.items()):
        division = team[0]
        table.add_row([division, team, stats['total_games'], stats['home_games'], stats['away_games']])
    print("\nSchedule Summary:")
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
    
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    
    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")
    
    schedule, team_stats = schedule_games(matchups, team_availability, field_availability)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    generate_matchup_table(schedule, division_teams)

if __name__ == "__main__":
    main()
