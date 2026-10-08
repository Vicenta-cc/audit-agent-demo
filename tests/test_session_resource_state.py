"""Phase 1A: recover business facts from disk without invoking an Agent or writes."""
import json
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import pytest

from backend.investigation_creation.contracts import ConfirmAndQueueCommand
from backend.investigation_creation.principal import Principal
from backend.resource_management.api import create_resource_router
from backend.resource_management.session_state import SessionResourceReader, ResourceStateError
from test_investigation_creation_conversation import creation_stack, _run_scripted_creation_turn
from test_ruleset_proposal_presentation import run, evidence
from test_ruleset_proposal_approval import approve
from test_resource_library_editor import rules
from test_resource_lifecycle import lexicon


P = Principal('principal-a')


def reader(stack):
    return SessionResourceReader(stack['creation_store'].db_path, stack['resource_db'],
                                 stack['conversation'].store.db_path)


def snapshot(stack):
    result = []
    for path in (stack['creation_store'].db_path, stack['resource_db'],
                 stack['conversation'].store.db_path):
        with sqlite3.connect(path) as conn:
            result.append('\n'.join(conn.iterdump()))
    return result


@pytest.fixture
def prepared(creation_stack):
    stack = creation_stack
    first = run(stack, rules())
    session_id = first['session_id']
    app = stack['app_service']
    manager = app.resource_management
    ctx = dict(session_id=session_id, principal=P)
    a = evidence(first)['proposal_id']
    saved = manager.save(a, 1, 'new', 'save-a', **ctx)
    body = rules()
    body['name'] = 'Candidate B'
    second = run(stack, body, session_id=session_id, client_message_id='create-b')
    b = evidence(second)['proposal_id']
    for version in (2, 3):
        body['name'] = f'Candidate B v{version}'
        second = _run_scripted_creation_turn(stack, content=f'修改 B 至 {version}',
            session_id=session_id, client_message_id=f'edit-b-{version}',
            actions=[('update_ruleset_proposal', dict(proposal_id=b,
                expected_version=version-1, content=deepcopy(body)))])
    result, _, _ = approve(stack, second)
    assert result['status'] == 'ok', result
    draft = result['data']['draft']
    task = app.confirm_and_queue(ConfirmAndQueueCommand(draft_id=draft['id'],
        expected_revision=1, confirmed=True, idempotency_key='freeze-b-v3'), principal=P)
    body['name'] = 'Candidate B v4'
    app.update_ruleset_proposal(b, session_id=session_id, expected_version=3, content=body)
    words = manager.create_lexicon(lexicon(), **ctx)
    manager.save(words['edit_id'], 1, 'new', 'save-words', **ctx)
    manager.update(words['edit_id'], 1,
                   [{'operation': 'set_metadata', 'values': {'title': '词库新版'}}], **ctx)
    # Another candidate with the same title must not be collapsed by name/hash.
    other = manager.create_lexicon(lexicon(), **ctx)
    stack['client'].app.include_router(create_resource_router(app, stack['conversation'], stack['principals']))
    return dict(stack=stack, session=session_id, a=a, b=b, saved=saved, run=task.id,
                words=words['edit_id'], other=other['edit_id'])


