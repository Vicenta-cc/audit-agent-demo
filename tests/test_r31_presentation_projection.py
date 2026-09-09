from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.reporting import create_reporting_router
from backend.reporting.account_entries import DEFAULT_ACCOUNT_FIXTURE_PATH
from backend.reporting.errors import ReportGenerationError
from backend.reporting.presentation_projection import (
    build_filtered_account_index_page,
    build_presentation_projection,
)
from backend.reporting.store import ReportStore
from hermes_m0.account_activity_repository import AccountActivityRepository


REPORT_A_DB = os.environ.get("R31_REAL_REPORT_A_DB")
REPORT_B_DB = os.environ.get("R31_REAL_REPORT_B_DB")

REAL_REPORTS = (
    (
        Path(REPORT_A_DB) if REPORT_A_DB else None,
        "report-version:8c355a5ba03f45619795813af83ac669",
        14,
        5,
        2446,
        33,
    ),
    (
        Path(REPORT_B_DB) if REPORT_B_DB else None,
        "report-version:4e3ebeccd2ed4f0c9c9a750332d22585",
        12,
        3,
        12133,
        13,
    ),
)


def _real_report_skip_reason() -> str | None:
    missing_variables = [
        name
        for name, value in (
            ("R31_REAL_REPORT_A_DB", REPORT_A_DB),
            ("R31_REAL_REPORT_B_DB", REPORT_B_DB),
        )
        if not value
    ]
    if missing_variables:
        return "real published R3.1 integration requires " + " and ".join(
            missing_variables
        )
    missing_files = [
        name
        for name, report in zip(
            ("R31_REAL_REPORT_A_DB", "R31_REAL_REPORT_B_DB"),
            REAL_REPORTS,
            strict=True,
        )
        if report[0] is None or not report[0].is_file()
    ]
    if missing_files:
        return "real published R3.1 database is unavailable for " + " and ".join(
            missing_files
        )
    return None


