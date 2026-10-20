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


class SyntheticR31Provider:
    """Synthetic prose responses; every real schema and semantic validator runs."""

    model = "synthetic-r31-contract-test"
    prompt_version = "synthetic-r31-contract-test-v1"

    def __init__(self):
        self.calls = []

    def generate_structured(self, *, messages, response_model, **kwargs):
        from backend.reporting.contracts import ModelStepResult

        payload = json.loads(next(
            message["content"].split("输入：\n", 1)[1]
            for message in reversed(messages) if "输入：\n" in message["content"]
        ))
        text = "现有审核记录提示相关内容需要复核，结论限于当前调查资料。"
        if response_model.__name__ == "ReportFindingPlan":
            output = {"investigation_findings": [{
                "finding_alias": "IF1", "title": "需复核的内容", "statement": text,
                "post_memberships": [{
                    "post_ref": item["post_alias"],
                    "audit_finding_ref": item["audit_finding_alias"],
                    "is_representative": index == 0,
                    "membership_evidence_refs": [e["evidence_alias"] for e in item["direct_evidence"]],
                } for index, item in enumerate(payload["risk_posts"])],
            }], "standalone_risk_posts": []}
        elif response_model.__name__ == "RiskSectionDraft":
            finding = payload["investigation_finding"]
            output = {
                "section_id": payload["section"]["section_id"],
                "title": payload["section"]["title"],
                "paragraphs": [{"text": text, "claim_ids": ["review-claim"]}],
                "claims": [{"claim_id": "review-claim", "claim_type": "domain_fact", "text": text,
                    "investigation_finding_refs": [finding["finding_alias"]],
                    "audit_finding_refs": [item["audit_finding_ref"] for item in finding["post_memberships"]],
                    "evidence_refs": [e for item in finding["post_memberships"] for e in item["membership_evidence_refs"]], "metric_refs": [], "support_type": "direct"}],
            }
        elif response_model.__name__ == "SynthesisConclusionDraft":
            audit_refs = sorted({ref for claim in payload["verified_section_claims"]
                                 for ref in claim.get("audit_finding_refs", [])})
            evidence_refs = sorted({ref for claim in payload["verified_section_claims"]
                                    for ref in claim.get("evidence_refs", [])})
            output = {key: [{"text": text, "audit_finding_refs": audit_refs,
                            "evidence_refs": evidence_refs, "metric_refs": []}]
                      for key in ("synthesis_paragraphs", "conclusion_paragraphs")}
        else:
            raise AssertionError(f"unexpected provider schema: {response_model.__name__}")
        validated = response_model.model_validate(output)
        self.calls.append(response_model.__name__)
        return ModelStepResult(output=validated.model_dump(mode="json"), model=self.model,
                               usage={"total_tokens": 1})


