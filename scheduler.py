import csv
import itertools
import random
from datetime import datetime, timedelta
from collections import defaultdict
from prettytable import PrettyTable

# Configurable parameters
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11
WEEKLY_GAME_LIMIT = 2  # max games per team per week
MAX_RETRIES = 10000    # scheduling backtracking limit

# Load team availability from CSV
def load_team_availability(file_path):
    availability = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # skip header
        for row in reader:
            team = row[0].strip()
            days = row[1:]
            availability[team] = {day.strip() for day in days if day.strip()}
    return availability

# Load field availability from CSV
def load_field_availability(file_path):
    field_availability = []
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # skip header
        for row in reader:
            date = datetime.strptime(row[0].strip(), '%Y-%m-%d')
            slot = row[1].strip()
            field = row[2].strip()
            field_availability.append((date, slot, field))
    # Sort field slots chronologically
    field_availability.sort(key=lambda x: x[0])
    return field_availability

# Intra-division matchups: every pair of teams plays two games (one home, one away)
def generate_intra_division_matchups(teams):
    matchups = []
    for team1, team2 in itertools.combinations(teams, 2):
        # Fixed home-away: one game at team1's field, one at team2's.
        matchups.append((team1, team2))
        matchups.append((team2, team1))
    return matchups

# Inter-division matchups generator using a round-robin (1-factorization) approach.
# For two divisions of equal size (assumed 8), we can generate 8 perfect matchings.
# Then randomly select the desired number (games_per_team) of rounds.
def generate_inter_division_matchups(teams1, teams2, games_per_team=4):
    n = len(teams1)  # assumed equal to len(teams2)
    rounds = []
    for r in range(n):
        round_match = []
        for i in range(n):
            # Pair team from teams1 with a team from teams2 using a cyclic offset
            round_match.append((teams1[i], teams2[(i + r) % n]))
        rounds.append(round_match)
    selected_rounds = random.sample(rounds, games_per_team)
    # Flatten the rounds into matchups list.
    matchups = []
    for round_match in selected_rounds:
        for matchup in round_match:
            # Randomly decide home/away for inter-divisional game
            if random.random() < 0.5:
                matchups.append(matchup)
            else:
                matchups.append((matchup[1], matchup[0]))
    return matchups

# Build full matchup list for all teams based on divisions.
def generate_full_matchups(division_teams):
    full_matchups = []

    # Intra-division games for each division (14 games per team)
    intra_matchups = {}
    for div, teams in division_teams.items():
        intra_matchups[div] = generate_intra_division_matchups(teams)
        full_matchups.extend(intra_matchups[div])

    # Inter-division games: For each pair of divisions, generate a 4-game-per-team pairing.
    # This ensures that for each division pair, each team gets 4 inter games.
    divisions = list(division_teams.keys())
    # For each unique pair (e.g. A-B, A-C, B-C)
    for i in range(len(divisions)):
        for j in range(i+1, len(divisions)):
            div1 = divisions[i]
            div2 = divisions[j]
            matchups = generate_inter_division_matchups(division_teams[div1], division_teams[div2], games_per_team=4)
            full_matchups.extend(matchups)
    # Shuffle overall matchups
    random.shuffle(full_matchups)
    return full_matchups

# Initialize team stats
def initialize_team_stats():
    return {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'weekly_games': defaultdict(int)
    }

