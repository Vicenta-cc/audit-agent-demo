"""Browser startup failures preserve unfinished keywords and the existing recovery path."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.audit_agent import pipeline as module
from backend.audit_agent.crawler_adapter import (
    MediaCrawlerAdapter, CrawlerBrowserInitializationError, CrawlerVerificationError,
    CrawlerRateLimitError, CrawlerAuthenticationError,
)
from backend.audit_agent.job_state import failure_metadata, available_job_actions
from backend.investigation_creation.contracts import RunStatus
from test_keyword_media_failure import run, crawler
from test_crawler_adapter_output_handoff import handoff
from test_crawler_collection_failures import write_handoff_failure
from test_task_admission import stack, make_run, worker_for
from test_investigation_creation_m3 import FakeExecutionAdapter, FakeReportAdapter


@pytest.mark.parametrize('exit_code', [0, 1])
@pytest.mark.parametrize('marker,expected', [
    ('BROWSER_INITIALIZATION_FAILED: local_storage_unavailable', CrawlerBrowserInitializationError),
    ('BROWSER_INITIALIZATION_FAILED: x\nCOLLECTION_INCOMPLETE', CrawlerBrowserInitializationError),
    ('BROWSER_INITIALIZATION_FAILED: x\nACCOUNT_VERIFY', CrawlerVerificationError),
    ('BROWSER_INITIALIZATION_FAILED: x\nPLATFORM_RATE_LIMITED', CrawlerRateLimitError),
])
def test_subprocess_marker_and_platform_signal_priority(handoff, exit_code, marker, expected):
    write_handoff_failure(handoff, marker, exit_code)
    with pytest.raises(expected):
        handoff.run()
    assert handoff.events.index('final') < handoff.events.index('cleanup')


@pytest.mark.parametrize('stage', ['candidate', 'detail'])
def test_unfinished_word_is_retried_on_resume_without_account_penalty(tmp_path, monkeypatch, stage):
    monkeypatch.setattr(module.settings, 'triage_mode', 'select')
    monkeypatch.setattr(module.job_store, 'log', lambda *a, **k: None)
    accounts = SimpleNamespace(get=lambda _: None, mark_expired=Mock(), mark_risk=Mock())
    monkeypatch.setattr(module, 'crawler_account_store', accounts)
    first = crawler({'词B': CrawlerBrowserInitializationError()} if stage == 'detail' else None)
    search = first.run_search
    if stage == 'candidate':
        def fail(**kwargs):
            if kwargs['keyword'] == '词B':
                raise CrawlerBrowserInitializationError()
            return search(**kwargs)
        first.run_search = fail
    emitted = []
    with pytest.raises(CrawlerBrowserInitializationError):
        run(first, tmp_path, lambda c, m: emitted.extend(c))
    assert [x['aweme_id'] for x in emitted] == ['a1']
    assert not (tmp_path/'keyword_failures.json').exists()  # not a skipped download
    accounts.mark_expired.assert_not_called()
    accounts.mark_risk.assert_not_called()
    second = crawler(existing_rows=['a1'])
    result = run(second, tmp_path)
    assert [c['keyword'] for c in second.search_calls] == ['词B']
    assert {c['aweme_id'] for c in result.contents} == {'a1', 'b1'}


@pytest.mark.parametrize('completed', [0, 1])
def test_worker_keeps_task_recoverable_without_automatic_empty_or_partial_report(stack, completed):
    creation, _, (owner, _) = stack
    task = make_run(creation, owner, 'browser-blocked')
    error = str(CrawlerBrowserInitializationError())
    state = {**FakeExecutionAdapter.completed_state(ingested=completed, completed=completed),
             'status': 'failed', 'crawl_status': 'failed', 'analysis_status': 'completed' if completed else 'idle',
             'run_crawler': True, 'error': error,
             'control': {'failure': failure_metadata('crawler_browser_initialization_failed', error)}}
    execution, reports = FakeExecutionAdapter(state), FakeReportAdapter()
    worker_for(creation, execution, reports).run_once()
    saved = creation.get_run_for_worker(task.id)
    assert saved.status == RunStatus.INTERRUPTED
    assert saved.error_code == 'crawler_browser_initialization_failed'
    assert creation.admission.for_task(task.id)['state'] == 'RESERVED'
    assert reports.generate_calls == 0
    actions = available_job_actions(state, state['task_stats'])
    assert actions['resume_crawl']
    assert actions['finish_collection'] == bool(completed)
