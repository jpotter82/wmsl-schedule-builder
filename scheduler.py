import csv
import itertools
import random
from datetime import datetime, timedelta
from collections import defaultdict
from prettytable import PrettyTable

# CONFIGURABLE PARAMETERS
MAX_GAMES = 22
HOME_AWAY_BALANCE = 11
WEEKLY_GAME_LIMIT = 2   # max games per team per week
MIN_GAP = 2             # minimum number of days between game nights

# -------------------------------
# DATA LOADING FUNCTIONS
# -------------------------------
def load_team_availability(file_path):
    availability = {}
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        for row in reader:
            team = row[0].strip()
            days = row[1:]
            availability[team] = {day.strip() for day in days if day.strip()}
    return availability

def load_field_availability(file_path):
    slots = []
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)  # Skip header
        for row in reader:
            dt = datetime.strptime(row[0].strip(), '%Y-%m-%d')
            slot = row[1].strip()
            field = row[2].strip()
            slots.append((dt, slot, field))
    slots.sort(key=lambda x: x[0])
    return slots

def load_team_blackouts(file_path):
    blackouts = {}
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            team = row[0].strip()
            dates = set()
            for d in row[1:]:
                d = d.strip()
                if d:
                    try:
                        dates.add(datetime.strptime(d, '%Y-%m-%d').date())
                    except Exception as e:
                        print(f"Error parsing blackout date '{d}' for team {team}: {e}")
            blackouts[team] = dates
    return blackouts

def load_doubleheader_dates(file_path):
    dheads = set()
    with open(file_path, mode='r') as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            d = row[0].strip()
            if d:
                try:
                    dheads.add(datetime.strptime(d, '%Y-%m-%d').date())
                except Exception as e:
                    print(f"Error parsing doubleheader date '{d}': {e}")
    return dheads

# -------------------------------
# MATCHUP GENERATION (UNCHANGED)
# -------------------------------
def assign_intra_division_weights(teams, two_game_count, three_game_count):
    pairs = list(itertools.combinations(sorted(teams), 2))
    count2 = {t: 0 for t in teams}
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
        for t1,t2 in itertools.combinations(sorted(teams), 2):
            matchups.append((t1,t2))
            matchups.append((t2,t1))
        return matchups
    elif division in ['A','C']:
        two_game_count = 3
        three_game_count = (len(teams)-1) - two_game_count
        assign = assign_intra_division_weights(teams, two_game_count, three_game_count)
        return generate_intra_matchups(teams, assign)
    else:
        raise Exception("Unknown division")

def generate_bipartite_regular_matchups(teams1, teams2, degree):
    teams1_order = teams1[:]
    random.shuffle(teams1_order)
    assign = {t: [] for t in teams1_order}
    capacity = {t: degree for t in teams2}
    def backtrack(i):
        if i == len(teams1_order):
            return True
        team = teams1_order[i]
        available = [t for t in teams2 if capacity[t]>0]
        for combo in itertools.combinations(available, degree):
            assign[team] = list(combo)
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
            for opp in assign[t]:
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
        return (t1,t2) if random.random() < 0.5 else (t2,t1)

# -------------------------------
# PERFECT MATCHING FOR LATE ROUND
# -------------------------------
def find_alternative_pairing(teams, early_pairing):
    """
    Given a list of teams (even number) and a dict early_pairing: team->opponent,
    find a perfect matching (list of (a,b) pairs) on teams such that for each pair (a,b),
    b != early_pairing.get(a) and a != early_pairing.get(b).
    Returns a list of pairs, or None if no such matching exists.
    """
    teams = list(teams)
    n = len(teams)
    if n % 2 != 0:
        return None
    pairing = []
    used = set()
    def backtrack():
        if len(used) == n:
            return True
        # pick first unused team
        for a in teams:
            if a not in used:
                break
        used.add(a)
        for b in teams:
            if b in used:
                continue
            # Ensure alternative pairing constraint:
            if early_pairing.get(a) == b or early_pairing.get(b) == a:
                continue
            used.add(b)
            pairing.append((a,b))
            if backtrack():
                return True
            pairing.pop()
            used.remove(b)
        used.remove(a)
        return False
    if backtrack():
        return pairing
    else:
        return None

