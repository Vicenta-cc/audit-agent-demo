from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
import tempfile
import unittest
from unittest.mock import patch

from hermes_m0.account_activity_repository import AccountActivityRepository
from hermes_m0.account_activity_service import AccountActivityToolService
from hermes_m0.display_labels import (
    DECISION_LABELS,
    PLATFORM_LABELS,
    RISK_LEVEL_LABELS,
    enum_label,
)
from hermes_m0.domain import (
    AuthorDisplay,
    Evidence,
    EvidenceContent,
    EvidenceSource,
    Finding,
    FixtureProvenance,
    FrozenSnapshot,
    InvestigationFixture,
    Post,
    PostSource,
    ReportCase,
    ReportStatistics,
    ReportVersion,
)
from hermes_m0.real_report_repository import (
    FrozenReportComment,
    FrozenEvidenceRelation,
    PublishedReportRepository,
)
from hermes_m0.report_task_service import ReportTaskInvestigationToolService


REPORT_SPECS = (
    (
        "R31_REAL_REPORT_A_DB",
        "report-version:8c355a5ba03f45619795813af83ac669",
        "bfce4b1669d6f4b4684e66b5b076cd5cd97c2c2807f8f1b1dfdca9f12bec4f64",
        32,
        33,
        33,
    ),
    (
        "R31_REAL_REPORT_B_DB",
        "report-version:4e3ebeccd2ed4f0c9c9a750332d22585",
        "29bf76de4eb1468a51dfec43df9701419555bb67b9bef29fb8ef054bbb016c40",
        10,
        10,
        13,
    ),
)


def _read_snapshot_hash(database_path: Path, version_id: str) -> str:
    uri = f"{database_path.as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only = ON")
        query_only = connection.execute("PRAGMA query_only").fetchone()
        if query_only is None or int(query_only[0]) != 1:
            raise AssertionError("SQLite query_only could not be enabled")
        row = connection.execute(
            "SELECT snapshot_hash FROM report_source_snapshots WHERE report_version_id = ?",
            (version_id,),
        ).fetchone()
        if row is None:
            raise AssertionError("Published ReportVersion snapshot hash is unavailable")
        return str(row[0])
    finally:
        connection.close()


def _load_report(env_name: str, version_id: str, content_hash: str):
    raw_path = os.environ.get(env_name)
    if not raw_path:
        raise unittest.SkipTest(f"{env_name} is not configured")
    try:
        database_path = Path(raw_path).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise unittest.SkipTest(f"{env_name} does not point to a file") from exc
    if not database_path.is_file():
        raise unittest.SkipTest(f"{env_name} does not point to a file")
    snapshot_hash = _read_snapshot_hash(database_path, version_id)
    return PublishedReportRepository.load(
        database_path,
        report_version_id=version_id,
        expected_database_sha256=hashlib.sha256(database_path.read_bytes()).hexdigest(),
        expected_content_hash=content_hash,
        expected_snapshot_hash=snapshot_hash,
    )


