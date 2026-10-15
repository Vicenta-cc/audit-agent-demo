"""Offline proxy ACL, secrets, binding and mutation API contracts."""
import fcntl
import json

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import main
from backend.application_auth.service import ApplicationAuthService
from backend.application_auth.store import AuthStore
from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
from backend.investigation_creation.principal import Principal
from backend.task_admission.resources import proxy_lock_path, proxy_mutation_guard

CANARY = 'api-proxy-secret-canary@#$'


@pytest.fixture
def api(tmp_path, monkeypatch):
    auth = AuthStore(tmp_path / 'auth.sqlite3')
    accounts = CrawlerAccountStore(tmp_path / 'accounts.sqlite3')
    proxies = CrawlerProxyStore(accounts, cipher=AuthStateCipher(Fernet.generate_key()), mutation_guard=proxy_mutation_guard)
    users = {name: auth.create_user(username=name, password='proxy test password 123', role='admin' if name == 'admin' else 'user') for name in ('admin', 'alice', 'bravo')}
    principal = [Principal(users['admin']['id'], role='admin')]
    monkeypatch.setattr(settings, 'app_auth_mode', 'required')
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', True)
    monkeypatch.setattr(settings, 'task_resource_lock_dir', tmp_path / 'locks')
    monkeypatch.setattr(main, 'auth_store', auth)
    monkeypatch.setattr(main, 'auth_service', ApplicationAuthService(auth))
    monkeypatch.setattr(main, 'crawler_account_store', accounts)
    monkeypatch.setattr(main, '_crawler_proxy_store', lambda: proxies)
    from backend.audit_agent import crawler_route
    monkeypatch.setattr(crawler_route, 'get_proxy_store', lambda account_store=None: proxies)
    monkeypatch.setattr(settings, 'request_scheduler_db', tmp_path / 'scheduler.sqlite3')
    app = FastAPI()
    app.add_exception_handler(main.RequestValidationError, main.proxy_request_validation_handler)
    app.dependency_overrides[main.principal_provider] = lambda: principal[0]
    for path, handler, method in [
        ('/api/crawler-proxies', main.list_crawler_proxies, 'GET'),
        ('/api/crawler-proxies', main.create_crawler_proxy, 'POST'),
        ('/api/crawler-proxies/{proxy_id}', main.get_crawler_proxy, 'GET'),
        ('/api/crawler-proxies/{proxy_id}', main.update_crawler_proxy, 'PATCH'),
        ('/api/crawler-proxies/{proxy_id}', main.delete_crawler_proxy, 'DELETE'),
        ('/api/crawler-proxies/{proxy_id}/recover', main.recover_crawler_proxy, 'POST'),
        ('/api/crawler-proxies/{proxy_id}/probe', main.probe_crawler_proxy, 'POST'),
        ('/api/crawler-accounts', main.create_crawler_account, 'POST'),
        ('/api/crawler-accounts/{account_id}', main.update_crawler_account, 'PATCH'),
        ('/api/crawler-accounts/{account_id}/proxy-binding', main.update_crawler_account_proxy_binding, 'PATCH'),
    ]:
        app.add_api_route(path, handler, methods=[method])
    return TestClient(app), auth, accounts, proxies, users, principal


def create_proxy(client, **extra):
    response = client.post('/api/crawler-proxies', json={'display_name':'试用入口', 'endpoint':{'scheme':'http', 'host':'127.0.0.1','port':18080}, 'credentials':{'username':'proxy-user','password':CANARY}, **extra})
    assert response.status_code == 200, response.text
    assert CANARY not in response.text
    return response.json()['item']


def test_public_projection_admin_and_explicit_use_grant(api):
    client, auth, _, _, users, principal = api
    resource = create_proxy(client)
    assert resource['endpoint'] == {'scheme':'http','host':'127.0.0.1','port':18080}
    assert resource['has_credentials'] is True
    principal[0] = Principal(users['alice']['id'], role='user')
    assert client.get('/api/crawler-proxies').json()['items'] == []
    assert client.get('/api/crawler-proxies/'+resource['id']).status_code == 404
    assert client.post('/api/crawler-proxies', json={'display_name':'no','endpoint':{'host':'localhost','port':80}}).status_code == 403
    assert client.patch('/api/crawler-proxies/'+resource['id'], json={'expected_revision':1,'display_name':'no'}).status_code == 403
    auth.grant_resource(user_id=users['alice']['id'], resource_type='crawler-proxy', resource_id=resource['id'], permission='use', actor_user_id=users['admin']['id'])
    visible = client.get('/api/crawler-proxies').json()
    assert len(visible['items']) == 1 and visible['can_manage'] is False
    encoded = json.dumps(visible)
    for secret_field in ('transport_ciphertext', 'password', 'username', CANARY):
        assert secret_field not in encoded


