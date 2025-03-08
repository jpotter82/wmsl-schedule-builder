import csv
import itertools
import random
from datetime import datetime, timedelta
from collections import defaultdict
from prettytable import PrettyTable

# Configurable parameters
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11
WEEKLY_GAME_LIMIT = 2   # max games per team per week
MAX_RETRIES = 10000     # scheduling backtracking limit
MIN_GAP = 2             # minimum days between standard games

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
        next(reader)
        for row in reader:
            team = row[0].strip()
            days = [d.strip() for d in row[1:] if d.strip()]
            availability[team] = set(days)
    return availability

def load_field_availability(file_path):
    field_availability = []
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            dt = datetime.strptime(row[0].strip(), '%Y-%m-%d')
            slot = row[1].strip()
            field = row[2].strip()
            field_availability.append((dt, slot, field))
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
    elif division in ['A', 'C']:
        two_game_count = 3
        three_game_count = (len(teams) - 1) - two_game_count
        assign = assign_intra_division_weights(teams, two_game_count, three_game_count)
        return generate_intra_matchups(teams, assign)
    else:
        raise Exception("Unknown division")

def generate_bipartite_regular_matchups(teams1, teams2, degree):
    teams1_order = teams1[:]
    random.shuffle(teams1_order)
    assign = {t: [] for t in teams1_order}
    capacity = {t: degree for t in teams2}
    def backtrack(i):
        if i == len(teams1_order):
            return True
        team = teams1_order[i]
        available = [t for t in teams2 if capacity[t] > 0]
        for combo in itertools.combinations(available, degree):
            assign[team] = list(combo)
            for t in combo:
                capacity[t] -= 1
            if backtrack(i+1):
                return True
            for t in combo:
                capacity[t] += 1
        return False
    if backtrack(0):
        edges = []
        for t in teams1_order:
            for opp in assign[t]:
                edges.append((t, opp))
        return edges
    else:
        raise Exception("Bipartite matching failed.")

def generate_inter_division_matchups(division_from, division_to, teams_from, teams_to):
    degree = 4
    edges = generate_bipartite_regular_matchups(teams_from, teams_to, degree)
    m = []
    for (t1, t2) in edges:
        if random.random() < 0.5:
            m.append((t1, t2))
        else:
            m.append((t2, t1))
    return m

def generate_full_matchups(division_teams):
    full = []
    for div, teams in division_teams.items():
        full.extend(generate_intra_division_matchups(div, teams))
    # Inter-division games: A vs. B and B vs. C (A vs. C not allowed)
    full.extend(generate_inter_division_matchups('A', 'B', division_teams['A'], division_teams['B']))
    full.extend(generate_inter_division_matchups('B', 'C', division_teams['B'], division_teams['C']))
    random.shuffle(full)
    return full

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
# Doubleheader Scheduling (Separate Pass)
# -------------------------------
def schedule_doubleheaders(doubleheader_dates, field_slots, team_availability, team_blackouts, division_teams):
    """
    For each date in doubleheader_dates, select 4 eligible teams and schedule a back-to-back doubleheader.
    For a selected group of 4 teams [T1, T2, T3, T4], we schedule:
      Diamond 1: early: T1 vs. T2, late: T2 vs. T3
      Diamond 2: early: T3 vs. T4, late: T4 vs. T1
    All pairings must be legal.
    Enforce max 1 doubleheader per team per week.
    """
    dh_games = []
    team_dh_weeks = defaultdict(set)
    # Combined team pool:
    all_teams = []
    for teams in division_teams.values():
        all_teams.extend(teams)
    all_teams = list(set(all_teams))
    # Group field slots by date and by diamond (field).
    slots_by_date = defaultdict(lambda: defaultdict(list))
    for dt, slot, field in field_slots:
        d = dt.date()
        slots_by_date[d][field].append((dt, slot, field))
    for d in sorted(doubleheader_dates):
        if d not in slots_by_date:
            continue
        # Consider fields with at least 2 slots.
        fields = {f: sorted(slots, key=lambda x: x[0]) for f, slots in slots_by_date[d].items() if len(slots) >= 2}
        if len(fields) < 2:
            continue
        # For simplicity, choose the two fields with the earliest slots.
        sorted_fields = sorted(fields.keys(), key=lambda f: fields[f][0][0])
        diamond1, diamond2 = sorted_fields[0], sorted_fields[1]
        early_slot1, late_slot1 = fields[diamond1][0], fields[diamond1][1]
        early_slot2, late_slot2 = fields[diamond2][0], fields[diamond2][1]
        day_str = early_slot1[0].strftime('%a')
        week_num = early_slot1[0].isocalendar()[1]
        # Build eligible pool.
        eligible = []
        for team in all_teams:
            if day_str in team_availability.get(team, set()) and d not in team_blackouts.get(team, set()) and week_num not in team_dh_weeks[team]:
                eligible.append(team)
        if len(eligible) < 4:
            continue
        # Try all combinations of 4 eligible teams.
        valid_group = None
        for group in itertools.combinations(eligible, 4):
            for order in itertools.permutations(group):
                T1, T2, T3, T4 = order
                early1 = (T1, T2)
                early2 = (T3, T4)
                late1 = (T2, T3)
                late2 = (T4, T1)
                if is_legal(early1) and is_legal(early2) and is_legal(late1) and is_legal(late2):
                    valid_group = order
                    break
            if valid_group:
                break
        if not valid_group:
            continue
        T1, T2, T3, T4 = valid_group
        game_early_d1 = (early_slot1[0], early_slot1[1], early_slot1[2], T1, T1[0], T2, T2[0])
        game_early_d2 = (early_slot2[0], early_slot2[1], early_slot2[2], T3, T3[0], T4, T4[0])
        game_late_d1  = (late_slot1[0], late_slot1[1], late_slot1[2], T2, T2[0], T3, T3[0])
        game_late_d2  = (late_slot2[0], late_slot2[1], late_slot2[2], T4, T4[0], T1, T1[0])
        dh_games.extend([game_early_d1, game_early_d2, game_late_d1, game_late_d2])
        for team in valid_group:
            team_dh_weeks[team].add(week_num)
    return dh_games

