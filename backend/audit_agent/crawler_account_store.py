from __future__ import annotations

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
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE crawler_accounts ADD COLUMN {name} {definition}")
        conn.execute('''CREATE TABLE IF NOT EXISTS crawler_route_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, event_type TEXT NOT NULL,
            route_mode TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
            operation_id TEXT NOT NULL DEFAULT '', shared_exit_key TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL)''')

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
                SET last_used_at = ?, last_error = '', updated_at = ?
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
                "AND COALESCE(auth_state_ciphertext,'')<>'' "
                + exclusion +
                "AND (cooldown_until IS NULL OR cooldown_until<=?) "
                "ORDER BY CASE access_scope WHEN 'public' THEN 0 ELSE 1 END, "
                "last_used_at IS NOT NULL, last_used_at, id",
                (platform, *excluded, now),
            ).fetchall()
            accounts = [self._row_to_account(row, connection=conn) for row in rows]
            # Legacy accounts remain usable; required routes never fall back to direct.
            if not any(account["proxy_required"] or account["proxy_id"] for account in accounts):
                return accounts
            table_exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proxy_resources'").fetchone()
            available = []
            epoch = time.time()
            for account in accounts:
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
                    and (resource["cooldown_until"] is None or resource["cooldown_until"] <= epoch)):
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
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def record_route_event(self, account_id, event_type, *, route_mode='', reason='', operation_id='', shared_exit_key=''):
        """Append only redacted route telemetry; never accept credentials or URLs."""
        import re
        reason = re.sub(r'(?i)(password|token|cookie|secret|authorization)=[^\s]+', r'\1=[redacted]', str(reason or ''))[:500]
        with self._lock, self._connect() as conn:
            conn.execute('INSERT INTO crawler_route_events(account_id,event_type,route_mode,reason,operation_id,shared_exit_key,created_at) VALUES(?,?,?,?,?,?,?)',
                         (account_id, str(event_type)[:80], str(route_mode)[:40], reason, str(operation_id)[:100], str(shared_exit_key)[:120], time.time()))

    def set_route_state(self, account_id, *, state, route_mode=None, paused_until=None, pause_reason=None, shared_exit_key=None, generation=None):
        allowed = {'active', 'transport_blocked', 'challenge_paused', 'manual_required', 'draining', 'drain_timeout'}
        if state not in allowed: raise ValueError('ROUTE_STATE_INVALID')
        updates = {'route_state': state}
        if route_mode is not None: updates['route_mode'] = str(route_mode)
        if paused_until is not None: updates['paused_until'] = float(paused_until)
        if pause_reason is not None: updates['pause_reason'] = str(pause_reason)[:200]
        if shared_exit_key is not None: updates['shared_exit_key'] = str(shared_exit_key)[:120]
        if generation is not None: updates['shared_exit_generation'] = int(generation)
        with self._lock, self._connect() as conn:
            sets = ','.join(f'{k}=?' for k in updates)
            cur = conn.execute(f'UPDATE crawler_accounts SET {sets},updated_at=? WHERE id=?', (*updates.values(), datetime.now().isoformat(timespec='seconds'), account_id))
            if not cur.rowcount: return None
        return self.get(account_id)

    def select_fair(self, platform, *, exclude_ids=(), skip_limit=3):
        candidates = self.available_accounts(platform, exclude_ids=exclude_ids)
        if not candidates: return None
        candidates.sort(key=lambda a: (bool(a.get('last_used_at')), a.get('last_used_at') or '', a['id']))
        forced = [a for a in candidates if a.get('skip_count', 0) >= skip_limit]
        return (forced[0] if forced else candidates[0])


crawler_account_store = CrawlerAccountStore()
