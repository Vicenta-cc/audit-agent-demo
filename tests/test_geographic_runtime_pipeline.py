"""Real freeze/bridge/state/ingestion, replacing external crawler/model only."""
from contextlib import nullcontext
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
        route_operation=lambda account_id: nullcontext(None),
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


def test_approved_detail_media_is_found_by_audit_and_after_restart(confirmed, tmp_path):
    from backend.audit_agent.pipeline import AuditPipeline
    events = []
    class WithMedia(Crawler):
        def run_detail(self, **kw):
            output = super().run_detail(**kw)
            folder = kw['save_root'] / 'douyin' / 'videos' / 'same'
            folder.mkdir(parents=True)
            (folder / 'video.mp4').write_bytes(b'\x00\x00\x00\x18ftypmp42')
            output.contents[0].update(video_download_url='https://remote.test/video.mp4',
                                      note_download_url='https://remote.test/cover.jpg')
            return output
    def make_runner():
        runner, _, _ = runtime(confirmed, tmp_path, events=events, crawler=WithMedia(events))
        runner.root = tmp_path / 'crawler' / 'geographic'
        runner.sink.raw_root = runner.root / 'delivered'
        return runner
    runner = make_runner()
    report, output = runner.run()
    assert report['handed_off'] == 1
    # Earlier failed attempts and other content must never become audit inputs.
    failed = runner.root / 'details' / 'failed' / 'douyin' / 'videos' / 'same'
    failed.mkdir(parents=True)
    (failed / 'bad.mp4').write_bytes(b'\x00\x00\x00\x18ftypmp42')
    def subject():
        pipeline = AuditPipeline.__new__(AuditPipeline)
        pipeline.job_id = runner.bridge.plan['runtime_binding']['job_id']
        pipeline.rule_snapshot = {}
        pipeline.ingestion = runner.ingestion
        return pipeline._build_subjects('dy', output.contents, [], tmp_path)[0]
    first = subject()
    assert len(first.local_video_paths) == 1
    assert '/failed/' not in first.local_video_paths[0]
    assert first.video_urls == first.image_urls == []
    assert make_runner().run()[0] == report
    assert subject().local_video_paths == first.local_video_paths
    assert [kind for kind, _ in events].count('detail') == 1
    # The approved ledger cannot be reused for another post's files.
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = runner.bridge.plan['runtime_binding']['job_id']
    pipeline.ingestion = runner.ingestion
    assert pipeline._find_local_media(tmp_path, 'dy', 'unapproved', 'videos', {'.mp4'}) == []
    # A symlink under the approved attempt may not escape this task's media root.
    video = Path(first.local_video_paths[0])
    outside = tmp_path.parent / (tmp_path.name + '-outside.mp4')
    outside.write_bytes(video.read_bytes())
    try:
        video.unlink()
        video.symlink_to(outside)
        with pytest.raises(ValueError):
            subject()
    finally:
        outside.unlink()


def test_unapproved_detail_media_is_not_discovered(confirmed, tmp_path):
    from backend.audit_agent.pipeline import AuditPipeline
    events = []
    class Outside(Crawler):
        def run_detail(self, **kw):
            output = super().run_detail(**kw)
            output.contents[0]['title'] = '北京市欠薪'
            folder = kw['save_root'] / 'douyin' / 'videos' / 'same'
            folder.mkdir(parents=True)
            (folder / 'video.mp4').write_bytes(b'\x00\x00\x00\x18ftypmp42')
            return output
    runner, _, _ = runtime(confirmed, tmp_path, crawler=Outside(events))
    runner.root = tmp_path / 'crawler' / 'geographic'
    runner.sink.raw_root = runner.root / 'delivered'
    assert runner.run()[0]['handed_off'] == 0
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = runner.bridge.plan['runtime_binding']['job_id']
    pipeline.ingestion = runner.ingestion
    assert pipeline._find_local_media(tmp_path, 'dy', 'same', 'videos', {'.mp4'}) == []


@pytest.mark.parametrize('changes', [
    {'attempt_id': '../escape'}, {'attempt_id': ''}, {'job_id': 'other-job'},
    {'content_key': 'other-post'}, {'platform': 'xhs'},
])
def test_geographic_media_binding_must_match_exact_task_and_safe_attempt(tmp_path, changes):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from backend.audit_agent.pipeline import AuditPipeline
    geographic = tmp_path / 'crawler' / 'geographic'
    geographic.mkdir(parents=True)
    binding = dict(job_id='job', platform='dy', content_key='same', attempt_id='approved') | changes
    ref = dict(platform='dy', content_key='same', geographic_binding=binding,
               raw_item_path=str(geographic / 'delivered' / 'receipt.json'))
    validated = Mock(return_value=([], [ref]))
    pipeline = AuditPipeline.__new__(AuditPipeline)
    pipeline.job_id = 'job'
    pipeline.ingestion = SimpleNamespace(validated_selection_for_task=validated)
    with pytest.raises(ValueError, match='binding'):
        pipeline._find_local_media(tmp_path, 'dy', 'same', 'videos', {'.mp4'})
    validated.assert_called_once_with('job')


