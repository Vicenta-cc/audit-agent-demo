"""Exact rule references cross the generation/presentation/adoption boundary offline."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from backend.investigation_creation.contracts import TemporaryRuleSetProposal
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.tools import HermesToolExecutionIdentity
from backend.resource_management import ruleset_refs
from backend.resource_management.contracts import LexiconContent
from backend.rulesets.contracts import RuleSetContent
from test_investigation_creation_conversation import (
    creation_stack, _draft_count, _run_count, _run_scripted_creation_turn,
)
from test_resource_lifecycle import lexicon
from test_ruleset_proposal_presentation import content, evidence
from test_ruleset_proposals import rows
from test_unified_draft_configuration import call as configure

P = Principal('principal-a')
REQUEST = {'objective': '调查招聘收费风险', 'platform': 'xhs'}


class OfflineGenerator:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def generate(self, kind, request):
        self.calls.append(kind)
        return (RuleSetContent.model_validate(self.body) if kind == 'ruleset'
                else LexiconContent.model_validate(lexicon()))


def invoke(stack, name, args, *, session='handoff', turn='generation', call_id='rule'):
    return stack['tool_service'].execute_with_identity(name, args, principal=P,
        identity=HermesToolExecutionIdentity(session, turn, call_id))


def assert_view(data):
    snapshot = TemporaryRuleSetProposal.model_validate(data['proposal_snapshot'])
    assert data['kind'] == 'ruleset'
    assert data['edit_id'] == data['proposal_id'] == snapshot.proposal_id
    assert data['version'] == snapshot.version
    assert data['resource_ref'].startswith('resource-ref:')
    assert data['update_arguments']['edit_id'] == snapshot.proposal_id
    assert data['update_arguments']['expected_version'] == snapshot.version
    assert 'content' not in data  # Canonical body occurs only inside proposal_snapshot.
    return snapshot


def draft_args(rule, word):
    return dict(operation='create', scope_decision='none', title='招聘风险调查',
        objective='调查招聘收费风险', platform='xhs',
        ruleset_ref=rule['resource_ref'], lexicon_ref=word['resource_ref'])


def generated(stack, body):
    generator = OfflineGenerator(body)
    stack['tool_service'].resource_generator = generator
    first = _run_scripted_creation_turn(stack, content='先生成规则和词库给我完整看看', actions=[
        ('create_ruleset_proposal', {'generation_request': REQUEST}),
        ('create_lexicon_edit', {'generation_request': REQUEST}),
    ])
    return first, generator


def test_generated_references_create_draft_only_after_complete_presentation(creation_stack, content):
    stack = creation_stack
    # The new result must be sufficient; the old recovery bridge is not invoked.
    manager = stack['app_service'].resource_management
    get_edit = manager.get_edit

    def lexicon_only(edit_id, **kwargs):
        assert not edit_id.startswith('ruleset-proposal:'), 'unnecessary reference conversion'
        return get_edit(edit_id, **kwargs)

    with patch.object(manager, 'get_edit', side_effect=lexicon_only):
        first, generator = generated(stack, content)
        rule, word = first['agent'].results
        snapshot = assert_view(rule)
        assert evidence(first)['snapshot'] == snapshot.model_dump(mode='json')
        assert evidence(first)['text'] in first['result'].answer
        assert _draft_count(stack['creation_store']) == 0
        result, identity, _ = configure(stack, first['session_id'], draft_args(rule, word))
    assert result['status'] == 'ok', result
    judgement = result['data']['draft']['configuration']['judgement']
    assert judgement['proposal_id'] == snapshot.proposal_id
    assert judgement['proposal_version'] == snapshot.version
    assert judgement['content_hash'] == snapshot.content_hash
    assert judgement['content'] == snapshot.content.model_dump(mode='json')
    assert generator.calls == ['ruleset', 'lexicon']
    assert stack['tool_service'].execute_with_identity('configure_investigation_draft',
        draft_args(rule, word), principal=P, identity=identity) == result
    assert _draft_count(stack['creation_store']) == 1
    assert _run_count(stack['creation_store']) == 0
    assert [name for name, _ in first['agent'].calls] == ['create_ruleset_proposal', 'create_lexicon_edit']


@pytest.mark.parametrize('boundary', ['unshown', 'same_turn', 'cross_session', 'stale'])
def test_reference_is_not_adoption_permission(creation_stack, content, boundary):
    stack = creation_stack
    if boundary == 'unshown':
        session = stack['conversation'].create_session(principal=P, workspace_key='unshown')
        rule = invoke(stack, 'create_ruleset_proposal', {'content': content}, session=session.id)['data']
        word = stack['tool_service'].execute('create_lexicon_edit', {'content': lexicon()},
                                            principal=P, session_id=session.id)
        session_id, turn = session.id, None
    else:
        first, _ = generated(stack, content)
        rule, word = first['agent'].results
        session_id, turn = first['session_id'], None
        if boundary == 'same_turn':
            turn = first['turn']
        elif boundary == 'cross_session':
            session_id = stack['conversation'].create_session(principal=P, workspace_key='other').id
        elif boundary == 'stale':
            changed = deepcopy(content); changed['name'] += ' 新版'
            updated = invoke(stack, 'update_ruleset_proposal', {'proposal_id': rule['proposal_id'],
                'expected_version': 1, 'content': changed}, session=session_id, call_id='edit')
            assert updated['status'] == 'ok', updated
    assert_view(rule)
    result, _, _ = configure(stack, session_id, draft_args(rule, word), call_id='rejected', turn=turn)
    assert result['status'] == 'error', result
    expected = {'unshown': 'PROPOSAL_NOT_PRESENTED', 'same_turn': 'PROPOSAL_NOT_PRESENTED',
                'cross_session': 'RESOURCE_REF_INVALID', 'stale': 'RESOURCE_REF_STALE'}
    assert result['error']['code'] == expected[boundary], result
    assert _draft_count(stack['creation_store']) == 0
    assert rows(stack['creation_store'], 'ruleset_proposal_approvals') == []


def test_get_and_update_return_pure_exact_snapshot_with_one_body(creation_stack, content):
    stack = creation_stack
    created = invoke(stack, 'create_ruleset_proposal', {'content': content})
    assert created['status'] == 'ok', created
    initial = assert_view(created['data'])
    changed = deepcopy(content); changed['name'] += ' 修改版'
    updated = invoke(stack, 'update_ruleset_proposal', {'proposal_id': initial.proposal_id,
        'expected_version': 1, 'content': changed}, call_id='update')
    assert updated['status'] == 'ok', updated
    current = assert_view(updated['data'])
    assert current.version == 2
    assert current.content.name == changed['name']
    assert updated['data']['resource_ref'] != created['data']['resource_ref']
    read = invoke(stack, 'get_ruleset_proposal', {'proposal_id': initial.proposal_id}, call_id='read')
    assert read['status'] == 'ok', read
    assert_view(read['data'])
    assert read['data']['proposal_snapshot'] == updated['data']['proposal_snapshot']
    assert read['data']['resource_ref'] == updated['data']['resource_ref']
    assert initial.version == 1  # Later edits do not rewrite an earlier tool receipt.
    assert invoke(stack, 'create_ruleset_proposal', {'content': content}) == created


def bound_turn(stack):
    session = stack['conversation'].create_session(principal=P, workspace_key='projection-fault')
    turn, _ = stack['conversation'].accept_message(session.id, content='生成规则', principal=P,
                                                 client_message_id='generate')
    return session.id, turn.id


def test_committed_create_projection_failure_recovers_without_generation(creation_stack, content):
    stack = creation_stack
    generator = OfflineGenerator(content)
    stack['tool_service'].resource_generator = generator
    session, turn = bound_turn(stack)
    stack['tool_service'].begin_conversation_turn(session, turn)
    args = {'generation_request': REQUEST}
    try:
        with patch.object(ruleset_refs, 'proposal_view', create=True, side_effect=RuntimeError('projection unavailable')):
            failed = invoke(stack, 'create_ruleset_proposal', args, session=session, turn=turn)
        assert failed['status'] == 'error', failed
        assert failed['error']['details']['write_status'] == 'COMMITTED', failed
        assert failed['error']['details']['mutation_applied'] is True
        assert len(rows(stack['creation_store'])) == 1
        recovered = invoke(stack, 'create_ruleset_proposal', args, session=session, turn=turn)
        assert recovered['status'] == 'ok', recovered
        snapshot = assert_view(recovered['data'])
        assert generator.calls == ['ruleset']
        assert len(rows(stack['creation_store'])) == 1
        assert stack['creation_store'].proposal_presentation_snapshots(session_id=session, turn_id=turn) == [
            snapshot.model_dump(mode='json')]
        assert invoke(stack, 'create_ruleset_proposal', args, session=session, turn=turn) == recovered
    finally:
        stack['tool_service'].end_conversation_turn(session)
    assert _draft_count(stack['creation_store']) == _run_count(stack['creation_store']) == 0


@pytest.mark.parametrize('superseded', [False, True])
def test_committed_update_projection_failure_never_repeats_write(creation_stack, content, superseded):
    stack = creation_stack
    created = invoke(stack, 'create_ruleset_proposal', {'content': content})['data']
    changed = deepcopy(content); changed['name'] += ' 第二版'
    args = {'proposal_id': created['proposal_id'], 'expected_version': 1, 'content': changed}
    with patch.object(ruleset_refs, 'proposal_view', create=True, side_effect=RuntimeError('projection unavailable')):
        failed = invoke(stack, 'update_ruleset_proposal', args, call_id='update-lost')
    assert failed['status'] == 'error', failed
    assert failed['error']['details']['write_status'] == 'COMMITTED', failed
    assert failed['error']['details']['mutation_applied'] is True
    if superseded:
        newer = deepcopy(changed); newer['name'] += ' 第三版'
        stack['app_service'].update_ruleset_proposal(created['proposal_id'], session_id='handoff',
                                                     expected_version=2, content=newer)
    recovered = invoke(stack, 'update_ruleset_proposal', args, call_id='update-lost')
    current = stack['app_service'].get_ruleset_proposal(created['proposal_id'], session_id='handoff')
    assert len(rows(stack['creation_store'])) == 1
    if superseded:
        assert recovered['error']['code'] == 'PROPOSAL_WRITE_SUPERSEDED', recovered
        assert recovered['error']['details']['write_status'] == 'COMMITTED'
        assert recovered['error']['details']['mutation_applied'] is True
        assert current.version == 3
        assert current.content.name == newer['name']
    else:
        assert recovered['status'] == 'ok', recovered
        assert assert_view(recovered['data']).version == current.version == 2
        assert current.content.name == changed['name']
        assert invoke(stack, 'update_ruleset_proposal', args, call_id='update-lost') == recovered


def test_interrupted_create_recovers_started_receipt_without_regenerating(creation_stack, content):
    stack = creation_stack
    generator = OfflineGenerator(content)
    stack['tool_service'].resource_generator = generator
    args = {'generation_request': REQUEST}
    with patch.object(ruleset_refs, 'proposal_view', side_effect=SystemExit('process interrupted')):
        with pytest.raises(SystemExit, match='process interrupted'):
            invoke(stack, 'create_ruleset_proposal', args)
    with stack['creation_store']._connect() as db:
        receipt = db.execute('SELECT * FROM investigation_creation_tool_receipts').fetchone()
        assert receipt['status'] == 'STARTED'
        assert receipt['response_json'] == ''
        assert receipt['committed_proposal_snapshot_json']
    restored = invoke(stack, 'create_ruleset_proposal', args)
    assert restored['status'] == 'ok', restored
    assert assert_view(restored['data']).version == 1
    assert generator.calls == ['ruleset']
    assert len(rows(stack['creation_store'])) == 1


@pytest.mark.parametrize('operation', ['create', 'update'])
def test_content_and_recovery_anchor_roll_back_together(creation_stack, content, operation):
    stack = creation_stack
    store = stack['creation_store']
    before = None
    if operation == 'update':
        before = invoke(stack, 'create_ruleset_proposal', {'content': content})['data']['proposal_snapshot']
        changed = deepcopy(content); changed['name'] += ' 不应提交'
        args = {'proposal_id': before['proposal_id'], 'expected_version': 1, 'content': changed}
        name = 'update_ruleset_proposal'
    else:
        args, name = {'content': content}, 'create_ruleset_proposal'
    with patch.object(store, '_bind_proposal_write', side_effect=RuntimeError('anchor failed')):
        failed = invoke(stack, name, args, call_id='anchor-failure')
    assert failed['status'] == 'error', failed
    assert failed['error']['details'].get('write_status') != 'COMMITTED'
    with store._connect() as db:
        receipt = db.execute('SELECT * FROM investigation_creation_tool_receipts WHERE tool_call_id=?',
                             ('anchor-failure',)).fetchone()
        assert receipt['committed_proposal_snapshot_json'] == ''
        if operation == 'create':
            assert db.execute('SELECT count(*) FROM ruleset_proposals').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM resource_edit_history').fetchone()[0] == 0
        else:
            assert store.get_ruleset_proposal(before['proposal_id'], session_id='handoff').model_dump(mode='json') == before
            assert db.execute('SELECT count(*) FROM resource_edit_history WHERE edit_id=?',
                              (before['proposal_id'],)).fetchone()[0] == 1


def test_noop_update_projection_failure_recovers_without_new_version(creation_stack, content):
    stack = creation_stack
    original = invoke(stack, 'create_ruleset_proposal', {'content': content})['data']
    args = {'proposal_id': original['proposal_id'], 'expected_version': 1, 'content': content}
    with patch.object(ruleset_refs, 'proposal_view', side_effect=RuntimeError('projection unavailable')):
        failed = invoke(stack, 'update_ruleset_proposal', args, call_id='noop')
    assert failed['error']['details']['write_status'] == 'COMMITTED', failed
    restored = invoke(stack, 'update_ruleset_proposal', args, call_id='noop')
    assert restored['status'] == 'ok', restored
    assert assert_view(restored['data']).version == 1
    assert restored['data']['proposal_snapshot'] == original['proposal_snapshot']
    assert restored['data']['resource_ref'] == original['resource_ref']
    assert len(rows(stack['creation_store'])) == 1
    assert invoke(stack, 'update_ruleset_proposal', args, call_id='noop') == restored


def test_legacy_bare_success_replays_unchanged_and_can_read_reference(creation_stack, content):
    stack = creation_stack
    store = stack['creation_store']
    proposal = stack['app_service'].create_ruleset_proposal(content, session_id='legacy')
    args = {'content': content}
    receipt = store.begin_tool_execution(session_id='legacy', turn_id='old', tool_call_id='old-call',
        principal=P.id, tool_name='create_ruleset_proposal', arguments=args, is_mutation=True)
    original = {'status': 'ok', 'data': proposal.model_dump(mode='json')}
    store.complete_tool_execution(receipt['receipt_id'], response=original, succeeded=True)
    generator = OfflineGenerator(content)
    stack['tool_service'].resource_generator = generator
    assert invoke(stack, 'create_ruleset_proposal', args, session='legacy', turn='old', call_id='old-call') == original
    read = invoke(stack, 'get_ruleset_proposal', {'proposal_id': proposal.proposal_id},
                  session='legacy', turn='new', call_id='read')
    assert read['status'] == 'ok', read
    assert assert_view(read['data']) == proposal
    assert generator.calls == []
    assert len(rows(store)) == 1
    with store._connect() as db:
        saved = db.execute('SELECT response_json FROM investigation_creation_tool_receipts WHERE receipt_id=?',
                           (receipt['receipt_id'],)).fetchone()[0]
    assert json.loads(saved) == original


def test_recovery_race_returns_original_success_when_original_request_finishes(creation_stack, content):
    stack = creation_stack
    store = stack['creation_store']
    generator = OfflineGenerator(content)
    stack['tool_service'].resource_generator = generator
    args = {'generation_request': REQUEST}
    with patch.object(ruleset_refs, 'proposal_view', side_effect=SystemExit('interrupt')):
        with pytest.raises(SystemExit):
            invoke(stack, 'create_ruleset_proposal', args)
    recover = store.recover_proposal_write
    completed = []

    def original_finishes_before_recovery(receipt_id, principal, *, session_id):
        snapshot = store.committed_proposal_write(receipt_id, principal.id)
        candidate = {'status': 'ok', 'data': ruleset_refs.proposal_view(
            store, snapshot, principal=principal, session_id=session_id)}
        # Duplicate has already observed STARTED. The original commits success
        # immediately before duplicate's transactional recovery operation.
        store.complete_tool_execution(receipt_id, response=candidate, succeeded=True)
        completed.append(candidate)
        return recover(receipt_id, principal, session_id=session_id)

    with patch.object(store, 'recover_proposal_write', side_effect=original_finishes_before_recovery):
        result = invoke(stack, 'create_ruleset_proposal', args)
    assert completed and result == completed[0]
    assert result['status'] == 'ok', result
    assert_view(result['data'])
    assert generator.calls == ['ruleset']
    assert len(rows(store)) == 1
    assert invoke(stack, 'create_ruleset_proposal', args) == result


def test_started_duplicate_replays_original_success_after_later_legal_edit(creation_stack, content):
    stack = creation_stack
    store = stack['creation_store']
    generator = OfflineGenerator(content)
    stack['tool_service'].resource_generator = generator
    args = {'generation_request': REQUEST}
    with patch.object(ruleset_refs, 'proposal_view', side_effect=SystemExit('interrupt')):
        with pytest.raises(SystemExit):
            invoke(stack, 'create_ruleset_proposal', args)
    with store._connect() as db:
        receipt_id = db.execute('SELECT receipt_id FROM investigation_creation_tool_receipts').fetchone()[0]
    snapshot = store.committed_proposal_write(receipt_id, P.id)
    original = {'status': 'ok', 'data': ruleset_refs.proposal_view(
        store, snapshot, principal=P, session_id='handoff')}
    begin = store.begin_tool_execution
    interleavings = []

    def original_completes_and_resource_changes(**kwargs):
        observed = begin(**kwargs)
        assert observed['replay'] and observed['status'] == 'STARTED'
        store.complete_tool_execution(receipt_id, response=original, succeeded=True)
        changed = deepcopy(content); changed['name'] += ' 后续合法更新'
        stack['app_service'].update_ruleset_proposal(snapshot.proposal_id, session_id='handoff',
                                                     expected_version=1, content=changed)
        interleavings.append(True)
        return observed

    with patch.object(store, 'begin_tool_execution', side_effect=original_completes_and_resource_changes):
        result = invoke(stack, 'create_ruleset_proposal', args)
    assert interleavings == [True]
    assert result == original  # Historical success remains exact despite current v2.
    assert result['data']['version'] == 1
    assert stack['app_service'].get_ruleset_proposal(snapshot.proposal_id, session_id='handoff').version == 2
    assert generator.calls == ['ruleset']
    assert len(rows(store)) == 1
    assert invoke(stack, 'create_ruleset_proposal', args) == original
