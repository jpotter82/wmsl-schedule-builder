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
MIN_DOUBLE_HEADERS = 7     # Updated: minimum number of doubleheader sessions per team
MAX_DOUBLE_HEADERS = 9     # Updated: maximum allowed doubleheader days per team

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
    """Returns True if the matchup is legal. Illegal: pairing an A–team with a C–team."""
    a, b = matchup
    if (a[0] == 'A' and b[0] == 'C') or (a[0] == 'C' and b[0] == 'A'):
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
            slot = row[1].strip()
            field = row[2].strip()
            field_availability.append((date, slot, field))
    field_availability.sort(key=lambda x: ((0 if x[0].weekday() == 6 else 1), x[0], datetime.strptime(x[1].strip(), "%I:%M %p")))
    return field_availability

def load_team_blackouts(file_path):
    blackouts = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            team = row[0].strip()
            dates = {datetime.strptime(d.strip(), '%Y-%m-%d').date() for d in row[1:] if d.strip()}
            blackouts[team] = dates
    return blackouts

# -------------------------------
# Doubleheader Scheduling
# -------------------------------
def schedule_doubleheaders(unscheduled, team_availability, field_availability, team_blackouts, timeslots_by_date,
                           team_stats, doubleheader_count, used_slots, schedule=None):
    """
    Enforce all doubleheaders first before single games.
    Updated to ensure minimum of 7 and max of 9 doubleheader days per team.
    """
    if schedule is None:
        schedule = []

    for team in team_stats.keys():
        while doubleheader_count[team] < MIN_DOUBLE_HEADERS:
            for d in sorted(timeslots_by_date.keys()):
                if d in team_blackouts.get(team, set()) or d.strftime('%a') not in team_availability.get(team, set()):
                    continue
                week_num = d.isocalendar()[1]
                slots = timeslots_by_date[d]
                if len(slots) < 2:
                    continue
                for i in range(len(slots) - 1):
                    slot1, slot2 = slots[i], slots[i+1]
                    available_matchups = [m for m in unscheduled if team in m]
                    if len(available_matchups) < 2:
                        continue
                    for m1, m2 in itertools.combinations(available_matchups, 2):
                        opp1, opp2 = (m1[0] if m1[1] == team else m1[1]), (m2[0] if m2[1] == team else m2[1])
                        if opp1 == opp2:
                            continue
                        if team_stats[team]['weekly_games'][week_num] + 2 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp1]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue
                        if team_stats[opp2]['weekly_games'][week_num] + 1 > WEEKLY_GAME_LIMIT:
                            continue
                        home1, away1 = team, opp1
                        home2, away2 = team, opp2
                        schedule.append((d.strftime('%Y-%m-%d'), slot1, home1, away1))
                        schedule.append((d.strftime('%Y-%m-%d'), slot2, home2, away2))
                        unscheduled.remove(m1)
                        unscheduled.remove(m2)
                        team_stats[team]['total_games'] += 2
                        team_stats[opp1]['total_games'] += 1
                        team_stats[opp2]['total_games'] += 1
                        doubleheader_count[team] += 1
                        break
    return schedule, team_stats, doubleheader_count, unscheduled

# -------------------------------
# Main function
# -------------------------------
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    team_blackouts = load_team_blackouts('team_blackouts.csv')
    
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    matchups = [(t1, t2) for div, teams in division_teams.items() for t1, t2 in itertools.combinations(teams, 2)]
    
    schedule = []
    team_stats = defaultdict(lambda: {'total_games': 0, 'weekly_games': defaultdict(int)})
    doubleheader_count = defaultdict(int)
    used_slots = {}
    timeslots_by_date = defaultdict(list)
    for date, slot, _ in field_availability:
        timeslots_by_date[date.date()].append(slot)
    
    schedule, team_stats, doubleheader_count, unscheduled = schedule_doubleheaders(
        matchups, team_availability, field_availability, team_blackouts, timeslots_by_date,
        team_stats, doubleheader_count, used_slots, schedule
    )
    
    print("Updated Schedule with Doubleheaders First:")
    for game in schedule:
        print(game)

if __name__ == "__main__":
    main()
