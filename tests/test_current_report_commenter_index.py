"""The current-report index includes every commenter, including safe/dual-role accounts."""
import copy
import unittest

from backend.reporting.presentation_projection import build_filtered_account_index_page


class CurrentReportCommenterIndexTest(unittest.TestCase):
    def test_complete_index_pagination_search_and_order_without_cross_report_corpus(self):
        entries = [
            {"entry_ref": f"account:{i:02}", "display_name": f"账号{i:02}",
             "roles": ["comment_author", "post_author"] if i == 24 else ["comment_author"],
             "current_investigation_statistics": {
                 "risk_comment_count": 2 if i < 6 else 0, "comment_count": 30 - i,
                 "commented_post_count": 1, "commented_post_author_count": 1,
             }}
            for i in range(25)
        ]
        projection = {"entries": [*entries, {"entry_ref": "publisher-only", "roles": ["post_author"]}]}
        before = copy.deepcopy(projection)
        def page(**kwargs):
            return build_filtered_account_index_page(
                projection, account_repository=None, current_task_id="current",
                account_filter="comment_author", limit=20, cursor=kwargs.pop("cursor", None), **kwargs)
        first = page()
        self.assertEqual(first["status"], "available")
        self.assertEqual(first["total_count"], 25)
        self.assertEqual([e["entry_ref"] for e in first["entries"][:5]], [e["entry_ref"] for e in entries[:5]])
        second = page(cursor=first["next_cursor"])
        self.assertEqual(len(first["entries"]), 20)
        self.assertEqual(len(second["entries"]), 5)
        self.assertFalse(second["has_more"])
        self.assertEqual({e["entry_ref"] for e in first["entries"] + second["entries"]}, {e["entry_ref"] for e in entries})
        self.assertEqual([e["entry_ref"] for e in page(search="账号24")["entries"]], ["account:24"])
        self.assertEqual(page(search="不存在")["total_count"], 0)
        self.assertEqual(page(sort_order="comment_count")["entries"][0]["entry_ref"], "account:00")
        self.assertEqual(projection, before)
