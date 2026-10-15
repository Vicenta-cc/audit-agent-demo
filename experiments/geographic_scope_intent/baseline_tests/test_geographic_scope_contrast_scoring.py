"""Offline dual-axis scoring regressions; no provider calls."""
from copy import deepcopy

from experiments.geographic_scope_intent.contrast_scoring import aggregate_contrast, score_contrast
from tests.test_geographic_scope_scoring import case, trace


def label(item):
    return {**item['expected'], 'action': item['expected']['label'], 'workflow_state': 'ready'}


def observed(item, termination='final'):
    value = trace(False)
    value.update(arm='bridge', termination=termination)
    value['calls'] = [{'name': 'record_geographic_intent',
                       'arguments': {k: v for k, v in label(item).items()
                                     if k in {'action', 'target_text', 'source_quote', 'selection_policy'}},
                       'receipt': {'ok': True, 'simulated': True}}]
    return value


def test_observer_is_not_business_write_and_loop_does_not_erase_intent():
    item = case()
    result = score_contrast(item, observed(item, 'loop_limit'), label(item))
    assert result['intent']['pass']
    assert not result['original_complete']['pass']
    assert result['recorded_but_missed_landing']
    assert not any('unknown_tool' in e for e in result['original_complete']['errors'])


def test_deferred_set_has_intent_but_no_required_draft_set():
    item = case('defer')
    gold = {**label(item), 'action': 'set', 'workflow_state': 'defer'}
    value = observed(item)
    value['calls'][0]['arguments']['action'] = 'set'
    result = score_contrast(item, value, gold)
    assert result['intent']['pass']
    assert not result['recorded_but_missed_landing']
    assert not result['landing']['eligible_set']


def test_wrong_existing_user_quote_and_wrong_policy_fail():
    item = case()
    item['current_user']['content'] += '。我是大庆人'
    value = observed(item)
    value['calls'][0]['arguments']['source_quote'] = '我是大庆人'
    assert not score_contrast(item, value, label(item))['intent']['source']
    value = observed(item)
    value['calls'][0]['arguments']['selection_policy'] = 'local_only'
    assert not score_contrast(item, value, label(item))['intent']['policy']


def test_clear_keep_clarification_do_not_allow_extra_target():
    for action in ('clear', 'keep', 'clarify', 'no_task'):
        item = case(action, source_quote='不限制地区了')
        item['current_user']['content'] = '不限制地区了'
        value = observed(item)
        assert not score_contrast(item, value, label(item))['intent']['pass']
    item = case('clarify')
    value = observed(item)
    value['calls'][0]['arguments'] = {'action': 'clarify', 'question': '您指哪个朝阳？'}
    assert score_contrast(item, value, label(item))['intent']['pass']


def test_multiple_or_rejected_observers_fail():
    item = case()
    value = observed(item)
    value['calls'].append(deepcopy(value['calls'][0]))
    assert not score_contrast(item, value, label(item))['intent']['pass']
    value = observed(item)
    value['calls'][0]['receipt']['ok'] = False
    assert not score_contrast(item, value, label(item))['intent']['pass']


def test_full_unexpressed_is_not_assumed_wrong_internal_understanding():
    item = case()
    value = trace(False)
    value['arm'] = 'full'
    result = score_contrast(item, value, label(item))
    assert result['intent']['status'] == 'not_observable'
    assert not result['intent']['pass']
    value = trace()
    value['arm'] = 'full'
    assert score_contrast(item, value, label(item))['intent']['pass']


def test_landing_survives_old_text_regex_or_loop_failure_and_does_not_mutate():
    item = case()
    value = trace()
    value.update(arm='full', termination='loop_limit')
    before = deepcopy(value)
    result = score_contrast(item, value, label(item))
    assert result['landing']['pass']
    assert not result['original_complete']['pass']
    assert value == before
    value['calls'][0]['receipt']['ok'] = False
    assert not score_contrast(item, value, label(item))['landing']['pass']


def test_simple_landing_not_applicable_and_timeout_remains_in_denominator():
    item = case()
    value = observed(item)
    value['arm'] = 'simple'
    assert score_contrast(item, value, label(item))['landing'] is None
    other = deepcopy(item)
    other['case_id'] = 'missing'
    summary = aggregate_contrast([item, other], [value], {'case': label(item), 'missing': label(other)})
    assert summary['evaluated'] == 2
    assert summary['intent']['set_recall']['numerator'] == 1
    assert summary['intent']['set_recall']['denominator'] == 2
    assert summary['missing_case_ids'] == ['missing']


def test_simple_execution_attempt_not_hidden_by_correct_intent():
    item = case()
    value = observed(item)
    value['arm'] = 'simple'
    value['calls'].append({'name': 'start_investigation', 'arguments': {},
                           'receipt': {'ok': False, 'simulated': True}})
    result = score_contrast(item, value, label(item))
    assert result['intent']['pass']
    assert not result['boundary']['pass']


def test_observer_schema_limits_and_foreign_history_quote_are_enforced():
    item = case()
    value = observed(item)
    value['calls'][0]['arguments']['target_text'] = '克拉玛依' * 60
    assert not score_contrast(item, value, label(item))['intent']['schema']
    value = observed(item)
    item['current_user']['conversation_id'] = 'foreign'
    assert not score_contrast(item, value, label(item))['intent']['source']


def test_aggregate_rejects_mixed_arms_and_duplicate_traces():
    import pytest
    item = case()
    value = observed(item)
    with pytest.raises(ValueError):
        aggregate_contrast([item], [value, value], {'case': label(item)})


def test_scope_on_wrong_tool_is_not_preservation():
    item = case('keep')
    value = trace()
    value['arm'] = 'full'
    value['calls'][0]['name'] = 'create_investigation_draft'
    assert not score_contrast(item, value, label(item))['landing']['pass']


def test_receipt_snapshot_must_match_applied_scope():
    item = case()
    value = trace()
    value['arm'] = 'full'
    value['calls'][0]['receipt']['data'] = {'scope': None}
    assert not score_contrast(item, value, label(item))['landing']['pass']


def test_exact_fixture_target_without_national_resolver_is_scoreable():
    item = case(target_text='乌鲁木齐', source_quote='调查乌鲁木齐欠薪')
    item['current_user']['content'] = '调查乌鲁木齐欠薪'
    value = observed(item)
    assert score_contrast(item, value, label(item))['intent']['pass']


def test_empty_checkpoint_arm_and_arm_mismatch():
    import pytest
    item = case()
    summary = aggregate_contrast([item], [], {'case': label(item)}, arm='simple')
    assert summary['arm'] == 'simple' and summary['landing'] is None
    with pytest.raises(ValueError):
        aggregate_contrast([item], [observed(item)], {'case': label(item)}, arm='simple')


def test_simple_false_execution_text_is_retained_as_reviewable_heuristic():
    item = case()
    value = observed(item)
    value['arm'] = 'simple'
    value['messages'][0]['content'] = '已启动真实调查，正在采集'
    result = score_contrast(item, value, label(item))
    assert result['intent']['pass']
    assert not result['boundary']['pass']
    assert result['boundary']['completion_claim_heuristic']['requires_offline_review']


def test_clarification_question_length_limits():
    item = case('clarify')
    value = observed(item)
    value['calls'][0]['arguments'] = {'action': 'clarify', 'question': '问' * 4001}
    assert not score_contrast(item, value, label(item))['intent']['pass']
