"""Validation caches retain only authenticated digests, never trust claimed hashes."""
from collections import OrderedDict
from copy import deepcopy

import pytest

from backend.audit_agent import geographic_mentions as m
from backend.audit_agent.scoped_selection import digest
from test_geographic_mention_pipeline import confirmed, creation_stack, content, setup, Crawler


@pytest.fixture(autouse=True)
def isolated_validation_caches(monkeypatch):
    monkeypatch.setattr(m, '_VALIDATED_PROFILES', OrderedDict())
    monkeypatch.setattr(m, '_VALIDATED_PLANS', OrderedDict())


@pytest.fixture
def plan(confirmed, tmp_path):
    runner, _ = setup(confirmed, tmp_path, Crawler([]))
    return runner.bridge.plan


def rehash(value, key):
    value[key] = digest({k: v for k, v in value.items() if k != key})


def test_warm_profile_cache_rejects_actual_payload_tampering():
    profile = m.freeze_profile()
    m.validate_profile(profile)
    assert profile['profile_digest'] in m._VALIDATED_PROFILES
    profile['matching_records'][0]['aliases'].append('forged alias')
    with pytest.raises(ValueError, match='digest mismatch'):
        m.validate_profile(profile)


def test_warm_profile_cache_rejects_rehashed_invalid_structure():
    profile = m.freeze_profile()
    m.validate_profile(profile)
    profile['matching_records'][0]['name'] = 'forged identity'
    rehash(profile, 'profile_digest')
    with pytest.raises(ValueError):
        m.validate_profile(profile)
    assert profile['profile_digest'] not in m._VALIDATED_PROFILES


@pytest.mark.parametrize('nested', [False, True])
def test_warm_plan_cache_rejects_actual_payload_tampering(plan, nested):
    m.validate_projected_plan(plan)
    assert plan['plan_digest'] in m._VALIDATED_PLANS
    if nested:
        plan['mention_profile']['matching_records'][0]['aliases'].append('forged alias')
    else:
        plan['queries'][0]['query'] = 'forged query'
    with pytest.raises(ValueError, match='plan digest mismatch'):
        m.validate_projected_plan(plan)


def test_warm_plan_cache_rejects_rehashed_invalid_structure(plan):
    m.validate_projected_plan(plan)
    plan['queries'][0]['round_index'] = 0
    rehash(plan, 'plan_digest')
    with pytest.raises(ValueError, match='invalid mention rounds'):
        m.validate_projected_plan(plan)
    assert plan['plan_digest'] not in m._VALIDATED_PLANS


def test_warm_plan_cache_always_checks_external_expected_bindings(plan):
    m.validate_projected_plan(plan)
    for field in ('plan_digest', 'execution_id', 'binding', 'runtime_binding'):
        m.validate_projected_plan(plan, **{'expected_' + field: deepcopy(plan[field])})
        wrong = deepcopy(plan[field])
        if isinstance(wrong, dict):
            wrong[next(iter(wrong))] = 'foreign'
        else:
            wrong = 'foreign'
        with pytest.raises(ValueError, match='plan binding mismatch'):
            m.validate_projected_plan(plan, **{'expected_' + field: wrong})


def test_validation_caches_are_bounded_digest_only_lru(plan):
    m._VALIDATED_PROFILES.clear()
    m._VALIDATED_PLANS.clear()
    profiles, plans = [], []
    for index in range(18):
        profile = deepcopy(plan['mention_profile'])
        profile['county_aliases'].append('缓存测试别名' + str(index))
        rehash(profile, 'profile_digest')
        m.validate_profile(profile)
        profiles.append(profile['profile_digest'])
        changed = deepcopy(plan)
        changed['task_limit'] = 100 + index
        rehash(changed, 'plan_digest')
        m.validate_projected_plan(changed)
        plans.append(changed['plan_digest'])
    for cache in (m._VALIDATED_PROFILES, m._VALIDATED_PLANS):
        assert len(cache) == 16
        assert all(isinstance(key, str) and len(key) == 64 and value is True
                   for key, value in cache.items())
    assert profiles[0] not in m._VALIDATED_PROFILES
    assert plans[0] not in m._VALIDATED_PLANS
    assert profiles[-1] in m._VALIDATED_PROFILES
    assert plans[-1] in m._VALIDATED_PLANS
    # A hit moves the digest to the newest position without retaining its payload.
    recent = deepcopy(plan)
    recent['task_limit'] = 116
    rehash(recent, 'plan_digest')
    assert recent['plan_digest'] == plans[-2]
    m.validate_projected_plan(recent)
    assert next(reversed(m._VALIDATED_PLANS)) == recent['plan_digest']
    assert len(m._VALIDATED_PLANS) == 16
