from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.contracts import (
    InvestigationActivityEventResponse,
    InvestigationAnswerDeltaEventResponse,
    InvestigationPublicStage,
    InvestigationTurnEventResponse,
    ReportTextBlockResponse,
)
from backend.api.investigation import create_investigation_router
from backend.api.investigation_execution import InvestigationTurnExecutor
from backend.api.reporting import create_reporting_router
from backend.audit_agent.config import settings
from backend.investigation.errors import (
    ConcurrentTurnError,
    InvestigationSessionNotFoundError,
    InvestigationTurnNotFoundError,
)
from backend.investigation.contracts import PublishedReportContext
from backend.investigation.store import InvestigationStore
from backend.reporting.store import ReportStore


class Stage3BPublicContractTest(unittest.TestCase):
    def test_public_contracts_reject_internal_extra_fields(self):
        with self.assertRaises(ValidationError):
            ReportTextBlockResponse(text="公开内容", claim_ids=["report-claim:internal"])

    def test_terminal_event_payload_is_fail_closed(self):
        with self.assertRaises(ValidationError):
            InvestigationTurnEventResponse(
                event_id="event:1",
                turn_id="turn:1",
                sequence=1,
                stage=InvestigationPublicStage.COMPLETED,
                occurred_at="2026-08-11T00:00:00+00:00",
            )
        event = InvestigationTurnEventResponse(
            event_id="event:2",
            turn_id="turn:1",
            sequence=2,
            stage=InvestigationPublicStage.FAILED,
            occurred_at="2026-08-11T00:00:01+00:00",
            safe_message="调查暂时无法完成。",
            retryable=True,
        )
        self.assertEqual(event.safe_message, "调查暂时无法完成。")

    def test_stream_extensions_reject_internal_and_oversized_payloads(self):
        with self.assertRaises(ValidationError):
            InvestigationActivityEventResponse(
                event_id="investigation-stream-event:" + "1" * 32,
                turn_id="investigation-turn:1",
                sequence=1,
                occurred_at="2026-08-11T00:00:00+00:00",
                activity_id="public-activity:" + "2" * 16,
                status="running",
                label="读取报告概览",
                tool_call_id="private-tool-call:1",
            )
        with self.assertRaises(ValidationError):
            InvestigationAnswerDeltaEventResponse(
                event_id="investigation-stream-event:" + "3" * 32,
                turn_id="investigation-turn:1",
                sequence=2,
                occurred_at="2026-08-11T00:00:01+00:00",
                message_id="raw-model-message-id",
                revision=1,
                delta="内部标识不应被接受",
            )
        with self.assertRaises(ValidationError):
            InvestigationAnswerDeltaEventResponse(
                event_id="investigation-stream-event:" + "4" * 32,
                turn_id="investigation-turn:1",
                sequence=3,
                occurred_at="2026-08-11T00:00:02+00:00",
                message_id="public-answer:" + "5" * 16,
                revision=1,
                delta="字" * 4_097,
            )


