"""Exercise reserve through its private socket with production SQLite journal modes."""
from contextlib import ExitStack, closing
from dataclasses import asdict
import json
import logging
import os
import sqlite3
import threading
import time

import pytest

from backend.audit_agent import crawler_request_authority as authority_module
from backend.audit_agent import crawler_route as routes
from backend.audit_agent.crawler_child_runtime import authority_for
from tests.test_camoufox_direct_route import direct  # noqa: F401 - pytest fixture


REQUEST_ID = 'b' * 32
MEDIA = {'operation': 'media', 'lease_seconds': 60}


@pytest.fixture
def managed(direct, tmp_path, monkeypatch):
    store, account = direct
    monkeypatch.setattr(routes.settings, 'task_resource_lock_dir', tmp_path / 'locks')
    contract = routes.route_contract()
    from tools.request_authority import AuthorityClient, ENV_FD

    ip = '203.0.113.9'
    monkeypatch.setattr(routes, '_run_maintenance_probe', lambda bundle: contract.ProbeReceipt(
        'service-direct', 1, 'probe', ip, time.time()))
    store.save_auth_state(account['id'], 'fixture-encrypted-state')
    with closing(sqlite3.connect(store.db_path)) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
    with ExitStack() as cleanup:
        bundle = routes.acquire_route_execution(account['id'], 'busy-test', 'owner', account_store=store)
        cleanup.callback(bundle.close)
        route = bundle.snapshot
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
        receipt = contract.ProbeReceipt(route.proxy_id, route.config_revision, route.context_id,
                                       ip, time.time(), route.exit_generation)
        client.call('make_gate', {'receipt': asdict(receipt)})
        yield route, client, owner


@pytest.fixture
def retry_seen(caplog):
    """Release the real lock only after SQLite has actually timed out once."""
    event = threading.Event()
    logger = logging.getLogger(authority_module.__name__)

    class RetryObserver(logging.Handler):
        def emit(self, record):
            if 'action=retry' in record.getMessage():
                event.set()

    handler = RetryObserver()
    logger.addHandler(handler)
    try:
        with caplog.at_level(logging.INFO, logger=logger.name):
            yield event
    finally:
        logger.removeHandler(handler)


def assert_accounting(route, *, granted):
    count = int(granted)
    with closing(sqlite3.connect(route.scheduler_db_path)) as db:
        assert db.execute('SELECT count(*) FROM crawler_authority_ledger WHERE request_id=?',
                          (REQUEST_ID,)).fetchone()[0] == count
        for table in ('request_scheduler_leases', 'crawler_authority_process_leases'):
            assert db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 4 * count
        states = db.execute('SELECT platform, request_times_json FROM request_scheduler_state').fetchall()
        charged = {scope: len(json.loads(times)) for scope, times in states if json.loads(times)}
        assert charged == ({scope: 1 for scope in route.scope_ids} if granted else {})


