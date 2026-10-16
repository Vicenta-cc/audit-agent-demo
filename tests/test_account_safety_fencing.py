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
from crawler_auth_fixtures import mark_camoufox_login_verified


def account(store, name):
    value=store.create(platform='dy',display_name=name)
    store.save_auth_state(value['id'],'fixture-ciphertext')
    return mark_camoufox_login_verified(store, value['id'])


def test_initial_human_login_can_retry_cancelled_claim_and_freeze_trusted_identity(tmp_path):
    store=CrawlerAccountStore(tmp_path/'safety.sqlite3')
    a=store.create(platform='dy',display_name='first-human-login')
    observation=store.observe_shared_exit('203.0.113.10',service_exit=True)
    generation=observation['shared_exit_generation']
    first=store.begin_manual_recovery(a['id'],'first',owner_id='owner',allow_active=True,observed_exit='203.0.113.10',generation=generation)
    assert first and not store.finish_manual_recovery(a['id'],'first',first,'cancelled',owner_id='owner')
    assert store.get(a['id'])['route_state']=='manual_required'
    second=store.begin_manual_recovery(a['id'],'second',owner_id='owner')
    assert second
    with store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        store.save_douyin_auth_state(a['id'],'trusted-fixture-ciphertext','trusted_new_identity',connection=db)
        assert store.finish_manual_recovery(a['id'],'second',second,'success',owner_id='owner',verified_identity='trusted_new_identity',connection=db)
    assert store.get(a['id'])['route_state']=='active'
    with store._connect() as db:
        assert db.execute("SELECT expected_identity FROM crawler_manual_recovery_operations WHERE login_operation_id='second'").fetchone()[0]=='trusted_new_identity'
    store.pause_account_exit(a['id'],until=time.time()+60)
    third=store.begin_manual_recovery(a['id'],'third',owner_id='owner')
    with store._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        store.save_douyin_auth_state(a['id'],'wrong-fixture-ciphertext','different_identity',connection=db)
        assert not store.finish_manual_recovery(a['id'],'third',third,'success',owner_id='owner',verified_identity='different_identity',connection=db)
    assert store.get(a['id'])['platform_account_id']=='trusted_new_identity'


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
    monkeypatch.setattr(settings,'crawler_direct_degrade_enabled',True)
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


def test_early_child_verification_receipt_does_not_duplicate_incident(safety,monkeypatch):
    from backend.audit_agent import pipeline as pipeline_module
    from backend.audit_agent.crawler_adapter import CrawlerVerificationError
    store,a,_=safety
    manager=AccountRotationManager(store)
    manager.cool_down_after_risk(a['id'])
    before=store.get(a['id'])
    error=CrawlerVerificationError('fixture verification')
    error.account_risk_recorded=True
    monkeypatch.setattr(pipeline_module,'crawler_account_store',store)
    monkeypatch.setattr(pipeline_module.job_store,'log',lambda *args,**kwargs:None)
    pipeline=pipeline_module.AuditPipeline.__new__(pipeline_module.AuditPipeline)
    pipeline.job_id='fixture'
    pipeline._retire_rotation_account(a,error)
    after=store.get(a['id'])
    assert after['risk_count']==before['risk_count']==1
    assert after['recovery_incident']==before['recovery_incident']


def test_expired_direct_recovery_metadata_is_probe_only_never_admission(safety):
    store,a,_=safety
    expired_incident(store,a)
    claim=store.claim_recovery(a['id'])
    with store._connect() as db:
        db.execute('UPDATE crawler_shared_exits SET observed_at=?',(time.time()-settings.crawler_exit_observation_ttl-1,))
    assert store.get_direct_route(a['id']) is None
    assert store.get_direct_route(a['id'],allow_stale_recovery=True)['shared_exit_key']=='203.0.113.10'
    with pytest.raises(ValueError):
        store.admit_route(a['id'],'203.0.113.10',1,'stale-recovery',recovery_claim=claim)
    store.observe_shared_exit('203.0.113.10',service_exit=True)
    assert store.admit_route(a['id'],'203.0.113.10',1,'fresh-recovery',recovery_claim=claim)


def test_admission_rechecks_auth_and_cooldown_after_candidate_read(safety):
    from datetime import datetime,timedelta
    store,a,_=safety
    assert a['id'] in {value['id'] for value in store.available_accounts('dy')}
    store.mark_cooldown(a['id'],'empty_search',(datetime.now()+timedelta(minutes=1)).isoformat(),failure_kind='empty_search')
    with pytest.raises(ValueError,match='ACCOUNT_CHALLENGE'):
        store.admit_route(a['id'],'203.0.113.10',1,'cooled-after-read')
    with store._connect() as db:
        db.execute('UPDATE crawler_accounts SET cooldown_until=NULL,auth_state_ciphertext=NULL WHERE id=?',(a['id'],))
    with pytest.raises(ValueError,match='AUTH_INVALID'):
        store.admit_route(a['id'],'203.0.113.10',1,'auth-after-read')


