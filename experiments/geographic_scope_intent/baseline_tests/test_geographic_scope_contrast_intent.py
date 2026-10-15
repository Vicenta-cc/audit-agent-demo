"""Offline lock for the contrast-only observation boundary."""
from copy import deepcopy
from collections import Counter
import json

import pytest

from experiments.geographic_scope_intent.data import load_cases
from experiments.geographic_scope_intent.runner import request_for
from experiments.geographic_scope_intent.contrast_intent import (
    ContrastDispatcher, load_labels, observer_tool, request_for_arm,
)


def dev_cases():
    return [case for case in load_cases() if case['split'] == 'dev']


def case_named(name='city-kelamayi'):
    return next(case for case in dev_cases() if case['case_id'] == name)


def tool_names(request):
    return [tool['function']['name'] for tool in request['tools']]


@pytest.mark.parametrize('case', dev_cases(), ids=lambda case: case['case_id'])
def test_full_request_preserves_frozen_constructor(case):
    assert json.dumps(request_for_arm(case, 'full'), ensure_ascii=False) == json.dumps(request_for(case), ensure_ascii=False)


@pytest.mark.parametrize('arm', ['bridge', 'simple'])
@pytest.mark.parametrize('case', dev_cases(), ids=lambda case: case['case_id'])
def test_visible_history_context_and_observer_contract_match(case, arm):
    full = request_for(case)
    actual = request_for_arm(case, arm)
    assert actual['messages'][1:] == full['messages'][1:]
    marker = '本实验 application context（全部为合成模拟状态）：'
    assert actual['messages'][0]['content'].split(marker)[-1] == full['messages'][0]['content'].split(marker)[-1]
    assert actual['tools'][-1] == {'type':'function','function':observer_tool()}
    assert 'tool_choice' not in actual
    if arm == 'bridge':
        assert actual['tools'][:-1] == full['tools']
    else:
        assert tool_names(actual) == ['record_geographic_intent']


@pytest.mark.parametrize('arm', ['full', 'bridge', 'simple'])
def test_no_gold_or_inaccessible_messages_in_requests(arm):
    case = case_named()
    case['expected']['reason'] = 'UNIQUE_GOLD_SENTINEL'
    foreign = deepcopy(case['current_user'])
    foreign.update(principal_id='foreign-user', sequence=1, content='FOREIGN_SENTINEL')
    future = deepcopy(case['current_user'])
    future.update(application_turn=case['application_turn'] + 1, content='FUTURE_SENTINEL')
    case['history'] += [foreign, future]
    payload = json.dumps(request_for_arm(case, arm), ensure_ascii=False)
    assert all(marker not in payload for marker in ['UNIQUE_GOLD_SENTINEL', 'FOREIGN_SENTINEL', 'FUTURE_SENTINEL', 'source_evidence_span', 'acceptable_response_patterns'])


@pytest.mark.parametrize('arguments', [
    {'action':'set', 'target_text':'乌鲁木齐', 'source_quote':'调查乌鲁木齐欠薪', 'selection_policy':'local_first_with_supplement'},
    {'action':'clear', 'source_quote':'不限制地区了'},
    {'action':'keep'}, {'action':'no_task'},
    {'action':'clarify', 'question':'朝阳指哪个地区？'},
])
@pytest.mark.parametrize('arm', ['bridge', 'simple'])
def test_observer_valid_shape_is_capture_only(arguments, arm):
    dispatcher = ContrastDispatcher(case_named(), arm)
    initial = deepcopy(dispatcher.draft)
    receipt = dispatcher.dispatch('record_geographic_intent', arguments)
    assert receipt['ok'] is True
    assert receipt['simulated'] is True
    assert receipt['mutation_applied'] is False
    assert receipt['captured_only'] is True
    assert dispatcher.draft == initial
    assert dispatcher.calls[-1]['arguments'] == arguments
    assert 'data' not in receipt


def test_observer_does_not_return_semantic_source_or_target_gold():
    dispatcher = ContrastDispatcher(case_named(), 'simple')
    wrong = {'action':'set','target_text':'朝阳','source_quote':'不是任何用户原话','selection_policy':'local_only'}
    assert dispatcher.dispatch('record_geographic_intent', wrong)['ok'] is True
    correct = {'action':'set','target_text':'克拉玛依','source_quote':case_named()['current_user']['content'],'selection_policy':'local_first_with_supplement'}
    assert dispatcher.dispatch('record_geographic_intent', correct) == dispatcher.calls[0]['receipt']