def test_safety_database_writer_releases_same_request_without_double_charge(managed, retry_seen):
    route, client, _ = managed
    with closing(sqlite3.connect(route.safety_db_path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        pending = client.submit('reserve', MEDIA, request_id=REQUEST_ID)
        assert retry_seen.wait(3)
        writer.rollback()

    grant = pending.result(4)
    assert grant['delay'] == 0 and set(grant['scope_tokens']) == set(route.scope_ids)
    assert client.call('reserve', MEDIA, request_id=REQUEST_ID) == grant
    assert_accounting(route, granted=True)


def test_busy_commit_rolls_back_written_debits_before_replay(managed, retry_seen, monkeypatch, caplog):
    """A DELETE-mode reader allows writes to start but blocks COMMIT."""
    from tools import persistent_request_gate

    route, client, _ = managed
    entered = threading.Event()
    reader_ready = threading.Event()
    original = persistent_request_gate._try_reserve_scopes

    def wait_for_reader(*args, **kwargs):
        entered.set()
        assert reader_ready.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(persistent_request_gate, '_try_reserve_scopes', wait_for_reader)
    with closing(sqlite3.connect(route.scheduler_db_path)) as reader:
        pending = client.submit('reserve', MEDIA, request_id=REQUEST_ID)
        try:
            assert entered.wait(2)
            reader.execute('BEGIN')
            reader.execute('SELECT count(*) FROM request_scheduler_leases').fetchone()
        finally:
            reader_ready.set()
        assert retry_seen.wait(3)
        reader.rollback()
    grant = pending.result(4)
    assert grant['delay'] == 0
    assert 'step=commit' in caplog.text
    assert client.call('reserve', MEDIA, request_id=REQUEST_ID) == grant
    assert_accounting(route, granted=True)


def test_persistent_lock_returns_explicit_error_without_charging(managed, retry_seen):
    from tools.collection_route import CollectionRouteError

    route, client, _ = managed
    with closing(sqlite3.connect(route.safety_db_path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        with pytest.raises(CollectionRouteError, match='SCHEDULER_DATABASE_BUSY'):
            client.call('reserve', MEDIA, request_id=REQUEST_ID)
        assert retry_seen.is_set()
    assert_accounting(route, granted=False)


def test_pause_committed_while_waiting_prevents_grant(managed, retry_seen):
    from tools.collection_route import CollectionRouteError

    route, client, _ = managed
    with closing(sqlite3.connect(route.safety_db_path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        changed = writer.execute('UPDATE crawler_shared_exits SET paused_until=? WHERE exit_key=?',
                                 (time.time() + 300, route.observed_exit_ip))
        assert changed.rowcount == 1
        pending = client.submit('reserve', MEDIA, request_id=REQUEST_ID)
        assert retry_seen.wait(3)
        writer.commit()
    with pytest.raises(CollectionRouteError, match='PROXY_UNAVAILABLE|ACCOUNT_CHALLENGE'):
        pending.result(4)
    assert_accounting(route, granted=False)


def test_missing_table_fails_without_retry(managed, retry_seen, caplog):
    from tools.collection_route import CollectionRouteError

    route, client, _ = managed
    with closing(sqlite3.connect(route.scheduler_db_path)) as db:
        db.execute('DROP TABLE request_scheduler_channels')
    with pytest.raises(CollectionRouteError, match='PROXY_UNAVAILABLE'):
        client.call('reserve', MEDIA, request_id=REQUEST_ID)
    assert not retry_seen.is_set()
    assert 'sqlite_code=1 retries=0' in caplog.text
    assert_accounting(route, granted=False)


def test_cancel_during_backoff_stops_owner_without_issuing_grant(managed, retry_seen):
    from tools.collection_route import CollectionRouteError

    route, client, owner = managed
    with closing(sqlite3.connect(route.safety_db_path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        pending = client.submit('reserve', MEDIA, request_id=REQUEST_ID)
        assert retry_seen.wait(3)
        owner.close()
        writer.rollback()

    with pytest.raises(CollectionRouteError):
        pending.result(2)
    owner.thread.join(timeout=2)
    assert not owner.thread.is_alive()
    assert_accounting(route, granted=False)


def test_busy_with_unknown_commit_outcome_is_not_replayed(managed, retry_seen, monkeypatch):
    from tools import persistent_request_gate
    from tools.collection_route import CollectionRouteError

    _, client, _ = managed
    calls = []

    def uncertain(*args, **kwargs):
        calls.append(kwargs['authority']['request_id'])
        error = sqlite3.OperationalError('ambiguous commit outcome')
        error.sqlite_errorcode = sqlite3.SQLITE_BUSY
        error.reservation_step = 'commit'
        error.reservation_rolled_back = False
        raise error

    monkeypatch.setattr(persistent_request_gate, '_try_reserve_scopes', uncertain)
    with pytest.raises(CollectionRouteError, match='SCHEDULER_DATABASE_BUSY'):
        client.call('reserve', MEDIA, request_id=REQUEST_ID)
    assert calls == [REQUEST_ID]
    assert not retry_seen.is_set()
