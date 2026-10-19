"""Published geographic reports must not enter collection-recovery validation."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.investigation_creation.adapters import InvestigationRunProjector
from backend.investigation_creation.principal import Principal
from test_investigation_creation_conversation import (
    _create_completed_turn,
    _publish_fake_run,
    creation_stack,
)


@pytest.mark.parametrize("job_status", ["completed", "crawl_paused"])
def test_published_geographic_workspace_and_report_handoff_skip_recovery(
    creation_stack: dict, monkeypatch: pytest.MonkeyPatch, job_status: str,
) -> None:
    stack = creation_stack
    client = stack["client"]
    result = _create_completed_turn(stack, workspace_key="published-geographic")
    published = _publish_fake_run(
        stack, result, idempotency_key="published-geographic-confirm",
    )
    run_id = published["run_id"]
    version_id = published["report_version_id"]
    job_id = "published-geographic-job"
    # The fake projector publishes a presentation, not persisted run lifecycle fields.
    # Set those fields before switching to the actual production projector.
    with sqlite3.connect(stack["creation_store"].db_path) as connection:
        connection.execute(
            "UPDATE investigation_runs SET status = ?, report_version_id = ?, job_id = ? WHERE id = ?",
            ("PUBLISHED", version_id, job_id, run_id),
        )
    job = {
        "id": job_id,
        "status": job_status,
        "run_crawler": True,
        "platform": "dy",
        "max_total_notes": 2,
        "geographic_plan": {"target": "克拉玛依市"},
        "control": {},
    }
    stats = {
        "ingested_count": 2,
        "completed_analysis_count": 2,
        "failed_analysis_count": 0,
        "pending_analysis_count": 0,
        "analyzing_count": 0,
    }
    projector = InvestigationRunProjector(
        job_store=SimpleNamespace(db_path=stack["resource_db"], get=lambda _: dict(job)),
        ingestion_store=SimpleNamespace(
            db_path=stack["resource_db"], stats_for_task=lambda _: dict(stats),
        ),
        audit_result_store=SimpleNamespace(list_results=lambda **_: {"items": []}),
        report_store=stack["report_store"],
        crawler_account_store=SimpleNamespace(),
    )
    stack["app_service"].run_projector = projector
    recovery_check = Mock(side_effect=AssertionError("published report loaded recovery snapshot"))
    monkeypatch.setattr(
        "backend.audit_agent.geographic_status.geographic_resume_blocker", recovery_check,
    )
    workspace = f"/api/investigation-workspaces/{result['workspace_id']}"
    run_path = f"{workspace}/runs/{run_id}"

    assert client.get(f"{workspace}/state").status_code == 200
    projection = client.get(f"/api/investigation-runs/{run_id}")
    assert projection.status_code == 200
    assert projection.json()["available_actions"]["resume_crawl"] is False
    assert projection.json()["report_status"] == "published"
    assert client.get(f"{run_path}/published-report").status_code == 200
    accepted = client.post(
        f"{run_path}/report-turns",
        json={"client_message_id": "published-question", "content": "报告里有哪些主要风险？"},
    )
    assert accepted.status_code == 202
    turn_id = accepted.json()["turn_id"]
    with client.stream("GET", f"{run_path}/report-turns/{turn_id}/events") as stream:
        assert stream.status_code == 200
        assert any('"completed"' in line for line in stream.iter_lines())

    # Knowing another workspace or report identifier does not grant access.
    other = client.post(
        "/api/investigation-workspaces", json={"workspace_key": "unrelated-workspace"},
    )
    assert other.status_code == 201
    other_path = (
        f"/api/investigation-workspaces/{other.json()['workspace_session_id']}/runs/{run_id}"
    )
    assert client.get(f"{other_path}/published-report").status_code == 404
    assert client.post(
        f"{other_path}/report-turns",
        json={"client_message_id": "wrong-workspace", "content": "读取报告"},
    ).status_code == 404
    stack["principals"].current = Principal("principal-b")
    assert client.get(f"{workspace}/state").status_code == 404
    assert client.get(f"{run_path}/published-report").status_code == 404
    assert client.post(
        f"{run_path}/report-turns",
        json={"client_message_id": "wrong-principal", "content": "读取报告"},
    ).status_code == 404
    recovery_check.assert_not_called()
