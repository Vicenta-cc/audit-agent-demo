"""Recovery authorization and worker ownership, without external services."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from test_geographic_runtime_pipeline import confirmed
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from backend.investigation_creation.worker import InvestigationWorker
from backend.investigation_creation.contracts import RunStatus


def interrupted(confirmed, creation_stack, code='audit_job_stopped'):
    adapter, cfg = confirmed
    store = creation_stack['creation_store']
    run = store.claim_next('original')
    job_id = adapter.job_id_for_run(run.id)
    store.bind_job(run.id, run.claim_token, job_id)
    store.mark_interrupted(run.id, run.claim_token, error_code=code, error_message='paused')
    return store, adapter, run, job_id


def test_command_idempotency_single_claim_and_frozen_limit(confirmed, creation_stack):
    store, adapter, run, job = interrupted(confirmed, creation_stack)
    frozen = store.get_run_for_worker(run.id).confirmed_configuration
    with pytest.raises(ValueError, match='数量'):
        store.enqueue_local_resume(job, action='resume_crawl', request_key='bad', analyze_limit=999)
    assert store.enqueue_local_resume(job, action='resume_crawl', request_key='once')
    assert not store.enqueue_local_resume(job, action='resume_crawl', request_key='once')
    with pytest.raises(ValueError, match='同一'):
        store.enqueue_local_resume(job, action='resume_analysis', request_key='once')
    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(store.claim_next, ['w1', 'w2']))
    claim = next(c for c in claims if c)
    assert sum(c is not None for c in claims) == 1
    assert claim.confirmed_configuration == frozen
    assert store.take_local_resume(claim.id, claim.claim_token)['action'] == 'resume_crawl'
    assert store.take_local_resume(claim.id, claim.claim_token, start=True)
    assert store.take_local_resume(claim.id, claim.claim_token) is None
    store.mark_interrupted(claim.id, claim.claim_token, error_code='audit_job_stopped', error_message='unknown attempt')
    with pytest.raises(ValueError, match='尚未确定'):
        store.enqueue_local_resume(job, action='resume_crawl', request_key='again')


@pytest.mark.parametrize('code', ['collection_result_unknown', 'invalid_confirmed_configuration', 'geo_evaluation_failed'])
def test_unknown_failures_cannot_be_authorized(confirmed, creation_stack, code):
    store, adapter, run, job = interrupted(confirmed, creation_stack, code)
    with pytest.raises(ValueError, match='不支持'):
        store.enqueue_local_resume(job, action='resume_crawl', request_key='no')
    assert store.claim_next('worker') is None


def test_worker_executes_resume_under_claim_with_original_configuration(confirmed, creation_stack, monkeypatch):
    store, adapter, run, job = interrupted(confirmed, creation_stack)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='go')
    run = store.claim_next('worker')
    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    monkeypatch.setattr(worker, '_refresh_local_routes', Mock())
    monkeypatch.setattr(adapter, 'validate_m3_configuration', Mock())
    monkeypatch.setattr(worker, '_classify_job_result', lambda run, job: run)
    calls = []
    def execute(job_id, cfg):
        assert store.owns_claim(run.id, run.claim_token)
        assert store.take_local_resume(run.id, run.claim_token) is None
        assert cfg['_resume_action'] == 'resume_crawl'
        assert cfg['analyze_limit'] == run.confirmed_configuration['execution']['analyze_limit']
        calls.append(job_id)
    monkeypatch.setattr(adapter, 'run_pipeline', execute)
    worker._recover_execution(run)
    assert calls == [job]
    worker._refresh_local_routes.assert_called_once()
    with store._connect() as db:
        assert db.execute('SELECT state FROM investigation_resume_commands').fetchone()[0] == 'finished'


def test_stop_after_queued_resume_is_not_cleared_by_worker(confirmed, creation_stack, monkeypatch):
    store, adapter, run, job = interrupted(confirmed, creation_stack)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='go')
    store.cancel_queued_local_resume(job)
    adapter.job_store.update_control(job, stop_all_requested=True)
    run = store.claim_next('worker')
    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    monkeypatch.setattr(worker, '_refresh_local_routes', Mock())
    monkeypatch.setattr(adapter, 'validate_m3_configuration', Mock())
    execute = Mock()
    monkeypatch.setattr(adapter, 'run_pipeline', execute)
    result = worker._recover_execution(run)
    assert result.status == RunStatus.INTERRUPTED
    execute.assert_not_called()
    assert adapter.job_store.get(job)['status'] == 'stopped'
    assert adapter.job_store.get(job)['control']['stop_all_requested'] is False


def test_explicit_preflight_deferral_can_be_retried_again(confirmed, creation_stack, monkeypatch):
    from backend.audit_agent.geographic_recovery import GeographicCaptureDeferred
    store, adapter, run, job = interrupted(confirmed, creation_stack, 'geographic_preflight_deferred')
    store.enqueue_local_resume(job, action='resume_crawl', request_key='go')
    run = store.claim_next('worker')
    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    monkeypatch.setattr(worker, '_refresh_local_routes', Mock())
    monkeypatch.setattr(adapter, 'validate_m3_configuration', Mock())
    monkeypatch.setattr(adapter, 'run_pipeline', Mock(side_effect=GeographicCaptureDeferred('PROXY_BUSY')))
    result = worker._recover_execution(run)
    assert result.error_code == 'geographic_preflight_deferred'
    assert store.enqueue_local_resume(job, action='resume_crawl', request_key='again')


def test_active_analysis_resume_only_signals_existing_worker(confirmed, creation_stack, monkeypatch):
    from backend import main
    from fastapi import BackgroundTasks
    adapter, _ = confirmed
    store = creation_stack['creation_store']
    run = store.claim_next('active')
    job = adapter.job_id_for_run(run.id)
    store.bind_job(run.id, run.claim_token, job)
    adapter.job_store.update(job, status='analysis_paused', crawl_status='running', analysis_status='paused')
    adapter.job_store.update_control(job, analysis_paused=True)
    monkeypatch.setattr(main, '_authz_enabled', lambda: False)
    monkeypatch.setattr(main, 'job_store', adapter.job_store)
    monkeypatch.setattr(main, 'ingestion_store', adapter.ingestion_store)
    monkeypatch.setattr(main, 'investigation_creation_store', store)
    monkeypatch.setattr(main, '_require_job', lambda *a, **k: adapter.job_store.get(job))
    monkeypatch.setattr(main, 'enrich_job', lambda job: job)
    background = BackgroundTasks()
    main.control_job(job, main.JobControlRequest(action='resume_analysis'), background, idempotency_key='continue')
    assert not adapter.job_store.get(job)['control']['analysis_paused']
    assert background.tasks == []
    assert store.owns_claim(run.id, run.claim_token)


def test_resume_eligibility_includes_only_authorized_stale_candidates(monkeypatch):
    from backend import main
    from backend.investigation_creation.principal import Principal
    accounts = SimpleNamespace(available_accounts=lambda p: [],
        stale_route_candidates=lambda p: [{'id': 'mine'}, {'id': 'someone-else'}])
    monkeypatch.setattr(main, 'crawler_account_store', accounts)
    monkeypatch.setattr(main, '_authz_enabled', lambda: True)
    monkeypatch.setattr(main, '_can_use_crawler_account', lambda p, a: a['id'] == 'mine')
    assert main._authorized_available_crawler_accounts(Principal('owner'), 'dy') == []
    assert main._authorized_available_crawler_accounts(Principal('owner'), 'dy', allow_stale=True) == [{'id': 'mine'}]


def test_worker_prepare_failure_retains_queued_command_without_external_execution(confirmed, creation_stack, monkeypatch):
    store, adapter, run, job = interrupted(confirmed, creation_stack)
    before = adapter.job_store.get(job)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='go')
    assert adapter.job_store.get(job) == before
    run = store.claim_next('worker')
    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    monkeypatch.setattr(worker, '_refresh_local_routes', Mock())
    monkeypatch.setattr(adapter, 'validate_m3_configuration', Mock())
    execute = Mock()
    monkeypatch.setattr(adapter, 'run_pipeline', execute)
    monkeypatch.setattr(adapter.job_store, 'update_control', Mock(side_effect=RuntimeError('write failed')))
    result = worker._recover_execution(run)
    assert result.error_code == 'resume_preparation_failed'
    execute.assert_not_called()
    with store._connect() as db:
        assert db.execute('SELECT state FROM investigation_resume_commands').fetchone()[0] == 'queued'
    assert store.enqueue_local_resume(job, action='resume_crawl', request_key='retry')


def test_api_replay_precedes_changed_geographic_guard(confirmed, creation_stack, monkeypatch):
    from backend import main
    from fastapi import BackgroundTasks
    from backend.audit_agent import geographic_status
    store, adapter, run, job = interrupted(confirmed, creation_stack)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='once')
    monkeypatch.setattr(main, '_authz_enabled', lambda: False)
    monkeypatch.setattr(main, 'job_store', adapter.job_store)
    monkeypatch.setattr(main, 'ingestion_store', adapter.ingestion_store)
    monkeypatch.setattr(main, 'investigation_creation_store', store)
    monkeypatch.setattr(main, '_require_job', lambda *a, **k: adapter.job_store.get(job))
    monkeypatch.setattr(main, 'enrich_job', lambda job: job)
    guard = Mock(side_effect=AssertionError('replay must not revalidate changed state'))
    monkeypatch.setattr(geographic_status, 'geographic_resume_blocker', guard)
    background = BackgroundTasks()
    response = main.control_job(job, main.JobControlRequest(action='resume_crawl'), background, idempotency_key='once')
    assert response['recovery_status'] == 'accepted'
    guard.assert_not_called()
    assert background.tasks == []


def test_api_stop_all_settles_queued_recovery_without_pipeline(confirmed, creation_stack, monkeypatch):
    from backend import main
    from fastapi import BackgroundTasks
    store, adapter, run, job = interrupted(confirmed, creation_stack)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='once')
    monkeypatch.setattr(main, '_authz_enabled', lambda: False)
    monkeypatch.setattr(main, 'job_store', adapter.job_store)
    monkeypatch.setattr(main, 'ingestion_store', adapter.ingestion_store)
    monkeypatch.setattr(main, 'investigation_creation_store', store)
    monkeypatch.setattr(main, '_require_job', lambda *a, **k: adapter.job_store.get(job))
    monkeypatch.setattr(main, 'enrich_job', lambda job: job)
    main.control_job(job, main.JobControlRequest(action='stop_all'), BackgroundTasks())
    claim = store.claim_next('worker')
    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    execute = Mock()
    monkeypatch.setattr(adapter, 'run_pipeline', execute)
    result = worker._recover_execution(claim)
    assert result.status == RunStatus.INTERRUPTED
    execute.assert_not_called()
    stopped = adapter.job_store.get(job)
    assert stopped['status'] == stopped['crawl_status'] == stopped['analysis_status'] == 'stopped'
    assert not stopped['control']['stop_all_requested']


def test_queued_cancel_intent_survives_without_cross_database_signal(confirmed, creation_stack):
    store, adapter, run, job = interrupted(confirmed, creation_stack)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='once')
    before = adapter.job_store.get(job)
    signal = Mock(side_effect=AssertionError('queued cancellation must not write the other database'))
    store.cancel_queued_local_resume(job, action='stop_all', signal=signal)
    signal.assert_not_called()
    assert adapter.job_store.get(job) == before
    # Simulate process loss after the creation DB commit, before any Job signal.
    store = type(store)(store.db_path, clock=store.clock)
    claim = store.claim_next('worker')
    command = store.take_local_resume(claim.id, claim.claim_token)
    assert command['state'] == 'cancelled' and command['cancel_action'] == 'stop_all'
    assert store.take_local_resume(claim.id, claim.claim_token, start=True, prepare=signal) is None
    signal.assert_not_called()

    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    result = worker._recover_execution(claim)
    assert result.status == RunStatus.INTERRUPTED
    assert adapter.job_store.get(job)['status'] == 'stopped'


@pytest.mark.parametrize('action', ['stop_all', 'pause_crawl', 'pause_analysis', 'stop_analysis'])
def test_cancelled_resume_lost_claim_cannot_overwrite_successor_job(confirmed, creation_stack, monkeypatch, action):
    from datetime import timedelta
    from backend.investigation_creation.errors import LeaseLostError

    store, adapter, _, job = interrupted(confirmed, creation_stack)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='cancelled')
    store.cancel_queued_local_resume(job, action=action)
    old = store.claim_next('worker-a')
    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    finish = worker._finish_cancelled_resume
    successor_store = type(store)(store.db_path, clock=lambda: store.clock() + timedelta(seconds=301))
    successor_state = []

    def replace_then_finish(run):
        successor = successor_store.claim_next('worker-b')
        assert successor and successor.claim_token != old.claim_token
        adapter.job_store.update(job, status='running', crawl_status='running', analysis_status='running')
        adapter.job_store.update_control(job, stop_all_requested=True, crawl_epoch=12)
        successor_state.append(adapter.job_store.get(job))
        try:
            return finish(run)
        finally:
            assert adapter.job_store.get(job) == successor_state[0]

    monkeypatch.setattr(worker, '_finish_cancelled_resume', replace_then_finish)
    with pytest.raises(LeaseLostError):
        worker._recover_execution(old)
    assert adapter.job_store.get(job) == successor_state[0]
    assert successor_store.get_run_for_worker(old.id).claimed_by == 'worker-b'
    with store._connect() as db:
        assert db.execute('SELECT state FROM investigation_resume_commands').fetchone()[0] == 'cancelled'


def test_cancelled_resume_serializes_both_job_writes_against_claim_replacement(confirmed, creation_stack, monkeypatch):
    import sqlite3
    from datetime import timedelta

    store, adapter, _, job = interrupted(confirmed, creation_stack)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='cancelled')
    store.cancel_queued_local_resume(job, action='stop_all')
    run = store.claim_next('worker-a')
    successor_store = type(store)(store.db_path, clock=lambda: store.clock() + timedelta(seconds=301))
    connect = successor_store._connect

    def nonblocking_connect():
        db = connect()
        db.execute('PRAGMA busy_timeout=0')
        return db

    monkeypatch.setattr(successor_store, '_connect', nonblocking_connect)
    update_control = adapter.job_store.update_control
    blocked = []

    def finish_projection(job_id, **changes):
        assert adapter.job_store.get(job_id)['status'] == 'stopped'
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            successor_store.claim_next('worker-b')
        signal = Mock()
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            successor_store.cancel_queued_local_resume(job, action='stop_all', signal=signal)
        signal.assert_not_called()
        assert store.owns_claim(run.id, run.claim_token)
        blocked.append(True)
        return update_control(job_id, **changes)

    monkeypatch.setattr(adapter.job_store, 'update_control', finish_projection)
    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    result = worker._recover_execution(run)
    assert blocked == [True]
    assert result.status == RunStatus.INTERRUPTED
    assert adapter.job_store.get(job)['status'] == 'stopped'
    assert adapter.job_store.get(job)['control']['stop_all_requested'] is False


def test_cancelled_resume_uses_latest_durable_stop_action(confirmed, creation_stack, monkeypatch):
    store, adapter, _, job = interrupted(confirmed, creation_stack)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='cancelled')
    store.cancel_queued_local_resume(job, action='pause_crawl')
    run = store.claim_next('worker')
    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    finish = worker._finish_cancelled_resume

    def change_then_finish(run):
        store.cancel_queued_local_resume(job, action='stop_all')
        return finish(run)

    monkeypatch.setattr(worker, '_finish_cancelled_resume', change_then_finish)
    result = worker._recover_execution(run)
    assert result.status == RunStatus.INTERRUPTED
    stopped = adapter.job_store.get(job)
    assert stopped['status'] == stopped['crawl_status'] == stopped['analysis_status'] == 'stopped'


@pytest.mark.parametrize('mismatch', ['job_binding', 'command_started'])
def test_cancelled_resume_rechecks_binding_and_command_before_job_mutation(confirmed, creation_stack, mismatch):
    from backend.investigation_creation.errors import LeaseLostError

    store, adapter, _, job = interrupted(confirmed, creation_stack)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='cancelled')
    run = store.claim_next('worker')
    if mismatch == 'job_binding':
        store.cancel_queued_local_resume(job, action='stop_all')
        run = run.model_copy(update={'job_id': 'unbound-job'})
    else:
        assert store.take_local_resume(run.id, run.claim_token, start=True)
    before = adapter.job_store.get(job)
    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    with pytest.raises(LeaseLostError):
        worker._finish_cancelled_resume(run)
    assert adapter.job_store.get(job) == before
    assert adapter.job_store.get('unbound-job') is None


@pytest.mark.parametrize('when', ['before_run_interruption', 'after_run_interruption'])
def test_real_api_stop_after_cancelled_projection_settles_without_pipeline(confirmed, creation_stack, monkeypatch, when):
    from backend import main
    from fastapi import BackgroundTasks
    from backend.audit_agent.job_state import available_job_actions

    store, adapter, _, job = interrupted(confirmed, creation_stack)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='cancelled')
    store.cancel_queued_local_resume(job, action='pause_crawl')
    run = store.claim_next('worker')
    finish = store.finish_cancelled_local_resume
    monkeypatch.setattr(main, '_authz_enabled', lambda: False)
    monkeypatch.setattr(main, 'job_store', adapter.job_store)
    monkeypatch.setattr(main, 'ingestion_store', adapter.ingestion_store)
    monkeypatch.setattr(main, 'investigation_creation_store', store)
    monkeypatch.setattr(main, '_require_job', lambda *a, **k: adapter.job_store.get(job))
    monkeypatch.setattr(main, 'enrich_job', lambda value: value)
    background = BackgroundTasks()

    def stop():
        main.control_job(job, main.JobControlRequest(action='stop_all'), background)

    def stop_after_fence(*args, **kwargs):
        finish(*args, **kwargs)
        with store._connect() as db:
            assert db.execute('SELECT state FROM investigation_resume_commands').fetchone()[0] == 'finished'
        if when == 'before_run_interruption':
            stop()

    monkeypatch.setattr(store, 'finish_cancelled_local_resume', stop_after_fence)
    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    monkeypatch.setattr(adapter, 'run_pipeline', Mock(side_effect=AssertionError('cancelled recovery started pipeline')))
    assert worker._recover_execution(run).status == RunStatus.INTERRUPTED
    if when == 'after_run_interruption':
        stop()
    state = adapter.job_store.get(job)
    assert state['status'] == state['crawl_status'] == state['analysis_status'] == 'stopped'
    assert state['control']['stop_all_requested'] is False
    assert available_job_actions(state, {})['resume_crawl']
    assert background.tasks == []
    adapter.run_pipeline.assert_not_called()


def test_cancelled_projection_failure_keeps_command_retryable(confirmed, creation_stack, monkeypatch):
    store, adapter, _, job = interrupted(confirmed, creation_stack)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='cancelled')
    store.cancel_queued_local_resume(job, action='stop_all')
    run = store.claim_next('worker')
    update = adapter.job_store.update_control
    monkeypatch.setattr(adapter.job_store, 'update_control', Mock(side_effect=RuntimeError('write failed')))
    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    with pytest.raises(RuntimeError, match='write failed'):
        worker._recover_execution(run)
    with store._connect() as db:
        assert db.execute('SELECT state FROM investigation_resume_commands').fetchone()[0] == 'cancelled'
    assert store.owns_claim(run.id, run.claim_token)
    monkeypatch.setattr(adapter.job_store, 'update_control', update)
    assert worker._recover_execution(run).status == RunStatus.INTERRUPTED
    assert adapter.job_store.get(job)['status'] == 'stopped'


@pytest.mark.parametrize('state', ['started', 'finished'])
def test_newer_executed_resume_cannot_use_old_cancellation_quiescence(confirmed, creation_stack, state):
    store, adapter, _, job = interrupted(confirmed, creation_stack)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='cancelled')
    store.cancel_queued_local_resume(job, action='stop_all')
    run = store.claim_next('cancel-worker')
    worker = InvestigationWorker(store, execution_adapter=adapter,
        report_adapter=SimpleNamespace(), session_adapter=SimpleNamespace())
    worker._recover_execution(run)
    store.enqueue_local_resume(job, action='resume_crawl', request_key='new-resume')
    run = store.claim_next('new-worker')
    assert store.take_local_resume(run.id, run.claim_token, start=True)
    if state == 'finished':
        store.finish_local_resume(run.id, run.claim_token, 'new-resume')
    signal, settle = Mock(), Mock()
    store.cancel_queued_local_resume(job, action='stop_all', signal=signal, settle=settle)
    signal.assert_called_once()
    settle.assert_not_called()
