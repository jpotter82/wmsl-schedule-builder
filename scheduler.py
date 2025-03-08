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
MIN_GAP = 2             # minimum days between game dates

# -------------------------------
# Helper Functions
# -------------------------------
def min_gap_ok(team, d, team_game_days):
    for gd in team_game_days[team]:
        if gd != d and (d - gd).days < MIN_GAP:
            return False
    return True

def is_legal(matchup):
    a, b = matchup
    # Illegal if one is A and the other is C.
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
    full.extend(generate_inter_division_matchups('A', 'B', division_teams['A'], division_teams['B']))
    full.extend(generate_inter_division_matchups('B', 'C', division_teams['B'], division_teams['C']))
    full = [m for m in full if is_legal(m)]
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
# Late Round Alternative Pairing (for Doubleheaders)
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
            pairing.pop()
            used.remove(b)
        used.remove(a)
        return False
    if backtrack():
        return pairing
    else:
        return None

# -------------------------------
# Doubleheader Scheduling (per Date)
# -------------------------------
def schedule_doubleheader_date(d, slots, unscheduled, team_availability, team_blackouts, team_game_days, team_stats):
    # Gather all slots on date d.
    # Partition into early and late rounds by time (earlier half vs. later half).
    slots.sort(key=lambda x: x[0])
    n = len(slots)
    if n < 2:
        return [], unscheduled
    early_slots = slots[:n//2]
    late_slots = slots[n//2:]
    if len(early_slots) != len(late_slots):
        # Force an equal number of early and late slots.
        return [], unscheduled
    # For all fields on d (across early_slots), we want to assign one candidate matchup per early slot.
    early_assignment = [None] * len(early_slots)
    S = set()
    def assign_early(i, unsched):
        if i == len(early_slots):
            return True
        day_str = early_slots[i][0].strftime('%a')
        for matchup in unsched[:]:
            if not is_legal(matchup):
                continue
            a, b = matchup
            if day_str not in team_availability.get(a, set()) or day_str not in team_availability.get(b, set()):
                continue
            if d in team_blackouts.get(a, set()) or d in team_blackouts.get(b, set()):
                continue
            if not (min_gap_ok(a, d, team_game_days) and min_gap_ok(b, d, team_game_days)):
                continue
            if team_stats[a]['total_games'] >= MAX_GAMES or team_stats[b]['total_games'] >= MAX_GAMES:
                continue
            if a in S or b in S:
                continue
            early_assignment[i] = matchup
            S.add(a)
            S.add(b)
            unsched.remove(matchup)
            if assign_early(i+1, unsched):
                return True
            unsched.append(matchup)
            S.remove(a)
            S.remove(b)
            early_assignment[i] = None
        return False
    unsched_copy = unscheduled[:]
    if not assign_early(0, unsched_copy):
        return [], unsched_copy
    # Build early round games and record early pairing.
    early_games = []
    early_pairing = {}
    for i, matchup in enumerate(early_assignment):
        a, b = matchup
        home, away = decide_home_away(a, b, team_stats)
        game = (early_slots[i][0], early_slots[i][1], early_slots[i][2], home, home[0], away, away[0])
        early_games.append(game)
        early_pairing[home] = away
        early_pairing[away] = home
    # Verify that S is even and no illegal interleague pair exists.
    if any(not is_legal((a, early_pairing[a])) for a in S):
        # This should not happen if candidates were filtered.
        return early_games, unsched_copy
    # Compute alternative pairing for late round on the same set S.
    alt_pairs = find_alternative_pairing(S, early_pairing)
    if alt_pairs is None or len(alt_pairs) < len(early_games):
        # Fallback: reverse each early pairing.
        alt_pairs = [(early_pairing[a], a) for a in early_pairing if a in S]
        # Remove duplicates.
        alt_pairs = list({tuple(sorted(p)) for p in alt_pairs})
        alt_pairs = [p for p in alt_pairs]
        if len(alt_pairs) < len(early_games):
            return early_games, unsched_copy
    # Assign late round games.
    late_games = []
    for i in range(len(late_slots)):
        a, b = alt_pairs[i]
        # Final legality check:
        if not is_legal((a, b)):
            a, b = early_games[i][5], early_games[i][3]  # reverse early pairing
        home, away = decide_home_away(a, b, team_stats)
        game = (late_slots[i][0], late_slots[i][1], late_slots[i][2], home, home[0], away, away[0])
        late_games.append(game)
    # Update stats for both rounds.
    for game in early_games + late_games:
        dt, slot, field, home, _, away, _ = game
        week_num = dt.isocalendar()[1]
        team_game_days[home].add(d)
        team_game_days[away].add(d)
        team_stats[home]['total_games'] += 1
        team_stats[home]['home_games'] += 1
        team_stats[away]['total_games'] += 1
        team_stats[away]['away_games'] += 1
        team_stats[home]['weekly_games'][week_num] += 1
        team_stats[away]['weekly_games'][week_num] += 1
    scheduled = early_games + late_games
    # Remove used matchups from unscheduled.
    for m in early_assignment:
        if m in unsched_copy:
            unsched_copy.remove(m)
    return scheduled, unsched_copy

# -------------------------------
# Single Date Scheduling (Non-Doubleheader)
# -------------------------------
def schedule_single_date(d, slots, unscheduled, team_availability, team_blackouts, team_game_days, team_stats):
    scheduled = []
    day_str = slots[0][0].strftime('%a')
    week_num = slots[0][0].isocalendar()[1]
    for slot in slots:
        candidate = None
        for matchup in unscheduled:
            if not is_legal(matchup):
                continue
            a, b = matchup
            if day_str not in team_availability.get(a, set()) or day_str not in team_availability.get(b, set()):
                continue
            if d in team_blackouts.get(a, set()) or d in team_blackouts.get(b, set()):
                continue
            if not (min_gap_ok(a, d, team_game_days) and min_gap_ok(b, d, team_game_days)):
                continue
            if team_stats[a]['total_games'] >= MAX_GAMES or team_stats[b]['total_games'] >= MAX_GAMES:
                continue
            candidate = matchup
            break
        if candidate:
            unscheduled.remove(candidate)
            a, b = candidate
            home, away = decide_home_away(a, b, team_stats)
            game = (slot[0], slot[1], slot[2], home, home[0], away, away[0])
            scheduled.append(game)
            team_game_days[home].add(d)
            team_game_days[away].add(d)
            team_stats[home]['total_games'] += 1
            team_stats[home]['home_games'] += 1
            team_stats[away]['total_games'] += 1
            team_stats[away]['away_games'] += 1
            team_stats[home]['weekly_games'][week_num] += 1
            team_stats[away]['weekly_games'][week_num] += 1
    return scheduled, unscheduled

# -------------------------------
# Main Scheduling Function
# -------------------------------
def schedule_games(matchups, field_slots, team_availability, team_blackouts, doubleheader_dates):
    schedule = []
    team_stats = defaultdict(lambda: {'total_games': 0, 'home_games': 0, 'away_games': 0, 'weekly_games': defaultdict(int)})
    team_game_days = defaultdict(set)
    
    # Group field slots by date.
    slots_by_date = defaultdict(list)
    for dt, slot, field in field_slots:
        slots_by_date[dt.date()].append((dt, slot, field))
    for d in slots_by_date:
        slots_by_date[d].sort(key=lambda x: x[0])
    
    unscheduled = [m for m in matchups if is_legal(m)]
    all_dates = sorted(slots_by_date.keys())
    # Phase 1: Process doubleheader dates (by day).
    for d in all_dates:
        if d in doubleheader_dates:
            games, unscheduled = schedule_doubleheader_date(d, slots_by_date[d], unscheduled, team_availability, team_blackouts, team_game_days, team_stats)
            schedule.extend(games)
    # Phase 2: Process non-doubleheader dates in descending order (later dates first).
    non_dh_dates = sorted([d for d in all_dates if d not in doubleheader_dates], reverse=True)
    for d in non_dh_dates:
        games, unscheduled = schedule_single_date(d, slots_by_date[d], unscheduled, team_availability, team_blackouts, team_game_days, team_stats)
        schedule.extend(games)
    if unscheduled:
        print("Warning: Some matchups could not be scheduled.")
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
            writer.writerow([dt.strftime('%Y-%m-%d'), slot, field, home, home[0], away, away[0]])

def print_schedule_summary(team_stats):
    table = PrettyTable()
    table.field_names = ["Division", "Team", "Total Games", "Home Games", "Away Games"]
    for team, stats in sorted(team_stats.items()):
        table.add_row([team[0], team, stats['total_games'], stats['home_games'], stats['away_games']])
    print("\nSchedule Summary:")
    print(table)

def generate_matchup_table(schedule, division_teams):
    matchup_count = defaultdict(lambda: defaultdict(int))
    for game in schedule:
        home = game[3]
        away = game[5]
        matchup_count[home][away] += 1
        matchup_count[away][home] += 1
    all_teams = sorted([t for teams in division_teams.values() for t in teams])
    table = PrettyTable()
    table.field_names = ["Team"] + all_teams
    for team in all_teams:
        row = [team] + [matchup_count[team][opp] for opp in all_teams]
        table.add_row(row)
    print("\nMatchup Table:")
    print(table)

# -------------------------------
# MAIN
# -------------------------------
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_slots = load_field_availability('field_availability.csv')
    team_blackouts = load_team_blackouts('team_blackouts.csv')
    doubleheader_dates = load_doubleheader_dates('doubleheaders.csv')
    
    print("\nTeam Availability Debug:")
    for team, days in team_availability.items():
        print(f"{team}: {', '.join(days)}")
    print("\nField Slots:")
    for slot in field_slots:
        print(slot)
    print("\nTeam Blackouts Debug:")
    for team, dates in team_blackouts.items():
        print(f"{team}: {', '.join(str(d) for d in dates)}")
    print("\nDoubleheader Dates:")
    for d in sorted(doubleheader_dates):
        print(d)
    
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    
    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")
    
    schedule, team_stats = schedule_games(matchups, field_slots, team_availability, team_blackouts, doubleheader_dates)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    generate_matchup_table(schedule, division_teams)

if __name__ == "__main__":
    main()
