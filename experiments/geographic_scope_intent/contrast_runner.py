"""Bounded paired contrast; preparation is offline, real execution is opt-in."""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import time

from .data import load_cases, validate_dataset
from .runner import HERE, RealProvider, run_case

ROOT = HERE.parents[1]
MODEL = 'deepseek-v4-pro-ga-260813'
BASE_URL = 'https://api.qhose.net/v1'
ARMS = ('full','bridge','simple')
PRIVATE = Path('/Users/ext.wanghongtao6/Documents/Codex/acceptance/unified-draft-frontend-20261012/environment.private.json')
CONFIG_ROOT = Path('/Users/ext.wanghongtao6/.codex/worktrees/unified-report-query-phase1/xhs-audit-agent-demo')
FREEZE = HERE/'evidence/contrast-v1-freeze.json'
OUTPUT = ROOT/'outputs/geographic-scope-intent/contrast-v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def request_hash(request):
    return hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def write_json(path, data, *, exclusive=False):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    if exclusive:
        with path.open('x') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2)
            stream.write('\n')
        return
    temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w') as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def make_schedule(cases):
    identities=[c['case_id'] for c in cases]
    random.Random(20261014).shuffle(identities)
    schedule=[]
    for index,identity in enumerate(identities):
        rotation=index%3
        for arm in ARMS[rotation:]+ARMS[:rotation]:
            schedule.append({'case_id':identity,'arm':arm})
    return schedule


class BudgetProvider:
    """Every HTTP attempt is journaled before dispatch; errors never retry."""
    def __init__(self,provider,ledger,*,max_requests=162,secret=''):
        if not 1 <= max_requests <= 162:
            raise ValueError('request budget must be within authorized 162')
        self.provider=provider
        self.mode=provider.mode
        self.ledger=Path(ledger)
        self.ledger.parent.mkdir(parents=True,exist_ok=True)
        self.ledger.open('x').close()
        self.max_requests=max_requests
        self.attempted=0
        self.completed=0
        self.consecutive_failures=0
        self.stop_reason=None
        self.secret=secret
        self.current={}

    def journal(self,item):
        encoded=json.dumps(item,ensure_ascii=False)
        if self.secret and self.secret in encoded:
            encoded=encoded.replace(self.secret,'[REDACTED]')
            self.stop_reason='sensitive_output_detected'
        with self.ledger.open('a') as stream:
            stream.write(encoded+'\n')
            stream.flush()
            os.fsync(stream.fileno())

    def failed(self):
        self.consecutive_failures+=1
        if self.consecutive_failures >= 3:
            self.stop_reason='three_consecutive_runtime_failures'

    def complete(self,request):
        if self.stop_reason or self.attempted >= self.max_requests:
            raise RuntimeError('contrast stopped or request budget exhausted')
        encoded=json.dumps(request,ensure_ascii=False)
        if self.secret and self.secret in encoded:
            self.stop_reason='sensitive_input_detected'
            raise RuntimeError('sensitive input blocked before HTTP')
        self.attempted+=1
        index=self.attempted
        started=time.monotonic()
        self.journal({'event':'request','index':index,**self.current,'request':deepcopy(request)})
        try:
            response=self.provider.complete(deepcopy(request))
        except Exception as exc:
            self.journal({'event':'error','index':index,**self.current,
                          'error_type':type(exc).__name__,'elapsed_seconds':time.monotonic()-started})
            self.failed()
            raise
        self.completed+=1
        self.journal({'event':'response','index':index,**self.current,'response':deepcopy(response),
                      'elapsed_seconds':time.monotonic()-started})
        if self.stop_reason == 'sensitive_output_detected':
            raise RuntimeError('sensitive output blocked')
        returned=response.get('model') if isinstance(response,dict) else None
        if returned is not None and returned != MODEL or self.mode == 'real' and returned != MODEL:
            self.stop_reason='returned_model_mismatch'
            raise RuntimeError('returned model differs from authorized model')
        choices=response.get('choices') if isinstance(response,dict) else None
        if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict) or not isinstance(choices[0].get('message'),dict):
            self.failed()
        else:
            self.consecutive_failures=0
        return response


