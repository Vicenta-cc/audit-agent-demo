"""Offline real application workflow; only external crawler/model responses fixed."""
from pathlib import Path
from test_geographic_runtime_pipeline import confirmed, Crawler, Model
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content


class AuditModel(Model):
    enabled=True
    provider_failure=''
    def audit_text(self,prompt,**kwargs):
        if 'audit_fusion_v4' in prompt:
            self.calls.append((prompt,kwargs))
            return {'schema_version':'audit_fusion_v4','content_title':'克拉玛依市事件调查',
                'summary':'离线固定外部响应：原文为事件记录，无已确认违规依据。',
                'decision_suggestion':'pass','risk_level_suggestion':'none','primary_risk':'',
                'categories':[],'evidence_items':[],'rule_matches':[]}
        return super().audit_text(prompt,**kwargs)


class AuthoredCrawler(Crawler):
    def run_search(self,**kwargs):
        output=super().run_search(**kwargs)
        for item in output.contents:
            item.update(user_id='offline-author',sec_uid='offline-sec-user',nickname='离线作者')
        return output
    def run_detail(self,**kwargs):
        output=super().run_detail(**kwargs)
        for item in output.contents:
            item.update(user_id='offline-author',sec_uid='offline-sec-user',nickname='离线作者')
        return output


def test_real_worker_pipeline_audit_and_native_report(confirmed,creation_stack,tmp_path,monkeypatch):
    import backend.audit_agent.pipeline as pipeline_module
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    from backend.investigation_creation.worker import InvestigationWorker
    from backend.investigation_creation.adapters import R31ReportAdapter,ProductSessionAdapter
    from backend.reporting.store import ReportStore
    from backend.hermes_runtime.service import HermesInvestigationAgentService
    from backend.investigation.report_query import ReportQueryFacade
    from backend.domain.query_service import DomainQueryService
    from backend.domain.repository import DomainRepository
    adapter,config=confirmed
    settings=pipeline_module.settings
    for name,value in {'data_dir':tmp_path/'data','outputs_dir':tmp_path/'outputs',
            'task_resource_lock_dir':tmp_path/'locks','crawler_browser_profile_root':tmp_path/'profiles',
            'stream_crawl_analysis':True,'ocr_enabled':False,'app_auth_mode':'disabled'}.items():
        monkeypatch.setattr(settings,name,value)
    model=AuditModel();events=[]
    crawler=AuthoredCrawler(events,change=True)
    cipher=AuthStateCipher(key_file=tmp_path/'test.key')
    accounts=adapter.crawler_account_store
    for account in accounts.list():
        accounts.save_auth_state(account['id'],cipher.encrypt({'cookies':[],'origins':[]}))
    monkeypatch.setattr(pipeline_module,'job_store',adapter.job_store)
    monkeypatch.setattr(pipeline_module,'crawler_account_store',accounts)
    monkeypatch.setattr(pipeline_module,'auth_state_cipher',cipher)
    monkeypatch.setattr(pipeline_module,'QwenClient',lambda:model)
    monkeypatch.setattr(pipeline_module,'MediaCrawlerAdapter',lambda:crawler)
    def factory(job_id):
        pipeline=pipeline_module.AuditPipeline(job_id)
        pipeline.ingestion=adapter.ingestion_store
        pipeline.audit_results=adapter.audit_result_store
        return pipeline
    adapter.pipeline_factory=factory
    report_store=ReportStore(creation_stack['resource_db'])
    session_service=HermesInvestigationAgentService(report_facade=ReportQueryFacade(creation_stack['resource_db'],query_service=DomainQueryService(repository=DomainRepository(creation_stack['resource_db'],outputs_dir=tmp_path/'outputs'))),
        store=creation_stack['conversation'].store,bind_runtime=False)
    worker=InvestigationWorker(creation_stack['creation_store'],execution_adapter=adapter,
        report_adapter=R31ReportAdapter(store=report_store),session_adapter=ProductSessionAdapter(session_service),
        heartbeat_interval_seconds=0.1)
    import socket
    import subprocess
    def forbidden(*args,**kwargs):
        raise AssertionError("offline workflow attempted external network/process")
    monkeypatch.setattr(socket.socket,"connect",forbidden)
    monkeypatch.setattr(subprocess,"Popen",forbidden)
    result=worker.run_once()
    job=adapter.job_store.get(adapter.job_id_for_run(result.id))
    assert result.status.value=='PUBLISHED', (result.error_code,result.error_message,adapter.ingestion_store.stats_for_task(job['id']),[(r.get('message'),r.get('error_code')) for r in job.get('logs',[])])
    assert len([e for e in events if e[0]=='detail'])==1
    assert job['control']['geographic_selection']['handed_off']==1
    audit=adapter.audit_result_store.list_results(job_id=job['id'])
    assert audit['total']==1
    assert audit['items'][0]['decision']=='pass'
    assert audit['items'][0]['content_key']=='same'
    report=report_store.get_frontend_report(result.report_version_id)
    assert len(report['posts'])==1
    assert report['posts'][0]['post_ref']=='post-001'
    assert report['posts'][0]['original_text']=='精采原文'
    assert any('audit_fusion_v4' in prompt for prompt,_ in model.calls)
    assert creation_stack['conversation'].store.get_session(result.report_session_id) is not None
