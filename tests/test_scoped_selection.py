"""Pure selector contracts; fixtures are explicitly not P2 authorization."""
import copy
from dataclasses import asdict

import pytest

from audit_agent.scoped_selection import digest, select_next_batch, validate_plan
from audit_agent.triage import CandidateScore

P3 = '2120c53379d1b79c45e3cb8232b9c9b96657b183'


def make_plan(*, policy='local_first_with_supplement', target='city', limit=4, max_notes=2):
    plan = dict(schema_version='p4-fixture-input-v1', validation_origin='fixture',
                execution_id='execution', platform='dy', mode='search', binding=dict(owner='owner', session='session', draft='draft', revision=1),
                scope_digest=digest({'scope_origin': 'fixture', 'target_level': target, 'scope': {'selection_policy': policy}, 'administrative_target': {}}), policy_digest=digest({'prompt_version': 'prompt'}), selection_policy=policy,
                target_level=target, p3_version=P3, triage_mode='select',
                versions={key: digest(key) for key in ('admin_catalog', 'prompt', 'model', 'risk')},
                queries=[dict(query_id=q, query=q, max_notes=max_notes, capture_parameters={}, risk_input_ref='risk:'+q)
                         for q in ('A', 'B', 'C')], task_limit=limit)
    plan['versions']['risk'] = digest({'frozen': 'risk'})
    plan['plan_digest'] = digest(plan)
    return plan


def occurrence(plan, query, key, tier='local', *, band='weak', score=100, rank=7, model_valid=True):
    candidate = {'platform': 'dy', 'content_key': key, 'post_id': key, 'post': {'post_id': key, 'title': '新疆克拉玛依', 'desc': '', 'source_truncated': False}, 'comments': []}
    risk_input = {'content_key': key, 'rank': rank, 'search_keyword': query,
                  'config': {'frozen': 'risk'}, 'item': copy.deepcopy(candidate['post']), 'comments': []}
    evidence = digest(candidate)
    request = dict(candidate=copy.deepcopy(candidate), scope_digest=plan['scope_digest'],
                   policy_digest=plan['policy_digest'], raw_evidence_digest=evidence,
                   model_input_digest=digest({'prompt': key, 'model': 'model'}), admin_catalog_digest=plan['versions']['admin_catalog'],
                   prompt=key, context={'model': 'model', 'scope_origin': 'fixture', 'target_level': plan['target_level'], 'scope': {'selection_policy': plan['selection_policy']}, 'administrative_target': {}}, policy={'prompt_version': 'prompt'})
    ref = dict(field='title', place='克拉玛依', quote='新疆克拉玛依', spans=[dict(start=2, end=6, quote='克拉玛依')])
    result = dict(schema_version='post-only-simple-names-v2', evaluation_status='completed',
                  status='no_location' if tier == 'ip_only' else 'located', geo_tier=tier,
                  evidence_refs=[] if tier == 'ip_only' else [ref], errors=[], ip_stats={'ip_support': tier == 'ip_only'},
                  model='model', prompt_version='prompt',
                  **{k: v for k, v in request.items() if k not in ('candidate', 'prompt', 'context', 'policy')})
    oid = query + ':' + key
    return dict(occurrence_id=oid, execution_id=plan['execution_id'], query_id=query,
                platform='dy', content_key=key, rank=rank, source_keyword=query, candidate=candidate,
                evidence_digest=evidence,
                risk=dict(occurrence_id=oid, input=risk_input, input_digest=digest(risk_input), input_ref='risk:'+query,
                          config_digest=plan['versions']['risk'], model_valid=model_valid,
                          score=asdict(CandidateScore(key, rank, score, band, 'reason', model={'suspicion': band} if model_valid else None))),
                geo=dict(evidence_digest=evidence, scope_digest=plan['scope_digest'], policy_digest=plan['policy_digest'],
                         p3_version=P3, request=request, request_digest=digest(request), result=result))


def states(plan, *items):
    return {q['query_id']: dict(status='evaluated', occurrences=[o for o in items if o['query_id'] == q['query_id']])
            for q in plan['queries']}


def state(**kw):
    return dict(busy=False, occupied=[], query_success={}, total_success=0, query_reserved={}, total_reserved=0,
                closed_queries={}, excluded_occurrences=[], **kw)


def test_tier_outer_then_query_then_supplied_ranking_without_mutation():
    p = make_plan(limit=2)
    a = occurrence(p, 'A', 'province', 'province_unknown', band='strong', score=999)
    b = occurrence(p, 'B', 'localweak', rank=99)
    c = occurrence(p, 'C', 'localstrong', band='strong', score=900)
    qs = states(p, a, b, c)
    before = copy.deepcopy(qs)
    result = select_next_batch(p, qs, state())
    assert result['query_id'] == 'B' and result['picks'] == [b]
    assert qs == before
    x = occurrence(p, 'B', 'second', band='strong', score=900, rank=1)
    assert select_next_batch(p, states(p, b, x), state())['picks'] == [b, x]


