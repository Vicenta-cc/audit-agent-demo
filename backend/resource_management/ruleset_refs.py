"""Exact ruleset handles. References identify versions; they never grant adoption."""
import json
from contextlib import nullcontext
from uuid import uuid4

from .contracts import ResourceError


def initialize(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS ruleset_resource_refs (
        ref TEXT PRIMARY KEY, principal_id TEXT NOT NULL, session_id TEXT NOT NULL,
        binding_json TEXT NOT NULL, UNIQUE(principal_id,session_id,binding_json))''')


def issue(conn, binding, *, principal, session_id=''):
    encoded = json.dumps(binding, sort_keys=True, ensure_ascii=False)
    row = conn.execute('SELECT ref FROM ruleset_resource_refs WHERE principal_id=? AND session_id=? AND binding_json=?',
                       (principal.id, session_id, encoded)).fetchone()
    if row:
        return row['ref']
    ref = 'resource-ref:' + uuid4().hex
    conn.execute('INSERT INTO ruleset_resource_refs VALUES (?,?,?,?)', (ref, principal.id, session_id, encoded))
    return ref


def proposal_view(store, proposal, *, principal, session_id, connection=None):
    """Expose one exact proposal snapshot and its handle, without adopting it."""
    if proposal.session_id != session_id:
        raise ResourceError('规则不属于当前会话。', code='RESOURCE_REF_INVALID')
    with (nullcontext(connection) if connection is not None else store._connect()) as conn:
        if connection is None:
            conn.execute('BEGIN IMMEDIATE')
        current = store._owned_proposal(conn, proposal.proposal_id, session_id)
        if current.version != proposal.version or current.content_hash != proposal.content_hash:
            raise ResourceError('规则已变化，请读取并展示准确版本后再采用。', code='RESOURCE_REF_STALE')
        ref = issue(conn, {'strategy': 'temporary_ruleset',
            'proposal_id': proposal.proposal_id, 'proposal_version': proposal.version,
            'content_hash': proposal.content_hash}, principal=principal, session_id=session_id)
    return {
        'kind': 'ruleset', 'edit_id': proposal.proposal_id,
        'proposal_id': proposal.proposal_id, 'version': proposal.version,
        'resource_ref': ref, 'proposal_snapshot': proposal.model_dump(mode='json'),
        'update_arguments': {'edit_id': proposal.proposal_id, 'expected_version': proposal.version},
    }


def resolve(service, ref, *, principal, session_id, turn_id):
    with service.app.store._connect() as conn:
        row = conn.execute('SELECT * FROM ruleset_resource_refs WHERE ref=?', (ref,)).fetchone()
    if row is None or row['principal_id'] != principal.id or row['session_id'] not in ('', session_id):
        raise ResourceError('规则引用无效或不属于当前用户、会话，请读取目标规则的真实引用。',
                            code='RESOURCE_REF_INVALID', details={'mutation_applied': False})
    binding = json.loads(row['binding_json'])
    if binding['strategy'] == 'existing_ruleset':
        # The application revalidates ownership, published version and hash in
        # its resource fence immediately before writing the draft.
        return binding, None
    proposal = service.app.get_ruleset_proposal(binding['proposal_id'], session_id=session_id)
    if proposal.version != binding['proposal_version'] or proposal.content_hash != binding['content_hash']:
        raise ResourceError('规则引用版本已变化，请读取并展示新版后等待用户采用。',
                            code='RESOURCE_REF_STALE', details={'mutation_applied': False})
    from backend.investigation_creation.approval import published_presentations, reject
    if not turn_id or service.app.conversation_store is None:
        reject('PROPOSAL_APPROVAL_REQUIRED')
    with service.app.conversation_store._connect() as conn:
        current = conn.execute('''SELECT m.sequence FROM investigation_turns t
            JOIN investigation_messages m ON m.id=t.user_message_id
            WHERE t.id=? AND t.session_id=?''', (turn_id, session_id)).fetchone()
        if current is None:
            reject('PROPOSAL_APPROVAL_REQUIRED')
        records, _ = published_presentations(conn, session_id=session_id, before_sequence=current['sequence'])
    matches = [r for r in records if r['proposal_id'] == proposal.proposal_id
               and r['proposal_version'] == proposal.version and r['content_hash'] == proposal.content_hash]
    if not matches:
        reject('PROPOSAL_NOT_PRESENTED')
    # This is only lookup. Existing adoption code checks the current user turn,
    # presentation, proposal version and content again inside its transaction.
    return None, matches[-1]['presentation_id']


def saved_view(service, saved, *, principal):
    """Project an exact durable save, never retarget an edit's existing handle."""
    if saved.get('kind') != 'ruleset' or not saved.get('revision_id'):
        return saved
    with service.app.store._connect() as conn:
        ref = issue(conn, {'strategy': 'existing_ruleset', 'ruleset_revision_id': saved['revision_id'],
            'expected_ruleset_version': saved['version'],
            'expected_ruleset_content_hash': saved['content_hash']}, principal=principal)
    return {**saved, 'resource_ref': ref}
