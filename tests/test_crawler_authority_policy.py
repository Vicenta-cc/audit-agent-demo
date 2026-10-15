"""Owner initializes first scheduler policy without replacing historical limits."""
import json
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest

from backend.audit_agent import crawler_request_authority as authority
from backend.audit_agent.config import settings


def prepare(tmp_path):
    root=settings.media_crawler_dir.resolve()
    if str(root) not in sys.path:
        sys.path.insert(0,str(root))
    from backend.audit_agent.crawler_route import route_contract
    c=route_contract()
    snapshot=c.BoundRoute('dy','account',c.derive_profile_key('account'),'proxy',1,1,'proxy.invalid',8080,
        '','',time.time()+600,None,None,str(tmp_path/'scheduler.sqlite3'),c.derive_scope_ids('dy','account','proxy'))
    handle=authority.open_authority(snapshot,crawler_root=root,session_options=dict(
        echo_url='https://echo.invalid/ip',budget_id='fresh-owner',reuse_existing_policy=True,
        account_policy={'min_interval':3,'per_minute':20}))
    return handle,c


def initialize_gate(handle,c):
    from tools.authority_ledger import owner_access
    marker=owner_access.set(True)
    try:
        handle._execute('initialize',{}, {})
        receipt=c.ProbeReceipt(handle.snapshot.proxy_id,1,'context','198.51.100.7',time.time())
        handle._execute('probe_confirm',{'ip':receipt.ip},{})
        return handle._execute('make_gate',{'receipt':receipt.__dict__},{})
    finally:
        owner_access.reset(marker)


def test_fresh_owner_scheduler_initializes_gate_from_explicit_app_settings(tmp_path,monkeypatch):
    for name,value in dict(request_min_interval=2.5,requests_per_minute=27,request_concurrency=2,
        media_request_interval=6,request_cooldown_seconds=301).items():
        monkeypatch.setattr(settings,name,value)
    handle,c=prepare(tmp_path)
    try:
        assert initialize_gate(handle,c)=={'trial_enabled':False}
        with sqlite3.connect(handle.snapshot.scheduler_db_path) as db:
            baseline=json.loads(db.execute("SELECT limits_json FROM request_scheduler_policy WHERE platform='dy'").fetchone()[0])
        assert baseline==[2.5,27,2,6]
    finally:
        handle.close()


def test_existing_stricter_global_policy_history_cooldown_and_probe_budget_are_preserved(tmp_path,monkeypatch):
    handle,c=prepare(tmp_path)
    from tools.persistent_request_gate import RequestScheduler
    limits=[12,4,1,15]
    state=RequestScheduler(handle.snapshot.scheduler_db_path,min_interval=12,per_minute=4,max_concurrency=1,media_interval=15)
    state.try_acquire()
    state.enter_cooldown('existing-pause',90)
    with sqlite3.connect(handle.snapshot.scheduler_db_path) as db:
        before=db.execute('SELECT * FROM request_scheduler_state').fetchall()
        db.execute('CREATE TABLE IF NOT EXISTS collection_probe_budget (budget_id TEXT PRIMARY KEY,max_calls INTEGER,used INTEGER,initial INTEGER,opens INTEGER,ticks INTEGER,boundaries INTEGER)')
        db.execute('INSERT INTO collection_probe_budget VALUES(?,?,?,?,?,?,?)',('fresh-owner',96,8,1,2,0,0))
    try:
        initialize_gate(handle,c)
        with sqlite3.connect(handle.snapshot.scheduler_db_path) as db:
            assert json.loads(db.execute("SELECT limits_json FROM request_scheduler_policy WHERE platform='dy'").fetchone()[0])==limits
            assert db.execute('SELECT * FROM request_scheduler_state').fetchall()==before
            assert db.execute("SELECT used FROM collection_probe_budget WHERE budget_id='fresh-owner'").fetchone()[0]==8
    finally:
        handle.close()


def test_concurrent_first_owner_seeding_keeps_one_policy_and_original_budget(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path=tmp_path/'scheduler.sqlite3'
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:authority.ensure_owner_request_policy(path),range(6)))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM request_scheduler_policy WHERE platform='dy'").fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM request_scheduler_state').fetchone()[0]==0
    assert all(state.policy()==results[0].policy() for state in results)


def test_existing_account_policy_reflects_stricter_new_login_parent_limits(tmp_path):
    handle,c=prepare(tmp_path)
    from tools.persistent_request_gate import RequestScheduler
    scope='dy:account:account'
    RequestScheduler(handle.snapshot.scheduler_db_path,min_interval=2,per_minute=30,max_concurrency=1,media_interval=5)
    RequestScheduler(handle.snapshot.scheduler_db_path,platform=scope,min_interval=3,per_minute=20,max_concurrency=1,media_interval=5)
    handle.options['account_policy']={'min_interval':9,'per_minute':6}
    try:
        initialize_gate(handle,c)
        with sqlite3.connect(handle.snapshot.scheduler_db_path) as db:
            limits=json.loads(db.execute('SELECT limits_json FROM request_scheduler_policy WHERE platform=?',(scope,)).fetchone()[0])
        assert limits==[9,6,1,5]
    finally:
        handle.close()


