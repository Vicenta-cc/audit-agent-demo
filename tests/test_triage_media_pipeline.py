"""Ordinary selected media crosses the producer/consumer boundary locally."""
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from backend.audit_agent import pipeline as pipeline_module
from backend.audit_agent.crawler_adapter import CrawlOutput
from backend.audit_agent.geographic_media import GeographicMediaNotReady
from backend.task_admission.resources import ExecutionLeaseBundle
from test_triage_account_rotation import RotationCrawler, _run_pipeline, env  # noqa: F401


def write_image(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (8, 8), 'red').save(path, format='JPEG')
    return path


@pytest.fixture
def media_case(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, 'app_auth_mode', 'disabled')
    monkeypatch.setattr(pipeline_module.settings, 'analysis_media_scope', 'all')
    monkeypatch.setattr(pipeline_module.settings, 'max_images_per_note', 2)
    monkeypatch.setattr(pipeline_module, 'job_store', SimpleNamespace(get=lambda _: {'owner_user_id': 'owner'}))
    pipeline = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id = 'job-media'
    pipeline._triage_local_media = True
    job_root = tmp_path / pipeline.job_id
    root = job_root / 'crawler'
    save_root = root / 'rotation-account'
    attempt = save_root / 'detail_attempts' / 'attempt-1'
    item = {'aweme_id': 'post-1', 'desc': 'local image subject', 'aweme_type': '68',
            'source_keyword': 'word', 'note_download_url': 'https://offline.invalid/one,https://offline.invalid/two'}
    output = CrawlOutput('dy', [item], [{'aweme_id': 'post-1', 'comment_id': 'c1', 'content': 'comment'}], attempt)
    route = object()
    calls = []

    def download(url, target, **kwargs):
        calls.append((url, Path(target), kwargs))
        return SimpleNamespace(path=write_image(target))

    monkeypatch.setattr(pipeline_module, 'download_url_with_error', download)
    receipt = save_root / 'douyin/detail_completed' / (hashlib.sha256(b'post-1').hexdigest() + '.json')

    def prepare(*, route=route, collect_media=True, stopped=False, output=output):
        pipeline._prepare_triage_delivery(output, root=root, save_root=save_root,
            collect_media=collect_media, route=route, stop_checker=lambda: stopped)

    return SimpleNamespace(pipeline=pipeline, root=root, job_root=job_root, save_root=save_root,
        attempt=attempt, output=output, route=route, calls=calls, receipt=receipt, prepare=prepare,
        images=attempt / 'douyin/images/post-1')


@pytest.mark.parametrize('existing', [1, 2], ids=['supplement-missing-image', 'reuse-complete-gallery'])
def test_producer_prepares_exact_gallery_before_publishing_receipt(media_case, existing):
    c = media_case
    originals = [write_image(c.images / f'{i:03d}.jpeg') for i in range(existing)]
    c.prepare()
    assert len(c.calls) == 2 - existing
    for url, target, kwargs in c.calls:
        assert url == 'https://offline.invalid/two'
        assert kwargs['route_context'] is c.route and kwargs['required_route'] is True
        assert kwargs['execution_id'] == 'job-media' and kwargs['owner_id'] == 'owner'
        target.relative_to(c.attempt)
    payload = json.loads(c.receipt.read_text())
    assert payload['contents'] == c.output.contents and payload['comments'] == c.output.comments
    assert payload['attempt_root'] == str(c.attempt)
    binding = payload['contents'][0]['triage_media_binding']
    assert binding['source_dir'] == str(c.attempt.relative_to(c.root))
    subject, = c.pipeline._build_subjects('dy', payload['contents'], payload['comments'], c.job_root)
    assert subject.local_image_paths[:existing] == [str(path) for path in originals]
    assert len(subject.local_image_paths) == 2
    assert subject.image_urls == subject.video_urls == []
    assert subject.comments == c.output.comments


