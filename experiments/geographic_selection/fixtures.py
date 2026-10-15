"""Explicit test projection; these helpers are not P2 freeze/authorization code."""
from __future__ import annotations
import copy
from .bridge import (P3_VERSION, catalogs, default_risk_config, digest, fixture_context, prepare_geo, score_risk)


def fixture_plan(execution_id='execution-fixture', queries=('A', 'B'), max_notes=1,
                 task_limit=2, target_code='650200', selection_policy='local_first_with_supplement',
                 risk_config=None):
    config = copy.deepcopy(risk_config or default_risk_config())
    context = fixture_context(target_code, selection_policy)
    probe = prepare_geo(context, {'platform': 'dy', 'content_key': 'fixture-probe',
        'post': {'post_id': 'fixture-probe', 'desc': '夹具'}}, {'status': 'no_location', 'locations': []})
    request = probe['request']
    plan = {'schema_version': 'p4-fixture-input-v1', 'validation_origin': 'fixture',
            'execution_id': execution_id, 'platform': 'dy', 'mode': 'search', 'binding': {'owner': 'fixture-owner', 'session': 'fixture-session',
              'draft': 'fixture-draft', 'revision': 1},
            'scope_digest': request['scope_digest'], 'policy_digest': request['policy_digest'],
            'selection_policy': selection_policy, 'target_level': context['target_level'],
            'p3_version': P3_VERSION, 'versions': {'admin_catalog': request['admin_catalog_digest'],
              'prompt': digest(request['policy']['prompt_version']), 'model': digest(context['model']),
              'risk': digest(config)}, 'triage_mode': 'select',
            'queries': [{'query_id': q, 'query': q, 'max_notes': max_notes,
                        'capture_parameters': {}, 'risk_input_ref': 'fixture:config:' + digest(config)} for q in queries],
            'task_limit': task_limit, 'fixture_context': context, 'fixture_risk_config': config}
    plan['plan_digest'] = digest(plan)
    return plan


def fixture_occurrence(plan, query_id, content_key, rank=1, desc='克拉玛依欠薪', raw_geo=None,
                       risk_response=None, comments=(), **item_fields):
    query = next(q for q in plan['queries'] if q['query_id'] == query_id)
    item = {'post_id': content_key, 'desc': desc, 'source_keyword': query['query'], **copy.deepcopy(item_fields)}
    candidate = {'platform': 'dy', 'content_key': content_key, 'post': copy.deepcopy(item),
                 'comments': copy.deepcopy(list(comments)), 'source_queries': [query['query']]}
    geo = prepare_geo(plan['fixture_context'], candidate, raw_geo if raw_geo is not None else
                      {'status': 'located', 'locations': [{'field': 'desc', 'place': '克拉玛依市'}]})
    occurrence_id = digest({'execution_id': plan['execution_id'], 'query_id': query_id,
                            'content_key': content_key, 'rank': rank})
    risk = score_risk(occurrence_id, content_key, rank, item, list(comments), query['query'],
                      plan['fixture_risk_config'], risk_response if risk_response is not None else
                      {'suspicion': 'weak', 'reason': '人工固定响应', 'matched_terms': [], 'locations': []})
    return {'occurrence_id': occurrence_id, 'execution_id': plan['execution_id'], 'query_id': query_id,
            'platform': 'dy', 'content_key': content_key, 'rank': rank, 'source_keyword': query['query'],
            'evidence_digest': geo['evidence_digest'], 'candidate': geo['request']['candidate'], 'risk': risk, 'geo': geo}

make_plan = fixture_plan
make_occurrence = fixture_occurrence
