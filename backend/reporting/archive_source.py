"""Regenerate a standard report from a verified archive, without live audits."""
from pathlib import Path
import sqlite3

from backend.reporting.integration_source import CanonicalReportSource
from backend.reporting.store import ReportStore
from hermes_m0.real_report_repository import PublishedReportRepository


class ReadOnlyReportStore(ReportStore):
    def __init__(self, db_path: Path):
        self.db_path = db_path.resolve(strict=True)

    def _connect(self):
        connection = sqlite3.connect(self.db_path.as_uri() + "?mode=ro&immutable=1", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection


class ArchivedReportSource(CanonicalReportSource):
    account_source = "report_snapshot"

    def __init__(self, manifest: dict, *, outputs_dir: Path):
        super().__init__(Path(manifest["source_database"]), outputs_dir)
        PublishedReportRepository.load(
            self.db_path, report_version_id=manifest["report_version_id"],
            expected_database_sha256=manifest["source_database_sha256"],
            expected_content_hash=manifest["report_content_hash"],
            expected_snapshot_hash=manifest["snapshot_hash"],
        )
        self.report_version_id = manifest["report_version_id"]
        self.snapshot = ReadOnlyReportStore(self.db_path).load_immutable_snapshot(manifest["report_version_id"])
        if self.snapshot.task_id != manifest["task_id"]:
            raise ValueError("archive task does not match manifest")

    def frozen_snapshot(self, task_id):
        if task_id != self.snapshot.task_id:
            raise ValueError("task outside archived report scope")
        return self.snapshot

    def canonical_rows(self, task_id):
        return [f.payload for f in self.frozen_snapshot(task_id).findings]

    def task_name(self, task_id):
        return self.frozen_snapshot(task_id).display_name

    def task_status(self, task_id):
        self.frozen_snapshot(task_id)
        return "completed"

    def has_explicit_creator_target(self, task_id):
        self.frozen_snapshot(task_id)
        return False

    def creator_account_identity(self, task_id):
        self.frozen_snapshot(task_id)
        return None
