from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.historical_reports import create_historical_report_router
from backend.api.reporting import create_reporting_router
from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.historical_reports import (
    HISTORICAL_REPORT_SPECS,
    HistoricalReportDemoService,
    HistoricalReportWorkspaceStore,
)
from backend.investigation.report_query import ReportQueryFacade
from backend.investigation.store import InvestigationStore
from backend.investigation_creation.principal import LocalPrincipalProvider
from backend.reporting.store import ReportStore


class _CapturingHermesAgent:
    histories: list[list[dict[str, Any]]] = []

    def __init__(self, **_: Any) -> None:
        self._api_max_retries = 1

    def close(self) -> None:
        return None

    def run_conversation(
        self,
        message: str,
        *,
        conversation_history: list[dict[str, Any]] | None = None,
        task_id: str,
        **_: Any,
    ) -> dict[str, Any]:
        history = [dict(item) for item in conversation_history or []]
        self.histories.append(history)
        answer = f"已基于当前报告完成回答：{message}"
        messages = [
            *history,
            {"role": "user", "content": message},
            {"role": "assistant", "content": answer},
        ]
        return {
            "final_response": answer,
            "messages": messages,
            "api_calls": 0,
            "completed": True,
            "failed": False,
            "interrupted": False,
            "turn_exit_reason": f"deterministic:{task_id}",
        }


class _SynchronousExecutor:
    def __init__(self, service: HermesInvestigationAgentService) -> None:
        self.service = service
        self.execution_count = 0

    def accept_turn(
        self,
        session_id: str,
        *,
        client_message_id: str,
        content: str,
    ) -> Any:
        turn, replay = self.service.accept_message(
            session_id,
            client_message_id=client_message_id,
            content=content,
        )
        if not self.service.store.list_public_turn_events(turn.id):
            self.service.store.append_public_turn_event(turn.id, stage="accepted")
        if not replay:
            self.execution_count += 1
            result = self.service.execute_turn(turn.id)
            self.service.store.append_public_turn_event(
                turn.id,
                stage="completed",
                answer=result.answer,
            )
        return self.service.store.get_turn(turn.id)

    def resume_turn(self, turn_id: str) -> Any:
        return self.service.store.get_turn(turn_id)


@pytest.fixture()
def historical_stack():
    from scripts.demo import restore_seed
    restore_seed(HISTORICAL_REPORT_SPECS[0].source_database.parent.parent)
    missing = [item.source_database for item in HISTORICAL_REPORT_SPECS if not item.source_database.is_file()]
    assert not missing, f"Gate-frozen historical archives are missing: {missing}"
    _CapturingHermesAgent.histories = []
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        report_db = root / "reports.sqlite3"
        report_store = ReportStore(report_db)
        agent_service = HermesInvestigationAgentService(
            report_facade=ReportQueryFacade(report_db, query_service=object()),
            store=InvestigationStore(root / "investigation.sqlite3"),
            agent_factory=_CapturingHermesAgent,
            bind_runtime=False,
            authorized_report_version_ids=tuple(
                item.report_version_id for item in HISTORICAL_REPORT_SPECS
            ),
            authorized_context_anchor_prefixes=("historical-report:",),
        )
        executor = _SynchronousExecutor(agent_service)
        service = HistoricalReportDemoService(
            specs=HISTORICAL_REPORT_SPECS,
            workspace_store=HistoricalReportWorkspaceStore(root / "workspaces.sqlite3"),
            report_store=report_store,
            report_service=agent_service,
            executor=executor,
        )
        app = FastAPI()
        app.include_router(
            create_historical_report_router(
                service,
                principal_provider=LocalPrincipalProvider("historical-demo-test"),
            )
        )
        app.include_router(
            create_reporting_router(
                report_store,
                principal_provider=LocalPrincipalProvider("historical-demo-test"),
                historical_report_service=service,
            )
        )
        with TestClient(app) as client:
            yield {
                "client": client,
                "service": service,
                "report_store": report_store,
                "report_db": report_db,
                "agent_service": agent_service,
                "executor": executor,
            }
        agent_service.close()