class InvestigationTurnEventStoreTest(unittest.TestCase):
    def test_events_are_durable_monotonic_and_consecutively_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "investigation.sqlite3"
            store = InvestigationStore(path)
            session = store.create_session(
                PublishedReportContext(
                    task_id="task-stage-3b-events",
                    report_id="report:" + "1" * 32,
                    report_version_id="report-version:" + "2" * 32,
                    version_number=1,
                    source_snapshot_id="source-snapshot:stage-3b-events",
                    snapshot_hash="3" * 64,
                    source_hash="source-stage-3b-events",
                    title="测试报告",
                    content_hash="content-stage-3b-events",
                    published_at="2026-08-11T00:00:00+00:00",
                    finding_ids=(),
                    evidence_ids=(),
                )
            )
            turn, replay = store.create_turn(
                session.id,
                client_message_id="client-message:event-store",
                user_input="这份报告讲什么？",
            )
            self.assertFalse(replay)
            accepted = store.append_public_turn_event(turn.id, stage="accepted")
            duplicate = store.append_public_turn_event(turn.id, stage="accepted")
            planning = store.append_public_turn_event(turn.id, stage="planning")

            self.assertEqual(accepted, duplicate)
            self.assertEqual(planning["sequence"], 2)
            reopened = InvestigationStore(path)
            restored = reopened.list_public_turn_events(
                turn.id, after_sequence=accepted["sequence"]
            )
            self.assertEqual([item["stage"] for item in restored], ["planning"])
            self.assertEqual(
                reopened.get_public_turn_event_sequence(
                    turn.id, planning["event_id"]
                ),
                2,
            )
            self.assertEqual(
                [
                    item["stage"]
                    for item in reopened.list_public_turn_events(turn.id, limit=1)
                ],
                ["accepted"],
            )

            other_session = store.create_session(self._report_context("other-cursor"))
            other_turn, _ = store.create_turn(
                other_session.id,
                client_message_id="client-message:other-cursor",
                user_input="另一个会话的问题。",
            )
            other_event = store.append_public_turn_event(
                other_turn.id, stage="accepted"
            )
            with self.assertRaises(InvestigationTurnNotFoundError):
                reopened.get_public_turn_event_sequence(
                    turn.id, other_event["event_id"]
                )

    def test_stream_extensions_are_durable_ordered_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "investigation.sqlite3"
            store = InvestigationStore(path)
            session = store.create_session(self._report_context("extensions"))
            turn, _ = store.create_turn(
                session.id,
                client_message_id="client-message:stream-extensions",
                user_input="请解释这份报告。",
            )
            accepted = store.append_public_turn_event(turn.id, stage="accepted")
            activity_payload = {
                "activity_id": "public-activity:" + "1" * 16,
                "status": "running",
                "label": "读取报告概览",
                "summary": "正在读取公开报告内容。",
                "result_count": 1,
            }
            activity = store.append_public_stream_event(
                turn.id,
                event_type="activity",
                payload=activity_payload,
                idempotency_key="public-event:" + "2" * 16,
            )
            duplicate = store.append_public_stream_event(
                turn.id,
                event_type="activity",
                payload=activity_payload,
                idempotency_key="public-event:" + "2" * 16,
            )
            first_delta = store.append_public_stream_event(
                turn.id,
                event_type="answer_delta",
                payload={
                    "message_id": "public-answer:" + "3" * 16,
                    "revision": 1,
                    "delta": "相同",
                },
                idempotency_key="public-event:" + "4" * 16,
            )
            second_delta = store.append_public_stream_event(
                turn.id,
                event_type="answer_delta",
                payload={
                    "message_id": "public-answer:" + "3" * 16,
                    "revision": 1,
                    "delta": "相同",
                },
                idempotency_key="public-event:" + "5" * 16,
            )
            reset = store.append_public_stream_event(
                turn.id,
                event_type="answer_reset",
                payload={
                    "message_id": "public-answer:" + "3" * 16,
                    "revision": 2,
                },
                idempotency_key="public-event:" + "6" * 16,
            )
            planning = store.append_public_turn_event(turn.id, stage="planning")

            self.assertEqual(activity, duplicate)
            self.assertEqual(
                [
                    accepted["sequence"],
                    activity["sequence"],
                    first_delta["sequence"],
                    second_delta["sequence"],
                    reset["sequence"],
                    planning["sequence"],
                ],
                [1, 2, 3, 4, 5, 6],
            )
            self.assertEqual(
                [item["event_type"] for item in store.list_public_turn_events(turn.id)],
                [
                    "turn",
                    "activity",
                    "answer_delta",
                    "answer_delta",
                    "answer_reset",
                    "turn",
                ],
            )
            self.assertNotEqual(first_delta["event_id"], second_delta["event_id"])
            self.assertNotIn("idempotency_key", activity)

            with self.assertRaisesRegex(ValueError, "idempotency conflict"):
                store.append_public_stream_event(
                    turn.id,
                    event_type="activity",
                    payload={**activity_payload, "label": "读取其他内容"},
                    idempotency_key="public-event:" + "2" * 16,
                )
            with self.assertRaises(ValidationError):
                store.append_public_stream_event(
                    turn.id,
                    event_type="activity",
                    payload={**activity_payload, "raw_arguments": {"report_id": "private"}},
                    idempotency_key="public-event:" + "7" * 16,
                )

            store.mark_interrupted(
                turn.id,
                error_code="test_interruption",
                safe_message="调查执行已中断。",
                retryable=True,
            )
            self.assertEqual(
                store.append_public_stream_event(
                    turn.id,
                    event_type="activity",
                    payload=activity_payload,
                    idempotency_key="public-event:" + "2" * 16,
                ),
                activity,
            )
            with self.assertRaisesRegex(ValueError, "running Turn"):
                store.append_public_stream_event(
                    turn.id,
                    event_type="activity",
                    payload=activity_payload,
                    idempotency_key="public-event:" + "a" * 16,
                )

            reopened = InvestigationStore(path)
            self.assertEqual(
                reopened.list_public_turn_events(
                    turn.id, after_sequence=activity["sequence"]
                )[0]["delta"],
                "相同",
            )

    def test_existing_event_table_is_migrated_without_losing_replay(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "investigation.sqlite3"
            store = InvestigationStore(path)
            session = store.create_session(self._report_context("migration"))
            turn, _ = store.create_turn(
                session.id,
                client_message_id="client-message:stream-migration",
                user_input="继续调查。",
            )
            accepted = store.append_public_turn_event(turn.id, stage="accepted")

            with sqlite3.connect(path) as connection:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    """
                    CREATE TABLE legacy_public_turn_events (
                        event_id TEXT PRIMARY KEY,
                        turn_id TEXT NOT NULL,
                        sequence INTEGER NOT NULL,
                        stage TEXT NOT NULL,
                        answer TEXT NOT NULL DEFAULT '',
                        safe_message TEXT NOT NULL DEFAULT '',
                        retryable INTEGER NOT NULL DEFAULT 0,
                        artifact_json TEXT NOT NULL DEFAULT '{}',
                        occurred_at TEXT NOT NULL,
                        UNIQUE(turn_id, sequence)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO legacy_public_turn_events (
                        event_id, turn_id, sequence, stage, answer, safe_message,
                        retryable, artifact_json, occurred_at
                    )
                    SELECT event_id, turn_id, sequence, stage, answer, safe_message,
                           retryable, artifact_json, occurred_at
                    FROM investigation_public_turn_events
                    """
                )
                connection.execute("DROP TABLE investigation_public_turn_events")
                connection.execute(
                    "ALTER TABLE legacy_public_turn_events "
                    "RENAME TO investigation_public_turn_events"
                )

            migrated = InvestigationStore(path)
            restored = migrated.list_public_turn_events(turn.id)
            self.assertEqual(restored[0]["event_id"], accepted["event_id"])
            self.assertEqual(restored[0]["event_type"], "turn")
            activity = migrated.append_public_stream_event(
                turn.id,
                event_type="activity",
                payload={
                    "activity_id": "public-activity:" + "8" * 16,
                    "status": "succeeded",
                    "label": "读取报告概览",
                    "summary": "已读取公开报告。",
                    "result_count": 1,
                },
                idempotency_key="public-event:" + "9" * 16,
            )
            self.assertEqual(activity["sequence"], 2)

    @staticmethod
    def _report_context(suffix: str) -> PublishedReportContext:
        return PublishedReportContext(
            task_id=f"task-stage-3b-{suffix}",
            report_id="report:" + "a" * 32,
            report_version_id="report-version:" + "b" * 32,
            version_number=1,
            source_snapshot_id=f"source-snapshot:stage-3b-{suffix}",
            snapshot_hash="c" * 64,
            source_hash=f"source-stage-3b-{suffix}",
            title="测试报告",
            content_hash=f"content-stage-3b-{suffix}",
            published_at="2026-08-11T00:00:00+00:00",
            finding_ids=(),
            evidence_ids=(),
        )