def test_unbound_account_is_not_healthy_capacity_under_proxy_required_config(tmp_path,monkeypatch):
    store=CrawlerAccountStore(tmp_path/'unbound.sqlite3')
    value=account(store,'unbound')
    # Global proxy support does not make every account proxy-required.
    with store._connect() as db:
        db.execute('UPDATE crawler_accounts SET proxy_required=1 WHERE id=?', (value['id'],))
    monkeypatch.setattr(settings,'crawler_proxy_enabled',True)
    monkeypatch.setattr(settings,'crawler_safety_enabled',True)
    assert store.available_accounts('dy')==[]
    assert not AccountRotationManager(store).healthy_capacity_available('dy','owner',{value['id']})


def test_generation_is_local_to_exit_and_cross_key_change_is_drain_fenced(safety):
    store,a,_=safety
    store.observe_shared_exit('203.0.113.20')
    with store._connect() as db:
        db.execute('UPDATE crawler_shared_exits SET generation=10 WHERE exit_key=?',('203.0.113.20',))
    store.set_route_state(a['id'],state='active',route_mode='proxy_preferred',shared_exit_key='203.0.113.20',generation=10)
    with pytest.raises(ValueError,match='GENERATION_STALE'):
        store.set_route_state(a['id'],state='active',generation=9)
    store.admit_route(a['id'],'203.0.113.20',10,'old-proxy-route')
    with pytest.raises(ValueError,match='ROUTE_BUSY'):
        store.set_route_state(a['id'],state='active',route_mode='direct_degraded',shared_exit_key='203.0.113.10',generation=1)
    store.release_route_admission('old-proxy-route')
    value=store.set_route_state(a['id'],state='active',route_mode='direct_degraded',shared_exit_key='203.0.113.10',generation=1)
    assert value['shared_exit_generation']==1
    with pytest.raises(ValueError,match='EXIT_INVALID'):
        store.set_route_state(a['id'],state='active',shared_exit_key='203.0.113.99',generation=1)
    with store._connect() as db:
        db.execute('UPDATE crawler_shared_exits SET observed_at=? WHERE exit_key=?',(time.time()-settings.crawler_exit_observation_ttl-1,'203.0.113.20'))
    with pytest.raises(ValueError,match='EXIT_INVALID'):
        store.set_route_state(a['id'],state='active',shared_exit_key='203.0.113.20',generation=10)


def test_dispatch_echo_refresh_repairs_idle_direct_ttl_once_without_query_io(safety,monkeypatch):
    from backend.audit_agent import crawler_route
    store,a,_=safety
    with store._connect() as db:
        db.execute('UPDATE crawler_shared_exits SET observed_at=?',(time.time()-settings.crawler_exit_observation_ttl-1,))
    calls=[]
    def echo(account_id,**kwargs):
        with store._connect() as db:
            assert db.execute('SELECT state FROM crawler_exit_refreshes WHERE account_id=?',(account_id,)).fetchone()[0]=='claimed'
        calls.append(account_id)
        store.observe_shared_exit('203.0.113.10',service_exit=True)
        return True
    monkeypatch.setattr(crawler_route,'refresh_existing_route',echo)
    assert store.available_accounts('dy')==[] and calls==[]
    manager=AccountRotationManager(store)
    manager.refresh_authorized('dy','owner',{a['id']})
    assert calls==[a['id']]
    assert store.admit_route(a['id'],'203.0.113.10',1,'refreshed-idle')
    store.release_route_admission('refreshed-idle')
    manager.refresh_authorized('dy','owner',{a['id']})
    assert calls==[a['id']]


def test_same_exit_refresh_does_not_forge_another_proxy_sample_ttl(tmp_path,monkeypatch):
    from backend.audit_agent import crawler_route
    monkeypatch.setattr(settings,'crawler_safety_enabled',True)
    accounts=CrawlerAccountStore(tmp_path/'stale-proxies.sqlite3')
    proxies=CrawlerProxyStore(accounts,cipher=AuthStateCipher(encryption_key='fixture'))
    values=[account(accounts,str(i)) for i in range(2)]
    resources=[proxies.create(display_name=str(i),endpoint='http://proxy.example:8080',expires_at=time.time()+3600) for i in range(2)]
    for value,resource in zip(values,resources):
        proxies.bind(value['id'],resource['id'],expected_binding_revision=0)
        proxies.observe_exit(resource['id'],'203.0.113.10')
        proxies.mark_route_login_validated(value['id'],expected_binding_revision=1,expected_config_revision=1)
    with accounts._connect() as db:
        db.execute('UPDATE proxy_resources SET observed_exit_at=?',(time.time()-settings.crawler_exit_observation_ttl-1,))
        db.execute('UPDATE crawler_shared_exits SET observed_at=?',(time.time()-settings.crawler_exit_observation_ttl-1,))
    calls=[]
    def echo(account_id,**kwargs):
        calls.append(account_id)
        proxies.observe_exit(accounts.get(account_id)['proxy_id'],'203.0.113.10')
        return True
    monkeypatch.setattr(crawler_route,'refresh_existing_route',echo)
    assert accounts.available_accounts('dy')==[]
    AccountRotationManager(accounts).refresh_authorized('dy','owner',{item['id'] for item in values})
    assert set(calls)=={item['id'] for item in values} and len(calls)==2
    assert len(accounts.available_accounts('dy'))==2


