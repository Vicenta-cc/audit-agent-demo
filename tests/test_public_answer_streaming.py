from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.api.investigation import public_answer_draft_for_turn
from backend.audit_agent.config import settings
from backend.hermes_runtime.service import (
    HermesInvestigationAgentService,
    redact_internal_account_references,
)
from backend.investigation.contracts import PublishedReportContext
from backend.investigation.public_answer import PublicAnswerStreamer
from backend.investigation.store import InvestigationStore


def _context() -> PublishedReportContext:
    return PublishedReportContext(
        task_id="task-public-answer",
        report_id="report:" + "1" * 32,
        report_version_id="report-version:" + "2" * 32,
        version_number=1,
        source_snapshot_id="source-snapshot:public-answer",
        snapshot_hash="3" * 64,
        source_hash="source-public-answer",
        title="公开回答流测试报告",
        content_hash="content-public-answer",
        published_at="2026-09-17T00:00:00+00:00",
        finding_ids=(),
        evidence_ids=(),
    )


def _running_turn(store: InvestigationStore):
    session = store.create_session(_context())
    turn, _ = store.create_turn(
        session.id,
        client_message_id="public-answer-test",
        user_input="请回答",
    )
    return session, turn


def _answer_events(store: InvestigationStore, turn_id: str):
    return [
        event
        for event in store.list_public_turn_events(turn_id)
        if event["event_type"] in {"answer_delta", "answer_reset"}
    ]


def _latest_revision_text(events: list[dict]) -> str:
    revision = 0
    text = ""
    for event in events:
        if event["revision"] < revision:
            continue
        if event["event_type"] == "answer_reset":
            revision = event["revision"]
            text = ""
        else:
            if event["revision"] > revision:
                revision = event["revision"]
                text = ""
            text += event["delta"]
    return text


def test_streamer_buffers_sanitizes_and_reconciles_final_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "answer_stream_enabled", True)
    store = InvestigationStore(tmp_path / "answer.sqlite3")
    session, turn = _running_turn(store)
    enabled = True
    streamer = PublicAnswerStreamer(
        store,
        enabled=lambda: enabled,
        sanitize=redact_internal_account_references,
    )
    leaked = (
        "这是经过报告证据核验的公开结论。" * 12
        + "内部值 account_ref=account8_34088f 不应显示。"
        + "后续公开说明。" * 16
    )

    with streamer.bind_turn(session.id, turn.id):
        streamer.begin_iteration(session.id, 1)
        for offset in range(0, len(leaked), 7):
            streamer.stream_delta(session.id, leaked[offset : offset + 7])
        before_final = _answer_events(store, turn.id)
        assert before_final
        assert len(before_final) < len(leaked) // 7
        assert "account8_34088f" not in str(before_final)
        streamer.finalize(turn.id, leaked)

    events = _answer_events(store, turn.id)
    expected = redact_internal_account_references(leaked)[0]
    assert _latest_revision_text(events) == expected
    assert "account_ref" not in str(events)
    draft = public_answer_draft_for_turn(store, turn.id)
    assert draft is not None
    assert draft.text == expected
    enabled = False
    monkeypatch.setattr(settings, "answer_stream_enabled", False)
    assert public_answer_draft_for_turn(store, turn.id) is None


def test_tool_boundary_and_process_resume_start_new_revisions(tmp_path):
    store = InvestigationStore(tmp_path / "revisions.sqlite3")
    session, turn = _running_turn(store)
    streamer = PublicAnswerStreamer(
        store,
        enabled=lambda: True,
        sanitize=redact_internal_account_references,
    )
    first = "第一版回答。" * 30
    second = "工具查询后的最终回答。" * 24

    with streamer.bind_turn(session.id, turn.id):
        streamer.begin_iteration(session.id, 1)
        streamer.stream_delta(session.id, first)
        streamer.discard_draft_for_session(session.id)
        streamer.begin_iteration(session.id, 2)
        streamer.stream_delta(session.id, second)

    resumed = PublicAnswerStreamer(
        store,
        enabled=lambda: True,
        sanitize=redact_internal_account_references,
    )
    with resumed.bind_turn(session.id, turn.id):
        resumed.begin_iteration(session.id, 1)
        resumed.stream_delta(session.id, second)
        resumed.finalize(turn.id, second)

    events = _answer_events(store, turn.id)
    resets = [event for event in events if event["event_type"] == "answer_reset"]
    assert len(resets) >= 2
    assert [event["revision"] for event in resets] == sorted(
        event["revision"] for event in resets
    )
    assert _latest_revision_text(events) == second


def test_completed_short_sentence_streams_without_waiting_for_holdback(tmp_path):
    store = InvestigationStore(tmp_path / "short-sentence.sqlite3")
    session, turn = _running_turn(store)
    streamer = PublicAnswerStreamer(
        store,
        enabled=lambda: True,
        sanitize=redact_internal_account_references,
    )

    with streamer.bind_turn(session.id, turn.id):
        streamer.stream_delta(session.id, "先给出一句短结论。")
        assert _latest_revision_text(_answer_events(store, turn.id)) == "先给出一句短结论。"
        streamer.stream_delta(session.id, "内部值 account_ref=account8_")
        assert "account8_" not in str(_answer_events(store, turn.id))
        streamer.stream_delta(session.id, "34088f 已清理。")

    events = _answer_events(store, turn.id)
    assert "account8_34088f" not in str(events)
    assert "内部账号引用已隐藏" in _latest_revision_text(events)


