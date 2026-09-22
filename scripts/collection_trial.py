"""Pinned local trial services, independently supervised by macOS launchd."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import plistlib
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / '.env.collection-trial.json'
SERVICES = ('demo-api', 'production-api', 'gateway', 'frontend', 'demo-worker', 'production-worker')


def read_config():
    config = json.loads(CONFIG.read_text())
    if Path(config['worktree']).resolve() != ROOT:
        raise RuntimeError('Configuration belongs to another checkout')
    if Path(sys.prefix).resolve() != Path(config['python']).parent.parent.resolve():
        raise RuntimeError('Pinned Python environment is required')
    return config


def configure_backend(config, role):
    from dotenv import load_dotenv
    load_dotenv(config['secrets_env'], override=True)
    data = Path(config[role + '_data']).resolve()
    os.environ.update(
        XHS_AUDIT_DATA_DIR=str(data), XHS_AUDIT_OUTPUTS_DIR=str(data / 'outputs'),
        HERMES_HOME=str(data / 'hermes'), HERMES_CREATION_FAKE_RUNTIME='false',
        CRAWLER_AUTH_KEY_FILE=str(data / 'crawler_auth.key'), EXECJS_RUNTIME='Node',
        INVESTIGATION_MAX_POSTS='20' if role == 'production' else '1',
        M3_COMMENTS_PER_POST='1000' if role == 'production' else '300',
    )
    os.environ.pop('CRAWLER_AUTH_ENCRYPTION_KEY', None)
    os.environ['PATH'] = str(Path(config['node']).parent) + os.pathsep + os.environ['PATH']
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))


def identity(config, role):
    return {'role': role, 'worktree': str(ROOT), 'baseline': 'cd7ce5e',
            'data_dir': config[role + '_data'], 'python': config['python'],
            'posts_per_keyword': 20 if role == 'production' else 1,
            'analyze_limit': 340 if role == 'production' else 1,
            'comments_per_post': 1000 if role == 'production' else 300,
            'subcomments': False}


def get_json(url):
    with urllib.request.urlopen(url, timeout=8) as response:
        return json.load(response)


def check(config):
    for key in ('python', 'node', 'secrets_env', 'registry'):
        if not Path(config[key]).is_file():
            raise RuntimeError(f'Missing {key}')
    for role in ('demo', 'production'):
        root = Path(config[role + '_data'])
        for name in ('audit_index.sqlite3', 'investigation.sqlite3', 'investigation_creation.sqlite3', 'crawler_auth.key'):
            if not (root / name).is_file():
                raise RuntimeError(f'{role} missing {name}; will not initialize empty data')
        if not (root / 'outputs').is_dir():
            raise RuntimeError(f'{role} outputs missing')
    if Path(config['demo_data']).resolve() == Path(config['production_data']).resolve():
        raise RuntimeError('Demo and production must use different databases')
    if not (ROOT / 'Audit_assistant/node_modules/vite/bin/vite.js').is_file():
        raise RuntimeError('Frontend dependencies missing')
    print('Pinned paths, existing databases, outputs and dependencies verified.', flush=True)


def status(config):
    for role in ('demo', 'production'):
        value = get_json(f"http://127.0.0.1:{config[role + '_port']}/api/collection-runtime")
        if value != identity(config, role):
            raise RuntimeError(f'{role} identity mismatch')
    runtimes = get_json(f"http://127.0.0.1:{config['frontend_port']}/api/runtime-status")
    for role in ('demo', 'production'):
        item = runtimes[role]
        if not item['available'] or item['boundary_error'] or Path(item['data_directory']).resolve() != Path(config[role + '_data']).resolve():
            raise RuntimeError(f'{role} proxy boundary mismatch')
    reports = get_json(f"http://127.0.0.1:{config['frontend_port']}/api/historical-report-workspaces")
    if not {'historical-report-a', 'historical-report-b'} <= {r['workspace_id'] for r in reports['items']}:
        raise RuntimeError('Demo A/B reports unavailable')
    print('Both API identities, shared frontend routing and A/B reports verified.', flush=True)


def launch(config, service):
    label = 'local.xhs.collection-trial.' + service
    domain = f'gui/{os.getuid()}'
    if subprocess.run(['launchctl', 'print', domain + '/' + label], capture_output=True).returncode == 0:
        return
    logs = Path(config['logs'])
    logs.mkdir(parents=True, exist_ok=True)
    plist = Path.home() / 'Library/LaunchAgents' / (label + '.plist')
    plist.parent.mkdir(parents=True, exist_ok=True)
    payload = {'Label': label, 'ProgramArguments': [config['python'], str(Path(__file__).resolve()), '_service', service],
               'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 15,
               'ProcessType': 'Background', 'StandardOutPath': str(logs / (service + '.log')),
               'StandardErrorPath': str(logs / (service + '.log')),
               'EnvironmentVariables': {'HOME': str(Path.home()), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'PYTHONUNBUFFERED': '1'}}
    with plist.open('wb') as handle:
        plistlib.dump(payload, handle)
    plist.chmod(0o600)
    subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)


def serve(config, service):
    if service == 'frontend':
        # Frontend never inherits the backend credentials loaded from secrets_env.
        os.environ['VITE_API_PROXY_TARGET'] = f"http://127.0.0.1:{config['gateway_port']}"
        os.chdir(ROOT / 'Audit_assistant')
        os.execv(config['node'], [config['node'], 'node_modules/vite/bin/vite.js', '--host', '127.0.0.1',
                                 '--port', str(config['frontend_port']), '--strictPort'])
    if service == 'gateway':
        import uvicorn
        from dual_runtime_gateway import create_app
        uvicorn.run(create_app(Path(config['registry']), f"http://127.0.0.1:{config['demo_port']}",
                               f"http://127.0.0.1:{config['production_port']}"),
                    host='127.0.0.1', port=config['gateway_port'], log_level='warning')
        return
    role = service.split('-')[0]
    configure_backend(config, role)
    if service.endswith('-api'):
        import uvicorn
        from backend.main import app
        @app.get('/api/collection-runtime', include_in_schema=False)
        def runtime_identity():
            return identity(config, role)
        uvicorn.run(app, host='127.0.0.1', port=config[role + '_port'], log_level='warning')
    else:
        # One worker for this database. A crash is recovered using native lease fencing;
        # an already-started crawl is not blindly replayed.
        lock = (Path(config[role + '_data']) / 'collection-trial-worker.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from backend.investigation_creation.worker import build_worker
        build_worker().run_forever(poll_seconds=2)


def main():
    config = read_config()
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if action == '_service':
        serve(config, sys.argv[2])
        return
    check(config)
    if action == 'check':
        return
    if action == 'status':
        status(config)
        return
    if action == 'start':
        try:
            status(config)
            return
        except Exception:
            pass
        for key in ('demo_port', 'production_port', 'gateway_port', 'frontend_port'):
            with socket.socket() as sock:
                try:
                    sock.bind(('127.0.0.1', config[key]))
                except OSError as exc:
                    raise RuntimeError(f"Port {config[key]} occupied; no process will be replaced") from exc
        for service in SERVICES[:4]:
            launch(config, service)
        deadline = time.monotonic() + 90
        while True:
            try:
                status(config)
                return
            except Exception:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Startup validation failed; inspect startup logs')
                time.sleep(1)
    elif action == 'workers':
        status(config)
        for service in SERVICES[4:]:
            launch(config, service)
    elif action == 'stop':
        for service in reversed(SERVICES):
            label = 'local.xhs.collection-trial.' + service
            subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/' + label], capture_output=True)
        # Plists are retained for review; unloading does not delete task data.
    else:
        raise RuntimeError('Usage: collection_trial.py check|start|status|workers|stop')


if __name__ == '__main__':
    main()
