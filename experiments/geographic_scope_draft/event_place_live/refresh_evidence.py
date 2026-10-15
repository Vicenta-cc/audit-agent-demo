"""Re-read observed DB tool calls to account for native deferred-tool wrappers.
No model calls, DB mutations, or expected-result changes.
"""
import argparse
import json
import hashlib
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from run_live import db,dump,evaluate,HERE,RUNTIME

def extract(tid):
    calls=[];descriptions=[]
    with db('investigation.sqlite3') as c:
        for row in c.execute('SELECT role,tool_name,content,metadata_json FROM investigation_messages WHERE turn_id=? ORDER BY sequence',(tid,)):
            meta=json.loads(row['metadata_json'] or '{}')
            for call in meta.get('tool_calls',[]) or []:
                func=call.get('function',{});name=func.get('name',call.get('name',''));args=func.get('arguments',call.get('arguments',{}))
                if isinstance(args,str):
                    try:args=json.loads(args)
                    except ValueError:continue
                if name in {'tool_call','tools.call'} and isinstance(args,dict) and args.get('name'):name,args=args['name'],args.get('arguments',{})
                if name=='configure_investigation_draft':calls.append({'name':name,'arguments':args})
                elif name: calls.append({'name':name})
            if row['role']=='tool' and 'configure_investigation_draft' in row['content'] and 'scope_decision' in row['content'] and ('schema' in row['content'] or 'description' in row['content']):
                descriptions.append(row['content'])
    return calls,descriptions

def native_accounting(tid,session_id):
    with db('investigation.sqlite3') as c:
        row=c.execute('SELECT created_at,completed_at FROM investigation_turns WHERE id=?',(tid,)).fetchone()
    if not row or not row['completed_at']:return None
    start=datetime.fromisoformat(row['created_at']);end=datetime.fromisoformat(row['completed_at'])
    log=RUNTIME/'data/hermes-investigation-creation/sessions'/hashlib.sha256(session_id.encode()).hexdigest()/'logs/agent.log'
    if not log.exists():return None
    calls=[];lifecycle=[]
    for line in log.read_text().splitlines():
        try:at=datetime.strptime(line[:23],'%Y-%m-%d %H:%M:%S,%f').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
        except ValueError:continue
        if not start<=at<=end:continue
        found=re.search(r'API call #(\d+): model=(\S+) provider=(\S+) in=(\d+) out=(\d+) total=(\d+) latency=([0-9.]+)s',line)
        if found:
            n,model,provider,inp,out,total,seconds=found.groups();calls.append({'number':int(n),'model':model,'provider':provider,'input_tokens':int(inp),'output_tokens':int(out),'total_tokens':int(total),'latency_seconds':float(seconds)})
        if 'Turn ended:' in line:lifecycle.append(line.split('Turn ended:',1)[1].strip())
        elif 'agent.tool_executor: tool ' in line:lifecycle.append(line.split('agent.tool_executor: ',1)[1].strip())
    return {'source':'normal native agent.log, timestamps bounded by persisted turn','api_calls':calls,'llm_call_count':len(calls),'total_tokens':sum(c['total_tokens'] for c in calls),'tool_lifecycle_and_exit':lifecycle}

def safe_receipts(sessions,turn_ids=None):
    receipts=[]
    with db('investigation_creation.sqlite3') as c:
        for sid in sessions:
            for r in c.execute('SELECT session_id,turn_id,tool_name,status,response_json FROM investigation_creation_tool_receipts WHERE session_id=? ORDER BY created_at',(sid,)):
                if turn_ids is not None and r['turn_id'] not in turn_ids:continue
                value=json.loads(r['response_json'] or '{}');err=value.get('error') or {}
                receipts.append({'session_id':sid,'turn_id':r['turn_id'],'tool_name':r['tool_name'],'status':r['status'],'error_code':err.get('code'),'error_message':err.get('message')})
    return receipts

def main():
    ap=argparse.ArgumentParser();ap.add_argument('output');ap.add_argument('--case-file',default=str(HERE/'cases.json'));a=ap.parse_args();out=Path(a.output)
    cases={c['id']:c for c in json.loads(Path(a.case_file).read_text())['cases']};records=[]
    for path in sorted(out.glob('[EH]*.json')):
        r=json.loads(path.read_text())
        for t in r.get('turns',[]):
            t['model_tool_calls'],t['configure_describe_receipts']=extract(t['turn_id'])
            t['native_accounting']=native_accounting(t['turn_id'],t['session_id'])
        r['session_tool_receipts']=safe_receipts(r.get('sessions',[]),{t['turn_id'] for t in r.get('turns',[])})
        if r.get('snapshot'):
            r['evaluation']=evaluate(cases[r['id'][:3]],r['snapshot'],r['turns'])
            if r['id'][:3] in {'E21','E22','E23'}:
                for name in ['create_ruleset_proposal','create_lexicon_edit']:
                    if not any(x['tool_name']==name and x['status']=='SUCCEEDED' for x in r['session_tool_receipts']):
                        r['evaluation']['pass']=False;r['evaluation']['errors'].append('real temporary resource generation not successful: '+name)
        dump(path,r);records.append(r)
    dump(out/'combined-summary.json',{'total_expected_runs':sum(2 if c['id']=='E21' else 1 for c in cases.values()),'completed':len(records),'passed':sum(r['evaluation']['pass'] for r in records),'failed':sum(not r['evaluation']['pass'] for r in records),'llm_calls':sum((t.get('native_accounting') or t['accounting'])['llm_call_count'] for r in records for t in r.get('turns',[])),'total_tokens':sum((t.get('native_accounting') or t['accounting'])['total_tokens'] for r in records for t in r.get('turns',[])),'records':[{'id':r['id'],**r['evaluation']} for r in records]})

if __name__=='__main__':main()
