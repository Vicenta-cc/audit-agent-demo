"""Published pass reports use actual frozen records through the M1/M2 tools."""

import hashlib
import json
from pathlib import Path
import runpy

from hermes_m0.runtime import configure_real_report_runtime

seed = runpy.run_path(str(Path(__file__).with_name("test_pass_report.py")))


def make_report(path, *, count=1, comments=None, decision="pass", risk="none"):
    path.mkdir(parents=True, exist_ok=True)
    source, store = seed["seed_audit"](path, count=count, comments=comments, decision=decision, risk=risk)
    runtime = seed["R31ReportRuntime"](
        store,
        **(
            {"graph_factory": seed["PassReportGraph"]}
            if decision == "pass" and risk == "none"
            else {}
        ),
    )
    result = runtime.generate(
        "new-search-task", source=source, checkpoint_path=path / "checkpoints.db"
    )

    def service(sid="pass-session"):
        version = store.get_version(result.report_version_id)
        snapshot = store.get_source_snapshot(result.report_version_id)
        svc = configure_real_report_runtime(
            store.db_path,
            report_version_id=result.report_version_id,
            expected_database_sha256=hashlib.sha256(
                store.db_path.read_bytes()
            ).hexdigest(),
            expected_content_hash=version["content_hash"],
            expected_snapshot_hash=snapshot["snapshot_hash"],
            ledger_path=path / (sid + ".db"),
        )
        svc.bind_session(sid)
        return svc

    return service, store


def call(service, tool, args, *, sid="pass-session", call_id=None):
    invoke = lambda a: service.dispatch(tool, a, session_id=sid, turn_id="turn")
    result = (
        service.execute_tool_call(
            session_id=sid,
            tool_name=tool,
            tool_call_id=call_id,
            args=args,
            next_call=invoke,
        )
        if call_id
        else invoke(args)
    )
    body = json.loads(result)
    assert body["ok"], body
    return body["data"]


def test_failed_comment_remains_unknown_in_published_report_tools(tmp_path):
    factory, _ = make_report(tmp_path, comments=[{
        "comment_id": "failed-comment", "audit_status": "failed", "risk_level": None,
        "content": "审核结果缺失的评论", "sec_uid": "commenter",
    }])
    svc = factory()
    report = call(svc, "read_report", {})
    assert report["statistics"]["comment_audit_coverage"]["failed"] == 1
    assert report["statistics"]["comment_audit_coverage"]["completed"] == 0
    coverage = report["statistics"]["comment_audit_coverage"]
    assert sum(coverage[k] for k in ("completed", "failed", "pending", "unknown")) == coverage["total"]
    post = report["post_previews"][0]["ref"]
    page = call(svc, "list_post_comments", {"post_ref": post, "limit": 20})
    comment = page["comments"][0]
    assert comment["audit_status"] == "failed"
    assert comment["risk_level"] == "unavailable"
    normal = call(svc, "list_account_occurrences", {
        "account_ref": comment["account_ref"], "kind": "comment_author",
        "risk_filter": "normal_only", "limit": 20,
    })
    assert normal["matched_count"] == 0


