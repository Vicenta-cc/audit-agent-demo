from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import runpy

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
