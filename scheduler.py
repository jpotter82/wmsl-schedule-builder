import csv
import itertools
import random
from datetime import datetime, timedelta
from collections import defaultdict
from prettytable import PrettyTable  # Add PrettyTable for better formatting

# Configurable parameters
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11
DIVISION_RULES = {
    'A': {'intra_extra': {'3_times': 4, '2_times': 3}, 'inter': {'B': 4}},
    'B': {'intra_extra': {'3_times': 0, '2_times': 7}, 'inter': {'A': 4, 'C': 4}},
    'C': {'intra_extra': {'3_times': 4, '2_times': 3}, 'inter': {'B': 4}}
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
            availability[team] = {day.strip() for day in days if day.strip()}
    return availability

# Load field availability
def load_field_availability(file_path):
    field_availability = []
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            date = datetime.strptime(row[0], '%Y-%m-%d')
            slot = row[1]
            field = row[2]
            field_availability.append((date, slot, field))
    return field_availability

# Initialize team stats
def initialize_team_stats():
    return {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'weekly_games': defaultdict(int),
        'intra_games': defaultdict(int),  # Tracks how many times they play intra teams
        'inter_games': defaultdict(int),  # Tracks how many times they play inter teams
    }

import itertools
import random

# Generate Matchups
def generate_matchups(rules, division_teams):
    matchups = []
    
    # Helper function to generate home/away balanced matchups
    def generate_home_away_matchups(teams, count_per_team):
        matchups = []
        for team1, team2 in itertools.combinations(teams, 2):
            # Generate home/away matchups
            matchups.append((team1, team2))  # Team1 home, Team2 away
            matchups.append((team2, team1))  # Team2 home, Team1 away
        random.shuffle(matchups)  # Shuffle for randomness
        return matchups[:count_per_team]

    # Intra-divisional matchups
    for division, rules_for_division in rules.items():
        intra_extra = rules_for_division['intra_extra']
        intra_teams = [f'{division}{i+1}' for i in range(8)]
        
        # For A and C Divisions: 
        # 3 times against 4 teams (2 home and away games)
        # 2 times against 3 teams (1 home and 1 away, random for the other)
        if division in ['A', 'C']:
            # 3 times against 4 teams (home/away)
            teams_3_times = random.sample(intra_teams, 4)
            for team in teams_3_times:
                for opponent in teams_3_times:
                    if team != opponent:
                        matchups.append((team, opponent))
                        matchups.append((opponent, team))

            # 2 times against 3 teams (1 home and 1 away, random for the other)
            teams_2_times = random.sample(intra_teams, 3)
            for team in teams_2_times:
                for opponent in teams_2_times:
                    if team != opponent:
                        matchups.append((team, opponent))
                        matchups.append((opponent, team))

        # For B Division: Play each of the 7 teams only 2 times (1 home and 1 away)
        elif division == 'B':
            # 2 times against 7 teams (home and away)
            for team1, team2 in itertools.combinations(intra_teams, 2):
                matchups.append((team1, team2))  # Team1 home, Team2 away
                matchups.append((team2, team1))  # Team2 home, Team1 away

    # Inter-divisional matchups
    inter_divisional_games = rules.get('inter', {})
    for inter_div, count in inter_divisional_games.items():
        inter_teams = [f'{inter_div}{i+1}' for i in range(8)]
        inter_matchups = list(itertools.product(division_teams, inter_teams))
        random.shuffle(inter_matchups)
        
        # Add the inter-divisional matchups based on count
        matchups.extend(inter_matchups[:count])

    # Debug: Check inter-division matchups
    print(f"Inter-division matchups generated: {len(matchups)}")

    # Shuffle the final matchups list
    random.shuffle(matchups)

    # Debug: Total matchups generated
    print(f"Total matchups generated: {len(matchups)}")

    # Print all matchups
    print("Generated Matchups:")
    for matchup in matchups:
        print(matchup)

    return matchups

# Schedule the games
def schedule_games(matchups, team_availability, field_availability):
    schedule = []
    team_stats = defaultdict(initialize_team_stats)
    scheduled_slots = defaultdict(set)
    unscheduled_matchups = matchups[:]

    retry_count = 0
    max_retries = 10000  # Increase retry limit to handle a high number of attempts

    while unscheduled_matchups and retry_count < max_retries:
        progress_made = False

        for date, slot, field in field_availability:
            day_of_week = date.strftime('%a')
            week_num = date.isocalendar()[1]

            for matchup in unscheduled_matchups[:]:  # Iterate over a copy of matchups
                home, away = matchup

                # Constraints check
                if (team_stats[home]['total_games'] < MAX_GAMES and
                    team_stats[away]['total_games'] < MAX_GAMES and
                    day_of_week in team_availability[home] and
                    day_of_week in team_availability[away] and
                    home not in scheduled_slots[(date, slot)] and
                    away not in scheduled_slots[(date, slot)]):
                    
                    # Relax weekly game constraints to ensure all games are scheduled
                    if (team_stats[home]['weekly_games'][week_num] < 2 and
                        team_stats[away]['weekly_games'][week_num] < 2) or retry_count > 5000:

                        # Swap home/away if home quota is exceeded
                        if team_stats[home]['home_games'] >= HOME_AWAY_BALANCE:
                            home, away = away, home

                        # Schedule the game
                        schedule.append((date, slot, field, home, home[0], away, away[0]))
                        team_stats[home]['total_games'] += 1
                        team_stats[home]['home_games'] += 1
                        team_stats[away]['total_games'] += 1
                        team_stats[away]['away_games'] += 1
                        team_stats[home]['weekly_games'][week_num] += 1
                        team_stats[away]['weekly_games'][week_num] += 1
                        scheduled_slots[(date, slot)].update([home, away])

                        # Do NOT remove matchup from unscheduled yet
                        progress_made = True
                        break

            if progress_made:
                break

        # Retry unscheduled matchups
        if not progress_made:
            retry_count += 1
        else:
            retry_count = 0  # Reset if progress made

    if retry_count >= max_retries:
        print("Warning: Retry limit reached. Some matchups could not be scheduled.")

    # Return the final schedule and team statistics
    return schedule, team_stats

# Output schedule to CSV
def output_schedule_to_csv(schedule, output_file):
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Diamond", "Home Team", "Home Division", "Away Team", "Away Division"])
        for game in schedule:
            writer.writerow([game[0].strftime('%Y-%m-%d'), game[1], game[2], game[3], game[4], game[5], game[6]])

# Print a readable table summary
def print_schedule_summary(team_stats):
    # Initialize PrettyTable
    table = PrettyTable()
    table.field_names = ["Division", "Team", "Total Games", "Home Games", "Away Games", "Intra Games", "Inter Games"]

    for team, stats in sorted(team_stats.items()):
        division = team[0]  # First character of team name (A, B, or C)
        intra_games = stats['intra_games'][division]
        inter_games = sum(stats['inter_games'].values())

        table.add_row([
            division,
            team,
            stats['total_games'],
            stats['home_games'],
            stats['away_games'],
            intra_games,
            inter_games
        ])

    print("\nSchedule Summary:")
    print(table)

from prettytable import PrettyTable

def generate_matchup_table(schedule, division_teams):
    # Initialize matchup tracker
    matchup_count = defaultdict(lambda: defaultdict(int))

    # Populate matchup tracker from schedule
    for game in schedule:
        home_team = game[3]
        away_team = game[5]
        matchup_count[home_team][away_team] += 1
        matchup_count[away_team][home_team] += 1

    # Sort teams for consistency
    all_teams = sorted([team for teams in division_teams.values() for team in teams])

    # Create the table
    table = PrettyTable()
    table.field_names = ["Team"] + all_teams

    for team in all_teams:
        row = [team]
        for opponent in all_teams:
            row.append(matchup_count[team][opponent])
        table.add_row(row)

    print("\nMatchup Table:")
    print(table)

# Main function
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    
    # Debug team availability
    print("\nTeam Availability Debug:")
    for team, days in team_availability.items():
        print(f"Team {team}: {', '.join(days)}")
    if not team_availability:
        print("ERROR: Team availability is empty!")

    # Debug field availability
    print("\nField Availability Debug:")
    for entry in field_availability:
        print(f"Field Slot: {entry}")
    if not field_availability:
        print("ERROR: Field availability is empty!")
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)],
    }

    matchups = {div: generate_matchups(teams, DIVISION_RULES[div]) for div, teams in division_teams.items()}
    flat_matchups = [match for matches in matchups.values() for match in matches]

    schedule, team_stats = schedule_games(flat_matchups, team_availability, field_availability)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("Schedule Generation Complete")
    print_schedule_summary(team_stats)
    generate_matchup_table(schedule, division_teams)

if __name__ == "__main__":
    main()
