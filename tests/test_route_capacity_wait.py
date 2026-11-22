"""Capacity contention may wait; uncertain collection and safety failures may not."""
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from backend.audit_agent.crawler_route import CollectionRouteError, RouteCapacityBusy
from backend.audit_agent.route_capacity_wait import acquire_with_capacity_wait


class Bundle:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def test_capacity_release_allows_progress_without_replaying_body():
    now, notifications, calls = [0.], [], []
    bundle = Bundle()

    def acquire():
        calls.append(now[0])
        if now[0] < .5:
            raise RouteCapacityBusy('exit_capacity')
        return bundle

    result = acquire_with_capacity_wait(acquire, check=lambda: None,
        on_wait=notifications.append, max_wait=2, clock=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
    assert result is bundle and bundle.closed == 0
    assert calls == [0., .25, .5]
    assert notifications == ['exit_capacity', '']
    result.close()


@pytest.mark.parametrize('code', ['PROXY_BUSY', 'PROXY_REVISION_MISMATCH',
                                  'PROXY_EXIT_CHANGED', 'PROXY_UNAVAILABLE'])
def test_untyped_busy_or_security_failure_never_retries(code):
    calls = []

    def acquire():
        calls.append(True)
        raise CollectionRouteError(code)

    with pytest.raises(CollectionRouteError, match=code):
        acquire_with_capacity_wait(acquire, check=lambda: None,
            on_wait=lambda _: pytest.fail('not a capacity wait'), max_wait=10,
            sleep=lambda _: pytest.fail('must not retry'))
    assert len(calls) == 1


def test_capacity_wait_timeout_is_bounded_and_observable():
    now, calls, notifications = [0.], [], []

    def acquire():
        calls.append(now[0])
        raise RouteCapacityBusy('route_capacity')

    with pytest.raises(RouteCapacityBusy) as error:
        acquire_with_capacity_wait(acquire, check=lambda: None,
            on_wait=notifications.append, max_wait=.4, clock=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
    assert error.value.wait_timed_out is True
    assert now[0] == pytest.approx(.4)
    assert calls == [0., .25]
    assert notifications == ['route_capacity', '']


def test_revoked_execution_while_waiting_never_attempts_again():
    revoked, calls, notifications = [False], [], []

    def check():
        if revoked[0]:
            raise PermissionError('execution revoked')

    def acquire():
        calls.append(True)
        raise RouteCapacityBusy('route_capacity')

    with pytest.raises(PermissionError, match='revoked'):
        acquire_with_capacity_wait(acquire, check=check,
            on_wait=notifications.append, max_wait=10,
            sleep=lambda _: revoked.__setitem__(0, True))
    assert len(calls) == 1
    assert notifications == ['route_capacity', '']


def test_late_success_after_stop_closes_before_handoff():
    stopped, bundle = [False], Bundle()

    def check():
        if stopped[0]:
            raise CollectionRouteError('SUPPLEMENT_DEFERRED')

    def acquire():
        stopped[0] = True
        return bundle

    with pytest.raises(CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
        acquire_with_capacity_wait(acquire, check=check, on_wait=lambda _: None, max_wait=10)
    assert bundle.closed == 1


def test_late_success_after_wait_deadline_closes_before_handoff():
    now, calls, bundle = [0.], [], Bundle()

    def acquire():
        calls.append(True)
        if len(calls) == 1:
            raise RouteCapacityBusy('exit_capacity')
        now[0] = 2.
        return bundle

    with pytest.raises(RouteCapacityBusy) as error:
        acquire_with_capacity_wait(acquire, check=lambda: None, on_wait=lambda _: None,
            max_wait=1, clock=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
    assert error.value.wait_timed_out and bundle.closed == 1


def test_failed_wait_status_update_closes_unhanded_bundle():
    calls, bundle = [], Bundle()

    def acquire():
        calls.append(True)
        if len(calls) == 1:
            raise RouteCapacityBusy('exit_capacity')
        return bundle

    def notify(reason):
        if not reason:
            raise OSError('status unavailable')

    with pytest.raises(OSError, match='status unavailable'):
        acquire_with_capacity_wait(acquire, check=lambda: None, on_wait=notify,
            max_wait=10, sleep=lambda _: None)
    assert bundle.closed == 1


@pytest.fixture
def pipeline_route(monkeypatch):
    from backend.audit_agent import pipeline as module
    state = {}
    store = SimpleNamespace(get=lambda _: {'owner_user_id': 'owner'},
        control=lambda _: state, log=lambda *a, **kw: None,
        update_control=lambda _, **kw: state.update(kw))
    monkeypatch.setattr(module, 'job_store', store)
    monkeypatch.setattr(module, 'crawler_account_store', SimpleNamespace(get=lambda _: {'platform': 'dy'}))
    monkeypatch.setattr(module, 'get_current_route_context', lambda: None)
    monkeypatch.setattr(module, 'route_execution_context', lambda _: nullcontext())
    instance = module.AuditPipeline.__new__(module.AuditPipeline)
    instance.job_id = 'job'
    instance._direct_egress_pin = object()
    return module, instance, state


def test_pipeline_never_retries_capacity_error_from_body(pipeline_route, monkeypatch):
    module, instance, _ = pipeline_route
    calls, bundle = [], Bundle()

    def acquire(*a, **kw):
        calls.append(True)
        return bundle

    monkeypatch.setattr(module, 'acquire_route_execution', acquire)
    with pytest.raises(RouteCapacityBusy):
        with instance._route_operation('a'):
            raise RouteCapacityBusy('exit_capacity')
    assert len(calls) == 1 and bundle.closed == 1


def test_nested_owner_never_waits_for_its_own_capacity(pipeline_route, monkeypatch):
    from backend.audit_agent import route_capacity_wait
    module, instance, _ = pipeline_route
    current = SimpleNamespace(account_id='other', snapshot=object())
    monkeypatch.setattr(module, 'get_current_route_context', lambda: current)
    monkeypatch.setattr(route_capacity_wait, 'acquire_with_capacity_wait',
                        lambda *a, **kw: pytest.fail('nested acquisition must not wait'))

    def busy(*a, **kw):
        raise RouteCapacityBusy('route_capacity')

    monkeypatch.setattr(module, 'acquire_route_execution', busy)
    with pytest.raises(RouteCapacityBusy):
        with instance._route_operation('a'):
            pytest.fail('capacity unavailable')


def test_pipeline_stop_during_acquire_prevents_collection(pipeline_route, monkeypatch):
    module, instance, state = pipeline_route
    bundle = Bundle()

    def acquire(*a, **kw):
        state['crawl_stop_requested'] = True
        return bundle

    monkeypatch.setattr(module, 'acquire_route_execution', acquire)
    with pytest.raises(CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
        with instance._route_operation('a'):
            pytest.fail('stop raced admission')
    assert bundle.closed == 1


def test_pipeline_capacity_timeout_preserves_diagnostic(pipeline_route, monkeypatch):
    module, instance, state = pipeline_route
    monkeypatch.setattr(module.settings, 'crawler_route_capacity_wait_seconds', 0)

    def busy(*a, **kw):
        raise RouteCapacityBusy('exit_capacity')

    monkeypatch.setattr(module, 'acquire_route_execution', busy)
    with pytest.raises(RouteCapacityBusy):
        with instance._route_operation('a'):
            pytest.fail('capacity unavailable')
    assert state['route_capacity_waiting'] is False
    assert state['route_capacity_wait_timed_out'] is True
    assert state['route_capacity_wait_reason'] == 'exit_capacity'


def test_worker_heartbeat_continues_while_capacity_waits():
    import threading
    from backend.investigation_creation.worker import _LeaseHeartbeat

    # Release capacity only after the real heartbeat thread has made progress.
    # This detects a wait that prevents the lease keeper from running.
    available, beats = threading.Event(), []
    bundle = Bundle()

    def heartbeat(run_id, token):
        assert (run_id, token) == ('run', 'claim')
        beats.append(True)
        if len(beats) >= 3:
            available.set()
        return True

    store = SimpleNamespace(heartbeat=heartbeat, owns_claim=lambda *a: True)

    def acquire():
        if not available.is_set():
            raise RouteCapacityBusy('route_capacity')
        return bundle

    with _LeaseHeartbeat(store, 'run', 'claim', interval_seconds=.05) as lease:
        result = acquire_with_capacity_wait(acquire, check=lease.assert_owned,
            on_wait=lambda _: None, max_wait=5)
    assert result is bundle and len(beats) >= 3
    result.close()
