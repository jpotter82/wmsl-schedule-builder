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

_run_lock = threading.Lock()
_run_state = {
    'status': 'idle',
    'log': '',
    'result': None,
    'progress': None,
}


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
    saved = {}
    for key in required:
        f = request.files.get(key)
        if not f or not f.filename:
            return jsonify({'error': f'Missing file: {key}'}), 400
        dest = UPLOADS_DIR / f'{key}.csv'
        f.save(str(dest))
        saved[key] = str(dest)

    summary = {}
    for key in required:
        path = UPLOADS_DIR / f'{key}.csv'
        with open(path, encoding='utf-8-sig') as fh:
            lines = fh.readlines()
        summary[key] = {'rows': max(0, len(lines) - 1), 'filename': request.files[key].filename}

    return jsonify({'uploaded': summary})


@app.route('/api/run', methods=['POST'])
def start_run():
    if not _run_lock.acquire(blocking=False):
        return jsonify({'error': 'A scheduler run is already in progress'}), 409

    for f in OUTPUT_DIR.iterdir():
        if f.is_file():
            f.unlink()

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

    def _progress(done, total, best_score):
        _run_state['progress'] = {'done': done, 'total': total, 'best_score': best_score}

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
            _run_lock.release()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({'started': True})


@app.route('/api/status')
def run_status():
    return jsonify({
        'status': _run_state['status'],
        'log': _run_state.get('log', ''),
        'progress': _run_state.get('progress'),
    })


@app.route('/api/validate', methods=['POST'])
def validate():
    config = request.get_json() or {}
    return jsonify({'warnings': validate_config(config)})


@app.route('/api/results')
def run_results():
    if _run_state['status'] != 'done':
        return jsonify({'error': 'No completed run', 'status': _run_state['status']}), 400
    r = _run_state.get('result', {})
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
