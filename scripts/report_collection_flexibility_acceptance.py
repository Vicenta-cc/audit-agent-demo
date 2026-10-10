"""Opt-in real conversation-model acceptance through the existing HTTP turn API.

Supply CONVERSATION_* in the process environment. Only synthetic frozen reports
are sent. Uses real local cookie/CSRF authentication with synthetic users; deployed browser acceptance is excluded.
No worker, crawler, cloud deployment or existing datastore is touched.
"""
import argparse
from contextlib import redirect_stdout,redirect_stderr
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
from unittest.mock import patch
from uuid import uuid4


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--single-report',action='store_true',help='Compare existing single-report service and a B-only collection; three turns each.')
    parser.add_argument('--mode',choices=['constrained','agent','paired'],default='constrained')
    args=parser.parse_args()
    if args.single_report and args.mode=='paired':
        parser.error('--single-report requires --mode constrained or --mode agent')
    root=args.output.resolve()
    root.mkdir(parents=True,exist_ok=False)
    os.umask(0o077)
    provider={k:os.environ[k] for k in ['CONVERSATION_API_KEY','CONVERSATION_BASE_URL','CONVERSATION_MODEL']}
    inherited={k:os.environ[k] for k in ['HOME','PATH','LANG','TMPDIR','PYTHONPATH'] if k in os.environ}
    os.environ.clear();os.environ.update(inherited);os.environ.update(provider)
    os.environ.update(PYTHON_DOTENV_DISABLED='1',APP_AUTH_MODE='disabled',PYTHONDONTWRITEBYTECODE='1')
    for key,name in {'XHS_AUDIT_DATA_DIR':'data','XHS_AUDIT_OUTPUTS_DIR':'outputs','APP_AUTH_DB':'auth.sqlite3',
        'HERMES_HOME':'hermes','CRAWLER_AUTH_KEY_FILE':'data/crawler_auth.key','MEDIACRAWLER_DIR':'disabled-crawler',
        'MEDIACRAWLER_REQUEST_SCHEDULER_DB':'scheduler.sqlite3'}.items():os.environ[key]=str(root/name)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
    from test_investigation_creation_conversation import creation_stack
    from test_report_collection import collection
    from openai.resources.chat.completions import Completions
    from backend.audit_agent.config import settings
    evidence={'suite':'scope-flexibility', 'status':'RUNNING','model':settings.conversation_model,
        'host':urlsplit(settings.conversation_base_url).hostname,'turns':[],'calls':[],
        'limitations':['Synthetic report data and isolated local users; real cookie/CSRF authentication',
            'In-process FastAPI HTTP transport; no browser or deployment test',
            'No multi-round creation, crawler or front-end changes']}
    def save():
        content=json.dumps(evidence,ensure_ascii=False,indent=2,default=str).replace(provider['CONVERSATION_API_KEY'],'[REDACTED]')
        (root/'evidence.json').write_text(content)
    original=Completions.create
    def observed(client,*a,**kw):
        if len(evidence['calls'])>=120:raise RuntimeError('验收最多 120 次模型请求。')
        evidence['calls'].append({'model':kw.get('model'),'host':urlsplit(str(client._client.base_url)).hostname,
            'tool_names':[t['function']['name'] for t in kw.get('tools',[])]})
        save()
        call=evidence['calls'][-1]
        started=time.monotonic()
        try:
            response=original(client,*a,**kw)
            call['outcome']='success'
            return response
        except Exception as exc:
            call['outcome']=type(exc).__name__
            raise
        finally:
            call['seconds']=round(time.monotonic()-started,3)
            save()
    save()
    fixture=creation_stack.__wrapped__(root)
    stack=next(fixture)
    try:
        collection.__wrapped__(stack,root)
        stack['conversation'].fake_runtime=False
        stack['collection'].agent_driven=args.mode=='agent'
        from backend.report_collection.tools import PROMPT,AGENT_PROMPT,schemas
        evidence['mode']=args.mode
        evidence['system_prompt']=AGENT_PROMPT if args.mode=='agent' else PROMPT
        evidence['system_prompt_chars']=len(evidence['system_prompt'])
        evidence['tool_descriptions_chars']=sum(len(s['description']) for s in schemas())
        validate=stack['collection'].validate_answer
        def observed_validation(session,turn,answer):
            try:return validate(session,turn,answer)
            except ValueError as exc:
                evidence.setdefault('rejected_answers',[]).append({'turn':turn,'answer':answer,'error':str(exc)})
                save()
                raise
        stack['collection'].validate_answer=observed_validation
        from report_collection_fixtures import authenticated_clients
        client,other_client,sid,auth=authenticated_clients(stack,root)
        assert other_client.get(f'/api/investigation-workspaces/{sid}/report-sources').status_code==404
        assert client.post(f'/api/investigation-workspaces/{sid}/report-sources',json={'reports':[{'label':'D','report_version_id':stack['reports']['D'].version}]}).status_code==404
        evidence['auth_checks']={'owner_read':True,'other_admin_denied':True,'other_report_denied':True}
        if args.single_report:
            from backend.hermes_runtime.service import HermesInvestigationAgentService
            from backend.investigation.report_query import ReportQueryFacade
            from backend.investigation.store import InvestigationStore
            legacy=HermesInvestigationAgentService(report_facade=ReportQueryFacade(stack['resource_db']),
                store=InvestigationStore(root/'legacy-turns.sqlite3'),hermes_state_dir=root/'legacy-hermes',
                authorized_report_version_ids=())
            legacy_sid=legacy.create_session(stack['reports']['B'].version).id
            single_sid=client.post('/api/investigation-workspaces',json={}).json()['workspace_session_id']
            registered=client.post(f'/api/investigation-workspaces/{single_sid}/report-sources',json={'reports':[
                {'label':'B','report_version_id':stack['reports']['B'].version}]})
            assert registered.status_code==200,registered.text
            evidence['suite']='single-report-comparison'
            evidence['limitations'].append('Legacy probe calls the production service directly; collection probe uses authenticated HTTP. Tiny sequential sample, not a performance benchmark.')
            with patch.object(Completions,'create',observed), (root/'runtime.log').open('w') as log, redirect_stdout(log),redirect_stderr(log):
                try:
                    for mode in ['legacy','collection']:
                        for question in ['这份报告有哪些高风险评论？请列出全部评论内容和来源。','这些评论分别是谁发的？','换个问题：这份报告有多少个帖子？']:
                            started=time.monotonic();call_start=len(evidence['calls'])
                            if mode=='legacy':
                                turn,_=legacy.accept_message(legacy_sid,client_message_id=uuid4().hex,content=question)
                                result=legacy.execute_turn(turn.id)
                                status=legacy.store.get_turn(turn.id).status
                            else:
                                response=client.post(f'/api/investigation-workspaces/{single_sid}/turns',json={'client_message_id':uuid4().hex,'content':question})
                                assert response.status_code==202,response.text
                                turn_id=response.json()['turn_id'];deadline=time.monotonic()+240
                                while stack['conversation'].store.get_turn(turn_id).status=='running':
                                    if time.monotonic()>deadline:raise TimeoutError('single-report turn timeout')
                                    time.sleep(.1)
                                result=stack['conversation'].store.turn_result(turn_id)
                                status=stack['conversation'].store.get_turn(turn_id).status
                            record={'mode':mode,'question':question,'status':status,'seconds':round(time.monotonic()-started,3),
                                'model_calls':len(evidence['calls'])-call_start,'result':result.model_dump(mode='json')}
                            evidence['turns'].append(record);save()
                finally:
                    legacy.close()
            evidence['status']='COMPLETED_NEEDS_ANSWER_REVIEW';save()
            print(json.dumps({'status':evidence['status'],'calls':len(evidence['calls'])}))
            return
        questions=[
            ('b_comments','只看报告 B 的全部评论，逐条列出评论内容、账号引用和来源。', 'comments', ['B'],2),
            ('a_reset','现在改为只看报告 A 的全部评论，不要沿用上一轮评论集合。', 'comments',['A'],3),
            ('b_reset','再只看报告 B 的全部评论。', 'comments',['B'],2),
            ('compare','那和 A 比较一下，两份报告共同出现过哪些账号？', 'accounts',['A','B'],2),
            ('b_subset','只看 B 中刚才那个共同评论账号的评论。', 'comments',['B'],1),
            ('followup','刚才那条评论是谁发的？', 'accounts',['B'],1),
            ('b_all_accounts','那 B 中所有账号呢？包括发帖者与评论者，不限于刚才那条评论。', 'accounts',['B'],3),
            ('topic_reset','换个问题：当前会话有哪些高风险评论？请检索全部报告。', 'comments',['A','B'],4),
            ('config','A 和 B 实际使用的关键词、审核规则、豁免及版本有哪些差异？', 'config_diff',['A','B'],None),
            ('normal_exclusion','只查询 B 的全部评论。回答开头请解释“本次仅分析 B，未纳入此前的 A”，然后列出 B 的评论。', 'comments',['B'],2),
            ('metadata_exclusion',f"只查询 B 的全部评论。开头注明：本次仅分析 B，未纳入报告 A（{stack['reports']['A'].version}）。这个 A 的编号只是范围说明，请保留它，不要引用 A 的评论。然后列出 B 的评论。", 'comments',['B'],2),
            ('read_then_compare','先读回刚才 B 的评论结果，确认那个共同评论账号，再查它在 A 的评论，比较它在 A 和 B 的风险判断。这个问题明确允许查询 A+B。', 'comments',['A'],1),
        ]
        with patch.object(Completions,'create',observed), (root/'runtime.log').open('w') as log, redirect_stdout(log),redirect_stderr(log):
            for mode in (['constrained','agent'] if args.mode=='paired' else [args.mode]):
                stack['collection'].agent_driven=mode=='agent'
                evidence.setdefault('configurations',{})[mode]={
                    'system_prompt':AGENT_PROMPT if mode=='agent' else PROMPT,
                    'system_prompt_chars':len(AGENT_PROMPT if mode=='agent' else PROMPT),
                    'tool_descriptions_chars':sum(len(s['description']) for s in schemas())}
                if args.mode=='paired':
                    sid=client.post('/api/investigation-workspaces',json={}).json()['workspace_session_id']
                    registered=client.post(f'/api/investigation-workspaces/{sid}/report-sources',json={'reports':[
                        {'label':n,'report_version_id':stack['reports'][n].version} for n in ['A','B']]})
                    assert registered.status_code==200,registered.text
                for case,question,kind,labels,count in questions:
                    started=time.monotonic();call_start=len(evidence['calls'])
                    response=client.post(f'/api/investigation-workspaces/{sid}/turns',json={'client_message_id':uuid4().hex,'content':question})
                    assert response.status_code==202,response.text
                    turn_id=response.json()['turn_id']
                    deadline=time.monotonic()+240
                    while stack['conversation'].store.get_turn(turn_id).status=='running':
                        if time.monotonic()>deadline:raise TimeoutError('模型验收超时')
                        time.sleep(.1)
                    turn=stack['conversation'].store.get_turn(turn_id)
                    result=stack['conversation'].store.turn_result(turn_id)
                    record={'mode':mode,'turn_id':turn_id,'case':case,'seconds':round(time.monotonic()-started,3),'model_calls':len(evidence['calls'])-call_start,'question':question,'status':turn.status,'result':result.model_dump(mode='json')}
                    with stack['collection'].connect() as db:
                        record['business_calls']=[dict(r) for r in db.execute('SELECT name,arguments,response FROM collection_calls WHERE turn=? ORDER BY id',(turn_id,))]
                        record['query_results']=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM collection_results WHERE turn=?',(turn_id,))]
                    candidates=[r for r in record['query_results'] if r['kind']==kind and {s['label'] for s in r['sources']}==set(labels)]
                    record['checks']={'completed':turn.status=='completed','expected_query':bool(candidates) and (count is None or len(candidates[-1]['items'])==count)}
                    if case=='followup':
                        from backend.report_collection.service import reference
                        # Fixture's target identity, independent of the model's query plan.
                        record['checks'].pop('expected_query')
                        record['checks']['correct_account']=reference('account',['douyin','same-user']) in record['result']['answer']
                    if case=='read_then_compare':
                        record['checks']['expected_query']=any(r['kind']=='comments' and 'A' in {s['label'] for s in r['sources']} and r['items'] for r in record['query_results'])
                    if set(labels)=={'B'}:
                        import re
                        earlier_a_refs={i['comment_ref'] for t in evidence['turns'] for r in t['query_results'] if r['kind']=='comments'
                            for i in r['items'] if i['source']['label']=='A'}
                        cited=set(re.findall(r'comment_[0-9a-f]{24}',record['result']['answer']))
                        record['checks']['no_A_evidence']=not (cited & earlier_a_refs)
                    if case=='metadata_exclusion':
                        record['checks']['requested_scope_note_retained']=stack['reports']['A'].version in record['result']['answer']
                    record['repair_count']=sum(c['name']=='validate_answer' for c in record['business_calls'])
                    record['rejected_tools']=[c for c in record['business_calls'] if c['name']!='validate_answer' and not json.loads(c['response']).get('ok')]
                    record['trace_checks']={'no_rejected_tool':not record['rejected_tools']}
                    if 'expected_query' in record['checks']:
                        record['trace_checks']['expected_query']=record['checks'].pop('expected_query')
                    evidence['turns'].append(record);save()
        assert evidence['calls'] and all(c['model']==settings.conversation_model for c in evidence['calls'])
        evidence['status']='CHECKS_PASS_NEEDS_ANSWER_REVIEW' if all(all(t['checks'].values()) for t in evidence['turns']) else 'FAIL_REVIEW'
        save()
    except Exception as exc:
        evidence['status']='FAIL';evidence['error']=str(exc);save()
        raise
    finally:
        if 'client' in locals():client.close()
        if 'other_client' in locals():other_client.close()
        try:next(fixture)
        except StopIteration:pass
    print(json.dumps({'status':evidence['status'],'model':evidence['model'],'calls':len(evidence['calls']),'output':str(root)},ensure_ascii=False))

if __name__=='__main__':main()