def test_every_capture_and_detail_uses_its_own_route_through_delivery(confirmed, tmp_path):
    from contextlib import contextmanager
    active = []
    events = []
    class Routed(Crawler):
        def check(self, kw):
            assert active == [kw['route_context']]
            assert kw['route_context']['account_id'] == kw['account_id']
        def run_search(self, **kw):
            self.check(kw)
            return super().run_search(**kw)
        def run_detail(self, **kw):
            self.check(kw)
            return super().run_detail(**kw)
    runner, _, _ = runtime(confirmed, tmp_path, events=events, crawler=Routed(events))
    runner.accounts['second'] = dict(id='second', auth_state={})
    runner.cursor = 1  # Frozen fixture has one query: capture B, then detail A.
    @contextmanager
    def route(account_id):
        assert active == []
        value = dict(account_id=account_id)
        active.append(value)
        try:
            yield value
        finally:
            assert active.pop() is value
    runner.route_operation = route
    delivered = []
    def deliver(refs):
        assert len(active) == 1
        delivered.append(active[0]['account_id'])
    runner.on_delivery = deliver
    report, _ = runner.run()
    assert report['handed_off'] == 1 and len(delivered) == 1
    assert active == []
    assert [kw['account_id'] for kind, kw in events if kind == 'capture'] == ['second']
    assert [kw['account_id'] for kind, kw in events if kind == 'detail'] == ['test-account']
    assert delivered == ['test-account']


@pytest.mark.parametrize('code', ['PROXY_EXIT_CHANGED', 'PROXY_REVISION_MISMATCH'])
def test_route_failure_never_rotates_or_delivers(confirmed, tmp_path, code):
    from contextlib import contextmanager
    from backend.audit_agent.crawler_route import CollectionRouteError
    runner, events, _ = runtime(confirmed, tmp_path)
    runner.accounts['second'] = dict(id='second', auth_state={})
    attempts, retired = [], []
    @contextmanager
    def route(account_id):
        attempts.append(account_id)
        raise CollectionRouteError(code)
        yield
    runner.route_operation = route
    runner.retire = lambda account, error: retired.append(account['id'])
    with pytest.raises(CollectionRouteError, match=code):
        runner.run()
    assert attempts == ['test-account'] and retired == [] and events == []


def test_verification_cannot_rotate_to_shared_proxy(confirmed, tmp_path):
    from backend.audit_agent.crawler_adapter import CrawlerVerificationError
    events = []
    class Challenge(Crawler):
        def run_search(self, **kw):
            if kw['account_id'] == 'test-account':
                events.append(('challenge', kw))
                raise CrawlerVerificationError('challenge')
            assert kw['account_id'] == 'independent'
            return super().run_search(**kw)
    runner, _, _ = runtime(confirmed, tmp_path, events=events, crawler=Challenge(events))
    runner.accounts['test-account']['proxy_id'] = 'shared'
    runner.accounts['same-exit'] = dict(id='same-exit', auth_state={}, proxy_id='shared')
    runner.accounts['independent'] = dict(id='independent', auth_state={}, proxy_id='other')
    retired = []
    runner.retire = lambda account, error: retired.append(account['id'])
    assert runner.run()[0]['handed_off'] == 1
    assert retired == ['test-account']
    assert set(runner.accounts) == {'independent'}


@pytest.mark.parametrize('leased,allowed,eligible,error', [
    (set(), {'a'}, {'a'}, PermissionError),
    ({'a'}, set(), {'a'}, PermissionError),
    ({'a'}, {'a'}, set(), RuntimeError),
])
def test_geographic_route_rechecks_authorization_and_eligibility(monkeypatch, leased, allowed, eligible, error):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from backend.audit_agent import pipeline as module
    pipeline = module.AuditPipeline.__new__(module.AuditPipeline)
    pipeline.job_id = 'geo-route-check'
    called = []
    @contextmanager
    def route(account_id, *, retain_direct=False):
        assert retain_direct
        called.append(account_id)
        yield object()
    pipeline._route_operation = route
    monkeypatch.setattr(module, '_authorized_crawler_account_ids', lambda job: allowed)
    monkeypatch.setattr(module, 'crawler_account_store', SimpleNamespace(
        available_accounts=lambda platform: [{'id': key} for key in eligible],
        mark_used=lambda account_id: called.append('used:' + account_id)))
    with pytest.raises(error):
        with pipeline._geographic_route_operation('dy', 'a', leased):
            pytest.fail('unauthorized collection')
    assert called == []


