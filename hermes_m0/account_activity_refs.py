"""Session-local aliases for Account Activity objects and pages."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass, replace
from typing import Literal

from hermes_m0.refs import ReferenceError


AccountActivityReferenceKind = Literal[
    "account", "occurrence", "comment_target", "post"
]


@dataclass(frozen=True)
class AccountActivityOrigin:
    source_tool: str
    content_state: str


@dataclass(frozen=True)
class AccountActivityScope:
    session_id: str
    task_id: str
    report_version_id: str
    snapshot_id: str
    report_revision: str
    snapshot_hash: str
    content_hash: str
    generation: int


@dataclass(frozen=True)
class AccountActivityReferenceRecord:
    token: str
    session_id: str
    generation: int
    kind: AccountActivityReferenceKind
    object_id: str
    parent_account_id: str | None
    corpus_revision: str
    source_tool: str
    content_state: str
    origins: tuple[AccountActivityOrigin, ...]


@dataclass(frozen=True)
class AccountActivityCursorRecord:
    token: str
    session_id: str
    generation: int
    account_id: str
    kind: str
    comment_target_account_id: str | None
    risk_filter: str | None
    corpus_revision: str
    ordered_occurrence_hash: str
    next_offset: int


class AccountActivityReferenceRegistry:
    def __init__(self, *, nonce: str | None = None) -> None:
        self._nonce = nonce or secrets.token_hex(3)
        self._lock = threading.RLock()
        self._counter = 0
        self._scopes: dict[str, AccountActivityScope] = {}
        self._records: dict[str, AccountActivityReferenceRecord] = {}
        self._by_object: dict[
            tuple[
                str,
                int,
                AccountActivityReferenceKind,
                str,
                str | None,
                str,
            ],
            str,
        ] = {}
        self._cursors: dict[str, AccountActivityCursorRecord] = {}

    def bind(
        self,
        *,
        session_id: str,
        task_id: str,
        report_version_id: str,
        snapshot_id: str,
        report_revision: str,
        snapshot_hash: str,
        content_hash: str,
        force_new_generation: bool = False,
    ) -> AccountActivityScope:
        if not session_id:
            raise ReferenceError("scope_unbound", "Hermes session_id is required")
        identity = (
            task_id,
            report_version_id,
            snapshot_id,
            report_revision,
            snapshot_hash,
            content_hash,
        )
        with self._lock:
            current = self._scopes.get(session_id)
            current_identity = (
                None
                if current is None
                else (
                    current.task_id,
                    current.report_version_id,
                    current.snapshot_id,
                    current.report_revision,
                    current.snapshot_hash,
                    current.content_hash,
                )
            )
            if current_identity == identity and not force_new_generation:
                return current
            scope = AccountActivityScope(
                session_id=session_id,
                task_id=task_id,
                report_version_id=report_version_id,
                snapshot_id=snapshot_id,
                report_revision=report_revision,
                snapshot_hash=snapshot_hash,
                content_hash=content_hash,
                generation=(current.generation + 1 if current else 1),
            )
            self._scopes[session_id] = scope
            return scope

    def expose(
        self,
        session_id: str,
        *,
        kind: AccountActivityReferenceKind,
        object_id: str,
        parent_account_id: str | None,
        corpus_revision: str,
        source_tool: str,
        content_state: str,
    ) -> str:
        scope = self._scope(session_id)
        key = (
            session_id,
            scope.generation,
            kind,
            object_id,
            parent_account_id,
            corpus_revision,
        )
        with self._lock:
            existing_token = self._by_object.get(key)
            if existing_token is not None:
                existing = self._records[existing_token]
                if existing.parent_account_id != parent_account_id:
                    raise ReferenceError(
                        "wrong_parent_ref",
                        "The Account Activity object has conflicting parent provenance.",
                    )
                origin = AccountActivityOrigin(source_tool, content_state)
                if origin not in existing.origins:
                    self._records[existing_token] = replace(
                        existing, origins=(*existing.origins, origin)
                    )
                return existing_token
            token = self._allocate(kind)
            record = AccountActivityReferenceRecord(
                token=token,
                session_id=session_id,
                generation=scope.generation,
                kind=kind,
                object_id=object_id,
                parent_account_id=parent_account_id,
                corpus_revision=corpus_revision,
                source_tool=source_tool,
                content_state=content_state,
                origins=(AccountActivityOrigin(source_tool, content_state),),
            )
            self._records[token] = record
            self._by_object[key] = token
            return token

    def resolve(
        self,
        session_id: str,
        token: str,
        *,
        expected_kind: AccountActivityReferenceKind,
        corpus_revision: str,
        expected_source_tool: str | None = None,
        expected_content_state: str | None = None,
    ) -> AccountActivityReferenceRecord:
        scope = self._scope(session_id)
        with self._lock:
            record = self._records.get(token)
        if record is None:
            raise ReferenceError(
                "unknown_ref",
                "The Account Activity reference was not produced in this session.",
            )
        if record.session_id != session_id:
            raise ReferenceError(
                "cross_scope_ref", "The Account Activity reference belongs to another session."
            )
        if record.generation != scope.generation:
            raise ReferenceError(
                "stale_revision_ref", "The Account Activity reference is from an older session generation."
            )
        if record.kind != expected_kind:
            raise ReferenceError(
                "wrong_ref_type",
                f"Expected a {expected_kind} reference, but received {record.kind}.",
            )
        if record.corpus_revision != corpus_revision:
            raise ReferenceError(
                "stale_account_data",
                "The Account data changed after this reference was displayed.",
            )
        if expected_source_tool is not None and record.source_tool != expected_source_tool:
            raise ReferenceError(
                "wrong_ref_source",
                "The Account Activity reference came from the wrong tool result.",
            )
        if (
            expected_content_state is not None
            and record.content_state != expected_content_state
        ):
            raise ReferenceError(
                "wrong_content_state",
                "The Account Activity reference has the wrong detail state.",
            )
        return record

    def issue_cursor(
        self,
        session_id: str,
        *,
        account_id: str,
        kind: str,
        comment_target_account_id: str | None,
        risk_filter: str | None,
        corpus_revision: str,
        ordered_occurrence_hash: str,
        next_offset: int,
    ) -> str:
        scope = self._scope(session_id)
        with self._lock:
            self._counter += 1
            token = f"page{self._counter}_{self._nonce}"
            self._cursors[token] = AccountActivityCursorRecord(
                token=token,
                session_id=session_id,
                generation=scope.generation,
                account_id=account_id,
                kind=kind,
                comment_target_account_id=comment_target_account_id,
                risk_filter=risk_filter,
                corpus_revision=corpus_revision,
                ordered_occurrence_hash=ordered_occurrence_hash,
                next_offset=next_offset,
            )
            return token

    def resolve_cursor(
        self,
        session_id: str,
        token: str,
        *,
        account_id: str,
        kind: str,
        comment_target_account_id: str | None,
        risk_filter: str | None,
        corpus_revision: str,
        ordered_occurrence_hash: str,
    ) -> AccountActivityCursorRecord:
        scope = self._scope(session_id)
        with self._lock:
            record = self._cursors.get(token)
        if record is None:
            raise ReferenceError(
                "unknown_cursor", "The cursor was not produced by Account Activity."
            )
        if record.session_id != session_id:
            raise ReferenceError(
                "cross_scope_cursor", "The cursor belongs to another session."
            )
        if record.generation != scope.generation:
            raise ReferenceError(
                "stale_revision_cursor", "The cursor is from an older session generation."
            )
        if record.corpus_revision != corpus_revision:
            raise ReferenceError(
                "stale_account_data", "The Account data changed after this cursor was issued."
            )
        if (
            record.account_id != account_id
            or record.kind != kind
            or record.comment_target_account_id != comment_target_account_id
            or record.risk_filter != risk_filter
        ):
            raise ReferenceError(
                "cursor_query_mismatch", "The cursor belongs to another Account query."
            )
        if record.ordered_occurrence_hash != ordered_occurrence_hash:
            raise ReferenceError(
                "cursor_order_mismatch", "The Account occurrence order has changed."
            )
        return record

    def replace_record_for_test(self, token: str, **updates: object) -> None:
        with self._lock:
            self._records[token] = replace(self._records[token], **updates)

    def replace_cursor_for_test(self, token: str, **updates: object) -> None:
        with self._lock:
            self._cursors[token] = replace(self._cursors[token], **updates)

    def _scope(self, session_id: str) -> AccountActivityScope:
        with self._lock:
            scope = self._scopes.get(session_id)
        if scope is None:
            raise ReferenceError(
                "investigation_scope_unbound",
                "This session has no bound Account Activity scope.",
            )
        return scope

    def _allocate(self, kind: AccountActivityReferenceKind) -> str:
        with self._lock:
            self._counter += 1
            prefix = {
                "account": "account",
                "occurrence": "activity",
                "comment_target": "target",
                "post": "post",
            }[kind]
            return f"{prefix}{self._counter}_{self._nonce}"
