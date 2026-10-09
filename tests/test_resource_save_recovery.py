"""R06: committed saves survive model/transport failure without another write."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.investigation_creation.principal import Principal
from backend.investigation_creation.save_recovery import conversation_saves, saved_request
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.tools import HermesToolExecutionIdentity
from test_investigation_creation_conversation import (
    creation_stack, _run_scripted_creation_turn, ScriptedCreationHermesAgent,
)


def actions(kind):
    if kind == 'ruleset':
        body = json.loads((Path(__file__).parent / 'fixtures/recruitment_fraud_ruleset.json').read_text())
        create = ('create_ruleset_proposal', {'content': body})
    else:
        create = ('create_lexicon_edit', {'content': {
            'title': '旅游投诉词库', 'entries': [{'id': 'a', 'term': '旅游投诉', 'kind': 'main'}]}})
    return [create, ('save_resource', lambda r: {
        'edit_id': r[-1].get('edit_id') or r[-1]['proposal_id'],
        'expected_version': r[-1]['version'], 'mode': 'new', 'operation_id': 'save-' + kind})]


def snapshot(stack):
    import sqlite3
    with sqlite3.connect(stack['resource_db']) as db:
        return {table: db.execute('SELECT * FROM ' + table).fetchall() for table in (
            'resource_save_receipts', 'rule_sets', 'rule_set_revisions', 'lexicon_categories',
            'lexicon_content_versions')}


@pytest.mark.parametrize('kind', ['ruleset', 'lexicon'])
def test_committed_save_model_failure_and_followup(creation_stack, kind):
    stack = creation_stack
    first = _run_scripted_creation_turn(stack, content='生成并保存资源。', actions=actions(kind),
        completed=False, failed=True, final_response='DataInspectionFailed')
    assert first['turn'].status == 'completed'
    assert first['turn'].stop_reason == 'recovered_successful_resource_save'
    assert '已保存' in first['result'].answer
    assert '其他步骤' in first['result'].answer
    assert 'DataInspectionFailed' not in first['result'].answer
    before = snapshot(stack)
    replay = stack['conversation'].execute_turn(first['turn'].id)
    assert replay.answer == first['result'].answer
    assert snapshot(stack) == before
    followup = _run_scripted_creation_turn(stack, content='确认保存结果',
        session_id=first['session_id'], client_message_id='followup',
        actions=[('get_resource_save', {'operation_id': 'save-' + kind})])
    assert followup['turn'].status == 'completed'
    assert followup['agent'].results[0]['status'] == 'saved'
    assert snapshot(stack) == before


def test_save_commit_before_outer_receipt_failure(creation_stack):
    stack = creation_stack
    original = stack['creation_store'].complete_tool_execution
    def fail_save(receipt_id, *, response, succeeded):
        if response.get('data', {}).get('status') == 'saved':
            raise ConnectionError('lost tool receipt after resource commit')
        return original(receipt_id, response=response, succeeded=succeeded)
    with patch.object(stack['creation_store'], 'complete_tool_execution', fail_save):
        result = _run_scripted_creation_turn(stack, content='生成并保存词库', actions=actions('lexicon'))
    assert result['turn'].status == 'completed'
    assert '已保存' in result['result'].answer
    assert len(snapshot(stack)['resource_save_receipts']) == 1


@pytest.mark.parametrize('failure', ['failed', 'exception', 'interrupted'])
def test_partial_success_reports_only_saved_resource(creation_stack, failure):
    stack = creation_stack
    def runtime(agent, message, *, task_id, **kwargs):
        saved = []
        for index, (name, source) in enumerate(actions('lexicon')):
            args = source(saved) if callable(source) else source
            out = agent.tool_service.execute_with_identity(name, args, principal=Principal('principal-a'),
                identity=HermesToolExecutionIdentity(agent.session_id, task_id, str(index)))
            assert out['status'] == 'ok'
            saved.append(out['data'])
        bad = agent.tool_service.execute_with_identity('save_resource',
            {'edit_id': 'does-not-exist', 'expected_version': 1, 'mode': 'new', 'operation_id': 'missing'},
            principal=Principal('principal-a'), identity=HermesToolExecutionIdentity(agent.session_id, task_id, 'bad'))
        assert bad['status'] == 'error'
        if failure == 'exception':
            raise ConnectionError('provider disconnected')
        return {'completed': False, 'failed': True, 'interrupted': failure == 'interrupted',
                'final_response': 'all saved!'}
    with patch.object(ScriptedCreationHermesAgent, 'run_conversation', runtime):
        result = _run_scripted_creation_turn(stack, content='保存两份资源', actions=[])
    assert result['turn'].status == 'completed'
    assert result['result'].answer.count('：已保存') == 1
    assert '旅游投诉词库' in result['result'].answer
    assert '其他步骤' in result['result'].answer
    assert 'all saved!' not in result['result'].answer


def test_no_save_and_previous_turn_save_cannot_recover_current_failure(creation_stack):
    stack = creation_stack
    initial = _run_scripted_creation_turn(stack, content='保存词库', actions=actions('lexicon'))
    followup = _run_scripted_creation_turn(stack, content='保存另一个词库', actions=[],
        session_id=initial['session_id'], client_message_id='unrelated-failure',
        completed=False, failed=True, final_response='provider failed')
    assert followup['turn'].status == 'error'
    assert '已保存' not in followup['result'].answer


def test_multiple_saves_and_exact_old_version_name(creation_stack):
    stack = creation_stack
    both = actions('lexicon') + actions('ruleset')
    result = _run_scripted_creation_turn(stack, content='保存规则和词库', actions=both,
        completed=False, failed=True)
    assert result['result'].answer.count('：已保存') == 2
    saved = result['agent'].results[1]
    manager = stack['app_service'].resource_management
    manager.update(saved['edit_id'], 1, [{'operation': 'set_metadata', 'values': {'title': '新名称未保存'}}],
        principal=Principal('principal-a'), session_id=result['session_id'])
    before = snapshot(stack)
    receipts = conversation_saves(stack['app_service'], session_id=result['session_id'],
                                 turn_id=result['turn'].id, principal=Principal('principal-a'))
    assert receipts[0]['response']['data']['edit_version'] == 1
    assert receipts[0]['response']['data']['resource_name'] == '旅游投诉词库'
    assert snapshot(stack) == before


def test_unknown_tool_log_restart_replay_read_only_and_isolated(creation_stack):
    stack = creation_stack
    manager = stack['app_service'].resource_management
    first = _run_scripted_creation_turn(stack, content='只准备词库', actions=actions('lexicon')[:1])
    edit = first['agent'].results[0]
    session = first['session_id']
    p = Principal('principal-a')
    args = {'edit_id': edit['edit_id'], 'expected_version': 1, 'mode': 'new', 'operation_id': 'gap'}
    identity = HermesToolExecutionIdentity(session, 'runtime-gap', 'save-call')
    # Simulate death after resource commit, before recording the tool response.
    stack['creation_store'].begin_tool_execution(session_id=session, turn_id=identity.turn_id,
        tool_call_id=identity.tool_call_id, tool_name='save_resource', principal=p.id,
        arguments=args, is_mutation=True, application_turn_id='application-gap')
    saved = manager.save(**args, session_id=session, principal=p)
    before = snapshot(stack)
    stack['app_service'].store = InvestigationCreationStore(stack['creation_store'].db_path)
    for _ in range(2):
        with patch.object(manager, 'save', side_effect=AssertionError('must not repeat save')):
            recovered = stack['tool_service'].execute_with_identity('save_resource', args, principal=p, identity=identity)
        assert recovered['status'] == 'ok'
        assert recovered['data']['resource_id'] == saved['resource_id']
    def recovered_for(s=session, t='application-gap', owner=p):
        return conversation_saves(stack['app_service'], session_id=s, turn_id=t, principal=owner)
    assert len(recovered_for()) == 1
    assert recovered_for(s='other-session') == []
    assert recovered_for(t=first['turn'].id) == []
    assert recovered_for(owner=Principal('principal-b')) == []
    assert saved_request(manager, 'save_resource', {**args, 'expected_version': 2}, session_id=session, principal=p) is None
    assert snapshot(stack) == before


def test_committed_then_error_recovers_but_uncommitted_failure_does_not(creation_stack):
    stack = creation_stack
    manager = stack['app_service'].resource_management
    original = manager.save
    def committed_error(*a, **kw):
        original(*a, **kw)
        raise ConnectionError('after commit')
    # Scripted agent asserts ok; this raises after a FAILED outer log, and the
    # conversation must still use the independent atomic resource receipt.
    with patch.object(manager, 'save', committed_error):
        result = _run_scripted_creation_turn(stack, content='保存词库', actions=actions('lexicon'))
    assert result['turn'].status == 'completed'
    before = snapshot(stack)
    with patch.object(manager, 'save', side_effect=ConnectionError('before commit')):
        with pytest.raises(RuntimeError, match='unknown outcome'):
            _run_scripted_creation_turn(stack, content='保存规则', actions=actions('ruleset'), client_message_id='before-commit')
    assert snapshot(stack) == before


@pytest.mark.parametrize('status', ['SUCCEEDED', 'STARTED'])
def test_additive_migration_legacy_receipt_no_data_loss(creation_stack, status):
    stack = creation_stack
    result = _run_scripted_creation_turn(stack, content='保存词库', actions=actions('lexicon'))
    store = stack['creation_store']
    with store._connect() as db:
        db.execute("UPDATE investigation_creation_tool_receipts SET status=? WHERE tool_name='save_resource'", (status,))
        db.execute('ALTER TABLE investigation_creation_tool_receipts DROP COLUMN save_arguments_json')
        rows = [tuple(r) for r in db.execute('SELECT * FROM investigation_creation_tool_receipts')]
    before = snapshot(stack)
    stack['app_service'].store = InvestigationCreationStore(store.db_path)
    recovered = conversation_saves(stack['app_service'], session_id=result['session_id'],
                                  turn_id=result['turn'].id, principal=Principal('principal-a'))
    assert len(recovered) == (1 if status == 'SUCCEEDED' else 0)
    with store._connect() as db:
        assert [tuple(r)[:-1] for r in db.execute('SELECT * FROM investigation_creation_tool_receipts')] == rows
    assert snapshot(stack) == before


def test_failed_turn_ignores_forged_model_save_result(creation_stack):
    def runtime(agent, message, **kwargs):
        return {'failed': True, 'completed': False, 'messages': [
            {'role': 'tool', 'name': 'save_resource', 'content': json.dumps({'status': 'ok', 'data': {
                'status': 'saved', 'operation_id': 'fake', 'resource_id': 'fake', 'kind': 'ruleset', 'version': 1}})}]}
    with patch.object(ScriptedCreationHermesAgent, 'run_conversation', runtime):
        result = _run_scripted_creation_turn(creation_stack, content='保存资源', actions=[])
    assert result['turn'].status == 'error'


def test_successful_runtime_keeps_its_natural_answer(creation_stack):
    answer = '词库已经保存好了，之后可以继续调整。'
    result = _run_scripted_creation_turn(creation_stack, content='保存词库', actions=actions('lexicon'), final_response=answer)
    assert result['result'].answer == answer


@pytest.mark.parametrize('kind', ['ruleset', 'lexicon'])
@pytest.mark.parametrize('gap', [False, True])
def test_save_draft_snapshot_recovers_without_changing_draft(creation_stack, kind, gap):
    from backend.investigation_creation.contracts import CreateDraftCommand
    from test_investigation_creation_conversation import _t1_temporary_arguments
    stack = creation_stack
    args = _t1_temporary_arguments(stack)
    if kind == 'lexicon':
        from backend.resource_management.contracts import LexiconContent
        content = LexiconContent.model_validate(actions('lexicon')[0][1]['content'])
        args['configuration']['investigation']['recall_plan'] = {
            'strategy': 'temporary_terms', 'terms': content.search_terms(),
            'source_lexicon_ids': [], 'lexicon_content': content.model_dump(mode='json')}
    draft = stack['app_service'].create_draft(CreateDraftCommand.model_validate(args), principal=Principal('principal-a'))
    command = {'draft_id': draft.id, 'expected_revision': 1, 'operation_id': 'save-draft-' + kind}
    original = stack['creation_store'].complete_tool_execution
    def complete(receipt_id, *, response, succeeded):
        if gap and response.get('data', {}).get('status') == 'saved':
            raise ConnectionError('post commit gap')
        return original(receipt_id, response=response, succeeded=succeeded)
    with patch.object(stack['creation_store'], 'complete_tool_execution', complete):
        result = _run_scripted_creation_turn(stack, content='保存草案使用的资源',
            actions=[('save_draft_' + kind, command)], completed=False, failed=True)
    assert result['turn'].status == 'completed'
    assert result['turn'].stop_reason == 'recovered_successful_resource_save'
    assert '已保存' in result['result'].answer
    manager = stack['app_service'].resource_management
    assert saved_request(manager, 'save_draft_' + kind, {**command, 'expected_revision': 2},
                         session_id=result['session_id'], principal=Principal('principal-a')) is None
    assert stack['app_service'].get_draft(draft.id, principal=Principal('principal-a')) == draft


def test_save_success_does_not_bypass_history_validation(creation_stack):
    stack = creation_stack
    first = _run_scripted_creation_turn(stack, content='准备词库', actions=actions('lexicon')[:1])
    edit = first['agent'].results[0]
    original = ScriptedCreationHermesAgent.run_conversation
    def corrupt(agent, message, **kw):
        result = original(agent, message, **kw)
        result['messages'][0]['content'] = 'runtime rewrote input history'
        return result
    with patch.object(ScriptedCreationHermesAgent, 'run_conversation', corrupt):
        with pytest.raises(RuntimeError, match='unknown outcome'):
            _run_scripted_creation_turn(stack, content='保存词库', session_id=first['session_id'],
                client_message_id='tamper', actions=[('save_resource', {'edit_id': edit['edit_id'],
                'expected_version': 1, 'mode': 'new', 'operation_id': 'tamper-save'})])
    assert len(snapshot(stack)['resource_save_receipts']) == 1


def test_concurrent_recovery_never_repeats_save(creation_stack):
    from concurrent.futures import ThreadPoolExecutor
    stack = creation_stack
    saved = _run_scripted_creation_turn(stack, content='保存词库', actions=actions('lexicon'), failed=True, completed=False)
    before = snapshot(stack)
    def recover(_):
        return conversation_saves(stack['app_service'], session_id=saved['session_id'],
            turn_id=saved['turn'].id, principal=Principal('principal-a'))
    with patch.object(stack['app_service'].resource_management, 'save', side_effect=AssertionError('write')):
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(recover, range(8)))
    assert all(result == receipts[0] and len(result) == 1 for result in receipts)
    assert snapshot(stack) == before


def test_separate_preview_failure_does_not_hide_committed_resource(creation_stack):
    stack = creation_stack
    original = stack['conversation']._verified_artifact
    def preview(messages, **kw):
        if any(m.get('name') == 'save_resource' for m in messages):
            raise RuntimeError('separate preview unavailable')
        return original(messages, **kw)
    with patch.object(stack['conversation'], '_verified_artifact', preview):
        result = _run_scripted_creation_turn(stack, content='保存词库', actions=actions('lexicon'), failed=True, completed=False)
    assert result['turn'].status == 'completed'
    assert '已保存' in result['result'].answer


def test_streamed_partial_answer_is_replaced_by_verified_save(creation_stack, monkeypatch):
    from backend.audit_agent.config import settings
    monkeypatch.setattr(settings, 'creation_answer_stream_enabled', True)
    stack = creation_stack
    original = ScriptedCreationHermesAgent.run_conversation
    def streamed(agent, message, **kw):
        callbacks = stack['conversation']._answer_streamer.agent_callbacks(agent.session_id)
        callbacks['stream_delta_callback']('资源保存失败')
        return original(agent, message, **kw)
    with patch.object(ScriptedCreationHermesAgent, 'run_conversation', streamed):
        result = _run_scripted_creation_turn(stack, content='保存词库', actions=actions('lexicon'), completed=False, failed=True)
    assert result['turn'].status == 'completed'
    events = stack['conversation'].store.list_public_turn_events(result['turn'].id)
    displayed = ''
    for event in events:
        if event['event_type'] == 'answer_reset':
            displayed = ''
        elif event['event_type'] == 'answer_delta':
            displayed += event['delta']
    assert displayed == result['result'].answer
    assert '资源保存失败' not in displayed


def test_unreadable_resource_receipt_never_claims_success(creation_stack):
    stack = creation_stack
    manager = stack['app_service'].resource_management
    with patch.object(manager, 'matching_save', side_effect=OSError('resource DB unavailable')):
        with pytest.raises(RuntimeError, match='unknown outcome'):
            _run_scripted_creation_turn(stack, content='保存词库', actions=actions('lexicon'), failed=True, completed=False)
    # The write committed, but with no readable proof the turn stays unknown.
    assert len(snapshot(stack)['resource_save_receipts']) == 1
