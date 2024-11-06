import csv
import itertools
import random
from datetime import datetime
from collections import defaultdict

# Configurable parameters
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11
DIVISION_RULES = {
    'A': {
        'intra_min': 14,  # Total A vs A games: 7 A teams * 2 (home/away)
        'intra_extra': 4,  # Play 4 teams in A a third time
        'inter': {'B': 4}  # Play 4 different B teams (2 home, 2 away)
    },
    'B': {
        'intra_min': 14,  # Total B vs B games: 7 B teams * 2 (home/away)
        'inter': {'A': 4, 'C': 4}  # Play 4 A and 4 C teams (2 home, 2 away each)
    },
    'C': {
        'intra_min': 14,  # Total C vs C games: 7 C teams * 2 (home/away)
        'intra_extra': 4,  # Play 4 teams in C a third time
        'inter': {'B': 4}  # Play 4 different B teams (2 home, 2 away)
    }
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

# Initialize team stats
def initialize_team_stats():
    return {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'intra_divisional': 0,
        'inter_divisional': defaultdict(int)
    }

# Generate matchups for intra- and inter-divisional games
def generate_matchups(division_teams, rules):
    matchups = []

    # Intra-divisional matchups
    intra_matchups = list(itertools.combinations(division_teams, 2))
    random.shuffle(intra_matchups)

    for home, away in intra_matchups:
        matchups.append((home, away))
        matchups.append((away, home))

    # Extra intra-divisional games
    if 'intra_extra' in rules:
        extra_intra = random.sample(intra_matchups, rules['intra_extra'])
        for home, away in extra_intra:
            matchups.append((home, away))
            matchups.append((away, home))

    # Inter-divisional matchups
    for inter_div, count in rules['inter'].items():
        inter_teams = [f'{inter_div}{i+1}' for i in range(8)]
        inter_matchups = list(itertools.product(division_teams, inter_teams))
        random.shuffle(inter_matchups)
        selected_inter = inter_matchups[:count]
        for home, away in selected_inter:
            matchups.append((home, away))
            matchups.append((away, home))

    return matchups

def schedule_games(matchups, team_availability, field_availability):
    schedule = []
    team_stats = defaultdict(initialize_team_stats)
    current_slot = 0

    while current_slot < len(field_availability):
        date, slot, field = field_availability[current_slot]
        day_of_week = date.strftime('%a')
        current_slot += 1
        scheduled_game = False

        # Randomize divisions for scheduling balance
        divisions = list(matchups.keys())
        random.shuffle(divisions)

        for div in divisions:
            if scheduled_game:
                break
            division_matchups = matchups[div]
            random.shuffle(division_matchups)  # Shuffle matchups

            for home, away in division_matchups:
                # Check constraints
                if (team_stats[home]['total_games'] < MAX_GAMES and
                    team_stats[away]['total_games'] < MAX_GAMES and
                    day_of_week in team_availability[home] and
                    day_of_week in team_availability[away]):

                    if team_stats[home]['home_games'] < HOME_AWAY_BALANCE:
                        schedule.append((date, slot, home, away, field))
                    else:
                        schedule.append((date, slot, away, home, field))

                    # Update stats
                    team_stats[home]['total_games'] += 1
                    team_stats[home]['home_games'] += 1
                    team_stats[away]['total_games'] += 1
                    team_stats[away]['away_games'] += 1
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
        print(f"  Intra-Divisional Games: {stats['intra_divisional']}")
        print("  Inter-Divisional Games:")
        for div, count in stats['inter_divisional'].items():
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
