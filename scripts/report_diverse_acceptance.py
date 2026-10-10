"""Isolated full-chain six-arm acceptance on new synthetic frozen reports.

Run one dataset/repeat per process: service tool globals must not be shared across
concurrent jobs. All arms in one job reuse the same frozen report versions. Only
model-bound tool-result representation and the short prompt vary between arms.
"""
import argparse
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

from scripts.report_diverse_cases import cases

ARMS=['original','data','semantic','brief','data_semantic','data_brief']


def publish(path, name, spec, db):
    from backend.audit_agent.audit_policy_store import TaskAuditConfigRevisionStore
    from backend.audit_agent.ingestion import AuditResultStore, IngestionStore
    from backend.audit_agent.job_store import JobStore
    from backend.reporting.integration_source import CanonicalReportSource
    from backend.reporting.runtime import R31ReportRuntime
    from backend.reporting.store import ReportStore
    path.mkdir()
    task='diverse-'+name
    JobStore(db).create(job_id=task,platform='dy',status='completed',display_name=name,
        crawl_mode='search',max_notes=len(spec['posts']),analyze_limit=len(spec['posts']))
    rules=dict(rules=[dict(id='r1',condition=spec['condition'])],exemptions=spec['exemptions'],
        ruleset_ref=dict(id='rules-'+spec['version'],version=spec['version']))
    configs=TaskAuditConfigRevisionStore(db)
    rev=configs.create(job_id=task,audit_config=dict(capabilities=['text'],rule_snapshot=rules))
    ingest=IngestionStore(db)
    for idx,p in enumerate(spec['posts']):
        batch=path/f'batch-{idx}.json'
        batch.write_text(json.dumps(dict(task_id=task,platform='dy',items=[dict(aweme_id=p['id'],
            title=p['title'],desc='合成报告对照数据',create_time=1788825600)]),ensure_ascii=False))
        ref=ingest.ingest_batch(batch,path/'raw')[0]
        audit=AuditResultStore(db).upsert_result(job_id=task,platform='dy',content_key=p['id'],
            content_id=ref['content_id'],audit_config_revision_id=rev['id'],result=dict(
            title=p['title'],desc='合成报告对照数据',url='https://www.douyin.com/video/'+p['id'],
            author=dict(nickname=p['name'],sec_uid=p['uid']),decision='pass' if p['risk']=='none' else 'review',risk_level=p['risk'],
            summary='独立的冻结研判记录；此样例不含逐条命中原因',comments=p['comments'],evidence_items=[],video_results=[]))
        ingest.mark_content_status('dy',p['id'],'completed',task_id=task,audit_result_id=audit['id'])
    class Source(CanonicalReportSource):
        def coverage(self,task_id):return {'fixture':True}
        def config(self,task_id):
            data=super().config(task_id)
            data.update(resolved_search_terms=spec['terms'],ruleset_revision=dict(id='rules-'+spec['version'],version=spec['version']))
            return data
    store=ReportStore(db)
    generated=R31ReportRuntime(store).generate(task,source=Source(db,path/'outputs'),checkpoint_path=path/'checkpoints.sqlite3')
    return SimpleNamespace(version=generated.report_version_id,task=task,configs=configs,store=store)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dataset',choices=list(cases()),required=True)
    parser.add_argument('--repeat',type=int,choices=[1,2],required=True)
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    root=args.output.resolve();os.umask(0o077);root.mkdir(parents=True,exist_ok=False)
    provider={k:os.environ[k] for k in ['CONVERSATION_API_KEY','CONVERSATION_BASE_URL','CONVERSATION_MODEL']}
    inherited={k:os.environ[k] for k in ['HOME','PATH','LANG','TMPDIR','PYTHONPATH'] if k in os.environ}
    os.environ.clear();os.environ.update(inherited);os.environ.update(provider)
    os.environ.update(PYTHON_DOTENV_DISABLED='1',APP_AUTH_MODE='disabled',PYTHONDONTWRITEBYTECODE='1')
    for key,name in {'XHS_AUDIT_DATA_DIR':'data','XHS_AUDIT_OUTPUTS_DIR':'outputs','APP_AUTH_DB':'auth.sqlite3',
        'HERMES_HOME':'hermes','CRAWLER_AUTH_KEY_FILE':'data/crawler_auth.key','MEDIACRAWLER_DIR':'disabled-crawler',
        'MEDIACRAWLER_REQUEST_SCHEDULER_DB':'scheduler.sqlite3'}.items():os.environ[key]=str(root/name)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
    from test_investigation_creation_conversation import creation_stack
    from report_collection_fixtures import authenticated_clients
    from backend.report_collection.service import ReportCollectionService
    from backend.report_collection.access import owns_report
    from backend.report_collection import tools
    from backend.investigation_creation.principal import Principal
    from scripts.report_original_scene import expression_only
    from scripts.report_expression_trial import PROMPT as SEMANTIC_PROMPT
    from openai.resources.chat.completions import Completions
    spec=cases()[args.dataset]
    # Balanced rotating order, reversed on repeat 2. No arm depends on another.
    shift=list(cases()).index(args.dataset)*2
    order=ARMS[shift:]+ARMS[:shift]
    if args.repeat==2:order=list(reversed(order))
    evidence=dict(suite='diverse-full-chain',dataset=args.dataset,repeat=args.repeat,status='PREPARING',
        model=provider['CONVERSATION_MODEL'],host=urlsplit(provider['CONVERSATION_BASE_URL']).hostname,
        arm_order=order,prompts=dict(original=tools.AGENT_BASE_PROMPT,semantic=SEMANTIC_PROMPT,brief=tools.FOCUSED_ANSWER_INSTRUCTION),
        fixture=spec,turns=[],calls=[],thinking='disabled',production_changed=False,
        rubric='Full answer, all requested subquestions, evidence and attribution; inspect side claims, tool coverage, and prior-turn references. Provider/transport failures are incomplete, not fact passes. No answer repair.')
    def save():
        data=json.dumps(evidence,ensure_ascii=False,indent=2,default=str)
        assert provider['CONVERSATION_API_KEY'] not in data
        temp=root/'evidence.tmp';temp.write_text(data);temp.replace(root/'evidence.json')
    save();fixture=creation_stack.__wrapped__(root);stack=next(fixture)
    try:
        reports={n:publish(root/n,n,s,stack['resource_db']) for n,s in spec['reports'].items()}
        with stack['creation_store'].admission.connect() as db:
            for name,r in reports.items():
                db.execute('''INSERT INTO task_admissions
                    (id,owner_id,task_id,job_id,kind,day,state,request_key,fingerprint,payload_json,created_at,updated_at)
                    VALUES(?,?,?,?,?,'2026-10-10','SUCCEEDED',?,?,'{}','2026-10-10','2026-10-10')''',
                    (name,'principal-b' if name=='D' else 'principal-a',r.task,r.task,'fixture',name,name))
        service=ReportCollectionService(conversation=stack['conversation'],report_store=stack['report_store'],
            can_read=lambda p,item:owns_report(stack['creation_store'],p,item),path=root/'collections.sqlite3',agent_driven=True,
            express_results=False)  # Historical six arms apply formatting at the captured request boundary.
        stack['conversation'].report_collection=service;tools.configure(service)
        stack.update(collection=service,reports=reports)
        stack['conversation'].fake_runtime=False
        client,other,unused_sid,auth=authenticated_clients(stack,root)
        evidence['reports']={n:dict(version=r.version,snapshot_hash=r.store.get_source_snapshot(r.version)['snapshot_hash']) for n,r in reports.items()}
        # A post-publication edit must not leak into comparison of frozen reports.
        if args.dataset=='config':
            reports['B'].configs.create(job_id=reports['B'].task,audit_config={'rule_snapshot':{
                'rules':[{'id':'live-only','condition':'LIVE-V9-MUST-NOT-REPLACE-FROZEN'}],
                'ruleset_ref':{'id':'rules-v9','version':'v9'}}})
        def new_session():
            sid=client.post('/api/investigation-workspaces',json={}).json()['workspace_session_id']
            response=client.post(f'/api/investigation-workspaces/{sid}/report-sources',json={'reports':[
                {'label':n,'report_version_id':reports[n].version} for n in spec['visible']]})
            assert response.status_code==200,response.text
            assert other.get(f'/api/investigation-workspaces/{sid}/report-sources').status_code==404
            assert client.post(f'/api/investigation-workspaces/{sid}/report-sources',json={'reports':[
                {'label':'D','report_version_id':reports['D'].version}]}).status_code==404
            return sid
        check_sid=new_session();principal=stack['conversation'].principal_for_session(check_sid)
        sources=service.members(check_sid,principal)
        truth=service.observations(sources)
        evidence['truth']=dict(observations=truth,configs={s['label']:service.repository(s)[1] for s in sources})
        for name in spec['visible']:
            actual=[r for r in truth if r['source']['label']==name]
            expected=spec['reports'][name]['posts']
            assert sum(r['kind']=='post_author' for r in actual)==len(expected)
            assert sum(r['kind']=='comment_author' for r in actual)==sum(len(p['comments']) for p in expected)
        assert all(r['account_ref'] for r in truth if r['kind']=='post_author')
        evidence['auth_checks']={'other_owner_denied':True,'foreign_report_registration_denied':True}
        if args.prepare_only:
            evidence['status']='PREPARED_NO_MODEL_CALLS';save();print(evidence['status']);return
        original=Completions.create
        active={}
        def observed(instance,*a,**kw):
            if len(evidence['calls'])>=180:raise RuntimeError('Per-job call safety limit reached')
            assert kw.get('model')==evidence['model']
            assert kw.get('extra_body',{}).get('thinking')=={'type':'disabled'}
            before=deepcopy(kw)
            if active['arm'].startswith('data'):
                kw=expression_only(kw)
                assert kw['tools']==before['tools']
                assert [m for m in kw['messages'] if m['role'] in ('user','assistant','system')]==[
                    m for m in before['messages'] if m['role'] in ('user','assistant','system')]
            number=len(evidence['calls'])+1
            request_name=f'request-{number:03d}.json'
            raw=json.dumps(kw,ensure_ascii=False,default=str)
            assert provider['CONVERSATION_API_KEY'] not in raw
            (root/request_name).write_text(raw)
            call=dict(number=number,arm=active['arm'],case=active['case'],request_file=request_name,
                input_chars=len(json.dumps(kw['messages'],ensure_ascii=False)))
            evidence['calls'].append(call);save();start=time.monotonic()
            try:
                response=original(instance,*a,**kw)
                call['status']='success'
                if getattr(response,'usage',None):call['usage']=response.usage.model_dump()
                if getattr(response,'choices',None):
                    call['response']=response.model_dump()
                    assert not getattr(response.choices[0].message,'reasoning_content',None)
                return response
            except Exception as exc:
                call.update(status='error',error_type=type(exc).__name__);raise
            finally:call['seconds']=round(time.monotonic()-start,3);save()
        evidence['status']='RUNNING';save()
        with patch.object(Completions,'create',observed), (root/'runtime.log').open('w') as log,redirect_stdout(log),redirect_stderr(log):
            for arm in order:
                prompt=tools.AGENT_BASE_PROMPT
                if 'semantic' in arm:prompt+='\n'+SEMANTIC_PROMPT
                elif 'brief' in arm:prompt+='\n'+tools.FOCUSED_ANSWER_INSTRUCTION
                sid=new_session()
                with patch.object(tools,'AGENT_PROMPT',prompt):
                    for case,question,rubric in spec['questions']:
                        active.update(arm=arm,case=case)
                        start=time.monotonic();call_start=len(evidence['calls'])
                        response=client.post(f'/api/investigation-workspaces/{sid}/turns',json={'client_message_id':uuid4().hex,'content':question})
                        assert response.status_code==202,response.text
                        tid=response.json()['turn_id'];deadline=time.monotonic()+240
                        while stack['conversation'].store.get_turn(tid).status=='running':
                            if time.monotonic()>deadline:raise TimeoutError('Turn exceeded 240 seconds')
                            time.sleep(.1)
                        result=stack['conversation'].store.turn_result(tid)
                        row=dict(arm=arm,case=case,question=question,rubric=rubric,session=sid,turn=tid,
                            status=stack['conversation'].store.get_turn(tid).status,result=result.model_dump(mode='json'),
                            seconds=round(time.monotonic()-start,3),call_start=call_start,model_calls=len(evidence['calls'])-call_start)
                        with service.connect() as db:
                            row['business_calls']=[dict(r) for r in db.execute('SELECT name,arguments,response FROM collection_calls WHERE turn=? ORDER BY id',(tid,))]
                            row['query_results']=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM collection_results WHERE turn=?',(tid,))]
                        evidence['turns'].append(row);save()
                        sys.__stdout__.write(json.dumps({k:row[k] for k in ['arm','case','status','seconds','model_calls']})+'\n');sys.__stdout__.flush()
        evidence['status']='COMPLETE_PENDING_WHOLE_ANSWER_REVIEW';save()
    except Exception as exc:
        evidence.update(status='ERROR',error_type=type(exc).__name__);save();raise
    finally:
        if 'client' in locals():client.close();other.close()
        try:next(fixture)
        except StopIteration:pass


if __name__=='__main__':main()
