import csv
import itertools
import random
from datetime import datetime
from collections import defaultdict

# Configurable parameters
MAX_GAMES = 22
DIVISION_RULES = {
    'A': {'intra': 18, 'inter': {'B': 4}},
    'B': {'intra': 14, 'inter': {'A': 4, 'C': 4}},
    'C': {'intra': 18, 'inter': {'B': 4}}
}

# Load team availability
def load_team_availability(file_path):
    availability = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            team = row[0]
            days = row[1:]
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

# Initialize team stats with constraints
def initialize_team_stats():
    return {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'intra_games': 0,
        'inter_games': defaultdict(int)
    }

# Generate randomized matchups for intra- and inter-divisional games
def generate_matchups(division_teams, rules):
    matchups = defaultdict(list)

    # Intra-divisional matchups
    intra_matchups = list(itertools.combinations(division_teams, 2))
    random.shuffle(intra_matchups)
    for home, away in intra_matchups:
        matchups['intra'].append((home, away))
        matchups['intra'].append((away, home))

    # Additional intra games to reach the required total
    extra_intra = random.sample(intra_matchups, rules['intra'] - len(intra_matchups))
    for home, away in extra_intra:
        matchups['intra'].append((home, away))
        matchups['intra'].append((away, home))

    # Inter-divisional matchups
    for inter_div, count in rules['inter'].items():
        inter_teams = [f'{inter_div}{i+1}' for i in range(8)]
        inter_matchups = list(itertools.product(division_teams, inter_teams))
        selected_matchups = random.sample(inter_matchups, count)
        for home, away in selected_matchups:
            matchups[inter_div].append((home, away))
            matchups[inter_div].append((away, home))

    return matchups

# Schedule games with constraints
def schedule_games(matchups, team_availability, field_availability):
    schedule = []
    team_stats = defaultdict(initialize_team_stats)
    current_slot = 0

    while current_slot < len(field_availability):
        date, slot, field = field_availability[current_slot]
        day_of_week = date.strftime('%a')
        current_slot += 1
        scheduled_game = False

        # Shuffle matchups to randomize scheduling
        divisions = list(matchups.keys())
        random.shuffle(divisions)

        for div in divisions:
            if scheduled_game:
                break
            random.shuffle(matchups[div])

            for matchup in matchups[div]:
                home, away = matchup

                if (team_stats[home]['total_games'] < MAX_GAMES and
                    team_stats[away]['total_games'] < MAX_GAMES and
                    day_of_week in team_availability[home] and
                    day_of_week in team_availability[away] and
                    team_stats[home]['home_games'] < 11 and
                    team_stats[away]['away_games'] < 11):

                    schedule.append((date, slot, home, away, field))
                    team_stats[home]['total_games'] += 1
                    team_stats[home]['home_games'] += 1
                    team_stats[away]['total_games'] += 1
                    team_stats[away]['away_games'] += 1

                    if div == 'intra':
                        team_stats[home]['intra_games'] += 1
                        team_stats[away]['intra_games'] += 1
                    else:
                        team_stats[home]['inter_games'][div] += 1
                        team_stats[away]['inter_games'][div] += 1

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
        print(f"  Intra-Divisional Games: {stats['intra_games']}")
        print("  Inter-Divisional Games:")
        for div, count in stats['inter_games'].items():
            print(f"    {div.capitalize()} Division: {count}")

# Main function
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')

    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)],
    }

    matchups = {div: generate_matchups(teams, DIVISION_RULES[div]) for div, teams in division_teams.items()}
    schedule, team_stats = schedule_games(matchups, team_availability, field_availability)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("Schedule Generation Complete")
    print_schedule_summary(team_stats)

if __name__ == "__main__":
    main()