def test_fresh_process_recovers_versions_receipts_and_frozen_task_without_history(prepared):
    f = prepared
    stack = f['stack']
    before = snapshot(stack)
    paths = [str(stack['creation_store'].db_path), str(stack['resource_db']),
             str(stack['conversation'].store.db_path)]
    script = '''import json,sys,sqlite3
from backend.resource_management.session_state import SessionResourceReader
from backend.investigation_creation.principal import Principal
original_connect=sqlite3.connect
def connect(*args, **kwargs):
    conn=original_connect(*args, **kwargs)
    conn.set_authorizer(lambda action, table, *unused:
        sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table in
        {'investigation_messages', 'investigation_turns', 'investigation_events'}
        else sqlite3.SQLITE_OK)
    return conn
sqlite3.connect=connect
r=SessionResourceReader(*sys.argv[1:4])
print(json.dumps(r.read(sys.argv[4],principal=Principal('principal-a'),limit=100)))
'''
    completed = subprocess.run([sys.executable, '-B', '-c', script, *paths, f['session']],
                               capture_output=True, text=True, check=True, timeout=30)
    state = json.loads(completed.stdout)
    assert state['status'] == 'complete'
    assert state['selection'] == {'status': 'unknown'}
    edits = [i for i in state['items'] if i['type'] == 'edit_version']
    assert {i['version'] for i in edits if i['id'] == f['b']} == {1, 2, 3, 4}
    assert [i['version'] for i in edits if i['id'] == f['b'] and i['is_current']] == [4]
    assert {f['a'], f['b'], f['words'], f['other']} <= {i['id'] for i in edits}
    save = next(i for i in state['items'] if i['type'] == 'save_receipt' and i['operation_id'] == 'save-a')
    assert save['edit_id'] == f['a'] and save['edit_version'] == 1
    assert save['resource_id'] == f['saved']['resource_id']
    frozen = next(i for i in state['items'] if i['type'] == 'run')
    assert frozen['id'] == f['run']
    assert frozen['ruleset']['proposal_id'] == f['b']
    assert frozen['ruleset']['proposal_version'] == 3
    assert frozen['ruleset']['lineage'] == 'verified_edit_version'
    detail = reader(stack).detail(f['session'], frozen['key'], principal=P)
    assert detail['content']['temporary_ruleset']['content']['name'] == 'Candidate B v3'
    assert 'execution' not in detail['content']
    assert snapshot(stack) == before


def test_http_summary_detail_and_repeated_reads_have_no_write_side_effects(prepared):
    f = prepared
    stack = f['stack']
    base = f"/api/investigation-workspaces/{f['session']}/resource-state"
    before = snapshot(stack)
    for _ in range(2):
        response = stack['client'].get(base, params={'limit': 100})
        assert response.status_code == 200, response.text
        assert all('content' not in i for i in response.json()['items'])
        current = next(i for i in response.json()['items'] if i['type'] == 'edit_version'
                       and i['id'] == f['b'] and i['version'] == 4)
        detail = stack['client'].get(base+'/detail', params={'key': current['key']})
        assert detail.status_code == 200, detail.text
        assert detail.json()['content']['name'] == 'Candidate B v4'
    assert snapshot(stack) == before


@pytest.mark.parametrize('actor', [Principal('other-user'), Principal('other-admin', role='admin')])
def test_other_users_cannot_read_directory_or_details(prepared, actor):
    f = prepared
    stack = f['stack']
    before = snapshot(stack)
    stack['principals'].current = actor
    base = f"/api/investigation-workspaces/{f['session']}/resource-state"
    assert stack['client'].get(base).status_code == 404
    assert stack['client'].get(base+'/detail', params={'key': f"edit/{f['b']}/3"}).status_code == 404
    assert snapshot(stack) == before


def test_same_owner_other_session_and_report_session_do_not_expose_edits(prepared):
    f = prepared
    stack = f['stack']
    other = stack['conversation'].create_session(principal=P, workspace_key='other-workspace')
    r = reader(stack)
    assert r.read(other.id, principal=P)['items'] == []
    with pytest.raises(ResourceStateError) as error:
        r.detail(other.id, f"edit/{f['b']}/3", principal=P)
    assert error.value.status_code == 404
    with sqlite3.connect(stack['conversation'].store.db_path) as conn:
        conn.execute("UPDATE investigation_sessions SET scope_type='report' WHERE id=?", (other.id,))
    with pytest.raises(ResourceStateError):
        r.read(other.id, principal=P)


def test_pagination_is_stable_and_rejects_changes_instead_of_mixing_versions(prepared):
    f = prepared
    r = reader(f['stack'])
    page = r.read(f['session'], principal=P, limit=2)
    all_items = list(page['items'])
    cursor = page['next_cursor']
    while cursor:
        page = r.read(f['session'], principal=P, limit=2, cursor=cursor)
        all_items += page['items']
        cursor = page['next_cursor']
    assert all_items == r.read(f['session'], principal=P, limit=100)['items']
    first = r.read(f['session'], principal=P, limit=2)
    f['stack']['app_service'].resource_management.create_lexicon(lexicon(), session_id=f['session'], principal=P)
    with pytest.raises(ResourceStateError) as error:
        r.read(f['session'], principal=P, limit=2, cursor=first['next_cursor'])
    assert error.value.code == 'RESOURCE_STATE_STALE'
    with pytest.raises(ResourceStateError):
        r.read(f['session'], principal=P, cursor='not-a-cursor')


