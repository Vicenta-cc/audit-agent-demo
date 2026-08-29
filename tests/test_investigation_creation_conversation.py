from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.investigation_conversation import (
    create_investigation_conversation_router,
)
from backend.api.investigation_creation import create_investigation_creation_router
from backend.api.investigation_execution import InvestigationTurnExecutor
from backend.api.investigation import create_investigation_router
from backend.api.reporting import create_reporting_router
from backend.audit_agent.audit_policy_store import AuditPolicyStore
from backend.audit_agent.crawler_account_store import CrawlerAccountStore
from backend.audit_agent.job_store import JobStore
from backend.audit_agent.lexicon_store import LexiconStore
from backend.hermes_runtime.adapter import HermesRuntimeBinding
from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.investigation.contracts import InvestigationSession
from backend.investigation.report_query import ReportQueryFacade
from backend.investigation.store import InvestigationStore
from backend.investigation_creation.adapters import InvestigationConfigurationResolver
from backend.investigation_creation.contracts import (
    ConfirmAndQueueCommand,
    QueryInvestigationOptions,
    RunStatus,
)
from backend.investigation_creation.conversation import (
    FakeCreationHermesAgent,
    InvestigationCreationConversationService,
)
from backend.investigation_creation.fake_runtime import (
    FakeInvestigationRunProjector,
    FakePublishedReportHermesAgent,
)
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.resources import InvestigationResourceService
from backend.investigation_creation.service import InvestigationCreationService
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.tools import (
    HERMES_M3_TOOL_SCHEMAS,
    M3_TOOL_INPUTS,
    InvestigationCreationToolService,
    configure_hermes_investigation_creation_tools,
    dispatch_hermes_investigation_creation_tool,
)
from backend.rulesets.service import RuleSetService
from backend.rulesets.store import RuleSetStore
from backend.reporting.store import ReportStore
from backend.investigation.protocol import validate_hermes_transcript_messages
from hermes_m0.schemas import M2_ACCOUNT_ACTIVITY_TOOLS


class MutablePrincipalProvider:
    def __init__(self, principal: str = "principal-a") -> None:
        self.current = Principal(principal)

    def __call__(self) -> Principal:
        return self.current


class RecordingReportService(HermesInvestigationAgentService):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.calls: list[tuple[str, str]] = []

    def create_session(self, report_version_id: str, *, anchor_key: str = ""):
        self.calls.append((report_version_id, anchor_key))
        return super().create_session(report_version_id, anchor_key=anchor_key)


class RecordingReportExecutor(InvestigationTurnExecutor):
    def __init__(self, service) -> None:
        super().__init__(service, max_workers=1)
        self.calls: list[tuple[str, str, str]] = []

    def accept_turn(
        self,
        session_id: str,
        *,
        client_message_id: str,
        content: str,
    ):
        self.calls.append((session_id, client_message_id, content))
        return super().accept_turn(
            session_id,
            client_message_id=client_message_id,
            content=content,
        )


@pytest.fixture
def creation_stack(tmp_path: Path) -> dict:
    resource_db = tmp_path / "resources.sqlite3"
    principals = MutablePrincipalProvider()
    JobStore(resource_db)
    lexicons = LexiconStore(resource_db)
    policies = AuditPolicyStore(resource_db)
    rulesets = RuleSetService(RuleSetStore(resource_db))
    resolver = InvestigationConfigurationResolver(
        lexicon_store=lexicons,
        policy_store=policies,
        crawler_account_store=CrawlerAccountStore(resource_db),
        ruleset_service=rulesets,
        principal_provider=principals,
    )
    resources = InvestigationResourceService(
        lexicon_store=lexicons,
        policy_store=policies,
        ruleset_service=rulesets,
        configuration_resolver=resolver,
    )
    creation_store = InvestigationCreationStore(tmp_path / "creation.sqlite3")
    app_service = InvestigationCreationService(
        creation_store,
        configuration_resolver=resolver,
        resource_service=resources,
    )
    tool_service = InvestigationCreationToolService(app_service)
    conversation = InvestigationCreationConversationService(
        tool_service=tool_service,
        store=InvestigationStore(tmp_path / "turns.sqlite3"),
        fake_runtime=True,
        hermes_state_dir=tmp_path / "hermes",
    )
    executor = InvestigationTurnExecutor(conversation, max_workers=1)
    report_service = RecordingReportService(
        report_facade=ReportQueryFacade(resource_db),
        store=conversation.store,
        agent_factory=FakePublishedReportHermesAgent,
        bind_runtime=False,
    )
    report_executor = RecordingReportExecutor(report_service)
    report_store = ReportStore(resource_db)
    app = FastAPI()
    app.include_router(
        create_investigation_creation_router(
            app_service, principal_provider=principals
        )
    )
    app.include_router(
        create_investigation_router(
            report_service,
            report_executor,
            report_store=report_store,
            m3_run_store=creation_store,
        )
    )
    app.include_router(
        create_reporting_router(
            report_store,
            principal_provider=principals,
            m3_run_store=creation_store,
        )
    )
    app.include_router(
        create_investigation_conversation_router(
            conversation,
            executor,
            principal_provider=principals,
            report_service=report_service,
            report_executor=report_executor,
            report_store=report_store,
        )
    )
    client = TestClient(app)
    yield {
        "principals": principals,
        "resource_db": resource_db,
        "creation_store": creation_store,
        "app_service": app_service,
        "tool_service": tool_service,
        "conversation": conversation,
        "executor": executor,
        "report_service": report_service,
        "report_executor": report_executor,
        "report_store": report_store,
        "client": client,
    }
    client.close()
    executor.shutdown(wait=True)
    report_executor.shutdown(wait=True)
    report_service.close()
    conversation.close()


