"""Offline-only original P1 risk and pinned P3 manual-response adapters.

All inputs are explicit fixture snapshots. No live resources, model clients, P2
imports, product stores, or private TriageEngine cache mutations are used.
"""
from __future__ import annotations
import copy
import json
from dataclasses import asdict
from pathlib import Path

from backend.audit_agent.triage import TriageEngine
from .dependencies.p3.backend.audit_agent import relevance as shared
from .dependencies.p3.backend.audit_agent import relevance_scope as scope
from .dependencies.p3.backend.audit_agent import relevance_post_only_names_v2 as names_v2

P3_VERSION = '2120c53379d1b79c45e3cb8232b9c9b96657b183'
DEPENDENCY_ROOT = Path(__file__).parent / 'dependencies/p3/experiments/geographic_relevance'
digest = shared.digest


def catalogs():
    return (json.loads((DEPENDENCY_ROOT / 'location_catalog.json').read_text()),
            json.loads((DEPENDENCY_ROOT / 'admin_catalog/geographic_catalog.json').read_text()))


class FrozenLexicon:
    def __init__(self, snapshot):
        self.snapshot = copy.deepcopy(snapshot)

    def triage_terms(self, category_ids):
        return copy.deepcopy([r for r in self.snapshot['terms'] if r['category_id'] in category_ids])

    def search_word_groups(self, category_ids):
        return copy.deepcopy(self.snapshot['search_groups'])


class ManualRiskClient:
    def __init__(self, response):
        self.response = copy.deepcopy(response)
        self.calls = []

    def audit_text(self, prompt, **kwargs):
        self.calls.append({'prompt': prompt, 'kwargs': copy.deepcopy(kwargs)})
        if isinstance(self.response, Exception):
            raise self.response
        return copy.deepcopy(self.response)


def default_risk_config():
    return {'category_ids': ['fixture'], 'lexicon': {'terms': [
        {'category_id': 'fixture', 'keyword': '欠薪', 'match_type': '模糊', 'risk_level': '高',
         'entry_kind': 'main', 'entry_group': '欠薪'}], 'search_groups': {'欠薪': '欠薪'}},
        'rules': {'audit_goal': '人工夹具风险验证', 'evidence_rules': '保留原词库及判定输入'},
        'parameters': {'model': 'manual-risk-fixture', 'max_comments': 60, 'request_timeout': 60}}


def score_risk(occurrence_id, content_key, rank, item, comments, search_keyword, config, response):
    """Let the original engine build term/group association and perform scoring."""
    frozen = copy.deepcopy(config)
    client = ManualRiskClient(response)
    engine = TriageEngine(FrozenLexicon(frozen['lexicon']), client, **frozen['parameters'])
    terms = engine.terms_for(frozen['category_ids'])
    input_data = {'content_key': content_key, 'rank': rank, 'item': copy.deepcopy(item),
                  'comments': copy.deepcopy(comments), 'search_keyword': search_keyword, 'config': frozen}
    score = engine.score(content_key, rank, item, comments, terms,
                         search_keyword=search_keyword, rules=frozen['rules'])
    valid = isinstance(score.model, dict) and isinstance(score.model.get('suspicion'), str) and (
        score.model['suspicion'].strip().lower() == score.band and score.band in {'strong', 'weak', 'none'})
    return {'occurrence_id': occurrence_id, 'input': input_data, 'input_digest': digest(input_data),
            'input_ref': 'fixture:risk:' + digest(input_data), 'config_digest': digest(frozen),
            'score': asdict(score), 'model_valid': valid, 'manual_response': copy.deepcopy(response)
            if not isinstance(response, Exception) else {'error': type(response).__name__}, 'calls': client.calls}


