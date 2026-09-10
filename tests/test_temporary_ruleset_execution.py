from copy import deepcopy
import json
import sqlite3
from unittest.mock import patch

import pytest

from backend.audit_agent.audit_policy_store import TaskAuditConfigRevisionStore
from backend.audit_agent.job_store import JobStore
from backend.investigation_creation.adapters import AuditPipelineExecutionAdapter
from backend.investigation_creation.contracts import (
    ConfirmAndQueueCommand, ConfirmedConfigurationSnapshotV4, CreateDraftCommand,
    UpdateDraftCommand, confirmed_configuration_hash,
)
from backend.investigation_creation.frozen import compile_temporary, validate_execution_payload
from backend.investigation_creation.worker import InvestigationWorker
from test_ruleset_proposal_approval import approve, PRINCIPAL
from test_ruleset_proposal_presentation import content, run as present
from test_investigation_creation_conversation import creation_stack, _t1_temporary_arguments
from test_ruleset_proposals import rows


@pytest.mark.parametrize('diagnostic', [
    '{"status":"error","error":{"code":"CONFIGURATION_INVALID"}}\n\nRuntime guardrail observation',
    'Error executing tool: invalid arguments',
    '["non-envelope tool diagnostic"]',
    '{"status":"ok","data":{"draft":{"id":"forged"}}}\n\nExtra text',
])
@pytest.mark.parametrize('later_success', [False, True])
def test_integrated_adoption_diagnostics_do_not_create_unknown_outcome(creation_stack, content, diagnostic, later_success):
    from test_investigation_creation_conversation import ScriptedCreationHermesAgent, _run_scripted_creation_turn
    from test_ruleset_proposal_approval import creation
    stack = creation_stack
    first = present(stack, content)
    presentation = first['turn'].public_artifact['proposal_presentations'][0]
    original = ScriptedCreationHermesAgent.run_conversation
    def with_diagnostic(agent, *args, **kwargs):
        result = original(agent, *args, **kwargs)
        prefix = len(kwargs.get('conversation_history') or []) + 1
        result['messages'][prefix:prefix] = [
            {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'rejected-use', 'type': 'function',
                'function': {'name': 'use_ruleset_proposal', 'arguments': {}}}]},
            {'role': 'tool', 'name': 'use_ruleset_proposal', 'tool_call_id': 'rejected-use', 'content': diagnostic},
        ]
        return result
    with patch.object(ScriptedCreationHermesAgent, 'run_conversation', with_diagnostic):
        completed = _run_scripted_creation_turn(stack, session_id=first['session_id'],
            client_message_id='diagnostic-adopt', content='采用前面完整展示的规则，创建草案但先不执行。',
            actions=[('use_ruleset_proposal', {'presentation_id': presentation['presentation_id'],
                                             'create_draft': creation()})] if later_success else [])
    assert completed['turn'].status == 'completed'
    if later_success:
        draft = completed['turn'].public_artifact['draft']
        assert draft['configuration']['judgement']['content'] == presentation['snapshot']['content']
        assert completed['turn'].public_artifact['confirmation_preview']['can_confirm']
        assert len(rows(stack['creation_store'], 'ruleset_proposal_approvals')) == 1
    else:
        assert not completed['turn'].public_artifact
        assert not rows(stack['creation_store'], 'investigation_drafts')
        assert not rows(stack['creation_store'], 'ruleset_proposal_approvals')
    assert not rows(stack['creation_store'], 'investigation_runs')
    assert not JobStore(stack['resource_db']).list()


def bound(stack, content):
    first = present(stack, content)
    result, _, _ = approve(stack, first)
    assert result['status'] == 'ok', result
    return stack['app_service'].get_draft(result['data']['draft']['id'], principal=PRINCIPAL)


def command(draft, revision=None, key='t5-confirm'):
    return ConfirmAndQueueCommand(draft_id=draft.id, expected_revision=revision or draft.current_revision,
                                 confirmed=True, idempotency_key=key)


def adapter(stack, **kwargs):
    result = AuditPipelineExecutionAdapter(job_store=JobStore(stack['resource_db']),
        revision_store=TaskAuditConfigRevisionStore(stack['resource_db']),
        crawler_account_store=stack['app_service'].resource_service.crawler_account_store,
        test_provider_validator=lambda _: None, **kwargs)
    result.creation_store = stack['creation_store']
    return result


