import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from backend.audit_agent import crawler_route as route
from backend.audit_agent import crawler_browser_migration as migration
from backend.audit_agent.crawler_browser_verification import verify_migrated_account


@pytest.fixture
def migrated(monkeypatch):
    from test_crawler_browser_migration import MigrationTest
    fixture = MigrationTest()
    fixture.setUp()
    try:
        row = fixture.logged_in()
        service = fixture.service
        monkeypatch.setattr(migration, 'get_browser_migration_service', lambda store=None: service)
        monkeypatch.setattr(route.settings, 'request_scheduler_db', fixture.scheduler)
        monkeypatch.setattr(route.settings, 'crawler_browser_profile_root', fixture.root / 'logical')
        monkeypatch.setattr(route.settings, 'crawler_camoufox_state_root', fixture.root / 'state')
        monkeypatch.setattr(route.settings, 'media_crawler_dir', Path(__file__).resolve().parents[2] / 'wukong-proxy-crawler')
        monkeypatch.setattr(route.settings, 'crawler_safety_enabled', True)
        monkeypatch.setattr(route.settings, 'crawler_proxy_enabled', False)
        monkeypatch.setattr(route.settings, 'crawler_browser_maintenance', True)
        monkeypatch.setattr(route.settings, 'app_auth_mode', 'local')
        yield fixture, row
    finally:
        fixture.doCleanups()


def observations(monkeypatch, *, outcome='success', altered_exit=False):
    children = []
    def probe(bundle, **options):
        assert "timeout" not in options, "migration must use the parent login-check deadline policy"
        snap = bundle.snapshot
        ip = '203.0.113.9'
        if not options.get('check_login'):
            return route.route_contract().ProbeReceipt(snap.proxy_id, snap.config_revision,'bootstrap',ip,time.time(),snap.exit_generation)
        # The actual browser/paired-echo boundary is controlled here; subprocess
        # startup and termination are real, with no platform credentials used.
        process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(.05)'])
        from backend.audit_agent.crawler_request_authority import process_identity
        identity = process_identity(process.pid)
        process.wait(timeout=3)
        children.append(process)
        return {'login_result':outcome, 'child_pid':process.pid,'process_start':identity,
                'shutdown_verified':True,'authority_verified':True,
                'receipt':route.route_contract().ProbeReceipt(snap.proxy_id,snap.config_revision,
                    snap.context_id,'203.0.113.10' if altered_exit else ip,time.time(),snap.exit_generation)}
    monkeypatch.setattr(route,'_run_maintenance_probe',probe)
    return children


def test_owner_observations_close_two_processes_and_commit_idempotently(migrated, monkeypatch):
    fixture, row = migrated
    children = observations(monkeypatch)
    result = verify_migrated_account(row['migration_id'], actor_id='owner',service=fixture.service)
    assert result['phase'] == 'COMMITTED'
    assert len(children) == 2 and all(child.returncode == 0 for child in children)
    again = verify_migrated_account(row['migration_id'], actor_id='owner', service=fixture.service)
    assert again['phase'] == 'COMMITTED' and len(children) == 2


@pytest.mark.parametrize('outcome', ['challenge','uncertain'])
def test_failed_identity_check_keeps_migration_fence(migrated, monkeypatch, outcome):
    fixture, row = migrated
    observations(monkeypatch,outcome=outcome)
    with pytest.raises(migration.MigrationError, match='MIGRATION_LOGIN_'):
        verify_migrated_account(row['migration_id'],actor_id='owner',service=fixture.service)
    assert fixture.service.status(row['migration_id'])['phase'] == 'IDENTITY_VERIFIED'
    with pytest.raises(migration.MigrationError, match='MIGRATION_BLOCKED'):
        fixture.service.guard(fixture.aid)


def test_changed_exit_and_wrong_owner_never_commit(migrated, monkeypatch):
    fixture, row = migrated
    children = observations(monkeypatch,altered_exit=True)
    with pytest.raises(migration.MigrationError):
        verify_migrated_account(row['migration_id'],actor_id='other',service=fixture.service)
    assert not children
    with pytest.raises(route.route_contract().CollectionRouteError, match='PROXY_EXIT_CHANGED'):
        verify_migrated_account(row['migration_id'],actor_id='owner',service=fixture.service)
    assert fixture.service.status(row['migration_id'])['phase'] == 'IDENTITY_VERIFIED'
