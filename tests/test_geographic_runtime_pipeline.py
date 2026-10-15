"""Real freeze/bridge/state/ingestion, replacing external crawler/model only."""
from copy import deepcopy
from pathlib import Path
import pytest
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_geographic_runtime_freeze import _adapter, _command
from test_geographic_execution_plan import make_authority
from test_unified_draft_configuration import P


@pytest.fixture
def confirmed(creation_stack, content, monkeypatch, tmp_path):
    from backend.audit_agent.config import settings
    from backend.investigation_creation.geographic_runtime import IsolatedGeographicCapability
    monkeypatch.setattr(settings, 'triage_mode', 'select')
    authority = make_authority(creation_stack, content)
    app = creation_stack['app_service']
    IsolatedGeographicCapability(tmp_path).install(app)
    run = app.confirm_and_queue(_command(authority['identity']), principal=P)
    adapter = _adapter(creation_stack)
    adapter.ensure_job(run)
    return adapter, adapter.verify_run_job(run)


class Model:
    def __init__(self): self.calls = []
    def audit_text(self, prompt, **kw):
        self.calls.append((prompt, kw))
        if 'suspicion' in prompt:
            return dict(suspicion='strong', risk_score=85, reason='offline fixed', matched_terms=[])
        return {'status': 'located', 'locations': [{'field': 'title', 'place': '北京市' if '北京市欠薪' in prompt else '克拉玛依市'}]}


class Crawler:
    def __init__(self, events, change=False): self.events, self.change = events, change
    def run_search(self, **kw):
        from backend.audit_agent.crawler_adapter import CrawlOutput
        self.events.append(('capture', kw))
        assert not kw.get('content_callback') and not kw['stream_items'] and not kw['collect_media']
        item = dict(aweme_id='same', title='克拉玛依市欠薪', desc='原文', follower_count=1)
        return CrawlOutput('dy', [item], [dict(aweme_id='same', comment_id='c', content='欠薪')], kw['save_root'])
    def run_detail(self, **kw):
        from backend.audit_agent.crawler_adapter import CrawlOutput
        self.events.append(('detail', kw))
        # Early callback must remain inert, even with unauthorized data.
        kw['content_callback']([dict(aweme_id='early', title='其他地方')], [])
        item = dict(aweme_id='same', title='克拉玛依市欠薪', desc='精采原文' if self.change else '原文', follower_count=1)
        return CrawlOutput('dy', [item], [], kw['save_root'])


def runtime(confirmed, tmp_path, *, events=None, crawler=None, fence=lambda db: True, stop=lambda:False):
    from backend.audit_agent.geographic_pipeline import GeographicPipeline
    adapter, cfg = confirmed
    events = [] if events is None else events
    model = Model()
    instance = GeographicPipeline(plan=cfg['geographic_plan'], binding=cfg['_verified_geographic_binding'],
        crawler=crawler or Crawler(events, change=True), ingestion=adapter.ingestion_store,
        state_path=adapter.job_store.db_path, save_root=tmp_path/'crawl', risk_client=model, geo_client=model,
        accounts=[dict(id='test-account', auth_state={})], lease_fence=fence, stop=stop,
        on_delivery=lambda refs: events.append(('delivery', refs)))
    return instance, events, model


def test_formal_capture_barrier_detail_review_durable_delivery_and_restart(confirmed, tmp_path):
    runner, events, model = runtime(confirmed, tmp_path)
    report, output = runner.run()
    assert report['handed_off'] == 1 and report['reserved'] == 0
    assert report['unfinished_queries'] == []
    assert [x['aweme_id'] for x in output.contents] == ['same']
    assert output.contents[0]['desc'] == '精采原文'
    kinds = [e[0] for e in events]
    assert kinds.count('capture') == len(runner.bridge.plan['queries'])
    assert max(i for i,k in enumerate(kinds) if k == 'capture') < kinds.index('detail') < kinds.index('delivery')
    adapter, cfg = confirmed
    memberships, refs = adapter.ingestion_store.validated_selection_for_task(cfg['_verified_geographic_binding']['job_id'])
    assert len(memberships) == 1 and refs[0]['item']['desc'] == '精采原文'
    saved = runner.execution.snapshot()
    assert all(o['risk']['input']['item']['desc'] == '原文' for q in saved['queries'].values() for o in q['occurrences'])
    before = len(model.calls)
    second, _, other_model = runtime(confirmed, tmp_path, events=events)
    assert second.run()[0] == report
    assert other_model.calls == [] and len(model.calls) == before
    assert [e[0] for e in events].count('detail') == 1


def test_runtime_uses_frozen_parameters_and_rejects_stale_fence(confirmed, tmp_path):
    active = [True]
    runner, events, _ = runtime(confirmed, tmp_path, fence=lambda db:active[0])
    active[0] = False
    with pytest.raises(ValueError, match='lease'):
        runner.run()
    assert events == []


def test_partial_stopped_capture_never_reaches_detail(confirmed, tmp_path):
    stopped = [False]
    events = []
    class Partial(Crawler):
        def run_search(self, **kw):
            result = super().run_search(**kw)
            stopped[0] = True
            return result
    runner, _, _ = runtime(confirmed, tmp_path, events=events, crawler=Partial(events), stop=lambda:stopped[0])
    with pytest.raises(RuntimeError, match='incomplete'):
        runner.run()
    assert all(e[0] == 'capture' for e in events)
    assert runner.execution.snapshot()['state']['total_success'] == 0


def test_detail_changed_outside_region_never_creates_membership(confirmed, tmp_path):
    events=[]
    class Outside(Crawler):
        def run_detail(self, **kw):
            result = super().run_detail(**kw)
            result.contents[0]['title'] = '北京市欠薪'
            return result
    runner, _, _ = runtime(confirmed, tmp_path, events=events, crawler=Outside(events))
    report, output = runner.run()
    assert report['collected'] == 1 and report['handed_off'] == 0
    assert 'collected_not_delivered' in report['reasons'] and output.contents == []
    assert not any(e[0] == 'delivery' for e in events)


def test_detail_auth_failure_cannot_restart_outer_account_sweep(confirmed, tmp_path):
    from backend.audit_agent.crawler_adapter import CrawlerAuthenticationError
    events = []
    retired = []
    class Expired(Crawler):
        def run_search(self, **kw):
            result = super().run_search(**kw)
            result.contents.append({**result.contents[0], 'aweme_id': 'second-content'})
            return result
        def run_detail(self, **kw):
            events.append(('detail', kw))
            raise CrawlerAuthenticationError('test-only expired account')
    runner, _, _ = runtime(confirmed, tmp_path, events=events, crawler=Expired(events))
    runner.accounts['second'] = dict(id='second', auth_state={})
    runner.retire = lambda account, error: retired.append(account['id'])
    with pytest.raises(CrawlerAuthenticationError) as caught:
        runner.run()
    assert caught.value.triage_rotation_stop is True
    assert sorted(retired) == ['second', 'test-account']
    assert [e[0] for e in events].count('detail') == 2
    assert runner.execution.snapshot()['state']['total_success'] == 0