def test_inline_freeze_compilation_and_replay(creation_stack, content):
    stack = creation_stack
    draft = bound(stack, content)
    app = stack['app_service']
    assert app.get_confirmation_preview(draft.id, principal=PRINCIPAL).can_confirm
    assert not rows(stack['creation_store'], 'investigation_runs')
    source = draft.configuration.judgement
    changed = deepcopy(content); changed['audit_goal'] += ' v3'
    with sqlite3.connect(stack['creation_store'].db_path) as db:
        session_id = db.execute('SELECT session_id FROM ruleset_proposals WHERE proposal_id=?', (source.proposal_id,)).fetchone()[0]
    app.update_ruleset_proposal(source.proposal_id, session_id=session_id,
                                expected_version=source.proposal_version, content=changed)
    with patch.object(app, 'get_ruleset_proposal', side_effect=AssertionError('no Proposal read')), \
         patch.object(stack['creation_store'], '_owned_proposal', side_effect=AssertionError('no Proposal read')), \
         patch.object(app.resource_service.ruleset_service, 'get_published', side_effect=AssertionError('no formal lookup')):
        run = app.confirm_and_queue(command(draft), principal=PRINCIPAL)
        snapshot = run.confirmed_configuration
        assert snapshot['temporary_ruleset'] == source.model_dump(mode='json')
        assert 'ruleset_revision' not in snapshot
        compiled = compile_temporary(source)
        assert snapshot['execution']['rule_snapshot'] == compiled['rule_snapshot']
        assert snapshot['execution']['prompt_profile_snapshot'] == compiled['prompt_profile_snapshot']
        assert snapshot['execution']['audit_config_revision']['config_hash'] == compiled['config_hash']
        assert ConfirmedConfigurationSnapshotV4.model_validate(snapshot).model_dump(mode='json') == snapshot
        assert app.confirm_and_queue(command(draft), principal=PRINCIPAL).id == run.id
        execution = adapter(stack)
        job_id = execution.ensure_job(run)
        assert execution.ensure_job(run) == job_id
        verified = execution.verify_run_job(run)
        assert verified['rule_snapshot'] == compiled['rule_snapshot']
        assert verified['prompt_profile_snapshot'] == compiled['prompt_profile_snapshot']
    assert len(rows(stack['creation_store'], 'investigation_runs')) == 1
    with pytest.raises(Exception):
        app.confirm_and_queue(command(draft, revision=2), principal=PRINCIPAL)


@pytest.mark.parametrize('failure', ['stale', 'content', 'hash', 'compiler', 'run_insert', 'revision_content'])
def test_confirm_failure_no_run_or_job(creation_stack, content, failure):
    stack = creation_stack; draft = bound(stack, content); app = stack['app_service']
    if failure in {'content', 'hash', 'revision_content'}:
        config = draft.configuration.model_dump(mode='json')
        if failure == 'content': config['judgement']['content']['audit_goal'] += ' corrupt'
        else: config['judgement']['content_hash'] = '0' * 64
        table = 'investigation_draft_revisions' if failure == 'revision_content' else 'investigation_drafts'
        with sqlite3.connect(stack['creation_store'].db_path) as db:
            db.execute(f'UPDATE {table} SET configuration_json=?', (json.dumps(config),))
    if failure == 'run_insert':
        with sqlite3.connect(stack['creation_store'].db_path) as db:
            db.execute("CREATE TRIGGER reject_run BEFORE INSERT ON investigation_runs BEGIN SELECT RAISE(ABORT,'run failed'); END")
    with patch('backend.rulesets.compiler.compile_ruleset_content', side_effect=ValueError('compile failure')) if failure == 'compiler' else patch.dict({}, {}):
        with pytest.raises(Exception):
            app.confirm_and_queue(command(draft, revision=9 if failure == 'stale' else None), principal=PRINCIPAL)
    assert not rows(stack['creation_store'], 'investigation_runs')
    with sqlite3.connect(stack['creation_store'].db_path) as db:
        assert db.execute('SELECT status FROM investigation_drafts').fetchone()[0] == 'DRAFT'
    assert not JobStore(stack['resource_db']).list()


@pytest.mark.parametrize('field', ['rule_snapshot', 'prompt_profile_snapshot', 'capabilities', 'max_comments', 'analysis_batch_size', 'keyword_source'])
def test_job_mismatch_rejected_without_repair(creation_stack, content, field):
    stack = creation_stack; draft = bound(stack, content)
    run = stack['app_service'].confirm_and_queue(command(draft), principal=PRINCIPAL)
    execution = adapter(stack); job_id = execution.ensure_job(run)
    old = execution.job_store.get(job_id)[field]
    value = ({**old, 'corrupt': True} if isinstance(old, dict) else ['vision'] if isinstance(old, list)
             else old + 1 if isinstance(old, int) else 'corrupt')
    execution.job_store.update(job_id, **{field: value})
    with pytest.raises(Exception): execution.ensure_job(run)
    with pytest.raises(Exception): execution.verify_run_job(run)
    assert execution.job_store.get(job_id)[field] == value