def test_monotonic_account_tightening_preserves_other_scopes_history_and_stricter_existing_policy(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    handle,c=prepare(tmp_path)
    from tools.persistent_request_gate import RequestScheduler
    scope='dy:account:account'
    try:
        initialize_gate(handle,c)
        with sqlite3.connect(handle.snapshot.scheduler_db_path) as db:
            db.execute('UPDATE request_scheduler_policy SET limits_json=? WHERE platform=?',(json.dumps([15,3,1,12]),scope))
            db.execute('INSERT INTO request_scheduler_state(platform,next_allowed_at,cooldown_until,request_times_json,last_reason,updated_at) VALUES(?,?,?,?,?,?)',
                (scope,time.time()+10,time.time()+90,json.dumps([time.time()]),'paused',time.time()))
            before=db.execute('SELECT * FROM request_scheduler_state').fetchall()
            other=db.execute('SELECT platform,limits_json FROM request_scheduler_policy WHERE platform<>? ORDER BY platform',(scope,)).fetchall()
        handle.session._account_policy={'min_interval':9,'per_minute':6}
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _:authority.tighten_owner_account_policy(handle.session),range(4)))
        with sqlite3.connect(handle.snapshot.scheduler_db_path) as db:
            assert json.loads(db.execute('SELECT limits_json FROM request_scheduler_policy WHERE platform=?',(scope,)).fetchone()[0])==[15,3,1,12]
            assert db.execute('SELECT * FROM request_scheduler_state').fetchall()==before
            assert db.execute('SELECT platform,limits_json FROM request_scheduler_policy WHERE platform<>? ORDER BY platform',(scope,)).fetchall()==other
    finally:
        handle.close()


def test_direct_degraded_existing_service_exit_policy_only_tightens(tmp_path):
    from dataclasses import replace
    from tools.persistent_request_gate import RequestScheduler
    handle,c=prepare(tmp_path)
    try:
        initialize_gate(handle,c)
        account='dy:account:account';shared='dy:service_exit:198.51.100.7';exit_scope='dy:exit:198.51.100.7'
        direct=replace(handle.session.snapshot,route_mode='direct_degraded',observed_exit_ip='198.51.100.7',
            scope_ids=tuple(sorted(['dy',account,shared,exit_scope])))
        handle.session.snapshot=direct;handle.session.guard.snapshot=direct
        RequestScheduler(direct.scheduler_db_path,platform=shared,min_interval=2,per_minute=30,max_concurrency=2,media_interval=3)
        authority.tighten_owner_account_policy(handle.session)
        with sqlite3.connect(direct.scheduler_db_path) as db:
            rows=dict(db.execute('SELECT platform,limits_json FROM request_scheduler_policy'))
        baseline=json.loads(rows['dy'])
        assert json.loads(rows[shared])==[max(9,baseline[0]),min(6,baseline[1]),min(2,baseline[2]),max(3,baseline[3])]
        assert json.loads(rows[account])[0]>=9 and json.loads(rows[account])[1]<=6
    finally:
        handle.close()


def test_fresh_owner_gate_over_real_child_fd_without_child_database_access(tmp_path):
    import os
    import subprocess
    from dataclasses import asdict
    handle,c=prepare(tmp_path)
    script='''
import asyncio,json,sqlite3,sys,time
from tools.collection_route import BoundRoute
from tools.bound_proxy import BoundProxySession
snapshot=BoundRoute(**json.loads(sys.stdin.readline()))
def denied(*args,**kwargs): raise AssertionError('CHILD_DATABASE_ACCESS')
sqlite3.connect=denied
session=BoundProxySession(snapshot,echo_url='https://echo.invalid/ip')
session.context_id='context'
session.receipt=session.guard.confirm_pair('context','198.51.100.7','198.51.100.7')
session.budget.confirm_baseline(snapshot,'198.51.100.7')
gate=session._make_gate()
async def run():
 async with gate.slot('login_navigation',timeout=2): pass
asyncio.run(run())
print('OWNER_GATE_READY',flush=True)
'''
    process=subprocess.Popen([sys.executable,'-c',script],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,text=True,env={**os.environ,**handle.env,
            'MEDIACRAWLER_DY_BROWSER_ENGINE':'camoufox',
            'PYTHONPATH':os.pathsep.join((str(settings.media_crawler_dir),os.environ.get('PYTHONPATH','')))},
        pass_fds=handle.pass_fds)
    handle.bind_child(process.pid)
    try:
        out,err=process.communicate(json.dumps(asdict(handle.snapshot))+'\n',timeout=10)
        assert process.returncode==0,err
        assert out.strip()=='OWNER_GATE_READY'
        with sqlite3.connect(handle.snapshot.scheduler_db_path) as db:
            assert db.execute("SELECT COUNT(*) FROM request_scheduler_policy WHERE platform='dy'").fetchone()[0]==1
            assert db.execute('SELECT COUNT(*) FROM request_scheduler_leases').fetchone()[0]==0
    finally:
        handle.close()
        if process.poll() is None:
            process.kill();process.wait()
