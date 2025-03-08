import csv
import itertools
import random
from datetime import datetime, timedelta
from collections import defaultdict
from prettytable import PrettyTable

# CONFIGURABLE PARAMETERS
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11
WEEKLY_GAME_LIMIT = 2  # max games per team per week
MIN_GAP = 2  # minimum days between game nights

# -------------------------------
# DATA LOADING FUNCTIONS
# -------------------------------
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

def load_field_availability(file_path):
    slots = []
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # skip header
        for row in reader:
            date_obj = datetime.strptime(row[0].strip(), '%Y-%m-%d')
            slot = row[1].strip()
            field = row[2].strip()
            slots.append((date_obj, slot, field))
    slots.sort(key=lambda x: x[0])
    return slots

def load_team_blackouts(file_path):
    blackouts = {}
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # skip header
        for row in reader:
            team = row[0].strip()
            dates = set()
            for d in row[1:]:
                d = d.strip()
                if d:
                    try:
                        dt = datetime.strptime(d, '%Y-%m-%d').date()
                        dates.add(dt)
                    except Exception as e:
                        print(f"Error parsing blackout date '{d}' for team {team}: {e}")
            blackouts[team] = dates
    return blackouts

def load_doubleheader_dates(file_path):
    dheads = set()
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # skip header
        for row in reader:
            d = row[0].strip()
            if d:
                try:
                    dt = datetime.strptime(d, '%Y-%m-%d').date()
                    dheads.add(dt)
                except Exception as e:
                    print(f"Error parsing doubleheader date '{d}': {e}")
    return dheads

# -------------------------------
# MATCHUP GENERATION (REUSED FROM BEFORE)
# -------------------------------
def assign_intra_division_weights(teams, two_game_count, three_game_count):
    pairs = list(itertools.combinations(sorted(teams), 2))
    count2 = {team: 0 for team in teams}
    assignment = {}
    def backtrack(i):
        if i == len(pairs):
            return all(count2[t] == two_game_count for t in teams)
        t1, t2 = pairs[i]
        if count2[t1] < two_game_count and count2[t2] < two_game_count:
            assignment[(t1,t2)] = 2
            count2[t1] += 1
            count2[t2] += 1
            if backtrack(i+1):
                return True
            count2[t1] -= 1
            count2[t2] -= 1
            del assignment[(t1,t2)]
        assignment[(t1,t2)] = 3
        if backtrack(i+1):
            return True
        del assignment[(t1,t2)]
        return False
    if backtrack(0):
        return assignment
    else:
        raise Exception("Intra-division assignment failed.")

def generate_intra_matchups(teams, weight_assignment):
    matchups = []
    for (t1,t2), weight in weight_assignment.items():
        if weight == 2:
            matchups.append((t1,t2))
            matchups.append((t2,t1))
        elif weight == 3:
            matchups.append((t1,t2))
            matchups.append((t2,t1))
            if random.random() < 0.5:
                matchups.append((t1,t2))
            else:
                matchups.append((t2,t1))
    return matchups

def generate_intra_division_matchups(division, teams):
    if division == 'B':
        matchups = []
        for t1,t2 in itertools.combinations(sorted(teams),2):
            matchups.append((t1,t2))
            matchups.append((t2,t1))
        return matchups
    elif division in ['A','C']:
        two_game_count = 3
        three_game_count = (len(teams)-1) - two_game_count
        assignment = assign_intra_division_weights(teams, two_game_count, three_game_count)
        return generate_intra_matchups(teams, assignment)
    else:
        raise Exception("Unknown division.")

def generate_bipartite_regular_matchups(teams1, teams2, degree):
    teams1_order = teams1[:]
    random.shuffle(teams1_order)
    assignment = {t: [] for t in teams1_order}
    capacity = {t: degree for t in teams2}
    def backtrack(i):
        if i == len(teams1_order):
            return True
        team = teams1_order[i]
        available = [t for t in teams2 if capacity[t]>0]
        for combo in itertools.combinations(available, degree):
            assignment[team] = list(combo)
            for t in combo:
                capacity[t] -= 1
            if backtrack(i+1):
                return True
            for t in combo:
                capacity[t] += 1
        return False
    if backtrack(0):
        edges = []
        for t in teams1_order:
            for opp in assignment[t]:
                edges.append((t,opp))
        return edges
    else:
        raise Exception("Bipartite matching failed.")

