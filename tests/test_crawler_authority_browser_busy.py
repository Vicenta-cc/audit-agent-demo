"""Real private-channel browser checks against contended temporary SQLite stores."""
from contextlib import closing
import sqlite3
import time

import pytest

from tests.test_crawler_authority_database_busy import assert_accounting, managed, retry_seen  # noqa: F401
from tests.test_crawler_authority_database_busy import recovering_call
from tests.test_camoufox_direct_route import direct  # noqa: F401


@pytest.mark.parametrize('database', ['safety_db_path', 'scheduler_db_path'])
def test_browser_waits_for_writer_then_revalidates(managed, retry_seen, database):
    route, client, _ = managed
    with closing(sqlite3.connect(getattr(route, database))) as writer:
        writer.execute('BEGIN IMMEDIATE')
        pending = recovering_call(client, 'browser_validate')
        assert retry_seen.wait(3)
        writer.rollback()
    assert pending.result(4) is None
    assert_accounting(route, granted=False)


@pytest.mark.parametrize('database', ['safety_db_path', 'scheduler_db_path'])
def test_browser_persistent_lock_is_bounded(managed, retry_seen, database):
    from tools.collection_route import CollectionRouteError
    route, client, _ = managed
    started = time.monotonic()
    with closing(sqlite3.connect(getattr(route, database))) as writer:
        writer.execute('BEGIN IMMEDIATE')
        with pytest.raises(CollectionRouteError, match='SCHEDULER_DATABASE_BUSY'):
            client.submit('browser_validate').result(2)
    assert retry_seen.is_set()
    assert time.monotonic() - started < 3


def change_safety(db, route, change):
    if change == 'pause':
        result = db.execute('UPDATE crawler_shared_exits SET paused_until=? WHERE exit_key=?',
                            (time.time() + 300, route.observed_exit_ip))
    elif change == 'generation':
        result = db.execute('UPDATE crawler_shared_exits SET generation=generation+1 WHERE exit_key=?',
                            (route.observed_exit_ip,))
    else:
        result = db.execute("UPDATE crawler_route_admissions SET state='revoked' WHERE operation_id=?",
                            (route.operation_id,))
    assert result.rowcount == 1


