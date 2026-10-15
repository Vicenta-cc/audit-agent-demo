"""Dataset integrity checks; these do not evaluate a real model."""
from copy import deepcopy
import json

import pytest

from experiments.geographic_scope_intent.data import (
    REQUIRED_CATEGORIES, load_cases, validate_dataset,
)


def test_dataset_covers_all_p0_categories_and_labels():
    cases = load_cases()
    assert len(cases) >= 24
    assert REQUIRED_CATEGORIES <= {case['category'] for case in cases}
    assert {case['expected']['label'] for case in cases} == {
        'set', 'clear', 'keep', 'clarify', 'defer', 'no_task',
    }
    assert {case['split'] for case in cases} == {'dev', 'holdout'}
    targets = {case['expected']['target_text'] for case in cases if case['expected']['label'] == 'set'}
    assert len(targets) >= 6
    assert all(case['synthetic'] for case in cases)


def test_complete_conversations_share_split():
    splits = {}
    for case in load_cases():
        assert splits.setdefault(case['conversation_id'], case['split']) == case['split']
    assert len(splits) < len(load_cases())  # Actual multi-turn groups, not only metadata.


def test_rejects_same_conversation_cross_split():
    cases = load_cases()
    repeated = deepcopy(cases[0])
    repeated['case_id'] = 'another-turn'
    repeated['application_turn'] = 2
    repeated['current_user']['application_turn'] = 2
    repeated['split'] = 'holdout'
    with pytest.raises(ValueError, match='crosses'):
        validate_dataset([cases[0], repeated])


@pytest.mark.parametrize('field', [
    'current_user', 'history', 'application_turn', 'task_round', 'current_draft',
    'resources_ready', 'read_fixtures', 'expected', 'resource_state',
    'allowed_mutation_tools',
])
def test_rejects_incomplete_case(field):
    case = deepcopy(load_cases()[0])
    del case[field]
    with pytest.raises(ValueError, match='missing required'):
        validate_dataset([case])


@pytest.mark.parametrize('mutation', [
    lambda c: c.update(synthetic=False),
    lambda c: c.update(application_turn=True),
    lambda c: c['current_user'].update(principal_id='someone-else'),
    lambda c: c['current_user'].update(role='assistant'),
    lambda c: c['expected'].update(selection_policy='national_supplement'),
    lambda c: c['expected'].update(source_quote=None),
    lambda c: c['read_fixtures']['read_resource'].update(simulated=False),
])
def test_rejects_invalid_types_identity_labels_and_unmarked_fixtures(mutation):
    case = deepcopy(load_cases()[0])
    mutation(case)
    with pytest.raises(ValueError):
        validate_dataset([case])


def test_rejects_missing_response_criteria_and_duplicate_ids():
    case = next(c for c in load_cases() if c['expected']['label'] == 'clarify')
    broken = deepcopy(case)
    broken['expected']['acceptable_response_patterns'] = []
    with pytest.raises(ValueError, match='response labels'):
        validate_dataset([broken])
    with pytest.raises(ValueError, match='duplicate case_id'):
        validate_dataset([case, deepcopy(case)])


def test_temporary_resources_are_completely_displayed_before_later_adoption():
    case = next(c for c in load_cases() if c['category'] == 'later_resource_adoption'
                and c['resource_state']['kind'] == 'temporary')
    state = case['resource_state']
    assert state['shown'] and state['adoption']
    assert state['displayed_turn'] < state['adoption_turn'] == case['application_turn']
    presentation = next(m for m in case['history'] if m['role'] == 'assistant')
    assert presentation['application_turn'] == state['displayed_turn']
    for response in case['read_fixtures']['get_resource_edit']['responses']:
        assert json.dumps(response['result']['content'], ensure_ascii=False) in presentation['content']
        assert response['result']['version'] == 1
    assert case['expected']['source_quote'] in case['history'][0]['content']
    assert case['history'][0]['task_round'] == case['task_round']