def _session_count(stack: dict[str, Any]) -> int:
    with sqlite3.connect(stack["agent_service"].store.db_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM investigation_sessions").fetchone()[0])


def _assert_public_payload(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False)
    forbidden_fragments = (
        "report_session_id",
        "investigation-session:",
        "source_account_key",
        "source_namespace",
        "corpus_revision",
        "credential",
        "prompt_version",
    )
    assert not any(fragment in serialized for fragment in forbidden_fragments)


def test_registration_is_explicit_idempotent_and_has_no_collection_side_effects(
    historical_stack: dict[str, Any],
) -> None:
    client = historical_stack["client"]

    first = client.get("/api/historical-report-workspaces")
    second = client.get("/api/historical-report-workspaces")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert [item["workspace_id"] for item in first.json()["items"]] == [
        "historical-report-a",
        "historical-report-b",
    ]
    assert [item["run_id"] for item in first.json()["items"]] == [
        "historical-report-run-a",
        "historical-report-run-b",
    ]
    assert all(item["run_status"] == "PUBLISHED" for item in first.json()["items"])
    assert all(item["import_semantics"] == "historical" for item in first.json()["items"])
    assert all(len(item["display_timeline"]) == 5 for item in first.json()["items"])
    assert all(item["conversation"] == [] for item in first.json()["items"])
    assert _session_count(historical_stack) == 0
    _assert_public_payload(first.json())

    for spec in HISTORICAL_REPORT_SPECS:
        historical_stack["report_store"].get_presentation_projection(spec.report_version_id)
    assert _session_count(historical_stack) == 0

    with sqlite3.connect(historical_stack["report_db"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM historical_report_imports").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM report_generation_runs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM report_provider_exchanges").fetchone()[0] == 0
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert not any(
        token in name.lower()
        for name in names
        for token in ("crawler", "worker", "audit_pipeline", "report_graph")
    )


def test_direct_report_refresh_imports_without_creating_a_session(
    historical_stack: dict[str, Any],
) -> None:
    spec = HISTORICAL_REPORT_SPECS[0]
    response = historical_stack["client"].get(
        f"/api/report-versions/{spec.report_version_id}"
    )
    assert response.status_code == 200
    assert response.json()["report_version_id"] == spec.report_version_id
    assert _session_count(historical_stack) == 0


def test_cross_report_contexts_are_limited_to_historical_workspace_anchors(
    historical_stack: dict[str, Any],
) -> None:
    service = historical_stack["service"]
    agent_service = historical_stack["agent_service"]
    service.list_workspaces(principal_id="historical-demo-test")
    primary = HISTORICAL_REPORT_SPECS[0]

    unscoped = agent_service.create_session(primary.report_version_id)
    assert agent_service._authorized_report_contexts(unscoped) == ()

    historical = agent_service.create_session(
        primary.report_version_id,
        anchor_key=service._anchor(primary.workspace_id, "historical-demo-test"),
    )
    contexts = agent_service._authorized_report_contexts(historical)
    assert [item.report_version_id for item in contexts] == [
        HISTORICAL_REPORT_SPECS[1].report_version_id
    ]


def test_lazy_sessions_completed_history_idempotency_and_scope(
    historical_stack: dict[str, Any],
) -> None:
    client = historical_stack["client"]
    assert client.get("/api/historical-report-workspaces").status_code == 200
    assert _session_count(historical_stack) == 0

    first_question = "先用三句话总结这份报告的主要结论，并说明报告覆盖范围和研判边界。"
    first = client.post(
        "/api/historical-report-workspaces/historical-report-a/turns",
        json={"client_message_id": "client-a-1", "content": first_question},
    )
    assert first.status_code == 202
    first_turn_id = first.json()["turn_id"]
    assert set(first.json()) == {"turn_id", "status"}
    assert _session_count(historical_stack) == 1
    assert _CapturingHermesAgent.histories == [[]]

    replay = client.post(
        "/api/historical-report-workspaces/historical-report-a/turns",
        json={"client_message_id": "client-a-1", "content": first_question},
    )
    assert replay.status_code == 202
    assert replay.json()["turn_id"] == first_turn_id
    assert historical_stack["executor"].execution_count == 1

    conflict = client.post(
        "/api/historical-report-workspaces/historical-report-a/turns",
        json={"client_message_id": "client-a-1", "content": "改成另一问题"},
    )
    assert conflict.status_code == 409
    assert historical_stack["executor"].execution_count == 1

    second_question = "第一篇代表内容为什么支持这项发现？请展示直接研判依据。"
    second = client.post(
        "/api/historical-report-workspaces/historical-report-a/turns",
        json={"client_message_id": "client-a-2", "content": second_question},
    )
    assert second.status_code == 202
    assert len(_CapturingHermesAgent.histories) == 2
    assert _CapturingHermesAgent.histories[1] == [
        {"role": "user", "content": first_question},
        {
            "role": "assistant",
            "content": f"已基于当前报告完成回答：{first_question}",
        },
    ]

    workspace = client.get("/api/historical-report-workspaces/historical-report-a")
    assert workspace.status_code == 200
    assert [item["role"] for item in workspace.json()["conversation"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert all(
        item["message_id"] not in {message["id"] for message in workspace.json()["display_timeline"]}
        for item in workspace.json()["conversation"]
    )
    _assert_public_payload(workspace.json())

    b_turn = client.post(
        "/api/historical-report-workspaces/historical-report-b/turns",
        json={"client_message_id": "client-b-1", "content": "展开当前报告的风险评论。"},
    )
    assert b_turn.status_code == 202
    assert _session_count(historical_stack) == 2
    cross_scope = client.get(
        f"/api/historical-report-workspaces/historical-report-a/turns/{b_turn.json()['turn_id']}"
    )
    assert cross_scope.status_code == 404


def test_sse_replays_from_after_sequence_and_last_event_id(
    historical_stack: dict[str, Any],
) -> None:
    client = historical_stack["client"]
    turn = client.post(
        "/api/historical-report-workspaces/historical-report-a/turns",
        json={"client_message_id": "client-sse", "content": "这条依据能够证明什么？"},
    )
    assert turn.status_code == 202
    turn_id = turn.json()["turn_id"]
    events = historical_stack["agent_service"].store.list_public_turn_events(turn_id)
    assert [item["stage"] for item in events] == ["accepted", "completed"]

    path = f"/api/historical-report-workspaces/historical-report-a/turns/{turn_id}/events"
    by_sequence = client.get(path, params={"after_sequence": 1})
    assert by_sequence.status_code == 200
    assert '"stage":"completed"' in by_sequence.text
    assert '"stage":"accepted"' not in by_sequence.text
    assert "session_id" not in by_sequence.text

    by_last_event = client.get(path, headers={"Last-Event-ID": events[0]["event_id"]})
    assert by_last_event.status_code == 200
    assert '"stage":"completed"' in by_last_event.text
    assert '"stage":"accepted"' not in by_last_event.text


def test_frontend_pending_storage_has_the_only_allowed_fields() -> None:
    source = (
        Path(__file__).parents[1]
        / "Audit_assistant/src/features/investigation/historicalReportPending.ts"
    ).read_text(encoding="utf-8")
    assert "client_message_id" in source
    assert "turn_id" in source
    assert "after_sequence" in source
    for forbidden in (
        "report_session_id",
        "transcript",
        "receipt",
        "corpus_revision",
        "account_ref",
        "question:",
    ):
        assert forbidden not in source