def run_arm(case,provider,arm):
    from .contrast_intent import ContrastDispatcher, request_for_arm
    if arm not in ARMS:
        raise ValueError('unknown contrast arm')
    if arm == 'full':
        trace=run_case(case,provider)
        trace['arm']=arm
        return trace
    dispatcher=ContrastDispatcher(case,arm)
    request=request_for_arm(case,arm)
    trace={'case_id':case['case_id'],'arm':arm,'mode':provider.mode,'requests':[],
           'responses':[],'messages':[],'calls':dispatcher.calls,'termination':'loop_limit',
           'usage':{},'cost':None}
    started=time.monotonic()
    seen=set()
    limits=(1,1) if arm == 'simple' else (4,12)
    dispatched=0
    for _ in range(limits[0]):
        trace['requests'].append(deepcopy(request))
        try:
            response=provider.complete(deepcopy(request))
        except Exception as exc:
            trace['termination']='timeout' if isinstance(exc,TimeoutError) or 'timeout' in type(exc).__name__.lower() else 'provider_error'
            trace['error_type']=type(exc).__name__
            break
        trace['responses'].append(deepcopy(response))
        usage=response.get('usage',{}) if isinstance(response,dict) else {}
        if isinstance(usage,dict):
            for key,value in usage.items():
                if type(value) in (int,float):
                    trace['usage'][key]=trace['usage'].get(key,0)+value
        choices=response.get('choices') if isinstance(response,dict) else None
        if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict) or not isinstance(choices[0].get('message'),dict):
            trace['termination']='no_reply'
            break
        message=choices[0]['message']
        trace['messages'].append(deepcopy(message))
        if message.get('role','assistant') != 'assistant':
            trace['termination']='invalid_arguments'
            break
        request['messages'].append({**deepcopy(message),'role':'assistant'})
        calls=message.get('tool_calls',[])
        if not isinstance(calls,list) or any(not isinstance(c,dict) or not isinstance(c.get('function'),dict) for c in calls):
            trace['termination']='invalid_arguments'
            break
        if not calls:
            trace['termination']='final' if isinstance(message.get('content'),str) and message['content'].strip() else 'no_reply'
            break
        failed=None
        for call in calls:
            function=call['function']
            name=function.get('name','')
            raw=function.get('arguments','')
            try:
                arguments=json.loads(raw) if isinstance(raw,str) else raw
            except (ValueError,TypeError):
                arguments=raw
                failed=failed or 'invalid_json'
            call_id=call.get('id','')
            if not isinstance(name,str) or not name or call.get('type','function') != 'function' or not isinstance(call_id,str) or not call_id or call_id in seen:
                failed=failed or 'invalid_arguments'
            else:
                seen.add(call_id)
            if dispatched >= limits[1]:
                failed=failed or 'loop_limit'
            if failed:
                receipt={'simulated':True,'mutation_applied':False,'ok':False,'code':'NOT_EXECUTED_AFTER_ERROR'}
                dispatcher.calls.append({'name':name if isinstance(name,str) else '',
                                         'arguments':arguments,'receipt':receipt,'raw_call':deepcopy(call)})
            else:
                dispatched+=1
                receipt=dispatcher.dispatch(name,arguments)
                if not receipt['ok']:
                    failed='unknown_tool' if receipt['code'] == 'UNKNOWN_TOOL' else 'invalid_arguments'
            request['messages'].append({'role':'tool','tool_call_id':call_id if isinstance(call_id,str) else '',
                                        'name':name if isinstance(name,str) else '',
                                        'content':json.dumps(receipt,ensure_ascii=False)})
        if failed:
            trace['termination']=failed
            break
        if arm == 'simple':
            trace['termination']='recorded'
            break
    trace['elapsed_seconds']=time.monotonic()-started
    return trace


def selected_cases():
    cases=load_cases()
    validate_dataset(cases)
    cases=[c for c in cases if c['split'] == 'dev']
    if len(cases) != 18:
        raise ValueError('fixed dev count changed; stop before HTTP')
    return cases


def verify_historical():
    previous=json.loads((HERE/'evidence/real-evaluation-freeze.json').read_text())
    bad=[p for p,h in previous['sha256'].items() if not (ROOT/p).is_file() or sha(ROOT/p)!=h]
    manifest=json.loads((HERE/'manifest.json').read_text())
    for path,expected in manifest['sources_sha256'].items():
        for root in (ROOT,CONFIG_ROOT):
            if sha(root/path)!=expected:
                bad.append(str(root/path))
    for path,expected in manifest['snapshots_sha256'].items():
        if sha(HERE/path)!=expected:
            bad.append(path)
    if bad:
        raise ValueError('baseline drift: '+','.join(bad))
    return previous,manifest


