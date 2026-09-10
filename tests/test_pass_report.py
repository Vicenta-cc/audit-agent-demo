from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.reporting import create_reporting_router
from backend.audit_agent.audit_policy_store import TaskAuditConfigRevisionStore
from backend.audit_agent.ingestion import AuditResultStore, IngestionStore
from backend.audit_agent.job_store import JobStore
from backend.reporting.errors import ReportValidationError
from backend.reporting.integration_source import CanonicalReportSource
from backend.reporting.pass_graph import PASS_TEMPLATE_VERSION, PassReportGraph
from backend.reporting.r31_graph import AccountOverviewReportGraph
from backend.reporting.runtime import R31ReportRuntime
from backend.reporting.store import ReportStore


def seed_audit(tmp_path: Path, *, count=1, decision="pass", risk="none", comments=None):
    db = tmp_path / "audit.sqlite3"
    jobs = JobStore(db)
    jobs.create(job_id="new-search-task", platform="dy", status="completed", display_name="单条帖子审核演示", crawl_mode="search", max_notes=count, analyze_limit=count)
    revision = TaskAuditConfigRevisionStore(db).create(job_id="new-search-task", audit_config={"capabilities": ["text"]})
    ingestion = IngestionStore(db)
    results = AuditResultStore(db)
    batch = tmp_path / "batch.json"
    contents = [{"aweme_id": str(10001 + index), "title": f"日常分享 {index + 1}", "desc": f"日常分享 {index + 1}", "create_time": 1788825600} for index in range(count)]
    batch.write_text(json.dumps({"task_id": "new-search-task", "platform": "dy", "items": contents}))
    refs = ingestion.ingest_batch(batch, tmp_path / "raw")
    assert len(refs) == count
    for index, ref in enumerate(refs):
        result = results.upsert_result(job_id="new-search-task", platform="dy", content_key=str(10001 + index), content_id=ref["content_id"], audit_config_revision_id=revision["id"], result={
            "title": contents[index]["title"], "desc": "今天去公园散步，记录日常生活。",
            "url": f"https://www.douyin.com/video/{10001 + index}",
            "author": {"nickname": "生活记录者", "sec_uid": "stable-author"},
            "decision": decision, "risk_level": risk,
            "summary": "内容为日常生活分享，未检出本次规则覆盖的风险。",
            "comments": comments or [], "evidence_items": [],
            "video_results": [{"transcript": {
                "text": "A walk in the park.", "text_zh": "在公园散步。",
                "asr_raw_rel": "private/transcript.json", "provider": "internal-provider",
            }}],
        })
        ingestion.mark_content_status("dy", str(10001 + index), "completed", task_id="new-search-task", audit_result_id=result["id"])
    return CanonicalReportSource(db, tmp_path / "outputs"), ReportStore(db)


@pytest.mark.parametrize("count", [1, 5])
def test_pass_report_publishes_without_provider_and_preserves_samples(tmp_path, count):
    source, store = seed_audit(tmp_path, count=count)
    provider = Mock()
    provider.generate_structured.side_effect = AssertionError("pass report must not invoke Qwen")
    runtime = R31ReportRuntime(store)
    result = runtime.generate("new-search-task", source=source, model_client=provider, checkpoint_path=tmp_path / "checkpoints.sqlite3")
    provider.generate_structured.assert_not_called()
    version = store.get_version(result.report_version_id)
    assert version["status"] == "published"
    assert version["model"] == "deterministic"
    assert version["prompt_version"] == PASS_TEMPLATE_VERSION
    # Reopen the durable store, as the API does after a process restart.
    reopened = ReportStore(store.db_path)
    view = reopened.get_presentation_projection(result.report_version_id)
    assert view["statistics"]["decision"] == {"pass": count, "review": 0, "reject": 0}
    assert [section["section_number"] for section in view["ordered_sections"]] == ["1", "2", "2.1", "2.2", "2.3", "3", "4", "5"]
    assert all(section["presentation_kind"] != "standalone_risk_posts" for section in view["ordered_sections"])
    samples = next(section for section in view["ordered_sections"] if section["presentation_kind"] == "audit_samples")
    assert samples["sample_total"] == count
    assert len(samples["sample_posts"]) == min(count, 3)
    detail = reopened.get_presentation_post_detail(result.report_version_id, post_ref=samples["sample_posts"][0]["post_ref"])
    assert detail["decision"] == "pass"
    assert detail["audit_summary"] == "内容为日常生活分享，未检出本次规则覆盖的风险。"
    assert detail["source_url"] == "https://www.douyin.com/video/10001"
    assert detail["original_text"] == "今天去公园散步，记录日常生活。"
    assert detail["transcripts"] == ["语音转写原文：A walk in the park.", "已有中文译文：在公园散步。"]
    assert "private/transcript.json" not in json.dumps(view)
    assert "全部判定为通过" in view["investigation_summary"]["paragraphs"][0]
    assert detail["direct_evidence"] == []
    assert detail["audit_source"]["task_id"] == "new-search-task"
    with store._connect() as connection:
        source_result = connection.execute("SELECT content_key FROM audit_results WHERE id=?", (detail["audit_source"]["output_id"],)).fetchone()
    assert source_result["content_key"] == "10001"
    assert view["accounts"]["snapshot_summary"]["entries"] == [{"display_name": "生活记录者", "published_post_count": count}]
    app = FastAPI()
    app.include_router(create_reporting_router(reopened))
    with TestClient(app) as client:
        base = f"/api/report-versions/{result.report_version_id}"
        response = client.get(base + "/presentation-projection")
        assert response.status_code == 200
        assert response.json() == view
        response = client.get(base + "/posts/" + detail["post_ref"])
        assert response.status_code == 200
        assert response.json() == detail
        response = client.get(base + "/appendix", params={"view": "posts", "limit": 1})
        assert response.status_code == 200
        page = response.json()
        assert page["matched_count"] == count
        assert len(page["items"]) == 1
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM report_provider_exchanges").fetchone()[0] == 0


