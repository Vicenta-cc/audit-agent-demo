"""Selected-report queries: bounded output without narrowing authorization."""
import json
from types import SimpleNamespace

import pytest

from hermes_m0.account_activity_service import AccountActivityToolService
from hermes_m0.account_activity_refs import AccountActivityReferenceRegistry
from hermes_m0.reference_state import _dump, _load, _ACCOUNT_TYPES


@pytest.fixture
def query_service():
    def report(task):
        return SimpleNamespace(
            template_kind="unified_audit", report=SimpleNamespace(title="同名报告"),
            fixture=SimpleNamespace(provenance=SimpleNamespace(source_task_id=task)),
            snapshot_hash="snapshot-" + task, content_hash="content-" + task,
        )
    reports = tuple(report(task) for task in ("a", "b", "c"))
    occurrences = []

    def add(task, identity, role="comment_author"):
        occurrences.append({"task_id": task, "account_ref": identity, "kind": role})

    # 25 stable common commenters; display names intentionally identical.
    for index in range(25):
        for task in ("a", "b"):
            add(task, f"common-{index:02}")
    add("a", "publisher", "post_author")
    add("b", "publisher", "post_author")
    add("a", "mixed", "post_author")
    add("b", "mixed")
    add("c", "common-00")
    add("a", "only-a")
    add("b", "only-b")
    add("outside-authorization", "only-a")
    add("a", "")
    repository = SimpleNamespace(
        corpus_revision="revision-1", corpus=SimpleNamespace(occurrences=occurrences),
        display_name=lambda identity: "同名账号",
    )
    service = AccountActivityToolService(
        reports[0], repository_loader=lambda: repository,
        authorized_report_repositories=reports,
    )
    bind = dict(task_id="a", report_version_id="v", snapshot_id="s", report_revision="1",
                snapshot_hash="hash", content_hash="content", force_new_generation=False)
    for session in ("first", "second"):
        service.bind_session(session, **bind)

    def call(tool, args=None, session="first"):
        return json.loads(service.dispatch(tool, args or {}, session_id=session))
    catalog = call("list_authorized_reports")["data"]
    refs = [item["report_ref"] for item in catalog["reports"]]
    return service, repository, reports, call, refs, bind


def test_intersection_only_bounded_and_stable_identity_not_nickname(query_service):
    _, _, _, call, refs, _ = query_service
    result = call("compare_authorized_report_accounts", {"report_refs": refs[:2]})
    assert result["ok"], result
    data = result["data"]
    assert data["common_account_count"] == 27
    assert data["matched_account_count"] == 27
    assert data["common_publisher_count"] == 1
    assert data["common_commenter_count"] == 25
    assert data["returned_count"] == 10
    assert data["has_more"]
    assert data["unresolved_activity_count"] == 1
    assert "accounts_by_report" not in data and "pairwise_comparisons" not in data
    assert len({item["account_ref"] for item in data["accounts"]}) == 10
    assert all(len(item["report_appearances"]) == 2 for item in data["accounts"])
    assert "common-00" not in json.dumps(result)


def test_all_selected_reports_not_any_pair(query_service):
    _, _, _, call, refs, _ = query_service
    data = call("compare_authorized_report_accounts", {"report_refs": refs})["data"]
    assert data["matched_account_count"] == 1
    assert data["match_semantics"] == "present_in_every_selected_report"
    assert len(data["accounts"][0]["report_appearances"]) == 3


def test_paging_complete_no_duplicates_and_query_binding(query_service):
    _, _, _, call, refs, _ = query_service
    args = {"report_refs": refs[:2], "limit": 20}
    first = call("compare_authorized_report_accounts", args)["data"]
    second = call("compare_authorized_report_accounts", {
        **args, "report_refs": list(reversed(refs[:2])), "cursor": first["cursor"],
    })["data"]
    assert first["returned_count"] == 20 and second["returned_count"] == 7
    assert not second["has_more"] and second["cursor"] is None
    assert len({item["account_ref"] for item in first["accounts"] + second["accounts"]}) == 27
    assert [item["position"] for item in second["accounts"]] == list(range(21, 28))
    for changed in ({"role": "post_author"}, {"report_refs": refs[1:]}):
        result = call("compare_authorized_report_accounts", {
            **args, "cursor": first["cursor"], **changed,
        })
        assert not result["ok"]


