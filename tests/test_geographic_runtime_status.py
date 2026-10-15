from types import SimpleNamespace
import pytest
from audit_agent.geographic_status import classify_geographic_status, geographic_completion_failure, selection_page
from audit_agent.pipeline import _geographic_progress_message


def report(**changes):
    return dict(unfinished_queries=[],reserved=0,collected=1,handed_off=1,collected_not_delivered=0,reasons=[])|changes


@pytest.mark.parametrize('changes',[
    {'unfinished_queries':['q']},{'reserved':1},{'collected_not_delivered':1},
    {'handed_off':0},{'reasons':['awaiting_reconciliation']},{'reasons':['collection_failed']},
    {'reserved':True},{'collected':-1},
])
def test_incomplete_cannot_publish(changes):
    assert geographic_completion_failure(report(**changes))
    assert classify_geographic_status(report(**changes))['status']=='incomplete'


def test_empty_differs_from_positive_complete():
    assert classify_geographic_status(report())['status']=='ready'
    assert classify_geographic_status(report(collected=0,handed_off=0))['status']=='empty'
    assert classify_geographic_status(None)['status']=='incomplete'


@pytest.mark.parametrize(('stage','progress','expected'), [
    ('capture_started', {'query':'克拉玛依大火'}, '地域预筛：正在抓取候选「克拉玛依大火」'),
    ('risk_started', {'query':'克拉玛依大火','rank':2}, '地域预筛：正在对「克拉玛依大火」第 2 条候选进行风险筛选'),
    ('geo_started', {'query':'克拉玛依大火','rank':2}, '地域预筛：正在对「克拉玛依大火」第 2 条候选进行地域判断'),
])
def test_geographic_progress_is_visible_in_pipeline_logs(stage, progress, expected):
    assert _geographic_progress_message({'stage':stage,'progress':progress}) == expected


def test_authorization_precedes_database_and_snapshot_access():
    calls=[]
    def get_run(run_id,principal):
        calls.append((run_id,principal))
        raise PermissionError('denied')
    service=SimpleNamespace(store=SimpleNamespace(get_run=get_run))
    with pytest.raises(PermissionError):selection_page(service,'other',SimpleNamespace(id='owner'))
    assert calls==[('other','owner')]


def test_worker_gate_rejects_pending_before_audit_completion(monkeypatch):
    import backend.investigation_creation.worker as worker_module
    worker=object.__new__(worker_module.InvestigationWorker)
    frozen=SimpleNamespace(schema_version='investigation-run-config-v5',execution=SimpleNamespace(geographic_plan={'frozen':True},analyze_limit=2,auto_analyze=None))
    monkeypatch.setattr(worker_module,'parse_confirmed_configuration_snapshot',lambda value:frozen)
    run=SimpleNamespace(job_id='job',confirmed_configuration={})
    state={'control':{'geographic_selection':report(reserved=1)},'task_stats':{'ingested_count':1,'completed_analysis_count':1}}
    assert worker._completion_gate_failure(run,state=state)[0]=='geographic_receipt_pending'
    state['control']['geographic_selection']=report(handed_off=0,collected=0)
    assert worker._completion_gate_failure(run,state=state)[0]=='geographic_membership_count_mismatch'


from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content


def test_authorized_real_run_endpoint_and_foreign_owner(creation_stack,content,monkeypatch,tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.investigation_creation import create_investigation_creation_router
    from backend.audit_agent.config import settings
    from backend.audit_agent.geographic_bridge import project_plan
    from backend.audit_agent.geographic_execution_store import GeographicExecutionStore
    from backend.investigation_creation.scope_search_plan import parse_confirmed_scope_search_plan
    from backend.investigation_creation.geographic_runtime import IsolatedGeographicCapability
    from backend.investigation_creation.contracts import parse_confirmed_configuration_snapshot
    from test_geographic_execution_plan import make_authority
    from test_geographic_runtime_freeze import _command,_adapter
    from test_unified_draft_configuration import P
    from backend.investigation_creation.principal import Principal
    monkeypatch.setattr(settings,'triage_mode','select')
    authority=make_authority(creation_stack,content)
    service=creation_stack['app_service']
    IsolatedGeographicCapability(tmp_path).install(service)
    run=service.confirm_and_queue(_command(authority['identity']),principal=P)
    claimed=service.store.claim_next('worker',lease_timeout_seconds=300)
    adapter=_adapter(creation_stack)
    job_id=adapter.ensure_job(claimed)
    run=service.store.bind_job(claimed.id,claimed.claim_token,job_id)
    frozen=parse_confirmed_configuration_snapshot(run.confirmed_configuration)
    raw=frozen.execution.geographic_plan
    p2=parse_confirmed_scope_search_plan(raw,expected_identity=raw['identity'],expected_digest=raw['plan_digest'])
    p4=project_plan(p2,execution_id='execution',run_id=run.id,job_id=job_id,config_hash=frozen.config_hash)
    state_store=GeographicExecutionStore(adapter.job_store.db_path,p4['binding']|p4['runtime_binding'],p4['plan_digest'],lambda db:True)
    state_store.create(p4)
    adapter.job_store.update_control(job_id,geographic_selection=report(execution_id='execution',plan_digest=p4['plan_digest'],unfinished_queries=['pending']))
    service.run_projector.job_store=adapter.job_store
    actor=[P]
    api=FastAPI();api.include_router(create_investigation_creation_router(service,principal_provider=lambda:actor[0]))
    client=TestClient(api)
    response=client.get(f'/api/investigation-runs/{run.id}/geographic-selection')
    assert response.status_code==200,response.text
    assert response.json()['status']=='incomplete'
    assert response.json()['items']==[]
    assert client.get(f'/api/investigation-runs/{run.id}/geographic-selection?limit=101').status_code==422
    actor[0]=Principal(id='foreign')
    assert client.get(f'/api/investigation-runs/{run.id}/geographic-selection').status_code==404
