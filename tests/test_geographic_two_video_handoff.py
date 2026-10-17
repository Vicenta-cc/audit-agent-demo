"""Offline production worker/queue handoff across two real route leases.

External crawler/model/probe responses are fixed; report generation uses the
production adapter without a checkpoint fixture.
"""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest

from test_geographic_draft_configuration import setup_geo, flow
from test_geographic_runtime_freeze import _adapter, _command
from test_unified_draft_configuration import P
from test_geographic_runtime_worker import AuditModel, AuthoredCrawler
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content


@pytest.fixture
def confirmed_two_terms(creation_stack, content, tmp_path, monkeypatch):
    from experiments.geographic_execution_plan.authority import capture_fixture_authority
    from backend.audit_agent.config import settings
    from backend.investigation_creation.geographic_runtime import IsolatedGeographicCapability
    monkeypatch.setattr(settings, 'triage_mode', 'select')
    first, ctx, manager, _, word, _, result, _ = setup_geo(creation_stack, content)
    word = manager.update(word['edit_id'], 1, [{'operation': 'upsert_entry',
        'values': {'term': '克拉玛依火灾', 'kind': 'variant',
            'parent_id': 'main-1'}}], **ctx)
    draft_id = result['data']['draft']['id']
    updated, _ = flow(creation_stack, first['session_id'], {'operation': 'update',
        'draft_id': draft_id, 'expected_revision': 1, 'platform': 'dy',
        'lexicon_ref': word['resource_ref']}, '使用抖音和刚更新的两个搜索词')
    assert updated['status'] == 'ok', updated
    authority = capture_fixture_authority(creation_stack['app_service'], draft_id, principal=P,
        session_id=first['session_id'], fixture_id='fixture:two-video-handoff')
    app = creation_stack['app_service']
    IsolatedGeographicCapability(tmp_path).install(app)
    run = app.confirm_and_queue(_command(authority['identity']), principal=P)
    adapter = _adapter(creation_stack)
    adapter.ensure_job(run)
    config = adapter.verify_run_job(run)
    assert config['max_total_notes'] == config['analyze_limit'] == 2
    assert len(config['geographic_plan']['execution_queries']) == 2
    return adapter, config


@pytest.fixture
def playable_video(tmp_path):
    from backend.audit_agent.config import settings
    configured = str(settings.ffmpeg_path or '')
    ffmpeg = shutil.which(configured) if configured else shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('local ffmpeg required for playable-video handoff verification')
    ffmpeg = str(Path(ffmpeg).resolve())
    video_fixture = tmp_path / 'offline-playable.mp4'
    subprocess.run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=black:s=16x16:r=1:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        str(video_fixture)], check=True, capture_output=True, timeout=15)
    return video_fixture, ffmpeg


