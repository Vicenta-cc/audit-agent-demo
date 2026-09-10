from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .config import settings


SUPPORTED_CRAWLER_ACCOUNT_PLATFORMS = {"xhs", "dy", "ks"}
CRAWLER_ACCOUNT_STATUSES = {"active", "login_required", "expired", "disabled"}


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
            return [self._row_to_account(row) for row in rows]

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
            return self._row_to_account(row) if row else None

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
    ) -> dict:
        normalized_platform = self._validate_platform(platform)
        normalized_name = self._validate_display_name(display_name)
        normalized_platform_account_id = str(platform_account_id or "").strip()
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
                    id, platform, display_name, platform_account_id, status,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'login_required', ?, ?)
                """,
                (
                    account_id,
                    normalized_platform,
                    normalized_name,
                    normalized_platform_account_id,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM crawler_accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
            return self._row_to_account(row)

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
            return self._row_to_account(row)

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
                    status = 'active', last_validated_at = ?, last_error = '', updated_at = ?
                WHERE id = ?
                """,
                (normalized_ciphertext, now, now, now, account_id),
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
            return self._row_to_account(row)

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
                SET status = 'expired', last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(message or "账号登录态已失效")[:500], now, account_id),
            )

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

    def _row_to_account(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "platform": row["platform"],
            "display_name": row["display_name"],
            "platform_account_id": row["platform_account_id"] or "",
            "status": row["status"],
            "last_validated_at": row["last_validated_at"] or "",
            "last_used_at": row["last_used_at"] or "",
            "last_error": row["last_error"] or "",
            "has_auth_state": bool(row["auth_state_ciphertext"]),
            "auth_state_updated_at": row["auth_state_updated_at"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


crawler_account_store = CrawlerAccountStore()