# Schedule games into available field slots.
def schedule_games(matchups, team_availability, field_availability):
    schedule = []
    team_stats = defaultdict(initialize_team_stats)
    scheduled_slots = defaultdict(set)  # keys: (date, slot) -> set of teams
    unscheduled_matchups = matchups[:]
    retry_count = 0

    # While there are still matchups to schedule and we haven't hit retry limit
    while unscheduled_matchups and retry_count < MAX_RETRIES:
        progress_made = False
        # Iterate over available field slots
        for date, slot, field in field_availability:
            day_of_week = date.strftime('%a')
            week_num = date.isocalendar()[1]

            # Try to find a matchup that can be scheduled in this slot.
            for matchup in unscheduled_matchups[:]:  # iterate over a copy
                home, away = matchup

                # Check team availability for the day
                if day_of_week not in team_availability.get(home, set()) or day_of_week not in team_availability.get(away, set()):
                    continue

                # Check if teams are already scheduled in this slot
                if home in scheduled_slots[(date, slot)] or away in scheduled_slots[(date, slot)]:
                    continue

                # Check total game count and weekly game limits
                if (team_stats[home]['total_games'] >= MAX_GAMES or team_stats[away]['total_games'] >= MAX_GAMES):
                    continue
                if (team_stats[home]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT or
                    team_stats[away]['weekly_games'][week_num] >= WEEKLY_GAME_LIMIT):
                    continue

                # Check home/away balance. If home team already reached home limit, swap if possible.
                if team_stats[home]['home_games'] >= HOME_AWAY_BALANCE:
                    # Only swap if away team can take a home game.
                    if team_stats[away]['home_games'] < HOME_AWAY_BALANCE:
                        home, away = away, home
                    else:
                        continue

                # Schedule the game
                schedule.append((date, slot, field, home, home[0], away, away[0]))
                team_stats[home]['total_games'] += 1
                team_stats[home]['home_games'] += 1
                team_stats[away]['total_games'] += 1
                team_stats[away]['away_games'] += 1
                team_stats[home]['weekly_games'][week_num] += 1
                team_stats[away]['weekly_games'][week_num] += 1
                scheduled_slots[(date, slot)].update([home, away])
                unscheduled_matchups.remove(matchup)
                progress_made = True
                break  # move to next field slot once a game is scheduled

            if progress_made:
                # Once a game is scheduled in a slot, break out to re-start scanning from the first slot.
                break

        if not progress_made:
            retry_count += 1
        else:
            retry_count = 0

    if unscheduled_matchups:
        print("Warning: Retry limit reached. Some matchups could not be scheduled.")
    return schedule, team_stats

# Output schedule to CSV file
def output_schedule_to_csv(schedule, output_file):
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Diamond", "Home Team", "Home Division", "Away Team", "Away Division"])
        for game in schedule:
            date, slot, field, home, home_div, away, away_div = game
            writer.writerow([date.strftime('%Y-%m-%d'), slot, field, home, home_div, away, away_div])

# Print team schedule summary using PrettyTable
def print_schedule_summary(team_stats):
    table = PrettyTable()
    table.field_names = ["Division", "Team", "Total Games", "Home Games", "Away Games"]
    for team, stats in sorted(team_stats.items()):
        division = team[0]  # Assumes first character indicates division
        table.add_row([division, team, stats['total_games'], stats['home_games'], stats['away_games']])
    print("\nSchedule Summary:")
    print(table)

# Create a matchup count table between teams.
def generate_matchup_table(schedule, division_teams):
    matchup_count = defaultdict(lambda: defaultdict(int))
    for game in schedule:
        home_team = game[3]
        away_team = game[5]
        matchup_count[home_team][away_team] += 1
        matchup_count[away_team][home_team] += 1

    all_teams = sorted([team for teams in division_teams.values() for team in teams])
    table = PrettyTable()
    table.field_names = ["Team"] + all_teams
    for team in all_teams:
        row = [team] + [matchup_count[team][opp] for opp in all_teams]
        table.add_row(row)
    print("\nMatchup Table:")
    print(table)

# Main function
def main():
    # Load CSV data (update file paths as needed)
    team_availability = load_team_availability('team_availability.csv')
    field_availability = load_field_availability('field_availability.csv')
    
    # Debug output for availability
    print("\nTeam Availability Debug:")
    for team, days in team_availability.items():
        print(f"Team {team}: {', '.join(days)}")
    if not team_availability:
        print("ERROR: Team availability is empty!")
    
    print("\nField Availability Debug:")
    for entry in field_availability:
        print(f"Field Slot: {entry}")
    if not field_availability:
        print("ERROR: Field availability is empty!")
    
    # Define teams per division
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    
    # Generate full matchup list (intra-division and inter-division)
    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")
    
    # Schedule games into available field slots
    schedule, team_stats = schedule_games(matchups, team_availability, field_availability)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    generate_matchup_table(schedule, division_teams)

if __name__ == "__main__":
    main()