def test_role_filter_before_page_and_zero_result(query_service):
    _, _, _, call, refs, _ = query_service
    data = call("compare_authorized_report_accounts", {
        "report_refs": refs[:2], "role": "post_author",
    })["data"]
    assert data["matched_account_count"] == 1 and data["returned_count"] == 1
    assert not data["has_more"]
    assert all(item["published_post_count"] == 1 for item in data["accounts"][0]["report_appearances"])
    empty = call("compare_authorized_report_accounts", {
        "report_refs": refs, "role": "post_author",
    })["data"]
    assert empty["matched_account_count"] == 0 and empty["accounts"] == []
    # An empty role-filtered result is not the same as no common accounts at all.
    assert empty["common_account_count"] == 1


def test_no_common_accounts_and_unidentified_are_not_safe_matches(query_service):
    _, repository, _, call, refs, _ = query_service
    repository.corpus.occurrences[:] = [
        {"task_id": "a", "kind": "post_author", "account_ref": "distinct-a"},
        {"task_id": "b", "kind": "post_author", "account_ref": "distinct-b"},
        {"task_id": "a", "kind": "comment_author", "account_ref": ""},
    ]
    data = call("compare_authorized_report_accounts", {"report_refs": refs[:2]})["data"]
    assert not data["has_common_accounts"]
    assert data["common_account_count"] == 0 and data["accounts"] == []
    assert data["unresolved_activity_count"] == 1


@pytest.mark.parametrize("args", [
    {}, {"report_refs": []}, {"report_refs": ["not-issued", "also-not-issued"]},
    {"report_refs": "all"}, {"report_refs": [1, 2]}, {"report_refs": [None, None]},
])
def test_comparison_never_defaults_to_every_authorized_report(query_service, args):
    _, _, _, call, _, _ = query_service
    assert not call("compare_authorized_report_accounts", args)["ok"]


@pytest.mark.parametrize("extra", [
    {"limit": 0}, {"limit": 21}, {"limit": True}, {"role": "bad"},
    {"cursor": "invented"}, {"unexpected": True},
])
def test_reject_invalid_parameters(query_service, extra):
    _, _, _, call, refs, _ = query_service
    assert not call("compare_authorized_report_accounts", {
        "report_refs": refs[:2], **extra,
    })["ok"]


def test_duplicate_cross_session_wrong_kind_and_revoked_report(query_service):
    service, _, reports, call, refs, _ = query_service
    assert not call("compare_authorized_report_accounts", {"report_refs": [refs[0], refs[0]]})["ok"]
    assert not call("compare_authorized_report_accounts", {"report_refs": refs[:2]}, "second")["ok"]
    first = call("compare_authorized_report_accounts", {"report_refs": refs[:2]})["data"]
    account = first["accounts"][0]["account_ref"]
    assert not call("compare_authorized_report_accounts", {"report_refs": [refs[0], account]})["ok"]
    service.authorized_report_repositories = reports[:1]
    assert not call("compare_authorized_report_accounts", {"report_refs": refs[:2]})["ok"]


def test_catalog_pagination_and_independent_directory(query_service):
    _, _, _, call, refs, _ = query_service
    first = call("list_authorized_reports", {"limit": 1})["data"]
    assert first["report_count"] == 3 and len(first["reports"]) == 1
    assert "accounts" not in first
    second = call("list_authorized_reports", {"limit": 1, "cursor": first["cursor"]})["data"]
    assert second["reports"][0]["position"] == 2
    data = call("list_report_accounts", {"report_ref": refs[0], "role": "post_author"})["data"]
    assert data["account_count"] == 2 and data["returned_count"] == 2
    assert all(len(item["report_appearances"]) == 1 for item in data["accounts"])
    assert not call("list_report_accounts", {
        "report_ref": refs[0], "cursor": first["cursor"],
    })["ok"]


def test_references_and_cursor_survive_registry_serialization(query_service):
    service, repository, reports, call, refs, bind = query_service
    page = call("compare_authorized_report_accounts", {"report_refs": refs[:2]})["data"]
    state = json.loads(json.dumps(_dump(service.refs, "first", _ACCOUNT_TYPES)))
    registry = AccountActivityReferenceRegistry()
    restored = AccountActivityToolService(
        reports[0], repository_loader=lambda: repository, refs=registry,
        authorized_report_repositories=reports,
    )
    restored.bind_session("first", **bind)
    _load(registry, "first", state, _ACCOUNT_TYPES)
    result = json.loads(restored.dispatch("compare_authorized_report_accounts", {
        "report_refs": refs[:2], "cursor": page["cursor"],
    }, session_id="first"))
    assert result["ok"], result
    assert result["data"]["accounts"][0]["position"] == 11
    repository.corpus_revision = "changed"
    stale = json.loads(restored.dispatch("compare_authorized_report_accounts", {
        "report_refs": refs[:2], "cursor": page["cursor"],
    }, session_id="first"))
    assert not stale["ok"]


