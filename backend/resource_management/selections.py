"""Explicit UI selection receipts. Neither a resource copy nor an adoption grant.

No model tool may manufacture a confirmed user selection. Existing chat adoption
approvals and frozen runs remain the authority for what a task actually used.
"""
from datetime import datetime, timezone
import sqlite3

from pydantic import Field, model_validator
from typing import Literal

from backend.rulesets.contracts import StrictModel
from .lexicon_versions import digest
from .session_state import ResourceStateError, _read_database


class SelectionInput(StrictModel):
    event_id: str = Field(min_length=1, max_length=128)
    expected_event_id: str = Field(default='', max_length=128)
    kind: Literal['ruleset', 'lexicon']
    purpose: Literal['edit', 'view']
    key: str = Field(default='', max_length=512)
    content_hash: str = Field(default='', max_length=128)

    @model_validator(mode='after')
    def coherent_target(self):
        if bool(self.key) != bool(self.content_hash):
            raise ValueError('A selection requires an exact key and hash; clear both to deselect')
        return self


def initialize(conn):
    # Additive only. No backfill: older reads/edits are not user selections.
    conn.execute('''CREATE TABLE IF NOT EXISTS resource_selection_events (
        sequence INTEGER PRIMARY KEY,
        session_id TEXT NOT NULL, principal_id TEXT NOT NULL,
        event_id TEXT NOT NULL, previous_event_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('ruleset','lexicon')),
        purpose TEXT NOT NULL CHECK(purpose IN ('edit','view')),
        target_key TEXT NOT NULL, content_hash TEXT NOT NULL,
        request_hash TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(session_id, principal_id, event_id))''')
    conn.execute('''CREATE INDEX IF NOT EXISTS resource_selection_slot
        ON resource_selection_events(session_id, principal_id, kind, purpose, sequence DESC)''')


def receipt(row):
    return dict(event_id=row['event_id'], previous_event_id=row['previous_event_id'],
                kind=row['kind'], purpose=row['purpose'], key=row['target_key'],
                content_hash=row['content_hash'], created_at=row['created_at'], source='ui')


def project(conn, items, *, session_id, principal):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='resource_selection_events'").fetchone():
        return {'status': 'unknown'}
    rows = conn.execute('''SELECT * FROM resource_selection_events e
        WHERE session_id=? AND principal_id=? AND sequence=(
            SELECT MAX(sequence) FROM resource_selection_events s
            WHERE s.session_id=e.session_id AND s.principal_id=e.principal_id
            AND s.kind=e.kind AND s.purpose=e.purpose)
        ORDER BY kind,purpose''', (session_id, principal.id)).fetchall()
    if not rows:
        return {'status': 'unknown'}
    by_key = {i['key']: i for i in items}
    slots = []
    for row in rows:
        entry = receipt(row)
        target = by_key.get(row['target_key'])
        if not row['target_key'] and not row['content_hash']:
            entry['status'] = 'cleared'
        elif (not target or target.get('type') not in ('edit_version', 'formal_resource')
              or target.get('kind') != row['kind'] or target.get('content_hash') != row['content_hash']):
            # Never substitute the current resource or the previous selection.
            entry = {k: v for k, v in entry.items() if k not in ('key', 'content_hash')}
            entry['status'] = 'unavailable'
        else:
            entry['status'] = ('stale' if row['purpose'] == 'edit'
                               and not target.get('is_current') else 'selected')
            entry.update(title=target['title'], version=target['version'])
        slots.append(entry)
    return {'status': 'recorded', 'slots': slots}


class ResourceSelectionWriter:
    def __init__(self, store, reader):
        self.store, self.reader = store, reader

    def record(self, session_id, *, principal, **kwargs):
        data = SelectionInput.model_validate(kwargs).model_dump()
        request_hash = digest(data)
        try:
            # Workspace deletion locks this creation DB in its attached
            # transaction. Acquire it before reading ownership, so a stale
            # authorization snapshot cannot recreate events after deletion.
            with self.store._connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                with _read_database(self.reader.conversation_db) as conversation:
                    self.reader.authorize(conversation, session_id, principal)
                    old = conn.execute('''SELECT * FROM resource_selection_events
                        WHERE session_id=? AND principal_id=? AND event_id=?''',
                        (session_id, principal.id, data['event_id'])).fetchone()
                    if old:
                        if old['request_hash'] != request_hash:
                            raise ResourceStateError('RESOURCE_SELECTION_CONFLICT', '选择操作已用于其他请求，请重新读取当前选择。', 409)
                        return {'event': receipt(old), 'replayed': True}
                    previous = conn.execute('''SELECT event_id FROM resource_selection_events
                        WHERE session_id=? AND principal_id=? AND kind=? AND purpose=?
                        ORDER BY sequence DESC LIMIT 1''',
                        (session_id, principal.id, data['kind'], data['purpose'])).fetchone()
                    if (previous[0] if previous else '') != data['expected_event_id']:
                        raise ResourceStateError('RESOURCE_SELECTION_CONFLICT', '当前选择已在其他页面改变，请刷新后重新选择。', 409)
                    if data['key']:
                        with _read_database(self.reader.resource_db) as resources:
                            items, _ = self.reader._project(conn, resources, session_id, principal)
                        target = next((i for i in items if i['key'] == data['key']), None)
                        if not target:
                            raise ResourceStateError('RESOURCE_STATE_NOT_FOUND', '资源版本不存在或不属于当前会话。', 404)
                        if (target['type'] not in ('edit_version', 'formal_resource')
                                or target.get('kind') != data['kind']):
                            raise ResourceStateError('RESOURCE_SELECTION_INVALID', '请选择对应的规则或词库版本。', 422)
                        if target['content_hash'] != data['content_hash']:
                            raise ResourceStateError('RESOURCE_SELECTION_STALE', '内容已经变化，请重新读取。', 409)
                        # Current edits and selections share the same locked DB.
                        # Formal versions can be viewed; editing requires the existing open/edit flow.
                        if data['purpose'] == 'edit' and (target['type'] != 'edit_version' or not target['is_current']):
                            raise ResourceStateError('RESOURCE_SELECTION_STALE', '该版本不可继续编辑，请先读取当前编辑稿。', 409)
                    conn.execute('''INSERT INTO resource_selection_events
                        (session_id,principal_id,event_id,previous_event_id,kind,purpose,target_key,
                         content_hash,request_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)''',
                        (session_id, principal.id, data['event_id'], data['expected_event_id'], data['kind'],
                         data['purpose'], data['key'], data['content_hash'], request_hash,
                         datetime.now(timezone.utc).isoformat()))
                    row = conn.execute('''SELECT * FROM resource_selection_events
                        WHERE session_id=? AND principal_id=? AND event_id=?''',
                        (session_id, principal.id, data['event_id'])).fetchone()
                    return {'event': receipt(row), 'replayed': False}
        except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ResourceStateError('RESOURCE_STATE_UNAVAILABLE', '选择暂时无法确认，请读取当前状态后重试。', 503) from exc
