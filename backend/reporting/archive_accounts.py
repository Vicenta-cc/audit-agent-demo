"""Account activity over an explicitly authorized set of immutable reports."""
from functools import lru_cache
from pathlib import Path

from backend.reporting.account_entries import DEFAULT_ACCOUNT_FIXTURE_PATH
from hermes_m0.account_corpus import AccountCorpus
from hermes_m0.pass_support import SnapshotAccountData, SnapshotAccountRepository
from hermes_m0.real_report_repository import PublishedReportRepository


@lru_cache(maxsize=4)
def archive_account_repository(sources):
    repositories = tuple(PublishedReportRepository.load(
        path, report_version_id=version, expected_database_sha256=database_hash,
        expected_content_hash=content_hash, expected_snapshot_hash=snapshot_hash,
    ) for path, version, database_hash, content_hash, snapshot_hash in sources)
    legacy = any(r.account_source != "report_snapshot" and r.template_kind not in {
        "all_pass", "single_risk_post", "selected_existing_audits"
    } for r in repositories)
    return SnapshotAccountRepository(SnapshotAccountData(
        repositories,
        legacy_corpus=AccountCorpus.load(DEFAULT_ACCOUNT_FIXTURE_PATH) if legacy else None,
        completed_only=True,
    ))
