"""Joint projection tests start from real P1 authority and persisted P2 plans."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_geographic_execution_plan import make_authority
from backend.investigation_creation.scope_search_plan import build_scope_search_plan
from backend.audit_agent.scoped_selection import digest
from experiments.geographic_execution_plan.storage import save_plan, load_plan
from experiments.geographic_joint_integration.adapter import (
    INPUT_MANIFEST, BoundAdapter, project_plan, restore_bundle, verify_input_manifest,
)


@pytest.fixture
def case_evidence(request):
    observed = {'case_id': request.node.nodeid, 'fixture_only': True}
    yield observed
    if output := os.environ.get('GEO_JOINT_OUTPUT'):
        path = Path(output) / 'adapter-contract-cases.json'
        body = json.loads(path.read_text()) if path.exists() else {
            'schema': 'p234-adapter-contract-evidence-v1', 'cases': {}}
        body['cases'][request.node.nodeid] = observed
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + '\n')


@pytest.fixture
def joint_input(creation_stack, content, tmp_path, monkeypatch, case_evidence):
    from backend.audit_agent.config import settings
    monkeypatch.setattr(settings, 'triage_mode', 'select')
    authority = make_authority(creation_stack, content)
    identity = deepcopy(authority['identity'])
    plan = build_scope_search_plan(authority, expected_identity=identity)
    path = tmp_path / 'joint-p2-plan.sqlite3'
    saved = save_plan(path, plan)
    restored = load_plan(path, expected_identity=identity, expected_digest=saved)
    arguments = dict(expected_identity=identity, expected_p2_digest=saved,
        execution_id='fixture:joint-adapter',
        source_manifest_digest=hashlib.sha256(INPUT_MANIFEST.read_bytes()).hexdigest())
    bundle = project_plan(restored, plan_path=path, authority=authority, **arguments)
    expected = {**arguments, 'expected_binding_digest': bundle['binding']['binding_digest']}
    case_evidence.update(input_bundle=bundle, external_expected=expected, authority_digest=digest(authority))
    return authority, restored, path, bundle, expected


def test_real_saved_plan_projection_preserves_frozen_fields(joint_input, case_evidence):
    authority, p2, path, bundle, expected = joint_input
    adapter = BoundAdapter(json.loads(json.dumps(bundle)), **expected)
    p4 = adapter.plan
    assert p4['binding'] == {'owner': p2.identity.owner, 'session': p2.identity.session_id,
        'draft': p2.identity.draft_id, 'revision': p2.identity.revision}
    assert [q['query'] for q in p4['queries']] == [q.query for q in p2.execution_queries]
    assert [q['max_notes'] for q in p4['queries']] == [q.per_keyword_limit for q in p2.execution_queries]
    assert p4['task_limit'] == p2.collection.max_total_notes
    for q in p4['queries']:
        for name in ('candidate_count', 'candidate_comment_limit', 'detail_comment_limit', 'collect_comments'):
            assert q['capture_parameters'][name] == getattr(p2.collection, name)
        for name, value in p2.collection.effective_parameters.model_dump(mode='json').items():
            assert q['capture_parameters'][name] == value
    assert bundle['binding']['p2_plan_digest'] == p2.plan_digest
    assert bundle['binding']['p4_projection_digest'] == p4['plan_digest'] != p2.plan_digest
    assert bundle['binding']['p2_risk_digest'] == p2.inputs.risk_inputs['digest']
    assert p4['fixture_risk_config']['rules'] == authority['resolution']['execution']['prompt_profile_snapshot']
    assert restore_bundle(bundle, **expected) == bundle
    # Callers cannot mutate the validated instance through exposed dictionaries.
    p4['queries'][0]['query'] = 'changed'
    assert adapter.plan == bundle['projection']
    case_evidence.update(result='accepted_and_restored', actual_projection=adapter.plan, defensive_copy_preserved=True)


@pytest.mark.parametrize('mutation', ['query', 'risk_rules', 'risk_model', 'capture_limit', 'execution'])
def test_self_rehashed_projection_cannot_replace_external_binding(joint_input, mutation, case_evidence):
    _, _, _, bundle, expected = joint_input
    changed = deepcopy(bundle)
    p4 = changed['projection']
    if mutation == 'query': p4['queries'][0]['query'] += ' changed'
    elif mutation == 'risk_rules': p4['fixture_risk_config']['rules']['audit_goal'] = 'different'
    elif mutation == 'risk_model': p4['fixture_risk_config']['parameters']['model'] = 'different'
    elif mutation == 'capture_limit': p4['queries'][0]['capture_parameters']['candidate_count'] += 1
    else: p4['execution_id'] = 'other-execution'
    p4['versions']['risk'] = digest(p4['fixture_risk_config'])
    p4['plan_digest'] = digest({k: v for k, v in p4.items() if k != 'plan_digest'})
    changed['binding']['p4_projection_digest'] = p4['plan_digest']
    changed['binding']['binding_digest'] = digest({k: v for k, v in changed['binding'].items() if k != 'binding_digest'})
    with pytest.raises(ValueError, match='joint_external_binding_mismatch') as rejected:
        BoundAdapter(changed, **expected)
    case_evidence.update(mutation=mutation, modified_bundle=changed, actual_error=str(rejected.value))


@pytest.mark.parametrize('field', ['expected_p2_digest', 'expected_binding_digest', 'source_manifest_digest', 'execution_id'])
def test_independent_expected_values_required(joint_input, field, case_evidence):
    _, _, _, bundle, expected = joint_input
    wrong = {**expected, field: '0' * 64 if field.endswith('digest') else 'foreign-execution'}
    with pytest.raises(ValueError) as rejected:
        restore_bundle(bundle, **wrong)
    case_evidence.update(mutation=field, supplied_external_expected=wrong, actual_error=str(rejected.value))


def test_first_projection_requires_saved_plan_and_independent_authority(joint_input, tmp_path, case_evidence):
    authority, plan, path, _, expected = joint_input
    args = {k: v for k, v in expected.items() if k != 'expected_binding_digest'}
    with pytest.raises(ValueError, match='missing fixture database') as missing:
        project_plan(plan, plan_path=tmp_path / 'not-saved.sqlite3', authority=authority, **args)
    wrong = deepcopy(authority)
    wrong['title'] += ' another captured authority'
    with pytest.raises(ValueError, match='authoritative inputs') as changed:
        project_plan(plan, plan_path=path, authority=wrong, **args)
    case_evidence.update(mutations=['missing_saved_database', 'changed_authority_title'],
        changed_authority_digest=digest(wrong), actual_errors=[str(missing.value), str(changed.value)])


def test_imported_source_bytes_are_checked_without_editing_upstream(tmp_path, case_evidence):
    manifest = json.loads(INPUT_MANIFEST.read_bytes())
    manifest['files'][0]['sha256'] = '0' * 64
    path = tmp_path / 'bad-manifest.json'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='imported_source_drift') as rejected:
        verify_input_manifest(path, hashlib.sha256(path.read_bytes()).hexdigest())
    case_evidence.update(mutation='imported_file_sha256', modified_entry=manifest['files'][0],
        supplied_manifest_digest=hashlib.sha256(path.read_bytes()).hexdigest(), actual_error=str(rejected.value))


def test_risk_and_geography_use_actual_frozen_plan(joint_input, case_evidence):
    _, p2, _, bundle, expected = joint_input
    adapter = BoundAdapter(bundle, **expected)
    query = adapter.plan['queries'][0]
    item = {'post_id': 'joint-post', 'desc': '新疆克拉玛依市欠薪', 'source_keyword': query['query'],
            'custom_verify': '', 'enterprise_verify_reason': '', 'follower_count': 3}
    occurrence = adapter.occurrence(query['query_id'], 'dy:joint-post', 7, item,
        [{'comment_id': 'c1', 'content': '询问详情', 'text': '另一规范化别名'}])
    risk = adapter.risk(occurrence, {'suspicion': 'weak', 'reason': '人工离线回答', 'matched_terms': []})
    assert risk['input']['item'] == item
    assert risk['input']['comments'][0]['content'] == '询问详情'
    assert risk['input']['config']['rules'] == p2.inputs.risk_inputs['rules']
    assert risk['calls'] and risk['calls'][0]['kwargs']['model'] == p2.inputs.risk_inputs['model']['name']
    geo = adapter.geo(occurrence['candidate'], {'status': 'located',
        'locations': [{'field': 'desc', 'place': '克拉玛依市'}]})
    adapter.validate_geo(adapter.plan, occurrence['candidate'], geo)
    assert geo['result']['geo_tier'] == 'local'
    assert geo['request']['repairs'] == {'place_aliases': True, 'role_prompt': True}
    case_evidence.update(result='validated', occurrence=occurrence, risk_record=risk, geo_record=geo)


@pytest.mark.parametrize('mode', ['off', 'compare'])
def test_nonselect_frozen_plan_is_explicitly_rejected(creation_stack, content, tmp_path, monkeypatch, mode, case_evidence):
    from backend.audit_agent.config import settings
    monkeypatch.setattr(settings, 'triage_mode', mode)
    authority = make_authority(creation_stack, content)
    identity = deepcopy(authority['identity'])
    plan = build_scope_search_plan(authority, expected_identity=identity)
    path = tmp_path / 'unsupported-mode.sqlite3'
    expected_digest = save_plan(path, plan)
    with pytest.raises(ValueError, match='unsupported_triage_mode') as rejected:
        project_plan(load_plan(path, expected_identity=identity, expected_digest=expected_digest),
            plan_path=path, authority=authority, expected_identity=identity,
            expected_p2_digest=expected_digest, execution_id='fixture:unsupported-mode',
            source_manifest_digest=hashlib.sha256(INPUT_MANIFEST.read_bytes()).hexdigest())
    case_evidence.update(mutation='frozen_triage_mode', mode=mode, actual_error=str(rejected.value),
        p2_plan_digest=expected_digest, identity=identity, collection=plan.collection.model_dump(mode='json'),
        source_manifest_digest=hashlib.sha256(INPUT_MANIFEST.read_bytes()).hexdigest())


def test_no_scope_does_not_create_geographic_projection(joint_input, case_evidence):
    authority, _, path, _, expected = joint_input
    authority = deepcopy(authority)
    authority['configuration'].pop('relevance_scope')
    authority['configuration'].pop('relevance_scope_state')
    assert build_scope_search_plan(authority, expected_identity=expected['expected_identity']) is None
    arguments = {k: v for k, v in expected.items() if k != 'expected_binding_digest'}
    actual = project_plan(None, plan_path=path, authority=authority, **arguments)
    assert actual is None
    case_evidence.update(mutation='absent_scope', actual_projection=actual,
        tested_configuration=authority['configuration'], authority_digest=digest(authority))
