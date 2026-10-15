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
    monkeypatch.setattr(app.settings, 'media_crawler_dir', Path(__file__).resolve().parents[2] / 'wukong-proxy-crawler')
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
