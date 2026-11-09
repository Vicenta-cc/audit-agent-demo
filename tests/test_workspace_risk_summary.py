from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.api.historical_reports import _workspace_response
from backend.audit_agent.ingestion import AuditResultStore
from backend.historical_reports.service import HistoricalReportDemoService
from backend.investigation_creation.principal import Principal
from test_investigation_creation_conversation import creation_stack


def _state(session, *, job_id: str):
    return SimpleNamespace(
        run=SimpleNamespace(
            run_id=f"run:{job_id}",
            job_id=job_id,
            status="PUBLISHED",
            updated_at=session.updated_at,
        ),
        draft_artifact={},
        latest_report_turn=None,
        messages=(),
        report_messages=(),
    )


def _result(store: AuditResultStore, job_id: str, key: str, **values) -> None:
    store.upsert_result(
        job_id=job_id,
        platform="dy",
        content_key=key,
        result={"note_id": key, **values},
    )


def test_workspace_list_returns_compact_risk_summaries_and_isolates_errors(
    creation_stack, tmp_path, monkeypatch
):
    conversation = creation_stack["conversation"]
    principal = Principal("principal-a")
    sessions = {
        name: conversation.create_session(principal=principal)
        for name in ("risk", "review", "safe", "empty", "broken")
    }
    store = AuditResultStore(tmp_path / "risk-summary.sqlite3")
    conversation.audit_result_store = store

    _result(store, "job-risk", "reject", decision="reject", risk_level="high")
    _result(store, "job-risk", "review", decision="review", risk_level="unknown")
    _result(
        store,
        "job-review",
        "partial-pass",
        decision="pass",
        risk_level="none",
        audit_completion_status="partial",
        manual_review_required=True,
    )
    _result(store, "job-safe", "pass", decision="pass", risk_level="none")

    states = {
        sessions["risk"].id: _state(sessions["risk"], job_id="job-risk"),
        sessions["review"].id: _state(sessions["review"], job_id="job-review"),
        sessions["safe"].id: _state(sessions["safe"], job_id="job-safe"),
        sessions["empty"].id: _state(sessions["empty"], job_id="job-empty"),
    }

    def get_state(session_id, **_):
        if session_id == sessions["broken"].id:
            raise RuntimeError("broken projection")
        return states[session_id]

    monkeypatch.setattr(conversation, "get_workspace_state", get_state)
    response = creation_stack["client"].get("/api/investigation-workspaces")
    assert response.status_code == 200
    by_id = {
        item["workspace_session_id"]: item
        for item in response.json()["items"]
    }
    assert by_id[sessions["risk"].id]["risk_summary"] == {
        "status": "risk",
        "risk_count": 1,
        "review_count": 1,
    }
    assert by_id[sessions["review"].id]["risk_summary"] == {
        "status": "review",
        "risk_count": 0,
        "review_count": 1,
    }
    assert by_id[sessions["safe"].id]["risk_summary"] == {
        "status": "safe",
        "risk_count": 0,
        "review_count": 0,
    }
    assert by_id[sessions["empty"].id]["risk_summary"] == {
        "status": "empty",
        "risk_count": 0,
        "review_count": 0,
    }
    assert by_id[sessions["broken"].id]["read_error"] == "WORKSPACE_READ_FAILED"
    assert by_id[sessions["broken"].id]["risk_summary"] is None


def test_historical_workspace_summary_comes_from_frozen_findings():
    service = object.__new__(HistoricalReportDemoService)
    service.report_store = SimpleNamespace(
        load_immutable_snapshot=lambda _version: SimpleNamespace(
            findings=(
                SimpleNamespace(payload={
                    "decision": "reject",
                    "raw_effective_result": {
                        "audit_completion_status": "completed",
                        "manual_review_required": False,
                    },
                }),
                SimpleNamespace(payload={
                    "decision": "review",
                    "raw_effective_result": {
                        "audit_completion_status": "partial",
                        "manual_review_required": True,
                    },
                }),
                SimpleNamespace(payload={
                    "decision": "pass",
                    "raw_effective_result": {
                        "audit_completion_status": "partial",
                        "manual_review_required": True,
                    },
                }),
            )
        )
    )
    service.report_service = SimpleNamespace(
        store=SimpleNamespace(list_report_version_sessions=lambda _anchor: ())
    )

    state = service._workspace_state(
        {"id": "historical-a", "report_version_id": "frozen-version"},
        principal_id="principal-a",
    )
    expected = {
        "status": "risk",
        "risk_count": 1,
        "review_count": 2,
    }
    response = _workspace_response(
        service,
        {
            **state,
            "run_id": "historical-run-a",
            "title": "历史报告",
            "task_id": "historical-task-a",
            "draft": {
                "task_name": "历史报告",
                "subject": "测试主题",
                "platform": "抖音",
                "search_terms": (),
                "analysis_plan": "读取冻结报告",
                "analysis_description": "只读测试",
                "recall_lexicons": (),
            },
            "display_timeline": (),
        },
    )
    assert response.risk_summary.model_dump() == expected
    service.report_store.load_immutable_snapshot = lambda _: pytest.fail("version was read twice")
    assert service.risk_summary_for_version("frozen-version") == expected


def test_workspace_summary_counts_all_rows_and_handles_legacy_json(tmp_path):
    store = AuditResultStore(tmp_path / "summary.sqlite3")
    with store._connect() as conn:
        conn.executemany(
            """INSERT INTO audit_results (job_id, platform, content_key, decision,
                   result_json, created_at, updated_at, analyzed_at)
               VALUES ('large', 'dy', ?, ?, ?, '', '', '')""",
            [(str(i), "reject", "invalid legacy json") for i in range(1001)]
            + [("partial", "pass", '{"audit_completion_status":"partial"}'),
               ("manual", "pass", '{"manual_review_required":true}'),
               ("integer", "pass", '{"manual_review_required":1}')],
        )
    result = store.risk_summaries_for_jobs(("large", "absent"))
    assert result["large"] == {"status": "risk", "risk_count": 1001, "review_count": 2}
    assert result["absent"]["status"] == "empty"
