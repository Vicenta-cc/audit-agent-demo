"""Tenant boundaries and durable saves, using isolated databases only."""
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.investigation_creation import create_investigation_creation_router
from backend.investigation_creation.contracts import ConfirmAndQueueCommand

from backend.investigation_creation.principal import Principal
from backend.resource_management.api import create_resource_router
from backend.resource_management.contracts import ResourceError
from backend.resource_management.service import ResourceManagementService
from backend.rulesets.errors import RuleSetNotFoundError
from test_resource_lifecycle import service, lexicon, P, CTX, save
from test_resource_library_editor import rules
from test_investigation_draft_configuration_m3 import m3_stack, _principal
from test_save_draft_lexicon_tool import make_draft


@pytest.mark.parametrize('kind,body', [('lexicon', lexicon()), ('ruleset', rules())])
def test_private_http_lifecycle_isolates_even_admin(service, kind, body):
    actor = [P]
    app = FastAPI()
    app.include_router(create_resource_router(SimpleNamespace(resource_management=service), None, lambda: actor[0]))
    path = '/api/resource-library/' + kind + '/tenant-resource'
    with TestClient(app) as client:
        saved = client.put(path, json={'content': body, 'expected_version': 0, 'operation_id': 'first'})
        assert saved.status_code == 200, saved.text
        for other in (Principal('user-b'), Principal('admin-b', role='admin')):
            actor[0] = other
            assert not any(i['id'] == 'tenant-resource' for i in client.get('/api/resource-library/' + kind).json()['items'])
            assert client.get(path).status_code == 404
            assert client.put(path, json={'content': body, 'expected_version': 1, 'operation_id': 'steal'}).status_code in (403, 404)
            assert client.request('DELETE', path, json={'expected_version': 1}).status_code in (403, 404)
        actor[0] = P
        assert client.get(path).json()['content']['entries' if kind == 'lexicon' else 'categories'] == saved.json()['content']['entries' if kind == 'lexicon' else 'categories']
        changed = deepcopy(body)
        changed['title' if kind == 'lexicon' else 'name'] = '本人修改'
        updated = client.put(path, json={'content': changed, 'expected_version': 1, 'operation_id': 'edit'})
        assert updated.status_code == 200, updated.text
        assert client.request('DELETE', path, json={'expected_version': updated.json()['version']}).status_code == 200


def test_published_rules_are_private_in_all_revision_operations(service):
    saved = service.save_library('ruleset', 'private-rule', rules(), 0, 'save-rule', principal=P)
    other = Principal('user-b')
    revision_id = saved['revision_id']
    assert not any(r['id'] == revision_id for r in service.rulesets.list_published(principal=other))
    assert not any(r['id'] == revision_id for r in service.rulesets.list_current_published(principal=other))
    for action in (
        lambda: service.rulesets.get_published(revision_id, principal=other),
        lambda: service.rulesets.get_current_published(revision_id, principal=other),
        lambda: service.rulesets.compile_for_execution(revision_id, principal=other),
        lambda: service.rulesets.fork_published(revision_id, principal=other),
    ):
        with pytest.raises(RuleSetNotFoundError):
            action()


def test_system_lexicon_read_only_copy_is_owned(service):
    resource = service.read('lexicon', 'soft', principal=P)
    assert not resource['editable']
    with pytest.raises(ResourceError):
        service.save_library('lexicon', 'soft', resource['content'], resource['version'], 'modify-system', principal=P)
    with pytest.raises(ResourceError):
        service.delete_library('lexicon', 'soft', resource['version'], principal=P)
    edit = service.open('lexicon', 'soft', **CTX)
    copied = save(service, edit, 'copy-system', 'copy')
    assert service.read('lexicon', copied['resource_id'], principal=P)['editable']
    with pytest.raises(ResourceError):
        service.read('lexicon', copied['resource_id'], principal=Principal('user-b'))


def test_legacy_low_level_editor_cannot_mutate_another_owner(service):
    saved = save(service, service.create_lexicon(lexicon(), **CTX))
    identifier = saved['resource_id']
    other = Principal('user-b')
    keyword_id = service.lexicons.get_category(identifier)['keywords'][0]['id']
    for action in (
        lambda: service.lexicons.upsert_category(category_id=identifier, title='overwrite', principal=other),
        lambda: service.lexicons.add_keyword(category_id=identifier, keyword='injected', principal=other),
        lambda: service.lexicons.update_keyword(keyword_id, keyword='injected', principal=other),
        lambda: service.lexicons.delete_keyword(keyword_id, principal=other),
        lambda: service.lexicons.delete_category_atomically(identifier, principal=other),
    ):
        with pytest.raises(ResourceError):
            action()
    assert service.read('lexicon', identifier, principal=P)['version'] == 1


