"""Host-process safety fencing using real SQLite commits and inherited FD locks."""
import multiprocessing
import sqlite3
import time

import pytest

from backend.audit_agent.account_rotation import AccountRotationManager
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore


def account(store, name):
    value=store.create(platform='dy',display_name=name)
    return store.save_auth_state(value['id'],'fixture-ciphertext')


def admit_worker(path, account_id, barrier, queue):
    store=CrawlerAccountStore(path)
    barrier.wait()
    try:
        store.admit_route(account_id,'203.0.113.10',1,'concurrent-'+str(multiprocessing.current_process().pid))
        queue.put('admitted')
    except ValueError:
        queue.put('blocked')


def pause_worker(path, account_id, barrier, queue):
    store=CrawlerAccountStore(path)
    barrier.wait()
    store.pause_account_exit(account_id,until=time.time()+60)
    queue.put('paused')


def recovery_worker(path, account_id, barrier, queue):
    store=CrawlerAccountStore(path)
    barrier.wait()
    queue.put(bool(store.claim_recovery(account_id)))


@pytest.fixture
def safety(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'crawler_safety_enabled',True)
    store=CrawlerAccountStore(tmp_path/'safety.sqlite3')
    a,b=account(store,'a'),account(store,'b')
    store.observe_shared_exit('203.0.113.10',service_exit=True)
    for item in (a,b):
        store.set_route_state(item['id'],state='active',route_mode='direct_degraded',shared_exit_key='203.0.113.10',generation=1)
        store.mark_direct_validated(item['id'],1)
    return store,a,b


def test_actual_process_pause_admission_fence(safety):
    store,a,b=safety
    ctx=multiprocessing.get_context('spawn')
    barrier,queue=ctx.Barrier(3),ctx.Queue()
    processes=[ctx.Process(target=admit_worker,args=(store.db_path,b['id'],barrier,queue)),ctx.Process(target=pause_worker,args=(store.db_path,a['id'],barrier,queue))]
    for process in processes: process.start()
    barrier.wait()
    for process in processes:
        process.join(15)
        assert process.exitcode==0
    results=[queue.get(timeout=1) for _ in processes]
    assert 'paused' in results
    with store._connect() as db:
        pause_at=db.execute("SELECT created_at FROM crawler_route_events WHERE event_type='challenge_paused'").fetchone()[0]
        admissions=db.execute('SELECT created_at FROM crawler_route_admissions').fetchall()
        assert all(row[0]<=pause_at for row in admissions)
    # A reservation may serialize before the pause, never after it.
    with pytest.raises(ValueError,match='ACCOUNT_CHALLENGE'):
        store.admit_route(b['id'],'203.0.113.10',1,'after-committed-pause')
    assert store.available_accounts('dy')==[]
    observed=store.observe_shared_exit('203.0.113.11')
    c=account(store,'independent')
    store.set_route_state(c['id'],state='active',route_mode='direct_degraded',shared_exit_key='203.0.113.11',generation=observed['shared_exit_generation'])
    store.mark_direct_validated(c['id'],observed['shared_exit_generation'])
    assert store.admit_route(c['id'],'203.0.113.11',observed['shared_exit_generation'],'independent')


def test_separate_proxies_same_physical_exit_share_pause(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'crawler_safety_enabled',True)
    accounts=CrawlerAccountStore(tmp_path/'s.sqlite3')
    proxies=CrawlerProxyStore(accounts,cipher=AuthStateCipher(encryption_key='test'))
    resources=[proxies.create(display_name=str(i),endpoint='http://proxy.example:8080',expires_at=time.time()+3600) for i in range(2)]
    for resource in resources:
        proxies.observe_exit(resource['id'],'2001:db8:0:0::1')
    assert not proxies.shared_exit_admission_allowed(resources[1]['id'])
    assert proxies.shared_exit_admission_allowed(resources[1]['id'],observed_exit='2001:db8::1')
    proxies.pause_shared_exit(resources[0]['id'])
    assert not proxies.shared_exit_admission_allowed(resources[1]['id'],observed_exit='2001:db8::1')


def test_observation_ttl_generation_and_strict_inputs(safety):
    store,a,_=safety
    with pytest.raises(ValueError,match='GENERATION'):
        store.admit_route(a['id'],'203.0.113.10',True,'bool')
    with pytest.raises(ValueError): store.set_route_state(a['id'],state='active',paused_until=float('nan'))
    with pytest.raises(ValueError): store.set_route_state(a['id'],state='active',generation=0)
    with store._connect() as db:
        db.execute('UPDATE crawler_shared_exits SET observed_at=?',(time.time()-settings.crawler_exit_observation_ttl-1,))
    with pytest.raises(ValueError,match='ACCOUNT_CHALLENGE'):
        store.admit_route(a['id'],'203.0.113.10',1,'expired')


