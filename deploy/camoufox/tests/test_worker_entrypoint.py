"""Exercise the released entry, manifest and supervisor with offline work bodies.

build_worker uses a synthetic body in a temporary, committed source copy. The
route test also replaces the external exit probe. The real entry, configure(),
artifact checks, Settings, run_forever, spawn supervisor and route factory run.
No browser, platform request or production state is involved.
"""

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest

from test_control import manifest
from control import digest


DEPLOY = Path(__file__).resolve().parents[1]
APP = DEPLOY.parents[1]

OFFLINE_WORKER = '''
# Test-only body: retain the real run_forever / _serve_process / supervisor.
def build_worker():
    import json, os
    from pathlib import Path
    from backend.audit_agent.config import settings
    root = Path(settings.outputs_dir)
    root.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    def run_once():
        receipt = {
            'pid': pid, 'parent': os.getppid(), 'cwd': os.getcwd(),
            'manifest': os.environ.get('CAMOUFOX_RELEASE_MANIFEST'),
            'auth': settings.app_auth_mode,
            'workers': settings.task_worker_processes,
            'capacity': settings.task_execution_capacity,
            'data': str(settings.data_dir),
            'locks': str(settings.task_resource_lock_dir),
            'profiles': str(settings.crawler_browser_profile_root),
            'scheduler': str(settings.request_scheduler_db),
            'safety': settings.crawler_safety_enabled,
            'inherited_unreviewed': os.environ.get('UNREVIEWED_PARENT_VALUE'),
        }
        (root / f'{pid}.ready').write_text(json.dumps(receipt))
        deadline = time.monotonic() + 30
        while not (root / f'{pid}.release').exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('offline work release deadline exceeded')
            time.sleep(.02)
        (root / f'{pid}.finished').touch()
        return None
    worker = object.__new__(InvestigationWorker)
    worker.run_once = run_once
    return worker
'''


