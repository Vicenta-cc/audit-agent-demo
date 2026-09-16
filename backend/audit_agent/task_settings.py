"""One application-wide parameter set; jobs retain their own execution snapshot."""
import json
import sqlite3
from pathlib import Path

from .config import settings
from backend.investigation_creation.contracts import InvestigationTaskParameters


class TaskSettingsConflict(ValueError):
    pass


def effective_parameters(requested):
    requested = InvestigationTaskParameters.model_validate(requested)
    return requested.model_copy(update={
        "max_notes": min(requested.max_notes, settings.m3_posts_per_keyword),
        "max_comments": min(requested.max_comments, settings.m3_comments_per_post) if requested.collect_comments else 0,
        "get_sub_comment": requested.get_sub_comment and requested.collect_comments and requested.max_comments > 0 and settings.m3_comments_per_post > 0,
        "max_concurrency": min(requested.max_concurrency, max(1, settings.crawler_max_concurrency)),
        "analyze_limit": min(requested.analyze_limit, settings.m3_analyze_limit) if requested.auto_analyze and settings.auto_analyze_crawled_content else 0,
        "auto_analyze": requested.auto_analyze and requested.analyze_limit > 0 and settings.auto_analyze_crawled_content,
    })


class TaskSettingsStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or settings.data_dir / "audit_index.sqlite3"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS task_settings (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL, parameters TEXT NOT NULL)")

    def get(self, connection=None):
        if connection is None:
            with sqlite3.connect(self.db_path) as owned:
                return self.get(owned)
        row = connection.execute("SELECT revision, parameters FROM task_settings WHERE id=1").fetchone()
        return {"revision": int(row[0]) if row else 0,
                "parameters": InvestigationTaskParameters.model_validate(json.loads(row[1]) if row else {}).model_dump(mode="json")}

    def save(self, parameters, expected_revision):
        parameters = InvestigationTaskParameters.model_validate(parameters).model_dump(mode="json")
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self.get(connection)
            if current["revision"] != expected_revision:
                raise TaskSettingsConflict("统一设置已被修改，请刷新后重试。")
            revision = current["revision"] + 1
            connection.execute("INSERT INTO task_settings VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,parameters=excluded.parameters",
                               (revision, json.dumps(parameters, ensure_ascii=False)))
        return {"revision": revision, "parameters": parameters}
