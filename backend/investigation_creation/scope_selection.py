"""Explicit user region selections, committed without a model conversation turn."""
from __future__ import annotations

import hashlib
import json

from pydantic import Field, StrictInt, StrictStr, field_validator

from .contracts import DraftStatus, InvestigationDraftConfiguration
from .errors import (
    ConfigurationValidationError, DraftAlreadyConfirmedError,
    DraftRevisionConflictError, IdempotencyConflictError, PrincipalAccessDeniedError,
)
from . import rounds, scope_intent


class RegionSelection(scope_intent.ScopeModel):
    expected_revision: StrictInt = Field(ge=1)
    operation_id: StrictStr = Field(min_length=1, max_length=200)
    target_text: StrictStr = Field(min_length=1, max_length=300)
    clarification_target_text: StrictStr | None = Field(default=None, min_length=1, max_length=300)

    @field_validator('operation_id', 'target_text', 'clarification_target_text', mode='before')
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class RegionSelectionHistory(scope_intent.ScopeModel):
    """A real UI operation and its immutable draft context, not a chat turn."""
    event_id: str
    draft_id: str
    previous_revision: int
    revision: int
    after_sequence: int
    created_at: str
    question: str
    previous_target: scope_intent.RegionName | None
    previous_state: scope_intent.PublicRelevanceScopeState | None
    selected_target: scope_intent.RegionName
    selected_province: scope_intent.RegionName


def history(application, *, session_id, principal):
    """Recover questions/candidates from the exact revisions each user edited.

    Existing selections already retain both revision numbers. Reading those
    snapshots also restores pre-feature history without inventing new events or
    consulting today's catalog, which might differ from the one the user saw.
    """
    conversation = application.conversation_store
    if conversation is None:
        return ()
    with application.store._connect() as db:
        db.execute('ATTACH DATABASE ? AS conversation', (str(conversation.db_path),))
        session = db.execute('''SELECT 1 FROM conversation.investigation_sessions
            WHERE id=? AND owner_principal=? AND scope_type='creation' ''',
            (session_id, principal.id)).fetchone()
        if session is None:
            raise PrincipalAccessDeniedError('调查会话不可访问。')
        rows = db.execute('''SELECT s.*, b.configuration_json AS before_json,
                a.configuration_json AS after_json
            FROM investigation_region_selections s
            JOIN investigation_drafts d ON d.id=s.draft_id AND d.owner_principal=s.principal
            JOIN investigation_draft_revisions b
                ON b.draft_id=s.draft_id AND b.revision=s.expected_revision
            JOIN investigation_draft_revisions a
                ON a.draft_id=s.draft_id AND a.revision=s.committed_revision
            WHERE s.session_id=? AND s.principal=?
            ORDER BY s.conversation_sequence, s.created_at, s.committed_revision''',
            (session_id, principal.id)).fetchall()
    result = []
    for row in rows:
        before = json.loads(row['before_json'])
        after = json.loads(row['after_json'])
        original_state = before.get('relevance_scope_state')
        raw_state = (json.loads(row['clarification_json'])
                     if row['clarification_json'] else original_state)
        state = (scope_intent.PublicRelevanceScopeState.model_validate({
            key: value for key, value in raw_state.items()
            if key in scope_intent.PublicRelevanceScopeState.model_fields
        }) if raw_state else None)
        previous = before.get('relevance_scope')
        if original_state and original_state['status'] in {'pending', 'cleared'}:
            previous = None
        selected = after['relevance_scope']
        identity = json.dumps([session_id, row['operation_id']], ensure_ascii=False)
        result.append(RegionSelectionHistory(
            event_id='region-selection:' + hashlib.sha256(identity.encode()).hexdigest(),
            draft_id=row['draft_id'], previous_revision=row['expected_revision'],
            revision=row['committed_revision'], after_sequence=row['conversation_sequence'],
            created_at=row['created_at'],
            question=(f'“{state.target_text}”指哪个调查范围？'
                      if state and state.target_text else '请选择或填写调查地域'),
            previous_target=(scope_intent.RegionName.model_validate(previous['target'])
                             if previous else None),
            previous_state=state,
            selected_target=scope_intent.RegionName.model_validate(selected['target']),
            selected_province=scope_intent.RegionName.model_validate(selected['province']),
        ))
    return tuple(result)