def prepare():
    from .contrast_intent import load_labels, request_for_arm
    cases=selected_cases()
    labels=load_labels()
    if set(labels)!={c['case_id'] for c in cases}:
        raise ValueError('sidecar annotations must exactly cover 18 dev cases')
    previous,manifest=verify_historical()
    schedule=make_schedule(cases)
    by_id={c['case_id']:c for c in cases}
    requests={}
    for item in schedule:
        case=by_id[item['case_id']]
        request=request_for_arm(case,item['arm'])
        poisoned=deepcopy(case)
        for key in ('expected','allowed_mutation_tools','critical','critical_negative','category','split','synthetic'):
            poisoned[key]='ASSESSMENT_ONLY_SENTINEL'
        if request_for_arm(poisoned,item['arm'])!=request or 'ASSESSMENT_ONLY_SENTINEL' in json.dumps(request):
            raise ValueError('evaluation metadata leaked into model input')
        requests[item['case_id']+':'+item['arm']]=request
    paths=set(previous['sha256'])
    paths.add(str((HERE/'CONTRAST_PLAN.md').relative_to(ROOT)))
    paths.update(str(p.relative_to(ROOT)) for p in HERE.glob('contrast*') if p.is_file() and p.suffix in {'.py','.txt','.json','.md'})
    paths.update(str(p.relative_to(ROOT)) for p in (ROOT/'tests').glob('test_geographic_scope_contrast*.py'))
    hashes={p:sha(ROOT/p) for p in sorted(paths)}
    freeze={'version':'geo-intent-p0-contrast-v1','frozen_at_utc':datetime.now(timezone.utc).isoformat(),
            'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
            'branch':subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip(),
            'model':MODEL,'provider_host':'api.qhose.net','base_url':BASE_URL,
            'configuration_source':str(PRIVATE),'configuration_root':str(CONFIG_ROOT),
            'authorization':'用户授权18条三组对照，最多162次真实模型请求；不重试、不扩大样本、不改正式业务、不进入P1',
            'counts':{'unique_cases':18,'case_runs':54,'intent_sets':sum(v['action']=='set' for v in labels.values()),'draft_sets':5},
            'limits':{'total_requests':162,'full_requests_per_case':4,'bridge_requests_per_case':4,'simple_requests_per_case':1,
                      'full_bridge_tool_requests':12,'simple_tool_requests':1,'timeout_seconds':45,'automatic_retries':0},
            'schedule_seed':20261014,'schedule':schedule,'sha256':hashes,
            'sources_sha256':manifest['sources_sha256'],
            'initial_request_sha256':{k:request_hash(v) for k,v in requests.items()},
            'input_leak_checks_passed':54,'labels':labels,
            'notes':['All 18 reused synthetic dev cases; diagnostic contrast, not independent holdout.',
                     'Observer receipts never consult annotation/source/region truth; semantics scored offline.',
                     'B adds record duty/outlet; C removes business prompt/tools: combined interventions.',
                     'Successful simple tool output ends recorded after one request, no acknowledgment request.']}
    write_json(OUTPUT/'prepared.json',{'kind':'offline_prepared_contrast','real_model_calls':0,'requests':requests},exclusive=True)
    write_json(FREEZE,freeze,exclusive=True)
    print(json.dumps({'kind':'contrast_frozen_offline','cases':18,'case_runs':54,'real_model_calls':0,'freeze':str(FREEZE)},ensure_ascii=False))


def verify_freeze():
    freeze=json.loads(FREEZE.read_text())
    verify_historical()
    bad=[p for p,h in freeze['sha256'].items() if sha(ROOT/p)!=h]
    if bad:
        raise ValueError('contrast freeze mismatch: '+','.join(bad))
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    branch=subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()
    if (head,branch)!=(freeze['head'],freeze['branch']):
        raise ValueError('checkout changed after freeze')
    return freeze


