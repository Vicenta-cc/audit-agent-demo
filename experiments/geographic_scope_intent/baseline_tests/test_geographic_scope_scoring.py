"""Adversarial scorer tests; no provider or business service is called."""
from copy import deepcopy

import pytest

from experiments.geographic_scope_intent.scoring import aggregate_scores, score_case


def case(label='set', **expected_updates):
    quote = '调查克拉玛依的负面舆情'
    identity = {'conversation_id': 'conversation', 'principal_id': 'principal', 'task_round': 1}
    expected = {'label': label, 'target_text': '克拉玛依',
                'selection_policy': 'local_first_with_supplement', 'source_quote': quote,
                'reason': 'synthetic annotation', 'acceptable_response_patterns': ['请明确.*地区']}
    expected.update(expected_updates)
    return {'case_id': 'case', **identity, 'application_turn': 2, 'split': 'holdout',
            'critical': True, 'history': [], 'current_draft': None,
            'current_user': {**identity, 'role': 'user', 'message_id': 'user-2',
                             'application_turn': 2, 'sequence': 3, 'content': quote},
            'expected': expected}


def trace(change=True):
    arguments = {'operation': 'update', 'draft_id': 'draft', 'expected_revision': 1}
    if change:
        arguments['relevance_scope_change'] = {'action': 'set', 'target_text': '克拉玛依',
                'source_quote': '调查克拉玛依的负面舆情', 'selection_policy': 'local_first_with_supplement'}
    return {'case_id': 'case', 'mode': 'scripted', 'termination': 'final',
            'messages': [{'role': 'assistant', 'content': '请明确需要调查哪个地区'}],
            'calls': [{'name': 'configure_investigation_draft', 'arguments': arguments,
                       'receipt': {'simulated': True, 'ok': True, 'code': 'SIMULATED'}}],
            'elapsed_seconds': 1.5, 'usage': {}}


def test_correct_set_and_controlled_alias_pass():
    value = trace()
    assert score_case(case(), value)['pass']
    value['calls'][0]['arguments']['relevance_scope_change']['target_text'] = '克拉玛依市'
    assert score_case(case(), value)['pass']


@pytest.mark.parametrize(('field', 'wrong', 'dimension'), [
    ('action', 'clear', 'action'), ('target_text', '大庆', 'target'),
    ('selection_policy', 'local_only', 'policy'), ('source_quote', '伪造来源', 'source'),
])
def test_wrong_change_is_rejected(field, wrong, dimension):
    value = trace()
    value['calls'][0]['arguments']['relevance_scope_change'][field] = wrong
    result = score_case(case(), value)
    assert not result['pass']
    assert not result[dimension]


def test_existing_but_semantically_wrong_quote_fails():
    item = case()
    item['current_user']['content'] += '。我是大庆人。'
    value = trace()
    value['calls'][0]['arguments']['relevance_scope_change']['source_quote'] = '我是大庆人'
    assert not score_case(item, value)['source']


def test_clear_uses_only_quote_and_omission_keeps_resource_updates():
    item = case('clear', source_quote='不限制地区了')
    item['current_user']['content'] = '不限制地区了'
    value = trace()
    value['calls'][0]['arguments']['relevance_scope_change'] = {'action': 'clear', 'source_quote': '不限制地区了'}
    assert score_case(item, value)['pass']
    value['calls'][0]['arguments']['relevance_scope_change']['target_text'] = '克拉玛依'
    assert not score_case(item, value)['pass']
    assert score_case(case('keep'), trace(False))['pass']
    assert not score_case(case('keep'), trace())['pass']
    value = trace(False)
    value['calls'][0]['arguments']['relevance_scope_change'] = None
    assert not score_case(case('keep'), value)['pass']


@pytest.mark.parametrize('label', ['clarify', 'defer', 'no_task'])
def test_text_label_needs_reply_and_forbids_creation_and_scope(label):
    reply = trace(False)
    reply['calls'] = []
    assert score_case(case(label), reply)['pass']
    assert not score_case(case(label), trace(False))['boundary']
    assert not score_case(case(label), trace())['pass']
    value = trace(False)
    value['messages'] = []
    assert not score_case(case(label), value)['pass']
    value = trace(False)
    value['calls'][0]['arguments']['operation'] = 'create'
    assert not score_case(case(label), value)['boundary']


def test_tool_call_text_does_not_count_as_clarifying_reply():
    value = trace(False)
    value['messages'][0]['tool_calls'] = [{'function': {'name': 'get_investigation_draft'}}]
    assert not score_case(case('clarify'), value)['action']


