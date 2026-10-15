"""Authority settings, strict profiles and persisted P1→P2→P3 replay."""
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_geographic_execution_plan import make_authority, build, expected
from test_unified_draft_configuration import P


def capture(stack, identity):
    from experiments.geographic_execution_plan.authority import capture_fixture_authority
    return capture_fixture_authority(stack['app_service'], identity['draft_id'], principal=P,
        session_id=identity['session_id'], fixture_id='fixture:p2-integration')


@pytest.mark.parametrize('comments,limit', [(False, 75), (True, 0), (True, 75)])
def test_settings_authority_revision_and_comment_derivation(creation_stack, content, comments, limit):
    from backend.investigation_creation.scope_search_plan import validate_scope_search_plan
    authority = make_authority(creation_stack, content)
    old = build(authority)
    assert old.inputs.settings.revision == 0
    task_settings = creation_stack['app_service'].resource_service.task_settings
    saved = task_settings.get()
    parameters = {**saved['parameters'], 'collect_comments': comments, 'max_comments': limit,
        'max_total_notes': 7}
    task_settings.save(parameters, expected_revision=0)
    changed = capture(creation_stack, authority['identity'])
    fresh = build(changed)
    assert fresh.inputs.settings.revision == 1
    assert fresh.inputs.settings.saved.max_total_notes == 7
    effective = fresh.collection.effective_parameters
    assert effective.max_notes == 1
    assert effective.analysis_batch_size == 5
    assert fresh.collection.detail_comment_limit == (min(limit, fresh.inputs.settings.limits.m3_comments_per_post) if comments else 0)
    assert fresh.collection.candidate_comment_limit == (min(fresh.inputs.settings.limits.triage_candidate_comments, effective.max_comments) if comments else 0)
    assert effective.get_sub_comment == (parameters['get_sub_comment'] and comments and limit > 0 and fresh.inputs.settings.limits.m3_comments_per_post > 0)
    with pytest.raises(ValueError):
        validate_scope_search_plan(old, changed, expected_identity=expected(changed))
    # The draft's optional local parameters are not the application's authority.
    changed['configuration']['task_parameters'] = {**parameters, 'max_total_notes': 6}
    revision = changed['identity']['revision']
    for row in changed['provenance']['history']:
        if row['draft_id'] == changed['identity']['draft_id'] and row['revision'] == revision:
            row['configuration']['task_parameters'] = deepcopy(changed['configuration']['task_parameters'])
    ignored = build(changed)
    assert ignored.collection.effective_parameters == fresh.collection.effective_parameters


@pytest.mark.parametrize('limit', [10, 100, 101, 200])
def test_search_cap_preserves_p1_supported_configuration(creation_stack, content, monkeypatch, limit):
    from backend.audit_agent.config import settings
    from backend.investigation_creation.scope_search_plan import serialize_scope_search_plan, parse_scope_search_plan
    monkeypatch.setattr(settings, 'search_terms_max', limit)
    authority = make_authority(creation_stack, content)
    plan = build(authority)
    assert plan.inputs.settings.limits.search_terms_max == limit
    assert len(plan.execution_queries) == 1
    assert [q.query for q in plan.execution_queries] == authority['resolution']['resolved_search_terms']
    restored = parse_scope_search_plan(serialize_scope_search_plan(plan),
        expected_identity=expected(authority), expected_digest=plan.plan_digest)
    assert restored == plan


def test_structured_rule_snapshot_cannot_replace_triage_prompt_profile(creation_stack, content):
    from backend.investigation_creation.scope_search_plan import digest
    authority = make_authority(creation_stack, content)
    assert authority['resolution']['execution']['prompt_profile_snapshot']['audit_goal']
    wrong = deepcopy(authority)
    risk = wrong['risk_inputs']
    risk['rules'] = deepcopy(wrong['resolution']['execution']['rule_snapshot'])
    risk['digest'] = digest({k: v for k, v in risk.items() if k != 'digest'})
    with pytest.raises(ValueError, match='risk rules differ'):
        build(wrong)


@pytest.mark.parametrize('field,value', [
    ('use_place_aliases', 1), ('use_role_prompt', 'true'),
    ('use_role_prompt', None), ('use_role_prompt', False),
    ('prompt_snapshot', 'changed prompt'), ('model', {}),
])
def test_rehashed_invalid_p3_profile_is_rejected(creation_stack, content, field, value):
    from backend.investigation_creation.scope_search_plan import digest
    a = make_authority(creation_stack, content)
    p3 = a['p3_inputs']
    p3[field] = value
    p3['profile_digest'] = digest({k:v for k,v in p3.items() if k != 'profile_digest'})
    with pytest.raises(ValueError): build(a)


