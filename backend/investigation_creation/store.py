from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from backend.audit_agent.config import settings
from backend.rulesets.contracts import RuleSetContent
from backend.rulesets.compiler import content_hash as ruleset_content_hash
from .approval import UseRuleSetProposalInput, reject, resolve_approval

from .contracts import (
    ConfirmationResolution,
    ConfirmationResolutionV3,
    ConfirmedConfigurationSnapshotV3,
    ConfirmedConfigurationSnapshotV4,
    DraftConfiguration,
    DraftStatus,
    InvestigationConfiguration,
    InvestigationDraft,
    InvestigationDraftConfiguration,
    TemporaryRuleSetJudgement,
    InvestigationRun,
    ReportGenerationBinding,
    RunStatus,
    TemporaryRuleSetProposal,
    parse_draft_configuration,
)
from .errors import (
    ConfirmationRequiredError,
    DraftAlreadyConfirmedError,
    DraftNotFoundError,
    DraftRevisionConflictError,
    IdempotencyConflictError,
    InvalidStateTransitionError,
    PrincipalAccessDeniedError,
    ProposalNotFoundError,
    ProposalVersionConflictError,
    RunNotFoundError,
)
from .principal import LOCAL_PRINCIPAL_ID


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InvestigationCreationStore:
    """Durable M3 facts, ownership boundaries, leases, and fencing state."""

    def __init__(
        self,
        db_path: Path | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.db_path = (
            db_path or (settings.data_dir / "investigation_creation.sqlite3")
        ).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
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
                CREATE TABLE IF NOT EXISTS investigation_drafts (
                    id TEXT PRIMARY KEY,
                    owner_principal TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('DRAFT', 'QUEUED')),
                    current_revision INTEGER NOT NULL CHECK(current_revision >= 1),
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    confirmed_revision INTEGER,
                    confirmed_by TEXT NOT NULL DEFAULT '',
                    confirmed_at TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ruleset_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 1),
                    content_hash TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ruleset_proposal_approvals (
                    approval_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    approving_user_turn_id TEXT NOT NULL,
                    approving_user_message_id TEXT NOT NULL,
                    presentation_assistant_message_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    proposal_version INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    draft_id TEXT NOT NULL REFERENCES investigation_drafts(id),
                    draft_revision INTEGER NOT NULL,
                    approved_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS investigation_draft_revisions (
                    draft_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(draft_id, revision),
                    FOREIGN KEY(draft_id) REFERENCES investigation_drafts(id)
                );

                CREATE TABLE IF NOT EXISTS investigation_runs (
                    id TEXT PRIMARY KEY,
                    owner_principal TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    draft_revision INTEGER NOT NULL CHECK(draft_revision >= 1),
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'QUEUED', 'RUNNING', 'AUDIT_COMPLETED', 'REPORT_GENERATING',
                        'PUBLISHED', 'FAILED', 'INTERRUPTED'
                    )),
                    confirmed_configuration_json TEXT NOT NULL,
                    confirmed_by TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    job_id TEXT NOT NULL DEFAULT '',
                    pipeline_started_at TEXT NOT NULL DEFAULT '',
                    pipeline_returned_at TEXT NOT NULL DEFAULT '',
                    report_version_id TEXT NOT NULL DEFAULT '',
                    report_session_id TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    claimed_by TEXT NOT NULL DEFAULT '',
                    claim_token TEXT NOT NULL DEFAULT '',
                    claimed_at TEXT NOT NULL DEFAULT '',
                    heartbeat_at TEXT NOT NULL DEFAULT '',
                    recovery_required INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(draft_id, draft_revision),
                    FOREIGN KEY(draft_id, draft_revision)
                        REFERENCES investigation_draft_revisions(draft_id, revision)
                );

                CREATE TABLE IF NOT EXISTS investigation_run_execution_holds (
                    run_id TEXT PRIMARY KEY REFERENCES investigation_runs(id),
                    source_name TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS investigation_run_idempotency_keys (
                    idempotency_key TEXT PRIMARY KEY,
                    principal TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    draft_revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES investigation_runs(id)
                );

                CREATE TABLE IF NOT EXISTS investigation_report_generation_bindings (
                    run_id TEXT PRIMARY KEY,
                    generation_key TEXT NOT NULL UNIQUE,
                    r31_run_id TEXT NOT NULL DEFAULT '',
                    report_version_id TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL CHECK(state IN ('RESERVED', 'STARTED', 'PUBLISHED')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES investigation_runs(id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_m3_r31_generation_binding
                ON investigation_report_generation_bindings(r31_run_id)
                WHERE r31_run_id <> '';

                CREATE INDEX IF NOT EXISTS idx_investigation_runs_status
                ON investigation_runs(status, created_at, id);

                CREATE TABLE IF NOT EXISTS investigation_creation_tool_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    principal TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_fingerprint TEXT NOT NULL,
                    is_mutation INTEGER NOT NULL CHECK(is_mutation IN (0, 1)),
                    status TEXT NOT NULL CHECK(status IN ('STARTED', 'SUCCEEDED', 'FAILED')),
                    response_json TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(session_id, turn_id, tool_call_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_creation_turn_mutation_tool
                ON investigation_creation_tool_receipts(session_id, turn_id, tool_name)
                WHERE is_mutation = 1 AND tool_name NOT IN ('create_lexicon_edit','open_resource_edit','update_resource_edit','save_resource');

                CREATE TABLE IF NOT EXISTS ruleset_proposal_conversation_bindings (
                    receipt_id TEXT PRIMARY KEY REFERENCES investigation_creation_tool_receipts(receipt_id),
                    application_turn_id TEXT NOT NULL
                );
                """
            )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            index = connection.execute("SELECT sql FROM sqlite_master WHERE name='uq_creation_turn_mutation_tool'").fetchone()
            if index and 'NOT IN' not in index[0]:
                connection.execute('DROP INDEX uq_creation_turn_mutation_tool')
                connection.execute("CREATE UNIQUE INDEX uq_creation_turn_mutation_tool ON investigation_creation_tool_receipts(session_id,turn_id,tool_name) WHERE is_mutation=1 AND tool_name NOT IN ('create_lexicon_edit','open_resource_edit','update_resource_edit','save_resource')")
            connection.execute("CREATE TABLE IF NOT EXISTS resource_edit_history (edit_id TEXT NOT NULL,version INTEGER NOT NULL,content_json TEXT NOT NULL,PRIMARY KEY(edit_id,version))")
            connection.execute("INSERT OR IGNORE INTO resource_edit_history SELECT proposal_id,version,content_json FROM ruleset_proposals")
        # Serialize additive upgrades so concurrent process initialization cannot
        # both observe and alter the same missing column.
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_column(connection, "ruleset_proposal_approvals", "presentation_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "ruleset_proposal_approvals", "tool_call_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "ruleset_proposal_approvals", "runtime_turn_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(
                connection,
                "investigation_drafts",
                "owner_principal",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "investigation_runs",
                "owner_principal",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "investigation_runs",
                "pipeline_started_at",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "investigation_runs",
                "pipeline_returned_at",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "investigation_runs",
                "recovery_required",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                connection,
                "investigation_run_idempotency_keys",
                "principal",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "investigation_run_idempotency_keys",
                "request_fingerprint",
                "TEXT NOT NULL DEFAULT ''",
            )
            connection.execute(
                """
                UPDATE investigation_drafts
                SET owner_principal = ?
                WHERE owner_principal = ''
                """,
                (LOCAL_PRINCIPAL_ID,),
            )
            connection.execute(
                """
                UPDATE investigation_runs
                SET owner_principal = ?
                WHERE owner_principal = ''
                """,
                (LOCAL_PRINCIPAL_ID,),
            )
            connection.execute(
                """
                UPDATE investigation_run_idempotency_keys
                SET principal = COALESCE((
                    SELECT owner_principal FROM investigation_runs
                    WHERE investigation_runs.id = investigation_run_idempotency_keys.run_id
                ), '')
                WHERE principal = ''
                """
            )
            legacy_keys = connection.execute(
                """
                SELECT idempotency_key, principal, draft_id, draft_revision
                FROM investigation_run_idempotency_keys
                WHERE request_fingerprint = ''
                """
            ).fetchall()
            for key in legacy_keys:
                fingerprint = self.confirmation_fingerprint(
                    principal=str(key["principal"]),
                    draft_id=str(key["draft_id"]),
                    expected_revision=int(key["draft_revision"]),
                    confirmed=True,
                )
                connection.execute(
                    """
                    UPDATE investigation_run_idempotency_keys
                    SET request_fingerprint = ? WHERE idempotency_key = ?
                    """,
                    (fingerprint, str(key["idempotency_key"])),
                )
        with self._connect() as connection:
            self._ensure_audit_completed_status(connection)

    @staticmethod
    def _ensure_audit_completed_status(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'investigation_runs'"
        ).fetchone()
        if row is None or "AUDIT_COMPLETED" in str(row["sql"] or ""):
            return
        columns = (
            "id, owner_principal, draft_id, draft_revision, idempotency_key, status, "
            "confirmed_configuration_json, confirmed_by, confirmed_at, job_id, "
            "pipeline_started_at, pipeline_returned_at, report_version_id, "
            "report_session_id, error_code, error_message, claimed_by, claim_token, "
            "claimed_at, heartbeat_at, recovery_required, created_at, updated_at, "
            "started_at, completed_at"
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.executescript(
                f"""
                BEGIN IMMEDIATE;
                CREATE TABLE investigation_runs_audit_completed (
                    id TEXT PRIMARY KEY,
                    owner_principal TEXT NOT NULL,
                    draft_id TEXT NOT NULL,
                    draft_revision INTEGER NOT NULL CHECK(draft_revision >= 1),
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'QUEUED', 'RUNNING', 'AUDIT_COMPLETED', 'REPORT_GENERATING',
                        'PUBLISHED', 'FAILED', 'INTERRUPTED'
                    )),
                    confirmed_configuration_json TEXT NOT NULL,
                    confirmed_by TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    job_id TEXT NOT NULL DEFAULT '',
                    pipeline_started_at TEXT NOT NULL DEFAULT '',
                    pipeline_returned_at TEXT NOT NULL DEFAULT '',
                    report_version_id TEXT NOT NULL DEFAULT '',
                    report_session_id TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    claimed_by TEXT NOT NULL DEFAULT '',
                    claim_token TEXT NOT NULL DEFAULT '',
                    claimed_at TEXT NOT NULL DEFAULT '',
                    heartbeat_at TEXT NOT NULL DEFAULT '',
                    recovery_required INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(draft_id, draft_revision),
                    FOREIGN KEY(draft_id, draft_revision)
                        REFERENCES investigation_draft_revisions(draft_id, revision)
                );
                INSERT INTO investigation_runs_audit_completed ({columns})
                SELECT {columns} FROM investigation_runs;
                DROP TABLE investigation_runs;
                ALTER TABLE investigation_runs_audit_completed RENAME TO investigation_runs;
                CREATE INDEX idx_investigation_runs_status
                ON investigation_runs(status, created_at, id);
                COMMIT;
                """
            )
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def tool_arguments_fingerprint(arguments: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                arguments,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def begin_tool_execution(
        self,
        *,
        session_id: str,
        turn_id: str,
        tool_call_id: str,
        principal: str,
        tool_name: str,
        arguments: dict[str, Any],
        is_mutation: bool,
        application_turn_id: str = "",
    ) -> dict[str, Any]:
        identity = {
            "session_id": str(session_id or "").strip(),
            "turn_id": str(turn_id or "").strip(),
            "tool_call_id": str(tool_call_id or "").strip(),
            "principal": str(principal or "").strip(),
            "tool_name": str(tool_name or "").strip(),
        }
        if not all(identity.values()):
            raise IdempotencyConflictError(
                "durable tool execution identity is required",
                code="TOOL_EXECUTION_IDENTITY_REQUIRED",
            )
        fingerprint = self.tool_arguments_fingerprint(arguments)
        now = self.clock().isoformat()
        receipt_id = f"creation-tool-receipt:{uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM investigation_creation_tool_receipts
                WHERE session_id = ? AND turn_id = ? AND tool_call_id = ?
                """,
                (identity["session_id"], identity["turn_id"], identity["tool_call_id"]),
            ).fetchone()
            if row is None and is_mutation and tool_name not in {"create_lexicon_edit", "open_resource_edit", "update_resource_edit", "save_resource"}:
                row = connection.execute(
                    """
                    SELECT * FROM investigation_creation_tool_receipts
                    WHERE session_id = ? AND turn_id = ? AND tool_name = ?
                      AND is_mutation = 1
                    """,
                    (identity["session_id"], identity["turn_id"], identity["tool_name"]),
                ).fetchone()
            if row is not None:
                if (
                    str(row["principal"]) != identity["principal"]
                    or str(row["tool_name"]) != identity["tool_name"]
                    or str(row["arguments_fingerprint"]) != fingerprint
                ):
                    raise IdempotencyConflictError(
                        "tool execution identity was reused with different input"
                    )
                response_json = str(row["response_json"] or "")
                if application_turn_id:
                    self._bind_proposal_conversation(connection, str(row["receipt_id"]), application_turn_id)
                return {
                    "receipt_id": str(row["receipt_id"]),
                    "status": str(row["status"]),
                    "response": json.loads(response_json) if response_json else None,
                    "replay": True,
                }
            connection.execute(
                """
                INSERT INTO investigation_creation_tool_receipts (
                    receipt_id, session_id, turn_id, tool_call_id, principal,
                    tool_name, arguments_fingerprint, is_mutation, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'STARTED', ?)
                """,
                (
                    receipt_id,
                    identity["session_id"],
                    identity["turn_id"],
                    identity["tool_call_id"],
                    identity["principal"],
                    identity["tool_name"],
                    fingerprint,
                    int(is_mutation),
                    now,
                ),
            )
            if application_turn_id:
                self._bind_proposal_conversation(connection, receipt_id, application_turn_id)
        return {
            "receipt_id": receipt_id,
            "status": "STARTED",
            "response": None,
            "replay": False,
        }

    def complete_tool_execution(
        self,
        receipt_id: str,
        *,
        response: dict[str, Any],
        succeeded: bool,
    ) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE investigation_creation_tool_receipts
                SET status = ?, response_json = ?, completed_at = ?
                WHERE receipt_id = ? AND status = 'STARTED'
                """,
                (
                    "SUCCEEDED" if succeeded else "FAILED",
                    json.dumps(response, ensure_ascii=False, sort_keys=True),
                    self.clock().isoformat(),
                    receipt_id,
                ),
            ).rowcount
        if not updated:
            raise IdempotencyConflictError(
                "tool execution receipt is no longer writable"
            )

    @staticmethod
    def _bind_proposal_conversation(connection: sqlite3.Connection, receipt_id: str, turn_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO ruleset_proposal_conversation_bindings VALUES (?, ?)",
            (receipt_id, turn_id),
        )
        bound = connection.execute(
            "SELECT application_turn_id FROM ruleset_proposal_conversation_bindings WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
        if bound["application_turn_id"] != turn_id:
            raise IdempotencyConflictError("Proposal receipt belongs to a different conversation turn")

    def successful_adoption_results(self, *, session_id: str, turn_id: str,
                                    principal: str) -> list[dict[str, Any]]:
        """Read adoption outcomes from Application receipts, never runtime text."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT r.* FROM investigation_creation_tool_receipts r
                   JOIN ruleset_proposal_conversation_bindings b ON b.receipt_id=r.receipt_id
                   WHERE r.session_id=? AND b.application_turn_id=? AND r.principal=?
                     AND r.tool_name='use_ruleset_proposal' AND r.is_mutation=1
                     AND r.status='SUCCEEDED' ORDER BY r.rowid""",
                (session_id, turn_id, principal),
            ).fetchall()
            results = []
            for row in rows:
                if not row["turn_id"] or not row["tool_call_id"] or not row["arguments_fingerprint"]:
                    raise RuntimeError("Adoption receipt execution identity is incomplete")
                envelope = json.loads(row["response_json"])
                if not isinstance(envelope, dict) or envelope.get("status") != "ok":
                    raise RuntimeError("Successful adoption receipt has an invalid response")
                data = envelope["data"]
                recorded = data["draft"]
                owned = self._owned_draft_row(connection, recorded["id"], principal)
                revision = connection.execute(
                    "SELECT * FROM investigation_draft_revisions WHERE draft_id=? AND revision=?",
                    (owned["id"], recorded["current_revision"]),
                ).fetchone()
                if (revision is None or revision["title"] != recorded["title"]
                        or revision["objective"] != recorded["objective"]
                        or json.loads(revision["configuration_json"]) != recorded["configuration"]):
                    raise RuntimeError("Adoption receipt does not match the durable Draft revision")
                judgement = recorded["configuration"]["judgement"]
                # An existing binding may be returned by a no-op. Its audit need
                # not belong to this call or to the current title/Recall revision.
                binding = connection.execute(
                    """SELECT 1 FROM ruleset_proposal_approvals WHERE session_id=? AND draft_id=?
                       AND proposal_id=? AND proposal_version=? AND content_hash=?
                       AND draft_revision<=? LIMIT 1""",
                    (session_id, owned["id"], judgement["proposal_id"], judgement["proposal_version"],
                     judgement["content_hash"], recorded["current_revision"]),
                ).fetchone()
                if judgement.get("strategy") != "temporary_ruleset" or binding is None:
                    raise RuntimeError("Adoption receipt has no matching durable binding")
                results.append(data)
        return results

    def proposal_presentation_snapshots(self, *, session_id: str, turn_id: str) -> list[dict[str, Any]]:
        # Only durable successful Application receipts may select snapshots for display.
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT r.response_json FROM investigation_creation_tool_receipts r
                   JOIN ruleset_proposal_conversation_bindings b ON b.receipt_id = r.receipt_id
                   WHERE r.session_id = ? AND b.application_turn_id = ? AND r.status = 'SUCCEEDED'
                     AND r.tool_name IN ('create_ruleset_proposal', 'update_ruleset_proposal', 'open_resource_edit', 'update_resource_edit', 'get_resource_edit')
                   ORDER BY r.rowid""", (session_id, turn_id),
            ).fetchall()
            latest = {}
            for row in rows:
                data = json.loads(row["response_json"])["data"]
                if 'edit_id' in data:
                    if data.get('kind') != 'ruleset':
                        continue
                    data = data['proposal_snapshot']
                recorded = TemporaryRuleSetProposal.model_validate(data)
                latest[recorded.proposal_id] = recorded
            snapshots = []
            for recorded in latest.values():
                current = self._owned_proposal(connection, recorded.proposal_id, session_id)
                # Do not silently replace a tool's version with a later unseen version.
                if current.version != recorded.version or current.content_hash != recorded.content_hash:
                    raise ProposalVersionConflictError("Proposal changed before presentation")
                snapshot = current.model_dump(mode="json")
                if snapshot not in snapshots:
                    snapshots.append(snapshot)
            return snapshots

    def create_ruleset_proposal(
        self, *, session_id: str, content: RuleSetContent, content_hash: str,
        connection: sqlite3.Connection | None = None,
    ) -> TemporaryRuleSetProposal:
        proposal_id = f"ruleset-proposal:{uuid4().hex}"
        now = self._now_text()
        with (nullcontext(connection) if connection is not None else self._connect()) as connection:
            connection.execute(
                "INSERT INTO ruleset_proposals VALUES (?, ?, 1, ?, ?, ?, ?)",
                (proposal_id, session_id, content_hash,
                 self._json(content.model_dump(mode="json")), now, now),
            )
            row = connection.execute('SELECT version,content_json FROM ruleset_proposals WHERE proposal_id=? AND session_id=?', (proposal_id,session_id)).fetchone()
            if row:
                connection.execute('INSERT OR IGNORE INTO resource_edit_history VALUES (?,?,?)', (proposal_id,row['version'],row['content_json']))
            return self._owned_proposal(connection, proposal_id, session_id)

    def get_ruleset_proposal(self, proposal_id: str, *, session_id: str) -> TemporaryRuleSetProposal:
        with self._connect() as connection:
            return self._owned_proposal(connection, proposal_id, session_id)

    def update_ruleset_proposal(
        self, proposal_id: str, *, session_id: str, expected_version: int,
        content: RuleSetContent, content_hash: str,
    ) -> TemporaryRuleSetProposal:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._owned_proposal(connection, proposal_id, session_id)
            self.check_proposal_version(current, expected_version)
            if current.content_hash == content_hash:
                return current
            connection.execute(
                """UPDATE ruleset_proposals
                   SET version = version + 1, content_hash = ?, content_json = ?, updated_at = ?
                   WHERE proposal_id = ? AND session_id = ? AND version = ?""",
                (content_hash, self._json(content.model_dump(mode="json")), self._now_text(),
                 proposal_id, session_id, expected_version),
            )
            row = connection.execute('SELECT version,content_json FROM ruleset_proposals WHERE proposal_id=? AND session_id=?', (proposal_id,session_id)).fetchone()
            if row:
                connection.execute('INSERT OR IGNORE INTO resource_edit_history VALUES (?,?,?)', (proposal_id,row['version'],row['content_json']))
            return self._owned_proposal(connection, proposal_id, session_id)

    @staticmethod
    def check_proposal_version(current: TemporaryRuleSetProposal, expected_version: int) -> None:
        if current.version != expected_version:
            raise ProposalVersionConflictError(
                "Proposal version is stale; read the current Proposal before editing.",
                details={"proposal_id": current.proposal_id,
                         "expected_version": expected_version, "current_version": current.version},
            )

    @staticmethod
    def _owned_proposal(
        connection: sqlite3.Connection, proposal_id: str, session_id: str
    ) -> TemporaryRuleSetProposal:
        row = connection.execute(
            "SELECT * FROM ruleset_proposals WHERE proposal_id = ? AND session_id = ?",
            (proposal_id, session_id),
        ).fetchone()
        if row is None:
            raise ProposalNotFoundError("Proposal was not found in the current Session")
        payload = dict(row)
        payload["content"] = json.loads(payload.pop("content_json"))
        return TemporaryRuleSetProposal.model_validate(payload)

    @staticmethod
    def protect_temporary_judgement(configuration: DraftConfiguration,
                                    previous: DraftConfiguration | None = None) -> None:
        judgement = getattr(configuration, "judgement", None)
        if isinstance(judgement, TemporaryRuleSetJudgement):
            if judgement != getattr(previous, "judgement", None):
                reject("PROPOSAL_APPROVAL_REQUIRED", "Temporary judgement can only be bound by use_ruleset_proposal.")

    def use_ruleset_proposal(self, command: UseRuleSetProposalInput, *, session_id: str,
                            turn_id: str, tool_call_id: str, runtime_turn_id: str, principal: str, conversation_db: Path,
                            normalize: Callable) -> InvestigationDraft:
        command = UseRuleSetProposalInput.model_validate(command.model_dump(mode="json"))
        with self._connect() as connection:
            # Lock both existing SQLite stores before reading the approval basis.
            connection.execute("ATTACH DATABASE ? AS conversation", (str(conversation_db),))
            connection.execute("BEGIN IMMEDIATE")
            approval = resolve_approval(connection, session_id=session_id, turn_id=turn_id,
                                        principal=principal, presentation_id=command.presentation_id)
            presented = approval["presentation"]
            try:
                proposal = self._owned_proposal(connection, presented["proposal_id"], session_id)
            except ProposalNotFoundError:
                reject("PROPOSAL_NOT_PRESENTED")
            except (ValueError, TypeError, KeyError):
                reject("INVALID_PROPOSAL_PRESENTATION")
            if (proposal.version != presented["proposal_version"]
                    or proposal.content_hash != presented["content_hash"]
                    or ruleset_content_hash(proposal.content) != proposal.content_hash
                    or proposal.content.model_dump(mode="json") != presented["snapshot"]["content"]):
                reject("PROPOSAL_PRESENTATION_STALE",
                       "Proposal changed. Present the latest complete Proposal and wait for new explicit approval.")
            judgement = TemporaryRuleSetJudgement(
                strategy="temporary_ruleset", proposal_id=proposal.proposal_id,
                proposal_version=proposal.version, content_hash=proposal.content_hash,
                content=proposal.content,
            )
            now = self._now_text()
            if command.draft_id:
                try:
                    row = self._owned_draft_row(connection, command.draft_id, principal)
                except (DraftNotFoundError, PrincipalAccessDeniedError):
                    reject("DRAFT_NOT_AUTHORIZED")
                draft = self._draft(row)
                if draft.status != DraftStatus.DRAFT:
                    reject("DRAFT_NOT_EDITABLE")
                if draft.current_revision != command.expected_revision:
                    reject("DRAFT_REVISION_STALE")
                if getattr(draft.configuration, "judgement", None) == judgement:
                    return draft
                if not isinstance(draft.configuration, InvestigationDraftConfiguration):
                    reject("CONFIGURATION_INVALID", "Proposal approval requires a v4 Draft configuration.")
                configuration = draft.configuration.model_copy(update={"judgement": judgement})
                draft_id, revision = draft.id, draft.current_revision + 1
                title, objective = draft.title, draft.objective
            else:
                previous_approval = connection.execute(
                    "SELECT draft_id FROM ruleset_proposal_approvals WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
                existing_id = approval["current_draft_id"] or (previous_approval["draft_id"] if previous_approval else "")
                if existing_id:
                    existing = self._owned_draft_row(connection, existing_id, principal)
                    reject("DRAFT_TARGET_REQUIRED", "Use the existing Draft with its expected revision.",
                           draft_id=existing_id, expected_revision=existing["current_revision"])
                creation = command.create_draft
                configuration = InvestigationDraftConfiguration(
                    **creation.configuration.model_dump(mode="json"), judgement=judgement,
                )
                draft_id, revision = f"investigation-draft:{uuid4().hex}", 1
                title, objective = creation.title, creation.objective
            configuration = normalize(configuration, previous=draft.configuration if command.draft_id else None)
            if (configuration.investigation.mode == "search"
                    and not (getattr(configuration.investigation.recall_plan, "terms", None)
                             or getattr(configuration.investigation.recall_plan, "enabled_main_terms", None))):
                reject("CONFIGURATION_INVALID", "Complete Recall configuration before adopting the Proposal.")
            payload = self._json(configuration.model_dump(mode="json"))
            if command.draft_id:
                connection.execute(
                    """UPDATE investigation_drafts SET configuration_json=?, current_revision=?,
                       updated_by=?, updated_at=? WHERE id=?""",
                    (payload, revision, principal, now, draft_id),
                )
            else:
                connection.execute(
                    """INSERT INTO investigation_drafts
                       (id, owner_principal, status, current_revision, title, objective,
                        configuration_json, created_by, updated_by, created_at, updated_at)
                       VALUES (?, ?, 'DRAFT', 1, ?, ?, ?, ?, ?, ?, ?)""",
                    (draft_id, principal, title, objective, payload, principal, principal, now, now),
                )
            connection.execute(
                """INSERT INTO investigation_draft_revisions
                   (draft_id, revision, title, objective, configuration_json, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (draft_id, revision, title, objective, payload, principal, now),
            )
            connection.execute(
                """INSERT INTO ruleset_proposal_approvals
                   (approval_id, session_id, approving_user_turn_id, approving_user_message_id,
                    presentation_assistant_message_id, proposal_id, proposal_version, content_hash,
                    draft_id, draft_revision, approved_at, presentation_id, tool_call_id, runtime_turn_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f"proposal-approval:{uuid4().hex}", session_id, approval["approving_user_turn_id"],
                 approval["approving_user_message_id"], presented["assistant_message_id"],
                 proposal.proposal_id, proposal.version, proposal.content_hash, draft_id, revision, now,
                 presented["presentation_id"], tool_call_id, runtime_turn_id),
            )
            result = self._draft(self._owned_draft_row(connection, draft_id, principal))
        return result

    def create_draft(
        self,
        *,
        principal: str,
        title: str,
        objective: str,
        configuration: DraftConfiguration,
    ) -> InvestigationDraft:
        self.protect_temporary_judgement(configuration)
        draft_id = f"investigation-draft:{uuid4().hex}"
        now = self._now_text()
        configuration_json = self._json(configuration.model_dump(mode="json"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO investigation_drafts (
                    id, owner_principal, status, current_revision, title, objective,
                    configuration_json, created_by, updated_by, created_at, updated_at
                ) VALUES (?, ?, 'DRAFT', 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    principal,
                    title,
                    objective,
                    configuration_json,
                    principal,
                    principal,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO investigation_draft_revisions (
                    draft_id, revision, title, objective, configuration_json,
                    created_by, created_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?)
                """,
                (draft_id, title, objective, configuration_json, principal, now),
            )
        return self.get_draft(draft_id, principal=principal)

    def update_draft(
        self,
        draft_id: str,
        *,
        principal: str,
        expected_revision: int,
        title: str | None = None,
        objective: str | None = None,
        configuration: DraftConfiguration | None = None,
    ) -> InvestigationDraft:
        now = self._now_text()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._owned_draft_row(connection, draft_id, principal)
            if configuration is not None:
                self.protect_temporary_judgement(configuration, self._draft(row).configuration)
            if str(row["status"]) != DraftStatus.DRAFT.value:
                raise DraftAlreadyConfirmedError(draft_id)
            current_revision = int(row["current_revision"])
            if current_revision != expected_revision:
                raise DraftRevisionConflictError(
                    f"expected revision {expected_revision}, current revision is {current_revision}"
                )
            next_revision = current_revision + 1
            next_title = title if title is not None else str(row["title"])
            next_objective = objective if objective is not None else str(row["objective"])
            next_configuration = (
                configuration.model_dump(mode="json")
                if configuration is not None
                else parse_draft_configuration(
                    self._load_json(str(row["configuration_json"]))
                ).model_dump(mode="json")
            )
            configuration_json = self._json(next_configuration)
            updated = connection.execute(
                """
                UPDATE investigation_drafts
                SET current_revision = ?, title = ?, objective = ?,
                    configuration_json = ?, updated_by = ?, updated_at = ?
                WHERE id = ? AND owner_principal = ?
                  AND status = 'DRAFT' AND current_revision = ?
                """,
                (
                    next_revision,
                    next_title,
                    next_objective,
                    configuration_json,
                    principal,
                    now,
                    draft_id,
                    principal,
                    expected_revision,
                ),
            ).rowcount
            if updated != 1:
                raise DraftRevisionConflictError(draft_id)
            connection.execute(
                """
                INSERT INTO investigation_draft_revisions (
                    draft_id, revision, title, objective, configuration_json,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    next_revision,
                    next_title,
                    next_objective,
                    configuration_json,
                    principal,
                    now,
                ),
            )
        return self.get_draft(draft_id, principal=principal)

    def get_draft(self, draft_id: str, *, principal: str) -> InvestigationDraft:
        with self._connect() as connection:
            row = self._owned_draft_row(connection, draft_id, principal)
        return self._draft(row)

    def confirm_and_queue(
        self,
        draft_id: str,
        *,
        principal: str,
        expected_revision: int,
        confirmed: bool,
        idempotency_key: str,
        request_fingerprint: str,
        resolved_configuration: dict[str, Any],
        confirmation_resolution: dict[str, Any] | None = None,
    ) -> InvestigationRun:
        if not confirmed:
            raise ConfirmationRequiredError("explicit confirmation is required")
        now = self._now_text()
        run_id = f"investigation-run:{uuid4().hex}"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_replay(
                connection,
                idempotency_key=idempotency_key,
                principal=principal,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return self._run(replay)

            draft = self._owned_draft_row(connection, draft_id, principal)
            current_revision = int(draft["current_revision"])
            if current_revision != expected_revision:
                raise DraftRevisionConflictError(
                    f"expected revision {expected_revision}, current revision is {current_revision}"
                )
            existing = connection.execute(
                """
                SELECT * FROM investigation_runs
                WHERE owner_principal = ? AND draft_id = ? AND draft_revision = ?
                """,
                (principal, draft_id, expected_revision),
            ).fetchone()
            if existing is not None:
                self._record_idempotency_key(
                    connection,
                    idempotency_key=idempotency_key,
                    principal=principal,
                    request_fingerprint=request_fingerprint,
                    run_id=str(existing["id"]),
                    draft_id=draft_id,
                    draft_revision=expected_revision,
                    created_at=now,
                )
                return self._run(existing)

            if confirmation_resolution is None:
                draft_configuration = InvestigationConfiguration.model_validate_json(
                    str(draft["configuration_json"])
                )
                # Keep the legacy v2 snapshot free of M3-only account fields.
                legacy_execution = dict(resolved_configuration)
                for key in (
                    "crawler_account_id",
                    "crawler_account_display_name",
                    "crawler_account_confirmed_state",
                ):
                    legacy_execution.pop(key, None)
                snapshot = {
                    "schema_version": "investigation-run-config-v2",
                    "draft_id": draft_id,
                    "draft_revision": expected_revision,
                    "title": str(draft["title"]),
                    "objective": str(draft["objective"]),
                    "draft_configuration": draft_configuration.model_dump(mode="json"),
                    "execution": legacy_execution,
                }
            else:
                if "audit_policy" in confirmation_resolution:
                    resolution_v3 = ConfirmationResolutionV3.model_validate(
                        confirmation_resolution
                    )
                    snapshot = ConfirmedConfigurationSnapshotV3.model_validate(
                        {
                            "schema_version": "investigation-run-config-v3",
                            "draft_id": draft_id,
                            "draft_revision": expected_revision,
                            "title": str(draft["title"]),
                            "objective": str(draft["objective"]),
                            **resolution_v3.model_dump(mode="json"),
                            "max_notes": 1,
                            "confirmed_by": principal,
                            "confirmed_at": now,
                        }
                    ).model_dump(mode="json")
                else:
                    resolution = ConfirmationResolution.model_validate(
                        confirmation_resolution
                    )
                    snapshot = ConfirmedConfigurationSnapshotV4.model_validate(
                        {
                            "schema_version": "investigation-run-config-v4",
                            "draft_id": draft_id,
                            "draft_revision": expected_revision,
                            "title": str(draft["title"]),
                            "objective": str(draft["objective"]),
                            **resolution.model_dump(mode="json"),
                            "max_notes": resolution.execution.max_notes,
                            "confirmed_by": principal,
                            "confirmed_at": now,
                        }
                    ).model_dump(mode="json")
            # Prove the resolution was derived from the transaction's exact Draft revision.
            persisted = self._draft(draft)
            if isinstance(persisted.configuration, InvestigationDraftConfiguration):
                from .frozen import same_payload, validate_temporary_execution
                revision_row = connection.execute(
                    "SELECT configuration_json FROM investigation_draft_revisions WHERE draft_id=? AND revision=?",
                    (draft_id, expected_revision),
                ).fetchone()
                if revision_row is None or not same_payload(
                    json.loads(revision_row["configuration_json"]), persisted.configuration.model_dump(mode="json")
                ):
                    reject("CONFIGURATION_INVALID", "Persisted Draft revision content mismatch.")
                judgement = persisted.configuration.judgement
                if isinstance(judgement, TemporaryRuleSetJudgement):
                    configuration = persisted.configuration
                    mode = configuration.investigation.mode
                    terms = []
                    creator_url = ""
                    if mode == "search":
                        plan = configuration.investigation.recall_plan
                        terms = list(plan.enabled_main_terms if plan.strategy == "existing_lexicon" else plan.terms)
                    else:
                        creator_url = configuration.investigation.creator_url
                    expected_fields = {"mode": mode, "platform": configuration.platform.value,
                                       "resolved_search_terms": terms, "creator_url": creator_url}
                    if any(not same_payload(snapshot.get(k), v) for k, v in expected_fields.items()):
                        reject("CONFIGURATION_INVALID", "Frozen configuration differs from expected Draft revision.")
                    execution = snapshot["execution"]
                    if (execution["platform"] != configuration.platform.value or execution["crawl_mode"] != mode
                            or execution["keyword"] != ",".join(terms) or execution["creator_url"] != creator_url):
                        reject("CONFIGURATION_INVALID", "Execution parameters differ from expected Draft revision.")
                    if not same_payload(snapshot.get("temporary_ruleset"), judgement.model_dump(mode="json")):
                        reject("CONFIGURATION_INVALID", "Frozen source differs from expected Draft revision.")
                    try:
                        validate_temporary_execution(judgement, snapshot["execution"])
                    except (ValueError, KeyError, TypeError) as exc:
                        reject("CONFIGURATION_INVALID", str(exc))
                elif snapshot.get("temporary_ruleset") is not None:
                    reject("CONFIGURATION_INVALID", "Temporary source does not match formal Draft.")
            connection.execute(
                """
                INSERT INTO investigation_runs (
                    id, owner_principal, draft_id, draft_revision, idempotency_key,
                    status, confirmed_configuration_json, confirmed_by, confirmed_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    principal,
                    draft_id,
                    expected_revision,
                    idempotency_key,
                    self._json(snapshot),
                    principal,
                    now,
                    now,
                    now,
                ),
            )
            self._record_idempotency_key(
                connection,
                idempotency_key=idempotency_key,
                principal=principal,
                request_fingerprint=request_fingerprint,
                run_id=run_id,
                draft_id=draft_id,
                draft_revision=expected_revision,
                created_at=now,
            )
            updated = connection.execute(
                """
                UPDATE investigation_drafts
                SET status = 'QUEUED', confirmed_revision = ?, confirmed_by = ?,
                    confirmed_at = ?, updated_by = ?, updated_at = ?
                WHERE id = ? AND owner_principal = ? AND current_revision = ?
                """,
                (
                    expected_revision,
                    principal,
                    now,
                    principal,
                    now,
                    draft_id,
                    principal,
                    expected_revision,
                ),
            ).rowcount
            if updated != 1:
                raise DraftRevisionConflictError(draft_id)
        return self.get_run(run_id, principal=principal)

    def replay_confirmation_identity(
        self,
        draft_id: str,
        *,
        principal: str,
        expected_revision: int,
        confirmed: bool,
        idempotency_key: str,
    ) -> InvestigationRun | None:
        """Replay a completed confirmation before consulting mutable resources."""

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            key = connection.execute(
                """
                SELECT * FROM investigation_run_idempotency_keys
                WHERE idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            if key is not None:
                if (
                    not confirmed
                    or str(key["principal"]) != principal
                    or str(key["draft_id"]) != draft_id
                    or int(key["draft_revision"]) != expected_revision
                ):
                    raise IdempotencyConflictError(
                        "idempotency key is already bound to a different request"
                    )
                run = connection.execute(
                    """
                    SELECT * FROM investigation_runs
                    WHERE id = ? AND owner_principal = ?
                    """,
                    (str(key["run_id"]), principal),
                ).fetchone()
                if run is None:
                    raise IdempotencyConflictError(
                        "idempotency key does not resolve to an owned Run"
                    )
                return self._run(run)

            self._owned_draft_row(connection, draft_id, principal)
            existing = connection.execute(
                """
                SELECT * FROM investigation_runs
                WHERE owner_principal = ? AND draft_id = ? AND draft_revision = ?
                """,
                (principal, draft_id, expected_revision),
            ).fetchone()
            if existing is None or not confirmed:
                return None
            snapshot = self._load_json(
                str(existing["confirmed_configuration_json"])
            )
            config_hash = self._snapshot_config_hash(snapshot)
            fingerprint = self.confirmation_fingerprint(
                principal=principal,
                draft_id=draft_id,
                expected_revision=expected_revision,
                confirmed=True,
                confirmed_configuration_hash=config_hash,
            )
            self._record_idempotency_key(
                connection,
                idempotency_key=idempotency_key,
                principal=principal,
                request_fingerprint=fingerprint,
                run_id=str(existing["id"]),
                draft_id=draft_id,
                draft_revision=expected_revision,
                created_at=self._now_text(),
            )
            return self._run(existing)

    def replay_confirmation(
        self,
        draft_id: str,
        *,
        principal: str,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> InvestigationRun | None:
        now = self._now_text()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._idempotent_replay(
                connection,
                idempotency_key=idempotency_key,
                principal=principal,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return self._run(replay)
            self._owned_draft_row(connection, draft_id, principal)
            existing = connection.execute(
                """
                SELECT * FROM investigation_runs
                WHERE owner_principal = ? AND draft_id = ? AND draft_revision = ?
                """,
                (principal, draft_id, expected_revision),
            ).fetchone()
            if existing is None:
                return None
            self._record_idempotency_key(
                connection,
                idempotency_key=idempotency_key,
                principal=principal,
                request_fingerprint=request_fingerprint,
                run_id=str(existing["id"]),
                draft_id=draft_id,
                draft_revision=expected_revision,
                created_at=now,
            )
            return self._run(existing)

    @staticmethod
    def confirmation_fingerprint(
        *,
        principal: str,
        draft_id: str,
        expected_revision: int,
        confirmed: bool,
        confirmed_configuration_hash: str = "",
    ) -> str:
        payload = json.dumps(
            {
                "action": "confirm_and_queue",
                "principal": principal,
                "draft_id": draft_id,
                "draft_revision": expected_revision,
                "confirmed": confirmed,
                "confirmed_configuration_hash": confirmed_configuration_hash,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def _snapshot_config_hash(cls, snapshot: dict[str, Any]) -> str:
        stored = str(snapshot.get("config_hash") or "").strip()
        if stored:
            return stored
        return hashlib.sha256(cls._json(snapshot).encode("utf-8")).hexdigest()

    def _idempotent_replay(
        self,
        connection: sqlite3.Connection,
        *,
        idempotency_key: str,
        principal: str,
        request_fingerprint: str,
    ) -> sqlite3.Row | None:
        key_row = connection.execute(
            """
            SELECT request_fingerprint, run_id
            FROM investigation_run_idempotency_keys
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if key_row is None:
            return None
        if str(key_row["request_fingerprint"]) != request_fingerprint:
            raise IdempotencyConflictError(
                "idempotency key is already bound to a different request"
            )
        return connection.execute(
            """
            SELECT * FROM investigation_runs
            WHERE id = ? AND owner_principal = ?
            """,
            (str(key_row["run_id"]), principal),
        ).fetchone()

    @staticmethod
    def _record_idempotency_key(
        connection: sqlite3.Connection,
        *,
        idempotency_key: str,
        principal: str,
        request_fingerprint: str,
        run_id: str,
        draft_id: str,
        draft_revision: int,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO investigation_run_idempotency_keys (
                idempotency_key, principal, request_fingerprint, run_id,
                draft_id, draft_revision, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                idempotency_key,
                principal,
                request_fingerprint,
                run_id,
                draft_id,
                draft_revision,
                created_at,
            ),
        )

    def get_run(self, run_id: str, *, principal: str) -> InvestigationRun:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM investigation_runs
                WHERE id = ? AND owner_principal = ?
                """,
                (run_id, principal),
            ).fetchone()
            if row is None:
                exists = connection.execute(
                    "SELECT 1 FROM investigation_runs WHERE id = ?", (run_id,)
                ).fetchone()
                if exists is not None:
                    raise PrincipalAccessDeniedError(run_id)
                raise RunNotFoundError(run_id)
        return self._run(row)

    def find_run_for_draft(
        self, draft_id: str, *, principal: str
    ) -> InvestigationRun | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM investigation_runs
                WHERE draft_id = ? AND owner_principal = ?
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (draft_id, principal),
            ).fetchone()
        return self._run(row) if row is not None else None

    def owner_principals_for_report(
        self, *, report_version_id: str, task_id: str
    ) -> frozenset[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT owner_principal
                FROM investigation_runs
                WHERE (report_version_id <> '' AND report_version_id = ?)
                   OR (job_id <> '' AND job_id = ?)
                   OR id = ?
                """,
                (report_version_id, task_id, task_id),
            ).fetchall()
        return frozenset(str(row["owner_principal"]) for row in rows)

    def get_run_for_job(self, job_id: str) -> InvestigationRun | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM investigation_runs WHERE job_id=?", (job_id,)).fetchone()
        return self._run(row) if row is not None else None

    def get_run_for_worker(self, run_id: str) -> InvestigationRun:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM investigation_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return self._run(row)

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_timeout_seconds: int = 300,
    ) -> InvestigationRun | None:
        now_value = self.clock()
        now = self._datetime_text(now_value)
        stale_before = self._datetime_text(
            now_value - timedelta(seconds=max(1, int(lease_timeout_seconds)))
        )
        claim_token = uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidate = connection.execute(
                """
                SELECT * FROM investigation_runs
                WHERE id NOT IN (SELECT run_id FROM investigation_run_execution_holds)
                  AND (status = 'QUEUED'
                   OR (
                       status IN ('RUNNING', 'REPORT_GENERATING')
                       AND (heartbeat_at = '' OR heartbeat_at <= ?)
                   ))
                ORDER BY
                    CASE status
                        WHEN 'REPORT_GENERATING' THEN 0
                        WHEN 'RUNNING' THEN 1
                        ELSE 2
                    END,
                    created_at,
                    id
                LIMIT 1
                """,
                (stale_before,),
            ).fetchone()
            if candidate is None:
                return None
            prior_status = str(candidate["status"])
            next_status = (
                RunStatus.RUNNING.value
                if prior_status == RunStatus.QUEUED.value
                else prior_status
            )
            recovery_required = 0 if prior_status == RunStatus.QUEUED.value else 1
            updated = connection.execute(
                """
                UPDATE investigation_runs
                SET status = ?, claimed_by = ?, claim_token = ?, claimed_at = ?,
                    heartbeat_at = ?, recovery_required = ?,
                    started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,
                    error_code = '', error_message = '', updated_at = ?
                WHERE id = ? AND status = ?
                  AND (
                      status = 'QUEUED'
                      OR heartbeat_at = ''
                      OR heartbeat_at <= ?
                  )
                """,
                (
                    next_status,
                    worker_id,
                    claim_token,
                    now,
                    now,
                    recovery_required,
                    now,
                    now,
                    str(candidate["id"]),
                    prior_status,
                    stale_before,
                ),
            ).rowcount
            if updated != 1:
                return None
            claimed = connection.execute(
                "SELECT * FROM investigation_runs WHERE id = ?",
                (str(candidate["id"]),),
            ).fetchone()
        return self._run(claimed)

    def heartbeat(self, run_id: str, claim_token: str) -> bool:
        now = self._now_text()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE investigation_runs SET heartbeat_at = ?, updated_at = ?
                WHERE id = ? AND claim_token = ?
                  AND status IN ('RUNNING', 'REPORT_GENERATING')
                """,
                (now, now, run_id, claim_token),
            ).rowcount
        return updated == 1

    def owns_claim(self, run_id: str, claim_token: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM investigation_runs
                WHERE id = ? AND claim_token = ?
                  AND status IN ('RUNNING', 'REPORT_GENERATING')
                """,
                (run_id, claim_token),
            ).fetchone()
        return row is not None

    def bind_job(self, run_id: str, claim_token: str, job_id: str) -> InvestigationRun:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._owned_run(connection, run_id, claim_token, RunStatus.RUNNING)
            current = str(row["job_id"])
            if current and current != job_id:
                raise InvalidStateTransitionError("run is already bound to another Job")
            now = self._now_text()
            connection.execute(
                """
                UPDATE investigation_runs SET job_id = ?, updated_at = ?
                WHERE id = ? AND claim_token = ? AND status = 'RUNNING'
                """,
                (job_id, now, run_id, claim_token),
            )
        return self.get_run_for_worker(run_id)

    def mark_pipeline_started(
        self, run_id: str, claim_token: str
    ) -> InvestigationRun:
        now = self._now_text()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE investigation_runs
                SET pipeline_started_at = ?, updated_at = ?
                WHERE id = ? AND claim_token = ? AND status = 'RUNNING'
                  AND job_id <> '' AND pipeline_started_at = ''
                """,
                (now, now, run_id, claim_token),
            ).rowcount
        if updated != 1:
            raise InvalidStateTransitionError("Pipeline start marker rejected")
        return self.get_run_for_worker(run_id)

    def mark_pipeline_returned(
        self, run_id: str, claim_token: str
    ) -> InvestigationRun:
        now = self._now_text()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE investigation_runs
                SET pipeline_returned_at = ?, updated_at = ?
                WHERE id = ? AND claim_token = ? AND status = 'RUNNING'
                  AND pipeline_started_at <> ''
                """,
                (now, now, run_id, claim_token),
            ).rowcount
        if updated != 1:
            raise InvalidStateTransitionError("Pipeline return marker rejected")
        return self.get_run_for_worker(run_id)

    def mark_report_generating(
        self, run_id: str, claim_token: str
    ) -> InvestigationRun:
        now = self._now_text()
        generation_key = self.generation_key_for_run(run_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """
                UPDATE investigation_runs
                SET status = 'REPORT_GENERATING', heartbeat_at = ?, updated_at = ?
                WHERE id = ? AND claim_token = ? AND status = 'RUNNING' AND job_id <> ''
                """,
                (now, now, run_id, claim_token),
            ).rowcount
            if updated != 1:
                raise InvalidStateTransitionError(
                    "RUNNING -> REPORT_GENERATING rejected"
                )
            connection.execute(
                """
                INSERT INTO investigation_report_generation_bindings (
                    run_id, generation_key, state, created_at, updated_at
                ) VALUES (?, ?, 'RESERVED', ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (run_id, generation_key, now, now),
            )
            binding = connection.execute(
                """
                SELECT * FROM investigation_report_generation_bindings
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if binding is None or str(binding["generation_key"]) != generation_key:
                raise InvalidStateTransitionError("report generation binding conflict")
        return self.get_run_for_worker(run_id)

    def mark_audit_completed(
        self, run_id: str, claim_token: str
    ) -> InvestigationRun:
        return self._mark_terminal(
            run_id,
            claim_token,
            RunStatus.AUDIT_COMPLETED,
            error_code="",
            error_message="",
        )

    @staticmethod
    def generation_key_for_run(run_id: str) -> str:
        digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        return f"m3-report-generation:{digest}"

    def get_report_binding(self, run_id: str) -> ReportGenerationBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM investigation_report_generation_bindings
                WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        return ReportGenerationBinding.model_validate(dict(row)) if row else None

    def bind_report_generation_started(
        self,
        run_id: str,
        *,
        generation_key: str,
        r31_run_id: str,
        report_version_id: str,
    ) -> ReportGenerationBinding:
        """Bind the R3.1 identity even if the initiating lease just expired."""

        now = self._now_text()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM investigation_report_generation_bindings
                WHERE run_id = ? AND generation_key = ?
                """,
                (run_id, generation_key),
            ).fetchone()
            if row is None:
                raise InvalidStateTransitionError("report generation binding is missing")
            current_run_id = str(row["r31_run_id"])
            current_version_id = str(row["report_version_id"])
            if current_run_id and (
                current_run_id != r31_run_id
                or current_version_id != report_version_id
            ):
                raise InvalidStateTransitionError(
                    "InvestigationRun is already bound to another R3.1 generation"
                )
            if not current_run_id:
                connection.execute(
                    """
                    UPDATE investigation_report_generation_bindings
                    SET r31_run_id = ?, report_version_id = ?, state = 'STARTED',
                        updated_at = ?
                    WHERE run_id = ? AND generation_key = ? AND r31_run_id = ''
                    """,
                    (r31_run_id, report_version_id, now, run_id, generation_key),
                )
        binding = self.get_report_binding(run_id)
        if binding is None:
            raise InvalidStateTransitionError("report generation binding disappeared")
        return binding

    def mark_report_binding_published(
        self,
        run_id: str,
        claim_token: str,
        *,
        report_version_id: str,
    ) -> ReportGenerationBinding:
        now = self._now_text()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_run(
                connection, run_id, claim_token, RunStatus.REPORT_GENERATING
            )
            updated = connection.execute(
                """
                UPDATE investigation_report_generation_bindings
                SET state = 'PUBLISHED', updated_at = ?
                WHERE run_id = ? AND report_version_id = ?
                  AND state IN ('STARTED', 'PUBLISHED')
                """,
                (now, run_id, report_version_id),
            ).rowcount
            if updated != 1:
                raise InvalidStateTransitionError(
                    "published ReportVersion does not match generation binding"
                )
        binding = self.get_report_binding(run_id)
        if binding is None:
            raise InvalidStateTransitionError("report generation binding disappeared")
        return binding

    def mark_published(
        self,
        run_id: str,
        claim_token: str,
        *,
        report_version_id: str,
        report_session_id: str,
    ) -> InvestigationRun:
        now = self._now_text()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            binding = connection.execute(
                """
                SELECT * FROM investigation_report_generation_bindings
                WHERE run_id = ? AND report_version_id = ? AND state = 'PUBLISHED'
                """,
                (run_id, report_version_id),
            ).fetchone()
            if binding is None:
                raise InvalidStateTransitionError(
                    "Run cannot publish without its fenced report binding"
                )
            updated = connection.execute(
                """
                UPDATE investigation_runs
                SET status = 'PUBLISHED', report_version_id = ?, report_session_id = ?,
                    claimed_by = '', claim_token = '', claimed_at = '', heartbeat_at = '',
                    recovery_required = 0, error_code = '', error_message = '',
                    completed_at = ?, updated_at = ?
                WHERE id = ? AND claim_token = ? AND status = 'REPORT_GENERATING'
                """,
                (
                    report_version_id,
                    report_session_id,
                    now,
                    now,
                    run_id,
                    claim_token,
                ),
            ).rowcount
        if updated != 1:
            raise InvalidStateTransitionError("REPORT_GENERATING -> PUBLISHED rejected")
        return self.get_run_for_worker(run_id)

    def defer_report_completion(
        self,
        run_id: str,
        claim_token: str,
        *,
        error_message: str,
    ) -> InvestigationRun:
        """Release a published report finalization for a fenced, safe retry."""

        now = self._now_text()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE investigation_runs
                SET claimed_by = '', claim_token = '', claimed_at = '', heartbeat_at = '',
                    recovery_required = 1,
                    error_code = 'report_completion_retryable', error_message = ?,
                    completed_at = '', updated_at = ?
                WHERE id = ? AND claim_token = ? AND status = 'REPORT_GENERATING'
                """,
                (error_message[:4_000], now, run_id, claim_token),
            ).rowcount
        if updated != 1:
            raise InvalidStateTransitionError("report completion retry release rejected")
        return self.get_run_for_worker(run_id)

    def mark_failed(
        self, run_id: str, claim_token: str, *, error_code: str, error_message: str
    ) -> InvestigationRun:
        return self._mark_terminal(
            run_id,
            claim_token,
            RunStatus.FAILED,
            error_code=error_code,
            error_message=error_message,
        )

    def mark_interrupted(
        self, run_id: str, claim_token: str, *, error_code: str, error_message: str
    ) -> InvestigationRun:
        return self._mark_terminal(
            run_id,
            claim_token,
            RunStatus.INTERRUPTED,
            error_code=error_code,
            error_message=error_message,
        )

    def _mark_terminal(
        self,
        run_id: str,
        claim_token: str,
        status: RunStatus,
        *,
        error_code: str,
        error_message: str,
    ) -> InvestigationRun:
        now = self._now_text()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE investigation_runs
                SET status = ?, error_code = ?, error_message = ?,
                    claimed_by = '', claim_token = '', claimed_at = '', heartbeat_at = '',
                    recovery_required = 0, completed_at = ?, updated_at = ?
                WHERE id = ? AND claim_token = ?
                  AND status IN ('RUNNING', 'REPORT_GENERATING')
                """,
                (
                    status.value,
                    error_code,
                    error_message[:4_000],
                    now,
                    now,
                    run_id,
                    claim_token,
                ),
            ).rowcount
        if updated != 1:
            raise InvalidStateTransitionError(f"transition to {status.value} rejected")
        return self.get_run_for_worker(run_id)

    def _owned_draft_row(
        self, connection: sqlite3.Connection, draft_id: str, principal: str
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM investigation_drafts
            WHERE id = ? AND owner_principal = ?
            """,
            (draft_id, principal),
        ).fetchone()
        if row is not None:
            return row
        exists = connection.execute(
            "SELECT 1 FROM investigation_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
        if exists is not None:
            raise PrincipalAccessDeniedError(draft_id)
        raise DraftNotFoundError(draft_id)

    @staticmethod
    def _owned_run(
        connection: sqlite3.Connection,
        run_id: str,
        claim_token: str,
        status: RunStatus,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM investigation_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        if str(row["claim_token"]) != claim_token or str(row["status"]) != status.value:
            raise InvalidStateTransitionError("worker does not own the Run transition")
        return row

    def _now_text(self) -> str:
        return self._datetime_text(self.clock())

    @staticmethod
    def _datetime_text(value: datetime) -> str:
        normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _load_json(value: str) -> dict[str, Any]:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}

    @classmethod
    def _draft(cls, row: sqlite3.Row) -> InvestigationDraft:
        record = dict(row)
        record["configuration"] = parse_draft_configuration(
            cls._load_json(record.pop("configuration_json"))
        )
        return InvestigationDraft.model_validate(record)

    @classmethod
    def _run(cls, row: sqlite3.Row) -> InvestigationRun:
        record = dict(row)
        record["confirmed_configuration"] = cls._load_json(
            record.pop("confirmed_configuration_json")
        )
        record["recovery_required"] = bool(record.get("recovery_required"))
        return InvestigationRun.model_validate(record)
