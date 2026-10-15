"""Offline P2 snapshot to P4 projection; all authority stays externally bound."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from backend.investigation_creation.scope_search_plan import (
    build_scope_search_plan, parse_scope_search_plan, serialize_scope_search_plan, validate_scope_search_plan,
)
from backend.audit_agent.scoped_selection import digest, validate_plan, validate_risk_record
from experiments.geographic_execution_plan.storage import load_plan
from experiments.geographic_execution_plan.frozen_risk import build_frozen_engine, validate_risk_inputs
from experiments.geographic_execution_plan import p3_bridge
from experiments.geographic_execution_plan.dependencies.p3.relevance import normalize_candidate
from experiments.geographic_selection.bridge import ManualRiskClient, validate_geo_record

ROOT = Path(__file__).resolve().parents[2]
INPUT_MANIFEST = Path(__file__).parent / 'artifacts/joint-20261015-01/INPUT_MANIFEST.json'
VERSION = 'p234-offline-adapter-v1'


def verify_input_manifest(manifest_path, expected_digest):
    """Read-only check of externally pinned manifest and every imported file."""
    path = Path(manifest_path)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_digest:
        raise ValueError('source_manifest_digest_mismatch')
    manifest = json.loads(raw)
    if (manifest['application_base'] != '031e0cd26379ee44c2372c9a28536c377ab3d4b9'
            or manifest['p3_commit'] != p3_bridge.COMMIT):
        raise ValueError('source_commit_mismatch')
    for row in manifest['files']:
        target = (ROOT / row['target_path']).resolve()
        if not target.is_relative_to(ROOT):
            raise ValueError('source_path_outside_checkout')
        data = target.read_bytes()
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('imported_source_drift:' + row['target_path'])
    return manifest


def _risk_config(snapshot):
    risk = validate_risk_inputs(snapshot)
    model = risk['model']
    return {'category_ids': deepcopy(risk['category_ids']),
            'lexicon': {k: deepcopy(risk[k]) for k in ('terms', 'search_groups')},
            'rules': deepcopy(risk['rules']),
            'parameters': {'model': model['name'], 'max_comments': model['max_comments'],
                'request_timeout': model['request_timeout'], **deepcopy(risk['identity']),
                **{k: risk['scoring'][k] for k in
                   ('rule_score_high', 'rule_score_medium', 'rule_score_low', 'rule_score_cap')}}}


def _projection(plan, execution_id):
    if not isinstance(execution_id, str) or not execution_id.strip():
        raise ValueError('explicit_execution_id_required')
    if plan.collection.triage_mode != 'select':
        raise ValueError('unsupported_triage_mode')
    profile = plan.inputs.p3_inputs
    if profile['use_place_aliases'] is not True or profile['use_role_prompt'] is not True:
        raise ValueError('both_p3_repairs_required')
    state = plan.inputs.configuration.relevance_scope_state.model_dump(mode='json')
    context = p3_bridge.derive_scope_context(state, profile)
    # Metadata only: this probe is never captured, scored or selected.
    probe = p3_bridge.prepare(state, profile, {'platform': 'dy', 'content_key': 'metadata-probe',
        'post': {'post_id': 'metadata-probe', 'desc': '离线配置元数据'}, 'comments': []})
    config = _risk_config(plan.inputs.risk_inputs)
    identity = plan.identity
    collection = plan.collection.model_dump(mode='json')
    effective = collection['effective_parameters']
    capture = {**deepcopy(effective), **{k: collection[k] for k in
        ('candidate_count', 'candidate_comment_limit', 'detail_comment_limit', 'collect_comments')}}
    queries = [{'query_id': digest({'p2_plan_digest': plan.plan_digest, 'order': q.order}),
                'query': q.query, 'max_notes': q.per_keyword_limit,
                'capture_parameters': deepcopy(capture),
                'risk_input_ref': 'fixture:p2-risk:' + plan.inputs.risk_inputs['digest']}
               for q in plan.execution_queries]
    result = {'schema_version': 'p4-fixture-input-v1', 'validation_origin': 'fixture',
        'execution_id': execution_id, 'platform': 'dy', 'mode': 'search',
        'binding': {'owner': identity.owner, 'session': identity.session_id,
                    'draft': identity.draft_id, 'revision': identity.revision},
        'scope_digest': probe['scope_digest'], 'policy_digest': probe['policy_digest'],
        'selection_policy': plan.policy.selection_policy, 'target_level': context['target_level'],
        'p3_version': p3_bridge.COMMIT,
        'versions': {'admin_catalog': probe['admin_catalog_digest'],
                     'prompt': digest(probe['policy']['prompt_version']),
                     'model': digest(context['model']), 'risk': digest(config)},
        'triage_mode': collection['triage_mode'], 'queries': queries,
        'task_limit': collection['max_total_notes'], 'fixture_context': context,
        'fixture_risk_config': config}
    result['plan_digest'] = digest(result)
    validate_plan(result)
    return result


def _bundle(plan, execution_id, source_manifest_digest):
    projection = _projection(plan, execution_id)
    binding = {'schema_version': 'p234-offline-binding-v1', 'adapter_version': VERSION,
        'execution_id': execution_id, 'expected_identity': plan.identity.model_dump(mode='json'),
        'p2_plan_digest': plan.plan_digest, 'p4_projection_digest': projection['plan_digest'],
        'source_manifest_digest': source_manifest_digest,
        'p2_risk_digest': plan.inputs.risk_inputs['digest'],
        'p4_risk_config_digest': projection['versions']['risk'],
        'query_sources': [{'query_id': q4['query_id'], **q2.model_dump(mode='json')}
                          for q2, q4 in zip(plan.execution_queries, projection['queries'])]}
    binding['binding_digest'] = digest(binding)
    return {'p2_serialized': serialize_scope_search_plan(plan),
            'projection': projection, 'binding': binding}


def project_plan(plan, *, plan_path, authority, expected_identity, expected_p2_digest,
                 execution_id, source_manifest_digest, manifest_path=INPUT_MANIFEST):
    verify_input_manifest(manifest_path, source_manifest_digest)
    if plan is None:
        if build_scope_search_plan(authority, expected_identity=expected_identity) is not None:
            raise ValueError('missing_scoped_plan')
        return None
    validated = validate_scope_search_plan(plan, authority, expected_identity=expected_identity)
    restored = load_plan(plan_path, expected_identity=expected_identity, expected_digest=expected_p2_digest)
    if serialize_scope_search_plan(validated) != serialize_scope_search_plan(restored):
        raise ValueError('saved_plan_authority_mismatch')
    return _bundle(restored, execution_id, source_manifest_digest)


def restore_bundle(bundle, *, expected_identity, expected_p2_digest, expected_binding_digest,
                   execution_id, source_manifest_digest, manifest_path=INPUT_MANIFEST):
    verify_input_manifest(manifest_path, source_manifest_digest)
    plan = parse_scope_search_plan(bundle['p2_serialized'], expected_identity=expected_identity,
                                   expected_digest=expected_p2_digest)
    rebuilt = _bundle(plan, execution_id, source_manifest_digest)
    if (rebuilt['binding']['binding_digest'] != expected_binding_digest
            or digest(bundle) != digest(rebuilt)):
        raise ValueError('joint_external_binding_mismatch')
    return deepcopy(rebuilt)


class BoundAdapter:
    """Verify at construction, retain private snapshots, never read live settings."""
    def __init__(self, bundle, **expected):
        self._bundle = restore_bundle(bundle, **expected)
        self._p2 = parse_scope_search_plan(self._bundle['p2_serialized'],
            expected_identity=expected['expected_identity'], expected_digest=expected['expected_p2_digest'])

    @property
    def plan(self):
        return deepcopy(self._bundle['projection'])

    @property
    def p2(self):
        return self._p2.model_copy(deep=True)

    def normalize(self, candidate):
        return normalize_candidate(deepcopy(candidate))

    def occurrence(self, query_id, content_key, rank, item, comments=()):
        plan = self.plan
        query = next(q for q in plan['queries'] if q['query_id'] == query_id)
        if type(rank) is not int or rank < 0 or not isinstance(item.get('post_id'), str):
            raise ValueError('explicit_raw_post_identity_and_rank_required')
        if item.get('source_keyword') != query['query']:
            raise ValueError('raw_source_keyword_mismatch')
        candidate = self.normalize({'platform': 'dy', 'content_key': content_key,
            'post': deepcopy(item), 'comments': deepcopy(list(comments)),
            'source_queries': [query['query']]})
        oid = digest({'execution_id': plan['execution_id'], 'query_id': query_id,
                      'content_key': content_key, 'rank': rank})
        return {'occurrence_id': oid, 'execution_id': plan['execution_id'], 'query_id': query_id,
            'platform': 'dy', 'content_key': content_key, 'rank': rank,
            'source_keyword': query['query'], 'candidate': candidate,
            'evidence_digest': digest(candidate)}

    def risk(self, occurrence, response):
        plan = self.plan
        query = next(q for q in plan['queries'] if q['query_id'] == occurrence['query_id'])
        raw = occurrence['candidate']['raw_source']
        item, comments = deepcopy(raw['post']), deepcopy(raw.get('comments', []))
        snapshot = self._p2.inputs.risk_inputs
        client = ManualRiskClient(response)
        engine = build_frozen_engine(snapshot, client)
        terms = engine.terms_for(snapshot['category_ids'])
        score = engine.score(occurrence['content_key'], occurrence['rank'], item, comments, terms,
                             search_keyword=query['query'], rules=snapshot['rules'])
        config = plan['fixture_risk_config']
        input_data = {'content_key': occurrence['content_key'], 'rank': occurrence['rank'],
            'item': item, 'comments': comments, 'search_keyword': query['query'], 'config': config}
        valid = isinstance(score.model, dict) and isinstance(score.model.get('suspicion'), str) and (
            score.model['suspicion'].strip().lower() == score.band and score.band in {'strong', 'weak', 'none'})
        record = {'occurrence_id': occurrence['occurrence_id'], 'input': input_data,
            'input_digest': digest(input_data), 'input_ref': 'fixture:risk:' + digest(input_data),
            'config_digest': digest(config), 'score': asdict(score), 'model_valid': valid,
            'manual_response': deepcopy(response) if not isinstance(response, Exception)
                               else {'error': type(response).__name__}, 'calls': client.calls}
        validate_risk_record(plan, occurrence, record)
        return record

    def geo(self, candidate, response):
        state = self._p2.inputs.configuration.relevance_scope_state.model_dump(mode='json')
        request = p3_bridge.prepare(state, self._p2.inputs.p3_inputs, candidate)
        result = p3_bridge.consume(request, response)
        record = {'evidence_digest': request['raw_evidence_digest'], 'scope_digest': request['scope_digest'],
            'policy_digest': request['policy_digest'], 'request_digest': digest(request),
            'p3_version': p3_bridge.COMMIT, 'result': result, 'request': request}
        self.validate_geo(self.plan, request['candidate'], record)
        return record

    def validate_geo(self, plan, candidate, geo):
        if digest(plan) != digest(self._bundle['projection']):
            raise ValueError('joint_projection_context_mismatch')
        validate_geo_record(plan, candidate, geo)
