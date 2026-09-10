from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import yaml

from backend.api.investigation import create_investigation_router
from backend.api.investigation_execution import InvestigationTurnExecutor
from backend.hermes_runtime.adapter import HermesRuntimeBinding
from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.investigation.contracts import PublishedReportContext
from backend.investigation.errors import ConcurrentTurnError, ToolProtocolError
from backend.investigation.protocol import validate_hermes_transcript_messages
from backend.investigation.store import InvestigationStore
from hermes_m0.plugin import _handler, _idempotent_tool_execution
from hermes_m0.runtime import (
    bind_report_task_session,
    release_all_report_task_sessions,
    report_runtime_binding_for_session,
)
from hermes_m0.schemas import M2_ACCOUNT_ACTIVITY_TOOLS
from hermes_m0.tool_results import error_result


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_PROMPT_FILE_SHA256 = (
    "3bd437c4bcd970fbb31737a4ad3059bb0f12395a03af10c50ddf42e696719cd3"
)
CANONICAL_INJECTED_PROMPT_SHA256 = (
    "7faa88a1a771cc02343f29126d96a65f7a8fec60732dcb83253cf96d2000bdd9"
)
# M1 intentionally adapts user-facing descriptions. This fence covers only the
# independently extracted canonical name + parameters projection.
CANONICAL_TOOL_PARAMETER_REFERENCE_SHA256 = (
    "155ef8e4792d4be942ab0234603ffe9c38297fe224f05ea4ff7ee9e46818b238"
)
CANONICAL_TRANSCRIPT_PROVENANCE = {
    "source_commit": "0a5090579c4cfcda1208269814f32fbe77da4c86",
    "hermes_version": "0.20.4",
    "accepted_raw_sha256": (
        "e61356c193cbbd7eccb1d52112bd2832c29d4fdd110f1d0c6eb5bbfd83354c56"
    ),
    "multiple_call_contract": "Hermes 0.20.4 chat-completions tool protocol",
}
CANONICAL_SCHEMA_SOURCE_ORDER = (
    "read_report",
    "list_finding_posts",
    "search_posts",
    "read_posts",
    "list_post_risk_comments",
    "list_evidence",
    "read_evidence",
    "get_account_overview",
    "list_account_occurrences",
    "read_account_occurrence",
    "read_account_post",
)
CANONICAL_QWEN_TOOL_ORDER = tuple(sorted(CANONICAL_SCHEMA_SOURCE_ORDER))
CANONICAL_TOOL_PARAMETER_PROJECTION_PATH = (
    ROOT / "tests/fixtures/canonical_m22_tool_parameter_projection.json"
)


def _parameter_projection(tools: list[dict]) -> list[list[object]]:
    return [[item["name"], item["parameters"]] for item in tools]


def _fixed_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_parameter_projection() -> list[list[object]]:
    return json.loads(
        CANONICAL_TOOL_PARAMETER_PROJECTION_PATH.read_text(encoding="utf-8")
    )


def _canonical_tool_call(call_id: str, name: str = "read_report") -> dict:
    return {
        "id": call_id,
        "call_id": call_id,
        "response_item_id": None,
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def _canonical_tool_result(call_id: str, name: str = "read_report") -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "tool_name": name,
        "name": name,
        "content": "{}",
    }


def _context(label: str) -> PublishedReportContext:
    token = "1" if label == "a" else "2"
    return PublishedReportContext(
        task_id=f"task-{label}",
        report_id="report:" + token * 32,
        report_version_id="report-version:" + token * 32,
        version_number=1,
        source_snapshot_id=f"source-snapshot:{label}",
        snapshot_hash=token * 64,
        source_hash=f"source-{label}",
        title=f"Report {label.upper()}",
        content_hash=f"content-{label}",
        published_at="2026-08-27T00:00:00+00:00",
        finding_ids=(),
        evidence_ids=(),
    )


class _MultiReportFacade:
    db_path = Path(__file__)

    def __init__(self) -> None:
        self.contexts = {
            _context("a").report_version_id: _context("a"),
            _context("b").report_version_id: _context("b"),
        }

    def get_published_report_context(self, report_version_id: str):
        return self.contexts[report_version_id]


