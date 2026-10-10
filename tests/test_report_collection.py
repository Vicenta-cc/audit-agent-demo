import json
import sqlite3
from uuid import uuid4
import pytest
from test_investigation_creation_conversation import creation_stack
from report_collection_fixtures import publish,comment
from backend.report_collection.service import ReportCollectionService
from backend.report_collection.access import owns_report
from backend.report_collection.tools import INPUTS,configure
from backend.investigation_creation.principal import Principal

@pytest.fixture
def collection(creation_stack,tmp_path):
    stack=creation_stack
    reports={}
    for name,comments in {
        'A':[comment('shared','same-user','low'),comment('a','a-user','high'),comment('unknown','','high')],
        'B':[comment('shared','same-user','high'),comment('b','b-user','high')],
        'C':[comment('C-SECRET','same-user','high')],
        'D':[comment('D-SECRET','same-user','high')],
    }.items():
        reports[name]=publish(tmp_path/name,name,comments,['旅游']+(['投诉'] if name=='B' else []),
            'v2' if name=='B' else 'v1',['e2'] if name=='B' else ['e1'],db=stack['resource_db'])
    # Synthetic completed task ownership, read by the real production ACL.
    with stack['creation_store'].admission.connect() as db:
        for name,r in reports.items():
            db.execute("""INSERT INTO task_admissions
                (id,owner_id,task_id,job_id,kind,day,state,request_key,fingerprint,payload_json,created_at,updated_at)
                VALUES(?,?,?,?,?,'2026-10-10','SUCCEEDED',?,?,'{}','2026-10-10','2026-10-10')""",
                (name,'principal-b' if name=='D' else 'principal-a',r.task,r.task,'fixture',name,name))
    service=ReportCollectionService(conversation=stack['conversation'],report_store=stack['report_store'],
        can_read=lambda p,item:owns_report(stack['creation_store'],p,item),path=tmp_path/'collections.sqlite3')
    stack['conversation'].report_collection=service
    configure(service)
    client=stack['client']; sid=client.post('/api/investigation-workspaces',json={}).json()['workspace_session_id']
    response=client.post(f'/api/investigation-workspaces/{sid}/report-sources',json={'reports':[
        {'label':n,'report_version_id':reports[n].version} for n in ['A','B']]})
    assert response.status_code==200,response.text
    stack.update(collection=service,reports=reports,sid=sid)
    return stack

def begin(stack):
    return stack['conversation'].accept_message(stack['sid'],client_message_id=uuid4().hex,
        content='测试',principal=Principal('principal-a'))[0].id

def call(stack,turn,name,**args):
    parsed=INPUTS[name].model_validate(args).model_dump()
    return stack['collection'].execute(name,parsed,stack['sid'],turn)

def finish(stack,turn):
    # Transport completion is independently covered by the real API acceptance.
    with stack['conversation'].store._connect() as db:
        db.execute("UPDATE investigation_turns SET status='completed' WHERE id=?",(turn,))

def test_union_intersection_limit_and_followup(collection):
    s=collection;t=begin(s)
    listing=call(s,t,'list_conversation_reports'); refs={r['label']:r['report_ref'] for r in listing['reports']}
    data=call(s,t,'query_conversation_reports',kind='accounts',common_only=True,scope='all')
    assert data['total_count']==1
    account=data['items'][0]
    assert account['unique_comment_count']==1
    assert {r['risk_level'] for r in account['observations']}=={'low','high'}
    assert {r['source']['label'] for r in account['observations']}=={'A','B'}
    assert data['unresolved_identity_comments']==1
    finish(s,t);t=begin(s)
    b=call(s,t,'query_conversation_reports',kind='comments',report_refs=[refs['B']],
        account_ref=account['account_ref'],from_result_ref=data['result_ref'])
    assert b['total_count']==1 and b['items'][0]['source']['label']=='B'
    finish(s,t);t=begin(s)
    follow=call(s,t,'query_conversation_reports',kind='accounts')
    assert follow['total_count']==1
    assert [x['label'] for x in follow['sources']]==['B']
    with pytest.raises(PermissionError): call(s,t,'query_conversation_reports',scope='all')
    finish(s,t);t=begin(s)
    all_results=call(s,t,'query_conversation_reports',scope='all',limit=1)
    assert all_results['total_count']==5 and all_results['has_more']
    page=call(s,t,'read_conversation_result',result_ref=all_results['result_ref'],offset=1,limit=50)
    assert len(page['items'])==4 and not page['has_more']
    assert 'C-SECRET' not in json.dumps(page) and 'D-SECRET' not in json.dumps(page)

