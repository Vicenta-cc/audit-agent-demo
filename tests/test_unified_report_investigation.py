from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock, call

from backend.hermes_runtime.service import HermesInvestigationAgentService
from backend.investigation.contracts import PublishedReportContext
from backend.investigation.store import InvestigationStore
from hermes_m0.runtime import configure_real_report_runtime
from hermes_m0.pass_support import pass_tool_schemas
from hermes_m0.schemas import M2_ACCOUNT_ACTIVITY_TOOLS
from hermes_m0.unified_support import (
    UNIFIED_REPORT_SYSTEM_PROMPT,
    UnifiedAuditReportToolService,
    unified_tool_schemas,
)


seed = runpy.run_path(str(Path(__file__).with_name("test_pass_report.py")))


def test_unified_overview_prompt_and_schema_support_bounded_post_directory():
    schemas = unified_tool_schemas()
    read_report = next(
        schema for schema in schemas if schema["name"] == "read_report"
    )
    list_report_posts = next(
        schema for schema in schemas if schema["name"] == "list_report_posts"
    )
    list_post_comments = next(
        schema for schema in schemas if schema["name"] == "list_post_comments"
    )
    names = {schema["name"] for schema in schemas}

    assert "报告的大概内容是什么" in UNIFIED_REPORT_SYSTEM_PROMPT
    assert "先调用read_report" in UNIFIED_REPORT_SYSTEM_PROMPT
    assert "不得只讲风险帖而遗漏安全帖" in UNIFIED_REPORT_SYSTEM_PROMPT
    assert "1至30条帖子" in read_report["description"]
    assert "第一页最多10条" in read_report["description"]
    assert list_report_posts["parameters"]["properties"]["page"]["maximum"] == 3
    assert list_post_comments["parameters"]["properties"]["risk_filter"][
        "enum"
    ] == ["all", "risk", "no_risk", "unknown"]
    assert "read_report" in names
    assert "list_report_posts" in names
    assert "read_posts" in names
    assert "list_post_comments" in names
    assert "list_evidence" in names
    assert "read_evidence" in names
    assert "search_posts" not in names
    assert "list_post_risk_comments" not in names
    assert "list_finding_posts" not in names
    assert "compare_authorized_report_accounts" in names
    assert "compare_authorized_report_accounts" not in {
        schema["name"] for schema in pass_tool_schemas()
    }
    assert "compare_authorized_report_accounts" not in {
        schema["name"] for schema in M2_ACCOUNT_ACTIVITY_TOOLS
    }


