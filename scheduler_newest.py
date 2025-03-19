import csv
import itertools
import random
from datetime import datetime, timedelta
from collections import defaultdict
from prettytable import PrettyTable

# -------------------------------
# Configurable parameters
# -------------------------------
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11
WEEKLY_GAME_LIMIT = 2  # max games per team per week
MAX_RETRIES = 20000  # scheduling backtracking limit
MIN_GAP = 2  # minimum days between game dates
MIN_DOUBLE_HEADERS = 7  # Updated: Minimum number of doubleheader sessions per team
MAX_DOUBLE_HEADERS = 9  # Updated: Maximum allowed doubleheader days per team

# -------------------------------
# Data Loading Functions
# -------------------------------
def load_team_availability(file_path):
    availability = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            team = row[0].strip()
            days = row[1:]
            availability[team] = {day.strip() for day in days if day.strip()}
    return availability

def load_field_availability(file_path):
    field_availability = []
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            date = datetime.strptime(row[0].strip(), '%Y-%m-%d')
            slot = row[1].strip()
            field = row[2].strip()
            field_availability.append((date, slot, field))
    return field_availability

def load_team_blackouts(file_path):
    blackouts = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header
        for row in reader:
            team = row[0].strip()
            dates = set()
            for d in row[1:]:
                d = d.strip()
                if d:
                    dt = datetime.strptime(d, '%Y-%m-%d').date()
                    dates.add(dt)
            blackouts[team] = dates
    return blackouts

# -------------------------------
# Doubleheader Scheduling
# -------------------------------
def schedule_doubleheaders_first(unscheduled, team_availability, field_availability, team_blackouts,
                                 timeslots_by_date, team_stats, doubleheader_count, team_game_days,
                                 team_game_slots, team_doubleheader_opponents, used_slots, schedule):
    """
    Schedules all doubleheaders before single games to ensure minimum and maximum requirements are met.
    """
    for d in sorted(timeslots_by_date.keys()):
        slots = timeslots_by_date[d]
        if len(slots) < 2:
            continue

        for team in list(team_stats.keys()):
            if doubleheader_count[team] >= MIN_DOUBLE_HEADERS:
                continue
            if d in team_blackouts.get(team, set()) or d.strftime('%a') not in team_availability.get(team, set()):
                continue

            matchups = [m for m in unscheduled if team in m]
            if len(matchups) < 2:
                continue

            for i in range(len(slots) - 1):
                slot1, slot2 = slots[i], slots[i + 1]
                available_fields1 = [(date, slot1, field) for (date, slot, field) in field_availability if date.date() == d and slot == slot1 and (date, slot1, field) not in used_slots]
                available_fields2 = [(date, slot2, field) for (date, slot, field) in field_availability if date.date() == d and slot == slot2 and (date, slot2, field) not in used_slots]
                
                if not available_fields1 or not available_fields2:
                    continue

                # Select two different opponents
                for combo in itertools.combinations(matchups, 2):
                    m1, m2 = combo
                    opp1 = m1[0] if m1[1] == team else m1[1]
                    opp2 = m2[0] if m2[1] == team else m2[1]
                    
                    if opp1 == opp2:
                        continue
                    if d in team_blackouts.get(opp1, set()) or d in team_blackouts.get(opp2, set()):
                        continue
                    if team_stats[opp1]['weekly_games'][d.isocalendar()[1]] >= WEEKLY_GAME_LIMIT:
                        continue
                    if team_stats[opp2]['weekly_games'][d.isocalendar()[1]] >= WEEKLY_GAME_LIMIT:
                        continue
                    
                    home1, away1 = (team, opp1) if team_stats[team]['home_games'] < HOME_AWAY_BALANCE else (opp1, team)
                    home2, away2 = (team, opp2) if team_stats[team]['home_games'] < HOME_AWAY_BALANCE else (opp2, team)

                    field1 = available_fields1[0][2]
                    field2 = available_fields2[0][2]

                    game1 = (d, slot1, field1, home1, away1)
                    game2 = (d, slot2, field2, home2, away2)

                    schedule.append(game1)
                    schedule.append(game2)

                    unscheduled.remove(m1)
                    unscheduled.remove(m2)

                    team_stats[team]['total_games'] += 2
                    team_stats[team]['weekly_games'][d.isocalendar()[1]] += 2
                    doubleheader_count[team] += 1
                    team_game_days[team][d] = 2
                    team_game_slots[team][d] = [slot1, slot2]
                    team_doubleheader_opponents[team][d].update([opp1, opp2])
                    used_slots[(d, slot1, field1)] = True
                    used_slots[(d, slot2, field2)] = True
                    
                    break  # Move to the next team
    
    return schedule, team_stats, doubleheader_count, used_slots, unscheduled
