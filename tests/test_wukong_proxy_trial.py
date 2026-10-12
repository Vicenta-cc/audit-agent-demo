import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('trial_kit', ROOT / 'scripts/wukong_proxy_trial.py')
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


@pytest.fixture
def inputs(tmp_path):
    app, crawler = tmp_path / 'app', tmp_path / 'crawler'
    (app / 'backend/audit_agent').mkdir(parents=True)
    (app / 'backend/audit_agent/crawler_route.py').write_text('validate_route_envelope')
    (crawler / 'tools').mkdir(parents=True)
    (crawler / 'tools/collection_route.py').write_text('SCHEMA_VERSION = 1')
    binary = tmp_path / 'binary'
    binary.write_text('fixture')
    binary.chmod(0o700)
    db = tmp_path / 'scheduler.sqlite3'
    with sqlite3.connect(db) as c:
        c.executescript("CREATE TABLE request_scheduler_policy(platform TEXT,limits_json TEXT); CREATE TABLE request_scheduler_state(platform TEXT,cooldown_until REAL); INSERT INTO request_scheduler_policy VALUES('dy','[2,30,1,5]'); INSERT INTO request_scheduler_state VALUES('dy',0);")
    return dict(app_root=app, crawler_root=crawler, trial_root=tmp_path / 'trial',
        trial_id='fixture', scheduler_db=db, app_python=binary, crawler_python=binary,
        browser_binary=binary, now=100)


def test_default_dry_run_is_read_only_and_waiting_resource(inputs):
    before = inputs['scheduler_db'].read_bytes()
    result = kit.prepare(**inputs)
    assert result['status'] == 'waiting_resource' and not result['trial_ready']
    assert result['decision'] == 'NO-GO' and not result['offline_ready']
    assert 'FIXED_HTTPS_ECHO_URL_REQUIRED' in result['blockers']
    assert 'RUNTIME_LIMITS_NOT_PROVEN' in result['blockers']
    assert not inputs['trial_root'].exists()
    assert inputs['scheduler_db'].read_bytes() == before
    assert result['paths']['scheduler_db'] == str(inputs['scheduler_db'])
    assert result['limits']['echo_total'] == 96
    assert result['runtime_environment']['CRAWLER_PROXY_ENABLED'] == 'true'
    assert result['runtime_environment']['CRAWLER_PROXY_TRIAL_ID'] == 'fixture'
    assert result['runtime_environment']['CRAWLER_PROXY_TRIAL_JOB_IDS'] == ''


def test_authorized_write_private_directory_never_overwrites(inputs):
    with pytest.raises(ValueError):
        kit.prepare(**inputs, write=True)
    result = kit.prepare(**inputs, write=True, dedicated_test_account=True)
    root = Path(result['paths']['trial_directory'])
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / 'manifest.json').stat().st_mode & 0o777 == 0o600
    assert not (root / 'data/audit_index.sqlite3').exists()
    with pytest.raises(ValueError):
        kit.prepare(**inputs, write=True, dedicated_test_account=True)


def test_missing_runtime_controls_skips_and_cooldown_are_no_go(inputs, tmp_path):
    path = tmp_path / 'evidence.json'
    evidence = dict(contract_version=1, browser_started=True, tls_ca_loaded=True,
        app_root=str(inputs['app_root']), crawler_root=str(inputs['crawler_root']),
        app_revision='fixture', crawler_revision='fixture', mandatory_suites=[
            dict(collected=1, passed=0, skipped=1, exit_code=0, test_nodes=['fixture'])])
    path.write_text(json.dumps(evidence))
    with sqlite3.connect(inputs['scheduler_db']) as c:
        c.execute('UPDATE request_scheduler_state SET cooldown_until=200')
    result = kit.prepare(**inputs, evidence_path=path, echo_url='https://echo.example/ip', dedicated_test_account=True)
    assert 'GLOBAL_DY_COOLDOWN_ACTIVE' in result['blockers']
    assert 'MANDATORY_SUITE_NOT_PROVEN' in result['blockers']
    assert 'RUNTIME_LIMITS_NOT_PROVEN' in result['blockers']


def test_secret_metadata_rejected_and_help_no_db_side_effect(inputs, tmp_path):
    resource = tmp_path / 'resource.json'
    resource.write_text(json.dumps({'password': 'canary-secret'}))
    with pytest.raises(ValueError, match='METADATA_ONLY'):
        kit.prepare(**inputs, resource_path=resource)
    storage = tmp_path / 'must-not-exist'
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/wukong_proxy_trial.py'), '--help'],
        env={**os.environ, 'XHS_AUDIT_DATA_DIR': str(storage)}, capture_output=True, text=True)
    assert result.returncode == 0 and not storage.exists()
    assert 'canary-secret' not in result.stdout