# -------------------------------
# DOUBLEHEADER SCHEDULING (PHASE 1)
# -------------------------------
def schedule_doubleheader_for_date(d, early_slots, late_slots, unscheduled, team_availability, team_blackouts, team_game_days, team_stats):
    scheduled_games = []
    day_str = early_slots[0][0].strftime('%a')
    week_num = early_slots[0][0].isocalendar()[1]
    # Phase 1: Schedule early round games.
    early_matchups = {}
    used_matchups = []
    for slot in early_slots:
        candidate = None
        for matchup in unscheduled:
            a, b = matchup
            if day_str not in team_availability.get(a, set()) or day_str not in team_availability.get(b, set()):
                continue
            if d in team_blackouts.get(a, set()) or d in team_blackouts.get(b, set()):
                continue
            # Ensure neither team is already scheduled in the early round on d.
            if a in early_matchups or b in early_matchups:
                continue
            candidate = matchup
            break
        if candidate is None:
            # Unable to fill this early slot; break out.
            break
        unscheduled.remove(candidate)
        a, b = candidate
        home, away = decide_home_away(a, b, team_stats)
        # Record the early game for this slot.
        game = (slot[0], slot[1], slot[2], home, home[0], away, away[0])
        scheduled_games.append(game)
        early_matchups[home] = away
        early_matchups[away] = home
        team_game_days[home].add(d)
        team_game_days[away].add(d)
        team_stats[home]['total_games'] += 1
        team_stats[home]['home_games'] += 1
        team_stats[away]['total_games'] += 1
        team_stats[away]['away_games'] += 1
        team_stats[home]['weekly_games'][week_num] += 1
        team_stats[away]['weekly_games'][week_num] += 1
        used_matchups.append(candidate)
    # If we did not fill all early slots, we cannot force a full doubleheader.
    if len(early_matchups) * 1 != len(early_slots):
        return scheduled_games  # return what was scheduled (may be incomplete)
    # Phase 2: The set S of teams scheduled in early round
    S = set(early_matchups.keys())
    # Find an alternative pairing on S.
    alt_pairs = find_alternative_pairing(S, early_matchups)
    if alt_pairs is None or len(alt_pairs) < len(late_slots):
        # If no valid complete pairing is found, try to assign as many as possible.
        alt_pairs = alt_pairs or []
        # (This could be improved further.)
    # Now assign alt_pairs to late_slots (up to the number available).
    for i, slot in enumerate(late_slots):
        if i >= len(alt_pairs):
            break
        a, b = alt_pairs[i]
        home, away = decide_home_away(a, b, team_stats)
        game = (slot[0], slot[1], slot[2], home, home[0], away, away[0])
        scheduled_games.append(game)
        team_game_days[home].add(d)
        team_game_days[away].add(d)
        team_stats[home]['total_games'] += 1
        team_stats[home]['home_games'] += 1
        team_stats[away]['total_games'] += 1
        team_stats[away]['away_games'] += 1
        team_stats[home]['weekly_games'][week_num] += 1
        team_stats[away]['weekly_games'][week_num] += 1
    return scheduled_games

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
                team_game_days[home].add(d)
                team_game_days[away].add(d)
                team_stats[home]['total_games'] += 1
                team_stats[home]['home_games'] += 1
                team_stats[away]['total_games'] += 1
                team_stats[away]['away_games'] += 1
                team_stats[home]['weekly_games'][week_num] += 1
                team_stats[away]['weekly_games'][week_num] += 1
    return scheduled

# -------------------------------
# MAIN SCHEDULING FUNCTION
# -------------------------------
def schedule_games(matchups, field_slots, team_availability, team_blackouts, doubleheader_dates):
    schedule = []
    team_stats = defaultdict(lambda: {'total_games': 0, 'home_games': 0, 'away_games': 0, 'weekly_games': defaultdict(int)})
    team_game_days = defaultdict(set)  # team -> set of dates with a game

    # Group field slots by date.
    slots_by_date = defaultdict(list)
    for date_obj, slot, field in field_slots:
        slots_by_date[date_obj.date()].append((date_obj, slot, field))
    for d in slots_by_date:
        slots_by_date[d].sort(key=lambda x: x[0])

    unscheduled = matchups[:]

    # Phase 1: Process doubleheader dates first.
    for d in sorted(doubleheader_dates):
        if d not in slots_by_date:
            continue
        # For simplicity, assume that on a doubleheader day we use the two earliest slots per field.
        # Group slots by field.
        fields = defaultdict(list)
        for slot in slots_by_date[d]:
            fields[slot[2]].append(slot)
        dh_games = []
        for field, slots in fields.items():
            slots.sort(key=lambda x: x[0])
            if len(slots) < 2:
                continue
            # Use the first two slots as early and late.
            early_slot, late_slot = slots[0], slots[1]
            games = schedule_doubleheader_for_date(d, [early_slot], [late_slot], unscheduled, team_availability, team_blackouts, team_game_days, team_stats)
            dh_games.extend(games)
        schedule.extend(dh_games)

    # Phase 2: Process non-doubleheader dates.
    ndh_games = schedule_non_doubleheaders(unscheduled, slots_by_date, doubleheader_dates, team_availability, team_blackouts, team_game_days, team_stats)
    schedule.extend(ndh_games)
    if unscheduled:
        print("Warning: Some matchups could not be scheduled.")
    return schedule, team_stats

# -------------------------------
# OUTPUT & SUMMARY FUNCTIONS
# -------------------------------
def output_schedule_to_csv(schedule, output_file):
    with open(output_file, mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Date", "Time", "Diamond", "Home Team", "Home Division", "Away Team", "Away Division"])
        for game in schedule:
            dt, slot, field, home, home_div, away, away_div = game
            writer.writerow([dt.strftime('%Y-%m-%d'), slot, field, home, home[0], away, away[0]])

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
    all_teams = sorted([t for teams in division_teams.values() for t in teams])
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