def _draft_count(store: InvestigationCreationStore) -> int:
    with sqlite3.connect(store.db_path) as connection:
        return int(
            connection.execute("SELECT COUNT(*) FROM investigation_drafts").fetchone()[0]
        )


def _run_count(store: InvestigationCreationStore) -> int:
    with sqlite3.connect(store.db_path) as connection:
        return int(
            connection.execute("SELECT COUNT(*) FROM investigation_runs").fetchone()[0]
        )


def _job_count(resource_db: Path) -> int:
    with sqlite3.connect(resource_db) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])


def _create_completed_turn(stack: dict, *, workspace_key: str = "workspace-1") -> dict:
    client = stack["client"]
    workspace = client.post(
        "/api/investigation-workspaces", json={"workspace_key": workspace_key}
    )
    assert workspace.status_code == 201
    workspace_id = workspace.json()["workspace_session_id"]
    accepted = client.post(
        f"/api/investigation-workspaces/{workspace_id}/turns",
        json={
            "client_message_id": "message-1",
            "content": "帮我调查世界杯期间的博彩引流",
        },
    )
    assert accepted.status_code == 202
    turn_id = accepted.json()["turn_id"]
    with client.stream(
        "GET", f"/api/investigation-workspace-turns/{turn_id}/events"
    ) as response:
        assert response.status_code == 200
        events = [line for line in response.iter_lines() if line.startswith("data: ")]
    assert events
    terminal = json.loads(events[-1][6:])
    assert terminal["stage"] == "completed"
    return {"workspace_id": workspace_id, "turn_id": turn_id, "terminal": terminal}


def _publish_fake_run(stack: dict, result: dict, *, idempotency_key: str) -> dict:
    artifact = result["terminal"]["artifact"]
    stack["app_service"].run_projector = FakeInvestigationRunProjector(
        report_store=ReportStore(stack["resource_db"]),
        report_agent_service=stack["report_service"],
        stage_seconds=0.05,
    )
    confirmed = stack["client"].post(
        f"/api/investigation-drafts/{artifact['draft_id']}/confirm-and-queue",
        headers={"Idempotency-Key": idempotency_key},
        json={
            "expected_revision": artifact["draft_revision"],
            "confirmed": True,
        },
    )
    assert confirmed.status_code == 202
    run_id = confirmed.json()["run_id"]
    with sqlite3.connect(stack["creation_store"].db_path) as connection:
        connection.execute(
            "UPDATE investigation_runs SET created_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", run_id),
        )
    projection = stack["client"].get(f"/api/investigation-runs/{run_id}")
    assert projection.status_code == 200
    assert projection.json()["status"] == "PUBLISHED"
    return projection.json()


def test_creation_session_has_no_report_anchor_and_report_contract_is_unchanged(
    creation_stack: dict,
) -> None:
    session = creation_stack["conversation"].create_session(
        principal=Principal("principal-a")
    )
    assert session.scope_type == "creation"
    assert not any(
        (
            session.task_id,
            session.report_id,
            session.report_version_id,
            session.source_snapshot_id,
            session.snapshot_hash,
        )
    )
    with pytest.raises(ValidationError):
        InvestigationSession(
            id="bad-report-session",
            scope_type="report",
            task_id="",
            report_id="",
            report_version_id="",
            source_snapshot_id="",
            snapshot_hash="",
            status="active",
            created_at="now",
            updated_at="now",
        )