def test_pass_tools_read_frozen_post_audit_translations_and_comments(tmp_path):
    factory, store = make_report(
        tmp_path,
        comments=[
            {
                "comment_id": "1",
                "content": "祝福",
                "audit_status": "completed",
                "risk_level": "none",
                "sec_uid": "commenter",
                "nickname": "读者",
                "create_time": 1788825601,
            },
            {
                "comment_id": "2",
                "content": "未审核",
                "audit_status": "pending",
                "risk_level": "none",
                "sec_uid": "commenter",
                "nickname": "读者",
                "create_time": 1788825602,
            },
        ],
    )
    digest = hashlib.sha256(store.db_path.read_bytes()).hexdigest()
    svc = factory()
    report = call(svc, "read_report", {}, call_id="overview")
    post = report["post_previews"][0]["ref"]
    assert report["post_previews"][0]["comment_count"] == 2
    search = call(
        svc,
        "search_posts",
        {
            "query_text": "日常",
            "requested_count": 1,
            "filters": {"decision": "pass", "risk_level": "none"},
        },
        call_id="search",
    )
    assert search["candidates"][0]["ref"] == post
    details = call(svc, "read_posts", {"post_refs": [post]}, call_id="detail")[
        "post_groups"
    ][0]
    assert details["effective_finding"]["decision"] == "pass"
    assert "日常生活" in details["effective_finding"]["summary"]
    assert details["post_content"]["author_caption"] == "今天去公园散步，记录日常生活。"
    speech = details["post_content"]["videos"][0]["speech_transcript"]
    assert speech["original_text"] == "A walk in the park."
    assert speech["translated_text"] == "在公园散步。"
    page = call(
        svc, "list_post_comments", {"post_ref": post, "limit": 1}, call_id="comments"
    )
    assert page["comments"][0]["audit_status"] == "completed"
    comment_account = page["comments"][0]["account_ref"]
    commenter = call(svc, "get_account_overview", {"account_ref": comment_account})
    assert commenter["statistics"]["comment_count"] == 2
    normal_comments = call(
        svc,
        "list_account_occurrences",
        {
            "account_ref": comment_account,
            "kind": "comment_author",
            "limit": 20,
            "risk_filter": "normal_only",
        },
    )
    assert normal_comments["matched_count"] == 1
    restored = factory()
    page2 = call(
        restored,
        "list_post_comments",
        {"post_ref": post, "limit": 1, "cursor": page["cursor"]},
    )
    assert page2["comments"][0]["audit_status"] == "pending"
    account = details["author"]["account_ref"]
    overview = call(restored, "get_account_overview", {"account_ref": account})
    assert overview["statistics"]["published_post_count"] == 1
    normal = call(
        restored,
        "list_account_occurrences",
        {
            "account_ref": account,
            "kind": "post_author",
            "limit": 2,
            "risk_filter": "normal_only",
        },
    )
    assert normal["matched_count"] == 1
    activity = call(
        restored,
        "read_account_occurrence",
        {"occurrence_ref": normal["occurrences"][0]["ref"]},
    )
    back = call(
        restored,
        "read_posts",
        {"post_refs": [activity["occurrence"]["original_post_ref"]]},
    )
    assert (
        back["post_groups"][0]["effective_finding"]["summary"]
        == details["effective_finding"]["summary"]
    )
    risk = call(
        restored,
        "list_account_occurrences",
        {
            "account_ref": account,
            "kind": "post_author",
            "limit": 2,
            "risk_filter": "risk_only",
        },
    )
    assert risk["matched_count"] == 0
    public = json.dumps(
        [report, details, page, page2, overview, normal, activity, back],
        ensure_ascii=False,
    )
    for secret in [
        "internal-provider",
        "asr_raw_rel",
        "private/transcript",
        "stable-author",
        "account:v2:",
    ]:
        assert secret not in public
    assert hashlib.sha256(store.db_path.read_bytes()).hexdigest() == digest


def test_pass_all_posts_paging_and_cross_session_refs(tmp_path):
    factory, _ = make_report(tmp_path, count=23)
    svc = factory()
    first = call(
        svc,
        "search_posts",
        {"query_text": "全部", "requested_count": 20},
        call_id="first",
    )
    assert first["total_candidate_count"] == 23 and len(first["candidates"]) == 20
    restored = factory()
    second = call(
        restored,
        "search_posts",
        {"query_text": "全部", "requested_count": 20, "cursor": first["cursor"]},
    )
    assert len(second["candidates"]) == 3
    other = factory("other")
    denied = json.loads(
        other.dispatch(
            "read_posts",
            {"post_refs": [first["candidates"][0]["ref"]]},
            session_id="other",
        )
    )
    assert not denied["ok"]


def test_pass_context_filters_and_cursor_validation(tmp_path):
    factory, _ = make_report(tmp_path)
    svc = factory()
    post = call(svc, "read_report", {})["post_previews"][0]["ref"]
    result = call(
        svc,
        "search_posts",
        {"query_text": "这个帖子", "requested_count": 1, "context_ref": post},
    )
    assert result["candidates"][0]["ref"] == post
    for args in (
        {"query_text": "帖子", "requested_count": 1, "filters": {"decision": []}},
        {"query_text": "帖子", "requested_count": 1, "cursor": []},
        {"query_text": "帖子", "requested_count": 1, "context_ref": "invented"},
    ):
        result = json.loads(
            svc.dispatch(
                "search_posts", args, session_id="pass-session", turn_id="validation"
            )
        )
        assert not result["ok"]
    result = json.loads(
        svc.dispatch(
            "list_post_comments",
            {"post_ref": post, "limit": 2, "cursor": []},
            session_id="pass-session",
        )
    )
    assert not result["ok"]


