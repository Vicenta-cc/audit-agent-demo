"""Two creation entrypoints share one operation; all writes use isolated databases."""
from contextlib import contextmanager
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest

from backend.investigation_creation.principal import Principal
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.tools import HermesToolExecutionIdentity
from test_investigation_creation_conversation import creation_stack
from test_ruleset_proposal_presentation import content, run, evidence
from test_ruleset_proposal_approval import creation
from test_draft_creation_recovery import arguments

P = Principal('principal-a')
CREATE = 'create_investigation_draft'
ADOPT = 'use_ruleset_proposal'


@contextmanager
def operation(stack, content):
    first = run(stack, content)
    session = first['session_id']
    turn, _ = stack['conversation'].accept_message(session, client_message_id='create-once',
        content='采用刚才的规则创建一个草案，不启动', principal=P)
    service = stack['tool_service']
    service.begin_conversation_turn(session, turn.id)
    commands = {CREATE: arguments(stack), ADOPT: {
        'presentation_id': evidence(first)['presentation_id'], 'create_draft': creation()}}
    def call(tool, args=None, call_id='one', runtime=None, principal=P):
        return service.execute_with_identity(tool, args or commands[tool], principal=principal,
            identity=HermesToolExecutionIdentity(session, runtime or turn.id, call_id))
    try:
        yield call, commands, session, turn
    finally:
        service.end_conversation_turn(session)


def counts(stack):
    with stack['app_service'].store._connect() as db:
        return tuple(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in (
            'investigation_drafts', 'ruleset_proposal_approvals', 'investigation_runs'))


def lexicon():
    return {'title': '旅游投诉', 'entries': [
        {'id': 'topic', 'kind': 'main', 'term': '交通投诉'},
        {'id': 'variant', 'kind': 'variant', 'parent_id': 'topic', 'term': '出租车绕路'}]}


@pytest.mark.parametrize('first_tool', [CREATE, ADOPT])
def test_cross_entrypoint_success_does_not_create_again(creation_stack, content, first_tool):
    s = creation_stack
    with operation(s, content) as (call, commands, session, turn):
        result = call(first_tool)
        assert result['status'] == 'ok', result
        other = ADOPT if first_tool == CREATE else CREATE
        conflict = call(other, call_id='other-entrypoint', runtime='restarted-runtime')
        assert conflict['status'] == 'error', conflict
        assert conflict['error']['details']['write_status'] == 'COMMITTED'
        assert conflict['error']['details']['draft_id'] == result['data']['draft']['id']
        assert call(first_tool, call_id='retry')['data']['draft']['id'] == result['data']['draft']['id']
        assert counts(s) == (1, int(first_tool == ADOPT), 0)
        if first_tool == CREATE:
            # A new explicit adoption of the EXISTING draft still performs all approval checks.
            update = {'presentation_id': commands[ADOPT]['presentation_id'],
                      'draft_id': result['data']['draft']['id'], 'expected_revision': 1}
            adopted = call(ADOPT, update, 'adopt-existing')
            assert adopted['status'] == 'ok', adopted
            assert adopted['data']['draft']['id'] == result['data']['draft']['id']
            assert counts(s) == (1, 1, 0)


