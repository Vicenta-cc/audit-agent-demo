"""Only one consumed media grant on its private FD can renew its capacity."""
import hashlib
import json
import os
import time
from uuid import uuid4

import pytest

from backend.audit_agent import crawler_request_authority as authority
from tests.test_crawler_authority_policy import prepare,initialize_gate


@pytest.fixture
def owner(tmp_path,monkeypatch):
    from backend.audit_agent.config import settings
    for name,value in dict(request_min_interval=0,requests_per_minute=1000,
        request_concurrency=10,media_request_interval=0).items():monkeypatch.setattr(settings,name,value)
    handle,c=prepare(tmp_path)
    handle.options['account_policy']={'min_interval':0,'per_minute':1000}
    initialize_gate(handle,c)
    handle.pid=os.getpid();handle.start_identity=authority.process_identity(os.getpid())
    yield handle
    handle.close()


def reserve(owner,operation='media',consume=True):
    payload={'operation':operation,'lease_seconds':90}
    request=dict(channel=owner.channel,request_id=uuid4().hex,pid=owner.pid,start_identity=owner.start_identity,
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest())
    result=owner._execute('reserve',payload,request)
    assert result['delay']==0
    if consume:owner._execute('consume',{'grant':result['grant']},{})
    return result


def test_owner_renews_only_exact_consumed_media_group_and_binds_real_child_identity(owner,monkeypatch):
    from tools import persistent_request_gate
    calls=[]
    def renew(db,tokens,**kwargs):
        calls.append((db,tokens,kwargs));return {'expires_at':time.time()+90}
    monkeypatch.setattr(persistent_request_gate,'renew_scope_tokens',renew)
    media=reserve(owner)
    result=owner._execute('renew_request',{'scope_tokens':media['scope_tokens']},{})
    assert result['expires_at']>time.time()+89
    assert calls==[(owner.snapshot.scheduler_db_path,media['scope_tokens'],
        {'pid':owner.pid,'start_identity':owner.start_identity,'safety_fence':owner.session._safety_fence()})]
    owner._execute('release',{'scope_tokens':media['scope_tokens']},{})
    with pytest.raises(RuntimeError,match='PROXY_REVISION_MISMATCH'):
        owner._execute('renew_request',{'scope_tokens':media['scope_tokens']},{})
    assert len(calls)==1


@pytest.mark.parametrize('kind',['api','unconsumed','partial','mixed','extra','empty','missing'])
def test_owner_rejects_unprivileged_or_mixed_token_groups_before_shared_renew(owner,monkeypatch,kind):
    from tools import persistent_request_gate
    monkeypatch.setattr(persistent_request_gate,'renew_scope_tokens',lambda *a,**k:pytest.fail('unauthorized renewal reached DB'))
    first=reserve(owner,'api' if kind=='api' else 'media',consume=kind!='unconsumed')
    tokens=dict(first['scope_tokens'])
    payload={'scope_tokens':tokens}
    if kind=='partial':tokens.pop(next(iter(tokens)))
    elif kind=='mixed':
        second=reserve(owner)
        scope=next(iter(tokens));tokens[scope]=second['scope_tokens'][scope]
    elif kind=='extra':payload['ttl']=3600
    elif kind=='empty':payload={'scope_tokens':{}}
    elif kind=='missing':payload={}
    with pytest.raises(RuntimeError,match='PROXY_REVISION_MISMATCH'):
        owner._execute('renew_request',payload,{})


def test_cross_fd_group_and_maintenance_capability_cannot_renew(owner,monkeypatch):
    from tools import persistent_request_gate
    monkeypatch.setattr(persistent_request_gate,'renew_scope_tokens',lambda *a,**k:pytest.fail('unauthorized renewal reached DB'))
    first=reserve(owner)
    other=object.__new__(authority.RequestAuthority)
    other.session=owner.session;other.media_grants={}
    with pytest.raises(RuntimeError,match='PROXY_REVISION_MISMATCH'):
        other._execute('renew_request',{'scope_tokens':first['scope_tokens']},{})
    owner.session.maintenance_only=True
    with pytest.raises(RuntimeError,match='PROXY_UNAVAILABLE'):
        owner._execute('renew_request',{'scope_tokens':first['scope_tokens']},{})


