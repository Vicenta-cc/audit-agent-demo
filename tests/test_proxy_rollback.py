"""Offline additive migration and current-binary rollback safety contracts.

An older binary may ignore proxy_required: this suite deliberately makes no claim
that restoring old code is safe without disabling bound accounts first.
"""
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
from backend.audit_agent import crawler_route
from backend.task_admission import resources


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(resources.settings, 'task_resource_lock_dir', tmp_path / 'locks')
    monkeypatch.setattr(resources.settings, 'crawler_browser_profile_root', tmp_path / 'profiles')
    monkeypatch.setattr(crawler_route.settings, 'crawler_proxy_enabled', False)
    monkeypatch.setattr(crawler_route.settings, 'app_auth_mode', 'off')
    accounts = CrawlerAccountStore(tmp_path / 'accounts.sqlite3')
    store = CrawlerProxyStore(accounts, cipher=AuthStateCipher(encryption_key='rollback-test-key'),
                              mutation_guard=resources.proxy_mutation_guard)
    monkeypatch.setattr(crawler_route, '_store', store)
    # The current resolver checks the account store before consulting proxy cache.
    monkeypatch.setattr('backend.audit_agent.crawler_account_store.crawler_account_store', accounts)
    return accounts, store


def test_additive_migration_preserves_auth_risk_history_profile(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    profile = tmp_path / 'profiles' / 'existing-account'
    profile.mkdir(parents=True)
    sentinel = profile / 'profile.sentinel'
    sentinel.write_bytes(b'existing browser identity')
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE crawler_accounts (id TEXT PRIMARY KEY, platform TEXT NOT NULL,
                display_name TEXT NOT NULL,platform_account_id TEXT,status TEXT NOT NULL,
                last_validated_at TEXT,last_used_at TEXT,last_error TEXT,cooldown_until TEXT,
                failure_kind TEXT,auth_state_ciphertext TEXT,auth_state_updated_at TEXT,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,access_scope TEXT,
                risk_count INTEGER,last_risk_at TEXT,auth_state_revision INTEGER);
            INSERT INTO crawler_accounts VALUES ('legacy','dy','legacy','identity','active',
                'validated','used','risk','2999-01-01','verify','preserved-auth-ciphertext',
                'auth-time','created','updated','private',7,'risk-time',11);
            CREATE TABLE scheduler_history(scope TEXT, timestamp REAL);
            INSERT INTO scheduler_history VALUES ('dy',12345);
        ''')
        before = conn.execute('SELECT * FROM crawler_accounts').fetchone()
    for _ in range(2):
        accounts = CrawlerAccountStore(path)
        CrawlerProxyStore(accounts, cipher=AuthStateCipher(encryption_key='migration-key'))
    with sqlite3.connect(path) as conn:
        after = conn.execute('SELECT * FROM crawler_accounts').fetchone()
        assert after[:len(before)] == before
        assert after[len(before):] == (None, 0, 0, 0, 0)
        assert conn.execute('SELECT * FROM scheduler_history').fetchall() == [('dy',12345.0)]
    assert sentinel.read_bytes() == b'existing browser identity'
    account = accounts.get('legacy')
    assert account['auth_state_revision'] == 11 and account['risk_count'] == 7
    assert accounts.get_auth_state_ciphertext('legacy') == 'preserved-auth-ciphertext'


def test_flag_off_never_downgrades_required_and_legacy_still_works(isolated, monkeypatch):
    accounts, store = isolated
    legacy = accounts.create(platform='dy', display_name='legacy')
    required = accounts.create(platform='dy', display_name='required')
    proxy = store.create(display_name='proxy', endpoint='http://localhost:8080')
    store.bind(required['id'], proxy['id'], expected_binding_revision=0)
    # Resolver must fail before even loading a transport contract or creating network clients.
    def forbidden_contract():
        pytest.fail('feature-off path attempted network-contract setup')
    monkeypatch.setattr(crawler_route, 'route_contract', forbidden_contract)
    with pytest.raises(crawler_route.CollectionRouteError, match='PROXY_UNAVAILABLE'):
        crawler_route.acquire_route_execution(required['id'], 'execution', 'owner')
    assert crawler_route.resolve_account_route(legacy['id']) is None
    bundle = crawler_route.acquire_route_execution(legacy['id'], 'execution', 'owner')
    try:
        assert bundle.snapshot is None
    finally:
        bundle.close()
    store.unbind(required['id'], expected_binding_revision=1)
    assert accounts.get(required['id'])['proxy_required']
    with pytest.raises(crawler_route.CollectionRouteError, match='PROXY_UNAVAILABLE'):
        crawler_route.acquire_route_execution(required['id'], 'execution', 'owner')


def test_production_store_factory_always_installs_real_mutation_guard(isolated, monkeypatch):
    accounts, store = isolated
    monkeypatch.setattr(crawler_route, '_store', None)
    monkeypatch.setattr('backend.audit_agent.crawler_account_store.crawler_account_store', accounts)
    default = crawler_route.get_proxy_store()
    injected_accounts = crawler_route.get_proxy_store(accounts)
    assert default.mutation_guard is resources.proxy_mutation_guard
    assert injected_accounts.mutation_guard.func is resources.proxy_mutation_guard
    assert injected_accounts.mutation_guard.keywords["account_store"] is accounts
    assert default.health_notifier is crawler_route.route_health_notifier
    assert injected_accounts.health_notifier is crawler_route.route_health_notifier
    assert default.db_path == accounts.db_path


def test_real_child_inherited_use_lock_blocks_store_edit(isolated):
    accounts, store = isolated
    account = accounts.create(platform='dy', display_name='child')
    proxy = store.create(display_name='proxy', endpoint='http://localhost:8080')
    bundle = resources.ExecutionLeaseBundle('execution', 'owner', account['id'], SimpleNamespace(proxy_id=proxy['id']))
    assert bundle.acquire()
    child = subprocess.Popen([sys.executable, '-c', 'import sys;sys.stdin.read()'],
                             stdin=subprocess.PIPE, pass_fds=bundle.pass_fds)
    try:
        bundle.close()
        with pytest.raises(crawler_route.CollectionRouteError, match='PROXY_BUSY'):
            store.update(proxy['id'], expected_revision=1, status='maintenance')
        assert store.get(proxy['id'])['status'] == 'active'
        assert store.get(proxy['id'])['config_revision'] == 1
    finally:
        child.communicate(timeout=5)
        bundle.close()
    assert store.update(proxy['id'], expected_revision=1, status='maintenance')['config_revision'] == 2
