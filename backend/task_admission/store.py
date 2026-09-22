from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.audit_agent.config import settings
from backend.investigation_creation.errors import InvestigationCreationError


class AdmissionError(InvestigationCreationError):
    code = "TASK_ADMISSION_REJECTED"


def fingerprint(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


class AdmissionStore:
    """One control DB transaction owns quota, user slot and dispatch intent.

    No foreign keys to deletable business records: the ledger survives erasure.
    RESERVED tracks the unfinished slot. charge_state independently tracks billing.
    """

    def __init__(
        self,
        db_path=None,
        *,
        auth_db=None,
        account_db=None,
        clock=None,
        enabled=None,
    ):
        self.db_path = Path(
            db_path or settings.data_dir / "investigation_creation.sqlite3"
        ).resolve()
        self.auth_db = Path(auth_db or settings.app_auth_db).resolve()
        self.account_db = Path(account_db or self.db_path).resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.enabled = (
            settings.app_auth_mode == "required" if enabled is None else enabled
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS task_admissions (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    job_id TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    day TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('RESERVED','SUCCEEDED','RELEASED')),
                    decision TEXT NOT NULL DEFAULT 'OPEN',
                    queue_state TEXT NOT NULL DEFAULT 'QUEUED',
                    request_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    execution_token TEXT NOT NULL DEFAULT '',
                    report_version_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    UNIQUE(owner_id, request_key)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_user_unfinished_admission
                ON task_admissions(owner_id) WHERE state='RESERVED';
                CREATE UNIQUE INDEX IF NOT EXISTS uq_task_live_admission
                ON task_admissions(task_id) WHERE state='RESERVED';
                CREATE INDEX IF NOT EXISTS idx_admission_day
                ON task_admissions(owner_id,day,state);
                CREATE INDEX IF NOT EXISTS idx_admission_job ON task_admissions(job_id);
                CREATE TABLE IF NOT EXISTS task_admission_commands (
                    owner_id TEXT NOT NULL, request_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, admission_id TEXT NOT NULL,
                    PRIMARY KEY(owner_id,request_key)
                );
            """)

        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in db.execute("PRAGMA table_info(task_admissions)")}
            for column in ("waiting_reason", "resource_account_id"):
                if column not in columns:
                    db.execute(
                        f"ALTER TABLE task_admissions ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    )

        # Existing rows retain report-v1 semantics; never reinterpret history.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in db.execute("PRAGMA table_info(task_admissions)")}
            additions = {
                "quota_policy": "TEXT NOT NULL DEFAULT 'report_v1'",
                "charge_state": "TEXT NOT NULL DEFAULT 'LEGACY'",
                "collection_phase": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
                "system_failure": "TEXT NOT NULL DEFAULT ''",
                "end_requested_at": "TEXT NOT NULL DEFAULT ''",
            }
            for column, declaration in additions.items():
                if column not in columns:
                    db.execute(
                        f"ALTER TABLE task_admissions ADD COLUMN {column} {declaration}"
                    )

    @staticmethod
    def charge_predicate():
        return "(charge_state='CHARGED' OR (quota_policy='report_v1' AND state IN ('RESERVED','SUCCEEDED')))"

    @staticmethod
    def finish_charge(db, row):
        # Caller must prove execution stopped. Only positive pre-launch system
        # fault evidence permits a refund; cancellation and ambiguity never do.
        if (
            row["quota_policy"] == "accepted_v2"
            and row["charge_state"] == "CHARGED"
            and row["collection_phase"] == "NOT_STARTED"
            and row["system_failure"]
            and not row["end_requested_at"]
        ):
            db.execute(
                "UPDATE task_admissions SET charge_state='REFUNDED' WHERE id=?",
                (row["id"],),
            )

    def collection_launch(self, task_id, token):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM task_admissions WHERE task_id=? AND state='RESERVED'",
                (task_id,),
            ).fetchone()
            if not row or row["execution_token"] != token or row["decision"] != "OPEN":
                raise AdmissionError(
                    "任务已结束，不能启动采集。", code="EXECUTION_FENCED"
                )
            self.validate_user(db, row["owner_id"], row["resource_account_id"])
            first = row["collection_phase"] == "NOT_STARTED"
            db.execute(
                "UPDATE task_admissions SET collection_phase='MAY_HAVE_STARTED',updated_at=? WHERE id=?",
                (self.now(), row["id"]),
            )
            return first

    def system_fault(self, task_id, token, reason, *, launch_rejected=False):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM task_admissions WHERE task_id=? AND state='RESERVED' AND execution_token=?",
                (task_id, token),
            ).fetchone()
            if not row:
                return
            if launch_rejected:
                # Only Popen's synchronous failure, before a child exists, may
                # retract the first launch barrier. Never clear an earlier run.
                db.execute(
                    "UPDATE task_admissions SET collection_phase='NOT_STARTED' WHERE id=?",
                    (row["id"],),
                )
            db.execute(
                "UPDATE task_admissions SET system_failure=?,updated_at=? WHERE id=?",
                (reason, self.now(), row["id"]),
            )

    def connect(self, *, attach_account_db=False):
        # The crawler-account database is an authorization input, not part of
        # the quota/slot write transaction. Attaching it here makes SQLite's
        # BEGIN IMMEDIATE lock the JobStore database and can deadlock callbacks
        # that project cancellation or recovery state. _crawler_account_scope
        # performs a separate read when the database is not attached.
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        if self.enabled and self.auth_db != self.db_path:
            db.execute("ATTACH DATABASE ? AS admission_auth", (str(self.auth_db),))
        if (
            attach_account_db
            and
            self.enabled
            and self.account_db != self.db_path
            and self.account_db != self.auth_db
        ):
            db.execute(
                "ATTACH DATABASE ? AS admission_accounts", (str(self.account_db),)
            )
        return db

    def now(self):
        return self.clock().astimezone(timezone.utc).isoformat()

    def user_record(self, db, owner):
        if not self.enabled:
            return ""
        schema = "main" if self.auth_db == self.db_path else "admission_auth"
        return db.execute(
            f"SELECT * FROM {schema}.app_users WHERE id=?", (owner,)
        ).fetchone()

    def validate_user(self, db, owner, account_id=""):
        if not self.enabled:
            return
        user = self.user_record(db, owner)
        if (
            not user
            or user["status"] != "active"
            or (user["role"] != "admin" and (
                not user["expires_at"]
                or datetime.fromisoformat(user["expires_at"]) <= self.clock()
            ))
        ):
            raise AdmissionError(
                "账号已失效，请重新登录或联系管理员。", code="ACCOUNT_EXPIRED"
            )
        if account_id:
            schema = "main" if self.auth_db == self.db_path else "admission_auth"
            grant = db.execute(
                f"""SELECT 1 FROM {schema}.crawler_account_owners
                WHERE owner_user_id=? AND account_id=?""",
                (owner, account_id),
            ).fetchone()
            if grant is not None:
                return "private"
            if self._crawler_account_scope(db, account_id) == "public":
                return "public"
            raise AdmissionError(
                "只能使用公共采集账号或自己的私有采集账号。",
                code="CRAWLER_ACCOUNT_FORBIDDEN",
            )
        return ""

    def _crawler_account_scope(self, db, account_id: str) -> str:
        if self.account_db == self.db_path:
            schema = "main"
        elif self.account_db == self.auth_db:
            schema = "main" if self.auth_db == self.db_path else "admission_auth"
        else:
            schema = "admission_accounts"
        attached = {str(row[1]) for row in db.execute("PRAGMA database_list")}
        if schema not in attached:
            # Investigation confirmation owns a wider transaction and passes
            # its connection into AdmissionStore.reserve(). That connection
            # attaches the auth database, but intentionally does not know the
            # independently configured crawler-account database. Read only the
            # account scope here; dispatch revalidates eligibility and ownership
            # after acquiring the execution/account leases.
            if schema != "admission_accounts":
                return ""
            with sqlite3.connect(self.account_db) as account_db:
                row = account_db.execute(
                    "SELECT access_scope FROM crawler_accounts WHERE id=?",
                    (account_id,),
                ).fetchone()
            return str(row[0] or "private") if row else ""
        exists = db.execute(
            f"SELECT 1 FROM {schema}.sqlite_master "
            "WHERE type='table' AND name='crawler_accounts'"
        ).fetchone()
        if not exists:
            return ""
        row = db.execute(
            f"SELECT access_scope FROM {schema}.crawler_accounts WHERE id=?",
            (account_id,),
        ).fetchone()
        return str(row[0] or "private") if row else ""

    def reserve(
        self, db, *, owner, task_id, kind, key, payload, job_id="", request_hash=None
    ):
        digest = request_hash or fingerprint(payload)
        self.validate_user(db, owner, payload.get("crawler_account_id") or "")
        user = self.user_record(db, owner)
        unlimited = bool(user) and user["role"] == "admin"
        previous = db.execute(
            "SELECT * FROM task_admissions WHERE owner_id=? AND request_key=?",
            (owner, key),
        ).fetchone()
        if previous:
            if previous["fingerprint"] != digest:
                raise AdmissionError(
                    "同一请求标识不能用于不同内容。", code="IDEMPOTENCY_CONFLICT"
                )
            return dict(previous)
        active = db.execute(
            "SELECT task_id,job_id FROM task_admissions WHERE owner_id=? AND state='RESERVED'",
            (owner,),
        ).fetchone()
        if active:
            raise AdmissionError(
                "已有未完成任务，请完成或结束整个任务后再提交。",
                code="USER_TASK_LIMIT",
                details=dict(active),
            )
        day = self.clock().astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        count = db.execute(
            "SELECT count(*) FROM task_admissions WHERE owner_id=? AND day=? AND "
            + self.charge_predicate(),
            (owner, day),
        ).fetchone()[0]
        if not unlimited and count >= 3:
            raise AdmissionError("今日三次任务额度已用完。", code="DAILY_REPORT_LIMIT")
        ident, now = uuid4().hex, self.now()
        db.execute(
            """INSERT INTO task_admissions
            (id,owner_id,task_id,job_id,kind,day,state,request_key,fingerprint,payload_json,created_at,updated_at,quota_policy,charge_state,collection_phase)
            VALUES (?,?,?,?,?,?,'RESERVED',?,?,?,?,?,'accepted_v2','CHARGED',?)""",
            (
                ident,
                owner,
                task_id,
                job_id,
                kind,
                day,
                key,
                digest,
                json.dumps(payload, ensure_ascii=False),
                now,
                now,
                "UNKNOWN" if payload.get("resume_action") else "NOT_STARTED",
            ),
        )
        return dict(
            db.execute("SELECT * FROM task_admissions WHERE id=?", (ident,)).fetchone()
        )

    def enqueue(
        self, *, owner, task_id, kind, key, payload, job_id="", request_hash=None
    ):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self.reserve(
                db,
                owner=owner,
                task_id=task_id,
                kind=kind,
                key=key,
                payload=payload,
                job_id=job_id,
                request_hash=request_hash,
            )

    def for_task(self, task_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM task_admissions WHERE task_id=? OR job_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (task_id, task_id),
            ).fetchone()
        return dict(row) if row else None

    def summary(self, owner):
        now = self.clock().astimezone(ZoneInfo("Asia/Shanghai"))
        day = now.date().isoformat()
        reset = datetime.combine(
            now.date() + timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo
        )
        with self.connect() as db:
            user = self.user_record(db, owner)
            unlimited = bool(user) and user["role"] == "admin"
            counts = dict(
                db.execute(
                    "SELECT state,count(*) FROM task_admissions WHERE owner_id=? AND day=? GROUP BY state",
                    (owner, day),
                ).fetchall()
            )
            charged = db.execute(
                "SELECT count(*) FROM task_admissions WHERE owner_id=? AND day=? AND "
                + self.charge_predicate(),
                (owner, day),
            ).fetchone()[0]
            legacy = db.execute(
                "SELECT count(*) FROM task_admissions WHERE owner_id=? AND day=? AND quota_policy='report_v1'",
                (owner, day),
            ).fetchone()[0]
            refunded = db.execute(
                "SELECT count(*) FROM task_admissions WHERE owner_id=? AND day=? AND charge_state='REFUNDED'",
                (owner, day),
            ).fetchone()[0]
            active = db.execute(
                "SELECT task_id,job_id,kind,queue_state,decision,day,waiting_reason,resource_account_id FROM task_admissions WHERE owner_id=? AND state='RESERVED'",
                (owner,),
            ).fetchone()
        used, reserved = counts.get("SUCCEEDED", 0), counts.get("RESERVED", 0)
        return dict(
            day=day,
            timezone="Asia/Shanghai",
            unlimited=unlimited,
            limit=None if unlimited else 3,
            completed=used,
            reserved=reserved,
            used=charged,
            in_progress=reserved,
            refunded=refunded,
            quota_policy="accepted_v2",
            legacy_record_count=legacy,
            remaining=None if unlimited else max(0, 3 - charged),
            reset_at=reset.isoformat(),
            active_task=dict(active) if active else None,
        )

    def cancel(self, task_id, owner, *, on_cancel=None):
        # Cancellation updates the Job projection in on_cancel(). Do not attach
        # the independently configured account database to this write
        # transaction: BEGIN IMMEDIATE would lock that database too and make the
        # callback deadlock against its own JobStore connection.
        with self.connect(attach_account_db=False) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM task_admissions WHERE (task_id=? OR job_id=?) AND owner_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (task_id, task_id, owner),
            ).fetchone()
            if not row:
                raise AdmissionError("任务不存在。", code="TASK_NOT_FOUND")
            if row["state"] != "RESERVED" or row["decision"] in (
                "PUBLISHING",
                "PUBLISHED",
            ):
                return dict(row)
            if on_cancel is not None:
                # Keep the stop projection and cancellation ordered before
                # recovery can release this attempt and admit a resume.
                on_cancel(dict(row))
            # Never free a slot here, including queued tasks. The worker proves
            # no surviving execution exists under the execution lock first.
            db.execute(
                "UPDATE task_admissions SET decision='CANCELLED',end_requested_at=?,updated_at=? WHERE id=?",
                (self.now(), self.now(), row["id"]),
            )
            return dict(
                db.execute(
                    "SELECT * FROM task_admissions WHERE id=?", (row["id"],)
                ).fetchone()
            )

    def start(self, task_id, token):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM task_admissions WHERE task_id=? AND state='RESERVED'",
                (task_id,),
            ).fetchone()
            if not row:
                raise AdmissionError("任务未通过准入。")
            self.validate_user(
                db,
                row["owner_id"],
                row["resource_account_id"]
                or json.loads(row["payload_json"]).get("crawler_account_id")
                or "",
            )
            if row["decision"] == "CANCELLED":
                raise AdmissionError("任务已取消。", code="TASK_CANCELLED")
            db.execute(
                "UPDATE task_admissions SET execution_token=?,queue_state='RUNNING',updated_at=? WHERE id=?",
                (token, self.now(), row["id"]),
            )

    def bind_job(self, task_id, job_id):
        with self.connect() as db:
            db.execute(
                "UPDATE task_admissions SET job_id=?,updated_at=? WHERE task_id=? AND state='RESERVED'",
                (job_id, self.now(), task_id),
            )

    def hold(self, task_id, reason="", *, expected_updated_at=None):
        with self.connect() as db:
            db.execute(
                "UPDATE task_admissions SET queue_state='HELD',reason=?,updated_at=? WHERE task_id=? AND state='RESERVED' AND (? IS NULL OR updated_at=?)",
                (reason, self.now(), task_id, expected_updated_at, expected_updated_at),
            )

    def settle(
        self,
        task_id,
        *,
        report_id="",
        reason="",
        stopped=False,
        expected_updated_at=None,
    ):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM task_admissions WHERE task_id=? AND state='RESERVED'",
                (task_id,),
            ).fetchone()
            if not row or (
                expected_updated_at is not None
                and row["updated_at"] != expected_updated_at
            ):
                return False
            if report_id:
                if row["decision"] == "CANCELLED":
                    raise AdmissionError("已取消任务不能发布报告。")
                state, decision = "SUCCEEDED", "PUBLISHED"
            else:
                if not stopped or row["decision"] in ("PUBLISHING", "PUBLISHED"):
                    raise AdmissionError("执行或发布结果尚未核实，继续保留名额。")
                state, decision = "RELEASED", "CANCELLED"
                self.finish_charge(db, row)
            db.execute(
                "UPDATE task_admissions SET state=?,decision=?,queue_state='DONE',report_version_id=?,reason=?,updated_at=? WHERE id=?",
                (state, decision, report_id, reason, self.now(), row["id"]),
            )
            return True

    @contextmanager
    def publication(self, job_id, report_id, token):
        # Durable intent serializes cancellation BEFORE touching the report DB.
        # A crash after intent leaves RESERVED; recovery checks this exact version.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM task_admissions WHERE job_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            if (
                not row
                or row["state"] != "RESERVED"
                or row["decision"] == "CANCELLED"
                or not token
                or row["execution_token"] != token
            ):
                raise AdmissionError(
                    "任务已取消、结束或执行权失效，禁止发布。",
                    code="PUBLICATION_FENCED",
                )
            if row["report_version_id"] and row["report_version_id"] != report_id:
                raise AdmissionError(
                    "任务已经绑定其他报告。", code="PUBLICATION_FENCED"
                )
            self.validate_user(db, row["owner_id"])
            db.execute(
                "UPDATE task_admissions SET decision='PUBLISHING',report_version_id=?,updated_at=? WHERE id=?",
                (report_id, self.now(), row["id"]),
            )
        yield
        with self.connect() as db:
            db.execute(
                "UPDATE task_admissions SET decision='PUBLISHED',updated_at=? WHERE id=? AND decision='PUBLISHING'",
                (self.now(), row["id"]),
            )
