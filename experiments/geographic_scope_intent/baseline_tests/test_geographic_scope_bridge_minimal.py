"""Local protocol/diagnostic checks, not evidence of model understanding."""
from copy import deepcopy
import json
import socket

import pytest

from experiments.geographic_scope_intent.bridge_minimal import (
    build_request, diagnose_progress, run_candidate,
)
from experiments.geographic_scope_intent.contrast_intent import request_for_arm
from experiments.geographic_scope_intent.contrast_runner import run_arm
from experiments.geographic_scope_intent.data import load_cases
from experiments.geographic_scope_intent.runner import ScriptedProvider


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('local tests must not open a socket')
    monkeypatch.setattr(socket.socket, 'connect', blocked)


def case(identity='city-kelamayi'):
    return next(c for c in load_cases() if c['case_id'] == identity)


def response(index, *tools, text=None):
    message = {'role': 'assistant', 'content': text}
    if tools:
        message['tool_calls'] = [
            {'id': f'call-{index}-{j}', 'type': 'function', 'function': {
                'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}
            for j, (name, args) in enumerate(tools)
        ]
    return {'model': 'deepseek-v4-pro-ga-260813', 'choices': [{'message': message}]}


def record(c):
    return {'action': 'set', 'target_text': '克拉玛依',
            'selection_policy': 'local_first_with_supplement',
            'source_quote': c['current_user']['content']}


def configure(c, scope=None):
    draft = c['current_draft']
    if draft:
        result = {'operation': 'update', 'draft_id': draft['draft_id'],
                  'expected_revision': draft['revision']}
    else:
        result = {'operation': 'create', 'title': '模拟调查',
                  'objective': '调查欠薪舆情', 'platform': 'dy',
                  **c['resource_state']['refs']}
    if scope:
        result['relevance_scope_change'] = deepcopy(scope)
    return result


@pytest.mark.parametrize('variant', ['confirmation', 'state', 'recording', 'combined'])
def test_only_system_guidance_changes_and_no_tool_forcing(variant):
    c = case()
    before = deepcopy(c)
    original = request_for_arm(c, 'bridge')
    candidate = build_request(c, variant)
    assert candidate['tools'] == original['tools']
    assert candidate['messages'][1:] == original['messages'][1:]
    assert candidate['messages'][0]['content'] != original['messages'][0]['content']
    assert 'tool_choice' not in candidate
    assert c == before


def test_baseline_request_is_exact_original():
    assert build_request(case(), 'baseline') == request_for_arm(case(), 'bridge')


def test_unknown_variant_fails_before_dispatch():
    with pytest.raises(ValueError):
        build_request(case(), 'forced')


def test_labels_and_answers_do_not_enter_candidate_request():
    c = case()
    expected = build_request(c)
    for key in ('expected', 'category', 'critical', 'critical_negative', 'split',
                'allowed_mutation_tools', 'synthetic'):
        c[key] = 'ASSESSMENT_ONLY_SENTINEL'
    assert build_request(c) == expected
    assert 'ASSESSMENT_ONLY_SENTINEL' not in json.dumps(expected)


def test_real_provider_is_blocked_before_any_call():
    class Forbidden:
        mode = 'real'
        def complete(self, request):
            raise AssertionError('must not call provider')
    with pytest.raises(PermissionError):
        run_candidate(case(), Forbidden())
    with pytest.raises(PermissionError):
        run_candidate(case(), Forbidden(), enable_real=True)


def test_actual_requests_are_captured_and_overlay_is_not_duplicated():
    c = case()
    seen = []
    class Capture:
        mode = 'scripted'
        def complete(self, request):
            seen.append(deepcopy(request))
            return (response(1, ('record_geographic_intent', {'action': 'keep'}))
                    if len(seen) == 1 else response(2, text='模拟记录已收到。'))
    t = run_candidate(c, Capture())
    assert t['requests'] == seen
    assert len(seen) == 2
    for request in seen:
        assert request['messages'][0] == build_request(c)['messages'][0]
        assert request['tools'] == request_for_arm(c, 'bridge')['tools']


def test_missing_record_is_reported_without_inventing_one():
    c = case()
    t = run_candidate(c, ScriptedProvider([response(1, text='调查克拉玛依。')]))
    assert t['progress']['record_status'] == 'missing'
    assert t['calls'] == []
    assert not t['progress']['draft_configured']


def test_correct_set_is_recorded_and_actually_applied():
    c = case()
    scope = record(c)
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', scope)),
        response(2, ('configure_investigation_draft', configure(c, scope))),
        response(3, text='模拟草案已准备，尚未启动真实调查。'),
    ]))
    assert t['progress']['handoff_status'] == 'applied'
    assert t['progress']['draft_configured'] and t['progress']['final_reply_complete']
    assert t['calls'][1]['receipt']['data']['scope'] == scope


