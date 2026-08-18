import json
import os
import re
import shutil
from datetime import datetime
from functools import wraps
from pathlib import Path

from flask import (Flask, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import (LoginManager, current_user, login_required, login_user,
                         logout_user)

import auth
import mailer
import plans
from scheduler_wrapper import DEFAULT_CONFIG, run_scheduler, validate_config

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent

# Legacy single-user directories. Still read once, to migrate their contents into
# the first account created; see _migrate_legacy_data.
LEGACY_CONFIGS_DIR = BASE_DIR / 'configs'
LEGACY_UPLOADS_DIR = BASE_DIR / 'uploads'

app.config['SECRET_KEY'] = auth.get_secret_key()
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# Only send the session cookie over HTTPS unless explicitly running plain HTTP
# locally. Left on by default so a production deployment is not silently insecure.
app.config['SESSION_COOKIE_SECURE'] = auth.env(
    'INSECURE_COOKIES').strip().lower() not in ('1', 'true', 'yes', 'on')

auth.init_db()

login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.session_protection = 'strong'


@login_manager.user_loader
def load_user(session_id):
    # Not get_user_by_id: the session value is "<id>.<password stamp>", so a password
    # change invalidates cookies issued before it.
    return auth.get_user_by_session_id(session_id)


@login_manager.unauthorized_handler
def unauthorized():
    # API callers get JSON so the frontend can react; browsers get the login page.
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not signed in', 'login_required': True}), 401
    return redirect(url_for('login', next=request.path))


# SYNC_RUNS is no longer read. Runs are scored a slice per request now, so there
# is no background thread to keep alive and no long request to protect: every
# request is short whatever the host does. Deployments still setting it are
# harmless, and dispatch.cgi.example no longer bothers.

# Copy leftover pre-accounts configs/ and uploads/ into the first account created.
# Off by default: see _migrate_legacy_data for why this must not be automatic on a
# deployment anyone can sign up to.
MIGRATE_LEGACY = auth.env('MIGRATE_LEGACY').strip().lower() in ('1', 'true', 'yes', 'on')

# No in-process lock any more. A run spans several requests, so a lock held in one
# process could not guard it; the job record in run_state.json is the shared truth
# instead, and that works whether requests land in one process or twenty.


def _blank_state():
    return {'status': 'idle', 'log': '', 'result': None, 'progress': None}


# ------------------------------------------------------------------ per-user paths
#
# Every path below is derived from the SIGNED-IN USER, never from anything in the
# request. That makes cross-account access impossible by construction rather than
# by remembering to check an owner field on each route.
def _user_dir(name):
    return current_user.dir(name)


def _save_state(state, path):
    """Persist a user's run state to `path`.

    Takes the path explicitly rather than reading current_user, because this is
    also called from the background thread used in non-synchronous mode. Flask's
    current_user is a request-context proxy and is None inside that thread, so
    resolving it here would raise and leave the run stuck reporting "running".

    Run state cannot be assumed to survive in memory either: /api/run, /api/status
    and /api/results are separate requests, and under CGI each is a new process,
    while under Passenger or multi-worker gunicorn they may hit different ones.
    Written to a temp file and renamed so a reader never sees a partial write.
    """
    try:
        tmp = path.with_suffix('.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        pass


# How many runs to keep per account. Enough to compare a session's worth of
# tuning; small enough that the file stays trivial to read and rewrite whole.
HISTORY_LIMIT = 25


def _history_path(home):
    return home / 'run_history.json'


def _read_history(home):
    try:
        with open(_history_path(home), encoding='utf-8') as fh:
            entries = json.load(fh)
        return entries if isinstance(entries, list) else []
    except (OSError, ValueError):
        return []


def _append_history(home, entry):
    """Add a finished run to this account's history, newest first.

    Written whole via temp-and-rename, like run state: under CGI two requests can
    overlap, and a half-written history file would be lost entirely rather than
    just stale.
    """
    entries = [entry] + _read_history(home)
    entries = entries[:HISTORY_LIMIT]
    try:
        path = _history_path(home)
        tmp = path.with_suffix('.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(entries, fh)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        pass


def _history_entry(result, config_name, attempts):
    """The six summary figures, plus what is needed to reproduce the run."""
    stats = (result or {}).get('stats') or {}
    wt = (result or {}).get('weekly_table') or {}
    teams = len(wt.get('teams') or [])
    weeks = len(wt.get('weeks') or [])
    team_weeks = teams * weeks
    idle = stats.get('idle_weeks') or 0
    heavy = stats.get('heavy_weeks') or 0
    return {
        'at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'config_name': config_name or '',
        'seed': stats.get('best_seed'),
        'attempts': attempts,
        'total_games': stats.get('total_games'),
        'on_target': (team_weeks - idle - heavy) if team_weeks else None,
        'team_weeks': team_weeks or None,
        'worst_idle_gap': stats.get('worst_idle_gap'),
        'idle_violations': stats.get('idle_violations'),
        'max_idle_days': stats.get('max_idle_days'),
        'games_short': stats.get('games_short'),
        'idle_weeks': idle,
        'heavy_weeks': heavy,
    }


def _read_state():
    try:
        with open(current_user.state_file, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return _blank_state()


def _safe_name(name):
    return re.sub(r'[^a-zA-Z0-9_-]', '', name or '')


def _migrate_legacy_data(user):
    """Copy the pre-accounts configs/ and uploads/ into the first account.

    OFF unless SKEDWORX_MIGRATE_LEGACY is set. This began as a one-time upgrade
    path so the original single-user install did not appear to lose its saved
    seasons — which was safe when the only person who could register was the owner.

    With open signup it is not: on any fresh deployment the first stranger to
    create an account would inherit whatever configs and uploads were sitting in
    the application directory, which is somebody else's league data. Opt in on the
    one install that needs it, and only for as long as it takes to run.
    """
    moved = []
    for legacy, name in ((LEGACY_CONFIGS_DIR, 'configs'), (LEGACY_UPLOADS_DIR, 'uploads')):
        if not legacy.is_dir():
            continue
        target = user.dir(name)
        for src in legacy.iterdir():
            if not src.is_file():
                continue
            dest = target / src.name
            if dest.exists():
                continue
            try:
                shutil.copy2(src, dest)
                moved.append(f'{name}/{src.name}')
            except OSError:
                pass
    return moved


# ------------------------------------------------------------------ auth routes
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'GET':
        return render_template('register.html',
                               invite_required=bool(auth.INVITE_CODE),
                               min_password=auth.MIN_PASSWORD_LENGTH)

    email = request.form.get('email', '')
    password = request.form.get('password', '')
    invite = request.form.get('invite', '')
    ip = (request.headers.get('X-Forwarded-For', request.remote_addr or '')
          .split(',')[0].strip())

    error = auth.validate_registration(email, password, invite)
    if not error and not auth.signup_allowed(ip):
        error = "Too many accounts created from this address today. Try again tomorrow."

    if error:
        return render_template('register.html', error=error, email=email,
                               invite_required=bool(auth.INVITE_CODE),
                               min_password=auth.MIN_PASSWORD_LENGTH), 400

    first_account = auth.user_count() == 0
    user = auth.create_user(email, password)
    auth.record_signup(ip)

    if first_account and MIGRATE_LEGACY:
        _migrate_legacy_data(user)

    login_user(user)
    return redirect(url_for('index'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))

    if request.method == 'GET':
        return render_template('login.html', next=request.args.get('next', ''))

    user = auth.verify_password(request.form.get('email', ''),
                                request.form.get('password', ''))
    if not user:
        # One message for both cases, so this cannot be used to discover which
        # email addresses have accounts.
        return render_template('login.html', error="Email or password is incorrect.",
                               email=request.form.get('email', '')), 401

    login_user(user, remember=bool(request.form.get('remember')))
    nxt = request.form.get('next') or ''
    # Only follow same-site paths, never an absolute URL supplied by the caller.
    return redirect(nxt if nxt.startswith('/') and not nxt.startswith('//')
                    else url_for('index'))


def _reset_url(token):
    """Absolute URL for a reset link.

    Prefers SKEDWORX_BASE_URL. Building this from the request would take the host from
    the Host header, which the caller controls: someone could request a reset for
    your address and have the mail arrive pointing at their own domain, harvesting
    the token when you clicked. Falling back to the request host is still the default
    because it is what works out of the box, but setting SKEDWORX_BASE_URL in
    production closes that off.
    """
    base = auth.env('BASE_URL').strip().rstrip('/')
    path = url_for('reset_password', token=token)
    return f"{base}{path}" if base else request.url_root.rstrip('/') + path


@app.route('/forgot', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'GET':
        return render_template('forgot.html')

    email = request.form.get('email', '')
    ip = request.remote_addr or ''
    # The throttle is checked before the lookup so a blocked caller cannot use
    # timing differences to tell registered addresses from unregistered ones.
    if auth.reset_request_allowed(ip):
        user = auth.get_user_by_email(email)
        if user:
            token = auth.create_reset_token(user, ip)
            mailer.send_password_reset(user.email, _reset_url(token),
                                       auth.RESET_TOKEN_TTL_SECONDS // 60)

    # Always the same response. Saying "no such account" here would turn this form
    # into a way to find out who has one.
    return render_template('forgot.html', sent=True, email=email)


@app.route('/reset/<token>', methods=['GET', 'POST'])
def reset_password(token):
    user = auth.user_for_reset_token(token)
    if user is None:
        return render_template('reset.html', invalid=True), 400
    if request.method == 'GET':
        return render_template('reset.html', token=token)

    password = request.form.get('password', '')
    confirm = request.form.get('confirm', '')
    error = auth.validate_password(password)
    if not error and password != confirm:
        error = "Those passwords do not match."
    if error:
        return render_template('reset.html', token=token, error=error), 400

    if not auth.consume_reset_token(token, password):
        # Only reachable if the link was used elsewhere between the two checks.
        return render_template('reset.html', invalid=True), 400

    # Deliberately not logged in here: changing the password invalidates every
    # session, and signing in proves the new password actually works.
    return redirect(url_for('login', reset='1'))


# ------------------------------------------------------------------ admin
def admin_required(view):
    """Admin-only route guard.

    Separate from @login_required rather than checked inside each view, so a new
    admin route cannot be added without deciding who may see it.
    """
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            # 404 rather than 403: there is no reason to confirm the page exists
            # to someone who cannot use it.
            return render_template('404.html'), 404
        return view(*args, **kwargs)
    return wrapped


def _account_usage(user_id):
    """Rough per-account footprint, for the user list."""
    home = auth.USERS_DIR / str(user_id)
    out = {'configs': 0, 'bytes': 0, 'last_run': None}
    if not home.is_dir():
        return out
    cfg = home / 'configs'
    if cfg.is_dir():
        out['configs'] = sum(1 for f in cfg.glob('*.json'))
    for f in home.rglob('*'):
        if f.is_file():
            try:
                out['bytes'] += f.stat().st_size
            except OSError:
                pass
    state = home / 'run_state.json'
    if state.is_file():
        try:
            out['last_run'] = datetime.utcfromtimestamp(state.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        except (OSError, ValueError):
            pass
    return out


@app.route('/admin')
@admin_required
def admin_home():
    users = auth.list_users()
    for u in users:
        u['usage'] = _account_usage(u['id'])
    return render_template('admin.html', users=users,
                           admin_count=auth.admin_count(),
                           plan_labels=plans.PLAN_LABELS,
                           valid_plans=plans.VALID_PLANS,
                           plan_changes=auth.plan_changes(15),
                           notice=request.args.get('notice'),
                           problem=request.args.get('problem'))


@app.route('/admin/users/<int:user_id>/admin', methods=['POST'])
@admin_required
def admin_set_admin(user_id):
    make = request.form.get('make') == '1'
    ok, message = auth.set_admin(user_id, make)
    return redirect(url_for('admin_home', **({'notice': message} if ok else {'problem': message})))


@app.route('/admin/users/<int:user_id>/plan', methods=['POST'])
@admin_required
def admin_set_plan(user_id):
    """Move a user between plans. Admin-only, and the only way a plan changes.

    There is deliberately no self-service route: a user cannot put themselves on
    Pro, by this or any other request. When billing arrives the subscription will
    move people instead, and this stays as the manual override.
    """
    ok, message = auth.set_plan(user_id, request.form.get('plan', ''),
                                changed_by=current_user)
    return redirect(url_for('admin_home', **({'notice': message} if ok else {'problem': message})))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


@app.route('/pricing')
def pricing():
    """Informational only. No payment flow exists yet, and the page says so."""
    # Each card carries its plan key. Deciding anything from the price string
    # would break the moment the price changes, which is the one thing a pricing
    # page can expect to do.
    cards = [dict(plans.PLAN_LABELS[key], key=key, paid=(key != plans.FREE))
             for key in plans.VALID_PLANS]
    return render_template('pricing.html', plans=cards,
                           free_plan=plans.FREE,
                           current=(plans.plan_of(current_user)
                                    if current_user.is_authenticated else None))


@app.route('/api/plan')
@login_required
def my_plan():
    return jsonify(plans.describe(current_user))


@app.route('/api/me')
@login_required
def whoami():
    return jsonify({'email': current_user.email,
                    'display_name': current_user.display_name,
                    'is_admin': current_user.is_admin})


# ------------------------------------------------------------------ app routes
@app.route('/')
def home():
    """Public landing page. The scheduler itself lives at /app behind sign-in."""
    return render_template('home.html',
                           signed_in=current_user.is_authenticated)


@app.route('/app')
@login_required
def index():
    # is_admin gates the raw generator log. It is not a security boundary -- the
    # same text is in /api/status for anyone who looks -- it is about not putting
    # scheduler internals in front of a league volunteer as if they were an error.
    plan = plans.plan_of(current_user)
    return render_template('index.html', user_email=current_user.email,
                           is_admin=current_user.is_admin,
                           plan=plan,
                           plan_name=plans.PLAN_LABELS[plan]['name'],
                           history_limit=HISTORY_LIMIT)


@app.route('/api/config/defaults')
@login_required
def config_defaults():
    return jsonify(DEFAULT_CONFIG)


@app.route('/api/configs')
@login_required
def list_configs():
    return jsonify(sorted(f.stem for f in _user_dir('configs').glob('*.json')))


@app.route('/api/configs/<name>', methods=['GET'])
@login_required
def load_config(name):
    path = _user_dir('configs') / f'{_safe_name(name)}.json'
    if not path.exists():
        return jsonify({'error': 'Config not found'}), 404
    with open(path) as f:
        return jsonify(json.load(f))


@app.route('/api/configs/<name>', methods=['POST'])
@login_required
def save_config(name):
    safe = _safe_name(name)
    if not safe:
        return jsonify({'error': 'Invalid config name'}), 400

    # A saved config IS a saved season here -- one concept, one limit. Overwriting
    # an existing one is always allowed, so a Free user can keep working on the
    # season they have rather than being locked out of their own file.
    configs_dir = _user_dir('configs')
    existing = {f.stem for f in configs_dir.glob('*.json')}
    if safe not in existing:
        allowed, ceiling = plans.within_limit(current_user, 'saved_seasons', len(existing))
        if not allowed:
            payload = plans.upgrade_message(
                'Free includes %d saved season%s. Upgrade to Pro to keep more than one, '
                'or overwrite the season you have.' % (ceiling, '' if ceiling == 1 else 's'))
            return jsonify(payload), 402
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No JSON body'}), 400
    with open(_user_dir('configs') / f'{safe}.json', 'w') as f:
        json.dump(data, f, indent=2)
    return jsonify({'saved': safe})


@app.route('/api/configs/<name>', methods=['DELETE'])
@login_required
def delete_config(name):
    path = _user_dir('configs') / f'{_safe_name(name)}.json'
    if not path.exists():
        return jsonify({'error': 'Config not found'}), 404
    path.unlink()
    return jsonify({'deleted': _safe_name(name)})


@app.route('/api/upload', methods=['POST'])
@login_required
def upload_csvs():
    required = ['team_availability', 'field_availability', 'team_blackouts']
    uploads = _user_dir('uploads')

    for key in required:
        f = request.files.get(key)
        if not f or not f.filename:
            return jsonify({'error': f'Missing file: {key}'}), 400
        f.save(str(uploads / f'{key}.csv'))

    # Optional: display names. Absent means teams show as their IDs, as before.
    optional = []
    teams_file = request.files.get('teams')
    if teams_file and teams_file.filename:
        teams_file.save(str(uploads / 'teams.csv'))
        optional.append('teams')

    summary = {}
    for key in required + optional:
        with open(uploads / f'{key}.csv', encoding='utf-8-sig') as fh:
            lines = fh.readlines()
        summary[key] = {'rows': max(0, len(lines) - 1),
                        'filename': request.files[key].filename}

    return jsonify({'uploaded': summary})


# How many attempts one request scores. Sized against measurement: an attempt is
# roughly 0.14s here and slower on shared hosting, so 25 keeps a request near a
# few seconds -- far inside any CGI timeout -- while the ~0.6s of interpreter
# startup each request pays stays a small share of the work done.
CHUNK_ATTEMPTS = 25


def _run_paths_or_error(uploads):
    """The CSV paths for a run, or (None, message) when something is missing."""
    paths = {k: str(uploads / f'{k}.csv')
             for k in ('team_availability', 'field_availability', 'team_blackouts')}
    for key, path in paths.items():
        if not os.path.exists(path):
            return None, f'CSV not uploaded yet: {key}'
    teams_csv = uploads / 'teams.csv'
    if teams_csv.is_file():
        paths['teams'] = str(teams_csv)
    return paths, None


@app.route('/api/run', methods=['POST'])
@login_required
def start_run():
    """Begin a run. Scores no attempts itself -- the client drives slices.

    Splitting the work across requests is what makes long runs survive shared
    hosting: a 500-attempt run is ~70 seconds, and one request that long is liable
    to be killed. Each slice is a few seconds instead, and doubles as the progress
    report, so no separate polling is needed while a run is in flight.
    """
    output_dir = _user_dir('output')
    uploads = _user_dir('uploads')

    csv_paths, problem = _run_paths_or_error(uploads)
    if problem:
        return jsonify({'error': problem}), 400

    # Clear previous output. A file that cannot be removed is skipped rather than
    # failing the run: on Windows a just-downloaded file may still be held open by
    # the server, and every output file is rewritten by name anyway.
    for f in output_dir.iterdir():
        if f.is_file():
            try:
                f.unlink()
            except OSError:
                pass

    payload = request.get_json() or {}
    config = payload.get('config', payload) or DEFAULT_CONFIG
    config_name = payload.get('config_name')
    attempts = int((config.get('general') or {}).get('attempts') or 1)

    # Checked before any work starts, so a refusal costs nothing and arrives
    # before the user watches a progress bar. Dormant until a ceiling is agreed.
    allowed, upgrade = plans.check_team_limit(current_user, config)
    if not allowed:
        return jsonify(upgrade), 402

    # Fix the base seed now so the whole run is one reproducible sequence, even
    # though it is scored across several requests.
    base_seed = (config.get('general') or {}).get('random_seed')
    if base_seed is None:
        base_seed = int.from_bytes(os.urandom(4), 'big')

    state = _blank_state()
    state['status'] = 'running'
    state['job'] = {
        'config': config,
        'config_name': config_name,
        'base_seed': int(base_seed),
        'total': attempts,
        'done': 0,
        'best_seed': None,
        'best_score': None,
    }
    state['progress'] = {'done': 0, 'total': attempts, 'best_score': None}
    _save_state(state, current_user.state_file)

    return jsonify({'started': True, 'total': attempts, 'chunk': CHUNK_ATTEMPTS})


@app.route('/api/run/chunk', methods=['POST'])
@login_required
def run_chunk():
    """Score the next slice of attempts, and finish the run when they run out."""
    state = _read_state()
    job = state.get('job')
    if not job or state.get('status') != 'running':
        return jsonify({'error': 'No run in progress'}), 409

    uploads = _user_dir('uploads')
    output_dir = _user_dir('output')
    csv_paths, problem = _run_paths_or_error(uploads)
    if problem:
        state['status'] = 'error'
        state['log'] = problem
        _save_state(state, current_user.state_file)
        return jsonify({'error': problem}), 400

    config = job['config']
    done, total = int(job['done']), int(job['total'])
    seeds = [job['base_seed'] + i
             for i in range(done, min(done + CHUNK_ATTEMPTS, total))]

    try:
        # score_only: no files, because only the winning seed is written at the end.
        sliced = run_scheduler(config, csv_paths, str(output_dir),
                               config_name=job.get('config_name'),
                               seeds=seeds, score_only=True)
        st = sliced.get('stats') or {}
        if not sliced.get('success'):
            raise RuntimeError(sliced.get('error') or 'Attempt failed')

        if job['best_score'] is None or st.get('best_score', 0) < job['best_score']:
            job['best_score'] = st.get('best_score')
            job['best_seed'] = st.get('best_seed')

        job['done'] = done + len(seeds)
        state['job'] = job
        state['progress'] = {'done': job['done'], 'total': total,
                             'best_score': job['best_score']}
        state['log'] = (state.get('log') or '') + (sliced.get('log') or '')

        finished = job['done'] >= total
        if finished:
            # Re-run the winner on its own to produce the real outputs. Sound only
            # because attempts are independent and a seed reproduces its schedule.
            final = run_scheduler(config, csv_paths, str(output_dir),
                                  config_name=job.get('config_name'),
                                  seeds=[job['best_seed']])
            state['result'] = final
            state['log'] = (state.get('log') or '') + (final.get('log') or '')
            state['status'] = 'done' if final.get('success') else 'error'
            state['job'] = None
            if final.get('success'):
                _append_history(current_user.home,
                                _history_entry(final, job.get('config_name'), total))

        _save_state(state, current_user.state_file)
        return jsonify({'done': job['done'], 'total': total,
                        'best_score': job['best_score'], 'finished': finished,
                        'status': state['status']})

    except Exception as e:                       # noqa: BLE001 - reported to the client
        state['status'] = 'error'
        state['job'] = None
        state['log'] = (state.get('log') or '') + f'\nFatal error: {e}'
        state['result'] = {'success': False, 'error': str(e), 'log': state.get('log', '')}
        _save_state(state, current_user.state_file)
        return jsonify({'error': str(e)}), 500


@app.route('/api/status')
@login_required
def run_status():
    state = _read_state()
    return jsonify({'status': state.get('status', 'idle'),
                    'log': state.get('log', ''),
                    'progress': state.get('progress')})


def _uploaded_team_names():
    """Team names in the user's uploaded availability file, or None if absent."""
    path = _user_dir('uploads') / 'team_availability.csv'
    if not path.is_file():
        return None
    names = set()
    try:
        with open(path, encoding='utf-8-sig') as fh:
            for i, line in enumerate(fh):
                first = line.split(',')[0].strip()
                # Row 1 is a header whose contents the parser ignores.
                if i == 0 or not first:
                    continue
                names.add(first)
    except OSError:
        return None
    return names


def _check_teams_against_config(config):
    """Warn when the config names teams the availability file does not define.

    Team names are generated from the divisions, so a team with no row in the
    file is simply never available and plays zero games. The run still succeeds
    and the shortfall looks like a scheduling failure, which is the most
    expensive way to discover a mismatched upload.
    """
    uploaded = _uploaded_team_names()
    if not uploaded:
        return []

    expected = []
    for div, d in sorted((config.get('divisions') or {}).items()):
        expected += [f'{div}{i + 1}' for i in range(int(d.get('team_count') or 0))]

    missing = [t for t in expected if t not in uploaded]
    if not missing:
        return []

    shown = ', '.join(missing[:8]) + (', and more' if len(missing) > 8 else '')
    return [
        f"Your config expects {len(expected)} teams, but the uploaded "
        f"team_availability.csv has no row for {len(missing)} of them "
        f"({shown}). Teams with no availability play zero games, so the schedule "
        f"will come up short. Either add them to the file and upload it again, or "
        f"change the division team counts to match."
    ]


@app.route('/api/validate', methods=['POST'])
@login_required
def validate():
    config = request.get_json() or {}
    return jsonify({'warnings': validate_config(config) + _check_teams_against_config(config)})


@app.route('/api/history')
@login_required
def run_history():
    return jsonify({'runs': _read_history(current_user.home)})


@app.route('/api/results')
@login_required
def run_results():
    state = _read_state()
    if state.get('status') != 'done':
        return jsonify({'error': 'No completed run', 'status': state.get('status', 'idle')}), 400
    r = state.get('result') or {}
    return jsonify({
        'success': r.get('success', False),
        'stats': r.get('stats', {}),
        'schedule_preview': r.get('schedule_preview', [])[:100],
        'matchup_matrix': r.get('matchup_matrix', {}),
        'weekly_table': r.get('weekly_table', {}),
        'team_names': r.get('team_names', {}),
        'output_files': r.get('output_files', {}),
        'warnings': r.get('warnings', []),
        'log': r.get('log', ''),
    })


@app.route('/api/download/<filename>')
@login_required
def download_file(filename):
    # The directory comes from the session, so a crafted filename can only ever
    # reach the caller's own output. Stripping separators additionally prevents
    # traversal out of it.
    safe = re.sub(r'[^a-zA-Z0-9_.-]', '', filename)
    output_dir = _user_dir('output')
    if not safe or not (output_dir / safe).exists():
        return jsonify({'error': 'File not found'}), 404
    return send_from_directory(str(output_dir), safe, as_attachment=True)


if __name__ == '__main__':
    app.run(debug=True, port=5000)