def test_pass_account_scope_merges_only_explicitly_authorized_snapshots(tmp_path):
    from types import SimpleNamespace
    from hermes_m0.pass_support import SnapshotAccountData, SnapshotAccountRepository

    factory, _ = make_report(tmp_path)
    first = factory().repository

    # A separately authorized task with the same stable identity contributes to
    # the global account view without changing the current report's statistics.
    def source(task):
        return SimpleNamespace(
            template_kind="all_pass",
            fixture=SimpleNamespace(provenance=SimpleNamespace(source_task_id=task)),
            report=first.report,
            snapshot_payloads=first.snapshot_payloads,
            snapshot_hash=first.snapshot_hash,
            content_hash=first.content_hash,
            finding_for_post=first.finding_for_post,
            _report_comments_by_post=first._report_comments_by_post,
        )

    current = SnapshotAccountRepository(SnapshotAccountData((first,)))
    combined = SnapshotAccountRepository(
        SnapshotAccountData((first, source("authorized-other")))
    )
    account = current.report_account_entries(first)[0].account_id
    assert len(current.corpus.occurrences_for(account)) == 1
    assert len(combined.corpus.occurrences_for(account)) == 2
    assert combined.report_account_entries(first)[0].published_post_count == 1
    assert combined.corpus_revision != current.corpus_revision
    assert "unauthorized-other" not in combined.corpus.authorized_task_ids


def test_pass_catalog_selection_does_not_mutate_ab_schema(monkeypatch):
    from backend.hermes_runtime.adapter import HermesRuntimeBinding
    from hermes_m0.plugin import register
    from hermes_m0.schemas import M2_ACCOUNT_ACTIVITY_TOOLS
    from hermes_m0.pass_support import pass_tool_schemas
    from hermes_m0.unified_support import unified_tool_schemas

    class Context:
        def __init__(self):
            self.schemas = []

        def register_middleware(self, *args, **kwargs):
            pass

        def register_tool(self, **kwargs):
            self.schemas.append(kwargs["name"])

    before = json.dumps(M2_ACCOUNT_ACTIVITY_TOOLS, sort_keys=True)
    legacy_search = next(
        schema for schema in M2_ACCOUNT_ACTIVITY_TOOLS if schema["name"] == "search_posts"
    )
    pass_search = next(
        schema for schema in pass_tool_schemas() if schema["name"] == "search_posts"
    )
    legacy_filters = legacy_search["parameters"]["properties"]["filters"]["properties"]
    pass_filters = pass_search["parameters"]["properties"]["filters"]["properties"]
    assert legacy_filters["decision"]["enum"] == ["review", "reject"]
    assert legacy_filters["risk_level"]["enum"] == ["low", "medium", "high"]
    assert pass_filters["decision"]["enum"] == ["pass", "review", "reject"]
    assert pass_filters["risk_level"]["enum"] == [
        "none",
        "low",
        "medium",
        "high",
    ]
    unified_names = [schema["name"] for schema in unified_tool_schemas()]
    assert "search_posts" not in unified_names
    assert "list_post_risk_comments" not in unified_names
    assert "list_post_comments" in unified_names
    for name in (
        "HERMES_INVESTIGATION_CREATION_MODE",
        "HERMES_INVESTIGATION_ACCOUNT_ACTIVITY_MODE",
        "HERMES_INVESTIGATION_REAL_REPORT_MODE",
        "HERMES_INVESTIGATION_REPORT_TASK_MODE",
        "HERMES_INVESTIGATION_TASK_MODE",
        "HERMES_INVESTIGATION_PASS_REPORT",
        "HERMES_INVESTIGATION_UNIFIED_REPORT",
    ):
        monkeypatch.setenv(name, "0")
    for mode, expected in [
        ("pass-report", pass_tool_schemas()),
        ("account-activity", M2_ACCOUNT_ACTIVITY_TOOLS),
        ("unified-report", unified_tool_schemas()),
        ("pass-report", pass_tool_schemas()),
    ]:
        HermesRuntimeBinding.activate_product_mode(mode)
        ctx = Context()
        register(ctx)
        assert ctx.schemas == [s["name"] for s in expected]
    assert json.dumps(M2_ACCOUNT_ACTIVITY_TOOLS, sort_keys=True) == before


def test_published_pass_selects_its_runtime_mode(tmp_path):
    from backend.hermes_runtime.service import HermesInvestigationAgentService
    from backend.investigation.report_query import ReportQueryFacade
    from backend.investigation.store import InvestigationStore

    factory, store = make_report(tmp_path)
    repo = factory().repository
    service = HermesInvestigationAgentService(
        report_facade=ReportQueryFacade(db_path=store.db_path),
        store=InvestigationStore(tmp_path / "sessions.sqlite3"),
    )
    session = service.create_session(repo.fixture.report_version.id)
    assert service._product_mode(session.id) == "pass-report"


