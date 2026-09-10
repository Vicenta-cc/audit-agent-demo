"""Session-scoped opaque references bound to one TaskSnapshot."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, replace
from typing import Literal

from hermes_m0.refs import ReferenceError


TaskReferenceKind = Literal["post", "evidence"]


@dataclass(frozen=True)
class TaskSessionScope:
    session_id: str
    task_id: str
    snapshot_id: str
    revision: str
    generation: int


@dataclass(frozen=True)
class TaskReferenceRecord:
    token: str
    session_id: str
    task_id: str
    snapshot_id: str
    revision: str
    generation: int
    kind: TaskReferenceKind
    object_id: str
    parent_id: str | None
    source_tool: str
    result_kind: str
    content_state: str


@dataclass(frozen=True)
class TaskSearchCursorRecord:
    token: str
    session_id: str
    task_id: str
    snapshot_id: str
    revision: str
    generation: int
    query_fingerprint: str
    ordered_post_ids_hash: str
    next_offset: int
    source_tool: str
    result_kind: str


class TaskReferenceBatch:
    def __init__(
        self, registry: "TaskReferenceRegistry", scope: TaskSessionScope
    ) -> None:
        self._registry = registry
        self.scope = scope
        self._staged: dict[str, TaskReferenceRecord] = {}
        self._committed = False

    def expose(
        self,
        *,
        kind: TaskReferenceKind,
        object_id: str,
        parent_id: str | None,
        source_tool: str,
        result_kind: str,
        content_state: str,
    ) -> str:
        if self._committed:
            raise RuntimeError("Reference batch is already committed")
        existing = self._registry.ref_for(
            self.scope.session_id, kind, object_id, required=False
        )
        if existing is not None:
            return existing
        token = self._registry._allocate_token(kind)
        self._staged[token] = TaskReferenceRecord(
            token=token,
            session_id=self.scope.session_id,
            task_id=self.scope.task_id,
            snapshot_id=self.scope.snapshot_id,
            revision=self.scope.revision,
            generation=self.scope.generation,
            kind=kind,
            object_id=object_id,
            parent_id=parent_id,
            source_tool=source_tool,
            result_kind=result_kind,
            content_state=content_state,
        )
        return token

    def commit(self) -> None:
        if not self._committed:
            self._registry._commit(self.scope, tuple(self._staged.values()))
            self._committed = True


class TaskReferenceRegistry:
    def __init__(self, *, nonce: str | None = None) -> None:
        self._nonce = nonce or secrets.token_hex(3)
        self._lock = threading.RLock()
        self._counter = 0
        self._scopes: dict[str, TaskSessionScope] = {}
        self._records: dict[str, TaskReferenceRecord] = {}
        self._by_object: dict[tuple[str, int, TaskReferenceKind, str], str] = {}
        self._search_cursors: dict[str, TaskSearchCursorRecord] = {}

    def bind(
        self,
        *,
        session_id: str,
        task_id: str,
        snapshot_id: str,
        revision: str,
        force_new_generation: bool = False,
    ) -> TaskSessionScope:
        if not session_id:
            raise ReferenceError("scope_unbound", "Hermes session_id is required")
        with self._lock:
            current = self._scopes.get(session_id)
            same = current is not None and (
                current.task_id,
                current.snapshot_id,
                current.revision,
            ) == (task_id, snapshot_id, revision)
            if same and not force_new_generation:
                return current
            scope = TaskSessionScope(
                session_id=session_id,
                task_id=task_id,
                snapshot_id=snapshot_id,
                revision=revision,
                generation=(current.generation + 1 if current else 1),
            )
            self._scopes[session_id] = scope
            return scope

    def scope(self, session_id: str) -> TaskSessionScope:
        with self._lock:
            scope = self._scopes.get(session_id)
        if scope is None:
            raise ReferenceError(
                "investigation_scope_unbound",
                "This Hermes session has no bound TaskSnapshot.",
            )
        return scope

    def has_scope(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._scopes

    def batch(self, session_id: str) -> TaskReferenceBatch:
        return TaskReferenceBatch(self, self.scope(session_id))

    def resolve(
        self,
        session_id: str,
        token: str,
        *,
        expected_kind: TaskReferenceKind,
    ) -> TaskReferenceRecord:
        scope = self.scope(session_id)
        with self._lock:
            record = self._records.get(token)
        if record is None:
            raise ReferenceError(
                "unknown_ref",
                "The reference was not produced by a ToolResult in this runtime.",
            )
        if record.session_id != session_id:
            raise ReferenceError(
                "cross_scope_ref", "The reference belongs to another task session."
            )
        if record.generation != scope.generation or record.revision != scope.revision:
            raise ReferenceError(
                "stale_revision_ref", "The reference belongs to an older task revision."
            )
        if record.task_id != scope.task_id or record.snapshot_id != scope.snapshot_id:
            raise ReferenceError(
                "cross_scope_ref", "The reference belongs to another TaskSnapshot."
            )
        if record.kind != expected_kind:
            raise ReferenceError(
                "wrong_ref_type",
                f"Expected a {expected_kind} reference, but received {record.kind}.",
            )
        return record

    def ref_for(
        self,
        session_id: str,
        kind: TaskReferenceKind,
        object_id: str,
        *,
        required: bool = True,
    ) -> str | None:
        scope = self.scope(session_id)
        with self._lock:
            token = self._by_object.get((session_id, scope.generation, kind, object_id))
        if token is None and required:
            raise ReferenceError(
                "unknown_ref", "The object has not been exposed in this session."
            )
        return token

    def issue_search_cursor(
        self,
        session_id: str,
        *,
        query_fingerprint: str,
        ordered_post_ids_hash: str,
        next_offset: int,
    ) -> str:
        scope = self.scope(session_id)
        with self._lock:
            token = self._allocate_cursor_token()
            self._search_cursors[token] = TaskSearchCursorRecord(
                token=token,
                session_id=scope.session_id,
                task_id=scope.task_id,
                snapshot_id=scope.snapshot_id,
                revision=scope.revision,
                generation=scope.generation,
                query_fingerprint=query_fingerprint,
                ordered_post_ids_hash=ordered_post_ids_hash,
                next_offset=next_offset,
                source_tool="search_posts",
                result_kind="post_search_results",
            )
        return token

    def resolve_search_cursor(
        self,
        session_id: str,
        token: str,
        *,
        query_fingerprint: str,
        ordered_post_ids_hash: str,
    ) -> TaskSearchCursorRecord:
        scope = self.scope(session_id)
        with self._lock:
            record = self._search_cursors.get(token)
        if record is None:
            raise ReferenceError(
                "unknown_cursor",
                "The cursor was not produced by search_posts in this runtime.",
            )
        if record.session_id != session_id:
            raise ReferenceError(
                "cross_scope_cursor", "The cursor belongs to another task session."
            )
        if record.generation != scope.generation or record.revision != scope.revision:
            raise ReferenceError(
                "stale_revision_cursor", "The cursor belongs to an older task revision."
            )
        if record.task_id != scope.task_id or record.snapshot_id != scope.snapshot_id:
            raise ReferenceError(
                "cross_scope_cursor", "The cursor belongs to another TaskSnapshot."
            )
        if (
            record.source_tool != "search_posts"
            or record.result_kind != "post_search_results"
        ):
            raise ReferenceError(
                "invalid_cursor_source",
                "The cursor was not produced by a post search result.",
            )
        if record.query_fingerprint != query_fingerprint:
            raise ReferenceError(
                "cursor_query_mismatch",
                "The cursor does not belong to this query and filter set.",
            )
        if record.ordered_post_ids_hash != ordered_post_ids_hash:
            raise ReferenceError(
                "cursor_order_mismatch",
                "The frozen search candidate order has changed.",
            )
        return record

    def replace_record_for_test(self, token: str, **updates: object) -> None:
        with self._lock:
            self._records[token] = replace(self._records[token], **updates)

    def replace_cursor_for_test(self, token: str, **updates: object) -> None:
        with self._lock:
            self._search_cursors[token] = replace(
                self._search_cursors[token], **updates
            )

    def _allocate_token(self, kind: TaskReferenceKind) -> str:
        with self._lock:
            self._counter += 1
            return f"{'p' if kind == 'post' else 'e'}{self._counter}_{self._nonce}"

    def _allocate_cursor_token(self) -> str:
        self._counter += 1
        return f"c{self._counter}_{self._nonce}"

    def _commit(
        self, scope: TaskSessionScope, records: tuple[TaskReferenceRecord, ...]
    ) -> None:
        with self._lock:
            if self._scopes.get(scope.session_id) != scope:
                raise ReferenceError(
                    "stale_revision_ref",
                    "Task scope changed before ToolResult emission.",
                )
            for record in records:
                key = (
                    record.session_id,
                    record.generation,
                    record.kind,
                    record.object_id,
                )
                existing = self._by_object.get(key)
                if existing is not None and existing != record.token:
                    raise RuntimeError("Concurrent task reference publication conflict")
                self._records[record.token] = record
                self._by_object[key] = record.token
