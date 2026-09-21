from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.investigation_creation.principal import Principal

from .passwords import hash_password, verify_password


LEGACY_UNASSIGNED_USER_ID = "legacy-unassigned"
ADMIN_SESSION_DAYS = 7
VALID_ROLES = frozenset({"admin", "user", "system"})
VALID_STATUSES = frozenset({"active", "disabled"})
VALID_ACTIVATION_MODES = frozenset({"first_login", "created_at"})
VALID_PERMISSIONS = frozenset({"read", "use", "manage"})


class AuthenticationError(Exception):
    pass


class AccountExpiredError(AuthenticationError):
    pass


class AuthorizationError(Exception):
    pass


class AuthStore:
    def __init__(
        self,
        db_path: Path,
        *,
        default_validity_days: int = 7,
        activation_mode: str = "first_login",
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_validity_days = max(1, int(default_validity_days))
        normalized_mode = str(activation_mode).strip().lower()
        if normalized_mode not in VALID_ACTIVATION_MODES:
            raise ValueError("unsupported account activation mode")
        self.activation_mode = normalized_mode
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _init_db(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    username_key TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'user', 'system')),
                    status TEXT NOT NULL CHECK(status IN ('active', 'disabled')),
                    activation_mode TEXT NOT NULL,
                    validity_days INTEGER NOT NULL,
                    validity_started_at TEXT,
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS login_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    csrf_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES app_users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_login_sessions_user
                ON login_sessions(user_id, expires_at);

                CREATE TABLE IF NOT EXISTS login_session_csrf_tokens (
                    session_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, token_hash),
                    FOREIGN KEY(session_id) REFERENCES login_sessions(id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS crawler_account_owners (
                    account_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL REFERENCES app_users(id),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_crawler_account_owners_user
                ON crawler_account_owners(owner_user_id);

                CREATE TABLE IF NOT EXISTS resource_grants (
                    user_id TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    permission TEXT NOT NULL CHECK(permission IN ('read', 'use', 'manage')),
                    granted_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT,
                    PRIMARY KEY(user_id, resource_type, resource_id, permission),
                    FOREIGN KEY(user_id) REFERENCES app_users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_resource_grants_resource
                ON resource_grants(resource_type, resource_id, revoked_at);

                CREATE TABLE IF NOT EXISTS application_audit_events (
                    id TEXT PRIMARY KEY,
                    actor_user_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                """
            )
            now = _utc_now()
            connection.execute(
                """
                INSERT OR IGNORE INTO app_users (
                    id, username, username_key, password_hash, role, status,
                    activation_mode, validity_days, validity_started_at,
                    expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'system', 'disabled', 'created_at', 1, ?, ?, ?, ?)
                """,
                (
                    LEGACY_UNASSIGNED_USER_ID,
                    "历史待分配空间",
                    "__legacy_unassigned__",
                    "disabled",
                    _iso(now),
                    _iso(now),
                    _iso(now),
                    _iso(now),
                ),
            )

    def create_user(
        self,
        *,
        username: str,
        password: str,
        role: str = "user",
        validity_days: int | None = None,
        activation_mode: str | None = None,
        actor_user_id: str = "system",
    ) -> dict[str, Any]:
        display, key = _normalize_username(username)
        normalized_role = str(role).strip().lower()
        if normalized_role not in {"admin", "user"}:
            raise ValueError("role must be admin or user")
        days = max(1, int(validity_days or self.default_validity_days))
        mode = str(activation_mode or self.activation_mode).strip().lower()
        if mode not in VALID_ACTIVATION_MODES:
            raise ValueError("unsupported account activation mode")
        encoded_password = hash_password(password)
        now = _utc_now()
        started = now if mode == "created_at" else None
        expires = now + timedelta(days=days) if started and normalized_role != "admin" else None
        user_id = str(uuid4())
        try:
            with self._lock, self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO app_users (
                        id, username, username_key, password_hash, role, status,
                        activation_mode, validity_days, validity_started_at,
                        expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        display,
                        key,
                        encoded_password,
                        normalized_role,
                        mode,
                        days,
                        _optional_iso(started),
                        _optional_iso(expires),
                        _iso(now),
                        _iso(now),
                    ),
                )
                self._audit(
                    connection,
                    actor_user_id=actor_user_id,
                    event_type="user.created",
                    subject_type="user",
                    subject_id=user_id,
                    detail=f"role={normalized_role};activation={mode};days={days}",
                    now=now,
                )
                row = connection.execute(
                    "SELECT * FROM app_users WHERE id=?", (user_id,)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError("username already exists") from exc
        return _public_user(row)

    def login(self, username: str, password: str) -> tuple[dict[str, Any], str, str]:
        _, key = _normalize_username(username)
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM app_users WHERE username_key=?", (key,)
            ).fetchone()
            if (
                row is None
                or row["role"] == "system"
                or row["status"] != "active"
                or not verify_password(password, str(row["password_hash"]))
            ):
                raise AuthenticationError("invalid username or password")
            started = _parse_optional(row["validity_started_at"])
            expires = _parse_optional(row["expires_at"])
            if row["role"] == "admin":
                # Account lifetime is unlimited; login sessions remain bounded.
                started = started or now
                expires = None
                connection.execute(
                    "UPDATE app_users SET validity_started_at=?, expires_at=NULL, updated_at=? WHERE id=?",
                    (_iso(started), _iso(now), row["id"]),
                )
                row = connection.execute("SELECT * FROM app_users WHERE id=?", (row["id"],)).fetchone()
            elif started is None:
                started = now
                expires = now + timedelta(days=int(row["validity_days"]))
                connection.execute(
                    """
                    UPDATE app_users
                    SET validity_started_at=?, expires_at=?, updated_at=?
                    WHERE id=? AND validity_started_at IS NULL
                    """,
                    (_iso(started), _iso(expires), _iso(now), row["id"]),
                )
                row = connection.execute(
                    "SELECT * FROM app_users WHERE id=?", (row["id"],)
                ).fetchone()
            if row["role"] != "admin" and (expires is None or now >= expires):
                raise AccountExpiredError("account has expired")
            session_expires = now + timedelta(days=ADMIN_SESSION_DAYS) if row["role"] == "admin" else expires
            session_token = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            session_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO login_sessions (
                    id, user_id, token_hash, csrf_hash, created_at, expires_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    session_id,
                    row["id"],
                    _secret_hash(session_token),
                    _secret_hash(csrf_token),
                    _iso(now),
                    _iso(session_expires),
                ),
            )
            self._audit(
                connection,
                actor_user_id=str(row["id"]),
                event_type="session.created",
                subject_type="session",
                subject_id=session_id,
                now=now,
            )
        return _public_user(row), session_token, csrf_token

    def authenticate_session(
        self,
        token: str,
        *,
        csrf_token: str | None = None,
        now: datetime | None = None,
    ) -> Principal:
        raw_token = str(token or "")
        if not raw_token:
            raise AuthenticationError("authentication required")
        observed = now or _utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    s.id AS session_id,
                    s.csrf_hash,
                    s.expires_at AS session_expires_at,
                    s.revoked_at,
                    u.id AS user_id,
                    u.role,
                    u.status,
                    u.expires_at AS user_expires_at
                FROM login_sessions s
                JOIN app_users u ON u.id=s.user_id
                WHERE s.token_hash=?
                """,
                (_secret_hash(raw_token),),
            ).fetchone()
            csrf_valid = True
            if row is not None and csrf_token is not None:
                observed_csrf_hash = _secret_hash(str(csrf_token))
                csrf_valid = secrets.compare_digest(
                    observed_csrf_hash, str(row["csrf_hash"])
                ) or connection.execute(
                    """
                    SELECT 1 FROM login_session_csrf_tokens
                    WHERE session_id=? AND token_hash=?
                    """,
                    (row["session_id"], observed_csrf_hash),
                ).fetchone() is not None
        if row is None or row["revoked_at"] or row["status"] != "active":
            raise AuthenticationError("session is invalid")
        session_expires = _parse_required(row["session_expires_at"])
        user_expires = None if row["role"] == "admin" else _parse_optional(row["user_expires_at"])
        if row["role"] != "admin" and (user_expires is None or observed >= user_expires):
            raise AccountExpiredError("account has expired")
        if observed >= session_expires:
            raise AuthenticationError("session has expired; please log in again")
        if csrf_token is not None and not csrf_valid:
            raise AuthorizationError("CSRF validation failed")
        return Principal(
            id=str(row["user_id"]),
            role=str(row["role"]),
            session_id=str(row["session_id"]),
            expires_at=_iso(min(session_expires, user_expires) if user_expires else session_expires),
        )

    def logout(self, token: str, *, actor_user_id: str) -> None:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM login_sessions WHERE token_hash=?",
                (_secret_hash(str(token or "")),),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                "UPDATE login_sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (_iso(now), row["id"]),
            )
            self._audit(
                connection,
                actor_user_id=actor_user_id,
                event_type="session.revoked",
                subject_type="session",
                subject_id=str(row["id"]),
                now=now,
            )

    def rotate_csrf(self, token: str) -> str:
        """Issue a CSRF secret for another tab without exposing the session token."""
        raw_token = str(token or "")
        if not raw_token:
            raise AuthenticationError("authentication required")
        csrf_token = secrets.token_urlsafe(32)
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT s.id, s.expires_at, s.revoked_at,
                       u.status, u.role, u.expires_at AS user_expires_at
                FROM login_sessions s
                JOIN app_users u ON u.id=s.user_id
                WHERE s.token_hash=?
                """,
                (_secret_hash(raw_token),),
            ).fetchone()
            if row is None or row["revoked_at"] or row["status"] != "active":
                raise AuthenticationError("session is invalid")
            user_expires = None if row["role"] == "admin" else _parse_optional(row["user_expires_at"])
            if row["role"] != "admin" and (user_expires is None or now >= user_expires):
                raise AccountExpiredError("account has expired")
            if now >= _parse_required(row["expires_at"]):
                raise AuthenticationError("session has expired; please log in again")
            connection.execute(
                """
                INSERT OR IGNORE INTO login_session_csrf_tokens (
                    session_id, token_hash, created_at
                ) VALUES (?, ?, ?)
                """,
                (row["id"], _secret_hash(csrf_token), _iso(now)),
            )
        return csrf_token

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM app_users WHERE id=?", (str(user_id),)
            ).fetchone()
        return _public_user(row) if row is not None else None

    def principal_for_user(
        self, user_id: str, *, now: datetime | None = None
    ) -> Principal:
        observed = now or _utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM app_users WHERE id=?", (str(user_id),)
            ).fetchone()
        if row is None or row["role"] == "system" or row["status"] != "active":
            raise AuthenticationError("account is invalid")
        expires = None if row["role"] == "admin" else _parse_optional(row["expires_at"])
        if row["role"] != "admin" and (expires is None or observed >= expires):
            raise AccountExpiredError("account has expired")
        return Principal(
            id=str(row["id"]),
            role=str(row["role"]),
            expires_at=_iso(expires) if expires else "",
        )

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM app_users
                WHERE role != 'system'
                ORDER BY created_at, id
                """
            ).fetchall()
        return [_public_user(row) for row in rows]

    def update_user(
        self,
        user_id: str,
        *,
        actor_user_id: str,
        status: str | None = None,
        role: str | None = None,
        password: str | None = None,
        renew_days: int | None = None,
    ) -> dict[str, Any]:
        normalized_status = str(status).strip().lower() if status is not None else None
        normalized_role = str(role).strip().lower() if role is not None else None
        if normalized_status is not None and normalized_status not in VALID_STATUSES:
            raise ValueError("status must be active or disabled")
        if normalized_role is not None and normalized_role not in {"admin", "user"}:
            raise ValueError("role must be admin or user")
        encoded_password = hash_password(password) if password is not None else None
        extension = max(1, int(renew_days)) if renew_days is not None else None
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM app_users WHERE id=? AND role!='system'", (user_id,)
            ).fetchone()
            if current is None:
                raise KeyError(user_id)
            assignments: list[str] = ["updated_at=?"]
            values: list[Any] = [_iso(now)]
            if normalized_status is not None:
                assignments.append("status=?")
                values.append(normalized_status)
            if normalized_role is not None:
                assignments.append("role=?")
                values.append(normalized_role)
            if encoded_password is not None:
                assignments.append("password_hash=?")
                values.append(encoded_password)
            effective_role = normalized_role or current["role"]
            if effective_role == "admin":
                if extension is not None:
                    raise ValueError("administrator accounts do not expire and need no renewal")
                assignments.append("expires_at=NULL")
            elif current["role"] == "admin":
                # Demotion must not leave a regular user with unlimited lifetime.
                assignments.extend(("validity_started_at=?", "expires_at=?"))
                values.extend((_iso(now), _iso(now + timedelta(days=extension or self.default_validity_days))))
            elif extension is not None:
                current_expires = _parse_optional(current["expires_at"])
                base = max(now, current_expires) if current_expires else now
                assignments.extend(("validity_started_at=COALESCE(validity_started_at, ?)", "expires_at=?"))
                values.extend((_iso(now), _iso(base + timedelta(days=extension))))
            values.append(user_id)
            connection.execute(
                f"UPDATE app_users SET {', '.join(assignments)} WHERE id=?", values
            )
            if (
                normalized_status == "disabled"
                or normalized_role is not None
                or encoded_password is not None
            ):
                connection.execute(
                    """
                    UPDATE login_sessions
                    SET revoked_at=?
                    WHERE user_id=? AND revoked_at IS NULL
                    """,
                    (_iso(now), user_id),
                )
            self._audit(
                connection,
                actor_user_id=actor_user_id,
                event_type="user.updated",
                subject_type="user",
                subject_id=user_id,
                detail=(
                    f"status={normalized_status or ''};role={normalized_role or ''};"
                    f"password_reset={encoded_password is not None};renew_days={extension or 0}"
                ),
                now=now,
            )
            updated = connection.execute(
                "SELECT * FROM app_users WHERE id=?", (user_id,)
            ).fetchone()
        return _public_user(updated)

    def register_crawler_account(self, account_id: str, owner_user_id: str) -> None:
        """Bind a newly created account once. Existing bindings cannot be reassigned."""
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO crawler_account_owners(account_id, owner_user_id, created_at) VALUES (?, ?, ?)",
                (_required_text(account_id, "account_id"), owner_user_id, _iso(_utc_now())),
            )

    def owns_crawler_account(self, user_id: str, account_id: str) -> bool:
        with self._lock, self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM crawler_account_owners WHERE account_id=? AND owner_user_id=?",
                (account_id, user_id),
            ).fetchone() is not None

    def owned_crawler_account_ids(self, user_id: str) -> frozenset[str]:
        with self._lock, self._connect() as connection:
            return frozenset(str(row[0]) for row in connection.execute(
                "SELECT account_id FROM crawler_account_owners WHERE owner_user_id=?", (user_id,)
            ).fetchall())

    def unregister_crawler_account(self, account_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM crawler_account_owners WHERE account_id=?",
                (_required_text(account_id, "account_id"),),
            )

    def grant_resource(
        self,
        *,
        user_id: str,
        resource_type: str,
        resource_id: str,
        permission: str,
        actor_user_id: str,
    ) -> dict[str, str]:
        kind = _required_text(resource_type, "resource_type")
        if kind == "crawler-account":
            raise ValueError("crawler accounts are private to their owner and cannot be shared")
        resource = _required_text(resource_id, "resource_id")
        normalized_permission = str(permission).strip().lower()
        if normalized_permission not in VALID_PERMISSIONS:
            raise ValueError("unsupported resource permission")
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            target = connection.execute(
                "SELECT 1 FROM app_users WHERE id=? AND role!='system'", (user_id,)
            ).fetchone()
            if target is None:
                raise KeyError(user_id)
            connection.execute(
                """
                INSERT INTO resource_grants (
                    user_id, resource_type, resource_id, permission,
                    granted_by, created_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(user_id, resource_type, resource_id, permission)
                DO UPDATE SET granted_by=excluded.granted_by,
                              created_at=excluded.created_at,
                              revoked_at=NULL
                """,
                (
                    user_id,
                    kind,
                    resource,
                    normalized_permission,
                    actor_user_id,
                    _iso(now),
                ),
            )
            self._audit(
                connection,
                actor_user_id=actor_user_id,
                event_type="grant.created",
                subject_type=kind,
                subject_id=resource,
                detail=f"user_id={user_id};permission={normalized_permission}",
                now=now,
            )
        return {
            "user_id": user_id,
            "resource_type": kind,
            "resource_id": resource,
            "permission": normalized_permission,
        }

    def revoke_resource(
        self,
        *,
        user_id: str,
        resource_type: str,
        resource_id: str,
        permission: str,
        actor_user_id: str,
    ) -> bool:
        now = _utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE resource_grants
                SET revoked_at=?
                WHERE user_id=? AND resource_type=? AND resource_id=?
                  AND permission=? AND revoked_at IS NULL
                """,
                (
                    _iso(now),
                    user_id,
                    _required_text(resource_type, "resource_type"),
                    _required_text(resource_id, "resource_id"),
                    str(permission).strip().lower(),
                ),
            )
            changed = cursor.rowcount > 0
            if changed:
                self._audit(
                    connection,
                    actor_user_id=actor_user_id,
                    event_type="grant.revoked",
                    subject_type=resource_type,
                    subject_id=resource_id,
                    detail=f"user_id={user_id};permission={permission}",
                    now=now,
                )
        return changed

    def has_grant(
        self,
        user_id: str,
        resource_type: str,
        resource_id: str,
        permission: str,
    ) -> bool:
        accepted = _accepted_permissions(permission)
        placeholders = ",".join("?" for _ in accepted)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT 1 FROM resource_grants
                WHERE user_id=? AND resource_type=? AND resource_id=?
                  AND permission IN ({placeholders}) AND revoked_at IS NULL
                LIMIT 1
                """,
                (
                    user_id,
                    resource_type,
                    resource_id,
                    *accepted,
                ),
            ).fetchone()
        return row is not None

    def granted_resource_ids(
        self, user_id: str, resource_type: str, permission: str
    ) -> frozenset[str]:
        accepted = _accepted_permissions(permission)
        placeholders = ",".join("?" for _ in accepted)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT DISTINCT resource_id FROM resource_grants
                WHERE user_id=? AND resource_type=?
                  AND permission IN ({placeholders}) AND revoked_at IS NULL
                """,
                (user_id, resource_type, *accepted),
            ).fetchall()
        return frozenset(str(row["resource_id"]) for row in rows)

    def list_grants(self, user_id: str) -> list[dict[str, str]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT user_id, resource_type, resource_id, permission, created_at
                FROM resource_grants
                WHERE user_id=? AND revoked_at IS NULL
                ORDER BY resource_type, resource_id, permission
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        *,
        actor_user_id: str,
        event_type: str,
        subject_type: str,
        subject_id: str,
        now: datetime,
        detail: str = "",
    ) -> None:
        connection.execute(
            """
            INSERT INTO application_audit_events (
                id, actor_user_id, event_type, subject_type,
                subject_id, detail, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                actor_user_id or "system",
                event_type,
                subject_type,
                subject_id,
                detail,
                _iso(now),
            ),
        )