def test_merged_save_root_is_not_an_exact_media_source(media_case):
    c = media_case
    c.output.output_dir = c.save_root
    write_image(c.save_root / 'douyin/images/post-1/000.jpeg')
    with pytest.raises(GeographicMediaNotReady):
        c.prepare()
    assert not c.calls and not c.receipt.exists()


def test_raw_completed_receipt_recovers_original_attempt_locally(media_case):
    c = media_case
    for i in range(2):
        write_image(c.images / f'{i:03d}.jpeg')
    c.receipt.parent.mkdir(parents=True)
    c.receipt.write_text(json.dumps(dict(contents=c.output.contents, comments=c.output.comments,
        creators=[], attempt_root=str(c.attempt))))
    payload = json.loads(c.receipt.read_text())
    recovered = CrawlOutput('dy', payload['contents'], payload['comments'], Path(payload['attempt_root']))
    c.prepare(output=recovered, route=None)
    assert not c.calls
    assert json.loads(c.receipt.read_text())['contents'][0]['triage_media_binding']
    c.prepare(output=recovered, route=None)
    assert not c.calls, 'already prepared recovery must remain local'


def test_subject_loading_and_analysis_forbid_platform_download(media_case, monkeypatch):
    c = media_case
    c.prepare()
    c.calls.clear()
    subject, = c.pipeline._build_subjects('dy', c.output.contents, c.output.comments, c.job_root)
    assert subject.media_readiness and not subject.image_urls and not subject.video_urls
    with pytest.raises(pipeline_module.CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
        c.pipeline._download_subject_media(subject, 'https://offline.invalid/new', c.job_root / 'new.jpg')
    monkeypatch.setattr(c.pipeline, '_analyze_subject_with_capacity', lambda s: {'paths': s.local_image_paths})
    assert len(c.pipeline._analyze_subject(subject)['paths']) == 2
    assert not c.calls


def test_media_changed_after_subject_build_fails_before_model_analysis(media_case, monkeypatch):
    c = media_case
    c.prepare()
    subject, = c.pipeline._build_subjects('dy', c.output.contents, [], c.job_root)
    Path(subject.local_image_paths[0]).write_bytes(b'changed after enqueue')
    monkeypatch.setattr(c.pipeline, '_analyze_subject_with_capacity', lambda _: pytest.fail('model saw corrupt media'))
    c.calls.clear()
    with pytest.raises(GeographicMediaNotReady):
        c.pipeline._analyze_subject(subject)
    assert not c.calls


def test_media_disabled_publishes_empty_local_contract(media_case):
    c = media_case
    c.prepare(collect_media=False)
    subject, = c.pipeline._build_subjects('dy', c.output.contents, [], c.job_root, include_media=False)
    assert subject.media_readiness and not subject.local_image_paths and not subject.local_video_paths
    assert not subject.image_urls and not subject.video_urls and not c.calls


def test_stop_fence_prevents_readiness_and_delivery_publication(media_case):
    c = media_case
    with pytest.raises(pipeline_module._TriagePreparationStopped):
        c.prepare(stopped=True)
    assert not c.receipt.exists() and not list(c.root.glob('media_ready/*.json'))
    assert not c.calls and 'triage_media_binding' not in c.output.contents[0]


def test_new_triage_subject_without_binding_cannot_fall_back_to_scan(media_case, monkeypatch):
    c = media_case
    write_image(c.images / '000.jpeg')
    monkeypatch.setattr(c.pipeline, '_find_local_media', lambda *args: pytest.fail('unbound fallback scan'))
    with pytest.raises(GeographicMediaNotReady):
        c.pipeline._build_subjects('dy', c.output.contents, [], c.job_root)
    assert not c.calls


@pytest.mark.parametrize('missing_image', [False, True], ids=['complete-gallery', 'supplement-before-ingestion'])
def test_real_images_reach_ingestion_and_next_keyword_while_first_model_waits(env, monkeypatch, missing_image):
    accounts = [env.add(name) for name in ('A', 'B')]
    for name, value in {'crawler_proxy_enabled': True, 'task_resource_lock_dir': env.tmp / 'locks',
            'stream_crawl_analysis': True, 'triage_accounts_per_task': 2,
            'crawler_shared_exit_active_limit': 1, 'max_images_per_note': 2,
            'analysis_media_scope': 'all'}.items():
        monkeypatch.setattr(pipeline_module.settings, name, value)
    entered, second_finished, release = threading.Event(), threading.Event(), threading.Event()
    bundles, detail_routes, analyzed, failures, downloads = [], [], [], [], []

    def acquire(account_id, execution_id, owner_id, **kwargs):
        bundle = ExecutionLeaseBundle(execution_id, owner_id, account_id, SimpleNamespace(proxy_id='service-direct'))
        if not bundle.acquire(max_active_accounts=1) or not bundle.acquire_exit_capacity('203.0.113.9'):
            bundle.close()
            raise pipeline_module.CollectionRouteError('PROXY_BUSY')
        bundles.append(bundle)
        return bundle

    monkeypatch.setattr(pipeline_module, 'acquire_route_execution', acquire)

    def download(url, target, **kwargs):
        assert missing_image and url == 'https://offline.invalid/two'
        route = kwargs['route_context']
        assert route is detail_routes[-1] and route.pass_fds and kwargs['required_route'] is True
        downloads.append(str(target))
        return SimpleNamespace(path=write_image(target))

    monkeypatch.setattr(pipeline_module, 'download_url_with_error', download)

    class ImagesCrawler(RotationCrawler):
        def __init__(self):
            super().__init__()
            self.items = []

        def run_detail(self, platform, content_id, *, source_keyword, save_root, account_id='', **kwargs):
            self.detail_accounts.append((source_keyword, account_id))
            detail_routes.append(bundles[-1])
            if len(detail_routes) == 2:
                assert entered.wait(5), 'first model never started'
                assert not release.is_set()
                assert not detail_routes[0].pass_fds and detail_routes[0]._borrowers == 0
            attempt = Path(save_root) / 'detail_attempts' / content_id
            item = {'aweme_id': content_id, 'desc': 'selected image', 'source_keyword': source_keyword,
                    'aweme_type': '68', 'note_download_url': 'https://offline.invalid/one,https://offline.invalid/two'}
            for index in range(1 if missing_image else 2):
                write_image(attempt / 'douyin/images' / content_id / f'{index:03d}.jpeg')
            self.items.append(item)
            if callback := kwargs.get('content_callback'):
                callback([item], [])
            if len(self.items) == 2:
                second_finished.set()
            return CrawlOutput(platform, [item], [], attempt)

        def _load_platform_output(self, save_root, platform):
            return CrawlOutput(platform, self.items, [], save_root)

    def model(self, subject):
        assert subject.media_readiness and not subject.image_urls and not subject.video_urls
        assert len(subject.local_image_paths) == 2
        assert getattr(self._media_route_context, 'value', None) is None
        for path in subject.local_image_paths:
            with Image.open(path) as image:
                image.load()
        refs = self.ingestion.validated_refs_for_task(self.job_id)
        assert any(ref['content_key'] == subject.note_id and ref['item'].get('triage_media_binding') for ref in refs)
        if missing_image:
            assert subject.local_image_paths[1] in downloads
        analyzed.append(subject.note_id)
        if len(analyzed) == 1:
            entered.set()
            assert release.wait(7), 'controller did not release model'
        return {'note_id': subject.note_id, 'content_key': subject.note_id}

    monkeypatch.setattr(pipeline_module.AuditPipeline, '_analyze_subject_with_capacity', model)

    def controller():
        try:
            assert entered.wait(5), 'first model never entered'
            assert second_finished.wait(3), 'second keyword waited for first model'
        except BaseException as exc:
            failures.append(str(exc))
        finally:
            release.set()

    thread = threading.Thread(target=controller, daemon=True)
    thread.start()
    try:
        job = _run_pipeline(env, monkeypatch, ImagesCrawler(), accounts[0],
            settings_auto_analyze=True, auto_analyze=True, analyze_limit=2, max_total_notes=2,
            collect_media=True, keyword='词1,词2', max_notes=1)
    finally:
        release.set()
        thread.join(6)
    assert not failures, (failures, job.get('error'))
    assert not thread.is_alive() and len(analyzed) == 2
    assert not job.get('error') and len(job['items']) == 2
    assert len(downloads) == (2 if missing_image else 0)
    assert all(not bundle.pass_fds for bundle in bundles)


@pytest.mark.parametrize('selection', ['stream', 'confirmed'])
def test_completed_legacy_subject_without_media_does_not_block_remaining_keywords(env, monkeypatch, selection):
    from backend.audit_agent.ingestion import IngestionStore
    from backend.audit_agent.job_store import JobStore

    account = env.add('A')
    monkeypatch.setattr(pipeline_module.settings, 'crawler_proxy_enabled', False)
    monkeypatch.setattr(pipeline_module.settings, 'stream_crawl_analysis', True)
    monkeypatch.setattr(pipeline_module.settings, 'triage_accounts_per_task', 1)
    job_root = env.tmp / 'outputs/job-rot'
    crawl_root = job_root / 'crawler'
    old = {'aweme_id': '词1-1', 'desc': 'already audited legacy image', 'source_keyword': '词1',
           'aweme_type': '68', 'note_download_url': 'https://offline.invalid/deleted-original'}
    receipt = crawl_root / 'douyin/detail_completed/old.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(dict(contents=[old], comments=[], creators=[],
        attempt_root=str(crawl_root / 'detail_attempts/deleted-old-attempt'))))
    store = IngestionStore(env.tmp / 'ingestion.sqlite3')
    batch = job_root / 'legacy-batch.json'
    batch.write_text(json.dumps(dict(task_id='job-rot', platform='dy', keyword='词1', items=[old], comments=[])))
    [ref] = store.ingest_batch(batch, job_root / 'raw_items')
    store.mark_content_status('dy', '词1-1', 'completed', task_id='job-rot')
    legacy_raw = Path(ref['raw_item_path'])
    original_bytes = legacy_raw.read_bytes()
    existing_result = {'note_id': '词1-1', 'content_key': '词1-1', 'summary': 'saved legacy verdict'}
    original_create = JobStore.create

    def create_with_completed_result(self, **kwargs):
        original_create(self, **kwargs)
        self.update('job-rot', items=[existing_result])
        return self.get('job-rot')

    monkeypatch.setattr(JobStore, 'create', create_with_completed_result)
    monkeypatch.setattr(pipeline_module, 'download_url_with_error',
                        lambda *_args, **_kwargs: pytest.fail('completed historical media must not be fetched'))
    analyzed = []

    def analyze(self, subject):
        analyzed.append(subject.note_id)
        assert subject.note_id == '词2-1'
        return {'note_id': subject.note_id, 'content_key': subject.note_id}

    monkeypatch.setattr(pipeline_module.AuditPipeline, '_analyze_subject', analyze)

    class ResumedCrawler(RotationCrawler):
        def run_detail(self, platform, content_id, *, source_keyword, save_root=None, account_id='', **kwargs):
            self.detail_accounts.append((source_keyword, account_id))
            return CrawlOutput(platform, [{'aweme_id': content_id, 'desc': 'new text only',
                                           'source_keyword': source_keyword}], [],
                               Path(save_root) / 'detail_attempts/new-attempt')

        def _load_platform_output(self, save_root, platform):
            return CrawlOutput(platform, [old, {'aweme_id': '词2-1', 'desc': 'new text only',
                                               'source_keyword': '词2'}], [], save_root)

    crawler = ResumedCrawler()
    snapshot = {'auto_analyze': True} if selection == 'stream' else {'_confirmed_analyze_limit': 2}
    error = None
    try:
        job = _run_pipeline(env, monkeypatch, crawler, account, settings_auto_analyze=True,
                            analyze_limit=2, max_total_notes=2, collect_media=True, **snapshot)
    except pipeline_module.CollectionRouteError as exc:
        error = exc
        job = pipeline_module.job_store.get('job-rot')
    assert error is None, f'completed legacy evidence blocked continuation: {error}'
    assert crawler.search_accounts == [('词2', account['id'])]
    assert analyzed == ['词2-1']
    assert len(job['items']) == 2 and existing_result in job['items']
    assert not job.get('error') and job['status'] == 'completed'
    assert legacy_raw.read_bytes() == original_bytes


