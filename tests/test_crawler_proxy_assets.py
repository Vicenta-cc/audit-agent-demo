"""Offline source integrity and application transport contracts (not browser proof)."""
import json
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.audit_agent import asset_utils, crawler_route
from backend.audit_agent.ingestion import BatchWriter, IngestionStore
from backend.audit_agent.config import settings


def source(account='source', observation='obs1'):
    now = time.time()
    return dict(schema_version=1, observation_id=observation, source_task_id='first',
                source_batch_id='batch1', collected_at=now, platform='dy',
                source_account_id=account, route_mode='proxy_required', proxy_id='proxy1',
                binding_revision=1, config_revision=1, observed_exit_ip='192.0.2.1', observed_exit_at=now)


def ingest(store, tmp_path, task, item, comments):
    writer = BatchWriter(job_id=task, platform='dy', keyword=task, root=tmp_path/task, batch_size=1)
    return store.ingest_batch(writer.add([item], comments)[0], tmp_path/task/'raw')


def test_shared_raw_item_comments_and_source_remain_same_observation(tmp_path):
    store = IngestionStore(tmp_path/'db.sqlite')
    old = dict(aweme_id='42', desc='original', collection_route_provenance=source())
    first = ingest(store, tmp_path, 'first', old, [dict(aweme_id='42', comment_id='c1', content='old')])[0]
    raw = Path(first['raw_item_path']); original_bytes = raw.read_bytes()
    new = dict(aweme_id='42', desc='new', collection_route_provenance=source('other', 'obs2'))
    reused = ingest(store, tmp_path, 'second', new, [dict(aweme_id='42', comment_id='c2', content='new')])[0]
    assert raw.read_bytes() == original_bytes
    assert reused['raw_item_path'] == str(raw)
    assert reused['item']['desc'] == 'original'
    assert reused['comments'][0]['comment_id'] == 'c1'
    assert reused['collection_route_provenance']['source_account_id'] == 'source'
    assert reused['use_context']['observation_id'] == 'obs2'
    restored = store.refs_for_task('second')[0]
    assert restored['collection_route_provenance'] == first['collection_route_provenance']
    assert restored['item']['collection_route_provenance'] == first['collection_route_provenance']
    with store._connect() as conn:
        use = json.loads(conn.execute("SELECT use_context_json FROM task_contents WHERE task_id='second'").fetchone()[0])
    assert use['task_id'] == 'second' and use['observation_id'] == 'obs2'


def test_historical_unknown_never_inherits_nested_guessed_source(tmp_path):
    payload = dict(item={'aweme_id': '42', 'collection_route_provenance': source()})
    assert IngestionStore._source_item(payload)['collection_route_provenance'] is None


