from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import sqlite3
import threading
import time

import pytest

from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
from backend.application_auth.service import ApplicationAuthService
from backend.application_auth.store import AuthStore, AuthorizationError
from backend.investigation_creation.principal import Principal


@pytest.fixture
def stores(tmp_path):
    accounts = CrawlerAccountStore(tmp_path / 'accounts.sqlite3')
    proxies = CrawlerProxyStore(accounts, cipher=AuthStateCipher(encryption_key='test-proxy-key'))
    return accounts, proxies


def create_proxy(proxies, **changes):
    return proxies.create(display_name='fixture', endpoint='http://proxy.example:8080',
                          credentials={'username': 'private-user', 'password': 'private-password'},
                          **({'expires_at': time.time() + 7200} | changes))


def create_account(accounts, platform='dy'):
    account = accounts.create(platform=platform, display_name='fixture')
    return accounts.save_auth_state(account['id'], 'test-ciphertext')


def test_additive_migration_and_encrypted_projection(stores):
    accounts, proxies = stores
    account = create_account(accounts)
    assert (account['proxy_id'], account['binding_revision'], account['proxy_required']) == (None, 0, False)
    resource = create_proxy(proxies)
    assert resource['endpoint'] == {'scheme': 'http', 'host': 'proxy.example', 'port': 8080}
    assert resource['has_credentials']
    assert 'ciphertext' not in repr(resource)
    assert 'private-password' not in repr(resource)
    assert 'private-user' not in repr(resource)
    transport = proxies.decrypt_snapshot(resource['id'], expected_revision=1)
    assert transport['credentials']['password'] == 'private-password'
    assert 'private-password' not in repr(transport)
    with sqlite3.connect(accounts.db_path) as conn:
        stored = conn.execute('SELECT transport_ciphertext FROM proxy_resources').fetchone()[0]
        assert all(text not in stored for text in ('private-user', 'private-password', 'proxy.example'))
    assert proxies.list() == [proxies.get_public(resource['id'])]
    assert 'private-password' not in accounts.db_path.read_bytes().decode('latin1')


@pytest.mark.parametrize('endpoint', ['https://host:8080', 'socks5://host:8080', 'http://u:p@host:8080',
                                      'http://host:0', 'http://host', 'http://host:8080/a',
                                      'http://bad host:8080', {'scheme': 'http', 'host': 'x', 'port': True}])
def test_invalid_endpoints_fail_without_network(stores, endpoint):
    with pytest.raises(ValueError, match='PROXY_INVALID_ENDPOINT'):
        stores[1].create(display_name='a', endpoint=endpoint)


def test_binding_capacity_required_unbind_and_platform(stores):
    accounts, proxies = stores
    resource = create_proxy(proxies)
    a, b = create_account(accounts), create_account(accounts)
    bound = proxies.bind(a['id'], resource['id'], expected_binding_revision=0)
    assert bound['binding_revision'] == 1 and bound['proxy_required']
    assert bound['auth_state_revision'] == a['auth_state_revision']
    assert proxies.bind(a['id'], resource['id'], expected_binding_revision=1)['binding_revision'] == 1
    with pytest.raises(ValueError, match='PROXY_CAPACITY_CONFLICT'):
        proxies.bind(b['id'], resource['id'], expected_binding_revision=0)
    xhs = create_account(accounts, 'xhs')
    with pytest.raises(ValueError, match='PROXY_PLATFORM_MISMATCH'):
        proxies.bind(xhs['id'], resource['id'], expected_binding_revision=0)
    with pytest.raises(ValueError, match='PROXY_RESOURCE_BOUND'):
        proxies.delete(resource['id'], expected_revision=1)
    unbound = proxies.unbind(a['id'], expected_binding_revision=1)
    assert unbound['proxy_required'] and unbound['binding_revision'] == 2
    assert unbound['proxy_id'] is None
    assert [item['id'] for item in accounts.available_accounts('dy')] == [b['id']]
    with pytest.raises(ValueError, match='PROXY_BINDING_REQUIRED'):
        proxies.resolve(a['id'])
    with pytest.raises(ValueError, match='PROXY_REVISION_MISMATCH'):
        proxies.bind(a['id'], resource['id'], expected_binding_revision=1)
    assert proxies.delete(resource['id'], expected_revision=1)


