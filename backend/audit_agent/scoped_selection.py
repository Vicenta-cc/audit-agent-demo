"""Pure geographic selection for explicit fixture or confirmed-run contracts.

The caller supplies the original per-query ``rank_candidates`` order. Geographic
filtering preserves that order and does not score, sort, capture or persist data.
The projection validates consistency, not the authority of a future P2 plan.
"""
from __future__ import annotations

import hashlib
import json
import re

P3_VERSION = '2120c53379d1b79c45e3cb8232b9c9b96657b183'
_HASH = re.compile(r'[0-9a-f]{64}\Z')


def digest(value):
    """Canonical JSON SHA256 shared by fixture plans and execution receipts."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _count(value):
    return type(value) is int and value >= 0


def validate_plan(plan, *, expected_execution_id=None, expected_plan_digest=None):
    """Reject malformed, changed or unsupported input with ValueError."""
    _require(isinstance(plan, dict), 'plan_not_object')
    _require(plan.get('schema_version') == 'p4-fixture-input-v1' and
             plan.get('validation_origin') == 'fixture' or
             plan.get('schema_version') == 'p4-confirmed-input-v1' and
             plan.get('validation_origin') == 'confirmed_run', 'fixture_projection_required')
    if plan.get('validation_origin') == 'confirmed_run':
        runtime = plan.get('runtime_binding')
        _require(isinstance(runtime, dict) and all(_text(runtime.get(k)) for k in ('run_id', 'job_id'))
                 and all(_hash(runtime.get(k)) for k in ('config_hash', 'p2_plan_digest')),
                 'invalid_runtime_binding')
    _require(_text(plan.get('execution_id')), 'missing_execution_id')
    _require(plan.get('platform') == 'dy' and plan.get('mode') == 'search', 'unsupported_platform_or_mode')
    if expected_execution_id is not None:
        _require(plan['execution_id'] == expected_execution_id, 'execution_binding_mismatch')
    _require(_hash(plan.get('plan_digest')), 'invalid_plan_digest')
    _require(plan['plan_digest'] == digest({k: v for k, v in plan.items() if k != 'plan_digest'}),
             'plan_digest_mismatch')
    if expected_plan_digest is not None:
        _require(plan['plan_digest'] == expected_plan_digest, 'expected_plan_digest_mismatch')
    binding = plan.get('binding')
    _require(isinstance(binding, dict) and all(_text(binding.get(k)) for k in ('owner', 'session', 'draft'))
             and _count(binding.get('revision')), 'invalid_fixture_binding')
    _require(all(_hash(plan.get(k)) for k in ('scope_digest', 'policy_digest')), 'invalid_scope_policy_digest')
    _require(plan.get('selection_policy') in ('local_only', 'local_first_with_supplement'), 'unsupported_policy')
    _require(plan.get('target_level') in ('city', 'province'), 'unsupported_target_level')
    _require(plan.get('triage_mode') == 'select', 'unsupported_triage_mode')
    _require(plan.get('p3_version') == P3_VERSION, 'p3_version_mismatch')
    versions = plan.get('versions')
    _require(isinstance(versions, dict) and all(_hash(versions.get(k)) for k in
             ('admin_catalog', 'prompt', 'model', 'risk')), 'missing_config_versions')
    queries = plan.get('queries')
    _require(isinstance(queries, list) and bool(queries), 'missing_queries')
    seen = set()
    for query in queries:
        _require(isinstance(query, dict) and _text(query.get('query_id')) and _text(query.get('query')),
                 'invalid_query')
        _require(query['query_id'] not in seen, 'duplicate_query_id')
        seen.add(query['query_id'])
        _require(_count(query.get('max_notes')) and isinstance(query.get('capture_parameters'), dict)
                 and _text(query.get('risk_input_ref')), 'invalid_query_parameters')
    _require(_count(plan.get('task_limit')), 'invalid_task_limit')


def _validate_sources(candidate, result):
    refs = result.get('evidence_refs')
    _require(isinstance(refs, list), 'missing_geo_evidence_refs')
    seen = set()
    for ref in refs:
        _require(isinstance(ref, dict) and ref.get('field') in ('title', 'desc') and _text(ref.get('place')),
                 'invalid_geo_evidence_source')
        field, place = ref['field'], ref['place']
        text = candidate.get('post', {}).get(field)
        _require(isinstance(text, str) and ref.get('quote') == text and place in text,
                 'geo_evidence_quote_mismatch')
        _require((field, place) not in seen, 'duplicate_geo_reference')
        seen.add((field, place))
        spans = ref.get('spans')
        _require(isinstance(spans, list) and bool(spans), 'missing_geo_evidence_spans')
        for span in spans:
            _require(isinstance(span, dict) and _count(span.get('start')) and _count(span.get('end')),
                     'invalid_geo_evidence_span')
            start, end = span['start'], span['end']
            _require(start < end <= len(text) and text[start:end] == place == span.get('quote'),
                     'geo_evidence_span_mismatch')
    status, tier = result.get('status'), result.get('geo_tier')
    _require(status in ('located', 'no_location', 'insufficient_context', 'conflict'), 'invalid_geo_status')
    _require(tier in ('local', 'province_unknown', 'ip_only', 'other_region', 'unresolved', 'conflict'),
             'invalid_geo_tier')
    if status == 'located':
        _require(bool(refs) and tier in ('local', 'province_unknown', 'other_region'), 'located_without_evidence')
    if status == 'no_location':
        _require(not refs and tier in ('ip_only', 'unresolved'), 'invalid_no_location')
    if tier == 'ip_only':
        _require(status == 'no_location' and result.get('ip_stats', {}).get('ip_support') is True,
                 'invalid_ip_support')
    if status == 'insufficient_context':
        _require(tier == 'unresolved', 'invalid_insufficient_tier')
    if status == 'conflict':
        _require(tier == 'conflict', 'invalid_conflict_tier')


def validate_risk_record(plan, occurrence, risk, *, risk_candidate=None):
    """Bind scoring to captured raw fields, never to P3's lossy text projection.

    P3 retains the exact original item/comments in candidate.raw_source. Its
    normalized post omits verification/follower fields, and normalized comment
    aliases differ from the risk engine's aliases. Compare the full raw objects;
    do not normalize, trim, truncate or rebuild scoring inputs here. Raw fixture
    candidates (without a normalization_version) use their own post/comments.

    Only detail review supplies a separate, already persisted risk_candidate;
    its current candidate is new geographic evidence, not new scoring input.
    """
    query = next((q for q in plan['queries'] if q['query_id'] == occurrence.get('query_id')), None)
    _require(query is not None and occurrence.get('execution_id') == plan['execution_id'] and
             occurrence.get('source_keyword') == query['query'], 'risk_capture_context_mismatch')
    candidate = occurrence.get('candidate') if risk_candidate is None else risk_candidate
    _require(isinstance(candidate, dict), 'missing_risk_capture')
    if risk_candidate is None:
        _require(digest(candidate) == occurrence.get('evidence_digest'), 'candidate_evidence_mismatch')
    _require(candidate.get('platform') == occurrence.get('platform') == plan['platform'] and
             candidate.get('content_key') == occurrence.get('content_key') and
             _text(candidate.get('post_id')), 'risk_capture_entity_mismatch')
    raw = candidate.get('raw_source') if 'normalization_version' in candidate else candidate
    _require(isinstance(raw, dict) and isinstance(raw.get('post'), dict) and
             isinstance(raw.get('comments', []), list), 'missing_raw_risk_capture')
    _require(isinstance(risk, dict) and risk.get('occurrence_id') == occurrence['occurrence_id'], 'risk_binding_mismatch')
    _require(isinstance(risk.get('input'), dict) and digest(risk['input']) == risk.get('input_digest') and
             _text(risk.get('input_ref')) and risk.get('config_digest') == plan['versions']['risk'],
             'risk_input_mismatch')
    _require(risk['input'].get('content_key') == occurrence['content_key'] and
             risk['input'].get('rank') == occurrence['rank'] and
             risk['input'].get('search_keyword') == query['query'], 'risk_input_context_mismatch')
    _require(isinstance(risk['input'].get('item'), dict) and
             risk['input']['item'].get('post_id') == candidate['post_id'], 'risk_post_identity_mismatch')
    _require(isinstance(risk['input'].get('config'), dict) and
             digest(risk['input']['config']) == risk['config_digest'], 'risk_config_digest_mismatch')
    score = risk.get('score')
    _require(isinstance(score, dict) and score.get('content_key') == occurrence['content_key'] and
             score.get('rank') == occurrence['rank'], 'risk_score_binding_mismatch')
    _require(type(score.get('score')) is int and score.get('band') in ('strong', 'weak', 'none', 'discard')
             and isinstance(score.get('reason'), str) and isinstance(score.get('hits'), list)
             and type(risk.get('model_valid')) is bool, 'invalid_risk_score')
    if risk['model_valid']:
        model = score.get('model')
        _require(isinstance(model, dict) and isinstance(model.get('suspicion'), str) and
                 model['suspicion'].strip().lower() == score['band'], 'risk_model_band_mismatch')
    # Compare both bound objects canonically, preserving JSON scalar types (True != 1).
    _require(digest({'item': risk['input']['item'], 'comments': risk['input'].get('comments')}) ==
             digest({'item': raw['post'], 'comments': raw.get('comments', [])}),
             'risk_capture_evidence_mismatch')


def _validate_occurrence(plan, query, occurrence, *, risk_candidate=None):
    _require(isinstance(occurrence, dict), 'invalid_occurrence')
    _require(all(_text(occurrence.get(k)) for k in
                 ('occurrence_id', 'platform', 'content_key', 'source_keyword')), 'missing_occurrence_identity')
    _require(occurrence.get('execution_id') == plan['execution_id'] and
             occurrence.get('query_id') == query['query_id'] and occurrence['source_keyword'] == query['query'],
             'occurrence_context_mismatch')
    _require(_count(occurrence.get('rank')), 'invalid_occurrence_rank')
    candidate = occurrence.get('candidate')
    _require(isinstance(candidate, dict) and digest(candidate) == occurrence.get('evidence_digest'),
             'candidate_evidence_mismatch')
    _require(candidate.get('platform') == occurrence['platform'] == plan['platform'] and
             candidate.get('content_key') == occurrence['content_key'] and _text(candidate.get('post_id')),
             'candidate_entity_mismatch')
    _require(isinstance(candidate.get('post'), dict) and
             candidate['post'].get('post_id') == candidate['post_id'], 'candidate_post_identity_mismatch')
    _require(type(occurrence.get('already_audited', False)) is bool, 'invalid_already_audited')
    validate_risk_record(plan, occurrence, occurrence.get('risk'), risk_candidate=risk_candidate)
    geo = occurrence.get('geo')
    _require(isinstance(geo, dict) and geo.get('p3_version') == plan['p3_version'], 'geo_version_mismatch')
    request, result = geo.get('request'), geo.get('result')
    _require(isinstance(request, dict) and digest(request) == geo.get('request_digest') and
             request.get('candidate') == candidate and isinstance(result, dict), 'geo_request_binding_mismatch')
    _require(result.get('schema_version') == 'post-only-simple-names-v2' and
             result.get('evaluation_status') == 'completed' and result.get('errors') == [], 'geo_evaluation_failed')
    for key, expected in (('scope_digest', plan['scope_digest']), ('policy_digest', plan['policy_digest']),
                          ('raw_evidence_digest', occurrence['evidence_digest']),
                          ('admin_catalog_digest', plan['versions']['admin_catalog'])):
        _require(request.get(key) == expected and result.get(key) == expected, 'geo_' + key + '_mismatch')
    _require(geo.get('scope_digest') == plan['scope_digest'] and geo.get('policy_digest') == plan['policy_digest']
             and geo.get('evidence_digest') == occurrence['evidence_digest'], 'geo_record_binding_mismatch')
    _require(_hash(request.get('model_input_digest')) and
             result.get('model_input_digest') == request['model_input_digest'], 'geo_model_input_mismatch')
    policy, context = request.get('policy'), request.get('context')
    _require(isinstance(policy, dict) and digest(policy) == plan['policy_digest'] and
             digest(policy.get('prompt_version')) == plan['versions']['prompt'] and
             result.get('prompt_version') == policy.get('prompt_version'), 'geo_prompt_config_mismatch')
    _require(isinstance(context, dict) and digest(context.get('model')) == plan['versions']['model'] and
             result.get('model') == context.get('model'), 'geo_model_config_mismatch')
    _require(context.get('target_level') == plan['target_level'] and
             isinstance(context.get('scope'), dict) and
             context['scope'].get('selection_policy') == plan['selection_policy'], 'geo_context_policy_mismatch')
    _require(all(k in context for k in ('scope_origin', 'target_level', 'scope', 'administrative_target')) and
             digest({k: context[k] for k in ('scope_origin', 'target_level', 'scope', 'administrative_target')})
             == plan['scope_digest'], 'geo_context_scope_digest_mismatch')
    _require(isinstance(request.get('prompt'), str) and
             digest({'prompt': request['prompt'], 'model': context['model']}) == request['model_input_digest'],
             'geo_prompt_input_mismatch')
    _validate_sources(candidate, result)



def eligible_tier(plan, occurrence, *, risk_candidate=None):
    """Validate a complete occurrence and return its policy/risk-eligible P3 tier.

    Detail revalidation may retain the original immutable risk input while
    replacing candidate evidence and its bound GeoRecord. It must compare the
    returned tier with the reserved tier before delivery.
    """
    validate_plan(plan)
    query = next((q for q in plan['queries'] if q['query_id'] == occurrence.get('query_id')), None)
    _require(query is not None, 'unknown_occurrence_query')
    _validate_occurrence(plan, query, occurrence, risk_candidate=risk_candidate)
    score, risk = occurrence['risk']['score'], occurrence['risk']
    tier = occurrence['geo']['result']['geo_tier']
    if occurrence.get('already_audited', False) or score['score'] <= 0 or score['band'] not in ('strong', 'weak'):
        return None
    if tier == 'local':
        return tier
    if plan['target_level'] != 'city' or plan['selection_policy'] != 'local_first_with_supplement':
        return None
    if tier == 'province_unknown':
        return tier
    if tier == 'ip_only' and risk['model_valid'] and score['band'] == 'strong':
        return tier
    return None


def _result(status, reasons, *, picks=None, query_id=None, tier=None):
    return dict(status=status, query_id=query_id, tier=tier, picks=picks or [], reasons=reasons)


def select_next_batch(plan, query_states, state):
    """Select one query and tier, or explicitly wait/block/exhaust; no mutation."""
    try:
        validate_plan(plan)
        _require(isinstance(query_states, dict) and isinstance(state, dict), 'invalid_selection_state')
        query_ids = {q['query_id'] for q in plan['queries']}
        _require(set(query_states) == query_ids, 'query_completion_set_mismatch')
        statuses = [query_states[q['query_id']].get('status') for q in plan['queries']]
        _require(all(s in ('pending', 'capturing', 'captured', 'evaluated', 'failed', 'paused') for s in statuses),
                 'invalid_query_status')
        if any(s in ('failed', 'paused') for s in statuses):
            return _result('blocked', ['query_failed_or_paused'])
        if any(s != 'evaluated' for s in statuses):
            return _result('waiting', ['all_query_evaluation_barrier'])
        seen = set()
        for query in plan['queries']:
            occurrences = query_states[query['query_id']].get('occurrences')
            _require(isinstance(occurrences, list), 'missing_occurrence_set')
            for occurrence in occurrences:
                _validate_occurrence(plan, query, occurrence)
                _require(occurrence['occurrence_id'] not in seen, 'duplicate_occurrence_id')
                seen.add(occurrence['occurrence_id'])
        _require(type(state.get('busy')) is bool, 'invalid_busy_state')
        for key in ('query_success', 'query_reserved', 'closed_queries'):
            _require(isinstance(state.get(key), dict) and set(state[key]) <= query_ids, 'invalid_' + key)
        for key in ('query_success', 'query_reserved'):
            _require(all(_count(v) for v in state[key].values()), 'invalid_' + key)
        for key in ('total_success', 'total_reserved'):
            _require(_count(state.get(key)), 'invalid_' + key)
        _require(sum(state['query_success'].values()) == state['total_success'] and
                 sum(state['query_reserved'].values()) == state['total_reserved'], 'budget_counts_mismatch')
        _require(all(isinstance(state.get(k), list) and all(_text(v) for v in state[k])
                     for k in ('occupied', 'excluded_occurrences')), 'invalid_exclusions')
        _require(all(v in ('no_refill', 'closed_media_failure', 'closed_auth_failure') for v in state['closed_queries'].values()),
                 'invalid_query_closure')
        if state['busy'] or state['total_reserved']:
            return _result('waiting', ['active_batch_or_pending_reconciliation'])
        remaining = plan['task_limit'] - state['total_success'] - state['total_reserved']
        if remaining <= 0:
            return _result('exhausted', ['task_success_limit'])
        tiers = ['local']
        if plan['target_level'] == 'city' and plan['selection_policy'] == 'local_first_with_supplement':
            tiers += ['province_unknown', 'ip_only']
        occupied, excluded = set(state['occupied']), set(state['excluded_occurrences'])
        for tier in tiers:
            for query in plan['queries']:
                qid = query['query_id']
                if qid in state['closed_queries']:
                    continue
                budget = min(remaining, query['max_notes'] - state['query_success'].get(qid, 0)
                             - state['query_reserved'].get(qid, 0))
                picks, batch_entities = [], set()
                for occurrence in query_states[qid]['occurrences']:
                    if len(picks) >= budget:
                        break
                    risk, geo = occurrence['risk'], occurrence['geo']['result']
                    entity = occurrence['platform'] + ':' + occurrence['content_key']
                    if (entity in occupied or entity in batch_entities or occurrence['occurrence_id'] in excluded
                            or occurrence.get('already_audited', False) or risk['score']['score'] <= 0 or risk['score']['band'] not in ('strong', 'weak')
                            or geo['geo_tier'] != tier):
                        continue
                    if tier == 'ip_only' and not (risk['model_valid'] and risk['score']['band'] == 'strong'):
                        continue
                    picks.append(occurrence)
                    batch_entities.add(entity)
                if picks:
                    return _result('ready', ['original_query_and_ranked_order'], picks=picks, query_id=qid, tier=tier)
        return _result('exhausted', ['no_eligible_occurrence_within_policy_and_budgets'])
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return _result('blocked', [str(exc)])
