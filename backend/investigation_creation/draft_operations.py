"""Draft creation operations; tool receipts remain the immutable attempt history.

Both draft-creating tools use this protocol; adopting into an existing draft does
not. Operation identity is supplied by the application turn (or the runtime turn
for non-conversation callers), never
by model arguments. A new user turn is not guessed to be a retry of an old intent.
"""
from __future__ import annotations

from contextlib import contextmanager

import hashlib
import json
from uuid import uuid4

from .errors import IdempotencyConflictError

TOOL = "create_investigation_draft"
ADOPT_TOOL = "use_ruleset_proposal"


def is_creation(tool_name, arguments):
    return tool_name == TOOL or (tool_name == ADOPT_TOOL
        and bool(arguments.get('create_draft')) and not arguments.get('draft_id'))


@contextmanager
def validation_phase(enabled=True):
    """Only wrap code known to run before writes, or a fully rolled-back transaction.

    An arbitrary timeout/programming error is deliberately not proof of no write.
    Callers must never wrap post-commit reads or preview generation in this guard.
    """
    from pydantic import ValidationError
    from .errors import InvestigationCreationError, ConfigurationValidationError, PrincipalAccessDeniedError
    from backend.resource_management.contracts import ResourceError
    try:
        yield
    except (ValidationError, InvestigationCreationError, ResourceError) as exc:
        if not enabled:
            raise
        if isinstance(exc, ValidationError):
            exc = ConfigurationValidationError('Resolved tool arguments do not match the required schema.',
                code='INVALID_TOOL_ARGUMENTS', details={'validation_errors': [
                    {**error, 'loc': list(error['loc'])} for error in exc.errors(
                        include_url=False, include_context=False, include_input=False)]})
        # Safety to attempt again and authority to perform the operation are
        # separate: denial/presentation failures never suggest automatic retry.
        denied = isinstance(exc, PrincipalAccessDeniedError) or exc.code in {
            'RESOURCE_REF_INVALID', 'PROPOSAL_APPROVAL_REQUIRED', 'PROPOSAL_NOT_PRESENTED',
            'INVALID_PROPOSAL_PRESENTATION', 'PROPOSAL_PRESENTATION_STALE',
            'DRAFT_NOT_AUTHORIZED', 'DRAFT_NOT_EDITABLE', 'DRAFT_TARGET_REQUIRED',
        }
        exc.details.update(write_status='NOT_STARTED', mutation_applied=False,
                           draft_created=False, retryable=not denied)
        raise exc

def initialize(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS draft_creation_operations (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, principal TEXT NOT NULL,
        scope TEXT NOT NULL, state TEXT NOT NULL
            CHECK(state IN ('READY','EXECUTING','CREATED','UNKNOWN')),
        active_receipt_id TEXT NOT NULL DEFAULT '',
        arguments_fingerprint TEXT NOT NULL DEFAULT '',
        draft_id TEXT UNIQUE REFERENCES investigation_drafts(id),
        result_json TEXT NOT NULL DEFAULT '',
        UNIQUE(session_id, principal, scope))""")
    columns = {row[1] for row in connection.execute('PRAGMA table_info(draft_creation_operations)')}
    if 'result_json' not in columns:
        connection.execute("ALTER TABLE draft_creation_operations ADD COLUMN result_json TEXT NOT NULL DEFAULT ''")
    connection.execute("""CREATE TABLE IF NOT EXISTS draft_creation_attempts (
        receipt_id TEXT PRIMARY KEY REFERENCES investigation_creation_tool_receipts(receipt_id),
        operation_id TEXT NOT NULL REFERENCES draft_creation_operations(id))""")
    index = connection.execute("SELECT sql FROM sqlite_master WHERE name='uq_creation_turn_mutation_tool'").fetchone()
    if index and (TOOL not in index[0] or ADOPT_TOOL not in index[0]):
        connection.execute('DROP INDEX uq_creation_turn_mutation_tool')
    connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_creation_turn_mutation_tool
        ON investigation_creation_tool_receipts(session_id,turn_id,tool_name)
        WHERE is_mutation=1 AND tool_name NOT IN
        ('create_lexicon_edit','open_resource_edit','update_resource_edit','save_resource',
         'create_investigation_draft','use_ruleset_proposal')""")


