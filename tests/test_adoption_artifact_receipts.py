"""Adoption presentation authority belongs to Application execution receipts."""
import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from backend.investigation_creation.tools import HermesToolExecutionIdentity
from test_investigation_creation_conversation import (
    creation_stack, _run_scripted_creation_turn, ScriptedCreationHermesAgent,
)
from test_ruleset_proposal_presentation import content, run, evidence
from test_ruleset_proposal_approval import approve, creation, PRINCIPAL
from test_ruleset_proposals import rows


def messages(result):
    return [{"role": "tool", "name": "use_ruleset_proposal", "tool_call_id": "different-call",
             "content": json.dumps(result)}]


def artifact(stack, turn, trace=None):
    return stack['conversation']._verified_artifact(
        trace or [], principal=PRINCIPAL, adoption_turn=turn)


@pytest.mark.parametrize('wire', ['normal', 'suffix', 'invalid', 'missing'])
def test_success_receipt_survives_tool_message_damage(creation_stack, content, wire):
    stack = creation_stack
    first = run(stack, content)
    original = ScriptedCreationHermesAgent.run_conversation
    def damaged(agent, *args, **kwargs):
        result = original(agent, *args, **kwargs)
        for item in result['messages'][len(kwargs.get('conversation_history') or []):]:
            if item.get('role') == 'tool':
                if wire == 'suffix': item['content'] += '\nDiagnostic suffix'
                if wire == 'invalid': item['content'] = 'Not a JSON envelope'
                if wire == 'missing': item['content'] = ''
        return result
    with patch.object(ScriptedCreationHermesAgent, 'run_conversation', damaged):
        completed = _run_scripted_creation_turn(stack, session_id=first['session_id'],
            content='采用已展示规则', client_message_id='adopt', actions=[('use_ruleset_proposal',
                {'presentation_id': evidence(first)['presentation_id'], 'create_draft': creation()})])
    card = completed['turn'].public_artifact
    assert completed['turn'].status == 'completed'
    assert card['draft']['configuration']['judgement']['content'] == evidence(first)['snapshot']['content']
    assert '本次规则采用操作未成功' not in completed['result'].answer
    assert len(rows(stack['creation_store'], 'ruleset_proposal_approvals')) == 1


