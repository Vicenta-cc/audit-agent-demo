from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .config import settings


def utc_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


DEFAULT_POLICY_CONFIGS = [
    {
        "id": "policy_prohibited",
        "name": "违禁引流研判方案",
        "description": "识别站外导流、私域口令、二维码、联系方式和评论区聚集。",
        "published_version": "v1.8",
        "config": {
            "library_ids": ["prohibited"],
            "capabilities": ["text", "ocr", "asr", "vision", "comment"],
            "scoring_template": "balanced",
            "rule_snapshot": {"thresholds": {"high": 80, "medium": 60, "review": 40}},
            "outputs": ["风险内容", "标题/正文/评论", "图片/视频文字", "语音转写", "评论/弹幕", "询证材料"],
        },
    },
    {
        "id": "policy_soft",
        "name": "软色情研判方案",
        "description": "识别擦边、性暗示、低俗交易导流和评论区求资源。",
        "published_version": "v2.1",
        "config": {
            "library_ids": ["soft"],
            "capabilities": ["text", "asr", "vision", "comment"],
            "scoring_template": "vision_first",
            "rule_snapshot": {"thresholds": {"high": 80, "medium": 60, "review": 40}},
            "outputs": ["风险内容", "标题/正文/评论", "语音转写", "图片/视频画面", "评论/弹幕", "询证材料"],
        },
    },
    {
        "id": "policy_terror",
        "name": "暴恐研判方案",
        "description": "识别暴恐宣传、极端主义、武器展示、组织招募和行动号召。",
        "published_version": "v1.2",
        "config": {
            "library_ids": ["terror"],
            "capabilities": ["text", "ocr", "asr", "vision", "comment"],
            "scoring_template": "strict",
            "rule_snapshot": {"thresholds": {"high": 80, "medium": 60, "review": 40}},
            "outputs": ["风险内容", "标题/正文/评论", "图片/视频文字", "语音转写", "图片/视频画面", "询证材料"],
        },
    },
    {
        "id": "policy_drug",
        "name": "涉毒研判方案",
        "description": "识别涉毒交易、吸贩毒暗号、同城邀约、违禁药物导流和接头互动。",
        "published_version": "v1.0",
        "config": {
            "library_ids": ["drug"],
            "capabilities": ["text", "ocr", "asr", "vision", "comment"],
            "scoring_template": "strict",
            "rule_snapshot": {"thresholds": {"high": 80, "medium": 60, "review": 40}},
            "outputs": ["风险内容", "标题/正文/评论", "图片/视频文字", "语音转写", "评论/弹幕", "询证材料"],
        },
    },
    {
        "id": "policy_gambling",
        "name": "赌博博彩研判方案",
        "description": "识别投注平台、盘口赔率、上分提现、代理推广和群聊导流。",
        "published_version": "v1.0",
        "config": {
            "library_ids": ["gambling"],
            "capabilities": ["text", "ocr", "asr", "vision", "comment"],
            "scoring_template": "balanced",
            "rule_snapshot": {"thresholds": {"high": 80, "medium": 60, "review": 40}},
            "outputs": ["风险内容", "标题/正文/评论", "图片/视频文字", "语音转写", "评论/弹幕", "询证材料"],
        },
    },
    {
        "id": "policy_fraud",
        "name": "诈骗研判方案",
        "description": "识别刷单返利、投资理财、虚假福利、仿冒客服和私域收割。",
        "published_version": "v1.0",
        "config": {
            "library_ids": ["fraud"],
            "capabilities": ["text", "ocr", "asr", "vision", "comment"],
            "scoring_template": "balanced",
            "rule_snapshot": {"thresholds": {"high": 80, "medium": 60, "review": 40}},
            "outputs": ["风险内容", "标题/正文/评论", "图片/视频文字", "语音转写", "评论/弹幕", "询证材料"],
        },
    },
    {
        "id": "policy_hate",
        "name": "仇恨歧视研判方案",
        "description": "识别针对群体身份的侮辱、排斥、煽动攻击和组织性网暴。",
        "published_version": "v1.0",
        "config": {
            "library_ids": ["hate"],
            "capabilities": ["text", "ocr", "asr", "vision", "comment"],
            "scoring_template": "balanced",
            "rule_snapshot": {"thresholds": {"high": 80, "medium": 60, "review": 40}},
            "outputs": ["风险内容", "标题/正文/评论", "图片/视频文字", "语音转写", "评论/弹幕", "询证材料"],
        },
    },
]


