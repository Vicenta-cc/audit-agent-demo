from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

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
from backend.audit_agent.config import settings
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
    CREATION_SYSTEM_PROMPT,
    FakeCreationHermesAgent,
    InvestigationCreationConversationService,
)
from backend.investigation_creation.fake_runtime import (
    FakeInvestigationRunProjector,
    FakePublishedReportHermesAgent,
)
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.public_answer import (
    redact_creation_internal_references,
)
from backend.investigation_creation.resources import InvestigationResourceService
from backend.investigation_creation.service import InvestigationCreationService
from backend.investigation_creation.store import InvestigationCreationStore
from backend.investigation_creation.tools import (
    HERMES_M3_TOOL_SCHEMAS,
    M3_TOOL_DESCRIPTIONS,
    M3_TOOL_INPUTS,
    HermesToolExecutionIdentity,
    InvestigationCreationToolService,
    configure_hermes_investigation_creation_tools,
    dispatch_hermes_investigation_creation_tool,
)
from backend.rulesets.service import RuleSetService
from backend.rulesets.store import RuleSetStore
from backend.reporting.store import ReportStore
from backend.reporting.errors import ReportGenerationError
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


ScriptArguments = dict[str, Any] | Callable[[list[dict[str, Any]]], dict[str, Any]]


class ScriptedCreationHermesAgent:
    """Hermes-shaped test agent that runs declared actions through real M3 tools."""

    def __init__(
        self,
        *,
        session_id: str,
        tool_service: InvestigationCreationToolService,
        principal_resolver: Callable[[str], Principal],
        actions: list[tuple[str, ScriptArguments]],
        final_response: str,
    ) -> None:
        self.session_id = session_id
        self.tool_service = tool_service
        self.principal_resolver = principal_resolver
        self.actions = actions
        self.final_response = final_response
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results: list[dict[str, Any]] = []

    def run_conversation(
        self,
        message: str,
        *,
        conversation_history: list[dict[str, Any]] | None = None,
        task_id: str,
        **_: Any,
    ) -> dict[str, Any]:
        principal = self.principal_resolver(self.session_id)
        messages = [
            *(conversation_history or []),
            {"role": "user", "content": message},
        ]
        for index, (name, argument_source) in enumerate(self.actions, start=1):
            arguments = (
                argument_source(self.results)
                if callable(argument_source)
                else dict(argument_source)
            )
            call_id = f"{task_id}:scripted:{index}"
            envelope = self.tool_service.execute_with_identity(
                name,
                arguments,
                principal=principal,
                identity=HermesToolExecutionIdentity.require(
                    session_id=self.session_id,
                    turn_id=task_id,
                    tool_call_id=call_id,
                ),
            )
            assert envelope["status"] == "ok"
            self.calls.append((name, arguments))
            self.results.append(envelope["data"])
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": arguments,
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": json.dumps(
                            envelope, ensure_ascii=False, sort_keys=True
                        ),
                    },
                ]
            )
        messages.append({"role": "assistant", "content": self.final_response})
        return {
            "completed": True,
            "failed": False,
            "interrupted": False,
            "final_response": self.final_response,
            "messages": messages,
            "turn_exit_reason": "completed",
            "api_calls": 0,
        }


@pytest.fixture
def creation_stack(tmp_path: Path) -> dict:
    resource_db = tmp_path / "resources.sqlite3"
    principals = MutablePrincipalProvider()
    JobStore(resource_db)
    lexicons = LexiconStore(resource_db)
    policies = AuditPolicyStore(resource_db)
    rulesets = RuleSetService(RuleSetStore(resource_db))
    crawler_accounts = CrawlerAccountStore(resource_db)
    for platform in ("xhs", "dy", "ks"):
        account = crawler_accounts.create(
            platform=platform,
            display_name=f"{platform} fixture account",
        )
        crawler_accounts.save_auth_state(account["id"], "synthetic-fixture-ciphertext")
    resolver = InvestigationConfigurationResolver(
        lexicon_store=lexicons,
        policy_store=policies,
        crawler_account_store=crawler_accounts,
        ruleset_service=rulesets,
        principal_provider=principals,
    )
    resources = InvestigationResourceService(
        lexicon_store=lexicons,
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


def test_workspace_list_recovers_sessions_and_respects_owner_and_pagination(creation_stack):
    stack = creation_stack
    conversation = stack["conversation"]
    owner = Principal("principal-a")
    first = conversation.create_session(principal=owner)
    second = conversation.create_session(principal=owner)
    hidden = conversation.create_session(principal=Principal("principal-b"))
    closed = conversation.create_session(principal=owner)
    conversation.store.close_session(closed.id)
    client = stack["client"]
    page = client.get("/api/investigation-workspaces", params={"limit": 1}).json()
    assert page["has_more"] is True
    next_page = client.get("/api/investigation-workspaces", params={"limit": 1, "offset": 1}).json()
    assert next_page["has_more"] is False
    ids = {item["workspace_session_id"] for item in page["items"] + next_page["items"]}
    assert ids == {first.id, second.id}
    assert hidden.id not in ids and closed.id not in ids
    assert all(item["title"] == "新调查需求" for item in page["items"])
    stack["principals"].current = Principal("principal-b")
    other = client.get("/api/investigation-workspaces").json()
    assert [item["workspace_session_id"] for item in other["items"]] == [hidden.id]
    assert _draft_count(stack["creation_store"]) == 0


def _run_count(store: InvestigationCreationStore) -> int:
    with sqlite3.connect(store.db_path) as connection:
        return int(
            connection.execute("SELECT COUNT(*) FROM investigation_runs").fetchone()[0]
        )


def _mutation_receipt_count(store: InvestigationCreationStore) -> int:
    with sqlite3.connect(store.db_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM investigation_creation_tool_receipts "
                "WHERE is_mutation = 1"
            ).fetchone()[0]
        )


def _job_count(resource_db: Path) -> int:
    with sqlite3.connect(resource_db) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])


def _report_version_count(resource_db: Path) -> int:
    with sqlite3.connect(resource_db) as connection:
        return int(
            connection.execute("SELECT COUNT(*) FROM report_versions").fetchone()[0]
        )


def _run_scripted_creation_turn(
    stack: dict,
    *,
    content: str,
    actions: list[tuple[str, ScriptArguments]],
    final_response: str = "已根据当前系统资源回答。",
    session_id: str = "",
    client_message_id: str = "scripted-message",
) -> dict[str, Any]:
    conversation = stack["conversation"]
    principal = Principal("principal-a")
    if session_id:
        session = conversation.get_session(session_id, principal=principal)
    else:
        session = conversation.create_session(
            principal=principal,
            workspace_key=f"scripted:{client_message_id}",
        )
    agent = ScriptedCreationHermesAgent(
        session_id=session.id,
        tool_service=stack["tool_service"],
        principal_resolver=conversation.principal_for_session,
        actions=actions,
        final_response=final_response,
    )
    conversation._agents[session.id] = agent
    turn, idempotent_replay = conversation.accept_message(
        session.id,
        client_message_id=client_message_id,
        content=content,
        principal=principal,
    )
    assert idempotent_replay is False
    result = conversation.execute_turn(turn.id)
    return {
        "session_id": session.id,
        "turn": conversation.store.get_turn(turn.id),
        "result": result,
        "agent": agent,
    }


def _requested_lexicon_terms(results: list[dict[str, Any]]) -> dict[str, Any]:
    lexicon = next(
        item for item in results[0]["recall_lexicons"] if item["available"]
    )
    return {
        "domain_hint": "博彩",
        "mode": "search",
        "lexicon_ids": [lexicon["id"]],
        "include_lexicon_terms_for_ids": [lexicon["id"]],
        "page_size": 20,
        "lexicon_term_limit": 100,
    }


