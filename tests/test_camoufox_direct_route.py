import json
import os
import subprocess
import sqlite3
import time
from pathlib import Path

import pytest

from backend.audit_agent import crawler_route as app
from backend.audit_agent.crawler_account_store import CrawlerAccountStore


@pytest.fixture
def direct(tmp_path, monkeypatch):
    monkeypatch.setattr(app.settings, 'crawler_proxy_enabled', False)
    monkeypatch.setattr(app.settings, 'crawler_safety_enabled', True)
    monkeypatch.setattr(app.settings, 'app_auth_mode', 'local')
    monkeypatch.setattr(app.settings, 'media_crawler_dir', Path(os.environ.get('MEDIACRAWLER_DIR', str(Path(__file__).resolve().parents[1] / 'external' / 'MediaCrawler'))))
    monkeypatch.setattr(app.settings, 'request_scheduler_db', tmp_path / 'scheduler.sqlite3')
    monkeypatch.setattr(app.settings, 'crawler_browser_profile_root', tmp_path / 'logical')
    monkeypatch.setattr(app.settings, 'crawler_camoufox_state_root', tmp_path / 'physical')
    store = CrawlerAccountStore(tmp_path / 'accounts.sqlite3')
    account = store.create(platform='dy', display_name='direct')
    return store, account


def test_no_proxy_account_gets_explicit_direct_template(direct):
    store, account = direct
    route = app.resolve_account_route(account['id'], account_store=store)
    assert route.route_mode == 'legacy_direct'
    assert route.expires_at is None
    assert route.playwright_proxy() is None and route.httpx_proxy() is None
    assert not route.managed
    assert route.safety_db_path == ''  # authority is assigned only after lease acquisition
    assert route.account_id == account['id']
    restored = app.route_contract().validate_route_envelope(json.loads(app.route_contract().serialize_route(route)), account_id=account['id'])
    assert restored.route_mode == 'legacy_direct'


