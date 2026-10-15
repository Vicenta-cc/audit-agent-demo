"""Actual original P1 and pinned P3 contracts; deterministic manual responses."""
import copy
from dataclasses import asdict
import pytest
from backend.audit_agent.triage import TriageEngine, CandidateScore, rank_candidates
from experiments.geographic_selection.bridge import (
    FrozenLexicon, ManualRiskClient, default_risk_config, digest, fixture_context, prepare_geo)
from experiments.geographic_selection.fixtures import fixture_plan, fixture_occurrence


def test_original_risk_prompt_scores_and_rank_unchanged_across_scopes():
    base = fixture_plan(queries=('欠薪',))
    other = fixture_plan(queries=('欠薪',), target_code='330100')
    records = []
    for rank, key in enumerate(('x', 'y'), 1):
        response = {'suspicion': 'strong', 'risk_score': 80, 'matched_terms': ['原词'], 'locations': ['desc']}
        a = fixture_occurrence(base, '欠薪', key, rank=rank, risk_response=response)
        b = fixture_occurrence(other, '欠薪', key, rank=rank, risk_response=response)
        assert a['risk'] == b['risk']
        assert '不计分' in a['risk']['score']['reason']
        config = base['fixture_risk_config']
        client = ManualRiskClient(response)
        engine = TriageEngine(FrozenLexicon(config['lexicon']), client, **config['parameters'])
        original = engine.score(key, rank, a['risk']['input']['item'], [], engine.terms_for(config['category_ids']),
                                search_keyword='欠薪', rules=config['rules'])
        assert asdict(original) == a['risk']['score']
        assert client.calls == a['risk']['calls']
        assert a['geo']['result']['geo_tier'] == 'local'
        assert b['geo']['result']['geo_tier'] == 'other_region'
        records.append(original)
    assert [s.content_key for s in rank_candidates(records)] == ['y', 'x']


def test_original_identity_filter_and_model_failure_not_valid():
    p = fixture_plan()
    discarded = fixture_occurrence(p, 'A', 'x', enterprise_verify_reason='公安官方')
    assert discarded['risk']['score']['band'] == 'discard'
    assert discarded['risk']['calls'] == []
    failed = fixture_occurrence(p, 'A', 'y', risk_response=RuntimeError('manual failure'))
    assert failed['risk']['score']['band'] == 'weak'
    assert failed['risk']['model_valid'] is False
    strong = fixture_occurrence(p, 'A', 'z', risk_response={'suspicion': 'strong'})
    assert strong['risk']['model_valid'] and strong['risk']['score']['model_risk'] is None


def test_same_entity_query_rank_comments_keep_distinct_risk_inputs():
    p = fixture_plan()
    a = fixture_occurrence(p, 'A', 'x', rank=7)
    b = fixture_occurrence(p, 'B', 'x', rank=2, comments=[{'comment_id': 'c', 'text': '欠薪'}])
    assert a['occurrence_id'] != b['occurrence_id']
    assert a['risk']['input_digest'] != b['risk']['input_digest']
    assert a['risk']['score']['rank'] == 7
    assert b['risk']['score']['rank'] == 2


def test_comments_do_not_enter_original_p3_semantic_request():
    p = fixture_plan()
    a = fixture_occurrence(p, 'A', 'x', comments=[{'comment_id': 'c', 'text': '杭州'}])
    b = fixture_occurrence(p, 'A', 'x', comments=[{'comment_id': 'c', 'text': '克拉玛依'}])
    assert a['geo']['request']['prompt'] == b['geo']['request']['prompt']
    assert a['geo']['request']['model_input_digest'] == b['geo']['request']['model_input_digest']
    assert a['evidence_digest'] != b['evidence_digest']


@pytest.mark.parametrize(('target','text','places','tier'), [
    ('650200', '乌鲁木齐欠薪', ['乌鲁木齐'], 'other_region'),
    ('650200', '新疆欠薪', ['新疆'], 'province_unknown'),
    ('650200', '新疆乌鲁木齐欠薪', ['新疆', '乌鲁木齐'], 'province_unknown'),
    ('650000', '乌鲁木齐欠薪', ['乌鲁木齐'], 'local'),
    ('110000', '北京欠薪', ['北京'], 'local'),
])
def test_original_p3_province_city_mixed_semantics_consumed(target, text, places, tier):
    p = fixture_plan(target_code=target)
    o = fixture_occurrence(p, 'A', 'x', desc=text, raw_geo={'status': 'located', 'locations':
                            [{'field': 'desc', 'place': place} for place in places]})
    assert o['geo']['result']['evaluation_status'] == 'completed'
    assert o['geo']['result']['geo_tier'] == tier
    from audit_agent.scoped_selection import eligible_tier
    assert eligible_tier(p, o) == (None if tier == 'other_region' else tier)