def known_no_write(response):
    # A generic FAILED/timeout is NOT proof of rollback. Only application
    # resource validation errors carrying the explicit pre-write guarantee qualify.
    error = response.get('error', {})
    return (response.get('status') == 'error'
            and (error.get('details', {}).get('write_status') == 'NOT_STARTED'
                 or error.get('code') in {  # Historical receipts preceding phase markers.
                     'RESOURCE_STALE', 'INVALID_RULESET_REFERENCE', 'INVALID_RESOURCE_REFERENCE',
                 })
            and error.get('details', {}).get('mutation_applied') is False)


def unknown():
    raise IdempotencyConflictError(
        'Draft creation is executing or its outcome is unknown; inspect the existing operation.',
        code='MUTATION_RESULT_UNKNOWN',
        details={'write_status': 'UNKNOWN', 'retryable': False},
    )


def begin(store, identity, arguments, application_turn_id):
    scope = ('application:' + application_turn_id if application_turn_id
             else 'runtime:' + identity['turn_id'])
    operation_id = 'draft-create:' + hashlib.sha256(json.dumps(
        [identity['session_id'], identity['principal'], scope]).encode()).hexdigest()
    fingerprint = store.tool_arguments_fingerprint(arguments)
    with store._connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        exact = conn.execute("""SELECT * FROM investigation_creation_tool_receipts
            WHERE session_id=? AND turn_id=? AND tool_call_id=?""",
            (identity['session_id'], identity['turn_id'], identity['tool_call_id'])).fetchone()
        if exact and (exact['principal'] != identity['principal']
                      or exact['tool_name'] != identity['tool_name']
                      or exact['arguments_fingerprint'] != fingerprint):
            raise IdempotencyConflictError('tool execution identity was reused with different input')
        if not application_turn_id:
            turns = conn.execute("""SELECT DISTINCT b.application_turn_id
                FROM ruleset_proposal_conversation_bindings b
                JOIN investigation_creation_tool_receipts r ON r.receipt_id=b.receipt_id
                WHERE r.session_id=? AND r.principal=? AND r.turn_id=?""",
                (identity['session_id'], identity['principal'], identity['turn_id'])).fetchall()
            if len(turns) > 1:
                unknown()
            if turns:
                application_turn_id = turns[0]['application_turn_id']
                scope = 'application:' + application_turn_id
                operation_id = 'draft-create:' + hashlib.sha256(json.dumps(
                    [identity['session_id'], identity['principal'], scope]).encode()).hexdigest()
        # Exact delivery after a runtime restart still belongs to its recorded operation.
        if exact:
            bound = conn.execute('SELECT operation_id FROM draft_creation_attempts WHERE receipt_id=?',
                                 (exact['receipt_id'],)).fetchone()
            if bound:
                operation_id = bound['operation_id']
            elif not application_turn_id:
                bound_turn = conn.execute('SELECT application_turn_id FROM ruleset_proposal_conversation_bindings WHERE receipt_id=?',
                                          (exact['receipt_id'],)).fetchone()
                if bound_turn:
                    application_turn_id = bound_turn['application_turn_id']
                    scope = 'application:' + application_turn_id
                    operation_id = 'draft-create:' + hashlib.sha256(json.dumps(
                        [identity['session_id'], identity['principal'], scope]).encode()).hexdigest()
        op = conn.execute('SELECT * FROM draft_creation_operations WHERE id=?', (operation_id,)).fetchone()
        legacy = conn.execute("""SELECT r.* FROM investigation_creation_tool_receipts r
            LEFT JOIN ruleset_proposal_conversation_bindings b ON b.receipt_id=r.receipt_id
            WHERE r.session_id=? AND r.principal=? AND r.tool_name IN (?,?)
            AND (r.turn_id=? OR (?<>'' AND b.application_turn_id=?))
            AND NOT EXISTS (SELECT 1 FROM draft_creation_attempts a WHERE a.receipt_id=r.receipt_id)""",
            (identity['session_id'], identity['principal'], TOOL, ADOPT_TOOL, identity['turn_id'],
             application_turn_id, application_turn_id)).fetchall()
        # An upgraded ordinary-create operation may coexist with an old adoption
        # receipt. Do not ignore a possible second historical write or guess a winner.
        if op and op['state'] == 'READY' and any(
                not known_no_write(json.loads(old['response_json'] or '{}')) for old in legacy):
            unknown()
        if op is None:
            # Adopt old receipts lazily, without rewriting them. Multiple legacy
            # runtime attempts in one application turn are ambiguous: fail closed.
            state, draft_id, accepted, active = 'READY', None, '', ''
            if legacy:
                state = 'UNKNOWN'
                if len(legacy) == 1:
                    old = legacy[0]
                    response = json.loads(old['response_json'] or '{}')
                    accepted, active = old['arguments_fingerprint'], old['receipt_id']
                    candidate = None
                    if old['tool_name'] == ADOPT_TOOL:
                        approvals = conn.execute("""SELECT DISTINCT draft_id FROM ruleset_proposal_approvals
                            WHERE session_id=? AND runtime_turn_id=? AND tool_call_id=? AND draft_revision=1""",
                            (old['session_id'], old['turn_id'], old['tool_call_id'])).fetchall()
                        if len(approvals) == 1:
                            candidate = approvals[0]['draft_id']
                    if candidate is None and old['status'] == 'FAILED' and known_no_write(response):
                        state = 'READY'
                    elif old['status'] == 'SUCCEEDED':
                        candidate = response.get('data', {}).get('draft', {}).get('id')
                    if candidate and conn.execute(
                        'SELECT 1 FROM investigation_drafts WHERE id=? AND owner_principal=?',
                        (candidate, identity['principal'])).fetchone():
                        if conn.execute('SELECT 1 FROM draft_creation_operations WHERE draft_id=?', (candidate,)).fetchone():
                            unknown()
                        state, draft_id = 'CREATED', candidate
            conn.execute('''INSERT INTO draft_creation_operations
                         (id,session_id,principal,scope,state,active_receipt_id,arguments_fingerprint,draft_id)
                         VALUES (?,?,?,?,?,?,?,?)''',
                         (operation_id, identity['session_id'], identity['principal'], scope,
                          state, active, accepted, draft_id))
            for old in legacy:
                conn.execute('INSERT INTO draft_creation_attempts VALUES (?,?)',
                             (old['receipt_id'], operation_id))
            op = conn.execute('SELECT * FROM draft_creation_operations WHERE id=?', (operation_id,)).fetchone()
        if exact:
            # Exact deliveries replay their original result, including old failures.
            # A committed draft with a missing/failed response can be reconstructed.
            prior_response = json.loads(exact['response_json'] or '{}')
            if (op['state'] == 'CREATED' and fingerprint == op['arguments_fingerprint']
                    and (exact['status'] == 'STARTED' or
                         (exact['status'] == 'FAILED' and exact['receipt_id'] == op['active_receipt_id']) or prior_response.get('error', {})
                         .get('details', {}).get('write_status') == 'COMMITTED')):
                return _attempt(op, exact['receipt_id'])
            if exact['response_json']:
                return dict(receipt_id=exact['receipt_id'], status=exact['status'],
                            response=json.loads(exact['response_json']), replay=True)
            unknown()
        if op['state'] in ('EXECUTING', 'UNKNOWN'):
            unknown()
        accepted_tool = conn.execute('SELECT tool_name FROM investigation_creation_tool_receipts WHERE receipt_id=?',
                                     (op['active_receipt_id'],)).fetchone()
        if op['state'] == 'CREATED' and (fingerprint != op['arguments_fingerprint']
                or not accepted_tool or accepted_tool['tool_name'] != identity['tool_name']):
            raise IdempotencyConflictError('This creation operation already created a draft.',
                details={'write_status': 'COMMITTED', 'draft_id': op['draft_id'], 'retryable': False,
                         'mutation_applied': True, 'recovery': 'Read the existing draft. To adopt different rules, target that draft explicitly with its expected revision; do not create again.'})
        receipt_id = 'creation-tool-receipt:' + uuid4().hex
        conn.execute("""INSERT INTO investigation_creation_tool_receipts
            (receipt_id,session_id,turn_id,tool_call_id,principal,tool_name,
             arguments_fingerprint,is_mutation,status,created_at)
            VALUES (?,?,?,?,?,?,?,1,'STARTED',?)""",
            (receipt_id, identity['session_id'], identity['turn_id'], identity['tool_call_id'],
             identity['principal'], identity['tool_name'], fingerprint, store.clock().isoformat()))
        conn.execute('INSERT INTO draft_creation_attempts VALUES (?,?)', (receipt_id, operation_id))
        if application_turn_id:
            store._bind_proposal_conversation(conn, receipt_id, application_turn_id)
        if op['state'] == 'READY':
            conn.execute("""UPDATE draft_creation_operations SET state='EXECUTING',
                active_receipt_id=?, arguments_fingerprint=? WHERE id=?""",
                (receipt_id, fingerprint, operation_id))
        return _attempt(op, receipt_id)