def test_additive_migration_auth_history_and_rollback_fail_closed(tmp_path, monkeypatch):
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
    from backend.audit_agent import crawler_route
    db = tmp_path / 'old.sqlite3'
    cipher = AuthStateCipher(encryption_key='trial-fixture-key')
    ciphertext = cipher.encrypt({'cookies': [{'name': 'fixture', 'value': 'auth-canary'}]})
    with sqlite3.connect(db) as c:
        c.executescript('''CREATE TABLE crawler_accounts(id TEXT PRIMARY KEY,platform TEXT NOT NULL,
            display_name TEXT NOT NULL,platform_account_id TEXT,status TEXT NOT NULL,
            last_validated_at TEXT,last_used_at TEXT,last_error TEXT,auth_state_ciphertext TEXT,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE request_gate(platform TEXT PRIMARY KEY,next_at REAL,times TEXT);
            INSERT INTO request_gate VALUES('dy',200,'[100,101]');''')
        c.execute("INSERT INTO crawler_accounts VALUES('old','dy','old',NULL,'active',NULL,NULL,NULL,?,'old','old')", (ciphertext,))
    accounts = CrawlerAccountStore(db)
    proxies = CrawlerProxyStore(accounts, cipher=cipher)
    resource = proxies.create(display_name='fixture', endpoint='http://fixture.example:8080')
    proxies.bind('old', resource['id'], expected_binding_revision=0)
    before = accounts.get('old')
    CrawlerProxyStore(CrawlerAccountStore(db), cipher=cipher)
    assert accounts.get('old') == before
    with sqlite3.connect(db) as c:
        saved_ciphertext = c.execute("SELECT auth_state_ciphertext FROM crawler_accounts WHERE id='old'").fetchone()[0]
    assert cipher.decrypt(saved_ciphertext)['cookies'][0]['value'] == 'auth-canary'
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT next_at,times FROM request_gate').fetchone() == (200, '[100,101]')
    original_profile = tmp_path / 'original-profile'
    original_profile.mkdir()
    seed = original_profile / 'identity'
    seed.write_bytes(b'unchanged-seed')
    monkeypatch.setattr(crawler_route, 'get_proxy_store', lambda *args: proxies)
    monkeypatch.setattr(crawler_route.settings, 'crawler_proxy_enabled', False)
    with pytest.raises(crawler_route.CollectionRouteError, match='PROXY_UNAVAILABLE'):
        crawler_route.resolve_account_route('old', account_store=accounts)
    # Maintenance before rollback keeps required accounts blocked even after flag-off.
    proxies.set_health(resource['id'], status='maintenance', stop_reason='ROLLBACK_MAINTENANCE')
    assert accounts.available_accounts('dy') == []
    with pytest.raises(crawler_route.CollectionRouteError):
        crawler_route.resolve_account_route('old', account_store=accounts)
    busy = kit.rollback_precheck(account_db=db, account_ids=['old'], proxy_ids=[resource['id']],
        active_pids=[os.getpid()], process_inventory_complete=True)
    assert 'ROLLBACK_ACTIVE_PROCESS' in busy['blockers']
    unknown = kit.rollback_precheck(account_db=db, account_ids=['old'], proxy_ids=[resource['id']], active_pids=[])
    assert 'ROLLBACK_SCOPE_OR_PROCESS_EVIDENCE_MISSING' in unknown['blockers']
    bounded = kit.rollback_precheck(account_db=db, account_ids=['old'], proxy_ids=[resource['id']],
        active_pids=[], process_inventory_complete=True)
    assert bounded['decision'] == 'MAINTENANCE_LEASE_GATE_PENDING' and not bounded['rollback_ready']
    assert seed.read_bytes() == b'unchanged-seed'
    assert accounts.get('old')['proxy_required']


def test_route_operation_transition_preserves_terminal_state(tmp_path):
    from backend.audit_agent.crawler_route import RouteOperation, RouteOperationStore

    operation = RouteOperation.create(
        owner_id='owner-1', account_id='account-1', profile_key='profile-1',
        route_mode='proxy_required', expires_at=time.time() + 300,
    )
    store = RouteOperationStore(tmp_path / 'route-operations.sqlite3')

    assert store.issue(operation)['state'] == 'issued'
    assert store.ack(operation.operation_id, owner_id='owner-1', lease_version=1)['state'] == 'acked'
    assert store.transition(operation.operation_id, state='draining', owner_id='owner-1', lease_version=1)['state'] == 'draining'
    assert store.transition(operation.operation_id, state='ended', owner_id='owner-1', lease_version=1)['state'] == 'ended'
    assert store.transition(operation.operation_id, state='issued', owner_id='owner-1', lease_version=1)['state'] == 'ended'

    with pytest.raises(ValueError, match='ROUTE_LEASE_REJECTED'):
        store.transition(operation.operation_id, state='issued', owner_id='other-owner', lease_version=1)


