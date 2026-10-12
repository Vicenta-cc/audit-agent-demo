"""Offline readonly safety and ACL-preserving backend pool binding."""
import time

from backend import main
from backend.audit_agent.config import settings
from backend.investigation_creation.principal import Principal
from test_crawler_proxy_api import api, create_proxy


def ready_account(client, accounts, proxies, *, public=False):
    resource = create_proxy(client, expires_at=time.time() + 3600)
    response = client.post('/api/crawler-accounts', json={
        'platform': 'dy', 'display_name': 'fixture', 'proxy_id': resource['id'],
        'access_scope': 'public' if public else 'private'})
    assert response.status_code == 200, response.text
    account = response.json()['item']
    accounts.save_auth_state(account['id'], 'encrypted-fixture')
    with accounts._connect() as conn:
        conn.execute('UPDATE crawler_accounts SET validated_proxy_binding_revision=binding_revision, validated_proxy_config_revision=1 WHERE id=?', (account['id'],))
    return accounts.get(account['id']), resource


def test_public_projection_pause_recovery_generation_and_no_secrets(api, monkeypatch):
    client, _, accounts, proxies, _, principal = api
    monkeypatch.setattr(settings, 'crawler_safety_enabled', True)
    account, resource = ready_account(client, accounts, proxies)
    observed = accounts.observe_shared_exit('203.0.113.8')
    with accounts._connect() as conn:
        conn.execute('UPDATE proxy_resources SET shared_exit_key=?,shared_exit_generation=?,observed_exit_at=? WHERE id=?',
                     (observed['shared_exit_key'], observed['shared_exit_generation'], time.time(), resource['id']))
    monkeypatch.setattr(proxies.cipher, 'decrypt', lambda *_: (_ for _ in ()).throw(AssertionError('readonly must not decrypt')))
    item = main._crawler_account_public_view(accounts.get(account['id']), principal[0])
    assert item['collection_ready']
    with accounts._connect() as conn:
        conn.execute('UPDATE crawler_shared_exits SET generation=2')
    assert not main._crawler_account_public_view(accounts.get(account['id']), principal[0])['collection_ready']
    with accounts._connect() as conn:
        conn.execute('UPDATE crawler_shared_exits SET generation=1')
    accounts.set_route_state(account['id'], state='active', paused_until=time.time()+60)
    assert main._crawler_account_public_view(accounts.get(account['id']), principal[0])['collection_block_reason'] == 'ACCOUNT_PAUSED'
    accounts.set_route_state(account['id'], state='challenge_paused', shared_exit_key=observed['shared_exit_key'], generation=1, paused_until=time.time()+60, pause_reason='ACCOUNT_CHALLENGE')
    item = main._crawler_account_public_view(accounts.get(account['id']), principal[0])
    assert not item['collection_ready'] and item['collection_block_reason'] == 'ACCOUNT_CHALLENGE_PAUSED'
    assert item['shared_exit_generation'] == 1 and item['paused_until'] > time.time()
    assert not {'shared_exit_key', 'recovery_claim', 'recovery_incident', 'profile_key', 'fingerprint_seed', 'auth_state_ciphertext'} & item.keys()
    accounts.set_route_state(account['id'], state='manual_required')
    assert main._crawler_account_public_view(accounts.get(account['id']), principal[0])['collection_block_reason'] == 'ACCOUNT_MANUAL_REQUIRED'