def test_real_shared_renewal_extends_same_tokens_without_redebit_or_extending_consume_deadline(owner):
    import sqlite3
    first=reserve(owner)
    with sqlite3.connect(owner.snapshot.scheduler_db_path) as db:
        counts=db.execute('SELECT platform,request_times_json,next_allowed_at,cooldown_until FROM request_scheduler_state ORDER BY platform').fetchall()
        grant=db.execute('SELECT expires_at,consumed FROM crawler_authority_ledger WHERE grant=?',(first['grant'],)).fetchone()
    result=owner._execute('renew_request',{'scope_tokens':first['scope_tokens']},{})
    with sqlite3.connect(owner.snapshot.scheduler_db_path) as db:
        assert db.execute('SELECT platform,request_times_json,next_allowed_at,cooldown_until FROM request_scheduler_state ORDER BY platform').fetchall()==counts
        assert db.execute('SELECT expires_at,consumed FROM crawler_authority_ledger WHERE grant=?',(first['grant'],)).fetchone()==grant
        leases=db.execute('SELECT platform,token,expires_at FROM request_scheduler_leases').fetchall()
    assert {scope:token for scope,token,_ in leases}==first['scope_tokens']
    assert all(expires==result['expires_at'] for _,_,expires in leases)
    assert time.time()+89<result['expires_at']<=time.time()+90


RENEW_CHILD = '''
import json,sqlite3,sys,time
from tools.collection_route import BoundRoute
from tools.request_authority import AuthorityClient
snapshot=BoundRoute(**json.loads(sys.stdin.readline()))
def denied(*args,**kwargs):raise AssertionError('CHILD_DATABASE_ACCESS')
sqlite3.connect=denied
client=AuthorityClient(snapshot)
client.call('initialize')
client.call('probe_confirm',{'ip':'198.51.100.7'})
client.call('make_gate',{'receipt':dict(proxy_id=snapshot.proxy_id,config_revision=snapshot.config_revision,
 context_id='context',ip='198.51.100.7',observed_at=time.time(),exit_generation=snapshot.exit_generation)})
result=client.call('reserve',{'operation':'media','lease_seconds':90})
assert result['delay']==0
client.call('consume',{'grant':result['grant']})
print(json.dumps({'ready':True,'tokens':result['scope_tokens']}),flush=True)
for line in sys.stdin:
 command=json.loads(line)
 try:
  answer=client.call(command['operation'],{'scope_tokens':result['scope_tokens']},request_id=command.get('request_id'))
  print(json.dumps({'ok':True,'result':answer}),flush=True)
 except Exception as exc:
  print(json.dumps({'ok':False,'error':str(exc)}),flush=True)
'''


@pytest.fixture
def real_worker(tmp_path,monkeypatch):
    import subprocess
    import sys
    from dataclasses import asdict
    from backend.audit_agent.config import settings
    for name,value in dict(request_min_interval=0,requests_per_minute=1000,
        request_concurrency=1,media_request_interval=0).items():monkeypatch.setattr(settings,name,value)
    handle,_=prepare(tmp_path)
    handle.options['account_policy']={'min_interval':0,'per_minute':1000}
    process=subprocess.Popen([sys.executable,'-c',RENEW_CHILD],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        env={**os.environ,**handle.env,'MEDIACRAWLER_DY_BROWSER_ENGINE':'camoufox',
             'PYTHONPATH':os.pathsep.join((str(settings.media_crawler_dir),os.environ.get('PYTHONPATH','')))},
        text=True,pass_fds=handle.pass_fds)
    handle.bind_child(process.pid)
    process.stdin.write(json.dumps(asdict(handle.snapshot))+'\n');process.stdin.flush()
    line=process.stdout.readline()
    assert line,process.stderr.read()
    ready=json.loads(line)
    def call(operation,request_id=None):
        process.stdin.write(json.dumps(dict(operation=operation,request_id=request_id))+'\n');process.stdin.flush()
        return json.loads(process.stdout.readline())
    yield handle,process,ready['tokens'],call
    handle.close()
    if process.poll() is None:process.kill()
    process.wait(timeout=3)
    process.stdin.close();process.stdout.close();process.stderr.close()