def test_fairness_uses_persisted_real_skip_counts(safety):
    store,a,b=safety
    for i in range(settings.crawler_fair_skip_limit):
        store.record_fair_selection(a['id'],[a['id'],b['id']])
    # Open another connection/store as the dispatcher would do.
    reopened=CrawlerAccountStore(store.db_path)
    assert reopened.select_fair('dy')['id']==b['id']
    assert reopened.get(b['id'])['skip_count']==0


def expired_incident(store,a):
    store.record_risk(a['id'])
    store.pause_account_exit(a['id'],until=time.time()+1)
    with store._connect() as db:
        db.execute('UPDATE crawler_accounts SET paused_until=? WHERE id=?',(time.time()-1,a['id']))
        db.execute('UPDATE crawler_shared_exits SET paused_until=?',(time.time()-1,))


def test_recovery_actual_process_claim_is_single(safety):
    store,a,_=safety
    expired_incident(store,a)
    ctx=multiprocessing.get_context('spawn')
    barrier,queue=ctx.Barrier(3),ctx.Queue()
    processes=[ctx.Process(target=recovery_worker,args=(store.db_path,a['id'],barrier,queue)) for _ in range(2)]
    for process in processes: process.start()
    barrier.wait()
    for process in processes:
        process.join(15)
        assert process.exitcode==0
    assert sorted(queue.get(timeout=1) for _ in processes)==[False,True]
    assert CrawlerAccountStore(store.db_path).claim_recovery(a['id']) is None


@pytest.mark.parametrize('result,expected',[(True,'success'),(False,'challenge'),(None,'uncertain')])
def test_recovery_one_bounded_operation_retains_risk(safety,result,expected):
    store,a,b=safety
    expired_incident(store,a)
    called=[]
    def check(account_id,claim):
        called.append(claim)
        assert store.admit_route(account_id,'203.0.113.10',1,'recovery-operation',recovery_claim=claim)
        return result
    manager=AccountRotationManager(store)
    assert manager.recover_once(a['id'],check)==expected
    assert manager.recover_once(a['id'],check) is None
    assert len(called)==1
    assert store.get(a['id'])['risk_count']==1
    if result is True:
        assert store.admit_route(b['id'],'203.0.113.10',1,'after-success')
    else:
        assert store.get(a['id'])['route_state']=='manual_required'
        with pytest.raises(ValueError): store.admit_route(b['id'],'203.0.113.10',1,'after-failed')


def test_secret_event_codes_never_persist(safety):
    store,a,_=safety
    for secret in ('http://user:password@host','Authorization: Bearer token','ACCOUNT_CHALLENGE\npassword=secret'):
        with pytest.raises(ValueError): store.record_route_event(a['id'],'challenge_paused',reason=secret)
    assert b'password' not in store.db_path.read_bytes()


