"""New-rule authoring removes mechanical fields, not business requirements."""
import json

import pytest

from backend.resource_management.contracts import ResourceError
from backend.resource_management.generation import _validate_content, generation_messages
from backend.resource_management.generation_contracts import ResourceGenerationRequest
from backend.rulesets.contracts import RuleSetContent
from test_resource_generation import REQUEST, generator
from test_ruleset_proposal_presentation import content


def semantic_content(content):
    body = (RuleSetContent.model_validate(content).model_dump(mode="json")
            if content.get("categories") and "category_id" in content["categories"][0]
            else json.loads(json.dumps(content)))
    body.pop('schema_version', None)
    for exemption in body['general_exemptions']:
        exemption.pop('exemption_id')
    for category in body['categories']:
        category.pop('category_id')
        category.pop('order')
        for rule in category['rules']:
            rule.pop('rule_id')
            rule.pop('order')
            for exemption in rule.get('rule_exemptions', []):
                exemption.pop('exemption_id')
    return body


def parse(body):
    return _validate_content('ruleset', json.dumps(body), ResourceGenerationRequest(**REQUEST))


def test_rules_generation_schema_excludes_ids_and_order():
    system = generation_messages('ruleset', ResourceGenerationRequest(**REQUEST))[0]['content']
    schema = json.loads(system.split('JSON Schema:\n', 1)[1])
    for definition in [schema, *schema['$defs'].values()]:
        assert not {'schema_version', 'exemption_id', 'rule_id', 'category_id', 'order'} & definition['properties'].keys()


def test_semantic_rules_assign_deterministic_ids_and_preserve_business_fields(content):
    body = semantic_content(content)
    body['general_exemptions'][0]['enabled'] = False
    body['categories'][0]['rules'][0]['enabled'] = False
    body['categories'][0]['rules'][0]['source_mappings'] = [
        {'source_file': 'rules.txt', 'source_locator': 'line 1', 'migrated_semantics': '原文条件'}]
    one, two = parse(body), parse(body)
    assert one == two
    assert semantic_content(one.model_dump(mode='json')) == body
    assert one.categories[0].order == 0
    assert one.categories[0].rules[0].order == 0
    assert len({r.rule_id for c in one.categories for r in c.rules}) == sum(len(c.rules) for c in one.categories)
    gen, client, _ = generator(body)
    assert gen.generate('ruleset', ResourceGenerationRequest(**REQUEST)) == one
    assert client.chat.completions.create.call_count == 1


@pytest.mark.parametrize('field', ['name', 'hit_condition', 'application_stages', 'adjudication_notes', 'suggested_risk_level'])
def test_missing_rule_business_fields_are_not_filled_in(content, field):
    body = semantic_content(content)
    del body['categories'][0]['rules'][0][field]
    with pytest.raises(ResourceError) as caught:
        parse(body)
    assert caught.value.details['generation_stage'] == 'validation'
    assert caught.value.details['retryable']
    assert field in caught.value.details['validation_errors'][0]['path']


def test_missing_exemption_semantics_and_empty_categories_still_rejected(content):
    body = semantic_content(content)
    del body['general_exemptions'][0]['condition']
    body['categories'] = []
    with pytest.raises(ResourceError) as caught:
        parse(body)
    paths = [e['path'] for e in caught.value.details['validation_errors']]
    assert ['general_exemptions', 0, 'condition'] in paths
    assert ['categories'] in paths


def test_rule_stage_uniqueness_and_exemption_semantics_remain_enforced(content):
    body = semantic_content(content)
    body['categories'][0]['rules'][0]['application_stages'] = ['fusion_audit', 'fusion_audit']
    with pytest.raises(ResourceError) as caught:
        parse(body)
    assert caught.value.details['validation_stage'] == 'storage_contract'
    assert 'application_stages' in caught.value.details['validation_errors'][0]['path']
    body = semantic_content(content)
    body['general_exemptions'][0]['condition'] = '  '
    with pytest.raises(ResourceError):
        parse(body)


def test_new_rule_authoring_has_no_model_supplied_identity(content):
    body = semantic_content(content)
    body['categories'][0]['rules'][0]['rule_id'] = 'attempted-model-id'
    with pytest.raises(ResourceError) as caught:
        parse(body)
    assert caught.value.details['validation_errors'][0]['type'] == 'extra_forbidden'


def test_legacy_author_output_is_validated_then_gets_backend_owned_ids_and_order(content):
    body = json.loads(json.dumps(content))
    body['categories'][0]['category_id'] = 'provider-chosen-category'
    body['categories'][0]['order'] = 700
    body['categories'][0]['rules'][0]['rule_id'] = 'provider-chosen-rule'
    body['categories'][0]['rules'][0]['order'] = 900
    body['general_exemptions'][0]['exemption_id'] = 'provider-chosen-exemption'
    one, two = parse(body), parse(semantic_content(body))
    assert one == two
    assert one.categories[0].category_id == 'generated-category-1'
    assert one.categories[0].rules[0].rule_id == 'generated-rule-1-1'
    assert one.categories[0].order == one.categories[0].rules[0].order == 0
    assert one.general_exemptions[0].exemption_id == 'generated-general-exemption-1'
    # Import contract keeps original IDs and order; this conversion is generation only.
    imported = RuleSetContent.model_validate(body)
    assert imported.categories[0].category_id == 'provider-chosen-category'
    assert imported.categories[0].rules[0].order == 900


@pytest.mark.parametrize('defect', ['missing_id', 'duplicate_id', 'invalid_id'])
def test_legacy_compatibility_never_repairs_invalid_or_partial_storage_output(content, defect):
    body = json.loads(json.dumps(content))
    if defect == 'missing_id':
        del body['categories'][0]['rules'][0]['rule_id']
    elif defect == 'duplicate_id':
        body['categories'][0]['rules'].append(json.loads(json.dumps(body['categories'][0]['rules'][0])))
    else:
        body['categories'][0]['rules'][0]['rule_id'] = 'INVALID ID'
    with pytest.raises(ResourceError):
        parse(body)