def test_settled_receipt_restart_delivers_without_borrowing_another_account_route(confirmed, tmp_path):
    from contextlib import contextmanager
    active = []
    @contextmanager
    def route(account_id):
        active.append(account_id)
        try:
            yield {'account_id': account_id}
        finally:
            active.pop()
    runner, _, _ = runtime(confirmed, tmp_path)
    runner.route_operation = route
    def interrupt(stage):
        if stage.get('stage') == 'after_settle':
            assert active == ['test-account']
            raise RuntimeError('stop after durable settlement')
    runner.on_progress = interrupt
    with pytest.raises(RuntimeError, match='durable settlement'):
        runner.run()
    assert active == []
    resumed, _, _ = runtime(confirmed, tmp_path)
    resumed.route_operation = route
    delivered = []
    def delivery(refs):
        # A saved receipt is valid membership evidence, not a live source-route
        # capability. No initial/other account route is fabricated on restart.
        assert active == []
        delivered.extend(refs)
    resumed.on_delivery = delivery
    assert resumed.run()[0]['handed_off'] == 1
    assert len(delivered) == 1 and active == []


def test_route_wait_cannot_cross_geographic_epoch_fence(confirmed, tmp_path):
    from contextlib import contextmanager
    valid = [True]
    active = []
    runner, events, _ = runtime(confirmed, tmp_path, fence=lambda db: valid[0])
    @contextmanager
    def route(account_id):
        active.append(account_id)
        valid[0] = False
        try:
            yield object()
        finally:
            active.pop()
    runner.route_operation = route
    with pytest.raises(ValueError, match='lease'):
        runner.run()
    assert events == [] and active == []


@pytest.mark.parametrize('revoke_during_route', [False, True])
def test_geographic_route_checks_grants_after_wait_and_records_actual_account(monkeypatch, revoke_during_route):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from backend.audit_agent import pipeline as module
    pipeline = module.AuditPipeline.__new__(module.AuditPipeline)
    pipeline.job_id = 'geo-selected-b'
    allowed, used, active = {'b'}, [], []
    @contextmanager
    def route(account_id, *, retain_direct=False):
        assert retain_direct
        active.append(account_id)
        if revoke_during_route:
            allowed.clear()
        try:
            yield 'route-b'
        finally:
            active.pop()
    pipeline._route_operation = route
    monkeypatch.setattr(module, '_authorized_crawler_account_ids', lambda job: allowed)
    monkeypatch.setattr(module, 'crawler_account_store', SimpleNamespace(
        available_accounts=lambda platform: [{'id': 'b'}], mark_used=used.append))
    if revoke_during_route:
        with pytest.raises(PermissionError):
            with pipeline._geographic_route_operation('dy', 'b', {'b'}):
                pytest.fail('revoked owner grant used')
        assert used == []
    else:
        with pipeline._geographic_route_operation('dy', 'b', {'b'}) as value:
            assert value == 'route-b' and active == ['b']
        assert used == ['b']
    assert active == []


def test_proxy_flag_off_geographic_route_retains_actual_legacy_direct_bundle(tmp_path, monkeypatch):
    from backend.audit_agent import pipeline as module
    from backend.audit_agent.crawler_route import resolve_account_route
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.job_store import JobStore
    from backend.task_admission.resources import ExecutionLeaseBundle, acquire_account_handle
    from crawler_auth_fixtures import mark_camoufox_login_verified
    monkeypatch.setattr(module.settings, 'crawler_proxy_enabled', False)
    monkeypatch.setattr(module.settings, 'app_auth_mode', 'disabled')
    monkeypatch.setattr(module.settings, 'crawler_browser_profile_root', tmp_path / 'profiles')
    monkeypatch.setattr(module.settings, 'task_resource_lock_dir', tmp_path / 'locks')
    monkeypatch.setattr(module.settings, 'request_scheduler_db', tmp_path / 'scheduler.sqlite3')
    accounts = CrawlerAccountStore(tmp_path / 'accounts.sqlite3')
    account = accounts.create(platform='dy', display_name='direct route fixture')
    accounts.save_auth_state(account['id'], 'isolated-fixture-ciphertext')
    mark_camoufox_login_verified(accounts, account['id'])
    jobs = JobStore(tmp_path / 'jobs.sqlite3')
    jobs.create(job_id='geo-direct', platform='dy')
    monkeypatch.setattr(module, 'crawler_account_store', accounts)
    monkeypatch.setattr(module, 'job_store', jobs)
    acquired = []
    def acquire(account_id, execution_id, owner, *, account_store):
        # Keep the real account/template/FD lease path, replacing only external
        # exit probing and browser startup with a local execution bundle.
        snapshot = resolve_account_route(account_id, account_store=account_store)
        assert snapshot.route_mode == 'legacy_direct'
        bundle = ExecutionLeaseBundle(execution_id, owner, account_id, snapshot)
        assert bundle.acquire()
        acquired.append(bundle)
        return bundle
    monkeypatch.setattr(module, 'acquire_route_execution', acquire)
    pipeline = module.AuditPipeline.__new__(module.AuditPipeline)
    pipeline.job_id = 'geo-direct'
    with pipeline._route_operation(account['id']) as ordinary:
        assert ordinary is None  # Ordinary flag-off legacy semantics unchanged.
    assert acquired == []
    with pipeline._geographic_route_operation('dy', account['id'], {account['id']}) as route:
        assert route is acquired[0] and route.snapshot.route_mode == 'legacy_direct'
        assert pipeline._collection_route_context is route and route.pass_fds
        assert acquire_account_handle('dy', account['id']) is None
    assert acquired[0].pass_fds == ()
    handle = acquire_account_handle('dy', account['id'])
    assert handle is not None
    handle.close()
    assert module.settings.crawler_proxy_enabled is False


