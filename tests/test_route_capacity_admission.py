"""Whole-browser capacity is independent of per-request concurrency."""
from contextlib import ExitStack, closing
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from backend.audit_agent import crawler_route as app
from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
from backend.task_admission import resources
from test_camoufox_direct_route import direct  # noqa: F401


@pytest.fixture
def capacity(direct, monkeypatch, tmp_path):
    accounts, first = direct
    monkeypatch.setattr(app.settings, 'task_resource_lock_dir', tmp_path / 'locks')
    monkeypatch.setattr(app.settings, 'request_concurrency', 1)
    monkeypatch.setattr(app.settings, 'crawler_shared_exit_active_limit', 2)
    monkeypatch.setattr(app.settings, 'crawler_local_direct_pinning_enabled', True)
    monkeypatch.setattr(app.settings, 'crawler_proxy_trial_id', '')
    entries = [first] + [accounts.create(platform='dy', display_name='capacity') for _ in range(2)]
    for account in entries:
        accounts.save_auth_state(account['id'], 'fixture-encrypted-state')
    monkeypatch.setattr(app, '_run_maintenance_probe', lambda bundle: app.route_contract().ProbeReceipt(
        bundle.snapshot.proxy_id, bundle.snapshot.config_revision, 'probe', '203.0.113.9', time.time()))
    return accounts, entries


def acquire(capacity, index, *, pinned=False):
    accounts, entries = capacity
    execution = 'job-' + str(index)
    kwargs = dict(account_store=accounts)
    if pinned:
        kwargs.update(investigation_collection=True,
            direct_egress_pin=app.LocalDirectEgressPin(execution_id=execution, owner_id='owner'))
    return app.acquire_route_execution(entries[index]['id'], execution, 'owner', **kwargs)


@pytest.mark.parametrize('pinned', [False, True])
def test_two_browser_routes_share_exit_while_http_concurrency_remains_one(capacity, pinned):
    with ExitStack() as stack:
        first = stack.enter_context(closing(acquire(capacity, 0, pinned=pinned)))
        stack.enter_context(closing(acquire(capacity, 1, pinned=pinned)))
        with pytest.raises(app.RouteCapacityBusy) as busy:
            acquire(capacity, 2, pinned=pinned)
        assert busy.value.code == 'PROXY_BUSY'
        assert busy.value.reason == 'route_capacity'
        with pytest.raises(app.CollectionRouteError) as same:
            acquire(capacity, 0, pinned=pinned)
        assert same.value.code == 'PROXY_BUSY'
        assert not isinstance(same.value, app.RouteCapacityBusy)
        from backend.audit_agent.crawler_request_authority import ensure_owner_request_policy
        ensure_owner_request_policy(first.snapshot.scheduler_db_path)
        with sqlite3.connect(first.snapshot.scheduler_db_path) as db:
            limits = json.loads(db.execute("SELECT limits_json FROM request_scheduler_policy WHERE platform='dy'").fetchone()[0])
        assert limits[2] == 1
        first.close()
        stack.enter_context(closing(acquire(capacity, 2, pinned=pinned)))
    # Both the failed attempt's profile lock and all shared capacity locks are reusable.
    with closing(acquire(capacity, 2, pinned=pinned)):
        pass
    with resources.proxy_edit_lease('service-direct') as editable:
        assert editable


def test_direct_limit_one_is_preserved(capacity, monkeypatch):
    monkeypatch.setattr(app.settings, 'crawler_shared_exit_active_limit', 1)
    with closing(acquire(capacity, 0)):
        with pytest.raises(app.RouteCapacityBusy, match='PROXY_BUSY') as busy:
            acquire(capacity, 1)
        assert busy.value.reason == 'route_capacity'
    with closing(acquire(capacity, 1)):
        pass


def bind_proxy(capacity, monkeypatch, *, max_active_accounts):
    accounts, entries = capacity
    monkeypatch.setattr(app.settings, 'crawler_proxy_enabled', True)
    proxies = CrawlerProxyStore(accounts, max_active_limit=2)
    proxy = proxies.create(display_name='fixture', endpoint='http://127.0.0.1:9',
        expires_at=time.time() + 3600, max_bound_accounts=3, max_active_accounts=max_active_accounts)
    proxies.observe_exit(proxy['id'], '203.0.113.9')
    for account in entries:
        bound = proxies.bind(account['id'], proxy['id'], expected_binding_revision=account['binding_revision'])
        proxies.mark_route_login_validated(account['id'], expected_binding_revision=bound['binding_revision'],
            expected_config_revision=proxy['config_revision'])
    return proxy


