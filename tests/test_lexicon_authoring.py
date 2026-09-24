"""New-generation DTO, deterministic storage fields and redacted diagnostics."""
import json
from unittest.mock import Mock

import pytest

from backend.resource_management.contracts import LexiconContent, ResourceError
from backend.resource_management.generation import _validate_content, generation_messages
from backend.resource_management.generation_contracts import ResourceGenerationRequest
from backend.resource_management.lexicon_authoring import GeneratedLexicon
from backend.rulesets.contracts import RuleSetContent
from test_resource_generation import REQUEST, authoring_lexicon, generator, lexicon
from test_ruleset_proposal_presentation import content


def parse(body, **overrides):
    return _validate_content('lexicon', json.dumps(body, ensure_ascii=False),
                             ResourceGenerationRequest(**REQUEST, **overrides))


def test_ids_parents_and_active_states_are_backend_owned_and_deterministic():
    body = authoring_lexicon(3)
    body['themes'][0]['note'] = '主题说明'
    body['themes'][0]['variants'][0].update(note='待验证；正常噪声', platform='抖音', risk_level='低')
    body['themes'].append({'term': '其他主题', 'variants': [{'term': '另一个候选'}]})
    one, two = parse(body), parse(body)
    assert one == two
    assert one.search_terms() == ['招聘押金0', '招聘押金1', '招聘押金2', '另一个候选']
    assert all(e.enabled for e in one.entries)
    mains = {e.id for e in one.entries if e.kind == 'main'}
    assert all(e.parent_id in mains for e in one.entries if e.kind == 'variant')
    assert one.entries[1].note == '待验证；正常噪声'
    assert one.entries[1].platform == '抖音'
    assert one.entries[1].risk_level == '低'
    assert len({e.id for e in one.entries}) == len(one.entries)
    assert 'id' not in body['themes'][0]  # caller payload is not mutated


def test_model_schema_has_no_mechanical_storage_fields():
    schema = GeneratedLexicon.model_json_schema()
    for definition in [schema, *schema['$defs'].values()]:
        assert not {'id', 'kind', 'parent_id', 'enabled'} & definition['properties'].keys()
    system = generation_messages('lexicon', ResourceGenerationRequest(**REQUEST))[0]['content']
    actual_schema = json.loads(system.split('JSON Schema:\n', 1)[1])
    assert actual_schema == schema


@pytest.mark.parametrize('field,value', [('id', 'model-id'), ('enabled', False), ('parent_id', 'wrong'), ('kind', 'tag')])
def test_model_cannot_override_mechanical_fields(field, value):
    body = authoring_lexicon()
    body['themes'][0]['variants'][0][field] = value
    with pytest.raises(ResourceError) as caught:
        parse(body)
    assert caught.value.details['validation_stage'] == 'authoring_schema'
    assert caught.value.details['validation_errors'][0]['type'] == 'extra_forbidden'


def test_legacy_disabled_theme_is_not_silently_reenabled_on_import():
    old = lexicon(2)
    old['entries'][0]['enabled'] = False
    imported = LexiconContent.model_validate(old)
    assert imported.search_terms() == []
    assert not imported.entries[0].enabled
    # New authoring cannot emit that contradictory parent/child state.
    assert parse(authoring_lexicon(2)).search_terms() == ['招聘押金0', '招聘押金1']


def test_alternatives_and_tags_do_not_enter_search_projection():
    body = authoring_lexicon(2)
    body['themes'][0]['alternatives'] = [{'term': '用户要求保留的停用备选'}]
    body['tags'] = [{'term': '分类标签'}]
    result = parse(body)
    assert result.search_terms() == ['招聘押金0', '招聘押金1']
    assert not next(e for e in result.entries if e.term == '用户要求保留的停用备选').enabled
    assert next(e for e in result.entries if e.term == '分类标签').kind == 'tag'


