"""Small generation discovery contracts retain the full explicit import path."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from backend.resource_management.contracts import CreateLexiconInput, LexiconContent
from backend.resource_management.tool_facades import (
    GenerateLexiconInput, GenerateRuleSetInput, ImportResourceInput,
    facade_schemas, normalize_import,
)
from backend.investigation_creation.tools import (
    CreateRuleSetProposalInput, _ALL_CREATION_TOOL_SCHEMAS, M3_TOOL_INPUTS,
    M3_TOOL_DESCRIPTIONS, HermesToolExecutionIdentity,
)
from backend.investigation_creation.principal import Principal
from test_investigation_creation_conversation import creation_stack
from backend.rulesets.contracts import RuleSetContent
from test_resource_lifecycle import lexicon
from test_ruleset_proposal_presentation import content


@pytest.mark.parametrize('schema', [GenerateRuleSetInput, GenerateLexiconInput])
def test_generation_contract_requires_only_generation_request(schema):
    request = {'objective': '调查招聘收费', 'platform': 'dy'}
    assert schema.model_validate({'generation_request': request}).generation_request.objective == request['objective']
    for args in ({}, {'content': {}}, {'generation_request': request, 'content': {}},
                 {'generation_request': request, 'platform': 'dy'}):
        with pytest.raises(ValidationError):
            schema.model_validate(args)
    exposed = schema.model_json_schema()
    assert exposed['required'] == ['generation_request']
    assert set(exposed['properties']) == {'generation_request'}
    assert exposed['additionalProperties'] is False


def test_facades_shrink_common_discovery_and_preserve_other_definitions():
    baseline = [{'name': item['name'],
                 'description': M3_TOOL_DESCRIPTIONS[item['name']],
                 'parameters': M3_TOOL_INPUTS[item['name']].model_json_schema()}
                for item in _ALL_CREATION_TOOL_SCHEMAS if item['name'] != 'import_resource']
    before = deepcopy(baseline)
    exposed = facade_schemas(baseline)
    by_name = {item['name']: item for item in exposed}
    assert len(exposed) == len(baseline) + 1
    for item in baseline:
        candidate = by_name[item['name']]
        if item['name'] in {'create_ruleset_proposal', 'create_lexicon_edit'}:
            assert len(json.dumps(candidate, ensure_ascii=False)) < len(json.dumps(item, ensure_ascii=False))
            assert 'content' not in candidate['parameters']['properties']
        else:
            assert candidate == item
    assert by_name['import_resource']['parameters']['required'] == ['kind', 'content']
    exposed[0]['description'] = 'mutated downstream'
    assert baseline == before
    assert len(facade_schemas(facade_schemas(baseline))) == len(baseline) + 1


@pytest.mark.parametrize('kind', ['ruleset', 'lexicon'])
def test_explicit_import_preserves_complete_typed_content(content, kind):
    body = content if kind == 'ruleset' else lexicon()
    schema = RuleSetContent if kind == 'ruleset' else LexiconContent
    legacy = CreateRuleSetProposalInput if kind == 'ruleset' else CreateLexiconInput
    expected = schema.model_validate(body).model_dump(mode='json')
    raw = {'kind': kind, 'content': deepcopy(body)}
    before = deepcopy(raw)
    name, args = normalize_import('import_resource', raw)
    assert name == ('create_ruleset_proposal' if kind == 'ruleset' else 'create_lexicon_edit')
    assert args == {'content': expected}
    assert legacy.model_validate(args).content.model_dump(mode='json') == expected
    assert raw == before


@pytest.mark.parametrize('kind', ['ruleset', 'lexicon'])
def test_import_rejects_mismatched_kind_and_generation(content, kind):
    body = lexicon() if kind == 'ruleset' else content
    for args in ({'kind': kind, 'content': body},
                 {'kind': kind, 'content': body, 'generation_request': {'objective': 'x', 'platform': 'dy'}},
                 {'kind': kind, 'generation_request': {'objective': 'x', 'platform': 'dy'}}):
        with pytest.raises(ValidationError):
            ImportResourceInput.model_validate(args)


def test_non_import_call_is_untouched():
    args = {'generation_request': {'objective': 'x', 'platform': 'dy'}}
    name, unchanged = normalize_import('create_lexicon_edit', args)
    assert name == 'create_lexicon_edit'
    assert unchanged is args


@pytest.mark.parametrize('kind', ['ruleset', 'lexicon'])
def test_discovered_import_reuses_existing_persistence_and_receipts(creation_stack, content, kind):
    tools = creation_stack['tool_service']
    from unittest.mock import Mock
    tools.resource_generator = Mock()
    principal = Principal('principal-a', role='admin')
    identity = HermesToolExecutionIdentity('import-session', 'import-turn', 'import-call')
    args = {'kind': kind, 'content': content if kind == 'ruleset' else lexicon()}
    first = tools.execute_with_identity('import_resource', args, principal=principal, identity=identity)
    assert first['status'] == 'ok', first
    assert tools.execute_with_identity('import_resource', args, principal=principal, identity=identity) == first
    canonical, canonical_args = normalize_import('import_resource', args)
    assert tools.execute_with_identity(canonical, canonical_args, principal=principal, identity=identity) == first
    tools.resource_generator.generate.assert_not_called()
    definitions = {item['function']['name']: item['function'] for item in tools.definitions()}
    assert 'import_resource' in tools.allowed_tool_names
    assert 'content' not in definitions[canonical]['parameters']['properties']