def test_r31_correction_publishes_with_original_report_unchanged(tmp_path):
    from backend.reporting.r31_graph import AccountOverviewReportGraph
    from backend.reporting.sqlite_snapshot import sqlite_snapshot
    from hermes_m0.account_corpus import build_account_fixture

    source, store = seed_audit(tmp_path, verdicts=[("pass", "none"), ("review", "low"), ("review", "medium"), ("review", "high")])
    with closing(sqlite3.connect(source.db_path)) as connection:
        connection.execute("UPDATE jobs SET crawl_mode='creator',creator_id='stable-author' WHERE id='new-search-task'")
        for row in connection.execute("SELECT id,result_json FROM audit_results").fetchall():
            payload = json.loads(row[1])
            payload["content_title"] = "合成审核样本"
            payload["evidence_items"] = [{"evidence_id": "ocr-review", "primary_modality": "ocr",
                "source": "video:1/frame:review", "ocr_text": "相关内容需要复核"}]
            connection.execute("UPDATE audit_results SET result_json=? WHERE id=?", (json.dumps(payload), row[0]))
        connection.commit()
    corpus = tmp_path / "synthetic-account-corpus.json.gz"
    # The historical corpus builder hashes its input, so give it a private,
    # closed consistent image, never the live SQLite file.
    with sqlite_snapshot(source.db_path) as (frozen, _):
        build_account_fixture(source_db=frozen, output_path=corpus, task_ids=["new-search-task"])
    provider = SyntheticR31Provider()
    graph = AccountOverviewReportGraph(source=source, store=store, model_client=provider,
        checkpoint_path=tmp_path / "r31-checkpoints.sqlite3", account_fixture_path=corpus)
    try:
        generated = graph.generate("new-search-task")
    finally:
        graph.close()
    original = store.get_full_version(generated.report_version_id)
    original_snapshot = store.load_immutable_snapshot(generated.report_version_id)
    assert provider.calls == ["ReportFindingPlan", "RiskSectionDraft", "SynthesisConclusionDraft"]
    with closing(sqlite3.connect(source.db_path)) as connection:
        connection.execute(
            "INSERT INTO jobs(id,status,control,created_at,updated_at) VALUES(?,?,?,?,?)",
            ("unrelated-new-task", "running", "", "2026-10-20", "2026-10-20"),
        )
        connection.commit()
    result = publish_target_account_role_correction(store=store,
        source_report_version_id=generated.report_version_id, report_source=source,
        account_fixture_path=corpus)
    assert result["status"] == "published"
    assert result["reused_model_step_count"] == 3
    assert result["provider_request_count"] == 0
    assert store.get_full_version(generated.report_version_id) == original
    assert store.load_immutable_snapshot(generated.report_version_id) == original_snapshot
    corrected = store.load_immutable_snapshot(result["report_version_id"])
    assert corrected.snapshot_ref != original_snapshot.snapshot_ref
    assert replace(corrected, snapshot_ref=original_snapshot.snapshot_ref) == original_snapshot
    assert store.get_frontend_report(result["report_version_id"])
    from hermes_m0.real_report_repository import PublishedReportRepository
    with sqlite_snapshot(source.db_path) as (frozen, digest):
        for version_id in (generated.report_version_id, result["report_version_id"]):
            version = store.get_full_version(version_id)
            loaded = PublishedReportRepository.load(frozen, report_version_id=version_id,
                expected_database_sha256=digest, expected_content_hash=version["content_hash"],
                expected_snapshot_hash=original_snapshot.snapshot_hash)
            assert loaded.snapshot_hash == original_snapshot.snapshot_hash
            assert len(loaded.fixture.posts) == len(original_snapshot.posts)
    # A valid model-backed source must also reject changed task inputs before
    # allocating another run or report version.
    with closing(sqlite3.connect(source.db_path)) as connection:
        before = connection.execute("SELECT COUNT(*) FROM report_versions").fetchone()[0]
        runs_before = connection.execute("SELECT COUNT(*) FROM report_generation_runs").fetchone()[0]
        connection.execute("UPDATE jobs SET creator_id='changed-author' WHERE id='new-search-task'")
        connection.commit()
    with pytest.raises(ReportValidationError, match="source data or configuration differs"):
        publish_target_account_role_correction(store=store,
            source_report_version_id=generated.report_version_id, report_source=source,
            account_fixture_path=corpus)
    with closing(sqlite3.connect(source.db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM report_versions").fetchone()[0] == before
        assert connection.execute("SELECT COUNT(*) FROM report_generation_runs").fetchone()[0] == runs_before
    assert store.get_full_version(generated.report_version_id) == original


def test_historical_cc82_snapshot_correction_publishes_and_loads_in_hermes(tmp_path):
    import gzip
    import hashlib
    from pathlib import Path

    from backend.reporting.integration_source import (
        CanonicalReportSource, ImmutableReportSnapshot, SnapshotPost, SnapshotFinding, SnapshotEvidence,
    )
    from backend.reporting.r31_graph import AccountOverviewReportGraph
    from backend.reporting.sqlite_snapshot import sqlite_snapshot
    from backend.reporting.store import ReportStore
    from hermes_m0.real_report_repository import PublishedReportRepository

    fixtures = Path(__file__).parent / "fixtures" / "reporting_legacy_cc82"
    provenance = json.loads((fixtures / "provenance.json").read_text())
    source_bytes = gzip.decompress((fixtures / "source.sqlite3.gz").read_bytes())
    assert hashlib.sha256(source_bytes).hexdigest() == provenance["source_sha256"]
    source_path = tmp_path / "historical-source.sqlite3"
    source_path.write_bytes(source_bytes)
    data = json.loads((fixtures / "snapshot.json").read_text())
    data["posts"] = tuple(SnapshotPost(**item) for item in data["posts"])
    data["findings"] = tuple(SnapshotFinding(**item) for item in data["findings"])
    data["evidence"] = tuple(SnapshotEvidence(**{**item, "source_formats": tuple(item["source_formats"])})
                             for item in data["evidence"])
    historical = ImmutableReportSnapshot(**data)
    assert "source_db_digest_kind" not in historical.statistics
    assert historical.snapshot_hash == provenance["snapshot_hash"]

    class HistoricalSource(CanonicalReportSource):
        def frozen_snapshot(self, task_id):
            assert task_id == historical.task_id
            return historical

        def task_status(self, task_id):
            assert task_id == historical.task_id
            return "completed"

    source = HistoricalSource(source_path, tmp_path / "outputs")
    store = ReportStore(tmp_path / "published.sqlite3")
    corpus = fixtures / "account-corpus.json.gz"
    provider = SyntheticR31Provider()
    graph = AccountOverviewReportGraph(source=source, store=store, model_client=provider,
        checkpoint_path=tmp_path / "legacy-checkpoints.sqlite3", account_fixture_path=corpus)
    try:
        generated = graph.generate(historical.task_id)
    finally:
        graph.close()
    original = store.get_full_version(generated.report_version_id)
    before_snapshot = store.load_immutable_snapshot(generated.report_version_id)
    assert before_snapshot.snapshot_hash == historical.snapshot_hash
    result = publish_target_account_role_correction(store=store,
        source_report_version_id=generated.report_version_id, report_source=source,
        account_fixture_path=corpus)
    assert result["status"] == "published"
    assert result["reused_model_step_count"] == 3
    assert provider.calls == ["ReportFindingPlan", "RiskSectionDraft", "SynthesisConclusionDraft"]
    assert store.get_full_version(generated.report_version_id) == original
    assert store.load_immutable_snapshot(generated.report_version_id) == before_snapshot
    assert store.load_immutable_snapshot(result["report_version_id"]).snapshot_hash == historical.snapshot_hash
    with sqlite_snapshot(store.db_path) as (frozen, digest):
        for version_id in (generated.report_version_id, result["report_version_id"]):
            version = store.get_full_version(version_id)
            loaded = PublishedReportRepository.load(frozen, report_version_id=version_id,
                expected_database_sha256=digest, expected_content_hash=version["content_hash"],
                expected_snapshot_hash=historical.snapshot_hash)
            assert loaded.snapshot_hash == historical.snapshot_hash
            assert len(loaded.fixture.posts) == len(historical.posts)
    with closing(sqlite3.connect(store.db_path)) as connection:
        versions_before = connection.execute("SELECT COUNT(*) FROM report_versions").fetchone()[0]
        runs_before = connection.execute("SELECT COUNT(*) FROM report_generation_runs").fetchone()[0]
    with closing(sqlite3.connect(source_path)) as connection:
        connection.execute("UPDATE jobs SET display_name='changed historical source'")
        connection.commit()
    with pytest.raises(ReportValidationError, match="database differs"):
        publish_target_account_role_correction(store=store,
            source_report_version_id=generated.report_version_id, report_source=source,
            account_fixture_path=corpus)
    with closing(sqlite3.connect(store.db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM report_versions").fetchone()[0] == versions_before
        assert connection.execute("SELECT COUNT(*) FROM report_generation_runs").fetchone()[0] == runs_before
    assert store.get_full_version(generated.report_version_id) == original