def test_fake_hermes_turn_returns_verified_draft_artifact_without_starting_run(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(creation_stack)
    terminal = result["terminal"]
    artifact = terminal["artifact"]
    assert artifact["artifact_type"] == "investigation_draft"
    assert artifact["presentation_stage"] == "suggestion"
    assert artifact["draft_id"] == artifact["draft"]["id"]
    assert artifact["draft_revision"] == 1
    assert artifact["confirmation_preview"]["max_notes"] == 1
    suggestion = artifact["suggestion"]
    assert [item["id"] for item in suggestion["platform_options"]] == [
        "dy",
        "xhs",
        "ks",
    ]
    assert suggestion["selected_platform"] in {"dy", "xhs", "ks"}
    assert suggestion["search_terms"]
    assert suggestion["audit_policy"]["published_version"]
    assert suggestion["ruleset_revision"]["version"] >= 1
    assert suggestion["recall_lexicons"]
    transcript = creation_stack["conversation"].store.latest_completed_hermes_transcript(
        result["workspace_id"]
    )
    assert transcript is not None
    tool_names = [
        call["function"]["name"]
        for message in transcript
        for call in message.get("tool_calls", [])
    ]
    assert tool_names[:2] == [
        "query_investigation_options",
        "create_investigation_draft",
    ]
    assert artifact["draft_id"] not in terminal["answer"]
    assert _draft_count(creation_stack["creation_store"]) == 1
    assert _run_count(creation_stack["creation_store"]) == 0
    assert _job_count(creation_stack["resource_db"]) == 0
    serialized = json.dumps(terminal, ensure_ascii=False)
    for forbidden in (
        "report_session_id",
        "DASHSCOPE_API_KEY",
        "system_message",
        "hermes_transcript",
        "sqlite3",
    ):
        assert forbidden not in serialized


def test_turn_and_sse_replay_do_not_create_a_second_draft(creation_stack: dict) -> None:
    result = _create_completed_turn(creation_stack)
    workspace_id = result["workspace_id"]
    replay = creation_stack["client"].post(
        f"/api/investigation-workspaces/{workspace_id}/turns",
        json={
            "client_message_id": "message-1",
            "content": "帮我调查世界杯期间的博彩引流",
        },
    )
    assert replay.status_code == 202
    assert replay.json()["turn_id"] == result["turn_id"]
    assert _draft_count(creation_stack["creation_store"]) == 1
    response = creation_stack["client"].get(
        f"/api/investigation-workspace-turns/{result['turn_id']}/events",
        params={"after_sequence": 0},
    )
    assert response.status_code == 200
    assert '"stage":"completed"' in response.text


def test_fake_runtime_uses_mutation_receipt_when_same_turn_is_replayed(
    creation_stack: dict,
) -> None:
    conversation = creation_stack["conversation"]
    session = conversation.create_session(
        principal=Principal("principal-a"), workspace_key="fake-receipt-workspace"
    )
    agent = FakeCreationHermesAgent(
        session_id=session.id,
        tool_service=creation_stack["tool_service"],
        principal_resolver=conversation.principal_for_session,
    )
    first = agent.run_conversation(
        "帮我生成小红书世界杯博彩调查方案",
        task_id="fake-replayed-turn",
    )
    replay = agent.run_conversation(
        "帮我生成小红书世界杯博彩调查方案",
        task_id="fake-replayed-turn",
    )
    assert first["messages"][-2] == replay["messages"][-2]
    assert _draft_count(creation_stack["creation_store"]) == 1


def test_creation_sse_replays_interrupted_boundary_and_reaches_terminal(
    creation_stack: dict,
) -> None:
    conversation = creation_stack["conversation"]
    session = conversation.create_session(
        principal=Principal("principal-a"), workspace_key="resume-workspace"
    )
    turn, _ = conversation.accept_message(
        session.id,
        client_message_id="resume-message",
        content="帮我生成小红书世界杯博彩调查方案",
        principal=Principal("principal-a"),
    )
    conversation.store.append_public_turn_event(turn.id, stage="accepted")
    conversation.store.mark_interrupted(
        turn.id,
        error_code="provider_outcome_unknown",
        safe_message="结果未知，可以安全恢复。",
        retryable=True,
    )
    interrupted_event = conversation.store.append_public_turn_event(
        turn.id,
        stage="interrupted",
        safe_message="结果未知，可以安全恢复。",
        retryable=True,
    )
    restored = creation_stack["client"].get(
        f"/api/investigation-workspaces/{session.id}/state"
    )
    assert restored.status_code == 200
    assert restored.json()["latest_turn"]["turn_id"] == turn.id
    assert restored.json()["latest_turn"]["status"] == "interrupted"
    assert restored.json()["latest_turn"]["retryable"] is True

    replay = creation_stack["client"].get(
        f"/api/investigation-workspace-turns/{turn.id}/events"
    )
    assert replay.status_code == 200
    assert '"stage":"interrupted"' in replay.text

    resumed = creation_stack["client"].post(
        f"/api/investigation-workspace-turns/{turn.id}/resume"
    )
    assert resumed.status_code == 202
    with creation_stack["client"].stream(
        "GET",
        f"/api/investigation-workspace-turns/{turn.id}/events",
        params={"after_sequence": interrupted_event["sequence"]},
    ) as response:
        events = [line for line in response.iter_lines() if line.startswith("data: ")]
    assert events
    terminal = json.loads(events[-1][6:])
    assert terminal["stage"] == "completed"
    assert terminal["artifact"]["artifact_type"] == "investigation_draft"
    assert _draft_count(creation_stack["creation_store"]) == 1


def test_mutation_receipt_replays_success_and_unknown_result_fails_safe(
    creation_stack: dict,
) -> None:
    service = creation_stack["app_service"]
    tool_service = creation_stack["tool_service"]
    store = creation_stack["creation_store"]
    principal = Principal("principal-a")
    options = service.query_investigation_options(
        QueryInvestigationOptions(), principal=principal
    )
    policy = options.audit_policies[0]
    arguments = {
        "title": "receipt test",
        "objective": "prove mutation replay safety",
        "configuration": {
            "platform": "xhs",
            "investigation": {
                "mode": "search",
                "recall_plan": {
                    "strategy": "temporary_terms",
                    "terms": ["世界杯博彩"],
                    "source_lexicon_ids": [],
                },
            },
            "audit_policy": {
                "id": policy.id,
                "expected_published_version": policy.published_version,
                "expected_published_config_hash": policy.published_config_hash,
                "expected_ruleset_revision_id": policy.ruleset_revision_id,
                "expected_ruleset_version": policy.ruleset_version,
                "expected_ruleset_content_hash": policy.ruleset_content_hash,
            },
        },
    }
    configure_hermes_investigation_creation_tools(
        tool_service, principal_provider=lambda _session_id: principal
    )
    first = dispatch_hermes_investigation_creation_tool(
        "create_investigation_draft",
        arguments,
        session_id="session-a",
        turn_id="turn-a",
        tool_call_id="call-a",
    )
    replay = dispatch_hermes_investigation_creation_tool(
        "create_investigation_draft",
        arguments,
        session_id="session-a",
        turn_id="turn-a",
        tool_call_id="call-a",
    )
    assert json.loads(first) == json.loads(replay)
    assert _draft_count(store) == 1

    store.begin_tool_execution(
        session_id="session-a",
        turn_id="turn-unknown",
        tool_call_id="call-unknown",
        principal=principal.id,
        tool_name="create_investigation_draft",
        arguments=arguments,
        is_mutation=True,
    )
    unknown = dispatch_hermes_investigation_creation_tool(
        "create_investigation_draft",
        arguments,
        session_id="session-a",
        turn_id="turn-unknown",
        tool_call_id="call-unknown",
    )
    assert json.loads(unknown)["error"]["code"] == "MUTATION_RESULT_UNKNOWN"
    assert _draft_count(store) == 1


def test_real_hermes_0204_dispatch_supplies_stable_mutation_identity(
    creation_stack: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = creation_stack["app_service"]
    store = creation_stack["creation_store"]
    conversation = creation_stack["conversation"]
    principal = Principal("principal-a")
    workspace = conversation.create_session(
        principal=principal, workspace_key="real-hermes-dispatch"
    )
    options = service.query_investigation_options(
        QueryInvestigationOptions(), principal=principal
    )
    policy = options.audit_policies[0]
    create_arguments = {
        "title": "Hermes middleware identity",
        "objective": "prove real registry dispatch executes each mutation once",
        "configuration": {
            "platform": "xhs",
            "investigation": {
                "mode": "search",
                "recall_plan": {
                    "strategy": "temporary_terms",
                    "terms": ["世界杯博彩"],
                    "source_lexicon_ids": [],
                },
            },
            "audit_policy": {
                "id": policy.id,
                "expected_published_version": policy.published_version,
                "expected_published_config_hash": policy.published_config_hash,
                "expected_ruleset_revision_id": policy.ruleset_revision_id,
                "expected_ruleset_version": policy.ruleset_version,
                "expected_ruleset_content_hash": policy.ruleset_content_hash,
            },
        },
    }
    configure_hermes_investigation_creation_tools(
        creation_stack["tool_service"],
        principal_provider=conversation.principal_for_session,
    )
    hermes_home = tmp_path / "hermes-real-dispatch"
    for name in (
        "HERMES_HOME",
        "HERMES_ENABLE_PROJECT_PLUGINS",
        "HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE",
        "HERMES_INVESTIGATION_CREATION_MODE",
        "HERMES_INVESTIGATION_REAL_REPORT_MODE",
        "HERMES_INVESTIGATION_REPORT_TASK_MODE",
        "HERMES_INVESTIGATION_TASK_MODE",
    ):
        monkeypatch.setenv(name, "0")
    binding = HermesRuntimeBinding()
    binding.configure_product_home(hermes_home)
    definitions = binding.tool_definitions(
        enabled_toolsets=["investigation"], product_mode="creation"
    )
    assert tuple(
        definition["function"]["name"] for definition in definitions
    ) == tuple(sorted(schema["name"] for schema in HERMES_M3_TOOL_SCHEMAS))

    from model_tools import handle_function_call

    def dispatch(
        name: str,
        arguments: dict,
        *,
        turn_id: str,
        tool_call_id: str,
    ) -> dict:
        return json.loads(
            handle_function_call(
                name,
                arguments,
                task_id=f"task:{turn_id}",
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                session_id=workspace.id,
                enabled_toolsets=["investigation"],
            )
        )

    created = dispatch(
        "create_investigation_draft",
        create_arguments,
        turn_id="turn:create",
        tool_call_id="call:create",
    )
    replayed = dispatch(
        "create_investigation_draft",
        create_arguments,
        turn_id="turn:create",
        tool_call_id="call:create",
    )
    assert created == replayed
    assert created["status"] == "ok"
    assert "TOOL_EXECUTION_IDENTITY_REQUIRED" not in json.dumps(created)
    assert _draft_count(store) == 1

    draft = created["data"]
    update_arguments = {
        "draft_id": draft["draft"]["id"],
        "expected_revision": draft["draft"]["current_revision"],
        "title": "Hermes middleware identity updated",
    }
    updated = dispatch(
        "update_investigation_draft",
        update_arguments,
        turn_id="turn:update",
        tool_call_id="call:update",
    )
    updated_replay = dispatch(
        "update_investigation_draft",
        update_arguments,
        turn_id="turn:update",
        tool_call_id="call:update",
    )
    conflicting_update = dispatch(
        "update_investigation_draft",
        {**update_arguments, "title": "conflicting update"},
        turn_id="turn:update",
        tool_call_id="call:update",
    )
    assert updated["status"] == "ok"
    assert updated_replay == updated
    assert updated["data"]["draft"]["current_revision"] == 2
    assert conflicting_update["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert service.get_draft_view(
        draft["draft"]["id"], principal=principal
    ).draft.current_revision == 2

    confirmed = dispatch(
        "confirm_and_queue_investigation",
        {
            "draft_id": draft["draft"]["id"],
            "expected_revision": 2,
            "confirmed": True,
            "idempotency_key": "real-hermes-confirm",
        },
        turn_id="turn:confirm",
        tool_call_id="call:confirm",
    )
    confirmed_replay = dispatch(
        "confirm_and_queue_investigation",
        {
            "draft_id": draft["draft"]["id"],
            "expected_revision": 2,
            "confirmed": True,
            "idempotency_key": "real-hermes-confirm",
        },
        turn_id="turn:confirm",
        tool_call_id="call:confirm",
    )
    assert confirmed == confirmed_replay
    assert confirmed["status"] == "ok"
    assert _run_count(store) == 1

    unknown_arguments = dict(create_arguments)
    unknown_arguments["title"] = "unknown provider outcome"
    store.begin_tool_execution(
        session_id=workspace.id,
        turn_id="turn:unknown",
        tool_call_id="call:unknown",
        principal=principal.id,
        tool_name="create_investigation_draft",
        arguments=unknown_arguments,
        is_mutation=True,
    )
    unknown = dispatch(
        "create_investigation_draft",
        unknown_arguments,
        turn_id="turn:unknown",
        tool_call_id="call:unknown",
    )
    assert unknown["error"]["code"] == "MUTATION_RESULT_UNKNOWN"
    assert _draft_count(store) == 1


def test_real_hermes_registry_switches_creation_report_creation_without_tool_loss(
    tmp_path: Path,
) -> None:
    binding = HermesRuntimeBinding()
    home = tmp_path / "hermes-mode-switch"

    def names(mode: str) -> tuple[str, ...]:
        with binding.product_mode_execution(home, product_mode=mode):
            return tuple(
                definition["function"]["name"]
                for definition in binding.tool_definitions(
                    enabled_toolsets=["investigation"], product_mode=mode
                )
            )

    expected_creation = tuple(
        sorted(schema["name"] for schema in HERMES_M3_TOOL_SCHEMAS)
    )
    expected_report = tuple(
        sorted(schema["name"] for schema in M2_ACCOUNT_ACTIVITY_TOOLS)
    )
    assert names("creation") == expected_creation
    assert names("account-activity") == expected_report
    assert names("creation") == expected_creation


def test_principal_isolation_applies_to_workspace_turn_messages_and_status(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(creation_stack)
    creation_stack["principals"].current = Principal("principal-b")
    client = creation_stack["client"]
    assert client.get(
        f"/api/investigation-workspaces/{result['workspace_id']}"
    ).status_code == 404
    assert client.get(
        f"/api/investigation-workspaces/{result['workspace_id']}/messages"
    ).status_code == 404
    assert client.get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    ).status_code == 404
    assert client.get(
        f"/api/investigation-workspace-turns/{result['turn_id']}"
    ).status_code == 404


def test_workspace_state_restores_messages_current_draft_and_has_no_side_effects(
    creation_stack: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _create_completed_turn(creation_stack, workspace_key="state-restore")
    artifact = result["terminal"]["artifact"]
    updated = creation_stack["client"].patch(
        f"/api/investigation-drafts/{artifact['draft_id']}",
        json={
            "expected_revision": artifact["draft_revision"],
            "title": "刷新后应恢复当前 revision",
            "objective": artifact["draft"]["objective"],
            "configuration": artifact["draft"]["configuration"],
        },
    )
    assert updated.status_code == 200
    counts_before = (
        _draft_count(creation_stack["creation_store"]),
        _run_count(creation_stack["creation_store"]),
    )
    monkeypatch.setattr(
        creation_stack["conversation"].store,
        "list_messages",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("workspace state must use one conversation snapshot")
        ),
    )
    monkeypatch.setattr(
        creation_stack["conversation"].store,
        "list_turns",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("workspace state must use one conversation snapshot")
        ),
    )

    response = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    replay = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )

    assert response.status_code == 200
    assert replay.json() == response.json()
    state = response.json()
    assert state["workspace"]["workspace_session_id"] == result["workspace_id"]
    assert [message["role"] for message in state["messages"]] == [
        "user",
        "assistant",
    ]
    assert state["draft_artifact"]["draft_id"] == artifact["draft_id"]
    assert state["draft_artifact"]["draft_revision"] == 2
    assert state["draft_artifact"]["draft"]["title"] == "刷新后应恢复当前 revision"
    assert state["draft_artifact"]["confirmation_preview"]["draft_id"] == artifact["draft_id"]
    assert state["latest_turn"]["turn_id"] == result["turn_id"]
    assert state["latest_turn"]["status"] == "completed"
    assert state["run"] is None
    assert "report_session_id" not in response.text
    assert counts_before == (
        _draft_count(creation_stack["creation_store"]),
        _run_count(creation_stack["creation_store"]),
    )


