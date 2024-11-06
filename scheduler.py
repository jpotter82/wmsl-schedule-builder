import csv
import itertools
from datetime import datetime, timedelta
from collections import defaultdict

# Configurable parameters
start_date = datetime(2025, 4, 7)
end_date = datetime(2025, 7, 14)
max_games = 22

# Define divisions and initial team setups
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
        next(reader)  # Skip header
        for row in reader:
            team = row[0]
            days = row[1:]  # Take all columns after the team name
            days = [day.strip() for day in days if day]  # Clean whitespace
            availability[team] = set(days)
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

# Track games to ensure no team exceeds 22 games
def can_schedule(game_counts, home, away):
    return game_counts[home] < max_games and game_counts[away] < max_games

# Generate all intra-division and cross-division matchups
def generate_matchups():
    matchups = {}
    for div, teams in divisions.items():
        matchups[div] = list(itertools.combinations(teams, 2))  # Intra-division matchups

    # Define cross-division matchups based on specified requirements
    cross_division_matchups = {
        "A-B": list(itertools.product(divisions['A'], divisions['B'])),
        "B-C": list(itertools.product(divisions['B'], divisions['C'])),
        "C-A": list(itertools.product(divisions['C'], divisions['A'])),
    }
    return matchups, cross_division_matchups

# Main scheduling function
def schedule_games(matchups, cross_division_matchups, team_availability, field_availability):
    print("Scheduling games...")
    schedule = []
    game_counts = defaultdict(int)
    home_away_counts = defaultdict(lambda: {'home': 0, 'away': 0})
    inter_division_counts = defaultdict(lambda: defaultdict(int))
    intra_division_counts = defaultdict(int)

    current_slot = 0

    while current_slot < len(field_availability):
        date, slot, field = field_availability[current_slot]
        day_of_week = date.strftime('%a')
        current_slot += 1

        print(f"\nProcessing slot on {date.strftime('%Y-%m-%d')} at {slot} on {field}")

        scheduled_game = False

        for div in ['A', 'B', 'C']:
            for _ in range(len(matchups[div])):
                home, away = matchups[div].pop(0)
                matchups[div].append((home, away))  # Rotate it to the end if not scheduled

                # Check availability, weekly game constraints, and max game count
                if (day_of_week in team_availability.get(home, set()) and
                    day_of_week in team_availability.get(away, set()) and
                    can_schedule(game_counts, home, away)):
                    
                    # Schedule game
                    schedule.append((date, slot, home, away, field))
                    game_counts[home] += 1
                    game_counts[away] += 1

                    # Track home and away games
                    home_away_counts[home]['home'] += 1
                    home_away_counts[away]['away'] += 1

                    # Track inter- and intra-divisional games
                    if div in [home[0], away[0]]:
                        intra_division_counts[home] += 1
                        intra_division_counts[away] += 1
                    else:
                        inter_division_counts[home][away[0]] += 1
                        inter_division_counts[away][home[0]] += 1

                    scheduled_game = True
                    print(f"    - Scheduled: {home} vs {away} on {date.strftime('%Y-%m-%d')} at {slot} ({field})")
                    break
            if scheduled_game:
                break

        if not scheduled_game:
            print("    - No valid games found for this slot.")

    print("Scheduling complete.")
    return schedule, game_counts, home_away_counts, inter_division_counts, intra_division_counts

# Output schedule to CSV
def output_schedule_to_csv(schedule, output_file):
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Home Team", "Away Team", "Field"])
        for game in schedule:
            writer.writerow([game[0].strftime('%Y-%m-%d'), game[1], game[2], game[3], game[4]])
    print("Schedule saved successfully.")

# Print summary
def print_summary(game_counts, home_away_counts, inter_division_counts, intra_division_counts):
    print("\nSchedule Summary:\n")
    for team in game_counts:
        print(f"{team}:")
        print(f"  - Total games: {game_counts[team]}")
        print(f"  - Home games: {home_away_counts[team]['home']}")
        print(f"  - Away games: {home_away_counts[team]['away']}")
        print(f"  - Intra-division games: {intra_division_counts[team]}")
        for div, count in inter_division_counts[team].items():
            print(f"  - Inter-division games with Division {div}: {count}")
        print()

# Main function
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')

    matchups, cross_division_matchups = generate_matchups()
    schedule, game_counts, home_away_counts, inter_division_counts, intra_division_counts = schedule_games(
        matchups, cross_division_matchups, team_availability, field_availability
    )
    
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("Schedule Generation Complete.")
    print_summary(game_counts, home_away_counts, inter_division_counts, intra_division_counts)

if __name__ == "__main__":
    main()
