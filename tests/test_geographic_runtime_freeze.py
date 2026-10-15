"""Formal freeze contracts; all stores use pytest temporary paths."""
import pytest
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content
from test_geographic_execution_plan import make_authority
from test_unified_draft_configuration import P


def test_capability_defaults_closed_and_rejects_foreign_database(tmp_path):
    from backend.investigation_creation.geographic_runtime import IsolatedGeographicCapability
    capability = IsolatedGeographicCapability(tmp_path)
    assert capability.allows_path(tmp_path / 'creation.sqlite3')
    assert not capability.allows_path(tmp_path.parent / 'outside.sqlite3')


def test_optional_plan_absent_from_legacy_serialization(creation_stack, content):
    from backend.investigation_creation.contracts import ResolvedExecutionConfiguration
    authority = make_authority(creation_stack, content)
    execution = ResolvedExecutionConfiguration.model_validate(authority['resolution']['execution'])
    assert 'geographic_plan' not in execution.model_dump(mode='json')


@pytest.mark.parametrize('formal_rules', [False, True])
@pytest.mark.parametrize('formal_lexicon', [False, True])
def test_formal_capture_freezes_real_authority(creation_stack, content, monkeypatch, formal_rules, formal_lexicon):
    from backend.audit_agent.config import settings
    from backend.investigation_creation.geographic_runtime import capture_confirmed_authority
    from backend.investigation_creation.scope_search_plan import build_confirmed_scope_search_plan, parse_confirmed_scope_search_plan
    monkeypatch.setattr(settings, 'triage_mode', 'select')
    authority = make_authority(creation_stack, content, formal_rules=formal_rules, formal_lexicon=formal_lexicon)
    app = creation_stack['app_service']
    draft = app.get_draft(authority['identity']['draft_id'], principal=P)
    with app.resource_service.confirmation_fence() as conn:
        frozen = capture_confirmed_authority(app, draft, principal=P, resource_connection=conn)
    plan = build_confirmed_scope_search_plan(frozen, expected_identity=authority['identity'])
    assert plan.validation_origin == 'confirmed_run'
    assert 'fixture_id' not in plan.inputs.provenance.model_dump()
    assert plan.inputs.p3_inputs['validation_origin'] == 'confirmed_run'
    assert [q.query for q in plan.execution_queries] == frozen['resolution']['resolved_search_terms']
    assert parse_confirmed_scope_search_plan(plan.model_dump(mode='json'), expected_identity=authority['identity'], expected_digest=plan.plan_digest) == plan


def _command(identity):
    from backend.investigation_creation.contracts import ConfirmAndQueueCommand
    return ConfirmAndQueueCommand(draft_id=identity['draft_id'], expected_revision=identity['revision'],
        confirmed=True, idempotency_key='runtime-confirm')


def _adapter(stack):
    from backend.investigation_creation.adapters import AuditPipelineExecutionAdapter
    from backend.audit_agent.job_store import JobStore
    from backend.audit_agent.ingestion import IngestionStore
    from backend.audit_agent.ingestion import AuditResultStore
    from backend.audit_agent.audit_policy_store import TaskAuditConfigRevisionStore
    return AuditPipelineExecutionAdapter(job_store=JobStore(stack['resource_db']),
        ingestion_store=IngestionStore(stack['resource_db']), audit_result_store=AuditResultStore(stack['resource_db']),
        revision_store=TaskAuditConfigRevisionStore(stack['resource_db']),
        crawler_account_store=stack['app_service'].resource_service.crawler_account_store,
        test_provider_validator=lambda configuration: None)