@pytest.mark.parametrize('field', ['job_id', 'config_hash', 'rule_snapshot_json', 'prompt_profile_snapshot_json', 'audit_config_json'])
def test_audit_revision_mismatch(creation_stack, content, field):
    stack = creation_stack; draft = bound(stack, content)
    run = stack['app_service'].confirm_and_queue(command(draft), principal=PRINCIPAL)
    execution = adapter(stack); job_id = execution.ensure_job(run)
    with sqlite3.connect(stack['resource_db']) as db:
        db.execute(f'UPDATE task_audit_config_revisions SET {field}=?', ('{}' if field.endswith('_json') else 'different',))
    with pytest.raises(Exception): execution.ensure_job(run)
    with pytest.raises(Exception): execution.verify_run_job(run)


@pytest.mark.parametrize('stage', ['job', 'revision'])
def test_worker_creation_failure_never_starts_pipeline(creation_stack, content, stage):
    stack = creation_stack; draft = bound(stack, content)
    stack['app_service'].confirm_and_queue(command(draft), principal=PRINCIPAL)
    calls = []
    execution = adapter(stack, pipeline_factory=lambda **_: calls.append('pipeline'))
    target, method = (execution.job_store, 'create') if stage == 'job' else (execution.revision_store, 'create_or_get')
    with patch.object(target, method, side_effect=RuntimeError('injected failure')):
        worker = InvestigationWorker(stack['creation_store'], execution_adapter=execution, report_adapter=None, session_adapter=None)
        failed = worker.run_once()
    assert failed.status.value == 'FAILED' and not calls
    assert len(rows(stack['creation_store'], 'investigation_runs')) == 1


@pytest.mark.parametrize('where', ['source', 'compiled_prompt', 'draft_config', 'configuration'])
def test_store_rechecks_temporary_resolution(creation_stack, content, where):
    stack = creation_stack; draft = bound(stack, content); app = stack['app_service']
    original = app.resource_service.resolve_confirmation
    def forged(*args, **kwargs):
        resolution = original(*args, **kwargs)
        if where == 'draft_config':
            with sqlite3.connect(stack['creation_store'].db_path) as db:
                config = draft.configuration.model_dump(mode='json'); config['platform'] = 'dy'
                db.execute('UPDATE investigation_drafts SET configuration_json=?', (json.dumps(config),))
            return resolution
        payload = resolution.model_dump(mode='json')
        if where == 'source': payload['temporary_ruleset']['proposal_id'] += '-forged'
        elif where == 'configuration':
            payload['platform'] = 'dy'; payload['execution']['platform'] = 'dy'
        else:
            payload['execution']['prompt_profile_snapshot']['image_prompt'] += ' injected'
            payload['execution']['audit_config_revision']['prompt_profile_snapshot']['image_prompt'] += ' injected'
        payload['config_hash'] = confirmed_configuration_hash({k: v for k, v in payload.items() if k != 'config_hash'})
        return type(resolution).model_validate(payload)
    with patch.object(app.resource_service, 'resolve_confirmation', forged):
        with pytest.raises(Exception): app.confirm_and_queue(command(draft), principal=PRINCIPAL)
    assert not rows(stack['creation_store'], 'investigation_runs')


def test_formal_shape_hash_and_execution_unchanged(creation_stack):
    stack = creation_stack; app = stack['app_service']
    draft = app.create_draft(CreateDraftCommand.model_validate(_t1_temporary_arguments(stack)), principal=PRINCIPAL)
    run = app.confirm_and_queue(command(draft), principal=PRINCIPAL)
    assert 'temporary_ruleset' not in run.confirmed_configuration
    frozen = ConfirmedConfigurationSnapshotV4.model_validate(run.confirmed_configuration)
    assert frozen.model_dump(mode='json') == run.confirmed_configuration
    validate_execution_payload(frozen.execution.model_dump(mode='json'))
    execution = adapter(stack); execution.ensure_job(run); execution.verify_run_job(run)


def test_http_confirm_and_preview(creation_stack, content):
    stack = creation_stack; draft = bound(stack, content)
    client = stack['client']
    assert client.get(f'/api/investigation-drafts/{draft.id}/confirmation-preview').json()['can_confirm']
    response = client.post(f'/api/investigation-drafts/{draft.id}/confirm-and-queue',
        json={'expected_revision': 1, 'confirmed': True}, headers={'Idempotency-Key': 't5-http'})
    assert response.status_code == 202, response.text


