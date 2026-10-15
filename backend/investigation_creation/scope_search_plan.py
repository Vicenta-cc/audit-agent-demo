"""Deterministic, fixture-only geographic plans. No confirmation or runtime hook.

The caller supplies independently verified authority; hashes are integrity checks,
not permission grants. Restoration checks a separately held identity and digest.
Search terms and the existing risk execution snapshot are never rewritten here.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from backend.audit_agent.search_terms_cap import cap_search_terms
from backend.resource_management.contracts import LexiconContent
from backend.rulesets.compiler import content_hash, compile_ruleset_content
from backend.rulesets.contracts import RuleSetContent
from .contracts import ConfirmationResolution, InvestigationDraftConfiguration, InvestigationTaskParameters
from .frozen import validate_execution_payload
from .geographic_catalog import validate_records

P1_COMMIT = '031e0cd26379ee44c2372c9a28536c377ab3d4b9'
P3_COMMIT = '2120c53379d1b79c45e3cb8232b9c9b96657b183'


def _json_value(value):
    require(type(value) in (dict, list, str, int, float, bool, type(None)), 'only strict JSON values are supported')
    if type(value) is dict:
        require(all(type(k) is str for k in value), 'JSON object keys must be strings')
        for child in value.values(): _json_value(child)
    elif type(value) is list:
        for child in value: _json_value(child)
    elif type(value) is float:
        require(math.isfinite(value), 'nonfinite JSON value')


def canonical(value: Any) -> str:
    _json_value(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class PlanIdentity(FrozenModel):
    owner: StrictStr = Field(min_length=1)
    session_id: StrictStr = Field(min_length=1)
    draft_id: StrictStr = Field(min_length=1)
    revision: StrictInt = Field(ge=1)


class SourceRevision(FrozenModel):
    draft_id: StrictStr
    revision: StrictInt = Field(ge=1)
    configuration: InvestigationDraftConfiguration


class UserMessage(FrozenModel):
    id: StrictStr
    turn_id: StrictStr
    sequence: StrictInt = Field(ge=1)
    content: StrictStr
    role: Literal['user']
    session_id: StrictStr
    owner: StrictStr


class SelectionRecord(FrozenModel):
    principal: StrictStr
    session_id: StrictStr
    draft_id: StrictStr
    operation_id: StrictStr
    expected_revision: StrictInt = Field(ge=1)
    committed_revision: StrictInt = Field(ge=2)
    target_text: StrictStr
    region_code: StrictStr
    catalog_version: StrictStr
    selection_policy: Literal['local_only', 'local_first_with_supplement']
    conversation_sequence: StrictInt = Field(ge=0)


class CopyLink(FrozenModel):
    principal: StrictStr
    session_id: StrictStr
    operation_id: StrictStr = Field(min_length=1)
    source_draft_id: StrictStr
    target_draft_id: StrictStr
    source_revision: StrictInt = Field(ge=1)
    source_status: Literal['QUEUED', 'CONFIRMED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED']
    source_configuration: InvestigationDraftConfiguration
    target_configuration: InvestigationDraftConfiguration


class Provenance(FrozenModel):
    identity: PlanIdentity
    session_owner: StrictStr
    session_scope_type: Literal['creation']
    task_draft_ids: list[StrictStr] = Field(min_length=1)
    origin_draft_id: StrictStr
    origin_revision: StrictInt = Field(ge=1)
    origin_configuration: InvestigationDraftConfiguration
    history: list[SourceRevision] = Field(min_length=1)
    lineage: list[CopyLink]
    selection: SelectionRecord | None
    message: UserMessage | None
    application_message: UserMessage | None
    anchor_message: UserMessage | None


class FixtureProvenance(Provenance):
    fixture_id: StrictStr = Field(pattern=r'^fixture:.+')


class ConfirmedProvenance(Provenance):
    authority_origin: Literal['confirmed_run']


class RuntimeLimits(FrozenModel):
    investigation_max_posts: StrictInt = Field(ge=1)
    m3_comments_per_post: StrictInt = Field(ge=0, le=1000)
    crawler_max_concurrency: StrictInt = Field(ge=1)
    search_terms_max: StrictInt = Field(ge=1, le=200)
    triage_candidates_per_keyword: StrictInt = Field(ge=1, le=30)
    triage_candidate_comments: StrictInt = Field(ge=0, le=200)
    triage_mode: Literal['off', 'select', 'compare']
    analysis_batch_size: Literal[5]
    picks_per_search_term: Literal[1]

    @model_validator(mode='before')
    @classmethod
    def reject_bool_literals(cls, value):
        if isinstance(value, dict):
            for key in ('analysis_batch_size', 'picks_per_search_term'):
                require(type(value.get(key)) is int, key + ' must be an integer')
        return value


class SettingsSnapshot(FrozenModel):
    revision: StrictInt = Field(ge=0)
    saved: InvestigationTaskParameters
    defaults: InvestigationTaskParameters
    limits: RuntimeLimits
    derivation_version: Literal['p1-task-settings-031e0cd']
    configuration_source: Literal['fixture:unified-task-settings']


class ResourceInputs(FrozenModel):
    ruleset_content: RuleSetContent
    lexicon_content: LexiconContent | None
    # Resolver output and adopted content are snapshots, never mutable paths.
    ruleset_source: dict[str, Any]
    recall_source: dict[str, Any]
    lexicon_runtime_terms: list[StrictStr]
    adoption_bindings: list[dict[str, Any]]


class PlanInputs(FrozenModel):
    identity: PlanIdentity
    title: StrictStr
    objective: StrictStr
    configuration: InvestigationDraftConfiguration
    provenance: FixtureProvenance
    resolution: ConfirmationResolution
    resources: ResourceInputs
    settings: SettingsSnapshot
    scope_context: dict[str, Any]
    risk_inputs: dict[str, Any]
    p3_inputs: dict[str, Any]
    p1_commit: Literal['031e0cd26379ee44c2372c9a28536c377ab3d4b9']


class QuerySource(FrozenModel):
    resource_strategy: Literal['existing_lexicon', 'temporary_terms']
    resource_id: StrictStr
    entry_id: StrictStr
    parent_id: StrictStr
    entry_kind: StrictStr
    selected_index: StrictInt = Field(ge=0)


class ExecutionQuery(FrozenModel):
    query: StrictStr = Field(min_length=1)
    source_term: StrictStr = Field(min_length=1)
    order: StrictInt = Field(ge=0)
    sources: list[QuerySource] = Field(min_length=1)
    per_keyword_limit: StrictInt = Field(ge=1)


class CollectionPlan(FrozenModel):
    candidate_count: StrictInt = Field(ge=1)
    candidate_comment_limit: StrictInt = Field(ge=0)
    detail_comment_limit: StrictInt = Field(ge=0)
    collect_comments: StrictBool
    max_total_notes: StrictInt = Field(ge=1)
    effective_parameters: InvestigationTaskParameters
    triage_mode: Literal['off', 'select', 'compare']
    derivation_version: Literal['p1-collection-031e0cd']


class GeographicPolicy(FrozenModel):
    version: Literal['geo-select-fixture-v1']
    selection_policy: Literal['local_only', 'local_first_with_supplement']
    tier_order: list[Literal['local', 'province_unknown', 'ip_only']]
    ip_min_suspicion: Literal['strong']
    status_policy: Literal['successful-no-location-only-for-ip-v1']
    # This describes future consumption, never changes the risk engine's mode.
    selection_strategy: Literal['p4-future-geographic-selection']
    quota_settlement: Literal['actual-selected-query-after-detail-success']


class ScopeSearchPlan(FrozenModel):
    schema_version: Literal['geo-execution-plan-v1']
    validation_origin: Literal['fixture']
    identity: PlanIdentity
    inputs: PlanInputs
    configuration_digest: StrictStr
    input_digests: dict[StrictStr, StrictStr]
    execution_queries: list[ExecutionQuery] = Field(min_length=1)
    collection: CollectionPlan
    policy: GeographicPolicy
    plan_digest: StrictStr = Field(pattern=r'^[0-9a-f]{64}$')


class ConfirmedSettingsSnapshot(SettingsSnapshot):
    configuration_source: Literal['confirmed_run:unified-task-settings']


class ConfirmedPlanInputs(PlanInputs):
    provenance: ConfirmedProvenance
    settings: ConfirmedSettingsSnapshot


class ConfirmedGeographicPolicy(GeographicPolicy):
    version: Literal['geo-select-confirmed-v1']
    selection_strategy: Literal['p4-geographic-selection']


class ConfirmedScopeSearchPlan(ScopeSearchPlan):
    policy: ConfirmedGeographicPolicy
    schema_version: Literal['geo-confirmed-execution-plan-v1']
    validation_origin: Literal['confirmed_run']
    inputs: ConfirmedPlanInputs


def _scope_key(configuration):
    body = configuration.model_dump(mode='json')
    state = dict(body.get('relevance_scope_state', {}))
    for key in ('draft_id', 'copied_from_draft_id'):
        state.pop(key, None)
    return {'scope': body.get('relevance_scope'), 'state': state}


def _validate_provenance(inputs):
    i, p, cfg = inputs.identity, inputs.provenance, inputs.configuration
    s = cfg.relevance_scope_state
    require(p.identity == i and p.session_owner == i.owner, 'source owner/session/revision mismatch')
    require(i.draft_id in p.task_draft_ids and p.task_draft_ids[-1] == i.draft_id,
            'source is not the current task draft')
    require(s.principal == i.owner and s.session_id == i.session_id and s.draft_id == i.draft_id,
            'internal state identity mismatch')
    require(_scope_key(p.origin_configuration) == _scope_key(cfg), 'source geography has been replaced')
    history = p.history
    require(len({(r.draft_id, r.revision) for r in history}) == len(history), 'duplicate revision evidence')
    by_revision = {(r.draft_id, r.revision): r.configuration for r in history}
    current_id, current_cfg = p.origin_draft_id, p.origin_configuration
    seen = {current_id}
    for link in p.lineage:
        require(link.principal == i.owner and link.session_id == i.session_id,
                'copy owner/session mismatch')
        require(link.source_draft_id == current_id and link.target_draft_id not in seen,
                'invalid copy lineage')
        require(by_revision.get((link.source_draft_id, link.source_revision)) == link.source_configuration,
                'copy source revision/configuration mismatch')
        require(by_revision.get((link.target_draft_id, 1)) == link.target_configuration,
                'copy target first revision/configuration mismatch')
        require(_scope_key(link.source_configuration) == _scope_key(current_cfg)
                == _scope_key(link.target_configuration), 'copy changed geography')
        target_state = link.target_configuration.relevance_scope_state
        require(target_state.draft_id == link.target_draft_id
                and target_state.copied_from_draft_id == link.source_draft_id,
                'copy state does not reference source')
        require(link.source_draft_id in p.task_draft_ids and link.target_draft_id in p.task_draft_ids,
                'copy is outside task')
        current_id, current_cfg = link.target_draft_id, link.target_configuration
        seen.add(current_id)
    require(current_id == i.draft_id, 'missing copy lineage')
    require((not p.lineage and not s.copied_from_draft_id) or
            (p.lineage and s.copied_from_draft_id == p.lineage[-1].source_draft_id), 'forged copy reference')
    origin = [r for r in history if (r.draft_id,r.revision)==(p.origin_draft_id,p.origin_revision)]
    require(len(origin)==1 and origin[0].configuration == p.origin_configuration, 'origin revision mismatch')
    now = [r for r in history if (r.draft_id,r.revision)==(i.draft_id,i.revision)]
    require(len(now)==1 and now[0].configuration == cfg, 'current revision evidence mismatch')
    for row in history:
        require(row.draft_id in p.task_draft_ids, 'foreign revision evidence')
        if row.draft_id == i.draft_id:
            require(row.revision <= i.revision, 'future revision evidence')
        # Every intervening revision must retain the same geographic source.
        if row.draft_id == p.origin_draft_id and row.revision >= p.origin_revision or row.draft_id in seen - {p.origin_draft_id}:
            require(_scope_key(row.configuration) == _scope_key(cfg), 'intervening geography changed')
    if s.source_kind == 'ui_selection':
        r=p.selection
        require(r is not None and p.message is None and p.application_message is None and p.anchor_message is None,
                'UI source cannot invent user messages')
        require((r.principal,r.session_id,r.draft_id,r.operation_id,r.committed_revision)==
                (i.owner,i.session_id,p.origin_draft_id,s.source_operation_id,p.origin_revision),
                'selection operation binding mismatch')
        require(r.committed_revision == r.expected_revision+1, 'selection revision mismatch')
        require((r.draft_id, r.expected_revision) in by_revision,
                'missing pre-selection revision evidence')
        require(by_revision.get((r.draft_id, r.committed_revision)) == p.origin_configuration,
                'missing or inconsistent committed selection revision evidence')
        require((r.target_text,r.region_code,r.catalog_version,r.selection_policy,r.conversation_sequence)==
                (s.target_text,s.target_code,s.catalog_version,s.selection_policy,s.source_sequence),
                'selection geography/version mismatch')
    else:
        require(p.selection is None and all((p.message,p.application_message,p.anchor_message)),
                'conversation requires actual source/application/anchor messages')
        m,a,t=p.message,p.application_message,p.anchor_message
        for row in (m,a,t):
            require(row.owner==i.owner and row.session_id==i.session_id, 'conversation owner/session mismatch')
        require((m.id,m.turn_id,m.sequence)==(s.source_user_message_id,s.source_turn_id,s.source_sequence)
                and s.source_quote in m.content, 'source quote/message mismatch')
        require(a.turn_id==s.application_turn_id and t.turn_id==s.task_anchor_turn_id
                and t.sequence <= m.sequence <= a.sequence, 'conversation task anchor mismatch')


def _effective_parameters(snapshot):
    p,lim=snapshot.saved,snapshot.limits
    if snapshot.revision==0:
        require(p == snapshot.defaults, 'revision zero must use frozen default settings')
    total=min(p.max_total_notes,lim.investigation_max_posts)
    return p.model_copy(update={
        'max_notes':lim.picks_per_search_term, 'max_total_notes':total,
        'max_comments':min(p.max_comments,lim.m3_comments_per_post) if p.collect_comments else 0,
        'get_sub_comment':p.get_sub_comment and p.collect_comments and p.max_comments>0 and lim.m3_comments_per_post>0,
        'max_concurrency':min(p.max_concurrency,lim.crawler_max_concurrency),
        'analysis_batch_size':lim.analysis_batch_size, 'analyze_limit':total, 'auto_analyze':True,
    })


def _keys(value, keys, label):
    require(type(value) is dict and set(value)==set(keys), 'invalid '+label+' fields')


def _integer(value, minimum, label):
    require(type(value) is int and value>=minimum, 'invalid integer '+label)


def _validate_profiles(inputs):
    if isinstance(inputs, ConfirmedPlanInputs):
        from backend.audit_agent.geographic_p3_runtime import validate_frozen_p3_inputs
        validate_frozen_p3_inputs(inputs.p3_inputs)
    else:
        p3=inputs.p3_inputs
        _keys(p3, {'schema_version','validation_origin','code_version','p3_schema_version','prompt_version',
            'prompt_snapshot','model','use_place_aliases','use_role_prompt','admin_catalog','location_catalog',
            'place_alias_overlay','ip_policy','profile_digest'}, 'P3 profile')
        require(p3['schema_version']=='p2-p3-fixture-profile-v1' and p3['validation_origin']=='fixture',
                'unknown P3 fixture schema/origin')
        require(type(p3['use_place_aliases']) is bool and type(p3['use_role_prompt']) is bool,
                'P3 flags must be explicit bools')
        require(p3['profile_digest']==digest({k:v for k,v in p3.items() if k!='profile_digest'}),
                'P3 profile digest mismatch')
        # Bind exact immutable dependency contents without importing an experiment or
        # reading the filesystem. Digests derive from the pinned input manifest.
        require(digest(p3['code_version'])=='c339dab1f55c3f0a4da3ea0b115ca44d9b2580a174dceeaa080601dd6c697442',
                'wrong P3 dependency contents')
        require(p3['code_version']['commit']==P3_COMMIT and p3['p3_schema_version']=='post-only-simple-names-v2',
                'wrong P3 protocol/version')
        for key, expected in {
            'admin_catalog': '0b0a106d5a770c8334b319d5af1c68a923d9b7b9f49737c7e9adf9d7d4556eb8',
            'location_catalog': '89a13f1b0928da9e6f809cccc069274e7e57b50e01ef9c96d8fc8d3664ddc021',
            'place_alias_overlay': '011394adde02e8b780a0dc02d0ec7a8bcb240591b4f43887ce6236f10f28bc4a',
        }.items():
            snapshot=p3[key]
            _keys(snapshot, {'version','snapshot','digest'}, key)
            require(snapshot['digest']==digest(snapshot['snapshot'])==expected
                    and snapshot['version']==snapshot['snapshot'].get('version'), 'wrong '+key+' snapshot')
        expected_prompt=(('post-only-source-role-v1','3b2e99c0e027eb96ea7ade888358679cc8d0c2b06d3331c0a4444fd17e9c2e77')
            if p3['use_role_prompt'] else ('post-only-simple-v1','fd788184c1f9f40c5d970beb8fd2c893b4893fc9294467ae5be9a5c2cdf2bef9'))
        require((p3['prompt_version'],digest(p3['prompt_snapshot']))==expected_prompt, 'P3 prompt/switch mismatch')
        model=p3['model']; _keys(model, {'identifier','config_source','invocation_config'}, 'P3 model')
        require(type(model['identifier']) is str and bool(model['identifier'].strip()), 'missing P3 model identity')
        _keys(model['config_source'], {'origin','reference'}, 'P3 model source')
        require(model['config_source']['origin']=='fixture' and type(model['config_source']['reference']) is str
                and bool(model['config_source']['reference'].strip()), 'missing fixture model source')
        call=model['invocation_config']
        require(canonical(call)==canonical({'max_tokens':3000,'enable_thinking':False,'request_timeout':60,'invocation_mode':'single'}),
                'P3 invocation configuration mismatch')
        ip=p3['ip_policy']; _keys(ip, {'version','fixture_only','thresholds'}, 'IP profile')
        require(ip['version']=='p2-comment-ip-fixture-v1' and ip['fixture_only'] is True, 'invalid fixture IP policy')
        thresholds=ip['thresholds'];_keys(thresholds, {'min_valid_users','min_valid_coverage','min_target_share','province_aliases'}, 'IP thresholds')
        _integer(thresholds['min_valid_users'],1,'min_valid_users')
        for key in ('min_valid_coverage','min_target_share'):
            require(type(thresholds[key]) in (int,float) and 0<thresholds[key]<=1, 'invalid IP threshold '+key)
        require(thresholds['province_aliases']=={}, 'unreviewed IP aliases')
    risk=inputs.risk_inputs
    _keys(risk, {'schema_version','category_ids','terms','search_groups','rules','model','prompt','scoring','identity','digest'}, 'risk inputs')
    require(risk['schema_version']=='geo-frozen-risk-v1', 'unknown frozen risk schema')
    require(risk['digest']==digest({k:v for k,v in risk.items() if k!='digest'}), 'risk input digest mismatch')
    require(risk['rules']==inputs.resolution.execution.prompt_profile_snapshot, 'risk rules differ from resolved triage prompt profile')
    ids=risk['category_ids']
    require(type(ids) is list and all(type(v) is str and bool(v.strip()) and v==v.strip() for v in ids)
            and len(set(ids))==len(ids), 'invalid risk categories')
    require(ids==inputs.resolution.execution.library_ids, 'risk categories differ from execution')
    require(type(risk['terms']) is list and type(risk['search_groups']) is dict, 'invalid risk terms/groups')
    require(bool(ids) or risk['terms']==[] and risk['search_groups']=={}, 'empty category context has risk data')
    for term in risk['terms']:
        _keys(term, {'keyword','match_type','category_id','risk_level','entry_kind','entry_group'}, 'risk term')
        require(all(type(v) is str for v in term.values()) and bool(term['keyword'].strip())
                and term['keyword']==term['keyword'].strip() and term['category_id'] in ids, 'invalid frozen term')
    require(all(type(k) is str and k.strip() and type(v) is str and v.strip() for k,v in risk['search_groups'].items()),
            'invalid search groups')
    _keys(risk['model'], {'name','max_comments','request_timeout'}, 'risk model')
    require(type(risk['model']['name']) is str and bool(risk['model']['name'].strip()), 'invalid risk model identifier')
    _integer(risk['model']['max_comments'],0,'max_comments');_integer(risk['model']['request_timeout'],1,'request_timeout')
    _keys(risk['prompt'], {'version','engine_sha256'}, 'risk prompt')
    require(risk['prompt']['version']=='p1-triage-031e0cd'
            and risk['prompt']['engine_sha256']=='a8c8d9c1ac3bbd339a4f49feeeff9e5ccf8415388d9cdb86c788c657df072f6b', 'risk engine version mismatch')
    scoring=risk['scoring']
    _keys(scoring, {'rule_score_high','rule_score_medium','rule_score_low','rule_score_cap','prompt_hit_limit','discard_score','model_scores'}, 'risk scoring')
    for key in ('rule_score_high','rule_score_medium','rule_score_low','rule_score_cap'):
        _integer(scoring[key],0,key)
    require(canonical(scoring['model_scores'])==canonical({'strong':200,'weak':100,'none':0})
        and type(scoring['prompt_hit_limit']) is int and scoring['prompt_hit_limit']==5
        and type(scoring['discard_score']) is int and scoring['discard_score']==-1000, 'changed fixed risk constants')
    identity=risk['identity']
    _keys(identity, {'max_followers_personal_verified','max_followers_unverified','official_verify_patterns'}, 'risk identity')
    for key in ('max_followers_personal_verified','max_followers_unverified'): _integer(identity[key],0,key)
    patterns=identity['official_verify_patterns']
    require(type(patterns) is list and all(type(v) is str for v in patterns), 'invalid identity patterns')
    re.compile('|'.join(patterns))


def _derive(inputs):
    cfg=inputs.configuration;state=cfg.relevance_scope_state;scope=cfg.relevance_scope
    require(state is not None and state.status=='resolved' and scope is not None, 'geographic state is not resolved')
    require(cfg.platform.value=='dy' and cfg.investigation.mode=='search', 'offline plan supports only dy + search')
    _validate_provenance(inputs)
    _validate_profiles(inputs)
    admin=inputs.p3_inputs['admin_catalog']['snapshot']
    records=validate_records(admin['records']); by_code={r['code']:r for r in records}
    target=by_code.get(state.target_code);province=by_code.get(state.province_code)
    require(target is not None and province is not None, 'unknown administrative code')
    require(target['level']=='province' or target['level']=='prefecture' and target['kind'] in {'地级市','prefecture_city'},
            'unsupported administrative level')
    require(target['province_code']==province['code'] and province['level']=='province'
            and target['level']==state.target_level and admin['version']==state.catalog_version,
            'administrative chain/version mismatch')
    for name,record in ((scope.target,target),(scope.province,province),(state.target,target),(state.province,province)):
        require(name is not None and name.name==record['name'] and name.aliases==record['aliases'], 'scope name/alias mismatch')
    require(scope.selection_policy==state.selection_policy, 'scope/state policy mismatch')
    # Execution level is supplied by the pinned P3 adapter, not inferred from names.
    context=inputs.scope_context
    _keys(context, {'schema_version','scope_origin','target_level','administrative_target','scope','policy','model','invocation_mode'}, 'P3 context')
    require(context['schema_version']=='post-only-v2' and context['scope_origin']==('confirmed_run' if isinstance(inputs, ConfirmedPlanInputs) else 'fixture')
        and context['model']==inputs.p3_inputs['model']['identifier'] and context['invocation_mode']=='single',
        'P3 context protocol/model mismatch')
    require(context['scope']=={'target':{'name':target['name'],'aliases':sorted(target['aliases'])},
        'province':{'name':province['name'],'aliases':sorted(province['aliases'])},
        'topic':None,'content_unit':'parent_post_event','selection_policy':scope.selection_policy},
        'P3 context scope differs from frozen administrative identity/policy')
    require(canonical(context['policy'])==canonical({'version':'post-only-v2',
        'ip':inputs.p3_inputs['ip_policy']['thresholds']}), 'P3 context IP policy mismatch')
    target_context=context.get('scope',{}).get('target',{})
    require(target_context.get('name')==target['name'] and context.get('target_level') in {'city','province'},
            'P3 administrative context mismatch')
    execution_level=context['target_level']
    require((execution_level=='city') == (target['level']=='prefecture' or target['kind'] in {'直辖市','municipality'}),
        'P3 execution level violates administrative identity')
    binding=context.get('administrative_target',{})
    require(binding=={'level':target['level'],'kind':target['kind'],'target_code':target['code'],
        'province_code':province['code'],'catalog_version':admin['version'],'catalog_digest':digest(admin)},
        'P3 administrative binding mismatch')
    _validate_resources(inputs)
    parameters=_effective_parameters(inputs.settings)
    resolution=inputs.resolution
    require(resolution.platform==cfg.platform and resolution.mode=='search', 'resolution platform/mode mismatch')
    require(resolution.requested_parameters==inputs.settings.saved, 'resolution did not use unified settings')
    ex=resolution.execution
    validate_execution_payload(ex.model_dump(mode='json'))
    recall=cfg.investigation.recall_plan
    selected=recall.enabled_main_terms if recall.strategy=='existing_lexicon' else recall.terms
    selected,dropped=cap_search_terms(selected,inputs.settings.limits.search_terms_max)
    content=inputs.resources.lexicon_content
    ordered=content.prioritize_search_terms(selected) if content else selected
    require(ordered==resolution.resolved_search_terms and dropped==resolution.unused_search_terms,
            'resolved search terms/order/cap differ from authority')
    require(bool(ordered), 'empty confirmed search plan')
    max_total=min(parameters.max_total_notes,parameters.max_notes*max(1,len(ordered)))
    for key,value in {**{k:getattr(parameters,k) for k in ('max_notes','max_comments','start_page','max_concurrency',
            'max_items_per_minute','get_sub_comment','collect_comments','collect_media','search_sort','analysis_batch_size')},
            'max_total_notes':max_total,'analyze_limit':max_total}.items():
        require(getattr(ex,key)==value, 'effective execution parameter mismatch: '+key)
    if recall.strategy=='existing_lexicon':
        require(resolution.recall_plan.lexicon_id==recall.lexicon_id
                and resolution.recall_plan.runtime_content_hash==recall.expected_runtime_content_hash
                and resolution.recall_plan.enabled_main_terms==ordered, 'formal recall snapshot mismatch')
    else:
        require(resolution.recall_plan.temporary_terms==ordered
                and resolution.recall_plan.source_edit_ref==recall.source_edit_ref
                and resolution.recall_plan.source_lexicon_ids==recall.source_lexicon_ids, 'temporary recall snapshot mismatch')
    queries=[]
    for order,term in enumerate(ordered):
        entries=[e for e in content._search_entries() if e.term==term] if content else []
        resource_id=recall.lexicon_id if recall.strategy=='existing_lexicon' else recall.source_edit_ref or ''
        sources=[QuerySource(resource_strategy=recall.strategy, resource_id=resource_id,
            entry_id=e.id,parent_id=e.parent_id,entry_kind=e.kind,selected_index=selected.index(term)) for e in entries]
        if not sources:
            sources=[QuerySource(resource_strategy=recall.strategy,resource_id=resource_id,
                entry_id='',parent_id='',entry_kind='confirmed_term',selected_index=selected.index(term))]
        queries.append(ExecutionQuery(query=term,source_term=term,order=order,sources=sources,per_keyword_limit=parameters.max_notes))
    lim=inputs.settings.limits
    collection=CollectionPlan(candidate_count=max(lim.triage_candidates_per_keyword,parameters.max_notes),
        candidate_comment_limit=min(lim.triage_candidate_comments,parameters.max_comments) if parameters.collect_comments else 0,
        detail_comment_limit=parameters.max_comments,collect_comments=parameters.collect_comments,
        max_total_notes=max_total,effective_parameters=parameters,triage_mode=lim.triage_mode,
        derivation_version='p1-collection-031e0cd')
    tiers=['local','province_unknown','ip_only'] if scope.selection_policy=='local_first_with_supplement' and execution_level=='city' else ['local']
    formal = isinstance(inputs, ConfirmedPlanInputs)
    policy=(ConfirmedGeographicPolicy if formal else GeographicPolicy)(version='geo-select-confirmed-v1' if formal else 'geo-select-fixture-v1',selection_policy=scope.selection_policy,tier_order=tiers,
        ip_min_suspicion='strong',status_policy='successful-no-location-only-for-ip-v1',
        selection_strategy='p4-geographic-selection' if formal else 'p4-future-geographic-selection',quota_settlement='actual-selected-query-after-detail-success')
    return queries,collection,policy


def _validate_resources(inputs):
    cfg,res,snap=inputs.configuration,inputs.resolution,inputs.resources
    rule=cfg.judgement
    require(content_hash(snap.ruleset_content)==(rule.content_hash if rule.strategy=='temporary_ruleset' else rule.expected_ruleset_content_hash),
            'ruleset content/identity mismatch')
    if rule.strategy=='temporary_ruleset':
        require(res.temporary_ruleset==rule and snap.ruleset_content==rule.content, 'adopted temporary rules differ')
        require(snap.ruleset_source==rule.model_dump(mode='json',exclude={'content'}), 'temporary source mismatch')
    else:
        require(res.ruleset_revision is not None and res.ruleset_revision.id==rule.ruleset_revision_id
                and res.ruleset_revision.version==rule.expected_ruleset_version
                and res.ruleset_revision.content_hash==rule.expected_ruleset_content_hash, 'formal ruleset identity mismatch')
        require(snap.ruleset_source==res.ruleset_revision.model_dump(mode='json'), 'formal ruleset source mismatch')
    compiled=compile_ruleset_content(snap.ruleset_content)
    for key in ('decision_rules','general_exemptions','stage_routes'):
        require(res.execution.rule_snapshot.get(key)==getattr(compiled,key), 'compiled rules differ: '+key)
    require(res.execution.prompt_profile_snapshot==compiled.prompt_profile_snapshot, 'compiled prompt differs')
    recall=cfg.investigation.recall_plan
    require(snap.recall_source==recall.model_dump(mode='json'), 'recall resource source mismatch')
    bindings=snap.adoption_bindings
    expected_kinds=(['temporary_lexicon'] if recall.strategy=='temporary_terms' and recall.source_edit_ref else [])
    if rule.strategy=='temporary_ruleset': expected_kinds.append('temporary_ruleset')
    require([b.get('kind') for b in bindings]==expected_kinds, 'missing or duplicate adoption binding')
    for binding in bindings:
        require(binding.get('principal_id')==inputs.identity.owner
            and binding.get('session_id')==inputs.identity.session_id, 'adoption owner/session mismatch')
        if binding['kind']=='temporary_lexicon':
            _keys(binding, {'kind','source_edit_ref','principal_id','session_id','edit_id','version','content_hash','source_lexicon_ids'}, 'lexicon adoption')
            _integer(binding['version'],1,'adopted lexicon version')
            require(type(binding['edit_id']) is str and bool(binding['edit_id'])
                and binding['source_edit_ref']==recall.source_edit_ref
                and snap.lexicon_content is not None
                and binding['content_hash']==digest(snap.lexicon_content.storage_dict())
                and binding['source_lexicon_ids']==recall.source_lexicon_ids, 'lexicon adoption snapshot mismatch')
        else:
            _keys(binding, {'kind','principal_id','approval_id','session_id','draft_id','draft_revision','proposal_id',
                'proposal_version','content_hash','presentation_id','approving_user_message_id','approving_user_turn_id',
                'presentation_assistant_message_id','presentation'}, 'ruleset adoption')
            _integer(binding['draft_revision'],1,'adopting draft revision')
            _integer(binding['proposal_version'],1,'adopting proposal version')
            require(all(type(binding[k]) is str and bool(binding[k]) for k in ('approval_id','presentation_id',
                'approving_user_message_id','approving_user_turn_id','presentation_assistant_message_id')), 'missing adoption identity')
            history=[r for r in inputs.provenance.history if r.draft_id==binding['draft_id']
                and r.revision==binding['draft_revision']]
            require(len(history)==1 and history[0].configuration.judgement==rule
                and binding['proposal_id']==rule.proposal_id and binding['proposal_version']==rule.proposal_version
                and binding['content_hash']==rule.content_hash, 'ruleset adoption revision mismatch')
            shown=binding['presentation']
            require(shown.get('presentation_id')==binding['presentation_id']
                and shown.get('assistant_message_id')==binding['presentation_assistant_message_id']
                and shown.get('proposal_id')==rule.proposal_id and shown.get('proposal_version')==rule.proposal_version
                and shown.get('content_hash')==rule.content_hash
                and shown.get('snapshot',{}).get('content')==rule.content.model_dump(mode='json'), 'ruleset presentation mismatch')
    if recall.strategy=='temporary_terms':
        require(snap.lexicon_content==recall.lexicon_content, 'adopted lexicon differs')
    else:
        require(snap.lexicon_content is not None, 'missing formal lexicon content')
        require(digest({'category_id':recall.lexicon_id,'enabled_main_terms':snap.lexicon_runtime_terms})
                ==recall.expected_runtime_content_hash, 'formal lexicon runtime hash mismatch')
        require(snap.lexicon_content.search_terms()==recall.enabled_main_terms, 'formal selected terms differ')


def build_scope_search_plan(authority, *, expected_identity):
    """Build from independently verified fixture authority, never from a client plan."""
    raw=authority.model_dump(mode='json') if isinstance(authority,PlanInputs) else authority
    canonical(raw)  # Reject nonfinite numbers even in nested opaque snapshots.
    inputs=PlanInputs.model_validate(raw)
    require(inputs.identity==PlanIdentity.model_validate(expected_identity), 'expected identity mismatch')
    cfg=inputs.configuration
    state=cfg.relevance_scope_state
    if cfg.relevance_scope is None and (state is None or state.status=='cleared'):
        return None
    queries,collection,policy=_derive(inputs)
    body={'schema_version':'geo-execution-plan-v1','validation_origin':'fixture',
        'identity':inputs.identity.model_dump(mode='json'),'inputs':inputs.model_dump(mode='json'),
        'configuration_digest':digest(cfg.model_dump(mode='json')),
        'input_digests':{key:digest(value) for key,value in inputs.model_dump(mode='json').items()},
        'execution_queries':[q.model_dump(mode='json') for q in queries],
        'collection':collection.model_dump(mode='json'),'policy':policy.model_dump(mode='json')}
    return ScopeSearchPlan.model_validate({**body,'plan_digest':digest(body)})


def _parse_plan(raw):
    if isinstance(raw,ScopeSearchPlan): raw=raw.model_dump(mode='json')
    if isinstance(raw,(str,bytes)):
        def unique(pairs):
            result={}
            for key,value in pairs:
                require(key not in result,'duplicate JSON key: '+key)
                result[key]=value
            return result
        raw=json.loads(raw,object_pairs_hook=unique,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    canonical(raw)
    plan=ScopeSearchPlan.model_validate(raw)
    rebuilt=build_scope_search_plan(plan.inputs,expected_identity=plan.identity.model_dump(mode='json'))
    require(rebuilt is not None and rebuilt.model_dump(mode='json')==plan.model_dump(mode='json'),
            'plan is inconsistent with its frozen inputs')
    return plan


def validate_scope_search_plan(plan, authority, *, expected_identity):
    """Recompute against external authority, including on rehashed client payloads."""
    parsed=_parse_plan(plan)
    expected=build_scope_search_plan(authority,expected_identity=expected_identity)
    require(expected is not None and parsed==expected, 'plan differs from authoritative inputs')
    return parsed


def serialize_scope_search_plan(plan):
    return canonical(_parse_plan(plan).model_dump(mode='json'))


def parse_scope_search_plan(raw, *, expected_identity, expected_digest):
    """Restore old immutable inputs without consulting today's settings/resources."""
    plan=_parse_plan(raw)
    require(plan.identity==PlanIdentity.model_validate(expected_identity), 'restored identity mismatch')
    require(plan.plan_digest==expected_digest, 'restored expected digest mismatch')
    return plan


