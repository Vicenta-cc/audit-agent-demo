"""Offline real socket/SQLite contention across recovery, revocation and renewal."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
import sqlite3
import threading
import time

import pytest

from tests.test_camoufox_direct_route import direct  # noqa: F401
from tests.test_crawler_authority_browser_busy import pinned  # noqa: F401


@contextmanager
def pending_commit(path, hold=1.2):
    """Ordinary DELETE-mode COMMIT blocked by a reader, released independently."""
    reader = sqlite3.connect(path, check_same_thread=False)
    reader.execute('BEGIN')
    reader.execute('SELECT * FROM request_scheduler_state').fetchall()
    errors = []
    committing = threading.Event()
    def write():
        try:
            with closing(sqlite3.connect(path, timeout=4)) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('UPDATE request_scheduler_state SET cooldown_until=cooldown_until')
                committing.set()
                db.commit()
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=write)
    worker.start()
    timer = None
    try:
        assert committing.wait(2)
        deadline = time.monotonic() + 2
        while True:
            try:
                with closing(sqlite3.connect(path, timeout=0)) as probe:
                    probe.execute('SELECT * FROM request_scheduler_state').fetchall()
            except sqlite3.OperationalError as exc:
                assert exc.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
                break
            assert time.monotonic() < deadline
            time.sleep(.005)
        def release():
            reader.rollback()
        timer = threading.Timer(hold, release)
        timer.start()
        yield
    finally:
        if timer is not None:
            timer.join(hold + 2)
        reader.rollback()
        reader.close()
        worker.join(5)
        assert not worker.is_alive()
        assert not errors


@pytest.mark.parametrize('count', [1, 8, 32])
def test_pending_commit_recovers_without_closing_channel(pinned, count):
    route, client, _ = pinned
    with pending_commit(route.scheduler_db_path), ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(lambda _: client.call('browser_validate'), range(count)))
    assert results == [None] * count
    assert not client.closed


def test_concurrent_persistent_busy_preserves_cause_and_channel(pinned):
    from tools.collection_route import CollectionRouteError
    route, client, _ = pinned
    def validate(_):
        try:
            client.call('browser_validate')
        except CollectionRouteError as exc:
            return exc.code
        pytest.fail('validation authorized while safety database remained locked')
    with closing(sqlite3.connect(route.safety_db_path)) as writer:
        writer.execute('BEGIN IMMEDIATE')
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(validate, range(32)))
        assert results == ['SCHEDULER_DATABASE_BUSY'] * 32
        assert time.monotonic() - started < 11
        assert not client.closed
        writer.rollback()
    # Exhausting one recovery budget must not corrupt the reusable channel.
    assert client.call('browser_validate') is None


@pytest.mark.parametrize('change', ['pause', 'revoke', 'generation'])
def test_pending_commit_does_not_hide_safety_change(pinned, change):
    from tools.collection_route import CollectionRouteError
    route, client, _ = pinned
    with pending_commit(route.scheduler_db_path):
        with closing(sqlite3.connect(route.safety_db_path)) as db:
            if change == 'pause':
                db.execute("UPDATE crawler_accounts SET status='paused' WHERE id=?", (route.account_id,))
            elif change == 'revoke':
                db.execute("UPDATE crawler_route_admissions SET state='ended' WHERE operation_id=?", (route.operation_id,))
            else:
                db.execute('UPDATE crawler_shared_exits SET generation=generation+1 WHERE exit_key=?', (route.observed_exit_ip,))
            db.commit()
        with pytest.raises(CollectionRouteError):
            client.call('browser_validate')
    assert not client.closed


def test_renewal_then_pending_read_preserves_one_new_ticket(pinned, monkeypatch):
    route, client, owner = pinned
    original = owner.session._lease_renewer
    clock = time.time() + 275
    monkeypatch.setattr(time, 'time', lambda: clock)
    calls = []
    managers = []
    def renew(prior):
        ticket = original(prior)
        calls.append(ticket)
        manager = pending_commit(route.scheduler_db_path)
        manager.__enter__()
        managers.append(manager)
        return ticket
    monkeypatch.setattr(owner.session, '_lease_renewer', renew)
    try:
        assert client.call('browser_validate') is None
        assert len(calls) == 1
        assert owner.session._lease_ack['lease_version'] == 2
        with closing(sqlite3.connect(route.scheduler_db_path)) as db:
            assert db.execute('SELECT count(*) FROM route_lease_commands').fetchone()[0] == 1
        assert owner.session.snapshot.observed_exit_ip == route.observed_exit_ip
        assert owner.session.snapshot.exit_generation == route.exit_generation
    finally:
        for manager in managers:
            manager.__exit__(None, None, None)


def test_persistent_pending_read_respects_recovery_budget(pinned, monkeypatch):
    from tools import request_authority
    from tools.collection_route import CollectionRouteError
    route, client, _ = pinned
    monkeypatch.setattr(request_authority, 'DATABASE_RECOVERY_SECONDS', .65)
    with pending_commit(route.scheduler_db_path, hold=1.4):
        start = time.monotonic()
        with pytest.raises(CollectionRouteError, match='SCHEDULER_DATABASE_BUSY'):
            client.call('browser_validate')
        assert time.monotonic() - start < 1.2


def test_close_during_pending_read_does_not_recover(pinned):
    from tools.collection_route import CollectionRouteError
    route, client, owner = pinned
    with pending_commit(route.scheduler_db_path), ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.call, 'browser_validate')
        owner.close()
        with pytest.raises(CollectionRouteError):
            pending.result(3)


@contextmanager
def safety_writer(route, *, pause=False):
    ready = threading.Event()
    errors = []
    def write():
        try:
            with closing(sqlite3.connect(route.safety_db_path)) as db:
                db.execute('BEGIN IMMEDIATE')
                if pause:
                    db.execute("UPDATE crawler_accounts SET status='paused' WHERE id=?", (route.account_id,))
                ready.set()
                time.sleep(1.2)
                db.commit()
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=write)
    worker.start()
    try:
        assert ready.wait(2)
        yield
    finally:
        worker.join(4)
        assert not worker.is_alive() and not errors


def accounting_snapshot(route):
    with closing(sqlite3.connect(route.scheduler_db_path)) as db:
        return db.execute('SELECT platform,request_times_json FROM request_scheduler_state ORDER BY platform').fetchall()


def test_consume_and_media_renewal_recover_without_second_charge(pinned):
    route, client, _ = pinned
    grant = client.call('reserve', {'operation': 'media', 'lease_seconds': 90})
    assert grant['delay'] == 0
    charged = accounting_snapshot(route)
    with safety_writer(route):
        assert client.call('consume', {'grant': grant['grant']}) is None
    assert accounting_snapshot(route) == charged
    with pending_commit(route.scheduler_db_path):
        renewed = client.call('renew_request', {'scope_tokens': grant['scope_tokens']})
    assert renewed['expires_at'] > time.time()
    assert accounting_snapshot(route) == charged
    with closing(sqlite3.connect(route.scheduler_db_path)) as db:
        assert db.execute('SELECT consumed FROM crawler_authority_ledger WHERE grant=?',
                          (grant['grant'],)).fetchone() == (1,)
        for scope, token in grant['scope_tokens'].items():
            assert db.execute('SELECT platform,expires_at FROM request_scheduler_leases WHERE token=?',
                              (token,)).fetchone() == (scope, renewed['expires_at'])
    assert not client.closed


@pytest.mark.parametrize('operation', ['consume', 'renew_request'])
def test_pause_during_consume_or_media_renewal_wait_rejects(pinned, operation):
    from tools.collection_route import CollectionRouteError
    route, client, _ = pinned
    grant = client.call('reserve', {'operation': 'media', 'lease_seconds': 90})
    assert grant['delay'] == 0
    payload = {'grant': grant['grant']}
    if operation == 'renew_request':
        client.call('consume', payload)
        payload = {'scope_tokens': grant['scope_tokens']}
    charged = accounting_snapshot(route)
    with safety_writer(route, pause=True):
        with pytest.raises(CollectionRouteError):
            client.call(operation, payload)
    assert accounting_snapshot(route) == charged
    with closing(sqlite3.connect(route.scheduler_db_path)) as db:
        assert db.execute('SELECT consumed FROM crawler_authority_ledger WHERE grant=?',
                          (grant['grant'],)).fetchone() == (int(operation == 'renew_request'),)