def generate_inter_division_matchups(division_from, division_to, teams_from, teams_to):
    degree = 4
    edges = generate_bipartite_regular_matchups(teams_from, teams_to, degree)
    matchups = []
    for (t1,t2) in edges:
        if random.random() < 0.5:
            matchups.append((t1,t2))
        else:
            matchups.append((t2,t1))
    return matchups

def generate_full_matchups(division_teams):
    full = []
    for div, teams in division_teams.items():
        full.extend(generate_intra_division_matchups(div, teams))
    full.extend(generate_inter_division_matchups('A','B', division_teams['A'], division_teams['B']))
    full.extend(generate_inter_division_matchups('B','C', division_teams['B'], division_teams['C']))
    random.shuffle(full)
    return full

# -------------------------------
# HOME/AWAY HELPER
# -------------------------------
def decide_home_away(t1, t2, team_stats):
    if team_stats[t1]['home_games'] >= HOME_AWAY_BALANCE and team_stats[t2]['home_games'] < HOME_AWAY_BALANCE:
        return t2, t1
    if team_stats[t2]['home_games'] >= HOME_AWAY_BALANCE and team_stats[t1]['home_games'] < HOME_AWAY_BALANCE:
        return t1, t2
    if team_stats[t1]['home_games'] < team_stats[t2]['home_games']:
        return t1, t2
    elif team_stats[t2]['home_games'] < team_stats[t1]['home_games']:
        return t2, t1
    else:
        return (t1,t2) if random.random()<0.5 else (t2,t1)

# -------------------------------
# HELPER FOR MIN GAP
# -------------------------------
def min_gap_ok(team, d, team_game_days):
    for gd in team_game_days[team]:
        if gd != d and (d - gd).days < MIN_GAP:
            return False
    return True

