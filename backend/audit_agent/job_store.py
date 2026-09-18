from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .config import settings


DEFAULT_CONTROL = {
    "crawl_stop_requested": False,
    "crawl_epoch": 0,
    "analysis_paused": False,
    "analysis_stop_requested": False,
    "stop_all_requested": False,
    "failure": {},
}

RECOVERABLE_STATUSES = {
    "queued",
    "running",
    "crawl_pausing",
    "stopping",
    "analysis_running",
    "analysis_pausing",
    "analysis_stopping",
    "analysis_paused",
}


JSON_FIELDS = {
    "items",
    "control",
    "library_ids",
    "capabilities",
    "lexicon_keywords",
    "rule_snapshot",
    "prompt_profile_snapshot",
    "requested_config",
    "effective_config",
}

JSON_OBJECT_FIELDS = {
    "control",
    "rule_snapshot",
    "prompt_profile_snapshot",
    "requested_config",
    "effective_config",
}

JOB_SUMMARY_COLUMNS = ", ".join((
    "id",
    "status",
    "crawl_status",
    "analysis_status",
    "platform",
    "crawler_account_id",
    "crawler_account_display_name",
    "display_name",
    "crawl_mode",
    "keyword",
    "keyword_source",
    "lexicon_category",
    "library_ids",
    "capabilities",
    "scoring_template",
    "rule_snapshot",
    "lexicon_keywords",
    "prompt_profile_snapshot",
    "current_audit_config_revision_id",
    "creator_url",
    "creator_id",
    "start_page",
    "crawl_checkpoint_page",
    "crawl_checkpoint_keyword",
    "max_notes",
    "max_total_notes",
    "max_comments",
    "max_concurrency",
    "max_items_per_minute",
    "get_sub_comment",
    "analyze_limit",
    "auto_analyze",
    "run_crawler",
    "source_output_id",
    "analysis_batch_size",
    "input_type",
    "input_filename",
    "requested_config",
    "effective_config",
    "control",
    "error",
    "archived",
    "created_at",
    "updated_at",
))


