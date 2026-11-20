"""Prepared source-task media can be reused only through the selected source."""
import hashlib
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from backend.audit_agent import pipeline as pipeline_module
from backend.audit_agent.crawler_adapter import CrawlOutput, MediaCrawlerAdapter
from backend.audit_agent.geographic_media import GeographicMediaNotReady
from backend.audit_agent.ingestion import IngestionStore
from backend.audit_agent.job_store import JobStore
from backend.audit_agent.scoped_selection import digest
from test_triage_account_rotation import _run_pipeline, env  # noqa: F401


@pytest.fixture
def prepared_source(env, monkeypatch):
    monkeypatch.setattr(pipeline_module.settings, 'outputs_dir', env.tmp / 'outputs')
    monkeypatch.setattr(pipeline_module.settings, 'analysis_media_scope', 'all')
    monkeypatch.setattr(pipeline_module.settings, 'max_images_per_note', 2)
    jobs = JobStore(env.tmp / 'jobs.sqlite3')
    jobs.create(job_id='source-A', platform='dy', run_crawler=True, collect_media=True)
    jobs.update_control('source-A', triage_local_media=True)
    monkeypatch.setattr(pipeline_module, 'job_store', jobs)
    producer = pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    producer.job_id = 'source-A'
    root = env.tmp / 'outputs/source-A/crawler'
    attempt = root / 'detail_attempts/attempt-1'
    image = attempt / 'douyin/images/post-1/000.jpeg'
    image.parent.mkdir(parents=True)
    Image.new('RGB', (8, 8), 'blue').save(image)
    item = {'aweme_id': 'post-1', 'desc': 'prepared source subject', 'aweme_type': '68',
            'note_download_url': 'https://offline.invalid/image', 'source_keyword': 'source word'}
    comments = [{'aweme_id': 'post-1', 'comment_id': 'c1', 'content': 'source comment'}]
    output = CrawlOutput('dy', [item], comments, attempt)
    monkeypatch.setattr(pipeline_module, 'download_url_with_error',
                        lambda *a, **k: pytest.fail('source reuse attempted platform download'))
    producer._prepare_triage_delivery(output, root=root, save_root=root,
        collect_media=True, route=None, stop_checker=lambda: False)
    receipt = root / 'douyin/detail_completed' / (hashlib.sha256(b'post-1').hexdigest() + '.json')
    # This is the real completed-detail reader, not a fixture returning the item.
    crawler = MediaCrawlerAdapter.__new__(MediaCrawlerAdapter)
    return SimpleNamespace(root=root, image=image, receipt=receipt, crawler=crawler, comments=comments)


def test_analyze_existing_output_keeps_source_binding_and_local_media(env, monkeypatch, prepared_source):
    source = prepared_source
    primary = env.add('unused-for-local-reanalysis')
    analyzed = []

    def model(self, subject):
        assert self.job_id == 'job-rot'
        assert subject.media_readiness['binding']['job_id'] == 'source-A'
        assert subject.local_image_paths == [str(source.image)]
        assert not subject.image_urls and not subject.video_urls
        assert subject.comments == source.comments
        with Image.open(subject.local_image_paths[0]) as image:
            image.load()
        refs = self.ingestion.validated_refs_for_task(self.job_id)
        assert len(refs) == 1
        assert refs[0]['item']['triage_media_binding']['job_id'] == 'source-A'
        with pytest.raises(pipeline_module.CollectionRouteError, match='SUPPLEMENT_DEFERRED'):
            self._download_subject_media(subject, 'https://offline.invalid/forbidden', env.tmp / 'forbidden.jpg')
        analyzed.append(subject.note_id)
        return {'note_id': subject.note_id, 'content_key': subject.note_id}

    monkeypatch.setattr(pipeline_module.AuditPipeline, '_analyze_subject_with_capacity', model)
    job = _run_pipeline(env, monkeypatch, source.crawler, primary,
        run_crawler=False, source_output_id='source-A', collect_media=True,
        auto_analyze=True, analyze_limit=1, max_total_notes=1)
    assert job['status'] == 'completed' and not job.get('error'), job.get('error')
    assert analyzed == ['post-1'] and len(job['items']) == 1
    refs = IngestionStore(env.tmp / 'ingestion.sqlite3').validated_refs_for_task('job-rot')
    assert refs[0]['item']['triage_media_binding']['job_id'] == 'source-A'


def test_item_cannot_override_trusted_source_task_with_its_own_marker(env, monkeypatch, prepared_source):
    source = prepared_source
    payload = json.loads(source.receipt.read_text())
    item = payload['contents'][0]
    # Keep the item's digest coherent: rejection must be about the selected
    # source identity, not merely the added field invalidating its digest.
    item['media_source_task_id'] = 'forged-source'
    item['triage_media_binding']['job_id'] = 'forged-source'
    item['triage_media_binding']['content_digest'] = digest(
        {k: v for k, v in item.items() if k != 'triage_media_binding'})
    source.receipt.write_text(json.dumps(payload))
    monkeypatch.setattr(pipeline_module.AuditPipeline, '_analyze_subject_with_capacity',
                        lambda *a: pytest.fail('forged source reached model'))
    with pytest.raises(GeographicMediaNotReady, match='SUPPLEMENT_DEFERRED'):
        _run_pipeline(env, monkeypatch, source.crawler, env.add('unused'),
            run_crawler=False, source_output_id='source-A', collect_media=True,
            auto_analyze=True, analyze_limit=1, max_total_notes=1)
    job = pipeline_module.job_store.get('job-rot')
    # The existing route-error policy records SUPPLEMENT_DEFERRED as paused,
    # even for a local-only task; it must never become a completed analysis.
    assert job['status'] == 'crawl_paused' and job['error'] == 'SUPPLEMENT_DEFERRED'
    assert IngestionStore(env.tmp / 'ingestion.sqlite3').validated_refs_for_task('job-rot') == []
