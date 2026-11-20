"""Frozen mention-only geography. No model calls or changes to risk scoring."""
from collections import OrderedDict
from threading import RLock
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
import json
import re
from .scoped_selection import digest

VERSION = 'geographic-mention-v1'
PROFILE = 'geographic-mention-profile-v1'
COUNTY_ALIASES = set('库尔勒 库车 伊宁 博乐 莎车 泽普 叶城 巴楚 特克斯 昭苏 布尔津 富蕴 新源 沙湾 额敏 奇台 玛纳斯 石河子 阿拉尔 霍尔果斯 塔什库尔干 塔县 克拉玛依 独山子 白碱滩 乌尔禾'.split())
_VALIDATED_PROFILES = OrderedDict()
_VALIDATED_PLANS = OrderedDict()
_VALIDATION_LOCK = RLock()


def _already_validated(cache, checksum):
    # Call only after hashing the actual supplied data, never its claimed digest.
    with _VALIDATION_LOCK:
        if checksum in cache:
            cache.move_to_end(checksum)
            return True
    return False


def _remember_validated(cache, checksum):
    with _VALIDATION_LOCK:
        cache[checksum] = True
        cache.move_to_end(checksum)
        if len(cache) > 16:
            cache.popitem(last=False)


def is_mention_plan(plan):
    return plan.get('geographic_strategy') == VERSION


@lru_cache(maxsize=1)
def _catalog():
    base = Path(__file__).parents[1] / 'investigation_creation/data/geographic_catalog.json'
    admin = json.loads(base.read_text())
    records = deepcopy(admin['records'])
    by_name = {r['name']: r for r in records}
    legacy = json.loads((Path(__file__).parent / 'geographic_p3_runtime/location_catalog.json').read_text())
    for entity in legacy['entities']:
        if entity['name'] in by_name:
            r = by_name[entity['name']]
            r['aliases'] = sorted(set(r['aliases'] + entity.get('aliases', [])) - {r['name']})
    extra = {'伊犁哈萨克自治州':['伊犁','伊犁州'], '喀什地区':['喀什'], '和田地区':['和田'], '阿克苏地区':['阿克苏'], '塔城地区':['塔城'], '阿勒泰地区':['阿勒泰'], '昌吉回族自治州':['昌吉州'],
             '博尔塔拉蒙古自治州':['博州'], '巴音郭楞蒙古自治州':['巴州'],
             '克孜勒苏柯尔克孜自治州':['克州'], '克拉玛依市':['克拉玛依地区'],
             '塔什库尔干塔吉克自治县':['塔什库尔干','塔县'],
             '新疆维吾尔自治区':['新疆維吾爾自治區','南疆','北疆','东疆','南北疆','天山南北','全疆','疆内','Xinjiang','Xin Jiang','新 疆','新·疆','新彊']}
    for name, aliases in extra.items():
        if name in by_name:
            by_name[name]['aliases'] = sorted(set(by_name[name]['aliases'] + aliases))
    return admin, records, legacy['entities']


def freeze_profile(max_rounds=3):
    if type(max_rounds) is not int or not 1 <= max_rounds <= 3:
        raise ValueError('invalid geographic max rounds')
    return deepcopy(_frozen_profile(max_rounds))


@lru_cache(maxsize=3)
def _frozen_profile(max_rounds):
    if type(max_rounds) is not int or not 1 <= max_rounds <= 3:
        raise ValueError('invalid geographic max rounds')
    admin, records, legacy = _catalog()
    result = dict(schema_version=PROFILE, validation_origin='confirmed_run', geographic_strategy=VERSION,
                  max_rounds=max_rounds, admin_catalog={'snapshot':deepcopy(admin)},
                  matching_records=deepcopy(records), legacy_places=deepcopy(legacy),
                  county_aliases=sorted(COUNTY_ALIASES))
    result['profile_digest'] = digest(result)
    return result


