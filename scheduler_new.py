import csv
import itertools
import random
from datetime import datetime, timedelta
from collections import defaultdict
from prettytable import PrettyTable

# Import OR-Tools CP-SAT solver
from ortools.sat.python import cp_model

# -------------------------------
# Configurable parameters
# -------------------------------
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11
WEEKLY_GAME_LIMIT = 2      # max games per team per week (soft constraint with penalty)
MIN_GAP = 2                # minimum days between game dates (if not a doubleheader)
MIN_DOUBLE_HEADERS = 5     # minimum number of doubleheader sessions per team (each session = 2 games)
MAX_DOUBLE_HEADERS = 5     # maximum allowed doubleheader days per team

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
            slot = row[1].strip()  # e.g., "10:30 PM"
            field = row[2].strip()
            field_availability.append((date, slot, field))
    # Custom sort: Prioritize Sundays then by date then by time.
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
            # Deterministically assign the extra game:
            matchups.append((team1, team2))
            matchups.append((team2, team1))
            matchups.append((team1, team2))
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

# -------------------------------
# Inter-division matchup generation
# -------------------------------
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
        matchups.append((t1, t2))
    return matchups

def generate_full_matchups(division_teams):
    full_matchups = []
    for div, teams in division_teams.items():
        full_matchups.extend(generate_intra_division_matchups(div, teams))
    inter_AB = generate_inter_division_matchups('A', 'B', division_teams['A'], division_teams['B'])
    full_matchups.extend(inter_AB)
    inter_BC = generate_inter_division_matchups('B', 'C', division_teams['B'], division_teams['C'])
    full_matchups.extend(inter_BC)
    # For reproducibility, sort the matchups deterministically.
    full_matchups.sort()
    return full_matchups