class _ConcurrentAgent:
    def __init__(self, options, barrier: Barrier, *, fail: bool = False):
        self.options = options
        self.barrier = barrier
        self.fail = fail
        self._api_max_retries = 3
        self.execution_count = 0
        self.calls = []

    def run_conversation(self, message, **kwargs):
        self.execution_count += 1
        self.calls.append((message, kwargs))
        self.barrier.wait(timeout=2)
        if self.fail:
            raise RuntimeError("injected unknown outcome")
        history = [dict(item) for item in kwargs.get("conversation_history") or []]
        answer = f"answer:{self.options['session_id']}"
        tool_call_id = (
            f"call:{self.options['session_id']}:{self.execution_count}"
        )
        return {
            "final_response": answer,
            "messages": [
                *history,
                {"role": "user", "content": message},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": tool_call_id,
                            "function": {
                                "name": "read_report",
                                "arguments": {},
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": "read_report",
                    "content": json.dumps(
                        {"private_report": self.options["session_id"]}
                    ),
                },
                {"role": "assistant", "content": answer},
            ],
            "api_calls": 1,
            "completed": True,
            "failed": False,
            "interrupted": False,
            "turn_exit_reason": "text_response",
        }

    def close(self):
        return None


class _ExecutorPathAgent:
    def __init__(self, options, *, fail: bool = False):
        self.options = options
        self.fail = fail
        self._api_max_retries = 3
        self.execution_count = 0
        self.tool_result = None

    def run_conversation(self, message, **kwargs):
        self.execution_count += 1
        session_id = self.options["session_id"]
        tool_call_id = f"call:{session_id}"
        handler = _handler("read_report")
        raw_result = _idempotent_tool_execution(
            tool_name="read_report",
            args={},
            session_id=session_id,
            task_id=kwargs["task_id"],
            tool_call_id=tool_call_id,
            next_call=lambda args: handler(
                args,
                session_id=session_id,
                task_id=kwargs["task_id"],
            ),
        )
        self.tool_result = json.loads(raw_result)
        if self.fail:
            raise RuntimeError("injected executor-path failure")
        history = [dict(item) for item in kwargs.get("conversation_history") or []]
        answer = f"answer:{self.tool_result['report']}"
        return {
            "final_response": answer,
            "messages": [
                *history,
                {"role": "user", "content": message},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": tool_call_id,
                            "type": "function",
                            "function": {"name": "read_report", "arguments": {}},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": "read_report",
                    "content": raw_result,
                },
                {"role": "assistant", "content": answer},
            ],
            "api_calls": 1,
            "completed": True,
            "failed": False,
            "interrupted": False,
            "turn_exit_reason": "text_response",
        }

    def close(self):
        return None


class _ObservedExecutor(InvestigationTurnExecutor):
    def __init__(self, service, *, max_workers: int):
        self.terminal_turns = set()
        self.terminal_event = Event()
        self.terminal_lock = Lock()
        super().__init__(service, max_workers=max_workers)

    def _append_terminal_event(self, turn_id: str) -> None:
        super()._append_terminal_event(turn_id)
        with self.terminal_lock:
            self.terminal_turns.add(turn_id)
            if len(self.terminal_turns) >= 2:
                self.terminal_event.set()


class _FakeRefs:
    def __init__(self, session_id: str, scope) -> None:
        self.session_id = session_id
        self.value = scope

    def scope(self, session_id: str):
        if session_id != self.session_id:
            raise KeyError(session_id)
        return self.value


class _ScopedToolService:
    def __init__(
        self,
        label: str,
        session_id: str,
        barrier: Barrier | None = None,
    ) -> None:
        token = "a" if label == "A" else "b"
        self.label = label
        self.session_id = session_id
        self.barrier = barrier
        self.execution_count = 0
        self.repository = SimpleNamespace(
            database_sha256=token * 64,
            snapshot_hash=token * 64,
            content_hash=f"content:{token}",
            fixture=SimpleNamespace(
                report_version=SimpleNamespace(
                    id=f"report-version:{token * 32}"
                )
            ),
        )
        self.account_activity = None
        self.scope = SimpleNamespace(
            report_version_id=f"report-version:{token * 32}",
            snapshot_id=f"snapshot:{token}",
            snapshot_hash=token * 64,
            content_hash=f"content:{token}",
        )
        self.refs = _FakeRefs(session_id, self.scope)

    def bind_session(self, session_id: str, *, force_new_generation=False):
        if session_id != self.session_id:
            raise RuntimeError("wrong session")
        return self.scope

    def has_session(self, session_id: str) -> bool:
        return session_id == self.session_id

    def dispatch(self, tool_name, args, *, session_id, turn_id=""):
        if args.get("fail"):
            if self.barrier is not None:
                self.barrier.wait(timeout=2)
            raise RuntimeError(f"failure:{self.label}")
        if self.barrier is not None:
            self.barrier.wait(timeout=2)
        reference = str(args.get("ref") or "")
        if reference and not reference.startswith(f"ref:{self.label}:"):
            return error_result(
                tool=tool_name,
                code="cross_scope_ref",
                message="Reference belongs to another Session.",
            )
        return json.dumps(
            {"report": self.label, "ref": f"ref:{self.label}:report"},
            separators=(",", ":"),
        )

    def execute_tool_call(self, *, args, next_call, **_kwargs):
        self.execution_count += 1
        return next_call(args)