def test_unified_report_read_report_exposes_every_mixed_post(tmp_path):
    verdicts = [
        ("pass", "none"),
        ("review", "medium"),
        ("pass", "none"),
        ("reject", "high"),
        ("pass", "none"),
    ]
    source, store = seed["seed_audit"](tmp_path, verdicts=verdicts)
    result = seed["R31ReportRuntime"](store).generate(
        "new-search-task",
        source=source,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )
    version = store.get_version(result.report_version_id)
    snapshot = store.get_source_snapshot(result.report_version_id)
    service = configure_real_report_runtime(
        store.db_path,
        report_version_id=result.report_version_id,
        expected_database_sha256=hashlib.sha256(store.db_path.read_bytes()).hexdigest(),
        expected_content_hash=version["content_hash"],
        expected_snapshot_hash=snapshot["snapshot_hash"],
        ledger_path=tmp_path / "tool-ledger.sqlite3",
    )
    assert type(service) is UnifiedAuditReportToolService
    service.bind_session("unified-session")
    response = json.loads(
        service.dispatch(
            "read_report", {}, session_id="unified-session", turn_id="turn-1"
        )
    )
    assert response["ok"], response
    previews = response["data"]["post_previews"]
    assert len(previews) == 5
    assert all(item["audit_summary"] for item in previews)
    assert Counter((item["decision"], item["risk_level"]) for item in previews) == Counter(
        verdicts
    )
    assert response["data"]["post_previews_complete_for_report"] is True
    assert response["data"]["post_directory"] == {
        "total_count": 5,
        "returned_count": 5,
        "page": 1,
        "page_size": 10,
        "page_count": 1,
        "has_more": False,
        "next_page": None,
        "complete_for_report": True,
    }
    assert "remaining post directory pages" not in response["not_loaded"]
    assert len({item["ref"] for item in previews}) == 5

    details = json.loads(
        service.dispatch(
            "read_posts",
            {"post_refs": [item["ref"] for item in previews]},
            session_id="unified-session",
            turn_id="turn-2",
        )
    )
    assert details["ok"], details
    groups = details["data"]["post_groups"]
    assert len(groups) == 5
    assert Counter(
        (
            item["effective_finding"]["decision"],
            item["effective_finding"]["risk_level"],
        )
        for item in groups
    ) == Counter(verdicts)

    second_safe = [item for item in previews if item["decision"] == "pass"][1]
    second_safe_detail = json.loads(
        service.dispatch(
            "read_posts",
            {"post_refs": [second_safe["ref"]]},
            session_id="unified-session",
            turn_id="turn-2-safe",
        )
    )
    assert second_safe_detail["ok"], second_safe_detail
    assert (
        second_safe_detail["data"]["post_groups"][0]["effective_finding"][
            "decision"
        ]
        == "pass"
    )

    comments = json.loads(
        service.dispatch(
            "list_post_comments",
            {"post_ref": previews[0]["ref"], "limit": 20},
            session_id="unified-session",
            turn_id="turn-3",
        )
    )
    assert comments["ok"], comments

    for preview in (previews[0], previews[1]):
        evidence = json.loads(
            service.dispatch(
                "list_evidence",
                {"post_ref": preview["ref"]},
                session_id="unified-session",
                turn_id="turn-3",
            )
        )
        assert evidence["ok"], evidence

    for unavailable in ("search_posts", "list_post_risk_comments"):
        rejected = json.loads(
            service.dispatch(
                unavailable,
                {"query_text": "风险", "requested_count": 1}
                if unavailable == "search_posts"
                else {"post_ref": previews[0]["ref"], "limit": 20},
                session_id="unified-session",
                turn_id="turn-4",
            )
        )
        assert not rejected["ok"]
        assert rejected["error"]["code"] == "tool_unavailable_for_report_task_scope"


def test_unified_report_thirty_post_directory_pages_and_restored_last_post(tmp_path):
    verdicts = [
        (("pass", "none"), ("review", "medium"), ("reject", "high"))[index % 3]
        for index in range(30)
    ]
    source, store = seed["seed_audit"](tmp_path, verdicts=verdicts)
    result = seed["R31ReportRuntime"](store).generate(
        "new-search-task",
        source=source,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )
    version = store.get_version(result.report_version_id)
    snapshot = store.get_source_snapshot(result.report_version_id)
    ledger_path = tmp_path / "tool-ledger.sqlite3"

    def configured_service():
        configured = configure_real_report_runtime(
            store.db_path,
            report_version_id=result.report_version_id,
            expected_database_sha256=hashlib.sha256(
                store.db_path.read_bytes()
            ).hexdigest(),
            expected_content_hash=version["content_hash"],
            expected_snapshot_hash=snapshot["snapshot_hash"],
            ledger_path=ledger_path,
        )
        configured.bind_session("thirty-post-session")
        return configured

    service = configured_service()
    overview = json.loads(
        service.dispatch(
            "read_report", {}, session_id="thirty-post-session", turn_id="overview"
        )
    )["data"]
    assert len(overview["post_previews"]) == 10
    assert [item["position"] for item in overview["post_previews"]] == list(
        range(1, 11)
    )
    assert overview["post_previews_complete_for_report"] is False
    assert overview["post_directory"]["page_count"] == 3
    assert overview["post_directory"]["next_page"] == 2

    second = json.loads(
        service.dispatch(
            "list_report_posts",
            {"page": 2},
            session_id="thirty-post-session",
            turn_id="page-2",
        )
    )["data"]
    assert [item["position"] for item in second["post_previews"]] == list(
        range(11, 21)
    )

    third_payload = service.execute_tool_call(
        session_id="thirty-post-session",
        tool_call_id="page-3-call",
        tool_name="list_report_posts",
        args={"page": 3},
        next_call=lambda args: service.dispatch(
            "list_report_posts",
            args,
            session_id="thirty-post-session",
            turn_id="page-3",
        ),
    )
    third = json.loads(third_payload)["data"]
    assert [item["position"] for item in third["post_previews"]] == list(
        range(21, 31)
    )
    assert third["post_directory"]["has_more"] is False

    restored = configured_service()
    last_post = third["post_previews"][-1]
    detail = json.loads(
        restored.dispatch(
            "read_posts",
            {"post_refs": [last_post["ref"]]},
            session_id="thirty-post-session",
            turn_id="read-last",
        )
    )
    assert detail["ok"], detail
    assert detail["data"]["post_groups"][0]["post"]["name"] == "日常分享 30"