def test_strict_missing_source_never_opens_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', True)
    monkeypatch.setattr(asset_utils.requests, 'Session', lambda: pytest.fail('network opened'))
    with pytest.raises(crawler_route.CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
        asset_utils.download_url_with_error('https://media.example/file', tmp_path/'file', platform='dy')


def test_bound_redirects_preserve_explicit_bundle_for_every_hop(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "crawler_proxy_enabled", True)
    bundle = SimpleNamespace(snapshot=object())
    calls = []
    @contextmanager
    def authorized(value, owner, execution, **kwargs):
        assert value["source_account_id"] == "source" and owner == "u1" and execution == "run1"
        yield bundle
    def hop(actual, url, headers, timeout, verify, on_chunk, **kwargs):
        assert actual is bundle and verify is True and timeout == 30
        assert "Proxy-Authorization" not in headers
        calls.append(url)
        if len(calls) == 1:
            return 302, {"location": "https://cdn.example/file"}
        on_chunk(b"file")
        return 200, {}
    monkeypatch.setattr(crawler_route, "authorize_source_route", authorized)
    monkeypatch.setattr(crawler_route, "download_asset_hop", hop)
    monkeypatch.setattr(asset_utils.requests, "Session", lambda: pytest.fail("bound used synchronous transport"))
    result = asset_utils.download_url_with_error("https://media.example/file", tmp_path/"file", platform="dy",
        collection_route_provenance=source(), owner_id="u1", execution_id="run1")
    assert result.path.read_bytes() == b"file"
    assert calls == ["https://media.example/file", "https://cdn.example/file"]


def _real_transport_helper(monkeypatch, tmp_path, bundle, fixture):
    """Exercise production HTTPX+deadline+four-scope slot; browser proof is independent."""
    import asyncio
    from backend.audit_agent.crawler_browser import load_request_scheduler
    snapshot = bundle.snapshot
    snapshot.account_id = "source"
    snapshot.proxy_id = "proxy1"
    snapshot.route_mode = "proxy_required"
    snapshot.binding_revision = snapshot.config_revision = 1
    snapshot.httpx_proxy = lambda: fixture.authenticated_url
    snapshot.scheduler_db_path = getattr(snapshot, "scheduler_db_path", str(tmp_path/"scheduler.sqlite"))
    scopes = ["dy", "dy:account:source", "dy:proxy:proxy1", "dy:exit:192.0.2.1"]
    module = load_request_scheduler()
    for scope in scopes:
        module.RequestScheduler(snapshot.scheduler_db_path, platform=scope, min_interval=0,
                                media_interval=0, per_minute=1000, cooldown_seconds=0)
    gate = module.MultiScopeGate(snapshot.scheduler_db_path, scopes)
    async def ensure_transport_ready():
        # This fixture isolates transport; actual paired-browser setup has its own mandatory suite.
        return None
    helper = SimpleNamespace(session=SimpleNamespace(slot=gate.slot, check_stop=lambda:None),
                             call=asyncio.run, ensure_open=ensure_transport_ready)
    bundle.account_id = "source"; bundle.owner_id = "owner"
    monkeypatch.setattr(crawler_route, "authorize_route_owner", lambda *a, **k: None)
    monkeypatch.setattr(crawler_route, "resolve_account_route", lambda *a, **k: snapshot)
    monkeypatch.setattr(crawler_route, "_asset_helper", lambda actual, **kwargs: helper)
    return gate


def test_source_authorization_denial_never_creates_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', True)
    @contextmanager
    def denied(*args, **kwargs):
        raise crawler_route.CollectionRouteError('SUPPLEMENT_DEFERRED')
        yield
    monkeypatch.setattr(crawler_route, 'authorize_source_route', denied)
    monkeypatch.setattr(asset_utils.requests, 'Session', lambda: pytest.fail('network opened'))
    with pytest.raises(crawler_route.CollectionRouteError):
        asset_utils.download_url_with_error('https://media.example/file', tmp_path/'file', platform='dy',
            collection_route_provenance=source(), owner_id='u2', execution_id='run2')


@pytest.fixture
def authorized_stores(tmp_path, monkeypatch):
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from backend.application_auth.store import AuthStore
    from backend.task_admission import resources
    accounts = CrawlerAccountStore(tmp_path/'accounts.sqlite')
    proxies = CrawlerProxyStore(accounts, cipher=AuthStateCipher(encryption_key='fixture'))
    resource = proxies.create(display_name='source', endpoint='http://proxy.example:8080')
    account = accounts.create(platform='dy', display_name='source')
    accounts.save_auth_state(account['id'], 'fixture-cipher')
    proxies.bind(account['id'], resource['id'], expected_binding_revision=0)
    auth = AuthStore(tmp_path/'auth.sqlite', activation_mode='created_at')
    owner = auth.create_user(username='source-owner', password='fixture-password', activation_mode='created_at')['id']
    reader = auth.create_user(username='reader', password='fixture-password', activation_mode='created_at')['id']
    auth.register_crawler_account(account['id'], owner)
    for user in (owner, reader):
        auth.grant_resource(user_id=user, resource_type='crawler-proxy', resource_id=resource['id'],
                            permission='use', actor_user_id='system')
    monkeypatch.setattr(settings, 'app_auth_mode', 'required')
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', True)
    monkeypatch.setattr(crawler_route, 'get_proxy_store', lambda account_store=None: proxies)
    monkeypatch.setattr(resources, 'settings', SimpleNamespace(task_resource_lock_dir=tmp_path/'locks', crawler_browser_profile_root=tmp_path/'profiles'))
    provenance = source()
    provenance.update(source_account_id=account['id'], proxy_id=resource['id'])
    return accounts, proxies, auth, owner, reader, provenance


def test_shared_content_and_proxy_grant_do_not_authorize_private_source(authorized_stores):
    accounts, proxies, auth, owner, reader, provenance = authorized_stores
    with pytest.raises(crawler_route.CollectionRouteError):
        with crawler_route.authorize_source_route(provenance, reader, 'run2', account_store=accounts, auth_store=auth):
            pytest.fail('private source authorized')


def test_source_proxy_revocation_rechecked_before_resume_download(authorized_stores):
    accounts, proxies, auth, owner, _, provenance = authorized_stores
    auth.revoke_resource(user_id=owner, resource_type='crawler-proxy', resource_id=provenance['proxy_id'],
                         permission='use', actor_user_id='system')
    with pytest.raises(crawler_route.CollectionRouteError):
        with crawler_route.authorize_source_route(provenance, owner, 'run1', account_store=accounts, auth_store=auth):
            pytest.fail('revoked proxy authorized')


def test_stale_source_config_refused_before_lease(authorized_stores):
    accounts, proxies, auth, owner, _, provenance = authorized_stores
    proxies.update(provenance['proxy_id'], expected_revision=1, endpoint='http://new.example:8080')
    with pytest.raises(crawler_route.CollectionRouteError, match='PROXY_REVISION_MISMATCH'):
        with crawler_route.authorize_source_route(provenance, owner, 'run1', account_store=accounts, auth_store=auth):
            pytest.fail('stale route authorized')


def test_deleted_source_account_refused(authorized_stores):
    accounts, proxies, auth, owner, _, provenance = authorized_stores
    accounts.delete(provenance['source_account_id'])
    with pytest.raises(crawler_route.CollectionRouteError):
        with crawler_route.authorize_source_route(provenance, owner, 'run1', account_store=accounts, auth_store=auth):
            pytest.fail('deleted source authorized')


@pytest.mark.parametrize('https', [False, True])
def test_actual_httpx_forward_and_connect_redirects_cannot_go_direct(tmp_path, monkeypatch, https):
    from proxy_fixture import local_proxy
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', True)
    with local_proxy(tmp_path/'network') as fixture:
        fixture.password = 'canary#/?@:秘密'
        snapshot = SimpleNamespace(proxy_server=fixture.server,
                                   username=fixture.username, password=fixture.password)
        bundle = SimpleNamespace(snapshot=snapshot)
        @contextmanager
        def authorized(*args, **kwargs):
            yield bundle
        monkeypatch.setattr(crawler_route, 'authorize_source_route', authorized)
        _real_transport_helper(monkeypatch, tmp_path, bundle, fixture)
        for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY'):
            monkeypatch.setenv(key, 'http://127.0.0.1:1')
        monkeypatch.setenv('NO_PROXY','*')
        result = asset_utils.download_url_with_error(fixture.url('/redirect', https=https), tmp_path/'media',
            platform='dy', collection_route_provenance=source(), owner_id='owner', execution_id='run', verify=fixture.ca)
        assert result.path is not None and result.path.exists()
        fixture.assert_routed()
        assert any(event['kind'] == ('CONNECT' if https else 'forward') for event in fixture.events)
        assert all(not origin['authorization'] for origin in fixture.origins)


def test_proxy_required_stays_closed_when_feature_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', False)
    @contextmanager
    def unavailable(*args, **kwargs):
        raise crawler_route.CollectionRouteError('PROXY_UNAVAILABLE')
        yield
    monkeypatch.setattr(crawler_route, 'authorize_source_route', unavailable)
    monkeypatch.setattr(asset_utils.requests, 'Session', lambda: pytest.fail('direct network'))
    with pytest.raises(crawler_route.CollectionRouteError, match='PROXY_UNAVAILABLE'):
        asset_utils.download_url_with_error('https://media.example/file', tmp_path/'media', platform='dy',
            collection_route_provenance=source(), owner_id='owner', execution_id='run')


def test_subject_receives_original_source_without_network(tmp_path):
    from backend.audit_agent.pipeline import AuditPipeline
    pipeline = AuditPipeline.__new__(AuditPipeline)
    provenance = source()
    subject = pipeline._build_subjects('dy', [{'aweme_id':'42', 'collection_route_provenance':provenance}],
                                      [], tmp_path, include_media=False)[0]
    assert subject.collection_route_provenance == provenance
    assert subject.image_urls == subject.video_urls == []


def test_unknown_local_asset_analysis_does_not_borrow_source(tmp_path, monkeypatch):
    from backend.audit_agent import pipeline as pipeline_module
    from backend.audit_agent.models import AuditSubject
    pipeline = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id = "local-job"
    local = tmp_path / "image.jpg"
    local.write_bytes(b"local")
    subject = AuditSubject("dy", "42", "", "", "", {}, ["https://platform.example/image"], [], [],
                           local_image_paths=[str(local)])
    monkeypatch.setattr(crawler_route, "authorize_source_route", lambda *a, **k: pytest.fail("local source borrowed"))
    monkeypatch.setattr(pipeline, "_download_subject_media", lambda *a, **k: pytest.fail("remote attempted"))
    monkeypatch.setattr(pipeline, "_stage_local_image", lambda path, *a: path)
    monkeypatch.setattr(pipeline, "_scan_image_ocr_batch", lambda *a: {})
    monkeypatch.setattr(pipeline, "_run_image_audit", lambda *a, **k: ({"summary": "local"}, []))
    monkeypatch.setattr(pipeline_module.job_store, "log", lambda *a, **k: None)
    result = pipeline._analyze_images(subject, tmp_path / "staged")
    assert result[0]["source"] == "local" and result[0]["summary"] == "local"


def test_invalid_source_is_typed_route_error_without_secret_fields(tmp_path):
    bad = source(); bad['password']='must-not-persist'
    writer = BatchWriter(job_id='first', platform='dy', keyword='first', root=tmp_path, batch_size=1)
    with pytest.raises(crawler_route.CollectionRouteError, match='PROXY_CONFIG_INVALID') as error:
        writer.add([{'aweme_id':'42', 'collection_route_provenance':bad}], [])
    assert 'must-not-persist' not in str(error.value)
    assert not list(tmp_path.glob('batches/*.json'))


def test_operation_flushes_source_queue_while_transferable_borrow_is_alive(tmp_path, monkeypatch):
    from backend.audit_agent import pipeline as pipeline_module
    from backend.task_admission import resources
    monkeypatch.setattr(resources, 'settings', SimpleNamespace(task_resource_lock_dir=tmp_path/'locks', crawler_browser_profile_root=tmp_path/'profiles'))
    bundle = resources.ExecutionLeaseBundle('run1', 'owner', 'source', SimpleNamespace(proxy_id='proxy'))
    assert bundle.acquire()
    pipeline = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id = 'run1'
    monkeypatch.setattr(pipeline_module.job_store, 'update_control', lambda *a, **k: None)
    monkeypatch.setattr(pipeline_module, 'crawler_account_store', SimpleNamespace(get=lambda _: {'proxy_required':True}))
    monkeypatch.setattr(pipeline_module, 'get_current_route_context', lambda: bundle)
    events=[]
    def flush():
        token = pipeline._collection_route_context
        with token.borrow(execution_id='run1', owner_id='owner', account_id='source'):
            events.append('borrowed-before-release')
    pipeline._route_flush_pending = flush
    with pipeline._route_operation('source') as token:
        assert token.account_id == 'source'
        bundle.close()  # Existing explicit borrower retains authority through stream flush.
        with resources.proxy_edit_lease('proxy') as editable:
            assert not editable
    assert events == ['borrowed-before-release']
    with resources.proxy_edit_lease('proxy') as editable:
        assert editable
    assert pipeline._collection_route_context is None


@pytest.mark.parametrize('policy', ['required_argument','bound_context'])
def test_feature_off_required_unknown_source_never_downloads(tmp_path, monkeypatch, policy):
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', False)
    monkeypatch.setattr(asset_utils.requests, 'Session', lambda: pytest.fail('direct network'))
    kwargs = {'required_route': True} if policy == 'required_argument' else {'route_context': SimpleNamespace(snapshot=object())}
    with pytest.raises(crawler_route.CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
        asset_utils.download_url_with_error('https://platform.example/image', tmp_path/'media', **kwargs)


def test_feature_off_required_task_unknown_source_cannot_reach_model_url(tmp_path, monkeypatch):
    from backend.audit_agent import pipeline as pipeline_module
    from backend.audit_agent.models import AuditSubject
    pipeline = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id = 'required-task'
    subject = AuditSubject('dy','42','','','',{},['https://platform.example/image'],[],[])
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', False)
    monkeypatch.setattr(pipeline_module.job_store, 'get', lambda _: {'crawler_account_id':'required-account','control':{}})
    monkeypatch.setattr(pipeline_module.job_store, 'log', lambda *a, **k: None)
    monkeypatch.setattr(pipeline_module, 'crawler_account_store', SimpleNamespace(get=lambda _: {'proxy_required':True}))
    monkeypatch.setattr(asset_utils.requests, 'Session', lambda: pytest.fail('direct download'))
    monkeypatch.setattr(pipeline, '_run_image_audit', lambda *a, **k: pytest.fail('model received platform URL'))
    with pytest.raises(crawler_route.CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
        pipeline._analyze_images(subject, tmp_path/'images')


def test_feature_off_never_required_unknown_source_retains_legacy_download(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', False)
    class Response:
        status_code=200; content=b'legacy'; headers={}
        def raise_for_status(self): pass
        def close(self): pass
    class Session:
        trust_env = True
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def get(self,*args,**kwargs):
            assert self.trust_env is True
            return Response()
    monkeypatch.setattr(asset_utils.requests, 'Session', Session)
    monkeypatch.setattr(crawler_route, 'authorize_source_route', lambda *a, **k: pytest.fail('legacy borrowed'))
    result=asset_utils.download_url_with_error('https://platform.example/image', tmp_path/'media')
    assert result.path.read_bytes()==b'legacy'


def test_actual_trial_media_over_remaining_cap_is_not_published_and_stops_next_request(tmp_path, monkeypatch):
    import sqlite3
    from proxy_fixture import local_proxy
    from backend.audit_agent.crawler_browser import load_request_scheduler
    trial_id = 'finite-trial'
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', True)
    monkeypatch.setattr(settings, 'crawler_proxy_trial_id', trial_id)
    db = tmp_path/'existing-scheduler.sqlite'
    load_request_scheduler().RequestScheduler(db, platform='dy', min_interval=0, media_interval=0, per_minute=1000, cooldown_seconds=0)
    with local_proxy(tmp_path/'network') as fixture:
        snapshot=SimpleNamespace(proxy_server=fixture.server, username=fixture.username, password=fixture.password,
                                 scheduler_db_path=str(db))
        bundle=SimpleNamespace(snapshot=snapshot)
        @contextmanager
        def authorized(*args, **kwargs):
            yield bundle
        monkeypatch.setattr(crawler_route, 'authorize_source_route', authorized)
        _real_transport_helper(monkeypatch, tmp_path, bundle, fixture)
        limits=asset_utils._trial_media_limits(snapshot)
        with sqlite3.connect(db) as conn:
            conn.execute('UPDATE proxy_trial_limits SET media_bytes=? WHERE trial_id=?',
                         (20*1024*1024-1,trial_id))
        target=tmp_path/'media.bin'
        options=dict(platform='dy', collection_route_provenance=source(), owner_id='owner', execution_id='run', verify=fixture.ca)
        with pytest.raises(crawler_route.CollectionRouteError, match='PROBE_BUDGET_EXHAUSTED'):
            asset_utils.download_url_with_error(fixture.url('/image'), target, **options)
        assert not target.exists() and not list(tmp_path.glob('.*.partial'))
        state=limits.snapshot()
        assert state['media_bytes']==20*1024*1024-1 and state['media_files']==1
        assert state['active_media']==0 and state['stopped']
        before=len(fixture.origins)
        with pytest.raises(crawler_route.CollectionRouteError, match='PROBE_BUDGET_EXHAUSTED'):
            asset_utils.download_url_with_error(fixture.url('/image'), target, **options)
        assert len(fixture.origins)==before
        fixture.assert_routed()


def test_trial_media_success_is_atomically_published_and_file_budget_is_shared(tmp_path, monkeypatch):
    from proxy_fixture import local_proxy
    from backend.audit_agent.crawler_browser import load_request_scheduler
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', True)
    monkeypatch.setattr(settings, 'crawler_proxy_trial_id', 'two-files')
    db=tmp_path/'existing-scheduler.sqlite'
    load_request_scheduler().RequestScheduler(db, platform='dy', min_interval=0, media_interval=0, per_minute=1000, cooldown_seconds=0)
    with local_proxy(tmp_path/'network') as fixture:
        snapshot=SimpleNamespace(proxy_server=fixture.server, username=fixture.username, password=fixture.password,
                                 scheduler_db_path=str(db))
        bundle=SimpleNamespace(snapshot=snapshot)
        @contextmanager
        def authorized(*args, **kwargs): yield bundle
        monkeypatch.setattr(crawler_route, 'authorize_source_route', authorized)
        _real_transport_helper(monkeypatch, tmp_path, bundle, fixture)
        options=dict(platform='dy', collection_route_provenance=source(), owner_id='owner', execution_id='run', verify=fixture.ca)
        first=asset_utils.download_url_with_error(fixture.url('/image'), tmp_path/'first', **options)
        second=asset_utils.download_url_with_error(fixture.url('/image', https=False), tmp_path/'second', **options)
        assert first.path.read_bytes() and second.path.read_bytes()
        state=asset_utils._trial_media_limits(snapshot).snapshot()
        assert state['media_files']==2 and state['active_media']==0
        assert state['media_bytes']==first.path.stat().st_size+second.path.stat().st_size
        count=len(fixture.origins)
        with pytest.raises(crawler_route.CollectionRouteError, match='PROBE_BUDGET_EXHAUSTED'):
            asset_utils.download_url_with_error(fixture.url('/image'), tmp_path/'third', **options)
        assert len(fixture.origins)==count and not (tmp_path/'third').exists()
        assert not list(tmp_path.glob('.*.partial'))
        fixture.assert_routed()


@pytest.mark.parametrize('trial', [False, True], ids=['normal-bound','trial-bound'])
def test_real_slow_drip_total_deadline_cancels_body_and_releases_all_scopes(tmp_path, monkeypatch, trial):
    import sqlite3
    from proxy_fixture import local_proxy
    monkeypatch.setattr(settings,'crawler_proxy_enabled',True)
    monkeypatch.setattr(settings,'crawler_proxy_trial_id','slow-trial' if trial else '')
    with local_proxy(tmp_path/'network') as fixture:
        snapshot=SimpleNamespace(proxy_server=fixture.server,username=fixture.username,password=fixture.password)
        bundle=SimpleNamespace(snapshot=snapshot)
        @contextmanager
        def authorized(*a,**k):yield bundle
        monkeypatch.setattr(crawler_route,'authorize_source_route',authorized)
        _real_transport_helper(monkeypatch,tmp_path,bundle,fixture)
        if trial:
            limits=asset_utils._trial_media_limits(snapshot)
            with sqlite3.connect(snapshot.scheduler_db_path) as conn:
                conn.execute("UPDATE proxy_trial_limits SET media_seconds=299.7 WHERE trial_id='slow-trial'")
        started=time.monotonic()
        target=tmp_path/'media.bin'
        with pytest.raises(crawler_route.CollectionRouteError,match='PROBE_BUDGET_EXHAUSTED' if trial else 'PROXY_UNAVAILABLE'):
            asset_utils.download_url_with_error(fixture.url('/slow-drip'),target,timeout=5 if trial else .3,
                platform='dy',collection_route_provenance=source(),owner_id='owner',execution_id='run',verify=fixture.ca)
        assert time.monotonic()-started<1.5
        assert fixture.drip_cancelled.wait(timeout=2),'body socket remained active after deadline'
        assert not target.exists() and not list(tmp_path.glob('.*.partial'))
        with sqlite3.connect(snapshot.scheduler_db_path) as conn:
            assert conn.execute('SELECT COUNT(*) FROM request_scheduler_leases').fetchone()[0]==0
        if trial:
            assert limits.snapshot()['stopped']
            assert limits.snapshot()['active_media']==0
        fixture.assert_routed()


def test_never_required_legacy_download_uses_existing_environment_proxy(tmp_path, monkeypatch):
    from proxy_fixture import local_proxy
    monkeypatch.setattr(settings,'crawler_proxy_enabled',False)
    monkeypatch.setattr(settings,'crawler_proxy_trial_id','')
    monkeypatch.delenv('MEDIACRAWLER_PROXY_TRIAL_ID',raising=False)
    with local_proxy(tmp_path/'network') as fixture:
        monkeypatch.setenv('HTTP_PROXY',fixture.authenticated_url)
        monkeypatch.setenv('HTTPS_PROXY',fixture.authenticated_url)
        monkeypatch.setenv('ALL_PROXY',fixture.authenticated_url)
        monkeypatch.setenv('NO_PROXY','')
        monkeypatch.setattr(crawler_route,'authorize_source_route',lambda *a,**k:pytest.fail('legacy resolved bound route'))
        result=asset_utils.download_url_with_error(fixture.url('/image',https=False),tmp_path/'legacy.bin')
        assert result.path is not None and result.path.read_bytes()
        assert any(event['kind']=='forward' for event in fixture.events)
        fixture.assert_routed()
