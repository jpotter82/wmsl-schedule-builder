"""A chunked run must produce the same schedule a single run would.

Run with:  python test_chunked_run.py

Long runs are scored a slice per request so no single request is long enough for
shared hosting to kill it. That is only sound because attempts are independent:
a seed scored in one slice has to produce the same schedule when re-run alone at
the end, which is the property test_reproducible.py pins down.
"""
import copy
import io as _io
import os
import shutil
import sys
import tempfile

RESULTS = []
PW = 'a-long-enough-password'


def check(name, condition):
    RESULTS.append(bool(condition))
    print(("  PASS  " if condition else "  FAIL  ") + name)


def main():
    tmp = tempfile.mkdtemp(prefix='skedworx-chunk-')
    os.environ['SKEDWORX_DATA_DIR'] = tmp
    os.environ['SKEDWORX_INSECURE_COOKIES'] = '1'
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import app as appmod
    import auth
    from scheduler_wrapper import DEFAULT_CONFIG, run_scheduler

    appmod.app.config.update(TESTING=True)
    auth.create_user('r@example.com', PW)
    c = appmod.app.test_client()
    c.post('/login', data={'email': 'r@example.com', 'password': PW})

    files = {k: (_io.BytesIO(open('static/samples/%s.csv' % k, 'rb').read()), '%s.csv' % k)
             for k in ('team_availability', 'field_availability', 'team_blackouts')}
    c.post('/api/upload', data=files, content_type='multipart/form-data')

    SEED, N = 505050, 40
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg['general']['attempts'] = N
    cfg['general']['random_seed'] = SEED

    started = c.post('/api/run', json={'config': cfg, 'config_name': 'chunked'}).get_json()
    check("starting a run reports the total", started.get('total') == N)
    check("...and does no work itself", started.get('chunk') == appmod.CHUNK_ATTEMPTS)

    slices, last = 0, None
    while True:
        last = c.post('/api/run/chunk').get_json()
        slices += 1
        if last.get('finished') or last.get('error') or slices > 50:
            break

    check("the run finished", bool(last.get('finished')))
    check("it took more than one slice", slices > 1)
    check("every attempt was scored", last.get('done') == N)

    chunked = c.get('/api/results').get_json()['stats']

    # The same seeds run in one go, for comparison.
    whole = run_scheduler(cfg, {
        'team_availability': 'static/samples/team_availability.csv',
        'field_availability': 'static/samples/field_availability.csv',
        'team_blackouts': 'static/samples/team_blackouts.csv',
    }, tempfile.mkdtemp(), 'whole')['stats']

    check("chunked picks the same winning seed", chunked['best_seed'] == whole['best_seed'])
    check("...with the same score", chunked['best_score'] == whole['best_score'])
    for field in ('total_games', 'games_short', 'worst_idle_gap', 'idle_weeks', 'heavy_weeks'):
        check("...and the same %s" % field, chunked[field] == whole[field])

    out = os.path.join(tmp, 'users', '1', 'output')
    written = sorted(os.listdir(out))
    check("the workbook was written once, at the end",
          any(f.endswith('.xlsx') for f in written))
    check("history recorded the run",
          len(c.get('/api/history').get_json()['runs']) == 1)

    # Chunking against a run that is already over should not half-start another.
    stray = c.post('/api/run/chunk')
    check("a stray slice after the run is refused", stray.status_code == 409)

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n  %d/%d passed" % (sum(RESULTS), len(RESULTS)))
    return 0 if all(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main())
