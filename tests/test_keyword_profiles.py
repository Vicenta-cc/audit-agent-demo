"""Optional topic guidance must not change generic, rule, or exact-term flows."""
import json
import logging

import pytest
from pydantic import ValidationError

from backend.resource_management.generation import generation_messages
from backend.resource_management.generation_contracts import ResourceGenerationRequest, LexiconGenerationRequest
from backend.resource_management.keyword_profiles import PROFILES
from backend.resource_management.contracts import ResourceError
from backend.investigation_creation.tools import CreateRuleSetProposalInput, HERMES_M3_TOOL_SCHEMAS, HermesToolExecutionIdentity
from test_resource_generation import generator, lexicon, REQUEST, PRINCIPAL
from test_investigation_creation_conversation import creation_stack, _draft_count, _run_count


@pytest.mark.parametrize('extra', [{}, {'keyword_profile': None}])
def test_absent_profile_preserves_original_provider_payload(extra):
    old = ResourceGenerationRequest(**REQUEST, requirements='保留用户要求')
    new = LexiconGenerationRequest(**old.model_dump(), **extra)
    assert generation_messages('lexicon', new) == generation_messages('lexicon', old)


@pytest.mark.parametrize('name', PROFILES)
def test_selected_profile_only_changes_dedicated_user_requirements(name):
    request = LexiconGenerationRequest(**REQUEST, keyword_profile=name, requirements='只看用户指定方向')
    messages = generation_messages('lexicon', request)
    baseline = generation_messages('lexicon', ResourceGenerationRequest(**REQUEST))
    assert messages[0] == baseline[0]
    payload = json.loads(messages[1]['content'])
    assert payload['objective'] == REQUEST['objective']
    assert payload['platform'] == REQUEST['platform']
    assert 'keyword_profile' not in payload
    assert PROFILES[name].guidance.strip() in payload['requirements']
    assert payload['requirements'].endswith('【用户本次要求】\n只看用户指定方向')
    for other, profile in PROFILES.items():
        if other != name:
            assert profile.guidance.strip() not in payload['requirements']
    assert request.requirements == '只看用户指定方向'  # don't mutate caller/receipt input


def test_unknown_profile_rejected_and_rules_schema_unchanged():
    with pytest.raises(ValidationError):
        LexiconGenerationRequest(**REQUEST, keyword_profile='invented')
    with pytest.raises(ValidationError):
        CreateRuleSetProposalInput(generation_request={**REQUEST, 'keyword_profile': 'sexual_service_leadgen'})
    definitions = {s['name']: s for s in HERMES_M3_TOOL_SCHEMAS}
    assert 'keyword_profile' in json.dumps(definitions['create_lexicon_edit'])
    assert 'keyword_profile' not in json.dumps(definitions['create_ruleset_proposal'])
    assert '无匹配模板' in definitions['create_lexicon_edit']['description']


@pytest.mark.parametrize('name', PROFILES)
def test_exact_terms_suppress_examples_and_still_validate_original_terms(name):
    terms = ['用户原文']
    request = LexiconGenerationRequest(**REQUEST, exact_terms=terms, keyword_profile=name)
    old = ResourceGenerationRequest(**REQUEST, exact_terms=terms)
    assert generation_messages('lexicon', request) == generation_messages('lexicon', old)
    gen, client, _ = generator()
    with pytest.raises(ResourceError) as error:
        gen.generate('lexicon', request)
    assert error.value.code == 'RESOURCE_GENERATION_INVALID'
    assert client.chat.completions.create.call_count == 1


def test_rules_messages_never_receive_keyword_guidance():
    old = ResourceGenerationRequest(**REQUEST)
    new = LexiconGenerationRequest(**REQUEST, keyword_profile='sexual_service_leadgen')
    assert generation_messages('ruleset', new) == generation_messages('ruleset', old)


def test_selected_templates_and_versions_match_product_choice():
    sexual = PROFILES['sexual_service_leadgen']
    assert sexual.version == '4'
    assert '门槛验牌、非绿地陪、hk互看、00后新出道老师' in sexual.guidance
    assert '不为覆盖类目而额外加入资源售卖或偷拍视频方向' in sexual.guidance
    assert '不是生成新暗语的拼接公式' in sexual.guidance
    assert '模板提供的候选表达示例' not in sexual.guidance
    religion = PROFILES['religion_content_risk']
    assert religion.version == '1'
    assert '不推断个人信仰或组织成员身份' in religion.guidance
    assert '没有样本依据的词义或别名关系标为待验证' in religion.guidance
    assert '不得仅凭词语命中认定违规' not in religion.guidance  # no appended _COMMON duplicate
    assert PROFILES['gambling_financial_abuse'].version == '2'