@pytest.mark.parametrize('formal_rules', [False, True])
@pytest.mark.parametrize('formal_lexicon', [False, True])
def test_confirm_job_roundtrip_and_live_drift(creation_stack, content, monkeypatch, tmp_path, formal_rules, formal_lexicon):
    from backend.audit_agent.config import settings
    from backend.investigation_creation.geographic_runtime import IsolatedGeographicCapability
    from backend.investigation_creation.errors import ConfigurationValidationError
    from backend.investigation_creation.contracts import parse_confirmed_configuration_snapshot
    monkeypatch.setattr(settings, 'triage_mode', 'select')
    authority = make_authority(creation_stack, content, formal_rules=formal_rules, formal_lexicon=formal_lexicon)
    app = creation_stack['app_service']
    command = _command(authority['identity'])
    with pytest.raises(ConfigurationValidationError, match='地域执行能力'):
        app.confirm_and_queue(command, principal=P)
    IsolatedGeographicCapability(tmp_path).install(app)
    run = app.confirm_and_queue(command, principal=P)
    assert run.confirmed_configuration['schema_version'] == 'investigation-run-config-v5'
    frozen = parse_confirmed_configuration_snapshot(run.confirmed_configuration)
    assert frozen.execution.geographic_plan['identity'] == authority['identity']
    adapter = _adapter(creation_stack)
    job_id = adapter.ensure_job(run)
    assert job_id == adapter.job_id_for_run(run.id)
    assert adapter.job_store.get(job_id)['geographic_plan'] == frozen.execution.geographic_plan
    monkeypatch.setattr(settings, 'triage_model', 'changed-live-model')
    monkeypatch.setattr(settings, 'triage_mode', 'off')
    monkeypatch.setattr(app.resource_service, 'resolve_confirmation', lambda *a, **k: pytest.fail('live resolver after freeze'))
    assert app.confirm_and_queue(command, principal=P).id == run.id
    verified = adapter.verify_run_job(run)
    assert verified['geographic_plan'] == frozen.execution.geographic_plan
    assert verified['_verified_geographic_binding'] == {**authority['identity'], 'run_id': run.id, 'job_id': job_id, 'config_hash': frozen.config_hash, 'p2_digest': frozen.execution.geographic_plan['plan_digest']}
    adapter.job_store.update(job_id, geographic_plan={**frozen.execution.geographic_plan, 'plan_digest': '0'*64})
    with pytest.raises(ValueError, match='geographic_plan'):
        adapter.verify_run_job(run)


def test_direct_store_cannot_use_installed_capability(creation_stack, content, monkeypatch, tmp_path):
    from backend.audit_agent.config import settings
    from backend.investigation_creation.geographic_runtime import IsolatedGeographicCapability
    monkeypatch.setattr(settings, 'triage_mode', 'select')
    authority = make_authority(creation_stack, content)
    app = creation_stack['app_service']
    IsolatedGeographicCapability(tmp_path).install(app)
    with pytest.raises(ValueError, match='server authority fence'):
        app.store.confirm_and_queue(authority['identity']['draft_id'], principal=P.id,
            expected_revision=authority['identity']['revision'], confirmed=True, idempotency_key='direct',
            request_fingerprint='forged', resolved_configuration=authority['resolution']['execution'],
            confirmation_resolution=authority['resolution'])
    with app.store._connect() as db:
        assert db.execute('SELECT count(*) FROM investigation_runs').fetchone()[0] == 0


