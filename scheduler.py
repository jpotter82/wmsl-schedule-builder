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
MIN_GAP = 2             # minimum days between game nights

# -------------------------------
# Helper: Minimum Gap Check
# -------------------------------
def min_gap_ok(team, d, team_game_days):
    for gd in team_game_days[team]:
        if gd != d and (d - gd).days < MIN_GAP:
            return False
    return True

# -------------------------------
# DATA LOADING FUNCTIONS
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
# MATCHUP GENERATION FUNCTIONS (unchanged)
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
    for (t1,t2), weight in weight_assignment.items():
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
        available = [t for t in teams2 if capacity[t]>0]
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
    full.extend(generate_inter_division_matchups('A','B', division_teams['A'], division_teams['B']))
    full.extend(generate_inter_division_matchups('B','C', division_teams['B'], division_teams['C']))
    random.shuffle(full)
    return full

# -------------------------------
# HOME/AWAY DECISION HELPER
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
# LATE ROUND ALTERNATIVE PAIRING (for Doubleheaders)
# -------------------------------
def find_alternative_pairing(teams, early_pairing):
    teams = list(teams)
    n = len(teams)
    if n % 2 != 0:
        return None
    pairing = []
    used = set()
    def backtrack():
        if len(used) == n:
            return True
        for a in teams:
            if a not in used:
                break
        used.add(a)
        for b in teams:
            if b in used:
                continue
            if early_pairing.get(a) == b or early_pairing.get(b) == a:
                continue
            used.add(b)
            pairing.append((a, b))
            if backtrack():
                return True
      
