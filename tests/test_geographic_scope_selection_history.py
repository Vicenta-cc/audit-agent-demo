"""Durable UI geography history is public state, never fabricated chat text."""
import json

import pytest

from backend.investigation_creation import scope_selection
from backend.investigation_creation.errors import (
    ConfigurationValidationError, PrincipalAccessDeniedError, IdempotencyConflictError,
)
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.store import InvestigationCreationStore
from test_geographic_draft_configuration import change, flow, no_effects, setup_geo, stored
from test_geographic_scope_intent import catalog
from test_geographic_scope_selection import select, resource_snapshot
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_unified_draft_configuration import P, prepare


def history(stack, session_id, principal=P):
    return [event.model_dump(mode='json') for event in scope_selection.history(
        stack['app_service'], session_id=session_id, principal=principal,
    )]


def pending_jilin(stack, content):
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    flow(stack, session, {
        'operation': 'update', 'draft_id': draft_id, 'expected_revision': 1,
        'relevance_scope_change': change('吉林', '调查吉林'),
    }, '调查吉林')
    return session, draft_id


def test_history_retains_original_pending_question_candidates_and_each_choice(creation_stack, content, catalog):
    stack = creation_stack
    session, draft_id = pending_jilin(stack, content)
    pending = stored(stack, draft_id).configuration.relevance_scope_state
    select(stack, session, draft_id, '吉林省', revision=2, operation_id='choose-province')
    first = history(stack, session)
    assert len(first) == 1
    event = first[0]
    assert event['draft_id'] == draft_id
    assert (event['previous_revision'], event['revision']) == (2, 3)
    assert event['question'] == '“吉林”指哪个调查范围？'
    assert event['previous_target'] is None
    assert event['previous_state']['status'] == 'pending'
    assert event['previous_state']['candidates'] == [candidate.model_dump(mode='json') for candidate in pending.candidates]
    assert event['selected_target']['name'] == '吉林省'
    assert event['selected_province']['name'] == '吉林省'
    assert event['event_id'] and event['created_at']
    assert event['after_sequence'] == max(message.sequence for message in stack['conversation'].get_messages(
        session, principal=P, include_tool_messages=True))
    select(stack, session, draft_id, '吉林市', revision=3, operation_id='choose-city')
    second = history(stack, session)
    assert second[0] == event
    assert len(second) == 2
    assert second[1]['event_id'] != event['event_id']
    assert (second[1]['previous_revision'], second[1]['revision']) == (3, 4)
    assert second[1]['previous_target']['name'] == '吉林省'
    assert second[1]['selected_target']['name'] == '吉林市'
    assert second[1]['selected_province']['name'] == '吉林省'


def test_replay_and_rejected_input_do_not_append_history(creation_stack, content, catalog):
    stack = creation_stack
    session, draft_id = pending_jilin(stack, content)
    select(stack, session, draft_id, revision=2, operation_id='single-choice')
    expected = history(stack, session)
    select(stack, session, draft_id, revision=2, operation_id='single-choice')
    assert history(stack, session) == expected
    with pytest.raises(ConfigurationValidationError):
        select(stack, session, draft_id, '深训', revision=3, operation_id='invalid-choice')
    assert history(stack, session) == expected


def test_later_draft_changes_cannot_rewrite_recorded_history(creation_stack, content, catalog):
    stack = creation_stack
    session, draft_id = pending_jilin(stack, content)
    select(stack, session, draft_id, revision=2)
    expected = history(stack, session)
    flow(stack, session, {
        'operation': 'update', 'draft_id': draft_id, 'expected_revision': 3,
        'title': '随后修改的调查标题',
        'relevance_scope_change': change('北京', '改查北京'),
    }, '改查北京')
    assert stored(stack, draft_id).configuration.relevance_scope.target.name == '北京市'
    assert history(stack, session) == expected


def test_existing_selection_records_recover_after_store_restart(creation_stack, content, catalog, monkeypatch):
    stack = creation_stack
    session, draft_id = pending_jilin(stack, content)
    select(stack, session, draft_id, revision=2)
    expected = history(stack, session)
    reopened = InvestigationCreationStore(stack['creation_store'].db_path)
    monkeypatch.setattr(stack['app_service'], 'store', reopened)
    assert history(stack, session) == expected
    state = stack['conversation'].get_workspace_state(session, principal=P)
    assert list(state.region_selection_history) == expected


def test_history_has_no_cross_session_or_owner_leak(creation_stack, content, catalog):
    stack = creation_stack
    session, draft_id = pending_jilin(stack, content)
    select(stack, session, draft_id, revision=2)
    other = stack['conversation'].create_session(principal=P, workspace_key='unrelated-history')
    assert history(stack, other.id) == []
    with pytest.raises(PrincipalAccessDeniedError):
        history(stack, session, principal=Principal('principal-b'))
    response = stack['client'].get(f'/api/investigation-workspaces/{other.id}/state')
    assert response.status_code == 200, response.text
    assert response.json()['region_selection_history'] == []
    stack['principals'].current = Principal('principal-b')
    denied = stack['client'].get(f'/api/investigation-workspaces/{session}/state')
    assert denied.status_code in {403, 404}, denied.text
    assert draft_id not in denied.text
    assert '吉林省' not in denied.text


