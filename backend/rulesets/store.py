from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from backend.audit_agent.config import settings

from .compiler import canonical_json
from .compiler import content_hash as compute_content_hash
from .contracts import RuleSetContent
from .errors import (
    RuleSetForbiddenError,
    RuleSetIdempotencyConflictError,
    RuleSetNotFoundError,
    RuleSetRevisionConflictError,
    RuleSetRevisionNotFoundError,
)
from .gambling_v1 import (
    GAMBLING_RULESET_ID,
    GAMBLING_RULESET_REVISION_ID,
    GAMBLING_RULESET_V1_REVISION_ID,
    gambling_ruleset_v1,
    gambling_ruleset_v2,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RuleSetStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (settings.data_dir / "audit_index.sqlite3")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS rule_sets (
                    id TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL DEFAULT 0,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    draft_revision INTEGER NOT NULL,
                    draft_version INTEGER NOT NULL,
                    draft_content_hash TEXT NOT NULL,
                    draft_content_json TEXT NOT NULL,
                    published_revision_id TEXT,
                    published_version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rule_set_revisions (
                    id TEXT PRIMARY KEY,
                    ruleset_id TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 0,
                    draft_revision INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status = 'published'),
                    content_hash TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    published_by TEXT NOT NULL,
                    UNIQUE(ruleset_id, draft_revision),
                    UNIQUE(ruleset_id, version),
                    FOREIGN KEY(ruleset_id) REFERENCES rule_sets(id)
                );

                CREATE TABLE IF NOT EXISTS rule_set_publish_idempotency (
                    principal_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(principal_id, idempotency_key),
                    FOREIGN KEY(revision_id) REFERENCES rule_set_revisions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_rule_set_revisions_ruleset
                ON rule_set_revisions(ruleset_id, version DESC);

                CREATE TRIGGER IF NOT EXISTS immutable_rule_set_revisions_update
                BEFORE UPDATE ON rule_set_revisions
                BEGIN
                    SELECT RAISE(ABORT, 'published RuleSetRevision is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS immutable_rule_set_revisions_delete
                BEFORE DELETE ON rule_set_revisions
                BEGIN
                    SELECT RAISE(ABORT, 'published RuleSetRevision is immutable');
                END;
                """
            )
            self._seed_gambling_v1(connection)
            self._seed_gambling_v2(connection)

    def _seed_gambling_v1(self, connection: sqlite3.Connection) -> None:
        if connection.execute("SELECT 1 FROM rule_sets WHERE id = ?", (GAMBLING_RULESET_ID,)).fetchone():
            return
        now = utc_now()
        content = gambling_ruleset_v1()
        snapshot = content.model_dump(mode="json")
        digest = compute_content_hash(content)
        connection.execute(
            """
            INSERT INTO rule_sets (
                id, schema_version, owner_id, status, draft_revision, draft_version,
                draft_content_hash, draft_content_json, published_revision_id,
                published_version, created_at, updated_at, created_by
            ) VALUES (?, 0, 'system', 'published', 1, 1, ?, ?, ?, 1, ?, ?, 'system')
            """,
            (
                GAMBLING_RULESET_ID,
                digest,
                canonical_json(snapshot),
                GAMBLING_RULESET_V1_REVISION_ID,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO rule_set_revisions (
                id, ruleset_id, schema_version, draft_revision, version, status,
                content_hash, snapshot_json, published_at, published_by
            ) VALUES (?, ?, 0, 1, 1, 'published', ?, ?, ?, 'system')
            """,
            (
                GAMBLING_RULESET_V1_REVISION_ID,
                GAMBLING_RULESET_ID,
                digest,
                canonical_json(snapshot),
                now,
            ),
        )

    def _seed_gambling_v2(self, connection: sqlite3.Connection) -> None:
        if connection.execute(
            "SELECT 1 FROM rule_set_revisions WHERE id = ?",
            (GAMBLING_RULESET_REVISION_ID,),
        ).fetchone():
            return
        aggregate = connection.execute(
            "SELECT * FROM rule_sets WHERE id = ?",
            (GAMBLING_RULESET_ID,),
        ).fetchone()
        if aggregate is None or aggregate["owner_id"] != "system":
            return

        now = utc_now()
        content = gambling_ruleset_v2()
        snapshot = content.model_dump(mode="json")
        digest = compute_content_hash(content)
        next_revision = max(2, int(aggregate["draft_revision"] or 0) + 1)
        connection.execute(
            """
            INSERT INTO rule_set_revisions (
                id, ruleset_id, schema_version, draft_revision, version, status,
                content_hash, snapshot_json, published_at, published_by
            ) VALUES (?, ?, 0, ?, 2, 'published', ?, ?, ?, 'system')
            """,
            (
                GAMBLING_RULESET_REVISION_ID,
                GAMBLING_RULESET_ID,
                next_revision,
                digest,
                canonical_json(snapshot),
                now,
            ),
        )
        connection.execute(
            """
            UPDATE rule_sets
            SET status = 'published', draft_revision = ?, draft_version = 2,
                draft_content_hash = ?, draft_content_json = ?,
                published_revision_id = ?, published_version = 2, updated_at = ?
            WHERE id = ? AND owner_id = 'system'
            """,
            (
                next_revision,
                digest,
                canonical_json(snapshot),
                GAMBLING_RULESET_REVISION_ID,
                now,
                GAMBLING_RULESET_ID,
            ),
        )

    def create_draft(
        self,
        *,
        content: RuleSetContent,
        actor_id: str,
        ruleset_id: str = "",
    ) -> dict:
        identifier = str(ruleset_id or f"ruleset.{uuid4().hex[:12]}").strip()
        snapshot = content.model_dump(mode="json")
        digest = compute_content_hash(content)
        now = utc_now()
        try:
            with self._lock, self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO rule_sets (
                        id, schema_version, owner_id, status, draft_revision, draft_version,
                        draft_content_hash, draft_content_json, published_revision_id,
                        published_version, created_at, updated_at, created_by
                    ) VALUES (?, 0, ?, 'draft', 1, 1, ?, ?, NULL, 0, ?, ?, ?)
                    """,
                    (identifier, actor_id, digest, canonical_json(snapshot), now, now, actor_id),
                )
                row = connection.execute(
                    "SELECT * FROM rule_sets WHERE id = ?", (identifier,)
                ).fetchone()
                return self._row_to_ruleset(row)
        except sqlite3.IntegrityError as exc:
            raise RuleSetRevisionConflictError(
                f"RuleSet already exists: {identifier}"
            ) from exc

    def get(self, ruleset_id: str, *, actor_id: str) -> dict:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM rule_sets WHERE id = ? AND (owner_id = ? OR owner_id = 'system')",
                (ruleset_id, actor_id),
            ).fetchone()
            if row is None:
                raise RuleSetNotFoundError(ruleset_id)
            return self._row_to_ruleset(row)

    def list(self, *, actor_id: str) -> list[dict]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM rule_sets
                WHERE owner_id = ? OR owner_id = 'system'
                ORDER BY updated_at DESC, id
                """,
                (actor_id,),
            ).fetchall()
            return [self._row_to_ruleset(row) for row in rows]

    def update_draft(
        self,
        ruleset_id: str,
        *,
        expected_revision: int,
        content: RuleSetContent,
        actor_id: str,
    ) -> dict:
        snapshot = content.model_dump(mode="json")
        digest = compute_content_hash(content)
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM rule_sets WHERE id = ?", (ruleset_id,)).fetchone()
            if row is None:
                raise RuleSetNotFoundError(ruleset_id)
            if row["owner_id"] == "system" or row["owner_id"] != actor_id:
                raise RuleSetForbiddenError("RuleSet draft is not editable by this principal")
            if int(row["draft_revision"]) != int(expected_revision):
                raise RuleSetRevisionConflictError(
                    f"expected draft revision {expected_revision}, current revision is {row['draft_revision']}"
                )
            next_revision = int(row["draft_revision"]) + 1
            next_version = int(row["published_version"] or 0) + 1
            updated = connection.execute(
                """
                UPDATE rule_sets
                SET status = 'draft', draft_revision = ?, draft_version = ?,
                    draft_content_hash = ?, draft_content_json = ?, updated_at = ?
                WHERE id = ? AND owner_id = ? AND draft_revision = ?
                """,
                (
                    next_revision,
                    next_version,
                    digest,
                    canonical_json(snapshot),
                    now,
                    ruleset_id,
                    actor_id,
                    expected_revision,
                ),
            )
            if updated.rowcount != 1:
                raise RuleSetRevisionConflictError("RuleSet draft changed concurrently")
            current = connection.execute("SELECT * FROM rule_sets WHERE id = ?", (ruleset_id,)).fetchone()
            return self._row_to_ruleset(current)

    def publish(
        self,
        ruleset_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        actor_id: str,
    ) -> dict:
        request_hash = hashlib.sha256(
            canonical_json({"ruleset_id": ruleset_id, "draft_revision": expected_revision}).encode("utf-8")
        ).hexdigest()
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM rule_sets WHERE id = ?", (ruleset_id,)).fetchone()
            if row is None:
                raise RuleSetNotFoundError(ruleset_id)
            if row["owner_id"] == "system" or row["owner_id"] != actor_id:
                raise RuleSetForbiddenError("RuleSet draft is not publishable by this principal")
            idempotent = connection.execute(
                """
                SELECT request_hash, revision_id FROM rule_set_publish_idempotency
                WHERE principal_id = ? AND idempotency_key = ?
                """,
                (actor_id, idempotency_key),
            ).fetchone()
            if idempotent is not None:
                if idempotent["request_hash"] != request_hash:
                    raise RuleSetIdempotencyConflictError(
                        "Idempotency-Key was already used for a different RuleSet publish request"
                    )
                return self._get_published_with_connection(connection, idempotent["revision_id"])

            if int(row["draft_revision"]) != int(expected_revision):
                raise RuleSetRevisionConflictError(
                    f"expected draft revision {expected_revision}, current revision is {row['draft_revision']}"
                )
            existing = connection.execute(
                """
                SELECT * FROM rule_set_revisions
                WHERE ruleset_id = ? AND draft_revision = ?
                """,
                (ruleset_id, expected_revision),
            ).fetchone()
            if existing is not None:
                connection.execute(
                    """
                    INSERT INTO rule_set_publish_idempotency (
                        principal_id, idempotency_key, request_hash, revision_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (actor_id, idempotency_key, request_hash, existing["id"], now),
                )
                return self._row_to_revision(existing)

            version = int(row["draft_version"])
            revision_id = f"ruleset-revision:{ruleset_id}:v{version}"
            connection.execute(
                """
                INSERT INTO rule_set_revisions (
                    id, ruleset_id, schema_version, draft_revision, version, status,
                    content_hash, snapshot_json, published_at, published_by
                ) VALUES (?, ?, 0, ?, ?, 'published', ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    ruleset_id,
                    expected_revision,
                    version,
                    row["draft_content_hash"],
                    row["draft_content_json"],
                    now,
                    actor_id,
                ),
            )
            connection.execute(
                """
                UPDATE rule_sets
                SET status = 'published', published_revision_id = ?, published_version = ?, updated_at = ?
                WHERE id = ?
                """,
                (revision_id, version, now, ruleset_id),
            )
            connection.execute(
                """
                INSERT INTO rule_set_publish_idempotency (
                    principal_id, idempotency_key, request_hash, revision_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (actor_id, idempotency_key, request_hash, revision_id, now),
            )
            return self._get_published_with_connection(connection, revision_id)

    def list_published(self, *, ruleset_id: str = "") -> list[dict]:
        with self._lock, self._connect() as connection:
            if ruleset_id:
                rows = connection.execute(
                    """
                    SELECT * FROM rule_set_revisions
                    WHERE ruleset_id = ? ORDER BY version DESC
                    """,
                    (ruleset_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM rule_set_revisions ORDER BY published_at DESC, id"
                ).fetchall()
            return [self._row_to_revision(row) for row in rows]

    def get_published(self, revision_id: str) -> dict:
        with self._lock, self._connect() as connection:
            return self._get_published_with_connection(connection, revision_id)

    def _get_published_with_connection(
        self,
        connection: sqlite3.Connection,
        revision_id: str,
    ) -> dict:
        row = connection.execute(
            "SELECT * FROM rule_set_revisions WHERE id = ?",
            (revision_id,),
        ).fetchone()
        if row is None:
            raise RuleSetRevisionNotFoundError(revision_id)
        return self._row_to_revision(row)

    @staticmethod
    def _row_to_ruleset(row: sqlite3.Row) -> dict:
        aggregate = dict(row)
        content = json.loads(aggregate.pop("draft_content_json"))
        draft = {
            **content,
            "revision": int(aggregate["draft_revision"]),
            "version": int(aggregate["draft_version"]),
            "status": "draft" if aggregate["status"] == "draft" else "published",
            "content_hash": aggregate["draft_content_hash"],
        }
        return {
            "id": aggregate["id"],
            **content,
            "status": aggregate["status"],
            "draft_revision": int(aggregate["draft_revision"]),
            "draft_version": int(aggregate["draft_version"]),
            "content_hash": aggregate["draft_content_hash"],
            "draft": draft,
            "published_revision_id": aggregate["published_revision_id"],
            "published_version": int(aggregate["published_version"] or 0),
            "owner_id": aggregate["owner_id"],
            "created_at": aggregate["created_at"],
            "updated_at": aggregate["updated_at"],
            "created_by": aggregate["created_by"],
        }

    @staticmethod
    def _row_to_revision(row: sqlite3.Row) -> dict:
        revision = dict(row)
        snapshot = json.loads(revision.pop("snapshot_json"))
        return {
            "id": revision["id"],
            "ruleset_id": revision["ruleset_id"],
            **snapshot,
            "revision": int(revision["draft_revision"]),
            "version": int(revision["version"]),
            "status": revision["status"],
            "content_hash": revision["content_hash"],
            "published_at": revision["published_at"],
            "published_by": revision["published_by"],
        }
