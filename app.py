import json
import os
import re
import threading
from pathlib import Path

from flask import Flask, jsonify, request, render_template, send_from_directory

from scheduler_wrapper import DEFAULT_CONFIG, run_scheduler, validate_config

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
CONFIGS_DIR = BASE_DIR / 'configs'
UPLOADS_DIR = BASE_DIR / 'uploads'
OUTPUT_DIR = BASE_DIR / 'output'

for d in (CONFIGS_DIR, UPLOADS_DIR, OUTPUT_DIR):
    d.mkdir(exist_ok=True)

# Run the scheduler inside the request instead of on a background thread.
#
# Required on any host that may run more than one worker process or recycle idle
# ones (cPanel/Passenger shared hosting, multi-worker gunicorn), because run state
# lives in memory in this process. Safe to enable anywhere: a 15-attempt run
# completes in about 1.6 seconds, well inside normal request timeouts.
SYNC_RUNS = os.environ.get('WMSL_SYNC_RUNS', '').strip().lower() in ('1', 'true', 'yes', 'on')

_run_lock = threading.Lock()
_run_state = {
    'status': 'idle',
    'log': '',
    'result': None,
    'progress': None,
}

# Run state is mirrored to disk because it cannot be assumed to survive in memory.
#
# /api/run, /api/status and /api/results are three separate requests. Under CGI every
# request is a brand new process; under Passenger or multi-worker gunicorn they may
# land on different long-lived processes. Either way the process answering /api/status
# is frequently NOT the one that ran the scheduler, so an in-memory dict alone reports
# "idle" forever and the UI waits for a result that already exists.
STATE_FILE = BASE_DIR / '.run_state.json'


def _save_state():
    """Persist run state. Written to a temp file and renamed so a concurrent reader
    never sees a half-written file."""
    try:
        tmp = STATE_FILE.with_suffix('.json.tmp')
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(_run_state, fh)
        os.replace(tmp, STATE_FILE)
    except (OSError, TypeError, ValueError):
        # Persistence is best-effort; in-process state still works for single-process use.
        pass


def _read_state():
    """Return the shared run state, preferring what is on disk."""
    try:
        with open(STATE_FILE, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return _run_state


def _safe_config_name(name):
    return re.sub(r'[^a-zA-Z0-9_-]', '', name)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/config/defaults')
def config_defaults():
    return jsonify(DEFAULT_CONFIG)


@app.route('/api/configs')
def list_configs():
    names = []
    if CONFIGS_DIR.exists():
        for f in sorted(CONFIGS_DIR.glob('*.json')):
            names.append(f.stem)
    return jsonify(names)


@app.route('/api/configs/<name>', methods=['GET'])
def load_config(name):
    name = _safe_config_name(name)
    path = CONFIGS_DIR / f'{name}.json'
    if not path.exists():
        return jsonify({'error': 'Config not found'}), 404
    with open(path) as f:
        return jsonify(json.load(f))


@app.route('/api/configs/<name>', methods=['POST'])
def save_config(name):
    name = _safe_config_name(name)
    if not name:
        return jsonify({'error': 'Invalid config name'}), 400
    path = CONFIGS_DIR / f'{name}.json'
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No JSON body'}), 400
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    return jsonify({'saved': name})


@app.route('/api/configs/<name>', methods=['DELETE'])
def delete_config(name):
    name = _safe_config_name(name)
    path = CONFIGS_DIR / f'{name}.json'
    if not path.exists():
        return jsonify({'error': 'Config not found'}), 404
    path.unlink()
    return jsonify({'deleted': name})


@app.route('/api/upload', methods=['POST'])
def upload_csvs():
    required = ['team_availability', 'field_availability', 'team_blackouts']

    for key in required:
        f = request.files.get(key)
        if not f or not f.filename:
            return jsonify({'error': f'Missing file: {key}'}), 400
        f.save(str(UPLOADS_DIR / f'{key}.csv'))

    summary = {}
    for key in required:
        path = UPLOADS_DIR / f'{key}.csv'
        with open(path, encoding='utf-8-sig') as fh:
            lines = fh.readlines()
        summary[key] = {'rows': max(0, len(lines) - 1),
                        'filename': request.files[key].filename}

    return jsonify({'uploaded': summary})


@app.route('/api/run', methods=['POST'])
def start_run():
    if not _run_lock.acquire(blocking=False):
        return jsonify({'error': 'A scheduler run is already in progress'}), 409

    # Clear previous output. A file that cannot be removed is skipped rather than
    # failing the run: on Windows a just-downloaded file may still be held open by
    # the server, and every output file is rewritten by name anyway, so a leftover
    # is harmless. Without this, downloading a schedule and then running again
    # returns a 500.
    for f in OUTPUT_DIR.iterdir():
        if f.is_file():
            try:
                f.unlink()
            except OSError:
                pass

    payload = request.get_json() or {}
    # Accept either a bare config or {config, config_name}
    config = payload.get('config', payload) or DEFAULT_CONFIG
    config_name = payload.get('config_name')

    csv_paths = {
        'team_availability': str(UPLOADS_DIR / 'team_availability.csv'),
        'field_availability': str(UPLOADS_DIR / 'field_availability.csv'),
        'team_blackouts': str(UPLOADS_DIR / 'team_blackouts.csv'),
    }

    for key, path in csv_paths.items():
        if not os.path.exists(path):
            _run_lock.release()
            return jsonify({'error': f'CSV not uploaded yet: {key}'}), 400

    _run_state['status'] = 'running'
    _run_state['log'] = ''
    _run_state['result'] = None
    _run_state['progress'] = None
    _save_state()

    def _progress(done, total, best_score):
        _run_state['progress'] = {'done': done, 'total': total, 'best_score': best_score}
        _save_state()

    def _run():
        try:
            result = run_scheduler(config, csv_paths, str(OUTPUT_DIR),
                                   config_name=config_name, progress=_progress)
            _run_state['result'] = result
            _run_state['log'] = result.get('log', '')
            _run_state['status'] = 'done' if result.get('success') else 'error'
        except Exception as e:
            _run_state['status'] = 'error'
            _run_state['log'] += f'\nFatal error: {e}'
            _run_state['result'] = {'success': False, 'error': str(e), 'log': _run_state['log']}
        finally:
            _save_state()
            _run_lock.release()

    if SYNC_RUNS:
        # Do the work inside the request. A full 15-attempt run takes under two
        # seconds, so there is nothing to gain from backgrounding it, and on hosts
        # that recycle or fork worker processes (cPanel/Passenger, multi-worker
        # gunicorn) a background thread is actively harmful: the status poll can
        # land on a process that never ran the job and appear to hang forever.
        _run()
        return jsonify({'started': True, 'sync': True,
                        'status': _run_state['status']})

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({'started': True, 'sync': False})


@app.route('/api/status')
def run_status():
    state = _read_state()
    return jsonify({
        'status': state.get('status', 'idle'),
        'log': state.get('log', ''),
        'progress': state.get('progress'),
    })


@app.route('/api/validate', methods=['POST'])
def validate():
    config = request.get_json() or {}
    return jsonify({'warnings': validate_config(config)})


@app.route('/api/results')
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
def download_file(filename):
    safe = re.sub(r'[^a-zA-Z0-9_.-]', '', filename)
    path = OUTPUT_DIR / safe
    if not path.exists():
        return jsonify({'error': 'File not found'}), 404
    return send_from_directory(str(OUTPUT_DIR), safe, as_attachment=True)


if __name__ == '__main__':
    app.run(debug=True, port=5000)
