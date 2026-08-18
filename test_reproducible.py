"""Seed replay has to actually reproduce a schedule.

Run with:  python test_reproducible.py

The app records the seed of a run's best attempt and offers to replay it. That is
only worth offering if it is true, and it was not: the engine builds its placement
RNGs from RANDOM_SEED, which was set once per run rather than per attempt, so an
attempt in a sequence and the same seed run alone were different schedules.
"""
import copy
import os
import sys
import tempfile

RESULTS = []
PATHS = {
    'team_availability': 'static/samples/team_availability.csv',
    'field_availability': 'static/samples/field_availability.csv',
    'team_blackouts': 'static/samples/team_blackouts.csv',
}


def check(name, condition):
    RESULTS.append(bool(condition))
    print(("  PASS  " if condition else "  FAIL  ") + name)


def key(stats):
    return (stats['total_games'], stats['games_short'], stats['worst_idle_gap'],
            stats['idle_weeks'], stats['heavy_weeks'], stats['best_score'])


def run(attempts, seed=None):
    from scheduler_wrapper import DEFAULT_CONFIG, run_scheduler
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg['general']['attempts'] = attempts
    cfg['general']['random_seed'] = seed
    return run_scheduler(cfg, PATHS, tempfile.mkdtemp(), 't')['stats']


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    a, b = run(1, 4242), run(1, 4242)
    check("the same seed gives the same schedule", key(a) == key(b))

    # The property that was broken: an attempt inside a run must match that same
    # seed run on its own, or a recorded seed cannot be replayed.
    multi = run(3, 900100)
    for entry in multi['attempt_log']:
        alone = run(1, entry['seed'])
        check("attempt with seed %s matches it run alone" % entry['seed'],
              alone['best_score'] == entry['score'])

    # And the workflow the history panel offers, end to end.
    orig = run(5)                       # no seed, as a normal run
    replay = run(1, orig['best_seed'])
    check("replaying a best-of-5's seed reproduces it", key(orig) == key(replay))

    print("\n  %d/%d passed" % (sum(RESULTS), len(RESULTS)))
    return 0 if all(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