def test_unified_comment_risk_filter_runs_before_paging_and_keeps_account_ref(
    tmp_path,
):
    comments = [
        {
            "comment_id": "risk-1",
            "content": "风险评论一",
            "audit_status": "completed",
            "risk_level": "high",
            "risk_type": "色情引流",
            "nickname": "风险用户一",
            "sec_uid": "risk-user-1",
            "create_time": 1788825601,
        },
        {
            "comment_id": "normal-1",
            "content": "正常评论",
            "audit_status": "completed",
            "risk_level": "none",
            "risk_type": "none",
            "nickname": "正常用户",
            "sec_uid": "normal-user",
            "create_time": 1788825602,
        },
        {
            "comment_id": "risk-2",
            "content": "风险评论二",
            "audit_status": "completed",
            "risk_level": "medium",
            "risk_type": "联系方式导流",
            "nickname": "风险用户二",
            "sec_uid": "risk-user-2",
            "create_time": 1788825603,
        },
        {
            "comment_id": "pending-1",
            "content": "尚未完成审核",
            "audit_status": "pending",
            "risk_level": "none",
            "risk_type": "none",
            "nickname": "待审核用户",
            "sec_uid": "pending-user",
            "create_time": 1788825604,
        },
    ]
    source, store = seed["seed_audit"](
        tmp_path,
        count=1,
        decision="review",
        risk="medium",
        comments=comments,
    )
    result = seed["R31ReportRuntime"](store).generate(
        "new-search-task",
        source=source,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )
    version = store.get_version(result.report_version_id)
    snapshot = store.get_source_snapshot(result.report_version_id)
    service = configure_real_report_runtime(
        store.db_path,
        report_version_id=result.report_version_id,
        expected_database_sha256=hashlib.sha256(store.db_path.read_bytes()).hexdigest(),
        expected_content_hash=version["content_hash"],
        expected_snapshot_hash=snapshot["snapshot_hash"],
        ledger_path=tmp_path / "comments-ledger.sqlite3",
    )
    service.bind_session("comment-filter-session")
    report = json.loads(
        service.dispatch(
            "read_report", {}, session_id="comment-filter-session", turn_id="overview"
        )
    )["data"]
    post_ref = report["post_previews"][0]["ref"]

    first = json.loads(
        service.dispatch(
            "list_post_comments",
            {"post_ref": post_ref, "risk_filter": "risk", "limit": 1},
            session_id="comment-filter-session",
            turn_id="risk-page-1",
        )
    )["data"]
    assert first["total_count"] == 4
    assert first["matched_count"] == 2
    assert first["returned_count"] == 1
    assert first["comments"][0]["risk_level"] in {"medium", "high"}
    assert first["comments"][0]["risk_type"] in {"色情引流", "联系方式导流"}
    assert first["comments"][0]["account_ref"]
    assert first["has_more"] is True

    second = json.loads(
        service.dispatch(
            "list_post_comments",
            {
                "post_ref": post_ref,
                "risk_filter": "risk",
                "limit": 1,
                "cursor": first["cursor"],
            },
            session_id="comment-filter-session",
            turn_id="risk-page-2",
        )
    )["data"]
    assert second["matched_count"] == 2
    assert second["returned_count"] == 1
    assert second["has_more"] is False

    wrong_filter = json.loads(
        service.dispatch(
            "list_post_comments",
            {
                "post_ref": post_ref,
                "risk_filter": "no_risk",
                "limit": 1,
                "cursor": first["cursor"],
            },
            session_id="comment-filter-session",
            turn_id="wrong-filter",
        )
    )
    assert not wrong_filter["ok"]
    assert wrong_filter["error"]["code"] == "cursor_order_mismatch"

    no_risk = json.loads(
        service.dispatch(
            "list_post_comments",
            {"post_ref": post_ref, "risk_filter": "no_risk", "limit": 20},
            session_id="comment-filter-session",
            turn_id="no-risk",
        )
    )["data"]
    unknown = json.loads(
        service.dispatch(
            "list_post_comments",
            {"post_ref": post_ref, "risk_filter": "unknown", "limit": 20},
            session_id="comment-filter-session",
            turn_id="unknown",
        )
    )["data"]
    assert no_risk["matched_count"] == 1
    assert unknown["matched_count"] == 1

    account = json.loads(
        service.dispatch(
            "get_account_overview",
            {"account_ref": first["comments"][0]["account_ref"]},
            session_id="comment-filter-session",
            turn_id="account-overview",
        )
    )
    assert account["ok"], account
    assert account["data"]["statistics"]["comment_count"] == 1