# -------------------------------
# DOUBLEHEADER SCHEDULING (PHASE 1)
# -------------------------------
def schedule_doubleheaders(unscheduled, slots_by_date, doubleheader_dates, team_availability, team_blackouts, team_game_days, team_stats):
    scheduled = []
    # For each doubleheader date d, process each field separately.
    # We maintain a dictionary to record, per date, each team’s opponent (if already scheduled) on that day.
    opponents_on_day = defaultdict(dict)
    for d in sorted(doubleheader_dates):
        if d not in slots_by_date:
            continue
        # Group slots on d by field.
        fields = defaultdict(list)
        for slot in slots_by_date[d]:
            fields[slot[2]].append(slot)
        # For each field that has at least 2 slots, we aim to schedule 2 games.
        for field, slots in fields.items():
            slots.sort(key=lambda x: x[0])
            if len(slots) < 2:
                continue
            early, late = slots[0], slots[1]
            day_str = early[0].strftime('%a')
            week_num = early[0].isocalendar()[1]
            # Find candidate matchup for early slot:
            cand1 = None
            for matchup in unscheduled:
                a, b = matchup
                if day_str not in team_availability.get(a, set()) or day_str not in team_availability.get(b, set()):
                    continue
                if d in team_blackouts.get(a, set()) or d in team_blackouts.get(b, set()):
                    continue
                if not (min_gap_ok(a, d, team_game_days) and min_gap_ok(b, d, team_game_days)):
                    continue
                # For an early game, neither team should already have a game on d.
                if a in opponents_on_day[d] or b in opponents_on_day[d]:
                    continue
                cand1 = matchup
                break
            if cand1 is None:
                continue  # cannot schedule on this field
            unscheduled.remove(cand1)
            a, b = cand1
            home1, away1 = decide_home_away(a, b, team_stats)
            game1 = (early[0], early[1], early[2], home1, home1[0], away1, away1[0])
            scheduled.append(game1)
            # Update stats and record opponents:
            week = early[0].isocalendar()[1]
            for team, is_home, opp in [(home1, True, away1), (away1, False, home1)]:
                team_stats[team]['total_games'] += 1
                if is_home:
                    team_stats[team]['home_games'] += 1
                else:
                    team_stats[team]['away_games'] += 1
                team_stats[team]['weekly_games'][week] += 1
                team_game_days[team].add(d)
                opponents_on_day[d][team] = opp
            # Now for late slot: find a candidate where if a team is already scheduled on d, its opponent is different.
            cand2 = None
            for matchup in unscheduled:
                a, b = matchup
                if day_str not in team_availability.get(a, set()) or day_str not in team_availability.get(b, set()):
                    continue
                if d in team_blackouts.get(a, set()) or d in team_blackouts.get(b, set()):
                    continue
                if not (min_gap_ok(a, d, team_game_days) and min_gap_ok(b, d, team_game_days)):
                    continue
                # If a team already played on d, ensure new opponent is different.
                if a in opponents_on_day[d] and opponents_on_day[d][a] == b:
                    continue
                if b in opponents_on_day[d] and opponents_on_day[d][b] == a:
                    continue
                cand2 = matchup
                break
            if cand2 is None:
                # In order to force a full slate, if no candidate is found we may try to reuse a candidate from a pool
                continue
            unscheduled.remove(cand2)
            a, b = cand2
            home2, away2 = decide_home_away(a, b, team_stats)
            game2 = (late[0], late[1], late[2], home2, home2[0], away2, away2[0])
            scheduled.append(game2)
            week = late[0].isocalendar()[1]
            for team, is_home, opp in [(home2, True, away2), (away2, False, home2)]:
                team_stats[team]['total_games'] += 1
                if is_home:
                    team_stats[team]['home_games'] += 1
                else:
                    team_stats[team]['away_games'] += 1
                team_stats[team]['weekly_games'][week] += 1
                team_game_days[team].add(d)
                opponents_on_day[d][team] = opp
    return scheduled, unscheduled

# -------------------------------
# NON-DOUBLEHEADER SCHEDULING (PHASE 2)
# -------------------------------
def schedule_non_doubleheaders(unscheduled, slots_by_date, doubleheader_dates, team_availability, team_blackouts, team_game_days, team_stats):
    scheduled = []
    for d in sorted(slots_by_date.keys()):
        if d in doubleheader_dates:
            continue
        slots = sorted(slots_by_date[d], key=lambda x: x[0])
        day_str = slots[0][0].strftime('%a')
        week_num = slots[0][0].isocalendar()[1]
        for slot in slots:
            cand = None
            for matchup in unscheduled:
                a, b = matchup
                if day_str not in team_availability.get(a, set()) or day_str not in team_availability.get(b, set()):
                    continue
                if d in team_blackouts.get(a, set()) or d in team_blackouts.get(b, set()):
                    continue
                if not (min_gap_ok(a, d, team_game_days) and min_gap_ok(b, d, team_game_days)):
                    continue
                cand = matchup
                break
            if cand:
                unscheduled.remove(cand)
                a, b = cand
                home, away = decide_home_away(a, b, team_stats)
                game = (slot[0], slot[1], slot[2], home, home[0], away, away[0])
                scheduled.append(game)
                team_stats[home]['total_games'] += 1
                team_stats[home]['home_games'] += 1
                team_stats[away]['total_games'] += 1
                team_stats[away]['away_games'] += 1
                team_stats[home]['weekly_games'][week_num] += 1
                team_stats[away]['weekly_games'][week_num] += 1
                team_game_days[home].add(d)
                team_game_days[away].add(d)
    return scheduled, unscheduled

