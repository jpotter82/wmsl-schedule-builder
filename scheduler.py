import csv
import itertools
from datetime import datetime, timedelta
import random

# Configurable parameters
total_games = 22  # Target number of games for each team

# Define divisions and initialize team matchups
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
            team = row[0]
            days = row[1:]  # Take all columns after the team name
            days = [day.strip() for day in days if day]  # Remove any empty strings or whitespace
            availability[team] = set(days)  # Store as a set for easy lookup
            # Debug output to verify parsing
            print(f"Loaded availability for {team}: {availability[team]}")
    return availability

# Load field availability from CSV
def load_field_availability(file_path):
    field_availability = []
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)
        for row in reader:
            date = datetime.strptime(row[0], '%Y-%m-%d')
            slot = datetime.strptime(row[1], '%I:%M %p').strftime('%I:%M %p')
            field = row[2]
            field_availability.append((date, slot, field))
    return field_availability

# Generate all intra- and cross-division matchups
def generate_matchups():
    matchups = {}
    for div, teams in divisions.items():
        matchups[div] = list(itertools.combinations(teams, 2))
    cross_division_matchups = {
        "A-B": list(itertools.product(divisions['A'], divisions['B'])),
        "B-C": list(itertools.product(divisions['B'], divisions['C']))
    }
    return matchups, cross_division_matchups

# Calculate average game count to prioritize teams with fewer games
def calculate_game_average(game_counts):
    total_games = sum(game_counts.values())
    num_teams = len(game_counts)
    return total_games / num_teams if num_teams > 0 else 0

# Scheduling games without stopping early
def schedule_games(matchups, cross_division_matchups, team_availability, field_availability):
    print("Scheduling games...")
    schedule = []
    game_counts = {team: 0 for team in itertools.chain(*divisions.values())}
    total_slots = len(field_availability)
    current_slot = 0
    unscheduled_rounds = 0  # Keeps track of consecutive unscheduled rounds, but won't stop the loop

    while current_slot < total_slots:
        date, slot, field = field_availability[current_slot]
        day_of_week = date.strftime('%a')
        current_slot += 1  # Increment slot for the next iteration
        teams_scheduled_today = set()

        print(f"\nProcessing slot on {date.strftime('%Y-%m-%d')} at {slot} on {field} (Avg games: {sum(game_counts.values()) / len(game_counts):.1f})")

        scheduled_game = False  # Track if a game was scheduled in this slot

        for div in ['A', 'B', 'C']:
            for _ in range(len(matchups[div])):
                home, away = matchups[div].pop(0)  # Get the first matchup for the division
                matchups[div].append((home, away))  # Rotate it to the end if not scheduled

                # Check availability and prevent same-day double scheduling
                if (day_of_week in team_availability.get(home, set()) and
                    day_of_week in team_availability.get(away, set()) and
                    home not in teams_scheduled_today and
                    away not in teams_scheduled_today):
                    
                    # Schedule the game
                    schedule.append((date, slot, home, away, field))
                    game_counts[home] += 1
                    game_counts[away] += 1
                    teams_scheduled_today.update([home, away])
                    scheduled_game = True
                    print(f"    - Scheduled: {home} vs {away} on {date.strftime('%Y-%m-%d')} at {slot} ({field})")
                    break  # Exit the division loop to prevent double booking
            if scheduled_game:
                break  # Exit outer loop if a game was scheduled

        if not scheduled_game:
            print("    - No valid games found for this slot.")
        
    print("Scheduling complete.")
    return schedule

# Output the final schedule to CSV
def output_schedule_to_csv(schedule, output_file):
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Home Team", "Away Team", "Field"])
        for game in schedule:
            writer.writerow([game[0].strftime('%Y-%m-%d'), game[1], game[2], game[3], game[4]])
    print("Schedule saved successfully.\n")

# Main function to execute scheduling process
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')

    matchups, cross_division_matchups = generate_matchups()
    schedule = schedule_games(matchups, cross_division_matchups, team_availability, field_availability)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("Schedule generation complete.")

if __name__ == "__main__":
    main()