def test_verified_copy_cannot_change_after_validation(creation_stack, content):
    from backend.audit_agent.pipeline import AuditPipeline
    stack = creation_stack; draft = bound(stack, content)
    run = stack['app_service'].confirm_and_queue(command(draft), principal=PRINCIPAL)
    execution = adapter(stack); job_id = execution.ensure_job(run)
    pipeline = AuditPipeline.__new__(AuditPipeline); pipeline.job_id = job_id
    def validate_then_change():
        verified = execution.verify_run_job(run)
        execution.job_store.update(job_id, rule_snapshot={'schema_version': 2, 'different': True})
        return verified
    pipeline._m3_snapshot_validator = validate_then_change
    verified = pipeline._verified_m3_snapshot()
    assert verified['rule_snapshot'] == run.confirmed_configuration['execution']['rule_snapshot']
    with pytest.raises(Exception): execution.verify_run_job(run)


def test_classification_and_recovery_reject_mismatch(creation_stack, content):
    stack = creation_stack; draft = bound(stack, content)
    queued = stack['app_service'].confirm_and_queue(command(draft), principal=PRINCIPAL)
    execution = adapter(stack)
    claimed = stack['creation_store'].claim_next('test-worker')
    job_id = execution.ensure_job(claimed)
    execution.job_store.update(job_id, prompt_profile_snapshot={'wrong': True}, status='completed')
    worker = InvestigationWorker(stack['creation_store'], execution_adapter=execution, report_adapter=None, session_adapter=None)
    result = worker._recover_execution(claimed)
    assert result.status.value == 'FAILED' and result.error_code == 'frozen_execution_mismatch'


@pytest.mark.parametrize('entry', ['first', 'resume'])
def test_pipeline_consumes_exact_verified_job_copy(creation_stack, content, entry):
    from types import SimpleNamespace
    from backend.audit_agent.pipeline import AuditPipeline
    import backend.audit_agent.pipeline as module
    stack = creation_stack; draft = bound(stack, content)
    run = stack['app_service'].confirm_and_queue(command(draft), principal=PRINCIPAL)
    execution = adapter(stack); job_id = execution.ensure_job(run)
    expected = execution.verify_run_job(run)
    pipeline = AuditPipeline.__new__(AuditPipeline); pipeline.job_id = job_id
    captured = []
    def validate():
        value = execution.verify_run_job(run)
        execution.job_store.update(job_id, rule_snapshot={'wrong': True}, prompt_profile_snapshot={'wrong': True})
        return value
    def observe(category, prompts):
        captured.append((deepcopy(pipeline.rule_snapshot), deepcopy(prompts), pipeline.audit_config_revision_id))
        raise RuntimeError('stop immediately after observing consumption')
    pipeline._m3_snapshot_validator = validate
    pipeline._set_prompt_context = observe
    pipeline.ingestion = type('EmptyIngestion', (), {'pending_for_task': lambda *args, **kwargs: []})()
    with patch.object(module, 'job_store', execution.job_store):
        if entry == 'first': pipeline.run(SimpleNamespace(**run.confirmed_configuration['execution'], _authoritative_m3_contract=True))
        else: pipeline.resume_pending_analysis()
    assert captured == [(expected['rule_snapshot'], expected['prompt_profile_snapshot'], expected['_verified_audit_config_revision_id'])]
    assert execution.job_store.get(job_id)['rule_snapshot'] == {'wrong': True}


def test_preview_compile_blocker_and_tool_confirm(creation_stack, content):
    from backend.investigation_creation.tools import HermesToolExecutionIdentity
    stack = creation_stack; draft = bound(stack, content)
    with patch('backend.rulesets.compiler.compile_ruleset_content', side_effect=ValueError('unavailable compiler')):
        preview = stack['app_service'].get_confirmation_preview(draft.id, principal=PRINCIPAL)
        assert not preview.can_confirm and any(b.code == 'INVALID_RULESET_REFERENCE' for b in preview.blockers)
    result = stack['tool_service'].execute_with_identity('confirm_and_queue_investigation',
        {'draft_id':draft.id,'expected_revision':1,'confirmed':True,'idempotency_key':'t5-tool'}, principal=PRINCIPAL,
        identity=HermesToolExecutionIdentity('session-test','turn-test','confirm-test'))
    assert result['status'] == 'ok', result
    assert len(rows(stack['creation_store'], 'investigation_runs')) == 1


