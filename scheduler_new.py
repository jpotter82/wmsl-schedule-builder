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
MIN_GAP = 2                # minimum days between games on different dates
MIN_DOUBLE_HEADERS = 5     # minimum number of days a team plays a doubleheader
MAX_DOUBLE_HEADERS = 5     # maximum number of doubleheader days per team

# -------------------------------
# Data Loading Functions
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
            dt = datetime.strptime(row[0].strip(), '%Y-%m-%d')
            slot = row[1].strip()  # e.g., "10:30 PM"
            field = row[2].strip()
            field_availability.append((dt, slot, field))
    # Sort so that Sundays come first, then by date then by time
    field_availability.sort(key=lambda x: ((0 if x[0].weekday() == 6 else 1),
                                           x[0],
                                           datetime.strptime(x[1], "%I:%M %p")))
    return field_availability

def load_team_blackouts(file_path):
    blackouts = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)
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
# Build a mapping of dates to available timeslots and fields
# -------------------------------
def build_date_mapping(field_availability):
    date_map = defaultdict(list)
    for dt, slot, field in field_availability:
        d = dt.date()
        date_map[d].append((slot, field))
    # Sort each day’s timeslots by time
    for d in date_map:
        date_map[d].sort(key=lambda x: datetime.strptime(x[0], "%I:%M %p"))
    return date_map

# -------------------------------
# Matchup Generation Functions (mostly as before)
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
            # One extra game in one random direction
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
# PHASE 1: Date Assignment (with Doubleheader Handling)
# -------------------------------
def feasible_dates_for_matchup(matchup, team_calendar, team_availability, team_blackouts, date_map):
    t1, t2 = matchup
    feasible = []
    for d in date_map.keys():
        day = d.strftime('%a')
        if day not in team_availability.get(t1, set()) or day not in team_availability.get(t2, set()):
            continue
        if d in team_blackouts.get(t1, set()) or d in team_blackouts.get(t2, set()):
            continue
        # Check weekly limit and minimum gap for each team:
        def team_ok(team):
            games_on_d = team_calendar[team].get(d, [])
            if len(games_on_d) >= 2:  # already two games this day
                return False
            # Check minimum gap between d and other game dates
            for other_d in team_calendar[team]:
                if other_d == d:
                    continue
                if abs((d - other_d).days) < MIN_GAP:
                    return False
            # Check weekly limit
            week = d.isocalendar()[1]
            weekly_games = sum(len(team_calendar[team][date])
                               for date in team_calendar[team]
                               if date.isocalendar()[1] == week)
            if weekly_games >= WEEKLY_GAME_LIMIT:
                return False
            return True

        if team_ok(t1) and team_ok(t2):
            feasible.append(d)
    return feasible

def assign_dates_to_matchups(matchups, team_availability, team_blackouts, date_map):
    # team_calendar: maps team -> {date: [matchups assigned on that date]}
    team_calendar = defaultdict(lambda: defaultdict(list))
    matchup_date_assignment = {}
    unscheduled = []

    # Sort matchups by number of currently feasible dates (least flexible first)
    def flexibility(m):
        return len(feasible_dates_for_matchup(m, team_calendar, team_availability, team_blackouts, date_map))
    sorted_matchups = sorted(matchups, key=flexibility)
    
    for m in sorted_matchups:
        feasible = feasible_dates_for_matchup(m, team_calendar, team_availability, team_blackouts, date_map)
        if not feasible:
            unscheduled.append(m)
            continue
        # If one team already has a game on a candidate date, try to choose one that would serve as a doubleheader
        best = min(feasible, key=lambda d: sum(len(team_calendar[t].get(d, [])) for t in m))
        matchup_date_assignment[m] = best
        for t in m:
            team_calendar[t][best].append(m)
    return matchup_date_assignment, team_calendar, unscheduled