def test_config_frozen_not_current(collection):
    s=collection;t=begin(s)
    refs=[r['report_ref'] for r in call(s,t,'list_conversation_reports')['reports']]
    before=call(s,t,'compare_conversation_config',report_refs=refs)
    assert before['configs'][0]['keywords']==['旅游']
    assert before['configs'][1]['keywords']==['旅游','投诉']
    assert {'keywords','rules','exemptions','ruleset_revision'} <= {i['field'] for i in before['items']}
    s['reports']['B'].configs.create(job_id='task-B',audit_config={'rule_snapshot':{'rules':['LIVE-CHANGED']}})
    after=call(s,t,'compare_conversation_config',report_refs=refs)
    assert before['configs']==after['configs']

def test_cross_session_owner_and_revocation(collection):
    s=collection;t=begin(s)
    data=call(s,t,'query_conversation_reports',scope='all')
    with pytest.raises(PermissionError):
        call(s,t,'query_conversation_reports',report_refs=['report-forged-C'])
    other=s['client'].post('/api/investigation-workspaces',json={}).json()['workspace_session_id']
    with pytest.raises(PermissionError):
        s['collection'].result(data['result_ref'],other,[])
    response=s['client'].post(f"/api/investigation-workspaces/{s['sid']}/report-sources",json={'reports':[
        {'label':'D','report_version_id':s['reports']['D'].version}]})
    assert response.status_code==404
    s['principals'].current=Principal('principal-b',role='admin')
    assert s['client'].get(f"/api/investigation-workspaces/{s['sid']}/report-sources").status_code==404
    with s['creation_store'].admission.connect() as db:
        db.execute("UPDATE task_admissions SET owner_id='revoked' WHERE task_id='task-A'")
    with pytest.raises(PermissionError): call(s,t,'read_conversation_result',result_ref=data['result_ref'])
    with pytest.raises(PermissionError): s['collection'].validate_history(s['sid'])

def test_new_membership_does_not_expand_accepted_turn(collection):
    s=collection;t=begin(s)
    s['collection'].register(s['sid'],Principal('principal-a'),[{'label':'C','report_version_id':s['reports']['C'].version}])
    first=call(s,t,'query_conversation_reports',scope='all')
    assert [r['label'] for r in first['sources']]==['A','B']
    finish(s,t); t2=begin(s)
    inherited=call(s,t2,'query_conversation_reports')
    assert [r['label'] for r in inherited['sources']]==['A','B']
    with pytest.raises(PermissionError): call(s,t2,'query_conversation_reports',scope='all')
    finish(s,t2);t3=begin(s)
    expanded=call(s,t3,'query_conversation_reports',scope='all')
    assert [r['label'] for r in expanded['sources']]==['A','B','C']


def test_real_login_cookie_csrf_owner_acl(collection,tmp_path):
    from report_collection_fixtures import authenticated_clients
    from fastapi.testclient import TestClient
    s=collection
    owner,admin,sid,auth=authenticated_clients(s,tmp_path)
    try:
        url=f'/api/investigation-workspaces/{sid}/report-sources'
        assert owner.get(url).status_code==200
        assert admin.get(url).status_code==404
        assert TestClient(owner.app).get(url).status_code==401
        assert owner.post(url,json={'reports':[{'label':'D','report_version_id':s['reports']['D'].version}]}).status_code==404
        assert owner.post(url,json={'reports':[{'label':'Unknown','report_version_id':'not-real'}]}).status_code==404
        token=owner.headers.pop('X-CSRF-Token')
        assert owner.post(url,json={'reports':[{'label':'C','report_version_id':s['reports']['C'].version}]}).status_code==403
        owner.headers['X-CSRF-Token']=token
    finally:
        owner.close();admin.close()


def test_collection_registry_excludes_legacy_and_creation_tools(collection,tmp_path):
    from backend.hermes_runtime.adapter import HermesRuntimeBinding
    from backend.report_collection.tools import SCHEMAS
    binding=HermesRuntimeBinding()
    with binding.product_mode_execution(tmp_path/'plugin-home',product_mode='collection'):
        definitions=binding.tool_definitions(enabled_toolsets=['investigation'],product_mode='collection')
        assert {d['function']['name'] for d in definitions}=={s['name'] for s in SCHEMAS}
    with binding.product_mode_execution(tmp_path/'plugin-home',product_mode='creation'):
        definitions=binding.tool_definitions(enabled_toolsets=['investigation'],product_mode='creation')
        assert 'query_conversation_reports' not in {d['function']['name'] for d in definitions}