def test_exit_capacity_child_fd_survives_parent_release(tmp_path,monkeypatch):
    import subprocess
    import sys
    from backend.task_admission.resources import ExecutionLeaseBundle
    monkeypatch.setattr(settings,'crawler_browser_profile_root',tmp_path/'profiles')
    monkeypatch.setattr(settings,'task_resource_lock_dir',tmp_path/'locks')
    monkeypatch.setattr(settings,'crawler_shared_exit_active_limit',1)
    first=ExecutionLeaseBundle('first','owner','a')
    second=ExecutionLeaseBundle('second','owner','b')
    assert first.acquire() and first.acquire_exit_capacity('2001:db8:0:0::1')
    child=subprocess.Popen([sys.executable,'-c','import sys; print("ready",flush=True); sys.stdin.read()'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,pass_fds=first.pass_fds)
    try:
        assert child.stdout.readline()==b'ready\n'
        first.close()
        assert second.acquire()
        assert not second.acquire_exit_capacity('2001:db8::1')
        child.communicate(input=b'',timeout=5)
        assert child.returncode==0
        assert second.acquire_exit_capacity('2001:db8::1')
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        second.close()


def test_failed_drain_retains_profile_and_admission_callback(tmp_path,monkeypatch):
    from backend.task_admission.resources import ExecutionLeaseBundle,acquire_account_handle
    monkeypatch.setattr(settings,'crawler_browser_profile_root',tmp_path/'profiles')
    bundle=ExecutionLeaseBundle('task','owner','a')
    assert bundle.acquire()
    released=[]
    bundle.add_closer(lambda:released.append(True))
    fail=[True]
    def drain():
        if fail[0]: raise RuntimeError('DRAIN_TIMEOUT')
    bundle.add_closer(drain)
    with pytest.raises(RuntimeError,match='DRAIN_TIMEOUT'): bundle.close()
    assert released==[]
    assert acquire_account_handle('dy','a') is None
    fail[0]=False
    bundle.close()
    assert released==[True]
    handle=acquire_account_handle('dy','a')
    assert handle is not None
    handle.close()


def test_configured_pool_binding_preferred_cannot_weaken_existing_required(tmp_path):
    accounts=CrawlerAccountStore(tmp_path/'pool.sqlite3')
    proxies=CrawlerProxyStore(accounts,cipher=AuthStateCipher(encryption_key='test'))
    resources=[proxies.create(display_name=str(i),endpoint='http://proxy.example:8080',expires_at=time.time()+3600) for i in range(3)]
    a,b,c=[account(accounts,str(i)) for i in range(3)]
    ids=[r['id'] for r in resources]
    resource=proxies.select_pool_resource(ids)
    bound=proxies.bind(a['id'],resource['id'],expected_binding_revision=0,route_mode='proxy_preferred')
    assert bound['route_mode']=='proxy_preferred' and not bound['proxy_required']
    next_resource=proxies.select_pool_resource(ids)
    assert next_resource['id']!=resource['id']
    strict=proxies.bind(b['id'],next_resource['id'],expected_binding_revision=0)
    assert strict['proxy_required']
    with pytest.raises(ValueError,match='IMMUTABLE'):
        proxies.bind(b['id'],resource['id'],expected_binding_revision=1,route_mode='proxy_preferred')


def test_initial_login_admission_explicit_bootstrap_only(tmp_path):
    store=CrawlerAccountStore(tmp_path/'login.sqlite3')
    value=store.create(platform='dy',display_name='login')
    store.observe_shared_exit('203.0.113.10')
    with pytest.raises(ValueError): store.admit_route(value['id'],'203.0.113.10',1,'normal')
    assert store.admit_route(value['id'],'203.0.113.10',1,'login',login_bootstrap=True)
    with pytest.raises(ValueError): store.admit_route(value['id'],'203.0.113.10',1,'bad-login',login_bootstrap=1)


def fairness_worker(path, selected, eligible, barrier, queue):
    store=CrawlerAccountStore(path)
    barrier.wait()
    store.record_fair_selection(selected,eligible)
    queue.put(True)


def test_fair_skip_updates_are_committed_across_processes(safety):
    store,a,b=safety
    ctx=multiprocessing.get_context('spawn')
    barrier,queue=ctx.Barrier(4),ctx.Queue()
    processes=[ctx.Process(target=fairness_worker,args=(store.db_path,a['id'],[a['id'],b['id']],barrier,queue)) for _ in range(3)]
    for process in processes: process.start()
    barrier.wait()
    for process in processes:
        process.join(15)
        assert process.exitcode==0
    assert all(queue.get(timeout=1) for _ in processes)
    assert CrawlerAccountStore(store.db_path).get(b['id'])['skip_count']==3
    assert CrawlerAccountStore(store.db_path).select_fair('dy')['id']==b['id']


def test_healthy_capacity_priority_uses_actual_exit_fds(safety,tmp_path,monkeypatch):
    from backend.task_admission.resources import ExecutionLeaseBundle
    store,a,b=safety
    monkeypatch.setattr(settings,'crawler_browser_profile_root',tmp_path/'profiles')
    monkeypatch.setattr(settings,'task_resource_lock_dir',tmp_path/'locks')
    manager=AccountRotationManager(store)
    assert manager.healthy_capacity_available('dy','owner',{a['id'],b['id']})
    busy=ExecutionLeaseBundle('busy','owner','other')
    assert busy.acquire() and busy.acquire_exit_capacity('203.0.113.10')
    try:
        assert not manager.healthy_capacity_available('dy','owner',{a['id'],b['id']})
    finally: busy.close()
    assert manager.healthy_capacity_available('dy','owner',{a['id'],b['id']})


def test_degraded_exit_cannot_use_unvalidated_or_newest_other_exit(safety):
    store,a,_=safety
    store.observe_shared_exit('203.0.113.11',service_exit=True)
    assert store.get_direct_route(a['id'])['shared_exit_key']=='203.0.113.10'
    with store._connect() as db:
        db.execute('UPDATE crawler_accounts SET validated_direct_generation=0 WHERE id=?',(a['id'],))
    assert a['id'] not in {v['id'] for v in store.available_accounts('dy')}
    with pytest.raises(ValueError,match='REVALIDATION'):
        store.admit_route(a['id'],'203.0.113.10',1,'unvalidated-direct')


def test_proxy_event_fixed_enum_rejects_arbitrary_machine_shaped_secrets(tmp_path):
    accounts=CrawlerAccountStore(tmp_path/'events.sqlite3')
    proxies=CrawlerProxyStore(accounts,cipher=AuthStateCipher(encryption_key='fixture'))
    resource=proxies.create(display_name='fixture',endpoint='http://proxy.example:8080',expires_at=time.time()+60)
    with pytest.raises(ValueError,match='EVENT'):
        proxies.record_event(resource['id'],'unavailable',code='SECRET_TOKEN_ABC')
    with pytest.raises(ValueError,match='EVENT'):
        proxies.set_health(resource['id'],status='unavailable',stop_reason='SECRET_TOKEN_ABC')
    assert b'SECRET_TOKEN_ABC' not in accounts.db_path.read_bytes()