def validate_profile(profile):
    from backend.investigation_creation.geographic_catalog import validate_records
    keys = {'schema_version','validation_origin','geographic_strategy','max_rounds',
            'admin_catalog','matching_records','legacy_places','county_aliases','profile_digest'}
    if not isinstance(profile,dict) or set(profile)!=keys:
        raise ValueError('invalid mention profile schema')
    if (profile['schema_version']!=PROFILE or profile['validation_origin']!='confirmed_run'
            or profile['geographic_strategy']!=VERSION):
        raise ValueError('invalid mention profile version')
    if type(profile['max_rounds']) is not int or not 1<=profile['max_rounds']<=3:
        raise ValueError('invalid geographic max rounds')
    if profile['profile_digest']!=digest({k:v for k,v in profile.items() if k!='profile_digest'}):
        raise ValueError('mention profile digest mismatch')
    if _already_validated(_VALIDATED_PROFILES, profile['profile_digest']):
        return
    admin=profile['admin_catalog']
    if not isinstance(admin,dict) or set(admin)!={'snapshot'} or not isinstance(admin['snapshot'],dict):
        raise ValueError('invalid frozen administrative catalog')
    snapshot=admin['snapshot']
    if not isinstance(snapshot.get('version'),str) or not snapshot['version']:
        raise ValueError('missing administrative catalog version')
    original={r['code']:r for r in validate_records(snapshot.get('records'))}
    records=validate_records(profile['matching_records'])
    if {r['code'] for r in records}!=set(original):
        raise ValueError('matching catalog identities differ')
    for r in records:
        if any(r[k]!=original[r['code']][k] for k in ('name','level','kind','province_code')):
            raise ValueError('matching catalog identity changed')
    aliases=profile['county_aliases']
    if not isinstance(aliases,list) or len(set(aliases))!=len(aliases) or any(not isinstance(x,str) or not x.strip() for x in aliases):
        raise ValueError('invalid frozen county aliases')
    entities=profile['legacy_places']
    if not isinstance(entities,list):raise ValueError('invalid frozen legacy places')
    names={r['name']:r for r in records}
    for e in entities:
        if (not isinstance(e,dict) or not isinstance(e.get('name'),str) or not e['name'].strip()
                or not isinstance(e.get('aliases'),list)
                or any(not isinstance(a,str) or not a.strip() for a in e['aliases'])
                or e.get('province') not in names or names[e['province']]['level']!='province'):
            raise ValueError('invalid legacy place identity')
        if e.get('city') and (e['city'] not in names or names[e['city']]['province_code']!=names[e['province']]['code']):
            raise ValueError('invalid legacy place city')
    _remember_validated(_VALIDATED_PROFILES, profile['profile_digest'])


def derive_context(state, profile):
    validate_profile(profile)
    records = {r['code']:r for r in profile['admin_catalog']['snapshot']['records']}
    target, province = records[state['target_code']], records[state['province_code']]
    return dict(schema_version=VERSION, scope_origin='confirmed_run',
                target_level='city' if target['level']=='prefecture' or target['kind'] in ('直辖市','municipality') else 'province',
                administrative_target=dict(level=target['level'],kind=target['kind'],target_code=target['code'],province_code=province['code'],catalog_version=profile['admin_catalog']['snapshot']['version'],catalog_digest=digest(profile['admin_catalog']['snapshot'])),
                scope={'target':{'name':target['name'],'aliases':sorted(target['aliases'])},'province':{'name':province['name'],'aliases':sorted(province['aliases'])},'selection_policy':state['selection_policy'],'topic':None,'content_unit':'parent_post_mention'},
                policy={'version':VERSION,'max_rounds':profile['max_rounds']})


def project_plan(p2, *, execution_id, run_id, job_id, config_hash, risk_config):
    profile = p2.inputs.p3_inputs
    context = derive_context(p2.inputs.configuration.relevance_scope_state.model_dump(mode='json'), profile)
    identity = p2.identity
    collection = p2.collection.model_dump(mode='json')
    capture = {**collection['effective_parameters'], **{k:collection[k] for k in ('candidate_count','candidate_comment_limit','detail_comment_limit','collect_comments')}}
    queries = []
    for q in p2.execution_queries:
        logical = digest({'p2_plan_digest':p2.plan_digest,'order':q.order})
        for index in range(1, profile['max_rounds']+1):
            queries.append(dict(query_id=digest({'logical_query_id':logical,'round_index':index}), logical_query_id=logical, round_index=index,max_rounds=profile['max_rounds'],query=q.query,max_notes=q.per_keyword_limit,capture_parameters=deepcopy(capture),risk_input_ref='confirmed:risk:'+p2.inputs.risk_inputs['digest']))
    result = dict(schema_version='p4-confirmed-input-v1',validation_origin='confirmed_run',geographic_strategy=VERSION,
        execution_id=execution_id,platform='dy',mode='search',binding=dict(owner=identity.owner,session=identity.session_id,draft=identity.draft_id,revision=identity.revision),runtime_binding=dict(run_id=run_id,job_id=job_id,config_hash=config_hash,p2_plan_digest=p2.plan_digest),
        scope_digest=digest(context),policy_digest=digest(context['policy']),selection_policy=p2.policy.selection_policy,target_level=context['target_level'],
        versions=dict(admin_catalog=digest(profile['matching_records']),risk=digest(risk_config)),
        triage_mode='select',queries=queries,task_limit=collection['max_total_notes'],scope_context=context,risk_config=risk_config,mention_profile=deepcopy(profile))
    result['plan_digest']=digest(result)
    return result


