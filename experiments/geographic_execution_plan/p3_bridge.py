"""Offline fixture bridge to byte-pinned names-v2; never production authorization.

No client, database or scheduler is constructed. The policy consumer below is a
fixture assertion aid, not the P4 allocation/collection implementation.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .dependencies.p3 import relevance as shared
from .dependencies.p3 import relevance_post_only_names_v2 as names
from .dependencies.p3 import relevance_post_only_simple as simple
from .dependencies.p3 import relevance_scope as scope

ROOT = Path(__file__).parent / 'dependencies' / 'p3'
COMMIT = '2120c53379d1b79c45e3cb8232b9c9b96657b183'
TAG = 'p3-post-only-names-v2-local-20261015'
PROFILE_SCHEMA = 'p2-p3-fixture-profile-v1'
MANIFEST_SHA256 = 'cb3a75f3255f4fc126880940ccfc717898536d2b236fc7a2f7b7020a487d6d18'
DEFAULT_IP = {'min_valid_users': 10, 'min_valid_coverage': 0.5,
              'min_target_share': 0.6, 'province_aliases': {}}


def _read(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))


def verify_dependencies():
    if hashlib.sha256((ROOT / 'import_manifest.json').read_bytes()).hexdigest() != MANIFEST_SHA256:
        raise ValueError('P3_IMPORT_MANIFEST_MISMATCH')
    manifest = _read('import_manifest.json')
    if manifest['commit'] != COMMIT or manifest['tag'] != TAG:
        raise ValueError('P3_CODE_VERSION_MISMATCH')
    for row in manifest['files']:
        path = ROOT / row['target_path'].split('dependencies/p3/', 1)[1]
        data = path.read_bytes()
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['file_bytes_sha256']:
            raise ValueError('P3_DEPENDENCY_DIGEST_MISMATCH:' + row['source_path'])
    return manifest


def _snapshot(value):
    return {'version': value['version'], 'snapshot': copy.deepcopy(value), 'digest': shared.digest(value)}


def freeze_p3_inputs(*, model: str, model_config_source: dict,
                     use_place_aliases: bool = True, use_role_prompt: bool = True,
                     ip_policy: dict | None = None) -> dict:
    """Freeze explicit fixture call configuration, immutable catalogs and code pins."""
    if type(use_place_aliases) is not bool or type(use_role_prompt) is not bool:
        raise ValueError('INVALID_REPAIR_SWITCHES')
    if not isinstance(model, str) or not model.strip():
        raise ValueError('MISSING_ACTUAL_MODEL_IDENTIFIER')
    if (not isinstance(model_config_source, dict) or model_config_source.get('origin') != 'fixture'
            or not isinstance(model_config_source.get('reference'), str) or not model_config_source['reference'].strip()):
        raise ValueError('EXPLICIT_FIXTURE_MODEL_SOURCE_REQUIRED')
    manifest = verify_dependencies()
    admin = _read('admin_catalog/geographic_catalog.json')
    catalog = _read('location_catalog.json')
    thresholds = copy.deepcopy(DEFAULT_IP if ip_policy is None else ip_policy)
    result = {'schema_version': PROFILE_SCHEMA, 'validation_origin': 'fixture',
        'code_version': {'tag': TAG, 'commit': COMMIT, 'files': manifest['files']},
        'p3_schema_version': names.SCHEMA_VERSION,
        'prompt_version': names.PROMPT_VERSION if use_role_prompt else simple.SCHEMA_VERSION,
        'prompt_snapshot': (names.ROLE_GUIDANCE if use_role_prompt else '') + simple.PROMPT,
        'model': {'identifier': model, 'config_source': copy.deepcopy(model_config_source),
                  'invocation_config': {'max_tokens': 3000, 'enable_thinking': False, 'request_timeout': 60,
                                        'invocation_mode': 'single'}},
        'use_place_aliases': use_place_aliases, 'use_role_prompt': use_role_prompt,
        'admin_catalog': _snapshot(admin), 'location_catalog': _snapshot(catalog),
        'place_alias_overlay': _snapshot(names.PLACE_ALIASES),
        'ip_policy': {'version': 'p2-comment-ip-fixture-v1', 'fixture_only': True, 'thresholds': thresholds}}
    # Let the original adapter validate thresholds, identity and scope semantics.
    _template(result)
    shared.normalize_context({'schema_version': shared.SCHEMA_VERSION, 'scope_origin': 'fixture',
        'target_level': 'province', 'scope': {'target': {'name': '新疆'}, 'province': {'name': '新疆'},
        'topic': None, 'content_unit': 'parent_post_event', 'selection_policy': 'local_only'},
        'model': model, 'invocation_mode': 'single',
        'policy': {'version': 'fixture', 'prompt_version': 'fixture', 'comment_char_budget': 1,
                  'max_comment_links': 1, 'truncation_strategy': 'whole_or_head_tail', 'ip': thresholds}})
    result['profile_digest'] = shared.digest(result)
    return result


def validate_frozen_p3_inputs(profile: dict) -> None:
    """Rebuild from exact dependencies and explicit config; reject self-rehashed drift."""
    if not isinstance(profile, dict) or profile.get('schema_version') != PROFILE_SCHEMA:
        raise ValueError('UNKNOWN_P3_PROFILE_SCHEMA')
    try:
        expected = freeze_p3_inputs(model=profile['model']['identifier'],
            model_config_source=profile['model']['config_source'],
            use_place_aliases=profile['use_place_aliases'], use_role_prompt=profile['use_role_prompt'],
            ip_policy=profile['ip_policy']['thresholds'])
        # Canonical serialization also rejects nonfinite data and bool/int substitutions.
        if json.dumps(profile, sort_keys=True, allow_nan=False) != json.dumps(expected, sort_keys=True, allow_nan=False):
            raise ValueError('P3_PROFILE_BINDING_MISMATCH')
    except (KeyError, TypeError) as exc:
        raise ValueError('INVALID_P3_PROFILE') from exc


def _template(profile):
    return {'schema_version': scope.SCHEMA_VERSION, 'scope_origin': 'fixture',
            'model': profile['model']['identifier'], 'invocation_mode': 'single',
            'policy': {'version': scope.SCHEMA_VERSION, 'ip': copy.deepcopy(profile['ip_policy']['thresholds'])}}


def derive_scope_context(scope_state: dict, frozen_p3: dict) -> dict:
    validate_frozen_p3_inputs(frozen_p3)
    return scope.context_from_scope_state(scope_state, _template(frozen_p3),
        admin_catalog=frozen_p3['admin_catalog']['snapshot'])


def prepare(scope_state: dict, frozen_p3: dict, candidate: dict) -> dict:
    context = derive_scope_context(scope_state, frozen_p3)
    return names.prepare_request(context, candidate,
        catalog=frozen_p3['location_catalog']['snapshot'], admin_catalog=frozen_p3['admin_catalog']['snapshot'],
        use_place_aliases=frozen_p3['use_place_aliases'], use_role_prompt=frozen_p3['use_role_prompt'])


def consume(request: dict, response) -> dict:
    return names.decision(request, response)


def consume_policy(decision: dict, *, target_level: str, selection_policy: str, risk: dict) -> dict:
    """Fixture eligibility only; preserves original risk and does not allocate quotas."""
    if target_level not in {'city', 'province'} or selection_policy not in {'local_only', 'local_first_with_supplement'}:
        raise ValueError('UNKNOWN_FIXTURE_SELECTION_POLICY')
    order = ['local']
    if target_level == 'city' and selection_policy == 'local_first_with_supplement':
        order += ['province_unknown', 'ip_only']
    tier = decision.get('geo_tier')
    valid_risk = isinstance(risk, dict) and risk.get('valid') is True and not risk.get('infrastructure_error')
    eligible = decision.get('evaluation_status') == 'completed' and tier in order and valid_risk
    if tier == 'ip_only':
        eligible = eligible and risk.get('strength') == 'strong'
    return {'fixture_only': True, 'production_p4': False, 'eligible': bool(eligible),
            'tier_order': order, 'priority': order.index(tier) if eligible else None}
