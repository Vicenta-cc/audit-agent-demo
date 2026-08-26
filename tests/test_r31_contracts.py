from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.reporting.account_overview import public_account_overview_projection
from backend.reporting.r31_graph import AccountOverviewReportGraph


class R31ContractsTest(unittest.TestCase):
    def test_account_overview_public_projection_orders_targets_and_full_index(self):
        entries = [
            {
                "entry_ref": "account-entry-target",
                "display_name": "Target",
                "roles": ("post_author",),
                "is_target_account": True,
                "target_display_ordinal": 1,
                "active_comment_rank": None,
                "active_comment_display_ordinal": None,
                "default_visible": True,
                "current_investigation_statistics": {
                    "published_post_count": 2,
                    "risk_published_post_count": 1,
                    "comment_count": 0,
                    "risk_comment_count": 0,
                    "commented_post_count": 0,
                    "commented_post_author_count": 0,
                    "latest_activity_at": "2026-01-01T00:00:00Z",
                },
            },
            {
                "entry_ref": "account-entry-active",
                "display_name": "Active",
                "roles": ("comment_author",),
                "is_target_account": False,
                "target_display_ordinal": None,
                "active_comment_rank": 1,
                "active_comment_display_ordinal": 1,
                "default_visible": True,
                "current_investigation_statistics": {
                    "published_post_count": 0,
                    "risk_published_post_count": 0,
                    "comment_count": 3,
                    "risk_comment_count": 1,
                    "commented_post_count": 2,
                    "commented_post_author_count": 1,
                    "latest_activity_at": "2026-01-02T00:00:00Z",
                },
            },
        ]
        projection = public_account_overview_projection(
            {
                "schema_version": "report-account-entry-r3.1/v1",
                "projection_hash": "a" * 64,
                "statistics": {
                    "post_author_account_count": 1,
                    "comment_author_account_count": 1,
                },
                "default_active_comment_limit": 10,
                "entries": entries,
                "scope_boundary": "current investigation only",
            }
        )
        self.assertEqual(
            [item["entry_ref"] for item in projection["target_account_entries"]],
            ["account-entry-target"],
        )
        self.assertEqual(
            [item["entry_ref"] for item in projection["default_active_comment_entries"]],
            ["account-entry-active"],
        )
        self.assertEqual(projection["full_account_index"]["total_count"], 2)
        self.assertNotIn("internal_account_ref", str(projection))

    def test_ordered_sections_include_account_overview_and_stable_numbers(self):
        graph = object.__new__(AccountOverviewReportGraph)
        graph._snapshot = lambda _report_version_id: SimpleNamespace(
            statistics={
                "canonical_posts": 2,
                "decision": {"pass": 1, "review": 1, "reject": 0},
                "risk_level": {"high": 1, "medium": 0, "low": 0, "none": 1},
            }
        )
        state = {
            "report_version_id": "report-v1",
            "section_drafts": [
                {"section_id": "overview", "title": "Overview", "paragraphs": []},
                {"section_id": "investigation-a", "title": "Finding A", "paragraphs": []},
                {"section_id": "synthesis", "title": "Synthesis", "paragraphs": []},
                {"section_id": "conclusion", "title": "Conclusion", "paragraphs": []},
            ],
            "standalone_risk_posts": [],
            "investigation_findings": [],
            "report_account_projection": {
                "entries": [
                    {
                        "current_investigation_statistics": {
                            "comment_count": 2,
                            "risk_comment_count": 1,
                        }
                    }
                ]
            },
        }
        projection = {
            "account_coverage_statistics": {
                "post_author_account_count": 1,
                "comment_author_account_count": 1,
            },
            "scope_boundary": "current investigation only",
        }
        sections = graph._ordered_sections(state, projection)
        self.assertEqual(
            [item["section_id"] for item in sections],
            [
                "overview",
                "data-overview",
                "content-comment-scale",
                "review-risk-levels",
                "account-activity-overview",
                "investigation-findings",
                "investigation-a",
                "standalone-risk-posts",
                "synthesis",
                "conclusion",
            ],
        )
        self.assertEqual([item["section_number"] for item in sections], ["1", "2", "2.1", "2.2", "2.3", "3", "3.1", "4", "5", "6"])


if __name__ == "__main__":
    unittest.main()
