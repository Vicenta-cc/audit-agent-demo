"""The editor HTTP endpoint imports content; only the Agent tool generates it."""
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from backend.investigation_creation.principal import Principal
from backend.resource_management.api import create_resource_router
from backend.resource_management.generation import ResourceGenerator
from test_investigation_creation_conversation import creation_stack


CONTENT = {'title': '招聘风险词库', 'entries': [
    {'id': 'main', 'term': '招聘风险', 'kind': 'main'},
    {'id': 'variant', 'term': '招聘押金', 'kind': 'variant', 'parent_id': 'main'},
]}
REQUEST = {'objective': '招聘风险调查', 'platform': 'dy'}


@pytest.fixture
def api(creation_stack):
    stack = creation_stack
    app = FastAPI()
    app.include_router(create_resource_router(stack['app_service'], stack['conversation'], stack['principals']))
    session = stack['conversation'].create_session(principal=Principal('principal-a'))
    manager = stack['app_service'].resource_management
    path = f'/api/investigation-workspaces/{session.id}/resource-edits/lexicon'
    with TestClient(app, raise_server_exceptions=False) as client:
        with patch.object(ResourceGenerator, 'generate', side_effect=AssertionError('HTTP editor must not call a model')) as generate:
            yield stack, manager, client, path
            generate.assert_not_called()


@pytest.mark.parametrize('payload,expected', [
    ({'content': CONTENT}, 200),
    ({'generation_request': REQUEST}, 422),
    ({}, 422),
    ({'content': CONTENT, 'generation_request': REQUEST}, 422),
    ({'content': None}, 422),
    ({'content': {'entries': []}}, 422),
    ({'content': CONTENT, 'unexpected': True}, 422),
    ({'content': {'title': '坏结构', 'entries': [
        {'id': 'v', 'term': '招聘押金', 'kind': 'variant', 'parent_id': 'missing'},
    ]}}, 422),
])
def test_http_input_matrix_no_500_or_unintended_mutation(api, payload, expected):
    stack, manager, client, path = api
    response = client.post(path, json=payload)
    assert response.status_code == expected, response.text
    with stack['creation_store']._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM lexicon_edits').fetchone()[0] == (expected == 200)
        assert conn.execute('SELECT COUNT(*) FROM investigation_drafts').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM investigation_runs').fetchone()[0] == 0
    with manager.lexicons._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM resource_save_receipts').fetchone()[0] == 0
    if expected == 200:
        assert response.json()['search_terms'] == ['招聘押金']
        assert response.json()['saved'] is False
    else:
        assert isinstance(response.json()['detail'], list)


def test_http_import_save_read_roundtrip(api):
    _, _, client, path = api
    edit = client.post(path, json={'content': CONTENT}).json()
    edit_path = path.rsplit('/', 1)[0] + '/' + edit['edit_id']
    saved = client.post(edit_path + '/save', json={
        'expected_version': edit['version'], 'mode': 'new', 'operation_id': 'http-save',
    })
    assert saved.status_code == 200, saved.text
    read = client.get('/api/resource-library/lexicon/' + saved.json()['resource_id'])
    assert read.status_code == 200, read.text
    assert read.json()['content'] == edit['content']
    assert read.json()['search_terms'] == ['招聘押金']


def test_http_cannot_create_edit_in_another_users_session(api):
    stack, _, client, path = api
    stack['principals'].current = Principal('principal-b')
    response = client.post(path, json={'content': CONTENT})
    assert response.status_code in (403, 404), response.text
    with stack['creation_store']._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM lexicon_edits').fetchone()[0] == 0


def test_http_still_requires_authentication(creation_stack):
    def unauthenticated():
        raise HTTPException(status_code=401, detail='authentication required')
    app = FastAPI()
    app.include_router(create_resource_router(creation_stack['app_service'], creation_stack['conversation'], unauthenticated))
    with TestClient(app) as client:
        response = client.post('/api/investigation-workspaces/example/resource-edits/lexicon', json={'content': CONTENT})
    assert response.status_code == 401


def test_openapi_editor_schema_requires_only_content(api):
    _, _, client, _ = api
    spec = client.get('/openapi.json').json()
    request = spec['paths']['/api/investigation-workspaces/{session_id}/resource-edits/lexicon']['post']['requestBody']
    reference = request['content']['application/json']['schema']['$ref'].split('/')[-1]
    body = spec['components']['schemas'][reference]
    assert body['required'] == ['content']
    assert set(body['properties']) == {'content'}
    assert body['additionalProperties'] is False


def test_card_edit_then_agent_adoption_uses_new_version_without_copying(api):
    from test_investigation_creation_conversation import _t1_temporary_arguments
    from backend.resource_management.snapshot_refs import tool_view
    from backend.resource_management.contracts import ResourceError
    stack, manager, client, path = api
    old = client.post(path, json={'content': CONTENT}).json()
    session_id = path.split('/')[3]
    ctx = dict(session_id=session_id, principal=Principal('principal-a'))
    edit_path = path.rsplit('/', 1)[0] + '/' + old['edit_id']
    response = client.patch(edit_path, json={'expected_version': 1, 'changes': [
        {'operation': 'upsert_entry', 'target_id': 'variant',
         'values': {'term': '招聘培训收费', 'note': '引号 "保留" 与反斜线 \\ 完整保存'}}]})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated['version'] == 2
    assert updated['resource_ref'] != old['resource_ref']
    with pytest.raises(ResourceError) as error:
        manager.resolve_lexicon_ref(old['resource_ref'], **ctx)
    assert error.value.code == 'RESOURCE_REF_STALE'
    selected = stack['tool_service'].execute('get_resource_edit', {'edit_id': old['edit_id']}, **ctx)
    assert selected['recall_plan'] == tool_view(updated)['recall_plan']
    args = _t1_temporary_arguments(stack)
    args['configuration']['investigation']['recall_plan'] = selected['recall_plan']
    result = stack['tool_service'].execute('create_investigation_draft', args, **ctx)
    plan = result['draft']['configuration']['investigation']['recall_plan']
    assert plan['terms'] == ['招聘培训收费']
    assert plan['lexicon_content']['entries'][1]['note'] == updated['content']['entries'][1]['note']
