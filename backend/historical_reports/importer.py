from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from backend.historical_reports.catalog import HistoricalReportSpec


class HistoricalReportImportError(RuntimeError):
    pass


_REPORT_COPY_PLAN = (
    ("reports", "task_id = ?", "task_id"),
    ("report_versions", "id = ?", "version"),
    ("report_source_snapshots", "report_version_id = ?", "version"),
    ("report_snapshot_posts", "report_version_id = ?", "version"),
    ("report_snapshot_findings", "report_version_id = ?", "version"),
    ("report_snapshot_evidence", "report_version_id = ?", "version"),
    ("report_sections", "report_version_id = ?", "version"),
    ("report_claims", "report_version_id = ?", "version"),
    (
        "report_claim_findings",
        "claim_id IN (SELECT id FROM report_claims WHERE report_version_id = ?)",
        "version",
    ),
    (
        "report_claim_evidence",
        "claim_id IN (SELECT id FROM report_claims WHERE report_version_id = ?)",
        "version",
    ),
    ("report_categories", "report_version_id = ?", "version"),
    (
        "report_category_displayed_members",
        "category_id IN (SELECT id FROM report_categories WHERE report_version_id = ?)",
        "version",
    ),
    ("report_investigation_findings", "report_version_id = ?", "version"),
    ("report_account_projections", "report_version_id = ?", "version"),
    ("report_account_entries", "report_version_id = ?", "version"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quoted(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise HistoricalReportImportError("historical report schema is invalid")
    return f'"{identifier}"'


class HistoricalReportImporter:
    """Copy only immutable published-report tables from a fenced archive."""

    def __init__(self, target_database: Path):
        self.target_database = target_database.expanduser().resolve()

    def import_report(self, spec: HistoricalReportSpec) -> None:
        source = spec.source_database.expanduser().resolve()
        if not source.is_file() or _sha256(source) != spec.source_database_sha256:
            raise HistoricalReportImportError("historical report archive is unavailable")

        source_uri = f"file:{source}?mode=ro&immutable=1"
        with sqlite3.connect(source_uri, uri=True) as source_connection:
            source_connection.row_factory = sqlite3.Row
            source_connection.execute("PRAGMA query_only = ON")
            self._validate_source(source_connection, spec)
            self._copy_report(source_connection, spec)

    def _copy_report(
        self,
        source_connection: sqlite3.Connection,
        spec: HistoricalReportSpec,
    ) -> None:
        self.target_database.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.target_database) as target:
            target.row_factory = sqlite3.Row
            target.execute("PRAGMA foreign_keys = OFF")
            target.execute("BEGIN IMMEDIATE")
            target.execute(
                """
                CREATE TABLE IF NOT EXISTS historical_report_imports (
                    report_version_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    source_database_sha256 TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            existing = target.execute(
                "SELECT * FROM historical_report_imports WHERE report_version_id = ?",
                (spec.report_version_id,),
            ).fetchone()
            if existing is not None:
                self._validate_import_record(existing, spec)
                self._validate_target(target, spec)
                return

            for table, where_clause, parameter_kind in _REPORT_COPY_PLAN:
                parameter = (
                    spec.task_id if parameter_kind == "task_id" else spec.report_version_id
                )
                self._copy_table_rows(
                    source_connection,
                    target,
                    table,
                    where_clause,
                    parameter,
                    overrides={"status": "draft"} if table == "report_versions" else None,
                )
            target.execute(
                "UPDATE report_versions SET status = 'published' WHERE id = ?",
                (spec.report_version_id,),
            )
            self._validate_target(target, spec)
            target.execute(
                """
                INSERT INTO historical_report_imports (
                    report_version_id, task_id, source_database_sha256,
                    content_hash, snapshot_hash
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    spec.report_version_id,
                    spec.task_id,
                    spec.source_database_sha256,
                    spec.report_content_hash,
                    spec.snapshot_hash,
                ),
            )

    @staticmethod
    def _copy_table_rows(
        source: sqlite3.Connection,
        target: sqlite3.Connection,
        table: str,
        where_clause: str,
        parameter: str,
        overrides: dict[str, Any] | None = None,
    ) -> None:
        source_columns = [
            str(row["name"])
            for row in source.execute(f"PRAGMA table_info({_quoted(table)})")
        ]
        target_columns = [
            str(row["name"])
            for row in target.execute(f"PRAGMA table_info({_quoted(table)})")
        ]
        if not source_columns or source_columns != target_columns:
            raise HistoricalReportImportError("historical report schema mismatch")
        columns = ", ".join(_quoted(item) for item in source_columns)
        placeholders = ", ".join("?" for _ in source_columns)
        rows = source.execute(
            f"SELECT {columns} FROM {_quoted(table)} WHERE {where_clause} ORDER BY rowid",
            (parameter,),
        ).fetchall()
        for row in rows:
            values = tuple(
                (overrides or {}).get(item, row[item]) for item in source_columns
            )
            try:
                target.execute(
                    f"INSERT INTO {_quoted(table)} ({columns}) VALUES ({placeholders})",
                    values,
                )
            except sqlite3.IntegrityError:
                if not HistoricalReportImporter._row_exists(
                    target, table, source_columns, values
                ):
                    raise HistoricalReportImportError(
                        "historical report import conflicts with existing data"
                    )

    @staticmethod
    def _row_exists(
        connection: sqlite3.Connection,
        table: str,
        columns: list[str],
        values: tuple[Any, ...],
    ) -> bool:
        predicates = []
        parameters: list[Any] = []
        for column, value in zip(columns, values, strict=True):
            if value is None:
                predicates.append(f"{_quoted(column)} IS NULL")
            else:
                predicates.append(f"{_quoted(column)} = ?")
                parameters.append(value)
        row = connection.execute(
            f"SELECT 1 FROM {_quoted(table)} WHERE {' AND '.join(predicates)} LIMIT 1",
            parameters,
        ).fetchone()
        return row is not None

    @staticmethod
    def _validate_source(
        connection: sqlite3.Connection, spec: HistoricalReportSpec
    ) -> None:
        row = connection.execute(
            """
            SELECT r.task_id, v.status, v.content_hash, s.snapshot_hash
            FROM report_versions v
            JOIN reports r ON r.id = v.report_id
            JOIN report_source_snapshots s ON s.report_version_id = v.id
            WHERE v.id = ?
            """,
            (spec.report_version_id,),
        ).fetchone()
        if row is None or (
            str(row["task_id"]),
            str(row["status"]),
            str(row["content_hash"]),
            str(row["snapshot_hash"]),
        ) != (
            spec.task_id,
            "published",
            spec.report_content_hash,
            spec.snapshot_hash,
        ):
            raise HistoricalReportImportError("historical report provenance mismatch")

    @staticmethod
    def _validate_target(
        connection: sqlite3.Connection, spec: HistoricalReportSpec
    ) -> None:
        HistoricalReportImporter._validate_source(connection, spec)

    @staticmethod
    def _validate_import_record(row: sqlite3.Row, spec: HistoricalReportSpec) -> None:
        actual = (
            str(row["task_id"]),
            str(row["source_database_sha256"]),
            str(row["content_hash"]),
            str(row["snapshot_hash"]),
        )
        expected = (
            spec.task_id,
            spec.source_database_sha256,
            spec.report_content_hash,
            spec.snapshot_hash,
        )
        if actual != expected:
            raise HistoricalReportImportError(
                "historical report registration cannot be rebound"
            )