def _accepted_permissions(permission: str) -> tuple[str, ...]:
    normalized = str(permission).strip().lower()
    if normalized == "read":
        return ("read", "use", "manage")
    if normalized == "use":
        return ("use", "manage")
    if normalized == "manage":
        return ("manage",)
    raise ValueError("unsupported resource permission")


def _normalize_username(username: str) -> tuple[str, str]:
    display = unicodedata.normalize("NFKC", str(username or "")).strip()
    if not 3 <= len(display) <= 64:
        raise ValueError("username must contain between 3 and 64 characters")
    if any(character.isspace() or ord(character) < 32 for character in display):
        raise ValueError("username cannot contain whitespace or control characters")
    return display, display.casefold()


def _required_text(value: str, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    if len(normalized) > 200:
        raise ValueError(f"{field} is too long")
    return normalized


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _optional_iso(value: datetime | None) -> str | None:
    return _iso(value) if value is not None else None


def _parse_optional(value: str | None) -> datetime | None:
    return _parse_required(value) if value else None


def _parse_required(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _public_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "username": str(row["username"]),
        "role": str(row["role"]),
        "status": str(row["status"]),
        "activation_mode": str(row["activation_mode"]),
        "validity_days": int(row["validity_days"]),
        "validity_started_at": row["validity_started_at"],
        "expires_at": None if row["role"] == "admin" else row["expires_at"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }
