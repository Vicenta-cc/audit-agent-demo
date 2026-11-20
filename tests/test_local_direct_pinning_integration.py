"""Local investigation pin across two real FD workers. All network sends are fixtures."""
import asyncio
from contextlib import closing, contextmanager
import json
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
def child_context(bundle, clock, log):
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
            receipt.drain()
            authority.close()
            config.close()
            proc.stdin.close()
            proc.stdout.close()


def test_confirm_once_then_renew_collect_and_download_without_echo(direct, monkeypatch, tmp_path):
    from backend.audit_agent import pipeline as pipeline_module
    from backend.audit_agent.job_store import JobStore
    from crawler_auth_fixtures import mark_camoufox_login_verified

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
    monkeypatch.setattr(routes.settings, 'crawler_local_direct_pinning_enabled', True)
    monkeypatch.setattr(routes.settings, 'crawler_proxy_echo_url', 'https://echo.invalid/ip')
    routes.route_contract()
    from tools.bound_proxy import BoundProxySession

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
    for batch in range(2):
        operation = (pipeline._route_operation(account['id']) if batch == 0 else
                     pipeline._geographic_route_operation('dy', account['id'], {account['id']}))
        with operation as bundle:
            snap = bundle.snapshot
            assert snap.local_direct_pinned and snap.observed_exit_at == observed_at
            contexts.add(snap.context_id)
            operations.add(snap.operation_id)
            with child_context(bundle, clock[0], tmp_path / f'child-{batch}.log') as exchange:
                for renewal in range(5 if batch == 0 else 1):
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
                    assert charged == dict.fromkeys(snap.scope_ids, clock[0])
                    assert db.execute('SELECT count(*) FROM request_scheduler_leases').fetchone() == (0,)
                if batch == 1:
                    accounts.pause_account_exit(account['id'], until=clock[0] + 60)
                    assert 'error' in exchange({'time': clock[0]})

    assert clock[0] - observed_at >= 20 * 60
    assert len(contexts) == len(operations) == 2
    assert sends == ['browser', 'http', 'browser', 'http']