@pytest.mark.parametrize('stop_control', ['crawl_stop_requested', 'stop_all_requested'])
def test_stop_during_media_preparation_is_not_a_network_failure(env, monkeypatch, stop_control):
    from backend.audit_agent.ingestion import IngestionStore

    account = env.add('A')
    monkeypatch.setattr(pipeline_module.settings, 'crawler_proxy_enabled', True)
    monkeypatch.setattr(pipeline_module.settings, 'task_resource_lock_dir', env.tmp / 'locks')
    monkeypatch.setattr(pipeline_module.settings, 'stream_crawl_analysis', True)
    bundles = []

    def acquire(account_id, execution_id, owner_id, **kwargs):
        bundle = ExecutionLeaseBundle(execution_id, owner_id, account_id,
                                      SimpleNamespace(proxy_id='service-direct'))
        assert bundle.acquire(max_active_accounts=1)
        bundles.append(bundle)
        return bundle

    monkeypatch.setattr(pipeline_module, 'acquire_route_execution', acquire)
    monkeypatch.setattr(pipeline_module, 'download_url_with_error',
                        lambda *_args, **_kwargs: pytest.fail('stopped preparation downloaded media'))
    monkeypatch.setattr(pipeline_module.AuditPipeline, '_analyze_subject',
                        lambda *_: pytest.fail('unready stopped detail reached analysis'))

    class StoppingCrawler(RotationCrawler):
        def run_detail(self, platform, content_id, *, source_keyword, save_root=None, account_id='', **kwargs):
            self.detail_accounts.append((source_keyword, account_id))
            item = {'aweme_id': content_id, 'desc': 'not ready', 'source_keyword': source_keyword,
                    'aweme_type': '68', 'note_download_url': 'https://offline.invalid/missing'}
            attempt = Path(save_root) / 'detail_attempts/stopped-attempt'
            directory = Path(save_root) / 'douyin/detail_completed'
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'stopped.json').write_text(json.dumps(dict(contents=[item], comments=[],
                                                                  creators=[], attempt_root=str(attempt))))
            # A stop arrives after the adapter returns its raw completion but
            # before producer-side media readiness is allowed to publish.
            pipeline_module.job_store.update_control('job-rot', **{stop_control: True})
            return CrawlOutput(platform, [item], [], attempt)

        def _load_platform_output(self, save_root, platform):
            return CrawlOutput(platform, [], [], save_root)

    error = None
    try:
        job = _run_pipeline(env, monkeypatch, StoppingCrawler(), account, settings_auto_analyze=True,
                            auto_analyze=True, analyze_limit=2, max_total_notes=2, collect_media=True)
    except pipeline_module.CollectionRouteError as exc:
        error = exc
        job = pipeline_module.job_store.get('job-rot')
    assert error is None, f'user stop surfaced as collection route failure: {error}'
    assert job['crawl_status'] == 'stopped'
    assert not job.get('error')
    assert not job['control'].get('network_deferred')
    assert job['control'][stop_control]
    assert all(not bundle.pass_fds for bundle in bundles)
    assert not IngestionStore(env.tmp / 'ingestion.sqlite3').validated_refs_for_task('job-rot')
    assert not list((env.tmp / 'outputs/job-rot/crawler').glob('media_ready/*.json'))