@pytest.mark.parametrize('status', ['pending', 'capturing', 'captured'])
def test_partial_query_completion_finishes_all_tiers_inside_current_query(status):
    p = make_plan()
    local = occurrence(p, 'A', 'local', 'local')
    province = occurrence(p, 'A', 'province', 'province_unknown')
    qs = states(p, local, province)
    qs['B']['status'] = status
    qs['B']['occurrences'] = []
    qs['C']['status'] = status
    qs['C']['occurrences'] = []

    assert select_next_batch(p, qs, state())['picks'] == [local]

    after_local = state()
    after_local.update(occupied=['dy:local'], query_success={'A': 1}, total_success=1)
    assert select_next_batch(p, qs, after_local)['picks'] == [province]

    after_current_query = state()
    after_current_query.update(
        occupied=['dy:local', 'dy:province'],
        query_success={'A': 2},
        total_success=2,
    )
    waiting = select_next_batch(p, qs, after_current_query)
    assert waiting['status'] == 'waiting'
    assert waiting['reasons'] == ['next_query_not_evaluated']


@pytest.mark.parametrize('status', ['failed', 'paused'])
def test_failed_or_paused_query_still_blocks_selection(status):
    p = make_plan(); qs = states(p, occurrence(p, 'A', 'local'))
    qs['C']['status'] = status
    result = select_next_batch(p, qs, state())
    assert result['status'] == 'blocked' and not result['picks']


@pytest.mark.parametrize('policy,target,tier,selected', [
    ('local_only','city','province_unknown',False), ('local_only','city','ip_only',False),
    ('local_first_with_supplement','province','province_unknown',False),
    ('local_first_with_supplement','province','local',True),
    ('local_first_with_supplement','city','other_region',False),
    ('local_first_with_supplement','city','province_unknown',True)])
def test_policy_and_p3_consumption(policy,target,tier,selected):
    p = make_plan(policy=policy,target=target)
    o = occurrence(p,'A','x',tier,band='strong')
    assert bool(select_next_batch(p,states(p,o),state())['picks']) == selected


@pytest.mark.parametrize('band,valid,allowed', [('strong',True,True),('weak',True,False),('none',True,False),('weak',False,False),('strong',False,False)])
def test_ip_requires_valid_strong_but_not_risk_score(band,valid,allowed):
    p=make_plan(); o=occurrence(p,'A','x','ip_only',band=band,model_valid=valid)
    assert o['risk']['score']['model_risk'] is None
    assert bool(select_next_batch(p,states(p,o),state())['picks']) == allowed


@pytest.mark.parametrize('field,value', [('triage_mode','off'),('triage_mode','compare'),('selection_policy','bad'),('p3_version','moving'),('target_level','country')])
def test_invalid_plan_fails_closed(field,value):
    p=make_plan(); p[field]=value; p['plan_digest']=digest({k:v for k,v in p.items() if k!='plan_digest'})
    with pytest.raises(ValueError): validate_plan(p)
    assert select_next_batch(p,states(p),state())['status']=='blocked'


def test_digest_identity_and_nan_fail_closed():
    p=make_plan(); validate_plan(p,expected_execution_id='execution',expected_plan_digest=p['plan_digest'])
    for kwargs in ({'expected_execution_id':'other'},{'expected_plan_digest':'other'}):
        with pytest.raises(ValueError): validate_plan(p,**kwargs)
    p['task_limit']=99
    with pytest.raises(ValueError): validate_plan(p)
    with pytest.raises(ValueError): digest(float('nan'))


def test_budget_dedup_exclusions_and_closed_query():
    p=make_plan(max_notes=3,limit=2); a=occurrence(p,'A','x'); b=occurrence(p,'B','x'); c=occurrence(p,'B','y')
    s=state(); s.update(query_success={'A':1},total_success=1,query_reserved={'C':1},total_reserved=1,closed_queries={'A':'no_refill'})
    assert select_next_batch(p,states(p,a,b,c),s)['status']=='waiting'
    s.update(query_reserved={},total_reserved=0)
    assert select_next_batch(p,states(p,a,b,c),s)['picks']==[b]
    s['occupied']=['dy:x']; assert select_next_batch(p,states(p,a,b,c),s)['picks']==[c]
    s['excluded_occurrences']=[c['occurrence_id']]
    assert select_next_batch(p,states(p,a,b,c),s)['status']=='exhausted'
    s['busy']=True; assert select_next_batch(p,states(p,a,b,c),s)['status']=='waiting'


@pytest.mark.parametrize('score,band', [(0,'none'),(-1000,'discard')])
def test_geo_never_revives_ineligible_risk(score,band):
    p=make_plan(); o=occurrence(p,'A','x',score=score,band=band)
    assert select_next_batch(p,states(p,o),state())['status']=='exhausted'