def _attempt(op, receipt_id):
    return dict(receipt_id=receipt_id, operation_id=op['id'], draft_id=op['draft_id'],
                status='STARTED', response=None, replay=False)


def finish(conn, receipt_id, response):
    op = conn.execute("""SELECT o.* FROM draft_creation_operations o
        JOIN draft_creation_attempts a ON a.operation_id=o.id WHERE a.receipt_id=?""",
        (receipt_id,)).fetchone()
    if op is None:
        return False
    if op['draft_id']:
        if response.get('status') == 'ok':
            # Retain the original failed attempt, but make a verified successful
            # recovery available to conversation checkpoints as the operation result.
            conn.execute("UPDATE draft_creation_operations SET result_json=? WHERE id=? AND result_json=''",
                         (json.dumps(response, ensure_ascii=False, sort_keys=True), op['id']))
        # A post-commit preview/transport error cannot release the operation.
        if response.get('status') == 'error':
            response['error'].setdefault('details', {}).update(
                write_status='COMMITTED', draft_id=op['draft_id'], retryable=True,
                mutation_applied=True, draft_created=True,
                recovery='Retry the same creation arguments to retrieve the existing draft; do not create another operation.')
    elif op['active_receipt_id'] == receipt_id:
        safe = known_no_write(response)
        conn.execute('UPDATE draft_creation_operations SET state=? WHERE id=?',
                     ('READY' if safe else 'UNKNOWN', op['id']))
        if safe:
            response['error']['details'].update(
                write_status='NOT_STARTED', draft_created=False)
            response['error']['details'].setdefault('retryable', True)
            response['error']['details'].setdefault('recovery',
                'Read the current resource, check any content changes, and retry with corrected arguments in this operation.')
        elif response.get('status') == 'error':
            response['error'].setdefault('details', {}).update(
                write_status='UNKNOWN', retryable=False,
                recovery='Inspect the existing creation operation before retrying; do not create another operation.')
    return True


def bind_draft(conn, context, principal, draft_id):
    operation_id, receipt_id = context
    changed = conn.execute("""UPDATE draft_creation_operations SET state='CREATED', draft_id=?
        WHERE id=? AND principal=? AND state='EXECUTING' AND active_receipt_id=? AND draft_id IS NULL""",
        (draft_id, operation_id, principal, receipt_id)).rowcount
    if changed != 1:
        raise IdempotencyConflictError('Draft creation attempt no longer owns this operation.')