def test_proxy_resource_capacity_is_not_expanded(capacity, monkeypatch):
    proxy = bind_proxy(capacity, monkeypatch, max_active_accounts=1)
    with closing(acquire(capacity, 0)):
        with pytest.raises(app.RouteCapacityBusy) as busy:
            acquire(capacity, 1)
        assert busy.value.reason == 'route_capacity'
    with closing(acquire(capacity, 1)):
        pass
    with resources.proxy_edit_lease(proxy['id']) as editable:
        assert editable


def test_actual_exit_capacity_still_caps_larger_proxy_resource(capacity, monkeypatch):
    monkeypatch.setattr(app.settings, 'crawler_shared_exit_active_limit', 1)
    proxy = bind_proxy(capacity, monkeypatch, max_active_accounts=2)
    with closing(acquire(capacity, 0)):
        with pytest.raises(app.RouteCapacityBusy) as busy:
            acquire(capacity, 1)
        assert busy.value.reason == 'exit_capacity'
    with closing(acquire(capacity, 1)):
        pass
    with resources.proxy_edit_lease(proxy['id']) as editable:
        assert editable


def test_boolean_capacity_reason_excludes_edit_account_and_cleanup_failures(capacity, monkeypatch):
    def bundle(account):
        return resources.ExecutionLeaseBundle('job', 'owner', account, SimpleNamespace(proxy_id='p'))
    first, second = bundle('a'), bundle('b')
    assert first.acquire()
    assert not second.acquire()
    assert second.capacity_wait_reason == 'route_capacity' and not second.pass_fds
    # Stale capacity classification must be cleared on each attempt.
    with closing(bundle('b')) as account_owner:
        account_owner.snapshot = None
        assert account_owner.acquire()
        assert not second.acquire()
        assert second.capacity_wait_reason == ''
    def fail_cleanup():
        raise RuntimeError('drain_failed')
    original_close = second._close_handles
    monkeypatch.setattr(second, '_close_handles', fail_cleanup)
    with pytest.raises(RuntimeError, match='drain_failed'):
        second.acquire()
    assert second.capacity_wait_reason == ''
    monkeypatch.setattr(second, '_close_handles', original_close)
    second.close()
    first.close()
    third = bundle('c')
    with resources.proxy_edit_lease('p') as editable:
        assert editable
        assert not third.acquire()
        assert third.capacity_wait_reason == '' and not third.pass_fds


def test_exit_capacity_cleanup_failure_is_not_retryable(capacity, monkeypatch):
    monkeypatch.setattr(app.settings, 'crawler_shared_exit_active_limit', 1)
    bind_proxy(capacity, monkeypatch, max_active_accounts=2)
    original_close = resources.ExecutionLeaseBundle.close
    retained = []
    with closing(acquire(capacity, 0)):
        def fail_attempt_cleanup(bundle):
            retained.append(bundle)
            raise RuntimeError('drain_failed')
        with monkeypatch.context() as patch:
            patch.setattr(resources.ExecutionLeaseBundle, 'close', fail_attempt_cleanup)
            with pytest.raises(RuntimeError, match='drain_failed') as error:
                acquire(capacity, 1)
            assert not isinstance(error.value, app.RouteCapacityBusy)
        assert len(retained) == 1
        assert retained[0].pass_fds  # Fail closed until the owner can finish draining.
        original_close(retained[0])
    with closing(acquire(capacity, 1)):
        pass


def test_proxy_store_capacity_uses_context_limit_and_honors_explicit_override(capacity):
    accounts, _ = capacity
    proxies = CrawlerProxyStore(accounts)
    proxy = proxies.create(display_name='capacity', endpoint='http://127.0.0.1:9',
        max_bound_accounts=3, max_active_accounts=2)
    assert proxy['max_active_accounts'] == 2
    changed = proxies.update(proxy['id'], expected_revision=proxy['config_revision'], max_active_accounts=1)
    changed = proxies.update(proxy['id'], expected_revision=changed['config_revision'], max_active_accounts=2)
    with pytest.raises(ValueError, match='PROXY_CAPACITY_CONFLICT'):
        proxies.update(proxy['id'], expected_revision=changed['config_revision'], max_active_accounts=3)
    restricted = CrawlerProxyStore(accounts, max_active_limit=1)
    with pytest.raises(ValueError, match='PROXY_INVALID_CAPACITY'):
        restricted.create(display_name='restricted', endpoint='http://127.0.0.1:9',
            max_bound_accounts=2, max_active_accounts=2)
    with pytest.raises(ValueError, match='PROXY_CAPACITY_CONFLICT'):
        restricted.update(proxy['id'], expected_revision=changed['config_revision'], max_active_accounts=2)