def test_confirmation_rechecks_authority_and_rolls_back(creation_stack, content, monkeypatch, tmp_path):
    from backend.audit_agent.config import settings
    from backend.investigation_creation import geographic_runtime as runtime
    monkeypatch.setattr(settings, 'triage_mode', 'select')
    authority = make_authority(creation_stack, content)
    app = creation_stack['app_service']
    runtime.IsolatedGeographicCapability(tmp_path).install(app)
    capture = runtime.capture_confirmed_authority
    count = 0
    def changed(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            monkeypatch.setattr(settings, 'triage_model', 'changed-during-confirmation')
        return capture(*args, **kwargs)
    monkeypatch.setattr(runtime, 'capture_confirmed_authority', changed)
    with pytest.raises(ValueError, match='current authoritative capture'):
        app.confirm_and_queue(_command(authority['identity']), principal=P)
    assert count == 2
    with app.store._connect() as db:
        for table in ('investigation_runs', 'investigation_run_idempotency_keys', 'task_admissions'):
            assert db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0


@pytest.mark.parametrize('mode', ['off', 'compare'])
def test_isolated_capability_does_not_enable_wrong_mode(creation_stack, content, monkeypatch, tmp_path, mode):
    from backend.audit_agent.config import settings
    from backend.investigation_creation.geographic_runtime import IsolatedGeographicCapability
    from backend.investigation_creation.errors import ConfigurationValidationError
    authority = make_authority(creation_stack, content)
    app = creation_stack['app_service']
    IsolatedGeographicCapability(tmp_path).install(app)
    monkeypatch.setattr(settings, 'triage_mode', mode)
    with pytest.raises(ConfigurationValidationError, match='地域执行能力'):
        app.confirm_and_queue(_command(authority['identity']), principal=P)


def test_fixture_and_rehashed_current_resource_forgery_rejected(creation_stack, content, monkeypatch, tmp_path):
    from copy import deepcopy
    from backend.audit_agent.config import settings
    from backend.investigation_creation import geographic_runtime as runtime
    from backend.investigation_creation.scope_search_plan import build_confirmed_scope_search_plan
    monkeypatch.setattr(settings, 'triage_mode', 'select')
    authority = make_authority(creation_stack, content)
    with pytest.raises(ValueError):
        build_confirmed_scope_search_plan(authority, expected_identity=authority['identity'])
    app = creation_stack['app_service']
    runtime.IsolatedGeographicCapability(tmp_path).install(app)
    capture = runtime.capture_confirmed_authority
    count = 0
    def forged(*args, **kwargs):
        nonlocal count
        count += 1
        raw = deepcopy(capture(*args, **kwargs))
        if count == 1:
            raw['risk_inputs']['model']['name'] = 'client-invented-model'
            from backend.investigation_creation.scope_search_plan import digest
            raw['risk_inputs']['digest'] = digest({k:v for k,v in raw['risk_inputs'].items() if k != 'digest'})
        return raw
    monkeypatch.setattr(runtime, 'capture_confirmed_authority', forged)
    with pytest.raises(ValueError, match='current authoritative capture'):
        app.confirm_and_queue(_command(authority['identity']), principal=P)


def test_isolation_capability_does_not_survive_store_recreation(creation_stack, content, monkeypatch, tmp_path):
    from backend.audit_agent.config import settings
    from backend.investigation_creation.geographic_runtime import IsolatedGeographicCapability
    from backend.investigation_creation.store import InvestigationCreationStore
    from backend.investigation_creation.errors import ConfigurationValidationError
    monkeypatch.setattr(settings, 'triage_mode', 'select')
    authority = make_authority(creation_stack, content)
    app = creation_stack['app_service']
    IsolatedGeographicCapability(tmp_path).install(app)
    fresh = InvestigationCreationStore(app.store.db_path)
    with pytest.raises(ConfigurationValidationError, match='地域执行能力'):
        fresh.confirm_and_queue(authority['identity']['draft_id'], principal=P.id,
            expected_revision=authority['identity']['revision'], confirmed=True, idempotency_key='restart',
            request_fingerprint='forged', resolved_configuration=authority['resolution']['execution'],
            confirmation_resolution=authority['resolution'])


def test_runtime_fence_checks_current_claim_on_every_call(creation_stack, content, monkeypatch, tmp_path):
    from backend.audit_agent.config import settings
    from backend.investigation_creation.geographic_runtime import IsolatedGeographicCapability
    from backend.investigation_creation.errors import LeaseLostError
    monkeypatch.setattr(settings, 'triage_mode', 'select')
    authority = make_authority(creation_stack, content)
    app = creation_stack['app_service']
    IsolatedGeographicCapability(tmp_path).install(app)
    run = app.confirm_and_queue(_command(authority['identity']), principal=P)
    adapter = _adapter(creation_stack)
    adapter.creation_store = app.store
    job_id = adapter.ensure_job(run)
    claimed = app.store.claim_next('worker', task_id=run.id)
    run = app.store.bind_job(run.id, claimed.claim_token, job_id)
    class Pipeline:
        def __init__(self, **kwargs):
            pass
        def run(self, request):
            self._geographic_lease_fence()
            with self._geographic_lease_guard():
                import sqlite3
                competing = sqlite3.connect(app.store.db_path, timeout=0.01)
                try:
                    with pytest.raises(sqlite3.OperationalError, match='locked'):
                        competing.execute("UPDATE investigation_runs SET claim_token='replacement' WHERE id=?", (run.id,))
                finally:
                    competing.close()
            assert self._m3_snapshot_validator()['_verified_geographic_binding']['run_id'] == run.id
            with app.store._connect() as db:
                db.execute("UPDATE investigation_runs SET claim_token='replacement' WHERE id=?", (run.id,))
            with pytest.raises(LeaseLostError):
                self._geographic_lease_fence()
            with pytest.raises(LeaseLostError):
                with self._geographic_lease_guard():
                    pytest.fail('stale claimant entered commit section')
    adapter.pipeline_factory = Pipeline
    adapter.run_pipeline(job_id, {**run.confirmed_configuration['execution'], '_authoritative_m3_contract': True})
