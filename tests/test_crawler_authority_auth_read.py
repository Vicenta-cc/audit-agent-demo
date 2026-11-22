"""Read-only required-auth renewal under real, temporary SQLite contention."""
from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from backend.audit_agent import crawler_route as routes
from backend.application_auth.store import AuthStore
from tests.test_camoufox_direct_route import direct  # noqa: F401
from tests.test_crawler_authority_browser_busy import pinned  # noqa: F401


@pytest.fixture
def required(pinned, tmp_path, monkeypatch):
    route, client, owner = pinned
    path = tmp_path / 'required-auth.sqlite3'
    auth = AuthStore(path)
    user = auth.create_user(username='fixture-owner', password='offline-fixture-password', activation_mode='created_at')
    with closing(sqlite3.connect(path)) as db:
        db.execute('UPDATE app_users SET id=? WHERE id=?', (route.owner_id, user['id']))
        db.commit()
    auth.register_crawler_account(route.account_id, route.owner_id)
    monkeypatch.setattr(routes.settings, 'app_auth_db', path)
    monkeypatch.setattr(routes.settings, 'app_auth_mode', 'required')
    return route, client, owner, auth


@contextmanager
def auth_pending_commit(path, *, change=None, hold=1.2):
    """Force a real DELETE-mode PENDING write lock until the timer releases it."""
    reader = sqlite3.connect(path, check_same_thread=False)
    reader.execute('BEGIN')
    reader.execute('SELECT id FROM app_users').fetchall()
    started = threading.Event()
    errors = []
    def write():
        try:
            with closing(sqlite3.connect(path, timeout=4)) as db:
                db.execute('BEGIN IMMEDIATE')
                if change:
                    db.execute(change)
                else:
                    db.execute('UPDATE app_users SET status=status')
                started.set()
                db.commit()
        except BaseException as exc:
            errors.append(exc)
    worker = threading.Thread(target=write)
    worker.start()
    timer = None
    try:
        assert started.wait(2)
        until = time.monotonic() + 2
        while True:
            try:
                with closing(sqlite3.connect(path, timeout=0)) as db:
                    db.execute('SELECT id FROM app_users').fetchall()
            except sqlite3.OperationalError as exc:
                assert exc.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
                break
            assert time.monotonic() < until
            time.sleep(.005)
        timer = threading.Timer(hold, reader.rollback)
        timer.start()
        yield
    finally:
        if timer:
            timer.join(hold + 2)
        reader.rollback()
        reader.close()
        worker.join(5)
        assert not worker.is_alive() and not errors


def test_required_renewal_reads_during_wal_writer_without_initialization(required, monkeypatch):
    route, client, owner, auth = required
    with closing(sqlite3.connect(auth.db_path)) as writer:
        assert writer.execute('PRAGMA journal_mode=WAL').fetchone() == ('wal',)
        writer.execute('BEGIN IMMEDIATE')
        clock = time.time() + 275
        monkeypatch.setattr(time, 'time', lambda: clock)
        start = time.monotonic()
        try:
            assert client.call('browser_validate') is None
            assert time.monotonic() - start < 1
            assert owner.session._lease_ack['lease_version'] == 2
            assert not client.closed
        finally:
            writer.rollback()


@pytest.mark.parametrize('change', [None, "UPDATE app_users SET status='disabled' WHERE id='owner'",
    "DELETE FROM crawler_account_owners", "UPDATE app_users SET expires_at='2000-01-01T00:00:00+00:00' WHERE id='owner'"])
def test_required_renewal_rechecks_after_pending_writer(required, monkeypatch, change):
    from tools.collection_route import CollectionRouteError
    route, client, owner, auth = required
    clock = time.time() + 275
    monkeypatch.setattr(time, 'time', lambda: clock)
    with auth_pending_commit(auth.db_path, change=change):
        if change:
            with pytest.raises(CollectionRouteError):
                client.call('browser_validate')
        else:
            assert client.call('browser_validate') is None
    assert not client.closed
    assert owner.session._lease_ack['lease_version'] == (1 if change else 2)
    with closing(sqlite3.connect(route.scheduler_db_path)) as db:
        assert db.execute('SELECT count(*) FROM route_lease_commands').fetchone()[0] == (0 if change else 1)