@pytest.mark.parametrize('pinned', [False, True])
def test_capacity_release_does_not_override_account_pause(capacity, monkeypatch, pinned):
    accounts, entries = capacity
    monkeypatch.setattr(app.settings, 'crawler_shared_exit_active_limit', 1)
    with closing(acquire(capacity, 0, pinned=pinned)):
        with pytest.raises(app.RouteCapacityBusy):
            acquire(capacity, 1, pinned=pinned)
        accounts.set_route_state(entries[1]['id'], state='challenge_paused',
            paused_until=time.time() + 3600, pause_reason='ACCOUNT_CHALLENGE')
    with pytest.raises(app.CollectionRouteError) as error:
        acquire(capacity, 1, pinned=pinned)
    assert not isinstance(error.value, app.RouteCapacityBusy)
    assert error.value.code == ('PROXY_REVISION_MISMATCH' if pinned else 'PROXY_UNAVAILABLE')


def test_waiting_pinned_route_rejects_changed_exit_generation(capacity, monkeypatch):
    accounts, entries = capacity
    monkeypatch.setattr(app.settings, 'crawler_shared_exit_active_limit', 1)
    pin = app.LocalDirectEgressPin(execution_id='pinned-job', owner_id='owner')
    def pinned_acquire():
        return app.acquire_route_execution(entries[1]['id'], 'pinned-job', 'owner',
            account_store=accounts, investigation_collection=True, direct_egress_pin=pin)
    with closing(pinned_acquire()):
        pass
    with closing(acquire(capacity, 0)):
        with pytest.raises(app.RouteCapacityBusy):
            pinned_acquire()
        accounts.observe_shared_exit('203.0.113.10', service_exit=True)
        accounts.observe_shared_exit('203.0.113.9', service_exit=True)
    with pytest.raises(app.CollectionRouteError, match='PROXY_REVISION_MISMATCH') as error:
        pinned_acquire()
    assert not isinstance(error.value, app.RouteCapacityBusy)
    with resources.proxy_edit_lease('service-direct') as editable:
        assert editable


@pytest.mark.parametrize('mode', ['legacy_direct', 'direct_degraded', 'proxy', 'proxy_two'])
def test_dry_capacity_matches_live_route_slots(capacity, monkeypatch, mode):
    from backend.audit_agent.account_rotation import AccountRotationManager
    from crawler_auth_fixtures import mark_camoufox_login_verified
    accounts, entries = capacity
    for account in entries:
        mark_camoufox_login_verified(accounts, account['id'])
    if mode in {'proxy', 'proxy_two'}:
        bind_proxy(capacity, monkeypatch, max_active_accounts=1 if mode == 'proxy' else 2)
    elif mode == 'direct_degraded':
        monkeypatch.setattr(app.settings, 'crawler_proxy_enabled', True)
        monkeypatch.setattr(app.settings, 'crawler_direct_degrade_enabled', True)
        observation = accounts.observe_shared_exit('203.0.113.9', service_exit=True)
        for account in entries:
            accounts.set_route_state(account['id'], state='active', route_mode='direct_degraded',
                shared_exit_key='203.0.113.9', generation=observation['shared_exit_generation'])
            accounts.mark_direct_validated(account['id'], observation['shared_exit_generation'])
    manager = AccountRotationManager(accounts)
    ids = {item['id'] for item in entries}
    with closing(acquire(capacity, 0)):
        assert manager.healthy_capacity_available('dy', 'owner', ids) == (mode != 'proxy')
        if mode != 'proxy':
            with closing(acquire(capacity, 1)):
                assert not manager.healthy_capacity_available('dy', 'owner', ids)
    assert manager.healthy_capacity_available('dy', 'owner', ids)


def test_dry_legacy_capacity_checks_shared_service_exit_without_account_binding(capacity):
    from backend.audit_agent.account_rotation import AccountRotationManager
    from crawler_auth_fixtures import mark_camoufox_login_verified
    accounts, entries = capacity
    for account in entries:
        mark_camoufox_login_verified(accounts, account['id'])
    accounts.observe_shared_exit('203.0.113.9', service_exit=True)
    assert not accounts.get(entries[0]['id'])['shared_exit_key']
    manager = AccountRotationManager(accounts)
    ids = {item['id'] for item in entries}
    with ExitStack() as stack:
        for index in range(2):
            held = stack.enter_context(closing(resources.ExecutionLeaseBundle('other', 'owner', 'other-' + str(index))))
            assert held.acquire() and held.acquire_exit_capacity('203.0.113.9')
        assert not manager.healthy_capacity_available('dy', 'owner', ids)
    assert manager.healthy_capacity_available('dy', 'owner', ids)
