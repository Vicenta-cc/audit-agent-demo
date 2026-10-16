from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading
import time

import pytest

from backend.audit_agent.account_rotation import AccountRotationManager
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_adapter import CrawlerVerificationError
from backend.audit_agent.crawler_browser_migration import profile_key


@pytest.fixture
def pool(monkeypatch):
    from test_crawler_browser_migration import MigrationTest
    fixture = MigrationTest()
    fixture.setUp()
    monkeypatch.setattr(settings,'crawler_browser_maintenance',False)
    monkeypatch.setattr(settings,'crawler_proxy_enabled',False)
    monkeypatch.setattr(settings,'crawler_safety_enabled',False)
    try:
        fixture.store.save_auth_state(fixture.aid,'old-browser-ciphertext')
        pending=fixture.applied()
        healthy=fixture.store.create(platform='dy',display_name='committed',platform_account_id='healthy-dy')
        import sqlite3
        with sqlite3.connect(fixture.owner_db) as db:
            db.execute('INSERT INTO crawler_account_owners VALUES(?,?)',(healthy['id'],'owner'))
        old=fixture.root/'logical'/profile_key(healthy['id'])
        old.mkdir(parents=True)
        row=fixture.service.plan(healthy['id'],actor_id='owner',dry_run=False)
        row=fixture.service.apply(row['migration_id'],actor_id='owner',dry_run=False)
        fixture.service.begin_login(row['migration_id'],actor_id='owner',operation_id='healthy-scan')
        cipher=AuthStateCipher('pool-fixture')
        row=fixture.service.verify_login(row['migration_id'],actor_id='owner',operation_id='healthy-scan',
            platform_account_id='healthy-dy',ciphertext=cipher.encrypt({'cookies':[]}),
            expected_auth_revision=row['expected_auth_revision'],generation=row['generation'])
        fixture.service.verify_route(row['migration_id'],actor_id='owner',receipt=fixture.route(row))
        fixture.service.commit(row['migration_id'],actor_id='owner')
        stale=fixture.store.create(platform='dy',display_name='stale')
        fixture.store.save_auth_state(stale['id'],cipher.encrypt({'cookies':[]}))
        with fixture.store._connect() as db:
            db.execute("UPDATE crawler_accounts SET auth_source_generation='old-generation' WHERE id=?",(stale['id'],))
        yield fixture, healthy, stale, pending, cipher
    finally:
        fixture.doCleanups()


def test_rotation_selects_committed_account_instead_of_pending_or_old_generation(pool):
    fixture, healthy, stale, pending, cipher=pool
    pending_account=fixture.store.get(fixture.aid)
    assert pending_account['status']=='active' and pending_account['has_auth_state']
    assert fixture.store.available_accounts('dy')[0]['id']==healthy['id']
    assert fixture.store.next_available('dy')['id']==healthy['id']
    # A newly verified identity is still ineligible until migration commit.
    fixture.service.begin_login(pending['migration_id'],actor_id='owner',operation_id='pending-scan')
    fixture.service.verify_login(pending['migration_id'],actor_id='owner',operation_id='pending-scan',
        platform_account_id='original-dy',ciphertext=cipher.encrypt({'cookies':[]}),
        expected_auth_revision=pending['expected_auth_revision'],generation=pending['generation'])
    assert {a['id'] for a in fixture.store.available_accounts('dy')} == {healthy['id']}
    initial=fixture.store.create(platform='dy',display_name='initial-healthy')
    fixture.store.save_auth_state(initial['id'],cipher.encrypt({'cookies':[]}))
    with fixture.store._connect() as db:
        db.execute('UPDATE crawler_accounts SET auth_source_generation=profile_generation WHERE id=?',(initial['id'],))
    called=[]
    def runner(account_id,state):
        called.append(account_id)
        if account_id==initial['id']:raise CrawlerVerificationError('verify')
        return account_id
    manager=AccountRotationManager(fixture.store,cipher)
    assert manager.run_with_rotation('dy',initial['id'],{},runner)==healthy['id']
    assert called==[initial['id'],healthy['id']]
    assert stale['id'] not in {a['id'] for a in fixture.store.available_accounts('dy')}


def test_maintenance_blocks_douyin_pool_only(pool,monkeypatch):
    fixture,*_=pool
    other=fixture.store.create(platform='xhs',display_name='other-platform')
    fixture.store.save_auth_state(other['id'],'other-platform-ciphertext')
    monkeypatch.setattr(settings,'crawler_browser_maintenance',True)
    assert fixture.store.available_accounts('dy')==[]
    assert [a['id'] for a in fixture.store.available_accounts('xhs')]==[other['id']]


