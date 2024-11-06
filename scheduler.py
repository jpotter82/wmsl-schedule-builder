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
    total_slots = len(field_availability)
    current_slot = 0
    unscheduled_rounds = 0

    used_slots = set()  # Track used slots for each day and field

    while field_availability and any(len(game_counts[team]) < (single_games + double_headers) for team in game_counts):
        date, slot, field = field_availability[current_slot]
        day_of_week = date.strftime('%a')
        slot_key = (date, slot, field)  # Unique identifier for date, time, and field
        current_slot = (current_slot + 1) % total_slots
        games_scheduled_this_round = 0
        teams_scheduled_today = set()

        print(f"\nAttempting to schedule games on {date.strftime('%Y-%m-%d')} at {slot} ({field})")

        # Ensure slot hasn't been used
        if slot_key in used_slots:
            print(f" - Slot {slot_key} already used, skipping.")
            continue

        # Try all available matchups in each division to ensure max usage of slots
        for div in ['A', 'B', 'C']:
            for i, (home, away) in enumerate(matchups[div]):
                # Check conditions for scheduling
                if (day_of_week in team_availability.get(home, set()) and
                    day_of_week in team_availability.get(away, set()) and
                    home not in teams_scheduled_today and
                    away not in teams_scheduled_today and
                    not has_double_header_this_week(game_counts, home, date) and
                    not has_double_header_this_week(game_counts, away, date)):

                    # Schedule game and update counts
                    schedule.append((date, slot, home, away, field))
                    game_counts[home].append(date)
                    game_counts[away].append(date)
                    teams_scheduled_today.update([home, away])
                    used_slots.add(slot_key)  # Mark slot as used
                    matchups[div].pop(i)  # Remove scheduled matchup
                    games_scheduled_this_round += 1
                    print(f" - Scheduled: {home} vs {away} on {date.strftime('%Y-%m-%d')} at {slot} ({field})")
                    break
                else:
                    print(f" - Skipping: {home} vs {away} due to unavailability, double-header limit, or already scheduled today")

        # Cross-division handling for additional games if no intra-division matchups available
        if not games_scheduled_this_round and not matchups['A'] and cross_division_matchups['A-B']:
            for i, (home, away) in enumerate(cross_division_matchups['A-B']):
                if (day_of_week in team_availability.get(home, set()) and
                    day_of_week in team_availability.get(away, set()) and
                    home not in teams_scheduled_today and
                    away not in teams_scheduled_today and
                    not has_double_header_this_week(game_counts, home, date) and
                    not has_double_header_this_week(game_counts, away, date)):

                    schedule.append((date, slot, home, away, field))
                    game_counts[home].append(date)
                    game_counts[away].append(date)
                    teams_scheduled_today.update([home, away])
                    used_slots.add(slot_key)  # Mark slot as used
                    cross_division_matchups['A-B'].pop(i)  # Remove scheduled matchup
                    games_scheduled_this_round += 1
                    print(f" - Scheduled cross-division: {home} vs {away} on {date.strftime('%Y-%m-%d')} at {slot} ({field})")
                    break

        # Check for infinite loop
        if games_scheduled_this_round == 0:
            unscheduled_rounds += 1
            if unscheduled_rounds >= total_slots:
                print("No more valid games can be scheduled. Exiting loop to prevent infinite repetition.")
                break
        else:
            unscheduled_rounds = 0

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
