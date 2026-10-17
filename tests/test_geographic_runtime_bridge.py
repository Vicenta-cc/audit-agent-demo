"""Production-origin contract changes must preserve pinned P3/P1 semantics."""
import ast
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

import pytest

from experiments.geographic_execution_plan import p3_bridge as baseline
from experiments.geographic_execution_plan.frozen_risk import build_frozen_engine as old_engine
from test_geographic_execution_plan_p3 import state, candidate, response
from test_geographic_execution_plan_risk import snapshot, ManualClient, run

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'backend/audit_agent/geographic_p3_runtime'
ORIGINAL = ROOT / 'experiments/geographic_execution_plan/dependencies/p3'


def runtime_profile():
    from backend.audit_agent.geographic_p3_runtime import freeze_p3_inputs
    return freeze_p3_inputs(model='manual-contract-comparison',
        model_config_source={'origin': 'confirmed_run', 'reference': 'server-frozen-model'})


@pytest.mark.parametrize('code,title,raw,extra', [
    ('650200', '克拉玛依市收费', response(), {}),
    ('440300', '克拉玛依市收费', response(), {}),
    ('650000', '克拉玛依市收费', response(), {}),
    ('110000', '北京市收费', response('北京市'), {}),
    ('650200', '新疆收费', response('新疆'), {}),
    ('650200', '新疆乌鲁木齐收费', {'status': 'located', 'locations': [
        {'field': 'title', 'place': '新疆'}, {'field': 'title', 'place': '乌鲁木齐'}]}, {}),
    ('440300', '收费咨询', {'status': 'no_location', 'locations': []}, {'author_ip_location': '广东'}),
    ('650200', '收费咨询', {'status': 'no_location', 'locations': []}, {'author_ip_location': '新疆'}),
    ('650200', '收费咨询', {'status': 'insufficient_context', 'locations': []}, {'author_ip_location': '新疆'}),
    ('650200', '收费咨询', {}, {'author_ip_location': '新疆'}),
])
def test_p3_production_origin_changes_only_envelope(code, title, raw, extra):
    from backend.audit_agent.geographic_p3_runtime import prepare, consume
    old_profile = baseline.freeze_p3_inputs(model='manual-contract-comparison',
        model_config_source={'origin': 'fixture', 'reference': 'comparison'})
    new_profile = runtime_profile()
    item = candidate(title, **extra)
    old = baseline.prepare(state(old_profile, code), old_profile, item)
    new = prepare(state(new_profile, code), new_profile, item)
    assert old['prompt'] == new['prompt']
    assert old['model_input'] == new['model_input']
    assert old['policy'] == new['policy']
    assert old['candidate'] == new['candidate']
    assert old['catalog'] == new['catalog'] and old['admin_catalog'] == new['admin_catalog']
    assert new['context']['scope_origin'] == 'confirmed_run'
    assert old['scope_digest'] != new['scope_digest']
    old_result, new_result = baseline.consume(old, raw), consume(new, raw)
    assert new_result['scope_origin'] == 'confirmed_run'
    assert {k: v for k, v in old_result.items() if k not in {'scope_origin', 'scope_digest'}} == {
        k: v for k, v in new_result.items() if k not in {'scope_origin', 'scope_digest'}}


