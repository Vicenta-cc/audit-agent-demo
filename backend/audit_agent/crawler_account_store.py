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
    if account.get('route_mode') == 'direct_degraded':
        return True
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
                ("validated_direct_generation", "INTEGER NOT NULL DEFAULT 0"),
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
            CREATE TABLE IF NOT EXISTS crawler_manual_recovery_operations (
                login_operation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                owner_id TEXT NOT NULL, claim TEXT NOT NULL UNIQUE,
                expected_identity TEXT NOT NULL, exit_key TEXT NOT NULL DEFAULT '', exit_generation INTEGER NOT NULL DEFAULT 0, cooldown_until REAL NOT NULL DEFAULT 0, state TEXT NOT NULL,
                result TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS crawler_exit_refreshes (
                account_id TEXT PRIMARY KEY, claim TEXT NOT NULL,
                claimed_at REAL NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS crawler_route_admissions (
                operation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                exit_key TEXT NOT NULL, generation INTEGER NOT NULL,
                state TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL);
        """)

        from .crawler_browser_migration import ensure_migration_schema
        ensure_migration_schema(conn)

        manual_columns={row[1] for row in conn.execute('PRAGMA table_info(crawler_manual_recovery_operations)')}
        for name in ('initial_login','risk_recorded'):
            if name not in manual_columns:
                conn.execute(f'ALTER TABLE crawler_manual_recovery_operations ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
        for name,definition in (('cooldown_until','REAL NOT NULL DEFAULT 0'),('exit_key',"TEXT NOT NULL DEFAULT ''"),('exit_generation','INTEGER NOT NULL DEFAULT 0')):
            if name not in manual_columns:
                conn.execute(f'ALTER TABLE crawler_manual_recovery_operations ADD COLUMN {name} {definition}')

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
            if normalized_platform == "dy":
                conn.execute("UPDATE crawler_accounts SET profile_engine='camoufox',profile_generation=? WHERE id=?",
                             (uuid4().hex, account_id))
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
        proposed = str(until)
        proposed_time = datetime.fromisoformat(proposed).timestamp()
        with self._lock, self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT cooldown_until FROM crawler_accounts WHERE id=?', (account_id,)).fetchone()
            if row is None:
                return
            existing = row['cooldown_until']
            if existing:
                try:
                    if datetime.fromisoformat(existing).timestamp() >= proposed_time:
                        proposed = existing
                except (TypeError, ValueError, OverflowError):
                    # An invalid existing fence is not proof that it expired.
                    proposed = existing
            conn.execute(
                "UPDATE crawler_accounts SET last_error=?, "
                "failure_kind=CASE WHEN failure_kind='verify' THEN failure_kind ELSE ? END, "
                "cooldown_until=?, updated_at=? WHERE id=?",
                (str(message or "账号进入冷却")[:500], str(failure_kind), proposed,
                 datetime.now().isoformat(timespec="seconds"), account_id),
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
        if platform == 'dy' and settings.crawler_browser_maintenance:
            return []
        now = datetime.now().isoformat(timespec="seconds")
        excluded = tuple(str(value) for value in exclude_ids if str(value))
        placeholders = ",".join("?" for _ in excluded)
        exclusion = f"AND id NOT IN ({placeholders}) " if excluded else ""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM crawler_accounts WHERE platform=? AND status='active' "
                "AND COALESCE(auth_state_ciphertext,'')<>'' AND route_state='active' AND (paused_until IS NULL OR paused_until<=strftime('%s','now')) "
                "AND (platform<>'dy' OR (profile_engine='camoufox' AND profile_generation<>'' "
                "AND auth_source_generation=profile_generation AND NOT EXISTS "
                "(SELECT 1 FROM crawler_browser_migrations m WHERE m.account_id=crawler_accounts.id AND m.phase<>'COMMITTED'))) "
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
                    if settings.crawler_safety_enabled and settings.crawler_direct_degrade_enabled and account['validated_direct_generation'] == account['shared_exit_generation'] and self._exit_allowed(conn, account["shared_exit_key"], account["shared_exit_generation"]):
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
                    and (not settings.crawler_safety_enabled or (resource['observed_exit_at'] is not None
                        and 0 <= epoch-resource['observed_exit_at'] <= settings.crawler_exit_observation_ttl
                        and self._exit_allowed(conn, resource["shared_exit_key"], resource["shared_exit_generation"])))):
                    available.append(account)
            return available

    def degraded_candidates(self, platform):
        """Transport evidence only. ACL and Profile/capacity are caller-owned."""
        if platform!='dy' or not settings.crawler_safety_enabled or not settings.crawler_direct_degrade_enabled:
            return []
        with self._connect() as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='proxy_resources'").fetchone():
                return []
            rows=conn.execute("SELECT a.*,p.stop_reason AS transport_error,p.expires_at AS proxy_expires FROM crawler_accounts a JOIN proxy_resources p ON p.id=a.proxy_id WHERE a.platform='dy' AND a.status='active' AND a.proxy_required=0 AND a.route_mode='proxy_preferred' AND a.route_state IN ('active','transport_blocked') AND COALESCE(a.auth_state_ciphertext,'')<>'' AND a.pause_reason='' AND (a.paused_until IS NULL OR a.paused_until<=?) AND (a.cooldown_until IS NULL OR a.cooldown_until<=?)",(time.time(),datetime.now().isoformat(timespec='seconds'))).fetchall()
            result=[]
            for row in rows:
                code=row['transport_error']
                if not code and row['proxy_expires'] is not None and row['proxy_expires']<=time.time():
                    code='PROXY_EXPIRED'
                if code not in {'PROXY_EXPIRED','PROXY_TRANSPORT_FAILED','PROXY_CONNECTION_REFUSED','PROXY_AUTH_FAILED'}:
                    continue
                value=self._row_to_account(row,connection=conn)
                value['transport_error']=code
                result.append(value)
            return sorted(result,key=self.fair_key)

    def _stale_route_candidate(self, conn, row):
        now=time.time()
        if (row['platform']!='dy' or row['status']!='active' or row['route_state']!='active'
                or row['pause_reason'] or not row['auth_state_ciphertext']
                or (row['paused_until'] is not None and row['paused_until']>now)):
            return None
        try:
            if row['cooldown_until'] and datetime.fromisoformat(row['cooldown_until'])>datetime.now(): return None
        except (ValueError,TypeError): return None
        if row['route_mode']=='direct_degraded':
            if not settings.crawler_direct_degrade_enabled: return None
            if row['validated_direct_generation']!=row['shared_exit_generation']: return None
            key,generation=row['shared_exit_key'],row['shared_exit_generation']
            sample_at=None
        else:
            if not row['proxy_id'] or not conn.execute("SELECT 1 FROM sqlite_master WHERE name='proxy_resources'").fetchone(): return None
            proxy=conn.execute('SELECT * FROM proxy_resources WHERE id=?',(row['proxy_id'],)).fetchone()
            if (not proxy or proxy['status']!='active' or proxy['stop_reason'] or proxy['expires_at'] is None
                    or proxy['expires_at']<=now or proxy['pause_reason'] or (proxy['paused_until'] is not None and proxy['paused_until']>now)
                    or (proxy['cooldown_until'] is not None and proxy['cooldown_until']>now)
                    or row['validated_proxy_binding_revision']!=row['binding_revision']
                    or row['validated_proxy_config_revision']!=proxy['config_revision']): return None
            key,generation=proxy['shared_exit_key'],proxy['shared_exit_generation']
            sample_at=proxy['observed_exit_at']
        observed=conn.execute('SELECT * FROM crawler_shared_exits WHERE exit_key=?',(key,)).fetchone()
        if not observed:
            # Additive migration may have confirmed legacy proxy samples before
            # the canonical table existed. A bounded echo can establish it;
            # such metadata never authorizes business admission on its own.
            if row['route_mode']=='direct_degraded' or sample_at is None or sample_at>now or generation<1:
                return None
            try:
                if self._exit_key(key)!=key or proxy['observed_exit_ip']!=key: return None
            except ValueError: return None
            if conn.execute("SELECT 1 FROM proxy_resources WHERE shared_exit_key=? AND (pause_reason<>'' OR paused_until>?)",(key,now)).fetchone():
                return None
            return self._row_to_account(row,connection=conn)
        if (observed['generation']!=generation or observed['pause_reason']
                or (observed['paused_until'] is not None and observed['paused_until']>now)):
            return None
        sample_at=observed['observed_at'] if sample_at is None else sample_at
        if sample_at>now or observed['observed_at']>now: return None
        if now-sample_at<=settings.crawler_exit_observation_ttl and now-observed['observed_at']<=settings.crawler_exit_observation_ttl:
            return None
        return self._row_to_account(row,connection=conn)

    def stale_route_candidates(self, platform):
        """Pure metadata eligibility; dispatch performs the bounded echo I/O."""
        if platform!='dy' or not settings.crawler_safety_enabled: return []
        with self._connect() as conn:
            candidates=[self._stale_route_candidate(conn,row) for row in conn.execute('SELECT * FROM crawler_accounts WHERE platform=?',(platform,))]
            return [value for value in candidates if value is not None]

    def claim_exit_refresh(self, account_id):
        with self._lock,self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            if row and row['platform'] == 'dy' and (settings.crawler_browser_maintenance or
                    conn.execute("SELECT 1 FROM crawler_browser_migrations WHERE account_id=? AND phase<>'COMMITTED'", (account_id,)).fetchone()):
                return None
            if not row or self._stale_route_candidate(conn,row) is None: return None
            prior=conn.execute('SELECT * FROM crawler_exit_refreshes WHERE account_id=?',(account_id,)).fetchone()
            if prior and (prior['state']=='claimed' or time.time()-prior['claimed_at']<settings.crawler_proxy_probe_interval): return None
            if conn.execute("SELECT 1 FROM crawler_route_admissions WHERE account_id=? AND state='active'",(account_id,)).fetchone(): return None
            claim=uuid4().hex
            conn.execute("INSERT INTO crawler_exit_refreshes(account_id,claim,claimed_at,state) VALUES(?,?,?,'claimed') ON CONFLICT(account_id) DO UPDATE SET claim=excluded.claim,claimed_at=excluded.claimed_at,state=excluded.state",(account_id,claim,time.time()))
            return claim

    def finish_exit_refresh(self, account_id, claim, success):
        if type(success) is not bool: raise ValueError('ROUTE_REFRESH_INVALID')
        with self._connect() as conn:
            conn.execute("UPDATE crawler_exit_refreshes SET state=? WHERE account_id=? AND claim=? AND state='claimed'",('success' if success else 'uncertain',account_id,claim))

    def next_available(self, platform: str, *, exclude_id: str = "") -> dict | None:
        if platform == 'dy' and settings.crawler_safety_enabled:
            return self.select_fair(platform, exclude_ids=(exclude_id,))
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
            "profile_engine": row["profile_engine"],
            "profile_generation": row["profile_generation"],
            "auth_source_generation": row["auth_source_generation"],
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
            "validated_direct_generation": int(row['validated_direct_generation'] or 0),
            "recovery_state": row["recovery_state"] or '',
            "recovery_incident": row["recovery_incident"] or '',
            "recovery_claim": row["recovery_claim"] or '',
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    ROUTE_MODES = frozenset({'proxy_required', 'proxy_preferred', 'direct_degraded', 'legacy_direct'})
    ROUTE_STATES = frozenset({'active', 'transport_blocked', 'challenge_paused', 'manual_required', 'draining', 'drain_timeout', 'recovery_check'})
    EVENT_TYPES = frozenset({'route_changed', 'transport_blocked', 'challenge_paused', 'recovery_claimed', 'recovery_succeeded', 'manual_required', 'route_admitted', 'route_ended', 'manual_recovery_claimed', 'manual_recovery_succeeded', 'manual_verified_waiting'})
    REASONS = frozenset({'', 'ACCOUNT_CHALLENGE', 'AUTH_INVALID', 'PLATFORM_429', 'PROXY_EXPIRED', 'PROXY_TRANSPORT_FAILED', 'PROXY_CONNECTION_REFUSED', 'PROXY_AUTH_FAILED', 'POOL_EXHAUSTED', 'RECOVERY_SUCCESS', 'RECOVERY_UNCERTAIN', 'DRAIN_TIMEOUT', 'LOGIN_REVALIDATION_REQUIRED', 'MANUAL_RECOVERY_SUCCESS', 'MANUAL_IDENTITY_MISMATCH', 'MANUAL_RECOVERY_FAILED', 'MANUAL_RECOVERY_CANCELLED'})

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
        if not isinstance(value,str):
            raise ValueError('ROUTE_EXIT_INVALID')
        try:
            return str(ipaddress.ip_address(value))
        except (ValueError, TypeError):
            raise ValueError('ROUTE_EXIT_INVALID') from None

    def _exit_allowed(self, conn, key, generation, *, recovery=False, manual=False):
        row = conn.execute('SELECT * FROM crawler_shared_exits WHERE exit_key=?', (key,)).fetchone()
        return bool(row and row['generation'] == generation
            and 0 <= time.time() - row['observed_at'] <= settings.crawler_exit_observation_ttl
            and (recovery or not row['pause_reason'])
            and (manual or row['paused_until'] is None or row['paused_until'] <= time.time()))

    def observe_shared_exit(self, ip, *, observed_at=None, service_exit=False, connection=None):
        key = self._exit_key(ip)
        at = time.time() if observed_at is None else self._epoch(observed_at)
        if at > time.time() + 5 or type(service_exit) is not bool:
            raise ValueError('ROUTE_OBSERVATION_INVALID')
        def update(conn):
            row = conn.execute('SELECT * FROM crawler_shared_exits WHERE exit_key=?', (key,)).fetchone()
            if row and at < row['observed_at']:
                raise ValueError('PROXY_STALE_OBSERVATION')
            generation = row['generation'] if row else conn.execute('SELECT COALESCE(MAX(generation),0)+1 FROM crawler_shared_exits').fetchone()[0]
            conn.execute("INSERT INTO crawler_shared_exits(exit_key,generation,observed_at,service_exit) VALUES(?,?,?,?) ON CONFLICT(exit_key) DO UPDATE SET observed_at=excluded.observed_at,service_exit=MAX(service_exit,excluded.service_exit)", (key,generation,at,int(service_exit)))
            return {'observed_exit_ip': key, 'observed_exit_at': at, 'shared_exit_key': key, 'shared_exit_generation': generation}
        if connection is not None:
            return update(connection)
        with self._lock, self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return update(conn)

    def consume_cached_exit(self, ip, *, observed_at, service_exit=False):
        """Consume a fresh resource sample without rolling back shared observations."""
        key=self._exit_key(ip);at=self._epoch(observed_at)
        if type(service_exit) is not bool or not 0 <= time.time()-at <= settings.crawler_exit_observation_ttl:
            raise ValueError('PROXY_STALE_OBSERVATION')
        with self._lock,self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM crawler_shared_exits WHERE exit_key=?',(key,)).fetchone()
            if row and row['observed_at']>at:
                return {'observed_exit_ip':key,'observed_exit_at':row['observed_at'],'shared_exit_key':key,'shared_exit_generation':row['generation']}
            return self.observe_shared_exit(key,observed_at=at,service_exit=service_exit,connection=conn)

    def get_direct_route(self, account_id, *, allow_stale_recovery=False):
        if type(allow_stale_recovery) is not bool:
            raise ValueError('ROUTE_RECOVERY_INVALID')
        with self._connect() as conn:
            account=conn.execute('SELECT * FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            if not account: return None
            if account['route_mode']=='direct_degraded':
                row=conn.execute('SELECT * FROM crawler_shared_exits WHERE service_exit=1 AND exit_key=? AND generation=?',(account['shared_exit_key'],account['shared_exit_generation'])).fetchone()
            else:
                row=conn.execute('SELECT * FROM crawler_shared_exits WHERE service_exit=1 ORDER BY observed_at DESC LIMIT 1').fetchone()
            recovery=account['recovery_state'] in {'claimed','manual_claimed'} and account['route_state']=='recovery_check'
            manual=account['recovery_state']=='manual_claimed' and self._manual_claim_valid(conn,account_id,account['recovery_claim'])
            fresh=bool(row and self._exit_allowed(conn,row['exit_key'],row['generation'],recovery=recovery,manual=manual))
            probe_only=bool(row and allow_stale_recovery and recovery and row['observed_at']<=time.time()
                and (manual or row['paused_until'] is None or row['paused_until']<=time.time()))
            if not fresh and not probe_only:
                return None
            return dict(observed_exit_ip=row['exit_key'],observed_exit_at=row['observed_at'],shared_exit_key=row['exit_key'],shared_exit_generation=row['generation'])

    def mark_direct_validated(self, account_id, generation, *, connection=None):
        generation=self._generation(generation)
        def write(conn):
            row=conn.execute('SELECT * FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            manual=bool(row and row['recovery_state']=='manual_claimed' and self._manual_claim_valid(conn,account_id,row['recovery_claim']))
            if (not row or row['route_mode']!='direct_degraded' or row['shared_exit_generation']!=generation
                    or not self._exit_allowed(conn,row['shared_exit_key'],generation,recovery=manual,manual=manual)):
                raise ValueError('ROUTE_GENERATION_STALE')
            conn.execute('UPDATE crawler_accounts SET validated_direct_generation=? WHERE id=?',(generation,account_id))
        if connection is not None:
            if not connection.in_transaction: raise ValueError('MANUAL_TRANSACTION_REQUIRED')
            return write(connection)
        with self._lock,self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return write(conn)

    def admit_route(self, account_id, observed_exit, generation, operation_id, *, recovery_claim=None, login_bootstrap=False):
        if type(login_bootstrap) is not bool:
            raise ValueError('ROUTE_BOOTSTRAP_INVALID')
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
            recovery = bool(account and recovery_claim and account['recovery_claim']==recovery_claim and account['recovery_state'] in {'claimed','manual_claimed'})
            manual = bool(recovery and account['recovery_state']=='manual_claimed' and self._manual_claim_valid(conn,account_id,recovery_claim,key=key,generation=generation))
            if recovery and (key!=account['shared_exit_key'] or generation<account['shared_exit_generation'] or account['recovery_state']=='manual_claimed' and not manual):
                raise ValueError('ACCOUNT_CHALLENGE')
            if not account or (account['status']!='active' and not ((login_bootstrap or manual) and account['status'] in {'login_required','expired'})) or (account['route_state']!='active' and not recovery and not (login_bootstrap and account['route_state']=='transport_blocked')) or (account['paused_until'] and account['paused_until']>time.time() and not manual) or not self._exit_allowed(conn,key,generation,recovery=recovery,manual=manual):
                raise ValueError('ACCOUNT_CHALLENGE')
            if not login_bootstrap and not manual:
                if not account['auth_state_ciphertext']:
                    raise ValueError('AUTH_INVALID')
                try:
                    cooling=bool(account['cooldown_until']) and datetime.fromisoformat(account['cooldown_until'])>datetime.now()
                except (ValueError,TypeError):
                    cooling=True
                if cooling:
                    raise ValueError('ACCOUNT_CHALLENGE')
            if account['route_mode']=='direct_degraded' and account['validated_direct_generation']!=generation and not (login_bootstrap or recovery):
                raise ValueError('LOGIN_REVALIDATION_REQUIRED')
            conn.execute('INSERT INTO crawler_route_admissions(operation_id,account_id,exit_key,generation,created_at) VALUES(?,?,?,?,?)',(operation_id,account_id,key,generation,time.time()))
            conn.execute('UPDATE crawler_accounts SET shared_exit_key=?,shared_exit_generation=? WHERE id=?',(key,generation,account_id))
            conn.execute("INSERT INTO crawler_route_events(account_id,event_type,route_mode,operation_id,shared_exit_key,created_at) VALUES(?,'route_admitted',?,?,?,?)",(account_id,account['route_mode'],operation_id,key,time.time()))
            return operation_id

    def release_route_admission(self, operation_id):
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("INSERT INTO crawler_route_events(account_id,event_type,operation_id,shared_exit_key,created_at) SELECT account_id,'route_ended',operation_id,exit_key,? FROM crawler_route_admissions WHERE operation_id=? AND state='active'",(time.time(),operation_id))
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
            until = max(until, row['paused_until'] or 0)
            key = row['shared_exit_key']
            if not key and row['proxy_id'] and conn.execute("SELECT 1 FROM sqlite_master WHERE name='proxy_resources'").fetchone():
                proxy = conn.execute('SELECT shared_exit_key FROM proxy_resources WHERE id=?',(row['proxy_id'],)).fetchone()
                key = proxy['shared_exit_key'] if proxy else ''
            in_check=row['recovery_state'] in {'claimed','manual_claimed'} and row['route_state']=='recovery_check'
            incident = row['recovery_incident'] if in_check and row['recovery_incident'] else uuid4().hex
            if in_check:
                conn.execute('UPDATE crawler_accounts SET paused_until=?,pause_reason=?,recovery_incident=?,shared_exit_key=? WHERE id=?',(max(until,row['paused_until'] or 0),reason,incident,key,account_id))
            elif row['route_state'] == 'manual_required':
                conn.execute('UPDATE crawler_accounts SET paused_until=?,pause_reason=?,shared_exit_key=? WHERE id=?',
                    (until, reason, key, account_id))
            else:
                conn.execute("UPDATE crawler_accounts SET route_state='challenge_paused',paused_until=?,pause_reason=?,recovery_incident=?,recovery_state='pending',recovery_claim='',shared_exit_key=? WHERE id=?",(until,reason,incident,key,account_id))
            if key:
                conn.execute("UPDATE crawler_shared_exits SET paused_until=MAX(COALESCE(paused_until,0),?),pause_reason=? WHERE exit_key=?",(until,reason,key))
            conn.execute("INSERT INTO crawler_route_events(account_id,event_type,reason,created_at) VALUES(?,'challenge_paused',?,?)",(account_id,reason,time.time()))
            return incident

    def claim_recovery(self, account_id):
        with self._lock, self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            if row and row['platform'] == 'dy' and (settings.crawler_browser_maintenance or
                    conn.execute("SELECT 1 FROM crawler_browser_migrations WHERE account_id=? AND phase<>'COMMITTED'", (account_id,)).fetchone()):
                return None
            if not row or row['recovery_state']!='pending' or row['route_state']!='challenge_paused' or row['paused_until'] is None or row['paused_until']>time.time():
                return None
            claim=uuid4().hex
            conn.execute("UPDATE crawler_accounts SET recovery_state='claimed',recovery_claim=?,route_state='recovery_check' WHERE id=?",(claim,account_id))
            conn.execute("INSERT INTO crawler_route_events(account_id,event_type,route_mode,operation_id,shared_exit_key,created_at) VALUES(?,'recovery_claimed',?,?,?,?)",(account_id,row['route_mode'],claim,row['shared_exit_key'],time.time()))
            return claim

    def finish_recovery(self, account_id, claim, result):
        if result not in {'success','challenge','uncertain'}:
            raise ValueError('RECOVERY_RESULT_INVALID')
        with self._lock, self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT * FROM crawler_accounts WHERE id=? AND recovery_claim=? AND recovery_state='claimed'",(account_id,claim)).fetchone()
            if not row:
                raise ValueError('RECOVERY_CLAIM_INVALID')
            if result=='success' and row['paused_until'] is not None and row['paused_until']>time.time():
                result='uncertain'
            state='active' if result=='success' else 'manual_required'
            conn.execute('UPDATE crawler_accounts SET recovery_state=?,route_state=?,pause_reason=?,paused_until=? WHERE id=?',(result,state,'' if result=='success' else 'RECOVERY_UNCERTAIN',None if result=='success' else row['paused_until'],account_id))
            conn.execute('INSERT INTO crawler_route_events(account_id,event_type,route_mode,reason,operation_id,shared_exit_key,created_at) VALUES(?,?,?,?,?,?,?)',(account_id,'recovery_succeeded' if result=='success' else 'manual_required',row['route_mode'],'RECOVERY_SUCCESS' if result=='success' else 'ACCOUNT_CHALLENGE' if result=='challenge' else 'RECOVERY_UNCERTAIN',claim,row['shared_exit_key'],time.time()))
            if result=='success' and row['shared_exit_key'] and not conn.execute("SELECT 1 FROM crawler_accounts WHERE shared_exit_key=? AND id<>? AND route_state IN ('challenge_paused','recovery_check','manual_required')",(row['shared_exit_key'],account_id)).fetchone():
                conn.execute("UPDATE crawler_shared_exits SET pause_reason='',paused_until=NULL WHERE exit_key=?",(row['shared_exit_key'],))

    @staticmethod
    def _manual_claim_valid(conn, account_id, claim, *, key=None, generation=None):
        row=conn.execute("SELECT * FROM crawler_manual_recovery_operations WHERE account_id=? AND claim=? AND state='claimed'",(account_id,claim)).fetchone() if claim else None
        return bool(row and (key is None or row['exit_key']==key)
            and (generation is None or generation>=row['exit_generation']))

    def release_admissions_for_stopped_account(self, account_id):
        """Coordinator-only: independent account and Profile FDs prove child stop.

        This method does not infer process death from time and never releases
        incident/exit pauses. The caller must hold both newly acquired fences.
        """
        with self._lock,self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("UPDATE crawler_exit_refreshes SET state='uncertain' WHERE account_id=? AND state='claimed'",(account_id,))
            conn.execute("INSERT INTO crawler_route_events(account_id,event_type,operation_id,shared_exit_key,created_at) SELECT account_id,'route_ended',operation_id,exit_key,? FROM crawler_route_admissions WHERE account_id=? AND state='active'",(time.time(),account_id))
            ended=conn.execute("UPDATE crawler_route_admissions SET state='ended' WHERE account_id=? AND state='active'",(account_id,)).rowcount
            row=conn.execute('SELECT * FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            if row and row['recovery_state'] in {'claimed','manual_claimed'}:
                conn.execute("UPDATE crawler_manual_recovery_operations SET state='finished',result='uncertain' WHERE account_id=? AND state='claimed'",(account_id,))
                conn.execute("UPDATE crawler_accounts SET route_state='manual_required',recovery_state='uncertain',pause_reason='RECOVERY_UNCERTAIN' WHERE id=?",(account_id,))
                conn.execute("INSERT INTO crawler_route_events(account_id,event_type,route_mode,reason,shared_exit_key,created_at) VALUES(?,'manual_required',?,'RECOVERY_UNCERTAIN',?,?)",(account_id,row['route_mode'],row['shared_exit_key'],time.time()))
            return ended

    def begin_manual_recovery(self, account_id, login_operation_id, *, owner_id, allow_active=False, observed_exit=None, generation=None):
        """Explicit authenticated login action; caller holds the Profile FD lease."""
        if (not isinstance(login_operation_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',login_operation_id)
                or not isinstance(owner_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',owner_id)):
            raise ValueError('MANUAL_OPERATION_INVALID')
        with self._lock,self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            prior=conn.execute('SELECT * FROM crawler_manual_recovery_operations WHERE login_operation_id=?',(login_operation_id,)).fetchone()
            if prior:
                if prior['account_id']!=account_id or prior['owner_id']!=owner_id: raise ValueError('MANUAL_OPERATION_CONFLICT')
                return None
            row=conn.execute('SELECT * FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            if type(allow_active) is not bool:
                raise ValueError('MANUAL_OPERATION_INVALID')
            if not row or row['route_state'] not in ({'active','transport_blocked','manual_required','challenge_paused','recovery_check'} if allow_active else {'manual_required','challenge_paused','recovery_check'}):
                return None
            if row['recovery_state']=='manual_claimed': return None
            if conn.execute("SELECT 1 FROM crawler_route_admissions WHERE account_id=? AND state='active'",(account_id,)).fetchone():
                return None
            bootstrap=bool(allow_active and row['route_state'] in {'active','transport_blocked'} and not row['pause_reason'])
            if bootstrap:
                key=self._exit_key(observed_exit);gen=self._generation(generation)
                if not self._exit_allowed(conn,key,gen):return None
                conn.execute('UPDATE crawler_accounts SET shared_exit_key=?,shared_exit_generation=? WHERE id=?',(key,gen,account_id))
                row=conn.execute('SELECT * FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            claim=uuid4().hex
            cooldown=float(row['paused_until'] or 0)
            try:
                if row['cooldown_until']: cooldown=max(cooldown,datetime.fromisoformat(row['cooldown_until']).timestamp())
            except (ValueError,TypeError): return None
            conn.execute("INSERT INTO crawler_manual_recovery_operations(login_operation_id,account_id,owner_id,claim,expected_identity,exit_key,exit_generation,cooldown_until,state,created_at) VALUES(?,?,?,?,?,?,?,?,'claimed',?)",(login_operation_id,account_id,owner_id,claim,row['platform_account_id'] or '',row['shared_exit_key'],row['shared_exit_generation'],cooldown,time.time()))
            exit_pause=conn.execute('SELECT pause_reason FROM crawler_shared_exits WHERE exit_key=?',(row['shared_exit_key'],)).fetchone()
            risk_recorded=bool(row['recovery_incident'] and row['risk_count'] and row['paused_until'] and exit_pause and exit_pause[0]=='ACCOUNT_CHALLENGE')
            conn.execute('UPDATE crawler_manual_recovery_operations SET initial_login=?,risk_recorded=? WHERE login_operation_id=?',(int(not row['platform_account_id']),int(risk_recorded),login_operation_id))
            conn.execute("UPDATE crawler_accounts SET route_state='recovery_check',recovery_state='manual_claimed',recovery_claim=? WHERE id=?",(claim,account_id))
            conn.execute("INSERT INTO crawler_route_events(account_id,event_type,route_mode,operation_id,shared_exit_key,created_at) VALUES(?,'manual_recovery_claimed',?,?,?,?)",(account_id,row['route_mode'],login_operation_id,row['shared_exit_key'],time.time()))
            return claim

    def _release_recovered_exit(self, conn, account_id, key):
        if key and not conn.execute("SELECT 1 FROM crawler_accounts WHERE shared_exit_key=? AND id<>? AND route_state IN ('challenge_paused','recovery_check','manual_required')",(key,account_id)).fetchone():
            conn.execute("UPDATE crawler_shared_exits SET pause_reason='',paused_until=NULL WHERE exit_key=?",(key,))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='proxy_resources'").fetchone():
                conn.execute("UPDATE proxy_resources SET pause_reason='',paused_until=NULL WHERE shared_exit_key=? AND pause_reason='ACCOUNT_CHALLENGE'",(key,))

    def finish_manual_recovery(self, account_id, login_operation_id, claim, result, *, owner_id, verified_identity='', connection=None):
        """Trusted auth save and incident settlement can share one transaction."""
        if result not in {'success','challenge','failed','cancelled','uncertain'} or not isinstance(verified_identity,str):
            raise ValueError('MANUAL_RESULT_INVALID')
        def write(conn):
            operation=conn.execute("SELECT * FROM crawler_manual_recovery_operations WHERE login_operation_id=? AND account_id=? AND owner_id=? AND claim=? AND state='claimed'",(login_operation_id,account_id,owner_id,claim)).fetchone()
            row=conn.execute("SELECT * FROM crawler_accounts WHERE id=? AND recovery_claim=? AND recovery_state='manual_claimed'",(account_id,claim)).fetchone()
            if not operation or not row: return False
            verified=bool(result=='success' and verified_identity and (verified_identity==operation['expected_identity'] if operation['expected_identity'] else operation['initial_login'])
                and row['platform_account_id']==verified_identity and row['auth_state_ciphertext']
                and row['shared_exit_key']==operation['exit_key'] and row['shared_exit_generation']>=operation['exit_generation'])
            if verified and not operation['expected_identity']:
                conn.execute('UPDATE crawler_manual_recovery_operations SET expected_identity=? WHERE login_operation_id=?',(verified_identity,login_operation_id))
            cooled=operation['cooldown_until']<=time.time() and (row['paused_until'] is None or row['paused_until']<=time.time())
            try:
                cooled=cooled and (not row['cooldown_until'] or datetime.fromisoformat(row['cooldown_until'])<=datetime.now())
            except (ValueError,TypeError): cooled=False
            if verified and cooled:
                state,recovery,operation_state,code='active','manual_success','finished','MANUAL_RECOVERY_SUCCESS'
                conn.execute("UPDATE crawler_accounts SET status='active',route_state=?,recovery_state=?,paused_until=NULL,pause_reason='' WHERE id=?",(state,recovery,account_id))
                self._release_recovered_exit(conn,account_id,operation['exit_key'])
            elif verified:
                state,recovery,operation_state,code='challenge_paused','manual_verified_waiting','verified_waiting','MANUAL_RECOVERY_SUCCESS'
                conn.execute('UPDATE crawler_accounts SET route_state=?,recovery_state=?,paused_until=MAX(COALESCE(paused_until,0),?) WHERE id=?',(state,recovery,operation['cooldown_until'],account_id))
            else:
                failed_result='identity_mismatch' if result=='success' else result
                state,recovery,operation_state='manual_required','manual_'+failed_result,'finished'
                code='MANUAL_IDENTITY_MISMATCH' if result=='success' else 'MANUAL_RECOVERY_CANCELLED' if result=='cancelled' else 'MANUAL_RECOVERY_FAILED'
                conn.execute("UPDATE crawler_accounts SET route_state=?,recovery_state=?,pause_reason=?,paused_until=MAX(COALESCE(paused_until,0),?),platform_account_id=CASE WHEN ?<>'' THEN ? ELSE platform_account_id END WHERE id=?",(state,recovery,code,operation['cooldown_until'],operation['expected_identity'],operation['expected_identity'],account_id))
            conn.execute('UPDATE crawler_manual_recovery_operations SET state=?,result=? WHERE login_operation_id=?',(operation_state,'success' if verified else failed_result,login_operation_id))
            event='manual_recovery_succeeded' if verified and cooled else 'manual_verified_waiting' if verified else 'manual_required'
            conn.execute('INSERT INTO crawler_route_events(account_id,event_type,route_mode,reason,operation_id,shared_exit_key,created_at) VALUES(?,?,?,?,?,?,?)',(account_id,event,row['route_mode'],code,login_operation_id,row['shared_exit_key'],time.time()))
            return verified
        if connection is not None:
            if not connection.in_transaction: raise ValueError('MANUAL_TRANSACTION_REQUIRED')
            return write(connection)
        with self._lock,self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            return write(conn)

    def release_verified_manual(self, account_id):
        """Release a completed human check after cooldown, without new network I/O."""
        with self._lock,self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT * FROM crawler_accounts WHERE id=? AND recovery_state='manual_verified_waiting'",(account_id,)).fetchone()
            if not row or (row['paused_until'] is not None and row['paused_until']>time.time()): return False
            try:
                if row['cooldown_until'] and datetime.fromisoformat(row['cooldown_until'])>datetime.now(): return False
            except (ValueError,TypeError): return False
            operation=conn.execute("SELECT * FROM crawler_manual_recovery_operations WHERE account_id=? AND claim=? AND state='verified_waiting' AND result='success'",(account_id,row['recovery_claim'])).fetchone()
            if not operation or operation['cooldown_until']>time.time() or not operation['expected_identity'] or row['platform_account_id']!=operation['expected_identity'] or not row['auth_state_ciphertext'] or row['shared_exit_key']!=operation['exit_key'] or row['shared_exit_generation']<operation['exit_generation']: return False
            conn.execute("UPDATE crawler_accounts SET status='active',route_state='active',recovery_state='manual_success',paused_until=NULL,pause_reason='' WHERE id=?",(account_id,))
            conn.execute("UPDATE crawler_manual_recovery_operations SET state='finished' WHERE login_operation_id=?",(operation['login_operation_id'],))
            self._release_recovered_exit(conn,account_id,operation['exit_key'])
            return True

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
            old=conn.execute('SELECT shared_exit_key,shared_exit_generation FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            if not old: return None
            new_key=updates.get('shared_exit_key',old['shared_exit_key'])
            if new_key!=old['shared_exit_key']:
                if generation is None or not self._exit_allowed(conn,new_key,generation):
                    raise ValueError('ROUTE_EXIT_INVALID')
                if conn.execute("SELECT 1 FROM crawler_route_admissions WHERE account_id=? AND state='active'",(account_id,)).fetchone():
                    raise ValueError('ROUTE_BUSY')
            elif generation is not None and generation < old['shared_exit_generation']:
                raise ValueError('ROUTE_GENERATION_STALE')
            sets=','.join(f'{k}=?' for k in updates)
            conn.execute(f'UPDATE crawler_accounts SET {sets},updated_at=? WHERE id=?',(*updates.values(),datetime.now().isoformat(timespec='seconds'),account_id))
            fresh=conn.execute('SELECT route_mode,shared_exit_key FROM crawler_accounts WHERE id=?',(account_id,)).fetchone()
            conn.execute('INSERT INTO crawler_route_events(account_id,event_type,route_mode,reason,shared_exit_key,created_at) VALUES(?,?,?,?,?,?)',(account_id,'manual_required' if state=='manual_required' else 'transport_blocked' if state=='transport_blocked' else 'route_changed',fresh['route_mode'],pause_reason or '',fresh['shared_exit_key'],time.time()))
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
