"""Phase 1B: explicit UI choices, not inferred edit/save/model activity."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import subprocess
import sys

import pytest

from backend.investigation_creation.principal import Principal
from backend.resource_management.selections import ResourceSelectionWriter, initialize
from backend.resource_management.session_state import ResourceStateError
from test_session_resource_state import prepared, creation_stack, reader, snapshot, P


def request(f, identifier=None, version=4, purpose='edit', event_id='choose-b', previous=''):
    target = reader(f['stack']).detail(f['session'], f'edit/{identifier or f["b"]}/{version}', principal=P)
    return dict(kind=target['kind'], purpose=purpose, key=target['key'],
                content_hash=target['content_hash'], event_id=event_id, expected_event_id=previous)


def choose(f, body, principal=P):
    return ResourceSelectionWriter(f['stack']['creation_store'], reader(f['stack'])).record(
        f['session'], principal=principal, **body)


def selections(f):
    return reader(f['stack']).read(f['session'], principal=P, limit=100)['selection']


def test_explicit_choice_survives_read_save_refresh_and_report(prepared):
    f = prepared
    body = request(f)
    choose(f, body)
    manager = f['stack']['app_service'].resource_management
    manager.get_edit(f['a'], session_id=f['session'], principal=P)
    manager.save(f['a'], 1, 'copy', 'save-a-again', session_id=f['session'], principal=P)
    # Viewing A is a different slot; it must not change the edit choice B.
    choose(f, request(f, f['a'], 1, 'view', 'view-a'))
    with f['stack']['creation_store']._connect() as db:
        db.execute("UPDATE investigation_runs SET status='PUBLISHED' WHERE id=?", (f['run'],))
    before = snapshot(f['stack'])
    paths = [str(p) for p in (reader(f['stack']).creation_db, reader(f['stack']).resource_db,
                              reader(f['stack']).conversation_db)]
    script = '''import sys,json
from backend.resource_management.session_state import SessionResourceReader
from backend.investigation_creation.principal import Principal
print(json.dumps(SessionResourceReader(*sys.argv[1:4]).read(sys.argv[4], principal=Principal('principal-a'),limit=100)))'''
    result = json.loads(subprocess.run([sys.executable, '-B', '-c', script, *paths, f['session']],
                                      capture_output=True, text=True, check=True, timeout=30).stdout)
    slots = {s['purpose']: s for s in result['selection']['slots']}
    assert slots['edit']['key'] == body['key']
    assert slots['edit']['source'] == 'ui'
    assert slots['view']['key'] == f'edit/{f["a"]}/1'
    task = next(i for i in result['items'] if i['type'] == 'run')
    assert task['ruleset']['proposal_version'] == 3
    assert snapshot(f['stack']) == before


def test_no_choice_is_inferred_from_chat_generation_save_or_adoption(prepared):
    # Fixture really executes chat generation, update, adoption and task freeze.
    assert selections(prepared) == {'status': 'unknown'}


def test_replay_after_lost_response_does_not_reselect_old_target(prepared):
    f = prepared
    body = request(f)
    first = choose(f, body)  # simulate losing this committed response
    choose(f, request(f, f['a'], 1, event_id='choose-a', previous='choose-b'))
    recovered = choose(f, body)
    assert recovered['event'] == first['event']
    assert recovered['replayed'] is True
    assert selections(f)['slots'][0]['event_id'] == 'choose-a'
    with f['stack']['creation_store']._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM resource_selection_events').fetchone()[0] == 2


def test_event_reuse_and_stale_tab_conflicts(prepared):
    f = prepared
    choose(f, request(f))
    for body in (request(f, f['a'], 1), request(f, f['a'], 1, event_id='stale-tab')):
        with pytest.raises(ResourceStateError) as error:
            choose(f, body)
        assert error.value.status_code == 409
    assert selections(f)['slots'][0]['key'] == f'edit/{f["b"]}/4'


def test_concurrent_selection_only_one_wins(prepared):
    f = prepared
    bodies = [request(f, event_id='one'), request(f, f['a'], 1, event_id='two')]
    def write(body):
        try:
            return choose(f, body)
        except ResourceStateError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, bodies))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert 'RESOURCE_SELECTION_CONFLICT' in results


def test_stale_resource_is_not_silently_upgraded(prepared):
    f = prepared
    body = request(f)
    choose(f, body)
    f['stack']['app_service'].resource_management.update(f['b'], 4,
        [{'operation': 'set_metadata', 'values': {'name': 'B v5'}}], session_id=f['session'], principal=P)
    slot = selections(f)['slots'][0]
    assert slot['key'] == body['key'] and slot['status'] == 'stale'
    with pytest.raises(ResourceStateError) as error:
        choose(f, {**body, 'event_id': 'new-choice', 'expected_event_id': 'choose-b'})
    assert error.value.code == 'RESOURCE_SELECTION_STALE'
    # Historical versions remain selectable for viewing, never as current edits.
    choose(f, request(f, version=3, purpose='view', event_id='view-frozen-version'))


def test_kind_slots_clear_and_aba_are_explicit(prepared):
    f = prepared
    choose(f, request(f))
    choose(f, request(f, f['words'], 2, event_id='choose-words'))
    assert len(selections(f)['slots']) == 2
    cleared = dict(kind='ruleset', purpose='edit', key='', content_hash='',
                   event_id='clear', expected_event_id='choose-b')
    choose(f, cleared)
    assert next(s for s in selections(f)['slots'] if s['kind'] == 'ruleset')['status'] == 'cleared'
    with pytest.raises(ResourceStateError):
        choose(f, request(f, event_id='old-page'))
    choose(f, request(f, event_id='reselect', previous='clear'))


def test_unavailable_target_never_falls_back_to_previous_selection(prepared):
    f = prepared
    choose(f, request(f, f['a'], 1, event_id='choose-a'))
    choose(f, request(f, previous='choose-a'))
    with f['stack']['creation_store']._connect() as db:
        db.execute('DELETE FROM ruleset_proposals WHERE proposal_id=?', (f['b'],))
    slot = selections(f)['slots'][0]
    assert slot['status'] == 'unavailable'
    assert 'key' not in slot  # no leaked or silently substituted reference


def test_api_validation_scope_and_no_hidden_read_writes(prepared):
    f = prepared
    stack = f['stack']
    client = stack['client']
    url = f'/api/investigation-workspaces/{f["session"]}/resource-selection'
    body = request(f)
    for changes in ({'purpose': 'adopt'}, {'source': 'chat'}, {'key': body['key'], 'content_hash': 'wrong'},
                    {'kind': 'lexicon'}):
        assert client.post(url, json={**body, **changes}).status_code in (409, 422)
    for principal in (Principal('other'), Principal('admin', role='admin')):
        stack['principals'].current = principal
        assert client.post(url, json=body).status_code == 404
    stack['principals'].current = P
    assert client.post(url, json=body).status_code == 200
    before = snapshot(stack)
    for _ in range(2):
        assert client.get(url.replace('resource-selection', 'resource-state')).status_code == 200
        reader(stack).detail(f['session'], body['key'], principal=P)
    assert snapshot(stack) == before


def test_same_user_other_session_cannot_select_resource(prepared):
    f = prepared
    other = f['stack']['conversation'].create_session(principal=P, workspace_key='other')
    with pytest.raises(ResourceStateError) as error:
        ResourceSelectionWriter(f['stack']['creation_store'], reader(f['stack'])).record(
            other.id, principal=P, **request(f))
    assert error.value.status_code == 404


def test_additive_migration_does_not_rewrite_existing_tables(prepared):
    f = prepared
    with f['stack']['creation_store']._connect() as db:
        db.execute('DROP TABLE resource_selection_events')
    before = snapshot(f['stack'])
    assert selections(f) == {'status': 'unknown'}  # old DB reads never migrate
    assert snapshot(f['stack']) == before
    with f['stack']['creation_store']._connect() as db:
        initialize(db)
        initialize(db)
        db.execute('DROP TABLE resource_selection_events')
    assert snapshot(f['stack']) == before


def test_insert_failure_rolls_back_and_retry_succeeds(prepared):
    f = prepared
    with f['stack']['creation_store']._connect() as db:
        db.execute("CREATE TRIGGER fail_selection AFTER INSERT ON resource_selection_events BEGIN SELECT RAISE(ABORT, 'injected'); END")
    before = snapshot(f['stack'])
    with pytest.raises(ResourceStateError) as error:
        choose(f, request(f))
    assert error.value.status_code == 503
    assert snapshot(f['stack']) == before
    with f['stack']['creation_store']._connect() as db:
        db.execute('DROP TRIGGER fail_selection')
    choose(f, request(f))


def test_selection_change_invalidates_directory_pagination(prepared):
    f = prepared
    cursor = reader(f['stack']).read(f['session'], principal=P, limit=1)['next_cursor']
    choose(f, request(f))
    with pytest.raises(ResourceStateError) as error:
        reader(f['stack']).read(f['session'], principal=P, limit=1, cursor=cursor)
    assert error.value.code == 'RESOURCE_STATE_STALE'


def test_concurrent_edit_and_selection_keep_exact_version(prepared):
    f = prepared
    body = request(f)
    def write_selection():
        try:
            return choose(f, body)
        except ResourceStateError as exc:
            assert exc.code == 'RESOURCE_SELECTION_STALE'
            return None
    def edit():
        return f['stack']['app_service'].resource_management.update(f['b'], 4,
            [{'operation': 'set_metadata', 'values': {'name': 'B v5'}}], session_id=f['session'], principal=P)
    with ThreadPoolExecutor(max_workers=2) as pool:
        selected, updated = pool.submit(write_selection), pool.submit(edit)
        result = selected.result()
        assert updated.result()['version'] == 5
    if result:
        assert selections(f)['slots'][0]['status'] == 'stale'
        assert selections(f)['slots'][0]['key'] == body['key']
    else:
        assert selections(f) == {'status': 'unknown'}


def test_old_database_upgrades_through_real_store_startup(prepared):
    from backend.investigation_creation.store import InvestigationCreationStore
    f = prepared
    stack = f['stack']
    with stack['creation_store']._connect() as db:
        db.execute('DROP TABLE resource_selection_events')
    before = snapshot(stack)
    for _ in range(2):
        InvestigationCreationStore(stack['creation_store'].db_path, account_db=stack['resource_db'])
    choose(f, request(f))
    # Only the new additive state may differ, including after old code ignores it.
    with stack['creation_store']._connect() as db:
        db.execute('DROP TABLE resource_selection_events')
    assert snapshot(stack) == before


def test_workspace_deletion_removes_only_its_selections(creation_stack, tmp_path):
    from test_ruleset_proposal_presentation import run, evidence
    from test_resource_library_editor import rules
    from test_workspace_deletion import install
    stack = creation_stack
    deletion = install(stack, tmp_path)
    first = run(stack, rules())
    other = run(stack, rules(), client_message_id='keep-selection')
    for result in (first, other):
        session = result['session_id']
        item = reader(stack).detail(session, f'edit/{evidence(result)["proposal_id"]}/1', principal=P)
        ResourceSelectionWriter(stack['creation_store'], reader(stack)).record(session, principal=P,
            event_id='choice', kind='ruleset', purpose='edit', key=item['key'], content_hash=item['content_hash'])
    deletion.delete(first['session_id'], principal_id=P.id)
    assert reader(stack).read(other['session_id'], principal=P)['selection']['status'] == 'recorded'
    with stack['creation_store']._connect() as db:
        assert db.execute('SELECT DISTINCT session_id FROM resource_selection_events').fetchall()[0][0] == other['session_id']
        assert db.execute('SELECT COUNT(*) FROM resource_selection_events').fetchone()[0] == 1
    # Even clearing an empty slot must recheck the surviving session, not recreate an orphan.
    with pytest.raises(ResourceStateError) as exc:
        ResourceSelectionWriter(stack['creation_store'], reader(stack)).record(first['session_id'], principal=P,
            event_id='late-clear', kind='ruleset', purpose='view', key='', content_hash='')
    assert exc.value.status_code == 404


def test_interrupted_schema_upgrade_is_atomic_and_retryable(prepared):
    stack = prepared['stack']
    with stack['creation_store']._connect() as db:
        db.execute('DROP TABLE resource_selection_events')
    before = snapshot(stack)
    with pytest.raises(sqlite3.DatabaseError):
        with stack['creation_store']._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.set_authorizer(lambda action, name, *args: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_CREATE_INDEX and name == 'resource_selection_slot' else sqlite3.SQLITE_OK)
            initialize(db)
    assert snapshot(stack) == before
    with stack['creation_store']._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        initialize(db)
    choose(prepared, request(prepared))
