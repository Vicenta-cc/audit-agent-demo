"""Candidate-local offline smoke for the canonical Hermes M0/M1/M2.2 paths."""

from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from hermes_m0.account_activity_repository import AccountActivityRepository
from hermes_m0.account_corpus import AccountCorpus
from hermes_m0.ledger import ToolExecutionLedger
from hermes_m0.refs import SessionReferenceRegistry
from hermes_m0.report_task_refs import ReportTaskReferenceRegistry
from hermes_m0.report_task_service import ReportTaskInvestigationToolService
from hermes_m0.repository import InvestigationRepository
from hermes_m0.schemas import REPORT_TASK_TOOLS, TOOLS
from hermes_m0.service import InvestigationToolService


ROOT = Path(__file__).resolve().parents[1]
REPORT_FIXTURE = ROOT / "hermes_m0" / "fixtures" / "report_2272c3692807.json"
ACCOUNT_FIXTURE = ROOT / "hermes_m0" / "fixtures" / "account_m22_corpus.json.gz"


class HermesOfflineSmokeTest(unittest.TestCase):
    def test_m0_fixture_tools_are_read_only_and_bound(self):
        repository = InvestigationRepository.load(REPORT_FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            service = InvestigationToolService(
                repository,
                refs=SessionReferenceRegistry(nonce="r0-m0"),
                ledger=ToolExecutionLedger(Path(directory) / "m0-ledger.sqlite3"),
            )
            service.bind_session("session-m0")
            report = json.loads(
                service.dispatch("read_report", {}, session_id="session-m0")
            )
            self.assertTrue(report["ok"])
            self.assertEqual(report["result_kind"], "report_overview")
            self.assertEqual(
                [schema["name"] for schema in TOOLS],
                ["read_report", "list_case_members", "read_posts", "list_evidence", "read_evidence"],
            )

    def test_m1_report_navigation_uses_only_frozen_fixture(self):
        repository = InvestigationRepository.load(REPORT_FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            service = ReportTaskInvestigationToolService(
                repository,
                refs=ReportTaskReferenceRegistry(nonce="r0-m1"),
                ledger=ToolExecutionLedger(Path(directory) / "m1-ledger.sqlite3"),
            )
            service.bind_session("session-m1")
            result = json.loads(
                service.dispatch("read_report", {}, session_id="session-m1")
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["content_state"], "overview_with_category_previews")
            self.assertEqual(
                [schema["name"] for schema in REPORT_TASK_TOOLS],
                ["read_report", "list_category_posts", "search_posts", "read_posts", "list_evidence", "read_evidence"],
            )

    def test_m1_report_finding_post_evidence_chain_is_read_only(self):
        repository = InvestigationRepository.load(REPORT_FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            service = ReportTaskInvestigationToolService(
                repository,
                refs=ReportTaskReferenceRegistry(nonce="r0-m1-chain"),
                ledger=ToolExecutionLedger(Path(directory) / "m1-chain-ledger.sqlite3"),
            )
            service.bind_session("session-m1-chain")
            report = json.loads(service.dispatch("read_report", {}, session_id="session-m1-chain"))
            category_ref = report["data"]["category_previews"][0]["ref"]
            directory_result = json.loads(
                service.dispatch(
                    "list_category_posts",
                    {"category_ref": category_ref},
                    session_id="session-m1-chain",
                )
            )
            post_ref = directory_result["data"]["posts"][0]["ref"]
            post_result = json.loads(
                service.dispatch(
                    "read_posts",
                    {"post_refs": [post_ref]},
                    session_id="session-m1-chain",
                )
            )
            evidence_directory = json.loads(
                service.dispatch(
                    "list_evidence",
                    {"post_ref": post_ref},
                    session_id="session-m1-chain",
                )
            )
            evidence_ref = evidence_directory["data"]["candidates"][0]["ref"]
            evidence_result = json.loads(
                service.dispatch(
                    "read_evidence",
                    {"evidence_refs": [evidence_ref]},
                    session_id="session-m1-chain",
                )
            )

            self.assertEqual(post_result["result_kind"], "post_detail")
            self.assertEqual(evidence_result["result_kind"], "evidence_full_content")
            self.assertEqual(
                evidence_result["data"]["evidence_groups"][0]["parent_post"]["ref"],
                post_ref,
            )
            serialized = json.dumps(
                [directory_result, post_result, evidence_directory, evidence_result],
                ensure_ascii=False,
            )
            self.assertNotIn("audit_result:", serialized)
            self.assertNotIn("/Users/", serialized)

    def test_m22_account_corpus_overview_and_occurrence_projection_are_offline(self):
        corpus = AccountCorpus.load(ACCOUNT_FIXTURE)
        repository = AccountActivityRepository(corpus)
        occurrence = next(
            item for item in corpus.occurrences if item["kind"] == "post_author"
        )
        account_id = str(occurrence["account_ref"])
        overview = repository.overview(account_id)
        self.assertEqual(overview["display_name"], repository.display_name(account_id))
        self.assertGreaterEqual(overview["statistics"]["published_post_count"], 1)
        posts = repository.ordered_occurrences(account_id, kind="post_author")
        self.assertTrue(posts)
        self.assertEqual(posts[0]["kind"], "post_author")
        self.assertEqual(corpus.corpus_revision, "6853831242a9b59ced78ba80ee9c17e03058df239fa063ed90a4e672f883897f")

    def test_m22_non_top5_account_and_risk_navigation_keep_parent_identity(self):
        corpus = AccountCorpus.load(ACCOUNT_FIXTURE)
        repository = AccountActivityRepository(corpus)
        targets = Counter(
            str(item["parent_post_author_account_ref"])
            for item in corpus.occurrences
            if item["kind"] == "comment_author"
            and item.get("parent_post_author_account_ref")
        )
        top5 = {account_id for account_id, _count in targets.most_common(5)}
        non_top5 = next(
            account_id
            for account_id in sorted(
                {str(item["account_ref"]) for item in corpus.occurrences if item["kind"] == "comment_author"}
            )
            if account_id not in top5
        )
        occurrence = repository.ordered_occurrences(non_top5, kind="comment_author")[0]
        preview = repository.occurrence_preview(
            occurrence,
            occurrence_ref=str(occurrence["occurrence_ref"]),
            report_risk_post_keys=frozenset(),
        )
        self.assertEqual(preview["role"], "当前账号是评论者")
        self.assertEqual(
            preview["parent_post"]["author_display_name"],
            repository.display_name(str(occurrence["parent_post_author_account_ref"])),
        )
        self.assertEqual(
            corpus.occurrence(str(occurrence["parent_post_occurrence_ref"]))["kind"],
            "post_author",
        )

        risk_occurrence = next(
            occurrence
            for account_id in sorted(
                {str(item["account_ref"]) for item in corpus.occurrences if item.get("account_ref")}
            )
            for occurrence in repository.ordered_occurrences(
                account_id, kind="comment_author", risk_filter="risk_only"
            )
        )
        self.assertIn(risk_occurrence["comment"]["risk_level"], {"low", "medium", "high"})
        risk_preview = repository.occurrence_preview(
            risk_occurrence,
            occurrence_ref=str(risk_occurrence["occurrence_ref"]),
            report_risk_post_keys=frozenset({str(risk_occurrence["post"]["content_key"])}),
        )
        self.assertTrue(risk_preview["parent_post_in_current_report_risk_summary"])

    def test_recovery_failed_turn_and_completed_replay_are_once_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery-ledger.sqlite3"
            ledger = ToolExecutionLedger(path)
            executions = 0

            def interrupted(_args):
                nonlocal executions
                executions += 1
                raise KeyboardInterrupt

            with self.assertRaises(KeyboardInterrupt):
                ledger.execute(
                    session_id="session-recovery",
                    tool_call_id="call-interrupted",
                    tool_name="read_report",
                    args={},
                    next_call=interrupted,
                )
            replay = json.loads(
                ledger.execute(
                    session_id="session-recovery",
                    tool_call_id="call-interrupted",
                    tool_name="read_report",
                    args={},
                    next_call=interrupted,
                )
            )
            record = ledger.get("session-recovery", "call-interrupted")
            self.assertEqual(replay["error"]["code"], "tool_execution_interrupted")
            self.assertEqual(executions, 1)
            self.assertEqual(record.execution_status, "interrupted")
            self.assertEqual(record.execution_count, 1)

            calls = 0

            def complete(_args):
                nonlocal calls
                calls += 1
                return '{"ok":true,"tool":"read_report"}'

            first = ledger.execute(
                session_id="session-recovery",
                tool_call_id="call-completed",
                tool_name="read_report",
                args={},
                next_call=complete,
            )
            second = ledger.execute(
                session_id="session-recovery",
                tool_call_id="call-completed",
                tool_name="read_report",
                args={},
                next_call=complete,
            )
            self.assertEqual(second, first)
            self.assertEqual(calls, 1)
            self.assertEqual(ledger.get("session-recovery", "call-completed").execution_count, 1)

            with ledger._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO tool_executions (
                        session_id, tool_call_id, tool_name, args_fingerprint,
                        execution_status, result_hash, result_cache, execution_count
                    ) VALUES ('session-recovery', 'call-orphan', 'read_report', 'fingerprint',
                              'running', '', '', 1)
                    """
                )
            reopened = ToolExecutionLedger(path)
            orphan = reopened.get("session-recovery", "call-orphan")
            self.assertEqual(orphan.execution_status, "interrupted")
            self.assertEqual(orphan.execution_count, 1)


if __name__ == "__main__":
    unittest.main()
