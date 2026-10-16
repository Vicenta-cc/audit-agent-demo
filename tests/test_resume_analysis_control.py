"""Durable resume must retain stop commands arriving after worker acceptance."""
from unittest.mock import Mock
import pytest

from backend.audit_agent import pipeline
from backend.audit_agent.ingestion import IngestionStore
from backend.audit_agent.job_store import JobStore


@pytest.mark.parametrize('flag,status', [
    ('analysis_paused', 'analysis_paused'),
    ('analysis_stop_requested', 'analysis_stopped'),
    ('stop_all_requested', 'stopped'),
])
@pytest.mark.parametrize('preserve', [True, False])
def test_resume_analysis_preserves_late_controls_only_when_requested(
        tmp_path, monkeypatch, flag, status, preserve):
    jobs = JobStore(tmp_path / 'audit.sqlite3')
    ingestion = IngestionStore(jobs.db_path)
    jobs.create(job_id='resume', platform='xhs', run_crawler=False)
    jobs.update('resume', crawl_status='skipped')
    monkeypatch.setattr(pipeline, 'job_store', jobs)
    monkeypatch.setattr(pipeline.settings, 'app_auth_mode', 'disabled')
    monkeypatch.setattr(pipeline.settings, 'outputs_dir', tmp_path / 'outputs')
    instance = object.__new__(pipeline.AuditPipeline)
    instance.job_id = 'resume'
    instance.ingestion = ingestion
    instance._verified_m3_snapshot = Mock(return_value=None)
    instance._resume_source_root = Mock(return_value=tmp_path)
    instance._rule_snapshot_from_source = Mock(return_value={})
    instance._set_prompt_context = Mock()
    instance._analysis_media_scope = Mock(return_value='all')
    instance._build_subjects = Mock(return_value=[])
    instance._analyze_subject = Mock(side_effect=AssertionError('unexpected external analysis'))
    def pending(*args, **kwargs):
        # Simulate a new user stop after resume began, before its former reset.
        jobs.update_control('resume', **{flag: True})
        return [{'content_key': 'post', 'item': {'note_id': 'post'}, 'comments': []}]
    monkeypatch.setattr(ingestion, 'pending_for_task', pending)
    if preserve:
        instance.resume_pending_analysis(preserve_control=True)
        assert jobs.control('resume')[flag] is True
        assert jobs.get('resume')['status'] == status
        instance._build_subjects.assert_not_called()
    else:
        instance.resume_pending_analysis()  # Old callers retain their explicit resume semantics.
        assert jobs.control('resume')[flag] is False
        instance._build_subjects.assert_called_once()
    instance._analyze_subject.assert_not_called()
