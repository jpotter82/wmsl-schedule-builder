import json
import os
import re
import shutil
import threading
from pathlib import Path

from flask import (Flask, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)
from flask_login import (LoginManager, current_user, login_required, login_user,
                         logout_user)

import auth
import mailer
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


# Run the scheduler inside the request instead of on a background thread.
#
# Required on any host that may run more than one worker process or recycle idle
# ones (cPanel/Passenger shared hosting, multi-worker gunicorn), because run state
# lives in memory in this process. Safe to enable anywhere: a 15-attempt run
# completes in about 1.6 seconds, well inside normal request timeouts.
SYNC_RUNS = auth.env('SYNC_RUNS').strip().lower() in ('1', 'true', 'yes', 'on')

# One run at a time PER USER, not per server. A single global lock would mean one
# league admin's run returns HTTP 409 to everyone else for its duration.
#
# Only meaningful when several requests share a process (threaded dev server,
# gunicorn with threads). Under CGI each request is its own process, so this cannot
# see other requests at all -- which is harmless, because a run there completes
# inside the request that started it.
_run_locks = {}
_run_locks_guard = threading.Lock()


def _lock_for_current_user():
    key = current_user.user_id
    with _run_locks_guard:
        lock = _run_locks.get(key)
        if lock is None:
            lock = _run_locks[key] = threading.Lock()
    return lock


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


def _read_state():
    try:
        with open(current_user.state_file, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return _blank_state()


def _safe_name(name):
    return re.sub(r'[^a-zA-Z0-9_-]', '', name or '')


def _migrate_legacy_data(user):
    """Move the pre-accounts configs/ and uploads/ into the first account.

    Runs once, when the first user registers, so the existing setup keeps working
    instead of appearing to have lost its saved seasons.
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

    if first_account:
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


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))


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
    return render_template('index.html', user_email=current_user.email)


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

    summary = {}
    for key in required:
        with open(uploads / f'{key}.csv', encoding='utf-8-sig') as fh:
            lines = fh.readlines()
        summary[key] = {'rows': max(0, len(lines) - 1),
                        'filename': request.files[key].filename}

    return jsonify({'uploaded': summary})


@app.route('/api/run', methods=['POST'])
@login_required
def start_run():
    run_lock = _lock_for_current_user()
    if not run_lock.acquire(blocking=False):
        return jsonify({'error': 'A scheduler run is already in progress'}), 409

    output_dir = _user_dir('output')
    uploads = _user_dir('uploads')

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

    csv_paths = {k: str(uploads / f'{k}.csv')
                 for k in ('team_availability', 'field_availability', 'team_blackouts')}
    for key, path in csv_paths.items():
        if not os.path.exists(path):
            run_lock.release()
            return jsonify({'error': f'CSV not uploaded yet: {key}'}), 400

    # Resolve everything that depends on the signed-in user HERE, while the request
    # context still exists. The background thread below has no request context, so
    # current_user is unavailable inside it.
    state_path = current_user.state_file

    state = _blank_state()
    state['status'] = 'running'
    _save_state(state, state_path)

    def _progress(done, total, best_score):
        state['progress'] = {'done': done, 'total': total, 'best_score': best_score}
        _save_state(state, state_path)

    def _run():
        try:
            result = run_scheduler(config, csv_paths, str(output_dir),
                                   config_name=config_name, progress=_progress)
            state['result'] = result
            state['log'] = result.get('log', '')
            state['status'] = 'done' if result.get('success') else 'error'
        except Exception as e:
            state['status'] = 'error'
            state['log'] += f'\nFatal error: {e}'
            state['result'] = {'success': False, 'error': str(e), 'log': state['log']}
        finally:
            _save_state(state, state_path)
            run_lock.release()

    if SYNC_RUNS:
        _run()
        return jsonify({'started': True, 'sync': True, 'status': state['status']})

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'started': True, 'sync': False})


@app.route('/api/status')
@login_required
def run_status():
    state = _read_state()
    return jsonify({'status': state.get('status', 'idle'),
                    'log': state.get('log', ''),
                    'progress': state.get('progress')})


@app.route('/api/validate', methods=['POST'])
@login_required
def validate():
    return jsonify({'warnings': validate_config(request.get_json() or {})})


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
