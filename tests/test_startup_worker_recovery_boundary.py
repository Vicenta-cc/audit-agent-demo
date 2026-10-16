"""API restarts recover legacy work without stealing durable worker leases."""
import ast
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.audit_agent.ingestion import IngestionStore
from backend.audit_agent.job_store import JobStore
from test_geographic_runtime_pipeline import confirmed
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content


def startup_function(**services):
    # Execute the actual startup body without importing main's live global stores.
    source = Path(__file__).resolve().parents[1] / 'backend/main.py'
    node = next(node for node in ast.parse(source.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == 'recover_interrupted_jobs')
    node.decorator_list = []
    namespace = dict(services)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['recover_interrupted_jobs']


@pytest.mark.parametrize('auth_enabled', [False, True])
@pytest.mark.parametrize('binding', ['unbound', 'live', 'expired'])
def test_api_restart_only_recovers_legacy_jobs_and_analysis(
        confirmed, creation_stack, tmp_path, auth_enabled, binding):
    adapter, _ = confirmed
    store = creation_stack['creation_store']
    run = store.claim_next('worker-original')
    assert run is not None
    job_id = adapter.job_id_for_run(run.id)
    jobs = adapter.job_store
    ingestion = adapter.ingestion_store
    if binding != 'unbound':
        run = store.bind_job(run.id, run.claim_token, job_id)
    if binding == 'expired':
        now = store.clock()
        store.clock = lambda: now + timedelta(seconds=600)
    assert store.manages_job(job_id)
    assert not store.manages_job('legacy-task')
    jobs.create(job_id='legacy-task', run_crawler=True)
    for task in (job_id, 'legacy-task'):
        jobs.update(task, status='running', crawl_status='running', analysis_status='running',
            control={'crawl_epoch': 7, 'analysis_paused': True,
                     'execution_account': {'id': 'fixed-account'}})
        batch = tmp_path / f'{task}.json'
        batch.write_text(json.dumps({'task_id': task, 'platform': 'dy', 'keyword': '测试',
            'items': [{'aweme_id': 'shared-post', 'desc': 'shared content'}], 'comments': []}))
        refs = ingestion.ingest_batch(batch, tmp_path / 'raw')
        ingestion.mark_content_status('dy', refs[0]['content_key'], 'analyzing', task_id=task)
    before_job = jobs.get(job_id)
    before_run = store.get_run_for_worker(run.id)
    with ingestion._connect() as db:
        before_content = dict(db.execute('SELECT * FROM contents').fetchone())
        before_task = dict(db.execute('SELECT * FROM task_contents WHERE task_id=?', (job_id,)).fetchone())
    startup = startup_function(
        crawler_login_v2_manager=SimpleNamespace(recover_interrupted=Mock(), operations=SimpleNamespace(prune=Mock())),
        settings=SimpleNamespace(crawler_login_v2_operation_retention_days=7),
        audit_result_store=SimpleNamespace(delete_for_archived_jobs=lambda: 0),
        job_store=jobs, ingestion_store=ingestion, investigation_creation_store=store,
        _authz_enabled=lambda: auth_enabled,
        investigation_turn_executor=SimpleNamespace(recover=lambda: {}),
        investigation_creation_turn_executor=SimpleNamespace(recover=lambda: {}),
    )
    startup()
    assert jobs.get(job_id) == before_job
    assert store.get_run_for_worker(run.id) == before_run
    assert jobs.get('legacy-task')['status'] == 'interrupted'
    assert ingestion.stats_for_task('legacy-task')['queued_analysis_count'] == 1
    with ingestion._connect() as db:
        assert dict(db.execute('SELECT * FROM contents').fetchone()) == before_content
        assert dict(db.execute('SELECT * FROM task_contents WHERE task_id=?', (job_id,)).fetchone()) == before_task
    if binding == 'expired':
        recovered = store.claim_next('worker-recovery', lease_timeout_seconds=300)
        assert recovered.id == run.id and recovered.recovery_required
        assert recovered.claim_token != run.claim_token
    else:
        assert store.claim_next('worker-other') is None


def test_ownership_lookup_failure_rolls_back_legacy_recovery(tmp_path):
    jobs = JobStore(tmp_path / 'jobs.sqlite3')
    ingestion = IngestionStore(jobs.db_path)
    jobs.create(job_id='legacy', run_crawler=True)
    jobs.update('legacy', status='running', crawl_status='running')
    batch = tmp_path / 'batch.json'
    batch.write_text(json.dumps({'task_id': 'legacy', 'platform': 'dy', 'keyword': '测试',
        'items': [{'aweme_id': 'one', 'desc': 'content'}], 'comments': []}))
    refs = ingestion.ingest_batch(batch, tmp_path / 'raw')
    ingestion.mark_content_status('dy', refs[0]['content_key'], 'analyzing', task_id='legacy')
    before = jobs.get('legacy')
    def unavailable(job_id):
        raise RuntimeError('ownership database unavailable')
    with pytest.raises(RuntimeError, match='ownership database'):
        jobs.recover_interrupted_jobs(is_worker_managed=unavailable)
    with pytest.raises(RuntimeError, match='ownership database'):
        ingestion.reset_all_analyzing(is_worker_managed=unavailable)
    assert jobs.get('legacy') == before
    assert ingestion.stats_for_task('legacy')['analyzing_count'] == 1
