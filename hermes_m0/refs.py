"""Session-scoped opaque references emitted by real ToolResult messages."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, replace
from typing import Literal


ReferenceKind = Literal["case", "post", "evidence"]


class ReferenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SessionScope:
    session_id: str
    report_version_id: str
    snapshot_id: str
    domain_revision: str
    generation: int


@dataclass(frozen=True)
class ReferenceRecord:
    token: str
    session_id: str
    report_version_id: str
    snapshot_id: str
    domain_revision: str
    generation: int
    kind: ReferenceKind
    object_id: str
    parent_id: str | None
    source_tool: str
    result_kind: str
    content_state: str


class ReferenceBatch:
    def __init__(self, registry: "SessionReferenceRegistry", scope: SessionScope) -> None:
        self._registry = registry
        self.scope = scope
        self._staged: dict[str, ReferenceRecord] = {}
        self._committed = False

    def expose(
        self,
        *,
        kind: ReferenceKind,
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
        self._staged[token] = ReferenceRecord(
            token=token,
            session_id=self.scope.session_id,
            report_version_id=self.scope.report_version_id,
            snapshot_id=self.scope.snapshot_id,
            domain_revision=self.scope.domain_revision,
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
        if self._committed:
            return
        self._registry._commit(self.scope, tuple(self._staged.values()))
        self._committed = True


class SessionReferenceRegistry:
    def __init__(self, *, nonce: str | None = None) -> None:
        self._nonce = nonce or secrets.token_hex(3)
        self._lock = threading.RLock()
        self._counter = 0
        self._scopes: dict[str, SessionScope] = {}
        self._records: dict[str, ReferenceRecord] = {}
        self._by_object: dict[tuple[str, int, ReferenceKind, str], str] = {}

    def bind(
        self,
        *,
        session_id: str,
        report_version_id: str,
        snapshot_id: str,
        domain_revision: str,
        force_new_generation: bool = False,
    ) -> SessionScope:
        if not session_id:
            raise ReferenceError("scope_unbound", "Hermes session_id is required")
        with self._lock:
            current = self._scopes.get(session_id)
            same = current is not None and (
                current.report_version_id,
                current.snapshot_id,
                current.domain_revision,
            ) == (report_version_id, snapshot_id, domain_revision)
            if same and not force_new_generation:
                return current
            scope = SessionScope(
                session_id=session_id,
                report_version_id=report_version_id,
                snapshot_id=snapshot_id,
                domain_revision=domain_revision,
                generation=(current.generation + 1 if current else 1),
            )
            self._scopes[session_id] = scope
            return scope

    def scope(self, session_id: str) -> SessionScope:
        with self._lock:
            scope = self._scopes.get(session_id)
        if scope is None:
            raise ReferenceError(
                "investigation_scope_unbound",
                "This Hermes session has no bound ReportVersion and FrozenSnapshot.",
            )
        return scope

    def batch(self, session_id: str) -> ReferenceBatch:
        return ReferenceBatch(self, self.scope(session_id))

    def resolve(
        self,
        session_id: str,
        token: str,
        *,
        expected_kind: ReferenceKind,
        expected_parent_id: str | None = None,
    ) -> ReferenceRecord:
        scope = self.scope(session_id)
        with self._lock:
            record = self._records.get(token)
        if record is None:
            raise ReferenceError(
                "unknown_ref", "The reference was not produced by a ToolResult in this runtime."
            )
        if record.session_id != session_id:
            raise ReferenceError(
                "cross_scope_ref", "The reference belongs to another Hermes session scope."
            )
        if record.generation != scope.generation or record.domain_revision != scope.domain_revision:
            raise ReferenceError(
                "stale_revision_ref", "The reference belongs to an older report revision."
            )
        if (
            record.report_version_id != scope.report_version_id
            or record.snapshot_id != scope.snapshot_id
        ):
            raise ReferenceError(
                "cross_scope_ref", "The reference belongs to another report or snapshot."
            )
        if record.kind != expected_kind:
            raise ReferenceError(
                "wrong_ref_type",
                f"Expected a {expected_kind} reference, but received {record.kind}.",
            )
        if expected_parent_id is not None and record.parent_id != expected_parent_id:
            raise ReferenceError(
                "wrong_parent_ref", "The referenced object does not belong to the required parent."
            )
        return record

    def ref_for(
        self,
        session_id: str,
        kind: ReferenceKind,
        object_id: str,
        *,
        required: bool = True,
    ) -> str | None:
        scope = self.scope(session_id)
        with self._lock:
            token = self._by_object.get((session_id, scope.generation, kind, object_id))
        if token is None and required:
            raise ReferenceError(
                "unknown_ref", "The object has not been exposed by a ToolResult in this session."
            )
        return token

    def replace_record_for_test(self, token: str, **updates: object) -> None:
        """Narrow corruption hook used to verify defensive parent checks."""
        with self._lock:
            record = self._records[token]
            self._records[token] = replace(record, **updates)

    def _allocate_token(self, kind: ReferenceKind) -> str:
        prefix = {"case": "c", "post": "p", "evidence": "e"}[kind]
        with self._lock:
            self._counter += 1
            return f"{prefix}{self._counter}_{self._nonce}"

    def _commit(
        self, scope: SessionScope, records: tuple[ReferenceRecord, ...]
    ) -> None:
        with self._lock:
            if self._scopes.get(scope.session_id) != scope:
                raise ReferenceError(
                    "stale_revision_ref", "The session scope changed before ToolResult emission."
                )
            for record in records:
                key = (record.session_id, record.generation, record.kind, record.object_id)
                existing = self._by_object.get(key)
                if existing is not None and existing != record.token:
                    raise RuntimeError("Concurrent reference publication conflict")
                self._records[record.token] = record
                self._by_object[key] = record.token
