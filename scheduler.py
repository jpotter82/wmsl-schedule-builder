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
MIN_GAP = 2            # minimum days between games on different days

# -------------------------------
# Helper Functions
# -------------------------------
def min_gap_ok(team, d, team_game_days):
    """
    Returns True if team has no game scheduled on a day less than MIN_GAP days before d.
    (For games on the same day, consecutive slots are checked separately.)
    """
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
    avail = {}
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            team = row[0].strip()
            days = [d.strip() for d in row[1:] if d.strip()]
            avail[team] = set(days)
    return avail

def load_field_availability(file_path):
    slots = []
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            dt = datetime.strptime(row[0].strip(), '%Y-%m-%d')
            slot = row[1].strip()
            field = row[2].strip()
            slots.append((dt, slot, field))
    slots.sort(key=lambda x: x[0])
    return slots

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
            if backtrack(i+1):
                return True
            count2[t1] -= 1
            count2[t2] -= 1
            del assignment[(t1, t2)]
        assignment[(t1, t2)] = 3
        if backtrack(i+1):
            return True
        del assignment[(t1, t2)]
        return False
    
    if backtrack(0):
        return assignment
    else:
        raise Exception("No valid intra-division assignment found.")

def generate_intra_matchups(teams, weight_assignment):
    m = []
    for (t1, t2), weight in weight_assignment.items():
        if weight == 2:
            m.append((t1, t2))
            m.append((t2, t1))
        elif weight == 3:
            m.append((t1, t2))
            m.append((t2, t1))
            if random.random() < 0.5:
                m.append((t1, t2))
            else:
                m.append((t2, t1))
    return m

def generate_intra_division_matchups(division, teams):
    if division == 'B':
        m = []
        for t1, t2 in itertools.combinations(sorted(teams), 2):
            m.append((t1, t2))
            m.append((t2, t1))
        return m
    elif division in ['A','C']:
        two_game_count = 3
        three_game_count = (len(teams) - 1) - two_game_count
        assign = assign_intra_division_weights(teams, two_game_count, three_game_count)
        return generate_intra_matchups(teams, assign)
    else:
        raise Exception("Unknown division")

# For inter-division matchups, we now implement an uneven bipartite matching function.
def generate_uneven_bipartite_matchups(teams_left, teams_right, left_degree, right_degree):
    """
    Generates a bipartite assignment such that each team in teams_left appears exactly left_degree times,
    and each team in teams_right appears exactly right_degree times.
    Requires len(teams_left)*left_degree == len(teams_right)*right_degree.
    Uses backtracking search.
    Returns a list of matchups (each is a tuple from teams_left, teams_right).
    """
    total = len(teams_left) * left_degree
    if total != len(teams_right) * right_degree:
        raise Exception("Uneven bipartite matching: degree condition not met.")
    # We'll build an assignment: for each team in teams_left, assign a list of teams from teams_right.
    assignment = {}
    # Track remaining capacity for each team on the right.
    capacity = {t: right_degree for t in teams_right}
    teams_left_order = teams_left[:]
    random.shuffle(teams_left_order)
    matchups = []
    
    def backtrack(i):
        if i == len(teams_left_order):
            return True
        team = teams_left_order[i]
        # Try all combinations of size left_degree from teams_right that have capacity left.
        available = [t for t in teams_right if capacity[t] > 0]
        for combo in itertools.combinations(available, left_degree):
            assignment[team] = list(combo)
            for t in combo:
                capacity[t] -= 1
            if backtrack(i+1):
                return True
            for t in combo:
                capacity[t] += 1
        return False
    
    if not backtrack(0):
        raise Exception("Uneven bipartite matching failed.")
    # Build matchup list.
    for team in teams_left_order:
        for opp in assignment[team]:
            matchups.append((team, opp))
    return matchups

def generate_inter_division_matchups(division_from, division_to, teams_from, teams_to):
    """
    For inter-division games.
    For A vs. B: teams_from are from A, teams_to are from B.
    For B vs. C: teams_from are from B, teams_to are from C.
    """
    # Decide degrees based on our requirements.
    # For A vs. B: only inter-eligible A teams (A5-A8) are used.
    # Each A team plays 4 games and each B team plays 2 games.
    # For B vs. C: only inter-eligible C teams (C1-C4) are used.
    # Each C team plays 4 games and each B team plays 2 games.
    if division_from == 'A' and division_to == 'B':
        left_degree = 4
        right_degree = 2
    elif division_from == 'B' and division_to == 'C':
        left_degree = 2
        right_degree = 4
    else:
        # Default: use degree 4 on both sides (should not happen as A vs C are illegal)
        left_degree = right_degree = 4
    edges = generate_uneven_bipartite_matchups(teams_from, teams_to, left_degree, right_degree)
    # Randomly assign home/away.
    matchups = []
    for (t1, t2) in edges:
        if random.random() < 0.5:
            matchups.append((t1, t2))
        else:
            matchups.append((t2, t1))
    return matchups

def generate_full_matchups(division_teams):
    full_matchups = []
    # Intra-division games: use full division teams.
    for div, teams in division_teams.items():
        full_matchups.extend(generate_intra_division_matchups(div, teams))
    # Inter-division games:
    # A and C do NOT play.
    # For A vs. B: use only A teams A5-A8.
    teams_A_inter = [team for team in division_teams['A'] if team[1] in "5678"]
    inter_AB = generate_inter_division_matchups('A', 'B', teams_A_inter, division_teams['B'])
    full_matchups.extend(inter_AB)
    # For B vs. C: use only C teams C1-C4.
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
    # For standard scheduling, track for each team the slot indices (by sorted order) on each date.
    team_day_slots = defaultdict(lambda: defaultdict(list))
    used_slots = {}
    unscheduled = matchups[:]
    # Process only slots on dates not in doubleheader_dates.
    standard_slots = [s for s in field_availability if s[0].date() not in doubleheader_dates]
    # Group slots by date.
    slots_by_date = defaultdict(list)
    for dt, slot, field in standard_slots:
        slots_by_date[dt.date()].append((dt, slot, field))
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
                    # Check consecutive slot rule.
                    valid = True
                    for team in (home, away):
                        if d in team_day_slots[team]:
                            last_idx = max(team_day_slots[team][d])
                            if idx != last_idx + 1:
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
        home = game[3]
        away = game[5]
        matchup_count[home][away] += 1
        matchup_count[away][home] += 1
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
