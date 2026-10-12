"""The user-bound edit entry must enforce its capability even for invented calls."""
import json
from pathlib import Path
import pytest
from test_investigation_creation_conversation import creation_stack
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.tools import HermesToolExecutionIdentity, creation_tool_schemas
from backend.resource_management.edit_entry import editing_entry
from backend.resource_management.contracts import ResourceError
from backend.investigation.errors import ClientMessageConflictError
from backend.investigation_creation.errors import ProposalNotFoundError

P = Principal('principal-a')

@pytest.fixture
def bound(creation_stack):
    stack = creation_stack
    conv = stack['conversation']
    sid = conv.create_session(principal=P).id
    body = json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())
    proposal = stack['app_service'].create_ruleset_proposal(body, session_id=sid)
    target = dict(edit_id=proposal.proposal_id, expected_version=1)
    return stack, sid, target


def call(stack, sid, name, args, call_id='call-1'):
    return stack['tool_service'].execute_with_identity(name, args, principal=P,
        identity=HermesToolExecutionIdentity(sid, 'runtime-turn', call_id))


@pytest.mark.parametrize('name,args', [
    ('create_ruleset_proposal', {'generation_request': {'objective': '重新生成'}}),
    ('update_ruleset_proposal', {}),
    ('create_lexicon_edit', {}),
    ('update_resource_edit', {'edit_id': 'other', 'changes': []}),
    ('save_resource', {'edit_id': 'other'}),
])
def test_wrong_paths_block_before_schema_generation_or_receipt(bound, name, args):
    stack, sid, target = bound
    service = stack['app_service'].resource_management
    original = service.get_edit(target['edit_id'], session_id=sid, principal=P)
    stack['tool_service'].begin_conversation_turn(sid, 'turn', resource_edit=target)
    try:
        response = call(stack, sid, name, args)
        assert response['status'] == 'error'
        assert response['error']['code'] in {'RESOURCE_EDIT_ENTRY_ONLY', 'RESOURCE_EDIT_TARGET_MISMATCH'}
        assert service.get_edit(target['edit_id'], session_id=sid, principal=P) == original
        with stack['creation_store']._connect() as conn:
            assert conn.execute('SELECT COUNT(*) FROM investigation_creation_tool_receipts').fetchone()[0] == 0
    finally:
        stack['tool_service'].end_conversation_turn(sid)


def test_patch_and_explicit_new_entry_are_independent(bound):
    stack, sid, target = bound
    service = stack['app_service'].resource_management
    original = service.get_edit(target['edit_id'], session_id=sid, principal=P)
    stack['tool_service'].begin_conversation_turn(sid, 'turn', resource_edit=target)
    try:
        result = call(stack, sid, 'update_resource_edit', dict(**target, changes=[
            {'operation': 'add_exemption', 'values': {'exemption_id': 'extra', 'name': '新增', 'condition': '测试条件'}}]))
        assert result['status'] == 'ok', result
        current = service.get_edit(target['edit_id'], session_id=sid, principal=P)
        assert current['content']['categories'] == original['content']['categories']
        assert current['content']['general_exemptions'][:-1] == original['content']['general_exemptions']
        for changes in [[{'operation': 'set_exemptions', 'values': {'general_exemptions': []}}],
                        [{'operation': 'update_rule', 'values': {'rule_exemptions': []}}]]:
            result = call(stack, sid, 'update_resource_edit', {'edit_id': target['edit_id'], 'expected_version': 2, 'changes': changes}, 'blocked')
            assert result['error']['code'] == 'RESOURCE_EDIT_ENTRY_ONLY'
    finally:
        stack['tool_service'].end_conversation_turn(sid)
    result = call(stack, sid, 'create_ruleset_proposal', {'content': original['content']}, 'new')
    assert result['status'] == 'ok', result
    assert service.get_edit(target['edit_id'], session_id=sid, principal=P) == current


