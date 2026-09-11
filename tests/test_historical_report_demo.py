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
                outputs_dir=root / "outputs",
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
                "outputs_dir": root / "outputs",
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


def test_historical_analysis_records_include_every_frozen_post_and_preserve_report(historical_stack):
    client = historical_stack['client']
    for spec, count in zip(HISTORICAL_REPORT_SPECS, (201, 304)):
        client.get('/api/historical-report-workspaces')
        before = historical_stack['report_store'].get_frontend_report(spec.report_version_id)
        response = client.get(f'/api/historical-report-workspaces/{spec.workspace_id}/analysis-records')
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload['record_count'] == count
        assert len({post['post_ref'] for post in payload['records']}) == count
        assert payload['excluded_posts'] == []
        assert '不表示恢复' in payload['notice']
        for post in (payload['records'][0], payload['records'][-1]):
            expected = historical_stack['report_store'].get_presentation_post_detail(spec.report_version_id, post_ref=post['post_ref'])
            assert post == expected
        assert historical_stack['report_store'].get_frontend_report(spec.report_version_id) == before
    assert client.get('/api/historical-report-workspaces/unknown/analysis-records').status_code == 404


def test_frozen_audit_detail_uses_full_saved_comments_and_evidence(historical_stack):
    client = historical_stack['client']
    client.get('/api/historical-report-workspaces')
    spec = HISTORICAL_REPORT_SPECS[0]
    records = client.get(f'/api/historical-report-workspaces/{spec.workspace_id}/analysis-records').json()['records']
    record = records[0]
    before = historical_stack['report_store'].get_frontend_report(spec.report_version_id)
    route = f"/api/report-versions/{spec.report_version_id}/posts/{record['post_ref']}/audit-detail"
    response = client.get(route)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail['audit_result']['job_id'] == spec.task_id
    assert str(detail['audit_result']['audit_result_id']) == record['audit_source']['output_id']
    snapshot = historical_stack['report_store'].load_immutable_snapshot(spec.report_version_id)
    original = next(post for post in snapshot.posts if post.payload['raw_content_payload'].get('note_id') == detail['audit_result'].get('note_id'))
    assert detail['audit_result']['comments'] == original.payload['raw_content_payload']['comments']
    assert detail['report_snapshot']['post_ref'] == record['post_ref']
    assert historical_stack['report_store'].get_frontend_report(spec.report_version_id) == before
    assert client.get(f'/api/report-versions/{spec.report_version_id}/posts/unknown/audit-detail').status_code == 404


def test_report_media_requires_a_saved_asset_of_the_selected_post(historical_stack):
    client = historical_stack["client"]
    spec = HISTORICAL_REPORT_SPECS[0]
    records = client.get(f"/api/historical-report-workspaces/{spec.workspace_id}/analysis-records").json()["records"]
    base = f"/api/report-versions/{spec.report_version_id}/posts/{records[0]['post_ref']}"
    item = client.get(base + "/audit-detail").json()["audit_result"]
    def paths(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "asset_rel" and isinstance(child, str):
                    yield child
                else:
                    yield from paths(child)
        elif isinstance(value, list):
            for child in value:
                yield from paths(child)
    asset = next(paths(item))
    destination = historical_stack["outputs_dir"] / item["job_id"] / asset
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"saved media fixture")
    assert client.get(base + "/assets", params={"path": asset}).content == b"saved media fixture"
    assert client.get(base + "/assets", params={"path": "../reports.sqlite3"}).status_code == 404
    unlisted = destination.parent / "unlisted.txt"
    unlisted.write_text("not in the published post")
    assert client.get(base + "/assets", params={"path": str(Path(asset).with_name('unlisted.txt'))}).status_code == 404


def test_old_absolute_media_uses_archived_copy_and_supports_video_ranges(historical_stack, monkeypatch):
    from backend.reporting.media import snapshot_asset_relative_path

    client = historical_stack["client"]
    spec = HISTORICAL_REPORT_SPECS[0]
    records = client.get(f"/api/historical-report-workspaces/{spec.workspace_id}/analysis-records").json()["records"]
    post_ref = records[0]["post_ref"]
    base = f"/api/report-versions/{spec.report_version_id}/posts/{post_ref}/assets"
    original = historical_stack["outputs_dir"].parent / "old-crawler" / "video.mp4"
    original.parent.mkdir()
    original.write_bytes(b"must not serve original filesystem file")
    store = historical_stack["report_store"]
    read_detail = store.get_snapshot_audit_detail

    def detail(version, *, post_ref):
        value = read_detail(version, post_ref=post_ref)
        if post_ref == records[0]["post_ref"]:
            value["audit_result"]["video_results"] = [{"local_path": str(original)}]
        return value

    monkeypatch.setattr(store, "get_snapshot_audit_detail", detail)
    # Existing old files alone must never grant filesystem access.
    assert client.get(base, params={"path": str(original)}).status_code == 404
    target = historical_stack["outputs_dir"] / spec.task_id / snapshot_asset_relative_path(str(original))
    target.parent.mkdir(parents=True)
    target.write_bytes(b"archived video bytes")
    response = client.get(base, params={"path": str(original)}, headers={"Range": "bytes=0-7"})
    assert response.status_code == 206
    assert response.content == b"archived"
    assert response.headers["content-range"] == "bytes 0-7/20"
    suffix = client.get(base, params={"path": str(original)}, headers={"Range": "bytes=-5"})
    assert suffix.status_code == 206 and suffix.content == b"bytes"
    beyond = client.get(base, params={"path": str(original)}, headers={"Range": "bytes=100-"})
    assert beyond.status_code == 416 and beyond.headers["content-range"] == "bytes */20"
    other = base.replace(post_ref, records[1]["post_ref"])
    assert client.get(other, params={"path": str(original)}).status_code == 404
    assert client.get(base, params={"path": str(target)}).status_code == 404


def test_new_report_version_keeps_prior_conversation_and_session_scope(historical_stack):
    service = historical_stack["service"]
    agent = historical_stack["agent_service"]
    principal = "historical-demo-test"
    original, replacement = HISTORICAL_REPORT_SPECS[:2]
    workspace = service.get_workspace(original.workspace_id, principal_id=principal)
    service.accept_turn(original.workspace_id, principal_id=principal,
                        client_message_id="version-old", content="旧版问题")
    old_anchor = service._anchor(original.workspace_id, principal)
    old_session = agent.store.find_session_by_anchor(old_anchor)
    updated = {**workspace, "report_version_id": replacement.report_version_id}
    new_anchor = service._current_anchor(updated, principal)
    assert new_anchor != old_anchor
    new_session = agent.create_session(replacement.report_version_id, anchor_key=new_anchor)
    service.executor.accept_turn(new_session.id, client_message_id="version-new", content="新版问题")
    view = service._workspace_state(updated, principal_id=principal)
    assert [m["content"] for m in view["conversation"] if m["role"] == "user"] == ["旧版问题", "新版问题"]
    assert agent.store.get_session(old_session.id).report_version_id == original.report_version_id
    assert view["latest_turn"].session_id == new_session.id
