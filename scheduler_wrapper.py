import io
import os
import re
import sys
import random
import traceback
from collections import defaultdict
from datetime import datetime

import scheduler_newest as sn


DEFAULT_CONFIG = {
    'divisions': {
        'A': {'team_count': 6, 'inter': False, 'target_games': 14, 'min_dh': 6, 'max_dh': 6, 'dh_only': True},
        'B': {'team_count': 8, 'inter': False, 'target_games': 14, 'min_dh': 6, 'max_dh': 6, 'dh_only': False},
        'C': {'team_count': 6, 'inter': False, 'target_games': 14, 'min_dh': 6, 'max_dh': 6, 'dh_only': False},
    },
    'general': {
        'max_retries': 20000,
        'preferred_min_gap': 3,
        'hard_min_gap': 2,
        'weekly_game_limit': 2,
        'home_away_balance': 7,
        'random_seed': None,
        'attempts': 1,
        'weekly_soft_target': None,
        'weekly_balance_penalty': 2500,
        'front_load_weeks': 0,
    },
    'pair_rules': {
        'A': {'min': 2, 'soft_cap': 4},
        'B': {'min': 1, 'soft_cap': 3},
        'C': {'min': 1, 'soft_cap': 3},
    },
    # Inter-division play. A pair only generates games when BOTH divisions have
    # inter=True AND the pair is listed here with enabled=True. 'degree' is the
    # average number of cross-division games per team against that division.
    'inter_pairs': {
        'A-B': {'enabled': False, 'degree': 0},
        'A-C': {'enabled': False, 'degree': 0},
        'B-C': {'enabled': False, 'degree': 4},
    },
    'sunday_pod_rotation': ['B', 'C', 'A'],
    'sunday_pods_per_sunday': 3,
    'sunday_priority': 0,
    'sunday_pods_only': False,
}


def _patch_globals(config):
    div_settings = {}
    for div_name, div_cfg in config['divisions'].items():
        div_settings[div_name] = {
            'inter': div_cfg.get('inter', False),
            'target_games': div_cfg.get('target_games', 14),
            'min_dh': div_cfg.get('min_dh', 6),
            'max_dh': div_cfg.get('max_dh', 6),
            'dh_only': div_cfg.get('dh_only', False),
        }
    sn.DIVISION_SETTINGS = div_settings

    pair_rules = {}
    for div_name, rules in config.get('pair_rules', {}).items():
        pair_rules[div_name] = {'min': rules.get('min', 1), 'soft_cap': rules.get('soft_cap', 3)}
    if pair_rules:
        sn.PAIR_RULES = pair_rules

    # Inter-division pair enablement + degree. Keys arrive as "B-C" strings.
    inter_pair_settings = {}
    inter_degree = {}
    for key, cfg in (config.get('inter_pairs') or {}).items():
        parts = [p.strip().upper() for p in str(key).split('-') if p.strip()]
        if len(parts) != 2:
            continue
        pair = (parts[0], parts[1])
        enabled = bool(cfg.get('enabled', False))
        inter_pair_settings[pair] = enabled
        if enabled:
            inter_degree[pair] = int(cfg.get('degree', 0) or 0)
    sn.INTER_PAIR_SETTINGS = inter_pair_settings
    sn.INTER_DEGREE = inter_degree

    gen = config.get('general', {})
    sn.MAX_RETRIES = gen.get('max_retries', 20000)
    sn.PREFERRED_MIN_GAP = gen.get('preferred_min_gap', 3)
    sn.HARD_MIN_GAP = gen.get('hard_min_gap', 2)
    sn.WEEKLY_GAME_LIMIT = gen.get('weekly_game_limit', 2)
    # Soft weekly target: what the scheduler aims for, vs the hard limit above.
    # None/0 means "derive an even spread from the season length".
    sn.WEEKLY_SOFT_TARGET = gen.get('weekly_soft_target') or None
    # Clamped to the reference so the dial has a real ceiling: the UI offers 0-2500,
    # and anything above (e.g. from the earlier 0-5000 scale) behaves as full strength
    # rather than silently pushing pacing harder than any UI option can express.
    _pen = gen.get('weekly_balance_penalty')
    _ref = getattr(sn, 'WEEKLY_BALANCE_REFERENCE', 2500)
    sn.WEEKLY_BALANCE_PENALTY = _ref if _pen is None else min(_ref, max(0, int(_pen)))
    sn.HOME_AWAY_BALANCE = gen.get('home_away_balance', 7)
    sn.RANDOM_SEED = gen.get('random_seed', None)
    sn.SUNDAY_POD_ROTATION = config.get('sunday_pod_rotation', ['B', 'C', 'A'])
    sn.SUNDAY_PODS_PER_SUNDAY = config.get('sunday_pods_per_sunday', 3)
    sn.SUNDAY_PRIORITY = max(0, int(config.get('sunday_priority', 0) or 0))
    sn.SUNDAY_PODS_ONLY = bool(config.get('sunday_pods_only', False))
    sn.FRONT_LOAD_WEEKS = max(0, int(gen.get('front_load_weeks', 0) or 0))


