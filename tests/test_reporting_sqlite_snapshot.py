import sqlite3
from contextlib import closing

import pytest

from backend.reporting.sqlite_snapshot import sqlite_snapshot
from backend.reporting.integration_source import CanonicalReportSource, build_immutable_snapshot
from backend.reporting.runtime import R31ReportRuntime
from tests.test_pass_report import seed_audit


def test_failed_projection_removes_copy_and_does_not_rebind_source(tmp_path):
    source, _ = seed_audit(tmp_path)
    observed = []

    class FailedSource(CanonicalReportSource):
        def config(self, task_id):
            observed.append(self.db_path)
            raise ValueError("projection failed")

    failed = FailedSource(source.db_path, source.outputs_dir)
    with pytest.raises(ValueError, match="projection failed"):
        build_immutable_snapshot(failed, "new-search-task")
    assert failed.db_path == source.db_path
    assert observed and observed[0] != source.db_path
    assert not observed[0].parent.exists()
    assert source.task_name("new-search-task")


def test_busy_backup_times_out_without_yielding_partial_image(tmp_path):
    path = tmp_path / "locked.sqlite3"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("CREATE TABLE t(value)")
        writer.commit()
        writer.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(TimeoutError, match="backup deadline"):
                with sqlite_snapshot(path, timeout=0.01):
                    pytest.fail("a locked source must not yield a partial image")
        finally:
            writer.rollback()
        with sqlite_snapshot(path) as (copy, digest):
            assert copy.exists() and len(digest) == 64
        assert not copy.parent.exists()


def test_report_account_projection_uses_frozen_identity(tmp_path):
    seeded, store = seed_audit(tmp_path)

    def live_identity_read(*args):
        raise AssertionError("account projection read live identity after freezing")

    class FreezeThenChangeSource(CanonicalReportSource):
        def config(self, task_id):
            # The bound copy retains its original methods; later graph stages
            # must use the persisted identity instead of consulting this source.
            source.creator_account_identity = live_identity_read
            source.has_explicit_creator_target = live_identity_read
            return super().config(task_id)

    source = FreezeThenChangeSource(seeded.db_path, seeded.outputs_dir)
    result = R31ReportRuntime(store).generate(
        "new-search-task", source=source, checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )
    assert store.get_version(result.report_version_id)["status"] == "published"
    snapshot = store.load_immutable_snapshot(result.report_version_id)
    assert snapshot.statistics["source_task_identity"] == {
        "creator_account": None, "explicit_creator_target": False,
    }