def test_real_private_fd_renewal_is_idempotent_and_release_is_final(real_worker):
    import sqlite3
    handle,process,tokens,call=real_worker
    first=call('renew_request','a'*32)
    assert first['ok'] and first['result']['expires_at']>time.time()+89
    assert call('renew_request','a'*32)==first
    assert call('release')['ok']
    assert not call('renew_request')['ok']
    with sqlite3.connect(handle.snapshot.scheduler_db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM request_scheduler_leases').fetchone()[0]==0
        assert all(len(json.loads(row[0]))==1 for row in db.execute('SELECT request_times_json FROM request_scheduler_state'))


def test_real_pause_racing_renewal_stops_next_renewal_without_releasing_live_child_capacity(real_worker):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from tools.persistent_request_gate import enter_cooldown_scopes
    import sqlite3
    handle,process,tokens,call=real_worker
    barrier=Barrier(2)
    def renew():
        barrier.wait();return call('renew_request')
    def pause():
        barrier.wait();enter_cooldown_scopes(handle.snapshot.scheduler_db_path,list(tokens),'HTTP429',90)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(renew);paused=pool.submit(pause)
        pending.result();paused.result()
    assert not call('renew_request')['ok']
    with sqlite3.connect(handle.snapshot.scheduler_db_path) as db:
        assert {row[0] for row in db.execute('SELECT pid FROM request_scheduler_leases')}=={process.pid}
    assert call('release')['ok']


def test_real_owner_eof_prevents_renewal_but_retains_live_child_lease_until_process_death(real_worker):
    import sqlite3
    from tools.persistent_request_gate import _try_reserve_scopes
    handle,process,tokens,call=real_worker
    handle.close()
    assert not call('renew_request')['ok']
    with sqlite3.connect(handle.snapshot.scheduler_db_path) as db:
        assert {row[0] for row in db.execute('SELECT pid FROM request_scheduler_leases')}=={process.pid}
    process.kill();process.wait(timeout=3)
    assert _try_reserve_scopes(handle.snapshot.scheduler_db_path,list(tokens),'media',90,time.time())['delay']==0


def test_request_renewal_does_not_reset_explicit_trial_operation_or_media_budgets(owner):
    import sqlite3
    from tools.trial_limits import SharedTrialLimits
    owner.session.trial_limits=SharedTrialLimits(owner.snapshot.scheduler_db_path,'renewal-trial')
    owner.session.trial_limits.owner_pid=owner.pid
    owner._execute('trial_operation',{}, {})
    trial_token=owner._execute('trial_begin_media',{}, {})
    owner._execute('trial_media_bytes',{'token':trial_token,'size':1024},{})
    before=owner.session.trial_limits.media_timeout(trial_token)
    media=reserve(owner)
    with sqlite3.connect(owner.snapshot.scheduler_db_path) as db:
        counters=db.execute('SELECT * FROM proxy_trial_limits').fetchall()
        timers=db.execute('SELECT * FROM proxy_trial_media').fetchall()
    owner._execute('renew_request',{'scope_tokens':media['scope_tokens']},{})
    with sqlite3.connect(owner.snapshot.scheduler_db_path) as db:
        assert db.execute('SELECT * FROM proxy_trial_limits').fetchall()==counters
        assert db.execute('SELECT * FROM proxy_trial_media').fetchall()==timers
    assert owner.session.trial_limits.media_timeout(trial_token)<=before
    owner._execute('release',{'scope_tokens':media['scope_tokens']},{})
    owner._execute('trial_finish_media',{'token':trial_token},{})
