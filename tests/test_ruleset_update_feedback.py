"""Narrow regression for failed rule edits falsely reported as completed."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.investigation_creation.principal import Principal
from backend.investigation_creation.public_answer import has_failed_ruleset_update
from backend.investigation_creation.tools import HermesToolExecutionIdentity
from test_investigation_creation_conversation import creation_stack, _draft_count


def trace(args, result, *, wrapped=True, name='update_ruleset_proposal', call_id='edit-1'):
    return [
        {'role': 'assistant', 'content': '', 'tool_calls': [{
            'id': call_id, 'type': 'function', 'function': {
                'name': 'tool_call' if wrapped else name,
                'arguments': json.dumps({'name': name, 'arguments': args} if wrapped else args),
            }}]},
        {'role': 'tool', 'tool_call_id': call_id, 'name': 'tool_call' if wrapped else name,
         'content': json.dumps(result)},
    ]


@pytest.mark.parametrize('wrapped', [False, True])
def test_failure_is_scoped_to_same_rule_edit(wrapped):
    failure = trace({'proposal_id': 'a'}, {'error': 'missing expected_version; tool NOT invoked'}, wrapped=wrapped)
    assert has_failed_ruleset_update(failure)
    success = {'status': 'ok', 'data': {'proposal_id': 'a', 'version': 2}}
    assert not has_failed_ruleset_update(trace({'proposal_id': 'a'}, success, wrapped=wrapped))
    assert not has_failed_ruleset_update(failure + trace({'proposal_id': 'a'}, success, call_id='edit-2'))
    assert has_failed_ruleset_update(failure + trace({'proposal_id': 'b'}, {'status': 'ok', 'data': {'proposal_id': 'b'}}, call_id='edit-2'))
    assert not has_failed_ruleset_update(trace({}, {'status': 'error', 'error': 'failure'}, name='create_lexicon_edit'))
    assert not has_failed_ruleset_update(trace({}, {'status': 'error', 'error': 'failure'}, name='get_ruleset_proposal'))
    assert not has_failed_ruleset_update([{'role': 'assistant', 'content': '修改失败'}])


@pytest.mark.parametrize('valid', [False, True])
def test_actual_edit_and_final_reply_agree_without_other_mutations(creation_stack, valid):
    conversation = creation_stack['conversation']
    service = creation_stack['tool_service']
    principal = Principal('principal-a')
    session = conversation.create_session(principal=principal)
    content = json.loads((Path(__file__).parent/'fixtures/recruitment_fraud_ruleset.json').read_text())
    proposal = service.application_service.create_ruleset_proposal(content, session_id=session.id)
    before = proposal.content.model_dump(mode='json')
    revised = deepcopy(before)
    revised['categories'][0]['rules'][0]['hit_condition'] = '必须同时具有明确诈骗语境和收费导流证据。'
    turn, _ = conversation.accept_message(session.id, client_message_id='edit', content='只修改第一条，不保存或启动。', principal=principal)
    args = {'proposal_id': proposal.proposal_id, 'content': revised}
    if valid:
        args['expected_version'] = proposal.version
    try:
        result = service.execute_with_identity('update_ruleset_proposal', args, principal=principal,
            identity=HermesToolExecutionIdentity.require(session_id=session.id, turn_id=turn.id, tool_call_id='edit-1'))
    except ValidationError:
        assert not valid
        result = {'error': "tool_call to 'update_ruleset_proposal' is missing required argument(s): expected_version. The tool was NOT invoked."}
    original = '已完成规则第1条修改，版本2。\n\n### 说明\n其他规则和豁免不变。'
    messages = [{'role': 'user', 'content': '只修改第一条。'}, *trace(args, result), {'role': 'assistant', 'content': original}]
    persisted = conversation._persist_result(turn, {'final_response': original}, messages, {}, history_count=0)
    actual = service.application_service.get_ruleset_proposal(proposal.proposal_id, session_id=session.id)
    history = conversation.store.latest_completed_hermes_transcript(session.id)
    if valid:
        assert result['status'] == 'ok'
        assert actual.version == 2
        assert actual.content.model_dump(mode='json') == revised
        assert persisted.answer.startswith(original)  # existing authoritative card may be appended
        assert history[-1]['content'] == original
    else:
        assert actual.version == 1
        assert actual.content.model_dump(mode='json') == before
        assert '本次规则修改未成功' in persisted.answer
        assert '已完成规则第1条修改' not in persisted.answer
        assert '版本2' not in persisted.answer
        assert history[-1]['content'] == persisted.answer
    assert _draft_count(creation_stack['creation_store']) == 0
    with creation_stack['creation_store']._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM investigation_runs').fetchone()[0] == 0