def test_complete_local_evidence_still_does_not_authorize_live_trial(inputs, tmp_path):
    path = tmp_path / 'evidence.json'
    path.write_text(json.dumps(dict(contract_version=1, browser_started=True, tls_ca_loaded=True,
        app_root=str(inputs['app_root']), crawler_root=str(inputs['crawler_root']),
        app_revision='fixture', crawler_revision='fixture', mandatory_suites=[
            dict(collected=1, passed=1, skipped=0, exit_code=0, test_nodes=['fixture'])],
        runtime_controls={name: dict(verified=True, test_node='fixture', config_path='/fixture/config')
                          for name in kit.RUNTIME_CONTROLS})))
    result = kit.prepare(**inputs, evidence_path=path, echo_url='https://echo.example/ip', dedicated_test_account=True)
    assert result['offline_ready'] and not result['trial_ready']
    assert result['status'] == 'waiting_resource'
    metadata = tmp_path / 'resource.json'
    metadata.write_text(json.dumps(dict(proxy_id='fixture', product='sticky-http', sticky_http=True,
        expires_at=200, owner_authorized=True, credentials_in_ui=True)))
    result = kit.prepare(**inputs, evidence_path=path, resource_path=metadata,
        echo_url='https://echo.example/ip', dedicated_test_account=True)
    assert result['status'] == 'pending_live_probe' and not result['trial_ready']
    metadata.write_text(json.dumps(dict(proxy_id='fixture', sticky_http=True, expires_at=90,
        owner_authorized=True, credentials_in_ui=True)))
    assert 'RESOURCE_NOT_VERIFIED' in kit.prepare(**inputs, resource_path=metadata)['blockers']


def test_binary_contract_and_missing_scheduler_fail_before_io(inputs):
    inputs['browser_binary'].unlink()
    (inputs['crawler_root'] / 'tools/collection_route.py').write_text('SCHEMA_VERSION = 2')
    inputs['scheduler_db'].unlink()
    result = kit.prepare(**inputs)
    assert 'BROWSER_BINARY_UNAVAILABLE' in result['blockers']
    assert 'CONTRACT_VERSION_UNVERIFIED' in result['blockers']
    assert 'SCHEDULER_MISSING' in result['blockers']
    assert not inputs['scheduler_db'].exists()


def test_live_validation_crosschecks_resource_revision_and_recent_dual_probe(inputs, tmp_path):
    db = tmp_path / 'trial-db.sqlite3'
    with sqlite3.connect(db) as c:
        c.executescript("""CREATE TABLE proxy_resources(id TEXT,status TEXT,expires_at REAL,
            config_revision INTEGER,observed_exit_ip TEXT,observed_exit_at REAL,cooldown_until REAL,stop_reason TEXT);
            INSERT INTO proxy_resources VALUES('p','active',200,2,'192.0.2.1',95,NULL,'');
            CREATE TABLE crawler_accounts(id TEXT,proxy_required INTEGER,proxy_id TEXT,binding_revision INTEGER,status TEXT);
            INSERT INTO crawler_accounts VALUES('a',1,'p',3,'active');""")
    manifest_path = tmp_path / 'manifest.json'
    manifest_path.write_text(json.dumps(dict(trial_id='fixture', offline_ready=True,
        paths=dict(account_operation_db=str(db), scheduler_db=str(inputs['scheduler_db'])))))
    proof_path = tmp_path / 'live.json'
    channel = dict(context_id='context', ip='192.0.2.1', observed_at=95, config_revision=2)
    proof = dict(trial_id='fixture', account_id='a', proxy_id='p', binding_revision=3, config_revision=2,
        received_at=95, maintenance_released=True, runtime_controls_active=True, browser=channel, http=channel)
    proof_path.write_text(json.dumps(proof))
    result = kit.validate_live_gate(manifest_path=manifest_path, live_proof_path=proof_path, now=100)
    assert result['decision'] == 'LIVE_EVIDENCE_CONSISTENT' and result['trial_ready']
    assert not result['authorization'] and not result['network_started']
    with sqlite3.connect(db) as c:
        c.execute("UPDATE proxy_resources SET config_revision=3")
    result = kit.validate_live_gate(manifest_path=manifest_path, live_proof_path=proof_path, now=100)
    assert not result['trial_ready'] and 'LIVE_RESOURCE_NOT_ACTIVE' in result['blockers']
    proof['http'] = {**channel, 'ip': '192.0.2.2'}
    proof_path.write_text(json.dumps(proof))
    assert 'LIVE_DUAL_PROBE_MISMATCH' in kit.validate_live_gate(manifest_path=manifest_path, live_proof_path=proof_path, now=100)['blockers']
    assert 'LIVE_PROOF_STALE' in kit.validate_live_gate(manifest_path=manifest_path, live_proof_path=proof_path, now=500)['blockers']