def test_binding_capacity_and_private_account_authorization(api):
    client, auth, accounts, _, users, principal = api
    resource = create_proxy(client)
    principal[0] = Principal(users['alice']['id'], role='user')
    payload = {'platform':'dy','display_name':'alice','proxy_id':resource['id']}
    assert client.post('/api/crawler-accounts', json=payload).status_code == 404
    assert accounts.list() == []
    auth.grant_resource(user_id=users['alice']['id'], resource_type='crawler-proxy', resource_id=resource['id'], permission='use', actor_user_id=users['admin']['id'])
    account = client.post('/api/crawler-accounts', json=payload).json()['item']
    assert account['proxy_id'] == resource['id'] and account['proxy_required']
    assert auth.owns_crawler_account(users['alice']['id'], account['id'])
    assert client.post('/api/crawler-accounts', json={**payload,'display_name':'second'}).status_code == 409
    assert len(accounts.list()) == 1
    principal[0] = Principal(users['admin']['id'], role='admin')
    assert client.patch('/api/crawler-accounts/'+account['id']+'/proxy-binding', json={'proxy_id':resource['id'],'expected_binding_revision':1}).status_code == 404
    assert client.delete('/api/crawler-proxies/'+resource['id']+'?expected_revision=1').status_code == 409


def test_required_binding_legacy_and_secret_preservation(api, monkeypatch):
    client, _, _, proxies, _, _ = api
    assert client.post('/api/crawler-accounts', json={'platform':'dy','display_name':'missing'}).status_code == 400
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', False)
    assert client.post('/api/crawler-accounts', json={'platform':'dy','display_name':'legacy'}).status_code == 200
    resource = create_proxy(client)
    response = client.patch('/api/crawler-proxies/'+resource['id'], json={'expected_revision':1,'display_name':'renamed'})
    assert response.status_code == 200
    assert response.json()['item']['has_credentials'] is True
    assert CANARY not in response.text
    snapshot = proxies.decrypt_snapshot(resource['id'], expected_revision=response.json()['item']['config_revision'])
    assert snapshot['credentials']['password'] == CANARY
    assert client.patch('/api/crawler-proxies/'+resource['id'], json={'expected_revision':1,'display_name':'stale'}).status_code == 409