@pytest.mark.parametrize('arguments', [
    {'action':'defer'}, {'action':'set'},
    {'action':'set','target_text':' ','source_quote':'原话','selection_policy':'local_only'},
    {'action':'set','target_text':'北京','source_quote':' ','selection_policy':'local_only'},
    {'action':'set','target_text':'北京','source_quote':'原话','selection_policy':'anywhere'},
    {'action':'clear','source_quote':'原话','target_text':'北京'},
    {'action':'keep','target_text':'北京'}, {'action':'no_task','source_quote':'原话'},
    {'action':'clarify','question':'哪个朝阳','target_text':'北京'},
    {'action':'clarify','question':' '}, None, 'bad',
])
def test_observer_rejects_invalid_shape(arguments):
    receipt = ContrastDispatcher(case_named(), 'simple').dispatch('record_geographic_intent', arguments)
    assert receipt['ok'] is False
    assert receipt['code'] == 'INVALID_ARGUMENTS'
    assert 'data' not in receipt


@pytest.mark.parametrize('arm', ['full', 'simple'])
def test_tools_are_allowlisted_per_arm(arm):
    name = 'record_geographic_intent' if arm == 'full' else 'configure_investigation_draft'
    receipt = ContrastDispatcher(case_named(), arm).dispatch(name, {})
    assert receipt['ok'] is False and receipt['code'] == 'UNKNOWN_TOOL'


@pytest.mark.parametrize('arm', ['full', 'bridge'])
def test_business_execution_remains_blocked(arm):
    dispatcher = ContrastDispatcher(case_named(), arm)
    tool = next(tool['function'] for tool in request_for(case_named())['tools'] if tool['function']['name'] == 'confirm_and_queue_investigation')
    # This is a business capability boundary, whether schema validation or the
    # explicit execution block rejects these arguments.
    receipt = dispatcher.dispatch(tool['name'], {})
    assert receipt['ok'] is False and receipt['mutation_applied'] is False


def test_bridge_shares_business_draft_calls_with_observer():
    dispatcher = ContrastDispatcher(case_named('keep-title'), 'bridge')
    draft = dispatcher.draft
    receipt = dispatcher.dispatch('configure_investigation_draft', {
        'operation':'update','draft_id':draft['draft_id'], 'expected_revision':draft['revision'], 'title':'更新标题',
    })
    assert receipt['ok'] is True
    assert dispatcher.draft['title'] == '更新标题'
    dispatcher.dispatch('record_geographic_intent', {'action':'keep'})
    assert len(dispatcher.calls) == 2
    assert dispatcher.draft['title'] == '更新标题'


def test_sidecar_is_dev_only_and_separates_readiness():
    labels = load_labels()
    assert set(labels) == {case['case_id'] for case in dev_cases()}
    assert Counter(label['action'] for label in labels.values()) == {'set':7,'clear':1,'keep':8,'clarify':1,'no_task':1}
    for name, target in [('defer-missing-all','乌鲁木齐'), ('adoption-temp-first','克拉玛依')]:
        assert labels[name]['action'] == 'set'
        assert labels[name]['target_text'] == target
        assert labels[name]['workflow_state'] == 'defer'
        assert labels[name]['source_evidence_span'] in labels[name]['source_quote']
    assert labels['adoption-temp-later']['source_quote'] == labels['adoption-temp-first']['source_quote']


@pytest.mark.parametrize('arm', ['', 'baseline', None])
def test_unknown_arm_fails_closed(arm):
    with pytest.raises(ValueError):
        request_for_arm(case_named(), arm)
    with pytest.raises(ValueError):
        ContrastDispatcher(case_named(), arm)


def test_all_mutation_labels_bind_real_accessible_user_evidence():
    from experiments.geographic_scope_intent.contract import validate_source
    labels = load_labels()
    for case in dev_cases():
        label = labels[case['case_id']]
        if label['action'] in {'set','clear'}:
            assert validate_source(case, label['source_quote'])['ok'] is True
            assert label['source_evidence_span'] in label['source_quote']


@pytest.mark.parametrize('case', dev_cases(), ids=lambda case: case['case_id'])
def test_full_dispatcher_read_is_equivalent_to_existing_fixture(case):
    from experiments.geographic_scope_intent.sandbox import FixtureDispatcher
    expected = FixtureDispatcher(case)
    actual = ContrastDispatcher(case, 'full')
    arguments = {'kind':'all'}
    assert actual.dispatch('query_investigation_options', arguments) == expected.dispatch('query_investigation_options', arguments)
    assert actual.calls == expected.calls
    assert actual.draft == expected.draft


def test_observer_requests_and_dispatch_do_not_open_network(monkeypatch):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError('network forbidden in isolated observer')
    monkeypatch.setattr(socket, 'socket', forbidden)
    for arm in ['full','bridge','simple']:
        request_for_arm(case_named(), arm)
        receipt = ContrastDispatcher(case_named(), arm).dispatch('record_geographic_intent', {'action':'keep'})
        assert receipt['mutation_applied'] is False
