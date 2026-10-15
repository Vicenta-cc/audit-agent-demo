"""Confirmed Run adapters to the shared P4 engine; no resource reads or clients created.

The caller obtains every expected binding from the verified Run/Job, never from
an untrusted request. This module validates integrity, not user authorization.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import re

from backend.audit_agent import geographic_p3_runtime as p3
from backend.audit_agent.frozen_risk import build_frozen_engine, validate_risk_inputs
from backend.audit_agent.scoped_selection import digest, validate_plan, validate_risk_record

VERSION = 'geographic-runtime-bridge-v1'


def risk_config(snapshot):
    risk = validate_risk_inputs(snapshot)
    model = risk['model']
    return {'category_ids': deepcopy(risk['category_ids']),
        'lexicon': {k: deepcopy(risk[k]) for k in ('terms', 'search_groups')},
        'rules': deepcopy(risk['rules']),
        'parameters': {'model': model['name'], 'max_comments': model['max_comments'],
            'request_timeout': model['request_timeout'], **deepcopy(risk['identity']),
            **{k: risk['scoring'][k] for k in
               ('rule_score_high', 'rule_score_medium', 'rule_score_low', 'rule_score_cap')}}}


class _RecordingClient:
    def __init__(self, client):
        self.client, self.calls = client, []

    def audit_text(self, prompt, **kwargs):
        self.calls.append({'prompt': prompt, 'kwargs': deepcopy(kwargs)})
        return self.client.audit_text(prompt, **kwargs)


def project_plan(plan, *, execution_id, run_id, job_id, config_hash):
    """Internal projection of a parsed confirmed plan; constructor verifies externals."""
    if plan.schema_version != 'geo-confirmed-execution-plan-v1' or plan.validation_origin != 'confirmed_run':
        raise ValueError('confirmed_geographic_plan_required')
    if any(not isinstance(value, str) or not value.strip() for value in (execution_id, run_id, job_id)):
        raise ValueError('explicit_runtime_identity_required')
    if not isinstance(config_hash, str) or re.fullmatch('[0-9a-f]{64}', config_hash) is None:
        raise ValueError('invalid_external_config_hash')
    if plan.collection.triage_mode != 'select':
        raise ValueError('unsupported_triage_mode')
    profile = plan.inputs.p3_inputs
    state = plan.inputs.configuration.relevance_scope_state.model_dump(mode='json')
    context = p3.derive_scope_context(state, profile)
    # Request metadata only; never part of candidate counts, scoring or selection.
    probe = p3.prepare(state, profile, {'platform': 'dy', 'content_key': 'metadata-probe',
        'post': {'post_id': 'metadata-probe', 'desc': '地域配置元数据'}, 'comments': []})
    config = risk_config(plan.inputs.risk_inputs)
    identity = plan.identity
    collection = plan.collection.model_dump(mode='json')
    capture = {**deepcopy(collection['effective_parameters']), **{k: collection[k] for k in
        ('candidate_count', 'candidate_comment_limit', 'detail_comment_limit', 'collect_comments')}}
    queries = [{'query_id': digest({'p2_plan_digest': plan.plan_digest, 'order': q.order}),
        'query': q.query, 'max_notes': q.per_keyword_limit, 'capture_parameters': deepcopy(capture),
        'risk_input_ref': 'confirmed:risk:' + plan.inputs.risk_inputs['digest']}
        for q in plan.execution_queries]
    result = {'schema_version': 'p4-confirmed-input-v1', 'validation_origin': 'confirmed_run',
        'execution_id': execution_id, 'platform': 'dy', 'mode': 'search',
        'binding': {'owner': identity.owner, 'session': identity.session_id,
                    'draft': identity.draft_id, 'revision': identity.revision},
        'runtime_binding': {'run_id': run_id, 'job_id': job_id, 'config_hash': config_hash,
                            'p2_plan_digest': plan.plan_digest},
        'scope_digest': probe['scope_digest'], 'policy_digest': probe['policy_digest'],
        'selection_policy': plan.policy.selection_policy, 'target_level': context['target_level'],
        'p3_version': p3.COMMIT, 'runtime_contract': p3.RUNTIME_CONTRACT,
        'versions': {'admin_catalog': probe['admin_catalog_digest'],
                     'prompt': digest(probe['policy']['prompt_version']),
                     'model': digest(context['model']), 'risk': digest(config)},
        'triage_mode': collection['triage_mode'], 'queries': queries,
        'task_limit': collection['max_total_notes'], 'scope_context': context, 'risk_config': config}
    result['plan_digest'] = digest(result)
    validate_plan(result)
    return result


class GeographicBridge:
    def __init__(self, plan, *, expected_identity, expected_plan_digest, execution_id,
                 run_id, job_id, config_hash, risk_client, geo_client):
        from backend.investigation_creation.scope_search_plan import parse_confirmed_scope_search_plan
        raw = json.loads(plan) if isinstance(plan, str) else (
            plan.model_dump(mode='json') if hasattr(plan, 'model_dump') else deepcopy(plan))
        self._p2 = parse_confirmed_scope_search_plan(raw, expected_identity=expected_identity,
                                                    expected_digest=expected_plan_digest)
        self._plan = project_plan(self._p2, execution_id=execution_id, run_id=run_id,
                                  job_id=job_id, config_hash=config_hash)
        self.risk_client, self.geo_client = risk_client, geo_client

    @property
    def plan(self):
        return deepcopy(self._plan)

    @property
    def p2(self):
        return self._p2.model_copy(deep=True)

    def _check(self, plan, query=None):
        if digest(plan) != digest(self._plan):
            raise ValueError('runtime_projection_binding_mismatch')
        if query is not None and query not in self._plan['queries']:
            raise ValueError('runtime_query_binding_mismatch')

    def normalize(self, candidate):
        return p3.shared.normalize_candidate(deepcopy(candidate))

    def occurrence(self, query_id, content_key, rank, item, comments=()):
        query = next(q for q in self._plan['queries'] if q['query_id'] == query_id)
        if type(rank) is not int or rank < 0 or not isinstance(item.get('post_id'), str):
            raise ValueError('explicit_raw_post_identity_and_rank_required')
        if item.get('source_keyword') != query['query']:
            raise ValueError('raw_source_keyword_mismatch')
        candidate = self.normalize({'platform': 'dy', 'content_key': content_key,
            'post': deepcopy(item), 'comments': deepcopy(list(comments)), 'source_queries': [query['query']]})
        execution_id = self._plan['execution_id']
        oid = digest({'execution_id': execution_id, 'query_id': query_id, 'content_key': content_key, 'rank': rank})
        return {'occurrence_id': oid, 'execution_id': execution_id, 'query_id': query_id,
            'platform': 'dy', 'content_key': content_key, 'rank': rank, 'source_keyword': query['query'],
            'candidate': candidate, 'evidence_digest': digest(candidate)}

    def risk(self, plan, query, occurrence):
        self._check(plan, query)
        if occurrence['query_id'] != query['query_id']:
            raise ValueError('runtime_occurrence_query_mismatch')
        raw = occurrence['candidate']['raw_source']
        item, comments = deepcopy(raw['post']), deepcopy(raw.get('comments', []))
        snapshot = self._p2.inputs.risk_inputs
        client = _RecordingClient(self.risk_client)
        engine = build_frozen_engine(snapshot, client)
        terms = engine.terms_for(snapshot['category_ids'])
        score = engine.score(occurrence['content_key'], occurrence['rank'], item, comments, terms,
                             search_keyword=query['query'], rules=snapshot['rules'])
        config = self._plan['risk_config']
        input_data = {'content_key': occurrence['content_key'], 'rank': occurrence['rank'],
            'item': item, 'comments': comments, 'search_keyword': query['query'], 'config': deepcopy(config)}
        valid = isinstance(score.model, dict) and isinstance(score.model.get('suspicion'), str) and (
            score.model['suspicion'].strip().lower() == score.band and score.band in {'strong', 'weak', 'none'})
        record = {'occurrence_id': occurrence['occurrence_id'], 'input': input_data,
            'input_digest': digest(input_data), 'input_ref': 'confirmed:risk:' + digest(input_data),
            'config_digest': digest(config), 'score': asdict(score), 'model_valid': valid, 'calls': client.calls}
        validate_risk_record(plan, occurrence, record)
        return record

    def _evaluate_geo(self, candidate):
        state = self._p2.inputs.configuration.relevance_scope_state.model_dump(mode='json')
        profile = self._p2.inputs.p3_inputs
        request = p3.prepare(state, profile, candidate)
        invocation = profile['model']['invocation_config']
        raw = self.geo_client.audit_text(request['prompt'], model=profile['model']['identifier'],
            **{key: invocation[key] for key in ('max_tokens', 'enable_thinking', 'request_timeout')})
        result = p3.consume(request, raw)
        record = {'evidence_digest': request['raw_evidence_digest'], 'scope_digest': request['scope_digest'],
            'policy_digest': request['policy_digest'], 'request_digest': digest(request),
            'p3_version': p3.COMMIT, 'result': result, 'request': request}
        self.validate_geo(self._plan, request['candidate'], record)
        return record

    def geo(self, plan, query, occurrence):
        self._check(plan, query)
        if occurrence['query_id'] != query['query_id']:
            raise ValueError('runtime_occurrence_query_mismatch')
        return self._evaluate_geo(occurrence['candidate'])

    def review(self, plan, occurrence, content):
        self._check(plan)
        if (content.get('content_key') != occurrence['content_key']
                or content.get('post_id') != occurrence['candidate']['post_id']):
            raise ValueError('runtime_detail_identity_mismatch')
        return self._evaluate_geo(content)

    def validate_geo(self, plan, candidate, geo):
        self._check(plan)
        validate_plan(plan)
        if candidate != self.normalize(candidate):
            raise ValueError('runtime_candidate_normalization_mismatch')
        state = self._p2.inputs.configuration.relevance_scope_state.model_dump(mode='json')
        expected_request = p3.prepare(state, self._p2.inputs.p3_inputs, candidate)
        if geo.get('request') != expected_request or geo.get('request_digest') != digest(expected_request):
            raise ValueError('runtime_geo_request_mismatch')
        result = geo.get('result')
        if not isinstance(result, dict) or p3.consume(expected_request, result.get('raw_response')) != result:
            raise ValueError('runtime_geo_result_mismatch')
        for key, expected in (('p3_version', p3.COMMIT), ('scope_digest', plan['scope_digest']),
                              ('policy_digest', plan['policy_digest']), ('evidence_digest', digest(candidate))):
            if geo.get(key) != expected:
                raise ValueError('runtime_geo_binding_mismatch:' + key)
