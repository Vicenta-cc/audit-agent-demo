"""Real HTTP/native-model experiment. Never constructs model tools or scope state.

Only safe public replies, configure arguments, and accounting are exported.
Database reads below are read-only. Isolated fixture resources are disclosed.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import http.cookiejar
import json
from pathlib import Path
import sqlite3
import time
import urllib.request
import uuid

HERE=Path(__file__).resolve().parent
RUNTIME=Path('/Users/ext.wanghongtao6/Documents/Codex/runtime/geographic-p1-event-place-20261015')
BASE='http://127.0.0.1:8993'
FORMAL='在抖音调查，使用现有“独立测试通用舆情规则”和“独立测试通用舆情词库”。这些测试资源按原样使用即可。请生成调查草案，不启动采集，不正式保存新资源。'
TEMP='对，在抖音调查这件事。请真实生成本次调查临时使用的审核规则和黑话库，完整展示给我，不正式保存，也不启动采集。'
ADOPT='就用刚才展示的这份临时审核规则和黑话库，生成调查草案，不启动采集，不正式保存资源。'

def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def db(file):
    c=sqlite3.connect(f'file:{RUNTIME/"data"/file}?mode=ro',uri=True);c.row_factory=sqlite3.Row;return c

class Client:
    def __init__(self):
        self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        login=self.request('POST','/api/auth/login',{'username':'p1-tester','password':'P1-Event-Test-2026!'})
        self.csrf=login['csrf_token'];self.turns=[];self.sessions=[]
    def request(self,method,path,value=None):
        headers={'Content-Type':'application/json'}
        if getattr(self,'csrf',None):headers['X-CSRF-Token']=self.csrf
        req=urllib.request.Request(BASE+path,data=None if value is None else json.dumps(value,ensure_ascii=False).encode(),headers=headers,method=method)
        with self.opener.open(req,timeout=30) as response:return json.load(response)
    def new(self):
        s=self.request('POST','/api/investigation-workspaces',{'workspace_key':f'event-place-{uuid.uuid4().hex}'})['workspace_session_id'];self.sessions.append(s);return s
    def state(self,s):return self.request('GET',f'/api/investigation-workspaces/{s}/state')
    def turn(self,s,text):
        start=time.monotonic();t=self.request('POST',f'/api/investigation-workspaces/{s}/turns',{'client_message_id':uuid.uuid4().hex,'content':text})['turn_id']
        while True:
            result=self.request('GET',f'/api/investigation-workspace-turns/{t}')
            if result['status'] in {'completed','error','interrupted'}:break
            if time.monotonic()-start>300:raise TimeoutError(f'turn {t} exceeded 300s')
            time.sleep(1.5)
        with db('investigation.sqlite3') as c:
            account=dict(c.execute('SELECT id,status,input_tokens,output_tokens,total_tokens,llm_call_count,stop_reason,error_code,safe_message FROM investigation_turns WHERE id=?',(t,)).fetchone())
            calls=[];descriptions=[]
            for row in c.execute('SELECT role,tool_name,content,metadata_json FROM investigation_messages WHERE turn_id=? ORDER BY sequence',(t,)):
                meta=json.loads(row['metadata_json'] or '{}')
                for call in meta.get('tool_calls',[]) or []:
                    func=call.get('function',{});name=func.get('name',call.get('name',''));args=func.get('arguments',call.get('arguments',{}))
                    if isinstance(args,str):
                        try:args=json.loads(args)
                        except ValueError:args={'unparsed':args[:2000]}
                    if name in {'tool_call','tools.call'} and isinstance(args,dict) and args.get('name'):
                        name=args['name'];args=args.get('arguments',{})
                    if name=='configure_investigation_draft':calls.append({'name':name,'arguments':args})
                    else:calls.append({'name':name})
                if row['role']=='tool' and row['tool_name'] in {'describe_tool','describe_tools','tool_describe'} and 'configure_investigation_draft' in row['content']:
                    descriptions.append(row['content'])
        record={'turn_id':t,'session_id':s,'user':text,'status':result['status'],'answer':result.get('answer',''),'safe_message':result.get('safe_message',''),'seconds':round(time.monotonic()-start,2),'accounting':account,'model_tool_calls':calls,'configure_describe_receipts':descriptions}
        self.turns.append(record)
        print(json.dumps({'turn':t,'status':result['status'],'calls':account['llm_call_count'],'seconds':record['seconds']},ensure_ascii=False),flush=True)
        return result
    def snapshot(self,s):
        st=self.state(s);artifact=st.get('draft_artifact') or {};draft=artifact.get('draft') or {};did=draft.get('id') or draft.get('draft_id')
        if not did:return {'session_id':s,'draft':None,'artifact_type':artifact.get('artifact_type')}
        with db('investigation_creation.sqlite3') as c:
            row=c.execute('SELECT id,current_revision,title,objective,configuration_json FROM investigation_drafts WHERE id=?',(did,)).fetchone();stored=dict(row);config=json.loads(stored.pop('configuration_json'));stored['configuration']=config
            receipts=[dict(r) for r in c.execute('SELECT tool_name,status,is_mutation,turn_id FROM investigation_creation_tool_receipts WHERE session_id=? ORDER BY created_at',(s,))]
        return {'session_id':s,'draft':stored,'public_draft':draft,'preview':self.request('GET',f'/api/investigation-drafts/{did}/confirmation-preview'),'receipts':receipts}
    def formal(self,s,text,lexicon=None):
        self.turn(s,text)
        directive=FORMAL
        if lexicon:directive=directive.replace('独立测试通用舆情词库',lexicon)
        self.turn(s,directive)
        if not (self.state(s).get('draft_artifact') or {}).get('draft'):
            self.turn(s,'按已有信息和刚才选择的测试资源生成草案即可；不需要补充信息，不启动采集。')
        return self.snapshot(s)

def evaluate(case,snapshot,turns):
    expected=case['expected'];draft=snapshot.get('draft');errors=[]
    if not draft:return {'pass':False,'errors':['no persisted draft; clarification-only is recorded as incomplete']}
    conf=draft['configuration'];scope=conf.get('relevance_scope');state=conf.get('relevance_scope_state') or {};status=state.get('status','absent');target=(scope or {}).get('target',{}).get('name')
    if expected['status']!=status:errors.append(f"status expected {expected['status']}, got {status}")
    if target!=expected['target']:errors.append(f"target expected {expected['target']}, got {target}")
    public=snapshot.get('public_draft',{}).get('configuration',{})
    public_state=public.get('relevance_scope_state')
    expected_public_state=None if not conf.get('relevance_scope_state') else {k:v for k,v in conf['relevance_scope_state'].items() if k in {'status','target_text','selection_policy','reason','candidates','target','province'}}
    if public.get('relevance_scope')!=scope or public_state!=expected_public_state:errors.append('DB/public draft scope mismatch')
    calls=[x['arguments'] for t in turns for x in t['model_tool_calls'] if x['name']=='configure_investigation_draft']
    cid=case.get('id','')[:3]
    if cid=='E19':
        if draft['title']!='深圳招聘复核':errors.append('title-only update not persisted')
        updates=[x for x in calls if x.get('operation')=='update']
        if not updates or any('relevance_scope_change' in x for x in updates):errors.append('title-only update did not omit scope change')
    if cid=='E20' and not all(x in draft.get('objective','') for x in ['深圳','楼市']):errors.append('changed investigation objective missing')
    if cid=='E21' and state.get('source_quote')!='调查克拉玛依大火':errors.append('adoption did not carry original user source quote')
    if cid=='E22' and '宁波' not in state.get('source_quote',''):errors.append('replacement did not use latest user source')
    if cid=='E23' and ('geographic' in json.dumps(snapshot.get('preview',{}),ensure_ascii=False).lower() and scope):errors.append('cancelled task retains geographic scope')
    if not calls:errors.append('no actual model configure call')
    if case.get('expected',{}).get('decision') and not any(x.get('operation')=='create' and x.get('scope_decision')==expected['decision'] for x in calls):errors.append('expected explicit model create decision absent')
    return {'pass':not errors,'errors':errors,'observed_status':status,'observed_target':target,'scope':scope,'scope_state':state}

def run(case,out):
    client=Client();s=client.new();key=case['id'];record={'id':key,'expectation':case['expected'],'sessions':client.sessions,'setup':case.get('resource_setup'),'started_at':datetime.now(timezone.utc).isoformat()}
    try:
        n=int(key[1:3])
        if n<=18:
            snapshot=client.formal(s,case['input'],'深圳招聘' if n==13 else None)
        elif n in {19,20,24}:
            before=client.formal(s,'调查克拉玛依大火');record['precondition']=before
            if (before.get('draft') or {}).get('configuration',{}).get('relevance_scope',{}).get('target',{}).get('name')!='克拉玛依市':raise RuntimeError('real prior geographic draft not established')
            if n==19:client.turn(s,'仅把草案标题改为“深圳招聘复核”，其他保持不变，不启动采集。')
            elif n==20:client.turn(s,'改查深圳楼市，同步修改调查目标，仍用原测试资源，不启动采集。')
            else:s=client.new();record['independent_session_id']=s;client.formal(s,'调查招聘诈骗')
            snapshot=client.snapshot(s)
        else:
            client.turn(s,'调查克拉玛依大火')
            client.turn(s,TEMP)
            if n==22:client.turn(s,'改查宁波住房问题。请相应调整刚才的临时规则和黑话库并展示，不正式保存，也不开始采集。')
            elif n==23:client.turn(s,'本次取消地域限制；调查议题和刚才的临时资源保持不变。')
            client.turn(s,ADOPT);snapshot=client.snapshot(s)
        record['snapshot']=snapshot;record['evaluation']=evaluate(case,snapshot,client.turns)
    except Exception as exc:record['evaluation']={'pass':False,'errors':[f'{type(exc).__name__}: {str(exc)[:1000]}']}
    record['turns']=client.turns;record['ended_at']=datetime.now(timezone.utc).isoformat();dump(out/f'{key}.json',record)
    print(json.dumps({'case':key,'pass':record['evaluation']['pass'],'errors':record['evaluation'].get('errors',[])},ensure_ascii=False),flush=True)
    return record

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cases',default='all');ap.add_argument('--workers',type=int,default=2);ap.add_argument('--output',required=True);ap.add_argument('--case-file',default=str(HERE/'cases.json'));args=ap.parse_args()
    cases=json.loads(Path(args.case_file).read_text())['cases'];expanded=[]
    for c in cases:
        if c['id']=='E21':
            for suffix in ('a','b'):expanded.append(dict(c,id='E21'+suffix))
        else:expanded.append(c)
    wanted=args.cases.split(',');selected=expanded if args.cases=='all' else [c for c in expanded if c['id'] in wanted]
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    records=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(run,c,out) for c in selected]
        for f in as_completed(futures):records.append(f.result());dump(out/'summary.json',{'total':len(selected),'completed':len(records),'passed':sum(r['evaluation']['pass'] for r in records),'records':[{'id':r['id'],**r['evaluation']} for r in sorted(records,key=lambda r:r['id'])]})

if __name__=='__main__':main()
