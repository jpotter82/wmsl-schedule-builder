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
MIN_GAP = 2                # minimum days gap between games on different dates
MIN_DOUBLE_HEADERS = 5     # minimum number of doubleheader sessions per team (each session = 2 games)
MAX_DOUBLE_HEADERS = 5     # maximum allowed doubleheader days per team

# -------------------------------
# Data Loading Functions (same as before)
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
            slot = row[1].strip()  # e.g., "10:30 PM"
            field = row[2].strip()
            field_availability.append((date, slot, field))
    # Prioritize Sundays (weekday()==6) then sort by date then by time.
    field_availability.sort(key=lambda x: ((0 if x[0].weekday()==6 else 1),
                                           x[0],
                                           datetime.strptime(x[1].strip(), "%I:%M %p")))
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

# -------------------------------
# Build Date Mapping from Field Availability
# -------------------------------
def build_date_mapping(field_availability):
    date_map = defaultdict(list)
    for date, slot, field in field_availability:
        d = date.date()
        date_map[d].append((slot, field))
    # Sort timeslots by time (using 12-hour time parsing)
    for d in date_map:
        date_map[d].sort(key=lambda x: datetime.strptime(x[0], "%I:%M %p"))
    return date_map

# -------------------------------
# Matchup Generation (reuse your existing logic)
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
# PHASE 1: Date Assignment for Matchups
# -------------------------------
def feasible_dates_for_matchup(matchup, team_schedule, team_availability, team_blackouts, date_map):
    t1, t2 = matchup
    feasible = []
    for d in date_map.keys():
        day_of_week = d.strftime('%a')
        if day_of_week not in team_availability.get(t1, set()) or day_of_week not in team_availability.get(t2, set()):
            continue
        if d in team_blackouts.get(t1, set()) or d in team_blackouts.get(t2, set()):
            continue
        # For each team, allow at most 2 games per day (for doubleheaders)
        def team_ok(team):
            games_on_date = team_schedule[team].get(d, 0)
            if games_on_date >= 2:
                return False
            # Check min gap with other dates (if not playing on the same day)
            for scheduled_date in team_schedule[team]:
                if scheduled_date == d:
                    continue
                if abs((d - scheduled_date).days) < MIN_GAP:
                    return False
            # Check weekly limit
            week = d.isocalendar()[1]
            week_games = sum(count for date_key, count in team_schedule[team].items() if date_key.isocalendar()[1] == week)
            if week_games >= WEEKLY_GAME_LIMIT:
                return False
            return True
        if team_ok(t1) and team_ok(t2):
            feasible.append(d)
    return feasible

def assign_dates_to_matchups(matchups, team_availability, team_blackouts, date_map):
    # team_schedule: maps team -> {date: count_of_games_on_that_date}
    team_schedule = defaultdict(lambda: defaultdict(int))
    matchup_date_assignment = {}
    unscheduled = []
    # Order matchups by increasing number of feasible dates
    sorted_matchups = sorted(matchups, key=lambda m: len(feasible_dates_for_matchup(m, team_schedule, team_availability, team_blackouts, date_map)))
    for m in sorted_matchups:
        feasible = feasible_dates_for_matchup(m, team_schedule, team_availability, team_blackouts, date_map)
        if not feasible:
            unscheduled.append(m)
            continue
        # Pick the date with the fewest games scheduled for the teams (heuristic)
        chosen_date = min(feasible, key=lambda d: sum(team_schedule[t].get(d, 0) for t in m))
        matchup_date_assignment[m] = chosen_date
        for t in m:
            team_schedule[t][chosen_date] += 1
    return matchup_date_assignment, team_schedule, unscheduled