def test_recorded_clear_is_pending_until_configure_runs():
    c = case('clear-unlimited')
    scope = {'action': 'clear', 'source_quote': c['current_user']['content']}
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', scope)),
        response(2, text='是否应用？'),
    ]))
    assert t['progress']['handoff_status'] == 'not_applied'
    assert not t['progress']['draft_configured']
    assert t['progress']['final_reply_complete']


def test_clear_lands_only_when_explicit_simulated_update_succeeds():
    c = case('clear-unlimited')
    scope = {'action': 'clear', 'source_quote': c['current_user']['content']}
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', scope),
                 ('configure_investigation_draft', configure(c, scope))),
        response(2, text='模拟草案已取消地域限制。'),
    ]))
    assert t['progress']['handoff_status'] == 'applied'
    assert t['calls'][1]['receipt']['data']['scope'] is None


def test_keep_retains_scope_and_unrelated_configuration():
    c = case('keep-title')
    args = {**configure(c), 'title': '十月欠薪专项'}
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', {'action': 'keep'}),
                 ('configure_investigation_draft', args)),
        response(2, text='模拟标题已修改。'),
    ]))
    data = t['calls'][1]['receipt']['data']
    assert data['scope'] == c['current_draft']['scope']
    assert data['ruleset_ref'] == c['current_draft']['ruleset_ref']
    assert data['lexicon_ref'] == c['current_draft']['lexicon_ref']
    assert t['progress']['handoff_status'] == 'preserved'


def test_no_existing_scope_clear_is_flagged_but_not_auto_corrected():
    c = case('assistant-city')
    args = {'action': 'clear', 'source_quote': c['current_user']['content']}
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', args)), response(2, text='模拟记录。'),
    ]))
    assert t['progress']['clear_without_existing_scope']
    assert t['calls'][0]['arguments'] == args


def test_fourth_round_configuration_and_final_reply_are_separate():
    c = case()
    scope = record(c)
    replies = [
        response(1, ('record_geographic_intent', scope)),
        response(2, ('query_investigation_options', {'platform': 'dy', 'mode': 'search'})),
        response(3, ('read_resource', {'kind': 'ruleset', 'resource_id': 'fixture-ruleset'}),
                 ('read_resource', {'kind': 'lexicon', 'resource_id': 'fixture-lexicon'})),
        response(4, ('configure_investigation_draft', configure(c, scope))),
        response(5, text='must not be called'),
    ]
    original = run_arm(c, ScriptedProvider(replies), 'bridge')
    candidate = run_candidate(c, ScriptedProvider(replies))
    assert original['termination'] == candidate['termination'] == 'loop_limit'
    assert len(candidate['requests']) == len(candidate['responses']) == 4
    assert candidate['calls'] == original['calls']
    assert candidate['progress']['draft_configured']
    assert candidate['progress']['pending_final_reply']
    assert not candidate['progress']['final_reply_complete']


def test_rejected_configuration_cannot_be_reported_as_applied():
    c = case('clear-unlimited')
    scope = {'action': 'clear', 'source_quote': c['current_user']['content']}
    args = configure(c, scope)
    args['expected_revision'] += 100
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', scope),
                 ('configure_investigation_draft', args)),
    ]))
    assert t['termination'] == 'invalid_arguments'
    assert not t['progress']['draft_configured']
    assert t['progress']['handoff_status'] == 'not_applied'


def test_mismatched_configured_scope_is_not_accepted_as_handoff():
    c = case()
    scope = record(c)
    wrong = {**scope, 'target_text': '大庆'}
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', scope),
                 ('configure_investigation_draft', configure(c, wrong))),
        response(2, text='模拟草案。'),
    ]))
    assert t['progress']['draft_configured']
    assert t['progress']['handoff_status'] == 'mismatch'
    assert t['calls'][1]['arguments']['relevance_scope_change'] == wrong


def test_execution_remains_blocked():
    t = run_candidate(case(), ScriptedProvider([
        response(1, ('confirm_and_queue_investigation', {})),
    ]))
    assert not t['calls'][0]['receipt']['ok']
    assert not t['progress']['draft_configured']


