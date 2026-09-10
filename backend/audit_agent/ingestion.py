from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Iterable

from .config import settings


def utc_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def content_identity(item: dict, platform: str) -> str:
    fields = {
        "xhs": ("note_id", "note_url"),
        "dy": ("aweme_id", "note_id", "aweme_url"),
        "ks": ("video_id", "note_id", "video_url"),
    }.get(platform, ("note_id", "url"))
    for field in fields:
        value = item.get(field)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            if value > 0:
                return str(value)
            continue
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if cleaned != value or cleaned.lower() in {"", "none", "null", "undefined"}:
            continue
        return cleaned
    return ""


SELECTED_CONTENT_PAYLOAD_UNAVAILABLE = "selected_content_payload_unavailable"


class SelectedContentPayloadUnavailableError(RuntimeError):
    code = SELECTED_CONTENT_PAYLOAD_UNAVAILABLE

    def __init__(self, task_content_id: int, reason: str):
        self.task_content_id = task_content_id
        self.reason = reason
        super().__init__(f"{self.code}: task_content_id={task_content_id}: {reason}")


def comment_content_identity(comment: dict, platform: str) -> str:
    fields = {
        "xhs": ("note_id",),
        "dy": ("aweme_id", "note_id"),
        "ks": ("video_id", "note_id"),
    }.get(platform, ("note_id",))
    for field in fields:
        value = comment.get(field)
        if value:
            return str(value)
    return ""


def safe_part(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value)
    return cleaned[:120] or "unknown"


