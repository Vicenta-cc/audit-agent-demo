"""Regression of the 2026-10-03 RESOURCE_STALE -> retry incident.

All databases are synthetic pytest temporary files. No provider, crawler or
production service is used; the real application validation/write paths run.
"""
import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch

import pytest

from test_investigation_creation_conversation import (
    creation_stack, _search_draft_from_options, _draft_count,
)
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.tools import HermesToolExecutionIdentity


def arguments(stack):
    options = stack['tool_service'].execute('query_investigation_options', {
        'domain_hint': '博彩', 'mode': 'search', 'include_lexicon_terms_for_ids': ['gambling'],
    }, principal=Principal('principal-a'))
    return _search_draft_from_options([options])


def call(stack, args, call_id, turn='turn-create', principal='principal-a'):
    return stack['tool_service'].execute_with_identity(
        'create_investigation_draft', args, principal=Principal(principal),
        identity=HermesToolExecutionIdentity.require(
            session_id='recovery-session', turn_id=turn, tool_call_id=call_id))


def publish_versions(stack):
    args = arguments(stack)
    with sqlite3.connect(stack['resource_db']) as conn:
        body = json.loads(conn.execute('SELECT snapshot_json FROM rule_set_revisions WHERE id=?',
            (args['configuration']['judgement']['ruleset_revision_id'],)).fetchone()[0])
    manager = stack['app_service'].resource_management
    def strip_legacy(value):
        if isinstance(value, dict):
            value.pop('source_mappings', None)
            for child in value.values():
                strip_legacy(child)
        elif isinstance(value, list):
            for child in value:
                strip_legacy(child)
    strip_legacy(body)
    first = manager.save_library('ruleset', 'ruleset.recovery', body, 0, 'save-v1',
                                 principal=Principal('principal-a'))
    def select(result):
        data = copy.deepcopy(args)
        data['configuration']['judgement'] = {
            'strategy': 'existing_ruleset', 'ruleset_revision_id': result['revision_id'],
            'expected_ruleset_version': result['published_version'],
            'expected_ruleset_content_hash': result['content_hash'],
        }
        return data
    old = select(first)
    # Same rule content, changed internal ordering numbers, as in the incident.
    for i, category in enumerate(body['categories']):
        category['order'] = i + 100
    second = manager.save_library('ruleset', 'ruleset.recovery', body, 1, 'save-v2',
                                  principal=Principal('principal-a'))
    return old, select(second)


