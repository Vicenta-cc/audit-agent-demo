"""Thin durable idempotency ledger for Investigation tool executions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from hermes_m0.tool_results import error_result


ExecutionStatus = Literal["running", "completed", "interrupted"]


@dataclass(frozen=True)
class ToolExecutionRecord:
    session_id: str
    tool_call_id: str
    tool_name: str
    args_fingerprint: str
    execution_status: ExecutionStatus
    result_hash: str
    result_cache: str
    execution_count: int


class ToolExecutionLedger:
    """One row per session/tool_call_id; no domain or conversation state."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()
        self.recover_orphans()

    def execute(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        args: dict[str, Any],
        next_call: Callable[[dict[str, Any]], Any],
    ) -> Any:
        if not session_id or not tool_call_id:
            return error_result(
                tool=tool_name,
                code="missing_tool_call_identity",
                message="Hermes session_id and tool_call_id are required for execution.",
            )
        fingerprint = fingerprint_args(args)
        decision, record = self._begin(
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            args_fingerprint=fingerprint,
        )
        if decision == "completed":
            if hashlib.sha256(record.result_cache.encode("utf-8")).hexdigest() != record.result_hash:
                return error_result(
                    tool=tool_name,
                    code="idempotency_cache_corrupt",
                    message="The cached ToolResult failed its integrity check.",
                )
            return record.result_cache
        if decision == "conflict":
            return error_result(
                tool=tool_name,
                code="tool_call_id_conflict",
                message="This tool_call_id was already bound to another tool or argument set.",
            )
        if decision == "running":
            return error_result(
                tool=tool_name,
                code="tool_execution_in_progress",
                message="This tool_call_id is already executing.",
            )
        if decision == "interrupted":
            return error_result(
                tool=tool_name,
                code="tool_execution_interrupted",
                message=(
                    "The prior execution was interrupted. It will not be executed again "
                    "under the same tool_call_id."
                ),
            )

        try:
            result = next_call(args)
        except BaseException:
            self._mark_interrupted(session_id, tool_call_id)
            raise
        if not isinstance(result, str):
            self._mark_interrupted(session_id, tool_call_id)
            return error_result(
                tool=tool_name,
                code="invalid_tool_result",
                message="Investigation tools must return a native string ToolResult.",
            )
        self._complete(session_id, tool_call_id, result)
        return result

    def recover_orphans(self) -> int:
        """Fence calls left running by a terminated process without replaying them."""
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE tool_executions SET execution_status = 'interrupted' "
                "WHERE execution_status = 'running'"
            )
            return int(cursor.rowcount)

    def get(self, session_id: str, tool_call_id: str) -> ToolExecutionRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, tool_call_id, tool_name, args_fingerprint,
                       execution_status, result_hash, result_cache, execution_count
                FROM tool_executions
                WHERE session_id = ? AND tool_call_id = ?
                """,
                (session_id, tool_call_id),
            ).fetchone()
        return _record(row) if row is not None else None

    def records(self, session_id: str) -> tuple[ToolExecutionRecord, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT session_id, tool_call_id, tool_name, args_fingerprint,
                       execution_status, result_hash, result_cache, execution_count
                FROM tool_executions
                WHERE session_id = ?
                ORDER BY rowid
                """,
                (session_id,),
            ).fetchall()
        return tuple(_record(row) for row in rows)

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_executions (
                    session_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    args_fingerprint TEXT NOT NULL,
                    execution_status TEXT NOT NULL
                        CHECK (execution_status IN ('running', 'completed', 'interrupted')),
                    result_hash TEXT NOT NULL DEFAULT '',
                    result_cache TEXT NOT NULL DEFAULT '',
                    execution_count INTEGER NOT NULL CHECK (execution_count >= 1),
                    PRIMARY KEY (session_id, tool_call_id)
                )
                """
            )

    def _begin(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        args_fingerprint: str,
    ) -> tuple[str, ToolExecutionRecord]:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT session_id, tool_call_id, tool_name, args_fingerprint,
                       execution_status, result_hash, result_cache, execution_count
                FROM tool_executions
                WHERE session_id = ? AND tool_call_id = ?
                """,
                (session_id, tool_call_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO tool_executions (
                        session_id, tool_call_id, tool_name, args_fingerprint,
                        execution_status, result_hash, result_cache, execution_count
                    ) VALUES (?, ?, ?, ?, 'running', '', '', 1)
                    """,
                    (session_id, tool_call_id, tool_name, args_fingerprint),
                )
                record = ToolExecutionRecord(
                    session_id=session_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args_fingerprint=args_fingerprint,
                    execution_status="running",
                    result_hash="",
                    result_cache="",
                    execution_count=1,
                )
                return "execute", record
            record = _record(row)
            if record.tool_name != tool_name or record.args_fingerprint != args_fingerprint:
                return "conflict", record
            return record.execution_status, record

    def _complete(self, session_id: str, tool_call_id: str, result: str) -> None:
        result_hash = hashlib.sha256(result.encode("utf-8")).hexdigest()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tool_executions
                SET execution_status = 'completed', result_hash = ?, result_cache = ?
                WHERE session_id = ? AND tool_call_id = ? AND execution_status = 'running'
                """,
                (result_hash, result, session_id, tool_call_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Tool execution lost its running ledger record")

    def _mark_interrupted(self, session_id: str, tool_call_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE tool_executions
                SET execution_status = 'interrupted'
                WHERE session_id = ? AND tool_call_id = ? AND execution_status = 'running'
                """,
                (session_id, tool_call_id),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection


def fingerprint_args(args: dict[str, Any]) -> str:
    encoded = json.dumps(
        args,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _record(row: sqlite3.Row) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        session_id=str(row["session_id"]),
        tool_call_id=str(row["tool_call_id"]),
        tool_name=str(row["tool_name"]),
        args_fingerprint=str(row["args_fingerprint"]),
        execution_status=row["execution_status"],
        result_hash=str(row["result_hash"]),
        result_cache=str(row["result_cache"]),
        execution_count=int(row["execution_count"]),
    )