@pytest.fixture
def released_worker(tmp_path, request):
    root = tmp_path.resolve()
    config = manifest(root)
    config['maintenance'] = False
    config['ports'].update(frontend=13299, allowed=[18298, 13299])
    app = root / 'app'
    for package in ('backend', 'hermes_m0'):
        shutil.copytree(APP / package, app / package,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    support = app / 'deploy/camoufox/runtime-support'
    support.mkdir(parents=True)
    shutil.copyfile(DEPLOY / 'control.py', support.parent / 'control.py')
    for name in ('serve.py', 'worker.py'):
        shutil.copyfile(DEPLOY / 'runtime-support' / name, support / name)
    worker_module = app / 'backend/investigation_creation/worker.py'
    worker_module.write_text(worker_module.read_text() + '\n' + (ROUTE_WORKER if getattr(request, 'param', '') == 'routes' else OFFLINE_WORKER))
    config['artifacts']['runtime_support']['path'] = str(support)
    for name, entry in config['artifacts'].items():
        if name not in {'app', 'runtime_support'}:
            path = Path(entry['path'])
            path.mkdir()
            (path / 'artifact').write_text(name)
    if getattr(request, 'param', '') == 'routes':
        shutil.copytree(APP.parent / 'crawler/tools', root / 'crawler/tools',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name, commit_key in (('app', 'app_commit'), ('crawler', 'crawler_commit')):
        path = Path(config['artifacts'][name]['path'])
        for command in (
            ['init', '-q'], ['add', '.'],
            ['-c', 'user.name=fixture', '-c', 'user.email=fixture@example.test',
             '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null',
             'commit', '-qm', 'offline release fixture'],
        ):
            subprocess.run(['git', '-C', str(path), *command], check=True)
        config['sources'][commit_key] = subprocess.check_output(
            ['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()
    for entry in config['artifacts'].values():
        entry['sha256'] = digest(entry['path'])
    private = {
        'APP_AUTH_MODE': 'required', 'TASK_WORKER_PROCESSES': '3',
        'TASK_EXECUTION_CAPACITY': '3', 'TASK_ANALYSIS_CAPACITY': '3',
        'TASK_RESOURCE_LOCK_DIR': str(root / 'resource-locks'),
        'CRAWLER_SHARED_EXIT_ACTIVE_LIMIT': '2', 'REQUEST_CONCURRENCY': '1',
        'CRAWLER_PROXY_ENABLED': 'false',
        'CRAWLER_SAFETY_ENABLED': 'true', 'HERMES_HOME': str(root / 'hermes'),
    }
    private_path = root / 'private.json'
    private_path.write_text(json.dumps(private))
    private_path.chmod(0o600)
    config['parent_env_files'] = [{
        'path': str(private_path), 'sha256': digest(private_path),
        'expected_uid': os.geteuid(), 'reviewed_allowlist': list(private),
    }]
    output = root / 'outputs'
    config['environment']['XHS_AUDIT_OUTPUTS_DIR'] = str(output)
    config['runtime']['app_python'] = sys.executable
    config['runtime']['crawler_python'] = sys.executable
    path = root / 'manifest.json'
    path.write_text(json.dumps(config))
    return support / 'worker.py', path, config, output


def process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def wait_ready(process, output, log, *, excluded=()):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        assert process.poll() is None, log.read_text()
        receipts = {}
        for path in output.glob('*.ready'):
            try:
                receipt = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            pid = receipt['pid']
            if pid not in excluded and process_alive(pid):
                receipts[pid] = receipt
        if len(receipts) == 3:
            return receipts
        time.sleep(.05)
    pytest.fail('release entry did not start three ready workers: ' + log.read_text())


@pytest.mark.parametrize('group_signal', [False, True])
def test_release_entry_starts_configured_workers_replaces_crash_and_drains(
        released_worker, tmp_path, group_signal):
    entry, path, config, output = released_worker
    log_path = tmp_path / 'worker.log'
    env = {
        'PATH': os.environ['PATH'], 'PYTHONDONTWRITEBYTECODE': '1',
        'CAMOUFOX_RELEASE_MANIFEST': str(path),
        # Reviewed manifest values must win and unrelated parent state disappear.
        'APP_AUTH_MODE': 'disabled', 'TASK_WORKER_PROCESSES': '1',
        'UNREVIEWED_PARENT_VALUE': 'must-not-reach-workers',
    }
    with log_path.open('w') as log:
        process = subprocess.Popen(
            [sys.executable, '-B', str(entry), '--poll-seconds', '.05'],
            cwd=tmp_path, env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            initial = wait_ready(process, output, log_path)
            for receipt in initial.values():
                assert receipt == {
                    'pid': receipt['pid'], 'parent': process.pid,
                    'cwd': config['artifacts']['app']['path'], 'manifest': str(path),
                    'auth': 'required', 'workers': 3, 'capacity': 3,
                    'data': str(Path(config['runtime']['account_db']).parent),
                    'locks': str(tmp_path.resolve() / 'resource-locks'),
                    'profiles': config['runtime']['logical_profile_root'],
                    'scheduler': config['runtime']['scheduler_db'],
                    'safety': True, 'inherited_unreviewed': None,
                }
            dead = next(iter(initial))
            os.kill(dead, signal.SIGKILL)
            current = wait_ready(process, output, log_path, excluded={dead})
            assert len(set(current) - set(initial)) == 1
            if group_signal:
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.send_signal(signal.SIGTERM)
            time.sleep(.15)
            assert process.poll() is None, 'accepted work must drain before exit'
            for pid in current:
                (output / f'{pid}.release').touch()
            assert process.wait(timeout=15) == 0, log_path.read_text()
            assert all((output / f'{pid}.finished').exists() for pid in current)
            assert all(not process_alive(pid) for pid in current)
        finally:
            # The parent can fail before reaping children; this group is ours
            # because Popen started a new session, even if the parent has exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.wait(timeout=5)


# Synthetic task dispatch, but real required-auth ownership, route factory,
# profile/resource/exit leases, admission rows and bounded capacity waiting.
ROUTE_WORKER = """
def build_worker():
    import fcntl, json, os
    from pathlib import Path
    from backend.audit_agent import crawler_route as routes
    from backend.audit_agent.config import settings
    from backend.audit_agent.crawler_account_store import crawler_account_store as accounts
    from backend.application_auth.store import AuthStore
    from backend.audit_agent.route_capacity_wait import acquire_with_capacity_wait
    root = Path(settings.outputs_dir)
    root.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    with (root / 'assignment.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_path = root / 'assignments.json'
        if not state_path.exists():
            auth = AuthStore(settings.app_auth_db)
            owner = auth.create_user(username='offline-owner', password='offline-password-123',
                                     activation_mode='created_at')['id']
            ids = []
            for index in range(3):
                account = accounts.create(platform='dy', display_name='offline-' + str(index))
                accounts.save_auth_state(account['id'], 'offline-encrypted-fixture')
                auth.register_crawler_account(account['id'], owner)
                ids.append(account['id'])
            state_path.write_text(json.dumps(dict(owner=owner, accounts=ids, next=0)))
        state = json.loads(state_path.read_text())
        account_id = state['accounts'][state['next']]
        state['next'] += 1
        state_path.write_text(json.dumps(state))
    routes._run_maintenance_probe = lambda bundle: routes.route_contract().ProbeReceipt(
        bundle.snapshot.proxy_id, bundle.snapshot.config_revision, 'offline-probe',
        '203.0.113.9', time.time())
    done = False
    def run_once():
        nonlocal done
        if done:
            return None
        done = True
        def waiting(reason):
            if reason:
                (root / f'{pid}.waiting').write_text(reason)
        bundle = acquire_with_capacity_wait(
            lambda: routes.acquire_route_execution(account_id, 'offline-' + str(pid),
                state['owner'], account_store=accounts),
            check=lambda: None, on_wait=waiting, max_wait=20)
        try:
            receipt = dict(pid=pid, account=account_id, operation=bundle.snapshot.operation_id,
                exit=bundle.snapshot.observed_exit_ip, handles=len(bundle.pass_fds),
                request_concurrency=settings.request_concurrency,
                exit_capacity=settings.crawler_shared_exit_active_limit,
                acquired=time.monotonic())
            (root / f'{pid}.owned.tmp').write_text(json.dumps(receipt))
            (root / f'{pid}.owned.tmp').replace(root / f'{pid}.owned')
            deadline = time.monotonic() + 25
            while not (root / f'{pid}.release').exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError('offline route release deadline exceeded')
                time.sleep(.02)
        finally:
            bundle.close()
            (root / f'{pid}.closed').write_text(str(time.monotonic()))
        return None
    worker = object.__new__(InvestigationWorker)
    worker.run_once = run_once
    return worker
"""


def await_condition(process, log, predicate):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        assert process.poll() is None, log.read_text()
        result = predicate()
        if result:
            return result
        time.sleep(.03)
    pytest.fail('offline route condition timed out: ' + log.read_text())


@pytest.mark.parametrize('released_worker', ['routes'], indirect=True)
def test_release_entry_composes_three_workers_real_route_capacity_and_wait(
        released_worker, tmp_path):
    import fcntl
    import sqlite3
    entry, path, config, output = released_worker
    log_path = tmp_path / 'routes.log'
    with log_path.open('w') as log:
        process = subprocess.Popen([sys.executable, '-B', str(entry), '--poll-seconds', '.05'],
            cwd=tmp_path, env={'PATH': os.environ['PATH'], 'CAMOUFOX_RELEASE_MANIFEST': str(path)},
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            await_condition(process, log_path, lambda:
                len(list(output.glob('*.owned'))) == 2 and len(list(output.glob('*.waiting'))) == 1)
            owned = [json.loads(p.read_text()) for p in output.glob('*.owned')]
            assert len({r['account'] for r in owned}) == 2
            assert all(r['handles'] >= 4 and r['request_concurrency'] == 1
                       and r['exit_capacity'] == 2 and r['exit'] == '203.0.113.9' for r in owned)
            waiter = next(output.glob('*.waiting'))
            assert waiter.read_text() == 'route_capacity'
            waiting_pid = int(waiter.stem)
            assert waiting_pid not in {r['pid'] for r in owned}
            # Both holders remain accepted, while the third has no issued route.
            with sqlite3.connect(config['runtime']['scheduler_db']) as db:
                assert db.execute("SELECT count(*) FROM route_operations WHERE state='issued'").fetchone()[0] == 2
            released = owned[0]['pid']
            (output / f'{released}.release').touch()
            await_condition(process, log_path, lambda: (output / f'{waiting_pid}.owned').exists())
            third = json.loads((output / f'{waiting_pid}.owned').read_text())
            assert third['account'] not in {r['account'] for r in owned}
            assert not (output / f"{owned[1]['pid']}.closed").exists()
            process.send_signal(signal.SIGTERM)
            for receipt in [*owned, third]:
                (output / f"{receipt['pid']}.release").touch()
            assert process.wait(timeout=15) == 0, log_path.read_text()
            assert len(list(output.glob('*.closed'))) == 3
            with sqlite3.connect(config['runtime']['scheduler_db']) as db:
                assert db.execute("SELECT count(*) FROM route_operations WHERE state != 'ended'").fetchone()[0] == 0
            with sqlite3.connect(config['runtime']['account_db']) as db:
                assert db.execute('SELECT count(*) FROM crawler_route_admissions WHERE state="active"').fetchone()[0] == 0
            # Every actual account, shared route and shared exit FD lock is free.
            locks = list((tmp_path / 'resource-locks').rglob('*.lock'))
            profile_locks = list((Path(config['runtime']['logical_profile_root']) / '.resource-locks').glob('*.lock'))
            assert len(profile_locks) == 3
            assert len(locks) == 5
            locks.extend(profile_locks)
            for lock_path in locks:
                with lock_path.open('a+b') as handle:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.wait(timeout=5)