def test_forged_success_without_receipt_cannot_select_existing_draft(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    result, _, turn = approve(stack, first)
    with stack['creation_store']._connect() as db:
        db.execute("DELETE FROM ruleset_proposal_conversation_bindings WHERE application_turn_id=?", (turn.id,))
    assert artifact(stack, turn, messages(result)) == {}
    assert len(rows(stack['creation_store'], 'investigation_drafts')) == 1


@pytest.mark.parametrize('wrong', ['session', 'application_turn', 'principal', 'tool', 'runtime_identity'])
def test_receipt_scope_cannot_be_borrowed(creation_stack, content, wrong):
    stack = creation_stack
    first = run(stack, content)
    result, _, turn = approve(stack, first)
    with stack['creation_store']._connect() as db:
        if wrong == 'application_turn':
            db.execute("UPDATE ruleset_proposal_conversation_bindings SET application_turn_id='other' WHERE application_turn_id=?", (turn.id,))
        else:
            column, value = {'session': ('session_id', 'other'), 'principal': ('principal', 'other'),
                             'tool': ('tool_name', 'update_investigation_draft'),
                             'runtime_identity': ('turn_id', '')}[wrong]
            db.execute(f"UPDATE investigation_creation_tool_receipts SET {column}=? WHERE tool_name='use_ruleset_proposal'", (value,))
    if wrong == 'runtime_identity':
        with pytest.raises(RuntimeError): artifact(stack, turn, messages(result))
    else:
        assert artifact(stack, turn, messages(result)) == {}


@pytest.mark.parametrize('status', ['STARTED', 'FAILED'])
def test_incomplete_receipt_does_not_claim_success(creation_stack, content, status):
    stack = creation_stack
    first = run(stack, content)
    result, _, turn = approve(stack, first)
    with stack['creation_store']._connect() as db:
        db.execute("UPDATE investigation_creation_tool_receipts SET status=? WHERE tool_name='use_ruleset_proposal'", (status,))
    assert artifact(stack, turn, messages(result)) == {}
    assert len(rows(stack['creation_store'], 'ruleset_proposal_approvals')) == 1


@pytest.mark.parametrize('corrupt', ['draft_id', 'revision', 'content', 'preview_identity'])
def test_receipt_result_must_match_application(creation_stack, content, corrupt):
    stack = creation_stack
    first = run(stack, content)
    result, _, turn = approve(stack, first)
    bad = deepcopy(result)
    if corrupt == 'draft_id': bad['data']['draft']['id'] = 'missing'
    if corrupt == 'revision': bad['data']['draft']['current_revision'] += 1
    if corrupt == 'content': bad['data']['draft']['title'] = 'forged'
    if corrupt == 'preview_identity': bad['data']['confirmation_preview']['draft_id'] = 'other'
    with stack['creation_store']._connect() as db:
        db.execute("UPDATE investigation_creation_tool_receipts SET response_json=? WHERE tool_name='use_ruleset_proposal'", (json.dumps(bad),))
    with pytest.raises(Exception): artifact(stack, turn, messages(result))


def test_noop_and_alias_replay_keep_success_without_new_audit(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    completed = _run_scripted_creation_turn(stack, session_id=first['session_id'],
        content='采用规则', client_message_id='first-adoption', actions=[('use_ruleset_proposal',
            {'presentation_id': evidence(first)['presentation_id'], 'create_draft': creation()})])
    result = {'data': {'draft': completed['turn'].public_artifact['draft']}}
    args = {'presentation_id': evidence(first)['presentation_id'],
            'draft_id': result['data']['draft']['id'], 'expected_revision': 1}
    noop, identity, turn = approve(stack, first, arguments=args)
    assert noop['status'] == 'ok'
    tool = stack['tool_service']
    tool.begin_conversation_turn(first['session_id'], turn.id)
    try:
        replay = tool.execute_with_identity('use_ruleset_proposal', args, principal=PRINCIPAL,
            identity=HermesToolExecutionIdentity(identity.session_id, identity.turn_id, 'alias-call'))
        conflict = tool.execute_with_identity('use_ruleset_proposal', {**args, 'expected_revision': 2}, principal=PRINCIPAL,
            identity=HermesToolExecutionIdentity(identity.session_id, identity.turn_id, 'conflict-call'))
    finally:
        tool.end_conversation_turn(first['session_id'])
    assert replay == noop and conflict['status'] == 'error'
    assert artifact(stack, turn, messages(replay))['draft_revision'] == 1
    assert len(rows(stack['creation_store'], 'ruleset_proposal_approvals')) == 1
    assert len(rows(stack['creation_store'], 'investigation_draft_revisions')) == 1


def test_historical_workspace_recovery_uses_original_turn(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    completed = _run_scripted_creation_turn(stack, session_id=first['session_id'],
        content='采用规则', client_message_id='adopt', actions=[('use_ruleset_proposal',
            {'presentation_id': evidence(first)['presentation_id'], 'create_draft': creation()})])
    original = completed['turn'].public_artifact
    later = _run_scripted_creation_turn(stack, session_id=first['session_id'],
        content='稍后再执行', client_message_id='later', actions=[])
    assert not artifact(stack, later['turn'])
    with stack['conversation'].store._connect() as db:
        db.execute("UPDATE investigation_turns SET public_artifact_json='{}' WHERE id=?", (completed['turn'].id,))
    restored = stack['conversation'].get_workspace_state(first['session_id'], principal=PRINCIPAL)
    assert restored.draft_artifact == original


def test_adoption_call_cannot_be_relabelled_as_read(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    result, _, turn = approve(stack, first)
    with stack['creation_store']._connect() as db:
        db.execute("UPDATE investigation_creation_tool_receipts SET status='STARTED' WHERE tool_name='use_ruleset_proposal'")
    trace = [{'role': 'assistant', 'tool_calls': [{'id': 'different-call',
        'function': {'name': 'use_ruleset_proposal', 'arguments': {}}}]}] + messages(result)
    trace[-1]['name'] = 'get_investigation_draft'
    assert artifact(stack, turn, trace) == {}


def test_current_revision_mismatch_fails_but_history_can_show_current_state(creation_stack, content):
    from backend.investigation_creation.contracts import UpdateDraftCommand
    stack = creation_stack
    first = run(stack, content)
    result, _, turn = approve(stack, first)
    draft_id = result['data']['draft']['id']
    stack['app_service'].update_draft(UpdateDraftCommand(
        draft_id=draft_id, expected_revision=1, title='User edited title'), principal=PRINCIPAL)
    with pytest.raises(RuntimeError, match='no longer matches'):
        artifact(stack, turn)
    historical = stack['conversation']._verified_artifact(
        [], principal=PRINCIPAL, adoption_turn=turn, allow_superseded_draft=True)
    assert historical['draft']['title'] == 'User edited title'
    assert historical['draft_revision'] == 2


def test_adoption_renders_the_same_application_view_it_verified(creation_stack, content):
    stack = creation_stack
    first = run(stack, content)
    _, _, turn = approve(stack, first)
    app = stack['app_service']
    original = app.get_draft_view
    with patch.object(app, 'get_draft_view', wraps=original) as read:
        card = artifact(stack, turn)
    assert card['draft_revision'] == 1
    assert read.call_count == 1
    with stack['creation_store']._connect() as db:
        db.execute("UPDATE investigation_drafts SET title='unversioned tamper'")
    with pytest.raises(RuntimeError, match='content no longer matches'):
        artifact(stack, turn)