def test_platform_edit_and_confirmation_preview_are_durable_without_run_or_job(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(
        creation_stack, workspace_key="durable-suggestion-flow"
    )
    client = creation_stack["client"]
    artifact = result["terminal"]["artifact"]
    assert artifact["suggestion"]["selected_platform"] == "dy"

    updated_configuration = json.loads(
        json.dumps(artifact["draft"]["configuration"])
    )
    updated_configuration["platform"] = "xhs"
    updated = client.patch(
        f"/api/investigation-drafts/{artifact['draft_id']}",
        json={
            "expected_revision": artifact["draft_revision"],
            "title": artifact["draft"]["title"],
            "objective": artifact["draft"]["objective"],
            "configuration": updated_configuration,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["current_revision"] == 2
    assert updated.json()["configuration"]["platform"] == "xhs"

    preview = client.get(
        f"/api/investigation-drafts/{artifact['draft_id']}/confirmation-preview"
    )
    assert preview.status_code == 200
    assert preview.json()["draft_revision"] == 2
    assert preview.json()["platform"] == "xhs"
    assert preview.json()["max_notes"] == 1

    suggestion_state = client.get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    assert suggestion_state.status_code == 200
    suggestion_artifact = suggestion_state.json()["draft_artifact"]
    assert suggestion_artifact["presentation_stage"] == "suggestion"
    assert suggestion_artifact["draft_revision"] == 2
    assert suggestion_artifact["suggestion"]["selected_platform"] == "xhs"
    assert _run_count(creation_stack["creation_store"]) == 0
    assert _job_count(creation_stack["resource_db"]) == 0

    generated = client.post(
        f"/api/investigation-workspaces/{result['workspace_id']}"
        "/confirmation-preview",
        json={
            "client_message_id": "generate-confirmation-preview-1",
            "draft_id": artifact["draft_id"],
            "expected_revision": 2,
        },
    )
    assert generated.status_code == 200
    assert generated.json()["artifact"]["presentation_stage"] == "confirmation"
    assert generated.json()["artifact"]["confirmation_preview"]["platform"] == "xhs"

    restored = client.get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    replay = client.get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    assert restored.status_code == 200
    assert replay.json() == restored.json()
    state = restored.json()
    assert state["draft_artifact"]["presentation_stage"] == "confirmation"
    assert state["draft_artifact"]["draft_revision"] == 2
    assert state["draft_artifact"]["suggestion"]["selected_platform"] == "xhs"
    assert [message["role"] for message in state["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert state["run"] is None
    assert _run_count(creation_stack["creation_store"]) == 0
    assert _job_count(creation_stack["resource_db"]) == 0

    creation_stack["principals"].current = Principal("principal-b")
    assert client.post(
        f"/api/investigation-workspaces/{result['workspace_id']}"
        "/confirmation-preview",
        json={
            "client_message_id": "cross-principal-preview",
            "draft_id": artifact["draft_id"],
            "expected_revision": 2,
        },
    ).status_code == 404


def test_real_hermes_meta_tool_results_verify_latest_draft_revision(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(
        creation_stack, workspace_key="real-meta-tool-result"
    )
    artifact = result["terminal"]["artifact"]
    configuration = json.loads(json.dumps(artifact["draft"]["configuration"]))
    configuration["platform"] = "xhs"
    updated = creation_stack["client"].patch(
        f"/api/investigation-drafts/{artifact['draft_id']}",
        json={
            "expected_revision": 1,
            "title": artifact["draft"]["title"],
            "objective": artifact["draft"]["objective"],
            "configuration": configuration,
        },
    )
    assert updated.status_code == 200

    transcript = creation_stack["conversation"].store.latest_completed_hermes_transcript(
        result["workspace_id"]
    )
    assert transcript is not None
    meta_transcript = json.loads(json.dumps(transcript))
    for message in meta_transcript:
        for call in message.get("tool_calls", []):
            call["function"]["name"] = "tool_call"
    view = creation_stack["app_service"].get_draft_view(
        artifact["draft_id"], principal=Principal("principal-a")
    )
    meta_transcript.extend([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "real-meta-update",
                "type": "function",
                "function": {"name": "tool_call", "arguments": {}},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "real-meta-update",
            "name": "update_investigation_draft",
            "content": json.dumps({
                "status": "ok",
                "data": view.model_dump(mode="json"),
            }),
        },
    ])

    verified = creation_stack["conversation"]._verified_artifact(
        meta_transcript, principal=Principal("principal-a")
    )
    assert verified["draft_revision"] == 2
    assert verified["suggestion"]["selected_platform"] == "xhs"


def test_workspace_state_recovers_public_draft_from_structured_hermes_transcript(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(
        creation_stack, workspace_key="structured-transcript-recovery"
    )
    with sqlite3.connect(creation_stack["conversation"].store.db_path) as connection:
        connection.execute(
            "UPDATE investigation_turns SET public_artifact_json = '{}' WHERE id = ?",
            (result["turn_id"],),
        )
    original = result["terminal"]["artifact"]
    configuration = json.loads(json.dumps(original["draft"]["configuration"]))
    configuration["platform"] = "xhs"
    updated = creation_stack["client"].patch(
        f"/api/investigation-drafts/{original['draft_id']}",
        json={
            "expected_revision": 1,
            "title": original["draft"]["title"],
            "objective": original["draft"]["objective"],
            "configuration": configuration,
        },
    )
    assert updated.status_code == 200

    restored = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    replay = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    assert restored.status_code == 200
    assert replay.json() == restored.json()
    artifact = restored.json()["draft_artifact"]
    assert artifact["artifact_type"] == "investigation_draft"
    assert artifact["presentation_stage"] == "suggestion"
    assert artifact["draft_revision"] == 2
    assert artifact["suggestion"]["selected_platform"] == "xhs"
    assert artifact["suggestion"]["platform_options"]
    assert restored.json()["run"] is None
    assert _run_count(creation_stack["creation_store"]) == 0
    assert _job_count(creation_stack["resource_db"]) == 0


def test_new_drafts_cannot_write_wb_but_options_only_offer_creation_platforms(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(creation_stack, workspace_key="reject-wb")
    artifact = result["terminal"]["artifact"]
    configuration = json.loads(json.dumps(artifact["draft"]["configuration"]))
    configuration["platform"] = "wb"

    rejected = creation_stack["client"].post(
        "/api/investigation-drafts",
        json={
            "title": "不得创建的微博 Draft",
            "objective": "验证历史平台不能用于新建",
            "configuration": configuration,
        },
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"]["code"] == "PLATFORM_MISMATCH"
    options = creation_stack["client"].get("/api/investigation-options")
    assert options.status_code == 200
    assert [item["id"] for item in options.json()["platforms"]] == [
        "dy",
        "xhs",
        "ks",
    ]
    assert _draft_count(creation_stack["creation_store"]) == 1
    assert _run_count(creation_stack["creation_store"]) == 0
    assert _job_count(creation_stack["resource_db"]) == 0


def test_workspace_state_restores_pending_turn_without_replaying_mutation(
    creation_stack: dict,
) -> None:
    conversation = creation_stack["conversation"]
    principal = Principal("principal-a")
    workspace = conversation.create_session(
        principal=principal, workspace_key="pending-state"
    )
    turn, replayed = conversation.accept_message(
        workspace.id,
        client_message_id="pending-message",
        content="先生成方案，不要开始采集",
        principal=principal,
    )
    assert replayed is False

    first = creation_stack["client"].get(
        f"/api/investigation-workspaces/{workspace.id}/state"
    )
    second = creation_stack["client"].get(
        f"/api/investigation-workspaces/{workspace.id}/state"
    )

    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["latest_turn"]["turn_id"] == turn.id
    assert first.json()["latest_turn"]["status"] == "running"
    assert first.json()["draft_artifact"] is None
    assert _draft_count(creation_stack["creation_store"]) == 0


def test_workspace_state_restores_confirmed_run_and_published_report_version(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(creation_stack, workspace_key="published-state")
    stack = creation_stack
    projection = _publish_fake_run(
        stack, result, idempotency_key="workspace-state-confirm"
    )
    run_id = projection["run_id"]

    response = stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )

    assert response.status_code == 200
    state = response.json()
    assert state["run"]["run_id"] == run_id
    assert state["run"]["status"] == "PUBLISHED"
    assert state["run"]["report_version_id"].startswith("report-version:")
    assert "report_session_id" not in response.text
    assert _run_count(stack["creation_store"]) == 1


def test_single_creation_turn_create_and_confirm_restores_workspace_run_and_report(
    creation_stack: dict,
) -> None:
    stack = creation_stack
    stack["app_service"].run_projector = FakeInvestigationRunProjector(
        report_store=stack["report_store"],
        report_agent_service=stack["report_service"],
        stage_seconds=0.05,
    )
    workspace = stack["client"].post(
        "/api/investigation-workspaces", json={"workspace_key": "single-turn-confirm"}
    ).json()
    workspace_id = workspace["workspace_session_id"]
    accepted = stack["client"].post(
        f"/api/investigation-workspaces/{workspace_id}/turns",
        json={
            "client_message_id": "single-turn-confirm-message",
            "content": "请生成调查方案，确认并开始调查",
        },
    )
    assert accepted.status_code == 202
    turn_id = accepted.json()["turn_id"]
    with stack["client"].stream(
        "GET", f"/api/investigation-workspace-turns/{turn_id}/events"
    ) as response:
        events = [line for line in response.iter_lines() if line.startswith("data: ")]
    terminal = json.loads(events[-1][6:])
    assert terminal["stage"] == "completed"
    assert terminal["artifact"]["artifact_type"] == "investigation_run"
    run_id = terminal["artifact"]["run_id"]
    with sqlite3.connect(stack["creation_store"].db_path) as connection:
        connection.execute(
            "UPDATE investigation_runs SET created_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+00:00", run_id),
        )

    state = stack["client"].get(
        f"/api/investigation-workspaces/{workspace_id}/state"
    )
    assert state.status_code == 200
    body = state.json()
    assert body["draft_artifact"]["draft_id"] == body["run"]["draft_id"]
    assert body["run"]["run_id"] == run_id
    assert body["run"]["status"] == "PUBLISHED"
    report = stack["client"].get(
        f"/api/investigation-workspaces/{workspace_id}/runs/{run_id}/published-report"
    )
    assert report.status_code == 200
    assert report.json()["report_version_id"] == body["run"]["report_version_id"]
    assert _draft_count(stack["creation_store"]) == 1
    assert _run_count(stack["creation_store"]) == 1


def test_creation_mode_has_exactly_six_tools() -> None:
    assert set(M3_TOOL_INPUTS) == {
        "query_investigation_options",
        "create_investigation_draft",
        "update_investigation_draft",
        "get_investigation_draft",
        "confirm_and_queue_investigation",
        "get_investigation_run",
    }
    parameters = [schema["parameters"] for schema in HERMES_M3_TOOL_SCHEMAS]
    assert '"wb"' not in json.dumps(parameters, sort_keys=True)


def test_published_report_handoff_reuses_internal_run_anchor_without_exposing_session(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(
        creation_stack, workspace_key="published-handoff"
    )
    projection = _publish_fake_run(
        creation_stack, result, idempotency_key="published-handoff-confirm"
    )
    run_id = projection["run_id"]
    report_version_id = projection["report_version_id"]
    report_session = creation_stack["conversation"].store.find_session_by_anchor(
        f"m3-run:{run_id}"
    )
    assert report_session is not None
    creation_stack["report_service"].calls.clear()

    response = creation_stack["client"].post(
        f"/api/investigation-workspaces/{result['workspace_id']}"
        f"/runs/{run_id}/report-turns",
        json={
            "client_message_id": "report-question-1",
            "content": "报告里有哪些主要风险？",
        },
    )

    assert response.status_code == 202
    turn_id = response.json()["turn_id"]
    assert response.json()["status"] == "running"
    assert "session" not in response.text
    assert creation_stack["report_service"].calls == [
        (report_version_id, f"m3-run:{run_id}")
    ]
    assert creation_stack["report_executor"].calls == [
        (
            report_session.id,
            "report-question-1",
            "报告里有哪些主要风险？",
        )
    ]
    with creation_stack["client"].stream(
        "GET",
        f"/api/investigation-workspaces/{result['workspace_id']}"
        f"/runs/{run_id}/report-turns/{turn_id}/events",
    ) as stream:
        assert stream.status_code == 200
        events = [line for line in stream.iter_lines() if line.startswith("data: ")]
    assert json.loads(events[-1][6:])["stage"] == "completed"

    status = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}"
        f"/runs/{run_id}/report-turns/{turn_id}"
    )
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert "session_id" not in status.text

    state = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    assert state.status_code == 200
    assert [item["role"] for item in state.json()["report_messages"]] == [
        "user",
        "assistant",
    ]
    assert state.json()["latest_report_turn"]["turn_id"] == turn_id
    assert "report_session_id" not in state.text

    assert creation_stack["client"].get(
        f"/api/investigation-turns/{turn_id}"
    ).status_code == 404
    assert creation_stack["client"].get(
        f"/api/investigation-sessions/{report_session.id}/messages"
    ).status_code == 404

    creation_stack["principals"].current = Principal("principal-b")
    assert creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}"
        f"/runs/{run_id}/report-turns/{turn_id}"
    ).status_code == 404


def test_m3_report_and_resource_routes_hide_cross_principal_resources(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(
        creation_stack, workspace_key="principal-report-boundary"
    )
    projection = _publish_fake_run(
        creation_stack, result, idempotency_key="principal-report-confirm"
    )
    artifact = result["terminal"]["artifact"]
    run_id = projection["run_id"]
    report_version_id = projection["report_version_id"]

    owner_report = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}"
        f"/runs/{run_id}/published-report"
    )
    assert owner_report.status_code == 200
    assert creation_stack["client"].get(
        f"/api/report-versions/{report_version_id}"
    ).status_code == 200
    assert creation_stack["client"].post(
        f"/api/report-versions/{report_version_id}/investigation-sessions"
    ).status_code == 404
    legacy_m3_session = creation_stack["report_service"].create_session(
        report_version_id
    )
    legacy_turn = creation_stack["report_executor"].accept_turn(
        legacy_m3_session.id,
        client_message_id="legacy-m3-report-turn",
        content="不应通过旧报告会话入口读取",
    )
    assert creation_stack["client"].get(
        f"/api/investigation-sessions/{legacy_m3_session.id}/messages"
    ).status_code == 404
    assert creation_stack["client"].post(
        f"/api/investigation-sessions/{legacy_m3_session.id}/turns",
        json={"client_message_id": "blocked", "content": "不应创建新 Turn"},
    ).status_code == 404
    assert creation_stack["client"].get(
        f"/api/investigation-turns/{legacy_turn.id}"
    ).status_code == 404

    creation_stack["principals"].current = Principal("principal-b")
    assert creation_stack["client"].get(
        f"/api/investigation-drafts/{artifact['draft_id']}"
    ).status_code == 404
    assert creation_stack["client"].get(
        f"/api/investigation-runs/{run_id}"
    ).status_code == 404
    assert creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}"
        f"/runs/{run_id}/published-report"
    ).status_code == 404
    assert creation_stack["client"].get(
        f"/api/report-versions/{report_version_id}"
    ).status_code == 404
    other_workspace = creation_stack["client"].post(
        "/api/investigation-workspaces", json={"workspace_key": "principal-b-workspace"}
    )
    assert other_workspace.status_code == 201
    other_workspace_id = other_workspace.json()["workspace_session_id"]
    assert creation_stack["client"].get(
        f"/api/investigation-workspaces/{other_workspace_id}"
        f"/runs/{run_id}/published-report"
    ).status_code == 404


def test_workspace_state_restores_pending_and_interrupted_report_turn(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(
        creation_stack, workspace_key="pending-report-turn"
    )
    projection = _publish_fake_run(
        creation_stack, result, idempotency_key="pending-report-confirm"
    )
    run_id = projection["run_id"]
    report_session = creation_stack["conversation"].store.find_session_by_anchor(
        f"m3-run:{run_id}"
    )
    assert report_session is not None
    turn, replayed = creation_stack["report_service"].accept_message(
        report_session.id,
        client_message_id="pending-report-message",
        content="报告中的主要结论是什么？",
    )
    assert replayed is False
    creation_stack["conversation"].store.append_public_turn_event(
        turn.id, stage="accepted"
    )

    pending = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    assert pending.status_code == 200
    assert pending.json()["latest_report_turn"]["turn_id"] == turn.id
    assert pending.json()["latest_report_turn"]["status"] == "running"
    assert [item["role"] for item in pending.json()["report_messages"]] == ["user"]
    assert "session_id" not in json.dumps(
        pending.json()["latest_report_turn"], ensure_ascii=False
    )

    creation_stack["conversation"].store.mark_interrupted(
        turn.id,
        error_code="process_restarted",
        safe_message="报告问答执行已中断，可以安全恢复。",
        retryable=True,
    )
    interrupted_event = creation_stack["conversation"].store.append_public_turn_event(
        turn.id,
        stage="interrupted",
        safe_message="报告问答执行已中断，可以安全恢复。",
        retryable=True,
    )
    interrupted = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    assert interrupted.status_code == 200
    assert interrupted.json()["latest_report_turn"]["status"] == "interrupted"
    assert interrupted.json()["latest_report_turn"]["retryable"] is True

    assert creation_stack["client"].post(
        f"/api/investigation-turns/{turn.id}/resume"
    ).status_code == 404
    resumed = creation_stack["client"].post(
        f"/api/investigation-workspaces/{result['workspace_id']}"
        f"/runs/{run_id}/report-turns/{turn.id}/resume"
    )
    assert resumed.status_code == 202
    assert resumed.json()["turn_id"] == turn.id
    assert "session" not in resumed.text
    with creation_stack["client"].stream(
        "GET",
        f"/api/investigation-workspaces/{result['workspace_id']}"
        f"/runs/{run_id}/report-turns/{turn.id}/events",
        params={"after_sequence": interrupted_event["sequence"]},
    ) as stream:
        events = [line for line in stream.iter_lines() if line.startswith("data: ")]
    assert json.loads(events[-1][6:])["stage"] == "completed"

    restored = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    assert [item["role"] for item in restored.json()["report_messages"]] == [
        "user",
        "assistant",
    ]
    assert restored.json()["latest_report_turn"]["status"] == "completed"
    assert "report_session_id" not in restored.text


def test_confirm_prepares_fake_report_after_confirmation_fence_without_lock_conflict(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(creation_stack)
    artifact = result["terminal"]["artifact"]
    stack = creation_stack
    stack["app_service"].run_projector = FakeInvestigationRunProjector(
        report_store=ReportStore(stack["resource_db"]),
        report_agent_service=stack["report_service"],
        stage_seconds=0.05,
    )
    command = ConfirmAndQueueCommand(
        draft_id=artifact["draft_id"],
        expected_revision=artifact["draft_revision"],
        confirmed=True,
        idempotency_key="confirm-lock-regression-1",
    )
    first = stack["app_service"].confirm_and_queue(
        command, principal=Principal("principal-a")
    )
    replay = stack["app_service"].confirm_and_queue(
        command, principal=Principal("principal-a")
    )
    assert first.id == replay.id
    assert first.status == RunStatus.QUEUED
    assert _run_count(stack["creation_store"]) == 1
    assert stack["report_service"].calls
    assert {call[1] for call in stack["report_service"].calls} == {
        f"m3-run:{first.id}"
    }


def test_fake_report_qa_is_valid_hermes_transcript_and_never_calls_provider() -> None:
    agent = FakePublishedReportHermesAgent()
    result = agent.run_conversation(
        "这份报告里有哪些风险结论？",
        task_id="fake-m3-run:test-run",
    )
    assert result["api_calls"] == 0
    assert result["completed"] is True
    assert "未启动 Worker" in result["final_response"]
    validate_hermes_transcript_messages(result["messages"])
    assert any(
        message.get("name") == "read_report_presentation"
        for message in result["messages"]
        if message.get("role") == "tool"
    )
