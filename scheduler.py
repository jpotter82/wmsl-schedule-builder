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

# Load team availability from CSV
def load_team_availability(file_path):
    availability = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)
        for row in reader:
            team, days = row[0], row[1].split(',')
            availability[team] = set(days)
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

# Schedule games based on availability and constraints
def schedule_games(matchups, cross_division_matchups, team_availability, field_availability):
    print("Scheduling games...")
    schedule = []
    game_counts = {team: 0 for team in itertools.chain(*divisions.values())}
    weekly_games = {team: 0 for team in game_counts}
    used_slots = set()  # Track used slots for each day and field

    # Shuffle matchups to add randomness to scheduling order
    for division in matchups.values():
        random.shuffle(division)
    for cross_division in cross_division_matchups.values():
        random.shuffle(cross_division)

    # Iterate over every available field slot independently
    for date, slot, field in field_availability:
        slot_key = (date, slot, field)
        day_of_week = date.strftime('%a')

        # Skip this slot if it’s already used
        if slot_key in used_slots:
            continue

        scheduled_for_slot = False

        # Randomize division order for each slot to distribute across divisions
        available_divisions = ['A', 'B', 'C']
        random.shuffle(available_divisions)

        for div in available_divisions:
            if not matchups.get(div):  # Skip if no more matchups in this division
                continue

            # Attempt to schedule a game within the division
            for i, (home, away) in enumerate(matchups[div]):
                # Validate conditions: team availability, weekly game limits, field exclusivity
                if (day_of_week in team_availability.get(home, set()) and
                    day_of_week in team_availability.get(away, set()) and
                    weekly_games[home] < 2 and weekly_games[away] < 2 and
                    game_counts[home] < total_games and game_counts[away] < total_games):

                    # Schedule game and update counts
                    schedule.append((date, slot, home, away, field))
                    game_counts[home] += 1
                    game_counts[away] += 1
                    weekly_games[home] += 1
                    weekly_games[away] += 1
                    used_slots.add(slot_key)
                    matchups[div].pop(i)  # Remove scheduled matchup
                    print(f" - Scheduled: {home} vs {away} on {date.strftime('%Y-%m-%d')} at {slot} ({field})")
                    scheduled_for_slot = True
                    break

            if scheduled_for_slot:
                break  # Move to the next slot if a game was scheduled for this slot

        # Reset weekly game count on Sunday night for a fresh start each week
        if date.weekday() == 6:  # Sunday
            for team in weekly_games:
                weekly_games[team] = 0

    print("Scheduling complete.\n")
    print(f"Final game counts: {game_counts}")
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
