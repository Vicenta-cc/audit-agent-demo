"""Integration regression against a hash-verified selected-audits archive."""
import json
import os
from pathlib import Path

import pytest

from hermes_m0.pass_support import SnapshotAccountData, SnapshotAccountRepository
from hermes_m0.real_report_repository import PublishedReportRepository


def test_selected_audits_keep_stable_comment_identity():
    manifest_path = os.environ.get("REPORT_C_TEST_MANIFEST")
    if not manifest_path:
        pytest.skip("Set REPORT_C_TEST_MANIFEST to a frozen Report C manifest")
    manifest = json.loads(Path(manifest_path).read_text())
    repository = PublishedReportRepository.load(
        manifest["source_database"],
        report_version_id=manifest["report_version_id"],
        expected_database_sha256=manifest["source_database_sha256"],
        expected_content_hash=manifest["report_content_hash"],
        expected_snapshot_hash=manifest["snapshot_hash"],
    )
    accounts = SnapshotAccountRepository(SnapshotAccountData((repository,)))
    task_id = repository.fixture.provenance.source_task_id
    # This stored comment has a stable platform ID; it previously disappeared
    # entirely from lookup even though the full audit page could display it.
    occurrence = accounts.comment_occurrence(
        task_id, "7576551165685585318", "7576982696070398769"
    )
    assert occurrence["account_ref"]
    activity = accounts.corpus.occurrences_for(occurrence["account_ref"])
    assert occurrence in activity
    assert all(item["task_id"] == task_id for item in activity)
    assert set(accounts.corpus.authorized_task_ids) == {task_id}
    assert accounts.display_name(occurrence["account_ref"]) == "桔子G"
