from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from backend.rulesets.gambling_v1 import GAMBLING_RULESET_REVISION_ID

from .config import settings
from .library_references import (
    policy_reference_scopes,
    referenced_library_ids,
)


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
        "published_version": "v2.0",
        "config": {
            "library_ids": ["gambling"],
            "ruleset_revision_id": GAMBLING_RULESET_REVISION_ID,
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
        "name": "民族意识形态风险研判方案",
        "description": "识别民族、宗教、地域等群体身份相关的排斥污名、仇恨歧视、煽动攻击和组织性网暴。",
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


class AuditPolicyRevisionConflictError(RuntimeError):
    pass


class AuditPolicyLibraryReferenceConflictError(AuditPolicyRevisionConflictError):
    def __init__(self, missing_library_ids: list[str] | tuple[str, ...]) -> None:
        self.missing_library_ids = tuple(missing_library_ids)
        joined = ", ".join(self.missing_library_ids)
        super().__init__(f"AuditPolicy references missing lexicon categories: {joined}")


class AuditPolicyStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (settings.data_dir / "audit_index.sqlite3")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_lexicon_store()
        self._init_db()

    def _ensure_lexicon_store(self) -> None:
        """Ensure shared policy/lexicon databases have the category table."""
        with sqlite3.connect(self.db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'lexicon_categories'"
            ).fetchone()
        if exists:
            return
        from .lexicon_store import LexiconStore

        LexiconStore(self.db_path)

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

                CREATE TABLE IF NOT EXISTS audit_policy_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            seeded = conn.execute(
                "SELECT value FROM audit_policy_metadata WHERE key = 'defaults_seeded'"
            ).fetchone()
            if not seeded:
                count = conn.execute("SELECT COUNT(*) AS count FROM audit_policies").fetchone()
                if int(count["count"] or 0) == 0:
                    self._seed_defaults(conn)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO audit_policy_metadata (key, value)
                    VALUES ('defaults_seeded', ?)
                    """,
                    (utc_now(),),
                )
            self._sync_default_display_names(conn)

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

    def _sync_default_display_names(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE audit_policies
            SET
                name = '民族意识形态风险研判方案',
                description = CASE
                    WHEN description = '识别针对群体身份的侮辱、排斥、煽动攻击和组织性网暴。'
                    THEN '识别民族、宗教、地域等群体身份相关的排斥污名、仇恨歧视、煽动攻击和组织性网暴。'
                    ELSE description
                END,
                updated_at = ?
            WHERE id = 'policy_hate'
              AND name = '仇恨歧视研判方案'
            """,
            (utc_now(),),
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
            conn.execute("BEGIN IMMEDIATE")
            self._raise_if_missing_library_ids(conn, config)
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
            conn.execute("BEGIN IMMEDIATE")
            if "config" in kwargs:
                config = kwargs.get("config") if isinstance(kwargs.get("config"), dict) else {}
                self._raise_if_missing_library_ids(conn, config)
            conn.execute(f"UPDATE audit_policies SET {', '.join(fields)} WHERE id = ?", values)
            row = conn.execute("SELECT * FROM audit_policies WHERE id = ?", (policy_id,)).fetchone()
            if not row:
                raise KeyError(policy_id)
            return self._row_to_policy(row)

    def publish(self, policy_id: str, *, expected_draft_hash: str) -> dict:
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM audit_policies WHERE id = ?", (policy_id,)).fetchone()
            if not row:
                raise KeyError(policy_id)
            current_hash = self._draft_hash_from_row(row)
            if not expected_draft_hash or current_hash != expected_draft_hash:
                raise AuditPolicyRevisionConflictError(
                    "AuditPolicy draft changed after validation"
                )
            self._raise_if_missing_library_ids(
                conn,
                self._loads_json_value(row["config_json"], {}),
            )
            next_version = self._next_version(row["published_version"])
            updated = conn.execute(
                """
                UPDATE audit_policies
                SET status = 'published',
                    draft_version = ?,
                    published_version = ?,
                    published_config_json = config_json,
                    updated_at = ?,
                    published_at = ?
                WHERE id = ? AND name = ? AND description = ?
                  AND config_json = ? AND updated_at = ?
                """,
                (
                    next_version,
                    next_version,
                    now,
                    now,
                    policy_id,
                    row["name"],
                    row["description"],
                    row["config_json"],
                    row["updated_at"],
                ),
            )
            if updated.rowcount != 1:
                raise AuditPolicyRevisionConflictError(
                    "AuditPolicy draft changed after validation"
                )
            row = conn.execute("SELECT * FROM audit_policies WHERE id = ?", (policy_id,)).fetchone()
            return self._row_to_policy(row)

    @classmethod
    def draft_hash(cls, policy: dict) -> str:
        payload = {
            "name": str(policy.get("name") or ""),
            "description": str(policy.get("description") or ""),
            "config": policy.get("config") if isinstance(policy.get("config"), dict) else {},
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _draft_hash_from_row(cls, row: sqlite3.Row) -> str:
        return cls.draft_hash({
            "name": row["name"],
            "description": row["description"],
            "config": cls._loads_json_value(row["config_json"], {}),
        })

    def delete(self, policy_id: str) -> dict:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM audit_policies WHERE id = ?", (policy_id,)).fetchone()
            if not row:
                raise KeyError(policy_id)
            policy = self._row_to_policy(row)
            conn.execute("DELETE FROM audit_policies WHERE id = ?", (policy_id,))
            return policy

    def remove_library_references(self, library_id: str) -> int:
        target = str(library_id or "").strip()
        if not target:
            return 0
        affected = 0
        now = utc_now()
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM audit_policies").fetchall()
            for row in rows:
                draft = self._loads_json(row["config_json"], {})
                new_draft, draft_changed = self._without_library(draft, target)
                if not draft_changed:
                    continue
                affected += 1
                conn.execute(
                    """
                    UPDATE audit_policies
                    SET config_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        json.dumps(new_draft, ensure_ascii=False),
                        now,
                        row["id"],
                    ),
                )
        return affected

    def find_library_references(self, library_id: str) -> list[dict]:
        target = str(library_id or "").strip()
        if not target:
            return []
        references: list[dict] = []
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT * FROM audit_policies").fetchall()
        for row in rows:
            reference = policy_reference_scopes(
                policy_id=row["id"],
                policy_name=row["name"],
                draft_config=self._loads_json(row["config_json"], {}),
                published_config=self._loads_json(row["published_config_json"], {}),
                library_id=target,
            )
            if reference:
                references.append(reference)
        return references

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
        return self._loads_json_value(value, fallback)

    @staticmethod
    def _loads_json_value(value, fallback):
        if not value:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return fallback

    def _without_library(self, config: dict, library_id: str) -> tuple[dict, bool]:
        if not isinstance(config, dict):
            return {}, False
        out = dict(config)
        changed = False
        libs = out.get("library_ids")
        if isinstance(libs, list):
            kept = [item for item in libs if str(item) != library_id]
            changed = len(kept) != len(libs)
            out["library_ids"] = kept
        snapshot = out.get("rule_snapshot")
        if isinstance(snapshot, dict):
            new_snapshot = dict(snapshot)
            rules = new_snapshot.get("scoring_rules")
            if isinstance(rules, list):
                kept_rules = [
                    rule for rule in rules
                    if not isinstance(rule, dict) or str(rule.get("library_id") or "") != library_id
                ]
                if len(kept_rules) != len(rules):
                    new_snapshot["scoring_rules"] = kept_rules
                    changed = True
            out["rule_snapshot"] = new_snapshot
        return out, changed

    def _raise_if_missing_library_ids(
        self,
        conn: sqlite3.Connection,
        config: object,
    ) -> None:
        library_ids = referenced_library_ids(config)
        if not library_ids:
            return
        placeholders = ", ".join("?" for _ in library_ids)
        try:
            rows = conn.execute(
                f"SELECT id FROM lexicon_categories WHERE id IN ({placeholders})",
                tuple(library_ids),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            rows = []
        existing = {str(row["id"]) for row in rows}
        missing = [library_id for library_id in library_ids if library_id not in existing]
        if missing:
            raise AuditPolicyLibraryReferenceConflictError(missing)


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

    def create_or_get(self, *, job_id: str, created_by: str = "", **payload) -> dict:
        """Idempotently persist one revision identity per Job/config hash."""

        config_hash = str(payload.get("config_hash") or "").strip()
        if not config_hash:
            raise ValueError("config_hash is required for idempotent revision creation")
        digest = hashlib.sha256(f"{job_id}\0{config_hash}".encode("utf-8")).hexdigest()
        revision_id = f"audit-config-revision:{digest[:32]}"
        now = utc_now()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT * FROM task_audit_config_revisions
                WHERE job_id = ? AND config_hash = ?
                ORDER BY version, rowid
                LIMIT 1
                """,
                (job_id, config_hash),
            ).fetchone()
            if existing is not None:
                return self._row_to_revision(existing)
            row = conn.execute(
                """
                SELECT COALESCE(MAX(version), 0) AS version
                FROM task_audit_config_revisions WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            version = int(row["version"] or 0) + 1
            conn.execute(
                """
                INSERT INTO task_audit_config_revisions (
                    id, job_id, version, source_policy_id, source_policy_name,
                    source_policy_version, audit_config_json,
                    knowledge_package_snapshots_json, rule_snapshot_json,
                    prompt_profile_snapshot_json, config_hash, effective_from,
                    created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    job_id,
                    version,
                    payload.get("source_policy_id"),
                    payload.get("source_policy_name"),
                    payload.get("source_policy_version"),
                    json.dumps(payload.get("audit_config") or {}, ensure_ascii=False),
                    json.dumps(
                        payload.get("knowledge_package_snapshots") or [],
                        ensure_ascii=False,
                    ),
                    json.dumps(payload.get("rule_snapshot") or {}, ensure_ascii=False),
                    json.dumps(
                        payload.get("prompt_profile_snapshot") or {},
                        ensure_ascii=False,
                    ),
                    config_hash,
                    now,
                    now,
                    created_by,
                ),
            )
            created = conn.execute(
                "SELECT * FROM task_audit_config_revisions WHERE id = ?",
                (revision_id,),
            ).fetchone()
            return self._row_to_revision(created)

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