@pytest.mark.parametrize('selection', ['stream', 'confirmed'])
@pytest.mark.parametrize('already_prepared', [False, True], ids=['legacy-queued', 'prepared-after-pause'])
def test_queued_complete_receipt_recovers_locally_and_reenters_analysis(env, monkeypatch, selection, already_prepared):
    from backend.audit_agent.geographic_media import TriageMediaReadiness
    from backend.audit_agent.ingestion import IngestionStore
    from backend.audit_agent.scoped_selection import digest

    account = env.add('A')
    for name, value in {'crawler_proxy_enabled': False, 'stream_crawl_analysis': True,
                        'triage_accounts_per_task': 1, 'max_images_per_note': 1,
                        'analysis_media_scope': 'all'}.items():
        monkeypatch.setattr(pipeline_module.settings, name, value)
    job_root = env.tmp / 'outputs/job-rot'
    crawl_root = job_root / 'crawler'
    attempt = crawl_root / 'detail_attempts/queued-attempt'
    local_image = write_image(attempt / 'douyin/images/词1-1/000.jpeg')
    item = {'aweme_id': '词1-1', 'desc': 'queued before restart', 'source_keyword': '词1',
            'aweme_type': '68', 'note_download_url': 'https://offline.invalid/one',
            'collection_route_provenance': None}
    if already_prepared:
        binding = dict(job_id='job-rot', platform='dy', content_key='词1-1',
                       content_digest=digest(item), source_dir=str(attempt.relative_to(crawl_root)))
        TriageMediaReadiness(crawl_root).prepare(binding, collect_media=True,
            image_urls=['https://offline.invalid/one'], video_urls=[], image_limit=1,
            analysis_scope='all', route=None, download=lambda *_: pytest.fail('fixture fetched media'))
        item['triage_media_binding'] = binding
    receipt = crawl_root / 'douyin/detail_completed/queued.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(dict(contents=[item], comments=[], creators=[], attempt_root=str(attempt))))
    store = IngestionStore(env.tmp / 'ingestion.sqlite3')
    batch = job_root / 'previous-run-batch.json'
    batch.write_text(json.dumps(dict(task_id='job-rot', platform='dy', keyword='词1', items=[item], comments=[])))
    store.ingest_batch(batch, job_root / 'raw_items')
    assert store.validated_refs_for_task('job-rot')[0]['analyze_status'] == 'queued'
    monkeypatch.setattr(pipeline_module, 'download_url_with_error',
                        lambda *_args, **_kwargs: pytest.fail('queued complete receipt recovery used network'))
    analyzed = []

    def analyze(self, subject):
        analyzed.append(subject.note_id)
        assert subject.media_readiness is not None
        assert subject.local_image_paths == [str(local_image)]
        assert subject.image_urls == subject.video_urls == []
        assert getattr(self._media_route_context, 'value', None) is None
        return {'note_id': subject.note_id, 'content_key': subject.note_id}

    monkeypatch.setattr(pipeline_module.AuditPipeline, '_analyze_subject', analyze)

    class CompletedReceiptCrawler(RotationCrawler):
        def run_search(self, **kwargs):
            pytest.fail('already collected keyword was fetched again')

        def run_detail(self, *args, **kwargs):
            pytest.fail('complete receipt was collected again')

        def _load_platform_output(self, save_root, platform):
            return CrawlOutput(platform, [item], [], save_root)

    snapshot = {'auto_analyze': True} if selection == 'stream' else {'_confirmed_analyze_limit': 1}
    error = None
    try:
        job = _run_pipeline(env, monkeypatch, CompletedReceiptCrawler(), account,
            settings_auto_analyze=True, analyze_limit=1, max_total_notes=1, keyword='词1',
            collect_media=True, **snapshot)
    except pipeline_module.CollectionRouteError as exc:
        error = exc
        job = pipeline_module.job_store.get('job-rot')
    assert error is None, f'queued recovery failed: {error}'
    assert analyzed == ['词1-1'], 'a previously selected queued member must be delivered exactly once'
    [ref] = store.validated_refs_for_task('job-rot')
    assert ref['analyze_status'] == 'completed'
    assert ref['item']['triage_media_binding']['job_id'] == 'job-rot'
    assert len(job['items']) == 1 and not job.get('error')
