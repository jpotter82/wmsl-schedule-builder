import csv
import itertools
import random
from datetime import datetime, timedelta
from collections import defaultdict

# Configurable parameters
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11
WEEKLY_SINGLE_GAMES_LIMIT = 2  # Maximum single games per team per week
DIVISION_RULES = {
    'A': {'intra_min': 2, 'extra': 4, 'inter_divisions': ['B']},
    'B': {'intra_min': 2, 'extra': 4, 'inter_divisions': ['A', 'C']},
    'C': {'intra_min': 2, 'extra': 4, 'inter_divisions': ['B']}
}

# Load team availability
def load_team_availability(file_path):
    availability = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            team = row[0]
            days = row[1:]  # Grab days in CSV columns
            availability[team] = {day.strip() for day in days if day}
    return availability

# Load field availability
def load_field_availability(file_path):
    field_availability = []
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            date = datetime.strptime(row[0], '%Y-%m-%d')
            slot = datetime.strptime(row[1], '%I:%M %p').strftime('%I:%M %p')
            field = row[2]
            field_availability.append((date, slot, field))
    return field_availability

# Initialize team stats with constraints for tracking
def initialize_team_stats():
    return {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'intra_divisional': 0,
        'inter_divisional': defaultdict(int)
    }

# Generate randomized matchups according to rules
def generate_matchups():
    matchups = defaultdict(list)

    for div, rules in DIVISION_RULES.items():
        # Generate intra-divisional games
        intra_teams = ['A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8'] if div == 'A' else \
                      ['B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8'] if div == 'B' else \
                      ['C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8']
        
        intra_matchups = list(itertools.combinations(intra_teams, 2))
        random.shuffle(intra_matchups)
        
        # Schedule intra-divisional games twice (one home, one away)
        for matchup in intra_matchups:
            home, away = matchup
            matchups[div].append((home, away))
            matchups[div].append((away, home))

        # Add extra intra-divisional games
        extra_matchups = random.sample(intra_matchups, rules['extra'])
        for home, away in extra_matchups:
            matchups[div].append((home, away))
            matchups[div].append((away, home))

        # Generate inter-divisional games
        for inter_div in rules['inter_divisions']:
            inter_teams = ['B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7', 'B8'] if inter_div == 'B' else \
                          ['A1', 'A2', 'A3', 'A4', 'A5', 'A6', 'A7', 'A8'] if inter_div == 'A' else \
                          ['C1', 'C2', 'C3', 'C4', 'C5', 'C6', 'C7', 'C8']
            inter_matchups = list(itertools.product(intra_teams, inter_teams))
            inter_sample = random.sample(inter_matchups, rules['extra'])
            for home, away in inter_sample:
                matchups[f"{div}_{inter_div}"].append((home, away))
                matchups[f"{div}_{inter_div}"].append((away, home))
    
    return matchups

# Schedule games with constraints
def schedule_games(matchups, team_availability, field_availability):
    schedule = []
    team_stats = defaultdict(initialize_team_stats)
    current_slot = 0

    while current_slot < len(field_availability):
        date, slot, field = field_availability[current_slot]
        day_of_week = date.strftime('%a')
        week_num = date.isocalendar()[1]
        current_slot += 1
        scheduled_game = False

        # Shuffle divisions to randomize matchups across divisions
        divisions = list(matchups.keys())
        random.shuffle(divisions)

        for div in divisions:
            if scheduled_game:
                break
            division_matchups = matchups[div]
            random.shuffle(division_matchups)  # Shuffle to randomize within division

            for matchup in division_matchups:
                home, away = matchup

                # Check constraints
                if (team_stats[home]['total_games'] < MAX_GAMES and 
                    team_stats[away]['total_games'] < MAX_GAMES and
                    day_of_week in team_availability[home] and 
                    day_of_week in team_availability[away] and
                    team_stats[home]['total_games'] < MAX_GAMES and
                    team_stats[away]['total_games'] < MAX_GAMES):

                    if team_stats[home]['home_games'] < HOME_AWAY_BALANCE:
                        home, away = away, home  # Alternate home team for balance

                    # Add game to schedule
                    schedule.append((date, slot, home, away, field))
                    team_stats[home]['total_games'] += 1
                    team_stats[home]['home_games'] += 1
                    team_stats[away]['total_games'] += 1
                    team_stats[away]['away_games'] += 1

                    if div in DIVISION_RULES:
                        team_stats[home]['intra_divisional'] += 1
                        team_stats[away]['intra_divisional'] += 1
                    else:
                        team_stats[home]['inter_divisional'][div] += 1
                        team_stats[away]['inter_divisional'][div] += 1
                    
                    scheduled_game = True
                    break

    return schedule, team_stats

# Output schedule to CSV
def output_schedule_to_csv(schedule, output_file):
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Home Team", "Away Team", "Field"])
        for game in schedule:
            writer.writerow([game[0].strftime('%Y-%m-%d'), game[1], game[2], game[3], game[4]])

# Print summary statistics
def print_schedule_summary(team_stats):
    print("Schedule Summary:")
    for team, stats in team_stats.items():
        print(f"\nTeam: {team}")
        print(f"  Total Games: {stats['total_games']}")
        print(f"  Home Games: {stats['home_games']}")
        print(f"  Away Games: {stats['away_games']}")
        print(f"  Intra-Divisional Games: {stats['intra_divisional']}")
        print("  Inter-Divisional Games:")
        for div, count in stats['inter_divisional'].items():
            print(f"    {div.capitalize()} Division: {count}")

# Main function
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    matchups = generate_matchups()
    schedule, team_stats = schedule_games(matchups, team_availability, field_availability)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("Schedule Generation Complete")
    print_schedule_summary(team_stats)

if __name__ == "__main__":
    main()
