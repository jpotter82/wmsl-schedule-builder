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
MIN_GAP = 2  # Minimum number of days between game nights

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
            # Expect date in YYYY-MM-DD; slot is a string (e.g., "10:30 AM")
            date = datetime.strptime(row[0].strip(), '%Y-%m-%d')
            slot = row[1].strip()
            field = row[2].strip()
            field_availability.append((date, slot, field))
    field_availability.sort(key=lambda x: x[0])
    return field_availability

def load_team_blackouts(file_path):
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
    pairs = list(itertools.combinations(sorted(teams), 2))
    count2 = {team: 0 for team in teams}
    assignment = {}
    
    def backtrack(i):
        if i == len(pairs):
            return all(count2[team] == two_game_count for team in teams)
        team1, team2 = pairs[i]
        # Option 1: try weight 2 if both teams need it.
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
    matchups = []
    for (team1, team2), weight in weight_assignment.items():
        if weight == 2:
            matchups.append((team1, team2))
            matchups.append((team2, team1))
        elif weight == 3:
            matchups.append((team1, team2))
            matchups.append((team2, team1))
            # Add extra game with random home assignment.
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
# Inter-division matchup generation
# -------------------------------
def generate_bipartite_regular_matchups(teams1, teams2, degree):
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
    # Inter-division games (A and C do NOT play):
    inter_AB = generate_inter_division_matchups('A', 'B', division_teams['A'], division_teams['B'])
    full_matchups.extend(inter_AB)
    inter_BC = generate_inter_division_matchups('B', 'C', division_teams['B'], division_teams['C'])
    full_matchups.extend(inter_BC)
    random.shuffle(full_matchups)
    return full_matchups

# -------------------------------
# Home/Away Decision Helper
# -------------------------------
def decide_home_away(team1, team2, team_stats):
    if team_stats[team1]['home_games'] >= HOME_AWAY_BALANCE and team_stats[team2]['home_games'] < HOME_AWAY_BALANCE:
        return team2, team1
    if team_stats[team2]['home_games'] >= HOME_AWAY_BALANCE and team_stats[team1]['home_games'] < HOME_AWAY_BALANCE:
        return team1, team2
    if team_stats[team1]['home_games'] < team_stats[team2]['home_games']:
        return team1, team2
    elif team_stats[team2]['home_games'] < team_stats[team1]['home_games']:
        return team2, team1
    else:
        return (team1, team2) if random.random() < 0.5 else (team2, team1)

# -------------------------------
# Doubleheader Scheduling Helper
# -------------------------------
def schedule_doubleheader_for_date(d, slots, unscheduled, team_availability, team_blackouts, team_game_days, team_stats, week_num):
    """
    For doubleheader date d, with at least two slots available (slots sorted by time),
    attempt to select two distinct matchups M1 and M2 (from unscheduled) that are valid for d.
    For any team that appears in both M1 and M2, the opponents must differ.
    Returns a list of scheduled games (each game is assigned to a distinct slot).
    """
    scheduled_games = []
    day_str = slots[0][0].strftime('%a')
    def min_gap_ok(team):
        for gd in team_game_days[team]:
            if gd != d and (d - gd).days < MIN_GAP:
                return False
        return True
    # Build candidate pool: all matchups in unscheduled that are valid on day d.
    candidates = []
    for matchup in unscheduled:
        t1, t2 = matchup
        if day_str not in team_availability.get(t1, set()) or day_str not in team_availability.get(t2, set()):
            continue
        if d in team_blackouts.get(t1, set()) or d in team_blackouts.get(t2, set()):
            continue
        if not (min_gap_ok(t1) and min_gap_ok(t2)):
            continue
        candidates.append(matchup)
    # We want to select two matchups M1 and M2 such that if a team appears in both, its opponents differ.
    selected_pair = None
    for i in range(len(candidates)):
        for j in range(i+1, len(candidates)):
            M1 = candidates[i]
            M2 = candidates[j]
            overlap = set(M1).intersection(set(M2))
            valid = True
            for team in overlap:
                opp1 = M1[1] if M1[0] == team else M1[0]
                opp2 = M2[1] if M2[0] == team else M2[0]
                if opp1 == opp2:
                    valid = False
                    break
            if valid:
                selected_pair = (M1, M2)
                break
        if selected_pair:
            break
    if selected_pair:
        M1, M2 = selected_pair
        unscheduled.remove(M1)
        unscheduled.remove(M2)
        home1, away1 = decide_home_away(M1[0], M1[1], team_stats)
        home2, away2 = decide_home_away(M2[0], M2[1], team_stats)
        # Use the earliest two slots for the two games.
        game1 = (slots[0][0], slots[0][1], slots[0][2], home1, home1[0], away1, away1[0])
        game2 = (slots[1][0], slots[1][1], slots[1][2], home2, home2[0], away2, away2[0])
        scheduled_games.extend([game1, game2])
        # Update stats and game days.
        for team, home_flag, opp in [(home1, True, away1), (away1, False, home1),
                                      (home2, True, away2), (away2, False, home2)]:
            team_stats[team]['total_games'] += 1
            if home_flag:
                team_stats[team]['home_games'] += 1
            else:
                team_stats[team]['away_games'] += 1
            team_stats[team]['weekly_games'][week_num] += 1
            team_game_days[team].add(d)
    else:
        # If no valid pair exists, try to schedule one game if possible.
        if candidates:
            M1 = candidates[0]
            unscheduled.remove(M1)
            home1, away1 = decide_home_away(M1[0], M1[1], team_stats)
            game1 = (slots[0][0], slots[0][1], slots[0][2], home1, home1[0], away1, away1[0])
            scheduled_games.append(game1)
            for team, home_flag in [(home1, True), (away1, False)]:
                team_stats[team]['total_games'] += 1
                if home_flag:
                    team_stats[team]['home_games'] += 1
                else:
                    team_stats[team]['away_games'] += 1
                team_stats[team]['weekly_games'][week_num] += 1
                team_game_days[team].add(d)
    return scheduled_games