class JobStore:
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
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    crawl_status TEXT NOT NULL DEFAULT 'pending',
                    analysis_status TEXT NOT NULL DEFAULT 'pending',
                    platform TEXT,
                    crawler_account_id TEXT,
                    crawler_account_display_name TEXT,
                    display_name TEXT,
                    crawl_mode TEXT,
                    keyword TEXT,
                    keyword_source TEXT,
                    lexicon_category TEXT,
                    library_ids TEXT NOT NULL DEFAULT '[]',
                    capabilities TEXT NOT NULL DEFAULT '[]',
                    scoring_template TEXT,
                    rule_snapshot TEXT NOT NULL DEFAULT '{}',
                    lexicon_keywords TEXT,
                    prompt_profile_snapshot TEXT NOT NULL DEFAULT '{}',
                    current_audit_config_revision_id TEXT,
                    creator_url TEXT,
                    creator_id TEXT,
                    start_page INTEGER,
                    crawl_checkpoint_page INTEGER,
                    crawl_checkpoint_keyword TEXT,
                    max_notes INTEGER,
                    max_total_notes INTEGER NOT NULL DEFAULT 5,
                    max_comments INTEGER,
                    max_concurrency INTEGER,
                    max_items_per_minute INTEGER NOT NULL DEFAULT 5,
                    get_sub_comment INTEGER,
                    analyze_limit INTEGER,
                    auto_analyze INTEGER NOT NULL DEFAULT 1,
                    run_crawler INTEGER,
                    source_output_id TEXT,
                    analysis_batch_size INTEGER,
                    input_type TEXT,
                    input_filename TEXT,
                    requested_config TEXT NOT NULL DEFAULT '{}',
                    effective_config TEXT NOT NULL DEFAULT '{}',
                    items TEXT NOT NULL DEFAULT '[]',
                    control TEXT NOT NULL,
                    error TEXT,
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS job_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    time TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT 'pipeline',
                    level TEXT NOT NULL DEFAULT 'info',
                    message TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    retryable INTEGER,
                    action TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_job_logs_job_id ON job_logs(job_id, id);
                CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs(updated_at);

                CREATE TABLE IF NOT EXISTS monitored_users (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    stable_key TEXT NOT NULL,
                    display_name TEXT,
                    profile_url TEXT,
                    raw_identity TEXT,
                    source_audit_result_id INTEGER,
                    source_job_id TEXT,
                    author_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(platform, stable_key)
                );

                CREATE TABLE IF NOT EXISTS suspected_related_accounts (
                    id TEXT PRIMARY KEY,
                    monitored_user_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    stable_key TEXT NOT NULL,
                    display_name TEXT,
                    profile_url TEXT,
                    raw_identity TEXT,
                    source_comment_id TEXT,
                    source_comment_text TEXT,
                    source_risk_content TEXT,
                    source_audit_result_id INTEGER,
                    source_job_id TEXT,
                    analysis_job_id TEXT,
                    analysis_status TEXT NOT NULL DEFAULT 'job_created',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(monitored_user_id, platform, stable_key)
                );

                CREATE INDEX IF NOT EXISTS idx_monitored_users_updated_at
                ON monitored_users(updated_at);

                CREATE INDEX IF NOT EXISTS idx_suspected_related_accounts_user
                ON suspected_related_accounts(monitored_user_id, updated_at);

                CREATE INDEX IF NOT EXISTS idx_suspected_related_accounts_analysis_job
                ON suspected_related_accounts(analysis_job_id);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "archived" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            if "prompt_profile_snapshot" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN prompt_profile_snapshot TEXT NOT NULL DEFAULT '{}'")
            if "display_name" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN display_name TEXT")
            if "library_ids" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN library_ids TEXT NOT NULL DEFAULT '[]'")
            if "capabilities" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN capabilities TEXT NOT NULL DEFAULT '[]'")
            if "scoring_template" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN scoring_template TEXT")
            if "rule_snapshot" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN rule_snapshot TEXT NOT NULL DEFAULT '{}'")
            if "current_audit_config_revision_id" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN current_audit_config_revision_id TEXT")
            if "crawler_account_id" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN crawler_account_id TEXT")
            if "crawler_account_display_name" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN crawler_account_display_name TEXT")
            if "max_items_per_minute" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN max_items_per_minute INTEGER NOT NULL DEFAULT 5")
            if "crawl_checkpoint_page" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN crawl_checkpoint_page INTEGER")
            if "crawl_checkpoint_keyword" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN crawl_checkpoint_keyword TEXT")
            if "crawl_status" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN crawl_status TEXT NOT NULL DEFAULT 'pending'")
            if "analysis_status" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN analysis_status TEXT NOT NULL DEFAULT 'pending'")
            if "requested_config" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN requested_config TEXT NOT NULL DEFAULT '{}'")
            if "effective_config" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN effective_config TEXT NOT NULL DEFAULT '{}'")
            if "auto_analyze" not in columns:
                conn.execute("ALTER TABLE jobs ADD COLUMN auto_analyze INTEGER NOT NULL DEFAULT 1")
            if "max_total_notes" not in columns:
                # Zero marks historical rows whose pre-cap collection semantics
                # must be derived from their mode, keyword count and max_notes.
                conn.execute("ALTER TABLE jobs ADD COLUMN max_total_notes INTEGER NOT NULL DEFAULT 0")

            log_columns = {row["name"] for row in conn.execute("PRAGMA table_info(job_logs)").fetchall()}
            if "stage" not in log_columns:
                conn.execute("ALTER TABLE job_logs ADD COLUMN stage TEXT NOT NULL DEFAULT 'pipeline'")
            if "level" not in log_columns:
                conn.execute("ALTER TABLE job_logs ADD COLUMN level TEXT NOT NULL DEFAULT 'info'")
            if "reason" not in log_columns:
                conn.execute("ALTER TABLE job_logs ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
            if "error_code" not in log_columns:
                conn.execute("ALTER TABLE job_logs ADD COLUMN error_code TEXT NOT NULL DEFAULT ''")
            if "retryable" not in log_columns:
                conn.execute("ALTER TABLE job_logs ADD COLUMN retryable INTEGER")
            if "action" not in log_columns:
                conn.execute("ALTER TABLE job_logs ADD COLUMN action TEXT NOT NULL DEFAULT ''")

            related_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(suspected_related_accounts)").fetchall()
            }
            for column, definition in {
                "source_comment_text": "TEXT",
                "source_risk_content": "TEXT",
                "analysis_status": "TEXT NOT NULL DEFAULT 'job_created'",
            }.items():
                if column not in related_columns:
                    conn.execute(f"ALTER TABLE suspected_related_accounts ADD COLUMN {column} {definition}")

    def create(self, *, job_id: str | None = None, **kwargs) -> dict:
        with self._lock, self._connect() as conn:
            now = datetime.now().isoformat(timespec="seconds")
            resolved_job_id = str(job_id or "").strip() or uuid4().hex[:12]
            job = {
                "id": resolved_job_id,
                "status": "queued",
                "crawl_status": "queued" if kwargs.get("run_crawler") else "skipped",
                "analysis_status": (
                    "queued"
                    if kwargs.get("auto_analyze", True) and int(kwargs.get("analyze_limit") or 0) > 0
                    else "pending"
                ),
                "created_at": now,
                "updated_at": now,
                "logs": [],
                "items": [],
                "control": dict(DEFAULT_CONTROL),
                **kwargs,
            }
            conn.execute(
                """
                INSERT INTO jobs (
                    id, status, crawl_status, analysis_status, platform, crawler_account_id, crawler_account_display_name,
                    display_name, crawl_mode, keyword, keyword_source, lexicon_category,
                    library_ids, capabilities, scoring_template, rule_snapshot,
                    lexicon_keywords, prompt_profile_snapshot, current_audit_config_revision_id,
                    creator_url, creator_id, start_page, crawl_checkpoint_page, crawl_checkpoint_keyword, max_notes,
                    max_total_notes, max_comments, max_concurrency, max_items_per_minute,
                    get_sub_comment, analyze_limit, auto_analyze, run_crawler,
                    source_output_id, analysis_batch_size, input_type, input_filename,
                    requested_config, effective_config, items, control,
                    error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"],
                    job["status"],
                    job["crawl_status"],
                    job["analysis_status"],
                    job.get("platform"),
                    job.get("crawler_account_id"),
                    job.get("crawler_account_display_name"),
                    job.get("display_name"),
                    job.get("crawl_mode"),
                    job.get("keyword"),
                    job.get("keyword_source"),
                    job.get("lexicon_category"),
                    json.dumps(job.get("library_ids") or [], ensure_ascii=False),
                    json.dumps(job.get("capabilities") or [], ensure_ascii=False),
                    job.get("scoring_template"),
                    json.dumps(job.get("rule_snapshot") or {}, ensure_ascii=False),
                    json.dumps(job.get("lexicon_keywords") or [], ensure_ascii=False),
                    json.dumps(job.get("prompt_profile_snapshot") or {}, ensure_ascii=False),
                    job.get("current_audit_config_revision_id"),
                    job.get("creator_url"),
                    job.get("creator_id"),
                    job.get("start_page"),
                    job.get("crawl_checkpoint_page"),
                    job.get("crawl_checkpoint_keyword"),
                    job.get("max_notes"),
                    job.get("max_total_notes")
                    or job.get("analyze_limit")
                    or job.get("max_notes")
                    or 5,
                    job.get("max_comments"),
                    job.get("max_concurrency"),
                    job.get("max_items_per_minute", 5),
                    1 if job.get("get_sub_comment") else 0,
                    job.get("analyze_limit"),
                    1 if job.get("auto_analyze", True) else 0,
                    1 if job.get("run_crawler") else 0,
                    job.get("source_output_id"),
                    job.get("analysis_batch_size"),
                    job.get("input_type"),
                    job.get("input_filename"),
                    json.dumps(job.get("requested_config") or {}, ensure_ascii=False),
                    json.dumps(job.get("effective_config") or {}, ensure_ascii=False),
                    json.dumps(job.get("items") or [], ensure_ascii=False),
                    json.dumps(job.get("control") or DEFAULT_CONTROL, ensure_ascii=False),
                    job.get("error"),
                    job["created_at"],
                    job["updated_at"],
                ),
            )
            return job

    def get(self, job_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return None
            return self._row_to_job(conn, row)

    def get_summary(self, job_id: str, *, log_limit: int = 8) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                f"SELECT {JOB_SUMMARY_COLUMNS} FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_job(conn, row, log_limit=log_limit)

    def exists(self, job_id: str) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return row is not None

    def list(self, include_archived: bool = False) -> list[dict]:
        with self._lock, self._connect() as conn:
            where = "" if include_archived else "WHERE archived = 0"
            rows = conn.execute(f"SELECT * FROM jobs {where} ORDER BY created_at DESC").fetchall()
            return [self._row_to_job(conn, row) for row in rows]

    def list_summaries(self, include_archived: bool = False, *, log_limit: int = 8) -> list[dict]:
        with self._lock, self._connect() as conn:
            where = "" if include_archived else "WHERE archived = 0"
            rows = conn.execute(
                f"SELECT {JOB_SUMMARY_COLUMNS} FROM jobs {where} ORDER BY created_at DESC"
            ).fetchall()
            return [self._row_to_job(conn, row, log_limit=log_limit) for row in rows]

    def list_policy_references(self) -> list[dict]:
        """Return the small job projection used by the configuration center."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    j.id,
                    j.display_name,
                    j.keyword,
                    j.input_filename,
                    j.status,
                    j.created_at,
                    j.updated_at,
                    r.source_policy_id
                FROM jobs j
                LEFT JOIN task_audit_config_revisions r
                    ON r.id = j.current_audit_config_revision_id
                WHERE j.archived = 0
                ORDER BY j.created_at DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def update(self, job_id: str, **kwargs) -> None:
        if not kwargs:
            return
        status = str(kwargs.get("status") or "")
        if status == "failed":
            kwargs.setdefault("crawl_status", "failed")
            kwargs.setdefault("analysis_status", "failed")
        elif status == "crawl_paused":
            kwargs.setdefault("crawl_status", "stopped")
        elif status == "analysis_stopped":
            kwargs.setdefault("analysis_status", "stopped")
        elif status == "analysis_paused":
            kwargs.setdefault("analysis_status", "paused")
        elif status == "completed":
            # Compatibility for callers that report only an overall terminal
            # state. Phase-aware resume paths always pass their preserved crawl
            # phase explicitly, so a paused crawl is not rewritten here.
            kwargs.setdefault("crawl_status", "completed")
            kwargs.setdefault("analysis_status", "completed")
        allowed = {
            "status",
            "crawl_status",
            "analysis_status",
            "items",
            "error",
            "platform",
            "crawler_account_id",
            "crawler_account_display_name",
            "display_name",
            "crawl_mode",
            "keyword",
            "keyword_source",
            "lexicon_category",
            "library_ids",
            "capabilities",
            "scoring_template",
            "rule_snapshot",
            "lexicon_keywords",
            "prompt_profile_snapshot",
            "current_audit_config_revision_id",
            "creator_url",
            "creator_id",
            "start_page",
            "crawl_checkpoint_page",
            "crawl_checkpoint_keyword",
            "max_notes",
            "max_total_notes",
            "max_comments",
            "max_concurrency",
            "max_items_per_minute",
            "get_sub_comment",
            "analyze_limit",
            "auto_analyze",
            "run_crawler",
            "source_output_id",
            "analysis_batch_size",
            "input_type",
            "input_filename",
            "requested_config",
            "effective_config",
        }
        now = datetime.now().isoformat(timespec="seconds")
        fields = []
        values = []
        for key, value in kwargs.items():
            if key not in allowed:
                continue
            fields.append(f"{key} = ?")
            values.append(self._to_db_value(key, value))
        fields.append("updated_at = ?")
        values.append(now)
        values.append(job_id)
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)

    def control(self, job_id: str) -> dict:
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        return dict(job.get("control") or DEFAULT_CONTROL)

    def update_control(self, job_id: str, **kwargs) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT control FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            control = self._normalize_control(row["control"])
            control.update(kwargs)
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "UPDATE jobs SET control = ?, updated_at = ? WHERE id = ?",
                (json.dumps(control, ensure_ascii=False), now, job_id),
            )
            return dict(control)

    def log(
        self,
        job_id: str,
        message: str,
        *,
        stage: str = "",
        level: str = "",
        reason: str = "",
        error_code: str = "",
        retryable: bool | None = None,
        action: str = "",
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        inferred_stage, inferred_level = self._classify_log(message)
        normalized_level = str(level or "").strip().lower()
        if normalized_level == "warn":
            normalized_level = "warning"
        if normalized_level not in {"info", "warning", "error"}:
            normalized_level = inferred_level
        default_reason = ""
        default_error_code = ""
        default_retryable: bool | None = None
        default_action = ""
        if normalized_level == "warning":
            (
                default_reason,
                default_error_code,
                default_retryable,
                default_action,
            ) = self._warning_metadata(message)
        resolved_retryable = default_retryable if retryable is None else retryable
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO job_logs (
                       job_id, time, stage, level, message,
                       reason, error_code, retryable, action
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    now,
                    stage or inferred_stage,
                    normalized_level,
                    message,
                    str(reason or default_reason),
                    str(error_code or default_error_code),
                    None if resolved_retryable is None else int(resolved_retryable),
                    str(action or default_action),
                ),
            )
            conn.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (now, job_id))

    def archive(self, job_id: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET archived = 1, updated_at = ? WHERE id = ?",
                (now, job_id),
            )

    def recover_interrupted_jobs(self) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        placeholders = ",".join("?" for _ in RECOVERABLE_STATUSES)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                f"SELECT id, status, crawl_status, analysis_status FROM jobs "
                f"WHERE archived = 0 AND status IN ({placeholders})",
                tuple(RECOVERABLE_STATUSES),
            ).fetchall()
            for row in rows:
                control = json.dumps(dict(DEFAULT_CONTROL), ensure_ascii=False)
                crawl_status = str(row["crawl_status"] or "pending")
                analysis_status = str(row["analysis_status"] or "pending")
                if crawl_status in {"queued", "running", "pausing", "stopping"}:
                    crawl_status = "interrupted"
                if analysis_status in {"queued", "running", "pausing", "stopping"}:
                    analysis_status = "pending"
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'interrupted', crawl_status = ?, analysis_status = ?,
                        control = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (crawl_status, analysis_status, control, now, row["id"]),
                )
                conn.execute(
                    "INSERT INTO job_logs (job_id, time, stage, level, message) VALUES (?, ?, ?, ?, ?)",
                    (
                        row["id"],
                        now,
                        "recovery",
                        "warning",
                        f"服务启动恢复：任务从 {row['status']} 标记为已中断，可执行补分析。",
                    ),
                )
            return len(rows)

    def upsert_monitored_user_from_audit_result(self, audit_result: dict) -> dict:
        author = audit_result.get("author") if isinstance(audit_result.get("author"), dict) else {}
        platform = self._normalize_platform_label(
            self._first_text(audit_result.get("platform"), author.get("platform"))
        )
        display_name = self._first_text(
            author.get("nickname"),
            author.get("user_unique_id"),
            author.get("short_user_id"),
            author.get("user_id"),
            author.get("sec_uid"),
            audit_result.get("author_key"),
            "未知账号",
        )
        profile_url = self._first_text(
            author.get("profile_url"),
            author.get("homepage_url"),
            author.get("homepage"),
            author.get("user_url"),
            author.get("url"),
        )
        raw_identity = self._first_text(
            author.get("sec_uid"),
            author.get("user_id"),
            author.get("user_unique_id"),
            author.get("short_user_id"),
            display_name,
        )
        stable_key = self._stable_account_key(
            platform=platform,
            profile_url=profile_url,
            raw_identity=raw_identity,
            display_name=display_name,
        )
        if not platform or not stable_key:
            raise ValueError("audit result has no stable author identity")

        result_id = int(audit_result.get("audit_result_id") or audit_result.get("id") or 0) or None
        source_job_id = self._first_text(audit_result.get("job_id"))
        now = datetime.now().isoformat(timespec="seconds")

        with self._lock, self._connect() as conn:
            monitored_id = self._upsert_monitored_user(
                conn,
                platform=platform,
                stable_key=stable_key,
                display_name=display_name,
                profile_url=profile_url,
                raw_identity=raw_identity,
                source_audit_result_id=result_id,
                source_job_id=source_job_id,
                author=author,
                now=now,
            )
            row = conn.execute("SELECT * FROM monitored_users WHERE id = ?", (monitored_id,)).fetchone()
            return self._row_to_monitored_user(conn, row)

    def upsert_comment_user_relation(
        self,
        *,
        analysis_job_id: str,
        relation_context: dict,
        parent_audit_result: dict,
    ) -> dict:
        if (relation_context or {}).get("source") != "comment_user_analysis":
            return {}

        author = parent_audit_result.get("author") if isinstance(parent_audit_result.get("author"), dict) else {}
        parent_platform = str(parent_audit_result.get("platform") or author.get("platform") or "").strip()
        parent_display_name = self._first_text(
            author.get("nickname"),
            author.get("user_unique_id"),
            author.get("short_user_id"),
            author.get("user_id"),
            author.get("sec_uid"),
            parent_audit_result.get("author_key"),
            "未知账号",
        )
        parent_profile_url = self._first_text(
            author.get("profile_url"),
            author.get("homepage_url"),
            author.get("homepage"),
            author.get("user_url"),
            author.get("url"),
        )
        parent_raw_identity = self._first_text(
            author.get("sec_uid"),
            author.get("user_id"),
            author.get("user_unique_id"),
            author.get("short_user_id"),
            parent_display_name,
        )
        parent_stable_key = self._stable_account_key(
            platform=parent_platform,
            profile_url=parent_profile_url,
            raw_identity=parent_raw_identity,
            display_name=parent_display_name,
        )
        if not parent_platform or not parent_stable_key:
            raise ValueError("parent audit result has no stable author identity")

        comment_id = self._first_text(relation_context.get("source_comment_id"))
        source_comment = self._find_source_comment(parent_audit_result, comment_id)
        suspect_platform = self._normalize_platform_label(
            self._first_text(relation_context.get("suspect_platform"), parent_platform)
        )
        suspect_display_name = self._first_text(
            relation_context.get("suspect_display_name"),
            source_comment.get("nickname"),
            source_comment.get("user_unique_id"),
            source_comment.get("short_user_id"),
            source_comment.get("user_id"),
            "评论者",
        )
        suspect_profile_url = self._first_text(relation_context.get("suspect_profile_url"))
        suspect_raw_identity = self._first_text(
            relation_context.get("suspect_raw_identity"),
            source_comment.get("sec_uid"),
            source_comment.get("user_id"),
            source_comment.get("user_unique_id"),
            source_comment.get("short_user_id"),
            suspect_display_name,
        )
        suspect_stable_key = self._stable_account_key(
            platform=suspect_platform,
            profile_url=suspect_profile_url,
            raw_identity=suspect_raw_identity,
            display_name=suspect_display_name,
        )
        if not suspect_platform or not suspect_stable_key:
            raise ValueError("relation_context has no stable suspect identity")

        parent_result_id = int(parent_audit_result.get("audit_result_id") or parent_audit_result.get("id") or 0) or None
        parent_job_id = self._first_text(parent_audit_result.get("job_id"), relation_context.get("source_job_id"))
        source_comment_text = self._first_text(
            relation_context.get("source_comment_text"),
            source_comment.get("content"),
            source_comment.get("text"),
        )
        source_risk_content = self._first_text(
            relation_context.get("source_risk_content"),
            source_comment_text,
            parent_audit_result.get("summary"),
            parent_audit_result.get("title"),
        )
        now = datetime.now().isoformat(timespec="seconds")

        with self._lock, self._connect() as conn:
            monitored_id = self._upsert_monitored_user(
                conn,
                platform=parent_platform,
                stable_key=parent_stable_key,
                display_name=parent_display_name,
                profile_url=parent_profile_url,
                raw_identity=parent_raw_identity,
                source_audit_result_id=parent_result_id,
                source_job_id=parent_job_id,
                author=author,
                now=now,
            )
            related_id = self._upsert_related_account(
                conn,
                monitored_user_id=monitored_id,
                platform=suspect_platform,
                stable_key=suspect_stable_key,
                display_name=suspect_display_name,
                profile_url=suspect_profile_url,
                raw_identity=suspect_raw_identity,
                source_comment_id=comment_id,
                source_comment_text=source_comment_text,
                source_risk_content=source_risk_content,
                source_audit_result_id=parent_result_id,
                source_job_id=parent_job_id,
                analysis_job_id=analysis_job_id,
                analysis_status="job_created" if analysis_job_id else "linked",
                now=now,
            )
            return {
                "monitored_user_id": monitored_id,
                "related_account_id": related_id,
            }

    def list_monitored_users(self) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM monitored_users
                ORDER BY updated_at DESC, created_at DESC
                """
            ).fetchall()
            return [self._row_to_monitored_user(conn, row) for row in rows]

    def _upsert_monitored_user(
        self,
        conn: sqlite3.Connection,
        *,
        platform: str,
        stable_key: str,
        display_name: str,
        profile_url: str,
        raw_identity: str,
        source_audit_result_id: int | None,
        source_job_id: str,
        author: dict,
        now: str,
    ) -> str:
        row = conn.execute(
            "SELECT id FROM monitored_users WHERE platform = ? AND stable_key = ?",
            (platform, stable_key),
        ).fetchone()
        if row:
            monitored_id = row["id"]
            conn.execute(
                """
                UPDATE monitored_users
                SET display_name = COALESCE(NULLIF(?, ''), display_name),
                    profile_url = COALESCE(NULLIF(?, ''), profile_url),
                    raw_identity = COALESCE(NULLIF(?, ''), raw_identity),
                    source_audit_result_id = COALESCE(?, source_audit_result_id),
                    source_job_id = COALESCE(NULLIF(?, ''), source_job_id),
                    author_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    display_name,
                    profile_url,
                    raw_identity,
                    source_audit_result_id,
                    source_job_id,
                    json.dumps(author or {}, ensure_ascii=False),
                    now,
                    monitored_id,
                ),
            )
            return monitored_id

        monitored_id = uuid4().hex[:12]
        conn.execute(
            """
            INSERT INTO monitored_users (
                id, platform, stable_key, display_name, profile_url, raw_identity,
                source_audit_result_id, source_job_id, author_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                monitored_id,
                platform,
                stable_key,
                display_name,
                profile_url,
                raw_identity,
                source_audit_result_id,
                source_job_id,
                json.dumps(author or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        return monitored_id

    def _upsert_related_account(
        self,
        conn: sqlite3.Connection,
        *,
        monitored_user_id: str,
        platform: str,
        stable_key: str,
        display_name: str,
        profile_url: str,
        raw_identity: str,
        source_comment_id: str,
        source_comment_text: str,
        source_risk_content: str,
        source_audit_result_id: int | None,
        source_job_id: str,
        analysis_job_id: str,
        analysis_status: str,
        now: str,
    ) -> str:
        row = conn.execute(
            """
            SELECT id
            FROM suspected_related_accounts
            WHERE monitored_user_id = ? AND platform = ? AND stable_key = ?
            """,
            (monitored_user_id, platform, stable_key),
        ).fetchone()
        if row:
            related_id = row["id"]
            conn.execute(
                """
                UPDATE suspected_related_accounts
                SET display_name = COALESCE(NULLIF(?, ''), display_name),
                    profile_url = COALESCE(NULLIF(?, ''), profile_url),
                    raw_identity = COALESCE(NULLIF(?, ''), raw_identity),
                    source_comment_id = COALESCE(NULLIF(?, ''), source_comment_id),
                    source_comment_text = COALESCE(NULLIF(?, ''), source_comment_text),
                    source_risk_content = COALESCE(NULLIF(?, ''), source_risk_content),
                    source_audit_result_id = COALESCE(?, source_audit_result_id),
                    source_job_id = COALESCE(NULLIF(?, ''), source_job_id),
                    analysis_job_id = COALESCE(NULLIF(?, ''), analysis_job_id),
                    analysis_status = CASE
                        WHEN NULLIF(?, '') IS NOT NULL THEN 'job_created'
                        WHEN NULLIF(analysis_job_id, '') IS NULL THEN ?
                        ELSE analysis_status
                    END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    display_name,
                    profile_url,
                    raw_identity,
                    source_comment_id,
                    source_comment_text,
                    source_risk_content,
                    source_audit_result_id,
                    source_job_id,
                    analysis_job_id,
                    analysis_job_id,
                    analysis_status,
                    now,
                    related_id,
                ),
            )
            return related_id

        related_id = uuid4().hex[:12]
        conn.execute(
            """
            INSERT INTO suspected_related_accounts (
                id, monitored_user_id, platform, stable_key, display_name, profile_url,
                raw_identity, source_comment_id, source_comment_text, source_risk_content,
                source_audit_result_id, source_job_id, analysis_job_id, analysis_status,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                related_id,
                monitored_user_id,
                platform,
                stable_key,
                display_name,
                profile_url,
                raw_identity,
                source_comment_id,
                source_comment_text,
                source_risk_content,
                source_audit_result_id,
                source_job_id,
                analysis_job_id,
                analysis_status,
                now,
                now,
            ),
        )
        return related_id

    def _row_to_monitored_user(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
        related_rows = conn.execute(
            """
            SELECT
                r.*,
                j.status AS job_status,
                j.display_name AS job_display_name,
                j.created_at AS job_created_at,
                j.updated_at AS job_updated_at
            FROM suspected_related_accounts r
            LEFT JOIN jobs j ON j.id = r.analysis_job_id
            WHERE r.monitored_user_id = ?
            ORDER BY r.updated_at DESC, r.created_at DESC
            """,
            (row["id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "platform": row["platform"],
            "stable_key": row["stable_key"],
            "display_name": row["display_name"] or "",
            "profile_url": row["profile_url"] or "",
            "raw_identity": row["raw_identity"] or "",
            "source_audit_result_id": row["source_audit_result_id"],
            "source_job_id": row["source_job_id"] or "",
            "author": self._loads_json(row["author_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "suspected_related_accounts": [
                self._row_to_related_account(related)
                for related in related_rows
            ],
        }

    def _row_to_related_account(self, row: sqlite3.Row) -> dict:
        job_status = row["job_status"] or ""
        analysis_status = job_status or row["analysis_status"] or "job_created"
        return {
            "id": row["id"],
            "monitored_user_id": row["monitored_user_id"],
            "platform": row["platform"],
            "stable_key": row["stable_key"],
            "display_name": row["display_name"] or "",
            "profile_url": row["profile_url"] or "",
            "raw_identity": row["raw_identity"] or "",
            "source_comment_id": row["source_comment_id"] or "",
            "source_comment_text": row["source_comment_text"] or "",
            "source_risk_content": row["source_risk_content"] or "",
            "source_audit_result_id": row["source_audit_result_id"],
            "source_job_id": row["source_job_id"] or "",
            "analysis_job_id": row["analysis_job_id"] or "",
            "analysis_status": analysis_status,
            "job_created_at": row["job_created_at"] or "",
            "job_updated_at": row["job_updated_at"] or "",
            "analysis_job": {
                "id": row["analysis_job_id"] or "",
                "status": job_status,
                "display_name": row["job_display_name"] or "",
                "created_at": row["job_created_at"] or "",
                "updated_at": row["job_updated_at"] or "",
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _find_source_comment(self, parent_audit_result: dict, comment_id: str) -> dict:
        comments = parent_audit_result.get("comments") or []
        if not isinstance(comments, list):
            return {}
        if comment_id:
            for comment in comments:
                if not isinstance(comment, dict):
                    continue
                current = self._first_text(comment.get("comment_id"), comment.get("id"))
                if current == comment_id:
                    return comment
        return {}

    def _stable_account_key(
        self,
        *,
        platform: str,
        profile_url: str = "",
        raw_identity: str = "",
        display_name: str = "",
    ) -> str:
        normalized_url = self._normalize_profile_url(profile_url)
        if normalized_url:
            return f"url:{normalized_url}"
        identity = self._first_text(raw_identity)
        if identity:
            return f"id:{identity}"
        name = self._first_text(display_name)
        if name:
            return f"name:{name.lower()}"
        return f"platform:{platform or 'unknown'}"

    def _normalize_profile_url(self, value: str) -> str:
        text = self._first_text(value)
        if not text:
            return ""
        try:
            from urllib.parse import urlparse

            parsed = urlparse(text)
        except ValueError:
            return text.rstrip("/").lower()
        if not parsed.scheme or not parsed.netloc:
            return text.rstrip("/").lower()
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"

    def _normalize_platform_label(self, value: str) -> str:
        text = self._first_text(value).lower()
        return {
            "抖音": "dy",
            "douyin": "dy",
            "小红书": "xhs",
            "xiaohongshu": "xhs",
            "快手": "ks",
            "kuaishou": "ks",
        }.get(text, text)

    def _first_text(self, *values) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    def _row_to_job(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        log_limit: int | None = None,
    ) -> dict:
        job = dict(row)
        job["library_ids"] = self._loads_json(job.get("library_ids"), [])
        job["capabilities"] = self._loads_json(job.get("capabilities"), [])
        job["rule_snapshot"] = self._loads_json(job.get("rule_snapshot"), {})
        job["lexicon_keywords"] = self._loads_json(job.get("lexicon_keywords"), [])
        job["prompt_profile_snapshot"] = self._loads_json(job.get("prompt_profile_snapshot"), {})
        job["requested_config"] = self._loads_json(job.get("requested_config"), {})
        job["effective_config"] = self._loads_json(job.get("effective_config"), {})
        for key in ("collect_comments", "collect_media"):
            if key in job["effective_config"]:
                job[key] = bool(job["effective_config"][key])
        job["items"] = self._loads_json(job.get("items"), [])
        job["control"] = self._normalize_control(job.get("control"))
        job["get_sub_comment"] = bool(job.get("get_sub_comment"))
        job["auto_analyze"] = bool(job.get("auto_analyze"))
        job["run_crawler"] = bool(job.get("run_crawler"))
        job["archived"] = bool(job.get("archived"))
        if log_limit is None:
            logs = conn.execute(
                """SELECT time, stage, level, message, reason, error_code, retryable, action
                   FROM job_logs WHERE job_id = ? ORDER BY id ASC""",
                (job["id"],),
            ).fetchall()
        elif log_limit > 0:
            logs = conn.execute(
                """
                SELECT time, stage, level, message, reason, error_code, retryable, action
                FROM (
                    SELECT id, time, stage, level, message, reason, error_code, retryable, action
                    FROM job_logs
                    WHERE job_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (job["id"], log_limit),
            ).fetchall()
        else:
            logs = []
        job["logs"] = [
            {
                "time": log["time"],
                "stage": log["stage"],
                "level": log["level"],
                "message": log["message"],
                "reason": log["reason"],
                "error_code": log["error_code"],
                "retryable": None if log["retryable"] is None else bool(log["retryable"]),
                "action": log["action"],
            }
            for log in logs
        ]
        return job

    @staticmethod
    def _classify_log(message: str) -> tuple[str, str]:
        text = str(message or "")
        # Counters such as "失败=0" describe a successful summary and must not
        # promote the entire event to ERROR merely because the word is present.
        semantic_text = re.sub(
            r"(?:失败|异常|超时|不可用)\s*[=:：]\s*0(?!\d)",
            "",
            text,
        )
        if any(token in semantic_text for token in (
            "任务失败",
            "审核线程异常",
            "继续分析失败",
            "本地视频审核任务失败",
            "审核失败（",
            "重试后仍漏项",
            "连续两次超时",
        )):
            level = "error"
        elif any(token in semantic_text for token in (
            "失败",
            "异常",
            "不可用",
            "超时",
            "重试",
            "暂停",
            "停止",
            "跳过",
            "冷却",
            "降级",
            "兜底",
            "漏项",
            "回退",
        )):
            level = "warning"
        else:
            level = "info"
        if any(token in text for token in ("参数", "limit", "配置")):
            stage = "configuration"
        elif any(token in text for token in ("账号", "登录", "验证")):
            stage = "account"
        elif any(token in text for token in ("控制指令", "暂停", "恢复", "停止")):
            stage = "control"
        elif any(token in text for token in ("评论", "媒体", "图片", "视频", "OCR", "ASR", "音频")):
            stage = "media"
        elif any(token in text for token in ("ingestion", "入库", "batch")):
            stage = "ingestion"
        elif any(token in text for token in ("爬取", "采集", "MediaCrawler", "分页")):
            stage = "crawl"
        elif any(token in text for token in ("分析", "审核", "研判", "队列")):
            stage = "analysis"
        elif any(token in text for token in ("恢复", "中断")):
            stage = "recovery"
        else:
            stage = "pipeline"
        return stage, level

    @staticmethod
    def _warning_metadata(message: str) -> tuple[str, str, bool, str]:
        """Provide safe, stable diagnostics for inferred WARN events.

        Call sites can still supply more precise fields; ``log`` only uses these
        values for fields the caller omitted.  Reasons intentionally avoid raw
        exception text because logs are also projected into the public API.
        """
        text = str(message or "")
        rules: tuple[tuple[tuple[str, ...], tuple[str, str, bool, str]], ...] = (
            (
                ("ASR 翻译返回不完整",),
                (
                    "ASR 翻译输出不完整，已自动拆分重试",
                    "asr_translation_incomplete",
                    True,
                    "retry_in_smaller_batches",
                ),
            ),
            (
                ("ASR 翻译未完成",),
                (
                    "ASR 翻译未生成可用结果，已保留原始转写并继续",
                    "asr_translation_failed",
                    True,
                    "continue_with_original_transcript",
                ),
            ),
            (
                ("视频下载失败",),
                (
                    "远程视频下载失败，已跳过该视频并继续审核",
                    "video_download_failed",
                    True,
                    "continue_without_video",
                ),
            ),
            (
                ("图片下载失败",),
                (
                    "远程图片下载失败，已跳过该图片并继续审核",
                    "image_download_failed",
                    True,
                    "continue_without_image",
                ),
            ),
            (
                ("远程媒体不是可播放视频",),
                (
                    "远程媒体不是可播放的视频文件，已安全跳过",
                    "invalid_remote_video",
                    False,
                    "skip_invalid_video",
                ),
            ),
            (
                ("媒体已确定无音轨",),
                (
                    "媒体没有音轨，无需执行语音识别",
                    "audio_track_missing",
                    False,
                    "skip_asr",
                ),
            ),
            (
                ("音频抽取失败",),
                (
                    "未能抽取音频，已跳过语音识别并继续视频审核",
                    "audio_extraction_failed",
                    True,
                    "continue_without_asr",
                ),
            ),
            (
                ("音频/转写失败",),
                (
                    "音频处理或语音识别未完成，已继续处理其他证据",
                    "audio_transcription_failed",
                    True,
                    "continue_without_asr",
                ),
            ),
            (
                ("ASR 未识别到有效语音文本",),
                (
                    "语音识别未产生有效文本，无需执行翻译",
                    "asr_text_empty",
                    False,
                    "skip_asr_translation",
                ),
            ),
            (
                ("融合模型调用", "超时", "将重试"),
                (
                    "融合审核调用超时，正在执行预期重试",
                    "fusion_timeout_retry",
                    True,
                    "retry_fusion",
                ),
            ),
            (
                ("评论审核", "漏项"),
                (
                    "评论审核结果不完整，正在执行补偿处理",
                    "comment_audit_incomplete",
                    True,
                    "retry_missing_comments",
                ),
            ),
            (
                ("账号", "冷却"),
                (
                    "采集账号处于冷却期，当前任务将等待或切换账号",
                    "crawler_account_cooldown",
                    True,
                    "wait_or_switch_account",
                ),
            ),
            (
                ("限流",),
                (
                    "平台请求受到限流，需等待冷却后继续",
                    "crawler_rate_limited",
                    True,
                    "wait_and_retry",
                ),
            ),
        )
        for required_tokens, metadata in rules:
            if all(token in text for token in required_tokens):
                return metadata

        if "暂停" in text:
            return (
                "任务已在安全检查点暂停",
                "operation_paused",
                True,
                "resume_when_ready",
            )
        if "停止" in text:
            return (
                "任务或当前处理阶段已安全停止",
                "operation_stopped",
                True,
                "resume_or_restart",
            )
        if any(token in text for token in ("降级", "兜底", "回退")):
            return (
                "已启用预期的降级或兜底路径继续处理",
                "expected_fallback",
                False,
                "continue_with_fallback",
            )
        if "跳过" in text:
            return (
                "当前步骤不满足处理条件，已跳过并继续",
                "expected_skip",
                False,
                "skip_and_continue",
            )
        if any(token in text for token in ("重试", "超时", "漏项")):
            return (
                "当前步骤未完整完成，系统将按既定策略重试",
                "expected_retry",
                True,
                "retry",
            )
        if any(token in text for token in ("失败", "异常", "不可用")):
            return (
                "当前步骤未完成，任务已继续或等待后续恢复",
                "recoverable_operation_failure",
                True,
                "continue_or_retry",
            )
        return (
            "任务进入需要关注但不阻断执行的状态",
            "operation_degraded",
            False,
            "inspect_if_repeated",
        )

    def _to_db_value(self, key: str, value):
        if key in JSON_FIELDS:
            if key == "control":
                return json.dumps(self._normalize_control(value), ensure_ascii=False)
            if key in JSON_OBJECT_FIELDS:
                return json.dumps(value or {}, ensure_ascii=False)
            return json.dumps(value or [], ensure_ascii=False)
        if key in {"get_sub_comment", "auto_analyze", "run_crawler"}:
            return 1 if value else 0
        return value

    def _loads_json(self, value, fallback):
        if not value:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return fallback

    def _normalize_control(self, value) -> dict:
        loaded = value if isinstance(value, dict) else self._loads_json(value, {})
        control = dict(DEFAULT_CONTROL)
        if isinstance(loaded, dict):
            control.update(loaded)
        return control


job_store = JobStore()
