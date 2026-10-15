"""P2 pure-plan contracts against real, isolated P1 stores (no live models)."""
from copy import deepcopy
import json

import pytest

from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_geographic_draft_configuration import flow, setup_geo, stored, no_effects
from test_geographic_scope_selection import select
from test_unified_draft_configuration import P


def make_authority(stack, content, *, formal_rules=False, formal_lexicon=False, ui=False):
    from experiments.geographic_execution_plan.authority import capture_fixture_authority
    first, _, _, _, _, _, result, _ = setup_geo(stack, content,
        formal_rules=formal_rules, formal_lexicon=formal_lexicon)
    draft_id = result['data']['draft']['id']
    # The inherited helper uses xhs; the offline profile explicitly supports dy.
    flow(stack, first['session_id'], {'operation': 'update', 'draft_id': draft_id,
        'expected_revision': 1, 'platform': 'dy'}, '使用抖音')
    if ui:
        select(stack, first['session_id'], draft_id, revision=2)
    return capture_fixture_authority(stack['app_service'], draft_id,
        principal=P, session_id=first['session_id'], fixture_id='fixture:p2-real-stores')


def expected(authority):
    return deepcopy(authority['identity'])


def build(authority):
    from backend.investigation_creation.scope_search_plan import build_scope_search_plan
    return build_scope_search_plan(authority, expected_identity=expected(authority))


@pytest.mark.parametrize('formal_rules', [False, True])
@pytest.mark.parametrize('formal_lexicon', [False, True])
@pytest.mark.parametrize('ui', [False, True])
def test_real_four_resources_and_both_sources(creation_stack, content, formal_rules, formal_lexicon, ui):
    authority = make_authority(creation_stack, content,
        formal_rules=formal_rules, formal_lexicon=formal_lexicon, ui=ui)
    plan = build(authority)
    assert plan.validation_origin == 'fixture'
    assert plan.inputs.configuration.relevance_scope_state.source_kind == ('ui_selection' if ui else 'conversation')
    assert [q.query for q in plan.execution_queries] == plan.inputs.resolution.resolved_search_terms
    assert all(q.source_term == q.query for q in plan.execution_queries)
    assert plan.inputs.resolution.execution.keyword == authority['resolution']['execution']['keyword']
    if ui:
        assert not plan.inputs.configuration.relevance_scope_state.source_quote
        assert not plan.inputs.configuration.relevance_scope_state.source_turn_id
    no_effects(creation_stack)


def test_source_revision_may_precede_title_and_resource_changes(creation_stack, content):
    authority = make_authority(creation_stack, content, ui=True)
    identity = authority['identity']
    flow(creation_stack, identity['session_id'], {'operation':'update','draft_id':identity['draft_id'],
        'expected_revision': identity['revision'], 'title':'仅改标题'}, '仅改标题')
    from experiments.geographic_execution_plan.authority import capture_fixture_authority
    changed = capture_fixture_authority(creation_stack['app_service'], identity['draft_id'], principal=P,
        session_id=identity['session_id'], fixture_id='fixture:p2-real-stores')
    plan=build(changed)
    assert plan.inputs.provenance.origin_revision < plan.identity.revision


@pytest.mark.parametrize('mutation', ['owner','session','revision','operation','source_code','source_revision','catalog','public_state','missing_scope','pending','wrong_parent'])
def test_invalid_bindings_fail(creation_stack, content, mutation):
    authority = make_authority(creation_stack, content, ui=True)
    bad=deepcopy(authority)
    if mutation == 'owner': bad['identity']['owner']='another-owner'
    elif mutation == 'session': bad['identity']['session_id']='another-session'
    elif mutation == 'revision': bad['identity']['revision']-=1
    elif mutation == 'operation': bad['provenance']['selection']['operation_id']='fabricated'
    elif mutation == 'source_code': bad['provenance']['selection']['region_code']='650200'
    elif mutation == 'source_revision': bad['provenance']['origin_revision']+=1
    elif mutation == 'catalog': bad['configuration']['relevance_scope_state']['catalog_version']='other'
    elif mutation == 'public_state':
        bad['configuration']['relevance_scope_state']={k:v for k,v in bad['configuration']['relevance_scope_state'].items() if k in {'status','target','province','target_text','selection_policy','reason','candidates'}}
    elif mutation == 'missing_scope': bad['configuration'].pop('relevance_scope')
    elif mutation == 'pending': bad['configuration']['relevance_scope_state']['status']='pending'
    elif mutation == 'wrong_parent': bad['configuration']['relevance_scope_state']['province_code']='650000'
    with pytest.raises(ValueError): build(bad)


def test_external_authority_defeats_rehashed_query_source_order_and_limits(creation_stack, content):
    from backend.investigation_creation.scope_search_plan import validate_scope_search_plan, digest
    authority=make_authority(creation_stack,content)
    plan=build(authority)
    for mutate in ('query','source','order','limit'):
        bad=plan.model_dump(mode='json')
        if mutate=='query': bad['execution_queries'][0]['query']='克拉玛依市 '+bad['execution_queries'][0]['query']
        if mutate=='source': bad['execution_queries'][0]['sources'][0]['entry_id']='wrong-entry'
        if mutate=='order': bad['execution_queries'][0]['order']=90
        if mutate=='limit': bad['collection']['candidate_count']+=1
        bad['plan_digest']=digest({k:v for k,v in bad.items() if k!='plan_digest'})
        with pytest.raises(ValueError): validate_scope_search_plan(bad,authority,expected_identity=expected(authority))


def test_json_roundtrip_requires_independent_identity_and_digest(creation_stack,content):
    from backend.investigation_creation.scope_search_plan import serialize_scope_search_plan,parse_scope_search_plan
    authority=make_authority(creation_stack,content)
    plan=build(authority)
    raw=serialize_scope_search_plan(plan)
    assert parse_scope_search_plan(raw,expected_identity=expected(authority),expected_digest=plan.plan_digest)==plan
    for bad_id in ({**expected(authority),'owner':'other'}, {**expected(authority),'revision':9}):
        with pytest.raises(ValueError): parse_scope_search_plan(raw,expected_identity=bad_id,expected_digest=plan.plan_digest)
    with pytest.raises(ValueError): parse_scope_search_plan(raw,expected_identity=expected(authority),expected_digest='0'*64)
    bad=json.loads(raw); bad['schema_version']='future'
    with pytest.raises(ValueError): parse_scope_search_plan(json.dumps(bad),expected_identity=expected(authority),expected_digest=plan.plan_digest)


@pytest.mark.parametrize('bad', [True, '10', 1.1, float('nan'), float('inf'), None])
def test_strict_integer_profile_rejects(creation_stack,content,bad):
    authority=make_authority(creation_stack,content)
    authority['settings']['limits']['triage_candidates_per_keyword']=bad
    with pytest.raises(ValueError): build(authority)


def test_history_and_cleared_do_not_create_scope_plan(creation_stack,content):
    authority=make_authority(creation_stack,content)
    for state in (None,'cleared'):
        a=deepcopy(authority);a['configuration'].pop('relevance_scope')
        if state is None: a['configuration'].pop('relevance_scope_state')
        else: a['configuration']['relevance_scope_state']['status']='cleared'
        assert build(a) is None