def test_real_stale_rule_corrected_in_same_turn_and_exact_replays(creation_stack):
    stack = creation_stack
    old, new = publish_versions(stack)
    failed = call(stack, old, 'v1')
    assert failed['error']['code'] == 'RESOURCE_STALE'
    assert failed['error']['details']['write_status'] == 'NOT_STARTED'
    assert _draft_count(stack['creation_store']) == 0
    read = stack['tool_service'].execute('read_resource', {
        'kind': 'ruleset', 'resource_id': 'ruleset.recovery',
    }, principal=Principal('principal-a'))
    assert read['published_version'] == 2
    created = call(stack, new, 'v2')
    assert created['status'] == 'ok'
    assert call(stack, new, 'v2') == created
    assert call(stack, new, 'network-retry')['data']['draft']['id'] == created['data']['draft']['id']
    assert call(stack, old, 'v1') == failed  # Original failed attempt is immutable.
    assert call(stack, new, 'v1')['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    changed = {**new, 'title': 'a different draft'}
    assert call(stack, changed, 'different')['error']['details']['write_status'] == 'COMMITTED'
    assert _draft_count(stack['creation_store']) == 1


def test_concurrent_corrected_attempts_create_one_draft(creation_stack):
    stack = creation_stack
    old, new = publish_versions(stack)
    assert call(stack, old, 'stale')['status'] == 'error'
    gate = Barrier(8)
    def attempt(i):
        gate.wait(timeout=10)
        return call(stack, new, f'parallel-{i}')
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    good = [r for r in results if r['status'] == 'ok']
    assert good
    assert len({r['data']['draft']['id'] for r in good}) == 1
    assert all(r['status'] == 'ok' or r['error']['code'] == 'MUTATION_RESULT_UNKNOWN' for r in results)
    assert _draft_count(stack['creation_store']) == 1
    assert call(stack, new, 'settled')['status'] == 'ok'


def test_running_attempt_is_not_released_or_replaced(creation_stack):
    stack = creation_stack
    args = arguments(stack)
    entered, release = Event(), Event()
    original = stack['app_service'].create_draft
    def delayed(*a, **kw):
        entered.set()
        assert release.wait(10)
        return original(*a, **kw)
    with patch.object(stack['app_service'], 'create_draft', side_effect=delayed):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(call, stack, args, 'running')
            assert entered.wait(10)
            try:
                blocked = call(stack, {**args, 'title': 'changed'}, 'competing')
                assert blocked['error']['code'] == 'MUTATION_RESULT_UNKNOWN'
                assert _draft_count(stack['creation_store']) == 0
            finally:
                release.set()
            assert pending.result()['status'] == 'ok'
    assert _draft_count(stack['creation_store']) == 1


@pytest.mark.parametrize('same_call', [True, False])
def test_committed_draft_survives_receipt_delivery_disconnect(creation_stack, same_call):
    stack = creation_stack
    args = arguments(stack)
    store = stack['creation_store']
    with patch.object(store, 'complete_tool_execution', side_effect=ConnectionError('disconnect after commit')):
        with pytest.raises(ConnectionError):
            call(stack, args, 'lost-response')
    assert _draft_count(store) == 1
    # Reopen the database as on process restart; recover from durable state only.
    reopened = InvestigationCreationStore(store.db_path)
    stack['app_service'].store = reopened
    result = call(stack, args, 'lost-response' if same_call else 'recovery')
    assert result['status'] == 'ok'
    assert _draft_count(reopened) == 1


def test_post_commit_preview_failure_is_not_a_no_write_failure(creation_stack):
    stack = creation_stack
    args = arguments(stack)
    from backend.investigation_creation.errors import ResourceStaleError
    with patch.object(stack['app_service'], 'get_draft_view', side_effect=ResourceStaleError(
            'preview failed', details={'mutation_applied': False})):
        result = call(stack, args, 'preview-failed')
        repeated = call(stack, args, 'preview-failed')
        assert repeated['error']['details']['write_status'] == 'COMMITTED'
        assert repeated['error']['details']['mutation_applied'] is True
    assert result['error']['details']['write_status'] == 'COMMITTED'
    assert result['error']['details']['draft_id']
    assert call(stack, args, 'preview-failed')['status'] == 'ok'
    assert call(stack, args, 'preview-retry')['status'] == 'ok'
    assert _draft_count(stack['creation_store']) == 1


def test_unclassified_exception_fails_closed(creation_stack):
    stack = creation_stack
    args = arguments(stack)
    with patch.object(stack['app_service'], 'create_draft', side_effect=TimeoutError('unknown write')):
        failed = call(stack, args, 'timeout')
        assert failed['error']['details']['write_status'] == 'UNKNOWN'
        assert failed['error']['details']['retryable'] is False
    result = call(stack, args, 'after-timeout')
    assert result['error']['code'] == 'MUTATION_RESULT_UNKNOWN'
    assert _draft_count(stack['creation_store']) == 0


def test_operation_binding_rolls_back_draft_on_failure(creation_stack):
    stack = creation_stack
    args = arguments(stack)
    from backend.investigation_creation.draft_operations import bind_draft
    def fail_after_binding(*a, **kw):
        bind_draft(*a, **kw)
        raise RuntimeError('commit fault')
    with patch('backend.investigation_creation.draft_operations.bind_draft', side_effect=fail_after_binding):
        assert call(stack, args, 'bind-fault')['status'] == 'error'
    assert _draft_count(stack['creation_store']) == 0
    with stack['creation_store']._connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM investigation_draft_revisions').fetchone()[0] == 0


def test_application_turn_survives_runtime_restart_and_new_intent_is_separate(creation_stack):
    stack = creation_stack
    old, args = publish_versions(stack)
    service = stack['tool_service']
    service.begin_conversation_turn('recovery-session', 'persistent-user-turn')
    try:
        assert call(stack, old, 'stale', turn='runtime-0')['error']['code'] == 'RESOURCE_STALE'
        first = call(stack, args, 'first', turn='runtime-1')
        again = call(stack, args, 'retry', turn='runtime-2')
        assert first['data']['draft']['id'] == again['data']['draft']['id']
    finally:
        service.end_conversation_turn('recovery-session')
    service.begin_conversation_turn('recovery-session', 'explicit-new-user-turn')
    try:
        second = call(stack, args, 'new', turn='runtime-3')
        assert first['data']['draft']['id'] != second['data']['draft']['id']
    finally:
        service.end_conversation_turn('recovery-session')


def test_concurrent_startup_and_migration_failure_are_safe(creation_stack):
    store = creation_stack['creation_store']
    from backend.investigation_creation.draft_operations import initialize
    with store._connect() as conn:
        conn.execute('DROP TABLE draft_creation_attempts')
        conn.execute('DROP TABLE draft_creation_operations')
        conn.execute('DROP INDEX uq_creation_turn_mutation_tool')
        conn.execute("""CREATE UNIQUE INDEX uq_creation_turn_mutation_tool
            ON investigation_creation_tool_receipts(session_id,turn_id,tool_name)
            WHERE is_mutation=1 AND tool_name NOT IN
            ('create_lexicon_edit','open_resource_edit','update_resource_edit','save_resource')""")
        old_index = conn.execute("SELECT sql FROM sqlite_master WHERE name='uq_creation_turn_mutation_tool'").fetchone()[0]
    def interrupted(conn):
        initialize(conn)
        raise RuntimeError('interrupted schema migration')
    with patch('backend.investigation_creation.draft_operations.initialize', side_effect=interrupted):
        with pytest.raises(RuntimeError):
            InvestigationCreationStore(store.db_path)
    with store._connect() as conn:
        assert conn.execute("SELECT sql FROM sqlite_master WHERE name='uq_creation_turn_mutation_tool'").fetchone()[0] == old_index
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='draft_creation_operations'").fetchone() is None
    gate = Barrier(4)
    def startup(_):
        gate.wait(timeout=10)
        InvestigationCreationStore(store.db_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(startup, range(4)))
    assert call(creation_stack, arguments(creation_stack), 'after-startup')['status'] == 'ok'
    with store._connect() as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


@pytest.mark.parametrize('legacy_status', ['FAILED_SAFE', 'SUCCEEDED', 'STARTED', 'FAILED_UNKNOWN'])
def test_legacy_migration_preserves_history_drafts_and_running_task(creation_stack, legacy_status):
    stack = creation_stack
    args = arguments(stack)
    store = stack['creation_store']
    view = stack['tool_service'].execute('create_investigation_draft', args, principal=Principal('principal-a'))
    draft_id = view['draft']['id']
    from backend.investigation_creation.contracts import ConfirmAndQueueCommand
    run = stack['app_service'].confirm_and_queue(ConfirmAndQueueCommand(
        draft_id=draft_id, expected_revision=1, confirmed=True,
        idempotency_key='existing-run',
        expected_task_settings_revision=view['confirmation_preview']['task_settings_revision'],
    ), principal=Principal('principal-a'))
    status = legacy_status.split('_')[0]
    response = ({'status': 'ok', 'data': view} if status == 'SUCCEEDED' else
                {'status': 'error', 'error': {'code': 'RESOURCE_STALE', 'details': {'mutation_applied': False}}}
                if legacy_status == 'FAILED_SAFE' else
                {'status': 'error', 'error': {'code': 'TOOL_EXECUTION_FAILED'}})
    with store._connect() as conn:
        # Construct the OLD schema in this empty synthetic operation database.
        conn.execute('DROP TABLE draft_creation_attempts')
        conn.execute('DROP TABLE draft_creation_operations')
        conn.execute('DROP INDEX uq_creation_turn_mutation_tool')
        conn.execute("""CREATE UNIQUE INDEX uq_creation_turn_mutation_tool ON investigation_creation_tool_receipts
            (session_id,turn_id,tool_name) WHERE is_mutation=1 AND tool_name NOT IN
            ('create_lexicon_edit','open_resource_edit','update_resource_edit','save_resource')""")
        conn.execute("""INSERT INTO investigation_creation_tool_receipts VALUES
            ('legacy','recovery-session','turn-create','legacy-call','principal-a',
             'create_investigation_draft',?,1,?,?,'2026-10-03','')""",
            (store.tool_arguments_fingerprint(args), status, '' if status == 'STARTED' else json.dumps(response)))
        conn.execute("UPDATE investigation_runs SET status='RUNNING', claimed_by='worker-1', claim_token='lease-token' WHERE id=?", (run.id,))
        tables = ['investigation_drafts', 'investigation_draft_revisions',
                  'investigation_runs', 'investigation_creation_tool_receipts']
        before = {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in tables}
    # Repeated startup migrations must be idempotent and preserve every old row.
    for _ in range(2):
        reopened = InvestigationCreationStore(store.db_path)
    with reopened._connect() as conn:
        assert before == {t: [tuple(r) for r in conn.execute(f'SELECT * FROM {t}')] for t in tables}
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    stack['app_service'].store = reopened
    result = call(stack, args, 'after-migration')
    if legacy_status == 'FAILED_SAFE':
        assert result['status'] == 'ok'
        assert _draft_count(reopened) == 2  # The unrelated pre-existing draft is retained.
    elif legacy_status == 'SUCCEEDED':
        assert result['status'] == 'ok', result
        assert result['data']['draft']['id'] == draft_id
        assert _draft_count(reopened) == 1
    else:
        assert result['error']['code'] == 'MUTATION_RESULT_UNKNOWN'
        assert _draft_count(reopened) == 1
    with reopened._connect() as conn:
        assert tuple(conn.execute("SELECT * FROM investigation_creation_tool_receipts WHERE receipt_id='legacy'").fetchone()) == before['investigation_creation_tool_receipts'][0]
        assert [tuple(r) for r in conn.execute('SELECT * FROM investigation_runs')] == before['investigation_runs']