def _build_division_teams(config):
    division_teams = {}
    for div_name in sorted(config['divisions'].keys()):
        count = config['divisions'][div_name]['team_count']
        division_teams[div_name] = ["{}{}".format(div_name, i + 1) for i in range(count)]
    return division_teams


def validate_config(config):
    """Return a list of human-readable warnings about a config.

    These are advisory, not fatal — the scheduler will still run.
    """
    warnings = []
    divisions = config.get('divisions', {})

    for name, d in divisions.items():
        count = d.get('team_count', 0)
        target = d.get('target_games', 0)
        dh_only = d.get('dh_only', False)

        if dh_only:
            if count < 4:
                warnings.append(
                    f"Division {name} is doubleheader-only but has {count} teams. "
                    f"Pods need at least 4 teams — this division cannot be scheduled."
                )
            if count % 4 != 0 and count >= 4:
                warnings.append(
                    f"Division {name} is doubleheader-only with {count} teams. "
                    f"Pods use 4 teams at a time, so some dates will leave teams idle."
                )
            if target % 2 != 0:
                warnings.append(
                    f"Division {name} is doubleheader-only but target_games={target} is odd. "
                    f"Every pod gives 2 games, so teams will finish 1 game short."
                )
        if d.get('min_dh', 0) > d.get('max_dh', 0):
            warnings.append(f"Division {name}: min_dh ({d.get('min_dh')}) is greater than max_dh ({d.get('max_dh')}).")
        if d.get('min_dh', 0) * 2 > target:
            warnings.append(
                f"Division {name}: min_dh={d.get('min_dh')} needs {d.get('min_dh') * 2} games "
                f"but target_games={target}."
            )

    # Inter-division sanity
    for key, cfg in (config.get('inter_pairs') or {}).items():
        if not cfg.get('enabled'):
            continue
        parts = [p.strip().upper() for p in str(key).split('-') if p.strip()]
        if len(parts) != 2:
            continue
        d1, d2 = parts
        for d in (d1, d2):
            if d not in divisions:
                warnings.append(f"Inter-division pair {key} is enabled but division {d} does not exist.")
            elif not divisions[d].get('inter', False):
                warnings.append(
                    f"Inter-division pair {key} is enabled, but division {d} does not have "
                    f"its 'Inter' checkbox ticked — no cross-division games will be created."
                )
            elif divisions[d].get('dh_only', False):
                warnings.append(
                    f"Inter-division pair {key} is enabled, but division {d} is doubleheader-only. "
                    f"Pod-only divisions build their own matchups and cannot play cross-division games."
                )

    if config.get('sunday_pods_only') and not config.get('sunday_priority'):
        warnings.append(
            "Sundays are reserved for doubleheaders, but Sunday Priority is 0. "
            "Those slots can go unused unless pods actively seek them out — consider raising it."
        )

    return warnings


