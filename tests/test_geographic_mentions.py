from copy import deepcopy
import pytest
from backend.audit_agent import geographic_mentions as m
from backend.audit_agent.scoped_selection import digest


def plan(code='650200'):
    profile=m.freeze_profile()
    context=m.derive_context({'target_code':code,'province_code':code[:2]+'0000','selection_policy':'local_only'},profile)
    return {'mention_profile':profile,'scope_context':context,'scope_digest':digest(context),'policy_digest':digest(context['policy'])}


def candidate(text='',comments=(),ip=''):
    return {'post':{'title':text,'desc':'','author_ip_location':ip},'comments':list(comments)}


@pytest.mark.parametrize('name',['克拉玛依','克拉玛依市','克拉玛依区','独山子','独山子区','白碱滩','白碱滩区','乌尔禾','乌尔禾区'])
def test_city_districts_share_target_without_disambiguation(name):
    assert m.evaluate(plan(),candidate(name))['result']['geo_tier']=='local'
    assert m.evaluate(plan(),candidate(comments=[{'comment_id':'c','content':name}]))['result']['geo_tier']=='province_unknown'


@pytest.mark.parametrize('name',['伊犁','伊犁州','喀什','北疆','南疆','Xin Jiang','XINJIANG','新 疆','新彊'])
def test_xinjiang_aliases_and_regional_names(name):
    assert m.evaluate(plan('650000'),candidate(name))['result']['geo_tier']=='local'
    assert m.evaluate(plan(),candidate(name))['result']['geo_tier']=='province_unknown'


def test_overlap_and_negative_examples():
    result=m.evaluate(plan('650000'),candidate('伊犁州旅游'))['result']
    assert len(result['evidence_refs'])==1
    for text in ['温泉','XJ','疆','新','自治区','兵团']:
        assert m.evaluate(plan(),candidate(text))['result']['geo_tier']=='unresolved'
    assert m.evaluate(plan('510100'),candidate('克拉玛依区'))['result']['geo_tier']=='unresolved'


def test_author_ip_not_commenter_ip_and_no_ai():
    assert m.evaluate(plan(),candidate(ip='IP属地：新疆'))['result']['geo_tier']=='ip_only'
    assert m.evaluate(plan('650000'),candidate(ip='新疆'))['result']['geo_tier']=='ip_only'
    assert m.evaluate(plan(),candidate(comments=[{'content':'你好','ip_location':'新疆'}]))['result']['geo_tier']=='unresolved'
    record=m.evaluate(plan(),candidate('不去新疆'))
    assert record['result']['geo_tier']=='province_unknown'
    assert not {'request','model','attempts'} & record.keys()


def test_frozen_catalog_and_all_xinjiang_counties():
    p=m.freeze_profile()
    assert sum(r['level']=='county' and r['province_code']=='650000' for r in p['matching_records'])==112
    for r in p['matching_records']:
        if r['level']=='county' and r['province_code']=='650000':
            assert m.evaluate(plan('650000'),candidate(r['name']))['result']['geo_tier']=='local',r
    bad=deepcopy(p);bad['max_rounds']=4
    with pytest.raises(ValueError):m.validate_profile(bad)


def test_frozen_profile_replays_when_live_catalog_and_aliases_change(monkeypatch):
    frozen=plan()
    original=m.evaluate(frozen,candidate('独山子租房'))
    m._INDEX_CACHE.clear()
    monkeypatch.setattr(m,'_catalog',lambda: (_ for _ in ()).throw(AssertionError('live catalog read')))
    monkeypatch.setattr(m,'_frozen_profile',lambda *args: (_ for _ in ()).throw(AssertionError('live profile read')))
    monkeypatch.setattr(m,'COUNTY_ALIASES',set())
    m.validate_profile(frozen['mention_profile'])
    assert m.evaluate(frozen,candidate('独山子租房'))==original


def test_index_is_bound_to_frozen_snapshot_and_cache_is_bounded():
    p=plan()
    assert m.evaluate(p,candidate('独山子'))['result']['geo_tier']=='local'
    for i in range(5):
        variant=deepcopy(p)
        profile=variant['mention_profile']
        profile['county_aliases']=[x for x in profile['county_aliases'] if x!='独山子']+[f'冻结别名{i}']
        profile['profile_digest']=digest({k:v for k,v in profile.items() if k!='profile_digest'})
        assert m.evaluate(variant,candidate('独山子'))['result']['geo_tier']=='unresolved'
    assert len(m._INDEX_CACHE)<=3
    assert m.evaluate(p,candidate('独山子'))['result']['geo_tier']=='local'
    bad=deepcopy(p['mention_profile'])
    bad['matching_records'][0]['province_code']='650000'
    bad['profile_digest']=digest({k:v for k,v in bad.items() if k!='profile_digest'})
    with pytest.raises(ValueError):m.validate_profile(bad)


def test_national_counties_do_not_expand_recall_and_xinjiang_keeps_coverage():
    # Only pre-existing legacy entries retain county coverage outside Xinjiang.
    assert m.evaluate(plan('510100'),candidate('金堂县'))['result']['geo_tier']=='unresolved'
    assert m.evaluate(plan('650000'),candidate('伊宁市'))['result']['geo_tier']=='local'


def test_post_author_ip_precedes_profile_and_ip_requires_province():
    value=candidate(ip='新疆')
    value['post']['ip_location']='四川'
    assert m.evaluate(plan(),value)['result']['geo_tier']=='unresolved'
    value['post']['ip_location']='新疆'
    value['post']['author_ip_location']='四川'
    assert m.evaluate(plan(),value)['result']['geo_tier']=='ip_only'
    assert m.evaluate(plan(),candidate(ip='克拉玛依'))['result']['geo_tier']=='unresolved'
