"""Logical-clock long runs over real private FDs and temporary SQLite stores.

Platform traffic and startup exit observation are fixtures, not real captures.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import sqlite3
import threading
import time

import pytest

from tests.test_camoufox_direct_route import direct  # noqa: F401
from tests.test_crawler_authority_browser_busy import pinned  # noqa: F401
from tests.test_crawler_authority_auth_read import required  # noqa: F401


@contextmanager
def short_safety_lock(path):
    with closing(sqlite3.connect(path, check_same_thread=False)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        errors = []

        def release():
            try:
                writer.rollback()
            except BaseException as exc:
                errors.append(exc)

        timer = threading.Timer(.35, release)
        timer.start()
        try:
            yield
        finally:
            timer.join(2)
            assert not timer.is_alive()
            writer.rollback()
            assert not errors


@pytest.mark.parametrize('authorization', ['pinned', 'required'], ids=['local-auth', 'required-auth'])
def test_twenty_renewal_cycles_with_concurrent_waiters(request, monkeypatch, authorization):
    route, client, owner = request.getfixturevalue(authorization)[:3]
    clock = [time.time()]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    for cycle in range(1, 21):
        clock[0] += 275
        with short_safety_lock(route.safety_db_path), ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: client.call('browser_validate'), range(8)))
        assert results == [None] * 8
        ack = client.call('check_stop')['ack']
        assert ack['lease_version'] == cycle + 1
        assert ack['expires_at'] > clock[0]
        assert owner.session._lease_ack == ack
        assert owner.session.snapshot == route
        assert not client.closed
        with closing(sqlite3.connect(route.scheduler_db_path)) as db:
            assert db.execute('SELECT count(*) FROM route_lease_commands').fetchone() == (cycle,)


@pytest.mark.parametrize('geographic', [False, True], ids=['keyword', 'geographic'])
def test_both_entries_renew_after_child_handoff_with_contention(direct, tmp_path, monkeypatch, geographic):
    from backend.audit_agent import crawler_route as routes, pipeline as pipeline_module
    from backend.audit_agent.job_store import JobStore
    from crawler_auth_fixtures import mark_camoufox_login_verified
    from tests.test_local_direct_pinning_integration import child_context

    accounts, account = direct
    accounts.save_auth_state(account['id'], 'fixture-ciphertext')
    mark_camoufox_login_verified(accounts, account['id'])
    jobs = JobStore(tmp_path / 'jobs.sqlite3')
    jobs.create(job_id='local-task', platform='dy', owner_user_id='owner')
    monkeypatch.setattr(pipeline_module, 'job_store', jobs)
    monkeypatch.setattr(pipeline_module, 'crawler_account_store', accounts)
    monkeypatch.setattr(routes.settings, 'task_resource_lock_dir', tmp_path / 'locks')
    monkeypatch.setattr(routes.settings, 'crawler_local_direct_pinning_enabled', True)
    for name in ('request_min_interval', 'crawler_account_min_interval', 'media_request_interval'):
        monkeypatch.setattr(routes.settings, name, 0)
    clock = [time.time()]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    contract = routes.route_contract()
    monkeypatch.setattr(routes, '_run_maintenance_probe', lambda bundle: contract.ProbeReceipt(
        'service-direct', 1, 'bootstrap', '203.0.113.9', time.time()))
    pipeline = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id = 'local-task'
    pipeline._direct_egress_pin = routes.LocalDirectEgressPin(execution_id='local-task', owner_id='owner')
    operation = (pipeline._geographic_route_operation('dy', account['id'], {account['id']})
                 if geographic else pipeline._route_operation(account['id']))
    snapshots = []
    with operation as bundle:
        for phase in range(2):
            with child_context(bundle, clock[0], tmp_path / f'phase-{phase}.log', drain=False) as exchange:
                snap = bundle.snapshot
                snapshots.append(snap)
                for renewal in range(1, 4):
                    clock[0] += 275
                    with short_safety_lock(snap.safety_db_path):
                        result = exchange({'time': clock[0], 'tick': True})
                    assert result['version'] == renewal + 1
                    source = result['source']
                    assert source['operation_id'] == snap.operation_id
                    assert source['observed_exit_ip'] == snap.observed_exit_ip
                    assert source['observed_exit_at'] == snapshots[0].observed_exit_at
                    assert source['exit_generation'] == snap.exit_generation
                    with closing(sqlite3.connect(snap.scheduler_db_path)) as db:
                        assert db.execute('SELECT count(*) FROM route_lease_commands').fetchone() == (phase * 3 + renewal,)
        assert snapshots[0].operation_id != snapshots[1].operation_id
        assert snapshots[0].context_id != snapshots[1].context_id
        assert snapshots[0].lease_id != snapshots[1].lease_id
        assert snapshots[0].observed_exit_ip == snapshots[1].observed_exit_ip
        assert snapshots[0].exit_generation == snapshots[1].exit_generation
