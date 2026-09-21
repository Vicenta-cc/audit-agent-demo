from __future__ import annotations

from types import SimpleNamespace

from backend.audit_agent.config import settings
from backend.api.investigation import public_activity_events_for_turns
from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.investigation.contracts import PublishedReportContext
from backend.investigation.public_activity import (
    PUBLIC_TOOL_ACTIVITIES,
    PublicActivityEmitter,
    public_activity_event,
)
from backend.investigation.store import InvestigationStore
from backend.investigation_creation.conversation import (
    InvestigationCreationConversationService,
)
from backend.investigation_creation.principal import Principal
from backend.investigation_creation.tools import (
    HermesToolExecutionIdentity,
    InvestigationCreationToolService,
    M3_TOOL_INPUTS,
)
from hermes_m0.pass_support import pass_tool_schemas
from hermes_m0.schemas import (
    M2_ACCOUNT_ACTIVITY_TOOLS,
    REAL_REPORT_TASK_TOOLS,
    REPORT_TASK_TOOLS,
    TASK_TOOLS,
    TOOLS,
)
from hermes_m0.unified_support import unified_tool_schemas


def test_every_product_tool_has_an_explicit_public_activity_mapping():
    catalogs = (
        TOOLS,
        TASK_TOOLS,
        REPORT_TASK_TOOLS,
        REAL_REPORT_TASK_TOOLS,
        M2_ACCOUNT_ACTIVITY_TOOLS,
        pass_tool_schemas(),
        unified_tool_schemas(),
    )
    expected = {
        str(schema["name"])
        for catalog in catalogs
        for schema in catalog
    } | set(M3_TOOL_INPUTS)
    assert set(PUBLIC_TOOL_ACTIVITIES) == expected


def test_projection_uses_only_fixed_public_copy_and_hashed_identifiers():
    started = public_activity_event(
        turn_id="investigation-turn:private-database-id",
        tool_call_id="tool-call:private-model-id",
        tool_name="confirm_and_queue_investigation",
        phase="started",
    )
    assert started is not None
    started_payload, started_key = started
    assert started_payload["status"] == "running"
    assert started_payload["label"] == "提交调查任务"
    assert "已确认" not in started_payload["summary"]
    assert started_payload["activity_id"].startswith("public-activity:")
    assert started_key.startswith("public-event:")

    completed = public_activity_event(
        turn_id="investigation-turn:private-database-id",
        tool_call_id="tool-call:private-model-id",
        tool_name="confirm_and_queue_investigation",
        phase="completed",
        result={
            "status": "ok",
            "data": {
                "run_id": "private-run-id",
                "raw_arguments": {"confirmed": True},
            },
        },
    )
    assert completed is not None
    completed_payload, _ = completed
    assert completed_payload["status"] == "succeeded"
    assert completed_payload["summary"] == "调查任务已确认并提交。"

    failed = public_activity_event(
        turn_id="investigation-turn:private-database-id",
        tool_call_id="tool-call:failed-private-id",
        tool_name="save_resource",
        phase="completed",
        result={
            "status": "error",
            "error": {
                "message": "private stack trace and resource id",
                "details": {"edit_id": "private-edit-id"},
            },
        },
    )
    assert failed is not None
    failed_payload, _ = failed
    assert failed_payload["status"] == "failed"

    serialized = str((started, completed, failed))
    for private_value in (
        "private-database-id",
        "private-model-id",
        "private-run-id",
        "private stack trace",
        "private-edit-id",
    ):
        assert private_value not in serialized
    assert public_activity_event(
        turn_id="investigation-turn:1",
        tool_call_id="tool-call:1",
        tool_name="unknown_private_tool",
        phase="started",
    ) is None


def test_options_activity_copy_distinguishes_catalog_and_selected_lexicon_terms():
    catalog = public_activity_event(
        turn_id="investigation-turn:options",
        tool_call_id="tool-call:catalog",
        tool_name="query_investigation_options",
        phase="completed",
        arguments={"platform": "dy", "mode": "search"},
        result={"status": "ok", "data": {}},
    )
    terms = public_activity_event(
        turn_id="investigation-turn:options",
        tool_call_id="tool-call:terms",
        tool_name="query_investigation_options",
        phase="completed",
        arguments={
            "platform": "dy",
            "mode": "search",
            "include_lexicon_terms_for_ids": ["hate", "minority"],
        },
        result={"status": "ok", "data": {}},
    )
    ruleset = public_activity_event(
        turn_id="investigation-turn:options",
        tool_call_id="tool-call:ruleset",
        tool_name="query_investigation_options",
        phase="completed",
        arguments={
            "platform": "dy",
            "mode": "search",
            "include_ruleset_details_for_revision_ids": ["ruleset-revision:1"],
        },
        result={"status": "ok", "data": {}},
    )

    assert catalog is not None and terms is not None and ruleset is not None
    assert catalog[0]["label"] == "查询可用平台与审核资源"
    assert catalog[0]["summary"] == "已读取可用平台与审核资源。"
    assert terms[0]["label"] == "读取所选黑话库词条"
    assert terms[0]["summary"] == "已读取所选黑话库词条。"
    assert ruleset[0]["label"] == "读取所选审核规则详情"
    assert ruleset[0]["summary"] == "已读取所选审核规则详情。"