@pytest.mark.parametrize('corruption', ['evidence','version','risk_binding','source','model_input','protocol'])
def test_mismatched_evaluation_is_blocked(corruption):
    p=make_plan(); o=occurrence(p,'A','x')
    if corruption=='evidence': o['evidence_digest']=digest('other')
    if corruption=='version': o['geo']['p3_version']='moving'
    if corruption=='risk_binding': o['risk']['score']['rank']=1
    if corruption=='source': o['geo']['result']['evidence_refs'][0]['field']='comment'
    if corruption=='model_input': o['geo']['result']['model_input_digest']=digest('other')
    if corruption=='protocol': o['geo']['result']['evaluation_status']='invalid_response'
    assert select_next_batch(p,states(p,o),state())['status']=='blocked'


@pytest.mark.parametrize('status,tier', [('insufficient_context','unresolved'),('conflict','conflict')])
def test_legitimate_insufficient_conflict_evaluated_excluded(status,tier):
    p=make_plan(); o=occurrence(p,'A','x'); o['geo']['result'].update(status=status,geo_tier=tier)
    assert select_next_batch(p,states(p,o),state())['status']=='exhausted'


@pytest.mark.parametrize('kind', ['candidate_platform', 'candidate_key', 'scope_level', 'scope_body', 'risk_config', 'post_id'])
def test_recomputed_envelope_does_not_hide_embedded_identity_change(kind):
    from audit_agent.scoped_selection import eligible_tier
    p=make_plan(); o=occurrence(p,'A','x')
    if kind == 'post_id': o['candidate']['post_id']='other'
    if kind == 'candidate_platform': o['platform']='xhs'
    if kind == 'candidate_key': o['content_key']='other'; o['risk']['score']['content_key']='other'
    if kind == 'scope_level': o['geo']['request']['context']['target_level']='province'
    if kind == 'scope_body': o['geo']['request']['context']['scope']['target']={'name':'other'}
    if kind == 'risk_config':
        o['risk']['input']['config']['frozen']='changed'
        o['risk']['input_digest']=digest(o['risk']['input'])
    o['geo']['request_digest']=digest(o['geo']['request'])
    with pytest.raises(ValueError): eligible_tier(p,o)
    assert select_next_batch(p,states(p,o),state())['status']=='blocked'


def test_already_audited_is_explicit_exclusion_and_does_not_rerank():
    from audit_agent.scoped_selection import eligible_tier
    p=make_plan(); audited=occurrence(p,'A','x',band='strong',score=900); audited['already_audited']=True
    kept=occurrence(p,'A','y',rank=88)
    before=copy.deepcopy(audited)
    assert eligible_tier(p,audited) is None
    assert select_next_batch(p,states(p,audited,kept),state())['picks']==[kept]
    assert audited==before


@pytest.mark.parametrize('field,value', [('platform','xhs'),('mode','account')])
def test_fixture_support_profile_is_explicit(field,value):
    p=make_plan();p[field]=value;p['plan_digest']=digest({k:v for k,v in p.items() if k!='plan_digest'})
    with pytest.raises(ValueError): validate_plan(p)


@pytest.mark.parametrize('changed', ['post', 'comments', 'identity'])
def test_selector_rejects_self_consistent_risk_from_different_capture(changed):
    from experiments.geographic_selection.fixtures import fixture_plan, fixture_occurrence
    p = fixture_plan(queries=('A',), task_limit=1)
    old = fixture_occurrence(p, 'A', 'x')
    changes = {'post': {'desc': '克拉玛依欠薪新原文'},
               'comments': {'comments': [{'comment_id': 'c', 'text': '新评论'}]},
               'identity': {'enterprise_verify_reason': '公安官方'}}
    current = fixture_occurrence(p, 'A', 'x', **changes[changed])
    current['risk'] = copy.deepcopy(old['risk'])
    result = select_next_batch(p, states(p, current), state())
    assert result['status'] == 'blocked'
    assert result['reasons'] == ['risk_capture_evidence_mismatch']


@pytest.mark.parametrize('field,value', [
    ('title', '新标题'), ('user_signature', '新签名'), ('nickname', '新昵称'),
    ('custom_verify', '个人认证'), ('follower_count', 10000000), ('liked_count', 999),
])
def test_old_risk_cannot_drop_raw_identity_or_ranking_fields(field, value):
    from experiments.geographic_selection.fixtures import fixture_plan, fixture_occurrence
    p = fixture_plan(queries=('A',))
    old = fixture_occurrence(p, 'A', 'x')
    current = fixture_occurrence(p, 'A', 'x', **{field: value})
    current['risk'] = old['risk']
    result = select_next_batch(p, states(p, current), state())
    assert result['status'] == 'blocked' and result['reasons'] == ['risk_capture_evidence_mismatch']



def test_risk_binding_preserves_raw_json_scalar_types():
    from experiments.geographic_selection.fixtures import fixture_plan, fixture_occurrence
    p = fixture_plan(queries=('A',))
    old = fixture_occurrence(p, 'A', 'x', user_signature=1)
    current = fixture_occurrence(p, 'A', 'x', user_signature=True)
    current['risk'] = old['risk']
    result = select_next_batch(p, states(p, current), state())
    assert result['status'] == 'blocked' and result['reasons'] == ['risk_capture_evidence_mismatch']