# -------------------------------
# Revised Scheduling Function
#   Phase 1: Schedule all doubleheader dates first (aim for 2 games per such date).
#   Phase 2: Process remaining (non-doubleheader) dates slot-by-slot.
# -------------------------------
def schedule_games(matchups, team_availability, field_availability, team_blackouts, doubleheader_dates):
    schedule = []
    team_stats = defaultdict(lambda: {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'weekly_games': defaultdict(int)
    })
    team_game_days = defaultdict(set)  # team -> set of dates on which team already has a game
    unscheduled = matchups[:]  # working copy
    
    # Group field slots by date.
    slots_by_date = defaultdict(list)
    for date, slot, field in field_availability:
        slots_by_date[date.date()].append((date, slot, field))
    for d in slots_by_date:
        slots_by_date[d].sort(key=lambda x: x[0])
    
    # Phase 1: Process doubleheader dates first.
    for d in sorted(doubleheader_dates):
        if d not in slots_by_date or len(slots_by_date[d]) < 2:
            continue  # Need at least 2 slots for a doubleheader.
        slots = slots_by_date[d]
        week_num = slots[0][0].isocalendar()[1]
        games = schedule_doubleheader_for_date(d, slots, unscheduled, team_availability, team_blackouts, team_game_days, team_stats, week_num)
        schedule.extend(games)
    
    # Helper for min_gap check.
    def min_gap_ok(team, d):
        for gd in team_game_days[team]:
            if gd != d and (d - gd).days < MIN_GAP:
                return False
        return True
    
    # Phase 2: Process non-doubleheader dates.
    for d in sorted(slots_by_date.keys()):
        if d in doubleheader_dates:
            continue
        slots = slots_by_date[d]
        day_str = slots[0][0].strftime('%a')
        week_num = slots[0][0].isocalendar()[1]
        for slot in slots:
            candidate = None
            for matchup in unscheduled:
                t1, t2 = matchup
                if day_str not in team_availability.get(t1, set()) or day_str not in team_availability.get(t2, set()):
                    continue
                if d in team_blackouts.get(t1, set()) or d in team_blackouts.get(t2, set()):
                    continue
                if not (min_gap_ok(t1, d) and min_gap_ok(t2, d)):
                    continue
                candidate = matchup
                break
            if candidate:
                unscheduled.remove(candidate)
                t1, t2 = candidate
                home, away = decide_home_away(t1, t2, team_stats)
                game = (slot[0], slot[1], slot[2], home, home[0], away, away[0])
                schedule.append(game)
                team_stats[home]['total_games'] += 1
                team_stats[home]['home_games'] += 1
                team_stats[away]['total_games'] += 1
                team_stats[away]['away_games'] += 1
                team_stats[home]['weekly_games'][week_num] += 1
                team_stats[away]['weekly_games'][week_num] += 1
                team_game_days[home].add(d)
                team_game_days[away].add(d)
    if unscheduled:
        print("Warning: Some matchups could not be scheduled.")
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
        table.add_row([team[0], team, stats['total_games'], stats['home_games'], stats['away_games']])
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