REAL_REPORT_SKIP_REASON = _real_report_skip_reason()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipIf(
    REAL_REPORT_SKIP_REASON is not None,
    REAL_REPORT_SKIP_REASON or "real published R3.1 integration is unavailable",
)
class R31PresentationProjectionRealReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.stores: list[tuple[ReportStore, str, tuple[int, int, int, int]]] = []
        for index, (source, version, sections, findings, comments, risks) in enumerate(
            REAL_REPORTS
        ):
            target = Path(self.temp_dir.name) / f"report-{index}.sqlite3"
            shutil.copy2(source, target)
            self.stores.append(
                (ReportStore(target), version, (sections, findings, comments, risks))
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_real_reports_are_deterministic_and_do_not_mutate_published_data(self):
        for store, version_id, expected in self.stores:
            db_hash = _sha256(store.db_path)
            version_before = copy.deepcopy(store.get_version(version_id))
            snapshot_before = copy.deepcopy(store.get_source_snapshot(version_id))

            first = store.get_presentation_projection(version_id)
            second = store.get_presentation_projection(version_id)

            self.assertEqual(first, second)
            self.assertEqual(_sha256(store.db_path), db_hash)
            self.assertEqual(store.get_version(version_id), version_before)
            self.assertEqual(store.get_source_snapshot(version_id), snapshot_before)
            self.assertEqual(len(first["ordered_sections"]), expected[0])
            finding_sections = [
                section
                for section in first["ordered_sections"]
                if section["section_type"] == "investigation_finding"
            ]
            self.assertEqual(len(finding_sections), expected[1])
            self.assertTrue(
                all(
                    item["finding_binding"]["status"] == "available"
                    for item in finding_sections
                )
            )
            self.assertEqual(
                first["statistics"]["independently_reviewed_comments"], expected[2]
            )
            self.assertEqual(first["statistics"]["comment_own_risk"], expected[3])
            self.assertEqual(first["investigation_summary"]["status"], "available")
            summary = first["investigation_summary"]["paragraphs"][0]
            for translated in (
                "通过",
                "无风险",
                "低风险",
                "中风险",
                "高风险",
                "直接研判依据",
                "帖子级审核发现",
            ):
                self.assertIn(translated, summary)
            self.assertIn("复审", summary)
            self.assertNotIn("需人工核验", summary)
            for source_term in ("pass", "review", "none", "low", "medium", "high", "Evidence", "Finding"):
                self.assertNotIn(source_term, summary)
            self.assertEqual(first["report_metadata"]["platform"], {
                "status": "available",
                "code": "dy",
                "label": "抖音",
            })
            self.assertNotIn("account_corpus_revision", json.dumps(first))
            self.assertNotIn('"claims"', json.dumps(first))
            standalone = next(
                section
                for section in first["ordered_sections"]
                if section["section_type"] == "standalone_risk_posts"
            )
            self.assertEqual(
                standalone["standalone_count"], len(standalone["standalone_items"])
            )

    def test_overview_and_risk_projection_use_structured_frozen_facts(self):
        store, version_id, _expected = self.stores[1]
        document = store.get_frontend_report(version_id)
        original_overview = next(
            item for item in document["ordered_sections"]
            if item["section_ref"] == "section-1"
        )
        original_data_overview = next(
            item for item in document["ordered_sections"]
            if item["section_ref"] == "section-2"
        )

        projection = store.get_presentation_projection(version_id)
        overview = next(
            item for item in projection["ordered_sections"]
            if item["section_ref"] == "section-1"
        )
        data_overview = next(
            item for item in projection["ordered_sections"]
            if item["section_ref"] == "section-2"
        )

        self.assertEqual(
            overview["paragraphs"],
            [item["text"] for item in original_overview["paragraphs"]],
        )
        self.assertEqual(
            data_overview["paragraphs"],
            [item["text"] for item in original_data_overview["paragraphs"]],
        )
        self.assertEqual(
            data_overview["presentation_paragraphs"],
            ["以下数据均来自本次调查的冻结资料快照。"],
        )
        self.assertEqual(projection["report_metadata"]["scope"], {"status": "unavailable"})
        self.assertEqual(
            projection["statistics"]["risk_distribution"],
            {
                "total": 304,
                "no_risk_summary": {
                    "label": "无风险内容占比",
                    "count": 292,
                    "percentage_label": "96.1%",
                    "description": (
                        "292 篇内容未发现风险，风险内容占全部样本的 3.9%。"
                    ),
                },
                "risk_bars": [
                    {"key": "high", "label": "高风险", "count": 2, "width_label": "25.0%"},
                    {"key": "medium", "label": "中风险", "count": 2, "width_label": "25.0%"},
                    {"key": "low", "label": "低风险", "count": 8, "width_label": "100.0%"},
                ],
            },
        )
        self.assertIn(
            "冻结资料包含 10 条直接研判依据，覆盖 9 条帖子级审核发现。",
            projection["investigation_summary"]["paragraphs"][0],
        )

    def test_account_groups_are_server_filtered_sorted_and_disjoint(self):
        first = self.stores[0][0].get_presentation_projection(self.stores[0][1])
        second = self.stores[1][0].get_presentation_projection(self.stores[1][1])
        self.assertEqual(first["accounts"]["post_author_entries"], [])
        for projection, expected_cross, expected_risk in (
            (first, 88, 31),
            (second, 88, 10),
        ):
            accounts = projection["accounts"]
            cross = accounts["cross_investigation_commenters"]
            risk = accounts["risk_commenters"]
            self.assertEqual(cross["status"], "available")
            self.assertEqual(cross["total_count"], expected_cross)
            self.assertEqual(risk["total_count"], expected_risk)
            self.assertLessEqual(len(cross["entries"]), 5)
            self.assertLessEqual(len(risk["entries"]), 5)
            self.assertTrue(
                all(item["comment_investigation_count"] >= 2 for item in cross["entries"])
            )
            self.assertTrue(
                all(item["statistics"]["risk_comment_count"] > 0 for item in risk["entries"])
            )
            groups = (
                accounts["target_entries"],
                accounts["post_author_entries"],
                cross["entries"],
                risk["entries"],
            )
            refs = [item["entry_ref"] for group in groups for item in group]
            self.assertEqual(len(refs), len(set(refs)))
        self.assertEqual(
            {
                item["entry_ref"]
                for item in second["accounts"]["post_author_entries"]
            },
            {"account-entry-4396a947cd1f161f"},
        )

    def test_complete_filtered_indexes_match_the_business_candidate_sets(self):
        repository = AccountActivityRepository.load(DEFAULT_ACCOUNT_FIXTURE_PATH)
        for store, version_id, _expected in self.stores:
            version = store.get_version(version_id)
            report = store.get_report(str(version["report_id"]))
            projection = store.get_report_account_projection(
                version_id, include_internal=True
            )
            comment_entries = [
                item
                for item in projection["entries"]
                if "comment_author" in item["roles"]
            ]
            counts = repository.comment_investigation_counts(
                [item["internal_account_ref"] for item in comment_entries],
                required_task_id=str(report["task_id"]),
            )
            expected_cross = {
                item["entry_ref"]
                for item in comment_entries
                if counts[item["internal_account_ref"]] >= 2
            }
            expected_risk = {
                item["entry_ref"]
                for item in comment_entries
                if item["current_investigation_statistics"]["risk_comment_count"] > 0
            }
            cross = store.list_presentation_account_entries(
                version_id,
                account_filter="cross_investigation_commenter",
                limit=100,
            )
            risk = store.list_presentation_account_entries(
                version_id,
                account_filter="risk_commenter",
                limit=100,
            )
            self.assertEqual(
                {item["entry_ref"] for item in cross["entries"]}, expected_cross
            )
            self.assertEqual(
                {item["entry_ref"] for item in risk["entries"]}, expected_risk
            )

    def test_filtered_indexes_support_server_search_sort_and_stable_cursor(self):
        store, version_id, _expected = self.stores[1]
        first = store.list_presentation_account_entries(
            version_id,
            account_filter="cross_investigation_commenter",
            limit=20,
        )
        self.assertEqual(first["total_count"], 88)
        self.assertEqual(len(first["entries"]), 20)
        self.assertTrue(first["has_more"])
        self.assertIsNotNone(first["next_cursor"])
        self.assertEqual(
            first["filter_counts"]["cross_investigation_commenter"]["total_count"],
            88,
        )
        self.assertEqual(
            first["filter_counts"]["risk_commenter"]["total_count"], 10
        )

        second = store.list_presentation_account_entries(
            version_id,
            account_filter="cross_investigation_commenter",
            limit=20,
            cursor=first["next_cursor"],
        )
        repeated = store.list_presentation_account_entries(
            version_id,
            account_filter="cross_investigation_commenter",
            limit=20,
            cursor=first["next_cursor"],
        )
        self.assertEqual(second, repeated)
        self.assertFalse(
            {item["entry_ref"] for item in first["entries"]}
            & {item["entry_ref"] for item in second["entries"]}
        )

        searched_name = first["entries"][0]["display_name"]
        searched = store.list_presentation_account_entries(
            version_id,
            account_filter="cross_investigation_commenter",
            search=searched_name,
            sort_order="comment_count",
            limit=20,
        )
        self.assertEqual(searched["search"], searched_name)
        self.assertEqual(searched["sort"], "comment_count")
        self.assertGreaterEqual(searched["total_count"], 1)
        self.assertTrue(
            all(
                searched_name.casefold() in item["display_name"].casefold()
                for item in searched["entries"]
            )
        )
        comments = [
            int(item["statistics"]["comment_count"] or 0)
            for item in searched["entries"]
        ]
        self.assertEqual(comments, sorted(comments, reverse=True))

        with self.assertRaises(ReportGenerationError):
            store.list_presentation_account_entries(
                version_id,
                account_filter="risk_commenter",
                sort_order="investigation_count",
            )

    def test_full_indexes_keep_qualifying_target_and_post_accounts(self):
        class Repository:
            def comment_investigation_counts(
                self, account_ids, *, required_task_id=None
            ):
                self.required_task_id = required_task_id
                return {str(account_id): 2 for account_id in account_ids}

        def entry(ref, roles, *, target=False, risk=0, comments=1):
            return {
                "entry_ref": ref,
                "internal_account_ref": "internal-" + ref,
                "display_name": ref,
                "roles": roles,
                "is_target_account": target,
                "current_investigation_statistics": {
                    "comment_count": comments,
                    "risk_comment_count": risk,
                    "published_post_count": 1 if "post_author" in roles else 0,
                    "risk_published_post_count": 0,
                    "commented_post_count": comments,
                    "commented_post_author_count": 1,
                    "earliest_activity_at": "2026-01-01T00:00:00Z",
                    "latest_activity_at": "2026-01-02T00:00:00Z",
                },
                "target_display_ordinal": 1 if target else None,
            }

        projection = {
            "entries": [
                entry("target", ["post_author", "comment_author"], target=True, risk=2),
                entry("publisher", ["post_author", "comment_author"], risk=1),
                entry("commenter", ["comment_author"]),
            ]
        }
        repository = Repository()
        for account_filter in (
            "cross_investigation_commenter",
            "risk_commenter",
        ):
            page = build_filtered_account_index_page(
                projection,
                account_repository=repository,
                current_task_id="current-task",
                account_filter=account_filter,
                limit=100,
                cursor=None,
            )
            refs = {item["entry_ref"] for item in page["entries"]}
            self.assertIn("target", refs)
            self.assertIn("publisher", refs)
            if account_filter == "risk_commenter":
                self.assertEqual(refs, {"target", "publisher"})
        self.assertEqual(repository.required_task_id, "current-task")

    def test_corpus_unavailability_only_degrades_dynamic_commenters(self):
        store, version_id, _expected = self.stores[1]
        projection = build_presentation_projection(
            store.get_frontend_report(version_id),
            snapshot=store.load_immutable_snapshot(version_id),
            account_projection=store.get_report_account_projection(
                version_id, include_internal=True
            ),
            account_repository=None,
            current_task_id="task-current",
        )
        accounts = projection["accounts"]
        self.assertEqual(
            accounts["cross_investigation_commenters"]["status"], "unavailable"
        )
        self.assertEqual(accounts["risk_commenters"]["status"], "available")
        self.assertGreater(
            accounts["risk_commenters"]["total_count"], 0
        )
        self.assertTrue(accounts["target_entries"])

    def test_section_finding_matching_uses_only_stable_ref_sets_and_fails_closed(self):
        store, version_id, _expected = self.stores[0]
        document = store.get_frontend_report(version_id)
        snapshot = store.load_immutable_snapshot(version_id)
        accounts = store.get_report_account_projection(version_id)

        reordered = copy.deepcopy(document)
        reordered["investigation_findings"].reverse()
        reordered["ordered_sections"].reverse()
        first_section = next(
            item
            for item in build_presentation_projection(
                reordered, snapshot=snapshot, account_projection=accounts
            )["ordered_sections"]
            if item["section_ref"] == "section-3-1"
        )
        self.assertEqual(
            first_section["finding_binding"]["investigation_finding_ref"],
            "investigation-finding-01",
        )

        no_match = copy.deepcopy(document)
        target = next(
            item for item in no_match["ordered_sections"]
            if item["section_ref"] == "section-3-1"
        )
        target["title"] = "与 Finding 标题完全无关"
        target["claims"] = [{
            "audit_finding_refs": ["audit-finding-missing"],
            "evidence_refs": ["evidence-missing"],
        }]
        result = build_presentation_projection(
            no_match, snapshot=snapshot, account_projection=accounts
        )
        unavailable = next(
            item for item in result["ordered_sections"]
            if item["section_ref"] == "section-3-1"
        )
        self.assertEqual(
            unavailable["paragraphs"],
            [item["text"] for item in target["paragraphs"]],
        )
        self.assertEqual(unavailable["finding_binding"], {"status": "unavailable"})

        multiple = copy.deepcopy(document)
        duplicate = copy.deepcopy(multiple["investigation_findings"][0])
        duplicate["investigation_finding_ref"] = "investigation-finding-duplicate"
        duplicate["title"] = "重复集合但不同标题"
        multiple["investigation_findings"].append(duplicate)
        result = build_presentation_projection(
            multiple, snapshot=snapshot, account_projection=accounts
        )
        unavailable = next(
            item for item in result["ordered_sections"]
            if item["section_ref"] == "section-3-1"
        )
        self.assertEqual(unavailable["finding_binding"], {"status": "unavailable"})

    def test_unified_account_index_labels_follow_the_role_filter(self):
        store, version_id, _expected = self.stores[1]
        unified = store.list_presentation_account_entries(version_id, limit=5)
        comments = store.list_presentation_account_entries(
            version_id, role="comment_author", limit=5
        )
        posts = store.list_presentation_account_entries(
            version_id, role="post_author", limit=5
        )
        self.assertIsNone(unified["role_filter"])
        self.assertEqual(unified["action_label"], "查看全部账号")
        self.assertEqual(comments["action_label"], "查看全部评论账号")
        self.assertEqual(posts["action_label"], "查看全部账号")
        self.assertEqual(posts["matched_count"], 2)
        detail = store.get_presentation_account_detail(
            version_id, entry_ref=unified["entries"][0]["entry_ref"]
        )
        self.assertEqual(detail["authorized_investigations"]["status"], "available")
        self.assertNotIn("history_activity", detail)

    def test_account_overview_uses_real_report_and_authorized_corpus_statistics(self):
        first_store, first_version, _expected = self.stores[0]
        second_store, second_version, _expected = self.stores[1]
        second_projection = second_store.get_presentation_projection(second_version)

        target = next(
            item
            for item in second_projection["accounts"]["target_entries"]
            if item["display_name"] == "麦热依姆古丽"
        )
        active = next(
            item
            for item in second_store.get_report_account_projection(second_version)[
                "all_entries"
            ]
            if item["display_name"] == "我爱我的幸福家庭"
        )
        metric_keys = (
            "comment_count",
            "risk_comment_count",
            "published_post_count",
            "risk_published_post_count",
            "commented_post_count",
            "commented_post_author_count",
        )
        self.assertEqual(
            {key: target["statistics"][key] for key in metric_keys},
            {
                "comment_count": 421,
                "risk_comment_count": 0,
                "published_post_count": 303,
                "risk_published_post_count": 12,
                "commented_post_count": 285,
                "commented_post_author_count": 2,
            },
        )
        self.assertEqual(active["roles"], ["comment_author"])
        self.assertEqual(
            {
                key: active["current_investigation_statistics"][key]
                for key in metric_keys
            },
            {
                "comment_count": 287,
                "risk_comment_count": 0,
                "published_post_count": 0,
                "risk_published_post_count": 0,
                "commented_post_count": 281,
                "commented_post_author_count": 2,
            },
        )

        detail_db_hash = _sha256(second_store.db_path)
        target_detail = second_store.get_presentation_account_detail(
            second_version, entry_ref=target["entry_ref"]
        )
        repeated_target_detail = second_store.get_presentation_account_detail(
            second_version, entry_ref=target["entry_ref"]
        )
        self.assertEqual(target_detail, repeated_target_detail)
        self.assertEqual(_sha256(second_store.db_path), detail_db_hash)
        self.assertEqual(
            target_detail["authorized_investigations"]["single_investigation_message"],
            "在当前授权范围内，该账号仅出现于本次调查。",
        )
        self.assertEqual(
            target_detail["authorized_investigations"]["investigation_count"], 1
        )
        self.assertEqual(target_detail["activity"]["status"], "available")
        self.assertTrue(target_detail["activity"]["earliest_activity_at"])
        self.assertTrue(target_detail["activity"]["latest_activity_at"])
        self.assertTrue(target_detail["activity"]["latest_comment_at"])
        self.assertTrue(target_detail["activity"]["latest_published_at"])
        self.assertLessEqual(len(target_detail["primary_comment_targets"]), 5)
        primary_targets = {
            item["display_name"]: item
            for item in target_detail["primary_comment_targets"]
        }
        self.assertEqual(primary_targets["麦热依姆古丽"]["comment_count"], 420)
        self.assertEqual(
            primary_targets["麦热依姆古丽"]["commented_post_count"], 284
        )

        first_internal = first_store.get_report_account_projection(
            first_version, include_internal=True
        )
        second_internal = second_store.get_report_account_projection(
            second_version, include_internal=True
        )
        first_shared = next(
            item
            for item in first_internal["entries"]
            if item["display_name"] == "温齐古丽"
        )
        second_shared = next(
            item
            for item in second_internal["entries"]
            if item["display_name"] == "温齐古丽"
        )
        self.assertEqual(
            first_shared["internal_account_ref"], second_shared["internal_account_ref"]
        )
        shared_detail = second_store.get_presentation_account_detail(
            second_version, entry_ref=second_shared["entry_ref"]
        )
        self.assertEqual(
            shared_detail["authorized_investigations"]["investigation_count"], 2
        )
        self.assertIsNone(
            shared_detail["authorized_investigations"]["single_investigation_message"]
        )
        for key in metric_keys:
            self.assertEqual(
                shared_detail["authorized_investigations"]["statistics"][key],
                sum(
                    item["statistics"][key]
                    for item in shared_detail["investigation_distribution"]
                ),
            )

        public_json = json.dumps(shared_detail, ensure_ascii=False)
        for forbidden in (
            "internal_account",
            '"task_id"',
            "account_corpus",
            "report_session",
            "authorized_task_count",
            "authorization_source",
            "risk_post_count",
            "account:v2:",
        ):
            self.assertNotIn(forbidden, public_json)

    def test_risk_comments_do_not_inherit_parent_post_risk(self):
        second_store, second_version, _expected = self.stores[1]
        internal_projection = second_store.get_report_account_projection(
            second_version, include_internal=True
        )
        target = next(
            item
            for item in internal_projection["entries"]
            if item["display_name"] == "麦热依姆古丽"
        )
        repository = AccountActivityRepository.load(DEFAULT_ACCOUNT_FIXTURE_PATH)
        overview = repository.overview(target["internal_account_ref"])
        occurrences = list(
            repository.corpus.occurrences_for(target["internal_account_ref"])
        )
        posts = {
            (item["task_id"], item["post"]["content_key"]): item
            for item in repository.corpus.occurrences
            if item["kind"] == "post_author"
        }
        direct_risk_comments = sum(
            item.get("risk_level") in {"low", "medium", "high"}
            for item in occurrences
            if item["kind"] == "comment_author"
        )
        comments_below_risk_posts = sum(
            (
                posts.get((item["task_id"], item["post"]["content_key"]))
                or {}
            ).get("risk_level")
            in {"low", "medium", "high"}
            for item in occurrences
            if item["kind"] == "comment_author"
        )
        self.assertEqual(direct_risk_comments, 0)
        self.assertGreater(comments_below_risk_posts, 0)
        self.assertEqual(overview["statistics"]["risk_comment_count"], 0)

    def test_appendix_and_details_filter_only_by_stable_refs(self):
        first_store, first_version, _expected = self.stores[0]
        related = first_store.get_presentation_appendix(
            first_version,
            view="posts",
            finding_ref="investigation-finding-01",
        )
        self.assertEqual(related["matched_count"], 5)
        self.assertEqual(
            {item["post_ref"] for item in related["items"]},
            {"post-033", "post-034", "post-036", "post-161", "post-164"},
        )
        evidence = first_store.get_presentation_finding_evidence(
            first_version,
            investigation_finding_ref="investigation-finding-01",
        )
        self.assertEqual(evidence["direct_evidence_count"], 11)
        self.assertTrue(
            all(item["support_type"] == "direct" for item in evidence["items"])
        )
        detail = first_store.get_presentation_post_detail(
            first_version, post_ref="post-033"
        )
        self.assertEqual(detail["post_ref"], "post-033")
        self.assertNotIn("author_entry_ref", detail)

        second_store, second_version, _expected = self.stores[1]
        standalone = second_store.get_presentation_appendix(
            second_version, view="standalone"
        )
        self.assertEqual(standalone["matched_count"], 3)

    def test_public_http_projection_uses_the_same_store(self):
        store, version_id, _expected = self.stores[0]
        app = FastAPI()
        app.include_router(create_reporting_router(store))
        with TestClient(app) as client:
            projection = client.get(
                f"/api/report-versions/{version_id}/presentation-projection"
            )
            related = client.get(
                f"/api/report-versions/{version_id}/appendix",
                params={"finding_ref": "investigation-finding-01"},
            )
            invalid = client.get(
                f"/api/report-versions/{version_id}/appendix",
                params={"finding_ref": "investigation-finding-missing"},
            )
            account = projection.json()["accounts"]["target_entries"][0]
            account_detail = client.get(
                f"/api/report-versions/{version_id}/accounts/{account['entry_ref']}"
            )
            cross_index = client.get(
                f"/api/report-versions/{version_id}/accounts",
                params={"filter": "cross_investigation_commenter", "limit": 100},
            )
            risk_index = client.get(
                f"/api/report-versions/{version_id}/accounts",
                params={"filter": "risk_commenter", "limit": 100},
            )
        self.assertEqual(projection.status_code, 200)
        self.assertEqual(related.status_code, 200)
        self.assertEqual(related.json()["matched_count"], 5)
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(account_detail.status_code, 200)
        self.assertEqual(cross_index.status_code, 200)
        self.assertEqual(risk_index.status_code, 200)
        self.assertEqual(cross_index.json()["total_count"], 88)
        self.assertEqual(risk_index.json()["total_count"], 31)
        self.assertEqual(len(cross_index.json()["entries"]), 88)
        self.assertEqual(cross_index.json()["sort"], "investigation_count")
        self.assertEqual(risk_index.json()["sort"], "risk_comment_count")
        public_json = json.dumps(
            {
                "projection": projection.json(),
                "account_detail": account_detail.json(),
                "cross_index": cross_index.json(),
                "risk_index": risk_index.json(),
            },
            ensure_ascii=False,
        )
        for forbidden in (
            "account_corpus_revision",
            '"task_id"',
            "internal_account",
            "account:v2:",
            "report_session",
            "authorized_task_count",
        ):
            self.assertNotIn(forbidden, public_json)


if __name__ == "__main__":
    unittest.main()