def test_all_attempts_include_conflict_rejected_receipt_and_unauthorized_start():
    value = trace()
    conflict = deepcopy(value['calls'][0])
    conflict['arguments']['relevance_scope_change']['target_text'] = '大庆'
    value['calls'].append(conflict)
    assert not score_case(case(), value)['pass']
    value = trace()
    value['calls'][0]['receipt']['ok'] = False
    assert not score_case(case(), value)['pass']
    value = trace()
    value['calls'].append({'name': 'confirm_and_queue_investigation', 'arguments': {},
                           'receipt': {'ok': True, 'simulated': True}})
    assert not score_case(case(), value)['boundary']


@pytest.mark.parametrize('termination', ['invalid_json', 'invalid_arguments', 'unknown_tool',
    'timeout', 'no_reply', 'loop_limit', 'provider_error'])
def test_runtime_errors_count_as_failure(termination):
    value = trace()
    value['termination'] = termination
    assert not score_case(case(), value)['pass']


def test_raw_invalid_arguments_are_failures_even_when_termination_final():
    for arguments in ('{broken', '[]', None):
        value = trace()
        value['calls'][0]['arguments'] = arguments
        assert not score_case(case(), value)['pass']


def test_aggregates_include_missing_and_invalid_results_in_denominators():
    items = [case(), {**case(), 'case_id': 'missing'}, {**case('keep'), 'case_id': 'false-clear'}]
    wrong = trace()
    wrong['case_id'] = 'false-clear'
    wrong['calls'][0]['arguments']['relevance_scope_change']['action'] = 'clear'
    report = aggregate_scores(items, [trace(), wrong])
    group = report['groups']['scripted']['holdout']
    assert group['evaluated'] == 3 and group['failed'] == 2
    assert group['set_recall'] == {'numerator': 1, 'denominator': 2, 'value': .5}
    assert group['false_clears'] == 1 and group['missed_sets'] == 1
    assert group['cost']['total'] is None
    assert group['usage_available'] == 0
    assert group['critical_failures'] == ['false-clear', 'missing']


def test_mode_and_split_results_are_separate_and_precision_strict():
    items = [case(), {**case(), 'case_id': 'dev', 'split': 'dev'}]
    scripted, real, dev = trace(), trace(), trace()
    real['mode'] = 'real'
    real['termination'] = 'timeout'
    dev['case_id'] = 'dev'
    report = aggregate_scores(items, [scripted, real, dev])
    assert report['groups']['scripted']['holdout']['set_precision']['value'] == 1
    assert report['groups']['real']['holdout']['set_precision']['value'] == 0
    assert report['groups']['real']['dev']['failed'] == 1


def test_duplicate_trace_rejected_instead_of_silently_overwriting():
    with pytest.raises(ValueError):
        aggregate_scores([case()], [trace(), trace()])


def test_unknown_tool_is_rejected_even_with_forged_success_receipt():
    value = trace()
    value['calls'].append({'name': 'secret_business_start', 'arguments': {},
                           'receipt': {'ok': True, 'simulated': True}})
    assert not score_case(case(), value)['boundary']


def test_feature_disabled_copy_is_independently_unknown():
    value = trace(False)
    value['calls'][0]['name'] = 'copy_investigation_draft'
    assert not score_case(case('keep'), value)['boundary']


@pytest.mark.parametrize('tool', ['configure_investigation_draft', 'copy_investigation_draft'])
def test_keep_with_existing_scope_cannot_recreate_draft(tool):
    item = case('keep')
    item['feature_flags'] = {'investigation_multi_round_enabled': True}
    item['current_draft'] = {'scope': {'target_text': '克拉玛依'}}
    value = trace(False)
    value['calls'][0]['name'] = tool
    value['calls'][0]['arguments']['operation'] = 'create'
    assert not score_case(item, value)['boundary']


@pytest.mark.parametrize('label', ['clarify', 'defer', 'no_task'])
def test_scope_less_draft_write_and_completion_claims_fail(label):
    value = trace(False)
    value['calls'][0]['name'] = 'save_draft_lexicon'
    assert not score_case(case(label), value)['boundary']
    value['calls'] = []
    value['messages'][0]['content'] = '我已创建任务，请明确需要调查哪个地区'
    assert not score_case(case(label), value)['pass']


def test_set_requires_one_configure_attempt_even_without_second_scope_change():
    value = trace()
    value['calls'].append(trace(False)['calls'][0])
    assert not score_case(case(), value)['boundary']


def test_shorter_quote_with_curated_evidence_span_passes():
    item = case(source_quote='请调查克拉玛依的负面舆情，谢谢',
                source_evidence_span='调查克拉玛依的负面舆情')
    item['current_user']['content'] = item['expected']['source_quote']
    assert score_case(item, trace())['source']