def validate_projected_plan(plan, **expected):
    if not is_mention_plan(plan) or plan.get('schema_version') != 'p4-confirmed-input-v1' or plan.get('validation_origin') != 'confirmed_run':
        raise ValueError('invalid mention plan')
    if plan['plan_digest'] != digest({k:v for k,v in plan.items() if k!='plan_digest'}):
        raise ValueError('plan digest mismatch')
    for key, value in expected.items():
        if value is not None and plan[key.removeprefix('expected_')] != value:
            raise ValueError('plan binding mismatch')
    if _already_validated(_VALIDATED_PLANS, plan['plan_digest']):
        return
    validate_profile(plan['mention_profile'])
    if plan['scope_digest']!=digest(plan['scope_context']) or plan['policy_digest']!=digest(plan['scope_context']['policy']) or plan['versions']['risk']!=digest(plan['risk_config']):
        raise ValueError('mention bindings mismatch')
    if plan.get('platform')!='dy' or plan.get('mode')!='search' or plan.get('triage_mode')!='select':
        raise ValueError('invalid mention execution mode')
    for k in ('plan_digest','scope_digest','policy_digest'):
        if not re.fullmatch(r'[0-9a-f]{64}',plan.get(k,'')): raise ValueError('invalid digest')
    binding=plan.get('binding',{})
    if not all(isinstance(binding.get(k),str) and binding[k] for k in ('owner','session','draft')) or type(binding.get('revision')) is not int or binding['revision']<1:
        raise ValueError('invalid mention identity')
    runtime=plan.get('runtime_binding',{})
    if not all(isinstance(runtime.get(k),str) and runtime[k] for k in ('run_id','job_id')) or not all(re.fullmatch(r'[0-9a-f]{64}',runtime.get(k,'')) for k in ('config_hash','p2_plan_digest')):
        raise ValueError('invalid mention runtime binding')
    context=plan['scope_context']; admin=context['administrative_target']
    rebuilt=derive_context(dict(target_code=admin['target_code'],province_code=admin['province_code'],selection_policy=plan['selection_policy']),plan['mention_profile'])
    if context!=rebuilt or plan['target_level']!=context['target_level'] or plan['versions']['admin_catalog']!=digest(plan['mention_profile']['matching_records']):
        raise ValueError('invalid mention scope')
    queries=plan['queries']; rounds=plan['mention_profile']['max_rounds']
    if not queries or len({q['query_id'] for q in queries})!=len(queries) or len(queries)%rounds:
        raise ValueError('invalid mention queries')
    if type(plan['task_limit']) is not int or plan['task_limit']<1:raise ValueError('invalid task limit')
    for order,start in enumerate(range(0,len(queries),rounds)):
        group=queries[start:start+rounds]; first=group[0]
        logical=digest({'p2_plan_digest':runtime['p2_plan_digest'],'order':order})
        for index,q in enumerate(group,1):
            if (q['logical_query_id']!=logical or q['round_index']!=index or q['max_rounds']!=rounds
                    or q['query_id']!=digest({'logical_query_id':logical,'round_index':index})
                    or type(q['max_notes']) is not int or q['max_notes']<1
                    or not isinstance(q['query'],str) or not q['query'].strip()
                    or any(q[k]!=first[k] for k in ('query','max_notes','capture_parameters','risk_input_ref'))):
                raise ValueError('invalid mention rounds')
    _remember_validated(_VALIDATED_PLANS, plan['plan_digest'])


_INDEX_CACHE = OrderedDict()
_INDEX_LOCK = RLock()