@pytest.mark.parametrize('tool', [CREATE, ADOPT])
@pytest.mark.parametrize('fault', ['stale_ref', 'empty_projection'])
def test_validation_failure_can_be_corrected_in_same_operation(creation_stack, content, tool, fault):
    s = creation_stack
    with operation(s, content) as (call, commands, session, turn):
        manager = s['app_service'].resource_management
        ctx = dict(session_id=session, principal=P)
        body = lexicon()
        if fault == 'empty_projection':
            body['entries'][0]['enabled'] = False
        edit = manager.create_lexicon(body, **ctx)
        args = deepcopy(commands[tool])
        config = args['create_draft']['configuration'] if tool == ADOPT else args['configuration']
        config['investigation']['recall_plan'] = {'strategy': 'resource_ref', 'resource_ref': edit['resource_ref']}
        patches = [{'operation': 'upsert_entry', 'target_id': 'topic', 'values': {'enabled': True}},
                   {'operation': 'set_metadata', 'values': {'title': '更新的旅游投诉'}}]
        if fault == 'stale_ref':
            updated = manager.update(edit['edit_id'], 1, patches, **ctx)
        failed = call(tool, args, 'bad')
        assert failed['status'] == 'error'
        assert failed['error']['details']['write_status'] == 'NOT_STARTED', failed
        assert failed['error']['details']['retryable'] is True
        assert counts(s) == (0, 0, 0)
        if fault == 'empty_projection':
            updated = manager.update(edit['edit_id'], 1, patches, **ctx)
        original = deepcopy(args)
        config['investigation']['recall_plan']['resource_ref'] = updated['resource_ref']
        success = call(tool, args, 'corrected')
        assert success['status'] == 'ok', success
        assert call(tool, args, 'repeated')['data']['draft']['id'] == success['data']['draft']['id']
        assert call(tool, original, 'bad') == failed
        assert counts(s) == (1, int(tool == ADOPT), 0)


@pytest.mark.parametrize('fault', ['preview', 'receipt'])
@pytest.mark.parametrize('same_call', [True, False])
def test_adoption_commit_recovered_after_restart(creation_stack, content, fault, same_call):
    s = creation_stack
    with operation(s, content) as (call, commands, session, turn):
        if fault == 'preview':
            with patch.object(s['app_service'].resource_service, 'confirmation_preview', side_effect=RuntimeError('preview unavailable')):
                failed = call(ADOPT)
            assert failed['error']['details']['write_status'] == 'COMMITTED', failed
            assert failed['error']['details']['mutation_applied'] is True
        else:
            with patch.object(s['creation_store'], 'complete_tool_execution', side_effect=ConnectionError('response lost')):
                with pytest.raises(ConnectionError):
                    call(ADOPT)
        assert counts(s) == (1, 1, 0)
        notice = s['conversation']._binding_failure_notice(turn)
        assert '调查草案和规则采用已保存' in notice
        assert '采用操作未成功' not in notice
        s['app_service'].store = InvestigationCreationStore(s['creation_store'].db_path)
        result = call(ADOPT, call_id='one' if same_call else 'recovered')
        assert result['status'] == 'ok', result
        assert counts(s) == (1, 1, 0)
        recovered_results = s['app_service'].store.successful_adoption_results(
            session_id=session, turn_id=turn.id, principal=P.id)
        assert recovered_results[-1]['draft']['id'] == result['data']['draft']['id']
        checkpoints = s['app_service'].store.successful_conversation_tool_receipts(
            session_id=session, turn_id=turn.id, principal=P.id)
        assert any(r['response']['data'].get('draft', {}).get('id') == result['data']['draft']['id'] for r in checkpoints)
        assert not s['conversation']._binding_failure_notice(turn)


def test_concurrent_cross_entrypoint_creates_once(creation_stack, content):
    s = creation_stack
    with operation(s, content) as (call, commands, session, turn):
        gate = Barrier(6)
        def attempt(i):
            gate.wait(timeout=10)
            return call(CREATE if i % 2 else ADOPT, call_id=f'parallel-{i}')
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(6)))
        successes = [r for r in results if r['status'] == 'ok']
        assert successes
        assert len({r['data']['draft']['id'] for r in successes}) == 1
        assert counts(s)[0] == 1
        assert counts(s)[2] == 0


