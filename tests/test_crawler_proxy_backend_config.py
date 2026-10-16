"""Offline backend configuration, default selection and readonly readiness."""
import fcntl
import io
import json
import time

import pytest
from cryptography.fernet import Fernet

from backend.audit_agent.auth_state_cipher import AuthStateCipher
from backend.audit_agent.config import settings
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.crawler_proxy_store import CrawlerProxyStore
from backend.task_admission.resources import proxy_lock_path, proxy_mutation_guard
from scripts import crawler_proxy_config as cli
from tests.test_crawler_proxy_api import api, create_proxy
from crawler_auth_fixtures import mark_camoufox_login_verified

SECRET = 'backend-credential-canary@!'


@pytest.fixture
def ops(tmp_path, monkeypatch):
    from functools import partial
    accounts = CrawlerAccountStore(tmp_path / 'accounts.sqlite3')
    monkeypatch.setattr(settings, 'task_resource_lock_dir', tmp_path / 'locks')
    monkeypatch.setattr(settings, 'crawler_browser_profile_root', tmp_path / 'profiles')
    return CrawlerProxyStore(accounts, cipher=AuthStateCipher(Fernet.generate_key()),
        mutation_guard=partial(proxy_mutation_guard, account_store=accounts))


def metadata():
    return {'operation':'create','fields':{'display_name':'backend-only',
        'endpoint':{'scheme':'http','host':'localhost','port':18080},
        'expires_at':None,'max_bound_accounts':2,'max_active_accounts':1}}


def invoke(tmp_path, capsys, store, config, *args):
    path = tmp_path / 'metadata.json'
    path.write_text(json.dumps(config))
    rc = cli.main(['--apply', str(path), *args], store=store)
    output = capsys.readouterr().out
    assert SECRET not in output
    return rc, json.loads(output)


def test_cli_encrypts_stdin_and_preserves_omitted_credentials(tmp_path, capsys, monkeypatch, ops):
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'username':'operator','password':SECRET})))
    rc, out = invoke(tmp_path, capsys, ops, metadata(), '--credentials-stdin')
    assert rc == 0 and out['item']['has_credentials']
    pid = out['item']['id']
    assert ops.decrypt_snapshot(pid, expected_revision=1)['credentials']['password'] == SECRET
    assert SECRET.encode() not in ops.db_path.read_bytes()
    update = {'operation':'update','proxy_id':pid,'expected_revision':1,'fields':{'display_name':'changed'}}
    assert invoke(tmp_path, capsys, ops, update)[1]['item']['config_revision'] == 2
    assert ops.decrypt_snapshot(pid, expected_revision=2)['credentials']['password'] == SECRET
    assert invoke(tmp_path, capsys, ops, update) == (1, {'code':'PROXY_REVISION_MISMATCH'})
    assert cli.main(['--list'], store=ops) == 0
    public = capsys.readouterr().out
    assert SECRET not in public and 'username' not in public and 'display_name' not in public


