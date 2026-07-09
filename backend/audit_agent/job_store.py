from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .config import settings


DEFAULT_CONTROL = {
    "crawl_stop_requested": False,
    "analysis_paused": False,
    "analysis_stop_requested": False,
    "stop_all_requested": False,
}

RECOVERABLE_STATUSES = {
    "queued",
    "running",
    "crawl_pausing",
    "stopping",
    "analysis_running",
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
}

JSON_OBJECT_FIELDS = {
    "control",
    "rule_snapshot",
    "prompt_profile_snapshot",
}


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
                    platform TEXT,
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
                    max_notes INTEGER,
                    max_comments INTEGER,
                    max_concurrency INTEGER,
                    get_sub_comment INTEGER,
                    analyze_limit INTEGER,
                    run_crawler INTEGER,
                    source_output_id TEXT,
                    analysis_batch_size INTEGER,
                    input_type TEXT,
                    input_filename TEXT,
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
                    message TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_job_logs_job_id ON job_logs(job_id, id);
                CREATE INDEX IF NOT EXISTS idx_jobs_updated_at ON jobs(updated_at);
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

    def create(self, **kwargs) -> dict:
        with self._lock, self._connect() as conn:
            now = datetime.now().isoformat(timespec="seconds")
            job_id = uuid4().hex[:12]
            job = {
                "id": job_id,
                "status": "queued",
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
                    id, status, platform, display_name, crawl_mode, keyword, keyword_source, lexicon_category,
                    library_ids, capabilities, scoring_template, rule_snapshot,
                    lexicon_keywords, prompt_profile_snapshot, current_audit_config_revision_id,
                    creator_url, creator_id, start_page, max_notes,
                    max_comments, max_concurrency, get_sub_comment, analyze_limit, run_crawler,
                    source_output_id, analysis_batch_size, input_type, input_filename, items, control,
                    error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"],
                    job["status"],
                    job.get("platform"),
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
                    job.get("max_notes"),
                    job.get("max_comments"),
                    job.get("max_concurrency"),
                    1 if job.get("get_sub_comment") else 0,
                    job.get("analyze_limit"),
                    1 if job.get("run_crawler") else 0,
                    job.get("source_output_id"),
                    job.get("analysis_batch_size"),
                    job.get("input_type"),
                    job.get("input_filename"),
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

    def list(self, include_archived: bool = False) -> list[dict]:
        with self._lock, self._connect() as conn:
            where = "" if include_archived else "WHERE archived = 0"
            rows = conn.execute(f"SELECT * FROM jobs {where} ORDER BY created_at DESC").fetchall()
            return [self._row_to_job(conn, row) for row in rows]

    def update(self, job_id: str, **kwargs) -> None:
        if not kwargs:
            return
        allowed = {
            "status",
            "items",
            "error",
            "platform",
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
            "max_notes",
            "max_comments",
            "max_concurrency",
            "get_sub_comment",
            "analyze_limit",
            "run_crawler",
            "source_output_id",
            "analysis_batch_size",
            "input_type",
            "input_filename",
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

    def log(self, job_id: str, message: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO job_logs (job_id, time, message) VALUES (?, ?, ?)",
                (job_id, now, message),
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
                f"SELECT id, status FROM jobs WHERE archived = 0 AND status IN ({placeholders})",
                tuple(RECOVERABLE_STATUSES),
            ).fetchall()
            for row in rows:
                control = json.dumps(dict(DEFAULT_CONTROL), ensure_ascii=False)
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'interrupted', control = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (control, now, row["id"]),
                )
                conn.execute(
                    "INSERT INTO job_logs (job_id, time, message) VALUES (?, ?, ?)",
                    (
                        row["id"],
                        now,
                        f"服务启动恢复：任务从 {row['status']} 标记为已中断，可执行补分析。",
                    ),
                )
            return len(rows)

    def _row_to_job(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
        job = dict(row)
        job["library_ids"] = self._loads_json(job.get("library_ids"), [])
        job["capabilities"] = self._loads_json(job.get("capabilities"), [])
        job["rule_snapshot"] = self._loads_json(job.get("rule_snapshot"), {})
        job["lexicon_keywords"] = self._loads_json(job.get("lexicon_keywords"), [])
        job["prompt_profile_snapshot"] = self._loads_json(job.get("prompt_profile_snapshot"), {})
        job["items"] = self._loads_json(job.get("items"), [])
        job["control"] = self._normalize_control(job.get("control"))
        job["get_sub_comment"] = bool(job.get("get_sub_comment"))
        job["run_crawler"] = bool(job.get("run_crawler"))
        job["archived"] = bool(job.get("archived"))
        logs = conn.execute(
            "SELECT time, message FROM job_logs WHERE job_id = ? ORDER BY id ASC",
            (job["id"],),
        ).fetchall()
        job["logs"] = [{"time": log["time"], "message": log["message"]} for log in logs]
        return job

    def _to_db_value(self, key: str, value):
        if key in JSON_FIELDS:
            if key == "control":
                return json.dumps(self._normalize_control(value), ensure_ascii=False)
            if key in JSON_OBJECT_FIELDS:
                return json.dumps(value or {}, ensure_ascii=False)
            return json.dumps(value or [], ensure_ascii=False)
        if key in {"get_sub_comment", "run_crawler"}:
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
