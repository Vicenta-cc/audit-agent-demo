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
