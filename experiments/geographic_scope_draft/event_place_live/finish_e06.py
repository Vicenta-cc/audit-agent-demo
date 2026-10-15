"""Finish an already accepted E06 turn after splitting the remaining scheduler.
No repeated user/model turn. Reads authoritative existing session records.
"""
import json,time
from datetime import datetime
from pathlib import Path
from run_live import Client,db,dump,evaluate,HERE
from refresh_evidence import extract,native_accounting,safe_receipts
s='investigation-session:55adf602d84145fcbbd7e5c6529f259f';c=Client();c.sessions=[s]
while True:
    with db('investigation.sqlite3') as conn:pending=conn.execute("SELECT count(*) FROM investigation_turns WHERE session_id=? AND status in ('queued','running')",(s,)).fetchone()[0]
    if not pending:break
    time.sleep(2)
snap=c.snapshot(s)
if not snap.get('draft'):
    c.turn(s,'按已有信息和刚才选择的测试资源生成草案即可；不需要补充信息，不启动采集。')
    snap=c.snapshot(s)
turns=[]
with db('investigation.sqlite3') as conn:
    for row in conn.execute('SELECT * FROM investigation_turns WHERE session_id=? ORDER BY created_at',(s,)):
        usr=conn.execute('SELECT content FROM investigation_messages WHERE id=?',(row['user_message_id'],)).fetchone()
        ans=conn.execute('SELECT content FROM investigation_messages WHERE id=?',(row['assistant_message_id'],)).fetchone()
        acct={k:row[k] for k in ['id','status','input_tokens','output_tokens','total_tokens','llm_call_count','stop_reason','error_code','safe_message']}
        calls,descs=extract(row['id']);turns.append({'turn_id':row['id'],'session_id':s,'user':usr[0],'answer':ans[0] if ans else '', 'status':row['status'],'safe_message':row['safe_message'],'seconds':round((datetime.fromisoformat(row['completed_at'])-datetime.fromisoformat(row['created_at'])).total_seconds(),2),'accounting':acct,'native_accounting':native_accounting(row['id'],s),'model_tool_calls':calls,'configure_describe_receipts':descs})
case=next(x for x in json.loads((HERE/'cases.json').read_text())['cases'] if x['id']=='E06')
r={'id':'E06','expectation':case['expected'],'sessions':[s],'setup':case['resource_setup'],'scheduler_note':'original accepted turn completed; rest split into parallel scheduler without duplicate model calls','turns':turns,'snapshot':snap,'session_tool_receipts':safe_receipts([s]),'evaluation':evaluate(case,snap,turns)}
dump(HERE/'results/native/E06.json',r);print(r['evaluation'],flush=True)