def test_reading_prior_result_becomes_next_turn_focus(collection):
    s=collection;t=begin(s)
    original=call(s,t,'query_conversation_reports',scope='all')
    finish(s,t);t=begin(s)
    b=next(r for r in call(s,t,'list_conversation_reports')['reports'] if r['label']=='B')
    call(s,t,'query_conversation_reports',report_refs=[b['report_ref']])
    finish(s,t);t=begin(s)
    call(s,t,'read_conversation_result',result_ref=original['result_ref'])
    finish(s,t);t=begin(s)
    followed=call(s,t,'query_conversation_reports')
    assert {r['label'] for r in followed['sources']}=={'A','B'}


def test_failed_turn_not_inherited_and_registration_atomic(collection):
    s=collection;t=begin(s)
    original=call(s,t,'query_conversation_reports',scope='all')
    finish(s,t);t=begin(s)
    b=next(r for r in call(s,t,'list_conversation_reports')['reports'] if r['label']=='B')
    call(s,t,'query_conversation_reports',report_refs=[b['report_ref']])
    s['conversation'].store.fail_turn(t,error_code='fixture',safe_message='fixture',retryable=False)
    t=begin(s)
    assert {r['label'] for r in call(s,t,'query_conversation_reports')['sources']}=={'A','B'}
    with pytest.raises(PermissionError):
        s['collection'].register(s['sid'],Principal('principal-a'),[
            {'label':'C','report_version_id':s['reports']['C'].version},
            {'label':'D','report_version_id':s['reports']['D'].version}])
    assert len(s['collection'].members(s['sid'],Principal('principal-a')))==2


def test_tampered_snapshot_identity_is_rejected(collection):
    s=collection;t=begin(s)
    call(s,t,'query_conversation_reports',scope='all')
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with s['report_store']._connect() as db:
            db.execute("UPDATE report_versions SET content_hash='tampered' WHERE id=?",(s['reports']['A'].version,))
    # Also reject corruption of a persisted read-only image after a process restart.
    image=next((s['collection'].path.parent/'report-snapshots').glob('*.sqlite3'))
    image.chmod(0o600)
    image.write_bytes(image.read_bytes()+b'corrupt-fixture')
    with pytest.raises(RuntimeError, match='SHA-256'):
        call(s,t,'query_conversation_reports',scope='all')


def test_configuration_uses_confirmed_run_snapshot(collection):
    from test_investigation_creation_conversation import _create_completed_turn,_publish_fake_run
    s=collection
    prepared=_create_completed_turn(s,workspace_key='frozen-execution-source')
    run=_publish_fake_run(s,prepared,idempotency_key='frozen-execution-source')
    frozen={'resolved_search_terms':['实际执行关键词'],
        'ruleset_revision':{'id':'frozen-rule','version':7},
        'execution':{'api_key':'must-never-be-exposed','rule_snapshot':{
            'decision_rules':[{'rule_id':'r1','condition':'执行时条件'}],
            'general_exemptions':[{'exemption_id':'e1','condition':'执行时豁免'}],
            'thresholds':{'high':7}}}}
    with s['creation_store']._connect() as db:
        db.execute("UPDATE investigation_runs SET status='PUBLISHED', report_version_id=?, confirmed_configuration_json=? WHERE id=?",
            (s['reports']['A'].version,json.dumps(frozen),run['run_id']))
    sid=s['conversation'].create_session(principal=Principal('principal-a')).id
    members=s['collection'].register(sid,Principal('principal-a'),[
        {'label':'A','report_version_id':s['reports']['A'].version}])
    config=s['collection'].repository(members[0])[1]
    assert config['keywords']==['实际执行关键词']
    assert config['rules'][0]['condition']=='执行时条件'
    assert config['exemptions'][0]['condition']=='执行时豁免'
    assert 'must-never-be-exposed' not in json.dumps(members)
    # Later execution-store edits do not rewrite this registered frozen identity.
    with s['creation_store']._connect() as db:
        db.execute("UPDATE investigation_runs SET confirmed_configuration_json='{}' WHERE id=?",(run['run_id'],))
    assert s['collection'].repository(members[0])[1]==config
