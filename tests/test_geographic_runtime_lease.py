"""Same crawl epoch does not authorize a replaced geographic Run claim."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.audit_agent import pipeline as module
from backend.investigation_creation.errors import LeaseLostError


@pytest.fixture
def stale_pipeline(monkeypatch):
    pipeline = module.AuditPipeline.__new__(module.AuditPipeline)
    pipeline.job_id = 'same-job'
    pipeline.audit_config_revision_id = ''
    pipeline.ingestion = Mock()
    pipeline.audit_results = Mock()
    pipeline._geographic_lease_fence = Mock(side_effect=LeaseLostError('claim replaced'))
    monkeypatch.setattr(module.settings, 'app_auth_mode', 'disabled')
    return pipeline


def test_stale_claim_cannot_persist_audit(stale_pipeline):
    with pytest.raises(LeaseLostError):
        stale_pipeline._persist_audit_result(platform='dy', content_key='same',
                                            result={}, result_path=Path('unused'))
    stale_pipeline.audit_results.upsert_result.assert_not_called()


def test_stale_claim_cannot_mark_subject_failed(stale_pipeline):
    with pytest.raises(LeaseLostError):
        stale_pipeline._record_subject_failure('dy', 'same', None, RuntimeError('old failure'))
    stale_pipeline.ingestion.mark_content_status.assert_not_called()


def test_stale_claim_cannot_write_result_file(stale_pipeline, monkeypatch, tmp_path):
    monkeypatch.setattr(module.settings, 'outputs_dir', tmp_path)
    with pytest.raises(LeaseLostError):
        stale_pipeline._write_result_json('same', {})
    assert not list(tmp_path.rglob('*.json'))


@pytest.mark.parametrize('error', [RuntimeError('old failure'),
    module.AuditProviderUnavailableError('old provider'), module.AuditProviderCallError('old call')])
def test_claim_replaced_same_epoch_does_not_overwrite_job(stale_pipeline, monkeypatch, error):
    store = Mock()
    store.get.return_value = {'control': {'crawl_epoch': 7}}
    store.control.return_value = {'crawl_epoch': 7}
    monkeypatch.setattr(module, 'job_store', store)
    stale_pipeline._geographic_lease_fence.side_effect = None
    def replace_claim():
        store.reset_mock()
        stale_pipeline._geographic_lease_fence.side_effect = LeaseLostError('claim replaced')
        raise error
    stale_pipeline._verified_m3_snapshot = replace_claim
    with pytest.raises(LeaseLostError):
        stale_pipeline.run(SimpleNamespace(run_crawler=True, geographic_plan={'frozen': True}))
    store.update.assert_not_called()
    store.update_control.assert_not_called()

from test_geographic_runtime_pipeline import confirmed
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content


@pytest.mark.parametrize('status', ['completed', 'failed', 'analysis_paused'])
def test_replaced_real_claim_blocks_terminal_job_commit(confirmed, creation_stack, monkeypatch, status):
    from backend.investigation_creation.geographic_runtime import hold_geographic_claim
    adapter, config = confirmed
    binding = config['_verified_geographic_binding']
    store = creation_stack['creation_store']
    run = store.claim_next('old-worker', task_id=binding['run_id'])
    pipeline = module.AuditPipeline.__new__(module.AuditPipeline)
    pipeline.job_id = binding['job_id']
    pipeline.ingestion = adapter.ingestion_store
    pipeline._geographic_lease_guard = lambda: hold_geographic_claim(store, run.id, run.claim_token)
    monkeypatch.setattr(module, 'job_store', adapter.job_store)
    fenced = pipeline._fenced_job_store()
    fenced.update(pipeline.job_id, status='running')
    epoch = adapter.job_store.control(pipeline.job_id).get('crawl_epoch')
    with store._connect() as db:
        db.execute('UPDATE investigation_runs SET claim_token=? WHERE id=?', ('replacement', run.id))
    with pytest.raises(LeaseLostError):
        fenced.update(pipeline.job_id, status=status)
    assert adapter.job_store.get(pipeline.job_id)['status'] == 'running'
    assert adapter.job_store.control(pipeline.job_id).get('crawl_epoch') == epoch


def test_audit_upsert_executes_inside_atomic_claim_guard(stale_pipeline):
    from contextlib import contextmanager
    active = []
    @contextmanager
    def guard():
        active.append(True)
        try:
            yield
        finally:
            active.pop()
    stale_pipeline._geographic_lease_guard = guard
    def persist(**kwargs):
        assert active == [True]
        return {'saved': True}
    stale_pipeline.audit_results.upsert_result.side_effect = persist
    assert stale_pipeline._persist_audit_result(platform='dy', content_key='same',
        result={}, result_path=Path('unused')) == {'saved': True}
    assert active == []
