"""Local FD lifecycle tests: no crawler, model, proxy or network calls."""
from contextlib import contextmanager
from types import SimpleNamespace
import threading

import pytest

from backend.audit_agent.pipeline import (
    _tracked_geographic_route_borrow, _wait_for_geographic_route_borrows,
)
from backend.audit_agent.crawler_route import CollectionRouteError
from backend.task_admission.resources import ExecutionLeaseBundle
from backend.investigation_creation.errors import LeaseLostError


@pytest.fixture
def leases(tmp_path, monkeypatch):
    from backend.audit_agent.config import settings
    monkeypatch.setattr(settings, 'crawler_browser_profile_root', tmp_path / 'profiles')
    monkeypatch.setattr(settings, 'task_resource_lock_dir', tmp_path / 'locks')
    bundles = []
    def new(account='account-one'):
        bundle = ExecutionLeaseBundle('same-job', 'owner', account,
            snapshot=SimpleNamespace(proxy_id='same-proxy'))
        bundles.append(bundle)
        return bundle
    yield new
    for bundle in bundles:
        bundle.close()


def test_next_capture_waits_for_its_analysis_borrow_before_single_slot_acquire(leases):
    source = leases()
    assert source.acquire(max_active_accounts=1)
    pending = []
    borrow = _tracked_geographic_route_borrow(source.borrow(), pending)
    borrow.__enter__()
    source.close()
    assert source.pass_fds
    blocked = leases('other-account')
    assert not blocked.acquire(max_active_accounts=1)  # Same proxy's one slot remains held.
    entered_wait, acquired = threading.Event(), threading.Event()
    errors = []
    def next_capture():
        try:
            _wait_for_geographic_route_borrows(pending, stop=lambda: False, fence=entered_wait.set)
            following = leases()
            assert following.acquire(max_active_accounts=1)
            following.close()
            acquired.set()
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=next_capture)
    thread.start()
    try:
        assert entered_wait.wait(2)
        assert not acquired.is_set()
        assert source.pass_fds  # Waiting never releases an in-use route.
        borrow.__exit__(None, None, None)
        thread.join(2)
        assert not thread.is_alive() and not errors
        assert acquired.is_set() and not pending and not source.pass_fds
    finally:
        if source.pass_fds:
            borrow.__exit__(None, None, None)
        thread.join(2)


@pytest.mark.parametrize('reason', ['cancel', 'pause', 'analysis_stop', 'analysis_error', 'lease_lost'])
def test_wait_interrupts_without_releasing_an_in_use_route(leases, reason):
    source = leases()
    assert source.acquire()
    pending = []
    borrow = _tracked_geographic_route_borrow(source.borrow(), pending)
    borrow.__enter__()
    source.close()
    error = LeaseLostError('lost') if reason == 'lease_lost' else RuntimeError('analysis failed')
    def fence():
        if reason in {'lease_lost', 'analysis_error'}:
            raise error
    expected = type(error) if reason in {'lease_lost', 'analysis_error'} else CollectionRouteError
    try:
        with pytest.raises(expected) as caught:
            _wait_for_geographic_route_borrows(pending, stop=lambda: reason == 'cancel', fence=fence,
                analysis_suspended=lambda: reason in {'pause', 'analysis_stop'})
        if reason in {'lease_lost', 'analysis_error'}:
            assert caught.value is error
        else:
            assert caught.value.code == 'SUPPLEMENT_DEFERRED'
        assert source.pass_fds and not pending[0].is_set()
    finally:
        borrow.__exit__(None, None, None)
    assert not source.pass_fds and pending[0].is_set()


def test_analysis_or_enqueue_exception_finishes_tracked_borrow(leases):
    source = leases()
    assert source.acquire()
    pending = []
    with pytest.raises(RuntimeError, match='failed'):
        with _tracked_geographic_route_borrow(source.borrow(), pending):
            source.close()
            raise RuntimeError('failed')
    assert pending[0].is_set() and not source.pass_fds


def test_release_failure_never_signals_slot_available():
    pending = []
    @contextmanager
    def broken():
        yield object()
        raise RuntimeError('release failed')
    with pytest.raises(RuntimeError, match='release failed'):
        with _tracked_geographic_route_borrow(broken(), pending):
            pass
    assert not pending[0].is_set()