def build_confirmed_scope_search_plan(authority, *, expected_identity):
    """Pure derivation; authorization is independently enforced at confirmation."""
    raw = authority.model_dump(mode='json') if isinstance(authority, ConfirmedPlanInputs) else authority
    canonical(raw)
    inputs = ConfirmedPlanInputs.model_validate(raw)
    require(inputs.identity == PlanIdentity.model_validate(expected_identity), 'expected identity mismatch')
    require(inputs.resolution.execution.geographic_plan is None, 'recursive geographic plan')
    require(inputs.settings.limits.triage_mode == 'select', 'geographic execution requires select')
    queries, collection, policy = _derive(inputs)
    body = dict(schema_version='geo-confirmed-execution-plan-v1', validation_origin='confirmed_run',
        identity=inputs.identity.model_dump(mode='json'), inputs=inputs.model_dump(mode='json'),
        configuration_digest=digest(inputs.configuration.model_dump(mode='json')),
        input_digests={k: digest(v) for k,v in inputs.model_dump(mode='json').items()},
        execution_queries=[q.model_dump(mode='json') for q in queries],
        collection=collection.model_dump(mode='json'), policy=policy.model_dump(mode='json'))
    return ConfirmedScopeSearchPlan.model_validate({**body, 'plan_digest': digest(body)})


def parse_confirmed_scope_search_plan(raw, *, expected_identity, expected_digest):
    canonical(raw)
    plan = ConfirmedScopeSearchPlan.model_validate(raw)
    rebuilt = build_confirmed_scope_search_plan(plan.inputs, expected_identity=expected_identity)
    require(plan == rebuilt and plan.plan_digest == expected_digest, 'confirmed plan binding mismatch')
    return plan
