"""Opt-in, bounded real Hermes/Qwen resource acceptance in disposable storage."""
from contextlib import redirect_stdout, redirect_stderr
import argparse
import getpass
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'scripts')]
from resource_experiment import environment


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--token-budget',type=int,default=80000)
    parser.add_argument('--max-calls',type=int,default=16)
    parser.add_argument('--output',default='qwen-resource-acceptance.json')
    parser.add_argument('--generation-only',action='store_true')
    parser.add_argument('--existing-task-only',action='store_true')
    parser.add_argument('--flow-matrix',action='store_true')
    parser.add_argument('--scenario',action='append',default=[])
    args=parser.parse_args()
    key=getpass.getpass('Experiment API key (hidden): ').replace('\\_','_').strip()
    evidence={'model':'qwen3.7-plus','runtime':'Hermes 0.20.4','thinking':True,'storage':'disposable','cases':[],'provider_calls':0,'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}}
    target=ROOT/'docs/evidence/m3-resource-lifecycle'/args.output
    def persist():target.write_text(json.dumps(evidence,ensure_ascii=False,indent=2).replace(key,'[redacted]')+'\n')
    with tempfile.TemporaryDirectory(prefix='m3-resource-real-qwen-') as directory:
        temp=Path(directory)
        isolated=environment(temp);os.environ.clear();os.environ.update(isolated)
        from backend.audit_agent.config import settings
        settings.dashscope_api_key=key;settings.dashscope_base_url=args.base_url.rstrip('/')
        from test_investigation_creation_conversation import creation_stack
        from backend.investigation_creation.principal import Principal
        from backend.investigation_creation.tools import configure_hermes_investigation_creation_tools
        from openai.resources.chat.completions import Completions
        original_create=Completions.create
        def completion(client,*a,**kwargs):
            if evidence['provider_calls']>=args.max_calls or evidence['usage']['total_tokens']>=args.token_budget:
                raise RuntimeError('Experiment token/call budget reached')
            assert kwargs.get('model')=='qwen3.7-plus'
            # Only this test process adds explicit thinking and a response limit.
            kwargs['extra_body']={**kwargs.get('extra_body',{}),'enable_thinking':True}
            kwargs['max_tokens']=6000
            if kwargs.get('stream'):kwargs['stream_options']={'include_usage':True}
            evidence['provider_calls']+=1
            result=original_create(client,*a,**kwargs)
            def usage(value):
                if value:
                    for name in evidence['usage']:evidence['usage'][name]+=int(getattr(value,name,0) or 0)
                    persist()
            if not kwargs.get('stream'):
                usage(result.usage);return result
            def chunks():
                try:
                    for chunk in result:
                        usage(chunk.usage)
                        yield chunk
                finally:result.close()
            return chunks()
        fixture=creation_stack.__wrapped__(temp);stack=next(fixture)
        conversation=stack['conversation'];conversation.fake_runtime=False
        principal=Principal('principal-a');manager=stack['app_service'].resource_management
        configure_hermes_investigation_creation_tools(stack['tool_service'],principal_provider=conversation.principal_for_session)
        session=conversation.create_session(principal=principal,workspace_key='resource-qwen-acceptance')
        def start_session(label):
            nonlocal session
            session=conversation.create_session(principal=principal,workspace_key='resource-flow-'+label)
            return session
        def counts():
            with stack['creation_store']._connect() as conn:
                return {t:conn.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('investigation_drafts','investigation_runs')}
        def turn(label,message,*,run_delta=0):
            before=counts()
            started=time.perf_counter()
            calls_before=evidence['provider_calls']
            usage_before=dict(evidence['usage'])
            tool_times=[]
            original_execute=stack['tool_service'].execute
            def measured_execute(*a,**kw):
                tick=time.perf_counter()
                try:
                    return original_execute(*a,**kw)
                finally:
                    tool_times.append({'tool':a[0], 'seconds':round(time.perf_counter()-tick,4)})
            accepted,_=conversation.accept_message(session.id,client_message_id=label,content=message,principal=principal)
            log=io.StringIO()
            with patch.object(stack['tool_service'],'execute',side_effect=measured_execute) as dispatch,redirect_stdout(log),redirect_stderr(log):
                result=conversation.execute_turn(accepted.id)
            saved=conversation.store.get_turn(accepted.id)
            edits=manager.list_edits(session_id=session.id,principal=principal)['items']
            after=counts()
            record={'case':label,'input':message,'status':saved.status,'answer':result.answer,'tools':[c.args[0] for c in dispatch.call_args_list],'artifact':saved.public_artifact,'edits':edits,'counts':after,'before_counts':before}
            record['timing']={'wall_seconds':round(time.perf_counter()-started,3),'provider_calls':evidence['provider_calls']-calls_before,
                              'usage':{k:evidence['usage'][k]-usage_before[k] for k in usage_before},'tools':tool_times}
            evidence['cases'].append(record);persist()
            print(json.dumps({'case':label,'status':saved.status,'tools':record['tools'],'usage':evidence['usage'],'counts':after},ensure_ascii=False),flush=True)
            assert after['investigation_runs']==before['investigation_runs']+run_delta,'Unexpected number of task starts'
            assert saved.status=='completed','Conversation did not complete'
            return record
        try:
            with patch.object(Completions,'create',completion):
                if args.flow_matrix:
                    from resource_flow_matrix import run_matrix
                    run_matrix(stack,manager,principal,start_session,turn,evidence,persist,args.scenario)
                    if evidence['status'] != 'PASS':
                        raise SystemExit(1)
                    return
                if args.existing_task_only:
                    current=turn('existing-natural-task','我想调查小红书上的赌博博彩推广风险，帮我准备抓取审核任务配置，先不要开始执行。')
                    assert current['counts']['investigation_drafts']==1
                    assert current['artifact']['draft']['configuration']['judgement']['strategy']=='existing_ruleset'
                    assert current['artifact']['draft']['configuration']['investigation']['recall_plan']['strategy']=='existing_lexicon'
                    evidence['status']='PASS'
                    return
                generated=turn('generate-no-save','请针对维汉民族关系讨论生成一套可实际审核的民族类 rule（4—6条）和一份 lexicon（3个主词，每个保留1个变体）。先完整展示，暂时不保存，也不创建或启动任务。')
                assert {e['kind'] for e in generated['edits']}=={'ruleset','lexicon'},'Both resources must be generated'
                assert all(not e['saved'] for e in generated['edits'])
                rules=next(e for e in generated['edits'] if e['kind']=='ruleset')['content']
                assert 4<=sum(len(c['rules']) for c in rules['categories'])<=6,'Requested rule count was not preserved'
                assert generated['counts']['investigation_drafts']==0
                if args.generation_only:
                    evidence['status']='PASS'
                    return
                lex=next(e for e in generated['edits'] if e['kind']=='lexicon')
                main=next(e for e in lex['content']['entries'] if e['kind']=='main')
                saved=turn('edit-and-save-both',f'把词库主词“{main["term"]}”改成“维汉日常交往”，保留它的变体。然后把这份词库和刚才生成的整套规则都保存到后台，仍然不要创建或启动任务。')
                assert all(e['saved'] for e in saved['edits'])
                assert saved['counts']['investigation_drafts']==0
                draft=turn('natural-language-draft','现在帮我准备小红书抓取审核草案：审核采用刚才已经正式保存的那套民族规则，搜索词只临时使用刚才词库里的启用主词，不搜索变体。先给我看草案，不要开始抓取。')
                assert draft['counts']['investigation_drafts']==1
                # Reopen the saved resources from another conversation: no generation replay.
                session=conversation.create_session(principal=principal,workspace_key='resource-qwen-existing')
                rule_name=next(e for e in saved['edits'] if e['kind']=='ruleset')['content']['name']
                lex_name=next(e for e in saved['edits'] if e['kind']=='lexicon')['content']['title']
                existing=turn('read-edit-existing',f'请读取后台已有的词库“{lex_name}”和规则“{rule_name}”。把词库主词“维汉日常交往”改成“维汉日常交流”，保留变体并保存回原词库。规则只把名称改成“民族关系审核副本”，其余条件不变，另存一份。不要创建或启动任务。')
                assert existing['counts']['investigation_drafts']==1
                assert len(existing['edits'])==2 and all(e['saved'] for e in existing['edits'])
                original_lex=next(e for e in saved['edits'] if e['kind']=='lexicon')
                new_lex=next(e for e in existing['edits'] if e['kind']=='lexicon')
                assert new_lex['saves'][-1]['resource_id']==original_lex['saves'][-1]['resource_id']
                original_rule=next(e for e in saved['edits'] if e['kind']=='ruleset')
                copied_rule=next(e for e in existing['edits'] if e['kind']=='ruleset')
                assert copied_rule['saves'][-1]['resource_id']!=original_rule['saves'][-1]['resource_id']
                assert copied_rule['content']['categories']==original_rule['content']['categories']
                assert new_lex['content']['entries'][1]['parent_id']==original_lex['content']['entries'][1]['parent_id']
                evidence['status']='PASS'
        except Exception as exc:
            evidence['status']='INCOMPLETE';evidence['failure_type']=type(exc).__name__;evidence['failure']=str(exc).replace(key,'[redacted]')
            print(json.dumps({'status':'INCOMPLETE','failure_type':type(exc).__name__,'failure':str(exc).replace(key,'[redacted]')},ensure_ascii=False),flush=True)
        finally:
            persist();next(fixture,None)
    print(json.dumps({'status':evidence['status'],'provider_calls':evidence['provider_calls'],'usage':evidence['usage']},ensure_ascii=False))
    if evidence['status'] != 'PASS':
        raise SystemExit(1)

if __name__=='__main__':main()
