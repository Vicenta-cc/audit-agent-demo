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
    # A reservation may serialize before the pause, never after it.
    with pytest.raises(ValueError,match='ACCOUNT_CHALLENGE'):
        store.admit_route(b['id'],'203.0.113.10',1,'after-committed-pause')
    assert store.available_accounts('dy')==[]
    store.observe_shared_exit('203.0.113.11')
    c=account(store,'independent')
    store.set_route_state(c['id'],state='active',route_mode='direct_degraded',shared_exit_key='203.0.113.11',generation=1)
    assert store.admit_route(c['id'],'203.0.113.11',1,'independent')


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