def test_capacity_atomic_across_distinct_store_instances(stores):
    accounts, proxies = stores
    resource = create_proxy(proxies, max_bound_accounts=2)
    ids = [create_account(accounts)['id'] for _ in range(3)]
    barrier = threading.Barrier(3)
    def bind(account_id):
        independent = CrawlerProxyStore(db_path=accounts.db_path, cipher=proxies.cipher)
        barrier.wait()
        try:
            independent.bind(account_id, resource['id'], expected_binding_revision=0)
            return 'bound'
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(bind, ids))
    assert results.count('bound') == 2
    assert results.count('PROXY_CAPACITY_CONFLICT') == 1
    assert proxies.get(resource['id'])['bound_accounts'] == 2
    with pytest.raises(ValueError, match='PROXY_CAPACITY_CONFLICT'):
        proxies.update(resource['id'], expected_revision=1, max_bound_accounts=1)
    with pytest.raises(ValueError, match='PROXY_CAPACITY_CONFLICT'):
        proxies.update(resource['id'], expected_revision=1, max_active_accounts=1, active_count=2)


def test_revision_credentials_clear_and_health(stores):
    accounts, proxies = stores
    r = create_proxy(proxies)
    a = create_account(accounts)
    proxies.bind(a['id'], r['id'], expected_binding_revision=0)
    assert proxies.update(r['id'], expected_revision=1, credentials={'username': '', 'password': ''})['config_revision'] == 1
    updated = proxies.update(r['id'], expected_revision=1, clear_credentials=True)
    assert updated['config_revision'] == 2 and not updated['has_credentials']
    proxies.mark_route_login_validated(a['id'], expected_binding_revision=1, expected_config_revision=2)
    assert proxies.decrypt_snapshot(r['id'])['credentials'] == {'username': '', 'password': ''}
    with pytest.raises(ValueError, match='PROXY_REVISION_MISMATCH'):
        proxies.update(r['id'], expected_revision=1, display_name='bad')
    for status in ('maintenance', 'unavailable', 'expired', 'exit_changed'):
        assert proxies.set_health(r['id'], status=status)['config_revision'] == 2
        assert not accounts.available_accounts('dy')
        with pytest.raises(ValueError):
            proxies.resolve(a['id'])
        if status != 'expired':
            assert proxies.resolve(a['id'], allow_probe=True)['resource']['status'] == status
    proxies.set_health(r['id'], status='active', cooldown_until=time.time() + 100)
    assert not accounts.available_accounts('dy')
    with pytest.raises(ValueError, match='PROXY_UNAVAILABLE'):
        proxies.resolve(a['id'])
    proxies.set_health(r['id'], status='active', cooldown_until=None, stop_reason='')
    assert accounts.available_accounts('dy')
    assert proxies.observe_exit(r['id'], '203.0.113.1', observed_at=10)['status'] == 'active'
    assert proxies.observe_exit(r['id'], '203.0.113.2', observed_at=11)['status'] == 'exit_changed'
    assert proxies.get(r['id'])['config_revision'] == 2
    with pytest.raises(ValueError, match='PROXY_STALE_OBSERVATION'):
        proxies.observe_exit(r['id'], '203.0.113.1', observed_at=9)
    with pytest.raises(ValueError, match='PROXY_EXIT_CHANGED'):
        proxies.resolve(a['id'])


def test_expiry_and_missing_resource_fail_closed(stores):
    accounts, proxies = stores
    a = create_account(accounts)
    r = create_proxy(proxies, expires_at=1)
    proxies.bind(a['id'], r['id'], expected_binding_revision=0)
    assert not accounts.available_accounts('dy')
    with pytest.raises(ValueError, match='PROXY_EXPIRED'):
        proxies.resolve(a['id'], allow_probe=True)
    with sqlite3.connect(accounts.db_path) as conn:
        conn.execute('DROP TABLE proxy_resources')
    assert not accounts.available_accounts('dy')


