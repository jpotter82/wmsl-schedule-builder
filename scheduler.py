import csv
import itertools
from datetime import datetime, timedelta
import random

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
    print("Loading team availability...")
    availability = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            team, days = row[0], row[1].split(',')
            availability[team] = set(days)
            print(f" - {team} available on: {', '.join(days)}")
    print("Team availability loaded.\n")
    return availability

# Load field availability from a CSV file, with AM/PM time formatting
def load_field_availability(file_path):
    print("Loading field availability...")
    field_availability = []
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            date = datetime.strptime(row[0], '%Y-%m-%d')
            slot = datetime.strptime(row[1], '%I:%M %p').strftime('%I:%M %p')  # Convert to 12-hour AM/PM format
            field = row[2]
            field_availability.append((date, slot, field))
            print(f" - {date.strftime('%Y-%m-%d')} at {slot} on {field}")
    print("Field availability loaded.\n")
    return field_availability

# Generate all intra-division and cross-division matchups
def generate_matchups():
    print("Generating matchups...")
    matchups = {}
    for div, teams in divisions.items():
        matchups[div] = list(itertools.combinations(teams, 2))
        print(f" - {div}-division matchups: {len(matchups[div])} games")
    cross_division_matchups = {
        "A-B": list(itertools.product(divisions['A'], divisions['B'])),
        "B-C": list(itertools.product(divisions['B'], divisions['C']))
    }
    print(f" - A-B cross-division matchups: {len(cross_division_matchups['A-B'])} games")
    print(f" - B-C cross-division matchups: {len(cross_division_matchups['B-C'])} games")
    print("Matchups generated.\n")
    return matchups, cross_division_matchups

# Schedule games based on availability and requirements
def schedule_games(matchups, cross_division_matchups, team_availability, field_availa
