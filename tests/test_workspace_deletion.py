import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from test_investigation_creation_conversation import (
    creation_stack, _create_completed_turn, _publish_fake_run,
)
from test_historical_report_demo import historical_stack
from backend.historical_reports import HistoricalReportDemoService, HistoricalReportWorkspaceStore
from backend.investigation.deletion import WorkspaceDeletionService, create_workspace_deletion_router
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.principal import LocalPrincipalProvider
from backend.hermes_runtime.report_snapshot import published_report_snapshot


def publish(stack, item, *, idempotency_key):
    run = _publish_fake_run(stack, item, idempotency_key=idempotency_key)
    # The existing fake projector changes the API projection only; a real worker
    # persists its terminal status. Exercise deletion against those durable facts.
    with sqlite3.connect(stack["creation_store"].db_path) as db:
        db.execute("UPDATE investigation_runs SET status='PUBLISHED',report_version_id=? WHERE id=?",
                   (run["report_version_id"], run["run_id"]))
    return run


def install(stack, tmp_path):
    stack["report_service"].hermes_state_dir = tmp_path / "report-runtime"
    history = HistoricalReportDemoService(
        specs=(), workspace_store=HistoricalReportWorkspaceStore(tmp_path / "history.sqlite3"),
        report_store=stack["report_store"], report_service=stack["report_service"],
        executor=stack["report_executor"],
    )
    service = WorkspaceDeletionService(conversations=stack["conversation"],
        creation_store=stack["creation_store"], reports=stack["report_store"],
        historical=history, report_service=stack["report_service"])
    stack["client"].app.include_router(create_workspace_deletion_router(service, stack["principals"]))
    return service


def test_published_workspace_erases_report_history_and_preserves_other_workspace(creation_stack, tmp_path):
    stack = creation_stack
    service = install(stack, tmp_path)
    deleted = _create_completed_turn(stack, workspace_key="delete-me")
    run = publish(stack, deleted, idempotency_key="delete-published")
    other = _create_completed_turn(stack, workspace_key="keep-me")
    keep_run = publish(stack, other, idempotency_key="keep-published")
    deleted_session_id = stack["report_service"].create_session(
        run["report_version_id"]
    ).id
    kept_session_id = stack["report_service"].create_session(
        keep_run["report_version_id"]
    ).id
    with sqlite3.connect(stack["creation_store"].db_path) as db:
        db.execute(
            "UPDATE investigation_runs SET report_session_id=? WHERE id=?",
            (deleted_session_id, run["run_id"]),
        )
        db.execute(
            "UPDATE investigation_runs SET report_session_id=? WHERE id=?",
            (kept_session_id, keep_run["run_id"]),
        )

    closed = []
    deleted_agent = SimpleNamespace(close=lambda: closed.append(deleted_session_id))
    kept_agent = SimpleNamespace(close=lambda: closed.append(kept_session_id))
    stack["report_service"]._agents.update({
        deleted_session_id: deleted_agent,
        kept_session_id: kept_agent,
    })
    snapshot_root = stack["report_service"].hermes_state_dir
    deleted_snapshot, _ = published_report_snapshot(
        stack["resource_db"],
        ledger_path=snapshot_root / f"{deleted_session_id}.sqlite3",
        session_id=deleted_session_id,
        identities=((run["report_version_id"],),),
    )
    kept_snapshot, _ = published_report_snapshot(
        stack["resource_db"],
        ledger_path=snapshot_root / f"{kept_session_id}.sqlite3",
        session_id=kept_session_id,
        identities=((keep_run["report_version_id"],),),
    )
    report = stack["report_store"].get_full_version(keep_run["report_version_id"])
    stale_context = stack["report_service"].report_facade.get_published_report_context(run["report_version_id"])
    path = f"/api/investigation-workspaces/{deleted['workspace_id']}"
    assert stack["client"].delete(path).status_code == 204
    assert stack["client"].delete(path).status_code == 204
    assert stack["client"].get(path + "/state").status_code == 404
    assert stack["client"].get(f"/api/report-versions/{run['report_version_id']}").status_code == 404
    assert stack["client"].get(f"/api/investigation-runs/{run['run_id']}").status_code == 404
    assert stack["report_store"].get_full_version(keep_run["report_version_id"]) == report
    assert deleted_session_id in closed
    assert kept_session_id not in closed
    assert kept_session_id in stack["report_service"]._agents
    assert not deleted_snapshot.exists()
    assert kept_snapshot.exists()
    assert stack["client"].get(f"/api/investigation-workspaces/{other['workspace_id']}/state").status_code == 200
    for path in (stack["conversation"].store.db_path, stack["creation_store"].db_path, stack["resource_db"]):
        with sqlite3.connect(path) as db:
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
            dump = "\n".join(db.iterdump())
            assert deleted["workspace_id"] not in dump
            assert run["report_version_id"] not in "\n".join(line for line in dump.splitlines() if "deleted_report_versions" not in line)
    from backend.investigation.errors import ReportNotFoundError
    with pytest.raises(ReportNotFoundError):
        stack["conversation"].store.create_session(stale_context)
    with sqlite3.connect(stack["resource_db"]) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM report_versions WHERE id=?", (keep_run["report_version_id"],))


