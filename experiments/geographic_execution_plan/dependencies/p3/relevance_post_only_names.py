"""Versioned program-only source-name compatibility; AI wire format is unchanged.

A canonical option name may select its unique, nonambiguous, same-field source.
Evidence always contains the actual source text; original model output is retained.
Frozen simple-v1 remains the strict validator and historical replay boundary.
"""
from __future__ import annotations

import copy
import json

from . import relevance as shared
from . import relevance_post_only as source
from . import relevance_post_only_simple as simple

SCHEMA_VERSION = 'post-only-simple-names-v1'
NAME_BINDING = 'same-field-sent-canonical-unique-entity-v1'


def prepare_request(context, candidate, *, catalog, admin_catalog):
    request = simple.prepare_request(context, candidate, catalog=catalog, admin_catalog=admin_catalog)
    request['schema_version'] = SCHEMA_VERSION
    request['policy'].update(version=SCHEMA_VERSION, name_binding=NAME_BINDING)
    request['policy_digest'] = shared.digest(request['policy'])
    return request


def _bound_request(request):
    if request.get('schema_version') != SCHEMA_VERSION:
        raise shared.RelevanceResponseError('POST_ONLY_VERSION_MISMATCH')
    previous = simple.prepare_request(request['context'], request['candidate'], catalog=request['catalog'],
                                      admin_catalog=request['admin_catalog'])
    expected = copy.deepcopy(previous)
    expected['schema_version'] = SCHEMA_VERSION
    expected['policy'].update(version=SCHEMA_VERSION, name_binding=NAME_BINDING)
    expected['policy_digest'] = shared.digest(expected['policy'])
    if request != expected:
        raise shared.RelevanceResponseError('POST_ONLY_REQUEST_BINDING_MISMATCH')
    return previous, simple._bound_request(previous)


def validate_response(request, raw):
    previous, index = _bound_request(request)
    original = copy.deepcopy(raw)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError as exc:
            raise shared.RelevanceResponseError('INVALID_JSON') from exc
    # Validate shape before touching names. Never strip extra fields or add missing ones.
    if not isinstance(raw, dict) or set(raw) != {'status', 'locations'}:
        raise shared.RelevanceResponseError('INVALID_POST_ONLY_RESPONSE_FIELDS')
    if not isinstance(raw['locations'], list):
        raise shared.RelevanceResponseError('INVALID_POST_ONLY_STATUS_OR_LOCATIONS')
    normalized, bindings = copy.deepcopy(raw), []
    for location in normalized['locations']:
        if (not isinstance(location, dict) or set(location) != {'field', 'place'}
                or any(not isinstance(location[k], str) for k in ('field', 'place'))):
            raise shared.RelevanceResponseError('INVALID_POST_ONLY_LOCATION')
        field, name = location['field'], location['place']
        key = (field, name)
        if key in index:
            choices = [index[key]]  # Exact ambiguous text must stay ambiguous.
            kind = 'literal'
        else:
            # These entities' names were sent as options.name. Administrative
            # parent fields, unsent aliases, other fields and target scope are excluded.
            choices = [ref for (ref_field, _), ref in index.items()
                       if ref_field == field and not ref['ambiguous']
                       and ref['entities'][0]['name'] == name]
            if not choices or len({ref['entities'][0]['id'] for ref in choices}) != 1:
                raise shared.RelevanceResponseError('LOCATION_SOURCE_MISMATCH_OR_DUPLICATE')
            kind = 'sent_canonical'
        # Same entity can have multiple literal aliases in one field. Pick longest,
        # then lexical order, independent of target and model selection order.
        choices.sort(key=lambda ref: (-len(ref['place']), ref['place']))
        ref = choices[0]
        location['place'] = ref['place']
        bindings.append({'field': field, 'model_place': name, 'source_place': ref['place'],
                         'match': kind, 'entity_ids': [e['id'] for e in ref['entities']],
                         'candidate_sources': [r['place'] for r in choices]})
    # All prior checks (status, ambiguity, duplicates, IP and tiers) remain strict.
    result = simple.validate_response(previous, normalized)
    result.update(raw_response=original, name_bindings=bindings)
    return result


def decision(request, raw):
    result = source._empty()
    result.update(schema_version=SCHEMA_VERSION, evaluation_status='invalid_response',
                  raw_response=copy.deepcopy(raw), name_bindings=[])
    try:
        result.update({key: request[key] for key in ('scope_digest', 'policy_digest', 'raw_evidence_digest',
                      'model_input_digest', 'catalog_digest', 'admin_catalog_digest')})
        result.update(model=request['context']['model'], scope_origin=request['context']['scope_origin'],
                      prompt_version=simple.SCHEMA_VERSION, name_binding=NAME_BINDING)
        result.update(validate_response(request, raw))
    except (shared.RelevanceInputError, shared.RelevanceResponseError, TypeError, ValueError, KeyError) as exc:
        result.update(errors=[str(exc)], reason_codes=['INVALID_MODEL_EVIDENCE'])
    return result


def evaluate_relevance(context, candidate, *, catalog, admin_catalog, client=None):
    try:
        request = prepare_request(context, candidate, catalog=catalog, admin_catalog=admin_catalog)
    except (shared.RelevanceInputError, TypeError, ValueError, KeyError) as exc:
        result = source._empty()
        result.update(schema_version=SCHEMA_VERSION, errors=[str(exc)], reason_codes=['INVALID_INPUT'])
        return result
    if client is None:
        error = {'type': 'NO_INJECTED_CLIENT'}
    else:
        try:
            raw = client.audit_text(request['prompt'], model=request['context']['model'], max_tokens=3000,
                                    enable_thinking=False, request_timeout=60)
        except Exception as exc:
            error = source._model_error(exc)
        else:
            return decision(request, raw)
    result = decision(request, None)
    result.update(evaluation_status='model_error', errors=[error['type']], model_error=error,
                  reason_codes=['MODEL_NOT_CALLED' if client is None else 'MODEL_CALL_FAILED'])
    return result
