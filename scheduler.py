import csv
import itertools
from datetime import datetime, timedelta
from collections import defaultdict

# Configurable parameters
MAX_GAMES = 22
WEEKLY_SINGLE_GAMES_LIMIT = 2  # Max single games per team per week

# Division rules
DIVISION_RULES = {
    'A': {'intra_min': 2, 'extra': 4, 'inter_min': 4, 'inter_divisions': ['B']},
    'B': {'intra_min': 2, 'extra': 4, 'inter_min': 4, 'inter_divisions': ['A', 'C']},
    'C': {'intra_min': 2, 'extra': 4, 'inter_min': 4, 'inter_divisions': ['B']}
}

# Team initialization
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
            team = row[0]
            days = row[1:]  # Grab days in CSV columns
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

# Generate matchups
def generate_matchups():
    matchups = {'A': {'intra': [], 'inter': []}, 'B': {'intra': [], 'inter': []}, 'C': {'intra': [], 'inter': []}}

    for div, teams in divisions.items():
        matchups[div]['intra'] = list(itertools.combinations(teams, 2))

    matchups['A']['inter'] = list(itertools.product(divisions['A'], divisions['B']))
    matchups['B']['inter'] = list(itertools.product(divisions['B'], divisions['A'])) + list(itertools.product(divisions['B'], divisions['C']))
    matchups['C']['inter'] = list(itertools.product(divisions['C'], divisions['B']))
    
    return matchups

# Initialize team stats
def initialize_team_stats():
    return {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'intra_divisional': 0,
        'inter_divisional': defaultdict(int)  # Track games per division
    }

# Check if inter-division game requirements are met
def can_schedule_inter_division(team, opp_team, team_stats, team_div, opp_div):
    if team_stats[team]['inter_divisional'][opp_div] < DIVISION_RULES[team_div]['inter_min']:
        return True
    if team_stats[opp_team]['inter_divisional'][team_div] < DIVISION_RULES[opp_div]['inter_min']:
        return True
    return False

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

        print(f"Processing slot on {date.strftime('%Y-%m-%d')} at {slot} on {field}")

        for div, rules in DIVISION_RULES.items():
            if scheduled_game:
                break
            intra_matchups = matchups[div]['intra']
            inter_matchups = matchups[div]['inter']

            # Schedule intra-division games first
            for matchup in intra_matchups:
                home, away = matchup
                if (team_stats[home]['total_games'] < MAX_GAMES and 
                    team_stats[away]['total_games'] < MAX_GAMES and
                    day_of_week in team_availability[home] and 
                    day_of_week in team_availability[away]):

                    # Balance home/away
                    if team_stats[home]['home_games'] < team_stats[home]['away_games']:
                        home, away = away, home

                    # Schedule game
                    schedule.append((date, slot, home, away, field))
                    team_stats[home]['total_games'] += 1
                    team_stats[home]['home_games'] += 1
                    team_stats[away]['total_games'] += 1
                    team_stats[away]['away_games'] += 1
                    team_stats[home]['intra_divisional'] += 1
                    team_stats[away]['intra_divisional'] += 1
                    scheduled_game = True
                    break

            # Schedule inter-division games
            if not scheduled_game:
                for matchup in inter_matchups:
                    home, away = matchup
                    opp_div = 'B' if div == 'A' else ('A' if div == 'B' else 'C')
                    if (team_stats[home]['total_games'] < MAX_GAMES and 
                        team_stats[away]['total_games'] < MAX_GAMES and
                        day_of_week in team_availability[home] and 
                        day_of_week in team_availability[away] and
                        can_schedule_inter_division(home, away, team_stats, div, opp_div)):

                        # Balance home/away
                        if team_stats[home]['home_games'] < team_stats[home]['away_games']:
                            home, away = away, home

                        # Schedule game
                        schedule.append((date, slot, home, away, field))
                        team_stats[home]['total_games'] += 1
                        team_stats[home]['home_games'] += 1
                        team_stats[away]['total_games'] += 1
                        team_stats[away]['away_games'] += 1
                        team_stats[home]['inter_divisional'][opp_div] += 1
                        team_stats[away]['inter_divisional'][div] += 1
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

# Print schedule summary
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

    matchups = generate_matchups()
    schedule, team_stats = schedule_games(matchups, team_availability, field_availability)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("Schedule Generation Complete")
    print_schedule_summary(team_stats)

if __name__ == "__main__":
    main()