def test_verified_direct_probe_retires_only_matching_transport_generation(direct, monkeypatch):
    from dataclasses import replace
    store, account = direct
    contract = app.route_contract()
    from tools.bound_proxy import route_health_key
    monkeypatch.setattr(app, '_run_maintenance_probe', lambda bundle: contract.ProbeReceipt(
        'service-direct', 1, 'probe', '203.0.113.9', time.time()))
    bundle = app.acquire_route_execution(account['id'], 'probe-health', 'owner', account_store=store,
        allow_probe=True)
    try:
        snapshot = bundle.snapshot
        matching = route_health_key(snapshot)
        other = route_health_key(replace(snapshot, exit_generation=snapshot.exit_generation + 1))
        with sqlite3.connect(snapshot.scheduler_db_path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS collection_route_health(proxy_id TEXT, config_revision INTEGER, stop_code TEXT, PRIMARY KEY(proxy_id,config_revision))')
            db.executemany('INSERT OR REPLACE INTO collection_route_health VALUES(?,?,?)', [
                (matching, snapshot.config_revision, 'PROXY_TRANSPORT_FAILED'),
                (other, snapshot.config_revision, 'PROXY_TRANSPORT_FAILED')])
        receipt = contract.ProbeReceipt(snapshot.proxy_id, snapshot.config_revision,
            snapshot.context_id, snapshot.observed_exit_ip, time.time(), snapshot.exit_generation)
        app._retire_verified_probe_uncertainty(bundle, receipt)
        with sqlite3.connect(snapshot.scheduler_db_path) as db:
            assert db.execute('SELECT proxy_id FROM collection_route_health').fetchall() == [(other,)]
            db.execute('INSERT INTO collection_route_health VALUES(?,?,?)',
                       (matching, snapshot.config_revision, 'ACCOUNT_CHALLENGE'))
        app._retire_verified_probe_uncertainty(bundle, receipt)
        with sqlite3.connect(snapshot.scheduler_db_path) as db:
            assert db.execute('SELECT stop_code FROM collection_route_health WHERE proxy_id=?', (matching,)).fetchone() == ('ACCOUNT_CHALLENGE',)
    finally:
        bundle.close()


@pytest.mark.parametrize('login', [True, False])
def test_direct_login_and_collection_are_managed_after_real_account_admission(direct, monkeypatch, login):
    store, account = direct
    if not login:
        store.save_auth_state(account['id'], 'fixture-encrypted-state')
    c = app.route_contract()
    monkeypatch.setattr(app, '_run_maintenance_probe', lambda bundle: c.ProbeReceipt('service-direct',1,'probe','203.0.113.9',time.time()))
    bundle = app.acquire_route_execution(account['id'], 'login' if login else 'crawl', 'owner',
        account_store=store, allow_probe=login, human_login_operation_id='manual' if login else None)
    try:
        snap = bundle.snapshot
        assert snap.managed and snap.route_mode == 'legacy_direct'
        assert snap.safety_db_path == str(store.db_path.resolve())
        assert snap.observed_exit_ip == '203.0.113.9'
        assert 'dy:service_exit:203.0.113.9' in snap.scope_ids
        assert snap.playwright_proxy() is None and snap.httpx_proxy() is None
        assert not store.get(account['id'])['proxy_id']
        restored = c.validate_route_envelope(json.loads(c.serialize_route(snap)), account_id=account['id'])
        assert restored.snapshot_digest == snap.snapshot_digest
    finally:
        bundle.close()


def test_firefox_process_lock_blocks_migration(direct):
    _, account = direct
    directory = app.settings.crawler_camoufox_state_root / 'profiles' / app.route_contract().derive_profile_key(account['id'])
    directory.mkdir(parents=True)
    lock = directory / '.parentlock'
    child = subprocess.Popen([os.sys.executable, '-c', 'import fcntl,sys,time; f=open(sys.argv[1],"w"); fcntl.lockf(f,fcntl.LOCK_EX); print("locked",flush=True); time.sleep(30)',str(lock)], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'locked'
        with pytest.raises(app.CollectionRouteError):
            app.assert_profile_browser_stopped(account['id'])
    finally:
        child.terminate(); child.wait()
    app.assert_profile_browser_stopped(account['id'])


def test_direct_managed_provenance_reuses_same_live_route(direct, monkeypatch):
    store, account = direct
    store.save_auth_state(account['id'], 'fixture-encrypted-state')
    c = app.route_contract()
    monkeypatch.setattr(app, '_run_maintenance_probe', lambda bundle: c.ProbeReceipt('service-direct',1,'probe','203.0.113.9',time.time()))
    bundle = app.acquire_route_execution(account['id'], 'crawl', 'owner', account_store=store)
    try:
        snap = bundle.snapshot
        receipt = c.ProbeReceipt(snap.proxy_id, snap.config_revision, snap.context_id,
            snap.observed_exit_ip, snap.observed_exit_at, snap.exit_generation)
        provenance = c.collection_route_provenance(snap, receipt, observation_id='observed',
            source_task_id='task', source_batch_id='batch', collected_at=time.time(), context_id=snap.context_id)
        assert provenance['route_mode'] == 'legacy_direct'
        with app.authorize_source_route(provenance, 'owner', 'crawl', route_context=bundle, account_store=store) as same:
            assert same is bundle
    finally:
        bundle.close()


def test_live_direct_task_renews_past_twenty_minutes_then_rejects_disabled_account(direct, monkeypatch):
    from backend.audit_agent.crawler_request_authority import parent_authority_renew_ticket

    accounts, account = direct
    accounts.save_auth_state(account['id'], 'fixture-encrypted-state')
    clock = [time.time()]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    ip = '203.0.113.9'
    accounts.observe_shared_exit(ip, service_exit=True)
    bundle = app.acquire_route_execution(account['id'], 'crawl', 'owner', account_store=accounts)
    try:
        snap = bundle.snapshot
        store = bundle.route_operation_store
        assert snap.expires_at is None
        row = store.ack(snap.operation_id, owner_id=snap.owner_id, lease_version=1,
            snapshot_digest=snap.snapshot_digest, context_id=snap.context_id)
        assert row['request']['proxy_expires_at'] == 0
        ticket = row['ack']
        for version in range(2, 12):
            clock[0] += 275
            accounts.observe_shared_exit(ip, service_exit=True)
            ticket = parent_authority_renew_ticket(bundle, ticket)
            assert ticket['lease_version'] == version
            assert ticket['expires_at'] == clock[0] + 300
        with accounts._connect() as db:
            db.execute("UPDATE crawler_accounts SET status='disabled' WHERE id=?", (account['id'],))
        with pytest.raises(ValueError, match='ACCOUNT_CHALLENGE'):
            parent_authority_renew_ticket(bundle, ticket)
        assert store.ticket(snap.operation_id, owner_id=snap.owner_id,
            snapshot_digest=snap.snapshot_digest, context_id=snap.context_id) == ticket
    finally:
        bundle.close()


def test_direct_secondary_phase_issues_short_ticket_and_refuses_revoked_owner(direct, monkeypatch, tmp_path):
    from backend.application_auth.store import AuthStore
    from backend.audit_agent.crawler_request_authority import parent_authority_renew_ticket

    accounts, account = direct
    accounts.save_auth_state(account['id'], 'fixture-encrypted-state')
    accounts.observe_shared_exit('203.0.113.9', service_exit=True)
    monkeypatch.setattr(app.settings, 'app_auth_mode', 'required')
    monkeypatch.setattr(app.settings, 'app_auth_db', tmp_path/'auth.db')
    auth = AuthStore(app.settings.app_auth_db)
    owner = auth.create_user(username='route-owner', password='long-fixture-password', activation_mode='created_at')['id']
    auth.register_crawler_account(account['id'], owner)
    bundle = app.acquire_route_execution(account['id'], 'crawl', owner, account_store=accounts)
    try:
        prior = bundle.snapshot.operation_id
        app.renew_same_route_phase(bundle)
        snap = bundle.snapshot
        assert snap.operation_id != prior and snap.expires_at is None
        row = bundle.route_operation_store.ack(snap.operation_id, owner_id=owner, lease_version=1,
            snapshot_digest=snap.snapshot_digest, context_id=snap.context_id)
        assert row['request']['proxy_expires_at'] == 0 and 0 < row['ack']['ttl'] <= 300
        auth.unregister_crawler_account(account['id'])
        with pytest.raises(app.CollectionRouteError, match='PROXY_UNAVAILABLE'):
            parent_authority_renew_ticket(bundle, row['ack'])
    finally:
        bundle.close()


def test_bound_proxy_renewal_honors_provider_expiry_and_stop(direct, monkeypatch):
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from backend.audit_agent.crawler_request_authority import parent_authority_renew_ticket

    accounts, account = direct
    monkeypatch.setattr(app.settings, 'crawler_proxy_enabled', True)
    accounts.save_auth_state(account['id'], 'fixture-encrypted-state')
    clock = [time.time()]
    monkeypatch.setattr(time, 'time', lambda: clock[0])
    expiry = clock[0] + 420
    proxies = CrawlerProxyStore(accounts)
    resource = proxies.create(display_name='fixture', endpoint='http://127.0.0.1:9', expires_at=expiry)
    bound = proxies.bind(account['id'], resource['id'], expected_binding_revision=account['binding_revision'])
    proxies.observe_exit(resource['id'], '203.0.113.9')
    proxies.mark_route_login_validated(account['id'], expected_binding_revision=bound['binding_revision'],
        expected_config_revision=resource['config_revision'])
    bundle = app.acquire_route_execution(account['id'], 'crawl', 'owner', account_store=accounts)
    try:
        snap = bundle.snapshot
        row = bundle.route_operation_store.ack(snap.operation_id, owner_id='owner', lease_version=1,
            snapshot_digest=snap.snapshot_digest, context_id=snap.context_id)
        clock[0] += 275
        ticket = parent_authority_renew_ticket(bundle, row['ack'])
        assert row['request']['proxy_expires_at'] == expiry == ticket['expires_at']
        proxies.set_health(resource['id'], status='unavailable', stop_reason='PROXY_TRANSPORT_FAILED')
        with pytest.raises(app.CollectionRouteError):
            parent_authority_renew_ticket(bundle, ticket)
    finally:
        bundle.close()


def test_degraded_direct_renewal_rejects_changed_binding(direct, monkeypatch):
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from backend.audit_agent.crawler_request_authority import parent_authority_renew_ticket

    accounts, account = direct
    monkeypatch.setattr(app.settings, 'crawler_proxy_enabled', True)
    monkeypatch.setattr(app.settings, 'crawler_direct_degrade_enabled', True)
    accounts.save_auth_state(account['id'], 'fixture-encrypted-state')
    proxies = CrawlerProxyStore(accounts)
    resource = proxies.create(display_name='fixture', endpoint='http://127.0.0.1:9', expires_at=time.time()+1800)
    proxies.bind(account['id'], resource['id'], expected_binding_revision=account['binding_revision'],
        route_mode='proxy_preferred')
    ip = '203.0.113.9'
    exit_state = accounts.observe_shared_exit(ip, service_exit=True)
    generation = exit_state['shared_exit_generation']
    accounts.set_route_state(account['id'], state='active', route_mode='direct_degraded',
        shared_exit_key=ip, generation=generation)
    accounts.mark_direct_validated(account['id'], generation)
    bundle = app.acquire_route_execution(account['id'], 'crawl', 'owner', account_store=accounts)
    try:
        snap = bundle.snapshot
        assert snap.route_mode == 'direct_degraded' and snap.expires_at is None
        row = bundle.route_operation_store.ack(snap.operation_id, owner_id='owner', lease_version=1,
            snapshot_digest=snap.snapshot_digest, context_id=snap.context_id)
        with accounts._connect() as db:
            db.execute('UPDATE crawler_accounts SET binding_revision=binding_revision+1 WHERE id=?', (account['id'],))
        with pytest.raises(app.CollectionRouteError, match='PROXY_REVISION_MISMATCH'):
            parent_authority_renew_ticket(bundle, row['ack'])
    finally:
        bundle.close()