def test_unified_report_overview_limits_default_account_entries_to_five_per_role(
    tmp_path,
):
    source, store = seed["seed_audit"](
        tmp_path, count=1, decision="review", risk="medium"
    )
    result = seed["R31ReportRuntime"](store).generate(
        "new-search-task",
        source=source,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )
    version = store.get_version(result.report_version_id)
    snapshot = store.get_source_snapshot(result.report_version_id)
    service = configure_real_report_runtime(
        store.db_path,
        report_version_id=result.report_version_id,
        expected_database_sha256=hashlib.sha256(store.db_path.read_bytes()).hexdigest(),
        expected_content_hash=version["content_hash"],
        expected_snapshot_hash=snapshot["snapshot_hash"],
        ledger_path=tmp_path / "accounts-ledger.sqlite3",
    )
    service.bind_session("account-entry-session")
    fake_entries = [
        {
            "account_ref": f"publisher-{index}",
            "report_roles": ["post_author"],
            "report_group_placement": {"default_active_comment_visible": False},
        }
        for index in range(7)
    ] + [
        {
            "account_ref": f"commenter-{index}",
            "report_roles": ["comment_author"],
            "report_group_placement": {"default_active_comment_visible": True},
        }
        for index in range(7)
    ]
    service.account_activity.report_entries = Mock(return_value=fake_entries)
    service.repository.report_account_entries = Mock(return_value=())

    report = json.loads(
        service.dispatch(
            "read_report", {}, session_id="account-entry-session", turn_id="overview"
        )
    )["data"]
    assert [item["account_ref"] for item in report["account_entries"]] == [
        *[f"publisher-{index}" for index in range(5)],
        *[f"commenter-{index}" for index in range(5)],
    ]
    assert report["account_entry_statistics"] == {
        "publisher_account_count": 7,
        "commenter_account_count": 7,
        "distinct_account_count": 14,
        "displayed_publisher_count": 5,
        "displayed_commenter_count": 5,
    }


def test_unified_report_selects_its_own_product_mode(tmp_path):
    from backend.hermes_runtime.service import HermesInvestigationAgentService
    from backend.investigation.report_query import ReportQueryFacade
    from backend.investigation.store import InvestigationStore

    source, store = seed["seed_audit"](
        tmp_path,
        verdicts=[("pass", "none"), ("review", "medium")],
    )
    result = seed["R31ReportRuntime"](store).generate(
        "new-search-task",
        source=source,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )
    product = HermesInvestigationAgentService(
        report_facade=ReportQueryFacade(db_path=store.db_path),
        store=InvestigationStore(tmp_path / "sessions.sqlite3"),
    )
    session = product.create_session(result.report_version_id)
    assert product._product_mode(session.id) == "unified-report"


