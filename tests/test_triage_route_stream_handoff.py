"""Ordinary triage keeps model work parallel without lending collection locks."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import backend.audit_agent.pipeline as pipeline_module
from backend.audit_agent.crawler_adapter import CrawlOutput
from backend.task_admission.resources import ExecutionLeaseBundle
from test_triage_account_rotation import RotationCrawler, _run_pipeline, env  # noqa: F401


@pytest.mark.parametrize('same_keyword', [False, True], ids=['rotated-keywords', 'two-picks-one-keyword'])
@pytest.mark.parametrize('analysis_control', ['', 'analysis_paused', 'analysis_stop_requested'])
def test_next_collection_finishes_while_first_model_is_running(env, monkeypatch, same_keyword, analysis_control):
    accounts = [env.add(name) for name in ('A', 'B')]
    accounts.sort(key=env.store.fair_key)
    for name, value in {
        'crawler_proxy_enabled': True,
        'task_resource_lock_dir': env.tmp / 'locks',
        'stream_crawl_analysis': True,
        'triage_accounts_per_task': 2,
        'crawler_shared_exit_active_limit': 1,
    }.items():
        monkeypatch.setattr(pipeline_module.settings, name, value)
    model_entered = threading.Event()
    second_detail_finished = threading.Event()
    release_model = threading.Event()
    bundles, detail_bundles, analyzed, model_routes, controller_errors = [], [], [], [], []
    observations = []

    def acquire(account_id, execution_id, owner_id, **kwargs):
        bundle = ExecutionLeaseBundle(execution_id, owner_id, account_id,
                                      SimpleNamespace(proxy_id='service-direct'))
        if not bundle.acquire(max_active_accounts=1) or not bundle.acquire_exit_capacity('203.0.113.9'):
            bundle.close()
            raise pipeline_module.CollectionRouteError('PROXY_BUSY')
        bundles.append(bundle)
        return bundle

    monkeypatch.setattr(pipeline_module, 'acquire_route_execution', acquire)

    class StreamingCrawler(RotationCrawler):
        def __init__(self):
            super().__init__()
            self.items = []

        def run_search(self, **kwargs):
            output = super().run_search(**kwargs)
            if same_keyword:
                output.contents.append({**output.contents[0], 'aweme_id': '词1-2'})
            return output

        def run_detail(self, platform, content_id, *, source_keyword, save_root=None, account_id='', **kwargs):
            self.detail_accounts.append((source_keyword, account_id))
            detail_bundles.append(bundles[-1])
            if len(self.detail_accounts) == 2:
                assert model_entered.wait(5), 'first subject never reached model analysis'
            item = {'aweme_id': content_id, 'desc': 'offline text', 'source_keyword': source_keyword,
                    'collection_route_provenance': {
                        'schema_version': 1, 'observation_id': 'offline-observation',
                        'source_task_id': 'job-rot', 'source_batch_id': 'offline-batch',
                        'collected_at': 1.0, 'platform': 'dy', 'source_account_id': account_id,
                        'route_mode': 'proxy_required', 'proxy_id': 'service-direct',
                        'binding_revision': 1, 'config_revision': 1,
                        'observed_exit_ip': '203.0.113.9', 'observed_exit_at': 1.0}}
            self.items.append(item)
            if callback := kwargs.get('content_callback'):
                callback([item], [])
            if len(self.items) == 2:
                observations.append((release_model.is_set(), detail_bundles[0]._borrowers,
                                     bool(detail_bundles[0].pass_fds)))
                second_detail_finished.set()
            return CrawlOutput(platform, [item], [], save_root)

        def _load_platform_output(self, save_root, platform):
            return CrawlOutput(platform, list(self.items), [], save_root)

    def analyze(self, subject):
        analyzed.append(subject.note_id)
        model_routes.append(getattr(self._media_route_context, 'value', None))
        if len(analyzed) == 1:
            if analysis_control:
                pipeline_module.job_store.update_control('job-rot', **{analysis_control: True})
            model_entered.set()
            assert release_model.wait(7), 'controller failed to release model'
        return {'note_id': subject.note_id, 'content_key': subject.note_id}

    monkeypatch.setattr(pipeline_module.AuditPipeline, '_analyze_subject', analyze)

    def controller():
        try:
            assert model_entered.wait(5)
            assert second_detail_finished.wait(3), 'next collection waited for model or failed acquiring its route'
        except BaseException as exc:
            controller_errors.append(str(exc))
        finally:
            if analysis_control == 'analysis_paused':
                pipeline_module.job_store.update_control('job-rot', analysis_paused=False)
            release_model.set()

    thread = threading.Thread(target=controller, daemon=True)
    thread.start()
    crawler = StreamingCrawler()
    error = None
    try:
        job = _run_pipeline(env, monkeypatch, crawler, accounts[0],
                            settings_auto_analyze=True, auto_analyze=True, analyze_limit=2,
                            max_total_notes=2, collect_media=False,
                            keyword='词1' if same_keyword else '词1,词2', max_notes=2 if same_keyword else 1)
    except pipeline_module.CollectionRouteError as exc:
        error = exc
        job = pipeline_module.job_store.get('job-rot')
    finally:
        release_model.set()
        thread.join(6)
    assert not thread.is_alive()
    assert not controller_errors, (controller_errors, str(error), job.get('error'))
    assert error is None
    assert observations == [(False, 0, False)], 'model must own no borrow; prior detail route must be closed'
    assert all(route is None for route in model_routes), 'analysis received live collection authority'
    expected_search = [('词1', accounts[0]['id'])]
    if not same_keyword:
        expected_search.append(('词2', accounts[1]['id']))
    assert crawler.search_accounts == expected_search
    assert len(crawler.detail_accounts) == 2
    assert all(not bundle.pass_fds for bundle in bundles)
    assert job.get('error') != 'PROXY_BUSY'
    if not analysis_control:
        assert len(analyzed) == len(job['items']) == 2


def test_route_operation_does_not_bypass_another_owners_account_lock(env, monkeypatch):
    account = env.add('busy-account')
    monkeypatch.setattr(pipeline_module.settings, 'crawler_proxy_enabled', True)
    monkeypatch.setattr(pipeline_module.settings, 'task_resource_lock_dir', env.tmp / 'locks')
    monkeypatch.setattr(pipeline_module, 'job_store', SimpleNamespace(get=lambda _: {}, control=lambda _: {}))
    other = ExecutionLeaseBundle('unrelated-job', 'other-owner', account['id'])
    assert other.acquire()
    attempts = []

    def acquire(account_id, execution_id, owner_id, **kwargs):
        attempts.append(account_id)
        bundle = ExecutionLeaseBundle(execution_id, owner_id, account_id)
        if not bundle.acquire():
            bundle.close()
            raise pipeline_module.CollectionRouteError('PROXY_BUSY')
        return bundle

    monkeypatch.setattr(pipeline_module, 'acquire_route_execution', acquire)
    pipeline = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id = 'waiting-job'
    try:
        with pytest.raises(pipeline_module.CollectionRouteError, match='PROXY_BUSY'):
            with pipeline._route_operation(account['id']):
                pytest.fail('entered route owned by another execution')
        assert attempts == [account['id']]
        assert other.pass_fds
    finally:
        other.close()
    with pipeline._route_operation(account['id']) as released:
        assert released.pass_fds
    assert not released.pass_fds