def test_disabled_downgrade_does_not_project_degraded_account_as_available(safety,monkeypatch):
    store,a,_=safety
    assert a['id'] in {value['id'] for value in store.available_accounts('dy')}
    monkeypatch.setattr(settings,'crawler_direct_degrade_enabled',False)
    assert store.available_accounts('dy')==[]


@pytest.mark.parametrize('code,required,expected', [('PROXY_AUTH_FAILED',False,True),('AUTH_INVALID',False,False),('PROXY_AUTH_FAILED',True,False)])
def test_confirmed_proxy_407_candidate_does_not_include_platform_auth_or_strict_accounts(tmp_path,monkeypatch,code,required,expected):
    from backend.audit_agent import crawler_route
    from backend.task_admission.resources import acquire_account_handle
    monkeypatch.setattr(settings,'crawler_safety_enabled',True)
    monkeypatch.setattr(settings,'crawler_direct_degrade_enabled',True)
    monkeypatch.setattr(settings,'crawler_browser_profile_root',tmp_path/'profiles')
    monkeypatch.setattr(settings,'task_resource_lock_dir',tmp_path/'locks')
    accounts=CrawlerAccountStore(tmp_path/'407.sqlite3')
    proxies=CrawlerProxyStore(accounts,cipher=AuthStateCipher(encryption_key='fixture'))
    value=account(accounts,'fixture')
    proxy=proxies.create(display_name='supplier',endpoint='http://proxy.example:8080',expires_at=time.time()+3600)
    proxies.bind(value['id'],proxy['id'],expected_binding_revision=0,route_mode='proxy_required' if required else 'proxy_preferred')
    proxies.observe_exit(proxy['id'],'203.0.113.10')
    proxies.mark_route_login_validated(value['id'],expected_binding_revision=1,expected_config_revision=1)
    proxies.set_health(proxy['id'],status='unavailable',stop_reason=code)
    direct=accounts.observe_shared_exit('203.0.113.11',service_exit=True)
    calls=[]
    def prepare(account_id,error,exhausted,**kwargs):
        assert error=='PROXY_AUTH_FAILED' and exhausted is True
        assert kwargs['old_context_drained'] is True and kwargs['owner_id']=='owner'
        assert acquire_account_handle('dy',account_id) is None
        calls.append(error)
        accounts.set_route_state(account_id,state='active',route_mode='direct_degraded',shared_exit_key=direct['shared_exit_key'],generation=direct['shared_exit_generation'])
        accounts.mark_direct_validated(account_id,direct['shared_exit_generation'])
        return True
    monkeypatch.setattr(crawler_route,'prepare_direct_degraded',prepare)
    result=AccountRotationManager(accounts).prepare_exhausted_pool('dy','owner',{value['id']})
    assert bool(result) is expected
    assert calls==(['PROXY_AUTH_FAILED'] if expected else [])


def test_legacy_confirmed_proxy_observation_warms_canonical_after_additive_upgrade(tmp_path,monkeypatch):
    from backend.audit_agent import crawler_route
    monkeypatch.setattr(settings,'crawler_safety_enabled',True)
    accounts=CrawlerAccountStore(tmp_path/'legacy.sqlite3')
    proxies=CrawlerProxyStore(accounts,cipher=AuthStateCipher(encryption_key='fixture'))
    value=account(accounts,'fixture')
    proxy=proxies.create(display_name='legacy',endpoint='http://proxy.example:8080',expires_at=time.time()+3600)
    proxies.bind(value['id'],proxy['id'],expected_binding_revision=0)
    proxies.observe_exit(proxy['id'],'203.0.113.10')
    proxies.mark_route_login_validated(value['id'],expected_binding_revision=1,expected_config_revision=1)
    with accounts._connect() as db: db.execute('DELETE FROM crawler_shared_exits')
    assert accounts.available_accounts('dy')==[]
    with pytest.raises(ValueError): accounts.admit_route(value['id'],'203.0.113.10',1,'legacy-unobserved-canonical')
    calls=[]
    def echo(account_id,**kwargs):
        calls.append(account_id)
        proxies.observe_exit(proxy['id'],'203.0.113.10')
        return True
    monkeypatch.setattr(crawler_route,'refresh_existing_route',echo)
    AccountRotationManager(accounts).refresh_authorized('dy','owner',{value['id']})
    assert calls==[value['id']]
    assert accounts.available_accounts('dy')[0]['id']==value['id']
