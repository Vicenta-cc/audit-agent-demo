"""A stopped route freezes supplements while already local content still audits."""
import threading
from pathlib import Path

import pytest

from test_triage_account_rotation import env, _run_pipeline, RotationCrawler
from test_crawler_proxy_assets import source
from backend.audit_agent import pipeline as pipeline_module
from backend.audit_agent import asset_utils
from backend.audit_agent.ingestion import IngestionStore


def test_interrupted_proxy_stream_keeps_local_audit_and_defers_old_remote_source(env,monkeypatch):
    monkeypatch.setattr(pipeline_module.settings,'crawler_safety_enabled',True)
    monkeypatch.setattr(pipeline_module.settings,'crawler_proxy_enabled',False)
    monkeypatch.setattr(pipeline_module.settings,'batch_ingestion_enabled',True)
    monkeypatch.setattr(pipeline_module.settings,'stream_crawl_analysis',True)
    account=env.add('fixture')
    provenance=source(account['id'])
    local=env.tmp/'outputs'/'job-rot'/'assets'/'local'/'images'/'ready.png'
    local.parent.mkdir(parents=True)
    local.write_bytes(b'offline-local-fixture')
    done=threading.Event()
    analyzed=[]
    downloads=[]
    original_download=pipeline_module.AuditPipeline._download_subject_media
    def no_network(*args,**kwargs):
        pytest.fail('old proxy asset attempted a direct network request')
    monkeypatch.setattr(asset_utils.requests.sessions.Session,'request',no_network)
    def audit(self,subject):
        analyzed.append(subject.note_id)
        assert subject.collection_route_provenance==provenance
        if subject.note_id=='remote':
            downloads.append(subject.note_id)
            # Real supplement authorization refuses the retired source lease.
            original_download(self,subject,subject.image_urls[0],env.tmp/'remote.png')
            pytest.fail('retired proxy supplement was admitted')
        assert subject.local_image_paths==[str(local)]
        done.set()
        return {'note_id':subject.note_id,'decision':'pass','risk_level':'low','summary':'local fixture','evidence':[]}
    monkeypatch.setattr(pipeline_module.AuditPipeline,'_analyze_subject',audit)
    class InterruptedCollector(RotationCrawler):
        def collect(self,callback):
            callback([
                {'aweme_id':'remote','desc':'missing remote fixture','aweme_type':'68','note_download_url':'https://fixture.invalid/old.png','collection_route_provenance':provenance},
                {'aweme_id':'local','desc':'local fixture','collection_route_provenance':provenance},
            ],[])
            assert done.wait(3), 'route-deferred subject blocked the ready local subject'
            raise pipeline_module.CollectionRouteError('PROXY_EXIT_CHANGED')
    collector=InterruptedCollector()
    monkeypatch.setattr(pipeline_module.AuditPipeline,'_run_triaged_search',lambda self,**kwargs:collector.collect(kwargs['content_callback']))
    with pytest.raises(pipeline_module.CollectionRouteError,match='PROXY_REVISION_MISMATCH'):
        _run_pipeline(env,monkeypatch,collector,account,settings_auto_analyze=True,auto_analyze=True,analyze_limit=2,max_total_notes=2)
    assert analyzed==['remote','local'] and downloads==['remote']
    job=pipeline_module.job_store.get('job-rot')
    assert [item['note_id'] for item in job['items']]==['local']
    assert job['status']=='interrupted' and job['crawl_status']=='stopped'
    assert job['control']['collection_partial']
    assert job['control']['network_deferred']
    assert job['control']['network_deferred_content_ids']==['remote']
    assert not job['control'].get('analysis_stop_requested')
    refs=IngestionStore(env.tmp/'ingestion.sqlite3').refs_for_task('job-rot')
    assert {item['content_key']:item['analyze_status'] for item in refs}=={'remote':'queued','local':'completed'}
    assert all(item['collection_route_provenance']==provenance for item in refs)
    assert not (env.tmp/'remote.png').exists()