def test_delete_owner_check_pending_turn_and_queued_run(creation_stack, tmp_path):
    stack = creation_stack
    service = install(stack, tmp_path)
    item = _create_completed_turn(stack)
    path = f"/api/investigation-workspaces/{item['workspace_id']}"
    with pytest.raises(HTTPException) as exc:
        service.delete(item["workspace_id"], principal_id="someone-else")
    assert exc.value.status_code == 404
    with sqlite3.connect(stack["conversation"].store.db_path) as db:
        db.execute("UPDATE investigation_turns SET status='running' WHERE id=?", (item["turn_id"],))
    assert stack["client"].delete(path).status_code == 409
    with sqlite3.connect(stack["conversation"].store.db_path) as db:
        db.execute("UPDATE investigation_turns SET status='completed' WHERE id=?", (item["turn_id"],))
    artifact = item["terminal"]["artifact"]
    response = stack["client"].post(f"/api/investigation-drafts/{artifact['draft_id']}/confirm-and-queue",
        headers={"Idempotency-Key": "queued-delete"}, json={"expected_revision": artifact["draft_revision"], "confirmed": True})
    assert response.status_code == 202
    assert stack["client"].delete(path).status_code == 409
    assert stack["client"].get(path + "/state").status_code == 200


def test_cleanup_failure_rolls_back_rows_and_delete_guards(creation_stack, tmp_path, monkeypatch):
    stack = creation_stack
    service = install(stack, tmp_path)
    item = _create_completed_turn(stack)
    run = publish(stack, item, idempotency_key="rollback")
    original = stack["report_store"].get_full_version(run["report_version_id"])
    def fail(*args):
        raise OSError("simulated cleanup failure")
    monkeypatch.setattr(service, "_clear_runtime", fail)
    with pytest.raises(OSError):
        service.delete(item["workspace_id"], principal_id="principal-a")
    assert stack["report_store"].get_full_version(run["report_version_id"]) == original
    assert stack["client"].get(f"/api/investigation-workspaces/{item['workspace_id']}/state").status_code == 200
    with sqlite3.connect(stack["resource_db"]) as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM report_versions WHERE id=?", (run["report_version_id"],))


def test_historical_delete_survives_registration_restart(historical_stack, tmp_path):
    stack = historical_stack
    history = stack["service"]
    report_service = stack["agent_service"]
    report_service.hermes_state_dir = tmp_path / "hermes"
    service = WorkspaceDeletionService(conversations=report_service,
        creation_store=InvestigationCreationStore(tmp_path / "creation.sqlite3"),
        reports=stack["report_store"], historical=history, report_service=report_service)
    client = stack["client"]
    client.app.include_router(create_workspace_deletion_router(service, LocalPrincipalProvider("historical-demo-test")))
    items = client.get("/api/historical-report-workspaces").json()["items"]
    first, other = items[:2]
    # Materialize real conversation history before deleting it.
    response = client.post(f"/api/historical-report-workspaces/{first['workspace_id']}/turns",
        json={"client_message_id": "delete-test", "content": "报告结论是什么？"})
    assert response.status_code == 202
    before = stack["report_store"].get_full_version(other["report_version_id"])
    path = f"/api/historical-report-workspaces/{first['workspace_id']}"
    assert client.delete(path).status_code == 204
    history._registered_principals.clear()
    remaining = client.get("/api/historical-report-workspaces")
    assert remaining.status_code == 200
    assert first["workspace_id"] not in {item["workspace_id"] for item in remaining.json()["items"]}
    assert client.get(path).status_code == 404
    assert client.get(f"/api/report-versions/{first['report_version_id']}").status_code == 404
    assert stack["report_store"].get_full_version(other["report_version_id"]) == before
    assert client.delete(path).status_code == 204


