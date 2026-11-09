import hashlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.audit_agent.manual_review import blocked_review_fields
from backend.investigation_creation.adapters import _public_audit_result
from backend.investigation_creation.worker import InvestigationWorker
from backend.reporting.errors import ReportValidationError
from backend.reporting.runtime import R31ReportRuntime
from backend.reporting.single_post_graph import SinglePostReportGraph
from hermes_m0.real_report_repository import PublishedReportRepository
from test_pass_report import seed_audit


def blocked_result():
    return {
        "decision": "review", "risk_level": "unknown", "risk_score": None,
        "audit_completion_status": "partial", "manual_review_required": True,
        "audit_gaps": [{"stage": "fusion_audit", "error_code": "audit_content_blocked",
                        "status": "unreviewed", "manual_review_required": True,
                        "reason": "private provider diagnostic"}],
    }


@pytest.mark.parametrize(("pending", "safe", "single"), [(10, 0, False), (2, 1, False), (1, 0, False), (1, 0, True)])
def test_blocked_report_can_be_published_and_reopened(tmp_path, pending, safe, single):
    source, store = seed_audit(
        tmp_path, verdicts=[("review", "unknown")] * pending + [("pass", "none")] * safe,
        comments=[{"comment_id": "media-only", "content": "", "audit_source": "local_empty",
                   "audit_status": "completed", "risk_score": 0, "risk_level": "none"}],
    )
    with sqlite3.connect(store.db_path) as conn:
        for key, raw in conn.execute("select id,result_json from audit_results where risk_level='unknown'").fetchall():
            record = json.loads(raw)
            record.update(blocked_result())
            conn.execute("update audit_results set result_json=? where id=?", (json.dumps(record), key))
    provider = Mock()
    provider.generate_structured.side_effect = AssertionError("stored results must be sufficient")
    runtime = R31ReportRuntime(store, graph_factory=SinglePostReportGraph) if single else R31ReportRuntime(store)
    published = runtime.generate(
        "new-search-task", source=source, model_client=provider,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )
    version = store.get_version(published.report_version_id)
    assert version["status"] == "published"
    assert "private provider diagnostic" not in version["body_markdown"]
    assert "待人工复核" in version["body_markdown"]
    assert "本次分析数据完整" not in version["body_markdown"]
    provider.generate_structured.assert_not_called()
    view = store.get_presentation_projection(published.report_version_id)
    assert view["statistics"]["pending_manual_review_posts"] == pending
    assert view["statistics"]["risk_assessed_posts"] == safe
    assert view["statistics"]["collected_posts"] == pending + safe
    assert view["statistics"]["risk_distribution"]["total"] == safe
    assert view["statistics"]["risk_level"]["none"] == safe
    assert view["statistics"]["independently_reviewed_comments"] == 0
    overview = "".join(view["investigation_summary"]["paragraphs"])
    assert f"风险未定、待人工复核 {pending} 条" in overview
    group = next(s for s in view["ordered_sections"] if s["presentation_kind"] == (
        "audit_samples" if single else "pending_post_analysis"
    ))
    posts = group["sample_posts"] if single else group["group_posts"]
    assert len(posts) == pending
    assert all(s["presentation_kind"] != "risk_post_analysis" for s in view["ordered_sections"])
    for post in posts:
        detail = store.get_presentation_post_detail(published.report_version_id, post_ref=post["post_ref"])
        assert detail["pending_manual_review"] and detail["risk_level"] == "unknown"
        assert detail["audit_gap_stages"] == ["整帖汇总审核"]
        assert detail["audit_source"]["task_id"] == "new-search-task"
    snapshot = store.get_source_snapshot(published.report_version_id)
    frozen_db = tmp_path / "published.sqlite3"
    with sqlite3.connect(store.db_path) as reader, sqlite3.connect(frozen_db) as output:
        reader.backup(output)
    repository = PublishedReportRepository.load(
        frozen_db, report_version_id=published.report_version_id,
        expected_database_sha256=hashlib.sha256(frozen_db.read_bytes()).hexdigest(),
        expected_content_hash=version["content_hash"], expected_snapshot_hash=snapshot["snapshot_hash"],
    )
    assert repository.comment_audit_coverage["completed"] == 0


@pytest.mark.parametrize("changes", [
    {"audit_completion_status": "completed"}, {"manual_review_required": False},
    {"risk_score": 0}, {"decision": "pass"}, {"audit_gaps": []},
    {"audit_gaps": [{"stage": "fusion_audit", "error_code": "network_error"}]},
])
def test_unverified_unknown_fails_worker_gate(monkeypatch, changes):
    import backend.investigation_creation.worker as implementation
    worker = object.__new__(InvestigationWorker)
    worker.execution_adapter = Mock()
    run = SimpleNamespace(job_id="test", confirmed_configuration={})
    execution = SimpleNamespace(geographic_plan=None, analyze_limit=1, auto_analyze=None)
    monkeypatch.setattr(implementation, "parse_confirmed_configuration_snapshot",
                        lambda _: SimpleNamespace(schema_version="investigation-run-config-v5", execution=execution))
    valid = dict(blocked_result(), content_key="post")
    def state(result):
        return {"task_stats": {"ingested_count": 1, "completed_analysis_count": 1},
                "audit_results": [_public_audit_result(result)]}
    assert worker._completion_gate_failure(run, state=state(valid)) is None
    invalid = valid | changes
    assert not blocked_review_fields(invalid)
    assert worker._completion_gate_failure(run, state=state(invalid))[0] == "completed_audit_result_invalid"


def test_unexplained_unknown_cannot_publish(tmp_path):
    source, store = seed_audit(tmp_path, verdicts=[("review", "unknown")])
    with pytest.raises(ReportValidationError, match="supported decision and risk level"):
        R31ReportRuntime(store).generate("new-search-task", source=source, checkpoint_path=tmp_path / "checkpoints.sqlite3")
    with store._connect() as conn:
        assert conn.execute("select count(*) from report_versions where status='published'").fetchone()[0] == 0