def analyse_sundays(field_availability, pods_per_sunday):
    """Report whether each Sunday's inventory divides cleanly into doubleheader pods.

    A pod needs two back-to-back timeslots on two diamonds (4 slots). A Sunday whose
    slots do not divide by 4 will always leave a remainder that only a single game
    can fill.
    """
    from collections import defaultdict as _dd
    by_date = _dd(list)
    for dt, slot, field in field_availability:
        if dt.date().weekday() == 6:
            by_date[dt.date()].append((slot, field))

    out = []
    for d in sorted(by_date):
        entries = by_date[d]
        times = sorted({s for s, _f in entries},
                       key=lambda s: datetime.strptime(s.strip(), "%I:%M %p"))
        pods = 0
        i = 0
        while i < len(times) - 1:
            a = len({f for s, f in entries if s == times[i]})
            b = len({f for s, f in entries if s == times[i + 1]})
            if a >= 2 and b >= 2:
                pods += 1
                i += 2
            else:
                i += 1
        capped = min(pods, pods_per_sunday) if pods_per_sunday else pods
        out.append({
            'date': d.strftime('%Y-%m-%d'),
            'slots': len(entries),
            'times': len(times),
            'pods_possible': pods,
            'pods_allowed': capped,
            'slots_used_by_pods': capped * 4,
            'leftover_slots': len(entries) - capped * 4,
        })
    return out


def _weekly_shape(schedule, all_teams):
    """Measure how evenly each team's games are spread across the season's weeks.

    Returns (idle_weeks, heavy_weeks, spread, per_team) where:
      idle_weeks  - team-weeks with no games at all (before the team's first game
                    and after its last are not counted; only gaps inside its season)
      heavy_weeks - team-weeks more than 1 game above the team's soft weekly target
      spread      - total (busiest week - quietest week) summed over teams
    """
    weeks = sorted({dt.isocalendar()[1] for dt, *_ in schedule}) if schedule else []
    if not weeks:
        return 0, 0, 0, {}

    per_team = {t: {w: 0 for w in weeks} for t in all_teams}
    for dt, slot, field, home, hd, away, ad in schedule:
        w = dt.isocalendar()[1]
        if home in per_team:
            per_team[home][w] += 1
        if away in per_team:
            per_team[away][w] += 1

    # Weeks whose field inventory is too small to seat the whole league are excluded:
    # a team sitting out one of those is a fact of the calendar, not a scheduling flaw.
    low = getattr(sn, 'LOW_CAPACITY_WEEKS', set())
    scored_weeks = [w for w in weeks if w not in low]

    idle = heavy = spread = 0
    for t, byweek in per_team.items():
        counts = [byweek[w] for w in scored_weeks]
        if not any(counts):
            continue
        target = sn.weekly_soft_target(t)
        idle += sum(1 for c in counts if c == 0)
        heavy += sum(1 for c in counts if c > target)
        spread += max(counts) - min(counts)
    return idle, heavy, spread, per_team


def _unused_front_slots(schedule, field_availability):
    """Count open slots left inside the front-load window.

    Early-week capacity that goes unused can never be recovered later, so a schedule
    that leaves week-1 slots empty is worse than one that fills them, even at equal
    game counts.
    """
    if not getattr(sn, 'FRONT_LOAD_WEEKS', 0):
        return 0
    used = {(dt, slot, field) for dt, slot, field, *_ in schedule}
    unused = 0
    for dt, slot, field in field_availability:
        if sn.in_front_load_window(dt.date()) and (dt, slot, field) not in used:
            unused += 1
    return unused