def test_workspace_api_projects_only_public_selection_history(creation_stack, content, catalog):
    stack = creation_stack
    session, draft_id = pending_jilin(stack, content)
    select(stack, session, draft_id, revision=2, operation_id='private-operation-token')
    response = stack['client'].get(f'/api/investigation-workspaces/{session}/state')
    assert response.status_code == 200, response.text
    events = response.json()['region_selection_history']
    assert events == history(stack, session)
    assert set(events[0]) == {
        'event_id', 'draft_id', 'previous_revision', 'revision', 'after_sequence', 'created_at',
        'question', 'previous_target', 'previous_state', 'selected_target', 'selected_province',
    }
    encoded = json.dumps(events, ensure_ascii=False)
    for private in ['source_quote', 'source_kind', 'source_operation_id', 'source_user_message_id',
                    'source_sequence', 'catalog_version', 'region_code', 'private-operation-token', P.id]:
        assert private not in encoded


def test_history_does_not_create_messages_mutate_resources_or_start_investigation(creation_stack, content, catalog):
    stack = creation_stack
    session, draft_id = pending_jilin(stack, content)
    messages = stack['conversation'].get_messages(session, principal=P, include_tool_messages=True)
    turns = stack['conversation'].store.list_turns(session)
    resources = resource_snapshot(stack)
    select(stack, session, draft_id, revision=2)
    assert len(history(stack, session)) == 1
    state = stack['conversation'].get_workspace_state(session, principal=P)
    assert len(state.region_selection_history) == 1
    assert stack['conversation'].get_messages(session, principal=P, include_tool_messages=True) == messages
    assert stack['conversation'].store.list_turns(session) == turns
    assert resource_snapshot(stack) == resources
    no_effects(stack)


def test_first_region_history_uses_generic_question_when_no_previous_scope(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, args = prepare(stack, content)
    result, _ = flow(stack, first['session_id'], args, '使用这两份资源创建调查')
    draft_id = result['data']['draft']['id']
    select(stack, first['session_id'], draft_id)
    event, = history(stack, first['session_id'])
    assert event['question'] == '请选择或填写调查地域'
    assert event['previous_target'] is None
    assert event['previous_state'] is None
    assert event['selected_target']['name'] == '吉林省'


def test_history_excludes_records_whose_draft_owner_no_longer_matches(creation_stack, content, catalog):
    stack = creation_stack
    session, draft_id = pending_jilin(stack, content)
    select(stack, session, draft_id, revision=2)
    assert len(history(stack, session)) == 1
    # Simulate a historical association that is no longer owned by this
    # principal. Session ownership alone must not expose the draft revisions.
    with stack['creation_store']._connect() as db:
        db.execute('UPDATE investigation_drafts SET owner_principal=? WHERE id=?',
                   ('principal-b', draft_id))
    assert history(stack, session) == []


def test_manual_ambiguity_keeps_actual_question_and_candidates(creation_stack, content, catalog, monkeypatch):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    url = f'/api/investigation-workspaces/{session}/drafts/{draft_id}/relevance-scope'
    unresolved = stack['client'].post(url, json={
        'expected_revision': 1, 'operation_id': 'check-jilin', 'target_text': '吉林',
    })
    assert unresolved.status_code == 422, unresolved.text
    assert unresolved.json()['detail']['details']['region_resolution']['target_text'] == '吉林'
    assert history(stack, session) == []
    resources = resource_snapshot(stack)
    args = dict(session_id=session, expected_revision=1, operation_id='choose-city',
                target_text='吉林市', clarification_target_text='吉林', principal=P)
    selected = stack['app_service'].select_relevance_scope(draft_id, **args)
    event, = history(stack, session)
    assert event['question'] == '“吉林”指哪个调查范围？'
    assert event['previous_target']['name'] == '克拉玛依市'
    assert event['previous_state']['status'] == 'pending'
    assert {c['name'] for c in event['previous_state']['candidates']} == {'吉林省', '吉林市'}
    assert event['selected_target']['name'] == '吉林市'
    assert selected.current_revision == 2
    assert resource_snapshot(stack) == resources
    stack['app_service'].select_relevance_scope(draft_id, **args)
    assert history(stack, session) == [event]
    with pytest.raises(IdempotencyConflictError):
        stack['app_service'].select_relevance_scope(draft_id, **{**args, 'clarification_target_text': None})
    reopened = InvestigationCreationStore(stack['creation_store'].db_path)
    monkeypatch.setattr(stack['app_service'], 'store', reopened)
    assert history(stack, session) == [event]
    no_effects(stack)


def test_resolved_input_cannot_forge_ambiguity_context(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    with pytest.raises(ConfigurationValidationError):
        stack['app_service'].select_relevance_scope(draft_id, session_id=session,
            expected_revision=1, operation_id='bad-context', target_text='吉林市',
            clarification_target_text='吉林省', principal=P)
    assert stored(stack, draft_id).current_revision == 1
    assert history(stack, session) == []


def test_old_selection_table_adds_optional_clarification_snapshot(tmp_path):
    import sqlite3
    path = tmp_path / 'old-selections.sqlite3'
    InvestigationCreationStore(path)
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE investigation_region_selections DROP COLUMN clarification_json')
    InvestigationCreationStore(path)
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(investigation_region_selections)')}
    assert 'clarification_json' in columns