def test_discovery_hides_generation_only_inside_entry(bound):
    _, _, target = bound
    assert 'create_ruleset_proposal' in {s['name'] for s in creation_tool_schemas()}
    with editing_entry(target):
        names = {s['name'] for s in creation_tool_schemas()}
        assert 'update_resource_edit' in names
        assert not names & {'create_ruleset_proposal', 'update_ruleset_proposal', 'create_lexicon_edit'}
    assert 'create_ruleset_proposal' in {s['name'] for s in creation_tool_schemas()}


def test_binding_is_durable_idempotent_and_not_inherited(bound):
    stack, sid, target = bound
    conv = stack['conversation']
    turn, replay = conv.accept_message(sid, client_message_id='one', content='改一条', principal=P, resource_edit=target)
    assert not replay
    assert conv.store.get_user_message_for_turn(turn.id).metadata['resource_edit'] == target
    assert conv.accept_message(sid, client_message_id='one', content='改一条', principal=P, resource_edit=target)[1]
    with pytest.raises(ClientMessageConflictError):
        conv.accept_message(sid, client_message_id='one', content='改一条', principal=P)
    with pytest.raises(ClientMessageConflictError):
        conv.accept_message(sid, client_message_id='one', content='改一条', principal=P,
                            resource_edit={**target,'expected_version': 2})
    conv.store.fail_turn(turn.id, error_code='test', safe_message='test', retryable=False)
    next_turn, _ = conv.accept_message(sid, client_message_id='two', content='从零新建', principal=P)
    assert not conv.store.get_user_message_for_turn(next_turn.id).metadata


def test_source_version_and_session_cannot_be_forged(bound):
    stack, sid, target = bound
    conv = stack['conversation']
    with pytest.raises(ResourceError):
        conv.accept_message(sid, client_message_id='stale', content='改', principal=P,
                            resource_edit={**target,'expected_version': 20})
    other = conv.create_session(principal=P, workspace_key='other').id
    with pytest.raises(ProposalNotFoundError):
        conv.accept_message(other, client_message_id='foreign', content='改', principal=P, resource_edit=target)
    assert not conv.store.list_turns(other)
    assert not conv.store.list_turns(sid)


def test_receipt_replay_cannot_bypass_entry(bound):
    stack, sid, target = bound
    body = stack['app_service'].resource_management.get_edit(target['edit_id'], session_id=sid, principal=P)['content']
    assert call(stack, sid, 'create_ruleset_proposal', {'content': body}, 'same')['status'] == 'ok'
    stack['tool_service'].begin_conversation_turn(sid, 'turn', resource_edit=target)
    try:
        assert call(stack, sid, 'create_ruleset_proposal', {'content': body}, 'same')['error']['code'] == 'RESOURCE_EDIT_ENTRY_ONLY'
    finally:
        stack['tool_service'].end_conversation_turn(sid)


def test_stale_copy_is_rejected_before_model_execution(bound):
    stack, sid, target = bound
    conv = stack['conversation']
    turn, _ = conv.accept_message(sid, client_message_id='stale-later', content='改', principal=P, resource_edit=target)
    stack['app_service'].resource_management.update(target['edit_id'], 1, [
        {'operation': 'set_metadata', 'values': {'name': 'Updated outside this turn'}}], session_id=sid, principal=P)
    result = conv.execute_turn(turn.id)
    assert result.status == 'error'
    assert conv.store.get_turn(turn.id).llm_call_count == 0
    assert sid not in stack['tool_service']._edit_targets


def test_public_request_carries_binding_without_exposing_it_as_model_argument(bound):
    from backend.api.investigation_conversation import CreateWorkspaceTurnRequest
    from pydantic import ValidationError
    _, _, target = bound
    request = CreateWorkspaceTurnRequest(client_message_id='one', content='编辑', resource_edit=target)
    assert request.resource_edit.model_dump() == target
    with pytest.raises(ValidationError):
        CreateWorkspaceTurnRequest(client_message_id='one', content='编辑', resource_edit={**target, 'mode': 'new'})