def test_last_blank_workspace_can_be_deleted(creation_stack, tmp_path):
    stack = creation_stack
    install(stack, tmp_path)
    client = stack["client"]
    item = client.post("/api/investigation-workspaces", json={}).json()
    path = f"/api/investigation-workspaces/{item['workspace_session_id']}"
    assert client.delete(path).status_code == 204
    assert client.get("/api/investigation-workspaces").json()["items"] == []
    assert client.get(path).status_code == 404


def terminal_workspace(stack, status):
    from backend.audit_agent.job_store import JobStore
    from backend.audit_agent.ingestion import IngestionStore
    item = _create_completed_turn(stack)
    artifact = item["terminal"]["artifact"]
    response = stack["client"].post(
        f"/api/investigation-drafts/{artifact['draft_id']}/confirm-and-queue",
        headers={"Idempotency-Key": "terminal-delete"},
        json={"expected_revision": artifact["draft_revision"], "confirmed": True},
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    jobs = JobStore(stack["resource_db"])
    job = jobs.create(job_id="terminal-job", owner_user_id="principal-a", platform="dy", status="completed")
    ingestion = IngestionStore(stack["resource_db"])
    stack["app_service"].run_projector = SimpleNamespace(job_store=jobs, ingestion_store=ingestion)
    with sqlite3.connect(stack["creation_store"].db_path) as db:
        db.execute("UPDATE investigation_runs SET status=?,job_id=? WHERE id=?", (status, job["id"], run_id))
    return item, run_id, job, ingestion


@pytest.mark.parametrize("status", ["AUDIT_COMPLETED", "FAILED"])
def test_drained_task_without_report_can_delete(creation_stack, tmp_path, status):
    stack = creation_stack
    install(stack, tmp_path)
    item, _, _, _ = terminal_workspace(stack, status)
    assert stack["client"].delete(f"/api/investigation-workspaces/{item['workspace_id']}").status_code == 204


def test_paused_or_undrained_task_cannot_delete(creation_stack, tmp_path, monkeypatch):
    stack = creation_stack
    install(stack, tmp_path)
    item, run_id, _, ingestion = terminal_workspace(stack, "INTERRUPTED")
    path = f"/api/investigation-workspaces/{item['workspace_id']}"
    assert stack["client"].delete(path).status_code == 409
    with sqlite3.connect(stack["creation_store"].db_path) as db:
        db.execute("UPDATE investigation_runs SET status='AUDIT_COMPLETED' WHERE id=?", (run_id,))
    monkeypatch.setattr(ingestion, "stats_for_task", lambda _: {"queued_analysis_count": 1})
    assert stack["client"].delete(path).status_code == 409


def test_end_waits_for_fence_and_deletion_preserves_charge_ledger(creation_stack, tmp_path):
    from backend.task_admission.execution import execution_lock
    stack = creation_stack
    install(stack, tmp_path)
    item, run_id, job, _ = terminal_workspace(stack, "FAILED")
    creation = stack["creation_store"]
    admission = creation.admission
    row = admission.enqueue(owner="principal-a", task_id=run_id, kind="investigation", key="end-task",
                            payload={}, job_id=job["id"])
    path = f"/api/investigation-workspaces/{item['workspace_id']}"
    admission.cancel(run_id, "principal-a")
    assert stack["client"].delete(path).status_code == 409
    admission.settle(run_id, stopped=True, reason="cancelled")
    with execution_lock(creation.db_path, run_id):
        assert stack["client"].delete(path).status_code == 409
    assert stack["client"].delete(path).status_code == 204
    retained = admission.for_task(run_id)
    assert retained["id"] == row["id"]
    assert retained["charge_state"] == "CHARGED"
    assert admission.summary("principal-a")["used"] == 1