def test_offline_diagnosis_never_changes_original_trace_or_labels():
    c = case('clear-unlimited')
    t = run_arm(c, ScriptedProvider([response(1, text='是否应用？')]), 'bridge')
    before = deepcopy(t)
    c['expected'] = 'POISONED_GOLD'
    result = diagnose_progress(c, t)
    assert result['record_status'] == 'missing'
    assert t == before


def test_unset_scope_keep_is_preserved_when_creating_draft():
    c = case('assistant-city')
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', {'action': 'keep'}),
                 ('configure_investigation_draft', configure(c))),
        response(2, text='模拟全国草案已准备。'),
    ]))
    assert t['progress']['handoff_status'] == 'preserved'
    assert not t['progress']['clear_without_existing_scope']
    assert 'relevance_scope_change' not in t['calls'][1]['arguments']
    assert t['calls'][1]['receipt']['data']['scope'] is None


def test_scope_none_without_explicit_clear_does_not_prove_clear_handoff():
    c = case('assistant-city')
    args = {'action': 'clear', 'source_quote': c['current_user']['content']}
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', args),
                 ('configure_investigation_draft', configure(c))),
        response(2, text='模拟全国草案。'),
    ]))
    assert t['progress']['draft_configured']
    assert t['progress']['handoff_status'] == 'not_applied'
    assert t['progress']['clear_without_existing_scope']


def test_multiple_or_rejected_records_do_not_prove_single_handoff():
    c = case()
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', {'action': 'keep'}),
                 ('record_geographic_intent', {'action': 'keep'})),
        response(2, text='模拟记录。'),
    ]))
    assert t['progress']['record_status'] == 'multiple'
    assert t['progress']['handoff_status'] == 'not_observable'
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', {'action': 'clarify'})),
    ]))
    assert t['termination'] == 'invalid_arguments'
    assert t['progress']['record_status'] == 'missing'


def test_timeout_keeps_attempt_and_does_not_retry():
    class Timeout:
        mode = 'scripted'
        def complete(self, request):
            raise TimeoutError('offline simulated timeout')
    t = run_candidate(case(), Timeout())
    assert t['termination'] == 'timeout'
    assert len(t['requests']) == 1 and len(t['responses']) == 0
    assert t['requests'][0] == build_request(case())
    assert not t['progress']['final_reply_complete']


def test_existing_equal_set_without_explicit_update_is_not_new_handoff():
    c = case('keep-title')
    scope = record(c)
    c['current_draft']['scope'] = deepcopy(scope)
    args = {**configure(c), 'title': '模拟修改标题'}
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', scope),
                 ('configure_investigation_draft', args)),
        response(2, text='模拟标题已修改。'),
    ]))
    assert t['progress']['handoff_status'] == 'not_applied'


def test_clear_followed_by_title_update_remains_applied():
    c = case('clear-unlimited')
    scope = {'action': 'clear', 'source_quote': c['current_user']['content']}
    title = {**configure(c), 'title': '模拟新标题'}
    title['expected_revision'] += 1
    t = run_candidate(c, ScriptedProvider([
        response(1, ('record_geographic_intent', scope),
                 ('configure_investigation_draft', configure(c, scope))),
        response(2, ('configure_investigation_draft', title)),
        response(3, text='模拟草案已取消地域并更新标题。'),
    ]))
    assert t['progress']['successful_configuration_count'] == 2
    assert t['progress']['handoff_status'] == 'applied'


def test_authorized_real_boundary_preserves_exact_request_without_network():
    seen = []
    class FakeReal:
        mode = 'real'
        def complete(self, request):
            seen.append(deepcopy(request))
            return response(1, text='只验证传输接线。')
    t = run_candidate(case(), FakeReal(), enable_real=True,
                      authorization_label='test boundary, fake provider only')
    assert t['mode'] == 'real'
    assert t['requests'] == seen == [build_request(case())]


@pytest.mark.parametrize('identity', [c['case_id'] for c in load_cases() if c['split'] == 'dev'])
def test_all_fixed_dev_inputs_can_be_prepared_without_network(identity):
    c = case(identity)
    t = run_candidate(c, ScriptedProvider([response(1, text='仅测试协议。')]))
    assert t['requests'][0] == build_request(c)
    assert t['mode'] == 'scripted'
    assert all(call['receipt']['simulated'] for call in t['calls'])