def test_unified_report_dynamic_contexts_are_published_unified_and_refreshable(
    tmp_path,
):
    from backend.hermes_runtime.service import HermesInvestigationAgentService
    from backend.investigation.store import InvestigationStore

    report_db = tmp_path / "reports.sqlite3"
    rows = (
        ("current", "published", "unified_audit"),
        ("peer-a", "published", "unified_audit"),
        ("peer-b", "published", "unified_audit"),
        ("legacy-pass", "published", "all_pass"),
        ("unfinished", "draft", "unified_audit"),
    )
    with sqlite3.connect(report_db) as connection:
        connection.execute(
            "CREATE TABLE report_versions "
            "(id TEXT PRIMARY KEY, status TEXT NOT NULL, body_json TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO report_versions(id, status, body_json) VALUES (?, ?, ?)",
            (
                (
                    report_id,
                    status,
                    json.dumps(
                        {"report_document": {"template_kind": template_kind}}
                    ),
                )
                for report_id, status, template_kind in rows
            ),
        )

    contexts = {
        report_id: SimpleNamespace(
            report_version_id=report_id,
            task_id=f"task-{report_id}",
        )
        for report_id, _status, _template_kind in rows
    }
    facade = Mock(db_path=report_db)
    facade.get_published_report_context.side_effect = contexts.__getitem__
    resolver = Mock(
        return_value=("peer-a", "legacy-pass", "unfinished", "current")
    )
    store = InvestigationStore(tmp_path / "sessions.sqlite3")
    store.session_anchor = Mock(return_value="m3-run:current-run")
    service = HermesInvestigationAgentService(
        report_facade=facade,
        store=store,
        authorized_report_version_ids=(),
        authorized_context_anchor_prefixes=("historical-report:",),
        authorized_report_version_resolver=resolver,
    )
    service._product_mode = Mock(return_value="unified-report")
    session = SimpleNamespace(
        id="session", task_id="task-current", report_version_id="current"
    )

    assert [
        item.report_version_id
        for item in service._authorized_report_contexts(session)
    ] == ["peer-a"]

    resolver.return_value = ("peer-b",)
    assert [
        item.report_version_id
        for item in service._authorized_report_contexts(session)
    ] == ["peer-b"]
    assert resolver.call_args_list == [
        call(session, "m3-run:current-run"),
        call(session, "m3-run:current-run"),
    ]


def test_only_unified_m3_sessions_request_dynamic_report_authorization(tmp_path):
    from backend.hermes_runtime.service import HermesInvestigationAgentService
    from backend.investigation.store import InvestigationStore

    report_db = tmp_path / "reports.sqlite3"
    with sqlite3.connect(report_db) as connection:
        connection.execute(
            "CREATE TABLE report_versions "
            "(id TEXT PRIMARY KEY, status TEXT NOT NULL, body_json TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO report_versions(id, status, body_json) VALUES (?, 'published', ?)",
            (
                (
                    "unified",
                    json.dumps(
                        {"report_document": {"template_kind": "unified_audit"}}
                    ),
                ),
                (
                    "legacy",
                    json.dumps(
                        {"report_document": {"template_kind": "all_pass"}}
                    ),
                ),
            ),
        )
    facade = Mock(db_path=report_db)
    facade.get_published_report_context.side_effect = lambda report_id: SimpleNamespace(
        report_version_id=report_id, task_id=f"task-{report_id}"
    )
    resolver = Mock(return_value=())
    store = InvestigationStore(tmp_path / "sessions.sqlite3")
    service = HermesInvestigationAgentService(
        report_facade=facade,
        store=store,
        authorized_report_version_ids=(),
        authorized_context_anchor_prefixes=("historical-report:",),
        authorized_report_version_resolver=resolver,
    )
    service._product_mode = Mock(
        side_effect=lambda session_id: (
            "unified-report"
            if session_id == "unified-session"
            else "pass-report"
        )
    )

    store.session_anchor = Mock(return_value="m3-run:unified-run")
    service._authorized_report_contexts(
        SimpleNamespace(
            id="unified-session",
            task_id="task-unified",
            report_version_id="unified",
        )
    )
    store.session_anchor = Mock(return_value="historical-report:a")
    service._authorized_report_contexts(
        SimpleNamespace(
            id="legacy-session",
            task_id="task-legacy",
            report_version_id="legacy",
        )
    )

    resolver.assert_called_once()


