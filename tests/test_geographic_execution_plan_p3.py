"""Scripted fixture contracts, never model semantic quality tests."""
import copy
import json
import socket
import pytest
from experiments.geographic_execution_plan import p3_bridge as bridge

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('network/model entry forbidden')
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    for module in (bridge.names, bridge.shared):
        monkeypatch.setattr(module, 'evaluate_relevance', blocked)

@pytest.fixture
def profile():
    return bridge.freeze_p3_inputs(model='qwen3.6-plus', model_config_source={'origin': 'fixture', 'reference': 'explicit-test-config'})

def state(profile, code='650200', policy='local_first_with_supplement'):
    catalog = profile['admin_catalog']['snapshot']
    records = {r['code']: r for r in catalog['records']}
    r = records[code]
    return {'status': 'resolved', 'target_code': code, 'target_level': r['level'], 'province_code': r['province_code'],
            'catalog_version': catalog['version'], 'target': {'name': r['name']},
            'province': {'name': records[r['province_code']]['name']}, 'selection_policy': policy}

def candidate(title='克拉玛依市培训收费', **post):
    return {'platform':'dy', 'content_key':'dy:p', 'post':{'id':'p','title':title, **post}, 'comments':[],
            'source_queries':['克拉玛依市包过'], 'raw_ref':'fixture:one'}

def response(place='克拉玛依市'):
    return {'status':'located','locations':[{'field':'title','place':place}]}

@pytest.mark.parametrize('aliases,role',[(False,False),(False,True),(True,False),(True,True)])
def test_flags_freeze_restore(profile, aliases, role):
    p=bridge.freeze_p3_inputs(model='qwen3.6-plus', model_config_source={'origin':'fixture','reference':'flags'}, use_place_aliases=aliases,use_role_prompt=role)
    restored=json.loads(json.dumps(p))
    bridge.validate_frozen_p3_inputs(restored)
    req=bridge.prepare(state(restored), restored,candidate())
    assert req['repairs']=={'place_aliases':aliases,'role_prompt':role}
    assert bridge.consume(req,response())['geo_tier']=='local'

@pytest.mark.parametrize('code,level',[('650200','city'),('440300','city'),('220000','province'),('220200','city'),('110000','city'),('650000','province'),('310000','city')])
def test_administrative_adapter(profile,code,level):
    ctx=bridge.derive_scope_context(state(profile,code),profile)
    assert ctx['target_level']==level
    assert ctx['administrative_target']['target_code']==code


def test_isolation(profile):
    a=candidate(); a['comments']=[{'id':'c','user_id':'u','text':'COMMENT_SENTINEL 北京','ip_location':'新疆'}]
    b=copy.deepcopy(a); b['comments'][0]['text']='changed 深圳'; b['source_queries']=['OTHER_QUERY']
    first=bridge.prepare(state(profile),profile,a)
    second=bridge.prepare(state(profile,'440300'),profile,b)
    assert first['prompt']==second['prompt']
    assert first['model_input_digest']==second['model_input_digest']
    assert 'COMMENT_SENTINEL' not in first['prompt']
    assert '克拉玛依市包过' not in first['prompt']
    assert first['raw_evidence_digest']!=second['raw_evidence_digest']
    assert bridge.consume(first,response())['geo_tier']=='local'
    assert bridge.consume(second,response())['geo_tier']=='other_region'

@pytest.mark.parametrize('change',['flag','model','catalog','overlay','prompt','code','nan'])
def test_tamper_rejected(profile,change):
    p=copy.deepcopy(profile)
    if change=='flag': p['use_role_prompt']=1
    elif change=='model': p['model']['identifier']='different'
    elif change=='catalog': p['admin_catalog']['snapshot']['version']='wrong'
    elif change=='overlay': p['place_alias_overlay']['snapshot']['aliases']=[]
    elif change=='prompt': p['prompt_snapshot']='different'
    elif change=='code': p['code_version']['commit']='wrong'
    else: p['ip_policy']['thresholds']['min_target_share']=float('nan')
    with pytest.raises(ValueError): bridge.validate_frozen_p3_inputs(p)