def test_legacy_missing_optional_tables_is_reported_without_migration(prepared):
    f = prepared
    stack = f['stack']
    with sqlite3.connect(stack['creation_store'].db_path) as conn:
        conn.execute('DROP TABLE resource_edit_history')
        conn.execute('DROP TABLE draft_creation_operations')
    before = snapshot(stack)
    state = reader(stack).read(f['session'], principal=P, limit=100)
    assert state['status'] == 'partial'
    assert 'history_unavailable' in state['issues']
    frozen = next(i for i in state['items'] if i['type'] == 'run')
    assert frozen['ruleset']['proposal_version'] == 3
    assert frozen['ruleset']['lineage'] == 'unknown'
    assert snapshot(stack) == before


def test_missing_or_corrupt_database_is_not_an_empty_directory(prepared, tmp_path):
    f = prepared
    stack = f['stack']
    missing = tmp_path/'does-not-exist.sqlite3'
    r = SessionResourceReader(stack['creation_store'].db_path, missing, stack['conversation'].store.db_path)
    with pytest.raises(ResourceStateError) as error:
        r.read(f['session'], principal=P)
    assert error.value.status_code == 503
    assert not missing.exists()
    with sqlite3.connect(stack['resource_db']) as conn:
        conn.execute("UPDATE resource_save_receipts SET result_json='invalid-json'")
    response = stack['client'].get(f"/api/investigation-workspaces/{f['session']}/resource-state")
    assert response.status_code == 503
    assert 'items' not in response.json()


def test_unrelated_resources_and_cross_owner_origins_are_not_disclosed(prepared):
    f = prepared
    stack = f['stack']
    manager = stack['app_service'].resource_management
    secret = manager.save_library('ruleset', 'not-in-this-session', rules(), 0, 'private', principal=Principal('other'))
    with sqlite3.connect(stack['creation_store'].db_path) as conn:
        conn.execute('UPDATE resource_edit_origins SET principal_id=? WHERE edit_id=?', ('other', f['other']))
    state = reader(stack).read(f['session'], principal=P, limit=100)
    encoded = json.dumps(state)
    assert secret['resource_id'] not in encoded
    assert f['other'] not in encoded
    assert state['status'] == 'partial'


def test_published_task_can_be_read_without_touching_task_or_report(prepared):
    f = prepared
    stack = f['stack']
    with sqlite3.connect(stack['creation_store'].db_path) as conn:
        conn.execute("UPDATE investigation_runs SET status='PUBLISHED',report_version_id='report:test' WHERE id=?", (f['run'],))
    before = snapshot(stack)
    state = reader(stack).read(f['session'], principal=P, limit=100)
    task = next(i for i in state['items'] if i['type'] == 'run')
    assert task['report_version_id'] == 'report:test'
    assert task['ruleset']['proposal_version'] == 3
    assert snapshot(stack) == before


def test_saved_historical_formal_version_remains_readable_after_later_save(prepared):
    f = prepared
    stack = f['stack']
    manager = stack['app_service'].resource_management
    ctx = dict(session_id=f['session'], principal=P)
    edit = manager.open('ruleset', f['saved']['resource_id'], **ctx)
    changed = manager.update(edit['edit_id'], 1,
        [{'operation': 'set_metadata', 'values': {'name': 'A latest saved version'}}], **ctx)
    manager.save(edit['edit_id'], changed['version'], 'update', 'save-a-again', **ctx)
    before = snapshot(stack)
    r = reader(stack)
    state = r.read(f['session'], principal=P, limit=100)
    versions = [i for i in state['items'] if i['type'] == 'formal_resource'
                and i['id'] == f['saved']['resource_id'] and i['stage'] == 'published']
    assert {i['version'] for i in versions} == {1, 2}
    old = next(i for i in versions if i['version'] == 1)
    assert not old['is_current']
    assert r.detail(f['session'], old['key'], principal=P)['content']['name'] == rules()['name']
    assert snapshot(stack) == before


