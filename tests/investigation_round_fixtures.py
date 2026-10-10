"""Local acceptance: real worker/publisher, synthetic collection and audit I/O.

No crawler, audit provider or production storage is reachable through this adapter.
"""
import json
from functools import partial
from types import SimpleNamespace
from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.audit_agent.audit_policy_store import TaskAuditConfigRevisionStore
from backend.audit_agent.ingestion import AuditResultStore, IngestionStore
from backend.audit_agent.job_store import JobStore
from backend.investigation_creation.adapters import R31ReportAdapter, ProductSessionAdapter
from backend.investigation_creation.worker import InvestigationWorker
from backend.reporting.integration_source import CanonicalReportSource
from backend.reporting.runtime import R31ReportRuntime


class SyntheticExecution:
    def __init__(self, stack):
        self.stack = stack
        self.db = stack['resource_db']
        self.root = self.db.parent / 'synthetic-pipeline'
        self.root.mkdir(exist_ok=True)

    @staticmethod
    def job_id_for_run(run_id):
        return 'round-fixture-' + run_id.split(':')[-1]

    def ensure_job(self, run):
        job = self.job_id_for_run(run.id)
        jobs = JobStore(self.db)
        if not jobs.get(job):
            jobs.create(job_id=job, platform='dy', status='running', display_name=run.id,
                crawl_mode='search', max_notes=1, analyze_limit=1)
        return job

    def run_pipeline(self, job, configuration):
        configs = TaskAuditConfigRevisionStore(self.db)
        revision = configs.create(job_id=job, audit_config={
            'capabilities':['text','comment'], 'rule_snapshot':configuration.get('rule_snapshot', {})})
        ingest = IngestionStore(self.db)
        batch = self.root / (job + '.json')
        batch.write_text(json.dumps({'task_id':job,'platform':'dy','items':[
            {'aweme_id':'10001','title':'本地验收帖子','desc':'隔离数据','create_time':1788825600}]}))
        ref = ingest.ingest_batch(batch,self.root/'raw')[0]
        result = AuditResultStore(self.db).upsert_result(job_id=job, platform='dy', content_key='10001',
            content_id=ref['content_id'], audit_config_revision_id=revision['id'], result={
                'title':'本地验收帖子','url':'https://www.douyin.com/video/10001',
                'author':{'nickname':'发布者','sec_uid':'publisher'},'decision':'review','risk_level':'medium',
                'summary':'合成研判结果，未调用审核模型', 'comments':[
                    {'comment_id':'shared-comment','sec_uid':'shared-user','nickname':'常来看看',
                     'content':'本地两轮调查的同一条评论','audit_status':'completed','risk_level':'low'}],
                'evidence_items':[],'video_results':[]})
        ingest.mark_content_status('dy','10001','completed',task_id=job,audit_result_id=result['id'])
        JobStore(self.db).update(job,status='completed')

    def get_job_state(self, job):
        current=JobStore(self.db).get(job)
        return {'status':current['status'],'error':'','audit_results':AuditResultStore(self.db).list_results(job_id=job)['items'],'task_stats':{
            'ingested_count':1,'pending_analysis_count':0,'analyzing_count':0,'completed_analysis_count':1}} if current else None

    def validate_selected_content_payloads(self, job):
        IngestionStore(self.db).validated_refs_for_task(job)


def worker(stack):
    execution = SyntheticExecution(stack)
    class Source(CanonicalReportSource):
        def coverage(self, task_id):
            return {'fixture': True}
        def config(self, task_id):
            run=stack['creation_store'].get_run_for_job(task_id)
            result=super().config(task_id)
            result.update(run.confirmed_configuration)
            return result
    class Runtime(R31ReportRuntime):
        def generate(self, task_id, **kwargs):
            return super().generate(task_id, source=Source(stack['resource_db'],execution.root/'outputs'),
                checkpoint_path=execution.root/'checkpoints.sqlite3', **kwargs)
    return InvestigationWorker(stack['creation_store'], execution_adapter=execution,
        report_adapter=R31ReportAdapter(store=stack['report_store'],runtime=Runtime(stack['report_store'])),
        session_adapter=ProductSessionAdapter(SimpleNamespace(create_session=partial(
            HermesInvestigationAgentService.create_session, stack['report_service']))))


def publish_round(stack, result, *, idempotency_key):
    artifact = result['terminal']['artifact']
    response = stack['client'].post(f"/api/investigation-drafts/{artifact['draft_id']}/confirm-and-queue",
        headers={'Idempotency-Key':idempotency_key}, json={
            'expected_revision':artifact['draft_revision'], 'confirmed':True})
    assert response.status_code == 202, response.text
    run=worker(stack).run_once()
    assert run is not None and run.status.value == 'PUBLISHED', (run.status, run.error_code, run.error_message) if run else None
    return stack['app_service'].get_run(run.id,principal=stack['conversation'].principal_for_session(stack['sid'])).model_dump(mode='json')