def test_adoption_unknown_write_stays_blocked(creation_stack, content):
    s = creation_stack
    with operation(s, content) as (call, commands, session, turn):
        with patch.object(s['app_service'], 'use_ruleset_proposal', side_effect=TimeoutError('unknown')):
            failed = call(ADOPT)
        assert failed['error']['details']['write_status'] == 'UNKNOWN'
        assert call(ADOPT, call_id='again')['error']['code'] == 'MUTATION_RESULT_UNKNOWN'
        assert call(CREATE, call_id='other')['error']['code'] == 'MUTATION_RESULT_UNKNOWN'
        assert counts(s) == (0, 0, 0)


@pytest.mark.parametrize('tool', [CREATE, ADOPT])
def test_corrected_attempt_can_switch_entrypoint(creation_stack, content, tool):
    s = creation_stack
    with operation(s, content) as (call, commands, session, turn):
        bad = deepcopy(commands[tool])
        manager = s['app_service'].resource_management
        edit = manager.create_lexicon(lexicon(), session_id=session, principal=P)
        manager.update(edit['edit_id'], 1, [{'operation': 'set_metadata', 'values': {'title': 'new'}}],
                       session_id=session, principal=P)
        config = bad['create_draft']['configuration'] if tool == ADOPT else bad['configuration']
        config['investigation']['recall_plan'] = {'strategy': 'resource_ref', 'resource_ref': edit['resource_ref']}
        failed = call(tool, bad, 'invalid-reference')
        assert failed['error']['details']['write_status'] == 'NOT_STARTED'
        other = ADOPT if tool == CREATE else CREATE
        result = call(other, call_id='corrected-entrypoint')
        assert result['status'] == 'ok', result
        assert counts(s) == (1, int(other == ADOPT), 0)


