"""Local investigation pin across two real FD workers. All network sends are fixtures."""
import asyncio
from contextlib import closing, contextmanager
import json
import importlib.util
from pathlib import Path
import os
import select
import sqlite3
import subprocess
import sys
import time

import httpx
import pytest

from backend.audit_agent import crawler_route as routes
from backend.audit_agent.crawler_child_runtime import authority_for
from tests.test_audit_runtime import config, camoufox_config, runtime  # noqa: F401
from tests.test_camoufox_direct_route import direct  # noqa: F401 - isolated account/scheduler fixture


CHILD = '''
import asyncio, json, os, sqlite3, sys, time
from tools.bound_proxy import BoundProxySession, initialize_bound_route
from tools.collection_route import CollectionRouteError

def forbidden(*args, **kwargs):
    raise AssertionError("unexpected echo or child database access")

clock = float(os.environ['TEST_CLOCK'])
time.time = lambda: clock
sqlite3.connect = forbidden
snapshot = initialize_bound_route()
session = BoundProxySession(snapshot, http_client_factory=forbidden)
session._browser_sample = forbidden
class Context:
    _navigation_policy_guard_ready = True
    _collection_native_echo_guard_url = os.environ["MEDIACRAWLER_PROXY_ECHO_URL"]

    async def close(self):
        pass

context = Context()

async def run():
    global clock
    # Compress the scheduling delay while advancing the application clock in
    # lockstep with the parent. Observe a real periodic heartbeat on each tick.
    heartbeat = asyncio.Event()
    validate_browser = session._validate_browser_safety_async
    async def observed_validation():
        await validate_browser()
        if asyncio.current_task() is session._periodic_task:
            heartbeat.set()
    session._validate_browser_safety_async = observed_validation
    session._next_probe_delay = lambda: 300 if heartbeat.is_set() else 0
    heartbeat.set()
    await session.open_context(context)
    assert session.receipt is None
    print(json.dumps({'context': session.context_id}), flush=True)
    try:
        for line in sys.stdin:
            command = json.loads(line)
            if command == 'close':
                break
            clock = command['time']
            try:
                if command.get('tick'):
                    session._periodic_task.cancel()
                    await asyncio.gather(session._periodic_task, return_exceptions=True)
                    heartbeat.clear()
                    session._periodic_task = asyncio.create_task(session._periodic_probe())
                    await asyncio.wait_for(heartbeat.wait(), timeout=4)
                await session._validate_browser_safety_async()
                async with session.slot('api', timeout=3):
                    source = await session.provenance_async(observation_id='fixture',
                        source_task_id='local-task', source_batch_id='batch')
                print(json.dumps({'source': source, 'version': session._lease_ack['lease_version']}), flush=True)
            except CollectionRouteError as error:
                print(json.dumps({'error': error.code}), flush=True)
    finally:
        await session.close()

asyncio.run(run())
'''


