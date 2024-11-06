import csv
import itertools
from datetime import datetime, timedelta
from collections import defaultdict

# Configurable parameters
MAX_GAMES = 22
WEEKLY_SINGLE_GAMES_LIMIT = 2  # Maximum single games per team per week
DIVISION_RULES = {
    'A': {'intra_min': 2, 'extra': 4},
    'B': {'intra_min': 2, 'extra': 4},
    'C': {'intra_min': 2, 'extra': 4},
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

# Initialize game counts, home/away, intra/inter-division
def initialize_team_stats():
    return {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'intra_divisional': 0,
        'inter_divisional': defaultdict(int)
    }

# Function to schedule games with constraints
def schedule_games(matchups, team_availability, field_availability):
    schedule = []
    team_stats = defaultdict(initialize_team_stats)
    current_slot = 0

    while current_slot < len(field_availability):
        date, slot, field = field_availability[current_slot]
        day_of_week = date.strftime('%a')
        current_slot += 1

        print(f"Processing slot on {date.strftime('%Y-%m-%d')} at {slot} on {field}")

        scheduled_game = False
        for div in matchups.keys():
            if scheduled_game:
                break
            for matchup in matchups[div]:
                home, away = matchup['teams']
                if (team_stats[home]['total_games'] < MAX_GAMES and 
                    team_stats[away]['total_games'] < MAX_GAMES and
                    day_of_week in team_availability[home] and 
                    day_of_week in team_availability[away]):

                    if team_stats[home]['total_games'] % 2 == 0:
                        home, away = away, home  # Alternate home team for balance

                    schedule.append((date, slot, home, away, field))
                    team_stats[home]['total_games'] += 1
                    team_stats[home]['home_games'] += 1
                    team_stats[away]['total_games'] += 1
                    team_stats[away]['away_games'] += 1
                    team_stats[home]['intra_divisional'] += 1 if div == 'intra' else 0
                    team_stats[away]['intra_divisional'] += 1 if div == 'intra' else 0
                    team_stats[home]['inter_divisional'][div] += 1 if div != 'intra' else 0
                    team_stats[away]['inter_divisional'][div] += 1 if div != 'intra' else 0
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

    # Example matchups for testing (replace with actual matchups generation)
    matchups = {
        'A': [{'teams': ('A1', 'A2')}, {'teams': ('A3', 'A4')}],
        'B': [{'teams': ('B1', 'B2')}, {'teams': ('B3', 'B4')}],
        'C': [{'teams': ('C1', 'C2')}, {'teams': ('C3', 'C4')}],
        'intra': [{'teams': ('A1', 'B1')}, {'teams': ('C1', 'B2')}]
    }

    schedule, team_stats = schedule_games(matchups, team_availability, field_availability)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("Schedule Generation Complete")
    print_schedule_summary(team_stats)

if __name__ == "__main__":
    main()
