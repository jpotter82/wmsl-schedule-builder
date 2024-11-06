import csv
import random
from datetime import datetime
from collections import defaultdict

# Configurable parameters
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11  # Maximum home or away games per team
WEEKLY_GAME_LIMIT = 2   # Max games per team per week

# Division rules with inter- and intra-divisional game requirements
DIVISION_RULES = {
    'A': {'intra_min': 2, 'extra': 4, 'inter_min': 4, 'inter_divisions': ['B']},
    'B': {'intra_min': 2, 'extra': 4, 'inter_min': 4, 'inter_divisions': ['A', 'C']},
    'C': {'intra_min': 2, 'extra': 4, 'inter_min': 4, 'inter_divisions': ['B']}
}

# Team setup by division
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

# Generate matchups for each division
def generate_matchups():
    matchups = {'A': [], 'B': [], 'C': [], 'AB': [], 'BC': []}
    for div, teams in divisions.items():
        intra_matchups = list(itertools.combinations(teams, 2))
        random.shuffle(intra_matchups)
        matchups[div].extend(intra_matchups)

    # Inter-divisional matchups
    matchups['AB'] = list(itertools.product(divisions['A'], divisions['B']))
    matchups['BC'] = list(itertools.product(divisions['B'], divisions['C']))
    random.shuffle(matchups['AB'])
    random.shuffle(matchups['BC'])
    return matchups

# Initialize team stats
def initialize_team_stats():
    return {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'intra_divisional': 0,
        'inter_divisional': defaultdict(int),
        'weekly_games': defaultdict(int)  # Track games per week to limit to WEEKLY_GAME_LIMIT
    }

# Check if inter-division games can be scheduled
def can_schedule_inter_division(team, opp_team, team_stats, team_div, opp_div):
    return (team_stats[team]['inter_divisional'][opp_div] < DIVISION_RULES[team_div]['inter_min'] and 
            team_stats[opp_team]['inter_divisional'][team_div] < DIVISION_RULES[opp_div]['inter_min'])

# Schedule games with constraints
def schedule_games(matchups, team_availability, field_availability):
    schedule = []
    team_stats = defaultdict(initialize_team_stats)
    scheduled_matchups = set()  # Track scheduled matchups to avoid repeats

    for date, slot, field in field_availability:
        day_of_week = date.strftime('%a')
        week_num = date.isocalendar()[1]
        scheduled_game = False

        # Try intra-division matchups first
        for div, games in matchups.items():
            if div in divisions and not scheduled_game:
                for matchup in games:
                    home, away = matchup
                    if (team_stats[home]['total_games'] < MAX_GAMES and 
                        team_stats[away]['total_games'] < MAX_GAMES and
                        day_of_week in team_availability[home] and 
                        day_of_week in team_availability[away] and
                        team_stats[home]['weekly_games'][week_num] < WEEKLY_GAME_LIMIT and
                        team_stats[away]['weekly_games'][week_num] < WEEKLY_GAME_LIMIT and
                        matchup not in scheduled_matchups):

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
                        team_stats[home]['weekly_games'][week_num] += 1
                        team_stats[away]['weekly_games'][week_num] += 1
                        scheduled_matchups.add(matchup)
                        scheduled_game = True
                        break

        # Try inter-division matchups if no intra scheduled
        if not scheduled_game:
            inter_matchups = matchups['AB'] if div == 'A' else matchups['BC'] if div == 'B' else []
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
                    scheduled_matchups.add(matchup)
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
