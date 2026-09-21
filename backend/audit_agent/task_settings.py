"""One application-wide parameter set; jobs retain their own execution snapshot."""
import json
import sqlite3
from pathlib import Path

from .config import settings
from backend.investigation_creation.contracts import InvestigationTaskParameters


class TaskSettingsConflict(ValueError):
    pass


def default_task_parameters() -> InvestigationTaskParameters:
    """Return the same unsaved defaults used by preview and execution."""
    return InvestigationTaskParameters(
        max_notes=min(5, max(1, int(settings.m3_posts_per_keyword))),
        max_comments=min(1000, max(0, int(settings.m3_comments_per_post))),
        max_items_per_minute=5,
        analyze_limit=max(1, int(settings.m3_analyze_limit)),
        analysis_batch_size=5,
    )


def effective_parameters(requested):
    requested = InvestigationTaskParameters.model_validate(requested)
    max_total_notes = min(requested.max_total_notes, 5)
    return requested.model_copy(update={
        "max_notes": min(requested.max_notes, 5),
        "max_total_notes": max_total_notes,
        "max_comments": min(requested.max_comments, settings.m3_comments_per_post) if requested.collect_comments else 0,
        "get_sub_comment": requested.get_sub_comment and requested.collect_comments and requested.max_comments > 0 and settings.m3_comments_per_post > 0,
        "max_concurrency": min(requested.max_concurrency, max(1, settings.crawler_max_concurrency)),
        # Every collected post is audited. The exact per-task limit is frozen
        # later, after the final keyword count is known.
        "analyze_limit": max_total_notes,
        "auto_analyze": True,
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
        parameters = (
            json.loads(row[1])
            if row
            else default_task_parameters().model_dump(mode="json")
        )
        return {"revision": int(row[0]) if row else 0,
                "parameters": InvestigationTaskParameters.model_validate(parameters).model_dump(mode="json")}

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