# -------------------------------
# MAIN SCHEDULING FUNCTION
# -------------------------------
def schedule_games(matchups, field_slots, team_availability, team_blackouts, doubleheader_dates):
    schedule = []
    team_stats = defaultdict(lambda: {'total_games': 0, 'home_games': 0, 'away_games': 0, 'weekly_games': defaultdict(int)})
    team_game_days = defaultdict(set)  # team -> set of dates on which team already has a game

    # Group field slots by date.
    slots_by_date = defaultdict(list)
    for date_obj, slot, field in field_slots:
        slots_by_date[date_obj.date()].append((date_obj, slot, field))
    for d in slots_by_date:
        slots_by_date[d].sort(key=lambda x: x[0])
    
    # Phase 1: Schedule doubleheader dates first.
    scheduled_dh, unscheduled = schedule_doubleheaders(matchups, slots_by_date, doubleheader_dates, team_availability, team_blackouts, team_game_days, team_stats)
    schedule.extend(scheduled_dh)
    
    # Phase 2: Process remaining dates.
    scheduled_ndh, unscheduled = schedule_non_doubleheaders(unscheduled, slots_by_date, doubleheader_dates, team_availability, team_blackouts, team_game_days, team_stats)
    schedule.extend(scheduled_ndh)
    
    if unscheduled:
        print("Warning: Some matchups could not be scheduled.")
    return schedule, team_stats

# -------------------------------
# OUTPUT & SUMMARY FUNCTIONS
# -------------------------------
def output_schedule_to_csv(schedule, output_file):
    with open(output_file, mode='w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["Date", "Time", "Diamond", "Home Team", "Home Division", "Away Team", "Away Division"])
        for game in schedule:
            date, slot, field, home, home_div, away, away_div = game
            writer.writerow([date.strftime('%Y-%m-%d'), slot, field, home, home[0], away, away[0]])

def print_schedule_summary(team_stats):
    table = PrettyTable()
    table.field_names = ["Division", "Team", "Total Games", "Home Games", "Away Games"]
    for team, stats in sorted(team_stats.items()):
        table.add_row([team[0], team, stats['total_games'], stats['home_games'], stats['away_games']])
    print("\nSchedule Summary:")
    print(table)

def generate_matchup_table(schedule, division_teams):
    matchup_count = defaultdict(lambda: defaultdict(int))
    for game in schedule:
        home = game[3]
        away = game[5]
        matchup_count[home][away] += 1
        matchup_count[away][home] += 1
    all_teams = sorted([team for teams in division_teams.values() for team in teams])
    table = PrettyTable()
    table.field_names = ["Team"] + all_teams
    for team in all_teams:
        row = [team] + [matchup_count[team][opp] for opp in all_teams]
        table.add_row(row)
    print("\nMatchup Table:")
    print(table)

# -------------------------------
# MAIN
# -------------------------------
def main():
    team_availability = load_team_availability('team_availability.csv')
    field_slots = load_field_availability('field_availability.csv')
    team_blackouts = load_team_blackouts('team_blackouts.csv')
    doubleheader_dates = load_doubleheader_dates('doubleheaders.csv')
    
    print("\nTeam Availability Debug:")
    for team, days in team_availability.items():
        print(f"{team}: {', '.join(days)}")
    print("\nField Slots:")
    for slot in field_slots:
        print(slot)
    print("\nTeam Blackouts Debug:")
    for team, dates in team_blackouts.items():
        print(f"{team}: {', '.join(str(d) for d in dates)}")
    print("\nDoubleheader Dates:")
    for d in sorted(doubleheader_dates):
        print(d)
    
    division_teams = {
        'A': [f'A{i+1}' for i in range(8)],
        'B': [f'B{i+1}' for i in range(8)],
        'C': [f'C{i+1}' for i in range(8)]
    }
    
    matchups = generate_full_matchups(division_teams)
    print(f"\nTotal generated matchups (unscheduled): {len(matchups)}")
    
    schedule, team_stats = schedule_games(matchups, field_slots, team_availability, team_blackouts, doubleheader_dates)
    output_schedule_to_csv(schedule, 'softball_schedule.csv')
    print("\nSchedule Generation Complete")
    print_schedule_summary(team_stats)
    generate_matchup_table(schedule, division_teams)

if __name__ == "__main__":
    main()
