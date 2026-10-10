"""Phase 2 actual non-thinking conversation; all business storage/I/O isolated.

The first report is prepared through real application tools and a local Worker.
Subsequent planning, mutations and queries use the configured conversation model.
Collection/auditing are synthetic; no production crawler or audit model is used.
"""
import argparse
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
from uuid import uuid4
from urllib.parse import urlsplit


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    root=args.output.resolve();os.umask(0o077);root.mkdir(parents=True,exist_ok=False)
    provider={k:os.environ[k] for k in ('CONVERSATION_API_KEY','CONVERSATION_BASE_URL','CONVERSATION_MODEL')}
    inherited={k:os.environ[k] for k in ('HOME','PATH','LANG','TMPDIR','PYTHONPATH') if k in os.environ}
    os.environ.clear();os.environ.update(inherited);os.environ.update(provider)
    os.environ.update(PYTHON_DOTENV_DISABLED='1',APP_AUTH_MODE='disabled',PYTHONDONTWRITEBYTECODE='1',
        INVESTIGATION_MULTI_ROUND_ENABLED='1',CONTINUOUS_RESOURCE_SESSION_ENABLED='1')
    for key,name in {'XHS_AUDIT_DATA_DIR':'data','XHS_AUDIT_OUTPUTS_DIR':'outputs','APP_AUTH_DB':'auth.sqlite3',
        'HERMES_HOME':'hermes','CRAWLER_AUTH_KEY_FILE':'data/crawler_auth.key','MEDIACRAWLER_DIR':'disabled-crawler',
        'MEDIACRAWLER_REQUEST_SCHEDULER_DB':'scheduler.sqlite3'}.items():os.environ[key]=str(root/name)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
    from test_investigation_creation_conversation import creation_stack
    from test_investigation_rounds import first
    from investigation_round_fixtures import publish_round,worker
    from backend.report_collection.service import ReportCollectionService
    from backend.report_collection.access import owns_report
    from backend.report_collection import tools
    from backend.investigation_creation.principal import Principal
    from backend.investigation_creation.tools import configure_hermes_investigation_creation_tools
    from openai.resources.chat.completions import Completions
    evidence={'model':provider['CONVERSATION_MODEL'],'host':urlsplit(provider['CONVERSATION_BASE_URL']).hostname,
        'thinking':'disabled','cloud_changed':False,'collection_and_audit':'synthetic local adapter',
        'worker_and_publisher':'real application implementations','turns':[],'calls':[],'status':'PREPARING'}
    def save():
        raw=json.dumps(evidence,ensure_ascii=False,indent=2,default=str)
        assert provider['CONVERSATION_API_KEY'] not in raw
        temp=root/'evidence.tmp';temp.write_text(raw);temp.replace(root/'evidence.json')
    fixture=creation_stack.__wrapped__(root);s=next(fixture)
    try:
        collection=ReportCollectionService(conversation=s['conversation'],report_store=s['report_store'],
            can_read=lambda p,item:owns_report(s['creation_store'],p,item),path=root/'collections.sqlite3',
            agent_driven=True,direct_delivery=True,direct_delivery_guidance=True)
        s['conversation'].report_collection=collection;tools.configure(collection)
        s['sid']=s['conversation'].create_session(principal=Principal('principal-a')).id
        evidence['replacement_lexicon']=s['app_service'].resource_management.save_library('lexicon','round-complaints',{
            'title':'第二轮验收投诉词库','entries':[
                {'id':'queue','kind':'main','term':'排队投诉'},
                {'id':'service','kind':'main','term':'服务投诉'}]},0,'seed-complaints',principal=Principal('principal-a'))
        seeded=first(s);initial=publish_round(s,seeded,idempotency_key='first-round')
        evidence['first_run']=initial
        original_first=s['creation_store'].get_run(initial['run_id'],principal='principal-a').confirmed_configuration
        s['conversation'].close()  # Clear scripted Agent, retain all durable chat state.
        s['conversation'].fake_runtime=False
        configure_hermes_investigation_creation_tools(s['tool_service'],
            principal_provider=s['conversation'].principal_for_session)
        client=s['client'];sid=s['sid']
        questions=[
            ('read_first','第1次调查里有哪些评论？列出来即可。'),
            ('copy_second','沿用第1次调查的配置，在这个会话准备第2次调查草案，先不要启动。'),
            ('edit_second','第2次调查的规则保持不变，关键词改用系统中已有的“第二轮验收投诉词库”（排队投诉、服务投诉），不要生成新的词库。先不要启动。'),
            ('confirm_second','确认刚才展示的第2次调查配置，启动这次调查。'),
            ('compare','第1次和第2次调查实际使用的关键词和审核规则有什么区别？'),
            ('only_second','只看第2次调查，把全部评论列出来。'),
            ('followup_expand','刚才那条评论是谁发的？这个账号在第1次调查也出现过吗？'),
            ('copy_third','沿用第2次的配置准备第3次调查草案，暂不启动。'),
            ('read_old','再看看第1次的配置，不修改任何调查。'),
        ]
        original=Completions.create
        active={}
        def observed(instance,*a,**kw):
            if len(evidence['calls'])>=90:raise RuntimeError('Acceptance model-call limit reached')
            assert kw.get('model')==evidence['model']
            assert kw.get('extra_body',{}).get('thinking')=={'type':'disabled'}
            number=len(evidence['calls'])+1
            name=f'request-{number:03d}.json'
            raw=json.dumps(kw,ensure_ascii=False,default=str)
            assert provider['CONVERSATION_API_KEY'] not in raw
            (root/name).write_text(raw)
            call={'case':active['case'],'request':name};evidence['calls'].append(call)
            start=time.monotonic();save()
            try:
                result=original(instance,*a,**kw)
                call['status']='success'
                if getattr(result,'choices',None):
                    call['response']=result.model_dump()
                    assert not getattr(result.choices[0].message,'reasoning_content',None)
                return result
            except Exception as exc:
                call.update(status='error',error_type=type(exc).__name__);raise
            finally:call['seconds']=round(time.monotonic()-start,3);save()
        evidence['status']='RUNNING';save()
        with patch.object(Completions,'create',observed),(root/'runtime.log').open('w') as log,redirect_stdout(log),redirect_stderr(log):
            for case,question in questions:
                active['case']=case;before=len(evidence['calls']);started=time.monotonic()
                response=client.post(f'/api/investigation-workspaces/{sid}/turns',json={
                    'client_message_id':uuid4().hex,'content':question})
                assert response.status_code==202,response.text
                tid=response.json()['turn_id'];deadline=time.monotonic()+300
                while s['conversation'].store.get_turn(tid).status=='running':
                    if time.monotonic()>deadline:raise TimeoutError('Conversation turn exceeded 300 seconds')
                    time.sleep(.1)
                turn=s['conversation'].store.get_turn(tid)
                result=s['conversation'].store.turn_result(tid)
                published=None
                if case=='confirm_second':
                    outcome=worker(s).run_once()
                    published={'status':outcome.status.value,'error':outcome.error_code,'run_id':outcome.id} if outcome else None
                state=client.get(f'/api/investigation-workspaces/{sid}/state')
                assert state.status_code==200,state.text
                with s['creation_store']._connect() as db:
                    receipts=[dict(r) for r in db.execute('''SELECT r.tool_name,r.status,r.response_json
                        FROM investigation_creation_tool_receipts r JOIN ruleset_proposal_conversation_bindings b
                        ON r.receipt_id=b.receipt_id WHERE b.application_turn_id=? ORDER BY r.rowid''',(tid,))]
                    counts={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0]
                        for table in ('investigation_drafts','investigation_runs')}
                row={'case':case,'question':question,'status':turn.status,'answer':result.answer,
                    'seconds':round(time.monotonic()-started,3),'model_calls':len(evidence['calls'])-before,
                    'counts':counts,'rounds':state.json()['rounds'],'artifact':turn.public_artifact, 'current_draft_id':state.json().get('draft_artifact',{}).get('draft_id'),
                    'creation_receipts':receipts,'local_worker':published}
                with collection.connect() as db:
                    row['report_calls']=[dict(r) for r in db.execute('SELECT name,arguments,response FROM collection_calls WHERE turn=? ORDER BY id',(tid,))]
                evidence['turns'].append(row)
                assert s['creation_store'].get_run(initial['run_id'],principal='principal-a').confirmed_configuration==original_first
                save()
                sys.__stdout__.write(json.dumps({k:row[k] for k in ('case','status','seconds','model_calls','counts')})+'\n');sys.__stdout__.flush()
                if turn.status!='completed':raise AssertionError('Conversation did not complete: '+case)
                expected_drafts = 1 if case=='read_first' else (3 if case in {'copy_third','read_old'} else 2)
                if counts['investigation_drafts'] != expected_drafts:
                    raise AssertionError('Unexpected round count after '+case)
                if case in {'copy_second','edit_second'} and counts['investigation_runs'] != 1:
                    raise AssertionError('Investigation started before confirmation')
                if case=='edit_second' and state.json()['draft_artifact']['confirmation_preview']['resolved_search_terms']!=['排队投诉','服务投诉']:
                    raise AssertionError('Requested keywords were not applied')
                if case=='read_old' and row['current_draft_id']!=row['rounds'][-1]['draft_id']:
                    raise AssertionError('Reading an old draft replaced the current round')
                if case in {'copy_third','read_old'} and counts['investigation_runs']!=2:
                    raise AssertionError('Third investigation started without confirmation')
                if case=='confirm_second' and (not published or published['status']!='PUBLISHED' or counts['investigation_runs']!=2):
                    raise AssertionError('Second investigation was not published')
        evidence['status']='COMPLETE_PENDING_REVIEW';save()
    except Exception as exc:
        evidence.update(status='ERROR',error_type=type(exc).__name__);save();raise
    finally:
        try:next(fixture)
        except StopIteration:pass


if __name__=='__main__':main()