def _score_attempt(all_teams, team_stats, doubleheader_count, unscheduled, violations,
                   schedule=None, field_availability=None):
    """Score a schedule attempt. LOWER is better.

    Priority order:
      1. games short of target   (dominant — this is the thing we most want to fix)
      2. weekly evenness         (idle weeks and 4-game weeks are what managers notice)
      3. doubleheader days short of min_dh
      4. matchups left unscheduled
      5. home/away imbalance
      6. availability violations (should always be 0)
    """
    games_short = sum(max(0, sn.target_games(t) - team_stats[t]['total_games']) for t in all_teams)
    dh_short = sum(max(0, sn.min_dh(t) - doubleheader_count[t]) for t in all_teams)
    imbalance = sum(abs(team_stats[t]['home_games'] - team_stats[t]['away_games']) for t in all_teams)

    idle_weeks, heavy_weeks, spread, _ = _weekly_shape(schedule or [], all_teams)

    # Weekly pacing is weighted by the same dial that drives placement, so that
    # setting the strength to 0 makes best-of-N ignore pacing too and simply pick
    # the schedule with the most games — i.e. a true revert to pre-pacing behaviour.
    strength = getattr(sn, 'WEEKLY_BALANCE_PENALTY', 0) or 0
    reference = getattr(sn, 'WEEKLY_BALANCE_REFERENCE', 2500) or 2500
    # Clamped at 1.0 so configs saved under the old 0-5000 scale don't double-weight pacing.
    pace_w = min(1.0, float(strength) / float(reference))

    unused_front = _unused_front_slots(schedule or [], field_availability or [])

    score = (
        games_short * 10000
        + unused_front * 2000
        + int(idle_weeks * 400 * pace_w)
        + int(heavy_weeks * 400 * pace_w)
        + int(spread * 100 * pace_w)
        + dh_short * 500
        + len(unscheduled) * 100
        + imbalance * 5
        + (len(violations) if violations else 0) * 100000
    )
    return score, {
        'games_short': games_short,
        'dh_short': dh_short,
        'unscheduled': len(unscheduled),
        'imbalance': imbalance,
        'violations': len(violations) if violations else 0,
        'idle_weeks': idle_weeks,
        'heavy_weeks': heavy_weeks,
        'weekly_spread': spread,
        'unused_front_slots': unused_front,
    }


def _run_attempt(config, loaded, seed):
    """Run one full scheduling pass with a given RNG seed.

    Returns a dict holding the resulting state — nothing is written to disk here,
    so callers can run many attempts and only persist the best one.
    """
    sn.RUN_SEED = seed
    random.seed(seed)

    team_availability = loaded['team_availability']
    field_availability = loaded['field_availability']
    team_blackouts = loaded['team_blackouts']
    division_teams = loaded['division_teams']
    all_teams = loaded['all_teams']

    schedule = []
    team_stats = defaultdict(lambda: {
        'total_games': 0,
        'home_games': 0,
        'away_games': 0,
        'weekly_games': defaultdict(int),
    })
    used_slots = {}
    team_game_days = defaultdict(lambda: defaultdict(int))
    team_game_slots = defaultdict(lambda: defaultdict(list))
    team_doubleheader_opponents = defaultdict(lambda: defaultdict(set))
    doubleheader_count = defaultdict(int)

    timeslots_by_date = defaultdict(list)
    for date, slot, field in field_availability:
        d = date.date()
        if slot not in timeslots_by_date[d]:
            timeslots_by_date[d].append(slot)
    for d in timeslots_by_date:
        timeslots_by_date[d].sort(key=lambda s: datetime.strptime(s.strip(), "%I:%M %p"))

    for t in all_teams:
        _ = team_stats[t]

    sunday_assignment = sn.build_sunday_pod_assignment(
        timeslots_by_date,
        rotation=sn.SUNDAY_POD_ROTATION,
        seed=seed,
    )
    sunday_pods_used = {}
    field_index = sn.build_field_index(field_availability)

    matchups = sn.generate_full_matchups(division_teams)
    unscheduled = matchups[:]

    # DH-only divisions first — they need the prime adjacent-slot inventory.
    for div in sn.dh_only_divisions(division_teams):
        (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
         used_slots) = sn.schedule_pod_only_division(
            div, division_teams, team_availability, field_availability, team_blackouts,
            timeslots_by_date, team_stats, doubleheader_count, team_game_days,
            team_game_slots, used_slots, schedule,
            sunday_assignment=sunday_assignment, sunday_pods_used=sunday_pods_used
        )

    unscheduled = [m for m in unscheduled
                   if not sn.is_dh_only(sn.div_of(m[0])) and not sn.is_dh_only(sn.div_of(m[1]))]

    for div in division_teams:
        if sn.is_dh_only(div):
            continue
        (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
         team_doubleheader_opponents, used_slots, unscheduled) = sn.schedule_division_pod_doubleheaders(
            div, division_teams, unscheduled,
            team_availability, field_availability, team_blackouts, timeslots_by_date,
            team_stats, doubleheader_count, team_game_days, team_game_slots,
            team_doubleheader_opponents, used_slots, schedule,
            sunday_assignment=sunday_assignment, sunday_pods_used=sunday_pods_used
        )

    (schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
     team_doubleheader_opponents, used_slots, unscheduled) = sn.schedule_games(
        unscheduled, team_availability, field_availability, team_blackouts,
        schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
        team_doubleheader_opponents, used_slots, timeslots_by_date,
        sunday_assignment=sunday_assignment
    )

    if any(team_stats[t]['total_games'] < sn.target_games(t) for t in all_teams):
        (schedule, team_stats, doubleheader_count, unscheduled) = sn.fill_missing_games(
            schedule, team_stats, doubleheader_count, team_game_days, team_game_slots,
            team_doubleheader_opponents, used_slots, timeslots_by_date, unscheduled,
            team_availability, team_blackouts, field_availability,
            sunday_assignment=sunday_assignment
        )

    schedule, repair_moves, diag_before, diag_after = sn.repair_schedule(
        schedule, all_teams, team_stats, doubleheader_count,
        team_game_days, team_game_slots, team_doubleheader_opponents,
        used_slots, timeslots_by_date, field_availability, field_index,
        team_availability, team_blackouts, sunday_assignment=sunday_assignment,
        max_moves=50
    )

    violations = sn.check_schedule_against_availability(schedule, team_availability)
    score, breakdown = _score_attempt(all_teams, team_stats, doubleheader_count, unscheduled,
                                      violations, schedule=schedule,
                                      field_availability=field_availability)

    return {
        'seed': seed,
        'score': score,
        'breakdown': breakdown,
        'schedule': schedule,
        'team_stats': team_stats,
        'doubleheader_count': doubleheader_count,
        'unscheduled': unscheduled,
        'violations': violations,
        'repair_moves': repair_moves,
        'diag_after': diag_after,
    }