def test_unified_peer_reports_merge_same_sec_uid_account_activity(tmp_path):
    from hermes_m0.account_activity_service import AccountActivityToolService
    from hermes_m0.pass_support import SnapshotAccountData, SnapshotAccountRepository

    source, store = seed["seed_audit"](
        tmp_path,
        verdicts=[("review", "medium")],
    )
    result = seed["R31ReportRuntime"](store).generate(
        "new-search-task",
        source=source,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )
    version = store.get_version(result.report_version_id)
    snapshot = store.get_source_snapshot(result.report_version_id)
    current_service = configure_real_report_runtime(
        store.db_path,
        report_version_id=result.report_version_id,
        expected_database_sha256=hashlib.sha256(store.db_path.read_bytes()).hexdigest(),
        expected_content_hash=version["content_hash"],
        expected_snapshot_hash=snapshot["snapshot_hash"],
        ledger_path=tmp_path / "current-ledger.sqlite3",
    )
    current = current_service.repository
    peer = SimpleNamespace(
        template_kind="unified_audit",
        account_source="report_snapshot",
        fixture=SimpleNamespace(
            provenance=SimpleNamespace(source_task_id="authorized-peer-task")
        ),
        report=SimpleNamespace(title="另一份新增统一报告"),
        snapshot_payloads=current.snapshot_payloads,
        snapshot_hash="peer-" + current.snapshot_hash,
        content_hash="peer-" + current.content_hash,
        finding_for_post=current.finding_for_post,
        _report_comments_by_post=current._report_comments_by_post,
    )
    combined = SnapshotAccountRepository(SnapshotAccountData((current, peer)))
    activity = AccountActivityToolService(
        current,
        repository_loader=lambda: combined,
        authorized_report_repositories=(current, peer),
    )
    service = UnifiedAuditReportToolService(current, account_activity=activity)
    service.bind_session("cross-report-session")

    report = json.loads(
        service.dispatch(
            "read_report", {}, session_id="cross-report-session", turn_id="overview"
        )
    )["data"]
    account_ref = report["account_entries"][0]["account_ref"]
    overview = json.loads(
        service.dispatch(
            "get_account_overview",
            {"account_ref": account_ref},
            session_id="cross-report-session",
            turn_id="account-overview",
        )
    )
    assert overview["ok"], overview
    assert overview["data"]["statistics"]["published_post_count"] == 2
    assert (
        report["account_entries"][0]["current_investigation_statistics"][
            "published_post_count"
        ]
        == 1
    )


def test_unified_reports_deterministically_enumerate_and_compare_all_stable_accounts(
    tmp_path,
):
    from hermes_m0.account_activity_service import AccountActivityToolService
    from hermes_m0.pass_support import SnapshotAccountData, SnapshotAccountRepository

    comments = [
        {
            "comment_id": "shared-comment",
            "audit_status": "completed",
            "risk_level": "none",
            "risk_type": "none",
            "content": "共同评论者的冻结评论",
            "nickname": "共同评论者",
            "sec_uid": "stable-shared-commenter",
            "create_time": 1789603200,
        }
    ]
    source, store = seed["seed_audit"](
        tmp_path,
        verdicts=[("review", "medium")],
        comments=comments,
    )
    result = seed["R31ReportRuntime"](store).generate(
        "new-search-task",
        source=source,
        checkpoint_path=tmp_path / "checkpoints.sqlite3",
    )
    version = store.get_version(result.report_version_id)
    snapshot = store.get_source_snapshot(result.report_version_id)
    current_service = configure_real_report_runtime(
        store.db_path,
        report_version_id=result.report_version_id,
        expected_database_sha256=hashlib.sha256(store.db_path.read_bytes()).hexdigest(),
        expected_content_hash=version["content_hash"],
        expected_snapshot_hash=snapshot["snapshot_hash"],
        ledger_path=tmp_path / "compare-source-ledger.sqlite3",
    )
    current = current_service.repository
    peer = SimpleNamespace(
        template_kind="unified_audit",
        account_source="report_snapshot",
        fixture=SimpleNamespace(
            provenance=SimpleNamespace(source_task_id="authorized-peer-task")
        ),
        report=SimpleNamespace(title="另一份新增统一报告"),
        snapshot_payloads=current.snapshot_payloads,
        snapshot_hash="peer-" + current.snapshot_hash,
        content_hash="peer-" + current.content_hash,
        finding_for_post=current.finding_for_post,
        _report_comments_by_post=current._report_comments_by_post,
    )
    legacy = SimpleNamespace(
        template_kind="single_risk_post",
        account_source="report_snapshot",
        fixture=SimpleNamespace(
            provenance=SimpleNamespace(source_task_id="historical-report-a-task")
        ),
        report=SimpleNamespace(title="Report A"),
        snapshot_payloads=current.snapshot_payloads,
        snapshot_hash="legacy-" + current.snapshot_hash,
        content_hash="legacy-" + current.content_hash,
        finding_for_post=current.finding_for_post,
        _report_comments_by_post=current._report_comments_by_post,
    )
    combined = SnapshotAccountRepository(
        SnapshotAccountData((current, peer, legacy))
    )
    activity = AccountActivityToolService(
        current,
        repository_loader=lambda: combined,
        authorized_report_repositories=(current, peer, legacy),
    )
    service = UnifiedAuditReportToolService(current, account_activity=activity)
    service.bind_session("compare-reports-session")

    comparison = json.loads(
        service.dispatch(
            "compare_authorized_report_accounts",
            {},
            session_id="compare-reports-session",
            turn_id="compare",
        )
    )
    assert comparison["ok"], comparison
    assert comparison["scope"]["account_activity_scope"] == (
        "authorized_published_unified_audit_reports"
    )
    data = comparison["data"]
    assert data["report_count"] == 2
    assert data["comparison_complete_for_stable_accounts"] is True
    assert [item["account_count"] for item in data["accounts_by_report"]] == [2, 2]
    assert {
        role
        for report in data["accounts_by_report"]
        for account in report["accounts"]
        for role in account["roles"]
    } == {"发布者", "评论者"}
    assert data["shared_account_count"] == 2
    pair = data["pairwise_comparisons"][0]
    assert pair["common_account_count"] == 2
    assert pair["common_publisher_count"] == 1
    assert pair["common_commenter_count"] == 1
    encoded = json.dumps(comparison, ensure_ascii=False)
    assert "account_ref" not in encoded
    assert "account:v2:" not in encoded
    assert "stable-author" not in encoded
    assert "stable-shared-commenter" not in encoded


