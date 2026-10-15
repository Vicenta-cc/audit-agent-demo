"""A generation failure is not a committed write or unlimited retry authority."""
from unittest.mock import Mock

import pytest

from backend.investigation_creation.tools import HermesToolExecutionIdentity
from backend.investigation_creation.principal import Principal
from backend.resource_management.contracts import ResourceError, LexiconContent
from backend.rulesets.contracts import RuleSetContent
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content

P = Principal('principal-a', role='admin')
REQUEST = {'generation_request': {'objective': '招聘风险', 'platform': 'dy'}}


def failed():
    return ResourceError('生成校验失败', code='RESOURCE_GENERATION_INVALID', details={
        'generation_stage': 'validation', 'retryable': True,
        'resource_created': False, 'mutation_applied': False,
    })


def invoke(tools, name, call, args=None):
    return tools.execute_with_identity(name, args or REQUEST, principal=P,
        identity=HermesToolExecutionIdentity('retry-session', 'retry-turn', call))


@pytest.mark.parametrize('name', ['create_ruleset_proposal', 'create_lexicon_edit'])
def test_failed_generation_new_call_corrects_once_and_success_deduplicates(creation_stack, content, name):
    tools = creation_stack['tool_service']
    valid = (RuleSetContent.model_validate(content) if name == 'create_ruleset_proposal'
             else LexiconContent(title='词库', entries=[{'id': 'm', 'kind': 'main', 'term': '招聘押金'}]))
    tools.resource_generator = Mock()
    tools.resource_generator.generate.side_effect = [failed(), valid]
    first = invoke(tools, name, 'first')
    assert first['status'] == 'error'
    assert invoke(tools, name, 'first') == first
    changed = {'generation_request': {**REQUEST['generation_request'], 'requirements': '保留风险条件'}}
    second = invoke(tools, name, 'second', changed)
    assert second['status'] == 'ok', second
    assert invoke(tools, name, 'third', changed) == second
    assert tools.resource_generator.generate.call_count == 2


@pytest.mark.parametrize('name', ['create_ruleset_proposal', 'create_lexicon_edit'])
def test_two_failures_exhaust_budget_even_with_new_ids(creation_stack, name):
    tools = creation_stack['tool_service']
    tools.resource_generator = Mock()
    tools.resource_generator.generate.side_effect = [failed(), failed(), failed()]
    assert invoke(tools, name, 'first')['status'] == 'error'
    second = invoke(tools, name, 'second')
    assert second['error']['details']['retryable'] is False
    assert invoke(tools, name, 'third')['status'] == 'error'
    assert tools.resource_generator.generate.call_count == 2


@pytest.mark.parametrize('name', ['create_ruleset_proposal', 'create_lexicon_edit'])
def test_failed_generation_does_not_authorize_handwritten_import(creation_stack, content, name):
    tools = creation_stack['tool_service']
    tools.resource_generator = Mock()
    tools.resource_generator.generate.side_effect = [failed()]
    invoke(tools, name, 'first')
    body = content if name == 'create_ruleset_proposal' else {'title': '词库', 'entries': [{'id': 'm', 'kind': 'main', 'term': '词'}]}
    result = invoke(tools, name, 'manual', {'content': body})
    assert result['status'] == 'error'
    assert result['error']['code'] == 'RESOURCE_GENERATION_RETRY_REQUIRES_REQUEST'
    assert tools.resource_generator.generate.call_count == 1


def test_started_or_committed_write_cannot_become_generation_retry(creation_stack, content):
    tools = creation_stack['tool_service']
    store = creation_stack['creation_store']
    receipt = store.begin_tool_execution(session_id='retry-session', turn_id='retry-turn',
        tool_call_id='first', principal=P.id, tool_name='create_ruleset_proposal',
        arguments=REQUEST, is_mutation=True)
    tools.resource_generator = Mock()
    result = invoke(tools, 'create_ruleset_proposal', 'second')
    assert result['error']['code'] == 'MUTATION_RESULT_UNKNOWN'
    tools.resource_generator.generate.assert_not_called()
    store.complete_tool_execution(receipt['receipt_id'], response={
        'status': 'error', 'error': {'code': 'RESOURCE_GENERATION_INVALID', 'details': failed().details}}, succeeded=False)
    with store._connect() as db:
        db.execute("UPDATE investigation_creation_tool_receipts SET committed_proposal_snapshot_json='{}' WHERE receipt_id=?", (receipt['receipt_id'],))
    # The store must retain the original receipt for committed-result recovery.
    replay = store.begin_tool_execution(session_id='retry-session', turn_id='retry-turn',
        tool_call_id='third', principal=P.id, tool_name='create_ruleset_proposal',
        arguments=REQUEST, is_mutation=True)
    assert replay['replay'] and replay['receipt_id'] == receipt['receipt_id']


@pytest.mark.parametrize('name', ['create_ruleset_proposal', 'create_lexicon_edit'])
def test_failed_call_identity_cannot_change_input_and_policy_is_not_retryable(creation_stack, name):
    tools = creation_stack['tool_service']
    tools.resource_generator = Mock()
    blocked = ResourceError('内容拦截', code='INPUT_CONTENT_BLOCKED', details={
        'generation_stage': 'generation', 'retryable': False,
        'resource_created': False, 'mutation_applied': False})
    tools.resource_generator.generate.side_effect = blocked
    first = invoke(tools, name, 'first')
    changed = {'generation_request': {**REQUEST['generation_request'], 'requirements': '改写'}}
    assert invoke(tools, name, 'first', changed)['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    assert invoke(tools, name, 'second') == first
    assert tools.resource_generator.generate.call_count == 1


def test_only_one_concurrent_corrected_attempt_can_own_a_receipt(creation_stack):
    from concurrent.futures import ThreadPoolExecutor
    store = creation_stack['creation_store']
    first = store.begin_tool_execution(session_id='retry-session', turn_id='retry-turn',
        tool_call_id='first', principal=P.id, tool_name='create_ruleset_proposal',
        arguments=REQUEST, is_mutation=True)
    store.complete_tool_execution(first['receipt_id'], response={
        'status': 'error', 'error': {'code': 'RESOURCE_GENERATION_INVALID', 'details': failed().details}}, succeeded=False)
    def begin(call):
        return store.begin_tool_execution(session_id='retry-session', turn_id='retry-turn',
            tool_call_id=call, principal=P.id, tool_name='create_ruleset_proposal',
            arguments=REQUEST, is_mutation=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(begin, ['second', 'third']))
    assert sum(not result['replay'] for result in results) == 1
    assert len({result['receipt_id'] for result in results}) == 1