def test_emitter_is_durable_idempotent_and_interrupts_unfinished_activity(tmp_path):
    store = InvestigationStore(tmp_path / "investigation.sqlite3")
    session = store.create_session(_report_context())
    turn, _ = store.create_turn(
        session.id,
        client_message_id="client-message:activity",
        user_input="这份报告有什么风险？",
    )
    store.append_public_turn_event(turn.id, stage="accepted")
    emitter = PublicActivityEmitter(store, enabled=lambda: True)
    callbacks = emitter.agent_callbacks(session.id)

    with emitter.bind_turn(session.id, turn.id):
        callbacks["tool_start_callback"](
            "tool-call:read-report",
            "read_report",
            {"database_id": "must-not-be-persisted"},
        )
        callbacks["tool_start_callback"](
            "tool-call:read-report",
            "read_report",
            {"database_id": "must-not-be-persisted"},
        )
        callbacks["tool_complete_callback"](
            "tool-call:read-report",
            "read_report",
            {"database_id": "must-not-be-persisted"},
            '{"ok":true,"data":{"private":"must-not-be-persisted"}}',
        )
        callbacks["tool_complete_callback"](
            "tool-call:read-report",
            "read_report",
            {},
            '{"ok":true}',
        )
        callbacks["tool_start_callback"](
            "tool-call:pending",
            "read_evidence",
            {"evidence_ref": "private-evidence-id"},
        )
        callbacks["tool_start_callback"](
            "tool-call:unknown",
            "unknown_private_tool",
            {"secret": "must-not-be-persisted"},
        )

    events = store.list_public_turn_events(turn.id)
    activities = [event for event in events if event["event_type"] == "activity"]
    assert [event["status"] for event in activities] == [
        "running",
        "succeeded",
        "running",
        "interrupted",
    ]
    assert [event["label"] for event in activities] == [
        "读取报告概览",
        "读取报告概览",
        "读取证据内容",
        "读取证据内容",
    ]
    serialized = str(activities)
    assert "tool-call:" not in serialized
    assert "must-not-be-persisted" not in serialized
    assert "private-evidence-id" not in serialized


def test_completion_backfills_missing_running_without_duplicate_execution(tmp_path):
    store = InvestigationStore(tmp_path / "investigation.sqlite3")
    session = store.create_session(_report_context())
    turn, _ = store.create_turn(
        session.id,
        client_message_id="client-message:backfill",
        user_input="保存并启动。",
    )
    store.append_public_turn_event(turn.id, stage="accepted")
    emitter = PublicActivityEmitter(store, enabled=lambda: True)

    with emitter.bind_turn(session.id, turn.id):
        emitter.tool_completed(
            session.id,
            "tool-call:confirm",
            "confirm_and_queue_investigation",
            {"status": "ok", "data": {"run_id": "must-not-be-persisted"}},
        )

    activities = [
        event
        for event in store.list_public_turn_events(turn.id)
        if event["event_type"] == "activity"
    ]
    assert [event["status"] for event in activities] == ["running", "succeeded"]
    assert len({event["activity_id"] for event in activities}) == 1
    assert "must-not-be-persisted" not in str(activities)

    # A restarted projector may not remember the start callback, but the
    # durable idempotency key still prevents a second public running event.
    before_restart = store.list_public_turn_events(turn.id)
    restarted = PublicActivityEmitter(store, enabled=lambda: True)
    with restarted.bind_turn(session.id, turn.id):
        restarted.tool_completed(
            session.id,
            "tool-call:confirm",
            "confirm_and_queue_investigation",
            {"status": "ok"},
        )
    assert store.list_public_turn_events(turn.id) == before_restart
    assert len([
        event
        for event in store.list_public_turn_events(turn.id)
        if event["event_type"] == "activity"
    ]) == 2