def run_scheduler(config, csv_paths, output_dir, config_name=None, progress=None):
    """Run the scheduler, optionally over several attempts, keeping the best result.

    config_name is used to suffix the output filenames so downloads from
    different season configs don't overwrite each other.
    progress is an optional callable(attempt, total, best_score) for status reporting.
    """
    _patch_globals(config)
    os.makedirs(output_dir, exist_ok=True)

    log_buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = log_buf

    result = {
        'success': False,
        'log': '',
        'output_files': {},
        'stats': {},
        'schedule_preview': [],
        'warnings': [],
    }

    try:
        gen = config.get('general', {})
        attempts = max(1, int(gen.get('attempts', 1) or 1))
        base_seed = gen.get('random_seed', None)

        warnings = validate_config(config)
        result['warnings'] = warnings
        if warnings:
            print("Configuration warnings:")
            for w in warnings:
                print(f"  ! {w}")
            print("")

        team_availability = sn.load_team_availability(csv_paths['team_availability'])
        print(f"Loaded team availability ({len(team_availability)} teams)")
        field_availability = sn.load_field_availability(csv_paths['field_availability'])
        print(f"Loaded field availability ({len(field_availability)} slots)")
        team_blackouts = sn.load_team_blackouts(csv_paths['team_blackouts'])
        print(f"Loaded team blackouts ({len(team_blackouts)} teams with blackouts)")

        division_teams = _build_division_teams(config)
        all_teams = [t for div in division_teams for t in division_teams[div]]
        print(f"Divisions: { {d: len(ts) for d, ts in division_teams.items()} }")

        dh_only = sn.dh_only_divisions(division_teams)
        print(f"Doubleheader-only divisions: {dh_only if dh_only else 'none'}")

        sunday_info = analyse_sundays(field_availability, sn.SUNDAY_PODS_PER_SUNDAY)
        if sunday_info:
            print(f"\nSundays ({len(sunday_info)}), pods-only={sn.SUNDAY_PODS_ONLY}, "
                  f"priority={sn.SUNDAY_PRIORITY}:")
            for s in sunday_info:
                note = "exact fit" if s['leftover_slots'] == 0 else f"{s['leftover_slots']} slot(s) left over"
                print(f"  {s['date']}: {s['slots']} slots, {s['pods_possible']} pods possible, "
                      f"{s['pods_allowed']} allowed -> {note}")

        sn.init_team_scarcity(all_teams, field_availability, team_availability, team_blackouts)
        scarce = sorted(all_teams, key=lambda t: sn.TEAM_PLAYABLE_DATES.get(t, 0))
        print("Most constrained teams (playable dates / {} total):".format(sn.SEASON_DATE_COUNT))
        for t in scarce[:5]:
            print(f"  {t}: {sn.TEAM_PLAYABLE_DATES.get(t, 0)} playable dates")

        loaded = {
            'team_availability': team_availability,
            'field_availability': field_availability,
            'team_blackouts': team_blackouts,
            'division_teams': division_teams,
            'all_teams': all_teams,
        }

        print(f"\nRunning {attempts} attempt(s)...")
        best = None
        attempt_log = []
        for i in range(attempts):
            if base_seed is None:
                seed = int.from_bytes(os.urandom(4), "big")
            else:
                # Derive distinct but reproducible seeds from the base seed
                seed = int(base_seed) + i

            attempt = _run_attempt(config, loaded, seed)
            b = attempt['breakdown']
            marker = ''
            if best is None or attempt['score'] < best['score']:
                best = attempt
                marker = '  <-- new best'
            attempt_log.append({
                'attempt': i + 1,
                'seed': seed,
                'score': attempt['score'],
                'games_short': b['games_short'],
                'dh_short': b['dh_short'],
                'unscheduled': b['unscheduled'],
            })
            print(f"  Attempt {i + 1}/{attempts} (seed {seed}): "
                  f"{b['games_short']} games short, {b['dh_short']} DH short, "
                  f"{b['idle_weeks']} idle wks, {b['heavy_weeks']} heavy wks, "
                  f"score {attempt['score']}{marker}")

            if progress:
                try:
                    progress(i + 1, attempts, best['score'])
                except Exception:
                    pass

            # A perfect schedule cannot be improved on — stop early.
            if attempt['score'] == 0:
                print("  Perfect schedule found — stopping early.")
                break

        print(f"\nBest attempt: seed {best['seed']} (score {best['score']})")

        # Unpack the winning attempt
        schedule = best['schedule']
        team_stats = best['team_stats']
        doubleheader_count = best['doubleheader_count']
        unscheduled = best['unscheduled']
        violations = best['violations']
        repair_moves = best['repair_moves']
        diag_after = best['diag_after']

        if violations:
            print(f"WARNING: {len(violations)} availability violations detected")
            for v in violations[:20]:
                print(f"  {v}")

        suffix = ''
        if config_name:
            safe = re.sub(r'[^A-Za-z0-9_-]', '', str(config_name))
            if safe:
                suffix = '_' + safe

        names = {
            'csv': f'softball_schedule{suffix}.csv',
            'xlsx': f'softball_schedule{suffix}.xlsx',
            'unscheduled': f'unscheduled_matchups{suffix}.csv',
            'remaining': f'team_remaining_needs{suffix}.csv',
        }

        sn.output_schedule_to_csv_full(field_availability, schedule, os.path.join(output_dir, names['csv']))
        sn.output_unscheduled_matchups_csv(unscheduled, os.path.join(output_dir, names['unscheduled']))
        sn.output_team_remaining_needs_csv(all_teams, team_stats, doubleheader_count,
                                           os.path.join(output_dir, names['remaining']))
        sn.export_schedule_to_xlsx(
            field_availability, schedule, division_teams, os.path.join(output_dir, names['xlsx']),
            remaining_matchups=unscheduled, team_stats=team_stats,
            doubleheader_count=doubleheader_count, team_availability=team_availability,
            team_blackouts=team_blackouts, diagnostics=diag_after
        )

        print("\nSchedule Generation Complete")
        sn.print_schedule_summary(team_stats)
        sn.print_doubleheader_summary(doubleheader_count)

        per_team = {}
        for t in all_teams:
            per_team[t] = {
                'total': team_stats[t]['total_games'],
                'home': team_stats[t]['home_games'],
                'away': team_stats[t]['away_games'],
                'dh_days': doubleheader_count[t],
                'target': sn.target_games(t),
                'playable_dates': sn.TEAM_PLAYABLE_DATES.get(t, None),
            }

        # Matchup matrix: symmetric count of games played between each pair of teams.
        teams_ordered = sorted(all_teams, key=lambda t: (sn.div_of(t), t))
        grid = {a: {b: 0 for b in teams_ordered} for a in teams_ordered}
        for g in schedule:
            _dt, _slot, _field, home, _hd, away, _ad = g
            if home in grid and away in grid[home]:
                grid[home][away] += 1
                grid[away][home] += 1
        matchup_matrix = {
            'teams': teams_ordered,
            'divisions': {t: sn.div_of(t) for t in teams_ordered},
            'grid': grid,
        }

        preview = []
        for g in sorted(schedule, key=lambda x: (x[0], x[1], x[2])):
            dt, slot, field, home, home_div, away, away_div = g
            preview.append({
                'date': dt.strftime('%Y-%m-%d'),
                'day': dt.strftime('%a'),
                'time': slot,
                'field': field,
                'home': home,
                'away': away,
                'home_div': home_div,
                'away_div': away_div,
            })

        result['success'] = True
        result['output_files'] = names
        result['stats'] = {
            'per_team': per_team,
            'total_games': len(schedule),
            'unscheduled_count': len(unscheduled),
            'violations': len(violations) if violations else 0,
            'repair_moves': len(repair_moves),
            'games_short': best['breakdown']['games_short'],
            'dh_short': best['breakdown']['dh_short'],
            'idle_weeks': best['breakdown']['idle_weeks'],
            'heavy_weeks': best['breakdown']['heavy_weeks'],
            'weekly_spread': best['breakdown']['weekly_spread'],
            'unused_front_slots': best['breakdown'].get('unused_front_slots', 0),
            'best_seed': best['seed'],
            'best_score': best['score'],
            'attempts_run': len(attempt_log),
            'attempt_log': attempt_log,
        }
        # Per-week game counts, so the season's rhythm is visible at a glance.
        wk_numbers = sorted({dt.isocalendar()[1] for dt, *_ in schedule}) if schedule else []
        first_date = {}
        for dt, *_ in schedule:
            w = dt.isocalendar()[1]
            d = dt.date()
            if w not in first_date or d < first_date[w]:
                first_date[w] = d
        _idle, _heavy, _spread, per_week = _weekly_shape(schedule, all_teams)
        slots_per_week = defaultdict(int)
        for (fdt, _s, _f) in field_availability:
            slots_per_week[fdt.date().isocalendar()[1]] += 1
        weekly_table = {
            'weeks': [
                {
                    'index': i + 1,
                    'iso': w,
                    'starts': first_date.get(w).strftime('%b %d') if first_date.get(w) else '',
                    'low_capacity': w in getattr(sn, 'LOW_CAPACITY_WEEKS', set()),
                    'slots': slots_per_week.get(w, 0),
                    # how many teams that week's inventory can seat for a full
                    # (soft-target-sized) week
                    'seats': slots_per_week.get(w, 0) * 2,
                }
                for i, w in enumerate(wk_numbers)
            ],
            'teams': teams_ordered,
            'counts': {t: [per_week.get(t, {}).get(w, 0) for w in wk_numbers] for t in teams_ordered},
            'soft_target': {t: sn.weekly_soft_target(t) for t in teams_ordered},
        }

        result['schedule_preview'] = preview
        result['matchup_matrix'] = matchup_matrix
        result['weekly_table'] = weekly_table

    except Exception as e:
        tb = traceback.format_exc()
        print(f"\nERROR: {e}")
        print(tb)
        result['success'] = False
        result['error'] = str(e)

    finally:
        sys.stdout = old_stdout
        result['log'] = log_buf.getvalue()

    return result
