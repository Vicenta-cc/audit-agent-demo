"""Simple model output with program-owned administrative and IP resolution.

The existing post-only-v2 scope container is data-only. This distinct model
protocol consumes field/place references, never province relationship claims.
Historical v1/v2 modules and requests remain untouched and replayable.
"""
from __future__ import annotations

import copy
import json

from . import relevance as shared
from . import relevance_post_only as v1
from . import relevance_post_only_v2 as administrative
from . import relevance_ip as ip
from . import relevance_scope as scope_adapter

SCHEMA_VERSION = 'post-only-simple-v1'
PROMPT = v1.PROMPT.replace(
    'no_location表示成功分析原帖且确无内容地点，locations=[]。',
    'no_location表示已理解原帖对象和句意且确无内容地点，locations=[]。'
    '无法可靠理解对象或句意时用insufficient_context，不猜地点。')


def prepare_request(context, candidate, *, catalog, admin_catalog):
    """Reuse the pinned administrative input builder, not its model protocol."""
    request = administrative.prepare_request(context, candidate, catalog=catalog, admin_catalog=admin_catalog)
    request['schema_version'] = SCHEMA_VERSION
    request['policy'].update(version=SCHEMA_VERSION, prompt_version=SCHEMA_VERSION,
                             scope_schema_version=scope_adapter.SCHEMA_VERSION)
    request['prompt'] = PROMPT + json.dumps(request['model_input'], ensure_ascii=False, separators=(',', ':'))
    request['policy_digest'] = shared.digest(request['policy'])
    request['model_input_digest'] = shared.digest({'prompt': request['prompt'], 'model': request['context']['model']})
    return request


def _bound_request(request):
    if request.get('schema_version') != SCHEMA_VERSION:
        raise shared.RelevanceResponseError('POST_ONLY_VERSION_MISMATCH')
    expected = prepare_request(request['context'], request['candidate'], catalog=request['catalog'],
                               admin_catalog=request['admin_catalog'])
    if request != expected:
        raise shared.RelevanceResponseError('POST_ONLY_REQUEST_BINDING_MISMATCH')
    return v1._sources(expected['candidate'], administrative._place_catalog(expected['catalog'], expected['admin_catalog']))[1]


def validate_response(request, raw):
    index = _bound_request(request)
    original = copy.deepcopy(raw)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise shared.RelevanceResponseError('INVALID_JSON') from exc
    if not isinstance(raw, dict) or set(raw) != {'status', 'locations'}:
        raise shared.RelevanceResponseError('INVALID_POST_ONLY_RESPONSE_FIELDS')
    status, locations = raw['status'], raw['locations']
    if not isinstance(status, str) or status not in v1.STATUSES or not isinstance(locations, list):
        raise shared.RelevanceResponseError('INVALID_POST_ONLY_STATUS_OR_LOCATIONS')
    if (status == 'located' and not locations or status == 'no_location' and locations
            or status == 'conflict' and len(locations) < 2):
        raise shared.RelevanceResponseError('POST_ONLY_STATUS_EVIDENCE_MISMATCH')
    model = request['model_input']
    if status == 'no_location' and (not model['sources'] or model['source_truncated']):
        raise shared.RelevanceResponseError('NO_LOCATION_WITH_INCOMPLETE_POST')
    refs, tiers, seen = [], set(), set()
    for location in locations:
        if (not isinstance(location, dict) or set(location) != {'field', 'place'}
                or any(not isinstance(location[k], str) for k in ('field', 'place'))):
            raise shared.RelevanceResponseError('INVALID_POST_ONLY_LOCATION')
        key = (location['field'], location['place'])
        if key not in index or key in seen:
            raise shared.RelevanceResponseError('LOCATION_SOURCE_MISMATCH_OR_DUPLICATE')
        seen.add(key)
        ref = copy.deepcopy(index[key])
        if ref['ambiguous'] and status == 'located':
            raise shared.RelevanceResponseError('AMBIGUOUS_LOCATION')
        if not ref['ambiguous']:
            ref['derived_tier'] = administrative._tier(ref['entities'][0], request['context'])
            tiers.add(ref['derived_tier'])
        refs.append(ref)
    stats = ip.compute_ip_stats(scope_adapter.ip_context(request['context']), request['candidate'], catalog=request['admin_catalog'])
    tier, basis, sufficient = 'unresolved', 'insufficient', None
    if status == 'located':
        # Preserve independent province evidence. A concrete place cannot prove
        # that every other selected reference is merely its administrative parent.
        tier = next(t for t in ('local', 'province_unknown', 'other_region') if t in tiers)
        basis, sufficient, codes = 'direct_text', True, ['DIRECT_CONTENT_LOCATION']
    elif status == 'no_location':
        sufficient, codes = True, ['NO_CONTENT_LOCATION']
        if request['context']['target_level'] == 'city' and stats['ip_support']:
            tier, basis = 'ip_only', 'ip_support'
            codes.append('IP_PROVENANCE_NOT_EVENT_LOCATION')
    elif status == 'conflict':
        tier, basis, codes = 'conflict', 'conflicting', ['LOCATION_CONFLICT']
    else:
        sufficient, codes = False, ['INSUFFICIENT_POST_CONTEXT']
    return {'evaluation_status': 'completed', 'geo_tier': tier, 'basis': basis,
            'status': status, 'context_sufficient': sufficient, 'evidence_refs': refs,
            'conflict_refs': copy.deepcopy(refs) if status == 'conflict' else [],
            'ip_stats': stats, 'topic_match': 'not_requested', 'reason_codes': codes,
            'errors': [], 'raw_response': original}


def decision(request, raw):
    result = v1._empty()
    result.update(schema_version=SCHEMA_VERSION, evaluation_status='invalid_response', raw_response=copy.deepcopy(raw))
    try:
        result.update({key: request[key] for key in ('scope_digest', 'policy_digest', 'raw_evidence_digest',
                      'model_input_digest', 'catalog_digest', 'admin_catalog_digest')})
        result.update(model=request['context']['model'], scope_origin=request['context']['scope_origin'], prompt_version=SCHEMA_VERSION)
        result.update(validate_response(request, raw))
    except (shared.RelevanceInputError, shared.RelevanceResponseError, TypeError, ValueError, KeyError) as exc:
        result.update(errors=[str(exc)], reason_codes=['INVALID_MODEL_EVIDENCE'])
    return result


def evaluate_relevance(context, candidate, *, catalog, admin_catalog, client=None):
    try:
        request = prepare_request(context, candidate, catalog=catalog, admin_catalog=admin_catalog)
    except (shared.RelevanceInputError, TypeError, ValueError, KeyError) as exc:
        result = v1._empty()
        result.update(schema_version=SCHEMA_VERSION, errors=[str(exc)], reason_codes=['INVALID_INPUT'])
        return result
    if client is None:
        error = {'type': 'NO_INJECTED_CLIENT'}
    else:
        try:
            raw = client.audit_text(request['prompt'], model=request['context']['model'], max_tokens=3000,
                                    enable_thinking=False, request_timeout=60)
        except Exception as exc:
            error = v1._model_error(exc)
        else:
            return decision(request, raw)
    result = decision(request, None)
    result.update(evaluation_status='model_error', errors=[error['type']], model_error=error,
                  reason_codes=['MODEL_NOT_CALLED' if client is None else 'MODEL_CALL_FAILED'])
    return result