@contextmanager
def child_context(bundle, clock, log, *, drain=True):
    routes.prepare_collection_child(bundle)
    config = routes.RouteConfigPipe(bundle.snapshot)
    receipt = routes.RouteReceiptPipe(bundle)
    authority = authority_for(bundle)
    crawler = str(routes.settings.media_crawler_dir)
    env = {**os.environ, **routes.probe_env(bundle), **config.env, **receipt.env, **authority.env,
           'PYTHONPATH': crawler, 'MEDIACRAWLER_DY_BROWSER_ENGINE': 'camoufox',
           'MEDIACRAWLER_ACCOUNT_ID': bundle.account_id, 'TEST_CLOCK': str(clock)}
    with log.open('w') as stderr:
        proc = subprocess.Popen([sys.executable, '-c', CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=stderr, text=True, start_new_session=True, cwd=crawler, env=env,
            pass_fds=(*bundle.pass_fds, *config.pass_fds, *receipt.pass_fds, *authority.pass_fds))
        def exchange(command=None):
            if command is not None:
                proc.stdin.write(json.dumps(command) + '\n')
                proc.stdin.flush()
            assert select.select([proc.stdout], [], [], 8)[0], log.read_text()
            line = proc.stdout.readline()
            assert line, log.read_text()
            return json.loads(line)

        try:
            authority.bind_child(proc.pid)
            receipt.start(proc)
            config.start(proc)
            bundle.child_active = True
            assert exchange() == {'context': bundle.snapshot.context_id}
            yield exchange
            proc.stdin.write('"close"\n')
            proc.stdin.flush()
            assert proc.wait(timeout=8) == 0, log.read_text()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            bundle.child_active = False
            if drain:
                receipt.drain()
            else:
                # The collection adapter closes the pipe after each child; the
                # next collection's preparation owns the phase transition.
                receipt.close()
            authority.close()
            config.close()
            proc.stdin.close()
            proc.stdout.close()


def entry_environment(entry, config, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    if entry == 'local':
        config['settings'].pop('CRAWLER_LOCAL_DIRECT_PINNING_ENABLED', None)
        return runtime.environment(config)
    spec = importlib.util.spec_from_file_location('release_entry_fixture', root / 'deploy/camoufox/runtime-support/serve.py')
    serve = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serve)
    release = json.loads((root / 'deploy/camoufox/runtime.json.example').read_text())
    release['artifacts']['app']['path'] = str(root)
    release['runtime']['account_db'] = str(Path(config['runtime_root']) / 'release-data/accounts.sqlite3')
    release['environment']['XHS_AUDIT_OUTPUTS_DIR'] = str(Path(config['runtime_root']) / 'release-outputs')
    release['environment'].update(CRAWLER_PROXY_ENABLED='false', CRAWLER_SAFETY_ENABLED='true',
                                  CRAWLER_DIRECT_DEGRADE_ENABLED='false')
    release['environment'].pop('CRAWLER_LOCAL_DIRECT_PINNING_ENABLED', None)
    # Linux deployment asset validation is separately covered by release tests;
    # exercise the real configure environment replacement without deployment I/O.
    with monkeypatch.context() as patch:
        patch.setenv('CAMOUFOX_RELEASE_MANIFEST', '/fixture/release.json')
        patch.setattr(serve, 'read_json', lambda _: release)
        patch.setattr(serve, 'validate', lambda value: value)
        patch.setattr(serve, 'check_artifacts', lambda _: None)
        patch.setattr(os, 'environ', os.environ.copy())
        patch.setattr(os, 'chdir', lambda _: None)
        patch.setattr(sys, 'path', sys.path.copy())
        serve.configure()
        env = dict(os.environ)
    assert 'AUDIT_RUNTIME_MANIFEST' not in env
    return env


@pytest.mark.parametrize('entry', ['local', 'release'])
@pytest.mark.parametrize('old_transport_fault', [False, True])
@pytest.mark.parametrize('first_renewals', [0, 5])
def test_confirm_once_then_renew_collect_and_download_without_echo(direct, monkeypatch, tmp_path, old_transport_fault, entry, camoufox_config, first_renewals):
    from dataclasses import replace
    from backend.audit_agent import pipeline as pipeline_module
    from backend.audit_agent.job_store import JobStore
    from crawler_auth_fixtures import mark_camoufox_login_verified

    env = entry_environment(entry, camoufox_config, monkeypatch)
    assert 'CRAWLER_LOCAL_DIRECT_PINNING_ENABLED' not in env
    with monkeypatch.context() as patch:
        patch.setattr(os, 'environ', env)
        spec = importlib.util.spec_from_file_location('backend.audit_agent.entry_settings', Path(routes.__file__).with_name('config.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    for name in ('crawler_local_direct_pinning_enabled', 'crawler_proxy_enabled', 'crawler_safety_enabled', 'crawler_direct_degrade_enabled'):
        if hasattr(module.Settings, name):
            monkeypatch.setattr(routes.settings, name, getattr(module.Settings, name))
    accounts, account = direct
    accounts.save_auth_state(account['id'], 'fixture-ciphertext')
    mark_camoufox_login_verified(accounts, account['id'])
    jobs = JobStore(tmp_path / 'jobs.sqlite3')
    jobs.create(job_id='local-task', platform='dy', owner_user_id='owner')
    monkeypatch.setattr(pipeline_module, 'job_store', jobs)
    monkeypatch.setattr(pipeline_module, 'crawler_account_store', accounts)
    clock = [time.time()]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    for name in ('request_min_interval', 'crawler_account_min_interval', 'media_request_interval'):
        monkeypatch.setattr(routes.settings, name, 0)
    monkeypatch.setattr(routes.settings, 'task_resource_lock_dir', tmp_path / 'locks')
    monkeypatch.setattr(routes.settings, 'crawler_proxy_echo_url', 'https://echo.invalid/ip')
    routes.route_contract()
    from tools.bound_proxy import BoundProxySession, route_health_key

    if old_transport_fault:
        old_time = clock[0]
        generation = accounts.observe_shared_exit('203.0.113.9', observed_at=old_time,
                                                  service_exit=True)['shared_exit_generation']
        accounts.set_route_state(account['id'], state='active',
                                 shared_exit_key='203.0.113.9', generation=generation)
        clock[0] += routes.settings.crawler_exit_observation_ttl + 1
        old_snapshot = replace(routes.resolve_account_route(account['id'], account_store=accounts),
                               observed_exit_ip='203.0.113.9', observed_exit_at=old_time,
                               exit_generation=generation)
        with sqlite3.connect(old_snapshot.scheduler_db_path) as db:
            db.execute('CREATE TABLE collection_route_health (proxy_id TEXT, config_revision INTEGER, '
                       'stop_code TEXT, PRIMARY KEY(proxy_id, config_revision))')
            db.execute('INSERT INTO collection_route_health VALUES(?,?,?)',
                       (route_health_key(old_snapshot), old_snapshot.config_revision, 'PROXY_TRANSPORT_FAILED'))

    sends = []
    def startup_observation(bundle):
        assert not sends, 'fresh echo requested after bootstrap'
        session = BoundProxySession(bundle.snapshot, authority_owner=True, maintenance_only=True,
            echo_url=routes.settings.crawler_proxy_echo_url,
            budget_id=routes.probe_env(bundle)['MEDIACRAWLER_PROXY_PROBE_BUDGET_ID'])
        async def sample(kind):
            sends.append(kind)
            return '203.0.113.9'
        session._browser_sample = lambda: sample('browser')
        session._http_sample = lambda: sample('http')
        async def check():
            try:
                return await session.open_context(object())
            finally:
                await session.close()
        return asyncio.run(check())

    monkeypatch.setattr(routes, '_run_maintenance_probe', startup_observation)
    url = 'https://media.invalid/fixture'
    def send_media(request):
        assert str(request.url) == url, 'parent attempted an echo request'
        return httpx.Response(200, content=b'fixture media')
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient',
        lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(send_media)))
    pipeline = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id = 'local-task'
    pipeline._direct_egress_pin = routes.LocalDirectEgressPin(execution_id=pipeline.job_id, owner_id='owner')
    observed_at = clock[0]
    contexts = set()
    operations = set()
    second = accounts.create(platform='dy', display_name='rotated')
    accounts.save_auth_state(second['id'], 'second-fixture-ciphertext')
    mark_camoufox_login_verified(accounts, second['id'])
    for batch in range(2):
        if batch == 1:
            account = second
        operation = (pipeline._route_operation(account['id']) if batch == 0 else
                     pipeline._geographic_route_operation('dy', account['id'], {account['id']}))
        with operation as bundle:
            snap = bundle.snapshot
            assert snap.local_direct_pinned and snap.observed_exit_at == observed_at
            if old_transport_fault:
                with sqlite3.connect(snap.scheduler_db_path) as db:
                    assert db.execute('SELECT stop_code FROM collection_route_health WHERE proxy_id=?',
                                      (route_health_key(snap),)).fetchone() is None
            contexts.add(snap.context_id)
            operations.add(snap.operation_id)
            with child_context(bundle, clock[0], tmp_path / f'child-{batch}.log', drain=False) as exchange:
                first_pid = bundle.child_receipt_pipe.process.pid
                with pytest.raises(routes.CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
                    routes.prepare_collection_child(bundle)
                assert bundle.child_receipt_pipe.process.poll() is None
                assert bundle.snapshot == snap
                source = exchange({'time': clock[0]})['source']
                for renewal in range(first_renewals if batch == 0 else 1):
                    clock[0] += 275
                    result = exchange({'time': clock[0], 'tick': True})
                    assert result['version'] == renewal + 2
                    source = result['source']
                    assert source['observed_exit_at'] == observed_at
                    assert source['operation_id'] == snap.operation_id
                    assert source['source_account_id'] == account['id']
                with routes.authorize_source_route(source, 'owner', 'local-task', route_context=bundle,
                                                  account_store=accounts) as authorized:
                    chunks = []
                    status, _ = routes.download_asset_hop(authorized, url, {}, 5, True, chunks.append)
                    assert status == 200 and b''.join(chunks) == b'fixture media'
                with closing(sqlite3.connect(snap.scheduler_db_path)) as db:
                    charged = dict(db.execute(
                        "SELECT platform,last_start FROM request_scheduler_channels WHERE channel='media'"))
                    assert {scope: charged[scope] for scope in snap.scope_ids} == dict.fromkeys(snap.scope_ids, clock[0])
                    assert db.execute('SELECT count(*) FROM request_scheduler_leases').fetchone() == (0,)
            with bundle.borrow():
                with pytest.raises(routes.CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
                    routes.prepare_collection_child(bundle)
                assert bundle.snapshot == snap
            routes.prepare_collection_child(bundle)
            prepared = bundle.snapshot
            routes.prepare_collection_child(bundle)
            assert bundle.snapshot == prepared  # Repeated preparation must not reissue.
            with child_context(bundle, clock[0], tmp_path / f'detail-{batch}.log', drain=False) as exchange:
                detail = bundle.snapshot
                assert detail.operation_id != snap.operation_id
                assert detail.lease_id != snap.lease_id and detail.context_id != snap.context_id
                assert detail.observed_exit_at == snap.observed_exit_at == observed_at
                assert detail.observed_exit_ip == snap.observed_exit_ip
                assert detail.exit_generation == snap.exit_generation
                assert detail.account_id == snap.account_id and detail.profile_key == snap.profile_key
                assert bundle.child_receipt_pipe.process.pid != first_pid
                assert exchange({'time': clock[0]})['source']['operation_id'] == detail.operation_id
                with closing(sqlite3.connect(detail.scheduler_db_path)) as db:
                    assert db.execute('SELECT state FROM route_operations WHERE operation_id=?',
                                      (snap.operation_id,)).fetchone() == ('ended',)
                    assert db.execute('SELECT lease_version FROM route_operations WHERE operation_id=?',
                                      (detail.operation_id,)).fetchone() == (1,)
                if batch == 1:
                    accounts.pause_account_exit(account['id'], until=clock[0] + 60)
                    assert 'error' in exchange({'time': clock[0]})
            if batch == 1:
                with pytest.raises(routes.CollectionRouteError, match='PROXY_REVISION_MISMATCH'):
                    routes.prepare_collection_child(bundle)
                assert bundle.snapshot == detail  # No new operation for a paused account.

    if first_renewals:
        assert clock[0] - observed_at >= 20 * 60
    assert len(contexts) == len(operations) == 2
    assert sends == ['browser', 'http', 'browser', 'http']
