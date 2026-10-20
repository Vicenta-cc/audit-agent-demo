"""Report correction preserves frozen inputs and historical digest contracts."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace

import pytest

from backend.reporting.errors import ReportGenerationError, ReportValidationError
from backend.reporting.integration_source import build_immutable_snapshot
from backend.reporting.target_account_correction import _validated_target_identity
from backend.reporting.runtime import R31ReportRuntime
from backend.reporting.target_account_correction import publish_target_account_role_correction
from tests.test_pass_report import seed_audit


def test_unsupported_correction_preserves_original_and_creates_no_version(tmp_path):
    source, store = seed_audit(tmp_path)
    generated = R31ReportRuntime(store).generate(
        "new-search-task", source=source, checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )
    original = store.get_full_version(generated.report_version_id)
    original_snapshot = store.load_immutable_snapshot(generated.report_version_id)
    with closing(sqlite3.connect(source.db_path)) as connection:
        before = connection.execute("SELECT COUNT(*) FROM report_versions").fetchone()[0]
    with pytest.raises(ReportGenerationError, match="successful model-step set"):
        publish_target_account_role_correction(
            store=store, source_report_version_id=generated.report_version_id, report_source=source,
        )
    assert store.get_full_version(generated.report_version_id) == original
    assert store.load_immutable_snapshot(generated.report_version_id) == original_snapshot
    with closing(sqlite3.connect(source.db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM report_versions").fetchone()[0] == before


@pytest.mark.parametrize("mutation", ["configuration", "original_text", "creator_identity"])
def test_changed_task_is_rejected_before_creating_version(tmp_path, mutation):
    source, store = seed_audit(tmp_path)
    generated = R31ReportRuntime(store).generate(
        "new-search-task", source=source, checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )
    with closing(sqlite3.connect(source.db_path)) as connection:
        before = connection.execute("SELECT COUNT(*) FROM report_versions").fetchone()[0]
        if mutation == "configuration":
            connection.execute("UPDATE task_audit_config_revisions SET audit_config_json=?", ('{"capabilities":["image"]}',))
        elif mutation == "original_text":
            row = connection.execute("SELECT id,result_json FROM audit_results").fetchone()
            payload = json.loads(row[1])
            payload["desc"] = "changed original text"
            connection.execute("UPDATE audit_results SET result_json=? WHERE id=?", (json.dumps(payload), row[0]))
        else:
            connection.execute("UPDATE jobs SET crawl_mode='creator',creator_id='different-creator' WHERE id='new-search-task'")
        connection.commit()
    with pytest.raises(ReportValidationError, match="source data or configuration differs"):
        publish_target_account_role_correction(
            store=store, source_report_version_id=generated.report_version_id, report_source=source,
        )
    with closing(sqlite3.connect(source.db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM report_versions").fetchone()[0] == before


def test_unrelated_task_does_not_invalidate_correction_guard(tmp_path):
    source, _ = seed_audit(tmp_path)
    snapshot = build_immutable_snapshot(source, "new-search-task")
    with closing(sqlite3.connect(source.db_path)) as connection:
        connection.execute(
            "INSERT INTO jobs(id,status,control,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("other-task", "running", "", "2026-10-20", "2026-10-20"),
        )
        connection.commit()
    assert _validated_target_identity(source, snapshot) is None


def test_unknown_digest_kind_is_rejected(tmp_path):
    source, _ = seed_audit(tmp_path)
    snapshot = build_immutable_snapshot(source, "new-search-task")
    snapshot = replace(snapshot, statistics={**snapshot.statistics, "source_db_digest_kind": "unknown-v99"})
    with pytest.raises(ReportValidationError, match="unsupported source database digest kind"):
        _validated_target_identity(source, snapshot)


@pytest.mark.parametrize("kind", ["unknown-v99", None])
def test_digest_kind_tampering_is_rejected_by_snapshot_loader(tmp_path, kind, monkeypatch):
    source, store = seed_audit(tmp_path)
    generated = R31ReportRuntime(store).generate(
        "new-search-task", source=source, checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )
    manifest = store.get_source_snapshot(generated.report_version_id)
    stats = manifest["statistics"]
    if kind is None:
        stats.pop("source_db_digest_kind")
    else:
        stats["source_db_digest_kind"] = kind
    # SQLite's immutable-row trigger already prevents ordinary UPDATE. Feed a
    # corrupted manifest at the storage boundary to test the independent hash
    # validation used when loading a restored or externally altered database.
    monkeypatch.setattr(store, "get_source_snapshot", lambda version_id: manifest)
    with pytest.raises(ReportGenerationError, match="Snapshot hash does not match"):
        store.load_immutable_snapshot(generated.report_version_id)


def test_legacy_guard_preserves_hash_contract_and_worker_visibility(tmp_path):
    source, _ = seed_audit(tmp_path)
    snapshot = build_immutable_snapshot(source, "new-search-task")
    legacy_statistics = {key: value for key, value in snapshot.statistics.items() if key != "source_db_digest_kind"}
    # Compute the historical main-file digest in an independent process too;
    # fixture connections may still be alive in this process.
    digest = subprocess.run(
        [sys.executable, "-c", "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], 'rb').read()).hexdigest())", str(source.db_path)],
        check=True, capture_output=True, text=True, timeout=20,
    ).stdout.strip()
    legacy = replace(snapshot, source_db_sha256=digest, statistics=legacy_statistics)
    with closing(sqlite3.connect(source.db_path)) as worker:
        worker.execute("SELECT COUNT(*) FROM jobs").fetchone()
        assert _validated_target_identity(source, legacy) is None
        with pytest.raises(ReportValidationError, match="database differs"):
            _validated_target_identity(source, replace(legacy, source_db_sha256="0" * 64))
        with pytest.raises(ReportValidationError, match="configuration does not match"):
            _validated_target_identity(source, replace(legacy, display_name="wrong name"))
        subprocess.run(
            [sys.executable, "-c", "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); c.execute('SELECT COUNT(*) FROM jobs').fetchone(); c.close()", str(source.db_path)],
            check=True, capture_output=True, text=True, timeout=20,
        )
        worker.execute("INSERT INTO job_logs(job_id,time,message) VALUES('new-search-task','now','after legacy validation')")
        worker.commit()
        result = subprocess.run(
            [sys.executable, "-c", "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute(\"SELECT COUNT(*) FROM job_logs WHERE message='after legacy validation'\").fetchone()[0]); c.close()", str(source.db_path)],
            check=True, capture_output=True, text=True, timeout=20,
        )
        assert result.stdout.strip() == "1"