class InvocationParityTest(unittest.TestCase):
    def test_retry_parity_fails_closed_when_runtime_control_is_missing(self):
        binding = HermesRuntimeBinding()
        with self.assertRaisesRegex(
            RuntimeError, "does not expose the canonical retry control"
        ):
            binding.create_agent(
                session_id="missing-retry-control",
                agent_factory=lambda **_options: SimpleNamespace(),
            )

    def test_frozen_runner_and_product_invocation_are_equivalent(self):
        captured = {}

        def factory(**options):
            captured.update(options)
            return SimpleNamespace(_api_max_retries=3)

        binding = HermesRuntimeBinding()
        agent = binding.create_agent(
            session_id="product-session",
            agent_factory=factory,
            base_url="https://example.invalid/v1",
            api_key="[REDACTED]",
            stream_delta_callback=lambda _delta: None,
        )
        effective = {
            "provider": captured["provider"],
            "model": captured["model"],
            "api_mode": captured["api_mode"],
            "max_iterations": captured["max_iterations"],
            "enabled_toolsets": captured["enabled_toolsets"],
            "quiet_mode": captured["quiet_mode"],
            "skip_context_files": captured["skip_context_files"],
            "skip_memory": captured["skip_memory"],
            "skip_background_review": captured["skip_background_review"],
            "load_soul_identity": captured.get("load_soul_identity", False),
        }
        self.assertEqual(
            effective,
            {
                "provider": "alibaba",
                "model": "qwen3.7-plus",
                "api_mode": "chat_completions",
                "max_iterations": 12,
                "enabled_toolsets": ["investigation"],
                "quiet_mode": True,
                "skip_context_files": True,
                "skip_memory": True,
                "skip_background_review": True,
                "load_soul_identity": False,
            },
        )
        self.assertIsNone(captured["session_db"])
        self.assertNotIn("ephemeral_system_prompt", captured)
        self.assertTrue(callable(captured["stream_delta_callback"]))
        self.assertEqual(agent._api_max_retries, 1)

        prompt_bytes = (ROOT / "hermes_m0/account_activity_prompt.txt").read_bytes()
        injected_prompt = binding.product_system_prompt()
        self.assertEqual(hashlib.sha256(prompt_bytes).hexdigest(), CANONICAL_PROMPT_FILE_SHA256)
        self.assertEqual(
            hashlib.sha256(injected_prompt.encode()).hexdigest(),
            CANONICAL_INJECTED_PROMPT_SHA256,
        )
        self.assertIn("medium、high 分别写作无风险、低风险、中风险、高风险", injected_prompt)
        canonical_projection = _canonical_parameter_projection()
        candidate_projection = _parameter_projection(M2_ACCOUNT_ACTIVITY_TOOLS)
        canonical_names = tuple(item[0] for item in canonical_projection)
        candidate_names = tuple(item[0] for item in candidate_projection)
        self.assertEqual(set(candidate_names), set(canonical_names))
        self.assertEqual(canonical_names, CANONICAL_SCHEMA_SOURCE_ORDER)
        self.assertEqual(candidate_names, canonical_names)
        canonical_hash = hashlib.sha256(
            _fixed_json(canonical_projection).encode("utf-8")
        ).hexdigest()
        candidate_hash = hashlib.sha256(
            _fixed_json(candidate_projection).encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            canonical_hash, CANONICAL_TOOL_PARAMETER_REFERENCE_SHA256
        )
        self.assertEqual(candidate_hash, canonical_hash)

    def test_real_hermes_qwen_visible_tool_definitions_match_heldout(self):
        with tempfile.TemporaryDirectory() as directory:
            binding = HermesRuntimeBinding()
            environment = {
                "HERMES_HOME": directory,
                "HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE": "1",
                "HERMES_ENABLE_PROJECT_PLUGINS": "1",
            }
            with patch.dict(os.environ, environment, clear=False):
                binding.configure_product_home(Path(directory))
                definitions = binding.tool_definitions(
                    enabled_toolsets=["investigation"]
                )
                agent = binding.create_agent(
                    session_id="offline-constructor-smoke",
                    base_url="https://example.invalid/v1",
                    api_key="[NOT-A-CREDENTIAL]",
                    stream_delta_callback=lambda _delta: None,
                )
                self.assertEqual(agent.provider, "alibaba")
                self.assertEqual(agent.model, "qwen3.7-plus")
                self.assertEqual(agent.api_mode, "chat_completions")
                self.assertEqual(agent.max_iterations, 12)
                self.assertEqual(agent._api_max_retries, 1)
                self.assertIsNone(agent._session_db)
                config = yaml.safe_load(
                    (Path(directory) / "config.yaml").read_text(encoding="utf-8")
                )
                self.assertEqual(config["agent"]["api_max_retries"], 1)
                agent.close()
        self.assertEqual(len(definitions), 11)
        canonical_projection = _canonical_parameter_projection()
        expected_parameters = dict(canonical_projection)
        canonical_runtime_order = tuple(
            sorted(item[0] for item in canonical_projection)
        )
        self.assertEqual(canonical_runtime_order, CANONICAL_QWEN_TOOL_ORDER)
        self.assertEqual(
            tuple(item["function"]["name"] for item in definitions),
            canonical_runtime_order,
        )
        for definition in definitions:
            function = definition["function"]
            self.assertEqual(
                function["parameters"], expected_parameters[function["name"]]
            )

    def test_full_transcript_round_trip_and_public_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            agents = []

            def factory(**options):
                agent = _ConcurrentAgent(options, Barrier(1))
                agents.append(agent)
                return agent

            service = HermesInvestigationAgentService(
                report_facade=_MultiReportFacade(),
                store=InvestigationStore(Path(directory) / "investigation.sqlite3"),
                agent_factory=factory,
                bind_runtime=False,
                hermes_state_dir=Path(directory) / "hermes",
            )
            session = service.create_session(_context("a").report_version_id)
            first, _ = service.accept_message(
                session.id, client_message_id="first", content="first question"
            )
            service.execute_turn(first.id)
            first_transcript = service.store.latest_completed_hermes_transcript(
                session.id
            )
            self.assertEqual(
                [item["role"] for item in first_transcript],
                ["user", "assistant", "tool", "assistant"],
            )
            self.assertEqual(
                first_transcript[1]["tool_calls"][0]["id"],
                f"call:{session.id}:1",
            )
            self.assertEqual(
                first_transcript[2]["tool_call_id"], f"call:{session.id}:1"
            )

            second, _ = service.accept_message(
                session.id, client_message_id="second", content="second question"
            )
            service.execute_turn(second.id)
            self.assertEqual(
                agents[0].calls[1][1]["conversation_history"], first_transcript
            )
            self.assertEqual(
                agents[0].calls[1][1]["system_message"],
                service.runtime_binding.product_system_prompt(),
            )

            app = FastAPI()
            app.include_router(create_investigation_router(service, executor=None))
            response = TestClient(app).get(
                f"/api/investigation-sessions/{session.id}/messages"
            )
            self.assertEqual(response.status_code, 200)
            serialized = response.text
            self.assertNotIn("tool_call_id", serialized)
            self.assertNotIn(f"call:{session.id}:1", serialized)
            self.assertNotIn("private_report", serialized)

            serialized_events = json.dumps(
                service.store.list_public_turn_events(second.id),
                ensure_ascii=False,
            )
            self.assertNotIn("tool_call_id", serialized_events)
            self.assertNotIn(f"call:{session.id}:2", serialized_events)
            self.assertNotIn("private_report", serialized_events)

    def test_unknown_outcome_does_not_replace_completed_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            agents = []

            def factory(**options):
                agent = _ConcurrentAgent(options, Barrier(1))
                agents.append(agent)
                return agent

            service = HermesInvestigationAgentService(
                report_facade=_MultiReportFacade(),
                store=InvestigationStore(Path(directory) / "investigation.sqlite3"),
                agent_factory=factory,
                bind_runtime=False,
            )
            session = service.create_session(_context("a").report_version_id)
            first, _ = service.accept_message(
                session.id, client_message_id="ok", content="completed"
            )
            service.execute_turn(first.id)
            before = service.store.latest_completed_hermes_transcript(session.id)
            agents[0].fail = True
            failed, _ = service.accept_message(
                session.id, client_message_id="unknown", content="unknown"
            )
            with self.assertRaisesRegex(RuntimeError, "unknown outcome"):
                service.execute_turn(failed.id)
            interrupted = service.store.get_turn(failed.id)
            self.assertEqual(interrupted.status, "interrupted")
            self.assertEqual(interrupted.error_code, "hermes_unknown_outcome")
            self.assertEqual(
                service.store.latest_completed_hermes_transcript(session.id), before
            )