@pytest.mark.parametrize('title,extra',[('',{}),('内容',{'truncated':True})])
def test_incomplete_never_ip_promotes(profile,title,extra):
    c=candidate(title,author_ip_location='新疆',**extra)
    req=bridge.prepare(state(profile),profile,c)
    result=bridge.consume(req,{'status':'no_location','locations':[]})
    assert result['evaluation_status']=='invalid_response'
    assert result['geo_tier']!='ip_only'

@pytest.mark.parametrize('raw',[{}, {'status':'located','locations':[{'field':'desc','place':'克拉玛依市'}]}, {'status':'insufficient_context','locations':[]}])
def test_invalid_or_insufficient_not_ip(profile,raw):
    req=bridge.prepare(state(profile),profile,candidate(author_ip_location='新疆'))
    assert bridge.consume(req,raw)['geo_tier']!='ip_only'


def test_author_ip_without_comments_and_policy(profile):
    req=bridge.prepare(state(profile,policy='local_only'),profile,candidate('没有地点的收费',author_ip_location='新疆'))
    decision=bridge.consume(req,{'status':'no_location','locations':[]})
    assert decision['geo_tier']=='ip_only'
    risk={'valid':True,'strength':'strong'}; before=copy.deepcopy(risk)
    assert not bridge.consume_policy(decision,target_level='city',selection_policy='local_only',risk=risk)['eligible']
    assert bridge.consume_policy(decision,target_level='city',selection_policy='local_first_with_supplement',risk=risk)['eligible']
    assert risk==before
    for invalid in ({'valid':False,'strength':'strong'},{'valid':True,'strength':'weak'},{'valid':True,'strength':'strong','infrastructure_error':True}):
        assert not bridge.consume_policy(decision,target_level='city',selection_policy='local_first_with_supplement',risk=invalid)['eligible']

@pytest.mark.parametrize('code,tier',[('650000','province_unknown'),('650100','other_region'),('440300','other_region')])
def test_city_tiers(profile,code,tier):
    record=next(r for r in profile['admin_catalog']['snapshot']['records'] if r['code']==code)
    req=bridge.prepare(state(profile),profile,candidate(record['name']+'收费'))
    assert bridge.consume(req,response(record['name']))['geo_tier']==tier


def test_province_local_and_not_nationwide(profile):
    req=bridge.prepare(state(profile,'650000'),profile,candidate())
    d=bridge.consume(req,response())
    assert d['geo_tier']=='local'
    assert bridge.consume_policy(d,target_level='province',selection_policy='local_first_with_supplement',risk={'valid':True,'strength':'weak'})['eligible']
    d['geo_tier']='ip_only'
    assert not bridge.consume_policy(d,target_level='province',selection_policy='local_first_with_supplement',risk={'valid':True,'strength':'strong'})['eligible']


def test_cross_post_and_identity_rejected(profile):
    c=candidate(); c['comments']=[{'id':'c','post_id':'another','user_id':'u','text':'x'}]
    with pytest.raises(ValueError): bridge.prepare(state(profile),profile,c)
    c=candidate(user_id='a',author_identity={'namespace':'user_id','value':'b'})
    with pytest.raises(ValueError): bridge.prepare(state(profile),profile,c)

@pytest.mark.parametrize('users,with_ids,expected',[(10,True,'ip_only'),(9,True,'unresolved'),(10,False,'unresolved')])
def test_comment_ip_threshold_and_stable_identity(profile,users,with_ids,expected):
    c=candidate('普通收费咨询')
    c['comments']=[{'id':str(i),'text':'克拉玛依市 评论不定位','ip_location':'新疆', **({'user_id':str(i)} if with_ids else {})} for i in range(users)]
    req=bridge.prepare(state(profile),profile,c)
    result=bridge.consume(req,{'status':'no_location','locations':[]})
    assert result['geo_tier']==expected
    assert '克拉玛依市 评论不定位' not in req['prompt']


def test_comparison_and_conflict(profile):
    req=bridge.prepare(state(profile),profile,candidate('克拉玛依市和深圳市收费比较'))
    raw={'status':'located','locations':[{'field':'title','place':'克拉玛依市'},{'field':'title','place':'深圳市'}]}
    d=bridge.consume(req,raw)
    assert d['geo_tier']=='local' and len(d['evidence_refs'])==2
    raw['status']='conflict'
    d=bridge.consume(req,raw)
    assert d['geo_tier']=='conflict'
    assert not bridge.consume_policy(d,target_level='city',selection_policy='local_first_with_supplement',risk={'valid':True,'strength':'strong'})['eligible']