def test_mutation_guard_is_entered_for_entire_transaction(stores):
    accounts, proxies = stores
    r = create_proxy(proxies)
    a = create_account(accounts)
    calls = []
    @contextmanager
    def guard(operation, proxy_id, account_id):
        calls.append(('enter', operation))
        yield
        # Separate connection sees committed changes while locks still held.
        with sqlite3.connect(accounts.db_path) as conn:
            if operation == 'bind':
                assert conn.execute('SELECT proxy_id FROM crawler_accounts WHERE id=?', (a['id'],)).fetchone()[0] == r['id']
            if operation == 'update':
                assert conn.execute('SELECT config_revision FROM proxy_resources WHERE id=?', (r['id'],)).fetchone()[0] == 2
        calls.append(('exit', operation))
    proxies.mutation_guard = guard
    proxies.bind(a['id'], r['id'], expected_binding_revision=0)
    proxies.update(r['id'], expected_revision=1, display_name='updated')
    proxies.unbind(a['id'], expected_binding_revision=1)
    proxies.delete(r['id'], expected_revision=2)
    assert calls == [(edge, op) for op in ('bind', 'update', 'unbind', 'delete') for edge in ('enter', 'exit')]


def test_proxy_use_grant_does_not_grant_private_account(tmp_path, stores):
    auth = AuthStore(tmp_path / 'auth.sqlite3')
    user = auth.create_user(username='proxy-user', password='Long-password-123!')
    principal = Principal(user['id'])
    service = ApplicationAuthService(auth)
    account = create_account(stores[0])
    proxy = create_proxy(stores[1])
    assert not service.can_use_crawler_proxy(principal, proxy['id'])
    auth.grant_resource(user_id=user['id'], resource_type='crawler-proxy', resource_id=proxy['id'], permission='use', actor_user_id='admin')
    assert service.can_use_crawler_proxy(principal, proxy['id'])
    assert not service.can_use_crawler_account(principal, account['id'])
    assert not service.can_manage_crawler_proxy(principal, proxy['id'])
    assert service.can_use_crawler_proxy(Principal('admin', role='admin'), proxy['id'])
    projection = service.public_crawler_account({**account, 'unknown_secret': 'secret', 'credentials': {'password': 'secret'}})
    assert projection == {key: value for key,value in account.items() if key not in {'shared_exit_key','recovery_claim','recovery_incident','validated_direct_generation'}}
    assert service.public_crawler_proxy({**proxy, 'password': 'secret', 'transport_ciphertext': 'secret'}) == proxy
    auth.revoke_resource(user_id=user['id'], resource_type='crawler-proxy', resource_id=proxy['id'], permission='use', actor_user_id='admin')
    with pytest.raises(AuthorizationError):
        service.require_crawler_proxy_use(principal, proxy['id'])