class _BlockingAsyncService:
    def __init__(self, store: InvestigationStore):
        self.store = store
        self.started = Event()
        self.release = Event()
        self.execute_count = 0
        self.resume_count = 0
        self.observers = []

    def add_turn_node_observer(self, observer):
        self.observers.append(observer)

    def accept_message(self, session_id: str, *, client_message_id: str, content: str):
        return self.store.create_turn(
            session_id,
            client_message_id=client_message_id,
            user_input=content,
        )

    def execute_turn(self, turn_id: str):
        self.execute_count += 1
        self.started.set()
        self.release.wait(2)
        self.store.mark_interrupted(
            turn_id,
            error_code="test_interruption",
            safe_message="调查执行被中断，可以恢复。",
            retryable=True,
        )

    def accept_resume(self, turn_id: str):
        return self.store.begin_resume(turn_id), False

    def execute_resume(self, turn_id: str):
        self.resume_count += 1
        self.store.mark_interrupted(
            turn_id,
            error_code="test_interruption_again",
            safe_message="调查执行再次被中断。",
            retryable=False,
        )


class InvestigationTurnExecutorTest(unittest.TestCase):
    def test_accept_is_non_blocking_idempotent_and_resume_reuses_the_turn(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            store = InvestigationStore(Path(temp_dir) / "executor.sqlite3")
            session = store.create_session(
                PublishedReportContext(
                    task_id="task-stage-3b-executor",
                    report_id="report:" + "4" * 32,
                    report_version_id="report-version:" + "5" * 32,
                    version_number=1,
                    source_snapshot_id="source-snapshot:stage-3b-executor",
                    snapshot_hash="6" * 64,
                    source_hash="source-stage-3b-executor",
                    title="异步测试报告",
                    content_hash="content-stage-3b-executor",
                    published_at="2026-08-11T00:00:00+00:00",
                    finding_ids=(),
                    evidence_ids=(),
                )
            )
            service = _BlockingAsyncService(store)
            executor = InvestigationTurnExecutor(service)
            try:
                first = executor.accept_turn(
                    session.id,
                    client_message_id="client-message:async",
                    content="有哪些证据？",
                )
                self.assertTrue(service.started.wait(1))
                self.assertEqual(store.get_turn(first.id).status, "running")

                replay = executor.accept_turn(
                    session.id,
                    client_message_id="client-message:async",
                    content="有哪些证据？",
                )
                self.assertEqual(replay.id, first.id)
                self.assertEqual(service.execute_count, 1)

                service.release.set()
                for _ in range(100):
                    current_events = store.list_public_turn_events(first.id)
                    if current_events and current_events[-1]["stage"] == "interrupted":
                        break
                    Event().wait(0.01)
                events = store.list_public_turn_events(first.id)
                self.assertEqual(events[0]["stage"], "accepted")
                self.assertIn("planning", [item["stage"] for item in events])
                self.assertEqual(events[-1]["stage"], "interrupted")

                resumed = executor.resume_turn(first.id)
                self.assertEqual(resumed.id, first.id)
                for _ in range(100):
                    if service.resume_count:
                        break
                    Event().wait(0.01)
                self.assertEqual(service.resume_count, 1)
                for _ in range(100):
                    if store.list_public_turn_events(first.id)[-1]["retryable"] is False:
                        break
                    Event().wait(0.01)
                self.assertEqual(
                    [item["stage"] for item in store.list_public_turn_events(first.id)][-3:],
                    ["accepted", "planning", "interrupted"],
                )
            finally:
                service.release.set()
                executor.shutdown(wait=True)


class _InvestigationApiStoreStub:
    def __init__(self):
        self.requested_limits: list[int | None] = []
        self.turn = SimpleNamespace(
            id="investigation-turn:1",
            session_id="investigation-session:1",
            assistant_message_id="investigation-message:2",
            status="completed",
            current_node="persist_turn",
            safe_message="",
            retryable=False,
            created_at="2026-08-11T00:00:01+00:00",
            started_at="2026-08-11T00:00:01+00:00",
            completed_at="2026-08-11T00:00:02+00:00",
        )
        self.events = (
            {
                "event_id": "investigation-turn-event:accepted",
                "turn_id": self.turn.id,
                "sequence": 1,
                "stage": "accepted",
                "answer": "",
                "safe_message": "",
                "retryable": False,
                "occurred_at": "2026-08-11T00:00:01+00:00",
            },
            {
                "event_id": "investigation-turn-event:completed",
                "turn_id": self.turn.id,
                "sequence": 2,
                "stage": "completed",
                "answer": "这份报告聚焦博彩内容风险。",
                "safe_message": "",
                "retryable": False,
                "occurred_at": "2026-08-11T00:00:02+00:00",
            },
        )

    def get_turn(self, turn_id: str):
        if turn_id != self.turn.id:
            raise InvestigationSessionNotFoundError(turn_id)
        return self.turn

    def turn_result(self, turn_id: str):
        self.get_turn(turn_id)
        return SimpleNamespace(
            session_id=self.turn.session_id,
            turn_id=self.turn.id,
            status="completed",
            answer="这份报告聚焦博彩内容风险。",
            tool_calls=(SimpleNamespace(name="read_report_presentation"),),
            total_tokens=999,
        )

    def list_public_turn_events(
        self,
        turn_id: str,
        *,
        after_sequence: int = 0,
        event_types: tuple[str, ...] | None = None,
        limit: int | None = None,
    ):
        self.get_turn(turn_id)
        self.requested_limits.append(limit)
        events = tuple(
            item
            for item in self.events
            if item["sequence"] > after_sequence
            and (
                event_types is None
                or str(item.get("event_type") or "turn") in event_types
            )
        )
        return events[:limit] if limit is not None else events

    def get_public_turn_event_sequence(self, turn_id: str, event_id: str):
        self.get_turn(turn_id)
        for item in self.events:
            if item["event_id"] == event_id:
                return item["sequence"]
        raise InvestigationSessionNotFoundError(event_id)

    def get_latest_public_turn_event_sequence(self, turn_id: str):
        self.get_turn(turn_id)
        return max((int(item["sequence"]) for item in self.events), default=0)


class _InvestigationApiServiceStub:
    def __init__(self):
        self.store = _InvestigationApiStoreStub()

    def create_session(self, report_version_id: str):
        return SimpleNamespace(
            id="investigation-session:1",
            report_version_id=report_version_id,
            status="active",
            created_at="2026-08-11T00:00:00+00:00",
            updated_at="2026-08-11T00:00:00+00:00",
        )

    def get_messages(self, session_id: str, *, include_tool_messages: bool):
        if session_id != "investigation-session:1":
            raise InvestigationSessionNotFoundError(session_id)
        if include_tool_messages:
            raise AssertionError("public API must not request tool messages")
        return (
            SimpleNamespace(
                id="investigation-message:1",
                turn_id="investigation-turn:1",
                role="user",
                content="这份报告讲什么？",
                sequence=1,
                created_at="2026-08-11T00:00:01+00:00",
                metadata={"private": True},
            ),
            SimpleNamespace(
                id="investigation-message:internal",
                turn_id="investigation-turn:1",
                role="assistant",
                content='<case_selection>{"selected_case_ref":"internal"}</case_selection>',
                sequence=2,
                created_at="2026-08-11T00:00:01+00:00",
                metadata={"private": True},
            ),
            SimpleNamespace(
                id="investigation-message:2",
                turn_id="investigation-turn:1",
                role="assistant",
                content="这份报告聚焦博彩内容风险。",
                sequence=3,
                created_at="2026-08-11T00:00:02+00:00",
                metadata={"tool_calls": ["private"]},
            ),
        )


class _InvestigationApiExecutorStub:
    def __init__(self, service: _InvestigationApiServiceStub):
        self.service = service
        self.raise_concurrent = False

    def accept_turn(
        self, session_id: str, *, client_message_id: str, content: str
    ):
        if self.raise_concurrent:
            raise ConcurrentTurnError()
        self.last_accept = (session_id, client_message_id, content)
        return self.service.store.turn

    def resume_turn(self, turn_id: str):
        self.last_resume = turn_id
        return self.service.store.turn


class InvestigationSessionApiTest(unittest.TestCase):
    def setUp(self):
        self.service = _InvestigationApiServiceStub()
        self.executor = _InvestigationApiExecutorStub(self.service)
        app = FastAPI()
        app.include_router(create_investigation_router(self.service, self.executor))
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_create_session_and_accept_turn_expose_only_public_fields(self):
        session_response = self.client.post(
            "/api/report-versions/report-version:1/investigation-sessions"
        )
        self.assertEqual(session_response.status_code, 200)
        self.assertEqual(
            session_response.json()["session_id"], "investigation-session:1"
        )

        turn_response = self.client.post(
            "/api/investigation-sessions/investigation-session:1/turns",
            json={
                "client_message_id": "client-message:1",
                "content": "这份报告讲什么？",
            },
        )
        self.assertEqual(turn_response.status_code, 202)
        payload = turn_response.json()
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["turn_id"], "investigation-turn:1")
        self.assertNotIn("stage", payload)
        self.assertNotIn("answer", payload)
        self.assertNotIn("tool_calls", turn_response.text)
        self.assertNotIn("total_tokens", turn_response.text)
        self.assertEqual(
            self.executor.last_accept,
            (
                "investigation-session:1",
                "client-message:1",
                "这份报告讲什么？",
            ),
        )

        terminal = self.client.get("/api/investigation-turns/investigation-turn:1")
        self.assertEqual(terminal.status_code, 200)
        self.assertEqual(terminal.json()["status"], "completed")
        self.assertEqual(
            terminal.json()["answer"], "这份报告聚焦博彩内容风险。"
        )

    def test_sse_replays_after_last_event_id_without_internal_state(self):
        response = self.client.get(
            "/api/investigation-turns/investigation-turn:1/events",
            headers={"Last-Event-ID": "investigation-turn-event:accepted"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("investigation-turn-event:accepted\n", response.text)
        self.assertIn("id: investigation-turn-event:completed", response.text)
        self.assertIn('"stage":"completed"', response.text)
        self.assertNotIn("tool_calls", response.text)
        self.assertNotIn("current_node", response.text)

    def test_sse_interleaves_typed_extensions_and_resumes_after_one(self):
        activity_id = "investigation-stream-event:activity"
        self.service.store.events = (
            {**self.service.store.events[0], "event_type": "turn"},
            {
                "event_id": activity_id,
                "turn_id": self.service.store.turn.id,
                "sequence": 2,
                "event_type": "activity",
                "activity_id": "public-activity:" + "1" * 16,
                "status": "succeeded",
                "label": "读取报告概览",
                "summary": "已读取公开报告。",
                "result_count": 1,
                "occurred_at": "2026-08-11T00:00:01+00:00",
            },
            {
                "event_id": "investigation-stream-event:delta-1",
                "turn_id": self.service.store.turn.id,
                "sequence": 3,
                "event_type": "answer_delta",
                "message_id": "public-answer:" + "2" * 16,
                "revision": 1,
                "delta": "这份",
                "occurred_at": "2026-08-11T00:00:01+00:00",
            },
            {
                "event_id": "investigation-stream-event:reset",
                "turn_id": self.service.store.turn.id,
                "sequence": 4,
                "event_type": "answer_reset",
                "message_id": "public-answer:" + "2" * 16,
                "revision": 2,
                "occurred_at": "2026-08-11T00:00:01+00:00",
            },
            {
                **self.service.store.events[1],
                "sequence": 5,
                "event_type": "turn",
            },
        )

        response = self.client.get(
            "/api/investigation-turns/investigation-turn:1/events"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: activity", response.text)
        self.assertIn("event: answer_delta", response.text)
        self.assertIn("event: answer_reset", response.text)
        self.assertLess(response.text.index("event: activity"), response.text.index("event: answer_delta"))
        self.assertLess(response.text.index("event: answer_reset"), response.text.index('"stage":"completed"'))
        self.assertNotIn("idempotency_key", response.text)
        self.assertNotIn("tool_call_id", response.text)

        replay = self.client.get(
            "/api/investigation-turns/investigation-turn:1/events",
            headers={"Last-Event-ID": activity_id},
        )
        self.assertNotIn("event: activity", replay.text)
        self.assertIn("event: answer_delta", replay.text)
        self.assertIn('"stage":"completed"', replay.text)

    def test_sse_replays_large_history_in_bounded_ordered_batches(self):
        self.service.store.events = (
            {**self.service.store.events[0], "event_type": "turn"},
            {
                "event_id": "investigation-stream-event:activity-batch",
                "turn_id": self.service.store.turn.id,
                "sequence": 2,
                "event_type": "activity",
                "activity_id": "public-activity:" + "4" * 16,
                "status": "succeeded",
                "label": "读取报告概览",
                "summary": "已读取公开报告。",
                "result_count": 1,
                "occurred_at": "2026-08-11T00:00:01+00:00",
            },
            {
                "event_id": "investigation-stream-event:delta-batch-1",
                "turn_id": self.service.store.turn.id,
                "sequence": 3,
                "event_type": "answer_delta",
                "message_id": "public-answer:" + "5" * 16,
                "revision": 1,
                "delta": "第一段",
                "occurred_at": "2026-08-11T00:00:02+00:00",
            },
            {
                "event_id": "investigation-stream-event:delta-batch-2",
                "turn_id": self.service.store.turn.id,
                "sequence": 4,
                "event_type": "answer_delta",
                "message_id": "public-answer:" + "5" * 16,
                "revision": 1,
                "delta": "第二段",
                "occurred_at": "2026-08-11T00:00:03+00:00",
            },
            {
                **self.service.store.events[1],
                "sequence": 5,
                "event_type": "turn",
            },
        )

        with patch.object(settings, "stream_replay_batch_size", 2):
            response = self.client.get(
                "/api/investigation-turns/investigation-turn:1/events"
            )

        self.assertEqual(response.status_code, 200)
        event_ids = [
            item[4:]
            for item in response.text.splitlines()
            if item.startswith("id: ")
        ]
        self.assertEqual(
            event_ids,
            [item["event_id"] for item in self.service.store.events],
        )
        self.assertEqual(self.service.store.requested_limits, [2, 2, 2])

    def test_status_projection_ignores_display_only_extensions(self):
        self.service.store.turn.status = "running"
        self.service.store.turn.current_node = "prepare_context"
        self.service.store.events = (
            {**self.service.store.events[0], "event_type": "turn"},
            {
                "event_id": "investigation-stream-event:activity",
                "turn_id": self.service.store.turn.id,
                "sequence": 2,
                "event_type": "activity",
                "activity_id": "public-activity:" + "3" * 16,
                "status": "running",
                "label": "读取报告概览",
                "summary": "",
                "result_count": None,
                "occurred_at": "2026-08-11T00:00:02+00:00",
            },
        )
        response = self.client.get("/api/investigation-turns/investigation-turn:1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["stage"], "accepted")
        self.assertEqual(
            response.json()["updated_at"], "2026-08-11T00:00:01+00:00"
        )

    def test_sse_replay_does_not_stop_at_a_previous_resume_attempt(self):
        self.service.store.turn.status = "interrupted"
        self.service.store.turn.safe_message = "第二次执行中断。"
        self.service.store.turn.retryable = False
        self.service.store.events = (
            self.service.store.events[0],
            {
                "event_id": "investigation-turn-event:first-interrupted",
                "turn_id": self.service.store.turn.id,
                "sequence": 2,
                "stage": "interrupted",
                "answer": "",
                "safe_message": "第一次执行中断。",
                "retryable": True,
                "occurred_at": "2026-08-11T00:00:02+00:00",
            },
            {
                "event_id": "investigation-turn-event:resume-accepted",
                "turn_id": self.service.store.turn.id,
                "sequence": 3,
                "stage": "accepted",
                "answer": "",
                "safe_message": "",
                "retryable": False,
                "occurred_at": "2026-08-11T00:00:03+00:00",
            },
            {
                "event_id": "investigation-turn-event:resume-interrupted",
                "turn_id": self.service.store.turn.id,
                "sequence": 4,
                "stage": "interrupted",
                "answer": "",
                "safe_message": "第二次执行中断。",
                "retryable": False,
                "occurred_at": "2026-08-11T00:00:04+00:00",
            },
        )
        with patch.object(settings, "stream_replay_batch_size", 2):
            response = self.client.get(
                "/api/investigation-turns/investigation-turn:1/events"
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("investigation-turn-event:first-interrupted", response.text)
        self.assertIn("investigation-turn-event:resume-accepted", response.text)
        self.assertIn("investigation-turn-event:resume-interrupted", response.text)
        self.assertLess(
            response.text.index("first-interrupted"),
            response.text.index("resume-accepted"),
        )

    def test_resume_returns_the_same_durable_turn(self):
        response = self.client.post(
            "/api/investigation-turns/investigation-turn:1/resume"
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["turn_id"], "investigation-turn:1")
        self.assertEqual(self.executor.last_resume, "investigation-turn:1")

    def test_message_history_excludes_tool_and_internal_metadata(self):
        response = self.client.get(
            "/api/investigation-sessions/investigation-session:1/messages"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["role"] for item in response.json()], ["user", "assistant"]
        )
        self.assertNotIn("metadata", response.text)
        self.assertNotIn("tool_calls", response.text)
        self.assertNotIn("case_selection", response.text)

    def test_public_errors_are_stable_and_fail_closed(self):
        missing = self.client.get(
            "/api/investigation-sessions/investigation-session:missing/messages"
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"], "调查会话不存在。")

        self.executor.raise_concurrent = True
        concurrent = self.client.post(
            "/api/investigation-sessions/investigation-session:1/turns",
            json={"client_message_id": "client-message:2", "content": "继续"},
        )
        self.assertEqual(concurrent.status_code, 409)
        self.assertEqual(
            concurrent.json()["detail"], "当前会话已有一轮对话正在执行。"
        )


class PublishedReportApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ReportStore(Path(self.temp_dir.name) / "report-api.sqlite3")
        self.task_id = "task-stage-3b"
        self.first = self._publish("第一版调查报告")
        self.second = self._publish("第二版调查报告")
        self.draft = self.store.create_generation(
            self.task_id,
            model="deterministic-test",
            prompt_version="report-api-test-v1",
        )
        app = FastAPI()
        app.include_router(create_reporting_router(self.store))
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def _publish(self, title: str) -> dict:
        generation = self.store.create_generation(
            self.task_id,
            model="deterministic-test",
            prompt_version="report-api-test-v1",
        )
        version_id = generation["report_version_id"]
        self.store.save_source_snapshot(
            {
                "snapshot_id": f"report-source-snapshot:{version_id.rsplit(':', 1)[-1]}",
                "report_version_id": version_id,
                "task_id": self.task_id,
                "task_status": "completed",
                "source_hash": f"source:{version_id}",
                "configuration_revision_id": "",
                "finding_ids": [],
                "evidence_ids": [],
                "data_quality_warnings": [],
                "statistic_inputs": [],
                "generated_at": "2026-08-11T00:00:00+00:00",
                "snapshot_hash": f"snapshot:{version_id}",
            }
        )
        human_report = {
            "presentation_version": "human-report-v1",
            "title": title,
            "summary": {"text": "调查摘要", "claim_ids": []},
            "key_metrics": [
                {
                    "label": "风险内容",
                    "value": "2",
                    "detail": "共识别两条",
                    "metric_refs": ["metric:internal"],
                }
            ],
            "sections": [
                {
                    "section_id": "risk",
                    "title": "风险特征",
                    "paragraphs": [{"text": "风险特征正文", "claim_ids": []}],
                }
            ],
            "case_blocks": [
                {
                    "title": "重点案例",
                    "text": "案例正文",
                    "claim_ids": [],
                    "citation_actions": [],
                }
            ],
            "conclusion": {"text": "调查结论", "claim_ids": []},
            "data_quality_note": {"text": "数据说明", "claim_ids": []},
        }
        return self.store.publish_version(
            report_version_id=version_id,
            title=title,
            body_markdown=f"# {title}",
            body_json={"human_report": human_report, "audit_model": {"private": True}},
            sections=[],
            citation_details={},
        )

    def test_list_only_returns_published_versions_newest_first(self):
        response = self.client.get(f"/api/tasks/{self.task_id}/report-versions")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [item["report_version_id"] for item in payload["items"]],
            [self.second["id"], self.first["id"]],
        )
        self.assertEqual(payload["latest_report_version_id"], self.second["id"])
        serialized = response.text
        self.assertNotIn("metric:internal", serialized)
        self.assertNotIn("audit_model", serialized)

    def test_detail_returns_only_human_presentation(self):
        response = self.client.get(f"/api/report-versions/{self.second['id']}")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["task_id"], self.task_id)
        self.assertEqual(payload["title"], "第二版调查报告")
        self.assertEqual(payload["presentation"]["summary"]["text"], "调查摘要")
        self.assertEqual(payload["presentation"]["key_metrics"][0]["value"], "2")
        self.assertNotIn("metric_refs", response.text)
        self.assertNotIn("claim_ids", response.text)
        self.assertNotIn("audit_model", response.text)

    def test_draft_and_missing_versions_are_not_public(self):
        draft_response = self.client.get(
            f"/api/report-versions/{self.draft['report_version_id']}"
        )
        missing_response = self.client.get("/api/report-versions/report-version:missing")
        empty_list = self.client.get("/api/tasks/task-without-report/report-versions")
        self.assertEqual(draft_response.status_code, 404)
        self.assertEqual(missing_response.status_code, 404)
        self.assertEqual(empty_list.status_code, 200)
        self.assertEqual(empty_list.json()["items"], [])


if __name__ == "__main__":
    unittest.main()