def _synthetic_comment_projection():
    captured_at = datetime(2026, 1, 1, tzinfo=UTC)
    post_id = "post:synthetic"
    revision_id = "post-revision:synthetic"
    finding_id = "finding:synthetic"
    evidence_id = "evidence:synthetic-comment"
    snapshot_id = "snapshot:synthetic"
    report_version_id = "report-version:synthetic"
    comment_id = "synthetic-comment"
    statistics = ReportStatistics(
        case_count=1,
        post_count=1,
        finding_count=1,
        evidence_count=1,
        decision_counts={"pass": 1},
        risk_counts={"none": 1},
        evidence_type_counts={"comment": 1},
    )
    fixture = InvestigationFixture(
        schema_version="hermes-investigation-fixture/v1",
        provenance=FixtureProvenance(
            source_workspace_head="synthetic",
            source_task_id="synthetic",
            source_scope_ref="task:synthetic",
            projection_contract="synthetic-test-only",
            projection_snapshot_id=snapshot_id,
            projection_manifest_hash="synthetic",
            projection_counts={"posts": 1, "findings": 1, "evidence": 1},
        ),
        snapshot=FrozenSnapshot(
            id=snapshot_id,
            captured_through=captured_at,
            post_ids=(post_id,),
            post_revision_ids=(revision_id,),
            finding_ids=(finding_id,),
            evidence_ids=(evidence_id,),
        ),
        report_version=ReportVersion(
            id=report_version_id,
            report_id="report:synthetic",
            snapshot_id=snapshot_id,
            revision="synthetic",
            public_revision="published",
            title="Synthetic report",
            overview="Synthetic report for relation completeness testing.",
            published_at=captured_at,
            case_ids=("case:synthetic",),
            statistics=statistics,
        ),
        cases=(
            ReportCase(
                id="case:synthetic",
                position=1,
                key="pass",
                title="Synthetic case",
                summary="Synthetic case",
                member_post_ids=(post_id,),
            ),
        ),
        posts=(
            Post(
                id=post_id,
                revision_id=revision_id,
                title="Synthetic post",
                body="Synthetic body",
                author=AuthorDisplay(
                    display_name="Synthetic author",
                    avatar_url="",
                    platform="dy",
                    account_id="synthetic-author",
                ),
                source=PostSource(
                    platform="dy",
                    url="https://example.invalid/synthetic",
                    published_at="2026-01-01T00:00:00Z",
                    captured_at=captured_at,
                    analyzed_at=captured_at,
                ),
                finding_id=finding_id,
            ),
        ),
        findings=(
            Finding(
                id=finding_id,
                post_id=post_id,
                post_revision_id=revision_id,
                decision="pass",
                risk_level="none",
                categories=(),
                summary="Synthetic finding",
                completed_at=captured_at,
            ),
        ),
        evidence=(
            Evidence(
                id=evidence_id,
                finding_id=finding_id,
                parent_post_id=post_id,
                parent_revision_id=revision_id,
                type="comment",
                ordinal=1,
                content=EvidenceContent(
                    original_text="Synthetic comment",
                    translated_text="",
                    summary="Synthetic comment evidence",
                    timestamp_start=None,
                    timestamp_end=None,
                    asset_path="",
                ),
                source=EvidenceSource(
                    locator="",
                    availability="available",
                    captured_at=captured_at,
                ),
            ),
        ),
    )
    comment = FrozenReportComment(
        id=comment_id,
        parent_post_id=post_id,
        text="Synthetic comment",
        audit_status="completed",
        risk_level="none",
        risk_type="",
        published_at="2026-01-01T00:00:00Z",
        author_display_name="Synthetic commenter",
        author_source_key="synthetic-source-key",
        author_public_identifier="synthetic-public-id",
        platform="dy",
        raw_available=True,
        identity_consistent=True,
    )
    relations = (
        FrozenEvidenceRelation(
            evidence_id=evidence_id,
            report_version_id=report_version_id,
            snapshot_id=snapshot_id,
            post_id=post_id,
            support_type="direct",
            local_evidence_id=f"comment:{comment_id}",
        ),
    )
    return fixture, comment, relations


def _synthetic_repository(
    fixture: InvestigationFixture,
    comment: FrozenReportComment,
    relations: tuple[FrozenEvidenceRelation, ...],
) -> PublishedReportRepository:
    return PublishedReportRepository(
        fixture,
        database_path=Path("/tmp/m1-provenance-synthetic.sqlite3"),
        database_sha256="synthetic",
        content_hash="synthetic",
        snapshot_hash="synthetic",
        report_overview="Synthetic report for relation completeness testing.",
        post_content={},
        finding_details={},
        investigation_findings=(),
        standalone_risk_posts=(),
        report_comments=(comment,),
        evidence_relations=relations,
    )


