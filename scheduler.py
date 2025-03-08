import csv
import itertools
import random
from datetime import datetime, timedelta
from collections import defaultdict
from prettytable import PrettyTable

# Configurable parameters
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11
WEEKLY_GAME_LIMIT = 2  # max games per team per week
MAX_RETRIES = 10000    # scheduling backtracking limit

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

def load_team_blackouts(file_path):
    """
    Loads blackout dates from a CSV file.
    Expected format: Team, Date1, Date2, Date3, ...
    Dates must be in YYYY-MM-DD format.
    Returns a dict mapping team to a set of datetime.date objects.
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

def load_doubleheader_dates(file_path):
    """
    Loads doubleheader dates from a CSV file.
    Expected format: Date (YYYY-MM-DD) per row (after a header).
    Returns a set of datetime.date objects.
    """
    doubleheaders = set()
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            d = row[0].strip()
            if d:
                try:
                    dt = datetime.strptime(d, '%Y-%m-%d').date()
                    doubleheaders.add(dt)
                except Exception as e:
                    print(f"Error parsing doubleheader date '{d}': {e}")
    return doubleheaders

# -------------------------------
# Intra-division matchup generation
# -------------------------------
def assign_intra_division_weights(teams, two_game_count, three_game_count):
    """
    For a given list of teams, assign each pairing (edge) a weight (2 or 3) such that
    each team ends up with exactly 'two_game_count' edges of weight 2 and the remaining
    edges (i.e. len(teams)-1 - two_game_count) get weight 3.
    """
    pairs = list(itertools.combinations(sorted(teams), 2))
    count2 = {team: 0 for team in teams}
    assignment = {}
    
    def backtrack(i):
        if i == len(pairs):
            if all(count2[team] == two_game_count for team in teams):
                return True
            else:
                return False
        team1, team2 = pairs[i]
        # Option 1: assign weight 2 if both teams need it.
        if count2[team1] < two_game_count and count2[team2] < two_game_count:
            assignment[(team1, team2)] = 2
            count2[team1] += 1
            count2[team2] += 1
            if backtrack(i + 1):
                return True
            count2[team1] -= 1
            count2[team2] -= 1
            del assignment[(team1, team2)]
        # Option 2: assign weight 3.
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
    """
    Build matchup list from weight assignment.
    For weight==2: add one game at each team's home.
    For weight==3: add two balanced games plus one extra game (randomly home/away).
    """
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
    """
    For divisions A and C, split the 7 opponents such that:
      - 3 opponents are played 2 times (home & away)
      - 4 opponents are played 3 times (with one extra game decided randomly)
    For division B, every opponent is played 2 times.
    """
    if division == 'B':
        matchups = []
        for team1, team2 in itertools.combinations(sorted(teams), 2):
            matchups.append((team1, team2))
            matchups.append((team2, team1))
        return matchups
    elif division in ['A', 'C']:
        two_game_count = 3
        three_game_count = (len(teams) - 1) - two_game_count  # 7-3 = 4
        weight_assignment = assign_intra_division_weights(teams, two_game_count, three_game_count)
        return generate_intra_matchups(teams, weight_assignment)
    else:
        raise Exception("Unknown division")

# -------------------------------
# Inter-division matchup generation
# -------------------------------
def generate_bipartite_regular_matchups(teams1, teams2, degree):
    """
    Given two lists of teams (assumed equal size), generate a bipartite graph in which
    each team in teams1 and teams2 appears exactly 'degree' times.
    """
    teams1_order = teams1[:]
    random.shuffle(teams1_order)
    assignment = {team: [] for team in teams1_order}
    capacity = {team: degree for team in teams2}
    
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
    
    if backtrack(0):
        edges = []
        for team in teams1_order:
            for opp in assignment[team]:
                edges.append((team, opp))
        return edges
    else:
        raise Exception("No valid bipartite regular matching found.")

def generate_inter_division_matchups(division_from, division_to, teams_from, teams_to):
    """
    Generate inter-division matchups between teams_from and teams_to as a bipartite regular graph.
    Each team in teams_from will have 4 inter games against teams_to and vice versa.
    Then randomly assign home/away.
    """
    degree = 4
    edges = generate_bipartite_regular_matchups(teams_from, teams_to, degree)
    matchups = []
    for (t1, t2) in edges:
        if random.random() < 0.5:
            matchups.append((t1, t2))
        else:
            matchups.append((t2, t1))
    return matchups

# -------------------------------
# Combine full matchup list
# -------------------------------
def generate_full_matchups(division_teams):
    full_matchups = []
    
    # Intra-division games:
    for div, teams in division_teams.items():
        intra = generate_intra_division_matchups(div, teams)
        full_matchups.extend(intra)
    
    # Inter-division games:
    # A and C do NOT play.
    # A vs B:
    inter_AB = generate_inter_division_matchups('A', 'B', division_teams['A'], division_teams['B'])
    full_matchups.extend(inter_AB)
    # B vs C:
    inter_BC = generate_inter_division_matchups('B', 'C', division_teams['B'], division_teams['C'])
    full_matchups.extend(inter_BC)
    
    random.shuffle(full_matchups)
    return full_matchups

# -------------------------------
# Home/Away Decision Helper
# -------------------------------
def decide_home_away(team1, team2, team_stats):
    """
    Decide which team should be home based on current home game counts.
    If one team is at the home limit (HOME_AWAY_BALANCE) and the other is not, 
    the latter becomes home. Otherwise, choose the team with fewer home games.
    If equal, randomize the assignment.
    """
    # If one team is already at the limit, force the other as home (if possible)
    if team_stats[team1]['home_games'] >= HOME_AWAY_BALANCE and team_stats[team2]['home_games'] < HOME_AWAY_BALANCE:
        return team2, team1
    if team_stats[team2]['home_games'] >= HOME_AWAY_BALANCE and team_stats[team1]['home_games'] < HOME_AWAY_BALANCE:
        return team1, team2
    # Otherwise, choose the team with fewer home games
    if team_stats[team1]['home_games'] < team_stats[team2]['home_games']:
        return team1, team2
    elif team_stats[team2]['home_games'] < team_stats[team1]['home_games']:
        return team2, team1
    else:
        # If equal, randomize the decision
        if random.random() < 0.5:
            return team1, team2
        else:
            return team2, team1

# -------------------------------
# Scheduling functions
# -------------------------------
def schedule_games(matchups, team_availability, field_availability, team_blackouts, doubleheader_dates):
    schedule = []
    team_stats = defaultdict(lambda: {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'weekly_games': defaultdict(int)
    })
    # Tracks which teams have a game on a given date and their opponent(s)
    team_games_by_date = defaultdict(lambda: defaultdict(list))
    
    scheduled_slots = defaultdict(set)  # key: (date, slot) -> set of teams
    unscheduled_matchups = matchups[:]
    retry_count = 0

    while unscheduled_matchups and retry_count < MAX_RETRIES:
        progress_made = False
        for date, slot, field in field_availability:
            day_of_week = date.strftime('%a')
            week_num = date.isocalendar()[1]
            game_date = date.date()
            for matchup in unscheduled_matchups[:]:
                # Unpack the matchup (order from generation is now considered unordered)
                t1, t2 = matchup
                # Check team day-of-week availability.
                if day_of_week not in team_availability.get(t1, set()) or day_of_week not in team_availability.get(t2, set()):
                    continue
                # Check team blackout dates.
                if game_date in team_blackouts.get(t1, set()) or game_date in team_blackouts.get(t2, set()):
                    continue
                # Check if teams are already scheduled in this specific slot.
                if t1 in scheduled_slots[(date, slot)] or t2 in scheduled_slots[(date, slot)]:
                    continue
                # Check overall game count and weekly limits.
                if team_stats[t1]['total_games'] >= MAX_GAMES or team_stats[t2]['total_games'] >= MAX_GAMES:
                    continue
                if (team_stats[t1]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or
                    team_stats[t2]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT):
                    continue
                
                # Decide on home/away based on current counts.
                home, away = decide_home_away(t1, t2, team_stats)
                
                # Enforce per-day limits with doubleheader consideration.
                if game_date in doubleheader_dates:
                    if len(team_games_by_date[home][game_date]) >= 2 or len(team_games_by_date[away][game_date]) >= 2:
                        continue
                    if len(team_games_by_date[home][game_date]) == 1:
                        if team_games_by_date[home][game_date][0] == away:
                            continue
                    if len(team_games_by_date[away][game_date]) == 1:
                        if team_games_by_date[away][game_date][0] == home:
                            continue
                else:
                    # Non-doubleheader day: allow only one game per team.
                    if len(team_games_by_date[home][game_date]) >= 1 or len(team_games_by_date[away][game_date]) >= 1:
                        continue

                # All constraints passed -- schedule the game.
                schedule.append((date, slot, field, home, home[0], away, away[0]))
                team_stats[home]['total_games'] += 1
                team_stats[home]['home_games'] += 1
                team_stats[away]['total_games'] += 1
                team_stats[away]['away_games'] += 1
                team_stats[home]['weekly_games'][week_num] += 1
                team_stats[away]['weekly_games'][week_num] += 1
                scheduled_slots[(date, slot)].update([home, away])
                # Record opponent for the day.
                team_games_by_date[home][game_date].append(away)
                team_games_by_date[away][game_date].append(home)
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
    team_blackouts = load_team_blackouts('team_blackouts.csv')
    doubleheader_dates = load_doubleheader_dates('doubleheaders.csv')
    
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
        
    print("\nDoubleheader Dates Debug:")
    for d in sorted(doubleheader_dates):
        print(d)
    
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    
    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")
    
    schedule, team_stats = schedule_games(matchups, team_availability, field_availability, team_blackouts, doubleheader_dates)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    generate_matchup_table(schedule, division_teams)

if __name__ == "__main__":
    main()