class AuditPolicyStore:
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
                CREATE TABLE IF NOT EXISTS audit_policies (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    draft_version TEXT NOT NULL DEFAULT 'draft',
                    published_version TEXT NOT NULL DEFAULT '',
                    config_json TEXT NOT NULL DEFAULT '{}',
                    published_config_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_audit_policies_status
                ON audit_policies(status, updated_at);
                """
            )
            count = conn.execute("SELECT COUNT(*) AS count FROM audit_policies").fetchone()
            if int(count["count"] or 0) == 0:
                self._seed_defaults(conn)

    def _seed_defaults(self, conn: sqlite3.Connection) -> None:
        now = utc_now()
        for policy in DEFAULT_POLICY_CONFIGS:
            config = dict(policy["config"])
            conn.execute(
                """
                INSERT INTO audit_policies (
                    id, name, description, status, draft_version, published_version,
                    config_json, published_config_json, created_at, updated_at, published_at
                )
                VALUES (?, ?, ?, 'published', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy["id"],
                    policy["name"],
                    policy["description"],
                    policy["published_version"],
                    policy["published_version"],
                    json.dumps(config, ensure_ascii=False),
                    json.dumps(config, ensure_ascii=False),
                    now,
                    now,
                    now,
                ),
            )

    def list(self, include_drafts: bool = True) -> list[dict]:
        with self._lock, self._connect() as conn:
            where = "" if include_drafts else "WHERE published_version != ''"
            rows = conn.execute(
                f"SELECT * FROM audit_policies {where} ORDER BY updated_at DESC"
            ).fetchall()
            return [self._row_to_policy(row) for row in rows]

    def get(self, policy_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM audit_policies WHERE id = ?", (policy_id,)).fetchone()
            return self._row_to_policy(row) if row else None

    def create(self, **kwargs) -> dict:
        now = utc_now()
        policy_id = str(kwargs.get("id") or uuid4().hex[:12])
        name = str(kwargs.get("name") or "未命名研判方案").strip()
        description = str(kwargs.get("description") or "").strip()
        config = kwargs.get("config") if isinstance(kwargs.get("config"), dict) else {}
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_policies (
                    id, name, description, status, draft_version, published_version,
                    config_json, published_config_json, created_at, updated_at, published_at
                )
                VALUES (?, ?, ?, 'draft', 'draft', '', ?, '{}', ?, ?, NULL)
                """,
                (policy_id, name, description, json.dumps(config, ensure_ascii=False), now, now),
            )
            row = conn.execute("SELECT * FROM audit_policies WHERE id = ?", (policy_id,)).fetchone()
            return self._row_to_policy(row)

    def update(self, policy_id: str, **kwargs) -> dict:
        allowed = {"name", "description", "config"}
        fields = []
        values = []
        for key, value in kwargs.items():
            if key not in allowed:
                continue
            column = "config_json" if key == "config" else key
            fields.append(f"{column} = ?")
            values.append(json.dumps(value or {}, ensure_ascii=False) if key == "config" else value)
        fields.append("status = ?")
        values.append("draft")
        fields.append("draft_version = ?")
        values.append("draft")
        fields.append("updated_at = ?")
        values.append(utc_now())
        values.append(policy_id)
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE audit_policies SET {', '.join(fields)} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM audit_policies WHERE id = ?", (policy_id,)).fetchone()
            if not row:
                raise KeyError(policy_id)
            return self._row_to_policy(row)

    def publish(self, policy_id: str) -> dict:
        now = utc_now()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM audit_policies WHERE id = ?", (policy_id,)).fetchone()
            if not row:
                raise KeyError(policy_id)
            next_version = self._next_version(row["published_version"])
            conn.execute(
                """
                UPDATE audit_policies
                SET status = 'published',
                    draft_version = ?,
                    published_version = ?,
                    published_config_json = config_json,
                    updated_at = ?,
                    published_at = ?
                WHERE id = ?
                """,
                (next_version, next_version, now, now, policy_id),
            )
            row = conn.execute("SELECT * FROM audit_policies WHERE id = ?", (policy_id,)).fetchone()
            return self._row_to_policy(row)

    @staticmethod
    def config_for_use(policy: dict) -> dict:
        config = policy.get("published_config") or policy.get("config") or {}
        return dict(config) if isinstance(config, dict) else {}

    @staticmethod
    def version_for_use(policy: dict) -> str:
        return str(policy.get("published_version") or policy.get("draft_version") or "draft")

    def _row_to_policy(self, row: sqlite3.Row) -> dict:
        policy = dict(row)
        policy["config"] = self._loads_json(policy.pop("config_json", None), {})
        policy["published_config"] = self._loads_json(policy.pop("published_config_json", None), {})
        return policy

    def _next_version(self, current: str | None) -> str:
        text = str(current or "").strip().lstrip("vV")
        if not text:
            return "v1.0"
        parts = text.split(".")
        try:
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            return "v1.0"
        return f"v{major}.{minor + 1}"

    def _loads_json(self, value, fallback):
        if not value:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return fallback


class TaskAuditConfigRevisionStore:
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
                CREATE TABLE IF NOT EXISTS task_audit_config_revisions (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    source_policy_id TEXT,
                    source_policy_name TEXT,
                    source_policy_version TEXT,
                    audit_config_json TEXT NOT NULL DEFAULT '{}',
                    knowledge_package_snapshots_json TEXT NOT NULL DEFAULT '[]',
                    rule_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    prompt_profile_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    config_hash TEXT NOT NULL,
                    effective_from TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    created_by TEXT,
                    UNIQUE(job_id, version)
                );

                CREATE INDEX IF NOT EXISTS idx_task_config_revisions_job
                ON task_audit_config_revisions(job_id, version DESC);

                CREATE INDEX IF NOT EXISTS idx_task_config_revisions_hash
                ON task_audit_config_revisions(config_hash);
                """
            )

    def create(self, *, job_id: str, created_by: str = "", **payload) -> dict:
        now = utc_now()
        revision_id = uuid4().hex[:12]
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM task_audit_config_revisions WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            version = int(row["version"] or 0) + 1
            conn.execute(
                """
                INSERT INTO task_audit_config_revisions (
                    id, job_id, version, source_policy_id, source_policy_name, source_policy_version,
                    audit_config_json, knowledge_package_snapshots_json, rule_snapshot_json,
                    prompt_profile_snapshot_json, config_hash, effective_from, created_at, created_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    job_id,
                    version,
                    payload.get("source_policy_id"),
                    payload.get("source_policy_name"),
                    payload.get("source_policy_version"),
                    json.dumps(payload.get("audit_config") or {}, ensure_ascii=False),
                    json.dumps(payload.get("knowledge_package_snapshots") or [], ensure_ascii=False),
                    json.dumps(payload.get("rule_snapshot") or {}, ensure_ascii=False),
                    json.dumps(payload.get("prompt_profile_snapshot") or {}, ensure_ascii=False),
                    str(payload.get("config_hash") or ""),
                    now,
                    now,
                    created_by,
                ),
            )
            row = conn.execute(
                "SELECT * FROM task_audit_config_revisions WHERE id = ?",
                (revision_id,),
            ).fetchone()
            return self._row_to_revision(row)

    def list_for_job(self, job_id: str) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM task_audit_config_revisions WHERE job_id = ? ORDER BY version DESC",
                (job_id,),
            ).fetchall()
            return [self._row_to_revision(row) for row in rows]

    def get(self, revision_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM task_audit_config_revisions WHERE id = ?",
                (revision_id,),
            ).fetchone()
            return self._row_to_revision(row) if row else None

    def get_for_job(self, job_id: str, revision_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM task_audit_config_revisions
                WHERE job_id = ? AND id = ?
                """,
                (job_id, revision_id),
            ).fetchone()
            return self._row_to_revision(row) if row else None

    def _row_to_revision(self, row: sqlite3.Row) -> dict:
        revision = dict(row)
        revision["audit_config"] = self._loads_json(revision.pop("audit_config_json", None), {})
        revision["knowledge_package_snapshots"] = self._loads_json(
            revision.pop("knowledge_package_snapshots_json", None),
            [],
        )
        revision["rule_snapshot"] = self._loads_json(revision.pop("rule_snapshot_json", None), {})
        revision["prompt_profile_snapshot"] = self._loads_json(
            revision.pop("prompt_profile_snapshot_json", None),
            {},
        )
        return revision

    def _loads_json(self, value, fallback):
        if not value:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return fallback