@pytest.mark.parametrize('body,stage,constraint', [
    ({'title': '缺少主题'}, 'authoring_schema', 'schema_validation'),
    ({'title': '空变体', 'themes': [{'term': '主题', 'variants': []}]}, 'authoring_schema', 'schema_validation'),
    ({'title': '空词', 'themes': [{'term': '主题', 'variants': [{'term': '  '}]}]}, 'authoring_schema', 'blank_term'),
    ({'title': '重复', 'themes': [{'term': '主题', 'variants': [{'term': '重复'}, {'term': '重复'}]}]}, 'storage_contract', 'duplicate_term_platform_match_type'),
    (authoring_lexicon(11), 'search_constraints', 'default_count_exceeded'),
])
def test_validation_reports_exact_stage_and_constraint(body, stage, constraint):
    with pytest.raises(ResourceError) as caught:
        parse(body)
    details = caught.value.details
    assert details['validation_stage'] == stage
    assert details['validation_errors'][0]['constraint'] == constraint
    assert not details['mutation_applied'] and not details['retryable']


@pytest.mark.parametrize('overrides,reason', [
    ({'requested_count': 3}, 'requested_count_mismatch'),
    ({'exact_terms': ['不在输出里的原文']}, 'exact_terms_mismatch'),
])
def test_original_word_and_count_fences_remain(overrides, reason):
    with pytest.raises(ResourceError) as caught:
        parse(authoring_lexicon(2), **overrides)
    assert caught.value.details['validation_errors'][0]['constraint'] == reason


def test_diagnostics_log_fields_not_raw_response_and_optional_private_sink(caplog):
    marker = 'PRIVATE_RAW_DO_NOT_LOG'
    body = authoring_lexicon()
    body['themes'][0]['variants'][0]['platform'] = {'private': marker}
    body[marker] = marker
    gen, client, _ = generator(body)
    capture = Mock()
    gen.diagnostic_sink = capture
    with pytest.raises(ResourceError) as caught:
        gen.generate('lexicon', ResourceGenerationRequest(**REQUEST))
    details = caught.value.details
    assert details['http_status'] == 200 and details['finish_reason'] == 'stop'
    assert details['authoring_version'] == '2'
    assert details['provider_request_id'] == 'provider-request'
    assert len(details['response_hash']) == 64 and details['diagnostic_id']
    assert marker not in json.dumps(details) and marker not in caplog.text
    assert 'test-private' not in caplog.text
    assert details['validation_error_count'] == 2
    evidence = capture.call_args.args[0]
    assert marker in evidence['raw_response']
    assert evidence['messages'] == client.chat.completions.create.call_args.kwargs['messages']
    assert 'test-private' not in json.dumps(evidence)
    assert client.chat.completions.create.call_count == 1


def test_capture_failure_does_not_replace_validation_failure(caplog):
    gen, client, _ = generator({'title': '缺少主题'})
    gen.diagnostic_sink = Mock(side_effect=RuntimeError('private filesystem detail'))
    with pytest.raises(ResourceError) as caught:
        gen.generate('lexicon', ResourceGenerationRequest(**REQUEST))
    assert caught.value.code == 'RESOURCE_GENERATION_INVALID'
    assert 'diagnostic_capture_failed' in caplog.text
    assert 'private filesystem detail' not in caplog.text
    assert client.chat.completions.create.call_count == 1


def test_malformed_json_retains_diagnostics_without_echoing_input():
    with pytest.raises(ResourceError) as caught:
        _validate_content('lexicon', '{ PRIVATE_INPUT', ResourceGenerationRequest(**REQUEST))
    assert caught.value.details['validation_errors'][0]['type'] == 'json_invalid'
    assert 'PRIVATE_INPUT' not in str(caught.value.details)


def test_diagnostics_do_not_change_rule_output_contract(content):
    gen, _, _ = generator(content)
    result = gen.generate('ruleset', ResourceGenerationRequest(**REQUEST))
    assert result == RuleSetContent.model_validate(content)