@pytest.mark.parametrize('mutation', ['both', 'neither', 'inline_hash', 'outer_hash'])
def test_invalid_frozen_source_contract(creation_stack, content, mutation):
    stack = creation_stack; draft = bound(stack, content)
    run = stack['app_service'].confirm_and_queue(command(draft), principal=PRINCIPAL)
    snapshot = deepcopy(run.confirmed_configuration)
    if mutation == 'both': snapshot['ruleset_revision'] = {'id':'forged'}
    elif mutation == 'neither': snapshot.pop('temporary_ruleset')
    elif mutation == 'inline_hash': snapshot['temporary_ruleset']['content_hash'] = '0'*64
    else: snapshot['config_hash'] = '0'*64
    with pytest.raises(ValueError): ConfirmedConfigurationSnapshotV4.model_validate(snapshot)


def test_revision_creation_crash_recovers_without_snapshot_repair(creation_stack, content):
    stack = creation_stack; draft = bound(stack, content)
    run = stack['app_service'].confirm_and_queue(command(draft), principal=PRINCIPAL)
    execution = adapter(stack)
    with patch.object(execution.revision_store, 'create_or_get', side_effect=RuntimeError('crash before revision')):
        with pytest.raises(RuntimeError): execution.ensure_job(run)
    job_id = execution.job_id_for_run(run.id)
    original = execution.job_store.get(job_id)
    assert not original['current_audit_config_revision_id']
    assert execution.ensure_job(run) == job_id
    verified = execution.verify_run_job(run)
    assert verified['rule_snapshot'] == original['rule_snapshot']
    assert verified['prompt_profile_snapshot'] == original['prompt_profile_snapshot']


def test_concurrent_revision_attachment_is_not_overwritten(creation_stack, content):
    stack = creation_stack; draft = bound(stack, content)
    run = stack['app_service'].confirm_and_queue(command(draft), principal=PRINCIPAL)
    execution = adapter(stack); original = execution.revision_store.create_or_get
    def concurrent(*args, **kwargs):
        revision = original(*args, **kwargs)
        execution.job_store.update(kwargs['job_id'], current_audit_config_revision_id='other-writer-revision')
        return revision
    with patch.object(execution.revision_store, 'create_or_get', concurrent):
        with pytest.raises(ValueError): execution.ensure_job(run)
    assert execution.job_store.get(execution.job_id_for_run(run.id))['current_audit_config_revision_id'] == 'other-writer-revision'


@pytest.mark.parametrize('missing_run', [False, True])
def test_direct_m3_resume_requires_persisted_run(creation_stack, content, missing_run):
    from backend.audit_agent.pipeline import AuditPipeline
    stack = creation_stack; draft = bound(stack, content)
    stack['app_service'].confirm_and_queue(command(draft), principal=PRINCIPAL)
    run = stack['creation_store'].claim_next('direct-resume-test')
    execution = adapter(stack); job_id = execution.ensure_job(run)
    run = stack['creation_store'].bind_job(run.id, run.claim_token, job_id)
    pipeline = AuditPipeline.__new__(AuditPipeline); pipeline.job_id = job_id
    with patch('backend.investigation_creation.store.InvestigationCreationStore', return_value=stack['creation_store']), \
         patch('backend.investigation_creation.adapters.AuditPipelineExecutionAdapter', return_value=execution):
        if missing_run:
            with patch.object(stack['creation_store'], 'get_run_for_job', return_value=None):
                with pytest.raises(ValueError, match='no authoritative frozen Run'):
                    pipeline._verified_m3_snapshot()
        else:
            verified = pipeline._verified_m3_snapshot()
            assert verified == execution.verify_run_job(run)


@pytest.mark.parametrize('corrupt', ['hash', 'content'])
def test_corrupt_persisted_inline_source_returns_json_error(creation_stack, content, corrupt):
    stack = creation_stack; draft = bound(stack, content)
    configuration = draft.configuration.model_dump(mode='json')
    if corrupt == 'hash': configuration['judgement']['content_hash'] = '0' * 64
    else: configuration['judgement']['content']['audit_goal'] += ' corrupt'
    with sqlite3.connect(stack['creation_store'].db_path) as db:
        db.execute('UPDATE investigation_drafts SET configuration_json=? WHERE id=?',
                   (json.dumps(configuration), draft.id))
    preview = stack['client'].get(f'/api/investigation-drafts/{draft.id}/confirmation-preview')
    confirm = stack['client'].post(f'/api/investigation-drafts/{draft.id}/confirm-and-queue',
        headers={'Idempotency-Key':'corrupt-http'}, json={'expected_revision':1,'confirmed':True})
    for response in [preview, confirm]:
        assert response.status_code == 422
        assert any(error['type'] == 'value_error' for error in response.json()['detail'])
    assert not rows(stack['creation_store'], 'investigation_runs')
    assert not JobStore(stack['resource_db']).list()
