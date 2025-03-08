import csv
import itertools
import random
from datetime import datetime, timedelta
from collections import defaultdict
from prettytable import PrettyTable

# CONFIGURABLE PARAMETERS
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11
WEEKLY_GAME_LIMIT = 2   # max games per team per week
MIN_GAP = 2             # minimum days between games (applies to standard scheduling)
MAX_RETRIES = 10000     # for standard scheduling

# -------------------------------
# Helper Functions
# -------------------------------
def min_gap_ok(team, d, team_game_days):
    """Return True if team has no game scheduled within MIN_GAP days before d."""
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
        next(reader)
        for row in reader:
            team = row[0].strip()
            days = [d.strip() for d in row[1:] if d.strip()]
            avail[team] = set(days)
    return avail

def load_field_availability(file_path):
    slots = []
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)
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
            assignment[(t1,t2)] = 2
            count2[t1] += 1
            count2[t2] += 1
            if backtrack(i+1):
                return True
            count2[t1] -= 1
            count2[t2] -= 1
            del assignment[(t1,t2)]
        assignment[(t1,t2)] = 3
        if backtrack(i+1):
            return True
        del assignment[(t1,t2)]
        return False
    if backtrack(0):
        return assignment
    else:
        raise Exception("Intra-division assignment failed.")

def generate_intra_matchups(teams, weight_assignment):
    m = []
    for (t1, t2), weight in weight_assignment.items():
        if weight == 2:
            m.append((t1,t2))
            m.append((t2,t1))
        elif weight == 3:
            m.append((t1,t2))
            m.append((t2,t1))
            if random.random() < 0.5:
                m.append((t1,t2))
            else:
                m.append((t2,t1))
    return m

def generate_intra_division_matchups(division, teams):
    if division == 'B':
        m = []
        for t1,t2 in itertools.combinations(sorted(teams), 2):
            m.append((t1,t2))
            m.append((t2,t1))
        return m
    elif division in ['A','C']:
        two_game_count = 3
        three_game_count = (len(teams)-1) - two_game_count
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
                edges.append((t,opp))
        return edges
    else:
        raise Exception("Bipartite matching failed.")

def generate_inter_division_matchups(division_from, division_to, teams_from, teams_to):
    degree = 4
    edges = generate_bipartite_regular_matchups(teams_from, teams_to, degree)
    m = []
    for (t1,t2) in edges:
        if random.random() < 0.5:
            m.append((t1,t2))
        else:
            m.append((t2,t1))
    return m

def generate_full_matchups(division_teams):
    full = []
    for div, teams in division_teams.items():
        full.extend(generate_intra_division_matchups(div, teams))
    # Inter-division: A vs B and B vs C (A vs C not allowed)
    full.extend(generate_inter_division_matchups('A','B', division_teams['A'], division_teams['B']))
    full.extend(generate_inter_division_matchups('B','C', division_teams['B'], division_teams['C']))
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
        return (t1,t2) if random.random() < 0.5 else (t2,t1)

