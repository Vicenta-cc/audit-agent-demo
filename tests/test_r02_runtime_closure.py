from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from backend.hermes_runtime.adapter import (
    HermesRuntimeBinding,
    HermesRuntimeUnavailable,
    PRODUCT_PROMPT_SHA256,
)
from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.investigation.contracts import PublishedReportContext
from backend.investigation.store import InvestigationStore
from backend.reporting.errors import ReportGenerationError
from backend.reporting.runtime import R31ReportRuntime
from backend.reporting.store import ReportStore
from backend.reporting.structured_contract import STRUCTURED_REPORT_SCHEMA_VERSION
from hermes_m0.plugin import register
from hermes_m0.schemas import M2_ACCOUNT_ACTIVITY_TOOLS


ROOT = Path(__file__).resolve().parents[1]


def _context() -> PublishedReportContext:
    return PublishedReportContext(
        task_id="task-r02",
        report_id="report:" + "1" * 32,
        report_version_id="report-version:" + "2" * 32,
        version_number=1,
        source_snapshot_id="source-snapshot:r02",
        snapshot_hash="3" * 64,
        source_hash="source-r02",
        title="R0.2 report",
        content_hash="content-r02",
        published_at="2026-08-27T00:00:00+00:00",
        finding_ids=(),
        evidence_ids=(),
    )


class _ReportFacade:
    db_path = Path(__file__)

    def get_published_report_context(self, report_version_id: str):
        context = _context()
        if report_version_id != context.report_version_id:
            raise KeyError(report_version_id)
        return context


class _FakeAgent:
    def __init__(self, options: dict[str, object], *, fail: bool = False):
        self.options = options
        self.fail = fail
        self.execution_count = 0
        self.closed = False

    def run_conversation(self, message, **kwargs):
        self.execution_count += 1
        if self.fail:
            raise RuntimeError("unknown provider outcome")
        answer = "报告写道当前样本存在已记录风险。"
        history = [dict(item) for item in kwargs.get("conversation_history") or []]
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
                            "id": "call-1",
                            "function": {"name": "read_report", "arguments": {}},
                        }
                    ],
                },
                {"role": "tool", "name": "read_report", "content": "{}"},
                {"role": "assistant", "content": answer},
            ],
            "api_calls": 1,
            "completed": True,
            "failed": False,
            "interrupted": False,
            "turn_exit_reason": "text_response",
            "input_tokens": 10,
            "output_tokens": 8,
            "total_tokens": 18,
        }

    def close(self):
        self.closed = True


class _PluginContext:
    def __init__(self):
        self.tools = []
        self.middleware = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)

    def register_middleware(self, name, callback):
        self.middleware.append((name, callback))


class PromptAndPluginClosureTest(unittest.TestCase):
    def test_canonical_prompt_hash_and_formal_agent_initialization(self):
        captured = {}

        def factory(**options):
            captured.update(options)
            return _FakeAgent(options)

        binding = HermesRuntimeBinding()
        agent = binding.create_agent(
            session_id="session-r02", agent_factory=factory
        )
        self.assertIsInstance(agent, _FakeAgent)
        prompt_bytes = (ROOT / "hermes_m0/account_activity_prompt.txt").read_bytes()
        self.assertEqual(hashlib.sha256(prompt_bytes).hexdigest(), PRODUCT_PROMPT_SHA256)
        self.assertNotIn("ephemeral_system_prompt", captured)
        self.assertEqual(captured["enabled_toolsets"], ["investigation"])
        self.assertEqual(captured["provider"], "alibaba")
        self.assertEqual(captured["model"], "qwen3.7-plus")
        self.assertEqual(captured["api_mode"], "chat_completions")
        self.assertEqual(captured["max_iterations"], 12)
        self.assertIsNone(captured["session_db"])

    def test_product_adapter_rejects_validation_only_task_mode(self):
        with patch.dict(
            os.environ,
            {"HERMES_INVESTIGATION_TASK_MODE": "1"},
            clear=False,
        ):
            with self.assertRaises(HermesRuntimeUnavailable):
                HermesRuntimeBinding.activate_product_mode()

    def test_manifest_and_register_match_final_m22_catalog(self):
        manifest = yaml.safe_load(
            (ROOT / ".hermes/plugins/xhs-investigation/plugin.yaml").read_text()
        )
        expected = [item["name"] for item in M2_ACCOUNT_ACTIVITY_TOOLS]
        self.assertEqual(manifest["provides_tools"], expected)
        environment = {
            "HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE": "1",
            "HERMES_INVESTIGATION_TASK_MODE": "",
            "HERMES_INVESTIGATION_REPORT_TASK_MODE": "",
            "HERMES_INVESTIGATION_REAL_REPORT_MODE": "",
        }
        with patch.dict(os.environ, environment, clear=False):
            context = _PluginContext()
            register(context)
        self.assertEqual([item["name"] for item in context.tools], expected)
        self.assertEqual([item[0] for item in context.middleware], ["tool_execution"])