class TranscriptIntegrityTest(unittest.TestCase):
    @staticmethod
    def _service(directory: str):
        agents = []

        def factory(**options):
            agent = _ConcurrentAgent(options, Barrier(1))
            agents.append(agent)
            return agent

        service = HermesInvestigationAgentService(
            report_facade=_MultiReportFacade(),
            store=InvestigationStore(Path(directory) / "investigation.sqlite3"),
            agent_factory=factory,
            bind_runtime=False,
            hermes_state_dir=Path(directory) / "hermes",
        )
        session = service.create_session(_context("a").report_version_id)
        return service, session, agents

    @staticmethod
    def _complete_first(service, session):
        turn, _ = service.accept_message(
            session.id,
            client_message_id="completed-before-failure",
            content="completed before failure",
        )
        service.execute_turn(turn.id)
        return service.store.latest_completed_hermes_transcript(session.id)

    def test_canonical_transcript_shapes_are_accepted(self):
        self.assertEqual(CANONICAL_TRANSCRIPT_PROVENANCE["hermes_version"], "0.20.4")
        # The accepted raw hash covers no-tool, single-tool, and multi-round
        # shapes; the installed Hermes contract additionally permits grouped calls.
        no_tool = [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ]
        single_tool = [
            {"role": "user", "content": "question"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_canonical_tool_call("call-1")],
            },
            _canonical_tool_result("call-1"),
            {"role": "assistant", "content": "answer"},
        ]
        multiple_rounds = [
            {"role": "user", "content": "question"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_canonical_tool_call("call-1")],
            },
            _canonical_tool_result("call-1"),
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [_canonical_tool_call("call-2", "read_posts")],
            },
            _canonical_tool_result("call-2", "read_posts"),
            {"role": "assistant", "content": "answer"},
        ]
        multiple_calls = [
            {"role": "user", "content": "question"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    _canonical_tool_call("call-1"),
                    _canonical_tool_call("call-2", "read_posts"),
                ],
            },
            _canonical_tool_result("call-1"),
            _canonical_tool_result("call-2", "read_posts"),
            {"role": "assistant", "content": "answer"},
        ]
        for name, messages in {
            "accepted-no-tool": no_tool,
            "accepted-single-tool": single_tool,
            "accepted-multiple-rounds": multiple_rounds,
            "hermes-0.20.4-multiple-calls": multiple_calls,
        }.items():
            with self.subTest(name=name):
                validated = HermesInvestigationAgentService._validate_completed_transcript(
                    {"messages": messages, "final_response": "answer"},
                    history=None,
                    user_message="question",
                )
                self.assertEqual(validated, messages)

    def test_invalid_transcript_shapes_fail_closed(self):
        call = _canonical_tool_call("call-1")
        result = _canonical_tool_result("call-1")
        invalid = {
            "non-dict-message": [
                {"role": "user", "content": "question"},
                "not-a-message",
                {"role": "assistant", "content": "answer"},
            ],
            "illegal-role": [
                {"role": "system", "content": "not persisted here"},
                {"role": "assistant", "content": "answer"},
            ],
            "orphan-result": [
                {"role": "user", "content": "question"},
                result,
                {"role": "assistant", "content": "answer"},
            ],
            "duplicate-result": [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                result,
                result,
                {"role": "assistant", "content": "answer"},
            ],
            "unmatched-result": [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                _canonical_tool_result("different-call"),
                {"role": "assistant", "content": "answer"},
            ],
            "dangling-call": [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                {"role": "assistant", "content": "answer"},
            ],
            "duplicate-call-id": [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                result,
                {"role": "assistant", "content": "", "tool_calls": [call]},
                result,
                {"role": "assistant", "content": "answer"},
            ],
            "missing-final-assistant": [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "", "tool_calls": [call]},
                result,
            ],
        }
        for name, messages in invalid.items():
            with self.subTest(name=name), self.assertRaises(ToolProtocolError):
                validate_hermes_transcript_messages(messages)

        with self.assertRaisesRegex(RuntimeError, "does not match"):
            HermesInvestigationAgentService._validate_completed_transcript(
                {
                    "messages": [
                        {"role": "user", "content": "question"},
                        {"role": "assistant", "content": "different"},
                    ],
                    "final_response": "answer",
                },
                history=None,
                user_message="question",
            )

        history = [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ]
        with self.assertRaisesRegex(RuntimeError, "preserve its input history"):
            HermesInvestigationAgentService._validate_completed_transcript(
                {
                    "messages": [
                        {"role": "user", "content": "changed question"},
                        {"role": "assistant", "content": "old answer"},
                        {"role": "user", "content": "question"},
                        {"role": "assistant", "content": "answer"},
                    ],
                    "final_response": "answer",
                },
                history=history,
                user_message="question",
            )

    def test_serialization_failure_is_unknown_and_preserves_completed_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            service, session, _agents = self._service(directory)
            before = self._complete_first(service, session)
            failed, _ = service.accept_message(
                session.id,
                client_message_id="serialization-failure",
                content="serialization failure",
            )
            with patch.object(
                service.store,
                "_hermes_transcript_json",
                side_effect=TypeError("injected serialization failure"),
            ), self.assertRaisesRegex(RuntimeError, "unknown outcome"):
                service.execute_turn(failed.id)
            current = service.store.get_turn(failed.id)
            self.assertEqual(current.status, "interrupted")
            self.assertEqual(current.error_code, "hermes_unknown_outcome")
            self.assertEqual(
                service.store.latest_completed_hermes_transcript(session.id), before
            )

    def test_incomplete_completed_transcript_is_unknown_and_not_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            service, session, agents = self._service(directory)
            before = self._complete_first(service, session)
            failed, _ = service.accept_message(
                session.id,
                client_message_id="incomplete-transcript",
                content="incomplete transcript",
            )

            def incomplete(message, **kwargs):
                history = [
                    dict(item)
                    for item in kwargs.get("conversation_history") or []
                ]
                return {
                    "final_response": "untrusted answer",
                    "messages": [
                        *history,
                        {"role": "user", "content": message},
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [_canonical_tool_call("incomplete-call")],
                        },
                        _canonical_tool_result("incomplete-call"),
                    ],
                    "completed": True,
                    "failed": False,
                    "interrupted": False,
                }

            with patch.object(
                agents[0], "run_conversation", side_effect=incomplete
            ), self.assertRaisesRegex(RuntimeError, "unknown outcome"):
                service.execute_turn(failed.id)
            current = service.store.get_turn(failed.id)
            self.assertEqual(current.status, "interrupted")
            self.assertEqual(current.error_code, "hermes_unknown_outcome")
            self.assertEqual(
                service.store.latest_completed_hermes_transcript(session.id), before
            )

    def test_database_failure_rolls_back_transcript_and_completed_status(self):
        with tempfile.TemporaryDirectory() as directory:
            service, session, _agents = self._service(directory)
            before = self._complete_first(service, session)
            failed, _ = service.accept_message(
                session.id,
                client_message_id="database-failure",
                content="database failure",
            )
            with patch.object(
                service.store,
                "_insert_query_receipts",
                side_effect=RuntimeError("injected database persistence failure"),
            ), self.assertRaisesRegex(RuntimeError, "unknown outcome"):
                service.execute_turn(failed.id)
            current = service.store.get_turn(failed.id)
            self.assertEqual(current.status, "interrupted")
            self.assertEqual(current.error_code, "hermes_unknown_outcome")
            self.assertEqual(
                service.store.latest_completed_hermes_transcript(session.id), before
            )
            with service.store._connect() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM investigation_hermes_transcripts WHERE turn_id = ?",
                    (failed.id,),
                ).fetchone()[0]
            self.assertEqual(count, 0)

    def test_explicit_interruption_has_distinct_error_code(self):
        with tempfile.TemporaryDirectory() as directory:
            service, session, _agents = self._service(directory)
            turn, _ = service.accept_message(
                session.id,
                client_message_id="explicit-interruption",
                content="explicit interruption",
            )
            agent = service._agent(session.id)
            with patch.object(
                agent,
                "run_conversation",
                return_value={"interrupted": True, "completed": False},
            ), self.assertRaisesRegex(RuntimeError, "was interrupted"):
                service.execute_turn(turn.id)
            current = service.store.get_turn(turn.id)
            self.assertEqual(current.status, "interrupted")
            self.assertEqual(current.error_code, "hermes_interrupted")

    def test_post_commit_notification_failure_keeps_completed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            service, session, _agents = self._service(directory)
            turn, _ = service.accept_message(
                session.id,
                client_message_id="notification-failure",
                content="notification failure",
            )

            def observer(_turn_id, node_name):
                if node_name == "persist_turn":
                    raise RuntimeError("injected notification failure")

            service.add_turn_node_observer(observer)
            result = service.execute_turn(turn.id)
            self.assertEqual(result.status, "completed")
            self.assertEqual(service.store.get_turn(turn.id).status, "completed")
            self.assertIsNotNone(
                service.store.latest_completed_hermes_transcript(session.id)
            )


class SessionScopedConcurrencyTest(unittest.TestCase):
    def tearDown(self):
        release_all_report_task_sessions()

    def test_formal_api_executor_concurrently_isolates_sessions_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            barrier = Barrier(2)
            agents = {}
            facade = _MultiReportFacade()

            def factory(**options):
                agent = _ExecutorPathAgent(
                    options,
                    fail=options["session_id"] == session_a.id,
                )
                agents[options["session_id"]] = agent
                return agent

            service = HermesInvestigationAgentService(
                report_facade=facade,
                store=InvestigationStore(Path(directory) / "investigation.sqlite3"),
                agent_factory=factory,
                bind_runtime=False,
            )
            session_a = service.create_session(_context("a").report_version_id)
            session_b = service.create_session(_context("b").report_version_id)
            tool_service_a = _ScopedToolService("A", session_a.id, barrier)
            tool_service_b = _ScopedToolService("B", session_b.id, barrier)
            bind_report_task_session(session_a.id, service=tool_service_a)
            bind_report_task_session(session_b.id, service=tool_service_b)

            executor = _ObservedExecutor(service, max_workers=2)
            app = FastAPI()
            app.include_router(create_investigation_router(service, executor))
            environment = {"HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE": "1"}
            try:
                with patch.dict(os.environ, environment, clear=False):
                    with TestClient(app) as client:
                        accepted_a = client.post(
                            f"/api/investigation-sessions/{session_a.id}/turns",
                            json={"client_message_id": "a-1", "content": "report a"},
                        )
                        self.assertEqual(accepted_a.status_code, 202)
                        conflict = client.post(
                            f"/api/investigation-sessions/{session_a.id}/turns",
                            json={"client_message_id": "a-2", "content": "conflict"},
                        )
                        self.assertEqual(conflict.status_code, 409)
                        accepted_b = client.post(
                            f"/api/investigation-sessions/{session_b.id}/turns",
                            json={"client_message_id": "b-1", "content": "report b"},
                        )
                        self.assertEqual(accepted_b.status_code, 202)
                        self.assertTrue(executor.terminal_event.wait(timeout=3))

                        turn_a_id = accepted_a.json()["turn_id"]
                        turn_b_id = accepted_b.json()["turn_id"]
                        self.assertEqual(service.store.get_turn(turn_a_id).status, "interrupted")
                        self.assertEqual(service.store.get_turn(turn_b_id).status, "completed")
                        self.assertEqual(agents[session_a.id].tool_result["report"], "A")
                        self.assertEqual(agents[session_b.id].tool_result["report"], "B")
                        self.assertEqual(tool_service_a.execution_count, 1)
                        self.assertEqual(tool_service_b.execution_count, 1)

                        tool_service_a.barrier = None
                        tool_service_b.barrier = None
                        cross_scope = json.loads(
                            _handler("read_report")(
                                {"ref": agents[session_a.id].tool_result["ref"]},
                                session_id=session_b.id,
                                task_id="cross-session",
                            )
                        )
                        self.assertEqual(
                            cross_scope["error"]["code"], "cross_scope_ref"
                        )

                        with patch.object(
                            executor,
                            "_submit_locked",
                            wraps=executor._submit_locked,
                        ) as submit:
                            replay = client.post(
                                f"/api/investigation-sessions/{session_b.id}/turns",
                                json={
                                    "client_message_id": "b-1",
                                    "content": "report b",
                                },
                            )
                        self.assertEqual(replay.status_code, 202)
                        self.assertEqual(replay.json()["turn_id"], turn_b_id)
                        submit.assert_not_called()
                        self.assertEqual(agents[session_b.id].execution_count, 1)
                        self.assertEqual(tool_service_b.execution_count, 1)
            finally:
                executor.shutdown(wait=True)

    def test_tool_registry_is_concurrent_isolated_and_fail_closed(self):
        release_all_report_task_sessions()
        barrier = Barrier(2)
        service_a = _ScopedToolService("A", "session-a", barrier)
        service_b = _ScopedToolService("B", "session-b", barrier)
        bind_report_task_session("session-a", service=service_a)
        bind_report_task_session("session-b", service=service_b)
        handler = _handler("read_report")
        environment = {"HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE": "1"}
        started = time.perf_counter()
        with patch.dict(os.environ, environment, clear=False):
            with ThreadPoolExecutor(max_workers=2) as pool:
                future_a = pool.submit(
                    handler, {}, session_id="session-a", task_id="turn-a"
                )
                future_b = pool.submit(
                    handler, {}, session_id="session-b", task_id="turn-b"
                )
                result_a = json.loads(future_a.result(timeout=3))
                result_b = json.loads(future_b.result(timeout=3))
            self.assertLess(time.perf_counter() - started, 2)
            self.assertEqual(result_a["report"], "A")
            self.assertEqual(result_b["report"], "B")

            service_a.barrier = None
            service_b.barrier = None
            self.assertEqual(
                json.loads(handler({}, session_id="session-a"))["report"], "A"
            )
            cross_scope = json.loads(
                handler(
                    {"ref": result_a["ref"]},
                    session_id="session-b",
                    task_id="turn-b-2",
                )
            )
            self.assertEqual(cross_scope["error"]["code"], "cross_scope_ref")
            unbound = json.loads(handler({}, session_id="missing"))
            self.assertEqual(unbound["error"]["code"], "product_session_unbound")

        self.assertIs(
            report_runtime_binding_for_session("session-a").service, service_a
        )
        with self.assertRaisesRegex(RuntimeError, "cannot be rebound"):
            bind_report_task_session(
                "session-a", service=_ScopedToolService("B", "session-a")
            )

    def test_agent_turns_overlap_same_session_conflicts_and_failure_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            barrier = Barrier(2)
            agents = {}

            def factory(**options):
                fail = options["session_id"].endswith("fail")
                agent = _ConcurrentAgent(options, barrier, fail=fail)
                agents[options["session_id"]] = agent
                return agent

            service = HermesInvestigationAgentService(
                report_facade=_MultiReportFacade(),
                store=InvestigationStore(Path(directory) / "investigation.sqlite3"),
                agent_factory=factory,
                bind_runtime=False,
            )
            session_a = service.create_session(_context("a").report_version_id)
            session_b = service.create_session(_context("b").report_version_id)
            turn_a, _ = service.accept_message(
                session_a.id, client_message_id="a", content="question a"
            )
            with self.assertRaises(ConcurrentTurnError):
                service.accept_message(
                    session_a.id, client_message_id="a-concurrent", content="other"
                )
            turn_b, _ = service.accept_message(
                session_b.id, client_message_id="b", content="question b"
            )
            with ThreadPoolExecutor(max_workers=2) as pool:
                future_a = pool.submit(service.execute_turn, turn_a.id)
                future_b = pool.submit(service.execute_turn, turn_b.id)
                result_a = future_a.result(timeout=3)
                result_b = future_b.result(timeout=3)
            self.assertEqual(result_a.answer, f"answer:{session_a.id}")
            self.assertEqual(result_b.answer, f"answer:{session_b.id}")
            self.assertEqual(agents[session_a.id].execution_count, 1)
            self.assertEqual(agents[session_b.id].execution_count, 1)

            replay_turn, replay = service.accept_message(
                session_a.id, client_message_id="a", content="question a"
            )
            self.assertTrue(replay)
            replay_result = service.execute_turn(replay_turn.id)
            self.assertTrue(replay_result.idempotent_replay)
            self.assertEqual(agents[session_a.id].execution_count, 1)

    def test_tool_exception_does_not_change_other_session_binding(self):
        release_all_report_task_sessions()
        barrier = Barrier(2)
        service_a = _ScopedToolService("A", "session-a", barrier)
        service_b = _ScopedToolService("B", "session-b", barrier)
        bind_report_task_session("session-a", service=service_a)
        bind_report_task_session("session-b", service=service_b)
        handler = _handler("read_report")
        with patch.dict(
            os.environ,
            {"HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE": "1"},
            clear=False,
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                failed = pool.submit(
                    handler,
                    {"fail": True},
                    session_id="session-a",
                    task_id="turn-a-failed",
                )
                healthy = pool.submit(
                    handler,
                    {},
                    session_id="session-b",
                    task_id="turn-b-healthy",
                )
                with self.assertRaisesRegex(RuntimeError, "failure:A"):
                    failed.result(timeout=3)
                self.assertEqual(
                    json.loads(healthy.result(timeout=3))["report"], "B"
                )
            service_a.barrier = None
            service_b.barrier = None
            self.assertEqual(
                json.loads(handler({}, session_id="session-a"))["report"], "A"
            )
            self.assertEqual(
                json.loads(handler({}, session_id="session-b"))["report"], "B"
            )


if __name__ == "__main__":
    unittest.main()
