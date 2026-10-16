"""Current persisted geography reaches the Agent without rewriting chat history."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from backend.audit_agent.config import settings
from test_geographic_draft_configuration import setup_geo, flow, change, stored
from test_geographic_scope_intent import catalog
from test_investigation_creation_conversation import (
    creation_stack, ScriptedCreationHermesAgent, _run_scripted_creation_turn,
)
from test_ruleset_proposal_presentation import content
from test_unified_draft_configuration import P


MARKER = '服务端当前调查草案'


def capture_continue(stack, session_id, monkeypatch):
    captured = []
    original = ScriptedCreationHermesAgent.run_conversation

    def run(agent, message, **kwargs):
        captured.append(message)
        agent.scope_contexts = [*getattr(agent, "scope_contexts", []), message]
        return original(agent, message, **kwargs)

    monkeypatch.setattr(ScriptedCreationHermesAgent, 'run_conversation', run)
    result = _run_scripted_creation_turn(stack, session_id=session_id,
        content='继续', client_message_id=uuid4().hex, actions=[])
    assert result['turn'].status == 'completed', result
    assert len(captured) == 1
    assert stack['conversation'].store.get_user_message_for_turn(result['turn'].id).content == '继续'
    # Completed-transcript validation checks the clean user text, not this prefix.
    assert captured[0].endswith('继续')
    # API-time context must be restored even though Agent instances are cached.
    assert getattr(result['agent'], 'ephemeral_system_prompt', '') == ''
    return captured[0], result


def summary(context):
    text = context.split(MARKER, 1)[1]
    return json.JSONDecoder().raw_decode(text[text.index('{'):])[0]


@pytest.mark.parametrize('mode', ['default', 'multi_round', 'continuous'])
def test_latest_saved_scope_in_real_next_turn_context(creation_stack, content, catalog, monkeypatch, mode, tmp_path):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    resources = deepcopy(stored(stack, draft_id).configuration.model_dump(mode='json'))
    selected = stack['app_service'].select_relevance_scope(draft_id,
        session_id=first['session_id'], expected_revision=1,
        operation_id='select-jilin-province', target_text='吉林省', principal=P)
    assert selected.current_revision == 2
    if mode == 'multi_round':
        from backend.report_collection.service import ReportCollectionService
        stack['conversation'].report_collection = ReportCollectionService(
            conversation=stack['conversation'], report_store=stack['report_store'],
            can_read=lambda principal, item: False, path=tmp_path / 'scope-reports.sqlite3',
            agent_driven=True)
    monkeypatch.setattr(settings, 'investigation_multi_round_enabled', mode == 'multi_round')
    monkeypatch.setattr(settings, 'continuous_resource_session_enabled', mode == 'continuous')
    text, run = capture_continue(stack, first['session_id'], monkeypatch)
    current = summary(text)
    assert current['draft_id'] == draft_id
    assert current['current_revision'] == 2
    assert current['status'] == 'DRAFT'
    assert current['relevance_scope']['target']['name'] == '吉林省'
    assert current['relevance_scope_state']['status'] == 'resolved'
    assert '用户已通过页面确认调查地域' in text
    event = json.JSONDecoder().raw_decode(text.split('页面地域确认事件：', 1)[1])[0]
    assert event == {
        'draft_id': draft_id, 'previous_revision': 1, 'confirmed_revision': 2,
        'selected_region': '吉林省',
    }
    assert current['selection_policy'] == 'local_first_with_supplement'
    assert '克拉玛依' not in text
    assert all(key not in text for key in ['source_quote', 'source_turn_id', 'principal_id', 'task_anchor', 'source_kind', 'source_operation_id'])
    after = stored(stack, draft_id).configuration.model_dump(mode='json')
    for payload in (resources, after):
        payload.pop('relevance_scope'); payload.pop('relevance_scope_state')
    assert after == resources
    # The new state is ephemeral system context, not fabricated chat history.
    assert not any(MARKER in str(message) for message in run['agent'].conversation_histories[0])


def test_pending_scope_masks_preserved_previous_value(creation_stack, content, catalog, monkeypatch):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    flow(stack, first['session_id'], {'operation': 'update', 'draft_id': draft_id,
        'expected_revision': 1, 'relevance_scope_change': change('吉林', '改查吉林')}, '改查吉林')
    assert stored(stack, draft_id).configuration.relevance_scope.target.name == '克拉玛依市'
    text, _ = capture_continue(stack, first['session_id'], monkeypatch)
    current = summary(text)
    assert current['relevance_scope'] is None
    assert current['relevance_scope_state']['status'] == 'pending'
    assert '当前调查地域已确认' not in text
    assert {item['name'] for item in current['relevance_scope_state']['candidates']} == {'吉林省', '吉林市'}
    assert '克拉玛依' not in text


def test_no_draft_and_session_isolation(creation_stack, content, catalog, monkeypatch):
    stack = creation_stack
    first, _, _, _, _, _, _, _ = setup_geo(stack, content)
    other = stack['conversation'].create_session(principal=P, workspace_key='other-geographic-context')
    text, _ = capture_continue(stack, other.id, monkeypatch)
    assert MARKER not in text
    assert first['session_id'] not in text
    assert '克拉玛依' not in text


def test_continuous_cached_agent_reads_new_selection_without_stale_context(
    creation_stack, content, catalog, monkeypatch
):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    sid = first['session_id']
    draft_id = result['data']['draft']['id']
    monkeypatch.setattr(settings, 'continuous_resource_session_enabled', True)
    first_context, first_run = capture_continue(stack, sid, monkeypatch)
    cached = first_run['agent']
    assert summary(first_context)['relevance_scope']['target']['name'] == '克拉玛依市'
    stack['app_service'].select_relevance_scope(draft_id, session_id=sid,
        expected_revision=1, operation_id='cached-select', target_text='吉林省', principal=P)
    stack['conversation']._agents[sid] = cached
    turn, replay = stack['conversation'].accept_message(sid, principal=P,
        content='继续', client_message_id=uuid4().hex)
    assert not replay
    stack['conversation'].execute_turn(turn.id)
    assert stack['conversation'].store.get_turn(turn.id).status == 'completed'
    assert stack['conversation']._agents[sid] is cached
    latest = summary(cached.scope_contexts[-1])
    assert latest['current_revision'] == 2
    assert latest['relevance_scope']['target']['name'] == '吉林省'
    assert '用户已通过页面确认调查地域' in cached.scope_contexts[-1]
    assert '克拉玛依' not in cached.scope_contexts[-1]
    assert cached.ephemeral_system_prompt == ''


def test_cleared_scope_does_not_revive_previous_geography(creation_stack, content, catalog, monkeypatch):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    draft_id = result['data']['draft']['id']
    flow(stack, first['session_id'], {'operation': 'update', 'draft_id': draft_id,
        'expected_revision': 1, 'relevance_scope_change': {
            'action': 'clear', 'source_quote': '不限制地域'}}, '不限制地域')
    context, _ = capture_continue(stack, first['session_id'], monkeypatch)
    current = summary(context)
    assert current['relevance_scope'] is None
    assert current['relevance_scope_state']['status'] == 'cleared'
    assert '当前调查地域已确认' not in context
    assert '克拉玛依' not in context


def test_failed_agent_restores_ephemeral_context(creation_stack, content, catalog, monkeypatch):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    sid = first['session_id']
    _, run = capture_continue(stack, sid, monkeypatch)
    agent = run['agent']
    observed = []

    def fail(*args, **kwargs):
        observed.append(args[0])
        raise RuntimeError('synthetic provider failure')

    monkeypatch.setattr(agent, 'run_conversation', fail)
    turn, _ = stack['conversation'].accept_message(sid, principal=P,
        content='继续', client_message_id=uuid4().hex)
    with pytest.raises(RuntimeError, match='unknown outcome'):
        stack['conversation'].execute_turn(turn.id)
    assert stack['conversation'].store.get_turn(turn.id).status == 'interrupted'
    assert summary(observed[0])['draft_id'] == result['data']['draft']['id']
    assert agent.ephemeral_system_prompt == ''


@pytest.mark.parametrize('later_change', ['title', 'pending', 'cleared'])
def test_ui_confirmation_event_survives_resource_edits_but_not_new_scope_request(
    creation_stack, content, catalog, monkeypatch, later_change
):
    stack = creation_stack
    first, _, _, _, _, _, result, _ = setup_geo(stack, content)
    sid, draft_id = first['session_id'], result['data']['draft']['id']
    flow(stack, sid, {'operation': 'update', 'draft_id': draft_id,
        'expected_revision': 1, 'relevance_scope_change': change('吉林', '改查吉林')}, '改查吉林')
    stack['app_service'].select_relevance_scope(draft_id, session_id=sid,
        expected_revision=2, operation_id='resolve-city', target_text='吉林市', principal=P)
    args = {'operation': 'update', 'draft_id': draft_id, 'expected_revision': 3}
    if later_change == 'title':
        args['title'] = '调整调查标题'
        message = '只修改标题为调整调查标题'
    elif later_change == 'pending':
        args['relevance_scope_change'] = change('吉林', '改查吉林')
        message = '改查吉林'
    else:
        args['relevance_scope_change'] = {'action': 'clear', 'source_quote': '不限制地域'}
        message = '不限制地域'
    flow(stack, sid, args, message)
    context, run = capture_continue(stack, sid, monkeypatch)
    assert summary(context)['current_revision'] == 4
    if later_change == 'title':
        event = json.JSONDecoder().raw_decode(context.split('页面地域确认事件：', 1)[1])[0]
        assert event['previous_revision'] == 2
        assert event['confirmed_revision'] == 3
        assert event['selected_region'] == '吉林市'
        assert summary(context)['relevance_scope_state']['status'] == 'resolved'
    else:
        assert '页面地域确认事件：' not in context
        assert summary(context)['relevance_scope'] is None
    assert not any('页面地域确认事件：' in str(m) for m in run['agent'].conversation_histories[0])
