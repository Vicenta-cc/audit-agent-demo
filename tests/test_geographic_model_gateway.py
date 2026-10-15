"""Budget gateway tests never bind a socket or call a real transport."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest


def make(tmp_path, transport, limit=30):
    from experiments.geographic_runtime_integration.model_gateway import ModelGateway
    return ModelGateway(isolation_root=tmp_path, database=tmp_path/'model_gateway_budget.sqlite3',
        upstream_base='https://model.example/v1', upstream_authorization='Bearer server-secret',
        gateway_token='local-test-token', limit=limit, transport=transport)


def call(gateway):
    return gateway.forward('POST', '/v1/chat/completions', b'{"messages":[]}', authorization='Bearer local-test-token')


def test_default_budget_timeout_and_restart(tmp_path):
    def timeout(**request):
        raise TimeoutError('must never include secrets')
    gateway = make(tmp_path, timeout)
    assert call(gateway).status == 502
    assert gateway.snapshot()['used'] == 1
    restarted = make(tmp_path, lambda **request: (200, b'{"ok":true}', 'application/json'))
    assert restarted.snapshot()['used'] == 1
    assert call(restarted).status == 200
    assert restarted.snapshot()['used'] == 2


def test_concurrent_last_credit_exactly_one_forward(tmp_path):
    calls = []
    gateway = make(tmp_path, lambda **request: (calls.append(request['url']) or (200, b'{}', 'application/json')), limit=1)
    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(lambda _: call(gateway).status, range(24)))
    assert statuses.count(200) == 1
    assert statuses.count(429) == 23
    assert len(calls) == 1
    assert gateway.snapshot()['used'] == 1


@pytest.mark.parametrize('method,path,auth', [('GET','/v1/chat/completions','Bearer local-test-token'),
    ('POST','https://evil.example/v1/chat/completions','Bearer local-test-token'),
    ('POST','/v1/chat/completions?target=https://evil.example','Bearer local-test-token'),
    ('POST','/v1/responses','Bearer local-test-token'),('POST','/v1/chat/completions','Bearer wrong')])
def test_rejected_request_never_uses_budget(tmp_path, method, path, auth):
    gateway = make(tmp_path, lambda **request: pytest.fail('forbidden transport'))
    assert gateway.forward(method,path,b'{}',authorization=auth).status in {401,404,405}
    assert gateway.snapshot()['used'] == 0


def test_restart_cannot_change_limit_or_destination(tmp_path):
    gateway = make(tmp_path, lambda **request: (200,b'{}','application/json'), limit=1)
    call(gateway)
    with pytest.raises(ValueError, match='configuration'):
        make(tmp_path, lambda **request: None, limit=2)
    assert call(make(tmp_path, lambda **request: pytest.fail('budget exhausted'), limit=1)).status == 429


def test_budget_is_shared_across_independent_processes(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    def invoke(_):
        result = subprocess.run([sys.executable, '-m', 'experiments.geographic_runtime_integration.replay_gateway_budget', str(tmp_path)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
        return int(result.stdout.strip())
    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(invoke, range(12)))
    assert statuses.count(200) == 3
    assert statuses.count(429) == 9
    assert make(tmp_path, lambda **request: pytest.fail('no credit'), limit=3).snapshot() == {'limit':3,'used':3,'remaining':0}


def test_secrets_and_bodies_never_persist_or_log(tmp_path, capsys):
    seen = []
    def fake(**request):
        seen.append(request)
        raise RuntimeError('Bearer server-secret PRIVATE_BODY')
    gateway = make(tmp_path, fake)
    response = gateway.forward('POST','/v1/chat/completions',b'{"secret":"PRIVATE_BODY"}',authorization='Bearer local-test-token')
    assert response.status == 502
    assert seen[0]['url'] == 'https://model.example/v1/chat/completions'
    assert seen[0]['headers']['Authorization'] == 'Bearer server-secret'
    assert b'PRIVATE_BODY' not in response.body and b'server-secret' not in response.body
    for path in tmp_path.iterdir():
        if path.is_file():
            assert b'PRIVATE_BODY' not in path.read_bytes()
            assert b'server-secret' not in path.read_bytes()
            assert b'local-test-token' not in path.read_bytes()
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('upstream', ['http://model.example/v1','https://user:password@model.example/v1','https://model.example/v1?url=x','https://model.example/../../v1','https://model.example/arbitrary'])
def test_invalid_upstream_rejected_before_creating_database(tmp_path, upstream):
    from experiments.geographic_runtime_integration.model_gateway import ModelGateway
    with pytest.raises(ValueError, match='HTTPS'):
        ModelGateway(isolation_root=tmp_path,database=tmp_path/'model_gateway_budget.sqlite3',upstream_base=upstream,
            upstream_authorization='Bearer server-secret',gateway_token='local-test-token')
    assert not list(tmp_path.iterdir())


def test_database_guard_rejects_foreign_path_and_business_tables(tmp_path):
    from experiments.geographic_runtime_integration.model_gateway import ModelGateway
    kwargs = dict(isolation_root=tmp_path,upstream_base='https://model.example/v1',upstream_authorization='Bearer key',gateway_token='local')
    with pytest.raises(ValueError, match='database'):
        ModelGateway(database=tmp_path/'jobs.db',**kwargs)
    with pytest.raises(ValueError, match='database'):
        ModelGateway(database=tmp_path.parent/'model_gateway_budget.sqlite3',**kwargs)
    database=tmp_path/'model_gateway_budget.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE jobs(id TEXT)')
    with pytest.raises(ValueError, match='unrelated'):
        ModelGateway(database=database,**kwargs)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('jobs',)]


def test_http_errors_and_client_retries_each_consume_budget_without_refund(tmp_path):
    seen = []
    gateway=make(tmp_path,lambda **request:(seen.append(1) or (429,b'PRIVATE_ERROR','text/plain')),limit=2)
    assert call(gateway).status == 429
    assert call(gateway).status == 429
    assert call(gateway).body.find(b'model_budget_exhausted') >= 0
    assert len(seen) == 2 and gateway.snapshot()['used'] == 2


def test_real_transport_configuration_verifies_tls_disables_redirects_and_has_no_retry(monkeypatch):
    import ssl
    from experiments.geographic_runtime_integration import model_gateway as module
    from urllib.request import HTTPSHandler
    captured=[]
    class Opener:
        def open(self, request, timeout):
            captured.append(request.full_url)
            raise TimeoutError('offline')
    def build(*handlers):
        https=next(handler for handler in handlers if isinstance(handler,HTTPSHandler))
        assert https._context.verify_mode == ssl.CERT_REQUIRED
        assert https._context.check_hostname is True
        redirect=next(handler for handler in handlers if isinstance(handler,module._NoRedirect))
        assert redirect.redirect_request(None,None,302,'',{},'https://other.example') is None
        return Opener()
    monkeypatch.setattr(module,'build_opener',build)
    with pytest.raises(TimeoutError):
        module.https_transport(url='https://model.example/v1/chat/completions',body=b'{}',headers={},timeout=1)
    assert captured == ['https://model.example/v1/chat/completions']
