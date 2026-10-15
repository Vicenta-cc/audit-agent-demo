"""Migration login through the real manager, route admission and SQLite CAS.

Only browser-side profile creation, exit observation and platform login protocol
are substituted. No route bundle, claim, migration authorization or commit is
mocked, so the former migration/manual-recovery conflict remains observable.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest

from backend.application_auth.store import AuthStore
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_browser_migration import BrowserMigrationService, profile_key
from backend.audit_agent.crawler_login_operation_store import CrawlerLoginOperationStore
from backend.audit_agent.crawler_login_v2_manager import CrawlerLoginV2Manager
from backend.audit_agent import crawler_browser_migration, crawler_route
from test_crawler_login_v2_manager import HELPER, wait_until


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    crawler = Path(os.environ.get('MEDIACRAWLER_DIR') or os.environ.get('MEDIA_CRAWLER_DIR') or settings.media_crawler_dir)
    for name, value in {
        'media_crawler_dir': crawler,
        'crawler_browser_profile_root': tmp_path / 'logical',
        'crawler_camoufox_state_root': tmp_path / 'physical',
        'crawler_camoufox_archive_root': tmp_path / 'archive',
        'request_scheduler_db': tmp_path / 'scheduler.sqlite3',
        'task_resource_lock_dir': tmp_path / 'locks',
        'app_auth_db': tmp_path / 'owners.sqlite3',
        'app_auth_mode': 'required',
        'crawler_browser_maintenance': True,
        'crawler_safety_enabled': True,
        'crawler_proxy_enabled': False,
    }.items():
        monkeypatch.setattr(settings, name, value)
    store = CrawlerAccountStore(tmp_path / 'accounts.sqlite3')
    cipher = AuthStateCipher('migration-test-only')
    account = store.create(platform='dy', display_name='original', platform_account_id='original-dy')
    store.save_auth_state(account['id'], cipher.encrypt({'cookies': [{'name': 'legacy', 'value': 'never-import'}]}))
    # Represent the deployed Chromium account before applying the real migration.
    with store._connect() as db:
        db.execute("UPDATE crawler_accounts SET profile_engine='legacy',profile_generation='',auth_source_generation='' WHERE id=?", (account['id'],))
    owners = AuthStore(settings.app_auth_db)
    owner = owners.create_user(username='owner', password='owner password 123456', activation_mode='created_at')['id']
    other = owners.create_user(username='other', password='other password 123456', activation_mode='created_at')['id']
    owners.register_crawler_account(account['id'], owner)
    old = settings.crawler_browser_profile_root / profile_key(account['id'])
    old.mkdir(parents=True)
    (old / 'Cookies').write_text('legacy-profile')
    with sqlite3.connect(settings.request_scheduler_db) as db:
        db.execute('CREATE TABLE original_budget(balance INT)')
        db.execute('INSERT INTO original_budget VALUES(17)')

    def initialize(aid, *, root, generation):
        directory = root / profile_key(aid)
        directory.mkdir(parents=True)
        identity = {'fixed': generation}
        (directory / 'launch-identity.json').write_text(json.dumps(identity))
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        (directory / 'profile.json').write_text(json.dumps({
            'engine': 'camoufox', 'generation': generation, 'account_key': profile_key(aid), 'identity_hash': digest,
        }))
        return {'directory': str(directory), 'engine': 'camoufox', 'generation': generation}

    service = BrowserMigrationService(store, owner_db_path=settings.app_auth_db,
        logical_root=settings.crawler_browser_profile_root, state_root=settings.crawler_camoufox_state_root,
        archive_root=settings.crawler_camoufox_archive_root, scheduler_db_path=settings.request_scheduler_db,
        initializer=initialize, process_probe=lambda: '')
    row = service.plan(account['id'], actor_id=owner, dry_run=False)
    row = service.apply(row['migration_id'], actor_id=owner, dry_run=False)
    assert row['phase'] == 'LOGIN_REQUIRED'
    monkeypatch.setattr(crawler_browser_migration, 'get_browser_migration_service', lambda account_store=None: service)
    observed = store.observe_shared_exit('203.0.113.9', service_exit=True)
    probes = []

    def probe(bundle, **kwargs):
        probes.append(bundle.snapshot)
        return crawler_route.route_contract().ProbeReceipt(bundle.snapshot.proxy_id,
            bundle.snapshot.config_revision, 'offline-exit-probe', observed['observed_exit_ip'], time.time())

    monkeypatch.setattr(crawler_route, '_run_maintenance_probe', probe)
    helper = tmp_path / 'child_protocol.py'
    helper.write_text(HELPER.replace("'identity'", "'original-dy'"))
    operations = CrawlerLoginOperationStore(store, cipher, migration_service=service)
    manager = CrawlerLoginV2Manager(store, cipher, operation_store=operations,
        python_path=sys.executable, helper_path=helper, timeout_seconds=15)
    yield SimpleNamespace(store=store, service=service, manager=manager, account_id=account['id'],
                          row=row, owner=owner, other=other, cipher=cipher, probes=probes)
    manager.shutdown()


def start(item, *, actor=None, key='original-account-page', migration_id=None):
    return item.manager.start(item.store.get(item.account_id), user_id=actor or item.owner,
        app_session_id='app', owner_token='x' * 32, request_key=key, migration_id=migration_id)[0]


def recovery_fields(item):
    account = item.store.get(item.account_id)
    return {name: account[name] for name in ('recovery_state', 'recovery_claim', 'recovery_incident',
                                            'route_state', 'paused_until', 'pause_reason', 'risk_count')}


def test_original_page_auto_binds_migration_without_creating_manual_recovery(migrated):
    item = migrated
    before = recovery_fields(item)
    snapshot = start(item)  # Existing frontend sends no migration_id.
    session = item.manager.sessions[snapshot['id']]
    wait_until(lambda: session.snapshot['phase'] == 'sms_required')
    operation = item.manager.operations.get(snapshot['operation_id'])
    assert operation['migration_id'] == item.row['migration_id']
    assert operation['profile_generation'] == item.row['generation']
    assert item.service.status(item.row['migration_id'])['login_operation_id'] == operation['id']
    assert session.route_bundle.migration_id == item.row['migration_id']
    assert session.route_bundle.snapshot.managed
    assert session.route_bundle.snapshot.route_mode == 'legacy_direct'
    assert session.route_bundle.snapshot.playwright_proxy() is None
    assert not session.route_bundle.snapshot.recovery_claim
    assert session.manual_recovery_claim is None
    assert recovery_fields(item) == before
    with item.store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM crawler_manual_recovery_operations').fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM crawler_route_admissions WHERE account_id=? AND state='active'", (item.account_id,)).fetchone()[0] == 1
    with sqlite3.connect(settings.request_scheduler_db) as db:
        persisted = db.execute('SELECT account_id,owner_id,state,request_json FROM route_operations WHERE operation_id=?',
                              (session.route_bundle.snapshot.operation_id,)).fetchone()
    assert persisted[:3] == (item.account_id, item.owner, 'issued')
    assert json.loads(persisted[3])['snapshot_digest'] == session.route_bundle.snapshot.snapshot_digest
    # The request key remains idempotent even though its migration was inferred.
    assert start(item)['operation_id'] == operation['id']


def complete_scan(item):
    snapshot = start(item)
    session = item.manager.sessions[snapshot['id']]
    wait_until(lambda: session.snapshot['phase'] == 'sms_required')
    # Exercise real manager ticket/epoch authorization and media-channel commit.
    ticket = item.manager.ticket(session.id, user_id=item.owner, app_session_id='app', owner_token='x' * 32)
    epoch, _ = item.manager.attach(session.id, ticket['ticket'], user_id=item.owner, app_session_id='app', controller=object())
    item.manager.wait_controller(session.id, epoch)
    current = item.manager.get(session.id, user_id=item.owner, app_session_id='app', owner_token='x' * 32)
    item.manager.command(session.id, {
        'type': 'action', 'command_id': 'scan-result', 'operation_id': session.operation_id,
        'controller_epoch': epoch, 'sequence': 1, 'expected_state_revision': current['state_revision'],
        'action': 'submit_sms', 'payload': {'code': '123456'},
    }, user_id=item.owner, app_session_id='app', owner_token='x' * 32)
    wait_until(lambda: session.snapshot['phase'] in {'done', 'failed'})
    assert session.snapshot['phase'] == 'done', session.snapshot['error']
    wait_until(session.cleanup_done.is_set)
    return session


def test_scan_commit_reaches_identity_verified_through_manager_and_real_route(migrated):
    item = migrated
    before = recovery_fields(item)
    session = complete_scan(item)
    row = item.service.status(item.row['migration_id'])
    assert row['phase'] == 'IDENTITY_VERIFIED'
    assert item.manager.operations.get(session.operation_id)['stage'] == 'committed'
    account = item.store.get(item.account_id)
    assert account['platform_account_id'] == 'original-dy'
    assert account['profile_generation'] == account['auth_source_generation'] == item.row['generation']
    assert item.cipher.decrypt(item.store.get_auth_state_ciphertext(item.account_id)) == {'cookies': []}
    assert recovery_fields(item) == before


@pytest.mark.parametrize('explicit', [False, True])
def test_wrong_owner_cannot_auto_bind_or_explicitly_claim_migration(migrated, explicit):
    item = migrated
    before = recovery_fields(item)
    with pytest.raises(ValueError, match='MIGRATION_FORBIDDEN'):
        start(item, actor=item.other, migration_id=item.row['migration_id'] if explicit else None)
    assert not item.manager.sessions
    assert item.service.status(item.row['migration_id'])['login_operation_id'] == ''
    assert recovery_fields(item) == before
    assert not item.manager.operations.list()


@pytest.mark.parametrize('committed', [False, True])
def test_fake_or_committed_migration_cannot_exempt_maintenance(migrated, committed):
    item = migrated
    migration_id = 'not-a-migration'
    if committed:
        complete_scan(item)
        # Drive the real service to its terminal phase with a substituted owner
        # browser receipt, then attempt to reuse that completed authority.
        row = item.service.status(item.row['migration_id'])
        item.service.verify_route(row['migration_id'], actor_id=item.owner, receipt={
            'browser_exit': '203.0.113.9', 'http_exit': '203.0.113.9', 'expected_exit': '203.0.113.9',
            'restart_verified': True, 'authority_verified': True, 'identity_hash': row['identity_hash'],
            'generation': row['generation'], 'binding_revision': row['binding_revision'], 'config_revision': row['config_revision'],
        })
        assert item.service.commit(row['migration_id'], actor_id=item.owner)['phase'] == 'COMMITTED'
        migration_id = item.row['migration_id']
    sessions_before = set(item.manager.sessions)
    operations_before = item.manager.operations.list()
    with pytest.raises(ValueError, match='MIGRATION_LOGIN_NOT_READY' if committed else 'MIGRATION_NOT_FOUND'):
        start(item, key='fresh-after-migration', migration_id=migration_id)
    assert set(item.manager.sessions) == sessions_before
    assert recovery_fields(item)['recovery_claim'] == ''
    assert item.manager.operations.list() == operations_before