# -------------------------------
# CP-based Scheduling Function with Soft Constraints
# -------------------------------
def schedule_games_cp(matchups, team_availability, field_availability, team_blackouts, seed=42):
    """
    Schedule all predetermined matchups using a CP-SAT model.
    Soft penalties are added so that even if 100% feasibility is not reached,
    the solver returns the best schedule.
    """
    random.seed(seed)
    # Precompute list of slots and mapping: index -> (date, time, field)
    slot_list = field_availability  # Each element: (datetime, slot, field)
    num_slots = len(slot_list)
    slot_data = {i: slot_list[i] for i in range(num_slots)}
    
    # Precompute “date diff” values: number of days since a base date.
    base_date = slot_list[0][0].date()
    date_diffs = []
    for i in range(num_slots):
        d = slot_data[i][0].date()
        diff = (d - base_date).days
        date_diffs.append(diff)
    
    # Precompute timeslot ordering (minutes since midnight)
    timeslot_orders = []
    for i in range(num_slots):
        t = datetime.strptime(slot_data[i][1], "%I:%M %p")
        order = t.hour * 60 + t.minute
        timeslot_orders.append(order)
    
    # For each matchup, precompute feasible slot indices (by team availability and blackouts)
    feasible_slots = []
    for matchup in matchups:
        team1, team2 = matchup
        feas = []
        for i, (dt, slot, field) in enumerate(slot_list):
            d = dt.date()
            day_str = dt.strftime('%a')
            if day_str not in team_availability.get(team1, set()) or day_str not in team_availability.get(team2, set()):
                continue
            if d in team_blackouts.get(team1, set()) or d in team_blackouts.get(team2, set()):
                continue
            feas.append(i)
        # Fallback to full domain if no feasible slot was found.
        if not feas:
            print(f"Warning: No feasible slot for matchup {matchup}; using all slots as fallback.")
            feas = list(range(num_slots))
        feasible_slots.append(feas)
    
    model = cp_model.CpModel()
    num_matches = len(matchups)
    
    # Decision variables: For each match, assign a slot index (from its feasible domain)
    slot_vars = []
    for m in range(num_matches):
        domain = feasible_slots[m]
        var = model.NewIntVarFromDomain(cp_model.Domain.FromValues(domain), f'slot_{m}')
        slot_vars.append(var)
    
    # For each match, decide whether to flip home/away.
    flip_vars = []
    for m in range(num_matches):
        flip = model.NewBoolVar(f'flip_{m}')
        flip_vars.append(flip)
    
    # Each slot should be used at most once.
    model.AddAllDifferent(slot_vars)
    
    # Mapping: team -> list of match indices in which it participates.
    team_to_matches = defaultdict(list)
    for m, (team1, team2) in enumerate(matchups):
        team_to_matches[team1].append(m)
        team_to_matches[team2].append(m)
    
    penalty_terms = []
    
    # -------------------------------
    # Soft Minimum Gap Constraint (with slack)
    # For each team, for every pair of matches, if scheduled on different days, we allow a gap violation.
    for team, match_indices in team_to_matches.items():
        for i in range(len(match_indices)):
            for j in range(i+1, len(match_indices)):
                m1 = match_indices[i]
                m2 = match_indices[j]
                # Extract dates via element constraints.
                date1 = model.NewIntVar(0, 10000, f'date_{team}_{m1}')
                date2 = model.NewIntVar(0, 10000, f'date_{team}_{m2}')
                model.AddElement(slot_vars[m1], date_diffs, date1)
                model.AddElement(slot_vars[m2], date_diffs, date2)
                # Define a boolean: same_day
                same_day = model.NewBoolVar(f'same_day_{team}_{m1}_{m2}')
                model.Add(date1 == date2).OnlyEnforceIf(same_day)
                model.Add(date1 != date2).OnlyEnforceIf(same_day.Not())
                # Compute difference and its absolute value.
                diff = model.NewIntVar(-10000, 10000, f'diff_{team}_{m1}_{m2}')
                model.Add(diff == date1 - date2)
                diff_abs = model.NewIntVar(0, 10000, f'diff_abs_{team}_{m1}_{m2}')
                model.AddAbsEquality(diff_abs, diff)
                # Introduce a slack variable for gap violation.
                gap_slack = model.NewIntVar(0, MIN_GAP, f'gap_slack_{team}_{m1}_{m2}')
                # If not same day, enforce diff_abs + gap_slack >= MIN_GAP.
                model.Add(diff_abs + gap_slack >= MIN_GAP).OnlyEnforceIf(same_day.Not())
                # If same day, set slack to zero (no gap penalty for doubleheaders).
                model.Add(gap_slack == 0).OnlyEnforceIf(same_day)
                penalty_terms.append(gap_slack)
    
    # -------------------------------
    # Weekly Game Limit (soft constraint)
    week_numbers = []
    for i in range(num_slots):
        d = slot_data[i][0].date()
        week_num = d.isocalendar()[1]
        week_numbers.append(week_num)
    
    for team, match_indices in team_to_matches.items():
        for week in range(1, 54):
            indicators = []
            for m in match_indices:
                indicator = model.NewBoolVar(f'week_{week}_team_{team}_match_{m}')
                allowed = [i for i in feasible_slots[m] if week_numbers[i] == week]
                if allowed:
                    # When slot is assigned one of the allowed values, indicator is 1.
                    model.AddAllowedAssignments([slot_vars[m]], [[val] for val in allowed]).OnlyEnforceIf(indicator)
                    model.AddForbiddenAssignments([slot_vars[m]], [[val] for val in allowed]).OnlyEnforceIf(indicator.Not())
                else:
                    model.Add(indicator == 0)
                indicators.append(indicator)
            week_count = model.NewIntVar(0, len(match_indices), f'week_count_{team}_{week}')
            model.Add(week_count == sum(indicators))
            excess = model.NewIntVar(0, len(match_indices), f'excess_{team}_{week}')
            model.Add(excess >= week_count - WEEKLY_GAME_LIMIT)
            penalty_terms.append(excess)
    
    # -------------------------------
    # Home/Away Balance (soft constraint)
    for team, match_indices in team_to_matches.items():
        home_indicators = []
        for m in match_indices:
            team1, team2 = matchups[m]
            home = model.NewBoolVar(f'home_{team}_{m}')
            if team == team1:
                model.Add(flip_vars[m] == 0).OnlyEnforceIf(home)
                model.Add(flip_vars[m] == 1).OnlyEnforceIf(home.Not())
            elif team == team2:
                model.Add(flip_vars[m] == 1).OnlyEnforceIf(home)
                model.Add(flip_vars[m] == 0).OnlyEnforceIf(home.Not())
            home_indicators.append(home)
        home_count = model.NewIntVar(0, len(match_indices), f'home_count_{team}')
        model.Add(home_count == sum(home_indicators))
        diff_home = model.NewIntVar(-len(match_indices), len(match_indices), f'home_diff_{team}')
        model.Add(diff_home == home_count - HOME_AWAY_BALANCE)
        abs_diff_home = model.NewIntVar(0, len(match_indices), f'abs_home_diff_{team}')
        model.AddAbsEquality(abs_diff_home, diff_home)
        penalty_terms.append(abs_diff_home)
    
    # (Additional soft constraints for doubleheaders could be added similarly.)
    
    # -------------------------------
    # Objective: minimize total penalty.
    model.Minimize(sum(penalty_terms))
    
    # -------------------------------
    # Solve the CP model.
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = seed
    status = solver.Solve(model)
    
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        schedule = []
        for m in range(num_matches):
            slot_index = solver.Value(slot_vars[m])
            dt, timeslot, field = slot_data[slot_index]
            team1, team2 = matchups[m]
            if solver.Value(flip_vars[m]) == 0:
                home, away = team1, team2
            else:
                home, away = team2, team1
            schedule.append((dt, timeslot, field, home, home[0], away, away[0]))
        # Optionally, you could print the total penalty value:
        print("Total penalty:", solver.ObjectiveValue())
        return schedule
    else:
        print("No solution found, but returning best partial solution if available.")
        return None

