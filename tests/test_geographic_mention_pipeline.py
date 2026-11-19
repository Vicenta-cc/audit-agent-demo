"""New confirmations through real stores, replacing only external collection/model."""
from copy import deepcopy
import pytest
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_geographic_execution_plan import make_authority
from test_geographic_runtime_freeze import _adapter, _command
from test_geographic_runtime_pipeline import runtime
from test_unified_draft_configuration import P


@pytest.fixture
def confirmed(creation_stack, content, monkeypatch, tmp_path, request):
    from backend.audit_agent.config import settings
    from backend.investigation_creation.geographic_runtime import IsolatedGeographicCapability
    from backend.audit_agent import geographic_capture_state
    monkeypatch.setattr(settings, 'triage_mode', 'select')
    if getattr(request, 'param', None):
        import test_unified_draft_configuration as draft_helpers
        body = draft_helpers.lexicon()
        body['entries'].append(dict(id='variant-2', term='第二搜索词', kind='variant', parent_id='main-1'))
        monkeypatch.setattr(draft_helpers, 'lexicon', lambda: deepcopy(body))
    authority = make_authority(creation_stack, content)
    app = creation_stack['app_service']
    if getattr(request, 'param', None):
        app.resource_service.task_settings.save(request.param, 0)
    IsolatedGeographicCapability(tmp_path).install(app)
    command = _command(authority['identity'])
    if getattr(request, 'param', None):
        command = command.model_copy(update={'expected_task_settings_revision': 1})
    run = app.confirm_and_queue(command, principal=P)
    adapter = _adapter(creation_stack)
    adapter.ensure_job(run)
    monkeypatch.setattr(geographic_capture_state, 'prepare_continuation',
        lambda scope_id, query_id, batch_id: dict(state_path=str(tmp_path/'continuation.sqlite'),
            scope_id=scope_id, query_id=query_id, batch_id=batch_id))
    return adapter, adapter.verify_run_job(run)


class Crawler:
    def __init__(self, batches, *, exhausted=False, detail_empty=False):
        self.batches, self.exhausted, self.detail_empty = batches, exhausted, detail_empty
        self.searches, self.details, self.items = [], [], {}

    def run_search(self, **kw):
        from backend.audit_agent.crawler_adapter import CrawlOutput
        self.searches.append(kw)
        items, comments = deepcopy(self.batches[len(self.searches)-1])
        self.items.update({item['aweme_id']: item for item in items})
        continuation = {k: v for k,v in kw['geographic_continuation'].items() if k != 'state_path'}
        continuation.update(status='complete', count=len(items), exhausted=self.exhausted)
        return CrawlOutput('dy', items, comments, kw['save_root'], continuation=continuation)

    def run_detail(self, **kw):
        from backend.audit_agent.crawler_adapter import CrawlOutput
        self.details.append(kw)
        items = [] if self.detail_empty else [self.items[kw['content_id']]]
        return CrawlOutput('dy', items, [], kw['save_root'])


def post(key, title='外地欠薪', **fields):
    return dict(aweme_id=key, title=title, desc='原始正文', follower_count=1, **fields)


def setup(confirmed, tmp_path, crawler):
    runner, events, model = runtime(confirmed, tmp_path, crawler=crawler)
    class ForbiddenGeo:
        def audit_text(self, *a, **kw):
            pytest.fail('new geographic strategy must not invoke AI')
    runner.bridge.geo_client = ForbiddenGeo()
    return runner, model


@pytest.mark.parametrize(('item', 'comments', 'tier'), [
    (post('p', '独山子欠薪'), [], 'local'),
    (post('p', '新疆欠薪'), [], 'province_unknown'),
    (post('p'), [dict(aweme_id='p', comment_id='c', content='伊犁也有')], 'province_unknown'),
    (post('p', author_ip_location='新疆'), [], 'ip_only'),
])
def test_tiers_stop_on_first_eligible_batch_and_resume_without_calls(confirmed, tmp_path, item, comments, tier):
    crawler = Crawler([([item], comments)])
    runner, model = setup(confirmed, tmp_path, crawler)
    report, output = runner.run()
    assert len(crawler.searches) == len(crawler.details) == 1
    assert report['handed_off'] == 1 and not report['unfinished_queries']
    assert report['queries'][0]['round_index'] == 1
    assert report['queries'][0]['end_reason'] in ('selected', 'task_limit')
    assert len(output.contents) == 1 and len(model.calls) == 1
    from backend.audit_agent.geographic_delivery import delivery_basis_for_job
    adapter, cfg = confirmed
    basis, = delivery_basis_for_job(adapter.ingestion_store, cfg['_verified_geographic_binding']['job_id'])
    assert basis['geo_tier'] == tier
    restarted, other_model = setup(confirmed, tmp_path, crawler)
    assert restarted.run()[0] == report
    assert not other_model.calls and len(crawler.searches) == len(crawler.details) == 1


def test_third_batch_hit_keeps_risk_input_and_account_and_avoids_full_snapshot(confirmed, tmp_path, monkeypatch):
    batches = [([post(str(i), '独山子欠薪' if i == 3 else '外地欠薪')], []) for i in range(1,4)]
    crawler = Crawler(batches)
    runner, model = setup(confirmed, tmp_path, crawler)
    runner.accounts['second'] = dict(id='second', auth_state={})
    monkeypatch.setattr(runner.store, 'load', lambda *a, **kw: pytest.fail('hot full snapshot load'))
    report, output = runner.run()
    assert len(crawler.searches) == 3 and len(model.calls) == 3
    assert {kw['account_id'] for kw in crawler.searches} == {'test-account'}
    assert len({kw['geographic_continuation']['batch_id'] for kw in crawler.searches}) == 3
    assert len({kw['geographic_continuation']['query_id'] for kw in crawler.searches}) == 1
    assert report['queries'][0]['round_index'] == 3
    assert report['queries'][0]['end_reason'] == 'selected'
    assert output.contents[0]['aweme_id'] == '3'
    assert all('原始正文' in prompt for prompt, _ in model.calls)


