"""Recovery retains exact evidence and budgets; unknown effects stay blocked."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from audit_agent.crawler_route import CollectionRouteError
from audit_agent.geographic_recovery import GeographicCaptureDeferred, snapshot_resume_blocker
from test_scoped_execution import Harness
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_geographic_runtime_pipeline import confirmed, runtime


@pytest.mark.parametrize('code', ['PROXY_BUSY', 'SUPPLEMENT_DEFERRED'])
def test_second_query_preflight_deferral_preserves_first_delivery_and_resumes_once(tmp_path, code):
    harness = Harness(tmp_path)
    capture = harness.capture
    blocked = [True]
    def defer_second(plan, query):
        if query['query_id'] == 'B' and blocked[0]:
            raise GeographicCaptureDeferred(code)
        return capture(plan, query)
    harness.capture = defer_second
    with pytest.raises(GeographicCaptureDeferred):
        harness.runner().run()
    saved = harness.runner().snapshot()
    assert saved['queries']['B']['status'] == 'paused'
    assert saved['queries']['B']['error']['stage'] == 'before_capture'
    assert saved['state']['total_success'] == 1
    assert len(saved['deliveries']) == 1
    assert snapshot_resume_blocker(saved) == ''
    first = deepcopy(saved['queries']['A'])
    blocked[0] = False
    result = harness.runner().run()
    assert result['handed_off'] == result['collected'] == 2
    assert not result['unfinished_queries']
    assert harness.runner().snapshot()['queries']['A'] == first
    assert harness.calls.count(('capture', 'A')) == 1
    assert harness.calls.count(('detail', 'x')) == harness.calls.count(('deliver', 'x')) == 1
    assert harness.runner().run() == result


def test_detail_preflight_deferral_keeps_same_reservation_without_spending_budget(tmp_path):
    harness = Harness(tmp_path)
    detail = harness.detail
    blocked = [True]
    def defer_detail(*args, **kwargs):
        if blocked[0]:
            raise GeographicCaptureDeferred('PROXY_BUSY')
        return detail(*args, **kwargs)
    harness.detail = defer_detail
    with pytest.raises(GeographicCaptureDeferred):
        harness.runner().run()
    saved = harness.runner().snapshot()
    assert saved['state']['total_reserved'] == 1 and saved['state']['total_success'] == 0
    assert [a['status'] for a in saved['attempts'].values()] == ['deferred']
    assert not saved['excluded_occurrences']
    assert snapshot_resume_blocker(saved) == ''
    blocked[0] = False
    result = harness.runner().run()
    assert result['collected'] == result['handed_off'] == 2 and result['reserved'] == 0
    assert harness.calls.count(('capture', 'A')) == harness.calls.count(('detail', 'x')) == 1
    final = harness.runner().snapshot()
    assert len(final['attempts']) == 3  # Deferred attempt remains in the audit trail.
    with pytest.raises(ValueError, match='external results'):
        settled = next(a for a in final['attempts'].values() if a['status'] == 'settled')
        harness.store.defer_attempt(harness.plan['execution_id'], settled['attempt_id'], 'bad-defer', 'PROXY_BUSY')


@pytest.mark.parametrize('stage', ['capture', 'detail'])
def test_unclassified_route_error_never_becomes_automatic_retry(tmp_path, stage):
    harness = Harness(tmp_path)
    def unknown(*args, **kwargs):
        raise CollectionRouteError('PROXY_BUSY')
    setattr(harness, stage, unknown)
    with pytest.raises(CollectionRouteError):
        harness.runner().run()
    saved = harness.runner().snapshot()
    assert snapshot_resume_blocker(saved)
    result = harness.runner().run()
    assert result['handed_off'] == 0
    assert 'query_failed' in result['reasons'] or 'awaiting_reconciliation' in result['reasons']


def test_old_failed_capture_is_not_reclassified_from_error_text(tmp_path):
    harness = Harness(tmp_path)
    runner = harness.runner()
    harness.store.record_query(harness.plan['execution_id'], 'A', 'failed', [], 'old-failure',
                              error={'type': 'CollectionRouteError', 'stage': 'capture'})
    assert snapshot_resume_blocker(runner.snapshot()) == 'geographic_capture_reconciliation_required'
    assert runner.run()['reasons'] == ['query_failed']
    assert harness.calls == []


def test_geographic_adapter_only_marks_route_entry_not_body_or_cleanup():
    from audit_agent.geographic_pipeline import GeographicPipeline
    runner = object.__new__(GeographicPipeline)
    @contextmanager
    def entry(account):
        raise CollectionRouteError('PROXY_BUSY')
        yield
    runner.route_operation = entry
    with pytest.raises(GeographicCaptureDeferred):
        with runner._acquire_route('a'):
            pytest.fail('network body reached')
    @contextmanager
    def normal(account):
        yield None
    runner.route_operation = normal
    with pytest.raises(CollectionRouteError) as error:
        with runner._acquire_route('a'):
            raise CollectionRouteError('PROXY_BUSY')
    assert type(error.value) is CollectionRouteError

    @contextmanager
    def cleanup(account):
        yield None
        raise CollectionRouteError('PROXY_BUSY')
    runner.route_operation = cleanup
    with pytest.raises(CollectionRouteError) as error:
        with runner._acquire_route('a'):
            pass
    assert type(error.value) is CollectionRouteError


def test_resume_qualification_reads_snapshot_not_optimistic_projection(confirmed, tmp_path):
    from backend.audit_agent.geographic_status import geographic_resume_blocker
    runner, _, _ = runtime(confirmed, tmp_path)
    jobs = confirmed[0].job_store
    job_id = runner.bridge.plan['runtime_binding']['job_id']
    qid = runner.bridge.plan['queries'][0]['query_id']
    runner.store.record_query(runner.execution.execution_id, qid, 'paused', [], 'deferral',
                             error={'stage': 'before_capture', 'code': 'PROXY_BUSY'})
    jobs.update_control(job_id, geographic_selection=runner.execution.report())
    job = jobs.get(job_id)
    assert geographic_resume_blocker(job, jobs.db_path) == ''
    runner.store.record_query(runner.execution.execution_id, qid, 'failed', [], 'unknown',
                             error={'stage': 'capture', 'type': 'CollectionRouteError'})
    # The Job projection still says paused; durable execution is authoritative.
    assert geographic_resume_blocker(job, jobs.db_path) == 'geographic_capture_reconciliation_required'
    assert geographic_resume_blocker({**job, 'id': 'other-job'}, jobs.db_path) == 'geographic_state_reconciliation_required'
    job['control']['geographic_selection']['plan_digest'] = 'wrong'
    assert geographic_resume_blocker(job, jobs.db_path) == 'geographic_state_reconciliation_required'


@pytest.mark.parametrize('run_status,query_status', [
    ('RUNNING', 'capturing'),
    ('INTERRUPTED', 'capturing'),
    ('FAILED', 'capturing'),
    ('INTERRUPTED', 'paused'),
])
def test_projector_only_checks_reconciliation_when_not_running(
    confirmed, tmp_path, monkeypatch, run_status, query_status,
):
    import backend.audit_agent.geographic_status as geographic_status
    from backend.investigation_creation.adapters import InvestigationRunProjector
    from backend.investigation_creation.contracts import RunStatus

    runner, _, _ = runtime(confirmed, tmp_path)
    adapter, _ = confirmed
    jobs = adapter.job_store
    job_id = runner.bridge.plan['runtime_binding']['job_id']
    qid = runner.bridge.plan['queries'][0]['query_id']
    error = {'stage': 'before_capture', 'code': 'PROXY_BUSY'} if query_status == 'paused' else None
    runner.store.record_query(runner.execution.execution_id, qid, query_status, [],
                             'projection-state', error=error)
    jobs.update_control(job_id, geographic_selection=runner.execution.report())
    # Even a stale Job view offering resume must not enable it for a live Run.
    jobs.update(job_id, status='crawl_paused')
    before = deepcopy(runner.execution.snapshot())
    calls = []
    guard = geographic_status.geographic_resume_blocker

    def checked_guard(job, path):
        assert run_status != 'RUNNING', 'live Run must not probe resume reconciliation'
        calls.append(job['id'])
        return guard(job, path)

    monkeypatch.setattr(geographic_status, 'geographic_resume_blocker', checked_guard)
    projector = InvestigationRunProjector(
        job_store=jobs, ingestion_store=adapter.ingestion_store,
        audit_result_store=adapter.audit_result_store,
        crawler_account_store=adapter.crawler_account_store,
        report_store=SimpleNamespace(),
    )
    result = projector.project(SimpleNamespace(
        job_id=job_id, status=RunStatus(run_status), report_version_id='',
    ))
    expected_blocker = (
        'geographic_capture_reconciliation_required'
        if run_status != 'RUNNING' and query_status == 'capturing' else ''
    )
    assert result['recovery_blocker'] == expected_blocker
    assert calls == ([] if run_status == 'RUNNING' else [job_id])
    if run_status == 'RUNNING' or expected_blocker:
        assert result['available_actions']['resume_crawl'] is False
        assert result['task_stats']['collection_resumable'] is False
    assert runner.execution.snapshot() == before


@pytest.mark.parametrize('job_status', ['completed', 'crawl_paused'])
def test_published_projection_never_loads_resume_snapshot(
    confirmed, tmp_path, monkeypatch, job_status,
):
    import backend.audit_agent.geographic_status as geographic_status
    from backend.investigation_creation.adapters import InvestigationRunProjector
    from backend.investigation_creation.contracts import RunStatus

    runner, _, _ = runtime(confirmed, tmp_path)
    adapter, _ = confirmed
    jobs = adapter.job_store
    job_id = runner.bridge.plan['runtime_binding']['job_id']
    jobs.update(job_id, status=job_status)

    def unexpected_resume_probe(*args):
        pytest.fail('completed collection must not load the geographic recovery snapshot')

    monkeypatch.setattr(geographic_status, 'geographic_resume_blocker', unexpected_resume_probe)
    projector = InvestigationRunProjector(
        job_store=jobs, ingestion_store=adapter.ingestion_store,
        audit_result_store=adapter.audit_result_store,
        crawler_account_store=adapter.crawler_account_store,
        report_store=SimpleNamespace(),
    )
    result = projector.project(SimpleNamespace(
        job_id=job_id, status=RunStatus.PUBLISHED, report_version_id='',
    ))
    assert result['recovery_blocker'] == ''
    assert result['available_actions']['resume_crawl'] is False
    assert result['task_stats']['collection_resumable'] is False