class AuditResultStore:
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
                CREATE TABLE IF NOT EXISTS audit_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    content_id INTEGER,
                    platform TEXT NOT NULL,
                    content_key TEXT NOT NULL,
                    note_id TEXT,
                    url TEXT,
                    title TEXT,
                    author_key TEXT,
                    author_json TEXT,
                    decision TEXT,
                    risk_level TEXT,
                    categories_json TEXT,
                    summary TEXT,
                    evidence_count INTEGER NOT NULL DEFAULT 0,
                    risk_image_count INTEGER NOT NULL DEFAULT 0,
                    risk_frame_count INTEGER NOT NULL DEFAULT 0,
                    comment_count INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT NOT NULL,
                    result_path TEXT,
                    model_text TEXT,
                    model_vl TEXT,
                    prompt_version TEXT,
                    audit_config_revision_id TEXT,
                    review_status TEXT,
                    review_note TEXT,
                    reviewed_at TEXT,
                    analyzed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, content_key)
                );

                CREATE INDEX IF NOT EXISTS idx_audit_results_job_seq
                ON audit_results(job_id, id);

                CREATE INDEX IF NOT EXISTS idx_audit_results_job_decision
                ON audit_results(job_id, decision, risk_level);

                CREATE INDEX IF NOT EXISTS idx_audit_results_author
                ON audit_results(job_id, author_key);

                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(audit_results)").fetchall()}
            if "audit_config_revision_id" not in columns:
                conn.execute("ALTER TABLE audit_results ADD COLUMN audit_config_revision_id TEXT")
            if "review_status" not in columns:
                conn.execute("ALTER TABLE audit_results ADD COLUMN review_status TEXT")
            if "review_note" not in columns:
                conn.execute("ALTER TABLE audit_results ADD COLUMN review_note TEXT")
            if "reviewed_at" not in columns:
                conn.execute("ALTER TABLE audit_results ADD COLUMN reviewed_at TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_results_revision
                ON audit_results(audit_config_revision_id)
                """
            )

    def upsert_result(
        self,
        *,
        job_id: str,
        platform: str,
        content_key: str,
        result: dict,
        result_path: str = "",
        content_id: int | None = None,
        model_text: str | None = None,
        model_vl: str | None = None,
        prompt_version: str = "",
        audit_config_revision_id: str = "",
    ) -> dict:
        now = utc_now()
        author = result.get("author") if isinstance(result.get("author"), dict) else {}
        categories = result.get("categories") or []
        if not isinstance(categories, list):
            categories = [str(categories)]

        note_id = str(result.get("note_id") or content_key or "")
        stable_content_key = str(content_key or note_id or result.get("url") or "unknown")
        payload = {
            "job_id": job_id,
            "content_id": content_id,
            "platform": platform,
            "content_key": stable_content_key,
            "note_id": note_id,
            "url": str(result.get("url") or ""),
            "title": str(result.get("title") or result.get("desc") or ""),
            "author_key": self._author_key(author),
            "author_json": json.dumps(author, ensure_ascii=False),
            "decision": str(result.get("decision") or "review"),
            "risk_level": str(result.get("risk_level") or "unknown"),
            "categories_json": json.dumps(categories, ensure_ascii=False),
            "summary": str(result.get("summary") or ""),
            "evidence_count": self._evidence_count(result),
            "risk_image_count": len(result.get("risk_images") or []),
            "risk_frame_count": len(result.get("risk_frames") or []),
            "comment_count": int(result.get("comments_count") or len(result.get("comments") or [])),
            "result_json": json.dumps(result, ensure_ascii=False),
            "result_path": result_path,
            "model_text": model_text if model_text is not None else settings.qwen_text_model,
            "model_vl": model_vl if model_vl is not None else settings.qwen_vl_model,
            "prompt_version": prompt_version,
            "audit_config_revision_id": audit_config_revision_id,
            "analyzed_at": now,
            "created_at": now,
            "updated_at": now,
        }

        columns = list(payload.keys())
        placeholders = ", ".join("?" for _ in columns)
        update_columns = [column for column in columns if column not in {"id", "job_id", "content_key", "created_at"}]
        assignments = ", ".join(f"{column} = excluded.{column}" for column in update_columns)
        values = [payload[column] for column in columns]

        with self._lock, self._connect() as conn:
            # Serialize the archive check with this write so deletion cannot race a late result.
            conn.execute("BEGIN IMMEDIATE")
            if self._job_is_archived(conn, job_id):
                return {}
            conn.execute(
                f"""
                INSERT INTO audit_results ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT(job_id, content_key) DO UPDATE SET {assignments}
                """,
                values,
            )
            row = conn.execute(
                "SELECT * FROM audit_results WHERE job_id = ? AND content_key = ?",
                (job_id, stable_content_key),
            ).fetchone()
        return self._row_to_item(row) if row else {}

    def list_results(
        self,
        job_id: str = "",
        *,
        after_id: int = 0,
        offset: int = 0,
        limit: int = 500,
        decision: str = "",
        risk_level: str = "",
        author_key: str = "",
        keyword: str = "",
        sort: str = "id",
        compact: bool = False,
    ) -> dict:
        limit = min(max(int(limit or 500), 1), 1000)
        offset = max(int(offset or 0), 0)
        where = []
        params: list[object] = []
        if job_id:
            where.append("job_id = ?")
            params.append(job_id)

        if after_id > 0:
            where.append("id > ?")
            params.append(after_id)
        if decision:
            where.append("decision = ?")
            params.append(decision)
        if risk_level:
            where.append("risk_level = ?")
            params.append(risk_level)
        if author_key:
            where.append("author_key = ?")
            params.append(author_key)
        if keyword:
            like = f"%{keyword}%"
            where.append(
                "(title LIKE ? OR summary LIKE ? OR note_id LIKE ? OR url LIKE ? OR result_json LIKE ?)"
            )
            params.extend([like, like, like, like, like])

        order_sql = self._order_sql(sort)
        with self._lock, self._connect() as conn:
            if self._has_jobs_table(conn):
                where.append(
                    "NOT EXISTS ("
                    "SELECT 1 FROM jobs "
                    "WHERE jobs.id = audit_results.job_id AND jobs.archived = 1"
                    ")"
                )
            where_sql = " AND ".join(where) if where else "1 = 1"
            total_row = conn.execute(
                f"SELECT COUNT(*) AS count FROM audit_results WHERE {where_sql}",
                params,
            ).fetchone()
            rows = conn.execute(
                f"SELECT * FROM audit_results WHERE {where_sql} {order_sql} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()

        items = [self._row_to_item(row) for row in rows]
        if compact:
            items = [self._compact_list_item(item) for item in items]
        next_after_id = max((int(item.get("id") or 0) for item in items), default=after_id)
        return {
            "items": items,
            "next_after_id": next_after_id,
            "total": int(total_row["count"] or 0) if total_row else 0,
        }

    def get_result(self, result_id: int) -> dict | None:
        with self._lock, self._connect() as conn:
            archived_filter = ""
            if self._has_jobs_table(conn):
                archived_filter = (
                    " AND NOT EXISTS ("
                    "SELECT 1 FROM jobs "
                    "WHERE jobs.id = audit_results.job_id AND jobs.archived = 1"
                    ")"
                )
            row = conn.execute(
                f"SELECT * FROM audit_results WHERE id = ?{archived_filter}",
                (result_id,),
            ).fetchone()
            return self._row_to_item(row) if row else None

    def delete_for_job(self, job_id: str) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM audit_results WHERE job_id = ?", (job_id,))
            return int(cursor.rowcount or 0)

    def delete_for_archived_jobs(self) -> int:
        with self._lock, self._connect() as conn:
            if not self._has_jobs_table(conn):
                return 0
            cursor = conn.execute(
                """
                DELETE FROM audit_results
                WHERE job_id IN (SELECT id FROM jobs WHERE archived = 1)
                """
            )
            return int(cursor.rowcount or 0)

    def review_result(
        self,
        result_id: int,
        *,
        status: str,
        note: str = "",
        reviewer: str = "",
    ) -> dict:
        now = utc_now()
        with self._lock, self._connect() as conn:
            archived_filter = ""
            if self._has_jobs_table(conn):
                archived_filter = (
                    " AND NOT EXISTS ("
                    "SELECT 1 FROM jobs "
                    "WHERE jobs.id = audit_results.job_id AND jobs.archived = 1"
                    ")"
                )
            row = conn.execute(
                f"SELECT * FROM audit_results WHERE id = ?{archived_filter}",
                (result_id,),
            ).fetchone()
            if not row:
                raise KeyError(result_id)
            result = self._loads_json(row["result_json"], {})
            if not isinstance(result, dict):
                result = {}
            result["review"] = {
                "status": status,
                "note": note,
                "reviewer": reviewer,
                "reviewed_at": now,
            }
            conn.execute(
                """
                UPDATE audit_results
                SET result_json = ?, review_status = ?, review_note = ?, reviewed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(result, ensure_ascii=False), status, note, now, now, result_id),
            )
            row = conn.execute("SELECT * FROM audit_results WHERE id = ?", (result_id,)).fetchone()
            return self._row_to_item(row)

    def representative_author_for_job(self, job_id: str) -> dict:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT author_json
                FROM audit_results
                WHERE job_id = ?
                  AND COALESCE(author_json, '') != ''
                ORDER BY id DESC
                LIMIT 20
                """,
                (job_id,),
            ).fetchall()

        for row in rows:
            author = self._loads_json(row["author_json"], {})
            if not isinstance(author, dict):
                continue
            if any(author.get(key) for key in ("nickname", "user_unique_id", "short_user_id", "user_id", "sec_uid")):
                return author
        return {}

    def _order_sql(self, sort: str) -> str:
        if sort == "risk":
            return """
                ORDER BY
                    CASE decision
                        WHEN 'reject' THEN 3
                        WHEN 'review' THEN 2
                        WHEN 'pass' THEN 1
                        ELSE 0
                    END DESC,
                    CASE risk_level
                        WHEN 'high' THEN 3
                        WHEN 'medium' THEN 2
                        WHEN 'low' THEN 1
                        ELSE 0
                    END DESC,
                    evidence_count DESC,
                    id DESC
            """
        if sort == "latest":
            return "ORDER BY id DESC"
        return "ORDER BY id ASC"

    def _row_to_item(self, row: sqlite3.Row) -> dict:
        result = self._loads_json(row["result_json"], {})
        if not isinstance(result, dict):
            result = {}
        categories = self._loads_json(row["categories_json"], [])
        item = dict(result)
        item.update({
            "id": int(row["id"]),
            "audit_result_id": int(row["id"]),
            "job_id": row["job_id"],
            "content_id": row["content_id"],
            "platform": row["platform"],
            "content_key": row["content_key"],
            "author_key": row["author_key"],
            "result_path": row["result_path"],
            "analyzed_at": row["analyzed_at"],
            "evidence_count": int(row["evidence_count"] or 0),
            "risk_image_count": int(row["risk_image_count"] or 0),
            "risk_frame_count": int(row["risk_frame_count"] or 0),
            "comment_count": int(row["comment_count"] or 0),
            "prompt_version": row["prompt_version"] or result.get("prompt_version") or "",
            "audit_config_revision_id": row["audit_config_revision_id"] or result.get("audit_config_revision_id") or "",
            "review_status": row["review_status"] or "",
            "review_note": row["review_note"] or "",
            "reviewed_at": row["reviewed_at"] or "",
        })
        item.setdefault("note_id", row["note_id"])
        item.setdefault("url", row["url"])
        item.setdefault("title", row["title"])
        item.setdefault("decision", row["decision"] or "review")
        item.setdefault("risk_level", row["risk_level"] or "unknown")
        item.setdefault("categories", categories if isinstance(categories, list) else [])
        item.setdefault("summary", row["summary"] or "")
        return item

    def _compact_list_item(self, item: dict) -> dict:
        fields = (
            "id",
            "audit_result_id",
            "job_id",
            "content_id",
            "platform",
            "content_key",
            "note_id",
            "url",
            "title",
            "title_zh",
            "content_title",
            "desc",
            "desc_zh",
            "summary",
            "decision",
            "risk_level",
            "risk_score",
            "primary_risk",
            "categories",
            "author_key",
            "author",
            "analyzed_at",
            "updated_at",
            "created_at",
            "review_status",
            "review_note",
            "reviewed_at",
            "audit_config_revision_id",
        )
        compact = {field: item.get(field) for field in fields if item.get(field) is not None}
        compact["evidence_count"] = int(item.get("evidence_count") or 0)
        groups = [value for value in item.get("evidence_groups") or [] if isinstance(value, dict)]
        if groups:
            compact["evidence_groups"] = [
                {key: group.get(key) for key in ("id", "type", "count") if group.get(key) is not None}
                for group in groups
            ]
        evidence = next(
            (
                value
                for value in item.get("evidence_items") or []
                if isinstance(value, dict) and (value.get("risk_library_id") or value.get("risk_library_label"))
            ),
            None,
        )
        if evidence:
            compact["evidence_items"] = [{
                "risk_library_id": evidence.get("risk_library_id"),
                "risk_library_label": evidence.get("risk_library_label"),
            }]
        thumbnail = self._compact_thumbnail_asset(item)
        if thumbnail:
            compact["thumbnail_asset"] = thumbnail
        duration = self._compact_duration(item)
        if duration is not None:
            compact["duration_seconds"] = duration
        return compact

    def _compact_thumbnail_asset(self, item: dict) -> dict:
        index = item.get("evidence_index") if isinstance(item.get("evidence_index"), dict) else {}
        candidates = []
        candidates.extend(item.get("image_analyses") or [])
        candidates.extend(index.get("image_units") or [])
        for field in ("cover_url", "video_cover_url", "thumbnail_url"):
            if item.get(field):
                candidates.append({"url": item[field]})
        for video in item.get("video_results") or []:
            if not isinstance(video, dict):
                continue
            for field in ("timeline_frames", "review_sheets", "segment_reviews"):
                candidates.extend(video.get(field) or [])
        for field in ("timeline_frames", "review_sheets", "segment_reviews"):
            candidates.extend(index.get(field) or [])
        for video in index.get("video_units") or []:
            if isinstance(video, dict):
                candidates.extend(video.get("segment_reviews") or [])

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            asset = {
                key: candidate.get(key)
                for key in ("asset_rel", "local_path", "original_path", "path", "url")
                if candidate.get(key)
            }
            if asset:
                return asset
        return {}

    def _compact_duration(self, item: dict) -> float | None:
        index = item.get("evidence_index") if isinstance(item.get("evidence_index"), dict) else {}
        values = [
            *((video or {}).get("duration") for video in item.get("video_results") or [] if isinstance(video, dict)),
            *((video or {}).get("duration") for video in index.get("video_units") or [] if isinstance(video, dict)),
        ]
        for value in values:
            try:
                duration = float(value)
            except (TypeError, ValueError):
                continue
            if duration > 0:
                return duration
        return None

    def _author_key(self, author: dict) -> str:
        for key in ("sec_uid", "user_id", "user_unique_id", "nickname"):
            value = author.get(key)
            if value:
                return str(value)
        return "unknown"

    def _evidence_count(self, result: dict) -> int:
        return (
            len(result.get("risk_evidence") or [])
            + len(result.get("risk_frames") or [])
            + len(result.get("risk_images") or [])
        )

    def _loads_json(self, value, fallback):
        if not value:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return fallback

    @staticmethod
    def _has_jobs_table(conn: sqlite3.Connection) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
        ).fetchone()
        return row is not None

    @classmethod
    def _job_is_archived(cls, conn: sqlite3.Connection, job_id: str) -> bool:
        if not cls._has_jobs_table(conn):
            return False
        row = conn.execute("SELECT archived FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return bool(row and row["archived"])


class BatchWriter:
    def __init__(
        self,
        *,
        job_id: str,
        platform: str,
        keyword: str,
        category: str = "",
        root: Path | None = None,
        batch_size: int | None = None,
        flush_seconds: float | None = None,
    ):
        self.job_id = job_id
        self.platform = platform
        self.keyword = keyword
        self.category = category
        self.root = root or (settings.outputs_dir / job_id)
        self.batch_dir = self.root / "batches"
        self.batch_dir.mkdir(parents=True, exist_ok=True)
        self.batch_size = max(1, batch_size or settings.batch_size)
        self.flush_seconds = max(1.0, flush_seconds or settings.batch_flush_seconds)
        self._lock = threading.Lock()
        existing_numbers = []
        for path in self.batch_dir.glob("batch_*.json"):
            try:
                existing_numbers.append(int(path.stem.removeprefix("batch_")))
            except ValueError:
                continue
        self._batch_no = max(existing_numbers, default=0)
        self._items: list[dict] = []
        self._comments_by_id: dict[str, dict] = {}
        self._started_at = monotonic()

    def add(self, contents: Iterable[dict], comments: Iterable[dict]) -> list[Path]:
        with self._lock:
            item_ids = set()
            for item in contents:
                item_id = content_identity(item, self.platform)
                if not item_id:
                    continue
                item_ids.add(item_id)
                self._items.append(item)

            if not item_ids:
                item_ids = {
                    content_identity(item, self.platform)
                    for item in self._items
                    if content_identity(item, self.platform)
                }

            if item_ids:
                for comment in comments:
                    if comment_content_identity(comment, self.platform) not in item_ids:
                        continue
                    comment_id = str(comment.get("comment_id") or id(comment))
                    self._comments_by_id[comment_id] = comment

            if len(self._items) >= self.batch_size:
                return [self._flush_locked("size")]
            return []

    def flush_due(self, *, force: bool = False) -> list[Path]:
        with self._lock:
            if not self._items:
                return []
            elapsed = monotonic() - self._started_at
            if not force and elapsed < self.flush_seconds:
                return []
            return [self._flush_locked("force" if force else "timeout")]

    def _flush_locked(self, reason: str) -> Path:
        self._batch_no += 1
        batch_name = f"batch_{self._batch_no:06d}.json"
        final_path = self.batch_dir / batch_name
        tmp_path = final_path.with_suffix(".json.tmp")
        payload = {
            "task_id": self.job_id,
            "platform": self.platform,
            "category": self.category,
            "keyword": self.keyword,
            "batch_no": self._batch_no,
            "created_at": utc_now(),
            "flush_reason": reason,
            "item_count": len(self._items),
            "comment_count": len(self._comments_by_id),
            "items": self._items,
            "comments": list(self._comments_by_id.values()),
        }
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(final_path)
        self._items = []
        self._comments_by_id = {}
        self._started_at = monotonic()
        return final_path


class IngestionStore:
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
                CREATE TABLE IF NOT EXISTS ingest_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    batch_path TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    item_count INTEGER NOT NULL DEFAULT 0,
                    processed_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    processed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS contents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    content_key TEXT NOT NULL,
                    note_id TEXT,
                    url TEXT,
                    title TEXT,
                    raw_item_path TEXT,
                    analyze_status TEXT NOT NULL DEFAULT 'queued',
                    result_path TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(platform, content_key)
                );

                CREATE TABLE IF NOT EXISTS content_matches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_id INTEGER NOT NULL,
                    task_id TEXT NOT NULL,
                    category TEXT,
                    keyword TEXT,
                    matched_at TEXT NOT NULL,
                    UNIQUE(content_id, task_id, category, keyword)
                );

                CREATE TABLE IF NOT EXISTS task_contents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    content_id INTEGER NOT NULL,
                    analyze_status TEXT NOT NULL DEFAULT 'queued',
                    raw_item_path TEXT,
                    result_path TEXT,
                    audit_result_id INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(task_id, content_id)
                );

                CREATE INDEX IF NOT EXISTS idx_task_contents_task_status
                ON task_contents(task_id, analyze_status);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(task_contents)").fetchall()}
            for column, definition in {
                "raw_item_path": "TEXT",
                "result_path": "TEXT",
                "audit_result_id": "INTEGER",
            }.items():
                if column not in columns:
                    conn.execute(f"ALTER TABLE task_contents ADD COLUMN {column} {definition}")
            conn.execute(
                """
                INSERT OR IGNORE INTO task_contents
                    (task_id, content_id, analyze_status, raw_item_path, result_path, audit_result_id, created_at, updated_at)
                SELECT
                    m.task_id,
                    m.content_id,
                    COALESCE(c.analyze_status, 'queued'),
                    c.raw_item_path,
                    c.result_path,
                    NULL,
                    MIN(m.matched_at),
                    MAX(m.matched_at)
                FROM content_matches m
                JOIN contents c ON c.id = m.content_id
                GROUP BY m.task_id, m.content_id
                """
            )

    def ingest_batch(self, batch_path: Path, raw_items_dir: Path) -> list[dict]:
        batch_path = batch_path.resolve()
        raw_items_dir.mkdir(parents=True, exist_ok=True)
        payload = json.loads(batch_path.read_text(encoding="utf-8"))
        platform = str(payload.get("platform") or "")
        task_id = str(payload.get("task_id") or "")
        category = str(payload.get("category") or "")
        keyword = str(payload.get("keyword") or "")
        comments = payload.get("comments") or []
        comments_by_content: dict[str, list[dict]] = {}
        for comment in comments:
            comments_by_content.setdefault(comment_content_identity(comment, platform), []).append(comment)

        queued: list[dict] = []
        now = utc_now()
        with self._lock, self._connect() as conn:
            existing_batch = conn.execute(
                "SELECT status FROM ingest_batches WHERE batch_path = ?",
                (str(batch_path),),
            ).fetchone()
            if existing_batch and existing_batch["status"] == "completed":
                return []

            conn.execute(
                """
                INSERT OR IGNORE INTO ingest_batches
                    (task_id, batch_path, status, item_count, processed_count, created_at)
                VALUES (?, ?, 'processing', ?, 0, ?)
                """,
                (task_id, str(batch_path), len(payload.get("items") or []), now),
            )
            conn.execute(
                "UPDATE ingest_batches SET status = 'processing' WHERE batch_path = ?",
                (str(batch_path),),
            )

            processed = 0
            for item in payload.get("items") or []:
                content_key = content_identity(item, platform)
                if not content_key:
                    continue
                item_keyword = str(item.get("source_keyword") or keyword)
                note_id = str(item.get("note_id") or item.get("aweme_id") or item.get("video_id") or "")
                url = str(item.get("note_url") or item.get("aweme_url") or item.get("video_url") or "")
                title = str(item.get("title") or item.get("desc") or "")
                content_comments = comments_by_content.get(content_key, [])
                raw_path = raw_items_dir / f"{safe_part(platform)}_{safe_part(content_key)}.json"
                raw_payload = {
                    "task_id": task_id,
                    "platform": platform,
                    "category": category,
                    "keyword": item_keyword,
                    "source_batch": str(batch_path),
                    "item": item,
                    "comments": content_comments,
                }

                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO contents
                        (platform, content_key, note_id, url, title, raw_item_path, analyze_status, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (platform, content_key, note_id, url, title, str(raw_path), now, now),
                )
                inserted = cursor.rowcount == 1
                row = conn.execute(
                    "SELECT id, analyze_status, raw_item_path, result_path FROM contents WHERE platform = ? AND content_key = ?",
                    (platform, content_key),
                ).fetchone()
                if not row:
                    continue
                content_id = int(row["id"])
                conn.execute(
                    """
                    UPDATE contents
                    SET last_seen_at = ?, url = COALESCE(NULLIF(?, ''), url), title = COALESCE(NULLIF(?, ''), title)
                    WHERE id = ?
                    """,
                    (now, url, title, content_id),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO content_matches
                        (content_id, task_id, category, keyword, matched_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (content_id, task_id, category, item_keyword, now),
                )
                conn.execute(
                    """
                    INSERT INTO task_contents
                        (task_id, content_id, analyze_status, raw_item_path, result_path, audit_result_id, created_at, updated_at)
                    VALUES (?, ?, 'queued', ?, NULL, NULL, ?, ?)
                    ON CONFLICT(task_id, content_id) DO UPDATE SET
                        raw_item_path = COALESCE(NULLIF(excluded.raw_item_path, ''), task_contents.raw_item_path),
                        updated_at = excluded.updated_at
                    """,
                    (task_id, content_id, str(raw_path), now, now),
                )
                processed += 1
                task_row = conn.execute(
                    """
                    SELECT analyze_status, raw_item_path, result_path
                    FROM task_contents
                    WHERE task_id = ? AND content_id = ?
                    """,
                    (task_id, content_id),
                ).fetchone()
                task_status = str(task_row["analyze_status"] or "queued") if task_row else "queued"
                task_raw_path = str(task_row["raw_item_path"] or row["raw_item_path"] or raw_path) if task_row else str(raw_path)

                raw_path.write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8")
                if task_status in {"queued", "failed"}:
                    queued.append({
                        "content_id": content_id,
                        "platform": platform,
                        "content_key": content_key,
                        "item": item,
                        "comments": content_comments,
                        "raw_item_path": task_raw_path,
                        "should_analyze": True,
                    })

            conn.execute(
                """
                UPDATE ingest_batches
                SET status = 'completed', processed_count = ?, processed_at = ?
                WHERE batch_path = ?
                """,
                (processed, utc_now(), str(batch_path)),
            )
        return queued

    def pending_for_task(self, task_id: str, limit: int = 0) -> list[dict]:
        sql = """
            SELECT
                c.id,
                c.platform,
                c.content_key,
                COALESCE(NULLIF(tc.raw_item_path, ''), c.raw_item_path) AS raw_item_path
            FROM task_contents tc
            JOIN contents c ON c.id = tc.content_id
            WHERE tc.task_id = ?
              AND tc.analyze_status IN ('queued', 'failed')
            GROUP BY c.id
            ORDER BY tc.created_at ASC
        """
        params: list[object] = [task_id]
        if limit > 0:
            sql += " LIMIT ?"
            params.append(limit)

        queued: list[dict] = []
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        for row in rows:
            raw_path = Path(str(row["raw_item_path"] or ""))
            if not raw_path.exists():
                continue
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
            queued.append({
                "content_id": int(row["id"]),
                "platform": str(row["platform"]),
                "content_key": str(row["content_key"]),
                "item": payload.get("item") or {},
                "comments": payload.get("comments") or [],
                "raw_item_path": str(raw_path),
                "should_analyze": True,
            })
        return queued

    def refs_for_task(self, task_id: str, limit: int = 0) -> list[dict]:
        memberships = self.selection_membership_for_task(task_id, limit=limit)
        refs: list[dict] = []
        for membership in memberships:
            raw_path = Path(str(membership["raw_item_path"] or ""))
            if not raw_path.exists():
                continue
            payload = json.loads(raw_path.read_text(encoding="utf-8"))
            refs.append(self._ref_from_payload(membership, payload, raw_path))
        return refs

    def selection_membership_for_task(self, task_id: str, limit: int = 0) -> list[dict]:
        """Return the frozen SQL selection without consulting raw payload files."""
        sql = """
            SELECT
                tc.id AS task_content_id,
                tc.content_id,
                COALESCE(c.platform, '') AS platform,
                COALESCE(c.content_key, '') AS content_key,
                tc.analyze_status,
                COALESCE(NULLIF(tc.raw_item_path, ''), c.raw_item_path) AS raw_item_path,
                tc.result_path,
                tc.audit_result_id
            FROM task_contents tc
            LEFT JOIN contents c ON c.id = tc.content_id
            WHERE tc.task_id = ?
            ORDER BY tc.id ASC
        """
        params: list[object] = [task_id]
        if limit > 0:
            sql += " LIMIT ?"
            params.append(limit)

        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            {
                "task_content_id": int(row["task_content_id"]),
                "content_id": int(row["content_id"]),
                "platform": str(row["platform"] or ""),
                "content_key": str(row["content_key"] or ""),
                "analyze_status": str(row["analyze_status"] or "queued"),
                "raw_item_path": str(row["raw_item_path"] or ""),
                "result_path": str(row["result_path"] or ""),
                "audit_result_id": row["audit_result_id"],
            }
            for row in rows
        ]

    def validated_refs_for_task(self, task_id: str, limit: int = 0) -> list[dict]:
        return self.validated_selection_for_task(task_id, limit=limit)[1]

    def validated_selection_for_task(
        self, task_id: str, limit: int = 0
    ) -> tuple[list[dict], list[dict]]:
        """Hydrate every selected SQL member or fail closed with a stable error."""
        memberships = self.selection_membership_for_task(task_id, limit=limit)
        refs: list[dict] = []
        for membership in memberships:
            task_content_id = int(membership["task_content_id"])
            raw_item_path = str(membership["raw_item_path"] or "")
            if not raw_item_path:
                raise SelectedContentPayloadUnavailableError(
                    task_content_id, "raw_item_path is empty"
                )
            raw_path = Path(raw_item_path)
            try:
                is_file = raw_path.is_file()
            except OSError as exc:
                raise SelectedContentPayloadUnavailableError(
                    task_content_id,
                    f"raw item cannot be inspected: {exc.__class__.__name__}",
                ) from exc
            if not is_file:
                raise SelectedContentPayloadUnavailableError(
                    task_content_id, "raw item is not a readable regular file"
                )
            try:
                raw_text = raw_path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise SelectedContentPayloadUnavailableError(
                    task_content_id, f"raw item cannot be read: {exc.__class__.__name__}"
                ) from exc
            try:
                payload = json.loads(raw_text)
            except (json.JSONDecodeError, TypeError) as exc:
                raise SelectedContentPayloadUnavailableError(
                    task_content_id, "raw item is not valid JSON"
                ) from exc
            if not isinstance(payload, dict):
                raise SelectedContentPayloadUnavailableError(
                    task_content_id, "raw payload root is not an object"
                )
            item = payload.get("item")
            if not isinstance(item, dict):
                raise SelectedContentPayloadUnavailableError(
                    task_content_id, "raw payload item is not an object"
                )
            platform = str(membership["platform"] or "")
            content_key = str(membership["content_key"] or "")
            if not platform or not content_key:
                raise SelectedContentPayloadUnavailableError(
                    task_content_id, "SQL content identity is incomplete"
                )
            if content_identity(item, platform) != content_key:
                raise SelectedContentPayloadUnavailableError(
                    task_content_id, "raw payload identity does not match SQL selection"
                )
            refs.append(self._ref_from_payload(membership, payload, raw_path))
        return memberships, refs

    @staticmethod
    def _ref_from_payload(membership: dict, payload: dict, raw_path: Path) -> dict:
        item = payload.get("item")
        comments = payload.get("comments")
        return {
            "task_content_id": int(membership["task_content_id"]),
            "content_id": int(membership["content_id"]),
            "platform": str(membership["platform"]),
            "content_key": str(membership["content_key"]),
            "item": item if isinstance(item, dict) else {},
            "comments": comments if isinstance(comments, list) else [],
            "raw_item_path": str(raw_path),
            "result_path": str(membership["result_path"] or ""),
            "audit_result_id": membership["audit_result_id"],
            "analyze_status": str(membership["analyze_status"] or "queued"),
            "should_analyze": str(membership["analyze_status"] or "queued")
            in {"queued", "failed"},
        }

    def stats_for_task(self, task_id: str) -> dict:
        with self._lock, self._connect() as conn:
            status_rows = conn.execute(
                """
                SELECT tc.analyze_status, COUNT(DISTINCT tc.content_id) AS count
                FROM task_contents tc
                WHERE tc.task_id = ?
                GROUP BY tc.analyze_status
                """,
                (task_id,),
            ).fetchall()
            batch_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS batch_count,
                    COALESCE(SUM(processed_count), 0) AS processed_count,
                    COALESCE(SUM(item_count), 0) AS item_count
                FROM ingest_batches
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()

        by_status = {str(row["analyze_status"] or "unknown"): int(row["count"] or 0) for row in status_rows}
        ingested = sum(by_status.values())
        queued = by_status.get("queued", 0)
        analyzing = by_status.get("analyzing", 0)
        completed = by_status.get("completed", 0)
        failed = by_status.get("failed", 0)
        pending = queued + failed
        return {
            "ingested_count": ingested,
            "queued_analysis_count": queued,
            "pending_analysis_count": pending,
            "analyzing_count": analyzing,
            "completed_analysis_count": completed,
            "failed_analysis_count": failed,
            "analysis_status_counts": by_status,
            "batch_count": int(batch_row["batch_count"] or 0) if batch_row else 0,
            "batch_item_count": int(batch_row["item_count"] or 0) if batch_row else 0,
            "batch_processed_count": int(batch_row["processed_count"] or 0) if batch_row else 0,
        }

    def mark_content_status(
        self,
        platform: str,
        content_key: str,
        status: str,
        result_path: str = "",
        *,
        task_id: str = "",
        audit_result_id: int | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE contents
                SET analyze_status = ?, result_path = COALESCE(NULLIF(?, ''), result_path), last_seen_at = ?
                WHERE platform = ? AND content_key = ?
                """,
                (status, result_path, utc_now(), platform, content_key),
            )
            if task_id:
                row = conn.execute(
                    "SELECT id FROM contents WHERE platform = ? AND content_key = ?",
                    (platform, content_key),
                ).fetchone()
                if row:
                    conn.execute(
                        """
                        UPDATE task_contents
                        SET analyze_status = ?,
                            result_path = COALESCE(NULLIF(?, ''), result_path),
                            audit_result_id = COALESCE(?, audit_result_id),
                            updated_at = ?
                        WHERE task_id = ? AND content_id = ?
                            """,
                        (status, result_path, audit_result_id, utc_now(), task_id, int(row["id"])),
                    )

    def mark_task_content_status(
        self,
        task_id: str,
        platform: str,
        content_key: str,
        status: str,
        result_path: str = "",
        audit_result_id: int | None = None,
    ) -> None:
        if not task_id or not content_key:
            return
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM contents WHERE platform = ? AND content_key = ?",
                (platform, content_key),
            ).fetchone()
            if not row:
                return
            conn.execute(
                """
                UPDATE task_contents
                SET analyze_status = ?,
                    result_path = COALESCE(NULLIF(?, ''), result_path),
                    audit_result_id = COALESCE(?, audit_result_id),
                    updated_at = ?
                WHERE task_id = ? AND content_id = ?
                """,
                (status, result_path, audit_result_id, utc_now(), task_id, int(row["id"])),
            )

    def reset_analyzing_for_task(self, task_id: str) -> int:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE task_contents
                SET analyze_status = 'queued', updated_at = ?
                WHERE task_id = ?
                  AND analyze_status = 'analyzing'
                """,
                (utc_now(), task_id),
            )
            return int(cursor.rowcount or 0)