def test_new_report_refs_are_sanitized_and_recovery_recognizes_them():
    from backend.hermes_runtime.service import redact_internal_account_references
    from hermes_m0.reference_state import _NAVIGATION_TOKEN
    text, changed = redact_internal_account_references("报告 report_ref=report123_abcdef；甲 account2_abcdef")
    assert changed and "report123_abcdef" not in text and "account2_abcdef" not in text
    assert _NAVIGATION_TOKEN.findall("report123_abcdef") == ["report123_abcdef"]


def test_statistics_full_counts_top_five_ties_same_nickname_and_scope(query_service):
    service, repository, _, call, refs, _ = query_service
    repository.corpus.occurrences[:] = [
        {"task_id": "a", "kind": "comment_author", "account_ref": f"id-{i:02}"}
        for i in range(58) for _ in range(4 if i == 0 else 2 if i < 4 else 1)
    ] + [{"task_id": "b", "kind": "comment_author", "account_ref": "id-04"}] * 100
    data = call("get_report_account_statistics")["data"]
    assert data["report"]["is_current_report"]
    assert data["statistics"]["commenter_account_count"] == 58
    assert data["statistics"]["comment_count"] == 64
    assert data["statistics"]["account_counts_complete"]
    assert data["statistics"] == service.current_report_account_statistics()
    assert [a["activity_count"] for a in data["accounts"]] == [4, 2, 2, 2, 1]
    assert [a["rank"] for a in data["accounts"]] == [1, 2, 2, 2, 5]
    assert len({a["account_ref"] for a in data["accounts"]}) == 5
    assert len({a["display_name"] for a in data["accounts"]}) == 1  # same nickname, distinct IDs
    assert data["cutoff_tied_account_count"] == 54
    assert data["cutoff_omitted_tied_count"] == 53
    repository.corpus.occurrences.reverse()
    assert call("get_report_account_statistics")["data"] == data
    peer = call("get_report_account_statistics", {"report_ref": refs[1]})["data"]
    assert peer["statistics"]["comment_count"] == 100
    assert peer["statistics"]["commenter_account_count"] == 1


def test_statistics_unresolved_empty_and_publisher_rank(query_service):
    _, repository, _, call, _, _ = query_service
    repository.corpus.occurrences[:] = [
        {"task_id": "a", "kind": "comment_author", "account_ref": ""},
        {"task_id": "a", "kind": "post_author", "account_ref": "author"},
        {"task_id": "a", "kind": "post_author", "account_ref": "author"},
        {"task_id": "a", "kind": "post_author", "account_ref": None},
    ]
    data = call("get_report_account_statistics")["data"]
    assert data["accounts"] == [] and data["account_count"] == 0
    assert data["cutoff_tied_account_count"] == 0
    assert data["statistics"]["comment_count"] == 1
    assert data["statistics"]["commenter_account_count"] == 0
    assert data["statistics"]["unresolved_comment_count"] == 1
    assert data["statistics"]["unresolved_post_count"] == 1
    assert not data["statistics"]["account_counts_complete"]
    authors = call("get_report_account_statistics", {"role": "post_author"})["data"]
    assert authors["account_count"] == 1
    assert authors["accounts"][0]["activity_count"] == 2
    assert authors["statistics"]["published_post_count"] == 3
    repository.corpus.occurrences.clear()
    assert call("get_report_account_statistics")["data"]["statistics"]["account_counts_complete"]


@pytest.mark.parametrize("args", [
    {"top_n": 0}, {"top_n": 21}, {"top_n": True}, {"top_n": "5"},
    {"role": "any"}, {"role": "commenter"}, {"report_ref": None},
    {"report_ref": "invented"}, {"task_id": "a"},
])
def test_statistics_reject_invalid_inputs(query_service, args):
    *_, call, refs, bind = query_service
    assert not call("get_report_account_statistics", args)["ok"]


def test_statistics_scope_and_reference_validation(query_service):
    service, repository, reports, call, refs, _ = query_service
    args = {"report_ref": refs[0]}
    assert not call("get_report_account_statistics", args, "second")["ok"]
    result = call("get_report_account_statistics")["data"]
    assert not call("get_report_account_statistics", {
        "report_ref": result["accounts"][0]["account_ref"],
    })["ok"]
    service.authorized_report_repositories = reports[1:]
    assert not call("get_report_account_statistics", args)["ok"]
    assert not call("get_report_account_statistics")["ok"]
