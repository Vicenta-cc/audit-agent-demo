"""A broken persisted workspace must not hide its owner's other sessions."""
import json
import sqlite3

import pytest

from backend.investigation_creation.principal import Principal
from test_investigation_creation_conversation import (
    creation_stack, _create_completed_turn, _publish_fake_run,
)


@pytest.mark.parametrize('corruption', ['invalid_json', 'invalid_snapshot'])
def test_bad_snapshot_isolated_with_pagination_ownership_and_recovery(creation_stack, caplog, corruption):
    stack = creation_stack
    conversation = stack['conversation']
    owner = stack['principals']()
    broken = _create_completed_turn(stack)
    run = _publish_fake_run(stack, broken, idempotency_key='isolation-test')
    healthy = conversation.create_session(principal=owner)
    hidden = conversation.create_session(principal=Principal('other-owner'))
    db_path = stack['creation_store'].db_path
    with sqlite3.connect(db_path) as conn:
        original = conn.execute('SELECT confirmed_configuration_json FROM investigation_runs WHERE id=?',
                                (run['run_id'],)).fetchone()[0]
        corrupted = '{private-input-do-not-log' if corruption == 'invalid_json' else json.dumps({
            **json.loads(original), 'execution': 'private-input-do-not-log',
        })
        conn.execute('UPDATE investigation_runs SET confirmed_configuration_json=? WHERE id=?',
                     (corrupted, run['run_id']))
    client = stack['client']
    pages = [client.get('/api/investigation-workspaces', params={'limit': 1, 'offset': offset})
             for offset in range(2)]
    assert all(page.status_code == 200 for page in pages)
    assert [page.json()['has_more'] for page in pages] == [True, False]
    items = [page.json()['items'][0] for page in pages]
    assert {item['workspace_session_id'] for item in items} == {broken['workspace_id'], healthy.id}
    fallback = next(item for item in items if item['workspace_session_id'] == broken['workspace_id'])
    assert fallback['read_error'] == 'WORKSPACE_READ_FAILED'
    assert fallback['title'] == '调查会话（暂时无法读取）'
    assert fallback['run_id'] == fallback['run_status'] == ''
    assert client.get(f'/api/investigation-workspaces/{healthy.id}/state').status_code == 200
    assert 'Workspace list projection failed' in caplog.text
    assert broken['workspace_id'] in caplog.text
    assert 'private-input-do-not-log' not in caplog.text
    assert 'private-input-do-not-log' not in str(items)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute('SELECT confirmed_configuration_json FROM investigation_runs WHERE id=?',
                            (run['run_id'],)).fetchone()[0] == corrupted
    stack['principals'].current = Principal('other-owner')
    assert [item['workspace_session_id'] for item in client.get('/api/investigation-workspaces').json()['items']] == [hidden.id]
    assert client.get(f"/api/investigation-workspaces/{broken['workspace_id']}/state").status_code == 404
    # Once the underlying record is repaired, no persisted error flag prevents recovery.
    with sqlite3.connect(db_path) as conn:
        conn.execute('UPDATE investigation_runs SET confirmed_configuration_json=? WHERE id=?',
                     (original, run['run_id']))
    stack['principals'].current = owner
    repaired = client.get('/api/investigation-workspaces').json()['items']
    assert all(not item['read_error'] for item in repaired)
    state = client.get(f"/api/investigation-workspaces/{broken['workspace_id']}/state")
    assert state.status_code == 200
    assert state.json()['run']['report_version_id'] == run['report_version_id']


@pytest.mark.parametrize('failure', [sqlite3.OperationalError('database is locked'), OSError('disk unavailable')])
def test_storage_outage_is_not_disguised_as_partial_success(creation_stack, monkeypatch, failure):
    conversation = creation_stack['conversation']
    principal = creation_stack['principals']()
    conversation.create_session(principal=principal)
    def unavailable(*args, **kwargs):
        raise failure
    monkeypatch.setattr(conversation, '_workspace_list_item', unavailable)
    with pytest.raises(type(failure)):
        conversation.list_workspaces(principal=principal)


def test_unexpected_projection_failure_is_isolated(creation_stack, monkeypatch):
    conversation = creation_stack['conversation']
    principal = creation_stack['principals']()
    bad = conversation.create_session(principal=principal)
    good = conversation.create_session(principal=principal)
    original = conversation._workspace_list_item
    def read(session_id, **kwargs):
        if session_id.id == bad.id:
            raise RuntimeError('projection bug')
        return original(session_id, **kwargs)
    monkeypatch.setattr(conversation, '_workspace_list_item', read)
    result = creation_stack['client'].get('/api/investigation-workspaces')
    assert result.status_code == 200
    by_id = {item['workspace_session_id']: item for item in result.json()['items']}
    assert by_id[bad.id]['read_error']
    assert not by_id[good.id]['read_error']


@pytest.mark.parametrize('deleted', [False, True])
def test_missing_related_record_is_visible_but_deleted_session_is_not(creation_stack, monkeypatch, deleted):
    from backend.investigation.errors import InvestigationSessionNotFoundError
    conversation = creation_stack['conversation']
    principal = creation_stack['principals']()
    session = conversation.create_session(principal=principal)
    def missing(*args, **kwargs):
        raise InvestigationSessionNotFoundError('missing related record')
    monkeypatch.setattr(conversation, '_workspace_list_item', missing)
    if deleted:
        monkeypatch.setattr(conversation, 'get_session', missing)
    items = conversation.list_workspaces(principal=principal)
    if deleted:
        assert items == []
    else:
        assert len(items) == 1
        assert items[0]['workspace_session_id'] == session.id
        assert items[0]['read_error'] == 'WORKSPACE_READ_FAILED'


def test_sidebar_reads_only_metadata_for_published_workspace(creation_stack, monkeypatch):
    stack = creation_stack
    result = _create_completed_turn(stack)
    published = _publish_fake_run(stack, result, idempotency_key="sidebar-metadata")
    conversation = stack["conversation"]
    report = conversation.store.find_session_by_anchor(f"m3-run:{published['run_id']}")
    report_turn, _ = conversation.store.create_turn(
        report.id, client_message_id="follow-up", user_input="report follow-up"
    )
    with sqlite3.connect(conversation.store.db_path) as conn:
        conn.execute("UPDATE investigation_messages SET created_at=? WHERE turn_id=?",
                     ("2099-01-01T00:00:00Z", report_turn.id))

    def forbidden(*args, **kwargs):
        pytest.fail("sidebar read the workspace detail payload")

    for obj, attr in (
        (conversation, "get_workspace_state"),
        (conversation.store, "conversation_snapshot"),
        (conversation.store, "latest_completed_hermes_transcript"),
        (stack["app_service"], "get_run"),
        (stack["app_service"].run_projector.report_store, "list_published_versions_for_task"),
    ):
        monkeypatch.setattr(obj, attr, forbidden)
    response = stack["client"].get("/api/investigation-workspaces")
    assert response.status_code == 200
    item, = response.json()["items"]
    assert item["title"] == result["terminal"]["artifact"]["draft"]["title"]
    assert item["run_id"] == published["run_id"]
    assert item["run_status"] == "PUBLISHED"
    assert item["updated_at"] == "2099-01-01T00:00:00Z"
    assert not item["read_error"]