def test_two_video_ready_delivery_overlaps_first_audit(
        confirmed_two_terms, creation_stack, content, tmp_path, monkeypatch, playable_video):
    from backend.audit_agent import crawler_route, pipeline
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    from backend.audit_agent.crawler_adapter import CrawlOutput
    from backend.investigation_creation.adapters import R31ReportAdapter, ProductSessionAdapter
    from backend.hermes_runtime.service import HermesInvestigationAgentService
    from backend.investigation.report_query import ReportQueryFacade
    from backend.domain.query_service import DomainQueryService
    from backend.domain.repository import DomainRepository
    from backend.investigation_creation.worker import InvestigationWorker
    from backend.reporting.store import ReportStore
    from backend.task_admission.resources import ExecutionLeaseBundle

    adapter, config = confirmed_two_terms
    video_fixture, ffmpeg = playable_video
    for name, value in {
        'data_dir': tmp_path / 'data', 'outputs_dir': tmp_path / 'outputs',
        'task_resource_lock_dir': tmp_path / 'locks',
        'crawler_browser_profile_root': tmp_path / 'profiles',
        'crawler_camoufox_state_root': tmp_path / 'physical-profiles',
        'request_scheduler_db': tmp_path / 'scheduler.sqlite3',
        'crawler_proxy_enabled': False, 'crawler_shared_exit_active_limit': 1,
        'stream_crawl_analysis': True, 'ocr_enabled': False,
        'app_auth_mode': 'disabled', 'ffmpeg_path': ffmpeg,
        'dashscope_api_key': 'offline-fixture-key', 'use_remote_vlm': False,
    }.items():
        monkeypatch.setattr(pipeline.settings, name, value)

    analysis_entered = threading.Event()
    allow_analysis = threading.Event()
    second_detail_started = threading.Event()
    first_route_released = threading.Event()
    detail_calls, bundles, handles, order, controller_errors = [], [], [], [], []
    contract = crawler_route.route_contract()
    monkeypatch.setattr(crawler_route, '_run_maintenance_probe', lambda bundle:
        contract.ProbeReceipt('service-direct', 1, 'offline-probe',
                              '203.0.113.9', time.time()))

    original_acquire = pipeline.acquire_route_execution
    def acquire(*args, **kwargs):
        bundle = original_acquire(*args, **kwargs)
        bundles.append(bundle)
        handles.extend(bundle._handles)
        return bundle
    monkeypatch.setattr(pipeline, 'acquire_route_execution', acquire)

    class TwoVideoCrawler(AuthoredCrawler):
        def run_search(self, **kwargs):
            result = super().run_search(**kwargs)
            key = 'same' if kwargs['keyword'] == config['geographic_plan']['execution_queries'][0]['query'] else 'second'
            result.contents[0]['aweme_id'] = key
            result.comments = []
            return result

        def run_detail(self, **kwargs):
            key = kwargs['content_id']
            bundle = kwargs['route_context']
            assert bundle in bundles and bundle.pass_fds
            if detail_calls:
                assert first_route_released.is_set(), 'second crawler started before producer released first route'
                assert not detail_calls[0][1].pass_fds
                assert analysis_entered.wait(60), 'first model audit never started'
                assert not allow_analysis.is_set(), 'second collection failed to overlap first audit'
                order.append('second_detail')
                second_detail_started.set()
            else:
                original_release = bundle._close_handles
                def release_handles():
                    original_release()
                    assert not bundle.pass_fds
                    order.append('first_route_released')
                    first_route_released.set()
                bundle._close_handles = release_handles
            for fd in bundle.pass_fds:
                os.fstat(fd)
            contender = ExecutionLeaseBundle('during-detail', 'owner', 'different-account')
            try:
                assert contender.acquire()
                assert not contender.acquire_exit_capacity('203.0.113.9')
            finally:
                contender.close()
            detail_calls.append((key, bundle))
            self.events.append(('detail', kwargs))
            snapshot = bundle.snapshot
            receipt = contract.ProbeReceipt(snapshot.proxy_id, snapshot.config_revision,
                snapshot.context_id, snapshot.observed_exit_ip, snapshot.observed_exit_at,
                snapshot.exit_generation)
            item = dict(aweme_id=key, title='克拉玛依市欠薪', desc=f'精采原文-{key}',
                        follower_count=1, user_id='offline-author',
                        sec_uid='offline-sec-user', nickname='离线作者')
            folder = kwargs['save_root'] / 'douyin' / 'videos' / key
            folder.mkdir(parents=True)
            shutil.copyfile(video_fixture, folder / 'video.mp4')
            item['video_download_url'] = f'https://offline.invalid/{key}.mp4'
            item['collection_route_provenance'] = contract.collection_route_provenance(
                snapshot, receipt, observation_id=f'offline-observation-{key}',
                source_task_id=bundle.execution_id, source_batch_id=f'offline-batch-{key}',
                collected_at=time.time(), context_id=snapshot.context_id)
            return CrawlOutput('dy', [item], [], kwargs['save_root'])

    class DelayedFirstAudit(AuditModel):
        def audit_text(self, prompt, **kwargs):
            if 'audit_fusion_v4' in prompt and not analysis_entered.is_set():
                order.append('first_analysis_entered')
                analysis_entered.set()
                assert allow_analysis.wait(60), 'controller never released first audit'
            return super().audit_text(prompt, **kwargs)

    def unblock_after_proving_overlap():
        try:
            assert analysis_entered.wait(60), 'first analysis never started'
            assert first_route_released.is_set(), 'producer route still retained at model audit'
            assert second_detail_started.wait(45), 'second collection blocked by unfinished first model audit'
            assert not allow_analysis.is_set()
            order.append('overlap_proved')
        except BaseException as error:
            controller_errors.append(error)
        finally:
            allow_analysis.set()

    model, crawler_events = DelayedFirstAudit(), []
    crawler = TwoVideoCrawler(crawler_events)
    cipher = AuthStateCipher(key_file=tmp_path / 'test.key')
    for account in adapter.crawler_account_store.list():
        adapter.crawler_account_store.save_auth_state(account['id'],
            cipher.encrypt({'cookies': [], 'origins': []}))
    monkeypatch.setattr(pipeline, 'job_store', adapter.job_store)
    monkeypatch.setattr(pipeline, 'crawler_account_store', adapter.crawler_account_store)
    monkeypatch.setattr(pipeline, 'auth_state_cipher', cipher)
    class ExternalModelClient(pipeline.QwenClient):
        def __new__(cls, *args, **kwargs):
            return model
    monkeypatch.setattr(pipeline, 'QwenClient', ExternalModelClient)
    monkeypatch.setattr(pipeline, 'MediaCrawlerAdapter', lambda: crawler)
    def factory(job_id):
        instance = pipeline.AuditPipeline(job_id)
        instance.ingestion = adapter.ingestion_store
        instance.audit_results = adapter.audit_result_store
        return instance
    adapter.pipeline_factory = factory

    analyzed_video_paths = []
    def external_video_analysis(self, index, video_path, video_dir, url, source, **kwargs):
        # ffmpeg/ASR/vision output is fixed; real producer readiness and local
        # discovery, route release, ingestion and fusion remain unmodified.
        assert source == 'local' and video_path.is_file()
        analyzed_video_paths.append(str(video_path))
        return {'index': index, 'source': source, 'local_path': str(video_path),
                'transcript': {'text': '', 'segments': []}, 'timeline_frames': [],
                'moments': [], 'moment_sheets': [], 'precise_sheets': [], 'errors': []}
    monkeypatch.setattr(pipeline.AuditPipeline, '_analyze_video_file', external_video_analysis)

    report_store = ReportStore(creation_stack['resource_db'])
    session_service = HermesInvestigationAgentService(
        report_facade=ReportQueryFacade(creation_stack['resource_db'],
            query_service=DomainQueryService(repository=DomainRepository(
                creation_stack['resource_db'], outputs_dir=tmp_path / 'outputs'))),
        store=creation_stack['conversation'].store, bind_runtime=False)
    worker = InvestigationWorker(creation_stack['creation_store'], execution_adapter=adapter,
        report_adapter=R31ReportAdapter(store=report_store),
        session_adapter=ProductSessionAdapter(session_service), heartbeat_interval_seconds=0.1)
    def forbidden(*args, **kwargs):
        raise AssertionError('offline handoff attempted external network/process')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    original_popen = subprocess.Popen
    allowed_probes = []
    def only_local_media_probe(command, *args, **kwargs):
        # The producer's decoder may spawn only this exact local ffmpeg check;
        # browser/crawler/process launches and URL-bearing commands stay denied.
        command = [str(arg) for arg in command] if isinstance(command, (list, tuple)) else []
        if (command and command[0] == ffmpeg and '-i' in command
                and command[-2:] == ['null', '-'] and not kwargs.get('shell')):
            source = command[command.index('-i') + 1]
            source_path = Path(source).resolve()
            source_path.relative_to(tmp_path.resolve())
            assert source_path.is_file()
            assert all('://' not in arg for arg in command)
            allowed_probes.append(str(source_path))
            return original_popen(command, *args, **kwargs)
        return forbidden(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', only_local_media_probe)

    controller = threading.Thread(target=unblock_after_proving_overlap)
    controller.start()
    try:
        result = worker.run_once()
        controller.join(12)
        assert not controller.is_alive(), 'controller thread failed to drain'
        assert not controller_errors, str(controller_errors)
        assert result.status.value == 'PUBLISHED', (result.error_code, result.error_message)
        job_id = adapter.job_id_for_run(result.id)
        job = adapter.job_store.get(job_id)
        assert [key for key, _ in detail_calls] == ['same', 'second']
        assert job['control']['geographic_selection']['handed_off'] == 2
        memberships, refs = adapter.ingestion_store.validated_selection_for_task(job_id)
        assert {ref['content_key'] for ref in refs} == {'same', 'second'}
        assert len(memberships) == 2
        source_by_content = {key: bundle.snapshot for key, bundle in detail_calls}
        for ref in refs:
            provenance = ref['item']['collection_route_provenance']
            source = source_by_content[ref['content_key']]
            assert provenance['source_account_id'] == source.account_id
            assert provenance['proxy_id'] == source.proxy_id
            assert provenance['observed_exit_ip'] == source.observed_exit_ip
            assert ref['item']['video_download_url'].endswith(f"/{ref['content_key']}.mp4")
        audits = adapter.audit_result_store.list_results(job_id=job_id)
        assert audits['total'] == 2
        assert {item['content_key'] for item in audits['items']} == {'same', 'second'}
        assert all(item['decision'] == 'pass' for item in audits['items'])
        report = report_store.get_frontend_report(result.report_version_id)
        assert len(report['posts']) == 2
        assert {post['original_text'] for post in report['posts']} == {
            '精采原文-same', '精采原文-second'}
        assert order.index('first_route_released') < order.index('first_analysis_entered') < order.index('second_detail')
        assert len(analyzed_video_paths) == 2
        assert len(set(allowed_probes)) == 2, 'both videos must pass real full decoding'
        assert handles and all(handle.closed for handle in handles)
        assert all(not bundle.pass_fds for bundle in bundles)
        fresh = ExecutionLeaseBundle('after-completion', 'owner', 'different-account')
        try:
            assert fresh.acquire() and fresh.acquire_exit_capacity('203.0.113.9')
        finally:
            fresh.close()
    finally:
        allow_analysis.set()
        controller.join(12)
        for bundle in bundles:
            type(bundle).close(bundle)


def test_real_single_proxy_and_exit_slots_remain_exclusive(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.audit_agent.config import settings
    from backend.task_admission.resources import ExecutionLeaseBundle
    monkeypatch.setattr(settings, 'task_resource_lock_dir', tmp_path / 'locks')
    monkeypatch.setattr(settings, 'crawler_shared_exit_active_limit', 1)
    snapshot = SimpleNamespace(proxy_id='offline-test-proxy')
    first = ExecutionLeaseBundle('first', 'owner', 'account-first', snapshot)
    second = ExecutionLeaseBundle('second', 'owner', 'account-second', snapshot)
    try:
        assert first.acquire(max_active_accounts=1)
        assert first.acquire_exit_capacity('203.0.113.9')
        assert len(first.pass_fds) == 4  # account, proxy shared, proxy active, physical exit
        for fd in first.pass_fds:
            os.fstat(fd)
        assert not second.acquire(max_active_accounts=1)
        assert not second.pass_fds
        first.close()
        assert second.acquire(max_active_accounts=1)
        assert second.acquire_exit_capacity('203.0.113.9')
    finally:
        first.close()
        second.close()
    assert not first.pass_fds and not second.pass_fds


def test_real_video_decoder_rejects_truncated_fixture(playable_video, tmp_path, monkeypatch):
    from backend.audit_agent.geographic_media import validate_media
    video, ffmpeg = playable_video
    def forbidden(*args, **kwargs):
        raise AssertionError('offline decoder attempted external network')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    validate_media(video, 'videos', ffmpeg=ffmpeg)
    truncated = tmp_path / 'truncated.mp4'
    truncated.write_bytes(video.read_bytes()[:100])
    with pytest.raises(ValueError, match='decode failed'):
        validate_media(truncated, 'videos', ffmpeg=ffmpeg)
