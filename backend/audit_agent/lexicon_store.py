from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .config import settings
from .knowledge_packages import get_default_knowledge_package
from .prompts import DEFAULT_PROMPT_PROFILES, render_prompt_profile_preview


DEFAULT_LEXICON = [
    {
        "id": "soft",
        "title": "软色情词库",
        "keywords": [
            ("泳装", "模糊", "全平台", "中", True),
            ("私拍", "精确", "小红书", "高", True),
            ("擦边", "模糊", "小红书", "中", True),
        ],
    },
    {
        "id": "gambling",
        "title": "赌博黑话词库",
        "keywords": [
            ("上分", "模糊", "全平台", "高", True),
            ("盘口", "精确", "抖音", "高", True),
            ("回血", "模糊", "快手", "中", False),
        ],
    },
    {
        "id": "fraud",
        "title": "涉诈话术词库",
        "keywords": [
            ("刷流水", "精确", "全平台", "高", True),
            ("认证金", "模糊", "小红书", "高", True),
            ("返利", "模糊", "全平台", "中", True),
        ],
    },
    {
        "id": "minority",
        "title": "民族意识形态风险词库",
        "keywords": [
            ("维语待标注", "tag", "全平台", "中", True),
            ("敏感短语 A", "模糊", "小红书", "高", False),
            ("敏感短语 B", "正则", "抖音", "中", True),
        ],
    },
    {
        "id": "hate",
        "title": "民族意识形态风险知识包",
        "keywords": [
            ("群体攻击", "模糊", "全平台", "高", True),
            ("驱逐", "模糊", "全平台", "高", True),
            ("排斥", "模糊", "全平台", "中", True),
        ],
    },
    {
        "id": "terror",
        "title": "暴恐风险知识包",
        "keywords": [
            ("武器", "模糊", "全平台", "中", True),
            ("爆炸", "模糊", "全平台", "高", True),
            ("行动号召", "模糊", "全平台", "高", True),
        ],
    },
    {
        "id": "drug",
        "title": "涉毒风险知识包",
        "keywords": [
            ("同城", "模糊", "全平台", "中", True),
            ("货", "模糊", "全平台", "中", True),
            ("邮寄", "模糊", "全平台", "高", True),
        ],
    },
    {
        "id": "prohibited",
        "title": "违禁引流知识包",
        "keywords": [
            ("私信", "模糊", "全平台", "中", True),
            ("看主页", "模糊", "全平台", "中", True),
            ("加群", "模糊", "全平台", "高", True),
        ],
    },
]


class LexiconStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (settings.data_dir / "audit_index.sqlite3")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        self._seed_defaults()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS lexicon_categories (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    risk_label TEXT NOT NULL DEFAULT '',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS lexicon_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id TEXT NOT NULL,
                    keyword TEXT NOT NULL,
                    match_type TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    risk_level TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    hit_count_7d INTEGER NOT NULL DEFAULT 0,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(category_id, keyword, platform, match_type)
                );

                CREATE INDEX IF NOT EXISTS idx_lexicon_keywords_category ON lexicon_keywords(category_id, enabled);

                CREATE TABLE IF NOT EXISTS lexicon_prompt_profiles (
                    category_id TEXT PRIMARY KEY,
                    audit_goal TEXT NOT NULL DEFAULT '',
                    evidence_rules TEXT NOT NULL DEFAULT '',
                    fusion_rules TEXT NOT NULL DEFAULT '',
                    image_prompt TEXT NOT NULL DEFAULT '',
                    frame_prompt TEXT NOT NULL DEFAULT '',
                    fusion_prompt_template TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 1,
                    prompt_version TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS lexicon_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(lexicon_prompt_profiles)").fetchall()}
            if "image_prompt" not in columns:
                conn.execute("ALTER TABLE lexicon_prompt_profiles ADD COLUMN image_prompt TEXT NOT NULL DEFAULT ''")
            if "frame_prompt" not in columns:
                conn.execute("ALTER TABLE lexicon_prompt_profiles ADD COLUMN frame_prompt TEXT NOT NULL DEFAULT ''")
            if "fusion_prompt_template" not in columns:
                conn.execute("ALTER TABLE lexicon_prompt_profiles ADD COLUMN fusion_prompt_template TEXT NOT NULL DEFAULT ''")
            category_columns = {row["name"] for row in conn.execute("PRAGMA table_info(lexicon_categories)").fetchall()}
            if "risk_label" not in category_columns:
                conn.execute("ALTER TABLE lexicon_categories ADD COLUMN risk_label TEXT NOT NULL DEFAULT ''")

    def _seed_defaults(self) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            seeded = conn.execute(
                "SELECT value FROM lexicon_metadata WHERE key = 'defaults_seeded'"
            ).fetchone()
            if seeded:
                self._sync_default_display_names(conn, now)
                return
            count = conn.execute("SELECT COUNT(*) AS count FROM lexicon_categories").fetchone()
            if int(count["count"] or 0) > 0:
                self._sync_default_display_names(conn, now)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO lexicon_metadata (key, value)
                    VALUES ('defaults_seeded', ?)
                    """,
                    (now,),
                )
                return
            for order, category in enumerate(DEFAULT_LEXICON):
                conn.execute(
                    """
                    INSERT INTO lexicon_categories (id, title, risk_label, sort_order, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (category["id"], category["title"], self._default_risk_label(category["id"], category["title"]), order, now, now),
                )
                for keyword, match_type, platform, risk_level, enabled in category["keywords"]:
                    conn.execute(
                        """
                        INSERT INTO lexicon_keywords
                            (category_id, keyword, match_type, platform, risk_level, enabled, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(category_id, keyword, platform, match_type) DO NOTHING
                        """,
                        (category["id"], keyword, match_type, platform, risk_level, 1 if enabled else 0, now, now),
                    )
                    conn.execute(
                        """
                        INSERT INTO lexicon_keywords
                            (category_id, keyword, match_type, platform, risk_level, enabled, created_at, updated_at)
                        VALUES (?, ?, '平台搜索词', ?, ?, ?, ?, ?)
                        ON CONFLICT(category_id, keyword, platform, match_type) DO NOTHING
                        """,
                        (category["id"], keyword, platform, risk_level, 1 if enabled else 0, now, now),
                    )
                conn.execute(
                    """
                    UPDATE lexicon_categories
                    SET risk_label = CASE WHEN risk_label = '' THEN ? ELSE risk_label END
                    WHERE id = ?
                    """,
                    (self._default_risk_label(category["id"], category["title"]), category["id"]),
                )
            self._seed_prompt_profiles(conn, now)
            conn.execute(
                """
                INSERT OR REPLACE INTO lexicon_metadata (key, value)
                VALUES ('defaults_seeded', ?)
                """,
                (now,),
            )

    def _sync_default_display_names(self, conn: sqlite3.Connection, now: str) -> None:
        conn.execute(
            """
            UPDATE lexicon_categories
            SET
                title = CASE
                    WHEN title IN ('民族宗教仇恨风险知识包', '仇恨歧视词库') THEN '民族意识形态风险知识包'
                    ELSE title
                END,
                risk_label = CASE
                    WHEN risk_label IN ('', '仇恨歧视', '民族宗教仇恨风险', '民族语言与宗教仇恨', '民族语言') THEN '民族意识形态风险'
                    ELSE risk_label
                END,
                updated_at = ?
            WHERE id = 'hate'
              AND (
                title IN ('民族宗教仇恨风险知识包', '仇恨歧视词库')
                OR risk_label IN ('', '仇恨歧视', '民族宗教仇恨风险', '民族语言与宗教仇恨', '民族语言')
              )
            """,
            (now,),
        )
        conn.execute(
            """
            UPDATE lexicon_categories
            SET
                title = CASE
                    WHEN title IN ('民族语言词库', '民族语言与宗教仇恨风险知识包') THEN '民族意识形态风险词库'
                    ELSE title
                END,
                risk_label = CASE
                    WHEN risk_label IN ('', '仇恨歧视', '民族宗教仇恨风险', '民族语言与宗教仇恨', '民族语言') THEN '民族意识形态风险'
                    ELSE risk_label
                END,
                updated_at = ?
            WHERE id = 'minority'
              AND (
                title IN ('民族语言词库', '民族语言与宗教仇恨风险知识包')
                OR risk_label IN ('', '仇恨歧视', '民族宗教仇恨风险', '民族语言与宗教仇恨', '民族语言')
              )
            """,
            (now,),
        )

    def _seed_prompt_profiles(self, conn: sqlite3.Connection, now: str) -> None:
        for category_id, profile in DEFAULT_PROMPT_PROFILES.items():
            category = conn.execute(
                "SELECT id FROM lexicon_categories WHERE id = ?",
                (category_id,),
            ).fetchone()
            if not category:
                continue
            conn.execute(
                """
                INSERT INTO lexicon_prompt_profiles
                    (
                        category_id, audit_goal, evidence_rules, fusion_rules,
                        image_prompt, frame_prompt, fusion_prompt_template,
                        version, prompt_version, updated_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(category_id) DO NOTHING
                """,
                (
                    category_id,
                    profile.get("audit_goal", ""),
                    profile.get("evidence_rules", ""),
                    profile.get("fusion_rules", ""),
                    profile["image_prompt"],
                    profile["frame_prompt"],
                    profile["fusion_prompt_template"],
                    int(profile.get("version") or 1),
                    profile["prompt_version"],
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE lexicon_prompt_profiles
                SET
                    image_prompt = CASE WHEN image_prompt = '' THEN ? ELSE image_prompt END,
                    frame_prompt = CASE WHEN frame_prompt = '' THEN ? ELSE frame_prompt END,
                    fusion_prompt_template = CASE WHEN fusion_prompt_template = '' THEN ? ELSE fusion_prompt_template END,
                    audit_goal = CASE WHEN audit_goal = '' THEN ? ELSE audit_goal END,
                    evidence_rules = CASE WHEN evidence_rules = '' THEN ? ELSE evidence_rules END,
                    fusion_rules = CASE WHEN fusion_rules = '' THEN ? ELSE fusion_rules END
                WHERE category_id = ?
                """,
                (
                    profile["image_prompt"],
                    profile["frame_prompt"],
                    profile["fusion_prompt_template"],
                    profile.get("audit_goal", ""),
                    profile.get("evidence_rules", ""),
                    profile.get("fusion_rules", ""),
                    category_id,
                ),
            )

    def list_categories(self) -> list[dict]:
        with self._lock, self._connect() as conn:
            categories = conn.execute(
                "SELECT * FROM lexicon_categories ORDER BY sort_order ASC, title ASC"
            ).fetchall()
            rows = conn.execute(
                "SELECT * FROM lexicon_keywords ORDER BY category_id ASC, id ASC"
            ).fetchall()
            profile_rows = conn.execute("SELECT * FROM lexicon_prompt_profiles").fetchall()

        keywords_by_category: dict[str, list[dict]] = {}
        for row in rows:
            keyword = self._keyword_row(row)
            keywords_by_category.setdefault(keyword["category_id"], []).append(keyword)
        profiles_by_category = {
            row["category_id"]: self._prompt_profile_row(row)
            for row in profile_rows
        }

        out = []
        for row in categories:
            items = keywords_by_category.get(row["id"], [])
            profile = profiles_by_category.get(row["id"]) or self._default_prompt_profile(row["id"])
            out.append({
                "id": row["id"],
                "title": row["title"],
                "risk_label": row["risk_label"] or self._default_risk_label(row["id"], row["title"]),
                "chips": [item["keyword"] for item in items[:12]],
                "keywords": items,
                "prompt_profile": profile,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            })
        return out

    def upsert_category(
        self,
        *,
        category_id: str = "",
        title: str,
        risk_label: str = "",
        terms: list[str] | None = None,
        platform_keywords: list[str] | None = None,
        platform_tags: list[str] | None = None,
        entries: list[dict] | None = None,
    ) -> dict:
        cleaned_title = str(title or "").strip() or "自定义词库"
        cleaned_risk_label = str(risk_label or "").strip() or cleaned_title.replace("词库", "").replace("知识库", "").strip()
        cleaned_id = str(category_id or "").strip() or f"custom_{uuid4().hex[:10]}"
        term_values = self._dedupe_terms(terms or [])
        search_values = self._dedupe_terms(platform_keywords or [])
        tag_values = self._dedupe_terms(platform_tags or [])
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            existing = conn.execute("SELECT id FROM lexicon_categories WHERE id = ?", (cleaned_id,)).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE lexicon_categories
                    SET title = ?, risk_label = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (cleaned_title, cleaned_risk_label, now, cleaned_id),
                )
            else:
                max_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS sort_order FROM lexicon_categories").fetchone()
                conn.execute(
                    """
                    INSERT INTO lexicon_categories (id, title, risk_label, sort_order, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (cleaned_id, cleaned_title, cleaned_risk_label, int(max_order["sort_order"] or 0) + 1, now, now),
                )
            conn.execute(
                """
                DELETE FROM lexicon_keywords
                WHERE category_id = ? AND match_type IN ('黑话词', '平台搜索词', '平台标签', '精确', '模糊', '正则', 'tag')
                """,
                (cleaned_id,),
            )
            if entries is not None:
                for entry in entries:
                    main_term = str(entry.get("main_term") or "").strip()
                    if not main_term:
                        continue
                    match_type = "平台标签" if entry.get("query_type") == "tag" else "黑话词"
                    enabled = bool(entry.get("enabled", True))
                    self._insert_keyword_row(conn, cleaned_id, main_term, match_type, "全平台", "中", enabled, now)
                    for variant in self._dedupe_terms(entry.get("variants") or []):
                        note = json.dumps({"variant_of": main_term}, ensure_ascii=False)
                        self._insert_keyword_row(conn, cleaned_id, variant, match_type, "全平台", "中", enabled, now, note)
            else:
                for value in term_values:
                    self._insert_keyword_row(conn, cleaned_id, value, "黑话词", "全平台", "中", True, now)
                for value in search_values:
                    self._insert_keyword_row(conn, cleaned_id, value, "平台搜索词", "全平台", "中", True, now)
                for value in tag_values:
                    self._insert_keyword_row(conn, cleaned_id, value, "平台标签", "全平台", "中", True, now)
            profile = DEFAULT_PROMPT_PROFILES.get(cleaned_id)
            base_profile = profile or DEFAULT_PROMPT_PROFILES.get("soft")
            if base_profile:
                audit_goal = profile.get("audit_goal", "") if profile else f"识别{cleaned_risk_label}相关风险"
                conn.execute(
                    """
                    INSERT INTO lexicon_prompt_profiles
                        (
                            category_id, audit_goal, evidence_rules, fusion_rules,
                            image_prompt, frame_prompt, fusion_prompt_template,
                            version, prompt_version, updated_at
                        )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(category_id) DO NOTHING
                    """,
                    (
                        cleaned_id,
                        audit_goal,
                        profile.get("evidence_rules", "") if profile else "",
                        profile.get("fusion_rules", "") if profile else "",
                        base_profile["image_prompt"],
                        base_profile["frame_prompt"],
                        base_profile["fusion_prompt_template"],
                        f"{cleaned_id}-profile-v1",
                        now,
                    ),
                )
        return self.get_category(cleaned_id)

    def get_category(self, category_id: str) -> dict:
        for category in self.list_categories():
            if category["id"] == category_id:
                return category
        raise KeyError(category_id)

    def get_prompt_profile(self, category_id: str) -> dict:
        with self._lock, self._connect() as conn:
            category = conn.execute(
                "SELECT id FROM lexicon_categories WHERE id = ?",
                (category_id,),
            ).fetchone()
            if not category:
                raise KeyError(category_id)
            row = conn.execute(
                "SELECT * FROM lexicon_prompt_profiles WHERE category_id = ?",
                (category_id,),
            ).fetchone()
        if row:
            return self._prompt_profile_row(row)
        return self._default_prompt_profile(category_id)

    def get_knowledge_packages(self, category_ids: list[str]) -> list[dict]:
        packages = []
        for category_id in category_ids:
            package = self.get_knowledge_package(category_id)
            packages.append(package)
        return packages

    def get_knowledge_package(self, category_id: str) -> dict:
        with self._lock, self._connect() as conn:
            category = conn.execute(
                "SELECT * FROM lexicon_categories WHERE id = ?",
                (category_id,),
            ).fetchone()
            if not category:
                raise KeyError(category_id)
            rows = conn.execute(
                """
                SELECT * FROM lexicon_keywords
                WHERE category_id = ? AND enabled = 1
                ORDER BY id ASC
                """,
                (category_id,),
            ).fetchall()
        profile = self.get_prompt_profile(category_id)
        exact = []
        fuzzy = []
        for row in rows:
            keyword = str(row["keyword"] or "").strip()
            if not keyword:
                continue
            match_type = str(row["match_type"] or "")
            if match_type in {"平台搜索词", "平台标签"}:
                continue
            if "精确" in match_type:
                exact.append(keyword)
            else:
                fuzzy.append(keyword)
        title = str(category["title"] or category_id)
        audit_goal = str(profile.get("audit_goal") or title)
        package = get_default_knowledge_package(category_id)
        if package:
            package["id"] = category_id
            package["lexicon_title"] = title
            package["version"] = str(profile.get("prompt_version") or package.get("version") or "1.0.0")
            package["audit_goal"] = str(package.get("audit_goal") or audit_goal)
            package["keywords"] = self._merge_keywords(package.get("keywords") or {}, exact, fuzzy)
            return package
        return {
            "schema_version": "1.0",
            "id": category_id,
            "title": title,
            "version": str(profile.get("prompt_version") or profile.get("version") or "1.0.0"),
            "audit_goal": audit_goal,
            "output_labels": self._output_labels(title, audit_goal),
            "risk_definition": {
                "included": [audit_goal],
                "excluded": [],
            },
            "risk_patterns": {
                "high": [],
                "medium": [],
                "low": [],
            },
            "modality_guidance": {
                "text": f"关注标题、正文、账号资料中与{title}相关的意图、话术、联系方式、交易或行动线索。",
                "ocr": f"关注画面文字、字幕、水印、二维码旁文字中的{title}暗号、变体和联系方式。",
                "asr": f"关注口播、背景音和语音转写中的{title}表达、暗号、邀约或行动号召。",
                "vision": f"关注图片或视频关键帧中与{title}相关的物品、场景、动作、符号和风险上下文。",
                "comment": f"关注评论区接头暗号、求联系方式、聚集互动、引流目标和作者回应。",
            },
            "keywords": {
                "exact": exact,
                "fuzzy": fuzzy,
                "negative_context": [],
            },
            "evidence_rules": self._rules_value(profile.get("evidence_rules")),
            "fusion_rules": self._rules_value(profile.get("fusion_rules")),
            "exemption_rules": [],
        }

    def update_prompt_profile(
        self,
        category_id: str,
        *,
        image_prompt: str,
        frame_prompt: str,
        fusion_prompt_template: str,
    ) -> dict:
        cleaned = {
            "image_prompt": image_prompt,
            "frame_prompt": frame_prompt,
            "fusion_prompt_template": fusion_prompt_template,
        }
        missing = [key for key, value in cleaned.items() if not value.strip()]
        if missing:
            raise ValueError(f"{', '.join(missing)} is required")
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            category = conn.execute(
                "SELECT id FROM lexicon_categories WHERE id = ?",
                (category_id,),
            ).fetchone()
            if not category:
                raise KeyError(category_id)
            existing = conn.execute(
                "SELECT version FROM lexicon_prompt_profiles WHERE category_id = ?",
                (category_id,),
            ).fetchone()
            version = int(existing["version"] or 1) + 1 if existing else 1
            prompt_version = f"{category_id}-profile-v{version}"
            conn.execute(
                """
                INSERT INTO lexicon_prompt_profiles
                    (
                        category_id, audit_goal, evidence_rules, fusion_rules,
                        image_prompt, frame_prompt, fusion_prompt_template,
                        version, prompt_version, updated_at
                    )
                VALUES (
                    ?,
                    COALESCE((SELECT audit_goal FROM lexicon_prompt_profiles WHERE category_id = ?), ''),
                    COALESCE((SELECT evidence_rules FROM lexicon_prompt_profiles WHERE category_id = ?), ''),
                    COALESCE((SELECT fusion_rules FROM lexicon_prompt_profiles WHERE category_id = ?), ''),
                    ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(category_id) DO UPDATE SET
                    image_prompt = excluded.image_prompt,
                    frame_prompt = excluded.frame_prompt,
                    fusion_prompt_template = excluded.fusion_prompt_template,
                    version = excluded.version,
                    prompt_version = excluded.prompt_version,
                    updated_at = excluded.updated_at
                """,
                (
                    category_id,
                    category_id,
                    category_id,
                    category_id,
                    cleaned["image_prompt"],
                    cleaned["frame_prompt"],
                    cleaned["fusion_prompt_template"],
                    version,
                    prompt_version,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM lexicon_prompt_profiles WHERE category_id = ?",
                (category_id,),
            ).fetchone()
        return self._prompt_profile_row(row)

    def reset_prompt_profile(self, category_id: str) -> dict:
        default = DEFAULT_PROMPT_PROFILES.get(category_id)
        if not default:
            raise KeyError(category_id)
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            category = conn.execute(
                "SELECT id FROM lexicon_categories WHERE id = ?",
                (category_id,),
            ).fetchone()
            if not category:
                raise KeyError(category_id)
            existing = conn.execute(
                "SELECT version FROM lexicon_prompt_profiles WHERE category_id = ?",
                (category_id,),
            ).fetchone()
            version = int(existing["version"] or 1) + 1 if existing else 1
            prompt_version = f"{category_id}-profile-v{version}"
            conn.execute(
                """
                INSERT INTO lexicon_prompt_profiles
                    (
                        category_id, audit_goal, evidence_rules, fusion_rules,
                        image_prompt, frame_prompt, fusion_prompt_template,
                        version, prompt_version, updated_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(category_id) DO UPDATE SET
                    audit_goal = excluded.audit_goal,
                    evidence_rules = excluded.evidence_rules,
                    fusion_rules = excluded.fusion_rules,
                    image_prompt = excluded.image_prompt,
                    frame_prompt = excluded.frame_prompt,
                    fusion_prompt_template = excluded.fusion_prompt_template,
                    version = excluded.version,
                    prompt_version = excluded.prompt_version,
                    updated_at = excluded.updated_at
                """,
                (
                    category_id,
                    default.get("audit_goal", ""),
                    default.get("evidence_rules", ""),
                    default.get("fusion_rules", ""),
                    default["image_prompt"],
                    default["frame_prompt"],
                    default["fusion_prompt_template"],
                    version,
                    prompt_version,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM lexicon_prompt_profiles WHERE category_id = ?",
                (category_id,),
            ).fetchone()
        return self._prompt_profile_row(row)

    def enabled_keywords(self, category_id: str) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT keyword FROM lexicon_keywords
                WHERE category_id = ? AND enabled = 1
                ORDER BY id ASC
                """,
                (category_id,),
            ).fetchall()
        return [str(row["keyword"]) for row in rows]

    def enabled_search_keywords(self, category_id: str) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT keyword FROM lexicon_keywords
                WHERE category_id = ? AND enabled = 1 AND match_type = '平台搜索词'
                ORDER BY id ASC
                """,
                (category_id,),
            ).fetchall()
        return [str(row["keyword"]) for row in rows]

    def add_keyword(
        self,
        *,
        category_id: str,
        keyword: str,
        match_type: str = "模糊",
        platform: str = "全平台",
        risk_level: str = "中",
        enabled: bool = True,
        note: str = "",
    ) -> dict:
        cleaned = keyword.strip()
        if not cleaned:
            raise ValueError("keyword is required")
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            category = conn.execute(
                "SELECT id FROM lexicon_categories WHERE id = ?",
                (category_id,),
            ).fetchone()
            if not category:
                raise KeyError(category_id)
            cursor = conn.execute(
                """
                INSERT INTO lexicon_keywords
                    (category_id, keyword, match_type, platform, risk_level, enabled, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(category_id, keyword, platform, match_type)
                DO UPDATE SET
                    risk_level = excluded.risk_level,
                    enabled = excluded.enabled,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (
                    category_id,
                    cleaned,
                    match_type.strip() or "模糊",
                    platform.strip() or "全平台",
                    risk_level.strip() or "中",
                    1 if enabled else 0,
                    note,
                    now,
                    now,
                ),
            )
            row_id = cursor.lastrowid
            row = conn.execute(
                """
                SELECT * FROM lexicon_keywords
                WHERE id = COALESCE(NULLIF(?, 0), (
                    SELECT id FROM lexicon_keywords
                    WHERE category_id = ? AND keyword = ? AND platform = ? AND match_type = ?
                ))
                """,
                (row_id, category_id, cleaned, platform.strip() or "全平台", match_type.strip() or "模糊"),
            ).fetchone()
        return self._keyword_row(row)

    def update_keyword(self, keyword_id: int, **kwargs) -> dict:
        allowed = {"keyword", "match_type", "platform", "risk_level", "enabled", "note"}
        updates = []
        values = []
        for key, value in kwargs.items():
            if key not in allowed:
                continue
            updates.append(f"{key} = ?")
            values.append(1 if key == "enabled" and value else (0 if key == "enabled" else value))
        if not updates:
            raise ValueError("no fields to update")
        updates.append("updated_at = ?")
        values.append(datetime.now().isoformat(timespec="seconds"))
        values.append(keyword_id)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE lexicon_keywords SET {', '.join(updates)} WHERE id = ?",
                values,
            )
            row = conn.execute("SELECT * FROM lexicon_keywords WHERE id = ?", (keyword_id,)).fetchone()
        if not row:
            raise KeyError(str(keyword_id))
        return self._keyword_row(row)

    def delete_keyword(self, keyword_id: int) -> None:
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM lexicon_keywords WHERE id = ?", (keyword_id,))
        if cursor.rowcount <= 0:
            raise KeyError(str(keyword_id))

    def delete_category(self, category_id: str) -> dict:
        cleaned_id = str(category_id or "").strip()
        if not cleaned_id:
            raise KeyError(category_id)
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM lexicon_categories WHERE id = ?", (cleaned_id,)).fetchone()
            if not row:
                raise KeyError(cleaned_id)
            category = dict(row)
            keyword_cursor = conn.execute("DELETE FROM lexicon_keywords WHERE category_id = ?", (cleaned_id,))
            profile_cursor = conn.execute("DELETE FROM lexicon_prompt_profiles WHERE category_id = ?", (cleaned_id,))
            conn.execute("DELETE FROM lexicon_categories WHERE id = ?", (cleaned_id,))
        return {
            "id": category["id"],
            "title": category["title"],
            "deleted_keyword_count": int(keyword_cursor.rowcount or 0),
            "deleted_prompt_profile_count": int(profile_cursor.rowcount or 0),
        }

    def _keyword_row(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "category_id": row["category_id"],
            "keyword": row["keyword"],
            "match_type": row["match_type"],
            "platform": row["platform"],
            "risk_level": row["risk_level"],
            "enabled": bool(row["enabled"]),
            "hit_count_7d": int(row["hit_count_7d"] or 0),
            "note": row["note"] or "",
        }

    def _insert_keyword_row(
        self,
        conn: sqlite3.Connection,
        category_id: str,
        keyword: str,
        match_type: str,
        platform: str,
        risk_level: str,
        enabled: bool,
        now: str,
        note: str = "",
    ) -> None:
        conn.execute(
            """
            INSERT INTO lexicon_keywords
                (category_id, keyword, match_type, platform, risk_level, enabled, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(category_id, keyword, platform, match_type) DO UPDATE SET
                risk_level = excluded.risk_level,
                enabled = excluded.enabled,
                note = excluded.note,
                updated_at = excluded.updated_at
            """,
            (category_id, keyword, match_type, platform, risk_level, 1 if enabled else 0, note, now, now),
        )

    def _dedupe_terms(self, values: list[str]) -> list[str]:
        out = []
        seen = set()
        for value in values:
            text = str(value or "").strip()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
        return out

    def _default_risk_label(self, category_id: str, title: str) -> str:
        mapping = {
            "prohibited": "违禁引流",
            "soft": "软色情",
            "terror": "暴恐",
            "drug": "涉毒",
            "gambling": "赌博博彩",
            "fraud": "诈骗",
            "hate": "民族意识形态风险",
            "minority": "民族意识形态风险",
        }
        return mapping.get(str(category_id or ""), str(title or "").replace("词库", "").replace("知识包", "").strip())

    def _prompt_profile_row(self, row: sqlite3.Row | dict) -> dict:
        category_id = row["category_id"]
        default = DEFAULT_PROMPT_PROFILES.get(category_id, DEFAULT_PROMPT_PROFILES["soft"])
        profile = {
            "category_id": category_id,
            "audit_goal": self._row_value(row, "audit_goal") or default.get("audit_goal", ""),
            "evidence_rules": self._row_value(row, "evidence_rules") or default.get("evidence_rules", ""),
            "fusion_rules": self._row_value(row, "fusion_rules") or default.get("fusion_rules", ""),
            "image_prompt": self._row_value(row, "image_prompt") or default["image_prompt"],
            "frame_prompt": self._row_value(row, "frame_prompt") or default["frame_prompt"],
            "fusion_prompt_template": self._row_value(row, "fusion_prompt_template") or default["fusion_prompt_template"],
            "version": int(row["version"] or 1),
            "prompt_version": row["prompt_version"],
            "updated_at": row["updated_at"] or "",
        }
        profile["preview"] = render_prompt_profile_preview(profile, profile["category_id"])
        return profile

    def _default_prompt_profile(self, category_id: str) -> dict:
        default = DEFAULT_PROMPT_PROFILES.get(category_id)
        if not default:
            base = DEFAULT_PROMPT_PROFILES["soft"]
            default = {
                **base,
                "audit_goal": f"识别{category_id}相关风险",
                "evidence_rules": "",
                "fusion_rules": "",
                "prompt_version": f"{category_id}-profile-v1",
            }
        return self._prompt_profile_row({
            "category_id": category_id,
            "audit_goal": default.get("audit_goal", ""),
            "evidence_rules": default.get("evidence_rules", ""),
            "fusion_rules": default.get("fusion_rules", ""),
            "image_prompt": default["image_prompt"],
            "frame_prompt": default["frame_prompt"],
            "fusion_prompt_template": default["fusion_prompt_template"],
            "version": int(default.get("version") or 1),
            "prompt_version": default["prompt_version"],
            "updated_at": "",
        })

    def _row_value(self, row: sqlite3.Row | dict, key: str) -> str:
        if isinstance(row, sqlite3.Row):
            return row[key] if key in row.keys() else ""
        return str(row.get(key) or "")

    def _output_labels(self, title: str, audit_goal: str) -> list[str]:
        clean_title = title.replace("词库", "").replace("知识包", "").strip() or title
        labels = [clean_title]
        for token in ("、", "，", ","):
            if token in audit_goal:
                labels.extend(part.strip() for part in audit_goal.split(token)[:4])
                break
        deduped = []
        for label in labels:
            if label and label not in deduped:
                deduped.append(label)
        return deduped[:6]

    def _rules_value(self, value) -> list | str:
        if not isinstance(value, str):
            return value or []
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") or text.startswith("{"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return text

    def _merge_keywords(self, base: dict, exact: list[str], fuzzy: list[str]) -> dict:
        def dedupe(values: list[str]) -> list[str]:
            out = []
            for value in values:
                text = str(value or "").strip()
                if text and text not in out:
                    out.append(text)
            return out

        return {
            "exact": dedupe(list(base.get("exact") or []) + exact),
            "fuzzy": dedupe(list(base.get("fuzzy") or []) + fuzzy),
            "negative_context": dedupe(list(base.get("negative_context") or [])),
        }