def test_pass_handoff_uses_explicit_server_authorization_only(tmp_path):
    import sqlite3
    from types import SimpleNamespace
    from unittest.mock import Mock
    from backend.hermes_runtime.service import HermesInvestigationAgentService

    facade = Mock()
    facade.db_path = tmp_path / "reports.sqlite3"
    with sqlite3.connect(facade.db_path) as connection:
        connection.execute(
            "CREATE TABLE report_versions (id TEXT PRIMARY KEY, status TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO report_versions(id, status) VALUES (?, 'published')",
            (("authorized-a",), ("authorized-b",)),
        )
    facade.get_published_report_context.side_effect = lambda version: SimpleNamespace(
        task_id=version
    )
    store = Mock()
    store.session_anchor.return_value = "m3-run:run"
    service = HermesInvestigationAgentService(
        report_facade=facade,
        store=store,
        authorized_report_version_ids=("authorized-a", "authorized-b"),
        authorized_context_anchor_prefixes=("historical-report:",),
    )
    service._product_mode = Mock(return_value="pass-report")
    session = SimpleNamespace(id="session", task_id="own", report_version_id="pass")
    assert [c.task_id for c in service._authorized_report_contexts(session)] == [
        "authorized-a",
        "authorized-b",
    ]
    store.session_anchor.return_value = "unrelated:anchor"
    assert service._authorized_report_contexts(session) == ()
    store.session_anchor.return_value = "m3-run:run"
    service._product_mode.return_value = "account-activity"
    assert service._authorized_report_contexts(session) == ()


def test_new_single_risk_report_uses_unified_all_post_tools(tmp_path):
    from hermes_m0.unified_support import UnifiedAuditReportToolService
    factory, store = make_report(tmp_path, decision="review", risk="high", comments=[
        {"comment_id": "risk", "audit_status": "completed", "risk_level": "high", "content": "风险评论"},
        {"comment_id": "missing", "audit_status": "failed", "risk_level": None},
    ])
    before = hashlib.sha256(store.db_path.read_bytes()).hexdigest()
    svc = factory()
    assert type(svc) is UnifiedAuditReportToolService
    assert svc.repository.template_kind == "unified_audit"
    report = call(svc, "read_report", {})
    coverage = report["statistics"]["comment_audit_coverage"]
    assert {key: coverage[key] for key in (
        "total", "completed", "failed", "pending", "unknown", "scope"
    )} == {
        "total": 2, "completed": 1, "failed": 1, "pending": 0, "unknown": 0,
        "scope": "stored_snapshot_comments_not_platform_total",
    }
    post = report["post_previews"][0]["ref"]
    details = call(svc, "read_posts", {"post_refs": [post]})["post_groups"][0]
    assert details["effective_finding"]["decision"] == "review"
    assert details["effective_finding"]["risk_level"] == "high"
    assert "日常生活" in details["post_content"]["author_caption"]
    comments = call(svc, "list_post_comments", {"post_ref": post, "limit": 20})
    assert "风险评论" in json.dumps(comments, ensure_ascii=False)
    overview = call(svc, "get_account_overview", {"account_ref": details["author"]["account_ref"]})
    assert overview["statistics"]["published_post_count"] == 1
    assert hashlib.sha256(store.db_path.read_bytes()).hexdigest() == before


def test_single_risk_report_comment_total_is_not_publisher_participation(tmp_path):
    factory, store = make_report(tmp_path, decision="review", risk="medium", comments=[
        {"comment_id": str(i), "audit_status": "completed", "risk_level": "none",
         "content": "普通读者评论", "sec_uid": f"reader-{i}", "nickname": f"读者{i}"}
        for i in range(3)
    ])
    before = hashlib.sha256(store.db_path.read_bytes()).hexdigest()
    svc = factory()
    report = call(svc, "read_report", {})
    coverage = report["statistics"]["comment_audit_coverage"]
    assert coverage["total"] == coverage["completed"] == 3
    assert sum(coverage[key] for key in ("completed", "failed", "pending", "unknown")) == 3
    post = report["post_previews"][0]["ref"]
    details = call(svc, "read_posts", {"post_refs": [post]})["post_groups"][0]
    author = call(svc, "get_account_overview", {"account_ref": details["author"]["account_ref"]})
    assert author["statistics"]["comment_count"] == 0
    assert hashlib.sha256(store.db_path.read_bytes()).hexdigest() == before