def test_readonly_store_does_not_initialize_or_create_database(tmp_path):
    missing = tmp_path / 'missing' / 'auth.db'
    auth = AuthStore(missing, read_only=True)
    with pytest.raises(sqlite3.OperationalError) as caught:
        auth.principal_for_user('owner')
    assert not getattr(caught.value, 'reservation_rolled_back', False)
    assert not missing.parent.exists()


@pytest.mark.parametrize(('case', 'allowed'), [
    ('private_unowned', False), ('public_unowned', True),
    ('proxy_granted', True), ('proxy_revoked', False), ('proxy_missing', False),
    ('admin_private_unowned', False),
])
def test_readonly_authorization_keeps_account_and_proxy_permissions(required, case, allowed):
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    route, _, _, auth = required
    accounts = CrawlerAccountStore(Path(route.safety_db_path))
    proxy_id = 'fixture-proxy' if case.startswith('proxy_') else None
    if 'unowned' in case:
        auth.unregister_crawler_account(route.account_id)
    if case == 'public_unowned':
        with closing(sqlite3.connect(route.safety_db_path)) as db:
            db.execute("UPDATE crawler_accounts SET access_scope='public' WHERE id=?", (route.account_id,))
            db.commit()
    if case == 'admin_private_unowned':
        with closing(sqlite3.connect(auth.db_path)) as db:
            db.execute("UPDATE app_users SET role='admin' WHERE id=?", (route.owner_id,))
            db.commit()
    if case in {'proxy_granted', 'proxy_revoked'}:
        auth.grant_resource(user_id=route.owner_id, resource_type='crawler-proxy',
            resource_id=proxy_id, permission='use', actor_user_id='fixture-admin')
    if case == 'proxy_revoked':
        auth.revoke_resource(user_id=route.owner_id, resource_type='crawler-proxy',
            resource_id=proxy_id, permission='use', actor_user_id='fixture-admin')
    def check():
        return routes.authorize_route_owner(route.account_id, proxy_id, route.owner_id,
            account_store=accounts, authority_retry=True)
    if allowed:
        assert check() is None
    else:
        with pytest.raises(routes.CollectionRouteError, match='PROXY_UNAVAILABLE'):
            check()


def test_busy_is_retryable_only_after_readonly_connection_closed(required, monkeypatch):
    _, _, _, auth = required
    readonly = AuthStore(auth.db_path, read_only=True)
    opened = []
    connect = sqlite3.connect
    def tracked(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection
    with auth_pending_commit(auth.db_path):
        with monkeypatch.context() as patch:
            patch.setattr(sqlite3, 'connect', tracked)
            started = time.monotonic()
            with pytest.raises(sqlite3.OperationalError) as caught:
                readonly.principal_for_user('owner')
            assert time.monotonic() - started < .8
    assert caught.value.sqlite_errorcode & 255 == sqlite3.SQLITE_BUSY
    assert caught.value.reservation_step == 'auth_read'
    assert caught.value.reservation_rolled_back is True
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        opened[0].execute('SELECT 1')


def test_readonly_nonbusy_failure_is_not_retryable_and_writes_are_denied(required):
    _, _, _, auth = required
    readonly = AuthStore(auth.db_path, read_only=True)
    with pytest.raises(sqlite3.OperationalError) as caught:
        readonly.register_crawler_account('new-account', 'owner')
    assert caught.value.sqlite_errorcode & 255 == sqlite3.SQLITE_READONLY
    assert not getattr(caught.value, 'reservation_rolled_back', False)
    with closing(sqlite3.connect(auth.db_path)) as db:
        db.execute('DROP TABLE resource_grants')
        db.commit()
    with pytest.raises(sqlite3.OperationalError) as caught:
        readonly.has_grant('owner', 'crawler-proxy', 'proxy', 'use')
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_ERROR
    assert not getattr(caught.value, 'reservation_rolled_back', False)


def test_readonly_close_failure_never_certifies_retry(required, monkeypatch):
    _, _, _, auth = required
    readonly = AuthStore(auth.db_path, read_only=True)
    busy = sqlite3.OperationalError('fixture busy')
    busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
    close_error = sqlite3.OperationalError('fixture close busy')
    close_error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    class BrokenClose:
        def close(self):
            raise close_error
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', lambda *args, **kwargs: BrokenClose())
        with pytest.raises(sqlite3.OperationalError) as caught:
            with readonly._connect():
                raise busy
    assert caught.value is close_error
    assert not getattr(caught.value, 'reservation_rolled_back', False)
    assert not getattr(busy, 'reservation_rolled_back', False)