def execute(authorization_label):
    from .contrast_intent import load_labels, request_for_arm
    from .contrast_scoring import aggregate_contrast
    freeze=verify_freeze()
    cases=selected_cases()
    labels=load_labels()
    forbidden=[OUTPUT/(arm+'.json') for arm in ARMS]+[OUTPUT/'http-ledger.jsonl',OUTPUT/'run-status.json']
    if any(p.exists() for p in forbidden):
        raise FileExistsError('never overwrite or resume an already attempted contrast')
    # Filter immediately; no other provider settings or secrets enter the process.
    values=json.loads(PRIVATE.read_text())
    selected={k:values.get(k) for k in ('CONVERSATION_MODEL','CONVERSATION_BASE_URL','CONVERSATION_API_KEY')}
    del values
    if not all(isinstance(v,str) and v for v in selected.values()):
        raise ValueError('coordinator configuration incomplete')
    if selected['CONVERSATION_MODEL']!=MODEL or selected['CONVERSATION_BASE_URL'].rstrip('/')!=BASE_URL:
        raise ValueError('coordinator configuration differs from authorized model/provider')
    os.environ.update(selected)
    delegate=RealProvider(enabled=True,authorization_label=authorization_label,config_root=CONFIG_ROOT,timeout=45)
    provider=BudgetProvider(delegate,OUTPUT/'http-ledger.jsonl',secret=selected['CONVERSATION_API_KEY'])
    by_id={c['case_id']:c for c in cases}
    traces={arm:[] for arm in ARMS}
    status={'kind':'real_model_contrast','version':freeze['version'],'authorization_label':authorization_label,
            'model':MODEL,'provider_host':'api.qhose.net','freeze_sha256':sha(FREEZE),'schedule_completed':0,
            'requests':0,'completed_responses':0,'stop_reason':None,'cost':'unknown'}
    write_json(OUTPUT/'run-status.json',status,exclusive=True)
    started=time.monotonic()
    for item in freeze['schedule']:
        if provider.stop_reason or provider.attempted>=162:
            break
        case=by_id[item['case_id']]
        if request_hash(request_for_arm(case,item['arm']))!=freeze['initial_request_sha256'][item['case_id']+':'+item['arm']]:
            provider.stop_reason='initial_request_drift'
            break
        provider.current=item
        trace=run_arm(case,provider,item['arm'])
        encoded=json.dumps(trace,ensure_ascii=False)
        if selected['CONVERSATION_API_KEY'] in encoded:
            provider.stop_reason='sensitive_output_detected'
            trace=json.loads(encoded.replace(selected['CONVERSATION_API_KEY'],'[REDACTED]'))
        traces[item['arm']].append(trace)
        for arm in ARMS:
            write_json(OUTPUT/(arm+'.json'),{'kind':'real_model_contrast_arm','arm':arm,'model':MODEL,
                       'freeze_sha256':status['freeze_sha256'],'traces':traces[arm],
                       'automatic':aggregate_contrast(cases,traces[arm],labels,arm=arm)},exclusive=False)
        status.update(schedule_completed=sum(map(len,traces.values())),requests=provider.attempted,
                      completed_responses=provider.completed,stop_reason=provider.stop_reason,
                      elapsed_seconds=time.monotonic()-started)
        write_json(OUTPUT/'run-status.json',status)
        print(json.dumps({**item,'completed_runs':status['schedule_completed'],'requests':provider.attempted,
                          'responses':provider.completed,'termination':trace['termination'],
                          'seconds':round(trace['elapsed_seconds'],2)},ensure_ascii=False),flush=True)
    status.update(stop_reason=provider.stop_reason,schedule_completed=sum(map(len,traces.values())),
                  requests=provider.attempted,completed_responses=provider.completed,
                  elapsed_seconds=time.monotonic()-started,
                  remaining_schedule=freeze['schedule'][sum(map(len,traces.values())):])
    write_json(OUTPUT/'run-status.json',status)
    verify_freeze()
    print(json.dumps({'kind':'contrast_finished','completed_runs':status['schedule_completed'],
                      'requests':provider.attempted,'responses':provider.completed,'stop_reason':provider.stop_reason},ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--enable-real-model',action='store_true')
    parser.add_argument('--authorization-label',default='')
    args=parser.parse_args()
    if args.prepare:
        prepare()
    elif args.enable_real_model and args.authorization_label.strip():
        execute(args.authorization_label)
    else:
        parser.error('real execution requires explicit enable and authorization label')


if __name__=='__main__':
    main()