def test_no_geographic_borrows_does_not_wait_on_unrelated_work():
    def unexpected():
        raise AssertionError('no owned borrow to wait for')
    _wait_for_geographic_route_borrows([], stop=unexpected, fence=unexpected,
        analysis_suspended=unexpected)


from test_account_safety_fencing import safety


@pytest.mark.parametrize('outcome', ['success', 'exit_changed', 'revoke', 'lease_lost', 'denied', 'unleased', 'risk_paused'])
def test_geographic_stale_exit_refresh_preserves_outer_lease_and_authority(
        safety, tmp_path, monkeypatch, outcome):
    import time
    from backend.audit_agent import pipeline, crawler_route
    from backend.task_admission.resources import account_lease, acquire_account_handle
    accounts, account, other = safety
    monkeypatch.setattr(pipeline.settings, 'app_auth_mode', 'disabled')
    monkeypatch.setattr(pipeline.settings, 'crawler_browser_profile_root', tmp_path / 'profiles')
    monkeypatch.setattr(pipeline.settings, 'task_resource_lock_dir', tmp_path / 'locks')
    monkeypatch.setattr(pipeline.settings, 'request_scheduler_db', tmp_path / 'scheduler.sqlite3')
    with accounts._connect() as db:
        db.execute('UPDATE crawler_shared_exits SET observed_at=?',
            (time.time() - pipeline.settings.crawler_exit_observation_ttl - 10,))
    if outcome == 'risk_paused':
        accounts.pause_account_exit(account['id'], until=time.time() + 60)
    assert accounts.available_accounts('dy') == []
    monkeypatch.setattr(pipeline, 'crawler_account_store', accounts)
    monkeypatch.setattr(pipeline, 'job_store', SimpleNamespace(get=lambda _: {'owner_user_id': 'owner'}))
    allowed = set() if outcome == 'denied' else {account['id']}
    monkeypatch.setattr(pipeline, '_authorized_crawler_account_ids', lambda _: allowed)
    instance = object.__new__(pipeline.AuditPipeline)
    instance.job_id = 'geographic-refresh'
    checks, probes, operations = [], [], []
    def fence():
        checks.append('fence')
        if outcome == 'lease_lost' and probes:
            raise LeaseLostError('lost during refresh')
    instance._assert_geographic_lease = fence
    @contextmanager
    def business_route(account_id, **kwargs):
        assert account_id in {row['id'] for row in accounts.available_accounts('dy')}
        operations.append(account_id)
        yield 'business-route'
    instance._route_operation = business_route
    contract = crawler_route.route_contract()
    def probe(bundle):
        probes.append(bundle.account_id)
        assert bundle.account_id == account['id'] and bundle.owner_id == 'owner'
        assert bundle.pass_fds and acquire_account_handle('dy', account['id']) is None
        with accounts._connect() as db:
            assert db.execute('SELECT state FROM crawler_exit_refreshes WHERE account_id=?',
                (account['id'],)).fetchone()[0] == 'claimed'
        if outcome == 'revoke':
            allowed.clear()
        return contract.ProbeReceipt(bundle.snapshot.proxy_id, bundle.snapshot.config_revision,
            'probe', '203.0.113.99' if outcome == 'exit_changed' else '203.0.113.10', time.time())
    monkeypatch.setattr(crawler_route, '_run_maintenance_probe', probe)
    with account_lease('dy', account['id']) as held:
        assert held
        leased = set() if outcome == 'unleased' else {account['id']}
        if outcome == 'success':
            with instance._geographic_route_operation('dy', account['id'], leased) as route:
                assert route == 'business-route'
            assert probes == operations == [account['id']]
            assert len(checks) == 3
        else:
            error = (PermissionError if outcome in {'denied', 'unleased', 'revoke'} else
                     LeaseLostError if outcome == 'lease_lost' else CollectionRouteError)
            with pytest.raises(error):
                with instance._geographic_route_operation('dy', account['id'], leased):
                    pytest.fail('blocked account reached business collection')
            assert not operations
            assert probes == ([] if outcome in {'denied', 'unleased', 'risk_paused'} else [account['id']])
        # Refresh duplicated/reentered the outer lease; it did not release it.
        assert acquire_account_handle('dy', account['id']) is None
    handle = acquire_account_handle('dy', account['id'])
    assert handle is not None
    handle.close()