def test_all_four_legitimate_switch_profiles_roundtrip(creation_stack, content):
    from experiments.geographic_execution_plan.p3_bridge import freeze_p3_inputs
    from backend.investigation_creation.scope_search_plan import serialize_scope_search_plan, parse_scope_search_plan
    a = make_authority(creation_stack, content)
    for aliases in (False, True):
        for role in (False, True):
            a['p3_inputs'] = freeze_p3_inputs(model='fixture-model',
                model_config_source={'origin':'fixture', 'reference':'explicit four switches'},
                use_place_aliases=aliases, use_role_prompt=role)
            from experiments.geographic_execution_plan.p3_bridge import derive_scope_context
            a['scope_context'] = derive_scope_context(a['configuration']['relevance_scope_state'], a['p3_inputs'])
            plan = build(a)
            restored = parse_scope_search_plan(serialize_scope_search_plan(plan),
                expected_identity=expected(a), expected_digest=plan.plan_digest)
            assert restored.inputs.p3_inputs['use_place_aliases'] is aliases
            assert restored.inputs.p3_inputs['use_role_prompt'] is role


@pytest.mark.parametrize('mutation', ['model','topic','policy','aliases','ip','origin'])
def test_p3_context_cannot_drift_from_frozen_inputs(creation_stack, content, mutation):
    a = make_authority(creation_stack, content)
    c = a['scope_context']
    if mutation == 'model': c['model'] = 'different'
    elif mutation == 'topic': c['scope']['topic'] = 'search-query-injected'
    elif mutation == 'policy': c['scope']['selection_policy'] = 'local_only'
    elif mutation == 'aliases': c['scope']['target']['aliases'] = ['made-up-alias']
    elif mutation == 'ip': c['policy']['ip']['min_valid_users'] = 2
    else: c['scope_origin'] = 'frozen'
    with pytest.raises(ValueError): build(a)


@pytest.mark.parametrize('platform,mode', [('xhs','search'), ('ks','search'), ('dy','creator')])
def test_explicit_offline_platform_and_mode_boundary(creation_stack, content, platform, mode):
    a = make_authority(creation_stack, content)
    a['configuration']['platform'] = platform
    if mode == 'creator':
        a['configuration']['investigation'] = {'mode':'creator', 'creator_url':'https://www.douyin.com/user/fixture'}
    with pytest.raises(ValueError): build(a)


def test_persisted_plan_to_pinned_p3_request_and_manual_result(creation_stack, content, tmp_path):
    from backend.investigation_creation.scope_search_plan import serialize_scope_search_plan, digest
    from experiments.geographic_execution_plan.storage import save_plan, load_plan
    from experiments.geographic_execution_plan.p3_bridge import prepare, consume, consume_policy
    from test_geographic_draft_configuration import no_effects
    a = make_authority(creation_stack, content, ui=True)
    plan = build(a)
    output = Path(os.environ['GEO_OFFLINE_OUTPUT']) if os.environ.get('GEO_OFFLINE_OUTPUT') else tmp_path
    path = output / 'example-fixture-plan.sqlite3'
    saved = save_plan(path, plan)
    restored = load_plan(path, expected_identity=expected(a), expected_digest=saved)
    candidate = {'platform':'dy', 'content_key':'dy:p2-fixture',
        'post':{'id':'p2-fixture', 'title':'吉林省招聘收费咨询', 'desc':'人工编写的离线样本'},
        'comments':[], 'source_queries':[q.query for q in restored.execution_queries],
        'raw_ref':'fixture:p2-persisted-replay'}
    state = restored.inputs.configuration.relevance_scope_state.model_dump(mode='json')
    request = prepare(state, restored.inputs.p3_inputs, candidate)
    manual = {'status':'located', 'locations':[{'field':'title', 'place':'吉林省'}]}
    decision = consume(request, manual)
    policy = consume_policy(decision, target_level=restored.inputs.scope_context['target_level'],
        selection_policy=restored.policy.selection_policy, risk={'valid':True, 'strength':'strong'})
    assert decision['geo_tier'] == 'local' and policy['eligible'] is True
    assert request == prepare(state, restored.inputs.p3_inputs, candidate)
    assert plan == restored
    no_effects(creation_stack)
    if os.environ.get('GEO_OFFLINE_OUTPUT'):
        (output/'example-fixture-plan.json').write_text(serialize_scope_search_plan(restored), encoding='utf-8')
        evidence = dict(validation_origin='fixture', semantic_quality_evaluation=False,
            expected_identity=expected(a), expected_digest=saved,
            plan_input_digests=restored.input_digests, candidate=candidate, request=request,
            manual_response=manual, decision=decision, test_consumer=policy,
            static_restore_equal=True, repeat_request_equal=True,
            scope_state=state, frozen_p3_digest=digest(restored.inputs.p3_inputs))
        (output/'p3-manual-replay.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        import subprocess
        import sys
        result = subprocess.run([sys.executable, '-m', 'experiments.geographic_execution_plan.replay',
            '--evidence-dir', str(output)], cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, check=True)
        checks = json.loads(result.stdout)
        assert checks['request_equal'] and checks['decision_equal'] and checks['consumer_equal']
        (output/'replay-cli.json').write_text(result.stdout, encoding='utf-8')
