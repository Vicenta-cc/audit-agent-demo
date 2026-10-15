"""Direct UI geography selection against real stores; no live AI requests."""
import json
import sqlite3
from uuid import uuid4

import pytest

from backend.investigation_creation.errors import (
    ConfigurationValidationError,
    DraftRevisionConflictError,
    IdempotencyConflictError,
    InvestigationCreationError,
)
from backend.investigation_creation.store import InvestigationCreationStore
from test_geographic_draft_configuration import change, flow, no_effects, setup_geo, stored
from test_geographic_scope_intent import catalog
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_resource_lifecycle import lexicon
from test_unified_draft_configuration import P, prepare, call


def select(stack, session_id, draft_id, target_text='吉林省', *, revision=1, operation_id=None):
    return stack['app_service'].select_relevance_scope(
        draft_id, session_id=session_id, expected_revision=revision,
        operation_id=operation_id or uuid4().hex, target_text=target_text, principal=P,
    )


def without_geography(configuration):
    return configuration.model_dump(mode='json', exclude={'relevance_scope', 'relevance_scope_state'})


def resource_snapshot(stack):
    # A logical dump proves no edits, versions, references, receipts or formal
    # library records changed, without depending on resource-table internals.
    with sqlite3.connect(stack['resource_db']) as db:
        return '\n'.join(db.iterdump())


@pytest.mark.parametrize('formal_rules', [False, True])
@pytest.mark.parametrize('formal_lexicon', [False, True])
def test_selection_preserves_all_four_resource_combinations(
    creation_stack, content, catalog, formal_rules, formal_lexicon,
):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(
        stack, content, formal_rules=formal_rules, formal_lexicon=formal_lexicon,
    )
    draft_id = result['data']['draft']['id']
    before = stored(stack, draft_id)
    resources = resource_snapshot(stack)
    changed = select(stack, first['session_id'], draft_id)
    assert changed.current_revision == 2
    assert changed.configuration.relevance_scope.target.name == '吉林省'
    assert changed.configuration.relevance_scope.selection_policy == before.configuration.relevance_scope.selection_policy
    assert without_geography(changed.configuration) == without_geography(before.configuration)
    assert resource_snapshot(stack) == resources
    assert changed.title == before.title
    assert changed.objective == before.objective
    restarted = InvestigationCreationStore(stack['creation_store'].db_path)
    assert restarted.get_draft(draft_id, principal=P.id).configuration == changed.configuration
    no_effects(stack)


@pytest.mark.parametrize('target', ['吉林省', '吉林市'])
def test_ambiguous_pending_scope_can_be_resolved_by_explicit_ui_choice(creation_stack, content, catalog, target):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    flow(stack, first['session_id'], {
        'operation': 'update', 'draft_id': draft_id, 'expected_revision': 1,
        'relevance_scope_change': change('吉林', '调查吉林'),
    }, '调查吉林')
    assert stored(stack, draft_id).configuration.relevance_scope_state.status == 'pending'
    selected = select(stack, first['session_id'], draft_id, target, revision=2)
    assert selected.current_revision == 3
    assert selected.configuration.relevance_scope_state.status == 'resolved'
    assert selected.configuration.relevance_scope.target.name == target
    no_effects(stack)


