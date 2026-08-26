"""Candidate-local offline smoke for the canonical Hermes M0/M1/M2.2 paths."""

from __future__ import annotations

import json
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