@pytest.mark.parametrize('change', ['pause', 'generation', 'revoke'])
def test_browser_rechecks_safety_after_wait(managed, retry_seen, change):
    from tools.collection_route import CollectionRouteError
    route, client, _ = managed
    with closing(sqlite3.connect(route.safety_db_path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        change_safety(writer, route, change)
        pending = recovering_call(client, 'browser_validate')
        assert retry_seen.wait(3)
        writer.commit()
    with pytest.raises(CollectionRouteError, match='PROXY_UNAVAILABLE|ACCOUNT_CHALLENGE'):
        pending.result(4)


@pytest.mark.parametrize('change', ['pause', 'generation', 'revoke'])
def test_browser_request_id_never_reuses_old_safety_decision(managed, change):
    from tools.collection_route import CollectionRouteError
    route, client, _ = managed
    request_id = 'c' * 32
    assert client.call('browser_validate', request_id=request_id) is None
    with closing(sqlite3.connect(route.safety_db_path)) as writer:
        change_safety(writer, route, change)
        writer.commit()
    with pytest.raises(CollectionRouteError, match='PROXY_UNAVAILABLE|ACCOUNT_CHALLENGE'):
        client.call('browser_validate', request_id=request_id)


def test_browser_cancel_during_wait_stops_channel(managed, retry_seen):
    from tools.collection_route import CollectionRouteError
    route, client, owner = managed
    with closing(sqlite3.connect(route.safety_db_path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        pending = recovering_call(client, 'browser_validate')
        assert retry_seen.wait(3)
        owner.close()
        writer.rollback()
    with pytest.raises(CollectionRouteError):
        pending.result(2)
    owner.thread.join(timeout=2)
    assert not owner.thread.is_alive()


def test_browser_missing_table_is_not_retried(managed, retry_seen):
    from tools.collection_route import CollectionRouteError
    route, client, _ = managed
    with closing(sqlite3.connect(route.scheduler_db_path)) as db:
        db.execute('DROP TABLE request_scheduler_state')
    with pytest.raises(CollectionRouteError, match='PROXY_UNAVAILABLE'):
        client.call('browser_validate')
    assert not retry_seen.is_set()


def test_browser_unknown_rollback_is_not_retried(managed, retry_seen, monkeypatch):
    from tools.collection_route import CollectionRouteError
    _, client, owner = managed
    calls = []
    def uncertain():
        calls.append(True)
        error = sqlite3.OperationalError('unknown outcome')
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        error.reservation_step = 'commit'
        error.reservation_rolled_back = False
        raise error
    monkeypatch.setattr(owner.session, '_validate_browser_safety', uncertain)
    with pytest.raises(CollectionRouteError, match='SCHEDULER_DATABASE_BUSY'):
        client.call('browser_validate')
    assert calls == [True]
    assert not retry_seen.is_set()


def test_browser_deadline_headroom_prevents_extra_attempt(managed, retry_seen, monkeypatch):
    from tools import request_authority
    from tools.collection_route import CollectionRouteError
    _, client, owner = managed
    calls = []
    def busy():
        calls.append(True)
        error = sqlite3.OperationalError('fixture busy')
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        error.reservation_step = 'begin'
        error.reservation_rolled_back = True
        raise error
    # Still a valid channel deadline, but insufficient for another DB attempt.
    monkeypatch.setattr(request_authority, 'TIMEOUT', .4)
    monkeypatch.setattr(owner.session, '_validate_browser_safety', busy)
    with pytest.raises(CollectionRouteError, match='SCHEDULER_DATABASE_BUSY'):
        client.submit('browser_validate').result(2)
    assert calls == [True]
    assert retry_seen.is_set()


def test_browser_reused_id_different_payload_is_rejected(managed):
    from tools.collection_route import CollectionRouteError
    _, client, _ = managed
    request_id = 'd' * 32
    client.call('browser_validate', request_id=request_id)
    with pytest.raises(CollectionRouteError, match='PROXY_REVISION_MISMATCH'):
        client.call('browser_validate', {'unexpected': True}, request_id=request_id)


@pytest.mark.parametrize('end', ['deadline', 'closed', 'expired'])
def test_browser_checks_lifetime_after_database_returns(managed, monkeypatch, end):
    from dataclasses import replace
    from backend.audit_agent import crawler_request_authority as authority_module
    _, _, owner = managed
    now = time.time()
    def validation():
        if end == 'deadline':
            monkeypatch.setattr(authority_module.time, 'time', lambda: now + 2)
        elif end == 'closed':
            owner.closed.set()
        else:
            owner.snapshot = replace(owner.snapshot, expires_at=now - 1)
    monkeypatch.setattr(owner.session, '_validate_browser_safety', validation)
    with pytest.raises((RuntimeError, TimeoutError)):
        owner._validate_browser({'deadline': now + 1})


def test_browser_sqlite_diagnostics_redact_exception_text(managed, monkeypatch, caplog):
    from tools.collection_route import CollectionRouteError
    _, client, owner = managed
    def invalid_database():
        error = sqlite3.OperationalError('SECRET_cookie_and_query_text')
        error.sqlite_errorcode = sqlite3.SQLITE_ERROR
        error.sqlite_errorname = 'SQLITE_ERROR'
        error.reservation_step = 'body'
        error.reservation_rolled_back = True
        raise error
    monkeypatch.setattr(owner.session, '_validate_browser_safety', invalid_database)
    with pytest.raises(CollectionRouteError):
        client.call('browser_validate')
    assert 'SECRET_cookie_and_query_text' not in caplog.text
    assert 'CRAWLER_BROWSER_DATABASE' in caplog.text
    assert 'sqlite_code=1' in caplog.text
    assert 'sqlite_name=SQLITE_ERROR' in caplog.text
    assert 'step=body' in caplog.text


@pytest.fixture
def pinned(direct, tmp_path, monkeypatch):
    """Production investigation lane: live owner pin, without network bootstrap."""
    from contextlib import ExitStack
    import os
    from backend.audit_agent import crawler_route as routes
    from backend.audit_agent.crawler_child_runtime import authority_for
    contract = routes.route_contract()
    from tools.request_authority import AuthorityClient, ENV_FD
    store, account = direct
    monkeypatch.setattr(routes.settings, 'task_resource_lock_dir', tmp_path / 'locks')
    monkeypatch.setattr(routes.settings, 'crawler_local_direct_pinning_enabled', True)
    monkeypatch.setattr(routes, '_run_maintenance_probe', lambda bundle: contract.ProbeReceipt(
        'service-direct', 1, 'bootstrap', '203.0.113.9', time.time()))
    store.save_auth_state(account['id'], 'fixture-encrypted-state')
    with closing(sqlite3.connect(store.db_path)) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
    with ExitStack() as cleanup:
        pin = routes.LocalDirectEgressPin(execution_id='pinned-busy', owner_id='owner')
        bundle = routes.acquire_route_execution(account['id'], 'pinned-busy', 'owner',
            account_store=store, investigation_collection=True, direct_egress_pin=pin)
        cleanup.callback(bundle.close)
        route = bundle.snapshot
        assert route.local_direct_pinned
        with closing(sqlite3.connect(route.scheduler_db_path)) as db:
            assert db.execute('PRAGMA journal_mode').fetchone() == ('delete',)
        bundle.route_operation_store.ack(route.operation_id, owner_id=route.owner_id,
            lease_version=route.lease_version, snapshot_digest=route.snapshot_digest,
            context_id=route.context_id)
        owner = authority_for(bundle)
        cleanup.callback(owner.close)
        env = dict(owner.env)
        env[ENV_FD] = str(os.dup(int(env[ENV_FD])))
        owner.bind_child(os.getpid())
        client = AuthorityClient(route, env=env)
        cleanup.callback(client.close)
        client.call('initialize')
        client.call('authorize_pinned_context')
        yield route, client, owner


@pytest.mark.parametrize('database', ['safety_db_path', 'scheduler_db_path'])
def test_pinned_browser_writer_release_preserves_context(pinned, retry_seen, database):
    route, client, _ = pinned
    with closing(sqlite3.connect(getattr(route, database))) as writer:
        writer.execute('BEGIN IMMEDIATE')
        pending = recovering_call(client, 'browser_validate')
        assert retry_seen.wait(3)
        writer.rollback()
    assert pending.result(4) is None
    assert_accounting(route, granted=False)


@pytest.mark.parametrize(('field', 'value'), [
    ('status', 'paused'), ('route_state', 'paused'),
    ('paused_until', 9999999999), ('binding_revision', 999),
])
def test_pinned_browser_rejects_account_change_during_busy(pinned, retry_seen, field, value):
    from tools.collection_route import CollectionRouteError
    route, client, _ = pinned
    with closing(sqlite3.connect(route.safety_db_path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        changed = writer.execute(f'UPDATE crawler_accounts SET {field}=? WHERE id=?',
                                 (value, route.account_id))
        assert changed.rowcount == 1
        pending = recovering_call(client, 'browser_validate')
        assert retry_seen.wait(3)
        writer.commit()
    with pytest.raises(CollectionRouteError, match='PROXY_UNAVAILABLE|ACCOUNT_CHALLENGE'):
        pending.result(4)
    assert_accounting(route, granted=False)


def test_pinned_browser_reused_id_rejects_closed_context(pinned):
    from tools.collection_route import CollectionRouteError
    _, client, _ = pinned
    request_id = 'e' * 32
    assert client.call('browser_validate', request_id=request_id) is None
    client.call('close_pinned_context')
    with pytest.raises(CollectionRouteError, match='PROXY_UNAVAILABLE'):
        client.call('browser_validate', request_id=request_id)