def test_disabled_streamer_exposes_no_callbacks_or_events(tmp_path):
    store = InvestigationStore(tmp_path / "disabled.sqlite3")
    session, turn = _running_turn(store)
    streamer = PublicAnswerStreamer(
        store,
        enabled=lambda: False,
        sanitize=redact_internal_account_references,
    )
    assert streamer.agent_callbacks(session.id) == {}
    with streamer.bind_turn(session.id, turn.id):
        streamer.stream_delta(session.id, "不会公开")
        streamer.finalize(turn.id, "不会公开")
    assert _answer_events(store, turn.id) == []


def test_report_service_composes_activity_and_answer_callbacks(tmp_path, monkeypatch):
    captured: list[dict] = []
    answer = "这是一段足够长的最终回答。" * 28

    class Agent:
        _api_max_retries = 0

        def __init__(self, **options):
            captured.append(options)
            self.options = options

        def close(self):
            return None

        def run_conversation(self, message, **kwargs):
            self.options["step_callback"](1, [])
            self.options["stream_delta_callback"](answer[:180])
            self.options["tool_start_callback"](
                "private-tool-call", "read_report", {"private": "argument"}
            )
            self.options["tool_complete_callback"](
                "private-tool-call", "read_report", {}, {"status": "ok"}
            )
            self.options["step_callback"](2, ["read_report"])
            self.options["stream_delta_callback"](answer)
            history = [dict(item) for item in kwargs.get("conversation_history") or []]
            return {
                "completed": True,
                "failed": False,
                "final_response": answer,
                "messages": [
                    *history,
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": answer},
                ],
            }

    monkeypatch.setattr(settings, "activity_stream_enabled", True)
    monkeypatch.setattr(settings, "answer_stream_enabled", True)
    context = _context()
    facade = SimpleNamespace(
        get_published_report_context=lambda _report_version_id: context,
    )
    store = InvestigationStore(tmp_path / "service.sqlite3")
    service = HermesInvestigationAgentService(
        report_facade=facade,
        store=store,
        agent_factory=Agent,
        bind_runtime=False,
    )
    session = service.create_session(context.report_version_id)
    turn, _ = service.accept_message(
        session.id,
        client_message_id="composed-callbacks",
        content="请读取报告后回答",
    )
    result = service.execute_turn(turn.id)

    assert result.answer == answer
    assert callable(captured[0]["tool_start_callback"])
    events = store.list_public_turn_events(turn.id)
    assert any(event["event_type"] == "activity" for event in events)
    assert any(event["event_type"] == "answer_delta" for event in events)
    assert _latest_revision_text(_answer_events(store, turn.id)) == answer
    serialized = str(events)
    assert "private-tool-call" not in serialized
    assert "argument" not in serialized


def test_interrupted_turn_resets_draft_before_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "activity_stream_enabled", False)
    monkeypatch.setattr(settings, "answer_stream_enabled", True)
    context = _context()
    final_answer = "恢复后生成的权威答案。"

    class Agent:
        _api_max_retries = 0

        def __init__(self, **options):
            self.options = options
            self.calls = 0

        def close(self):
            return None

        def run_conversation(self, message, **kwargs):
            self.calls += 1
            self.options["step_callback"](1, [])
            if self.calls == 1:
                self.options["stream_delta_callback"]("中断前的临时回答。")
                return {"interrupted": True}
            self.options["stream_delta_callback"](final_answer)
            history = [dict(item) for item in kwargs.get("conversation_history") or []]
            return {
                "completed": True,
                "failed": False,
                "final_response": final_answer,
                "messages": [
                    *history,
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": final_answer},
                ],
            }

    facade = SimpleNamespace(
        get_published_report_context=lambda _report_version_id: context,
    )
    store = InvestigationStore(tmp_path / "resume.sqlite3")
    service = HermesInvestigationAgentService(
        report_facade=facade,
        store=store,
        agent_factory=Agent,
        bind_runtime=False,
    )
    session = service.create_session(context.report_version_id)
    turn, _ = service.accept_message(
        session.id,
        client_message_id="resume-answer",
        content="请回答",
    )

    with pytest.raises(RuntimeError, match="interrupted"):
        service.execute_turn(turn.id)
    first_events = _answer_events(store, turn.id)
    assert first_events[-1]["event_type"] == "answer_reset"
    assert _latest_revision_text(first_events) == ""

    resumed_turn, replay = service.accept_resume(turn.id)
    assert replay is False
    assert resumed_turn.status == "running"
    result = service.execute_resume(turn.id)
    assert result.answer == final_answer
    assert _latest_revision_text(_answer_events(store, turn.id)) == final_answer