def test_busy_edit_is_conflict(api):
    client, _, _, _, _, _ = api
    resource = create_proxy(client)
    path = proxy_lock_path(resource['id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    try:
        response = client.patch('/api/crawler-proxies/'+resource['id'], json={'expected_revision':1,'display_name':'busy'})
        assert response.status_code == 409, response.text
        assert CANARY not in response.text
    finally:
        handle.close()


def test_validation_error_never_echoes_credentials(api):
    client, *_ = api
    response = client.post('/api/crawler-proxies', json={'display_name':'invalid','endpoint':{'host':'localhost','port':8080}, 'credentials':{'username':{'secret':CANARY}, 'password':CANARY}})
    assert response.status_code == 422
    assert CANARY not in response.text
    assert 'input' not in response.text


def test_binding_endpoint_is_separate_and_revision_checked(api):
    client, _, accounts, _, _, _ = api
    first = create_proxy(client)
    second = create_proxy(client)
    account = client.post('/api/crawler-accounts', json={'platform':'dy','display_name':'owner','proxy_id':first['id']}).json()['item']
    response = client.patch('/api/crawler-accounts/'+account['id'], json={'display_name':'','proxy_id':second['id'],'expected_binding_revision':1})
    assert response.status_code == 400
    assert accounts.get(account['id'])['proxy_id'] == first['id']
    response = client.patch('/api/crawler-accounts/'+account['id']+'/proxy-binding', json={'proxy_id':second['id'],'expected_binding_revision':0})
    assert response.status_code == 409
    assert accounts.get(account['id'])['proxy_id'] == first['id']
    response = client.patch('/api/crawler-accounts/'+account['id']+'/proxy-binding', json={'proxy_id':second['id'],'expected_binding_revision':1})
    assert response.status_code == 200
    assert response.json()['item']['proxy_id'] == second['id']
    assert response.json()['item']['binding_revision'] == 2


def test_admin_recover_requires_idle_current_revision_and_clears_local_stop(api):
    client, _, _, proxies, users, principal = api
    resource = create_proxy(client)
    proxies.set_health(resource['id'], status='exit_changed', stop_reason='PROXY_EXIT_CHANGED')
    path = '/api/crawler-proxies/'+resource['id']+'/recover'
    principal[0] = Principal(users['alice']['id'], role='user')
    assert client.post(path, json={'expected_revision':1}).status_code == 403
    principal[0] = Principal(users['admin']['id'], role='admin')
    lock = proxy_lock_path(resource['id'])
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        assert client.post(path, json={'expected_revision':1}).status_code == 409
    response = client.post(path, json={'expected_revision':1})
    assert response.status_code == 200, response.text
    item = response.json()['item']
    assert item['config_revision'] == 2 and item['status'] == 'active' and item['stop_reason'] == ''
    assert CANARY not in response.text
    assert client.post(path, json={'expected_revision':1}).status_code == 409
    proxies.set_health(resource['id'], status='unavailable', stop_reason='PROXY_UNAVAILABLE')
    response = client.patch('/api/crawler-proxies/'+resource['id'], json={'expected_revision':2,'status':'active'})
    assert response.status_code == 200
    assert response.json()['item']['config_revision'] == 3
    assert response.json()['item']['stop_reason'] == ''


def test_probe_checks_private_owner_binding_admin_and_safe_projection(api, monkeypatch):
    client, auth, accounts, proxies, users, principal = api
    resource = create_proxy(client)
    account = accounts.create(platform='dy', display_name='private-probe')
    proxies.bind(account['id'], resource['id'], expected_binding_revision=0)
    auth.register_crawler_account(account['id'], users['alice']['id'])
    from backend.audit_agent import crawler_route
    calls = []
    def probe(account_id, owner_id, **kwargs):
        calls.append((account_id, owner_id, kwargs))
        return {'schema_version':1,'proxy_id':resource['id'],'account_id':account_id,'config_revision':1,'binding_revision':1,'observed_exit_ip':'203.0.113.9','observed_at':1791630000,'status':'maintenance','business_enabled':True,'password':CANARY,'credentials':{'password':CANARY}}
    monkeypatch.setattr(crawler_route, 'probe_account_route', probe)
    path = '/api/crawler-proxies/'+resource['id']+'/probe'
    assert client.post(path, json={'account_id':account['id'],'expected_revision':1}).status_code == 404
    assert calls == []
    principal[0] = Principal(users['alice']['id'], role='user')
    assert client.post(path, json={'account_id':account['id'],'expected_revision':1}).status_code == 403
    assert calls == []
    principal[0] = Principal(users['admin']['id'], role='admin')
    own = client.post('/api/crawler-accounts', json={'platform':'dy','display_name':'admin-probe','proxy_id':resource['id']})
    assert own.status_code == 409  # binding capacity, no hidden account publication
    resource = proxies.update(resource['id'], expected_revision=1, max_bound_accounts=2)
    own = client.post('/api/crawler-accounts', json={'platform':'dy','display_name':'admin-probe','proxy_id':resource['id']}).json()['item']
    assert client.post(path, json={'account_id':own['id'],'expected_revision':1}).status_code == 409
    proxies.set_health(resource['id'], status='maintenance', stop_reason='PROXY_UNAVAILABLE')
    response = client.post(path, json={'account_id':own['id'],'expected_revision':2})
    assert response.status_code == 200
    assert response.json()['item']['business_enabled'] is False
    assert CANARY not in response.text and 'credentials' not in response.text
    assert len(calls) == 1 and calls[0][2]['expected_config_revision'] == 2
    assert proxies.get(resource['id'])['status'] == 'maintenance'
    assert proxies.get(resource['id'])['stop_reason'] == 'PROXY_UNAVAILABLE'


def test_platform_normalization_requires_binding_and_summary_excludes_pending(api):
    client, _, accounts, proxies, _, _ = api
    for platform in ('DY', ' dy '):
        response = client.post('/api/crawler-accounts', json={'platform':platform,'display_name':'unbound'})
        assert response.status_code == 400 and response.json()['detail'] == 'PROXY_CONFIGURATION_REQUIRED'
    assert accounts.list() == []
    resource = create_proxy(client)
    bound = client.post('/api/crawler-accounts', json={'platform':' DY ','display_name':'bound','access_scope':'public','proxy_id':resource['id']}).json()['item']
    accounts.save_auth_state(bound['id'], 'fixture-encrypted')
    legacy = accounts.create(platform='dy', display_name='legacy-public', access_scope='public')
    accounts.save_auth_state(legacy['id'], 'fixture-encrypted')
    assert main._shared_crawler_pool_summary()['ready'] == 0
    cap = client.get('/api/crawler-proxies').json()['max_active_accounts_cap']
    assert cap == settings.request_concurrency