def test_coordinator_tool_schema_exposes_all_profiles_and_religion_scope():
    definitions = {s['name']: s for s in HERMES_M3_TOOL_SCHEMAS}
    schema = json.dumps(definitions['create_lexicon_edit'], ensure_ascii=False)
    for name in PROFILES:
        assert name in schema
        assert name in definitions['create_lexicon_edit']['description']
    assert '借宗教、修行名义实施的欺诈或胁迫招募' in schema
    assert '正常宗教、建筑艺术、民俗和学术资料发现不套此风险模板' in schema
    assert 'religion_content_risk' not in json.dumps(definitions['create_ruleset_proposal'])


@pytest.mark.parametrize('name', [None, *PROFILES])
def test_profile_receipt_replay_save_and_read_do_not_regenerate(creation_stack, name, caplog):
    tools = creation_stack['tool_service']
    gen, client, _ = generator()
    tools.resource_generator = gen
    caplog.set_level(logging.INFO, logger='backend.resource_management.generation')
    identity = HermesToolExecutionIdentity('profile-session', 'profile-turn', 'profile-call')
    args = {'generation_request': {**REQUEST, 'keyword_profile': name}}
    result = tools.execute_with_identity('create_lexicon_edit', args, principal=PRINCIPAL, identity=identity)
    assert result['status'] == 'ok', result
    assert result['data']['generation_profile'] == {
        'requested_profile': name, 'applied_profile': name,
        'profile_version': PROFILES[name].version if name else None, 'skip_reason': None,
    }
    assert f'keyword_profile={name}' in caplog.text
    assert tools.execute_with_identity('create_lexicon_edit', args, principal=PRINCIPAL, identity=identity) == result
    edit = result['data']
    saved = tools.execute('save_resource', {'edit_id': edit['edit_id'], 'expected_version': edit['version'],
                         'operation_id': 'profile-save'}, session_id=identity.session_id, principal=PRINCIPAL)
    read = tools.execute('read_resource', {'kind': 'lexicon', 'resource_id': saved['resource_id']},
                         session_id=identity.session_id, principal=PRINCIPAL)
    assert read['content'] == edit['content']
    assert read['resource_ref'] == saved['resource_ref']
    assert client.chat.completions.create.call_count == 1
    assert _draft_count(creation_stack['creation_store']) == _run_count(creation_stack['creation_store']) == 0


def test_import_does_not_add_profile_metadata_or_generate(creation_stack):
    tools = creation_stack['tool_service']
    gen, client, _ = generator()
    tools.resource_generator = gen
    result = tools.execute('create_lexicon_edit', {'content': lexicon()}, session_id='import', principal=PRINCIPAL)
    assert 'generation_profile' not in result
    client.chat.completions.create.assert_not_called()


def test_invalid_profile_returns_argument_error_without_provider_call(creation_stack):
    tools = creation_stack['tool_service']
    gen, client, _ = generator()
    tools.resource_generator = gen
    result = tools.execute_with_identity('create_lexicon_edit', {
        'generation_request': {**REQUEST, 'keyword_profile': 'unknown'},
    }, principal=PRINCIPAL, identity=HermesToolExecutionIdentity('s', 't', 'c'))
    assert result['error']['code'] == 'INVALID_TOOL_ARGUMENTS'
    assert not result['error']['details']['mutation_applied']
    client.chat.completions.create.assert_not_called()


def test_exact_term_receipt_records_skipped_profile(creation_stack):
    tools = creation_stack['tool_service']
    gen, _, _ = generator()
    tools.resource_generator = gen
    terms = [f'招聘押金{i}' for i in range(6)]
    result = tools.execute('create_lexicon_edit', {'generation_request': {
        **REQUEST, 'keyword_profile': 'gambling_financial_abuse', 'exact_terms': terms,
    }}, session_id='exact', principal=PRINCIPAL)
    assert result['search_terms'] == terms
    assert result['generation_profile'] == {
        'requested_profile': 'gambling_financial_abuse', 'applied_profile': None,
        'profile_version': None, 'skip_reason': 'exact_terms',
    }


def test_profile_failure_does_not_create_edit_or_retry(creation_stack):
    tools = creation_stack['tool_service']
    _ = tools.application_service.resource_management
    gen, client, _ = generator(error=RuntimeError('400 data_inspection_failed Input data'))
    tools.resource_generator = gen
    args = {'generation_request': {**REQUEST, 'keyword_profile': 'gambling_financial_abuse'}}
    identity = HermesToolExecutionIdentity('blocked', 'turn', 'call')
    result = tools.execute_with_identity('create_lexicon_edit', args, principal=PRINCIPAL, identity=identity)
    assert result['error']['code'] == 'INPUT_CONTENT_BLOCKED'
    assert tools.execute_with_identity('create_lexicon_edit', args, principal=PRINCIPAL, identity=identity) == result
    assert client.chat.completions.create.call_count == 1
    with creation_stack['creation_store']._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM lexicon_edits').fetchone()[0] == 0