class HermesSessionTurnClosureTest(unittest.TestCase):
    def _service(self, directory: str, *, fail: bool = False):
        agents = []

        def factory(**options):
            agent = _FakeAgent(options, fail=fail)
            agents.append(agent)
            return agent

        service = HermesInvestigationAgentService(
            report_facade=_ReportFacade(),
            store=InvestigationStore(Path(directory) / "investigation.sqlite3"),
            agent_factory=factory,
            bind_runtime=False,
            hermes_state_dir=Path(directory) / "hermes",
        )
        return service, agents

    def test_completed_replay_executes_hermes_once_and_keeps_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            service, agents = self._service(directory)
            session = service.create_session(_context().report_version_id)
            turn, replay = service.accept_message(
                session.id,
                client_message_id="client-r02",
                content="报告中记录了什么？",
            )
            self.assertFalse(replay)
            first = service.execute_turn(turn.id)
            same_turn, replay = service.accept_message(
                session.id,
                client_message_id="client-r02",
                content="报告中记录了什么？",
            )
            second = service.execute_turn(same_turn.id)
            self.assertTrue(replay)
            self.assertEqual(first.answer, second.answer)
            self.assertTrue(second.idempotent_replay)
            self.assertEqual(agents[0].execution_count, 1)
            self.assertEqual(first.tool_names, ("read_report",))
            self.assertEqual(first.llm_call_count, 1)
            service.close()
            self.assertTrue(agents[0].closed)

    def test_unknown_outcome_is_interrupted_and_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            service, _agents = self._service(directory, fail=True)
            session = service.create_session(_context().report_version_id)
            turn, _ = service.accept_message(
                session.id,
                client_message_id="client-unknown",
                content="继续调查",
            )
            with self.assertRaisesRegex(RuntimeError, "unknown outcome"):
                service.execute_turn(turn.id)
            interrupted = service.store.get_turn(turn.id)
            self.assertEqual(interrupted.status, "interrupted")
            self.assertTrue(interrupted.retryable)
            resumed, replay = service.accept_resume(turn.id)
            self.assertFalse(replay)
            self.assertEqual(resumed.id, turn.id)
            self.assertEqual(resumed.status, "running")

    def test_report_runtime_rebinds_when_sessions_switch(self):
        class RuntimeBinding:
            def __init__(self):
                self.bound = []

            def configure_product_home(self, _path):
                return None

            def discover_plugins(self, *, force=False):
                return None

            def bind_published_report_session(self, **options):
                self.bound.append(options["session_id"])

        class ReportFacade:
            db_path = Path(__file__)

            @staticmethod
            def get_published_report_context(report_version_id):
                return SimpleNamespace(content_hash=f"hash:{report_version_id}")

        binding = RuntimeBinding()
        service = HermesInvestigationAgentService(
            report_facade=ReportFacade(),
            runtime_binding=binding,
            bind_runtime=True,
        )
        first = SimpleNamespace(
            id="session-first",
            task_id="task:first",
            report_version_id="report-version:first",
            snapshot_hash="snapshot:first",
        )
        second = SimpleNamespace(
            id="session-second",
            task_id="task:second",
            report_version_id="report-version:second",
            snapshot_hash="snapshot:second",
        )
        service._bind_session(first)
        service._bind_session(first)
        service._bind_session(second)
        service._bind_session(first)
        self.assertEqual(
            binding.bound,
            ["session-first", "session-second"],
        )


class R31RuntimeClosureTest(unittest.TestCase):
    @staticmethod
    def _account_model():
        return {
            "schema_version": "report-account-overview-r3.1/v1",
            "account_coverage_statistics": {"distinct_account_count": 0},
            "target_account_entries": [],
            "default_active_comment_entries": [],
            "full_account_index": {"entries": [], "total_count": 0},
            "scope_boundary": "current investigation only",
        }

    @classmethod
    def _document(cls):
        account_model = cls._account_model()
        return {
            "schema_version": STRUCTURED_REPORT_SCHEMA_VERSION,
            "report_metadata": {"title": "R3.1 report"},
            "ordered_sections": [],
            "account_coverage_statistics": account_model[
                "account_coverage_statistics"
            ],
            "target_account_entries": account_model["target_account_entries"],
            "default_active_comment_entries": account_model[
                "default_active_comment_entries"
            ],
            "full_account_index": account_model["full_account_index"],
            "account_scope_boundary": account_model["scope_boundary"],
        }

    def test_store_structured_read_schema_fence_and_metadata(self):
        store = object.__new__(ReportStore)
        store.get_version = lambda _version_id: {
            "status": "published",
            "body": {
                "report_document": self._document(),
                "account_model": self._account_model(),
            },
            "content_hash": "content-hash",
            "published_at": "2026-08-27T00:00:00+00:00",
        }
        result = store.get_frontend_report("report-version:r02")
        self.assertEqual(result["schema_version"], STRUCTURED_REPORT_SCHEMA_VERSION)
        self.assertEqual(result["report_metadata"]["content_hash"], "content-hash")
        self.assertEqual(
            result["report_metadata"]["published_at"],
            "2026-08-27T00:00:00+00:00",
        )

        invalid = self._document()
        invalid["task_id"] = "private"
        store.get_version = lambda _version_id: {
            "status": "published",
            "body": {
                "report_document": invalid,
                "account_model": self._account_model(),
            },
            "content_hash": "content-hash",
            "published_at": "2026-08-27T00:00:00+00:00",
        }
        with self.assertRaises(ReportGenerationError):
            store.get_frontend_report("report-version:r02")

    def test_formal_generation_entry_calls_final_graph_and_closes_it(self):
        events = []

        class FakeGraph:
            def __init__(self, **kwargs):
                events.append(("init", kwargs))

            def generate(self, task_id):
                events.append(("generate", task_id))
                return {"task_id": task_id}

            def close(self):
                events.append(("close", None))

        runtime = R31ReportRuntime(object(), graph_factory=FakeGraph)
        source = object()
        result = runtime.generate("task-r31", source=source, model_client=object())
        self.assertEqual(result, {"task_id": "task-r31"})
        self.assertEqual([item[0] for item in events], ["init", "generate", "close"])
        self.assertIs(events[0][1]["source"], source)


if __name__ == "__main__":
    unittest.main()