def test_resource_bodies_follow_current_production_contracts_without_services():
    from backend.resource_management.contracts import LexiconContent
    from backend.rulesets.contracts import RuleSetContent
    for case in load_cases():
        for fixture in case['read_fixtures'].values():
            for response in fixture.get('responses', []):
                body = response['result']
                contract = RuleSetContent if body['kind'] == 'ruleset' else LexiconContent
                contract.model_validate(body['content'])


def test_old_round_isolation_is_explicit_and_enabled():
    for case in load_cases():
        if case['category'] == 'independent_new_round':
            assert case['feature_flags']['investigation_multi_round_enabled']
            assert all(m['task_round'] < case['task_round'] for m in case['history'])
            assert case['current_draft'] is None
            assert case['expected']['label'] in {'set', 'keep'}


def test_custom_jsonl_load(tmp_path):
    case = load_cases()[0]
    path = tmp_path / 'cases.jsonl'
    path.write_text(json.dumps(case, ensure_ascii=False) + '\n\n', encoding='utf-8')
    assert load_cases(path) == [case]


def test_curated_evidence_spans_are_in_bound_task_user_messages():
    mutation_cases = [c for c in load_cases() if c['expected']['label'] in {'set', 'clear'}]
    assert len(mutation_cases) == 13
    for case in mutation_cases:
        expected = case['expected']
        assert expected['source_evidence_span'] in expected['source_quote']
        sources = [m for m in [*case['history'], case['current_user']]
                   if m['role'] == 'user' and m['principal_id'] == case['principal_id']
                   and m['conversation_id'] == case['conversation_id']
                   and m['task_round'] == case['task_round']
                   and m['application_turn'] <= case['application_turn']
                   and expected['source_quote'] in m['content']]
        assert len(sources) == 1
        assert expected['source_evidence_span'] in sources[0]['content']


@pytest.mark.parametrize('span', [None, '', ' ', '伪造的地域要求'])
def test_rejects_invalid_evidence_span(span):
    case = deepcopy(load_cases()[0])
    case['expected']['source_evidence_span'] = span
    with pytest.raises(ValueError, match='source_evidence_span'):
        validate_dataset([case])


def test_unready_resources_represent_partial_and_unadopted_states():
    cases = {c['case_id']: c for c in load_cases()}
    partial = cases['defer-missing-rules']
    assert not partial['resources_ready']
    assert set(partial['resource_state']['refs']) == {'lexicon_ref'}
    assert all(r['result']['kind'] == 'lexicon'
               for r in partial['read_fixtures']['read_resource']['responses'])
    unadopted = cases['defer-unadopted']
    assert not unadopted['resources_ready']
    assert unadopted['resource_state']['shown']
    assert not unadopted['resource_state']['adoption']
    assert unadopted['resource_state']['adoption_turn'] is None
    assert unadopted['resource_state']['displayed_turn'] < unadopted['application_turn']


def test_mutation_authority_is_curated_from_each_user_request():
    preparation_cases = {'defer-missing-all', 'adoption-temp-first', 'adoption-formal-first'}
    for case in load_cases():
        allowed = case['allowed_mutation_tools']
        if case['case_id'] in preparation_cases:
            assert allowed == ['create_ruleset_proposal', 'create_lexicon_edit']
            assert any(word in case['current_user']['content'] for word in ('准备', '资源'))
        elif case['expected']['label'] in {'set', 'clear', 'keep'}:
            assert allowed == ['configure_investigation_draft']
        else:
            assert allowed == []
        assert 'save_resource' not in allowed
        assert 'confirm_investigation_draft' not in allowed


@pytest.mark.parametrize('tools', [None, 'save_resource', [''], ['save_resource', 'save_resource'],
                                  [False], ['not a tool'], ['save-resource']])
def test_rejects_invalid_mutation_authority(tools):
    case = deepcopy(load_cases()[0])
    case['allowed_mutation_tools'] = tools
    with pytest.raises(ValueError, match='allowed_mutation_tools'):
        validate_dataset([case])