@pytest.mark.parametrize('multi_round', [False, True])
def test_open_in_edit_entry_reuses_exact_selected_copy(bound, monkeypatch, multi_round):
    from backend.audit_agent.config import settings
    monkeypatch.setattr(settings, 'investigation_multi_round_enabled', multi_round)
    stack, sid, target = bound
    service = stack['app_service'].resource_management
    formal = service.save(target['edit_id'], 1, 'new', 'publish-fixture', session_id=sid, principal=P)
    selected = service.open('ruleset', formal['resource_id'], revision_id=formal['revision_id'], session_id=sid, principal=P)
    target = {'edit_id': selected['edit_id'], 'expected_version': selected['version']}
    stack['tool_service'].begin_conversation_turn(sid, 'turn', resource_edit=target)
    try:
        for i in range(2):
            result = call(stack, sid, 'open_resource_edit', {'kind':'ruleset','resource_id':formal['resource_id'],'revision_id':formal['revision_id']}, 'open'+str(i))
            assert result['status']=='ok', result
            assert result['data']['edit_id']==target['edit_id']
        for args in [{'kind':'ruleset','resource_id':'other'}, {'kind':'ruleset','resource_id':formal['resource_id'],'revision_id':'other-version'}]:
            result = call(stack, sid, 'open_resource_edit', args, str(args))
            assert result['error']['code']=='RESOURCE_EDIT_TARGET_MISMATCH'
        with stack['creation_store']._connect() as conn:
            assert conn.execute('SELECT COUNT(*) FROM ruleset_proposals WHERE session_id=?',(sid,)).fetchone()[0] == 2
    finally:
        stack['tool_service'].end_conversation_turn(sid)


def test_lexicon_entry_is_partial_and_session_isolated(bound):
    from test_resource_lifecycle import lexicon
    stack, sid, _ = bound
    service = stack['app_service'].resource_management
    original = service.create_lexicon(lexicon(), session_id=sid, principal=P)
    target = {'edit_id': original['edit_id'], 'expected_version': original['version']}
    stack['tool_service'].begin_conversation_turn(sid, 'turn', resource_edit=target)
    try:
        result = call(stack, sid, 'update_resource_edit', {**target, 'changes': [
            {'operation': 'upsert_entry', 'target_id': 'main-1', 'values': {'term': '新名称'}}]})
        assert result['status'] == 'ok', result
        current = service.get_edit(target['edit_id'], session_id=sid, principal=P)
        expected = json.loads(json.dumps(original['content']))
        expected['entries'][0]['term'] = '新名称'
        assert current['content'] == expected
        blocked = call(stack, sid, 'create_lexicon_edit', {'content': lexicon()}, 'blocked-new')
        assert blocked['error']['code'] == 'RESOURCE_EDIT_ENTRY_ONLY'
        other = stack['conversation'].create_session(principal=P, workspace_key='unbound').id
        result = call(stack, other, 'create_lexicon_edit', {'content': lexicon()}, 'allowed-new')
        assert result['status'] == 'ok', result
    finally:
        stack['tool_service'].end_conversation_turn(sid)


def test_cached_agent_does_not_keep_previous_entry_or_target(bound):
    stack, sid, target = bound
    conv = stack['conversation']
    ordinary = conv._agent(sid)
    with editing_entry(target):
        selected = conv._agent(sid)
        assert selected is not ordinary
        assert conv._agent(sid) is selected
    restored = conv._agent(sid)
    assert restored is not selected
    assert restored._resource_edit_binding is None
    with editing_entry({**target, 'expected_version': 2}):
        changed = conv._agent(sid)
        assert changed is not restored
        assert changed._resource_edit_binding['expected_version'] == 2
    assert len(conv._agents) == 1
