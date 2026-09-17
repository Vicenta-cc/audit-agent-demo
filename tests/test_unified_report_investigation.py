from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import runpy
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock, call

from hermes_m0.runtime import configure_real_report_runtime
from hermes_m0.unified_support import (
    UNIFIED_REPORT_SYSTEM_PROMPT,
    UnifiedAuditReportToolService,
    unified_tool_schemas,
)


seed = runpy.run_path(str(Path(__file__).with_name("test_pass_report.py")))


def test_unified_overview_prompt_and_schema_require_all_post_report_read():
    schemas = unified_tool_schemas()
    read_report = next(
        schema for schema in schemas if schema["name"] == "read_report"
    )
    names = {schema["name"] for schema in schemas}

    assert "报告的大概内容是什么" in UNIFIED_REPORT_SYSTEM_PROMPT
    assert "先调用read_report" in UNIFIED_REPORT_SYSTEM_PROMPT
    assert "不得只讲风险帖而遗漏安全帖" in UNIFIED_REPORT_SYSTEM_PROMPT
    assert "全部1至5条冻结帖子" in read_report["description"]
    assert "read_report" in names
    assert "read_posts" in names
    assert "list_post_comments" in names
    assert "list_evidence" in names
    assert "read_evidence" in names
    assert "search_posts" not in names
    assert "list_post_risk_comments" not in names
    assert "list_finding_posts" not in names


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