def test_runtime_source_manifest_and_semantic_code_are_exact():
    from backend.audit_agent.geographic_p3_runtime import verify_dependencies
    manifest = verify_dependencies()
    assert manifest['decision_commit'] == baseline.COMMIT
    assert manifest['runtime_contract'] == 'p3-confirmed-run-v1'
    changed = {'relevance.py', 'relevance_scope.py'}
    for row in manifest['files']:
        path = RUNTIME / row['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
        if row['path'] not in changed:
            assert path.read_bytes() == (ORIGINAL / row['path']).read_bytes()
    # Formal origin literals/error labels are the only AST modifications.
    for name in changed:
        old = ast.parse((ORIGINAL / name).read_text())
        new = ast.parse((RUNTIME / name).read_text())
        for tree in (old, new):
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    node.value = node.value.replace('confirmed_run', 'fixture').replace(
                        'SCOPE_MUST_BE_CONFIRMED_RUN', 'SCOPE_MUST_BE_FIXTURE')
        assert ast.dump(old) == ast.dump(new)


def test_production_profile_rejects_fixture_origin_and_rehashed_drift():
    from backend.audit_agent.geographic_p3_runtime import (
        freeze_p3_inputs, validate_frozen_p3_inputs, derive_scope_context, shared, scope)
    with pytest.raises(ValueError):
        freeze_p3_inputs(model='m', model_config_source={'origin': 'fixture', 'reference': 'fake'})
    profile = runtime_profile()
    for key, value in [('validation_origin', 'fixture'), ('prompt_snapshot', 'changed'),
            ('use_role_prompt', False)]:
        bad = deepcopy(profile)
        bad[key] = value
        bad['profile_digest'] = shared.digest({k: v for k, v in bad.items() if k != 'profile_digest'})
        with pytest.raises(ValueError):
            validate_frozen_p3_inputs(bad)
    context = derive_scope_context(state(profile), profile)
    context['scope_origin'] = 'fixture'
    with pytest.raises(ValueError):
        scope.normalize_context(context, admin_catalog=profile['admin_catalog']['snapshot'])


def test_original_risk_inputs_requests_scores_and_sort_are_unchanged():
    from backend.audit_agent.frozen_risk import build_frozen_engine, validate_risk_inputs
    frozen = snapshot()
    first, second = ManualClient(), ManualClient()
    old = run(old_engine(frozen, first), frozen['rules'])
    new = run(build_frozen_engine(frozen, second), frozen['rules'])
    assert validate_risk_inputs(frozen) == frozen
    assert old == new and first.calls == second.calls
    assert [row['band'] for row in new[0][-3:]] == ['discard'] * 3


def test_product_modules_never_import_experiments():
    for path in [RUNTIME / '__init__.py', ROOT / 'backend/audit_agent/frozen_risk.py',
                 ROOT / 'backend/audit_agent/geographic_bridge.py', *RUNTIME.glob('*.py')]:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or '').startswith('experiments')
            elif isinstance(node, ast.Import):
                assert all(not name.name.startswith('experiments') for name in node.names)


from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content


class ResponseClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def audit_text(self, prompt, **kwargs):
        self.calls.append({'prompt': prompt, 'kwargs': kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return deepcopy(self.response)


def confirmed_bridge(stack, body, monkeypatch, tmp_path, **resources):
    from backend.audit_agent.config import settings
    from backend.audit_agent.geographic_bridge import GeographicBridge
    from backend.investigation_creation.geographic_runtime import IsolatedGeographicCapability
    from test_geographic_execution_plan import make_authority
    from test_geographic_runtime_freeze import _command, _adapter
    from test_unified_draft_configuration import P
    monkeypatch.setattr(settings, 'triage_mode', 'select')
    authority = make_authority(stack, body, **resources)
    app = stack['app_service']
    IsolatedGeographicCapability(tmp_path).install(app)
    run = app.confirm_and_queue(_command(authority['identity']), principal=P)
    executor = _adapter(stack)
    job_id = executor.ensure_job(run)
    verified = executor.verify_run_job(run)
    frozen = verified['geographic_plan']
    # Expected identity comes from the durable Run and its bound session, not the
    # payload being tested. Expected digest comes from the verified Job config.
    identity = {'owner': run.owner_principal, 'session_id': authority['identity']['session_id'],
        'draft_id': run.draft_id, 'revision': run.draft_revision}
    bindings = dict(expected_identity=identity, expected_plan_digest=frozen['plan_digest'],
        execution_id='geo:' + run.id, run_id=run.id, job_id=job_id,
        config_hash=run.confirmed_configuration['config_hash'])
    risk = ResponseClient({'suspicion': 'weak', 'reason': 'controlled risk'})
    geo = ResponseClient(response())
    bridge = GeographicBridge(frozen, **bindings, risk_client=risk, geo_client=geo)
    return bridge, risk, geo, bindings, frozen


@pytest.mark.parametrize('formal_rules', [False, True])
@pytest.mark.parametrize('formal_lexicon', [False, True])
def test_actual_confirmed_run_drives_bridge_without_risk_changes(creation_stack, content,
        monkeypatch, tmp_path, formal_rules, formal_lexicon):
    from backend.audit_agent.scoped_selection import eligible_tier, validate_risk_record, digest
    from backend.audit_agent.frozen_risk import build_frozen_engine
    from backend.audit_agent.geographic_bridge import GeographicBridge
    bridge, risk_client, geo_client, bindings, frozen = confirmed_bridge(
        creation_stack, content, monkeypatch, tmp_path,
        formal_rules=formal_rules, formal_lexicon=formal_lexicon)
    plan = bridge.plan
    query = plan['queries'][0]
    item = {'post_id': 'one', 'aweme_id': 'one', 'title': '克拉玛依市收费',
        'source_keyword': query['query'], 'custom_verify': '', 'follower_count': 1}
    comments = [{'comment_id': 'c', 'post_id': 'one', 'content': '收费咨询', 'user_id': 'commenter'}]
    occurrence = bridge.occurrence(query['query_id'], 'dy:one', 1, item, comments)
    occurrence['risk'] = bridge.risk(plan, query, occurrence)
    occurrence['geo'] = bridge.geo(plan, query, occurrence)
    assert eligible_tier(plan, occurrence) == 'local'
    assert occurrence['candidate']['raw_source']['post'] == item
    old_client = ResponseClient(risk_client.response)
    snapshot = bridge.p2.inputs.risk_inputs
    baseline_engine = build_frozen_engine(snapshot, old_client)
    original = baseline_engine.score('dy:one', 1, item, comments,
        baseline_engine.terms_for(snapshot['category_ids']), search_keyword=query['query'], rules=snapshot['rules'])
    assert occurrence['risk']['score'] == asdict(original)
    assert risk_client.calls == old_client.calls
    assert occurrence['geo']['request']['context']['scope_origin'] == 'confirmed_run'
    # Same tier detail changes preserve original risk and do not invoke its client.
    changed = deepcopy(occurrence['candidate']['raw_source'])
    changed['post']['desc'] = '精采后同档新正文'
    changed['post']['enterprise_verify_reason'] = '公安官方'
    detailed = bridge.normalize({**changed, 'platform': 'dy', 'content_key': 'dy:one'})
    review = bridge.review(plan, occurrence, detailed)
    updated = {**occurrence, 'candidate': detailed, 'evidence_digest': digest(detailed), 'geo': review}
    assert eligible_tier(plan, updated, risk_candidate=occurrence['candidate']) == 'local'
    assert len(risk_client.calls) == len(old_client.calls) == 1
    # The same stale risk is forbidden for an INITIAL current capture.
    for field in ('post', 'comments', 'identity'):
        bad = deepcopy(occurrence)
        raw = deepcopy(occurrence['candidate']['raw_source'])
        if field == 'post': raw['post']['desc'] = 'changed first capture'
        elif field == 'comments': raw['comments'][0]['content'] = 'changed comment'
        else: raw['post']['enterprise_verify_reason'] = '公安官方'
        bad['candidate'] = bridge.normalize({**raw, 'platform': 'dy', 'content_key': 'dy:one'})
        bad['evidence_digest'] = digest(bad['candidate'])
        with pytest.raises(ValueError, match='risk_capture_evidence_mismatch'):
            validate_risk_record(plan, bad, occurrence['risk'])
    # Recomputed input hash cannot substitute for independently expected bindings.
    tampered = deepcopy(frozen)
    tampered['execution_queries'][0]['query'] += 'changed'
    tampered['plan_digest'] = digest({k: v for k, v in tampered.items() if k != 'plan_digest'})
    with pytest.raises(ValueError):
        GeographicBridge(tampered, **bindings, risk_client=risk_client, geo_client=geo_client)
    for key in ('owner', 'session_id', 'draft_id', 'revision'):
        changed_bindings = deepcopy(bindings)
        changed_bindings['expected_identity'][key] = 999 if key == 'revision' else 'foreign'
        with pytest.raises(ValueError):
            GeographicBridge(frozen, **changed_bindings, risk_client=risk_client, geo_client=geo_client)


def test_provider_metadata_is_not_passed_into_strict_p3_contract(
        creation_stack, content, monkeypatch, tmp_path):
    bridge, _, geo_client, _, _ = confirmed_bridge(
        creation_stack, content, monkeypatch, tmp_path)
    geo_client.response = response() | {
        '_llm_meta': {'model': 'qwen3.6-flash', 'finish_reason': 'stop', 'total_tokens': 42}}
    plan = bridge.plan
    query = plan['queries'][0]
    item = {'post_id': 'metadata', 'aweme_id': 'metadata', 'title': '克拉玛依市收费',
        'source_keyword': query['query'], 'custom_verify': '', 'follower_count': 1}
    occurrence = bridge.occurrence(query['query_id'], 'dy:metadata', 1, item)

    record = bridge.geo(plan, query, occurrence)

    assert record['result']['geo_tier'] == 'local'
    assert '_llm_meta' not in record['result']['raw_response']
    assert record['model_meta'] == geo_client.response['_llm_meta']
    bridge.validate_geo(plan, occurrence['candidate'], record)


@pytest.mark.parametrize('entrypoint', ['geo', 'review'])
@pytest.mark.parametrize('invalid_count', [0, 1, 3])
def test_geo_retries_only_invalid_evidence_with_fixed_protocol_reminder(
        creation_stack, content, monkeypatch, tmp_path, entrypoint, invalid_count):
    from backend.audit_agent.scoped_selection import eligible_tier
    bridge, _, geo_client, _, _ = confirmed_bridge(
        creation_stack, content, monkeypatch, tmp_path)
    plan = bridge.plan
    query = plan['queries'][0]
    occurrence = bridge.occurrence(query['query_id'], 'dy:retry', 1,
        {'post_id': 'retry', 'title': '克拉玛依市收费', 'source_keyword': query['query']})
    occurrence['risk'] = bridge.risk(plan, query, occurrence)
    responses = [response('新疆克拉玛依市')] * invalid_count + [response()]
    calls = []

    def audit_text(prompt, **kwargs):
        calls.append({'prompt': prompt, 'kwargs': deepcopy(kwargs)})
        return deepcopy(responses[len(calls) - 1]) | {'_llm_meta': {'attempt': len(calls)}}

    monkeypatch.setattr(geo_client, 'audit_text', audit_text)
    record = (bridge.geo(plan, query, occurrence) if entrypoint == 'geo'
              else bridge.review(plan, occurrence, occurrence['candidate']))
    expected_count = min(invalid_count + 1, 3)
    assert len(calls) == expected_count
    invocation = bridge.p2.inputs.p3_inputs['model']
    from backend.audit_agent.geographic_response import attempt_prompt
    assert calls == [{'prompt': attempt_prompt(record['request'], record['attempts'][:index]),
        'kwargs': {'model': invocation['identifier'], **{key: invocation['invocation_config'][key]
            for key in ('max_tokens', 'enable_thinking', 'request_timeout')}}}
        for index in range(expected_count)]
    if expected_count > 1:
        assert 'LOCATION_SOURCE_MISMATCH_OR_DUPLICATE' in calls[1]['prompt']
        assert '新疆克拉玛依市' in calls[1]['prompt']
    assert record['model_meta'] == {'attempt': expected_count}
    assert len(record['attempts']) == expected_count
    for index, attempt in enumerate(record['attempts']):
        assert attempt['raw_response'] == responses[index]
        assert attempt['model_meta'] == {'attempt': index + 1}
        assert attempt['evaluation_status'] == ('invalid_response' if index < invalid_count else 'completed')
        assert bool(attempt['errors']) == (index < invalid_count)
    bridge.validate_geo(plan, occurrence['candidate'], record)
    for mutation in ('prompt', 'final', 'count'):
        bad = deepcopy(record)
        if mutation == 'prompt':
            bad['attempts'][0]['prompt'] += 'tampered'
        elif mutation == 'final':
            bad['attempts'][-1]['model_meta'] = {'tampered': True}
        else:
            bad['attempts'] *= 4
        with pytest.raises(ValueError, match='runtime_geo_'):
            bridge.validate_geo(plan, occurrence['candidate'], bad)
    occurrence['geo'] = record
    if invalid_count == 3:
        assert record['result']['evaluation_status'] == 'invalid_response'
        with pytest.raises(ValueError, match='geo_evaluation_failed'):
            eligible_tier(plan, occurrence)
    else:
        assert record['result']['evaluation_status'] == 'completed'
        assert eligible_tier(plan, occurrence) == 'local'


@pytest.mark.parametrize('entrypoint', ['geo', 'review'])
@pytest.mark.parametrize('failure', ['transport', 'consume_binding'])
def test_geo_retry_does_not_swallow_exceptions(
        creation_stack, content, monkeypatch, tmp_path, entrypoint, failure):
    from backend.audit_agent import geographic_bridge
    bridge, _, geo_client, _, _ = confirmed_bridge(
        creation_stack, content, monkeypatch, tmp_path)
    plan = bridge.plan
    query = plan['queries'][0]
    occurrence = bridge.occurrence(query['query_id'], 'dy:error', 1,
        {'post_id': 'error', 'title': '克拉玛依市收费', 'source_keyword': query['query']})
    error = RuntimeError('transport') if failure == 'transport' else ValueError('binding')
    if failure == 'transport':
        geo_client.response = error
    else:
        def fail_consume(*args):
            raise error
        monkeypatch.setattr(geographic_bridge.p3, 'consume', fail_consume)
    with pytest.raises(type(error)) as caught:
        if entrypoint == 'geo':
            bridge.geo(plan, query, occurrence)
        else:
            bridge.review(plan, occurrence, occurrence['candidate'])
    assert caught.value is error
    assert len(geo_client.calls) == 1


@pytest.mark.parametrize('title,model_place,expected', [
    ('新疆克拉玛依市收费', '新疆克拉玛依市', 'completed'),
    ('新疆维吾尔自治区克拉玛依市收费', '新疆维吾尔自治区克拉玛依市', 'completed'),
    ('浙江省杭州市收费', '浙江省杭州市', 'completed'),
    ('浙江杭州收费', '浙江杭州', 'completed'),
    ('克拉玛依市收费', '新疆克拉玛依市', 'invalid_response'),
    ('浙江克拉玛依市收费', '浙江克拉玛依市', 'invalid_response'),
    ('新疆 克拉玛依市收费', '新疆克拉玛依市', 'invalid_response'),
])
def test_geo_compound_binding_requires_exact_source_and_matching_parent(
        creation_stack, content, monkeypatch, tmp_path, title, model_place, expected):
    bridge, _, client, _, _ = confirmed_bridge(creation_stack, content, monkeypatch, tmp_path)
    plan = bridge.plan
    query = plan['queries'][0]
    occurrence = bridge.occurrence(query['query_id'], 'dy:compound', 0,
        {'post_id': 'compound', 'title': title, 'source_keyword': query['query']})
    client.response = response(model_place)
    record = bridge.geo(plan, query, occurrence)
    assert record['result']['evaluation_status'] == expected
    assert record['original_raw_response'] == response(model_place)
    assert bool(record['normalization_bindings']) == (expected == 'completed')
    assert len(client.calls) == (1 if expected == 'completed' else 3)
    bridge.validate_geo(plan, occurrence['candidate'], record)
    tampered = deepcopy(record)
    tampered['normalization_bindings'] = [{'forged': True}]
    with pytest.raises(ValueError, match='runtime_geo_result_mismatch'):
        bridge.validate_geo(plan, occurrence['candidate'], tampered)


def test_geo_attempt_budget_survives_interrupted_calls(
        creation_stack, content, monkeypatch, tmp_path):
    bridge, _, client, _, _ = confirmed_bridge(creation_stack, content, monkeypatch, tmp_path)
    plan = bridge.plan
    query = plan['queries'][0]
    occurrence = bridge.occurrence(query['query_id'], 'dy:interrupt', 0,
        {'post_id': 'interrupt', 'title': '新疆克拉玛依市收费', 'source_keyword': query['query']})
    persisted = []
    def save(attempts):
        persisted[:] = deepcopy(attempts)
    client.response = RuntimeError('offline provider interruption')
    for index in range(3):
        with pytest.raises(RuntimeError, match='offline provider interruption'):
            bridge.geo(plan, query, occurrence, attempt_state=persisted, on_attempt=save)
        assert len(persisted) == index + 1
        assert persisted[-1]['state'] == 'started'
    record = bridge.geo(plan, query, occurrence, attempt_state=persisted, on_attempt=save)
    assert len(client.calls) == 3
    assert [a['state'] for a in record['attempts']] == ['interrupted'] * 3
    assert record['result']['evaluation_status'] == 'invalid_response'
    bridge.validate_geo(plan, occurrence['candidate'], record)
    replay = bridge.geo(plan, query, occurrence, attempt_state=persisted)
    assert replay == record
    assert len(client.calls) == 3


def test_geo_completed_attempt_replays_without_model_call(
        creation_stack, content, monkeypatch, tmp_path):
    bridge, _, client, _, _ = confirmed_bridge(creation_stack, content, monkeypatch, tmp_path)
    plan = bridge.plan
    query = plan['queries'][0]
    occurrence = bridge.occurrence(query['query_id'], 'dy:cached', 0,
        {'post_id': 'cached', 'title': '新疆克拉玛依市收费', 'source_keyword': query['query']})
    client.response = response('新疆克拉玛依市')
    record = bridge.geo(plan, query, occurrence)
    assert bridge.geo(plan, query, occurrence, attempt_state=record['attempts']) == record
    assert len(client.calls) == 1


def test_geo_adapter_does_not_move_evidence_between_fields():
    from backend.audit_agent import geographic_p3_runtime as p3
    from backend.audit_agent.geographic_response import consume
    profile = runtime_profile()
    request = p3.prepare(state(profile, '650200'), profile,
                         candidate('新疆克拉玛依市收费', desc='克拉玛依市收费'))
    raw = {'status': 'located', 'locations': [{'field': 'desc', 'place': '新疆克拉玛依市'}]}
    result, bindings = consume(request, raw)
    assert result['evaluation_status'] == 'invalid_response'
    assert bindings == []


def test_geo_legacy_record_remains_replayable(creation_stack, content, monkeypatch, tmp_path):
    from backend.audit_agent.geographic_bridge import _geo_attempt_prompt
    bridge, _, client, _, _ = confirmed_bridge(creation_stack, content, monkeypatch, tmp_path)
    plan = bridge.plan
    query = plan['queries'][0]
    occurrence = bridge.occurrence(query['query_id'], 'dy:legacy', 0,
        {'post_id': 'legacy', 'title': '克拉玛依市收费', 'source_keyword': query['query']})
    record = bridge.geo(plan, query, occurrence)
    legacy = {key: value for key, value in record.items() if key not in {
        'response_adapter_version', 'original_raw_response', 'normalization_bindings'}}
    legacy['attempts'] = [{key: value for key, value in attempt.items()
                           if key not in {'state', 'normalization_bindings'}} for attempt in record['attempts']]
    legacy['attempts'][0]['prompt'] = _geo_attempt_prompt(record['request'], 0)
    bridge.validate_geo(plan, occurrence['candidate'], legacy)


def test_geo_compound_binding_is_independent_of_target_and_keeps_duplicate_protection():
    from backend.audit_agent import geographic_p3_runtime as p3
    from backend.audit_agent.geographic_response import consume
    profile = runtime_profile()
    request = p3.prepare(state(profile, '440300'), profile, candidate('新疆克拉玛依市收费'))
    result, bindings = consume(request, response('新疆克拉玛依市'))
    assert result['evaluation_status'] == 'completed'
    assert result['geo_tier'] == 'other_region'
    assert len(bindings) == 1
    duplicated = response('新疆克拉玛依市')
    duplicated['locations'].append({'field': 'title', 'place': '克拉玛依市'})
    result, _ = consume(request, duplicated)
    assert result['evaluation_status'] == 'invalid_response'
