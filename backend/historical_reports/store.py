from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.audit_agent.config import settings
from backend.historical_reports.catalog import HistoricalReportSpec


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HistoricalReportWorkspaceStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = (
            db_path or settings.data_dir / "historical_report_demo.sqlite3"
        ).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS historical_report_workspaces (
                    id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    report_version_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL UNIQUE,
                    draft_json TEXT NOT NULL,
                    display_timeline_json TEXT NOT NULL,
                    registered_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS historical_report_runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    report_version_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL CHECK(status = 'PUBLISHED'),
                    import_semantics TEXT NOT NULL CHECK(import_semantics = 'historical'),
                    registered_at TEXT NOT NULL,
                    FOREIGN KEY(workspace_id) REFERENCES historical_report_workspaces(id)
                );
                """
            )

    def register(self, spec: HistoricalReportSpec, *, principal_id: str) -> dict[str, Any]:
        now = utc_now()
        values = (
            spec.workspace_id,
            principal_id,
            spec.title,
            spec.task_id,
            spec.report_version_id,
            spec.run_id,
            json.dumps(spec.draft, ensure_ascii=False, sort_keys=True),
            json.dumps(spec.display_timeline, ensure_ascii=False, sort_keys=True),
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO historical_report_workspaces (
                    id, principal_id, title, task_id, report_version_id, run_id,
                    draft_json, display_timeline_json, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*values, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO historical_report_runs (
                    id, workspace_id, task_id, report_version_id,
                    status, import_semantics, registered_at
                ) VALUES (?, ?, ?, ?, 'PUBLISHED', 'historical', ?)
                """,
                (
                    spec.run_id,
                    spec.workspace_id,
                    spec.task_id,
                    spec.report_version_id,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM historical_report_workspaces WHERE id = ?",
                (spec.workspace_id,),
            ).fetchone()
            run = connection.execute(
                "SELECT * FROM historical_report_runs WHERE id = ?",
                (spec.run_id,),
            ).fetchone()
            if row is None or run is None:
                raise RuntimeError("historical report workspace registration failed")
            actual = tuple(row[key] for key in (
                "id", "principal_id", "title", "task_id", "report_version_id",
                "run_id", "draft_json", "display_timeline_json",
            ))
            run_actual = tuple(run[key] for key in (
                "workspace_id", "task_id", "report_version_id", "status",
                "import_semantics",
            ))
            if actual != values or run_actual != (
                spec.workspace_id,
                spec.task_id,
                spec.report_version_id,
                "PUBLISHED",
                "historical",
            ):
                raise RuntimeError("historical report workspace cannot be rebound")
        return self.get(spec.workspace_id, principal_id=principal_id)

    def list(self, *, principal_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM historical_report_workspaces
                WHERE principal_id = ? ORDER BY id
                """,
                (principal_id,),
            ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def get(self, workspace_id: str, *, principal_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM historical_report_workspaces
                WHERE id = ? AND principal_id = ?
                """,
                (workspace_id, principal_id),
            ).fetchone()
        if row is None:
            raise KeyError(workspace_id)
        return self._decode(row)

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["draft"] = json.loads(str(value.pop("draft_json")))
        value["display_timeline"] = json.loads(
            str(value.pop("display_timeline_json"))
        )
        return value
