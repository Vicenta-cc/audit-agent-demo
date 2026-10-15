"""Real HTTP option validation/dispatch, independent of browser process fixtures."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import main
from backend.audit_agent.config import settings
from backend.investigation_creation.principal import Principal


@pytest.fixture
def api(monkeypatch):
    calls = []
    account = {'id': 'original-account', 'platform': 'dy'}
    principal = Principal('owner', role='admin')
    manager = SimpleNamespace(sessions={})
    def start(selected, **kwargs):
        calls.append((selected, kwargs))
        return {'id': 'login-session'}, True
    manager.start = start
    monkeypatch.setattr(main, 'crawler_login_v2_manager', manager)
    monkeypatch.setattr(main, 'crawler_account_login_manager', SimpleNamespace(_sessions={}))
    monkeypatch.setattr(main, '_require_crawler_account', lambda *a, **kw: account)
    monkeypatch.setattr(settings, 'crawler_login_v2_enabled', True)
    app = FastAPI()
    app.dependency_overrides[main.principal_provider] = lambda: principal
    app.add_api_route('/accounts/{account_id}/login', main.start_crawler_account_login, methods=['POST'])
    with TestClient(app) as client:
        yield client, calls, account


def test_migration_login_passes_id_and_actor_to_manager(api):
    client, calls, account = api
    response = client.post('/accounts/original-account/login',
        json={'supported_protocols': [2], 'migration_id': 'approved-migration'},
        headers={'x-login-token': 'x' * 32, 'idempotency-key': 'migration-scan'})
    assert response.status_code == 201
    assert calls[0][0] is account
    assert calls[0][1]['migration_id'] == 'approved-migration'
    assert calls[0][1]['user_id'] == 'owner'
    assert calls[0][1]['request_key'] == 'migration-scan'


@pytest.mark.parametrize('migration', ['', '   ', {}, [], 123, 'x' * 129])
def test_invalid_migration_options_never_start_browser(api, migration):
    client, calls, _ = api
    response = client.post('/accounts/original-account/login',
        json={'supported_protocols': [2], 'migration_id': migration})
    assert response.status_code == 400
    assert response.json()['detail']['code']=='INVALID_MIGRATION_ID'
    assert not calls


@pytest.mark.parametrize('platform,protocols', [('dy', [1]), ('xhs', [2])])
def test_migration_cannot_fall_back_to_other_login_engine(api, platform, protocols):
    client, calls, account = api
    account['platform'] = platform
    response = client.post('/accounts/original-account/login',
        json={'supported_protocols': protocols, 'migration_id': 'approved-migration'})
    assert response.status_code == 400
    assert response.json()['detail']['code']=='MIGRATION_REQUIRES_DY_V2'
    assert not calls


@pytest.mark.parametrize('failure,expected_code',[
    ('profile','BROWSER_PROFILE_MIGRATION_REQUIRED'),
    ('blocked','MIGRATION_BLOCKED'),
    ('missing','MIGRATION_NOT_FOUND'),
    ('maintenance','CRAWLER_BROWSER_MAINTENANCE'),
])
def test_actual_login_store_guard_errors_keep_specific_api_codes(api,tmp_path,monkeypatch,failure,expected_code):
    import sys
    import time
    from backend.audit_agent.auth_state_cipher import AuthStateCipher
    from backend.audit_agent.crawler_account_store import CrawlerAccountStore
    from backend.audit_agent.crawler_login_v2_manager import CrawlerLoginV2Manager
    client,calls,selected=api
    accounts=CrawlerAccountStore(tmp_path/'accounts.sqlite3')
    account=accounts.create(platform='dy',display_name='guarded')
    selected.update(account)
    monkeypatch.setattr(settings,'crawler_browser_profile_root',tmp_path/'old-profiles')
    monkeypatch.setattr(settings,'crawler_camoufox_state_root',tmp_path/'camoufox-state')
    monkeypatch.setattr(settings,'request_scheduler_db',tmp_path/'scheduler.sqlite3')
    monkeypatch.setattr(settings,'crawler_browser_maintenance',failure=='maintenance')
    if failure=='profile':
        with accounts._connect() as db:
            db.execute("UPDATE crawler_accounts SET profile_engine='legacy',profile_generation='' WHERE id=?",(account['id'],))
    elif failure=='blocked':
        with accounts._connect() as db:
            db.execute("""INSERT INTO crawler_browser_migrations(migration_id,account_id,owner_id,phase,generation,
                expected_auth_revision,binding_revision,config_revision,platform_account_id,original_status,
                old_profile_path,new_profile_path,archive_path,scheduler_identity,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ('blocked-migration',account['id'],'owner','ADMISSION_BLOCKED','fixture-generation',0,0,0,'','active',
                 '','','','',time.time(),time.time()))
    manager=CrawlerLoginV2Manager(accounts,AuthStateCipher('fixture'),python_path=sys.executable,
        helper_path=tmp_path/'unused-helper.py',lease_factory=lambda _:pytest.fail('blocked login acquired a browser lease'))
    monkeypatch.setattr(main,'crawler_login_v2_manager',manager)
    body={'supported_protocols':[2]}
    if failure=='missing':
        body['migration_id']='missing-migration'
    try:
        response=client.post('/accounts/'+account['id']+'/login',json=body,
            headers={'x-login-token':'x'*32,'idempotency-key':'guard-error'})
        assert response.status_code==409
        detail=response.json()['detail']
        assert detail['code']==expected_code
        assert detail['message'] and str(tmp_path) not in detail['message']
        assert not calls and not manager.sessions
    finally:
        manager.shutdown()


def test_safe_error_mapping_does_not_accept_arbitrary_exception_text():
    from backend.audit_agent.crawler_login_v2_manager import safe_error_code
    assert safe_error_code(ValueError('https://private.invalid/password'))=='LOGIN_FAILED'
    # Registering fixed codes does not make generic runtime strings public.
    assert safe_error_code(RuntimeError('CRAWLER_BROWSER_MAINTENANCE'))=='LOGIN_FAILED'
