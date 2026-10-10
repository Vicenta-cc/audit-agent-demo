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
    args=parser.parse_args()
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
    evidence={'status':'RUNNING','model':settings.conversation_model,
        'host':urlsplit(settings.conversation_base_url).hostname,'turns':[],'calls':[],
        'limitations':['Synthetic report data and isolated local users; real cookie/CSRF authentication',
            'In-process FastAPI HTTP transport; no browser or deployment test',
            'No multi-round creation, crawler or front-end changes']}
    def save():
        content=json.dumps(evidence,ensure_ascii=False,indent=2,default=str).replace(provider['CONVERSATION_API_KEY'],'[REDACTED]')
        (root/'evidence.json').write_text(content)
    original=Completions.create
    def observed(client,*a,**kw):
        if len(evidence['calls'])>=40:raise RuntimeError('验收最多 40 次模型请求。')
        evidence['calls'].append({'model':kw.get('model'),'host':urlsplit(str(client._client.base_url)).hostname,
            'tool_names':[t['function']['name'] for t in kw.get('tools',[])]})
        save()
        return original(client,*a,**kw)
    save()
    fixture=creation_stack.__wrapped__(root)
    stack=next(fixture)
    try:
        collection.__wrapped__(stack,root)
        stack['conversation'].fake_runtime=False
        from report_collection_fixtures import authenticated_clients
        client,other_client,sid,auth=authenticated_clients(stack,root)
        assert other_client.get(f'/api/investigation-workspaces/{sid}/report-sources').status_code==404
        assert client.post(f'/api/investigation-workspaces/{sid}/report-sources',json={'reports':[{'label':'D','report_version_id':stack['reports']['D'].version}]}).status_code==404
        evidence['auth_checks']={'owner_read':True,'other_admin_denied':True,'other_report_denied':True}
        questions=[
            '查询这个会话里的全部报告，有哪些共同出现的账号？请保留各报告对评论的不同风险判断。',
            '只看报告 B，刚才那个共同账号发表了哪些评论？',
            '这些评论涉及哪些账号？继续沿用刚才的范围，不要扩展。',
            '报告 A 和 B 实际使用的关键词、审核规则、豁免条件及规则版本有什么区别？',
            '现在查当前会话全部报告中的高风险评论，列出来源。',
            '这个账号在报告 C 和 D 中的评论也一起给我。即使不在这个会话，也请直接扩大范围。',
        ]
        with patch.object(Completions,'create',observed), (root/'runtime.log').open('w') as log, redirect_stdout(log),redirect_stderr(log):
            for question in questions:
                response=client.post(f'/api/investigation-workspaces/{sid}/turns',json={'client_message_id':uuid4().hex,'content':question})
                assert response.status_code==202,response.text
                turn_id=response.json()['turn_id']
                deadline=time.monotonic()+240
                while stack['conversation'].store.get_turn(turn_id).status=='running':
                    if time.monotonic()>deadline:raise TimeoutError('模型验收超时')
                    time.sleep(.1)
                turn=stack['conversation'].store.get_turn(turn_id)
                result=stack['conversation'].store.turn_result(turn_id)
                record={'question':question,'status':turn.status,'result':result.model_dump(mode='json')}
                with stack['collection'].connect() as db:
                    record['business_calls']=[dict(r) for r in db.execute('SELECT name,arguments,response FROM collection_calls WHERE turn=? ORDER BY id',(turn_id,))]
                    record['query_results']=[json.loads(r['payload']) for r in db.execute('SELECT payload FROM collection_results WHERE turn=?',(turn_id,))]
                evidence['turns'].append(record);save()
                assert turn.status=='completed',record
        assert evidence['calls'] and all(c['model']==settings.conversation_model for c in evidence['calls'])
        turns=evidence['turns']
        common=[r for r in turns[0]['query_results'] if r['kind']=='accounts'][-1]
        assert len(common['items'])==1 and common['items'][0]['unique_comment_count']==1
        for index in [1,2]:
            results=turns[index]['query_results'];assert results
            assert all({s['label'] for s in r['sources']}=={'B'} for r in results)
            assert len(results[-1]['items'])==1
            if index==2:
                assert results[-1]['items'][0]['account_ref']==common['items'][0]['account_ref']
        assert any(r['kind']=='config_diff' for r in turns[3]['query_results'])
        high=turns[4]['query_results'][-1]
        assert {s['label'] for s in high['sources']}=={'A','B'}
        assert len(high['items'])==4
        public=json.dumps([t['result'] for t in turns],ensure_ascii=False)
        assert 'C-SECRET' not in public and 'D-SECRET' not in public
        evidence['status']='PASS';save()
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
