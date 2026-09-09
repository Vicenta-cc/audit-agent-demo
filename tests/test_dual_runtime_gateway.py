import importlib.util
import json
import sqlite3
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location('dual_runtime_gateway', Path(__file__).parents[1] / 'scripts/dual_runtime_gateway.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class Body(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"status":"running"}\n\n'
        yield b'data: {"status":"completed"}\n\n'


def test_durable_descendants_and_encoded_ids(tmp_path):
    with sqlite3.connect(tmp_path / 'investigation_creation.sqlite3') as c:
        c.execute('CREATE TABLE investigation_runs(id, draft_id, job_id, report_version_id, report_session_id)')
        c.execute('INSERT INTO investigation_runs VALUES (?,?,?,?,?)', ('run:1', 'draft:1', 'job1', 'report:1', 'session:report'))
    with sqlite3.connect(tmp_path / 'investigation.sqlite3') as c:
        c.execute('CREATE TABLE investigation_turns(id, session_id)')
        c.executemany('INSERT INTO investigation_turns VALUES (?,?)', [('turn:1', 'session:1'), ('turn:report', 'session:report')])
    owned = gateway.production_ids({'data_directory': str(tmp_path), 'workspace_ids': ['session:1'], 'draft_ids': ['draft:1']})
    for path in ('/api/investigation-workspaces/session%3A1/state', '/api/investigation-drafts/draft:1/confirm-and-queue',
                 '/api/investigation-workspace-turns/turn%3Areport/events', '/api/jobs/job1/artifacts', '/api/report-versions/report:1'):
        assert gateway.backend_for_path(path, owned) == 'production'
    assert gateway.backend_for_path('/api/investigation-workspaces', owned) == 'demo'
    assert gateway.backend_for_path('/api/jobs/job10', owned) == 'demo'


def test_streams_and_mutations_never_fall_back(tmp_path, monkeypatch):
    registry = tmp_path / 'registry.json'
    registry.write_text('{}')
    monkeypatch.setattr(gateway, 'production_ids', lambda _: {'session:prod'})
    calls = []
    fail = False

    async def handle(request):
        calls.append((request.url.host, request.method, request.content))
        if fail and request.url.host == 'production':
            raise httpx.ConnectError('offline', request=request)
        return httpx.Response(200, stream=Body(), headers={'Content-Type': 'text/event-stream'})

    app = gateway.create_app(registry, 'http://demo', 'http://production', httpx.MockTransport(handle))
    with TestClient(app) as client:
        response = client.get('/api/investigation-workspaces/session%3Aprod/events')
        assert response.headers['x-task-runtime'] == 'production'
        assert 'running' in response.text and 'completed' in response.text
        client.post('/api/investigation-workspaces', json={'workspace_key': 'new-demo'})
        assert calls[-1][0] == 'demo'
        fail = True
        before = len(calls)
        assert client.post('/api/investigation-workspaces/session%3Aprod/turns', json={'content': 'test'}).status_code == 502
        assert len(calls) == before + 1
        assert calls[-1][0] == 'production'


def test_list_merges_only_registered_tasks_and_survives_one_backend_failure(tmp_path):
    registry = tmp_path / 'registry.json'
    registry.write_text(json.dumps({'workspace_ids': ['p']}))
    fail = False

    async def handle(request):
        if request.url.host == 'production':
            if fail:
                raise httpx.ConnectError('offline', request=request)
            items = [{'workspace_session_id': 'p', 'updated_at': '2026-02'}, {'workspace_session_id': 'private', 'updated_at': '2026-03'}]
        else:
            items = [{'workspace_session_id': 'd', 'updated_at': '2026-01'}]
        return httpx.Response(200, json={'items': items, 'has_more': False})

    with TestClient(gateway.create_app(registry, 'http://demo', 'http://production', httpx.MockTransport(handle))) as client:
        result = client.get('/api/investigation-workspaces').json()
        assert [r['workspace_session_id'] for r in result['items']] == ['p', 'd']
        fail = True
        response = client.get('/api/investigation-workspaces')
        assert response.status_code == 200
        assert response.json()['unavailable_runtimes'] == ['production']
        assert response.json()['items'][0]['workspace_session_id'] == 'd'


def test_audit_reads_are_scoped_to_job_not_colliding_numeric_ids(tmp_path, monkeypatch):
    registry = tmp_path / 'registry.json'
    registry.write_text('{}')
    monkeypatch.setattr(gateway, 'production_ids', lambda _: {'job:prod'})

    async def handle(request):
        return httpx.Response(200, stream=Body(), headers={'x-test-backend': request.url.host})

    with TestClient(gateway.create_app(registry, 'http://demo', 'http://production', httpx.MockTransport(handle))) as client:
        for path in ('/api/audit-results', '/api/audit-results/813'):
            assert client.get(path, params={'job_id': 'job:prod'}).headers['x-test-backend'] == 'production'
            assert client.get(path, params={'job_id': 'job:demo'}).headers['x-test-backend'] == 'demo'
            assert client.get(path).headers['x-test-backend'] == 'demo'
