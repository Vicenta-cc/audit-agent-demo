"""Report freezing must preserve the live worker's cross-process WAL visibility."""
from __future__ import annotations

import gc
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from backend.reporting.integration_source import build_immutable_snapshot
from tests.test_pass_report import seed_audit


@pytest.mark.skipif(os.name != "posix", reason="POSIX SQLite file-lock regression")
def test_report_freeze_preserves_next_task_and_logs_across_processes(tmp_path):
    source, _ = seed_audit(tmp_path)
    gc.collect()
    previously_enabled = gc.isenabled()
    gc.disable()
    try:
        # Keep the worker connection alive across report generation and round two.
        # Disabling GC also keeps legacy unclosed source connections alive, making
        # accidental lock release deterministic on affected SQLite versions.
        with closing(sqlite3.connect(source.db_path)) as worker:
            assert worker.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            worker.execute("PRAGMA wal_autocheckpoint=0")
            worker.execute("UPDATE jobs SET display_name='first report' WHERE id='new-search-task'")
            worker.commit()
            snapshot = build_immutable_snapshot(source, "new-search-task")
            assert snapshot.display_name == "first report"

            # A normal independent API process opens a read/write connection,
            # performs only SELECT, and closes it. It must not tear down a WAL
            # still in use by the worker, even after report checksum generation.
            subprocess.run(
                [sys.executable, "-c", "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
                 "assert c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 1; c.close()",
                 str(source.db_path)],
                check=True, capture_output=True, text=True, timeout=20,
            )
            worker.execute(
                "INSERT INTO jobs(id,status,control,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("second-task", "running", "", "2026-10-20", "2026-10-20"),
            )
            worker.execute(
                "INSERT INTO job_logs(job_id,time,message) VALUES(?,?,?)",
                ("second-task", "2026-10-20", "collecting second round"),
            )
            worker.commit()
            result = subprocess.run(
                [sys.executable, "-c", "import sqlite3,sys,json; c=sqlite3.connect(sys.argv[1]); "
                 "print(json.dumps([c.execute(\"SELECT status FROM jobs WHERE id='second-task'\").fetchall(),"
                 "c.execute(\"SELECT message FROM job_logs WHERE job_id='second-task'\").fetchall()])); c.close()",
                 str(source.db_path)],
                check=True, capture_output=True, text=True, timeout=20,
            )
            assert json.loads(result.stdout) == [[["running"]], [["collecting second round"]]]
    finally:
        gc.collect()
        if previously_enabled:
            gc.enable()


def test_report_inputs_and_digest_share_one_wal_inclusive_database_copy(tmp_path):
    import hashlib

    from backend.reporting.integration_source import CanonicalReportSource

    source, _ = seed_audit(tmp_path)
    live_path = source.db_path
    observed = {}

    class ConcurrentUpdateSource(CanonicalReportSource):
        def config(self, task_id):
            # This hook runs after canonical rows have been read. All remaining
            # reads and the source digest must use the same independent image.
            assert self.db_path != live_path
            observed["digest"] = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
            with closing(sqlite3.connect(live_path)) as concurrent_writer:
                concurrent_writer.execute(
                    "UPDATE jobs SET display_name='changed during report' WHERE id=?",
                    (task_id,),
                )
                concurrent_writer.commit()
            return super().config(task_id)

    with closing(sqlite3.connect(live_path)) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "UPDATE jobs SET display_name='committed in WAL' WHERE id='new-search-task'"
        )
        writer.commit()
        assert live_path.with_name(live_path.name + "-wal").stat().st_size > 0
        snapshot = build_immutable_snapshot(
            ConcurrentUpdateSource(live_path, source.outputs_dir), "new-search-task"
        )
        assert snapshot.display_name == "committed in WAL"
        assert snapshot.source_db_sha256 == observed["digest"]
        assert len(snapshot.posts) == 1
        assert writer.execute(
            "SELECT display_name FROM jobs WHERE id='new-search-task'"
        ).fetchone()[0] == "changed during report"