# -------------------------------
# PHASE 2: Timeslot Assignment (Enforcing Doubleheader Back-to-Back)
# -------------------------------
def assign_timeslots_for_date(games, timeslots):
    """
    games: list of matchups scheduled on the same date.
    timeslots: list of (slot, field) available on that date.
    For teams with two games, ensure the assigned timeslot indices are consecutive.
    Returns a dictionary mapping matchup to (slot, field) or None if no valid assignment is found.
    """
    n = len(games)
    if n > len(timeslots):
        return None
    assignment = {}
    
    # Backtracking over game indices
    def backtrack(i, used, curr_assign):
        if i == n:
            # Check doubleheader consecutiveness: For each team with 2 games, their slot indices must be consecutive.
            team_slots = defaultdict(list)
            for m, idx in curr_assign.items():
                t1, t2 = m
                team_slots[t1].append(idx)
                team_slots[t2].append(idx)
            for team, indices in team_slots.items():
                if len(indices) == 2 and abs(indices[0] - indices[1]) != 1:
                    return None
                if len(indices) > 2:
                    return None
            return curr_assign
        m = games[i]
        for idx in range(len(timeslots)):
            if idx in used:
                continue
            new_assign = curr_assign.copy()
            new_assign[m] = idx
            used.add(idx)
            result = backtrack(i+1, used, new_assign)
            if result is not None:
                return result
            used.remove(idx)
        return None

    assignment_result = backtrack(0, set(), {})
    if assignment_result is None:
        return None
    # Map each matchup to its assigned (slot, field)
    final_assignment = {}
    for m, idx in assignment_result.items():
        final_assignment[m] = timeslots[idx]
    return final_assignment

def assign_all_timeslots(matchup_date_assignment, date_map):
    # Group matchups by date
    timeslot_assignment = {}
    games_by_date = defaultdict(list)
    for matchup, d in matchup_date_assignment.items():
        games_by_date[d].append(matchup)
    for d, games in games_by_date.items():
        timeslots = date_map[d]
        assignment = assign_timeslots_for_date(games, timeslots)
        if assignment is None:
            # Fallback: assign the earliest available unique timeslot for each game (without enforcing doubleheader consecutiveness)
            assignment = {}
            used = set()
            for m in games:
                for idx, ts in enumerate(timeslots):
                    if idx not in used:
                        used.add(idx)
                        assignment[m] = ts
                        break
        for m, ts in assignment.items():
            timeslot_assignment[(m, d)] = ts
    return timeslot_assignment

# -------------------------------
# PHASE 3: Home/Away Assignment and Adjustment
# -------------------------------
def initial_home_away_assignment(matchup_date_assignment):
    ha_assignment = {}
    for m in matchup_date_assignment:
        t1, t2 = m
        if random.random() < 0.5:
            ha_assignment[m] = (t1, t2)
        else:
            ha_assignment[m] = (t2, t1)
    return ha_assignment

def compute_team_stats(ha_assignment):
    stats = defaultdict(lambda: {'home_games': 0, 'away_games': 0, 'total_games': 0})
    for m, (home, away) in ha_assignment.items():
        stats[home]['home_games'] += 1
        stats[home]['total_games'] += 1
        stats[away]['away_games'] += 1
        stats[away]['total_games'] += 1
    return stats

def adjust_home_away(ha_assignment, team_stats):
    adjusted = ha_assignment.copy()
    for m, (home, away) in ha_assignment.items():
        if team_stats[home]['home_games'] > HOME_AWAY_BALANCE and team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
            adjusted[m] = (away, home)
            team_stats[home]['home_games'] -= 1
            team_stats[home]['away_games'] += 1
            team_stats[away]['home_games'] += 1
            team_stats[away]['away_games'] -= 1
    return adjusted

# -------------------------------
# Fill Missing Games (if teams have less than MAX_GAMES)
# -------------------------------
def fill_missing_games(matchup_date_assignment, team_calendar, team_availability, team_blackouts, date_map):
    # Create a pool of potential fill games.
    # For each team that is short on games, schedule additional matchups with any opponent that still needs games.
    fill_matchups = []
    teams = set()
    for m in matchup_date_assignment:
        teams.update(m)
    teams = list(teams)
    team_game_count = defaultdict(int)
    for team in teams:
        # Count total games from team_calendar
        team_game_count[team] = sum(len(games) for games in team_calendar[team].values())
    for i in range(len(teams)):
        for j in range(i+1, len(teams)):
            t1, t2 = teams[i], teams[j]
            # Allow fill game if either team needs more games.
            if team_game_count[t1] < MAX_GAMES or team_game_count[t2] < MAX_GAMES:
                fill_matchups.append((t1, t2))
                fill_matchups.append((t2, t1))
    random.shuffle(fill_matchups)
    unscheduled = []
    for m in fill_matchups:
        # If already scheduled enough games for both teams, skip.
        if team_game_count[m[0]] >= MAX_GAMES and team_game_count[m[1]] >= MAX_GAMES:
            continue
        feasible = feasible_dates_for_matchup(m, team_calendar, team_availability, team_blackouts, date_map)
        if not feasible:
            unscheduled.append(m)
            continue
        chosen_date = min(feasible, key=lambda d: sum(len(team_calendar[t].get(d, [])) for t in m))
        matchup_date_assignment[m] = chosen_date
        for t in m:
            team_calendar[t][chosen_date].append(m)
            team_game_count[t] += 1
    return matchup_date_assignment, team_calendar