def test_workspace_activity_recovery_returns_only_validated_public_events(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "activity_stream_enabled", True)
    store = InvestigationStore(tmp_path / "investigation.sqlite3")
    session = store.create_session(_report_context())
    turn, _ = store.create_turn(
        session.id,
        client_message_id="client-message:recovery",
        user_input="读取报告。",
    )
    store.append_public_turn_event(turn.id, stage="accepted")
    emitter = PublicActivityEmitter(store, enabled=lambda: True)
    with emitter.bind_turn(session.id, turn.id):
        emitter.tool_started(session.id, "tool-call:read", "read_report")
        emitter.tool_completed(
            session.id,
            "tool-call:read",
            "read_report",
            {"status": "ok", "data": {"private": "must-not-be-persisted"}},
        )

    recovered = public_activity_events_for_turns(
        store,
        [turn.id, turn.id, ""],
    )
    assert [event.status for event in recovered] == ["running", "succeeded"]
    assert {event.turn_id for event in recovered} == {turn.id}
    assert "must-not-be-persisted" not in str(recovered)

    monkeypatch.setattr(settings, "activity_stream_enabled", False)
    assert public_activity_events_for_turns(store, [turn.id]) == ()


def test_workspace_activity_recovery_reads_only_recent_turn_activity(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "activity_stream_enabled", True)
    store = InvestigationStore(tmp_path / "investigation.sqlite3")
    session = store.create_session(_report_context())
    emitter = PublicActivityEmitter(store, enabled=lambda: True)
    turn_ids: list[str] = []

    for index in range(4):
        turn, _ = store.create_turn(
            session.id,
            client_message_id=f"client-message:bounded-{index}",
            user_input=f"读取第 {index + 1} 轮报告。",
        )
        turn_ids.append(turn.id)
        store.append_public_turn_event(turn.id, stage="accepted")
        with emitter.bind_turn(session.id, turn.id):
            emitter.tool_started(
                session.id,
                f"tool-call:bounded-{index}",
                "read_report",
            )
            emitter.tool_completed(
                session.id,
                f"tool-call:bounded-{index}",
                "read_report",
                {"status": "ok"},
            )
        store.fail_turn(
            turn.id,
            error_code="bounded_recovery_fixture",
            safe_message="测试 Turn 已结束。",
            retryable=False,
        )

    original_list_events = store.list_public_turn_events
    reads: list[tuple[str, tuple[str, ...] | None]] = []

    def observed_list_events(
        turn_id: str,
        *,
        after_sequence: int = 0,
        event_types: tuple[str, ...] | None = None,
    ):
        reads.append((turn_id, event_types))
        return original_list_events(
            turn_id,
            after_sequence=after_sequence,
            event_types=event_types,
        )

    monkeypatch.setattr(store, "list_public_turn_events", observed_list_events)
    recovered = public_activity_events_for_turns(
        store,
        [turn_ids[0], turn_ids[1], turn_ids[1], *turn_ids[2:]],
        max_turns=2,
    )

    assert {event.turn_id for event in recovered} == set(turn_ids[-2:])
    assert len(recovered) == 4
    assert reads == [
        (turn_ids[-2], ("activity",)),
        (turn_ids[-1], ("activity",)),
    ]
    assert all(event.status in {"running", "succeeded"} for event in recovered)


def test_public_turn_event_store_can_filter_without_changing_replay(tmp_path):
    store = InvestigationStore(tmp_path / "investigation.sqlite3")
    session = store.create_session(_report_context())
    turn, _ = store.create_turn(
        session.id,
        client_message_id="client-message:event-filter",
        user_input="读取报告。",
    )
    store.append_public_turn_event(turn.id, stage="accepted")
    emitter = PublicActivityEmitter(store, enabled=lambda: True)
    with emitter.bind_turn(session.id, turn.id):
        emitter.tool_started(session.id, "tool-call:event-filter", "read_report")
        emitter.tool_completed(
            session.id,
            "tool-call:event-filter",
            "read_report",
            {"status": "ok"},
        )

    all_events = store.list_public_turn_events(turn.id)
    activity_events = store.list_public_turn_events(
        turn.id,
        event_types=("activity",),
    )

    assert len(all_events) == 3
    assert [event["event_type"] for event in activity_events] == [
        "activity",
        "activity",
    ]
    assert store.list_public_turn_events(turn.id, event_types=()) == ()
    assert store.list_public_turn_events(
        turn.id,
        after_sequence=activity_events[0]["sequence"],
        event_types=("activity",),
    ) == (activity_events[1],)


