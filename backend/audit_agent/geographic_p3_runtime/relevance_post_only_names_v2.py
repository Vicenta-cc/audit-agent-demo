"""Opt-in place spelling and semantic-role repairs; frozen v1 remains replayable.

Only a copied content catalog receives the explicit spelling overlay. Scope and
IP use the original catalog. Responses retain the existing two-field protocol.
"""
from __future__ import annotations

import copy
import json

from . import relevance as shared
from . import relevance_ip as ip
from . import relevance_post_only as source
from . import relevance_post_only_names as names
from . import relevance_post_only_simple as simple
from . import relevance_scope as scope

SCHEMA_VERSION = 'post-only-simple-names-v2'
PROMPT_VERSION = 'post-only-source-role-v1'
PLACE_ALIASES = {
    'version': 'content-place-spelling-v1',
    'origin': 'program-reviewed spelling variants; not a new administrative publication',
    'code': '650200', 'name': '克拉玛依市', 'aliases': ['克拉瑪依', '克拉瑪依市'],
}
ROLE_GUIDANCE = (
    '按语义区分人物来源与实质讨论地点：人物籍贯、身份来源本身不证明帖子讨论的事情在那里；'
    '选择帖子实质讨论的地点。比如“福建人在杭州投诉当地酒店”，福建仅表人物来源，选杭州；'
    '“福建旅游服务需要改进”实质讨论福建，可以选福建。不要按“某地人”等词形机械排除，'
    '地点也可以是被讨论的服务或对象所在区域。仍只返回原有status和locations，不增加字段。\n'
)


def _content_admin(admin_catalog, enabled):
    ip.validate_catalog(admin_catalog)
    result = copy.deepcopy(admin_catalog)
    if not enabled:
        return result
    records = result['records']
    selected = [r for r in records if r['code'] == PLACE_ALIASES['code']]
    if (len(selected) != 1 or selected[0]['name'] != PLACE_ALIASES['name']
            or selected[0]['level'] != 'prefecture' or selected[0]['province_code'] != '650000'):
        raise shared.RelevanceInputError('PLACE_ALIAS_ENTITY_MISMATCH')
    for alias in PLACE_ALIASES['aliases']:
        if any(r['code'] != PLACE_ALIASES['code'] and alias in {r['name'], *r['aliases']} for r in records):
            raise shared.RelevanceInputError('PLACE_ALIAS_COLLISION')
        if alias not in selected[0]['aliases']:
            selected[0]['aliases'].append(alias)
    result['version'] += '+' + PLACE_ALIASES['version']
    result['validation']['records_sha256'] = shared.digest(records)
    return result


def _previous(context, candidate, catalog, admin_catalog, repairs):
    # Validate scope before applying content-only aliases; they cannot widen P1/IP scope.
    context = scope.normalize_context(context, admin_catalog=admin_catalog)
    internal_context = copy.deepcopy(context)
    internal_context.pop('administrative_target', None)
    return names.prepare_request(internal_context, candidate, catalog=catalog,
                                 admin_catalog=_content_admin(admin_catalog, repairs['place_aliases']))