# -------------------------------
# Output and Summary Functions
# -------------------------------
def output_schedule_to_csv(schedule, output_file):
    sorted_schedule = sorted(schedule, key=lambda game: (
        game[0],
        datetime.strptime(game[1], "%I:%M %p")
    ))
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Diamond", "Home Team", "Home Division", "Away Team", "Away Division"])
        for game in sorted_schedule:
            date, slot, field, home, away = game
            writer.writerow([date.strftime('%Y-%m-%d'), slot, field, home, home[0], away, away[0]])

def print_schedule_summary(team_stats):
    table = PrettyTable()
    table.field_names = ["Division", "Team", "Total Games", "Home Games", "Away Games"]
    for team, stats in sorted(team_stats.items()):
        table.add_row([team[0], team, stats['total_games'], stats['home_games'], stats['away_games']])
    print("\nSchedule Summary:")
    print(table)

def print_doubleheader_summary(team_calendar):
    table = PrettyTable()
    table.field_names = ["Team", "Doubleheader Days"]
    for team, sched in sorted(team_calendar.items()):
        dh_count = sum(1 for d, games in sched.items() if len(games) == 2)
        table.add_row([team, dh_count])
    print("\nDoubleheader Summary (Days with 2 games):")
    print(table)

def generate_matchup_table(schedule, division_teams):
    matchup_count = defaultdict(lambda: defaultdict(int))
    for game in schedule:
        # Each game is now (date, slot, field, home, away)
        _, _, _, home, away = game
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
# MAIN FUNCTION: Orchestrate all phases
# -------------------------------
def main():
    # Load input data
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    team_blackouts = load_team_blackouts('team_blackouts.csv')
    
    # Debug prints
    print("\nTeam Availability:")
    for team, days in team_availability.items():
        print(f"{team}: {', '.join(days)}")
    print("\nField Availability:")
    for entry in field_availability:
        print(f"Field Slot: {entry}")
    print("\nTeam Blackouts:")
    for team, dates in team_blackouts.items():
        print(f"{team}: {', '.join(str(d) for d in dates)}")
    
    # Define division teams and generate matchups
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")
    
    # Build date mapping from field availability
    date_map = build_date_mapping(field_availability)
    
    # PHASE 1: Date Assignment (with doubleheader considerations)
    matchup_date_assignment, team_calendar, unscheduled_phase1 = assign_dates_to_matchups(
        matchups, team_availability, team_blackouts, date_map
    )
    if unscheduled_phase1:
        print("Warning: The following matchups could not be scheduled in Phase 1:", unscheduled_phase1)
    
    # Fill missing games if teams have fewer than MAX_GAMES
    matchup_date_assignment, team_calendar = fill_missing_games(
        matchup_date_assignment, team_calendar, team_availability, team_blackouts, date_map
    )
    
    # PHASE 2: Timeslot Assignment
    timeslot_assignment = assign_all_timeslots(matchup_date_assignment, date_map)
    
    # PHASE 3: Home/Away Assignment and Adjustment
    ha_assignment = initial_home_away_assignment(matchup_date_assignment)
    team_stats = compute_team_stats(ha_assignment)
    ha_assignment = adjust_home_away(ha_assignment, team_stats)
    
    # Build final schedule list: (date, slot, field, home, away)
    schedule = []
    for matchup, d in matchup_date_assignment.items():
        ts = timeslot_assignment.get((matchup, d))
        if ts is None:
            continue
        slot, field = ts
        home, away = ha_assignment[matchup]
        schedule.append((d, slot, field, home, away))
    
    # Output schedule CSV and summaries
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    print_doubleheader_summary(team_calendar)
    generate_matchup_table(schedule, division_teams)
    
    # Final checks for doubleheaders
    under_dh = [team for team, sched in team_calendar.items() if sum(1 for d, games in sched.items() if len(games)==2) < MIN_DOUBLE_HEADERS]
    if under_dh:
        print(f"\nCritical: The following teams did not meet the minimum doubleheader sessions ({MIN_DOUBLE_HEADERS} required): {under_dh}")

if __name__ == "__main__":
    main()