def test_ownership_migration_preserves_unknown_and_conflicting_resources(service):
    for name in ('proved', 'unknown', 'conflicting'):
        service.lexicons.upsert_category(category_id=name, title=name, owner_id='')
    with service.lexicons._connect() as conn:
        for operation, resource_id, owner in [('a', 'proved', 'user-a'), ('b', 'conflicting', 'user-a'), ('c', 'conflicting', 'user-b')]:
            conn.execute('INSERT INTO resource_save_receipts VALUES (?,?,?,?,?,?)',
                         (owner, operation, '', 'legacy-hash', json.dumps({'kind': 'lexicon', 'resource_id': resource_id}), '2026'))
    restarted = ResourceManagementService(service.app)
    with restarted.lexicons._connect() as conn:
        owners = dict(conn.execute('SELECT id,owner_id FROM lexicon_categories'))
    assert owners['proved'] == 'user-a'
    assert owners['unknown'] == owners['conflicting'] == ''
    for principal in (Principal('user-a'), Principal('user-b'), Principal('admin', role='admin')):
        visible = {r['id'] for r in restarted.read('lexicon', principal=principal)['items']}
        assert 'unknown' not in visible and 'conflicting' not in visible
        assert ('proved' in visible) == (principal.id == 'user-a')


def test_snapshot_save_concurrent_operations_create_once_and_receipts_survive_restart(m3_stack):
    app = m3_stack['service']
    draft = make_draft(m3_stack)
    principal = _principal(m3_stack)
    def perform(index):
        return app.save_draft_lexicon(draft.id, expected_revision=draft.current_revision,
                                     operation_id=f'op-{index}', principal=principal, session_id='mine')
    with ThreadPoolExecutor(max_workers=3) as pool:
        receipts = list(pool.map(perform, range(6)))
    assert len({r['resource_id'] for r in receipts}) == 1
    restarted = ResourceManagementService(app)
    for i, receipt in enumerate(receipts):
        assert restarted.get_save(f'op-{i}', session_id='mine', principal=principal) == receipt
        assert restarted.get_save(f'op-{i}', session_id='other-session', principal=principal)['status'] == 'not_found'
        assert restarted.get_save(f'op-{i}', session_id='mine', principal=Principal('other-user'))['status'] == 'not_found'


def test_task_rule_snapshot_save_is_private_and_does_not_rewrite_draft(m3_stack):
    app = m3_stack['service']
    draft = make_draft(m3_stack)
    principal = _principal(m3_stack)
    before = draft.model_dump(mode='json')
    saved = app.save_draft_ruleset(draft.id, expected_revision=1, operation_id='save-rules', principal=principal)
    again = app.save_draft_ruleset(draft.id, expected_revision=1, operation_id='recover-rules', principal=principal)
    assert saved['resource_id'] == again['resource_id']
    assert app.get_draft(draft.id, principal=principal).model_dump(mode='json') == before
    with pytest.raises(Exception) as caught:
        app.save_draft_ruleset(draft.id, expected_revision=1, operation_id='steal-rules', principal=Principal('other'))
    assert getattr(caught.value, 'code', '')
    with pytest.raises(RuleSetNotFoundError):
        app.resource_management.read('ruleset', saved['resource_id'], principal=Principal('other'))


@pytest.mark.parametrize('kind', ['lexicon', 'ruleset'])
@pytest.mark.parametrize('state', ['draft', 'queued', 'running', 'failed', 'published'])
def test_snapshot_save_http_state_and_owner_matrix(m3_stack, kind, state):
    service = m3_stack['service']
    draft = make_draft(m3_stack)
    principal = _principal(m3_stack)
    actor = [principal]
    run = None
    if state != 'draft':
        run = service.confirm_and_queue(ConfirmAndQueueCommand(
            draft_id=draft.id, expected_revision=1, confirmed=True,
            idempotency_key='start'), principal=principal)
        with m3_stack['store']._connect() as conn:
            conn.execute('UPDATE investigation_runs SET status=? WHERE id=?', (state.upper(), run.id))
        snapshot = m3_stack['store'].get_run_for_worker(run.id).model_dump(mode='json')
    before = service.get_draft(draft.id, principal=principal).model_dump(mode='json')
    api = FastAPI()
    api.include_router(create_investigation_creation_router(service, principal_provider=lambda: actor[0]))
    path = f'/api/investigation-drafts/{draft.id}/save-{kind}'
    with TestClient(api) as client:
        assert client.post(path, json={'expected_revision': 99, 'operation_id': 'stale'}).status_code == 409
        saved = client.post(path, json={'expected_revision': 1, 'operation_id': 'save'})
        assert saved.status_code == 200, saved.text
        repeated = client.post(path, json={'expected_revision': 1, 'operation_id': 'save'})
        assert repeated.json() == saved.json()
        actor[0] = Principal('another-admin', role='admin')
        assert client.post(path, json={'expected_revision': 1, 'operation_id': 'steal'}).status_code in (403, 404)
    assert service.get_draft(draft.id, principal=principal).model_dump(mode='json') == before
    if run:
        assert m3_stack['store'].get_run_for_worker(run.id).model_dump(mode='json') == snapshot
