from pathlib import Path
from types import SimpleNamespace
from cryptography.fernet import Fernet
import backend.audit_agent.pipeline as pipeline_module
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_adapter import CrawlOutput, CrawlerVerificationError
from backend.audit_agent.ingestion import AuditResultStore, IngestionStore
from backend.audit_agent.job_store import JobStore
from backend.audit_agent.pipeline import AuditPipeline

class VerifyThenSuccessCrawler:
    def __init__(self): self.calls=[]
    def run_search(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls)==1: raise CrawlerVerificationError('simulated verify')
        item={'aweme_id':'post-1','desc':'盘口测试内容'}
        if kwargs.get('content_callback'): kwargs['content_callback']([item],[])
        return CrawlOutput(kwargs['platform'],[item],[],Path(kwargs['save_root']),command=['fake'])

def test_pipeline_verify_rotates_account_and_keeps_ingested_content(tmp_path, monkeypatch):
    jobs=JobStore(tmp_path/'jobs.sqlite3'); accounts=CrawlerAccountStore(tmp_path/'accounts.sqlite3')
    ingestion=IngestionStore(tmp_path/'ingestion.sqlite3'); results=AuditResultStore(tmp_path/'ingestion.sqlite3')
    cipher=AuthStateCipher(Fernet.generate_key())
    first=accounts.create(platform='dy',display_name='first'); second=accounts.create(platform='dy',display_name='second')
    accounts.save_auth_state(first['id'],cipher.encrypt({'account':'first'})); accounts.save_auth_state(second['id'],cipher.encrypt({'account':'second'}))
    job_id='verify-rotation-pipeline'
    config={'platform':'dy','display_name':'verify rotation','crawl_mode':'search','keyword':'盘口','keyword_source':'keyword','lexicon_category':'soft','library_ids':[],'capabilities':['text'],'scoring_template':'balanced','rule_snapshot':{},'lexicon_keywords':[],'creator_url':'','creator_id':'','start_page':0,'max_notes':1,'max_comments':0,'max_concurrency':1,'max_items_per_minute':1,'crawler_account_id':first['id'],'get_sub_comment':False,'analyze_limit':0,'run_crawler':True,'source_output_id':None,'analysis_batch_size':1,'prompt_profile_snapshot':{},'policy_id':''}
    jobs.create(job_id=job_id,**config)
    monkeypatch.setattr(pipeline_module,'job_store',jobs); monkeypatch.setattr(pipeline_module,'crawler_account_store',accounts); monkeypatch.setattr(pipeline_module,'auth_state_cipher',cipher)
    monkeypatch.setattr(pipeline_module.settings,'outputs_dir',tmp_path/'outputs'); monkeypatch.setattr(pipeline_module.settings,'auto_analyze_crawled_content',False)
    pipeline=AuditPipeline.__new__(AuditPipeline); pipeline.job_id=job_id; pipeline.crawler=VerifyThenSuccessCrawler(); pipeline.ingestion=ingestion; pipeline.audit_results=results; pipeline.qwen=SimpleNamespace(provider_failure=''); pipeline.prompt_profile_snapshot={}; pipeline.audit_config_revision_id=''; pipeline.rule_snapshot={}; pipeline.authoritative_m3=False
    pipeline.run(SimpleNamespace(**config))
    assert len(pipeline.crawler.calls)==2
    assert pipeline.crawler.calls[0]['auth_state']=={'account':'first'}; assert pipeline.crawler.calls[1]['auth_state']=={'account':'second'}
    assert accounts.get(first['id'])['failure_kind']=='verify'; assert accounts.get(first['id'])['cooldown_until']; assert accounts.get(second['id'])['status']=='active'
    assert ingestion.stats_for_task(job_id)['ingested_count']==1
