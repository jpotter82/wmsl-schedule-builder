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
MIN_GAP = 2            # minimum days between standard games

# -------------------------------
# Helper Functions
# -------------------------------
def min_gap_ok(team, d, team_game_days):
    """Return True if team has no game scheduled within MIN_GAP days before date d."""
    for gd in team_game_days[team]:
        if gd != d and (d - gd).days < MIN_GAP:
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
# Data Loading Functions
# -------------------------------
def load_team_availability(file_path):
    availability = {}
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        for row in reader:
            team = row[0].strip()
            days = row[1:]
            availability[team] = {day.strip() for day in days if day.strip()}
    return availability

def load_field_availability(file_path):
    field_availability = []
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        for row in reader:
            date = datetime.strptime(row[0].strip(), '%Y-%m-%d')
            slot = row[1].strip()
            field = row[2].strip()
            field_availability.append((date, slot, field))
    field_availability.sort(key=lambda x: x[0])
    return field_availability

def load_team_blackouts(file_path):
    blackouts = {}
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            team = row[0].strip()
            dlist = [d.strip() for d in row[1:] if d.strip()]
            dates = set()
            for d in dlist:
                try:
                    dates.add(datetime.strptime(d, '%Y-%m-%d').date())
                except Exception as e:
                    print(f"Error parsing blackout date '{d}' for {team}: {e}")
            blackouts[team] = dates
    return blackouts

def load_doubleheader_dates(file_path):
    dh = set()
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            d = row[0].strip()
            if d:
                try:
                    dh.add(datetime.strptime(d, '%Y-%m-%d').date())
                except Exception as e:
                    print(f"Error parsing doubleheader date '{d}': {e}")
    return dh

# -------------------------------
# Matchup Generation Functions
# -------------------------------
def assign_intra_division_weights(teams, two_game_count, three_game_count):
    pairs = list(itertools.combinations(sorted(teams), 2))
    count2 = {t: 0 for t in teams}
    assignment = {}
    
    def backtrack(i):
        if i == len(pairs):
            return all(count2[t] == two_game_count for t in teams)
        t1, t2 = pairs[i]
        if count2[t1] < two_game_count and count2[t2] < two_game_count:
            assignment[(t1, t2)] = 2
            count2[t1] += 1
            count2[t2] += 1
            if backtrack(i + 1):
                return True
            count2[t1] -= 1
            count2[t2] -= 1
            del assignment[(t1, t2)]
        assignment[(t1, t2)] = 3
        if backtrack(i + 1):
            return True
        del assignment[(t1, t2)]
        return False

    if backtrack(0):
        return assignment
    else:
        raise Exception("No valid intra-division assignment found.")

def generate_intra_matchups(teams, weight_assignment):
    matchups = []
    for (t1, t2), weight in weight_assignment.items():
        if weight == 2:
            matchups.append((t1, t2))
            matchups.append((t2, t1))
        elif weight == 3:
            matchups.append((t1, t2))
            matchups.append((t2, t1))
            if random.random() < 0.5:
                matchups.append((t1, t2))
            else:
                matchups.append((t2, t1))
    return matchups

def generate_intra_division_matchups(division, teams):
    if division == 'B':
        matchups = []
        for t1, t2 in itertools.combinations(sorted(teams), 2):
            matchups.append((t1, t2))
            matchups.append((t2, t1))
        return matchups
    elif division in ['A','C']:
        two_game_count = 3
        three_game_count = (len(teams) - 1) - two_game_count
        weight_assignment = assign_intra_division_weights(teams, two_game_count, three_game_count)
        return generate_intra_matchups(teams, weight_assignment)
    else:
        raise Exception("Unknown division")

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
        raise Exception("Bipartite matching failed.")

def generate_inter_division_matchups(division_from, division_to, teams_from, teams_to):
    degree = 4
    edges = generate_bipartite_regular_matchups(teams_from, teams_to, degree)
    matchups = []
    for (t1, t2) in edges:
        if random.random() < 0.5:
            matchups.append((t1, t2))
        else:
            matchups.append((t2, t1))
    return matchups

