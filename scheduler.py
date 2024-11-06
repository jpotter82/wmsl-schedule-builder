import csv
import itertools
from datetime import datetime, timedelta
import random

# Configurable parameters
start_date = datetime(2025, 4, 7)
end_date = datetime(2025, 7, 14)
single_games = 7
double_headers = 8

# Load teams and divisions
divisions = {
    'A': ['A' + str(i) for i in range(1, 9)],
    'B': ['B' + str(i) for i in range(1, 9)],
    'C': ['C' + str(i) for i in range(1, 9)]
}

# Load custom team availability from a CSV file
def load_team_availability(file_path):
    availability = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            team, days = row[0], row[1].split(',')
            availability[team] = set(days)
    return availability

# Load field availability from a CSV file, with AM/PM time formatting
def load_field_availability(file_path):
    field_availability = []
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            date = datetime.strptime(row[0], '%Y-%m-%d')
            slot = datetime.strptime(row[1], '%I:%M %p').strftime('%I:%M %p')  # Convert to 12-hour AM/PM format
            field = row[2]
            field_availability.append((date, slot, field))
    return field_availability


# Generate all intra-division and cross-division matchups
def generate_matchups():
    matchups = {}
    for div, teams in divisions.items():
        matchups[div] = list(itertools.combinations(teams, 2))
    cross_division_matchups = {
        "A-B": list(itertools.product(divisions['A'], divisions['B'])),
        "B-C": list(itertools.product(divisions['B'], divisions['C']))
    }
    return matchups, cross_division_matchups

# Schedule games based on availability and requirements
def schedule_games(matchups, cross_division_matchups, team_availability, field_availability):
    schedule = []
    game_counts = {team: 0 for team in itertools.chain(*divisions.values())}
    current_slot = 0

    while field_availability and any(game_counts[team] < (single_games + double_headers) for team in game_counts):
        date, slot, field = field_availability[current_slot]
        day_of_week = date.strftime('%a')  # Get day as a short weekday (e.g., "Mon")
        current_slot = (current_slot + 1) % len(field_availability)

        # Try to find a matchup for this slot
        for div in ['A', 'B', 'C']:
            if matchups[div] and game_counts[matchups[div][0][0]] < (single_games + double_headers):
                home, away = matchups[div].pop(0)
                if (day_of_week in team_availability.get(home, set()) and 
                    day_of_week in team_availability.get(away, set())):
                    # Schedule game only if both teams are available on the given day
                    schedule.append((date, slot, home, away, field))
                    game_counts[home] += 1
                    game_counts[away] += 1
                    break

        # Cross-division handling
        if not matchups['A'] and cross_division_matchups['A-B']:
            home, away = cross_division_matchups['A-B'].pop(0)
            if (day_of_week in team_availability.get(home, set()) and 
                day_of_week in team_availability.get(away, set())):
                schedule.append((date, slot, home, away, field))
                game_counts[home] += 1
                game_counts[away] += 1
    return schedule


# Output the final schedule to CSV
def output_schedule_to_csv(schedule, output_file):
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Home Team", "Away Team", "Field"])
        for game in schedule:
            writer.writerow([game[0].strftime('%Y-%m-%d'), game[1], game[2], game[3], game[4]])

# Main function to execute scheduling process
def main():
    # Load data
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')

    # Generate matchups
    matchups, cross_division_matchups = generate_matchups()

    # Schedule games
    schedule = schedule_games(matchups, cross_division_matchups, team_availability, field_availability)

    # Output to CSV
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("Schedule generated and saved to 'softball_schedule.csv'.")

if __name__ == "__main__":
    main()
