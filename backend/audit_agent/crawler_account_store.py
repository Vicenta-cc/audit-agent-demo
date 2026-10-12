from __future__ import annotations

import ipaddress
import math
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .config import settings


SUPPORTED_CRAWLER_ACCOUNT_PLATFORMS = {"xhs", "dy", "ks"}
CRAWLER_ACCOUNT_STATUSES = {"active", "login_required", "expired", "disabled"}
CRAWLER_ACCOUNT_ACCESS_SCOPES = {"private", "public"}


def account_is_cooling_down(account: dict, *, now: datetime | None = None) -> bool:
    cooldown_until = str(account.get("cooldown_until") or "").strip()
    if not cooldown_until:
        return False
    try:
        return datetime.fromisoformat(cooldown_until) > (now or datetime.now())
    except ValueError:
        return False


def account_in_slow_mode(account: dict, *, now: datetime | None = None) -> bool:
    """New logins and accounts probing after a risk cooldown crawl at reduced pace."""
    if int(account.get("risk_count") or 0) > 0:
        return True
    login_at = str(account.get("auth_state_updated_at") or "").strip()
    if not login_at:
        return False
    try:
        logged_in = datetime.fromisoformat(login_at)
    except ValueError:
        return False
    return (now or datetime.now()) - logged_in < timedelta(days=settings.crawler_new_account_days)


class CrawlerAccountStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (settings.data_dir / "audit_index.sqlite3")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS crawler_accounts (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    platform_account_id TEXT,
                    status TEXT NOT NULL DEFAULT 'login_required',
                    last_validated_at TEXT,
                    last_used_at TEXT,
                    last_error TEXT,
                    cooldown_until TEXT,
                    failure_kind TEXT,
                    auth_state_ciphertext TEXT,
                    auth_state_updated_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_crawler_accounts_platform_status
                ON crawler_accounts(platform, status, updated_at);
                """
            )
            self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(crawler_accounts)").fetchall()
        }
        if "auth_state_ciphertext" not in columns:
            conn.execute("ALTER TABLE crawler_accounts ADD COLUMN auth_state_ciphertext TEXT")
        if "auth_state_updated_at" not in columns:
            conn.execute("ALTER TABLE crawler_accounts ADD COLUMN auth_state_updated_at TEXT")
        if "cooldown_until" not in columns:
            conn.execute("ALTER TABLE crawler_accounts ADD COLUMN cooldown_until TEXT")
        if "failure_kind" not in columns:
            conn.execute("ALTER TABLE crawler_accounts ADD COLUMN failure_kind TEXT")
        if "access_scope" not in columns:
            conn.execute(
                "ALTER TABLE crawler_accounts ADD COLUMN access_scope TEXT NOT NULL DEFAULT 'private'"
            )
        if "risk_count" not in columns:
            conn.execute("ALTER TABLE crawler_accounts ADD COLUMN risk_count INTEGER NOT NULL DEFAULT 0")
        if "auth_state_revision" not in columns:
            conn.execute("ALTER TABLE crawler_accounts ADD COLUMN auth_state_revision INTEGER NOT NULL DEFAULT 0")
        if "last_risk_at" not in columns:
            conn.execute("ALTER TABLE crawler_accounts ADD COLUMN last_risk_at TEXT")

        for name, definition in (
            ("proxy_id", "TEXT"),
            ("binding_revision", "INTEGER NOT NULL DEFAULT 0"),
            ("proxy_required", "INTEGER NOT NULL DEFAULT 0"),
            ("validated_proxy_binding_revision", "INTEGER NOT NULL DEFAULT 0"),
            ("validated_proxy_config_revision", "INTEGER NOT NULL DEFAULT 0"),
                ("route_mode", "TEXT NOT NULL DEFAULT 'proxy_required'"),
                ("route_state", "TEXT NOT NULL DEFAULT 'active'"),
                ("shared_exit_key", "TEXT NOT NULL DEFAULT ''"),
                ("shared_exit_generation", "INTEGER NOT NULL DEFAULT 0"),
                ("paused_until", "REAL"),
                ("pause_reason", "TEXT NOT NULL DEFAULT ''"),
                ("skip_count", "INTEGER NOT NULL DEFAULT 0"),
                ("recovery_incident", "TEXT NOT NULL DEFAULT ''"),
                ("recovery_state", "TEXT NOT NULL DEFAULT ''"),
                ("recovery_claim", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE crawler_accounts ADD COLUMN {name} {definition}")
        conn.execute('''CREATE TABLE IF NOT EXISTS crawler_route_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, event_type TEXT NOT NULL,
            route_mode TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
            operation_id TEXT NOT NULL DEFAULT '', shared_exit_key TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL)''')

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS crawler_shared_exits (
                exit_key TEXT PRIMARY KEY, generation INTEGER NOT NULL,
                observed_at REAL NOT NULL, paused_until REAL,
                pause_reason TEXT NOT NULL DEFAULT '', service_exit INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS crawler_route_admissions (
                operation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                exit_key TEXT NOT NULL, generation INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL);
        """)

    def list(
        self,
        *,
        platform: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> list[dict]:
        def read(conn: sqlite3.Connection) -> list[dict]:
            parameters: tuple[str, ...] = ()
            where = ""
            if platform is not None:
                where = "WHERE platform = ?"
                parameters = (self._validate_platform(platform),)
            rows = conn.execute(
                """
                SELECT *
                FROM crawler_accounts
                {where}
                ORDER BY
                    CASE status
                        WHEN 'active' THEN 0
                        WHEN 'login_required' THEN 1
                        WHEN 'expired' THEN 2
                        ELSE 3
                    END,
                    updated_at DESC,
                    id ASC
                """.format(where=where),
                parameters,
            ).fetchall()
            return [self._row_to_account(row, connection=conn) for row in rows]

        if connection is not None:
            return read(connection)
        with self._lock, self._connect() as conn:
            return read(conn)

    def get(
        self,
        account_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict | None:
        def read(conn: sqlite3.Connection) -> dict | None:
            row = conn.execute(
                "SELECT * FROM crawler_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            return self._row_to_account(row, connection=conn) if row else None

        if connection is not None:
            return read(connection)
        with self._lock, self._connect() as conn:
            return read(conn)

    def create(
        self,
        *,
        platform: str,
        display_name: str,
        platform_account_id: str = "",
        access_scope: str = "private",
    ) -> dict:
        normalized_platform = self._validate_platform(platform)
        normalized_name = self._validate_display_name(display_name)
        normalized_platform_account_id = str(platform_account_id or "").strip()
        normalized_access_scope = self._validate_access_scope(access_scope)
        now = datetime.now().isoformat(timespec="seconds")
        account_id = uuid4().hex[:12]

        with self._lock, self._connect() as conn:
            self._ensure_account_id_available(
                conn,
                platform=normalized_platform,
                platform_account_id=normalized_platform_account_id,
            )
            conn.execute(
                """
                INSERT INTO crawler_accounts (
                    id, platform, display_name, platform_account_id, access_scope, status,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'login_required', ?, ?)
                """,
                (
                    account_id,
                    normalized_platform,
                    normalized_name,
                    normalized_platform_account_id,
                    normalized_access_scope,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM crawler_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            return self._row_to_account(row, connection=conn)

    def update(
        self,
        account_id: str,
        *,
        display_name: str | None = None,
        platform_account_id: str | None = None,
        status: str | None = None,
    ) -> dict | None:
        updates: dict[str, str] = {}
        if display_name is not None:
            updates["display_name"] = self._validate_display_name(display_name)
        if platform_account_id is not None:
            updates["platform_account_id"] = str(platform_account_id or "").strip()
        if status is not None:
            normalized_status = str(status or "").strip().lower()
            if normalized_status not in CRAWLER_ACCOUNT_STATUSES:
                raise ValueError(f"Unsupported crawler account status: {status}")
            updates["status"] = normalized_status
        if not updates:
            return self.get(account_id)

        with self._lock, self._connect() as conn:
            current = conn.execute(
                "SELECT * FROM crawler_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if not current:
                return None
            next_platform_account_id = updates.get(
                "platform_account_id",
                current["platform_account_id"] or "",
            )
            self._ensure_account_id_available(
                conn,
                platform=current["platform"],
                platform_account_id=next_platform_account_id,
                exclude_id=account_id,
            )
            if current["platform"] == "dy" and any(
                key in updates and updates[key] != (current[key] or "")
                for key in ("platform_account_id", "status")
            ):
                updates["auth_state_revision"] = int(current["auth_state_revision"]) + 1
            updates["updated_at"] = datetime.now().isoformat(timespec="seconds")
            assignments = ", ".join(f"{column} = ?" for column in updates)
            conn.execute(
                f"UPDATE crawler_accounts SET {assignments} WHERE id = ?",
                (*updates.values(), account_id),
            )
            row = conn.execute(
                "SELECT * FROM crawler_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            return self._row_to_account(row, connection=conn)

    def delete(self, account_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM crawler_accounts WHERE id = ?",
                (account_id,),
            )
            return cursor.rowcount > 0

    def save_auth_state(
        self,
        account_id: str,
        ciphertext: str,
        platform_account_id: str | None = None,
    ) -> dict | None:
        normalized_ciphertext = str(ciphertext or "").strip()
        if not normalized_ciphertext:
            raise ValueError("auth_state 密文不能为空")
        normalized_platform_account_id = (
            str(platform_account_id or "").strip()
            if platform_account_id is not None
            else None
        )
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            current = conn.execute(
                "SELECT * FROM crawler_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            if not current:
                return None
            if normalized_platform_account_id:
                self._ensure_account_id_available(
                    conn,
                    platform=current["platform"],
                    platform_account_id=normalized_platform_account_id,
                    exclude_id=account_id,
                )
            cursor = conn.execute(
                """
                UPDATE crawler_accounts
                SET auth_state_ciphertext = ?, auth_state_updated_at = ?,
                    status = 'active', last_validated_at = ?, last_error = '',
                    cooldown_until = NULL, failure_kind = NULL, updated_at = ?,
                    auth_state_revision = auth_state_revision + CASE WHEN platform='dy' AND (COALESCE(auth_state_ciphertext,'') <> ? OR status <> 'active' OR (? <> '' AND COALESCE(platform_account_id,'') <> ?)) THEN 1 ELSE 0 END
                WHERE id = ?
                """,
                (normalized_ciphertext, now, now, now, normalized_ciphertext, normalized_platform_account_id or "", normalized_platform_account_id or "", account_id),
            )
            if normalized_platform_account_id:
                conn.execute(
                    "UPDATE crawler_accounts SET platform_account_id = ? WHERE id = ?",
                    (normalized_platform_account_id, account_id),
                )
            row = conn.execute(
                "SELECT * FROM crawler_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            return self._row_to_account(row, connection=conn)

    def save_douyin_auth_state(
        self, account_id: str, ciphertext: str, platform_account_id: str | None = None,
        *, connection: sqlite3.Connection | None = None, expected_revision: int | None = None,
        credentials_changed: bool = True,
    ) -> dict | None:
        if not str(ciphertext or "").strip():
            raise ValueError("auth_state 密文不能为空")
        def write(conn):
            current = self.get(account_id, connection=conn)
            if current is None:
                return None
            if current["platform"] != "dy":
                raise ValueError("DY_ACCOUNT_REQUIRED")
            if expected_revision is not None and current["auth_state_revision"] != expected_revision:
                raise ValueError("AUTH_REVISION_CONFLICT")
            identity = str(platform_account_id or current["platform_account_id"]).strip()
            self._ensure_account_id_available(conn, platform="dy", platform_account_id=identity, exclude_id=account_id)
            now = datetime.now().isoformat(timespec="seconds")
            risk = current["failure_kind"] not in ("", "auth")
            status = "disabled" if current["status"] == "disabled" else "active"
            existing_ciphertext = conn.execute("SELECT auth_state_ciphertext FROM crawler_accounts WHERE id=?", (account_id,)).fetchone()[0]
            changed_credentials = (credentials_changed or not existing_ciphertext) and ciphertext != (existing_ciphertext or "")
            stored_ciphertext = ciphertext if changed_credentials else existing_ciphertext
            revision_change = changed_credentials or identity != current["platform_account_id"] or status != current["status"]
            conn.execute("""UPDATE crawler_accounts SET auth_state_ciphertext=?,
                platform_account_id=?, status=?, last_validated_at=?, updated_at=?,
                auth_state_updated_at=CASE WHEN ? THEN ? ELSE auth_state_updated_at END,
                auth_state_revision=auth_state_revision+?, last_error=?, failure_kind=? WHERE id=?""",
                (stored_ciphertext, identity, status, now, now, changed_credentials, now, int(revision_change),
                 current["last_error"] if risk else "", current["failure_kind"] if risk else None, account_id))
            return self.get(account_id, connection=conn)
        if connection is not None:
            return write(connection)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return write(conn)

    save_douyin_login_auth_state = save_douyin_auth_state

    def mark_douyin_auth_invalid(
        self, account_id: str, message: str = "", *, status: str = "expired",
        reset: bool = False, connection: sqlite3.Connection | None = None,
    ) -> dict | None:
        if status not in ("expired", "login_required"):
            raise ValueError("INVALID_AUTH_STATUS")
        def write(conn):
            current = self.get(account_id, connection=conn)
            if current is None:
                return None
            if current["platform"] != "dy":
                raise ValueError("DY_ACCOUNT_REQUIRED")
            risk = current["failure_kind"] not in ("", "auth")
            next_status = "disabled" if current["status"] == "disabled" else status
            changed = reset or current["status"] != next_status
            conn.execute("""UPDATE crawler_accounts SET status=?,
                auth_state_ciphertext=CASE WHEN ? THEN NULL ELSE auth_state_ciphertext END,
                auth_state_updated_at=CASE WHEN ? THEN NULL ELSE auth_state_updated_at END,
                auth_state_revision=auth_state_revision+?, failure_kind=?, last_error=?, updated_at=? WHERE id=?""",
                (next_status, reset, reset, int(changed), current["failure_kind"] if risk else "auth",
                 current["last_error"] if risk else str(message)[:500], datetime.now().isoformat(timespec="seconds"), account_id))
            return self.get(account_id, connection=conn)
        if connection is not None:
            return write(connection)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return write(conn)

    def reset_douyin_auth_state(self, account_id: str, *, connection=None) -> dict | None:
        return self.mark_douyin_auth_invalid(account_id, status="login_required", reset=True, connection=connection)

    def get_auth_state_ciphertext(self, account_id: str) -> str:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT auth_state_ciphertext FROM crawler_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            return str(row["auth_state_ciphertext"] or "") if row else ""

    def record_login_error(self, account_id: str, message: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE crawler_accounts
                SET last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(message or "")[:500], now, account_id),
            )

    def mark_used(self, account_id: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE crawler_accounts
                SET last_used_at = ?, skip_count=0, last_error = '', updated_at = ?
                WHERE id = ?
                """,
                (now, now, account_id),
            )

    def mark_expired(self, account_id: str, message: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE crawler_accounts
                SET auth_state_revision = auth_state_revision + CASE WHEN platform='dy' AND status <> 'expired' THEN 1 ELSE 0 END,
                    status = 'expired', last_error = ?, failure_kind = 'auth',
                    cooldown_until = NULL, updated_at = ?
                WHERE id = ?
                """,
                (str(message or "账号登录态已失效")[:500], now, account_id),
            )

    def mark_cooldown(self, account_id: str, message: str, until: str, *, failure_kind: str = "rate_limit") -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE crawler_accounts SET status='active', last_error=?, failure_kind=?, cooldown_until=?, updated_at=? WHERE id=?",
                (str(message or "账号进入冷却")[:500], str(failure_kind), str(until), datetime.now().isoformat(timespec="seconds"), account_id),
            )

    def record_risk(self, account_id: str, *, window: timedelta = timedelta(hours=24)) -> int:
        """Count a risk signal; a signal after ``window`` of quiet starts over at 1."""
        now = datetime.now()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT risk_count, last_risk_at FROM crawler_accounts WHERE id=?", (account_id,)
            ).fetchone()
            if not row:
                return 0
            count = int(row["risk_count"] or 0)
            try:
                recent = bool(row["last_risk_at"]) and now - datetime.fromisoformat(row["last_risk_at"]) <= window
            except ValueError:
                recent = False
            count = count + 1 if recent else 1
            conn.execute(
                "UPDATE crawler_accounts SET risk_count=?, last_risk_at=? WHERE id=?",
                (count, now.isoformat(timespec="seconds"), account_id),
            )
            return count

    def reset_risk(self, account_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE crawler_accounts SET risk_count=0 WHERE id=? AND risk_count<>0", (account_id,))

    def available_accounts(self, platform: str, *, exclude_ids=()) -> list[dict]:
        now = datetime.now().isoformat(timespec="seconds")
        excluded = tuple(str(value) for value in exclude_ids if str(value))
        placeholders = ",".join("?" for _ in excluded)
        exclusion = f"AND id NOT IN ({placeholders}) " if excluded else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM crawler_accounts WHERE platform=? AND status='active' "
                "AND COALESCE(auth_state_ciphertext,'')<>'' AND route_state='active' AND (paused_until IS NULL OR paused_until<=strftime('%s','now')) "
                + exclusion +
                "AND (cooldown_until IS NULL OR cooldown_until<=?) "
                "ORDER BY CASE access_scope WHEN 'public' THEN 0 ELSE 1 END, "
                "last_used_at IS NOT NULL, last_used_at, id",
                (platform, *excluded, now),
            ).fetchall()
            accounts = [self._row_to_account(row, connection=conn) for row in rows]
            table_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proxy_resources'").fetchone()
            available = []
            epoch = time.time()
            for account in accounts:
                if account.get("route_mode") == "direct_degraded":
                    if self._exit_allowed(conn, account["shared_exit_key"], account["shared_exit_generation"]):
                        available.append(account)
                    continue
                if not account["proxy_id"]:
                    if not account["proxy_required"]:
                        available.append(account)
                    continue
                resource = conn.execute("SELECT * FROM proxy_resources WHERE id=?", (account["proxy_id"],)).fetchone() if table_exists else None
                if (resource and resource["platform"] == account["platform"] and resource["status"] == "active"
                    and not resource["stop_reason"]
                    and account["validated_proxy_binding_revision"] == account["binding_revision"]
                    and account["validated_proxy_config_revision"] == resource["config_revision"]
                    and (resource["expires_at"] is not None and resource["expires_at"] > epoch)
                    and (resource["cooldown_until"] is None or resource["cooldown_until"] <= epoch)
                    and (resource["paused_until"] is None or resource["paused_until"] <= epoch)
                    and (not settings.crawler_safety_enabled or self._exit_allowed(conn, resource["shared_exit_key"], resource["shared_exit_generation"]))):
                    available.append(account)
            return available

    def next_available(self, platform: str, *, exclude_id: str = "") -> dict | None:
        accounts = self.available_accounts(platform, exclude_ids=(exclude_id,))
        return accounts[0] if accounts else None

    def _ensure_account_id_available(
        self,
        conn: sqlite3.Connection,
        *,
        platform: str,
        platform_account_id: str,
        exclude_id: str = "",
    ) -> None:
        if not platform_account_id:
            return
        row = conn.execute(
            """
            SELECT id
            FROM crawler_accounts
            WHERE platform = ? AND lower(platform_account_id) = lower(?) AND id != ?
            """,
            (platform, platform_account_id, exclude_id),
        ).fetchone()
        if row:
            raise ValueError("该平台账号已存在")

    def _validate_platform(self, platform: str) -> str:
        normalized = str(platform or "").strip().lower()
        if normalized not in SUPPORTED_CRAWLER_ACCOUNT_PLATFORMS:
            raise ValueError(f"Unsupported crawler account platform: {platform}")
        return normalized

    def _validate_display_name(self, display_name: str) -> str:
        normalized = str(display_name or "").strip()
        if not normalized:
            raise ValueError("账号名称不能为空")
        if len(normalized) > 80:
            raise ValueError("账号名称不能超过 80 个字符")
        return normalized

    def _validate_access_scope(self, access_scope: str) -> str:
        normalized = str(access_scope or "private").strip().lower()
        if normalized not in CRAWLER_ACCOUNT_ACCESS_SCOPES:
            raise ValueError(f"Unsupported crawler account access scope: {access_scope}")
        return normalized

    def _row_to_account(self, row: sqlite3.Row, *, connection: sqlite3.Connection | None = None) -> dict:
        needs_validation = False
        if row["proxy_required"]:
            needs_validation = True
            if row["proxy_id"] and connection is not None:
                exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='proxy_resources' AND type='table'").fetchone()
                resource = connection.execute("SELECT config_revision FROM proxy_resources WHERE id=?", (row["proxy_id"],)).fetchone() if exists else None
                needs_validation = not (resource and row["validated_proxy_binding_revision"] == row["binding_revision"]
                    and row["validated_proxy_config_revision"] == resource["config_revision"])
        return {
            "id": row["id"],
            "proxy_id": row["proxy_id"],
            "binding_revision": int(row["binding_revision"]),
            "proxy_required": bool(row["proxy_required"]),
            "validated_proxy_binding_revision": int(row["validated_proxy_binding_revision"]),
            "validated_proxy_config_revision": int(row["validated_proxy_config_revision"]),
            "needs_proxy_login_validation": needs_validation,
            "platform": row["platform"],
            "display_name": row["display_name"],
            "platform_account_id": row["platform_account_id"] or "",
            "access_scope": row["access_scope"] or "private",
            "status": row["status"],
            "last_validated_at": row["last_validated_at"] or "",
            "last_used_at": row["last_used_at"] or "",
            "last_error": row["last_error"] or "",
            "cooldown_until": row["cooldown_until"] or "",
            "failure_kind": row["failure_kind"] or "",
            "auth_state_revision": int(row["auth_state_revision"]),
            "has_auth_state": bool(row["auth_state_ciphertext"]),
            "auth_state_updated_at": row["auth_state_updated_at"] or "",
            "risk_count": int(row["risk_count"] or 0),
            "last_risk_at": row["last_risk_at"] or "",
            "route_mode": row["route_mode"] or "proxy_required",
            "route_state": row["route_state"] or "active",
            "shared_exit_key": row["shared_exit_key"] or "",
            "shared_exit_generation": int(row["shared_exit_generation"] or 0),
            "paused_until": row["paused_until"],
            "pause_reason": row["pause_reason"] or "",
            "skip_count": int(row["skip_count"] or 0),
            "recovery_state": row["recovery_state"] or '',
            "recovery_incident": row["recovery_incident"] or '',
            "recovery_claim": row["recovery_claim"] or '', 
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    ROUTE_MODES = frozenset({'proxy_required', 'proxy_preferred', 'direct_degraded', 'legacy_direct'})
    ROUTE_STATES = frozenset({'active', 'transport_blocked', 'challenge_paused', 'manual_required', 'draining', 'drain_timeout', 'recovery_check'})
    EVENT_TYPES = frozenset({'route_changed', 'transport_blocked', 'challenge_paused', 'recovery_claimed', 'recovery_succeeded', 'manual_required', 'route_admitted', 'route_ended'})
    REASONS = frozenset({'', 'ACCOUNT_CHALLENGE', 'AUTH_INVALID', 'PLATFORM_429', 'PROXY_EXPIRED', 'PROXY_TRANSPORT_FAILED', 'PROXY_CONNECTION_REFUSED', 'POOL_EXHAUSTED', 'RECOVERY_SUCCESS', 'RECOVERY_UNCERTAIN', 'DRAIN_TIMEOUT'})

    @staticmethod
    def _epoch(value):
        if value is None:
            return None
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('ROUTE_TIME_INVALID')
        return float(value)

    @staticmethod
    def _generation(value):
        if type(value) is not int or value < 1:
            raise ValueError('ROUTE_GENERATION_INVALID')
        return value

    @staticmethod
    def _exit_key(value):
        try:
            return str(ipaddress.ip_address(value))
        except (ValueError, TypeError):
            raise ValueError('ROUTE_EXIT_INVALID') from None

    def _exit_allowed(self, conn, key, generation, *, recovery=False):
        row = conn.execute('SELECT * FROM crawler_shared_exits WHERE exit_key=?', (key,)).fetchone()
        return bool(row and row['generation'] == generation
            and 0 <= time.time() - row['observed_at'] <= settings.crawler_exit_observation_ttl
            and (recovery or not row['pause_reason'])
            and (row['paused_until'] is None or row['paused_until'] <= time.time()))

    def observe_shared_exit(self, ip, *, observed_at=None, service_exit=False, connection=None):
        key = self._exit_key(ip)
        at = time.time() if observed_at is None else self._epoch(observed_at)
        if at > time.time() + 5 or type(service_exit) is not bool:
            raise ValueError('ROUTE_OBSERVATION_INVALID')
        def update(conn):
            row = conn.execute('SELECT * FROM crawler_shared_exits WHERE exit_key=?', (key,)).fetchone()
            if row and at < row['observed_at']:
                raise ValueError('PROXY_STALE_OBSERVATION')
            generation = row['generation'] if row else 1
            conn.execute("INSERT INTO crawler_shared_exits(exit_key,generation,observed_at,service_exit) VALUES(?,?,?,?) ON CONFLICT(exit_key) DO UPDATE SET observed_at=excluded.observed_at,service_exit=MAX(service_exit,excluded.service_exit)", (key,generation,at,int(service_exit)))
            return {'observed_exit_ip': key, 'observed_exit_at': at, 'shared_exit_key': key, 'shared_exit_generation': generation}
        if connection is not None:
            return update(connection)
        with self._lock, self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return update(conn)

    def get_direct_route(self, account_id):
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM crawler_shared_exits WHERE service_exit=1 ORDER BY observed_at DESC LIMIT 1').fetchone()
            if not row or not self._exit_allowed(conn,row['exit_key'],row['generation']):
                return None
            return dict(observed_exit_ip=row['exit_key'],observed_exit_at=row['observed_at'],shared_exit_key=row['exit_key'],shared_exit_generation=row['generation'])

    def admit_route(self, account_id, observed_exit, generation, operation_id, *, recovery_claim=None):
        key = self._exit_key(observed_exit)
        generation = self._generation(generation)
        if not isinstance(operation_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',operation_id):
            raise ValueError('ROUTE_OPERATION_INVALID')
        with self._lock, self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            prior = conn.execute('SELECT * FROM crawler_route_admissions WHERE operation_id=?',(operation_id,)).fetchone()
            if prior:
                if (prior['account_id'],prior['exit_key'],prior['generation'],prior['state']) != (account_id,key,generation,'active'):
                    raise ValueError('ROUTE_OPERATION_CONFLICT')
                return operation_id
            account = conn.execute('SELECT * FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            recovery = bool(account and recovery_claim and account['recovery_claim']==recovery_claim and account['recovery_state']=='claimed')
            if not account or account['status']!='active' or (account['route_state']!='active' and not recovery) or (account['paused_until'] and account['paused_until']>time.time()) or not self._exit_allowed(conn,key,generation,recovery=recovery):
                raise ValueError('ACCOUNT_CHALLENGE')
            conn.execute('INSERT INTO crawler_route_admissions(operation_id,account_id,exit_key,generation,created_at) VALUES(?,?,?,?,?)',(operation_id,account_id,key,generation,time.time()))
            conn.execute('UPDATE crawler_accounts SET shared_exit_key=?,shared_exit_generation=? WHERE id=?',(key,generation,account_id))
            return operation_id

    def release_route_admission(self, operation_id):
        with self._connect() as conn:
            conn.execute("UPDATE crawler_route_admissions SET state='ended' WHERE operation_id=?",(operation_id,))

    def pause_account_exit(self, account_id, *, until, reason='ACCOUNT_CHALLENGE'):
        until = self._epoch(until)
        if until is None or until <= time.time() or reason not in self.REASONS or not reason:
            raise ValueError('ROUTE_PAUSE_INVALID')
        with self._lock, self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            if row is None:
                raise ValueError('ACCOUNT_NOT_FOUND')
            key = row['shared_exit_key']
            if not key and row['proxy_id'] and conn.execute("SELECT 1 FROM sqlite_master WHERE name='proxy_resources'").fetchone():
                proxy = conn.execute('SELECT shared_exit_key FROM proxy_resources WHERE id=?',(row['proxy_id'],)).fetchone()
                key = proxy['shared_exit_key'] if proxy else ''
            incident = uuid4().hex
            conn.execute("UPDATE crawler_accounts SET route_state='challenge_paused',paused_until=?,pause_reason=?,recovery_incident=?,recovery_state='pending',recovery_claim='',shared_exit_key=? WHERE id=?",(until,reason,incident,key,account_id))
            if key:
                conn.execute("UPDATE crawler_shared_exits SET paused_until=MAX(COALESCE(paused_until,0),?),pause_reason=? WHERE exit_key=?",(until,reason,key))
            conn.execute("INSERT INTO crawler_route_events(account_id,event_type,reason,created_at) VALUES(?,'challenge_paused',?,?)",(account_id,reason,time.time()))
            return incident

    def claim_recovery(self, account_id):
        with self._lock, self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            if not row or row['recovery_state']!='pending' or row['route_state']!='challenge_paused' or row['paused_until'] is None or row['paused_until']>time.time():
                return None
            claim=uuid4().hex
            conn.execute("UPDATE crawler_accounts SET recovery_state='claimed',recovery_claim=?,route_state='recovery_check' WHERE id=?",(claim,account_id))
            return claim

    def finish_recovery(self, account_id, claim, result):
        if result not in {'success','challenge','uncertain'}:
            raise ValueError('RECOVERY_RESULT_INVALID')
        with self._lock, self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT * FROM crawler_accounts WHERE id=? AND recovery_claim=? AND recovery_state='claimed'",(account_id,claim)).fetchone()
            if not row:
                raise ValueError('RECOVERY_CLAIM_INVALID')
            state='active' if result=='success' else 'manual_required'
            conn.execute('UPDATE crawler_accounts SET recovery_state=?,route_state=?,pause_reason=?,paused_until=NULL WHERE id=?',(result,state,'' if result=='success' else 'RECOVERY_UNCERTAIN',account_id))
            if result=='success' and row['shared_exit_key'] and not conn.execute("SELECT 1 FROM crawler_accounts WHERE shared_exit_key=? AND id<>? AND route_state IN ('challenge_paused','recovery_check','manual_required')",(row['shared_exit_key'],account_id)).fetchone():
                conn.execute("UPDATE crawler_shared_exits SET pause_reason='',paused_until=NULL WHERE exit_key=?",(row['shared_exit_key'],))

    def record_route_event(self, account_id, event_type, *, route_mode='', reason='', operation_id='', shared_exit_key=''):
        if event_type not in self.EVENT_TYPES or reason not in self.REASONS or route_mode not in self.ROUTE_MODES | {''}:
            raise ValueError('ROUTE_EVENT_INVALID')
        if operation_id and (not isinstance(operation_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',operation_id)):
            raise ValueError('ROUTE_OPERATION_INVALID')
        key=self._exit_key(shared_exit_key) if shared_exit_key else ''
        with self._lock, self._connect() as conn:
            conn.execute('INSERT INTO crawler_route_events(account_id,event_type,route_mode,reason,operation_id,shared_exit_key,created_at) VALUES(?,?,?,?,?,?,?)',(account_id,event_type,route_mode,reason,operation_id,key,time.time()))

    def set_route_state(self, account_id, *, state, route_mode=None, paused_until=None, pause_reason=None, shared_exit_key=None, generation=None):
        if state not in self.ROUTE_STATES or (route_mode is not None and route_mode not in self.ROUTE_MODES) or (pause_reason is not None and pause_reason not in self.REASONS):
            raise ValueError('ROUTE_STATE_INVALID')
        updates={'route_state':state}
        if route_mode is not None: updates['route_mode']=route_mode
        if paused_until is not None: updates['paused_until']=self._epoch(paused_until)
        if pause_reason is not None: updates['pause_reason']=pause_reason
        if shared_exit_key is not None: updates['shared_exit_key']=self._exit_key(shared_exit_key)
        if generation is not None: updates['shared_exit_generation']=self._generation(generation)
        with self._lock, self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old=conn.execute('SELECT shared_exit_generation FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            if not old: return None
            if generation is not None and generation < old[0]: raise ValueError('ROUTE_GENERATION_STALE')
            sets=','.join(f'{k}=?' for k in updates)
            conn.execute(f'UPDATE crawler_accounts SET {sets},updated_at=? WHERE id=?',(*updates.values(),datetime.now().isoformat(timespec='seconds'),account_id))
        return self.get(account_id)

    @staticmethod
    def fair_key(account, *, skip_limit=None):
        limit=settings.crawler_fair_skip_limit if skip_limit is None else skip_limit
        return (account.get('skip_count',0)<limit, bool(account.get('last_used_at')),account.get('last_used_at') or '',account['id'])

    def record_fair_selection(self, selected_id, eligible_ids):
        ids=tuple(dict.fromkeys(eligible_ids))
        if selected_id not in ids: raise ValueError('FAIR_SELECTION_INVALID')
        with self._lock,self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for account_id in ids:
                conn.execute("UPDATE crawler_accounts SET skip_count=CASE WHEN id=? THEN 0 ELSE MIN(skip_count+1,?) END WHERE id=? AND route_state='active'",(selected_id,settings.crawler_fair_skip_limit,account_id))

    def select_fair(self, platform, *, exclude_ids=(), skip_limit=3):
        candidates=self.available_accounts(platform,exclude_ids=exclude_ids)
        if not candidates: return None
        chosen=min(candidates,key=lambda a:self.fair_key(a,skip_limit=skip_limit))
        self.record_fair_selection(chosen['id'],[a['id'] for a in candidates])
        return chosen


crawler_account_store = CrawlerAccountStore()