def generate_full_matchups(division_teams):
    full_matchups = []
    # Intra-division: use full division teams.
    for div, teams in division_teams.items():
        full_matchups.extend(generate_intra_division_matchups(div, teams))
    # Inter-division:
    # For A vs. B, only use A teams from A5-A8.
    teams_A_inter = [team for team in division_teams['A'] if team[1] in "5678"]
    inter_AB = generate_inter_division_matchups('A', 'B', teams_A_inter, division_teams['B'])
    full_matchups.extend(inter_AB)
    # For B vs. C, only use C teams from C1-C4.
    teams_C_inter = [team for team in division_teams['C'] if team[1] in "1234"]
    inter_BC = generate_inter_division_matchups('B', 'C', division_teams['B'], teams_C_inter)
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
# Standard (Single-Game) Scheduling (with Consecutive Slot Enforcement)
# -------------------------------
def schedule_standard_games(matchups, team_availability, field_availability, doubleheader_dates):
    schedule = []
    team_stats = defaultdict(lambda: {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'weekly_games': defaultdict(int)
    })
    # For standard scheduling, we now track for each team the indices of slots they have on each date.
    team_day_slots = defaultdict(lambda: defaultdict(list))
    used_slots = {}
    unscheduled = matchups[:]
    
    # Process only field slots on dates not in doubleheader_dates.
    standard_slots = [s for s in field_availability if s[0].date() not in doubleheader_dates]
    # Group slots by date.
    slots_by_date = defaultdict(list)
    for dt, slot, field in standard_slots:
        slots_by_date[dt.date()].append((dt, slot, field))
    # For each date, sort the slots (assume they are already in time order)
    for d in slots_by_date:
        slots_by_date[d].sort(key=lambda x: x[0])
    all_dates = sorted(slots_by_date.keys())
    retry_count = 0
    while unscheduled and retry_count < MAX_RETRIES:
        progress_made = False
        for d in all_dates:
            slots = slots_by_date[d]
            for idx, (dt, slot, field) in enumerate(slots):
                if used_slots.get((dt, slot, field), False):
                    continue
                day_str = dt.strftime('%a')
                week_num = dt.isocalendar()[1]
                for matchup in unscheduled[:]:
                    home, away = matchup
                    if day_str not in team_availability.get(home, set()) or day_str not in team_availability.get(away, set()):
                        continue
                    if team_stats[home]['total_games'] >= MAX_GAMES or team_stats[away]['total_games'] >= MAX_GAMES:
                        continue
                    if (team_stats[home]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or
                        team_stats[away]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT):
                        continue
                    # Check if either team already has a game on d.
                    valid = True
                    for team in (home, away):
                        if d in team_day_slots[team]:
                            last_index = max(team_day_slots[team][d])
                            if idx != last_index + 1:
                                valid = False
                                break
                    if not valid:
                        continue
                    if team_stats[home]['home_games'] >= HOME_AWAY_BALANCE:
                        if team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
                            home, away = away, home
                        else:
                            continue
                    schedule.append((dt, slot, field, home, home[0], away, away[0]))
                    team_stats[home]['total_games'] += 1
                    team_stats[home]['home_games'] += 1
                    team_stats[away]['total_games'] += 1
                    team_stats[away]['away_games'] += 1
                    team_stats[home]['weekly_games'][week_num] += 1
                    team_stats[away]['weekly_games'][week_num] += 1
                    used_slots[(dt, slot, field)] = True
                    team_day_slots[home][d].append(idx)
                    team_day_slots[away][d].append(idx)
                    unscheduled.remove(matchup)
                    progress_made = True
                    break
                if progress_made:
                    break
            if progress_made:
                break
        if not progress_made:
            retry_count += 1
        else:
            retry_count = 0
    if unscheduled:
        print("Warning: Retry limit reached. Some matchups could not be scheduled.")
    return schedule, team_stats

# -------------------------------
# Output & Summary Functions
# -------------------------------
def output_schedule_to_csv(schedule, output_file):
    with open(output_file, mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Time", "Diamond", "Home Team", "Home Division", "Away Team", "Away Division"])
        for game in schedule:
            dt, slot, field, home, home_div, away, away_div = game
            writer.writerow([dt.strftime('%Y-%m-%d'), slot, field, home, home_div, away, away_div])

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
# Main Function
# -------------------------------
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    
    print("\nTeam Availability Debug:")
    for team, days in team_availability.items():
        print(f"Team {team}: {', '.join(days)}")
    
    print("\nField Availability Debug:")
    for entry in field_availability:
        print(f"Field Slot: {entry}")
    
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    
    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")
    
    schedule, team_stats = schedule_standard_games(matchups, team_availability, field_availability, set())
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    generate_matchup_table(schedule, division_teams)

if __name__ == "__main__":
    main()
