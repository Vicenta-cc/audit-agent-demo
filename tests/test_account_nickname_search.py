"""Nickname discovery starts without report refs and keeps stable identities."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from hermes_m0.account_activity_service import AccountActivityToolService
from hermes_m0.account_corpus import account_ref
from hermes_m0.pass_support import SnapshotAccountData, SnapshotAccountRepository
from tests.test_snapshot_account_overview import snapshot


def setup_service(source=None):
    source = source or snapshot()
    corpus = SnapshotAccountData.from_snapshot(source)
    repository = SnapshotAccountRepository(corpus)
    report = SimpleNamespace(
        report=SimpleNamespace(title="测试报告"),
        fixture=SimpleNamespace(provenance=SimpleNamespace(source_task_id=source.task_id)),
        risk_posts=lambda: (),
    )
    service = AccountActivityToolService(report, repository_loader=lambda: repository)
    bind(service)
    return service, corpus


def bind(service, sid="nickname-test", *, fresh=False):
    service.bind_session(sid, task_id="report-c", report_version_id="version-c",
        snapshot_id="snapshot-c", report_revision="1", snapshot_hash="a" * 64,
        content_hash="b" * 64, force_new_generation=fresh)


def call(service, tool="search_accounts", sid="nickname-test", **args):
    return json.loads(service.dispatch(tool, args, session_id=sid))


def reader_id():
    return account_ref(platform="douyin", source_namespace="douyin.sec_uid", source_account_key="reader")


def unique_snapshot():
    source = snapshot()
    for post in source.posts:
        for comment in post.payload["comments"]:
            if comment["sec_uid"] == "reader":
                comment["nickname"] = "刚满18岁"
    return source


def test_unique_nickname_returns_overview_and_usable_activity_and_target_refs():
    service, _ = setup_service(unique_snapshot())
    result = call(service, nickname="  刚满18岁  ")
    assert result["ok"]
    data = result["data"]
    assert data["match_status"] == "unique"
    assert data["total_count"] == 1
    overview = data["overview"]
    assert overview["statistics"]["comment_count"] == 3
    assert overview["statistics"]["published_post_count"] == 0
    assert service.repository_load_count == 1
    target = overview["comment_target_distribution"][0]
    assert target["comment_count"] == 3
    token = overview["account"]["ref"]
    detail = call(service, "list_account_occurrences", account_ref=token,
        kind="comment_author", limit=20, comment_target_ref=target["comment_target_ref"])
    assert detail["ok"]
    assert len(detail["data"]["occurrences"]) == 3
    risk = call(service, "list_account_occurrences", account_ref=token,
        kind="comment_author", limit=20, risk_filter="risk_only")
    assert len(risk["data"]["occurrences"]) == 1
    # No read_report, report cards, Post refs, or risk-comment discovery needed.
    assert reader_id() not in json.dumps(result)


def test_same_nickname_accounts_stay_separate_even_when_page_has_one_candidate():
    service, _ = setup_service()
    first = call(service, nickname="同名账号", limit=1)["data"]
    assert first["total_count"] == 3
    assert first["match_status"] == "needs_selection"
    assert first["overview"] is None
    second = call(service, nickname="同名账号", limit=1, offset=first["next_offset"])["data"]
    assert second["total_count"] == 3
    assert second["candidates"][0]["account_ref"] != first["candidates"][0]["account_ref"]


def test_normal_commenter_and_aliases_are_searchable_without_risk_or_top_cards():
    source = unique_snapshot()
    for post in source.posts:
        for comment in post.payload["comments"]:
            comment["risk_level"] = "none"
    service, corpus = setup_service(source)
    corpus._aliases = dict(corpus._aliases)
    corpus._aliases[reader_id()] = [
        {"nickname": "曾用昵称"}, {"nickname": "刚满18岁"}, {"nickname": "曾用昵称"},
    ]
    data = call(service, nickname="曾用昵称")["data"]
    assert data["total_count"] == 1
    assert data["overview"]["account"]["display_name"] == "曾用昵称"
    assert call(service, nickname="刚满18岁")["data"]["total_count"] == 1
    corpus._aliases[reader_id()].append({"nickname": "秘密昵称", "task_id": "unauthorized-task"})
    assert call(service, nickname="秘密昵称")["data"]["total_count"] == 0
    assert call(service, nickname="曾用昵称")["data"]["overview"]["account"]["display_name"] == "曾用昵称"


def test_exact_does_not_silently_fuzzy_match_or_auto_select_fuzzy_candidate():
    service, _ = setup_service(unique_snapshot())
    assert call(service, nickname="刚满")["data"]["match_status"] == "not_found"
    fuzzy = call(service, nickname="刚满", match_mode="contains")["data"]
    assert fuzzy["total_count"] == 1
    assert fuzzy["match_status"] == "needs_selection"
    assert fuzzy["overview"] is None


def test_unicode_and_case_normalization_preserve_unique_stable_account():
    service, corpus = setup_service(unique_snapshot())
    corpus._aliases = dict(corpus._aliases)
    corpus._aliases[reader_id()] = [{"nickname": "Café"}]
    assert call(service, nickname=" CAFE\u0301 ")["data"]["match_status"] == "unique"


def test_all_matches_are_paginated_without_a_top_twenty_identity_cutoff():
    source = snapshot()
    seed = source.posts[0].payload["comments"][0]
    source.posts[0].payload["comments"] = [
        {**deepcopy(seed), "comment_id": f"c-{i}", "sec_uid": f"reader-{i}", "nickname": "分页用户"}
        for i in range(25)
    ]
    service, _ = setup_service(source)
    first = call(service, nickname="分页用户")["data"]
    second = call(service, nickname="分页用户", offset=first["next_offset"])["data"]
    assert first["total_count"] == second["total_count"] == 25
    assert first["returned_count"] == 20
    assert second["returned_count"] == 5
    assert second["next_offset"] is None
    assert len({c["account_ref"] for c in first["candidates"] + second["candidates"]}) == 25


def test_search_refs_cannot_cross_session_or_report_generation():
    service, _ = setup_service(unique_snapshot())
    token = call(service, nickname="刚满18岁")["data"]["overview"]["account"]["ref"]
    bind(service, "another-session")
    assert not call(service, "get_account_overview", sid="another-session", account_ref=token)["ok"]
    bind(service, fresh=True)
    assert not call(service, "get_account_overview", account_ref=token)["ok"]
    assert call(service, nickname="刚满18岁")["ok"]


def test_published_report_dispatch_forwards_nickname_search():
    from pathlib import Path
    from unittest.mock import Mock
    from hermes_m0.real_report_repository import PublishedReportRepository
    from hermes_m0.report_task_service import ReportTaskInvestigationToolService
    from hermes_m0.repository import InvestigationRepository

    account_service, _ = setup_service(unique_snapshot())
    report = Mock(spec=PublishedReportRepository)
    report.fixture = InvestigationRepository.load(
        Path(__file__).resolve().parents[1] / "hermes_m0/fixtures/report_2272c3692807.json"
    ).fixture
    report.snapshot_hash = "a" * 64
    report.content_hash = "b" * 64
    wrapper = ReportTaskInvestigationToolService(report, account_activity=account_service)
    wrapper.bind_session("nickname-test")
    found = call(wrapper, nickname="刚满18岁")
    assert found["ok"]
    assert found["data"]["overview"]["statistics"]["comment_count"] == 3


@pytest.mark.parametrize("args", [
    {}, {"nickname": " "}, {"nickname": "a" * 201}, {"nickname": 3},
    {"nickname": "a", "match_mode": "regex"}, {"nickname": "a", "limit": 21},
    {"nickname": "a", "offset": -1}, {"nickname": "a", "task_id": "unauthorized"},
])
def test_invalid_search_inputs_are_rejected_before_reading_corpus(args):
    service, _ = setup_service()
    assert not call(service, **args)["ok"]
    assert service.repository_load_count == 0
