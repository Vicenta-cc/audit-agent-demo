from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from backend.audit_agent.config import settings
from backend.domain.identity import stable_hash
from backend.reporting.errors import PublishedReportImmutableError, ReportGenerationError
from backend.reporting.identity import make_report_claim_id


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = (db_path or (settings.data_dir / "audit_index.sqlite3")).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS report_versions (
                    id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    version_number INTEGER NOT NULL,
                    generation_run_id TEXT,
                    source_snapshot_id TEXT,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    body_markdown TEXT NOT NULL DEFAULT '',
                    body_json TEXT NOT NULL DEFAULT '{}',
                    content_hash TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    UNIQUE(report_id, version_number),
                    FOREIGN KEY(report_id) REFERENCES reports(id)
                );

                CREATE TABLE IF NOT EXISTS report_generation_runs (
                    id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    report_version_id TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    checkpoint_thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_node TEXT NOT NULL DEFAULT '',
                    error_type TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(report_id) REFERENCES reports(id),
                    FOREIGN KEY(report_version_id) REFERENCES report_versions(id)
                );

                CREATE TABLE IF NOT EXISTS report_source_snapshots (
                    id TEXT PRIMARY KEY,
                    report_version_id TEXT NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    task_status TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    configuration_revision_id TEXT NOT NULL DEFAULT '',
                    finding_ids_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    data_quality_warnings_json TEXT NOT NULL,
                    statistic_inputs_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    source_revision TEXT NOT NULL DEFAULT '',
                    relation_hash TEXT NOT NULL DEFAULT '',
                    statistics_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(report_version_id) REFERENCES report_versions(id)
                );

                CREATE TABLE IF NOT EXISTS report_snapshot_posts (
                    id TEXT PRIMARY KEY,
                    report_version_id TEXT NOT NULL,
                    post_ref TEXT NOT NULL,
                    canonical_key TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    UNIQUE(report_version_id, post_ref),
                    FOREIGN KEY(report_version_id) REFERENCES report_versions(id)
                );

                CREATE TABLE IF NOT EXISTS report_snapshot_findings (
                    id TEXT PRIMARY KEY,
                    report_version_id TEXT NOT NULL,
                    finding_ref TEXT NOT NULL,
                    audit_result_id INTEGER NOT NULL,
                    post_ref TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    UNIQUE(report_version_id, finding_ref),
                    FOREIGN KEY(report_version_id) REFERENCES report_versions(id)
                );

                CREATE TABLE IF NOT EXISTS report_snapshot_evidence (
                    id TEXT PRIMARY KEY,
                    report_version_id TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    audit_result_id INTEGER NOT NULL,
                    post_ref TEXT NOT NULL,
                    finding_ref TEXT NOT NULL,
                    support_type TEXT NOT NULL,
                    source_formats_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    UNIQUE(report_version_id, evidence_ref),
                    FOREIGN KEY(report_version_id) REFERENCES report_versions(id)
                );

                CREATE TABLE IF NOT EXISTS report_categories (
                    id TEXT PRIMARY KEY,
                    report_version_id TEXT NOT NULL,
                    category_ref TEXT NOT NULL,
                    display_ordinal INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    membership_scope TEXT NOT NULL,
                    membership_complete INTEGER NOT NULL CHECK (membership_complete IN (0, 1)),
                    source_section_id TEXT NOT NULL,
                    UNIQUE(report_version_id, category_ref),
                    FOREIGN KEY(report_version_id) REFERENCES report_versions(id)
                );

                CREATE TABLE IF NOT EXISTS report_category_displayed_members (
                    category_id TEXT NOT NULL,
                    display_ordinal INTEGER NOT NULL,
                    post_ref TEXT NOT NULL,
                    finding_ref TEXT NOT NULL,
                    PRIMARY KEY(category_id, display_ordinal),
                    FOREIGN KEY(category_id) REFERENCES report_categories(id)
                );

                CREATE TABLE IF NOT EXISTS report_investigation_findings (
                    id TEXT PRIMARY KEY,
                    report_version_id TEXT NOT NULL,
                    finding_ref TEXT NOT NULL,
                    display_ordinal INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    related_post_refs_json TEXT NOT NULL,
                    representative_post_refs_json TEXT NOT NULL,
                    audit_finding_refs_json TEXT NOT NULL,
                    evidence_refs_json TEXT NOT NULL,
                    metric_refs_json TEXT NOT NULL,
                    boundary_notes_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    UNIQUE(report_version_id, finding_ref),
                    FOREIGN KEY(report_version_id) REFERENCES report_versions(id)
                );

                CREATE TABLE IF NOT EXISTS report_account_projections (
                    report_version_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    report_snapshot_hash TEXT NOT NULL,
                    account_corpus_schema_version TEXT NOT NULL,
                    account_corpus_revision TEXT NOT NULL,
                    account_fixture_sha256 TEXT NOT NULL,
                    account_task_snapshot_ref TEXT NOT NULL,
                    account_task_source_hash TEXT NOT NULL,
                    occurrence_set_hash TEXT NOT NULL,
                    default_active_comment_limit INTEGER NOT NULL,
                    statistics_json TEXT NOT NULL,
                    projection_hash TEXT NOT NULL,
                    FOREIGN KEY(report_version_id) REFERENCES report_versions(id)
                );

                CREATE TABLE IF NOT EXISTS report_account_entries (
                    id TEXT PRIMARY KEY,
                    report_version_id TEXT NOT NULL,
                    entry_ref TEXT NOT NULL,
                    internal_account_ref TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    roles_json TEXT NOT NULL,
                    current_statistics_json TEXT NOT NULL,
                    target_display_order INTEGER,
                    active_comment_display_order INTEGER,
                    default_active_comment_visible INTEGER NOT NULL
                        CHECK (default_active_comment_visible IN (0, 1)),
                    full_index_order INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    UNIQUE(report_version_id, entry_ref),
                    UNIQUE(report_version_id, internal_account_ref),
                    UNIQUE(report_version_id, full_index_order),
                    FOREIGN KEY(report_version_id)
                        REFERENCES report_account_projections(report_version_id)
                );

                CREATE TABLE IF NOT EXISTS report_provider_exchanges (
                    id TEXT PRIMARY KEY,
                    report_version_id TEXT NOT NULL,
                    sequence_no INTEGER NOT NULL,
                    node_name TEXT NOT NULL,
                    section_id TEXT NOT NULL DEFAULT '',
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    UNIQUE(report_version_id, sequence_no),
                    FOREIGN KEY(report_version_id) REFERENCES report_versions(id)
                );

                CREATE TABLE IF NOT EXISTS report_sections (
                    id TEXT PRIMARY KEY,
                    report_version_id TEXT NOT NULL,
                    section_id TEXT NOT NULL,
                    sort_order INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT '',
                    section_type TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    UNIQUE(report_version_id, section_id),
                    FOREIGN KEY(report_version_id) REFERENCES report_versions(id)
                );

                CREATE TABLE IF NOT EXISTS report_claims (
                    id TEXT PRIMARY KEY,
                    report_version_id TEXT NOT NULL,
                    report_section_id TEXT NOT NULL,
                    local_claim_id TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    support_type TEXT NOT NULL,
                    metric_refs_json TEXT NOT NULL DEFAULT '[]',
                    content_hash TEXT NOT NULL,
                    FOREIGN KEY(report_version_id) REFERENCES report_versions(id),
                    FOREIGN KEY(report_section_id) REFERENCES report_sections(id)
                );

                CREATE TABLE IF NOT EXISTS report_claim_findings (
                    claim_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    PRIMARY KEY(claim_id, finding_id),
                    FOREIGN KEY(claim_id) REFERENCES report_claims(id)
                );

                CREATE TABLE IF NOT EXISTS report_claim_evidence (
                    claim_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    citation_excerpt TEXT NOT NULL DEFAULT '',
                    asset_status TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(claim_id, evidence_id),
                    FOREIGN KEY(claim_id) REFERENCES report_claims(id)
                );

                CREATE TABLE IF NOT EXISTS report_model_steps (
                    id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    report_version_id TEXT NOT NULL,
                    node_name TEXT NOT NULL,
                    section_id TEXT NOT NULL DEFAULT '',
                    prompt_version TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output_json TEXT NOT NULL DEFAULT '{}',
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    error_type TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(report_version_id) REFERENCES report_versions(id)
                );

                CREATE TABLE IF NOT EXISTS report_run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    node_name TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES report_generation_runs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_report_versions_report
                ON report_versions(report_id, version_number);
                CREATE INDEX IF NOT EXISTS idx_report_claims_version
                ON report_claims(report_version_id);
                CREATE INDEX IF NOT EXISTS idx_report_model_steps_version
                ON report_model_steps(report_version_id, node_name);
                CREATE INDEX IF NOT EXISTS idx_report_run_events_run
                ON report_run_events(run_id, id);
                CREATE INDEX IF NOT EXISTS idx_report_account_entries_order
                ON report_account_entries(report_version_id, full_index_order);

                CREATE TRIGGER IF NOT EXISTS report_versions_published_no_update
                BEFORE UPDATE ON report_versions
                WHEN OLD.status = 'published'
                BEGIN
                    SELECT RAISE(ABORT, 'published report version is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_versions_published_no_delete
                BEFORE DELETE ON report_versions
                WHEN OLD.status = 'published'
                BEGIN
                    SELECT RAISE(ABORT, 'published report version is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_snapshots_no_update
                BEFORE UPDATE ON report_source_snapshots
                BEGIN
                    SELECT RAISE(ABORT, 'report source snapshot is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_snapshots_no_delete
                BEFORE DELETE ON report_source_snapshots
                BEGIN
                    SELECT RAISE(ABORT, 'report source snapshot is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_snapshot_posts_no_update
                BEFORE UPDATE ON report_snapshot_posts
                BEGIN
                    SELECT RAISE(ABORT, 'report snapshot post is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_snapshot_posts_no_delete
                BEFORE DELETE ON report_snapshot_posts
                BEGIN
                    SELECT RAISE(ABORT, 'report snapshot post is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_snapshot_findings_no_update
                BEFORE UPDATE ON report_snapshot_findings
                BEGIN
                    SELECT RAISE(ABORT, 'report snapshot finding is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_snapshot_findings_no_delete
                BEFORE DELETE ON report_snapshot_findings
                BEGIN
                    SELECT RAISE(ABORT, 'report snapshot finding is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_snapshot_evidence_no_update
                BEFORE UPDATE ON report_snapshot_evidence
                BEGIN
                    SELECT RAISE(ABORT, 'report snapshot evidence is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_snapshot_evidence_no_delete
                BEFORE DELETE ON report_snapshot_evidence
                BEGIN
                    SELECT RAISE(ABORT, 'report snapshot evidence is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_categories_published_no_update
                BEFORE UPDATE ON report_categories
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = OLD.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report category is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_categories_published_no_delete
                BEFORE DELETE ON report_categories
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = OLD.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report category is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_investigation_findings_published_no_update
                BEFORE UPDATE ON report_investigation_findings
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = OLD.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published InvestigationFinding is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_investigation_findings_published_no_insert
                BEFORE INSERT ON report_investigation_findings
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = NEW.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published InvestigationFinding is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_investigation_findings_published_no_delete
                BEFORE DELETE ON report_investigation_findings
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = OLD.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published InvestigationFinding is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_category_members_published_no_insert
                BEFORE INSERT ON report_category_displayed_members
                WHEN EXISTS (
                    SELECT 1 FROM report_categories c
                    JOIN report_versions v ON v.id = c.report_version_id
                    WHERE c.id = NEW.category_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report category member is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_category_members_published_no_update
                BEFORE UPDATE ON report_category_displayed_members
                WHEN EXISTS (
                    SELECT 1 FROM report_categories c
                    JOIN report_versions v ON v.id = c.report_version_id
                    WHERE c.id = OLD.category_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report category member is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_category_members_published_no_delete
                BEFORE DELETE ON report_category_displayed_members
                WHEN EXISTS (
                    SELECT 1 FROM report_categories c
                    JOIN report_versions v ON v.id = c.report_version_id
                    WHERE c.id = OLD.category_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report category member is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_sections_published_no_update
                BEFORE UPDATE ON report_sections
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = OLD.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report section is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_sections_published_no_insert
                BEFORE INSERT ON report_sections
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = NEW.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report section is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_sections_published_no_delete
                BEFORE DELETE ON report_sections
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = OLD.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report section is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_claims_published_no_update
                BEFORE UPDATE ON report_claims
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = OLD.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report claim is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_claims_published_no_insert
                BEFORE INSERT ON report_claims
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = NEW.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report claim is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_claims_published_no_delete
                BEFORE DELETE ON report_claims
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = OLD.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report claim is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_claim_findings_published_no_delete
                BEFORE DELETE ON report_claim_findings
                WHEN EXISTS (
                    SELECT 1 FROM report_claims c
                    JOIN report_versions v ON v.id = c.report_version_id
                    WHERE c.id = OLD.claim_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report claim link is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_claim_findings_published_no_insert
                BEFORE INSERT ON report_claim_findings
                WHEN EXISTS (
                    SELECT 1 FROM report_claims c
                    JOIN report_versions v ON v.id = c.report_version_id
                    WHERE c.id = NEW.claim_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report claim link is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_claim_findings_published_no_update
                BEFORE UPDATE ON report_claim_findings
                WHEN EXISTS (
                    SELECT 1 FROM report_claims c
                    JOIN report_versions v ON v.id = c.report_version_id
                    WHERE c.id = OLD.claim_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report claim link is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_claim_evidence_published_no_delete
                BEFORE DELETE ON report_claim_evidence
                WHEN EXISTS (
                    SELECT 1 FROM report_claims c
                    JOIN report_versions v ON v.id = c.report_version_id
                    WHERE c.id = OLD.claim_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report citation is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_claim_evidence_published_no_insert
                BEFORE INSERT ON report_claim_evidence
                WHEN EXISTS (
                    SELECT 1 FROM report_claims c
                    JOIN report_versions v ON v.id = c.report_version_id
                    WHERE c.id = NEW.claim_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report citation is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_claim_evidence_published_no_update
                BEFORE UPDATE ON report_claim_evidence
                WHEN EXISTS (
                    SELECT 1 FROM report_claims c
                    JOIN report_versions v ON v.id = c.report_version_id
                    WHERE c.id = OLD.claim_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report citation is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_account_projections_published_no_insert
                BEFORE INSERT ON report_account_projections
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = NEW.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report Account projection is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_account_projections_published_no_update
                BEFORE UPDATE ON report_account_projections
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = OLD.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report Account projection is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_account_projections_published_no_delete
                BEFORE DELETE ON report_account_projections
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = OLD.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report Account projection is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_account_entries_published_no_insert
                BEFORE INSERT ON report_account_entries
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = NEW.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report Account entry is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_account_entries_published_no_update
                BEFORE UPDATE ON report_account_entries
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = OLD.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report Account entry is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS report_account_entries_published_no_delete
                BEFORE DELETE ON report_account_entries
                WHEN EXISTS (
                    SELECT 1 FROM report_versions v
                    WHERE v.id = OLD.report_version_id AND v.status = 'published'
                )
                BEGIN
                    SELECT RAISE(ABORT, 'published report Account entry is immutable');
                END;
                """
            )
            for column, definition in (
                ("display_name", "TEXT NOT NULL DEFAULT ''"),
                ("source_revision", "TEXT NOT NULL DEFAULT ''"),
                ("relation_hash", "TEXT NOT NULL DEFAULT ''"),
                ("statistics_json", "TEXT NOT NULL DEFAULT '{}'"),
            ):
                self._ensure_column(connection, "report_source_snapshots", column, definition)
            self._ensure_column(connection, "report_versions", "standalone_risk_posts_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(connection, "report_investigation_findings", "post_memberships_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(connection, "report_sections", "section_type", "TEXT NOT NULL DEFAULT ''")
            for column, definition in (
                ("section_ref", "TEXT NOT NULL DEFAULT ''"),
                ("section_number", "TEXT NOT NULL DEFAULT ''"),
                ("parent_section_ref", "TEXT"),
                ("display_ordinal", "INTEGER NOT NULL DEFAULT 0"),
            ):
                self._ensure_column(connection, "report_sections", column, definition)
            for column, definition in (
                ("is_target_account", "INTEGER"),
                ("target_display_ordinal", "INTEGER"),
                ("active_comment_rank", "INTEGER"),
                ("active_comment_display_ordinal", "INTEGER"),
                ("default_visible", "INTEGER"),
            ):
                self._ensure_column(
                    connection, "report_account_entries", column, definition
                )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def create_generation(self, task_id: str, *, model: str, prompt_version: str) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            report_row = connection.execute(
                "SELECT * FROM reports WHERE task_id = ?", (task_id,)
            ).fetchone()
            if report_row is None:
                report_id = f"report:{uuid4().hex}"
                connection.execute(
                    "INSERT INTO reports (id, task_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (report_id, task_id, now, now),
                )
            else:
                report_id = str(report_row["id"])
            version_number = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version_number), 0) + 1 FROM report_versions WHERE report_id = ?",
                    (report_id,),
                ).fetchone()[0]
            )
            version_id = f"report-version:{uuid4().hex}"
            run_id = f"report-run:{uuid4().hex}"
            connection.execute(
                """
                INSERT INTO report_versions (
                    id, report_id, version_number, generation_run_id, status,
                    model, prompt_version, created_at
                ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)
                """,
                (version_id, report_id, version_number, run_id, model, prompt_version, now),
            )
            connection.execute(
                """
                INSERT INTO report_generation_runs (
                    id, report_id, report_version_id, task_id, checkpoint_thread_id,
                    status, started_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?)
                """,
                (run_id, report_id, version_id, task_id, run_id, now),
            )
            connection.execute(
                "UPDATE reports SET updated_at = ? WHERE id = ?", (now, report_id)
            )
        return {
            "run_id": run_id,
            "report_id": report_id,
            "report_version_id": version_id,
            "version_number": version_number,
            "task_id": task_id,
            "model": model,
            "prompt_version": prompt_version,
        }

    def save_source_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        version_id = str(payload["report_version_id"])
        existing = self.get_source_snapshot(version_id)
        if existing:
            if existing["snapshot_hash"] != payload["snapshot_hash"]:
                raise ReportGenerationError("source snapshot already exists with a different hash")
            return existing
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO report_source_snapshots (
                    id, report_version_id, task_id, task_status, source_hash,
                    configuration_revision_id, finding_ids_json, evidence_ids_json,
                    data_quality_warnings_json, statistic_inputs_json, generated_at, snapshot_hash,
                    display_name, source_revision, relation_hash, statistics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["snapshot_id"],
                    version_id,
                    payload["task_id"],
                    payload["task_status"],
                    payload["source_hash"],
                    payload.get("configuration_revision_id", ""),
                    self._json(payload["finding_ids"]),
                    self._json(payload["evidence_ids"]),
                    self._json(payload["data_quality_warnings"]),
                    self._json(payload["statistic_inputs"]),
                    payload["generated_at"],
                    payload["snapshot_hash"],
                    payload.get("display_name", ""),
                    payload.get("source_revision", ""),
                    payload.get("relation_hash", ""),
                    self._json(payload.get("statistics") or {}),
                ),
            )
            connection.execute(
                "UPDATE report_versions SET source_snapshot_id = ? WHERE id = ? AND status = 'draft'",
                (payload["snapshot_id"], version_id),
            )
        return self.get_source_snapshot(version_id) or {}

    def get_source_snapshot(self, report_version_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_source_snapshots WHERE report_version_id = ?",
                (report_version_id,),
            ).fetchone()
        if row is None:
            return None
        output = dict(row)
        for field in (
            "finding_ids_json",
            "evidence_ids_json",
            "data_quality_warnings_json",
            "statistic_inputs_json",
            "statistics_json",
        ):
            output[field.removesuffix("_json")] = json.loads(output.pop(field))
        return output

    def save_snapshot_payloads(
        self,
        report_version_id: str,
        *,
        posts: list[dict[str, Any]],
        findings: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> None:
        """Persist the complete immutable payload behind a source snapshot."""
        with self._connect() as connection:
            for item in posts:
                connection.execute(
                    """
                    INSERT INTO report_snapshot_posts
                        (id, report_version_id, post_ref, canonical_key, revision, payload_json, payload_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(report_version_id, post_ref) DO NOTHING
                    """,
                    (
                        f"snapshot-post:{uuid4().hex}", report_version_id, item["ref"],
                        item["canonical_key"], item["revision"], self._json(item["payload"]), item["payload_hash"],
                    ),
                )
            for item in findings:
                connection.execute(
                    """
                    INSERT INTO report_snapshot_findings
                        (id, report_version_id, finding_ref, audit_result_id, post_ref, payload_json, payload_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(report_version_id, finding_ref) DO NOTHING
                    """,
                    (
                        f"snapshot-finding:{uuid4().hex}", report_version_id, item["ref"],
                        int(item["audit_result_id"]), item["post_ref"], self._json(item["payload"]), item["payload_hash"],
                    ),
                )
            for item in evidence:
                connection.execute(
                    """
                    INSERT INTO report_snapshot_evidence
                        (id, report_version_id, evidence_ref, audit_result_id, post_ref, finding_ref,
                         support_type, source_formats_json, payload_json, payload_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(report_version_id, evidence_ref) DO NOTHING
                    """,
                    (
                        f"snapshot-evidence:{uuid4().hex}", report_version_id, item["ref"],
                        int(item["audit_result_id"]), item["post_ref"], item["finding_ref"],
                        item["support_type"], self._json(item["source_formats"]),
                        self._json(item["payload"]), item["payload_hash"],
                    ),
                )

    def list_snapshot_payloads(self, report_version_id: str) -> dict[str, list[dict[str, Any]]]:
        with self._connect() as connection:
            posts = connection.execute(
                "SELECT * FROM report_snapshot_posts WHERE report_version_id = ? ORDER BY rowid",
                (report_version_id,),
            ).fetchall()
            findings = connection.execute(
                "SELECT * FROM report_snapshot_findings WHERE report_version_id = ? ORDER BY rowid",
                (report_version_id,),
            ).fetchall()
            evidence = connection.execute(
                "SELECT * FROM report_snapshot_evidence WHERE report_version_id = ? ORDER BY rowid",
                (report_version_id,),
            ).fetchall()
        return {
            "posts": [self._decode_json_fields(dict(row), ("payload_json",)) for row in posts],
            "findings": [self._decode_json_fields(dict(row), ("payload_json",)) for row in findings],
            "evidence": [self._decode_json_fields(dict(row), ("source_formats_json", "payload_json")) for row in evidence],
        }

    def load_immutable_snapshot(self, report_version_id: str) -> Any:
        """Rehydrate the frozen report Snapshot without consulting live domain data."""
        from backend.reporting.integration_source import (
            ImmutableReportSnapshot,
            SnapshotEvidence,
            SnapshotFinding,
            SnapshotPost,
        )

        manifest = self.get_source_snapshot(report_version_id)
        if manifest is None:
            raise ReportGenerationError("report source snapshot is missing")
        payloads = self.list_snapshot_payloads(report_version_id)
        if not payloads["posts"] or not payloads["findings"]:
            raise ReportGenerationError("report source snapshot payloads are incomplete")
        posts = tuple(
            SnapshotPost(
                ref=str(row["post_ref"]),
                canonical_key=str(row["canonical_key"]),
                revision=str(row["revision"]),
                payload=row["payload"],
                payload_hash=str(row["payload_hash"]),
            )
            for row in payloads["posts"]
        )
        findings = tuple(
            SnapshotFinding(
                ref=str(row["finding_ref"]),
                audit_result_id=int(row["audit_result_id"]),
                post_ref=str(row["post_ref"]),
                payload=row["payload"],
                payload_hash=str(row["payload_hash"]),
            )
            for row in payloads["findings"]
        )
        evidence = tuple(
            SnapshotEvidence(
                ref=str(row["evidence_ref"]),
                audit_result_id=int(row["audit_result_id"]),
                post_ref=str(row["post_ref"]),
                finding_ref=str(row["finding_ref"]),
                support_type=str(row["support_type"]),
                source_formats=tuple(row.get("source_formats") or []),
                payload=row["payload"],
                payload_hash=str(row["payload_hash"]),
            )
            for row in payloads["evidence"]
        )
        if len({item.ref for item in posts}) != len(posts):
            raise ReportGenerationError("frozen Snapshot contains duplicate Post refs")
        if len({item.ref for item in findings}) != len(findings):
            raise ReportGenerationError("frozen Snapshot contains duplicate Finding refs")
        if len({item.ref for item in evidence}) != len(evidence):
            raise ReportGenerationError("frozen Snapshot contains duplicate Evidence refs")
        for item in (*posts, *findings, *evidence):
            if stable_hash(item.payload) != item.payload_hash:
                raise ReportGenerationError(f"frozen Snapshot payload hash mismatch: {item.ref}")
        if tuple(manifest.get("finding_ids") or ()) != tuple(item.ref for item in findings):
            raise ReportGenerationError("frozen Snapshot Finding order does not match manifest")
        if tuple(manifest.get("evidence_ids") or ()) != tuple(item.ref for item in evidence):
            raise ReportGenerationError("frozen Snapshot Evidence order does not match manifest")
        post_refs = {item.ref for item in posts}
        finding_refs = {item.ref for item in findings}
        for finding in findings:
            if finding.post_ref not in post_refs:
                raise ReportGenerationError(f"frozen Finding has unknown Post: {finding.ref}")
        for item in evidence:
            if item.post_ref not in post_refs or item.finding_ref not in finding_refs:
                raise ReportGenerationError(f"frozen Evidence has invalid parent: {item.ref}")
            owner = next(finding for finding in findings if finding.ref == item.finding_ref)
            if owner.post_ref != item.post_ref:
                raise ReportGenerationError(f"frozen Evidence crosses Post parent: {item.ref}")
        relation_hash = stable_hash(
            [
                {"post_ref": item.post_ref, "finding_ref": item.finding_ref, "evidence_ref": item.ref}
                for item in evidence
            ]
        )
        if manifest.get("relation_hash") and manifest["relation_hash"] != relation_hash:
            raise ReportGenerationError("frozen Snapshot relation hash does not match payloads")
        source_revision = str(manifest.get("source_revision") or stable_hash(
            [(item.ref, item.revision, item.payload_hash) for item in posts]
        ))
        statistics = manifest.get("statistics") or {
            "canonical_posts": len(posts),
            "findings": len(findings),
            "direct_evidence": len(evidence),
            "direct_legacy_risk_image": sum(
                "legacy_risk_image" in item.source_formats for item in evidence
            ),
            "decision": dict(sorted(Counter(item.payload.get("decision", "") for item in findings).items())),
            "risk_level": dict(sorted(Counter(item.payload.get("risk_level", "") for item in findings).items())),
        }
        snapshot_body = {
            "task_id": manifest["task_id"],
            "display_name": manifest.get("display_name") or manifest["task_id"],
            "source_db_sha256": manifest["source_hash"],
            "source_revision": source_revision,
            "audit_config_revision_id": manifest["configuration_revision_id"],
            "posts": [item.payload_hash for item in posts],
            "findings": [item.payload_hash for item in findings],
            "evidence": [item.payload_hash for item in evidence],
            "relations": relation_hash,
        }
        snapshot_hash = stable_hash(snapshot_body)
        if manifest.get("snapshot_hash") and manifest["snapshot_hash"] != snapshot_hash:
            raise ReportGenerationError("frozen Snapshot hash does not match payloads")
        return ImmutableReportSnapshot(
            snapshot_ref=str(manifest["id"]),
            task_id=str(manifest["task_id"]),
            display_name=str(manifest.get("display_name") or manifest["task_id"]),
            source_db_sha256=str(manifest["source_hash"]),
            source_revision=source_revision,
            audit_config_revision_id=str(manifest["configuration_revision_id"]),
            posts=posts,
            findings=findings,
            evidence=evidence,
            relation_hash=relation_hash,
            snapshot_hash=snapshot_hash,
            statistics=statistics,
        )

    def save_provider_exchange(
        self,
        *,
        report_version_id: str,
        request: dict[str, Any],
        response: dict[str, Any],
        status: str,
        metadata: dict[str, Any] | None = None,
        node_name: str = "",
        section_id: str = "",
    ) -> None:
        with self._connect() as connection:
            sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM report_provider_exchanges WHERE report_version_id = ?",
                    (report_version_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO report_provider_exchanges
                    (id, report_version_id, sequence_no, node_name, section_id,
                     request_json, response_json, status, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"provider-exchange:{uuid4().hex}", report_version_id, sequence,
                    node_name, section_id, self._json(request), self._json(response),
                    status, self._json(metadata or {}), utc_now(),
                ),
            )

    def list_provider_exchanges(self, report_version_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM report_provider_exchanges WHERE report_version_id = ? ORDER BY sequence_no",
                (report_version_id,),
            ).fetchall()
        return [
            self._decode_json_fields(dict(row), ("request_json", "response_json", "metadata_json"))
            for row in rows
        ]

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        current_node: str | None = None,
        error: Exception | None = None,
        warnings: list[dict[str, Any]] | None = None,
    ) -> None:
        assignments = []
        values: list[Any] = []
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
            if status in {"running", "completed"} and error is None:
                assignments.extend(("error_type = ''", "error_message = ''"))
            if status == "completed":
                assignments.append("completed_at = ?")
                values.append(utc_now())
        if current_node is not None:
            assignments.append("current_node = ?")
            values.append(current_node)
        if error is not None:
            assignments.extend(("error_type = ?", "error_message = ?"))
            values.extend((type(error).__name__, str(error)))
        if warnings is not None:
            assignments.append("warnings_json = ?")
            values.append(self._json(warnings))
        if not assignments:
            return
        values.append(run_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE report_generation_runs SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def record_run_event(
        self,
        run_id: str,
        node_name: str,
        event: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO report_run_events (run_id, node_name, event, detail_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, node_name, event, self._json(detail or {}), utc_now()),
            )

    def list_run_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM report_run_events WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return [self._decode_json_fields(dict(row), ("detail_json",)) for row in rows]

    def get_successful_model_step(self, fingerprint: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_model_steps WHERE fingerprint = ? AND status = 'succeeded'",
                (fingerprint,),
            ).fetchone()
        if row is None:
            return None
        return self._decode_json_fields(dict(row), ("output_json", "usage_json"))

    def save_model_step(
        self,
        *,
        fingerprint: str,
        report_version_id: str,
        node_name: str,
        section_id: str,
        prompt_version: str,
        input_hash: str,
        model: str,
        status: str,
        output: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO report_model_steps (
                    id, fingerprint, report_version_id, node_name, section_id,
                    prompt_version, input_hash, model, status, output_json, usage_json,
                    error_type, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    status = excluded.status,
                    output_json = excluded.output_json,
                    usage_json = excluded.usage_json,
                    error_type = excluded.error_type,
                    error_message = excluded.error_message,
                    updated_at = excluded.updated_at
                """,
                (
                    f"model-step:{uuid4().hex}",
                    fingerprint,
                    report_version_id,
                    node_name,
                    section_id,
                    prompt_version,
                    input_hash,
                    model,
                    status,
                    self._json(output or {}),
                    self._json(usage or {}),
                    type(error).__name__ if error else "",
                    str(error) if error else "",
                    now,
                    now,
                ),
            )

    def list_model_steps(self, report_version_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM report_model_steps WHERE report_version_id = ? ORDER BY created_at",
                (report_version_id,),
            ).fetchall()
        return [
            self._decode_json_fields(dict(row), ("output_json", "usage_json"))
            for row in rows
        ]

    def save_investigation_findings(
        self, report_version_id: str, findings: list[dict[str, Any]]
    ) -> None:
        """Persist server-validated InvestigationFindings while the version is draft."""
        with self._connect() as connection:
            version = connection.execute(
                "SELECT status FROM report_versions WHERE id = ?", (report_version_id,)
            ).fetchone()
            if version is None:
                raise ReportGenerationError("report version not found")
            if version["status"] != "draft":
                raise ReportGenerationError("InvestigationFindings require a draft version")
            for item in findings:
                connection.execute(
                    """
                    INSERT INTO report_investigation_findings (
                        id, report_version_id, finding_ref, display_ordinal, title, statement,
                        related_post_refs_json, representative_post_refs_json,
                        audit_finding_refs_json, evidence_refs_json, metric_refs_json,
                        boundary_notes_json, post_memberships_json, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(report_version_id, finding_ref) DO NOTHING
                    """,
                    (
                        f"investigation-finding:{uuid4().hex}", report_version_id,
                        item["finding_ref"], int(item["display_ordinal"]), item["title"],
                        item["statement"], "[]", "[]", "[]", "[]",
                        self._json(item.get("metric_refs") or []),
                        self._json(item.get("boundary_notes") or []),
                        self._json(item.get("post_memberships") or []),
                        item.get("content_hash") or stable_hash(item),
                    ),
                )

    def list_investigation_findings(self, report_version_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM report_investigation_findings "
                "WHERE report_version_id = ? ORDER BY display_ordinal",
                (report_version_id,),
            ).fetchall()
        return [
            self._decode_json_fields(
                dict(row),
                (
                    "related_post_refs_json", "representative_post_refs_json",
                    "audit_finding_refs_json", "evidence_refs_json",
                    "metric_refs_json", "boundary_notes_json", "post_memberships_json",
                ),
            )
            for row in rows
        ]

    def _insert_account_projection(
        self,
        connection: sqlite3.Connection,
        *,
        report_version_id: str,
        projection: dict[str, Any],
        body_json: dict[str, Any],
    ) -> None:
        from backend.reporting.account_entries import public_account_projection
        from backend.reporting.account_overview import (
            REPORT_ACCOUNT_OVERVIEW_SCHEMA_VERSION,
            public_account_overview_projection,
        )

        if projection.get("schema_version") == REPORT_ACCOUNT_OVERVIEW_SCHEMA_VERSION:
            self._insert_account_overview_projection(
                connection,
                report_version_id=report_version_id,
                projection=projection,
                body_json=body_json,
                public_projection=public_account_overview_projection(projection),
            )
            return

        identity = {
            key: value for key, value in projection.items() if key != "projection_hash"
        }
        if stable_hash(identity) != str(projection.get("projection_hash") or ""):
            raise ReportGenerationError("report Account projection hash mismatch")
        if body_json.get("account_model") != public_account_projection(projection):
            raise ReportGenerationError(
                "ReportVersion Account projection disagrees with the published body"
            )

        entries = projection.get("entries") or []
        if not isinstance(entries, list) or not entries:
            raise ReportGenerationError("report Account projection has no entries")
        statistics = projection.get("statistics") or {}
        targets = [
            item for item in entries if item.get("target_display_order") is not None
        ]
        commenters = [
            item
            for item in entries
            if item.get("active_comment_display_order") is not None
        ]
        defaults = [
            item for item in commenters if item.get("default_active_comment_visible")
        ]
        expected_statistics = {
            "target_account_count": len(targets),
            "comment_account_count": len(commenters),
            "all_account_count": len(entries),
            "default_active_comment_account_count": len(defaults),
        }
        if statistics != expected_statistics:
            raise ReportGenerationError("report Account statistics do not match entries")
        if sorted(int(item["target_display_order"]) for item in targets) != list(
            range(1, len(targets) + 1)
        ):
            raise ReportGenerationError("target Account display order is not contiguous")
        if sorted(
            int(item["active_comment_display_order"]) for item in commenters
        ) != list(range(1, len(commenters) + 1)):
            raise ReportGenerationError(
                "active Comment Account display order is not contiguous"
            )
        default_limit = int(projection["default_active_comment_limit"])
        if {
            int(item["active_comment_display_order"]) for item in defaults
        } != set(range(1, min(len(commenters), default_limit) + 1)):
            raise ReportGenerationError(
                "default active Comment Accounts do not match the configured limit"
            )
        entry_refs = [str(item.get("entry_ref") or "") for item in entries]
        account_refs = [str(item.get("internal_account_ref") or "") for item in entries]
        if (
            any(not value for value in entry_refs + account_refs)
            or len(set(entry_refs)) != len(entries)
            or len(set(account_refs)) != len(entries)
        ):
            raise ReportGenerationError("report Account entry identity is invalid")

        connection.execute(
            """
            INSERT INTO report_account_projections (
                report_version_id, schema_version, report_snapshot_hash,
                account_corpus_schema_version, account_corpus_revision,
                account_fixture_sha256, account_task_snapshot_ref,
                account_task_source_hash, occurrence_set_hash,
                default_active_comment_limit, statistics_json, projection_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_version_id,
                projection["schema_version"],
                projection["report_snapshot_hash"],
                projection["account_corpus_schema_version"],
                projection["account_corpus_revision"],
                projection["account_fixture_sha256"],
                projection["account_task_snapshot_ref"],
                projection["account_task_source_hash"],
                projection["occurrence_set_hash"],
                default_limit,
                self._json(statistics),
                projection["projection_hash"],
            ),
        )
        allowed_roles = {"post_author", "comment_author"}
        for full_index_order, item in enumerate(entries, 1):
            roles = list(item.get("roles") or [])
            current_statistics = item.get("current_investigation_statistics") or {}
            if not roles or set(roles) - allowed_roles:
                raise ReportGenerationError("report Account entry role is invalid")
            if set(current_statistics) != {
                "comment_count",
                "commented_post_count",
                "commented_post_author_count",
                "earliest_activity_at",
                "latest_activity_at",
                "published_post_count",
            }:
                raise ReportGenerationError(
                    "report Account entry statistics are incomplete"
                )
            connection.execute(
                """
                INSERT INTO report_account_entries (
                    id, report_version_id, entry_ref, internal_account_ref,
                    display_name, roles_json, current_statistics_json,
                    target_display_order, active_comment_display_order,
                    default_active_comment_visible, full_index_order, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"report-account-entry:{uuid4().hex}",
                    report_version_id,
                    item["entry_ref"],
                    item["internal_account_ref"],
                    item["display_name"],
                    self._json(roles),
                    self._json(current_statistics),
                    item.get("target_display_order"),
                    item.get("active_comment_display_order"),
                    1 if item.get("default_active_comment_visible") else 0,
                    full_index_order,
                    stable_hash(item),
                ),
            )

    def _insert_account_overview_projection(
        self,
        connection: sqlite3.Connection,
        *,
        report_version_id: str,
        projection: dict[str, Any],
        body_json: dict[str, Any],
        public_projection: dict[str, Any],
    ) -> None:
        identity = {
            key: value for key, value in projection.items() if key != "projection_hash"
        }
        if stable_hash(identity) != str(projection.get("projection_hash") or ""):
            raise ReportGenerationError("report Account projection hash mismatch")
        if body_json.get("account_model") != public_projection:
            raise ReportGenerationError(
                "ReportVersion Account projection disagrees with the published body"
            )
        entries = projection.get("entries") or []
        if not isinstance(entries, list) or not entries:
            raise ReportGenerationError("report Account projection has no entries")
        targets = [item for item in entries if item.get("is_target_account")]
        post_authors = [item for item in entries if "post_author" in item.get("roles", [])]
        commenters = [item for item in entries if "comment_author" in item.get("roles", [])]
        defaults = [
            item
            for item in commenters
            if item.get("active_comment_display_ordinal") is not None
        ]
        expected_statistics = {
            "target_account_count": len(targets),
            "post_author_account_count": len(post_authors),
            "comment_author_account_count": len(commenters),
            "distinct_account_count": len(entries),
            "default_active_comment_account_count": len(defaults),
            "full_account_index_available": True,
        }
        if projection.get("statistics") != expected_statistics:
            raise ReportGenerationError("report Account statistics do not match entries")
        if sorted(int(item["target_display_ordinal"]) for item in targets) != list(
            range(1, len(targets) + 1)
        ):
            raise ReportGenerationError("target Account display order is not contiguous")
        if sorted(int(item["active_comment_rank"]) for item in commenters) != list(
            range(1, len(commenters) + 1)
        ):
            raise ReportGenerationError("active Comment Account rank is not contiguous")
        if sorted(
            int(item["active_comment_display_ordinal"]) for item in defaults
        ) != list(range(1, len(defaults) + 1)):
            raise ReportGenerationError(
                "default active Comment Account order is not contiguous"
            )
        if any(item.get("is_target_account") for item in defaults):
            raise ReportGenerationError(
                "target Accounts cannot be duplicated in the active Comment group"
            )
        default_limit = int(projection["default_active_comment_limit"])
        if len(defaults) != min(
            len([item for item in commenters if not item.get("is_target_account")]),
            default_limit,
        ):
            raise ReportGenerationError(
                "default active Comment Accounts do not match the configured limit"
            )
        entry_refs = [str(item.get("entry_ref") or "") for item in entries]
        account_refs = [str(item.get("internal_account_ref") or "") for item in entries]
        if (
            any(not value for value in entry_refs + account_refs)
            or len(set(entry_refs)) != len(entries)
            or len(set(account_refs)) != len(entries)
        ):
            raise ReportGenerationError("report Account entry identity is invalid")

        connection.execute(
            """
            INSERT INTO report_account_projections (
                report_version_id, schema_version, report_snapshot_hash,
                account_corpus_schema_version, account_corpus_revision,
                account_fixture_sha256, account_task_snapshot_ref,
                account_task_source_hash, occurrence_set_hash,
                default_active_comment_limit, statistics_json, projection_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_version_id,
                projection["schema_version"],
                projection["report_snapshot_hash"],
                projection["account_corpus_schema_version"],
                projection["account_corpus_revision"],
                projection["account_fixture_sha256"],
                projection["account_task_snapshot_ref"],
                projection["account_task_source_hash"],
                projection["occurrence_set_hash"],
                default_limit,
                self._json(projection["statistics"]),
                projection["projection_hash"],
            ),
        )
        required_statistics = {
            "comment_count",
            "risk_comment_count",
            "commented_post_count",
            "commented_post_author_count",
            "earliest_activity_at",
            "latest_activity_at",
            "published_post_count",
            "risk_published_post_count",
        }
        for full_index_order, item in enumerate(entries, 1):
            roles = list(item.get("roles") or [])
            current_statistics = item.get("current_investigation_statistics") or {}
            if not roles or set(roles) - {"post_author", "comment_author"}:
                raise ReportGenerationError("report Account entry role is invalid")
            if set(current_statistics) != required_statistics:
                raise ReportGenerationError(
                    "report Account entry statistics are incomplete"
                )
            connection.execute(
                """
                INSERT INTO report_account_entries (
                    id, report_version_id, entry_ref, internal_account_ref,
                    display_name, roles_json, current_statistics_json,
                    target_display_order, active_comment_display_order,
                    default_active_comment_visible, full_index_order, content_hash,
                    is_target_account, target_display_ordinal,
                    active_comment_rank, active_comment_display_ordinal,
                    default_visible
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"report-account-entry:{uuid4().hex}",
                    report_version_id,
                    item["entry_ref"],
                    item["internal_account_ref"],
                    item["display_name"],
                    self._json(roles),
                    self._json(current_statistics),
                    item.get("target_display_ordinal"),
                    item.get("active_comment_display_ordinal"),
                    1 if item.get("default_visible") else 0,
                    full_index_order,
                    stable_hash(item),
                    1 if item.get("is_target_account") else 0,
                    item.get("target_display_ordinal"),
                    item.get("active_comment_rank"),
                    item.get("active_comment_display_ordinal"),
                    1 if item.get("default_visible") else 0,
                ),
            )

    def publish_version(
        self,
        *,
        report_version_id: str,
        title: str,
        body_markdown: str,
        body_json: dict[str, Any],
        sections: list[dict[str, Any]],
        citation_details: dict[str, dict[str, Any]],
        categories: list[dict[str, Any]] | None = None,
        investigation_findings: list[dict[str, Any]] | None = None,
        standalone_risk_posts: list[dict[str, Any]] | None = None,
        account_projection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute(
                "SELECT * FROM report_versions WHERE id = ?", (report_version_id,)
            ).fetchone()
            if version is None:
                raise ReportGenerationError(f"report version not found: {report_version_id}")
            if version["status"] == "published":
                return dict(version)
            if version["status"] != "draft":
                raise ReportGenerationError(f"report version is not publishable: {version['status']}")
            snapshot = connection.execute(
                "SELECT id FROM report_source_snapshots WHERE report_version_id = ?",
                (report_version_id,),
            ).fetchone()
            if snapshot is None:
                raise ReportGenerationError("report source snapshot is missing")

            if investigation_findings:
                for item in investigation_findings:
                    connection.execute(
                        """
                        INSERT INTO report_investigation_findings (
                            id, report_version_id, finding_ref, display_ordinal, title, statement,
                            related_post_refs_json, representative_post_refs_json,
                            audit_finding_refs_json, evidence_refs_json, metric_refs_json,
                            boundary_notes_json, post_memberships_json, content_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"investigation-finding:{uuid4().hex}", report_version_id,
                            item["finding_ref"], int(item["display_ordinal"]), item["title"],
                            item["statement"], "[]", "[]", "[]", "[]",
                            self._json(item.get("metric_refs") or []),
                            self._json(item.get("boundary_notes") or []),
                            self._json(item.get("post_memberships") or []),
                            item.get("content_hash") or stable_hash(item),
                        ),
                    )

            structured_section_refs = {
                str(section.get("section_ref") or "") for section in sections
            }
            has_structured_sections = any(
                section.get("section_number") for section in sections
            )
            if has_structured_sections:
                if (
                    "" in structured_section_refs
                    or len(structured_section_refs) != len(sections)
                    or len(
                        {
                            str(section.get("section_number") or "")
                            for section in sections
                        }
                    )
                    != len(sections)
                ):
                    raise ReportGenerationError(
                        "structured Report section identity is invalid"
                    )
                for index, section in enumerate(sections, 1):
                    if int(section.get("display_ordinal", 0)) != index:
                        raise ReportGenerationError(
                            "structured Report section order is invalid"
                        )
                    parent = section.get("parent_section_ref")
                    if parent and parent not in {
                        str(item.get("section_ref") or "")
                        for item in sections[: index - 1]
                    }:
                        raise ReportGenerationError(
                            "structured Report section parent is invalid"
                        )
                    if not str(section.get("section_type") or ""):
                        raise ReportGenerationError(
                            "structured Report section type is missing"
                        )

            persisted_sections = []
            for index, section in enumerate(sections):
                section_row_id = f"report-section:{uuid4().hex}"
                section_hash = stable_hash(section)
                connection.execute(
                    """
                    INSERT INTO report_sections (
                        id, report_version_id, section_id, sort_order, title, purpose,
                        section_type, body, content_hash, section_ref,
                        section_number, parent_section_ref, display_ordinal
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        section_row_id,
                        report_version_id,
                        section["section_id"],
                        index,
                        section["title"],
                        section.get("purpose", ""),
                        section.get("section_kind", ""),
                        section["body"],
                        section_hash,
                        section.get("section_ref", ""),
                        section.get("section_number", ""),
                        section.get("parent_section_ref"),
                        int(section.get("display_ordinal", index + 1)),
                    ),
                )
                persisted_sections.append(section_row_id)
                for claim_index, claim in enumerate(section.get("claims") or []):
                    local_claim_id = claim.get("claim_id") or f"claim-{claim_index + 1}"
                    claim_id = make_report_claim_id(
                        report_version_id,
                        section["section_id"],
                        local_claim_id,
                    )
                    connection.execute(
                        """
                        INSERT INTO report_claims (
                            id, report_version_id, report_section_id, local_claim_id,
                            claim_type, text, support_type, metric_refs_json, content_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            claim_id,
                            report_version_id,
                            section_row_id,
                            local_claim_id,
                            claim["claim_type"],
                            claim["text"],
                            claim.get("support_type", "direct"),
                            self._json(claim.get("metric_refs") or []),
                            stable_hash(claim),
                        ),
                    )
                    for finding_id in dict.fromkeys(claim.get("finding_ids") or []):
                        connection.execute(
                            "INSERT INTO report_claim_findings (claim_id, finding_id) VALUES (?, ?)",
                            (claim_id, finding_id),
                        )
                    for evidence_id in dict.fromkeys(claim.get("evidence_ids") or []):
                        detail = citation_details.get(evidence_id, {})
                        connection.execute(
                            """
                            INSERT INTO report_claim_evidence (
                                claim_id, evidence_id, citation_excerpt, asset_status
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (
                                claim_id,
                                evidence_id,
                                str(detail.get("citation_excerpt") or "")[:1000],
                                str(detail.get("asset_status") or ""),
                            ),
                        )

            for category in categories or []:
                category_id = f"report-category:{uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO report_categories (
                        id, report_version_id, category_ref, display_ordinal, title,
                        membership_scope, membership_complete, source_section_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        category_id,
                        report_version_id,
                        category["category_ref"],
                        int(category["display_ordinal"]),
                        category["title"],
                        category["membership_scope"],
                        1 if category.get("membership_complete") else 0,
                        category["source_section_id"],
                    ),
                )
                for member in category.get("members") or []:
                    connection.execute(
                        """
                        INSERT INTO report_category_displayed_members
                            (category_id, display_ordinal, post_ref, finding_ref)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            category_id,
                            int(member["display_ordinal"]),
                            member["post_ref"],
                            member["finding_ref"],
                        ),
                    )

            if account_projection is not None:
                self._insert_account_projection(
                    connection,
                    report_version_id=report_version_id,
                    projection=account_projection,
                    body_json=body_json,
                )

            content_hash = stable_hash(
                {
                    "title": title,
                    "body_markdown": body_markdown,
                    "body_json": body_json,
                    "sections": sections,
                    "categories": categories or [],
                    "investigation_findings": investigation_findings or [],
                    "standalone_risk_posts": standalone_risk_posts or [],
                }
            )
            published_at = utc_now()
            connection.execute(
                """
                UPDATE report_versions
                SET status = 'published', title = ?, body_markdown = ?, body_json = ?,
                    content_hash = ?, published_at = ?, standalone_risk_posts_json = ?
                WHERE id = ?
                """,
                (
                    title,
                    body_markdown,
                    self._json(body_json),
                    content_hash,
                    published_at,
                    self._json(standalone_risk_posts or []),
                    report_version_id,
                ),
            )
            connection.execute(
                "UPDATE reports SET updated_at = ? WHERE id = ?",
                (published_at, version["report_id"]),
            )
        return self.get_version(report_version_id) or {}

    def get_report_for_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM reports WHERE task_id = ?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_published_versions_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT v.* FROM report_versions AS v
                JOIN reports AS r ON r.id = v.report_id
                WHERE r.task_id = ? AND v.status = 'published'
                ORDER BY v.version_number DESC
                """,
                (task_id,),
            ).fetchall()
        return [
            self._decode_json_fields(dict(row), ("body_json",)) for row in rows
        ]

    def list_versions(self, report_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM report_versions WHERE report_id = ? ORDER BY version_number",
                (report_id,),
            ).fetchall()
        return [self._decode_json_fields(dict(row), ("body_json", "standalone_risk_posts_json")) for row in rows]

    def get_version(self, report_version_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_versions WHERE id = ?", (report_version_id,)
            ).fetchone()
        return self._decode_json_fields(dict(row), ("body_json", "standalone_risk_posts_json")) if row else None

    def get_human_report(self, report_version_id: str) -> dict[str, Any] | None:
        version = self.get_version(report_version_id)
        if version is None:
            return None
        human_report = version.get("body", {}).get("human_report")
        return human_report if isinstance(human_report, dict) else None

    def get_report_account_projection(
        self, report_version_id: str, *, include_internal: bool = False
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            projection_row = connection.execute(
                "SELECT * FROM report_account_projections WHERE report_version_id = ?",
                (report_version_id,),
            ).fetchone()
            if projection_row is None:
                return None
            entry_rows = connection.execute(
                """
                SELECT * FROM report_account_entries
                WHERE report_version_id = ? ORDER BY full_index_order
                """,
                (report_version_id,),
            ).fetchall()
        projection_record = self._decode_json_fields(
            dict(projection_row), ("statistics_json",)
        )
        from backend.reporting.account_overview import (
            REPORT_ACCOUNT_OVERVIEW_SCHEMA_VERSION,
        )

        is_account_overview = (
            projection_record["schema_version"]
            == REPORT_ACCOUNT_OVERVIEW_SCHEMA_VERSION
        )
        entries = []
        for row in entry_rows:
            record = self._decode_json_fields(
                dict(row), ("roles_json", "current_statistics_json")
            )
            if is_account_overview:
                entry = {
                    "entry_ref": record["entry_ref"],
                    "internal_account_ref": record["internal_account_ref"],
                    "display_name": record["display_name"],
                    "roles": record["roles"],
                    "is_target_account": bool(record["is_target_account"]),
                    "current_investigation_statistics": record[
                        "current_statistics"
                    ],
                    "target_display_ordinal": record[
                        "target_display_ordinal"
                    ],
                    "active_comment_rank": record["active_comment_rank"],
                    "active_comment_display_ordinal": record[
                        "active_comment_display_ordinal"
                    ],
                    "default_visible": bool(record["default_visible"]),
                }
            else:
                entry = {
                    "entry_ref": record["entry_ref"],
                    "internal_account_ref": record["internal_account_ref"],
                    "display_name": record["display_name"],
                    "roles": record["roles"],
                    "current_investigation_statistics": record["current_statistics"],
                    "target_display_order": record["target_display_order"],
                    "active_comment_display_order": record[
                        "active_comment_display_order"
                    ],
                    "default_active_comment_visible": bool(
                        record["default_active_comment_visible"]
                    ),
                }
            if stable_hash(entry) != str(record["content_hash"]):
                raise ReportGenerationError("stored ReportAccountEntry hash mismatch")
            entries.append(entry)
        projection = {
            "schema_version": projection_record["schema_version"],
            "report_snapshot_hash": projection_record["report_snapshot_hash"],
            "account_corpus_schema_version": projection_record[
                "account_corpus_schema_version"
            ],
            "account_corpus_revision": projection_record["account_corpus_revision"],
            "account_fixture_sha256": projection_record["account_fixture_sha256"],
            "account_task_snapshot_ref": projection_record[
                "account_task_snapshot_ref"
            ],
            "account_task_source_hash": projection_record[
                "account_task_source_hash"
            ],
            "occurrence_set_hash": projection_record["occurrence_set_hash"],
            "default_active_comment_limit": int(
                projection_record["default_active_comment_limit"]
            ),
            "statistics": projection_record["statistics"],
            "entries": entries,
            "projection_hash": projection_record["projection_hash"],
        }
        identity = {
            key: value for key, value in projection.items() if key != "projection_hash"
        }
        if stable_hash(identity) != projection["projection_hash"]:
            raise ReportGenerationError("stored report Account projection hash mismatch")
        if include_internal:
            return projection

        if is_account_overview:
            from backend.reporting.account_overview import (
                public_account_overview_projection,
            )

            public = public_account_overview_projection(projection)
        else:
            from backend.reporting.account_entries import public_account_projection

            public = public_account_projection(projection)
        public["all_entries"] = [
            {
                key: value
                for key, value in item.items()
                if key != "internal_account_ref"
            }
            for item in entries
        ]
        return public

    def list_report_account_entries(
        self,
        report_version_id: str,
        *,
        role: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if role not in {None, "post_author", "comment_author"}:
            raise ReportGenerationError("invalid ReportAccountEntry role filter")
        if not 1 <= int(limit) <= 100:
            raise ReportGenerationError("ReportAccountEntry limit must be between 1 and 100")
        projection = self.get_report_account_projection(report_version_id)
        if projection is None:
            raise ReportGenerationError("ReportVersion has no Account projection")
        entries = [
            item
            for item in projection["all_entries"]
            if role is None or role in item["roles"]
        ]
        start = 0
        if cursor:
            indexes = [
                index for index, item in enumerate(entries) if item["entry_ref"] == cursor
            ]
            if not indexes:
                raise ReportGenerationError("ReportAccountEntry cursor is invalid")
            start = indexes[0] + 1
        page = entries[start : start + int(limit)]
        has_more = start + len(page) < len(entries)
        return {
            "entries": page,
            "matched_count": len(entries),
            "has_more": has_more,
            "cursor": page[-1]["entry_ref"] if has_more and page else None,
        }

    def resolve_report_account_entry(
        self, report_version_id: str, entry_ref: str
    ) -> str:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT internal_account_ref FROM report_account_entries
                WHERE report_version_id = ? AND entry_ref = ?
                """,
                (report_version_id, entry_ref),
            ).fetchone()
        if row is None:
            raise ReportGenerationError("ReportAccountEntry was not found")
        return str(row["internal_account_ref"])

    def get_frontend_report(self, report_version_id: str) -> dict[str, Any]:
        from backend.reporting.structured_contract import (
            validate_structured_report_document,
        )

        version = self.get_version(report_version_id)
        if version is None or version.get("status") != "published":
            raise ReportGenerationError("published ReportVersion was not found")
        # `_decode_json_fields` exposes the JSON column as `body`; keep this
        # frontend API independent of the SQLite column naming convention.
        body = version.get("body") or {}
        document = body.get("report_document")
        if not isinstance(document, dict):
            raise ReportGenerationError(
                "ReportVersion has no structured frontend report document"
            )
        account_model = body.get("account_model")
        if not isinstance(account_model, dict):
            raise ReportGenerationError(
                "ReportVersion has no structured Account model"
            )
        output = json.loads(self._json(document))
        metadata = dict(output.get("report_metadata") or {})
        metadata["content_hash"] = version["content_hash"]
        metadata["published_at"] = version["published_at"]
        output["report_metadata"] = metadata
        validate_structured_report_document(output, account_model=account_model)
        return output

    def get_presentation_projection(self, report_version_id: str) -> dict[str, Any]:
        from backend.reporting.presentation_projection import (
            build_presentation_projection,
        )

        document = self.get_frontend_report(report_version_id)
        account_projection, account_repository, current_task_id = (
            self._presentation_account_context(report_version_id)
        )
        return build_presentation_projection(
            document,
            snapshot=self.load_immutable_snapshot(report_version_id),
            account_projection=account_projection,
            account_repository=account_repository,
            current_task_id=current_task_id,
        )

    def list_presentation_account_entries(
        self,
        report_version_id: str,
        *,
        role: str | None = None,
        account_filter: str | None = None,
        search: str | None = None,
        sort_order: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        from backend.reporting.presentation_projection import (
            build_account_index_page,
            build_filtered_account_index_page,
        )

        if role is not None and account_filter is not None:
            raise ReportGenerationError(
                "ReportAccountEntry role and presentation filters are mutually exclusive"
            )
        if account_filter is not None:
            projection, account_repository, current_task_id = (
                self._presentation_account_context(report_version_id)
            )
            return build_filtered_account_index_page(
                projection,
                account_repository=account_repository,
                current_task_id=current_task_id,
                account_filter=account_filter,
                limit=limit,
                cursor=cursor,
                search=search,
                sort_order=sort_order,
            )

        if search is not None or sort_order is not None:
            raise ReportGenerationError(
                "ReportAccountEntry search and sort require a presentation filter"
            )

        page = self.list_report_account_entries(
            report_version_id, role=role, limit=limit, cursor=cursor
        )
        return build_account_index_page(page, role=role)

    def get_presentation_account_detail(
        self, report_version_id: str, *, entry_ref: str
    ) -> dict[str, Any]:
        from backend.reporting.presentation_projection import build_account_detail
        projection, account_repository, current_task_id = (
            self._presentation_account_context(report_version_id)
        )
        if projection is None:
            raise ReportGenerationError("ReportVersion has no Account projection")

        return build_account_detail(
            projection,
            entry_ref=entry_ref,
            account_repository=account_repository,
            current_task_id=current_task_id,
        )

    def _presentation_account_context(
        self, report_version_id: str
    ) -> tuple[dict[str, Any] | None, Any | None, str]:
        from backend.reporting.account_entries import DEFAULT_ACCOUNT_FIXTURE_PATH
        from hermes_m0.account_activity_repository import AccountActivityRepository

        projection = self.get_report_account_projection(
            report_version_id, include_internal=True
        )
        version = self.get_version(report_version_id)
        report = (
            self.get_report(str(version["report_id"])) if version is not None else None
        )
        if report is None:
            raise ReportGenerationError("published ReportVersion metadata was not found")
        try:
            account_repository = AccountActivityRepository.load(
                DEFAULT_ACCOUNT_FIXTURE_PATH
            )
        except (OSError, ValueError, KeyError):
            account_repository = None
        return projection, account_repository, str(report["task_id"])

    def get_presentation_post_detail(
        self, report_version_id: str, *, post_ref: str
    ) -> dict[str, Any]:
        from backend.reporting.presentation_projection import build_post_detail

        return build_post_detail(
            self.get_frontend_report(report_version_id), post_ref=post_ref
        )

    def get_presentation_finding_evidence(
        self, report_version_id: str, *, investigation_finding_ref: str
    ) -> dict[str, Any]:
        from backend.reporting.presentation_projection import build_finding_evidence

        return build_finding_evidence(
            self.get_frontend_report(report_version_id),
            investigation_finding_ref=investigation_finding_ref,
        )

    def get_presentation_appendix(
        self,
        report_version_id: str,
        *,
        view: str = "posts",
        finding_ref: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        from backend.reporting.presentation_projection import build_appendix_page

        return build_appendix_page(
            self.get_frontend_report(report_version_id),
            view=view,
            finding_ref=finding_ref,
            limit=limit,
            cursor=cursor,
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM report_generation_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return self._decode_json_fields(dict(row), ("warnings_json",)) if row else None

    def get_latest_run_for_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM report_generation_runs
                WHERE task_id = ?
                ORDER BY started_at DESC, rowid DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
        return self._decode_json_fields(dict(row), ("warnings_json",)) if row else None

    def get_full_version(self, report_version_id: str) -> dict[str, Any] | None:
        version = self.get_version(report_version_id)
        if version is None:
            return None
        with self._connect() as connection:
            section_rows = connection.execute(
                "SELECT * FROM report_sections WHERE report_version_id = ? ORDER BY sort_order",
                (report_version_id,),
            ).fetchall()
            claim_rows = connection.execute(
                "SELECT * FROM report_claims WHERE report_version_id = ? ORDER BY rowid",
                (report_version_id,),
            ).fetchall()
            finding_rows = connection.execute(
                """
                SELECT cf.* FROM report_claim_findings cf
                JOIN report_claims c ON c.id = cf.claim_id
                WHERE c.report_version_id = ?
                ORDER BY cf.claim_id, cf.finding_id
                """,
                (report_version_id,),
            ).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT ce.* FROM report_claim_evidence ce
                JOIN report_claims c ON c.id = ce.claim_id
                WHERE c.report_version_id = ?
                ORDER BY ce.claim_id, ce.evidence_id
                """,
                (report_version_id,),
            ).fetchall()
            category_rows = connection.execute(
                "SELECT * FROM report_categories WHERE report_version_id = ? ORDER BY display_ordinal",
                (report_version_id,),
            ).fetchall()
            category_member_rows = connection.execute(
                """
                SELECT m.* FROM report_category_displayed_members m
                JOIN report_categories c ON c.id = m.category_id
                WHERE c.report_version_id = ?
                ORDER BY m.category_id, m.display_ordinal
                """,
                (report_version_id,),
            ).fetchall()
            investigation_finding_rows = connection.execute(
                "SELECT * FROM report_investigation_findings "
                "WHERE report_version_id = ? ORDER BY display_ordinal",
                (report_version_id,),
            ).fetchall()
        version["source_snapshot"] = self.get_source_snapshot(report_version_id)
        version["sections"] = [dict(row) for row in section_rows]
        version["claims"] = [
            self._decode_json_fields(dict(row), ("metric_refs_json",)) for row in claim_rows
        ]
        version["claim_findings"] = [dict(row) for row in finding_rows]
        version["claim_evidence"] = [dict(row) for row in evidence_rows]
        version["categories"] = [dict(row) for row in category_rows]
        version["category_members"] = [dict(row) for row in category_member_rows]
        version["investigation_findings"] = [
            self._decode_json_fields(
                dict(row),
                (
                    "related_post_refs_json", "representative_post_refs_json",
                    "audit_finding_refs_json", "evidence_refs_json",
                    "metric_refs_json", "boundary_notes_json", "post_memberships_json",
                ),
            )
            for row in investigation_finding_rows
        ]
        version["snapshot_payloads"] = self.list_snapshot_payloads(report_version_id)
        version["provider_exchanges"] = self.list_provider_exchanges(report_version_id)
        version["account_projection"] = self.get_report_account_projection(
            report_version_id
        )
        return version

    def assert_published_immutable(self, report_version_id: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE report_versions SET title = title || ' changed' WHERE id = ?",
                    (report_version_id,),
                )
        except sqlite3.IntegrityError as exc:
            if "immutable" in str(exc):
                raise PublishedReportImmutableError(str(exc)) from exc
            raise

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_json_fields(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
        for field in fields:
            if field in record:
                record[field.removesuffix("_json")] = json.loads(record.pop(field) or "{}")
        return record
