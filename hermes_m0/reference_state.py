"""Durable, session-scoped navigation aliases for fresh Agent processes.

Only typed registry records are stored, never executable Python objects. The
current authorized report binding is checked before restoring any alias.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from contextlib import contextmanager
import hashlib
import json
import sqlite3
from typing import Any

from .account_activity_refs import (
    AccountActivityScope, AccountActivityReferenceRecord, AccountActivityOrigin,
    AccountActivityCursorRecord,
)
from .report_task_refs import (
    ReportTaskSessionScope, ReportTaskReferenceRecord, ReferenceOrigin,
    ReportTaskSearchCursorRecord, ReportTaskRiskCommentCursorRecord,
)


_REPORT_TYPES = {
    "_scopes": ReportTaskSessionScope, "_records": ReportTaskReferenceRecord,
    "_search_cursors": ReportTaskSearchCursorRecord,
    "_risk_comment_cursors": ReportTaskRiskCommentCursorRecord,
}
_ACCOUNT_TYPES = {
    "_scopes": AccountActivityScope, "_records": AccountActivityReferenceRecord,
    "_cursors": AccountActivityCursorRecord,
}


def _dump(registry: Any, session_id: str, types: dict) -> dict:
    with registry._lock:
        return {
            "nonce": registry._nonce, "counter": registry._counter,
            **{name: [asdict(r) for r in getattr(registry, name).values()
                       if r.session_id == session_id] for name in types},
        }


def _load(registry: Any, session_id: str, data: dict, types: dict) -> None:
    current = registry._scopes[session_id]
    scopes = data["_scopes"]
    if len(scopes) != 1 or any(
        value != scopes[0].get(key) for key, value in asdict(current).items()
        if key != "generation"
    ):
        raise ValueError("saved reference scope differs from the authorized session")
    restored = {}
    for name, cls in types.items():
        records = []
        for raw in data[name]:
            raw = dict(raw)
            if raw["session_id"] != session_id:
                raise ValueError("cross-session reference state")
            if name == "_records":
                origin_type = ReferenceOrigin if cls is ReportTaskReferenceRecord else AccountActivityOrigin
                raw["origins"] = tuple(origin_type(**o) for o in raw["origins"])
            records.append(cls(**raw))
        restored[name] = records
    with registry._lock:
        registry._nonce = data["nonce"]
        registry._counter = max(registry._counter, int(data["counter"]))
        for name, records in restored.items():
            getattr(registry, name).update({
                r.session_id if name == "_scopes" else r.token: r for r in records
            })
        for r in restored["_records"]:
            key = (r.session_id, r.generation, r.kind, r.object_id)
            if isinstance(r, AccountActivityReferenceRecord):
                key += (r.parent_account_id, r.corpus_revision)
            registry._by_object[key] = r.token


def _identity(service: Any, session_id: str) -> dict:
    scope = asdict(service.refs.scope(session_id))
    scope.pop("generation")
    sources = service.account_activity.authorized_report_repositories if service.account_activity else ()
    return {"scope": scope, "sources": [
        [r.fixture.report_version.id,
         str(getattr(r, "snapshot_hash", r.fixture.provenance.projection_manifest_hash)),
         str(getattr(r, "content_hash", r.fixture.report_version.revision))]
        for r in sources
    ]}


@contextmanager
def _connect(service: Any):
    connection = sqlite3.connect(str(service.ledger.path) + ".references.sqlite3", timeout=30)
    try:
        with connection:
            connection.execute("CREATE TABLE IF NOT EXISTS reference_states "
                               "(session_id TEXT PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL)")
            yield connection
    finally:
        connection.close()


def save(service: Any, session_id: str) -> None:
    if service.ledger is None:
        return
    with service._reference_state_lock:
        _save(service, session_id)


def _save(service: Any, session_id: str) -> None:
    state = {"version": 1, "identity": _identity(service, session_id),
             "report": _dump(service.refs, session_id, _REPORT_TYPES),
             "account": (_dump(service.account_activity.refs, session_id, _ACCOUNT_TYPES)
                         if service.account_activity else None)}
    payload = json.dumps(state, ensure_ascii=False, sort_keys=True)
    with _connect(service) as conn:
        conn.execute("INSERT OR REPLACE INTO reference_states VALUES (?, ?, ?)",
                     (session_id, payload, hashlib.sha256(payload.encode()).hexdigest()))


def restore(service: Any, session_id: str) -> bool:
    if service.ledger is None:
        return False
    with _connect(service) as conn:
        row = conn.execute("SELECT payload, sha256 FROM reference_states WHERE session_id=?",
                           (session_id,)).fetchone()
    if row is None:
        return False
    if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
        raise ValueError("saved reference state failed its integrity check")
    state = json.loads(row[0])
    if state["version"] != 1:
        raise ValueError("unsupported saved reference state version")
    if state["identity"] != _identity(service, session_id):
        # Changed authorization/snapshot: never resurrect the old references.
        return False
    _load(service.refs, session_id, state["report"], _REPORT_TYPES)
    if service.account_activity and state["account"] is not None:
        _load(service.account_activity.refs, session_id, state["account"], _ACCOUNT_TYPES)
    return True


def _tokens(service: Any) -> dict[str, tuple[Any, str, Any]]:
    result = {}
    registries = [(service.refs, _REPORT_TYPES)]
    if service.account_activity:
        registries.append((service.account_activity.refs, _ACCOUNT_TYPES))
    for registry, types in registries:
        with registry._lock:
            for name in types:
                if name != "_scopes":
                    result.update({k: (registry, name, v) for k, v in getattr(registry, name).items()})
    return result


def _replace_tokens(value: Any, aliases: dict) -> Any:
    if isinstance(value, str):
        return aliases.get(value, value)
    if isinstance(value, list):
        return [_replace_tokens(v, aliases) for v in value]
    if isinstance(value, dict):
        return {k: _replace_tokens(v, aliases) for k, v in value.items()}
    return value


def restore_legacy_transcript(service: Any, session_id: str, history: list[dict]) -> None:
    """Upgrade pre-checkpoint histories using verified read-only tool results.

    Re-read only the report service's own query handlers. An old token is accepted
    only when the entire original ToolResult matches the freshly authorized read
    after substituting opaque tokens. Never infer object identity from prose.
    """
    calls, aliases = {}, {}
    turn_id = "reference-restore:0"
    for index, message in enumerate(history):
        if message.get("role") == "user":
            turn_id = f"reference-restore:{index}"
            continue
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                calls[call["id"]] = call.get("function") or {}
            continue
        if message.get("role") != "tool":
            continue
        call = calls.get(message.get("tool_call_id"), {})
        name = call.get("name")
        try:
            args = call.get("arguments", {})
            args = json.loads(args) if isinstance(args, str) else args
            old = json.loads(message.get("content", "{}"))
        except (ValueError, TypeError):
            continue
        if not isinstance(args, dict):
            continue
        if name == "tool_call":
            name = args.get("tool_name") or args.get("name")
            args = args.get("arguments", {})
        if name not in service._handlers or not isinstance(old, dict) or old.get("ok") is not True:
            continue
        fresh = json.loads(service.dispatch(
            name, _replace_tokens(args, aliases), session_id=session_id, turn_id=turn_id))
        tokens, proposed = _tokens(service), {}

        def compare(left, right):
            if isinstance(left, str) and isinstance(right, str) and right in tokens:
                if left in proposed and proposed[left] != right:
                    return False
                proposed[left] = right
                return True
            if isinstance(left, dict) and isinstance(right, dict):
                return left.keys() == right.keys() and all(compare(left[k], right[k]) for k in left)
            if isinstance(left, list) and isinstance(right, list):
                return len(left) == len(right) and all(compare(a, b) for a, b in zip(left, right))
            return left == right

        if not compare(old, fresh):
            continue
        for old_token, token in proposed.items():
            registry, field, record = tokens[token]
            with registry._lock:
                existing = getattr(registry, field).get(old_token)
                alias = replace(record, token=old_token)
                if existing is not None and hasattr(alias, "origins"):
                    if replace(existing, origins=alias.origins) == alias:
                        alias = replace(alias, origins=tuple(dict.fromkeys((*existing.origins, *alias.origins))))
                if existing is not None and existing != alias:
                    if not hasattr(alias, "origins") or replace(existing, origins=alias.origins) != alias:
                        raise ValueError("conflicting legacy reference identity")
                getattr(registry, field)[old_token] = alias
            aliases[old_token] = token
    save(service, session_id)
    service.restored_reference_sessions.add(session_id)
