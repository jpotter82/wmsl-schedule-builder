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
    print("Loading team availability...")
    availability = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            team, days = row[0], row[1].split(',')
            availability[team] = set(days)
            print(f" - {team} available on: {', '.join(days)}")
    print("Team availability loaded.\n")
    return availability

# Load field availability from a CSV file, with AM/PM time formatting
def load_field_availability(file_path):
    print("Loading field availability...")
    field_availability = []
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            date = datetime.strptime(row[0], '%Y-%m-%d')
            slot = datetime.strptime(row[1], '%I:%M %p').strftime('%I:%M %p')  # Convert to 12-hour AM/PM format
            field = row[2]
            field_availability.append((date, slot, field))
            print(f" - {date.strftime('%Y-%m-%d')} at {slot} on {field}")
    print("Field availability loaded.\n")
    return field_availability

# Generate all intra-division and cross-division matchups
def generate_matchups():
    print("Generating matchups...")
    matchups = {}
    for div, teams in divisions.items():
        matchups[div] = list(itertools.combinations(teams, 2))
        print(f" - {div}-division matchups: {len(matchups[div])} games")
    cross_division_matchups = {
        "A-B": list(itertools.product(divisions['A'], divisions['B'])),
        "B-C": list(itertools.product(divisions['B'], divisions['C']))
    }
    print(f" - A-B cross-division matchups: {len(cross_division_matchups['A-B'])} games")
    print(f" - B-C cross-division matchups: {len(cross_division_matchups['B-C'])} games")
    print("Matchups generated.\n")
    return matchups, cross_division_matchups

# Schedule games based on availability and requirements
def schedule_games(matchups, cross_division_matchups, team_availability, field_availability):
    print("Scheduling games...")
    schedule = []
    game_counts = {team: 0 for team in itertools.chain(*divisions.values())}
    total_slots = len(field_availability)
    current_slot = 0
    unscheduled_rounds = 0  # Track how many rounds go without scheduling

    while field_availability and any(game_counts[team] < (single_games + double_headers) for team in game_counts):
        date, slot, field = field_availability[current_slot]
        day_of_week = date.strftime('%a')  # Get day as a short weekday (e.g., "Mon")
        current_slot = (current_slot + 1) % total_slots
        games_scheduled_this_round = 0

        # Progress output every 50 slots
        if current_slot % 50 == 0:
            print(f"Progress: Scheduling slot {current_slot}/{total_slots}")
            print(f"Game counts: {game_counts}")

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
                    games_scheduled_this_round += 1
                    print(f" - Scheduled: {home} vs {away} on {date.strftime('%Y-%m-%d')} at {slot} ({field})")
                    break

        # Cross-division handling
        if not matchups['A'] and cross_division_matchups['A-B']:
            home, away = cross_division_matchups['A-B'].pop(0)
            if (day_of_week in team_availability.get(home, set()) and 
                day_of_week in team_availability.get(away, set())):
                schedule.append((date, slot, home, away, field))
                game_counts[home] += 1
                game_counts[away] += 1
                games_scheduled_this_round += 1
                print(f" - Scheduled cross-division: {home} vs {away} on {date.strftime('%Y-%m-%d')} at {slot} ({field})")

        # If no games were scheduled in this round, increase unscheduled rounds count
        if games_scheduled_this_round == 0:
            unscheduled_rounds += 1
            if unscheduled_rounds >= total_slots:  # If all slots pass without scheduling, break to prevent loop
                print("Unable to find available matchups; exiting loop to prevent infinite repetition.")
                break
        else:
            unscheduled_rounds = 0  # Reset counter if games were scheduled

    print("Scheduling complete.\n")
    print(f"Final game counts: {game_counts}")
    return schedule

# Output the final schedule to CSV
def output_schedule_to_csv(schedule, output_file):
    print(f"Saving schedule to {output_file}...")
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Home Team", "Away Team", "Field"])
        for game in schedule:
            writer.writerow([game[0].strftime('%Y-%m-%d'), game[1], game[2], game[3], game[4]])
    print("Schedule saved successfully.\n")

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
    print("Schedule generation complete.")

if __name__ == "__main__":
    main()