def _requested_ruleset_details(results: list[dict[str, Any]]) -> dict[str, Any]:
    revision = results[0]["ruleset_revisions"][0]
    return {
        "domain_hint": "赌博博彩",
        "mode": "search",
        "include_ruleset_details_for_revision_ids": [revision["id"]],
        "page_size": 20,
    }


def _search_draft_from_options(results: list[dict[str, Any]]) -> dict[str, Any]:
    options = results[-1]
    platform = next(
        (item for item in options["platforms"] if item["id"] == "xhs"),
        options["platforms"][0],
    )
    ruleset = options["ruleset_revisions"][0]
    lexicon = next(
        item
        for item in options["recall_lexicons"]
        if item["available"] and item["terms_included"]
    )
    return {
        "title": "世界杯博彩风险调查",
        "objective": "调查世界杯博彩风险",
        "configuration": {
            "schema_version": "investigation-draft-config-v4",
            "platform": platform["id"],
            "investigation": {
                "mode": "search",
                "recall_plan": {
                    "strategy": "existing_lexicon",
                    "lexicon_id": lexicon["id"],
                    "expected_runtime_content_hash": lexicon[
                        "runtime_content_hash"
                    ],
                    "enabled_main_terms": lexicon["enabled_main_terms"],
                },
            },
            "judgement": {
                "strategy": "existing_ruleset",
                "ruleset_revision_id": ruleset["id"],
                "expected_ruleset_version": ruleset["version"],
                "expected_ruleset_content_hash": ruleset["content_hash"],
            },
        },
    }


def _explicit_create_actions() -> list[tuple[str, ScriptArguments]]:
    return [
        (
            "query_investigation_options",
            {"domain_hint": "博彩", "mode": "search", "page_size": 20},
        ),
        ("query_investigation_options", _requested_lexicon_terms),
        ("create_investigation_draft", _search_draft_from_options),
    ]


def _create_completed_turn(
    stack: dict,
    *,
    workspace_key: str = "workspace-1",
    content: str = "帮我调查世界杯期间的博彩引流",
) -> dict:
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
            "content": content,
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


def test_failed_creation_requirements_survive_followup_without_failed_claims(creation_stack: dict):
    conversation = creation_stack['conversation']
    principal = Principal('principal-a')
    session = conversation.create_session(principal=principal)
    turn, _ = conversation.accept_message(
        session.id, client_message_id='failed-resource-edit', principal=principal,
        content='只临时使用这份词库，不保存；正常宗教和饮食描述不应判为风险',
    )
    conversation.store.fail_turn(turn.id, error_code='hermes_execution_failed',
                                 safe_message='未核实草稿：已覆盖正式词库', retryable=False)
    result = _run_scripted_creation_turn(
        creation_stack, session_id=session.id, content='继续，保留我刚才的要求',
        actions=[], client_message_id='recover-resource-edit', final_response='继续读取当前配置。',
    )
    transcript = conversation.store.latest_completed_hermes_transcript(session.id)
    assert [m['content'] for m in transcript if m['role'] == 'user'] == [
        '只临时使用这份词库，不保存；正常宗教和饮食描述不应判为风险', '继续，保留我刚才的要求',
    ]
    assert '已覆盖正式词库' not in json.dumps(transcript, ensure_ascii=False)
    assert result['turn'].status == 'completed'
    assert _draft_count(creation_stack['creation_store']) == 0


def test_fake_hermes_turn_returns_verified_draft_artifact_without_starting_run(
    creation_stack: dict,
) -> None:
    with (
        patch("subprocess.run") as subprocess_run,
        patch(
            "backend.audit_agent.crawler_adapter.MediaCrawlerAdapter.run_search"
        ) as crawler_run,
    ):
        result = _create_completed_turn(creation_stack)
    subprocess_run.assert_not_called()
    crawler_run.assert_not_called()
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
    assert tool_names[:3] == [
        "query_investigation_options",
        "query_investigation_options",
        "create_investigation_draft",
    ]
    assert artifact["draft_id"] not in terminal["answer"]
    assert _draft_count(creation_stack["creation_store"]) == 1
    assert _run_count(creation_stack["creation_store"]) == 0
    assert _job_count(creation_stack["resource_db"]) == 0
    assert _report_version_count(creation_stack["resource_db"]) == 0
    assert creation_stack["report_service"].calls == []
    assert creation_stack["report_executor"].calls == []
    serialized = json.dumps(terminal, ensure_ascii=False)
    for forbidden in (
        "crawler_account",
        "fixture account",
        "report_session_id",
        "DASHSCOPE_API_KEY",
        "system_message",
        "hermes_transcript",
        "sqlite3",
    ):
        assert forbidden not in serialized
    serialized_transcript = json.dumps(transcript, ensure_ascii=False)
    assert "crawler_account" not in serialized_transcript
    assert "fixture account" not in serialized_transcript


def test_fake_creator_homepage_turn_uses_creator_draft_without_recall_or_run(
    creation_stack: dict,
) -> None:
    creator_url = "https://www.douyin.com/user/MS4wLjABAAAA-valid"
    result = _create_completed_turn(
        creation_stack,
        workspace_key="creator-workspace",
        content=f"调查这个博主主页 {creator_url}",
    )
    artifact = result["terminal"]["artifact"]
    assert artifact["draft"]["configuration"]["investigation"] == {
        "mode": "creator",
        "creator_url": creator_url,
    }
    preview = artifact["confirmation_preview"]
    assert preview["mode"] == "creator"
    assert preview["creator_url"] == creator_url
    assert preview["resolved_search_terms"] == []
    assert preview["recall_plan"]["strategy"] == "none"
    assert artifact["suggestion"]["mode"] == "creator"
    assert artifact["suggestion"]["creator_url"] == creator_url
    assert artifact["suggestion"]["recall_lexicons"] == []
    assert _run_count(creation_stack["creation_store"]) == 0
    assert _job_count(creation_stack["resource_db"]) == 0
    assert _report_version_count(creation_stack["resource_db"]) == 0
    assert creation_stack["report_service"].calls == []
    assert creation_stack["report_executor"].calls == []


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
    assert "crawler_account" not in response.text
    assert "fixture account" not in response.text