def test_cooldown_and_pause_never_shorten_or_enable_disabled_account(tmp_path):
    store=CrawlerAccountStore(tmp_path/'accounts.sqlite3')
    account=store.create(platform='dy',display_name='disabled')
    store.update(account['id'],status='disabled')
    store.mark_cooldown(account['id'],'long risk','2099-01-01',failure_kind='verify')
    store.mark_cooldown(account['id'],'shorter',datetime.now().isoformat(),failure_kind='rate_limit')
    value=store.get(account['id'])
    assert value['status']=='disabled' and value['failure_kind']=='verify' and value['cooldown_until']=='2099-01-01'
    long=time.time()+9000
    store.pause_account_exit(account['id'],until=long)
    store.set_route_state(account['id'],state='manual_required',paused_until=long,pause_reason='RECOVERY_UNCERTAIN')
    store.pause_account_exit(account['id'],until=time.time()+60)
    value=store.get(account['id'])
    assert value['paused_until']==long and value['status']=='disabled' and value['route_state']=='manual_required'


def test_independent_store_transactions_preserve_max_concurrent_pause_and_cooldown(tmp_path):
    path=tmp_path/'accounts.sqlite3'
    first,second=CrawlerAccountStore(path),CrawlerAccountStore(path)
    account=first.create(platform='dy',display_name='concurrent')
    barrier=threading.Barrier(2)
    long=time.time()+9000
    untils=[(first,long,'2099-01-01'),(second,time.time()+60,'2098-01-01')]
    def pause(args):
        store,until,cooldown=args
        barrier.wait()
        store.mark_cooldown(account['id'],'risk',cooldown,failure_kind='verify')
        store.pause_account_exit(account['id'],until=until)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(pause,untils))
    value=first.get(account['id'])
    assert value['cooldown_until']=='2099-01-01'
    assert value['paused_until']==long


@pytest.mark.parametrize('barrier_kind',['maintenance','migration'])
@pytest.mark.parametrize('claim_kind',['recovery','refresh'])
def test_blocked_recovery_and_refresh_claims_do_not_consume_attempt(pool, monkeypatch, barrier_kind, claim_kind):
    fixture, healthy, _, pending, _=pool
    store=fixture.store
    account_id=fixture.aid if barrier_kind=='migration' else healthy['id']
    monkeypatch.setattr(settings,'crawler_safety_enabled',True)
    monkeypatch.setattr(settings,'crawler_browser_maintenance',barrier_kind=='maintenance')
    if claim_kind=='recovery':
        store.pause_account_exit(account_id,until=time.time()+60)
        with store._connect() as db:
            db.execute('UPDATE crawler_accounts SET paused_until=? WHERE id=?',(time.time()-1,account_id))
    else:
        from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
        proxies=CrawlerProxyStore(store)
        resource=proxies.create(display_name='refresh fixture',endpoint='http://127.0.0.1:18080',expires_at=time.time()+3600)
        account=store.get(account_id)
        bound=proxies.bind(account_id,resource['id'],expected_binding_revision=account['binding_revision'])
        proxies.observe_exit(resource['id'],'203.0.113.55')
        proxies.mark_route_login_validated(account_id,expected_binding_revision=bound['binding_revision'],expected_config_revision=resource['config_revision'])
        with store._connect() as db:
            old=time.time()-settings.crawler_exit_observation_ttl-10
            db.execute('UPDATE proxy_resources SET observed_exit_at=? WHERE id=?',(old,resource['id']))
            db.execute('UPDATE crawler_shared_exits SET observed_at=? WHERE exit_key=?',(old,'203.0.113.55'))
    before=store.get(account_id)
    with store._connect() as db:
        events=db.execute('SELECT COUNT(*) FROM crawler_route_events').fetchone()[0]
        refreshes=db.execute('SELECT COUNT(*) FROM crawler_exit_refreshes').fetchone()[0]
    if claim_kind=='recovery':
        assert store.claim_recovery(account_id) is None
        calls=[]
        assert AccountRotationManager(store).recover_once(account_id,lambda *args:calls.append(args)) is None
        assert not calls
    else:
        assert store.claim_exit_refresh(account_id) is None
        from backend.audit_agent import crawler_route
        calls=[]
        monkeypatch.setattr(crawler_route,'refresh_existing_route',lambda *args,**kwargs:calls.append(args))
        AccountRotationManager(store).refresh_authorized('dy','owner',{account_id})
        assert not calls
    assert store.get(account_id)==before
    with store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM crawler_route_events').fetchone()[0]==events
        assert db.execute('SELECT COUNT(*) FROM crawler_exit_refreshes').fetchone()[0]==refreshes
    if barrier_kind=='migration':
        assert healthy['id'] in {a['id'] for a in store.available_accounts('dy')}