# -------------------------------
# Output functions
# -------------------------------
def output_schedule_to_csv(schedule, output_file):
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
            writer.writerow([date.strftime('%Y-%m-%d'), slot, field, home, home_div, away, away_div])

def print_schedule_summary(team_stats):
    table = PrettyTable()
    table.field_names = ["Division", "Team", "Total Games", "Home Games", "Away Games"]
    for team, stats in sorted(team_stats.items()):
        table.add_row([team[0], team, stats['total_games'], stats['home_games'], stats['away_games']])
    print("\nSchedule Summary:")
    print(table)

def print_doubleheader_summary(doubleheader_count):
    table = PrettyTable()
    table.field_names = ["Team", "Doubleheader Days"]
    for team, count in sorted(doubleheader_count.items()):
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
# Main function
# -------------------------------
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    team_blackouts = load_team_blackouts('team_blackouts.csv')
    
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
        
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    
    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")
    
    # Use the CP-based scheduler to assign each matchup a field slot and home/away decision.
    schedule = schedule_games_cp(matchups, team_availability, field_availability, team_blackouts, seed=42)
    if schedule is None:
        print("Scheduling failed.")
        return
    
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    
    # Recalculate team stats and doubleheader counts from the schedule.
    team_stats = defaultdict(lambda: {'total_games': 0, 'home_games': 0, 'away_games': 0})
    doubleheader_count = defaultdict(int)
    team_game_days = defaultdict(lambda: defaultdict(int))
    for game in schedule:
        dt, slot, field, home, _, away, _ = game
        for team in (home, away):
            team_stats[team]['total_games'] += 1
        team_stats[home]['home_games'] += 1
        team_stats[away]['away_games'] += 1
        d = dt.date()
        team_game_days[home][d] += 1
        team_game_days[away][d] += 1
    for team, days in team_game_days.items():
        for d, count in days.items():
            if count == 2:
                doubleheader_count[team] += 1
                
    print_schedule_summary(team_stats)
    print_doubleheader_summary(doubleheader_count)
    generate_matchup_table(schedule, division_teams)

if __name__ == "__main__":
    main()
