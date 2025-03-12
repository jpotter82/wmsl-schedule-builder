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
MAX_RETRIES = 20000        # scheduling backtracking limit
MIN_GAP = 2                # minimum days between game dates
MIN_DOUBLE_HEADERS = 5     # minimum number of doubleheader sessions per team (each session = 2 games)
MAX_DOUBLE_HEADERS = 6     # maximum allowed doubleheader days per team

# -------------------------------
# Helper Functions
# -------------------------------
def min_gap_ok(team, d, team_game_days):
    """Return True if 'team' has no game scheduled within MIN_GAP days of date d."""
    for gd in team_game_days[team]:
        if gd != d and abs((d - gd).days) < MIN_GAP:
            return False
    return True

def is_legal(matchup):
    """
    Returns True if the matchup is legal.
    Illegal: pairing an A–team with a C–team.
    (Assumes team names begin with their division letter.)
    """
    a, b = matchup
    if (a[0]=='A' and b[0]=='C') or (a[0]=='C' and b[0]=='A'):
        return False
    return True

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
    # Custom sort: Prioritize Sundays (weekday()==6 -> Sunday) then by date then by time.
    field_availability.sort(key=lambda x: ((0 if x[0].weekday()==6 else 1),
                                           x[0],
                                           datetime.strptime(x[1].strip(), "%I:%M %p")))
    return field_availability

def load_team_blackouts(file_path):
    """
    Loads blackout dates from a CSV file.
    CSV format: Team, Date1, Date2, Date3, ...
    Dates must be in YYYY-MM-DD format.
    Returns a dict mapping team to a set of date objects.
    """
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
        matchups.append((t1, t2) if random.random() < 0.5 else (t2, t1))
    return matchups

# -------------------------------
# Combine full matchup list
# -------------------------------
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
# Scheduling functions (with timeslot and doubleheader enforcement)
# -------------------------------
def schedule_games(matchups, team_availability, field_availability, team_blackouts):
    schedule = []
    team_stats = defaultdict(lambda: {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'weekly_games': defaultdict(int)
    })
    used_slots = {}  # (date, slot, field) -> True
    team_game_days = defaultdict(lambda: defaultdict(int))  # team -> date -> count of games
    team_game_slots = defaultdict(lambda: defaultdict(list))  # team -> date -> list of timeslot strings scheduled
    team_doubleheader_opponents = defaultdict(lambda: defaultdict(set))  # team -> date -> set(opponents)
    doubleheader_count = defaultdict(int)  # team -> number of days with 2 games

    # Build mapping: date -> sorted list of timeslot strings available.
    timeslots_by_date = defaultdict(list)
    for date, slot, field in field_availability:
        d = date.date()
        if slot not in timeslots_by_date[d]:
            timeslots_by_date[d].append(slot)
    for d in timeslots_by_date:
        timeslots_by_date[d].sort(key=lambda s: datetime.strptime(s.strip(), "%I:%M %p"))

    unscheduled = matchups[:]  # predetermined legal matchups
    retry_count = 0
    while unscheduled and retry_count < MAX_RETRIES:
        progress_made = False
        for date, slot, field in field_availability:
            if used_slots.get((date, slot, field), False):
                continue
            d = date.date()
            day_of_week = date.strftime('%a')
            week_num = date.isocalendar()[1]
            for matchup in unscheduled[:]:
                home, away = matchup
                if day_of_week not in team_availability.get(home, set()) or day_of_week not in team_availability.get(away, set()):
                    continue
                if d in team_blackouts.get(home, set()) or d in team_blackouts.get(away, set()):
                    continue
                if team_stats[home]['total_games'] >= MAX_GAMES or team_stats[away]['total_games'] >= MAX_GAMES:
                    continue
                if (team_stats[home]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or
                    team_stats[away]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT):
                    continue
                if not (min_gap_ok(home, d, team_game_days) and min_gap_ok(away, d, team_game_days)):
                    continue
                if slot in team_game_slots[home][d] or slot in team_game_slots[away][d]:
                    continue
                # If a team already has a game today, the new game must be in the immediate next timeslot.
                valid_slot = True
                for team in (home, away):
                    if team_game_slots[team][d]:
                        current = team_game_slots[team][d][0]
                        sorted_slots = timeslots_by_date[d]
                        try:
                            idx = sorted_slots.index(current)
                        except ValueError:
                            valid_slot = False
                            break
                        if idx+1 >= len(sorted_slots):
                            valid_slot = False
                            break
                        required_slot = sorted_slots[idx+1]
                        if slot != required_slot:
                            valid_slot = False
                            break
                if not valid_slot:
                    continue
                # Enforce doubleheader limits and ensure different opponents on a doubleheader day.
                can_double = True
                for team, opp in ((home, away), (away, home)):
                    if team_game_days[team][d] == 1:
                        if doubleheader_count[team] >= MAX_DOUBLE_HEADERS:
                            can_double = False
                            break
                        if team_doubleheader_opponents[team][d] and opp in team_doubleheader_opponents[team][d]:
                            can_double = False
                            break
                if not can_double:
                    continue
                # Home/Away balancing.
                if team_stats[home]['home_games'] >= HOME_AWAY_BALANCE:
                    if team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
                        home, away = away, home
                    else:
                        continue
                # Schedule the game.
                schedule.append((date, slot, field, home, home[0], away, away[0]))
                for team in (home, away):
                    team_stats[team]['total_games'] += 1
                    team_stats[team]['weekly_games'][week_num] += 1
                    team_game_slots[team][d].append(slot)
                    team_game_days[team][d] += 1
                team_stats[home]['home_games'] += 1
                team_stats[away]['away_games'] += 1
                for team, opp in ((home, away), (away, home)):
                    if team_game_days[team][d] == 2:
                        doubleheader_count[team] += 1
                        team_doubleheader_opponents[team][d].add(opp)
                used_slots[(date, slot, field)] = True
                unscheduled.remove(matchup)
                progress_made = True
                break
            if progress_made:
                break
        if not progress_made:
            retry_count += 1
        else:
            retry_count = 0
    if unscheduled:
        print("Warning: Retry limit reached in primary scheduling. Some predetermined matchups could not be scheduled.")
    
    return schedule, team_stats, doubleheader_count, team_game_days, team_game_slots, team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled

def fill_missing_games(schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
                       team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled,
                       team_availability, team_blackouts, field_availability):
    retry_count = 0
    while any(stats['total_games'] < MAX_GAMES for stats in team_stats.values()) and retry_count < MAX_RETRIES:
        progress = False
        for date, slot, field in field_availability:
            if used_slots.get((date, slot, field), False):
                continue
            d = date.date()
            day_of_week = date.strftime('%a')
            week_num = date.isocalendar()[1]
            for matchup in unscheduled[:]:
                home, away = matchup
                # Only consider if at least one team is missing games.
                if team_stats[home]['total_games'] >= MAX_GAMES and team_stats[away]['total_games'] >= MAX_GAMES:
                    continue
                if day_of_week not in team_availability.get(home, set()) or day_of_week not in team_availability.get(away, set()):
                    continue
                if d in team_blackouts.get(home, set()) or d in team_blackouts.get(away, set()):
                    continue
                # WEEKLY_GAME_LIMIT check is relaxed in this phase.
                if not (min_gap_ok(home, d, team_game_days) and min_gap_ok(away, d, team_game_days)):
                    continue
                if slot in team_game_slots[home][d] or slot in team_game_slots[away][d]:
                    continue
                valid_slot = True
                for team in (home, away):
                    if team_game_slots[team][d]:
                        current = team_game_slots[team][d][0]
                        sorted_slots = timeslots_by_date[d]
                        try:
                            idx = sorted_slots.index(current)
                        except ValueError:
                            valid_slot = False
                            break
                        if idx+1 >= len(sorted_slots):
                            valid_slot = False
                            break
                        required_slot = sorted_slots[idx+1]
                        if slot != required_slot:
                            valid_slot = False
                            break
                if not valid_slot:
                    continue
                can_double = True
                for team, opp in ((home, away), (away, home)):
                    if team_game_days[team][d] == 1:
                        if doubleheader_count[team] >= MAX_DOUBLE_HEADERS:
                            can_double = False
                            break
                        if team_doubleheader_opponents[team][d] and opp in team_doubleheader_opponents[team][d]:
                            can_double = False
                            break
                if not can_double:
                    continue
                if team_stats[home]['home_games'] >= HOME_AWAY_BALANCE:
                    if team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
                        home, away = away, home
                    else:
                        continue
                schedule.append((date, slot, field, home, home[0], away, away[0]))
                for team in (home, away):
                    team_stats[team]['total_games'] += 1
                    team_stats[team]['weekly_games'][week_num] += 1
                    team_game_slots[team][d].append(slot)
                    team_game_days[team][d] += 1
                team_stats[home]['home_games'] += 1
                team_stats[away]['away_games'] += 1
                for team, opp in ((home, away), (away, home)):
                    if team_game_days[team][d] == 2:
                        doubleheader_count[team] += 1
                        team_doubleheader_opponents[team][d].add(opp)
                used_slots[(date, slot, field)] = True
                unscheduled.remove(matchup)
                progress = True
                break
            if progress:
                break
        if not progress:
            retry_count += 1
        else:
            retry_count = 0
    return schedule, team_stats, doubleheader_count, unscheduled

def output_schedule_to_csv(schedule, output_file):
    # Sort schedule by date, then by time (parsed as 12-hour) then by field.
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
    
    # Primary scheduling pass.
    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled) = schedule_games(
        matchups, team_availability, field_availability, team_blackouts
    )
    
    # Fill missing games if some teams haven't reached MAX_GAMES.
    if any(stats['total_games'] < MAX_GAMES for stats in team_stats.values()):
        print("Filling missing games...")
        (schedule, team_stats, doubleheader_count, unscheduled) = fill_missing_games(
            schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
            team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled,
            team_availability, team_blackouts, field_availability
        )
    
    # Final checks.
    missing = [team for team, stats in team_stats.items() if stats['total_games'] < MAX_GAMES]
    if missing:
        print("Critical: The following teams did not reach the required {} games: {}".format(MAX_GAMES, missing))
    under_dh = [team for team, count in doubleheader_count.items() if count < MIN_DOUBLE_HEADERS]
    if under_dh:
        print("Critical: The following teams did not meet the minimum doubleheader sessions ({} required): {}".format(MIN_DOUBLE_HEADERS, under_dh))
    
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    print_doubleheader_summary(doubleheader_count)
    generate_matchup_table(schedule, division_teams)

if __name__ == "__main__":
    main()