# -------------------------------
# Doubleheader Scheduling (Separate Pass)
# -------------------------------
def schedule_doubleheaders(doubleheader_dates, field_slots, team_availability, team_blackouts, division_teams):
    """
    For each date in doubleheader_dates, schedule doubleheaders.
    For each such date, we assume that the field_slots list contains slots with that date.
    We group slots by diamond. For each diamond that has at least two slots (early and late),
    we form one doubleheader group.
    
    For each doubleheader group, we select a group of 4 eligible teams (from the union of all teams)
    that satisfy:
      - The team’s availability (day-of-week in team_availability)
      - Not on blackout on that date
      - Haven't yet been assigned a doubleheader in that week (max 1 per week)
      - The group can be ordered so that the early round pairings and late round pairings are legal.
    
    We then assign:
      - For Diamond X: early game: (T1 vs T2) at the early slot.
      - For Diamond Y: early game: (T3 vs T4) at the early slot.
      - For Diamond X: late game: (T2 vs T3) at the late slot.
      - For Diamond Y: late game: (T4 vs T1) at the late slot.
      
    Returns a list of doubleheader games and a set of dates used.
    """
    dh_games = []
    # To enforce max 1 doubleheader per week, track for each team the week numbers they have played DH.
    team_dh_weeks = defaultdict(set)
    # Create a combined list of all teams:
    all_teams = []
    for teams in division_teams.values():
        all_teams.extend(teams)
    all_teams = list(set(all_teams))
    # Group field slots by date and by field.
    slots_by_date = defaultdict(lambda: defaultdict(list))
    for dt, slot, field in field_slots:
        d = dt.date()
        slots_by_date[d][field].append((dt, slot, field))
    # For each doubleheader date:
    for d in sorted(doubleheader_dates):
        if d not in slots_by_date:
            continue
        # For this date, consider only fields that have at least two slots.
        fields_with_slots = {field: sorted(slots, key=lambda x: x[0]) 
                             for field, slots in slots_by_date[d].items() if len(slots) >= 2}
        # We need at least 2 diamonds to form a group of 4.
        if len(fields_with_slots) < 2:
            continue
        # For simplicity, we will combine two fields (the two with the earliest slots).
        sorted_fields = sorted(fields_with_slots.keys(), key=lambda f: fields_with_slots[f][0][0])
        diamond1 = sorted_fields[0]
        diamond2 = sorted_fields[1]
        early_slot1, late_slot1 = fields_with_slots[diamond1][0], fields_with_slots[diamond1][1]
        early_slot2, late_slot2 = fields_with_slots[diamond2][0], fields_with_slots[diamond2][1]
        # Build eligible pool: teams that are available on d (by day-of-week), not on blackout,
        # and that have not yet had a doubleheader in this week.
        day_str = early_slot1[0].strftime('%a')
        week_num = early_slot1[0].isocalendar()[1]
        eligible = []
        for team in all_teams:
            if day_str in team_availability.get(team, set()) and d not in team_blackouts.get(team, set()) and week_num not in team_dh_weeks[team]:
                eligible.append(team)
        # We need at least 4 eligible teams.
        if len(eligible) < 4:
            continue
        # Now, try all combinations of 4 teams from eligible.
        found = False
        from itertools import permutations, combinations
        for group in combinations(eligible, 4):
            # Try all orders (permutations) of the 4-team group.
            for order in permutations(group):
                T1, T2, T3, T4 = order
                # Early pairings:
                early1 = (T1, T2)
                early2 = (T3, T4)
                # Late pairings (swapping opponents):
                late1 = (T2, T3)
                late2 = (T4, T1)
                if is_legal(early1) and is_legal(early2) and is_legal(late1) and is_legal(late2):
                    # We found a valid ordering.
                    found = True
                    selected_order = order
                    break
            if found:
                break
        if not found:
            continue
        # With the selected order, schedule the games.
        T1, T2, T3, T4 = selected_order
        # Early games:
        game1 = (early_slot1[0], early_slot1[1], early_slot1[2], T1, T1[0], T2, T2[0])
        game2 = (early_slot2[0], early_slot2[1], early_slot2[2], T3, T3[0], T4, T4[0])
        # Late games:
        game3 = (late_slot1[0], late_slot1[1], late_slot1[2], T2, T2[0], T3, T3[0])
        game4 = (late_slot2[0], late_slot2[1], late_slot2[2], T4, T4[0], T1, T1[0])
        dh_games.extend([game1, game2, game3, game4])
        # Mark these teams as having had a doubleheader in this week.
        for team in selected_order:
            team_dh_weeks[team].add(week_num)
        # Remove the used slots for these two diamonds from field_slots.
        # (They are now “used” and will not be available for standard scheduling.)
        # We assume that on a doubleheader day, all slots for that diamond are reserved.
        for f in [diamond1, diamond2]:
            slots_by_date[d][f] = []  # clear out slots for these fields on d
    return dh_games

# -------------------------------
# Standard (Single Game) Scheduling
# -------------------------------
def schedule_standard_games(matchups, team_availability, field_availability, doubleheader_dates):
    schedule = []
    team_stats = defaultdict(lambda: {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'weekly_games': defaultdict(int)
    })
    team_game_days = defaultdict(set)
    used_slots = {}
    unscheduled_matchups = matchups[:]
    retry_count = 0
    # Filter field slots to only those on dates not in doubleheader_dates.
    standard_slots = [s for s in field_availability if s[0].date() not in doubleheader_dates]
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
    # Load data files.
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    team_blackouts = load_team_blackouts('team_blackouts.csv')
    doubleheader_dates = load_doubleheader_dates('doubleheaders.csv')
    
    # Debug prints.
    print("\nTeam Availability Debug:")
    for team, days in team_availability.items():
        print(f"{team}: {', '.join(days)}")
    print("\nField Availability Debug:")
    for entry in field_availability:
        print(f"Field Slot: {entry}")
    
    # Define teams per division.
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    
    # Generate matchup pool.
    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")
    
    # First, schedule doubleheaders.
    dh_games = schedule_doubleheaders(doubleheader_dates, field_availability, team_availability, team_blackouts, division_teams)
    
    # Now, schedule standard games on dates not used for doubleheaders.
    standard_games, standard_stats = schedule_standard_games(matchups, team_availability, field_availability, doubleheader_dates)
    
    # Combine schedules.
    full_schedule = dh_games + standard_games
    
    # Output schedule and summaries.
    output_schedule_to_csv(full_schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    # (For simplicity, we only print the standard_stats summary here.)
    print_schedule_summary(standard_stats)
    generate_matchup_table(full_schedule, division_teams)

if __name__ == "__main__":
    main()