def test_fake_runtime_activity_stream_preserves_turn_replay_idempotency(
    creation_stack: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "activity_stream_enabled", True)
    result = _create_completed_turn(
        creation_stack,
        workspace_key="activity-stream-replay",
    )
    events = creation_stack["conversation"].store.list_public_turn_events(
        result["turn_id"]
    )
    activities = [event for event in events if event["event_type"] == "activity"]

    assert [(event["label"], event["status"]) for event in activities] == [
        ("查询可用平台、审核规则和黑话库", "running"),
        ("查询可用平台、审核规则和黑话库", "succeeded"),
        ("查询可用平台、审核规则和黑话库", "running"),
        ("查询可用平台、审核规则和黑话库", "succeeded"),
        ("创建任务配置草案", "running"),
        ("创建任务配置草案", "succeeded"),
    ]
    assert _draft_count(creation_stack["creation_store"]) == 1
    assert _run_count(creation_stack["creation_store"]) == 0

    recovered = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    assert recovered.status_code == 200
    recovered_activities = recovered.json()["activity_events"]
    assert [event["status"] for event in recovered_activities] == [
        "running",
        "succeeded",
        "running",
        "succeeded",
        "running",
        "succeeded",
    ]
    assert {event["turn_id"] for event in recovered_activities} == {
        result["turn_id"]
    }
    assert all(
        set(event) == {
            "event_id",
            "turn_id",
            "sequence",
            "occurred_at",
            "activity_id",
            "status",
            "label",
            "summary",
            "result_count",
        }
        for event in recovered_activities
    )
    monkeypatch.setattr(settings, "activity_stream_enabled", False)
    disabled_recovery = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    assert disabled_recovery.status_code == 200
    assert disabled_recovery.json()["activity_events"] == []
    monkeypatch.setattr(settings, "activity_stream_enabled", True)

    replay = creation_stack["client"].post(
        f"/api/investigation-workspaces/{result['workspace_id']}/turns",
        json={
            "client_message_id": "message-1",
            "content": "帮我调查世界杯期间的博彩引流",
        },
    )
    assert replay.status_code == 202
    assert replay.json()["turn_id"] == result["turn_id"]
    replayed_events = creation_stack["conversation"].store.list_public_turn_events(
        result["turn_id"]
    )
    assert replayed_events == events
    assert _draft_count(creation_stack["creation_store"]) == 1
    assert _run_count(creation_stack["creation_store"]) == 0


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
    ruleset = options.ruleset_revisions[0]
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
            "judgement": {
                "strategy": "existing_ruleset",
                "ruleset_revision_id": ruleset.id,
                "expected_ruleset_version": ruleset.version,
                "expected_ruleset_content_hash": ruleset.content_hash,
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


def test_invalid_create_does_not_reserve_receipt_and_corrected_same_turn_succeeds(
    creation_stack: dict,
) -> None:
    tool_service = creation_stack["tool_service"]
    store = creation_stack["creation_store"]
    principal = Principal("principal-a")
    session_id = "session:invalid-then-valid"
    turn_id = "turn:invalid-then-valid"
    invalid_arguments = {
        "title": "世界杯博彩风险调查",
        "objective": "调查世界杯博彩风险",
        "configuration": {
            "platform": "xhs",
            "crawl_mode": "search",
        },
    }

    with patch.object(creation_stack["app_service"], "create_draft") as create_draft:
        invalid = tool_service.execute_with_identity(
            "create_investigation_draft",
            invalid_arguments,
            principal=principal,
            identity=HermesToolExecutionIdentity.require(
                session_id=session_id,
                turn_id=turn_id,
                tool_call_id="call:invalid-create",
            ),
        )
    create_draft.assert_not_called()

    assert invalid["status"] == "error"
    assert invalid["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
    details = invalid["error"]["details"]
    assert details["receipt_created"] is False
    assert details["mutation_applied"] is False
    assert details["retryable"] is True
    assert details["draft_created"] is False
    assert details["validation_errors"]
    assert set(details["validation_errors"][0]) == {"loc", "type", "message"}
    assert details["recovery"] == (
        "Draft was not created. Correct the arguments using the Tool schema and retry "
        "create_investigation_draft."
    )
    assert _draft_count(store) == 0
    assert _mutation_receipt_count(store) == 0

    options = tool_service.execute(
        "query_investigation_options",
        {
            "domain_hint": "博彩",
            "mode": "search",
            "include_lexicon_terms_for_ids": ["gambling"],
        },
        principal=principal,
    )
    valid_arguments = _search_draft_from_options([options])
    valid_identity = HermesToolExecutionIdentity.require(
        session_id=session_id,
        turn_id=turn_id,
        tool_call_id="call:valid-create",
    )
    created = tool_service.execute_with_identity(
        "create_investigation_draft",
        valid_arguments,
        principal=principal,
        identity=valid_identity,
    )

    assert created["status"] == "ok"
    assert _draft_count(store) == 1
    assert _mutation_receipt_count(store) == 1

    replayed = tool_service.execute_with_identity(
        "create_investigation_draft",
        valid_arguments,
        principal=principal,
        identity=valid_identity,
    )
    assert replayed == created
    assert _draft_count(store) == 1
    assert _mutation_receipt_count(store) == 1

    conflicting_arguments = json.loads(json.dumps(valid_arguments))
    conflicting_arguments["title"] = "同一 Turn 的冲突创建"
    conflict = tool_service.execute_with_identity(
        "create_investigation_draft",
        conflicting_arguments,
        principal=principal,
        identity=HermesToolExecutionIdentity.require(
            session_id=session_id,
            turn_id=turn_id,
            tool_call_id="call:conflicting-create",
        ),
    )
    assert conflict["status"] == "error"
    assert conflict["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert _draft_count(store) == 1
    assert _mutation_receipt_count(store) == 1


@pytest.mark.parametrize(
    ("tool_name", "arguments", "application_method"),
    [
        (
            "update_investigation_draft",
            {"draft_id": "investigation-draft:missing", "expected_revision": 1},
            "update_draft",
        ),
        (
            "confirm_and_queue_investigation",
            {
                "draft_id": "investigation-draft:missing",
                "expected_revision": 1,
                "idempotency_key": "invalid-confirm",
            },
            "confirm_and_queue",
        ),
    ],
)
def test_invalid_shared_mutation_inputs_do_not_reserve_receipts(
    creation_stack: dict,
    tool_name: str,
    arguments: dict[str, Any],
    application_method: str,
) -> None:
    store = creation_stack["creation_store"]

    with patch.object(
        creation_stack["app_service"], application_method
    ) as application_call:
        result = creation_stack["tool_service"].execute_with_identity(
            tool_name,
            arguments,
            principal=Principal("principal-a"),
            identity=HermesToolExecutionIdentity.require(
                session_id="session:invalid-shared-mutation",
                turn_id=f"turn:{tool_name}",
                tool_call_id=f"call:{tool_name}",
            ),
        )
    application_call.assert_not_called()

    assert result["status"] == "error"
    assert result["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
    assert result["error"]["details"]["receipt_created"] is False
    assert result["error"]["details"]["mutation_applied"] is False
    assert result["error"]["details"]["retryable"] is True
    assert "draft_created" not in result["error"]["details"]
    assert _mutation_receipt_count(store) == 0


def test_schema_valid_resource_failure_remains_inside_receipt_fence(
    creation_stack: dict,
) -> None:
    tool_service = creation_stack["tool_service"]
    store = creation_stack["creation_store"]
    principal = Principal("principal-a")
    options = tool_service.execute(
        "query_investigation_options",
        {
            "domain_hint": "博彩",
            "mode": "search",
            "include_lexicon_terms_for_ids": ["gambling"],
        },
        principal=principal,
    )
    arguments = _search_draft_from_options([options])
    arguments["configuration"]["investigation"]["recall_plan"][
        "expected_runtime_content_hash"
    ] = "0" * 64

    result = tool_service.execute_with_identity(
        "create_investigation_draft",
        arguments,
        principal=principal,
        identity=HermesToolExecutionIdentity.require(
            session_id="session:resource-failure",
            turn_id="turn:resource-failure",
            tool_call_id="call:resource-failure",
        ),
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "RESOURCE_STALE"
    assert result["error"]["details"]["mutation_applied"] is False
    assert _draft_count(store) == 0
    assert _mutation_receipt_count(store) == 1


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
    ruleset = options.ruleset_revisions[0]
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
            "judgement": {
                "strategy": "existing_ruleset",
                "ruleset_revision_id": ruleset.id,
                "expected_ruleset_version": ruleset.version,
                "expected_ruleset_content_hash": ruleset.content_hash,
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


def test_published_creation_session_directs_new_investigation_to_new_session(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(creation_stack, workspace_key="published-boundary")
    _publish_fake_run(creation_stack, result, idempotency_key="published-boundary-confirm")
    stack = creation_stack
    before_runs = _run_count(stack["creation_store"])
    accepted = stack["client"].post(
        f"/api/investigation-workspaces/{result['workspace_id']}/turns",
        json={
            "client_message_id": "published-boundary-followup",
            "content": "照当前配置再做一次，给我一个新的独立草稿，先不要启动",
        },
    )
    assert accepted.status_code == 202
    turn_id = accepted.json()["turn_id"]
    with stack["client"].stream(
        "GET", f"/api/investigation-workspace-turns/{turn_id}/events"
    ) as response:
        assert response.status_code == 200
        events = [line for line in response.iter_lines() if line.startswith("data: ")]
    terminal = json.loads(events[-1][6:])
    assert terminal["stage"] == "completed"
    assert terminal["answer"] == (
        "当前会话已生成并发布报告，后续可继续围绕该报告进行提问。\n"
        "如需基于当前配置发起新的调查，请新建会话。"
    )
    assert _run_count(stack["creation_store"]) == before_runs


def test_fake_runtime_publishes_linked_structured_frontend_contract(
    creation_stack: dict,
) -> None:
    result = _create_completed_turn(
        creation_stack, workspace_key="structured-fixture-contract"
    )
    projection = _publish_fake_run(
        creation_stack, result, idempotency_key="structured-fixture-confirm"
    )
    report_version_id = projection["report_version_id"]

    assert projection["task_stats"]["ingested_count"] == 1
    assert projection["task_stats"]["completed_analysis_count"] == 1

    presentation = creation_stack["client"].get(
        f"/api/report-versions/{report_version_id}/presentation-projection"
    )
    assert presentation.status_code == 200
    assert presentation.json()["statistics"]["canonical_posts"] == 1
    assert presentation.json()["statistics"]["risk_level"]["high"] == 1
    assert presentation.json()["statistics"]["evidence"]["direct"] == 5

    appendix = creation_stack["client"].get(
        f"/api/report-versions/{report_version_id}/appendix",
        params={"view": "posts", "limit": 100},
    )
    assert appendix.status_code == 200
    post_page = appendix.json()
    assert post_page["item_kind"] == "post"
    assert post_page["matched_count"] == 1
    post = post_page["items"][0]
    assert post["decision"] == "reject"
    assert post["risk_level"] == "high"
    assert post["audit_summary"]
    post_ref = post["post_ref"]
    finding_ref = post["audit_finding_ref"]

    detail = creation_stack["client"].get(
        f"/api/report-versions/{report_version_id}/posts/{post_ref}"
    )
    assert detail.status_code == 200
    post_detail = detail.json()
    assert post_detail["post_ref"] == post_ref
    assert post_detail["investigation_finding_refs"] == [finding_ref]
    assert len(post_detail["direct_evidence"]) == 5
    assert {
        item["evidence_type"] for item in post_detail["direct_evidence"]
    } == {"post_text", "ocr", "asr_audio", "comment_text", "visual_frame"}
    evidence_refs = {
        item["evidence_ref"] for item in post_detail["direct_evidence"]
    }
    assert len(evidence_refs) == 5
    assert all(
        item["post_ref"] == post_ref
        and item["audit_finding_ref"] == finding_ref
        and item["support_type"] == "direct"
        for item in post_detail["direct_evidence"]
    )

    finding_evidence = creation_stack["client"].get(
        f"/api/report-versions/{report_version_id}/findings/{finding_ref}/evidence"
    )
    assert finding_evidence.status_code == 200
    finding_body = finding_evidence.json()
    assert finding_body["investigation_finding_ref"] == finding_ref
    assert finding_body["direct_evidence_count"] == 5
    assert {item["evidence_ref"] for item in finding_body["items"]} == evidence_refs

    evidence_appendix = creation_stack["client"].get(
        f"/api/report-versions/{report_version_id}/appendix",
        params={"view": "evidence", "finding_ref": finding_ref, "limit": 50},
    )
    assert evidence_appendix.status_code == 200
    evidence_page = evidence_appendix.json()
    assert evidence_page["item_kind"] == "evidence"
    assert evidence_page["finding_ref"] == finding_ref
    assert evidence_page["matched_count"] == 5
    assert {item["evidence_ref"] for item in evidence_page["items"]} == evidence_refs

    snapshot = creation_stack["report_store"].load_immutable_snapshot(
        report_version_id
    )
    assert len(snapshot.posts) == 1
    assert len(snapshot.findings) == 1
    assert len(snapshot.evidence) == 5
    assert snapshot.posts[0].ref == post_ref
    assert snapshot.findings[0].ref == finding_ref
    assert all(
        item.post_ref == post_ref and item.finding_ref == finding_ref
        for item in snapshot.evidence
    )

    replay = creation_stack["client"].get(
        f"/api/investigation-runs/{projection['run_id']}"
    )
    assert replay.status_code == 200
    assert replay.json()["report_version_id"] == report_version_id
    assert _report_version_count(creation_stack["resource_db"]) == 1


def test_published_report_without_structured_document_fails_closed(
    tmp_path: Path,
) -> None:
    store = ReportStore(tmp_path / "missing-structured-report.sqlite3")
    generation = store.create_generation(
        "missing-structured-report", model="fixture", prompt_version="fixture-v1"
    )
    report_version_id = generation["report_version_id"]
    store.save_source_snapshot(
        {
            "snapshot_id": "snapshot:missing-structured-report",
            "report_version_id": report_version_id,
            "task_id": "missing-structured-report",
            "task_status": "completed",
            "source_hash": "fixture-source-hash",
            "configuration_revision_id": "",
            "finding_ids": [],
            "evidence_ids": [],
            "data_quality_warnings": [],
            "statistic_inputs": [],
            "generated_at": "2026-08-31T00:00:00+00:00",
            "snapshot_hash": "fixture-snapshot-hash",
        }
    )
    store.publish_version(
        report_version_id=report_version_id,
        title="legacy fixture",
        body_markdown="# legacy fixture",
        body_json={"human_report": {}},
        sections=[],
        citation_details={},
    )

    with pytest.raises(
        ReportGenerationError,
        match="ReportVersion has no structured frontend report document",
    ):
        store.get_presentation_projection(report_version_id)


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
                "content": "请生成世界杯博彩调查方案，确认并开始调查",
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


def test_creation_mode_keeps_legacy_tools_and_adds_resource_tools() -> None:
    from backend.resource_management.tools import RESOURCE_TOOL_INPUTS
    assert set(M3_TOOL_INPUTS) == set(RESOURCE_TOOL_INPUTS) | {
        "use_ruleset_proposal",
        "create_ruleset_proposal",
        "update_ruleset_proposal",
        "get_ruleset_proposal",
        "query_investigation_options",
        "create_investigation_draft",
        "update_investigation_draft",
        "get_investigation_draft",
        "confirm_and_queue_investigation",
        "get_investigation_run",
    }
    parameters = [schema["parameters"] for schema in HERMES_M3_TOOL_SCHEMAS]
    assert '"wb"' not in json.dumps(parameters, sort_keys=True)
    query_schema = next(
        schema
        for schema in HERMES_M3_TOOL_SCHEMAS
        if schema["name"] == "query_investigation_options"
    )
    assert (
        "include_ruleset_details_for_revision_ids"
        in query_schema["parameters"]["properties"]
    )
    assert "ruleset_revision_ids" in query_schema["parameters"]["properties"]
    serialized_query = json.dumps(query_schema["parameters"], sort_keys=True)
    assert "audit_policy" not in serialized_query
    for tool_name in (
        "create_investigation_draft",
        "update_investigation_draft",
    ):
        mutation_schema = next(
            schema for schema in HERMES_M3_TOOL_SCHEMAS
            if schema["name"] == tool_name
        )
        serialized = json.dumps(mutation_schema["parameters"], sort_keys=True)
        assert "judgement" in serialized
        for forbidden in (
            "audit_policy",
            "capabilities",
            "scoring_template",
            "thresholds",
            "rule_importance",
            "scoring_rules",
            "outputs",
            "crawler_account_id",
        ):
            assert forbidden not in serialized


def test_creation_prompt_leaves_resource_query_timing_to_the_agent() -> None:
    normalized_prompt = " ".join(CREATION_SYSTEM_PROMPT.split())
    assert "Conversation is primary" in CREATION_SYSTEM_PROMPT
    assert "according to the user's current intent" in CREATION_SYSTEM_PROMPT
    assert "For a read-only request" in CREATION_SYSTEM_PROMPT
    assert "without calling\ncreate_investigation_draft" in CREATION_SYSTEM_PROMPT
    assert '"I want to investigate X" is sufficient' in normalized_prompt
    assert "When resource discovery is needed" in normalized_prompt
    assert "Complete resource identities already present" in normalized_prompt
    assert "authoritatively validate them during creation" in normalized_prompt
    assert "Only call\nconfirm_and_queue_investigation after an explicit" in CREATION_SYSTEM_PROMPT
    for workflow_first_instruction in (
        "Always call\nquery_investigation_options first",
        "first call query_investigation_options in the same turn",
        "Query that lexicon with\ninclude_lexicon_terms_for_ids before creating",
        "MUST create exactly one editable",
        "Do not stop at a prose summary",
    ):
        assert workflow_first_instruction not in CREATION_SYSTEM_PROMPT


def test_creation_prompt_defaults_only_editable_fields_from_real_resources() -> None:
    assert (
        "editable recommended configuration, not a final confirmed execution"
        in CREATION_SYSTEM_PROMPT
    )
    assert "do\nnot require explicit confirmation of every editable or defaultable field" in (
        CREATION_SYSTEM_PROMPT
    )
    assert "preserve any available platform the user explicitly selected" in (
        CREATION_SYSTEM_PROMPT
    )
    assert "choose one reasonable available platform from the available resource context" in (
        CREATION_SYSTEM_PROMPT
    )
    assert "available in the conversation context" in CREATION_SYSTEM_PROMPT
    assert "authoritative term snapshot is the recall\nconfiguration" in (
        CREATION_SYSTEM_PROMPT
    )
    assert "do not ask the user to enter separate search keywords" in (
        CREATION_SYSTEM_PROMPT
    )


def test_creation_prompt_keeps_hard_missing_resource_boundaries() -> None:
    assert "Never fabricate missing domain resources" in CREATION_SYSTEM_PROMPT
    assert "no real recall configuration can be formed" in CREATION_SYSTEM_PROMPT
    assert "the user's requested\nplatform is unavailable" in CREATION_SYSTEM_PROMPT
    assert "do not create a\nmisleading Draft" in CREATION_SYSTEM_PROMPT
    assert "cannot default a missing creator homepage URL" in CREATION_SYSTEM_PROMPT
    assert "Only call\nconfirm_and_queue_investigation after an explicit" in (
        CREATION_SYSTEM_PROMPT
    )


def test_creation_tool_descriptions_are_capability_oriented() -> None:
    query_description = M3_TOOL_DESCRIPTIONS["query_investigation_options"]
    create_description = M3_TOOL_DESCRIPTIONS["create_investigation_draft"]
    assert "discover, explain, compare, recommend, or configure" in query_description
    assert "categories, 审核规则, hit conditions, exemptions" in query_description
    assert "application stages" in query_description
    assert "when the user wants" in create_description
    descriptions = " ".join(M3_TOOL_DESCRIPTIONS.values())
    for workflow_instruction in (
        "must call this before",
        "must subsequently create",
        "first step",
    ):
        assert workflow_instruction not in descriptions.lower()


@pytest.mark.parametrize(
    ("content", "query_arguments"),
    [
        ("现在有哪些召回词库？", {"mode": "search"}),
        (
            "有哪些博彩相关词库？",
            {"domain_hint": "博彩", "mode": "search"},
        ),
        ("现在有哪些研判方案？", {"mode": "search"}),
        (
            "赌博博彩研判方案使用了什么规则？",
            {"domain_hint": "赌博博彩", "mode": "search"},
        ),
        (
            "我想调查世界杯博彩风险。",
            {"domain_hint": "世界杯博彩风险", "mode": "search"},
        ),
    ],
)
def test_query_only_resource_turn_stops_without_creating_draft(
    creation_stack: dict,
    content: str,
    query_arguments: dict[str, Any],
) -> None:
    before = _draft_count(creation_stack["creation_store"])

    turn = _run_scripted_creation_turn(
        creation_stack,
        content=content,
        actions=[("query_investigation_options", query_arguments)],
        client_message_id=f"query-only:{content}",
    )

    assert [name for name, _ in turn["agent"].calls] == [
        "query_investigation_options"
    ]
    assert turn["result"].tool_names == ("query_investigation_options",)
    assert turn["turn"].public_artifact == {}
    assert _draft_count(creation_stack["creation_store"]) == before == 0
    state = creation_stack["conversation"].get_workspace_state(
        turn["session_id"], principal=Principal("principal-a")
    )
    assert state.draft_artifact == {}


def test_query_only_lexicon_terms_returns_terms_without_draft_artifact(
    creation_stack: dict,
) -> None:
    initial = creation_stack["tool_service"].execute(
        "query_investigation_options",
        {"domain_hint": "博彩", "mode": "search"},
        principal=Principal("principal-a"),
    )
    lexicon = next(item for item in initial["recall_lexicons"] if item["available"])
    arguments = {
        "domain_hint": "博彩",
        "mode": "search",
        "lexicon_ids": [lexicon["id"]],
        "include_lexicon_terms_for_ids": [lexicon["id"]],
    }

    turn = _run_scripted_creation_turn(
        creation_stack,
        content="这个词库里面有哪些词？",
        actions=[("query_investigation_options", arguments)],
        client_message_id="query-lexicon-terms",
    )

    returned = turn["agent"].results[0]["recall_lexicons"][0]
    assert returned["id"] == lexicon["id"]
    assert returned["terms_included"] is True
    assert returned["enabled_main_terms"]
    assert turn["turn"].public_artifact == {}
    assert _draft_count(creation_stack["creation_store"]) == 0


def test_query_only_ruleset_detail_returns_rule_fields_without_draft_artifact(
    creation_stack: dict,
) -> None:
    turn = _run_scripted_creation_turn(
        creation_stack,
        content="这个研判方案的风险分类、规则、命中和豁免条件是什么？",
        actions=[
            (
                "query_investigation_options",
                {"domain_hint": "赌博博彩", "mode": "search"},
            ),
            ("query_investigation_options", _requested_ruleset_details),
        ],
        client_message_id="query-ruleset-detail",
    )

    detail_result = turn["agent"].results[1]
    assert len(detail_result["ruleset_revision_details"]) == 1
    detail = detail_result["ruleset_revision_details"][0]
    assert detail["categories"]
    category = next(
        item
        for item in detail["categories"]
        if item["category_id"] == "gambling.access_and_funds"
    )
    rule = next(
        item
        for item in category["rules"]
        if item["rule_id"] == "gambling.platform_entry_and_funding"
    )
    assert detail["general_exemptions"][0]["condition"]
    assert rule["hit_condition"]
    assert rule["rule_exemptions"][0]["condition"]
    assert rule["adjudication_notes"]
    assert rule["application_stages"] == [
        "image_evidence",
        "video_frame_evidence",
        "comment_audit",
        "fusion_audit",
    ]
    assert "source_mappings" not in json.dumps(detail_result, ensure_ascii=False)
    assert [name for name, _ in turn["agent"].calls] == [
        "query_investigation_options",
        "query_investigation_options",
    ]
    assert turn["result"].tool_names == (
        "query_investigation_options",
        "query_investigation_options",
    )
    assert turn["turn"].public_artifact == {}
    assert _draft_count(creation_stack["creation_store"]) == 0
    assert _run_count(creation_stack["creation_store"]) == 0
    assert _job_count(creation_stack["resource_db"]) == 0


def test_explicit_create_still_uses_query_then_create_and_returns_draft(
    creation_stack: dict,
) -> None:
    turn = _run_scripted_creation_turn(
        creation_stack,
        content="就按刚才这个配置创建调查。",
        actions=_explicit_create_actions(),
        final_response="已创建可编辑调查草案，尚未开始调查。",
        client_message_id="explicit-create",
    )

    assert [name for name, _ in turn["agent"].calls] == [
        "query_investigation_options",
        "query_investigation_options",
        "create_investigation_draft",
    ]
    assert turn["turn"].public_artifact["artifact_type"] == "investigation_draft"
    assert turn["turn"].public_artifact["draft_revision"] == 1
    assert _draft_count(creation_stack["creation_store"]) == 1
    assert _run_count(creation_stack["creation_store"]) == 0


def test_existing_draft_get_update_and_confirm_do_not_create_second_draft(
    creation_stack: dict,
) -> None:
    created = _run_scripted_creation_turn(
        creation_stack,
        content="用当前配置创建调查。",
        actions=_explicit_create_actions(),
        client_message_id="lifecycle-create",
    )
    session_id = created["session_id"]
    draft = created["turn"].public_artifact["draft"]

    viewed = _run_scripted_creation_turn(
        creation_stack,
        session_id=session_id,
        content="把刚才的 Draft 给我看看。",
        actions=[("get_investigation_draft", {"draft_id": draft["id"]})],
        client_message_id="lifecycle-get",
    )
    assert [name for name, _ in viewed["agent"].calls] == [
        "get_investigation_draft"
    ]
    assert viewed["turn"].public_artifact["draft_id"] == draft["id"]
    assert _draft_count(creation_stack["creation_store"]) == 1

    updated_configuration = json.loads(json.dumps(draft["configuration"]))
    updated_configuration["platform"] = "ks"
    updated = _run_scripted_creation_turn(
        creation_stack,
        session_id=session_id,
        content="把快手加进去。",
        actions=[
            (
                "update_investigation_draft",
                {
                    "draft_id": draft["id"],
                    "expected_revision": 1,
                    "configuration": updated_configuration,
                },
            )
        ],
        client_message_id="lifecycle-update",
    )
    assert [name for name, _ in updated["agent"].calls] == [
        "update_investigation_draft"
    ]
    assert updated["turn"].public_artifact["draft_revision"] == 2
    assert updated["turn"].public_artifact["draft"]["configuration"]["platform"] == "ks"
    assert _draft_count(creation_stack["creation_store"]) == 1

    confirmed = _run_scripted_creation_turn(
        creation_stack,
        session_id=session_id,
        content="确认开始。",
        actions=[
            (
                "confirm_and_queue_investigation",
                {
                    "draft_id": draft["id"],
                    "expected_revision": 2,
                    "confirmed": True,
                    "idempotency_key": "conversation-first-confirm",
                },
            )
        ],
        client_message_id="lifecycle-confirm",
    )
    assert [name for name, _ in confirmed["agent"].calls] == [
        "confirm_and_queue_investigation"
    ]
    assert confirmed["turn"].public_artifact["artifact_type"] == "investigation_run"
    assert _draft_count(creation_stack["creation_store"]) == 1
    assert _run_count(creation_stack["creation_store"]) == 1


def test_same_turn_direct_create_with_known_resources_reaches_application(
    creation_stack: dict,
) -> None:
    options = creation_stack["app_service"].query_investigation_options(
        QueryInvestigationOptions(
            domain_hint="博彩",
            mode="search",
            include_lexicon_terms_for_ids=["gambling"],
        ),
        principal=Principal("principal-a"),
    ).model_dump(mode="json")
    create_arguments = _search_draft_from_options([options])

    direct = _run_scripted_creation_turn(
        creation_stack,
        content="按这些已知资源创建调查。",
        actions=[("create_investigation_draft", create_arguments)],
        client_message_id="direct-create-known-resources",
    )

    assert [name for name, _ in direct["agent"].calls] == [
        "create_investigation_draft"
    ]
    assert direct["result"].tool_names == ("create_investigation_draft",)
    assert direct["turn"].status == "completed"
    assert direct["turn"].public_artifact["artifact_type"] == "investigation_draft"
    assert _draft_count(creation_stack["creation_store"]) == 1
    assert _run_count(creation_stack["creation_store"]) == 0


@pytest.mark.parametrize(
    ("field_path", "invalid_value", "expected_code"),
    [
        (
            ("judgement", "ruleset_revision_id"),
            "ruleset-revision:missing:v1",
            "INVALID_RESOURCE_REFERENCE",
        ),
        (
            ("judgement", "expected_ruleset_content_hash"),
            "0" * 64,
            "RESOURCE_STALE",
        ),
        (
            ("investigation", "recall_plan", "lexicon_id"),
            "missing-lexicon",
            "INVALID_RESOURCE_REFERENCE",
        ),
    ],
)
def test_direct_create_without_turn_query_keeps_authoritative_resource_rejection(
    creation_stack: dict,
    field_path: tuple[str, ...],
    invalid_value: str,
    expected_code: str,
) -> None:
    options = creation_stack["app_service"].query_investigation_options(
        QueryInvestigationOptions(
            domain_hint="博彩",
            mode="search",
            include_lexicon_terms_for_ids=["gambling"],
        ),
        principal=Principal("principal-a"),
    ).model_dump(mode="json")
    arguments = _search_draft_from_options([options])
    target = arguments["configuration"]
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = invalid_value

    result = creation_stack["tool_service"].execute_with_identity(
        "create_investigation_draft",
        arguments,
        principal=Principal("principal-a"),
        identity=HermesToolExecutionIdentity.require(
            session_id="session:phase-2c-invalid-direct-create",
            turn_id="turn:phase-2c-invalid-direct-create",
            tool_call_id=f"call:{field_path[-1]}",
        ),
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == expected_code
    assert result["error"]["details"]["mutation_applied"] is False
    assert _draft_count(creation_stack["creation_store"]) == 0
    assert _mutation_receipt_count(creation_stack["creation_store"]) == 1


def test_cross_turn_known_resources_can_be_reused_without_same_turn_query(
    creation_stack: dict,
) -> None:
    queried = _run_scripted_creation_turn(
        creation_stack,
        content="有哪些博彩相关规则和召回词库？",
        actions=[
            (
                "query_investigation_options",
                {
                    "domain_hint": "博彩",
                    "mode": "search",
                    "include_lexicon_terms_for_ids": ["gambling"],
                },
            )
        ],
        client_message_id="cross-turn-resource-query",
    )
    assert queried["result"].tool_names == ("query_investigation_options",)
    assert queried["turn"].public_artifact == {}
    assert _draft_count(creation_stack["creation_store"]) == 0

    create_arguments = _search_draft_from_options(queried["agent"].results)
    created = _run_scripted_creation_turn(
        creation_stack,
        session_id=queried["session_id"],
        content="就按刚才的资源，调查抖音上的世界杯博彩风险。",
        actions=[("create_investigation_draft", create_arguments)],
        client_message_id="cross-turn-direct-create",
    )

    assert [name for name, _ in created["agent"].calls] == [
        "create_investigation_draft"
    ]
    assert created["result"].tool_names == ("create_investigation_draft",)
    assert created["turn"].status == "completed"
    assert created["turn"].public_artifact["artifact_type"] == "investigation_draft"
    assert _draft_count(creation_stack["creation_store"]) == 1
    assert _run_count(creation_stack["creation_store"]) == 0


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
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "answer_stream_enabled", True)
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
    answer_message_id = "public-answer:" + "a" * 32
    answer_event = creation_stack["conversation"].store.append_public_stream_event(
        turn.id,
        event_type="answer_delta",
        payload={
            "message_id": answer_message_id,
            "revision": 1,
            "delta": "正在恢复的报告回答",
        },
        idempotency_key="public-event:" + "b" * 32,
    )

    pending = creation_stack["client"].get(
        f"/api/investigation-workspaces/{result['workspace_id']}/state"
    )
    assert pending.status_code == 200
    assert pending.json()["latest_report_turn"]["turn_id"] == turn.id
    assert pending.json()["latest_report_turn"]["status"] == "running"
    assert [item["role"] for item in pending.json()["report_messages"]] == ["user"]
    assert pending.json()["report_answer_draft"] == {
        "message_id": answer_message_id,
        "revision": 1,
        "text": "正在恢复的报告回答",
        "event_sequence": answer_event["sequence"],
    }
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
    assert interrupted.json()["report_answer_draft"] is None

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


def test_creation_answer_stream_is_filtered_revisioned_and_canonical(
    creation_stack: dict,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "activity_stream_enabled", True)
    monkeypatch.setattr(settings, "answer_stream_enabled", False)
    monkeypatch.setattr(settings, "creation_answer_stream_enabled", True)
    conversation = creation_stack["conversation"]
    captured: dict[str, Any] = {}
    raw_final = (
        "调查方案已生成。"
        "draft_id=investigation-draft:" + "a" * 32
        + "，尚未开始采集。内部文件 /Users/private/runtime/state.db 不应展示。"
    )

    class StreamingAgent:
        def __init__(self, **options: Any) -> None:
            captured.update(options)
            self.options = options

        def close(self) -> None:
            return None

        def run_conversation(self, message: str, **kwargs: Any) -> dict[str, Any]:
            self.options["step_callback"](1, [])
            self.options["stream_delta_callback"]("正在读取可用配置。")
            self.options["tool_start_callback"](
                "private-tool-call-id",
                "query_investigation_options",
                {"draft_id": "private-argument"},
            )
            self.options["tool_complete_callback"](
                "private-tool-call-id",
                "query_investigation_options",
                {"draft_id": "private-argument"},
                {"status": "ok", "data": {"receipt_id": "private-receipt"}},
            )
            self.options["step_callback"](2, ["query_investigation_options"])
            for offset in range(0, len(raw_final), 9):
                self.options["stream_delta_callback"](raw_final[offset : offset + 9])
            history = [dict(item) for item in kwargs.get("conversation_history") or []]
            return {
                "completed": True,
                "failed": False,
                "interrupted": False,
                "final_response": raw_final,
                "messages": [
                    *history,
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": raw_final},
                ],
                "turn_exit_reason": "completed",
                "api_calls": 1,
            }

    class Runtime:
        def create_agent(self, **options: Any) -> StreamingAgent:
            return StreamingAgent(**options)

        @contextmanager
        def product_mode_execution(self, *_args: Any, **_kwargs: Any):
            yield

    conversation.fake_runtime = False
    conversation.runtime_binding = Runtime()
    session = conversation.create_session(
        principal=Principal("principal-a"), workspace_key="creation-answer-stream"
    )
    turn, replayed = conversation.accept_message(
        session.id,
        client_message_id="creation-answer-stream-message",
        content="请说明当前可用配置，不要启动任务。",
        principal=Principal("principal-a"),
    )
    assert replayed is False
    result = conversation.execute_turn(turn.id)

    expected = redact_creation_internal_references(raw_final)[0]
    assert result.answer == expected
    assert callable(captured["stream_delta_callback"])
    events = conversation.store.list_public_turn_events(turn.id)
    answer_events = [
        event
        for event in events
        if event["event_type"] in {"answer_delta", "answer_reset"}
    ]
    assert any(event["event_type"] == "answer_reset" for event in answer_events)
    revision = 0
    draft = ""
    for event in answer_events:
        if event["event_type"] == "answer_reset":
            revision = event["revision"]
            draft = ""
        elif event["revision"] >= revision:
            if event["revision"] > revision:
                revision = event["revision"]
                draft = ""
            draft += event["delta"]
    assert draft == expected
    activity = [event for event in events if event["event_type"] == "activity"]
    assert [event["status"] for event in activity] == ["running", "succeeded"]
    serialized = json.dumps(events, ensure_ascii=False)
    for forbidden in (
        "private-tool-call-id",
        "private-argument",
        "private-receipt",
        "investigation-draft:",
        "/Users/private",
        "draft_id",
    ):
        assert forbidden not in serialized


def test_creation_answer_filter_covers_resource_run_and_receipt_metadata() -> None:
    raw = "\n".join(
        [
            "审核规则草案已生成，尚未正式保存。",
            'proposal_id="ruleset-proposal:' + "a" * 32 + '"',
            'presentation_id="ruleset-presentation:' + "b" * 32 + '"',
            'run_id="investigation-run:' + "c" * 32 + '"',
            'receipt_id="creation-tool-receipt:' + "d" * 32 + '"',
            'content_hash="' + "e" * 64 + '"',
            "tool_call_id=tool-call:private-call-id",
            "runtime=/tmp/private/creation.sqlite3",
            "应用阶段：image_evidence、comment_audit、fusion_audit。",
        ]
    )

    public, changed = redact_creation_internal_references(raw)

    assert changed is True
    assert "审核规则草案已生成，尚未正式保存。" in public
    assert "图片证据提取、评论研判、融合研判" in public
    for forbidden in (
        "proposal_id",
        "presentation_id",
        "run_id",
        "receipt_id",
        "content_hash",
        "tool_call_id",
        "ruleset-proposal:",
        "ruleset-presentation:",
        "investigation-run:",
        "creation-tool-receipt:",
        "/tmp/private",
        "image_evidence",
        "comment_audit",
        "fusion_audit",
    ):
        assert forbidden not in public


def test_workspace_state_restores_creation_answer_only_when_enabled(
    creation_stack: dict,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "creation_answer_stream_enabled", True)
    conversation = creation_stack["conversation"]
    session = conversation.create_session(
        principal=Principal("principal-a"), workspace_key="pending-creation-answer"
    )
    turn, replayed = conversation.accept_message(
        session.id,
        client_message_id="pending-creation-answer-message",
        content="请读取审核规则。",
        principal=Principal("principal-a"),
    )
    assert replayed is False
    conversation.store.append_public_turn_event(turn.id, stage="answering")
    event = conversation.store.append_public_stream_event(
        turn.id,
        event_type="answer_delta",
        payload={
            "message_id": "public-answer:" + "c" * 32,
            "revision": 1,
            "delta": "正在整理调查建议",
        },
        idempotency_key="public-event:" + "d" * 32,
    )

    state = creation_stack["client"].get(
        f"/api/investigation-workspaces/{session.id}/state"
    )
    assert state.status_code == 200
    assert state.json()["creation_answer_draft"] == {
        "message_id": "public-answer:" + "c" * 32,
        "revision": 1,
        "text": "正在整理调查建议",
        "event_sequence": event["sequence"],
    }
    assert state.json()["report_answer_draft"] is None

    monkeypatch.setattr(settings, "creation_answer_stream_enabled", False)
    disabled = creation_stack["client"].get(
        f"/api/investigation-workspaces/{session.id}/state"
    )
    assert disabled.status_code == 200
    assert disabled.json()["creation_answer_draft"] is None


def test_creation_stream_projection_failure_does_not_repeat_or_block_draft(
    creation_stack: dict,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "activity_stream_enabled", True)
    monkeypatch.setattr(settings, "creation_answer_stream_enabled", True)
    conversation = creation_stack["conversation"]
    projection_attempts = 0

    def fail_projection(*_args, **_kwargs):
        nonlocal projection_attempts
        projection_attempts += 1
        raise OSError("injected creation projection failure")

    monkeypatch.setattr(
        conversation.store,
        "append_public_stream_event",
        fail_projection,
    )
    result = _create_completed_turn(
        creation_stack,
        workspace_key="creation-projection-failure",
    )

    assert result["terminal"]["stage"] == "completed"
    assert result["terminal"]["artifact"]["artifact_type"] == "investigation_draft"
    assert _draft_count(creation_stack["creation_store"]) == 1
    assert _run_count(creation_stack["creation_store"]) == 0
    assert projection_attempts >= 2
    events = conversation.store.list_public_turn_events(result["turn_id"])
    assert events
    assert {event["event_type"] for event in events} == {"turn"}


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


def _t1_temporary_arguments(stack):
    options = stack["app_service"].query_investigation_options(
        QueryInvestigationOptions(include_lexicon_terms_for_ids=["gambling"]),
        principal=Principal("principal-a"),
    ).model_dump(mode="json")
    arguments = _search_draft_from_options([options])
    arguments["configuration"]["investigation"]["recall_plan"] = {
        "strategy": "temporary_terms", "terms": ["外围盘口", "滚球下注", "代理开户"],
        "source_lexicon_ids": [],
    }
    return arguments


@pytest.mark.parametrize("operation", ["create", "update"])
def test_t1_comma_is_rejected_before_receipt_and_application(creation_stack, operation):
    from backend.investigation_creation.contracts import CreateDraftCommand

    arguments = _t1_temporary_arguments(creation_stack)
    if operation == "update":
        draft = creation_stack["app_service"].create_draft(
            CreateDraftCommand(**arguments), principal=Principal("principal-a"),
        )
        arguments = {"draft_id": draft.id, "expected_revision": 1, "configuration": arguments["configuration"]}
    arguments["configuration"]["investigation"]["recall_plan"]["terms"] = ["外围,盘口"]
    with patch.object(creation_stack["app_service"], f"{operation}_draft") as mutation:
        result = creation_stack["tool_service"].execute_with_identity(
            f"{operation}_investigation_draft", arguments, principal=Principal("principal-a"),
            identity=HermesToolExecutionIdentity.require(
                session_id="t1-comma", turn_id=operation, tool_call_id="invalid",
            ),
        )
    mutation.assert_not_called()
    assert result["error"]["code"] == "INVALID_TOOL_ARGUMENTS"
    assert result["error"]["details"]["mutation_applied"] is False
    assert result["error"]["details"]["receipt_created"] is False
    assert _mutation_receipt_count(creation_stack["creation_store"]) == 0
    assert _draft_count(creation_stack["creation_store"]) == (operation == "update")
    if operation == "update":
        assert creation_stack["creation_store"].get_draft(draft.id, principal="principal-a").current_revision == 1


@pytest.mark.parametrize("preauthorized", [False, True])
def test_t1_missing_recall_conversation_contract(creation_stack, preauthorized):
    arguments = _t1_temporary_arguments(creation_stack)
    with sqlite3.connect(creation_stack["resource_db"]) as connection:
        connection.execute("DELETE FROM lexicon_keywords")
        connection.execute("DELETE FROM lexicon_categories")
    actions = [("query_investigation_options", {"domain_hint": "博彩", "mode": "search"})]
    if preauthorized:
        actions.append(("create_investigation_draft", arguments))
    turn = _run_scripted_creation_turn(
        creation_stack,
        content="我想调查博彩风险。" + ("没有合适召回词就帮我生成。" if preauthorized else ""),
        actions=actions,
        final_response=("已创建本次临时召回词 Draft，尚未启动。" if preauthorized
                        else "当前没有找到合适的现成召回词资源，可以为本次调查生成临时搜索词。"),
        client_message_id=f"t1-authorized-{preauthorized}",
    )
    assert turn["agent"].results[0]["recall_lexicons"] == []
    assert turn["turn"].status == "completed"
    assert _draft_count(creation_stack["creation_store"]) == int(preauthorized)
    assert _run_count(creation_stack["creation_store"]) == 0
    if preauthorized:
        plan = turn["agent"].results[-1]["draft"]["configuration"]["investigation"]["recall_plan"]
        assert plan == arguments["configuration"]["investigation"]["recall_plan"]
    else:
        assert turn["turn"].public_artifact == {}
        assert turn["result"].tool_names == ("query_investigation_options",)


def test_t1_generation_guidance_preserves_conversation_authority():
    prompt = " ".join(CREATION_SYSTEM_PROMPT.split())
    assert "Without that authorization" in prompt
    assert "no Draft and no generated terms" in prompt
    assert "do not list even illustrative example terms or candidate terms" in prompt
    assert "Each generated Chinese query is natural continuous text with no whitespace" in prompt
    assert "审核规则 Proposal, then show its 审核规则 and the complete search-term list, and END this turn" in prompt
    assert "create the Draft in the same turn" in prompt
    assert "Do not ask again for permission to generate terms" in prompt
    assert "real recall 黑话库 when it is sufficiently suitable" in prompt
    assert "Generation and editing never bind Draft Judgement" in prompt
    assert "Only use_ruleset_proposal can bind that exact presented version" in prompt
    assert "Preview and Confirm never generate or expand terms" in prompt
    assert "provenance references only" in prompt


def test_t1_draft_card_does_not_recommend_provenance_as_runtime_lexicon(creation_stack):
    arguments = _t1_temporary_arguments(creation_stack)
    arguments["configuration"]["investigation"]["recall_plan"]["source_lexicon_ids"] = ["gambling"]
    turn = _run_scripted_creation_turn(
        creation_stack, content="没有合适召回词就帮我生成这次搜索词并创建调查。",
        actions=[("create_investigation_draft", arguments)], client_message_id="t1-provenance-card",
    )
    artifact = turn["turn"].public_artifact
    assert artifact["suggestion"]["recall_lexicons"] == []
    assert artifact["suggestion"]["search_terms"] == arguments["configuration"]["investigation"]["recall_plan"]["terms"]
    assert artifact["confirmation_preview"]["recall_plan"]["source_lexicon_ids"] == ["gambling"]


def test_production_limits_are_previewed_and_frozen_without_changing_demo_defaults(creation_stack, monkeypatch):
    from backend.audit_agent.config import settings
    from backend.investigation_creation.contracts import ConfirmedConfigurationSnapshotV4
    stack = creation_stack
    options = stack['app_service'].query_investigation_options(
        QueryInvestigationOptions(domain_hint='博彩', mode='search', include_lexicon_terms_for_ids=['gambling']),
        principal=Principal('principal-a'),
    ).model_dump(mode='json')
    arguments = _search_draft_from_options([options])
    response = stack['client'].post('/api/investigation-drafts', json=arguments)
    assert response.status_code == 201, response.text
    draft = response.json()
    endpoint = '/api/investigation-drafts/' + draft['id']
    demo = stack['client'].get(endpoint + '/confirmation-preview').json()
    assert demo['max_notes'] == 1
    monkeypatch.setattr(settings, 'm3_posts_per_keyword', 20)
    monkeypatch.setattr(settings, 'm3_analyze_limit', 340)
    monkeypatch.setattr(settings, 'm3_comments_per_post', 1000)
    preview = stack['client'].get(endpoint + '/confirmation-preview').json()
    assert preview['max_notes'] == min(340, len(preview['resolved_search_terms']) * 5)
    assert preview['max_posts_per_keyword'] == 5
    assert preview['max_comments_per_post'] == 1000
    assert preview['get_sub_comment'] is False
    queued = stack['client'].post(endpoint + '/confirm-and-queue', json={'expected_revision': draft['current_revision'], 'confirmed': True}, headers={'Idempotency-Key': 'production-limits'})
    assert queued.status_code == 202, queued.text
    run = stack['creation_store'].get_run(queued.json()['run_id'], principal='principal-a')
    snapshot = ConfirmedConfigurationSnapshotV4.model_validate(run.confirmed_configuration)
    assert snapshot.max_notes == snapshot.execution.max_notes == 5
    assert snapshot.execution.analyze_limit == 340
    assert snapshot.execution.max_comments == 1000
    assert snapshot.execution.max_concurrency == 1
    assert snapshot.execution.get_sub_comment is False
    from backend.investigation_creation.adapters import AuditPipelineExecutionAdapter
    adapter = AuditPipelineExecutionAdapter.__new__(AuditPipelineExecutionAdapter)
    adapter.crawler_account_store = CrawlerAccountStore(stack['resource_db'])
    adapter._provider_validator = lambda configuration: None
    adapter.validate_m3_configuration(snapshot.execution.model_dump(mode='json'))
    monkeypatch.setattr(settings, 'm3_posts_per_keyword', 1)
    monkeypatch.setattr(settings, 'm3_analyze_limit', 1)
    with pytest.raises(ValueError, match='per-keyword limit'):
        adapter.validate_m3_configuration(snapshot.execution.model_dump(mode='json'))
    changed = snapshot.model_dump(mode='json')
    changed['max_notes'] = 1
    with pytest.raises(ValidationError):
        ConfirmedConfigurationSnapshotV4.model_validate(changed)
