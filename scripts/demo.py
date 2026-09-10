#!/usr/bin/env python3
"""Portable single-environment demo supervisor (macOS/Linux)."""
from __future__ import annotations
import argparse
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SEED = ROOT / 'demo' / 'seed'


def absolute(value):
    p = Path(value).expanduser()
    return (ROOT / p).resolve() if not p.is_absolute() else p.resolve()


def environment(config):
    from dotenv import dotenv_values
    import imageio_ffmpeg
    if not config.is_file():
        raise RuntimeError('Copy demo/demo.env.example to .env.demo.local and configure it first.')
    values = {k: v for k, v in dotenv_values(config).items() if v is not None}
    data = absolute(values.get('DEMO_DATA_DIR', './data/demo'))
    # Do not inherit unrelated provider keys or old deployment settings.
    env = {k: v for k, v in os.environ.items() if k in (
        'PATH', 'HOME', 'USER', 'TMPDIR', 'LANG', 'LC_ALL', 'SSL_CERT_FILE',
        'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY', 'PLAYWRIGHT_BROWSERS_PATH',
    )}
    env.update(values)
    api, web = int(values.get('DEMO_API_PORT', '8158')), int(values.get('DEMO_WEB_PORT', '3158'))
    if api == web or not all(1024 <= p <= 65535 for p in (api, web)):
        raise RuntimeError('API and frontend require different ports in 1024..65535.')
    env.update({
        'XHS_AUDIT_DATA_DIR': str(data), 'XHS_AUDIT_OUTPUTS_DIR': str(data / 'outputs'),
        'CRAWLER_AUTH_KEY_FILE': str(data / 'crawler_auth.key'),
        'HERMES_HOME': str(data / 'hermes'),
        'MEDIACRAWLER_DIR': str(ROOT / 'external' / 'MediaCrawler'),
        'CRAWLER_LOGIN_PYTHON': str(ROOT / 'external' / 'MediaCrawler' / '.venv' / 'bin' / 'python'),
        'XHS_AUDIT_BACKEND_PORT': str(api), 'DEMO_WEB_PORT': str(web),
        'FFMPEG_PATH': values.get('FFMPEG_PATH') or imageio_ffmpeg.get_ffmpeg_exe(),
        'DEMO_CODE_DIR': str(ROOT), 'PYTHONUNBUFFERED': '1', 'PYTHON_DOTENV_DISABLED': '1',
        'CORS_ALLOW_ORIGINS': f'http://127.0.0.1:{web},http://localhost:{web}',
    })
    # Empty corpus or fixture overrides must not revive machine-specific archives.
    env.pop('HISTORICAL_REPORT_A_DB', None)
    env.pop('HISTORICAL_REPORT_B_DB', None)
    return env, data, api, web


