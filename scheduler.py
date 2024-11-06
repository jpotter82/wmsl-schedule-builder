import csv
import itertools
from datetime import datetime, timedelta
import random

# Configurable parameters
start_date = datetime(2024, 4, 8)
end_date = datetime(2024, 7, 14)
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

# Helper function to check if a team has already played in a given week
def has_played_this_week(game_counts, team, current_date):
    week_start = current_date - timedelta(days=current_date.weekday())  # Start of the week
    return any(game_date >= week_start and game_date < week_start + timedelta(days=7) 
               for game_date in game_counts.get(team, []))

# Schedule games based on availability and requirements
def schedule_games(matchups, cross_division_matchups, team_availability, field_availability):
    schedule = []
    game_counts = {team: [] for team in itertools.chain(*divisions.values())}  # Track games per team by date
    total_slots = len(field_availability)
    current_slot = 0

    while field_availability and any(len(game_counts[team]) < (single_games + double_headers) for team in game_counts):
        date, slot, field = field_availability[current_slot]
        day_of_week = date.strftime('%a')
        current_slot = (current_slot + 1) % total_slots
        games_scheduled_this_round = 0

        # Track teams scheduled this slot to prevent scheduling them simultaneously on multiple fields
        teams_scheduled_today = set()

        # Try to find a single-game or double-header matchup
        for div in ['A', 'B', 'C']:
            if matchups[div]:
                home, away = matchups[div][0]
                
                # Conditions: team availability, not scheduled elsewhere today, weekly game limits
                if (day_of_week in team_availability.get(home, set()) and 
                    day_of_week in team_availability.get(away, set()) and
                    home not in teams_scheduled_today and
                    away not in teams_scheduled_today and
                    not has_played_this_week(game_counts, home, date) and
                    not has_played_this_week(game_counts, away, date)):
                    
                    # Schedule single or double header as per remaining game count
                    if len(game_counts[home]) < single_games + double_headers and \
                       len(game_counts[away]) < single_games + double_headers:
                        
                        # Schedule game and update counts
                        schedule.append((date, slot, home, away, field))
                        game_counts[home].append(date)
                        game_counts[away].append(date)
                        teams_scheduled_today.update([home, away])
                        matchups[div].pop(0)  # Remove scheduled matchup
                        games_scheduled_this_round += 1
                        break

        # Cross-division handling for additional games if no intra-division matchups available
        if not games_scheduled_this_round and not matchups['A'] and cross_division_matchups['A-B']:
            home, away = cross_division_matchups['A-B'][0]
            if (day_of_week in team_availability.get(home, set()) and 
                day_of_week in team_availability.get(away, set()) and
                home not in teams_scheduled_today and
                away not in teams_scheduled_today and
                not has_played_this_week(game_counts, home, date) and
                not has_played_this_week(game_counts, away, date)):

                schedule.append((date, slot, home, away, field))
                game_counts[home].append(date)
                game_counts[away].append(date)
                teams_scheduled_today.update([home, away])
                cross_division_matchups['A-B'].pop(0)  # Remove scheduled matchup
                games_scheduled_this_round += 1

        # If no games are scheduled in this slot, we move on to the next slot

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
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')

    matchups, cross_division_matchups = generate_matchups()
    schedule = schedule_games(matchups, cross_division_matchups, team_availability, field_availability)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("Schedule generation complete.")

if __name__ == "__main__":
    main()