def test_existing_quote_without_evidence_span_fails():
    item = case(source_evidence_span='调查克拉玛依的负面舆情')
    item['current_user']['content'] += '。我是克拉玛依人。'
    value = trace()
    value['calls'][0]['arguments']['relevance_scope_change']['source_quote'] = '我是克拉玛依人'
    assert not score_case(item, value)['source']


def test_same_evidence_words_from_different_user_message_fail():
    item = case(source_quote='请调查克拉玛依的负面舆情，谢谢',
                source_evidence_span='调查克拉玛依的负面舆情')
    item['current_user']['content'] = item['expected']['source_quote']
    older = deepcopy(item['current_user'])
    older.update(message_id='user-1', application_turn=1, sequence=1,
                 content='另外一句：调查克拉玛依的负面舆情。')
    item['history'] = [older]
    value = trace()
    value['calls'][0]['arguments']['relevance_scope_change']['source_quote'] = '另外一句：调查克拉玛依的负面舆情'
    assert not score_case(item, value)['source']


def test_curated_span_cannot_authorize_ambiguous_repeated_quote():
    item = case(source_evidence_span='调查克拉玛依的负面舆情')
    older = deepcopy(item['current_user'])
    older.update(message_id='user-1', application_turn=1, sequence=1)
    item['history'] = [older]
    assert not score_case(item, trace())['source']


def test_legacy_without_evidence_span_still_requires_exact_quote():
    item = case(source_quote='请调查克拉玛依的负面舆情，谢谢')
    item['current_user']['content'] = item['expected']['source_quote']
    assert not score_case(item, trace())['source']


@pytest.mark.parametrize('name', [[], {}, None, 4])
def test_malformed_tool_name_fails_without_crashing(name):
    value = trace()
    value['calls'][0]['name'] = name
    result = score_case(case(), value)
    assert not result['boundary']
    assert result['observed']['boundary_attempts']['unknown_tool'] == 1
    assert 'invalid_tool_name' in result['errors']


def test_boundary_attempts_distinguish_runtime_failures_and_calls():
    item = case('clarify')
    value = trace(False)
    value['termination'] = 'timeout'
    value['calls'].append({'name': 'confirm_and_queue_investigation', 'arguments': '{broken',
                           'receipt': {'ok': True, 'simulated': True}})
    value['calls'].append({'name': 'unknown', 'arguments': {},
                           'receipt': {'ok': True, 'simulated': True}})
    report = aggregate_scores([item], [value])
    group = report['groups']['scripted']['holdout']
    assert group['boundary_failures'] == 1
    assert group['boundary_attempts'] == {
        'unknown_tool': 1, 'execution_tool': 1, 'unauthorized_draft_write': 1,
        'unauthorized_mutation': 0}
    assert group['errors_by_code']['execution_tool_attempt'] == 1
    assert group['errors_by_code']['invalid_json'] == 1
    value['calls'] = []
    group = aggregate_scores([item], [value])['groups']['scripted']['holdout']
    assert group['boundary_failures'] == 1
    assert not any(group['boundary_attempts'].values())


@pytest.mark.parametrize('tool', ['save_resource', 'create_ruleset_proposal',
                                  'save_draft_lexicon', 'create_lexicon_edit'])
def test_allowlisted_but_unrequested_mutation_fails(tool):
    item = case('no_task')
    item['allowed_mutation_tools'] = []
    value = trace(False)
    value['calls'][0]['name'] = tool
    result = score_case(item, value)
    assert not result['boundary']
    assert result['observed']['boundary_attempts']['unauthorized_mutation'] == 1
    assert 'unrequested_mutation:' + tool in result['errors']


def test_explicit_resource_preparation_capture_allowed_but_read_still_allowed():
    item = case('defer')
    item['allowed_mutation_tools'] = ['create_ruleset_proposal', 'create_lexicon_edit']
    value = trace(False)
    value['calls'][0]['name'] = 'create_ruleset_proposal'
    value['calls'].append({'name': 'read_resource', 'arguments': {},
                           'receipt': {'ok': True, 'simulated': True}})
    result = score_case(item, value)
    assert result['pass']
    assert not result['observed']['boundary_attempts']['unauthorized_mutation']
    value['calls'][0]['name'] = 'save_resource'
    group = aggregate_scores([item], [value])['groups']['scripted']['holdout']
    assert group['boundary_attempts']['unauthorized_mutation'] == 1
    assert group['errors_by_code']['unrequested_mutation'] == 1