def test_unknown_shared_exit_and_direct_flag_fail_closed(api, monkeypatch):
    client, _, accounts, proxies, _, principal = api
    monkeypatch.setattr(settings, 'crawler_safety_enabled', True)
    account, _ = ready_account(client, accounts, proxies)
    assert main._crawler_account_public_view(account, principal[0])['collection_block_reason'] == 'SHARED_EXIT_UNAVAILABLE'
    observed = accounts.observe_shared_exit('203.0.113.9', service_exit=True)
    accounts.set_route_state(account['id'], state='active', route_mode='direct_degraded', shared_exit_key=observed['shared_exit_key'], generation=1)
    monkeypatch.setattr(settings, 'crawler_direct_degrade_enabled', False)
    assert not main._crawler_account_public_view(accounts.get(account['id']), principal[0])['collection_ready']
    monkeypatch.setattr(settings, 'crawler_direct_degrade_enabled', True)
    item = main._crawler_account_public_view(accounts.get(account['id']), principal[0])
    assert not item['collection_ready'] and item['collection_block_reason'] == 'LOGIN_REVALIDATION_REQUIRED'
    accounts.mark_direct_validated(account['id'], observed['shared_exit_generation'])
    item = main._crawler_account_public_view(accounts.get(account['id']), principal[0])
    assert item['collection_ready'] and item['route_mode'] == 'direct_degraded'
    monkeypatch.setattr(settings, 'crawler_direct_degrade_enabled', False)
    assert not main._crawler_account_public_view(accounts.get(account['id']), principal[0])['collection_ready']
    monkeypatch.setattr(settings, 'crawler_direct_degrade_enabled', True)
    with accounts._connect() as conn:
        conn.execute("UPDATE crawler_shared_exits SET pause_reason='ACCOUNT_CHALLENGE'")
    assert not main._crawler_account_public_view(accounts.get(account['id']), principal[0])['collection_ready']


def test_default_pool_capacity_acl_and_expiry(api, monkeypatch):
    client, auth, accounts, _, users, principal = api
    monkeypatch.setattr(settings, 'crawler_safety_enabled', True)
    first = create_proxy(client, expires_at=time.time()+3600)
    second = create_proxy(client, expires_at=time.time()+3600)
    expired = create_proxy(client, expires_at=time.time()-1)
    monkeypatch.setattr(settings, 'crawler_proxy_pool_ids', (first['id'], expired['id'], second['id']))
    principal[0] = Principal(users['alice']['id'], role='user')
    payload = {'platform': 'dy', 'display_name': 'owner'}
    assert client.post('/api/crawler-accounts', json=payload).status_code == 400
    assert accounts.list() == []
    for resource in (first, second):
        auth.grant_resource(user_id=users['alice']['id'], resource_type='crawler-proxy', resource_id=resource['id'], permission='use', actor_user_id=users['admin']['id'])
    one = client.post('/api/crawler-accounts', json=payload)
    two = client.post('/api/crawler-accounts', json=payload)
    assert one.status_code == two.status_code == 200
    assert one.json()['item']['route_mode'] == two.json()['item']['route_mode'] == 'proxy_preferred'
    assert {one.json()['item']['proxy_id'], two.json()['item']['proxy_id']} == {first['id'], second['id']}
    assert client.post('/api/crawler-accounts', json=payload).status_code == 409
    assert len(accounts.list()) == 2
    assert all(auth.owns_crawler_account(users['alice']['id'], item['id']) for item in accounts.list())
    response = client.get('/api/crawler-proxies')
    assert response.status_code == 200
    assert all(not {'endpoint','observed_exit_ip','shared_exit_key'} & item.keys() for item in response.json()['items'])


def test_default_pool_retries_capacity_race_and_rolls_back_ownership_failure(api, monkeypatch):
    client, _, accounts, proxies, _, _ = api
    monkeypatch.setattr(settings, 'crawler_safety_enabled', True)
    pool = [create_proxy(client, expires_at=time.time()+3600) for _ in range(2)]
    monkeypatch.setattr(settings, 'crawler_proxy_pool_ids', tuple(item['id'] for item in pool))
    original = proxies.bind
    calls = []
    def race(account_id, proxy_id, **kwargs):
        calls.append(proxy_id)
        if len(calls) == 1:
            raise ValueError('PROXY_CAPACITY_CONFLICT')
        return original(account_id, proxy_id, **kwargs)
    monkeypatch.setattr(proxies, 'bind', race)
    response = client.post('/api/crawler-accounts', json={'platform':'dy','display_name':'race'})
    assert response.status_code == 200 and len(set(calls)) == 2
    assert len(accounts.list()) == 1
    def reject(*args):
        raise RuntimeError('fixture ownership failure')
    monkeypatch.setattr(main.auth_store, 'register_crawler_account', reject)
    import pytest
    with pytest.raises(RuntimeError, match='fixture ownership failure'):
        client.post('/api/crawler-accounts', json={'platform':'dy','display_name':'rollback'})
    assert len(accounts.list()) == 1