@pytest.mark.parametrize('exhausted,count,reason', [(False,3,'max_rounds'), (True,1,'source_exhausted')])
def test_no_match_stops_at_fixed_bound(confirmed, tmp_path, exhausted, count, reason):
    crawler = Crawler([([post(str(i))], []) for i in range(count)], exhausted=exhausted)
    runner, _ = setup(confirmed, tmp_path, crawler)
    report, output = runner.run()
    assert len(crawler.searches) == count and not crawler.details and not output.contents
    assert not report['unfinished_queries']
    assert report['queries'][0]['end_reason'] == reason


def test_empty_detail_does_not_refill_from_next_batch(confirmed, tmp_path):
    crawler = Crawler([([post('p', '乌尔禾欠薪')], [])], detail_empty=True)
    runner, _ = setup(confirmed, tmp_path, crawler)
    report, output = runner.run()
    assert len(crawler.searches) == len(crawler.details) == 1
    assert not output.contents and report['queries'][0]['end_reason'] == 'selected'


def test_weak_risk_is_eligible_in_ip_tier(confirmed, tmp_path):
    crawler = Crawler([([post('p', author_ip_location='新疆')], [])])
    runner, _ = setup(confirmed, tmp_path, crawler)
    class Weak:
        def audit_text(self, *a, **kw):
            return dict(suspicion='weak', risk_score=40, reason='offline fixed', matched_terms=[])
    runner.bridge.risk_client = Weak()
    assert runner.run()[0]['handed_off'] == 1


@pytest.mark.parametrize('stage', ['after_capture', 'after_reserve', 'after_receipt'])
def test_interrupted_after_capture_reuses_frozen_batch(confirmed, tmp_path, stage):
    crawler = Crawler([([post('p', '独山子欠薪')], [])])
    runner, model = setup(confirmed, tmp_path, crawler)
    def interrupt(report):
        if report['stage'] == stage:
            raise RuntimeError('checkpoint interruption')
    runner.on_progress = interrupt
    with pytest.raises(RuntimeError, match='checkpoint interruption'):
        runner.run()
    restarted, _ = setup(confirmed, tmp_path, crawler)
    assert restarted.run()[0]['handed_off'] == 1
    assert len(crawler.searches) == len(crawler.details) == 1


@pytest.mark.parametrize('mixed_tiers', [True, False])
def test_tier_priority_then_original_risk_rank(confirmed, tmp_path, mixed_tiers):
    items = ([post('province', '新疆欠薪'), post('local', '独山子欠薪')]
             if mixed_tiers else [post('first', '独山子欠薪'), post('second', '独山子欠薪')])
    crawler = Crawler([(items, [])])
    runner, model = setup(confirmed, tmp_path, crawler)
    if mixed_tiers:
        class Risk:
            def audit_text(self, prompt, **kw):
                return dict(suspicion='strong' if '新疆欠薪' in prompt else 'weak',
                            risk_score=95 if '新疆欠薪' in prompt else 20,
                            reason='tier priority regression', matched_terms=[])
        runner.bridge.risk_client = Risk()
    report, output = runner.run()
    assert report['handed_off'] == 1
    # Original engine breaks equal-score ties by later original rank.
    assert output.contents[0]['aweme_id'] == ('local' if mixed_tiers else 'second')
    snapshot = runner.execution.snapshot()
    occurrences = next(q['occurrences'] for q in snapshot['queries'].values() if q['occurrences'])
    assert [o['content_key'] for o in occurrences] == (['province', 'local'] if mixed_tiers else ['second', 'first'])


@pytest.mark.parametrize('confirmed', [{'max_total_notes': 2}], indirect=True)
def test_cross_keyword_duplicate_still_scores_new_input(confirmed, tmp_path):
    crawler = Crawler([([post('same', '独山子欠薪')], [])] * 3, exhausted=True)
    runner, model = setup(confirmed, tmp_path, crawler)
    logical = {q['logical_query_id'] for q in runner.bridge.plan['queries']}
    assert len(logical) >= 2
    report, output = runner.run()
    assert report['handed_off'] == len(output.contents) == 1
    assert len(crawler.details) == 1
    assert len(model.calls) == len(crawler.searches) == len(logical)
    rows = [o for q in runner.execution.snapshot()['queries'].values() for o in q['occurrences']]
    assert len({o['risk']['input']['search_keyword'] for o in rows}) == len(logical)
    assert len({o['risk']['input_digest'] for o in rows}) == len(logical)


def test_new_capture_auth_failure_does_not_rotate_or_advance(confirmed, tmp_path, monkeypatch):
    from backend.audit_agent.crawler_adapter import CrawlerAuthenticationError
    crawler = Crawler([])
    calls = []
    def fail(**kw):
        calls.append(kw)
        raise CrawlerAuthenticationError('expired session')
    monkeypatch.setattr(crawler, 'run_search', fail)
    runner, model = setup(confirmed, tmp_path, crawler)
    monkeypatch.setattr(runner, 'rotate', lambda *a, **kw: pytest.fail('must not rotate persisted search session'))
    with pytest.raises(CrawlerAuthenticationError):
        runner.run()
    assert len(calls) == 1 and not model.calls
    restarted, _ = setup(confirmed, tmp_path, crawler)
    restarted.run()
    assert len(calls) == 1
