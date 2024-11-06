import csv
import itertools
from datetime import datetime, timedelta

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
            slot = datetime.strptime(row[1], '%I:%M %p').strftime('%I:%M %p')
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

# Helper function to check if a team has a double-header scheduled in a given week
def has_double_header_this_week(game_counts, team, current_date):
    week_start = current_date - timedelta(days=current_date.weekday())
    games_this_week = [game_date for game_date in game_counts.get(team, []) if week_start <= game_date < week_start + timedelta(days=7)]
    return len(games_this_week) >= 2

# Schedule games based on availability and requirements
def schedule_games(matchups, cross_division_matchups, team_availability, field_availability):
    print("Scheduling games...")
    schedule = []
    game_counts = {team: [] for team in itertools.chain(*divisions.values())}
    used_slots = set()  # Track used slots for each day and field
    division_cycle = itertools.cycle(['A', 'B', 'C'])  # Cycle through divisions each slot

    # Iterate over each available slot and cycle through divisions for each slot
    for date, slot, field in field_availability:
        slot_key = (date, slot, field)

        # Skip this slot if it's already used
        if slot_key in used_slots:
            continue

        day_of_week = date.strftime('%a')
        scheduled_for_slot = False

        # Cycle through divisions to ensure even distribution
        for div in division_cycle:
            if div not in matchups or not matchups[div]:
                continue

            # Attempt to schedule a game within the division
            for i, (home, away) in enumerate(matchups[div]):
                if (day_of_week in team_availability.get(home, set()) and
                    day_of_week in team_availability.get(away, set()) and
                    not has_double_header_this_week(game_counts, home, date) and
                    not has_double_header_this_week(game_counts, away, date)):

                    # Schedule game and update counts
                    schedule.append((date, slot, home, away, field))
                    game_counts[home].append(date)
                    game_counts[away].append(date)
                    used_slots.add(slot_key)
                    matchups[div].pop(i)  # Remove scheduled matchup
                    print(f" - Scheduled: {home} vs {away} on {date.strftime('%Y-%m-%d')} at {slot} ({field})")
                    scheduled_for_slot = True
                    break

            if scheduled_for_slot:
                break  # Move to the next slot if this one is filled

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