def test_cross_database_concurrent_save_is_explicitly_partial_not_mislinked(prepared, monkeypatch):
    from backend.resource_management import session_state
    f = prepared
    stack = f['stack']
    # WAL permits a genuine writer while the directory keeps its old read snapshot.
    with sqlite3.connect(stack['creation_store'].db_path) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
    original = session_state._read_database
    written = []

    @contextmanager
    def interleave(path):
        if Path(path) == stack['resource_db'] and not written:
            written.append(True)
            manager = stack['app_service'].resource_management
            ctx = dict(session_id=f['session'], principal=P)
            edit = manager.update(f['b'], 4,
                [{'operation': 'set_metadata', 'values': {'name': 'Concurrent B v5'}}], **ctx)
            manager.save(f['b'], edit['version'], 'new', 'concurrent-b', **ctx)
        with original(path) as conn:
            yield conn

    monkeypatch.setattr(session_state, '_read_database', interleave)
    state = reader(stack).read(f['session'], principal=P, limit=100)
    assert state['status'] == 'partial'
    assert 'save_edit_link_unverified' in state['issues']
    assert state['consistency'] == 'per_database_snapshot'
    assert state['revalidate_before_write'] is True
    saved = next(i for i in state['items'] if i.get('operation_id') == 'concurrent-b')
    assert saved['edit_version'] == 5 and saved['edit_link'] == 'unknown'
    assert [i['version'] for i in state['items'] if i['type'] == 'edit_version'
            and i['id'] == f['b'] and i['is_current']] == [4]
    monkeypatch.setattr(session_state, '_read_database', original)
    assert reader(stack).read(f['session'], principal=P)['status'] == 'complete'


def test_reader_connections_cannot_write_even_accidentally(prepared):
    from backend.resource_management.session_state import _read_database
    f = prepared
    before = snapshot(f['stack'])
    with _read_database(f['stack']['creation_store'].db_path) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE ruleset_proposals SET version=99")
    assert snapshot(f['stack']) == before


def test_conflicting_origin_in_another_session_is_not_treated_as_missing(prepared):
    f = prepared
    stack = f['stack']
    manager = stack['app_service'].resource_management
    edit = manager.open('ruleset', f['saved']['resource_id'], session_id=f['session'], principal=P)
    with sqlite3.connect(stack['creation_store'].db_path) as conn:
        conn.execute('UPDATE resource_edit_origins SET session_id=? WHERE edit_id=?', ('other', edit['edit_id']))
    state = reader(stack).read(f['session'], principal=P, limit=100)
    assert edit['edit_id'] not in json.dumps(state)
    assert 'unverified_edit_owner' in state['issues']


def test_missing_legacy_receipt_hash_cannot_verify_a_missing_edit_version(prepared):
    f = prepared
    stack = f['stack']
    with sqlite3.connect(stack['resource_db']) as conn:
        row = conn.execute("SELECT result_json FROM resource_save_receipts WHERE operation_id='save-a'").fetchone()
        value = json.loads(row[0])
        value.pop('edit_content_hash')
        value['edit_version'] = 99
        conn.execute("UPDATE resource_save_receipts SET result_json=? WHERE operation_id='save-a'", (json.dumps(value),))
    state = reader(stack).read(f['session'], principal=P, limit=100)
    saved = next(i for i in state['items'] if i.get('operation_id') == 'save-a')
    assert saved['edit_link'] == 'unknown'
    assert state['status'] == 'partial'


def test_inconsistent_frozen_content_is_not_claimed_to_match_edit_history(prepared):
    f = prepared
    stack = f['stack']
    with sqlite3.connect(stack['creation_store'].db_path) as conn:
        row = conn.execute('SELECT confirmed_configuration_json FROM investigation_runs WHERE id=?', (f['run'],)).fetchone()
        value = json.loads(row[0])
        value['temporary_ruleset']['content']['name'] = 'Wrong content with old v3 hash'
        conn.execute('UPDATE investigation_runs SET confirmed_configuration_json=? WHERE id=?', (json.dumps(value), f['run']))
    with pytest.raises(ResourceStateError) as error:
        reader(stack).read(f['session'], principal=P)
    assert error.value.status_code == 503
