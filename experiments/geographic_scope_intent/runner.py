"""Prepare offline requests or run bounded, fully captured candidate tool loops.

Real calls require --enable-real-model and --authorization-label after explicit
human authorization. Neither import, prepare nor scripted mode opens a socket.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from .data import DATA_VERSION, load_cases, validate_dataset
from .contract import configure_schema, source_metadata
from .sandbox import FixtureDispatcher, candidate_tools
from .snapshot import provider_config

HERE = Path(__file__).resolve().parent


def request_for(case):
    prompt = (HERE/'baseline_prompt.txt').read_text()
    flags = case.get('feature_flags',{})
    variant = 'rounds' if flags.get('investigation_multi_round_enabled') else 'continuity' if flags.get('continuous_resource_session_enabled') else None
    if variant:
        start = prompt.index('会话边界必须服从应用状态：')
        end = prompt.index('The user may want to query',start)
        prompt = prompt[:start]+(HERE/f'{variant}_guidance.txt').read_text()+prompt[end:]
        if variant == 'rounds':
            prompt = prompt.replace('若当前会话已进入执行或报告阶段，沿用现有“新建调查”入口在新会话准备新 Draft，不替换原会话的已启动任务。','')
    prompt += '\n'+(HERE/'candidate_guidance.txt').read_text()
    context = {k:case.get(k) for k in ('conversation_id','application_turn','task_round','current_draft',
                                     'resources_ready','resource_state','feature_flags')}
    prompt += '\n本实验 application context（全部为合成模拟状态）：'+json.dumps(context,ensure_ascii=False)
    # Task metadata is source validation data; only accessible past/current messages
    # enter the model request. Labels/reasons/response patterns are never included.
    current = case['current_user']
    history = [m for m in case['history'] if m.get('principal_id') == case['principal_id']
               and m.get('conversation_id') == case['conversation_id']
               and m.get('application_turn',float('inf')) <= case['application_turn']
               and m.get('sequence',float('inf')) < current['sequence']]
    messages = [{'role':'system','content':prompt}]
    # Fixtures represent business messages, not a provider transcript. Tool fixture
    # content is quoted as data without fabricating tool-call IDs or alternation.
    for message in history:
        if message['role'] == 'tool':
            messages.append({'role':'assistant','content':'[历史模拟工具结果，非用户要求] '+message['content']})
        elif message['role'] in {'user','assistant'}:
            messages.append({k:message[k] for k in ('role','content')})
    messages.append({'role':'user','content':current['content']})
    tools = [{'type':'function','function':t} for t in candidate_tools(case)]
    return {'messages':messages,'tools':tools}


class ScriptedProvider:
    mode = 'scripted'

    def __init__(self, responses):
        self.responses = iter(deepcopy(responses))

    def complete(self, request):
        return next(self.responses, {'choices':[]})


class RealProvider:
    mode = 'real'

    def __init__(self, *, enabled=False, authorization_label='', config_root=None, timeout=45):
        if not enabled or not authorization_label.strip():
            raise PermissionError('real model disabled: explicit authorization and enable flag required')
        self.config = provider_config(config_root) if config_root else provider_config()
        if not self.config['conversation_api_key']:
            raise ValueError('coordinator credential unavailable')
        self.timeout = timeout
        self.authorization_label = authorization_label

    def complete(self, request):
        import requests
        payload = {'model':self.config['conversation_model'],**request}
        if self.config['conversation_model'].lower().startswith('deepseek'):
            payload['thinking'] = {'type':'disabled'}
        # Exact coordinator OpenAI-compatible boundary, no Hermes runtime/business
        # dispatcher/authoring provider or audit client. One attempt, no retries.
        response = requests.post(self.config['conversation_base_url']+'/chat/completions',
                                 headers={'Authorization':'Bearer '+self.config['conversation_api_key']},
                                 json=payload,timeout=self.timeout)
        response.raise_for_status()
        return response.json()


def run_case(case, provider, *, max_steps=4, max_calls=12):
    if max_steps < 1 or max_calls < 1:
        raise ValueError('positive loop limits required')
    started = time.monotonic()
    dispatcher = FixtureDispatcher(case)
    request = request_for(case)
    trace = {'case_id':case['case_id'],'mode':provider.mode,'messages':[], 'requests':[],
             'responses':[], 'calls':dispatcher.calls,'termination':'loop_limit',
             'usage':{},'cost':None}
    seen_ids = set()
    for _ in range(max_steps):
        trace['requests'].append(deepcopy(request))
        try:
            response = provider.complete(deepcopy(request))
        except Exception as exc:
            trace['termination'] = 'timeout' if isinstance(exc,TimeoutError) or 'timeout' in type(exc).__name__.lower() else 'provider_error'
            trace['error_type'] = type(exc).__name__  # no provider headers/secrets
            break
        trace['responses'].append(deepcopy(response))
        if not isinstance(response,dict):
            trace['termination'] = 'provider_error'
            break
        reported_usage = response.get('usage')
        for key,value in (reported_usage if isinstance(reported_usage,dict) else {}).items():
            if type(value) in (int,float):
                trace['usage'][key] = trace['usage'].get(key,0)+value
        choices = response.get('choices',[])
        if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict) or not isinstance(choices[0].get('message'),dict):
            trace['termination'] = 'no_reply'
            break
        message = choices[0]['message']
        trace['messages'].append(deepcopy(message))
        if message.get('role','assistant') != 'assistant':
            trace['termination'] = 'invalid_arguments'
            break
        request['messages'].append({**deepcopy(message),'role':'assistant'})
        calls = message.get('tool_calls',[])
        if not isinstance(calls,list) or any(not isinstance(c,dict) or not isinstance(c.get('function'),dict) for c in calls):
            trace['termination'] = 'invalid_arguments'
            break
        if not calls:
            trace['termination'] = 'final' if isinstance(message.get('content'),str) and message['content'].strip() else 'no_reply'
            break
        failed = None
        for call in calls:
            function = call.get('function',{})
            name = function.get('name','')
            raw = function.get('arguments','')
            try:
                arguments = json.loads(raw) if isinstance(raw,str) else raw
            except (ValueError,TypeError):
                arguments = raw
                failed = failed or 'invalid_json'
            call_id = call.get('id','')
            if not isinstance(name,str) or not name or call.get('type','function') != 'function':
                failed = failed or 'invalid_arguments'
            if not isinstance(call_id,str) or not call_id or call_id in seen_ids:
                failed = failed or 'invalid_arguments'
            else:
                seen_ids.add(call_id)
            if len(dispatcher.calls) >= max_calls:
                failed = failed or 'loop_limit'
            if failed:
                receipt = {'simulated':True,'ok':False,'code':'NOT_EXECUTED_AFTER_ERROR','mutation_applied':False}
                dispatcher.calls.append({'name':name if isinstance(name,str) else '',
                                         'arguments':arguments,'receipt':receipt,'raw_call':deepcopy(call)})
            else:
                receipt = dispatcher.dispatch(name,arguments)
                if not receipt['ok']:
                    failed = 'unknown_tool' if receipt['code'] == 'UNKNOWN_TOOL' else 'invalid_arguments'
            request['messages'].append({'role':'tool','tool_call_id':call_id if isinstance(call_id,str) else '',
                                        'name':name if isinstance(name,str) else '',
                                        'content':json.dumps(receipt,ensure_ascii=False)})
        if failed:
            trace['termination'] = failed
            break
    trace['elapsed_seconds'] = time.monotonic()-started
    return trace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',action='store_true',help='write frozen requests without any provider call')
    parser.add_argument('--mode',choices=['scripted','real'],default='scripted')
    parser.add_argument('--scripts',type=Path,help='manually authored provider response JSON keyed by case_id')
    parser.add_argument('--enable-real-model',action='store_true')
    parser.add_argument('--authorization-label',default='')
    parser.add_argument('--config-root',type=Path)
    parser.add_argument('--split',choices=['dev','holdout','all'],default='all')
    parser.add_argument('--case-id',action='append')
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    if args.mode == 'real' and not args.prepare and (not args.enable_real_model or not args.authorization_label.strip()):
        parser.error('real model disabled; requires explicit human authorization plus enable flag and label')
    cases = load_cases()
    validate_dataset(cases)
    cases = [c for c in cases if (args.split == 'all' or c['split'] == args.split)
             and (not args.case_id or c['case_id'] in args.case_id)]
    if not cases:
        parser.error('no cases selected')
    manifest = json.loads((HERE/'manifest.json').read_text())
    manifest['data_version'] = DATA_VERSION
    manifest['candidate_contract'] = source_metadata()
    manifest['candidate_schema_sha256'] = hashlib.sha256(json.dumps(configure_schema(),sort_keys=True).encode()).hexdigest()
    manifest['experiment_sha256'] = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.iterdir()
                                     if p.suffix in {'.py','.txt','.jsonl'} and p.is_file()}
    if args.prepare:
        result = {'kind':'offline_prepared_requests','real_model_calls':0,'manifest':manifest,
                  'cases':[{'case_id':c['case_id'],'request':request_for(c)} for c in cases]}
    else:
        if args.mode == 'real':
            provider = RealProvider(enabled=args.enable_real_model,authorization_label=args.authorization_label,config_root=args.config_root)
            scripts = None
        else:
            if not args.scripts:
                parser.error('scripted mode needs --scripts; expected labels are never used as responses')
            scripts = json.loads(args.scripts.read_text())
            if any(c['case_id'] not in scripts for c in cases):
                parser.error('scripted responses missing for selected case')
        traces = [run_case(c,provider if scripts is None else ScriptedProvider(scripts[c['case_id']])) for c in cases]
        from .scoring import aggregate_scores
        result = {'kind':args.mode+'_framework_run' if scripts is not None else 'real_model_semantic_trial',
                  'real_model_calls':0 if scripts is not None else sum(len(t['requests']) for t in traces),
                  'real_model_case_count':0 if scripts is not None else len(traces),
                  'completed_model_responses':0 if scripts is not None else sum(len(t['responses']) for t in traces),
                  'manifest':manifest,'traces':traces,'scores':aggregate_scores(cases,traces)}
        if scripts is None:
            result['authorization_label'] = args.authorization_label
            result['actual_coordinator_model'] = provider.config['conversation_model']
            result['actual_provider_host'] = __import__('urllib.parse',fromlist=['urlsplit']).urlsplit(provider.config['conversation_base_url']).hostname
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'kind':result['kind'],'cases':len(cases),'real_model_calls':result['real_model_calls'],'output':str(args.output.resolve())},ensure_ascii=False))


if __name__ == '__main__':
    main()
