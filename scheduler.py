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
MIN_GAP = 2             # minimum days between games on different days

# -------------------------------
# Helper Functions
# -------------------------------
def min_gap_ok(team, d, team_game_days):
    """Return True if team has no game scheduled on a day less than MIN_GAP days before d."""
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
        next(reader)  # Skip header
        for row in reader:
            team = row[0].strip()
            days = row[1:]
            availability[team] = {day.strip() for day in days if day.strip()}
    return availability

def load_field_availability(file_path):
    field_availability = []
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        for row in reader:
            date = datetime.strptime(row[0].strip(), '%Y-%m-%d')
            slot = row[1].strip()
            field = row[2].strip()
            field_availability.append((date, slot, field))
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
# (Matchup generation functions omitted for brevity; assume unchanged)
# -------------------------------

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
# Standard (Single-Game) Scheduling (with Diamond Grouping & Consecutive Slot Enforcement)
# -------------------------------
def schedule_standard_games(matchups, team_availability, field_availability, doubleheader_dates):
    """
    Standard scheduling on dates not used for doubleheaders.
    Group slots by date then by diamond.
    A team may play twice on the same day only if both games are on the same diamond and in consecutive slots.
    Also, a team cannot be scheduled in simultaneous slots across different diamonds.
    """
    schedule = []
    team_stats = defaultdict(lambda: {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'weekly_games': defaultdict(int)
    })
    # team_game_days: team -> set of dates (for min gap between days)
    team_game_days = defaultdict(set)
    # team_field_slots: team -> dict keyed by (date, field) with the last slot index assigned on that diamond.
    team_field_slots = defaultdict(lambda: {})
    # simul_slots: (date, slot) -> set of teams scheduled at that time (across any diamond)
    simul_slots = defaultdict(set)
    used_slots = {}
    
    # Filter out slots on doubleheader dates.
    standard_slots = [s for s in field_availability if s[0].date() not in doubleheader_dates]
    # Group slots by date and then by field.
    slots_by_date_field = defaultdict(lambda: defaultdict(list))
    for dt, slot, field in standard_slots:
        d = dt.date()
        slots_by_date_field[d][field].append((dt, slot, field))
    # Sort slots for each diamond.
    for d in slots_by_date_field:
        for field in slots_by_date_field[d]:
            slots_by_date_field[d][field].sort(key=lambda x: x[0])
    # Process dates in ascending order.
    all_dates = sorted(slots_by_date_field.keys())
    retry_count = 0
    unscheduled = matchups[:]
    while unscheduled and retry_count < MAX_RETRIES:
        progress_made = False
        for d in all_dates:
            for field, slot_list in slots_by_date_field[d].items():
                for idx, (dt, slot_time, field) in enumerate(slot_list):
                    # Skip if slot already used.
                    if used_slots.get((dt, slot_time, field), False):
                        continue
                    # Check if any team is scheduled in the same time (date, slot_time) on any diamond.
                    if simul_slots.get((d, slot_time), set()):
                        # If any team in candidate matchup is already in simul_slots, skip.
                        candidate_conflict = True
                    else:
                        candidate_conflict = False
                    day_str = dt.strftime('%a')
                    week_num = dt.isocalendar()[1]
                    for matchup in unscheduled[:]:
                        home, away = matchup
                        # Check availability.
                        if day_str not in team_availability.get(home, set()) or day_str not in team_availability.get(away, set()):
                            continue
                        # Check overall game limits and weekly limits.
                        if team_stats[home]['total_games'] >= MAX_GAMES or team_stats[away]['total_games'] >= MAX_GAMES:
                            continue
                        if team_stats[home]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or team_stats[away]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT:
                            continue
                        # Check min gap for games on different days.
                        if not (min_gap_ok(home, d, team_game_days) and min_gap_ok(away, d, team_game_days)):
                            continue
                        # Enforce: if either team is already scheduled on this diamond on this day,
                        # then the new slot index must equal (last index + 1).
                        valid = True
                        for team in (home, away):
                            key = (d, field)
                            if key in team_field_slots[team]:
                                last_idx = team_field_slots[team][key]
                                if idx != last_idx + 1:
                                    valid = False
                                    break
                        if not valid:
                            continue
                        # Also, if a team is scheduled at this same time (regardless of diamond), skip candidate.
                        if simul_slots.get((d, slot_time), set()):
                            if home in simul_slots[(d, slot_time)] or away in simul_slots[(d, slot_time)]:
                                continue
                        # If home is at home quota, adjust.
                        if team_stats[home]['home_games'] >= HOME_AWAY_BALANCE:
                            if team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
                                home, away = away, home
                            else:
                                continue
                        # If candidate passes all checks, schedule it.
                        schedule.append((dt, slot_time, field, home, home[0], away, away[0]))
                        team_stats[home]['total_games'] += 1
                        team_stats[home]['home_games'] += 1
                        team_stats[away]['total_games'] += 1
                        team_stats[away]['away_games'] += 1
                        team_stats[home]['weekly_games'][week_num] += 1
                        team_stats[away]['weekly_games'][week_num] += 1
                        used_slots[(dt, slot_time, field)] = True
                        simul_slots.setdefault((d, slot_time), set()).update([home, away])
                        team_field_slots[home][(d, field)] = idx
                        team_field_slots[away][(d, field)] = idx
                        team_game_days[home].add(d)
                        team_game_days[away].add(d)
                        unscheduled.remove(matchup)
                        progress_made = True
                        break
                    if progress_made:
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