def test_ip_changes_only_evidence(profile):
    a=candidate('没有地点的收费',author_ip_location='新疆')
    b=copy.deepcopy(a); b['post']['author_ip_location']='广东'
    ar=bridge.prepare(state(profile),profile,a); br=bridge.prepare(state(profile),profile,b)
    assert ar['prompt']==br['prompt'] and ar['raw_evidence_digest']!=br['raw_evidence_digest']
    raw={'status':'no_location','locations':[]}
    assert bridge.consume(ar,raw)['geo_tier']=='ip_only'
    assert bridge.consume(br,raw)['geo_tier']=='unresolved'

@pytest.mark.parametrize('changes',[{'use_place_aliases':1},{'use_role_prompt':'true'},{'model':''},{'model_config_source':{}},{'ip_policy':{'min_valid_users':True,'min_valid_coverage':.5,'min_target_share':.6}}])
def test_invalid_freeze_inputs(changes):
    args={'model':'qwen3.6-plus','model_config_source':{'origin':'fixture','reference':'test'}}
    args.update(changes)
    with pytest.raises(ValueError): bridge.freeze_p3_inputs(**args)


def test_dependency_corruption_does_not_fallback(profile,monkeypatch,tmp_path):
    import shutil
    shutil.copytree(bridge.ROOT,tmp_path/'p3')
    monkeypatch.setattr(bridge,'ROOT',tmp_path/'p3')
    (bridge.ROOT/'relevance_scope.py').write_text('corrupted')
    with pytest.raises(ValueError,match='DEPENDENCY_DIGEST'): bridge.validate_frozen_p3_inputs(profile)


def test_unknown_schema_pending_and_unsupported_target(profile):
    p=copy.deepcopy(profile); p['schema_version']='future'
    with pytest.raises(ValueError): bridge.validate_frozen_p3_inputs(p)
    s=state(profile); s['status']='pending'
    with pytest.raises(ValueError): bridge.derive_scope_context(s,profile)
    s=state(profile); s['target_code']='650202'
    with pytest.raises(ValueError): bridge.derive_scope_context(s,profile)


def test_content_alias_overlay_is_not_ip_or_scope_alias(profile):
    req=bridge.prepare(state(profile),profile,candidate('克拉瑪依市收费'))
    assert bridge.consume(req,response())['geo_tier']=='local'
    disabled=bridge.freeze_p3_inputs(model='qwen3.6-plus',model_config_source={'origin':'fixture','reference':'alias-off'},use_place_aliases=False)
    req_off=bridge.prepare(state(disabled),disabled,candidate('克拉瑪依市收费'))
    assert bridge.consume(req_off,response())['evaluation_status']=='invalid_response'
    ip_req=bridge.prepare(state(profile),profile,candidate('普通收费',author_ip_location='克拉瑪依市'))
    assert bridge.consume(ip_req,{'status':'no_location','locations':[]})['geo_tier']=='unresolved'
    s=state(profile); s['target']['name']='克拉瑪依市'
    with pytest.raises(ValueError): bridge.derive_scope_context(s,profile)

@pytest.mark.parametrize('mutate',['province','level','code','version'])
def test_scope_binding_not_guessed(profile,mutate):
    s=state(profile)
    if mutate=='province': s['province_code']='440000'
    elif mutate=='level': s['target_level']='province'
    elif mutate=='code': s['target_code']='nonexistent'
    else: s['catalog_version']='live-newer'
    with pytest.raises(ValueError): bridge.derive_scope_context(s,profile)


def test_response_and_request_binding(profile):
    req=bridge.prepare(state(profile),profile,candidate())
    req['repairs']['role_prompt']=False
    assert bridge.consume(req,response())['evaluation_status']=='invalid_response'


def test_missing_required_dependency_rejected(profile,monkeypatch,tmp_path):
    import shutil
    shutil.copytree(bridge.ROOT,tmp_path/'p3'); monkeypatch.setattr(bridge,'ROOT',tmp_path/'p3')
    (bridge.ROOT/'location_catalog.json').unlink()
    with pytest.raises(FileNotFoundError): bridge.validate_frozen_p3_inputs(profile)
