import csv
import itertools
from datetime import datetime, timedelta

# Configurable parameters
start_date = datetime(2024, 4, 7)
end_date = datetime(2024, 7, 14)
single_games_per_week = 2
double_headers_per_week = 1
total_games = 22

# Define divisions and teams
divisions = {
    'A': ['A' + str(i) for i in range(1, 9)],
    'B': ['B' + str(i) for i in range(1, 9)],
    'C': ['C' + str(i) for i in range(1, 9)]
}

# Load team availability
def load_team_availability(file_path):
    availability = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            team, days = row[0], row[1].split(',')
            availability[team] = set(days)
    return availability

# Load field availability from CSV
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

# Generate required matchups for each division
def generate_matchups():
    matchups = {}
    for div, teams in divisions.items():
        matchups[div] = list(itertools.combinations(teams, 2))
    cross_division_matchups = {
        "A-B": list(itertools.product(divisions['A'], divisions['B'])),
        "B-C": list(itertools.product(divisions['B'], divisions['C']))
    }
    return matchups, cross_division_matchups

# Check weekly game limit for a team
def weekly_limit_reached(game_counts, team, current_date):
    week_start = current_date - timedelta(days=current_date.weekday())
    games_this_week = [game_date for game_date in game_counts.get(team, []) if week_start <= game_date < week_start + timedelta(days=7)]
    return len(games_this_week) >= single_games_per_week or has_double_header_this_week(game_counts, team, current_date)

# Check if a team has a double-header scheduled in a given week
def has_double_header_this_week(game_counts, team, current_date):
    week_start = current_date - timedelta(days=current_date.weekday())
    games_this_week = [game_date for game_date in game_counts.get(team, []) if week_start <= game_date < week_start + timedelta(days=7)]
    return len(games_this_week) >= double_headers_per_week

# Schedule games
def schedule_games(matchups, cross_division_matchups, team_availability, field_availability):
    print("Scheduling games...")
    schedule = []
    game_counts = {team: [] for team in itertools.chain(*divisions.values())}
    team_games = {team: 0 for team in itertools.chain(*divisions.values())}  # Track total games per team
    used_slots = set()  # Track used slots to avoid double-booking

    # Attempt to fill earliest slots first
    for date, slot, field in field_availability:
        slot_key = (date, slot, field)

        # Skip if slot already used
        if slot_key in used_slots:
            continue

        # Try scheduling games for each division
        for div in ['A', 'B', 'C']:
            if not matchups.get(div):
                continue

            # Attempt to schedule within division
            for i, (home, away) in enumerate(matchups[div]):
                if (team_games[home] < total_games and team_games[away] < total_games and
                    date.strftime('%a') in team_availability.get(home, set()) and
                    date.strftime('%a') in team_availability.get(away, set()) and
                    not weekly_limit_reached(game_counts, home, date) and
                    not weekly_limit_reached(game_counts, away, date)):

                    # Schedule game, update counts, and mark slot as used
                    schedule.append((date, slot, home, away, field))
                    game_counts[home].append(date)
                    game_counts[away].append(date)
                    team_games[home] += 1
                    team_games[away] += 1
                    used_slots.add(slot_key)
                    matchups[div].pop(i)
                    print(f" - Scheduled: {home} vs {away} on {date.strftime('%Y-%m-%d')} at {slot} ({field})")
                    break

    print("Scheduling complete.\n")
    print(f"Total games scheduled per team: {team_games}")
    return schedule

# Output schedule to CSV
def output_schedule_to_csv(schedule, output_file):
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Home Team", "Away Team", "Field"])
        for game in schedule:
            writer.writerow([game[0].strftime('%Y-%m-%d'), game[1], game[2], game[3], game[4]])
    print("Schedule saved successfully.\n")

# Main function
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')

    matchups, cross_division_matchups = generate_matchups()
    schedule = schedule_games(matchups, cross_division_matchups, team_availability, field_availability)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("Schedule generation complete.")

if __name__ == "__main__":
    main()