@pytest.mark.parametrize('queued_id,source_id,allowed', [
    (None, 'source-a', False),
    ('other-b', 'source-a', False),
    ('source-a', None, False),
    ('source-a', 'source-a', True),
])
def test_geographic_media_requires_retained_delivery_source_not_producer(monkeypatch, tmp_path, queued_id, source_id, allowed):
    from types import SimpleNamespace
    from backend.audit_agent import pipeline as module
    pipeline = module.AuditPipeline.__new__(module.AuditPipeline)
    pipeline.job_id = 'geo-media-restart'
    pipeline._geographic_entry = True
    pipeline._collection_route_context = SimpleNamespace(account_id='source-a', snapshot=object())
    queued = SimpleNamespace(account_id=queued_id, snapshot=object()) if queued_id else None
    pipeline._media_route_context = SimpleNamespace(value=queued)
    source = {'source_account_id': source_id} if source_id else None
    subject = SimpleNamespace(platform='dy', url='https://fixture.invalid/post', collection_route_provenance=source)
    monkeypatch.setattr(module.job_store, 'get', lambda job: {'owner_user_id': 'owner'})
    monkeypatch.setattr(module.crawler_account_store, 'get', lambda account: None)
    calls = []
    def download(*args, **kw):
        calls.append(kw)
        assert kw['route_context'] is queued
        return 'retained-source-only'
    monkeypatch.setattr(module, 'download_url_with_error', download)
    if allowed:
        assert pipeline._download_subject_media(subject, 'https://fixture.invalid/image', tmp_path/'image') == 'retained-source-only'
        assert len(calls) == 1
    else:
        with pytest.raises(module.CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
            pipeline._download_subject_media(subject, 'https://fixture.invalid/image', tmp_path/'image')
        assert calls == [] and not (tmp_path/'image').exists()


def test_fresh_resume_from_verified_geographic_run_never_falls_back_to_producer(confirmed, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.audit_agent import pipeline as module
    adapter, configuration = confirmed
    pipeline = module.AuditPipeline.__new__(module.AuditPipeline)
    pipeline.job_id = configuration['_verified_geographic_binding']['job_id']
    pipeline.ingestion = adapter.ingestion_store
    pipeline._m3_snapshot_validator = lambda: configuration
    # Fresh pipeline has not passed through run(), so it has no hand-set Geo flag.
    assert not hasattr(pipeline, '_geographic_entry')
    pipeline._collection_route_context = SimpleNamespace(account_id='source-a', snapshot=object())
    subject = SimpleNamespace(platform='dy', url='https://fixture.invalid/post',
                              collection_route_provenance={'source_account_id': 'source-a'})
    calls = []
    monkeypatch.setattr(module, 'job_store', adapter.job_store)
    monkeypatch.setattr(module.settings, 'app_auth_mode', 'disabled')
    monkeypatch.setattr(module, 'download_url_with_error', lambda *a, **kw: calls.append(kw))
    def source_root(job):
        assert pipeline._geographic_entry is True
        pipeline._download_subject_media(subject, 'https://fixture.invalid/media', tmp_path/'media')
        pytest.fail('fresh resume fell through to generic downloader')
    pipeline._resume_source_root = source_root
    with pytest.raises(module.CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
        pipeline.resume_pending_analysis()
    assert calls == [] and not (tmp_path/'media').exists()