def select(application, draft_id, *, session_id, principal, **values):
    command = RegionSelection(**values)
    conversation = application.conversation_store
    if conversation is None:
        raise ConfigurationValidationError('当前调查会话不可用。', code='REGION_SOURCE_INVALID')
    store = application.store
    with store._connect() as db:
        db.execute('ATTACH DATABASE ? AS conversation', (str(conversation.db_path),))
        # The attached conversation is read-only here. Its read lock and the
        # creation write lock keep membership/busy checks valid until commit.
        db.execute('BEGIN IMMEDIATE')
        session = db.execute('''SELECT * FROM conversation.investigation_sessions
            WHERE id=? AND owner_principal=? AND scope_type='creation' AND status='active' ''',
            (session_id, principal.id)).fetchone()
        if session is None:
            raise PrincipalAccessDeniedError('调查会话不可访问。')
        members = rounds.draft_ids(db, session_id, principal.id)
        if not members or draft_id != members[-1]:
            raise PrincipalAccessDeniedError('只能修改当前会话最新草案。')
        draft = store._draft(store._owned_draft_row(db, draft_id, principal.id))
        previous = db.execute('''SELECT * FROM investigation_region_selections
            WHERE principal=? AND session_id=? AND operation_id=?''',
            (principal.id, session_id, command.operation_id)).fetchone()
        if previous is not None:
            prior_clarification = (json.loads(previous['clarification_json'])['target_text']
                                   if previous['clarification_json'] else None)
            if (previous['draft_id'], previous['expected_revision'], previous['target_text']) != (
                    draft_id, command.expected_revision, command.target_text) or (
                    prior_clarification != command.clarification_target_text):
                raise IdempotencyConflictError('该地域操作标识已用于其他修改。')
            # Return today's state, never replay an old configuration over it.
            return draft
        if draft.status is not DraftStatus.DRAFT:
            raise DraftAlreadyConfirmedError(draft_id)
        if db.execute('''SELECT 1 FROM conversation.investigation_turns
                WHERE session_id=? AND status='running' LIMIT 1''', (session_id,)).fetchone():
            raise ConfigurationValidationError('当前对话正在处理中，请完成后再修改地域。',
                code='REGION_SELECTION_BUSY', details={'mutation_applied': False})
        if draft.current_revision != command.expected_revision:
            raise DraftRevisionConflictError('草案已更新，请刷新后重新选择地域。')
        configuration = draft.configuration
        if not isinstance(configuration, InvestigationDraftConfiguration):
            raise ConfigurationValidationError('该历史草案暂不支持地域编辑。')
        resolved = scope_intent.resolve_region(command.target_text)
        if resolved['status'] != 'resolved':
            public = scope_intent.PublicRelevanceScopeState.model_validate({
                key: value for key, value in resolved.items()
                if key in scope_intent.PublicRelevanceScopeState.model_fields
            })
            raise ConfigurationValidationError(resolved['reason'], code='REGION_SELECTION_UNRESOLVED',
                details={'mutation_applied': False, 'region_resolution': {
                    **public.model_dump(mode='json'), 'target_text': command.target_text}})
        clarification_json = ''
        if command.clarification_target_text is not None:
            clarification = scope_intent.resolve_region(command.clarification_target_text)
            if clarification['status'] != 'pending':
                raise ConfigurationValidationError('地域澄清上下文已变化，请重新验证地域。',
                    code='REGION_CLARIFICATION_INVALID', details={'mutation_applied': False})
            public = scope_intent.PublicRelevanceScopeState.model_validate({
                key: value for key, value in clarification.items()
                if key in scope_intent.PublicRelevanceScopeState.model_fields
            }).model_copy(update={'target_text': command.clarification_target_text})
            clarification_json = public.model_dump_json()
        sequence = db.execute('''SELECT COALESCE(MAX(sequence), 0)
            FROM conversation.investigation_messages WHERE session_id=?''', (session_id,)).fetchone()[0]
        state = configuration.relevance_scope_state
        scope = configuration.relevance_scope
        policy = ((state.selection_policy if state else None)
                  or (scope.selection_policy if scope else None) or 'local_first_with_supplement')
        selected = scope_intent.RelevanceScopeState(**resolved, principal=principal.id,
            session_id=session_id, draft_id=draft_id, source_kind='ui_selection',
            source_operation_id=command.operation_id, source_sequence=sequence,
            target_text=command.target_text, selection_policy=policy)
        selected_scope = scope_intent.RelevanceScope(target=selected.target,
            province=selected.province, selection_policy=policy)
        updated = store.update_draft(draft_id, principal=principal.id,
            expected_revision=command.expected_revision, _connection=db,
            configuration=configuration.model_copy(update={
                'relevance_scope': selected_scope, 'relevance_scope_state': selected}))
        db.execute('''INSERT INTO investigation_region_selections
            (principal, session_id, operation_id, draft_id, expected_revision, committed_revision,
             target_text, region_code, catalog_version, conversation_sequence, created_at, clarification_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (principal.id, session_id, command.operation_id, draft_id, command.expected_revision,
             updated.current_revision, command.target_text, selected.target_code,
             selected.catalog_version, sequence, store._now_text(), clarification_json))
        return updated