# -------------------------------
# PHASE 2: Timeslot Assignment
# -------------------------------
def assign_timeslots_for_date(games, timeslots):
    """
    games: list of tuples (matchup, placeholder)
    timeslots: list of available (slot, field) for the date.
    If a team plays twice, the two timeslot indices must be consecutive.
    Returns a mapping from matchup to assigned (slot, field) or None on failure.
    """
    n = len(games)
    if n > len(timeslots):
        return None  # not enough slots
    assignment = {}
    # Backtracking to assign a unique timeslot index to each game.
    def backtrack(i, used, current_assignment):
        if i == n:
            # For every team with two games on this date, check for consecutiveness.
            team_slots = defaultdict(list)
            for game, slot_index in current_assignment:
                t1, t2 = game
                team_slots[t1].append(slot_index)
                team_slots[t2].append(slot_index)
            for team, indices in team_slots.items():
                if len(indices) == 2:
                    if abs(indices[0] - indices[1]) != 1:
                        return None
                elif len(indices) > 2:
                    return None
            return current_assignment
        for idx in range(len(timeslots)):
            if idx in used:
                continue
            new_assignment = current_assignment + [(games[i][0], idx)]
            # Early check on doubleheader teams
            team_slots = defaultdict(list)
            for game, slot_index in new_assignment:
                t1, t2 = game
                team_slots[t1].append(slot_index)
                team_slots[t2].append(slot_index)
            valid = True
            for team, indices in team_slots.items():
                if len(indices) == 2 and abs(indices[0] - indices[1]) != 1:
                    valid = False
                    break
                if len(indices) > 2:
                    valid = False
                    break
            if not valid:
                continue
            used.add(idx)
            result = backtrack(i+1, used, new_assignment)
            if result is not None:
                return result
            used.remove(idx)
        return None

    result = backtrack(0, set(), [])
    if result is None:
        return None
    # Map each matchup to the (slot, field) from the chosen timeslot index.
    game_slot_assignment = {}
    for game, slot_index in result:
        game_slot_assignment[game] = timeslots[slot_index]
    return game_slot_assignment

def assign_timeslots(matchup_date_assignment, date_map):
    """
    For each matchup assigned a date, assign a specific timeslot (and field)
    from the available slots on that date.
    Returns a mapping {(matchup, date): (slot, field)}.
    """
    # Group games by date
    games_by_date = defaultdict(list)
    for matchup, d in matchup_date_assignment.items():
        games_by_date[d].append((matchup, {}))
    timeslot_assignment = {}
    for d, games in games_by_date.items():
        timeslots = date_map[d]
        assigned = assign_timeslots_for_date(games, timeslots)
        if assigned is None:
            # Fallback: assign earliest available timeslot for each game (ignoring doubleheader consecutiveness)
            assigned = {}
            used = set()
            for matchup, _ in games:
                for idx, ts in enumerate(timeslots):
                    if idx not in used:
                        used.add(idx)
                        assigned[matchup] = ts
                        break
        timeslot_assignment.update({(game, d): ts for game, ts in assigned.items()})
    return timeslot_assignment

# -------------------------------
# PHASE 3: Home/Away Assignment and Adjustment
# -------------------------------
def initial_home_away_assignment(matchup_date_assignment):
    """Randomly assign home/away roles for each matchup."""
    ha_assignment = {}
    for matchup in matchup_date_assignment.keys():
        t1, t2 = matchup
        if random.random() < 0.5:
            ha_assignment[matchup] = (t1, t2)
        else:
            ha_assignment[matchup] = (t2, t1)
    return ha_assignment

def compute_team_stats(ha_assignment):
    stats = defaultdict(lambda: {'home_games': 0, 'away_games': 0, 'total_games': 0})
    for matchup, (home, away) in ha_assignment.items():
        stats[home]['home_games'] += 1
        stats[home]['total_games'] += 1
        stats[away]['away_games'] += 1
        stats[away]['total_games'] += 1
    return stats

def adjust_home_away(ha_assignment, team_stats):
    """Try to swap home/away roles when one team exceeds the home game balance."""
    adjusted = ha_assignment.copy()
    for matchup, (home, away) in ha_assignment.items():
        if team_stats[home]['home_games'] > HOME_AWAY_BALANCE and team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
            # swap roles
            adjusted[matchup] = (away, home)
            team_stats[home]['home_games'] -= 1
            team_stats[home]['away_games'] += 1
            team_stats[away]['home_games'] += 1
            team_stats[away]['away_games'] -= 1
    return adjusted