# -------------------------------
# Standard (Single Game) Scheduling
# -------------------------------
def schedule_standard_games(matchups, team_availability, field_availability, doubleheader_dates):
    schedule = []
    team_stats = defaultdict(lambda: {'total_games': 0, 'home_games': 0, 'away_games': 0, 'weekly_games': defaultdict(int)})
    team_game_days = defaultdict(set)
    used_slots = {}
    unscheduled_matchups = matchups[:]
    # Process only slots on dates not in doubleheader_dates.
    standard_slots = [s for s in field_availability if s[0].date() not in doubleheader_dates]
    # Process in ascending order so that early-season slots are filled first.
    standard_slots.sort(key=lambda x: x[0])
    retry_count = 0
    while unscheduled_matchups and retry_count < MAX_RETRIES:
        progress_made = False
        for date, slot, field in standard_slots:
            if used_slots.get((date, slot, field), False):
                continue
            day_of_week = date.strftime('%a')
            week_num = date.isocalendar()[1]
            for matchup in unscheduled_matchups[:]:
                home, away = matchup
                if day_of_week not in team_availability.get(home, set()) or day_of_week not in team_availability.get(away, set()):
                    continue
                if team_stats[home]['total_games'] >= MAX_GAMES or team_stats[away]['total_games'] >= MAX_GAMES:
                    continue
                if (team_stats[home]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or
                    team_stats[away]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT):
                    continue
                if not (min_gap_ok(home, date.date(), team_game_days) and min_gap_ok(away, date.date(), team_game_days)):
                    continue
                if team_stats[home]['home_games'] >= HOME_AWAY_BALANCE:
                    if team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
                        home, away = away, home
                    else:
                        continue
                schedule.append((date, slot, field, home, home[0], away, away[0]))
                team_stats[home]['total_games'] += 1
                team_stats[home]['home_games'] += 1
                team_stats[away]['total_games'] += 1
                team_stats[away]['away_games'] += 1
                team_stats[home]['weekly_games'][week_num] += 1
                team_stats[away]['weekly_games'][week_num] += 1
                used_slots[(date, slot, field)] = True
                team_game_days[home].add(date.date())
                team_game_days[away].add(date.date())
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

# -------------------------------
# Main Scheduling Function
# -------------------------------
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    team_blackouts = load_team_blackouts('team_blackouts.csv')
    doubleheader_dates = load_doubleheader_dates('doubleheaders.csv')
    
    print("\nTeam Availability Debug:")
    for team, days in team_availability.items():
        print(f"{team}: {', '.join(days)}")
    
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
    
    # First, schedule doubleheaders.
    dh_games = schedule_doubleheaders(doubleheader_dates, field_availability, team_availability, team_blackouts, division_teams)
    # Then, schedule standard games on dates not in doubleheaders.
    standard_games, standard_stats = schedule_standard_games(matchups, team_availability, field_availability, doubleheader_dates)
    
    full_schedule = dh_games + standard_games
    output_schedule_to_csv(full_schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    print_schedule_summary(standard_stats)
    generate_matchup_table(full_schedule, division_teams)

if __name__ == "__main__":
    main()