def test_adoption_and_operation_binding_rollback_together(creation_stack, content):
    from backend.investigation_creation import draft_operations
    s = creation_stack
    original = draft_operations.bind_draft
    def fault(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('failure before commit')
    with operation(s, content) as (call, commands, session, turn):
        with patch.object(draft_operations, 'bind_draft', side_effect=fault):
            failed = call(ADOPT)
        assert failed['status'] == 'error'
        assert counts(s) == (0, 0, 0)
        with s['creation_store']._connect() as db:
            assert db.execute('SELECT COUNT(*) FROM investigation_draft_revisions').fetchone()[0] == 0
            row = db.execute('SELECT * FROM draft_creation_operations').fetchone()
            assert row['draft_id'] is None
            assert row['state'] == 'UNKNOWN'


def test_foreign_resource_and_stale_presentation_never_grant_authority(creation_stack, content):
    s = creation_stack
    with operation(s, content) as (call, commands, session, turn):
        edit = s['app_service'].resource_management.create_lexicon(lexicon(), session_id=session,
                                                                  principal=Principal('another-user'))
        args = deepcopy(commands[ADOPT])
        args['create_draft']['configuration']['investigation']['recall_plan'] = {
            'strategy': 'resource_ref', 'resource_ref': edit['resource_ref']}
        denied = call(ADOPT, args, 'foreign')
        assert denied['error']['code'] == 'RESOURCE_REF_INVALID'
        assert denied['error']['details']['retryable'] is False
        assert counts(s) == (0, 0, 0)
        with s['creation_store']._connect() as db:
            proposal_id = db.execute('SELECT proposal_id FROM ruleset_proposals').fetchone()[0]
        changed = deepcopy(content)
        changed['audit_goal'] = 'A new unreviewed goal'
        s['app_service'].update_ruleset_proposal(proposal_id, session_id=session, expected_version=1, content=changed)
        stale = call(ADOPT, call_id='stale-presentation')
        assert stale['error']['code'] == 'PROPOSAL_PRESENTATION_STALE'
        assert stale['error']['details']['retryable'] is False
        assert counts(s) == (0, 0, 0)


@pytest.mark.parametrize('status', ['SUCCEEDED', 'FAILED', 'STARTED'])
def test_old_adoption_receipts_recover_from_committed_approval(creation_stack, content, status):
    import json
    s = creation_stack
    with operation(s, content) as (call, commands, session, turn):
        created = call(ADOPT)
        assert created['status'] == 'ok'
        with s['creation_store']._connect() as db:
            # Simulate pre-upgrade adoption: approval committed, no operation journal.
            db.execute('DELETE FROM draft_creation_attempts')
            db.execute('DELETE FROM draft_creation_operations')
            if status != 'SUCCEEDED':
                response = json.dumps({'status': 'error', 'error': {'code': 'TOOL_EXECUTION_FAILED'}}) if status == 'FAILED' else ''
                db.execute('UPDATE investigation_creation_tool_receipts SET status=?,response_json=? WHERE tool_name=?',
                           (status, response, ADOPT))
            db.execute('DROP INDEX uq_creation_turn_mutation_tool')
            db.execute("""CREATE UNIQUE INDEX uq_creation_turn_mutation_tool
                ON investigation_creation_tool_receipts(session_id,turn_id,tool_name)
                WHERE is_mutation=1 AND tool_name NOT IN
                ('create_lexicon_edit','open_resource_edit','update_resource_edit','save_resource','create_investigation_draft')""")
            tables = ['investigation_drafts', 'investigation_draft_revisions', 'ruleset_proposal_approvals',
                      'investigation_creation_tool_receipts']
            before = {t: [tuple(r) for r in db.execute(f'SELECT * FROM {t}')] for t in tables}
        for _ in range(2):
            s['app_service'].store = InvestigationCreationStore(s['creation_store'].db_path)
        with s['app_service'].store._connect() as db:
            assert before == {t: [tuple(r) for r in db.execute(f'SELECT * FROM {t}')] for t in tables}
        recovered = call(ADOPT, call_id='one' if status == 'FAILED' else 'after-migration')
        assert recovered['status'] == 'ok', recovered
        assert recovered['data']['draft']['id'] == created['data']['draft']['id']
        assert counts(s) == (1, 1, 0)
        with s['app_service'].store._connect() as db:
            original = next(r for r in before['investigation_creation_tool_receipts'] if r[5] == ADOPT)
            stored = tuple(db.execute('SELECT * FROM investigation_creation_tool_receipts WHERE receipt_id=?', (original[0],)).fetchone())
            assert stored == original
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []


def test_recovery_preserves_created_revision_after_later_edit(creation_stack, content):
    s = creation_stack
    with operation(s, content) as (call, commands, session, turn):
        with patch.object(s['app_service'].resource_service, 'confirmation_preview', side_effect=RuntimeError('preview failed')):
            failed = call(ADOPT)
        draft_id = failed['error']['details']['draft_id']
        s['creation_store'].update_draft(draft_id, principal=P.id, expected_revision=1, title='later edit')
        result = call(ADOPT, call_id='recovered')
        assert result['status'] == 'ok', result
        assert result['data']['draft']['title'] == commands[ADOPT]['create_draft']['title']
        assert result['data']['draft']['current_revision'] == 1
        assert result['data']['confirmation_preview']['draft_revision'] == 1
        assert s['app_service'].get_draft(draft_id, principal=P).current_revision == 2
        assert counts(s) == (1, 1, 0)


@pytest.mark.parametrize('stage', ['before_commit', 'after_commit'])
def test_real_process_exit_during_adoption(creation_stack, content, stage):
    import os
    import time
    import signal
    if not hasattr(os, 'fork'):
        pytest.skip('requires fork to terminate the isolated application process')
    s = creation_stack
    with operation(s, content) as (call, commands, session, turn):
        pid = os.fork()
        if pid == 0:
            try:
                def terminate(*args, **kwargs):
                    os._exit(71)
                target = ('backend.investigation_creation.draft_operations.bind_draft'
                          if stage == 'before_commit' else
                          'backend.investigation_creation.store.InvestigationCreationStore.complete_tool_execution')
                with patch(target, side_effect=terminate):
                    call(ADOPT)
                os._exit(72)
            except BaseException:
                os._exit(73)
        deadline = time.monotonic() + 15
        while True:
            finished, status = os.waitpid(pid, os.WNOHANG)
            if finished:
                break
            if time.monotonic() > deadline:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
                pytest.fail('isolated child timed out')
            time.sleep(0.05)
        assert os.waitstatus_to_exitcode(status) == 71
        s['app_service'].store = InvestigationCreationStore(s['creation_store'].db_path)
        recovered = call(ADOPT, call_id='after-process-exit')
        if stage == 'after_commit':
            assert recovered['status'] == 'ok', recovered
            assert counts(s) == (1, 1, 0)
        else:
            assert recovered['error']['code'] == 'MUTATION_RESULT_UNKNOWN'
            assert counts(s) == (0, 0, 0)


def test_upgrade_existing_operation_journal_is_atomic_and_preserves_running_task(creation_stack, content):
    from backend.investigation_creation import draft_operations
    from backend.investigation_creation.contracts import ConfirmAndQueueCommand
    s = creation_stack
    with operation(s, content) as (call, commands, session, turn):
        view = call(CREATE)['data']
        job = s['app_service'].confirm_and_queue(ConfirmAndQueueCommand(
            draft_id=view['draft']['id'], expected_revision=1, confirmed=True,
            idempotency_key='preserved-job',
            expected_task_settings_revision=view['confirmation_preview']['task_settings_revision'],
        ), principal=P)
        store = s['creation_store']
        with store._connect() as db:
            db.execute("UPDATE investigation_runs SET status='RUNNING', claimed_by='worker', claim_token='lease' WHERE id=?", (job.id,))
            db.execute('ALTER TABLE draft_creation_operations DROP COLUMN result_json')
            db.execute('DROP INDEX uq_creation_turn_mutation_tool')
            db.execute("""CREATE UNIQUE INDEX uq_creation_turn_mutation_tool
                ON investigation_creation_tool_receipts(session_id,turn_id,tool_name)
                WHERE is_mutation=1 AND tool_name NOT IN
                ('create_lexicon_edit','open_resource_edit','update_resource_edit','save_resource','create_investigation_draft')""")
            old_index = db.execute("SELECT sql FROM sqlite_master WHERE name='uq_creation_turn_mutation_tool'").fetchone()[0]
            tables = ['investigation_drafts', 'investigation_draft_revisions', 'investigation_runs',
                      'draft_creation_attempts', 'investigation_creation_tool_receipts']
            before = {t: [tuple(r) for r in db.execute(f'SELECT * FROM {t}')] for t in tables}
            operations = [dict(r) for r in db.execute('SELECT * FROM draft_creation_operations')]
        initialize = draft_operations.initialize
        def interrupted(db):
            initialize(db)
            raise RuntimeError('interrupted upgrade')
        with patch.object(draft_operations, 'initialize', side_effect=interrupted):
            with pytest.raises(RuntimeError):
                InvestigationCreationStore(store.db_path)
        with store._connect() as db:
            assert 'result_json' not in {r[1] for r in db.execute('PRAGMA table_info(draft_creation_operations)')}
            assert db.execute("SELECT sql FROM sqlite_master WHERE name='uq_creation_turn_mutation_tool'").fetchone()[0] == old_index
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: InvestigationCreationStore(store.db_path), range(4)))
        s['app_service'].store = InvestigationCreationStore(store.db_path)
        with store._connect() as db:
            assert before == {t: [tuple(r) for r in db.execute(f'SELECT * FROM {t}')] for t in tables}
            current = [dict(r) for r in db.execute('SELECT * FROM draft_creation_operations')]
            assert current == [{**r, 'result_json': ''} for r in operations]
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert call(CREATE)['data']['draft']['id'] == view['draft']['id']
        assert call(ADOPT, call_id='cannot-create-second')['error']['details']['write_status'] == 'COMMITTED'
        assert counts(s) == (1, 0, 1)