def verified_seed(data):
    manifest = json.loads((SEED / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        p = SEED / name
        if hashlib.sha256(p.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Seed hash mismatch: {name}')
    for label, spec in manifest['reports'].items():
        p = data / 'demo-seed' / f'report-{label}.sqlite3'
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != spec['database_sha256']:
            raise RuntimeError('Run init first; archive missing or modified: ' + str(p))
    return manifest


def restore_seed(data):
    manifest = json.loads((SEED / 'manifest.json').read_text())
    archive = data / 'demo-seed'
    archive.mkdir(parents=True, exist_ok=True)
    for name, expected in manifest['files'].items():
        packed = SEED / name
        if hashlib.sha256(packed.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Seed hash mismatch: {name}')
        if not name.endswith('.sqlite3.gz'):
            continue
        label = name.split('-')[1].split('.')[0]
        destination = archive / name.removesuffix('.gz')
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != manifest['reports'][label]['database_sha256']:
                raise RuntimeError(f'Refusing to overwrite changed archive: {destination}')
            continue
        payload = gzip.decompress(packed.read_bytes())
        if hashlib.sha256(payload).hexdigest() != manifest['reports'][label]['database_sha256']:
            raise RuntimeError(f'Expanded archive mismatch: {name}')
        temporary = destination.with_suffix('.tmp')
        temporary.write_bytes(payload)
        temporary.replace(destination)
    verified_seed(data)


def initialize(data):
    marker = data / 'demo-data.json'
    if not marker.exists() and data.exists() and any(data.glob('*.sqlite3')):
        raise RuntimeError('Refusing to initialize an unmarked existing database. Choose a new demo directory.')
    data.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({'format': 1, 'purpose': 'standalone-demo'}))
    restore_seed(data)
    from backend.main import historical_report_demo_service
    workspaces = historical_report_demo_service.list_workspaces(principal_id='local-user')
    if len(workspaces) != 2:
        raise RuntimeError('Expected exactly two A/B workspaces.')
    print('Initialized A/B and required stores. Existing tasks, accounts and conversations retained.')


def check(data):
    verified_seed(data)
    from backend.hermes_runtime.adapter import HermesRuntimeBinding
    from hermes_m0.account_corpus import AccountCorpus
    binding = HermesRuntimeBinding()
    binding._verify_version()
    binding.product_system_prompt()
    binding.agent_factory()
    AccountCorpus.load()
    if not (ROOT / 'Audit_assistant' / 'dist' / 'index.html').is_file():
        raise RuntimeError('Frontend build missing. Run pnpm --dir Audit_assistant build.')
    crawler = ROOT / 'external' / 'MediaCrawler'
    python = crawler / '.venv' / 'bin' / 'python'
    if not python.is_file():
        raise RuntimeError('MediaCrawler .venv missing; follow the install guide.')
    subprocess.run([str(python), '-c', 'from playwright.sync_api import sync_playwright; from pathlib import Path\nwith sync_playwright() as p:\n assert Path(p.chromium.executable_path).is_file(), "Install Playwright Chromium first"'], check=True, stdout=subprocess.DEVNULL)
    subprocess.run([os.environ['FFMPEG_PATH'], '-version'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not os.getenv('DASHSCOPE_API_KEY', '').strip() or os.getenv('DASHSCOPE_API_KEY', '').startswith('your_'):
        raise RuntimeError('Set DASHSCOPE_API_KEY in .env.demo.local for natural-language features.')
    from backend.main import historical_report_demo_service
    if len(historical_report_demo_service.list_workspaces(principal_id='local-user')) != 2:
        raise RuntimeError('A/B import incomplete.')
    print('Check passed: Hermes, prompt, A/B, corpus, frontend, crawler/browser, ffmpeg and key presence. No provider/crawl calls made.')
    print('Platform login and provider availability are checked only during real use.')


def fetch(port, path):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=3) as response:
        return json.load(response)


def status(data, api, web):
    expected = {'code_dir': str(ROOT), 'data_dir': str(data), 'python': sys.executable, 'api_port': api, 'web_port': web}
    identity = fetch(api, '/api/demo/identity')
    proxy = fetch(web, '/api/demo/identity')
    if identity != expected or proxy != expected:
        raise RuntimeError('Runtime identity mismatch; refusing to use this environment.')
    state = json.loads((data / 'demo-runtime.json').read_text())
    for key in ('supervisor', 'api', 'web', 'worker'):
        os.kill(state[key], 0)
    fetch(web, '/api/historical-report-workspaces')
    print(f'Running: http://127.0.0.1:{web}/investigation | {data}')


def vacant(port):
    with socket.socket() as s:
        try:
            s.bind(('127.0.0.1', port))
        except OSError as exc:
            raise RuntimeError(f'Port {port} is occupied. No process was killed or port changed.') from exc


def start(env, data, api, web):
    data.mkdir(parents=True, exist_ok=True)
    with (data / 'demo-supervisor.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            status(data, api, web)
            return
        check(data)
        vacant(api)
        vacant(web)
        logs = data / 'startup-logs'
        logs.mkdir(exist_ok=True)
        children, handles = {}, []
        stopping = False
        def stop_signal(*_):
            nonlocal stopping
            stopping = True
        old_handlers = {sig: signal.signal(sig, stop_signal) for sig in (signal.SIGINT, signal.SIGTERM)}
        def launch(name, args):
            handle = (logs / f'{name}.log').open('a')
            handles.append(handle)
            child_env = env if name != 'web' else {k: v for k, v in env.items() if k in ('PATH', 'HOME', 'LANG', 'PYTHONUNBUFFERED', 'XHS_AUDIT_BACKEND_PORT')}
            children[name] = subprocess.Popen(args, cwd=ROOT, env=child_env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            launch('api', [sys.executable, '-m', 'uvicorn', 'backend.demo_app:app', '--host', '127.0.0.1', '--port', str(api)])
            # Static frontend and proxy live in one tiny separate process. No Vite receives keys.
            launch('web', [sys.executable, '-m', 'uvicorn', 'backend.demo_web:app', '--host', '127.0.0.1', '--port', str(web)])
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and not stopping:
                if any(p.poll() is not None for p in children.values()):
                    raise RuntimeError(f'Startup child exited; see {logs}')
                try:
                    identity = fetch(web, '/api/demo/identity')
                    if identity.get('data_dir') != str(data) or identity.get('code_dir') != str(ROOT):
                        raise RuntimeError('Startup identity mismatch.')
                    fetch(web, '/api/historical-report-workspaces')
                    break
                except (OSError, ValueError):
                    time.sleep(0.5)
            else:
                raise RuntimeError('Startup interrupted or timed out.')
            launch('worker', [sys.executable, '-m', 'backend.investigation_creation.worker'])
            state = {'supervisor': os.getpid(), **{name: p.pid for name, p in children.items()}}
            (data / 'demo-runtime.json').write_text(json.dumps(state))
            status(data, api, web)
            print('Keep this terminal open. Ctrl+C stops this stack; reports and conversations remain saved.', flush=True)
            while not stopping:
                for name, child in children.items():
                    if child.poll() is not None:
                        raise RuntimeError(f'{name} exited; stopping this stack. See {logs / (name + ".log")}')
                time.sleep(0.5)
        finally:
            # Signal only process groups created by this supervisor; never discover/kill unrelated workers.
            for child in children.values():
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            for child in children.values():
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            for handle in handles:
                handle.close()
            (data / 'demo-runtime.json').unlink(missing_ok=True)
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init', 'check', 'start', 'status'))
    parser.add_argument('--config', type=Path, default=ROOT / '.env.demo.local')
    args = parser.parse_args()
    env, data, api, web = environment(args.config.resolve())
    os.environ.clear()
    os.environ.update(env)
    if args.command == 'init':
        initialize(data)
    elif args.command == 'check':
        check(data)
    elif args.command == 'status':
        status(data, api, web)
    else:
        start(env, data, api, web)

if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f'Demo: {exc}', file=sys.stderr)
        sys.exit(1)