# -------------------------------
# Final Output Functions (same as before)
# -------------------------------
def output_schedule_to_csv(schedule, output_file):
    # Sort schedule by date, then by time then by field.
    sorted_schedule = sorted(schedule, key=lambda game: (
        game[0],
        datetime.strptime(game[1].strip(), "%I:%M %p"),
        game[2]
    ))
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Diamond", "Home Team", "Home Division", "Away Team", "Away Division"])
        for game in sorted_schedule:
            date, slot, field, home, home_div, away, away_div = game
            writer.writerow([date.strftime('%Y-%m-%d'), slot, field, home, home[0], away, away[0]])

def print_schedule_summary(team_stats):
    table = PrettyTable()
    table.field_names = ["Division", "Team", "Total Games", "Home Games", "Away Games"]
    for team, stats in sorted(team_stats.items()):
        table.add_row([team[0], team, stats['total_games'], stats['home_games'], stats['away_games']])
    print("\nSchedule Summary:")
    print(table)

def print_doubleheader_summary(team_schedule):
    # Count days where a team plays 2 games
    table = PrettyTable()
    table.field_names = ["Team", "Doubleheader Days"]
    doubleheader = {}
    for team, schedule in team_schedule.items():
        count = sum(1 for d, cnt in schedule.items() if cnt == 2)
        doubleheader[team] = count
        table.add_row([team, count])
    print("\nDoubleheader Summary (Days with 2 games):")
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
# Main Function: Orchestrate the 3 Phases
# -------------------------------
def main():
    # Load data
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    team_blackouts = load_team_blackouts('team_blackouts.csv')
    
    # Debug prints (optional)
    print("\nTeam Availability:")
    for team, days in team_availability.items():
        print(f"{team}: {', '.join(days)}")
        
    print("\nField Availability:")
    for entry in field_availability:
        print(f"Field Slot: {entry}")
    
    print("\nTeam Blackouts:")
    for team, dates in team_blackouts.items():
        print(f"{team}: {', '.join(str(d) for d in dates)}")
    
    # Define divisions and generate matchups
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")
    
    # Build mapping of dates to available timeslots
    date_map = build_date_mapping(field_availability)
    
    # PHASE 1: Date Assignment
    matchup_date_assignment, team_schedule, unscheduled = assign_dates_to_matchups(
        matchups, team_availability, team_blackouts, date_map
    )
    if unscheduled:
        print("Warning: The following matchups could not be scheduled in Phase 1:", unscheduled)
    
    # PHASE 2: Timeslot Assignment
    timeslot_assignment = assign_timeslots(matchup_date_assignment, date_map)
    
    # PHASE 3: Home/Away Assignment and Adjustment
    ha_assignment = initial_home_away_assignment(matchup_date_assignment)
    team_stats = compute_team_stats(ha_assignment)
    ha_assignment = adjust_home_away(ha_assignment, team_stats)
    
    # Combine final schedule entries
    schedule = []
    for matchup, d in matchup_date_assignment.items():
        ts = timeslot_assignment.get((matchup, d))
        if ts is None:
            continue
        slot, field = ts
        home, away = ha_assignment[matchup]
        schedule.append((d, slot, field, home, home[0], away, away[0]))
    
    # Write schedule to CSV and print summaries
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    print_doubleheader_summary(team_schedule)
    generate_matchup_table(schedule, division_teams)
    
    # Check if teams meet the minimum doubleheader sessions.
    under_dh = [team for team, sched in team_schedule.items() if sum(1 for d, cnt in sched.items() if cnt == 2) < MIN_DOUBLE_HEADERS]
    if under_dh:
        print(f"\nCritical: The following teams did not meet the minimum doubleheader sessions ({MIN_DOUBLE_HEADERS} required): {under_dh}")

if __name__ == "__main__":
    main()
