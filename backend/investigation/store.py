from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from backend.audit_agent.config import settings
from backend.domain.identity import stable_hash
from backend.investigation.contracts import (
    GroundingValidation,
    InvestigationMessage,
    InvestigationSession,
    InvestigationTurn,
    PlannerShadowTrace,
    PublishedReportContext,
    QueryArtifactIndexEntry,
    QueryResultArtifact,
    QueryResultArtifactLink,
    QueryResultArtifactMember,
    Referent,
    ResolvedReference,
    SourceArtifact,
    SourceLedgerCandidate,
    SourceLedgerEntry,
    SourcePreparationShadowTrace,
    ToolCall,
    ToolQueryReceipt,
    TurnResult,
)
from backend.investigation.errors import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    ConcurrentTurnError,
    ClientMessageConflictError,
    InvestigationSessionNotFoundError,
    InvestigationTurnNotFoundError,
    ReportNotFoundError,
)
from backend.investigation.protocol import validate_hermes_transcript_messages
from backend.investigation.public_stream import (
    PUBLIC_STREAM_EVENT_TYPES,
    normalize_public_stream_payload,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InvestigationStore:
    """Official business facts for sessions, turns, messages, and source ledger."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = (db_path or (settings.data_dir / "investigation.sqlite3")).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS investigation_sessions (
                    id TEXT PRIMARY KEY,
                    scope_type TEXT NOT NULL DEFAULT 'report'
                        CHECK(scope_type IN ('report', 'creation')),
                    owner_principal TEXT NOT NULL DEFAULT '',
                    task_id TEXT NOT NULL,
                    report_id TEXT NOT NULL,
                    report_version_id TEXT NOT NULL,
                    source_snapshot_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    anchor_key TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('active', 'closed')),
                    summary_text TEXT NOT NULL DEFAULT '',
                    active_focus_json TEXT NOT NULL DEFAULT '{}',
                    ordered_referents_json TEXT NOT NULL DEFAULT '[]',
                    last_claim_id TEXT NOT NULL DEFAULT '',
                    last_finding_id TEXT NOT NULL DEFAULT '',
                    last_evidence_id TEXT NOT NULL DEFAULT '',
                    last_answer_message_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS deleted_report_versions (
                    report_version_id TEXT PRIMARY KEY
                );

                CREATE TABLE IF NOT EXISTS investigation_messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool')),
                    content TEXT NOT NULL DEFAULT '',
                    client_message_id TEXT NOT NULL DEFAULT '',
                    tool_call_id TEXT NOT NULL DEFAULT '',
                    tool_name TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, sequence),
                    FOREIGN KEY(session_id) REFERENCES investigation_sessions(id)
                );

                CREATE TABLE IF NOT EXISTS investigation_turns (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_message_id TEXT NOT NULL UNIQUE,
                    assistant_message_id TEXT NOT NULL DEFAULT '',
                    client_message_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'interrupted', 'error')),
                    current_node TEXT NOT NULL DEFAULT '',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    llm_call_count INTEGER NOT NULL DEFAULT 0,
                    stop_reason TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    safe_message TEXT NOT NULL DEFAULT '',
                    retryable INTEGER NOT NULL DEFAULT 0,
                    public_artifact_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(session_id, client_message_id),
                    FOREIGN KEY(session_id) REFERENCES investigation_sessions(id),
                    FOREIGN KEY(user_message_id) REFERENCES investigation_messages(id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_investigation_running_turn
                ON investigation_turns(session_id)
                WHERE status = 'running';

                CREATE UNIQUE INDEX IF NOT EXISTS uq_investigation_client_message
                ON investigation_messages(session_id, client_message_id)
                WHERE role = 'user' AND client_message_id <> '';

                CREATE TABLE IF NOT EXISTS investigation_hermes_transcripts (
                    turn_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    report_version_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    messages_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(turn_id) REFERENCES investigation_turns(id),
                    FOREIGN KEY(session_id) REFERENCES investigation_sessions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_investigation_hermes_transcripts_session
                ON investigation_hermes_transcripts(session_id, created_at, turn_id);

                CREATE TABLE IF NOT EXISTS investigation_public_turn_events (
                    event_id TEXT PRIMARY KEY,
                    turn_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL DEFAULT 'turn' CHECK(event_type IN (
                        'turn', 'activity', 'answer_delta', 'answer_reset'
                    )),
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL CHECK(stage IN (
                        'accepted', 'planning', 'preparing_sources',
                        'acquiring_source', 'answering', 'completed',
                        'interrupted', 'failed'
                    )),
                    answer TEXT NOT NULL DEFAULT '',
                    safe_message TEXT NOT NULL DEFAULT '',
                    retryable INTEGER NOT NULL DEFAULT 0,
                    artifact_json TEXT NOT NULL DEFAULT '{}',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    occurred_at TEXT NOT NULL,
                    UNIQUE(turn_id, sequence),
                    FOREIGN KEY(turn_id) REFERENCES investigation_turns(id)
                );

                CREATE TABLE IF NOT EXISTS investigation_source_ledger (
                    ledger_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    report_version_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    metric_key TEXT NOT NULL DEFAULT '',
                    section_id TEXT NOT NULL DEFAULT '',
                    claim_id TEXT NOT NULL DEFAULT '',
                    finding_id TEXT NOT NULL DEFAULT '',
                    evidence_id TEXT NOT NULL DEFAULT '',
                    source_hash TEXT NOT NULL,
                    excerpt TEXT NOT NULL DEFAULT '',
                    asset_status TEXT NOT NULL DEFAULT '',
                    query_fingerprint TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL DEFAULT '',
                    query_receipt_id TEXT NOT NULL DEFAULT '',
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    freshness TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES investigation_sessions(id),
                    FOREIGN KEY(turn_id) REFERENCES investigation_turns(id),
                    FOREIGN KEY(message_id) REFERENCES investigation_messages(id)
                );

                CREATE TABLE IF NOT EXISTS investigation_tool_query_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    report_version_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    subject_refs_json TEXT NOT NULL DEFAULT '[]',
                    query_params_json TEXT NOT NULL DEFAULT '{}',
                    arguments_fingerprint TEXT NOT NULL,
                    query_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('ok', 'error')),
                    error_code TEXT NOT NULL DEFAULT '',
                    returned_refs_json TEXT NOT NULL DEFAULT '[]',
                    model_visible_refs_json TEXT NOT NULL DEFAULT '[]',
                    result_count INTEGER,
                    total INTEGER,
                    has_more INTEGER,
                    cursor TEXT NOT NULL DEFAULT '',
                    next_cursor TEXT NOT NULL DEFAULT '',
                    model_output_truncated INTEGER NOT NULL DEFAULT 0,
                    result_fingerprint TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    executed_at TEXT NOT NULL,
                    UNIQUE(turn_id, tool_call_id),
                    FOREIGN KEY(session_id) REFERENCES investigation_sessions(id),
                    FOREIGN KEY(turn_id) REFERENCES investigation_turns(id)
                );

                CREATE TABLE IF NOT EXISTS investigation_planner_shadow_traces (
                    trace_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    report_version_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    planner_prompt_version TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    active_focus_exists INTEGER NOT NULL DEFAULT 0,
                    active_focus_type TEXT NOT NULL DEFAULT '',
                    resolved_referent_status TEXT NOT NULL,
                    resolved_referent_type TEXT NOT NULL DEFAULT '',
                    planning_status TEXT NOT NULL CHECK(planning_status IN ('ok', 'error')),
                    plan_json TEXT NOT NULL DEFAULT 'null',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    planner_error TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    request_id TEXT NOT NULL DEFAULT '',
                    planner_input_tokens INTEGER NOT NULL DEFAULT 0,
                    planner_output_tokens INTEGER NOT NULL DEFAULT 0,
                    planner_total_tokens INTEGER NOT NULL DEFAULT 0,
                    planner_llm_call_count INTEGER NOT NULL DEFAULT 0,
                    planner_latency_ms INTEGER NOT NULL DEFAULT 0,
                    planner_retry_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(turn_id, planner_prompt_version),
                    FOREIGN KEY(session_id) REFERENCES investigation_sessions(id),
                    FOREIGN KEY(turn_id) REFERENCES investigation_turns(id)
                );

                CREATE INDEX IF NOT EXISTS idx_investigation_messages_session
                ON investigation_messages(session_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_investigation_turns_session
                ON investigation_turns(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_investigation_ledger_message
                ON investigation_source_ledger(message_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_investigation_receipts_session
                ON investigation_tool_query_receipts(session_id, executed_at, receipt_id);
                CREATE INDEX IF NOT EXISTS idx_investigation_receipts_turn
                ON investigation_tool_query_receipts(turn_id, executed_at, receipt_id);
                CREATE INDEX IF NOT EXISTS idx_investigation_planner_traces_session
                ON investigation_planner_shadow_traces(session_id, created_at, trace_id);
                """
            )
            # Serialize additive DDL and re-check columns after the write lock is
            # acquired. This prevents concurrent process startup from both
            # attempting the same ALTER TABLE.
            connection.execute("BEGIN IMMEDIATE")
            session_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(investigation_sessions)"
                ).fetchall()
            }
            if "anchor_key" not in session_columns:
                connection.execute(
                    "ALTER TABLE investigation_sessions "
                    "ADD COLUMN anchor_key TEXT NOT NULL DEFAULT ''"
                )
            if "scope_type" not in session_columns:
                connection.execute(
                    "ALTER TABLE investigation_sessions "
                    "ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'report'"
                )
            if "owner_principal" not in session_columns:
                connection.execute(
                    "ALTER TABLE investigation_sessions "
                    "ADD COLUMN owner_principal TEXT NOT NULL DEFAULT ''"
                )
            turn_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(investigation_turns)"
                ).fetchall()
            }
            if "public_artifact_json" not in turn_columns:
                connection.execute(
                    "ALTER TABLE investigation_turns "
                    "ADD COLUMN public_artifact_json TEXT NOT NULL DEFAULT '{}'"
                )
            public_event_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(investigation_public_turn_events)"
                ).fetchall()
            }
            if "artifact_json" not in public_event_columns:
                connection.execute(
                    "ALTER TABLE investigation_public_turn_events "
                    "ADD COLUMN artifact_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "event_type" not in public_event_columns:
                connection.execute(
                    "ALTER TABLE investigation_public_turn_events "
                    "ADD COLUMN event_type TEXT NOT NULL DEFAULT 'turn'"
                )
            if "idempotency_key" not in public_event_columns:
                connection.execute(
                    "ALTER TABLE investigation_public_turn_events "
                    "ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''"
                )
            if "payload_json" not in public_event_columns:
                connection.execute(
                    "ALTER TABLE investigation_public_turn_events "
                    "ADD COLUMN payload_json TEXT NOT NULL DEFAULT '{}'"
                )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_investigation_public_event_idempotency
                ON investigation_public_turn_events(
                    turn_id, event_type, idempotency_key
                )
                WHERE idempotency_key <> ''
                """
            )
            connection.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS validate_investigation_public_event_type_insert
                BEFORE INSERT ON investigation_public_turn_events
                WHEN NEW.event_type NOT IN (
                    'turn', 'activity', 'answer_delta', 'answer_reset'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'invalid public stream event type');
                END;

                CREATE TRIGGER IF NOT EXISTS validate_investigation_public_event_type_update
                BEFORE UPDATE OF event_type ON investigation_public_turn_events
                WHEN NEW.event_type NOT IN (
                    'turn', 'activity', 'answer_delta', 'answer_reset'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'invalid public stream event type');
                END;
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_investigation_session_anchor
                ON investigation_sessions(anchor_key)
                WHERE anchor_key <> ''
                """
            )
            ledger_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(investigation_source_ledger)"
                ).fetchall()
            }
            if "tool_call_id" not in ledger_columns:
                connection.execute(
                    "ALTER TABLE investigation_source_ledger "
                    "ADD COLUMN tool_call_id TEXT NOT NULL DEFAULT ''"
                )
            if "query_receipt_id" not in ledger_columns:
                connection.execute(
                    "ALTER TABLE investigation_source_ledger "
                    "ADD COLUMN query_receipt_id TEXT NOT NULL DEFAULT ''"
                )
            planner_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(investigation_planner_shadow_traces)"
                ).fetchall()
            }
            if "planner_error" not in planner_columns:
                connection.execute(
                    "ALTER TABLE investigation_planner_shadow_traces "
                    "ADD COLUMN planner_error TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                """
                UPDATE investigation_planner_shadow_traces
                SET planner_error = error_code
                WHERE planning_status = 'error'
                  AND planner_error = ''
                  AND error_code != ''
                """
            )
            self._init_artifact_schema(connection)
            self._init_source_preparation_shadow_schema(connection)

    @staticmethod
    def _init_artifact_schema(connection: sqlite3.Connection) -> None:
        """Install the additive Phase 1 schema atomically inside one savepoint."""
        connection.execute("SAVEPOINT investigation_artifact_phase1")
        try:
            statements = (
                """
                CREATE TABLE IF NOT EXISTS investigation_source_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    report_version_id TEXT NOT NULL,
                    source_snapshot_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    stable_source_ref TEXT NOT NULL,
                    source_type TEXT NOT NULL CHECK(source_type IN (
                        'text', 'comment', 'ocr', 'asr', 'visual', 'keyframe',
                        'profile', 'rule_reference', 'other'
                    )),
                    content_level TEXT NOT NULL CHECK(content_level IN (
                        'evidence_collection_item', 'evidence_detail'
                    )),
                    canonical_content_json TEXT NOT NULL,
                    canonical_content_hash TEXT NOT NULL,
                    freshness TEXT NOT NULL CHECK(freshness = 'current_source'),
                    observed_at TEXT NOT NULL,
                    observation_metadata_json TEXT NOT NULL DEFAULT '{}',
                    source_fingerprint TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES investigation_sessions(id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS investigation_query_result_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    report_version_id TEXT NOT NULL,
                    source_snapshot_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN (
                        'finding_evidence_list', 'evidence_detail_read'
                    )),
                    subject_ref TEXT NOT NULL,
                    normalized_query_json TEXT NOT NULL,
                    normalized_filters_json TEXT NOT NULL DEFAULT '{}',
                    returned_count INTEGER NOT NULL CHECK(returned_count >= 0),
                    total INTEGER CHECK(total IS NULL OR total >= 0),
                    has_more INTEGER CHECK(has_more IS NULL OR has_more IN (0, 1)),
                    cursor TEXT,
                    next_cursor TEXT,
                    canonical_payload_json TEXT NOT NULL,
                    canonical_payload_hash TEXT NOT NULL,
                    query_fingerprint TEXT NOT NULL,
                    result_fingerprint TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    observation_metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES investigation_sessions(id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS investigation_query_result_members (
                    query_result_artifact_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                    stable_source_ref TEXT NOT NULL,
                    source_artifact_id TEXT NOT NULL,
                    content_level TEXT NOT NULL CHECK(content_level IN (
                        'evidence_collection_item', 'evidence_detail'
                    )),
                    PRIMARY KEY(query_result_artifact_id, ordinal),
                    UNIQUE(query_result_artifact_id, stable_source_ref),
                    UNIQUE(query_result_artifact_id, source_artifact_id),
                    FOREIGN KEY(query_result_artifact_id)
                        REFERENCES investigation_query_result_artifacts(artifact_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(source_artifact_id)
                        REFERENCES investigation_source_artifacts(artifact_id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS investigation_query_artifact_links (
                    receipt_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    query_result_artifact_id TEXT NOT NULL,
                    linked_at TEXT NOT NULL,
                    FOREIGN KEY(receipt_id)
                        REFERENCES investigation_tool_query_receipts(receipt_id),
                    FOREIGN KEY(query_result_artifact_id)
                        REFERENCES investigation_query_result_artifacts(artifact_id)
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_investigation_source_artifacts_scope
                ON investigation_source_artifacts(
                    session_id, report_version_id, snapshot_hash,
                    stable_source_ref, content_level, observed_at
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_investigation_query_artifacts_scope
                ON investigation_query_result_artifacts(
                    session_id, report_version_id, snapshot_hash,
                    operation, subject_ref, query_fingerprint, observed_at
                )
                """,
                """
                CREATE INDEX IF NOT EXISTS idx_investigation_query_artifact_links_result
                ON investigation_query_artifact_links(query_result_artifact_id, linked_at)
                """,
            )
            for statement in statements:
                connection.execute(statement)
            connection.execute("RELEASE SAVEPOINT investigation_artifact_phase1")
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT investigation_artifact_phase1")
            connection.execute("RELEASE SAVEPOINT investigation_artifact_phase1")
            raise

    @staticmethod
    def _init_source_preparation_shadow_schema(
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute("SAVEPOINT investigation_source_shadow_phase3")
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS investigation_source_preparation_shadow_traces (
                    trace_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    report_version_id TEXT NOT NULL,
                    source_snapshot_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    planner_trace_id TEXT NOT NULL,
                    planner_prompt_version TEXT NOT NULL,
                    subject_binder_version TEXT NOT NULL,
                    orchestrator_version TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    requirement_kind TEXT NOT NULL,
                    binding_status TEXT NOT NULL,
                    status TEXT NOT NULL,
                    bound_requirement_json TEXT NOT NULL DEFAULT 'null',
                    preparation_result_json TEXT NOT NULL DEFAULT 'null',
                    matched_query_result_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
                    selected_query_result_artifact_id TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(turn_id, orchestrator_version),
                    FOREIGN KEY(session_id) REFERENCES investigation_sessions(id),
                    FOREIGN KEY(turn_id) REFERENCES investigation_turns(id),
                    FOREIGN KEY(planner_trace_id)
                        REFERENCES investigation_planner_shadow_traces(trace_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_investigation_source_shadow_session
                ON investigation_source_preparation_shadow_traces(
                    session_id, created_at, trace_id
                )
                """
            )
            connection.execute("RELEASE SAVEPOINT investigation_source_shadow_phase3")
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT investigation_source_shadow_phase3")
            connection.execute("RELEASE SAVEPOINT investigation_source_shadow_phase3")
            raise

    def create_session(
        self,
        context: PublishedReportContext,
        *,
        anchor_key: str = "",
    ) -> InvestigationSession:
        now = utc_now()
        session_id = f"investigation-session:{uuid4().hex}"
        normalized_anchor = str(anchor_key or "").strip()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM deleted_report_versions WHERE report_version_id=?", (context.report_version_id,)).fetchone():
                raise ReportNotFoundError("report was deleted")
            existing = None
            if normalized_anchor:
                existing = connection.execute(
                    "SELECT * FROM investigation_sessions WHERE anchor_key = ?",
                    (normalized_anchor,),
                ).fetchone()
            if existing is not None:
                if str(existing["scope_type"]) != "report":
                    raise ValueError(
                        "investigation Session anchor cannot cross conversation scope"
                    )
                expected_scope = (
                    context.task_id,
                    context.report_id,
                    context.report_version_id,
                    context.source_snapshot_id,
                    context.snapshot_hash,
                )
                existing_scope = (
                    str(existing["task_id"]),
                    str(existing["report_id"]),
                    str(existing["report_version_id"]),
                    str(existing["source_snapshot_id"]),
                    str(existing["snapshot_hash"]),
                )
                if existing_scope != expected_scope:
                    raise ValueError(
                        "investigation Session anchor cannot be rebound to another report scope"
                    )
                session_id = str(existing["id"])
            else:
                connection.execute(
                    """
                    INSERT INTO investigation_sessions (
                        id, scope_type, owner_principal, task_id, report_id,
                        report_version_id, source_snapshot_id,
                        snapshot_hash, anchor_key, status, created_at, updated_at
                    ) VALUES (?, 'report', '', ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (
                        session_id,
                        context.task_id,
                        context.report_id,
                        context.report_version_id,
                        context.source_snapshot_id,
                        context.snapshot_hash,
                        normalized_anchor,
                        now,
                        now,
                    ),
                )
        return self.get_session(session_id)

    def create_creation_session(
        self,
        *,
        principal: str,
        anchor_key: str = "",
    ) -> InvestigationSession:
        normalized_principal = str(principal or "").strip()
        if not normalized_principal:
            raise ValueError("creation Session principal is required")
        normalized_anchor = str(anchor_key or "").strip()
        now = utc_now()
        session_id = f"investigation-session:{uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = None
            if normalized_anchor:
                existing = connection.execute(
                    "SELECT * FROM investigation_sessions WHERE anchor_key = ?",
                    (normalized_anchor,),
                ).fetchone()
            if existing is not None:
                if (
                    str(existing["scope_type"]) != "creation"
                    or str(existing["owner_principal"]) != normalized_principal
                ):
                    raise ValueError(
                        "creation Session anchor cannot be rebound across scope or Principal"
                    )
                session_id = str(existing["id"])
            else:
                connection.execute(
                    """
                    INSERT INTO investigation_sessions (
                        id, scope_type, owner_principal, task_id, report_id,
                        report_version_id, source_snapshot_id, snapshot_hash,
                        anchor_key, status, created_at, updated_at
                    ) VALUES (?, 'creation', ?, '', '', '', '', '', ?, 'active', ?, ?)
                    """,
                    (
                        session_id,
                        normalized_principal,
                        normalized_anchor,
                        now,
                        now,
                    ),
                )
        return self.get_session(session_id)

    def list_creation_sessions(
        self, *, principal: str, limit: int = 50, offset: int = 0
    ) -> tuple[InvestigationSession, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM investigation_sessions
                   WHERE scope_type='creation' AND owner_principal=? AND status='active'
                   ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?""",
                (principal, limit, offset),
            ).fetchall()
        return tuple(self._session(row) for row in rows)

    def get_session(self, session_id: str) -> InvestigationSession:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investigation_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise InvestigationSessionNotFoundError(session_id)
        return self._session(row)

    def find_session_by_anchor(self, anchor_key: str) -> InvestigationSession | None:
        normalized_anchor = str(anchor_key or "").strip()
        if not normalized_anchor:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investigation_sessions WHERE anchor_key = ?",
                (normalized_anchor,),
            ).fetchone()
        return self._session(row) if row is not None else None

    def list_report_version_sessions(self, anchor: str) -> tuple[InvestigationSession, ...]:
        """Read the original session and immutable version sessions for one workspace."""
        prefix = anchor + ":version:"
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM investigation_sessions WHERE anchor_key = ? "
                "OR substr(anchor_key, 1, ?) = ? ORDER BY created_at, id",
                (anchor, len(prefix), prefix),
            ).fetchall()
        return tuple(self._session(row) for row in rows)

    def session_anchor(self, session_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT anchor_key FROM investigation_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            raise InvestigationSessionNotFoundError(session_id)
        return str(row["anchor_key"] or "")

    def conversation_snapshot(
        self, session_id: str, *, include_tool_messages: bool = False
    ) -> tuple[
        InvestigationSession,
        tuple[InvestigationMessage, ...],
        tuple[InvestigationTurn, ...],
    ]:
        """Read one Session's public conversation inputs from one SQLite snapshot."""

        message_where = "session_id = ?"
        if not include_tool_messages:
            message_where += " AND role IN ('user', 'assistant')"
        with self._connect() as connection:
            connection.execute("BEGIN")
            session_row = connection.execute(
                "SELECT * FROM investigation_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session_row is None:
                raise InvestigationSessionNotFoundError(session_id)
            message_rows = connection.execute(
                f"SELECT * FROM investigation_messages WHERE {message_where} ORDER BY sequence",
                (session_id,),
            ).fetchall()
            turn_rows = connection.execute(
                """
                SELECT * FROM investigation_turns
                WHERE session_id = ? ORDER BY created_at, id
                """,
                (session_id,),
            ).fetchall()
        return (
            self._session(session_row),
            tuple(self._message(row) for row in message_rows),
            tuple(self._turn(row) for row in turn_rows),
        )

    def close_session(self, session_id: str) -> InvestigationSession:
        with self._connect() as connection:
            updated = connection.execute(
                "UPDATE investigation_sessions SET status = 'closed', updated_at = ? WHERE id = ?",
                (utc_now(), session_id),
            ).rowcount
        if not updated:
            raise InvestigationSessionNotFoundError(session_id)
        return self.get_session(session_id)

    def create_turn(
        self,
        session_id: str,
        *,
        client_message_id: str,
        user_input: str,
    ) -> tuple[InvestigationTurn, bool]:
        now = utc_now()
        turn_id = f"investigation-turn:{uuid4().hex}"
        message_id = f"investigation-message:{uuid4().hex}"
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT t.*
                    FROM investigation_turns t
                    WHERE t.session_id = ? AND t.client_message_id = ?
                    """,
                    (session_id, client_message_id),
                ).fetchone()
                if existing is not None:
                    existing_content = connection.execute(
                        "SELECT content FROM investigation_messages WHERE id = ?",
                        (str(existing["user_message_id"]),),
                    ).fetchone()
                    if (
                        existing_content is None
                        or str(existing_content["content"]) != user_input
                    ):
                        raise ClientMessageConflictError()
                    return self._turn(existing), True
                session = connection.execute(
                    "SELECT status FROM investigation_sessions WHERE id = ?", (session_id,)
                ).fetchone()
                if session is None:
                    raise InvestigationSessionNotFoundError(session_id)
                if str(session["status"]) != "active":
                    raise InvestigationSessionNotFoundError("session is not active")
                sequence = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM investigation_messages WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO investigation_messages (
                        id, session_id, turn_id, role, content, client_message_id,
                        sequence, created_at
                    ) VALUES (?, ?, ?, 'user', ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        session_id,
                        turn_id,
                        user_input,
                        client_message_id,
                        sequence,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO investigation_turns (
                        id, session_id, user_message_id, client_message_id,
                        status, created_at, started_at
                    ) VALUES (?, ?, ?, ?, 'running', ?, ?)
                    """,
                    (turn_id, session_id, message_id, client_message_id, now, now),
                )
        except sqlite3.IntegrityError as exc:
            if "uq_investigation_running_turn" in str(exc) or "UNIQUE constraint failed" in str(exc):
                existing = self.get_turn_by_client_message(session_id, client_message_id)
                if existing is not None:
                    if self.get_user_message_for_turn(existing.id).content != user_input:
                        raise ClientMessageConflictError() from exc
                    return existing, True
                raise ConcurrentTurnError() from exc
            raise
        return self.get_turn(turn_id), False

    def get_turn(self, turn_id: str) -> InvestigationTurn:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investigation_turns WHERE id = ?", (turn_id,)
            ).fetchone()
        if row is None:
            raise InvestigationTurnNotFoundError(turn_id)
        return self._turn(row)

    def get_turn_by_client_message(
        self, session_id: str, client_message_id: str
    ) -> InvestigationTurn | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM investigation_turns
                WHERE session_id = ? AND client_message_id = ?
                """,
                (session_id, client_message_id),
            ).fetchone()
        return self._turn(row) if row else None

    def list_turns(self, session_id: str) -> tuple[InvestigationTurn, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM investigation_turns
                WHERE session_id = ? ORDER BY created_at, id
                """,
                (session_id,),
            ).fetchall()
        return tuple(self._turn(row) for row in rows)

    def get_user_message_for_turn(self, turn_id: str) -> InvestigationMessage:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT m.* FROM investigation_messages m
                JOIN investigation_turns t ON t.user_message_id = m.id
                WHERE t.id = ?
                """,
                (turn_id,),
            ).fetchone()
        if row is None:
            raise InvestigationTurnNotFoundError(turn_id)
        return self._message(row)

    def list_running_turns(self) -> tuple[InvestigationTurn, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM investigation_turns
                WHERE status = 'running' ORDER BY created_at, id
                """
            ).fetchall()
        return tuple(self._turn(row) for row in rows)

    def append_public_turn_event(
        self,
        turn_id: str,
        *,
        stage: str,
        answer: str = "",
        safe_message: str = "",
        retryable: bool = False,
        artifact: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "accepted",
            "planning",
            "preparing_sources",
            "acquiring_source",
            "answering",
            "completed",
            "interrupted",
            "failed",
        }
        normalized_stage = str(stage or "").strip()
        if normalized_stage not in allowed:
            raise ValueError("invalid public Turn stage")
        normalized_answer = str(answer or "")
        normalized_safe_message = str(safe_message or "")
        normalized_artifact = dict(artifact or {})
        artifact_json = self._dump(normalized_artifact)
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            turn = connection.execute(
                "SELECT id FROM investigation_turns WHERE id = ?", (turn_id,)
            ).fetchone()
            if turn is None:
                raise InvestigationTurnNotFoundError(turn_id)
            latest = connection.execute(
                """
                SELECT * FROM investigation_public_turn_events
                WHERE turn_id = ? ORDER BY sequence DESC LIMIT 1
                """,
                (turn_id,),
            ).fetchone()
            latest_turn = connection.execute(
                """
                SELECT * FROM investigation_public_turn_events
                WHERE turn_id = ? AND event_type = 'turn'
                ORDER BY sequence DESC LIMIT 1
                """,
                (turn_id,),
            ).fetchone()
            if latest_turn is not None and (
                str(latest_turn["stage"]) == normalized_stage
                and str(latest_turn["answer"]) == normalized_answer
                and str(latest_turn["safe_message"]) == normalized_safe_message
                and bool(latest_turn["retryable"]) is bool(retryable)
                and str(latest_turn["artifact_json"]) == artifact_json
            ):
                return self._public_turn_event(latest_turn)
            sequence = int(latest["sequence"] if latest is not None else 0) + 1
            event_id = (
                "investigation-turn-event:"
                + stable_hash(
                    {
                        "turn_id": turn_id,
                        "sequence": sequence,
                        "stage": normalized_stage,
                    }
                )[:32]
            )
            connection.execute(
                """
                INSERT INTO investigation_public_turn_events (
                    event_id, turn_id, sequence, event_type, stage, answer,
                    safe_message, retryable, artifact_json, payload_json,
                    occurred_at
                ) VALUES (?, ?, ?, 'turn', ?, ?, ?, ?, ?, '{}', ?)
                """,
                (
                    event_id,
                    turn_id,
                    sequence,
                    normalized_stage,
                    normalized_answer,
                    normalized_safe_message,
                    int(retryable),
                    artifact_json,
                    now,
                ),
            )
            stored = connection.execute(
                "SELECT * FROM investigation_public_turn_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return self._public_turn_event(stored)

    def append_public_stream_event(
        self,
        turn_id: str,
        *,
        event_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_type = str(event_type or "").strip()
        if normalized_type not in PUBLIC_STREAM_EVENT_TYPES:
            raise ValueError("invalid public stream event type")
        normalized_key = str(idempotency_key or "").strip()
        if not re.fullmatch(r"public-event:[0-9a-f]{16,64}", normalized_key):
            raise ValueError("invalid public stream event idempotency key")
        normalized_payload = normalize_public_stream_payload(
            normalized_type,
            dict(payload or {}),
        )
        payload_json = self._dump(normalized_payload)
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            turn = connection.execute(
                "SELECT id, status FROM investigation_turns WHERE id = ?", (turn_id,)
            ).fetchone()
            if turn is None:
                raise InvestigationTurnNotFoundError(turn_id)
            existing = connection.execute(
                """
                SELECT * FROM investigation_public_turn_events
                WHERE turn_id = ? AND event_type = ? AND idempotency_key = ?
                """,
                (turn_id, normalized_type, normalized_key),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_json"]) != payload_json:
                    raise ValueError("public stream event idempotency conflict")
                return self._public_turn_event(existing)
            if str(turn["status"]) != "running":
                raise ValueError("public stream events require a running Turn")
            latest = connection.execute(
                """
                SELECT * FROM investigation_public_turn_events
                WHERE turn_id = ? ORDER BY sequence DESC LIMIT 1
                """,
                (turn_id,),
            ).fetchone()
            latest_turn = connection.execute(
                """
                SELECT stage FROM investigation_public_turn_events
                WHERE turn_id = ? AND event_type = 'turn'
                ORDER BY sequence DESC LIMIT 1
                """,
                (turn_id,),
            ).fetchone()
            sequence = int(latest["sequence"] if latest is not None else 0) + 1
            legacy_stage = str(latest_turn["stage"]) if latest_turn else "accepted"
            event_id = (
                "investigation-stream-event:"
                + stable_hash(
                    {
                        "turn_id": turn_id,
                        "event_type": normalized_type,
                        "idempotency_key": normalized_key,
                    }
                )[:32]
            )
            connection.execute(
                """
                INSERT INTO investigation_public_turn_events (
                    event_id, turn_id, sequence, event_type, idempotency_key,
                    stage, payload_json, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    turn_id,
                    sequence,
                    normalized_type,
                    normalized_key,
                    legacy_stage,
                    payload_json,
                    now,
                ),
            )
            stored = connection.execute(
                "SELECT * FROM investigation_public_turn_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return self._public_turn_event(stored)

    def list_public_turn_events(
        self,
        turn_id: str,
        *,
        after_sequence: int = 0,
        event_types: tuple[str, ...] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        self.get_turn(turn_id)
        normalized_types = tuple(
            dict.fromkeys(
                str(event_type or "").strip()
                for event_type in (event_types or ())
                if str(event_type or "").strip()
            )
        )
        if event_types is not None and not normalized_types:
            return ()
        type_clause = ""
        parameters: list[Any] = [turn_id, max(0, int(after_sequence))]
        if normalized_types:
            placeholders = ", ".join("?" for _ in normalized_types)
            type_clause = f" AND event_type IN ({placeholders})"
            parameters.extend(normalized_types)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM investigation_public_turn_events
                WHERE turn_id = ? AND sequence > ?
                {type_clause}
                ORDER BY sequence, event_id
                """,
                tuple(parameters),
            ).fetchall()
        return tuple(self._public_turn_event(row) for row in rows)

    def get_public_turn_event_sequence(self, turn_id: str, event_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT sequence FROM investigation_public_turn_events
                WHERE turn_id = ? AND event_id = ?
                """,
                (turn_id, event_id),
            ).fetchone()
        if row is None:
            raise InvestigationTurnNotFoundError("public Turn event not found")
        return int(row["sequence"])

    def get_latest_public_turn_event_sequence(self, turn_id: str) -> int:
        self.get_turn(turn_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0)
                FROM investigation_public_turn_events
                WHERE turn_id = ?
                """,
                (turn_id,),
            ).fetchone()
        return int(row[0] if row is not None else 0)

    def set_turn_node(self, turn_id: str, node_name: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE investigation_turns SET current_node = ?
                WHERE id = ? AND status = 'running'
                """,
                (node_name, turn_id),
            )

    def mark_interrupted(
        self,
        turn_id: str,
        *,
        error_code: str,
        safe_message: str,
        retryable: bool,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE investigation_turns
                SET status = 'interrupted', error_code = ?, safe_message = ?, retryable = ?
                WHERE id = ? AND status = 'running'
                """,
                (error_code, safe_message, int(retryable), turn_id),
            )

    def begin_resume(self, turn_id: str) -> InvestigationTurn:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM investigation_turns WHERE id = ?", (turn_id,)
                ).fetchone()
                if row is None:
                    raise InvestigationTurnNotFoundError(turn_id)
                if str(row["status"]) == "completed":
                    return self._turn(row)
                if str(row["status"]) != "interrupted":
                    raise InvestigationTurnNotFoundError("turn is not resumable")
                connection.execute(
                    """
                    UPDATE investigation_turns
                    SET status = 'running', error_code = '', safe_message = '', retryable = 0
                    WHERE id = ?
                    """,
                    (turn_id,),
                )
        except sqlite3.IntegrityError as exc:
            raise ConcurrentTurnError() from exc
        return self.get_turn(turn_id)

    def complete_turn(
        self,
        turn_id: str,
        *,
        answer: str,
        trace_messages: list[dict[str, Any]],
        pending_sources: list[dict[str, Any]],
        grounding_validation: dict[str, Any],
        resolved_references: list[dict[str, Any]],
        all_tool_calls: list[dict[str, Any]],
        query_receipts: list[dict[str, Any]],
        summary_text: str,
        active_focus: dict[str, Any],
        ordered_referents: list[dict[str, Any]],
        last_claim_id: str,
        last_finding_id: str,
        last_evidence_id: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        llm_call_count: int,
        stop_reason: str,
        context_accounting: list[dict[str, Any]],
        grounding_issues: list[dict[str, Any]],
        grounding_repair_count: int,
        scope_repair_count: int,
        source_repair_count: int,
        semantic_rewrite_count: int,
        scope_initial_draft: str,
        scope_repaired_draft: str,
        scope_initial_issues: list[dict[str, Any]],
        scope_remaining_issues: list[dict[str, Any]],
        hermes_transcript: list[dict[str, Any]] | None = None,
        public_artifact: dict[str, Any] | None = None,
        proposal_snapshots: list[dict[str, Any]] | None = None,
    ) -> tuple[InvestigationTurn, InvestigationMessage, tuple[SourceLedgerEntry, ...]]:
        now = utc_now()
        transcript_json = (
            None
            if hermes_transcript is None
            else self._hermes_transcript_json(hermes_transcript)
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            turn_row = connection.execute(
                "SELECT * FROM investigation_turns WHERE id = ?", (turn_id,)
            ).fetchone()
            if turn_row is None:
                raise InvestigationTurnNotFoundError(turn_id)
            if str(turn_row["status"]) == "completed":
                assistant = connection.execute(
                    "SELECT * FROM investigation_messages WHERE id = ?",
                    (turn_row["assistant_message_id"],),
                ).fetchone()
                ledgers = connection.execute(
                    "SELECT * FROM investigation_source_ledger WHERE turn_id = ? ORDER BY created_at, ledger_id",
                    (turn_id,),
                ).fetchall()
                return self._turn(turn_row), self._message(assistant), tuple(
                    self._ledger(item) for item in ledgers
                )
            if str(turn_row["status"]) != "running":
                raise InvestigationTurnNotFoundError("turn is not running")
            session_id = str(turn_row["session_id"])
            if transcript_json is not None:
                session_identity = connection.execute(
                    """
                    SELECT report_version_id, snapshot_hash
                    FROM investigation_sessions WHERE id = ?
                    """,
                    (session_id,),
                ).fetchone()
                if session_identity is None:
                    raise InvestigationSessionNotFoundError(session_id)
                connection.execute(
                    """
                    INSERT INTO investigation_hermes_transcripts (
                        turn_id, session_id, report_version_id, snapshot_hash,
                        messages_json, messages_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        turn_id,
                        session_id,
                        str(session_identity["report_version_id"]),
                        str(session_identity["snapshot_hash"]),
                        transcript_json,
                        hashlib.sha256(transcript_json.encode("utf-8")).hexdigest(),
                        now,
                    ),
                )
            self._insert_query_receipts(
                connection,
                session_id=session_id,
                turn_id=turn_id,
                receipts=query_receipts,
            )
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM investigation_messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
            )
            for index, message in enumerate(trace_messages, start=1):
                role = str(message.get("role") or "")
                if role not in {"assistant", "tool"}:
                    continue
                sequence += 1
                trace_id = self._stable_message_id(turn_id, f"trace:{index}")
                metadata = {
                    key: value
                    for key, value in message.items()
                    if key not in {"role", "content", "tool_call_id", "name"}
                }
                connection.execute(
                    """
                    INSERT OR IGNORE INTO investigation_messages (
                        id, session_id, turn_id, role, content, tool_call_id, tool_name,
                        metadata_json, sequence, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trace_id,
                        session_id,
                        turn_id,
                        role,
                        str(message.get("content") or ""),
                        str(message.get("tool_call_id") or ""),
                        str(message.get("name") or ""),
                        self._dump(metadata),
                        sequence,
                        now,
                    ),
                )
            sequence += 1
            assistant_id = self._stable_message_id(turn_id, "answer")
            if proposal_snapshots:
                from backend.investigation_creation.presentation import message_presentations

                scope = connection.execute(
                    "SELECT scope_type FROM investigation_sessions WHERE id = ?", (session_id,),
                ).fetchone()
                if scope["scope_type"] != "creation":
                    raise ValueError("Proposal presentation requires a creation Session")
                presentations = message_presentations(
                    proposal_snapshots, session_id=session_id, turn_id=turn_id,
                    user_message_id=str(turn_row["user_message_id"]),
                    assistant_message_id=assistant_id, presented_at=now,
                )
                answer = "\n\n".join([answer, *(item["text"] for item in presentations)]).strip()
                public_artifact = dict(public_artifact or {"artifact_type": "ruleset_proposal_presentation"})
                public_artifact["proposal_presentations"] = presentations
                from pydantic import TypeAdapter
                from backend.investigation_creation.public_projection import InvestigationConversationArtifact

                TypeAdapter(InvestigationConversationArtifact).validate_python(public_artifact)
            assistant_metadata = {
                "grounding_validation": grounding_validation,
                "resolved_references": resolved_references,
                "tool_calls": all_tool_calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "llm_call_count": llm_call_count,
                "stop_reason": stop_reason,
                "context_accounting": context_accounting,
                "grounding_issues": grounding_issues,
                "grounding_repair_count": grounding_repair_count,
                "scope_repair_count": scope_repair_count,
                "source_repair_count": source_repair_count,
                "semantic_rewrite_count": semantic_rewrite_count,
                "scope_initial_draft": scope_initial_draft,
                "scope_repaired_draft": scope_repaired_draft,
                "scope_initial_issues": scope_initial_issues,
                "scope_remaining_issues": scope_remaining_issues,
                "source_semantics": "retrieved_accessed_source_audit",
            }
            connection.execute(
                """
                INSERT OR IGNORE INTO investigation_messages (
                    id, session_id, turn_id, role, content, metadata_json, sequence, created_at
                ) VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?)
                """,
                (
                    assistant_id,
                    session_id,
                    turn_id,
                    answer,
                    self._dump(assistant_metadata),
                    sequence,
                    now,
                ),
            )
            if proposal_snapshots:
                stored_answer = connection.execute(
                    "SELECT session_id, turn_id, role, content FROM investigation_messages WHERE id = ?",
                    (assistant_id,),
                ).fetchone()
                if tuple(stored_answer) != (session_id, turn_id, "assistant", answer):
                    raise ValueError("Proposal presentation does not match the public assistant message")
            normalized_referents = []
            for item in ordered_referents:
                value = dict(item)
                if not value.get("source_message_id"):
                    value["source_message_id"] = assistant_id
                normalized_referents.append(Referent.model_validate(value).model_dump(mode="json"))
            unique_sources: dict[str, SourceLedgerCandidate] = {}
            for raw in pending_sources:
                source = SourceLedgerCandidate.model_validate(raw)
                identity = stable_hash(source.model_dump(mode="json"))
                unique_sources.setdefault(identity, source)
            for identity, source in unique_sources.items():
                ledger_id = f"source-ledger:{stable_hash({'message_id': assistant_id, 'source': identity})[:32]}"
                connection.execute(
                    """
                    INSERT OR IGNORE INTO investigation_source_ledger (
                        ledger_id, session_id, turn_id, message_id, report_version_id,
                        snapshot_hash, source_kind, metric_key, section_id, claim_id,
                        finding_id, evidence_id, source_hash, excerpt, asset_status,
                        query_fingerprint, tool_call_id, query_receipt_id, warnings_json,
                        freshness, created_at
                    ) SELECT ?, ?, ?, ?, report_version_id, snapshot_hash, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    FROM investigation_sessions WHERE id = ?
                    """,
                    (
                        ledger_id,
                        session_id,
                        turn_id,
                        assistant_id,
                        source.source_kind.value,
                        source.metric_key,
                        source.section_id,
                        source.claim_id,
                        source.finding_id,
                        source.evidence_id,
                        source.source_hash,
                        source.excerpt,
                        source.asset_status,
                        source.query_fingerprint,
                        source.tool_call_id,
                        source.query_receipt_id,
                        self._dump(source.warnings),
                        source.freshness,
                        now,
                        session_id,
                    ),
                )
            connection.execute(
                """
                UPDATE investigation_sessions
                SET summary_text = ?, active_focus_json = ?, ordered_referents_json = ?,
                    last_claim_id = ?, last_finding_id = ?, last_evidence_id = ?,
                    last_answer_message_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    summary_text,
                    self._dump(active_focus),
                    self._dump(normalized_referents),
                    last_claim_id,
                    last_finding_id,
                    last_evidence_id,
                    assistant_id,
                    now,
                    session_id,
                ),
            )
            connection.execute(
                """
                UPDATE investigation_turns
                SET assistant_message_id = ?, status = 'completed', current_node = 'persist_turn',
                    input_tokens = ?, output_tokens = ?, total_tokens = ?, llm_call_count = ?,
                    stop_reason = ?, completed_at = ?, error_code = '', safe_message = '', retryable = 0,
                    public_artifact_json = ?
                WHERE id = ?
                """,
                (
                    assistant_id,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    llm_call_count,
                    stop_reason,
                    now,
                    self._dump(public_artifact or {}),
                    turn_id,
                ),
            )
        turn = self.get_turn(turn_id)
        message = self.get_message(assistant_id)
        return turn, message, self.list_ledger(turn_id=turn_id)

    def fail_turn(
        self,
        turn_id: str,
        *,
        error_code: str,
        safe_message: str,
        retryable: bool,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        llm_call_count: int = 0,
        stop_reason: str = "error",
        trace_messages: list[dict[str, Any]] | None = None,
        pending_sources: list[dict[str, Any]] | None = None,
        grounding_validation: dict[str, Any] | None = None,
        resolved_references: list[dict[str, Any]] | None = None,
        all_tool_calls: list[dict[str, Any]] | None = None,
        all_tool_results: list[dict[str, Any]] | None = None,
        query_receipts: list[dict[str, Any]] | None = None,
        grounding_errors: list[str] | None = None,
        grounding_issues: list[dict[str, Any]] | None = None,
        context_accounting: list[dict[str, Any]] | None = None,
        grounding_repair_count: int = 0,
        scope_repair_count: int = 0,
        source_repair_count: int = 0,
        semantic_rewrite_count: int = 0,
        scope_initial_draft: str = "",
        scope_repaired_draft: str = "",
        scope_initial_issues: list[dict[str, Any]] | None = None,
        scope_remaining_issues: list[dict[str, Any]] | None = None,
    ) -> InvestigationTurn:
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM investigation_turns WHERE id = ?", (turn_id,)
            ).fetchone()
            if row is None:
                raise InvestigationTurnNotFoundError(turn_id)
            if str(row["status"]) == "completed":
                return self._turn(row)
            session_id = str(row["session_id"])
            self._insert_query_receipts(
                connection,
                session_id=session_id,
                turn_id=turn_id,
                receipts=list(query_receipts or []),
            )
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM investigation_messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
            )
            for index, message in enumerate(trace_messages or [], start=1):
                role = str(message.get("role") or "")
                if role not in {"assistant", "tool"}:
                    continue
                sequence += 1
                trace_id = self._stable_message_id(turn_id, f"error-trace:{index}")
                metadata = {
                    key: value
                    for key, value in message.items()
                    if key not in {"role", "content", "tool_call_id", "name"}
                }
                connection.execute(
                    """
                    INSERT OR IGNORE INTO investigation_messages (
                        id, session_id, turn_id, role, content, tool_call_id, tool_name,
                        metadata_json, sequence, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trace_id,
                        session_id,
                        turn_id,
                        role,
                        str(message.get("content") or ""),
                        str(message.get("tool_call_id") or ""),
                        str(message.get("name") or ""),
                        self._dump(metadata),
                        sequence,
                        now,
                    ),
                )
            sequence += 1
            assistant_id = self._stable_message_id(turn_id, "error")
            tool_calls = list(all_tool_calls or [])
            tool_results = list(all_tool_results or [])
            assistant_metadata = {
                "error_code": error_code,
                "retryable": retryable,
                "grounding_validation": grounding_validation or {
                    "status": "failed",
                    "source_count": len(pending_sources or []),
                    "warnings": list(grounding_errors or []),
                },
                "grounding_errors": list(grounding_errors or []),
                "grounding_issues": list(grounding_issues or []),
                "context_accounting": list(context_accounting or []),
                "grounding_repair_count": grounding_repair_count,
                "scope_repair_count": scope_repair_count,
                "source_repair_count": source_repair_count,
                "semantic_rewrite_count": semantic_rewrite_count,
                "scope_initial_draft": scope_initial_draft,
                "scope_repaired_draft": scope_repaired_draft,
                "scope_initial_issues": list(scope_initial_issues or []),
                "scope_remaining_issues": list(scope_remaining_issues or []),
                "source_semantics": "retrieved_accessed_source_audit",
                "resolved_references": list(resolved_references or []),
                "tool_calls": tool_calls,
                "tool_audit": self._tool_audit(tool_calls, tool_results),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "llm_call_count": llm_call_count,
                "stop_reason": stop_reason,
            }
            connection.execute(
                """
                INSERT OR IGNORE INTO investigation_messages (
                    id, session_id, turn_id, role, content, metadata_json, sequence, created_at
                ) VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?)
                """,
                (
                    assistant_id,
                    session_id,
                    turn_id,
                    safe_message,
                    self._dump(assistant_metadata),
                    sequence,
                    now,
                ),
            )
            unique_sources: dict[str, SourceLedgerCandidate] = {}
            for raw in pending_sources or []:
                source = SourceLedgerCandidate.model_validate(raw)
                identity = stable_hash(source.model_dump(mode="json"))
                unique_sources.setdefault(identity, source)
            for identity, source in unique_sources.items():
                ledger_id = f"source-ledger:{stable_hash({'message_id': assistant_id, 'source': identity})[:32]}"
                connection.execute(
                    """
                    INSERT OR IGNORE INTO investigation_source_ledger (
                        ledger_id, session_id, turn_id, message_id, report_version_id,
                        snapshot_hash, source_kind, metric_key, section_id, claim_id,
                        finding_id, evidence_id, source_hash, excerpt, asset_status,
                        query_fingerprint, tool_call_id, query_receipt_id, warnings_json,
                        freshness, created_at
                    ) SELECT ?, ?, ?, ?, report_version_id, snapshot_hash, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    FROM investigation_sessions WHERE id = ?
                    """,
                    (
                        ledger_id,
                        session_id,
                        turn_id,
                        assistant_id,
                        source.source_kind.value,
                        source.metric_key,
                        source.section_id,
                        source.claim_id,
                        source.finding_id,
                        source.evidence_id,
                        source.source_hash,
                        source.excerpt,
                        source.asset_status,
                        source.query_fingerprint,
                        source.tool_call_id,
                        source.query_receipt_id,
                        self._dump(source.warnings),
                        source.freshness,
                        now,
                        session_id,
                    ),
                )
            connection.execute(
                """
                UPDATE investigation_turns
                SET assistant_message_id = ?, status = 'error', error_code = ?, safe_message = ?,
                    retryable = ?, input_tokens = ?, output_tokens = ?, total_tokens = ?,
                    llm_call_count = ?, stop_reason = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    assistant_id,
                    error_code,
                    safe_message,
                    int(retryable),
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    llm_call_count,
                    stop_reason,
                    now,
                    turn_id,
                ),
            )
        return self.get_turn(turn_id)

    def get_message(self, message_id: str) -> InvestigationMessage:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investigation_messages WHERE id = ?", (message_id,)
            ).fetchone()
        if row is None:
            raise InvestigationTurnNotFoundError("message not found")
        return self._message(row)

    def list_messages(
        self, session_id: str, *, include_tool_messages: bool = True
    ) -> tuple[InvestigationMessage, ...]:
        where = "session_id = ?"
        if not include_tool_messages:
            where += " AND role IN ('user', 'assistant')"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM investigation_messages WHERE {where} ORDER BY sequence",
                (session_id,),
            ).fetchall()
        return tuple(self._message(row) for row in rows)

    def recent_conversation(
        self, session_id: str, *, max_turns: int = 12
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT m.role, m.content, m.turn_id, m.sequence
                FROM investigation_messages m
                JOIN investigation_turns t ON t.id = m.turn_id
                WHERE m.session_id = ? AND m.role IN ('user', 'assistant')
                  AND t.status = 'completed'
                  AND (m.role = 'user' OR m.id = t.assistant_message_id)
                ORDER BY m.sequence DESC
                LIMIT ?
                """,
                (session_id, max_turns * 2),
            ).fetchall()
        return [
            {"role": str(row["role"]), "content": str(row["content"])}
            for row in reversed(rows)
        ]

    def latest_completed_hermes_transcript(
        self, session_id: str
    ) -> list[dict[str, Any]] | None:
        """Return a private full Hermes transcript, never a public DTO projection."""

        self.get_session(session_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT h.messages_json, h.messages_sha256
                FROM investigation_hermes_transcripts h
                JOIN investigation_turns t ON t.id = h.turn_id
                WHERE h.session_id = ? AND t.status = 'completed'
                ORDER BY t.completed_at DESC, h.turn_id DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        serialized = str(row["messages_json"])
        actual_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if actual_hash != str(row["messages_sha256"]):
            raise InvestigationTurnNotFoundError(
                "private Hermes transcript failed its integrity check"
            )
        value = json.loads(serialized)
        if not isinstance(value, list):
            raise InvestigationTurnNotFoundError("private Hermes transcript is invalid")
        try:
            validate_hermes_transcript_messages(value)
        except Exception as exc:
            raise InvestigationTurnNotFoundError(
                "private Hermes transcript failed protocol validation"
            ) from exc
        return [dict(item) for item in value]

    def hermes_conversation_history(self, turn_id: str) -> list[dict[str, Any]] | None:
        """Keep unanswered user intent without promoting a failed draft to evidence."""
        turn = self.get_turn(turn_id)
        history = self.latest_completed_hermes_transcript(turn.session_id) or []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT m.content FROM investigation_messages m
                JOIN investigation_turns t ON t.id = m.turn_id
                WHERE m.session_id = ? AND m.role = 'user'
                  AND t.status IN ('error', 'interrupted')
                  AND m.sequence < (
                    SELECT sequence FROM investigation_messages
                    WHERE turn_id = ? AND role = 'user'
                  )
                  AND m.sequence > COALESCE((
                    SELECT u.sequence FROM investigation_hermes_transcripts h
                    JOIN investigation_turns done ON done.id = h.turn_id
                    JOIN investigation_messages u ON u.turn_id = done.id AND u.role = 'user'
                    WHERE h.session_id = ? AND done.status = 'completed'
                    ORDER BY done.completed_at DESC, h.turn_id DESC LIMIT 1
                  ), 0)
                ORDER BY m.sequence
                """,
                (turn.session_id, turn_id, turn.session_id),
            ).fetchall()
        for row in rows:
            history.extend([
                {"role": "user", "content": str(row["content"])},
                {"role": "assistant", "content": (
                    "上次请求未完成，未形成可采信的查询结论。用户指定的账号、范围和修改要求仍然有效。"
                    "后续追问应沿用最近明确指定的目标；需要重新查询，无法确定目标时先澄清，"
                    "不要自动改成报告博主。"
                )},
            ])
        return history or None

    @staticmethod
    def _hermes_transcript_json(messages: list[dict[str, Any]]) -> str:
        validate_hermes_transcript_messages(messages)
        return json.dumps(
            messages,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def list_ledger(
        self,
        *,
        session_id: str = "",
        turn_id: str = "",
        message_id: str = "",
        limit: int = 200,
    ) -> tuple[SourceLedgerEntry, ...]:
        where = []
        values: list[Any] = []
        if session_id:
            where.append("session_id = ?")
            values.append(session_id)
        if turn_id:
            where.append("turn_id = ?")
            values.append(turn_id)
        if message_id:
            where.append("message_id = ?")
            values.append(message_id)
        clause = " AND ".join(where) or "1 = 1"
        values.append(min(max(1, limit), 1000))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM investigation_source_ledger
                WHERE {clause} ORDER BY created_at DESC, ledger_id LIMIT ?
                """,
                values,
            ).fetchall()
        return tuple(self._ledger(row) for row in reversed(rows))

    def list_query_receipts(
        self,
        *,
        session_id: str = "",
        turn_id: str = "",
        tool_call_id: str = "",
        limit: int = 500,
    ) -> tuple[ToolQueryReceipt, ...]:
        where = []
        values: list[Any] = []
        if session_id:
            where.append("session_id = ?")
            values.append(session_id)
        if turn_id:
            where.append("turn_id = ?")
            values.append(turn_id)
        if tool_call_id:
            where.append("tool_call_id = ?")
            values.append(tool_call_id)
        clause = " AND ".join(where) or "1 = 1"
        values.append(min(max(1, limit), 5_000))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM investigation_tool_query_receipts
                WHERE {clause} ORDER BY executed_at DESC, receipt_id LIMIT ?
                """,
                values,
            ).fetchall()
        return tuple(self._query_receipt(row) for row in reversed(rows))

    def put_artifact_bundle(
        self,
        query_result: QueryResultArtifact,
        source_artifacts: tuple[SourceArtifact, ...],
        *,
        link: QueryResultArtifactLink | None = None,
    ) -> QueryResultArtifact:
        """Atomically persist one immutable result graph without changing Agent paths."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._put_artifact_bundle(
                connection,
                query_result=query_result,
                source_artifacts=source_artifacts,
                link=link,
            )
        stored = self.get_query_result_artifact(query_result.artifact_id)
        if stored is None:
            raise ArtifactNotFoundError(query_result.artifact_id)
        return stored

    def put_query_receipt_artifacts(
        self,
        receipt: ToolQueryReceipt,
        query_result: QueryResultArtifact,
        source_artifacts: tuple[SourceArtifact, ...],
        *,
        link: QueryResultArtifactLink,
    ) -> QueryResultArtifact:
        """Atomically persist a successful Receipt and its canonical Artifact graph."""
        if link.receipt_id != receipt.receipt_id:
            raise ArtifactIntegrityError("artifact link points to a different Receipt")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_query_receipts(
                connection,
                session_id=receipt.session_id,
                turn_id=receipt.turn_id,
                receipts=[receipt.model_dump(mode="json")],
            )
            self._put_artifact_bundle(
                connection,
                query_result=query_result,
                source_artifacts=source_artifacts,
                link=link,
            )
        stored = self.get_query_result_artifact(query_result.artifact_id)
        if stored is None:
            raise ArtifactNotFoundError(query_result.artifact_id)
        return stored

    def _put_artifact_bundle(
        self,
        connection: sqlite3.Connection,
        *,
        query_result: QueryResultArtifact,
        source_artifacts: tuple[SourceArtifact, ...],
        link: QueryResultArtifactLink | None,
    ) -> None:
        source_by_id = {item.artifact_id: item for item in source_artifacts}
        member_ids = {item.source_artifact_id for item in query_result.ordered_members}
        if len(source_by_id) != len(source_artifacts) or set(source_by_id) != member_ids:
            raise ArtifactIntegrityError(
                "source_artifacts must exactly match query result membership"
            )
        if link is not None and link.query_result_artifact_id != query_result.artifact_id:
            raise ArtifactIntegrityError("artifact link points to a different result")

        session = connection.execute(
            """
            SELECT report_version_id, source_snapshot_id, snapshot_hash
            FROM investigation_sessions WHERE id = ?
            """,
            (query_result.session_id,),
        ).fetchone()
        if session is None:
            raise InvestigationSessionNotFoundError(query_result.session_id)
        expected_scope = (
            query_result.session_id,
            str(session["report_version_id"]),
            str(session["source_snapshot_id"]),
            str(session["snapshot_hash"]),
        )
        if self._artifact_scope(query_result) != expected_scope:
            raise ArtifactIntegrityError("query result scope does not match session")

        for member in query_result.ordered_members:
            source = source_by_id[member.source_artifact_id]
            if self._artifact_scope(source) != expected_scope:
                raise ArtifactIntegrityError("source artifact scope does not match result")
            if (
                member.stable_source_ref != source.stable_source_ref
                or member.content_level != source.content_level
            ):
                raise ArtifactIntegrityError(
                    "query member does not match its source artifact"
                )
            self._insert_source_artifact(connection, source)

        self._insert_query_result_artifact(connection, query_result)
        if link is not None:
            self._insert_query_artifact_link(
                connection, query_result=query_result, link=link
            )

    def get_source_artifact(self, artifact_id: str) -> SourceArtifact | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investigation_source_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        return self._source_artifact(row) if row is not None else None

    def get_query_result_artifact(
        self, artifact_id: str
    ) -> QueryResultArtifact | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM investigation_query_result_artifacts
                WHERE artifact_id = ?
                """,
                (artifact_id,),
            ).fetchone()
            if row is None:
                return None
            members = connection.execute(
                """
                SELECT * FROM investigation_query_result_members
                WHERE query_result_artifact_id = ? ORDER BY ordinal
                """,
                (artifact_id,),
            ).fetchall()
        return self._query_result_artifact(row, members)

    def get_query_result_for_receipt(
        self, receipt_id: str
    ) -> QueryResultArtifact | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT query_result_artifact_id
                FROM investigation_query_artifact_links WHERE receipt_id = ?
                """,
                (receipt_id,),
            ).fetchone()
        if row is None:
            return None
        return self.get_query_result_artifact(str(row["query_result_artifact_id"]))

    def list_source_artifacts(
        self, *, session_id: str = "", limit: int = 500
    ) -> tuple[SourceArtifact, ...]:
        where = "session_id = ?" if session_id else "1 = 1"
        values: list[Any] = [session_id] if session_id else []
        values.append(min(max(1, limit), 5_000))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM investigation_source_artifacts
                WHERE {where} ORDER BY observed_at, artifact_id LIMIT ?
                """,
                values,
            ).fetchall()
        return tuple(self._source_artifact(row) for row in rows)

    def list_query_result_artifacts(
        self, *, session_id: str = "", limit: int = 500
    ) -> tuple[QueryResultArtifact, ...]:
        where = "session_id = ?" if session_id else "1 = 1"
        values: list[Any] = [session_id] if session_id else []
        values.append(min(max(1, limit), 5_000))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT artifact_id FROM investigation_query_result_artifacts
                WHERE {where} ORDER BY observed_at, artifact_id LIMIT ?
                """,
                values,
            ).fetchall()
        output = []
        for row in rows:
            artifact = self.get_query_result_artifact(str(row["artifact_id"]))
            if artifact is not None:
                output.append(artifact)
        return tuple(output)

    def list_query_artifact_index(
        self,
        *,
        session_id: str = "",
        report_version_id: str = "",
        snapshot_hash: str = "",
        operation: str = "",
        query_fingerprint: str = "",
        limit: int = 500,
    ) -> tuple[QueryArtifactIndexEntry, ...]:
        """Rebuild the query index from successful Receipts and immutable Artifacts."""
        where = ["r.status = 'ok'"]
        values: list[Any] = []
        for column, value in (
            ("q.session_id", session_id),
            ("q.report_version_id", report_version_id),
            ("q.snapshot_hash", snapshot_hash),
            ("q.operation", operation),
            ("q.query_fingerprint", query_fingerprint),
        ):
            if value:
                where.append(f"{column} = ?")
                values.append(value)
        values.append(min(max(1, limit), 5_000))
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT l.receipt_id, l.query_result_artifact_id, l.linked_at,
                       q.session_id, q.report_version_id, q.source_snapshot_id,
                       q.snapshot_hash, q.operation, q.subject_ref,
                       q.normalized_query_json, q.normalized_filters_json,
                       q.query_fingerprint, q.result_fingerprint, q.observed_at
                FROM investigation_query_artifact_links AS l
                JOIN investigation_tool_query_receipts AS r
                  ON r.receipt_id = l.receipt_id
                JOIN investigation_query_result_artifacts AS q
                  ON q.artifact_id = l.query_result_artifact_id
                WHERE {' AND '.join(where)}
                ORDER BY q.observed_at DESC, q.artifact_id
                LIMIT ?
                """,
                values,
            ).fetchall()
        try:
            return tuple(
                QueryArtifactIndexEntry(
                    receipt_id=str(row["receipt_id"]),
                    query_result_artifact_id=str(row["query_result_artifact_id"]),
                    session_id=str(row["session_id"]),
                    report_version_id=str(row["report_version_id"]),
                    source_snapshot_id=str(row["source_snapshot_id"]),
                    snapshot_hash=str(row["snapshot_hash"]),
                    operation=str(row["operation"]),
                    subject_ref=str(row["subject_ref"]),
                    normalized_query=self._json(row["normalized_query_json"], {}),
                    normalized_filters=self._json(
                        row["normalized_filters_json"], {}
                    ),
                    query_fingerprint=str(row["query_fingerprint"]),
                    result_fingerprint=str(row["result_fingerprint"]),
                    observed_at=str(row["observed_at"]),
                    linked_at=str(row["linked_at"]),
                )
                for row in rows
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("query artifact index is corrupt") from exc

    def put_planner_shadow_trace(
        self, trace: PlannerShadowTrace
    ) -> PlannerShadowTrace:
        with self._connect() as connection:
            scope = connection.execute(
                """
                SELECT t.session_id, s.report_version_id, s.snapshot_hash
                FROM investigation_turns AS t
                JOIN investigation_sessions AS s ON s.id = t.session_id
                WHERE t.id = ?
                """,
                (trace.turn_id,),
            ).fetchone()
            if scope is None:
                raise InvestigationTurnNotFoundError(trace.turn_id)
            if (
                trace.session_id != str(scope["session_id"])
                or trace.report_version_id != str(scope["report_version_id"])
                or trace.snapshot_hash != str(scope["snapshot_hash"])
            ):
                raise ValueError("planner shadow trace scope does not match turn")
            connection.execute(
                """
                INSERT OR IGNORE INTO investigation_planner_shadow_traces (
                    trace_id, schema_version, session_id, turn_id, report_version_id,
                    snapshot_hash, planner_prompt_version, input_fingerprint,
                    active_focus_exists, active_focus_type, resolved_referent_status,
                    resolved_referent_type, planning_status, plan_json, error_code,
                    error_message, planner_error, model, request_id, planner_input_tokens,
                    planner_output_tokens, planner_total_tokens,
                    planner_llm_call_count, planner_latency_ms,
                    planner_retry_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.trace_id,
                    trace.schema_version,
                    trace.session_id,
                    trace.turn_id,
                    trace.report_version_id,
                    trace.snapshot_hash,
                    trace.planner_prompt_version,
                    trace.input_fingerprint,
                    int(trace.active_focus_exists),
                    trace.active_focus_type,
                    trace.resolved_referent_status,
                    trace.resolved_referent_type,
                    trace.planning_status,
                    self._dump(
                        trace.plan.model_dump(mode="json") if trace.plan else None
                    ),
                    trace.error_code,
                    trace.error_message,
                    trace.planner_error,
                    trace.model,
                    trace.request_id,
                    trace.planner_input_tokens,
                    trace.planner_output_tokens,
                    trace.planner_total_tokens,
                    trace.planner_llm_call_count,
                    trace.planner_latency_ms,
                    trace.planner_retry_count,
                    trace.created_at,
                ),
            )
        stored = self.get_planner_shadow_trace(
            trace.turn_id, planner_prompt_version=trace.planner_prompt_version
        )
        if stored is None:
            raise InvestigationTurnNotFoundError("planner shadow trace was not stored")
        return stored

    def get_planner_shadow_trace(
        self, turn_id: str, *, planner_prompt_version: str = ""
    ) -> PlannerShadowTrace | None:
        where = "turn_id = ?"
        values: list[Any] = [turn_id]
        if planner_prompt_version:
            where += " AND planner_prompt_version = ?"
            values.append(planner_prompt_version)
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT * FROM investigation_planner_shadow_traces
                WHERE {where} ORDER BY created_at DESC, trace_id LIMIT 1
                """,
                values,
            ).fetchone()
        return self._planner_shadow_trace(row) if row is not None else None

    def put_source_preparation_shadow_trace(
        self, trace: SourcePreparationShadowTrace
    ) -> SourcePreparationShadowTrace:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            scope = connection.execute(
                """
                SELECT t.session_id, s.report_version_id, s.source_snapshot_id,
                       s.snapshot_hash
                FROM investigation_turns AS t
                JOIN investigation_sessions AS s ON s.id = t.session_id
                WHERE t.id = ?
                """,
                (trace.turn_id,),
            ).fetchone()
            if scope is None:
                raise InvestigationTurnNotFoundError(trace.turn_id)
            if (
                trace.session_id != str(scope["session_id"])
                or trace.report_version_id != str(scope["report_version_id"])
                or trace.source_snapshot_id != str(scope["source_snapshot_id"])
                or trace.snapshot_hash != str(scope["snapshot_hash"])
            ):
                raise ValueError("source preparation trace scope does not match turn")
            planner = connection.execute(
                """
                SELECT trace_id FROM investigation_planner_shadow_traces
                WHERE trace_id = ? AND turn_id = ?
                """,
                (trace.planner_trace_id, trace.turn_id),
            ).fetchone()
            if planner is None:
                raise ArtifactNotFoundError(trace.planner_trace_id)
            existing = connection.execute(
                """
                SELECT * FROM investigation_source_preparation_shadow_traces
                WHERE turn_id = ? AND orchestrator_version = ?
                """,
                (trace.turn_id, trace.orchestrator_version),
            ).fetchone()
            if existing is not None:
                stored = self._source_preparation_shadow_trace(existing)
                if stored != trace:
                    raise ArtifactConflictError(trace.trace_id)
                return stored
            connection.execute(
                """
                INSERT INTO investigation_source_preparation_shadow_traces (
                    trace_id, schema_version, session_id, turn_id,
                    report_version_id, source_snapshot_id, snapshot_hash,
                    planner_trace_id, planner_prompt_version,
                    subject_binder_version, orchestrator_version,
                    input_fingerprint, requirement_kind, binding_status, status,
                    bound_requirement_json, preparation_result_json,
                    matched_query_result_artifact_ids_json,
                    selected_query_result_artifact_id, reason, error_code,
                    error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace.trace_id,
                    trace.schema_version,
                    trace.session_id,
                    trace.turn_id,
                    trace.report_version_id,
                    trace.source_snapshot_id,
                    trace.snapshot_hash,
                    trace.planner_trace_id,
                    trace.planner_prompt_version,
                    trace.subject_binder_version,
                    trace.orchestrator_version,
                    trace.input_fingerprint,
                    trace.requirement_kind,
                    trace.binding_status,
                    trace.status,
                    self._dump(
                        trace.bound_requirement.model_dump(mode="json")
                        if trace.bound_requirement
                        else None
                    ),
                    self._dump(
                        trace.preparation_result.model_dump(mode="json")
                        if trace.preparation_result
                        else None
                    ),
                    self._dump(trace.matched_query_result_artifact_ids),
                    trace.selected_query_result_artifact_id or "",
                    trace.reason,
                    trace.error_code,
                    trace.error_message,
                    trace.created_at,
                ),
            )
        stored = self.get_source_preparation_shadow_trace(
            trace.turn_id, orchestrator_version=trace.orchestrator_version
        )
        if stored is None:
            raise InvestigationTurnNotFoundError(
                "source preparation shadow trace was not stored"
            )
        return stored

    def get_source_preparation_shadow_trace(
        self, turn_id: str, *, orchestrator_version: str = ""
    ) -> SourcePreparationShadowTrace | None:
        where = "turn_id = ?"
        values: list[Any] = [turn_id]
        if orchestrator_version:
            where += " AND orchestrator_version = ?"
            values.append(orchestrator_version)
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT * FROM investigation_source_preparation_shadow_traces
                WHERE {where} ORDER BY created_at DESC, trace_id LIMIT 1
                """,
                values,
            ).fetchone()
        return (
            self._source_preparation_shadow_trace(row)
            if row is not None
            else None
        )

    def recent_ledger_refs(self, session_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        entries = self.list_ledger(session_id=session_id, limit=limit)
        return [
            {
                "ledger_id": item.ledger_id,
                "source_kind": item.source_kind.value,
                "metric_key": item.metric_key,
                "claim_id": item.claim_id,
                "finding_id": item.finding_id,
                "evidence_id": item.evidence_id,
                "excerpt": item.excerpt[:300],
                "freshness": item.freshness,
            }
            for item in entries
        ]

    def turn_result(self, turn_id: str, *, idempotent_replay: bool = False) -> TurnResult:
        turn = self.get_turn(turn_id)
        if turn.status not in {"completed", "error"}:
            raise InvestigationTurnNotFoundError("turn has no terminal result")
        message = self.get_message(turn.assistant_message_id)
        metadata = message.metadata
        grounding = metadata.get("grounding_validation") or {
            "status": "failed" if turn.status == "error" else "passed",
            "source_count": 0,
            "warnings": [turn.safe_message] if turn.safe_message else [],
        }
        tool_calls = tuple(
            ToolCall.model_validate(item) for item in metadata.get("tool_calls") or []
        )
        query_receipts = self.list_query_receipts(turn_id=turn.id)
        planner_shadow_trace = self.get_planner_shadow_trace(turn.id)
        source_preparation_shadow_trace = (
            self.get_source_preparation_shadow_trace(turn.id)
        )
        accessed_sources = self.list_ledger(turn_id=turn.id)
        return TurnResult(
            session_id=turn.session_id,
            turn_id=turn.id,
            message_id=message.id,
            answer=message.content,
            status="completed" if turn.status == "completed" else "error",
            tool_calls=tool_calls,
            tool_names=tuple(item.name for item in tool_calls),
            query_receipts=query_receipts,
            planner_shadow_trace=planner_shadow_trace,
            source_preparation_shadow_trace=source_preparation_shadow_trace,
            accessed_sources=accessed_sources,
            citations=(
                accessed_sources if turn.status == "completed" else ()
            ),
            resolved_references=tuple(
                ResolvedReference.model_validate(item)
                for item in metadata.get("resolved_references") or []
            ),
            grounding_validation=GroundingValidation.model_validate(grounding),
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            total_tokens=turn.total_tokens,
            llm_call_count=turn.llm_call_count,
            context_accounting=tuple(metadata.get("context_accounting") or []),
            grounding_issues=tuple(metadata.get("grounding_issues") or []),
            grounding_repair_count=int(metadata.get("grounding_repair_count") or 0),
            scope_repair_count=int(metadata.get("scope_repair_count") or 0),
            source_repair_count=int(metadata.get("source_repair_count") or 0),
            semantic_rewrite_count=int(metadata.get("semantic_rewrite_count") or 0),
            scope_initial_draft=str(metadata.get("scope_initial_draft") or ""),
            scope_repaired_draft=str(metadata.get("scope_repaired_draft") or ""),
            scope_initial_issues=tuple(metadata.get("scope_initial_issues") or []),
            scope_remaining_issues=tuple(metadata.get("scope_remaining_issues") or []),
            stop_reason=turn.stop_reason,
            idempotent_replay=idempotent_replay,
        )

    def get_turn_audit(self, turn_id: str) -> dict[str, Any]:
        turn = self.get_turn(turn_id)
        message = self.get_message(turn.assistant_message_id)
        return {
            "turn_id": turn.id,
            "status": turn.status,
            "stop_reason": turn.stop_reason,
            "error_code": turn.error_code,
            "tool_audit": list(message.metadata.get("tool_audit") or []),
            "query_receipts": [
                item.model_dump(mode="json")
                for item in self.list_query_receipts(turn_id=turn.id)
            ],
            "planner_shadow_trace": (
                trace.model_dump(mode="json")
                if (trace := self.get_planner_shadow_trace(turn.id))
                else None
            ),
            "source_preparation_shadow_trace": (
                trace.model_dump(mode="json")
                if (trace := self.get_source_preparation_shadow_trace(turn.id))
                else None
            ),
            "grounding_errors": list(message.metadata.get("grounding_errors") or []),
            "grounding_issues": list(message.metadata.get("grounding_issues") or []),
            "context_accounting": list(message.metadata.get("context_accounting") or []),
            "grounding_repair_count": int(
                message.metadata.get("grounding_repair_count") or 0
            ),
            "scope_repair_count": int(
                message.metadata.get("scope_repair_count") or 0
            ),
            "source_repair_count": int(message.metadata.get("source_repair_count") or 0),
            "semantic_rewrite_count": int(
                message.metadata.get("semantic_rewrite_count") or 0
            ),
            "scope_initial_draft": str(
                message.metadata.get("scope_initial_draft") or ""
            ),
            "scope_repaired_draft": str(
                message.metadata.get("scope_repaired_draft") or ""
            ),
            "scope_initial_issues": list(
                message.metadata.get("scope_initial_issues") or []
            ),
            "scope_remaining_issues": list(
                message.metadata.get("scope_remaining_issues") or []
            ),
            "source_semantics": str(
                message.metadata.get("source_semantics")
                or "retrieved_accessed_source_audit"
            ),
            "answer_support_sources": [],
            "input_tokens": turn.input_tokens,
            "output_tokens": turn.output_tokens,
            "total_tokens": turn.total_tokens,
            "llm_call_count": turn.llm_call_count,
            "source_refs": [
                item.model_dump(mode="json")
                for item in self.list_ledger(turn_id=turn.id)
            ],
        }

    @staticmethod
    def _tool_audit(
        calls: list[dict[str, Any]], results: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        output = []
        for index, call in enumerate(calls):
            result = results[index] if index < len(results) else {}
            sources = []
            for source in result.get("provenance") or []:
                sources.append(
                    {
                        "source_kind": source.get("source_kind", ""),
                        "metric_key": source.get("metric_key", ""),
                        "claim_id": source.get("claim_id", ""),
                        "finding_id": source.get("finding_id", ""),
                        "evidence_id": source.get("evidence_id", ""),
                        "source_hash": source.get("source_hash", ""),
                        "query_fingerprint": source.get("query_fingerprint", ""),
                        "freshness": source.get("freshness", ""),
                    }
                )
            output.append(
                {
                    "tool_call_id": str(call.get("id") or ""),
                    "tool_name": str(call.get("name") or ""),
                    "arguments_fingerprint": stable_hash(
                        {
                            "tool_name": call.get("name"),
                            "arguments": call.get("arguments") or {},
                        }
                    ),
                    "result_status": str(result.get("status") or "missing"),
                    "result_fingerprint": str(result.get("result_fingerprint") or ""),
                    "source_refs": sources,
                }
            )
        return output

    @staticmethod
    def _artifact_scope(
        artifact: QueryResultArtifact | SourceArtifact,
    ) -> tuple[str, str, str, str]:
        return (
            artifact.session_id,
            artifact.report_version_id,
            artifact.source_snapshot_id,
            artifact.snapshot_hash,
        )

    @classmethod
    def _insert_source_artifact(
        cls, connection: sqlite3.Connection, artifact: SourceArtifact
    ) -> None:
        existing = connection.execute(
            """
            SELECT * FROM investigation_source_artifacts
            WHERE artifact_id = ? OR source_fingerprint = ?
            """,
            (artifact.artifact_id, artifact.source_fingerprint),
        ).fetchone()
        if existing is not None:
            stored = cls._source_artifact(existing)
            if stored.model_dump(exclude={"created_at"}) != artifact.model_dump(
                exclude={"created_at"}
            ):
                raise ArtifactConflictError(artifact.artifact_id)
            return
        connection.execute(
            """
            INSERT INTO investigation_source_artifacts (
                artifact_id, schema_version, session_id, report_version_id,
                source_snapshot_id, snapshot_hash, stable_source_ref, source_type,
                content_level, canonical_content_json, canonical_content_hash,
                freshness, observed_at, observation_metadata_json,
                source_fingerprint, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                artifact.schema_version,
                artifact.session_id,
                artifact.report_version_id,
                artifact.source_snapshot_id,
                artifact.snapshot_hash,
                artifact.stable_source_ref,
                artifact.source_type,
                artifact.content_level,
                cls._dump(artifact.canonical_content),
                artifact.canonical_content_hash,
                artifact.freshness,
                artifact.observed_at,
                cls._dump(artifact.observation_metadata),
                artifact.source_fingerprint,
                artifact.created_at,
            ),
        )

    @classmethod
    def _insert_query_result_artifact(
        cls, connection: sqlite3.Connection, artifact: QueryResultArtifact
    ) -> None:
        existing = connection.execute(
            """
            SELECT * FROM investigation_query_result_artifacts
            WHERE artifact_id = ?
            """,
            (artifact.artifact_id,),
        ).fetchone()
        if existing is not None:
            members = connection.execute(
                """
                SELECT * FROM investigation_query_result_members
                WHERE query_result_artifact_id = ? ORDER BY ordinal
                """,
                (artifact.artifact_id,),
            ).fetchall()
            stored = cls._query_result_artifact(existing, members)
            if stored.model_dump(exclude={"created_at"}) != artifact.model_dump(
                exclude={"created_at"}
            ):
                raise ArtifactConflictError(artifact.artifact_id)
            return
        connection.execute(
            """
            INSERT INTO investigation_query_result_artifacts (
                artifact_id, schema_version, session_id, report_version_id,
                source_snapshot_id, snapshot_hash, operation, subject_ref,
                normalized_query_json, normalized_filters_json, returned_count,
                total, has_more, cursor, next_cursor, canonical_payload_json,
                canonical_payload_hash, query_fingerprint, result_fingerprint,
                observed_at, observation_metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                artifact.schema_version,
                artifact.session_id,
                artifact.report_version_id,
                artifact.source_snapshot_id,
                artifact.snapshot_hash,
                artifact.operation,
                artifact.subject_ref,
                cls._dump(artifact.normalized_query),
                cls._dump(artifact.normalized_filters),
                artifact.returned_count,
                artifact.total,
                None if artifact.has_more is None else int(artifact.has_more),
                artifact.cursor,
                artifact.next_cursor,
                cls._dump(artifact.canonical_payload),
                artifact.canonical_payload_hash,
                artifact.query_fingerprint,
                artifact.result_fingerprint,
                artifact.observed_at,
                cls._dump(artifact.observation_metadata),
                artifact.created_at,
            ),
        )
        for member in artifact.ordered_members:
            connection.execute(
                """
                INSERT INTO investigation_query_result_members (
                    query_result_artifact_id, ordinal, stable_source_ref,
                    source_artifact_id, content_level
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    member.ordinal,
                    member.stable_source_ref,
                    member.source_artifact_id,
                    member.content_level,
                ),
            )

    @classmethod
    def _insert_query_artifact_link(
        cls,
        connection: sqlite3.Connection,
        *,
        query_result: QueryResultArtifact,
        link: QueryResultArtifactLink,
    ) -> None:
        receipt = connection.execute(
            """
            SELECT r.*, s.source_snapshot_id
            FROM investigation_tool_query_receipts AS r
            JOIN investigation_sessions AS s ON s.id = r.session_id
            WHERE r.receipt_id = ?
            """,
            (link.receipt_id,),
        ).fetchone()
        if receipt is None:
            raise ArtifactNotFoundError(link.receipt_id)
        if str(receipt["status"]) != "ok":
            raise ArtifactIntegrityError("failed query receipt cannot materialize artifacts")
        receipt_scope = (
            str(receipt["session_id"]),
            str(receipt["report_version_id"]),
            str(receipt["source_snapshot_id"]),
            str(receipt["snapshot_hash"]),
        )
        if receipt_scope != cls._artifact_scope(query_result):
            raise ArtifactIntegrityError("receipt and query artifact scopes differ")
        if (
            str(receipt["operation"]) != query_result.operation
            or str(receipt["query_fingerprint"])
            != query_result.query_fingerprint
            or str(receipt["result_fingerprint"])
            != query_result.result_fingerprint
            or cls._json(receipt["query_params_json"], {})
            != query_result.normalized_query
            or str(receipt["executed_at"]) != query_result.observed_at
            or str(receipt["executed_at"]) != link.linked_at
        ):
            raise ArtifactIntegrityError("receipt and query artifact semantics differ")
        subject_refs = cls._json(receipt["subject_refs_json"], [])
        if query_result.subject_ref not in subject_refs:
            raise ArtifactIntegrityError("receipt does not contain artifact subject")
        returned_refs = tuple(cls._json(receipt["returned_refs_json"], []))
        artifact_refs = tuple(
            item.stable_source_ref for item in query_result.ordered_members
        )
        returned_refs_match = (
            returned_refs == artifact_refs
            if query_result.operation == "finding_evidence_list"
            else artifact_refs == (query_result.subject_ref,)
            and query_result.subject_ref in returned_refs
        )
        receipt_has_more = (
            None if receipt["has_more"] is None else bool(receipt["has_more"])
        )
        if (
            not returned_refs_match
            or receipt["result_count"] != query_result.returned_count
            or receipt["total"] != query_result.total
            or receipt_has_more != query_result.has_more
            or (str(receipt["cursor"] or "") or None) != query_result.cursor
            or (str(receipt["next_cursor"] or "") or None)
            != query_result.next_cursor
        ):
            raise ArtifactIntegrityError("receipt and query result facts differ")

        existing = connection.execute(
            """
            SELECT * FROM investigation_query_artifact_links WHERE receipt_id = ?
            """,
            (link.receipt_id,),
        ).fetchone()
        if existing is not None:
            stored = QueryResultArtifactLink.model_validate(dict(existing))
            if stored != link:
                raise ArtifactConflictError(link.receipt_id)
            return
        connection.execute(
            """
            INSERT INTO investigation_query_artifact_links (
                receipt_id, schema_version, query_result_artifact_id, linked_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                link.receipt_id,
                link.schema_version,
                link.query_result_artifact_id,
                link.linked_at,
            ),
        )

    @classmethod
    def _insert_query_receipts(
        cls,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        turn_id: str,
        receipts: list[dict[str, Any]],
    ) -> None:
        scope = connection.execute(
            "SELECT report_version_id, snapshot_hash FROM investigation_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if scope is None:
            raise InvestigationSessionNotFoundError(session_id)
        for raw in receipts:
            receipt = ToolQueryReceipt.model_validate(raw)
            if (
                receipt.session_id != session_id
                or receipt.turn_id != turn_id
                or receipt.report_version_id != str(scope["report_version_id"])
                or receipt.snapshot_hash != str(scope["snapshot_hash"])
            ):
                raise ValueError("query receipt scope does not match turn")
            connection.execute(
                """
                INSERT OR IGNORE INTO investigation_tool_query_receipts (
                    receipt_id, schema_version, session_id, turn_id, tool_call_id,
                    report_version_id, snapshot_hash, tool_name, operation,
                    subject_refs_json, query_params_json, arguments_fingerprint,
                    query_fingerprint, status, error_code, returned_refs_json,
                    model_visible_refs_json, result_count, total, has_more, cursor,
                    next_cursor, model_output_truncated, result_fingerprint,
                    details_json, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt.receipt_id,
                    receipt.schema_version,
                    receipt.session_id,
                    receipt.turn_id,
                    receipt.tool_call_id,
                    receipt.report_version_id,
                    receipt.snapshot_hash,
                    receipt.tool_name,
                    receipt.operation,
                    cls._dump(receipt.subject_refs),
                    cls._dump(receipt.query_params),
                    receipt.arguments_fingerprint,
                    receipt.query_fingerprint,
                    receipt.status,
                    receipt.error_code,
                    cls._dump(receipt.returned_refs),
                    cls._dump(receipt.model_visible_refs),
                    receipt.result_count,
                    receipt.total,
                    None if receipt.has_more is None else int(receipt.has_more),
                    receipt.cursor,
                    receipt.next_cursor,
                    int(receipt.model_output_truncated),
                    receipt.result_fingerprint,
                    cls._dump(receipt.details.model_dump(mode="json")),
                    receipt.executed_at,
                ),
            )

    @staticmethod
    def _stable_message_id(turn_id: str, kind: str) -> str:
        return f"investigation-message:{stable_hash({'turn_id': turn_id, 'kind': kind})[:32]}"

    @staticmethod
    def _turn(row: sqlite3.Row) -> InvestigationTurn:
        record = dict(row)
        record["retryable"] = bool(record["retryable"])
        raw_artifact = record.pop("public_artifact_json", "{}")
        try:
            parsed_artifact = json.loads(raw_artifact or "{}")
        except (TypeError, json.JSONDecodeError):
            parsed_artifact = {}
        record["public_artifact"] = (
            parsed_artifact if isinstance(parsed_artifact, dict) else {}
        )
        return InvestigationTurn.model_validate(record)

    @classmethod
    def _session(cls, row: sqlite3.Row) -> InvestigationSession:
        record = dict(row)
        record.pop("anchor_key", None)
        referents = cls._json(record.pop("ordered_referents_json"), [])
        focus = cls._json(record.pop("active_focus_json"), {})
        return InvestigationSession(
            **record,
            active_focus=focus if isinstance(focus, dict) else {},
            ordered_referents=tuple(Referent.model_validate(item) for item in referents),
        )

    @classmethod
    def _message(cls, row: sqlite3.Row) -> InvestigationMessage:
        record = dict(row)
        record["metadata"] = cls._json(record.pop("metadata_json"), {})
        return InvestigationMessage.model_validate(record)

    @classmethod
    def _ledger(cls, row: sqlite3.Row) -> SourceLedgerEntry:
        record = dict(row)
        record["warnings"] = tuple(cls._json(record.pop("warnings_json"), []))
        return SourceLedgerEntry.model_validate(record)

    @classmethod
    def _query_receipt(cls, row: sqlite3.Row) -> ToolQueryReceipt:
        record = dict(row)
        record["subject_refs"] = tuple(cls._json(record.pop("subject_refs_json"), []))
        record["query_params"] = cls._json(record.pop("query_params_json"), {})
        record["returned_refs"] = tuple(cls._json(record.pop("returned_refs_json"), []))
        record["model_visible_refs"] = tuple(
            cls._json(record.pop("model_visible_refs_json"), [])
        )
        record["details"] = cls._json(record.pop("details_json"), {})
        record["model_output_truncated"] = bool(record["model_output_truncated"])
        if record["has_more"] is not None:
            record["has_more"] = bool(record["has_more"])
        return ToolQueryReceipt.model_validate(record)

    @staticmethod
    def _public_turn_event(row: sqlite3.Row) -> dict[str, Any]:
        common = {
            "event_id": str(row["event_id"]),
            "turn_id": str(row["turn_id"]),
            "sequence": int(row["sequence"]),
            "event_type": str(row["event_type"]),
            "occurred_at": str(row["occurred_at"]),
        }
        if common["event_type"] != "turn":
            payload = InvestigationStore._json(row["payload_json"], {})
            return {
                **(payload if isinstance(payload, dict) else {}),
                **common,
            }
        return {
            **common,
            "stage": str(row["stage"]),
            "answer": str(row["answer"]),
            "safe_message": str(row["safe_message"]),
            "retryable": bool(row["retryable"]),
            "artifact": InvestigationStore._json(row["artifact_json"], {}) or None,
        }

    @classmethod
    def _source_artifact(cls, row: sqlite3.Row) -> SourceArtifact:
        record = dict(row)
        record["canonical_content"] = cls._json(
            record.pop("canonical_content_json"), {}
        )
        record["observation_metadata"] = cls._json(
            record.pop("observation_metadata_json"), {}
        )
        try:
            return SourceArtifact.model_validate(record)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("source artifact is corrupt") from exc

    @classmethod
    def _query_result_artifact(
        cls, row: sqlite3.Row, member_rows: list[sqlite3.Row]
    ) -> QueryResultArtifact:
        record = dict(row)
        record["normalized_query"] = cls._json(
            record.pop("normalized_query_json"), {}
        )
        record["normalized_filters"] = cls._json(
            record.pop("normalized_filters_json"), {}
        )
        record["canonical_payload"] = cls._json(
            record.pop("canonical_payload_json"), {}
        )
        record["observation_metadata"] = cls._json(
            record.pop("observation_metadata_json"), {}
        )
        record["has_more"] = (
            None if record["has_more"] is None else bool(record["has_more"])
        )
        record["ordered_members"] = tuple(
            QueryResultArtifactMember(
                ordinal=int(member["ordinal"]),
                stable_source_ref=str(member["stable_source_ref"]),
                source_artifact_id=str(member["source_artifact_id"]),
                content_level=str(member["content_level"]),
            )
            for member in member_rows
        )
        try:
            return QueryResultArtifact.model_validate(record)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("query result artifact is corrupt") from exc

    @classmethod
    def _planner_shadow_trace(cls, row: sqlite3.Row) -> PlannerShadowTrace:
        record = dict(row)
        record["active_focus_exists"] = bool(record["active_focus_exists"])
        record["plan"] = cls._json(record.pop("plan_json"), None)
        return PlannerShadowTrace.model_validate(record)

    @classmethod
    def _source_preparation_shadow_trace(
        cls, row: sqlite3.Row
    ) -> SourcePreparationShadowTrace:
        record = dict(row)
        record["bound_requirement"] = cls._json(
            record.pop("bound_requirement_json"), None
        )
        record["preparation_result"] = cls._json(
            record.pop("preparation_result_json"), None
        )
        record["matched_query_result_artifact_ids"] = tuple(
            cls._json(
                record.pop("matched_query_result_artifact_ids_json"), []
            )
        )
        record["selected_query_result_artifact_id"] = (
            str(record["selected_query_result_artifact_id"] or "") or None
        )
        try:
            return SourcePreparationShadowTrace.model_validate(record)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ArtifactIntegrityError(
                "source preparation shadow trace is corrupt"
            ) from exc

    @staticmethod
    def _json(raw: Any, fallback: Any) -> Any:
        try:
            return json.loads(str(raw))
        except (json.JSONDecodeError, TypeError):
            return fallback

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
