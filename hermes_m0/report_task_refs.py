"""Opaque references for one task-bound report, snapshot, and revision."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, replace
from typing import Literal

from hermes_m0.refs import ReferenceError


ReportTaskReferenceKind = Literal[
    "category", "finding", "post", "comment", "evidence"
]


@dataclass(frozen=True)
class ReportTaskSessionScope:
    session_id: str
    task_id: str
    report_version_id: str
    snapshot_id: str
    revision: str
    snapshot_hash: str
    content_hash: str
    generation: int


@dataclass(frozen=True)
class ReferenceOrigin:
    parent_id: str | None
    source_tool: str
    result_kind: str
    content_state: str


@dataclass(frozen=True)
class ReportTaskReferenceRecord:
    token: str
    session_id: str
    task_id: str
    report_version_id: str
    snapshot_id: str
    revision: str
    snapshot_hash: str
    content_hash: str
    generation: int
    kind: ReportTaskReferenceKind
    object_id: str
    origins: tuple[ReferenceOrigin, ...]


@dataclass(frozen=True)
class ReportTaskSearchCursorRecord:
    token: str
    session_id: str
    task_id: str
    report_version_id: str
    snapshot_id: str
    revision: str
    snapshot_hash: str
    content_hash: str
    generation: int
    discovery_fingerprint: str
    ordered_post_ids_hash: str
    next_offset: int
    source_tool: str
    result_kind: str


@dataclass(frozen=True)
class ReportTaskRiskCommentCursorRecord:
    token: str
    session_id: str
    task_id: str
    report_version_id: str
    snapshot_id: str
    revision: str
    snapshot_hash: str
    content_hash: str
    generation: int
    post_id: str
    ordered_comment_ids_hash: str
    next_offset: int
    source_tool: str
    result_kind: str


class ReportTaskReferenceBatch:
    def __init__(
        self, registry: "ReportTaskReferenceRegistry", scope: ReportTaskSessionScope
    ) -> None:
        self._registry = registry
        self.scope = scope
        self._staged: dict[str, ReportTaskReferenceRecord] = {}
        self._committed = False

    def expose(
        self,
        *,
        kind: ReportTaskReferenceKind,
        object_id: str,
        parent_id: str | None,
        source_tool: str,
        result_kind: str,
        content_state: str,
    ) -> str:
        if self._committed:
            raise RuntimeError("Reference batch is already committed")
        origin = ReferenceOrigin(
            parent_id=parent_id,
            source_tool=source_tool,
            result_kind=result_kind,
            content_state=content_state,
        )
        existing = self._registry.record_for_object(
            self.scope.session_id, kind, object_id, required=False
        )
        if existing is not None:
            origins = existing.origins
            if origin not in origins:
                origins = (*origins, origin)
            self._staged[existing.token] = replace(existing, origins=origins)
            return existing.token
        token = self._registry._allocate_token(kind)
        self._staged[token] = ReportTaskReferenceRecord(
            token=token,
            session_id=self.scope.session_id,
            task_id=self.scope.task_id,
            report_version_id=self.scope.report_version_id,
            snapshot_id=self.scope.snapshot_id,
            revision=self.scope.revision,
            snapshot_hash=self.scope.snapshot_hash,
            content_hash=self.scope.content_hash,
            generation=self.scope.generation,
            kind=kind,
            object_id=object_id,
            origins=(origin,),
        )
        return token

    def commit(self) -> None:
        if not self._committed:
            self._registry._commit(self.scope, tuple(self._staged.values()))
            self._committed = True


class ReportTaskReferenceRegistry:
    def __init__(self, *, nonce: str | None = None) -> None:
        self._nonce = nonce or secrets.token_hex(3)
        self._lock = threading.RLock()
        self._counter = 0
        self._scopes: dict[str, ReportTaskSessionScope] = {}
        self._records: dict[str, ReportTaskReferenceRecord] = {}
        self._by_object: dict[
            tuple[str, int, ReportTaskReferenceKind, str], str
        ] = {}
        self._search_cursors: dict[str, ReportTaskSearchCursorRecord] = {}
        self._risk_comment_cursors: dict[
            str, ReportTaskRiskCommentCursorRecord
        ] = {}

    def bind(
        self,
        *,
        session_id: str,
        task_id: str,
        report_version_id: str,
        snapshot_id: str,
        revision: str,
        snapshot_hash: str = "",
        content_hash: str = "",
        force_new_generation: bool = False,
    ) -> ReportTaskSessionScope:
        if not session_id:
            raise ReferenceError("scope_unbound", "Hermes session_id is required")
        with self._lock:
            current = self._scopes.get(session_id)
            same = current is not None and (
                current.task_id,
                current.report_version_id,
                current.snapshot_id,
                current.revision,
                current.snapshot_hash,
                current.content_hash,
            ) == (
                task_id,
                report_version_id,
                snapshot_id,
                revision,
                snapshot_hash,
                content_hash,
            )
            if same and not force_new_generation:
                return current
            scope = ReportTaskSessionScope(
                session_id=session_id,
                task_id=task_id,
                report_version_id=report_version_id,
                snapshot_id=snapshot_id,
                revision=revision,
                snapshot_hash=snapshot_hash,
                content_hash=content_hash,
                generation=(current.generation + 1 if current else 1),
            )
            self._scopes[session_id] = scope
            return scope

    def scope(self, session_id: str) -> ReportTaskSessionScope:
        with self._lock:
            scope = self._scopes.get(session_id)
        if scope is None:
            raise ReferenceError(
                "investigation_scope_unbound",
                "This Hermes session has no bound investigation task and report.",
            )
        return scope

    def has_scope(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._scopes

    def batch(self, session_id: str) -> ReportTaskReferenceBatch:
        return ReportTaskReferenceBatch(self, self.scope(session_id))

    def resolve(
        self,
        session_id: str,
        token: str,
        *,
        expected_kind: ReportTaskReferenceKind | None = None,
    ) -> ReportTaskReferenceRecord:
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
                "cross_scope_ref", "The reference belongs to another Hermes session."
            )
        if record.generation != scope.generation or record.revision != scope.revision:
            raise ReferenceError(
                "stale_revision_ref", "The reference belongs to an older revision."
            )
        if (
            record.task_id != scope.task_id
            or record.report_version_id != scope.report_version_id
            or record.snapshot_id != scope.snapshot_id
            or record.snapshot_hash != scope.snapshot_hash
            or record.content_hash != scope.content_hash
        ):
            raise ReferenceError(
                "cross_scope_ref",
                "The reference belongs to another task, report, or snapshot.",
            )
        if expected_kind is not None and record.kind != expected_kind:
            raise ReferenceError(
                "wrong_ref_type",
                f"Expected a {expected_kind} reference, but received {record.kind}.",
            )
        return record

    def record_for_object(
        self,
        session_id: str,
        kind: ReportTaskReferenceKind,
        object_id: str,
        *,
        required: bool = True,
    ) -> ReportTaskReferenceRecord | None:
        scope = self.scope(session_id)
        with self._lock:
            token = self._by_object.get((session_id, scope.generation, kind, object_id))
            record = self._records.get(token) if token is not None else None
        if record is None and required:
            raise ReferenceError(
                "unknown_ref", "The object has not been exposed in this session."
            )
        return record

    def ref_for(
        self,
        session_id: str,
        kind: ReportTaskReferenceKind,
        object_id: str,
        *,
        required: bool = True,
    ) -> str | None:
        record = self.record_for_object(
            session_id, kind, object_id, required=required
        )
        return record.token if record is not None else None

    def issue_search_cursor(
        self,
        session_id: str,
        *,
        discovery_fingerprint: str,
        ordered_post_ids_hash: str,
        next_offset: int,
    ) -> str:
        scope = self.scope(session_id)
        with self._lock:
            token = self._allocate_cursor_token()
            self._search_cursors[token] = ReportTaskSearchCursorRecord(
                token=token,
                session_id=scope.session_id,
                task_id=scope.task_id,
                report_version_id=scope.report_version_id,
                snapshot_id=scope.snapshot_id,
                revision=scope.revision,
                snapshot_hash=scope.snapshot_hash,
                content_hash=scope.content_hash,
                generation=scope.generation,
                discovery_fingerprint=discovery_fingerprint,
                ordered_post_ids_hash=ordered_post_ids_hash,
                next_offset=next_offset,
                source_tool="search_posts",
                result_kind="risk_post_candidate_directory",
            )
        return token

    def resolve_search_cursor(
        self,
        session_id: str,
        token: str,
        *,
        discovery_fingerprint: str,
        ordered_post_ids_hash: str,
    ) -> ReportTaskSearchCursorRecord:
        scope = self.scope(session_id)
        with self._lock:
            record = self._search_cursors.get(token)
        if record is None:
            raise ReferenceError(
                "unknown_cursor", "The cursor was not produced by search_posts."
            )
        if record.session_id != session_id:
            raise ReferenceError(
                "cross_scope_cursor", "The cursor belongs to another Hermes session."
            )
        if record.generation != scope.generation or record.revision != scope.revision:
            raise ReferenceError(
                "stale_revision_cursor", "The cursor belongs to an older revision."
            )
        if (
            record.task_id != scope.task_id
            or record.report_version_id != scope.report_version_id
            or record.snapshot_id != scope.snapshot_id
            or record.snapshot_hash != scope.snapshot_hash
            or record.content_hash != scope.content_hash
        ):
            raise ReferenceError(
                "cross_scope_cursor",
                "The cursor belongs to another task, report, or snapshot.",
            )
        if (
            record.source_tool != "search_posts"
            or record.result_kind != "risk_post_candidate_directory"
        ):
            raise ReferenceError(
                "invalid_cursor_source", "The cursor is not a risk discovery cursor."
            )
        if record.discovery_fingerprint != discovery_fingerprint:
            raise ReferenceError(
                "cursor_query_mismatch",
                "The cursor does not belong to this discovery goal and context.",
            )
        if record.ordered_post_ids_hash != ordered_post_ids_hash:
            raise ReferenceError(
                "cursor_order_mismatch", "The frozen candidate order has changed."
            )
        return record

    def issue_risk_comment_cursor(
        self,
        session_id: str,
        *,
        post_id: str,
        ordered_comment_ids_hash: str,
        next_offset: int,
    ) -> str:
        scope = self.scope(session_id)
        with self._lock:
            token = self._allocate_risk_comment_cursor_token()
            self._risk_comment_cursors[token] = ReportTaskRiskCommentCursorRecord(
                token=token,
                session_id=scope.session_id,
                task_id=scope.task_id,
                report_version_id=scope.report_version_id,
                snapshot_id=scope.snapshot_id,
                revision=scope.revision,
                snapshot_hash=scope.snapshot_hash,
                content_hash=scope.content_hash,
                generation=scope.generation,
                post_id=post_id,
                ordered_comment_ids_hash=ordered_comment_ids_hash,
                next_offset=next_offset,
                source_tool="list_post_risk_comments",
                result_kind="post_risk_comment_directory",
            )
        return token

    def resolve_risk_comment_cursor(
        self,
        session_id: str,
        token: str,
        *,
        post_id: str,
        ordered_comment_ids_hash: str,
    ) -> ReportTaskRiskCommentCursorRecord:
        scope = self.scope(session_id)
        with self._lock:
            record = self._risk_comment_cursors.get(token)
        if record is None:
            raise ReferenceError(
                "unknown_cursor",
                "The cursor was not produced by list_post_risk_comments.",
            )
        if record.session_id != session_id:
            raise ReferenceError(
                "cross_scope_cursor", "The cursor belongs to another Hermes session."
            )
        if record.generation != scope.generation or record.revision != scope.revision:
            raise ReferenceError(
                "stale_revision_cursor", "The cursor belongs to an older revision."
            )
        if (
            record.task_id != scope.task_id
            or record.report_version_id != scope.report_version_id
            or record.snapshot_id != scope.snapshot_id
            or record.snapshot_hash != scope.snapshot_hash
            or record.content_hash != scope.content_hash
        ):
            raise ReferenceError(
                "cross_scope_cursor",
                "The cursor belongs to another task, report, or snapshot.",
            )
        if record.post_id != post_id:
            raise ReferenceError(
                "cursor_query_mismatch", "The cursor belongs to another Parent Post."
            )
        if record.ordered_comment_ids_hash != ordered_comment_ids_hash:
            raise ReferenceError(
                "cursor_order_mismatch", "The frozen Comment order has changed."
            )
        if (
            record.source_tool != "list_post_risk_comments"
            or record.result_kind != "post_risk_comment_directory"
        ):
            raise ReferenceError(
                "invalid_cursor_source", "The cursor is not a risk Comment cursor."
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

    def _allocate_token(self, kind: ReportTaskReferenceKind) -> str:
        prefix = {
            "category": "g",
            "finding": "f",
            "post": "p",
            "comment": "c",
            "evidence": "e",
        }[kind]
        with self._lock:
            self._counter += 1
            return f"{prefix}{self._counter}_{self._nonce}"

    def _allocate_cursor_token(self) -> str:
        with self._lock:
            self._counter += 1
            return f"r{self._counter}_{self._nonce}"

    def _allocate_risk_comment_cursor_token(self) -> str:
        with self._lock:
            self._counter += 1
            return f"q{self._counter}_{self._nonce}"

    def _commit(
        self,
        scope: ReportTaskSessionScope,
        records: tuple[ReportTaskReferenceRecord, ...],
    ) -> None:
        with self._lock:
            if self._scopes.get(scope.session_id) != scope:
                raise ReferenceError(
                    "stale_revision_ref",
                    "The session scope changed before ToolResult emission.",
                )
            for record in records:
                key = (
                    record.session_id,
                    record.generation,
                    record.kind,
                    record.object_id,
                )
                existing_token = self._by_object.get(key)
                if existing_token is not None and existing_token != record.token:
                    raise RuntimeError("Concurrent reference publication conflict")
                self._records[record.token] = record
                self._by_object[key] = record.token