def test_original_p3_ip_fallback_only_valid_no_location():
    p = fixture_plan()
    for status, expected in [('no_location', 'ip_only'), ('insufficient_context', 'unresolved')]:
        o = fixture_occurrence(p, 'A', 'x', desc='今天内容讨论', author_ip_location='新疆',
                               raw_geo={'status': status, 'locations': []})
        assert o['geo']['result']['geo_tier'] == expected
    invalid = fixture_occurrence(p, 'A', 'x', desc='讨论', author_ip_location='新疆',
        raw_geo={'status': 'located', 'locations': [{'field': 'desc', 'place': '未在来源里的地点'}]})
    assert invalid['geo']['result']['evaluation_status'] == 'invalid_response'
    assert invalid['geo']['result']['geo_tier'] != 'ip_only'


def test_pinned_snapshot_manifest_exact_bytes():
    import hashlib, json
    from pathlib import Path
    directory = Path(__file__).resolve().parents[1]/'experiments/geographic_selection'
    manifest = json.loads((directory/'dependencies/IMPORT_MANIFEST.json').read_text())
    assert len(manifest['exports']) == 11
    for entry in manifest['exports']:
        data = (directory/entry['destination']).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry['sha256']
        assert len(data) == entry['bytes']


def test_original_p3_comment_ip_thresholds_and_source_rejection():
    p = fixture_plan()
    comments = [{'comment_id': str(i), 'user_id': 'user-' + str(i), 'text': '普通评论',
                 'ip_location': '新疆'} for i in range(10)]
    raw = {'status': 'no_location', 'locations': []}
    yes = fixture_occurrence(p, 'A', 'x', desc='讨论事项', comments=comments, raw_geo=raw)
    assert yes['geo']['result']['geo_tier'] == 'ip_only'
    below = fixture_occurrence(p, 'A', 'x', desc='讨论事项', comments=comments[:9], raw_geo=raw)
    assert below['geo']['result']['geo_tier'] == 'unresolved'
    untrusted = copy.deepcopy(comments)
    for comment in untrusted:
        comment['ip_location'] = '未收录的地点'
    invalid = fixture_occurrence(p, 'A', 'x', desc='讨论事项', comments=untrusted, raw_geo=raw)
    assert invalid['geo']['result']['geo_tier'] == 'unresolved'


def test_geo_integrity_replay_rejects_modified_derived_ip_output():
    from experiments.geographic_selection.bridge import validate_geo_record
    p = fixture_plan()
    o = fixture_occurrence(p, 'A', 'x', desc='普通内容', raw_geo={'status': 'no_location', 'locations': []})
    validate_geo_record(p, o['candidate'], o['geo'])
    changed = copy.deepcopy(o['geo'])
    changed['result']['geo_tier'] = 'ip_only'
    changed['result']['basis'] = 'ip_support'
    changed['result']['ip_stats']['ip_support'] = True
    with pytest.raises(ValueError, match='geo_original_result_mismatch'):
        validate_geo_record(p, o['candidate'], changed)


def test_geo_integrity_replay_rejects_malformed_request_even_with_rehashed_digest():
    from experiments.geographic_selection.bridge import validate_geo_record
    p = fixture_plan()
    o = fixture_occurrence(p, 'A', 'x')
    changed = copy.deepcopy(o['geo'])
    changed['request']['prompt'] = 'altered request'
    changed['request']['model_input_digest'] = digest({'prompt': 'altered request',
                                                     'model': changed['request']['context']['model']})
    changed['result']['model_input_digest'] = changed['request']['model_input_digest']
    changed['request_digest'] = digest(changed['request'])
    with pytest.raises(ValueError, match='geo_original_request_mismatch'):
        validate_geo_record(p, o['candidate'], changed)