def test_legacy_account_database_migration(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.executescript('''CREATE TABLE crawler_accounts (
            id TEXT PRIMARY KEY,platform TEXT NOT NULL,display_name TEXT NOT NULL,
            platform_account_id TEXT,status TEXT NOT NULL,last_validated_at TEXT,
            last_used_at TEXT,last_error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            INSERT INTO crawler_accounts VALUES ('legacy','dy','old',NULL,'active',NULL,NULL,NULL,'old','old');''')
    accounts = CrawlerAccountStore(path)
    before = accounts.get('legacy')
    proxies = CrawlerProxyStore(accounts, cipher=AuthStateCipher(encryption_key='test-proxy-key'))
    assert accounts.get('legacy') == before
    assert not before['proxy_required'] and before['binding_revision'] == 0
    assert proxies.list() == []


def test_configuration_changes_require_guard_but_health_does_not(stores):
    accounts, proxies = stores
    resource = create_proxy(proxies)
    a = create_account(accounts)
    @contextmanager
    def busy_guard(*args):
        raise ValueError('PROXY_BUSY')
        yield
    proxies.mutation_guard = busy_guard
    with pytest.raises(ValueError, match='PROXY_BUSY'):
        proxies.update(resource['id'], expected_revision=1, display_name='blocked')
    with pytest.raises(ValueError, match='PROXY_BUSY'):
        proxies.bind(a['id'], resource['id'], expected_binding_revision=0)
    assert proxies.get(resource['id'])['config_revision'] == 1
    assert accounts.get(a['id'])['proxy_id'] is None
    proxies.set_health(resource['id'], status='unavailable', stop_reason='PROXY_AUTH_FAILED')
    proxies.record_event(resource['id'], 'unavailable', code='PROXY_AUTH_FAILED')
    assert proxies.get(resource['id'])['config_revision'] == 1
    with pytest.raises(ValueError, match='PROXY_INVALID_EVENT'):
        proxies.set_health(resource['id'], status='unavailable', stop_reason='http://user:password@example')


@pytest.mark.parametrize('changes', [
    {'credentials': {'username': 'user', 'password': ''}},
    {'credentials': {'username': 'user', 'password': 'line\nbreak'}},
    {'expires_at': float('nan')}, {'expires_at': True},
    {'max_bound_accounts': True}, {'max_active_accounts': 2},
    {'platform': 'xhs'}, {'provider': 'other'},
])
def test_resource_invalid_input(stores, changes):
    values = {'display_name': 'fixture', 'endpoint': 'http://host.example:8080', **changes}
    with pytest.raises(ValueError):
        stores[1].create(**values)
    assert stores[1].list() == []


def test_health_notifier_only_runs_after_commit_and_defaults_preserve_stop(stores):
    accounts, proxies = stores
    proxy = create_proxy(proxies)
    notifications = []
    def notifier(proxy_id, revision, status, stop_reason):
        with sqlite3.connect(accounts.db_path) as conn:
            row = conn.execute('SELECT config_revision,status,stop_reason FROM proxy_resources WHERE id=?', (proxy_id,)).fetchone()
            assert row == (revision, status, stop_reason)
        notifications.append(row)
    proxies.health_notifier = notifier
    proxies.set_health(proxy['id'], status='unavailable', cooldown_until=1000, stop_reason='PROXY_AUTH_FAILED')
    restored = proxies.set_health(proxy['id'], status='active')
    assert restored['cooldown_until'] == 1000
    assert restored['stop_reason'] == 'PROXY_AUTH_FAILED'
    assert proxies.observe_exit(proxy['id'], '203.0.113.1', observed_at=10)['status'] == 'active'
    assert proxies.observe_exit(proxy['id'], '203.0.113.2', observed_at=11)['status'] == 'exit_changed'
    assert notifications[-1] == (1, 'exit_changed', 'PROXY_EXIT_CHANGED')
    updated = proxies.update(proxy['id'], expected_revision=1, display_name='after commit')
    assert notifications[-1] == (2, 'exit_changed', 'PROXY_EXIT_CHANGED')
    assert updated['config_revision'] == 2
    proxies.set_health(proxy['id'], status='active', cooldown_until=None, stop_reason='')
    assert notifications[-1] == (2, 'active', '')


def test_login_validation_tracks_binding_and_configuration_without_erasing_auth(stores):
    accounts, proxies = stores
    a = create_account(accounts)
    original_auth_revision = a['auth_state_revision']
    proxy = create_proxy(proxies)
    bound = proxies.bind(a['id'], proxy['id'], expected_binding_revision=0)
    assert bound['needs_proxy_login_validation']
    assert not accounts.available_accounts('dy')
    with pytest.raises(ValueError, match='PROXY_LOGIN_VALIDATION_REQUIRED'):
        proxies.resolve(a['id'])
    assert proxies.resolve(a['id'], allow_probe=True)['resource']['id'] == proxy['id']
    with pytest.raises(ValueError, match='PROXY_REVISION_MISMATCH'):
        proxies.mark_route_login_validated(a['id'], expected_binding_revision=0, expected_config_revision=1)
    validated = proxies.mark_route_login_validated(a['id'], expected_binding_revision=1, expected_config_revision=1)
    assert not validated['needs_proxy_login_validation']
    assert len(accounts.available_accounts('dy')) == 1
    assert proxies.resolve(a['id'])['resource']['id'] == proxy['id']
    proxies.observe_exit(proxy['id'], '203.0.113.1', observed_at=10)
    proxies.update(proxy['id'], expected_revision=1, endpoint='http://new-proxy.example:8081')
    changed = accounts.get(a['id'])
    assert changed['needs_proxy_login_validation']
    assert changed['auth_state_revision'] == original_auth_revision
    assert accounts.get_auth_state_ciphertext(a['id']) == 'test-ciphertext'
    assert changed['status'] == 'active'
    resource = proxies.get(proxy['id'])
    assert resource['observed_exit_ip'] is None and resource['observed_exit_at'] is None
    with sqlite3.connect(accounts.db_path) as conn:
        assert conn.execute('SELECT kind,code FROM resource_events WHERE proxy_id=?', (proxy['id'],)).fetchall() == [('route_configuration_changed', 'PROBE_REQUIRED')]
    with pytest.raises(ValueError, match='PROXY_REVISION_MISMATCH'):
        proxies.mark_route_login_validated(a['id'], expected_binding_revision=1, expected_config_revision=1)
    proxies.mark_route_login_validated(a['id'], expected_binding_revision=1, expected_config_revision=2)
    assert not accounts.get(a['id'])['needs_proxy_login_validation']
    proxies.unbind(a['id'], expected_binding_revision=1)
    proxies.bind(a['id'], proxy['id'], expected_binding_revision=2)
    assert accounts.get(a['id'])['needs_proxy_login_validation']


def test_login_validation_rejects_unhealthy_or_unauthenticated_account(stores):
    accounts, proxies = stores
    a = accounts.create(platform='dy', display_name='not logged in')
    proxy = create_proxy(proxies)
    proxies.bind(a['id'], proxy['id'], expected_binding_revision=0)
    with pytest.raises(ValueError, match='PROXY_UNAVAILABLE'):
        proxies.mark_route_login_validated(a['id'], expected_binding_revision=1, expected_config_revision=1)
    accounts.save_auth_state(a['id'], 'test-ciphertext')
    proxies.set_health(proxy['id'], status='maintenance')
    with pytest.raises(ValueError, match='PROXY_UNAVAILABLE'):
        proxies.mark_route_login_validated(a['id'], expected_binding_revision=1, expected_config_revision=1)
    assert accounts.get(a['id'])['validated_proxy_config_revision'] == 0


def test_active_session_limit_cannot_exceed_global_platform_limit(stores):
    accounts, proxies = stores
    assert proxies.max_active_limit == 1
    with pytest.raises(ValueError, match='PROXY_INVALID_CAPACITY'):
        create_proxy(proxies, max_bound_accounts=3, max_active_accounts=2)
    resource = create_proxy(proxies, max_bound_accounts=3)
    with pytest.raises(ValueError, match='PROXY_CAPACITY_CONFLICT'):
        proxies.update(resource['id'], expected_revision=1, max_active_accounts=2)
    assert proxies.get(resource['id'])['max_active_accounts'] == 1
    custom = CrawlerProxyStore(accounts, cipher=proxies.cipher, max_active_limit=2)
    assert create_proxy(custom, max_bound_accounts=3, max_active_accounts=2)['max_active_accounts'] == 2


def test_login_validation_uses_caller_transaction_and_rolls_back_with_auth(stores):
    accounts, proxies = stores
    account = accounts.create(platform='dy', display_name='transaction')
    proxy = create_proxy(proxies)
    proxies.bind(account['id'], proxy['id'], expected_binding_revision=0)
    with pytest.raises(RuntimeError, match='rollback'):
        with accounts._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            accounts.save_douyin_auth_state(account['id'], 'new-ciphertext', connection=conn)
            proxies.mark_route_login_validated(account['id'], expected_binding_revision=1,
                expected_config_revision=1, connection=conn)
            assert not accounts.get(account['id'], connection=conn)['needs_proxy_login_validation']
            raise RuntimeError('rollback')
    current = accounts.get(account['id'])
    assert current['needs_proxy_login_validation'] and current['validated_proxy_config_revision'] == 0
    assert not current['has_auth_state'] and current['status'] == 'login_required'


def test_unknown_expiry_is_probe_only_never_business_ready(stores):
    accounts, proxies = stores
    account = create_account(accounts)
    proxy = create_proxy(proxies, expires_at=None)
    proxies.bind(account['id'], proxy['id'], expected_binding_revision=0)
    assert proxies.get_public(proxy['id'])['expires_at'] is None
    assert proxies.resolve(account['id'], allow_probe=True)['resource']['expires_at'] is None
    with pytest.raises(ValueError, match='PROXY_UNAVAILABLE'):
        proxies.resolve(account['id'])
    with pytest.raises(ValueError, match='PROXY_UNAVAILABLE'):
        proxies.mark_route_login_validated(account['id'], expected_binding_revision=1, expected_config_revision=1)
    assert not accounts.available_accounts('dy')
    updated = proxies.update(proxy['id'], expected_revision=1, expires_at=time.time() + 7200)
    proxies.mark_route_login_validated(account['id'], expected_binding_revision=1,
                                      expected_config_revision=updated['config_revision'])
    assert len(accounts.available_accounts('dy')) == 1
    assert proxies.resolve(account['id'])['resource']['expires_at'] > time.time()