def prepare_request(context, candidate, *, catalog, admin_catalog,
                    use_place_aliases=True, use_role_prompt=True):
    if type(use_place_aliases) is not bool or type(use_role_prompt) is not bool:
        raise shared.RelevanceInputError('INVALID_REPAIR_SWITCHES')
    repairs = {'place_aliases': use_place_aliases, 'role_prompt': use_role_prompt}
    previous = _previous(context, candidate, catalog, admin_catalog, repairs)
    request = copy.deepcopy(previous)
    original_context = scope.normalize_context(context, admin_catalog=admin_catalog)
    request.update(schema_version=SCHEMA_VERSION, repairs=repairs, context=original_context,
                   scope_digest=shared.digest({key: original_context[key] for key in
                       ('scope_origin', 'target_level', 'scope', 'administrative_target')}),
                   admin_catalog=copy.deepcopy(admin_catalog), admin_catalog_digest=shared.digest(admin_catalog))
    request['policy'].update(version=SCHEMA_VERSION,
                             prompt_version=PROMPT_VERSION if use_role_prompt else simple.SCHEMA_VERSION,
                             admin_catalog_digest=request['admin_catalog_digest'],
                             content_admin_catalog_digest=previous['admin_catalog_digest'],
                             place_alias_overlay=copy.deepcopy(PLACE_ALIASES) if use_place_aliases else None)
    prompt = ROLE_GUIDANCE + simple.PROMPT if use_role_prompt else simple.PROMPT
    request['prompt'] = prompt + json.dumps(request['model_input'], ensure_ascii=False, separators=(',', ':'))
    request['policy_digest'] = shared.digest(request['policy'])
    request['model_input_digest'] = shared.digest({'prompt': request['prompt'], 'model': request['context']['model']})
    return request


def _bound_request(request):
    if request.get('schema_version') != SCHEMA_VERSION:
        raise shared.RelevanceResponseError('POST_ONLY_VERSION_MISMATCH')
    repairs = request.get('repairs')
    if not isinstance(repairs, dict) or set(repairs) != {'place_aliases', 'role_prompt'}:
        raise shared.RelevanceResponseError('INVALID_REPAIR_SWITCHES')
    expected = prepare_request(request['context'], request['candidate'], catalog=request['catalog'],
                               admin_catalog=request['admin_catalog'],
                               use_place_aliases=repairs['place_aliases'], use_role_prompt=repairs['role_prompt'])
    if request != expected:
        raise shared.RelevanceResponseError('POST_ONLY_REQUEST_BINDING_MISMATCH')
    return _previous(request['context'], request['candidate'], request['catalog'], request['admin_catalog'], repairs)


def validate_response(request, raw):
    result = names.validate_response(_bound_request(request), raw)
    # The inner strict consumer uses the content overlay. Replace its IP computation
    # with the original catalog and recompute its only IP-dependent tier branch.
    stats = ip.compute_ip_stats(scope.ip_context(request['context']), request['candidate'],
                                catalog=request['admin_catalog'])
    result['ip_stats'] = stats
    if result['status'] == 'no_location':
        supported = request['context']['target_level'] == 'city' and stats['ip_support']
        result.update(geo_tier='ip_only' if supported else 'unresolved',
                      basis='ip_support' if supported else 'insufficient',
                      reason_codes=['NO_CONTENT_LOCATION'] + (['IP_PROVENANCE_NOT_EVENT_LOCATION'] if supported else []))
    return result


def decision(request, raw):
    result = source._empty()
    result.update(schema_version=SCHEMA_VERSION, evaluation_status='invalid_response',
                  raw_response=copy.deepcopy(raw), name_bindings=[])
    try:
        result.update({key: request[key] for key in ('scope_digest', 'policy_digest', 'raw_evidence_digest',
                      'model_input_digest', 'catalog_digest', 'admin_catalog_digest')})
        result.update(model=request['context']['model'], scope_origin=request['context']['scope_origin'],
                      prompt_version=request['policy']['prompt_version'], name_binding=names.NAME_BINDING,
                      place_alias_overlay=request['policy']['place_alias_overlay'])
        result.update(validate_response(request, raw))
    except (shared.RelevanceInputError, shared.RelevanceResponseError, TypeError, ValueError, KeyError) as exc:
        result.update(errors=[str(exc)], reason_codes=['INVALID_MODEL_EVIDENCE'])
    return result


def evaluate_relevance(context, candidate, *, catalog, admin_catalog, client=None,
                       use_place_aliases=True, use_role_prompt=True):
    try:
        request = prepare_request(context, candidate, catalog=catalog, admin_catalog=admin_catalog,
                                  use_place_aliases=use_place_aliases, use_role_prompt=use_role_prompt)
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