def test_creation_tool_boundary_observes_start_before_validation() -> None:
    tools = InvestigationCreationToolService(SimpleNamespace())
    observed: list[tuple[str, str, str]] = []
    tools.set_tool_start_observer(
        lambda session_id, tool_call_id, tool_name: observed.append(
            (session_id, tool_call_id, tool_name)
        )
    )

    result = tools.execute_with_identity(
        "create_investigation_draft",
        {},
        principal=Principal("principal-a"),
        identity=HermesToolExecutionIdentity(
            session_id="investigation-session:creation",
            turn_id="investigation-turn:creation",
            tool_call_id="tool-call:create",
        ),
    )

    assert result["status"] == "error"
    assert observed == [(
        "investigation-session:creation",
        "tool-call:create",
        "create_investigation_draft",
    )]


def test_emitter_is_a_noop_when_the_rollout_switch_is_off(tmp_path):
    store = InvestigationStore(tmp_path / "investigation.sqlite3")
    session = store.create_session(_report_context())
    turn, _ = store.create_turn(
        session.id,
        client_message_id="client-message:disabled-activity",
        user_input="继续。",
    )
    emitter = PublicActivityEmitter(store, enabled=lambda: False)
    assert emitter.agent_callbacks(session.id) == {}
    with emitter.bind_turn(session.id, turn.id):
        emitter.tool_started(session.id, "tool-call:1", "read_report")
    assert store.list_public_turn_events(turn.id) == ()


def test_emitter_suppresses_ambiguous_overlap_without_blocking_execution(tmp_path):
    store = InvestigationStore(tmp_path / "investigation.sqlite3")
    session = store.create_session(_report_context())
    turn, _ = store.create_turn(
        session.id,
        client_message_id="client-message:overlap",
        user_input="继续。",
    )
    emitter = PublicActivityEmitter(store, enabled=lambda: True)
    callbacks = emitter.agent_callbacks(session.id)

    with emitter.bind_turn(session.id, turn.id):
        with emitter.bind_turn(session.id, "another-concurrent-turn"):
            callbacks["tool_start_callback"]("tool-call:ambiguous", "read_report", {})
        callbacks["tool_start_callback"]("tool-call:unambiguous", "read_report", {})

    activities = [
        event
        for event in store.list_public_turn_events(turn.id)
        if event["event_type"] == "activity"
    ]
    assert [event["status"] for event in activities] == [
        "running",
        "interrupted",
    ]


def test_report_and_creation_agents_receive_callbacks_only_when_enabled(
    tmp_path,
    monkeypatch,
):
    captured: list[dict] = []

    class Agent:
        def __init__(self, **options):
            captured.append(options)
            self._api_max_retries = 0

        def close(self):
            return None

    monkeypatch.setattr(settings, "activity_stream_enabled", True)
    report_service = HermesInvestigationAgentService(
        report_facade=SimpleNamespace(),
        store=InvestigationStore(tmp_path / "report.sqlite3"),
        agent_factory=Agent,
        bind_runtime=False,
    )
    report_service._agent("investigation-session:report")
    assert callable(captured[-1]["tool_start_callback"])
    assert callable(captured[-1]["tool_complete_callback"])

    application_service = SimpleNamespace()
    tool_service = SimpleNamespace(application_service=application_service)
    creation_service = InvestigationCreationConversationService(
        tool_service=tool_service,
        store=InvestigationStore(tmp_path / "creation.sqlite3"),
        agent_factory=Agent,
        fake_runtime=False,
    )
    creation_service._agent("investigation-session:creation")
    assert callable(captured[-1]["tool_start_callback"])
    assert callable(captured[-1]["tool_complete_callback"])

    monkeypatch.setattr(settings, "activity_stream_enabled", False)
    disabled_service = HermesInvestigationAgentService(
        report_facade=SimpleNamespace(),
        store=InvestigationStore(tmp_path / "disabled.sqlite3"),
        agent_factory=Agent,
        bind_runtime=False,
    )
    disabled_service._agent("investigation-session:disabled")
    assert "tool_start_callback" not in captured[-1]
    assert "tool_complete_callback" not in captured[-1]


def _report_context() -> PublishedReportContext:
    return PublishedReportContext(
        task_id="task-public-activity",
        report_id="report:" + "1" * 32,
        report_version_id="report-version:" + "2" * 32,
        version_number=1,
        source_snapshot_id="source-snapshot:public-activity",
        snapshot_hash="3" * 64,
        source_hash="source-public-activity",
        title="公开活动测试报告",
        content_hash="content-public-activity",
        published_at="2026-09-17T00:00:00+00:00",
        finding_ids=(),
        evidence_ids=(),
    )