def _index(profile):
    # Always authenticate the supplied snapshot, including on a cache hit.
    validate_profile(profile)
    key=profile['profile_digest']
    with _INDEX_LOCK:
        if key not in _INDEX_CACHE:
            _INDEX_CACHE[key]=_build_index(profile)
            if len(_INDEX_CACHE)>3:
                _INDEX_CACHE.popitem(last=False)
        _INDEX_CACHE.move_to_end(key)
        return _INDEX_CACHE[key]


def _build_index(profile):
    records=profile['matching_records']; by_code={r['code']:r for r in records}
    result={}
    for r in records:
        positive=r['level']!='county' or r['province_code']=='650000'
        city=r['code'] if r['level']=='prefecture' else r['code'][:4]+'00'
        if city not in by_code or by_code[city]['level']!='prefecture': city=None
        if r['kind'] in ('直辖市','municipality'):city=r['code']
        for name in [r['name'],*r['aliases']]:
            allowed=positive and (r['level']!='county' or name==r['name'] or name in profile['county_aliases'])
            if allowed or name==r['name']:
                result.setdefault(name,[]).append((r['province_code'],city,allowed))
    names={r['name']:r['code'] for r in records}
    for e in profile['legacy_places']:
        province=names.get(e['province']); city=names.get(e.get('city'))
        if province:
            for name in [e['name'],*e.get('aliases',[])]:
                result.setdefault(name,[]).append((province,city,True))
    return result


def _matches(text,index):
    spans=[]
    for name, places in index.items():
        # Chinese names are exact literal matches. Avoid compiling thousands
        # of absent names for every collected comment; ASCII still uses its
        # case-insensitive word-boundary rule below.
        if not name.isascii() and name not in text:
            continue
        regex=re.escape(name)
        if name.isascii(): regex=r'(?<![A-Za-z])'+regex+r'(?![A-Za-z])'
        for match in re.finditer(regex,text,re.I if name.isascii() else 0):
            spans.append((match.start(),match.end(),name,places))
    for start,end,name,places in spans:
        if any(a<=start and end<=b and b-a>end-start for a,b,_,_ in spans):continue
        provinces={p[0] for p in places}; cities={p[1] for p in places}
        if not any(p[2] for p in places):continue
        yield dict(start=start,end=end,quote=text[start:end],place=name,province_code=next(iter(provinces)) if len(provinces)==1 else None,city_code=next(iter(cities)) if len(cities)==1 else None)


def evaluate(plan,candidate):
    profile=plan['mention_profile']; index=_index(profile)
    raw=candidate.get('raw_source',candidate);post=raw['post']; context=plan['scope_context']['administrative_target']
    refs=[];local=False;same=False
    sources=[(f,post.get(f) if isinstance(post.get(f),str) else '',None,None) for f in ('title','desc')]
    for n,c in enumerate(raw.get('comments',[])):
        text=c.get('content') or c.get('text') or ''
        if not isinstance(text,str): continue
        sources.append(('comment',text,str(c.get('comment_id') or c.get('id')) if c.get('comment_id') or c.get('id') else None,n))
    for field,text,cid,comment_index in sources:
        for hit in _matches(text,index):
            if hit['province_code']!=context['province_code']:continue
            direct=context['target_code']==hit['province_code'] or context['target_code']==hit['city_code']
            same=True;local=local or (direct and field!='comment')
            refs.append(dict(field=field,comment_id=cid,comment_index=comment_index,**hit))
    ip=str(post.get('ip_location') or post.get('author_ip_location') or '').strip()
    ip=re.sub(r'^IP属地[：: ]*','',ip,flags=re.I).strip()
    ip_provinces={r['code'] for r in profile['matching_records'] if r['level']=='province'
                  and ip.casefold() in {name.casefold() for name in [r['name'],*r['aliases']]}}
    ip_support=ip_provinces=={context['province_code']}
    tier='local' if local else 'province_unknown' if same else 'ip_only' if ip_support else 'unresolved'
    result=dict(schema_version=VERSION,evaluation_status='completed',errors=[],status='located' if refs else 'no_location',geo_tier=tier,evidence_refs=refs,author_ip={'value':ip,'source':'ip_location' if post.get('ip_location') else 'author_ip_location'} if ip else None)
    return dict(geographic_strategy=VERSION,evidence_digest=digest(candidate),scope_digest=plan['scope_digest'],policy_digest=plan['policy_digest'],result=result)


def validate_occurrence_geo(plan,occurrence):
    if occurrence['geo']!=evaluate(plan,occurrence['candidate']):
        raise ValueError('mention geographic evidence mismatch')