class M1FindingCommentProvenanceTest(unittest.TestCase):
    def test_snapshot_hash_lookup_is_immutable_read_only_and_query_only(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "published-report.sqlite3"
            with sqlite3.connect(database_path) as connection:
                connection.execute(
                    "CREATE TABLE report_source_snapshots "
                    "(report_version_id TEXT PRIMARY KEY, snapshot_hash TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO report_source_snapshots VALUES (?, ?)",
                    ("report-version:test", "snapshot-hash-test"),
                )

            expected_uri = f"{database_path.resolve(strict=True).as_uri()}?mode=ro&immutable=1"
            with patch.object(sqlite3, "connect", wraps=sqlite3.connect) as connect:
                self.assertEqual(
                    _read_snapshot_hash(
                        database_path.resolve(strict=True), "report-version:test"
                    ),
                    "snapshot-hash-test",
                )
            connect.assert_called_once_with(expected_uri, uri=True)

            with sqlite3.connect(expected_uri, uri=True) as connection:
                connection.execute("PRAGMA query_only = ON")
                self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute(
                        "INSERT INTO report_source_snapshots VALUES (?, ?)",
                        ("report-version:forbidden", "forbidden"),
                    )

    def test_duplicate_evidence_relation_disables_complete_author_projection(self):
        fixture, comment, complete_relations = _synthetic_comment_projection()
        self.assertEqual(
            {item.evidence_id for item in complete_relations},
            {item.id for item in fixture.evidence},
        )

        baseline = _synthetic_repository(fixture, comment, complete_relations)
        target_evidence = fixture.evidence[0]
        self.assertTrue(baseline._has_evidence_relation_projection)
        self.assertEqual(
            baseline.comment_author_for_evidence(target_evidence.id), comment
        )

        duplicate_relations = (*complete_relations, complete_relations[0])
        self.assertEqual(duplicate_relations[:-1], complete_relations)
        self.assertEqual(duplicate_relations[-1], complete_relations[0])
        duplicate = _synthetic_repository(fixture, comment, duplicate_relations)
        self.assertFalse(duplicate._has_evidence_relation_projection)
        self.assertIsNone(duplicate.comment_author_for_evidence(target_evidence.id))

    def test_public_enum_labels_are_deterministic_and_unknown_is_null(self):
        self.assertEqual(enum_label(PLATFORM_LABELS, "dy"), "抖音")
        self.assertEqual(enum_label(PLATFORM_LABELS, "douyin"), "抖音")
        self.assertEqual(enum_label(PLATFORM_LABELS, "xhs"), "小红书")
        self.assertEqual(enum_label(PLATFORM_LABELS, "xiaohongshu"), "小红书")
        self.assertEqual(enum_label(DECISION_LABELS, "pass"), "通过")
        self.assertEqual(enum_label(DECISION_LABELS, "review"), "需复核")
        self.assertEqual(enum_label(DECISION_LABELS, "reject"), "不通过")
        self.assertEqual(enum_label(RISK_LEVEL_LABELS, "none"), "无风险")
        self.assertEqual(enum_label(RISK_LEVEL_LABELS, "low"), "低风险")
        self.assertEqual(enum_label(RISK_LEVEL_LABELS, "medium"), "中风险")
        self.assertEqual(enum_label(RISK_LEVEL_LABELS, "high"), "高风险")
        self.assertIsNone(enum_label(PLATFORM_LABELS, "unknown"))
        self.assertIsNone(enum_label(DECISION_LABELS, None))

    def test_account_activity_public_results_keep_raw_enums_and_add_labels(self):
        repository = AccountActivityRepository.load()
        comment = next(
            item
            for item in repository.corpus.occurrences
            if item["kind"] == "comment_author"
        )
        comment_preview = repository.occurrence_preview(
            comment,
            occurrence_ref="safe-comment-occurrence",
            report_risk_post_keys=frozenset(),
        )
        self.assertEqual(comment_preview["platform"], comment["post"]["platform"])
        self.assertEqual(
            comment_preview["platform_label"],
            enum_label(PLATFORM_LABELS, comment_preview["platform"]),
        )
        self.assertEqual(comment_preview["risk_level"], comment.get("risk_level"))
        self.assertEqual(
            comment_preview["risk_level_label"],
            enum_label(RISK_LEVEL_LABELS, comment_preview["risk_level"]),
        )

        post = next(
            item
            for item in repository.corpus.occurrences
            if item["kind"] == "post_author"
        )
        post_detail = repository.occurrence_detail(
            post,
            occurrence_ref="safe-post-occurrence",
            report_risk_post_keys=frozenset(),
            report_post=None,
        )
        self.assertEqual(post_detail["platform"], post["post"]["platform"])
        self.assertEqual(
            post_detail["platform_label"],
            enum_label(PLATFORM_LABELS, post_detail["platform"]),
        )
        self.assertEqual(post_detail["post"]["decision"], post.get("decision"))
        self.assertEqual(
            post_detail["post"]["decision_label"],
            enum_label(DECISION_LABELS, post_detail["post"]["decision"]),
        )
        self.assertEqual(post_detail["post"]["risk_level"], post.get("risk_level"))
        self.assertEqual(
            post_detail["post"]["risk_level_label"],
            enum_label(RISK_LEVEL_LABELS, post_detail["post"]["risk_level"]),
        )

    def test_report_navigation_public_results_keep_raw_enums_and_add_labels(self):
        repo = _load_report(*REPORT_SPECS[0][:3])
        service = ReportTaskInvestigationToolService(repo)
        service.bind_session("m1-labels")
        report = json.loads(service.dispatch("read_report", {}, session_id="m1-labels"))
        finding = report["data"]["investigation_finding_previews"][0]
        self.assertEqual(finding["type"], "investigation_finding")
        self.assertEqual(finding["type_label"], "主要调查发现")

        report_b_repo = _load_report(*REPORT_SPECS[1][:3])
        report_b_service = ReportTaskInvestigationToolService(report_b_repo)
        report_b_service.bind_session("m1-labels-b")
        report_b = json.loads(
            report_b_service.dispatch(
                "read_report", {}, session_id="m1-labels-b"
            )
        )
        standalone = report_b["data"]["standalone_risk_post_previews"][0]
        self.assertEqual(standalone["type"], "standalone_risk_post")
        self.assertEqual(standalone["type_label"], "其他独立风险事项")
        standalone_audit = standalone["audit_finding_preview"]
        self.assertEqual(standalone_audit["type_label"], "帖子级审核发现")
        self.assertEqual(
            standalone_audit["decision_label"],
            enum_label(DECISION_LABELS, standalone_audit["decision"]),
        )
        self.assertEqual(
            standalone_audit["risk_level_label"],
            enum_label(RISK_LEVEL_LABELS, standalone_audit["risk_level"]),
        )

        memberships = json.loads(
            service.dispatch(
                "list_finding_posts",
                {"finding_ref": finding["ref"], "limit": 20},
                session_id="m1-labels",
            )
        )
        membership = memberships["data"]["post_memberships"][0]
        audit = membership["membership_audit_finding"]
        self.assertEqual(audit["type_label"], "帖子级审核发现")
        self.assertEqual(
            audit["decision_label"], enum_label(DECISION_LABELS, audit["decision"])
        )
        self.assertEqual(
            audit["risk_level_label"],
            enum_label(RISK_LEVEL_LABELS, audit["risk_level"]),
        )
        self.assertEqual(
            membership["post"]["platform_label"],
            enum_label(PLATFORM_LABELS, membership["post"]["platform"]),
        )

        search = json.loads(
            service.dispatch(
                "search_posts",
                {"query_text": "风险帖子", "requested_count": 1},
                session_id="m1-labels",
                turn_id="label-search",
            )
        )
        candidate = search["data"]["candidates"][0]
        candidate_finding = candidate["finding_preview"]
        self.assertEqual(
            candidate_finding["decision_label"],
            enum_label(DECISION_LABELS, candidate_finding["decision"]),
        )
        self.assertEqual(
            candidate_finding["risk_level_label"],
            enum_label(RISK_LEVEL_LABELS, candidate_finding["risk_level"]),
        )
        self.assertIsNone(search["data"]["applied_filters"]["decision_label"])
        self.assertIsNone(search["data"]["applied_filters"]["risk_level_label"])

        post_detail = json.loads(
            service.dispatch(
                "read_posts",
                {"post_refs": [candidate["ref"]]},
                session_id="m1-labels",
            )
        )["data"]["post_groups"][0]
        self.assertEqual(
            post_detail["author"]["platform_label"],
            enum_label(PLATFORM_LABELS, post_detail["author"]["platform"]),
        )
        self.assertEqual(
            post_detail["natural_source"]["platform_label"],
            enum_label(PLATFORM_LABELS, post_detail["natural_source"]["platform"]),
        )
        self.assertEqual(
            post_detail["effective_finding"]["decision_label"],
            enum_label(
                DECISION_LABELS, post_detail["effective_finding"]["decision"]
            ),
        )

        report_b_search = json.loads(
            report_b_service.dispatch(
                "search_posts",
                {"query_text": "风险评论", "requested_count": 1},
                session_id="m1-labels-b",
                turn_id="label-search-b",
            )
        )
        risk_comments = None
        for item in report_b_search["data"]["candidates"]:
            candidate_comments = json.loads(
                report_b_service.dispatch(
                    "list_post_risk_comments",
                    {"post_ref": item["ref"], "limit": 50},
                    session_id="m1-labels-b",
                )
            )
            if candidate_comments["data"]["comments"]:
                risk_comments = candidate_comments
                break
        self.assertIsNotNone(risk_comments)
        comment = risk_comments["data"]["comments"][0]
        self.assertEqual(
            comment["risk_level_label"],
            enum_label(RISK_LEVEL_LABELS, comment["risk_level"]),
        )

        evidence = membership["membership_evidence_subset"][0]
        evidence_detail = json.loads(
            service.dispatch(
                "read_evidence",
                {"evidence_refs": [evidence["ref"]]},
                session_id="m1-labels",
            )
        )["data"]["evidence_groups"][0]
        self.assertEqual(
            evidence_detail["natural_source"]["platform_label"],
            enum_label(
                PLATFORM_LABELS, evidence_detail["natural_source"]["platform"]
            ),
        )

    def test_finding_membership_subset_and_whole_post_count(self):
        repo = _load_report(*REPORT_SPECS[0][:3])
        finding = next(
            item
            for item in repo.ordered_investigation_findings()
            if any(
                membership.post_id == "post:7637069400713867846"
                for membership in item.memberships
            )
        )
        membership = repo.finding_membership(finding.id, "post:7637069400713867846")
        self.assertEqual(len(membership.evidence_ids), 3)
        self.assertEqual(repo.post_total_evidence_count(membership.post_id), 8)

        service = ReportTaskInvestigationToolService(repo)
        service.bind_session("m1-scope")
        report = json.loads(service.dispatch("read_report", {}, session_id="m1-scope"))
        finding_ref = next(
            item["ref"]
            for item in report["data"]["investigation_finding_previews"]
            if item["name"] == finding.title
        )
        result = json.loads(
            service.dispatch(
                "list_finding_posts",
                {"finding_ref": finding_ref, "limit": 20},
                session_id="m1-scope",
            )
        )
        card = next(
            item
            for item in result["data"]["post_memberships"]
            if item["post"]["name"] == repo.post(membership.post_id).title
            and item["post_total_evidence_count"] == 8
        )
        self.assertEqual(card["membership_evidence_count"], 3)
        self.assertEqual(card["evidence_scope"], "finding_membership_subset")
        self.assertFalse(card["post_content_loaded"])
        self.assertIn("recorded_summary", card["membership_audit_finding"])
        self.assertEqual(card["membership_audit_finding"]["scope_label"], "整帖审核背景")
        self.assertFalse(card["membership_evidence_is_all_post_evidence"])

    def test_all_comment_evidence_resolves_for_both_read_only_oracles(self):
        for (
            env_name,
            version_id,
            content_hash,
            expected_comment_count,
            expected_direct_count,
            expected_risk_comment_count,
        ) in REPORT_SPECS:
            repo = _load_report(env_name, version_id, content_hash)
            comment_evidence = [
                item for item in repo.fixture.evidence if item.type == "comment"
            ]
            self.assertEqual(len(comment_evidence), expected_comment_count)
            self.assertEqual(len(repo.fixture.evidence), expected_direct_count)
            self.assertEqual(
                sum(
                    len(repo.risk_comments_for_post(post.id))
                    for post in repo.fixture.posts
                ),
                expected_risk_comment_count,
            )
            self.assertTrue(
                all(repo.comment_author_for_evidence(item.id) is not None for item in comment_evidence)
            )

    def test_comment_author_projection_is_relation_only_and_fail_closed(self):
        repo = _load_report(*REPORT_SPECS[0][:3])
        comment_evidence = next(item for item in repo.fixture.evidence if item.type == "comment")
        relation = repo._evidence_relations[comment_evidence.id]
        original = repo.comment_author_for_evidence(comment_evidence.id)
        self.assertIsNotNone(original)

        for altered in (
            replace(relation, post_id="post:wrong"),
            replace(relation, report_version_id="report-version:other"),
            replace(relation, snapshot_id="snapshot:other"),
            replace(relation, local_evidence_id="comment:"),
            replace(relation, local_evidence_id="visual:123"),
        ):
            repo._evidence_relations = MappingProxyType({comment_evidence.id: altered})
            self.assertIsNone(repo.comment_author_for_evidence(comment_evidence.id))
        repo._evidence_relations = MappingProxyType({relation.evidence_id: relation})

        comments = list(repo._report_comments_by_post[comment_evidence.parent_post_id])
        comment_id = relation.local_evidence_id.removeprefix("comment:")
        original_comment = next(item for item in comments if item.id == comment_id)
        repo._report_comments_by_post = MappingProxyType(
            {
                comment_evidence.parent_post_id: (
                    *comments,
                    original_comment,
                )
            }
        )
        self.assertIsNone(repo.comment_author_for_evidence(comment_evidence.id))
        repo._report_comments_by_post = MappingProxyType(
            {comment_evidence.parent_post_id: tuple(comments)}
        )
        repo._report_comments_by_post = MappingProxyType(
            {
                comment_evidence.parent_post_id: (
                    *[
                        replace(item, raw_available=False)
                        if item.id == comment_id
                        else item
                        for item in comments
                    ],
                )
            }
        )
        self.assertIsNone(repo.comment_author_for_evidence(comment_evidence.id))
        repo._report_comments_by_post = MappingProxyType(
            {
                comment_evidence.parent_post_id: (
                    *[
                        replace(item, identity_consistent=False)
                        if item.id == comment_id
                        else item
                        for item in comments
                    ],
                )
            }
        )
        self.assertIsNone(repo.comment_author_for_evidence(comment_evidence.id))

        visual = next(item for item in repo.fixture.evidence if item.type != "comment")
        self.assertIsNone(repo.comment_author_for_evidence(visual.id))

    def test_read_evidence_returns_public_comment_author_without_sensitive_ids(self):
        repo = _load_report(*REPORT_SPECS[0][:3])
        account_activity = AccountActivityToolService(repo)
        service = ReportTaskInvestigationToolService(repo, account_activity=account_activity)
        service.bind_session("m1-author")
        report = json.loads(service.dispatch("read_report", {}, session_id="m1-author"))
        finding_ref = report["data"]["investigation_finding_previews"][0]["ref"]
        listing = json.loads(
            service.dispatch(
                "list_finding_posts",
                {"finding_ref": finding_ref, "limit": 20},
                session_id="m1-author",
            )
        )
        evidence_ref = listing["data"]["post_memberships"][0]["membership_evidence_subset"][0]["ref"]
        result = json.loads(
            service.dispatch(
                "read_evidence",
                {"evidence_refs": [evidence_ref]},
                session_id="m1-author",
            )
        )
        source_comment = result["data"]["evidence_groups"][0]["source_comment"]
        author = source_comment["author"]
        self.assertTrue(author["display_name"])
        self.assertEqual(author["public_profile_identifier"]["label"], "抖音号")
        serialized = json.dumps(result, ensure_ascii=False)
        for secret in ("comment_id", "sec_uid", "short_user_id", "raw user_id", "task_id"):
            self.assertNotIn(secret, serialized)

        service.bind_session("m1-author-other")
        cross_session = json.loads(
            service.dispatch(
                "read_account_occurrence",
                {"occurrence_ref": source_comment["account_occurrence_ref"]},
                session_id="m1-author-other",
            )
        )
        self.assertFalse(cross_session["ok"])

        forged = json.loads(
            service.dispatch(
                "read_evidence",
                {"evidence_refs": ["evidence-forged"]},
                session_id="m1-author",
            )
        )
        self.assertFalse(forged["ok"])

    def test_missing_public_identifier_is_null_and_no_short_id_fallback(self):
        repo = _load_report(*REPORT_SPECS[0][:3])
        target = next(
            item
            for item in repo.fixture.evidence
            if item.type == "comment"
            and repo.comment_author_for_evidence(item.id) is not None
            and not repo.comment_author_for_evidence(item.id).author_public_identifier
        )
        comment = repo.comment_author_for_evidence(target.id)
        self.assertIsNotNone(comment)
        self.assertEqual(comment.author_public_identifier, "")
        self.assertEqual(comment.platform, "dy")

    def test_account_bridge_failure_does_not_hide_frozen_author_fields(self):
        repo = _load_report(*REPORT_SPECS[0][:3])
        evidence = next(item for item in repo.fixture.evidence if item.type == "comment")

        def unavailable_corpus():
            raise RuntimeError("isolated corpus unavailable")

        account_activity = AccountActivityToolService(
            repo, repository_loader=unavailable_corpus
        )
        service = ReportTaskInvestigationToolService(
            repo, account_activity=account_activity
        )
        projection = service._source_comment_projection(
            "bridge-unavailable", evidence, repo.post(evidence.parent_post_id)
        )
        self.assertTrue(projection["author"]["display_name"])
        self.assertIsNotNone(projection["author"]["public_profile_identifier"])
        self.assertIsNone(projection["author"]["account_ref"])