def test_pass_post_with_risk_comment_is_not_published_as_safe(tmp_path):
    source, store = seed_audit(tmp_path, comments=[{"comment_id": "comment-risk", "audit_status": "completed", "risk_level": "high", "content": "risky comment"}])
    with pytest.raises(ReportValidationError, match="risk comments"):
        R31ReportRuntime(store).generate("new-search-task", source=source, checkpoint_path=tmp_path / "checkpoints.sqlite3")
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM report_versions WHERE status = 'published'").fetchone()[0] == 0


def test_pending_comments_are_reported_as_unreviewed(tmp_path):
    source, store = seed_audit(tmp_path, comments=[{"comment_id": "comment-pending", "audit_status": "pending", "risk_level": "none", "content": "not yet reviewed"}])
    result = R31ReportRuntime(store).generate("new-search-task", source=source, checkpoint_path=tmp_path / "checkpoints.sqlite3")
    document = store.get_frontend_report(result.report_version_id)
    assert document["comment_audit_coverage"] == {"total": 1, "completed": 0, "unreviewed": 1}
    assert "不纳入无风险结论" in json.dumps(document, ensure_ascii=False)


@pytest.mark.parametrize("status", ["failed", "pending", "queued"])
@pytest.mark.parametrize("risk", [None, "", "unknown", "unavailable"])
def test_missing_comment_verdict_does_not_block_publication(tmp_path, status, risk):
    source, store = seed_audit(tmp_path, comments=[
        {"comment_id": "ok", "audit_status": "completed", "risk_level": "none"},
        {"comment_id": "missing", "audit_status": status, "risk_level": risk,
         "content": "保留这条评论", "audit_error": "comment result missing after retries"},
    ])
    result = R31ReportRuntime(store).generate("new-search-task", source=source, checkpoint_path=tmp_path / "checkpoints.sqlite3")
    assert store.get_version(result.report_version_id)["status"] == "published"
    document = store.get_frontend_report(result.report_version_id)
    assert document["comment_audit_coverage"] == {"total": 2, "completed": 1, "unreviewed": 1}
    assert "不纳入无风险结论" in json.dumps(document, ensure_ascii=False)
    with store._connect() as connection:
        payload = json.loads(connection.execute("SELECT payload_json FROM report_snapshot_posts WHERE report_version_id=?", (result.report_version_id,)).fetchone()[0])
        raw = json.loads(connection.execute("SELECT result_json FROM audit_results").fetchone()[0])
    missing = next(c for c in payload["comments"] if c["comment_id"] == "missing")
    assert missing["audit_status"] == status
    assert missing["risk_level"] == "unavailable"
    assert missing["content"] == "保留这条评论"
    assert raw["comments"][1]["audit_error"] == "comment result missing after retries"
    assert raw["comments"][1]["risk_level"] == risk


@pytest.mark.parametrize("status,risk", [("completed", None), ("completed", "unknown"), ("failed", "garbage")])
def test_invalid_comment_verdict_still_blocks_publication(tmp_path, status, risk):
    source, store = seed_audit(tmp_path, comments=[{"comment_id": "invalid", "audit_status": status, "risk_level": risk}])
    with pytest.raises(ValueError, match="invalid risk level"):
        R31ReportRuntime(store).generate("new-search-task", source=source, checkpoint_path=tmp_path / "checkpoints.sqlite3")


def test_in_progress_task_cannot_publish_a_partial_all_pass_report(tmp_path):
    source, store = seed_audit(tmp_path)
    JobStore(store.db_path).update("new-search-task", status="analysis_running")
    with pytest.raises(ReportValidationError, match="completed audit task"):
        R31ReportRuntime(store).generate(
            "new-search-task", source=source, checkpoint_path=tmp_path / "checkpoints.sqlite3"
        )
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM report_versions WHERE status = 'published'").fetchone()[0] == 0


def test_resume_uses_frozen_pass_template_without_provider_or_live_reclassification(tmp_path, monkeypatch):
    source, store = seed_audit(tmp_path)
    checkpoint = tmp_path / "checkpoints.sqlite3"
    with monkeypatch.context() as patch:
        def unavailable(_self, _state):
            raise RuntimeError("publication temporarily unavailable")
        patch.setattr(PassReportGraph, "_publish_report_version", unavailable)
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            R31ReportRuntime(store).generate("new-search-task", source=source, checkpoint_path=checkpoint)
    run = store.get_latest_run_for_task("new-search-task")
    monkeypatch.setattr(source, "canonical_rows", Mock(side_effect=AssertionError("must use frozen source")))
    provider = Mock()
    resumed = R31ReportRuntime(store).resume(run["id"], source=source, model_client=provider, checkpoint_path=checkpoint)
    assert resumed.report_version_id == run["report_version_id"]
    assert store.get_version(resumed.report_version_id)["status"] == "published"
    provider.generate_structured.assert_not_called()


@pytest.mark.parametrize("decision,risk", [("review", "high"), ("reject", "high"), ("pass", "low")])
def test_risk_results_keep_the_existing_risk_graph(tmp_path, decision, risk):
    source, store = seed_audit(tmp_path, count=2, decision=decision, risk=risk)
    graph = R31ReportRuntime(store).generation_graph(source=source, task_id="new-search-task", checkpoint_path=tmp_path / "checkpoints.sqlite3")
    try:
        assert isinstance(graph, AccountOverviewReportGraph)
        assert not isinstance(graph, PassReportGraph)
    finally:
        graph.close()