def fixture_context(target_code='650200', selection_policy='local_first_with_supplement'):
    _, admin = catalogs()
    indexed = {row['code']: row for row in admin['records']}
    target = indexed[target_code]
    state = {'status': 'resolved', 'target_code': target_code, 'target_level': target['level'],
             'province_code': target['province_code'], 'catalog_version': admin['version'],
             'catalog_digest': digest(admin), 'target': {'name': target['name']},
             'province': {'name': indexed[target['province_code']]['name']}, 'selection_policy': selection_policy}
    template = {'schema_version': 'post-only-v2', 'scope_origin': 'fixture',
                'model': 'manual-geography-fixture', 'invocation_mode': 'injected_client',
                'policy': {'ip': {'min_valid_users': 10, 'min_valid_coverage': .5,
                                  'min_target_share': .6, 'province_aliases': {}}}}
    return scope.context_from_scope_state(state, template, admin_catalog=admin)


def prepare_geo(context, candidate, manual_response):
    catalog, admin = catalogs()
    request = names_v2.prepare_request(context, candidate, catalog=catalog, admin_catalog=admin,
                                      use_place_aliases=True, use_role_prompt=True)
    result = names_v2.decision(request, manual_response)
    return {'evidence_digest': request['raw_evidence_digest'], 'scope_digest': request['scope_digest'],
            'policy_digest': request['policy_digest'], 'request_digest': digest(request),
            'p3_version': P3_VERSION, 'result': result, 'request': request}


def validate_geo_record(plan, candidate, geo):
    """Replay the pinned pure consumer to verify its exact derived output.

    This checks fixture integrity, not model truth or P2 authorization. The
    retained original request and manual response remain the replay boundary.
    """
    from backend.audit_agent.scoped_selection import validate_plan
    validate_plan(plan)
    try:
        if not isinstance(candidate, dict) or not isinstance(geo, dict):
            raise ValueError('geo_record_not_object')
        request, result = geo['request'], geo['result']
        if not isinstance(request, dict) or not isinstance(result, dict):
            raise ValueError('geo_request_result_not_object')
        if geo['p3_version'] != P3_VERSION or plan['p3_version'] != P3_VERSION:
            raise ValueError('geo_version_mismatch')
        if request['candidate'] != candidate or shared.normalize_candidate(candidate) != candidate:
            raise ValueError('geo_candidate_binding_mismatch')
        if geo['request_digest'] != digest(request):
            raise ValueError('geo_request_digest_mismatch')
        for key, expected in (('scope_digest', plan['scope_digest']),
                              ('policy_digest', plan['policy_digest']),
                              ('raw_evidence_digest', digest(candidate)),
                              ('admin_catalog_digest', plan['versions']['admin_catalog'])):
            if request[key] != expected or result[key] != expected:
                raise ValueError('geo_' + key + '_mismatch')
        if (geo['scope_digest'] != plan['scope_digest'] or geo['policy_digest'] != plan['policy_digest']
                or geo['evidence_digest'] != digest(candidate)):
            raise ValueError('geo_record_binding_mismatch')
        context, policy = request['context'], request['policy']
        if (context['scope_origin'] != 'fixture' or context['target_level'] != plan['target_level']
                or context['scope']['selection_policy'] != plan['selection_policy']
                or digest({k: context[k] for k in ('scope_origin', 'target_level', 'scope', 'administrative_target')})
                != plan['scope_digest'] or digest(policy) != plan['policy_digest']
                or digest(policy['prompt_version']) != plan['versions']['prompt']
                or digest(context['model']) != plan['versions']['model']):
            raise ValueError('geo_context_config_mismatch')
        catalog, admin = catalogs()
        if request['catalog'] != catalog or request['admin_catalog'] != admin:
            raise ValueError('geo_pinned_catalog_mismatch')
        if request['repairs'] != {'place_aliases': True, 'role_prompt': True}:
            raise ValueError('geo_repair_configuration_mismatch')
        original = names_v2.prepare_request(context, candidate, catalog=catalog, admin_catalog=admin,
                                           use_place_aliases=True, use_role_prompt=True)
        if request != original:
            raise ValueError('geo_original_request_mismatch')
        if names_v2.decision(request, result['raw_response']) != result:
            raise ValueError('geo_original_result_mismatch')
    except (KeyError, TypeError) as exc:
        raise ValueError('malformed_geo_record') from exc