def test_report_answer_persistence_redacts_internal_account_references(tmp_path):
    context = PublishedReportContext(
        task_id="task-redaction",
        report_id="report:" + "1" * 32,
        report_version_id="report-version:" + "1" * 32,
        version_number=1,
        source_snapshot_id="source-snapshot:redaction",
        snapshot_hash="1" * 64,
        source_hash="source-redaction",
        title="脱敏验收报告",
        content_hash="content-redaction",
        published_at="2026-09-17T00:00:00+00:00",
        finding_ids=(),
        evidence_ids=(),
    )

    class Facade:
        db_path = tmp_path / "unused.sqlite3"

        @staticmethod
        def get_published_report_context(_report_version_id):
            return context

    class Agent:
        _api_max_retries = 1

        @staticmethod
        def run_conversation(message, **kwargs):
            leaked = (
                "黑玫瑰对应 account_ref=account8_34088f，底层标识是 "
                + "account:v2:"
                + "a" * 64
                + "。"
            )
            history = [dict(item) for item in kwargs.get("conversation_history") or []]
            return {
                "completed": True,
                "failed": False,
                "final_response": leaked,
                "messages": [
                    *history,
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": leaked},
                ],
            }

    service = HermesInvestigationAgentService(
        report_facade=Facade(),
        store=InvestigationStore(tmp_path / "sessions.sqlite3"),
        agent_factory=lambda **_kwargs: Agent(),
        bind_runtime=False,
    )
    session = service.create_session(context.report_version_id)
    turn, _ = service.accept_message(
        session.id,
        client_message_id="redact-account-reference",
        content="这个账号是谁？",
    )
    result = service.execute_turn(turn.id)

    assert "account8_34088f" not in result.answer
    assert "account:v2:" not in result.answer
    assert "account_ref" not in result.answer
    assert "内部账号引用已隐藏" in result.answer
    public_messages = service.get_messages(session.id)
    assert all("account8_34088f" not in item.content for item in public_messages)
    transcript = service.store.latest_completed_hermes_transcript(session.id)
    assert transcript is not None
    assert "account8_34088f" not in transcript[-1]["content"]
    assert "account:v2:" not in transcript[-1]["content"]
