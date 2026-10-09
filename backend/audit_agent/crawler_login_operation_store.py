"""Durable login results and non-replayable command intents in the account database."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from uuid import uuid4

from .auth_state_cipher import AuthStateCipher, auth_state_cipher
from .crawler_account_store import CrawlerAccountStore

TERMINAL_STAGES = {"committed", "failed", "cancelled", "expired", "recovered", "superseded"}
STAGES = TERMINAL_STAGES | {"created", "checking", "reset_authorized", "reset_done", "cleanup_authorized", "profile_ready", "recovery_required"}
COMMAND_KINDS = {"request_sms", "submit_sms", "refresh_qr", "refresh_challenge", "switch_to_sms", "manual_action"}


def _now():
    return datetime.now().isoformat(timespec="microseconds")


def _record(row):
    if row is None:
        return None
    value = dict(row)
    value["result"] = json.loads(value["result_json"]) if value.get("result_json") else None
    return value


class CrawlerLoginOperationStore:
    def __init__(self, account_store: CrawlerAccountStore, cipher: AuthStateCipher | None = None):
        self.accounts = account_store
        self.db_path = account_store.db_path
        self.cipher = cipher or auth_state_cipher
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS crawler_login_operations (
                    id TEXT PRIMARY KEY, account_id TEXT NOT NULL, owner_user_id TEXT NOT NULL,
                    application_session_id TEXT NOT NULL, request_key_hash TEXT NOT NULL,
                    owner_token_hash TEXT NOT NULL, engine TEXT NOT NULL, intent TEXT NOT NULL,
                    source_operation_id TEXT REFERENCES crawler_login_operations(id),
                    recovered_by_operation_id TEXT REFERENCES crawler_login_operations(id),
                    expected_platform_account_id TEXT NOT NULL DEFAULT '', expected_auth_revision INTEGER NOT NULL,
                    stage TEXT NOT NULL, credentials_reset INTEGER NOT NULL DEFAULT 0,
                    profile_authority_established INTEGER NOT NULL DEFAULT 0,
                    credentials_changed INTEGER,
                    request_fingerprint TEXT NOT NULL, verified_platform_account_id TEXT NOT NULL DEFAULT '',
                    result_json TEXT, error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_login_operation_request ON crawler_login_operations(owner_user_id, request_key_hash);
                CREATE INDEX IF NOT EXISTS idx_login_operation_account ON crawler_login_operations(account_id, created_at);
                CREATE TABLE IF NOT EXISTS crawler_login_commands (
                    operation_id TEXT NOT NULL REFERENCES crawler_login_operations(id), command_id TEXT NOT NULL,
                    kind TEXT NOT NULL, request_fingerprint TEXT NOT NULL, fingerprint_key_id TEXT NOT NULL,
                    application_session_id TEXT NOT NULL, controller_epoch INTEGER NOT NULL, sequence INTEGER NOT NULL,
                    execution_status TEXT NOT NULL, platform_status TEXT NOT NULL, result_json TEXT, error_code TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, dispatched_at TEXT, completed_at TEXT,
                    PRIMARY KEY(operation_id, command_id));
                CREATE INDEX IF NOT EXISTS idx_login_command_pending ON crawler_login_commands(operation_id, execution_status, updated_at);
            """)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(crawler_login_operations)")}
            if "credentials_changed" not in columns:
                conn.execute("ALTER TABLE crawler_login_operations ADD COLUMN credentials_changed INTEGER")

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def get(self, operation_id):
        with self._connect() as conn:
            return self._get(conn, operation_id)

    def _get(self, conn, operation_id):
        return _record(conn.execute("SELECT * FROM crawler_login_operations WHERE id=?", (operation_id,)).fetchone())

    def list(self, *, account_id=None, owner_user_id=None, stages=None):
        clauses, params = [], []
        for key, value in (("account_id", account_id), ("owner_user_id", owner_user_id)):
            if value is not None:
                clauses.append(f"{key}=?")
                params.append(value)
        if stages:
            clauses.append("stage IN (" + ",".join("?" for _ in stages) + ")")
            params.extend(stages)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            return [_record(row) for row in conn.execute("SELECT * FROM crawler_login_operations" + where + " ORDER BY created_at DESC", params)]

    def begin(self, *, account_id, owner_user_id, application_session_id, owner_token_hash,
              request_key_hash, request_fingerprint, intent="check_or_login", engine="v2", source_operation_id=None):
        if intent not in {"check", "reauth", "recover", "check_or_login"}:
            raise ValueError("INVALID_LOGIN_INTENT")
        if (intent == "recover") != bool(source_operation_id):
            raise ValueError("RECOVERY_SOURCE_REQUIRED")
        superseded = False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = _record(conn.execute("SELECT * FROM crawler_login_operations WHERE owner_user_id=? AND request_key_hash=?", (owner_user_id, request_key_hash)).fetchone())
            if old:
                if old["request_fingerprint"] != request_fingerprint or old["account_id"] != account_id or old["intent"] != intent:
                    raise ValueError("OPERATION_REQUEST_CONFLICT")
                return old
            account = self.accounts.get(account_id, connection=conn)
            if not account or account["platform"] != "dy":
                raise ValueError("DY_ACCOUNT_REQUIRED")
            if account["status"] == "disabled":
                raise ValueError("ACCOUNT_DISABLED")
            source = self._get(conn, source_operation_id) if source_operation_id else None
            if source_operation_id:
                if not source or source["account_id"] != account_id or source["owner_user_id"] != owner_user_id:
                    raise ValueError("RECOVERY_SOURCE_FORBIDDEN")
                if source["stage"] in {"committed", "recovered"}:
                    return self._get(conn, source["recovered_by_operation_id"]) if source["stage"] == "recovered" else source
                if source["stage"] != "recovery_required":
                    raise ValueError("RECOVERY_SOURCE_NOT_RECOVERABLE")
                if account["auth_state_revision"] != source["expected_auth_revision"] or account["platform_account_id"] != source["expected_platform_account_id"]:
                    conn.execute("UPDATE crawler_login_operations SET stage='superseded', error_code='RECOVERY_SOURCE_SUPERSEDED', updated_at=? WHERE id=?", (_now(), source_operation_id))
                    superseded = True
                elif conn.execute("SELECT id FROM crawler_login_operations WHERE source_operation_id=? AND stage NOT IN ('committed','recovered','failed','cancelled','expired','superseded','recovery_required')", (source_operation_id,)).fetchone():
                    raise ValueError("ACCOUNT_BUSY")
            if not superseded:
                operation_id, now = uuid4().hex, _now()
                conn.execute("""INSERT INTO crawler_login_operations(id,account_id,owner_user_id,application_session_id,
                    request_key_hash,owner_token_hash,engine,intent,source_operation_id,expected_platform_account_id,
                    expected_auth_revision,stage,request_fingerprint,credentials_reset,profile_authority_established,credentials_changed,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,'created',?,?,?,?,?,?)""",
                    (operation_id,account_id,owner_user_id,application_session_id,request_key_hash,owner_token_hash,engine,intent,
                     source_operation_id,account["platform_account_id"],account["auth_state_revision"],request_fingerprint,
                     source["credentials_reset"] if source else 0,source["profile_authority_established"] if source else 0,source["credentials_changed"] if source else None,now,now))
                result = self._get(conn, operation_id)
        if superseded:
            raise ValueError("RECOVERY_SOURCE_SUPERSEDED")
        return result

    def mark_stage(self, operation_id, stage, **fields):
        allowed = {"credentials_changed", "credentials_reset", "profile_authority_established", "verified_platform_account_id", "error_code", "result_json"}
        if stage not in STAGES or fields.keys() - allowed:
            raise ValueError("INVALID_OPERATION_UPDATE")
        if stage in {"committed", "recovered"}:
            raise ValueError("ATOMIC_COMMIT_REQUIRED")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            op = self._get(conn, operation_id)
            if not op:
                raise ValueError("OPERATION_EXPIRED")
            if op["stage"] in TERMINAL_STAGES:
                return op
            values = {"stage": stage, "updated_at": _now(), **fields}
            if stage in TERMINAL_STAGES:
                values["completed_at"] = _now()
            conn.execute("UPDATE crawler_login_operations SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?", (*values.values(),operation_id))
            return self._get(conn, operation_id)

    def authorize_reset(self, operation_id):
        return self.prepare_profile(operation_id, reset=True)

    def prepare_profile(self, operation_id, *, reset=False):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            op = self._get(conn, operation_id)
            if not op or op["stage"] in TERMINAL_STAGES:
                raise ValueError("OPERATION_NOT_ACTIVE")
            if reset and op["credentials_reset"]:
                return op
            self._check_revision(conn, op)
            account = self.accounts.mark_douyin_auth_invalid(op["account_id"], status="login_required", reset=reset, connection=conn)
            conn.execute("UPDATE crawler_login_operations SET stage=?, credentials_reset=?, expected_auth_revision=?, updated_at=? WHERE id=?", ("reset_authorized" if reset else "checking", int(reset or op["credentials_reset"]),account["auth_state_revision"],_now(),operation_id))
            return self._get(conn, operation_id)

    def _check_revision(self, conn, op):
        account = self.accounts.get(op["account_id"], connection=conn)
        if not account or account["status"] == "disabled" or account["auth_state_revision"] != op["expected_auth_revision"] or account["platform_account_id"] != op["expected_platform_account_id"]:
            raise ValueError("AUTH_REVISION_CONFLICT")
        return account

    def commit_auth(self, operation_id, ciphertext, platform_account_id, *, credentials_changed=True):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            op = self._get(conn, operation_id)
            if not op:
                raise ValueError("OPERATION_EXPIRED")
            if op["stage"] == "committed":
                return op
            if op["stage"] in TERMINAL_STAGES or not op["profile_authority_established"]:
                raise ValueError("OPERATION_NOT_COMMITTABLE")
            self._check_revision(conn, op)
            if not platform_account_id or (op["expected_platform_account_id"] and platform_account_id != op["expected_platform_account_id"]):
                raise ValueError("IDENTITY_MISMATCH")
            source = self._get(conn, op["source_operation_id"]) if op["source_operation_id"] else None
            if source and source["stage"] != "recovery_required":
                raise ValueError("RECOVERY_SOURCE_SUPERSEDED")
            # A recovered Profile can be valid even though its new credentials were
            # never committed to SQLite. The recovery helper's observation of "no
            # new login" does not override the original candidate's provenance.
            stored_change = op["credentials_changed"]
            if op["intent"] == "recover":
                credentials_changed = True if stored_change is None else bool(stored_change)
            elif stored_change is not None:
                credentials_changed = bool(stored_change)
            credentials_changed = credentials_changed or bool(op["credentials_reset"])
            backup = conn.execute("SELECT auth_state_ciphertext FROM crawler_accounts WHERE id=?", (op["account_id"],)).fetchone()[0]
            credentials_changed = credentials_changed or not bool(backup)
            account = self.accounts.save_douyin_auth_state(op["account_id"], ciphertext, platform_account_id,
                connection=conn, expected_revision=op["expected_auth_revision"], credentials_changed=credentials_changed)
            result = json.dumps({"status":"success", "operation_id":operation_id,"account":account}, ensure_ascii=False)
            now = _now()
            conn.execute("UPDATE crawler_login_operations SET stage='committed', verified_platform_account_id=?,result_json=?,updated_at=?,completed_at=? WHERE id=?", (platform_account_id,result,now,now,operation_id))
            if source:
                conn.execute("UPDATE crawler_login_operations SET stage='recovered', recovered_by_operation_id=?, result_json=?,updated_at=?,completed_at=? WHERE id=?", (operation_id,result,now,now,source["id"]))
            return self._get(conn, operation_id)

    def get_command(self, operation_id, command_id):
        with self._connect() as conn:
            return _record(conn.execute("SELECT * FROM crawler_login_commands WHERE operation_id=? AND command_id=?", (operation_id,command_id)).fetchone())

    def record_command(self, operation_id, command_id, kind, payload, *, application_session_id, controller_epoch, sequence):
        if kind not in COMMAND_KINDS:
            raise ValueError("COMMAND_NOT_DURABLE")
        fingerprint = self.cipher.command_fingerprint({"kind":kind,"payload":payload})
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = _record(conn.execute("SELECT * FROM crawler_login_commands WHERE operation_id=? AND command_id=?", (operation_id,command_id)).fetchone())
            if old:
                if old["fingerprint_key_id"] != self.cipher.fingerprint_key_id:
                    raise ValueError("COMMAND_FINGERPRINT_UNAVAILABLE")
                if old["request_fingerprint"] != fingerprint:
                    raise ValueError("COMMAND_ID_CONFLICT")
                return old
            op = self._get(conn, operation_id)
            if not op or op["stage"] in TERMINAL_STAGES or op["stage"] == "recovery_required":
                raise ValueError("OPERATION_NOT_ACTIVE")
            if application_session_id != op["application_session_id"]:
                raise ValueError("SESSION_REPLACED")
            now = _now()
            conn.execute("""INSERT INTO crawler_login_commands(operation_id,command_id,kind,request_fingerprint,fingerprint_key_id,
                application_session_id,controller_epoch,sequence,execution_status,platform_status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,'accepted','pending',?,?)""", (operation_id,command_id,kind,fingerprint,self.cipher.fingerprint_key_id,
                application_session_id,controller_epoch,sequence,now,now))
        return self.get_command(operation_id,command_id)

    def dispatch_command(self, operation_id, command_id):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            op = self._get(conn, operation_id)
            if not op or op["stage"] in TERMINAL_STAGES or op["stage"] == "recovery_required":
                raise ValueError("OPERATION_NOT_ACTIVE")
            cursor = conn.execute("UPDATE crawler_login_commands SET execution_status='dispatching', dispatched_at=?,updated_at=? WHERE operation_id=? AND command_id=? AND execution_status='accepted'", (_now(),_now(),operation_id,command_id))
            if cursor.rowcount != 1:
                raise ValueError("COMMAND_NOT_DISPATCHABLE")
        return self.get_command(operation_id,command_id)

    @staticmethod
    def _validate_result(result):
        # Results are status metadata, never material from a login form or browser storage.
        forbidden = {"phone", "phone_number", "mobile", "code", "sms_code", "captcha", "cookies", "localStorage", "auth_state", "qr", "payload"}
        def walk(value):
            if isinstance(value, dict):
                if value.keys() & forbidden:
                    raise ValueError("SENSITIVE_COMMAND_RESULT")
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        walk(result)

    def finish_command(self, operation_id, command_id, *, execution_status="executed", platform_status="unknown", result=None, error_code=None):
        self._validate_result(result)
        if execution_status not in {"executed", "not_executed", "failed", "unknown"} or platform_status not in {"pending","confirmed","rejected","unknown","not_applicable"}:
            raise ValueError("INVALID_COMMAND_STATUS")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT execution_status FROM crawler_login_commands WHERE operation_id=? AND command_id=?", (operation_id,command_id)).fetchone()
            if not row:
                raise ValueError("COMMAND_NOT_FOUND")
            if row[0] in {"not_executed","failed"} or (row[0] == "unknown" and platform_status not in {"confirmed","rejected"}):
                return self.get_command(operation_id,command_id)
            conn.execute("UPDATE crawler_login_commands SET execution_status=?,platform_status=?,result_json=?,error_code=?,updated_at=?,completed_at=? WHERE operation_id=? AND command_id=?", (execution_status,platform_status,json.dumps(result) if result else None,error_code,_now(),_now(),operation_id,command_id))
        return self.get_command(operation_id,command_id)

    def interrupt_commands(self, operation_id):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE crawler_login_commands SET execution_status=CASE execution_status WHEN 'accepted' THEN 'not_executed' ELSE 'unknown' END,
                platform_status=CASE execution_status WHEN 'accepted' THEN 'not_applicable' ELSE 'unknown' END,updated_at=?,completed_at=?
                WHERE operation_id=? AND (execution_status IN ('accepted','dispatching') OR (execution_status='executed' AND platform_status IN ('pending','unknown')))""", (_now(),_now(),operation_id))

    def prune(self, *, retention_days=7, now=None):
        """Remove old terminal records only when no recovery/reference depends on them."""
        cutoff = ((now or datetime.now()) - timedelta(days=retention_days)).isoformat(timespec="microseconds")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT id,source_operation_id,recovered_by_operation_id,stage,completed_at FROM crawler_login_operations").fetchall()
            candidates = {r["id"] for r in rows if r["stage"] in TERMINAL_STAGES and r["completed_at"] and r["completed_at"] < cutoff}
            # A dependency from any retained record keeps its target retained too.
            while True:
                protected = {ref for r in rows if r["id"] not in candidates
                             for ref in (r["source_operation_id"], r["recovered_by_operation_id"]) if ref}
                remaining = candidates - protected
                if remaining == candidates:
                    break
                candidates = remaining
            for operation_id in candidates:
                conn.execute("UPDATE crawler_login_operations SET source_operation_id=NULL,recovered_by_operation_id=NULL WHERE id=?", (operation_id,))
            for operation_id in candidates:
                conn.execute("DELETE FROM crawler_login_commands WHERE operation_id=?", (operation_id,))
                conn.execute("DELETE FROM crawler_login_operations WHERE id=?", (operation_id,))
            return len(candidates)