def test_first_ui_scope_uses_default_policy_without_fabricating_conversation(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, args = prepare(stack, content)
    result, _ = flow(stack, first['session_id'], args, '使用这两份资源创建调查')
    draft_id = result['data']['draft']['id']
    turns_before = stack['conversation'].store.list_turns(first['session_id'])
    selected = select(stack, first['session_id'], draft_id, operation_id='real-interface-choice')
    state = selected.configuration.relevance_scope_state
    assert selected.configuration.relevance_scope.selection_policy == 'local_first_with_supplement'
    assert state.source_kind == 'ui_selection'
    assert state.source_operation_id == 'real-interface-choice'
    assert not state.source_user_message_id
    assert not state.source_turn_id
    assert stack['conversation'].store.list_turns(first['session_id']) == turns_before


def test_explicit_local_only_policy_survives_region_change(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    flow(stack, session, {
        'operation': 'update', 'draft_id': draft_id, 'expected_revision': 1,
        'relevance_scope_change': change('北京', '只调查北京本地', 'local_only'),
    }, '只调查北京本地')
    selected = select(stack, session, draft_id, '吉林省', revision=2)
    assert selected.configuration.relevance_scope.selection_policy == 'local_only'
    assert selected.configuration.relevance_scope_state.selection_policy == 'local_only'


@pytest.mark.parametrize('target', ['深训', '朝阳区', '吉林'])
def test_unresolved_ui_input_returns_candidates_without_mutating_draft(creation_stack, content, catalog, target):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    before = stored(stack, draft_id)
    resources = resource_snapshot(stack)
    with pytest.raises(ConfigurationValidationError) as failure:
        select(stack, first['session_id'], draft_id, target)
    assert failure.value.code == 'REGION_SELECTION_UNRESOLVED'
    assert failure.value.details['mutation_applied'] is False
    resolution = failure.value.details['region_resolution']
    assert resolution['status'] == 'pending'
    if target == '吉林':
        assert {candidate['name'] for candidate in resolution['candidates']} == {'吉林省', '吉林市'}
    assert stored(stack, draft_id) == before
    assert resource_snapshot(stack) == resources
    no_effects(stack)


def test_selection_replay_does_not_reapply_old_value_or_increment_revision(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    selected = select(stack, session, draft_id, operation_id='first-selection')
    replay = select(stack, session, draft_id, operation_id='first-selection')
    assert replay == selected
    newer = select(stack, session, draft_id, '吉林市', revision=2, operation_id='second-selection')
    old_replay = select(stack, session, draft_id, operation_id='first-selection')
    assert old_replay == newer
    assert old_replay.current_revision == 3
    assert old_replay.configuration.relevance_scope.target.name == '吉林市'
    with pytest.raises(IdempotencyConflictError):
        select(stack, session, draft_id, '北京', operation_id='first-selection')
    assert stored(stack, draft_id) == newer


def test_stale_revision_is_rejected_without_resource_changes(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    selected = select(stack, session, draft_id)
    resources = resource_snapshot(stack)
    with pytest.raises(DraftRevisionConflictError):
        select(stack, session, draft_id, '吉林市', revision=1)
    assert stored(stack, draft_id) == selected
    assert resource_snapshot(stack) == resources


def test_selection_cannot_cross_workspace_boundary(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    other = stack['conversation'].create_session(principal=P, workspace_key='another-workspace')
    before = stored(stack, draft_id)
    with pytest.raises(InvestigationCreationError):
        select(stack, other.id, draft_id)
    assert stored(stack, draft_id) == before


def test_pending_conversation_turn_blocks_ui_selection(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    before = stored(stack, draft_id)
    stack['conversation'].accept_message(
        first['session_id'], content='继续', principal=P, client_message_id='pending-before-click',
    )
    with pytest.raises(ConfigurationValidationError) as failure:
        select(stack, first['session_id'], draft_id)
    assert failure.value.code == 'REGION_SELECTION_BUSY'
    assert stored(stack, draft_id) == before


def test_old_quote_cannot_overwrite_selection_but_new_user_request_can(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    selected = select(stack, session, draft_id)
    failure, _, turn = call(stack, session, {
        'operation': 'update', 'draft_id': draft_id, 'expected_revision': 2,
        'relevance_scope_change': change(),
    }, call_id='old-geography-quote', message='继续')
    assert failure['error']['code'] == 'REGION_SOURCE_SUPERSEDED', failure
    assert stored(stack, draft_id) == selected
    stack['conversation'].store.fail_turn(
        turn.id, error_code='REGION_SOURCE_SUPERSEDED', safe_message='旧引用已失效', retryable=False,
    )
    updated, _ = flow(stack, session, {
        'operation': 'update', 'draft_id': draft_id, 'expected_revision': 2,
        'relevance_scope_change': change('吉林市', '改查吉林市'),
    }, '改查吉林市')
    assert updated['status'] == 'ok'
    assert stored(stack, draft_id).configuration.relevance_scope.target.name == '吉林市'


def test_resource_only_update_preserves_direct_selection(creation_stack, content, catalog):
    stack = creation_stack
    first, ctx, manager, _, _, _, result, _ = setup_geo(stack, content)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    selected = select(stack, session, draft_id)
    replacement = manager.create_lexicon({**lexicon(), 'title': '用户另选的词库'}, **ctx)
    updated, _ = flow(stack, session, {
        'operation': 'update', 'draft_id': draft_id, 'expected_revision': 2,
        'lexicon_ref': replacement['resource_ref'],
    }, '换成刚才的新词库')
    assert updated['status'] == 'ok'
    after = stored(stack, draft_id)
    assert after.configuration.relevance_scope == selected.configuration.relevance_scope
    assert after.configuration.relevance_scope_state == selected.configuration.relevance_scope_state
    assert after.current_revision == 3
    no_effects(stack)


def test_public_selection_endpoint_returns_canonical_scope_without_private_source(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    response = stack['client'].post(
        f'/api/investigation-workspaces/{session}/drafts/{draft_id}/relevance-scope',
        json={'expected_revision': 1, 'operation_id': 'api-selection', 'target_text': '吉林省'},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['current_revision'] == 2
    assert body['configuration']['relevance_scope']['target']['name'] == '吉林省'
    encoded = json.dumps(body, ensure_ascii=False)
    for private in ['source_quote', 'source_user_message_id', 'source_sequence',
                    'source_operation_id', 'task_anchor_turn_id', 'catalog_version', P.id]:
        assert private not in encoded
    no_effects(stack)


def test_unresolved_api_selection_exposes_only_safe_candidates(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    before = stored(stack, draft_id)
    response = stack['client'].post(
        f'/api/investigation-workspaces/{session}/drafts/{draft_id}/relevance-scope',
        json={'expected_revision': 1, 'operation_id': 'ambiguous-ui-input', 'target_text': '吉林'},
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body['detail']['code'] == 'REGION_SELECTION_UNRESOLVED'
    resolution = body['detail']['details']['region_resolution']
    assert {candidate['name'] for candidate in resolution['candidates']} == {'吉林省', '吉林市'}
    encoded = json.dumps(body, ensure_ascii=False)
    for private in ['source_quote', 'source_operation_id', 'source_sequence', P.id]:
        assert private not in encoded
    assert stored(stack, draft_id) == before


def test_selection_audit_failure_rolls_back_draft_revision_and_operation(creation_stack, content, catalog, monkeypatch):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    store = stack['creation_store']
    before = stored(stack, draft_id)
    resources = resource_snapshot(stack)
    real_now = store._now_text
    calls = 0

    def fail_audit_timestamp():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('injected selection audit failure')
        return real_now()

    with monkeypatch.context() as patch:
        patch.setattr(store, '_now_text', fail_audit_timestamp)
        with pytest.raises(RuntimeError, match='injected selection audit failure'):
            select(stack, session, draft_id, operation_id='failed-audit-selection')
    assert calls == 2
    assert stored(stack, draft_id) == before
    with store._connect() as db:
        assert db.execute('SELECT count(*) FROM investigation_draft_revisions WHERE draft_id=?',
                          (draft_id,)).fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM investigation_region_selections WHERE operation_id=?',
                          ('failed-audit-selection',)).fetchone()[0] == 0
    assert resource_snapshot(stack) == resources
    # The rolled-back operation remains usable; no ghost receipt blocks retry.
    retried = select(stack, session, draft_id, operation_id='failed-audit-selection')
    assert retried.current_revision == 2
    no_effects(stack)


def test_clearing_scope_does_not_restore_old_pre_selection_quote_authority(creation_stack, content, catalog):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    session, draft_id = first['session_id'], result['data']['draft']['id']
    select(stack, session, draft_id)
    flow(stack, session, {
        'operation': 'update', 'draft_id': draft_id, 'expected_revision': 2,
        'relevance_scope_change': {'action': 'clear', 'source_quote': '取消地域限制'},
    }, '取消地域限制')
    cleared = stored(stack, draft_id)
    assert cleared.current_revision == 3
    assert cleared.configuration.relevance_scope is None
    assert cleared.configuration.relevance_scope_state.status == 'cleared'
    failure, _, _ = call(stack, session, {
        'operation': 'update', 'draft_id': draft_id, 'expected_revision': 3,
        'relevance_scope_change': change(),
    }, call_id='old-quote-after-clear', message='继续')
    assert failure['error']['code'] == 'REGION_SOURCE_SUPERSEDED', failure
    assert stored(stack, draft_id) == cleared
    no_effects(stack)