def test_cli_busy_update_and_binding_are_guarded(tmp_path, capsys, ops):
    item = cli.apply_config(ops, metadata())
    pid = item['id']
    account = ops.accounts.create(platform='dy', display_name='owned')
    path = proxy_lock_path(pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        for config in (
            {'operation':'update','proxy_id':pid,'expected_revision':1,'fields':{'max_bound_accounts':3}},
            {'operation':'bind','proxy_id':pid,'account_id':account['id'],'expected_binding_revision':0},
        ):
            assert invoke(tmp_path, capsys, ops, config) == (1, {'code':'PROXY_BUSY'})
    binding = {'operation':'bind','proxy_id':pid,'account_id':account['id'],'expected_binding_revision':0}
    assert invoke(tmp_path, capsys, ops, binding)[1]['item']['binding_revision'] == 1
    assert invoke(tmp_path, capsys, ops, binding) == (1, {'code':'PROXY_REVISION_MISMATCH'})
    assert ops.get(pid)['config_revision'] == 1


def test_cli_rejects_secrets_in_metadata_and_redacts_all_error_values(tmp_path, capsys, monkeypatch, ops):
    config = metadata()
    config['fields']['credentials'] = {'password':SECRET}
    assert invoke(tmp_path, capsys, ops, config) == (1, {'code':'PROXY_CONFIG_INVALID'})
    monkeypatch.setattr('sys.stdin', io.StringIO(json.dumps({'username':{'secret':SECRET},'password':SECRET})))
    assert invoke(tmp_path, capsys, ops, metadata(), '--credentials-stdin') == (1, {'code':'PROXY_INVALID_CREDENTIALS'})
    assert cli.main(['--password', SECRET], store=ops) == 1
    assert capsys.readouterr().out == '{"code": "PROXY_CONFIG_INVALID"}\n'
    assert ops.list() == []


def test_default_selection_acl_capacity_and_expired_no_partial(api, monkeypatch):
    from backend import main
    from backend.investigation_creation.principal import Principal
    client, auth, accounts, proxies, users, principal = api
    monkeypatch.setattr(settings, 'crawler_proxy_default_id', '')
    payload = {'platform':' DY ','display_name':'default'}
    assert client.post('/api/crawler-accounts', json=payload).json()['detail'] == 'PROXY_CONFIGURATION_REQUIRED'
    resource = create_proxy(client, expires_at=time.time()+3600)
    monkeypatch.setattr(settings, 'crawler_proxy_default_id', resource['id'])
    principal[0] = Principal(users['alice']['id'], role='user')
    # HEAD 77f73f3 filters unauthorized defaults out of the pool rather than
    # exposing the hidden proxy's existence through a 404 lookup response.
    denied = client.post('/api/crawler-accounts', json=payload)
    assert denied.status_code == 400 and denied.json()['detail'] == 'PROXY_CONFIGURATION_REQUIRED'
    assert accounts.list() == []
    auth.grant_resource(user_id=users['alice']['id'], resource_type='crawler-proxy', resource_id=resource['id'], permission='use', actor_user_id=users['admin']['id'])
    response = client.post('/api/crawler-accounts', json=payload)
    assert response.status_code == 200
    item = response.json()['item']
    assert item['proxy_id'] == resource['id'] and not item['collection_ready']
    assert item['collection_block_reason'] == 'ACCOUNT_LOGIN_REQUIRED'
    accounts.save_auth_state(item['id'], 'trusted-fixture-login')
    mark_camoufox_login_verified(accounts, item['id'])
    assert main._crawler_account_public_view(accounts.get(item['id']),principal[0])['collection_block_reason'] == 'PROXY_LOGIN_VALIDATION_REQUIRED'
    full = client.post('/api/crawler-accounts', json=payload)
    assert full.status_code == 409 and full.json()['detail'] == 'PROXY_CAPACITY_EXCEEDED'
    assert len(accounts.list()) == 1
    proxies.update(resource['id'], expected_revision=1, expires_at=time.time()-1)
    assert client.post('/api/crawler-accounts', json=payload).status_code == 409
    assert len(accounts.list()) == 1
    assert main._crawler_account_public_view(accounts.get(item['id']), principal[0])['collection_block_reason'] == 'PROXY_UNAVAILABLE'


def test_readiness_requires_validated_route_current_revision_and_use_acl(api, monkeypatch):
    from backend import main
    from backend.investigation_creation.principal import Principal
    client, auth, accounts, proxies, users, principal = api
    resource = create_proxy(client, expires_at=time.time()+3600)
    item = client.post('/api/crawler-accounts', json={'platform':'dy','display_name':'test','proxy_id':resource['id']}).json()['item']
    accounts.save_auth_state(item['id'], 'encrypted-fixture')
    mark_camoufox_login_verified(accounts, item['id'])
    with accounts._connect() as conn:
        conn.execute('UPDATE crawler_accounts SET validated_proxy_binding_revision=1,validated_proxy_config_revision=1 WHERE id=?', (item['id'],))
    project = lambda: main._crawler_account_public_view(accounts.get(item['id']), principal[0])
    assert project()['collection_ready'] is True
    proxies.update(resource['id'], expected_revision=1, display_name='new revision')
    assert project()['collection_block_reason'] == 'PROXY_LOGIN_VALIDATION_REQUIRED'
    principal[0] = Principal(users['alice']['id'], role='user')
    assert project()['collection_block_reason'] == 'PROXY_UNAVAILABLE'
    monkeypatch.setattr(settings, 'crawler_proxy_enabled', False)
    assert project()['collection_ready'] is False


def test_default_binding_busy_and_owner_failure_roll_back(api, monkeypatch):
    client, auth, accounts, proxies, users, principal = api
    resource = create_proxy(client, expires_at=time.time()+3600)
    monkeypatch.setattr(settings, 'crawler_proxy_default_id', resource['id'])
    payload = {'platform':'dy','display_name':'rollback'}
    path = proxy_lock_path(resource['id'])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        response = client.post('/api/crawler-accounts', json=payload)
        # The existing default-pool retry exhausts all busy choices and reports
        # its capacity contract, while explicit edit operations retain BUSY.
        assert response.status_code == 409 and response.json()['detail'] == 'PROXY_CAPACITY_CONFLICT'
        assert accounts.list() == []
    def failure(*args):
        raise RuntimeError('ownership registration failed')
    monkeypatch.setattr(auth, 'register_crawler_account', failure)
    with pytest.raises(RuntimeError, match='ownership registration failed'):
        client.post('/api/crawler-accounts', json=payload)
    assert accounts.list() == [] and proxies.get(resource['id'])['bound_accounts'] == 0


def test_cli_rejects_boolean_revision_before_mutation(tmp_path, capsys, ops):
    resource = cli.apply_config(ops, metadata())
    config = {'operation':'update','proxy_id':resource['id'],'expected_revision':True,'fields':{'display_name':'bad'}}
    assert invoke(tmp_path, capsys, ops, config) == (1, {'code':'PROXY_CONFIG_INVALID'})
    assert ops.get(resource['id'])['config_revision'] == 1


@pytest.mark.parametrize('value', ['false', 'true', '', 0, 1, None])
def test_cli_invalid_clear_credentials_preserves_ciphertext_and_revision(tmp_path, capsys, ops, value):
    resource = cli.apply_config(ops, metadata(), credentials={'username':'operator','password':SECRET})
    with ops.accounts._connect() as conn:
        original = conn.execute('SELECT transport_ciphertext FROM proxy_resources WHERE id=?', (resource['id'],)).fetchone()[0]
    config = {'operation':'update','proxy_id':resource['id'],'expected_revision':1,'fields':{'clear_credentials':value}}
    assert invoke(tmp_path, capsys, ops, config) == (1, {'code':'PROXY_CONFIG_INVALID'})
    assert ops.get(resource['id'])['config_revision'] == 1
    with ops.accounts._connect() as conn:
        assert conn.execute('SELECT transport_ciphertext FROM proxy_resources WHERE id=?', (resource['id'],)).fetchone()[0] == original
    assert ops.decrypt_snapshot(resource['id'], expected_revision=1)['credentials']['password'] == SECRET


def test_cli_clear_credentials_requires_explicit_true(tmp_path, capsys, ops):
    resource = cli.apply_config(ops, metadata(), credentials={'username':'operator','password':SECRET})
    config = {'operation':'update','proxy_id':resource['id'],'expected_revision':1,'fields':{'clear_credentials':False}}
    assert invoke(tmp_path, capsys, ops, config)[1]['item']['config_revision'] == 1
    assert ops.decrypt_snapshot(resource['id'], expected_revision=1)['credentials']['password'] == SECRET
    config['fields']['clear_credentials'] = True
    rc, output = invoke(tmp_path, capsys, ops, config)
    assert rc == 0 and output['item']['config_revision'] == 2
    assert output['item']['has_credentials'] is False
    assert ops.decrypt_snapshot(resource['id'], expected_revision=2)['credentials'] == {'username':'','password':''}


@pytest.mark.parametrize('failure', ['expired', 'unavailable', 'cooldown', 'paused', 'shared_exit_paused'])
def test_unhealthy_default_pool_is_not_reported_as_full(api, monkeypatch, failure):
    client, auth, accounts, proxies, users, principal = api
    resource = create_proxy(client, expires_at=time.time()+3600)
    monkeypatch.setattr(settings, 'crawler_proxy_default_id', resource['id'])
    existing = accounts.create(platform='dy', display_name='existing')
    proxies.bind(existing['id'], resource['id'], expected_binding_revision=0)
    with accounts._connect() as db:
        if failure == 'expired':
            db.execute('UPDATE proxy_resources SET expires_at=? WHERE id=?', (time.time()-1, resource['id']))
        elif failure == 'unavailable':
            db.execute("UPDATE proxy_resources SET status='unavailable',stop_reason='PROXY_UNAVAILABLE' WHERE id=?", (resource['id'],))
        elif failure in {'cooldown', 'paused'}:
            field = 'cooldown_until' if failure == 'cooldown' else 'paused_until'
            db.execute(f'UPDATE proxy_resources SET {field}=? WHERE id=?', (time.time()+60, resource['id']))
        else:
            accounts.observe_shared_exit('203.0.113.19')
            db.execute("UPDATE proxy_resources SET shared_exit_key='203.0.113.19' WHERE id=?", (resource['id'],))
            db.execute("UPDATE crawler_shared_exits SET pause_reason='ACCOUNT_CHALLENGE' WHERE exit_key='203.0.113.19'")
    response = client.post('/api/crawler-accounts', json={'platform':'dy', 'display_name':'blocked'})
    assert response.status_code == 409 and response.json()['detail'] == 'PROXY_UNAVAILABLE'
    assert [item['id'] for item in accounts.list()] == [existing['id']]
    assert proxies.get(resource['id'])['bound_accounts'] == 1


def test_default_pool_concurrent_binding_rechecks_capacity_and_rolls_back(api, monkeypatch):
    client, auth, accounts, proxies, users, principal = api
    resource = create_proxy(client, expires_at=time.time()+3600)
    monkeypatch.setattr(settings, 'crawler_proxy_default_id', resource['id'])
    bind = proxies.bind
    raced = []
    def racing_bind(account_id, proxy_id, **kwargs):
        if not raced:
            winner = accounts.create(platform='dy', display_name='concurrent winner')
            raced.append(winner['id'])
            bind(winner['id'], proxy_id, expected_binding_revision=0)
        return bind(account_id, proxy_id, **kwargs)
    monkeypatch.setattr(proxies, 'bind', racing_bind)
    response = client.post('/api/crawler-accounts', json={'platform':'dy', 'display_name':'losing creation'})
    assert response.status_code == 409 and response.json()['detail'] == 'PROXY_CAPACITY_CONFLICT'
    assert [item['id'] for item in accounts.list()] == raced
    assert proxies.get(resource['id'])['bound_accounts'] == 1
